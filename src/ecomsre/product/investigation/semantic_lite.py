"""Bounded C/D development protocol. Catalog reads capability metadata, not answers."""

from copy import deepcopy
from time import monotonic
from typing import Literal

from pydantic import Field

from .contracts import StrictModel
from .semantic_analysis import canonical_request, digest, finite
from .semantic_contracts import AnalysisRequest
from .semantic_representation import select_reference
from ecomsre.product.errors import ProductError

TASK = "investigate_semantic_lite_v1"
LIMITS = [
    "TRACE_SAMPLE_NOT_POPULATION",
    "HISTORICAL_REFERENCE_NOT_HEALTHY_BASELINE",
    "LOCAL_PREDICTION_NOT_CAUSAL_PROOF_OR_DISPROOF",
    "INDEPENDENT_RESIDUALS_RETAINED",
]


class HypothesisDelta(StrictModel):
    hypothesis_id: Literal["H1", "H2", "H3", "H4"]
    target: str
    explanation: str = Field(min_length=1, max_length=350)
    support: list[str] = Field(max_length=4)
    conflicts: list[str] = Field(max_length=4)


class Expectation(StrictModel):
    hypothesis_id: Literal["H1", "H2", "H3", "H4"]
    outcome: Literal["YES", "NO"]


class LiteReport(StrictModel):
    ranked_components: list[str] = Field(max_length=4)
    explanation: str = Field(max_length=900)
    references: list[str] = Field(max_length=6)
    residuals: list[str] = Field(max_length=4)
    limitations: list[str] = Field(min_length=1, max_length=4)
    conclusion: Literal["LOCAL_FINDING", "INSUFFICIENT_EVIDENCE", "ABSTAIN"]


class LiteDecision(StrictModel):
    action: Literal["analyze", "report"]
    question_id: str | None
    hypothesis_updates: list[HypothesisDelta] = Field(max_length=4)
    expectations: list[Expectation] = Field(max_length=2)
    rationale: str = Field(max_length=350)
    report: LiteReport | None


class ReportDecision(StrictModel):
    action: Literal["report"]
    rationale: str = Field(max_length=350)
    report: LiteReport


class AnalyzeDecision(StrictModel):
    action: Literal["analyze"]
    question_id: str
    hypothesis_updates: list[HypothesisDelta] = Field(max_length=4)
    expectations: list[Expectation] = Field(max_length=2)
    rationale: str = Field(max_length=350)


class TurnDecision(StrictModel):
    decision: AnalyzeDecision | ReportDecision


def bound_schema(parameters, view):
    """Bind actual identities; nested anyOf separates analyze/report fields."""
    parameters = deepcopy(parameters)
    definitions = parameters.get("$defs", {})

    def array_values(field, values):
        if values:
            field["items"] = dict(type="string", enum=sorted(values))
        else:
            field["maxItems"] = 0

    report = definitions.get("LiteReport", {}).get("properties", {})
    if report:
        array_values(report["ranked_components"], view["services"])
        array_values(report["references"], view["reference_handles"])
    delta = definitions.get("HypothesisDelta", {}).get("properties", {})
    if delta:
        if view["services"]:
            delta["target"] = dict(type="string", enum=sorted(view["services"]))
        for key in ("support", "conflicts"):
            array_values(delta[key], view["reference_handles"])
    analyze = definitions.get("AnalyzeDecision", {}).get("properties", {})
    if analyze:
        ids = [q["question_id"] for q in view["questions"]]
        if not ids:
            raise ValueError("EMPTY_CATALOG_REQUIRES_REPORT_SCHEMA")
        analyze["question_id"] = dict(type="string", enum=ids)
    return parameters


def reference_inventory(analysis):
    groups = {}
    by_scope = {}
    for ref in analysis.snapshot.get("references", {}).values():
        by_scope.setdefault(tuple(ref["scope"]), set()).add(ref["signal"])
    for scope, signals in by_scope.items():
        groups.setdefault(tuple(sorted(signals)), []).append(list(scope))
    return [
        dict(signals=list(signals), scopes=sorted(scopes, key=str))
        for signals, scopes in sorted(groups.items())
    ]


