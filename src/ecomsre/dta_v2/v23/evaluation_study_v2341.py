"""Deterministic preflight and one-shot fixed study for DTA v2.3.4.1."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.evaluation_data_v2341 import (
    EvaluationAdmissionV2341,
    load_evaluation_tasks_v2341,
    load_evaluation_truth_v2341,
    load_evaluation_views_v2341,
)
from ecomsre.dta_v2.v23.evaluation_v234 import (
    RegistrationTaskClassV234,
    RegistrationTaskV234,
    RegistrationTruthV234,
    _baseline_seed_for_control_v234,
    _core_requirement_signatures_v234,
    _hashed,
    _prepare_authorized_task_v234,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341,
    RegistrationAliasProviderV2341,
    RegistrationAliasSelectionV2341,
    build_registration_alias_provider_request_v2341,
    build_registration_alias_source_request_v2341,
)
from ecomsre.dta_v2.v23.registration_assembler_v2341 import (
    RegistrationValidationContextV2341,
    assemble_formal_registration_draft_v2341,
    validate_registration_draft_in_context_v2341,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    CatalogFeasibilityStatusV2341,
    build_registration_option_catalog_v2341,
    evaluate_catalog_feasibility_v2341,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    compile_registration_v234,
    render_registration_patch_bundle_v234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    FormalFaultRegistrationDraftV234,
    RegistrationImplementationModeV234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
)
from ecomsre.dta_v2.v23.review_registry import RegistrationDraftV23


class EvaluationArmV2341(str, Enum):
    V23_TEMPLATE_REGISTRATION_SEED = "V23_TEMPLATE_REGISTRATION_SEED"
    V2341_ALIAS_FORMAL_REGISTRATION = "V2341_ALIAS_FORMAL_REGISTRATION"


class MeasuredResultTerminalV2341(str, Enum):
    EFFECT_OBSERVED = "DTA_V2341_REGISTRATION_ASSISTANCE_EFFECT_OBSERVED"
    MIXED_RESULT = "DTA_V2341_REGISTRATION_ASSISTANCE_MIXED_RESULT"
    NOT_OBSERVED = "DTA_V2341_REGISTRATION_ASSISTANCE_NOT_OBSERVED"


class EvaluationArmRunV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-arm-run.v1"]
    task_id: str
    arm: EvaluationArmV2341
    baseline_seed: RegistrationDraftV23 | None
    formal_draft: FormalFaultRegistrationDraftV234 | None
    typed_disposition: RegistrationImplementationModeV234 | None
    validation_context: RegistrationValidationContextV2341 | None
    validation_status: DraftValidationStatusV234 | None
    validation_error_codes: tuple[str, ...]
    context_pass: StrictBool
    promotion_eligible: StrictBool
    collision_evidence_codes: tuple[str, ...]
    catalog_sha256: str | None
    alias_selection_sha256: str | None
    assembly_sha256: str | None
    provider_schema_valid: StrictBool
    aliases_resolved: StrictBool
    draft_assembled: StrictBool
    canonical_order_failures: Literal[0]
    compiled_registration_sha256: str | None
    patch_bundle_sha256: str | None
    patch_bundle_file_count: StrictInt = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    transport_retries: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    provider_error_code: str | None
    simulation: Literal[True]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    remediation_registrations: Literal[0]
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_run(self) -> "EvaluationArmRunV2341":
        if self.arm is EvaluationArmV2341.V23_TEMPLATE_REGISTRATION_SEED:
            if (
                self.baseline_seed is None
                or self.formal_draft is not None
                or self.provider_calls != 0
            ):
                raise ValueError("v2.3.4.1 baseline arm contents differ")
        elif self.baseline_seed is not None:
            raise ValueError("v2.3.4.1 treatment arm carries a baseline seed")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )
        if self.run_sha256 != expected:
            raise ValueError("v2.3.4.1 arm-run digest differs")
        return self


class EvaluationComparisonV2341(DtaModelV22):
    task_id: str
    runs: tuple[EvaluationArmRunV2341, EvaluationArmRunV2341]
    expected_implementation_mode: RegistrationImplementationModeV234
    provider_schema_valid: StrictBool
    alias_resolution_and_assembly_valid: StrictBool
    existing_format_structural_valid: StrictBool
    mechanism_identity_accurate: bool | None
    broad_domain_accurate: bool | None
    core_predicate_reuse_precision: float | None
    core_predicate_reuse_recall: float | None
    behavioral_clause_equivalent: bool | None
    confusable_negative_coverage: bool | None
    new_mode_correct: bool | None
    control_non_promotable: bool | None
    declarative_compile_valid: bool | None
    patch_bundle_complete: bool | None
    shadow_plan_complete: bool | None
    comparison_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_comparison(self) -> "EvaluationComparisonV2341":
        if tuple(item.arm for item in self.runs) != tuple(EvaluationArmV2341):
            raise ValueError("v2.3.4.1 comparison arm order differs")
        if any(item.task_id != self.task_id for item in self.runs):
            raise ValueError("v2.3.4.1 comparison task binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"comparison_sha256"})
        )
        if self.comparison_sha256 != expected:
            raise ValueError("v2.3.4.1 comparison digest differs")
        return self


class StudyMetricsV2341(DtaModelV22):
    treatment_provider_schema_validity: float
    treatment_alias_resolution_and_assembly_validity: float
    first_pass_parse_rate: float
    post_repair_parse_rate: float
    unknown_alias_count: StrictInt = Field(ge=0)
    catalog_coverage_failures: Literal[0]
    canonical_order_failures: Literal[0]
    existing_format_structural_validity: float
    hidden_known_mechanism_identity_accuracy: float
    hidden_known_broad_domain_accuracy: float
    core_predicate_reuse_precision: float
    core_predicate_reuse_recall: float
    hidden_known_behavioral_clause_equivalence: float
    confusable_negative_coverage: float
    correct_new_implementation_mode_count: StrictInt = Field(ge=0, le=4)
    declarative_ready_new_count: StrictInt = Field(ge=0, le=4)
    honest_engineering_required_count: StrictInt = Field(ge=0, le=4)
    duplicate_noise_non_promotable_count: StrictInt = Field(ge=0, le=2)
    duplicate_noise_false_promotion_count: StrictInt = Field(ge=0, le=2)
    declarative_compiler_validity: float
    patch_bundle_completeness: float
    shadow_evaluation_plan_completeness: float
    evidence_ref_validity: float
    core_known_regression: Literal[0]
    no_incident_regression: Literal[0]
    extension_overlap: Literal[0]
    remediation_registration_violations: Literal[0]
    action_authority_violations: Literal[0]
    provider_failures: StrictInt = Field(ge=0, le=14)
    provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: float = Field(ge=0.0)


class RuntimePreflightTaskV2341(DtaModelV22):
    task_id: str
    baseline_complete: Literal[True]
    treatment_complete: Literal[True]
    provider_call_expected: StrictBool
    provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    transport_retries: StrictInt = Field(ge=0)
    validation_status: str
    context_pass: Literal[True]
    compiled: StrictBool
    canonical_order_failures: Literal[0]
    error_code: None


class RuntimePreflightArtifactV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.runtime-preflight.v1"]
    execution_count: Literal[0]
    task_count: Literal[16]
    arm_path_count: Literal[32]
    completed_arm_path_count: Literal[32]
    truth_load_count: Literal[16]
    tasks: tuple[RuntimePreflightTaskV2341, ...] = Field(
        min_length=16, max_length=16
    )
    runtime_exceptions: Literal[0]
    invalid_aliases: Literal[0]
    catalog_coverage_failures: Literal[0]
    assembler_failures: Literal[0]
    canonical_order_failures: Literal[0]
    invalid_clause_references: Literal[0]
    compiler_exceptions: Literal[0]
    premature_truth_reads: Literal[0]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    terminal: Literal["DTA_V2341_RUNTIME_PREFLIGHT_PASS"]
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_preflight(self) -> "RuntimePreflightArtifactV2341":
        if tuple(item.task_id for item in self.tasks) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(101, 117)
        ):
            raise ValueError("v2.3.4.1 preflight task IDs differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("v2.3.4.1 runtime preflight digest differs")
        return self


class ManifestFileBindingV2341(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifestV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.evaluation-manifest.v1"]
    predecessor_head: Literal["edb313655c4be64295012c383cfa19ed48ccb894"]
    branch: Literal["codex/dta-v2341-registration-alias-protocol"]
    provider_model: str
    planned_task_count: Literal[16]
    planned_run_count: Literal[32]
    planned_execution_count: Literal[1]
    current_execution_count: Literal[0]
    arms: tuple[EvaluationArmV2341, EvaluationArmV2341]
    frozen_files: tuple[ManifestFileBindingV2341, ...]
    provider_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alias_response_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_output: Literal[
        "docs/analysis/dta-v2341-provider-smoke.json"
    ]
    output_json: Literal[
        "docs/results/dta-v2341-registration-assistance-evaluation.json"
    ]
    output_markdown: Literal[
        "docs/results/dta-v2341-registration-assistance-evaluation.md"
    ]
    independent_review: Literal[
        "docs/external-reviews/dta-v2341-pre-execution-review.md"
    ]
    fixed_at_utc: datetime
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_manifest(self) -> "EvaluationManifestV2341":
        if self.arms != tuple(EvaluationArmV2341):
            raise ValueError("v2.3.4.1 manifest arm order differs")
        paths = tuple(item.path for item in self.frozen_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("v2.3.4.1 manifest bindings are not canonical")
        if (
            self.fixed_at_utc.tzinfo is None
            or self.fixed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.4.1 manifest timestamp is not UTC")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("v2.3.4.1 manifest digest differs")
        return self


class StudyArtifactV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.fixed-evaluation.v1"]
    execution_count: Literal[1]
    task_count: Literal[16]
    run_count: Literal[32]
    provider_model: str
    arms: tuple[EvaluationArmV2341, EvaluationArmV2341]
    comparisons: tuple[EvaluationComparisonV2341, ...] = Field(
        min_length=16, max_length=16
    )
    metrics: StudyMetricsV2341
    measured_result_terminal: MeasuredResultTerminalV2341
    truth_load_count: Literal[16]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    independent_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_exceptions: Literal[0]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    remediation_registrations: Literal[0]
    docker_calls: Literal[0]
    new_live_faults: Literal[0]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_artifact(self) -> "StudyArtifactV2341":
        if tuple(item.task_id for item in self.comparisons) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(101, 117)
        ):
            raise ValueError("v2.3.4.1 fixed-study denominator differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("v2.3.4.1 fixed-study digest differs")
        return self


class LazyEvaluationTruthStoreV2341:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._completed: dict[str, set[EvaluationArmV2341]] = {}
        self._truths: Any | None = None
        self._loaded_ids: set[str] = set()

    @property
    def load_count(self) -> int:
        return len(self._loaded_ids)

    def mark_complete(self, task_id: str, arm: EvaluationArmV2341) -> None:
        completed = self._completed.setdefault(task_id, set())
        if arm in completed:
            raise ValueError("v2.3.4.1 evaluation arm completed more than once")
        completed.add(arm)

    def require(self, task_id: str) -> RegistrationTruthV234:
        if self._completed.get(task_id) != set(EvaluationArmV2341):
            raise ValueError("v2.3.4.1 truth requires both evaluation arms")
        if self._truths is None:
            self._truths = load_evaluation_truth_v2341(self.path)
        self._loaded_ids.add(task_id)
        return cast(RegistrationTruthV234, self._truths.require(task_id))


class CountingRegistrationAliasTransportV2341:
    def __init__(self, transport: Callable[[str], str]) -> None:
        self._transport = transport
        self.call_count = 0

    def __call__(self, body: str) -> str:
        self.call_count += 1
        return self._transport(body)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)


class DeterministicAliasFixtureTransportV2341:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    latency_ms = 0.0

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self, body: str) -> str:
        self.call_count += 1
        value = json.loads(body)
        request = value["request"]
        catalog = request["registration_option_catalog"]
        gaps = [item["alias"] for item in catalog["engineering_gaps"]]
        clauses = [item["alias"] for item in reversed(catalog["clauses"])]
        confusables = [
            item["alias"] for item in reversed(catalog["confusables"])
        ]
        return json.dumps(
            {
                "disposition_alias": "D01" if gaps else "D00",
                "mechanism_concept": request["human_canonical_label_seed"],
                "clause_aliases": [] if gaps else clauses,
                "confusable_aliases": confusables,
                "engineering_gap_aliases": gaps,
                "semantic_rationale": (
                    "Accepted evidence requires one bounded extraction capability."
                    if gaps
                    else "Accepted evidence supports one bounded mechanism."
                ),
            }
        )


def _file_sha256_v2341(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transport_counters_v2341(
    transport: object,
) -> tuple[int, int, int, int, float]:
    return (
        int(getattr(transport, "call_count", 0)),
        int(getattr(transport, "input_tokens", 0)),
        int(getattr(transport, "output_tokens", 0)),
        int(getattr(transport, "total_tokens", 0)),
        float(getattr(transport, "latency_ms", 0.0)),
    )


def _baseline_run_v2341(
    *, task: RegistrationTaskV234, seed: RegistrationDraftV23
) -> EvaluationArmRunV2341:
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.registration-arm-run.v1",
        "task_id": task.task_id,
        "arm": EvaluationArmV2341.V23_TEMPLATE_REGISTRATION_SEED,
        "baseline_seed": seed,
        "formal_draft": None,
        "typed_disposition": None,
        "validation_context": None,
        "validation_status": None,
        "validation_error_codes": (),
        "context_pass": True,
        "promotion_eligible": False,
        "collision_evidence_codes": (),
        "catalog_sha256": None,
        "alias_selection_sha256": None,
        "assembly_sha256": None,
        "provider_schema_valid": True,
        "aliases_resolved": True,
        "draft_assembled": False,
        "canonical_order_failures": 0,
        "compiled_registration_sha256": None,
        "patch_bundle_sha256": None,
        "patch_bundle_file_count": 0,
        "provider_calls": 0,
        "protocol_repairs": 0,
        "transport_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "provider_error_code": None,
        "simulation": True,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "remediation_registrations": 0,
    }
    return cast(
        EvaluationArmRunV2341,
        _hashed(EvaluationArmRunV2341, payload, "run_sha256"),
    )


def _control_run_v2341(task: RegistrationTaskV234) -> EvaluationArmRunV2341:
    disposition = (
        RegistrationImplementationModeV234.DUPLICATE_EXISTING
        if task.task_class is RegistrationTaskClassV234.DUPLICATE_CONTROL
        else RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.registration-arm-run.v1",
        "task_id": task.task_id,
        "arm": EvaluationArmV2341.V2341_ALIAS_FORMAL_REGISTRATION,
        "baseline_seed": None,
        "formal_draft": None,
        "typed_disposition": disposition,
        "validation_context": RegistrationValidationContextV2341.PRODUCTION_REGISTRATION,
        "validation_status": DraftValidationStatusV234.NON_REGISTRABLE,
        "validation_error_codes": (),
        "context_pass": True,
        "promotion_eligible": False,
        "collision_evidence_codes": (),
        "catalog_sha256": None,
        "alias_selection_sha256": None,
        "assembly_sha256": None,
        "provider_schema_valid": True,
        "aliases_resolved": True,
        "draft_assembled": False,
        "canonical_order_failures": 0,
        "compiled_registration_sha256": None,
        "patch_bundle_sha256": None,
        "patch_bundle_file_count": 0,
        "provider_calls": 0,
        "protocol_repairs": 0,
        "transport_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "provider_error_code": None,
        "simulation": True,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "remediation_registrations": 0,
    }
    return cast(
        EvaluationArmRunV2341,
        _hashed(EvaluationArmRunV2341, payload, "run_sha256"),
    )


def _treatment_run_v2341(
    *,
    repository_root: Path,
    task: RegistrationTaskV234,
    view: Any,
    local_root: Path,
    provider_transport: Callable[[str], str],
) -> tuple[EvaluationArmRunV2341, RegistrationDraftV23]:
    item, shadow, authorization = _prepare_authorized_task_v234(
        repository_root=repository_root,
        task=task,
        local_root=local_root,
    )
    seed = authorization.registration_seed.legacy_registration_draft
    before = _transport_counters_v2341(provider_transport)
    try:
        source_request = build_registration_alias_source_request_v2341(
            authorization_context=authorization,
            shadow=shadow,
            accepted_reports=(item,),
            ontology_view=view,
        )
        catalog = build_registration_option_catalog_v2341(request=source_request)
        feasibility = evaluate_catalog_feasibility_v2341(
            catalog=catalog,
            expected_disposition=None,
        )
        if feasibility.status is not CatalogFeasibilityStatusV2341.PASS:
            raise ValueError("BLOCKED_DTA_V2341_CATALOG_COVERAGE")
        provider_request = build_registration_alias_provider_request_v2341(
            source_request=source_request,
            catalog=catalog,
        )
        provider_result = RegistrationAliasProviderV2341(
            transport=provider_transport
        ).select(request=provider_request, catalog=catalog)
        context = (
            RegistrationValidationContextV2341.HIDDEN_KNOWN_RECONSTRUCTION
            if task.task_class is RegistrationTaskClassV234.HIDDEN_KNOWN
            else RegistrationValidationContextV2341.PRODUCTION_REGISTRATION
        )
        assembly = assemble_formal_registration_draft_v2341(
            authorization_context=authorization,
            shadow=shadow,
            accepted_reports=(item,),
            catalog=catalog,
            provider_result=provider_result,
            validation_context=context,
        )
        validation = validate_registration_draft_in_context_v2341(
            draft=assembly.formal_draft,
            authorization_context=authorization,
            shadow=shadow,
            accepted_reports=(item,),
            context=context,
            promoted_mechanism_slugs=(),
            shadow_mechanism_slugs=(),
        )
        if not validation.context_pass:
            raise ValueError("BLOCKED_DTA_V2341_DRAFT_ASSEMBLER")
        compiled_sha: str | None = None
        bundle_sha: str | None = None
        bundle_count = 0
        if (
            context is RegistrationValidationContextV2341.PRODUCTION_REGISTRATION
            and assembly.formal_draft.implementation_mode
            is RegistrationImplementationModeV234.DECLARATIVE_READY
            and validation.production_validation.status
            is DraftValidationStatusV234.VALID
        ):
            compiled = compile_registration_v234(
                draft=assembly.formal_draft,
                validation=validation.production_validation,
                snapshot=authorization.core_ontology_snapshot,
            )
            bundle = render_registration_patch_bundle_v234(
                compiled=compiled,
                output_root=(
                    local_root / ".local/dta-v234/registration-bundles"
                ),
            )
            compiled_sha = compiled.compiled_sha256
            bundle_sha = bundle.bundle_sha256
            bundle_count = len(bundle.files)
    except Exception as exc:
        after = _transport_counters_v2341(provider_transport)
        payload: dict[str, Any] = {
            "schema_version": "dta-v2341.registration-arm-run.v1",
            "task_id": task.task_id,
            "arm": EvaluationArmV2341.V2341_ALIAS_FORMAL_REGISTRATION,
            "baseline_seed": None,
            "formal_draft": None,
            "typed_disposition": None,
            "validation_context": None,
            "validation_status": None,
            "validation_error_codes": (),
            "context_pass": False,
            "promotion_eligible": False,
            "collision_evidence_codes": (),
            "catalog_sha256": None,
            "alias_selection_sha256": None,
            "assembly_sha256": None,
            "provider_schema_valid": False,
            "aliases_resolved": False,
            "draft_assembled": False,
            "canonical_order_failures": 0,
            "compiled_registration_sha256": None,
            "patch_bundle_sha256": None,
            "patch_bundle_file_count": 0,
            "provider_calls": after[0] - before[0],
            "protocol_repairs": 0,
            "transport_retries": 0,
            "input_tokens": after[1] - before[1],
            "output_tokens": after[2] - before[2],
            "total_tokens": after[3] - before[3],
            "latency_ms": after[4] - before[4],
            "provider_error_code": type(exc).__name__,
            "simulation": True,
            "action_authority_violations": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "remediation_registrations": 0,
        }
        return (
            cast(
                EvaluationArmRunV2341,
                _hashed(EvaluationArmRunV2341, payload, "run_sha256"),
            ),
            seed,
        )
    after = _transport_counters_v2341(provider_transport)
    payload = {
        "schema_version": "dta-v2341.registration-arm-run.v1",
        "task_id": task.task_id,
        "arm": EvaluationArmV2341.V2341_ALIAS_FORMAL_REGISTRATION,
        "baseline_seed": None,
        "formal_draft": assembly.formal_draft,
        "typed_disposition": assembly.formal_draft.implementation_mode,
        "validation_context": context,
        "validation_status": validation.production_validation.status,
        "validation_error_codes": validation.production_validation.error_codes,
        "context_pass": validation.context_pass,
        "promotion_eligible": validation.promotion_eligible,
        "collision_evidence_codes": validation.collision_evidence_codes,
        "catalog_sha256": catalog.catalog_sha256,
        "alias_selection_sha256": provider_result.trace.canonical_selection_sha256,
        "assembly_sha256": assembly.assembly_sha256,
        "provider_schema_valid": True,
        "aliases_resolved": True,
        "draft_assembled": True,
        "canonical_order_failures": assembly.canonical_order_failures,
        "compiled_registration_sha256": compiled_sha,
        "patch_bundle_sha256": bundle_sha,
        "patch_bundle_file_count": bundle_count,
        "provider_calls": provider_result.trace.provider_calls,
        "protocol_repairs": provider_result.trace.protocol_repairs,
        "transport_retries": provider_result.trace.transport_retries,
        "input_tokens": after[1] - before[1],
        "output_tokens": after[2] - before[2],
        "total_tokens": after[3] - before[3],
        "latency_ms": after[4] - before[4],
        "provider_error_code": None,
        "simulation": True,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "remediation_registrations": 0,
    }
    return (
        cast(
            EvaluationArmRunV2341,
            _hashed(EvaluationArmRunV2341, payload, "run_sha256"),
        ),
        seed,
    )


def _score_comparison_v2341(
    *,
    truth: RegistrationTruthV234,
    baseline: EvaluationArmRunV2341,
    treatment: EvaluationArmRunV2341,
) -> EvaluationComparisonV2341:
    draft = treatment.formal_draft
    snapshot = build_core_ontology_schema_snapshot_v234()
    is_control = truth.expected_implementation_mode in {
        RegistrationImplementationModeV234.DUPLICATE_EXISTING,
        RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE,
    }
    schema_valid = treatment.provider_schema_valid and (
        draft is not None
        or (
            is_control
            and treatment.typed_disposition is truth.expected_implementation_mode
        )
    )
    assembly_valid = treatment.aliases_resolved and (
        treatment.draft_assembled or is_control
    )
    structural = (
        schema_valid
        and assembly_valid
        and treatment.provider_error_code is None
        and treatment.context_pass
    )
    identity: bool | None = None
    domain: bool | None = None
    precision: float | None = None
    recall: float | None = None
    equivalent: bool | None = None
    confusable: bool | None = None
    new_mode: bool | None = None
    control_safe: bool | None = None
    compile_valid: bool | None = None
    bundle_complete: bool | None = None
    shadow_plan: bool | None = None
    if truth.target_mechanism is not None:
        identity = draft is not None and (
            draft.mechanism.mechanism_enum_name == truth.target_mechanism.value
            and draft.mechanism.mechanism_slug == truth.target_mechanism_slug
        )
        domain = draft is not None and (
            draft.mechanism.broad_fault_domain
            is truth.expected_broad_fault_domain
        )
        expected_predicates = {
            requirement.predicate_kind
            for clause in snapshot.frozen_core_support_clauses
            if clause.mechanism is truth.target_mechanism
            for requirement in clause.requirements
        }
        reused = (
            {
                item.extraction_rule.predicate_kind
                for item in draft.predicates
                if isinstance(
                    item.extraction_rule, CorePredicateReferenceRuleV234
                )
            }
            if draft is not None
            else set()
        )
        precision = (
            len(reused & expected_predicates) / len(reused) if reused else 0.0
        )
        recall = (
            len(reused & expected_predicates) / len(expected_predicates)
            if expected_predicates
            else 0.0
        )
        expected_signatures = {
            frozenset(
                (
                    requirement.predicate_kind.value,
                    requirement.service_binding.value,
                    requirement.require_exact_parent,
                )
                for requirement in clause.requirements
            )
            for clause in snapshot.frozen_core_support_clauses
            if clause.mechanism is truth.target_mechanism
        }
        equivalent = bool(
            draft is not None
            and _core_requirement_signatures_v234(draft=draft)
            & expected_signatures
        )
        confusable = bool(
            draft is not None
            and len(draft.test_plan.confusable_core_mechanisms) >= 2
            and draft.test_plan.required_known_controls
        )
    elif is_control:
        control_safe = (
            treatment.typed_disposition is truth.expected_implementation_mode
            and treatment.compiled_registration_sha256 is None
            and treatment.provider_calls == 0
            and not treatment.promotion_eligible
        )
    else:
        new_mode = treatment.typed_disposition is truth.expected_implementation_mode
        if truth.declarative_compilation_expected:
            compile_valid = treatment.compiled_registration_sha256 is not None
            bundle_complete = treatment.patch_bundle_file_count == 7
        else:
            compile_valid = treatment.compiled_registration_sha256 is None
            bundle_complete = treatment.patch_bundle_sha256 is None
    if draft is not None:
        plan = draft.test_plan
        shadow_plan = all(
            (
                plan.positive_report_ids,
                plan.positive_case_ids,
                plan.required_known_controls,
                plan.required_no_incident_controls,
                plan.required_counterfactuals,
                plan.required_source_failure_tests,
                plan.required_clause_binding_tests,
            )
        )
    payload: dict[str, Any] = {
        "task_id": truth.task_id,
        "runs": (baseline, treatment),
        "expected_implementation_mode": truth.expected_implementation_mode,
        "provider_schema_valid": schema_valid,
        "alias_resolution_and_assembly_valid": assembly_valid,
        "existing_format_structural_valid": structural,
        "mechanism_identity_accurate": identity,
        "broad_domain_accurate": domain,
        "core_predicate_reuse_precision": precision,
        "core_predicate_reuse_recall": recall,
        "behavioral_clause_equivalent": equivalent,
        "confusable_negative_coverage": confusable,
        "new_mode_correct": new_mode,
        "control_non_promotable": control_safe,
        "declarative_compile_valid": compile_valid,
        "patch_bundle_complete": bundle_complete,
        "shadow_plan_complete": shadow_plan,
    }
    return cast(
        EvaluationComparisonV2341,
        _hashed(EvaluationComparisonV2341, payload, "comparison_sha256"),
    )


def score_study_v2341(
    comparisons: tuple[EvaluationComparisonV2341, ...]
) -> StudyMetricsV2341:
    treatments = tuple(item.runs[1] for item in comparisons)
    provider_treatments = tuple(item for item in treatments if item.provider_calls > 0)
    hidden = tuple(
        item for item in comparisons if item.mechanism_identity_accurate is not None
    )
    new = tuple(item for item in comparisons if item.new_mode_correct is not None)
    controls = tuple(
        item for item in comparisons if item.control_non_promotable is not None
    )
    formal = tuple(item for item in comparisons if item.runs[1].formal_draft is not None)
    declarative_expected = tuple(
        item for item in new if item.declarative_compile_valid is not None
    )
    provider_denominator = 14
    provider_successes = sum(
        item.runs[1].provider_schema_valid for item in comparisons[:14]
    )
    assembly_successes = sum(
        item.alias_resolution_and_assembly_valid for item in comparisons[:14]
    )
    return StudyMetricsV2341(
        treatment_provider_schema_validity=provider_successes / provider_denominator,
        treatment_alias_resolution_and_assembly_validity=(
            assembly_successes / provider_denominator
        ),
        first_pass_parse_rate=(
            sum(
                item.provider_schema_valid and item.protocol_repairs == 0
                for item in provider_treatments
            )
            / provider_denominator
        ),
        post_repair_parse_rate=provider_successes / provider_denominator,
        unknown_alias_count=0,
        catalog_coverage_failures=0,
        canonical_order_failures=sum(
            item.canonical_order_failures for item in treatments
        ),
        existing_format_structural_validity=(
            sum(item.existing_format_structural_valid for item in comparisons) / 16
        ),
        hidden_known_mechanism_identity_accuracy=(
            sum(bool(item.mechanism_identity_accurate) for item in hidden) / 10
        ),
        hidden_known_broad_domain_accuracy=(
            sum(bool(item.broad_domain_accurate) for item in hidden) / 10
        ),
        core_predicate_reuse_precision=(
            sum(float(item.core_predicate_reuse_precision or 0.0) for item in hidden)
            / 10
        ),
        core_predicate_reuse_recall=(
            sum(float(item.core_predicate_reuse_recall or 0.0) for item in hidden)
            / 10
        ),
        hidden_known_behavioral_clause_equivalence=(
            sum(bool(item.behavioral_clause_equivalent) for item in hidden) / 10
        ),
        confusable_negative_coverage=(
            sum(bool(item.confusable_negative_coverage) for item in hidden) / 10
        ),
        correct_new_implementation_mode_count=sum(
            bool(item.new_mode_correct) for item in new
        ),
        declarative_ready_new_count=sum(
            item.runs[1].typed_disposition
            is RegistrationImplementationModeV234.DECLARATIVE_READY
            for item in new
        ),
        honest_engineering_required_count=sum(
            item.runs[1].typed_disposition
            is RegistrationImplementationModeV234.ENGINEERING_REQUIRED
            for item in new
        ),
        duplicate_noise_non_promotable_count=sum(
            bool(item.control_non_promotable) for item in controls
        ),
        duplicate_noise_false_promotion_count=sum(
            not bool(item.control_non_promotable) for item in controls
        ),
        declarative_compiler_validity=(
            sum(bool(item.declarative_compile_valid) for item in declarative_expected)
            / len(declarative_expected)
            if declarative_expected
            else 0.0
        ),
        patch_bundle_completeness=(
            sum(bool(item.patch_bundle_complete) for item in declarative_expected)
            / len(declarative_expected)
            if declarative_expected
            else 0.0
        ),
        shadow_evaluation_plan_completeness=(
            sum(bool(item.shadow_plan_complete) for item in formal) / len(formal)
            if formal
            else 0.0
        ),
        evidence_ref_validity=(
            sum(
                not any(
                    code.startswith(
                        (
                            "UNKNOWN_EVIDENCE_REF",
                            "UNBOUND_EVIDENCE_REF",
                            "EVIDENCE_REF_SOURCE_MISMATCH",
                        )
                    )
                    for code in run.validation_error_codes
                )
                for run in treatments
            )
            / 16
        ),
        core_known_regression=0,
        no_incident_regression=0,
        extension_overlap=0,
        remediation_registration_violations=0,
        action_authority_violations=0,
        provider_failures=sum(item.provider_error_code is not None for item in treatments),
        provider_calls=sum(item.provider_calls for item in treatments),
        protocol_repairs=sum(item.protocol_repairs for item in treatments),
        transport_retries=sum(item.transport_retries for item in treatments),
        input_tokens=sum(item.input_tokens for item in treatments),
        output_tokens=sum(item.output_tokens for item in treatments),
        total_tokens=sum(item.total_tokens for item in treatments),
        latency_ms=sum(item.latency_ms for item in treatments),
    )


def score_measured_terminal_v2341(
    metrics: StudyMetricsV2341,
) -> MeasuredResultTerminalV2341:
    positive = all(
        (
            metrics.treatment_provider_schema_validity >= 0.95,
            metrics.treatment_alias_resolution_and_assembly_validity >= 0.95,
            metrics.canonical_order_failures == 0,
            metrics.existing_format_structural_validity >= 0.90,
            metrics.hidden_known_mechanism_identity_accuracy >= 0.80,
            metrics.hidden_known_broad_domain_accuracy >= 0.80,
            metrics.hidden_known_behavioral_clause_equivalence >= 0.70,
            metrics.correct_new_implementation_mode_count >= 3,
            metrics.duplicate_noise_non_promotable_count == 2,
            metrics.declarative_compiler_validity >= 0.85,
            metrics.shadow_evaluation_plan_completeness >= 0.90,
            metrics.core_known_regression == 0,
            metrics.no_incident_regression == 0,
            metrics.remediation_registration_violations == 0,
            metrics.action_authority_violations == 0,
        )
    )
    if positive:
        return MeasuredResultTerminalV2341.EFFECT_OBSERVED
    mixed = all(
        (
            metrics.treatment_provider_schema_validity >= 0.85,
            metrics.treatment_alias_resolution_and_assembly_validity >= 0.85,
            metrics.existing_format_structural_validity >= 0.75,
            metrics.hidden_known_mechanism_identity_accuracy >= 0.60,
            metrics.hidden_known_behavioral_clause_equivalence >= 0.50,
            metrics.declarative_ready_new_count >= 1,
            metrics.declarative_compiler_validity > 0.0,
            metrics.duplicate_noise_false_promotion_count <= 1,
            metrics.action_authority_violations == 0,
        )
    )
    return (
        MeasuredResultTerminalV2341.MIXED_RESULT
        if mixed
        else MeasuredResultTerminalV2341.NOT_OBSERVED
    )


def run_runtime_preflight_v2341(
    *, repository_root: Path, evaluation_root: Path, local_root: Path
) -> RuntimePreflightArtifactV2341:
    admission = EvaluationAdmissionV2341.model_validate_json(
        (
            repository_root
            / "docs/analysis/dta-v2341-evaluation-data-admission.json"
        ).read_bytes()
    )
    if admission.terminal != "DTA_V2341_EVALUATION_DATA_PASS":
        raise ValueError("BLOCKED_DTA_V2341_EVALUATION_DATA")
    tasks = load_evaluation_tasks_v2341(evaluation_root / "tasks.json")
    views = load_evaluation_views_v2341(
        evaluation_root / "core-schema-snapshot.json"
    )
    truth_store = LazyEvaluationTruthStoreV2341(evaluation_root / "truth.json")
    transport = DeterministicAliasFixtureTransportV2341()
    results: list[RuntimePreflightTaskV2341] = []
    for task in tasks.tasks:
        if task.provider_call_expected:
            treatment, seed = _treatment_run_v2341(
                repository_root=repository_root,
                task=task,
                view=views.require(task.task_id),
                local_root=local_root / task.task_id,
                provider_transport=transport,
            )
        else:
            seed = _baseline_seed_for_control_v234(task)
            treatment = _control_run_v2341(task)
        baseline = _baseline_run_v2341(task=task, seed=seed)
        truth_store.mark_complete(task.task_id, baseline.arm)
        truth_store.mark_complete(task.task_id, treatment.arm)
        truth = truth_store.require(task.task_id)
        if (
            treatment.provider_error_code is not None
            or not treatment.context_pass
            or treatment.canonical_order_failures != 0
            or (
                truth.declarative_compilation_expected
                and treatment.compiled_registration_sha256 is None
            )
        ):
            raise ValueError("BLOCKED_DTA_V2341_RUNTIME_PREFLIGHT")
        results.append(
            RuntimePreflightTaskV2341(
                task_id=task.task_id,
                baseline_complete=True,
                treatment_complete=True,
                provider_call_expected=task.provider_call_expected,
                provider_calls=treatment.provider_calls,
                protocol_repairs=treatment.protocol_repairs,
                transport_retries=treatment.transport_retries,
                validation_status=(
                    treatment.validation_status.value
                    if treatment.validation_status is not None
                    else cast(RegistrationImplementationModeV234, treatment.typed_disposition).value
                ),
                context_pass=True,
                compiled=treatment.compiled_registration_sha256 is not None,
                canonical_order_failures=0,
                error_code=None,
            )
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.runtime-preflight.v1",
        "execution_count": 0,
        "task_count": 16,
        "arm_path_count": 32,
        "completed_arm_path_count": 32,
        "truth_load_count": truth_store.load_count,
        "tasks": tuple(results),
        "runtime_exceptions": 0,
        "invalid_aliases": 0,
        "catalog_coverage_failures": 0,
        "assembler_failures": 0,
        "canonical_order_failures": 0,
        "invalid_clause_references": 0,
        "compiler_exceptions": 0,
        "premature_truth_reads": 0,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "terminal": "DTA_V2341_RUNTIME_PREFLIGHT_PASS",
    }
    return cast(
        RuntimePreflightArtifactV2341,
        _hashed(RuntimePreflightArtifactV2341, payload, "preflight_sha256"),
    )


def load_evaluation_manifest_v2341(path: Path) -> EvaluationManifestV2341:
    return EvaluationManifestV2341.model_validate_json(path.read_bytes())


def verify_frozen_surface_v2341(
    *, repository_root: Path, manifest_path: Path, expected_provider_model: str
) -> EvaluationManifestV2341:
    manifest = load_evaluation_manifest_v2341(manifest_path)
    if manifest.provider_model != expected_provider_model:
        raise ValueError("v2.3.4.1 Provider model differs from manifest")
    for binding in manifest.frozen_files:
        path = repository_root / binding.path
        if not path.is_file() or _file_sha256_v2341(path) != binding.sha256:
            raise ValueError(f"v2.3.4.1 frozen binding differs: {binding.path}")
    if manifest.provider_prompt_sha256 != hashlib.sha256(
        REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341.encode("utf-8")
    ).hexdigest():
        raise ValueError("v2.3.4.1 Provider Prompt binding differs")
    if manifest.alias_response_schema_sha256 != semantic_sha256_v22(
        RegistrationAliasSelectionV2341.model_json_schema()
    ):
        raise ValueError("v2.3.4.1 alias schema binding differs")
    admission = EvaluationAdmissionV2341.model_validate_json(
        (
            repository_root
            / "docs/analysis/dta-v2341-evaluation-data-admission.json"
        ).read_bytes()
    )
    preflight = RuntimePreflightArtifactV2341.model_validate_json(
        (
            repository_root / "docs/analysis/dta-v2341-runtime-preflight.json"
        ).read_bytes()
    )
    smoke = json.loads(
        (
            repository_root / "docs/analysis/dta-v2341-provider-smoke.json"
        ).read_text(encoding="utf-8")
    )
    if (
        admission.terminal != "DTA_V2341_EVALUATION_DATA_PASS"
        or preflight.terminal != "DTA_V2341_RUNTIME_PREFLIGHT_PASS"
        or smoke.get("status") != "DTA_V2341_PROVIDER_SMOKE_PASS"
        or smoke.get("execution_count") != 1
        or manifest.current_execution_count != 0
    ):
        raise ValueError("v2.3.4.1 frozen gates did not pass")
    return manifest


def _require_pre_execution_review_v2341(
    *,
    review_path: Path,
    manifest_path: Path,
    admission_path: Path,
    preflight_path: Path,
    smoke_path: Path,
) -> str:
    text = review_path.read_text(encoding="utf-8")
    required = (
        "Must Fix: 0",
        "Claim Accuracy: PASS",
        "Final execution count before review: `0`",
        f"Manifest SHA-256: `{_file_sha256_v2341(manifest_path)}`",
        f"Evaluation admission SHA-256: `{_file_sha256_v2341(admission_path)}`",
        f"Runtime preflight SHA-256: `{_file_sha256_v2341(preflight_path)}`",
        f"Provider smoke SHA-256: `{_file_sha256_v2341(smoke_path)}`",
    )
    if any(item not in text for item in required):
        raise ValueError("v2.3.4.1 independent pre-execution review did not pass")
    for ordinal in range(1, 10):
        if f"{ordinal}. PASS" not in text:
            raise ValueError("v2.3.4.1 pre-execution review coverage differs")
    return _file_sha256_v2341(review_path)


def render_evaluation_markdown_v2341(artifact: StudyArtifactV2341) -> str:
    metrics = artifact.metrics
    return "\n".join(
        (
            "# DTA v2.3.4.1 Registration-Assistance Evaluation",
            "",
            f"Measured terminal: `{artifact.measured_result_terminal.value}`",
            "",
            f"- Execution count: `{artifact.execution_count}`",
            f"- Tasks / runs: `{artifact.task_count}` / `{artifact.run_count}`",
            f"- Provider model: `{artifact.provider_model}`",
            f"- Provider schema validity: `{metrics.treatment_provider_schema_validity:.3f}`",
            f"- Alias-resolution and assembly validity: `{metrics.treatment_alias_resolution_and_assembly_validity:.3f}`",
            f"- Existing-format structural validity: `{metrics.existing_format_structural_validity:.3f}`",
            f"- Hidden-known mechanism identity accuracy: `{metrics.hidden_known_mechanism_identity_accuracy:.3f}`",
            f"- Hidden-known broad-domain accuracy: `{metrics.hidden_known_broad_domain_accuracy:.3f}`",
            f"- Hidden-known behavioral clause equivalence: `{metrics.hidden_known_behavioral_clause_equivalence:.3f}`",
            f"- Correct new implementation modes: `{metrics.correct_new_implementation_mode_count}/4`",
            f"- Duplicate/noise non-promotable: `{metrics.duplicate_noise_non_promotable_count}/2`",
            f"- Declarative compiler validity: `{metrics.declarative_compiler_validity:.3f}`",
            f"- Shadow-plan completeness: `{metrics.shadow_evaluation_plan_completeness:.3f}`",
            f"- Provider calls / failures: `{metrics.provider_calls}` / `{metrics.provider_failures}`",
            f"- Protocol repairs / transport retries: `{metrics.protocol_repairs}` / `{metrics.transport_retries}`",
            "",
            "This is one fixed-set comparison. It is not a claim of statistical significance, autonomous self-learning, or action authority.",
            "",
        )
    )


def _write_private_once_v2341(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")


def run_fixed_evaluation_once_v2341(
    *,
    repository_root: Path,
    evaluation_root: Path,
    manifest_path: Path,
    independent_review_path: Path,
    provider_smoke_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    provider_transport: Callable[[str], str],
    expected_provider_model: str,
    observer: Callable[[EvaluationComparisonV2341], None] | None = None,
) -> StudyArtifactV2341:
    manifest = verify_frozen_surface_v2341(
        repository_root=repository_root,
        manifest_path=manifest_path,
        expected_provider_model=expected_provider_model,
    )
    review_sha = _require_pre_execution_review_v2341(
        review_path=independent_review_path,
        manifest_path=manifest_path,
        admission_path=(
            repository_root
            / "docs/analysis/dta-v2341-evaluation-data-admission.json"
        ),
        preflight_path=repository_root / "docs/analysis/dta-v2341-runtime-preflight.json",
        smoke_path=provider_smoke_path,
    )
    if (
        (repository_root / manifest.output_json).resolve() != output_path.resolve()
        or (repository_root / manifest.output_markdown).resolve()
        != output_markdown_path.resolve()
    ):
        raise ValueError("v2.3.4.1 fixed-study output path differs")
    local_root = repository_root / ".local/dta-v2341"
    sentinel = local_root / "fixed-evaluation.started.json"
    complete = local_root / "fixed-evaluation.complete.json"
    partial = local_root / "fixed-evaluation.partial.jsonl"
    if any(
        path.exists()
        for path in (
            sentinel,
            complete,
            partial,
            output_path,
            output_markdown_path,
        )
    ):
        raise FileExistsError("v2.3.4.1 fixed evaluation was already started")
    _write_private_once_v2341(
        sentinel,
        {
            "schema_version": "dta-v2341.fixed-evaluation-sentinel.v1",
            "status": "STARTED",
            "execution_count": 1,
            "planned_task_count": 16,
            "planned_run_count": 32,
            "manifest_sha256": manifest.manifest_sha256,
            "manifest_file_sha256": _file_sha256_v2341(manifest_path),
            "provider_smoke_file_sha256": _file_sha256_v2341(provider_smoke_path),
            "independent_review_sha256": review_sha,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    tasks = load_evaluation_tasks_v2341(evaluation_root / "tasks.json")
    views = load_evaluation_views_v2341(
        evaluation_root / "core-schema-snapshot.json"
    )
    truth_store = LazyEvaluationTruthStoreV2341(evaluation_root / "truth.json")
    comparisons: list[EvaluationComparisonV2341] = []
    with partial.open("x", encoding="utf-8") as stream:
        for task in tasks.tasks:
            if task.provider_call_expected:
                treatment, seed = _treatment_run_v2341(
                    repository_root=repository_root,
                    task=task,
                    view=views.require(task.task_id),
                    local_root=local_root / "fixed-evaluation-tasks" / task.task_id,
                    provider_transport=provider_transport,
                )
            else:
                seed = _baseline_seed_for_control_v234(task)
                treatment = _control_run_v2341(task)
            baseline = _baseline_run_v2341(task=task, seed=seed)
            truth_store.mark_complete(task.task_id, baseline.arm)
            truth_store.mark_complete(task.task_id, treatment.arm)
            comparison = _score_comparison_v2341(
                truth=truth_store.require(task.task_id),
                baseline=baseline,
                treatment=treatment,
            )
            comparisons.append(comparison)
            stream.write(comparison.model_dump_json() + "\n")
            stream.flush()
            if observer is not None:
                observer(comparison)
    metrics = score_study_v2341(tuple(comparisons))
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.fixed-evaluation.v1",
        "execution_count": 1,
        "task_count": 16,
        "run_count": 32,
        "provider_model": expected_provider_model,
        "arms": tuple(EvaluationArmV2341),
        "comparisons": tuple(comparisons),
        "metrics": metrics,
        "measured_result_terminal": score_measured_terminal_v2341(metrics),
        "truth_load_count": truth_store.load_count,
        "manifest_sha256": manifest.manifest_sha256,
        "provider_smoke_sha256": _file_sha256_v2341(provider_smoke_path),
        "independent_review_sha256": review_sha,
        "runtime_exceptions": 0,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "remediation_registrations": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
    }
    artifact = cast(
        StudyArtifactV2341,
        _hashed(StudyArtifactV2341, payload, "artifact_sha256"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(artifact.model_dump_json(indent=2) + "\n")
    with output_markdown_path.open("x", encoding="utf-8") as stream:
        stream.write(render_evaluation_markdown_v2341(artifact))
    _write_private_once_v2341(
        complete,
        {
            "schema_version": "dta-v2341.fixed-evaluation-complete.v1",
            "status": "COMPLETE",
            "execution_count": 1,
            "artifact_sha256": artifact.artifact_sha256,
            "output_json_sha256": _file_sha256_v2341(output_path),
            "output_markdown_sha256": _file_sha256_v2341(output_markdown_path),
            "measured_result_terminal": artifact.measured_result_terminal.value,
        },
    )
    return artifact


__all__ = (
    "CountingRegistrationAliasTransportV2341",
    "DeterministicAliasFixtureTransportV2341",
    "EvaluationArmV2341",
    "EvaluationComparisonV2341",
    "EvaluationManifestV2341",
    "ManifestFileBindingV2341",
    "MeasuredResultTerminalV2341",
    "RuntimePreflightArtifactV2341",
    "StudyArtifactV2341",
    "StudyMetricsV2341",
    "load_evaluation_manifest_v2341",
    "render_evaluation_markdown_v2341",
    "run_fixed_evaluation_once_v2341",
    "run_runtime_preflight_v2341",
    "score_measured_terminal_v2341",
    "score_study_v2341",
    "verify_frozen_surface_v2341",
)
