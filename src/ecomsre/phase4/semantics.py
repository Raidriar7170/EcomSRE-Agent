"""Evidence-native Phase 4 ecommerce-domain mechanism semantics."""

from __future__ import annotations

from ecomsre.phase1.contracts import Evidence, EvidenceSource
from ecomsre.phase4.contracts import DomainFaultMechanism


_MECHANISM_SERVICE = {
    DomainFaultMechanism.FEATURE_FRESHNESS_LAG: "feature",
    DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH: "ranking",
    DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE: "ranking",
}


def _attributes(item: Evidence) -> dict[str, object]:
    return {attribute.name: attribute.value for attribute in item.attributes}


def _native_domain_mechanism(item: Evidence) -> DomainFaultMechanism | None:
    attributes = _attributes(item)
    if item.source is EvidenceSource.METRICS:
        if attributes.get("anomaly") is not True:
            return None
        if (
            item.observation_type == "feature_freshness_lag"
            and attributes.get("freshness_status") == "stale"
            and attributes.get("component_role") == "feature_store"
        ):
            return DomainFaultMechanism.FEATURE_FRESHNESS_LAG
        if (
            item.observation_type == "schema_validation_failure_rate"
            and attributes.get("component_role") == "feature_adapter"
            and attributes.get("outcome") == "failure"
        ):
            return DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH
        if (
            item.observation_type == "ranking_request_failure_rate"
            and attributes.get("component_role") == "ranking_engine"
            and attributes.get("outcome") == "failure"
        ):
            return DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE
        return None

    if item.source in {EvidenceSource.LOGS, EvidenceSource.TRACES}:
        if (
            item.observation_type
            in {"stale_feature_read_log", "stale_feature_read_span"}
            and attributes.get("dependency_role") == "feature_store"
            and attributes.get("freshness_status") == "stale"
        ):
            return DomainFaultMechanism.FEATURE_FRESHNESS_LAG
        if (
            item.observation_type
            in {
                "model_feature_schema_mismatch_log",
                "model_feature_schema_mismatch_span",
            }
            and attributes.get("compatibility") == "mismatch"
            and attributes.get("component_role") == "feature_adapter"
        ):
            return DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH
        if (
            item.observation_type
            in {
                "ranking_configuration_error_log",
                "ranking_configuration_error_span",
            }
            and attributes.get("diagnostic_kind")
            == "ranking_configuration_invalid"
        ):
            return DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE
        return None

    if item.source is EvidenceSource.CHANGES and (
        item.observation_type == "ranking_configuration_transition"
        and attributes.get("change_kind") == "ranking_configuration"
        and attributes.get("transition") == "valid_to_invalid"
    ):
        return DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE
    return None


def classify_domain_evidence_mechanism(
    item: Evidence,
) -> DomainFaultMechanism | None:
    """Classify only exact native semantics; declared conflicts fail closed."""

    native = _native_domain_mechanism(item)
    if native is None:
        return None
    if item.service != _MECHANISM_SERVICE[native]:
        return None
    attributes = _attributes(item)
    declared = tuple(
        attributes[name]
        for name in ("fault_mechanism", "mechanism")
        if name in attributes
    )
    if any(type(value) is not str for value in declared):
        return None
    if len(set(declared)) > 1:
        return None
    if declared and declared[0] != native.value:
        return None
    return native


def domain_evidence_supports_mechanism(
    item: Evidence,
    mechanism: DomainFaultMechanism,
) -> bool:
    """Return whether one evidence item natively supports the mechanism."""

    return classify_domain_evidence_mechanism(item) is mechanism


def is_domain_anomalous_metric(item: Evidence) -> bool:
    """Recognize only a typed mechanism-bearing domain metric anomaly."""

    return (
        item.source is EvidenceSource.METRICS
        and classify_domain_evidence_mechanism(item) is not None
    )


def is_business_sli_anomaly(item: Evidence) -> bool:
    """Recognize a Search or Recommendation business SLI anomaly."""

    if item.source is not EvidenceSource.METRICS:
        return False
    attributes = _attributes(item)
    return (
        item.service in {"search", "recommendation"}
        and item.observation_type in {"search_sli", "recommendation_sli"}
        and attributes.get("anomaly") is True
        and attributes.get("sli_status") == "degraded"
    )