def representation_catalog(analysis):
    catalog, gaps = {}, []

    def add(request, description):
        key = "Q" + str(len(catalog) + 1)
        catalog[key] = dict(
            question_id=key,
            request=request.model_dump(),
            kind="continuous_observation",
            question=description,
            threshold=None,
        )

    meta = analysis.metadata()
    for service in sorted(analysis.services):
        add(
            AnalysisRequest(tool="profile_operations", target=service),
            "Inspect current operation/direction status counts and duration profile. Zero ERROR markers does not prove health.",
        )
        scopes = [(None, None)]
        if analysis.config["reference_view"] == "operation":
            scopes += sorted(
                {
                    (op, d)
                    for s, op, d in meta["operation_inventory"]
                    if s == service and op and d
                },
                key=str,
            )
        for operation, direction in scopes:
            for signal in ("duration_ms", "error_marker_fraction"):
                request = AnalysisRequest(
                    tool="compare_baseline",
                    target=service,
                    operation=operation,
                    direction=direction,
                    signal=signal,
                )
                ref, _, reason = select_reference(analysis.snapshot, request)
                if reason:
                    gaps.append(
                        dict(
                            service=service,
                            operation=operation,
                            direction=direction,
                            signal=signal,
                            reason=reason,
                        )
                    )
                else:
                    add(
                        request.model_copy(update={"reference_id": ref}),
                        "Inspect continuous historical change for this scope; sample support and missing values remain visible. Historical is not healthy.",
                    )
        neighbors = sorted(
            {
                n
                for edge in meta["observed_topology"]
                if service in edge
                for n in edge
                if n != service
            }
        )[:3]
        if neighbors:
            add(
                AnalysisRequest(
                    tool="compare_dependencies",
                    target=service,
                    neighbors=neighbors,
                    signal="duration_ms",
                ),
                "Inspect observed local edges, status distributions and matched historical changes. Shared ERROR markers do not establish causality.",
            )
    return catalog, gaps


def question_catalog(analysis):
    """Only identities, field presence, relationships and reference scope/time.

    No error/duration/reference values, root labels, or execute() calls. All
    candidate services remain visible, including those lacking a capability.
    Four question templates/service; no model-authored numerical expressions.
    """
    if analysis.representation:
        return representation_catalog(analysis)
    meta = analysis.metadata()
    refs = meta["references"]
    window = meta["windows"]["current"]
    rows = analysis.snapshot["records"]
    catalog, gaps = {}, []

    def add(request, kind, question, threshold=None):
        key = "Q" + str(len(catalog) + 1)
        catalog[key] = dict(
            question_id=key,
            request=request.model_dump(),
            kind=kind,
            question=question,
            threshold=threshold,
        )

    for service in sorted(meta["services"]):
        identities = [r for r in rows if r["service"] == service]
        if any(r.get("operation") for r in identities) and any(
            "error" in r for r in identities
        ):
            add(
                AnalysisRequest(tool="profile_operations", target=service),
                "operation_errors",
                "Does any observed operation/direction group have >=50% errors among known statuses? Unknown statuses remain missing.",
                0.5,
            )
        else:
            gaps.append(
                dict(
                    service=service,
                    tool="profile_operations",
                    reason="MISSING_OPERATION_OR_STATUS_FIELD",
                )
            )
        for signal, unit in [("duration_ms", "ms"), ("error_fraction", "fraction")]:
            candidates = sorted(
                k
                for k, v in refs.items()
                if v["scope"] == [service, None, None]
                and v["unit"] == unit
                and v["method"] == "trace_sample"
                and v["fixed_at"] < window[0]
                and v["window"][1] <= window[0]
                and v["window"][1] - v["window"][0] == window[1] - window[0]
            )
            if candidates:
                threshold = analysis.config["baseline"][signal]["threshold"]
                add(
                    AnalysisRequest(
                        tool="compare_baseline",
                        target=service,
                        reference_id=candidates[0],
                        signal=signal,
                    ),
                    "historical_increase",
                    f"Is the service-wide sampled {signal} at least {threshold} {unit} above its matched historical median? Not a healthy baseline.",
                    threshold,
                )
            else:
                gaps.append(
                    dict(
                        service=service,
                        tool="compare_baseline",
                        signal=signal,
                        reason="NO_COMPATIBLE_PRIOR_REFERENCE",
                    )
                )
        neighbors = sorted(
            {
                n
                for edge in meta["observed_topology"]
                if service in edge
                for n in edge
                if n != service
            }
        )[:3]
        if neighbors and any(
            r.get("trace_id") and r.get("parent_span_id") for r in identities
        ):
            add(
                AnalysisRequest(
                    tool="compare_dependencies", target=service, neighbors=neighbors
                ),
                "shared_errors",
                "Is at least one returned shared trace observed with errors on both target and a listed neighbor? NO only when returned trace coverage/statuses suffice.",
            )
        else:
            gaps.append(
                dict(
                    service=service,
                    tool="compare_dependencies",
                    reason="NO_OBSERVED_RELATION_OR_PARENT_IDENTITY",
                )
            )
    return catalog, gaps


