from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ecomsre.product.connectors.base import ConnectorQueryContextV1, ConnectorWindowV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.opensearch import (
    OpenSearchConnectorV1,
    _project_observer_message_v1,
)
from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
    _project_observer_message_v022,
)
from ecomsre.product.contracts import ConnectorConfigV1


NOW = datetime(2026, 9, 3, tzinfo=UTC)
CONTEXT = ConnectorQueryContextV1(
    environment_id="env-" + "1" * 24,
    requested_services=("fraud-detection",),
    service_aliases={"fraud-detection": "fraud-detection"},
    window=ConnectorWindowV1(started_at=NOW - timedelta(seconds=60), ended_at=NOW),
    maximum_records=10,
)


def _project(message, parser):
    if parser == "legacy":
        return _project_observer_message_v1(message, policy="OBSERVER_SYMPTOM_V1")
    return _project_observer_message_v022(
        message,
        policy="OBSERVER_SYMPTOM_V1",
        context=CONTEXT,
        field_path="body",
        hit_ordinal=0,
    )


@pytest.mark.parametrize("parser", ["legacy", "profile"])
@pytest.mark.parametrize(
    "message,expected",
    [
        (
            "Warning: FeatureFlag 'kafkaQueueProblems' is activated, overloading queue now.",
            "Warning: overloading queue now.",
        ),
        (
            "FeatureFlag 'kafkaQueueProblems' is enabled, sleeping 1 second",
            "sleeping 1 second",
        ),
        (
            "Done with #5 messages for overload simulation.",
            "Queue overload activity completed.",
        ),
        ("Kafka consumer connection failed", "Kafka consumer connection failed"),
    ],
)
def test_projection_removes_pinned_control_prefix_but_keeps_symptom(
    parser, message, expected
):
    assert _project(message, parser) == expected


@pytest.mark.parametrize("parser", ["legacy", "profile"])
def test_unknown_control_format_still_fails_closed(parser):
    error = ValueError if parser == "legacy" else OpenSearchSchemaExceptionV022
    with pytest.raises(error):
        _project(
            "FeatureFlag 'kafkaQueueProblems' has an unknown control format", parser
        )


def test_connector_preserves_genuine_error_and_fatal_records():
    rows = (
        ("INFO", "FeatureFlag 'kafkaQueueProblems' is enabled, sleeping 1 second"),
        ("ERROR", "Kafka consumer connection failed"),
        ("FATAL", "Kafka consumer cannot recover"),
    )
    connector = OpenSearchConnectorV1(
        ConnectorConfigV1(
            name="logs",
            kind="OPENSEARCH",
            endpoint="https://opensearch.test",
            settings={
                "index_pattern": "otel-*",
                "timestamp_field": "@timestamp",
                "service_field": "service",
                "severity_field": "severity",
                "message_field": "body",
                "message_projection_policy": "OBSERVER_SYMPTOM_V1",
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "hits": {
                        "total": {"value": len(rows)},
                        "hits": [
                            {
                                "_source": {
                                    "@timestamp": (
                                        NOW - timedelta(seconds=10 - index)
                                    ).isoformat(),
                                    "service": "fraud-detection",
                                    "severity": severity,
                                    "body": message,
                                }
                            }
                            for index, (severity, message) in enumerate(rows)
                        ],
                    }
                },
            )
        ),
    )
    try:
        result = connector.query(CONTEXT)[0]
    finally:
        connector.close()
    assert result.status.value == "SUCCESS_NONEMPTY"
    assert [(record.severity, record.message) for record in result.records] == [
        ("DIAGNOSTIC", "sleeping 1 second"),
        ("ERROR", rows[1][1]),
        ("FATAL", rows[2][1]),
    ]
    assert "kafkaqueueproblems" not in result.model_dump_json().casefold()
