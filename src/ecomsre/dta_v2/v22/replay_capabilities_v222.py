"""Replay capture availability and source-aware action masking for v2.2.2."""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
from typing import Literal, Mapping, cast

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import (
    ActionCatalogV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseModifierV22,
    PracticalCaseSpecV22,
    load_synthetic_evaluation_source_v222,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)


_SOURCE_BY_CAPTURE_TOOL_V222 = {
    "query_metrics": EvidenceSourceV22.METRICS,
    "search_logs": EvidenceSourceV22.LOGS,
    "query_trace_neighborhood": EvidenceSourceV22.TRACES,
    "inspect_service_runtime": EvidenceSourceV22.RUNTIME,
    "inspect_resource_usage": EvidenceSourceV22.RESOURCES,
    "inspect_recent_changes": EvidenceSourceV22.CHANGES,
}


class ReplaySourceAvailabilityV222(str, Enum):
    CAPTURED = "CAPTURED"
    NOT_CAPTURED = "NOT_CAPTURED"


class ReplaySourceCapabilityV222(DtaModelV22):
    source: EvidenceSourceV22
    availability: ReplaySourceAvailabilityV222
    outcome_preview_exposed: Literal[False] = False


class ReplayCapabilitiesV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.replay-capabilities.v1"]
    sources: tuple[ReplaySourceCapabilityV222, ...]
    derived_from_capture_metadata: StrictBool
    capabilities_sha256: str

    @model_validator(mode="after")
    def require_capabilities(self) -> "ReplayCapabilitiesV222":
        if tuple(item.source for item in self.sources) != tuple(EvidenceSourceV22):
            raise ValueError("replay capabilities are incomplete or noncanonical")
        if any(item.outcome_preview_exposed for item in self.sources):
            raise ValueError("replay capability leaks future read outcome")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"capabilities_sha256"})
        )
        if self.capabilities_sha256 != expected:
            raise ValueError("replay capabilities digest differs")
        return self

    def require(self, source: EvidenceSourceV22) -> ReplaySourceCapabilityV222:
        return next(item for item in self.sources if item.source is source)


def captured_sources_from_case_spec_v222(
    *, spec: PracticalCaseSpecV22, repository_root: Path
) -> tuple[EvidenceSourceV22, ...]:
    """Read only capture/tool presence; never inspect record count or yield."""

    if spec.source_path is None:
        # The existing inline payment fixture declares precisely these captured
        # sources in its derivation contract.  Empty future outcomes are not used.
        captured = {
            EvidenceSourceV22.METRICS,
            EvidenceSourceV22.TRACES,
            EvidenceSourceV22.RUNTIME,
        }
    elif spec.modifier is PracticalCaseModifierV22.V222_EVALUATION_FIXTURE:
        captured = set(
            load_synthetic_evaluation_source_v222(
                spec=spec,
                repository_root=repository_root,
            ).captured_sources
        )
    else:
        raw = cast(
            Mapping[str, object],
            json.loads((repository_root / spec.source_path).read_bytes()),
        )
        observations = raw.get("observations")
        if not isinstance(observations, list):
            raise ValueError("legacy replay lacks capture metadata")
        captured = set()
        for item in observations:
            if not isinstance(item, Mapping):
                raise ValueError("legacy replay capture metadata is invalid")
            tool = item.get("tool")
            if not isinstance(tool, str) or tool not in _SOURCE_BY_CAPTURE_TOOL_V222:
                raise ValueError("legacy replay capture uses an unknown read tool")
            captured.add(_SOURCE_BY_CAPTURE_TOOL_V222[tool])
    return tuple(source for source in EvidenceSourceV22 if source in captured)


def build_replay_capabilities_v222(
    *, spec: PracticalCaseSpecV22, repository_root: Path
) -> ReplayCapabilitiesV222:
    captured = set(
        captured_sources_from_case_spec_v222(
            spec=spec,
            repository_root=repository_root,
        )
    )
    sources = tuple(
        ReplaySourceCapabilityV222(
            source=source,
            availability=(
                ReplaySourceAvailabilityV222.CAPTURED
                if source in captured
                else ReplaySourceAvailabilityV222.NOT_CAPTURED
            ),
        )
        for source in EvidenceSourceV22
    )
    digest_payload = {
        "schema_version": "dta-v22.2.replay-capabilities.v1",
        "sources": tuple(item.model_dump(mode="json") for item in sources),
        "derived_from_capture_metadata": True,
    }
    return ReplayCapabilitiesV222(
        schema_version="dta-v22.2.replay-capabilities.v1",
        sources=sources,
        derived_from_capture_metadata=True,
        capabilities_sha256=semantic_sha256_v22(digest_payload),
    )


def build_source_aware_action_catalog_v222(
    *,
    candidate_services: tuple[str, ...],
    topology: StaticTopologyV22,
    replay_capabilities: ReplayCapabilitiesV222,
    executed_action_ids: tuple[str, ...],
    remaining_budget: float,
    covered_capability_keys: tuple[str, ...] = (),
) -> ActionCatalogV22:
    replay_capabilities = ReplayCapabilitiesV222.model_validate(
        replay_capabilities.model_dump(mode="python")
    )
    disabled = tuple(
        item.source
        for item in replay_capabilities.sources
        if item.availability is ReplaySourceAvailabilityV222.NOT_CAPTURED
    )
    return build_action_catalog_v22(
        candidate_services=candidate_services,
        topology=topology,
        capability_registry=build_tool_capability_registry_v22(
            disabled_sources=disabled
        ),
        executed_action_ids=executed_action_ids,
        covered_capability_keys=covered_capability_keys,
        remaining_budget=remaining_budget,
    )


__all__ = (
    "ReplayCapabilitiesV222",
    "ReplaySourceAvailabilityV222",
    "ReplaySourceCapabilityV222",
    "build_replay_capabilities_v222",
    "build_source_aware_action_catalog_v222",
    "captured_sources_from_case_spec_v222",
)
