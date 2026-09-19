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


# Explicit successor; v050.1 functions and retained outputs remain replayable.
SCOPED_PROTOCOL = "knowledge-draft-v050.2"
SCOPED_TASK = "propose_detection_draft_v050_2"


class ScopedKnowledgeDraft(KnowledgeDraft):
    binding_id: str


def scoped_view(old, *, request_key, target, members, feedback=None):
    """Canonical request-bound mapping; no truth-based or value-based selection."""
    from copy import deepcopy

    if "evidence_catalog" not in old:
        discovery = old
        old = draft_view(discovery)
        observations = {
            o["evidence_ref"]: o
            for session in discovery["sessions"]
            for o in session["observations"]
        }
        for row in old["evidence_catalog"].values():
            observation = observations[row["evidence_ref"]]
            row["requested_services"] = observation.get(
                "targets", observation["covered_services"]
            )
            row["record_count"] = observation.get(
                "record_count", len(observation["records"])
            )
            row["object_sha256"] = observation.get("object_sha256")
    members = sorted(members)
    if len(members) != len(set(members)) or len(members) < 2:
        raise ValueError("DISTINCT_SCOPE_MEMBERS_REQUIRED")
    member_map = {f"I{i:02}": m for i, m in enumerate(members, 1)}
    evidence, comparison, gaps, dependencies = {}, {}, [], {}
    for alias, row in sorted(old["evidence_catalog"].items()):
        if row["incident_id"] not in members:
            continue
        if row["snapshot"] != old["snapshot_sha256"]:
            raise ValueError("SOURCE_SNAPSHOT_MISMATCH")
        for service in sorted(row["services"]):
            bound = deepcopy(row)
            bound["records"] = [
                r for r in row["records"] if r.get("service") == service
            ]
            bound["services"] = [service]
            bound["allowed_target_services"] = (
                [service] if service in row["allowed_target_services"] else []
            )
            bound["original_alias"] = alias
            if not bound["allowed_target_services"]:
                continue
            catalog, prefix = (
                (evidence, "E") if service == target else (comparison, "C")
            )
            catalog[f"{prefix}{len(catalog) + 1:02}"] = bound
        requested = set(row.get("requested_services", row["services"])) | set(
            row["services"]
        )
        missing = sorted(requested - set(row["allowed_target_services"]))
        if missing or not row["allowed_target_services"]:
            gaps.append(
                dict(
                    member=next(
                        k for k, v in member_map.items() if v == row["incident_id"]
                    ),
                    services=missing,
                    source=row["source"],
                    status=row["status"],
                    truncated=row["truncated"],
                    covered_services=row["covered_services"],
                    window=row["window"],
                    record_count=row.get("record_count", len(row["records"])),
                    reason="NOT_ADMISSIBLE_TARGET_EVIDENCE",
                )
            )
    for alias, row in sorted(old["dependency_catalog"].items()):
        if row["incident_id"] not in members or row["target"] != target:
            continue
        if row["snapshot"] != old["snapshot_sha256"]:
            raise ValueError("SOURCE_SNAPSHOT_MISMATCH")
        if row["availability"] == "BOUND_OBSERVATION":
            dependencies[f"D{len(dependencies) + 1:02}"] = dict(
                deepcopy(row), original_alias=alias
            )
        else:
            gaps.append(
                dict(
                    member=next(
                        k for k, v in member_map.items() if v == row["incident_id"]
                    ),
                    source="RESOURCES",
                    dependency=row["dependency"],
                    availability=row["availability"],
                    reason="DEPENDENCY_NOT_COLLECTED_OR_INCOMPLETE",
                )
            )
    view = dict(
        protocol=SCOPED_PROTOCOL,
        request_key=request_key,
        snapshot_sha256=old["snapshot_sha256"],
        target=target,
        members=member_map,
        target_evidence=evidence,
        comparison_evidence=comparison,
        dependencies=dependencies,
        gaps=gaps,
        predicate_catalog=old["predicate_catalog"],
        feature_catalog=old["feature_catalog"],
        feedback=feedback,
    )
    view["binding_id"] = sha(view)
    return view


def _require_scoped_binding(view):
    if sha({k: v for k, v in view.items() if k != "binding_id"}) != view["binding_id"]:
        raise ValueError("SCOPED_BINDING_MISMATCH")


