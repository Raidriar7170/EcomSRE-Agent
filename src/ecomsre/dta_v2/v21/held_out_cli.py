"""One-time held-out execution, post-seal unblinding, and bounded reporting."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v21.agent import AgentProviderV21, DtaAgentRunResultV21
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21, build_alert_context_v21
from ecomsre.dta_v2.v21.agent_provider import OpenAICompatibleDtaAgentProviderV21
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.evaluation_agents import (
    EvaluationEntryResultV21,
    build_evaluation_prediction_v21,
    execute_evaluation_arm_v21,
)
from ecomsre.dta_v2.v21.evaluation_campaign import (
    ABLATION_CASE_IDS_V21,
    DevelopmentEvaluationReportV21,
    EvaluationAggregateV21,
    EvaluationFreezeManifestV21,
    EvaluationPreregistrationV21,
    EvaluationSchedulePhaseV21,
    EvaluationScheduleV21,
    HeldOutEvaluationReportV21,
    PlannerAdvantageThresholdsV21,
    _aggregate,
    build_held_out_report_v21,
)
from ecomsre.dta_v2.v21.evaluation_cli import (
    DevelopmentAttemptManifestV21,
    DevelopmentAttemptReceiptV21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationArmV21,
    EvaluationPredictionV21,
    EvaluationSplitV21,
    EvaluatorCaseTruthV21,
    PublicCaseBindingV21,
    build_evaluation_score_v21,
)
from ecomsre.dta_v2.v21.evaluation_dataset import (
    write_public_model_create_once_v21,
)
from ecomsre.dta_v2.v21.evaluation_scenarios import (
    build_evaluation_scenario_registry_v21,
)
from ecomsre.dta_v2.v21.evaluation_seal import (
    HeldOutPackSealV21,
    verify_held_out_pack_seal_v21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.environment.command_runner import AuditedSubprocessRunner
from ecomsre_live_sandbox.contracts import (
    verify_private_tree_permissions,
    write_private_json,
)


ProviderFactoryV21 = Callable[[AgentArmV21, OpenAICompatibleConfig], AgentProviderV21]
PROTOCOL_VERSION_V21 = "dta-v21-p0-master-v1"


class _GitCommandResult(Protocol):
    exit_code: int
    stdout: str


class _GitCommandRunner(Protocol):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> _GitCommandResult: ...


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC")


class HeldOutExecutionManifestV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-execution-manifest.v1"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: datetime
    execution_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    model_id: str = Field(min_length=1, max_length=128)
    identity_sha256s: tuple[Sha256V21, Sha256V21, Sha256V21]
    authoritative_claim_sha256: Sha256V21
    freeze_manifest_sha256: Sha256V21
    held_out_pack_seal_sha256: Sha256V21
    public_case_manifest_sha256: Sha256V21
    schedule_sha256: Sha256V21
    preregistration_sha256: Sha256V21
    entry_count: Literal[24]
    manifest_sha256: Sha256V21

    @model_validator(mode="after")
    def require_manifest(self) -> HeldOutExecutionManifestV21:
        _require_utc(self.created_at, "held-out execution manifest time")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("held-out execution manifest digest differs")
        return self


class HeldOutExecutionClaimV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-execution-claim.v1"]
    protocol_version: Literal["dta-v21-p0-master-v1"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    claimed_at: datetime
    execution_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    held_out_pack_seal_sha256: Sha256V21
    freeze_manifest_sha256: Sha256V21
    schedule_sha256: Sha256V21
    preregistration_sha256: Sha256V21
    claim_sha256: Sha256V21

    @model_validator(mode="after")
    def require_claim(self) -> HeldOutExecutionClaimV21:
        _require_utc(self.claimed_at, "held-out execution claim time")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"claim_sha256"})
        )
        if self.claim_sha256 != expected:
            raise ValueError("held-out execution claim digest differs")
        return self


class HeldOutExecutionEntryClaimV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-execution-entry-claim.v1"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    entry_execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ordinal: StrictInt = Field(ge=41, le=64)
    case_id: str = Field(pattern=r"^dta21-case-0(?:1[3-9]|20)$")
    case_sha256: Sha256V21
    truth_sha256: Sha256V21
    arm: EvaluationArmV21
    claim_sha256: Sha256V21

    @model_validator(mode="after")
    def require_claim(self) -> HeldOutExecutionEntryClaimV21:
        if self.arm is EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION:
            raise ValueError("held-out execution claim carries ablation arm")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"claim_sha256"})
        )
        if self.claim_sha256 != expected:
            raise ValueError("held-out execution entry claim digest differs")
        return self


class HeldOutExecutionEntryReceiptV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-execution-entry-receipt.v1"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    entry_execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ordinal: StrictInt = Field(ge=41, le=64)
    case_id: str = Field(pattern=r"^dta21-case-0(?:1[3-9]|20)$")
    case_sha256: Sha256V21
    truth_sha256: Sha256V21
    arm: EvaluationArmV21
    model_id: str = Field(min_length=1, max_length=128)
    identity_sha256: Sha256V21
    entry_claim_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    prediction_sha256: Sha256V21
    receipt_sha256: Sha256V21

    @model_validator(mode="after")
    def require_receipt(self) -> HeldOutExecutionEntryReceiptV21:
        if self.arm is EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION:
            raise ValueError("held-out execution receipt carries ablation arm")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("held-out execution entry receipt digest differs")
        return self


class HeldOutExecutionSealV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-execution-seal.v1"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sealed_at: datetime
    execution_manifest_sha256: Sha256V21
    authoritative_claim_sha256: Sha256V21
    held_out_pack_seal_sha256: Sha256V21
    schedule_sha256: Sha256V21
    entry_count: Literal[24]
    entries: tuple[HeldOutExecutionEntryReceiptV21, ...] = Field(
        min_length=24, max_length=24
    )
    truth_isolation: Literal["PASS"]
    provider_phase_unblinded: Literal[False]
    execution_seal_sha256: Sha256V21

    @model_validator(mode="after")
    def require_seal(self) -> HeldOutExecutionSealV21:
        _require_utc(self.sealed_at, "held-out execution seal time")
        if tuple(item.ordinal for item in self.entries) != tuple(range(41, 65)):
            raise ValueError("held-out execution seal entry order differs")
        if len({item.entry_execution_id for item in self.entries}) != 24:
            raise ValueError("held-out execution seal contains duplicate entries")
        if {item.execution_id for item in self.entries} != {self.execution_id}:
            raise ValueError("held-out execution seal entry execution differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"execution_seal_sha256"})
        )
        if self.execution_seal_sha256 != expected:
            raise ValueError("held-out execution seal digest differs")
        return self


class HeldOutUnblindingReceiptV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-unblinding-receipt.v1"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    unblinded_at: datetime
    held_out_pack_seal_sha256: Sha256V21
    execution_seal_sha256: Sha256V21
    prediction_sha256s: tuple[Sha256V21, ...] = Field(min_length=24, max_length=24)
    unblinding_receipt_sha256: Sha256V21

    @model_validator(mode="after")
    def require_receipt(self) -> HeldOutUnblindingReceiptV21:
        _require_utc(self.unblinded_at, "held-out unblinding time")
        if len(set(self.prediction_sha256s)) != 24:
            raise ValueError("held-out unblinding prediction set contains duplicates")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"unblinding_receipt_sha256"})
        )
        if self.unblinding_receipt_sha256 != expected:
            raise ValueError("held-out unblinding receipt digest differs")
        return self


class HeldOutPublicEvaluationReportV21(DtaModelV21):
    schema_version: Literal["dta-v21.public-held-out-evaluation-report.v1"]
    terminal: Literal["DTA_V21_PR_E_HELD_OUT_COMPLETED"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    held_out_case_count: Literal[8]
    scored_entry_count: Literal[24]
    model_id: str = Field(min_length=1, max_length=128)
    identity_sha256s: tuple[Sha256V21, Sha256V21, Sha256V21]
    held_out_pack_seal_sha256: Sha256V21
    execution_seal_sha256: Sha256V21
    unblinding_receipt_sha256: Sha256V21
    primary_comparison: Literal["EVIDENCE_GUIDED_PLANNER_vs_FLAT_ADAPTIVE"]
    one_shot_role: Literal["DESCRIPTIVE_ANCHOR_ONLY"]
    preregistered_thresholds: PlannerAdvantageThresholdsV21
    evaluation: HeldOutEvaluationReportV21
    exact_claim: Literal[
        "DTA_V21_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
    ]
    limitations: tuple[str, ...] = Field(min_length=3)
    report_sha256: Sha256V21

    @model_validator(mode="after")
    def require_report(self) -> HeldOutPublicEvaluationReportV21:
        if (
            self.evaluation.model_id != self.model_id
            or self.evaluation.identity_sha256s != self.identity_sha256s
            or self.evaluation.claim_decision.marker != self.exact_claim
        ):
            raise ValueError("public held-out report differs from frozen evaluation")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("public held-out report digest differs")
        return self


class HeldOutEvaluationDispositionV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-evaluation-disposition.v1"]
    terminal: Literal["DTA_V21_PR_E_HELD_OUT_COMPLETED"]
    claim: Literal[
        "DTA_V21_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
    ]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    held_out_case_count: Literal[8]
    scored_entry_count: Literal[24]
    held_out_pack_seal_sha256: Sha256V21
    execution_seal_sha256: Sha256V21
    unblinding_receipt_sha256: Sha256V21
    report_sha256: Sha256V21
    truth_isolation: Literal["PASS"]
    scorer_verification: Literal["PASS"]
    no_agent_provider_rerun_during_analysis: Literal[True]
    disposition_sha256: Sha256V21

    @model_validator(mode="after")
    def require_disposition(self) -> HeldOutEvaluationDispositionV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"disposition_sha256"})
        )
        if self.disposition_sha256 != expected:
            raise ValueError("held-out evaluation disposition digest differs")
        return self


class DevelopmentAblationReportV21(DtaModelV21):
    schema_version: Literal["dta-v21.development-ablation-report.v1"]
    development_attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    development_report_sha256: Sha256V21
    matched_case_ids: tuple[str, str, str, str]
    compact_context: EvaluationAggregateV21
    no_compaction: EvaluationAggregateV21
    mean_input_token_ratio: float = Field(ge=0.0)
    mean_total_token_ratio: float = Field(ge=0.0)
    mean_semantic_read_ratio: float = Field(ge=0.0)
    compact_context_reduced_mean_input_tokens: bool
    limitations: tuple[str, ...] = Field(min_length=2)
    report_sha256: Sha256V21

    @model_validator(mode="after")
    def require_report(self) -> DevelopmentAblationReportV21:
        if self.matched_case_ids != ABLATION_CASE_IDS_V21:
            raise ValueError("development ablation case set differs")
        if (
            self.compact_context.group_type != "ARM"
            or self.compact_context.group_value
            != EvaluationArmV21.EVIDENCE_GUIDED_PLANNER.value
            or self.compact_context.scored_entries != 4
            or self.no_compaction.group_type != "ARM"
            or self.no_compaction.group_value
            != EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION.value
            or self.no_compaction.scored_entries != 4
        ):
            raise ValueError("development ablation aggregates differ")
        if (
            self.no_compaction.mean_input_tokens == 0.0
            or self.no_compaction.mean_total_tokens == 0.0
            or self.no_compaction.mean_read_tool_dispatches == 0.0
        ):
            raise ValueError("development ablation denominator is zero")
        ratios = (
            self.compact_context.mean_input_tokens
            / self.no_compaction.mean_input_tokens,
            self.compact_context.mean_total_tokens
            / self.no_compaction.mean_total_tokens,
            self.compact_context.mean_read_tool_dispatches
            / self.no_compaction.mean_read_tool_dispatches,
        )
        if (
            self.mean_input_token_ratio,
            self.mean_total_token_ratio,
            self.mean_semantic_read_ratio,
        ) != ratios or self.compact_context_reduced_mean_input_tokens is not (
            ratios[0] < 1.0
        ):
            raise ValueError("development ablation ratios differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("development ablation report digest differs")
        return self


def _with_digest(model_type: type[DtaModelV21], payload: dict[str, object], field: str):
    draft = cast(Any, model_type).model_construct(**payload, **{field: "0" * 64})
    return model_type.model_validate(
        {
            **payload,
            field: semantic_sha256(draft.model_dump(mode="json", exclude={field})),
        }
    )


def _read_regular(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("held-out input is missing or unsafe")
    return path.read_text(encoding="utf-8")


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("held-out input is missing or unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_exact_clean_head(
    execution_code_head: str, *, runner: _GitCommandRunner
) -> None:
    head = runner.run(("git", "rev-parse", "HEAD"), timeout_seconds=30.0)
    if head.exit_code != 0 or head.stdout.strip() != execution_code_head:
        raise ValueError("held-out execution code HEAD differs")
    status = runner.run(("git", "status", "--porcelain"), timeout_seconds=30.0)
    if status.exit_code != 0 or status.stdout:
        raise ValueError("held-out execution worktree is not clean")


def _verify_frozen_inputs(
    *,
    repository_root: Path,
    freeze_manifest: EvaluationFreezeManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
    held_out_pack_root: Path,
    held_out_pack_seal: HeldOutPackSealV21,
) -> None:
    if (
        freeze_manifest.schedule_sha256 != schedule.schedule_sha256
        or freeze_manifest.preregistration_sha256
        != preregistration.preregistration_sha256
        or freeze_manifest.public_case_manifest.manifest_sha256
        != held_out_pack_seal.public_case_manifest_sha256
        or held_out_pack_seal.freeze_manifest_sha256 != freeze_manifest.manifest_sha256
        or held_out_pack_seal.schedule_sha256 != schedule.schedule_sha256
        or held_out_pack_seal.preregistration_sha256
        != preregistration.preregistration_sha256
    ):
        raise ValueError("held-out frozen protocol bindings differ")
    source_root = repository_root / "src/ecomsre/dta_v2/v21"
    for binding in freeze_manifest.source_bindings:
        if _file_sha256(source_root / binding.name) != binding.source_sha256:
            raise ValueError(f"held-out frozen source changed: {binding.name}")
    scenarios = build_evaluation_scenario_registry_v21(repository_root)
    runbooks = load_default_runbook_registry(repository_root)
    if (
        scenarios.registry_sha256 != freeze_manifest.scenario_registry_sha256
        or runbooks.registry_sha256 != freeze_manifest.runbook_registry_sha256
    ):
        raise ValueError("held-out frozen registry changed")
    verify_held_out_pack_seal_v21(
        held_out_pack_root=held_out_pack_root,
        seal=held_out_pack_seal,
    )


def _default_provider_factory(
    arm: AgentArmV21, config: OpenAICompatibleConfig
) -> AgentProviderV21:
    return OpenAICompatibleDtaAgentProviderV21(
        arm=arm,
        config=config,
        timeout_seconds=90.0,
        max_completion_tokens=1600,
    )


def _agent_arm(arm: EvaluationArmV21) -> AgentArmV21:
    if arm is EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION:
        raise ValueError("held-out execution cannot use the ablation arm")
    return AgentArmV21(arm.value)


def _entry_name(ordinal: int, case_id: str, arm: EvaluationArmV21) -> str:
    return f"{ordinal:02d}-{case_id}-{arm.value.lower()}"


def _entry_execution_id(
    execution_id: str, ordinal: int, case_id: str, arm: EvaluationArmV21
) -> str:
    return semantic_sha256(
        {
            "execution_id": execution_id,
            "ordinal": ordinal,
            "case_id": case_id,
            "arm": arm.value,
        }
    )[:32]


def _default_authoritative_claim_path(
    held_out_pack_seal: HeldOutPackSealV21,
) -> Path:
    return (
        Path.home()
        / ".ecomsre"
        / "private"
        / PROTOCOL_VERSION_V21
        / "pr-e"
        / "one-time-claims"
        / f"{held_out_pack_seal.seal_sha256}.json"
    )


def _resolved_authoritative_claim_path(
    *,
    held_out_pack_seal: HeldOutPackSealV21,
    authoritative_claim_path: Path | None,
    provider_factory: ProviderFactoryV21 | None = None,
) -> Path:
    if authoritative_claim_path is not None:
        if provider_factory is _default_provider_factory:
            raise ValueError(
                "production held-out execution claim path cannot be overridden"
            )
        return authoritative_claim_path
    return _default_authoritative_claim_path(held_out_pack_seal)


def _build_execution_claim(
    *,
    execution_id: str,
    claimed_at: datetime,
    execution_code_head: str,
    freeze_manifest: EvaluationFreezeManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
    held_out_pack_seal: HeldOutPackSealV21,
) -> HeldOutExecutionClaimV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.held-out-execution-claim.v1",
        "protocol_version": PROTOCOL_VERSION_V21,
        "execution_id": execution_id,
        "claimed_at": claimed_at,
        "execution_code_head": execution_code_head,
        "held_out_pack_seal_sha256": held_out_pack_seal.seal_sha256,
        "freeze_manifest_sha256": freeze_manifest.manifest_sha256,
        "schedule_sha256": schedule.schedule_sha256,
        "preregistration_sha256": preregistration.preregistration_sha256,
    }
    return cast(
        HeldOutExecutionClaimV21,
        _with_digest(HeldOutExecutionClaimV21, payload, "claim_sha256"),
    )


def _load_authoritative_claim(
    *,
    authoritative_claim_path: Path,
    manifest: HeldOutExecutionManifestV21,
    seal: HeldOutExecutionSealV21,
    held_out_pack_seal: HeldOutPackSealV21,
    schedule: EvaluationScheduleV21,
) -> HeldOutExecutionClaimV21:
    verify_private_tree_permissions(authoritative_claim_path.parent)
    claim = HeldOutExecutionClaimV21.model_validate_json(
        _read_regular(authoritative_claim_path)
    )
    if (
        claim.execution_id != manifest.execution_id
        or claim.claimed_at != manifest.created_at
        or claim.execution_code_head != manifest.execution_code_head
        or claim.held_out_pack_seal_sha256 != held_out_pack_seal.seal_sha256
        or claim.freeze_manifest_sha256 != manifest.freeze_manifest_sha256
        or claim.schedule_sha256 != schedule.schedule_sha256
        or claim.preregistration_sha256 != manifest.preregistration_sha256
        or claim.claim_sha256 != manifest.authoritative_claim_sha256
        or claim.claim_sha256 != seal.authoritative_claim_sha256
    ):
        raise ValueError("held-out authoritative execution claim differs")
    return claim


def _build_entry_claim(
    *,
    execution_id: str,
    ordinal: int,
    case_id: str,
    case_sha256: str,
    truth_sha256: str,
    arm: EvaluationArmV21,
) -> HeldOutExecutionEntryClaimV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.held-out-execution-entry-claim.v1",
        "execution_id": execution_id,
        "entry_execution_id": _entry_execution_id(execution_id, ordinal, case_id, arm),
        "ordinal": ordinal,
        "case_id": case_id,
        "case_sha256": case_sha256,
        "truth_sha256": truth_sha256,
        "arm": arm,
    }
    return cast(
        HeldOutExecutionEntryClaimV21,
        _with_digest(HeldOutExecutionEntryClaimV21, payload, "claim_sha256"),
    )


def _require_exact_sealed_execution_tree(
    *, private_execution_root: Path, schedule: EvaluationScheduleV21
) -> None:
    verify_private_tree_permissions(private_execution_root)
    root_entries = {path.name for path in private_execution_root.iterdir()}
    if root_entries != {"execution-manifest.json", "execution-seal.json", "entries"}:
        raise ValueError("held-out sealed execution root tree differs")
    entries_root = private_execution_root / "entries"
    if entries_root.is_symlink() or not entries_root.is_dir():
        raise ValueError("held-out sealed execution entries root is unsafe")
    held_out_schedule = tuple(
        item
        for item in schedule.entries
        if item.phase is EvaluationSchedulePhaseV21.HELD_OUT_PRIMARY
    )
    expected_entry_names = {
        _entry_name(item.ordinal, item.case_id, item.arm) for item in held_out_schedule
    }
    if {path.name for path in entries_root.iterdir()} != expected_entry_names:
        raise ValueError("held-out sealed execution entry directory set differs")
    expected_files = {
        "entry-claim.json",
        "agent-result.json",
        "prediction.json",
        "entry-receipt.json",
    }
    for entry_name in expected_entry_names:
        entry_root = entries_root / entry_name
        if entry_root.is_symlink() or not entry_root.is_dir():
            raise ValueError("held-out sealed execution entry root is unsafe")
        if {path.name for path in entry_root.iterdir()} != expected_files:
            raise ValueError("held-out sealed execution entry tree differs")


def execute_held_out_evaluation_v21(
    *,
    repository_root: Path,
    provider_env_path: Path,
    held_out_pack_root: Path,
    private_execution_root: Path,
    execution_id: str,
    execution_code_head: str,
    freeze_manifest: EvaluationFreezeManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
    held_out_pack_seal: HeldOutPackSealV21,
    git_runner: _GitCommandRunner,
    provider_factory: ProviderFactoryV21 = _default_provider_factory,
    created_at: datetime | None = None,
    authoritative_claim_path: Path | None = None,
) -> HeldOutExecutionSealV21:
    """Execute 24 frozen arms without semantically loading evaluator truth."""

    manifest_path = private_execution_root / "execution-manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise FileExistsError("held-out execution was already claimed")
    resolved_claim_path = _resolved_authoritative_claim_path(
        held_out_pack_seal=held_out_pack_seal,
        authoritative_claim_path=authoritative_claim_path,
        provider_factory=provider_factory,
    )
    _require_exact_clean_head(execution_code_head, runner=git_runner)
    _verify_frozen_inputs(
        repository_root=repository_root,
        freeze_manifest=freeze_manifest,
        schedule=schedule,
        preregistration=preregistration,
        held_out_pack_root=held_out_pack_root,
        held_out_pack_seal=held_out_pack_seal,
    )
    values = load_private_provider_env(provider_env_path)
    config = OpenAICompatibleConfig.from_environment(values)
    if config is None or config.model != preregistration.model_id:
        raise ValueError("held-out Provider differs from the frozen model")
    identities = build_three_arm_identities_v21(
        model_id=config.model,
        max_completion_tokens=preregistration.max_completion_tokens,
    )
    if identities != freeze_manifest.agent_identities:
        raise ValueError("held-out Agent identities differ from the freeze")
    identity_by_arm = {item.arm: item for item in identities}
    held_out_schedule = tuple(
        item
        for item in schedule.entries
        if item.phase is EvaluationSchedulePhaseV21.HELD_OUT_PRIMARY
    )
    if len(held_out_schedule) != 24:
        raise ValueError("held-out schedule does not contain exact 24 entries")
    bindings = {
        item.case_id: item
        for item in freeze_manifest.public_case_manifest.held_out_cases
    }
    now = (created_at or datetime.now(timezone.utc)).replace(microsecond=0)
    claim = _build_execution_claim(
        execution_id=execution_id,
        claimed_at=now,
        execution_code_head=execution_code_head,
        freeze_manifest=freeze_manifest,
        schedule=schedule,
        preregistration=preregistration,
        held_out_pack_seal=held_out_pack_seal,
    )
    if resolved_claim_path.exists() or resolved_claim_path.is_symlink():
        raise FileExistsError("held-out execution was already claimed")
    write_private_json(resolved_claim_path, claim, create_once=True)
    manifest_payload: dict[str, object] = {
        "schema_version": "dta-v21.held-out-execution-manifest.v1",
        "execution_id": execution_id,
        "created_at": now,
        "execution_code_head": execution_code_head,
        "model_id": config.model,
        "identity_sha256s": tuple(item.identity_sha256 for item in identities),
        "authoritative_claim_sha256": claim.claim_sha256,
        "freeze_manifest_sha256": freeze_manifest.manifest_sha256,
        "held_out_pack_seal_sha256": held_out_pack_seal.seal_sha256,
        "public_case_manifest_sha256": (
            freeze_manifest.public_case_manifest.manifest_sha256
        ),
        "schedule_sha256": schedule.schedule_sha256,
        "preregistration_sha256": preregistration.preregistration_sha256,
        "entry_count": 24,
    }
    manifest = cast(
        HeldOutExecutionManifestV21,
        _with_digest(
            HeldOutExecutionManifestV21,
            manifest_payload,
            "manifest_sha256",
        ),
    )
    write_private_json(manifest_path, manifest, create_once=True)
    scenarios = build_evaluation_scenario_registry_v21(repository_root)
    runbooks = load_default_runbook_registry(repository_root)
    receipts: list[HeldOutExecutionEntryReceiptV21] = []
    for scheduled in held_out_schedule:
        entry_root = (
            private_execution_root
            / "entries"
            / _entry_name(scheduled.ordinal, scheduled.case_id, scheduled.arm)
        )
        claim_path = entry_root / "entry-claim.json"
        if claim_path.exists() or claim_path.is_symlink():
            raise RuntimeError("claimed held-out entry cannot be rerun")
        binding = bindings.get(scheduled.case_id)
        if binding is None:
            raise ValueError("held-out schedule case lacks a public binding")
        entry_claim = _build_entry_claim(
            execution_id=execution_id,
            ordinal=scheduled.ordinal,
            case_id=scheduled.case_id,
            case_sha256=binding.case_sha256,
            truth_sha256=binding.truth_sha256,
            arm=scheduled.arm,
        )
        entry_id = entry_claim.entry_execution_id
        write_private_json(claim_path, entry_claim, create_once=True)
        case = AgentVisibleReplayCaseV21.model_validate_json(
            _read_regular(
                held_out_pack_root / "cases" / scheduled.case_id / "agent-visible.json"
            )
        )
        if case.case_id != binding.case_id or case.case_sha256 != binding.case_sha256:
            raise ValueError("held-out Agent-visible case differs from public binding")
        context = build_alert_context_v21(
            scenario=scenarios.require(case.scenario_id),
            run_id=entry_id,
            started_at=case.captured_started_at,
            ended_at=case.captured_ended_at,
        )
        provider_arm = _agent_arm(scheduled.arm)
        try:
            provider = provider_factory(provider_arm, config)
            if provider.identity != identity_by_arm[provider_arm]:
                raise ValueError("held-out Provider identity differs from the freeze")
            execution = execute_evaluation_arm_v21(
                case=case,
                context=context,
                arm=scheduled.arm,
                registry=runbooks,
                provider=provider,
            )
            prediction = build_evaluation_prediction_v21(execution)
            write_private_json(
                entry_root / "agent-result.json",
                execution.agent_result,
                create_once=True,
            )
            write_private_json(
                entry_root / "prediction.json", prediction, create_once=True
            )
        except Exception as error:
            write_private_json(
                private_execution_root / "execution-failure.json",
                {
                    "schema_version": "dta-v21.held-out-execution-failure.v1",
                    "terminal": "BLOCKED_DTA_V21_HELD_OUT_PROTOCOL",
                    "execution_id": execution_id,
                    "ordinal": scheduled.ordinal,
                    "case_id": scheduled.case_id,
                    "arm": scheduled.arm.value,
                    "failure_type": type(error).__name__,
                },
                create_once=True,
            )
            raise
        receipt_payload: dict[str, object] = {
            "schema_version": "dta-v21.held-out-execution-entry-receipt.v1",
            "execution_id": execution_id,
            "entry_execution_id": entry_id,
            "ordinal": scheduled.ordinal,
            "case_id": scheduled.case_id,
            "case_sha256": binding.case_sha256,
            "truth_sha256": binding.truth_sha256,
            "arm": scheduled.arm,
            "model_id": execution.agent_result.identity.model_id,
            "identity_sha256": execution.agent_result.identity.identity_sha256,
            "entry_claim_sha256": entry_claim.claim_sha256,
            "agent_result_sha256": execution.agent_result.result_sha256,
            "prediction_sha256": semantic_sha256(prediction.model_dump(mode="json")),
        }
        receipt = cast(
            HeldOutExecutionEntryReceiptV21,
            _with_digest(
                HeldOutExecutionEntryReceiptV21,
                receipt_payload,
                "receipt_sha256",
            ),
        )
        write_private_json(entry_root / "entry-receipt.json", receipt, create_once=True)
        receipts.append(receipt)
    seal_payload: dict[str, object] = {
        "schema_version": "dta-v21.held-out-execution-seal.v1",
        "execution_id": execution_id,
        "sealed_at": (
            now
            if created_at is not None
            else datetime.now(timezone.utc).replace(microsecond=0)
        ),
        "execution_manifest_sha256": manifest.manifest_sha256,
        "authoritative_claim_sha256": claim.claim_sha256,
        "held_out_pack_seal_sha256": held_out_pack_seal.seal_sha256,
        "schedule_sha256": schedule.schedule_sha256,
        "entry_count": 24,
        "entries": tuple(receipts),
        "truth_isolation": "PASS",
        "provider_phase_unblinded": False,
    }
    seal = cast(
        HeldOutExecutionSealV21,
        _with_digest(
            HeldOutExecutionSealV21,
            seal_payload,
            "execution_seal_sha256",
        ),
    )
    write_private_json(
        private_execution_root / "execution-seal.json", seal, create_once=True
    )
    return verify_held_out_execution_seal_v21(
        private_execution_root=private_execution_root,
        held_out_pack_seal=held_out_pack_seal,
        schedule=schedule,
        authoritative_claim_path=resolved_claim_path,
    )


def _load_execution_entry(
    private_execution_root: Path,
    receipt: HeldOutExecutionEntryReceiptV21,
    expected_claim: HeldOutExecutionEntryClaimV21,
) -> tuple[DtaAgentRunResultV21, EvaluationPredictionV21]:
    root = (
        private_execution_root
        / "entries"
        / _entry_name(receipt.ordinal, receipt.case_id, receipt.arm)
    )
    agent_result = DtaAgentRunResultV21.model_validate_json(
        _read_regular(root / "agent-result.json")
    )
    prediction = EvaluationPredictionV21.model_validate_json(
        _read_regular(root / "prediction.json")
    )
    persisted = HeldOutExecutionEntryReceiptV21.model_validate_json(
        _read_regular(root / "entry-receipt.json")
    )
    persisted_claim = HeldOutExecutionEntryClaimV21.model_validate_json(
        _read_regular(root / "entry-claim.json")
    )
    if (
        persisted != receipt
        or persisted_claim != expected_claim
        or persisted_claim.claim_sha256 != receipt.entry_claim_sha256
        or agent_result.result_sha256 != receipt.agent_result_sha256
        or semantic_sha256(prediction.model_dump(mode="json"))
        != receipt.prediction_sha256
        or prediction.case_id != receipt.case_id
        or prediction.arm is not receipt.arm
        or agent_result.identity.identity_sha256 != receipt.identity_sha256
        or agent_result.identity.model_id != receipt.model_id
    ):
        raise ValueError("held-out execution entry differs from its receipt")
    return agent_result, prediction


def verify_held_out_execution_seal_v21(
    *,
    private_execution_root: Path,
    held_out_pack_seal: HeldOutPackSealV21,
    schedule: EvaluationScheduleV21,
    authoritative_claim_path: Path | None = None,
) -> HeldOutExecutionSealV21:
    _require_exact_sealed_execution_tree(
        private_execution_root=private_execution_root, schedule=schedule
    )
    seal = HeldOutExecutionSealV21.model_validate_json(
        _read_regular(private_execution_root / "execution-seal.json")
    )
    manifest = HeldOutExecutionManifestV21.model_validate_json(
        _read_regular(private_execution_root / "execution-manifest.json")
    )
    held_out_schedule = tuple(
        item
        for item in schedule.entries
        if item.phase is EvaluationSchedulePhaseV21.HELD_OUT_PRIMARY
    )
    if (
        seal.execution_id != manifest.execution_id
        or seal.execution_manifest_sha256 != manifest.manifest_sha256
        or seal.authoritative_claim_sha256 != manifest.authoritative_claim_sha256
        or seal.held_out_pack_seal_sha256 != held_out_pack_seal.seal_sha256
        or manifest.held_out_pack_seal_sha256 != held_out_pack_seal.seal_sha256
        or seal.schedule_sha256 != schedule.schedule_sha256
        or manifest.schedule_sha256 != schedule.schedule_sha256
        or len(held_out_schedule) != 24
    ):
        raise ValueError("held-out execution seal protocol binding differs")
    resolved_claim_path = _resolved_authoritative_claim_path(
        held_out_pack_seal=held_out_pack_seal,
        authoritative_claim_path=authoritative_claim_path,
    )
    _load_authoritative_claim(
        authoritative_claim_path=resolved_claim_path,
        manifest=manifest,
        seal=seal,
        held_out_pack_seal=held_out_pack_seal,
        schedule=schedule,
    )
    pack_bindings = {item.case_id: item for item in held_out_pack_seal.cases}
    for scheduled, receipt in zip(held_out_schedule, seal.entries, strict=True):
        binding = pack_bindings.get(scheduled.case_id)
        if binding is None:
            raise ValueError("held-out execution schedule case lacks sealed binding")
        expected_claim = _build_entry_claim(
            execution_id=manifest.execution_id,
            ordinal=scheduled.ordinal,
            case_id=scheduled.case_id,
            case_sha256=binding.case_sha256,
            truth_sha256=binding.truth_sha256,
            arm=scheduled.arm,
        )
        if (
            receipt.ordinal != scheduled.ordinal
            or receipt.case_id != scheduled.case_id
            or receipt.arm is not scheduled.arm
            or receipt.execution_id != manifest.execution_id
            or receipt.entry_execution_id != expected_claim.entry_execution_id
            or receipt.case_sha256 != binding.case_sha256
            or receipt.truth_sha256 != binding.truth_sha256
            or receipt.entry_claim_sha256 != expected_claim.claim_sha256
        ):
            raise ValueError("held-out execution seal schedule differs")
        _load_execution_entry(private_execution_root, receipt, expected_claim)
    return seal


def _build_entry_result(
    *,
    receipt: HeldOutExecutionEntryReceiptV21,
    prediction: EvaluationPredictionV21,
    truth: EvaluatorCaseTruthV21,
    agent_result: DtaAgentRunResultV21,
) -> EvaluationEntryResultV21:
    score = build_evaluation_score_v21(prediction=prediction, truth=truth)
    payload: dict[str, Any] = {
        "schema_version": "dta-v21.evaluation-entry-result.v1",
        "execution_id": receipt.entry_execution_id,
        "case_sha256": receipt.case_sha256,
        "truth_sha256": receipt.truth_sha256,
        "arm": receipt.arm,
        "model_id": receipt.model_id,
        "identity_sha256": receipt.identity_sha256,
        "agent_result_sha256": agent_result.result_sha256,
        "prediction": prediction,
        "score": score,
    }
    return cast(
        EvaluationEntryResultV21,
        _with_digest(EvaluationEntryResultV21, payload, "entry_sha256"),
    )


def _entry_claim_from_receipt(
    receipt: HeldOutExecutionEntryReceiptV21,
) -> HeldOutExecutionEntryClaimV21:
    return _build_entry_claim(
        execution_id=receipt.execution_id,
        ordinal=receipt.ordinal,
        case_id=receipt.case_id,
        case_sha256=receipt.case_sha256,
        truth_sha256=receipt.truth_sha256,
        arm=receipt.arm,
    )


def _public_report(
    *,
    evaluation: HeldOutEvaluationReportV21,
    execution_seal: HeldOutExecutionSealV21,
    pack_seal: HeldOutPackSealV21,
    unblinding: HeldOutUnblindingReceiptV21,
    preregistration: EvaluationPreregistrationV21,
) -> HeldOutPublicEvaluationReportV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.public-held-out-evaluation-report.v1",
        "terminal": "DTA_V21_PR_E_HELD_OUT_COMPLETED",
        "execution_id": execution_seal.execution_id,
        "held_out_case_count": 8,
        "scored_entry_count": 24,
        "model_id": evaluation.model_id,
        "identity_sha256s": evaluation.identity_sha256s,
        "held_out_pack_seal_sha256": pack_seal.seal_sha256,
        "execution_seal_sha256": execution_seal.execution_seal_sha256,
        "unblinding_receipt_sha256": unblinding.unblinding_receipt_sha256,
        "primary_comparison": "EVIDENCE_GUIDED_PLANNER_vs_FLAT_ADAPTIVE",
        "one_shot_role": "DESCRIPTIVE_ANCHOR_ONLY",
        "preregistered_thresholds": preregistration.thresholds,
        "evaluation": evaluation,
        "exact_claim": evaluation.claim_decision.marker,
        "limitations": (
            "This is one sealed eight-case local replay evaluation.",
            "One-shot is a descriptive anchor, not a superiority target.",
            "The result is not production evidence or live recovery accuracy.",
        ),
    }
    return cast(
        HeldOutPublicEvaluationReportV21,
        _with_digest(HeldOutPublicEvaluationReportV21, payload, "report_sha256"),
    )


def _disposition(
    report: HeldOutPublicEvaluationReportV21,
) -> HeldOutEvaluationDispositionV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.held-out-evaluation-disposition.v1",
        "terminal": report.terminal,
        "claim": report.exact_claim,
        "execution_id": report.execution_id,
        "held_out_case_count": report.held_out_case_count,
        "scored_entry_count": report.scored_entry_count,
        "held_out_pack_seal_sha256": report.held_out_pack_seal_sha256,
        "execution_seal_sha256": report.execution_seal_sha256,
        "unblinding_receipt_sha256": report.unblinding_receipt_sha256,
        "report_sha256": report.report_sha256,
        "truth_isolation": "PASS",
        "scorer_verification": "PASS",
        "no_agent_provider_rerun_during_analysis": True,
    }
    return cast(
        HeldOutEvaluationDispositionV21,
        _with_digest(
            HeldOutEvaluationDispositionV21,
            payload,
            "disposition_sha256",
        ),
    )


def _markdown_report(report: HeldOutPublicEvaluationReportV21) -> str:
    lines = [
        "# DTA v2.1 P0 Held-Out Evaluation",
        "",
        f"- Terminal: `{report.terminal}`",
        f"- Exact claim: `{report.exact_claim}`",
        f"- Execution ID: `{report.execution_id}`",
        f"- Held-out cases / scored entries: {report.held_out_case_count} / {report.scored_entry_count}",
        f"- Model: `{report.model_id}`",
        f"- Held-out pack seal: `{report.held_out_pack_seal_sha256}`",
        f"- Execution seal: `{report.execution_seal_sha256}`",
        "",
        "## Aggregate metrics",
        "",
        "| Group | Entries | Protocol | Root | Mechanism | Macro-F1 | Evidence | Action | Mean input | Mean total | Mean reads | Median latency ms | Unsafe |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report.evaluation.aggregates:
        if item.group_type not in {"OVERALL", "ARM"}:
            continue
        lines.append(
            "| "
            + " | ".join(
                (
                    item.group_value,
                    str(item.scored_entries),
                    f"{item.protocol_acceptance_rate:.3f}",
                    "n/a"
                    if item.root_exact_match_rate is None
                    else f"{item.root_exact_match_rate:.3f}",
                    "n/a"
                    if item.mechanism_accuracy is None
                    else f"{item.mechanism_accuracy:.3f}",
                    "n/a"
                    if item.mechanism_macro_f1 is None
                    else f"{item.mechanism_macro_f1:.3f}",
                    f"{item.evidence_validity_rate:.3f}",
                    f"{item.action_precision:.3f}",
                    f"{item.mean_input_tokens:.1f}",
                    f"{item.mean_total_tokens:.1f}",
                    f"{item.mean_read_tool_dispatches:.2f}",
                    f"{item.median_latency_ms:.1f}",
                    str(item.unsafe_proposal_attempts),
                )
            )
            + " |"
        )
    thresholds = report.preregistered_thresholds
    decision = report.evaluation.claim_decision
    threshold_rows = (
        (
            "Protocol acceptance for both primary arms",
            f"{thresholds.protocol_acceptance_both:.2f}",
            decision.protocol_acceptance_both,
        ),
        ("Root exact match", "Planner not lower", decision.root_not_lower),
        (
            "Mechanism Macro-F1 delta",
            f">= {thresholds.mechanism_macro_f1_minimum_delta:.2f}",
            decision.mechanism_macro_f1_delta,
        ),
        (
            "Evidence-validity advantage",
            f">= {thresholds.evidence_validity_minimum_additional_cases} case and >= {thresholds.evidence_validity_minimum_rate_delta:.2f}",
            decision.evidence_validity_advantage,
        ),
        (
            "Runbook Top-1 or action-precision advantage",
            f">= {thresholds.action_metric_minimum_additional_cases} case and >= {thresholds.action_metric_minimum_rate_delta:.2f}",
            decision.action_metric_advantage,
        ),
        (
            "Mean input-token ratio",
            f"<= {thresholds.planner_mean_input_token_ratio_maximum:.2f}",
            decision.mean_input_token_ratio,
        ),
        (
            "Mean total-token ratio",
            f"<= {thresholds.planner_mean_total_token_ratio_maximum:.2f}",
            decision.mean_total_token_ratio,
        ),
        (
            "Mean semantic-read ratio",
            f"<= {thresholds.planner_mean_semantic_read_ratio_maximum:.2f}",
            decision.mean_semantic_read_ratio,
        ),
        (
            "Median latency ratio",
            f"<= {thresholds.planner_median_latency_ratio_maximum:.2f}",
            decision.median_latency_ratio,
        ),
        ("Duplicate normalized calls", "0", decision.duplicate_normalized_calls_zero),
        ("Unsafe proposal attempts", "0", decision.unsafe_proposal_attempts_zero),
        ("Arbitrary shell attempts", "0", decision.arbitrary_shell_attempts_zero),
        ("Non-owned mutations", "0", decision.non_owned_mutations_zero),
    )
    lines.extend(
        (
            "",
            "## Preregistered threshold table",
            "",
            "| Condition | Frozen threshold | Passed |",
            "|---|---|---|",
        )
    )
    lines.extend(
        f"| {name} | {threshold} | `{str(passed).lower()}` |"
        for name, threshold, passed in threshold_rows
    )
    lines.extend(
        (
            f"| Truth isolation | PASS | `{str(decision.truth_isolation).lower()}` |",
            f"| Scorer verification | PASS | `{str(decision.scorer_verification).lower()}` |",
            "",
            "## Limitations",
            "",
        )
    )
    lines.extend(f"- {item}" for item in report.limitations)
    lines.extend(
        (
            "",
            "The JSON report contains the complete per-arm, per-mechanism, and per-generalization-slice metric set plus the frozen threshold decision.",
            "",
        )
    )
    return "\n".join(lines)


def _write_public_text_create_once(path: Path, text: str) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    data = text.encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("public held-out write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def score_held_out_evaluation_v21(
    *,
    repository_root: Path,
    held_out_pack_root: Path,
    private_execution_root: Path,
    private_unblinding_root: Path,
    freeze_manifest: EvaluationFreezeManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
    held_out_pack_seal: HeldOutPackSealV21,
    execution_seal: HeldOutExecutionSealV21,
    public_evaluation_json: Path,
    public_evaluation_markdown: Path,
    public_disposition_path: Path,
    unblinded_at: datetime | None = None,
    authoritative_claim_path: Path | None = None,
) -> tuple[HeldOutPublicEvaluationReportV21, HeldOutEvaluationDispositionV21]:
    """Unblind once after the complete execution seal, then score deterministically."""

    receipt_path = private_unblinding_root / "unblinding-receipt.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise FileExistsError("held-out execution was already unblinded")
    _verify_frozen_inputs(
        repository_root=repository_root,
        freeze_manifest=freeze_manifest,
        schedule=schedule,
        preregistration=preregistration,
        held_out_pack_root=held_out_pack_root,
        held_out_pack_seal=held_out_pack_seal,
    )
    verified = verify_held_out_execution_seal_v21(
        private_execution_root=private_execution_root,
        held_out_pack_seal=held_out_pack_seal,
        schedule=schedule,
        authoritative_claim_path=authoritative_claim_path,
    )
    if verified != execution_seal:
        raise ValueError("held-out execution seal argument differs from disk")
    receipt_payload: dict[str, object] = {
        "schema_version": "dta-v21.held-out-unblinding-receipt.v1",
        "execution_id": execution_seal.execution_id,
        "unblinded_at": (unblinded_at or datetime.now(timezone.utc)).replace(
            microsecond=0
        ),
        "held_out_pack_seal_sha256": held_out_pack_seal.seal_sha256,
        "execution_seal_sha256": execution_seal.execution_seal_sha256,
        "prediction_sha256s": tuple(
            item.prediction_sha256 for item in execution_seal.entries
        ),
    }
    unblinding = cast(
        HeldOutUnblindingReceiptV21,
        _with_digest(
            HeldOutUnblindingReceiptV21,
            receipt_payload,
            "unblinding_receipt_sha256",
        ),
    )
    write_private_json(receipt_path, unblinding, create_once=True)
    truths: dict[str, EvaluatorCaseTruthV21] = {}
    case_bindings: dict[str, PublicCaseBindingV21] = {}
    for binding in freeze_manifest.public_case_manifest.held_out_cases:
        truth = EvaluatorCaseTruthV21.model_validate_json(
            _read_regular(
                held_out_pack_root / "cases" / binding.case_id / "evaluator-truth.json"
            )
        )
        if (
            truth.case_id != binding.case_id
            or truth.split is not EvaluationSplitV21.HELD_OUT
            or truth.truth_sha256 != binding.truth_sha256
        ):
            raise ValueError("unblinded truth differs from the frozen binding")
        truths[binding.case_id] = truth
        case_bindings[binding.case_id] = binding
    entries: list[EvaluationEntryResultV21] = []
    for receipt in execution_seal.entries:
        agent_result, prediction = _load_execution_entry(
            private_execution_root, receipt, _entry_claim_from_receipt(receipt)
        )
        entry = _build_entry_result(
            receipt=receipt,
            prediction=prediction,
            truth=truths[receipt.case_id],
            agent_result=agent_result,
        )
        repeated = _build_entry_result(
            receipt=receipt,
            prediction=prediction,
            truth=truths[receipt.case_id],
            agent_result=agent_result,
        )
        if repeated != entry:
            raise ValueError("held-out scorer is not deterministic")
        entries.append(entry)
    identities = build_three_arm_identities_v21(
        model_id=preregistration.model_id,
        max_completion_tokens=preregistration.max_completion_tokens,
    )
    evaluation = build_held_out_report_v21(
        entries=tuple(entries),
        truths=truths,
        case_bindings=case_bindings,
        identities=identities,
        preregistration=preregistration,
        truth_isolation_verified=True,
        scorer_verified=True,
    )
    repeated_evaluation = build_held_out_report_v21(
        entries=tuple(entries),
        truths=truths,
        case_bindings=case_bindings,
        identities=identities,
        preregistration=preregistration,
        truth_isolation_verified=True,
        scorer_verified=True,
    )
    if repeated_evaluation != evaluation:
        raise ValueError("held-out aggregate report is not deterministic")
    for receipt, entry in zip(execution_seal.entries, entries, strict=True):
        root = (
            private_unblinding_root
            / "entries"
            / _entry_name(receipt.ordinal, receipt.case_id, receipt.arm)
        )
        write_private_json(root / "score.json", entry.score, create_once=True)
        write_private_json(root / "entry-result.json", entry, create_once=True)
    write_private_json(
        private_unblinding_root / "held-out-evaluation-report.json",
        evaluation,
        create_once=True,
    )
    report = _public_report(
        evaluation=evaluation,
        execution_seal=execution_seal,
        pack_seal=held_out_pack_seal,
        unblinding=unblinding,
        preregistration=preregistration,
    )
    disposition = _disposition(report)
    write_public_model_create_once_v21(public_evaluation_json, report)
    _write_public_text_create_once(public_evaluation_markdown, _markdown_report(report))
    write_public_model_create_once_v21(public_disposition_path, disposition)
    return report, disposition


def verify_private_held_out_evaluation_v21(
    *,
    repository_root: Path,
    held_out_pack_root: Path,
    private_execution_root: Path,
    private_unblinding_root: Path,
    freeze_manifest: EvaluationFreezeManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
    held_out_pack_seal: HeldOutPackSealV21,
    public_report: HeldOutPublicEvaluationReportV21,
    authoritative_claim_path: Path | None = None,
) -> HeldOutEvaluationReportV21:
    """Verify sealed predictions and deterministic scores without a Provider call."""

    _verify_frozen_inputs(
        repository_root=repository_root,
        freeze_manifest=freeze_manifest,
        schedule=schedule,
        preregistration=preregistration,
        held_out_pack_root=held_out_pack_root,
        held_out_pack_seal=held_out_pack_seal,
    )
    execution_seal = verify_held_out_execution_seal_v21(
        private_execution_root=private_execution_root,
        held_out_pack_seal=held_out_pack_seal,
        schedule=schedule,
        authoritative_claim_path=authoritative_claim_path,
    )
    unblinding = HeldOutUnblindingReceiptV21.model_validate_json(
        _read_regular(private_unblinding_root / "unblinding-receipt.json")
    )
    if (
        unblinding.execution_id != execution_seal.execution_id
        or unblinding.execution_seal_sha256 != execution_seal.execution_seal_sha256
        or unblinding.held_out_pack_seal_sha256 != held_out_pack_seal.seal_sha256
        or tuple(item.prediction_sha256 for item in execution_seal.entries)
        != unblinding.prediction_sha256s
    ):
        raise ValueError("held-out unblinding receipt differs")
    truths: dict[str, EvaluatorCaseTruthV21] = {}
    bindings = {
        item.case_id: item
        for item in freeze_manifest.public_case_manifest.held_out_cases
    }
    for case_id, binding in bindings.items():
        truth = EvaluatorCaseTruthV21.model_validate_json(
            _read_regular(
                held_out_pack_root / "cases" / case_id / "evaluator-truth.json"
            )
        )
        if (
            truth.case_id != case_id
            or truth.truth_sha256 != binding.truth_sha256
            or truth.split is not EvaluationSplitV21.HELD_OUT
        ):
            raise ValueError("held-out verification truth differs")
        truths[case_id] = truth
    entries: list[EvaluationEntryResultV21] = []
    for receipt in execution_seal.entries:
        agent_result, prediction = _load_execution_entry(
            private_execution_root, receipt, _entry_claim_from_receipt(receipt)
        )
        entry_root = (
            private_unblinding_root
            / "entries"
            / _entry_name(receipt.ordinal, receipt.case_id, receipt.arm)
        )
        persisted = EvaluationEntryResultV21.model_validate_json(
            _read_regular(entry_root / "entry-result.json")
        )
        rebuilt = _build_entry_result(
            receipt=receipt,
            prediction=prediction,
            truth=truths[receipt.case_id],
            agent_result=agent_result,
        )
        if persisted != rebuilt:
            raise ValueError("held-out persisted score is not deterministic")
        entries.append(persisted)
    identities = build_three_arm_identities_v21(
        model_id=preregistration.model_id,
        max_completion_tokens=preregistration.max_completion_tokens,
    )
    rebuilt_report = build_held_out_report_v21(
        entries=tuple(entries),
        truths=truths,
        case_bindings=bindings,
        identities=identities,
        preregistration=preregistration,
        truth_isolation_verified=True,
        scorer_verified=True,
    )
    private_report = HeldOutEvaluationReportV21.model_validate_json(
        _read_regular(private_unblinding_root / "held-out-evaluation-report.json")
    )
    if private_report != rebuilt_report or public_report.evaluation != rebuilt_report:
        raise ValueError("held-out aggregate report differs from sealed scores")
    return rebuilt_report


def build_development_ablation_report_v21(
    *,
    development_attempt_root: Path,
    development_dataset_root: Path,
    public_development_report_path: Path,
) -> DevelopmentAblationReportV21:
    """Build the matched four-case visible-development compaction ablation."""

    manifest = DevelopmentAttemptManifestV21.model_validate_json(
        _read_regular(development_attempt_root / "attempt-manifest.json")
    )
    receipt = DevelopmentAttemptReceiptV21.model_validate_json(
        _read_regular(development_attempt_root / "attempt-receipt.json")
    )
    private_report = DevelopmentEvaluationReportV21.model_validate_json(
        _read_regular(development_attempt_root / "development-report.json")
    )
    public_report = DevelopmentEvaluationReportV21.model_validate_json(
        _read_regular(public_development_report_path)
    )
    entry_paths = sorted(
        (development_attempt_root / "entries").glob("*/entry-result.json"),
        key=lambda path: int(path.parent.name.split("-", 1)[0]),
    )
    entries = tuple(
        EvaluationEntryResultV21.model_validate_json(_read_regular(path))
        for path in entry_paths
    )
    if (
        manifest.attempt_id != receipt.attempt_id
        or len(entries) != 40
        or tuple(item.entry_sha256 for item in entries) != receipt.entry_sha256s
        or private_report != public_report
        or receipt.report_sha256 != public_report.report_sha256
    ):
        raise ValueError("development ablation attempt evidence differs")
    truths = {
        case_id: EvaluatorCaseTruthV21.model_validate_json(
            _read_regular(
                development_dataset_root / "evaluator-truth" / f"{case_id}.json"
            )
        )
        for case_id in ABLATION_CASE_IDS_V21
    }
    compact = tuple(
        item
        for item in entries
        if item.prediction.case_id in ABLATION_CASE_IDS_V21
        and item.arm is EvaluationArmV21.EVIDENCE_GUIDED_PLANNER
    )
    no_compaction = tuple(
        item
        for item in entries
        if item.prediction.case_id in ABLATION_CASE_IDS_V21
        and item.arm is EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION
    )
    if (
        len(compact) != 4
        or len(no_compaction) != 4
        or {item.prediction.case_id for item in compact} != set(ABLATION_CASE_IDS_V21)
        or {item.prediction.case_id for item in no_compaction}
        != set(ABLATION_CASE_IDS_V21)
    ):
        raise ValueError("development ablation matched entry set differs")
    compact_aggregate = _aggregate(
        group_type="ARM",
        group_value=EvaluationArmV21.EVIDENCE_GUIDED_PLANNER.value,
        entries=list(compact),
        truths=truths,
    )
    no_compaction_aggregate = _aggregate(
        group_type="ARM",
        group_value=(EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION.value),
        entries=list(no_compaction),
        truths=truths,
    )
    if (
        no_compaction_aggregate.mean_input_tokens == 0.0
        or no_compaction_aggregate.mean_total_tokens == 0.0
        or no_compaction_aggregate.mean_read_tool_dispatches == 0.0
    ):
        raise ValueError("development ablation denominator is zero")
    input_ratio = (
        compact_aggregate.mean_input_tokens / no_compaction_aggregate.mean_input_tokens
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.development-ablation-report.v1",
        "development_attempt_id": manifest.attempt_id,
        "development_report_sha256": public_report.report_sha256,
        "matched_case_ids": ABLATION_CASE_IDS_V21,
        "compact_context": compact_aggregate,
        "no_compaction": no_compaction_aggregate,
        "mean_input_token_ratio": input_ratio,
        "mean_total_token_ratio": (
            compact_aggregate.mean_total_tokens
            / no_compaction_aggregate.mean_total_tokens
        ),
        "mean_semantic_read_ratio": (
            compact_aggregate.mean_read_tool_dispatches
            / no_compaction_aggregate.mean_read_tool_dispatches
        ),
        "compact_context_reduced_mean_input_tokens": input_ratio < 1.0,
        "limitations": (
            "This ablation uses four visible development cases only.",
            "It is not part of the held-out planner-advantage claim.",
        ),
    }
    return cast(
        DevelopmentAblationReportV21,
        _with_digest(DevelopmentAblationReportV21, payload, "report_sha256"),
    )


def _ablation_markdown(report: DevelopmentAblationReportV21) -> str:
    return "\n".join(
        (
            "# DTA v2.1 P0 Development Compaction Ablation",
            "",
            f"- Matched visible-development cases: {len(report.matched_case_ids)}",
            f"- Development report: `{report.development_report_sha256}`",
            f"- Compact / no-compaction mean input-token ratio: {report.mean_input_token_ratio:.4f}",
            f"- Compact / no-compaction mean total-token ratio: {report.mean_total_token_ratio:.4f}",
            f"- Compact / no-compaction mean semantic-read ratio: {report.mean_semantic_read_ratio:.4f}",
            f"- Reduced mean input tokens: `{str(report.compact_context_reduced_mean_input_tokens).lower()}`",
            "",
            "This is a development-only matched ablation and is not part of the held-out superiority claim.",
            "",
        )
    )


def publish_development_ablation_report_v21(
    *,
    report: DevelopmentAblationReportV21,
    public_json_path: Path,
    public_markdown_path: Path,
) -> None:
    report = DevelopmentAblationReportV21.model_validate(
        report.model_dump(mode="python")
    )
    write_public_model_create_once_v21(public_json_path, report)
    _write_public_text_create_once(public_markdown_path, _ablation_markdown(report))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute or score the one-time DTA v2.1 held-out evaluation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("execute", "score"):
        child = subparsers.add_parser(command)
        child.add_argument("--repository-root", type=Path, required=True)
        child.add_argument("--held-out-pack-root", type=Path, required=True)
        child.add_argument("--freeze-manifest", type=Path, required=True)
        child.add_argument("--schedule", type=Path, required=True)
        child.add_argument("--preregistration", type=Path, required=True)
        child.add_argument("--held-out-pack-seal", type=Path, required=True)
    execute = subparsers.choices["execute"]
    execute.add_argument("--provider-env", type=Path, required=True)
    execute.add_argument("--private-execution-root", type=Path, required=True)
    execute.add_argument("--execution-id", required=True)
    execute.add_argument("--execution-code-head", required=True)
    execute.add_argument("--git-audit-root", type=Path, required=True)
    score = subparsers.choices["score"]
    score.add_argument("--private-execution-root", type=Path, required=True)
    score.add_argument("--private-unblinding-root", type=Path, required=True)
    score.add_argument("--development-attempt-root", type=Path, required=True)
    score.add_argument("--development-dataset-root", type=Path, required=True)
    score.add_argument("--public-development-report", type=Path, required=True)
    score.add_argument("--public-evaluation-json", type=Path, required=True)
    score.add_argument("--public-evaluation-markdown", type=Path, required=True)
    score.add_argument("--public-ablation-json", type=Path, required=True)
    score.add_argument("--public-ablation-markdown", type=Path, required=True)
    score.add_argument("--public-disposition", type=Path, required=True)
    return parser


def _load_protocol(args):
    freeze = EvaluationFreezeManifestV21.model_validate_json(
        _read_regular(args.freeze_manifest.resolve())
    )
    schedule = EvaluationScheduleV21.model_validate_json(
        _read_regular(args.schedule.resolve())
    )
    preregistration = EvaluationPreregistrationV21.model_validate_json(
        _read_regular(args.preregistration.resolve())
    )
    pack_seal = HeldOutPackSealV21.model_validate_json(
        _read_regular(args.held_out_pack_seal.resolve())
    )
    return freeze, schedule, preregistration, pack_seal


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        freeze, schedule, preregistration, pack_seal = _load_protocol(args)
        if args.command == "execute":
            runner = AuditedSubprocessRunner(
                project_root=args.repository_root.resolve(),
                artifacts_root=args.git_audit_root.resolve(),
                run_id=args.execution_id,
            )
            seal = execute_held_out_evaluation_v21(
                repository_root=args.repository_root.resolve(),
                provider_env_path=args.provider_env.resolve(),
                held_out_pack_root=args.held_out_pack_root.resolve(),
                private_execution_root=args.private_execution_root.resolve(),
                execution_id=args.execution_id,
                execution_code_head=args.execution_code_head,
                freeze_manifest=freeze,
                schedule=schedule,
                preregistration=preregistration,
                held_out_pack_seal=pack_seal,
                git_runner=runner,
            )
            print(
                json.dumps(
                    {
                        "terminal": "DTA_V21_HELD_OUT_EXECUTION_SEALED",
                        "execution_id": seal.execution_id,
                        "entry_count": seal.entry_count,
                        "execution_seal_sha256": seal.execution_seal_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0
        ablation = build_development_ablation_report_v21(
            development_attempt_root=args.development_attempt_root.resolve(),
            development_dataset_root=args.development_dataset_root.resolve(),
            public_development_report_path=args.public_development_report.resolve(),
        )
        for target in (
            args.public_evaluation_json,
            args.public_evaluation_markdown,
            args.public_ablation_json,
            args.public_ablation_markdown,
            args.public_disposition,
        ):
            if target.exists() or target.is_symlink():
                raise FileExistsError(target)
        execution_seal = HeldOutExecutionSealV21.model_validate_json(
            _read_regular(args.private_execution_root.resolve() / "execution-seal.json")
        )
        report, disposition = score_held_out_evaluation_v21(
            repository_root=args.repository_root.resolve(),
            held_out_pack_root=args.held_out_pack_root.resolve(),
            private_execution_root=args.private_execution_root.resolve(),
            private_unblinding_root=args.private_unblinding_root.resolve(),
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            held_out_pack_seal=pack_seal,
            execution_seal=execution_seal,
            public_evaluation_json=args.public_evaluation_json.resolve(),
            public_evaluation_markdown=args.public_evaluation_markdown.resolve(),
            public_disposition_path=args.public_disposition.resolve(),
        )
        publish_development_ablation_report_v21(
            report=ablation,
            public_json_path=args.public_ablation_json.resolve(),
            public_markdown_path=args.public_ablation_markdown.resolve(),
        )
        print(
            json.dumps(
                {
                    "terminal": disposition.terminal,
                    "claim": disposition.claim,
                    "execution_id": report.execution_id,
                    "scored_entry_count": report.scored_entry_count,
                    "report_sha256": report.report_sha256,
                    "ablation_report_sha256": ablation.report_sha256,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "terminal": "BLOCKED_DTA_V21_HELD_OUT_PROTOCOL",
                    "failure_type": type(error).__name__,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "DevelopmentAblationReportV21",
    "HeldOutEvaluationDispositionV21",
    "HeldOutExecutionClaimV21",
    "HeldOutExecutionEntryClaimV21",
    "HeldOutExecutionEntryReceiptV21",
    "HeldOutExecutionManifestV21",
    "HeldOutExecutionSealV21",
    "HeldOutPublicEvaluationReportV21",
    "HeldOutUnblindingReceiptV21",
    "execute_held_out_evaluation_v21",
    "build_development_ablation_report_v21",
    "publish_development_ablation_report_v21",
    "score_held_out_evaluation_v21",
    "verify_held_out_execution_seal_v21",
    "verify_private_held_out_evaluation_v21",
)
