"""Frozen smoke and one-shot successor study contracts for DTA v2.3.2."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
)
from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23
from ecomsre.dta_v2.v23.discovery_provider_v231 import (
    DISCOVERY_SYSTEM_PROMPT_V231,
)
from ecomsre.dta_v2.v23.evaluation import EvaluationArmRunV23
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    AdmissionMatrixV232,
    load_evaluation_cases_v232,
    load_evaluation_truth_index_v232,
    load_evaluation_truth_shard_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationArmRunV231,
    EvaluationCasePairV231,
    EvaluationMetricsV231,
    EvaluationTruthV231,
    MeasuredResultTerminalV231,
    OpenAICompatibleDiscoveryTransportV231,
    score_evaluation_pairs_v231,
    score_measured_terminal_v231,
)
from ecomsre.dta_v2.v23.evaluation_v232 import (
    EvaluationPolicyV232,
    run_evaluation_policy_v232,
    run_evaluation_policy_with_trace_v232,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.runtime_preflight_v232 import (
    RuntimeTotalityPreflightV232,
)


class MeasuredResultTerminalV232(str, Enum):
    EFFECT_OBSERVED = "DTA_V232_CONFLICT_AWARE_DISCOVERY_EFFECT_OBSERVED"
    MIXED_RESULT = "DTA_V232_CONFLICT_AWARE_DISCOVERY_MIXED_RESULT"
    NOT_OBSERVED = "DTA_V232_CONFLICT_AWARE_DISCOVERY_NOT_OBSERVED"


class ManifestFileBindingV232(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifestV232(DtaModelV22):
    schema_version: Literal["dta-v232.evaluation-manifest.v1"]
    base_commit: Literal["7fe2bff7186cca1cedd2513f7984709057fc19e5"]
    branch: Literal["codex/dta-v232-anomaly-totality-successor"]
    provider_model: str
    planned_case_count: Literal[24]
    planned_run_count: Literal[48]
    planned_execution_count: Literal[1]
    predecessor_evaluation_data: Literal["BLOCKED_DTA_V231_EVALUATION_DATA"]
    predecessor_repository_acceptance: Literal[
        "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE"
    ]
    study_relation: Literal["INDEPENDENT_SUCCESSOR_NOT_RERUN"]
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    history_ledger: ManifestFileBindingV232
    history_verifier: ManifestFileBindingV232
    cases: ManifestFileBindingV232
    truth_index: ManifestFileBindingV232
    truth_shards: tuple[ManifestFileBindingV232, ...] = Field(
        min_length=24,
        max_length=24,
    )
    ontology_views: ManifestFileBindingV232
    strata: ManifestFileBindingV232
    admission_matrix: ManifestFileBindingV232
    runtime_totality_preflight: ManifestFileBindingV232
    fixed_surface_sources: tuple[ManifestFileBindingV232, ...] = Field(
        min_length=10
    )
    frozen_predecessor_sources: tuple[ManifestFileBindingV232, ...] = Field(
        min_length=3
    )
    strict_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    treatment_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_output: str
    output_json: str
    output_markdown: str
    independent_review: str
    fixed_at_utc: datetime

    @model_validator(mode="after")
    def require_manifest(self) -> "EvaluationManifestV232":
        if (
            self.fixed_at_utc.tzinfo is None
            or self.fixed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.2 manifest timestamp is not UTC")
        if self.registry_sha256 != (
            DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.registry_sha256
        ):
            raise ValueError("v2.3.2 manifest registry binding differs")
        truth_paths = tuple(item.path for item in self.truth_shards)
        if truth_paths != tuple(
            f"config/dta-v232/evaluation/truth/vx-{ordinal:03d}.json"
            for ordinal in range(201, 225)
        ):
            raise ValueError("v2.3.2 manifest truth shard set differs")
        for bindings in (
            self.fixed_surface_sources,
            self.frozen_predecessor_sources,
        ):
            paths = tuple(item.path for item in bindings)
            if paths != tuple(sorted(set(paths))):
                raise ValueError("v2.3.2 manifest source bindings are not canonical")
        return self


class EvaluationCasePairV232(DtaModelV22):
    schema_version: Literal["dta-v232.evaluation-case-pair.v1"]
    case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    baseline_policy: Literal[
        EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL
    ]
    treatment_policy: Literal[
        EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL
    ]
    arm_order: tuple[EvaluationPolicyV232, EvaluationPolicyV232]
    strict: EvaluationArmRunV23
    treatment: EvaluationArmRunV231
    evaluator_truth: EvaluationTruthV231
    pair_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_pair(self) -> "EvaluationCasePairV232":
        if {
            self.case_id,
            self.strict.case_id,
            self.treatment.case_id,
            self.evaluator_truth.case_id,
        } != {self.case_id}:
            raise ValueError("v2.3.2 pair case IDs differ")
        if set(self.arm_order) != set(EvaluationPolicyV232):
            raise ValueError("v2.3.2 pair arm order differs")
        for field in (
            "case_bytes_sha256",
            "active_view_sha256",
            "bootstrap_memory_sha256",
            "common_memory_sha256",
            "common_read_count",
            "known_admission_sha256",
        ):
            if getattr(self.strict, field) != getattr(self.treatment, field):
                raise ValueError(f"v2.3.2 pair common input differs: {field}")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"pair_sha256"})
        )
        if self.pair_sha256 != expected:
            raise ValueError("v2.3.2 pair digest differs")
        return self


class LazyTruthStoreV232:
    """Open one truth shard only after both arms for that case complete."""

    def __init__(self, path: Path) -> None:
        self._index_path = path
        self._index = load_evaluation_truth_index_v232(path)
        self._loaded_case_ids: set[str] = set()

    @property
    def loaded_case_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._loaded_case_ids))

    def load_case_after_both_arms(
        self,
        *,
        case_id: str,
        strict: EvaluationArmRunV23,
        treatment: EvaluationArmRunV231,
    ) -> EvaluationTruthV231:
        if strict.case_id != case_id or treatment.case_id != case_id:
            raise ValueError("v2.3.2 truth unlock arm case differs")
        if case_id in self._loaded_case_ids:
            raise ValueError("v2.3.2 truth shard was loaded twice")
        binding = self._index.require(case_id)
        truth = load_evaluation_truth_shard_v232(
            index_path=self._index_path,
            binding=binding,
        ).record.evaluator_truth
        self._loaded_case_ids.add(case_id)
        return truth


def counterbalanced_arm_order_v232(
    case_index: int,
) -> tuple[EvaluationPolicyV232, EvaluationPolicyV232]:
    strict = EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL
    treatment = EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL
    return (strict, treatment) if case_index % 2 == 0 else (treatment, strict)


def _file_sha256_v232(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evaluation_manifest_v232(path: Path) -> EvaluationManifestV232:
    return EvaluationManifestV232.model_validate_json(path.read_bytes())


def _verify_binding_v232(
    *,
    repository_root: Path,
    binding: ManifestFileBindingV232,
) -> None:
    path = repository_root / binding.path
    if not path.is_file() or _file_sha256_v232(path) != binding.sha256:
        raise ValueError(f"v2.3.2 frozen binding differs: {binding.path}")


def verify_frozen_surface_v232(
    *,
    repository_root: Path,
    manifest_path: Path,
    expected_provider_model: str,
) -> EvaluationManifestV232:
    manifest = load_evaluation_manifest_v232(manifest_path)
    if manifest.provider_model != expected_provider_model:
        raise ValueError("v2.3.2 Provider model differs from manifest")
    for binding in (
        manifest.history_ledger,
        manifest.history_verifier,
        manifest.cases,
        manifest.truth_index,
        *manifest.truth_shards,
        manifest.ontology_views,
        manifest.strata,
        manifest.admission_matrix,
        manifest.runtime_totality_preflight,
        *manifest.fixed_surface_sources,
        *manifest.frozen_predecessor_sources,
    ):
        _verify_binding_v232(repository_root=repository_root, binding=binding)
    if manifest.strict_system_prompt_sha256 != hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V23.encode("utf-8")
    ).hexdigest():
        raise ValueError("v2.3.2 strict Prompt binding differs")
    if manifest.treatment_system_prompt_sha256 != hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V231.encode("utf-8")
    ).hexdigest():
        raise ValueError("v2.3.2 treatment Prompt binding differs")
    history = json.loads(
        (repository_root / manifest.history_ledger.path).read_text(encoding="utf-8")
    )
    attempts = history.get("blocked_attempts", ())
    if [item.get("terminal") for item in attempts] != [
        "BLOCKED_DTA_V231_EVALUATION_DATA",
        "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE",
    ] or any(item.get("may_continue") or item.get("may_rerun") for item in attempts):
        raise ValueError("v2.3.2 blocked study preservation differs")
    return manifest


def _require_pre_execution_review_v232(
    *,
    review_path: Path,
    manifest_sha256: str,
    admission_matrix_sha256: str,
    runtime_preflight_sha256: str,
) -> str:
    text = review_path.read_text(encoding="utf-8")
    required = (
        "Must Fix:\n0",
        "Claim Accuracy:\nPASS",
        "Final execution count before review: `0`",
        f"Manifest SHA-256: `{manifest_sha256}`",
        f"Admission matrix SHA-256: `{admission_matrix_sha256}`",
        f"Runtime preflight SHA-256: `{runtime_preflight_sha256}`",
    )
    if any(value not in text for value in required):
        raise ValueError("v2.3.2 independent pre-execution review did not pass")
    for ordinal in range(1, 10):
        if f"{ordinal}. PASS" not in text:
            raise ValueError("v2.3.2 independent review question coverage differs")
    return _file_sha256_v232(review_path)


class ProviderSmokeRoleV232(str, Enum):
    STRICT_NOVELTY = "STRICT_NOVELTY"
    TREATMENT_NOVELTY = "TREATMENT_NOVELTY"
    COMPETING_HYPOTHESIS = "COMPETING_HYPOTHESIS"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    IRRECONCILABLE = "IRRECONCILABLE"


class ProviderSmokeRunV232(DtaModelV22):
    case_id: str
    policy: EvaluationPolicyV232
    role: ProviderSmokeRoleV232
    final_disposition: str
    report_present: bool
    competing_report: bool
    encountered_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    provider_calls: int = Field(ge=0, le=3)
    protocol_repairs: int = Field(ge=0, le=2)
    transport_retries: int = Field(ge=0, le=3)
    provider_error_code: str | None
    action_authority_violations: Literal[0]


class ProviderSmokeArtifactV232(DtaModelV22):
    schema_version: Literal["dta-v232.provider-smoke.v1"]
    execution_count: Literal[1]
    case_count: Literal[8]
    arm_run_count: Literal[8]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runs: tuple[ProviderSmokeRunV232, ...] = Field(min_length=8, max_length=8)
    provider_output_parse_failures: Literal[0]
    protocol_failures: Literal[0]
    runner_failures: Literal[0]
    log_error_cluster_successful_paths: int = Field(ge=1)
    action_authority_violations: Literal[0]
    status: Literal["DTA_V232_PROVIDER_SMOKE_PASS"]
    smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_smoke(self) -> "ProviderSmokeArtifactV232":
        if len({item.case_id for item in self.runs}) != 8:
            raise ValueError("v2.3.2 smoke case denominator differs")
        counts = {
            role: sum(item.role is role for item in self.runs)
            for role in ProviderSmokeRoleV232
        }
        if counts != {
            ProviderSmokeRoleV232.STRICT_NOVELTY: 2,
            ProviderSmokeRoleV232.TREATMENT_NOVELTY: 2,
            ProviderSmokeRoleV232.COMPETING_HYPOTHESIS: 1,
            ProviderSmokeRoleV232.REGISTERED_KNOWN: 1,
            ProviderSmokeRoleV232.NO_INCIDENT: 1,
            ProviderSmokeRoleV232.IRRECONCILABLE: 1,
        }:
            raise ValueError("v2.3.2 smoke role composition differs")
        if any(item.provider_error_code is not None for item in self.runs):
            raise ValueError("v2.3.2 smoke contains a Provider failure")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"smoke_sha256"})
        )
        if self.smoke_sha256 != expected:
            raise ValueError("v2.3.2 smoke digest differs")
        return self


_SMOKE_SCHEDULE_V232: tuple[
    tuple[str, EvaluationPolicyV232, ProviderSmokeRoleV232], ...
] = (
    (
        "vx-209",
        EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL,
        ProviderSmokeRoleV232.STRICT_NOVELTY,
    ),
    (
        "vx-210",
        EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL,
        ProviderSmokeRoleV232.STRICT_NOVELTY,
    ),
    (
        "vx-211",
        EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL,
        ProviderSmokeRoleV232.TREATMENT_NOVELTY,
    ),
    (
        "vx-212",
        EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL,
        ProviderSmokeRoleV232.TREATMENT_NOVELTY,
    ),
    (
        "vx-213",
        EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL,
        ProviderSmokeRoleV232.COMPETING_HYPOTHESIS,
    ),
    (
        "vx-215",
        EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL,
        ProviderSmokeRoleV232.REGISTERED_KNOWN,
    ),
    (
        "vx-219",
        EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL,
        ProviderSmokeRoleV232.NO_INCIDENT,
    ),
    (
        "vx-222",
        EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL,
        ProviderSmokeRoleV232.IRRECONCILABLE,
    ),
)


def _load_admission_matrix_v232(path: Path) -> AdmissionMatrixV232:
    return AdmissionMatrixV232.model_validate_json(path.read_bytes())


def _load_runtime_preflight_v232(path: Path) -> RuntimeTotalityPreflightV232:
    return RuntimeTotalityPreflightV232.model_validate_json(path.read_bytes())


def require_provider_gates_v232(
    *,
    repository_root: Path,
    manifest_path: Path,
    independent_review_path: Path,
    expected_provider_model: str,
) -> EvaluationManifestV232:
    """Require all three user-authorized gates before any Provider call."""

    manifest = verify_frozen_surface_v232(
        repository_root=repository_root,
        manifest_path=manifest_path,
        expected_provider_model=expected_provider_model,
    )
    matrix_path = repository_root / manifest.admission_matrix.path
    preflight_path = repository_root / manifest.runtime_totality_preflight.path
    matrix = _load_admission_matrix_v232(matrix_path)
    runtime_preflight = _load_runtime_preflight_v232(preflight_path)
    if matrix.status != "DTA_V232_SUCCESSOR_EVALUATION_DATA_PASS":
        raise ValueError("v2.3.2 evaluation-data gate did not pass")
    if runtime_preflight.status != "DTA_V232_RUNTIME_TOTALITY_PREFLIGHT_PASS":
        raise ValueError("v2.3.2 runtime-totality gate did not pass")
    _require_pre_execution_review_v232(
        review_path=independent_review_path,
        manifest_sha256=_file_sha256_v232(manifest_path),
        admission_matrix_sha256=_file_sha256_v232(matrix_path),
        runtime_preflight_sha256=_file_sha256_v232(preflight_path),
    )
    return manifest


def run_provider_smoke_v232(
    *,
    repository_root: Path,
    cases_path: Path,
    ontology_views_path: Path,
    manifest_path: Path,
    independent_review_path: Path,
    output_path: Path,
    provider_transport: OpenAICompatibleDiscoveryTransportV231,
) -> ProviderSmokeArtifactV232:
    manifest = require_provider_gates_v232(
        repository_root=repository_root,
        manifest_path=manifest_path,
        independent_review_path=independent_review_path,
        expected_provider_model=provider_transport.config.model,
    )
    if (repository_root / manifest.provider_smoke_output).resolve() != (
        output_path.resolve()
    ):
        raise ValueError("v2.3.2 Provider smoke output path differs")
    cases = load_evaluation_cases_v232(cases_path)
    views = load_evaluation_views_v232(ontology_views_path)
    runs: list[ProviderSmokeRunV232] = []
    for case_id, policy, role in _SMOKE_SCHEDULE_V232:
        spec = cases.require(case_id)
        view = views.require(case_id)
        _, deterministic_trace = run_evaluation_policy_with_trace_v232(
            repository_root=repository_root,
            spec=spec,
            view_spec=view,
            policy=policy,
        )
        run = run_evaluation_policy_v232(
            repository_root=repository_root,
            spec=spec,
            view_spec=view,
            policy=policy,
            provider_transport=provider_transport,
        )
        report = run.provisional_report
        competing = False
        if isinstance(run, EvaluationArmRunV231):
            treatment_report = run.provisional_report
            competing = bool(
                treatment_report is not None
                and treatment_report.uncertainty_mode.value
                == "COMPETING_HYPOTHESES"
            )
        if role in {
            ProviderSmokeRoleV232.STRICT_NOVELTY,
            ProviderSmokeRoleV232.TREATMENT_NOVELTY,
            ProviderSmokeRoleV232.COMPETING_HYPOTHESIS,
        } and report is None:
            raise ValueError(f"v2.3.2 smoke report missing: {case_id}")
        if role is ProviderSmokeRoleV232.COMPETING_HYPOTHESIS and not competing:
            raise ValueError("v2.3.2 smoke competing report missing")
        cost = run.provider_cost
        runs.append(
            ProviderSmokeRunV232(
                case_id=case_id,
                policy=policy,
                role=role,
                final_disposition=run.final_disposition,
                report_present=report is not None,
                competing_report=competing,
                encountered_anomaly_kinds=(
                    deterministic_trace.encountered_anomaly_kinds
                ),
                provider_calls=cost.provider_calls,
                protocol_repairs=cost.protocol_repairs,
                transport_retries=cost.transport_retries,
                provider_error_code=run.provider_error_code,
                action_authority_violations=run.action_authority_violations,
            )
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.provider-smoke.v1",
        "execution_count": 1,
        "case_count": 8,
        "arm_run_count": 8,
        "manifest_sha256": _file_sha256_v232(manifest_path),
        "runs": tuple(runs),
        "provider_output_parse_failures": 0,
        "protocol_failures": 0,
        "runner_failures": 0,
        "log_error_cluster_successful_paths": sum(
            GenericAnomalyKindV23.LOG_ERROR_CLUSTER
            in set(item.encountered_anomaly_kinds)
            and item.provider_error_code is None
            for item in runs
        ),
        "action_authority_violations": 0,
        "status": "DTA_V232_PROVIDER_SMOKE_PASS",
    }
    draft = ProviderSmokeArtifactV232.model_construct(
        **payload,
        smoke_sha256="0" * 64,
    )
    artifact = ProviderSmokeArtifactV232.model_validate(
        {
            **payload,
            "smoke_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"smoke_sha256"})
            ),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    return artifact


class EvaluationPreflightV232(DtaModelV22):
    schema_version: Literal["dta-v232.final-evaluation-preflight.v1"]
    case_count: Literal[24]
    planned_runs: Literal[48]
    execution_count_before: Literal[0]
    provider_model: str
    cases_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_shard_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ontology_views_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission_matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    independent_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    new_case_bytes_sha256: tuple[str, ...] = Field(min_length=24, max_length=24)
    admission_status: Literal["DTA_V232_SUCCESSOR_EVALUATION_DATA_PASS"]
    runtime_totality_status: Literal[
        "DTA_V232_RUNTIME_TOTALITY_PREFLIGHT_PASS"
    ]
    provider_smoke_status: Literal["DTA_V232_PROVIDER_SMOKE_PASS"]
    independent_review_status: Literal["MUST_FIX_0_CLAIM_ACCURACY_PASS"]
    predecessor_evaluation_data: Literal["BLOCKED_DTA_V231_EVALUATION_DATA"]
    predecessor_repository_acceptance: Literal[
        "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE"
    ]
    study_relation: Literal["INDEPENDENT_SUCCESSOR_NOT_RERUN"]
    output_path: str
    output_markdown_path: str
    status: Literal["DTA_V232_FINAL_EVALUATION_PREFLIGHT_PASS"]


def _old_case_id_service_and_bytes_v232(
    repository_root: Path,
) -> tuple[set[str], set[str], set[str]]:
    ids: set[str] = set()
    services: set[str] = set()
    source_hashes: set[str] = set()
    for relative in (
        "config/dta-v231/evaluation/cases.json",
        "config/dta-v231-successor/evaluation/cases.json",
    ):
        payload = json.loads((repository_root / relative).read_text(encoding="utf-8"))
        for case in payload["cases"]:
            ids.add(str(case["case_id"]))
            services.update(str(item) for item in case["candidate_services"])
            source_hashes.add(str(case["source_bytes_sha256"]))
    return ids, services, source_hashes


def build_final_evaluation_preflight_v232(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_index_path: Path,
    ontology_views_path: Path,
    manifest_path: Path,
    independent_review_path: Path,
    provider_smoke_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    expected_provider_model: str,
) -> EvaluationPreflightV232:
    manifest = require_provider_gates_v232(
        repository_root=repository_root,
        manifest_path=manifest_path,
        independent_review_path=independent_review_path,
        expected_provider_model=expected_provider_model,
    )
    expected_paths = {
        cases_path.resolve(): manifest.cases,
        truth_index_path.resolve(): manifest.truth_index,
        ontology_views_path.resolve(): manifest.ontology_views,
    }
    for path, binding in expected_paths.items():
        if (repository_root / binding.path).resolve() != path:
            raise ValueError("v2.3.2 fixed input path differs from manifest")
    matrix_path = repository_root / manifest.admission_matrix.path
    runtime_preflight_path = repository_root / manifest.runtime_totality_preflight.path
    matrix = _load_admission_matrix_v232(matrix_path)
    runtime_preflight = _load_runtime_preflight_v232(runtime_preflight_path)
    smoke = ProviderSmokeArtifactV232.model_validate_json(
        provider_smoke_path.read_bytes()
    )
    manifest_sha = _file_sha256_v232(manifest_path)
    if smoke.manifest_sha256 != manifest_sha:
        raise ValueError("v2.3.2 Provider smoke does not bind the manifest")
    if (repository_root / manifest.provider_smoke_output).resolve() != (
        provider_smoke_path.resolve()
    ):
        raise ValueError("v2.3.2 Provider smoke path differs")
    if (repository_root / manifest.output_json).resolve() != output_path.resolve():
        raise ValueError("v2.3.2 JSON output path differs")
    if (repository_root / manifest.output_markdown).resolve() != (
        output_markdown_path.resolve()
    ):
        raise ValueError("v2.3.2 Markdown output path differs")
    cases = load_evaluation_cases_v232(cases_path)
    views = load_evaluation_views_v232(ontology_views_path)
    truth_index = load_evaluation_truth_index_v232(truth_index_path)
    case_ids = tuple(item.case_id for item in cases.cases)
    if case_ids != tuple(item.case_id for item in views.views):
        raise ValueError("v2.3.2 case and view IDs differ")
    if case_ids != tuple(item.case_id for item in truth_index.shards):
        raise ValueError("v2.3.2 case and truth index IDs differ")
    old_ids, old_services, old_hashes = _old_case_id_service_and_bytes_v232(
        repository_root
    )
    new_services = {
        service for spec in cases.cases for service in spec.candidate_services
    }
    new_hashes = tuple(spec.source_bytes_sha256 for spec in cases.cases)
    if old_ids.intersection(case_ids):
        raise ValueError("v2.3.2 reuses a consumed case ID")
    if old_services.intersection(new_services):
        raise ValueError("v2.3.2 reuses a consumed opaque service ID")
    if old_hashes.intersection(new_hashes):
        raise ValueError("v2.3.2 reuses consumed case bytes")
    local_root = repository_root / ".local/dta-v232"
    if any(
        path.exists()
        for path in (
            local_root / "fixed-evaluation.started.json",
            local_root / "fixed-evaluation.partial.jsonl",
            output_path,
            output_markdown_path,
        )
    ):
        raise FileExistsError("v2.3.2 write-once boundary already exists")
    return EvaluationPreflightV232(
        schema_version="dta-v232.final-evaluation-preflight.v1",
        case_count=24,
        planned_runs=48,
        execution_count_before=0,
        provider_model=expected_provider_model,
        cases_sha256=_file_sha256_v232(cases_path),
        truth_index_sha256=_file_sha256_v232(truth_index_path),
        truth_shard_set_sha256=semantic_sha256_v22(
            tuple(item.model_dump(mode="json") for item in manifest.truth_shards)
        ),
        ontology_views_sha256=_file_sha256_v232(ontology_views_path),
        admission_matrix_sha256=_file_sha256_v232(matrix_path),
        runtime_preflight_sha256=_file_sha256_v232(runtime_preflight_path),
        provider_smoke_sha256=_file_sha256_v232(provider_smoke_path),
        manifest_sha256=manifest_sha,
        independent_review_sha256=_file_sha256_v232(independent_review_path),
        new_case_bytes_sha256=new_hashes,
        admission_status=matrix.status,
        runtime_totality_status=runtime_preflight.status,
        provider_smoke_status=smoke.status,
        independent_review_status="MUST_FIX_0_CLAIM_ACCURACY_PASS",
        predecessor_evaluation_data=manifest.predecessor_evaluation_data,
        predecessor_repository_acceptance=(
            manifest.predecessor_repository_acceptance
        ),
        study_relation=manifest.study_relation,
        output_path=str(output_path.relative_to(repository_root)),
        output_markdown_path=str(output_markdown_path.relative_to(repository_root)),
        status="DTA_V232_FINAL_EVALUATION_PREFLIGHT_PASS",
    )


def _build_case_pair_v232(
    *,
    repository_root: Path,
    spec: Any,
    view_spec: Any,
    truth_store: LazyTruthStoreV232,
    provider_transport: OpenAICompatibleDiscoveryTransportV231,
    arm_order: tuple[EvaluationPolicyV232, EvaluationPolicyV232],
) -> EvaluationCasePairV232:
    completed: dict[
        EvaluationPolicyV232,
        EvaluationArmRunV23 | EvaluationArmRunV231,
    ] = {}
    for policy in arm_order:
        completed[policy] = run_evaluation_policy_v232(
            repository_root=repository_root,
            spec=spec,
            view_spec=view_spec,
            policy=policy,
            provider_transport=provider_transport,
        )
    strict = completed[EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL]
    treatment = completed[EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL]
    if not isinstance(strict, EvaluationArmRunV23):
        raise TypeError("v2.3.2 strict arm type differs")
    if not isinstance(treatment, EvaluationArmRunV231):
        raise TypeError("v2.3.2 treatment arm type differs")
    truth = truth_store.load_case_after_both_arms(
        case_id=spec.case_id,
        strict=strict,
        treatment=treatment,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.evaluation-case-pair.v1",
        "case_id": spec.case_id,
        "baseline_policy": (
            EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL
        ),
        "treatment_policy": (
            EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL
        ),
        "arm_order": arm_order,
        "strict": strict,
        "treatment": treatment,
        "evaluator_truth": truth,
    }
    draft = EvaluationCasePairV232.model_construct(
        **payload,
        pair_sha256="0" * 64,
    )
    return EvaluationCasePairV232.model_validate(
        {
            **payload,
            "pair_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"pair_sha256"})
            ),
        }
    )


def _score_terminal_v232(metrics: EvaluationMetricsV231) -> MeasuredResultTerminalV232:
    old = score_measured_terminal_v231(
        baseline_novelty_recall=metrics.baseline_novelty_recall,
        treatment_novelty_recall=metrics.treatment_novelty_recall,
        conflict_prone_novelty_recall=(
            metrics.conflict_prone_treatment_recall
        ),
        root_localization=metrics.treatment_root_localization,
        broad_domain_accuracy=metrics.treatment_broad_domain_accuracy,
        evidence_ref_validity=metrics.treatment_evidence_ref_validity,
        false_novel_rate=metrics.treatment_false_novel_rate,
        known_accuracy_drop_cases=metrics.known_accuracy_drop_cases,
        no_incident_accuracy_drop_cases=metrics.no_incident_accuracy_drop_cases,
        true_conflict_converted_cases=metrics.true_conflict_converted_cases,
        action_authority_violations=metrics.action_authority_violations,
    )
    return {
        MeasuredResultTerminalV231.EFFECT_OBSERVED: (
            MeasuredResultTerminalV232.EFFECT_OBSERVED
        ),
        MeasuredResultTerminalV231.MIXED_RESULT: (
            MeasuredResultTerminalV232.MIXED_RESULT
        ),
        MeasuredResultTerminalV231.NOT_OBSERVED: (
            MeasuredResultTerminalV232.NOT_OBSERVED
        ),
    }[old]


class FixedEvaluationArtifactV232(DtaModelV22):
    schema_version: Literal["dta-v232.fixed-evaluation.v1"]
    execution_count: Literal[1]
    case_count: Literal[24]
    run_count: Literal[48]
    baseline_policy: Literal[
        EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL
    ]
    treatment_policy: Literal[
        EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL
    ]
    predecessor_evaluation_data: Literal["BLOCKED_DTA_V231_EVALUATION_DATA"]
    predecessor_repository_acceptance: Literal[
        "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE"
    ]
    study_relation: Literal["INDEPENDENT_SUCCESSOR_NOT_RERUN"]
    provider_model: str
    preflight: EvaluationPreflightV232
    pairs: tuple[EvaluationCasePairV232, ...] = Field(min_length=24, max_length=24)
    metrics: EvaluationMetricsV231
    runtime_exceptions: Literal[0]
    unmapped_anomaly_count: Literal[0]
    measured_result_terminal: MeasuredResultTerminalV232
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    docker_calls: Literal[0]
    new_live_faults: Literal[0]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_artifact(self) -> "FixedEvaluationArtifactV232":
        ids = tuple(item.case_id for item in self.pairs)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(201, 225)):
            raise ValueError("v2.3.2 fixed pair denominator differs")
        if self.provider_model != self.preflight.provider_model:
            raise ValueError("v2.3.2 Provider binding differs")
        expected_orders = tuple(
            counterbalanced_arm_order_v232(index) for index in range(24)
        )
        if tuple(item.arm_order for item in self.pairs) != expected_orders:
            raise ValueError("v2.3.2 arm schedule is not counterbalanced")
        if self.measured_result_terminal is not _score_terminal_v232(self.metrics):
            raise ValueError("v2.3.2 measured terminal differs")
        if self.metrics.action_authority_violations != 0:
            raise ValueError("v2.3.2 action authority was violated")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("v2.3.2 artifact digest differs")
        return self


def _build_artifact_v232(
    *,
    preflight: EvaluationPreflightV232,
    pairs: tuple[EvaluationCasePairV232, ...],
) -> FixedEvaluationArtifactV232:
    metrics = score_evaluation_pairs_v231(
        cast(tuple[EvaluationCasePairV231, ...], pairs)
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.fixed-evaluation.v1",
        "execution_count": 1,
        "case_count": 24,
        "run_count": 48,
        "baseline_policy": EvaluationPolicyV232.V23_STRICT_CONFLICT_GATE_TOTAL,
        "treatment_policy": EvaluationPolicyV232.V231_CONFLICT_AWARE_GATE_TOTAL,
        "predecessor_evaluation_data": preflight.predecessor_evaluation_data,
        "predecessor_repository_acceptance": (
            preflight.predecessor_repository_acceptance
        ),
        "study_relation": preflight.study_relation,
        "provider_model": preflight.provider_model,
        "preflight": preflight,
        "pairs": pairs,
        "metrics": metrics,
        "runtime_exceptions": 0,
        "unmapped_anomaly_count": 0,
        "measured_result_terminal": _score_terminal_v232(metrics),
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
    }
    draft = FixedEvaluationArtifactV232.model_construct(
        **payload,
        artifact_sha256="0" * 64,
    )
    return FixedEvaluationArtifactV232.model_validate(
        {
            **payload,
            "artifact_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"artifact_sha256"})
            ),
        }
    )


def render_evaluation_markdown_v232(artifact: FixedEvaluationArtifactV232) -> str:
    metrics = artifact.metrics
    return "\n".join(
        (
            "# DTA v2.3.2 Conflict-Aware Successor Evaluation",
            "",
            f"Measured terminal: `{artifact.measured_result_terminal.value}`",
            "",
            f"- Study relation: `{artifact.study_relation}`",
            f"- Preserved Attempt A: `{artifact.predecessor_evaluation_data}`",
            (
                "- Preserved Attempt B: "
                f"`{artifact.predecessor_repository_acceptance}`"
            ),
            f"- Execution count: `{artifact.execution_count}`",
            f"- Cases / runs: `{artifact.case_count}` / `{artifact.run_count}`",
            f"- Baseline novelty recall: `{metrics.baseline_novelty_recall:.3f}`",
            f"- Treatment novelty recall: `{metrics.treatment_novelty_recall:.3f}`",
            f"- Recall improvement: `{metrics.novelty_recall_improvement:.3f}`",
            (
                "- Conflict-prone recall (baseline / treatment): "
                f"`{metrics.conflict_prone_baseline_recall:.3f}` / "
                f"`{metrics.conflict_prone_treatment_recall:.3f}`"
            ),
            (
                "- Non-conflict recall (baseline / treatment): "
                f"`{metrics.non_conflict_baseline_recall:.3f}` / "
                f"`{metrics.non_conflict_treatment_recall:.3f}`"
            ),
            f"- Root localization: `{metrics.treatment_root_localization:.3f}`",
            (
                "- Broad-domain accuracy: "
                f"`{metrics.treatment_broad_domain_accuracy:.3f}`"
            ),
            (
                "- Evidence-ref validity: "
                f"`{metrics.treatment_evidence_ref_validity:.3f}`"
            ),
            (
                "- Alternative-hypothesis completeness: "
                f"`{metrics.alternative_hypothesis_completeness:.3f}`"
            ),
            (
                "- Registered-known accuracy (baseline / treatment): "
                f"`{metrics.registered_known_baseline_accuracy:.3f}` / "
                f"`{metrics.registered_known_treatment_accuracy:.3f}`"
            ),
            (
                "- No-Incident accuracy (baseline / treatment): "
                f"`{metrics.no_incident_baseline_accuracy:.3f}` / "
                f"`{metrics.no_incident_treatment_accuracy:.3f}`"
            ),
            (
                "- Irreconcilable-control accuracy (baseline / treatment): "
                f"`{metrics.insufficient_conflict_baseline_accuracy:.3f}` / "
                f"`{metrics.insufficient_conflict_treatment_accuracy:.3f}`"
            ),
            f"- False-novel rate: `{metrics.treatment_false_novel_rate:.3f}`",
            (
                "- Mean discovery reads (baseline / treatment): "
                f"`{metrics.mean_baseline_discovery_reads:.3f}` / "
                f"`{metrics.mean_treatment_discovery_reads:.3f}`"
            ),
            (
                "- Provider calls (baseline / treatment): "
                f"`{metrics.baseline_provider_calls}` / "
                f"`{metrics.treatment_provider_calls}`"
            ),
            f"- Total tokens: `{metrics.total_tokens}`",
            f"- Provider latency ms: `{metrics.provider_latency_ms:.3f}`",
            (
                "- Protocol repairs / transport retries: "
                f"`{metrics.protocol_repairs}` / `{metrics.transport_retries}`"
            ),
            f"- Runtime exceptions: `{artifact.runtime_exceptions}`",
            f"- Unmapped anomaly count: `{artifact.unmapped_anomaly_count}`",
            (
                "- Action-authority violations: "
                f"`{metrics.action_authority_violations}`"
            ),
            "",
            "This is a new fixed successor study. Neither consumed v2.3.1 attempt was continued or rerun.",
            "",
        )
    )


def run_fixed_evaluation_once_v232(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_index_path: Path,
    ontology_views_path: Path,
    manifest_path: Path,
    independent_review_path: Path,
    provider_smoke_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    provider_transport: OpenAICompatibleDiscoveryTransportV231,
    observer: Callable[[EvaluationCasePairV232], None] | None = None,
) -> FixedEvaluationArtifactV232:
    preflight = build_final_evaluation_preflight_v232(
        repository_root=repository_root,
        cases_path=cases_path,
        truth_index_path=truth_index_path,
        ontology_views_path=ontology_views_path,
        manifest_path=manifest_path,
        independent_review_path=independent_review_path,
        provider_smoke_path=provider_smoke_path,
        output_path=output_path,
        output_markdown_path=output_markdown_path,
        expected_provider_model=provider_transport.config.model,
    )
    local_root = repository_root / ".local/dta-v232"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "fixed-evaluation.started.json"
    partial = local_root / "fixed-evaluation.partial.jsonl"
    with sentinel.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "status": "STARTED",
                    "planned_execution_count": 1,
                    "planned_case_count": 24,
                    "planned_run_count": 48,
                    "study_relation": preflight.study_relation,
                    "cases_sha256": preflight.cases_sha256,
                    "truth_index_sha256": preflight.truth_index_sha256,
                    "truth_shard_set_sha256": preflight.truth_shard_set_sha256,
                    "admission_matrix_sha256": preflight.admission_matrix_sha256,
                    "runtime_preflight_sha256": preflight.runtime_preflight_sha256,
                    "provider_smoke_sha256": preflight.provider_smoke_sha256,
                    "manifest_sha256": preflight.manifest_sha256,
                    "independent_review_sha256": (
                        preflight.independent_review_sha256
                    ),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    cases = load_evaluation_cases_v232(cases_path)
    views = load_evaluation_views_v232(ontology_views_path)
    truth_store = LazyTruthStoreV232(truth_index_path)
    pairs: list[EvaluationCasePairV232] = []
    with partial.open("x", encoding="utf-8") as handle:
        for index, spec in enumerate(cases.cases):
            pair = _build_case_pair_v232(
                repository_root=repository_root,
                spec=spec,
                view_spec=views.require(spec.case_id),
                truth_store=truth_store,
                provider_transport=provider_transport,
                arm_order=counterbalanced_arm_order_v232(index),
            )
            pairs.append(pair)
            handle.write(pair.model_dump_json() + "\n")
            handle.flush()
            if observer is not None:
                observer(pair)
    expected_ids = tuple(item.case_id for item in cases.cases)
    if truth_store.loaded_case_ids != expected_ids:
        raise ValueError("v2.3.2 truth coverage differs after both arms")
    artifact = _build_artifact_v232(preflight=preflight, pairs=tuple(pairs))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    with output_markdown_path.open("x", encoding="utf-8") as handle:
        handle.write(render_evaluation_markdown_v232(artifact))
    sentinel.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "execution_count": 1,
                "study_relation": artifact.study_relation,
                "artifact_sha256": artifact.artifact_sha256,
                "output_json_sha256": _file_sha256_v232(output_path),
                "output_markdown_sha256": _file_sha256_v232(
                    output_markdown_path
                ),
                "measured_result_terminal": (
                    artifact.measured_result_terminal.value
                ),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return artifact


__all__ = (
    "EvaluationManifestV232",
    "EvaluationPreflightV232",
    "FixedEvaluationArtifactV232",
    "LazyTruthStoreV232",
    "MeasuredResultTerminalV232",
    "ProviderSmokeArtifactV232",
    "build_final_evaluation_preflight_v232",
    "counterbalanced_arm_order_v232",
    "load_evaluation_manifest_v232",
    "render_evaluation_markdown_v232",
    "require_provider_gates_v232",
    "run_fixed_evaluation_once_v232",
    "run_provider_smoke_v232",
    "verify_frozen_surface_v232",
)
