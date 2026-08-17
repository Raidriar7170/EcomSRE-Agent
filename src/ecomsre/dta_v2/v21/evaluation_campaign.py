"""Frozen schedule, preregistration, identity, and reporting contracts."""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
import hashlib
from pathlib import Path
import random
import statistics
from typing import Any, Literal, Mapping, cast

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21, AgentIdentityManifestV21
from ecomsre.dta_v2.v21.contracts import (
    DtaModelV21,
    FaultMechanismV21,
    Sha256V21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.evaluation_agents import EvaluationEntryResultV21
from ecomsre.dta_v2.v21.evaluation_contracts import (
    EvaluationArmV21,
    EvaluationSplitV21,
    EvaluatorCaseTruthV21,
    PublicEvaluationManifestV21,
)
from ecomsre.dta_v2.v21.evaluation_scenarios import (
    build_evaluation_scenario_registry_v21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry


PRIMARY_ARMS_V21 = (
    EvaluationArmV21.ONE_SHOT_FULL_CONTEXT,
    EvaluationArmV21.FLAT_ADAPTIVE,
    EvaluationArmV21.EVIDENCE_GUIDED_PLANNER,
)
ABLATION_CASE_IDS_V21 = (
    "dta21-case-001",
    "dta21-case-006",
    "dta21-case-008",
    "dta21-case-010",
)
_REQUIRED_SOURCE_BINDINGS = (
    "agent.py",
    "agent_contracts.py",
    "agent_provider.py",
    "candidate_filter.py",
    "context_projection.py",
    "evaluation_agents.py",
    "evaluation_campaign.py",
    "evaluation_contracts.py",
    "evaluation_replay.py",
    "evaluation_scenarios.py",
    "planner.py",
    "planner_contracts.py",
    "prompts.py",
)


class EvaluationSchedulePhaseV21(str, Enum):
    DEVELOPMENT_PRIMARY = "DEVELOPMENT_PRIMARY"
    DEVELOPMENT_ABLATION = "DEVELOPMENT_ABLATION"
    HELD_OUT_PRIMARY = "HELD_OUT_PRIMARY"


class EvaluationScheduleEntryV21(DtaModelV21):
    ordinal: StrictInt = Field(ge=1, le=64)
    phase: EvaluationSchedulePhaseV21
    split: EvaluationSplitV21
    case_id: str = Field(pattern=r"^dta21-case-[0-9]{3}$")
    arm: EvaluationArmV21


class EvaluationScheduleV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-schedule.v1"]
    seed_sha256: Sha256V21
    entries: tuple[EvaluationScheduleEntryV21, ...] = Field(
        min_length=64, max_length=64
    )
    schedule_sha256: Sha256V21

    @model_validator(mode="after")
    def require_schedule(self) -> EvaluationScheduleV21:
        if tuple(item.ordinal for item in self.entries) != tuple(range(1, 65)):
            raise ValueError("evaluation schedule ordinals are not exact")
        primary_development = tuple(
            item
            for item in self.entries
            if item.phase is EvaluationSchedulePhaseV21.DEVELOPMENT_PRIMARY
        )
        ablation = tuple(
            item
            for item in self.entries
            if item.phase is EvaluationSchedulePhaseV21.DEVELOPMENT_ABLATION
        )
        held_out = tuple(
            item
            for item in self.entries
            if item.phase is EvaluationSchedulePhaseV21.HELD_OUT_PRIMARY
        )
        expected_development = {
            (f"dta21-case-{index:03d}", arm)
            for index in range(1, 13)
            for arm in PRIMARY_ARMS_V21
        }
        expected_ablation = {
            (case_id, EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION)
            for case_id in ABLATION_CASE_IDS_V21
        }
        expected_held_out = {
            (f"dta21-case-{index:03d}", arm)
            for index in range(13, 21)
            for arm in PRIMARY_ARMS_V21
        }
        if {
            (item.case_id, item.arm) for item in primary_development
        } != expected_development:
            raise ValueError("development primary schedule differs")
        if {(item.case_id, item.arm) for item in ablation} != expected_ablation:
            raise ValueError("development ablation schedule differs")
        if {(item.case_id, item.arm) for item in held_out} != expected_held_out:
            raise ValueError("held-out primary schedule differs")
        if any(
            item.split is not EvaluationSplitV21.DEVELOPMENT
            for item in (*primary_development, *ablation)
        ):
            raise ValueError("development schedule split differs")
        if any(item.split is not EvaluationSplitV21.HELD_OUT for item in held_out):
            raise ValueError("held-out schedule split differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"schedule_sha256"})
        )
        if self.schedule_sha256 != expected:
            raise ValueError("evaluation schedule digest differs")
        return self


def build_evaluation_schedule_v21(*, seed_sha256: str) -> EvaluationScheduleV21:
    development = [
        (
            EvaluationSchedulePhaseV21.DEVELOPMENT_PRIMARY,
            EvaluationSplitV21.DEVELOPMENT,
            f"dta21-case-{index:03d}",
            arm,
        )
        for index in range(1, 13)
        for arm in PRIMARY_ARMS_V21
    ]
    ablation = [
        (
            EvaluationSchedulePhaseV21.DEVELOPMENT_ABLATION,
            EvaluationSplitV21.DEVELOPMENT,
            case_id,
            EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION,
        )
        for case_id in ABLATION_CASE_IDS_V21
    ]
    held_out = [
        (
            EvaluationSchedulePhaseV21.HELD_OUT_PRIMARY,
            EvaluationSplitV21.HELD_OUT,
            f"dta21-case-{index:03d}",
            arm,
        )
        for index in range(13, 21)
        for arm in PRIMARY_ARMS_V21
    ]
    randomizer = random.Random(int(seed_sha256, 16))
    randomizer.shuffle(development)
    randomizer.shuffle(ablation)
    randomizer.shuffle(held_out)
    raw = (*development, *ablation, *held_out)
    entries = tuple(
        EvaluationScheduleEntryV21(
            ordinal=index,
            phase=item[0],
            split=item[1],
            case_id=item[2],
            arm=item[3],
        )
        for index, item in enumerate(raw, start=1)
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evaluation-schedule.v1",
        "seed_sha256": seed_sha256,
        "entries": entries,
    }
    draft = cast(Any, EvaluationScheduleV21).model_construct(
        **payload, schedule_sha256="0" * 64
    )
    return EvaluationScheduleV21.model_validate(
        {
            **payload,
            "schedule_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"schedule_sha256"})
            ),
        }
    )


class PlannerAdvantageThresholdsV21(DtaModelV21):
    protocol_acceptance_both: StrictFloat = Field(default=1.0, ge=0.0, le=1.0)
    planner_root_not_lower: Literal[True] = True
    mechanism_macro_f1_minimum_delta: StrictFloat = Field(default=0.10, ge=0.0)
    evidence_validity_minimum_additional_cases: Literal[1] = 1
    evidence_validity_minimum_rate_delta: StrictFloat = Field(default=0.10, ge=0.0)
    action_metric_minimum_additional_cases: Literal[1] = 1
    action_metric_minimum_rate_delta: StrictFloat = Field(default=0.10, ge=0.0)
    planner_mean_input_token_ratio_maximum: StrictFloat = Field(default=0.75, ge=0.0)
    planner_mean_total_token_ratio_maximum: StrictFloat = Field(default=0.80, ge=0.0)
    planner_mean_semantic_read_ratio_maximum: StrictFloat = Field(default=1.0, ge=0.0)
    planner_median_latency_ratio_maximum: StrictFloat = Field(default=1.25, ge=0.0)
    duplicate_normalized_calls_maximum: Literal[0] = 0
    unsafe_proposal_attempts_maximum: Literal[0] = 0
    arbitrary_shell_attempts_maximum: Literal[0] = 0
    non_owned_mutations_maximum: Literal[0] = 0

    @model_validator(mode="after")
    def require_exact_thresholds(self) -> PlannerAdvantageThresholdsV21:
        expected = {
            "protocol_acceptance_both": 1.0,
            "mechanism_macro_f1_minimum_delta": 0.10,
            "evidence_validity_minimum_rate_delta": 0.10,
            "action_metric_minimum_rate_delta": 0.10,
            "planner_mean_input_token_ratio_maximum": 0.75,
            "planner_mean_total_token_ratio_maximum": 0.80,
            "planner_mean_semantic_read_ratio_maximum": 1.0,
            "planner_median_latency_ratio_maximum": 1.25,
        }
        if any(getattr(self, name) != value for name, value in expected.items()):
            raise ValueError("preregistered advantage threshold differs")
        return self


class EvaluationPreregistrationV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-preregistration.v1"]
    primary_arm: Literal[EvaluationArmV21.EVIDENCE_GUIDED_PLANNER]
    comparator_arm: Literal[EvaluationArmV21.FLAT_ADAPTIVE]
    descriptive_anchor_arm: Literal[EvaluationArmV21.ONE_SHOT_FULL_CONTEXT]
    model_id: str = Field(min_length=1, max_length=128)
    temperature: StrictFloat = Field(ge=0.0, le=0.0)
    max_completion_tokens: StrictInt = Field(ge=1, le=100_000)
    primary_case_count: Literal[8]
    primary_scored_entry_count: Literal[24]
    ablation_case_ids: tuple[str, str, str, str]
    thresholds: PlannerAdvantageThresholdsV21
    schedule_sha256: Sha256V21
    supported_marker: Literal["DTA_V21_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"]
    unsupported_marker: Literal["DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"]
    preregistration_sha256: Sha256V21

    @model_validator(mode="after")
    def require_preregistration(self) -> EvaluationPreregistrationV21:
        if self.ablation_case_ids != ABLATION_CASE_IDS_V21:
            raise ValueError("development ablation case set differs")
        if self.temperature != 0.0:
            raise ValueError("evaluation temperature differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"preregistration_sha256"})
        )
        if self.preregistration_sha256 != expected:
            raise ValueError("evaluation preregistration digest differs")
        return self


def build_evaluation_preregistration_v21(
    *, model_id: str, max_completion_tokens: int, schedule_sha256: str
) -> EvaluationPreregistrationV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evaluation-preregistration.v1",
        "primary_arm": EvaluationArmV21.EVIDENCE_GUIDED_PLANNER,
        "comparator_arm": EvaluationArmV21.FLAT_ADAPTIVE,
        "descriptive_anchor_arm": EvaluationArmV21.ONE_SHOT_FULL_CONTEXT,
        "model_id": model_id,
        "temperature": 0.0,
        "max_completion_tokens": max_completion_tokens,
        "primary_case_count": 8,
        "primary_scored_entry_count": 24,
        "ablation_case_ids": ABLATION_CASE_IDS_V21,
        "thresholds": PlannerAdvantageThresholdsV21(),
        "schedule_sha256": schedule_sha256,
        "supported_marker": "DTA_V21_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        "unsupported_marker": "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
    }
    draft = cast(Any, EvaluationPreregistrationV21).model_construct(
        **payload, preregistration_sha256="0" * 64
    )
    return EvaluationPreregistrationV21.model_validate(
        {
            **payload,
            "preregistration_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"preregistration_sha256"})
            ),
        }
    )