def scoped_model_view(view):
    """Deterministic per-service projection; full numeric samples retained once."""
    _require_scoped_binding(view)
    import json

    def project(row):
        from collections import Counter
        import json

        # Identical records are represented by a lossless value + multiplicity.
        counts = Counter(
            json.dumps(r, sort_keys=True, separators=(",", ":")) for r in row["records"]
        )
        return dict(
            member=next(
                k for k, v in view["members"].items() if v == row["incident_id"]
            ),
            service=row["services"][0],
            source=row["source"],
            window=row["window"],
            status=row["status"],
            truncated=row["truncated"],
            covered_services=row["covered_services"],
            records=_factor_records(
                [dict(value=json.loads(r), count=n) for r, n in sorted(counts.items())]
            ),
        )

    result = dict(
        protocol=view["protocol"],
        binding_id=view["binding_id"],
        scope=dict(
            target=view["target"],
            members=list(view["members"]),
            selection="RUNTIME_SCOPE_FROM_NORMAL_PRODUCT_IDENTITIES; NOT_MODEL_TARGET_DISCOVERY",
        ),
        target_evidence={k: project(v) for k, v in view["target_evidence"].items()},
        comparison_evidence={
            k: project(v) for k, v in view["comparison_evidence"].items()
        },
        dependencies={
            k: dict(
                member=next(
                    i for i, m in view["members"].items() if m == v["incident_id"]
                ),
                target=v["target"],
                dependency=v["dependency"],
                supporting_handles=[
                    i
                    for i, e in view["target_evidence"].items()
                    if e["evidence_ref"] in v["supporting_refs"]
                ],
            )
            for k, v in view["dependencies"].items()
        },
        gaps=view["gaps"],
        predicate_catalog=view["predicate_catalog"],
        feature_catalog=view["feature_catalog"],
        feedback=view["feedback"],
    )

    # Window dictionary removes repeated timestamps without losing alignment.
    windows = {}
    for section in ("target_evidence", "comparison_evidence"):
        for row in result[section].values():
            encoded = json.dumps(row["window"], sort_keys=True)
            if encoded not in windows:
                windows[encoded] = f"W{len(windows) + 1:02}"
            row["window"] = windows[encoded]
    gaps = []
    for original in result["gaps"]:
        row = dict(original)
        if "window" in row:
            encoded = json.dumps(row["window"], sort_keys=True)
            if encoded not in windows:
                windows[encoded] = f"W{len(windows) + 1:02}"
            row["window"] = windows[encoded]
        gaps.append(row)
    result["windows"] = {alias: json.loads(value) for value, alias in windows.items()}
    columns = sorted({k for row in gaps for k in row})
    result["gaps"] = {
        "columns": columns,
        "rows": [[row.get(k) for k in columns] for row in gaps],
        "null_means": "FIELD_NOT_APPLICABLE; NEVER_HEALTHY_OR_ZERO",
    }
    return result


def scoped_schema(view):
    """The exact schema sent on the wire; empty catalogs use maxItems=0."""
    _require_scoped_binding(view)
    schema = strict_schema(ScopedKnowledgeDraft)
    schema["properties"]["binding_id"]["enum"] = [view["binding_id"]]
    props = schema["$defs"]["CandidateDraft"]["properties"]
    props["target"]["enum"] = [view["target"]]

    def choices(node, values):
        if values:
            node["items"] = {"type": "string", "enum": list(values)}
        else:
            node.pop("minItems", None)
            node["maxItems"] = 0

    choices(props["member_incidents"], view["members"])
    props["member_incidents"]["minItems"] = len(view["members"])
    props["member_incidents"]["maxItems"] = len(view["members"])
    choices(props["predicates"], view["predicate_catalog"])
    for field in ("target_support", "target_counterevidence"):
        choices(props[field], view["target_evidence"])
    # Each comparison alternative binds service and handle together.
    alternatives = []
    comparison_services = sorted(
        {r["services"][0] for r in view["comparison_evidence"].values()}
    )
    for service in comparison_services:
        from copy import deepcopy

        node = deepcopy(schema["$defs"]["Comparison"])
        node["properties"]["evidence_alias"]["enum"] = [
            a
            for a, r in view["comparison_evidence"].items()
            if r["services"] == [service]
        ]
        node["properties"]["service"]["enum"] = [service]
        alternatives.append(node)
    if alternatives:
        props["comparison_context"]["items"] = {"anyOf": alternatives}
    else:
        props["comparison_context"]["maxItems"] = 0
    dep = schema["$defs"]["DraftExpression"]["properties"]["dependency_aliases"]
    choices(dep, view["dependencies"])
    # A scoped Level B task needs a common collected semantic for EVERY member.
    semantics = {sha(r["dependency"]) for r in view["dependencies"].values()}
    complete = any(
        {
            r["incident_id"]
            for r in view["dependencies"].values()
            if sha(r["dependency"]) == s
        }
        == set(view["members"].values())
        for s in semantics
    )
    if not complete:
        props["expression"] = {"type": "null"}

    def enum_count(x):
        if isinstance(x, dict):
            return len(x.get("enum", [])) + sum(
                enum_count(v) for k, v in x.items() if k != "enum"
            )
        if isinstance(x, list):
            return sum(map(enum_count, x))
        return 0

    if enum_count(schema) > 1000:
        raise ValueError("SCHEMA_SCOPE_REQUIRES_BOUNDED_SELECTION")
    return schema


