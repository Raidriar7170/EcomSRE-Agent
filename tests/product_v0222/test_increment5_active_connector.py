from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    ReadSourceStatusV22,
)
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorQueryContextV1,
    ConnectorWindowV1,
    ConnectorHealthResultV1,
    ConnectorQueryResultV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OpenSearchNormalizationProfileV0222,
    OpenSearchProfileStatusV0222,
)
from ecomsre.product.connectors.opensearch_smoke_v0222 import (
    CONNECTOR_SMOKE_PASS_V0222,
    build_connector_smoke_profile_v0222,
    evaluate_connector_smoke_v0222,
)
from ecomsre.product.contracts import ConnectorConfigV1
from scripts.product_v0222.prove_active_profile_restart import (
    run_active_profile_restart_proof_v0222,
)
from scripts.product_v0222.run_connector_smoke import (
    build_service_identity_binding_v0222,
)


ROOT = Path(__file__).resolve().parents[2]


def test_active_profile_connector_reads_nested_dotted_keys() -> None:
    profile = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            ROOT / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    assert profile.profile_status is OpenSearchProfileStatusV0222.ACTIVE

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["size"] == 0:
            return httpx.Response(
                200,
                json={
                    "hits": {"hits": []},
                    "aggregations": {
                        "services": {
                            "buckets": [
                                {"key": "checkoutservice", "doc_count": 1}
                            ]
                        }
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "hits": {
                    "total": {"value": 1},
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-08-29T06:00:05Z",
                                "resource": {"service.name": "checkoutservice"},
                                "severity": {"text": "INFO"},
                                "body": "healthy checkout log",
                                "traceId": "a" * 32,
                            }
                        }
                    ],
                }
            },
        )

    connector = OpenSearchConnectorV1(
        ConnectorConfigV1(
            name="logs",
            kind="OPENSEARCH",
            endpoint="http://127.0.0.1:19200",
            settings={
                "index_pattern": profile.index_pattern,
                "timestamp_field": profile.timestamp_extraction.extraction.paths[0],
                "service_field": profile.service_source_field,
                "service_query_field": profile.service_query_field,
                "severity_field": profile.severity_extraction.extraction.paths[0],
                "message_field": profile.message_extraction.extraction.paths[0],
                "trace_id_field": profile.trace_id_extraction.paths[0],
                "message_projection_policy": profile.message_projection_policy,
                "maximum_result_count": 5,
                "maximum_response_bytes": 2_000_000,
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    context = ConnectorQueryContextV1(
        environment_id="env-" + "0" * 24,
        requested_services=("checkout",),
        service_aliases={"checkout": "checkout", "checkoutservice": "checkout"},
        window=ConnectorWindowV1(
            started_at=datetime(2026, 8, 29, 6, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 29, 6, 0, 10, tzinfo=UTC),
        ),
        maximum_records=5,
    )

    assert connector.verify().status is ConnectorAvailabilityV1.AVAILABLE
    result = connector.query(context)[0]

    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert len(result.records) == 1
    assert result.records[0].service == "checkout"
    assert result.records[0].message == "healthy checkout log"


def test_three_window_smoke_report_binds_restart_and_diagnostics() -> None:
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            ROOT / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    smoke_profile = build_connector_smoke_profile_v0222(active_profile=active)
    windows = tuple(
        ConnectorWindowV1(
            started_at=datetime(2026, 8, 29, 6, 0, ordinal * 10, tzinfo=UTC),
            ended_at=datetime(
                2026,
                8,
                29,
                6,
                0,
                ordinal * 10 + 9,
                tzinfo=UTC,
            ),
        )
        for ordinal in range(3)
    )
    results = (
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.LOGS,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            requested_services=("checkout",),
            covered_services=("checkout",),
            window=windows[0],
            records=(
                LogRecordV22(
                    schema_version="dta-v22.log-record.v1",
                    observed_at=datetime(2026, 8, 29, 6, 0, 5, tzinfo=UTC),
                    service="checkout",
                    severity="DIAGNOSTIC",
                    message="healthy checkout log",
                ),
            ),
            truncated=False,
            safe_error_code=None,
            latency_ms=1,
        ),
        *tuple(
            ConnectorQueryResultV1.build(
                source=EvidenceSourceV22.LOGS,
                status=ReadSourceStatusV22.SUCCESS_EMPTY,
                requested_services=("checkout",),
                covered_services=(),
                window=window,
                records=(),
                truncated=False,
                safe_error_code=None,
                latency_ms=1,
            )
            for window in windows[1:]
        ),
    )
    health = ConnectorHealthResultV1(
        connector_name="logs",
        kind="OPENSEARCH",
        status=ConnectorAvailabilityV1.AVAILABLE,
        capabilities=OpenSearchConnectorV1(
            smoke_profile.connector_config,
            credential_resolver=CredentialResolverV1(environment={}),
            timeout_seconds=2,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "hits": {"hits": []},
                        "aggregations": {"services": {"buckets": []}},
                    },
                )
            ),
        ).capabilities(),
        discovered_services=("checkoutservice",),
        safe_error_code=None,
        latency_ms=1,
    )
    report = evaluate_connector_smoke_v0222(
        smoke_profile=smoke_profile,
        active_profile=active,
        connector_health=health,
        query_results=results,
        active_profile_file_sha256_before="4" * 64,
        active_profile_file_sha256_after="4" * 64,
        healthy_traffic_result_sha256="5" * 64,
        healthy_traffic_attempted=30,
        healthy_traffic_succeeded=30,
        queue_flag_value=0,
        baseline_unchanged=True,
        cleanup="CLEAN",
    )

    assert report.terminal == CONNECTOR_SMOKE_PASS_V0222
    assert report.query_count == 3
    assert report.nonempty_window_count == 1
    assert report.accepted_checkout_record_count == 1
    assert report.active_profile_survived_restart is True
    assert [item.status for item in report.query_diagnostics] == [
        "SUCCESS_NONEMPTY",
        "SUCCESS_EMPTY",
        "SUCCESS_EMPTY",
    ]


def test_active_profile_is_reloaded_by_a_distinct_consumer_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "restart-proof.json"
    monkeypatch.delenv("PYTHONPATH", raising=False)

    proof = run_active_profile_restart_proof_v0222(ROOT, output_path=output)

    assert proof["terminal"] == (
        "ECOMSRE_PRODUCT_V0222_ACTIVE_PROFILE_RESTART_PROOF_PASS"
    )
    assert proof["parent_pid"] != proof["child_pid"]
    assert proof["process_relation"] == "DISTINCT_CONSUMER_PROCESS"
    assert proof["network_request_count"] == 0
    assert proof["live_smoke_rerun_count"] == 0
    assert proof["active_profile_sha256"] == (
        "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
    )
    assert output.exists()


def test_service_identity_binds_configured_aliases_and_successful_queries() -> None:
    binding = build_service_identity_binding_v0222(ROOT)

    assert binding["logical_service"] == "checkout"
    assert binding["configured_service_aliases"] == (
        "checkout",
        "checkoutservice",
    )
    assert binding["successful_query_count"] == 3
    assert len(binding["successful_query_result_sha256"]) == 3
    assert binding["accepted_checkout_record_count"] == 15
