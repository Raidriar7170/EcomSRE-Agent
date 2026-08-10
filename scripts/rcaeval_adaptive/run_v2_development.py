"""Run one bounded Adaptive v2 TUNE or consumed-data regression arm."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Literal, Mapping

from ecomsre_rcaeval.contracts import TerminalRecord, TerminalStatus
from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval.scoring import normalize_indicator
from ecomsre_rcaeval_adaptive.contracts import AdaptiveTerminalStatus
from ecomsre_rcaeval_adaptive.evaluation import BaselineOutcome, load_design_baseline
from ecomsre_rcaeval_adaptive.v2 import (
    AdaptiveV2Route,
    DeterministicFusionPolicy,
    StrongSingleIndicatorPolicy,
    V2GatePolicy,
)
from ecomsre_rcaeval_adaptive.v2_runner import (
    AdaptiveV2TerminalRecord,
    execute_v2_batch,
)
from ecomsre_rcaeval_v2.dev3_execution import (
    load_private_schedule,
    provider_config_from_env_file,
)
from ecomsre_rcaeval_v2.dev3_schedule import Variant
from ecomsre_rcaeval_v2.dev_execution import discover_case_index
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.schedule import CaseIdentity, SplitName


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config/rcaeval-adaptive-v2"
_RUNTIME_SCOPES = (
    "docs/analysis/rcaeval-adaptive-v2-candidate4-metrics-alternative-analysis.json",
    "docs/analysis/rcaeval-adaptive-v2-candidate4-metrics-alternative-analysis.md",
    "docs/design/rcaeval-adaptive-v2-candidate-5-decision.md",
    "scripts/analysis/rcaeval_adaptive_v2_gate_diagnosis.py",
    "src/ecomsre_rcaeval_adaptive/contracts.py",
    "src/ecomsre_rcaeval_adaptive/specialists.py",
    "src/ecomsre_rcaeval_adaptive/v2.py",
    "src/ecomsre_rcaeval_adaptive/v2_runner.py",
    "scripts/rcaeval_adaptive/run_v2_development.py",
    "config/rcaeval-adaptive-v2",
    "tests/benchmarks/rcaeval_adaptive/test_specialists.py",
    "tests/benchmarks/rcaeval_adaptive/test_v2.py",
    "tests/benchmarks/rcaeval_adaptive/test_v2_development.py",
)
_CANDIDATE_IDS = tuple(f"candidate-{index}" for index in range(1, 6))


def _candidate_metadata(
    candidate_id: str, evaluation: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    frozen = (
        _load(CONFIG_ROOT / "evaluation.json") if evaluation is None else evaluation
    )
    budget = frozen.get("candidate_budget")
    if not isinstance(budget, Mapping):
        raise ValueError("Adaptive v2 candidate budget is invalid")
    capacity_ids = tuple(budget.get("capacity_record_ids", ()))
    algorithm_ids = tuple(budget.get("algorithm_candidate_ids", ()))
    candidate_ids = capacity_ids + algorithm_ids
    if (
        candidate_ids != _CANDIDATE_IDS
        or budget.get("record_limit") != len(candidate_ids)
        or budget.get("algorithm_candidate_limit") != len(algorithm_ids)
        or candidate_id not in candidate_ids
    ):
        raise ValueError("Adaptive v2 candidate metadata is invalid")
    return {
        "candidate_kind": (
            "CAPACITY_RECORD" if candidate_id in capacity_ids else "ALGORITHM_TUNE"
        ),
        "algorithm_candidate_ordinal": (
            None
            if candidate_id in capacity_ids
            else algorithm_ids.index(candidate_id) + 1
        ),
        "algorithm_candidate_limit": int(budget["algorithm_candidate_limit"]),
    }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Adaptive v2 config must be an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def _clean_implementation_sha() -> str:
    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "--", *_RUNTIME_SCOPES),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout:
        raise ValueError(
            "Adaptive v2 runtime must be committed before Provider execution"
        )
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _validate_private_run_root(path: Path) -> Path:
    requested = path.expanduser()
    if not requested.is_absolute():
        raise ValueError("Adaptive v2 run root must be an absolute private path")
    if requested.is_symlink():
        raise ValueError("Adaptive v2 run root must not be a symlink")
    resolved = requested.resolve(strict=False)
    project = PROJECT_ROOT.resolve()
    if resolved == project or resolved.is_relative_to(project):
        raise ValueError("Adaptive v2 run root must remain outside Git")
    existing = resolved
    while not existing.exists():
        if existing == existing.parent:
            break
        existing = existing.parent
    inside_git = subprocess.run(
        ("git", "-C", str(existing), "rev-parse", "--is-inside-work-tree"),
        check=False,
        capture_output=True,
        text=True,
    )
    if inside_git.returncode == 0 and inside_git.stdout.strip() == "true":
        raise ValueError("Adaptive v2 run root must remain outside Git")
    return resolved


def _load_private_result(path: Path) -> dict[str, Any]:
    requested = path.expanduser()
    if not requested.is_absolute() or requested.is_symlink():
        raise ValueError(
            "Adaptive v2 prior result must be an absolute non-symlink file"
        )
    resolved = requested.resolve(strict=True)
    _validate_private_run_root(resolved.parent)
    return _load(resolved)


def _validate_tune_lineage(
    candidate_id: str, previous_results: tuple[Path, ...]
) -> tuple[str, ...]:
    if candidate_id not in _CANDIDATE_IDS:
        raise ValueError("Adaptive v2 candidate lineage is invalid")
    ordinal = int(candidate_id[-1])
    expected = tuple(f"candidate-{index}" for index in range(1, ordinal))
    if len(previous_results) != len(expected):
        raise ValueError(
            "Adaptive v2 candidate lineage is incomplete or outside the limit"
        )
    observed: list[str] = []
    for path, expected_id in zip(previous_results, expected, strict=True):
        result = _load_private_result(path)
        aggregate = result.get("aggregate")
        if (
            result.get("schema_version")
            != "rcaeval-single-first-adaptive.development-result.v2"
            or result.get("phase") != "TUNE_SET"
            or result.get("candidate_id") != expected_id
            or not isinstance(aggregate, dict)
            or aggregate.get("scheduled") != 60
            or type(aggregate.get("gate_passed")) is not bool
        ):
            raise ValueError("Adaptive v2 candidate lineage result differs")
        if aggregate["gate_passed"] is True:
            raise ValueError("Adaptive v2 candidate loop already passed")
        observed.append(expected_id)
    return tuple(observed)


def _git_success(*args: str) -> bool:
    return (
        subprocess.run(
            ("git", *args),
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _validate_regression_authorization(
    *,
    candidate_id: str,
    tune_result_path: Path,
    current_implementation_sha: str,
    agent_config_sha256: str,
    model_lock_sha256: str,
    evaluation_config_sha256: str,
    evaluation: Mapping[str, Any],
) -> None:
    result = _load_private_result(tune_result_path)
    aggregate = result.get("aggregate")
    if (
        result.get("schema_version")
        != "rcaeval-single-first-adaptive.development-result.v2"
        or result.get("phase") != "TUNE_SET"
        or result.get("candidate_id") != candidate_id
        or not isinstance(aggregate, dict)
        or aggregate.get("gate_passed") is not True
        or aggregate.get("gate_disposition") != "PASSED"
        or aggregate.get("algorithm_quality_evaluable") is not True
        or not _gate_passed("tune", aggregate, evaluation)
    ):
        raise ValueError("Adaptive v2 regression requires a passed TUNE result")
    lock = _load_private_result(tune_result_path.parent / "candidate-lock.json")
    implementation_sha = lock.get("implementation_git_sha")
    if (
        lock.get("schema_version") != "rcaeval-single-first-adaptive.candidate-lock.v2"
        or lock.get("candidate_id") != candidate_id
        or lock.get("phase") != "TUNE_SET"
        or lock.get("agent_config_sha256") != agent_config_sha256
        or lock.get("model_lock_sha256") != model_lock_sha256
        or lock.get("evaluation_config_sha256") != evaluation_config_sha256
        or not isinstance(implementation_sha, str)
        or len(implementation_sha) != 40
        or not _git_success(
            "merge-base",
            "--is-ancestor",
            implementation_sha,
            current_implementation_sha,
        )
        or not _git_success(
            "diff",
            "--quiet",
            implementation_sha,
            current_implementation_sha,
            "--",
            *_RUNTIME_SCOPES,
        )
    ):
        raise ValueError("Adaptive v2 TUNE binding differs from regression runtime")


def _write_create_once(path: Path, payload: object) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
            raise ValueError("existing Adaptive v2 private artifact differs")
        return
    with path.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _regression_baseline(
    identities: tuple[CaseIdentity, ...],
    cases: Mapping[CaseIdentity, DevCase],
    terminal_root: Path,
) -> dict[CaseIdentity, BaselineOutcome]:
    records = tuple(
        TerminalRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(terminal_root.glob("*.json"))
    )
    if len(records) != 120:
        raise ValueError("Adaptive v2 regression baseline requires 120 terminals")
    by_case = {item.case_id: item for item in records}
    output: dict[CaseIdentity, BaselineOutcome] = {}
    for identity in identities:
        case = cases[identity]
        terminal = by_case.get(case.case_id)
        if terminal is None or terminal.terminal_status is not TerminalStatus.COMPLETED:
            raise ValueError("Adaptive v2 regression baseline is incomplete")
        assert terminal.diagnosis is not None
        root_correct = (
            terminal.diagnosis.root_cause_service == identity.root_cause_service
        )
        output[identity] = BaselineOutcome(
            identity=identity,
            root_correct=root_correct,
            pair_correct=(
                root_correct
                and terminal.diagnosis.root_cause_indicator
                == normalize_indicator(identity.fault)
            ),
        )
    return output


def _aggregate(
    identities: tuple[CaseIdentity, ...],
    terminals: tuple[AdaptiveV2TerminalRecord, ...],
    baseline: Mapping[CaseIdentity, BaselineOutcome],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(identities) != len(terminals):
        raise ValueError("Adaptive v2 evaluation terminal count differs")
    rows: list[dict[str, Any]] = []
    routes: Counter[str] = Counter()
    indicator_actions: Counter[str] = Counter()
    fusion_actions: Counter[str] = Counter()
    fusion_reasons: Counter[str] = Counter()
    pairwise_preferences: Counter[str] = Counter()
    metrics_alternative_ranks: Counter[int] = Counter()
    provider_failure_codes: Counter[str] = Counter()
    for ordinal, (identity, terminal) in enumerate(
        zip(identities, terminals, strict=True), start=1
    ):
        reference = baseline[identity]
        completed = terminal.status is AdaptiveTerminalStatus.COMPLETED
        result = terminal.result
        initial_correct = False
        initial_pair_correct = False
        root_correct = False
        pair_correct = False
        route = None
        semantic_operations = terminal.semantic_operations_attempted
        pairwise_call_attempts = terminal.pairwise_calls_attempted
        correct_override = False
        wrong_override = False
        metrics_alternative_rank = None
        metrics_alternative_is_true_root = None
        pairwise_preference = None
        pairwise_initial_role = None
        pairwise_alternative_role = None
        fusion_reason = None
        decision_basis = None
        if terminal.status is AdaptiveTerminalStatus.PROVIDER_FAILURE:
            provider_failure_codes[
                terminal.failure_code or "UNKNOWN_PROVIDER_FAILURE"
            ] += 1
        if result is not None:
            diagnosis = result.diagnosis
            decision_basis = getattr(
                diagnosis, "decision_basis", "LEGACY_RANKED_HYPOTHESES"
            )
            initial_correct = (
                diagnosis.initial_diagnosis.root_cause_service
                == identity.root_cause_service
            )
            initial_pair_correct = (
                initial_correct
                and diagnosis.initial_diagnosis.root_cause_indicator
                == normalize_indicator(identity.fault)
            )
            root_correct = diagnosis.final_root_service == identity.root_cause_service
            pair_correct = (
                root_correct
                and diagnosis.final_indicator == normalize_indicator(identity.fault)
            )
            route = diagnosis.gate_decision.route.value
            routes[route] += 1
            semantic_operations = (
                terminal.semantic_operations_attempted
                if terminal.semantic_operations_attempted
                else result.semantic_operations
            )
            fusion_actions[diagnosis.fusion_decision.action] += 1
            fusion_reason = getattr(
                diagnosis.fusion_decision,
                "reason_codes",
                ("LEGACY_FUSION",),
            )[0]
            fusion_reasons[fusion_reason] += 1
            indicator_actions[diagnosis.indicator_resolution.action.value] += 1
            metrics_alternative = getattr(diagnosis, "metrics_alternative", None)
            if metrics_alternative is not None:
                metrics_alternative_rank = metrics_alternative.alternative_rank
                metrics_alternative_ranks[metrics_alternative_rank] += 1
                metrics_alternative_is_true_root = (
                    metrics_alternative.alternative_service
                    == identity.root_cause_service
                )
            pairwise = getattr(diagnosis, "logs_pairwise_verification", None)
            if pairwise is not None:
                pairwise_preference = pairwise.preference.value
                pairwise_initial_role = pairwise.initial_role.value
                pairwise_alternative_role = pairwise.alternative_role.value
                pairwise_preferences[pairwise_preference] += 1
            if diagnosis.fusion_decision.action == "OVERRIDE_INITIAL":
                correct_override = root_correct and not initial_correct
                wrong_override = not root_correct
        rows.append(
            {
                "pair_ordinal": ordinal,
                "completed": completed,
                "terminal_status": terminal.status.value,
                "failure_code": terminal.failure_code,
                "baseline_root_correct": reference.root_correct,
                "baseline_pair_correct": reference.pair_correct,
                "initial_root_correct": initial_correct if completed else None,
                "initial_pair_correct": initial_pair_correct if completed else None,
                "root_correct": root_correct,
                "pair_correct": pair_correct,
                "route": route,
                "semantic_operations": semantic_operations,
                "provider_attempts": terminal.attempt_accounting.provider_attempt_count,
                "transport_retries": terminal.attempt_accounting.retry_attempt_count,
                "known_token_lower_bound": terminal.attempt_accounting.known_token_lower_bound,
                "conservative_token_upper_bound": terminal.attempt_accounting.conservative_token_upper_bound,
                "latency_ms": terminal.latency_ms,
                "correct_override": correct_override,
                "wrong_override": wrong_override,
                "specialist_hypothesis_count": (
                    0 if result is None else len(result.diagnosis.specialist_hypotheses)
                ),
                "metrics_alternative_rank": metrics_alternative_rank,
                "metrics_alternative_is_true_root": metrics_alternative_is_true_root,
                "pairwise_preference": pairwise_preference,
                "pairwise_call_attempts": pairwise_call_attempts,
                "pairwise_initial_role": pairwise_initial_role,
                "pairwise_alternative_role": pairwise_alternative_role,
                "fusion_action": (
                    None if result is None else result.diagnosis.fusion_decision.action
                ),
                "fusion_reason": fusion_reason,
                "decision_basis": decision_basis,
            }
        )
    scheduled = len(rows)
    completed_count = sum(item["completed"] for item in rows)
    completed_rows = tuple(item for item in rows if item["completed"])
    root_correct_count = sum(item["root_correct"] for item in rows)
    pair_correct_count = sum(item["pair_correct"] for item in rows)
    initial_root_correct_count = sum(
        item["initial_root_correct"] for item in completed_rows
    )
    initial_pair_correct_count = sum(
        item["initial_pair_correct"] for item in completed_rows
    )
    same_run_root_damage = sum(
        item["initial_root_correct"] and not item["root_correct"]
        for item in completed_rows
    )
    same_run_root_rescue = sum(
        not item["initial_root_correct"] and item["root_correct"]
        for item in completed_rows
    )
    same_run_pair_damage = sum(
        item["initial_pair_correct"] and not item["pair_correct"]
        for item in completed_rows
    )
    same_run_pair_rescue = sum(
        not item["initial_pair_correct"] and item["pair_correct"]
        for item in completed_rows
    )
    escalated_rows = tuple(
        item
        for item in completed_rows
        if item["route"] != AdaptiveV2Route.DIRECT_RETURN.value
    )
    initial_wrong_count = completed_count - initial_root_correct_count
    escalated_initial_wrong = sum(
        not item["initial_root_correct"] for item in escalated_rows
    )
    baseline_pair_correct = sum(item["baseline_pair_correct"] for item in rows)
    baseline_pair_wrong = scheduled - baseline_pair_correct
    baseline_root_correct = sum(item["baseline_root_correct"] for item in rows)
    baseline_root_wrong = scheduled - baseline_root_correct
    historical_root_damage = sum(
        item["baseline_root_correct"] and not item["root_correct"] for item in rows
    )
    historical_root_rescue = sum(
        not item["baseline_root_correct"] and item["root_correct"] for item in rows
    )
    damage = sum(
        item["baseline_pair_correct"] and not item["pair_correct"] for item in rows
    )
    rescue = sum(
        not item["baseline_pair_correct"] and item["pair_correct"] for item in rows
    )
    disqualifying = sum(
        item["terminal_status"]
        in {
            "INVALID_SCHEMA",
            "PROTOCOL_VIOLATION",
            "RUNTIME_CONTRACT_VIOLATION",
            "INTERRUPTED",
        }
        for item in rows
    )
    aggregate = {
        "scheduled": scheduled,
        "completed": completed_count,
        "completion_coverage": _rate(completed_count, scheduled),
        "algorithm_quality_evaluable": completed_count > 0,
        "completed_only_root_service_accuracy": _rate(
            sum(item["root_correct"] for item in completed_rows), completed_count
        ),
        "completed_only_pair_accuracy": _rate(
            sum(item["pair_correct"] for item in completed_rows), completed_count
        ),
        "root_service_correct": root_correct_count,
        "pair_correct": pair_correct_count,
        "initial_root_correct": initial_root_correct_count,
        "final_root_correct": root_correct_count,
        "same_run_root_damage": same_run_root_damage,
        "same_run_root_damage_rate": _rate(
            same_run_root_damage, initial_root_correct_count
        ),
        "same_run_root_rescue": same_run_root_rescue,
        "same_run_root_rescue_rate": _rate(same_run_root_rescue, initial_wrong_count),
        "same_run_root_net_rescue": same_run_root_rescue - same_run_root_damage,
        "initial_pair_correct": initial_pair_correct_count,
        "final_pair_correct": pair_correct_count,
        "same_run_pair_damage": same_run_pair_damage,
        "same_run_pair_damage_rate": _rate(
            same_run_pair_damage, initial_pair_correct_count
        ),
        "same_run_pair_rescue": same_run_pair_rescue,
        "same_run_pair_rescue_rate": _rate(
            same_run_pair_rescue, completed_count - initial_pair_correct_count
        ),
        "same_run_pair_net_rescue": same_run_pair_rescue - same_run_pair_damage,
        "legacy_damage_rescue_alias_classification": [
            "CROSS_RUN_CONTEXTUAL_COMPARISON",
            "MODEL_RUN_VARIABILITY_CONFOUNDED",
        ],
        "damage": damage,
        "damage_rate": _rate(damage, baseline_pair_correct),
        "rescue": rescue,
        "rescue_rate": _rate(rescue, baseline_pair_wrong),
        "net_rescue": rescue - damage,
        "historical_cross_run_comparison": {
            "classification": [
                "CROSS_RUN_CONTEXTUAL_COMPARISON",
                "MODEL_RUN_VARIABILITY_CONFOUNDED",
            ],
            "root_damage": historical_root_damage,
            "root_damage_rate": _rate(historical_root_damage, baseline_root_correct),
            "root_rescue": historical_root_rescue,
            "root_rescue_rate": _rate(historical_root_rescue, baseline_root_wrong),
            "pair_damage": damage,
            "pair_damage_rate": _rate(damage, baseline_pair_correct),
            "pair_rescue": rescue,
            "pair_rescue_rate": _rate(rescue, baseline_pair_wrong),
        },
        "direct_return": routes[AdaptiveV2Route.DIRECT_RETURN.value],
        "route_distribution": {
            route.value: routes[route.value] for route in AdaptiveV2Route
        },
        "trace_routes": (
            routes[AdaptiveV2Route.VERIFY_TRACES.value]
            + routes[AdaptiveV2Route.VERIFY_BOTH.value]
        ),
        "escalation_precision": _rate(escalated_initial_wrong, len(escalated_rows)),
        "escalation_recall": _rate(escalated_initial_wrong, initial_wrong_count),
        "initial_correct_escalated": sum(
            item["initial_root_correct"] for item in escalated_rows
        ),
        "initial_wrong_direct": sum(
            not item["initial_root_correct"]
            and item["route"] == AdaptiveV2Route.DIRECT_RETURN.value
            for item in completed_rows
        ),
        "specialist_hypothesis_count": sum(
            item["specialist_hypothesis_count"] for item in rows
        ),
        "metrics_alternative_rank_distribution": dict(
            sorted(metrics_alternative_ranks.items())
        ),
        "no_metrics_alternative": sum(
            item["decision_basis"] == "METRICS_LOGS_PAIRWISE"
            and item["metrics_alternative_rank"] is None
            for item in rows
        ),
        "pairwise_calls": sum(
            item["pairwise_call_attempts"] for item in rows
        ),
        "pairwise_completed_verifications": sum(
            item["pairwise_preference"] is not None for item in rows
        ),
        "pairwise_preference_distribution": {
            preference: pairwise_preferences[preference]
            for preference in ("INITIAL", "ALTERNATIVE", "INCONCLUSIVE")
        },
        "alternative_preference_when_alternative_true_root": sum(
            item["pairwise_preference"] == "ALTERNATIVE"
            and item["metrics_alternative_is_true_root"] is True
            for item in rows
        ),
        "alternative_preference_when_alternative_wrong": sum(
            item["pairwise_preference"] == "ALTERNATIVE"
            and item["metrics_alternative_is_true_root"] is False
            for item in rows
        ),
        "mean_semantic_operations": sum(item["semantic_operations"] for item in rows)
        / scheduled,
        "mean_semantic_operations_basis": "FIXED_SCHEDULED_DENOMINATOR",
        "mean_semantic_operations_completed_only": (
            None
            if not completed_rows
            else sum(item["semantic_operations"] for item in completed_rows)
            / completed_count
        ),
        "provider_attempts": sum(item["provider_attempts"] for item in rows),
        "transport_retries": sum(item["transport_retries"] for item in rows),
        "known_token_lower_bound": sum(
            item["known_token_lower_bound"] for item in rows
        ),
        "conservative_token_upper_bound": sum(
            item["conservative_token_upper_bound"] for item in rows
        ),
        "mean_latency_ms": sum(item["latency_ms"] for item in rows) / scheduled,
        "mean_latency_ms_completed_only": (
            None
            if not completed_rows
            else sum(item["latency_ms"] for item in completed_rows) / completed_count
        ),
        "correct_overrides": sum(item["correct_override"] for item in rows),
        "wrong_overrides": sum(item["wrong_override"] for item in rows),
        "fusion_action_distribution": dict(sorted(fusion_actions.items())),
        "fusion_reason_distribution": dict(sorted(fusion_reasons.items())),
        "indicator_action_distribution": dict(sorted(indicator_actions.items())),
        "http_429_terminal_failures": sum(
            item["failure_code"] == "HTTP_429" for item in rows
        ),
        "provider_failure_count": sum(provider_failure_codes.values()),
        "provider_failure_code_distribution": dict(
            sorted(provider_failure_codes.items())
        ),
        "disqualifying_failure_count": disqualifying,
    }
    return aggregate, rows


def _gate_passed(
    phase: str,
    aggregate: Mapping[str, Any],
    evaluation: Mapping[str, Any] | None = None,
) -> bool:
    frozen = (
        _load(CONFIG_ROOT / "evaluation.json") if evaluation is None else evaluation
    )
    gate_name = "tune_gate" if phase == "tune" else "regression_gate"
    if phase not in {"tune", "regression"}:
        raise ValueError("Adaptive v2 gate phase is invalid")
    gate = frozen.get(gate_name)
    if not isinstance(gate, Mapping):
        raise ValueError("Adaptive v2 evaluation gate is invalid")
    damage_rate = aggregate["same_run_root_damage_rate"]["value"]
    shared = (
        gate["wrong_override_not_greater_than_correct"] is True
        and aggregate["wrong_overrides"] <= aggregate["correct_overrides"]
        and aggregate["disqualifying_failure_count"]
        <= int(gate["privacy_schema_schedule_failure_max"])
    )
    if phase == "tune":
        return bool(
            shared
            and aggregate["completed"] >= int(gate["completion_min"])
            and aggregate["http_429_terminal_failures"]
            <= int(gate["http_429_terminal_failure_max"])
            and aggregate["final_root_correct"] >= int(gate["root_service_correct_min"])
            and aggregate["final_pair_correct"] >= int(gate["pair_correct_min"])
            and gate["same_run_root_rescue_strictly_greater_than_damage"] is True
            and aggregate["same_run_root_rescue"] > aggregate["same_run_root_damage"]
            and aggregate["same_run_root_damage"]
            <= int(gate["same_run_root_damage_max"])
            and aggregate["same_run_root_net_rescue"]
            >= int(gate["same_run_root_net_rescue_min"])
            and gate["same_run_pair_rescue_not_less_than_damage"] is True
            and aggregate["same_run_pair_rescue"] >= aggregate["same_run_pair_damage"]
            and aggregate["same_run_pair_net_rescue"]
            >= int(gate["same_run_pair_net_rescue_min"])
            and aggregate["direct_return"] >= int(gate["direct_return_min"])
            and aggregate["direct_return"] <= int(gate["direct_return_max"])
            and aggregate["mean_semantic_operations"]
            <= float(gate["mean_semantic_operations_max"])
            and aggregate["trace_routes"] <= int(gate["trace_route_max"])
        )
    return bool(
        shared
        and aggregate["completed"] >= int(gate["completion_min"])
        and aggregate["final_root_correct"] >= int(gate["root_service_correct_min"])
        and aggregate["final_pair_correct"] >= int(gate["pair_correct_min"])
        and gate["same_run_root_rescue_not_less_than_damage"] is True
        and aggregate["same_run_root_rescue"] >= aggregate["same_run_root_damage"]
        and aggregate["same_run_root_net_rescue"]
        >= int(gate["same_run_root_net_rescue_min"])
        and gate["same_run_pair_rescue_not_less_than_damage"] is True
        and aggregate["same_run_pair_rescue"] >= aggregate["same_run_pair_damage"]
        and aggregate["same_run_pair_net_rescue"]
        >= int(gate["same_run_pair_net_rescue_min"])
        and damage_rate is not None
        and damage_rate <= float(gate["same_run_root_damage_rate_max"])
        and aggregate["direct_return"] >= int(gate["direct_return_min"])
        and aggregate["mean_semantic_operations"]
        <= float(gate["mean_semantic_operations_max"])
        and aggregate["trace_routes"] <= int(gate["trace_route_max"])
        and aggregate["http_429_terminal_failures"]
        <= int(gate["http_429_terminal_failure_max"])
    )


def _gate_disposition(
    phase: str,
    aggregate: Mapping[str, Any],
    evaluation: Mapping[str, Any] | None = None,
) -> str:
    if _gate_passed(phase, aggregate, evaluation):
        return "PASSED"
    if (
        aggregate["completed"] == 0
        and aggregate["provider_failure_count"] == aggregate["scheduled"]
    ):
        return (
            "PROVIDER_CAPACITY_BLOCKED"
            if aggregate["http_429_terminal_failures"] > 0
            else "PROVIDER_EXECUTION_BLOCKED"
        )
    return "TUNE_GATE_NOT_PASSED" if phase == "tune" else "REGRESSION_GATE_NOT_PASSED"


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("tune", "regression"))
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--baseline-outcomes", type=Path)
    parser.add_argument("--reference-terminal-root", type=Path)
    parser.add_argument(
        "--previous-tune-result", action="append", default=[], type=Path
    )
    parser.add_argument("--tune-result", type=Path)
    parser.add_argument("--candidate-selection-reason")
    args = parser.parse_args(argv)

    args.run_root = _validate_private_run_root(args.run_root)
    implementation_sha = _clean_implementation_sha()
    agent = _load(CONFIG_ROOT / "agent.json")
    model = _load(CONFIG_ROOT / "model-lock.json")
    evaluation = _load(CONFIG_ROOT / "evaluation.json")
    agent_config_sha256 = _sha(CONFIG_ROOT / "agent.json")
    model_lock_sha256 = _sha(CONFIG_ROOT / "model-lock.json")
    evaluation_config_sha256 = _sha(CONFIG_ROOT / "evaluation.json")
    candidate_metadata = _candidate_metadata(args.candidate_id, evaluation)
    candidate_selection_reason = (
        args.candidate_selection_reason.strip()
        if isinstance(args.candidate_selection_reason, str)
        else None
    )
    if (
        candidate_metadata["candidate_kind"] == "ALGORITHM_TUNE"
        and int(candidate_metadata["algorithm_candidate_ordinal"]) >= 2
        and not candidate_selection_reason
    ):
        raise ValueError(
            "Adaptive v2 real algorithm candidate requires a selection reason"
        )
    if args.phase == "tune":
        if args.tune_result is not None:
            raise ValueError("TUNE_SET does not accept regression authorization")
        _validate_tune_lineage(args.candidate_id, tuple(args.previous_tune_result))
    else:
        if args.previous_tune_result or args.tune_result is None:
            raise ValueError("REGRESSION_SET requires one passed TUNE result")
        _validate_regression_authorization(
            candidate_id=args.candidate_id,
            tune_result_path=args.tune_result,
            current_implementation_sha=implementation_sha,
            agent_config_sha256=agent_config_sha256,
            model_lock_sha256=model_lock_sha256,
            evaluation_config_sha256=evaluation_config_sha256,
            evaluation=evaluation,
        )
    split = SplitName.DESIGN if args.phase == "tune" else SplitName.DEV_VALIDATION
    records = load_private_schedule(args.schedule, allowed_split=split)
    identities = tuple(
        item.identity for item in records if item.variant is Variant.SINGLE_V1_REFERENCE
    )
    expected = 60 if args.phase == "tune" else 120
    if len(identities) != expected or len(set(identities)) != expected:
        raise ValueError("Adaptive v2 development schedule count differs")
    cases = discover_case_index(args.ob_root, args.ss_root, set(identities))
    if args.phase == "tune":
        if args.baseline_outcomes is None:
            raise ValueError("TUNE_SET requires the historical baseline outcomes")
        baseline = load_design_baseline(args.baseline_outcomes)
        split_name: Literal["TUNE_SET", "REGRESSION_SET"] = "TUNE_SET"
    else:
        if args.reference_terminal_root is None:
            raise ValueError("REGRESSION_SET requires Strong Single terminals")
        baseline = _regression_baseline(identities, cases, args.reference_terminal_root)
        split_name = "REGRESSION_SET"
    formula_path = PROJECT_ROOT / str(model["inherited_indicator_config_path"])
    policy_path = PROJECT_ROOT / str(model["inherited_transport_retry_policy_path"])
    if _sha(formula_path) != model["inherited_indicator_config_sha256"]:
        raise ValueError("Adaptive v2 indicator config hash drift")
    if _sha(policy_path) != model["transport_retry_policy_sha256"]:
        raise ValueError("Adaptive v2 transport policy hash drift")
    candidate_lock = {
        "schema_version": "rcaeval-single-first-adaptive.candidate-lock.v2",
        "candidate_id": args.candidate_id,
        "implementation_git_sha": implementation_sha,
        "agent_config_sha256": agent_config_sha256,
        "model_lock_sha256": model_lock_sha256,
        "evaluation_config_sha256": evaluation_config_sha256,
        "phase": split_name,
        **candidate_metadata,
        "candidate_selection_reason": candidate_selection_reason,
        "agent_policy_snapshot": {
            "gate": agent["gate"],
            "fusion": agent["fusion"],
            "indicator": agent["indicator"],
            "pacing": agent["pacing"],
        },
        "evaluation_policy_snapshot": {
            "candidate_budget": evaluation["candidate_budget"],
            "tune_gate": evaluation["tune_gate"],
            "regression_gate": evaluation["regression_gate"],
        },
    }
    _write_create_once(args.run_root / "candidate-lock.json", candidate_lock)
    terminals = execute_v2_batch(
        identities,
        cases=cases,
        candidate_id=args.candidate_id,
        split=split_name,
        provider_config=provider_config_from_env_file(args.env_file),
        model=str(model["model"]),
        timeout_seconds=float(model["timeout_seconds"]),
        max_completion_tokens=int(model["max_completion_tokens"]),
        indicator_formula=FormulaId.F0,
        indicator_config=load_indicator_config(
            formula_path,
            expected_sha256=str(model["inherited_indicator_config_sha256"]),
        ),
        gate_policy=V2GatePolicy.model_validate(agent["gate"]),
        fusion_policy=DeterministicFusionPolicy.model_validate(agent["fusion"]),
        indicator_policy=StrongSingleIndicatorPolicy.model_validate(agent["indicator"]),
        run_root=args.run_root,
        policy_lock_sha256=str(model["transport_retry_policy_sha256"]),
        minimum_interval_seconds=float(agent["pacing"]["minimum_interval_seconds"]),
        progress=lambda index, total, terminal: print(
            f"{args.phase} {index}/{total} {terminal.status.value}", flush=True
        ),
    )
    aggregate, rows = _aggregate(identities, terminals, baseline)
    aggregate["gate_passed"] = _gate_passed(args.phase, aggregate, evaluation)
    aggregate["gate_disposition"] = _gate_disposition(args.phase, aggregate, evaluation)
    private = {
        "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
        "classification": [
            "CONSUMED_OBSS_DEVELOPMENT_RESULT",
            "NOT_EXTERNAL_VALIDATION",
        ],
        "candidate_id": args.candidate_id,
        "phase": split_name,
        "evaluation_config_sha256": evaluation_config_sha256,
        **candidate_metadata,
        "aggregate": aggregate,
        "outcomes": rows,
    }
    _write_create_once(args.run_root / "development-result.json", private)
    print(
        json.dumps(
            {
                "candidate_id": args.candidate_id,
                "phase": split_name,
                "gate_passed": aggregate["gate_passed"],
                "completed": aggregate["completed"],
                "root": aggregate["root_service_correct"],
                "pair": aggregate["pair_correct"],
                "direct": aggregate["direct_return"],
                "mean_operations": aggregate["mean_semantic_operations"],
                "http_429": aggregate["http_429_terminal_failures"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if aggregate["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
