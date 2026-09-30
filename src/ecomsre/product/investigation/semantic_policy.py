"""Budgeted contrastive view selection, with pre-observation questions."""

import operator
from time import monotonic
from .semantic_analysis import digest, finite, canonical_request
from .semantic_contracts import AnalysisRequest, Candidate, SemanticDecision
from ecomsre.product.errors import ProductError

OPS = {
    "gt": operator.gt,
    "ge": operator.ge,
    "lt": operator.lt,
    "le": operator.le,
    "eq": operator.eq,
}


def question_identity(candidate):
    q = candidate.question
    if q is None:
        return digest(canonical_request(candidate.request))
    return digest(
        [
            canonical_request(candidate.request),
            q.field,
            q.row_key,
            q.comparator,
            q.threshold,
        ]
    )


def contrast(candidate, hypotheses, analysis):
    e = analysis.estimate(candidate.request)
    q = candidate.question
    ids = {h.hypothesis_id for h in hypotheses}
    expected = [q.expectations.get(h, "UNSPECIFIED") for h in sorted(ids)] if q else []
    pairs = len(expected) * (len(expected) - 1) // 2
    opposite = sum(
        {a, b} == {"YES", "NO"}
        for i, a in enumerate(expected)
        for b in expected[i + 1 :]
    )
    disagreement = (
        opposite / pairs
        if pairs
        else (
            1.0
            if len(ids) == 1 and q and any(v != "UNSPECIFIED" for v in expected)
            else 0.0
        )
    )
    return dict(
        **e,
        disagreement=disagreement,
        score=e["quality"] * disagreement / (1 + e["cost"]),
    )


def observe(question, result):
    if question is None:
        return "UNKNOWN"
    rows = [r for r in result["table"] if r.get("row_key") == question.row_key]
    if len(rows) != 1 or not finite(rows[0].get(question.field)):
        return "UNKNOWN"
    return (
        "YES"
        if OPS[question.comparator](rows[0][question.field], question.threshold)
        else "NO"
    )


class Progress:
    def __init__(self):
        self.analyses = set()
        self.questions = {}
        self.bindings = {}
        self.answers = set()
        self.exposed = set()
        self.gaps = set()

    def update(self, candidate, result):
        action = digest(canonical_request(candidate.request))
        identity = question_identity(candidate)
        answer = observe(candidate.question, result)
        prefix = (
            candidate.request.target,
            candidate.request.source,
            candidate.request.window,
        )
        qkey = (
            (*prefix, candidate.question.row_key, candidate.question.field)
            if candidate.question
            else None
        )
        precommitted = (
            qkey is not None and qkey not in self.exposed and qkey not in self.gaps
        )
        if candidate.question:
            for h, v in candidate.question.expectations.items():
                binding = (identity, h)
                if binding in self.bindings and self.bindings[binding] != v:
                    raise ValueError("PREDICTION_REWRITTEN")
                self.bindings[binding] = v
        factual = [
            {k: v for k, v in row.items() if k not in ("representative_refs",)}
            for row in result["table"]
        ]
        fact_key = digest([candidate.request.target, candidate.request.source, factual])
        fields = {
            (*prefix, row.get("row_key", row.get("record_ref", "raw")), k)
            for row in factual
            for k, v in row.items()
            if finite(v)
        }
        new_fields = fields - self.exposed
        gap_key = (
            *prefix,
            candidate.request.tool,
            candidate.request.operation,
            candidate.request.direction,
        )
        new_gap = not fields and gap_key not in self.gaps
        new = (
            action not in self.analyses
            and fact_key not in self.answers
            and bool(new_fields or new_gap or (precommitted and answer != "UNKNOWN"))
        )
        self.answers.add(fact_key)
        self.exposed.update(fields)
        if new_gap:
            self.gaps.add(gap_key)
        if qkey is not None and answer == "UNKNOWN":
            self.gaps.add(qkey)
        # A changed question after the same answer was already exposed cannot
        # manufacture semantic progress or a new pre-observation prediction.
        self.analyses.add(action)
        if new:
            self.questions[identity] = answer
        return new, dict(
            question_id=identity,
            outcome=answer,
            precommitted=bool(new and precommitted),
            prediction_status={
                h: (
                    "UNSPECIFIED"
                    if v == "UNSPECIFIED"
                    else "UNKNOWN"
                    if answer == "UNKNOWN"
                    else "SUPPORT"
                    if v == answer
                    else "CONFLICT"
                )
                for h, v in (
                    candidate.question.expectations.items()
                    if candidate.question and new and precommitted
                    else []
                )
            },
        )