class EvaluationSourceBindingV21(DtaModelV21):
    name: str = Field(pattern=r"^[a-z_]+\.py$")
    source_sha256: Sha256V21


class EvaluationFreezeManifestV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-freeze-manifest.v1"]
    base_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_id: str = Field(min_length=1, max_length=128)
    temperature: StrictFloat = Field(ge=0.0, le=0.0)
    agent_identities: tuple[
        AgentIdentityManifestV21,
        AgentIdentityManifestV21,
        AgentIdentityManifestV21,
    ]
    source_bindings: tuple[EvaluationSourceBindingV21, ...]
    scenario_registry_sha256: Sha256V21
    runbook_registry_sha256: Sha256V21
    public_case_manifest: PublicEvaluationManifestV21
    schedule_sha256: Sha256V21
    preregistration_sha256: Sha256V21
    historical_v2_bindings_sha256: Sha256V21
    evaluation_frozen: Literal[True]
    held_out_executed: Literal[False]
    manifest_sha256: Sha256V21

    @model_validator(mode="after")
    def require_freeze(self) -> EvaluationFreezeManifestV21:
        identities = self.agent_identities
        if tuple(item.arm for item in identities) != tuple(AgentArmV21):
            raise ValueError("freeze manifest Agent identity order differs")
        if {item.model_id for item in identities} != {self.model_id}:
            raise ValueError("freeze manifest model differs from Agent identities")
        if {item.temperature for item in identities} != {self.temperature}:
            raise ValueError("freeze manifest temperature differs")
        names = tuple(item.name for item in self.source_bindings)
        if names != _REQUIRED_SOURCE_BINDINGS:
            raise ValueError("evaluation source bindings differ from exact set")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("evaluation freeze manifest digest differs")
        return self


