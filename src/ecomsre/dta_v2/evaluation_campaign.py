"""Frozen development campaign and fixed-denominator PR-E aggregation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.agent import AgentRunTerminal
from ecomsre.dta_v2.agent_contracts import build_alert_context
from ecomsre.dta_v2.agent_evidence import _write_private_json
from ecomsre.dta_v2.agent_provider import (
    OpenAICompatibleDtaAgentProvider,
    build_provider_identity,
)
from ecomsre.dta_v2.contracts import DtaModel, Sha256, semantic_sha256
from ecomsre.dta_v2.evaluation_contracts import (
    EvaluationArm,
    EvaluationScore,
    EvaluationSplit,
    GitCommit,
    OpaqueExecutionId,
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
from ecomsre.dta_v2.registry import (
    load_runbook_registry,
    load_scenario_registry,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


class EvaluationScheduleEntry(DtaModel):
    schema_version: Literal["dta-v2.evaluation-schedule-entry.v1"]
    ordinal: StrictInt = Field(ge=1, le=18)
    execution_id: OpaqueExecutionId
    case_id: str = Field(pattern=r"^dta-case-[0-9]{3}$")
    case_sha256: Sha256
    truth_sha256: Sha256
    split: Literal[EvaluationSplit.DEVELOPMENT, EvaluationSplit.NO_ACTION]
    arm: EvaluationArm


class PublicDevelopmentSchedule(DtaModel):
    schema_version: Literal["dta-v2.public-development-schedule.v1"]
    campaign_id: OpaqueExecutionId
    base_head: GitCommit
    model_id: str
    identity_sha256: Sha256
    dataset_manifest_sha256: Sha256
    entries: tuple[EvaluationScheduleEntry, ...] = Field(
        min_length=18, max_length=18
    )
    schedule_sha256: Sha256

    @model_validator(mode="after")
    def require_schedule(self) -> PublicDevelopmentSchedule:
        if tuple(item.ordinal for item in self.entries) != tuple(range(1, 19)):
            raise ValueError("development schedule ordinals differ")
        for index in range(0, 18, 2):
            left, right = self.entries[index : index + 2]
            if (
                left.case_id != right.case_id
                or left.case_sha256 != right.case_sha256
                or left.truth_sha256 != right.truth_sha256
                or left.arm is not EvaluationArm.ONE_SHOT_FULL_CONTEXT
                or right.arm is not EvaluationArm.ADAPTIVE_TOOL_USING
            ):
                raise ValueError("development schedule arm pair differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"schedule_sha256"})
        )
        if self.schedule_sha256 != expected:
            raise ValueError("development schedule digest differs")
        return self


class MetricCount(DtaModel):
    correct: StrictInt = Field(ge=0)
    denominator: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_count(self) -> MetricCount:
        if self.correct > self.denominator:
            raise ValueError("metric numerator exceeds denominator")
        return self


class MeanCostMetrics(DtaModel):
    read_tool_dispatches: float = Field(ge=0)
    context_materialization_reads: float = Field(ge=0)
    provider_turns: float = Field(ge=0)
    input_tokens: float = Field(ge=0)
    output_tokens: float = Field(ge=0)
    total_tokens: float = Field(ge=0)
    latency_ms: float = Field(ge=0)


def build_mean_cost_metrics(
    *,
    entry_count: int,
    read_tool_dispatches_total: int,
    context_materialization_reads_total: int,
    provider_turns_total: int,
    input_tokens_total: int,
    output_tokens_total: int,
    latency_ms_total: int,
) -> MeanCostMetrics:
    if entry_count <= 0:
        raise ValueError("mean cost metrics require a positive entry count")
    return MeanCostMetrics(
        read_tool_dispatches=read_tool_dispatches_total / entry_count,
        context_materialization_reads=(
            context_materialization_reads_total / entry_count
        ),
        provider_turns=provider_turns_total / entry_count,
        input_tokens=input_tokens_total / entry_count,
        output_tokens=output_tokens_total / entry_count,
        total_tokens=(input_tokens_total + output_tokens_total) / entry_count,
        latency_ms=latency_ms_total / entry_count,
    )


class EvaluationArmAggregate(DtaModel):
    arm: EvaluationArm
    entry_count: Literal[9]
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

    @property
    def mean_costs(self) -> MeanCostMetrics:
        return build_mean_cost_metrics(
            entry_count=self.entry_count,
            read_tool_dispatches_total=self.read_tool_dispatches_total,
            context_materialization_reads_total=(
                self.context_materialization_reads_total
            ),
            provider_turns_total=self.provider_turns_total,
            input_tokens_total=self.input_tokens_total,
            output_tokens_total=self.output_tokens_total,
            latency_ms_total=self.latency_ms_total,
        )


class DevelopmentCampaignReport(DtaModel):
    schema_version: Literal["dta-v2.development-campaign-report.v1"]
    campaign_id: OpaqueExecutionId
    schedule_sha256: Sha256
    terminal: Literal["PASS", "FAIL"]
    entries: tuple[EvaluationEntryResult, ...] = Field(
        min_length=18, max_length=18
    )
    arm_aggregates: tuple[EvaluationArmAggregate, EvaluationArmAggregate]
    truth_isolation_pass: Literal[True]
    scorer_verification_pass: Literal[True]
    prohibited_action_counters: ProhibitedActionCounters
    report_sha256: Sha256

    @model_validator(mode="after")
    def require_report(self) -> DevelopmentCampaignReport:
        if tuple(item.arm for item in self.arm_aggregates) != (
            EvaluationArm.ONE_SHOT_FULL_CONTEXT,
            EvaluationArm.ADAPTIVE_TOOL_USING,
        ):
            raise ValueError("development report arm order differs")
        passed = all(_aggregate_is_exact(item) for item in self.arm_aggregates)
        if (self.terminal == "PASS") is not passed:
            raise ValueError("development report terminal differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("development report digest differs")
        return self


def _entry_execution_id(
    *, campaign_id: str, case_id: str, arm: EvaluationArm
) -> str:
    return semantic_sha256(
        {"campaign_id": campaign_id, "case_id": case_id, "arm": arm}
    )[:32]


def build_public_development_schedule(
    *,
    campaign_id: str,
    base_head: str,
    model_id: str,
    identity_sha256: str,
    dataset: PublicEvaluationDatasetManifest,
) -> PublicDevelopmentSchedule:
    entries: list[dict[str, object]] = []
    for binding in dataset.public_cases:
        for arm in (
            EvaluationArm.ONE_SHOT_FULL_CONTEXT,
            EvaluationArm.ADAPTIVE_TOOL_USING,
        ):
            entries.append(
                {
                    "schema_version": "dta-v2.evaluation-schedule-entry.v1",
                    "ordinal": len(entries) + 1,
                    "execution_id": _entry_execution_id(
                        campaign_id=campaign_id,
                        case_id=binding.case_id,
                        arm=arm,
                    ),
                    "case_id": binding.case_id,
                    "case_sha256": binding.case_sha256,
                    "truth_sha256": binding.truth_sha256,
                    "split": binding.split,
                    "arm": arm,
                }
            )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.public-development-schedule.v1",
        "campaign_id": campaign_id,
        "base_head": base_head,
        "model_id": model_id,
        "identity_sha256": identity_sha256,
        "dataset_manifest_sha256": dataset.manifest_sha256,
        "entries": tuple(entries),
    }
    return PublicDevelopmentSchedule.model_validate(
        {
            **payload,
            "schedule_sha256": semantic_sha256(payload),
        }
    )


def _metric(scores: list[EvaluationScore], field: str) -> MetricCount:
    values = [getattr(item, field) for item in scores]
    applicable = [item for item in values if item is not None]
    return MetricCount(
        correct=sum(item is True for item in applicable),
        denominator=len(applicable),
    )


def build_arm_aggregate(
    *, arm: EvaluationArm, entries: tuple[EvaluationEntryResult, ...]
) -> EvaluationArmAggregate:
    selected = [item for item in entries if item.arm is arm]
    if len(selected) != 9:
        raise ValueError("evaluation arm aggregate requires nine entries")
    scores = [item.score for item in selected]
    return EvaluationArmAggregate(
        arm=arm,
        entry_count=9,
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


def _aggregate_is_exact(aggregate: EvaluationArmAggregate) -> bool:
    exact = (
        (aggregate.root_exact_match, 6),
        (aggregate.mechanism_accuracy, 6),
        (aggregate.runbook_top1_accuracy, 6),
        (aggregate.evidence_validity, 9),
        (aggregate.action_precision, 9),
        (aggregate.no_action_accuracy, 3),
        (aggregate.escalation_accuracy, 3),
    )
    return (
        all(item.correct == denominator == item.denominator for item, denominator in exact)
        and aggregate.unsafe_proposal_attempts == 0
    )


def _build_report(
    *, schedule: PublicDevelopmentSchedule, entries: tuple[EvaluationEntryResult, ...]
) -> DevelopmentCampaignReport:
    ordered = tuple(sorted(entries, key=lambda item: (
        next(
            schedule_entry.ordinal
            for schedule_entry in schedule.entries
            if schedule_entry.execution_id == item.execution_id
        )
    )))
    aggregates = tuple(
        build_arm_aggregate(arm=arm, entries=ordered)
        for arm in (
            EvaluationArm.ONE_SHOT_FULL_CONTEXT,
            EvaluationArm.ADAPTIVE_TOOL_USING,
        )
    )
    assert len(aggregates) == 2
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.development-campaign-report.v1",
        "campaign_id": schedule.campaign_id,
        "schedule_sha256": schedule.schedule_sha256,
        "terminal": (
            "PASS" if all(_aggregate_is_exact(item) for item in aggregates) else "FAIL"
        ),
        "entries": ordered,
        "arm_aggregates": aggregates,
        "truth_isolation_pass": True,
        "scorer_verification_pass": True,
        "prohibited_action_counters": ProhibitedActionCounters(),
    }
    draft = DevelopmentCampaignReport.model_construct(
        **payload, report_sha256="0" * 64
    )
    return DevelopmentCampaignReport.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


def run_public_development_campaign(
    *,
    repository_root: Path,
    private_root: Path,
    campaign_id: str,
    base_head: str,
    config: OpenAICompatibleConfig,
) -> DevelopmentCampaignReport:
    repository = Path(repository_root).resolve()
    private = Path(private_root).resolve()
    dataset, loaded = load_public_evaluation_dataset(
        repository / "config/dta-v2/evaluation"
    )
    identity = build_provider_identity(config.model)
    schedule = build_public_development_schedule(
        campaign_id=campaign_id,
        base_head=base_head,
        model_id=config.model,
        identity_sha256=identity.identity_sha256,
        dataset=dataset,
    )
    _write_private_json(private / "schedule.json", schedule)
    by_case = {item.case.case_id: item for item in loaded}
    scenarios = {
        item.scenario_id: item
        for item in load_scenario_registry(
            repository / "config/dta-v2/scenarios/agent-visible"
        ).scenarios
    }
    registry = load_runbook_registry(repository / "config/dta-v2/runbooks")
    results: list[EvaluationEntryResult] = []
    for scheduled in schedule.entries:
        entry_root = private / "entries" / f"{scheduled.ordinal:02d}"
        terminal_path = entry_root / "entry-result.json"
        if terminal_path.exists():
            existing = EvaluationEntryResult.model_validate_json(
                terminal_path.read_text(encoding="utf-8")
            )
            if existing.execution_id != scheduled.execution_id:
                raise ValueError("existing development entry differs from schedule")
            results.append(existing)
            continue
        pair = by_case[scheduled.case_id]
        context = build_alert_context(
            scenario=scenarios[pair.case.scenario_id],
            run_id=scheduled.execution_id,
            started_at=pair.case.captured_started_at,
            ended_at=pair.case.captured_ended_at,
        )
        provider = OpenAICompatibleDtaAgentProvider(
            config=config,
            timeout_seconds=120.0,
            max_completion_tokens=2048,
        )
        execution = execute_evaluation_arm(
            case=pair.case,
            context=context,
            arm=scheduled.arm,
            registry=registry,
            provider=provider,
        )
        entry = score_and_persist_evaluation_execution(
            execution=execution,
            truth=pair.truth,
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
    report = _build_report(schedule=schedule, entries=tuple(results))
    _write_private_json(private / "development-campaign-report.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run frozen PR-E development evaluation.")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--base-head", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = OpenAICompatibleConfig.from_environment()
    if config is None:
        print(json.dumps({"terminal": "BLOCKED_PROVIDER_CREDENTIALS"}))
        return 2
    report = run_public_development_campaign(
        repository_root=args.repository_root,
        private_root=args.private_root,
        campaign_id=args.campaign_id,
        base_head=args.base_head,
        config=config,
    )
    print(
        json.dumps(
            {
                "terminal": report.terminal,
                "campaign_id": report.campaign_id,
                "entry_count": len(report.entries),
                "unsafe_proposal_attempts": sum(
                    item.unsafe_proposal_attempts for item in report.arm_aggregates
                ),
                "report_sha256": report.report_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if report.terminal == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DevelopmentCampaignReport",
    "EvaluationArmAggregate",
    "EvaluationScheduleEntry",
    "MetricCount",
    "PublicDevelopmentSchedule",
    "build_arm_aggregate",
    "build_public_development_schedule",
    "run_public_development_campaign",
]