def fixed_requests(analysis):
    # Sorted, label-free service rotation; two targets fit the common 6-action cap.
    out = []
    preferred = sorted(
        {
            r["service"]
            for residual in analysis.snapshot.get("residuals", [])
            for r in residual.get("records", [])
            if r.get("service") in analysis.services
        }
    )
    for target in (preferred or sorted(analysis.services))[:2]:
        refs = sorted(
            k
            for k, v in analysis.snapshot.get("references", {}).items()
            if v["scope"] == [target, None, None] and v["unit"] == "fraction"
        )
        neighbors = sorted(
            {
                n
                for edge in analysis.snapshot.get("topology", [])
                if target in edge
                for n in edge
                if n != target
            }
        )[:3]
        out += [
            AnalysisRequest(tool="profile_operations", target=target),
            AnalysisRequest(
                tool="compare_baseline",
                target=target,
                reference_id=refs[0] if refs else None,
            ),
            AnalysisRequest(
                tool="compare_dependencies", target=target, neighbors=neighbors
            ),
        ]
    return out


def investigate_semantic_v1(analysis, provider, method, run_id, config):
    if method not in "ABCD" or len(method) != 1:
        raise ValueError("UNKNOWN_METHOD")
    started = monotonic()
    state = dict(
        run_id=run_id,
        method=method,
        task="investigate_semantic_v1",
        status="RUNNING",
        observations=[],
        analysis_results=[],
        trajectory=[],
        hypotheses=[],
        report=None,
        provider_calls=0,
        analysis_actions=0,
        queries=0,
        records_scanned=0,
        returned_bytes=0,
        compute_ms=0.0,
    )
    state["preprocessing_records"] = analysis.snapshot["metadata"][
        "record_count"
    ] + analysis.snapshot["metadata"].get("counter_points", 0)
    progress = Progress()
    stale = repairs = 0
    fixed = fixed_requests(analysis)
    error = None
    registry = {}
    if method == "B":
        # Fixed tools need no intermediate model decision; charge actual work,
        # then ask the same model once to interpret the accumulated evidence.
        for index, request in enumerate(fixed[: config["max_actions"]]):
            estimated = analysis.estimate(request)
            if (
                state["queries"] + estimated["queries"] > config["max_queries"]
                or state["records_scanned"] + estimated["records_scanned"]
                > config["max_records_scanned"]
            ):
                break
            result = analysis.execute(request)
            _, check = progress.update(Candidate(request=request), result)
            state["analysis_results"].append(result)
            state["analysis_actions"] += 1
            for k in ("queries", "records_scanned", "returned_bytes", "compute_ms"):
                state[k] += result["access"][k]
            state["trajectory"].append(
                dict(turn=-index - 1, fixed_request=request.model_dump(), check=check)
            )
    for turn in range(config["max_model_calls"]):

        def projected(results):
            out = []
            for result in results:
                item = dict(result)
                item["source_ref_count"] = len(result["source_refs"])
                item["source_refs"] = result["source_refs"][:8]
                item["source_refs_truncated"] = len(result["source_refs"]) > 8
                out.append(item)
            return out

        view = dict(
            metadata=analysis.metadata(),
            method=method,
            strategy={
                "A": "Ordinary ReAct. Use read_records with service/operation/direction filters and pagination. Reason from raw fields and mapping.",
                "B": "Fixed workflow. Runtime chooses three tools in order for two sorted services. Interpret all results; final answer after fixed actions.",
                "C": "Ordinary ReAct. Freely choose the most useful semantic tool; no disagreement matrix required. Hypotheses are optional.",
                "D": "Propose up to four competing stable hypotheses and candidate analyses. For each candidate precommit one observable question with YES/NO/UNSPECIFIED per hypothesis. Runtime chooses Q*D/(1+C); do not manufacture opposition.",
            }[method],
            allowed_tools=["read_records"]
            if method == "A"
            else ["profile_operations", "compare_baseline", "compare_dependencies"],
            hypotheses=state["hypotheses"],
            observations=projected(state["observations"]),
            analysis_results=projected(state["analysis_results"]),
            prior_checks=[t["check"] for t in state["trajectory"] if "check" in t],
            remaining_actions=config["max_actions"] - state["analysis_actions"],
            remaining_calls=config["max_model_calls"] - turn,
            last_validation_error=error,
            fixed_next=fixed[state["analysis_actions"]].model_dump()
            if method == "B" and state["analysis_actions"] < len(fixed)
            else None,
        )
        # No schema/token overflow truncation that silently drops evidence.
        state["provider_calls"] += 1
        try:
            decision = provider.complete(
                key=f"{run_id}:{turn}",
                task="investigate_semantic_v1",
                view=view,
                schema=SemanticDecision,
                reasoning=config["reasoning"],
            )
            ids = [h.hypothesis_id for h in decision.hypotheses]
            if len(set(ids)) != len(ids):
                raise ValueError("DUPLICATE_HYPOTHESIS_ID")
            seen = {r["analysis_id"] for r in state["analysis_results"]} | {
                r["analysis_id"] for r in state["observations"]
            }
            seen |= {
                r["observation_id"]
                for r in analysis.snapshot.get("residuals", [])
                if "observation_id" in r
            }
            for h in decision.hypotheses:
                if h.target not in analysis.services or any(
                    r not in seen for r in h.support + h.conflicts
                ):
                    raise ValueError("HYPOTHESIS_SCOPE_OR_REFERENCE")
                old = registry.get(h.hypothesis_id)
                if old and old["target"] != h.target:
                    raise ValueError("HYPOTHESIS_ID_TARGET_CHANGED")
                registry[h.hypothesis_id] = h.model_dump()
            state["hypotheses"] = [h.model_dump() for h in decision.hypotheses]
            entry = dict(turn=turn, decision=decision.model_dump())
            state["trajectory"].append(entry)
            if decision.report is not None and (
                method != "B" or state["analysis_actions"] >= len(fixed)
            ):
                if any(
                    c not in analysis.services
                    for c in decision.report.ranked_components
                ) or len(set(decision.report.ranked_components)) != len(
                    decision.report.ranked_components
                ):
                    raise ValueError("REPORT_COMPONENT_SCOPE")
                state.update(report=decision.report.model_dump(), status="COMPLETED")
                break
            if state["analysis_actions"] >= config["max_actions"]:
                state["status"] = "ACTION_BUDGET"
                break
            if method == "B":
                candidates = [Candidate(request=fixed[state["analysis_actions"]])]
            else:
                candidates = decision.candidates
            if not candidates:
                stale += 1
                if stale >= 2:
                    state["status"] = "NO_SEMANTIC_PROGRESS"
                    break
                continue
            scores = []
            for c in candidates:
                if c.request.tool not in view["allowed_tools"]:
                    raise ValueError("TOOL_NOT_ALLOWED")
                if c.question and (set(c.question.expectations) - set(ids)):
                    raise ValueError("QUESTION_UNKNOWN_HYPOTHESIS")
                scores.append(contrast(c, decision.hypotheses, analysis))
            if method == "D":
                # Stable tie order, with finite exploratory fallback for no disagreement.
                selected = max(
                    range(len(candidates)), key=lambda i: (scores[i]["score"], -i)
                )
            else:
                selected = 0
            candidate = candidates[selected]
            estimate = scores[selected]
            entry.update(candidate_scores=scores, selected=selected)
            if (
                state["queries"] + estimate["queries"] > config["max_queries"]
                or state["records_scanned"] + estimate["records_scanned"]
                > config["max_records_scanned"]
            ):
                state["status"] = "ACCESS_BUDGET"
                break
            action = digest(canonical_request(candidate.request))
            if action in progress.analyses:
                stale += 1
                entry["check"] = dict(
                    outcome="UNKNOWN", reason="DUPLICATE_ANALYSIS_NO_PROGRESS"
                )
                if stale >= 2:
                    state["status"] = "NO_SEMANTIC_PROGRESS"
                    break
                continue
            result = analysis.execute(candidate.request)
            changed, check = progress.update(candidate, result)
            entry["check"] = check
            state["observations" if method == "A" else "analysis_results"].append(
                result
            )
            state["analysis_actions"] += 1
            for k in ("queries", "records_scanned", "returned_bytes", "compute_ms"):
                state[k] += result["access"][k]
            stale = 0 if changed else stale + 1
            error = None
        except (ValueError, ProductError) as exc:
            code = exc.code if isinstance(exc, ProductError) else str(exc)
            state["trajectory"].append(dict(turn=turn, error=code))
            if isinstance(exc, ProductError) and code != "PROVIDER_PROTOCOL_INVALID":
                state["status"] = code
                break
            repairs += 1
            error = {
                "code": code,
                "schema_fields": getattr(provider, "validation_errors", []),
            }
            if repairs > config["max_repairs"]:
                state["status"] = "FORMAT_FAILED"
                break
    if state["status"] == "RUNNING":
        state["status"] = "MODEL_BUDGET"
    state["elapsed_seconds"] = monotonic() - started
    state["action_authority"] = "NONE"
    return state
