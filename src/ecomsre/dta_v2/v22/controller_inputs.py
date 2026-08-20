"""Common bootstrap and symmetric primary-controller inputs for DTA v2.2."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import (
    Field,
    InstanceOf,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationInfo,
    model_validator,
)

from ecomsre.dta_v2.v22.action_catalog import (
    ActionCatalogV22,
    StaticTopologyV22,
    ToolCapabilityRegistryV22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    BeliefLedgerViewV22,
    HypothesisCatalogV22,
)
from ecomsre.dta_v2.v22.memory import (
    MetricSalientPayloadV22,
    PredicateKindV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
)
from ecomsre.dta_v2.v22.predicates import (
    EvidenceSupportPolicyV22,
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    LogicalServiceV22,
    MetricKindV22,
    Sha256V22,
    semantic_sha256_v22,
)


_CORE_METRICS_V22 = frozenset(
    {
        MetricKindV22.ERROR_RATE,
        MetricKindV22.LATENCY_P95_MS,
        MetricKindV22.REQUEST_SUPPORT,
    }
)
_BENIGN_BOOTSTRAP_PREDICATES_V22 = frozenset(
    {
        PredicateKindV22.CHANGE_RECENT_ROLLOUT,
        PredicateKindV22.RUNTIME_HEALTHY,
    }
)


class ControllerArmV22(str, Enum):
    FLAT_CANONICAL = "FLAT_CANONICAL"
    PLANNER_LITE = "PLANNER_LITE"


class TriageSnapshotV22(DtaModelV22):
    schema_version: Literal["dta-v22.triage-snapshot.v1"]
    candidate_services: tuple[LogicalServiceV22, ...] = Field(min_length=1, max_length=4)
    memory_sha256: Sha256V22
    topology_sha256: Sha256V22
    capability_registry_sha256: Sha256V22
    enabled_sources: tuple[EvidenceSourceV22, ...]
    runtime_fact_ids: tuple[str, ...]
    core_metric_fact_ids: tuple[str, ...]
    strong_anomaly_predicate_ids: tuple[str, ...]
    bootstrap_evidence_refs: tuple[str, ...]
    candidate_subgraph_edges: tuple[tuple[LogicalServiceV22, LogicalServiceV22], ...]
    bootstrap_weighted_cost: StrictFloat = Field(ge=1.0, le=1.0)
    snapshot_sha256: Sha256V22

    @model_validator(mode="after")
    def require_snapshot(self, info: ValidationInfo) -> TriageSnapshotV22:
        context = info.context if isinstance(info.context, dict) else None
        if (
            context is None
            or not isinstance(context.get("memory"), SalientEvidenceMemoryV22)
            or not isinstance(context.get("topology"), StaticTopologyV22)
            or not isinstance(
                context.get("capability_registry"),
                ToolCapabilityRegistryV22,
            )
        ):
            raise ValueError("triage snapshot requires authoritative bootstrap provenance")
        expected_payload = _triage_payload_v22(
            memory=context["memory"],
            candidates=self.candidate_services,
            topology=context["topology"],
            capability_registry=context["capability_registry"],
        )
        expected_draft = TriageSnapshotV22.model_construct(
            **expected_payload,
            snapshot_sha256="0" * 64,
        )
        if self.model_dump(mode="json", exclude={"snapshot_sha256"}) != (
            expected_draft.model_dump(mode="json", exclude={"snapshot_sha256"})
        ):
            raise ValueError("triage snapshot differs from authoritative bootstrap")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("triage snapshot digest differs")
        return self


def _triage_payload_v22(
    *,
    memory: SalientEvidenceMemoryV22,
    candidates: tuple[str, ...],
    topology: StaticTopologyV22,
    capability_registry: ToolCapabilityRegistryV22,
) -> dict[str, Any]:
    candidate_set = set(candidates)
    if not candidate_set.issubset(topology.services):
        raise ValueError("triage candidate is absent from static topology")
    runtime_facts = tuple(
        sorted(
            fact.fact_id
            for fact in memory.salient_facts
            if fact.service in candidate_set
            and isinstance(fact.payload, RuntimeSalientPayloadV22)
        )
    )
    metric_facts = tuple(
        sorted(
            fact.fact_id
            for fact in memory.salient_facts
            if fact.service in candidate_set
            and isinstance(fact.payload, MetricSalientPayloadV22)
            and fact.payload.metric_kind in _CORE_METRICS_V22
        )
    )
    retained_ids = set(runtime_facts) | set(metric_facts)
    bootstrap_refs = tuple(
        sorted(
            {
                ref
                for fact in memory.salient_facts
                if fact.fact_id in retained_ids
                for ref in fact.evidence_refs
            }
        )
    )
    return {
        "schema_version": "dta-v22.triage-snapshot.v1",
        "candidate_services": candidates,
        "memory_sha256": memory.memory_sha256,
        "topology_sha256": topology.topology_sha256,
        "capability_registry_sha256": capability_registry.registry_sha256,
        "enabled_sources": tuple(
            item.source for item in capability_registry.capabilities if item.enabled
        ),
        "runtime_fact_ids": runtime_facts,
        "core_metric_fact_ids": metric_facts,
        "strong_anomaly_predicate_ids": tuple(
            sorted(
                item.predicate_id
                for item in memory.predicates
                if item.service in candidate_set
                and item.predicate_kind not in _BENIGN_BOOTSTRAP_PREDICATES_V22
            )
        ),
        "bootstrap_evidence_refs": bootstrap_refs,
        "candidate_subgraph_edges": tuple(
            edge
            for edge in topology.edges
            if edge[0] in candidate_set and edge[1] in candidate_set
        ),
        "bootstrap_weighted_cost": 1.0,
    }


def build_common_triage_snapshot_v22(
    *,
    memory: SalientEvidenceMemoryV22,
    candidate_services: tuple[str, ...],
    topology: StaticTopologyV22,
    capability_registry: ToolCapabilityRegistryV22,
) -> TriageSnapshotV22:
    candidates = tuple(sorted(item.strip() for item in candidate_services))
    if not 1 <= len(candidates) <= 4 or candidates != tuple(sorted(set(candidates))):
        raise ValueError("triage candidates require one to four unique services")
    payload = _triage_payload_v22(
        memory=memory,
        candidates=candidates,
        topology=topology,
        capability_registry=capability_registry,
    )
    draft = TriageSnapshotV22.model_construct(
        **payload,
        snapshot_sha256="0" * 64,
    )
    return TriageSnapshotV22.model_validate(
        {
            **payload,
            "snapshot_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"snapshot_sha256"})
            ),
        },
        context={
            "memory": memory,
            "topology": topology,
            "capability_registry": capability_registry,
        },
    )


class ControllerRuntimeContextV22(DtaModelV22):
    schema_version: Literal["dta-v22.controller-runtime-context.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    turn_ordinal: StrictInt = Field(ge=1, le=6)
    controller_identity_sha256: Sha256V22
    remaining_evidence_budget: StrictFloat = Field(ge=0, le=3)
    remaining_provider_turns: StrictInt = Field(ge=1, le=5)
    correction_remaining: StrictBool
    context_sha256: Sha256V22

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        turn_ordinal: int,
        controller_identity_sha256: str,
        remaining_evidence_budget: float,
        remaining_provider_turns: int,
        correction_remaining: bool,
    ) -> ControllerRuntimeContextV22:
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.controller-runtime-context.v1",
            "run_id": run_id,
            "turn_ordinal": turn_ordinal,
            "controller_identity_sha256": controller_identity_sha256,
            "remaining_evidence_budget": float(remaining_evidence_budget),
            "remaining_provider_turns": remaining_provider_turns,
            "correction_remaining": correction_remaining,
        }
        return cls.model_validate(
            {**payload, "context_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_context(self) -> ControllerRuntimeContextV22:
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"context_sha256"})
        )
        if self.context_sha256 != expected:
            raise ValueError("controller runtime context digest differs")
        return self


class ControllerTurnInputV22(DtaModelV22):
    schema_version: Literal["dta-v22.controller-turn-input.v1"]
    arm: ControllerArmV22
    runtime_context: ControllerRuntimeContextV22
    bootstrap: InstanceOf[TriageSnapshotV22]
    hypothesis_catalog: HypothesisCatalogV22
    action_catalog: ActionCatalogV22
    salient_memory: InstanceOf[SalientEvidenceMemoryV22]
    evidence_support_policy: EvidenceSupportPolicyV22
    belief_ledger_view: InstanceOf[BeliefLedgerViewV22] | None
    input_sha256: Sha256V22

    @model_validator(mode="after")
    def require_input(self) -> ControllerTurnInputV22:
        if (
            self.arm is ControllerArmV22.FLAT_CANONICAL
            and self.belief_ledger_view is not None
        ):
            raise ValueError("Flat cannot receive a persistent belief ledger view")
        if (
            self.arm is ControllerArmV22.PLANNER_LITE
            and self.belief_ledger_view is None
        ):
            raise ValueError("Planner-Lite requires a persistent belief ledger view")
        candidates = self.bootstrap.candidate_services
        if (
            self.hypothesis_catalog.candidate_services != candidates
            or self.action_catalog.candidate_services != candidates
        ):
            raise ValueError("controller input candidate surfaces differ")
        if (
            self.bootstrap.topology_sha256 != self.action_catalog.topology_sha256
            or self.bootstrap.capability_registry_sha256
            != self.action_catalog.capability_registry_sha256
            or self.bootstrap.enabled_sources != self.action_catalog.enabled_sources
            or self.runtime_context.remaining_evidence_budget
            != self.action_catalog.remaining_budget
        ):
            raise ValueError("controller input runtime surfaces differ")
        if self.belief_ledger_view is not None:
            view = self.belief_ledger_view
            if (
                view.hypothesis_catalog_sha256
                != self.hypothesis_catalog.catalog_sha256
                or view.executed_action_ids
                != self.action_catalog.action_coverage.executed_action_ids
                or view.covered_capability_keys
                != self.action_catalog.action_coverage.covered_capability_keys
            ):
                raise ValueError("Planner-Lite view differs from current action state")
        if (
            self.evidence_support_policy
            != build_default_evidence_support_policy_v22()
        ):
            raise ValueError("controller input support policy is not the frozen default")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"input_sha256"})
        )
        if self.input_sha256 != expected:
            raise ValueError("controller turn input digest differs")
        return self


def build_controller_turn_input_v22(
    *,
    arm: ControllerArmV22,
    runtime_context: ControllerRuntimeContextV22,
    bootstrap: TriageSnapshotV22,
    hypothesis_catalog: HypothesisCatalogV22,
    action_catalog: ActionCatalogV22,
    salient_memory: SalientEvidenceMemoryV22,
    belief_ledger_view: BeliefLedgerViewV22 | None,
    evidence_support_policy: EvidenceSupportPolicyV22 | None = None,
) -> ControllerTurnInputV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.controller-turn-input.v1",
        "arm": arm,
        "runtime_context": runtime_context,
        "bootstrap": bootstrap,
        "hypothesis_catalog": hypothesis_catalog,
        "action_catalog": action_catalog,
        "salient_memory": salient_memory,
        "evidence_support_policy": (
            build_default_evidence_support_policy_v22()
            if evidence_support_policy is None
            else evidence_support_policy
        ),
        "belief_ledger_view": belief_ledger_view,
    }
    draft = ControllerTurnInputV22.model_construct(
        **payload,
        input_sha256="0" * 64,
    )
    return ControllerTurnInputV22.model_validate(
        {
            **payload,
            "input_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"input_sha256"})
            ),
        }
    )


__all__ = (
    "ControllerArmV22",
    "ControllerRuntimeContextV22",
    "ControllerTurnInputV22",
    "TriageSnapshotV22",
    "build_common_triage_snapshot_v22",
    "build_controller_turn_input_v22",
)