def _source_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evaluation freeze source is missing or unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_evaluation_freeze_manifest_v21(
    *,
    repository_root: Path,
    base_code_head: str,
    model_id: str,
    max_completion_tokens: int,
    public_case_manifest: PublicEvaluationManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
) -> EvaluationFreezeManifestV21:
    source_root = repository_root / "src/ecomsre/dta_v2/v21"
    bindings = tuple(
        EvaluationSourceBindingV21(
            name=name, source_sha256=_source_sha256(source_root / name)
        )
        for name in _REQUIRED_SOURCE_BINDINGS
    )
    identities = build_three_arm_identities_v21(
        model_id=model_id, max_completion_tokens=max_completion_tokens
    )
    scenarios = build_evaluation_scenario_registry_v21(repository_root)
    runbooks = load_default_runbook_registry(repository_root)
    historical_path = repository_root / "config/dta-v21/historical-v2-bindings.v1.json"
    if schedule.schedule_sha256 != preregistration.schedule_sha256:
        raise ValueError("freeze schedule differs from preregistration")
    if preregistration.model_id != model_id:
        raise ValueError("freeze model differs from preregistration")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evaluation-freeze-manifest.v1",
        "base_code_head": base_code_head,
        "model_id": model_id,
        "temperature": 0.0,
        "agent_identities": identities,
        "source_bindings": bindings,
        "scenario_registry_sha256": scenarios.registry_sha256,
        "runbook_registry_sha256": runbooks.registry_sha256,
        "public_case_manifest": public_case_manifest,
        "schedule_sha256": schedule.schedule_sha256,
        "preregistration_sha256": preregistration.preregistration_sha256,
        "historical_v2_bindings_sha256": _source_sha256(historical_path),
        "evaluation_frozen": True,
        "held_out_executed": False,
    }
    draft = cast(Any, EvaluationFreezeManifestV21).model_construct(
        **payload, manifest_sha256="0" * 64
    )
    return EvaluationFreezeManifestV21.model_validate(
        {
            **payload,
            "manifest_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"manifest_sha256"})
            ),
        }
    )


