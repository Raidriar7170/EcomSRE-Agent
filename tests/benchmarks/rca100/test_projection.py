from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ecomsre_rca100.entity import load_entity_catalog
from ecomsre_rca100.projection import (
    build_agent_context,
    load_agent_task,
    project_logs,
    project_metrics,
    project_traces,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_case(root: Path) -> Path:
    case = root / "source-case"
    case.mkdir()
    _write_json(
        case / "task.json",
        {
            "task_id": "forbidden-source-id",
            "task_version": "1.0",
            "alert_event_id": "event-1",
            "alert_title": "Latency alert",
            "alert_trigger_time": "",
            "alert_window": {
                "start": "1970-01-01T00:01:40+00:00",
                "end": "1970-01-01T00:02:00+00:00",
            },
            "alert_entity": {
                "entity_id": "pod-a",
                "entity_name": "pod-a",
                "entity_type": "k8s.pod",
                "entity_domain": "k8s",
            },
            "prompt_text": "Investigate the latency alert.",
            "workspace": "synthetic",
            "region_id": "synthetic",
            "available_modalities": [
                "metrics",
                "logs",
                "traces",
                "events",
                "alerts",
                "topology",
            ],
            "scoring_note": "Synthetic schema only.",
        },
    )
    _write_json(
        case / "topology.json",
        {
            "case_id": "not-agent-visible",
            "cluster_id": "cluster",
            "source": "synthetic",
            "window": {},
            "stats": {},
            "entities": [
                {
                    "id": "svc-a",
                    "type": "apm.service",
                    "name": "  SÉRVICE   A ",
                    "props": {"service": "SÉRVICE A"},
                },
                {
                    "id": "svc-a-alias",
                    "type": "apm.service",
                    "name": "Service A Alias",
                    "props": {},
                },
                {
                    "id": "svc-b",
                    "type": "apm.service",
                    "name": "Service B",
                    "props": {"service": "Service B"},
                },
                {
                    "id": "instance-a",
                    "type": "apm.instance",
                    "name": "instance-a",
                    "props": {},
                },
                {
                    "id": "pod-a",
                    "type": "k8s.pod",
                    "name": "pod-a",
                    "props": {},
                },
            ],
            "edges": [
                {"src": "svc-a", "dst": "instance-a", "relation": "contains"},
                {"src": "pod-a", "dst": "instance-a", "relation": "hosts"},
                {"src": "svc-a", "dst": "svc-a-alias", "relation": "same_as"},
            ],
        },
    )
    pq.write_table(
        pa.table(
            {
                "id": ["other"],
                "time": ["1970-01-01T00:01:50+00:00"],
                "timestamp": [""],
                "time_s": [110],
            }
        ),
        case / "alerts.parquet",
    )
    rows: list[dict[str, object]] = []
    for timestamp, value_a, value_b in (
        (101_000_000, 1.0, 1.0),
        (102_000_000, 1.0, 1.0),
        (103_000_000, 1.0, 1.0),
        (111_000_000, 2.0, 5.0),
        (112_000_000, 2.0, 5.0),
        (113_000_000, 2.0, 5.0),
    ):
        rows.extend(
            (
                {
                    "time": timestamp,
                    "domain": "apm",
                    "entity_set": "apm.service.legacy",
                    "entity_id": "",
                    "entity_name": "SÉRVICE A",
                    "metric": "cpu",
                    "value": value_a,
                    "metric_set_id": None,
                    "service": None,
                },
                {
                    "time": timestamp,
                    "domain": "apm",
                    "entity_set": "apm.operation",
                    "entity_id": "svc-b",
                    "entity_name": "Service B",
                    "metric": "latency",
                    "value": value_b,
                    "metric_set_id": "synthetic",
                    "service": None,
                },
            )
        )
    pq.write_table(pa.Table.from_pylist(rows), case / "metrics.parquet")
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "content": "ERROR request 100 failed",
                    "_time_": "1970-01-01T00:01:49+00:00",
                    "_pod_uid_": "pod-a",
                    "_pod_name_": "pod-a",
                    "_container_name_": "service-a",
                },
                {
                    "content": "ERROR request 101 failed",
                    "_time_": "1970-01-01T00:01:51+00:00",
                    "_pod_uid_": "pod-a",
                    "_pod_name_": "pod-a",
                    "_container_name_": "service-a",
                },
            ]
        ),
        case / "logs.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "startTime": "109000000000",
                    "duration": "10",
                    "serviceName": "Service B",
                    "statusCode": "0",
                },
                {
                    "startTime": "111000000000",
                    "duration": "30",
                    "serviceName": "Service B",
                    "statusCode": "2",
                },
            ]
        ),
        case / "traces.parquet",
    )
    return case


