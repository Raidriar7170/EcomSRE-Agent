"""A Product successor candidate shared by model and deterministic proposers."""

from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.investigation.contracts import StrictModel
from ecomsre.product.knowledge.compiler import _predicate_parts
from ecomsre.product.knowledge.expressions import (
    DerivedExpression,
    ExpressionOutcome,
    evaluate_expression,
)


from ecomsre.product.knowledge.observations_v050 import ResourceDependency


class KnowledgeProposal(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    kind: Literal["PATTERN_ONLY", "MECHANISM_SUPPORTED"]
    target: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    broad_domain: Literal[
        "RUNTIME", "RESOURCE", "CONFIGURATION", "DEPENDENCY", "APPLICATION", "UNKNOWN"
    ]
    member_incidents: list[str] = Field(min_length=2, max_length=12)
    predicates: list[str] = Field(min_length=1, max_length=3)
    expression: DerivedExpression | None
    resource_dependency: ResourceDependency | None = Field(default=None, exclude_if=lambda v: v is None)
    supporting_refs: list[str] = Field(min_length=1, max_length=24)
    counter_evidence_refs: list[str] = Field(max_length=24)
    confusable_patterns: list[str] = Field(min_length=1, max_length=5)
    prediction: str = Field(min_length=1, max_length=500)
    inapplicable_conditions: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def bounded_compilation(self):
        if self.resource_dependency is not None and (
            self.expression is None
            or self.expression.window_seconds != self.resource_dependency.sampling_window_seconds
            or self.expression.minimum_samples > self.resource_dependency.sample_count
        ):
            raise ValueError("resource dependency does not satisfy expression sampling")
        if len(set(self.member_incidents)) != len(self.member_incidents):
            raise ValueError("repeated incidents are not independent members")
        if len(set(self.predicates)) != len(self.predicates):
            raise ValueError("duplicate predicates")
        sources = {_predicate_parts(p)[1].value for p in self.predicates}
        if self.expression is not None:
            sources.add(self.expression.source)
        if len(sources) < 2 and self.predicates != ["core:RUNTIME_NOT_RUNNING"]:
            raise ValueError("candidate requires two evidence sources")
        return self

    @property
    def required_sources(self) -> tuple[str, ...]:
        sources = {_predicate_parts(p)[1].value for p in self.predicates}
        if self.expression is not None:
            sources.add(self.expression.source)
        return tuple(sorted(sources))


class CompiledKnowledge(StrictModel):
    schema_version: Literal["ecomsre.product.compiled-knowledge.v050"] = (
        "ecomsre.product.compiled-knowledge.v050"
    )
    registration_id: str
    environment_id: str
    capability_sha256: str
    origin: Literal["LLM", "DETERMINISTIC_MINER", "FIXTURE_ONLY"]
    source_request_key: str | None
    discovery_snapshot_sha256: str
    discovery_incident_ids: tuple[str, ...]
    proposal: KnowledgeProposal
    action_authority: Literal["NONE"] = "NONE"
    compiled_sha256: str

    @model_validator(mode="after")
    def binding(self):
        if (
            semantic_sha256_v22(
                self.model_dump(mode="json", exclude={"compiled_sha256"})
            )
            != self.compiled_sha256
        ):
            raise ValueError("compiled candidate binding differs")
        if (self.origin == "LLM") != (self.source_request_key is not None):
            raise ValueError("candidate source request differs")
        return self


def evaluate_candidate(
    candidate: CompiledKnowledge,
    *,
    target: str,
    memory,
    anomalies,
    observations: list[dict[str, Any]],
    incident_end=None,
) -> ExpressionOutcome:
    """The same compiled predicate/expression evaluation in Shadow and diagnosis."""
    proposal = candidate.proposal
    if proposal.resource_dependency is not None:
        from ecomsre.product.knowledge.observations_v050 import select_dependency
        if incident_end is None:
            return ExpressionOutcome(status="UNKNOWN", value=None, reason="DEPENDENCY_EVENT_WINDOW_MISSING")
        selected = select_dependency(proposal.resource_dependency, incident_end=incident_end, observations=observations, target=target)
        observations = [o for o in observations if o["source"] != "RESOURCES"] + selected
    if target != proposal.target:
        return ExpressionOutcome(status="FALSE", value=None, reason="TARGET_MISMATCH")
    refs: set[str] = set()
    for source in proposal.required_sources:
        if not any(
            o["source"] == source
            and o["status"] == "SUCCESS_NONEMPTY"
            and not o["truncated"]
            and target in o["covered_services"]
            for o in observations
        ):
            return ExpressionOutcome(
                status="UNKNOWN", value=None, reason="SOURCE_COVERAGE_INCOMPLETE"
            )
    for predicate in proposal.predicates:
        namespace, kind = predicate.split(":", 1)
        matches = [
            p
            for p in (memory.predicates if namespace == "core" else anomalies)
            if p.service == target
            and (namespace == "core" or p.strength.value == "STRONG")
            and (p.predicate_kind.value if namespace == "core" else p.kind.value)
            == kind
        ]
        if not matches:
            return ExpressionOutcome(
                status="FALSE", value=None, reason="BASE_PREDICATE_ABSENT"
            )
        refs.update(ref for match in matches for ref in match.evidence_refs)
    value = None
    if proposal.expression is not None:
        outcome = evaluate_expression(
            proposal.expression, target=target, observations=observations
        )
        if outcome.status != "TRUE":
            return outcome
        refs.update(outcome.evidence_refs)
        value = outcome.value
    return ExpressionOutcome(
        status="TRUE",
        value=value,
        reason="CANDIDATE_MATCHED",
        evidence_refs=tuple(sorted(refs)),
    )


def snapshot_observations(snapshots, memory) -> list[dict[str, Any]]:
    """Typed connector snapshots and resolvable memory references, not model facts."""
    observations = []
    for snapshot in snapshots:
        result = snapshot["connector_result"]
        action_id = snapshot["action"]["action_id"]
        refs = [
            r.evidence_ref for r in memory.evidence_refs if r.action_id == action_id
        ]
        if not refs:
            continue
        observations.append(
            {
                "evidence_ref": refs[0],
                "source": result["source"],
                "status": result["status"],
                "covered_services": result["covered_services"],
                "truncated": result["truncated"],
                "window": result["window"],
                "records": result["records"],
            }
        )
    return observations