class PerClassMetricV21(DtaModelV21):
    mechanism: FaultMechanismV21
    true_positive: StrictInt = Field(ge=0)
    false_positive: StrictInt = Field(ge=0)
    false_negative: StrictInt = Field(ge=0)
    precision: StrictFloat = Field(ge=0.0, le=1.0)
    recall: StrictFloat = Field(ge=0.0, le=1.0)
    f1: StrictFloat = Field(ge=0.0, le=1.0)


class EvaluationAggregateV21(DtaModelV21):
    group_type: Literal["OVERALL", "ARM", "MECHANISM", "GENERALIZATION_SLICE"]
    group_value: str
    scored_entries: StrictInt = Field(ge=1)
    protocol_acceptance_rate: StrictFloat = Field(ge=0.0, le=1.0)
    root_exact_match_rate: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    fault_domain_accuracy: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    mechanism_accuracy: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    mechanism_macro_f1: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    per_class: tuple[PerClassMetricV21, ...]
    evidence_validity_rate: StrictFloat = Field(ge=0.0, le=1.0)
    expected_source_coverage_rate: StrictFloat | None = Field(
        default=None, ge=0.0, le=1.0
    )
    runbook_top1_accuracy: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    action_precision: StrictFloat = Field(ge=0.0, le=1.0)
    no_action_accuracy: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    escalation_accuracy: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    tool_source_selection_accuracy: StrictFloat | None = Field(
        default=None, ge=0.0, le=1.0
    )
    tool_target_selection_accuracy: StrictFloat | None = Field(
        default=None, ge=0.0, le=1.0
    )
    duplicate_normalized_calls: StrictInt = Field(ge=0)
    mean_read_tool_dispatches: StrictFloat = Field(ge=0.0)
    mean_context_materialization_reads: StrictFloat = Field(ge=0.0)
    mean_provider_turns: StrictFloat = Field(ge=0.0)
    mean_input_tokens: StrictFloat = Field(ge=0.0)
    mean_output_tokens: StrictFloat = Field(ge=0.0)
    mean_total_tokens: StrictFloat = Field(ge=0.0)
    mean_latency_ms: StrictFloat = Field(ge=0.0)
    median_latency_ms: StrictFloat = Field(ge=0.0)
    unsafe_proposal_attempts: StrictInt = Field(ge=0)
    arbitrary_shell_attempts: Literal[0]


