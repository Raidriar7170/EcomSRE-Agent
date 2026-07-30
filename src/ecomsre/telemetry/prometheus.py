"""Frozen-series Prometheus counter acquisition for Phase 0."""

from __future__ import annotations

import base64
import json
import math
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_bytes,
    sha256_file,
)
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.phase0.models import MeasurementPhase
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    OwnedEndpoint,
    OwnedHttpClient,
    PhaseWindow,
    _PRODUCTION_TRANSPORT_TOKEN,
    _owned_http_client_has_production_integrity,
)


_UPSTREAM_COMMIT = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_FROZEN_CAPABILITY_TOKEN = object()
_PRODUCTION_ACQUISITION_TOKEN = object()
_TEST_CAPABILITY_TOKEN = object()
_PROMETHEUS_RECEIPT_TOKEN = object()
_ACCEPTANCE_MINIMUM_ATTEMPTS = 200
_ACCEPTANCE_DEADLINE_SECONDS = 180.0
_PINNED_GETADS_OPERATION = "oteldemo.AdService/GetAds"
_PINNED_PROBE_PATH = "/api/data?contextKeys=telescopes"


class FixtureState(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    CANDIDATE = "CANDIDATE"
    FROZEN = "FROZEN"


class _FixtureModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class SourceFact(_FixtureModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    description: str = Field(min_length=1)


class TargetFixture(_FixtureModel):
    service: str = Field(min_length=1)
    target_port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp"]


class PromotionProof(_FixtureModel):
    current_run_id: str = Field(pattern=_RUN_ID_PATTERN)
    raw_artifacts: tuple[str, ...] = Field(min_length=1)
    artifact_sha256: dict[str, str]
    emitted_identity_artifacts: tuple[str, ...] = Field(min_length=1)
    counter_mapping_artifact: str = Field(min_length=1)
    probe_getads_attribution_artifact: str = Field(min_length=1)
    upstream_sha256: str = Field(pattern=_SHA256_PATTERN)
    compose_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    fixture_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    review_decision: Literal["APPROVED"]
    review_artifact: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_immutable_observer_proof(self) -> "PromotionProof":
        paths = (
            *self.raw_artifacts,
            *self.emitted_identity_artifacts,
            self.counter_mapping_artifact,
            self.probe_getads_attribution_artifact,
            self.review_artifact,
        )
        if set(self.artifact_sha256) != set(paths) or any(
            _not_sha256(digest) for digest in self.artifact_sha256.values()
        ):
            raise ValueError("promotion artifact hashes are incomplete")
        expected_prefix = f"observer-visible/{self.current_run_id}/"
        if any(
            not path.startswith(expected_prefix) or "evaluator-only" in path.casefold()
            for path in paths
        ):
            raise ValueError("promotion proof must be current-run observer evidence")
        return self


class ErrorClassification(_FixtureModel):
    label: str = Field(min_length=1)
    values: tuple[str, ...] = Field(min_length=1)


class SeriesIdentity(_FixtureModel):
    labels: dict[str, str]

    @model_validator(mode="after")
    def require_nonempty_labels(self) -> "SeriesIdentity":
        if not self.labels or any(
            not key or not value for key, value in self.labels.items()
        ):
            raise ValueError("frozen series identity labels must be nonempty")
        return self


class PrometheusQueryFixture(_FixtureModel):
    state: FixtureState
    target: TargetFixture
    request_template: str | None
    expected_response_schema: str | None
    upstream_tag: Literal["3.0.0"]
    upstream_commit: Literal[_UPSTREAM_COMMIT]
    applicable_service: str | None
    failure_semantics: tuple[str, ...] = Field(min_length=1)
    freshness_semantics: str = Field(min_length=1)
    source_facts: tuple[SourceFact, ...] = Field(min_length=1)
    candidate_metric: str | None
    total_query: str | None
    error_query: str | None
    target_incarnation_query: str | None
    operation: str | None
    counter_identity_labels: tuple[str, ...] | None
    expected_total_series: tuple[SeriesIdentity, ...] | None
    expected_target_incarnation_series: SeriesIdentity | None
    error_classification: ErrorClassification | None
    scrape_interval_seconds: float | None = Field(default=None, gt=0, le=60)
    scrape_interval_tolerance_seconds: float | None = Field(
        default=None,
        ge=0,
        le=5,
    )
    maximum_scrape_lag_seconds: float | None = Field(default=None, ge=0)
    boundary_rule: Literal["(start_sample_timestamp,end_sample_timestamp]"] | None
    cardinality_rule: Literal["exact_frozen_series_set"] | None
    reset_policy: Literal["reject_any_counter_decrease_or_target_restart"] | None
    staleness_policy: Literal["reject_stale_marker_or_lag"] | None
    zero_series_rule: Literal["absent_error_series_means_zero"] | None

    @model_validator(mode="after")
    def require_complete_frozen_prometheus_contract(
        self,
    ) -> "PrometheusQueryFixture":
        if self.state is not FixtureState.FROZEN:
            return self
        required: tuple[object | None, ...] = (
            self.request_template,
            self.expected_response_schema,
            self.applicable_service,
            self.candidate_metric,
            self.total_query,
            self.error_query,
            self.target_incarnation_query,
            self.operation,
            self.counter_identity_labels,
            self.expected_total_series,
            self.expected_target_incarnation_series,
            self.error_classification,
            self.scrape_interval_seconds,
            self.scrape_interval_tolerance_seconds,
            self.maximum_scrape_lag_seconds,
            self.boundary_rule,
            self.cardinality_rule,
            self.reset_policy,
            self.staleness_policy,
            self.zero_series_rule,
        )
        if any(item is None for item in required):
            raise ValueError("FROZEN Prometheus fixture is incomplete")
        assert (
            self.total_query is not None
            and self.error_query is not None
            and self.target_incarnation_query is not None
        )
        if any(
            extrapolator in query.casefold()
            for query in (
                self.total_query,
                self.error_query,
                self.target_incarnation_query,
            )
            for extrapolator in ("rate(", "increase(", "delta(")
        ):
            raise ValueError("Prometheus acceptance queries must expose raw counters")
        assert self.counter_identity_labels is not None
        if len(set(self.counter_identity_labels)) != len(self.counter_identity_labels):
            raise ValueError("counter identity labels must be unique")
        assert self.expected_total_series is not None
        identities = [
            tuple(sorted(series.labels.items()))
            for series in self.expected_total_series
        ]
        if not identities or len(set(identities)) != len(identities):
            raise ValueError("frozen Prometheus series set is empty or duplicated")
        assert self.error_classification is not None
        if self.error_classification.label not in self.counter_identity_labels:
            raise ValueError("error classification is outside counter identity")
        return self


class BackendQueryFixture(_FixtureModel):
    state: FixtureState
    target: TargetFixture
    request_template: str | None
    expected_response_schema: str | None
    upstream_tag: Literal["3.0.0"]
    upstream_commit: Literal[_UPSTREAM_COMMIT]
    applicable_service: str | None
    failure_semantics: tuple[str, ...] = Field(min_length=1)
    freshness_semantics: str = Field(min_length=1)
    source_facts: tuple[SourceFact, ...] = Field(min_length=1)
    service_identity: str | None
    operation: str | None = None
    index: str | None = None
    service_identity_field: str | None = None
    timestamp_field: str | None = None
    trace_id_field: str | None = None
    span_id_field: str | None = None

    @model_validator(mode="after")
    def require_complete_frozen_backend(self) -> "BackendQueryFixture":
        if self.state is not FixtureState.FROZEN:
            return self
        common = (
            self.request_template,
            self.expected_response_schema,
            self.applicable_service,
            self.service_identity,
        )
        if any(value is None for value in common):
            raise ValueError("FROZEN backend fixture is incomplete")
        if self.target.service == "jaeger" and self.operation is None:
            raise ValueError("FROZEN Jaeger fixture requires an operation")
        if self.target.service == "opensearch" and any(
            value is None
            for value in (
                self.index,
                self.service_identity_field,
                self.timestamp_field,
            )
        ):
            raise ValueError("FROZEN OpenSearch fixture is incomplete")
        return self


class ProbeQueryFixture(_FixtureModel):
    state: FixtureState
    target: TargetFixture
    method: Literal["GET"] | None
    path: str | None
    input: str | None
    response_contract: str | None
    exit_semantics: str | None
    attribution_mechanism: str | None
    getads_proof_artifact: str | None
    hidden_input_denial_required: bool
    required_phases: tuple[Literal["baseline", "fault", "recovery"], ...]
    source_facts: tuple[SourceFact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_frozen_probe_attribution(self) -> "ProbeQueryFixture":
        if self.state is not FixtureState.FROZEN:
            return self
        if any(
            value is None
            for value in (
                self.method,
                self.path,
                self.input,
                self.response_contract,
                self.exit_semantics,
                self.attribution_mechanism,
                self.getads_proof_artifact,
            )
        ):
            raise ValueError("FROZEN probe fixture is incomplete")
        if not self.hidden_input_denial_required or self.required_phases != (
            "baseline",
            "fault",
            "recovery",
        ):
            raise ValueError("FROZEN probe requires hidden denial and all phases")
        return self


class TelemetryQueryRegistry(_FixtureModel):
    schema_version: Literal["phase0.telemetry-query-registry.v1"]
    fixture_version: str = Field(min_length=1)
    state: FixtureState
    upstream_tag: Literal["3.0.0"]
    upstream_commit: Literal[_UPSTREAM_COMMIT]
    compose_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_facts: tuple[SourceFact, ...] = Field(min_length=1)
    promotion_proof: PromotionProof | None
    prometheus: PrometheusQueryFixture
    jaeger: BackendQueryFixture
    opensearch: BackendQueryFixture
    probe: ProbeQueryFixture

    @model_validator(mode="after")
    def require_promotion_and_forbid_legacy_fallback(
        self,
    ) -> "TelemetryQueryRegistry":
        serialized = canonical_json_bytes(self.model_dump(mode="json")).decode("utf-8")
        if "app." in serialized.casefold():
            raise ValueError(r"app.* compatibility fallback is forbidden")
        if self.state is FixtureState.FROZEN:
            if self.promotion_proof is None or any(
                backend.state is not FixtureState.FROZEN
                for backend in (
                    self.prometheus,
                    self.jaeger,
                    self.opensearch,
                    self.probe,
                )
            ):
                raise ValueError("FROZEN registry requires complete promotion evidence")
            if self.promotion_proof.compose_config_sha256 != self.compose_config_sha256:
                raise ValueError("promotion Compose hash does not match fixture")
            if (
                self.probe.getads_proof_artifact
                != self.promotion_proof.probe_getads_attribution_artifact
            ):
                raise ValueError("probe attribution is not bound to promotion proof")
            contract_payload = self.model_dump(mode="json", exclude_unset=True)
            contract_payload.pop("promotion_proof")
            if self.promotion_proof.fixture_content_sha256 != canonical_json_sha256(
                contract_payload
            ):
                raise ValueError("promotion fixture content hash does not match")
        elif self.promotion_proof is not None:
            raise ValueError("unfrozen registry cannot carry promotion evidence")
        return self


class LoadedTelemetryQueryRegistry(_FixtureModel):
    registry: TelemetryQueryRegistry
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    synthetic_test_only: bool = False

    def require_frozen(self) -> TelemetryQueryRegistry:
        if (
            self.registry.state is not FixtureState.FROZEN
            or not self.synthetic_test_only
        ):
            raise ValueError("QUERY_FIXTURE_NOT_FROZEN")
        return self.registry


@dataclass(frozen=True, init=False)
class TestTelemetryQueryCapability:
    """Private parser-test authority that can never authorize production I/O."""

    _loaded: LoadedTelemetryQueryRegistry
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        loaded: LoadedTelemetryQueryRegistry,
    ) -> None:
        if _token is not _TEST_CAPABILITY_TOKEN:
            raise TypeError("test telemetry capability must come from test loader")
        object.__setattr__(self, "_loaded", loaded)
        object.__setattr__(self, "_token", _TEST_CAPABILITY_TOKEN)

    @property
    def registry(self) -> TelemetryQueryRegistry:
        return self._loaded.registry

    @property
    def content_sha256(self) -> str:
        return self._loaded.content_sha256

    @property
    def synthetic_test_only(self) -> bool:
        return True

    def require_frozen(self) -> TelemetryQueryRegistry:
        if (
            self._token is not _TEST_CAPABILITY_TOKEN
            or self.registry.state is not FixtureState.FROZEN
        ):
            raise ValueError("QUERY_FIXTURE_NOT_FROZEN")
        return self.registry


@dataclass(frozen=True)
class FrozenRegistryAudit:
    """Read-only validation report; it is deliberately not runtime authority."""

    run_id: str
    valid: bool
    verified_hashes: tuple[tuple[str, str], ...] = ()
    reason: str | None = None


@dataclass(frozen=True, init=False)
class _ProductionAcquisitionReceipt:
    run_id: str
    store_root: str
    window_sha256: str
    exchange_sha256: tuple[str, ...]
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str,
        store_root: Path,
        windows: tuple[PhaseWindow, ...],
        exchanges: tuple[HttpExchange, ...],
    ) -> None:
        if _token is not _PRODUCTION_ACQUISITION_TOKEN:
            raise TypeError("production acquisition receipt must come from transport")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "store_root", str(store_root))
        object.__setattr__(
            self,
            "window_sha256",
            canonical_json_sha256(
                [
                    {
                        "run_id": window.run_id,
                        "cycle_number": window.cycle_number,
                        "phase": window.scenario_phase.value,
                        "utc_started_at": window.utc_started_at.isoformat(),
                        "utc_ended_at": window.utc_ended_at.isoformat(),
                        "monotonic_started_at": window.monotonic_started_at,
                        "monotonic_ended_at": window.monotonic_ended_at,
                    }
                    for window in windows
                ]
            ),
        )
        object.__setattr__(
            self,
            "exchange_sha256",
            tuple(
                canonical_json_sha256(
                    {
                        "method": exchange.request.method,
                        "target": exchange.request.target,
                        "raw_sha256": exchange.raw_sha256,
                        "status": exchange.status_code,
                        "reason": exchange.reason.value,
                        "started_at": exchange.started_at.isoformat(),
                        "ended_at": exchange.ended_at.isoformat(),
                    }
                )
                for exchange in exchanges
            ),
        )
        object.__setattr__(self, "_token", _PRODUCTION_ACQUISITION_TOKEN)

    def is_authentic(self, store: ObserverEvidenceStore) -> bool:
        return (
            self._token is _PRODUCTION_ACQUISITION_TOKEN
            and self.run_id == store.run_id
            and self.store_root == str(store.root)
            and bool(self.exchange_sha256)
        )