def answer_question(question, result):
    """Predicates only on the actual tool result, never ground-truth or prose."""
    if question["kind"] == "continuous_observation":
        return dict(
            outcome="UNKNOWN",
            category="DESCRIPTIVE_OBSERVATION",
            reason="OPEN_ANALYSIS_NO_BINARY_PREDICATE",
        )
    rows = result["table"]
    if not rows:
        return dict(
            outcome="UNKNOWN",
            category="OBSERVATION_INSUFFICIENT",
            reason="EMPTY_CURRENT_SCOPE",
        )
    if question["kind"] == "operation_errors":
        values = [r.get("error_fraction") for r in rows]
        if any(finite(x) and x >= question["threshold"] for x in values):
            outcome = "YES"
        elif not all(finite(x) for x in values):
            outcome = "UNKNOWN"
        else:
            outcome = "NO"
        reason = "KNOWN_STATUS_FRACTIONS_ONLY"
    elif question["kind"] == "historical_increase":
        value = rows[0].get("absolute_difference")
        outcome = (
            "YES"
            if finite(value) and value >= question["threshold"]
            else "NO"
            if finite(value)
            else "UNKNOWN"
        )
        reason = rows[0].get("status", "MISSING_DIFFERENCE")
    else:
        row = rows[0]
        target = question["request"]["target"]
        shared = row.get("shared_trace_errors", [])
        yes = any(
            t["services"].get(target, {}).get("errors", 0) > 0
            and any(s != target and v["errors"] > 0 for s, v in t["services"].items())
            for t in shared
        )
        unknown = (
            not shared
            or row.get("shared_trace_truncated", True)
            or any(
                v.get("unknown_status", 0)
                for v in row.get("service_samples", {}).values()
            )
        )
        outcome = "YES" if yes else "UNKNOWN" if unknown else "NO"
        reason = "SAMPLED_SHARED_TRACES_NOT_CAUSAL"
    return dict(
        outcome=outcome,
        category="OBSERVATION_INSUFFICIENT" if outcome == "UNKNOWN" else "OBSERVED",
        reason=reason,
    )


