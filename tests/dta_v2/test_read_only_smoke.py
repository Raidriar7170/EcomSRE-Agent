from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.read_only_smoke import run_read_only_smoke
from ecomsre.dta_v2.read_tools import FakeReadBackend
from ecomsre.dta_v2.tool_contracts import ObservationStatus


def test_smoke_queries_all_five_adapters_in_separate_bounded_investigations(
    tmp_path: Path,
) -> None:
    report = run_read_only_smoke(
        smoke_id="4" * 32,
        service="payment",
        backend=FakeReadBackend.healthy(),
        evidence_root=tmp_path / "evidence",
    )

    assert report.terminal == "PASS"
    assert len(report.tool_results) == 5
    assert {item.tool.value for item in report.tool_results} == {
        "query_metrics",
        "search_logs",
        "query_trace_neighborhood",
        "inspect_service_runtime",
        "inspect_resource_usage",
    }
    assert all(item.status is ObservationStatus.SUCCESS for item in report.tool_results)
    assert all(item.dispatch_count == 1 for item in report.tool_results)
    assert report.fault_injection_count == 0
    assert report.agent_call_count == 0
    assert report.provider_call_count == 0
    assert report.runbook_execution_count == 0
    assert report.forward_mutation_count == 0
    assert report.configuration_mutation_count == 0
    assert report.service_mutation_count == 0
    assert len(list((tmp_path / "evidence").glob("*.json"))) == 6
