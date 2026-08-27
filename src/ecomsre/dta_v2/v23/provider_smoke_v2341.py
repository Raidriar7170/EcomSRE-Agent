"""Fresh eight-task smoke-only data and Provider gate for DTA v2.3.4.1."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.evaluation_data_v233 import (
    load_evaluation_cases_v233,
)
from ecomsre.dta_v2.v23.evaluation_v234 import (
    RegistrationTaskClassV234,
    RegistrationTaskV234,
    _build_report_item_v234,
    _case_context,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    OpenAICompatibleRegistrationAliasTransportV2341,
    RegistrationAliasProviderV2341,
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
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    RegistrationImplementationModeV234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    ProviderCoreOntologyViewV234,
    build_provider_core_ontology_view_v234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    ReviewQueueItemV23,
    ShadowFaultEntryV23,
    TEST_REVIEWER_V23,
)


class RegistrationSmokeRoleV2341(str, Enum):
    HIDDEN_KNOWN_RECONSTRUCTION = "HIDDEN_KNOWN_RECONSTRUCTION"
    DECLARATIVE_READY_NEW = "DECLARATIVE_READY_NEW"
    ENGINEERING_REQUIRED = "ENGINEERING_REQUIRED"
    DUPLICATE_CONTROL = "DUPLICATE_CONTROL"
    INSUFFICIENT_CONTROL = "INSUFFICIENT_CONTROL"
    AMBIGUOUS_CLAUSE_COMPOSITION = "AMBIGUOUS_CLAUSE_COMPOSITION"


class RegistrationSmokeModeV2341(str, Enum):
    DETERMINISTIC_FIXTURE = "DETERMINISTIC_FIXTURE"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


class RegistrationSmokeTaskV2341(DtaModelV22):
    task_id: str = Field(pattern=r"^smoke-v2341-[0-9]{2}$")
    source_case_id: str = Field(pattern=r"^vx-[0-9]{3}$")
    source_case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: RegistrationSmokeRoleV2341
    provider_view: dict[str, Any]
    provider_view_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_root_service: str
    broad_fault_domain: ProvisionalFaultDomainV23
    neutral_human_label: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    mechanism_description: str
    observed_symptoms: tuple[str, ...] = Field(min_length=1)
    selected_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    provider_call_expected: StrictBool
    repair_path_fixture: StrictBool
    noncanonical_order_fixture: StrictBool
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_task(self) -> "RegistrationSmokeTaskV2341":
        if self.observed_symptoms != tuple(sorted(set(self.observed_symptoms))):
            raise ValueError("v2.3.4.1 smoke symptoms are not canonical")
        if self.selected_anomaly_kinds != tuple(
            sorted(set(self.selected_anomaly_kinds), key=lambda item: item.value)
        ):
            raise ValueError("v2.3.4.1 smoke anomaly kinds are not canonical")
        expected_provider = self.role not in {
            RegistrationSmokeRoleV2341.DUPLICATE_CONTROL,
            RegistrationSmokeRoleV2341.INSUFFICIENT_CONTROL,
        }
        if self.provider_call_expected != expected_provider:
            raise ValueError("v2.3.4.1 smoke Provider-call route differs")
        if expected_provider and len(self.selected_anomaly_kinds) < 2:
            raise ValueError("v2.3.4.1 smoke Provider task lacks two signals")
        view = self.materialize_provider_view()
        if view.view_sha256 != self.provider_view_sha256:
            raise ValueError("v2.3.4.1 smoke Provider view binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"task_sha256"})
        )
        if self.task_sha256 != expected:
            raise ValueError("v2.3.4.1 smoke task digest differs")
        return self

    def materialize_provider_view(self) -> ProviderCoreOntologyViewV234:
        return ProviderCoreOntologyViewV234.model_validate_json(
            json.dumps({**self.provider_view, "hidden_mechanism": None})
        )


class RegistrationSmokeTaskSetV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-smoke-task-set.v1"]
    freeze_id: Literal["dta-v2341-provider-smoke-freeze-20260827-a"]
    tasks: tuple[RegistrationSmokeTaskV2341, ...] = Field(min_length=8, max_length=8)
    predecessor_task_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "RegistrationSmokeTaskSetV2341":
        if tuple(item.task_id for item in self.tasks) != tuple(
            f"smoke-v2341-{ordinal:02d}" for ordinal in range(1, 9)
        ):
            raise ValueError("v2.3.4.1 smoke task IDs differ")
        counts = {
            role: sum(item.role is role for item in self.tasks)
            for role in RegistrationSmokeRoleV2341
        }
        if counts != {
            RegistrationSmokeRoleV2341.HIDDEN_KNOWN_RECONSTRUCTION: 2,
            RegistrationSmokeRoleV2341.DECLARATIVE_READY_NEW: 2,
            RegistrationSmokeRoleV2341.ENGINEERING_REQUIRED: 1,
            RegistrationSmokeRoleV2341.DUPLICATE_CONTROL: 1,
            RegistrationSmokeRoleV2341.INSUFFICIENT_CONTROL: 1,
            RegistrationSmokeRoleV2341.AMBIGUOUS_CLAUSE_COMPOSITION: 1,
        }:
            raise ValueError("v2.3.4.1 smoke role composition differs")
        if sum(item.repair_path_fixture for item in self.tasks) < 1:
            raise ValueError("v2.3.4.1 smoke lacks a repair fixture")
        if sum(item.noncanonical_order_fixture for item in self.tasks) < 1:
            raise ValueError("v2.3.4.1 smoke lacks an ordering fixture")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"task_set_sha256"})
        )
        if self.task_set_sha256 != expected:
            raise ValueError("v2.3.4.1 smoke task-set digest differs")
        return self

    def require(self, task_id: str) -> RegistrationSmokeTaskV2341:
        item = next((item for item in self.tasks if item.task_id == task_id), None)
        if item is None:
            raise ValueError("v2.3.4.1 smoke task is absent")
        return item


class RegistrationSmokeTruthV2341(DtaModelV22):
    task_id: str
    target_mechanism: MechanismV22 | None
    expected_implementation_mode: RegistrationImplementationModeV234
    expected_broad_fault_domain: ProvisionalFaultDomainV23 | None


class RegistrationSmokeTruthSetV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-smoke-truth-set.v1"]
    truths: tuple[RegistrationSmokeTruthV2341, ...] = Field(min_length=8, max_length=8)
    truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "RegistrationSmokeTruthSetV2341":
        if tuple(item.task_id for item in self.truths) != tuple(
            f"smoke-v2341-{ordinal:02d}" for ordinal in range(1, 9)
        ):
            raise ValueError("v2.3.4.1 smoke truth IDs differ")
        if sum(item.target_mechanism is not None for item in self.truths) != 2:
            raise ValueError("v2.3.4.1 smoke hidden truth composition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"truth_sha256"})
        )
        if self.truth_sha256 != expected:
            raise ValueError("v2.3.4.1 smoke truth digest differs")
        return self

    def require(self, task_id: str) -> RegistrationSmokeTruthV2341:
        item = next((item for item in self.truths if item.task_id == task_id), None)
        if item is None:
            raise ValueError("v2.3.4.1 smoke truth is absent")
        return item


class RegistrationSmokeManifestFileV2341(DtaModelV22):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegistrationSmokeManifestV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-smoke-manifest.v1"]
    predecessor_head: Literal["edb313655c4be64295012c383cfa19ed48ccb894"]
    branch: Literal["codex/dta-v2341-registration-alias-protocol"]
    provider_model: str
    planned_task_count: Literal[8]
    planned_provider_called_task_count: Literal[6]
    planned_execution_count: Literal[1]
    current_execution_count: Literal[0, 1]
    fixed_evaluation_execution_count: Literal[0]
    real_fix_count: StrictInt = Field(ge=0, le=2)
    repair_record_path: str | None
    repair_record_sha256: str | None
    prior_manifest_sha256: str | None
    prior_manifest_file_sha256: str | None
    frozen_files: tuple[RegistrationSmokeManifestFileV2341, ...]
    provider_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alias_response_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_path: Literal["docs/analysis/dta-v2341-provider-smoke.json"]
    terminal: Literal["DTA_V2341_SMOKE_SURFACE_FROZEN"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_manifest(self) -> "RegistrationSmokeManifestV2341":
        paths = tuple(item.path for item in self.frozen_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("v2.3.4.1 smoke manifest paths are not canonical")
        repair_fields = (
            self.repair_record_path,
            self.repair_record_sha256,
            self.prior_manifest_sha256,
            self.prior_manifest_file_sha256,
        )
        if self.current_execution_count == 0:
            if self.real_fix_count != 0 or any(item is not None for item in repair_fields):
                raise ValueError("fresh v2.3.4.1 smoke manifest claims a repair")
        elif (
            self.real_fix_count not in {1, 2}
            or any(item is None for item in repair_fields)
            or self.repair_record_path not in paths
        ):
            raise ValueError("repaired v2.3.4.1 smoke manifest lacks its chain")
        for digest in repair_fields[1:]:
            if digest is not None and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("v2.3.4.1 smoke repair digest is invalid")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("v2.3.4.1 smoke manifest digest differs")
        return self


class RegistrationSmokeRawBindingV2341(DtaModelV22):
    path: str = Field(
        pattern=(
            r"^\.local/dta-v2341/provider-raw/smoke/"
            r"(?:request|response)-[0-9]{3}\.json$"
        )
    )
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegistrationSmokeRepairDiagnosticV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.provider-smoke-repair-diagnostic.v1"]
    repair_ordinal: StrictInt = Field(ge=1, le=2)
    execution_count: Literal[1]
    fixed_evaluation_execution_count: Literal[0]
    failure_task_id: Literal["smoke-v2341-05"]
    failure_role: Literal["ENGINEERING_REQUIRED"]
    safe_exception_type: Literal["DiscoveryProviderProtocolFailureV23"]
    safe_error: Literal["registration alias Provider exhausted two protocol repairs"]
    fix_code: Literal["V2341_DISPOSITION_GAP_CARDINALITY_BINDING"]
    prior_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_manifest_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_sentinel_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocker_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocker_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_provider_artifacts_scope: Literal[
        ".local/dta-v2341/provider-raw/smoke"
    ]
    raw_bindings: tuple[RegistrationSmokeRawBindingV2341, ...]
    raw_bindings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_provider_call_count: Literal[7]
    diagnosed_at_utc: datetime
    diagnostic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_diagnostic(self) -> "RegistrationSmokeRepairDiagnosticV2341":
        paths = tuple(item.path for item in self.raw_bindings)
        expected_paths = tuple(
            sorted(
                f".local/dta-v2341/provider-raw/smoke/{kind}-{ordinal:03d}.json"
                for kind in ("request", "response")
                for ordinal in range(1, 8)
            )
        )
        if paths != expected_paths:
            raise ValueError("v2.3.4.1 smoke raw repair bindings differ")
        if self.raw_bindings_sha256 != semantic_sha256_v22(
            [item.model_dump(mode="json") for item in self.raw_bindings]
        ):
            raise ValueError("v2.3.4.1 smoke raw binding digest differs")
        if (
            self.diagnosed_at_utc.tzinfo is None
            or self.diagnosed_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.4.1 smoke diagnosis timestamp is not UTC")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"diagnostic_sha256"})
        )
        if self.diagnostic_sha256 != expected:
            raise ValueError("v2.3.4.1 smoke diagnosis digest differs")
        return self


class RegistrationSmokeRepairRecordV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.provider-smoke-repair.v1"]
    repair_ordinal: StrictInt = Field(ge=1, le=2)
    execution_count: Literal[1]
    fixed_evaluation_execution_count: Literal[0]
    fix_code: Literal["V2341_DISPOSITION_GAP_CARDINALITY_BINDING"]
    prior_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_manifest_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    superseded_manifest_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_sentinel_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocker_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocker_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_bindings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    diagnostic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    diagnostic_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_set_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fix_files: tuple[str, ...]
    recorded_at_utc: datetime
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_record(self) -> "RegistrationSmokeRepairRecordV2341":
        if self.fix_files != tuple(sorted(set(self.fix_files))):
            raise ValueError("v2.3.4.1 smoke repair files are not canonical")
        if self.prior_manifest_file_sha256 != self.superseded_manifest_file_sha256:
            raise ValueError("v2.3.4.1 superseded smoke manifest binding differs")
        if (
            self.recorded_at_utc.tzinfo is None
            or self.recorded_at_utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("v2.3.4.1 smoke repair timestamp is not UTC")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"record_sha256"})
        )
        if self.record_sha256 != expected:
            raise ValueError("v2.3.4.1 smoke repair record digest differs")
        return self


class ReplayThenLiveRegistrationAliasTransportV2341:
    """Replay completed decisions, then continue through the live transport."""

    def __init__(
        self,
        *,
        replayed_responses: tuple[str, ...],
        live_transport: Callable[[str], str],
    ) -> None:
        self._replayed = iter(replayed_responses)
        self._remaining_replays = len(replayed_responses)
        self._live_transport = live_transport
        self.replayed_call_count = 0
        self.live_call_count = 0

    def __call__(self, body: str) -> str:
        if self._remaining_replays:
            self._remaining_replays -= 1
            self.replayed_call_count += 1
            return next(self._replayed)
        self.live_call_count += 1
        return self._live_transport(body)


def _file_sha256_v2341(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_smoke_repair_resume_v2341(
    *,
    repository_root: Path,
    manifest: RegistrationSmokeManifestV2341,
    repair_record_path: Path,
    repair_ordinal: int,
    sentinel_path: Path,
    blocker_path: Path,
) -> tuple[RegistrationSmokeRepairRecordV2341, tuple[str, ...]]:
    """Bind a resumed smoke to the consumed campaign and replayable prefix."""

    record = RegistrationSmokeRepairRecordV2341.model_validate_json(
        repair_record_path.read_bytes()
    )
    expected_record_path = repair_record_path.resolve().relative_to(
        repository_root.resolve()
    ).as_posix()
    if (
        record.repair_ordinal != repair_ordinal
        or manifest.current_execution_count != 1
        or manifest.real_fix_count != repair_ordinal
        or manifest.repair_record_path != expected_record_path
        or manifest.repair_record_sha256 != record.record_sha256
        or manifest.prior_manifest_sha256 != record.prior_manifest_sha256
        or manifest.prior_manifest_file_sha256 != record.prior_manifest_file_sha256
    ):
        raise ValueError("v2.3.4.1 smoke repair manifest binding differs")
    diagnostic_path = (
        repository_root
        / f"docs/analysis/dta-v2341-provider-smoke-fix{repair_ordinal}-diagnostic.json"
    )
    superseded_path = (
        repository_root
        / f"docs/analysis/dta-v2341-smoke-manifest-fix{repair_ordinal}-superseded.json"
    )
    diagnostic = RegistrationSmokeRepairDiagnosticV2341.model_validate_json(
        diagnostic_path.read_bytes()
    )
    prior_manifest = json.loads(superseded_path.read_text(encoding="utf-8"))
    blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
    sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    if (
        _file_sha256_v2341(diagnostic_path) != record.diagnostic_file_sha256
        or diagnostic.diagnostic_sha256 != record.diagnostic_sha256
        or _file_sha256_v2341(superseded_path)
        != record.superseded_manifest_file_sha256
        or prior_manifest.get("manifest_sha256") != record.prior_manifest_sha256
        or _file_sha256_v2341(sentinel_path) != record.prior_sentinel_file_sha256
        or sentinel.get("status") != "STARTED"
        or sentinel.get("execution_count") != 1
        or sentinel.get("manifest_sha256") != record.prior_manifest_sha256
        or _file_sha256_v2341(blocker_path) != record.blocker_file_sha256
        or blocker.get("blocker_sha256") != record.blocker_sha256
        or blocker.get("terminal") != "BLOCKED_DTA_V2341_PROVIDER_SMOKE"
        or blocker.get("execution_count") != 1
        or _file_sha256_v2341(
            repository_root / "config/dta-v2341/smoke/tasks.json"
        )
        != record.task_set_file_sha256
        or _file_sha256_v2341(
            repository_root / "config/dta-v2341/smoke/truth.json"
        )
        != record.truth_file_sha256
    ):
        raise ValueError("v2.3.4.1 smoke repair evidence binding differs")
    raw_bindings = diagnostic.raw_bindings
    if diagnostic.raw_bindings_sha256 != record.raw_bindings_sha256:
        raise ValueError("v2.3.4.1 smoke repair raw lineage differs")
    actual_paths = tuple(
        sorted(
            path.relative_to(repository_root).as_posix()
            for path in (repository_root / diagnostic.raw_provider_artifacts_scope).glob(
                "*.json"
            )
        )
    )
    if actual_paths != tuple(item.path for item in raw_bindings):
        raise ValueError("v2.3.4.1 smoke raw artifact set differs")
    for binding in raw_bindings:
        if _file_sha256_v2341(repository_root / binding.path) != binding.sha256:
            raise ValueError("v2.3.4.1 smoke raw artifact bytes differ")
    replayed: list[str] = []
    for ordinal in range(1, 5):
        payload = json.loads(
            (
                repository_root
                / f".local/dta-v2341/provider-raw/smoke/response-{ordinal:03d}.json"
            ).read_text(encoding="utf-8")
        )
        replayed.append(
            OpenAICompatibleRegistrationAliasTransportV2341._extract(payload)
        )
    return record, tuple(replayed)


class RegistrationSmokeTaskResultV2341(DtaModelV22):
    task_id: str
    role: RegistrationSmokeRoleV2341
    provider_call_expected: StrictBool
    provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    transport_retries: StrictInt = Field(ge=0)
    catalog_feasibility_pass: Literal[True]
    provider_schema_valid: Literal[True]
    aliases_resolved: Literal[True]
    draft_assembled: StrictBool
    implementation_mode: RegistrationImplementationModeV234
    validation_status: str
    context_pass: Literal[True]
    production_collision_safe: StrictBool
    compile_valid: StrictBool
    promotion_eligible: Literal[False]
    canonical_order_failures: Literal[0]
    executable_content_violations: Literal[0]
    action_authority_violations: Literal[0]
    passed: Literal[True]


class RegistrationProviderSmokeArtifactV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-provider-smoke.v1"]
    mode: RegistrationSmokeModeV2341
    execution_count: int = Field(ge=0, le=1)
    task_count: Literal[8]
    provider_called_task_count: Literal[6]
    zero_call_control_count: Literal[2]
    tasks: tuple[RegistrationSmokeTaskResultV2341, ...]
    provider_call_count: StrictInt = Field(ge=0)
    protocol_repair_count: StrictInt = Field(ge=0)
    transport_retry_count: StrictInt = Field(ge=0)
    catalog_feasibility_pass_count: Literal[6]
    alias_resolution_failure_count: Literal[0]
    assembler_failure_count: Literal[0]
    canonical_order_failures: Literal[0]
    executable_content_violations: Literal[0]
    action_authority_violations: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    remediation_registrations: Literal[0]
    terminal: Literal[
        "DTA_V2341_SMOKE_PREFLIGHT_PASS",
        "DTA_V2341_PROVIDER_SMOKE_PASS",
    ]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_artifact(self) -> "RegistrationProviderSmokeArtifactV2341":
        if self.mode is RegistrationSmokeModeV2341.DETERMINISTIC_FIXTURE:
            if self.execution_count != 0 or self.terminal != "DTA_V2341_SMOKE_PREFLIGHT_PASS":
                raise ValueError("deterministic smoke terminal differs")
        elif self.execution_count != 1 or self.terminal != "DTA_V2341_PROVIDER_SMOKE_PASS":
            raise ValueError("real Provider smoke terminal differs")
        if tuple(item.task_id for item in self.tasks) != tuple(
            f"smoke-v2341-{ordinal:02d}" for ordinal in range(1, 9)
        ):
            raise ValueError("smoke result task IDs differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("v2.3.4.1 smoke artifact digest differs")
        return self


class RegistrationSmokeCatalogTaskV2341(DtaModelV22):
    task_id: str
    role: RegistrationSmokeRoleV2341
    provider_call_expected: StrictBool
    predicate_option_count: StrictInt = Field(ge=0, le=12)
    clause_option_count: StrictInt = Field(ge=0, le=24)
    engineering_gap_option_count: StrictInt = Field(ge=0)
    hidden_target_leaks: Literal[0]
    provider_calls: Literal[0]
    status: Literal["PASS"]


class RegistrationSmokeCatalogFeasibilityV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.smoke-catalog-feasibility.v1"]
    task_count: Literal[8]
    provider_called_task_count: Literal[6]
    catalog_feasibility_pass_count: Literal[6]
    deterministic_zero_call_control_count: Literal[2]
    hidden_target_leaks: Literal[0]
    provider_calls: Literal[0]
    tasks: tuple[RegistrationSmokeCatalogTaskV2341, ...]
    terminal: Literal["DTA_V2341_CATALOG_FEASIBILITY_PASS"]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_artifact(self) -> "RegistrationSmokeCatalogFeasibilityV2341":
        if tuple(item.task_id for item in self.tasks) != tuple(
            f"smoke-v2341-{ordinal:02d}" for ordinal in range(1, 9)
        ):
            raise ValueError("smoke catalog-feasibility task IDs differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("smoke catalog-feasibility digest differs")
        return self


_SMOKE_SPECS_V2341: tuple[dict[str, Any], ...] = (
    {"source": "vx-302", "role": "HIDDEN_KNOWN_RECONSTRUCTION", "domain": "CONFIGURATION", "root": "svc-77d4c9fe68", "label": "opaque-signal-cluster-alpha", "description": "Recent opaque state movement coincides with strong target error evidence.", "symptoms": ("changes show recent opaque state movement", "metrics show strong target error evidence"), "kinds": ("METRIC_ERROR_OUTLIER", "RECENT_CHANGE_CORRELATION"), "target": "CONFIGURATION_ERROR", "mode": "DECLARATIVE_READY"},
    {"source": "vx-304", "role": "HIDDEN_KNOWN_RECONSTRUCTION", "domain": "DEPENDENCY", "root": "svc-c7902c06b9", "label": "opaque-signal-cluster-beta", "description": "A slow downstream path coincides with strong target latency evidence.", "symptoms": ("metrics show strong target latency", "traces show a slow downstream path"), "kinds": ("METRIC_LATENCY_OUTLIER", "TRACE_LATENCY_OUTLIER"), "target": "DEPENDENCY_LATENCY", "mode": "DECLARATIVE_READY"},
    {"source": "vx-312", "role": "DECLARATIVE_READY_NEW", "domain": "CONCURRENCY", "root": "svc-4c99b5103c", "label": "bounded-worker-capacity-pressure", "description": "Concurrent work waits for bounded worker capacity while target errors rise.", "symptoms": ("logs show bounded worker wait under load", "metrics show strong target error evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER"), "target": None, "mode": "DECLARATIVE_READY"},
    {"source": "vx-314", "role": "DECLARATIVE_READY_NEW", "domain": "CONCURRENCY", "root": "svc-4b50cc374b", "label": "bounded-queue-pressure", "description": "Queue pressure delays bounded workers while target errors rise.", "symptoms": ("logs show queue pressure worker wait", "metrics show strong target error evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER"), "target": None, "mode": "DECLARATIVE_READY"},
    {"source": "vx-315", "role": "ENGINEERING_REQUIRED", "domain": "NETWORK", "root": "svc-4c39ea767b", "label": "bounded-transport-ordering-pressure", "description": "Ordering-sensitive transport resets require correlation beyond bounded declarative rules.", "symptoms": ("logs show transport reset ordering", "metrics show strong target error evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER"), "target": None, "mode": "ENGINEERING_REQUIRED"},
    {"source": "vx-318", "role": "DUPLICATE_CONTROL", "domain": "DEPENDENCY", "root": "svc-c1c6cc881d", "label": "visible-existing-pattern", "description": "Visible evidence already satisfies an active Runtime registration.", "symptoms": ("metrics and traces match a visible active clause",), "kinds": (), "target": None, "mode": "DUPLICATE_EXISTING"},
    {"source": "vx-328", "role": "INSUFFICIENT_CONTROL", "domain": "UNKNOWN", "root": "svc-01a294e042", "label": "weak-observation-pattern", "description": "The available observation does not establish a formal incident mechanism.", "symptoms": ("runtime remains healthy without corroborating evidence",), "kinds": (), "target": None, "mode": "INSUFFICIENT_EVIDENCE"},
    {"source": "vx-316", "role": "AMBIGUOUS_CLAUSE_COMPOSITION", "domain": "EXTERNAL", "root": "svc-9470751930", "label": "bounded-external-throttle-pressure", "description": "External throttle responses coincide with error and latency evidence.", "symptoms": ("logs show external throttle responses", "metrics show strong error and latency evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER", "METRIC_LATENCY_OUTLIER"), "target": None, "mode": "DECLARATIVE_READY"},
)


def _hashed_v2341(model: type[DtaModelV22], payload: dict[str, Any], field: str) -> Any:
    factory = cast(Any, model)
    draft = factory.model_construct(**payload, **{field: "0" * 64})
    return model.model_validate(
        {
            **payload,
            field: semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={field})
            ),
        }
    )


def build_smoke_data_v2341(
    *, repository_root: Path
) -> tuple[RegistrationSmokeTaskSetV2341, RegistrationSmokeTruthSetV2341]:
    cases = load_evaluation_cases_v233(
        repository_root / "config/dta-v233/evaluation/cases.json"
    )
    predecessor = json.loads(
        (repository_root / "config/dta-v234/evaluation/tasks.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot = build_core_ontology_schema_snapshot_v234()
    tasks: list[RegistrationSmokeTaskV2341] = []
    truths: list[RegistrationSmokeTruthV2341] = []
    for ordinal, spec in enumerate(_SMOKE_SPECS_V2341, start=1):
        task_id = f"smoke-v2341-{ordinal:02d}"
        target = MechanismV22(spec["target"]) if spec["target"] is not None else None
        source = cases.require(cast(str, spec["source"]))
        view = build_provider_core_ontology_view_v234(
            snapshot=snapshot,
            hidden_mechanism=target,
        )
        role = RegistrationSmokeRoleV2341(spec["role"])
        payload: dict[str, Any] = {
            "task_id": task_id,
            "source_case_id": source.case_id,
            "source_case_sha256": source.source_bytes_sha256,
            "role": role,
            "provider_view": view.model_dump(mode="json"),
            "provider_view_sha256": view.view_sha256,
            "selected_root_service": spec["root"],
            "broad_fault_domain": ProvisionalFaultDomainV23(spec["domain"]),
            "neutral_human_label": spec["label"],
            "mechanism_description": spec["description"],
            "observed_symptoms": tuple(sorted(spec["symptoms"])),
            "selected_anomaly_kinds": tuple(
                sorted(
                    (GenericAnomalyKindV23(item) for item in spec["kinds"]),
                    key=lambda item: item.value,
                )
            ),
            "provider_call_expected": role not in {
                RegistrationSmokeRoleV2341.DUPLICATE_CONTROL,
                RegistrationSmokeRoleV2341.INSUFFICIENT_CONTROL,
            },
            "repair_path_fixture": ordinal == 3,
            "noncanonical_order_fixture": ordinal == 8,
        }
        tasks.append(_hashed_v2341(RegistrationSmokeTaskV2341, payload, "task_sha256"))
        truths.append(
            RegistrationSmokeTruthV2341(
                task_id=task_id,
                target_mechanism=target,
                expected_implementation_mode=RegistrationImplementationModeV234(
                    spec["mode"]
                ),
                expected_broad_fault_domain=(
                    None
                    if role is RegistrationSmokeRoleV2341.INSUFFICIENT_CONTROL
                    else ProvisionalFaultDomainV23(spec["domain"])
                ),
            )
        )
    task_set = _hashed_v2341(
        RegistrationSmokeTaskSetV2341,
        {
            "schema_version": "dta-v2341.registration-smoke-task-set.v1",
            "freeze_id": "dta-v2341-provider-smoke-freeze-20260827-a",
            "tasks": tuple(tasks),
            "predecessor_task_set_sha256": predecessor["task_set_sha256"],
        },
        "task_set_sha256",
    )
    truth_set = _hashed_v2341(
        RegistrationSmokeTruthSetV2341,
        {
            "schema_version": "dta-v2341.registration-smoke-truth-set.v1",
            "truths": tuple(truths),
        },
        "truth_sha256",
    )
    return task_set, truth_set


def load_smoke_tasks_v2341(path: Path) -> RegistrationSmokeTaskSetV2341:
    return RegistrationSmokeTaskSetV2341.model_validate_json(path.read_bytes())


def load_smoke_truth_v2341(path: Path) -> RegistrationSmokeTruthSetV2341:
    return RegistrationSmokeTruthSetV2341.model_validate_json(path.read_bytes())


def load_smoke_manifest_v2341(path: Path) -> RegistrationSmokeManifestV2341:
    return RegistrationSmokeManifestV2341.model_validate_json(path.read_bytes())


def verify_smoke_surface_v2341(
    *,
    repository_root: Path,
    manifest_path: Path,
    expected_provider_model: str,
) -> RegistrationSmokeManifestV2341:
    manifest = load_smoke_manifest_v2341(manifest_path)
    if manifest.provider_model != expected_provider_model:
        raise ValueError("v2.3.4.1 smoke Provider model differs")
    for binding in manifest.frozen_files:
        path = repository_root / binding.path
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != binding.sha256
        ):
            raise ValueError(f"v2.3.4.1 smoke frozen file differs: {binding.path}")
    task_set = load_smoke_tasks_v2341(
        repository_root / "config/dta-v2341/smoke/tasks.json"
    )
    truth_set = load_smoke_truth_v2341(
        repository_root / "config/dta-v2341/smoke/truth.json"
    )
    if len(task_set.tasks) != 8 or len(truth_set.truths) != 8:
        raise ValueError("v2.3.4.1 smoke denominator differs")
    predecessor_bytes = (
        repository_root / "config/dta-v234/evaluation/tasks.json"
    ).read_bytes()
    smoke_bytes = (
        repository_root / "config/dta-v2341/smoke/tasks.json"
    ).read_bytes()
    if smoke_bytes == predecessor_bytes:
        raise ValueError("v2.3.4.1 smoke reuses predecessor task bytes")
    return manifest


def _surrogate_task_v2341(
    task: RegistrationSmokeTaskV2341,
) -> RegistrationTaskV234:
    ordinal = int(task.task_id.rsplit("-", 1)[1])
    task_class = (
        RegistrationTaskClassV234.HIDDEN_KNOWN
        if task.role is RegistrationSmokeRoleV2341.HIDDEN_KNOWN_RECONSTRUCTION
        else RegistrationTaskClassV234.UNREGISTERED
    )
    payload: dict[str, Any] = {
        "task_id": f"rt-{400 + ordinal:03d}",
        "source_case_id": task.source_case_id,
        "source_case_sha256": task.source_case_sha256,
        "task_class": task_class,
        "provider_view_sha256": task.provider_view_sha256,
        "selected_root_service": task.selected_root_service,
        "broad_fault_domain": task.broad_fault_domain,
        "provisional_mechanism_label": task.neutral_human_label.replace("-", " "),
        "mechanism_description": task.mechanism_description,
        "observed_symptoms": task.observed_symptoms,
        "selected_anomaly_kinds": task.selected_anomaly_kinds,
        "provider_call_expected": True,
    }
    return _hashed_v2341(RegistrationTaskV234, payload, "task_sha256")


def _prepare_authorized_smoke_task_v2341(
    *,
    repository_root: Path,
    task: RegistrationSmokeTaskV2341,
    local_root: Path,
) -> tuple[
    ReviewQueueItemV23,
    ShadowFaultEntryV23,
    DraftGenerationAuthorizationResultV234,
]:
    item = _build_report_item_v234(
        repository_root=repository_root,
        task=_surrogate_task_v2341(task),
    )
    review_store = LocalReviewStoreV23(local_root)
    review_store.enqueue(item)
    accepted = review_store.decide(
        report_id=item.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW for fresh v2.3.4.1 smoke only.",
        canonical_label=task.neutral_human_label,
        merge_target=None,
        requested_observations=(),
        reviewed_at=item.queued_at,
    )
    if accepted.shadow_entry is None:
        raise ValueError("v2.3.4.1 smoke acceptance lacks a Shadow Fault")
    authorization = LocalOntologyExpansionStoreV234(
        local_root
    ).authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note=(
            "SIMULATED HUMAN REVIEW authorizes one smoke formal draft only."
        ),
        authorized_at=item.queued_at,
    )
    return item, accepted.shadow_entry, authorization


def _control_disposition_v2341(
    *, repository_root: Path, task: RegistrationSmokeTaskV2341
) -> RegistrationImplementationModeV234:
    context = _case_context(
        repository_root=repository_root,
        task=_surrogate_task_v2341(
            task.model_copy(
                update={
                    "role": RegistrationSmokeRoleV2341.DECLARATIVE_READY_NEW,
                    "provider_call_expected": True,
                    "selected_anomaly_kinds": (
                        GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
                        GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER,
                    ),
                }
            )
        ),
        hide=None,
    )
    if context.admission.admitted_diagnosis is not None:
        return RegistrationImplementationModeV234.DUPLICATE_EXISTING
    sources = {
        item.source
        for item in extract_generic_anomalies_v23(
            memory=context.memory,
            candidate_services=context.case.candidate_services,
        )
    }
    if len(sources) <= 1:
        return RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE
    raise ValueError("smoke zero-call control is not deterministically classifiable")


def _fixture_response_v2341(
    *,
    task: RegistrationSmokeTaskV2341,
    catalog: Any,
) -> str:
    engineering = bool(catalog.engineering_gap_options)
    clauses = [item.clause_alias for item in catalog.clause_options[:2]]
    confusables = [item.confusable_alias for item in catalog.confusable_options[:2]]
    if task.noncanonical_order_fixture:
        clauses = [*reversed(clauses), *clauses[:1]]
        confusables = [*reversed(confusables), *confusables[:1]]
    return json.dumps(
        {
            "disposition_alias": "D01" if engineering else "D00",
            "mechanism_concept": task.neutral_human_label.replace("-", " "),
            "clause_aliases": [] if engineering else clauses,
            "confusable_aliases": confusables,
            "engineering_gap_aliases": [
                item.engineering_gap_alias for item in catalog.engineering_gap_options
            ],
            "semantic_rationale": (
                "Accepted evidence requires one bounded extraction capability."
                if engineering
                else "Accepted evidence supports one bounded mechanism."
            ),
        }
    )


def _lint_hidden_target_v2341(
    *, truth: RegistrationSmokeTruthV2341, provider_payload: dict[str, Any]
) -> None:
    if truth.target_mechanism is None:
        return
    rendered = json.dumps(provider_payload, sort_keys=True).casefold()
    labels = {
        truth.target_mechanism.value.casefold(),
        truth.target_mechanism.value.casefold().replace("_", "-"),
        truth.target_mechanism.value.casefold().replace("_", " "),
    }
    if any(label in rendered for label in labels):
        raise ValueError("hidden smoke target leaked into Provider payload")


def audit_smoke_catalog_feasibility_v2341(
    *,
    repository_root: Path,
    task_set: RegistrationSmokeTaskSetV2341,
    truth_set: RegistrationSmokeTruthSetV2341,
) -> RegistrationSmokeCatalogFeasibilityV2341:
    results: list[RegistrationSmokeCatalogTaskV2341] = []
    with tempfile.TemporaryDirectory(prefix="dta-v2341-catalog-feasibility-") as raw:
        local_root = Path(raw)
        for task in task_set.tasks:
            truth = truth_set.require(task.task_id)
            if not task.provider_call_expected:
                disposition = _control_disposition_v2341(
                    repository_root=repository_root,
                    task=task,
                )
                if disposition is not truth.expected_implementation_mode:
                    raise ValueError("smoke zero-call catalog control differs")
                results.append(
                    RegistrationSmokeCatalogTaskV2341(
                        task_id=task.task_id,
                        role=task.role,
                        provider_call_expected=False,
                        predicate_option_count=0,
                        clause_option_count=0,
                        engineering_gap_option_count=0,
                        hidden_target_leaks=0,
                        provider_calls=0,
                        status="PASS",
                    )
                )
                continue
            item, shadow, authorization = _prepare_authorized_smoke_task_v2341(
                repository_root=repository_root,
                task=task,
                local_root=local_root / task.task_id,
            )
            source_request = build_registration_alias_source_request_v2341(
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                ontology_view=task.materialize_provider_view(),
            )
            catalog = build_registration_option_catalog_v2341(request=source_request)
            feasibility = evaluate_catalog_feasibility_v2341(
                catalog=catalog,
                expected_disposition=truth.expected_implementation_mode,
            )
            if feasibility.status is not CatalogFeasibilityStatusV2341.PASS:
                raise ValueError("BLOCKED_DTA_V2341_CATALOG_COVERAGE")
            provider_request = build_registration_alias_provider_request_v2341(
                source_request=source_request,
                catalog=catalog,
            )
            _lint_hidden_target_v2341(
                truth=truth,
                provider_payload=provider_request.provider_payload(),
            )
            results.append(
                RegistrationSmokeCatalogTaskV2341(
                    task_id=task.task_id,
                    role=task.role,
                    provider_call_expected=True,
                    predicate_option_count=len(catalog.predicate_options),
                    clause_option_count=len(catalog.clause_options),
                    engineering_gap_option_count=len(catalog.engineering_gap_options),
                    hidden_target_leaks=0,
                    provider_calls=0,
                    status="PASS",
                )
            )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.smoke-catalog-feasibility.v1",
        "task_count": 8,
        "provider_called_task_count": 6,
        "catalog_feasibility_pass_count": 6,
        "deterministic_zero_call_control_count": 2,
        "hidden_target_leaks": 0,
        "provider_calls": 0,
        "tasks": tuple(results),
        "terminal": "DTA_V2341_CATALOG_FEASIBILITY_PASS",
    }
    return _hashed_v2341(
        RegistrationSmokeCatalogFeasibilityV2341,
        payload,
        "artifact_sha256",
    )


def run_provider_smoke_v2341(
    *,
    repository_root: Path,
    task_set: RegistrationSmokeTaskSetV2341,
    truth_set: RegistrationSmokeTruthSetV2341,
    mode: RegistrationSmokeModeV2341,
    transport: Callable[[str], str] | None = None,
) -> RegistrationProviderSmokeArtifactV2341:
    if mode is RegistrationSmokeModeV2341.OPENAI_COMPATIBLE and transport is None:
        raise ValueError("real v2.3.4.1 smoke requires a Provider transport")
    results: list[RegistrationSmokeTaskResultV2341] = []
    with tempfile.TemporaryDirectory(prefix="dta-v2341-provider-smoke-") as raw:
        local_root = Path(raw)
        for task in task_set.tasks:
            truth = truth_set.require(task.task_id)
            if not task.provider_call_expected:
                disposition = _control_disposition_v2341(
                    repository_root=repository_root,
                    task=task,
                )
                if disposition is not truth.expected_implementation_mode:
                    raise ValueError("zero-call smoke control disposition differs")
                results.append(
                    RegistrationSmokeTaskResultV2341(
                        task_id=task.task_id,
                        role=task.role,
                        provider_call_expected=False,
                        provider_calls=0,
                        protocol_repairs=0,
                        transport_retries=0,
                        catalog_feasibility_pass=True,
                        provider_schema_valid=True,
                        aliases_resolved=True,
                        draft_assembled=False,
                        implementation_mode=disposition,
                        validation_status=disposition.value,
                        context_pass=True,
                        production_collision_safe=False,
                        compile_valid=False,
                        promotion_eligible=False,
                        canonical_order_failures=0,
                        executable_content_violations=0,
                        action_authority_violations=0,
                        passed=True,
                    )
                )
                continue
            item, shadow, authorization = _prepare_authorized_smoke_task_v2341(
                repository_root=repository_root,
                task=task,
                local_root=local_root / task.task_id,
            )
            source_request = build_registration_alias_source_request_v2341(
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                ontology_view=task.materialize_provider_view(),
            )
            catalog = build_registration_option_catalog_v2341(request=source_request)
            feasibility = evaluate_catalog_feasibility_v2341(
                catalog=catalog,
                expected_disposition=truth.expected_implementation_mode,
            )
            if feasibility.status is not CatalogFeasibilityStatusV2341.PASS:
                raise ValueError("BLOCKED_DTA_V2341_CATALOG_COVERAGE")
            provider_request = build_registration_alias_provider_request_v2341(
                source_request=source_request,
                catalog=catalog,
            )
            _lint_hidden_target_v2341(
                truth=truth,
                provider_payload=provider_request.provider_payload(),
            )
            if mode is RegistrationSmokeModeV2341.DETERMINISTIC_FIXTURE:
                valid_response = _fixture_response_v2341(task=task, catalog=catalog)
                responses = (
                    iter(("{}", valid_response))
                    if task.repair_path_fixture
                    else iter((valid_response,))
                )

                def fixture_transport(_body: str) -> str:
                    return next(responses)

                task_transport: Callable[[str], str] = fixture_transport
            else:
                assert transport is not None
                task_transport = transport
            provider_result = RegistrationAliasProviderV2341(
                transport=task_transport
            ).select(request=provider_request, catalog=catalog)
            hidden_known = (
                task.role
                is RegistrationSmokeRoleV2341.HIDDEN_KNOWN_RECONSTRUCTION
            )
            assembly = assemble_formal_registration_draft_v2341(
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                catalog=catalog,
                provider_result=provider_result,
                validation_context=(
                    RegistrationValidationContextV2341.SMOKE_ROLE_VALIDATION
                ),
            )
            validation = validate_registration_draft_in_context_v2341(
                draft=assembly.formal_draft,
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                context=RegistrationValidationContextV2341.SMOKE_ROLE_VALIDATION,
                promoted_mechanism_slugs=(),
                shadow_mechanism_slugs=(),
                smoke_hidden_known=hidden_known,
            )
            if provider_result.selection.disposition_alias == "D00":
                implementation_mode = RegistrationImplementationModeV234.DECLARATIVE_READY
            elif provider_result.selection.disposition_alias == "D01":
                implementation_mode = RegistrationImplementationModeV234.ENGINEERING_REQUIRED
            elif provider_result.selection.disposition_alias == "D02":
                implementation_mode = RegistrationImplementationModeV234.DUPLICATE_EXISTING
            else:
                implementation_mode = RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE
            if implementation_mode is not truth.expected_implementation_mode:
                raise ValueError("BLOCKED_DTA_V2341_PROVIDER_SMOKE")
            collision_safe = hidden_known and bool(validation.collision_evidence_codes)
            compile_valid = False
            if task.role in {
                RegistrationSmokeRoleV2341.DECLARATIVE_READY_NEW,
                RegistrationSmokeRoleV2341.AMBIGUOUS_CLAUSE_COMPOSITION,
            }:
                if validation.production_validation.status is not DraftValidationStatusV234.VALID:
                    raise ValueError("BLOCKED_DTA_V2341_DRAFT_ASSEMBLER")
                compile_registration_v234(
                    draft=assembly.formal_draft,
                    validation=validation.production_validation,
                    snapshot=authorization.core_ontology_snapshot,
                )
                compile_valid = True
            if hidden_known and not collision_safe:
                raise ValueError("BLOCKED_DTA_V2341_DRAFT_ASSEMBLER")
            if not validation.context_pass or validation.promotion_eligible:
                raise ValueError("BLOCKED_DTA_V2341_DRAFT_ASSEMBLER")
            results.append(
                RegistrationSmokeTaskResultV2341(
                    task_id=task.task_id,
                    role=task.role,
                    provider_call_expected=True,
                    provider_calls=provider_result.trace.provider_calls,
                    protocol_repairs=provider_result.trace.protocol_repairs,
                    transport_retries=provider_result.trace.transport_retries,
                    catalog_feasibility_pass=True,
                    provider_schema_valid=True,
                    aliases_resolved=True,
                    draft_assembled=True,
                    implementation_mode=implementation_mode,
                    validation_status=validation.production_validation.status.value,
                    context_pass=True,
                    production_collision_safe=collision_safe,
                    compile_valid=compile_valid,
                    promotion_eligible=False,
                    canonical_order_failures=assembly.canonical_order_failures,
                    executable_content_violations=0,
                    action_authority_violations=0,
                    passed=True,
                )
            )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.registration-provider-smoke.v1",
        "mode": mode,
        "execution_count": int(mode is RegistrationSmokeModeV2341.OPENAI_COMPATIBLE),
        "task_count": 8,
        "provider_called_task_count": 6,
        "zero_call_control_count": 2,
        "tasks": tuple(results),
        "provider_call_count": sum(item.provider_calls for item in results),
        "protocol_repair_count": sum(item.protocol_repairs for item in results),
        "transport_retry_count": sum(item.transport_retries for item in results),
        "catalog_feasibility_pass_count": 6,
        "alias_resolution_failure_count": 0,
        "assembler_failure_count": 0,
        "canonical_order_failures": 0,
        "executable_content_violations": 0,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "remediation_registrations": 0,
        "terminal": (
            "DTA_V2341_PROVIDER_SMOKE_PASS"
            if mode is RegistrationSmokeModeV2341.OPENAI_COMPATIBLE
            else "DTA_V2341_SMOKE_PREFLIGHT_PASS"
        ),
    }
    return _hashed_v2341(
        RegistrationProviderSmokeArtifactV2341,
        payload,
        "artifact_sha256",
    )


__all__ = (
    "RegistrationProviderSmokeArtifactV2341",
    "RegistrationSmokeCatalogFeasibilityV2341",
    "RegistrationSmokeModeV2341",
    "RegistrationSmokeManifestFileV2341",
    "RegistrationSmokeManifestV2341",
    "RegistrationSmokeRoleV2341",
    "RegistrationSmokeTaskSetV2341",
    "RegistrationSmokeTaskV2341",
    "RegistrationSmokeTruthSetV2341",
    "build_smoke_data_v2341",
    "audit_smoke_catalog_feasibility_v2341",
    "load_smoke_tasks_v2341",
    "load_smoke_manifest_v2341",
    "load_smoke_truth_v2341",
    "run_provider_smoke_v2341",
    "verify_smoke_surface_v2341",
)