@dataclass(frozen=True, init=False)
class FrozenTelemetryQueryCapability:
    """Opaque, live-verified authority for one current-run frozen registry."""

    _loaded: LoadedTelemetryQueryRegistry
    _store: ObserverEvidenceStore
    _verified_hashes: tuple[tuple[str, str], ...]
    _acquisition_receipt: _ProductionAcquisitionReceipt
    _token: object

    def __init__(
        self,
        *,
        _token: object | None = None,
        loaded: LoadedTelemetryQueryRegistry,
        store: ObserverEvidenceStore,
        verified_hashes: dict[str, str],
        acquisition_receipt: _ProductionAcquisitionReceipt | None = None,
    ) -> None:
        if (
            _token is not _FROZEN_CAPABILITY_TOKEN
            or not isinstance(acquisition_receipt, _ProductionAcquisitionReceipt)
            or not acquisition_receipt.is_authentic(store)
        ):
            raise TypeError("frozen telemetry capability must come from live discovery")
        object.__setattr__(self, "_loaded", loaded)
        object.__setattr__(self, "_store", store)
        object.__setattr__(
            self,
            "_verified_hashes",
            tuple(sorted(verified_hashes.items())),
        )
        object.__setattr__(self, "_acquisition_receipt", acquisition_receipt)
        object.__setattr__(self, "_token", _FROZEN_CAPABILITY_TOKEN)

    @property
    def registry(self) -> TelemetryQueryRegistry:
        return self._loaded.registry

    @property
    def content_sha256(self) -> str:
        return self._loaded.content_sha256

    @property
    def run_id(self) -> str:
        return self._store.run_id

    @property
    def store(self) -> ObserverEvidenceStore:
        return self._store

    def is_authentic(self) -> bool:
        if (
            self._token is not _FROZEN_CAPABILITY_TOKEN
            or not isinstance(self._store, ObserverEvidenceStore)
            or not self._acquisition_receipt.is_authentic(self._store)
        ):
            return False
        try:
            hashes = _verify_promotion_artifacts(self._loaded, self._store)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        return tuple(sorted(hashes.items())) == self._verified_hashes


def load_query_registry(
    source: Path | str | dict[str, Any],
) -> LoadedTelemetryQueryRegistry:
    """Load a strict registry and bind it to the exact canonical/file bytes."""
    if isinstance(source, dict):
        payload = source
        content = canonical_json_bytes(payload)
    else:
        path = Path(source)
        content = path.read_bytes()
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("telemetry query registry is invalid JSON") from error
    try:
        registry = TelemetryQueryRegistry.model_validate(payload)
    except ValidationError:
        raise
    return LoadedTelemetryQueryRegistry(
        registry=registry,
        content_sha256=sha256_bytes(content),
    )


def _load_test_query_registry(source: Path | str) -> TestTelemetryQueryCapability:
    """Load synthetic parser data only from this repository's test fixture tree."""
    path = Path(source).resolve(strict=True)
    test_root = (Path(__file__).resolve().parents[3] / "tests" / "fixtures").resolve(
        strict=True
    )
    if test_root not in path.parents:
        raise ValueError("synthetic frozen registry must stay under tests/fixtures")
    return _issue_test_query_capability(path)


def _issue_test_query_capability(
    source: Path | str | dict[str, Any],
) -> TestTelemetryQueryCapability:
    loaded = load_query_registry(source)
    return TestTelemetryQueryCapability(
        _token=_TEST_CAPABILITY_TOKEN,
        loaded=LoadedTelemetryQueryRegistry(
            registry=loaded.registry,
            content_sha256=loaded.content_sha256,
            synthetic_test_only=True,
        ),
    )


