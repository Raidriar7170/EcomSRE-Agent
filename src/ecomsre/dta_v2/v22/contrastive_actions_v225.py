"""Contrastive all-candidate Resources action for DTA v2.2.5."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictFloat, model_validator

from ecomsre.dta_v2.v22.memory import (
    PredicateKindV22,
    ResourceSalientPayloadV22,
    SalientEvidenceMemoryV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    ReplayTargetCoverageModeV225,
    ReplayTargetCoverageV225,
)


class ResourceContrastRowV225(DtaModelV22):
    service: str
    cpu_p95_percent: StrictFloat
    cpu_baseline_ratio: StrictFloat | None
    memory_slope_bytes_per_second: StrictFloat
    new_predicate_kinds: tuple[PredicateKindV22, ...]


class ContrastiveResourceDeltaV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.contrastive-resource-delta.v1"]
    action_id: str
    contrast_rows: tuple[ResourceContrastRowV225, ...]
    delta_sha256: str

    @model_validator(mode="after")
    def require_delta(self) -> "ContrastiveResourceDeltaV225":
        services = tuple(item.service for item in self.contrast_rows)
        if services != tuple(sorted(set(services))):
            raise ValueError("contrastive Resources delta rows are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"delta_sha256"})
        )
        if self.delta_sha256 != expected:
            raise ValueError("contrastive Resources delta digest differs")
        return self


class ContrastiveResourceRequestV225(DtaModelV22):
    """Versioned multi-target request without changing frozen v2.2 contracts."""

    schema_version: Literal["dta-v22.5.contrastive-resource-request.v1"]
    source: Literal[EvidenceSourceV22.RESOURCES]
    target_services: tuple[str, ...] = Field(min_length=2, max_length=4)
    sampling_window_seconds: Literal[10]
    sample_count: Literal[5]
    request_sha256: str

    @model_validator(mode="after")
    def require_request(self) -> "ContrastiveResourceRequestV225":
        if self.target_services != tuple(sorted(set(self.target_services))):
            raise ValueError("contrastive Resources request targets are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("contrastive Resources request digest differs")
        return self


class ContrastiveResourceActionV225(DtaModelV22):
    """A versioned Resources action that symmetrically covers every candidate."""

    schema_version: Literal["dta-v22.5.contrastive-resource-action.v1"]
    action_id: str
    source: Literal[EvidenceSourceV22.RESOURCES]
    target_services: tuple[str, ...] = Field(min_length=2, max_length=4)
    request: ContrastiveResourceRequestV225
    coverage_keys: tuple[str, ...]
    weighted_cost: StrictFloat
    request_sha256: str
    dominates_action_ids: tuple[str, ...]
    action_sha256: str

    @model_validator(mode="after")
    def require_contrastive_resource_action(self) -> "ContrastiveResourceActionV225":
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
        if self.request.target_services != self.target_services:
            raise ValueError("contrastive Resources action request targets differ")
        if self.request_sha256 != self.request.request_sha256:
            raise ValueError("contrastive Resources action request digest differs")
        if self.coverage_keys != tuple(
            f"resources:{service}:read" for service in self.target_services
        ):
            raise ValueError("contrastive Resources action coverage differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"action_sha256"})
        )
        if self.action_sha256 != expected:
            raise ValueError("contrastive Resources action digest differs")
        return self


def _build_contrastive_resource_action_v225(
    *, candidate_services: tuple[str, ...]
) -> ContrastiveResourceActionV225:
    request_payload = {
        "schema_version": "dta-v22.5.contrastive-resource-request.v1",
        "source": EvidenceSourceV22.RESOURCES,
        "target_services": candidate_services,
        "sampling_window_seconds": 10,
        "sample_count": 5,
    }
    request = ContrastiveResourceRequestV225.model_validate(
        {
            **request_payload,
            "request_sha256": semantic_sha256_v22(request_payload),
        }
    )
    digest = semantic_sha256_v22(list(candidate_services))[:12]
    payload = {
        "schema_version": "dta-v22.5.contrastive-resource-action.v1",
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
    digest_payload = {**payload, "request": request.model_dump(mode="json")}
    return ContrastiveResourceActionV225.model_validate(
        {
            **payload,
            "action_sha256": semantic_sha256_v22(digest_payload),
        }
    )


def contrastive_resource_action_if_eligible_v225(
    *,
    coverage: ReplayTargetCoverageV225,
    resources_enabled: bool,
    unresolved_resource_hypotheses: int,
    remaining_budget: float,
    bundle_mode: bool,
) -> ContrastiveResourceActionV225 | None:
    coverage = ReplayTargetCoverageV225.model_validate(
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
        or coverage.coverage_mode is not ReplayTargetCoverageModeV225.TARGET_COMPLETE
        or not 2 <= len(coverage.candidate_services) <= 4
        or unresolved_resource_hypotheses < 2
    ):
        return None
    action = _build_contrastive_resource_action_v225(
        candidate_services=coverage.candidate_services
    )
    return action if action.weighted_cost <= remaining_budget else None


def build_contrastive_resource_delta_v225(
    *,
    action: ContrastiveResourceActionV225,
    before_memory: SalientEvidenceMemoryV22 | None,
    after_memory: SalientEvidenceMemoryV22,
) -> ContrastiveResourceDeltaV225:
    action = ContrastiveResourceActionV225.model_validate(
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
        ResourceContrastRowV225(
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
        "schema_version": "dta-v22.5.contrastive-resource-delta.v1",
        "action_id": action.action_id,
        "contrast_rows": tuple(item.model_dump(mode="json") for item in rows),
    }
    return ContrastiveResourceDeltaV225(
        schema_version="dta-v22.5.contrastive-resource-delta.v1",
        action_id=action.action_id,
        contrast_rows=rows,
        delta_sha256=semantic_sha256_v22(payload),
    )


__all__ = (
    "ContrastiveResourceDeltaV225",
    "ContrastiveResourceActionV225",
    "ContrastiveResourceRequestV225",
    "ResourceContrastRowV225",
    "build_contrastive_resource_delta_v225",
    "contrastive_resource_action_if_eligible_v225",
)
