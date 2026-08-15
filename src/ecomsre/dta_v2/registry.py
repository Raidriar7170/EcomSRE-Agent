"""Read-only loaders for the frozen v2 scenario and runbook registries."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from ecomsre.dta_v2.contracts import (
    DtaModel,
    RiskLevel,
    RunbookId,
    RunbookSpec,
    ScenarioSpec,
    semantic_sha256,
)


_MVP_RUNBOOK_IDS = tuple(sorted(RunbookId, key=lambda item: item.value))
_MVP_SCENARIO_IDS = ("dta-dev-001", "dta-dev-002", "dta-dev-003")
_MVP_RUNBOOK_SHA256 = {
    RunbookId.MITIGATE_MEMORY_LEAK: (
        "870cde9e39d2685c41f0fa36aedbc09af4b1849c29725c4302da501dbc47664c"
    ),
    RunbookId.RESTART_SERVICE: (
        "f70130a4f7398caf034a1849af4823778a82aac784e2d87799474859e36e7d83"
    ),
    RunbookId.ROLLBACK_CONFIGURATION: (
        "b9132c522e81e673c1b765e5f28d1687d1ea3384e085bac9fa1bc5d571130481"
    ),
}
_MVP_SCENARIO_SHA256 = {
    "dta-dev-001": "6893cb923545bf95b5da937f8faeb0fea29708645dfd757e7a1b473b31375492",
    "dta-dev-002": "2330a017630eaba302d30ac892646800359cb330358ca33852892717ec377606",
    "dta-dev-003": "c1826afbae913b726dc25fdc8e5d14ed7ccf3c32c6a4d9b3805faf8394f04b09",
}


class RunbookRegistry(DtaModel):
    runbooks: tuple[RunbookSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_frozen_mvp_catalog(self) -> RunbookRegistry:
        ids = tuple(runbook.runbook_id for runbook in self.runbooks)
        if any(runbook.risk_level is RiskLevel.HIGH for runbook in self.runbooks):
            raise ValueError("HIGH risk runbooks are outside the v2 MVP")
        if len(ids) != len(set(ids)):
            raise ValueError("runbook registry contains duplicate IDs")
        if ids != tuple(sorted(ids, key=lambda item: item.value)):
            raise ValueError("runbook registry is not canonically ordered")
        if ids != _MVP_RUNBOOK_IDS:
            raise ValueError("runbook registry does not contain the exact MVP set")
        for runbook in self.runbooks:
            observed = semantic_sha256(runbook.model_dump(mode="json"))
            if observed != _MVP_RUNBOOK_SHA256[runbook.runbook_id]:
                raise ValueError("runbook differs from the frozen MVP contract")
        return self

    @property
    def runbook_ids(self) -> tuple[RunbookId, ...]:
        return tuple(runbook.runbook_id for runbook in self.runbooks)

    def require(self, runbook_id: RunbookId) -> RunbookSpec:
        for runbook in self.runbooks:
            if runbook.runbook_id is runbook_id:
                return runbook
        raise KeyError(runbook_id.value)


class ScenarioRegistry(DtaModel):
    scenarios: tuple[ScenarioSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_frozen_mvp_scenarios(self) -> ScenarioRegistry:
        ids = tuple(scenario.scenario_id for scenario in self.scenarios)
        if len(ids) != len(set(ids)):
            raise ValueError("scenario registry contains duplicate IDs")
        if ids != tuple(sorted(ids)):
            raise ValueError("scenario registry is not canonically ordered")
        if ids != _MVP_SCENARIO_IDS:
            raise ValueError("scenario registry does not contain the exact MVP set")
        for scenario in self.scenarios:
            observed = semantic_sha256(scenario.model_dump(mode="json"))
            if observed != _MVP_SCENARIO_SHA256[scenario.scenario_id]:
                raise ValueError(
                    "scenario differs from the frozen agent-visible contract"
                )
        return self

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(scenario.scenario_id for scenario in self.scenarios)


def _contract_files(directory: Path) -> tuple[Path, ...]:
    if directory.is_symlink():
        raise ValueError("registry directory must not be a symlink")
    if not directory.is_dir():
        raise ValueError("registry directory does not exist")
    entries = tuple(sorted(directory.iterdir(), key=lambda path: path.name))
    if not entries:
        raise ValueError("registry directory is empty")
    for entry in entries:
        if entry.is_symlink():
            raise ValueError("registry contract file must not be a symlink")
        if not entry.is_file() or entry.suffix != ".json":
            raise ValueError("registry contains a non-JSON contract entry")
    return entries


def load_runbook_registry(directory: Path) -> RunbookRegistry:
    runbooks = tuple(
        RunbookSpec.model_validate_json(path.read_text(encoding="utf-8"))
        for path in _contract_files(directory)
    )
    return RunbookRegistry(runbooks=runbooks)


def load_scenario_registry(directory: Path) -> ScenarioRegistry:
    scenarios = tuple(
        ScenarioSpec.model_validate_json(path.read_text(encoding="utf-8"))
        for path in _contract_files(directory)
    )
    return ScenarioRegistry(scenarios=scenarios)
