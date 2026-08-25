"""Deterministic explanation accounting for the v2.3 discovery lane."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import (
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    build_default_evidence_support_policy_v22,
    evaluate_support_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ReadSourceStatusV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyV23
from ecomsre.dta_v2.v23.ontology_view import ActiveOntologyViewV23


class KnownTerminalCandidateV23(DtaModelV22):
    schema_version: Literal["dta-v23.known-terminal-candidate.v1"]
    hypothesis_id: str
    root_service: str
    mechanism: MechanismV22
    matched_clause_id: str
    supporting_evidence_refs: tuple[str, ...]
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_candidate(self) -> "KnownTerminalCandidateV23":
        if self.mechanism in {MechanismV22.NO_INCIDENT, MechanismV22.UNKNOWN}:
            raise ValueError("sentinel mechanism cannot be a known incident candidate")
        if self.supporting_evidence_refs != tuple(
            sorted(set(self.supporting_evidence_refs))
        ):
            raise ValueError("known terminal refs are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"candidate_sha256"})
        )
        if self.candidate_sha256 != expected:
            raise ValueError("known terminal candidate digest differs")
        return self


class SourceCoverageV23(DtaModelV22):
    source: EvidenceSourceV22
    queried: StrictBool
    covered_services: tuple[str, ...]
    successful_observations: StrictInt = Field(ge=0)
    failed_observations: StrictInt = Field(ge=0)


class ResidualEvidenceGraphV23(DtaModelV22):
    schema_version: Literal["dta-v23.residual-evidence-graph.v1"]
    candidate_services: tuple[str, ...]
    generic_anomalies: tuple[GenericAnomalyV23, ...]
    known_terminal_candidates: tuple[KnownTerminalCandidateV23, ...]
    explained_anomaly_ids: tuple[str, ...]
    residual_anomaly_ids: tuple[str, ...]
    contradicted_anomaly_ids: tuple[str, ...]
    source_coverage: tuple[SourceCoverageV23, ...]
    explanation_coverage: StrictFloat = Field(ge=0.0, le=1.0)
    healthy_runtime_services: tuple[str, ...]
    contrastive_target_present: StrictBool
    graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_graph(self) -> "ResidualEvidenceGraphV23":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("residual graph candidates are not canonical")
        anomaly_ids = tuple(item.anomaly_id for item in self.generic_anomalies)
        if anomaly_ids != tuple(sorted(set(anomaly_ids))):
            raise ValueError("residual graph anomalies are not canonical")
        partitions = (
            set(self.explained_anomaly_ids),
            set(self.residual_anomaly_ids),
            set(self.contradicted_anomaly_ids),
        )
        if any(left.intersection(right) for index, left in enumerate(partitions) for right in partitions[index + 1 :]):
            raise ValueError("residual graph anomaly partitions overlap")
        if set().union(*partitions) != set(anomaly_ids):
            raise ValueError("residual graph anomaly partition is incomplete")
        if self.healthy_runtime_services != tuple(
            sorted(set(self.healthy_runtime_services))
        ):
            raise ValueError("healthy runtime services are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"graph_sha256"})
        )
        if self.graph_sha256 != expected:
            raise ValueError("residual graph digest differs")
        return self


def _parent_for(
    *,
    target: str,
    mechanism: MechanismV22,
    topology_edges: tuple[tuple[str, str], ...],
) -> str | None:
    if mechanism is not MechanismV22.DEPENDENCY_LATENCY:
        return None
    return next(
        (
            right if left == target else left
            for left, right in topology_edges
            if target in {left, right}
        ),
        None,
    )


def build_known_terminal_candidates_v23(
    *,
    view: ActiveOntologyViewV23,
    memory: SalientEvidenceMemoryV22,
    topology_edges: tuple[tuple[str, str], ...] = (),
) -> tuple[KnownTerminalCandidateV23, ...]:
    """Project only enabled v2.2 base-policy terminals into the v2.3 lane."""

    policy = build_default_evidence_support_policy_v22()
    if policy.policy_sha256 != view.support_policy_sha256:
        raise ValueError("active ontology view is not bound to the frozen base policy")
    candidates: list[KnownTerminalCandidateV23] = []
    for hypothesis in view.active_hypotheses:
        if hypothesis.target_service is None or hypothesis.mechanism in {
            MechanismV22.NO_INCIDENT,
            MechanismV22.UNKNOWN,
        }:
            continue
        decision = evaluate_support_v22(
            policy=policy,
            mechanism=hypothesis.mechanism,
            target_service=hypothesis.target_service,
            parent_service=_parent_for(
                target=hypothesis.target_service,
                mechanism=hypothesis.mechanism,
                topology_edges=topology_edges,
            ),
            predicates=memory.predicates,
        )
        if not decision.accepted or decision.matched_clause_id is None:
            continue
        payload: dict[str, Any] = {
            "schema_version": "dta-v23.known-terminal-candidate.v1",
            "hypothesis_id": hypothesis.hypothesis_id,
            "root_service": hypothesis.target_service,
            "mechanism": hypothesis.mechanism,
            "matched_clause_id": decision.matched_clause_id,
            "supporting_evidence_refs": decision.supporting_evidence_refs,
        }
        draft = KnownTerminalCandidateV23.model_construct(
            **payload,
            candidate_sha256="0" * 64,
        )
        candidates.append(
            KnownTerminalCandidateV23.model_validate(
                {
                    **payload,
                    "candidate_sha256": semantic_sha256_v22(
                        draft.model_dump(mode="json", exclude={"candidate_sha256"})
                    ),
                }
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.hypothesis_id))


_WEIGHT = {
    SignalStrengthV22.STRONG: 2,
    SignalStrengthV22.MODERATE: 1,
    SignalStrengthV22.WEAK: 0,
    SignalStrengthV22.NONE: 0,
}


def build_residual_evidence_graph_v23(
    *,
    candidate_services: tuple[str, ...],
    generic_anomalies: tuple[GenericAnomalyV23, ...],
    known_terminal_candidates: tuple[KnownTerminalCandidateV23, ...],
    memory: SalientEvidenceMemoryV22,
) -> ResidualEvidenceGraphV23:
    candidates = tuple(sorted(set(candidate_services)))
    if candidates != candidate_services:
        raise ValueError("residual graph candidates are not canonical")
    terminal_refs = {
        ref
        for terminal in known_terminal_candidates
        for ref in terminal.supporting_evidence_refs
    }
    anomalies = tuple(sorted(generic_anomalies, key=lambda item: item.anomaly_id))
    explained = tuple(
        item.anomaly_id
        for item in anomalies
        if terminal_refs.intersection(item.evidence_refs)
    )
    residual = tuple(
        item.anomaly_id for item in anomalies if item.anomaly_id not in set(explained)
    )
    total_weight = sum(_WEIGHT[item.strength] for item in anomalies)
    explained_weight = sum(
        _WEIGHT[item.strength]
        for item in anomalies
        if item.anomaly_id in set(explained)
    )
    summaries_by_source = {
        source: tuple(
            item for item in memory.observation_summaries if item.source is source
        )
        for source in EvidenceSourceV22
    }
    coverage = tuple(
        SourceCoverageV23(
            source=source,
            queried=bool(summaries_by_source[source]),
            covered_services=tuple(
                sorted(
                    {
                        fact.service
                        for fact in memory.salient_facts
                        if fact.source is source and fact.service in set(candidates)
                    }
                )
            ),
            successful_observations=sum(
                item.status
                in {
                    ReadSourceStatusV22.SUCCESS_EMPTY,
                    ReadSourceStatusV22.SUCCESS_NONEMPTY,
                }
                for item in summaries_by_source[source]
            ),
            failed_observations=sum(
                item.status
                not in {
                    ReadSourceStatusV22.SUCCESS_EMPTY,
                    ReadSourceStatusV22.SUCCESS_NONEMPTY,
                }
                for item in summaries_by_source[source]
            ),
        )
        for source in EvidenceSourceV22
    )
    healthy_runtime = tuple(
        sorted(
            {
                fact.service
                for fact in memory.salient_facts
                if isinstance(fact.payload, RuntimeSalientPayloadV22)
                and fact.payload.state is RuntimeStateV22.RUNNING
                and fact.payload.healthy
                and fact.service in set(candidates)
            }
        )
    )
    covered_by_source = {item.source: set(item.covered_services) for item in coverage}
    anomaly_services_by_source = {
        source: {
            item.service
            for item in anomalies
            if item.source is source
            and item.strength in {SignalStrengthV22.MODERATE, SignalStrengthV22.STRONG}
        }
        for source in EvidenceSourceV22
    }
    contrastive = any(
        covered_by_source[source] == set(candidates)
        and bool(anomaly_services_by_source[source])
        and anomaly_services_by_source[source] != set(candidates)
        for source in EvidenceSourceV22
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.residual-evidence-graph.v1",
        "candidate_services": candidates,
        "generic_anomalies": anomalies,
        "known_terminal_candidates": known_terminal_candidates,
        "explained_anomaly_ids": explained,
        "residual_anomaly_ids": residual,
        "contradicted_anomaly_ids": (),
        "source_coverage": coverage,
        "explanation_coverage": (
            1.0 if total_weight == 0 and known_terminal_candidates else
            0.0 if total_weight == 0 else
            explained_weight / total_weight
        ),
        "healthy_runtime_services": healthy_runtime,
        "contrastive_target_present": contrastive,
    }
    draft = ResidualEvidenceGraphV23.model_construct(
        **payload,
        graph_sha256="0" * 64,
    )
    return ResidualEvidenceGraphV23.model_validate(
        {
            **payload,
            "graph_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"graph_sha256"})
            ),
        }
    )


__all__ = (
    "KnownTerminalCandidateV23",
    "ResidualEvidenceGraphV23",
    "SourceCoverageV23",
    "build_known_terminal_candidates_v23",
    "build_residual_evidence_graph_v23",
)
