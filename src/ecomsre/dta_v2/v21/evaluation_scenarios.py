"""Truth-free scenario catalog used by the frozen v2.1 evaluation cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.tool_contracts import ToolName
from ecomsre.dta_v2.v21.contracts import (
    DtaModelV21,
    ScenarioSpecV21,
    Sha256V21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.registry import load_default_scenario_registries


class EvaluationScenarioRegistryV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-scenario-registry.v1"]
    scenarios: tuple[ScenarioSpecV21, ...] = Field(min_length=7, max_length=7)
    registry_sha256: Sha256V21

    @model_validator(mode="after")
    def require_registry(self) -> EvaluationScenarioRegistryV21:
        ids = tuple(item.scenario_id for item in self.scenarios)
        expected_ids = tuple(
            sorted(
                (
                    *[f"dta21-dev-{index:03d}" for index in range(1, 7)],
                    "dta21-legacy-recommendation",
                )
            )
        )
        if ids != expected_ids:
            raise ValueError("evaluation scenario registry differs from exact catalog")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("evaluation scenario registry digest differs")
        return self

    def require(self, scenario_id: str) -> ScenarioSpecV21:
        for scenario in self.scenarios:
            if scenario.scenario_id == scenario_id:
                return scenario
        raise KeyError(scenario_id)


def build_evaluation_scenario_registry_v21(
    repository_root: Path,
) -> EvaluationScenarioRegistryV21:
    base, _, _ = load_default_scenario_registries(repository_root)
    legacy_recommendation = ScenarioSpecV21(
        schema_version="dta-v21.scenario.v1",
        scenario_id="dta21-legacy-recommendation",
        alert_summary=(
            "Product suggestions stopped appearing during the bounded customer "
            "journey window."
        ),
        candidate_services=("frontend", "recommendation"),
        allowed_read_tools=tuple(ToolName),
        maximum_read_tool_dispatches=4,
        maximum_repeated_identical_calls=0,
    )
    scenarios = tuple(
        sorted(
            (*base.scenarios, legacy_recommendation), key=lambda item: item.scenario_id
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evaluation-scenario-registry.v1",
        "scenarios": scenarios,
    }
    draft = cast(Any, EvaluationScenarioRegistryV21).model_construct(
        **payload, registry_sha256="0" * 64
    )
    return EvaluationScenarioRegistryV21.model_validate(
        {
            **payload,
            "registry_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"registry_sha256"})
            ),
        }
    )


__all__ = (
    "EvaluationScenarioRegistryV21",
    "build_evaluation_scenario_registry_v21",
)