def already_exposed(question, results):
    req = question["request"]
    for result in results:
        old = result["request"]
        # Aggregate error fraction >= threshold already entails that some
        # operation group reaches that threshold. This is exposed evidence,
        # even though the exact operation has not yet been profiled.
        if question["kind"] == "operation_errors":
            if (
                old["tool"] == "compare_baseline"
                and old["signal"] == "error_fraction"
                and old["target"] == req["target"]
            ):
                if any(
                    finite(r.get("current")) and r["current"] >= question["threshold"]
                    for r in result["table"]
                ):
                    return True
            if old["tool"] == "compare_dependencies":
                for row in result["table"]:
                    samples = row.get("service_samples", {}).get(req["target"], {})
                    known = samples.get("count", 0) - samples.get("unknown_status", 0)
                    errors = samples.get("error_count", 0)
                    if known > 0 and (
                        errors / known >= question["threshold"]
                        or (errors == 0 and samples.get("unknown_status", 0) == 0)
                    ):
                        return True
        if canonical_request(AnalysisRequest(**old)) == canonical_request(
            AnalysisRequest(**req)
        ):
            return True
        if old["tool"] == "compare_dependencies":
            involved = {old["target"], *old["neighbors"]}
            if (
                question["kind"] == "historical_increase"
                and req["target"] in involved
                and old["signal"] == req["signal"]
            ):
                if any(
                    finite(
                        r.get("relative_changes", {})
                        .get(req["target"], {})
                        .get("absolute_difference")
                    )
                    for r in result["table"]
                ):
                    return True
            if (
                question["kind"] == "shared_errors"
                and {req["target"], *req["neighbors"]} <= involved
            ):
                return True
    return False


STATISTIC_COLUMNS = [
    "n_total",
    "n_valid",
    "n_unset",
    "n_ok",
    "n_error",
    "n_missing_or_invalid",
    "unique_trace_count",
    "status_coverage",
    "missing_status_fraction",
    "error_marker_fraction",
    "explicit_status_error_fraction",
    "duration_sample_count",
    "median_duration_ms",
    "p95_duration_ms",
]


