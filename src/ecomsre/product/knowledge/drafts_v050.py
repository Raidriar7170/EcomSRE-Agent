"""Versioned model draft: semantic choices stay with the model, bindings with Runtime."""

from typing import Literal
from pydantic import Field
from ecomsre.product.investigation.contracts import StrictModel
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.knowledge.candidates_v050 import KnowledgeProposal
from ecomsre.product.knowledge.expressions import Aggregate, DerivedExpression
from ecomsre.product.knowledge.observations_v050 import ResourceDependency

PROTOCOL = "knowledge-draft-v050.1"
TASK = "propose_detection_draft_v050_1"


class DraftExpression(StrictModel):
    numerator: Aggregate
    denominator: Aggregate | None
    comparator: Literal["gt", "ge", "lt", "le"]
    threshold: float = Field(allow_inf_nan=False)
    threshold_unit: Literal[
        "PERCENT", "BYTES", "PERCENT_PER_SECOND", "BYTES_PER_SECOND", "RATIO"
    ]
    dependency_aliases: list[str] = Field(min_length=2, max_length=12)


class Comparison(StrictModel):
    evidence_alias: str
    service: str
    inference: str = Field(min_length=1, max_length=300)


class CandidateDraft(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,79}$")
    target: str
    broad_domain: Literal[
        "RUNTIME", "RESOURCE", "CONFIGURATION", "DEPENDENCY", "APPLICATION", "UNKNOWN"
    ]
    member_incidents: list[str] = Field(min_length=2, max_length=12)
    predicates: list[str] = Field(min_length=1, max_length=3)
    expression: DraftExpression | None
    target_support: list[str] = Field(max_length=24)
    target_counterevidence: list[str] = Field(max_length=24)
    comparison_context: list[Comparison] = Field(max_length=12)
    confusable_patterns: list[str] = Field(min_length=1, max_length=5)
    prediction: str = Field(min_length=1, max_length=500)
    inapplicable_conditions: list[str] = Field(min_length=1, max_length=5)


class KnowledgeDraft(StrictModel):
    disposition: Literal["CANDIDATE", "NO_CANDIDATE", "NEEDS_OBSERVATION"]
    candidate: CandidateDraft | None
    reason: str = Field(min_length=1, max_length=500)


def strict_schema(schema):
    """Normalize only structural optionality; semantic validators remain local."""

    def visit(node):
        if isinstance(node, list):
            return [visit(x) for x in node]
        if not isinstance(node, dict):
            return node
        result = {k: visit(v) for k, v in node.items() if k not in {"default", "title"}}
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return visit(schema.model_json_schema())


def draft_view(discovery, feedback=None):
    evidence, dependencies = {}, {}
    for session in discovery["sessions"]:
        event = session["incident_id"]
        for observation in session["observations"]:
            services = sorted(
                {
                    r.get("service")
                    for r in observation["records"]
                    if isinstance(r.get("service"), str)
                }
            )
            complete = (
                observation["status"] == "SUCCESS_NONEMPTY"
                and not observation["truncated"]
            )
            binding = dict(
                snapshot=discovery["snapshot_sha256"],
                incident_id=event,
                evidence_ref=observation["evidence_ref"],
                window=observation["window"],
                services=services,
            )
            alias = "e-" + sha(binding)
            evidence[alias] = dict(
                **binding,
                source=observation["source"],
                status=observation["status"],
                truncated=observation["truncated"],
                covered_services=observation["covered_services"],
                allowed_target_services=[
                    s
                    for s in services
                    if complete and s in observation["covered_services"]
                ],
                allowed_roles=[
                    "target_support",
                    "target_counterevidence",
                    "comparison_context",
                ]
                if complete
                else [],
                records=observation["records"],
            )
        for dependency in session["deployable_resource_dependencies"]:
            binding = dict(
                snapshot=discovery["snapshot_sha256"], incident_id=event, **dependency
            )
            dependencies["d-" + sha(binding)] = binding
    return dict(
        protocol=PROTOCOL,
        snapshot_sha256=discovery["snapshot_sha256"],
        sessions=[
            {k: s[k] for k in ("incident_id", "status", "hypotheses", "decisions")}
            for s in discovery["sessions"]
        ],
        evidence_catalog=evidence,
        dependency_catalog=dependencies,
        predicate_catalog=discovery["predicate_catalog"],
        feature_catalog=discovery["feature_catalog"],
        feedback=feedback,
        constraints="Propose a PATTERN_ONLY conjunction supported by these seen events, or abstain. Choose every predicate, numeric operator and threshold yourself. Use evidence aliases with actual service records. target_support/counterevidence must concern the proposed target; comparison_context is audit-only model inference and NEVER a predicate or support. If cross-service health must be a trigger, report NEEDS_OBSERVATION: this DSL cannot express cross-service conditions. Context cannot replace required target sources. Choose collected dependency aliases, one per member with identical deployable semantics, for an expression; Runtime binds window, sampling, units and versions. All predicates AND expression must hold, not alternatives. Incomplete evidence remains incomplete. No new reads are available in this replay.",
    )


