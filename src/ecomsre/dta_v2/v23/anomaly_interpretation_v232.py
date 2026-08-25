"""Exhaustive, evidence-bound anomaly interpretation for DTA v2.3.2."""

from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.memory import (
    LogCategoryV22,
    LogSalientPayloadV22,
    PredicateKindV22,
    SalientEvidenceMemoryV22,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    GenericAnomalyV23,
)


class AnomalyInterpretationContractErrorV232(ValueError):
    """Typed preflight error for an anomaly kind outside the frozen registry."""


class InterpretationSourceV232(str, Enum):
    STATIC_KIND = "STATIC_KIND"
    BOUND_LOG_CATEGORY = "BOUND_LOG_CATEGORY"
    UNRESOLVED_LOG_CATEGORY = "UNRESOLVED_LOG_CATEGORY"
    COVERAGE_STATE = "COVERAGE_STATE"


class AnomalyInterpretationV232(DtaModelV22):
    schema_version: Literal["dta-v232.anomaly-interpretation.v1"]
    anomaly_id: str
    anomaly_kind: GenericAnomalyKindV23
    candidate_domains: tuple[ProvisionalFaultDomainV23, ...] = Field(min_length=1)
    primary_domain: ProvisionalFaultDomainV23
    interpretation_source: InterpretationSourceV232
    reason_codes: tuple[str, ...] = Field(min_length=1)
    interpretation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_interpretation(self) -> "AnomalyInterpretationV232":
        if self.candidate_domains != tuple(
            sorted(set(self.candidate_domains), key=lambda item: item.value)
        ):
            raise ValueError("anomaly interpretation domains are not canonical")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("anomaly interpretation reasons are not canonical")
        if (
            self.primary_domain is not ProvisionalFaultDomainV23.UNKNOWN
            and self.primary_domain not in set(self.candidate_domains)
        ):
            raise ValueError("primary anomaly domain is not a candidate")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"interpretation_sha256"})
        )
        if self.interpretation_sha256 != expected:
            raise ValueError("anomaly interpretation digest differs")
        return self


_STATIC_DOMAIN_BY_KIND_V232 = {
    GenericAnomalyKindV23.METRIC_ERROR_OUTLIER: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.RUNTIME_NOT_RUNNING: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_UNHEALTHY: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY: ProvisionalFaultDomainV23.RUNTIME,
    GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER: ProvisionalFaultDomainV23.RESOURCE,
    GenericAnomalyKindV23.RESOURCE_MEMORY_TREND: ProvisionalFaultDomainV23.RESOURCE,
    GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER: ProvisionalFaultDomainV23.DEPENDENCY,
    GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN: ProvisionalFaultDomainV23.UNKNOWN,
    GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION: ProvisionalFaultDomainV23.CONFIGURATION,
    GenericAnomalyKindV23.SOURCE_COVERAGE_GAP: ProvisionalFaultDomainV23.UNKNOWN,
}

_DOMAIN_BY_LOG_CATEGORY_V232 = {
    LogCategoryV22.CONFIGURATION_ERROR: ProvisionalFaultDomainV23.CONFIGURATION,
    LogCategoryV22.DEPENDENCY_TIMEOUT: ProvisionalFaultDomainV23.DEPENDENCY,
    LogCategoryV22.MEMORY_PRESSURE: ProvisionalFaultDomainV23.RESOURCE,
    LogCategoryV22.OTHER: ProvisionalFaultDomainV23.UNKNOWN,
}

_LOG_CATEGORY_BY_PREDICATE_V232 = {
    PredicateKindV22.LOG_CONFIGURATION_ERROR: LogCategoryV22.CONFIGURATION_ERROR,
    PredicateKindV22.LOG_DEPENDENCY_TIMEOUT: LogCategoryV22.DEPENDENCY_TIMEOUT,
    PredicateKindV22.LOG_MEMORY_PRESSURE: LogCategoryV22.MEMORY_PRESSURE,
}


