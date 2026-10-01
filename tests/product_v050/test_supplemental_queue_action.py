"""Round-trip the existing exact Product action without widening frozen Core."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22, semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030
from ecomsre.product.knowledge.observations_v050 import ensure_table, load_observations


def persisted_queue(tmp_path, mutate=None):
    def connect():
        c = sqlite3.connect(tmp_path / "test.sqlite3")
        c.row_factory = sqlite3.Row
        return c

    store = SimpleNamespace(connect=connect)
    ensure_table(store)
    action = build_queue_lag_action_v030()
    end = datetime(2026, 9, 18, tzinfo=UTC)
    incident = SimpleNamespace(
        incident_id="test",
        incident_sha256="1" * 64,
        environment_id="env",
        source_capability_sha256="2" * 64,
        diagnosis_observed_at=end,
    )
    window = ConnectorWindowV1(started_at=end - timedelta(seconds=30), ended_at=end)
    result = ConnectorQueryResultV1.build(
        source=action.source,
        status=ReadSourceStatusV22.SUCCESS_EMPTY,
        requested_services=action.target_services,
        covered_services=(),
        window=window,
        records=(),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    payload = action.model_dump(mode="json")
    if mutate:
        mutate(payload)
        payload["action_sha256"] = semantic_sha256_v22(
            {k: v for k, v in payload.items() if k != "action_sha256"}
        )
    envelope = dict(
        incident_id=incident.incident_id,
        incident_sha256=incident.incident_sha256,
        environment_id=incident.environment_id,
        capability_sha256=incident.source_capability_sha256,
        parent_diagnosis_id=None,
        action=payload,
        window=window.model_dump(mode="json"),
        result=result.model_dump(mode="json"),
        resource_dependency=None,
    )
    digest = semantic_sha256_v22(envelope)
    query = semantic_sha256_v22(
        {"request": action.request_sha256, "window": envelope["window"]}
    )
    with store.connect() as c:
        c.execute(
            "INSERT INTO supplemental_observations_v050 VALUES (?,?,?)",
            ("test", query, digest),
        )
    objects = SimpleNamespace(
        metadata_store=store, read_bytes=lambda _: json.dumps(envelope).encode()
    )
    return incident, objects


def test_exact_queue_action_loads_without_inventing_resource_dependency(tmp_path):
    incident, objects = persisted_queue(tmp_path)
    rows = load_observations(incident, objects)
    assert len(rows) == 1
    assert rows[0]["source"] == "METRICS"
    assert rows[0]["status"] == "SUCCESS_EMPTY"
    assert rows[0]["resource_dependency"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"action_id": "a:metrics:fraud-detection:unknown"},
        {"coverage_keys": ["METRICS:fraud-detection:CPU_PERCENT"]},
        {"weighted_cost": 2.0},
        {"target_services": ["kafka"]},
    ],
)
def test_rehashed_queue_action_changes_remain_rejected(tmp_path, change):
    incident, objects = persisted_queue(tmp_path, lambda p: p.update(change))
    with pytest.raises(ValueError):
        load_observations(incident, objects)
