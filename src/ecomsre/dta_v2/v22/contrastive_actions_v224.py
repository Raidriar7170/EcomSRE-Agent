"""Contrastive all-candidate Resources action for DTA v2.2.4."""

from __future__ import annotations

from typing import Literal

from pydantic import StrictFloat, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.memory import (
    PredicateKindV22,
    ResourceSalientPayloadV22,
    SalientEvidenceMemoryV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    build_canonical_read_request_v22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay_target_coverage_v224 import (
    ReplayTargetCoverageModeV224,
    ReplayTargetCoverageV224,
)


class ResourceContrastRowV224(DtaModelV22):
    service: str
    cpu_p95_percent: StrictFloat
    cpu_baseline_ratio: StrictFloat | None
    memory_slope_bytes_per_second: StrictFloat
    new_predicate_kinds: tuple[PredicateKindV22, ...]


class ContrastiveResourceDeltaV224(DtaModelV22):
    schema_version: Literal["dta-v22.4.contrastive-resource-delta.v1"]
    action_id: str
    contrast_rows: tuple[ResourceContrastRowV224, ...]
    delta_sha256: str

    @model_validator(mode="after")
    def require_delta(self) -> "ContrastiveResourceDeltaV224":
        services = tuple(item.service for item in self.contrast_rows)
        if services != tuple(sorted(set(services))):
            raise ValueError("contrastive Resources delta rows are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"delta_sha256"})
        )
        if self.delta_sha256 != expected:
            raise ValueError("contrastive Resources delta digest differs")
        return self


class ContrastiveResourceActionV224(EvidenceActionV22):
    """A versioned Resources action that symmetrically covers every candidate."""

    @model_validator(mode="after")
    def require_contrastive_resource_action(self) -> "ContrastiveResourceActionV224":
        if self.source is not EvidenceSourceV22.RESOURCES:
            raise ValueError("contrastive Resources action has a non-Resources source")
        if not 2 <= len(self.target_services) <= 4:
            raise ValueError("contrastive Resources action requires two to four targets")
        if not self.action_id.startswith("a:resources:all-candidates:"):
            raise ValueError("contrastive Resources action ID differs")
        expected_cost = min(3.0, 1.5 + 0.5 * (len(self.target_services) - 1))
        if self.weighted_cost != expected_cost:
            raise ValueError("contrastive Resources action cost differs")
        expected_dominated = tuple(
            f"a:resources:{service}" for service in self.target_services
        )
        if self.dominates_action_ids != expected_dominated:
            raise ValueError("contrastive Resources action dominance differs")
        return self


def _build_contrastive_resource_action_v224(
    *, candidate_services: tuple[str, ...]
) -> ContrastiveResourceActionV224:
    request = build_canonical_read_request_v22(
        source=EvidenceSourceV22.RESOURCES,
        target_services=candidate_services,
        sampling_window_seconds=10,
        sample_count=5,
    )
    digest = semantic_sha256_v22(list(candidate_services))[:12]
    payload = {
        "schema_version": "dta-v22.evidence-action.v1",
        "action_id": f"a:resources:all-candidates:{digest}",
        "source": EvidenceSourceV22.RESOURCES,
        "target_services": candidate_services,
        "request": request,
        "coverage_keys": tuple(
            f"resources:{service}:read" for service in candidate_services
        ),
        "weighted_cost": min(3.0, 1.5 + 0.5 * (len(candidate_services) - 1)),
        "request_sha256": request.request_sha256,
        "dominates_action_ids": tuple(
            f"a:resources:{service}" for service in candidate_services
        ),
    }
    draft = ContrastiveResourceActionV224.model_construct(
        **payload,
        action_sha256="0" * 64,
    )
    return ContrastiveResourceActionV224.model_validate(
        {
            **payload,
            "action_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"action_sha256"})
            ),
        }
    )


def contrastive_resource_action_if_eligible_v224(
    *,
    coverage: ReplayTargetCoverageV224,
    resources_enabled: bool,
    unresolved_resource_hypotheses: int,
    remaining_budget: float,
    bundle_mode: bool,
) -> ContrastiveResourceActionV224 | None:
    coverage = ReplayTargetCoverageV224.model_validate(
        coverage.model_dump(mode="python")
    )
    if remaining_budget < 0:
        raise ValueError("remaining evidence budget cannot be negative")
    if unresolved_resource_hypotheses < 0:
        raise ValueError("unresolved Resources hypothesis count cannot be negative")
    if (
        not bundle_mode
        or not resources_enabled
        or coverage.source is not EvidenceSourceV22.RESOURCES
        or coverage.coverage_mode is not ReplayTargetCoverageModeV224.TARGET_COMPLETE
        or not 2 <= len(coverage.candidate_services) <= 4
        or unresolved_resource_hypotheses < 2
    ):
        return None
    action = _build_contrastive_resource_action_v224(
        candidate_services=coverage.candidate_services
    )
    return action if action.weighted_cost <= remaining_budget else None


def build_contrastive_resource_delta_v224(
    *,
    action: ContrastiveResourceActionV224,
    before_memory: SalientEvidenceMemoryV22 | None,
    after_memory: SalientEvidenceMemoryV22,
) -> ContrastiveResourceDeltaV224:
    action = ContrastiveResourceActionV224.model_validate(
        action.model_dump(mode="python")
    )
    before_ids = (
        set()
        if before_memory is None
        else {item.predicate_id for item in before_memory.predicates}
    )
    facts = {
        item.service: item.payload
        for item in after_memory.salient_facts
        if item.source is EvidenceSourceV22.RESOURCES
        and isinstance(item.payload, ResourceSalientPayloadV22)
        and item.service in set(action.target_services)
    }
    if set(facts) != set(action.target_services):
        raise ValueError("contrastive Resources delta lacks a target fact")
    rows = tuple(
        ResourceContrastRowV224(
            service=service,
            cpu_p95_percent=facts[service].cpu_p95_percent,
            cpu_baseline_ratio=facts[service].cpu_baseline_ratio,
            memory_slope_bytes_per_second=(
                facts[service].memory_slope_bytes_per_second
            ),
            new_predicate_kinds=tuple(
                sorted(
                    {
                        item.predicate_kind
                        for item in after_memory.predicates
                        if item.source is EvidenceSourceV22.RESOURCES
                        and item.service == service
                        and item.predicate_id not in before_ids
                    },
                    key=lambda item: item.value,
                )
            ),
        )
        for service in action.target_services
    )
    payload = {
        "schema_version": "dta-v22.4.contrastive-resource-delta.v1",
        "action_id": action.action_id,
        "contrast_rows": tuple(item.model_dump(mode="json") for item in rows),
    }
    return ContrastiveResourceDeltaV224(
        schema_version="dta-v22.4.contrastive-resource-delta.v1",
        action_id=action.action_id,
        contrast_rows=rows,
        delta_sha256=semantic_sha256_v22(payload),
    )


__all__ = (
    "ContrastiveResourceDeltaV224",
    "ContrastiveResourceActionV224",
    "ResourceContrastRowV224",
    "build_contrastive_resource_delta_v224",
    "contrastive_resource_action_if_eligible_v224",
)
