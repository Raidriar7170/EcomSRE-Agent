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
    "src/ecomsre_rcaeval_adaptive/v2.py",
    "src/ecomsre_rcaeval_adaptive/v2_runner.py",
    "scripts/rcaeval_adaptive/run_v2_development.py",
    "config/rcaeval-adaptive-v2",
)


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
        raise ValueError("Adaptive v2 runtime must be committed before Provider execution")
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
        raise ValueError("Adaptive v2 prior result must be an absolute non-symlink file")
    resolved = requested.resolve(strict=True)
    _validate_private_run_root(resolved.parent)
    return _load(resolved)


def _validate_tune_lineage(
    candidate_id: str, previous_results: tuple[Path, ...]
) -> tuple[str, ...]:
    if candidate_id not in {"candidate-1", "candidate-2", "candidate-3"}:
        raise ValueError("Adaptive v2 candidate lineage is invalid")
    ordinal = int(candidate_id[-1])
    expected = tuple(f"candidate-{index}" for index in range(1, ordinal))
    if ordinal not in {1, 2, 3} or len(previous_results) != len(expected):
        raise ValueError("Adaptive v2 candidate lineage is incomplete or outside the limit")
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
        or not _gate_passed("tune", aggregate)
    ):
        raise ValueError("Adaptive v2 regression requires a passed TUNE result")
    lock = _load_private_result(tune_result_path.parent / "candidate-lock.json")
    implementation_sha = lock.get("implementation_git_sha")
    if (
        lock.get("schema_version")
        != "rcaeval-single-first-adaptive.candidate-lock.v2"
        or lock.get("candidate_id") != candidate_id
        or lock.get("phase") != "TUNE_SET"
        or lock.get("agent_config_sha256") != agent_config_sha256
        or lock.get("model_lock_sha256") != model_lock_sha256
        or not isinstance(implementation_sha, str)
        or len(implementation_sha) != 40
        or not _git_success("merge-base", "--is-ancestor", implementation_sha, current_implementation_sha)
        or not _git_success("diff", "--quiet", implementation_sha, current_implementation_sha, "--", *_RUNTIME_SCOPES)
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
        root_correct = terminal.diagnosis.root_cause_service == identity.root_cause_service
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
    provider_failure_codes: Counter[str] = Counter()
    for ordinal, (identity, terminal) in enumerate(
        zip(identities, terminals, strict=True), start=1
    ):
        reference = baseline[identity]
        completed = terminal.status is AdaptiveTerminalStatus.COMPLETED
        result = terminal.result
        initial_correct = False
        root_correct = False
        pair_correct = False
        route = None
        semantic_operations = 0
        correct_override = False
        wrong_override = False
        if terminal.status is AdaptiveTerminalStatus.PROVIDER_FAILURE:
            provider_failure_codes[terminal.failure_code or "UNKNOWN_PROVIDER_FAILURE"] += 1
        if result is not None:
            diagnosis = result.diagnosis
            initial_correct = (
                diagnosis.initial_diagnosis.root_cause_service
                == identity.root_cause_service
            )
            root_correct = diagnosis.final_root_service == identity.root_cause_service
            pair_correct = root_correct and diagnosis.final_indicator == normalize_indicator(
                identity.fault
            )
            route = diagnosis.gate_decision.route.value
            routes[route] += 1
            semantic_operations = result.semantic_operations
            fusion_actions[diagnosis.fusion_decision.action] += 1
            indicator_actions[diagnosis.indicator_resolution.action.value] += 1
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
            }
        )
    scheduled = len(rows)
    completed_count = sum(item["completed"] for item in rows)
    completed_rows = tuple(item for item in rows if item["completed"])
    root_correct_count = sum(item["root_correct"] for item in rows)
    pair_correct_count = sum(item["pair_correct"] for item in rows)
    baseline_pair_correct = sum(item["baseline_pair_correct"] for item in rows)
    baseline_pair_wrong = scheduled - baseline_pair_correct
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
        "damage": damage,
        "damage_rate": _rate(damage, baseline_pair_correct),
        "rescue": rescue,
        "rescue_rate": _rate(rescue, baseline_pair_wrong),
        "net_rescue": rescue - damage,
        "direct_return": routes[AdaptiveV2Route.DIRECT_RETURN.value],
        "route_distribution": {
            route.value: routes[route.value] for route in AdaptiveV2Route
        },
        "trace_routes": (
            routes[AdaptiveV2Route.VERIFY_TRACES.value]
            + routes[AdaptiveV2Route.VERIFY_BOTH.value]
        ),
        "mean_semantic_operations": sum(
            item["semantic_operations"] for item in rows
        )
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
        "indicator_action_distribution": dict(sorted(indicator_actions.items())),
        "http_429_terminal_failures": sum(
            item["failure_code"] == "HTTP_429" for item in rows
        ),
        "provider_failure_count": sum(provider_failure_codes.values()),
        "provider_failure_code_distribution": dict(sorted(provider_failure_codes.items())),
        "disqualifying_failure_count": disqualifying,
    }
    return aggregate, rows


def _gate_passed(phase: str, aggregate: Mapping[str, Any]) -> bool:
    damage_rate = aggregate["damage_rate"]["value"]
    shared = (
        aggregate["damage"] <= aggregate["rescue"]
        and aggregate["wrong_overrides"] <= aggregate["correct_overrides"]
        and aggregate["disqualifying_failure_count"] == 0
    )
    if phase == "tune":
        return bool(
            shared
            and aggregate["completed"] >= 58
            and aggregate["root_service_correct"] >= 51
            and aggregate["pair_correct"] >= 29
            and damage_rate is not None
            and damage_rate <= 0.05
            and aggregate["direct_return"] >= 36
            and aggregate["mean_semantic_operations"] <= 1.8
            and aggregate["trace_routes"] <= 12
        )
    return bool(
        shared
        and aggregate["completed"] >= 114
        and aggregate["root_service_correct"] >= 97
        and aggregate["pair_correct"] >= 53
        and aggregate["net_rescue"] >= 0
        and aggregate["direct_return"] >= 72
        and aggregate["mean_semantic_operations"] <= 1.8
        and aggregate["trace_routes"] <= 24
        and aggregate["http_429_terminal_failures"] <= 6
    )


def _gate_disposition(phase: str, aggregate: Mapping[str, Any]) -> str:
    if _gate_passed(phase, aggregate):
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
    parser.add_argument("--previous-tune-result", action="append", default=[], type=Path)
    parser.add_argument("--tune-result", type=Path)
    args = parser.parse_args(argv)

    args.run_root = _validate_private_run_root(args.run_root)
    implementation_sha = _clean_implementation_sha()
    agent = _load(CONFIG_ROOT / "agent.json")
    model = _load(CONFIG_ROOT / "model-lock.json")
    agent_config_sha256 = _sha(CONFIG_ROOT / "agent.json")
    model_lock_sha256 = _sha(CONFIG_ROOT / "model-lock.json")
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
        baseline = _regression_baseline(
            identities, cases, args.reference_terminal_root
        )
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
        "phase": split_name,
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
    aggregate["gate_passed"] = _gate_passed(args.phase, aggregate)
    aggregate["gate_disposition"] = _gate_disposition(args.phase, aggregate)
    private = {
        "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
        "classification": [
            "CONSUMED_OBSS_DEVELOPMENT_RESULT",
            "NOT_EXTERNAL_VALIDATION",
        ],
        "candidate_id": args.candidate_id,
        "phase": split_name,
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
