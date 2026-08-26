"""Deterministic 84-path preflight for the DTA v2.3.3 final study."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    load_admission_matrix_v233,
    load_evaluation_cases_v233,
    load_evaluation_views_v233,
)
from ecomsre.dta_v2.v23.evaluation_v231 import materialize_evaluation_case_v231
from ecomsre.dta_v2.v23.evaluation_v233 import (
    EvaluationArmRunV233,
    EvaluationPolicyV233,
    LazyTruthStoreV233,
    run_combined_arm_v233,
    run_domain_bound_arm_v233,
    run_v232_baseline_arm_v233,
)
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDispositionV233,
)


class RuntimePreflightPathV233(DtaModelV22):
    case_id: str
    policy: EvaluationPolicyV233
    final_disposition: str
    discovery_read_count: int = Field(ge=0, le=3)
    domain_projection_present: bool
    guard_disposition: str | None
    provider_calls: Literal[0]
    action_authority_violations: Literal[0]
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimePreflightV233(DtaModelV22):
    schema_version: Literal["dta-v233.runtime-preflight.v1"]
    case_count: Literal[28]
    arm_count: Literal[3]
    deterministic_path_count: Literal[84]
    fixed_evaluation_execution_count: Literal[0]
    runtime_exceptions: Literal[0]
    unmapped_anomaly_kinds: Literal[0]
    domain_projection_missing: Literal[0]
    provider_mechanical_field_drift: Literal[0]
    witness_contract_failures: Literal[0]
    premature_truth_access: Literal[0]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    truth_load_count: Literal[28]
    paths: tuple[RuntimePreflightPathV233, ...] = Field(
        min_length=84,
        max_length=84,
    )
    terminal: Literal["DTA_V233_RUNTIME_PREFLIGHT_PASS"]
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_preflight(self) -> "RuntimePreflightV233":
        keys = tuple((item.case_id, item.policy.value) for item in self.paths)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("v2.3.3 preflight paths are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("v2.3.3 preflight digest differs")
        return self


def _require_shared_inputs(runs: tuple[EvaluationArmRunV233, ...]) -> None:
    for field in (
        "case_id",
        "case_bytes_sha256",
        "active_view_sha256",
        "bootstrap_memory_sha256",
        "common_memory_sha256",
        "common_read_count",
    ):
        if len({getattr(item, field) for item in runs}) != 1:
            raise ValueError(f"v2.3.3 preflight common input differs: {field}")


def run_runtime_preflight_v233(
    *,
    repository_root: Path,
    evaluation_root: Path,
) -> RuntimePreflightV233:
    admission = load_admission_matrix_v233(
        evaluation_root / "admission-matrix.json"
    )
    if admission.terminal != "DTA_V233_EVALUATION_DATA_PASS":
        raise ValueError("v2.3.3 preflight lacks data admission")
    cases = load_evaluation_cases_v233(evaluation_root / "cases.json")
    views = load_evaluation_views_v233(evaluation_root / "ontology-views.json")
    truth_store = LazyTruthStoreV233(evaluation_root / "truth.json")
    paths: list[RuntimePreflightPathV233] = []
    domain_missing = 0
    drift = 0
    witness_failures = 0
    premature_truth = 0
    authority = 0
    for spec in cases.cases:
        if truth_store.loaded_case_ids:
            expected_prefix = tuple(
                item.case_id for item in cases.cases if item.case_id < spec.case_id
            )
            premature_truth += truth_store.loaded_case_ids != expected_prefix
        view = views.require(spec.case_id)
        case = materialize_evaluation_case_v231(
            repository_root=repository_root,
            spec=spec,
        )
        context = _build_common_context_v23(
            case=case,
            hidden_mechanism=view.hidden_mechanism,
        )
        runs = (
            run_v232_baseline_arm_v233(
                context=context,
                provider_transport=None,
            ),
            run_domain_bound_arm_v233(
                context=context,
                provider_transport=None,
            ),
            run_combined_arm_v233(
                repository_root=repository_root,
                context=context,
                provider_transport=None,
            ),
        )
        _require_shared_inputs(runs)
        if truth_store.loaded_case_ids and truth_store.loaded_case_ids[-1] == spec.case_id:
            premature_truth += 1
        for run in runs:
            if (
                run.policy is not EvaluationPolicyV233.V232_CONFLICT_AWARE_BASELINE
                and run.provisional_report is not None
                and run.domain_projection is None
            ):
                domain_missing += 1
            if run.provisional_report is not None and (
                run.runtime_root_service
                != run.provisional_report.runtime_selected_root_service
                or run.runtime_broad_domain
                is not run.provisional_report.broad_fault_domain
                or run.supporting_evidence_refs
                != run.provisional_report.supporting_evidence_refs
                or run.contradicting_evidence_refs
                != run.provisional_report.contradicting_evidence_refs
            ):
                drift += 1
            if (
                run.guard_decision is not None
                and run.guard_decision.disposition
                is IrreconcilableGuardDispositionV233.IRRECONCILABLE
                and not run.guard_decision.blocking_witness_ids
            ):
                witness_failures += 1
            authority += run.action_authority_violations
            paths.append(
                RuntimePreflightPathV233(
                    case_id=run.case_id,
                    policy=run.policy,
                    final_disposition=run.final_disposition,
                    discovery_read_count=run.discovery_read_count,
                    domain_projection_present=run.domain_projection is not None,
                    guard_disposition=(
                        None
                        if run.guard_decision is None
                        else run.guard_decision.disposition.value
                    ),
                    provider_calls=0,
                    action_authority_violations=0,
                    run_sha256=run.run_sha256,
                )
            )
        truth_store.load_case_after_three_arms(case_id=spec.case_id, runs=runs)
    canonical = tuple(sorted(paths, key=lambda item: (item.case_id, item.policy.value)))
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.runtime-preflight.v1",
        "case_count": 28,
        "arm_count": 3,
        "deterministic_path_count": len(canonical),
        "fixed_evaluation_execution_count": 0,
        "runtime_exceptions": 0,
        "unmapped_anomaly_kinds": 0,
        "domain_projection_missing": domain_missing,
        "provider_mechanical_field_drift": drift,
        "witness_contract_failures": witness_failures,
        "premature_truth_access": premature_truth,
        "action_authority_violations": authority,
        "agent_writes": 0,
        "runbook_executions": 0,
        "truth_load_count": len(truth_store.loaded_case_ids),
        "paths": canonical,
        "terminal": "DTA_V233_RUNTIME_PREFLIGHT_PASS",
    }
    draft = RuntimePreflightV233.model_construct(
        **payload,
        preflight_sha256="0" * 64,
    )
    return RuntimePreflightV233.model_validate(
        {
            **payload,
            "preflight_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"preflight_sha256"})
            ),
        }
    )


__all__ = (
    "RuntimePreflightPathV233",
    "RuntimePreflightV233",
    "run_runtime_preflight_v233",
)
