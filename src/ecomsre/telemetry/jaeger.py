"""Exact-identity, phase-local Jaeger readiness gate."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from ecomsre.evidence.hashes import sha256_file
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

_JAEGER_RECEIPT_TOKEN = object()


class JaegerReason(str, Enum):
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
    JAEGER_SCHEMA_INVALID = "JAEGER_SCHEMA_INVALID"
    JAEGER_IDENTITY_MISMATCH = "JAEGER_IDENTITY_MISMATCH"
    JAEGER_STALE_TRACE = "JAEGER_STALE_TRACE"


@dataclass(frozen=True)
class JaegerReadiness:
    reason: JaegerReason
    run_id: str | None = None
    cycle_number: int | None = None
    phase: str | None = None
    fixture_sha256: str | None = None
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
        return self.reason is JaegerReason.READY

    def is_production_receipt(
        self,
        *,
        capability: FrozenTelemetryQueryCapability,
        store: ObserverEvidenceStore,
        window: PhaseWindow,
    ) -> bool:
        return (
            self._receipt_token is _JAEGER_RECEIPT_TOKEN
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
class _SelectedSpan:
    trace_id: str
    span_id: str
    started_at: datetime


class JaegerAdapter:
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

    def _result(self, **values: Any) -> JaegerReadiness:
        production = (
            isinstance(self._loaded, FrozenTelemetryQueryCapability)
            and type(self._client) is OwnedHttpClient
            and _owned_http_client_has_production_integrity(self._client)
            and isinstance(self._store, ObserverEvidenceStore)
            and self._loaded.store is self._store
        )
        return JaegerReadiness(
            **values,
            _receipt_token=_JAEGER_RECEIPT_TOKEN,
            _production_receipt=production,
            _store_root=str(self._store.root) if production else None,
        )

    def check_readiness(
        self,
        *,
        window: PhaseWindow,
        base_url: str,
        artifact_prefix: str,
    ) -> JaegerReadiness:
        registry = self._loaded.registry
        fixture = registry.jaeger
        if (
            not _registry_access_is_frozen_for_adapter(
                self._loaded,
                run_id=window.run_id,
                evidence_store=self._store,
                client=self._client,
            )
            or fixture.state is not FixtureState.FROZEN
        ):
            return self._result(reason=JaegerReason.QUERY_FIXTURE_NOT_FROZEN)
        if self._client.run_id != window.run_id:
            return self._result(reason=JaegerReason.RESOURCE_OWNERSHIP_UNKNOWN)
        assert fixture.service_identity is not None
        assert fixture.operation is not None
        query = urlencode(
            {
                "service": fixture.service_identity,
                "operation": fixture.operation,
                "start": _epoch_microseconds(window.utc_started_at),
                "end": _epoch_microseconds(window.utc_ended_at),
                "limit": 100,
            }
        )
        request = HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_url,
                service=fixture.target.service,
                target_port=fixture.target.target_port,
                protocol=fixture.target.protocol,
            ),
            method="GET",
            target=f"/api/traces?{query}",
            absolute_deadline_monotonic=window.monotonic_ended_at,
        )
        exchange = self._client.request(request)
        raw_path = f"{artifact_prefix}/telemetry/jaeger/readiness-raw.json"
        paths: list[str] = []
        if not self._persist_raw(
            raw_path,
            exchange,
            window=window,
            paths=paths,
        ):
            return self._result(reason=JaegerReason.EVIDENCE_PERSISTENCE_FAILED)
        selected: _SelectedSpan | None = None
        if not exchange.succeeded:
            reason = _http_reason(exchange.reason)
        else:
            try:
                selected = _select_span(
                    exchange.raw_body,
                    service=fixture.service_identity,
                    operation=fixture.operation,
                    window=window,
                )
            except _JaegerFailure as failure:
                reason = failure.reason
            else:
                reason = JaegerReason.READY
        if not self._persist_decision(
            raw_path,
            reason=reason,
            selected=selected,
            window=window,
            paths=paths,
        ):
            return self._result(
                reason=JaegerReason.EVIDENCE_PERSISTENCE_FAILED,
                artifact_paths=tuple(paths),
            )
        return self._result(
            reason=reason,
            run_id=window.run_id,
            cycle_number=window.cycle_number,
            phase=window.scenario_phase.value,
            fixture_sha256=self._loaded.content_sha256,
            trace_id=selected.trace_id if selected is not None else None,
            span_id=selected.span_id if selected is not None else None,
            observed_at=selected.started_at if selected is not None else None,
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
            "backend": "jaeger",
            "exact_request": exchange.request.target,
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
        reason: JaegerReason,
        selected: _SelectedSpan | None,
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
            "backend": "jaeger",
            "raw_response_artifact": raw_path,
            "decision": reason is JaegerReason.READY,
            "reason": reason.value,
            "parsed_service_identity": (
                self._loaded.registry.jaeger.service_identity
                if selected is not None
                else None
            ),
            "parsed_trace_id": selected.trace_id if selected is not None else None,
            "parsed_span_id": selected.span_id if selected is not None else None,
            "parsed_timestamp": (
                selected.started_at.isoformat() if selected is not None else None
            ),
        }
        decision_path = raw_path.removesuffix("raw.json") + "decision.json"
        return _write(self._store, decision_path, payload, paths)


class _JaegerFailure(RuntimeError):
    def __init__(self, reason: JaegerReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _select_span(
    body: bytes,
    *,
    service: str,
    operation: str,
    window: PhaseWindow,
) -> _SelectedSpan:
    try:
        payload = json.loads(body, object_pairs_hook=_reject_duplicates)
        traces = payload["data"]
        if not isinstance(payload, dict) or not isinstance(traces, list):
            raise TypeError
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise _JaegerFailure(JaegerReason.JAEGER_SCHEMA_INVALID) from None

    exact_identity_seen = False
    stale_seen = False
    try:
        for trace in traces:
            trace_id = trace["traceID"]
            spans = trace["spans"]
            processes = trace["processes"]
            if (
                not isinstance(trace_id, str)
                or not trace_id
                or not isinstance(spans, list)
                or not isinstance(processes, dict)
            ):
                raise TypeError
            for span in spans:
                process = processes.get(span["processID"])
                if (
                    not isinstance(process, dict)
                    or not isinstance(process.get("serviceName"), str)
                    or not isinstance(span.get("operationName"), str)
                ):
                    raise TypeError
                if (
                    process["serviceName"] != service
                    or span["operationName"] != operation
                ):
                    continue
                exact_identity_seen = True
                span_id = span["spanID"]
                start_us = span["startTime"]
                duration_us = span["duration"]
                if (
                    not isinstance(span_id, str)
                    or not span_id
                    or not isinstance(start_us, int)
                    or not isinstance(duration_us, int)
                    or duration_us < 0
                ):
                    raise TypeError
                started_at = datetime.fromtimestamp(start_us / 1_000_000, tz=UTC)
                ended_at = started_at + timedelta(microseconds=duration_us)
                if window.contains_observation(
                    started_at
                ) and window.contains_observation(ended_at):
                    return _SelectedSpan(
                        trace_id=trace_id,
                        span_id=span_id,
                        started_at=started_at,
                    )
                stale_seen = True
    except (KeyError, OSError, OverflowError, TypeError, ValueError):
        raise _JaegerFailure(JaegerReason.JAEGER_SCHEMA_INVALID) from None
    if exact_identity_seen and stale_seen:
        raise _JaegerFailure(JaegerReason.JAEGER_STALE_TRACE)
    raise _JaegerFailure(JaegerReason.JAEGER_IDENTITY_MISMATCH)


def _epoch_microseconds(timestamp: datetime) -> int:
    return int(timestamp.timestamp() * 1_000_000)


def _http_reason(reason: HttpReason) -> JaegerReason:
    try:
        return JaegerReason(reason.value)
    except ValueError:
        return JaegerReason.HTTP_TRANSPORT_ERROR


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
