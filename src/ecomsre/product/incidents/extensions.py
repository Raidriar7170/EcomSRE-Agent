"""Environment-scoped Extension Ontology evaluation for Product diagnosis."""

from __future__ import annotations

from dataclasses import dataclass

from ecomsre.product.knowledge.candidates_v050 import CompiledKnowledge, evaluate_candidate, snapshot_observations
from typing import Any

from ecomsre.dta_v2.v22.memory import BaselineProfileV22, SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v23.extension_runtime_v234 import (
    ExtensionRuntimeInputV234,
    ExtensionSourceCoverageV234,
    ExtensionSupportPolicyV234,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyV23
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import hashed_model_v234


@dataclass(frozen=True)
class ProductExtensionRegistrationV1:
    """Active registry entry projected into the non-actionable runtime."""

    registration_id: str
    mechanism_slug: str
    broad_fault_domain: str
    compiled_registration: CompiledFaultRegistrationV234

    def __post_init__(self) -> None:
        if self.registration_id != self.compiled_registration.registration_id:
            raise ValueError("extension registration identity differs from compiled payload")
        if self.mechanism_slug != self.compiled_registration.mechanism.mechanism_slug:
            raise ValueError("extension mechanism differs from compiled payload")
        if self.compiled_registration.action_authority != "NONE":
            raise ValueError("extension registration carries action authority")


@dataclass(frozen=True)
class ProductExtensionMatchV1:
    registration_id: str
    mechanism_slug: str
    broad_fault_domain: str
    root_service: str
    supporting_evidence_refs: tuple[str, ...]


def build_product_extension_runtime_input_v1(
    *,
    case_id: str,
    candidate_services: tuple[str, ...],
    topology_edges: tuple[tuple[str, str], ...],
    baseline: BaselineProfileV22,
    memory: SalientEvidenceMemoryV22,
    generic_anomalies: tuple[GenericAnomalyV23, ...],
    raw_outcomes: tuple[ReadOutcomeV22, ...],
) -> ExtensionRuntimeInputV234:
    coverage = tuple(
        ExtensionSourceCoverageV234(
            source=source,
            statuses=tuple(
                sorted(
                    {item.status for item in raw_outcomes if item.source is source},
                    key=lambda item: item.value,
                )
            ),
            reachable=any(
                item.source is source
                and item.status
                in {
                    ReadSourceStatusV22.SUCCESS_EMPTY,
                    ReadSourceStatusV22.SUCCESS_NONEMPTY,
                }
                for item in raw_outcomes
            ),
        )
        for source in sorted(
            {item.source for item in raw_outcomes},
            key=lambda item: item.value,
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.extension-runtime-input.v1",
        "case_id": case_id,
        "candidate_services": candidate_services,
        "adjacent_services": tuple(
            sorted(
                edge
                for edge in topology_edges
                if set(edge).issubset(candidate_services)
            )
        ),
        "baseline": baseline,
        "memory": memory,
        "generic_anomalies": generic_anomalies,
        "source_coverage": coverage,
    }
    return hashed_model_v234(
        ExtensionRuntimeInputV234,
        payload,
        "runtime_input_sha256",
    )


class ProductExtensionMatcherV1:
    """Evaluate every active registration and return every admitted match."""

    def __init__(
        self,
        registrations: tuple[ProductExtensionRegistrationV1, ...] = (),
        *, derived_registrations: tuple[CompiledKnowledge, ...] = (),
        capability_sha256: str | None = None,
        supplemental_reads=None,
    ) -> None:
        self.supplemental_observations: list[dict[str, Any]] = []
        self._supplemental_reads = supplemental_reads
        self._derived_registrations = derived_registrations
        self._capability_sha256 = capability_sha256
        self._registrations = tuple(
            sorted(registrations, key=lambda item: item.registration_id)
        )

    def match(
        self,
        *,
        case_id: str,
        candidate_services: tuple[str, ...],
        topology_edges: tuple[tuple[str, str], ...],
        baseline: BaselineProfileV22,
        memory: SalientEvidenceMemoryV22,
        generic_anomalies: tuple[GenericAnomalyV23, ...],
        raw_outcomes: tuple[ReadOutcomeV22, ...],
        snapshots: tuple[dict[str, Any], ...] = (),
    ) -> tuple[ProductExtensionMatchV1, ...]:
        runtime_input = build_product_extension_runtime_input_v1(
            case_id=case_id,
            candidate_services=candidate_services,
            topology_edges=topology_edges,
            baseline=baseline,
            memory=memory,
            generic_anomalies=generic_anomalies,
            raw_outcomes=raw_outcomes,
        )
        matches: list[ProductExtensionMatchV1] = []
        policy = ExtensionSupportPolicyV234()
        for entry in self._registrations:
            decisions = policy.evaluate(
                registration=entry.compiled_registration,
                runtime_input=runtime_input,
            )
            matches.extend(
                ProductExtensionMatchV1(
                    registration_id=entry.registration_id,
                    mechanism_slug=entry.mechanism_slug,
                    broad_fault_domain=entry.broad_fault_domain,
                    root_service=decision.target_service,
                    supporting_evidence_refs=decision.supporting_evidence_refs,
                )
                for decision in decisions
                if decision.admitted
            )
        observations = snapshot_observations(snapshots, memory) if self._derived_registrations else []
        if self._supplemental_reads is not None:
            from ecomsre.product.knowledge.observations_v050 import acquire_dependencies
            self.supplemental_observations = acquire_dependencies(self._derived_registrations, self._supplemental_reads)
            observations += self.supplemental_observations
        for candidate in self._derived_registrations:
            if candidate.capability_sha256 != self._capability_sha256:
                continue
            for target in candidate_services:
                outcome = evaluate_candidate(candidate, target=target, memory=memory,
                                             anomalies=generic_anomalies, observations=observations,
                                             incident_end=self._supplemental_reads.incident.diagnosis_observed_at if self._supplemental_reads else None)
                if outcome.status == "TRUE":
                    matches.append(ProductExtensionMatchV1(
                        registration_id=candidate.registration_id, mechanism_slug=candidate.proposal.name,
                        broad_fault_domain=candidate.proposal.broad_domain, root_service=target,
                        supporting_evidence_refs=outcome.evidence_refs,
                    ))
        return tuple(
            sorted(
                matches,
                key=lambda item: (item.registration_id, item.root_service),
            )
        )


__all__ = (
    "build_product_extension_runtime_input_v1",
    "ProductExtensionMatchV1",
    "ProductExtensionMatcherV1",
    "ProductExtensionRegistrationV1",
)
