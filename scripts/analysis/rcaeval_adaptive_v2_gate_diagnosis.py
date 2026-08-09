"""Diagnose Adaptive v2 candidate-3 Gate features without a Provider."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import fmean
from typing import Any, Literal, Mapping

from ecomsre_rcaeval.adapter import ArchitectureContextBuilder
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.scoring import normalize_indicator
from ecomsre_rcaeval_adaptive.runner import _bounded_evidence, _service_ranking
from ecomsre_rcaeval_adaptive.v2 import V2GatePolicy, _normalized_margin
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.dev3_execution import load_private_schedule
from ecomsre_rcaeval_v2.dev3_schedule import Variant
from ecomsre_rcaeval_v2.dev_execution import discover_case_index
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.indicator_evaluation import build_runtime_metric_candidates
from ecomsre_rcaeval_v2.schedule import CaseIdentity, SplitName, case_identity_bytes


Route = Literal["DIRECT_RETURN", "VERIFY_LOGS", "VERIFY_TRACES", "VERIFY_BOTH"]
ROUTES: tuple[Route, ...] = (
    "DIRECT_RETURN",
    "VERIFY_LOGS",
    "VERIFY_TRACES",
    "VERIFY_BOTH",
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_CONFIG_PATH = PROJECT_ROOT / "config/rcaeval-adaptive-v2/agent.json"
_FORBIDDEN_PUBLIC_KEYS = {
    "case_id",
    "run_id",
    "raw_provider_output",
    "private_path",
    "evidence_ref",
    "evidence_refs",
    "api_key",
    "authorization",
    "credentials",
}
_FORBIDDEN_PUBLIC_TEXT = (
    "/users/",
    "/home/",
    "/private/",
    "bearer ",
    "tt-case-",
)
_CONCRETE_REF = re.compile(r"(?:metric|log|trace|indicator):[0-9]{4}", re.IGNORECASE)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Adaptive v2 diagnostic input must be an object")
    return value


def _write_json(path: Path, payload: object, *, private: bool = False) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    path.parent.mkdir(mode=0o700 if private else 0o755, parents=True, exist_ok=True)
    path.write_bytes(encoded)
    path.chmod(0o600 if private else 0o644)


def assert_public_payload(payload: object) -> None:
    """Fail closed on identifiers, paths, credentials, and concrete references."""

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).casefold() in _FORBIDDEN_PUBLIC_KEYS:
                    raise ValueError(f"public payload contains forbidden key: {key}")
                walk(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            lowered = value.casefold()
            if any(marker in lowered for marker in _FORBIDDEN_PUBLIC_TEXT):
                raise ValueError("public payload contains forbidden local material")
            if _CONCRETE_REF.search(value):
                raise ValueError("public payload contains forbidden concrete reference")

    walk(payload)


def _tracked_gate_policy(
    path: Path = AGENT_CONFIG_PATH,
) -> tuple[V2GatePolicy, str]:
    resolved = path.expanduser().resolve(strict=True)
    if resolved != AGENT_CONFIG_PATH.resolve(strict=True):
        raise ValueError("Gate diagnosis requires the tracked production agent config")
    config = _read_object(resolved)
    policy = V2GatePolicy.model_validate(config.get("gate"))
    return policy, hashlib.sha256(resolved.read_bytes()).hexdigest()


def _metrics_rank_risk(rank: int | None, conflict_rank: int) -> bool:
    return rank is None or rank >= conflict_rank


def _continuous(values: list[float]) -> dict[str, float | None]:
    return {
        "minimum": None if not values else min(values),
        "maximum": None if not values else max(values),
        "mean": None if not values else fmean(values),
    }


def _feature_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [
        int(row["metrics_service_rank"])
        for row in rows
        if row["metrics_service_rank"] is not None
    ]
    return {
        "case_count": len(rows),
        "initial_confidence": _continuous([float(row["confidence"]) for row in rows]),
        "metrics_service_rank": {
            "distribution": dict(sorted(Counter(ranks).items())),
            "absent": sum(row["metrics_service_rank"] is None for row in rows),
        },
        "metrics_top1_top2_margin": _continuous(
            [float(row["metrics_margin"]) for row in rows]
        ),
        "initial_service_is_metrics_top1": sum(
            row["initial_service_is_metrics_top1"] for row in rows
        ),
        "diagnosis_evidence_supports_service": sum(
            row["diagnosis_evidence_supports_service"] for row in rows
        ),
        "logs_explicitly_oppose_initial": sum(row["logs_oppose"] for row in rows),
        "propagation_conflict": sum(row["propagation_conflict"] for row in rows),
        "indicator_candidate_available": sum(
            row["indicator_candidate_available"] for row in rows
        ),
        "initial_unstable": sum(row["initial_unstable"] for row in rows),
        "gate_reason_code_distribution": dict(
            sorted(
                Counter(
                    reason for row in rows for reason in row["stored_gate_reason_codes"]
                ).items()
            )
        ),
    }


def _bins(
    rows: list[dict[str, Any]],
    *,
    key: str,
    boundaries: tuple[float, ...],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for lower, upper in zip(boundaries[:-1], boundaries[1:], strict=True):
        selected = [row for row in rows if lower <= float(row[key]) < upper]
        output.append(
            {
                "range": f"[{lower:.2f},{upper:.2f})",
                "case_count": len(selected),
                "initial_root_correct": sum(
                    row["initial_root_correct"] for row in selected
                ),
                "initial_pair_correct": sum(
                    row["initial_pair_correct"] for row in selected
                ),
            }
        )
    return output


def _simulated_route(row: Mapping[str, Any], risk_threshold: int) -> Route:
    if (
        row["trace_semantics"]
        and row["logs_oppose"]
        and (row["below_low"] or row["metrics_rank_risk"])
    ):
        return "VERIFY_BOTH"
    if row["trace_semantics"]:
        return "VERIFY_TRACES"
    if row["logs_oppose"]:
        return "VERIFY_LOGS"
    if int(row["risk_count"]) >= risk_threshold:
        return "VERIFY_LOGS"
    return "DIRECT_RETURN"


def _simulate(rows: list[dict[str, Any]], risk_threshold: int) -> dict[str, Any]:
    routes = [_simulated_route(row, risk_threshold) for row in rows]
    distribution = Counter(routes)
    escalated = [
        (row, route)
        for row, route in zip(rows, routes, strict=True)
        if route != "DIRECT_RETURN"
    ]
    return {
        "risk_signal_threshold": risk_threshold,
        "route_distribution": {route: distribution[route] for route in ROUTES},
        "escalation_count": len(escalated),
        "trace_bearing_count": sum(
            route in {"VERIFY_TRACES", "VERIFY_BOTH"} for _, route in escalated
        ),
        "initial_wrong_capture_count": sum(
            not row["initial_root_correct"] for row, _ in escalated
        ),
        "initial_correct_escalation_count": sum(
            row["initial_root_correct"] for row, _ in escalated
        ),
    }


def _identity_rows(
    *,
    identities: tuple[CaseIdentity, ...],
    cases: Mapping[CaseIdentity, Any],
    terminals_by_case: Mapping[str, dict[str, Any]],
    indicator_config_path: Path,
    direct_confidence_threshold: float,
    low_confidence_threshold: float,
    metrics_conflict_rank: int,
    metrics_margin_threshold: float,
) -> list[dict[str, Any]]:
    indicator_config = load_indicator_config(
        indicator_config_path,
        expected_sha256=hashlib.sha256(indicator_config_path.read_bytes()).hexdigest(),
    )
    rows: list[dict[str, Any]] = []
    for ordinal, identity in enumerate(identities, start=1):
        case = cases[identity]
        terminal = terminals_by_case.get(case.case_id)
        if terminal is None or terminal.get("status") != "COMPLETED":
            raise ValueError("candidate-3 diagnostic requires 60 completed terminals")
        result = terminal.get("result")
        if not isinstance(result, dict) or not isinstance(
            result.get("diagnosis"), dict
        ):
            raise ValueError("candidate-3 terminal lacks a completed diagnosis")
        diagnosis = result["diagnosis"]
        initial = diagnosis.get("initial_diagnosis")
        stored_gate = diagnosis.get("gate_decision")
        if not isinstance(initial, dict) or not isinstance(stored_gate, dict):
            raise ValueError("candidate-3 terminal lacks Gate inputs")

        telemetry = dev_case_to_telemetry_case(case)
        builder = ArchitectureContextBuilder(
            telemetry, Architecture.SINGLE, run_id="d" * 32
        )
        for source in ("metrics", "logs", "traces"):
            builder.query_source(source)  # type: ignore[arg-type]
        context = builder.snapshot()
        candidates = build_runtime_metric_candidates(
            telemetry,
            case_identity_sha256=hashlib.sha256(
                case_identity_bytes(identity)
            ).hexdigest(),
            formula=FormulaId.F0,
            config=indicator_config,
        )
        ranking = _service_ranking(candidates)
        bounded = _bounded_evidence(context)
        service_by_ref = {item.evidence_ref: item.service for item in bounded}
        log_services = tuple(
            dict.fromkeys(
                item.service
                for item in bounded
                if item.source == "logs" and item.service != "unknown"
            )
        )
        trace_services = tuple(
            dict.fromkeys(
                item.service
                for item in bounded
                if item.source == "traces" and item.service != "unknown"
            )
        )
        initial_service = str(initial["root_cause_service"])
        initial_indicator = str(initial["root_cause_indicator"])
        rank = next(
            (
                index
                for index, (service, _) in enumerate(ranking, start=1)
                if service == initial_service
            ),
            None,
        )
        if rank != stored_gate.get("metrics_service_rank"):
            raise ValueError("recomputed candidate-3 Metrics rank differs")
        confidence = initial.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ValueError("candidate-3 confidence must be numeric")
        metrics_margin = stored_gate.get("metrics_top1_top2_margin")
        if not isinstance(metrics_margin, (int, float)) or isinstance(
            metrics_margin, bool
        ):
            raise ValueError("candidate-3 Metrics margin must be numeric")
        recomputed_margin = _normalized_margin(ranking)
        if not math.isclose(
            float(metrics_margin), recomputed_margin, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("recomputed candidate-3 Metrics margin differs")
        evidence_supports = any(
            service_by_ref.get(str(reference)) == initial_service
            for reference in initial.get("evidence_refs", [])
        )
        metrics_top = ranking[0][0]
        logs_oppose = (
            initial_service not in log_services[:2] and metrics_top in log_services[:2]
        )
        propagation_conflict = (
            bool(trace_services)
            and initial_service not in trace_services[:2]
            and metrics_top in trace_services[:2]
        )
        trace_semantics = (
            telemetry.traces_path is not None
            and initial_indicator in {"latency", "socket"}
            and propagation_conflict
        )
        indicator_available = any(
            item.service == initial_service for item in candidates
        )
        below_direct = float(confidence) < direct_confidence_threshold
        below_low = float(confidence) < low_confidence_threshold
        metrics_conflict = rank is None or rank >= metrics_conflict_rank
        metrics_rank_risk = _metrics_rank_risk(rank, metrics_conflict_rank)
        metrics_margin_risk = float(metrics_margin) < metrics_margin_threshold
        evidence_weak = not evidence_supports
        indicator_missing = not indicator_available
        risk_count = sum(
            (
                below_direct,
                metrics_rank_risk,
                metrics_margin_risk,
                evidence_weak,
                indicator_missing,
            )
        )
        initial_root_correct = initial_service == identity.root_cause_service
        initial_pair_correct = (
            initial_root_correct
            and initial_indicator == normalize_indicator(identity.fault)
        )
        rows.append(
            {
                "pair_ordinal": ordinal,
                "initial_root_correct": initial_root_correct,
                "initial_pair_correct": initial_pair_correct,
                "confidence": float(confidence),
                "metrics_service_rank": rank,
                "metrics_margin": float(metrics_margin),
                "initial_service_is_metrics_top1": rank == 1,
                "diagnosis_evidence_supports_service": evidence_supports,
                "logs_oppose": logs_oppose,
                "propagation_conflict": propagation_conflict,
                "trace_semantics": trace_semantics,
                "indicator_candidate_available": indicator_available,
                "below_direct": below_direct,
                "below_low": below_low,
                "metrics_conflict": metrics_conflict,
                "metrics_rank_risk": metrics_rank_risk,
                "metrics_margin_risk": metrics_margin_risk,
                "evidence_weak": evidence_weak,
                "indicator_missing": indicator_missing,
                "risk_count": risk_count,
                "initial_unstable": bool(stored_gate.get("initial_unstable")),
                "stored_route": stored_gate.get("route"),
                "stored_gate_reason_codes": tuple(
                    str(item) for item in stored_gate.get("reason_codes", [])
                ),
            }
        )
    return rows


def build_diagnosis(
    rows: list[dict[str, Any]],
    *,
    direct_confidence_threshold: float,
    low_confidence_threshold: float,
    metrics_conflict_rank: int,
    metrics_margin_threshold: float,
) -> dict[str, Any]:
    if len(rows) != 60:
        raise ValueError("candidate-3 Gate diagnosis requires exactly 60 rows")
    current_routes = Counter(str(row["stored_route"]) for row in rows)
    correct = [row for row in rows if row["initial_root_correct"]]
    wrong = [row for row in rows if not row["initial_root_correct"]]
    policy_a = _simulate(rows, 2)
    policy_b = _simulate(rows, 1)
    return {
        "schema_version": "rcaeval-adaptive-v2.gate-diagnosis.v1",
        "classification": [
            "POST_HOC_CONSUMED_TUNE_DIAGNOSTIC",
            "NO_PROVIDER_CALLS",
            "NOT_EXTERNAL_VALIDATION",
        ],
        "provider_calls": 0,
        "scope": {
            "candidate": "candidate-3",
            "completed_records": len(rows),
            "initial_root_correct": len(correct),
            "initial_root_wrong": len(wrong),
        },
        "policy_inputs": {
            "direct_confidence_threshold": direct_confidence_threshold,
            "low_confidence_threshold": low_confidence_threshold,
            "metrics_conflict_rank": metrics_conflict_rank,
            "metrics_margin_threshold": metrics_margin_threshold,
        },
        "features_by_initial_root_outcome": {
            "correct": _feature_group(correct),
            "wrong": _feature_group(wrong),
        },
        "control_flow_audit": {
            "below_direct": sum(row["below_direct"] for row in rows),
            "below_low": sum(row["below_low"] for row in rows),
            "metrics_conflict": sum(row["metrics_conflict"] for row in rows),
            "metrics_rank_risk": sum(row["metrics_rank_risk"] for row in rows),
            "metrics_margin_risk": sum(row["metrics_margin_risk"] for row in rows),
            "evidence_weak": sum(row["evidence_weak"] for row in rows),
            "logs_oppose": sum(row["logs_oppose"] for row in rows),
            "propagation_conflict": sum(row["propagation_conflict"] for row in rows),
            "trace_semantics": sum(row["trace_semantics"] for row in rows),
            "indicator_missing": sum(row["indicator_missing"] for row in rows),
            "initial_unstable": sum(row["initial_unstable"] for row in rows),
            "initial_unstable_and_direct": sum(
                row["initial_unstable"] and row["stored_route"] == "DIRECT_RETURN"
                for row in rows
            ),
            "stored_route_distribution": dict(sorted(current_routes.items())),
            "computed_but_not_route_authoritative": [
                "LOW_CONFIDENCE",
                "METRICS_MARGIN_RISK",
                "INITIAL_UNSTABLE",
            ],
        },
        "confidence_bins": _bins(
            rows,
            key="confidence",
            boundaries=(0.0, 0.5, 0.75, 0.9, 1.0000001),
        ),
        "metrics_margin_bins": _bins(
            rows,
            key="metrics_margin",
            boundaries=(0.0, 0.05, 0.10, 0.20, 1.0000001),
        ),
        "offline_policy_simulations": {
            "policy_a_risk_count_at_least_2": policy_a,
            "policy_b_risk_count_at_least_1": policy_b,
            "limitations": "Route-only simulation; Specialist and Final accuracy are not estimated.",
        },
    }


def _markdown(report: Mapping[str, Any]) -> str:
    scope = report["scope"]
    audit = report["control_flow_audit"]
    simulations = report["offline_policy_simulations"]
    policy_source = report["policy_source"]
    policy_a = simulations["policy_a_risk_count_at_least_2"]
    policy_b = simulations["policy_b_risk_count_at_least_1"]
    return "\n".join(
        (
            "# Adaptive v2 candidate-3 Gate diagnosis",
            "",
            "Classification: `POST_HOC_CONSUMED_TUNE_DIAGNOSTIC / NO_PROVIDER_CALLS / NOT_EXTERNAL_VALIDATION`.",
            f"Gate policy: `{policy_source['classification']}` (`agent.json` SHA-256 `{policy_source['agent_config_sha256']}`).",
            "",
            "## Finding",
            "",
            f"All {scope['completed_records']} completed records used `DIRECT_RETURN`, including all {audit['initial_unstable_and_direct']} records marked unstable. The current control flow records some risk signals but does not make confidence, Metrics margin, or the aggregate unstable flag independently route-authoritative.",
            "",
            "## Initial outcome",
            "",
            f"- Initial Root correct / wrong: {scope['initial_root_correct']} / {scope['initial_root_wrong']}",
            f"- Below direct / below low: {audit['below_direct']} / {audit['below_low']}",
            f"- Metrics rank / margin risk: {audit['metrics_rank_risk']} / {audit['metrics_margin_risk']}",
            f"- Evidence weak / Logs opposition: {audit['evidence_weak']} / {audit['logs_oppose']}",
            f"- Propagation conflict / strict Trace semantics: {audit['propagation_conflict']} / {audit['trace_semantics']}",
            f"- Indicator missing: {audit['indicator_missing']}",
            "",
            "## Finite route simulations",
            "",
            f"- Policy A (`risk_count >= 2`): {policy_a['escalation_count']} escalations; {policy_a['initial_wrong_capture_count']} Initial-wrong captured; {policy_a['initial_correct_escalation_count']} Initial-correct escalated.",
            f"- Policy B (`risk_count >= 1`): {policy_b['escalation_count']} escalations; {policy_b['initial_wrong_capture_count']} Initial-wrong captured; {policy_b['initial_correct_escalation_count']} Initial-correct escalated.",
            "",
            "These are route-only simulations over consumed TUNE features. They do not estimate Specialist or Final accuracy.",
            "",
        )
    )


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal-root", required=True, type=Path)
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--indicator-config", required=True, type=Path)
    parser.add_argument("--public-json", required=True, type=Path)
    parser.add_argument("--public-markdown", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--agent-config", type=Path, default=AGENT_CONFIG_PATH)
    args = parser.parse_args(argv)

    private_output = args.private_output.expanduser()
    if not private_output.is_absolute():
        raise ValueError("private diagnostic output must use an absolute path")
    if private_output.resolve(strict=False).is_relative_to(PROJECT_ROOT):
        raise ValueError("private diagnostic output must remain outside Git")
    gate_policy, agent_config_sha256 = _tracked_gate_policy(args.agent_config)
    terminal_paths = tuple(sorted(args.terminal_root.glob("*.json")))
    if len(terminal_paths) != 60:
        raise ValueError("candidate-3 diagnostic requires 60 terminal files")
    terminals = tuple(_read_object(path) for path in terminal_paths)
    if any(item.get("candidate_id") != "candidate-3" for item in terminals):
        raise ValueError("Gate diagnosis accepts candidate-3 only")
    terminals_by_case = {str(item["case_id"]): item for item in terminals}
    if len(terminals_by_case) != 60:
        raise ValueError("candidate-3 diagnostic terminal identities differ")

    schedule = load_private_schedule(args.schedule, allowed_split=SplitName.DESIGN)
    identities = tuple(
        item.identity
        for item in schedule
        if item.variant is Variant.SINGLE_V1_REFERENCE
    )
    if len(identities) != 60 or len(set(identities)) != 60:
        raise ValueError("candidate-3 diagnostic schedule differs")
    cases = discover_case_index(args.ob_root, args.ss_root, set(identities))
    rows = _identity_rows(
        identities=identities,
        cases=cases,
        terminals_by_case=terminals_by_case,
        indicator_config_path=args.indicator_config,
        direct_confidence_threshold=gate_policy.direct_confidence_threshold,
        low_confidence_threshold=gate_policy.low_confidence_threshold,
        metrics_conflict_rank=gate_policy.metrics_conflict_rank,
        metrics_margin_threshold=gate_policy.metrics_margin_threshold,
    )
    report = build_diagnosis(
        rows,
        direct_confidence_threshold=gate_policy.direct_confidence_threshold,
        low_confidence_threshold=gate_policy.low_confidence_threshold,
        metrics_conflict_rank=gate_policy.metrics_conflict_rank,
        metrics_margin_threshold=gate_policy.metrics_margin_threshold,
    )
    report["policy_source"] = {
        "classification": "TRACKED_PRODUCTION_GATE_CONFIG",
        "agent_config_sha256": agent_config_sha256,
    }
    markdown = _markdown(report)
    assert_public_payload(report)
    assert_public_payload(markdown)
    _write_json(args.public_json, report)
    args.public_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.public_markdown.write_text(markdown, encoding="utf-8")
    _write_json(
        private_output,
        {
            "schema_version": "rcaeval-adaptive-v2.gate-diagnosis-private.v1",
            "classification": ["PRIVATE_GIT_EXTERNAL", "NO_PROVIDER_CALLS"],
            "rows": rows,
        },
        private=True,
    )
    print(
        json.dumps(
            {
                "provider_calls": 0,
                "records": len(rows),
                "initial_root_wrong": report["scope"]["initial_root_wrong"],
                "policy_a_escalations": report["offline_policy_simulations"][
                    "policy_a_risk_count_at_least_2"
                ]["escalation_count"],
                "policy_b_escalations": report["offline_policy_simulations"][
                    "policy_b_risk_count_at_least_1"
                ]["escalation_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
