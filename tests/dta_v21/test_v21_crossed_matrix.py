from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v21.registry import (
    load_default_scenario_registries,
    validate_crossed_matrix,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_crossed_scenario_matrix_passes_all_anti_shortcut_checks() -> None:
    observer, evaluator, anchors = load_default_scenario_registries(REPO_ROOT)
    report = validate_crossed_matrix(
        observer_registry=observer,
        evaluator_registry=evaluator,
        legacy_anchors=anchors,
    )

    assert report.status == "PASS"
    assert all(report.checks.values())
    assert len(report.report_sha256) == 64


def test_each_goal_defined_scenario_tuple_is_frozen() -> None:
    observer, evaluator, _ = load_default_scenario_registries(REPO_ROOT)
    observed = {
        scenario.scenario_id: {
            "candidates": scenario.candidate_services,
            "terminal": truth.terminal.value,
            "root": truth.root_service,
            "domain": None if truth.fault_domain is None else truth.fault_domain.value,
            "mechanism": (
                None if truth.fault_mechanism is None else truth.fault_mechanism.value
            ),
            "evidence": tuple(
                source.value for source in truth.required_evaluator_evidence
            ),
            "runbook": (
                None if truth.expected_runbook is None else truth.expected_runbook.value
            ),
            "backend": (
                None if truth.runbook_backend is None else truth.runbook_backend.value
            ),
            "live": truth.live_required,
            "writes": truth.forward_writes,
        }
        for scenario, truth in zip(observer.scenarios, evaluator.scenarios, strict=True)
    }

    assert observed == {
        "dta21-dev-001": {
            "candidates": ("ad", "frontend", "load-generator"),
            "terminal": "COMPLETED",
            "root": "ad",
            "domain": "LOCAL_RESOURCE",
            "mechanism": "CPU_SATURATION",
            "evidence": ("METRICS", "RUNTIME", "RESOURCES"),
            "runbook": "MITIGATE_CPU_SATURATION",
            "backend": "LIVE_ALLOWED",
            "live": True,
            "writes": 1,
        },
        "dta21-dev-002": {
            "candidates": ("checkout", "email", "frontend"),
            "terminal": "COMPLETED",
            "root": "email",
            "domain": "SERVICE_RUNTIME",
            "mechanism": "SERVICE_UNAVAILABLE",
            "evidence": ("METRICS", "RUNTIME"),
            "runbook": "RESTORE_SERVICE_AVAILABILITY",
            "backend": "LIVE_ALLOWED",
            "live": True,
            "writes": 1,
        },
        "dta21-dev-003": {
            "candidates": ("checkout", "frontend", "product-catalog"),
            "terminal": "COMPLETED",
            "root": "product-catalog",
            "domain": "SERVICE_RUNTIME",
            "mechanism": "SERVICE_UNAVAILABLE",
            "evidence": ("TRACES", "RUNTIME"),
            "runbook": "RESTORE_SERVICE_AVAILABILITY",
            "backend": "LIVE_ALLOWED",
            "live": True,
            "writes": 1,
        },
        "dta21-dev-004": {
            "candidates": ("checkout", "frontend", "quote", "shipping"),
            "terminal": "COMPLETED",
            "root": "shipping",
            "domain": "DEPENDENCY",
            "mechanism": "DEPENDENCY_LATENCY",
            "evidence": ("METRICS", "TRACES"),
            "runbook": "RESTORE_DEPENDENCY_LATENCY",
            "backend": "REPLAY_ONLY",
            "live": False,
            "writes": 0,
        },
        "dta21-dev-005": {
            "candidates": ("checkout", "frontend", "payment"),
            "terminal": "COMPLETED",
            "root": None,
            "domain": None,
            "mechanism": None,
            "evidence": ("METRICS", "RUNTIME"),
            "runbook": None,
            "backend": None,
            "live": True,
            "writes": 0,
        },
        "dta21-dev-006": {
            "candidates": ("checkout", "email", "frontend"),
            "terminal": "NEED_MORE_EVIDENCE",
            "root": None,
            "domain": None,
            "mechanism": None,
            "evidence": ("METRICS", "RUNTIME"),
            "runbook": None,
            "backend": None,
            "live": False,
            "writes": 0,
        },
    }
