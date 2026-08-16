"""Strict replay, evaluator-truth, scoring, and held-out seal contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt, StringConstraints, model_validator

from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    DtaModel,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RunbookId,
    Sha256,
    Terminal,
    semantic_sha256,
)
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    MetricRecord,
    ResourceUsageRecord,
    RuntimeRecord,
    ToolName,
    ToolErrorCode,
    ToolResultRecord,
    TraceNeighborhoodRecord,
    assert_truth_isolated,
)


CaseId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^dta-case-[0-9]{3}$"),
]
GitCommit = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$"),
]
OpaqueExecutionId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[0-9a-f]{32}$"),
]


class EvaluationSplit(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    HELD_OUT = "HELD_OUT"
    NO_ACTION = "NO_ACTION"


class EvaluationArm(str, Enum):
    ONE_SHOT_FULL_CONTEXT = "ONE_SHOT_FULL_CONTEXT"
    ADAPTIVE_TOOL_USING = "ADAPTIVE_TOOL_USING"


class EvaluationTerminal(str, Enum):
    COMPLETED = "COMPLETED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    ABSTAIN = "ABSTAIN"
    FAILED = "FAILED"


class ScenarioFamily(str, Enum):
    PAYMENT = "PAYMENT"
    RECOMMENDATION = "RECOMMENDATION"
    EMAIL = "EMAIL"
    NO_REAL_FAULT = "NO_REAL_FAULT"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    UNKNOWN_MECHANISM = "UNKNOWN_MECHANISM"


_RESULT_TYPE_BY_TOOL = {
    ToolName.QUERY_METRICS: MetricRecord,
    ToolName.SEARCH_LOGS: DiagnosticLogRecord,
    ToolName.QUERY_TRACE_NEIGHBORHOOD: TraceNeighborhoodRecord,
    ToolName.INSPECT_SERVICE_RUNTIME: RuntimeRecord,
    ToolName.INSPECT_RESOURCE_USAGE: ResourceUsageRecord,
}
_OBSERVATION_DIFFERENCES = {
    "fault_strength",
    "load_level",
    "timing_window",
    "noise_decoy_pattern",
    "evidence_availability",
    "no_real_fault",
    "conflicting_evidence",
    "unknown_mechanism",
}


class ReplayObservationFixture(DtaModel):
    """Run-independent safe records rehydrated by the replay backend."""

    schema_version: Literal["dta-v2.replay-observation-fixture.v1"]
    tool: ToolName
    service_scope: tuple[str, ...] = Field(min_length=1, max_length=10)
    records: tuple[ToolResultRecord, ...] = Field(max_length=40)
    truncated: bool
    error_code: ToolErrorCode | None
    fixture_sha256: Sha256

    @model_validator(mode="after")
    def require_fixture(self) -> ReplayObservationFixture:
        expected_type = _RESULT_TYPE_BY_TOOL[self.tool]
        if (
            len(self.service_scope) != len(set(self.service_scope))
            or self.service_scope != tuple(sorted(self.service_scope))
        ):
            raise ValueError("replay fixture service scope is not canonical")
        if any(type(record) is not expected_type for record in self.records):
            raise ValueError("replay fixture result type differs from tool")
        if self.error_code is not None and (self.records or self.truncated):
            raise ValueError("failed replay fixture carries successful results")
        assert_truth_isolated(
            [record.model_dump(mode="json") for record in self.records]
        )
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"fixture_sha256"})
        )
        if self.fixture_sha256 != expected:
            raise ValueError("replay fixture digest differs")
        return self


class AgentVisibleReplayCase(DtaModel):
    """Truth-free case bytes available to both comparison arms."""

    schema_version: Literal["dta-v2.agent-visible-replay-case.v1"]
    case_id: CaseId
    scenario_id: str = Field(pattern=r"^dta-dev-00[1-3]$")
    captured_started_at: datetime
    captured_ended_at: datetime
    observations: tuple[ReplayObservationFixture, ...] = Field(
        min_length=1, max_length=5
    )
    full_context_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=4)
    case_sha256: Sha256

    @model_validator(mode="after")
    def require_case(self) -> AgentVisibleReplayCase:
        for value in (self.captured_started_at, self.captured_ended_at):
            offset = value.utcoffset()
            if (
                value.tzinfo is None
                or offset is None
                or offset.total_seconds() != 0
            ):
                raise ValueError("replay capture window must use UTC")
        if (
            self.captured_ended_at <= self.captured_started_at
            or self.captured_ended_at - self.captured_started_at > timedelta(hours=1)
        ):
            raise ValueError("replay capture window is invalid")
        tools = tuple(item.tool for item in self.observations)
        if len(tools) != len(set(tools)):
            raise ValueError("replay case contains duplicate tool fixtures")
        if tools != tuple(sorted(tools, key=lambda item: item.value)):
            raise ValueError("replay case tools are not canonical")
        if (
            len(self.full_context_tools) != len(set(self.full_context_tools))
            or any(item not in tools for item in self.full_context_tools)
            or self.full_context_tools
            != tuple(sorted(self.full_context_tools, key=lambda item: item.value))
        ):
            raise ValueError("full-context tool projection is invalid")
        digest_payload = self.model_dump(mode="json", exclude={"case_sha256"})
        assert_truth_isolated(
            {
                "case_id": self.case_id,
                "scenario_id": self.scenario_id,
                "captured_started_at": self.captured_started_at.isoformat(),
                "captured_ended_at": self.captured_ended_at.isoformat(),
                "full_context_tools": self.full_context_tools,
                "observations": [
                    {
                        "tool": item.tool,
                        "service_scope": item.service_scope,
                        "records": [
                            record.model_dump(mode="json")
                            for record in item.records
                        ],
                        "truncated": item.truncated,
                        "error_code": item.error_code,
                    }
                    for item in self.observations
                ],
            }
        )
        expected = semantic_sha256(digest_payload)
        if self.case_sha256 != expected:
            raise ValueError("agent-visible replay case digest differs")
        return self


class EvaluatorCaseTruth(DtaModel):
    """Evaluator-only label never supplied to Provider or Agent runtime."""

    schema_version: Literal["dta-v2.evaluator-case-truth.v1"]
    case_id: CaseId
    split: EvaluationSplit
    scenario_family: ScenarioFamily
    meaningful_observation_differences: tuple[str, ...] = Field(
        min_length=1, max_length=5
    )
    expected_terminal: Terminal
    expected_root_service: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9-]*$"
    )
    expected_fault_domain: FaultDomain | None
    expected_mechanism: FaultMechanism | None
    expected_disposition: ActionDisposition | None
    expected_runbook: RunbookId | None
    expected_evidence_sources: tuple[EvidenceSource, ...] = Field(max_length=5)
    truth_sha256: Sha256

    @model_validator(mode="after")
    def require_truth(self) -> EvaluatorCaseTruth:
        differences = self.meaningful_observation_differences
        if (
            len(differences) != len(set(differences))
            or any(item not in _OBSERVATION_DIFFERENCES for item in differences)
        ):
            raise ValueError("evaluator observation differences are invalid")
        sources = self.expected_evidence_sources
        if len(sources) != len(set(sources)):
            raise ValueError("expected evidence sources contain duplicates")
        if self.expected_terminal is Terminal.COMPLETED:
            if any(
                item is None
                for item in (
                    self.expected_root_service,
                    self.expected_fault_domain,
                    self.expected_mechanism,
                    self.expected_disposition,
                )
            ):
                raise ValueError("completed evaluator truth lacks semantics")
            if self.expected_disposition is ActionDisposition.EXECUTE_RUNBOOK:
                if self.expected_runbook is None or not sources:
                    raise ValueError("write truth lacks Runbook or evidence")
            elif self.expected_runbook is not None:
                raise ValueError("nonwrite truth cannot name a Runbook")
        elif any(
            item is not None
            for item in (
                self.expected_root_service,
                self.expected_fault_domain,
                self.expected_mechanism,
                self.expected_disposition,
                self.expected_runbook,
            )
        ) or sources:
            raise ValueError("noncompleted evaluator truth carries diagnosis/action")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"truth_sha256"})
        )
        if self.truth_sha256 != expected:
            raise ValueError("evaluator truth digest differs")
        return self


class EvaluationPrediction(DtaModel):
    """Private semantic/cost projection scored without raw Provider content."""

    schema_version: Literal["dta-v2.evaluation-prediction.v1"]
    case_id: CaseId
    arm: EvaluationArm
    terminal: Terminal | Literal["FAILED"]
    root_service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    fault_domain: FaultDomain | None
    mechanism: FaultMechanism | None
    disposition: ActionDisposition | None
    runbook_id: RunbookId | None
    cited_evidence_sources: tuple[EvidenceSource, ...] = Field(max_length=5)
    evidence_refs_valid: bool
    read_tool_dispatches: StrictInt = Field(ge=0, le=4)
    context_materialization_reads: StrictInt = Field(default=0, ge=0, le=4)
    provider_turns: StrictInt = Field(ge=0, le=6)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictInt = Field(ge=0)
    unsafe_proposal_attempts: StrictInt = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_prediction(self) -> EvaluationPrediction:
        if len(self.cited_evidence_sources) != len(
            set(self.cited_evidence_sources)
        ):
            raise ValueError("prediction evidence sources contain duplicates")
        if self.terminal is Terminal.COMPLETED:
            if any(
                item is None
                for item in (self.root_service, self.fault_domain, self.mechanism)
            ):
                raise ValueError("completed prediction lacks diagnosis")
        elif any(
            item is not None
            for item in (
                self.root_service,
                self.fault_domain,
                self.mechanism,
                self.disposition,
                self.runbook_id,
            )
        ):
            raise ValueError("noncompleted prediction carries diagnosis/action")
        if self.disposition is ActionDisposition.EXECUTE_RUNBOOK:
            if self.runbook_id is None:
                raise ValueError("execute prediction lacks Runbook")
        elif self.runbook_id is not None:
            raise ValueError("nonexecute prediction names a Runbook")
        return self


class EvaluationScore(DtaModel):
    schema_version: Literal["dta-v2.evaluation-score.v1"]
    case_id: CaseId
    arm: EvaluationArm
    root_exact_match: bool | None
    mechanism_accuracy: bool | None
    runbook_top1_accuracy: bool | None
    evidence_validity: bool
    action_precision: bool
    no_action_accuracy: bool | None
    escalation_accuracy: bool | None
    read_tool_dispatches: StrictInt = Field(ge=0, le=4)
    context_materialization_reads: StrictInt = Field(default=0, ge=0, le=4)
    provider_turns: StrictInt = Field(ge=0, le=6)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictInt = Field(ge=0)
    unsafe_proposal_attempts: StrictInt = Field(ge=0, le=1)
    score_sha256: Sha256

    @model_validator(mode="after")
    def require_score(self) -> EvaluationScore:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"score_sha256"})
        )
        if self.score_sha256 != expected:
            raise ValueError("evaluation score digest differs")
        return self


def build_evaluation_score(
    *, prediction: EvaluationPrediction, truth: EvaluatorCaseTruth
) -> EvaluationScore:
    prediction = EvaluationPrediction.model_validate(prediction.model_dump())
    truth = EvaluatorCaseTruth.model_validate(truth.model_dump())
    if prediction.case_id != truth.case_id:
        raise ValueError("prediction and evaluator truth case IDs differ")
    is_no_action = truth.split is EvaluationSplit.NO_ACTION
    terminal_match = prediction.terminal == truth.expected_terminal
    expected_sources = set(truth.expected_evidence_sources)
    evidence_valid = prediction.evidence_refs_valid and expected_sources.issubset(
        set(prediction.cited_evidence_sources)
    )
    if truth.expected_disposition is ActionDisposition.EXECUTE_RUNBOOK:
        action_precision = (
            prediction.disposition is ActionDisposition.EXECUTE_RUNBOOK
            and prediction.runbook_id is truth.expected_runbook
        )
    elif truth.expected_disposition is not None:
        action_precision = (
            prediction.disposition is truth.expected_disposition
            and prediction.runbook_id is None
        )
    else:
        action_precision = (
            prediction.disposition is None and prediction.runbook_id is None
        )
    escalation_accuracy: bool | None = None
    if truth.expected_terminal in (Terminal.NEED_MORE_EVIDENCE, Terminal.ABSTAIN):
        escalation_accuracy = terminal_match
    elif truth.expected_disposition is ActionDisposition.ESCALATE_HUMAN:
        escalation_accuracy = (
            prediction.disposition is ActionDisposition.ESCALATE_HUMAN
        )
    payload: dict[str, object] = {
        "schema_version": "dta-v2.evaluation-score.v1",
        "case_id": prediction.case_id,
        "arm": prediction.arm,
        "root_exact_match": (
            None
            if truth.expected_root_service is None
            else prediction.root_service == truth.expected_root_service
        ),
        "mechanism_accuracy": (
            None
            if truth.expected_mechanism is None
            else prediction.mechanism is truth.expected_mechanism
        ),
        "runbook_top1_accuracy": (
            None
            if truth.expected_runbook is None
            else prediction.runbook_id is truth.expected_runbook
        ),
        "evidence_validity": evidence_valid,
        "action_precision": action_precision,
        "no_action_accuracy": terminal_match and action_precision if is_no_action else None,
        "escalation_accuracy": escalation_accuracy,
        "read_tool_dispatches": prediction.read_tool_dispatches,
        "context_materialization_reads": (
            prediction.context_materialization_reads
        ),
        "provider_turns": prediction.provider_turns,
        "input_tokens": prediction.input_tokens,
        "output_tokens": prediction.output_tokens,
        "latency_ms": prediction.latency_ms,
        "unsafe_proposal_attempts": prediction.unsafe_proposal_attempts,
    }
    return EvaluationScore.model_validate(
        {**payload, "score_sha256": semantic_sha256(payload)}
    )


class HeldOutSeal(DtaModel):
    schema_version: Literal["dta-v2.held-out-seal.v1"]
    base_head: GitCommit
    model_id: str = Field(min_length=1, max_length=128)
    agent_identity_sha256: Sha256
    one_shot_prompt_sha256: Sha256
    adaptive_prompt_sha256: Sha256
    tool_schema_sha256: Sha256
    budgets_sha256: Sha256
    diagnosis_schema_sha256: Sha256
    runbook_registry_sha256: Sha256
    candidate_filter_sha256: Sha256
    action_schema_sha256: Sha256
    scorer_sha256: Sha256
    held_out_case_sha256s: tuple[Sha256, Sha256, Sha256]
    evaluator_truth_sha256s: tuple[Sha256, Sha256, Sha256]
    seal_sha256: Sha256

    @model_validator(mode="after")
    def require_seal(self) -> HeldOutSeal:
        for values, label in (
            (self.held_out_case_sha256s, "held-out cases"),
            (self.evaluator_truth_sha256s, "held-out truths"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} contain duplicate digests")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"seal_sha256"})
        )
        if self.seal_sha256 != expected:
            raise ValueError("held-out seal digest differs")
        return self


def build_held_out_seal(**values: object) -> HeldOutSeal:
    payload = {"schema_version": "dta-v2.held-out-seal.v1", **values}
    return HeldOutSeal.model_validate(
        {**payload, "seal_sha256": semantic_sha256(payload)}
    )


class HeldOutExecutionClaim(DtaModel):
    schema_version: Literal["dta-v2.held-out-execution-claim.v1"]
    execution_id: OpaqueExecutionId
    seal_sha256: Sha256
    claimed_at: datetime
    claim_sha256: Sha256

    @model_validator(mode="after")
    def require_claim(self) -> HeldOutExecutionClaim:
        offset = self.claimed_at.utcoffset()
        if (
            self.claimed_at.tzinfo is None
            or offset is None
            or offset.total_seconds() != 0
        ):
            raise ValueError("held-out execution claim must use UTC")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"claim_sha256"})
        )
        if self.claim_sha256 != expected:
            raise ValueError("held-out execution claim digest differs")
        return self


def persist_held_out_execution_claim(
    path: Path, *, seal: HeldOutSeal, execution_id: str
) -> HeldOutExecutionClaim:
    """Create the one durable claim before any held-out Provider call."""

    seal = HeldOutSeal.model_validate(seal.model_dump())
    target = Path(path)
    _ensure_private_directory(target.parent)
    if target.is_symlink():
        raise ValueError("held-out execution claim is a symbolic link")
    if target.exists():
        existing = HeldOutExecutionClaim.model_validate_json(
            target.read_text(encoding="utf-8")
        )
        if (
            existing.execution_id != execution_id
            or existing.seal_sha256 != seal.seal_sha256
        ):
            raise FileExistsError("held-out execution was already claimed")
        if target.stat().st_mode & 0o777 != 0o600:
            raise PermissionError("held-out execution claim mode differs")
        return existing
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.held-out-execution-claim.v1",
        "execution_id": execution_id,
        "seal_sha256": seal.seal_sha256,
        "claimed_at": datetime.now(timezone.utc),
    }
    draft = HeldOutExecutionClaim.model_construct(
        **payload, claim_sha256="0" * 64
    )
    claim = HeldOutExecutionClaim.model_validate(
        {
            **payload,
            "claim_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"claim_sha256"})
            ),
        }
    )
    encoded = (
        json.dumps(
            claim.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        target,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        raise
    target.chmod(0o600)
    if target.stat().st_mode & 0o777 != 0o600:
        raise PermissionError("held-out execution claim mode differs")
    return claim


def _ensure_private_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise ValueError("private directory ancestor is a symbolic link")
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError("private directory ancestor is invalid")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=False)
        directory.chmod(0o700)
    path.chmod(0o700)
    if path.stat().st_mode & 0o777 != 0o700:
        raise PermissionError("private directory mode differs")


__all__ = [
    "AgentVisibleReplayCase",
    "EvaluationArm",
    "EvaluationPrediction",
    "EvaluationScore",
    "EvaluationSplit",
    "EvaluationTerminal",
    "EvaluatorCaseTruth",
    "HeldOutExecutionClaim",
    "HeldOutSeal",
    "ReplayObservationFixture",
    "ScenarioFamily",
    "build_evaluation_score",
    "build_held_out_seal",
    "persist_held_out_execution_claim",
]
