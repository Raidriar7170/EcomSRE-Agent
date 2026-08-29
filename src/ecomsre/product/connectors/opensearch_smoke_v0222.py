"""Active-profile connector smoke contracts for Product v0.2.2.2."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22, semantic_sha256_v22
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorHealthResultV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OpenSearchNormalizationProfileV0222,
    OpenSearchProfileStatusV0222,
)
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    ProductModelV1,
)
from ecomsre.product.pilot.baseline_readiness_v021 import HealthyTrafficProfileV021


CONNECTOR_SMOKE_PASS_V0222 = "ECOMSRE_PRODUCT_V0222_CONNECTOR_SMOKE_PASS"
CONNECTOR_SMOKE_BLOCKED_V0222 = "BLOCKED_ECOMSRE_PRODUCT_V0222_CONNECTOR_SMOKE"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class OpenSearchConnectorSmokeProfileV0222(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-connector-smoke-profile.v0222"
    ] = "ecomsre.product.opensearch-connector-smoke-profile.v0222"
    session_id: Literal["product-v0222-connector-smoke-1"]
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    connector_config: ConnectorConfigV1
    healthy_traffic_profile: HealthyTrafficProfileV021
    window_count: Literal[3]
    maximum_records_per_window: Literal[5]
    index_settle_seconds: int = Field(ge=0, le=30)
    private_root: Literal[
        ".local/product-v0222/opensearch-connector-smoke/private"
    ]
    smoke_profile_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_profile(self) -> "OpenSearchConnectorSmokeProfileV0222":
        if (
            self.connector_config.name != "logs"
            or self.connector_config.kind.value != "OPENSEARCH"
            or self.connector_config.endpoint != "http://127.0.0.1:19200"
            or self.connector_config.credential_refs
            or self.healthy_traffic_profile.maximum_request_count != 30
            or self.healthy_traffic_profile.requests_per_second != 1.0
        ):
            raise ValueError("OpenSearch connector smoke profile differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"smoke_profile_sha256"})
        )
        if self.smoke_profile_sha256 != expected:
            raise ValueError("OpenSearch connector smoke profile digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchConnectorSmokeProfileV0222":
        body = {
            "schema_version": (
                "ecomsre.product.opensearch-connector-smoke-profile.v0222"
            ),
            **values,
        }
        draft = cls.model_construct(**body, smoke_profile_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"smoke_profile_sha256"})
        return cls.model_validate(
            {
                **serialized,
                "smoke_profile_sha256": semantic_sha256_v22(serialized),
            }
        )


def build_connector_smoke_profile_v0222(
    *,
    active_profile: OpenSearchNormalizationProfileV0222,
) -> OpenSearchConnectorSmokeProfileV0222:
    if active_profile.profile_status is not OpenSearchProfileStatusV0222.ACTIVE:
        raise ValueError("OpenSearch connector smoke requires an active profile")
    trace_id_field = (
        None
        if active_profile.trace_id_extraction is None
        else active_profile.trace_id_extraction.paths[0]
    )
    return OpenSearchConnectorSmokeProfileV0222.build(
        session_id="product-v0222-connector-smoke-1",
        active_profile_sha256=active_profile.profile_sha256,
        connector_config=ConnectorConfigV1(
            name="logs",
            kind=ConnectorKindV1.OPENSEARCH,
            endpoint="http://127.0.0.1:19200",
            settings={
                "index_pattern": active_profile.index_pattern,
                "timestamp_field": (
                    active_profile.timestamp_extraction.extraction.paths[0]
                ),
                "service_field": active_profile.service_source_field,
                "service_query_field": active_profile.service_query_field,
                "severity_field": (
                    active_profile.severity_extraction.extraction.paths[0]
                ),
                "message_field": (
                    active_profile.message_extraction.extraction.paths[0]
                ),
                "message_projection_policy": (
                    active_profile.message_projection_policy
                ),
                "trace_id_field": trace_id_field,
                "maximum_result_count": 5,
                "maximum_response_bytes": 2_000_000,
            },
            credential_refs={},
        ),
        healthy_traffic_profile=HealthyTrafficProfileV021(
            request_seed=5223,
            maximum_request_count=30,
            requests_per_second=1.0,
            error_budget=5,
        ),
        window_count=3,
        maximum_records_per_window=5,
        index_settle_seconds=10,
        private_root=(
            ".local/product-v0222/opensearch-connector-smoke/private"
        ),
    )


class OpenSearchWindowStatusV0222(str, Enum):
    SUCCESS_NONEMPTY = "SUCCESS_NONEMPTY"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    FAILURE = "FAILURE"


class OpenSearchConnectorQueryDiagnosticV0222(ProductModelV1):
    window_ordinal: int = Field(ge=1, le=3)
    window: ConnectorWindowV1
    query_completed: bool
    status: OpenSearchWindowStatusV0222
    query_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    safe_error_code: str | None
    returned_record_count: int = Field(ge=0, le=5)
    accepted_checkout_record_count: int = Field(ge=0, le=5)
    rejected_record_count: int = Field(ge=0, le=5)
    rejection_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    outer_schema_failure_count: int = Field(ge=0, le=1)
    all_records_rejected_failure_count: int = Field(ge=0, le=1)
    service_alias_unmapped_count: int = Field(ge=0, le=1)
    timestamp_parse_failure_count: int = Field(ge=0, le=1)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_consistent_diagnostic(
        self,
    ) -> "OpenSearchConnectorQueryDiagnosticV0222":
        success = self.status in {
            OpenSearchWindowStatusV0222.SUCCESS_EMPTY,
            OpenSearchWindowStatusV0222.SUCCESS_NONEMPTY,
        }
        if self.query_completed != success:
            raise ValueError("OpenSearch connector query completion differs")
        if success and self.safe_error_code is not None:
            raise ValueError("OpenSearch connector successful query has an error")
        if not success and self.safe_error_code is None:
            raise ValueError("OpenSearch connector failed query lacks an error")
        return self


class OpenSearchConnectorSmokeReportV0222(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-connector-smoke.v0222"
    ] = "ecomsre.product.opensearch-connector-smoke.v0222"
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0222_CONNECTOR_SMOKE_PASS",
        "BLOCKED_ECOMSRE_PRODUCT_V0222_CONNECTOR_SMOKE",
    ]
    session_id: Literal["product-v0222-connector-smoke-1"]
    smoke_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_file_sha256_before: str = Field(pattern=_SHA256_PATTERN)
    active_profile_file_sha256_after: str = Field(pattern=_SHA256_PATTERN)
    active_profile_survived_restart: bool
    connector_verify_status: str
    discovered_services: tuple[str, ...]
    service_identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    opensearch_capability_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_count: int = Field(ge=0, le=3)
    nonempty_window_count: int = Field(ge=0, le=3)
    accepted_checkout_record_count: int = Field(ge=0, le=15)
    query_diagnostics: tuple[OpenSearchConnectorQueryDiagnosticV0222, ...]
    outer_schema_failure_count: int = Field(ge=0, le=3)
    all_records_rejected_failure_count: int = Field(ge=0, le=3)
    service_alias_unmapped_count: int = Field(ge=0, le=3)
    timestamp_parse_failure_count: int = Field(ge=0, le=3)
    healthy_traffic_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    healthy_traffic_attempted: int = Field(ge=0, le=30)
    healthy_traffic_succeeded: int = Field(ge=0, le=30)
    queue_flag_value: int
    baseline_unchanged: bool
    cleanup: Literal["CLEAN", "BLOCKED"]
    fault_attempt_count: Literal[0]
    baseline_readiness_attempt_count: Literal[0]
    product_diagnosis_attempt_count: Literal[0]
    knowledge_loop_campaign_count: Literal[0]
    action_authority: Literal["NONE"]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    smoke_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_smoke(self) -> "OpenSearchConnectorSmokeReportV0222":
        if len(self.query_diagnostics) != self.query_count or tuple(
            item.window_ordinal for item in self.query_diagnostics
        ) != tuple(range(1, self.query_count + 1)):
            raise ValueError("OpenSearch connector query diagnostics differ")
        for previous, current in zip(
            self.query_diagnostics,
            self.query_diagnostics[1:],
            strict=False,
        ):
            if previous.window.ended_at >= current.window.started_at:
                raise ValueError("OpenSearch connector windows overlap")
        passing = (
            self.connector_verify_status == ConnectorAvailabilityV1.AVAILABLE.value
            and self.query_count == 3
            and self.nonempty_window_count >= 1
            and self.accepted_checkout_record_count >= 1
            and all(item.query_completed for item in self.query_diagnostics)
            and all(
                item.rejection_fraction <= 0.2
                for item in self.query_diagnostics
                if item.returned_record_count or item.rejected_record_count
            )
            and self.outer_schema_failure_count == 0
            and self.all_records_rejected_failure_count == 0
            and self.service_alias_unmapped_count == 0
            and self.timestamp_parse_failure_count == 0
            and self.healthy_traffic_attempted == 30
            and self.healthy_traffic_succeeded == 30
            and self.queue_flag_value == 0
            and self.active_profile_survived_restart
            and self.baseline_unchanged
            and self.cleanup == "CLEAN"
        )
        if (self.terminal == CONNECTOR_SMOKE_PASS_V0222) != passing:
            raise ValueError("OpenSearch connector smoke terminal differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"smoke_sha256"})
        )
        if self.smoke_sha256 != expected:
            raise ValueError("OpenSearch connector smoke digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchConnectorSmokeReportV0222":
        body = {
            "schema_version": "ecomsre.product.opensearch-connector-smoke.v0222",
            **values,
        }
        draft = cls.model_construct(**body, smoke_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"smoke_sha256"})
        return cls.model_validate(
            {**serialized, "smoke_sha256": semantic_sha256_v22(serialized)}
        )


def _diagnostic_v0222(
    ordinal: int,
    result: ConnectorQueryResultV1,
) -> OpenSearchConnectorQueryDiagnosticV0222:
    if result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY:
        status = OpenSearchWindowStatusV0222.SUCCESS_NONEMPTY
    elif result.status is ReadSourceStatusV22.SUCCESS_EMPTY:
        status = OpenSearchWindowStatusV0222.SUCCESS_EMPTY
    else:
        status = OpenSearchWindowStatusV0222.FAILURE
    success = status is not OpenSearchWindowStatusV0222.FAILURE
    returned = len(result.records)
    accepted_checkout = sum(record.service == "checkout" for record in result.records)
    rejected = 0 if success else 1
    return OpenSearchConnectorQueryDiagnosticV0222(
        window_ordinal=ordinal,
        window=result.window,
        query_completed=success,
        status=status,
        query_result_sha256=result.result_sha256,
        safe_error_code=result.safe_error_code,
        returned_record_count=returned,
        accepted_checkout_record_count=accepted_checkout,
        rejected_record_count=rejected,
        rejection_fraction=0.0 if success else 1.0,
        outer_schema_failure_count=0 if success else 1,
        all_records_rejected_failure_count=0 if success else 1,
        service_alias_unmapped_count=0 if success else 1,
        timestamp_parse_failure_count=0 if success else 1,
        latency_ms=result.latency_ms,
    )


def evaluate_connector_smoke_v0222(
    *,
    smoke_profile: OpenSearchConnectorSmokeProfileV0222,
    active_profile: OpenSearchNormalizationProfileV0222,
    connector_health: ConnectorHealthResultV1,
    query_results: tuple[ConnectorQueryResultV1, ...],
    active_profile_file_sha256_before: str,
    active_profile_file_sha256_after: str,
    healthy_traffic_result_sha256: str,
    healthy_traffic_attempted: int,
    healthy_traffic_succeeded: int,
    queue_flag_value: int,
    baseline_unchanged: bool,
    cleanup: str,
) -> OpenSearchConnectorSmokeReportV0222:
    if (
        active_profile.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
        or active_profile.profile_sha256 != smoke_profile.active_profile_sha256
    ):
        raise ValueError("OpenSearch connector smoke active profile differs")
    diagnostics = tuple(
        _diagnostic_v0222(ordinal, result)
        for ordinal, result in enumerate(query_results, start=1)
    )
    outer = sum(item.outer_schema_failure_count for item in diagnostics)
    all_rejected = sum(
        item.all_records_rejected_failure_count for item in diagnostics
    )
    alias_unmapped = sum(item.service_alias_unmapped_count for item in diagnostics)
    timestamp_failures = sum(
        item.timestamp_parse_failure_count for item in diagnostics
    )
    profile_survived = (
        active_profile_file_sha256_before == active_profile_file_sha256_after
    )
    identity_body = {
        "schema_version": "ecomsre.product.service-identity-binding.v0222",
        "logical_service": "checkout",
        "opensearch_aliases": tuple(
            sorted(
                service
                for service in connector_health.discovered_services
                if "checkout" in service.lower().replace("-", "").replace("_", "")
            )
        ),
        "service_source_field": active_profile.service_source_field,
        "service_query_field": active_profile.service_query_field,
    }
    capability_body = {
        "schema_version": "ecomsre.product.opensearch-capability-binding.v0222",
        "connector_name": connector_health.connector_name,
        "status": connector_health.status.value,
        "capabilities": tuple(
            item.model_dump(mode="json") for item in connector_health.capabilities
        ),
        "discovered_services": connector_health.discovered_services,
    }
    passing = (
        connector_health.status is ConnectorAvailabilityV1.AVAILABLE
        and len(diagnostics) == 3
        and any(
            item.status is OpenSearchWindowStatusV0222.SUCCESS_NONEMPTY
            for item in diagnostics
        )
        and sum(item.accepted_checkout_record_count for item in diagnostics) > 0
        and all(item.query_completed for item in diagnostics)
        and outer == 0
        and all_rejected == 0
        and alias_unmapped == 0
        and timestamp_failures == 0
        and healthy_traffic_attempted == healthy_traffic_succeeded == 30
        and queue_flag_value == 0
        and profile_survived
        and baseline_unchanged
        and cleanup == "CLEAN"
    )
    return OpenSearchConnectorSmokeReportV0222.build(
        terminal=(
            CONNECTOR_SMOKE_PASS_V0222
            if passing
            else CONNECTOR_SMOKE_BLOCKED_V0222
        ),
        session_id=smoke_profile.session_id,
        smoke_profile_sha256=smoke_profile.smoke_profile_sha256,
        active_profile_sha256=active_profile.profile_sha256,
        active_profile_file_sha256_before=active_profile_file_sha256_before,
        active_profile_file_sha256_after=active_profile_file_sha256_after,
        active_profile_survived_restart=profile_survived,
        connector_verify_status=connector_health.status.value,
        discovered_services=connector_health.discovered_services,
        service_identity_sha256=semantic_sha256_v22(identity_body),
        opensearch_capability_sha256=semantic_sha256_v22(capability_body),
        query_count=len(diagnostics),
        nonempty_window_count=sum(
            item.status is OpenSearchWindowStatusV0222.SUCCESS_NONEMPTY
            for item in diagnostics
        ),
        accepted_checkout_record_count=sum(
            item.accepted_checkout_record_count for item in diagnostics
        ),
        query_diagnostics=diagnostics,
        outer_schema_failure_count=outer,
        all_records_rejected_failure_count=all_rejected,
        service_alias_unmapped_count=alias_unmapped,
        timestamp_parse_failure_count=timestamp_failures,
        healthy_traffic_result_sha256=healthy_traffic_result_sha256,
        healthy_traffic_attempted=healthy_traffic_attempted,
        healthy_traffic_succeeded=healthy_traffic_succeeded,
        queue_flag_value=queue_flag_value,
        baseline_unchanged=baseline_unchanged,
        cleanup=cleanup,
        fault_attempt_count=0,
        baseline_readiness_attempt_count=0,
        product_diagnosis_attempt_count=0,
        knowledge_loop_campaign_count=0,
        action_authority="NONE",
        agent_writes=0,
        runbook_executions=0,
    )


__all__ = (
    "CONNECTOR_SMOKE_BLOCKED_V0222",
    "CONNECTOR_SMOKE_PASS_V0222",
    "OpenSearchConnectorQueryDiagnosticV0222",
    "OpenSearchConnectorSmokeProfileV0222",
    "OpenSearchConnectorSmokeReportV0222",
    "OpenSearchWindowStatusV0222",
    "build_connector_smoke_profile_v0222",
    "evaluate_connector_smoke_v0222",
)