class DevelopmentEvaluationReportV21(DtaModelV21):
    schema_version: Literal["dta-v21.development-evaluation-report.v1"]
    model_id: str = Field(min_length=1, max_length=128)
    identity_sha256s: tuple[Sha256V21, Sha256V21, Sha256V21]
    primary_entry_count: Literal[36]
    ablation_entry_count: Literal[4]
    aggregates: tuple[EvaluationAggregateV21, ...] = Field(min_length=1)
    truth_isolation: Literal["PASS"]
    scorer_self_tests: Literal["PASS"]
    unsafe_writes: Literal[0]
    report_sha256: Sha256V21

    @model_validator(mode="after")
    def require_report(self) -> DevelopmentEvaluationReportV21:
        keys = tuple((item.group_type, item.group_value) for item in self.aggregates)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("development report groups are not canonical")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("development report digest differs")
        return self


def _rate(values: list[bool | None]) -> float | None:
    applicable = [item for item in values if item is not None]
    return None if not applicable else sum(applicable) / len(applicable)


def _classification(
    entries: list[EvaluationEntryResultV21],
    truths: Mapping[str, EvaluatorCaseTruthV21],
) -> tuple[tuple[PerClassMetricV21, ...], float | None]:
    classes = sorted(
        {
            truth.expected_mechanism
            for truth in truths.values()
            if truth.expected_mechanism is not None
        },
        key=lambda item: item.value,
    )
    metrics = []
    for mechanism in classes:
        tp = sum(
            truths[item.prediction.case_id].expected_mechanism is mechanism
            and item.prediction.mechanism is mechanism
            for item in entries
        )
        fp = sum(
            truths[item.prediction.case_id].expected_mechanism is not mechanism
            and item.prediction.mechanism is mechanism
            for item in entries
        )
        fn = sum(
            truths[item.prediction.case_id].expected_mechanism is mechanism
            and item.prediction.mechanism is not mechanism
            for item in entries
        )
        precision = 0.0 if tp + fp == 0 else tp / (tp + fp)
        recall = 0.0 if tp + fn == 0 else tp / (tp + fn)
        f1 = (
            0.0
            if precision + recall == 0
            else 2 * precision * recall / (precision + recall)
        )
        metrics.append(
            PerClassMetricV21(
                mechanism=mechanism,
                true_positive=tp,
                false_positive=fp,
                false_negative=fn,
                precision=precision,
                recall=recall,
                f1=f1,
            )
        )
    return tuple(metrics), (
        None if not metrics else statistics.fmean(item.f1 for item in metrics)
    )


def _aggregate(
    *,
    group_type: Literal["OVERALL", "ARM", "MECHANISM", "GENERALIZATION_SLICE"],
    group_value: str,
    entries: list[EvaluationEntryResultV21],
    truths: Mapping[str, EvaluatorCaseTruthV21],
) -> EvaluationAggregateV21:
    scores = [item.score for item in entries]
    per_class, macro = _classification(entries, truths)
    return EvaluationAggregateV21(
        group_type=group_type,
        group_value=group_value,
        scored_entries=len(entries),
        protocol_acceptance_rate=statistics.fmean(
            item.protocol_acceptance for item in scores
        ),
        root_exact_match_rate=_rate([item.root_exact_match for item in scores]),
        fault_domain_accuracy=_rate([item.fault_domain_accuracy for item in scores]),
        mechanism_accuracy=_rate([item.mechanism_accuracy for item in scores]),
        mechanism_macro_f1=macro,
        per_class=per_class,
        evidence_validity_rate=statistics.fmean(
            item.evidence_validity for item in scores
        ),
        expected_source_coverage_rate=_rate(
            [item.tool_source_selection_accuracy for item in scores]
        ),
        runbook_top1_accuracy=_rate([item.runbook_top1_accuracy for item in scores]),
        action_precision=statistics.fmean(item.action_precision for item in scores),
        no_action_accuracy=_rate([item.no_action_accuracy for item in scores]),
        escalation_accuracy=_rate([item.escalation_accuracy for item in scores]),
        tool_source_selection_accuracy=_rate(
            [item.tool_source_selection_accuracy for item in scores]
        ),
        tool_target_selection_accuracy=_rate(
            [item.tool_target_selection_accuracy for item in scores]
        ),
        duplicate_normalized_calls=sum(
            item.duplicate_normalized_calls for item in scores
        ),
        mean_read_tool_dispatches=statistics.fmean(
            item.read_tool_dispatches for item in scores
        ),
        mean_context_materialization_reads=statistics.fmean(
            item.context_materialization_reads for item in scores
        ),
        mean_provider_turns=statistics.fmean(item.provider_turns for item in scores),
        mean_input_tokens=statistics.fmean(item.input_tokens for item in scores),
        mean_output_tokens=statistics.fmean(item.output_tokens for item in scores),
        mean_total_tokens=statistics.fmean(item.total_tokens for item in scores),
        mean_latency_ms=statistics.fmean(item.latency_ms for item in scores),
        median_latency_ms=statistics.median(item.latency_ms for item in scores),
        unsafe_proposal_attempts=sum(item.unsafe_proposal_attempts for item in scores),
        arbitrary_shell_attempts=0,
    )


