"""One-claim, six-entry replay held-out execution for frozen PR-E."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.agent import AgentRunTerminal
from ecomsre.dta_v2.agent_contracts import build_alert_context
from ecomsre.dta_v2.agent_evidence import _write_private_json
from ecomsre.dta_v2.agent_provider import (
    ACTION_SELECTION_SYSTEM_PROMPT,
    INVESTIGATION_SYSTEM_PROMPT,
    OpenAICompatibleDtaAgentProvider,
    build_provider_identity,
)
from ecomsre.dta_v2.capture_campaign import CaptureCampaignClosure, CaptureTerminal
from ecomsre.dta_v2.contracts import DtaModel, Sha256, semantic_sha256
from ecomsre.dta_v2.evaluation_campaign import (
    DevelopmentCampaignReport,
    MetricCount,
)
from ecomsre.dta_v2.evaluation_contracts import (
    AgentVisibleReplayCase,
    EvaluationArm,
    EvaluationScore,
    EvaluationSplit,
    EvaluatorCaseTruth,
    HeldOutSeal,
    OpaqueExecutionId,
    build_held_out_seal,
    persist_held_out_execution_claim,
)
from ecomsre.dta_v2.evaluation_dataset import (
    PublicEvaluationDatasetManifest,
    load_public_evaluation_dataset,
)
from ecomsre.dta_v2.evaluation_runner import (
    EvaluationEntryResult,
    execute_evaluation_arm,
    score_and_persist_evaluation_execution,
)
from ecomsre.dta_v2.provider_development_smoke import ProhibitedActionCounters
from ecomsre.dta_v2.registry import load_runbook_registry, load_scenario_registry
from ecomsre.model.gateway import OpenAICompatibleConfig


class FrozenInputHashes(DtaModel):
    one_shot_prompt_sha256: Sha256
    adaptive_prompt_sha256: Sha256
    budgets_sha256: Sha256
    candidate_filter_sha256: Sha256
    scorer_sha256: Sha256


class HeldOutScheduleEntry(DtaModel):
    schema_version: Literal["dta-v2.held-out-schedule-entry.v1"]
    ordinal: StrictInt = Field(ge=1, le=6)
    execution_id: OpaqueExecutionId
    case_id: str = Field(pattern=r"^dta-case-00[7-9]$")
    case_sha256: Sha256
    truth_sha256: Sha256
    split: Literal[EvaluationSplit.HELD_OUT]
    arm: EvaluationArm


class HeldOutSchedule(DtaModel):
    schema_version: Literal["dta-v2.held-out-schedule.v1"]
    execution_id: OpaqueExecutionId
    seal_sha256: Sha256
    entries: tuple[HeldOutScheduleEntry, ...] = Field(min_length=6, max_length=6)
    schedule_sha256: Sha256

    @model_validator(mode="after")
    def require_schedule(self) -> HeldOutSchedule:
        if tuple(item.ordinal for item in self.entries) != tuple(range(1, 7)):
            raise ValueError("held-out schedule ordinals differ")
        if tuple(item.case_id for item in self.entries[::2]) != (
            "dta-case-007",
            "dta-case-008",
            "dta-case-009",
        ):
            raise ValueError("held-out schedule case order differs")
        for index in range(0, 6, 2):
            left, right = self.entries[index : index + 2]
            if (
                left.case_id != right.case_id
                or left.case_sha256 != right.case_sha256
                or left.truth_sha256 != right.truth_sha256
                or left.arm is not EvaluationArm.ONE_SHOT_FULL_CONTEXT
                or right.arm is not EvaluationArm.ADAPTIVE_TOOL_USING
            ):
                raise ValueError("held-out schedule arm pair differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"schedule_sha256"})
        )
        if self.schedule_sha256 != expected:
            raise ValueError("held-out schedule digest differs")
        return self


class HeldOutArmAggregate(DtaModel):
    arm: EvaluationArm
    entry_count: Literal[3]
    root_exact_match: MetricCount
    mechanism_accuracy: MetricCount
    runbook_top1_accuracy: MetricCount
    evidence_validity: MetricCount
    action_precision: MetricCount
    no_action_accuracy: MetricCount
    escalation_accuracy: MetricCount
    read_tool_dispatches_total: StrictInt = Field(ge=0)
    context_materialization_reads_total: StrictInt = Field(ge=0)
    provider_turns_total: StrictInt = Field(ge=0)
    input_tokens_total: StrictInt = Field(ge=0)
    output_tokens_total: StrictInt = Field(ge=0)
    latency_ms_total: StrictInt = Field(ge=0)
    unsafe_proposal_attempts: StrictInt = Field(ge=0)


class HeldOutCampaignReport(DtaModel):
    schema_version: Literal["dta-v2.held-out-campaign-report.v1"]
    execution_id: OpaqueExecutionId
    seal_sha256: Sha256
    claim_sha256: Sha256
    schedule_sha256: Sha256
    terminal: Literal["COMPLETED", "BLOCKED_UNSAFE"]
    entries: tuple[EvaluationEntryResult, ...] = Field(min_length=6, max_length=6)
    arm_aggregates: tuple[HeldOutArmAggregate, HeldOutArmAggregate]
    truth_isolation_pass: Literal[True]
    scorer_verification_pass: Literal[True]
    prohibited_action_counters: ProhibitedActionCounters
    report_sha256: Sha256

    @model_validator(mode="after")
    def require_report(self) -> HeldOutCampaignReport:
        if tuple(item.arm for item in self.arm_aggregates) != (
            EvaluationArm.ONE_SHOT_FULL_CONTEXT,
            EvaluationArm.ADAPTIVE_TOOL_USING,
        ):
            raise ValueError("held-out report arm order differs")
        unsafe = sum(item.unsafe_proposal_attempts for item in self.arm_aggregates)
        if (self.terminal == "BLOCKED_UNSAFE") is not (unsafe > 0):
            raise ValueError("held-out report terminal differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("held-out report digest differs")
        return self


CaseBinding = tuple[str, str, str]


def _file_sha256(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("frozen source path is invalid")
    target = path.resolve()
    if not target.is_file():
        raise ValueError("frozen source path is invalid")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def build_frozen_input_hashes(repository_root: Path) -> FrozenInputHashes:
    repository = Path(repository_root).resolve()
    prompt_common = {
        "investigation_system_prompt": INVESTIGATION_SYSTEM_PROMPT,
        "action_selection_system_prompt": ACTION_SELECTION_SYSTEM_PROMPT,
    }
    return FrozenInputHashes(
        one_shot_prompt_sha256=semantic_sha256(
            {
                **prompt_common,
                "arm": EvaluationArm.ONE_SHOT_FULL_CONTEXT,
                "read_tools_enabled": False,
                "context_materialization_reads": 4,
            }
        ),
        adaptive_prompt_sha256=semantic_sha256(
            {
                **prompt_common,
                "arm": EvaluationArm.ADAPTIVE_TOOL_USING,
                "read_tools_enabled": True,
                "maximum_read_tool_dispatches": 4,
                "maximum_repeated_identical_calls": 0,
            }
        ),
        budgets_sha256=semantic_sha256(
            {
                "maximum_read_tool_dispatches": 4,
                "maximum_repeated_identical_calls": 0,
                "one_shot_context_materialization_reads": 4,
                "provider_timeout_seconds": 120.0,
                "maximum_completion_tokens": 2048,
                "temperature": 0.0,
            }
        ),
        candidate_filter_sha256=_file_sha256(
            repository / "src/ecomsre/dta_v2/candidate_filter.py"
        ),
        scorer_sha256=_file_sha256(
            repository / "src/ecomsre/dta_v2/evaluation_contracts.py"
        ),
    )


def _entry_execution_id(
    *, execution_id: str, case_id: str, arm: EvaluationArm
) -> str:
    return semantic_sha256(
        {"execution_id": execution_id, "case_id": case_id, "arm": arm}
    )[:32]


def build_held_out_schedule(
    *,
    execution_id: str,
    seal: HeldOutSeal,
    case_bindings: tuple[CaseBinding, CaseBinding, CaseBinding],
) -> HeldOutSchedule:
    seal = HeldOutSeal.model_validate(seal.model_dump())
    ordered = tuple(sorted(case_bindings, key=lambda item: item[0]))
    if tuple(item[0] for item in ordered) != (
        "dta-case-007",
        "dta-case-008",
        "dta-case-009",
    ):
        raise ValueError("held-out case projection differs")
    if (
        set(item[1] for item in ordered) != set(seal.held_out_case_sha256s)
        or set(item[2] for item in ordered) != set(seal.evaluator_truth_sha256s)
    ):
        raise ValueError("held-out digest projection differs")
    entries: list[dict[str, object]] = []
    for case_id, case_sha256, truth_sha256 in ordered:
        for arm in (
            EvaluationArm.ONE_SHOT_FULL_CONTEXT,
            EvaluationArm.ADAPTIVE_TOOL_USING,
        ):
            entries.append(
                {
                    "schema_version": "dta-v2.held-out-schedule-entry.v1",
                    "ordinal": len(entries) + 1,
                    "execution_id": _entry_execution_id(
                        execution_id=execution_id,
                        case_id=case_id,
                        arm=arm,
                    ),
                    "case_id": case_id,
                    "case_sha256": case_sha256,
                    "truth_sha256": truth_sha256,
                    "split": EvaluationSplit.HELD_OUT,
                    "arm": arm,
                }
            )
    payload: dict[str, object] = {
        "schema_version": "dta-v2.held-out-schedule.v1",
        "execution_id": execution_id,
        "seal_sha256": seal.seal_sha256,
        "entries": tuple(entries),
    }
    return HeldOutSchedule.model_validate(
        {**payload, "schedule_sha256": semantic_sha256(payload)}
    )


def _verify_exact_clean_head(repository: Path, base_head: str) -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != base_head:
        raise ValueError("held-out base HEAD differs")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ValueError("held-out worktree has tracked changes")


def _load_development_pass(
    path: Path, *, model_id: str, identity_sha256: str
) -> DevelopmentCampaignReport:
    report = DevelopmentCampaignReport.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    if report.terminal != "PASS":
        raise ValueError("held-out freeze requires development PASS")
    if any(
        item.model_id != model_id or item.identity_sha256 != identity_sha256
        for item in report.entries
    ):
        raise ValueError("development PASS identity differs from held-out freeze")
    return report


def _load_capture_bindings(
    *,
    capture_root: Path,
    dataset: PublicEvaluationDatasetManifest,
) -> tuple[
    tuple[CaseBinding, CaseBinding, CaseBinding],
    dict[str, tuple[AgentVisibleReplayCase, EvaluatorCaseTruth]],
]:
    source = Path(capture_root)
    if source.is_symlink():
        raise ValueError("held-out capture root is a symbolic link")
    root = source.resolve()
    closure = CaptureCampaignClosure.model_validate_json(
        (root / "capture-campaign-closure.json").read_text(encoding="utf-8")
    )
    if (
        closure.terminal is not CaptureTerminal.PASS
        or closure.closure_sha256 != dataset.capture_closure_sha256
    ):
        raise ValueError("held-out capture closure differs")
    bindings: list[CaseBinding] = []
    loaded: dict[str, tuple[AgentVisibleReplayCase, EvaluatorCaseTruth]] = {}
    for case_id in ("dta-case-007", "dta-case-008", "dta-case-009"):
        directory = root / "cases" / case_id
        case_path = directory / "agent-visible.json"
        truth_path = directory / "evaluator-truth.json"
        if directory.is_symlink() or case_path.is_symlink() or truth_path.is_symlink():
            raise ValueError("held-out capture path is a symbolic link")
        case = AgentVisibleReplayCase.model_validate_json(
            case_path.read_text(encoding="utf-8")
        )
        truth = EvaluatorCaseTruth.model_validate_json(
            truth_path.read_text(encoding="utf-8")
        )
        if (
            case.case_id != case_id
            or truth.case_id != case_id
            or truth.split is not EvaluationSplit.HELD_OUT
            or case.case_sha256 not in closure.captured_case_sha256s
        ):
            raise ValueError("held-out capture binding differs")
        bindings.append((case_id, case.case_sha256, truth.truth_sha256))
        loaded[case_id] = (case, truth)
    if (
        set(item[1] for item in bindings) != set(dataset.held_out_case_sha256s)
        or set(item[2] for item in bindings) != set(dataset.held_out_truth_sha256s)
    ):
        raise ValueError("held-out public digest projection differs")
    return (bindings[0], bindings[1], bindings[2]), loaded


def _build_current_seal(
    *,
    repository: Path,
    capture_root: Path,
    development_report_path: Path,
    base_head: str,
    model_id: str,
) -> tuple[
    HeldOutSeal,
    tuple[CaseBinding, CaseBinding, CaseBinding],
    dict[str, tuple[AgentVisibleReplayCase, EvaluatorCaseTruth]],
]:
    _verify_exact_clean_head(repository, base_head)
    identity = build_provider_identity(model_id)
    _load_development_pass(
        development_report_path,
        model_id=model_id,
        identity_sha256=identity.identity_sha256,
    )
    dataset, _ = load_public_evaluation_dataset(
        repository / "config/dta-v2/evaluation"
    )
    bindings, loaded = _load_capture_bindings(
        capture_root=capture_root,
        dataset=dataset,
    )
    frozen = build_frozen_input_hashes(repository)
    registry = load_runbook_registry(repository / "config/dta-v2/runbooks")
    seal = build_held_out_seal(
        base_head=base_head,
        model_id=model_id,
        agent_identity_sha256=identity.identity_sha256,
        one_shot_prompt_sha256=frozen.one_shot_prompt_sha256,
        adaptive_prompt_sha256=frozen.adaptive_prompt_sha256,
        tool_schema_sha256=identity.tool_schema_sha256,
        budgets_sha256=frozen.budgets_sha256,
        diagnosis_schema_sha256=identity.diagnosis_schema_sha256,
        runbook_registry_sha256=registry.registry_sha256,
        candidate_filter_sha256=frozen.candidate_filter_sha256,
        action_schema_sha256=identity.action_proposal_schema_sha256,
        scorer_sha256=frozen.scorer_sha256,
        held_out_case_sha256s=tuple(sorted(item[1] for item in bindings)),
        evaluator_truth_sha256s=tuple(sorted(item[2] for item in bindings)),
    )
    return seal, bindings, loaded


def freeze_held_out_campaign(
    *,
    repository_root: Path,
    capture_root: Path,
    private_root: Path,
    development_report_path: Path,
    execution_id: str,
    base_head: str,
    model_id: str,
) -> tuple[HeldOutSeal, HeldOutSchedule]:
    repository = Path(repository_root).resolve()
    seal, bindings, _ = _build_current_seal(
        repository=repository,
        capture_root=capture_root,
        development_report_path=development_report_path,
        base_head=base_head,
        model_id=model_id,
    )
    schedule = build_held_out_schedule(
        execution_id=execution_id,
        seal=seal,
        case_bindings=bindings,
    )
    root = Path(private_root).resolve()
    _write_private_json(root / "held-out-seal.json", seal)
    _write_private_json(root / "schedule.json", schedule)
    return seal, schedule


def _metric(scores: list[EvaluationScore], field: str) -> MetricCount:
    values = [getattr(item, field) for item in scores]
    applicable = [item for item in values if item is not None]
    return MetricCount(
        correct=sum(item is True for item in applicable),
        denominator=len(applicable),
    )


def _arm_aggregate(
    *, arm: EvaluationArm, entries: tuple[EvaluationEntryResult, ...]
) -> HeldOutArmAggregate:
    selected = [item for item in entries if item.arm is arm]
    if len(selected) != 3:
        raise ValueError("held-out arm aggregate requires three entries")
    scores = [item.score for item in selected]
    return HeldOutArmAggregate(
        arm=arm,
        entry_count=3,
        root_exact_match=_metric(scores, "root_exact_match"),
        mechanism_accuracy=_metric(scores, "mechanism_accuracy"),
        runbook_top1_accuracy=_metric(scores, "runbook_top1_accuracy"),
        evidence_validity=_metric(scores, "evidence_validity"),
        action_precision=_metric(scores, "action_precision"),
        no_action_accuracy=_metric(scores, "no_action_accuracy"),
        escalation_accuracy=_metric(scores, "escalation_accuracy"),
        read_tool_dispatches_total=sum(item.read_tool_dispatches for item in scores),
        context_materialization_reads_total=sum(
            item.context_materialization_reads for item in scores
        ),
        provider_turns_total=sum(item.provider_turns for item in scores),
        input_tokens_total=sum(item.input_tokens for item in scores),
        output_tokens_total=sum(item.output_tokens for item in scores),
        latency_ms_total=sum(item.latency_ms for item in scores),
        unsafe_proposal_attempts=sum(
            item.unsafe_proposal_attempts for item in scores
        ),
    )


def _build_report(
    *,
    seal: HeldOutSeal,
    schedule: HeldOutSchedule,
    claim_sha256: str,
    entries: tuple[EvaluationEntryResult, ...],
) -> HeldOutCampaignReport:
    by_id = {item.execution_id: item for item in entries}
    ordered = tuple(by_id[item.execution_id] for item in schedule.entries)
    aggregates = tuple(
        _arm_aggregate(arm=arm, entries=ordered)
        for arm in (
            EvaluationArm.ONE_SHOT_FULL_CONTEXT,
            EvaluationArm.ADAPTIVE_TOOL_USING,
        )
    )
    assert len(aggregates) == 2
    unsafe = sum(item.unsafe_proposal_attempts for item in aggregates)
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.held-out-campaign-report.v1",
        "execution_id": schedule.execution_id,
        "seal_sha256": seal.seal_sha256,
        "claim_sha256": claim_sha256,
        "schedule_sha256": schedule.schedule_sha256,
        "terminal": "BLOCKED_UNSAFE" if unsafe else "COMPLETED",
        "entries": ordered,
        "arm_aggregates": aggregates,
        "truth_isolation_pass": True,
        "scorer_verification_pass": True,
        "prohibited_action_counters": ProhibitedActionCounters(),
    }
    draft = HeldOutCampaignReport.model_construct(
        **payload, report_sha256="0" * 64
    )
    return HeldOutCampaignReport.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


def _entry_claim_payload(
    *, seal: HeldOutSeal, schedule: HeldOutSchedule, entry: HeldOutScheduleEntry
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.held-out-entry-claim.v1",
        "seal_sha256": seal.seal_sha256,
        "schedule_sha256": schedule.schedule_sha256,
        "execution_id": entry.execution_id,
        "ordinal": entry.ordinal,
        "case_sha256": entry.case_sha256,
        "truth_sha256": entry.truth_sha256,
        "arm": entry.arm,
    }
    return {**payload, "claim_sha256": semantic_sha256(payload)}


def run_frozen_held_out_campaign(
    *,
    repository_root: Path,
    capture_root: Path,
    private_root: Path,
    development_report_path: Path,
    base_head: str,
    config: OpenAICompatibleConfig,
    provider_factory: Callable[[], OpenAICompatibleDtaAgentProvider] | None = None,
) -> HeldOutCampaignReport:
    repository = Path(repository_root).resolve()
    root = Path(private_root).resolve()
    seal = HeldOutSeal.model_validate_json(
        (root / "held-out-seal.json").read_text(encoding="utf-8")
    )
    schedule = HeldOutSchedule.model_validate_json(
        (root / "schedule.json").read_text(encoding="utf-8")
    )
    current, bindings, loaded = _build_current_seal(
        repository=repository,
        capture_root=capture_root,
        development_report_path=development_report_path,
        base_head=base_head,
        model_id=config.model,
    )
    if current != seal or schedule.seal_sha256 != seal.seal_sha256:
        raise ValueError("held-out frozen inputs differ")
    expected_schedule = build_held_out_schedule(
        execution_id=schedule.execution_id,
        seal=seal,
        case_bindings=bindings,
    )
    if schedule != expected_schedule:
        raise ValueError("held-out schedule differs")
    report_path = root / "held-out-campaign-report.json"
    if report_path.exists():
        existing_report = HeldOutCampaignReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
        if (
            existing_report.execution_id != schedule.execution_id
            or existing_report.seal_sha256 != seal.seal_sha256
            or existing_report.schedule_sha256 != schedule.schedule_sha256
        ):
            raise ValueError("existing held-out report differs")
        return existing_report
    claim = persist_held_out_execution_claim(
        root / "held-out-execution-claim.json",
        seal=seal,
        execution_id=schedule.execution_id,
    )
    scenarios = {
        item.scenario_id: item
        for item in load_scenario_registry(
            repository / "config/dta-v2/scenarios/agent-visible"
        ).scenarios
    }
    registry = load_runbook_registry(repository / "config/dta-v2/runbooks")
    results: list[EvaluationEntryResult] = []
    for scheduled in schedule.entries:
        entry_root = root / "entries" / f"{scheduled.ordinal:02d}"
        terminal_path = entry_root / "entry-result.json"
        claim_path = entry_root / "entry-claim.json"
        if terminal_path.exists():
            existing = EvaluationEntryResult.model_validate_json(
                terminal_path.read_text(encoding="utf-8")
            )
            if (
                existing.execution_id != scheduled.execution_id
                or existing.case_sha256 != scheduled.case_sha256
                or existing.truth_sha256 != scheduled.truth_sha256
                or existing.split is not EvaluationSplit.HELD_OUT
                or existing.arm is not scheduled.arm
                or existing.model_id != seal.model_id
                or existing.identity_sha256 != seal.agent_identity_sha256
            ):
                raise ValueError("existing held-out entry differs")
            results.append(existing)
            continue
        if claim_path.exists():
            raise RuntimeError("claimed held-out entry lacks terminal; rerun forbidden")
        _write_private_json(
            claim_path,
            _entry_claim_payload(seal=seal, schedule=schedule, entry=scheduled),
        )
        case, truth = loaded[scheduled.case_id]
        context = build_alert_context(
            scenario=scenarios[case.scenario_id],
            run_id=scheduled.execution_id,
            started_at=case.captured_started_at,
            ended_at=case.captured_ended_at,
        )
        provider = (
            provider_factory()
            if provider_factory is not None
            else OpenAICompatibleDtaAgentProvider(
                config=config,
                timeout_seconds=120.0,
                max_completion_tokens=2048,
            )
        )
        execution = execute_evaluation_arm(
            case=case,
            context=context,
            arm=scheduled.arm,
            registry=registry,
            provider=provider,
        )
        entry = score_and_persist_evaluation_execution(
            execution=execution,
            truth=truth,
            execution_id=scheduled.execution_id,
            private_root=entry_root,
            forbidden_secrets=(config.api_key,),
        )
        if (
            execution.agent_result.terminal is AgentRunTerminal.FAILED
            and provider.last_safe_raw_response is not None
        ):
            _write_private_json(
                entry_root / "rejected-provider-response.json",
                provider.last_safe_raw_response,
            )
        results.append(entry)
    report = _build_report(
        seal=seal,
        schedule=schedule,
        claim_sha256=claim.claim_sha256,
        entries=tuple(results),
    )
    _write_private_json(report_path, report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze or run PR-E held-out once.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("freeze", "run"):
        child = subparsers.add_parser(command)
        child.add_argument("--repository-root", type=Path, required=True)
        child.add_argument("--capture-root", type=Path, required=True)
        child.add_argument("--private-root", type=Path, required=True)
        child.add_argument("--development-report", type=Path, required=True)
        child.add_argument("--base-head", required=True)
        if command == "freeze":
            child.add_argument("--execution-id", required=True)
            child.add_argument("--model-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "freeze":
        seal, schedule = freeze_held_out_campaign(
            repository_root=args.repository_root,
            capture_root=args.capture_root,
            private_root=args.private_root,
            development_report_path=args.development_report,
            execution_id=args.execution_id,
            base_head=args.base_head,
            model_id=args.model_id,
        )
        print(
            json.dumps(
                {
                    "terminal": "FROZEN",
                    "seal_sha256": seal.seal_sha256,
                    "schedule_sha256": schedule.schedule_sha256,
                    "entry_count": len(schedule.entries),
                },
                sort_keys=True,
            )
        )
        return 0
    config = OpenAICompatibleConfig.from_environment()
    if config is None:
        print(json.dumps({"terminal": "BLOCKED_PROVIDER_CREDENTIALS"}))
        return 2
    report = run_frozen_held_out_campaign(
        repository_root=args.repository_root,
        capture_root=args.capture_root,
        private_root=args.private_root,
        development_report_path=args.development_report,
        base_head=args.base_head,
        config=config,
    )
    unsafe = sum(item.unsafe_proposal_attempts for item in report.arm_aggregates)
    print(
        json.dumps(
            {
                "terminal": report.terminal,
                "entry_count": len(report.entries),
                "unsafe_proposal_attempts": unsafe,
                "report_sha256": report.report_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if report.terminal == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FrozenInputHashes",
    "HeldOutArmAggregate",
    "HeldOutCampaignReport",
    "HeldOutSchedule",
    "HeldOutScheduleEntry",
    "build_frozen_input_hashes",
    "build_held_out_schedule",
    "freeze_held_out_campaign",
    "run_frozen_held_out_campaign",
]
