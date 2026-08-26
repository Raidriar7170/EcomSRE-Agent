"""Typed, evidence-backed contradiction witnesses for DTA v2.3.3."""

from __future__ import annotations

from enum import Enum
from itertools import combinations
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.memory import (
    ResourceSalientPayloadV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    GenericAnomalyV23,
    ObservedValueV23,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class ContradictionKindV233(str, Enum):
    SAME_SERVICE_RUNTIME_STATE = "SAME_SERVICE_RUNTIME_STATE"
    SAME_SERVICE_RESOURCE_STATE = "SAME_SERVICE_RESOURCE_STATE"
    MUTUALLY_EXCLUSIVE_FIRST_ERROR = "MUTUALLY_EXCLUSIVE_FIRST_ERROR"
    DISCONNECTED_EXCLUSIVE_ROOTS = "DISCONNECTED_EXCLUSIVE_ROOTS"
    CHANGE_CAUSE_EXCLUDED_BY_COMPLETE_COVERAGE = (
        "CHANGE_CAUSE_EXCLUDED_BY_COMPLETE_COVERAGE"
    )
    DEPENDENCY_CAUSE_EXCLUDED_BY_COMPLETE_TRACE = (
        "DEPENDENCY_CAUSE_EXCLUDED_BY_COMPLETE_TRACE"
    )


class ClaimPolarityV233(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class WitnessStrengthV233(str, Enum):
    WEAK = "WEAK"
    STRONG = "STRONG"


class EvidenceClaimV233(DtaModelV22):
    schema_version: Literal["dta-v233.evidence-claim.v1"]
    claim_id: str = Field(pattern=r"^claim-v233-[0-9a-f]{16}$")
    claim_type: str
    service: str
    related_services: tuple[str, ...]
    source: EvidenceSourceV22
    polarity: ClaimPolarityV233
    observed_values: tuple[ObservedValueV23, ...]
    evidence_refs: tuple[str, ...]

    @model_validator(mode="after")
    def require_claim(self) -> "EvidenceClaimV233":
        for values, label in (
            (self.related_services, "related services"),
            (self.evidence_refs, "evidence refs"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 claim {label} are not canonical")
        keys = tuple(item.key for item in self.observed_values)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("v2.3.3 claim values are not canonical")
        identity = self.model_dump(mode="json", exclude={"claim_id"})
        if self.claim_id != f"claim-v233-{semantic_sha256_v22(identity)[:16]}":
            raise ValueError("v2.3.3 claim identity differs")
        return self


class ContradictionWitnessV233(DtaModelV22):
    schema_version: Literal["dta-v233.contradiction-witness.v1"]
    witness_id: str = Field(pattern=r"^witness-v233-[0-9a-f]{16}$")
    kind: ContradictionKindV233
    left_claim: EvidenceClaimV233
    right_claim: EvidenceClaimV233
    services: tuple[str, ...]
    source_set: tuple[EvidenceSourceV22, ...]
    left_evidence_refs: tuple[str, ...]
    right_evidence_refs: tuple[str, ...]
    observation_scope: str
    coverage_requirements: tuple[str, ...]
    coverage_satisfied: StrictBool
    resolvable_sources: tuple[EvidenceSourceV22, ...]
    strength: WitnessStrengthV233
    reason_codes: tuple[str, ...] = Field(min_length=1)
    witness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_witness(self) -> "ContradictionWitnessV233":
        if self.left_claim.claim_id == self.right_claim.claim_id:
            raise ValueError("v2.3.3 witness claims are identical")
        if self.left_evidence_refs != self.left_claim.evidence_refs:
            raise ValueError("v2.3.3 witness left evidence differs")
        if self.right_evidence_refs != self.right_claim.evidence_refs:
            raise ValueError("v2.3.3 witness right evidence differs")
        if self.services != tuple(sorted(set(self.services))):
            raise ValueError("v2.3.3 witness services are not canonical")
        if self.source_set != tuple(
            sorted(set(self.source_set), key=lambda item: item.value)
        ):
            raise ValueError("v2.3.3 witness sources are not canonical")
        if self.resolvable_sources != tuple(
            sorted(set(self.resolvable_sources), key=lambda item: item.value)
        ):
            raise ValueError("v2.3.3 witness resolution sources are not canonical")
        for values, label in (
            (self.coverage_requirements, "coverage"),
            (self.reason_codes, "reasons"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 witness {label} is not canonical")
        if self.strength is WitnessStrengthV233.STRONG and (
            not self.coverage_satisfied
            or not self.left_evidence_refs
            or not self.right_evidence_refs
        ):
            raise ValueError("strong v2.3.3 witness lacks closed evidence")
        identity = self.model_dump(
            mode="json",
            exclude={"witness_id", "witness_sha256"},
        )
        if self.witness_id != f"witness-v233-{semantic_sha256_v22(identity)[:16]}":
            raise ValueError("v2.3.3 witness identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"witness_sha256"})
        )
        if self.witness_sha256 != expected:
            raise ValueError("v2.3.3 witness digest differs")
        return self


def _claim(
    *,
    claim_type: str,
    service: str,
    related_services: tuple[str, ...],
    source: EvidenceSourceV22,
    polarity: ClaimPolarityV233,
    observed_values: tuple[ObservedValueV23, ...],
    evidence_refs: tuple[str, ...],
) -> EvidenceClaimV233:
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.evidence-claim.v1",
        "claim_type": claim_type,
        "service": service,
        "related_services": tuple(sorted(set(related_services))),
        "source": source,
        "polarity": polarity,
        "observed_values": tuple(
            sorted(observed_values, key=lambda item: item.key)
        ),
        "evidence_refs": tuple(sorted(set(evidence_refs))),
    }
    draft = EvidenceClaimV233.model_construct(
        **payload,
        claim_id="claim-v233-0000000000000000",
    )
    identity = draft.model_dump(mode="json", exclude={"claim_id"})
    return EvidenceClaimV233.model_validate(
        {
            **payload,
            "claim_id": f"claim-v233-{semantic_sha256_v22(identity)[:16]}",
        }
    )


def _anomaly_claim(anomaly: GenericAnomalyV23) -> EvidenceClaimV233:
    return _claim(
        claim_type=anomaly.kind.value,
        service=anomaly.service,
        related_services=anomaly.related_services,
        source=anomaly.source,
        polarity=ClaimPolarityV233.POSITIVE,
        observed_values=anomaly.observed_values,
        evidence_refs=anomaly.evidence_refs,
    )


def _coverage_complete(
    graph: ResidualEvidenceGraphV23,
    source: EvidenceSourceV22,
) -> bool:
    coverage = next(item for item in graph.source_coverage if item.source is source)
    return bool(coverage.queried) and set(coverage.covered_services) == set(
        graph.candidate_services
    ) and coverage.failed_observations == 0


def _witness(
    *,
    kind: ContradictionKindV233,
    left: EvidenceClaimV233,
    right: EvidenceClaimV233,
    observation_scope: str,
    coverage_requirements: tuple[str, ...],
    coverage_satisfied: bool,
    resolvable_sources: tuple[EvidenceSourceV22, ...],
    reason_codes: tuple[str, ...],
) -> ContradictionWitnessV233:
    strength = (
        WitnessStrengthV233.STRONG
        if coverage_satisfied and left.evidence_refs and right.evidence_refs
        else WitnessStrengthV233.WEAK
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.contradiction-witness.v1",
        "kind": kind,
        "left_claim": left,
        "right_claim": right,
        "services": tuple(
            sorted(
                {
                    left.service,
                    right.service,
                    *left.related_services,
                    *right.related_services,
                }
            )
        ),
        "source_set": tuple(
            sorted({left.source, right.source}, key=lambda item: item.value)
        ),
        "left_evidence_refs": left.evidence_refs,
        "right_evidence_refs": right.evidence_refs,
        "observation_scope": observation_scope,
        "coverage_requirements": tuple(sorted(set(coverage_requirements))),
        "coverage_satisfied": coverage_satisfied,
        "resolvable_sources": tuple(
            sorted(set(resolvable_sources), key=lambda item: item.value)
        ),
        "strength": strength,
        "reason_codes": tuple(sorted(set(reason_codes))),
    }
    identity_draft = ContradictionWitnessV233.model_construct(
        **payload,
        witness_id="witness-v233-0000000000000000",
        witness_sha256="0" * 64,
    )
    identity = identity_draft.model_dump(
        mode="json",
        exclude={"witness_id", "witness_sha256"},
    )
    witness_id = f"witness-v233-{semantic_sha256_v22(identity)[:16]}"
    draft = ContradictionWitnessV233.model_construct(
        **payload,
        witness_id=witness_id,
        witness_sha256="0" * 64,
    )
    return ContradictionWitnessV233.model_validate(
        {
            **payload,
            "witness_id": witness_id,
            "witness_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"witness_sha256"})
            ),
        }
    )


def _normal_resource_claims(
    memory: SalientEvidenceMemoryV22,
) -> dict[str, EvidenceClaimV233]:
    result: dict[str, EvidenceClaimV233] = {}
    for fact in memory.salient_facts:
        if (
            fact.source is not EvidenceSourceV22.RESOURCES
            or not isinstance(fact.payload, ResourceSalientPayloadV22)
            or fact.signal_strength is not SignalStrengthV22.NONE
        ):
            continue
        payload = fact.payload
        result[fact.service] = _claim(
            claim_type="TARGET_COMPLETE_NORMAL_RESOURCE_STATE",
            service=fact.service,
            related_services=(),
            source=EvidenceSourceV22.RESOURCES,
            polarity=ClaimPolarityV233.NEGATIVE,
            observed_values=(
                ObservedValueV23(
                    key="cpu_p95_percent",
                    value=str(payload.cpu_p95_percent),
                ),
                ObservedValueV23(
                    key="memory_slope_bytes_per_second",
                    value=str(payload.memory_slope_bytes_per_second),
                ),
            ),
            evidence_refs=fact.evidence_refs,
        )
    return result


def _healthy_runtime_claims(
    memory: SalientEvidenceMemoryV22,
) -> dict[str, EvidenceClaimV233]:
    result: dict[str, EvidenceClaimV233] = {}
    for fact in memory.salient_facts:
        if (
            fact.source is not EvidenceSourceV22.RUNTIME
            or not isinstance(fact.payload, RuntimeSalientPayloadV22)
            or fact.payload.state is not RuntimeStateV22.RUNNING
            or not fact.payload.healthy
        ):
            continue
        result[fact.service] = _claim(
            claim_type="AUTHORITATIVE_RUNTIME_RUNNING_HEALTHY",
            service=fact.service,
            related_services=(),
            source=EvidenceSourceV22.RUNTIME,
            polarity=ClaimPolarityV233.NEGATIVE,
            observed_values=(
                ObservedValueV23(key="healthy", value="true"),
                ObservedValueV23(key="state", value=RuntimeStateV22.RUNNING.value),
            ),
            evidence_refs=fact.evidence_refs,
        )
    return result


def build_contradiction_witnesses_v233(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    observation_scope: str,
) -> tuple[ContradictionWitnessV233, ...]:
    """Build witnesses without truth labels or domain/root competition shortcuts."""

    residual_ids = set(graph.residual_anomaly_ids)
    anomalies = tuple(
        item for item in graph.generic_anomalies if item.anomaly_id in residual_ids
    )
    witnesses: dict[str, ContradictionWitnessV233] = {}

    healthy = _healthy_runtime_claims(memory)
    runtime_complete = _coverage_complete(graph, EvidenceSourceV22.RUNTIME)
    for anomaly in anomalies:
        if anomaly.kind not in {
            GenericAnomalyKindV23.RUNTIME_NOT_RUNNING,
            GenericAnomalyKindV23.RUNTIME_UNHEALTHY,
        } or anomaly.service not in healthy:
            continue
        item = _witness(
            kind=ContradictionKindV233.SAME_SERVICE_RUNTIME_STATE,
            left=_anomaly_claim(anomaly),
            right=healthy[anomaly.service],
            observation_scope=observation_scope,
            coverage_requirements=("RUNTIME_TARGET_COMPLETE",),
            coverage_satisfied=runtime_complete,
            resolvable_sources=(),
            reason_codes=("SAME_SERVICE_RUNTIME_STATES_CANNOT_BOTH_HOLD",),
        )
        witnesses[item.witness_id] = item

    normal_resources = _normal_resource_claims(memory)
    resource_complete = _coverage_complete(graph, EvidenceSourceV22.RESOURCES)
    for anomaly in anomalies:
        if anomaly.kind not in {
            GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER,
            GenericAnomalyKindV23.RESOURCE_MEMORY_TREND,
        } or anomaly.service not in normal_resources:
            continue
        item = _witness(
            kind=ContradictionKindV233.SAME_SERVICE_RESOURCE_STATE,
            left=_anomaly_claim(anomaly),
            right=normal_resources[anomaly.service],
            observation_scope=observation_scope,
            coverage_requirements=("RESOURCES_TARGET_COMPLETE",),
            coverage_satisfied=resource_complete,
            resolvable_sources=(),
            reason_codes=("SAME_SERVICE_RESOURCE_STATES_CANNOT_BOTH_HOLD",),
        )
        witnesses[item.witness_id] = item

    first_errors = tuple(
        item
        for item in anomalies
        if item.kind is GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION
    )
    trace_complete = _coverage_complete(graph, EvidenceSourceV22.TRACES)
    logs_complete = _coverage_complete(graph, EvidenceSourceV22.LOGS)
    for left, right in combinations(first_errors, 2):
        left_surface = {left.service, *left.related_services}
        right_surface = {right.service, *right.related_services}
        if left.service == right.service or left_surface != right_surface:
            continue
        item = _witness(
            kind=ContradictionKindV233.MUTUALLY_EXCLUSIVE_FIRST_ERROR,
            left=_anomaly_claim(left),
            right=_anomaly_claim(right),
            observation_scope=observation_scope,
            coverage_requirements=("TRACE_TARGET_COMPLETE",),
            coverage_satisfied=trace_complete,
            resolvable_sources=(
                () if logs_complete else (EvidenceSourceV22.LOGS,)
            ),
            reason_codes=(
                "DISTINCT_FIRST_ERRORS_SHARE_ONE_INCIDENT_SURFACE",
                "OPPOSING_CAUSAL_PATHS_HAVE_NO_PROPAGATION_EDGE",
            ),
        )
        witnesses[item.witness_id] = item

    return tuple(witnesses[key] for key in sorted(witnesses))


__all__ = (
    "ClaimPolarityV233",
    "ContradictionKindV233",
    "ContradictionWitnessV233",
    "EvidenceClaimV233",
    "WitnessStrengthV233",
    "build_contradiction_witnesses_v233",
)