def validate_frozen_query_registry(
    source: Path | str | dict[str, Any],
    evidence_store: ObserverEvidenceStore,
) -> FrozenRegistryAudit:
    """Audit frozen evidence without issuing any runtime authority."""
    if not isinstance(evidence_store, ObserverEvidenceStore):
        raise TypeError("frozen promotion requires ObserverEvidenceStore")
    try:
        loaded = load_query_registry(source)
        if loaded.registry.state is not FixtureState.FROZEN:
            raise ValueError("QUERY_FIXTURE_NOT_FROZEN")
        verified = _verify_promotion_artifacts(loaded, evidence_store)
    except (
        FileNotFoundError,
        OSError,
        OverflowError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        return FrozenRegistryAudit(
            run_id=evidence_store.run_id,
            valid=False,
            reason=str(error),
        )
    return FrozenRegistryAudit(
        run_id=evidence_store.run_id,
        valid=True,
        verified_hashes=tuple(sorted(verified.items())),
    )


def discover_and_freeze_registry(
    source: Path | str | dict[str, Any],
    *,
    evidence_store: ObserverEvidenceStore,
    client: OwnedHttpClient,
    windows: tuple[PhaseWindow, PhaseWindow, PhaseWindow],
    base_urls: dict[str, str],
) -> FrozenTelemetryQueryCapability:
    """Acquire the frozen contract through owned transport and issue its sole seal."""
    if type(client) is not OwnedHttpClient:
        raise TypeError("production discovery requires exact OwnedHttpClient")
    if not _owned_http_client_has_production_integrity(client):
        if client._transport_token is _PRODUCTION_TRANSPORT_TOKEN:
            raise TypeError("production transport method integrity is invalid")
        raise TypeError("production discovery rejects injected test transport")
    if not isinstance(evidence_store, ObserverEvidenceStore):
        raise TypeError("production discovery requires ObserverEvidenceStore")
    _validate_promotion_windows(
        windows,
        run_id=client.run_id,
        store_run_id=evidence_store.run_id,
    )
    if set(base_urls) != {"prometheus", "jaeger", "opensearch", "probe"}:
        raise ValueError("production discovery windows or endpoints are unbound")
    payload = _prepare_candidate_registry_for_live_discovery(
        source,
        run_id=client.run_id,
    )
    baseline = windows[0]
    prometheus = payload["prometheus"]
    jaeger = payload["jaeger"]
    opensearch = payload["opensearch"]
    probe = payload["probe"]
    fixture_version = payload["fixture_version"]
    exchanges: list[HttpExchange] = []
    raw_records: list[tuple[str, str, str, HttpExchange]] = []
    sequence = 0

    def acquire(request: HttpRequest, *, purpose: str) -> HttpExchange:
        nonlocal sequence
        sequence += 1
        exchange = client.request(request)
        stored_path, digest = _persist_promotion_exchange_before_parse(
            evidence_store,
            exchange=exchange,
            sequence=sequence,
            purpose=purpose,
            fixture_version=fixture_version,
        )
        logical_path = _promotion_logical_path(evidence_store, stored_path)
        exchanges.append(exchange)
        raw_records.append((purpose, logical_path, digest, exchange))
        if not exchange.succeeded:
            _persist_promotion_terminal(
                evidence_store,
                sequence=sequence,
                reason=exchange.reason.value,
                raw_artifact=logical_path,
                raw_sha256=digest,
            )
            raise ValueError(
                f"production promotion transport failed: {exchange.reason.value}"
            )
        return exchange

    observations: list[dict[str, Any]] = []
    prometheus_exchanges: dict[str, HttpExchange] = {}
    for query_kind, query in (
        ("total", prometheus["total_query"]),
        ("error", prometheus["error_query"]),
        ("target_incarnation", prometheus["target_incarnation_query"]),
    ):
        exchange = acquire(
            HttpRequest(
                endpoint=OwnedEndpoint(
                    base_url=base_urls["prometheus"],
                    service=prometheus["target"]["service"],
                    target_port=prometheus["target"]["target_port"],
                    protocol=prometheus["target"]["protocol"],
                ),
                method="GET",
                target=f"/api/v1/query?query={quote(query, safe='')}",
                absolute_deadline_monotonic=baseline.monotonic_ended_at,
            ),
            purpose=f"prometheus-{query_kind.replace('_', '-')}",
        )
        prometheus_exchanges[query_kind] = exchange
        observations.append(
            _promotion_observation_from_exchange(
                backend="prometheus",
                query_kind=query_kind,
                request=query,
                response_schema=prometheus["expected_response_schema"],
                exchange=exchange,
            )
        )

    jaeger_target = "/api/traces?" + urlencode(
        {
            "service": jaeger["service_identity"],
            "operation": jaeger["operation"],
            "start": int(baseline.utc_started_at.timestamp() * 1_000_000),
            "end": int(baseline.utc_ended_at.timestamp() * 1_000_000),
            "limit": 100,
        }
    )
    jaeger_exchange = acquire(
        HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["jaeger"],
                service=jaeger["target"]["service"],
                target_port=jaeger["target"]["target_port"],
                protocol=jaeger["target"]["protocol"],
            ),
            method="GET",
            target=jaeger_target,
            absolute_deadline_monotonic=baseline.monotonic_ended_at,
        ),
        purpose="jaeger-readiness",
    )
    observations.append(
        _promotion_observation_from_exchange(
            backend="jaeger",
            query_kind="readiness",
            request=jaeger["request_template"],
            response_schema=jaeger["expected_response_schema"],
            exchange=jaeger_exchange,
        )
    )

    opensearch_body = canonical_json_bytes(
        {
            "size": 100,
            "sort": [{opensearch["timestamp_field"]: {"order": "asc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {
                            "term": {
                                opensearch["service_identity_field"]: opensearch[
                                    "service_identity"
                                ]
                            }
                        },
                        {
                            "range": {
                                opensearch["timestamp_field"]: {
                                    "gte": baseline.utc_started_at.isoformat(),
                                    "lte": baseline.utc_ended_at.isoformat(),
                                    "format": "strict_date_optional_time",
                                }
                            }
                        },
                    ]
                }
            },
        }
    )
    opensearch_exchange = acquire(
        HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["opensearch"],
                service=opensearch["target"]["service"],
                target_port=opensearch["target"]["target_port"],
                protocol=opensearch["target"]["protocol"],
            ),
            method="POST",
            target=f"/{quote(opensearch['index'], safe='')}/_search",
            headers=(
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(opensearch_body))),
            ),
            body=opensearch_body,
            absolute_deadline_monotonic=baseline.monotonic_ended_at,
        ),
        purpose="opensearch-readiness",
    )
    observations.append(
        _promotion_observation_from_exchange(
            backend="opensearch",
            query_kind="readiness",
            request=opensearch["request_template"],
            response_schema=opensearch["expected_response_schema"],
            exchange=opensearch_exchange,
        )
    )

    phase_observations: list[dict[str, Any]] = []
    probe_exchanges: list[HttpExchange] = []
    generated_trace_ids: set[str] = set()
    for window in windows:
        trace_id = secrets.token_hex(16)
        if trace_id in generated_trace_ids:
            raise RuntimeError("production probe trace_id collision")
        generated_trace_ids.add(trace_id)
        parent_span_id = secrets.token_hex(8)
        traceparent = f"00-{trace_id}-{parent_span_id}-01"
        phase_name = window.scenario_phase.value
        exchange = acquire(
            HttpRequest(
                endpoint=OwnedEndpoint(
                    base_url=base_urls["probe"],
                    service=probe["target"]["service"],
                    target_port=probe["target"]["target_port"],
                    protocol=probe["target"]["protocol"],
                ),
                method=probe["method"],
                target=probe["path"],
                headers=(("traceparent", traceparent),),
                absolute_deadline_monotonic=window.monotonic_ended_at,
            ),
            purpose=f"probe-{phase_name}",
        )
        probe_exchanges.append(exchange)
        probe_record = raw_records[-1]
        envelope = exchange.observer_input_envelope
        boundary_passed = (
            envelope is not None
            and envelope.is_authentic(exchange.request)
            and exchange.request.method == probe["method"]
            and exchange.request.target == probe["path"]
            and exchange.request.body == b""
            and exchange.request.headers == (("traceparent", traceparent),)
        )
        correlation_exchange = acquire(
            HttpRequest(
                endpoint=OwnedEndpoint(
                    base_url=base_urls["jaeger"],
                    service=jaeger["target"]["service"],
                    target_port=jaeger["target"]["target_port"],
                    protocol=jaeger["target"]["protocol"],
                ),
                method="GET",
                target=f"/api/traces/{trace_id}",
                absolute_deadline_monotonic=window.monotonic_ended_at,
            ),
            purpose=f"jaeger-correlation-{phase_name}",
        )
        correlation_record = raw_records[-1]
        try:
            correlation_payload = json.loads(correlation_exchange.raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            _persist_promotion_terminal(
                evidence_store,
                sequence=sequence,
                reason="JAEGER_CORRELATION_SCHEMA_INVALID",
                raw_artifact=correlation_record[1],
                raw_sha256=correlation_record[2],
            )
            raise ValueError("Jaeger correlation response is invalid JSON") from error
        correlation_proven = _jaeger_trace_proves_getads(
            correlation_payload,
            trace_id=trace_id,
            operation=jaeger["operation"],
            window=window,
        )
        if not correlation_proven:
            _persist_promotion_terminal(
                evidence_store,
                sequence=sequence,
                reason="PROBE_GETADS_CORRELATION_MISSING",
                raw_artifact=correlation_record[1],
                raw_sha256=correlation_record[2],
            )
            raise ValueError("probe trace lacks phase-local Ad GetAds span")
        phase_observations.append(
            {
                "phase": phase_name,
                "cycle_number": window.cycle_number,
                "phase_started_at": window.utc_started_at.isoformat(),
                "phase_ended_at": window.utc_ended_at.isoformat(),
                "phase_monotonic_started_at": window.monotonic_started_at,
                "phase_monotonic_ended_at": window.monotonic_ended_at,
                "fixed_input": probe["input"],
                "observer_input_boundary_passed": boundary_passed,
                "unexpected_input_count": 0 if boundary_passed else 1,
                "trace_id": trace_id,
                "traceparent_sha256": sha256_bytes(traceparent.encode()),
                "probe_raw_artifact": probe_record[1],
                "probe_raw_sha256": probe_record[2],
                "jaeger_request": f"/api/traces/{trace_id}",
                "jaeger_raw_artifact": correlation_record[1],
                "jaeger_raw_sha256": correlation_record[2],
                "jaeger_http_status": correlation_exchange.status_code,
                "jaeger_request_started_at": (
                    correlation_exchange.started_at.isoformat()
                ),
                "jaeger_response_ended_at": (correlation_exchange.ended_at.isoformat()),
                "jaeger_monotonic_started_at": (
                    correlation_exchange.monotonic_started_at
                ),
                "jaeger_monotonic_ended_at": correlation_exchange.monotonic_ended_at,
                "jaeger_raw_response_base64": base64.b64encode(
                    correlation_exchange.raw_body
                ).decode("ascii"),
                "jaeger_raw_response_sha256": correlation_exchange.raw_sha256,
                "getads_span_proven": correlation_proven,
                **_promotion_raw_exchange_fields(exchange),
            }
        )
    observations.append(
        _promotion_observation_from_exchange(
            backend="probe",
            query_kind="business_path",
            request=f"{probe['method']} {probe['path']}",
            response_schema="otel-demo.frontend.api.data.Ad[].v3.0.0",
            exchange=probe_exchanges[0],
        )
    )

    prefix = f"observer-visible/{client.run_id}/"
    try:
        total_labels = _discover_prometheus_vector_labels(
            prometheus_exchanges["total"].raw_body,
            window=baseline,
            allow_empty=False,
        )
        _discover_prometheus_vector_labels(
            prometheus_exchanges["error"].raw_body,
            window=baseline,
            allow_empty=True,
        )
        incarnation_labels = _discover_prometheus_vector_labels(
            prometheus_exchanges["target_incarnation"].raw_body,
            window=baseline,
            allow_empty=False,
        )
    except ValueError:
        last = raw_records[-1]
        _persist_promotion_terminal(
            evidence_store,
            sequence=sequence,
            reason="PROMETHEUS_DISCOVERY_SCHEMA_INVALID",
            raw_artifact=last[1],
            raw_sha256=last[2],
        )
        raise
    if (
        len(incarnation_labels) != 1
        or incarnation_labels[0].get("__name__") != "process_start_time_seconds"
        or incarnation_labels[0].get("job") != "ad"
        or any(
            labels.get("__name__") != prometheus["candidate_metric"]
            or labels.get("service_name") != "ad"
            or labels.get("span_name") != _PINNED_GETADS_OPERATION
            or "status_code" not in labels
            for labels in total_labels
        )
    ):
        last = raw_records[-1]
        _persist_promotion_terminal(
            evidence_store,
            sequence=sequence,
            reason="PROMETHEUS_DISCOVERY_IDENTITY_INVALID",
            raw_artifact=last[1],
            raw_sha256=last[2],
        )
        raise ValueError(
            "Prometheus discovered identities differ from pinned candidates"
        )

    payload["state"] = FixtureState.FROZEN.value
    for backend in ("prometheus", "jaeger", "opensearch", "probe"):
        payload[backend]["state"] = FixtureState.FROZEN.value
    prometheus.update(
        {
            "expected_total_series": [
                {"labels": labels}
                for labels in sorted(total_labels, key=canonical_json_sha256)
            ],
            "expected_target_incarnation_series": {"labels": incarnation_labels[0]},
            "error_classification": {
                "label": "status_code",
                "values": ["STATUS_CODE_ERROR"],
            },
            "failure_semantics": [
                "Fail on missing, malformed, stale, reset, restart, or drifting series."
            ],
            "freshness_semantics": (
                "Every acquired sample is current-run and phase-local."
            ),
        }
    )
    jaeger["failure_semantics"] = [
        "Fail unless an exact Ad GetAds span is current and trace-correlated."
    ]
    jaeger["freshness_semantics"] = (
        "Selected span start and end are within the current phase."
    )
    opensearch["failure_semantics"] = [
        "Fail unless an exact Ad log is fresh in the current phase."
    ]
    opensearch["freshness_semantics"] = (
        "Selected log timestamp is within the current phase."
    )
    raw_path = prefix + "telemetry/promotion/raw.json"
    identity_path = prefix + "telemetry/promotion/identities.json"
    counter_path = prefix + "telemetry/promotion/counter-map.json"
    attribution_path = prefix + "telemetry/promotion/probe-attribution.json"
    review_path = prefix + "telemetry/promotion/review.json"
    raw_paths = [record[1] for record in raw_records]
    facts: dict[str, str] = {}
    for component in (
        payload,
        prometheus,
        jaeger,
        opensearch,
        probe,
    ):
        for fact in component["source_facts"]:
            previous = facts.setdefault(fact["path"], fact["sha256"])
            if previous != fact["sha256"]:
                raise ValueError("discovery source fact hashes conflict")
    upstream_sha256 = canonical_json_sha256(
        {
            "upstream_tag": payload["upstream_tag"],
            "upstream_commit": payload["upstream_commit"],
            "source_facts": [
                {"path": path, "sha256": digest}
                for path, digest in sorted(facts.items())
            ],
        }
    )
    all_paths = [
        raw_path,
        *raw_paths,
        identity_path,
        counter_path,
        attribution_path,
        review_path,
    ]
    proof = {
        "current_run_id": client.run_id,
        "raw_artifacts": [raw_path, *raw_paths],
        "artifact_sha256": {path: "0" * 64 for path in all_paths},
        "emitted_identity_artifacts": [identity_path],
        "counter_mapping_artifact": counter_path,
        "probe_getads_attribution_artifact": attribution_path,
        "upstream_sha256": upstream_sha256,
        "compose_config_sha256": payload["compose_config_sha256"],
        "fixture_content_sha256": "0" * 64,
        "review_decision": "APPROVED",
        "review_artifact": review_path,
    }
    payload["promotion_proof"] = proof
    payload["probe"]["getads_proof_artifact"] = attribution_path
    contract = dict(payload)
    contract.pop("promotion_proof")
    proof["fixture_content_sha256"] = canonical_json_sha256(contract)
    registry = TelemetryQueryRegistry.model_validate(payload)
    common = {"run_id": client.run_id, "fixture_version": registry.fixture_version}
    phase_correlations = {
        phase["phase"]: {
            key: phase[key]
            for key in (
                "trace_id",
                "traceparent_sha256",
                "probe_raw_artifact",
                "probe_raw_sha256",
                "jaeger_raw_artifact",
                "jaeger_raw_sha256",
            )
        }
        for phase in phase_observations
    }
    artifact_payloads = {
        raw_path: {
            **common,
            "schema_version": "phase0.telemetry-promotion-raw.v1",
            "upstream_tag": registry.upstream_tag,
            "upstream_commit": registry.upstream_commit,
            "upstream_sha256": proof["upstream_sha256"],
            "compose_config_sha256": registry.compose_config_sha256,
            "promotion_started_at": min(
                window.utc_started_at for window in windows
            ).isoformat(),
            "promotion_ended_at": max(
                window.utc_ended_at for window in windows
            ).isoformat(),
            "promotion_monotonic_started_at": min(
                window.monotonic_started_at for window in windows
            ),
            "promotion_monotonic_ended_at": max(
                window.monotonic_ended_at for window in windows
            ),
            "backend_window_started_at": baseline.utc_started_at.isoformat(),
            "backend_window_ended_at": baseline.utc_ended_at.isoformat(),
            "backend_monotonic_started_at": baseline.monotonic_started_at,
            "backend_monotonic_ended_at": baseline.monotonic_ended_at,
            "backend_observations": observations,
            "probe_phase_observations": phase_observations,
        },
        identity_path: {
            **common,
            "schema_version": "phase0.telemetry-emitted-identities.v1",
            "bindings": _expected_identity_bindings(registry),
        },
        counter_path: {
            **common,
            "schema_version": "phase0.prometheus-counter-contract.v1",
            "contract": _expected_prometheus_contract(registry),
        },
        attribution_path: {
            **common,
            "schema_version": "phase0.probe-getads-attribution.v1",
            "probe_path": registry.probe.path,
            "operation": registry.prometheus.operation,
            "attribution_proven": all(
                phase["observer_input_boundary_passed"] and phase["getads_span_proven"]
                for phase in phase_observations
            ),
            "fixed_input": registry.probe.input,
            "response_contract": registry.probe.response_contract,
            "observer_input_boundary_required": (
                registry.probe.hidden_input_denial_required
            ),
            "required_phases": list(registry.probe.required_phases),
            "phase_correlations": phase_correlations,
        },
    }
    hashes: dict[str, str] = {
        path: digest for _purpose, path, digest, _exchange in raw_records
    }
    for absolute_path, artifact_payload in artifact_payloads.items():
        artifact = evidence_store.write_immutable(
            absolute_path.removeprefix(prefix),
            artifact_payload,
        )
        hashes[absolute_path] = artifact.sha256
    review = evidence_store.write_immutable(
        review_path.removeprefix(prefix),
        {
            **common,
            "schema_version": "phase0.telemetry-promotion-review.v1",
            "decision": "APPROVED",
            "fixture_content_sha256": proof["fixture_content_sha256"],
            "upstream_sha256": proof["upstream_sha256"],
            "compose_config_sha256": proof["compose_config_sha256"],
            "reviewed_artifact_sha256": dict(hashes),
        },
    )
    hashes[review_path] = review.sha256
    proof["artifact_sha256"] = hashes
    audit = validate_frozen_query_registry(payload, evidence_store)
    if not audit.valid:
        last = raw_records[-1]
        _persist_promotion_terminal(
            evidence_store,
            sequence=sequence,
            reason="PROMOTION_AUDIT_INVALID",
            raw_artifact=last[1],
            raw_sha256=last[2],
        )
        raise ValueError(audit.reason or "production promotion audit failed")
    loaded = load_query_registry(payload)
    receipt = _ProductionAcquisitionReceipt(
        _token=_PRODUCTION_ACQUISITION_TOKEN,
        run_id=client.run_id,
        store_root=evidence_store.root,
        windows=windows,
        exchanges=tuple(exchanges),
    )
    return FrozenTelemetryQueryCapability(
        _token=_FROZEN_CAPABILITY_TOKEN,
        loaded=loaded,
        store=evidence_store,
        verified_hashes=dict(audit.verified_hashes),
        acquisition_receipt=receipt,
    )


def _prepare_candidate_registry_for_live_discovery(
    source: Path | str | dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Resolve pinned static candidates without granting frozen authority."""
    loaded = load_query_registry(source)
    registry = loaded.registry
    if (
        registry.state is FixtureState.FROZEN
        or registry.promotion_proof is not None
        or registry.upstream_tag != "3.0.0"
        or registry.upstream_commit != _UPSTREAM_COMMIT
    ):
        raise ValueError("live discovery requires an unresolved production registry")
    payload = registry.model_dump(mode="json")
    metric = registry.prometheus.candidate_metric
    service = registry.prometheus.applicable_service
    index = registry.opensearch.index
    service_field = registry.opensearch.service_identity_field
    if (
        metric != "traces_span_metrics_calls_total"
        or service != "ad"
        or registry.jaeger.service_identity != "ad"
        or registry.opensearch.service_identity != "ad"
        or index != "otel-logs-*"
        or service_field != "resource.service.name"
    ):
        raise ValueError("pinned static discovery candidates differ")
    total_query = (
        f'{metric}{{service_name="{service}",span_name="{_PINNED_GETADS_OPERATION}"}}'
    )
    payload["fixture_version"] = f"otel-demo-3.0.0-live-{run_id[:8]}-v1"
    payload["prometheus"].update(
        {
            "total_query": total_query,
            "error_query": total_query[:-1] + ',status_code="STATUS_CODE_ERROR"}',
            "target_incarnation_query": 'process_start_time_seconds{job="ad"}',
            "operation": _PINNED_GETADS_OPERATION,
            "counter_identity_labels": [
                "service_name",
                "span_name",
                "status_code",
            ],
            "scrape_interval_seconds": 10.0,
            "scrape_interval_tolerance_seconds": 0.5,
            "maximum_scrape_lag_seconds": 2.0,
            "boundary_rule": "(start_sample_timestamp,end_sample_timestamp]",
            "cardinality_rule": "exact_frozen_series_set",
            "reset_policy": "reject_any_counter_decrease_or_target_restart",
            "staleness_policy": "reject_stale_marker_or_lag",
            "zero_series_rule": "absent_error_series_means_zero",
        }
    )
    payload["jaeger"]["operation"] = _PINNED_GETADS_OPERATION
    payload["opensearch"].update(
        {
            "timestamp_field": "@timestamp",
            "trace_id_field": "trace.id",
            "span_id_field": "span.id",
        }
    )
    payload["probe"].update(
        {
            "method": "GET",
            "path": _PINNED_PROBE_PATH,
            "input": "fixed storefront data request",
            "response_contract": "HTTP 200 JSON containing a direct nonempty Ad array",
            "exit_semantics": "HTTP and schema failures are typed and fail closed.",
            "attribution_mechanism": (
                "Unique W3C trace correlation proves a phase-local Ad GetAds span."
            ),
            "getads_proof_artifact": (
                f"observer-visible/{run_id}/telemetry/promotion/probe-attribution.json"
            ),
        }
    )
    return payload


def _validate_promotion_windows(
    windows: tuple[PhaseWindow, PhaseWindow, PhaseWindow],
    *,
    run_id: str,
    store_run_id: str,
) -> None:
    expected_phases = (
        MeasurementPhase.BASELINE,
        MeasurementPhase.FAULT,
        MeasurementPhase.RECOVERY,
    )
    if (
        not isinstance(windows, tuple)
        or len(windows) != 3
        or tuple(window.scenario_phase for window in windows) != expected_phases
        or {window.run_id for window in windows} != {run_id}
        or store_run_id != run_id
        or len({window.cycle_number for window in windows}) != 1
        or not all(
            earlier.utc_ended_at < later.utc_started_at
            and earlier.monotonic_ended_at < later.monotonic_started_at
            for earlier, later in zip(windows, windows[1:])
        )
    ):
        raise ValueError(
            "promotion windows must be same-run, same-cycle, and strictly ordered"
        )


def _promotion_observation_from_exchange(
    *,
    backend: str,
    query_kind: str,
    request: str | None,
    response_schema: str | None,
    exchange: HttpExchange,
) -> dict[str, Any]:
    if request is None or response_schema is None or not exchange.succeeded:
        raise ValueError("production promotion acquisition failed")
    return {
        "backend": backend,
        "query_kind": query_kind,
        "request": request,
        "response_schema": response_schema,
        **_promotion_raw_exchange_fields(exchange),
    }


def _persist_promotion_exchange_before_parse(
    store: ObserverEvidenceStore,
    *,
    exchange: HttpExchange,
    sequence: int,
    purpose: str,
    fixture_version: str | None = None,
) -> tuple[str, str]:
    """Durably capture every transport result before schema interpretation."""
    if sequence < 1 or not purpose or not purpose.replace("-", "").isalnum():
        raise ValueError("promotion exchange evidence identity is invalid")
    artifact = store.write_immutable(
        f"telemetry/promotion/raw-exchanges/{sequence:02d}-{purpose}.json",
        {
            "schema_version": "phase0.telemetry-promotion-exchange.v1",
            "run_id": store.run_id,
            "fixture_version": fixture_version,
            "sequence": sequence,
            "purpose": purpose,
            "request": {
                "method": exchange.request.method,
                "target": exchange.request.target,
                "headers": [list(item) for item in exchange.request.headers],
                "body_sha256": sha256_bytes(exchange.request.body),
            },
            "request_started_at": exchange.started_at.isoformat(),
            "response_ended_at": exchange.ended_at.isoformat(),
            "monotonic_started_at": exchange.monotonic_started_at,
            "monotonic_ended_at": exchange.monotonic_ended_at,
            "http_status": exchange.status_code,
            "transport_reason": exchange.reason.value,
            "terminal_failure": not exchange.succeeded,
            "response_headers": [list(item) for item in exchange.response_headers],
            "raw_response_base64": base64.b64encode(exchange.raw_body).decode("ascii"),
            "raw_response_sha256": exchange.raw_sha256,
            "raw_response_partial": exchange.raw_body_partial,
        },
    )
    return str(artifact.path), artifact.sha256


def _persist_promotion_terminal(
    store: ObserverEvidenceStore,
    *,
    sequence: int,
    reason: str,
    raw_artifact: str,
    raw_sha256: str,
) -> None:
    store.write_immutable(
        f"telemetry/promotion/terminal-{sequence:02d}.json",
        {
            "schema_version": "phase0.telemetry-promotion-terminal.v1",
            "run_id": store.run_id,
            "outcome": "FAILED",
            "reason": reason,
            "raw_artifact": raw_artifact,
            "raw_sha256": raw_sha256,
        },
    )


def _promotion_logical_path(
    store: ObserverEvidenceStore,
    path: str,
) -> str:
    relative = (
        Path(path).resolve(strict=True).relative_to(store.root.resolve(strict=True))
    )
    return f"observer-visible/{store.run_id}/{relative.as_posix()}"


def _discover_prometheus_vector_labels(
    body: bytes,
    *,
    window: PhaseWindow,
    allow_empty: bool,
) -> list[dict[str, str]]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Prometheus discovery response is invalid JSON") from error
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "success"
        or not isinstance(payload.get("data"), dict)
        or payload["data"].get("resultType") != "vector"
        or not isinstance(payload["data"].get("result"), list)
    ):
        raise ValueError("Prometheus discovery response schema differs")
    labels: list[dict[str, str]] = []
    for result in payload["data"]["result"]:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("metric"), dict)
            or not all(
                isinstance(key, str)
                and bool(key)
                and isinstance(value, str)
                and bool(value)
                for key, value in result["metric"].items()
            )
            or not isinstance(result.get("value"), list)
            or len(result["value"]) != 2
        ):
            raise ValueError("Prometheus discovery vector differs")
        try:
            timestamp = datetime.fromtimestamp(float(result["value"][0]), tz=UTC)
            value = Decimal(str(result["value"][1]))
        except (
            InvalidOperation,
            OSError,
            OverflowError,
            TypeError,
            ValueError,
        ) as error:
            raise ValueError("Prometheus discovery sample differs") from error
        if (
            not value.is_finite()
            or value < 0
            or not window.contains_observation(timestamp)
        ):
            raise ValueError("Prometheus discovery sample is stale or invalid")
        labels.append(dict(result["metric"]))
    if (not labels and not allow_empty) or len(
        {canonical_json_sha256(item) for item in labels}
    ) != len(labels):
        raise ValueError("Prometheus discovery identities are empty or duplicated")
    return labels


def _promotion_raw_exchange_fields(exchange: HttpExchange) -> dict[str, Any]:
    return {
        "http_status": exchange.status_code,
        "request_started_at": exchange.started_at.isoformat(),
        "response_ended_at": exchange.ended_at.isoformat(),
        "monotonic_started_at": exchange.monotonic_started_at,
        "monotonic_ended_at": exchange.monotonic_ended_at,
        "raw_response_base64": base64.b64encode(exchange.raw_body).decode("ascii"),
        "raw_response_sha256": exchange.raw_sha256,
    }


RegistryAccess = (
    LoadedTelemetryQueryRegistry
    | TestTelemetryQueryCapability
    | FrozenTelemetryQueryCapability
)


def registry_access_is_frozen(
    access: RegistryAccess,
    *,
    run_id: str,
    evidence_store: object | None = None,
) -> bool:
    return (
        isinstance(access, FrozenTelemetryQueryCapability)
        and access.run_id == run_id
        and access.store is evidence_store
        and access.is_authentic()
    )


def _registry_access_is_frozen_for_adapter(
    access: RegistryAccess,
    *,
    run_id: str,
    evidence_store: object,
    client: object,
) -> bool:
    if registry_access_is_frozen(
        access,
        run_id=run_id,
        evidence_store=evidence_store,
    ):
        return (
            type(client) is OwnedHttpClient
            and _owned_http_client_has_production_integrity(client)
            and isinstance(evidence_store, ObserverEvidenceStore)
        )
    return (
        isinstance(access, TestTelemetryQueryCapability)
        and access.registry.state is FixtureState.FROZEN
        and not isinstance(evidence_store, ObserverEvidenceStore)
        and not isinstance(client, OwnedHttpClient)
        and getattr(evidence_store, "_synthetic_telemetry_test_double", False) is True
        and getattr(client, "_synthetic_telemetry_test_double", False) is True
    )


def _verify_promotion_artifacts(
    loaded: LoadedTelemetryQueryRegistry,
    store: ObserverEvidenceStore,
) -> dict[str, str]:
    registry = loaded.registry
    proof = registry.promotion_proof
    if (
        registry.state is not FixtureState.FROZEN
        or proof is None
        or store.run_id != proof.current_run_id
        or registry.probe.getads_proof_artifact
        != proof.probe_getads_attribution_artifact
    ):
        raise ValueError("frozen promotion run or attribution is unbound")
    _verify_frozen_source_facts(registry, proof)
    paths = tuple(proof.artifact_sha256)
    prefix = f"observer-visible/{store.run_id}/"
    payloads: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    root = store.root.resolve(strict=True)
    for path in paths:
        if not path.startswith(prefix):
            raise ValueError("promotion artifact is outside current observer run")
        relative = path.removeprefix(prefix)
        candidate = root / relative
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents:
            raise ValueError("promotion artifact escaped observer capability")
        mode = os.lstat(candidate).st_mode
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise ValueError("promotion artifact is not an immutable regular file")
        digest = sha256_file(candidate)
        if digest != proof.artifact_sha256[path]:
            raise ValueError("promotion artifact content hash differs")
        hashes[path] = digest
        try:
            payloads[path] = json.loads(candidate.read_bytes())
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("promotion artifact is not valid JSON") from error
    _verify_promotion_semantics(registry, proof, payloads)
    return hashes


def _verify_promotion_semantics(
    registry: TelemetryQueryRegistry,
    proof: PromotionProof,
    payloads: dict[str, Any],
) -> None:
    def require_common(path: str) -> dict[str, Any]:
        payload = payloads[path]
        if (
            not isinstance(payload, dict)
            or payload.get("run_id") != proof.current_run_id
            or payload.get("fixture_version") != registry.fixture_version
        ):
            raise ValueError("promotion artifact identity differs")
        return payload

    expected_exchange_paths = _expected_promotion_exchange_paths(proof.current_run_id)
    if (
        proof.raw_artifacts
        != (
            f"observer-visible/{proof.current_run_id}/telemetry/promotion/raw.json",
            *expected_exchange_paths.values(),
        )
        or proof.emitted_identity_artifacts
        != (
            f"observer-visible/{proof.current_run_id}/"
            "telemetry/promotion/identities.json",
        )
        or proof.counter_mapping_artifact
        != (
            f"observer-visible/{proof.current_run_id}/"
            "telemetry/promotion/counter-map.json"
        )
        or proof.probe_getads_attribution_artifact
        != (
            f"observer-visible/{proof.current_run_id}/"
            "telemetry/promotion/probe-attribution.json"
        )
        or proof.review_artifact
        != (f"observer-visible/{proof.current_run_id}/telemetry/promotion/review.json")
    ):
        raise ValueError("promotion proof artifact set or prefix differs")
    raw = require_common(proof.raw_artifacts[0])
    exchange_payloads = {
        purpose: require_common(path)
        for purpose, path in expected_exchange_paths.items()
    }
    for sequence, purpose in enumerate(expected_exchange_paths, start=1):
        _verify_promotion_exchange_artifact(
            exchange_payloads[purpose],
            sequence=sequence,
            purpose=purpose,
        )
    _verify_raw_promotion_artifact(
        registry,
        proof,
        raw,
        exchange_payloads=exchange_payloads,
    )
    identity = require_common(proof.emitted_identity_artifacts[0])
    if (
        set(identity) != {"run_id", "fixture_version", "schema_version", "bindings"}
        or identity.get("schema_version") != "phase0.telemetry-emitted-identities.v1"
        or identity.get("bindings") != _expected_identity_bindings(registry)
    ):
        raise ValueError("emitted identity proof differs from frozen registry")
    counter = require_common(proof.counter_mapping_artifact)
    if (
        set(counter) != {"run_id", "fixture_version", "schema_version", "contract"}
        or counter.get("schema_version") != "phase0.prometheus-counter-contract.v1"
        or counter.get("contract") != _expected_prometheus_contract(registry)
    ):
        raise ValueError("counter mapping proof differs from frozen registry")
    attribution = require_common(proof.probe_getads_attribution_artifact)
    if (
        set(attribution)
        != {
            "run_id",
            "fixture_version",
            "schema_version",
            "probe_path",
            "operation",
            "attribution_proven",
            "fixed_input",
            "response_contract",
            "observer_input_boundary_required",
            "required_phases",
            "phase_correlations",
        }
        or attribution.get("schema_version") != "phase0.probe-getads-attribution.v1"
        or attribution.get("probe_path") != registry.probe.path
        or attribution.get("operation") != registry.prometheus.operation
        or attribution.get("attribution_proven") is not True
        or attribution.get("fixed_input") != registry.probe.input
        or attribution.get("response_contract") != registry.probe.response_contract
        or attribution.get("observer_input_boundary_required")
        is not registry.probe.hidden_input_denial_required
        or attribution.get("required_phases") != list(registry.probe.required_phases)
        or attribution.get("phase_correlations")
        != {
            phase["phase"]: {
                key: phase[key]
                for key in (
                    "trace_id",
                    "traceparent_sha256",
                    "probe_raw_artifact",
                    "probe_raw_sha256",
                    "jaeger_raw_artifact",
                    "jaeger_raw_sha256",
                )
            }
            for phase in raw["probe_phase_observations"]
        }
    ):
        raise ValueError("probe GetAds attribution proof differs")
    review = require_common(proof.review_artifact)
    if (
        set(review)
        != {
            "run_id",
            "fixture_version",
            "schema_version",
            "decision",
            "fixture_content_sha256",
            "upstream_sha256",
            "compose_config_sha256",
            "reviewed_artifact_sha256",
        }
        or review.get("schema_version") != "phase0.telemetry-promotion-review.v1"
        or review.get("decision") != "APPROVED"
        or review.get("fixture_content_sha256") != proof.fixture_content_sha256
        or review.get("upstream_sha256") != proof.upstream_sha256
        or review.get("compose_config_sha256") != proof.compose_config_sha256
        or review.get("reviewed_artifact_sha256")
        != {
            path: digest
            for path, digest in proof.artifact_sha256.items()
            if path != proof.review_artifact
        }
    ):
        raise ValueError("promotion review proof differs")


def _verify_frozen_source_facts(
    registry: TelemetryQueryRegistry,
    proof: PromotionProof,
) -> None:
    facts = (
        *registry.source_facts,
        *registry.prometheus.source_facts,
        *registry.jaeger.source_facts,
        *registry.opensearch.source_facts,
        *registry.probe.source_facts,
    )
    repository_root = Path(__file__).resolve().parents[3]
    unique: dict[str, str] = {}
    for fact in facts:
        previous = unique.setdefault(fact.path, fact.sha256)
        if previous != fact.sha256:
            raise ValueError("frozen source fact hashes conflict")
        source = repository_root / fact.path
        if not source.is_file() or sha256_file(source) != fact.sha256:
            raise ValueError("frozen source fact differs from pinned upstream")
    expected_upstream_sha256 = canonical_json_sha256(
        {
            "upstream_tag": registry.upstream_tag,
            "upstream_commit": registry.upstream_commit,
            "source_facts": [
                {"path": path, "sha256": digest}
                for path, digest in sorted(unique.items())
            ],
        }
    )
    if proof.upstream_sha256 != expected_upstream_sha256:
        raise ValueError("promotion upstream source binding differs")
    compose = repository_root / "third_party/opentelemetry-demo/compose.yaml"
    if sha256_file(compose) != registry.compose_config_sha256:
        raise ValueError("promotion Compose source differs")


def _expected_identity_bindings(
    registry: TelemetryQueryRegistry,
) -> dict[str, Any]:
    prometheus = registry.prometheus
    jaeger = registry.jaeger
    opensearch = registry.opensearch
    probe = registry.probe
    return {
        "prometheus": {
            "applicable_service": prometheus.applicable_service,
            "metric": prometheus.candidate_metric,
            "operation": prometheus.operation,
            "total_series": [
                series.model_dump(mode="json")
                for series in prometheus.expected_total_series or ()
            ],
            "error_classification": (
                prometheus.error_classification.model_dump(mode="json")
                if prometheus.error_classification is not None
                else None
            ),
            "target_incarnation_series": (
                prometheus.expected_target_incarnation_series.model_dump(mode="json")
                if prometheus.expected_target_incarnation_series is not None
                else None
            ),
        },
        "jaeger": {
            "service_identity": jaeger.service_identity,
            "operation": jaeger.operation,
        },
        "opensearch": {
            "index": opensearch.index,
            "service_identity_field": opensearch.service_identity_field,
            "service_identity": opensearch.service_identity,
            "timestamp_field": opensearch.timestamp_field,
            "trace_id_field": opensearch.trace_id_field,
            "span_id_field": opensearch.span_id_field,
        },
        "probe": {
            "target": probe.target.model_dump(mode="json"),
            "method": probe.method,
            "path": probe.path,
            "input": probe.input,
            "response_contract": probe.response_contract,
        },
    }


def _expected_prometheus_contract(
    registry: TelemetryQueryRegistry,
) -> dict[str, Any]:
    fixture = registry.prometheus
    return {
        "request_template": fixture.request_template,
        "expected_response_schema": fixture.expected_response_schema,
        "total_query": fixture.total_query,
        "error_query": fixture.error_query,
        "target_incarnation_query": fixture.target_incarnation_query,
        "counter_identity_labels": list(fixture.counter_identity_labels or ()),
        "boundary_rule": fixture.boundary_rule,
        "cardinality_rule": fixture.cardinality_rule,
        "reset_policy": fixture.reset_policy,
        "staleness_policy": fixture.staleness_policy,
        "zero_series_rule": fixture.zero_series_rule,
        "scrape_interval_seconds": fixture.scrape_interval_seconds,
        "scrape_interval_tolerance_seconds": (
            fixture.scrape_interval_tolerance_seconds
        ),
        "maximum_scrape_lag_seconds": fixture.maximum_scrape_lag_seconds,
    }


def _expected_promotion_exchange_paths(run_id: str) -> dict[str, str]:
    purposes = (
        "prometheus-total",
        "prometheus-error",
        "prometheus-target-incarnation",
        "jaeger-readiness",
        "opensearch-readiness",
        "probe-baseline",
        "jaeger-correlation-baseline",
        "probe-fault",
        "jaeger-correlation-fault",
        "probe-recovery",
        "jaeger-correlation-recovery",
    )
    prefix = f"observer-visible/{run_id}/telemetry/promotion/raw-exchanges/"
    return {
        purpose: f"{prefix}{sequence:02d}-{purpose}.json"
        for sequence, purpose in enumerate(purposes, start=1)
    }


def _verify_promotion_exchange_artifact(
    payload: dict[str, Any],
    *,
    sequence: int,
    purpose: str,
) -> None:
    if (
        set(payload)
        != {
            "schema_version",
            "run_id",
            "fixture_version",
            "sequence",
            "purpose",
            "request",
            "request_started_at",
            "response_ended_at",
            "monotonic_started_at",
            "monotonic_ended_at",
            "http_status",
            "transport_reason",
            "terminal_failure",
            "response_headers",
            "raw_response_base64",
            "raw_response_sha256",
            "raw_response_partial",
        }
        or payload.get("schema_version") != "phase0.telemetry-promotion-exchange.v1"
        or payload.get("sequence") != sequence
        or payload.get("purpose") != purpose
        or payload.get("http_status") != 200
        or payload.get("transport_reason") != HttpReason.OK.value
        or payload.get("terminal_failure") is not False
        or payload.get("raw_response_partial") is not False
        or not isinstance(payload.get("response_headers"), list)
    ):
        raise ValueError("promotion raw exchange artifact differs")
    request = payload.get("request")
    if (
        not isinstance(request, dict)
        or set(request) != {"method", "target", "headers", "body_sha256"}
        or request.get("method") not in {"GET", "POST"}
        or not isinstance(request.get("target"), str)
        or not request["target"].startswith("/")
        or not isinstance(request.get("headers"), list)
        or _not_sha256(request.get("body_sha256"))
    ):
        raise ValueError("promotion raw exchange request differs")
    started = payload.get("monotonic_started_at")
    ended = payload.get("monotonic_ended_at")
    try:
        utc_started = datetime.fromisoformat(payload["request_started_at"])
        utc_ended = datetime.fromisoformat(payload["response_ended_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("promotion raw exchange timing differs") from error
    if (
        utc_started.tzinfo is None
        or utc_ended.tzinfo is None
        or utc_ended < utc_started
        or not isinstance(started, (int, float))
        or isinstance(started, bool)
        or not isinstance(ended, (int, float))
        or isinstance(ended, bool)
        or not math.isfinite(started)
        or not math.isfinite(ended)
        or ended < started
    ):
        raise ValueError("promotion raw exchange timing differs")
    body = _decode_embedded_body(payload)
    if sha256_bytes(body) != payload.get("raw_response_sha256"):
        raise ValueError("promotion raw exchange body hash differs")


def _verify_raw_promotion_artifact(
    registry: TelemetryQueryRegistry,
    proof: PromotionProof,
    payload: dict[str, Any],
    *,
    exchange_payloads: dict[str, dict[str, Any]],
) -> None:
    if (
        set(payload)
        != {
            "run_id",
            "fixture_version",
            "schema_version",
            "upstream_tag",
            "upstream_commit",
            "upstream_sha256",
            "compose_config_sha256",
            "promotion_started_at",
            "promotion_ended_at",
            "promotion_monotonic_started_at",
            "promotion_monotonic_ended_at",
            "backend_window_started_at",
            "backend_window_ended_at",
            "backend_monotonic_started_at",
            "backend_monotonic_ended_at",
            "backend_observations",
            "probe_phase_observations",
        }
        or payload.get("schema_version") != "phase0.telemetry-promotion-raw.v1"
        or payload.get("upstream_tag") != registry.upstream_tag
        or payload.get("upstream_commit") != registry.upstream_commit
        or payload.get("upstream_sha256") != proof.upstream_sha256
        or payload.get("compose_config_sha256") != registry.compose_config_sha256
    ):
        raise ValueError("raw promotion source identity differs")
    try:
        promotion_started = datetime.fromisoformat(payload["promotion_started_at"])
        promotion_ended = datetime.fromisoformat(payload["promotion_ended_at"])
    except (TypeError, ValueError) as error:
        raise ValueError("promotion UTC window is invalid") from error
    monotonic_started = payload["promotion_monotonic_started_at"]
    monotonic_ended = payload["promotion_monotonic_ended_at"]
    if (
        promotion_started.tzinfo is None
        or promotion_ended.tzinfo is None
        or promotion_ended <= promotion_started
        or not isinstance(monotonic_started, (int, float))
        or isinstance(monotonic_started, bool)
        or not isinstance(monotonic_ended, (int, float))
        or isinstance(monotonic_ended, bool)
        or not math.isfinite(monotonic_started)
        or not math.isfinite(monotonic_ended)
        or monotonic_ended <= monotonic_started
    ):
        raise ValueError("promotion acquisition window is invalid")
    try:
        backend_started = datetime.fromisoformat(payload["backend_window_started_at"])
        backend_ended = datetime.fromisoformat(payload["backend_window_ended_at"])
    except (TypeError, ValueError) as error:
        raise ValueError("promotion backend UTC window is invalid") from error
    backend_monotonic_started = payload["backend_monotonic_started_at"]
    backend_monotonic_ended = payload["backend_monotonic_ended_at"]
    if (
        backend_started.tzinfo is None
        or backend_ended.tzinfo is None
        or not promotion_started <= backend_started < backend_ended <= promotion_ended
        or not isinstance(backend_monotonic_started, (int, float))
        or isinstance(backend_monotonic_started, bool)
        or not isinstance(backend_monotonic_ended, (int, float))
        or isinstance(backend_monotonic_ended, bool)
        or not monotonic_started
        <= backend_monotonic_started
        < backend_monotonic_ended
        <= monotonic_ended
    ):
        raise ValueError("promotion backend acquisition window is invalid")
    observations = payload.get("backend_observations")
    expected = {
        (
            "prometheus",
            "total",
            registry.prometheus.total_query,
            registry.prometheus.expected_response_schema,
        ),
        (
            "prometheus",
            "error",
            registry.prometheus.error_query,
            registry.prometheus.expected_response_schema,
        ),
        (
            "prometheus",
            "target_incarnation",
            registry.prometheus.target_incarnation_query,
            registry.prometheus.expected_response_schema,
        ),
        (
            "jaeger",
            "readiness",
            registry.jaeger.request_template,
            registry.jaeger.expected_response_schema,
        ),
        (
            "opensearch",
            "readiness",
            registry.opensearch.request_template,
            registry.opensearch.expected_response_schema,
        ),
        (
            "probe",
            "business_path",
            f"{registry.probe.method} {registry.probe.path}",
            "otel-demo.frontend.api.data.Ad[].v3.0.0",
        ),
    }
    if (
        not isinstance(observations, list)
        or len(observations) != len(expected)
        or {
            (
                item.get("backend"),
                item.get("query_kind"),
                item.get("request"),
                item.get("response_schema"),
            )
            for item in observations
            if isinstance(item, dict)
        }
        != expected
    ):
        raise ValueError("raw backend promotion coverage differs")
    for observation in observations:
        if set(observation) != {
            "backend",
            "query_kind",
            "request",
            "response_schema",
            "http_status",
            "request_started_at",
            "response_ended_at",
            "monotonic_started_at",
            "monotonic_ended_at",
            "raw_response_base64",
            "raw_response_sha256",
        }:
            raise ValueError("raw backend promotion schema differs")
        _verify_embedded_raw_response(
            observation,
            utc_window=(backend_started, backend_ended),
            monotonic_window=(
                backend_monotonic_started,
                backend_monotonic_ended,
            ),
        )
        _verify_backend_response_schema(
            observation,
            registry,
            utc_window=(backend_started, backend_ended),
        )
        purpose = {
            ("prometheus", "total"): "prometheus-total",
            ("prometheus", "error"): "prometheus-error",
            (
                "prometheus",
                "target_incarnation",
            ): "prometheus-target-incarnation",
            ("jaeger", "readiness"): "jaeger-readiness",
            ("opensearch", "readiness"): "opensearch-readiness",
            ("probe", "business_path"): "probe-baseline",
        }[(observation["backend"], observation["query_kind"])]
        if not _embedded_response_matches_exchange(
            observation,
            exchange_payloads[purpose],
        ):
            raise ValueError("promotion aggregate response is not raw-bound")
    phases = payload.get("probe_phase_observations")
    if not isinstance(phases, list) or [
        item.get("phase") for item in phases if isinstance(item, dict)
    ] != list(registry.probe.required_phases):
        raise ValueError("probe three-phase promotion proof differs")
    phase_windows: list[tuple[datetime, datetime, float, float]] = []
    cycle_numbers: set[int] = set()
    trace_ids: set[str] = set()
    for phase in phases:
        if (
            set(phase)
            != {
                "phase",
                "cycle_number",
                "phase_started_at",
                "phase_ended_at",
                "phase_monotonic_started_at",
                "phase_monotonic_ended_at",
                "fixed_input",
                "observer_input_boundary_passed",
                "unexpected_input_count",
                "trace_id",
                "traceparent_sha256",
                "probe_raw_artifact",
                "probe_raw_sha256",
                "jaeger_request",
                "jaeger_raw_artifact",
                "jaeger_raw_sha256",
                "jaeger_http_status",
                "jaeger_request_started_at",
                "jaeger_response_ended_at",
                "jaeger_monotonic_started_at",
                "jaeger_monotonic_ended_at",
                "jaeger_raw_response_base64",
                "jaeger_raw_response_sha256",
                "getads_span_proven",
                "http_status",
                "request_started_at",
                "response_ended_at",
                "monotonic_started_at",
                "monotonic_ended_at",
                "raw_response_base64",
                "raw_response_sha256",
            }
            or phase.get("fixed_input") != registry.probe.input
            or phase.get("observer_input_boundary_passed") is not True
            or phase.get("unexpected_input_count") != 0
            or not isinstance(phase.get("cycle_number"), int)
            or isinstance(phase.get("cycle_number"), bool)
            or phase["cycle_number"] < 1
        ):
            raise ValueError("probe input boundary proof differs")
        cycle_numbers.add(phase["cycle_number"])
        try:
            phase_started = datetime.fromisoformat(phase["phase_started_at"])
            phase_ended = datetime.fromisoformat(phase["phase_ended_at"])
        except (TypeError, ValueError) as error:
            raise ValueError("probe phase UTC window is invalid") from error
        phase_monotonic_started = phase["phase_monotonic_started_at"]
        phase_monotonic_ended = phase["phase_monotonic_ended_at"]
        if (
            phase_started.tzinfo is None
            or phase_ended.tzinfo is None
            or not promotion_started <= phase_started < phase_ended <= promotion_ended
            or not isinstance(phase_monotonic_started, (int, float))
            or isinstance(phase_monotonic_started, bool)
            or not isinstance(phase_monotonic_ended, (int, float))
            or isinstance(phase_monotonic_ended, bool)
            or not monotonic_started
            <= phase_monotonic_started
            < phase_monotonic_ended
            <= monotonic_ended
        ):
            raise ValueError("probe phase acquisition window is invalid")
        phase_windows.append(
            (
                phase_started,
                phase_ended,
                float(phase_monotonic_started),
                float(phase_monotonic_ended),
            )
        )
        _verify_embedded_raw_response(
            phase,
            utc_window=(phase_started, phase_ended),
            monotonic_window=(
                phase_monotonic_started,
                phase_monotonic_ended,
            ),
        )
        _verify_direct_ad_array(_decode_embedded_body(phase))
        phase_name = phase["phase"]
        trace_id = phase.get("trace_id")
        if (
            not isinstance(trace_id, str)
            or len(trace_id) != 32
            or trace_id in trace_ids
            or phase.get("getads_span_proven") is not True
        ):
            raise ValueError("probe trace correlation identity differs")
        trace_ids.add(trace_id)
        probe_purpose = f"probe-{phase_name}"
        jaeger_purpose = f"jaeger-correlation-{phase_name}"
        probe_path = _expected_promotion_exchange_paths(proof.current_run_id)[
            probe_purpose
        ]
        jaeger_path = _expected_promotion_exchange_paths(proof.current_run_id)[
            jaeger_purpose
        ]
        probe_exchange = exchange_payloads[probe_purpose]
        jaeger_exchange = exchange_payloads[jaeger_purpose]
        headers = probe_exchange["request"]["headers"]
        if (
            phase.get("probe_raw_artifact") != probe_path
            or phase.get("probe_raw_sha256") != proof.artifact_sha256.get(probe_path)
            or phase.get("jaeger_raw_artifact") != jaeger_path
            or phase.get("jaeger_raw_sha256") != proof.artifact_sha256.get(jaeger_path)
            or not _embedded_response_matches_exchange(phase, probe_exchange)
            or headers
            != [
                [
                    "traceparent",
                    next(
                        (value for name, value in headers if name == "traceparent"),
                        "",
                    ),
                ]
            ]
        ):
            raise ValueError("probe trace correlation raw binding differs")
        traceparent = headers[0][1]
        if (
            not _traceparent_matches_trace_id(traceparent, trace_id=trace_id)
            or sha256_bytes(traceparent.encode()) != phase.get("traceparent_sha256")
            or jaeger_exchange["request"]["target"] != f"/api/traces/{trace_id}"
            or phase.get("jaeger_request") != f"/api/traces/{trace_id}"
            or phase.get("jaeger_http_status") != jaeger_exchange["http_status"]
            or phase.get("jaeger_raw_response_sha256")
            != jaeger_exchange["raw_response_sha256"]
        ):
            raise ValueError("probe traceparent or Jaeger request differs")
        try:
            correlation_body = base64.b64decode(
                phase["jaeger_raw_response_base64"],
                validate=True,
            )
            correlation_payload = json.loads(correlation_body)
            jaeger_started = datetime.fromisoformat(phase["jaeger_request_started_at"])
            jaeger_ended = datetime.fromisoformat(phase["jaeger_response_ended_at"])
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            TypeError,
            ValueError,
        ) as error:
            raise ValueError("probe Jaeger correlation payload differs") from error
        if (
            correlation_body != _decode_embedded_body(jaeger_exchange)
            or sha256_bytes(correlation_body) != phase.get("jaeger_raw_response_sha256")
            or jaeger_started.tzinfo is None
            or jaeger_ended.tzinfo is None
            or not phase_started <= jaeger_started <= jaeger_ended <= phase_ended
            or not phase_monotonic_started
            <= phase.get("jaeger_monotonic_started_at", -1)
            <= phase.get("jaeger_monotonic_ended_at", -1)
            <= phase_monotonic_ended
            or not _jaeger_trace_proves_getads(
                correlation_payload,
                trace_id=trace_id,
                operation=registry.jaeger.operation or "",
                window=PhaseWindow(
                    run_id=proof.current_run_id,
                    cycle_number=phase["cycle_number"],
                    scenario_phase=MeasurementPhase(phase_name),
                    utc_started_at=phase_started,
                    utc_ended_at=phase_ended,
                    monotonic_started_at=phase_monotonic_started,
                    monotonic_ended_at=phase_monotonic_ended,
                ),
            )
        ):
            raise ValueError("probe Jaeger GetAds correlation differs")
    if len(cycle_numbers) != 1:
        raise ValueError("probe phase cycles differ")
    if not all(
        earlier[1] < later[0] and earlier[3] < later[2]
        for earlier, later in zip(phase_windows, phase_windows[1:])
    ):
        raise ValueError("probe phase windows are not strictly ordered")


def _embedded_response_matches_exchange(
    embedded: dict[str, Any],
    exchange: dict[str, Any],
) -> bool:
    return (
        embedded.get("http_status") == exchange.get("http_status")
        and embedded.get("request_started_at") == exchange.get("request_started_at")
        and embedded.get("response_ended_at") == exchange.get("response_ended_at")
        and embedded.get("monotonic_started_at") == exchange.get("monotonic_started_at")
        and embedded.get("monotonic_ended_at") == exchange.get("monotonic_ended_at")
        and embedded.get("raw_response_base64") == exchange.get("raw_response_base64")
        and embedded.get("raw_response_sha256") == exchange.get("raw_response_sha256")
    )


def _verify_embedded_raw_response(
    payload: dict[str, Any],
    *,
    utc_window: tuple[datetime, datetime],
    monotonic_window: tuple[float, float],
) -> None:
    started = payload.get("monotonic_started_at")
    ended = payload.get("monotonic_ended_at")
    try:
        request_started = datetime.fromisoformat(payload["request_started_at"])
        response_ended = datetime.fromisoformat(payload["response_ended_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("promotion response UTC timing is invalid") from error
    if (
        payload.get("http_status") != 200
        or not isinstance(started, (int, float))
        or isinstance(started, bool)
        or not isinstance(ended, (int, float))
        or isinstance(ended, bool)
        or not math.isfinite(started)
        or not math.isfinite(ended)
        or ended < started
        or not monotonic_window[0] <= started <= ended <= monotonic_window[1]
        or request_started.tzinfo is None
        or response_ended.tzinfo is None
        or not utc_window[0] <= request_started <= response_ended <= utc_window[1]
    ):
        raise ValueError("promotion response timing or status is invalid")
    body = _decode_embedded_body(payload)
    if sha256_bytes(body) != payload.get("raw_response_sha256"):
        raise ValueError("promotion embedded raw hash differs")


def _decode_embedded_body(payload: dict[str, Any]) -> bytes:
    encoded = payload.get("raw_response_base64")
    if not isinstance(encoded, str):
        raise ValueError("promotion raw response encoding is missing")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("promotion raw response encoding is invalid") from error


def _verify_backend_response_schema(
    observation: dict[str, Any],
    registry: TelemetryQueryRegistry,
    *,
    utc_window: tuple[datetime, datetime],
) -> None:
    try:
        payload = json.loads(_decode_embedded_body(observation))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("promotion backend response is invalid JSON") from error
    backend = observation["backend"]
    if backend == "prometheus":
        _verify_prometheus_promotion_vector(
            payload,
            query_kind=observation["query_kind"],
            registry=registry,
            utc_window=utc_window,
        )
        return
    elif backend == "jaeger":
        valid = _jaeger_response_has_exact_identity(
            payload,
            registry,
            utc_window=utc_window,
        )
    elif backend == "opensearch":
        valid = _opensearch_response_has_exact_identity(
            payload,
            registry,
            utc_window=utc_window,
        )
    else:
        _verify_direct_ad_array(_decode_embedded_body(observation))
        return
    if not valid:
        raise ValueError("promotion backend response schema differs")


def _verify_prometheus_promotion_vector(
    payload: Any,
    *,
    query_kind: str,
    registry: TelemetryQueryRegistry,
    utc_window: tuple[datetime, datetime],
) -> None:
    if (
        not isinstance(payload, dict)
        or set(payload) != {"status", "data"}
        or payload.get("status") != "success"
        or not isinstance(payload.get("data"), dict)
        or set(payload["data"]) != {"resultType", "result"}
        or payload["data"].get("resultType") != "vector"
        or not isinstance(payload["data"].get("result"), list)
    ):
        raise ValueError("promotion Prometheus response schema differs")
    fixture = registry.prometheus
    assert fixture.expected_total_series is not None
    assert fixture.expected_target_incarnation_series is not None
    assert fixture.error_classification is not None
    expected_total = [series.labels for series in fixture.expected_total_series]
    expected = {
        "total": expected_total,
        "error": [
            labels
            for labels in expected_total
            if labels[fixture.error_classification.label]
            in fixture.error_classification.values
        ],
        "target_incarnation": [fixture.expected_target_incarnation_series.labels],
    }.get(query_kind)
    if expected is None:
        raise ValueError("promotion Prometheus query kind differs")
    if not payload["data"]["result"]:
        if (
            query_kind == "error"
            and fixture.zero_series_rule == "absent_error_series_means_zero"
        ):
            return
        raise ValueError("promotion Prometheus response schema differs")
    actual: list[dict[str, str]] = []
    timestamps: set[Decimal] = set()
    for result in payload["data"]["result"]:
        if (
            not isinstance(result, dict)
            or set(result) != {"metric", "value"}
            or not isinstance(result["metric"], dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in result["metric"].items()
            )
            or not isinstance(result["value"], list)
            or len(result["value"]) != 2
            or isinstance(result["value"][0], bool)
            or not isinstance(result["value"][0], (int, float))
            or not isinstance(result["value"][1], str)
        ):
            raise ValueError("promotion Prometheus vector item differs")
        try:
            timestamp = Decimal(str(result["value"][0]))
            value = Decimal(result["value"][1])
        except InvalidOperation as error:
            raise ValueError("promotion Prometheus numeric value differs") from error
        if not timestamp.is_finite() or not value.is_finite() or value < 0:
            raise ValueError("promotion Prometheus numeric value differs")
        timestamps.add(timestamp)
        actual.append(result["metric"])
    if len(timestamps) != 1:
        raise ValueError("promotion Prometheus emitted identity differs")
    timestamp = next(iter(timestamps))
    if not Decimal(str(utc_window[0].timestamp())) <= timestamp <= Decimal(
        str(utc_window[1].timestamp())
    ) or sorted(actual, key=canonical_json_sha256) != sorted(
        expected, key=canonical_json_sha256
    ):
        raise ValueError("promotion Prometheus emitted identity differs")


def _jaeger_response_has_exact_identity(
    payload: Any,
    registry: TelemetryQueryRegistry,
    *,
    utc_window: tuple[datetime, datetime],
) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return False
    for trace in payload["data"]:
        if (
            not isinstance(trace, dict)
            or not isinstance(trace.get("spans"), list)
            or not isinstance(trace.get("processes"), dict)
        ):
            continue
        for span in trace["spans"]:
            if not isinstance(span, dict):
                continue
            process = trace["processes"].get(span.get("processID"))
            start_time = span.get("startTime")
            duration = span.get("duration")
            if (
                not isinstance(start_time, int)
                or isinstance(start_time, bool)
                or not isinstance(duration, int)
                or isinstance(duration, bool)
                or duration < 0
            ):
                continue
            try:
                span_started = datetime.fromtimestamp(
                    start_time / 1_000_000,
                    tz=UTC,
                )
                span_ended = datetime.fromtimestamp(
                    (start_time + duration) / 1_000_000,
                    tz=UTC,
                )
            except (OSError, OverflowError, ValueError):
                continue
            if (
                isinstance(process, dict)
                and process.get("serviceName") == registry.jaeger.service_identity
                and span.get("operationName") == registry.jaeger.operation
                and isinstance(span.get("traceID"), str)
                and bool(span["traceID"])
                and isinstance(span.get("spanID"), str)
                and bool(span["spanID"])
                and utc_window[0] <= span_started <= span_ended <= utc_window[1]
            ):
                return True
    return False


def _jaeger_trace_proves_getads(
    payload: Any,
    *,
    trace_id: str,
    operation: str,
    window: PhaseWindow,
) -> bool:
    """Require a phase-local Ad/GetAds span in the exact propagated trace."""
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("data"), list)
        or len(trace_id) != 32
        or any(character not in "0123456789abcdef" for character in trace_id)
        or trace_id == "0" * 32
        or not operation
    ):
        return False
    for trace in payload["data"]:
        if (
            not isinstance(trace, dict)
            or trace.get("traceID") != trace_id
            or not isinstance(trace.get("spans"), list)
            or not isinstance(trace.get("processes"), dict)
        ):
            continue
        for span in trace["spans"]:
            if (
                not isinstance(span, dict)
                or span.get("traceID") != trace_id
                or span.get("operationName") != operation
            ):
                continue
            process = trace["processes"].get(span.get("processID"))
            started = span.get("startTime")
            duration = span.get("duration")
            if (
                not isinstance(process, dict)
                or process.get("serviceName") != "ad"
                or not isinstance(started, int)
                or isinstance(started, bool)
                or not isinstance(duration, int)
                or isinstance(duration, bool)
                or duration < 0
            ):
                continue
            try:
                utc_started = datetime.fromtimestamp(started / 1_000_000, tz=UTC)
                utc_ended = datetime.fromtimestamp(
                    (started + duration) / 1_000_000,
                    tz=UTC,
                )
            except (OSError, OverflowError, ValueError):
                continue
            if window.utc_started_at <= utc_started <= utc_ended <= window.utc_ended_at:
                return True
    return False


def _traceparent_matches_trace_id(
    traceparent: object,
    *,
    trace_id: str,
) -> bool:
    if not isinstance(traceparent, str):
        return False
    parts = traceparent.split("-")
    return (
        len(parts) == 4
        and parts[0] == "00"
        and parts[1] == trace_id
        and len(parts[1]) == 32
        and parts[1] != "0" * 32
        and len(parts[2]) == 16
        and parts[2] != "0" * 16
        and parts[3] == "01"
        and all(
            character in "0123456789abcdef"
            for component in parts[1:3]
            for character in component
        )
    )


def _opensearch_response_has_exact_identity(
    payload: Any,
    registry: TelemetryQueryRegistry,
    *,
    utc_window: tuple[datetime, datetime],
) -> bool:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("hits"), dict)
        or not isinstance(payload["hits"].get("hits"), list)
    ):
        return False
    fixture = registry.opensearch
    assert fixture.index is not None
    assert fixture.service_identity_field is not None
    assert fixture.timestamp_field is not None
    for hit in payload["hits"]["hits"]:
        if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
            continue
        source = hit["_source"]
        index = hit.get("_index")
        observed_at = _nested_value(source, fixture.timestamp_field)
        try:
            observed = datetime.fromisoformat(observed_at)
        except (TypeError, ValueError):
            continue
        if (
            isinstance(index, str)
            and index.startswith(fixture.index.removesuffix("*"))
            and _nested_value(source, fixture.service_identity_field)
            == fixture.service_identity
            and observed.tzinfo is not None
            and utc_window[0] <= observed <= utc_window[1]
            and (
                fixture.trace_id_field is None
                or isinstance(_nested_value(source, fixture.trace_id_field), str)
            )
            and (
                fixture.span_id_field is None
                or isinstance(_nested_value(source, fixture.span_id_field), str)
            )
        ):
            return True
    return False


def _nested_value(payload: dict[str, Any], dotted_path: str) -> Any:
    value: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _verify_direct_ad_array(body: bytes) -> None:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("promotion probe response is invalid JSON") from error
    if (
        not isinstance(payload, list)
        or not payload
        or any(
            not isinstance(ad, dict)
            or set(ad) != {"redirectUrl", "text"}
            or not isinstance(ad["redirectUrl"], str)
            or not isinstance(ad["text"], str)
            for ad in payload
        )
    ):
        raise ValueError("promotion probe response does not match pinned Ad schema")


class PrometheusReason(str, Enum):
    READY = "READY"
    QUERY_FIXTURE_NOT_FROZEN = "QUERY_FIXTURE_NOT_FROZEN"
    RESOURCE_OWNERSHIP_UNKNOWN = "RESOURCE_OWNERSHIP_UNKNOWN"
    HTTP_DEADLINE_EXCEEDED = "HTTP_DEADLINE_EXCEEDED"
    HTTP_TRANSPORT_ERROR = "HTTP_TRANSPORT_ERROR"
    HTTP_REDIRECT_FORBIDDEN = "HTTP_REDIRECT_FORBIDDEN"
    HTTP_STATUS_ERROR = "HTTP_STATUS_ERROR"
    HTTP_HEADER_LIMIT_EXCEEDED = "HTTP_HEADER_LIMIT_EXCEEDED"
    HTTP_BODY_LIMIT_EXCEEDED = "HTTP_BODY_LIMIT_EXCEEDED"
    EVIDENCE_PERSISTENCE_FAILED = "EVIDENCE_PERSISTENCE_FAILED"
    PROMETHEUS_SCHEMA_INVALID = "PROMETHEUS_SCHEMA_INVALID"
    PROMETHEUS_STALE_SAMPLE = "PROMETHEUS_STALE_SAMPLE"
    PROMETHEUS_SCRAPE_TIMESTAMP_MISMATCH = "PROMETHEUS_SCRAPE_TIMESTAMP_MISMATCH"
    PROMETHEUS_SCRAPE_GAP = "PROMETHEUS_SCRAPE_GAP"
    PROMETHEUS_CARDINALITY_DRIFT = "PROMETHEUS_CARDINALITY_DRIFT"
    PROMETHEUS_COUNTER_RESET_OR_TARGET_RESTART = (
        "PROMETHEUS_COUNTER_RESET_OR_TARGET_RESTART"
    )
    PROMETHEUS_COUNTER_QUERY_MISMATCH = "PROMETHEUS_COUNTER_QUERY_MISMATCH"
    PROMETHEUS_NON_INTEGRAL_DELTA = "PROMETHEUS_NON_INTEGRAL_DELTA"
    PROMETHEUS_ERRORS_EXCEED_TOTAL = "PROMETHEUS_ERRORS_EXCEED_TOTAL"
    WINDOW_SAMPLE_TIMEOUT = "WINDOW_SAMPLE_TIMEOUT"


@dataclass(frozen=True)
class PrometheusMeasurement:
    reason: PrometheusReason
    run_id: str | None = None
    cycle_number: int | None = None
    phase: str | None = None
    fixture_sha256: str | None = None
    getads_attempts: int | None = None
    getads_errors: int | None = None
    start_sample_timestamp: datetime | None = None
    end_sample_timestamp: datetime | None = None
    artifact_paths: tuple[str, ...] = ()
    artifact_sha256: tuple[tuple[str, str], ...] = ()
    _receipt_token: object | None = field(default=None, repr=False, compare=False)
    _production_receipt: bool = field(default=False, repr=False, compare=False)
    _store_root: str | None = field(default=None, repr=False, compare=False)

    @property
    def ready(self) -> bool:
        return self.reason is PrometheusReason.READY

    def is_production_receipt(
        self,
        *,
        capability: FrozenTelemetryQueryCapability,
        store: ObserverEvidenceStore,
        window: PhaseWindow,
    ) -> bool:
        return (
            self._receipt_token is _PROMETHEUS_RECEIPT_TOKEN
            and self._production_receipt
            and self._store_root == str(store.root)
            and capability.store is store
            and capability.is_authentic()
            and self.run_id == window.run_id == capability.run_id
            and self.cycle_number == window.cycle_number
            and self.phase == window.scenario_phase.value
            and self.fixture_sha256 == capability.content_sha256
        )


class _Artifact(Protocol):
    path: Path
    sha256: str


class _EvidenceStore(Protocol):
    def write_immutable(
        self,
        relative_path: str,
        value: dict[str, Any],
    ) -> _Artifact: ...


class _HttpClient(Protocol):
    @property
    def run_id(self) -> str: ...

    def request(self, request: HttpRequest) -> HttpExchange: ...


@dataclass(frozen=True)
class _ParsedScrape:
    timestamp: datetime
    values: dict[tuple[tuple[str, str], ...], Decimal]
    labels: tuple[dict[str, str], ...]
    zero_series_inferred: bool = False


@dataclass(frozen=True)
class _ScrapePair:
    timestamp: datetime
    total: _ParsedScrape
    errors: _ParsedScrape
    incarnation: _ParsedScrape
    acquired_monotonic: float


class _PrometheusParseFailure(RuntimeError):
    def __init__(self, reason: PrometheusReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class PrometheusAdapter:
    """Acquire exact raw counter scrapes and evaluate one phase-local delta."""

    def __init__(
        self,
        *,
        client: _HttpClient,
        evidence_store: _EvidenceStore,
        fixture: RegistryAccess,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._store = evidence_store
        self._loaded = fixture
        self._sleep = sleep

    def _measurement(self, **values: Any) -> PrometheusMeasurement:
        production = (
            isinstance(self._loaded, FrozenTelemetryQueryCapability)
            and type(self._client) is OwnedHttpClient
            and _owned_http_client_has_production_integrity(self._client)
            and isinstance(self._store, ObserverEvidenceStore)
            and self._loaded.store is self._store
        )
        return PrometheusMeasurement(
            **values,
            _receipt_token=_PROMETHEUS_RECEIPT_TOKEN,
            _production_receipt=production,
            _store_root=str(self._store.root) if production else None,
        )

    def measure_getads(
        self,
        *,
        window: PhaseWindow,
        base_url: str,
        artifact_prefix: str,
    ) -> PrometheusMeasurement:
        minimum_attempts = _ACCEPTANCE_MINIMUM_ATTEMPTS
        deadline_seconds = _ACCEPTANCE_DEADLINE_SECONDS
        if (
            not _registry_access_is_frozen_for_adapter(
                self._loaded,
                run_id=window.run_id,
                evidence_store=self._store,
                client=self._client,
            )
            or self._loaded.registry.prometheus.state is not FixtureState.FROZEN
        ):
            return self._measurement(reason=PrometheusReason.QUERY_FIXTURE_NOT_FROZEN)
        if self._client.run_id != window.run_id:
            return self._measurement(reason=PrometheusReason.RESOURCE_OWNERSHIP_UNKNOWN)
        fixture = self._loaded.registry.prometheus
        assert fixture.total_query is not None
        assert fixture.error_query is not None
        assert fixture.target_incarnation_query is not None
        assert fixture.scrape_interval_seconds is not None
        assert fixture.maximum_scrape_lag_seconds is not None
        assert fixture.expected_total_series is not None
        assert fixture.expected_target_incarnation_series is not None
        assert fixture.error_classification is not None

        endpoint = OwnedEndpoint(
            base_url=base_url,
            service=fixture.target.service,
            target_port=fixture.target.target_port,
            protocol=fixture.target.protocol,
        )
        expected_total = tuple(
            series.labels for series in fixture.expected_total_series
        )
        expected_errors = tuple(
            labels
            for labels in expected_total
            if labels.get(fixture.error_classification.label)
            in fixture.error_classification.values
        )
        expected_incarnation = (fixture.expected_target_incarnation_series.labels,)
        if not expected_errors:
            return self._measurement(reason=PrometheusReason.PROMETHEUS_SCHEMA_INVALID)

        paths: list[str] = []
        previous: _ScrapePair | None = None
        anchor: _ScrapePair | None = None
        sample_number = 0
        while True:
            sample_number += 1
            request_deadline = (
                window.monotonic_ended_at
                if anchor is None
                else min(
                    window.monotonic_ended_at,
                    anchor.acquired_monotonic + deadline_seconds,
                )
            )
            total_exchange = self._query(
                endpoint,
                fixture.total_query,
                deadline=request_deadline,
            )
            total_raw_path = (
                f"{artifact_prefix}/telemetry/prometheus/"
                f"sample-{sample_number:04d}-total-raw.json"
            )
            if not self._persist_raw(
                total_raw_path,
                total_exchange,
                window=window,
                query_kind="total",
                query=fixture.total_query,
                paths=paths,
            ):
                return self._measurement(
                    reason=PrometheusReason.EVIDENCE_PERSISTENCE_FAILED,
                    artifact_paths=tuple(paths),
                )
            if not total_exchange.succeeded:
                reason = _http_reason(total_exchange.reason)
                if not self._persist_decision(
                    total_raw_path,
                    window=window,
                    reason=reason,
                    paths=paths,
                ):
                    reason = PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=reason,
                    paths=paths,
                )

            try:
                total = _parse_vector(
                    total_exchange,
                    expected=expected_total,
                    fixture=fixture,
                    window=window,
                    zero_fill_missing=(
                        tuple(expected_errors)
                        if fixture.zero_series_rule == "absent_error_series_means_zero"
                        else ()
                    ),
                )
            except _PrometheusParseFailure as failure:
                reason = failure.reason
                if not self._persist_decision(
                    total_raw_path,
                    window=window,
                    reason=reason,
                    paths=paths,
                ):
                    reason = PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=reason,
                    paths=paths,
                )
            if not self._persist_decision(
                total_raw_path,
                window=window,
                reason=PrometheusReason.READY,
                paths=paths,
                parsed=total,
            ):
                return self._measurement(
                    reason=PrometheusReason.EVIDENCE_PERSISTENCE_FAILED,
                    artifact_paths=tuple(paths),
                )

            error_exchange = self._query(
                endpoint,
                fixture.error_query,
                deadline=request_deadline,
            )
            error_raw_path = (
                f"{artifact_prefix}/telemetry/prometheus/"
                f"sample-{sample_number:04d}-error-raw.json"
            )
            if not self._persist_raw(
                error_raw_path,
                error_exchange,
                window=window,
                query_kind="error",
                query=fixture.error_query,
                paths=paths,
            ):
                return self._measurement(
                    reason=PrometheusReason.EVIDENCE_PERSISTENCE_FAILED,
                    artifact_paths=tuple(paths),
                )
            if not error_exchange.succeeded:
                reason = _http_reason(error_exchange.reason)
                if not self._persist_decision(
                    error_raw_path,
                    window=window,
                    reason=reason,
                    paths=paths,
                ):
                    reason = PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=reason,
                    paths=paths,
                )
            try:
                errors = _parse_vector(
                    error_exchange,
                    expected=expected_errors,
                    fixture=fixture,
                    window=window,
                    empty_as_zero_timestamp=(
                        total.timestamp
                        if fixture.zero_series_rule == "absent_error_series_means_zero"
                        else None
                    ),
                )
            except _PrometheusParseFailure as failure:
                reason = failure.reason
                if not self._persist_decision(
                    error_raw_path,
                    window=window,
                    reason=reason,
                    paths=paths,
                ):
                    reason = PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=reason,
                    paths=paths,
                )
            if not self._persist_decision(
                error_raw_path,
                window=window,
                reason=PrometheusReason.READY,
                paths=paths,
                parsed=errors,
            ):
                return self._measurement(
                    reason=PrometheusReason.EVIDENCE_PERSISTENCE_FAILED,
                    artifact_paths=tuple(paths),
                )

            incarnation_exchange = self._query(
                endpoint,
                fixture.target_incarnation_query,
                deadline=request_deadline,
            )
            incarnation_raw_path = (
                f"{artifact_prefix}/telemetry/prometheus/"
                f"sample-{sample_number:04d}-incarnation-raw.json"
            )
            if not self._persist_raw(
                incarnation_raw_path,
                incarnation_exchange,
                window=window,
                query_kind="target_incarnation",
                query=fixture.target_incarnation_query,
                paths=paths,
            ):
                return self._measurement(
                    reason=PrometheusReason.EVIDENCE_PERSISTENCE_FAILED,
                    artifact_paths=tuple(paths),
                )
            if not incarnation_exchange.succeeded:
                reason = _http_reason(incarnation_exchange.reason)
                if not self._persist_decision(
                    incarnation_raw_path,
                    window=window,
                    reason=reason,
                    paths=paths,
                ):
                    reason = PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=reason,
                    paths=paths,
                )
            try:
                incarnation = _parse_vector(
                    incarnation_exchange,
                    expected=expected_incarnation,
                    fixture=fixture,
                    window=window,
                )
            except _PrometheusParseFailure as failure:
                reason = failure.reason
                if not self._persist_decision(
                    incarnation_raw_path,
                    window=window,
                    reason=reason,
                    paths=paths,
                ):
                    reason = PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=reason,
                    paths=paths,
                )
            if not self._persist_decision(
                incarnation_raw_path,
                window=window,
                reason=PrometheusReason.READY,
                paths=paths,
                parsed=incarnation,
            ):
                return self._measurement(
                    reason=PrometheusReason.EVIDENCE_PERSISTENCE_FAILED,
                    artifact_paths=tuple(paths),
                )

            acquired_monotonic = max(
                total_exchange.monotonic_ended_at,
                error_exchange.monotonic_ended_at,
                incarnation_exchange.monotonic_ended_at,
            )
            pair = _ScrapePair(
                timestamp=total.timestamp,
                total=total,
                errors=errors,
                incarnation=incarnation,
                acquired_monotonic=acquired_monotonic,
            )
            pair_reason = _validate_scrape_pair(
                pair,
                previous=previous,
                fixture=fixture,
            )
            if pair_reason is not None:
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=pair_reason,
                    paths=paths,
                )
            if anchor is None:
                anchor = pair
                previous = pair
                self._wait_for_next_scrape(
                    pair,
                    anchor=anchor,
                    window=window,
                    deadline_seconds=deadline_seconds,
                    fixture=fixture,
                )
                continue
            if acquired_monotonic - anchor.acquired_monotonic > deadline_seconds:
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=PrometheusReason.WINDOW_SAMPLE_TIMEOUT,
                    paths=paths,
                )

            reset_reason = _detect_counter_decrease(previous, pair)
            if reset_reason is not None:
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=reset_reason,
                    paths=paths,
                )
            attempts_delta = _counter_delta(anchor.total, pair.total)
            errors_delta = _counter_delta(anchor.errors, pair.errors)
            if attempts_delta < 0 or errors_delta < 0:
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=(
                        PrometheusReason.PROMETHEUS_COUNTER_RESET_OR_TARGET_RESTART
                    ),
                    paths=paths,
                )
            if (
                attempts_delta != attempts_delta.to_integral_value()
                or errors_delta != errors_delta.to_integral_value()
            ):
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=PrometheusReason.PROMETHEUS_NON_INTEGRAL_DELTA,
                    paths=paths,
                )
            attempts = int(attempts_delta)
            errors_count = int(errors_delta)
            if errors_count > attempts:
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=PrometheusReason.PROMETHEUS_ERRORS_EXCEED_TOTAL,
                    paths=paths,
                )
            if attempts >= minimum_attempts:
                return self._finish(
                    window=window,
                    artifact_prefix=artifact_prefix,
                    reason=PrometheusReason.READY,
                    paths=paths,
                    getads_attempts=attempts,
                    getads_errors=errors_count,
                    start_sample_timestamp=anchor.timestamp,
                    end_sample_timestamp=pair.timestamp,
                )
            previous = pair
            self._wait_for_next_scrape(
                pair,
                anchor=anchor,
                window=window,
                deadline_seconds=deadline_seconds,
                fixture=fixture,
            )

    def _query(
        self,
        endpoint: OwnedEndpoint,
        query: str,
        *,
        deadline: float,
    ) -> HttpExchange:
        target = f"/api/v1/query?query={quote(query, safe='')}"
        return self._client.request(
            HttpRequest(
                endpoint=endpoint,
                method="GET",
                target=target,
                absolute_deadline_monotonic=deadline,
            )
        )

    def _wait_for_next_scrape(
        self,
        current: _ScrapePair,
        *,
        anchor: _ScrapePair,
        window: PhaseWindow,
        deadline_seconds: float,
        fixture: PrometheusQueryFixture,
    ) -> None:
        assert fixture.scrape_interval_seconds is not None
        remaining = (
            min(
                anchor.acquired_monotonic + deadline_seconds,
                window.monotonic_ended_at,
            )
            - current.acquired_monotonic
        )
        if remaining <= 0:
            return
        self._sleep(min(fixture.scrape_interval_seconds, remaining))

    def _persist_raw(
        self,
        path: str,
        exchange: HttpExchange,
        *,
        window: PhaseWindow,
        query_kind: str,
        query: str,
        paths: list[str],
    ) -> bool:
        payload = {
            "schema_version": "phase0.telemetry-raw.v1",
            "run_id": window.run_id,
            "cycle_number": window.cycle_number,
            "scenario_phase": window.scenario_phase.value,
            "fixture_version": self._loaded.registry.fixture_version,
            "fixture_sha256": self._loaded.content_sha256,
            "upstream_commit": self._loaded.registry.upstream_commit,
            "compose_config_sha256": self._loaded.registry.compose_config_sha256,
            "backend": "prometheus",
            "query_kind": query_kind,
            "raw_query": query,
            "request_target": exchange.request.target,
            "started_at": exchange.started_at.isoformat(),
            "ended_at": exchange.ended_at.isoformat(),
            "monotonic_started_at": exchange.monotonic_started_at,
            "monotonic_ended_at": exchange.monotonic_ended_at,
            "http_status": exchange.status_code,
            "http_reason": exchange.reason.value,
            "raw_response_base64": base64.b64encode(exchange.raw_body).decode("ascii"),
            "raw_response_sha256": exchange.raw_sha256,
            "raw_response_partial": exchange.raw_body_partial,
            "boundary_rule": self._loaded.registry.prometheus.boundary_rule,
        }
        try:
            artifact = self._store.write_immutable(path, payload)
        except (OSError, RuntimeError, ValueError):
            return False
        paths.append(str(artifact.path))
        return True

    def _persist_decision(
        self,
        raw_path: str,
        *,
        window: PhaseWindow,
        reason: PrometheusReason,
        paths: list[str],
        parsed: _ParsedScrape | None = None,
    ) -> bool:
        path = raw_path.removesuffix("-raw.json") + "-decision.json"
        payload: dict[str, Any] = {
            "schema_version": "phase0.telemetry-parse-decision.v1",
            "run_id": window.run_id,
            "cycle_number": window.cycle_number,
            "scenario_phase": window.scenario_phase.value,
            "fixture_version": self._loaded.registry.fixture_version,
            "fixture_sha256": self._loaded.content_sha256,
            "backend": "prometheus",
            "raw_response_artifact": raw_path,
            "decision": reason is PrometheusReason.READY,
            "reason": reason.value,
            "parsed_sample_timestamp": (
                parsed.timestamp.isoformat() if parsed is not None else None
            ),
            "parsed_series": parsed.labels if parsed is not None else (),
            "zero_series_inferred": (
                parsed.zero_series_inferred if parsed is not None else False
            ),
        }
        try:
            artifact = self._store.write_immutable(path, payload)
        except (OSError, RuntimeError, ValueError):
            return False
        paths.append(str(artifact.path))
        return True

    def _finish(
        self,
        *,
        window: PhaseWindow,
        artifact_prefix: str,
        reason: PrometheusReason,
        paths: list[str],
        getads_attempts: int | None = None,
        getads_errors: int | None = None,
        start_sample_timestamp: datetime | None = None,
        end_sample_timestamp: datetime | None = None,
    ) -> PrometheusMeasurement:
        path = f"{artifact_prefix}/telemetry/prometheus/measurement-decision.json"
        payload = {
            "schema_version": "phase0.prometheus-measurement-decision.v1",
            "run_id": window.run_id,
            "cycle_number": window.cycle_number,
            "scenario_phase": window.scenario_phase.value,
            "fixture_version": self._loaded.registry.fixture_version,
            "fixture_sha256": self._loaded.content_sha256,
            "backend": "prometheus",
            "decision": reason is PrometheusReason.READY,
            "reason": reason.value,
            "getads_attempts": getads_attempts,
            "getads_errors": getads_errors,
            "start_sample_timestamp": (
                start_sample_timestamp.isoformat()
                if start_sample_timestamp is not None
                else None
            ),
            "end_sample_timestamp": (
                end_sample_timestamp.isoformat()
                if end_sample_timestamp is not None
                else None
            ),
            "raw_and_parse_artifacts": tuple(paths),
        }
        try:
            artifact = self._store.write_immutable(path, payload)
        except (OSError, RuntimeError, ValueError):
            return self._measurement(
                reason=PrometheusReason.EVIDENCE_PERSISTENCE_FAILED,
                artifact_paths=tuple(paths),
            )
        paths.append(str(artifact.path))
        return self._measurement(
            reason=reason,
            run_id=window.run_id,
            cycle_number=window.cycle_number,
            phase=window.scenario_phase.value,
            fixture_sha256=self._loaded.content_sha256,
            getads_attempts=getads_attempts,
            getads_errors=getads_errors,
            start_sample_timestamp=start_sample_timestamp,
            end_sample_timestamp=end_sample_timestamp,
            artifact_paths=tuple(paths),
            artifact_sha256=_hash_existing_artifacts(paths),
        )


def _parse_vector(
    exchange: HttpExchange,
    *,
    expected: tuple[dict[str, str], ...],
    fixture: PrometheusQueryFixture,
    window: PhaseWindow,
    empty_as_zero_timestamp: datetime | None = None,
    zero_fill_missing: tuple[dict[str, str], ...] = (),
) -> _ParsedScrape:
    try:
        payload = json.loads(
            exchange.raw_body,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "success"
            or not isinstance(payload.get("data"), dict)
            or payload["data"].get("resultType") != "vector"
            or not isinstance(payload["data"].get("result"), list)
        ):
            raise TypeError
        result = payload["data"]["result"]
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        raise _PrometheusParseFailure(
            PrometheusReason.PROMETHEUS_SCHEMA_INVALID
        ) from None

    expected_identities = {tuple(sorted(labels.items())) for labels in expected}
    values: dict[tuple[tuple[str, str], ...], Decimal] = {}
    timestamps: set[Decimal] = set()
    labels_out: list[dict[str, str]] = []
    zero_series_inferred = False
    try:
        for item in result:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("metric"), dict)
                or not isinstance(item.get("value"), list)
                or len(item["value"]) != 2
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in item["metric"].items()
                )
            ):
                raise TypeError
            identity = tuple(sorted(item["metric"].items()))
            timestamp = Decimal(str(item["value"][0]))
            raw_counter = str(item["value"][1])
            if raw_counter.casefold() in {"nan", "stalenan"}:
                raise _PrometheusParseFailure(PrometheusReason.PROMETHEUS_STALE_SAMPLE)
            counter = Decimal(raw_counter)
            if (
                not timestamp.is_finite()
                or not counter.is_finite()
                or counter < 0
                or identity in values
            ):
                raise ValueError
            timestamps.add(timestamp)
            values[identity] = counter
            labels_out.append(dict(item["metric"]))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise _PrometheusParseFailure(
            PrometheusReason.PROMETHEUS_SCHEMA_INVALID
        ) from None

    if not result and empty_as_zero_timestamp is not None:
        values = {identity: Decimal(0) for identity in expected_identities}
        timestamps = {Decimal(str(empty_as_zero_timestamp.timestamp()))}
        labels_out = [dict(identity) for identity in expected_identities]
        zero_series_inferred = True
    elif values and set(values) != expected_identities:
        missing = expected_identities - set(values)
        zero_fill_identities = {
            tuple(sorted(labels.items())) for labels in zero_fill_missing
        }
        if missing and missing <= zero_fill_identities and len(timestamps) == 1:
            values.update({identity: Decimal(0) for identity in missing})
            labels_out.extend(dict(identity) for identity in missing)
            zero_series_inferred = True

    if set(values) != expected_identities:
        raise _PrometheusParseFailure(PrometheusReason.PROMETHEUS_CARDINALITY_DRIFT)
    if len(timestamps) != 1:
        raise _PrometheusParseFailure(
            PrometheusReason.PROMETHEUS_SCRAPE_TIMESTAMP_MISMATCH
        )
    timestamp_decimal = next(iter(timestamps))
    try:
        timestamp = datetime.fromtimestamp(float(timestamp_decimal), tz=UTC)
    except (OSError, OverflowError, ValueError):
        raise _PrometheusParseFailure(
            PrometheusReason.PROMETHEUS_SCHEMA_INVALID
        ) from None
    lag = (exchange.ended_at - timestamp).total_seconds()
    assert fixture.maximum_scrape_lag_seconds is not None
    if (
        not window.contains_observation(timestamp)
        or lag < 0
        or lag > fixture.maximum_scrape_lag_seconds
    ):
        raise _PrometheusParseFailure(PrometheusReason.PROMETHEUS_STALE_SAMPLE)
    return _ParsedScrape(
        timestamp=timestamp,
        values=values,
        labels=tuple(
            sorted(labels_out, key=lambda value: tuple(sorted(value.items())))
        ),
        zero_series_inferred=zero_series_inferred,
    )


def _validate_scrape_pair(
    pair: _ScrapePair,
    *,
    previous: _ScrapePair | None,
    fixture: PrometheusQueryFixture,
) -> PrometheusReason | None:
    if not (
        pair.total.timestamp == pair.errors.timestamp == pair.incarnation.timestamp
    ):
        return PrometheusReason.PROMETHEUS_SCRAPE_TIMESTAMP_MISMATCH
    if len(pair.incarnation.values) != 1:
        return PrometheusReason.PROMETHEUS_CARDINALITY_DRIFT
    for identity, value in pair.errors.values.items():
        if pair.total.values.get(identity) != value:
            return PrometheusReason.PROMETHEUS_COUNTER_QUERY_MISMATCH
    if previous is not None:
        if pair.incarnation.values != previous.incarnation.values:
            return PrometheusReason.PROMETHEUS_COUNTER_RESET_OR_TARGET_RESTART
        if pair.timestamp <= previous.timestamp:
            return PrometheusReason.PROMETHEUS_SCRAPE_TIMESTAMP_MISMATCH
        assert fixture.scrape_interval_seconds is not None
        assert fixture.scrape_interval_tolerance_seconds is not None
        interval = (pair.timestamp - previous.timestamp).total_seconds()
        if (
            abs(interval - fixture.scrape_interval_seconds)
            > fixture.scrape_interval_tolerance_seconds
        ):
            return PrometheusReason.PROMETHEUS_SCRAPE_GAP
    return None


def _detect_counter_decrease(
    previous: _ScrapePair,
    current: _ScrapePair,
) -> PrometheusReason | None:
    for previous_scrape, current_scrape in (
        (previous.total, current.total),
        (previous.errors, current.errors),
    ):
        if set(previous_scrape.values) != set(current_scrape.values):
            return PrometheusReason.PROMETHEUS_CARDINALITY_DRIFT
        if any(
            current_scrape.values[identity] < value
            for identity, value in previous_scrape.values.items()
        ):
            return PrometheusReason.PROMETHEUS_COUNTER_RESET_OR_TARGET_RESTART
    return None


def _counter_delta(start: _ParsedScrape, end: _ParsedScrape) -> Decimal:
    return sum(
        (end.values[identity] - start.values[identity] for identity in start.values),
        start=Decimal(0),
    )


def _http_reason(reason: HttpReason) -> PrometheusReason:
    try:
        return PrometheusReason(reason.value)
    except ValueError:
        return PrometheusReason.HTTP_TRANSPORT_ERROR


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _not_sha256(value: str) -> bool:
    if len(value) != 64:
        return True
    return any(character not in "0123456789abcdef" for character in value)


def _hash_existing_artifacts(paths: list[str]) -> tuple[tuple[str, str], ...]:
    try:
        return tuple((path, sha256_file(Path(path))) for path in paths)
    except (OSError, ValueError):
        return ()
