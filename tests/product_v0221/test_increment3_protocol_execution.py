from __future__ import annotations

from datetime import UTC, datetime
import json

import httpx

from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchProbeClientV0221,
)
from ecomsre.product.connectors.opensearch_probe_execution_v0221 import (
    execute_probe_protocol_v0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchFieldCapsStatusV0221,
)


def _mapping() -> dict[str, object]:
    return {
        "otel-logs-0001": {
            "mappings": {
                "properties": {
                    "observed": {
                        "properties": {"timestamp": {"type": "date"}}
                    },
                    "resource": {
                        "properties": {
                            "service": {
                                "properties": {
                                    "name": {
                                        "type": "text",
                                        "fields": {
                                            "keyword": {"type": "keyword"}
                                        },
                                    }
                                }
                            }
                        }
                    },
                    "body": {
                        "properties": {"stringValue": {"type": "text"}}
                    },
                    "severityText": {"type": "keyword"},
                    "traceId": {"type": "keyword"},
                }
            }
        }
    }


def _field_caps() -> dict[str, object]:
    return {
        "fields": {
            "observed.timestamp": {
                "date": {"type": "date", "searchable": True, "aggregatable": True}
            },
            "resource.service.name": {
                "text": {"type": "text", "searchable": True, "aggregatable": False}
            },
            "resource.service.name.keyword": {
                "keyword": {
                    "type": "keyword",
                    "searchable": True,
                    "aggregatable": True,
                }
            },
            "body.stringValue": {
                "text": {"type": "text", "searchable": True, "aggregatable": False}
            },
            "severityText": {
                "keyword": {
                    "type": "keyword",
                    "searchable": True,
                    "aggregatable": True,
                }
            },
            "traceId": {
                "keyword": {
                    "type": "keyword",
                    "searchable": True,
                    "aggregatable": True,
                }
            },
        }
    }


def _source() -> dict[str, object]:
    return {
        "observed": {"timestamp": "2026-08-28T12:00:01Z"},
        "resource": {"service": {"name": "checkoutservice"}},
        "body": {"stringValue": "checkout completed"},
        "severityText": "INFO",
        "traceId": "a" * 32,
    }


def _search_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if "aggs" in body:
        return httpx.Response(
            200,
            json={
                "timed_out": False,
                "_shards": {"failed": 0},
                "hits": {"hits": []},
                "aggregations": {
                    "services": {
                        "buckets": [{"key": "checkoutservice", "doc_count": 2}]
                    }
                },
            },
        )
    if body.get("size") == 0:
        return httpx.Response(
            200,
            json={
                "timed_out": False,
                "_shards": {"failed": 0},
                "hits": {"hits": []},
            },
        )
    return httpx.Response(
        200,
        json={
            "timed_out": False,
            "_shards": {"failed": 0},
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{"_source": _source()}],
            },
        },
    )


def _run(field_caps_status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_mapping"):
            return httpx.Response(200, json=_mapping())
        if request.url.path.endswith("/_field_caps"):
            assert request.url.params.get("fields")
            assert request.content in {b"", b"null"}
            if field_caps_status == 200:
                return httpx.Response(200, json=_field_caps())
            return httpx.Response(
                field_caps_status,
                json={
                    "error": {
                        "type": "security_exception",
                        "reason": "field capabilities unavailable",
                    },
                    "status": field_caps_status,
                },
            )
        assert request.url.path.endswith("/_search")
        return _search_response(request)

    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=16,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(handler),
    )
    try:
        return execute_probe_protocol_v0221(
            client=client,
            index_pattern="otel-logs-*",
            checkout_aliases=("checkout", "checkoutservice"),
            maximum_sample_documents=5,
            maximum_transport_retries=2,
            started_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 28, 12, 5, tzinfo=UTC),
        )
    finally:
        client.close()


def test_plan_a_executes_complete_official_protocol_and_resolves_profile() -> None:
    execution = _run(200)

    assert len(execution.plans) == 1
    assert len(execution.attempts) == 6
    assert execution.request_count == 6
    assert execution.safe_error_envelopes == ()
    assert execution.resolution.profile.field_caps_status is (
        OpenSearchFieldCapsStatusV0221.AVAILABLE
    )
    assert execution.resolution.profile.service_query_field == (
        "resource.service.name.keyword"
    )
    assert len(execution.samples) == 1


def test_field_caps_permission_failure_changes_to_plan_c_and_still_resolves() -> None:
    execution = _run(403)

    assert [plan.plan_id for plan in execution.plans] == [
        "plan-a-field-caps-get-query",
        "plan-c-mapping-sample-empirical",
    ]
    assert len(execution.attempts) == 7
    assert execution.safe_error_envelopes[0].http_status == 403
    assert execution.raw_error_bodies[0].startswith(b'{"error"')
    assert execution.resolution.profile.field_caps_status is (
        OpenSearchFieldCapsStatusV0221.UNAVAILABLE_OPTIONAL
    )
