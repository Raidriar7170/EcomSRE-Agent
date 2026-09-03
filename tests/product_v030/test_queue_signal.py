from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest

from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    SignalStrengthV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricFactV22,
    MetricKindV22,
    MetricUnitV22,
    MetricSupportStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v23.generic_anomalies import extract_generic_anomalies_v23
from ecomsre.dta_v2.v23.ontology_view import build_active_ontology_view_v23
from ecomsre.product.connectors.base import ConnectorQueryContextV1, ConnectorWindowV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.contracts import ConnectorConfigV1, PrometheusConnectorSettingsV1
from ecomsre.product.incidents.diagnosis_bridge import _domain_for_anomalies


NOW = datetime(2026, 9, 3, tzinfo=UTC)
TEMPLATES = {
    name: f'{name}{{service="{{service}}"}}'
    for name in (
        "request_support",
        "error_rate",
        "latency",
        "cpu",
        "memory",
    )
}


@pytest.mark.parametrize(
    "lag,mean,stddev,samples,expected",
    [
        (0.0, 0.0, 0.0, 3, False),
        (19.9, 0.0, 0.0, 3, False),
        (20.0, 0.0, 0.0, 3, True),
        (100.0, 0.0, 0.0, 2, False),
        (59.9, 10.0, 10.0, 3, False),
        (60.0, 10.0, 10.0, 3, True),
        (100.0, None, None, 3, False),
    ],
)
def test_frozen_queue_detector(lag, mean, stddev, samples, expected):
    kind = MetricKindV22("QUEUE_LAG")
    record = MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service="fraud-detection",
        metric_kind=kind,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=samples,
        value=lag,
        unit=MetricUnitV22.COUNT,
        window_started_at=NOW - timedelta(seconds=60),
        window_ended_at=NOW,
    )
    payload = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": "a:metrics:fraud-detection:queue-lag",
        "source": "METRICS",
        "request_sha256": "0" * 64,
        "status": "SUCCESS_NONEMPTY",
        "records": [record.model_dump(mode="json")],
        "truncated": False,
    }
    outcome = ReadOutcomeV22.model_validate_json(
        json.dumps(
            {
                **payload,
                "outcome_sha256": semantic_sha256_v22(payload),
            }
        )
    )
    baseline = BaselineProfileV22.build(
        metric_stats=() if mean is None else (("fraud-detection", kind, mean, stddev),),
        trace_stats=(),
        resource_stats=(),
    )
    memory, _ = build_memory_views_v22(
        outcomes=(outcome,), baseline=baseline, observed_at=NOW, top_k=64
    )
    anomalies = extract_generic_anomalies_v23(
        memory=memory, candidate_services=("fraud-detection",)
    )
    assert bool(anomalies) is expected
    if expected:
        assert len(anomalies) == 1
        assert anomalies[0].kind.value == "METRIC_QUEUE_LAG_OUTLIER"
        assert anomalies[0].strength is SignalStrengthV22.STRONG
        assert _domain_for_anomalies(anomalies).value == "CONCURRENCY"
    assert not memory.predicates


def test_optional_template_preserves_existing_contract():
    assert (
        PrometheusConnectorSettingsV1(query_templates=TEMPLATES).query_templates
        == TEMPLATES
    )
    optional = {**TEMPLATES, "queue_lag": 'sum(lag{group="{service}"})'}
    assert (
        "queue_lag"
        in PrometheusConnectorSettingsV1(query_templates=optional).query_templates
    )
    with pytest.raises(ValueError):
        PrometheusConnectorSettingsV1(query_templates={**TEMPLATES, "arbitrary": "x"})


@pytest.mark.parametrize("baseline_query", [False, True])
def test_queue_connector_returns_one_count_fact_with_real_sample_count(baseline_query):
    queries = []

    def handler(request):
        queries.append(request.url.params["query"])
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {},
                            "values": [
                                [NOW.timestamp() - seconds, "0" if queries[-1].startswith("error_rate") else value]
                                for seconds, value in ((20, "30"), (10, "40"), (0, "50"))
                            ],
                        }
                    ],
                },
            },
        )

    connector = PrometheusConnectorV1(
        ConnectorConfigV1(
            name="prometheus",
            kind="PROMETHEUS",
            endpoint="http://prometheus.test",
            credential_refs={},
            settings={
                "query_templates": {
                    **TEMPLATES,
                    "queue_lag": 'sum(lag{group="{service}"})',
                }
            },
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = connector.query(
            ConnectorQueryContextV1(
                environment_id="env-0123456789abcdef01234567",
                requested_services=("fraud-detection",),
                service_aliases={"fraud-detection": "fraud-detection"},
                window=ConnectorWindowV1(
                    started_at=NOW - timedelta(seconds=30), ended_at=NOW
                ),
                maximum_records=10 if baseline_query else 1,
                requested_source=None if baseline_query else EvidenceSourceV22.METRICS,
                request_sha256="0" * 64,
                metric_kinds=(MetricKindV22("QUEUE_LAG"),),
            )
        )[0]
    finally:
        connector.close()
    if baseline_query:
        diagnostics = connector.baseline_diagnostics_v023()
        assert diagnostics is not None
        assert any(item.template_name == "queue_lag" and item.sample_count == 3 for item in diagnostics.templates)
        assert len(queries) == 6
    else:
        assert queries == ['sum(lag{group="fraud-detection"})']
    assert result.covered_services == ("fraud-detection",)
    queue_records = [item for item in result.records if isinstance(item, MetricFactV22) and item.metric_kind is MetricKindV22.QUEUE_LAG]
    assert len(queue_records) == 1
    assert queue_records[0].unit is MetricUnitV22.COUNT
    assert queue_records[0].sample_count == 3
    assert queue_records[0].value == 40.0


def test_core_ontology_has_no_queue_mechanism():
    ontology = build_active_ontology_view_v23(
        candidate_services=("checkout", "fraud-detection", "kafka")
    )
    assert "KAFKA_QUEUE_BACKLOG" not in ontology.model_dump_json()


def test_queue_action_is_product_only_and_does_not_expand_checkout_bundle():
    from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030
    from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22

    action = build_queue_lag_action_v030()
    assert action.target_services == ("fraud-detection",)
    assert action.request.metric_kinds == (MetricKindV22.QUEUE_LAG,)
    assert action.request.max_results == 1
    with pytest.raises(ValueError):
        EvidenceActionV22.model_validate(action.model_dump())
    altered = action.model_dump()
    altered["target_services"] = ("checkout",)
    with pytest.raises(ValueError):
        type(action).model_validate(altered)
