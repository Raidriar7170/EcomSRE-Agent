"""Truth-independent canonical action catalog and dynamic action mask."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictFloat, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    ActionIdV22,
    CanonicalReadRequestV22,
    DtaModelV22,
    EvidenceSourceV22,
    LogicalServiceV22,
    MetricKindV22,
    Sha256V22,
    build_canonical_read_request_v22,
    semantic_sha256_v22,
)


_CANONICAL_COST_BY_SOURCE = {
    EvidenceSourceV22.METRICS: 1.0,
    EvidenceSourceV22.LOGS: 1.0,
    EvidenceSourceV22.TRACES: 1.5,
    EvidenceSourceV22.RUNTIME: 0.5,
    EvidenceSourceV22.RESOURCES: 1.5,
    EvidenceSourceV22.CHANGES: 0.75,
}


def _canonical_weighted_cost(
    source: EvidenceSourceV22,
    targets: tuple[str, ...],
) -> float:
    base = _CANONICAL_COST_BY_SOURCE[source]
    if source is EvidenceSourceV22.RUNTIME and len(targets) > 1:
        return base * 2
    if source is EvidenceSourceV22.RESOURCES and len(targets) > 1:
        return min(3.0, base + 0.5 * (len(targets) - 1))
    return base


class StaticTopologyV22(DtaModelV22):
    schema_version: Literal["dta-v22.static-topology.v1"]
    services: tuple[LogicalServiceV22, ...] = Field(min_length=1, max_length=64)
    edges: tuple[tuple[LogicalServiceV22, LogicalServiceV22], ...] = Field(
        max_length=128
    )
    topology_sha256: Sha256V22

    @classmethod
    def build(
        cls,
        *,
        services: tuple[str, ...],
        edges: tuple[tuple[str, str], ...],
    ) -> StaticTopologyV22:
        canonical_services = tuple(sorted(item.strip() for item in services))
        canonical_edges = tuple(
            sorted(
                tuple(sorted((left.strip(), right.strip())))
                for left, right in edges
            )
        )
        payload: dict[str, object] = {
            "schema_version": "dta-v22.static-topology.v1",
            "services": canonical_services,
            "edges": canonical_edges,
        }
        return cls.model_validate(
            {**payload, "topology_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_topology(self) -> StaticTopologyV22:
        if self.services != tuple(sorted(set(self.services))):
            raise ValueError("topology services are not sorted and unique")
        if self.edges != tuple(sorted(set(self.edges))):
            raise ValueError("topology edges are not sorted and unique")
        service_set = set(self.services)
        for left, right in self.edges:
            if left >= right or left not in service_set or right not in service_set:
                raise ValueError("topology edge is invalid or noncanonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"topology_sha256"})
        )
        if self.topology_sha256 != expected:
            raise ValueError("topology digest does not bind topology")
        return self


class ToolCapabilityV22(DtaModelV22):
    source: EvidenceSourceV22
    enabled: StrictBool
    weighted_cost: StrictFloat = Field(gt=0, le=10)


class ToolCapabilityRegistryV22(DtaModelV22):
    schema_version: Literal["dta-v22.tool-capability-registry.v1"]
    capabilities: tuple[ToolCapabilityV22, ...]
    registry_sha256: Sha256V22

    @model_validator(mode="after")
    def require_registry(self) -> ToolCapabilityRegistryV22:
        sources = tuple(item.source for item in self.capabilities)
        if sources != tuple(EvidenceSourceV22):
            raise ValueError("tool capability registry is incomplete or noncanonical")
        if any(
            item.weighted_cost != _CANONICAL_COST_BY_SOURCE[item.source]
            for item in self.capabilities
        ):
            raise ValueError("tool capability registry has an unversioned cost")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("capability registry digest does not bind registry")
        return self

    def require(self, source: EvidenceSourceV22) -> ToolCapabilityV22:
        return next(item for item in self.capabilities if item.source is source)


class EvidenceActionV22(DtaModelV22):
    schema_version: Literal["dta-v22.evidence-action.v1"]
    action_id: ActionIdV22
    source: EvidenceSourceV22
    target_services: tuple[LogicalServiceV22, ...]
    request: CanonicalReadRequestV22
    coverage_keys: tuple[str, ...]
    weighted_cost: StrictFloat = Field(gt=0, le=10)
    request_sha256: Sha256V22
    dominates_action_ids: tuple[ActionIdV22, ...]
    action_sha256: Sha256V22

    @model_validator(mode="after")
    def require_action_binding(self) -> EvidenceActionV22:
        if self.target_services != self.request.target_services:
            raise ValueError("action targets differ from canonical request")
        if self.source is not self.request.source:
            raise ValueError("action source differs from canonical request")
        if self.request_sha256 != self.request.request_sha256:
            raise ValueError("action request digest differs from canonical request")
        if self.request != _request_for(self.source, self.target_services):
            raise ValueError("action request differs from versioned canonical parameters")
        if self.weighted_cost != _canonical_weighted_cost(
            self.source,
            self.target_services,
        ):
            raise ValueError("action differs from versioned weighted cost")
        if self.coverage_keys != tuple(sorted(set(self.coverage_keys))):
            raise ValueError("action coverage keys are not sorted and unique")
        if self.coverage_keys != _coverage_keys(self.source, self.target_services):
            raise ValueError("action coverage differs from canonical coverage")
        if self.dominates_action_ids != tuple(sorted(set(self.dominates_action_ids))):
            raise ValueError("dominated action IDs are not sorted and unique")
        if self.dominates_action_ids != _canonical_dominates_action_ids(
            self.source,
            self.target_services,
        ):
            raise ValueError("action dominance differs from canonical dominance")
        if self.action_id != _action_id_for_request(self.request):
            raise ValueError("action ID does not canonically identify request")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"action_sha256"})
        )
        if self.action_sha256 != expected:
            raise ValueError("action digest does not bind action")
        return self


class ActionMaskReasonV22(str, Enum):
    EXECUTED = "EXECUTED"
    COVERED = "COVERED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    OVER_BUDGET = "OVER_BUDGET"
    DOMINATED = "DOMINATED"


class MaskedActionV22(DtaModelV22):
    action_id: ActionIdV22
    reason: ActionMaskReasonV22
    dominating_action_id: ActionIdV22 | None

    @model_validator(mode="after")
    def require_dominance_detail(self) -> MaskedActionV22:
        if (self.reason is ActionMaskReasonV22.DOMINATED) != (
            self.dominating_action_id is not None
        ):
            raise ValueError("masked action dominance detail is inconsistent")
        return self


class ActionCoverageV22(DtaModelV22):
    schema_version: Literal["dta-v22.action-coverage.v1"]
    executed_action_ids: tuple[ActionIdV22, ...]
    covered_capability_keys: tuple[str, ...]
    coverage_sha256: Sha256V22

    @classmethod
    def build(
        cls,
        *,
        executed_action_ids: tuple[str, ...],
        covered_capability_keys: tuple[str, ...],
    ) -> ActionCoverageV22:
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.action-coverage.v1",
            "executed_action_ids": tuple(sorted(set(executed_action_ids))),
            "covered_capability_keys": tuple(
                sorted(set(covered_capability_keys))
            ),
        }
        return cls.model_validate(
            {**payload, "coverage_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_coverage(self) -> ActionCoverageV22:
        if self.executed_action_ids != tuple(sorted(set(self.executed_action_ids))):
            raise ValueError("executed action IDs are not sorted and unique")
        if self.covered_capability_keys != tuple(
            sorted(set(self.covered_capability_keys))
        ):
            raise ValueError("covered capability keys are not sorted and unique")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"coverage_sha256"})
        )
        if self.coverage_sha256 != expected:
            raise ValueError("coverage digest does not bind action coverage")
        return self


class ActionCatalogV22(DtaModelV22):
    schema_version: Literal["dta-v22.action-catalog.v1"]
    candidate_services: tuple[LogicalServiceV22, ...]
    topology_sha256: Sha256V22
    capability_registry_sha256: Sha256V22
    enabled_sources: tuple[EvidenceSourceV22, ...]
    remaining_budget: StrictFloat = Field(ge=0)
    action_coverage: ActionCoverageV22
    registry_actions: tuple[EvidenceActionV22, ...]
    actions: tuple[EvidenceActionV22, ...]
    masked_actions: tuple[MaskedActionV22, ...]
    catalog_sha256: Sha256V22

    @model_validator(mode="after")
    def require_catalog(self) -> ActionCatalogV22:
        if not 1 <= len(self.candidate_services) <= 4:
            raise ValueError("catalog requires one to four candidate services")
        for values, label in (
            (self.candidate_services, "candidate services"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"catalog {label} are not sorted and unique")
        if self.enabled_sources != tuple(
            sorted(set(self.enabled_sources), key=list(EvidenceSourceV22).index)
        ):
            raise ValueError("catalog enabled sources are not canonical and unique")
        canonical_registry = build_tool_capability_registry_v22(
            disabled_sources=tuple(
                source
                for source in EvidenceSourceV22
                if source not in self.enabled_sources
            )
        )
        if self.capability_registry_sha256 != canonical_registry.registry_sha256:
            raise ValueError("catalog capability registry binding differs")
        if self.registry_actions != _registry_actions(
            candidates=self.candidate_services,
            registry=canonical_registry,
        ):
            raise ValueError("catalog differs from canonical registry surface")
        registry_ids = tuple(item.action_id for item in self.registry_actions)
        action_ids = tuple(item.action_id for item in self.actions)
        masked_ids = tuple(item.action_id for item in self.masked_actions)
        if registry_ids != tuple(sorted(set(registry_ids))):
            raise ValueError("registry action surface is not canonical")
        if action_ids != tuple(sorted(set(action_ids))):
            raise ValueError("available action surface is not canonical")
        if masked_ids != tuple(sorted(set(masked_ids))):
            raise ValueError("masked action surface is not canonical")
        if set(action_ids).intersection(masked_ids) or set(action_ids) | set(
            masked_ids
        ) != set(registry_ids):
            raise ValueError("available and masked actions do not partition registry")
        registry_by_id = {item.action_id: item for item in self.registry_actions}
        if any(
            not set(item.target_services).issubset(self.candidate_services)
            for item in self.registry_actions
        ):
            raise ValueError("registry action targets a non-candidate service")
        if any(item != registry_by_id[item.action_id] for item in self.actions):
            raise ValueError("available action differs from registry binding")
        if not set(self.action_coverage.executed_action_ids).issubset(registry_by_id):
            raise ValueError("executed action is outside registry surface")
        known_coverage = {
            key for item in self.registry_actions for key in item.coverage_keys
        }
        if not set(self.action_coverage.covered_capability_keys).issubset(
            known_coverage
        ):
            raise ValueError("covered capability is outside registry surface")
        action_id_set = set(action_ids)
        masked_by_id = {item.action_id: item for item in self.masked_actions}
        for action_id in self.action_coverage.executed_action_ids:
            if action_id in action_id_set:
                raise ValueError("executed action remains available")
            if masked_by_id[action_id].reason is not ActionMaskReasonV22.EXECUTED:
                raise ValueError("executed action lacks exact EXECUTED mask")
        if any(
            item.reason is ActionMaskReasonV22.EXECUTED
            and item.action_id not in self.action_coverage.executed_action_ids
            for item in self.masked_actions
        ):
            raise ValueError("unexecuted action has EXECUTED mask")
        covered = set(self.action_coverage.covered_capability_keys)
        executed_coverage = {
            key
            for action_id in self.action_coverage.executed_action_ids
            for key in registry_by_id[action_id].coverage_keys
        }
        if not executed_coverage.issubset(covered):
            raise ValueError("catalog does not record all executed coverage")
        for action in self.registry_actions:
            if action.action_id in self.action_coverage.executed_action_ids:
                continue
            if set(action.coverage_keys).issubset(covered):
                if action.action_id in action_id_set:
                    raise ValueError("covered action remains available")
                if masked_by_id[action.action_id].reason is not ActionMaskReasonV22.COVERED:
                    raise ValueError("covered action lacks exact COVERED mask")
        if any(item.weighted_cost > self.remaining_budget for item in self.actions):
            raise ValueError("over-budget action remains available")
        enabled = set(self.enabled_sources)
        if any(item.source not in enabled for item in self.actions):
            raise ValueError("source-unavailable action remains available")
        if any(
            item.reason is ActionMaskReasonV22.SOURCE_UNAVAILABLE
            and registry_by_id[item.action_id].source in enabled
            for item in self.masked_actions
        ):
            raise ValueError("enabled-source action has SOURCE_UNAVAILABLE mask")
        if any(
            item.reason is ActionMaskReasonV22.OVER_BUDGET
            and registry_by_id[item.action_id].weighted_cost <= self.remaining_budget
            for item in self.masked_actions
        ):
            raise ValueError("within-budget action has OVER_BUDGET mask")
        for masked in self.masked_actions:
            if masked.reason is not ActionMaskReasonV22.DOMINATED:
                continue
            assert masked.dominating_action_id is not None
            if masked.dominating_action_id not in action_id_set or masked.action_id not in set(
                registry_by_id[masked.dominating_action_id].dominates_action_ids
            ):
                raise ValueError("DOMINATED mask lacks an available canonical dominator")
        for dominator in self.actions:
            if action_id_set.intersection(dominator.dominates_action_ids):
                raise ValueError("dominated action remains available")
        expected_masks: dict[str, MaskedActionV22] = {}
        eligible_ids: set[str] = set()
        for action in self.registry_actions:
            if action.action_id in self.action_coverage.executed_action_ids:
                reason = ActionMaskReasonV22.EXECUTED
            elif set(action.coverage_keys).issubset(covered):
                reason = ActionMaskReasonV22.COVERED
            elif action.source not in enabled:
                reason = ActionMaskReasonV22.SOURCE_UNAVAILABLE
            elif action.weighted_cost > self.remaining_budget:
                reason = ActionMaskReasonV22.OVER_BUDGET
            else:
                eligible_ids.add(action.action_id)
                continue
            expected_masks[action.action_id] = MaskedActionV22(
                action_id=action.action_id,
                reason=reason,
                dominating_action_id=None,
            )
        for dominator in self.registry_actions:
            if dominator.action_id not in eligible_ids:
                continue
            for dominated_id in dominator.dominates_action_ids:
                if dominated_id in eligible_ids:
                    expected_masks[dominated_id] = MaskedActionV22(
                        action_id=dominated_id,
                        reason=ActionMaskReasonV22.DOMINATED,
                        dominating_action_id=dominator.action_id,
                    )
        expected_available = tuple(
            item
            for item in self.registry_actions
            if item.action_id not in expected_masks
        )
        expected_masked = tuple(
            sorted(expected_masks.values(), key=lambda item: item.action_id)
        )
        if self.actions != expected_available or self.masked_actions != expected_masked:
            raise ValueError("catalog mask differs from canonical dynamic mask")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"catalog_sha256"})
        )
        if self.catalog_sha256 != expected:
            raise ValueError("catalog digest does not bind catalog")
        return self

    @property
    def executed_action_ids(self) -> tuple[ActionIdV22, ...]:
        return self.action_coverage.executed_action_ids

    @property
    def covered_capability_keys(self) -> tuple[str, ...]:
        return self.action_coverage.covered_capability_keys


def build_tool_capability_registry_v22(
    *,
    disabled_sources: tuple[EvidenceSourceV22, ...] = (),
) -> ToolCapabilityRegistryV22:
    disabled = tuple(
        sorted(set(disabled_sources), key=list(EvidenceSourceV22).index)
    )
    if disabled != disabled_sources:
        raise ValueError("disabled sources are not canonical and unique")
    capabilities = tuple(
        ToolCapabilityV22(
            source=source,
            enabled=source not in disabled,
            weighted_cost=_CANONICAL_COST_BY_SOURCE[source],
        )
        for source in EvidenceSourceV22
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.tool-capability-registry.v1",
        "capabilities": capabilities,
    }
    draft = ToolCapabilityRegistryV22.model_construct(
        **payload,
        registry_sha256="0" * 64,
    )
    return ToolCapabilityRegistryV22.model_validate(
        {
            **payload,
            "registry_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"registry_sha256"})
            ),
        }
    )


def build_default_tool_capability_registry_v22() -> ToolCapabilityRegistryV22:
    return build_tool_capability_registry_v22()


def _action_id_for_request(request: CanonicalReadRequestV22) -> str:
    source = request.source.value.casefold()
    if request.source is EvidenceSourceV22.METRICS:
        return f"a:{source}:{request.target_services[0]}:core"
    if request.source in {
        EvidenceSourceV22.RUNTIME,
        EvidenceSourceV22.RESOURCES,
    } and len(request.target_services) > 1:
        target_digest = semantic_sha256_v22(list(request.target_services))[:12]
        return f"a:{source}:all-candidates:{target_digest}"
    return f"a:{source}:{request.target_services[0]}"


def _request_for(
    source: EvidenceSourceV22,
    targets: tuple[str, ...],
) -> CanonicalReadRequestV22:
    if source is EvidenceSourceV22.METRICS:
        return build_canonical_read_request_v22(
            source=source,
            target_services=targets,
            metric_kinds=(
                MetricKindV22.ERROR_RATE,
                MetricKindV22.LATENCY_P95_MS,
                MetricKindV22.REQUEST_SUPPORT,
            ),
            lookback_seconds=300,
            max_results=3,
        )
    if source is EvidenceSourceV22.LOGS:
        return build_canonical_read_request_v22(
            source=source,
            target_services=targets,
            lookback_seconds=300,
            max_records=12,
        )
    if source is EvidenceSourceV22.TRACES:
        return build_canonical_read_request_v22(
            source=source,
            target_services=targets,
            lookback_seconds=300,
            max_spans=12,
            neighborhood_hops=1,
        )
    if source is EvidenceSourceV22.RUNTIME:
        return build_canonical_read_request_v22(
            source=source,
            target_services=targets,
            max_results=len(targets),
        )
    if source is EvidenceSourceV22.RESOURCES:
        return build_canonical_read_request_v22(
            source=source,
            target_services=targets,
            sampling_window_seconds=10,
            sample_count=5,
        )
    return build_canonical_read_request_v22(
        source=source,
        target_services=targets,
        lookback_seconds=3600,
        max_records=12,
    )


def _coverage_keys(
    source: EvidenceSourceV22,
    targets: tuple[str, ...],
) -> tuple[str, ...]:
    suffix = "core" if source is EvidenceSourceV22.METRICS else (
        "radius-1" if source is EvidenceSourceV22.TRACES else "read"
    )
    return tuple(sorted(f"{source.value.casefold()}:{target}:{suffix}" for target in targets))


def _canonical_dominates_action_ids(
    source: EvidenceSourceV22,
    targets: tuple[str, ...],
) -> tuple[str, ...]:
    if source not in {
        EvidenceSourceV22.RUNTIME,
        EvidenceSourceV22.RESOURCES,
    } or len(targets) <= 1:
        return ()
    return tuple(
        sorted(
            _action_id_for_request(_request_for(source, (target,)))
            for target in targets
        )
    )


def _build_action(
    *,
    source: EvidenceSourceV22,
    targets: tuple[str, ...],
    weighted_cost: float,
    dominates_action_ids: tuple[str, ...] = (),
) -> EvidenceActionV22:
    request = _request_for(source, targets)
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.evidence-action.v1",
        "action_id": _action_id_for_request(request),
        "source": source,
        "target_services": request.target_services,
        "request": request,
        "coverage_keys": _coverage_keys(source, request.target_services),
        "weighted_cost": weighted_cost,
        "request_sha256": request.request_sha256,
        "dominates_action_ids": tuple(sorted(dominates_action_ids)),
    }
    draft = EvidenceActionV22.model_construct(**payload, action_sha256="0" * 64)
    return EvidenceActionV22.model_validate(
        {
            **payload,
            "action_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"action_sha256"})
            ),
        }
    )


def _registry_actions(
    *,
    candidates: tuple[str, ...],
    registry: ToolCapabilityRegistryV22,
) -> tuple[EvidenceActionV22, ...]:
    actions: list[EvidenceActionV22] = []
    for service in candidates:
        for source in EvidenceSourceV22:
            capability = registry.require(source)
            actions.append(
                _build_action(
                    source=source,
                    targets=(service,),
                    weighted_cost=capability.weighted_cost,
                )
            )
    if len(candidates) > 1:
        individual_ids = tuple(
            _action_id_for_request(_request_for(EvidenceSourceV22.RUNTIME, (service,)))
            for service in candidates
        )
        actions.append(
            _build_action(
                source=EvidenceSourceV22.RUNTIME,
                targets=candidates,
                weighted_cost=_canonical_weighted_cost(
                    EvidenceSourceV22.RUNTIME,
                    candidates,
                ),
                dominates_action_ids=individual_ids,
            )
        )
    return tuple(sorted(actions, key=lambda item: item.action_id))


def build_action_catalog_v22(
    *,
    candidate_services: tuple[str, ...],
    topology: StaticTopologyV22,
    capability_registry: ToolCapabilityRegistryV22,
    executed_action_ids: tuple[str, ...],
    remaining_budget: float,
    covered_capability_keys: tuple[str, ...] = (),
) -> ActionCatalogV22:
    candidates = tuple(sorted(item.strip() for item in candidate_services))
    if not 1 <= len(candidates) <= 4 or candidates != tuple(sorted(set(candidates))):
        raise ValueError("candidate services must contain one to four unique services")
    if not set(candidates).issubset(topology.services):
        raise ValueError("candidate service is absent from static topology")
    registry_actions = _registry_actions(
        candidates=candidates,
        registry=capability_registry,
    )
    by_id = {item.action_id: item for item in registry_actions}
    executed = tuple(sorted(set(executed_action_ids)))
    if not set(executed).issubset(by_id):
        raise ValueError("executed action is outside canonical registry surface")
    covered = set(covered_capability_keys)
    for action_id in executed:
        covered.update(by_id[action_id].coverage_keys)

    masked: dict[str, MaskedActionV22] = {}
    eligible: list[EvidenceActionV22] = []
    for action in registry_actions:
        capability = capability_registry.require(action.source)
        if action.action_id in executed:
            masked[action.action_id] = MaskedActionV22(
                action_id=action.action_id,
                reason=ActionMaskReasonV22.EXECUTED,
                dominating_action_id=None,
            )
        elif set(action.coverage_keys).issubset(covered):
            masked[action.action_id] = MaskedActionV22(
                action_id=action.action_id,
                reason=ActionMaskReasonV22.COVERED,
                dominating_action_id=None,
            )
        elif not capability.enabled:
            masked[action.action_id] = MaskedActionV22(
                action_id=action.action_id,
                reason=ActionMaskReasonV22.SOURCE_UNAVAILABLE,
                dominating_action_id=None,
            )
        elif action.weighted_cost > remaining_budget:
            masked[action.action_id] = MaskedActionV22(
                action_id=action.action_id,
                reason=ActionMaskReasonV22.OVER_BUDGET,
                dominating_action_id=None,
            )
        else:
            eligible.append(action)

    eligible_by_id = {item.action_id: item for item in eligible}
    for dominator in eligible:
        for dominated_id in dominator.dominates_action_ids:
            if dominated_id in eligible_by_id and dominated_id not in masked:
                masked[dominated_id] = MaskedActionV22(
                    action_id=dominated_id,
                    reason=ActionMaskReasonV22.DOMINATED,
                    dominating_action_id=dominator.action_id,
                )
    actions = tuple(
        item for item in eligible if item.action_id not in masked
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.action-catalog.v1",
        "candidate_services": candidates,
        "topology_sha256": topology.topology_sha256,
        "capability_registry_sha256": capability_registry.registry_sha256,
        "enabled_sources": tuple(
            item.source for item in capability_registry.capabilities if item.enabled
        ),
        "remaining_budget": float(remaining_budget),
        "action_coverage": ActionCoverageV22.build(
            executed_action_ids=executed,
            covered_capability_keys=tuple(sorted(covered)),
        ),
        "registry_actions": registry_actions,
        "actions": tuple(sorted(actions, key=lambda item: item.action_id)),
        "masked_actions": tuple(sorted(masked.values(), key=lambda item: item.action_id)),
    }
    draft = ActionCatalogV22.model_construct(**payload, catalog_sha256="0" * 64)
    return ActionCatalogV22.model_validate(
        {
            **payload,
            "catalog_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"catalog_sha256"})
            ),
        }
    )


def resolve_canonical_request_v22(
    *,
    catalog: ActionCatalogV22,
    action_id: str,
) -> CanonicalReadRequestV22:
    action = next((item for item in catalog.actions if item.action_id == action_id), None)
    if action is None:
        raise ValueError("canonical action is not available in the current catalog")
    return CanonicalReadRequestV22.model_validate(action.request.model_dump(mode="python"))


__all__ = (
    "ActionCatalogV22",
    "ActionCoverageV22",
    "ActionMaskReasonV22",
    "EvidenceActionV22",
    "MaskedActionV22",
    "StaticTopologyV22",
    "ToolCapabilityRegistryV22",
    "ToolCapabilityV22",
    "build_action_catalog_v22",
    "build_default_tool_capability_registry_v22",
    "build_tool_capability_registry_v22",
    "resolve_canonical_request_v22",
)