def test_metrics_projection_treats_null_values_as_unusable_rows(
    tmp_path: Path,
) -> None:
    case = _write_case(tmp_path)
    table = pq.read_table(case / "metrics.parquet")
    rows = table.to_pylist()
    rows.append({**rows[0], "time": 109_000_000, "value": None})
    pq.write_table(pa.Table.from_pylist(rows), case / "metrics.parquet")
    catalog = load_entity_catalog(case / "topology.json")
    task = load_agent_task(case, opaque_case_id="rca100-case-0001", catalog=catalog)

    projection = project_metrics(case / "metrics.parquet", task=task, catalog=catalog)

    assert projection.status == "AVAILABLE"
    assert projection.unmapped_rows == 1


def test_entity_catalog_normalizes_aliases_and_explicit_parent_service(
    tmp_path: Path,
) -> None:
    case = _write_case(tmp_path)

    catalog = load_entity_catalog(case / "topology.json")

    service = catalog.by_ref["apm|apm.service|svc-a"]
    pod = catalog.by_ref["k8s|k8s.pod|pod-a"]
    assert service.normalized_name == "sérvice a"
    assert service.same_as_refs == ("apm|apm.service|svc-a-alias",)
    assert pod.parent_service_ref_or_none == "apm|apm.service|svc-a"


def test_task_strips_source_identity_and_uses_task_scoped_alert_fallback(
    tmp_path: Path,
) -> None:
    case = _write_case(tmp_path)
    catalog = load_entity_catalog(case / "topology.json")

    task = load_agent_task(case, opaque_case_id="rca100-case-0001", catalog=catalog)

    assert task.opaque_case_id == "rca100-case-0001"
    assert task.anchor_timestamp == 110.0
    assert task.anchor_source == "ALERTS_TASK_SCOPED_FIRST_OCCURRED"
    assert task.alert_entity_ref == "k8s|k8s.pod|pod-a"
    assert "forbidden-source-id" not in task.model_dump_json()


def test_task_uses_fixed_neutral_title_when_public_title_is_empty(
    tmp_path: Path,
) -> None:
    case = _write_case(tmp_path)
    payload = json.loads((case / "task.json").read_text(encoding="utf-8"))
    payload["alert_title"] = ""
    _write_json(case / "task.json", payload)
    catalog = load_entity_catalog(case / "topology.json")

    task = load_agent_task(case, opaque_case_id="rca100-case-0001", catalog=catalog)

    assert task.alert_title == "Alert title unavailable."


def test_metrics_projection_reuses_f0_and_max_series_entity_aggregation(
    tmp_path: Path,
) -> None:
    case = _write_case(tmp_path)
    catalog = load_entity_catalog(case / "topology.json")
    task = load_agent_task(case, opaque_case_id="rca100-case-0001", catalog=catalog)

    projection = project_metrics(case / "metrics.parquet", task=task, catalog=catalog)

    assert projection.status == "AVAILABLE"
    assert projection.ranking[0].entity_ref == "apm|apm.service|svc-b"
    assert projection.ranking[0].score == 4.0
    assert projection.ranking[1].entity_ref == "apm|apm.service|svc-a"
    assert projection.ranking[1].score == 1.0
    assert projection.ranking[0].supporting_metrics_evidence_refs == (
        "metric:0001",
    )
    assert projection.valid_series == 2
    assert projection.unmapped_rows == 0


def test_logs_and_traces_reuse_bounded_strong_single_semantics(
    tmp_path: Path,
) -> None:
    case = _write_case(tmp_path)
    catalog = load_entity_catalog(case / "topology.json")
    task = load_agent_task(case, opaque_case_id="rca100-case-0001", catalog=catalog)

    logs = project_logs(case / "logs.parquet", task=task, catalog=catalog)
    traces = project_traces(case / "traces.parquet", task=task, catalog=catalog)

    assert logs.status == "AVAILABLE"
    assert logs.evidence[0].evidence_ref == "log:0001"
    assert logs.evidence[0].entity_ref == "k8s|k8s.pod|pod-a"
    assert logs.evidence[0].score == 2.0
    assert traces.status == "AVAILABLE"
    assert traces.evidence[0].evidence_ref == "trace:0001"
    assert traces.evidence[0].entity_ref == "apm|apm.service|svc-b"
    assert traces.evidence[0].score == 2.5


def test_agent_context_is_bounded_and_exposes_no_source_identity(
    tmp_path: Path,
) -> None:
    case = _write_case(tmp_path)

    context = build_agent_context(case, opaque_case_id="rca100-case-0001")

    encoded = context.model_dump_json()
    assert "forbidden-source-id" not in encoded
    assert "not-agent-visible" not in encoded
    assert len(context.visible_entities) <= 64
    assert context.task.alert_entity_ref in {
        item.entity_ref for item in context.visible_entities
    }
