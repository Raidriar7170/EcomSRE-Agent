"""Frozen smoke, scoring, and write-once three-arm study for DTA v2.3.3."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.contracts_v231 import ReportUncertaintyModeV231
from ecomsre.dta_v2.v23.discovery_provider_v233 import (
    DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233,
    OpenAICompatibleDiscoveryTransportV233,
)
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    EvaluationClassV233,
    EvaluationTruthV233,
    load_admission_matrix_v233,
    load_evaluation_cases_v233,
    load_evaluation_views_v233,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    materialize_evaluation_case_v231,
)
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
from ecomsre.dta_v2.v23.runtime_preflight_v233 import RuntimePreflightV233


class MeasuredResultTerminalV233(str, Enum):
    EFFECT_OBSERVED = "DTA_V233_DOMAIN_AND_GUARD_EFFECT_OBSERVED"
    MIXED_RESULT = "DTA_V233_DOMAIN_AND_GUARD_MIXED_RESULT"
    NOT_OBSERVED = "DTA_V233_DOMAIN_AND_GUARD_NOT_OBSERVED"


class ManifestFileBindingV233(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationManifestV233(DtaModelV22):
    schema_version: Literal["dta-v233.evaluation-manifest.v1"]
    base_commit: Literal["447e7a8ed4c8b9d592c16d181f5709bdfdc3d4cb"]
    branch: Literal["codex/dta-v233-domain-witness-guard"]
    provider_model: str
    planned_case_count: Literal[28]
    planned_run_count: Literal[84]
    planned_execution_count: Literal[1]
    policies: tuple[EvaluationPolicyV233, EvaluationPolicyV233, EvaluationPolicyV233]
    frozen_files: tuple[ManifestFileBindingV233, ...] = Field(min_length=12)
    provider_system_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_output: str
    output_json: str
    output_markdown: str
    independent_review: str
    fixed_at_utc: datetime

    @model_validator(mode="after")
    def require_manifest(self) -> "EvaluationManifestV233":
        if self.policies != tuple(EvaluationPolicyV233):
            raise ValueError("v2.3.3 manifest policy order differs")
        paths = tuple(item.path for item in self.frozen_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("v2.3.3 manifest bindings are not canonical")
        if (
            self.fixed_at_utc.tzinfo is None
            or self.fixed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.3 manifest timestamp is not UTC")
        return self


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_evaluation_manifest_v233(path: Path) -> EvaluationManifestV233:
    return EvaluationManifestV233.model_validate_json(path.read_bytes())


def verify_frozen_surface_v233(
    *,
    repository_root: Path,
    manifest_path: Path,
    expected_provider_model: str,
) -> EvaluationManifestV233:
    manifest = load_evaluation_manifest_v233(manifest_path)
    if manifest.provider_model != expected_provider_model:
        raise ValueError("v2.3.3 Provider model differs from manifest")
    for binding in manifest.frozen_files:
        path = repository_root / binding.path
        if not path.is_file() or _file_sha256(path) != binding.sha256:
            raise ValueError(f"v2.3.3 frozen binding differs: {binding.path}")
    by_path = {item.path: item for item in manifest.frozen_files}
    history_path = repository_root / "config/dta-v233/historical-results.v1.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    historical_bindings = history.get("bindings", ())
    if not isinstance(historical_bindings, list):
        raise ValueError("v2.3.3 historical binding set is invalid")
    for historical in historical_bindings:
        if not isinstance(historical, dict):
            raise ValueError("v2.3.3 historical binding is invalid")
        relative = str(historical.get("path", ""))
        path = repository_root / relative
        manifest_binding = by_path.get(relative)
        if (
            manifest_binding is None
            or not path.is_file()
            or path.stat().st_size != historical.get("size_bytes")
            or _file_sha256(path) != historical.get("sha256")
            or manifest_binding.sha256 != historical.get("sha256")
        ):
            raise ValueError(f"v2.3.3 historical byte differs: {relative}")
    prompt_sha = hashlib.sha256(
        DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233.encode("utf-8")
    ).hexdigest()
    if manifest.provider_system_prompt_sha256 != prompt_sha:
        raise ValueError("v2.3.3 Provider Prompt binding differs")
    return manifest


def _require_pre_execution_review(
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
    if any(item not in text for item in required):
        raise ValueError("v2.3.3 independent pre-execution review did not pass")
    for ordinal in range(1, 10):
        if f"{ordinal}. PASS" not in text:
            raise ValueError("v2.3.3 independent review question coverage differs")
    return _file_sha256(review_path)


def require_provider_gates_v233(
    *,
    repository_root: Path,
    manifest_path: Path,
    independent_review_path: Path,
    expected_provider_model: str,
) -> EvaluationManifestV233:
    manifest = verify_frozen_surface_v233(
        repository_root=repository_root,
        manifest_path=manifest_path,
        expected_provider_model=expected_provider_model,
    )
    bindings = {item.path: item for item in manifest.frozen_files}
    admission_path = repository_root / "config/dta-v233/evaluation/admission-matrix.json"
    preflight_path = repository_root / "docs/analysis/dta-v233-runtime-preflight.json"
    admission = load_admission_matrix_v233(admission_path)
    preflight = RuntimePreflightV233.model_validate_json(preflight_path.read_bytes())
    if admission.terminal != "DTA_V233_EVALUATION_DATA_PASS":
        raise ValueError("v2.3.3 evaluation-data gate did not pass")
    if preflight.terminal != "DTA_V233_RUNTIME_PREFLIGHT_PASS":
        raise ValueError("v2.3.3 runtime preflight gate did not pass")
    for path in (admission_path, preflight_path):
        relative = str(path.relative_to(repository_root))
        if relative not in bindings or bindings[relative].sha256 != _file_sha256(path):
            raise ValueError(f"v2.3.3 gate is not frozen: {relative}")
    _require_pre_execution_review(
        review_path=independent_review_path,
        manifest_sha256=_file_sha256(manifest_path),
        admission_matrix_sha256=_file_sha256(admission_path),
        runtime_preflight_sha256=_file_sha256(preflight_path),
    )
    return manifest


class ProviderSmokeRoleV233(str, Enum):
    SINGLE_LEADING = "SINGLE_LEADING"
    COMPETING = "COMPETING"
    DOMAIN_AMBIGUOUS = "DOMAIN_AMBIGUOUS"
    IRRECONCILABLE = "IRRECONCILABLE"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT = "INSUFFICIENT"
    REPAIR_PATH = "REPAIR_PATH"


class ProviderSmokeRunV233(DtaModelV22):
    case_id: str
    role: ProviderSmokeRoleV233
    final_disposition: str
    report_present: bool
    uncertainty_mode: ReportUncertaintyModeV231 | None
    projection_status: str | None
    provider_calls: int = Field(ge=0)
    protocol_repairs: int = Field(ge=0, le=2)
    transport_retries: int = Field(ge=0, le=3)
    provider_error_code: str | None
    root_domain_evidence_drift: Literal[0]
    action_authority_violations: Literal[0]


class ProviderSmokeRepairRecordV233(DtaModelV22):
    schema_version: Literal["dta-v233.provider-smoke-repair.v1"]
    execution_count: Literal[1]
    repair_ordinal: Literal[1, 2]
    failure_case_id: Literal["vx-303"]
    prior_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_sentinel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    safe_error_code: Literal["REPORT_MISSING_AFTER_PROVIDER"]
    provider_calls_before_repair: None
    fix_code: Literal[
        "V233_MINIMAL_TOOL_SCHEMA_BINDING",
        "V233_PROVIDER_LIST_CANONICALIZATION",
    ]
    fix_files: tuple[str, ...] = Field(min_length=1)
    prior_repair_record_sha256: str | None = None
    superseded_manifest_sha256: str | None = None
    diagnostic_file_sha256: str | None = None
    final_execution_count_before_resume: Literal[0]
    recorded_at_utc: datetime
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_record(self) -> "ProviderSmokeRepairRecordV233":
        if self.fix_files != tuple(sorted(set(self.fix_files))):
            raise ValueError("v2.3.3 smoke repair files are not canonical")
        chain = (
            self.prior_repair_record_sha256,
            self.superseded_manifest_sha256,
            self.diagnostic_file_sha256,
        )
        if self.repair_ordinal == 1:
            if any(item is not None for item in chain):
                raise ValueError("v2.3.3 repair-1 gained a predecessor chain")
        elif any(
            item is None or re.fullmatch(r"[0-9a-f]{64}", item) is None
            for item in chain
        ):
            raise ValueError("v2.3.3 repair-2 predecessor chain differs")
        if (
            self.recorded_at_utc.tzinfo is None
            or self.recorded_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.3 smoke repair timestamp is not UTC")
        digest_exclusions = {"record_sha256"}
        if self.repair_ordinal == 1:
            digest_exclusions.update(
                {
                    "prior_repair_record_sha256",
                    "superseded_manifest_sha256",
                    "diagnostic_file_sha256",
                }
            )
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude=digest_exclusions)
        )
        if self.record_sha256 != expected:
            raise ValueError("v2.3.3 smoke repair digest differs")
        return self


class ProviderSmokeArtifactV233(DtaModelV22):
    schema_version: Literal["dta-v233.provider-smoke.v1"]
    execution_count: Literal[1]
    case_count: Literal[12]
    arm_run_count: Literal[12]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runs: tuple[ProviderSmokeRunV233, ...]
    provider_output_parse_failures: Literal[0]
    protocol_failures: Literal[0]
    root_domain_evidence_drift: Literal[0]
    irreconcilable_provider_calls: Literal[0]
    action_authority_violations: Literal[0]
    real_fixes: int = Field(default=0, ge=0, le=2)
    repair_record_sha256: str | None = None
    status: Literal["DTA_V233_PROVIDER_SMOKE_PASS"]
    smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_smoke(self) -> "ProviderSmokeArtifactV233":
        expected = {
            ProviderSmokeRoleV233.SINGLE_LEADING: 2,
            ProviderSmokeRoleV233.COMPETING: 2,
            ProviderSmokeRoleV233.DOMAIN_AMBIGUOUS: 2,
            ProviderSmokeRoleV233.IRRECONCILABLE: 2,
            ProviderSmokeRoleV233.REGISTERED_KNOWN: 1,
            ProviderSmokeRoleV233.NO_INCIDENT: 1,
            ProviderSmokeRoleV233.INSUFFICIENT: 1,
            ProviderSmokeRoleV233.REPAIR_PATH: 1,
        }
        actual = {
            role: sum(item.role is role for item in self.runs)
            for role in ProviderSmokeRoleV233
        }
        if len(self.runs) != 12 or len({item.case_id for item in self.runs}) != 12:
            raise ValueError("v2.3.3 smoke denominator differs")
        if actual != expected:
            raise ValueError("v2.3.3 smoke role composition differs")
        if any(item.provider_error_code is not None for item in self.runs):
            raise ValueError("v2.3.3 smoke contains a Provider failure")
        repair = next(
            item for item in self.runs if item.role is ProviderSmokeRoleV233.REPAIR_PATH
        )
        if repair.protocol_repairs < 1:
            raise ValueError("v2.3.3 smoke did not exercise protocol repair")
        if (self.real_fixes == 0) != (self.repair_record_sha256 is None):
            raise ValueError("v2.3.3 smoke repair binding differs")
        if self.repair_record_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.repair_record_sha256
        ):
            raise ValueError("v2.3.3 smoke repair digest is invalid")
        digest = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"smoke_sha256"})
        )
        if self.smoke_sha256 != digest:
            raise ValueError("v2.3.3 smoke digest differs")
        return self


_SMOKE_SCHEDULE_V233: tuple[tuple[str, ProviderSmokeRoleV233], ...] = (
    ("vx-303", ProviderSmokeRoleV233.SINGLE_LEADING),
    ("vx-305", ProviderSmokeRoleV233.SINGLE_LEADING),
    ("vx-301", ProviderSmokeRoleV233.COMPETING),
    ("vx-307", ProviderSmokeRoleV233.COMPETING),
    ("vx-311", ProviderSmokeRoleV233.DOMAIN_AMBIGUOUS),
    ("vx-315", ProviderSmokeRoleV233.DOMAIN_AMBIGUOUS),
    ("vx-324", ProviderSmokeRoleV233.IRRECONCILABLE),
    ("vx-325", ProviderSmokeRoleV233.IRRECONCILABLE),
    ("vx-317", ProviderSmokeRoleV233.REGISTERED_KNOWN),
    ("vx-321", ProviderSmokeRoleV233.NO_INCIDENT),
    ("vx-328", ProviderSmokeRoleV233.INSUFFICIENT),
    ("vx-304", ProviderSmokeRoleV233.REPAIR_PATH),
)


class _ForcedRepairTransportV233:
    def __init__(self, transport: OpenAICompatibleDiscoveryTransportV233) -> None:
        self._transport = transport
        self._forced = False

    def __call__(self, body: str) -> str:
        raw = self._transport(body)
        if not self._forced:
            self._forced = True
            return "{}"
        return raw

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)


def run_provider_smoke_v233(
    *,
    repository_root: Path,
    evaluation_root: Path,
    manifest_path: Path,
    independent_review_path: Path,
    output_path: Path,
    provider_transport: OpenAICompatibleDiscoveryTransportV233,
    repair_record_path: Path | None = None,
    resume_after_fix: int = 0,
) -> ProviderSmokeArtifactV233:
    manifest = require_provider_gates_v233(
        repository_root=repository_root,
        manifest_path=manifest_path,
        independent_review_path=independent_review_path,
        expected_provider_model=provider_transport.config.model,
    )
    if (repository_root / manifest.provider_smoke_output).resolve() != output_path.resolve():
        raise ValueError("v2.3.3 smoke output path differs from manifest")
    local_root = repository_root / ".local/dta-v233"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "provider-smoke.started.json"
    repair_record: ProviderSmokeRepairRecordV233 | None = None
    if sentinel.exists():
        started = json.loads(sentinel.read_text(encoding="utf-8"))
        if output_path.exists():
            raise FileExistsError("v2.3.3 Provider smoke output already exists")
        if resume_after_fix not in {1, 2} or repair_record_path is None:
            raise FileExistsError("v2.3.3 Provider smoke was already started")
        repair_record = ProviderSmokeRepairRecordV233.model_validate_json(
            repair_record_path.read_bytes()
        )
        if (
            started.get("status") != "STARTED"
            or started.get("execution_count") != 1
            or repair_record.repair_ordinal != resume_after_fix
            or started.get("manifest_sha256")
            != repair_record.prior_manifest_sha256
            or _file_sha256(sentinel) != repair_record.prior_sentinel_sha256
            or repair_record.prior_manifest_sha256 == _file_sha256(manifest_path)
        ):
            raise ValueError("v2.3.3 smoke repair resume binding differs")
        if resume_after_fix == 2:
            prior_repair = ProviderSmokeRepairRecordV233.model_validate_json(
                (
                    repository_root
                    / "docs/analysis/dta-v233-provider-smoke-repair-1.json"
                ).read_bytes()
            )
            superseded = (
                repository_root
                / "docs/analysis/dta-v233-manifest-provider-smoke-fix2-superseded.json"
            )
            diagnostic = (
                repository_root
                / "docs/analysis/dta-v233-provider-smoke-repair-2-diagnostic.json"
            )
            if (
                repair_record.prior_repair_record_sha256
                != prior_repair.record_sha256
                or repair_record.superseded_manifest_sha256
                != _file_sha256(superseded)
                or repair_record.diagnostic_file_sha256
                != _file_sha256(diagnostic)
            ):
                raise ValueError("v2.3.3 smoke repair-2 chain differs")
    else:
        if resume_after_fix != 0 or repair_record_path is not None:
            raise ValueError("v2.3.3 smoke repair cannot start a fresh campaign")
        sentinel.write_text(
            json.dumps(
                {
                    "status": "STARTED",
                    "execution_count": 1,
                    "case_count": 12,
                    "manifest_sha256": _file_sha256(manifest_path),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    cases = load_evaluation_cases_v233(evaluation_root / "cases.json")
    views = load_evaluation_views_v233(evaluation_root / "ontology-views.json")
    runs: list[ProviderSmokeRunV233] = []
    for case_id, role in _SMOKE_SCHEDULE_V233:
        spec = cases.require(case_id)
        view = views.require(case_id)
        context = _build_common_context_v23(
            case=materialize_evaluation_case_v231(
                repository_root=repository_root,
                spec=spec,
            ),
            hidden_mechanism=view.hidden_mechanism,
        )
        transport: Callable[[str], str] = provider_transport
        if role is ProviderSmokeRoleV233.REPAIR_PATH:
            transport = _ForcedRepairTransportV233(provider_transport)
        run = run_combined_arm_v233(
            repository_root=repository_root,
            context=context,
            provider_transport=transport,
        )
        report = run.provisional_report
        if role in {
            ProviderSmokeRoleV233.SINGLE_LEADING,
            ProviderSmokeRoleV233.COMPETING,
            ProviderSmokeRoleV233.DOMAIN_AMBIGUOUS,
            ProviderSmokeRoleV233.REPAIR_PATH,
        } and report is None:
            raise ValueError(f"v2.3.3 smoke report missing: {case_id}")
        if role is ProviderSmokeRoleV233.SINGLE_LEADING and (
            report is None
            or report.uncertainty_mode
            is not ReportUncertaintyModeV231.SINGLE_LEADING_HYPOTHESIS
        ):
            raise ValueError(f"v2.3.3 single-leading smoke differs: {case_id}")
        if role is ProviderSmokeRoleV233.COMPETING and (
            report is None
            or report.uncertainty_mode
            is not ReportUncertaintyModeV231.COMPETING_HYPOTHESES
        ):
            raise ValueError(f"v2.3.3 competing smoke differs: {case_id}")
        if role is ProviderSmokeRoleV233.DOMAIN_AMBIGUOUS and (
            report is None
            or run.domain_projection is None
            or run.domain_projection.status.value != "AMBIGUOUS"
        ):
            raise ValueError(f"v2.3.3 ambiguous smoke differs: {case_id}")
        drift = int(
            report is not None
            and (
                report.runtime_selected_root_service != run.runtime_root_service
                or report.broad_fault_domain is not run.runtime_broad_domain
                or report.supporting_evidence_refs != run.supporting_evidence_refs
            )
        )
        if drift:
            raise ValueError(f"v2.3.3 smoke mechanical-field drift: {case_id}")
        runs.append(
            ProviderSmokeRunV233(
                case_id=case_id,
                role=role,
                final_disposition=run.final_disposition,
                report_present=report is not None,
                uncertainty_mode=None if report is None else report.uncertainty_mode,
                projection_status=(
                    None
                    if run.domain_projection is None
                    else run.domain_projection.status.value
                ),
                provider_calls=run.provider_cost.provider_calls,
                protocol_repairs=run.provider_cost.protocol_repairs,
                transport_retries=run.provider_cost.transport_retries,
                provider_error_code=run.provider_error_code,
                root_domain_evidence_drift=0,
                action_authority_violations=run.action_authority_violations,
            )
        )
    conflict_calls = sum(
        item.provider_calls
        for item in runs
        if item.role is ProviderSmokeRoleV233.IRRECONCILABLE
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.provider-smoke.v1",
        "execution_count": 1,
        "case_count": 12,
        "arm_run_count": 12,
        "manifest_sha256": _file_sha256(manifest_path),
        "runs": tuple(runs),
        "provider_output_parse_failures": 0,
        "protocol_failures": 0,
        "root_domain_evidence_drift": sum(
            item.root_domain_evidence_drift for item in runs
        ),
        "irreconcilable_provider_calls": conflict_calls,
        "action_authority_violations": sum(
            item.action_authority_violations for item in runs
        ),
        "real_fixes": resume_after_fix,
        "repair_record_sha256": (
            None if repair_record is None else repair_record.record_sha256
        ),
        "status": "DTA_V233_PROVIDER_SMOKE_PASS",
    }
    draft = ProviderSmokeArtifactV233.model_construct(
        **payload,
        smoke_sha256="0" * 64,
    )
    artifact = ProviderSmokeArtifactV233.model_validate(
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
    sentinel.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "execution_count": 1,
                "real_fixes": resume_after_fix,
                "smoke_sha256": artifact.smoke_sha256,
                "output_sha256": _file_sha256(output_path),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return artifact


def counterbalanced_arm_order_v233(
    case_index: int,
) -> tuple[EvaluationPolicyV233, EvaluationPolicyV233, EvaluationPolicyV233]:
    policies = tuple(EvaluationPolicyV233)
    offset = case_index % len(policies)
    schedule = policies[offset:] + policies[:offset]
    return (schedule[0], schedule[1], schedule[2])


class EvaluationCaseComparisonV233(DtaModelV22):
    schema_version: Literal["dta-v233.evaluation-case-comparison.v1"]
    case_id: str
    arm_order: tuple[EvaluationPolicyV233, EvaluationPolicyV233, EvaluationPolicyV233]
    runs: tuple[EvaluationArmRunV233, EvaluationArmRunV233, EvaluationArmRunV233]
    evaluator_truth: EvaluationTruthV233
    comparison_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_comparison(self) -> "EvaluationCaseComparisonV233":
        if self.arm_order != tuple(item.policy for item in self.runs):
            raise ValueError("v2.3.3 comparison arm order differs")
        if set(self.arm_order) != set(EvaluationPolicyV233):
            raise ValueError("v2.3.3 comparison lacks an exact arm")
        if {self.case_id, self.evaluator_truth.case_id, *(item.case_id for item in self.runs)} != {self.case_id}:
            raise ValueError("v2.3.3 comparison case IDs differ")
        for field in (
            "case_bytes_sha256",
            "active_view_sha256",
            "bootstrap_memory_sha256",
            "common_memory_sha256",
            "common_read_count",
        ):
            if len({getattr(item, field) for item in self.runs}) != 1:
                raise ValueError(f"v2.3.3 comparison common input differs: {field}")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"comparison_sha256"})
        )
        if self.comparison_sha256 != expected:
            raise ValueError("v2.3.3 comparison digest differs")
        return self

    def require(self, policy: EvaluationPolicyV233) -> EvaluationArmRunV233:
        return next(item for item in self.runs if item.policy is policy)


class DomainConfusionEntryV233(DtaModelV22):
    expected: ProvisionalFaultDomainV23
    predicted: ProvisionalFaultDomainV23 | None
    count: int = Field(ge=1)


class ArmMetricsV233(DtaModelV22):
    policy: EvaluationPolicyV233
    novelty_recall: float = Field(ge=0.0, le=1.0)
    root_localization: float = Field(ge=0.0, le=1.0)
    broad_domain_accuracy: float = Field(ge=0.0, le=1.0)
    top2_domain_recall: float = Field(ge=0.0, le=1.0)
    projection_resolved: int = Field(ge=0)
    projection_ambiguous: int = Field(ge=0)
    projection_unsupported: int = Field(ge=0)
    mean_score_margin: float = Field(ge=0.0)
    domain_confusion: tuple[DomainConfusionEntryV233, ...]
    provider_domain_drift: int = Field(ge=0)
    irreconcilable_control_accuracy: float = Field(ge=0.0, le=1.0)
    strong_witness_precision: float = Field(ge=0.0, le=1.0)
    strong_witness_recall: float = Field(ge=0.0, le=1.0)
    novelty_cases_blocked_by_guard: int = Field(ge=0)
    resolvable_witness_read_rate: float = Field(ge=0.0, le=1.0)
    post_read_witness_closure_rate: float = Field(ge=0.0, le=1.0)
    hard_conflict_rate: float = Field(ge=0.0, le=1.0)
    evidence_ref_validity: float = Field(ge=0.0, le=1.0)
    alternative_completeness: float = Field(ge=0.0, le=1.0)
    unresolved_question_completeness: float = Field(ge=0.0, le=1.0)
    false_novel_rate: float = Field(ge=0.0, le=1.0)
    known_world_accuracy: float = Field(ge=0.0, le=1.0)
    no_incident_accuracy: float = Field(ge=0.0, le=1.0)
    mean_discovery_reads: float = Field(ge=0.0)
    provider_calls: int = Field(ge=0)
    protocol_repairs: int = Field(ge=0)
    protocol_failures: int = Field(ge=0)
    transport_retries: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    runtime_exceptions: Literal[0]
    action_authority_violations: Literal[0]


class StudyMetricsV233(DtaModelV22):
    arms: tuple[ArmMetricsV233, ArmMetricsV233, ArmMetricsV233]
    registered_known_accuracy_drop_cases: int = Field(ge=0)
    no_incident_accuracy_drop_cases: int = Field(ge=0)

    def require(self, policy: EvaluationPolicyV233) -> ArmMetricsV233:
        return next(item for item in self.arms if item.policy is policy)


_REPORTABLE = {
    "UNREGISTERED_INCIDENT_SUSPECTED",
    "UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES",
    "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
}


def _rate(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _score_arm(
    *,
    policy: EvaluationPolicyV233,
    comparisons: tuple[EvaluationCaseComparisonV233, ...],
) -> ArmMetricsV233:
    items = tuple((item.require(policy), item.evaluator_truth) for item in comparisons)
    novelty = tuple(item for item in items if item[1].evaluation_class is EvaluationClassV233.NOVELTY)
    known = tuple(item for item in items if item[1].evaluation_class is EvaluationClassV233.REGISTERED_KNOWN)
    no_incident = tuple(item for item in items if item[1].evaluation_class is EvaluationClassV233.NO_INCIDENT)
    conflicts = tuple(item for item in items if item[1].evaluation_class is EvaluationClassV233.IRRECONCILABLE_CONTROL)
    controls = tuple(item for item in items if item[1].evaluation_class is not EvaluationClassV233.NOVELTY)
    reports = tuple(run for run, _truth in items if run.provisional_report is not None)
    predicted_pairs = tuple((truth.expected_broad_domain, run.runtime_broad_domain) for run, truth in novelty)
    confusion_counts: dict[tuple[ProvisionalFaultDomainV23, ProvisionalFaultDomainV23 | None], int] = {}
    for expected, predicted in predicted_pairs:
        if expected is None:
            continue
        confusion_counts[(expected, predicted)] = confusion_counts.get((expected, predicted), 0) + 1
    confusion = tuple(
        DomainConfusionEntryV233(expected=key[0], predicted=key[1], count=value)
        for key, value in sorted(
            confusion_counts.items(),
            key=lambda item: (item[0][0].value, "" if item[0][1] is None else item[0][1].value),
        )
    )
    top2 = 0
    for run, truth in novelty:
        candidates: tuple[ProvisionalFaultDomainV23 | None, ...]
        if run.domain_projection is None:
            candidates = (run.runtime_broad_domain,)
        else:
            candidates = tuple(
                item.domain
                for item in sorted(
                    run.domain_projection.domain_scores,
                    key=lambda item: (-item.score, item.domain.value),
                )[:2]
            )
        top2 += truth.expected_broad_domain in candidates
    irrecon_runs = tuple(
        (run, truth)
        for run, truth in items
        if run.guard_decision is not None
        and run.guard_decision.disposition is IrreconcilableGuardDispositionV233.IRRECONCILABLE
    )
    guard_read_runs = tuple(run for run, _truth in items if run.guard_read_used)
    margins = tuple(
        run.domain_projection.score_margin
        for run, truth in novelty
        if run.domain_projection is not None
    )
    evidence_valid = sum(
        set((*run.supporting_evidence_refs, *run.contradicting_evidence_refs)).issubset(run.memory_evidence_refs)
        for run in reports
    )
    alternative_complete = sum(
        set(report.alternative_hypothesis_ids)
        == {item.hypothesis_id for item in report.runtime_hypotheses if item.hypothesis_id != report.preferred_hypothesis_id}
        for report in (item.provisional_report for item in reports)
        if report is not None
    )
    unresolved_complete = sum(
        all(
            set(item.unresolved_questions).issubset(report.unresolved_questions)
            for item in report.runtime_hypotheses
        )
        for report in (item.provisional_report for item in reports)
        if report is not None
    )
    return ArmMetricsV233(
        policy=policy,
        novelty_recall=_rate(sum(run.final_disposition in _REPORTABLE for run, _truth in novelty), len(novelty)),
        root_localization=_rate(sum(run.runtime_root_service == truth.expected_root_service for run, truth in novelty), len(novelty)),
        broad_domain_accuracy=_rate(sum(run.runtime_broad_domain is truth.expected_broad_domain for run, truth in novelty), len(novelty)),
        top2_domain_recall=_rate(top2, len(novelty)),
        projection_resolved=sum(run.domain_projection is not None and run.domain_projection.status.value == "RESOLVED" for run, _truth in novelty),
        projection_ambiguous=sum(run.domain_projection is not None and run.domain_projection.status.value == "AMBIGUOUS" for run, _truth in novelty),
        projection_unsupported=sum(run.domain_projection is None or run.domain_projection.status.value == "UNSUPPORTED" for run, _truth in novelty),
        mean_score_margin=0.0 if not margins else sum(margins) / len(margins),
        domain_confusion=confusion,
        provider_domain_drift=sum(run.provisional_report is not None and run.provisional_report.broad_fault_domain is not run.runtime_broad_domain for run, _truth in items),
        irreconcilable_control_accuracy=_rate(sum(run.final_disposition == "CONFLICTING_EVIDENCE" for run, _truth in conflicts), len(conflicts)),
        strong_witness_precision=_rate(sum(truth.evaluation_class is EvaluationClassV233.IRRECONCILABLE_CONTROL for _run, truth in irrecon_runs), len(irrecon_runs)),
        strong_witness_recall=_rate(sum(run.final_disposition == "CONFLICTING_EVIDENCE" for run, _truth in conflicts), len(conflicts)),
        novelty_cases_blocked_by_guard=sum(run.final_disposition == "CONFLICTING_EVIDENCE" for run, _truth in novelty),
        resolvable_witness_read_rate=_rate(len(guard_read_runs), sum(run.guard_decision is not None for run, _truth in items)),
        post_read_witness_closure_rate=_rate(sum(run.guard_read_used and run.final_disposition == "CONFLICTING_EVIDENCE" for run, _truth in items), len(guard_read_runs)),
        hard_conflict_rate=_rate(sum(run.final_disposition == "CONFLICTING_EVIDENCE" for run, _truth in items), len(items)),
        evidence_ref_validity=_rate(evidence_valid, len(reports)),
        alternative_completeness=_rate(alternative_complete, len(reports)),
        unresolved_question_completeness=_rate(unresolved_complete, len(reports)),
        false_novel_rate=_rate(sum(run.final_disposition in _REPORTABLE for run, _truth in controls), len(controls)),
        known_world_accuracy=_rate(sum(run.final_disposition in {"KNOWN_INCIDENT", "REGISTERED_KNOWN"} for run, _truth in known), len(known)),
        no_incident_accuracy=_rate(sum(run.final_disposition == "NO_INCIDENT" for run, _truth in no_incident), len(no_incident)),
        mean_discovery_reads=sum(run.discovery_read_count for run, _truth in items) / len(items),
        provider_calls=sum(run.provider_cost.provider_calls for run, _truth in items),
        protocol_repairs=sum(run.provider_cost.protocol_repairs for run, _truth in items),
        protocol_failures=sum(run.provider_error_code == "PROTOCOL_FAILED" for run, _truth in items),
        transport_retries=sum(run.provider_cost.transport_retries for run, _truth in items),
        input_tokens=sum(run.provider_cost.input_tokens for run, _truth in items),
        output_tokens=sum(run.provider_cost.output_tokens for run, _truth in items),
        total_tokens=sum(run.provider_cost.total_tokens for run, _truth in items),
        latency_ms=sum(run.provider_cost.latency_ms for run, _truth in items),
        runtime_exceptions=0,
        action_authority_violations=sum(run.action_authority_violations for run, _truth in items),
    )


def score_study_v233(
    comparisons: tuple[EvaluationCaseComparisonV233, ...],
) -> StudyMetricsV233:
    scored = tuple(
        _score_arm(policy=policy, comparisons=comparisons)
        for policy in EvaluationPolicyV233
    )
    arms = (scored[0], scored[1], scored[2])
    baseline = next(item for item in arms if item.policy is EvaluationPolicyV233.V232_CONFLICT_AWARE_BASELINE)
    combined = next(item for item in arms if item.policy is EvaluationPolicyV233.V233_DOMAIN_BOUND_WITNESS_GUARD)
    return StudyMetricsV233(
        arms=arms,
        registered_known_accuracy_drop_cases=max(0, round((baseline.known_world_accuracy - combined.known_world_accuracy) * 4)),
        no_incident_accuracy_drop_cases=max(0, round((baseline.no_incident_accuracy - combined.no_incident_accuracy) * 3)),
    )


def score_measured_terminal_v233(metrics: StudyMetricsV233) -> MeasuredResultTerminalV233:
    baseline = metrics.require(EvaluationPolicyV233.V232_CONFLICT_AWARE_BASELINE)
    combined = metrics.require(EvaluationPolicyV233.V233_DOMAIN_BOUND_WITNESS_GUARD)
    positive = (
        combined.novelty_recall >= 0.80
        and combined.novelty_recall >= baseline.novelty_recall - 0.05
        and combined.root_localization >= 0.80
        and combined.broad_domain_accuracy >= 0.65
        and combined.broad_domain_accuracy >= baseline.broad_domain_accuracy + 0.30
        and combined.irreconcilable_control_accuracy >= 0.75
        and combined.false_novel_rate <= 0.10
        and combined.evidence_ref_validity >= 0.90
        and metrics.registered_known_accuracy_drop_cases <= 1
        and metrics.no_incident_accuracy_drop_cases <= 1
        and combined.protocol_failures <= 1
        and combined.action_authority_violations == 0
    )
    if positive:
        return MeasuredResultTerminalV233.EFFECT_OBSERVED
    mixed = (
        combined.novelty_recall >= 0.75
        and combined.broad_domain_accuracy >= baseline.broad_domain_accuracy + 0.20
        and combined.irreconcilable_control_accuracy >= 0.50
        and combined.false_novel_rate <= 0.20
        and combined.evidence_ref_validity >= 0.85
        and combined.action_authority_violations == 0
    )
    return (
        MeasuredResultTerminalV233.MIXED_RESULT
        if mixed
        else MeasuredResultTerminalV233.NOT_OBSERVED
    )


class StudyArtifactV233(DtaModelV22):
    schema_version: Literal["dta-v233.fixed-evaluation.v1"]
    execution_count: int = Field(ge=0, le=1)
    case_count: Literal[28]
    run_count: Literal[84]
    provider_model: str
    comparisons: tuple[EvaluationCaseComparisonV233, ...] = Field(min_length=28, max_length=28)
    metrics: StudyMetricsV233
    measured_result_terminal: MeasuredResultTerminalV233
    truth_load_count: Literal[28]
    runtime_exceptions: Literal[0]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    docker_calls: Literal[0]
    new_live_faults: Literal[0]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_artifact(self) -> "StudyArtifactV233":
        ids = tuple(item.case_id for item in self.comparisons)
        if ids != tuple(f"vx-{ordinal:03d}" for ordinal in range(301, 329)):
            raise ValueError("v2.3.3 study denominator differs")
        if self.measured_result_terminal is not score_measured_terminal_v233(self.metrics):
            raise ValueError("v2.3.3 measured terminal differs")
        expected = semantic_sha256_v22(self.model_dump(mode="json", exclude={"artifact_sha256"}))
        if self.artifact_sha256 != expected:
            raise ValueError("v2.3.3 study digest differs")
        return self


def _build_comparison(
    *,
    repository_root: Path,
    context: Any,
    truth_store: LazyTruthStoreV233,
    arm_order: tuple[EvaluationPolicyV233, EvaluationPolicyV233, EvaluationPolicyV233],
    provider_transport: Callable[[str], str] | None,
) -> EvaluationCaseComparisonV233:
    completed: list[EvaluationArmRunV233] = []
    for policy in arm_order:
        if policy is EvaluationPolicyV233.V232_CONFLICT_AWARE_BASELINE:
            run = run_v232_baseline_arm_v233(context=context, provider_transport=provider_transport)
        elif policy is EvaluationPolicyV233.V233_DOMAIN_BOUND:
            run = run_domain_bound_arm_v233(context=context, provider_transport=provider_transport)
        else:
            run = run_combined_arm_v233(repository_root=repository_root, context=context, provider_transport=provider_transport)
        completed.append(run)
    runs = tuple(completed)
    canonical_runs = tuple(
        next(item for item in runs if item.policy is policy)
        for policy in EvaluationPolicyV233
    )
    truth = truth_store.load_case_after_three_arms(
        case_id=context.case.case_id,
        runs=canonical_runs,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.evaluation-case-comparison.v1",
        "case_id": context.case.case_id,
        "arm_order": arm_order,
        "runs": runs,
        "evaluator_truth": truth,
    }
    draft = EvaluationCaseComparisonV233.model_construct(**payload, comparison_sha256="0" * 64)
    return EvaluationCaseComparisonV233.model_validate(
        {
            **payload,
            "comparison_sha256": semantic_sha256_v22(draft.model_dump(mode="json", exclude={"comparison_sha256"})),
        }
    )


def _build_study_artifact(
    *,
    execution_count: int,
    provider_model: str,
    comparisons: tuple[EvaluationCaseComparisonV233, ...],
    truth_load_count: int,
) -> StudyArtifactV233:
    metrics = score_study_v233(comparisons)
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.fixed-evaluation.v1",
        "execution_count": execution_count,
        "case_count": 28,
        "run_count": 84,
        "provider_model": provider_model,
        "comparisons": comparisons,
        "metrics": metrics,
        "measured_result_terminal": score_measured_terminal_v233(metrics),
        "truth_load_count": truth_load_count,
        "runtime_exceptions": 0,
        "action_authority_violations": sum(item.action_authority_violations for item in metrics.arms),
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
    }
    draft = StudyArtifactV233.model_construct(**payload, artifact_sha256="0" * 64)
    return StudyArtifactV233.model_validate(
        {
            **payload,
            "artifact_sha256": semantic_sha256_v22(draft.model_dump(mode="json", exclude={"artifact_sha256"})),
        }
    )


def run_deterministic_study_v233(
    *,
    repository_root: Path,
    evaluation_root: Path,
) -> StudyArtifactV233:
    cases = load_evaluation_cases_v233(evaluation_root / "cases.json")
    views = load_evaluation_views_v233(evaluation_root / "ontology-views.json")
    truth_store = LazyTruthStoreV233(evaluation_root / "truth.json")
    comparisons: list[EvaluationCaseComparisonV233] = []
    for index, spec in enumerate(cases.cases):
        view = views.require(spec.case_id)
        context = _build_common_context_v23(
            case=materialize_evaluation_case_v231(repository_root=repository_root, spec=spec),
            hidden_mechanism=view.hidden_mechanism,
        )
        comparisons.append(
            _build_comparison(
                repository_root=repository_root,
                context=context,
                truth_store=truth_store,
                arm_order=counterbalanced_arm_order_v233(index),
                provider_transport=None,
            )
        )
    return _build_study_artifact(
        execution_count=0,
        provider_model="DETERMINISTIC_NO_PROVIDER",
        comparisons=tuple(comparisons),
        truth_load_count=len(truth_store.loaded_case_ids),
    )


class EvaluationPreflightV233(DtaModelV22):
    schema_version: Literal["dta-v233.final-evaluation-preflight.v1"]
    case_count: Literal[28]
    planned_runs: Literal[84]
    execution_count_before: Literal[0]
    provider_model: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission_matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    smoke_manifest_bridge_sha256: str | None
    independent_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["DTA_V233_FINAL_EVALUATION_PREFLIGHT_PASS"]


class _TotalityAddendumRedEvidenceV233(DtaModelV22):
    legacy_tool_has_uncertainty_mode: Literal[False]
    legacy_tool_has_suspected_root_services: Literal[True]
    legacy_tool_property_count: Literal[14]


class _TotalityAddendumGreenEvidenceV233(DtaModelV22):
    focused_test: Literal[
        "test_v233_transport_preserves_the_v231_legacy_tool_schema"
    ]
    focused_suite_passed: Literal[32]
    ruff: Literal["PASS"]
    mypy: Literal["PASS"]


class _TotalityAddendumScopeV233(DtaModelV22):
    v233_minimal_schema_unchanged: Literal[True]
    v231_legacy_schema_preserved: Literal[True]
    domain_projection_unchanged: Literal[True]
    witness_guard_unchanged: Literal[True]
    evaluation_data_unchanged: Literal[True]
    provider_prompt_unchanged: Literal[True]
    scorer_unchanged: Literal[True]
    thresholds_unchanged: Literal[True]


class _TotalityAddendumSmokeV233(DtaModelV22):
    status: Literal["DTA_V233_PROVIDER_SMOKE_PASS"]
    execution_count: Literal[1]
    real_fixes: Literal[2]
    smoke_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProviderSmokeTotalityAddendumV233(DtaModelV22):
    schema_version: Literal[
        "dta-v233.provider-smoke-repair-totality-addendum.v1"
    ]
    repair_ordinal: Literal[2]
    real_provider_calls: Literal[0]
    reason: Literal["PRESERVE_EXACT_V232_BASELINE_TOOL_SCHEMA"]
    red_evidence: _TotalityAddendumRedEvidenceV233
    green_evidence: _TotalityAddendumGreenEvidenceV233
    scope: _TotalityAddendumScopeV233
    source_sha256: dict[str, str]
    preserved_smoke: _TotalityAddendumSmokeV233
    superseded_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_evaluation_execution_count: Literal[0]

    @model_validator(mode="after")
    def require_addendum(self) -> "ProviderSmokeTotalityAddendumV233":
        expected_sources = {
            "src/ecomsre/dta_v2/v23/discovery_provider_v233.py",
            "tests/dta_v233/test_provider_report_v233.py",
        }
        if set(self.source_sha256) != expected_sources or any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in self.source_sha256.values()
        ):
            raise ValueError("v2.3.3 totality addendum source binding differs")
        return self


def _verify_smoke_manifest_bridge_v233(
    *,
    repository_root: Path,
    manifest: EvaluationManifestV233,
    smoke: ProviderSmokeArtifactV233,
    provider_smoke_path: Path,
) -> str:
    addendum_relative = (
        "docs/analysis/dta-v233-provider-smoke-repair-2-totality-addendum.json"
    )
    smoke_relative = str(provider_smoke_path.relative_to(repository_root))
    bindings = {item.path: item for item in manifest.frozen_files}
    if smoke_relative not in bindings or addendum_relative not in bindings:
        raise ValueError("v2.3.3 smoke bridge files are not directly frozen")
    addendum_path = repository_root / addendum_relative
    addendum = ProviderSmokeTotalityAddendumV233.model_validate_json(
        addendum_path.read_bytes()
    )
    smoke_file_sha = _file_sha256(provider_smoke_path)
    addendum_file_sha = _file_sha256(addendum_path)
    if (
        bindings[smoke_relative].sha256 != smoke_file_sha
        or bindings[addendum_relative].sha256 != addendum_file_sha
        or addendum.superseded_manifest_sha256 != smoke.manifest_sha256
        or addendum.preserved_smoke.status != smoke.status
        or addendum.preserved_smoke.execution_count != smoke.execution_count
        or addendum.preserved_smoke.real_fixes != smoke.real_fixes
        or addendum.preserved_smoke.smoke_sha256 != smoke.smoke_sha256
        or addendum.preserved_smoke.file_sha256 != smoke_file_sha
    ):
        raise ValueError("v2.3.3 smoke bridge preservation differs")
    expected_sources = {
        path: _file_sha256(repository_root / path)
        for path in addendum.source_sha256
    }
    if addendum.source_sha256 != expected_sources:
        raise ValueError("v2.3.3 smoke bridge source bytes differ")
    return addendum_file_sha


def build_final_evaluation_preflight_v233(
    *,
    repository_root: Path,
    evaluation_root: Path,
    manifest_path: Path,
    independent_review_path: Path,
    provider_smoke_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    expected_provider_model: str,
) -> EvaluationPreflightV233:
    manifest = require_provider_gates_v233(
        repository_root=repository_root,
        manifest_path=manifest_path,
        independent_review_path=independent_review_path,
        expected_provider_model=expected_provider_model,
    )
    smoke = ProviderSmokeArtifactV233.model_validate_json(provider_smoke_path.read_bytes())
    manifest_sha = _file_sha256(manifest_path)
    bridge_sha: str | None = None
    if smoke.manifest_sha256 != manifest_sha:
        bridge_sha = _verify_smoke_manifest_bridge_v233(
            repository_root=repository_root,
            manifest=manifest,
            smoke=smoke,
            provider_smoke_path=provider_smoke_path,
        )
    if (repository_root / manifest.provider_smoke_output).resolve() != provider_smoke_path.resolve():
        raise ValueError("v2.3.3 smoke path differs")
    if (repository_root / manifest.output_json).resolve() != output_path.resolve():
        raise ValueError("v2.3.3 JSON output path differs")
    if (repository_root / manifest.output_markdown).resolve() != output_markdown_path.resolve():
        raise ValueError("v2.3.3 Markdown output path differs")
    local_root = repository_root / ".local/dta-v233"
    if any(path.exists() for path in (local_root / "fixed-evaluation.started.json", local_root / "fixed-evaluation.partial.jsonl", output_path, output_markdown_path)):
        raise FileExistsError("v2.3.3 final write-once boundary already exists")
    return EvaluationPreflightV233(
        schema_version="dta-v233.final-evaluation-preflight.v1",
        case_count=28,
        planned_runs=84,
        execution_count_before=0,
        provider_model=expected_provider_model,
        manifest_sha256=manifest_sha,
        admission_matrix_sha256=_file_sha256(evaluation_root / "admission-matrix.json"),
        runtime_preflight_sha256=_file_sha256(repository_root / "docs/analysis/dta-v233-runtime-preflight.json"),
        provider_smoke_sha256=_file_sha256(provider_smoke_path),
        smoke_manifest_bridge_sha256=bridge_sha,
        independent_review_sha256=_file_sha256(independent_review_path),
        status="DTA_V233_FINAL_EVALUATION_PREFLIGHT_PASS",
    )


def render_evaluation_markdown_v233(artifact: StudyArtifactV233) -> str:
    lines = [
        "# DTA v2.3.3 Domain-Bound Witness-Guard Evaluation",
        "",
        f"Measured terminal: `{artifact.measured_result_terminal.value}`",
        "",
        f"- Execution count: `{artifact.execution_count}`",
        f"- Cases / runs: `{artifact.case_count}` / `{artifact.run_count}`",
        f"- Provider model: `{artifact.provider_model}`",
        "",
        "| Arm | Novelty | Root | Domain | Top-2 | Conflict | False novel | Evidence refs | Calls | Tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for metrics in artifact.metrics.arms:
        lines.append(
            f"| `{metrics.policy.value}` | {metrics.novelty_recall:.3f} | {metrics.root_localization:.3f} | {metrics.broad_domain_accuracy:.3f} | {metrics.top2_domain_recall:.3f} | {metrics.irreconcilable_control_accuracy:.3f} | {metrics.false_novel_rate:.3f} | {metrics.evidence_ref_validity:.3f} | {metrics.provider_calls} | {metrics.total_tokens} |"
        )
    baseline = artifact.metrics.require(EvaluationPolicyV233.V232_CONFLICT_AWARE_BASELINE)
    domain = artifact.metrics.require(EvaluationPolicyV233.V233_DOMAIN_BOUND)
    combined = artifact.metrics.require(EvaluationPolicyV233.V233_DOMAIN_BOUND_WITNESS_GUARD)
    lines.extend(
        (
            "",
            "## Component interpretation",
            "",
            f"- Domain package: broad-domain accuracy `{baseline.broad_domain_accuracy:.3f}` → `{domain.broad_domain_accuracy:.3f}`; Provider calls `{baseline.provider_calls}` → `{domain.provider_calls}`.",
            f"- Guard increment: irreconcilable accuracy `{domain.irreconcilable_control_accuracy:.3f}` → `{combined.irreconcilable_control_accuracy:.3f}`; false-novel rate `{domain.false_novel_rate:.3f}` → `{combined.false_novel_rate:.3f}`; mean reads `{domain.mean_discovery_reads:.3f}` → `{combined.mean_discovery_reads:.3f}`.",
            "",
            "These are fixed-set component comparisons, not claims of statistical significance.",
            "",
        )
    )
    return "\n".join(lines)


def run_fixed_evaluation_once_v233(
    *,
    repository_root: Path,
    evaluation_root: Path,
    manifest_path: Path,
    independent_review_path: Path,
    provider_smoke_path: Path,
    output_path: Path,
    output_markdown_path: Path,
    provider_transport: OpenAICompatibleDiscoveryTransportV233,
    observer: Callable[[EvaluationCaseComparisonV233], None] | None = None,
) -> StudyArtifactV233:
    preflight = build_final_evaluation_preflight_v233(
        repository_root=repository_root,
        evaluation_root=evaluation_root,
        manifest_path=manifest_path,
        independent_review_path=independent_review_path,
        provider_smoke_path=provider_smoke_path,
        output_path=output_path,
        output_markdown_path=output_markdown_path,
        expected_provider_model=provider_transport.config.model,
    )
    local_root = repository_root / ".local/dta-v233"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "fixed-evaluation.started.json"
    partial = local_root / "fixed-evaluation.partial.jsonl"
    with sentinel.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "status": "STARTED",
                    "planned_execution_count": 1,
                    "planned_case_count": 28,
                    "planned_run_count": 84,
                    "manifest_sha256": preflight.manifest_sha256,
                    "provider_smoke_sha256": preflight.provider_smoke_sha256,
                    "independent_review_sha256": preflight.independent_review_sha256,
                    "started_at_utc": datetime.now(timezone.utc).isoformat(),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    cases = load_evaluation_cases_v233(evaluation_root / "cases.json")
    views = load_evaluation_views_v233(evaluation_root / "ontology-views.json")
    truth_store = LazyTruthStoreV233(evaluation_root / "truth.json")
    comparisons: list[EvaluationCaseComparisonV233] = []
    with partial.open("x", encoding="utf-8") as handle:
        for index, spec in enumerate(cases.cases):
            view = views.require(spec.case_id)
            context = _build_common_context_v23(
                case=materialize_evaluation_case_v231(repository_root=repository_root, spec=spec),
                hidden_mechanism=view.hidden_mechanism,
            )
            comparison = _build_comparison(
                repository_root=repository_root,
                context=context,
                truth_store=truth_store,
                arm_order=counterbalanced_arm_order_v233(index),
                provider_transport=provider_transport,
            )
            comparisons.append(comparison)
            handle.write(comparison.model_dump_json() + "\n")
            handle.flush()
            if observer is not None:
                observer(comparison)
    artifact = _build_study_artifact(
        execution_count=1,
        provider_model=provider_transport.config.model,
        comparisons=tuple(comparisons),
        truth_load_count=len(truth_store.loaded_case_ids),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    with output_markdown_path.open("x", encoding="utf-8") as handle:
        handle.write(render_evaluation_markdown_v233(artifact))
    sentinel.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "execution_count": 1,
                "artifact_sha256": artifact.artifact_sha256,
                "output_json_sha256": _file_sha256(output_path),
                "output_markdown_sha256": _file_sha256(output_markdown_path),
                "measured_result_terminal": artifact.measured_result_terminal.value,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return artifact


__all__ = (
    "ArmMetricsV233",
    "EvaluationCaseComparisonV233",
    "EvaluationManifestV233",
    "EvaluationPreflightV233",
    "MeasuredResultTerminalV233",
    "ProviderSmokeArtifactV233",
    "ProviderSmokeRepairRecordV233",
    "ProviderSmokeRoleV233",
    "StudyArtifactV233",
    "StudyMetricsV233",
    "build_final_evaluation_preflight_v233",
    "counterbalanced_arm_order_v233",
    "load_evaluation_manifest_v233",
    "render_evaluation_markdown_v233",
    "require_provider_gates_v233",
    "run_deterministic_study_v233",
    "run_fixed_evaluation_once_v233",
    "run_provider_smoke_v233",
    "score_measured_terminal_v233",
    "score_study_v233",
    "verify_frozen_surface_v233",
)
