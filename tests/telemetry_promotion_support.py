"""Strict test-only promotion evidence builder."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_bytes,
)
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.telemetry.prometheus import (
    TestTelemetryQueryCapability,
    _issue_test_query_capability,
    validate_frozen_query_registry,
)

_PROMOTION_START = datetime(2026, 7, 30, 1, 2, 0, tzinfo=UTC)


def issue_strict_frozen_test_capability(
    store: ObserverEvidenceStore,
    *,
    run_id: str,
    fixture_path: Path,
    artifact_mutator: Callable[[dict[str, dict[str, Any]]], None] | None = None,
) -> tuple[dict[str, Any], TestTelemetryQueryCapability]:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    proof = payload["promotion_proof"]
    old_run = proof["current_run_id"]
    old_prefix = f"observer-visible/{old_run}/"
    prefix = f"observer-visible/{run_id}/"
    proof["current_run_id"] = run_id
    for field in ("raw_artifacts", "emitted_identity_artifacts"):
        proof[field] = [path.replace(old_prefix, prefix) for path in proof[field]]
    for field in (
        "counter_mapping_artifact",
        "probe_getads_attribution_artifact",
        "review_artifact",
    ):
        proof[field] = proof[field].replace(old_prefix, prefix)
    payload["probe"]["getads_proof_artifact"] = proof[
        "probe_getads_attribution_artifact"
    ]
    contract = dict(payload)
    contract.pop("promotion_proof")
    proof["fixture_content_sha256"] = canonical_json_sha256(contract)

    raw_path = proof["raw_artifacts"][0]
    identity_path = proof["emitted_identity_artifacts"][0]
    counter_path = proof["counter_mapping_artifact"]
    attribution_path = proof["probe_getads_attribution_artifact"]
    review_path = proof["review_artifact"]
    exchange_purposes = (
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
    exchange_paths = {
        purpose: (
            f"{prefix}telemetry/promotion/raw-exchanges/{sequence:02d}-{purpose}.json"
        )
        for sequence, purpose in enumerate(exchange_purposes, start=1)
    }
    proof["raw_artifacts"] = [raw_path, *exchange_paths.values()]
    common = {
        "run_id": run_id,
        "fixture_version": payload["fixture_version"],
    }
    probe_body = (fixture_path.parent / "probe-current.json").read_bytes()
    jaeger_body = (fixture_path.parent / "jaeger-current.json").read_bytes()
    opensearch_body = (fixture_path.parent / "opensearch-current.json").read_bytes()
    promotion_started = _PROMOTION_START
    promotion_ended = datetime(2026, 7, 30, 1, 4, 0, tzinfo=UTC)
    timestamp = (promotion_started.replace(second=3)).timestamp()
    prom = payload["prometheus"]
    total_body = _vector(
        timestamp,
        [(series["labels"], "10") for series in prom["expected_total_series"]],
    )
    error_body = _vector(
        timestamp,
        [
            (
                next(
                    series["labels"]
                    for series in prom["expected_total_series"]
                    if series["labels"][prom["error_classification"]["label"]]
                    in prom["error_classification"]["values"]
                ),
                "0",
            )
        ],
    )
    incarnation_body = _vector(
        timestamp,
        [(prom["expected_target_incarnation_series"]["labels"], "1000")],
    )
    backend_observations = [
        _observation(
            "prometheus",
            "total",
            prom["total_query"],
            prom["expected_response_schema"],
            total_body,
            1.0,
        ),
        _observation(
            "prometheus",
            "error",
            prom["error_query"],
            prom["expected_response_schema"],
            error_body,
            2.0,
        ),
        _observation(
            "prometheus",
            "target_incarnation",
            prom["target_incarnation_query"],
            prom["expected_response_schema"],
            incarnation_body,
            3.0,
        ),
        _observation(
            "jaeger",
            "readiness",
            payload["jaeger"]["request_template"],
            payload["jaeger"]["expected_response_schema"],
            jaeger_body,
            4.0,
        ),
        _observation(
            "opensearch",
            "readiness",
            payload["opensearch"]["request_template"],
            payload["opensearch"]["expected_response_schema"],
            opensearch_body,
            5.0,
        ),
        _observation(
            "probe",
            "business_path",
            f"{payload['probe']['method']} {payload['probe']['path']}",
            "otel-demo.frontend.api.data.Ad[].v3.0.0",
            probe_body,
            10.0,
        ),
    ]
    phase_observations = []
    correlation_bodies: dict[str, bytes] = {}
    traceparents: dict[str, str] = {}
    for index, (phase, offset) in enumerate(
        zip(
            payload["probe"]["required_phases"],
            (0.0, 40.0, 80.0),
            strict=True,
        ),
        start=1,
    ):
        trace_id = f"{index:032x}"
        traceparent = f"00-{trace_id}-{index:016x}-01"
        traceparents[phase] = traceparent
        correlation_body = _correlated_jaeger_body(
            jaeger_body,
            trace_id=trace_id,
            offset=offset,
        )
        correlation_bodies[phase] = correlation_body
        probe_exchange = _promotion_exchange_payload(
            run_id=run_id,
            fixture_version=payload["fixture_version"],
            sequence=6 + (index - 1) * 2,
            purpose=f"probe-{phase}",
            method=payload["probe"]["method"],
            target=payload["probe"]["path"],
            headers=(("traceparent", traceparent),),
            body=probe_body,
            monotonic_started_at=offset + 10.0,
        )
        jaeger_exchange = _promotion_exchange_payload(
            run_id=run_id,
            fixture_version=payload["fixture_version"],
            sequence=7 + (index - 1) * 2,
            purpose=f"jaeger-correlation-{phase}",
            method="GET",
            target=f"/api/traces/{trace_id}",
            body=correlation_body,
            monotonic_started_at=offset + 11.0,
        )
        probe_artifact_sha256 = canonical_json_sha256(probe_exchange)
        jaeger_artifact_sha256 = canonical_json_sha256(jaeger_exchange)
        phase_observations.append(
            {
                "phase": phase,
                "cycle_number": 1,
                "phase_started_at": (
                    promotion_started + timedelta(seconds=offset)
                ).isoformat(),
                "phase_ended_at": (
                    promotion_started + timedelta(seconds=offset + 30)
                ).isoformat(),
                "phase_monotonic_started_at": offset,
                "phase_monotonic_ended_at": offset + 30,
                "fixed_input": payload["probe"]["input"],
                "observer_input_boundary_passed": True,
                "unexpected_input_count": 0,
                "trace_id": trace_id,
                "traceparent_sha256": sha256_bytes(traceparent.encode()),
                "probe_raw_artifact": exchange_paths[f"probe-{phase}"],
                "probe_raw_sha256": probe_artifact_sha256,
                "jaeger_request": f"/api/traces/{trace_id}",
                "jaeger_raw_artifact": exchange_paths[f"jaeger-correlation-{phase}"],
                "jaeger_raw_sha256": jaeger_artifact_sha256,
                "jaeger_http_status": 200,
                "jaeger_request_started_at": (
                    promotion_started + timedelta(seconds=offset + 11.0)
                ).isoformat(),
                "jaeger_response_ended_at": (
                    promotion_started + timedelta(seconds=offset + 11.5)
                ).isoformat(),
                "jaeger_monotonic_started_at": offset + 11.0,
                "jaeger_monotonic_ended_at": offset + 11.5,
                "jaeger_raw_response_base64": base64.b64encode(correlation_body).decode(
                    "ascii"
                ),
                "jaeger_raw_response_sha256": sha256_bytes(correlation_body),
                "getads_span_proven": True,
                **_raw_body(probe_body, offset + 10.0),
            }
        )
    backend_exchange_specs = (
        (
            "prometheus-total",
            "GET",
            f"/api/v1/query?query={quote(prom['total_query'], safe='')}",
            (),
            total_body,
            1.0,
        ),
        (
            "prometheus-error",
            "GET",
            f"/api/v1/query?query={quote(prom['error_query'], safe='')}",
            (),
            error_body,
            2.0,
        ),
        (
            "prometheus-target-incarnation",
            "GET",
            (f"/api/v1/query?query={quote(prom['target_incarnation_query'], safe='')}"),
            (),
            incarnation_body,
            3.0,
        ),
        (
            "jaeger-readiness",
            "GET",
            "/api/traces?"
            + urlencode(
                {
                    "service": payload["jaeger"]["service_identity"],
                    "operation": payload["jaeger"]["operation"],
                    "start": int(promotion_started.timestamp() * 1_000_000),
                    "end": int(
                        (promotion_started + timedelta(seconds=30)).timestamp()
                        * 1_000_000
                    ),
                    "limit": 100,
                }
            ),
            (),
            jaeger_body,
            4.0,
        ),
        (
            "opensearch-readiness",
            "POST",
            "/otel-logs-%2A/_search",
            (("Content-Type", "application/json"),),
            opensearch_body,
            5.0,
        ),
    )
    exchange_artifacts = {
        exchange_paths[purpose]: _promotion_exchange_payload(
            run_id=run_id,
            fixture_version=payload["fixture_version"],
            sequence=sequence,
            purpose=purpose,
            method=method,
            target=target,
            headers=headers,
            body=body,
            monotonic_started_at=started,
        )
        for sequence, (
            purpose,
            method,
            target,
            headers,
            body,
            started,
        ) in enumerate(backend_exchange_specs, start=1)
    }
    for index, phase in enumerate(payload["probe"]["required_phases"], start=1):
        probe_purpose = f"probe-{phase}"
        correlation_purpose = f"jaeger-correlation-{phase}"
        exchange_artifacts[exchange_paths[probe_purpose]] = _promotion_exchange_payload(
            run_id=run_id,
            fixture_version=payload["fixture_version"],
            sequence=6 + (index - 1) * 2,
            purpose=probe_purpose,
            method=payload["probe"]["method"],
            target=payload["probe"]["path"],
            headers=(("traceparent", traceparents[phase]),),
            body=probe_body,
            monotonic_started_at=(index - 1) * 40.0 + 10.0,
        )
        exchange_artifacts[exchange_paths[correlation_purpose]] = (
            _promotion_exchange_payload(
                run_id=run_id,
                fixture_version=payload["fixture_version"],
                sequence=7 + (index - 1) * 2,
                purpose=correlation_purpose,
                method="GET",
                target=f"/api/traces/{index:032x}",
                body=correlation_bodies[phase],
                monotonic_started_at=(index - 1) * 40.0 + 11.0,
            )
        )
    artifact_payloads = {
        raw_path: {
            **common,
            "schema_version": "phase0.telemetry-promotion-raw.v1",
            "upstream_tag": payload["upstream_tag"],
            "upstream_commit": payload["upstream_commit"],
            "upstream_sha256": proof["upstream_sha256"],
            "compose_config_sha256": payload["compose_config_sha256"],
            "promotion_started_at": promotion_started.isoformat(),
            "promotion_ended_at": promotion_ended.isoformat(),
            "promotion_monotonic_started_at": 0.0,
            "promotion_monotonic_ended_at": 120.0,
            "backend_window_started_at": promotion_started.isoformat(),
            "backend_window_ended_at": (
                promotion_started + timedelta(seconds=30)
            ).isoformat(),
            "backend_monotonic_started_at": 0.0,
            "backend_monotonic_ended_at": 30.0,
            "backend_observations": backend_observations,
            "probe_phase_observations": phase_observations,
        },
        identity_path: {
            **common,
            "schema_version": "phase0.telemetry-emitted-identities.v1",
            "bindings": _identity_bindings(payload),
        },
        counter_path: {
            **common,
            "schema_version": "phase0.prometheus-counter-contract.v1",
            "contract": _prometheus_contract(payload),
        },
        attribution_path: {
            **common,
            "schema_version": "phase0.probe-getads-attribution.v1",
            "probe_path": payload["probe"]["path"],
            "operation": prom["operation"],
            "attribution_proven": True,
            "fixed_input": payload["probe"]["input"],
            "response_contract": payload["probe"]["response_contract"],
            "observer_input_boundary_required": payload["probe"][
                "hidden_input_denial_required"
            ],
            "required_phases": payload["probe"]["required_phases"],
            "phase_correlations": {
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
            },
        },
        **exchange_artifacts,
    }
    if artifact_mutator is not None:
        artifact_mutator(artifact_payloads)
    hashes = {}
    for absolute_path, artifact_payload in artifact_payloads.items():
        artifact = store.write_immutable(
            absolute_path.removeprefix(prefix),
            artifact_payload,
        )
        hashes[absolute_path] = artifact.sha256
    review_payload = {
        **common,
        "schema_version": "phase0.telemetry-promotion-review.v1",
        "decision": "APPROVED",
        "fixture_content_sha256": proof["fixture_content_sha256"],
        "upstream_sha256": proof["upstream_sha256"],
        "compose_config_sha256": proof["compose_config_sha256"],
        "reviewed_artifact_sha256": dict(hashes),
    }
    review_artifact = store.write_immutable(
        review_path.removeprefix(prefix),
        review_payload,
    )
    hashes[review_path] = review_artifact.sha256
    proof["artifact_sha256"] = hashes
    audit = validate_frozen_query_registry(payload, store)
    if not audit.valid:
        raise ValueError(audit.reason or "frozen promotion audit failed")
    capability = _issue_test_query_capability(payload)
    return payload, capability


def _vector(
    timestamp: float,
    values: list[tuple[dict[str, str], str]],
) -> bytes:
    return canonical_json_bytes(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": labels,
                        "value": [timestamp, value],
                    }
                    for labels, value in values
                ],
            },
        }
    )


def _observation(
    backend: str,
    query_kind: str,
    request: str,
    response_schema: str,
    body: bytes,
    monotonic_started_at: float,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "query_kind": query_kind,
        "request": request,
        "response_schema": response_schema,
        **_raw_body(body, monotonic_started_at),
    }


def _raw_body(body: bytes, monotonic_started_at: float) -> dict[str, Any]:
    request_started = _PROMOTION_START + timedelta(seconds=monotonic_started_at)
    return {
        "http_status": 200,
        "request_started_at": request_started.isoformat(),
        "response_ended_at": (request_started + timedelta(seconds=0.5)).isoformat(),
        "monotonic_started_at": monotonic_started_at,
        "monotonic_ended_at": monotonic_started_at + 0.5,
        "raw_response_base64": base64.b64encode(body).decode("ascii"),
        "raw_response_sha256": sha256_bytes(body),
    }


def _promotion_exchange_payload(
    *,
    run_id: str,
    fixture_version: str,
    sequence: int,
    purpose: str,
    method: str,
    target: str,
    body: bytes,
    monotonic_started_at: float,
    headers: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    request_started = _PROMOTION_START + timedelta(seconds=monotonic_started_at)
    return {
        "schema_version": "phase0.telemetry-promotion-exchange.v1",
        "run_id": run_id,
        "fixture_version": fixture_version,
        "sequence": sequence,
        "purpose": purpose,
        "request": {
            "method": method,
            "target": target,
            "headers": [list(item) for item in headers],
            "body_sha256": sha256_bytes(b"" if method == "GET" else b""),
        },
        "request_started_at": request_started.isoformat(),
        "response_ended_at": (request_started + timedelta(seconds=0.5)).isoformat(),
        "monotonic_started_at": monotonic_started_at,
        "monotonic_ended_at": monotonic_started_at + 0.5,
        "http_status": 200,
        "transport_reason": "OK",
        "terminal_failure": False,
        "response_headers": [["Content-Type", "application/json"]],
        "raw_response_base64": base64.b64encode(body).decode("ascii"),
        "raw_response_sha256": sha256_bytes(body),
        "raw_response_partial": False,
    }


def _correlated_jaeger_body(
    template: bytes,
    *,
    trace_id: str,
    offset: float,
) -> bytes:
    payload = json.loads(template)
    trace = payload["data"][0]
    trace["traceID"] = trace_id
    span = trace["spans"][0]
    span["traceID"] = trace_id
    span["startTime"] = int(
        (_PROMOTION_START + timedelta(seconds=offset + 11.1)).timestamp() * 1_000_000
    )
    return canonical_json_bytes(payload)


def _identity_bindings(payload: dict[str, Any]) -> dict[str, Any]:
    prometheus = payload["prometheus"]
    return {
        "prometheus": {
            "applicable_service": prometheus["applicable_service"],
            "metric": prometheus["candidate_metric"],
            "operation": prometheus["operation"],
            "total_series": prometheus["expected_total_series"],
            "error_classification": prometheus["error_classification"],
            "target_incarnation_series": prometheus[
                "expected_target_incarnation_series"
            ],
        },
        "jaeger": {
            "service_identity": payload["jaeger"]["service_identity"],
            "operation": payload["jaeger"]["operation"],
        },
        "opensearch": {
            key: payload["opensearch"][key]
            for key in (
                "index",
                "service_identity_field",
                "service_identity",
                "timestamp_field",
                "trace_id_field",
                "span_id_field",
            )
        },
        "probe": {
            "target": payload["probe"]["target"],
            "method": payload["probe"]["method"],
            "path": payload["probe"]["path"],
            "input": payload["probe"]["input"],
            "response_contract": payload["probe"]["response_contract"],
        },
    }


def _prometheus_contract(payload: dict[str, Any]) -> dict[str, Any]:
    fixture = payload["prometheus"]
    return {
        "request_template": fixture["request_template"],
        "expected_response_schema": fixture["expected_response_schema"],
        "total_query": fixture["total_query"],
        "error_query": fixture["error_query"],
        "target_incarnation_query": fixture["target_incarnation_query"],
        "counter_identity_labels": fixture["counter_identity_labels"],
        "boundary_rule": fixture["boundary_rule"],
        "cardinality_rule": fixture["cardinality_rule"],
        "reset_policy": fixture["reset_policy"],
        "staleness_policy": fixture["staleness_policy"],
        "zero_series_rule": fixture["zero_series_rule"],
        "scrape_interval_seconds": fixture["scrape_interval_seconds"],
        "scrape_interval_tolerance_seconds": fixture[
            "scrape_interval_tolerance_seconds"
        ],
        "maximum_scrape_lag_seconds": fixture["maximum_scrape_lag_seconds"],
    }