class AnomalyInterpretationRegistryV232(DtaModelV22):
    schema_version: Literal["dta-v232.anomaly-interpretation-registry.v1"]
    supported_kinds: tuple[GenericAnomalyKindV23, ...]
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(cls) -> "AnomalyInterpretationRegistryV232":
        supported = tuple(sorted(GenericAnomalyKindV23, key=lambda item: item.value))
        payload: dict[str, Any] = {
            "schema_version": "dta-v232.anomaly-interpretation-registry.v1",
            "supported_kinds": supported,
        }
        registry_identity = {
            **payload,
            "static_domains": {
                kind.value: domain.value
                for kind, domain in sorted(
                    _STATIC_DOMAIN_BY_KIND_V232.items(),
                    key=lambda item: item[0].value,
                )
            },
            "log_categories": {
                category.value: domain.value
                for category, domain in sorted(
                    _DOMAIN_BY_LOG_CATEGORY_V232.items(),
                    key=lambda item: item[0].value,
                )
            },
        }
        return cls.model_validate(
            {**payload, "registry_sha256": semantic_sha256_v22(registry_identity)}
        )

    @model_validator(mode="after")
    def require_total_registry(self) -> "AnomalyInterpretationRegistryV232":
        expected = tuple(sorted(GenericAnomalyKindV23, key=lambda item: item.value))
        if self.supported_kinds != expected:
            raise ValueError("v2.3.2 anomaly registry is not enum-total")
        registered = set(_STATIC_DOMAIN_BY_KIND_V232) | {
            GenericAnomalyKindV23.LOG_ERROR_CLUSTER
        }
        if registered != set(GenericAnomalyKindV23):
            raise ValueError("v2.3.2 anomaly registry implementation is not total")
        registry_identity = {
            "schema_version": self.schema_version,
            "supported_kinds": self.supported_kinds,
            "static_domains": {
                kind.value: domain.value
                for kind, domain in sorted(
                    _STATIC_DOMAIN_BY_KIND_V232.items(),
                    key=lambda item: item[0].value,
                )
            },
            "log_categories": {
                category.value: domain.value
                for category, domain in sorted(
                    _DOMAIN_BY_LOG_CATEGORY_V232.items(),
                    key=lambda item: item[0].value,
                )
            },
        }
        if self.registry_sha256 != semantic_sha256_v22(registry_identity):
            raise ValueError("v2.3.2 anomaly registry digest differs")
        return self

    def require_supported_kind(
        self,
        kind: GenericAnomalyKindV23 | str,
    ) -> GenericAnomalyKindV23:
        try:
            parsed = GenericAnomalyKindV23(kind)
        except ValueError as exc:
            raise AnomalyInterpretationContractErrorV232(
                f"unmapped anomaly kind: {kind}"
            ) from exc
        if parsed not in set(self.supported_kinds):
            raise AnomalyInterpretationContractErrorV232(
                f"unmapped anomaly kind: {parsed.value}"
            )
        return parsed

    def interpret(
        self,
        *,
        anomaly: GenericAnomalyV23,
        memory: SalientEvidenceMemoryV22,
    ) -> AnomalyInterpretationV232:
        kind = self.require_supported_kind(anomaly.kind)
        if kind is GenericAnomalyKindV23.LOG_ERROR_CLUSTER:
            domains, primary, source, reasons = _interpret_log_error_cluster_v232(
                anomaly=anomaly,
                memory=memory,
            )
        elif kind is GenericAnomalyKindV23.SOURCE_COVERAGE_GAP:
            domains = (ProvisionalFaultDomainV23.UNKNOWN,)
            primary = ProvisionalFaultDomainV23.UNKNOWN
            source = InterpretationSourceV232.COVERAGE_STATE
            reasons = ("SOURCE_COVERAGE_GAP_IS_NOT_MECHANISM_EVIDENCE",)
        else:
            domain = _STATIC_DOMAIN_BY_KIND_V232[kind]
            domains = (domain,)
            primary = domain
            source = InterpretationSourceV232.STATIC_KIND
            reasons = (f"STATIC_KIND_{kind.value}",)
        payload: dict[str, Any] = {
            "schema_version": "dta-v232.anomaly-interpretation.v1",
            "anomaly_id": anomaly.anomaly_id,
            "anomaly_kind": kind,
            "candidate_domains": domains,
            "primary_domain": primary,
            "interpretation_source": source,
            "reason_codes": tuple(sorted(reasons)),
        }
        draft = AnomalyInterpretationV232.model_construct(
            **payload,
            interpretation_sha256="0" * 64,
        )
        return AnomalyInterpretationV232.model_validate(
            {
                **payload,
                "interpretation_sha256": semantic_sha256_v22(
                    draft.model_dump(
                        mode="json",
                        exclude={"interpretation_sha256"},
                    )
                ),
            }
        )


def _interpret_log_error_cluster_v232(
    *,
    anomaly: GenericAnomalyV23,
    memory: SalientEvidenceMemoryV22,
) -> tuple[
    tuple[ProvisionalFaultDomainV23, ...],
    ProvisionalFaultDomainV23,
    InterpretationSourceV232,
    tuple[str, ...],
]:
    anomaly_refs = set(anomaly.evidence_refs)
    categories = [
        fact.payload.category
        for fact in memory.salient_facts
        if set(fact.evidence_refs).intersection(anomaly_refs)
        and isinstance(fact.payload, LogSalientPayloadV22)
    ]
    if not categories:
        categories = [
            _LOG_CATEGORY_BY_PREDICATE_V232[predicate.predicate_kind]
            for predicate in memory.predicates
            if set(predicate.evidence_refs).intersection(anomaly_refs)
            and predicate.predicate_kind in _LOG_CATEGORY_BY_PREDICATE_V232
        ]
    if not categories:
        return (
            (ProvisionalFaultDomainV23.UNKNOWN,),
            ProvisionalFaultDomainV23.UNKNOWN,
            InterpretationSourceV232.UNRESOLVED_LOG_CATEGORY,
            ("LOG_CATEGORY_UNRESOLVED",),
        )
    support = Counter(_DOMAIN_BY_LOG_CATEGORY_V232[category] for category in categories)
    domains = tuple(sorted(support, key=lambda item: item.value))
    ranked = sorted(support.items(), key=lambda item: (-item[1], item[0].value))
    primary = (
        ranked[0][0]
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]
        else ProvisionalFaultDomainV23.UNKNOWN
    )
    reasons = tuple(
        sorted(
            {
                f"LOG_CATEGORY_{category.value}_BOUND"
                for category in categories
            }
            | ({"LOG_CATEGORY_SUPPORT_TIED"} if primary is ProvisionalFaultDomainV23.UNKNOWN else set())
        )
    )
    return domains, primary, InterpretationSourceV232.BOUND_LOG_CATEGORY, reasons


DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232 = (
    AnomalyInterpretationRegistryV232.build()
)

# Import-time construction is intentionally fail-closed. A future enum member
# cannot silently reach a Provider evaluation without updating this registry.
assert set(DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.supported_kinds) == set(
    GenericAnomalyKindV23
)


__all__ = (
    "AnomalyInterpretationContractErrorV232",
    "AnomalyInterpretationRegistryV232",
    "AnomalyInterpretationV232",
    "DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232",
    "InterpretationSourceV232",
)
