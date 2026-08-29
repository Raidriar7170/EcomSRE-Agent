"""First-class Product binding for the active v0.2.2.2 OpenSearch profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OpenSearchNormalizationProfileV0222,
    OpenSearchProfileStatusV0222,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchBatchNormalizationV022,
    OpenSearchBatchStatusV022,
    OpenSearchExtractionRuleV022,
    OpenSearchMessageExtractionV022,
    OpenSearchNormalizationProfileV022,
    OpenSearchSeverityExtractionV022,
    OpenSearchTimestampExtractionV022,
)
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    EnvironmentCreateV1,
    ProductModelV1,
)


PROFILE_BINDING_PASS_V023 = "ECOMSRE_PRODUCT_V023_PROFILE_BINDING_PASS"
PROFILE_BINDING_BLOCKED_V023 = "BLOCKED_ECOMSRE_PRODUCT_V023_PROFILE_BINDING"
ACTIVE_PROFILE_SHA256_V023 = (
    "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
)
ACTIVE_PROFILE_BINDING_SHA256_V023 = (
    "e35903cfd93b28edf4244c00e6f589788353817e7c9b515ba667360be14421e2"
)
CANDIDATE_SET_SHA256_V023 = (
    "f3aeaf272ab199c1284238c9e7785ec89f46b1cb54ad1608188a052c27f9d4de"
)
OPERATOR_DECISION_SHA256_V023 = (
    "51effb280e9390d5619bf18fed80c2c158214db2dd98dcfce3634275125b8b5e"
)
CAPTURE_BUNDLE_SHA256_V023 = (
    "4084941d8368c4f74ec2db95ac2215f36c9531367f9904b9b90cd653bceeea94"
)
BASELINE_HANDOFF_SHA256_V023 = (
    "fee46e6f335f106f365c3c0c85bb1cf8e7fb0b7cbf00289f5555ec84ea0cdaa7"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3 profile binding input must be an object")
    return payload


class OpenSearchConnectorProfileBindingV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-connector-profile-binding.v023"
    ] = "ecomsre.product.opensearch-connector-profile-binding.v023"
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_status: Literal["ACTIVE"]
    profile_source: Literal["CAPTURE_FIRST_OPERATOR_SELECTION_V0222"]
    selected_candidate_alias: Literal["P01"]
    selected_candidate_sha256: str = Field(pattern=_SHA256_PATTERN)
    capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    operator_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_handoff_sha256: str = Field(pattern=_SHA256_PATTERN)
    index_pattern: str = Field(min_length=1, max_length=255)
    mapping_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    field_caps_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    structural_sample_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    timestamp_extraction: OpenSearchTimestampExtractionV022
    service_extraction: OpenSearchExtractionRuleV022
    service_source_field: str = Field(min_length=1, max_length=255)
    service_query_field: str = Field(min_length=1, max_length=255)
    severity_extraction: OpenSearchSeverityExtractionV022
    message_extraction: OpenSearchMessageExtractionV022
    trace_id_extraction: OpenSearchExtractionRuleV022 | None
    message_projection_policy: Literal["AS_OBSERVED", "OBSERVER_SYMPTOM_V1"]
    maximum_record_rejection_fraction: float = Field(
        ge=0,
        le=0.25,
        allow_inf_nan=False,
    )
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_active_profile(self) -> "OpenSearchConnectorProfileBindingV023":
        active = self.as_v0222()
        if (
            active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
            or active.profile_sha256 != self.profile_sha256
            or self.profile_sha256 != ACTIVE_PROFILE_SHA256_V023
            or self.selected_candidate_alias != "P01"
            or self.candidate_set_sha256 != CANDIDATE_SET_SHA256_V023
            or self.operator_decision_sha256 != OPERATOR_DECISION_SHA256_V023
            or self.capture_bundle_sha256 != CAPTURE_BUNDLE_SHA256_V023
            or self.baseline_handoff_sha256 != BASELINE_HANDOFF_SHA256_V023
            or self.binding_sha256 != ACTIVE_PROFILE_BINDING_SHA256_V023
        ):
            raise ValueError("Product v0.2.3 frozen OpenSearch binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("Product v0.2.3 profile binding digest differs")
        return self

    @classmethod
    def from_active_profile(
        cls,
        active: OpenSearchNormalizationProfileV0222,
        *,
        baseline_handoff_sha256: str,
    ) -> "OpenSearchConnectorProfileBindingV023":
        if active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE:
            raise ValueError("Product v0.2.3 requires an ACTIVE OpenSearch profile")
        body: dict[str, Any] = {
            "schema_version": (
                "ecomsre.product.opensearch-connector-profile-binding.v023"
            ),
            "profile_id": active.profile_id,
            "profile_sha256": active.profile_sha256,
            "profile_status": active.profile_status.value,
            "profile_source": active.profile_source,
            "selected_candidate_alias": active.selected_candidate_alias,
            "selected_candidate_sha256": active.selected_candidate_sha256,
            "capture_bundle_sha256": active.capture_bundle_sha256,
            "candidate_set_sha256": active.candidate_set_sha256,
            "operator_decision_sha256": active.operator_decision_sha256,
            "baseline_handoff_sha256": baseline_handoff_sha256,
            "index_pattern": active.index_pattern,
            "mapping_response_sha256": active.mapping_response_sha256,
            "field_caps_response_sha256": active.field_caps_response_sha256,
            "structural_sample_response_sha256": (
                active.structural_sample_response_sha256
            ),
            "timestamp_extraction": active.timestamp_extraction,
            "service_extraction": active.service_extraction,
            "service_source_field": active.service_source_field,
            "service_query_field": active.service_query_field,
            "severity_extraction": active.severity_extraction,
            "message_extraction": active.message_extraction,
            "trace_id_extraction": active.trace_id_extraction,
            "message_projection_policy": active.message_projection_policy,
            "maximum_record_rejection_fraction": (
                active.maximum_record_rejection_fraction
            ),
        }
        draft = cls.model_construct(**body, binding_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"binding_sha256"})
        return cls.model_validate(
            {**serialized, "binding_sha256": semantic_sha256_v22(serialized)}
        )

    def as_v0222(self) -> OpenSearchNormalizationProfileV0222:
        return OpenSearchNormalizationProfileV0222.model_validate(
            {
                "schema_version": (
                    "ecomsre.product.opensearch-normalization-profile.v0222"
                ),
                "profile_id": self.profile_id,
                "profile_status": self.profile_status,
                "index_pattern": self.index_pattern,
                "capture_bundle_sha256": self.capture_bundle_sha256,
                "candidate_set_sha256": self.candidate_set_sha256,
                "operator_decision_sha256": self.operator_decision_sha256,
                "selected_candidate_alias": self.selected_candidate_alias,
                "selected_candidate_sha256": self.selected_candidate_sha256,
                "mapping_response_sha256": self.mapping_response_sha256,
                "field_caps_response_sha256": self.field_caps_response_sha256,
                "structural_sample_response_sha256": (
                    self.structural_sample_response_sha256
                ),
                "timestamp_extraction": self.timestamp_extraction,
                "service_extraction": self.service_extraction,
                "service_source_field": self.service_source_field,
                "service_query_field": self.service_query_field,
                "severity_extraction": self.severity_extraction,
                "message_extraction": self.message_extraction,
                "trace_id_extraction": self.trace_id_extraction,
                "message_projection_policy": self.message_projection_policy,
                "maximum_record_rejection_fraction": (
                    self.maximum_record_rejection_fraction
                ),
                "profile_source": self.profile_source,
                "profile_sha256": self.profile_sha256,
            }
        )

    def as_normalization_profile(self) -> OpenSearchNormalizationProfileV022:
        return self.as_v0222().as_v022()

    @property
    def timestamp_query_field(self) -> str:
        return self.timestamp_extraction.extraction.paths[0]

    @property
    def severity_field(self) -> str:
        return self.severity_extraction.extraction.paths[0]

    @property
    def message_field(self) -> str:
        return self.message_extraction.extraction.paths[0]

    @property
    def trace_id_field(self) -> str | None:
        if self.trace_id_extraction is None:
            return None
        return self.trace_id_extraction.paths[0]

    @property
    def projected_source_fields(self) -> tuple[str, ...]:
        ordered = (
            *self.timestamp_extraction.extraction.paths,
            *self.service_extraction.paths,
            *self.severity_extraction.extraction.paths,
            *self.message_extraction.extraction.paths,
            *(
                ()
                if self.trace_id_extraction is None
                else self.trace_id_extraction.paths
            ),
        )
        return tuple(dict.fromkeys(ordered))


class OpenSearchConnectorDiagnosticsV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-connector-diagnostics.v023"
    ] = "ecomsre.product.opensearch-connector-diagnostics.v023"
    terminal: Literal["ECOMSRE_PRODUCT_V023_PROFILE_BINDING_PASS"]
    settings_mode: Literal["PROFILE_BOUND"]
    profile_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    index_pattern: str
    timestamp_query_field: str
    service_source_field: str
    service_query_field: str
    severity_field: str
    message_field: str
    trace_id_field: str | None
    maximum_record_rejection_fraction: float = Field(ge=0, le=0.25)
    last_query_status: Literal[
        "SUCCESS_EMPTY",
        "SUCCESS_NONEMPTY",
        "FAILURE_SCHEMA",
        "FAILURE_TIMEOUT",
        "FAILURE_UNAVAILABLE",
    ] | None
    last_normalization_status: Literal[
        "SUCCESS_EMPTY",
        "SUCCESS_NONEMPTY",
        "PARTIAL_SCHEMA",
        "FAILURE_SCHEMA",
    ] | None
    last_query_batch_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    last_safe_error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{2,119}$",
    )
    last_sampled_record_count: int = Field(ge=0, le=200)
    last_accepted_record_count: int = Field(ge=0, le=200)
    last_rejected_record_count: int = Field(ge=0, le=200)
    last_rejection_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    last_rejection_codes_by_count: dict[str, int]
    diagnostics_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_diagnostics(self) -> "OpenSearchConnectorDiagnosticsV023":
        if (
            self.profile_binding_sha256 != ACTIVE_PROFILE_BINDING_SHA256_V023
            or self.profile_sha256 != ACTIVE_PROFILE_SHA256_V023
        ):
            raise ValueError("Product v0.2.3 connector diagnostics profile differs")
        if self.last_sampled_record_count != (
            self.last_accepted_record_count + self.last_rejected_record_count
        ):
            raise ValueError("Product v0.2.3 connector diagnostic counts differ")
        has_batch = self.last_query_batch_sha256 is not None
        if has_batch != (self.last_normalization_status is not None):
            raise ValueError("Product v0.2.3 normalization diagnostic binding differs")
        failure_statuses = {
            ReadSourceStatusV22.FAILURE_SCHEMA.value,
            ReadSourceStatusV22.FAILURE_TIMEOUT.value,
            ReadSourceStatusV22.FAILURE_UNAVAILABLE.value,
        }
        if self.last_query_status is None:
            if any(
                (
                    has_batch,
                    self.last_safe_error_code is not None,
                    self.last_sampled_record_count,
                    self.last_accepted_record_count,
                    self.last_rejected_record_count,
                    self.last_rejection_fraction,
                    len(self.last_rejection_codes_by_count),
                )
            ):
                raise ValueError("Product v0.2.3 empty connector diagnostics differ")
        elif self.last_query_status in failure_statuses:
            if self.last_safe_error_code is None:
                raise ValueError("Product v0.2.3 connector failure diagnostic differs")
        elif self.last_safe_error_code is not None or not has_batch:
            raise ValueError("Product v0.2.3 connector success diagnostic differs")
        if not has_batch and any(
            (
                self.last_sampled_record_count,
                self.last_accepted_record_count,
                self.last_rejected_record_count,
                self.last_rejection_fraction,
                len(self.last_rejection_codes_by_count),
            )
        ):
            raise ValueError("Product v0.2.3 batchless diagnostics differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"diagnostics_sha256"})
        )
        if self.diagnostics_sha256 != expected:
            raise ValueError("Product v0.2.3 connector diagnostics digest differs")
        return self

    @classmethod
    def build(
        cls,
        binding: OpenSearchConnectorProfileBindingV023,
        *,
        batch: OpenSearchBatchNormalizationV022 | None = None,
        failure_status: ReadSourceStatusV22 | None = None,
        safe_error_code: str | None = None,
    ) -> "OpenSearchConnectorDiagnosticsV023":
        if (failure_status is None) != (safe_error_code is None):
            raise ValueError("Product v0.2.3 connector failure state differs")
        if failure_status is not None and failure_status not in {
            ReadSourceStatusV22.FAILURE_SCHEMA,
            ReadSourceStatusV22.FAILURE_TIMEOUT,
            ReadSourceStatusV22.FAILURE_UNAVAILABLE,
        }:
            raise ValueError("Product v0.2.3 connector failure status differs")
        if batch is not None and (
            failure_status is None
        ) != (
            batch.status
            in {
                OpenSearchBatchStatusV022.SUCCESS_EMPTY,
                OpenSearchBatchStatusV022.SUCCESS_NONEMPTY,
            }
        ):
            raise ValueError("Product v0.2.3 batch disposition differs")
        body: dict[str, Any] = {
            "schema_version": (
                "ecomsre.product.opensearch-connector-diagnostics.v023"
            ),
            "terminal": PROFILE_BINDING_PASS_V023,
            "settings_mode": "PROFILE_BOUND",
            "profile_binding_sha256": binding.binding_sha256,
            "profile_sha256": binding.profile_sha256,
            "index_pattern": binding.index_pattern,
            "timestamp_query_field": binding.timestamp_query_field,
            "service_source_field": binding.service_source_field,
            "service_query_field": binding.service_query_field,
            "severity_field": binding.severity_field,
            "message_field": binding.message_field,
            "trace_id_field": binding.trace_id_field,
            "maximum_record_rejection_fraction": (
                binding.maximum_record_rejection_fraction
            ),
            "last_query_status": (
                failure_status.value
                if failure_status is not None
                else None if batch is None else batch.status.value
            ),
            "last_normalization_status": (
                None if batch is None else batch.status.value
            ),
            "last_query_batch_sha256": None if batch is None else batch.batch_sha256,
            "last_safe_error_code": safe_error_code,
            "last_sampled_record_count": (
                0 if batch is None else batch.sampled_hit_count
            ),
            "last_accepted_record_count": (
                0 if batch is None else batch.accepted_record_count
            ),
            "last_rejected_record_count": (
                0 if batch is None else batch.rejected_record_count
            ),
            "last_rejection_fraction": (
                0.0 if batch is None else batch.rejection_fraction
            ),
            "last_rejection_codes_by_count": (
                {} if batch is None else batch.rejection_codes_by_count
            ),
        }
        draft = cls.model_construct(**body, diagnostics_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"diagnostics_sha256"})
        return cls.model_validate(
            {**serialized, "diagnostics_sha256": semantic_sha256_v22(serialized)}
        )


def load_product_v023_profile_binding(
    *,
    active_profile_path: Path,
    handoff_path: Path,
) -> OpenSearchConnectorProfileBindingV023:
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        active_profile_path.read_text(encoding="utf-8")
    )
    handoff = _load_object(handoff_path)
    handoff_body: Mapping[str, object] = {
        key: value for key, value in handoff.items() if key != "handoff_sha256"
    }
    if (
        active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
        or active.profile_sha256 != ACTIVE_PROFILE_SHA256_V023
        or active.selected_candidate_alias != "P01"
        or active.candidate_set_sha256 != CANDIDATE_SET_SHA256_V023
        or active.operator_decision_sha256 != OPERATOR_DECISION_SHA256_V023
        or active.capture_bundle_sha256 != CAPTURE_BUNDLE_SHA256_V023
        or handoff.get("status")
        != "ECOMSRE_PRODUCT_V0222_BASELINE_HANDOFF_READY"
        or handoff.get("active_normalization_profile_sha256")
        != active.profile_sha256
        or handoff.get("candidate_set_sha256") != active.candidate_set_sha256
        or handoff.get("operator_decision_sha256")
        != active.operator_decision_sha256
        or handoff.get("handoff_sha256") != BASELINE_HANDOFF_SHA256_V023
        or semantic_sha256_v22(handoff_body) != BASELINE_HANDOFF_SHA256_V023
    ):
        raise ValueError("Product v0.2.3 frozen OpenSearch handoff differs")
    return OpenSearchConnectorProfileBindingV023.from_active_profile(
        active,
        baseline_handoff_sha256=BASELINE_HANDOFF_SHA256_V023,
    )


def build_profile_bound_opensearch_config_v023(
    *,
    active_profile_path: Path,
    handoff_path: Path,
    endpoint: str,
    name: str = "logs",
    credential_refs: Mapping[str, str] | None = None,
    maximum_result_count: int = 200,
    maximum_response_bytes: int = 10_000_000,
) -> ConnectorConfigV1:
    binding = load_product_v023_profile_binding(
        active_profile_path=active_profile_path,
        handoff_path=handoff_path,
    )
    return ConnectorConfigV1(
        name=name,
        kind=ConnectorKindV1.OPENSEARCH,
        endpoint=endpoint,
        settings={
            "mode": "PROFILE_BOUND",
            "profile_binding": binding.model_dump(mode="json"),
            "maximum_result_count": maximum_result_count,
            "maximum_response_bytes": maximum_response_bytes,
        },
        credential_refs=dict(credential_refs or {}),
    )


def build_product_v023_environment_payload(
    *,
    repository_root: Path,
    runtime_authority_sha256: str,
) -> dict[str, Any]:
    root = Path(repository_root).resolve(strict=True)
    if len(runtime_authority_sha256) != 64 or any(
        character not in "0123456789abcdef"
        for character in runtime_authority_sha256
    ):
        raise ValueError("Product v0.2.3 Runtime authority digest is invalid")
    source = _load_object(root / "examples/product/environment.otel-demo.json")
    connectors = source.get("connector_configs")
    if not isinstance(connectors, list):
        raise ValueError("Product v0.2.3 environment connector list differs")
    normalized: list[dict[str, Any]] = []
    for raw in connectors:
        if not isinstance(raw, dict):
            raise ValueError("Product v0.2.3 environment connector differs")
        item = json.loads(json.dumps(raw))
        endpoint = item.get("endpoint")
        if isinstance(endpoint, str):
            item["endpoint"] = endpoint.replace(
                "host.docker.internal",
                "127.0.0.1",
            )
        if item.get("kind") == "OPENSEARCH":
            item = build_profile_bound_opensearch_config_v023(
                active_profile_path=(
                    root
                    / "config/product-v0222/opensearch/normalization-profile.json"
                ),
                handoff_path=(
                    root / "docs/analysis/product-v0222-baseline-handoff.json"
                ),
                endpoint=str(item["endpoint"]),
                name=str(item["name"]),
            ).model_dump(mode="json")
        elif item.get("kind") == "HTTP_HEALTH":
            settings = item.get("settings")
            services = settings.get("services") if isinstance(settings, dict) else None
            if isinstance(services, list):
                for service in services:
                    health_url = (
                        service.get("health_url")
                        if isinstance(service, dict)
                        else None
                    )
                    if isinstance(health_url, str):
                        service["health_url"] = health_url.replace(
                            "host.docker.internal",
                            "127.0.0.1",
                        )
        normalized.append(item)
    normalized.append(
        {
            "name": "pilot-runtime",
            "kind": "PILOT_RUNTIME",
            "endpoint": None,
            "settings": {
                "snapshot_ref": "pilot/runtime-readiness.json",
                "authority_sha256": runtime_authority_sha256,
                "maximum_age_seconds": 600,
            },
            "credential_refs": {},
        }
    )
    payload = {
        "name": "product-v023-fresh-baseline-nofault",
        "description": (
            "Fresh read-only Product v0.2.3 environment bound to ACTIVE P01."
        ),
        "timezone": "UTC",
        "service_identity_policy": {
            "services": [
                {
                    "logical_service": "checkout",
                    "aliases": {
                        "prometheus": ["checkout", "checkoutservice"],
                        "opensearch": ["checkout", "checkoutservice"],
                        "jaeger": ["checkout"],
                        "http_health": [],
                    },
                    "approved_many_to_one": True,
                }
            ]
        },
        "connector_configs": normalized,
        "explicit_service_catalog": ["checkout"],
    }
    return EnvironmentCreateV1.model_validate(payload).model_dump(mode="json")


__all__ = (
    "ACTIVE_PROFILE_BINDING_SHA256_V023",
    "ACTIVE_PROFILE_SHA256_V023",
    "BASELINE_HANDOFF_SHA256_V023",
    "CANDIDATE_SET_SHA256_V023",
    "CAPTURE_BUNDLE_SHA256_V023",
    "OPERATOR_DECISION_SHA256_V023",
    "PROFILE_BINDING_BLOCKED_V023",
    "PROFILE_BINDING_PASS_V023",
    "OpenSearchConnectorDiagnosticsV023",
    "OpenSearchConnectorProfileBindingV023",
    "build_profile_bound_opensearch_config_v023",
    "build_product_v023_environment_payload",
    "load_product_v023_profile_binding",
)
