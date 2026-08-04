"""Isolated 12 by 3 visible development evaluation for Phase 5A."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import ModuleType
from typing import Any, cast

from ecomsre.phase1.contracts import Evidence, EvidenceSource
from ecomsre.phase5a.contracts import (
    DiagnosisDecisionV2,
    UnifiedMechanismV2,
)
from ecomsre.phase5a.workflows import (
    DiagnosisVariantV2,
    DiagnosisWorkflowTraceV2,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_VISIBLE_ROOTS = {
    "phase1": Path("config/phase1/replay-cases/agent-visible"),
    "phase4": Path("config/phase4/replay-cases/agent-visible"),
}
_EXPECTED_CASE_COUNTS = {"phase1": 7, "phase4": 5}
_V1_SEMANTIC_SHA256 = (
    "3734e5814a5a0bbe139f7e7ca346e06f0d139ec4f9947b4a97cb6a34c7af14b4"
)


@dataclass(frozen=True, slots=True)
class _GroundTruth:
    suite: str
    case_id: str
    decision: DiagnosisDecisionV2
    root_service: str | None
    fault_mechanism: UnifiedMechanismV2 | None
    decoys: tuple[tuple[EvidenceSource, str, str], ...]


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
            raise ValueError("duplicate evaluator JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite evaluator JSON constant: {value}")


def _load_worker_runner(project_root: Path) -> ModuleType:
    module_name = "_ecomsre_phase5a_evaluator_worker_runner"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = project_root / "eval/phase5a/runner.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 5A evaluator worker runner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _worker_request(project_root: Path, request: dict[str, object]) -> object:
    runner = _load_worker_runner(project_root)
    return cast(object, runner.worker_request(project_root, request))


def run_worker_probe(project_root: Path = PROJECT_ROOT) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    payload = _worker_request(root, {"mode": "probe", "project_root": str(root)})
    if not isinstance(payload, dict):
        raise ValueError("worker probe response must be an object")
    return cast(dict[str, object], payload)


def _discover_cases(project_root: Path, suite: str) -> tuple[str, ...]:
    visible = (project_root / _VISIBLE_ROOTS[suite]).resolve(strict=True)
    case_ids: list[str] = []
    for candidate in visible.iterdir():
        details = candidate.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise ValueError("visible case root contains an unsafe entry")
        if candidate.parent != visible or not (candidate / "manifest.json").is_file():
            raise ValueError("visible case directory is incomplete")
        case_ids.append(candidate.name)
    discovered = tuple(sorted(case_ids))
    if len(discovered) != _EXPECTED_CASE_COUNTS[suite]:
        raise ValueError("visible development template count changed")
    return discovered


def _run_workflow_trace(
    project_root: Path,
    suite: str,
    case_id: str,
    variant: DiagnosisVariantV2,
) -> DiagnosisWorkflowTraceV2:
    root = Path(project_root).resolve(strict=True)
    payload = _worker_request(
        root,
        {
            "mode": "run",
            "project_root": str(root),
            "suite": suite,
            "case_id": case_id,
            "variant": variant.value,
        },
    )
    # JSON has no tuple type. The worker already emitted a validated strict
    # trace; this boundary revalidates its complete shape after JSON transport.
    return DiagnosisWorkflowTraceV2.model_validate_json(
        _canonical_json(payload),
        strict=False,
    )


def _ground_truth_path(project_root: Path, suite: str, case_id: str) -> Path:
    truth_root = (project_root / f"eval/{suite}/ground-truth").resolve(strict=True)
    candidate = truth_root / f"{case_id}.json"
    details = candidate.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("ground truth must be a regular non-symlink file")
    if details.st_size > 64 * 1024 or candidate.resolve(strict=True).parent != truth_root:
        raise ValueError("ground truth escapes its bounded evaluator root")
    return candidate


def _load_ground_truth(
    project_root: Path,
    suite: str,
    case_id: str,
) -> _GroundTruth:
    path = _ground_truth_path(project_root, suite, case_id)
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict) or payload.get("case_id") != case_id:
        raise ValueError("ground truth identity is invalid")
    decision = DiagnosisDecisionV2(payload.get("expected_decision"))
    root_service = payload.get("expected_root_service")
    if root_service is not None and not isinstance(root_service, str):
        raise ValueError("expected root service is invalid")
    raw_mechanism = payload.get("expected_fault_mechanism")
    mechanism = (
        UnifiedMechanismV2(raw_mechanism)
        if isinstance(raw_mechanism, str)
        else None
    )
    decoys: list[tuple[EvidenceSource, str, str]] = []
    raw_decoys = payload.get("decoys", payload.get("decoy_evidence", []))
    if not isinstance(raw_decoys, list):
        raise ValueError("ground truth decoys must be an array")
    for item in raw_decoys:
        if isinstance(item, dict):
            decoys.append(
                (
                    EvidenceSource(item["source"]),
                    str(item["service"]),
                    str(item["observation_type"]),
                )
            )
        elif isinstance(item, str):
            parts = item.split(":")
            if len(parts) != 3:
                raise ValueError("domain decoy selector is invalid")
            decoys.append((EvidenceSource(parts[0]), parts[1], parts[2]))
        else:
            raise ValueError("ground truth decoy selector is invalid")
    return _GroundTruth(
        suite=suite,
        case_id=case_id,
        decision=decision,
        root_service=root_service,
        fault_mechanism=mechanism,
        decoys=tuple(decoys),
    )


def _trace_evidence(trace: DiagnosisWorkflowTraceV2) -> tuple[Evidence, ...]:
    by_ref = {
        item.evidence_ref: item
        for record in trace.tool_call_records
        for item in record.evidence
    }
    return tuple(by_ref[key] for key in sorted(by_ref))


def _rate(values: list[bool]) -> dict[str, int | float]:
    return {
        "numerator": sum(values),
        "denominator": len(values),
        "rate": sum(values) / len(values) if values else 0.0,
    }


def _average(values: list[int | float]) -> dict[str, int | float]:
    total = sum(values)
    return {
        "total": total,
        "denominator": len(values),
        "average": total / len(values) if values else 0.0,
    }


def _subset_tags(
    trace: DiagnosisWorkflowTraceV2,
    truth: _GroundTruth,
) -> tuple[str, ...]:
    tags: list[str] = []
    if any(
        item.status.value != "AVAILABLE" for item in trace.source_observations
    ):
        tags.append("missing telemetry")
    if truth.decoys:
        tags.append("decoy change")
    if truth.fault_mechanism in {
        UnifiedMechanismV2.RUNTIME_CONFIGURATION_FAILURE,
        UnifiedMechanismV2.RANKING_CONFIGURATION_FAILURE,
    }:
        tags.append("configuration")
    if truth.fault_mechanism is UnifiedMechanismV2.CACHE_BACKEND_TIMEOUT:
        tags.append("cache/dependency")
    if truth.suite == "phase4":
        tags.append("Search/Recommendation domain")
    if truth.decision is DiagnosisDecisionV2.ABSTAIN:
        tags.append("negative/no-incident")
    return tuple(tags)


def _evaluate_run(
    trace: DiagnosisWorkflowTraceV2,
    truth: _GroundTruth,
) -> dict[str, object]:
    final = trace.final_diagnosis
    decision = final.decision if final is not None else None
    evidence = _trace_evidence(trace)
    evidence_refs = {item.evidence_ref for item in evidence}
    cited = (
        {*final.supporting_evidence, *final.contradicting_evidence}
        if final is not None
        else set()
    )
    decoy_refs = {
        item.evidence_ref
        for item in evidence
        if (item.source, item.service, item.observation_type) in truth.decoys
    }
    decoy_resistant = (
        final is not None
        and bool(decoy_refs)
        and decoy_refs.isdisjoint(final.supporting_evidence)
        if truth.decoys
        else None
    )
    missing_telemetry = any(
        item.status.value != "AVAILABLE" for item in trace.source_observations
    )
    confirmed_truth = truth.decision is DiagnosisDecisionV2.RCA_CONFIRMED
    contradiction_applicable = truth.decision is DiagnosisDecisionV2.ABSTAIN or bool(
        truth.decoys
    )
    return {
        "suite": truth.suite,
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
        "expected_decision": truth.decision.value,
        "expected_root_service": truth.root_service,
        "expected_fault_mechanism": (
            truth.fault_mechanism.value if truth.fault_mechanism is not None else None
        ),
        "decision_correct": decision is truth.decision,
        "root_service_correct": (
            final is not None and final.root_service == truth.root_service
            if confirmed_truth
            else None
        ),
        "mechanism_correct": (
            final is not None and final.fault_mechanism is truth.fault_mechanism
            if confirmed_truth
            else None
        ),
        "evidence_references_valid": final is not None and cited <= evidence_refs,
        "need_more_evidence_correct": (
            decision is DiagnosisDecisionV2.NEED_MORE_EVIDENCE
            if truth.decision is DiagnosisDecisionV2.NEED_MORE_EVIDENCE
            else None
        ),
        "abstention_correct": (
            decision is DiagnosisDecisionV2.ABSTAIN
            if truth.decision is DiagnosisDecisionV2.ABSTAIN
            else None
        ),
        "decoy_resistant": decoy_resistant,
        "contradiction_handled": (
            decision is truth.decision and decoy_resistant is not False
            if contradiction_applicable
            else None
        ),
        "schema_valid": trace.status == "COMPLETED" and final is not None,
        "runtime_completed": trace.status == "COMPLETED" and final is not None,
        "model_calls": trace.final_budget_snapshot.charged_model_calls,
        "tool_calls": trace.final_budget_snapshot.charged_tool_calls,
        "token_usage": trace.final_budget_snapshot.cumulative_tokens,
        "deterministic_replay_latency": (
            trace.final_budget_snapshot.monotonic_elapsed_seconds
        ),
        "sources_investigated": len(trace.investigated_sources),
        "targeted_refinement_used": trace.targeted_refinement_used,
        "unnecessary_source_avoided": (
            len(trace.investigated_sources) < 4
            if trace.variant is DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2
            else None
        ),
        "missing_source_recovered": (
            trace.status == "COMPLETED" if missing_telemetry else None
        ),
        "empty_evidence_workflow_failure": (
            trace.status == "FAILED" and missing_telemetry
        ),
        "subset_tags": list(_subset_tags(trace, truth)),
        "trace": trace.model_dump(mode="json"),
    }


_QUALITY_FIELDS = {
    "Decision Accuracy": "decision_correct",
    "Root Service Accuracy": "root_service_correct",
    "Mechanism Accuracy": "mechanism_correct",
    "Evidence Reference Validity": "evidence_references_valid",
    "Need-More-Evidence Accuracy": "need_more_evidence_correct",
    "Abstention Accuracy": "abstention_correct",
    "Decoy Resistance": "decoy_resistant",
    "Contradiction Handling": "contradiction_handled",
    "Schema Valid Rate": "schema_valid",
    "Runtime Completion Rate": "runtime_completed",
}


def _aggregate(run_results: list[dict[str, object]]) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for label, field in _QUALITY_FIELDS.items():
        values = [
            cast(bool, item[field])
            for item in run_results
            if item[field] is not None
        ]
        metrics[label] = _rate(values)
    metrics.update(
        {
            "Model Calls": _average(
                [cast(int, item["model_calls"]) for item in run_results]
            ),
            "Tool Calls": _average(
                [cast(int, item["tool_calls"]) for item in run_results]
            ),
            "Token Usage": _average(
                [cast(int, item["token_usage"]) for item in run_results]
            ),
            "Deterministic replay latency": _average(
                [
                    cast(float, item["deterministic_replay_latency"])
                    for item in run_results
                ]
            ),
            "Average sources investigated": _average(
                [cast(int, item["sources_investigated"]) for item in run_results]
            ),
            "Targeted refinement rate": _rate(
                [cast(bool, item["targeted_refinement_used"]) for item in run_results]
            ),
            "Unnecessary source avoidance": _rate(
                [
                    cast(bool, item["unnecessary_source_avoided"])
                    for item in run_results
                    if item["unnecessary_source_avoided"] is not None
                ]
            ),
            "Missing-source recovery rate": _rate(
                [
                    cast(bool, item["missing_source_recovered"])
                    for item in run_results
                    if item["missing_source_recovered"] is not None
                ]
            ),
        }
    )
    return metrics


def run_capability_parity_evaluation(
    project_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    """Run every isolated trace before reading its public development truth."""

    root = Path(project_root).resolve(strict=True)
    cases_by_suite = {
        suite: _discover_cases(root, suite) for suite in _VISIBLE_ROOTS
    }
    run_results: list[dict[str, object]] = []
    for variant in DiagnosisVariantV2:
        for suite, case_ids in cases_by_suite.items():
            for case_id in case_ids:
                trace = _run_workflow_trace(root, suite, case_id, variant)
                truth = _load_ground_truth(root, suite, case_id)
                run_results.append(_evaluate_run(trace, truth))

    variant_results = []
    for variant in DiagnosisVariantV2:
        selected = [
            item for item in run_results if item["variant"] == variant.value
        ]
        variant_results.append(
            {
                "variant": variant.value,
                "run_count": len(selected),
                "metrics": _aggregate(selected),
            }
        )
    hard_subset_names = (
        "missing telemetry",
        "decoy change",
        "configuration",
        "cache/dependency",
        "Search/Recommendation domain",
        "negative/no-incident",
    )
    hard_subsets = {
        name: {
            "run_count": len(selected := [
                item
                for item in run_results
                if name in cast(list[str], item["subset_tags"])
            ]),
            "metrics": _aggregate(selected),
        }
        for name in hard_subset_names
    }
    original = [item for item in run_results if item["suite"] == "phase1"]
    original_correct = {
        variant.value: sum(
            bool(item["decision_correct"])
            for item in original
            if item["variant"] == variant.value
        )
        for variant in DiagnosisVariantV2
    }
    tool_average: dict[str, int | float] = {}
    for item in variant_results:
        metrics = cast(dict[str, object], item["metrics"])
        tool_calls = cast(dict[str, int | float], metrics["Tool Calls"])
        tool_average[cast(str, item["variant"])] = tool_calls["average"]
    runtime_completed = sum(
        bool(item["runtime_completed"]) for item in run_results
    )
    failures = len(run_results) - runtime_completed
    empty_failures = sum(
        bool(item["empty_evidence_workflow_failure"]) for item in run_results
    )
    fixed_score = original_correct[DiagnosisVariantV2.FIXED_SPECIALIST_V2.value]
    dynamic_score = original_correct[
        DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2.value
    ]
    dynamic_tools = cast(
        float,
        tool_average[DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2.value],
    )
    fixed_tools = cast(
        float,
        tool_average[DiagnosisVariantV2.FIXED_SPECIALIST_V2.value],
    )
    passed = (
        runtime_completed == 36
        and empty_failures == 0
        and fixed_score > 2
        and dynamic_score > 2
        and dynamic_tools <= fixed_tools
    )
    report: dict[str, object] = {
        "schema_version": "phase5a.capability-parity-report.v2",
        "status": "COMPLETED" if passed else "FAILED",
        "evaluation_label": "VISIBLE DEVELOPMENT EVALUATION",
        "claim_boundary": "NOT A SUPERIORITY CLAIM",
        "case_counts": {suite: len(ids) for suite, ids in cases_by_suite.items()},
        "variants": [variant.value for variant in DiagnosisVariantV2],
        "run_count": len(run_results),
        "run_results": run_results,
        "variant_results": variant_results,
        "hard_subsets": hard_subsets,
        "runtime_gate": {
            "typed_terminal_results": runtime_completed,
            "workflow_failures": failures,
            "empty_evidence_failures": empty_failures,
        },
        "quality_gate": {
            "single_original_7": original_correct[
                DiagnosisVariantV2.SINGLE_AGENT_V2.value
            ],
            "fixed_original_7": fixed_score,
            "dynamic_original_7": dynamic_score,
        },
        "efficiency_gate": {
            "dynamic_average_tool_calls": dynamic_tools,
            "fixed_average_tool_calls": fixed_tools,
        },
        "frozen_v1_baseline": {
            "single": "7/7",
            "fixed": "2/7",
            "dynamic": "2/7",
            "semantic_sha256": _V1_SEMANTIC_SHA256,
        },
        "failure_denominator_policy": "all 36 runs are retained",
        "superiority_claim": False,
        "hidden_evaluation": False,
        "phase5b_entered": False,
        "live_mutation": False,
        "new_remediation_action": False,
    }
    report["deterministic_semantic_sha256"] = _sha256(report)
    return report
