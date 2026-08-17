"""Truth-separated replay, prediction, scoring, and public binding contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import json
import re
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StringConstraints, model_validator

from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    MetricRecord,
    ResourceUsageRecord,
    RuntimeRecord,
    ToolErrorCode,
    ToolName,
    ToolResultRecord,
    TraceNeighborhoodRecord,
    assert_truth_isolated,
)
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    DtaModelV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    Sha256V21,
    TerminalV21,
    semantic_sha256,
)


CaseIdV21 = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^dta21-case-[0-9]{3}$"),
]


class EvaluationSplitV21(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    HELD_OUT = "HELD_OUT"


class EvaluationArmV21(str, Enum):
    ONE_SHOT_FULL_CONTEXT = "ONE_SHOT_FULL_CONTEXT"
    FLAT_ADAPTIVE = "FLAT_ADAPTIVE"
    EVIDENCE_GUIDED_PLANNER = "EVIDENCE_GUIDED_PLANNER"
    EVIDENCE_GUIDED_PLANNER_NO_COMPACTION = "EVIDENCE_GUIDED_PLANNER_NO_COMPACTION"


class ScenarioFamilyV21(str, Enum):
    PAYMENT_CONFIGURATION = "PAYMENT_CONFIGURATION"
    EMAIL_MEMORY_LEAK = "EMAIL_MEMORY_LEAK"
    RECOMMENDATION_UNAVAILABLE = "RECOMMENDATION_UNAVAILABLE"
    AD_CPU_SATURATION = "AD_CPU_SATURATION"
    EMAIL_UNAVAILABLE = "EMAIL_UNAVAILABLE"
    PRODUCT_CATALOG_UNAVAILABLE = "PRODUCT_CATALOG_UNAVAILABLE"
    SHIPPING_DEPENDENCY_LATENCY = "SHIPPING_DEPENDENCY_LATENCY"
    NO_FAULT = "NO_FAULT"
    MISSING_CONFLICTING_EVIDENCE = "MISSING_CONFLICTING_EVIDENCE"


class GeneralizationSliceV21(str, Enum):
    SEEN_SERVICE_SEEN_MECHANISM = "SEEN_SERVICE_SEEN_MECHANISM"
    NEW_SERVICE_SEEN_MECHANISM = "NEW_SERVICE_SEEN_MECHANISM"
    SEEN_SERVICE_NEW_MECHANISM = "SEEN_SERVICE_NEW_MECHANISM"
    NEW_SERVICE_NEW_MECHANISM = "NEW_SERVICE_NEW_MECHANISM"
    SAME_SERVICE_MULTIPLE_MECHANISMS = "SAME_SERVICE_MULTIPLE_MECHANISMS"
    NO_FAULT = "NO_FAULT"
    MISSING_CONFLICTING_EVIDENCE = "MISSING_CONFLICTING_EVIDENCE"


_RESULT_TYPE_BY_TOOL = {
    ToolName.QUERY_METRICS: MetricRecord,
    ToolName.SEARCH_LOGS: DiagnosticLogRecord,
    ToolName.QUERY_TRACE_NEIGHBORHOOD: TraceNeighborhoodRecord,
    ToolName.INSPECT_SERVICE_RUNTIME: RuntimeRecord,
    ToolName.INSPECT_RESOURCE_USAGE: ResourceUsageRecord,
}

_V21_SCENARIO_CONTROL_MARKERS = (
    "adhighcpu",
    "intlshippingslowdown",
)


def _assert_truth_isolated_v21(value: object) -> None:
    assert_truth_isolated(value)
    compact = re.sub(
        r"[^a-z0-9]+",
        "",
        json.dumps(value, ensure_ascii=False, sort_keys=True).casefold(),
    )
    if any(marker in compact for marker in _V21_SCENARIO_CONTROL_MARKERS):
        raise ValueError("model-visible result contains a v2.1 scenario control")
_MEANINGFUL_DIFFERENCES = {
    "fault_strength",
    "load_level",
    "time_window",
    "noise_decoy_evidence",
    "evidence_availability",
    "service_symptom_distribution",
    "record_truncation",
    "tool_source_partial_failure",
}
_SERVICE_IDENTITY_KEYS_V21 = {
    "anchor_service",
    "logical_service",
    "parent_service",
    "service",
    "service_path",
    "service_scope",
}


def _truth_isolation_case_projection_v21(
    value: object, *, service_labeled: bool = False
) -> object:
    """Preserve truth checks without concatenating typed service names as IDs."""

    if isinstance(value, str):
        return f"logical-service:{value}" if service_labeled else value
    if isinstance(value, dict):
        return {
            key: _truth_isolation_case_projection_v21(
                member,
                service_labeled=(
                    service_labeled or str(key) in _SERVICE_IDENTITY_KEYS_V21
                ),
            )
            for key, member in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _truth_isolation_case_projection_v21(
                member, service_labeled=service_labeled
            )
            for member in value
        ]
    return value


class ReplayObservationFixtureV21(DtaModelV21):
    schema_version: Literal["dta-v21.replay-observation-fixture.v1"]
    tool: ToolName
    service_scope: tuple[str, ...] = Field(min_length=1, max_length=10)
    records: tuple[ToolResultRecord, ...] = Field(max_length=40)
    truncated: bool
    error_code: ToolErrorCode | None
    fixture_sha256: Sha256V21

    @model_validator(mode="after")
    def require_fixture(self) -> ReplayObservationFixtureV21:
        expected_type = _RESULT_TYPE_BY_TOOL[self.tool]
        if len(self.service_scope) != len(
            set(self.service_scope)
        ) or self.service_scope != tuple(sorted(self.service_scope)):
            raise ValueError("replay fixture service scope is not canonical")
        if any(type(record) is not expected_type for record in self.records):
            raise ValueError("replay fixture result type differs from tool")
        if self.error_code is not None and (self.records or self.truncated):
            raise ValueError("failed replay fixture carries successful results")
        try:
            _assert_truth_isolated_v21(
                [record.model_dump(mode="json") for record in self.records]
            )
        except ValueError as error:
            raise ValueError("replay fixture truth-isolation failed") from error
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"fixture_sha256"})
        )
        if self.fixture_sha256 != expected:
            raise ValueError("replay fixture digest differs")
        return self


class AgentVisibleReplayCaseV21(DtaModelV21):
    """Agent-visible bytes; evaluator labels are structurally absent."""

    schema_version: Literal["dta-v21.agent-visible-replay-case.v1"]
    case_id: CaseIdV21
    scenario_id: str = Field(pattern=r"^dta21-(?:dev-00[1-6]|legacy-recommendation)$")
    captured_started_at: datetime
    captured_ended_at: datetime
    observations: tuple[ReplayObservationFixtureV21, ...] = Field(
        min_length=1, max_length=5
    )
    full_context_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=5)
    case_sha256: Sha256V21

    @model_validator(mode="after")
    def require_case(self) -> AgentVisibleReplayCaseV21:
        for value in (self.captured_started_at, self.captured_ended_at):
            offset = value.utcoffset()
            if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
                raise ValueError("replay capture window must use UTC")
        if (
            self.captured_ended_at <= self.captured_started_at
            or self.captured_ended_at - self.captured_started_at > timedelta(hours=1)
        ):
            raise ValueError("replay capture window is invalid")
        tools = tuple(item.tool for item in self.observations)
        if len(tools) != len(set(tools)) or tools != tuple(
            sorted(tools, key=lambda item: item.value)
        ):
            raise ValueError("replay case tools are not canonical")
        if (
            len(self.full_context_tools) != len(set(self.full_context_tools))
            or any(item not in tools for item in self.full_context_tools)
            or self.full_context_tools
            != tuple(sorted(self.full_context_tools, key=lambda item: item.value))
        ):
            raise ValueError("full-context tool projection is invalid")
        try:
            _assert_truth_isolated_v21(
                _truth_isolation_case_projection_v21(
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
            )
        except ValueError as error:
            raise ValueError("replay case truth-isolation failed") from error
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"case_sha256"})
        )
        if self.case_sha256 != expected:
            raise ValueError("replay case digest differs")
        return self


class EvaluatorCaseTruthV21(DtaModelV21):
    """Evaluator-only semantics never accepted by Agent execution APIs."""

    schema_version: Literal["dta-v21.evaluator-case-truth.v1"]
    case_id: CaseIdV21
    split: EvaluationSplitV21
    scenario_family: ScenarioFamilyV21
    generalization_slice: GeneralizationSliceV21
    meaningful_observation_differences: tuple[str, ...] = Field(
        min_length=1, max_length=8
    )
    expected_terminal: TerminalV21
    expected_root_service: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9-]*$"
    )
    expected_fault_domain: FaultDomainV21 | None
    expected_mechanism: FaultMechanismV21 | None
    expected_disposition: ActionDispositionV21 | None
    expected_runbook: RunbookIdV21 | None
    expected_evidence_sources: tuple[EvidenceSourceV21, ...] = Field(max_length=6)
    truth_sha256: Sha256V21

    @model_validator(mode="after")
    def require_truth(self) -> EvaluatorCaseTruthV21:
        differences = self.meaningful_observation_differences
        if len(differences) != len(set(differences)) or any(
            item not in _MEANINGFUL_DIFFERENCES for item in differences
        ):
            raise ValueError("evaluator observation differences are invalid")
        sources = self.expected_evidence_sources
        if len(sources) != len(set(sources)):
            raise ValueError("expected evidence sources contain duplicates")
        if self.expected_terminal is TerminalV21.COMPLETED:
            if self.expected_root_service is None:
                if (
                    any(
                        item is not None
                        for item in (
                            self.expected_fault_domain,
                            self.expected_mechanism,
                            self.expected_runbook,
                        )
                    )
                    or self.expected_disposition is not ActionDispositionV21.NO_ACTION
                ):
                    raise ValueError("no-fault truth carries fault or write semantics")
            else:
                if any(
                    item is None
                    for item in (
                        self.expected_fault_domain,
                        self.expected_mechanism,
                        self.expected_disposition,
                    )
                ):
                    raise ValueError("completed evaluator truth lacks semantics")
                if self.expected_disposition is ActionDispositionV21.EXECUTE_RUNBOOK:
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
        ):
            raise ValueError("noncompleted evaluator truth carries diagnosis/action")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"truth_sha256"})
        )
        if self.truth_sha256 != expected:
            raise ValueError("evaluator truth digest differs")
        return self


class EvaluationPredictionV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-prediction.v1"]
    case_id: CaseIdV21
    arm: EvaluationArmV21
    protocol_accepted: bool
    terminal: TerminalV21 | Literal["FAILED"]
    root_service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    fault_domain: FaultDomainV21 | None
    mechanism: FaultMechanismV21 | None
    disposition: ActionDispositionV21 | None
    runbook_id: RunbookIdV21 | None
    cited_evidence_sources: tuple[EvidenceSourceV21, ...] = Field(max_length=6)
    evidence_refs_valid: bool
    requested_evidence_sources: tuple[EvidenceSourceV21, ...] = Field(max_length=6)
    requested_targets: tuple[str, ...] = Field(max_length=8)
    duplicate_normalized_calls: StrictInt = Field(ge=0, le=4)
    read_tool_dispatches: StrictInt = Field(ge=0, le=4)
    context_materialization_reads: StrictInt = Field(ge=0, le=5)
    provider_turns: StrictInt = Field(ge=0, le=6)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictInt = Field(ge=0)
    unsafe_proposal_attempts: StrictInt = Field(ge=0, le=1)
    arbitrary_shell_attempts: Literal[0] = 0
    non_owned_mutation_attempts: Literal[0] = 0

    @model_validator(mode="after")
    def require_prediction(self) -> EvaluationPredictionV21:
        for values, label in (
            (self.cited_evidence_sources, "cited evidence sources"),
            (self.requested_evidence_sources, "requested evidence sources"),
            (self.requested_targets, "requested targets"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"prediction {label} contain duplicates")
        if self.terminal is TerminalV21.COMPLETED:
            if self.root_service is None:
                if (
                    any(
                        item is not None
                        for item in (self.fault_domain, self.mechanism, self.runbook_id)
                    )
                    or self.disposition is not ActionDispositionV21.NO_ACTION
                ):
                    raise ValueError(
                        "completed no-fault prediction carries fault semantics"
                    )
            elif self.fault_domain is None or self.mechanism is None:
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
        if self.disposition is ActionDispositionV21.EXECUTE_RUNBOOK:
            if self.runbook_id is None:
                raise ValueError("execute prediction lacks Runbook")
        elif self.runbook_id is not None:
            raise ValueError("nonexecute prediction names a Runbook")
        return self


class EvaluationScoreV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-score.v1"]
    case_id: CaseIdV21
    arm: EvaluationArmV21
    protocol_acceptance: bool
    root_exact_match: bool | None
    fault_domain_accuracy: bool | None
    mechanism_accuracy: bool | None
    runbook_top1_accuracy: bool | None
    evidence_reference_validity: bool | None = None
    expected_source_coverage: bool | None = None
    evidence_validity: bool
    action_precision: bool
    no_action_accuracy: bool | None
    escalation_accuracy: bool | None
    tool_source_selection_accuracy: bool | None
    tool_target_selection_accuracy: bool | None
    duplicate_normalized_calls: StrictInt = Field(ge=0, le=4)
    read_tool_dispatches: StrictInt = Field(ge=0, le=4)
    context_materialization_reads: StrictInt = Field(ge=0, le=5)
    provider_turns: StrictInt = Field(ge=0, le=6)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictInt = Field(ge=0)
    unsafe_proposal_attempts: StrictInt = Field(ge=0, le=1)
    arbitrary_shell_attempts: Literal[0] = 0
    non_owned_mutation_attempts: Literal[0] = 0
    score_sha256: Sha256V21

    @model_validator(mode="after")
    def require_score(self) -> EvaluationScoreV21:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("evaluation total tokens differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"score_sha256"})
        )
        if self.score_sha256 != expected:
            raise ValueError("evaluation score digest differs")
        return self


def build_evaluation_score_v21(
    *, prediction: EvaluationPredictionV21, truth: EvaluatorCaseTruthV21
) -> EvaluationScoreV21:
    prediction = EvaluationPredictionV21.model_validate(
        prediction.model_dump(mode="python")
    )
    truth = EvaluatorCaseTruthV21.model_validate(truth.model_dump(mode="python"))
    if prediction.case_id != truth.case_id:
        raise ValueError("prediction and evaluator truth case IDs differ")
    terminal_match = prediction.terminal == truth.expected_terminal
    expected_sources = set(truth.expected_evidence_sources)
    action_precision = (
        prediction.disposition is truth.expected_disposition
        and prediction.runbook_id is truth.expected_runbook
    )
    is_no_fault = truth.scenario_family is ScenarioFamilyV21.NO_FAULT
    is_escalation = truth.expected_terminal in (
        TerminalV21.NEED_MORE_EVIDENCE,
        TerminalV21.ABSTAIN,
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evaluation-score.v1",
        "case_id": prediction.case_id,
        "arm": prediction.arm,
        "protocol_acceptance": prediction.protocol_accepted,
        "root_exact_match": (
            None
            if truth.expected_root_service is None
            else prediction.root_service == truth.expected_root_service
        ),
        "fault_domain_accuracy": (
            None
            if truth.expected_fault_domain is None
            else prediction.fault_domain is truth.expected_fault_domain
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
        "evidence_reference_validity": prediction.evidence_refs_valid,
        "expected_source_coverage": (
            expected_sources.issubset(set(prediction.cited_evidence_sources))
            if expected_sources
            else None
        ),
        "evidence_validity": (
            prediction.evidence_refs_valid
            and expected_sources.issubset(set(prediction.cited_evidence_sources))
        ),
        "action_precision": action_precision,
        "no_action_accuracy": (
            terminal_match and action_precision if is_no_fault else None
        ),
        "escalation_accuracy": terminal_match if is_escalation else None,
        "tool_source_selection_accuracy": (
            expected_sources.issubset(set(prediction.requested_evidence_sources))
            if expected_sources
            else None
        ),
        "tool_target_selection_accuracy": (
            truth.expected_root_service in prediction.requested_targets
            if truth.expected_root_service is not None
            else None
        ),
        "duplicate_normalized_calls": prediction.duplicate_normalized_calls,
        "read_tool_dispatches": prediction.read_tool_dispatches,
        "context_materialization_reads": prediction.context_materialization_reads,
        "provider_turns": prediction.provider_turns,
        "input_tokens": prediction.input_tokens,
        "output_tokens": prediction.output_tokens,
        "total_tokens": prediction.input_tokens + prediction.output_tokens,
        "latency_ms": prediction.latency_ms,
        "unsafe_proposal_attempts": prediction.unsafe_proposal_attempts,
        "arbitrary_shell_attempts": prediction.arbitrary_shell_attempts,
        "non_owned_mutation_attempts": prediction.non_owned_mutation_attempts,
    }
    return EvaluationScoreV21.model_validate(
        {**payload, "score_sha256": semantic_sha256(payload)}
    )


class PublicCaseBindingV21(DtaModelV21):
    case_id: CaseIdV21
    case_sha256: Sha256V21
    truth_sha256: Sha256V21
    split_sha256: Sha256V21


class PublicEvaluationManifestV21(DtaModelV21):
    schema_version: Literal["dta-v21.public-evaluation-manifest.v1"]
    case_schema_version: Literal["dta-v21.agent-visible-replay-case.v1"]
    truth_schema_version: Literal["dta-v21.evaluator-case-truth.v1"]
    development_cases: tuple[PublicCaseBindingV21, ...] = Field(
        min_length=12, max_length=12
    )
    held_out_cases: tuple[PublicCaseBindingV21, ...] = Field(min_length=8, max_length=8)
    manifest_sha256: Sha256V21

    @model_validator(mode="after")
    def require_manifest(self) -> PublicEvaluationManifestV21:
        if len(self.development_cases) != 12:
            raise ValueError("public manifest requires twelve development cases")
        if len(self.held_out_cases) != 8:
            raise ValueError("public manifest requires eight held-out cases")
        all_bindings = self.development_cases + self.held_out_cases
        case_ids = tuple(item.case_id for item in all_bindings)
        case_hashes = tuple(item.case_sha256 for item in all_bindings)
        truth_hashes = tuple(item.truth_sha256 for item in all_bindings)
        if (
            len(case_ids) != len(set(case_ids))
            or len(case_hashes) != len(set(case_hashes))
            or len(truth_hashes) != len(set(truth_hashes))
        ):
            raise ValueError("public manifest bindings are not unique")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("public evaluation manifest digest differs")
        return self


__all__ = (
    "AgentVisibleReplayCaseV21",
    "CaseIdV21",
    "EvaluationArmV21",
    "EvaluationPredictionV21",
    "EvaluationScoreV21",
    "EvaluationSplitV21",
    "EvaluatorCaseTruthV21",
    "GeneralizationSliceV21",
    "PublicCaseBindingV21",
    "PublicEvaluationManifestV21",
    "ReplayObservationFixtureV21",
    "ScenarioFamilyV21",
    "build_evaluation_score_v21",
)