def compact_statistics(value):
    if isinstance(value, list):
        return [compact_statistics(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = (
        {
            k: compact_statistics(v)
            for k, v in value.items()
            if k not in STATISTIC_COLUMNS
        }
        if "n_total" in value
        else {k: compact_statistics(v) for k, v in value.items()}
    )
    if "n_total" in value:
        result["statistics"] = [value.get(k) for k in STATISTIC_COLUMNS]
    return result


def compact_result(result, alias, representation=False):
    # Keep full numerical tables/limitations; omit only redundant source handles,
    # which remain in the private saved result and alias mapping.
    return dict(
        reference=alias,
        analysis_id=result["analysis_id"],
        request=result["request"],
        table=compact_statistics(result["table"])
        if representation
        else result["table"],
        coverage=result["coverage"],
        truncated=result["truncated"],
        limitations=result["limitations"],
        source_ref_count=len(result["source_refs"]),
    )


def validate_common_decision(
    decision, services, catalog, registry, aliases, force_report
):
    """Validate identities, references and action shape; never enforce a strategy."""
    if decision.action == "report" and decision.hypothesis_updates:
        raise ValueError("REPORT_MUST_NOT_UPDATE_HYPOTHESES")
    updated = deepcopy(registry)
    ids = [h.hypothesis_id for h in decision.hypothesis_updates]
    if len(set(ids)) != len(ids):
        raise ValueError("DUPLICATE_HYPOTHESIS_ID")
    for h in decision.hypothesis_updates:
        if h.target not in services or any(
            ref not in aliases for ref in h.support + h.conflicts
        ):
            raise ValueError("HYPOTHESIS_SCOPE_OR_REFERENCE")
        previous = updated.get(h.hypothesis_id)
        changed = previous is None or any(
            previous[k] != v for k, v in h.model_dump().items()
        )
        updated[h.hypothesis_id] = dict(
            h.model_dump(),
            version=(previous or {}).get("version", 0) + int(changed),
        )
    if decision.action == "report":
        if (
            decision.question_id is not None
            or decision.expectations
            or decision.report is None
        ):
            raise ValueError("REPORT_FIELD_COMBINATION")
        report = decision.report
        if any(s not in services for s in report.ranked_components) or len(
            set(report.ranked_components)
        ) != len(report.ranked_components):
            raise ValueError("REPORT_COMPONENT_SCOPE")
        if any(ref not in aliases for ref in report.references):
            raise ValueError("REPORT_REFERENCE")
        if bool(report.ranked_components) == (report.conclusion == "ABSTAIN"):
            raise ValueError("ABSTENTION_RANKING_MISMATCH")
        if report.ranked_components and not report.references:
            raise ValueError("REPORT_REQUIRES_ACTUAL_REFERENCE")
        return updated
    if force_report or decision.report is not None:
        raise ValueError("REPORT_REQUIRED_OR_ACTION_FIELD_COMBINATION")
    if decision.question_id not in catalog:
        raise ValueError("QUESTION_UNAVAILABLE")
    if any(e.hypothesis_id not in updated for e in decision.expectations):
        raise ValueError("EXPECTATION_UNKNOWN_HYPOTHESIS")
    return updated


def select_strategy(method, decision, question, results, estimate):
    """Score the single proposed question before observation, without answer peeking.

    This is contrast admission, not a search/ranking over unproposed questions.
    A non-discriminating or exposed proposal remains a legal ReAct action.
    """
    expected = decision.expectations
    exposed = already_exposed(question, results)
    pair = (
        len(expected) == 2
        and len({e.hypothesis_id for e in expected}) == 2
        and {e.outcome for e in expected} == {"YES", "NO"}
    )
    contrast = method == "D" and pair and not exposed
    return dict(
        branch="CONTRAST"
        if contrast
        else "REACT"
        if method == "C"
        else "REACT_FALLBACK",
        reason="UNEXPOSED_OPPOSITE_PAIR"
        if contrast
        else "ORDINARY_REACT"
        if method == "C"
        else "OBSERVATION_ALREADY_EXPOSED"
        if exposed
        else "NO_VALID_OPPOSITE_PAIR",
        disagreement=int(pair),
        selection_score=(1 / (1 + estimate["cost"])) if contrast else 0.0,
        score_scope="SINGLE_PROPOSED_QUESTION_ADMISSION",
        estimated_cost=estimate["cost"],
        prediction_admitted=bool(expected)
        and not exposed
        and (method == "C" or contrast),
    )


def investigate_semantic_lite(analysis, provider, method, run_id, config):
    if method not in ("C", "D"):
        raise ValueError("LITE_METHOD_NOT_ALLOWED")
    if analysis.representation and method != "C":
        raise ValueError("REPRESENTATION_REPAIR_C_ONLY")
    started = monotonic()
    catalog, gaps = question_catalog(analysis)
    state = dict(
        run_id=run_id,
        method=method,
        task=TASK,
        status="RUNNING",
        report=None,
        hypotheses=[],
        trajectory=[],
        analysis_results=[],
        observations=[],
        provider_calls=0,
        analysis_actions=0,
        queries=0,
        records_scanned=0,
        returned_bytes=0,
        compute_ms=0.0,
        preprocessing_records=analysis.snapshot["metadata"]["record_count"],
        reference_preprocessing_record_visits=(
            len(analysis.rows)
            * 3
            * len(
                {
                    tuple(v["scope"])
                    for v in analysis.snapshot.get("references", {}).values()
                    if analysis.config.get("reference_view") == "operation"
                    or v["scope"][1:] == [None, None]
                }
            )
        )
        if analysis.representation
        else 0,
        cost_scope="Logical record visits and query equivalents, not measured physical I/O; offline source restoration reported separately.",
        catalog=catalog,
        capability_gaps=gaps,
        independent_residuals=analysis.snapshot.get("residuals", []),
    )
    registry, aliases, checks, seen_actions = {}, {}, [], set()
    for i, residual in enumerate(state["independent_residuals"], 1):
        aliases[f"O{i}"] = residual.get("observation_id", f"residual-{i}")
    repairs = stale = 0
    error = None
    for turn in range(config["max_model_calls"]):
        remaining = config["max_actions"] - state["analysis_actions"]
        force_report = (
            remaining <= 0 or turn == config["max_model_calls"] - 1 or stale >= 2
        )
        visible_catalog = [
            dict(
                q,
                prediction_already_exposed=already_exposed(
                    q, state["analysis_results"]
                ),
            )
            for q in catalog.values()
            if not analysis.representation
            or digest(canonical_request(AnalysisRequest(**q["request"])))
            not in seen_actions
        ]
        if analysis.representation and not visible_catalog:
            force_report = True
        view = dict(
            strategy="Ordinary ReAct: freely choose the useful question or report. Hypotheses and expectations are optional; single-hypothesis and same-direction predictions are legal; you may reason about alternatives normally."
            if method == "C"
            else "D-lite: prefer comparing at most two current competing explanations using ONE shared question with opposite YES/NO expectations before observation. When no valid contrast exists, use the same ordinary ReAct exploration or report as C. Missing opposition or exposed predictions trigger fallback, not protocol failure; do not invent opposition. Use returned checks to update explanations/next action, not claim whole-cause falsification.",
            services=sorted(analysis.services),
            questions=[
                dict(
                    question_id=q["question_id"],
                    tool=q["request"]["tool"],
                    scope=[
                        q["request"][k] for k in ("target", "operation", "direction")
                    ],
                    **{
                        k: q["request"][k]
                        for k in ("signal", "reference_id", "neighbors")
                        if q["request"][k]
                        and (
                            k != "signal"
                            or q["request"]["tool"] != "profile_operations"
                        )
                    },
                )
                for q in visible_catalog
            ]
            if analysis.representation
            else visible_catalog,
            statistic_columns=STATISTIC_COLUMNS if analysis.representation else [],
            statistics_semantics="Statistics arrays follow statistic_columns. p95 is nearest rank, historical center is equal-weight median of window statistics. Counts are sampled spans; no health or causal proof."
            if analysis.representation
            else None,
            capability_gaps=gaps,
            hypotheses=list(registry.values()),
            evidence=[
                compact_result(r, f"E{i}", analysis.representation)
                for i, r in enumerate(state["analysis_results"], 1)
            ],
            prediction_checks=checks,
            residuals=[
                dict(reference=f"O{i}", observation=r)
                for i, r in enumerate(state["independent_residuals"], 1)
            ],
            reference_handles=aliases,
            required_limits=LIMITS,
            remaining_actions=remaining,
            remaining_calls=config["max_model_calls"] - turn,
            report_required=force_report,
            representation_version=analysis.config.get("representation_version"),
            operation_inventory=analysis.metadata()["operation_inventory"]
            if analysis.representation
            else [],
            reference_availability=reference_inventory(analysis)
            if analysis.representation
            else {},
            windows=analysis.snapshot["windows"] if analysis.representation else {},
            scope_columns=["service", "operation", "direction"]
            if analysis.representation
            else [],
            tool_semantics="profile_operations: current status/duration by all operation/direction groups. compare_baseline: continuous matched historical changes, not a binary test. compare_dependencies: observed edges and each service's own changes; operation view expands first operation by identity and retains other counts/handles. Select Q ID; do not construct parameters. Profile costs 1 query, baseline 2; dependencies charge all component accesses."
            if analysis.representation
            else None,
            last_validation_error=error,
        )
        if analysis.representation and force_report:
            view["questions"] = []
        state["provider_calls"] += 1
        try:
            decision = provider.complete(
                key=f"{run_id}:{turn}",
                task=TASK,
                view=view,
                schema=(ReportDecision if force_report else TurnDecision)
                if analysis.representation
                else LiteDecision,
                reasoning=config["reasoning"],
            )
            if isinstance(decision, TurnDecision):
                decision = decision.decision
            if isinstance(decision, AnalyzeDecision):
                decision = LiteDecision(**decision.model_dump(), report=None)
            if isinstance(decision, ReportDecision):
                decision = LiteDecision(
                    **decision.model_dump(),
                    question_id=None,
                    hypothesis_updates=[],
                    expectations=[],
                )
            entry = dict(turn=turn, decision=decision.model_dump())
            state["trajectory"].append(entry)
            updated = validate_common_decision(
                decision,
                analysis.services,
                {q["question_id"]: q for q in visible_catalog}
                if analysis.representation
                else catalog,
                registry,
                aliases,
                force_report,
            )
            if decision.action == "report":
                report = decision.report
                state.update(
                    report=dict(
                        report.model_dump(),
                        resolved_references=[aliases[x] for x in report.references],
                        independent_residuals=state["independent_residuals"],
                        required_limits=LIMITS,
                    ),
                    status="COMPLETED",
                )
                registry = updated
                break
            question = catalog[decision.question_id]
            expected = decision.expectations
            req = AnalysisRequest(**question["request"])
            estimate = analysis.estimate(req)
            selection = select_strategy(
                method, decision, question, state["analysis_results"], estimate
            )
            entry["selection"] = dict(selection, executed=False)
            # Valid increments remain useful even when a read is deduplicated.
            error = None
            action = digest(canonical_request(req))
            if action in seen_actions:
                registry = updated
                state["hypotheses"] = list(registry.values())
                stale += 1
                entry["check"] = dict(
                    outcome="UNKNOWN",
                    category="NO_PROGRESS",
                    reason="DUPLICATE_ANALYSIS_NOT_EXECUTED",
                )
                continue
            if (
                state["queries"] + estimate["queries"] > config["max_queries"]
                or state["records_scanned"] + estimate["records_scanned"]
                > config["max_records_scanned"]
            ):
                stale = 2
                entry["check"] = dict(
                    outcome="UNKNOWN",
                    category="UNAVAILABLE_QUESTION",
                    reason="ACCESS_BUDGET",
                )
                continue
            result = analysis.execute(req)
            entry["selection"]["executed"] = True
            check = answer_question(question, result)
            alias = "E" + str(len(state["analysis_results"]) + 1)
            aliases[alias] = result["analysis_id"]
            check.update(
                reference=alias,
                question_id=question["question_id"],
                precommitted=selection["prediction_admitted"],
                predictions=[
                    dict(
                        hypothesis_id=e.hypothesis_id,
                        version=updated[e.hypothesis_id]["version"],
                        expected=e.outcome,
                        outcome=check["outcome"],
                        status="UNKNOWN"
                        if check["outcome"] == "UNKNOWN"
                        else "PREDICTION_SUPPORTED"
                        if e.outcome == check["outcome"]
                        else "PREDICTION_CONTRADICTED",
                    )
                    for e in expected
                    if selection["prediction_admitted"]
                ],
            )
            checks.append(check)
            entry.update(
                check=check,
                hypothesis_versions={
                    e.hypothesis_id: updated[e.hypothesis_id] for e in expected
                },
            )
            state["analysis_results"].append(result)
            state["analysis_actions"] += 1
            for k in ("queries", "records_scanned", "returned_bytes", "compute_ms"):
                state[k] += result["access"][k]
            seen_actions.add(action)
            registry = updated
            stale = 0  # A new available question answered or its missing observation established.
            error = None
        except (ValueError, ProductError) as exc:
            code = exc.code if isinstance(exc, ProductError) else str(exc)
            state["trajectory"].append(dict(turn=turn, error=code))
            if isinstance(exc, ProductError) and code != "PROVIDER_PROTOCOL_INVALID":
                state["status"] = code
                break
            repairs += 1
            error = dict(
                code=code,
                schema_fields=getattr(provider, "validation_errors", []),
                increments_accepted=False,
                current_hypothesis_ids=sorted(registry),
                instruction="The rejected turn registered no new hypotheses. Use these actual IDs or resubmit valid increments.",
            )
            if repairs > config["max_repairs"]:
                state["status"] = "FORMAT_FAILED"
                break
    state["hypotheses"] = list(registry.values())
    state["reference_handles"] = aliases
    state["prediction_checks"] = checks
    if state["status"] == "RUNNING":
        state["status"] = "MODEL_BUDGET"
    state["elapsed_seconds"] = monotonic() - started
    state["action_authority"] = "NONE"
    return state
