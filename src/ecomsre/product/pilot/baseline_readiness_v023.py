"""Product v0.2.3 source-aware fresh-baseline readiness contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
)
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.errors import ProductError, not_found
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineConnectorBindingV021,
    BaselineConnectorExpectationV021,
    evaluate_baseline_windows_v021,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


BASELINE_PREFLIGHT_PASS_V023 = "ECOMSRE_PRODUCT_V023_BASELINE_PREFLIGHT_PASS"
BASELINE_PREFLIGHT_BLOCKED_V023 = (
    "BLOCKED_ECOMSRE_PRODUCT_V023_BASELINE_PREFLIGHT"
)
_SUCCESS_STATUSES_V023 = frozenset(
    {ReadSourceStatusV22.SUCCESS_EMPTY, ReadSourceStatusV22.SUCCESS_NONEMPTY}
)
_PROMETHEUS_TEMPLATE_NAMES_V023 = frozenset(
    {"request_support", "error_rate", "latency", "cpu", "memory", "queue_lag"}
)
_FORBIDDEN_LOG_REJECTION_FRAGMENTS_V023 = (
    "TIMESTAMP",
    "SERVICE",
    "MESSAGE",
    "OBSERVER_PROJECTION",
)


def _require_utc(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")


def _sealed_model(model_type: type[ProductModelV1], body: Mapping[str, object]):
    normalized = dict(body)
    digest_field = next(
        name for name in ("diagnostics_sha256", "window_sha256", "parity_sha256")
        if name in model_type.model_fields
    )
    return model_type.model_validate(
        {**normalized, digest_field: semantic_sha256_v22(normalized)}
    )


class ProductBaselineReadinessProfileV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-readiness-profile.v023"
    ] = "ecomsre.product.baseline-readiness-profile.v023"
    mode: Literal["DEMO_ONLY"]
    candidate_services: tuple[Literal["checkout"], ...]
    warmup_seconds: Literal[180]
    baseline_accumulation_seconds: Literal[360]
    lookback_seconds: Literal[180]
    window_count: Literal[5]
    minimum_accepted_windows: Literal[4]
    healthy_traffic_request_count: Literal[180]
    healthy_traffic_requests_per_second: float = Field(
        gt=0,
        le=10,
        allow_inf_nan=False,
    )
    maximum_error_fraction: float = Field(ge=0, le=0.05, allow_inf_nan=False)
    queue_fault_flag: Literal[0]
    active_opensearch_profile_sha256: Literal[
        "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
    ]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_frozen_profile(self) -> "ProductBaselineReadinessProfileV023":
        if (
            self.candidate_services != ("checkout",)
            or self.healthy_traffic_requests_per_second != 0.5
        ):
            raise ValueError("Product v0.2.3 baseline candidates differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("Product v0.2.3 baseline profile digest differs")
        return self

    @classmethod
    def load(cls, path: Path) -> "ProductBaselineReadinessProfileV023":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Product v0.2.3 baseline profile must be an object")
        return cls.model_validate(payload)

    @classmethod
    def default(cls) -> "ProductBaselineReadinessProfileV023":
        body = {
            "schema_version": "ecomsre.product.baseline-readiness-profile.v023",
            "mode": "DEMO_ONLY",
            "candidate_services": ("checkout",),
            "warmup_seconds": 180,
            "baseline_accumulation_seconds": 360,
            "lookback_seconds": 180,
            "window_count": 5,
            "minimum_accepted_windows": 4,
            "healthy_traffic_request_count": 180,
            "healthy_traffic_requests_per_second": 0.5,
            "maximum_error_fraction": 0.01,
            "queue_fault_flag": 0,
            "active_opensearch_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        }
        return cls.model_validate(
            {**body, "profile_sha256": semantic_sha256_v22(body)}
        )


class PrometheusTemplateDiagnosticV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.prometheus-template-diagnostic.v023"
    ] = "ecomsre.product.prometheus-template-diagnostic.v023"
    template_name: Literal[
        "request_support", "error_rate", "latency", "cpu", "memory", "queue_lag"
    ]
    logical_service: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    status: ReadSourceStatusV22
    sample_count: int = Field(ge=0)
    safe_error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,95}$",
    )
    diagnostics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_diagnostic(self) -> "PrometheusTemplateDiagnosticV023":
        if self.template_name not in _PROMETHEUS_TEMPLATE_NAMES_V023:
            raise ValueError("Prometheus template diagnostic name differs")
        if self.status is ReadSourceStatusV22.SUCCESS_NONEMPTY:
            if self.sample_count < 1 or self.safe_error_code is not None:
                raise ValueError("Prometheus nonempty diagnostic semantics differ")
        elif self.status is ReadSourceStatusV22.SUCCESS_EMPTY:
            if self.sample_count != 0 or self.safe_error_code is not None:
                raise ValueError("Prometheus empty diagnostic semantics differ")
        elif self.sample_count != 0 or self.safe_error_code is None:
            raise ValueError("Prometheus failure diagnostic semantics differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"diagnostics_sha256"})
        )
        if self.diagnostics_sha256 != expected:
            raise ValueError("Prometheus template diagnostic digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        template_name: str,
        logical_service: str,
        status: ReadSourceStatusV22,
        sample_count: int,
        safe_error_code: str | None = None,
    ) -> "PrometheusTemplateDiagnosticV023":
        body = {
            "schema_version": "ecomsre.product.prometheus-template-diagnostic.v023",
            "template_name": template_name,
            "logical_service": logical_service,
            "status": status.value,
            "sample_count": sample_count,
            "safe_error_code": safe_error_code,
        }
        return PrometheusTemplateDiagnosticV023.model_validate(
            _sealed_model(cls, body)
        )


class PrometheusWindowDiagnosticsV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.prometheus-window-diagnostics.v023"
    ] = "ecomsre.product.prometheus-window-diagnostics.v023"
    window: ConnectorWindowV1
    metric_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    templates: tuple[PrometheusTemplateDiagnosticV023, ...]
    diagnostics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_window(self) -> "PrometheusWindowDiagnosticsV023":
        keys = tuple((item.logical_service, item.template_name) for item in self.templates)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Prometheus window diagnostics are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"diagnostics_sha256"})
        )
        if self.diagnostics_sha256 != expected:
            raise ValueError("Prometheus window diagnostic digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        window: ConnectorWindowV1,
        metric_result_sha256: str,
        resource_result_sha256: str,
        templates: tuple[PrometheusTemplateDiagnosticV023, ...],
    ) -> "PrometheusWindowDiagnosticsV023":
        ordered = tuple(
            sorted(templates, key=lambda item: (item.logical_service, item.template_name))
        )
        body = {
            "schema_version": "ecomsre.product.prometheus-window-diagnostics.v023",
            "window": window.model_dump(mode="json"),
            "metric_result_sha256": metric_result_sha256,
            "resource_result_sha256": resource_result_sha256,
            "templates": tuple(item.model_dump(mode="json") for item in ordered),
        }
        return PrometheusWindowDiagnosticsV023.model_validate(
            _sealed_model(cls, body)
        )


class OpenSearchWindowDiagnosticsV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-window-diagnostics.v023"
    ] = "ecomsre.product.opensearch-window-diagnostics.v023"
    window: ConnectorWindowV1
    log_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_status: ReadSourceStatusV22
    safe_error_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,119}$",
    )
    sampled_record_count: int = Field(ge=0, le=200)
    accepted_record_count: int = Field(ge=0, le=200)
    rejected_record_count: int = Field(ge=0, le=200)
    rejection_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    rejection_codes_by_count: dict[str, int]
    diagnostics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_window(self) -> "OpenSearchWindowDiagnosticsV023":
        if self.sampled_record_count != (
            self.accepted_record_count + self.rejected_record_count
        ):
            raise ValueError("OpenSearch window diagnostic counts differ")
        expected_fraction = (
            0.0
            if self.sampled_record_count == 0
            else self.rejected_record_count / self.sampled_record_count
        )
        if abs(self.rejection_fraction - expected_fraction) > 1e-12:
            raise ValueError("OpenSearch window rejection fraction differs")
        if self.query_status in _SUCCESS_STATUSES_V023:
            if self.safe_error_code is not None:
                raise ValueError("OpenSearch success diagnostic has a safe error")
        elif self.safe_error_code is None:
            raise ValueError("OpenSearch failure diagnostic lacks a safe error")
        if self.rejection_codes_by_count != dict(
            sorted(self.rejection_codes_by_count.items())
        ) or sum(self.rejection_codes_by_count.values()) != self.rejected_record_count:
            raise ValueError("OpenSearch rejection-code counts differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"diagnostics_sha256"})
        )
        if self.diagnostics_sha256 != expected:
            raise ValueError("OpenSearch window diagnostic digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        window: ConnectorWindowV1,
        log_result_sha256: str,
        profile_sha256: str,
        query_status: ReadSourceStatusV22,
        sampled_record_count: int,
        accepted_record_count: int,
        rejected_record_count: int,
        rejection_fraction: float,
        rejection_codes_by_count: Mapping[str, int],
        safe_error_code: str | None = None,
    ) -> "OpenSearchWindowDiagnosticsV023":
        body = {
            "schema_version": "ecomsre.product.opensearch-window-diagnostics.v023",
            "window": window.model_dump(mode="json"),
            "log_result_sha256": log_result_sha256,
            "profile_sha256": profile_sha256,
            "query_status": query_status.value,
            "safe_error_code": safe_error_code,
            "sampled_record_count": sampled_record_count,
            "accepted_record_count": accepted_record_count,
            "rejected_record_count": rejected_record_count,
            "rejection_fraction": rejection_fraction,
            "rejection_codes_by_count": dict(sorted(rejection_codes_by_count.items())),
        }
        return OpenSearchWindowDiagnosticsV023.model_validate(
            _sealed_model(cls, body)
        )


class BaselineRejectionReasonCodeV023(str, Enum):
    STRUCTURAL_WINDOW_REJECTED = "STRUCTURAL_WINDOW_REJECTED"
    PROMETHEUS_DIAGNOSTICS_MISMATCH = "PROMETHEUS_DIAGNOSTICS_MISMATCH"
    METRICS_REQUEST_SUPPORT_EMPTY = "METRICS_REQUEST_SUPPORT_EMPTY"
    METRICS_ERROR_RATE_INVALID = "METRICS_ERROR_RATE_INVALID"
    METRICS_LATENCY_INVALID = "METRICS_LATENCY_INVALID"
    OPENSEARCH_DIAGNOSTICS_MISMATCH = "OPENSEARCH_DIAGNOSTICS_MISMATCH"
    OPENSEARCH_PROFILE_SHA_INVALID = "OPENSEARCH_PROFILE_SHA_INVALID"
    OPENSEARCH_QUERY_FAILED = "OPENSEARCH_QUERY_FAILED"
    OPENSEARCH_REJECTION_FRACTION_EXCEEDED = (
        "OPENSEARCH_REJECTION_FRACTION_EXCEEDED"
    )
    OPENSEARCH_REQUIRED_EXTRACTION_FAILED = (
        "OPENSEARCH_REQUIRED_EXTRACTION_FAILED"
    )


_REASON_ORDER_V023 = {
    reason: index for index, reason in enumerate(BaselineRejectionReasonCodeV023)
}


class BaselineWindowAuditV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-window-audit.v023"
    ] = "ecomsre.product.baseline-window-audit.v023"
    window_ordinal: int = Field(ge=1, le=5)
    window: ConnectorWindowV1
    result_sha256s: tuple[str, ...]
    prometheus_diagnostics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    opensearch_diagnostics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    opensearch_rejection_codes: tuple[str, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    accepted: bool
    rejection_reason_codes: tuple[BaselineRejectionReasonCodeV023, ...]
    window_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_window(self) -> "BaselineWindowAuditV023":
        if self.accepted != (not self.rejection_reason_codes):
            raise ValueError("Product v0.2.3 window disposition differs")
        if self.opensearch_rejection_codes != tuple(
            sorted(set(self.opensearch_rejection_codes))
        ):
            raise ValueError("Product v0.2.3 OpenSearch rejection codes are not canonical")
        if self.result_sha256s != tuple(sorted(set(self.result_sha256s))):
            raise ValueError("Product v0.2.3 result digests are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"window_sha256"})
        )
        if self.window_sha256 != expected:
            raise ValueError("Product v0.2.3 window audit digest differs")
        return self


class BaselineWindowEvaluationV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-window-evaluation.v023"
    ] = "ecomsre.product.baseline-window-evaluation.v023"
    terminal: Literal[
        "ECOMSRE_PRODUCT_V023_BASELINE_PREFLIGHT_PASS",
        "BLOCKED_ECOMSRE_PRODUCT_V023_BASELINE_PREFLIGHT",
    ]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_opensearch_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    windows: tuple[BaselineWindowAuditV023, ...]
    accepted_ordinals: tuple[int, ...]
    logs_nonempty_window_count: int = Field(ge=0, le=5)
    accepted_checkout_log_record_count: int = Field(ge=0)
    has_normal_checkout_log_template: bool
    aggregate_rejection_reason_codes: tuple[str, ...]
    final_builder_would_pass: bool
    parity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_evaluation(self) -> "BaselineWindowEvaluationV023":
        ordinals = tuple(item.window_ordinal for item in self.windows if item.accepted)
        if self.accepted_ordinals != ordinals:
            raise ValueError("Product v0.2.3 accepted-window ordinals differ")
        if self.aggregate_rejection_reason_codes != tuple(
            sorted(set(self.aggregate_rejection_reason_codes))
        ):
            raise ValueError("Product v0.2.3 aggregate reasons are not canonical")
        if self.final_builder_would_pass != (
            self.terminal == BASELINE_PREFLIGHT_PASS_V023
            and not self.aggregate_rejection_reason_codes
        ):
            raise ValueError("Product v0.2.3 preflight terminal differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"parity_sha256"})
        )
        if self.parity_sha256 != expected:
            raise ValueError("Product v0.2.3 audit-builder parity digest differs")
        return self


class ProductBaselineReadinessAuditV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-readiness-audit.v023"
    ] = "ecomsre.product.baseline-readiness-audit.v023"
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    service_ids: tuple[str, ...]
    baseline_entity_service_ids: tuple[str, ...]
    build_policy: dict[str, Any]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_opensearch_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    service_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation: BaselineWindowEvaluationV023
    final_builder_would_pass: bool
    parity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_audit(self) -> "ProductBaselineReadinessAuditV023":
        if self.service_ids != tuple(sorted(set(self.service_ids))):
            raise ValueError("Product v0.2.3 audit service IDs are not canonical")
        if self.baseline_entity_service_ids != tuple(
            sorted(set(self.baseline_entity_service_ids))
        ):
            raise ValueError("Product v0.2.3 audit entity IDs are not canonical")
        if (
            self.profile_sha256 != self.evaluation.profile_sha256
            or self.active_opensearch_profile_sha256
            != self.evaluation.active_opensearch_profile_sha256
            or self.parity_sha256 != self.evaluation.parity_sha256
            or self.final_builder_would_pass
            != self.evaluation.final_builder_would_pass
            or (self.baseline_sha256 is not None) != self.final_builder_would_pass
        ):
            raise ValueError("Product v0.2.3 audit bindings differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"audit_sha256"})
        )
        if self.audit_sha256 != expected:
            raise ValueError("Product v0.2.3 readiness audit digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        environment_id: str,
        baseline_id: str,
        baseline_sha256: str | None,
        service_ids: tuple[str, ...],
        baseline_entity_service_ids: tuple[str, ...],
        build_policy: Mapping[str, Any],
        service_identity_sha256: str,
        capability_sha256: str,
        evaluation: BaselineWindowEvaluationV023,
    ) -> "ProductBaselineReadinessAuditV023":
        body = {
            "schema_version": "ecomsre.product.baseline-readiness-audit.v023",
            "environment_id": environment_id,
            "baseline_id": baseline_id,
            "baseline_sha256": baseline_sha256,
            "service_ids": tuple(sorted(set(service_ids))),
            "baseline_entity_service_ids": tuple(
                sorted(set(baseline_entity_service_ids))
            ),
            "build_policy": dict(build_policy),
            "profile_sha256": evaluation.profile_sha256,
            "active_opensearch_profile_sha256": (
                evaluation.active_opensearch_profile_sha256
            ),
            "service_identity_sha256": service_identity_sha256,
            "capability_sha256": capability_sha256,
            "evaluation": evaluation.model_dump(mode="json"),
            "final_builder_would_pass": evaluation.final_builder_would_pass,
            "parity_sha256": evaluation.parity_sha256,
        }
        return cls.model_validate(
            {**body, "audit_sha256": semantic_sha256_v22(body)}
        )


def put_readiness_audit_in_transaction_v023(
    connection: sqlite3.Connection,
    audit: ProductBaselineReadinessAuditV023,
    *,
    created_at: datetime,
) -> None:
    serialized = json.dumps(
        audit.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    existing = connection.execute(
        "SELECT payload_json FROM baseline_readiness_audits_v023 "
        "WHERE baseline_id = ? OR audit_sha256 = ?",
        (audit.baseline_id, audit.audit_sha256),
    ).fetchone()
    if existing is not None:
        if existing["payload_json"] != serialized:
            raise ProductError(
                "BASELINE_V023_AUDIT_IMMUTABLE_CONFLICT",
                "The Product v0.2.3 readiness audit already differs.",
                status_code=409,
            )
        return
    connection.execute(
        """INSERT INTO baseline_readiness_audits_v023(
               audit_sha256, environment_id, baseline_id, payload_json, created_at
           ) VALUES (?, ?, ?, ?, ?)""",
        (
            audit.audit_sha256,
            audit.environment_id,
            audit.baseline_id,
            serialized,
            created_at.isoformat(),
        ),
    )


class ProductBaselineReadinessAuditRepositoryV023:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def put(
        self,
        audit: ProductBaselineReadinessAuditV023,
        *,
        created_at: datetime,
        fence: JobLeaseFenceV1 | None = None,
    ) -> None:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                put_readiness_audit_in_transaction_v023(
                    connection,
                    audit,
                    created_at=created_at,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_latest(self, environment_id: str) -> ProductBaselineReadinessAuditV023:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM baseline_readiness_audits_v023 "
                "WHERE environment_id = ? ORDER BY created_at DESC LIMIT 1",
                (environment_id,),
            ).fetchone()
        if row is None:
            raise not_found(
                "BASELINE_V023_AUDIT_NOT_FOUND",
                "No Product v0.2.3 readiness audit exists for the environment.",
            )
        return ProductBaselineReadinessAuditV023.model_validate_json(
            row["payload_json"]
        )

    def get_by_baseline(self, baseline_id: str) -> ProductBaselineReadinessAuditV023:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM baseline_readiness_audits_v023 "
                "WHERE baseline_id = ?",
                (baseline_id,),
            ).fetchone()
        if row is None:
            raise not_found(
                "BASELINE_V023_AUDIT_NOT_FOUND",
                "No Product v0.2.3 readiness audit exists for the baseline.",
            )
        return ProductBaselineReadinessAuditV023.model_validate_json(
            row["payload_json"]
        )


def _one_result(
    results: tuple[ConnectorQueryResultV1, ...],
    source: EvidenceSourceV22,
) -> ConnectorQueryResultV1 | None:
    matches = tuple(item for item in results if item.source is source)
    return matches[0] if len(matches) == 1 else None


def _prometheus_reasons_v023(
    results: tuple[ConnectorQueryResultV1, ...],
    diagnostic: PrometheusWindowDiagnosticsV023,
    expected_window: ConnectorWindowV1,
) -> set[BaselineRejectionReasonCodeV023]:
    reasons: set[BaselineRejectionReasonCodeV023] = set()
    metrics = _one_result(results, EvidenceSourceV22.METRICS)
    resources = _one_result(results, EvidenceSourceV22.RESOURCES)
    if (
        metrics is None
        or resources is None
        or diagnostic.window != expected_window
        or diagnostic.metric_result_sha256
        != (None if metrics is None else metrics.result_sha256)
        or diagnostic.resource_result_sha256
        != (None if resources is None else resources.result_sha256)
    ):
        reasons.add(BaselineRejectionReasonCodeV023.PROMETHEUS_DIAGNOSTICS_MISMATCH)
        return reasons
    by_name: dict[str, PrometheusTemplateDiagnosticV023] = {
        item.template_name: item
        for item in diagnostic.templates
        if item.logical_service == "checkout"
    }
    request_support = by_name.get("request_support")
    if (
        request_support is None
        or request_support.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
        or not any(
            isinstance(record, MetricFactV22)
            and record.service == "checkout"
            and record.metric_kind is MetricKindV22.REQUEST_SUPPORT
            for record in metrics.records
        )
    ):
        reasons.add(BaselineRejectionReasonCodeV023.METRICS_REQUEST_SUPPORT_EMPTY)
    for template, metric_kind, reason in (
        (
            "error_rate",
            MetricKindV22.ERROR_RATE,
            BaselineRejectionReasonCodeV023.METRICS_ERROR_RATE_INVALID,
        ),
        (
            "latency",
            MetricKindV22.LATENCY_P95_MS,
            BaselineRejectionReasonCodeV023.METRICS_LATENCY_INVALID,
        ),
    ):
        item = by_name.get(template)
        if item is None or item.status not in _SUCCESS_STATUSES_V023:
            reasons.add(reason)
        elif item.status is ReadSourceStatusV22.SUCCESS_NONEMPTY and not any(
            isinstance(record, MetricFactV22)
            and record.service == "checkout"
            and record.metric_kind is metric_kind
            for record in metrics.records
        ):
            reasons.add(reason)
    return reasons


def _opensearch_reasons_v023(
    results: tuple[ConnectorQueryResultV1, ...],
    diagnostic: OpenSearchWindowDiagnosticsV023,
    expected_window: ConnectorWindowV1,
) -> set[BaselineRejectionReasonCodeV023]:
    reasons: set[BaselineRejectionReasonCodeV023] = set()
    logs = _one_result(results, EvidenceSourceV22.LOGS)
    if (
        logs is None
        or diagnostic.window != expected_window
        or diagnostic.log_result_sha256
        != (None if logs is None else logs.result_sha256)
        or diagnostic.query_status != (None if logs is None else logs.status)
        or diagnostic.accepted_record_count
        != (0 if logs is None else len(logs.records))
    ):
        reasons.add(BaselineRejectionReasonCodeV023.OPENSEARCH_DIAGNOSTICS_MISMATCH)
        return reasons
    if diagnostic.profile_sha256 != ACTIVE_PROFILE_SHA256_V023:
        reasons.add(BaselineRejectionReasonCodeV023.OPENSEARCH_PROFILE_SHA_INVALID)
    if diagnostic.query_status not in _SUCCESS_STATUSES_V023:
        reasons.add(BaselineRejectionReasonCodeV023.OPENSEARCH_QUERY_FAILED)
    if diagnostic.sampled_record_count and diagnostic.rejection_fraction > 0.20:
        reasons.add(
            BaselineRejectionReasonCodeV023.OPENSEARCH_REJECTION_FRACTION_EXCEEDED
        )
    if any(
        count > 0 and any(fragment in code for fragment in _FORBIDDEN_LOG_REJECTION_FRAGMENTS_V023)
        for code, count in diagnostic.rejection_codes_by_count.items()
    ):
        reasons.add(
            BaselineRejectionReasonCodeV023.OPENSEARCH_REQUIRED_EXTRACTION_FAILED
        )
    return reasons


def evaluate_baseline_windows_v023(
    *,
    profile: ProductBaselineReadinessProfileV023,
    window_results: tuple[tuple[ConnectorQueryResultV1, ...], ...],
    expected_windows: tuple[ConnectorWindowV1, ...],
    connector_bindings: tuple[tuple[BaselineConnectorBindingV021, ...], ...],
    connector_expectations: tuple[
        tuple[BaselineConnectorExpectationV021, ...], ...
    ],
    prometheus_diagnostics: tuple[PrometheusWindowDiagnosticsV023, ...],
    opensearch_diagnostics: tuple[OpenSearchWindowDiagnosticsV023, ...],
) -> BaselineWindowEvaluationV023:
    if not all(
        len(items) == profile.window_count
        for items in (
            window_results,
            expected_windows,
            connector_bindings,
            connector_expectations,
            prometheus_diagnostics,
            opensearch_diagnostics,
        )
    ):
        raise ValueError("Product v0.2.3 requires all five scheduled windows")
    structural = evaluate_baseline_windows_v021(
        window_results=window_results,
        required_complete_sources=(),
        expected_windows=expected_windows,
        connector_bindings=connector_bindings,
        connector_expectations=connector_expectations,
    )
    audits: list[BaselineWindowAuditV023] = []
    for index, results in enumerate(window_results):
        reasons: set[BaselineRejectionReasonCodeV023] = set()
        if not structural.windows[index].accepted:
            reasons.add(BaselineRejectionReasonCodeV023.STRUCTURAL_WINDOW_REJECTED)
        reasons.update(
            _prometheus_reasons_v023(
                results,
                prometheus_diagnostics[index],
                expected_windows[index],
            )
        )
        reasons.update(
            _opensearch_reasons_v023(
                results,
                opensearch_diagnostics[index],
                expected_windows[index],
            )
        )
        ordered_reasons = tuple(sorted(reasons, key=_REASON_ORDER_V023.__getitem__))
        exact_opensearch_rejections = tuple(
            sorted(
                code
                for code, count in opensearch_diagnostics[
                    index
                ].rejection_codes_by_count.items()
                if count > 0
            )
        )
        body: dict[str, Any] = {
            "schema_version": "ecomsre.product.baseline-window-audit.v023",
            "window_ordinal": index + 1,
            "window": expected_windows[index].model_dump(mode="json"),
            "result_sha256s": tuple(sorted(item.result_sha256 for item in results)),
            "prometheus_diagnostics_sha256": (
                prometheus_diagnostics[index].diagnostics_sha256
            ),
            "opensearch_diagnostics_sha256": (
                opensearch_diagnostics[index].diagnostics_sha256
            ),
            "accepted": not ordered_reasons,
            "rejection_reason_codes": tuple(item.value for item in ordered_reasons),
        }
        if exact_opensearch_rejections:
            body["opensearch_rejection_codes"] = exact_opensearch_rejections
        audits.append(BaselineWindowAuditV023.model_validate(_sealed_model(BaselineWindowAuditV023, body)))
    accepted_ordinals = tuple(item.window_ordinal for item in audits if item.accepted)
    accepted_results = tuple(window_results[index - 1] for index in accepted_ordinals)
    accepted_logs = tuple(
        record
        for results in accepted_results
        for result in results
        if result.source is EvidenceSourceV22.LOGS
        for record in result.records
        if isinstance(record, LogRecordV22) and record.service == "checkout"
    )
    logs_nonempty_window_count = sum(
        logs is not None
        and logs.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
        for logs in (
            _one_result(results, EvidenceSourceV22.LOGS)
            for results in window_results
        )
    )
    aggregate_reasons: list[str] = []
    if len(accepted_ordinals) < profile.minimum_accepted_windows:
        aggregate_reasons.append("MINIMUM_ACCEPTED_WINDOWS_NOT_MET")
    if logs_nonempty_window_count < 3:
        aggregate_reasons.append("LOGS_NONEMPTY_WINDOW_MINIMUM_NOT_MET")
    if len(accepted_logs) < 10:
        aggregate_reasons.append("CHECKOUT_LOG_RECORD_MINIMUM_NOT_MET")
    has_normal_template = any(record.severity == "DIAGNOSTIC" for record in accepted_logs)
    if not has_normal_template:
        aggregate_reasons.append("NORMAL_CHECKOUT_LOG_TEMPLATE_MISSING")
    if any(
        any(reason.value.startswith("OPENSEARCH_") for reason in item.rejection_reason_codes)
        for item in audits
    ):
        aggregate_reasons.append("OPENSEARCH_ALL_WINDOWS_QUALITY_NOT_MET")
    aggregate = tuple(sorted(set(aggregate_reasons)))
    passed = not aggregate
    body = {
        "schema_version": "ecomsre.product.baseline-window-evaluation.v023",
        "terminal": (
            BASELINE_PREFLIGHT_PASS_V023 if passed else BASELINE_PREFLIGHT_BLOCKED_V023
        ),
        "profile_sha256": profile.profile_sha256,
        "active_opensearch_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "windows": tuple(item.model_dump(mode="json") for item in audits),
        "accepted_ordinals": accepted_ordinals,
        "logs_nonempty_window_count": logs_nonempty_window_count,
        "accepted_checkout_log_record_count": len(accepted_logs),
        "has_normal_checkout_log_template": has_normal_template,
        "aggregate_rejection_reason_codes": aggregate,
        "final_builder_would_pass": passed,
    }
    return BaselineWindowEvaluationV023.model_validate(
        _sealed_model(BaselineWindowEvaluationV023, body)
    )


__all__ = (
    "BASELINE_PREFLIGHT_BLOCKED_V023",
    "BASELINE_PREFLIGHT_PASS_V023",
    "BaselineRejectionReasonCodeV023",
    "BaselineWindowAuditV023",
    "BaselineWindowEvaluationV023",
    "OpenSearchWindowDiagnosticsV023",
    "ProductBaselineReadinessAuditRepositoryV023",
    "ProductBaselineReadinessAuditV023",
    "ProductBaselineReadinessProfileV023",
    "PrometheusTemplateDiagnosticV023",
    "PrometheusWindowDiagnosticsV023",
    "evaluate_baseline_windows_v023",
    "put_readiness_audit_in_transaction_v023",
)
