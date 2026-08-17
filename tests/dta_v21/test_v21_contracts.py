from __future__ import annotations

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v21.contracts import (
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    ScenarioSpecV21,
    TerminalV21,
)
from ecomsre.dta_v2.v21.replay import build_replay_diagnosis


def test_successor_ontology_is_independent_and_exact() -> None:
    assert {item.value for item in FaultDomainV21} == {
        "APPLICATION",
        "CONFIGURATION",
        "SERVICE_RUNTIME",
        "LOCAL_RESOURCE",
        "NETWORK",
        "DEPENDENCY",
        "QUEUE",
        "UNKNOWN",
    }
    assert {item.value for item in FaultMechanismV21} == {
        "CONFIGURATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "MEMORY_LEAK",
        "CPU_SATURATION",
        "DEPENDENCY_LATENCY",
        "UNKNOWN",
    }
    assert "DEPENDENCY_TIMEOUT" not in {item.value for item in FaultMechanismV21}
    assert {item.value for item in RunbookIdV21} == {
        "ROLLBACK_CONFIGURATION",
        "RESTART_SERVICE",
        "MITIGATE_MEMORY_LEAK",
        "MITIGATE_CPU_SATURATION",
        "RESTORE_SERVICE_AVAILABILITY",
        "RESTORE_DEPENDENCY_LATENCY",
    }


def test_resolved_evidence_digest_and_run_binding_fail_closed() -> None:
    diagnosis, view = build_replay_diagnosis(
        run_id="1" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="ad",
        fault_domain=FaultDomainV21.LOCAL_RESOURCE,
        mechanism=FaultMechanismV21.CPU_SATURATION,
        evidence_sources=("METRICS", "RESOURCES", "RUNTIME"),
    )

    assert diagnosis.run_id == view.run_id
    with pytest.raises(ValidationError, match="digest"):
        type(view).model_validate(
            {
                **view.model_dump(mode="python"),
                "resolved_evidence_sha256": "0" * 64,
            }
        )


def test_agent_visible_scenario_rejects_truth_and_extra_fields() -> None:
    payload = {
        "schema_version": "dta-v21.scenario.v1",
        "scenario_id": "dta21-dev-001",
        "alert_summary": "Advertising responses became slower in the bounded window.",
        "candidate_services": ("ad", "frontend", "load-generator"),
        "allowed_read_tools": (
            "query_metrics",
            "search_logs",
            "query_trace_neighborhood",
            "inspect_service_runtime",
            "inspect_resource_usage",
        ),
        "maximum_read_tool_dispatches": 4,
        "maximum_repeated_identical_calls": 0,
    }
    assert ScenarioSpecV21.model_validate(payload).scenario_id == "dta21-dev-001"

    with pytest.raises(ValidationError):
        ScenarioSpecV21.model_validate({**payload, "expected_runbook": "x"})
    with pytest.raises(ValidationError, match="truth|scenario-control|evaluator"):
        ScenarioSpecV21.model_validate(
            {**payload, "alert_summary": "Ground truth is CPU_SATURATION"}
        )
