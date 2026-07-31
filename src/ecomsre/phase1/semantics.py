"""Shared evidence-native mechanism semantics for policy and validation."""

from __future__ import annotations

from ecomsre.phase1.contracts import (
    Evidence,
    EvidenceSource,
    FaultMechanism,
)

_MISSING = object()


def _attributes(item: Evidence) -> dict[str, object]:
    return {attribute.name: attribute.value for attribute in item.attributes}


def _native_mechanism(
    item: Evidence,
    attributes: dict[str, object],
) -> FaultMechanism | None:
    if item.source is EvidenceSource.METRICS:
        if (
            item.observation_type == "request_handler_failure_rate"
            and attributes.get("component_role") == "request_handler"
            and attributes.get("outcome") == "failure"
        ):
            return FaultMechanism.REQUEST_PROCESSING_FAILURE
        if (
            item.observation_type == "cache_timeout_rate"
            and attributes.get("dependency_role") == "cache"
            and attributes.get("outcome") == "timeout"
        ):
            return FaultMechanism.CACHE_BACKEND_TIMEOUT
        return None

    if item.source is EvidenceSource.CHANGES:
        if (
            item.observation_type == "configuration_transition"
            and attributes.get("change_kind") == "configuration"
            and attributes.get("transition") == "valid_to_invalid"
        ):
            return FaultMechanism.RUNTIME_CONFIGURATION_FAILURE
        if (
            item.observation_type == "deployment"
            and attributes.get("release_scope") == "request_path"
            and attributes.get("risk_signal") == "request_handler_regression"
        ):
            return FaultMechanism.REQUEST_PROCESSING_FAILURE
        return None

    if attributes.get(
        "diagnostic_kind"
    ) == "configuration_parse_failure" and item.observation_type in {
        "configuration_error_span",
        "configuration_error_log",
    }:
        return FaultMechanism.RUNTIME_CONFIGURATION_FAILURE
    if (
        attributes.get("dependency_role") == "cache"
        and attributes.get("outcome") == "timeout"
        and item.observation_type in {"cache_client_timeout_span", "cache_timeout_log"}
    ):
        return FaultMechanism.CACHE_BACKEND_TIMEOUT
    if (
        attributes.get("component_role") == "request_handler"
        and attributes.get("outcome") == "failure"
        and item.observation_type
        in {"request_handler_failure_span", "request_handler_failure_log"}
    ):
        return FaultMechanism.REQUEST_PROCESSING_FAILURE
    return None


def classify_evidence_mechanism(
    item: Evidence,
) -> FaultMechanism | None:
    """Classify one observation without incident, case, or evaluator context."""

    attributes = _attributes(item)
    native = _native_mechanism(item, attributes)
    explicit_value = attributes.get("fault_mechanism", _MISSING)
    if explicit_value is _MISSING:
        return native
    if not isinstance(explicit_value, str):
        return None
    try:
        explicit = FaultMechanism(explicit_value.strip())
    except ValueError:
        return None
    if native is None or native is not explicit:
        return None
    return native


def is_anomalous_metric_evidence(item: Evidence) -> bool:
    """Return whether one Metrics observation establishes an SLI anomaly."""

    if item.source is not EvidenceSource.METRICS:
        return False
    attributes = _attributes(item)
    explicit = attributes.get("anomaly", attributes.get("anomalous"))
    if isinstance(explicit, bool):
        return explicit
    status = attributes.get("status", attributes.get("sli_status"))
    if isinstance(status, str):
        normalized = status.strip().lower()
        if normalized in {"normal", "healthy", "ok", "baseline"}:
            return False
        if normalized in {
            "abnormal",
            "degraded",
            "error",
            "failing",
            "high",
            "critical",
        }:
            return True
    observation_type = item.observation_type.lower()
    if any(marker in observation_type for marker in ("normal", "healthy", "baseline")):
        return False
    return False


def evidence_supports_mechanism(
    item: Evidence,
    mechanism: FaultMechanism,
) -> bool:
    """Return whether one observation supports one closed mechanism claim."""

    classified = classify_evidence_mechanism(item)
    if item.source is not EvidenceSource.METRICS:
        return classified is mechanism
    return is_anomalous_metric_evidence(item) and classified is mechanism
