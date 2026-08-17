"""Trusted loaders for the DTA v2.1 Runbook and scenario registries."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v21.contracts import (
    CrossedMatrixReportV21,
    DtaModelV21,
    EvidenceSourceV21,
    FaultMechanismV21,
    LegacyDevelopmentAnchorV21,
    RunbookIdV21,
    RunbookSpecV21,
    ScenarioEvaluationContractV21,
    ScenarioSpecV21,
    Sha256V21,
    TerminalV21,
    semantic_sha256,
)


_RUNBOOK_IDS = tuple(sorted(RunbookIdV21, key=lambda item: item.value))
_SCENARIO_IDS = tuple(f"dta21-dev-{index:03d}" for index in range(1, 7))
_AGENT_VISIBLE_DIR = Path("config/dta-v21/scenarios/agent-visible")
_EVALUATOR_DIR = Path("config/dta-v21/scenarios/evaluator-contract")
_LEGACY_ANCHORS_FILE = Path("config/dta-v21/scenarios/legacy-anchors.v1.json")
_RUNBOOK_DIR = Path("config/dta-v21/runbooks")


class RegistryError(ValueError):
    """Fail-closed registry loading or matrix validation error."""


class RunbookRegistryV21(DtaModelV21):
    schema_version: Literal["dta-v21.runbook-registry.v1"]
    runbooks: tuple[RunbookSpecV21, ...] = Field(min_length=1)
    registry_sha256: Sha256V21

    @model_validator(mode="after")
    def require_exact_catalog_and_digest(self) -> RunbookRegistryV21:
        ids = tuple(item.runbook_id for item in self.runbooks)
        if ids != _RUNBOOK_IDS:
            raise ValueError("runbook registry does not contain the exact P0 catalog")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("registry digest does not bind the Runbook catalog")
        return self

    @property
    def runbook_ids(self) -> tuple[RunbookIdV21, ...]:
        return tuple(item.runbook_id for item in self.runbooks)

    def require(self, runbook_id: RunbookIdV21) -> RunbookSpecV21:
        for runbook in self.runbooks:
            if runbook.runbook_id is runbook_id:
                return runbook
        raise KeyError(runbook_id.value)


class ScenarioRegistryV21(DtaModelV21):
    schema_version: Literal["dta-v21.scenario-registry.v1"]
    scenarios: tuple[ScenarioSpecV21, ...] = Field(min_length=1)
    registry_sha256: Sha256V21

    @model_validator(mode="after")
    def require_exact_catalog_and_digest(self) -> ScenarioRegistryV21:
        ids = tuple(item.scenario_id for item in self.scenarios)
        if ids != _SCENARIO_IDS:
            raise ValueError("observer scenario registry differs from the exact P0 set")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("observer registry digest does not bind the catalog")
        return self

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(item.scenario_id for item in self.scenarios)


class ScenarioEvaluationRegistryV21(DtaModelV21):
    schema_version: Literal["dta-v21.scenario-evaluation-registry.v1"]
    scenarios: tuple[ScenarioEvaluationContractV21, ...] = Field(min_length=1)
    registry_sha256: Sha256V21

    @model_validator(mode="after")
    def require_exact_catalog_and_digest(self) -> ScenarioEvaluationRegistryV21:
        ids = tuple(item.scenario_id for item in self.scenarios)
        if ids != _SCENARIO_IDS:
            raise ValueError(
                "evaluator scenario registry differs from the exact P0 set"
            )
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("evaluator registry digest does not bind the catalog")
        return self

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return tuple(item.scenario_id for item in self.scenarios)


class LegacyDevelopmentAnchorRegistryV21(DtaModelV21):
    schema_version: Literal["dta-v21.legacy-development-anchor-registry.v1"]
    anchors: tuple[LegacyDevelopmentAnchorV21, ...] = Field(min_length=1)
    registry_sha256: Sha256V21

    @model_validator(mode="after")
    def require_canonical_anchors_and_digest(
        self,
    ) -> LegacyDevelopmentAnchorRegistryV21:
        ids = tuple(item.anchor_id for item in self.anchors)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("legacy anchors are not canonical and unique")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("legacy anchor digest does not bind the registry")
        return self


def _json_files(directory: Path) -> tuple[Path, ...]:
    if directory.is_symlink():
        raise RegistryError("registry directory must not be a symlink")
    if not directory.is_dir():
        raise RegistryError(f"registry directory does not exist: {directory}")
    entries = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
    if not entries:
        raise RegistryError("registry directory is empty")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file() or entry.suffix != ".json":
            raise RegistryError("registry contains an unsafe or non-JSON entry")
    return entries


def _read_text(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RegistryError(f"registry file is missing or unsafe: {path}")
    return path.read_text(encoding="utf-8")


def load_runbook_registry(directory: Path) -> RunbookRegistryV21:
    runbooks = tuple(
        sorted(
            (
                RunbookSpecV21.model_validate_json(_read_text(path))
                for path in _json_files(directory)
            ),
            key=lambda item: item.runbook_id.value,
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.runbook-registry.v1",
        "runbooks": runbooks,
    }
    digest_payload = {
        **payload,
        "runbooks": [item.model_dump(mode="json") for item in runbooks],
    }
    return RunbookRegistryV21.model_validate(
        {**payload, "registry_sha256": semantic_sha256(digest_payload)}
    )


def _load_scenario_registry(directory: Path) -> ScenarioRegistryV21:
    scenarios = tuple(
        sorted(
            (
                ScenarioSpecV21.model_validate_json(_read_text(path))
                for path in _json_files(directory)
            ),
            key=lambda item: item.scenario_id,
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.scenario-registry.v1",
        "scenarios": scenarios,
    }
    digest_payload = {
        **payload,
        "scenarios": [item.model_dump(mode="json") for item in scenarios],
    }
    return ScenarioRegistryV21.model_validate(
        {**payload, "registry_sha256": semantic_sha256(digest_payload)}
    )


def _load_evaluator_registry(directory: Path) -> ScenarioEvaluationRegistryV21:
    scenarios = tuple(
        sorted(
            (
                ScenarioEvaluationContractV21.model_validate_json(_read_text(path))
                for path in _json_files(directory)
            ),
            key=lambda item: item.scenario_id,
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.scenario-evaluation-registry.v1",
        "scenarios": scenarios,
    }
    digest_payload = {
        **payload,
        "scenarios": [item.model_dump(mode="json") for item in scenarios],
    }
    return ScenarioEvaluationRegistryV21.model_validate(
        {**payload, "registry_sha256": semantic_sha256(digest_payload)}
    )


def _load_legacy_anchors(
    *, repository_root: Path, path: Path
) -> LegacyDevelopmentAnchorRegistryV21:
    payload = LegacyDevelopmentAnchorRegistryV21.model_validate_json(_read_text(path))
    for anchor in payload.anchors:
        source = repository_root / anchor.source_path
        if source.is_symlink() or not source.is_file():
            raise RegistryError("legacy anchor source is missing or unsafe")
        observed = hashlib.sha256(source.read_bytes()).hexdigest()
        if observed != anchor.source_sha256:
            raise RegistryError("legacy DTA v2 anchor bytes changed")
    return payload


def load_default_runbook_registry(repository_root: Path) -> RunbookRegistryV21:
    return load_runbook_registry(repository_root / _RUNBOOK_DIR)


def load_default_scenario_registries(
    repository_root: Path,
) -> tuple[
    ScenarioRegistryV21,
    ScenarioEvaluationRegistryV21,
    LegacyDevelopmentAnchorRegistryV21,
]:
    return (
        _load_scenario_registry(repository_root / _AGENT_VISIBLE_DIR),
        _load_evaluator_registry(repository_root / _EVALUATOR_DIR),
        _load_legacy_anchors(
            repository_root=repository_root,
            path=repository_root / _LEGACY_ANCHORS_FILE,
        ),
    )


def validate_crossed_matrix(
    *,
    observer_registry: ScenarioRegistryV21,
    evaluator_registry: ScenarioEvaluationRegistryV21,
    legacy_anchors: LegacyDevelopmentAnchorRegistryV21,
) -> CrossedMatrixReportV21:
    if observer_registry.scenario_ids != evaluator_registry.scenario_ids:
        raise RegistryError("observer and evaluator scenario IDs differ")

    typed_cases = tuple(evaluator_registry.scenarios) + tuple(legacy_anchors.anchors)
    service_mechanisms: dict[str, set[FaultMechanismV21]] = {}
    for item in typed_cases:
        if item.root_service is not None and item.fault_mechanism is not None:
            service_mechanisms.setdefault(item.root_service, set()).add(
                item.fault_mechanism
            )

    unavailable_services = {
        item.root_service
        for item in typed_cases
        if item.fault_mechanism is FaultMechanismV21.SERVICE_UNAVAILABLE
        and item.root_service is not None
    }
    legacy_services = {item.root_service for item in legacy_anchors.anchors}
    legacy_mechanisms = {
        item.fault_mechanism
        for item in legacy_anchors.anchors
        if item.fault_mechanism is not None
    }
    observer_pairs = [
        set(left.candidate_services).intersection(right.candidate_services)
        for index, left in enumerate(observer_registry.scenarios)
        for right in observer_registry.scenarios[index + 1 :]
    ]
    visible_text = " ".join(
        item.alert_summary.casefold() for item in observer_registry.scenarios
    )
    control_tokens = ("cpu_saturation", "dependency_latency", "service_unavailable")

    checks = {
        "email_has_multiple_mechanisms": len(service_mechanisms.get("email", set()))
        >= 2,
        "missing_or_conflicting_case": any(
            item.terminal in {TerminalV21.NEED_MORE_EVIDENCE, TerminalV21.ABSTAIN}
            for item in evaluator_registry.scenarios
        ),
        "new_mechanism_on_new_service": any(
            item.root_service not in legacy_services
            and item.fault_mechanism not in legacy_mechanisms
            for item in evaluator_registry.scenarios
            if item.root_service is not None and item.fault_mechanism is not None
        ),
        "new_service_known_mechanism": any(
            item.root_service not in legacy_services
            and item.fault_mechanism in legacy_mechanisms
            for item in evaluator_registry.scenarios
            if item.root_service is not None and item.fault_mechanism is not None
        ),
        "no_shortcut_control_tokens_in_alerts": not any(
            token in visible_text for token in control_tokens
        ),
        "no_write_case": any(
            item.forward_writes == 0 and item.expected_runbook is None
            for item in evaluator_registry.scenarios
        ),
        "overlapping_candidate_sets": any(
            len(overlap) >= 2 for overlap in observer_pairs
        ),
        "resource_required_cpu_case": any(
            item.fault_mechanism is FaultMechanismV21.CPU_SATURATION
            and EvidenceSourceV21.RESOURCES in item.required_evaluator_evidence
            for item in evaluator_registry.scenarios
        ),
        "service_unavailable_on_three_services": len(unavailable_services) >= 3,
        "traces_required_dependency_case": any(
            item.fault_mechanism is FaultMechanismV21.DEPENDENCY_LATENCY
            and EvidenceSourceV21.TRACES in item.required_evaluator_evidence
            for item in evaluator_registry.scenarios
        ),
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    if failed:
        raise RegistryError(f"crossed matrix failed: {', '.join(failed)}")
    ordered_checks = dict(sorted(checks.items()))
    payload: dict[str, object] = {
        "schema_version": "dta-v21.crossed-matrix-report.v1",
        "status": "PASS",
        "checks": ordered_checks,
    }
    return CrossedMatrixReportV21.model_validate(
        {**payload, "report_sha256": semantic_sha256(payload)}
    )


__all__ = (
    "LegacyDevelopmentAnchorRegistryV21",
    "RegistryError",
    "RunbookRegistryV21",
    "ScenarioEvaluationRegistryV21",
    "ScenarioRegistryV21",
    "load_default_runbook_registry",
    "load_default_scenario_registries",
    "load_runbook_registry",
    "validate_crossed_matrix",
)
