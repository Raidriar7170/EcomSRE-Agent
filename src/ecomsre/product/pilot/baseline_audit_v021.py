"""Observable Product v0.2.1 baseline-window acceptance contracts."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
import json
import sqlite3
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.contracts import ConnectorKindV1, ProductModelV1
from ecomsre.product.errors import ProductError, not_found
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


_SUCCESS_STATUSES_V021 = frozenset(
    {
        ReadSourceStatusV22.SUCCESS_EMPTY,
        ReadSourceStatusV22.SUCCESS_NONEMPTY,
    }
)


class BaselineWindowDispositionV021(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class BaselineRejectionReasonCodeV021(str, Enum):
    WINDOW_HAS_NO_RESULTS = "WINDOW_HAS_NO_RESULTS"
    SOURCE_STATUS_FAILED = "SOURCE_STATUS_FAILED"
    SOURCE_RESULT_TRUNCATED = "SOURCE_RESULT_TRUNCATED"
    WINDOW_HAS_NO_RECORDS = "WINDOW_HAS_NO_RECORDS"
    REQUIRED_SOURCE_MISSING = "REQUIRED_SOURCE_MISSING"
    REQUIRED_SOURCE_DUPLICATED = "REQUIRED_SOURCE_DUPLICATED"
    TARGET_COVERAGE_INCOMPLETE = "TARGET_COVERAGE_INCOMPLETE"
    CONNECTOR_SOURCE_SET_INVALID = "CONNECTOR_SOURCE_SET_INVALID"
    WINDOW_TIME_INVALID = "WINDOW_TIME_INVALID"
    SERVICE_ALIAS_UNRESOLVED = "SERVICE_ALIAS_UNRESOLVED"


_REASON_ORDER_V021 = {
    reason: index for index, reason in enumerate(BaselineRejectionReasonCodeV021)
}


def _ordered_reasons_v021(
    reasons: set[BaselineRejectionReasonCodeV021],
) -> tuple[BaselineRejectionReasonCodeV021, ...]:
    return tuple(sorted(reasons, key=_REASON_ORDER_V021.__getitem__))


class BaselineConnectorBindingV021(ProductModelV1):
    """Public connector identity aligned with one source result."""

    schema_version: Literal["ecomsre.product.baseline-connector-binding.v021"] = (
        "ecomsre.product.baseline-connector-binding.v021"
    )
    connector_name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    connector_kind: ConnectorKindV1


class BaselineConnectorExpectationV021(ProductModelV1):
    """Expected baseline-capable source set for one configured connector."""

    schema_version: Literal["ecomsre.product.baseline-connector-expectation.v021"] = (
        "ecomsre.product.baseline-connector-expectation.v021"
    )
    connector_name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    connector_kind: ConnectorKindV1
    expected_sources: tuple[EvidenceSourceV22, ...]

    @model_validator(mode="after")
    def require_canonical_sources(self) -> "BaselineConnectorExpectationV021":
        if self.expected_sources != tuple(
            sorted(set(self.expected_sources), key=lambda item: item.value)
        ):
            raise ValueError("baseline connector expected sources are not canonical")
        return self


class BaselineSourceWindowAuditV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-source-window-audit.v021"] = (
        "ecomsre.product.baseline-source-window-audit.v021"
    )
    connector_name: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    connector_kind: ConnectorKindV1
    source: EvidenceSourceV22
    window_started_at: datetime
    window_ended_at: datetime
    requested_services: tuple[str, ...]
    covered_services: tuple[str, ...]
    missing_services: tuple[str, ...]
    status: ReadSourceStatusV22
    record_count: int = Field(ge=0)
    truncated: bool
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    safe_error_code: str | None
    target_complete_required: bool
    target_complete_satisfied: bool
    accepted: bool
    rejection_reason_codes: tuple[BaselineRejectionReasonCodeV021, ...]
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BaselineWindowAuditV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-window-audit.v021"] = (
        "ecomsre.product.baseline-window-audit.v021"
    )
    window_ordinal: int = Field(ge=1, le=60)
    window: ConnectorWindowV1
    connector_expectations: tuple[BaselineConnectorExpectationV021, ...]
    source_results: tuple[BaselineSourceWindowAuditV021, ...]
    has_any_record: bool
    required_sources_present: bool
    target_complete_sources_satisfied: bool
    disposition: BaselineWindowDispositionV021
    accepted: bool
    rejection_reason_codes: tuple[BaselineRejectionReasonCodeV021, ...]
    window_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_window(self) -> "BaselineWindowAuditV021":
        if self.accepted != (self.disposition is BaselineWindowDispositionV021.ACCEPTED):
            raise ValueError("baseline window disposition differs from acceptance")
        if self.accepted != (not self.rejection_reason_codes):
            raise ValueError("baseline window rejection reasons differ from acceptance")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"window_sha256"})
        )
        if self.window_sha256 != expected:
            raise ValueError("baseline window audit digest differs")
        return self


class BaselineWindowEvaluationV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-window-evaluation.v021"] = (
        "ecomsre.product.baseline-window-evaluation.v021"
    )
    required_complete_sources: tuple[EvidenceSourceV22, ...]
    windows: tuple[BaselineWindowAuditV021, ...]
    accepted_ordinals: tuple[int, ...]
    parity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_evaluation(self) -> "BaselineWindowEvaluationV021":
        expected_ordinals = tuple(item.window_ordinal for item in self.windows if item.accepted)
        if self.accepted_ordinals != expected_ordinals:
            raise ValueError("accepted window ordinals differ from window audits")
        expected = _parity_sha256_v021(
            windows=self.windows,
            required_complete_sources=self.required_complete_sources,
        )
        if self.parity_sha256 != expected:
            raise ValueError("baseline audit-builder parity digest differs")
        return self


class BaselineReadinessAuditV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-readiness-audit.v021"] = (
        "ecomsre.product.baseline-readiness-audit.v021"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    # Goal-prescribed service_ids are logical query names (for example, checkout).
    service_ids: tuple[str, ...]
    baseline_entity_service_ids: tuple[str, ...]
    build_policy: dict[str, Any]
    capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_complete_sources: tuple[EvidenceSourceV22, ...]
    windows: tuple[BaselineWindowAuditV021, ...]
    scheduled_window_count: int = Field(ge=0, le=60)
    configured_window_count: int = Field(ge=1, le=60)
    accepted_window_count: int = Field(ge=0, le=60)
    required_window_count: int = Field(ge=1, le=60)
    source_acceptance_rates: dict[str, float]
    coverage_matrix: dict[str, dict[str, int]]
    final_builder_would_pass: bool
    parity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_readiness_audit(self) -> "BaselineReadinessAuditV021":
        if self.service_ids != tuple(sorted(set(self.service_ids))):
            raise ValueError("baseline readiness service IDs are not canonical")
        if self.baseline_entity_service_ids != tuple(
            sorted(set(self.baseline_entity_service_ids))
        ):
            raise ValueError("baseline readiness entity service IDs are not canonical")
        if self.required_complete_sources != tuple(
            sorted(set(self.required_complete_sources), key=lambda item: item.value)
        ):
            raise ValueError("required complete baseline sources are not canonical")
        if self.accepted_window_count != sum(item.accepted for item in self.windows):
            raise ValueError("baseline readiness accepted-window count differs")
        if self.scheduled_window_count != len(self.windows):
            raise ValueError("baseline readiness scheduled-window count differs")
        if self.configured_window_count != int(self.build_policy["window_count"]):
            raise ValueError("baseline readiness configured-window count differs")
        if self.required_window_count != int(
            self.build_policy["minimum_successful_windows"]
        ):
            raise ValueError("baseline readiness required-window count differs")
        if self.final_builder_would_pass != (
            self.scheduled_window_count == self.configured_window_count
            and self.accepted_window_count >= self.required_window_count
        ):
            raise ValueError("baseline readiness builder disposition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"audit_sha256"})
        )
        if self.audit_sha256 != expected:
            raise ValueError("baseline readiness audit digest differs")
        return self


def _put_readiness_audit_in_transaction_v021(
    connection: sqlite3.Connection,
    audit: BaselineReadinessAuditV021,
    *,
    baseline_id: str,
    created_at: datetime,
) -> None:
    """Insert one audit using the caller's already-fenced transaction."""

    serialized = json.dumps(
        audit.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    existing = connection.execute(
        """SELECT environment_id, baseline_id, audit_sha256, payload_json
           FROM baseline_readiness_audits_v021
           WHERE baseline_id = ? OR audit_sha256 = ?""",
        (baseline_id, audit.audit_sha256),
    ).fetchone()
    if existing is not None:
        if (
            existing["environment_id"] != audit.environment_id
            or existing["baseline_id"] != baseline_id
            or existing["audit_sha256"] != audit.audit_sha256
            or existing["payload_json"] != serialized
        ):
            raise ProductError(
                "BASELINE_READINESS_AUDIT_IMMUTABLE_CONFLICT",
                "The baseline readiness audit already exists with different content.",
                status_code=409,
            )
        return
    connection.execute(
        """INSERT INTO baseline_readiness_audits_v021(
            audit_sha256, environment_id, baseline_id, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?)""",
        (
            audit.audit_sha256,
            audit.environment_id,
            baseline_id,
            serialized,
            created_at.isoformat(),
        ),
    )


class BaselineReadinessAuditRepositoryV021:
    """Create-once persistence for both successful and rejected window audits."""

    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def put(
        self,
        audit: BaselineReadinessAuditV021,
        *,
        baseline_id: str,
        created_at: datetime,
        fence: JobLeaseFenceV1 | None = None,
    ) -> None:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                _put_readiness_audit_in_transaction_v021(
                    connection,
                    audit,
                    baseline_id=baseline_id,
                    created_at=created_at,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_latest(self, environment_id: str) -> BaselineReadinessAuditV021:
        with self.store.connect() as connection:
            row = connection.execute(
                """SELECT payload_json
                   FROM baseline_readiness_audits_v021
                   WHERE environment_id = ?
                   ORDER BY created_at DESC, audit_sha256 DESC
                   LIMIT 1""",
                (environment_id,),
            ).fetchone()
        if row is None:
            raise not_found(
                "BASELINE_READINESS_AUDIT_NOT_FOUND",
                "The environment has no baseline readiness audit.",
            )
        return BaselineReadinessAuditV021.model_validate_json(row["payload_json"])

    def get_by_baseline(self, baseline_id: str) -> BaselineReadinessAuditV021:
        with self.store.connect() as connection:
            row = connection.execute(
                """SELECT payload_json
                   FROM baseline_readiness_audits_v021
                   WHERE baseline_id = ?""",
                (baseline_id,),
            ).fetchone()
        if row is None:
            raise not_found(
                "BASELINE_WINDOW_AUDIT_NOT_FOUND",
                "The baseline has no v0.2.1 window audit.",
            )
        return BaselineReadinessAuditV021.model_validate_json(row["payload_json"])


def _default_bindings_v021(
    results: tuple[ConnectorQueryResultV1, ...],
) -> tuple[BaselineConnectorBindingV021, ...]:
    return tuple(
        BaselineConnectorBindingV021(
            connector_name=f"legacy-{item.source.value.lower().replace('_', '-')}",
            connector_kind=ConnectorKindV1.FIXTURE,
        )
        for item in results
    )


def _default_expectations_v021(
    results: tuple[ConnectorQueryResultV1, ...],
    bindings: tuple[BaselineConnectorBindingV021, ...],
) -> tuple[BaselineConnectorExpectationV021, ...]:
    sources_by_connector: defaultdict[
        tuple[str, ConnectorKindV1], set[EvidenceSourceV22]
    ] = defaultdict(set)
    for result, binding in zip(results, bindings, strict=True):
        sources_by_connector[(binding.connector_name, binding.connector_kind)].add(
            result.source
        )
    return tuple(
        BaselineConnectorExpectationV021(
            connector_name=name,
            connector_kind=kind,
            expected_sources=tuple(sorted(sources, key=lambda item: item.value)),
        )
        for (name, kind), sources in sorted(
            sources_by_connector.items(),
            key=lambda item: (item[0][0], item[0][1].value),
        )
    )


def _parity_sha256_v021(
    *,
    windows: tuple[BaselineWindowAuditV021, ...],
    required_complete_sources: tuple[EvidenceSourceV22, ...],
) -> str:
    return semantic_sha256_v22(
        {
            "window_inputs": [
                {
                    "window_sha256": window.window_sha256,
                    "connector_expectations": [
                        expectation.model_dump(mode="json")
                        for expectation in window.connector_expectations
                    ],
                    "source_results": [
                        {
                            "connector_name": result.connector_name,
                            "connector_kind": result.connector_kind.value,
                            "source": result.source.value,
                            "result_sha256": result.result_sha256,
                        }
                        for result in window.source_results
                    ],
                }
                for window in windows
            ],
            "required_complete_sources": [
                item.value for item in required_complete_sources
            ],
            "accepted_ordinals": [item.window_ordinal for item in windows if item.accepted],
            "rejection_reason_codes": [
                [reason.value for reason in item.rejection_reason_codes]
                for item in windows
            ],
        }
    )


def evaluate_baseline_windows_v021(
    *,
    window_results: tuple[tuple[ConnectorQueryResultV1, ...], ...],
    required_complete_sources: tuple[EvidenceSourceV22, ...] = (),
    expected_windows: tuple[ConnectorWindowV1, ...],
    connector_bindings: tuple[tuple[BaselineConnectorBindingV021, ...], ...] | None = None,
    connector_expectations: tuple[
        tuple[BaselineConnectorExpectationV021, ...], ...
    ]
    | None = None,
) -> BaselineWindowEvaluationV021:
    """Evaluate every window once for both the public audit and real builder."""

    required = tuple(sorted(set(required_complete_sources), key=lambda item: item.value))
    if len(window_results) != len(expected_windows):
        raise ValueError("baseline result and expected-window counts differ")
    bindings = connector_bindings or tuple(
        _default_bindings_v021(results) for results in window_results
    )
    if len(bindings) != len(window_results) or any(
        len(result_rows) != len(binding_rows)
        for result_rows, binding_rows in zip(window_results, bindings, strict=True)
    ):
        raise ValueError("baseline connector bindings do not align with source results")
    expectations = connector_expectations or tuple(
        _default_expectations_v021(results, binding_rows)
        for results, binding_rows in zip(window_results, bindings, strict=True)
    )
    if len(expectations) != len(window_results):
        raise ValueError("baseline connector expectations do not align with windows")

    window_audits: list[BaselineWindowAuditV021] = []
    for ordinal, (results, expected_window, result_bindings, window_expectations) in enumerate(
        zip(window_results, expected_windows, bindings, expectations, strict=True),
        start=1,
    ):
        aggregate_reasons: set[BaselineRejectionReasonCodeV021] = set()
        if not results:
            aggregate_reasons.add(BaselineRejectionReasonCodeV021.WINDOW_HAS_NO_RESULTS)
        actual_by_connector: defaultdict[
            tuple[str, ConnectorKindV1], list[EvidenceSourceV22]
        ] = defaultdict(list)
        for result, binding in zip(results, result_bindings, strict=True):
            actual_by_connector[(binding.connector_name, binding.connector_kind)].append(
                result.source
            )
        expected_by_connector = {
            (item.connector_name, item.connector_kind): item.expected_sources
            for item in window_expectations
        }
        invalid_connector_keys = {
            key
            for key in set(actual_by_connector).union(expected_by_connector)
            if set(actual_by_connector.get(key, ()))
            != set(expected_by_connector.get(key, ()))
        }
        if invalid_connector_keys:
            aggregate_reasons.add(
                BaselineRejectionReasonCodeV021.CONNECTOR_SOURCE_SET_INVALID
            )
        source_counts = Counter(item.source for item in results)
        if any(source_counts[source] == 0 for source in required):
            aggregate_reasons.add(BaselineRejectionReasonCodeV021.REQUIRED_SOURCE_MISSING)
        if any(source_counts[source] > 1 for source in required):
            aggregate_reasons.add(
                BaselineRejectionReasonCodeV021.REQUIRED_SOURCE_DUPLICATED
            )

        source_audits: list[BaselineSourceWindowAuditV021] = []
        for result, binding in zip(results, result_bindings, strict=True):
            source_reasons: set[BaselineRejectionReasonCodeV021] = set()
            if result.window != expected_window:
                source_reasons.add(BaselineRejectionReasonCodeV021.WINDOW_TIME_INVALID)
            if result.status not in _SUCCESS_STATUSES_V021:
                source_reasons.add(BaselineRejectionReasonCodeV021.SOURCE_STATUS_FAILED)
            if result.truncated:
                source_reasons.add(
                    BaselineRejectionReasonCodeV021.SOURCE_RESULT_TRUNCATED
                )
            target_required = result.source in required
            target_satisfied = set(result.requested_services).issubset(
                result.covered_services
            )
            if target_required and not target_satisfied:
                source_reasons.add(
                    BaselineRejectionReasonCodeV021.TARGET_COVERAGE_INCOMPLETE
                )
            if result.safe_error_code == "SERVICE_ALIAS_UNRESOLVED":
                source_reasons.add(
                    BaselineRejectionReasonCodeV021.SERVICE_ALIAS_UNRESOLVED
                )
            if target_required and source_counts[result.source] > 1:
                source_reasons.add(
                    BaselineRejectionReasonCodeV021.REQUIRED_SOURCE_DUPLICATED
                )
            if (binding.connector_name, binding.connector_kind) in invalid_connector_keys:
                source_reasons.add(
                    BaselineRejectionReasonCodeV021.CONNECTOR_SOURCE_SET_INVALID
                )
            aggregate_reasons.update(source_reasons)
            source_audits.append(
                BaselineSourceWindowAuditV021(
                    connector_name=binding.connector_name,
                    connector_kind=binding.connector_kind,
                    source=result.source,
                    window_started_at=result.window.started_at,
                    window_ended_at=result.window.ended_at,
                    requested_services=result.requested_services,
                    covered_services=result.covered_services,
                    missing_services=tuple(
                        sorted(set(result.requested_services) - set(result.covered_services))
                    ),
                    status=result.status,
                    record_count=len(result.records),
                    truncated=result.truncated,
                    latency_ms=result.latency_ms,
                    safe_error_code=result.safe_error_code,
                    target_complete_required=target_required,
                    target_complete_satisfied=target_satisfied,
                    accepted=not source_reasons,
                    rejection_reason_codes=_ordered_reasons_v021(source_reasons),
                    result_sha256=result.result_sha256,
                )
            )

        has_any_record = any(item.records for item in results)
        if not has_any_record:
            aggregate_reasons.add(BaselineRejectionReasonCodeV021.WINDOW_HAS_NO_RECORDS)
        required_sources_present = all(source_counts[source] >= 1 for source in required)
        target_complete_sources_satisfied = all(
            source_counts[source] == 1
            and set(next(item for item in results if item.source is source).requested_services)
            .issubset(next(item for item in results if item.source is source).covered_services)
            for source in required
        )
        reasons = _ordered_reasons_v021(aggregate_reasons)
        payload: dict[str, Any] = {
            "window_ordinal": ordinal,
            "window": expected_window,
            "connector_expectations": tuple(
                sorted(
                    window_expectations,
                    key=lambda item: (item.connector_name, item.connector_kind.value),
                )
            ),
            "source_results": tuple(
                sorted(
                    source_audits,
                    key=lambda item: (item.source.value, item.connector_name),
                )
            ),
            "has_any_record": has_any_record,
            "required_sources_present": required_sources_present,
            "target_complete_sources_satisfied": target_complete_sources_satisfied,
            "disposition": (
                BaselineWindowDispositionV021.ACCEPTED
                if not reasons
                else BaselineWindowDispositionV021.REJECTED
            ),
            "accepted": not reasons,
            "rejection_reason_codes": reasons,
        }
        draft = BaselineWindowAuditV021.model_construct(
            **payload,
            window_sha256="0" * 64,
        )
        window_audits.append(
            BaselineWindowAuditV021.model_validate(
                {
                    **payload,
                    "window_sha256": semantic_sha256_v22(
                        draft.model_dump(mode="json", exclude={"window_sha256"})
                    ),
                }
            )
        )

    windows = tuple(window_audits)
    payload = {
        "required_complete_sources": required,
        "windows": windows,
        "accepted_ordinals": tuple(item.window_ordinal for item in windows if item.accepted),
    }
    return BaselineWindowEvaluationV021.model_validate(
        {
            **payload,
            "parity_sha256": _parity_sha256_v021(
                windows=windows,
                required_complete_sources=required,
            ),
        }
    )


def build_baseline_readiness_audit_v021(
    *,
    environment_id: str,
    service_ids: tuple[str, ...],
    baseline_entity_service_ids: tuple[str, ...],
    build_policy: Mapping[str, Any],
    capability_sha256: str,
    required_complete_sources: tuple[EvidenceSourceV22, ...],
    window_results: tuple[tuple[ConnectorQueryResultV1, ...], ...],
    expected_windows: tuple[ConnectorWindowV1, ...],
    connector_bindings: tuple[tuple[BaselineConnectorBindingV021, ...], ...] | None = None,
    connector_expectations: tuple[
        tuple[BaselineConnectorExpectationV021, ...], ...
    ]
    | None = None,
) -> BaselineReadinessAuditV021:
    evaluation = evaluate_baseline_windows_v021(
        window_results=window_results,
        required_complete_sources=required_complete_sources,
        expected_windows=expected_windows,
        connector_bindings=connector_bindings,
        connector_expectations=connector_expectations,
    )
    observed: Counter[EvidenceSourceV22] = Counter()
    accepted: Counter[EvidenceSourceV22] = Counter()
    coverage: defaultdict[EvidenceSourceV22, Counter[str]] = defaultdict(Counter)
    for window in evaluation.windows:
        for result in window.source_results:
            observed[result.source] += 1
            accepted[result.source] += int(result.accepted)
            for service in result.covered_services:
                coverage[result.source][service] += 1
    policy = dict(build_policy)
    required_window_count = int(policy["minimum_successful_windows"])
    configured_window_count = int(policy["window_count"])
    scheduled_window_count = len(evaluation.windows)
    payload: dict[str, Any] = {
        "environment_id": environment_id,
        "service_ids": tuple(sorted(set(service_ids))),
        "baseline_entity_service_ids": tuple(
            sorted(set(baseline_entity_service_ids))
        ),
        "build_policy": policy,
        "capability_sha256": capability_sha256,
        "required_complete_sources": evaluation.required_complete_sources,
        "windows": evaluation.windows,
        "scheduled_window_count": scheduled_window_count,
        "configured_window_count": configured_window_count,
        "accepted_window_count": len(evaluation.accepted_ordinals),
        "required_window_count": required_window_count,
        "source_acceptance_rates": {
            source.value: accepted[source] / observed[source]
            for source in sorted(observed, key=lambda item: item.value)
        },
        "coverage_matrix": {
            source.value: {
                service: counts[service]
                for service in sorted(counts)
            }
            for source, counts in sorted(coverage.items(), key=lambda item: item[0].value)
        },
        "final_builder_would_pass": (
            scheduled_window_count == configured_window_count
            and len(evaluation.accepted_ordinals) >= required_window_count
        ),
        "parity_sha256": evaluation.parity_sha256,
    }
    draft = BaselineReadinessAuditV021.model_construct(
        **payload,
        audit_sha256="0" * 64,
    )
    return BaselineReadinessAuditV021.model_validate(
        {
            **payload,
            "audit_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"audit_sha256"})
            ),
        }
    )


__all__ = (
    "BaselineConnectorBindingV021",
    "BaselineConnectorExpectationV021",
    "BaselineReadinessAuditV021",
    "BaselineReadinessAuditRepositoryV021",
    "BaselineRejectionReasonCodeV021",
    "BaselineSourceWindowAuditV021",
    "BaselineWindowAuditV021",
    "BaselineWindowDispositionV021",
    "BaselineWindowEvaluationV021",
    "build_baseline_readiness_audit_v021",
    "evaluate_baseline_windows_v021",
)
