"""Evaluator-only isolated 7-case by 3-variant replay comparison."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from ecomsre.phase1.contracts import Evidence, RCADecision
from ecomsre.phase2.comparison_adapter import BudgetCaps
from ecomsre.phase2.contracts import (
    Phase2Variant,
    SPECIALIST_TOOL_BINDINGS,
)
from ecomsre.phase2.workflows import WorkflowRunTrace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_CASE_IDS = (
    "ad-partial-failure-complete",
    "ad-partial-failure-without-logs",
    "ad-partial-failure-frontend-decoy",
    "ad-change-with-normal-sli",
    "telemetry-insufficient",
    "no-real-incident",
    "recommendation-cache-failure",
)
FROZEN_SINGLE_AGENT_SEMANTIC_SHA256 = (
    "15d8da7e6b589fddf4cbb50dd611b60b9c72196bcec701c268867bbfb9ba3a01"
)
_WORKFLOW_CEILINGS = {
    Phase2Variant.SINGLE_AGENT: {"model_calls": 8, "tool_calls": 8},
    Phase2Variant.FIXED_SPECIALIST_WORKFLOW: {
        "model_calls": 5,
        "tool_calls": 4,
    },
    Phase2Variant.DYNAMIC_MULTI_AGENT: {"model_calls": 8, "tool_calls": 5},
}
_MAX_WORKER_RESPONSE_BYTES = 8 * 1024 * 1024
_WORKER_TIMEOUT_SECONDS = 60.0


def _load_phase1_evaluator() -> ModuleType:
    module_name = "_ecomsre_phase1_evaluator_for_phase2"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return cast(ModuleType, existing)
    source = PROJECT_ROOT / "eval/phase1/run.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 1 evaluator spec cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_PHASE1_EVALUATOR = _load_phase1_evaluator()
_load_ground_truth = _PHASE1_EVALUATOR._load_ground_truth


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


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate worker response key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite worker response constant: {value}")


def _sandbox_profile(project_root: Path) -> str:
    evaluator_root = (project_root / "eval/phase1").resolve(strict=True)
    literal = '"' + str(evaluator_root).replace("\\", "\\\\").replace(
        '"', '\\"'
    ) + '"'
    return "\n".join(
        (
            "(version 1)",
            "(allow default)",
            f"(deny file-read* (subpath {literal}))",
            f"(deny file-write* (subpath {literal}))",
            "(deny network*)",
        )
    )


def _worker_request(project_root: Path, request: dict[str, object]) -> object:
    root = Path(project_root).resolve(strict=True)
    sandbox = _PHASE1_EVALUATOR._verified_sandbox_exec()
    worker = root / "src/ecomsre/phase2/replay_worker.py"
    environment = dict(os.environ)
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    ):
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            str(sandbox),
            "-p",
            _sandbox_profile(root),
            sys.executable,
            "-I",
            str(worker),
        ],
        input=(_canonical_json(request) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=root,
        env=environment,
        timeout=_WORKER_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"isolated Phase 2 replay worker failed: {detail}")
    if len(completed.stdout) > _MAX_WORKER_RESPONSE_BYTES:
        raise ValueError("isolated Phase 2 worker response exceeds size limit")
    return json.loads(
        completed.stdout.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


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
    variant: Phase2Variant,
) -> WorkflowRunTrace:
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
    return WorkflowRunTrace.model_validate(payload)


def _rate(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else 0.0,
    }


def _trace_evidence(trace: WorkflowRunTrace) -> tuple[Evidence, ...]:
    by_ref: dict[str, Evidence] = {}
    for record in trace.tool_call_records:
        for evidence in record.evidence:
            by_ref[evidence.evidence_ref] = evidence
    return tuple(by_ref[key] for key in sorted(by_ref))


def _budget_compliant(trace: WorkflowRunTrace) -> bool:
    snapshot = trace.final_budget_snapshot
    charged_audits = tuple(
        record for record in trace.model_call_audits if record.status == "CHARGED"
    )
    return (
        trace.status == "COMPLETED"
        and snapshot.max_model_calls == 8
        and snapshot.max_tool_calls == 8
        and snapshot.max_total_tokens == BudgetCaps().total_tokens
        and snapshot.charged_model_calls == len(charged_audits)
        and snapshot.charged_tool_calls == len(trace.tool_call_audits)
        and snapshot.cumulative_tokens
        == sum(cast(int, record.total_tokens) for record in charged_audits)
        and snapshot.charged_model_calls <= snapshot.max_model_calls
        and snapshot.charged_tool_calls <= snapshot.max_tool_calls
        and snapshot.cumulative_tokens <= snapshot.max_total_tokens
    )


def _isolation_conforming(trace: WorkflowRunTrace) -> tuple[int, int]:
    expected = {
        source: tool_name
        for _, source, tool_name in SPECIALIST_TOOL_BINDINGS.values()
    }
    specialist_calls = tuple(
        record
        for record in trace.tool_call_audits
        if trace.variant is not Phase2Variant.SINGLE_AGENT
    )
    conforming = sum(
        expected[record.source] is record.tool_name for record in specialist_calls
    )
    return conforming, len(specialist_calls)


def _evaluate_case(
    trace: WorkflowRunTrace,
    truth: Any,
) -> dict[str, object]:
    final = trace.final_rca
    decision = final.decision if final is not None else None
    root_service = final.root_service if final is not None else None
    mechanism = final.fault_mechanism if final is not None else None
    decision_correct = decision is truth.expected_decision
    confirmed_truth = truth.expected_decision is RCADecision.RCA_CONFIRMED
    root_correct = (
        decision_correct and root_service == truth.expected_root_service
        if confirmed_truth
        else None
    )
    mechanism_correct = (
        decision_correct and mechanism is truth.expected_fault_mechanism
        if confirmed_truth
        else None
    )
    abstention_correct = decision_correct if not confirmed_truth else None
    evidence = _trace_evidence(trace)
    evidence_by_ref = {item.evidence_ref: item for item in evidence}
    cited = (
        {*final.supporting_evidence, *final.contradicting_evidence}
        if final is not None
        else set()
    )
    evidence_references_valid = final is not None and cited <= evidence_by_ref.keys()
    decoy_resistant: bool | None = None
    if truth.decoys:
        decoy_refs = {
            item.evidence_ref
            for item in evidence
            if any(
                item.source is decoy.source
                and item.service == decoy.service
                and item.observation_type == decoy.observation_type
                for decoy in truth.decoys
            )
        }
        decoy_resistant = (
            decision_correct
            and root_service == truth.expected_root_service
            and final is not None
            and bool(decoy_refs)
            and decoy_refs.isdisjoint(final.supporting_evidence)
        )
    graph = trace.admitted_graph
    refinement_used = graph is not None and graph.refinement_fragment is not None
    return {
        "case_id": trace.case_id,
        "run_id": trace.run_id,
        "status": trace.status,
        "failure_code": (
            trace.terminal_failure_code.value
            if trace.terminal_failure_code is not None
            else None
        ),
        "terminal_reason": trace.terminal_reason,
        "decision": decision.value if decision is not None else None,
        "root_service": root_service,
        "fault_mechanism": mechanism.value if mechanism is not None else None,
        "schema_valid": trace.status == "COMPLETED" and final is not None,
        "evidence_references_valid": evidence_references_valid,
        "decision_correct": decision_correct,
        "root_service_correct": root_correct,
        "fault_mechanism_correct": mechanism_correct,
        "abstention_correct": abstention_correct,
        "decoy_resistant": decoy_resistant,
        "model_calls": trace.final_budget_snapshot.charged_model_calls,
        "tool_calls": trace.final_budget_snapshot.charged_tool_calls,
        "token_usage": trace.final_budget_snapshot.cumulative_tokens,
        "monotonic_latency_seconds": (
            trace.final_budget_snapshot.monotonic_elapsed_seconds
        ),
        "plan_ids": (
            [graph.initial_plan.plan_id] if graph is not None else []
        ),
        "finding_ids": [item.finding_id for item in trace.findings],
        "refinement_used": refinement_used,
        "initial_node_count": (
            len(graph.initial_plan.nodes) if graph is not None else 0
        ),
        "graph_valid": graph is not None,
        "budget_compliant": _budget_compliant(trace),
        "model_audit_count": len(trace.model_call_audits),
        "tool_audit_count": len(trace.tool_call_audits),
    }


def _aggregate_variant(
    variant: Phase2Variant,
    case_results: list[dict[str, object]],
    traces: list[WorkflowRunTrace],
) -> dict[str, object]:
    count = len(case_results)
    root_values = [
        item["root_service_correct"]
        for item in case_results
        if item["root_service_correct"] is not None
    ]
    mechanism_values = [
        item["fault_mechanism_correct"]
        for item in case_results
        if item["fault_mechanism_correct"] is not None
    ]
    abstention_values = [
        item["abstention_correct"]
        for item in case_results
        if item["abstention_correct"] is not None
    ]
    decoy_values = [
        item["decoy_resistant"]
        for item in case_results
        if item["decoy_resistant"] is not None
    ]
    tool_per_case = {
        cast(str, item["case_id"]): cast(int, item["tool_calls"])
        for item in case_results
    }
    token_per_case = {
        cast(str, item["case_id"]): cast(int, item["token_usage"])
        for item in case_results
    }
    latency_per_case = {
        cast(str, item["case_id"]): cast(
            float, item["monotonic_latency_seconds"]
        )
        for item in case_results
    }
    tool_total = sum(tool_per_case.values())
    token_total = sum(token_per_case.values())
    latency_total = sum(latency_per_case.values())
    primary = {
        "Decision Accuracy": _rate(
            sum(bool(item["decision_correct"]) for item in case_results), count
        ),
        "Schema Valid Rate": _rate(
            sum(bool(item["schema_valid"]) for item in case_results), count
        ),
        "Root Service Accuracy": _rate(
            sum(bool(item) for item in root_values), len(root_values)
        ),
        "Fault Mechanism Accuracy": _rate(
            sum(bool(item) for item in mechanism_values), len(mechanism_values)
        ),
        "Evidence Reference Validity": _rate(
            sum(bool(item["evidence_references_valid"]) for item in case_results),
            count,
        ),
        "Abstention Accuracy": _rate(
            sum(bool(item) for item in abstention_values), len(abstention_values)
        ),
        "Decoy Resistance": _rate(
            sum(bool(item) for item in decoy_values), len(decoy_values)
        ),
        "Average Tool Calls": {
            "per_case": tool_per_case,
            "total": tool_total,
            "denominator": count,
            "average": tool_total / count,
        },
        "Token Usage": {
            "per_case": token_per_case,
            "total": token_total,
            "denominator": count,
            "average": token_total / count,
        },
        "Wall-clock Latency": {
            "per_case": latency_per_case,
            "total_seconds": latency_total,
            "denominator": count,
            "average_seconds": latency_total / count,
            "measurement": "deterministic injected monotonic replay clock",
        },
    }
    isolation_pairs = tuple(_isolation_conforming(trace) for trace in traces)
    graph_shapes: dict[str, int] = {}
    for item in case_results:
        shape = str(item["initial_node_count"])
        graph_shapes[shape] = graph_shapes.get(shape, 0) + 1
    refinement_results = [item for item in case_results if item["refinement_used"]]
    diagnostic = {
        "DAG Validity": {
            **_dag_validity(variant, case_results),
            "shape_distribution": graph_shapes,
        },
        "Specialist Tool Isolation": _rate(
            sum(pair[0] for pair in isolation_pairs),
            sum(pair[1] for pair in isolation_pairs),
        ),
        "Budget Compliance": _rate(
            sum(bool(item["budget_compliant"]) for item in case_results), count
        ),
        "Refinement Utility": _rate(
            sum(bool(item["decision_correct"]) for item in refinement_results),
            len(refinement_results),
        ),
        "Unnecessary Source Avoidance": _rate(
            (
                sum(cast(int, item["initial_node_count"]) < 4 for item in case_results)
                if variant is Phase2Variant.DYNAMIC_MULTI_AGENT
                else 0
            ),
            count if variant is Phase2Variant.DYNAMIC_MULTI_AGENT else 0,
        ),
    }
    caps = BudgetCaps()
    result: dict[str, object] = {
        "variant": variant.value,
        "case_results": case_results,
        "failed_case_count": sum(
            item["status"] == "FAILED" for item in case_results
        ),
        "outer_caps": {
            "model_calls": caps.model_calls,
            "tool_calls": caps.tool_calls,
            "total_tokens": caps.total_tokens,
        },
        "workflow_call_ceiling": _WORKFLOW_CEILINGS[variant],
        "primary_metrics": primary,
        "diagnostic_metrics": diagnostic,
    }
    result["deterministic_semantic_sha256"] = _sha256(result)
    return result


def _dag_validity(
    variant: Phase2Variant,
    case_results: list[dict[str, object]],
) -> dict[str, int | float]:
    if variant is Phase2Variant.SINGLE_AGENT:
        return _rate(0, 0)
    return _rate(
        sum(bool(item["graph_valid"]) for item in case_results),
        len(case_results),
    )


def _baseline_verification(project_root: Path, enabled: bool) -> dict[str, object]:
    if not enabled:
        return {
            "status": "SKIPPED",
            "expected_semantic_sha256": FROZEN_SINGLE_AGENT_SEMANTIC_SHA256,
            "observed_semantic_sha256": None,
        }
    report = _PHASE1_EVALUATOR.run_evaluation(project_root)
    observed = report["deterministic_semantic_sha256"]
    if observed != FROZEN_SINGLE_AGENT_SEMANTIC_SHA256:
        raise RuntimeError("frozen Single-Agent semantic fingerprint drifted")
    return {
        "status": "VERIFIED",
        "expected_semantic_sha256": FROZEN_SINGLE_AGENT_SEMANTIC_SHA256,
        "observed_semantic_sha256": observed,
    }


def run_comparison(
    project_root: Path = PROJECT_ROOT,
    *,
    verify_baseline: bool = True,
) -> dict[str, object]:
    """Run every isolated workflow before reading its evaluator-only truth."""

    root = Path(project_root).resolve(strict=True)
    baseline = _baseline_verification(root, verify_baseline)
    variant_results: list[dict[str, object]] = []
    for variant in Phase2Variant:
        case_results: list[dict[str, object]] = []
        traces: list[WorkflowRunTrace] = []
        for case_id in EVALUATION_CASE_IDS:
            trace = _run_workflow_trace(root, case_id, variant)
            truth = _load_ground_truth(
                root / "eval/phase1/ground-truth" / f"{case_id}.json",
                case_id,
                allowed_root=root / "eval/phase1/ground-truth",
            )
            traces.append(trace)
            case_results.append(_evaluate_case(trace, truth))
        variant_results.append(_aggregate_variant(variant, case_results, traces))
    report: dict[str, object] = {
        "schema_version": "phase2.comparison-report.v1",
        "status": "COMPLETED",
        "case_order": list(EVALUATION_CASE_IDS),
        "baseline_verification": baseline,
        "variant_results": variant_results,
        "failure_denominator_policy": "all seven cases retained per variant",
    }
    report["deterministic_semantic_sha256"] = _sha256(report)
    return report
