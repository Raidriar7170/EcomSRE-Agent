from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v21.contracts import (
    RiskLevelV21,
    RunbookBackendV21,
    RunbookIdV21,
    RunbookSpecV21,
)
from ecomsre.dta_v2.v21.registry import (
    load_default_runbook_registry,
    load_default_scenario_registries,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_trusted_runbook_registry_binds_exact_p0_semantics() -> None:
    registry = load_default_runbook_registry(REPO_ROOT)

    assert registry.runbook_ids == tuple(
        sorted(RunbookIdV21, key=lambda item: item.value)
    )
    assert registry.require(RunbookIdV21.MITIGATE_CPU_SATURATION).risk_level is (
        RiskLevelV21.LOW
    )
    assert registry.require(RunbookIdV21.RESTORE_DEPENDENCY_LATENCY).backend is (
        RunbookBackendV21.REPLAY_ONLY
    )
    assert registry.require(
        RunbookIdV21.RESTORE_SERVICE_AVAILABILITY
    ).target_services == ("email", "product-catalog")
    availability = registry.require(RunbookIdV21.RESTORE_SERVICE_AVAILABILITY)
    assert tuple(
        source.value for source in availability.required_evidence_for_target("email")
    ) == ("METRICS", "RUNTIME")
    assert tuple(
        source.value
        for source in availability.required_evidence_for_target("product-catalog")
    ) == ("TRACES", "RUNTIME")
    assert registry.registry_sha256 == (
        "02bbcddba67da53c10324624dc770c9f73056e0126469567c8e70a79710047e9"
    )
    for runbook in registry.runbooks:
        assert runbook.target_services
        assert runbook.required_evidence_sources
        assert runbook.preconditions
        assert len(runbook.forward_steps) == runbook.maximum_forward_steps
        assert runbook.executor_id
        assert runbook.verifier_id
        assert len(runbook.semantic_sha256) == 64

    tampered = registry.runbooks[0].model_dump(mode="python")
    tampered["executor_id"] = "TamperedExecutorV21"
    with pytest.raises(ValidationError, match="semantic hash"):
        RunbookSpecV21.model_validate(tampered)


def test_scenario_truth_is_separate_from_observer_registry() -> None:
    observer, evaluator, anchors = load_default_scenario_registries(REPO_ROOT)

    assert observer.scenario_ids == tuple(
        f"dta21-dev-{index:03d}" for index in range(1, 7)
    )
    assert evaluator.scenario_ids == observer.scenario_ids
    assert len(anchors.anchors) == 4
    assert observer.registry_sha256 == (
        "632835d1a96a260064704e9a3e0ae193c61ed696115c92c81fa9a1905a3f8621"
    )
    assert evaluator.registry_sha256 == (
        "760f1125352c0bcce885cbbb874a638efe943362773a37a0a9031f4f07527bc9"
    )
    assert anchors.registry_sha256 == (
        "61d1d2d8ec10f11d0f84bc138692ce13dfbc30d087f9f6ad8019b46b3b4ebac0"
    )
    for scenario in observer.scenarios:
        payload = scenario.model_dump(mode="json")
        assert "fault_domain" not in payload
        assert "fault_mechanism" not in payload
        assert "expected_runbook" not in payload
        assert "held_out" not in payload
    first_alert = observer.scenarios[0].alert_summary.casefold()
    assert "cpu" not in first_alert
    assert "adhighcpu" not in first_alert.replace("_", "")
    assert "expected" not in first_alert
