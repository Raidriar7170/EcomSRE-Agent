from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre_rcaeval.dataset import (
    DevSystem,
    audit_dev_dataset,
    discover_dev_cases,
)
from ecomsre_rcaeval.tools import RCAEvalToolset, SourceStatus


def _case(
    root: Path,
    *,
    group: str,
    instance: str,
    traces: bool,
) -> Path:
    case = root / group / instance
    case.mkdir(parents=True)
    (case / "inject_time.txt").write_text("2\n", encoding="utf-8")
    (case / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu,checkoutservice_latency-90,cartservice_cpu\n"
        "0,1,1,1\n"
        "1,1,1,1\n"
        "2,10,1,1\n"
        "3,10,1,1\n",
        encoding="utf-8",
    )
    (case / "logs.csv").write_text(
        "time,service,message,level\n"
        "1,checkoutservice,healthy,INFO\n"
        "2,checkoutservice,deadline exceeded,ERROR\n"
        "3,cartservice,healthy,INFO\n",
        encoding="utf-8",
    )
    if traces:
        (case / "traces.csv").write_text(
            "time,service,peer,duration,error\n"
            "1,checkoutservice,cartservice,1.0,0\n"
            "2,checkoutservice,cartservice,9.0,1\n",
            encoding="utf-8",
        )
    return case


def test_dev_dataset_audit_reads_only_allowed_system_shape(tmp_path: Path) -> None:
    root = tmp_path / "RE2-OB"
    _case(root, group="checkoutservice_cpu", instance="1", traces=True)

    cases = discover_dev_cases(root, DevSystem.RE2_OB)
    audit = audit_dev_dataset(root, DevSystem.RE2_OB, expected_cases=1)

    assert len(cases) == 1
    assert cases[0].root_cause_service == "checkoutservice"
    assert cases[0].fault == "cpu"
    assert cases[0].instance == "1"
    assert cases[0].inject_time == 2
    assert audit.case_count == 1
    assert audit.metrics_cases == 1
    assert audit.logs_cases == 1
    assert audit.traces_cases == 1
    assert audit.timestamp_min == 0
    assert audit.timestamp_max == 3


def test_dev_dataset_discovery_ignores_upstream_auxiliary_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-OB"
    _case(root, group="checkoutservice_cpu", instance="1", traces=True)
    auxiliary = root / "checkoutservice_cpu" / "multi-source-data"
    auxiliary.mkdir()
    (auxiliary / "metrics.csv").write_text("time,value\n0,1\n", encoding="utf-8")

    cases = discover_dev_cases(root, DevSystem.RE2_OB)

    assert tuple(item.instance for item in cases) == ("1",)


def test_upstream_metric_rows_without_timestamps_are_not_agent_visible(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-OB"
    case_root = _case(root, group="checkoutservice_cpu", instance="1", traces=True)
    with (case_root / "simple_metrics.csv").open("a", encoding="utf-8") as handle:
        handle.write(",999,999,999\n")
    case = discover_dev_cases(root, DevSystem.RE2_OB)[0]

    audit = audit_dev_dataset(root, DevSystem.RE2_OB, expected_cases=1)
    response = RCAEvalToolset(case).rank_metric_anomalies(top_k=3)

    assert audit.timestamp_min == 0
    assert audit.timestamp_max == 3
    assert all(point[0] <= 3 for item in response.evidence for point in item.points)


def test_sock_shop_missing_traces_is_typed_source_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "RE2-SS"
    case_root = _case(root, group="checkoutservice_cpu", instance="1", traces=False)
    case = discover_dev_cases(root, DevSystem.RE2_SS)[0]
    tools = RCAEvalToolset(case)

    response = tools.list_trace_services()

    assert response.status is SourceStatus.SOURCE_UNAVAILABLE
    assert response.reason == "RCAEval RE2-SS does not provide traces"
    assert str(case_root) not in json.dumps(response.model_dump(mode="json"))


def test_metrics_logs_and_traces_are_bounded_and_evidence_referenced(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-OB"
    case_root = _case(root, group="checkoutservice_cpu", instance="1", traces=True)
    case = discover_dev_cases(root, DevSystem.RE2_OB)[0]
    tools = RCAEvalToolset(case)

    metric_services = tools.list_metric_services()
    anomalies = tools.rank_metric_anomalies(top_k=2)
    logs = tools.search_logs(service="checkoutservice", query="deadline", limit=2)
    trace_errors = tools.rank_trace_error_anomalies(top_k=2)

    assert metric_services.status is SourceStatus.AVAILABLE
    assert metric_services.values == ("cartservice", "checkoutservice")
    assert anomalies.evidence[0].name == "checkoutservice_cpu"
    assert anomalies.evidence[0].evidence_id == "metric:0001"
    assert logs.evidence[0].evidence_id == "log:0001"
    assert "deadline exceeded" in logs.evidence[0].summary
    assert trace_errors.evidence[0].evidence_id == "trace:0001"
    encoded = json.dumps(
        {
            "metrics": anomalies.model_dump(mode="json"),
            "logs": logs.model_dump(mode="json"),
            "traces": trace_errors.model_dump(mode="json"),
        }
    )
    assert str(case_root) not in encoded
    assert len(anomalies.evidence) <= 2
    assert len(logs.evidence) <= 2
    assert len(trace_errors.evidence) <= 2


def test_dataset_root_name_mismatch_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "unexpected-system"
    root.mkdir()
    with pytest.raises(ValueError, match="root name"):
        discover_dev_cases(root, DevSystem.RE2_OB)


def test_real_re2_log_schema_uses_epoch_timestamp_and_container_name(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-SS"
    case_root = _case(root, group="carts_cpu", instance="1", traces=False)
    (case_root / "inject_time.txt").write_text("1705595460\n", encoding="utf-8")
    (case_root / "logs.csv").write_text(
        "time,timestamp,container_name,message,level\n"
        "16:30,1705595459012034902,catalogue,healthy,INFO\n"
        "16:31,1705595461012034902,carts,deadline exceeded,ERROR\n",
        encoding="utf-8",
    )
    case = discover_dev_cases(root, DevSystem.RE2_SS)[0]
    tools = RCAEvalToolset(case)

    services = tools.list_log_services()
    logs = tools.search_logs(service="carts", query="deadline", limit=2)

    assert services.values == ("carts", "catalogue")
    assert logs.evidence[0].service == "carts"
    assert logs.evidence[0].started_at == pytest.approx(1705595461.0120349)


def test_real_re2_ob_trace_schema_prefers_epoch_start_time_millis(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-OB"
    case_root = _case(root, group="checkoutservice_cpu", instance="1", traces=True)
    (case_root / "inject_time.txt").write_text("1705595460\n", encoding="utf-8")
    (case_root / "traces.csv").write_text(
        "time,serviceName,startTimeMillis,duration,statusCode\n"
        "16:30,checkoutservice,1705595459000,1,UNSET\n"
        "16:31,checkoutservice,1705595461000,9,ERROR\n",
        encoding="utf-8",
    )
    case = discover_dev_cases(root, DevSystem.RE2_OB)[0]

    traces = RCAEvalToolset(case).summarize_trace_diagnostics(top_k=2)

    assert traces.status is SourceStatus.AVAILABLE
    assert traces.evidence[0].started_at == pytest.approx(1705595459.0)
    assert traces.evidence[0].ended_at == pytest.approx(1705595461.0)
