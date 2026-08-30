"""Versioned, self-sealed evidence provenance sidecars for Product v0.2.3.2."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal, TypeVar

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.novelty_gate import NoveltyDispositionV23
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_BINDING_SHA256_V023,
    ACTIVE_PROFILE_SHA256_V023,
    CANDIDATE_SET_SHA256_V023,
    OPERATOR_DECISION_SHA256_V023,
    OpenSearchConnectorDiagnosticsV023,
    OpenSearchConnectorProfileBindingV023,
)
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    PilotRuntimeConnectorSettingsV02,
    ProductModelV1,
)
from ecomsre.product.environment.capabilities import SourceCapabilityStatusV1
from ecomsre.product.pilot.runtime_authority_v02 import PilotRuntimeAuthorityV02


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{0,119}$"
_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:+-]{0,239}$"
_ZERO_SHA256 = "0" * 64
_ACTIVE_PROFILE_ID_V0232 = "product-v0222-operator-selected-profile"

_ModelT = TypeVar("_ModelT", bound=ProductModelV1)


def _require_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def _require_canonical_strings(values: tuple[str, ...], *, field_name: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field_name} are not canonical")


def _require_seal(model: ProductModelV1, *, field_name: str, label: str) -> None:
    expected = semantic_sha256_v22(model.model_dump(mode="json", exclude={field_name}))
    if getattr(model, field_name) != expected:
        raise ValueError(f"{label} digest differs")


def _build_sealed(
    model_type: type[_ModelT],
    *,
    schema_version: str,
    seal_field: str,
    payload: dict[str, Any],
) -> _ModelT:
    body = {"schema_version": schema_version, **payload}
    draft_payload: dict[str, Any] = {**body, seal_field: _ZERO_SHA256}
    draft = model_type.model_construct(**draft_payload)
    normalized = draft.model_dump(
        mode="json",
        exclude={seal_field},
        warnings=False,
    )
    return model_type.model_validate(
        {**normalized, seal_field: semantic_sha256_v22(normalized)}
    )


class ConnectorBindingKindV0232(str, Enum):
    GENERIC = "GENERIC"
    OPENSEARCH_PROFILE = "OPENSEARCH_PROFILE"
    RUNTIME_SNAPSHOT = "RUNTIME_SNAPSHOT"


class CapabilityLimitationCategoryV0232(str, Enum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_PARTIAL = "SOURCE_PARTIAL"
    QUERY_FAILURE = "QUERY_FAILURE"
    COVERAGE_GAP = "COVERAGE_GAP"
    RUNTIME_AUTHORITY_UNAVAILABLE = "RUNTIME_AUTHORITY_UNAVAILABLE"


class CapabilityCoverageStatusV0232(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class KnownAdmissionStatusV0232(str, Enum):
    NONE = "NONE"
    SINGLE_ADMISSION = "SINGLE_ADMISSION"
    MULTIPLE_ADMISSIONS = "MULTIPLE_ADMISSIONS"


class ConnectorEvidenceBindingV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.connector-evidence-binding.v0232"] = (
        "ecomsre.product.connector-evidence-binding.v0232"
    )
    binding_id: str = Field(pattern=_REFERENCE_PATTERN)
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    action_id: str = Field(pattern=r"^a:[a-z0-9][a-z0-9:+-]*$")
    source: EvidenceSourceV22
    connector_name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    connector_kind: ConnectorKindV1
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    connector_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_context_sha256: str = Field(pattern=_SHA256_PATTERN)
    component_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    combined_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    requested_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    covered_services: tuple[str, ...] = Field(max_length=20)
    window: ConnectorWindowV1
    binding_kind: ConnectorBindingKindV0232
    binding_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_closed_binding(self) -> "ConnectorEvidenceBindingV0232":
        _require_canonical_strings(
            self.requested_services,
            field_name="requested services",
        )
        _require_canonical_strings(
            self.covered_services,
            field_name="covered services",
        )
        if (
            self.source is not EvidenceSourceV22.TRACES
            and not set(self.covered_services).issubset(self.requested_services)
        ):
            raise ValueError("covered services exceed requested services")
        _require_seal(
            self,
            field_name="binding_sha256",
            label="connector evidence binding",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "ConnectorEvidenceBindingV0232":
        normalized = {
            **payload,
            "requested_services": tuple(sorted(set(payload["requested_services"]))),
            "covered_services": tuple(sorted(set(payload["covered_services"]))),
        }
        return _build_sealed(
            cls,
            schema_version="ecomsre.product.connector-evidence-binding.v0232",
            seal_field="binding_sha256",
            payload=normalized,
        )


def build_connector_evidence_binding_v0232(
    *,
    incident_id: str,
    action_id: str,
    config: ConnectorConfigV1,
    context: ConnectorQueryContextV1,
    component_result: ConnectorQueryResultV1,
    combined_result: ConnectorQueryResultV1,
    binding_kind: ConnectorBindingKindV0232 | str,
    binding_payload_sha256: str,
) -> ConnectorEvidenceBindingV0232:
    connector_config_sha256 = semantic_sha256_v22(config.model_dump(mode="json"))
    query_context_sha256 = semantic_sha256_v22(context.model_dump(mode="json"))
    identity_sha256 = semantic_sha256_v22(
        {
            "incident_id": incident_id,
            "action_id": action_id,
            "connector_name": config.name,
            "connector_kind": config.kind.value,
            "connector_config_sha256": connector_config_sha256,
            "query_context_sha256": query_context_sha256,
            "component_result_sha256": component_result.result_sha256,
            "combined_result_sha256": combined_result.result_sha256,
        }
    )
    return ConnectorEvidenceBindingV0232.build(
        binding_id=f"binding:v0232:{identity_sha256[:24]}",
        incident_id=incident_id,
        action_id=action_id,
        source=component_result.source,
        connector_name=config.name,
        connector_kind=config.kind,
        environment_id=context.environment_id,
        connector_config_sha256=connector_config_sha256,
        query_context_sha256=query_context_sha256,
        component_result_sha256=component_result.result_sha256,
        combined_result_sha256=combined_result.result_sha256,
        requested_services=component_result.requested_services,
        covered_services=component_result.covered_services,
        window=component_result.window,
        binding_kind=binding_kind,
        binding_payload_sha256=binding_payload_sha256,
    )


class OpenSearchProfileEvidenceBindingV0232(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-profile-evidence-binding.v0232"
    ] = "ecomsre.product.opensearch-profile-evidence-binding.v0232"
    active_profile_id: Literal["product-v0222-operator-selected-profile"]
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    selected_candidate_alias: Literal["P01"]
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    operator_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_diagnostics_sha256: str = Field(pattern=_SHA256_PATTERN)
    accepted_record_count: int = Field(ge=0, le=200)
    rejected_record_count: int = Field(ge=0, le=200)
    rejection_reason_codes: tuple[str, ...] = Field(max_length=200)
    connector_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_window: ConnectorWindowV1
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_p01_profile(
        self,
    ) -> "OpenSearchProfileEvidenceBindingV0232":
        if (
            self.active_profile_id != _ACTIVE_PROFILE_ID_V0232
            or self.active_profile_sha256 != ACTIVE_PROFILE_SHA256_V023
            or self.profile_binding_sha256 != ACTIVE_PROFILE_BINDING_SHA256_V023
            or self.selected_candidate_alias != "P01"
            or self.candidate_set_sha256 != CANDIDATE_SET_SHA256_V023
            or self.operator_decision_sha256 != OPERATOR_DECISION_SHA256_V023
        ):
            raise ValueError("frozen P01 profile differs")
        _require_canonical_strings(
            self.rejection_reason_codes,
            field_name="rejection reason codes",
        )
        if bool(self.rejected_record_count) != bool(self.rejection_reason_codes):
            raise ValueError("OpenSearch rejection reasons differ")
        _require_seal(
            self,
            field_name="binding_sha256",
            label="OpenSearch profile evidence binding",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "OpenSearchProfileEvidenceBindingV0232":
        normalized = {
            **payload,
            "rejection_reason_codes": tuple(
                sorted(set(payload["rejection_reason_codes"]))
            ),
        }
        return _build_sealed(
            cls,
            schema_version=(
                "ecomsre.product.opensearch-profile-evidence-binding.v0232"
            ),
            seal_field="binding_sha256",
            payload=normalized,
        )


def build_opensearch_profile_evidence_binding_v0232(
    *,
    profile_binding: OpenSearchConnectorProfileBindingV023,
    diagnostics: OpenSearchConnectorDiagnosticsV023,
    connector_result: ConnectorQueryResultV1,
) -> OpenSearchProfileEvidenceBindingV0232:
    if (
        connector_result.source is not EvidenceSourceV22.LOGS
        or connector_result.status
        not in {
            ReadSourceStatusV22.SUCCESS_EMPTY,
            ReadSourceStatusV22.SUCCESS_NONEMPTY,
        }
        or diagnostics.last_query_status != connector_result.status.value
        or diagnostics.last_normalization_status != connector_result.status.value
        or diagnostics.last_safe_error_code is not None
        or diagnostics.last_accepted_record_count != len(connector_result.records)
        or (
            connector_result.status is ReadSourceStatusV22.SUCCESS_EMPTY
            and any(
                (
                    diagnostics.last_sampled_record_count,
                    diagnostics.last_accepted_record_count,
                    diagnostics.last_rejected_record_count,
                )
            )
        )
    ):
        raise ValueError("OpenSearch diagnostics/result semantics differ")
    if (
        profile_binding.profile_status != "ACTIVE"
        or profile_binding.profile_id != _ACTIVE_PROFILE_ID_V0232
        or diagnostics.profile_binding_sha256 != profile_binding.binding_sha256
        or diagnostics.profile_sha256 != profile_binding.profile_sha256
    ):
        raise ValueError("OpenSearch active profile binding differs")
    return OpenSearchProfileEvidenceBindingV0232.build(
        active_profile_id=profile_binding.profile_id,
        active_profile_sha256=profile_binding.profile_sha256,
        profile_binding_sha256=profile_binding.binding_sha256,
        selected_candidate_alias=profile_binding.selected_candidate_alias,
        candidate_set_sha256=profile_binding.candidate_set_sha256,
        operator_decision_sha256=profile_binding.operator_decision_sha256,
        query_diagnostics_sha256=diagnostics.diagnostics_sha256,
        accepted_record_count=diagnostics.last_accepted_record_count,
        rejected_record_count=diagnostics.last_rejected_record_count,
        rejection_reason_codes=tuple(diagnostics.last_rejection_codes_by_count),
        connector_result_sha256=connector_result.result_sha256,
        query_window=connector_result.window,
    )


class RuntimeSnapshotEvidenceBindingV0232(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.runtime-snapshot-evidence-binding.v0232"
    ] = "ecomsre.product.runtime-snapshot-evidence-binding.v0232"
    runtime_snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_snapshot_observed_at: datetime
    runtime_snapshot_environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    runtime_snapshot_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    pilot_runtime_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    read_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    connector_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    maximum_age_seconds: int = Field(ge=1, le=600)
    age_at_query_seconds: float = Field(ge=0, le=600, allow_inf_nan=False)
    requested_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    covered_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    connector_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_window: ConnectorWindowV1
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_fresh_checkout_snapshot(
        self,
    ) -> "RuntimeSnapshotEvidenceBindingV0232":
        _require_utc(
            self.runtime_snapshot_observed_at,
            field_name="runtime_snapshot_observed_at",
        )
        if self.requested_services != ("checkout",) or self.covered_services != (
            "checkout",
        ):
            raise ValueError("Runtime snapshot checkout coverage differs")
        if self.runtime_snapshot_authority_sha256 != self.connector_binding_sha256:
            raise ValueError("Runtime snapshot authority differs")
        if not (
            self.query_window.started_at
            <= self.runtime_snapshot_observed_at
            <= self.query_window.ended_at
        ):
            raise ValueError("Runtime snapshot is outside the query window")
        expected_age = (
            self.query_window.ended_at - self.runtime_snapshot_observed_at
        ).total_seconds()
        if abs(self.age_at_query_seconds - expected_age) > 1e-6:
            raise ValueError("Runtime snapshot age at query differs")
        if self.age_at_query_seconds > self.maximum_age_seconds:
            raise ValueError("Runtime snapshot exceeds maximum age")
        _require_seal(
            self,
            field_name="binding_sha256",
            label="Runtime snapshot evidence binding",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "RuntimeSnapshotEvidenceBindingV0232":
        normalized = {
            **payload,
            "requested_services": tuple(sorted(set(payload["requested_services"]))),
            "covered_services": tuple(sorted(set(payload["covered_services"]))),
        }
        return _build_sealed(
            cls,
            schema_version=("ecomsre.product.runtime-snapshot-evidence-binding.v0232"),
            seal_field="binding_sha256",
            payload=normalized,
        )


def build_runtime_snapshot_evidence_binding_v0232(
    *,
    snapshot: PilotRuntimeSnapshotV02 | None,
    config: ConnectorConfigV1,
    runtime_authority: PilotRuntimeAuthorityV02,
    connector_result: ConnectorQueryResultV1,
    formal_traffic_started_at: datetime,
    diagnosis_observed_at: datetime,
) -> RuntimeSnapshotEvidenceBindingV0232:
    _require_utc(formal_traffic_started_at, field_name="formal_traffic_started_at")
    _require_utc(diagnosis_observed_at, field_name="diagnosis_observed_at")
    settings = PilotRuntimeConnectorSettingsV02.model_validate(config.settings)
    runtime_records = tuple(
        item
        for item in connector_result.records
        if isinstance(item, RuntimeRecordV22)
    )
    if (
        snapshot is None
        or config.kind is not ConnectorKindV1.PILOT_RUNTIME
        or snapshot.environment_id != runtime_authority.environment_id
        or snapshot.authority_sha256 != settings.authority_sha256
        or snapshot.authority_sha256 != runtime_authority.connector_binding_sha256
        or not runtime_authority.admits(
            environment_id=snapshot.environment_id,
            services=connector_result.requested_services,
        )
        or connector_result.source is not EvidenceSourceV22.RUNTIME
        or connector_result.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
        or connector_result.requested_services != ("checkout",)
        or connector_result.covered_services != ("checkout",)
        or len(runtime_records) != 1
        or len(runtime_records) != len(connector_result.records)
        or runtime_records[0].service != "checkout"
        or runtime_records[0].state is not RuntimeStateV22.RUNNING
        or runtime_records[0].healthy is not True
        or runtime_records[0].restart_count != 0
        or snapshot.observed_at < formal_traffic_started_at
        or snapshot.observed_at > diagnosis_observed_at
        or connector_result.window.ended_at != diagnosis_observed_at
    ):
        raise ValueError("Runtime snapshot/result authority semantics differ")
    return RuntimeSnapshotEvidenceBindingV0232.build(
        runtime_snapshot_sha256=snapshot.snapshot_sha256,
        runtime_snapshot_observed_at=snapshot.observed_at,
        runtime_snapshot_environment_id=snapshot.environment_id,
        runtime_snapshot_authority_sha256=snapshot.authority_sha256,
        pilot_runtime_authority_sha256=runtime_authority.pilot_authority_sha256,
        read_authority_sha256=runtime_authority.read_authority.authority_sha256,
        connector_binding_sha256=runtime_authority.connector_binding_sha256,
        maximum_age_seconds=settings.maximum_age_seconds,
        age_at_query_seconds=(
            connector_result.window.ended_at - snapshot.observed_at
        ).total_seconds(),
        requested_services=connector_result.requested_services,
        covered_services=connector_result.covered_services,
        connector_result_sha256=connector_result.result_sha256,
        query_window=connector_result.window,
    )


class CapabilityLimitationCandidateV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.capability-limitation-candidate.v0232"] = (
        "ecomsre.product.capability-limitation-candidate.v0232"
    )
    limitation_code: str = Field(pattern=_SAFE_CODE_PATTERN)
    category: CapabilityLimitationCategoryV0232
    source: EvidenceSourceV22
    capability_status: SourceCapabilityStatusV1
    connector_action_id: str | None = Field(
        default=None,
        pattern=r"^a:[a-z0-9][a-z0-9:+-]*$",
    )
    connector_result_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    safe_error_code: str | None = Field(
        default=None,
        pattern=_SAFE_CODE_PATTERN,
    )
    coverage_required_services: tuple[str, ...] = Field(
        min_length=1,
        max_length=20,
    )
    coverage_observed_services: tuple[str, ...] = Field(max_length=20)
    candidate_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_typed_candidate(self) -> "CapabilityLimitationCandidateV0232":
        _require_canonical_strings(
            self.coverage_required_services,
            field_name="coverage required services",
        )
        _require_canonical_strings(
            self.coverage_observed_services,
            field_name="coverage observed services",
        )
        if not set(self.coverage_observed_services).issubset(
            self.coverage_required_services
        ):
            raise ValueError("observed capability coverage exceeds required coverage")
        has_action = self.connector_action_id is not None
        has_result = self.connector_result_sha256 is not None
        if has_action != has_result:
            raise ValueError("connector candidate action/result binding differs")
        if self.category is CapabilityLimitationCategoryV0232.SOURCE_UNAVAILABLE and (
            self.capability_status is not SourceCapabilityStatusV1.UNAVAILABLE
            or self.coverage_observed_services
        ):
            raise ValueError("source unavailable candidate differs")
        if self.category is CapabilityLimitationCategoryV0232.SOURCE_PARTIAL and (
            self.capability_status is not SourceCapabilityStatusV1.PARTIAL
            or not self.coverage_observed_services
            or self.coverage_observed_services == self.coverage_required_services
        ):
            raise ValueError("source partial candidate differs")
        if self.category is CapabilityLimitationCategoryV0232.QUERY_FAILURE and (
            not has_action or self.safe_error_code is None
        ):
            raise ValueError("query failure evidence differs")
        if self.category is CapabilityLimitationCategoryV0232.COVERAGE_GAP and (
            not has_action
            or self.coverage_observed_services == self.coverage_required_services
        ):
            raise ValueError("coverage gap is absent")
        if (
            self.category
            is CapabilityLimitationCategoryV0232.RUNTIME_AUTHORITY_UNAVAILABLE
            and (
                self.source is not EvidenceSourceV22.RUNTIME
                or not has_action
                or self.safe_error_code is None
            )
        ):
            raise ValueError("Runtime authority limitation candidate differs")
        _require_seal(
            self,
            field_name="candidate_sha256",
            label="capability limitation candidate",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "CapabilityLimitationCandidateV0232":
        normalized = {
            **payload,
            "coverage_required_services": tuple(
                sorted(set(payload["coverage_required_services"]))
            ),
            "coverage_observed_services": tuple(
                sorted(set(payload["coverage_observed_services"]))
            ),
        }
        return _build_sealed(
            cls,
            schema_version=("ecomsre.product.capability-limitation-candidate.v0232"),
            seal_field="candidate_sha256",
            payload=normalized,
        )


class CapabilityEvidenceObservationV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.capability-evidence-observation.v0232"] = (
        "ecomsre.product.capability-evidence-observation.v0232"
    )
    source: EvidenceSourceV22
    capability_matrix_sha256: str = Field(pattern=_SHA256_PATTERN)
    capability_status: SourceCapabilityStatusV1
    required_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    available_services: tuple[str, ...] = Field(max_length=20)
    reason_code: str = Field(pattern=_SAFE_CODE_PATTERN)
    observation_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_observation(self) -> "CapabilityEvidenceObservationV0232":
        _require_canonical_strings(
            self.required_services,
            field_name="required services",
        )
        _require_canonical_strings(
            self.available_services,
            field_name="available services",
        )
        if not set(self.available_services).issubset(self.required_services):
            raise ValueError("available services exceed required services")
        if self.capability_status is SourceCapabilityStatusV1.AVAILABLE and (
            self.available_services != self.required_services
        ):
            raise ValueError("available capability observation is incomplete")
        if self.capability_status is SourceCapabilityStatusV1.PARTIAL and (
            not self.available_services
            or self.available_services == self.required_services
        ):
            raise ValueError("partial capability observation differs")
        if (
            self.capability_status is SourceCapabilityStatusV1.UNAVAILABLE
            and self.available_services
        ):
            raise ValueError("unavailable capability observation has services")
        _require_seal(
            self,
            field_name="observation_sha256",
            label="capability evidence observation",
        )
        return self

    @property
    def evidence_ref(self) -> str:
        return (
            f"capability:v0232:{self.source.value.lower()}:"
            f"{self.observation_sha256[:24]}"
        )

    @classmethod
    def build(cls, **payload: Any) -> "CapabilityEvidenceObservationV0232":
        normalized = {
            **payload,
            "required_services": tuple(sorted(set(payload["required_services"]))),
            "available_services": tuple(sorted(set(payload["available_services"]))),
        }
        return _build_sealed(
            cls,
            schema_version=("ecomsre.product.capability-evidence-observation.v0232"),
            seal_field="observation_sha256",
            payload=normalized,
        )


class CapabilityLimitationBindingV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.capability-limitation-binding.v0232"] = (
        "ecomsre.product.capability-limitation-binding.v0232"
    )
    limitation_code: str = Field(pattern=_SAFE_CODE_PATTERN)
    category: CapabilityLimitationCategoryV0232
    source: EvidenceSourceV22
    evidence_ref: str = Field(pattern=_REFERENCE_PATTERN)
    connector_result_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    capability_observation_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    safe_error_code: str | None = Field(
        default=None,
        pattern=_SAFE_CODE_PATTERN,
    )
    coverage_status: CapabilityCoverageStatusV0232
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_evidence_backing(
        self,
    ) -> "CapabilityLimitationBindingV0232":
        if (self.connector_result_sha256 is None) == (
            self.capability_observation_sha256 is None
        ):
            raise ValueError("limitation requires exactly one evidence backing")
        if (
            self.category
            in {
                CapabilityLimitationCategoryV0232.QUERY_FAILURE,
                CapabilityLimitationCategoryV0232.COVERAGE_GAP,
            }
            and self.connector_result_sha256 is None
        ):
            raise ValueError("connector limitation lacks connector result")
        if (
            self.category is CapabilityLimitationCategoryV0232.QUERY_FAILURE
            and self.safe_error_code is None
        ):
            raise ValueError("query failure limitation lacks safe error")
        if (
            self.category is CapabilityLimitationCategoryV0232.SOURCE_UNAVAILABLE
            and self.coverage_status is not CapabilityCoverageStatusV0232.NONE
        ):
            raise ValueError("unavailable limitation coverage differs")
        if (
            self.category
            in {
                CapabilityLimitationCategoryV0232.SOURCE_PARTIAL,
                CapabilityLimitationCategoryV0232.COVERAGE_GAP,
            }
            and self.coverage_status is CapabilityCoverageStatusV0232.COMPLETE
        ):
            raise ValueError("partial limitation coverage differs")
        if (
            self.category
            is CapabilityLimitationCategoryV0232.RUNTIME_AUTHORITY_UNAVAILABLE
            and self.source is not EvidenceSourceV22.RUNTIME
        ):
            raise ValueError("Runtime authority limitation source differs")
        _require_seal(
            self,
            field_name="binding_sha256",
            label="capability limitation binding",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "CapabilityLimitationBindingV0232":
        return _build_sealed(
            cls,
            schema_version=("ecomsre.product.capability-limitation-binding.v0232"),
            seal_field="binding_sha256",
            payload=payload,
        )


class DiagnosisDecisionTraceV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.diagnosis-decision-trace.v0232"] = (
        "ecomsre.product.diagnosis-decision-trace.v0232"
    )
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    known_admission_status: KnownAdmissionStatusV0232
    extension_match_count: int = Field(ge=0)
    no_incident_admissible: bool
    required_coverage_satisfied: bool
    failed_sources: tuple[EvidenceSourceV22, ...]
    novelty_gate_disposition: NoveltyDispositionV23 | None
    novelty_gate_reason_codes: tuple[str, ...]
    residual_anomaly_ids: tuple[str, ...]
    trace_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_decision_trace(self) -> "DiagnosisDecisionTraceV0232":
        expected_sources = tuple(
            sorted(set(self.failed_sources), key=lambda item: item.value)
        )
        if self.failed_sources != expected_sources:
            raise ValueError("failed sources are not canonical")
        _require_canonical_strings(
            self.novelty_gate_reason_codes,
            field_name="novelty gate reason codes",
        )
        _require_canonical_strings(
            self.residual_anomaly_ids,
            field_name="residual anomaly IDs",
        )
        _require_seal(
            self,
            field_name="trace_sha256",
            label="diagnosis decision trace",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "DiagnosisDecisionTraceV0232":
        normalized = {
            **payload,
            "failed_sources": tuple(
                sorted(set(payload["failed_sources"]), key=lambda item: item.value)
            ),
            "novelty_gate_reason_codes": tuple(
                sorted(set(payload["novelty_gate_reason_codes"]))
            ),
            "residual_anomaly_ids": tuple(sorted(set(payload["residual_anomaly_ids"]))),
        }
        return _build_sealed(
            cls,
            schema_version="ecomsre.product.diagnosis-decision-trace.v0232",
            seal_field="trace_sha256",
            payload=normalized,
        )


class DiagnosisEvidenceIndexV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.diagnosis-evidence-index.v0232"] = (
        "ecomsre.product.diagnosis-evidence-index.v0232"
    )
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    evidence_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    all_object_refs: tuple[str, ...]
    all_object_sha256_by_ref: dict[str, str]
    linked_support_refs: tuple[str, ...]
    linked_contradiction_refs: tuple[str, ...]
    successful_source_refs: tuple[str, ...]
    failed_source_refs: tuple[str, ...]
    open_search_profile_binding_ref: str | None = Field(
        default=None,
        pattern=_REFERENCE_PATTERN,
    )
    runtime_snapshot_binding_ref: str | None = Field(
        default=None,
        pattern=_REFERENCE_PATTERN,
    )
    capability_limitation_bindings: tuple[
        CapabilityLimitationBindingV0232,
        ...,
    ] = Field(max_length=40)
    decision_trace_sha256: str = Field(pattern=_SHA256_PATTERN)
    index_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_closed_index(self) -> "DiagnosisEvidenceIndexV0232":
        for field_name in (
            "all_object_refs",
            "linked_support_refs",
            "linked_contradiction_refs",
            "successful_source_refs",
            "failed_source_refs",
        ):
            _require_canonical_strings(
                getattr(self, field_name),
                field_name=field_name.replace("_", " "),
            )
        if self.all_object_sha256_by_ref != dict(
            sorted(self.all_object_sha256_by_ref.items())
        ) or set(self.all_object_sha256_by_ref) != set(self.all_object_refs):
            raise ValueError("Evidence Index object ref/SHA map differs")
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in self.all_object_sha256_by_ref.values()
        ):
            raise ValueError("Evidence Index object SHA differs")
        all_refs = set(self.all_object_refs)
        support = set(self.linked_support_refs)
        contradiction = set(self.linked_contradiction_refs)
        successful = set(self.successful_source_refs)
        failed = set(self.failed_source_refs)
        if support.intersection(contradiction):
            raise ValueError("linked support and contradiction refs overlap")
        if not support.union(contradiction).issubset(all_refs):
            raise ValueError("linked diagnosis refs are unresolved")
        if successful.intersection(failed):
            raise ValueError("successful and failed refs overlap")
        if not successful.union(failed).issubset(all_refs):
            raise ValueError("source disposition refs are unresolved")
        specialized_refs = tuple(
            reference
            for reference in (
                self.open_search_profile_binding_ref,
                self.runtime_snapshot_binding_ref,
            )
            if reference is not None
        )
        if len(specialized_refs) != len(set(specialized_refs)) or not set(
            specialized_refs
        ).issubset(successful):
            raise ValueError("connector provenance refs differ")
        binding_keys = tuple(
            item.limitation_code for item in self.capability_limitation_bindings
        )
        expected_bindings = tuple(
            sorted(
                self.capability_limitation_bindings,
                key=lambda item: (item.limitation_code, item.evidence_ref),
            )
        )
        if self.capability_limitation_bindings != expected_bindings or len(
            binding_keys
        ) != len(set(binding_keys)):
            raise ValueError("capability limitation bindings are not unique")
        if any(
            item.evidence_ref not in all_refs
            for item in self.capability_limitation_bindings
        ):
            raise ValueError("capability limitation evidence ref is unresolved")
        _require_seal(
            self,
            field_name="index_sha256",
            label="diagnosis evidence index",
        )
        return self

    @classmethod
    def build(cls, **payload: Any) -> "DiagnosisEvidenceIndexV0232":
        bindings = tuple(
            sorted(
                (
                    item
                    if isinstance(item, CapabilityLimitationBindingV0232)
                    else CapabilityLimitationBindingV0232.model_validate(item)
                    for item in payload["capability_limitation_bindings"]
                ),
                key=lambda item: (item.limitation_code, item.evidence_ref),
            )
        )
        normalized = {
            **payload,
            "all_object_refs": tuple(sorted(set(payload["all_object_refs"]))),
            "all_object_sha256_by_ref": dict(
                sorted(dict(payload["all_object_sha256_by_ref"]).items())
            ),
            "linked_support_refs": tuple(sorted(set(payload["linked_support_refs"]))),
            "linked_contradiction_refs": tuple(
                sorted(set(payload["linked_contradiction_refs"]))
            ),
            "successful_source_refs": tuple(
                sorted(set(payload["successful_source_refs"]))
            ),
            "failed_source_refs": tuple(sorted(set(payload["failed_source_refs"]))),
            "capability_limitation_bindings": bindings,
        }
        return _build_sealed(
            cls,
            schema_version="ecomsre.product.diagnosis-evidence-index.v0232",
            seal_field="index_sha256",
            payload=normalized,
        )


__all__ = (
    "CapabilityEvidenceObservationV0232",
    "CapabilityLimitationBindingV0232",
    "CapabilityLimitationCandidateV0232",
    "ConnectorEvidenceBindingV0232",
    "DiagnosisDecisionTraceV0232",
    "DiagnosisEvidenceIndexV0232",
    "OpenSearchProfileEvidenceBindingV0232",
    "RuntimeSnapshotEvidenceBindingV0232",
    "build_connector_evidence_binding_v0232",
    "build_opensearch_profile_evidence_binding_v0232",
    "build_runtime_snapshot_evidence_binding_v0232",
)
