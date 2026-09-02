from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.changes import ChangeEventRepositoryV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    EnvironmentRecordV1,
    ServiceIdentityMapV1,
    ServiceIdentityV1,
)
from ecomsre.product.environment.capabilities import (
    EnvironmentCapabilityMatrixV1,
    SourceCapabilityV1,
)
from ecomsre.product.incidents.contracts import IncidentRecordV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.telemetry.metrics import ProductMetricsV1


NOW = datetime(2026, 9, 3, tzinfo=UTC)
ENVIRONMENT_ID = "env-" + "1" * 24


def _sealed(model, digest_field, **payload):
    draft = model.model_construct(**payload, **{digest_field: "0" * 64})
    value = draft.model_dump(mode="json", exclude={digest_field})
    return model.model_validate_json(
        json.dumps({**value, digest_field: semantic_sha256_v22(value)})
    )


@pytest.mark.parametrize(
    "has_template,has_candidate,metrics_available",
    [
        (False, False, True),
        (False, True, True),
        (True, False, True),
        (True, True, True),
        (True, True, False),
    ],
)
def test_acquisition_schedules_queue_only_when_all_gates_hold(
    tmp_path, has_template, has_candidate, metrics_available
):
    candidates = ("checkout", "fraud-detection") if has_candidate else ("checkout",)
    templates = {
        name: f'{name}{{service="{{service}}"}}'
        for name in ("request_support", "error_rate", "latency", "cpu", "memory")
    }
    if has_template:
        templates["queue_lag"] = 'queue_lag{group="{service}"}'
    environment = EnvironmentRecordV1(
        environment_id=ENVIRONMENT_ID,
        name="queue-action-contract",
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        connector_configs=(
            ConnectorConfigV1(
                name="prometheus",
                kind="PROMETHEUS",
                endpoint="http://prometheus.test",
                credential_refs={},
                settings={"query_templates": templates},
            ),
        ),
    )
    identity_map = ServiceIdentityMapV1.build(
        environment_id=ENVIRONMENT_ID,
        services=tuple(
            ServiceIdentityV1(
                service_id=f"svc-{index:024x}",
                logical_service=service,
            )
            for index, service in enumerate(candidates)
        ),
    )
    capability = _sealed(
        EnvironmentCapabilityMatrixV1,
        "capability_sha256",
        environment_id=ENVIRONMENT_ID,
        logical_services=candidates,
        sources=(
            SourceCapabilityV1(
                source="METRICS",
                status="AVAILABLE" if metrics_available else "UNAVAILABLE",
                connector_names=("prometheus",),
                covered_services=candidates if metrics_available else (),
                target_complete_coverage=False,
                observable_predicates=(),
            ),
        ),
        mechanisms=(),
        no_incident_eligible=False,
        effective_policy_sha256="0" * 64,
        verified_at=NOW,
    )
    incident = _sealed(
        IncidentRecordV1,
        "incident_sha256",
        environment_id=ENVIRONMENT_ID,
        external_incident_key="queue-action-contract",
        alert_name="observation",
        summary="Bounded connector scheduling test",
        started_at=NOW - timedelta(seconds=60),
        ended_at=NOW,
        candidate_service_ids=tuple(item.service_id for item in identity_map.services),
        incident_id="inc-" + "1" * 24,
        baseline_id="base-" + "1" * 24,
        baseline_sha256="0" * 64,
        service_identity_sha256=identity_map.identity_sha256,
        source_capability_sha256=capability.capability_sha256,
        candidate_logical_services=candidates,
        diagnosis_observed_at=NOW,
        created_at=NOW,
    )
    queries = []

    def handler(request):
        assert request.url.path == "/api/v1/query_range"
        queries.append(request.url.params["query"])
        sample = "0" if queries[-1].startswith("error_rate{") else "40"
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
                                [NOW.timestamp() - seconds, sample]
                                for seconds in (20, 10, 0)
                            ],
                        }
                    ],
                },
            },
        )

    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    backend = ProductReadBackendV1(
        connectors=ConnectorRegistryV1(
            credential_resolver=CredentialResolverV1(environment={}),
            timeout_seconds=2,
            transports={"prometheus": httpx.MockTransport(handler)},
        ),
        changes=ChangeEventRepositoryV1(store),
        metrics=ProductMetricsV1(store),
    )
    acquisition = backend.acquire(
        incident=incident,
        environment=environment,
        identity_map=identity_map,
        capability_matrix=capability,
        topology_edges=(),
    )
    queue = [
        item
        for item in acquisition.snapshots
        if item["action"]["action_id"] == "a:metrics:fraud-detection:queue-lag"
    ]
    expected_queue = has_template and has_candidate and metrics_available
    assert len(queue) == int(expected_queue)
    assert queries.count('queue_lag{group="fraud-detection"}') == int(expected_queue)
    if expected_queue:
        assert queue[0]["read_outcome"]["status"] == "SUCCESS_NONEMPTY"
        assert queue[0]["read_outcome"]["records"][0]["metric_kind"] == "QUEUE_LAG"
        assert queue[0]["read_outcome"]["records"][0]["sample_count"] == 3
        assert queue[0]["action"]["request"]["metric_kinds"] == ["QUEUE_LAG"]
    checkout = [
        item
        for item in acquisition.snapshots
        if item["action"]["target_services"] == ["checkout"]
    ]
    assert len(checkout) == int(metrics_available)
    if checkout:
        kinds = checkout[0]["action"]["request"]["metric_kinds"]
        assert set(kinds) == {"REQUEST_SUPPORT", "ERROR_RATE", "LATENCY_P95_MS"}
        assert len(kinds) == 3
    else:
        assert not queries
