"""Evaluator-controlled active views over the frozen v2.2 ontology."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.controller_contracts import (
    HypothesisCatalogEntryV22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.predicates import (
    EvidenceSupportPolicyV22,
    MechanismV22,
    SupportClauseV22,
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    LogicalServiceV22,
    Sha256V22,
    semantic_sha256_v22,
)


REGISTERED_MECHANISMS_V23 = tuple(
    sorted(
        (
            MechanismV22.CONFIGURATION_ERROR,
            MechanismV22.SERVICE_UNAVAILABLE,
            MechanismV22.CPU_SATURATION,
            MechanismV22.MEMORY_LEAK,
            MechanismV22.DEPENDENCY_LATENCY,
        ),
        key=lambda item: item.value,
    )
)


class ActiveOntologyViewV23(DtaModelV22):
    """One production or leave-one-mechanism-out ontology projection.

    ``hidden_mechanisms`` is evaluator-only. Provider projections must be built
    through :func:`provider_ontology_payload_v23`, which deliberately omits it.
    """

    schema_version: Literal["dta-v23.active-ontology-view.v1"]
    candidate_services: tuple[LogicalServiceV22, ...]
    enabled_mechanisms: tuple[MechanismV22, ...]
    hidden_mechanisms: tuple[MechanismV22, ...]
    active_hypotheses: tuple[HypothesisCatalogEntryV22, ...]
    active_support_clauses: tuple[SupportClauseV22, ...]
    support_policy_sha256: Sha256V22
    view_sha256: Sha256V22

    @model_validator(mode="after")
    def require_view(self) -> "ActiveOntologyViewV23":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("ontology-view candidates are not canonical")
        if len(self.hidden_mechanisms) > 1:
            raise ValueError("v2.3 evaluation may hide at most one mechanism")
        if any(item not in REGISTERED_MECHANISMS_V23 for item in self.hidden_mechanisms):
            raise ValueError("ontology view hides a non-registered mechanism")
        expected_enabled = tuple(
            item for item in REGISTERED_MECHANISMS_V23 if item not in self.hidden_mechanisms
        )
        if self.enabled_mechanisms != expected_enabled:
            raise ValueError("enabled ontology differs from registered minus hidden")
        hidden = set(self.hidden_mechanisms)
        if any(item.mechanism in hidden for item in self.active_hypotheses):
            raise ValueError("hidden mechanism remains in active hypotheses")
        if any(item.mechanism in hidden for item in self.active_support_clauses):
            raise ValueError("hidden mechanism remains in active support clauses")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"view_sha256"})
        )
        if self.view_sha256 != expected:
            raise ValueError("ontology view digest differs")
        return self


def build_active_ontology_view_v23(
    *,
    candidate_services: tuple[str, ...],
    hidden_mechanisms: tuple[MechanismV22, ...] = (),
    support_policy: EvidenceSupportPolicyV22 | None = None,
) -> ActiveOntologyViewV23:
    candidates = tuple(sorted(set(candidate_services)))
    if candidates != candidate_services or not 1 <= len(candidates) <= 4:
        raise ValueError("ontology-view candidates require one to four canonical services")
    hidden = tuple(sorted(set(hidden_mechanisms), key=lambda item: item.value))
    if len(hidden) != len(hidden_mechanisms):
        raise ValueError("ontology view contains duplicate hidden mechanisms")
    catalog = build_hypothesis_catalog_v22(candidate_services=candidates)
    policy = support_policy or build_default_evidence_support_policy_v22()
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.active-ontology-view.v1",
        "candidate_services": candidates,
        "enabled_mechanisms": tuple(
            item for item in REGISTERED_MECHANISMS_V23 if item not in hidden
        ),
        "hidden_mechanisms": hidden,
        "active_hypotheses": tuple(
            item for item in catalog.hypotheses if item.mechanism not in hidden
        ),
        "active_support_clauses": tuple(
            item for item in policy.clauses if item.mechanism not in hidden
        ),
        "support_policy_sha256": policy.policy_sha256,
    }
    draft = ActiveOntologyViewV23.model_construct(
        **payload,
        view_sha256="0" * 64,
    )
    return ActiveOntologyViewV23.model_validate(
        {
            **payload,
            "view_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"view_sha256"})
            ),
        }
    )


def provider_ontology_payload_v23(view: ActiveOntologyViewV23) -> dict[str, object]:
    """Return the only ontology projection allowed into a discovery prompt."""

    payload: dict[str, object] = {
        "schema_version": "dta-v23.provider-ontology-view.v1",
        "candidate_services": list(view.candidate_services),
        "enabled_mechanisms": [item.value for item in view.enabled_mechanisms],
        "active_hypotheses": [
            {
                "hypothesis_id": item.hypothesis_id,
                "target_service": item.target_service,
                "fault_domain": item.fault_domain.value,
                "mechanism": item.mechanism.value,
            }
            for item in view.active_hypotheses
        ],
        "active_support_clauses": [
            {
                "clause_id": item.clause_id,
                "mechanism": item.mechanism.value,
                "requirements": [
                    requirement.predicate_kind.value
                    for requirement in item.requirements
                ],
            }
            for item in view.active_support_clauses
        ],
        "view_sha256": view.view_sha256,
    }
    lint_provider_ontology_payload_v23(
        payload=payload,
        hidden_mechanisms=view.hidden_mechanisms,
    )
    return payload


def lint_provider_ontology_payload_v23(
    *,
    payload: object,
    hidden_mechanisms: tuple[MechanismV22, ...],
) -> None:
    """Fail closed if an evaluator-only mechanism label leaks into a payload."""

    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).casefold()
    for mechanism in hidden_mechanisms:
        words = mechanism.value.casefold().split("_")
        forbidden = {
            mechanism.value.casefold(),
            "-".join(words),
            " ".join(words),
        }
        leaked = next((token for token in forbidden if token in rendered), None)
        if leaked is not None:
            raise ValueError("hidden mechanism leaked into Provider-visible ontology")


__all__ = (
    "ActiveOntologyViewV23",
    "REGISTERED_MECHANISMS_V23",
    "build_active_ontology_view_v23",
    "lint_provider_ontology_payload_v23",
    "provider_ontology_payload_v23",
)
