"""Total strict-conflict interpretation for the DTA v2.3.2 successor."""

from __future__ import annotations

from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22, SignalStrengthV22
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    AnomalyInterpretationRegistryV232,
    AnomalyInterpretationV232,
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
    InterpretationSourceV232,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


def interpret_residual_anomalies_v232(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    registry: AnomalyInterpretationRegistryV232 = (
        DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232
    ),
) -> tuple[AnomalyInterpretationV232, ...]:
    residual_ids = set(graph.residual_anomaly_ids)
    return tuple(
        sorted(
            (
                registry.interpret(anomaly=item, memory=memory)
                for item in graph.generic_anomalies
                if item.anomaly_id in residual_ids
            ),
            key=lambda item: item.anomaly_id,
        )
    )


def derive_unresolved_interpretation_conflict_v232(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    bounded_reads_completed: int,
    registry: AnomalyInterpretationRegistryV232 = (
        DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232
    ),
) -> bool:
    """Preserve the strict v2.3 rule over an enum-total interpretation layer."""

    if bounded_reads_completed < 1:
        return False
    residual_ids = set(graph.residual_anomaly_ids)
    interpretations: set[tuple[str, str]] = set()
    by_id = {
        item.anomaly_id: item
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids
        and item.strength is SignalStrengthV22.STRONG
    }
    for interpretation in interpret_residual_anomalies_v232(
        graph=graph,
        memory=memory,
        registry=registry,
    ):
        anomaly = by_id.get(interpretation.anomaly_id)
        if anomaly is None:
            continue
        if interpretation.interpretation_source is InterpretationSourceV232.COVERAGE_STATE:
            continue
        interpretations.update(
            (anomaly.service, domain.value)
            for domain in interpretation.candidate_domains
        )
    if len(interpretations) < 2:
        return False
    services = {service for service, _domain in interpretations}
    domains = {domain for _service, domain in interpretations}
    return len(services) > 1 or len(domains) > 1


__all__ = (
    "derive_unresolved_interpretation_conflict_v232",
    "interpret_residual_anomalies_v232",
)
