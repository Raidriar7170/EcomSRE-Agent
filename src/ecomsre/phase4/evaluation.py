"""Isolated deterministic evaluation for the ten Phase 4 Domain runs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import ModuleType
from typing import cast

from ecomsre.phase1.contracts import Evidence, RCADecision
from ecomsre.phase2.comparison_adapter import BudgetCaps
from ecomsre.phase2.contracts import SPECIALIST_TOOL_BINDINGS
from ecomsre.phase4.contracts import (
    DomainGroundTruth,
    DomainVariant,
    DomainWorkflowTrace,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVALUATION_CASE_IDS = (
    "search-feature-freshness-lag-complete",
    "recommendation-model-feature-schema-mismatch",
    "search-ranking-configuration-frontend-decoy",
    "recommendation-feature-evidence-insufficient",
    "ranking-change-with-normal-search-sli",
)
def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _load_worker_runner() -> ModuleType:
    module_name = "_ecomsre_phase4_evaluator_worker_runner"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = PROJECT_ROOT / "eval/phase4/runner.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 4 evaluator worker runner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_WORKER_RUNNER = _load_worker_runner()


def _worker_request(project_root: Path, request: dict[str, object]) -> object:
    return cast(object, _WORKER_RUNNER.worker_request(project_root, request))


def run_worker_probe(project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    payload = _worker_request(
        root,
        {"mode": "probe", "project_root": str(root)},
    )
    if not isinstance(payload, dict):
        raise ValueError("worker probe response must be an object")
    return cast(dict[str, object], payload)


def _run_workflow_trace(
    project_root: Path,
    case_id: str,
    variant: DomainVariant,
) -> DomainWorkflowTrace:
    root = Path(project_root).resolve(strict=True)
    payload = _worker_request(
        root,
        {
            "mode": "run",
            "project_root": str(root),
            "case_id": case_id,
            "variant": variant.value,
        },
    )
    return DomainWorkflowTrace.model_validate_json(_canonical_json(payload))


def _load_ground_truth(root: Path, case_id: str) -> DomainGroundTruth:
    allowed = root.resolve(strict=True)
    candidate = allowed / f"{case_id}.json"
    details = candidate.lstat()
    if stat.S_ISLNK(details.st_mode):
        raise ValueError("ground truth path must not be a symlink")
    path = candidate.resolve(strict=True)
    if path.parent != allowed:
        raise ValueError("ground truth path escapes its evaluator root")
    if not stat.S_ISREG(details.st_mode) or details.st_size > 64 * 1024:
        raise ValueError("ground truth must be one bounded regular file")
    truth = DomainGroundTruth.model_validate_json(path.read_bytes())
    if truth.case_id != case_id:
        raise ValueError("ground truth case identity mismatch")
    return truth


def _rate(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else 0.0,
    }


def _trace_evidence(trace: DomainWorkflowTrace) -> tuple[Evidence, ...]:
    by_ref: dict[str, Evidence] = {}
    for record in trace.tool_call_records:
        for evidence in record.evidence:
            by_ref[evidence.evidence_ref] = evidence
    return tuple(by_ref[key] for key in sorted(by_ref))


def _budget_compliant(trace: DomainWorkflowTrace) -> bool:
    snapshot = trace.final_budget_snapshot
    charged_audits = tuple(
        record for record in trace.model_call_audits if record.status == "CHARGED"
    )
    return (
        trace.status == "COMPLETED"
        and snapshot.max_model_calls == 8
        and snapshot.max_tool_calls == 8
        and snapshot.max_total_tokens == BudgetCaps().total_tokens
        and snapshot.charged_model_calls
        == len(charged_audits) + len(trace.domain_model_call_audits)
        and snapshot.charged_tool_calls == len(trace.tool_call_audits)
        and snapshot.cumulative_tokens
        == sum(cast(int, record.total_tokens) for record in charged_audits)
        + sum(record.total_tokens for record in trace.domain_model_call_audits)
        and not snapshot.active_capacity_slot_ids
        and not snapshot.active_specialist_authorization_ids
        and not snapshot.active_lease_ids
    )


def _tool_isolation(trace: DomainWorkflowTrace) -> tuple[int, int]:
    expected = {
        source: tool_name
        for _, source, tool_name in SPECIALIST_TOOL_BINDINGS.values()
    }
    return (
        sum(expected[record.source] is record.tool_name for record in trace.tool_call_audits),
        len(trace.tool_call_audits),
    )


def _evaluate_run(
    trace: DomainWorkflowTrace,
    truth: DomainGroundTruth,
) -> dict[str, object]:
    final = trace.final_rca
    decision = final.decision if final is not None else None
    confirmed_truth = truth.expected_decision is RCADecision.RCA_CONFIRMED
    evidence = _trace_evidence(trace)
    evidence_by_ref = {item.evidence_ref: item for item in evidence}
    cited = (
        {*final.supporting_evidence, *final.contradicting_evidence}
        if final is not None
        else set()
    )
    decoy_resistant: bool | None = None
    if truth.decoy_evidence:
        decoy_keys = set(truth.decoy_evidence)
        decoy_refs = {
            item.evidence_ref
            for item in evidence
            if f"{item.source.value}:{item.service}:{item.observation_type}"
            in decoy_keys
        }
        decoy_resistant = (
            final is not None
            and bool(decoy_refs)
            and decoy_refs.isdisjoint(final.supporting_evidence)
        )
    isolated, isolation_total = _tool_isolation(trace)
    return {
        "case_id": trace.case_id,
        "variant": trace.variant.value,
        "run_id": trace.run_id,
        "status": trace.status,
        "terminal_reason": trace.terminal_reason,
        "decision": decision.value if decision is not None else None,
        "root_service": final.root_service if final is not None else None,
        "fault_mechanism": (
            final.fault_mechanism.value
            if final is not None and final.fault_mechanism is not None
            else None
        ),
        "decision_correct": decision is truth.expected_decision,
        "root_service_correct": (
            final is not None and final.root_service == truth.expected_root_service
            if confirmed_truth
            else None
        ),
        "domain_mechanism_correct": (
            final is not None
            and final.fault_mechanism is truth.expected_fault_mechanism
            if confirmed_truth
            else None
        ),
        "need_more_evidence_correct": (
            decision is RCADecision.NEED_MORE_EVIDENCE
            if truth.expected_decision is RCADecision.NEED_MORE_EVIDENCE
            else None
        ),
        "abstention_correct": (
            decision is RCADecision.ABSTAIN
            if truth.expected_decision is RCADecision.ABSTAIN
            else None
        ),
        "decoy_resistant": decoy_resistant,
        "schema_valid": trace.status == "COMPLETED" and final is not None,
        "evidence_references_valid": final is not None and cited <= evidence_by_ref.keys(),
        "dag_valid": trace.admitted_graph is not None,
        "specialist_tool_isolation": _rate(isolated, isolation_total),
        "budget_compliant": _budget_compliant(trace),
        "tool_calls": trace.final_budget_snapshot.charged_tool_calls,
        "token_usage": trace.final_budget_snapshot.cumulative_tokens,
        "trace": trace.model_dump(mode="json"),
    }


def _aggregate(run_results: list[dict[str, object]]) -> dict[str, object]:
    count = len(run_results)
    roots = [
        item["root_service_correct"]
        for item in run_results
        if item["root_service_correct"] is not None
    ]
    mechanisms = [
        item["domain_mechanism_correct"]
        for item in run_results
        if item["domain_mechanism_correct"] is not None
    ]
    need_more = [
        item["need_more_evidence_correct"]
        for item in run_results
        if item["need_more_evidence_correct"] is not None
    ]
    abstentions = [
        item["abstention_correct"]
        for item in run_results
        if item["abstention_correct"] is not None
    ]
    decoys = [
        item["decoy_resistant"]
        for item in run_results
        if item["decoy_resistant"] is not None
    ]
    isolation_numerator = sum(
        cast(dict[str, int], item["specialist_tool_isolation"])["numerator"]
        for item in run_results
    )
    isolation_denominator = sum(
        cast(dict[str, int], item["specialist_tool_isolation"])["denominator"]
        for item in run_results
    )
    tool_total = sum(cast(int, item["tool_calls"]) for item in run_results)
    token_total = sum(cast(int, item["token_usage"]) for item in run_results)
    return {
        "Decision Accuracy": _rate(
            sum(bool(item["decision_correct"]) for item in run_results), count
        ),
        "Root Service Accuracy": _rate(sum(bool(item) for item in roots), len(roots)),
        "Domain Mechanism Accuracy": _rate(
            sum(bool(item) for item in mechanisms), len(mechanisms)
        ),
        "Evidence Reference Validity": _rate(
            sum(bool(item["evidence_references_valid"]) for item in run_results),
            count,
        ),
        "Need-More-Evidence Accuracy": _rate(
            sum(bool(item) for item in need_more), len(need_more)
        ),
        "Abstention Accuracy": _rate(
            sum(bool(item) for item in abstentions), len(abstentions)
        ),
        "Decoy Resistance": _rate(sum(bool(item) for item in decoys), len(decoys)),
        "Schema Valid Rate": _rate(
            sum(bool(item["schema_valid"]) for item in run_results), count
        ),
        "DAG Validity": _rate(
            sum(bool(item["dag_valid"]) for item in run_results), count
        ),
        "Specialist Tool Isolation": _rate(
            isolation_numerator,
            isolation_denominator,
        ),
        "Budget Compliance": _rate(
            sum(bool(item["budget_compliant"]) for item in run_results), count
        ),
        "Average Tool Calls": {
            "total": tool_total,
            "denominator": count,
            "average": tool_total / count,
        },
        "Token Usage": {
            "total": token_total,
            "denominator": count,
            "average": token_total / count,
        },
    }


def run_domain_evaluation(project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    """Run each isolated trace before reading its evaluator-only truth."""

    root = Path(project_root).resolve(strict=True)
    run_results: list[dict[str, object]] = []
    for variant in DomainVariant:
        for case_id in EVALUATION_CASE_IDS:
            trace = _run_workflow_trace(root, case_id, variant)
            truth = _load_ground_truth(
                root / "eval/phase4/ground-truth",
                case_id,
            )
            run_results.append(_evaluate_run(trace, truth))
    metrics = _aggregate(run_results)
    all_green = all(
        cast(dict[str, int | float], value)["rate"] == 1.0
        for key, value in metrics.items()
        if key
        not in {
            "Average Tool Calls",
            "Token Usage",
        }
    )
    report: dict[str, object] = {
        "schema_version": "phase4.domain-comparison-report.v1",
        "status": "COMPLETED" if all_green else "FAILED",
        "case_order": list(EVALUATION_CASE_IDS),
        "variants": [variant.value for variant in DomainVariant],
        "run_count": len(run_results),
        "run_results": run_results,
        "metrics": metrics,
        "failure_denominator_policy": "all ten runs are retained",
        "superiority_claim": False,
        "phase5_entered": False,
    }
    report["deterministic_semantic_sha256"] = _sha256(report)
    return report
