from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.contracts import (
    FaultMechanism,
    RiskLevel,
    RunbookId,
    ScenarioSpec,
)
from ecomsre.dta_v2.registry import (
    load_runbook_registry,
    load_scenario_registry,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "config" / "dta-v2"


def test_shipped_runbook_registry_freezes_three_mvp_runbooks() -> None:
    registry = load_runbook_registry(CONFIG_ROOT / "runbooks")

    assert registry.schema_version == "dta-v2.runbook-registry.v1"
    assert len(registry.registry_sha256) == 64
    assert registry.runbook_ids == (
        RunbookId.MITIGATE_MEMORY_LEAK,
        RunbookId.RESTART_SERVICE,
        RunbookId.ROLLBACK_CONFIGURATION,
    )

    payment = registry.require(RunbookId.ROLLBACK_CONFIGURATION)
    recommendation = registry.require(RunbookId.RESTART_SERVICE)
    email = registry.require(RunbookId.MITIGATE_MEMORY_LEAK)

    assert payment.risk_level is RiskLevel.LOW
    assert payment.maximum_forward_steps == 1
    assert payment.partial_failure_policy is None
    assert payment.supported_mechanisms == (
        FaultMechanism.CONFIGURATION_ERROR,
    )
    assert recommendation.risk_level is RiskLevel.LOW
    assert recommendation.maximum_forward_steps == 1
    assert recommendation.partial_failure_policy is None
    assert email.risk_level is RiskLevel.MEDIUM
    assert email.maximum_forward_steps == 2
    assert email.partial_failure_policy is not None

    forged = registry.model_dump(mode="python")
    forged["registry_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="registry digest"):
        type(registry).model_validate(forged)


def test_shipped_scenario_registry_is_agent_visible_and_four_call_bounded() -> None:
    registry = load_scenario_registry(CONFIG_ROOT / "scenarios" / "agent-visible")

    assert registry.scenario_ids == (
        "dta-dev-001",
        "dta-dev-002",
        "dta-dev-003",
    )
    for scenario in registry.scenarios:
        assert scenario.maximum_read_tool_dispatches == 4
        assert scenario.maximum_repeated_identical_calls == 0
        assert len(scenario.allowed_read_tools) == 5
        serialized = json.dumps(scenario.model_dump(mode="json"), sort_keys=True)
        assert "expected_root" not in serialized
        assert "expected_mechanism" not in serialized
        assert "expected_runbook" not in serialized
        assert "injected_fault" not in serialized


def test_registry_rejects_symlinked_contract_files(tmp_path: Path) -> None:
    source = CONFIG_ROOT / "runbooks" / "restart-service.json"
    (tmp_path / "restart-service.json").symlink_to(source)

    with pytest.raises(ValueError, match="symlink"):
        load_runbook_registry(tmp_path)


def test_scenario_registry_rejects_evaluator_only_fields(tmp_path: Path) -> None:
    payload = json.loads(
        (CONFIG_ROOT / "scenarios" / "agent-visible" / "dta-dev-001.json")
        .read_text(encoding="utf-8")
    )
    payload["expected_mechanism"] = "CONFIGURATION_ERROR"
    (tmp_path / "scenario.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_mechanism|extra"):
        load_scenario_registry(tmp_path)


def test_registry_rejects_unfrozen_or_high_risk_catalog(tmp_path: Path) -> None:
    source = CONFIG_ROOT / "runbooks" / "restart-service.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["risk_level"] = "HIGH"
    (tmp_path / "restart-service.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="HIGH risk"):
        load_runbook_registry(tmp_path)


def test_registry_rejects_runbook_matrix_drift(tmp_path: Path) -> None:
    source_root = CONFIG_ROOT / "runbooks"
    for source in source_root.iterdir():
        payload = json.loads(source.read_text(encoding="utf-8"))
        if source.name == "restart-service.json":
            payload["executor_id"] = "OtherBoundedExecutor"
        (tmp_path / source.name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="frozen MVP contract"):
        load_runbook_registry(tmp_path)


@pytest.mark.parametrize("field", ["parameters", "preconditions"])
def test_registry_rejects_runbook_contract_content_drift(
    tmp_path: Path,
    field: str,
) -> None:
    source_root = CONFIG_ROOT / "runbooks"
    for source in source_root.iterdir():
        payload = json.loads(source.read_text(encoding="utf-8"))
        if source.name == "restart-service.json":
            if field == "parameters":
                payload[field][0]["maximum"] = 119
            else:
                payload[field] = payload[field][:-1]
        (tmp_path / source.name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="frozen MVP contract"):
        load_runbook_registry(tmp_path)


def test_registry_rejects_agent_visible_scenario_content_drift(tmp_path: Path) -> None:
    source_root = CONFIG_ROOT / "scenarios" / "agent-visible"
    for source in source_root.iterdir():
        payload = json.loads(source.read_text(encoding="utf-8"))
        if source.name == "dta-dev-001.json":
            payload["alert_summary"] = "Checkout failures increased in another window."
        (tmp_path / source.name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="frozen agent-visible contract"):
        load_scenario_registry(tmp_path)


def test_scenario_contract_rejects_known_control_marker(tmp_path: Path) -> None:
    source_root = CONFIG_ROOT / "scenarios" / "agent-visible"
    for source in source_root.iterdir():
        payload = json.loads(source.read_text(encoding="utf-8"))
        if source.name == "dta-dev-001.json":
            payload["alert_summary"] = (
                "paymentFailure.defaultVariant is the injected configuration key."
            )
        (tmp_path / source.name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="scenario-control marker"):
        load_scenario_registry(tmp_path)


@pytest.mark.parametrize(
    "scenario_name",
    ["dta-dev-001.json", "dta-dev-002.json", "dta-dev-003.json"],
)
@pytest.mark.parametrize(
    "leaked_control_text",
    [
        "paymentFailure is the active scenario control.",
        "paymentFailure.defaultVariant is the injected key.",
        "defaultVariant is the selected injection variant.",
        "emailMemoryLeak is active.",
        "expected_root is payment.",
        "expected_mechanism is CONFIGURATION_ERROR.",
        "expected_runbook is ROLLBACK_CONFIGURATION.",
        "executor and verifier are evaluator-selected.",
        "The injected fault is evaluator controlled.",
        "Run docker stop recommendation.",
    ],
)
def test_each_mvp_scenario_rejects_evaluator_control_leakage(
    scenario_name: str,
    leaked_control_text: str,
) -> None:
    payload = json.loads(
        (CONFIG_ROOT / "scenarios" / "agent-visible" / scenario_name).read_text(
            encoding="utf-8"
        )
    )
    payload["alert_summary"] = leaked_control_text

    with pytest.raises(
        ValueError,
        match="evaluator marker|scenario-control marker|executable text",
    ):
        ScenarioSpec.model_validate_json(json.dumps(payload))