def compile_draft(draft, view):
    if draft.disposition != "CANDIDATE":
        if draft.candidate is not None:
            raise ValueError("ABSTENTION_WITH_CANDIDATE")
        raise ValueError(draft.disposition)
    if draft.candidate is None:
        raise ValueError("CANDIDATE_MISSING")
    d = draft.candidate

    def resolve(alias, target_role=True, service=None):
        row = view["evidence_catalog"].get(alias)
        if row is None or row["snapshot"] != view["snapshot_sha256"]:
            raise ValueError("EVIDENCE_ALIAS_SNAPSHOT_MISMATCH")
        if row["incident_id"] not in d.member_incidents:
            raise ValueError("EVIDENCE_MEMBER_MISMATCH")
        wanted = d.target if target_role else service
        if wanted not in row["allowed_target_services"]:
            raise ValueError(
                "TARGET_EVIDENCE_ROLE_MISMATCH"
                if target_role
                else "COMPARISON_SERVICE_MISMATCH"
            )
        return row["evidence_ref"]

    support = [resolve(a) for a in d.target_support]
    against = [resolve(a) for a in d.target_counterevidence]
    context = [
        dict(
            c.model_dump(),
            evidence_ref=resolve(c.evidence_alias, False, c.service),
            authority="AUDIT_ONLY_MODEL_INFERENCE",
        )
        for c in d.comparison_context
    ]
    expression, dependency = None, None
    if d.expression is not None:
        selected = [
            view["dependency_catalog"].get(a) for a in d.expression.dependency_aliases
        ]
        if any(r is None or r["snapshot"] != view["snapshot_sha256"] for r in selected):
            raise ValueError("DEPENDENCY_ALIAS_SNAPSHOT_MISMATCH")
        if len(selected) != len(d.member_incidents) or {
            r["incident_id"] for r in selected
        } != set(d.member_incidents):
            raise ValueError("DEPENDENCY_MEMBER_MISMATCH")
        for r in selected:
            if (
                r["target"] != d.target
                or r["availability"] != "BOUND_OBSERVATION"
                or not set(r["supporting_refs"]) & set(support)
            ):
                raise ValueError("DEPENDENCY_NOT_SUPPORTED")
        if any(r["dependency"] != selected[0]["dependency"] for r in selected):
            raise ValueError("DEPENDENCY_SEMANTICS_MISMATCH")
        dependency = ResourceDependency.model_validate(selected[0]["dependency"])
        expression = DerivedExpression(
            **d.expression.model_dump(exclude={"dependency_aliases"}),
            threshold_provenance=view["snapshot_sha256"],
            window_seconds=dependency.sampling_window_seconds,
            minimum_samples=dependency.sample_count,
        )
    proposal = KnowledgeProposal(
        **d.model_dump(
            exclude={
                "expression",
                "target_support",
                "target_counterevidence",
                "comparison_context",
            }
        ),
        kind="PATTERN_ONLY",
        expression=expression,
        resource_dependency=dependency,
        supporting_refs=support,
        counter_evidence_refs=against,
    )
    return proposal, context


def safe_parameters(arguments, view):
    """Bounded structural diagnostics only: no raw strings, prose or hidden reasoning."""
    import json

    try:
        data = json.loads(arguments)
    except (ValueError, TypeError):
        return {"status": "UNRECOVERABLE_JSON"}
    allowed = {
        "cpu_percent",
        "memory_bytes",
        "mean",
        "max",
        "delta",
        "rate",
        "gt",
        "ge",
        "lt",
        "le",
        "PERCENT",
        "BYTES",
        "RATIO",
        "PERCENT_PER_SECOND",
        "BYTES_PER_SECOND",
        "CANDIDATE",
        "NO_CANDIDATE",
        "NEEDS_OBSERVATION",
    }
    aliases = set(view["evidence_catalog"]) | set(view["dependency_catalog"])
    safe_keys = {
        "disposition",
        "candidate",
        "expression",
        "numerator",
        "denominator",
        "field",
        "operator",
        "comparator",
        "threshold",
        "threshold_unit",
        "dependency_aliases",
        "target_support",
        "target_counterevidence",
    }
    result = []

    def walk(value, path):
        if len(result) >= 40:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                if k in safe_keys:
                    walk(v, path + [k])
        elif isinstance(value, list):
            for i, v in enumerate(value[:24]):
                walk(v, path + [i])
        else:
            row = {"path": path, "type": type(value).__name__}
            if (
                value is None
                or type(value) in (int, float)
                or (isinstance(value, str) and value in allowed)
            ):
                row["value"] = value
            elif isinstance(value, str) and value in aliases:
                row["catalog_entry"] = value
            else:
                row["status"] = "UNSAFE_OR_UNKNOWN_VALUE_NOT_RETAINED"
            result.append(row)

    walk(data, [])
    return {"status": "SANITIZED_PARAMETERS_ONLY", "fields": result}
