"""Unified evidence-native semantics over the frozen Phase 1 and Phase 4 worlds."""

from __future__ import annotations

from ecomsre.phase1.contracts import Evidence, EvidenceSource
from ecomsre.phase1.semantics import (
    classify_evidence_mechanism,
    evidence_supports_mechanism,
    is_anomalous_metric_evidence,
)
from ecomsre.phase4.semantics import (
    classify_domain_evidence_mechanism,
    domain_evidence_supports_mechanism,
    is_business_sli_anomaly,
    is_domain_anomalous_metric,
)
from ecomsre.phase5a.contracts import UnifiedMechanismV2


def _attributes(item: Evidence) -> dict[str, object]:
    return {attribute.name: attribute.value for attribute in item.attributes}


def classify_evidence_candidate(
    item: Evidence,
) -> tuple[str, UnifiedMechanismV2] | None:
    """Classify one native observation without case or evaluator context."""

    phase1 = classify_evidence_mechanism(item)
    if phase1 is not None:
        return item.service, UnifiedMechanismV2(phase1.value)
    domain = classify_domain_evidence_mechanism(item)
    if domain is not None:
        return item.service, UnifiedMechanismV2(domain.value)
    return None


def evidence_supports_candidate(
    item: Evidence,
    *,
    root_service: str,
    fault_mechanism: UnifiedMechanismV2,
) -> bool:
    """Require exact root identity plus one closed native mechanism."""

    classified = classify_evidence_candidate(item)
    if classified != (root_service, fault_mechanism):
        return False
    phase1 = classify_evidence_mechanism(item)
    if phase1 is not None:
        return evidence_supports_mechanism(item, phase1)
    domain = classify_domain_evidence_mechanism(item)
    return domain is not None and domain_evidence_supports_mechanism(item, domain)


def _normal_metric(item: Evidence) -> bool:
    if item.source is not EvidenceSource.METRICS:
        return False
    attributes = _attributes(item)
    anomaly = attributes.get("anomaly", attributes.get("anomalous"))
    if anomaly is False:
        return True
    status = attributes.get("sli_status", attributes.get("status"))
    return isinstance(status, str) and status.strip().lower() in {
        "normal",
        "healthy",
        "ok",
        "baseline",
    }


def evidence_contradicts_candidate(
    item: Evidence,
    *,
    root_service: str,
    fault_mechanism: UnifiedMechanismV2,
) -> bool:
    """Recognize bounded native contradictions without temporal guesswork."""

    attributes = _attributes(item)
    if item.service == root_service and _normal_metric(item):
        return True
    if (
        item.service == root_service
        and fault_mechanism is UnifiedMechanismV2.CACHE_BACKEND_TIMEOUT
        and attributes.get("dependency_role") == "cache"
        and str(attributes.get("status", "")).lower()
        in {"healthy", "normal", "ok", "available"}
    ):
        return True
    classified = classify_evidence_candidate(item)
    return (
        classified is not None
        and classified[0] == root_service
        and classified[1] is not fault_mechanism
    )


def is_anomalous_service_signal(item: Evidence) -> bool:
    """Recognize typed service or business-SLI anomaly signals."""

    return (
        is_anomalous_metric_evidence(item)
        or is_domain_anomalous_metric(item)
        or is_business_sli_anomaly(item)
    )


def is_normal_sli_signal(item: Evidence) -> bool:
    """Expose normal metric evidence for Judge-level incident contradiction."""

    return _normal_metric(item)