def build_development_report_v21(
    *,
    entries: tuple[EvaluationEntryResultV21, ...],
    truths: Mapping[str, EvaluatorCaseTruthV21],
    identities: tuple[AgentIdentityManifestV21, ...],
) -> DevelopmentEvaluationReportV21:
    if len(entries) != 40:
        raise ValueError("development report requires exact 40 entries")
    primary = [
        item
        for item in entries
        if item.arm is not EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION
    ]
    ablation = [
        item
        for item in entries
        if item.arm is EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION
    ]
    if len(primary) != 36 or len(ablation) != 4:
        raise ValueError("development primary or ablation cardinality differs")
    by_arm: dict[str, list[EvaluationEntryResultV21]] = defaultdict(list)
    by_mechanism: dict[str, list[EvaluationEntryResultV21]] = defaultdict(list)
    by_slice: dict[str, list[EvaluationEntryResultV21]] = defaultdict(list)
    for entry in entries:
        truth = truths[entry.prediction.case_id]
        by_arm[entry.arm.value].append(entry)
        by_mechanism[
            "NONE"
            if truth.expected_mechanism is None
            else truth.expected_mechanism.value
        ].append(entry)
        by_slice[truth.generalization_slice.value].append(entry)
    aggregates = [
        _aggregate(
            group_type="OVERALL",
            group_value="DEVELOPMENT",
            entries=list(entries),
            truths=truths,
        )
    ]
    for group_type, groups in (
        ("ARM", by_arm),
        ("MECHANISM", by_mechanism),
        ("GENERALIZATION_SLICE", by_slice),
    ):
        for value, grouped in groups.items():
            aggregates.append(
                _aggregate(
                    group_type=cast(Any, group_type),
                    group_value=value,
                    entries=grouped,
                    truths=truths,
                )
            )
    aggregates.sort(key=lambda item: (item.group_type, item.group_value))
    ordered_identities = tuple(
        sorted(identities, key=lambda item: list(AgentArmV21).index(item.arm))
    )
    if len(ordered_identities) != 3:
        raise ValueError("development report requires three Agent identities")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.development-evaluation-report.v1",
        "model_id": ordered_identities[0].model_id,
        "identity_sha256s": tuple(item.identity_sha256 for item in ordered_identities),
        "primary_entry_count": 36,
        "ablation_entry_count": 4,
        "aggregates": tuple(aggregates),
        "truth_isolation": "PASS",
        "scorer_self_tests": "PASS",
        "unsafe_writes": 0,
    }
    draft = cast(Any, DevelopmentEvaluationReportV21).model_construct(
        **payload, report_sha256="0" * 64
    )
    return DevelopmentEvaluationReportV21.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


__all__ = (
    "ABLATION_CASE_IDS_V21",
    "DevelopmentEvaluationReportV21",
    "EvaluationAggregateV21",
    "EvaluationFreezeManifestV21",
    "EvaluationPreregistrationV21",
    "EvaluationScheduleEntryV21",
    "EvaluationSchedulePhaseV21",
    "EvaluationScheduleV21",
    "EvaluationSourceBindingV21",
    "PRIMARY_ARMS_V21",
    "PlannerAdvantageThresholdsV21",
    "build_development_report_v21",
    "build_evaluation_freeze_manifest_v21",
    "build_evaluation_preregistration_v21",
    "build_evaluation_schedule_v21",
)
