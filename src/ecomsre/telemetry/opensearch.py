"""Exact-identity, phase-local OpenSearch readiness gate."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_bytes, sha256_file
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    OwnedEndpoint,
    OwnedHttpClient,
    PhaseWindow,
    _owned_http_client_has_production_integrity,
)
from ecomsre.telemetry.prometheus import (
    FixtureState,
    FrozenTelemetryQueryCapability,
    RegistryAccess,
    _registry_access_is_frozen_for_adapter,
)

_OPENSEARCH_RECEIPT_TOKEN = object()


class OpenSearchReason(str, Enum):
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
    OPENSEARCH_SCHEMA_INVALID = "OPENSEARCH_SCHEMA_INVALID"
    OPENSEARCH_IDENTITY_MISMATCH = "OPENSEARCH_IDENTITY_MISMATCH"
    OPENSEARCH_STALE_LOG = "OPENSEARCH_STALE_LOG"


@dataclass(frozen=True)
class OpenSearchReadiness:
    reason: OpenSearchReason
    run_id: str | None = None
    cycle_number: int | None = None
    phase: str | None = None
    fixture_sha256: str | None = None
    log_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    observed_at: datetime | None = None
    artifact_paths: tuple[str, ...] = ()
    artifact_sha256: tuple[tuple[str, str], ...] = ()
    _receipt_token: object | None = field(default=None, repr=False, compare=False)
    _production_receipt: bool = field(default=False, repr=False, compare=False)
    _store_root: str | None = field(default=None, repr=False, compare=False)

    @property
    def ready(self) -> bool:
        return self.reason is OpenSearchReason.READY

    def is_production_receipt(
        self,
        *,
        capability: FrozenTelemetryQueryCapability,
        store: ObserverEvidenceStore,
        window: PhaseWindow,
    ) -> bool:
        return (
            self._receipt_token is _OPENSEARCH_RECEIPT_TOKEN
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
class _SelectedLog:
    log_id: str
    observed_at: datetime
    trace_id: str | None
    span_id: str | None


class OpenSearchAdapter:
    def __init__(
        self,
        *,
        client: _HttpClient,
        evidence_store: _EvidenceStore,
        fixture: RegistryAccess,
    ) -> None:
        self._client = client
        self._store = evidence_store
        self._loaded = fixture

    def _result(self, **values: Any) -> OpenSearchReadiness:
        production = (
            isinstance(self._loaded, FrozenTelemetryQueryCapability)
            and type(self._client) is OwnedHttpClient
            and _owned_http_client_has_production_integrity(self._client)
            and isinstance(self._store, ObserverEvidenceStore)
            and self._loaded.store is self._store
        )
        return OpenSearchReadiness(
            **values,
            _receipt_token=_OPENSEARCH_RECEIPT_TOKEN,
            _production_receipt=production,
            _store_root=str(self._store.root) if production else None,
        )

    def check_readiness(
        self,
        *,
        window: PhaseWindow,
        base_url: str,
        artifact_prefix: str,
    ) -> OpenSearchReadiness:
        registry = self._loaded.registry
        fixture = registry.opensearch
        if (
            not _registry_access_is_frozen_for_adapter(
                self._loaded,
                run_id=window.run_id,
                evidence_store=self._store,
                client=self._client,
            )
            or fixture.state is not FixtureState.FROZEN
        ):
            return self._result(reason=OpenSearchReason.QUERY_FIXTURE_NOT_FROZEN)
        if self._client.run_id != window.run_id:
            return self._result(reason=OpenSearchReason.RESOURCE_OWNERSHIP_UNKNOWN)
        assert fixture.index is not None
        assert fixture.service_identity_field is not None
        assert fixture.service_identity is not None
        assert fixture.timestamp_field is not None
        body = canonical_json_bytes(
            {
                "size": 100,
                "sort": [{fixture.timestamp_field: {"order": "asc"}}],
                "query": {
                    "bool": {
                        "filter": [
                            {
                                "term": {
                                    fixture.service_identity_field: (
                                        fixture.service_identity
                                    )
                                }
                            },
                            {
                                "range": {
                                    fixture.timestamp_field: {
                                        "gte": window.utc_started_at.isoformat(),
                                        "lte": window.utc_ended_at.isoformat(),
                                        "format": "strict_date_optional_time",
                                    }
                                }
                            },
                        ]
                    }
                },
            }
        )
        request = HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_url,
                service=fixture.target.service,
                target_port=fixture.target.target_port,
                protocol=fixture.target.protocol,
            ),
            method="POST",
            target=f"/{quote(fixture.index, safe='')}/_search",
            headers=(
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
            ),
            body=body,
            absolute_deadline_monotonic=window.monotonic_ended_at,
        )
        exchange = self._client.request(request)
        raw_path = f"{artifact_prefix}/telemetry/opensearch/readiness-raw.json"
        paths: list[str] = []
        if not self._persist_raw(
            raw_path,
            exchange,
            window=window,
            paths=paths,
        ):
            return self._result(reason=OpenSearchReason.EVIDENCE_PERSISTENCE_FAILED)
        selected: _SelectedLog | None = None
        if not exchange.succeeded:
            reason = _http_reason(exchange.reason)
        else:
            try:
                selected = _select_log(
                    exchange.raw_body,
                    service_field=fixture.service_identity_field,
                    service=fixture.service_identity,
                    timestamp_field=fixture.timestamp_field,
                    trace_id_field=fixture.trace_id_field,
                    span_id_field=fixture.span_id_field,
                    window=window,
                )
            except _OpenSearchFailure as failure:
                reason = failure.reason
            else:
                reason = OpenSearchReason.READY
        if not self._persist_decision(
            raw_path,
            reason=reason,
            selected=selected,
            window=window,
            paths=paths,
        ):
            return self._result(
                reason=OpenSearchReason.EVIDENCE_PERSISTENCE_FAILED,
                artifact_paths=tuple(paths),
            )
        return self._result(
            reason=reason,
            run_id=window.run_id,
            cycle_number=window.cycle_number,
            phase=window.scenario_phase.value,
            fixture_sha256=self._loaded.content_sha256,
            log_id=selected.log_id if selected is not None else None,
            trace_id=selected.trace_id if selected is not None else None,
            span_id=selected.span_id if selected is not None else None,
            observed_at=selected.observed_at if selected is not None else None,
            artifact_paths=tuple(paths),
            artifact_sha256=_hash_existing_artifacts(paths),
        )

    def _persist_raw(
        self,
        path: str,
        exchange: HttpExchange,
        *,
        window: PhaseWindow,
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
            "backend": "opensearch",
            "exact_request": exchange.request.target,
            "request_body_base64": base64.b64encode(exchange.request.body).decode(
                "ascii"
            ),
            "request_body_sha256": sha256_bytes(exchange.request.body),
            "started_at": exchange.started_at.isoformat(),
            "ended_at": exchange.ended_at.isoformat(),
            "monotonic_started_at": exchange.monotonic_started_at,
            "monotonic_ended_at": exchange.monotonic_ended_at,
            "http_status": exchange.status_code,
            "http_reason": exchange.reason.value,
            "raw_response_base64": base64.b64encode(exchange.raw_body).decode("ascii"),
            "raw_response_sha256": exchange.raw_sha256,
            "raw_response_partial": exchange.raw_body_partial,
        }
        return _write(self._store, path, payload, paths)

    def _persist_decision(
        self,
        raw_path: str,
        *,
        reason: OpenSearchReason,
        selected: _SelectedLog | None,
        window: PhaseWindow,
        paths: list[str],
    ) -> bool:
        payload = {
            "schema_version": "phase0.telemetry-gate-decision.v1",
            "run_id": window.run_id,
            "cycle_number": window.cycle_number,
            "scenario_phase": window.scenario_phase.value,
            "fixture_version": self._loaded.registry.fixture_version,
            "fixture_sha256": self._loaded.content_sha256,
            "backend": "opensearch",
            "raw_response_artifact": raw_path,
            "decision": reason is OpenSearchReason.READY,
            "reason": reason.value,
            "parsed_service_identity": (
                self._loaded.registry.opensearch.service_identity
                if selected is not None
                else None
            ),
            "parsed_log_id": selected.log_id if selected is not None else None,
            "parsed_trace_id": selected.trace_id if selected is not None else None,
            "parsed_span_id": selected.span_id if selected is not None else None,
            "parsed_timestamp": (
                selected.observed_at.isoformat() if selected is not None else None
            ),
        }
        decision_path = raw_path.removesuffix("raw.json") + "decision.json"
        return _write(self._store, decision_path, payload, paths)


class _OpenSearchFailure(RuntimeError):
    def __init__(self, reason: OpenSearchReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _select_log(
    body: bytes,
    *,
    service_field: str,
    service: str,
    timestamp_field: str,
    trace_id_field: str | None,
    span_id_field: str | None,
    window: PhaseWindow,
) -> _SelectedLog:
    try:
        payload = json.loads(body, object_pairs_hook=_reject_duplicates)
        hits = payload["hits"]["hits"]
        if not isinstance(hits, list):
            raise TypeError
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise _OpenSearchFailure(OpenSearchReason.OPENSEARCH_SCHEMA_INVALID) from None

    identity_seen = False
    stale_seen = False
    try:
        for hit in hits:
            source = hit["_source"]
            log_id = hit["_id"]
            if (
                not isinstance(source, dict)
                or not isinstance(log_id, str)
                or not log_id
            ):
                raise TypeError
            identity = _nested(source, service_field)
            if identity != service:
                continue
            identity_seen = True
            raw_timestamp = _nested(source, timestamp_field)
            if not isinstance(raw_timestamp, str):
                raise TypeError
            observed_at = _parse_timestamp(raw_timestamp)
            if not window.contains_observation(observed_at):
                stale_seen = True
                continue
            trace_id = _optional_string(source, trace_id_field)
            span_id = _optional_string(source, span_id_field)
            return _SelectedLog(
                log_id=log_id,
                observed_at=observed_at,
                trace_id=trace_id,
                span_id=span_id,
            )
    except (KeyError, TypeError, ValueError):
        raise _OpenSearchFailure(OpenSearchReason.OPENSEARCH_SCHEMA_INVALID) from None
    if identity_seen and stale_seen:
        raise _OpenSearchFailure(OpenSearchReason.OPENSEARCH_STALE_LOG)
    raise _OpenSearchFailure(OpenSearchReason.OPENSEARCH_IDENTITY_MISMATCH)


def _nested(payload: dict[str, Any], field: str) -> Any:
    if field in payload:
        return payload[field]
    current: Any = payload
    for component in field.split("."):
        if not isinstance(current, dict) or component not in current:
            raise KeyError(field)
        current = current[component]
    return current


def _optional_string(payload: dict[str, Any], field: str | None) -> str | None:
    if field is None:
        return None
    try:
        value = _nested(payload, field)
    except KeyError:
        return None
    if not isinstance(value, str):
        raise TypeError
    return value


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("OpenSearch timestamp is not UTC")
    return parsed.astimezone(UTC)


def _http_reason(reason: HttpReason) -> OpenSearchReason:
    try:
        return OpenSearchReason(reason.value)
    except ValueError:
        return OpenSearchReason.HTTP_TRANSPORT_ERROR


def _hash_existing_artifacts(paths: list[str]) -> tuple[tuple[str, str], ...]:
    try:
        return tuple((path, sha256_file(Path(path))) for path in paths)
    except (OSError, ValueError):
        return ()


def _write(
    store: _EvidenceStore,
    path: str,
    payload: dict[str, Any],
    paths: list[str],
) -> bool:
    try:
        artifact = store.write_immutable(path, payload)
    except (OSError, RuntimeError, ValueError):
        return False
    paths.append(str(artifact.path))
    return True


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
