"""Goal-scoped Product configuration for live knowledge evolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    build_product_v023_environment_payload,
)
from ecomsre.product.contracts import EnvironmentCreateV1


QUEUE_LAG_QUERY_V030 = (
    'sum(kafka_consumer_group_lag_ratio{group="{service}",topic="orders"})'
)
CANDIDATES_V030 = ("checkout", "fraud-detection", "kafka", "payment")


def build_product_v030_environment_payload(
    *,
    repository_root: Path,
    runtime_authority_sha256: str,
) -> dict[str, Any]:
    payload = build_product_v023_environment_payload(
        repository_root=repository_root,
        runtime_authority_sha256=runtime_authority_sha256,
    )
    by_kind = {item["kind"]: item for item in payload["connector_configs"]}
    prometheus = by_kind["PROMETHEUS"]
    prometheus["settings"]["query_templates"]["queue_lag"] = QUEUE_LAG_QUERY_V030
    prometheus["settings"]["step_seconds"] = 10
    templates = prometheus["settings"]["query_templates"]
    # Native latency is the broker's Produce request-time histogram quantile.
    # Count/failed are partition-append counters (failed excludes expected errors),
    # not a complete network ACK success ratio or the latency sample population.
    # Method spans are execution evidence, never a broker latency substitute.
    native = {
        "request_support": 'sum(rate(kafka_request_count_total{service_name="{service}",type="produce"}[5m]))',
        "error_rate": 'sum(rate(kafka_request_failed_total{service_name="{service}",type="produce"}[5m])) / clamp_min(sum(rate(kafka_request_count_total{service_name="{service}",type="produce"}[5m])), 0.000001)',
        "latency": 'sum(kafka_produce_request_time_95p_milliseconds{service_name="{service}"})',
    }
    for name, query in native.items():
        fallback = templates[name].replace(
            'service_name="{service}"', 'service_name="{service}",service_name!="kafka"'
        )
        templates[name] = f"({query}) or ({fallback})"
    # The frozen P01 profile intentionally preserves AS_OBSERVED bytes. This
    # Goal needs the already-supported observer projection without altering it.
    opensearch = by_kind["OPENSEARCH"]
    opensearch["settings"] = {
        "mode": "LEGACY_EXPLICIT_FIELDS",
        "index_pattern": "otel-logs-*",
        "timestamp_field": "@timestamp",
        "service_field": "resource.service.name",
        "service_query_field": "resource.service.name.keyword",
        "severity_field": "severity.text",
        "message_field": "body",
        "trace_id_field": "traceId",
        "message_projection_policy": "OBSERVER_SYMPTOM_V1",
        "maximum_result_count": 200,
        "maximum_response_bytes": 10_000_000,
    }
    # The frontend HTTP check is not a Runtime probe for these candidates.
    payload["connector_configs"] = [
        item for item in payload["connector_configs"] if item["kind"] != "HTTP_HEALTH"
    ]
    payload["name"] = "product-v030-live-knowledge-evolution"
    payload["description"] = (
        "Read-only full-mode environment for live unknown-fault knowledge evolution."
    )
    payload["explicit_service_catalog"] = list(CANDIDATES_V030)
    payload["service_identity_policy"] = {
        "discovery_mode": "DECLARED_ONLY",
        "services": [
            {
                "logical_service": service,
                "aliases": {
                    "prometheus": [service],
                    "opensearch": [service],
                    "jaeger": [service],
                    "http_health": [service] if service == "checkout" else [],
                },
                "approved_many_to_one": False,
            }
            for service in CANDIDATES_V030
        ],
    }
    return EnvironmentCreateV1.model_validate(payload).model_dump(mode="json")