def diagnose_scoped_draft(data, view):
    """Read-only aggregate diagnostics; never repair or register model content."""
    _require_scoped_binding(view)
    issues = []

    def issue(path, code):
        issues.append(dict(path=path, code=code))

    if not isinstance(data, dict):
        return [dict(path="", code="OBJECT_REQUIRED")]
    from copy import deepcopy

    data = deepcopy(data)
    raw = data.get("candidate")
    if isinstance(raw, dict):
        for field in (
            "member_incidents",
            "target_support",
            "target_counterevidence",
            "predicates",
        ):
            value = raw.get(field)
            if not isinstance(value, list) or any(
                not isinstance(v, str) for v in value
            ):
                issue("candidate." + field, "STRING_ARRAY_REQUIRED")
                raw[field] = []
        value = raw.get("comparison_context")
        if not isinstance(value, list) or any(
            not isinstance(v, dict)
            or not isinstance(v.get("evidence_alias"), str)
            or not isinstance(v.get("service"), str)
            for v in value
        ):
            issue("candidate.comparison_context", "COMPARISON_SHAPE_INVALID")
            raw["comparison_context"] = []
        expr = raw.get("expression")
        if expr is not None:
            if not isinstance(expr, dict):
                issue("candidate.expression", "EXPRESSION_OBJECT_REQUIRED")
                raw["expression"] = None
            else:
                aliases = expr.get("dependency_aliases")
                if not isinstance(aliases, list) or any(
                    not isinstance(v, str) for v in aliases
                ):
                    issue(
                        "candidate.expression.dependency_aliases",
                        "STRING_ARRAY_REQUIRED",
                    )
                    expr["dependency_aliases"] = []
    if data.get("binding_id") != view["binding_id"]:
        issue("binding_id", "SCOPED_BINDING_MISMATCH")
    draft = data.get("candidate")
    if not isinstance(draft, dict):
        if data.get("disposition") == "CANDIDATE":
            issue("candidate", "CANDIDATE_MISSING")
        return issues
    if data.get("disposition") != "CANDIDATE":
        issue("candidate", "ABSTENTION_WITH_CANDIDATE")
    if draft.get("target") != view["target"]:
        issue("candidate.target", "TARGET_SCOPE_MISMATCH")
    members = draft.get("member_incidents", [])
    if set(members) != set(view["members"]) or len(members) != len(set(members)):
        issue("candidate.member_incidents", "MEMBER_SCOPE_MISMATCH")
    for field in ("target_support", "target_counterevidence"):
        for n, alias in enumerate(draft.get(field, [])):
            row = view["target_evidence"].get(alias)
            if row is None:
                issue(f"candidate.{field}.{n}", "UNKNOWN_TARGET_EVIDENCE")
            elif row["incident_id"] not in [
                view["members"][i] for i in members if i in view["members"]
            ]:
                issue(f"candidate.{field}.{n}", "EVIDENCE_MEMBER_MISMATCH")
    for n, item in enumerate(draft.get("comparison_context", [])):
        row = view["comparison_evidence"].get(item.get("evidence_alias"))
        if row is None or item.get("service") not in row["services"]:
            issue(f"candidate.comparison_context.{n}", "COMPARISON_SERVICE_MISMATCH")
    for n, predicate in enumerate(draft.get("predicates", [])):
        if predicate not in view["predicate_catalog"]:
            issue(f"candidate.predicates.{n}", "UNKNOWN_PREDICATE")
    expr = draft.get("expression")
    if expr:
        if (
            scoped_schema(view)["$defs"]["CandidateDraft"]["properties"][
                "expression"
            ].get("type")
            == "null"
        ):
            issue("candidate.expression", "EXPRESSION_UNAVAILABLE_IN_SCOPE")
        aliases = expr.get("dependency_aliases", [])
        if len(set(aliases)) != len(aliases):
            issue("candidate.expression.dependency_aliases", "DUPLICATE_DEPENDENCY")
        rows = []
        for n, alias in enumerate(aliases):
            row = view["dependencies"].get(alias)
            if row is None:
                issue(
                    f"candidate.expression.dependency_aliases.{n}", "UNKNOWN_DEPENDENCY"
                )
            else:
                rows.append(row)
                support = {
                    view["target_evidence"][a]["evidence_ref"]
                    for a in draft.get("target_support", [])
                    if a in view["target_evidence"]
                }
                if not support.intersection(row["supporting_refs"]):
                    issue(
                        f"candidate.expression.dependency_aliases.{n}",
                        "DEPENDENCY_NOT_SUPPORTED",
                    )
        if {r["incident_id"] for r in rows} != set(view["members"].values()) or len(
            rows
        ) != len(view["members"]):
            issue(
                "candidate.expression.dependency_aliases", "DEPENDENCY_MEMBER_MISMATCH"
            )
        if len({sha(r["dependency"]) for r in rows}) > 1:
            issue(
                "candidate.expression.dependency_aliases",
                "DEPENDENCY_SEMANTICS_MISMATCH",
            )
        try:
            # Validate unit algebra even if unrelated reference fields are wrong.
            DerivedExpression(
                **{k: v for k, v in expr.items() if k != "dependency_aliases"},
                threshold_provenance=view["snapshot_sha256"],
                window_seconds=rows[0]["dependency"]["sampling_window_seconds"]
                if rows
                else 10,
                minimum_samples=rows[0]["dependency"]["sample_count"] if rows else 2,
            )
        except ValueError:
            issue("candidate.expression", "UNIT_MISMATCH")
    try:
        from ecomsre.product.knowledge.compiler import _predicate_parts

        sources = {
            _predicate_parts(p)[1].value
            for p in draft.get("predicates", [])
            if p in view["predicate_catalog"]
        }
        if expr:
            sources.add("RESOURCES")
        if len(sources) < 2 and draft.get("predicates") != ["core:RUNTIME_NOT_RUNNING"]:
            issue("candidate.predicates", "TWO_SOURCES_REQUIRED")
    except ValueError:
        issue("candidate.predicates", "INVALID_PREDICATE")
    return issues


def compile_scoped_draft(draft, view):
    issues = diagnose_scoped_draft(draft.model_dump(mode="json"), view)
    if issues:
        raise ValueError(";".join(i["code"] for i in issues))
    data = draft.model_dump(mode="json", exclude={"binding_id"})
    if data["candidate"] is None:
        raise ValueError(data["disposition"])
    data["candidate"]["member_incidents"] = [
        view["members"][i] for i in data["candidate"]["member_incidents"]
    ]
    legacy = dict(
        snapshot_sha256=view["snapshot_sha256"],
        evidence_catalog=view["target_evidence"] | view["comparison_evidence"],
        dependency_catalog=view["dependencies"],
    )
    return compile_draft(KnowledgeDraft.model_validate(data), legacy)


def _factor_records(records):
    """Lossless deterministic compression: shared fields plus per-row differences."""
    if not records:
        return dict(shared={}, rows=[])
    values = [r["value"] for r in records]
    common = {
        k: v for k, v in values[0].items() if all(k in r and r[k] == v for r in values)
    }
    return dict(
        shared=common,
        rows=[
            dict(
                value={k: v for k, v in r["value"].items() if k not in common},
                count=r["count"],
            )
            for r in records
        ],
    )
