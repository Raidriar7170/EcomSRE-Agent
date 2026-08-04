"""Strict public contracts for the frozen Phase 5B evaluation protocol."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)


EvaluationVersion = Literal["phase5b.v1"]
VariantName = Literal[
    "SINGLE_AGENT_V2",
    "FIXED_SPECIALIST_V2",
    "DYNAMIC_MULTI_AGENT_V2",
]

_FROZEN_PUBLIC_ANCHORS = (
    ("ad-partial-failure-complete", ("simple_confirmed_incident", "safe_remediation_candidate")),
    ("ad-partial-failure-without-logs", ("missing_telemetry",)),
    ("ad-partial-failure-frontend-decoy", ("decoy_change",)),
    ("recommendation-cache-failure", ("cache_dependency",)),
    (
        "recommendation-feature-evidence-insufficient",
        ("required_need_more_evidence", "anomaly_requiring_no_write"),
    ),
    ("ranking-change-with-normal-search-sli", ("normal_sli_abstention",)),
)
_FROZEN_HIDDEN_SLOTS = (
    ("hidden-01", "cross_service_cascade", "RCA_CONFIRMED", "causal_chain_reconstruction", "NO_ACTION"),
    ("hidden-02", "conflicting_evidence", "NEED_MORE_EVIDENCE", "unresolved_contradiction", "NO_ACTION"),
    ("hidden-03", "delayed_stale_telemetry_negative", "ABSTAIN", "freshness_and_abstention", "NO_ACTION"),
    ("hidden-04", "multi_service_anomaly", "RCA_CONFIRMED", "root_disambiguation", "NO_ACTION"),
    ("hidden-05", "confounded_changes", "RCA_CONFIRMED", "causal_change_selection", "SAFE_REPLAY_REMEDIATION_CANDIDATE"),
    ("hidden-06", "partial_tool_failure", "RCA_CONFIRMED", "graceful_degradation", "NO_ACTION"),
)
_FROZEN_ALLOWED_TRANSFORMATIONS = (
    "run_id",
    "evidence_ref",
    "opaque_request_trace_ids",
    "timestamp_offset",
    "evidence_declaration_order",
    "same_layer_dag_declaration_order",
    "decoy_opaque_id",
    "irrelevant_healthy_telemetry_order",
    "safe_lexical_variant_index",
)
_FROZEN_FORBIDDEN_TRANSFORMATIONS = (
    "ground_truth_decision",
    "root_service",
    "fault_mechanism",
    "critical_evidence_availability",
    "contradiction_class",
    "write_no_write_disposition",
    "budget",
    "model",
    "provider",
    "tool_schema",
)


class Phase5BModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PublicAnchor(Phase5BModel):
    template_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    visibility: Literal["PUBLIC_ANCHOR"]
    coverage: tuple[str, ...] = Field(min_length=1)


class HiddenCoverageSlot(Phase5BModel):
    template_id: str = Field(pattern=r"^hidden-0[1-6]$")
    visibility: Literal["HIDDEN_SLOT"]
    coverage: str = Field(min_length=1, max_length=128)
    expected_decision_family: Literal[
        "RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"
    ]
    challenge: str = Field(min_length=1, max_length=256)
    write_disposition: Literal["NO_ACTION", "SAFE_REPLAY_REMEDIATION_CANDIDATE"]
    actual_content_created: Literal[False]
    actual_ground_truth_created: Literal[False]


class SuiteRegistry(Phase5BModel):
    schema_version: Literal["phase5b.suite-registry.v1"]
    evaluation_version: EvaluationVersion
    template_count: Literal[12]
    public_anchor_count: Literal[6]
    hidden_template_count: Literal[6]
    hidden_share: StrictFloat
    public_anchors: tuple[PublicAnchor, ...]
    hidden_slots: tuple[HiddenCoverageSlot, ...]

    @model_validator(mode="after")
    def require_exact_suite(self) -> SuiteRegistry:
        if self.hidden_share != 0.5:
            raise ValueError("hidden share must be exactly 50 percent")
        if len(self.public_anchors) != self.public_anchor_count:
            raise ValueError("public anchor count mismatch")
        if len(self.hidden_slots) != self.hidden_template_count:
            raise ValueError("hidden slot count mismatch")
        identifiers = tuple(
            item.template_id for item in self.public_anchors + self.hidden_slots
        )
        if len(identifiers) != self.template_count or len(set(identifiers)) != 12:
            raise ValueError("suite template identities must be unique and complete")
        public = tuple((item.template_id, item.coverage) for item in self.public_anchors)
        if public != _FROZEN_PUBLIC_ANCHORS:
            raise ValueError("suite public anchors differ from the frozen public anchors")
        hidden = tuple(
            (
                item.template_id,
                item.coverage,
                item.expected_decision_family,
                item.challenge,
                item.write_disposition,
            )
            for item in self.hidden_slots
        )
        if hidden != _FROZEN_HIDDEN_SLOTS:
            raise ValueError("suite hidden slots differ from the frozen hidden coverage slots")
        return self


class SeedPolicy(Phase5BModel):
    schema_version: Literal["phase5b.seed-policy.v1"]
    evaluation_version: EvaluationVersion
    seed_count_per_template: Literal[5]
    seed_ids: tuple[str, ...]
    derivation: Literal[
        "sha256(evaluation_version+NUL+template_id+NUL+seed_id)"
    ]
    temperature: Literal[0]
    allowed_transformations: tuple[str, ...]
    forbidden_transformations: tuple[str, ...]

    @model_validator(mode="after")
    def require_exact_seed_policy(self) -> SeedPolicy:
        expected = tuple(f"seed-{index:02d}" for index in range(5))
        if self.seed_ids != expected:
            raise ValueError("seed identifiers are not the frozen five-seed set")
        if len(set(self.allowed_transformations)) != len(
            self.allowed_transformations
        ):
            raise ValueError("allowed seed transformations contain duplicates")
        if len(set(self.forbidden_transformations)) != len(
            self.forbidden_transformations
        ):
            raise ValueError("forbidden seed transformations contain duplicates")
        if set(self.allowed_transformations).intersection(
            self.forbidden_transformations
        ):
            raise ValueError("seed transformations cannot be both allowed and forbidden")
        if self.allowed_transformations != _FROZEN_ALLOWED_TRANSFORMATIONS:
            raise ValueError("seed policy differs from the frozen allowed transformations")
        if self.forbidden_transformations != _FROZEN_FORBIDDEN_TRANSFORMATIONS:
            raise ValueError("seed policy differs from the frozen forbidden transformations")
        return self


class ScheduledRun(Phase5BModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    pairing_unit_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    template_id: str = Field(min_length=1, max_length=128)
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    seed_material_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant_order_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant: VariantName
    call_position: StrictInt = Field(ge=1, le=3)


class ExecutionSchedule(Phase5BModel):
    schema_version: Literal["phase5b.execution-schedule.v1"]
    evaluation_version: EvaluationVersion
    variant_order_method: Literal["ranked_hash_round_robin_six_permutations"]
    pairing_unit_count: Literal[60]
    run_count: Literal[180]
    provider_pacing_seconds: Literal[2]
    hidden_retry: Literal[False]
    scripted_fallback: Literal[False]
    runs: tuple[ScheduledRun, ...]
    call_position_balance: dict[str, tuple[StrictInt, StrictInt, StrictInt]]

    @model_validator(mode="after")
    def require_complete_paired_schedule(self) -> ExecutionSchedule:
        if len(self.runs) != self.run_count:
            raise ValueError("schedule run count mismatch")
        if len({item.run_id for item in self.runs}) != self.run_count:
            raise ValueError("schedule run identifiers must be unique")
        groups: dict[tuple[str, str], list[ScheduledRun]] = {}
        for item in self.runs:
            groups.setdefault((item.template_id, item.seed_id), []).append(item)
        if len(groups) != self.pairing_unit_count:
            raise ValueError("schedule pairing unit count mismatch")
        expected_variants = {
            "SINGLE_AGENT_V2",
            "FIXED_SPECIALIST_V2",
            "DYNAMIC_MULTI_AGENT_V2",
        }
        for items in groups.values():
            if {item.variant for item in items} != expected_variants:
                raise ValueError("each pairing unit must contain all architecture arms")
            if {item.call_position for item in items} != {1, 2, 3}:
                raise ValueError("each pairing unit must fill all call positions")
            if len({item.seed_material_sha256 for item in items}) != 1:
                raise ValueError("paired architecture arms must share seed material")
        if set(self.call_position_balance) != expected_variants:
            raise ValueError("call-position balance must cover every architecture arm")
        if any(tuple(counts) != (20, 20, 20) for counts in self.call_position_balance.values()):
            raise ValueError("call-position balance must be exact for the frozen schedule")
        return self


class HiddenSeedManifest(Phase5BModel):
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    agent_visible_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ground_truth_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HiddenTemplateManifest(Phase5BModel):
    template_id: str = Field(pattern=r"^hidden-0[1-6]$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seeds: tuple[HiddenSeedManifest, ...]

    @model_validator(mode="after")
    def require_five_unique_seeds(self) -> HiddenTemplateManifest:
        expected = tuple(f"seed-{index:02d}" for index in range(5))
        if tuple(item.seed_id for item in self.seeds) != expected:
            raise ValueError("hidden template must contain the frozen five seeds")
        return self


class HiddenPackManifest(Phase5BModel):
    schema_version: Literal["phase5b.hidden-pack-manifest.v1"]
    evaluation_version: EvaluationVersion
    pack_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    template_count: Literal[6]
    seed_count_per_template: Literal[5]
    agent_visible_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ground_truth_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    sealed: Literal[True]
    unblinded: Literal[False]
    templates: tuple[HiddenTemplateManifest, ...]

    @property
    def template_ids(self) -> tuple[str, ...]:
        return tuple(item.template_id for item in self.templates)

    @model_validator(mode="after")
    def require_six_opaque_templates(self) -> HiddenPackManifest:
        expected = tuple(f"hidden-{index:02d}" for index in range(1, 7))
        if self.template_ids != expected:
            raise ValueError("hidden pack must contain the six opaque slots")
        return self


class FrozenExecutionRecord(Phase5BModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    terminal_status: Literal[
        "COMPLETED",
        "WORKFLOW_FAILURE",
        "PROVIDER_TRANSPORT_FAILURE",
        "PROVIDER_PROTOCOL_FAILURE",
        "SEMANTIC_FAILURE",
        "INVALID_SCHEMA",
        "UNRESOLVED_EVIDENCE_REFERENCE",
        "BUDGET_FAILURE",
        "EMPTY_FINAL_ANSWER",
    ]
    observed_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FrozenExecutionReport(Phase5BModel):
    schema_version: Literal["phase5b.execution-report.v1"]
    evaluation_version: EvaluationVersion
    execution_schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_count: Literal[180]
    all_terminal: Literal[True]
    records: tuple[FrozenExecutionRecord, ...]

    @model_validator(mode="after")
    def require_exact_terminal_record_set(self) -> FrozenExecutionReport:
        if len(self.records) != self.run_count:
            raise ValueError("execution report must contain exactly 180 terminal records")
        if len({item.run_id for item in self.records}) != self.run_count:
            raise ValueError("execution report run identifiers must be unique")
        return self


class FrozenEvaluationManifest(Phase5BModel):
    schema_version: Literal["phase5b.freeze-manifest.v1"]
    evaluation_version: EvaluationVersion
    base_main_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    provider: Literal["openai-compatible"]
    model_snapshot: Literal["gpt-5.4-mini-2026-03-17"]
    temperature: Literal[0]
    max_model_calls: Literal[8]
    max_tool_calls: Literal[8]
    max_tokens: Literal[32000]
    max_completion_tokens: Literal[2048]
    provider_pacing_seconds: Literal[2]
    hidden_retry: Literal[False]
    scripted_fallback: Literal[False]
    frozen_files: dict[str, str]

    @model_validator(mode="after")
    def require_sha256_file_bindings(self) -> FrozenEvaluationManifest:
        if not self.frozen_files:
            raise ValueError("freeze manifest must bind files")
        if any(
            not path or len(digest) != 64 or set(digest) - set("0123456789abcdef")
            for path, digest in self.frozen_files.items()
        ):
            raise ValueError("freeze manifest file binding is invalid")
        if tuple(self.frozen_files) != tuple(sorted(self.frozen_files)):
            raise ValueError("freeze manifest paths must be sorted")
        for path in self.frozen_files:
            parsed = PurePosixPath(path)
            if (
                parsed.is_absolute()
                or "\\" in path
                or not path
                or path != parsed.as_posix()
                or any(part in {"", ".", ".."} for part in parsed.parts)
            ):
                raise ValueError("freeze manifest path is unsafe or non-normal")
        return self


class UnblindingRecord(Phase5BModel):
    schema_version: Literal["phase5b.unblinding-record.v1"]
    evaluation_version: EvaluationVersion
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    freeze_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hidden_pack_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_visible_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ground_truth_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_runs: Literal[180]
    from_state: Literal["EXECUTION_COMPLETE"]
    to_state: Literal["UNBLINDED"]
    irreversible: Literal[True]
