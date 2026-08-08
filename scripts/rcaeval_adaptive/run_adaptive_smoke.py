"""Run the 12-case create-once Provider smoke for one Adaptive candidate."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from ecomsre_rcaeval_adaptive.contracts import (
    AdaptiveTerminalStatus,
    InitialFailureCode,
)
from ecomsre_rcaeval_adaptive.gate import GatePolicy
from ecomsre_rcaeval_adaptive.indicator import IndicatorPolicy
from ecomsre_rcaeval_adaptive.evaluation import validate_smoke_strata
from ecomsre_rcaeval_adaptive.runner import (
    execute_adaptive_batch,
    require_clean_implementation_git_sha,
)
from ecomsre_rcaeval_v2.dev3_evidence import verify_provider_sidecar
from ecomsre_rcaeval_v2.dev3_execution import (
    discover_case_index,
    load_private_schedule,
)
from ecomsre_rcaeval_v2.dev3_schedule import Variant
from ecomsre_rcaeval_v2.dev_execution import provider_config_from_env_file
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.privacy import scan_agent_visible_payload
from ecomsre_rcaeval_v2.public_projection import write_private_json_create_once
from ecomsre_rcaeval_v2.schedule import SplitName, case_identity_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config/rcaeval-adaptive-v1"


def _load(name: str) -> dict[str, Any]:
    value = json.loads((CONFIG_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("adaptive config root must be an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--smoke-schedule", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.candidate_id != "candidate-1":
        raise ValueError("adaptive shared interface smoke requires candidate-1")

    agent = _load("agent.json")
    model = _load("model-lock.json")
    evaluation = _load("evaluation.json")
    policy_path = PROJECT_ROOT / str(
        model["inherited_transport_retry_policy_path"]
    )
    policy_sha = _sha(policy_path)
    if policy_sha != model["transport_retry_policy_sha256"]:
        raise ValueError("adaptive transport retry policy hash drift")
    formula_path = PROJECT_ROOT / str(model["inherited_indicator_config_path"])
    formula = load_indicator_config(
        formula_path,
        expected_sha256=str(model["inherited_indicator_config_sha256"]),
    )
    schedule = load_private_schedule(
        args.smoke_schedule, allowed_split=SplitName.DESIGN
    )
    identities = tuple(
        record.identity
        for record in schedule
        if record.variant is Variant.SINGLE_V1_REFERENCE
    )
    validate_smoke_strata(identities)
    cases = discover_case_index(args.ob_root, args.ss_root, set(identities))
    budgets = evaluation["phase_budgets"]
    if not isinstance(budgets, dict) or not isinstance(budgets.get("smoke"), dict):
        raise ValueError("adaptive smoke budget config is invalid")
    budget = budgets["smoke"]
    provider_config = provider_config_from_env_file(args.env_file)
    indicator_policy = IndicatorPolicy(
        deterministic_margin_threshold=float(
            agent["indicator_resolver"]["deterministic_margin_threshold"]
        )
    )
    run_domain = str(evaluation["run_domain"])
    terminals = execute_adaptive_batch(
        identities,
        cases=cases,
        candidate_id=args.candidate_id,
        run_domain=run_domain,
        split="DESIGN",
        provider_config=provider_config,
        model=str(model["model"]),
        timeout_seconds=float(model["timeout_seconds"]),
        max_completion_tokens=int(model["max_completion_tokens"]),
        indicator_formula=FormulaId.F0,
        indicator_config=formula,
        gate_policy=GatePolicy.model_validate(agent["gate"]),
        indicator_policy=indicator_policy,
        agent_config=agent,
        implementation_git_sha=require_clean_implementation_git_sha(PROJECT_ROOT),
        run_root=args.run_root,
        policy_lock_sha256=policy_sha,
        max_semantic_operations=int(budget["semantic_operations"]),
        max_provider_attempts=int(budget["provider_attempts"]),
        max_transport_retries=int(budget["transport_retries"]),
        max_conservative_tokens=int(budget["conservative_tokens"]),
        progress=lambda index, total, terminal: print(
            f"{index}/{total} {terminal.status.value}", flush=True
        ),
    )
    for terminal in terminals:
        expected = len(
            tuple(
                (
                    args.run_root
                    / "provider-sidecars"
                    / terminal.run_id
                    / "semantic-operations"
                ).glob("*.json")
            )
        )
        verify_provider_sidecar(
            args.run_root / "provider-sidecars" / terminal.run_id,
            expected_semantic_operations=expected,
            expected_policy_lock_sha256=policy_sha,
        )
    dumped = [item.model_dump(mode="json") for item in terminals]
    path_hits = scan_agent_visible_payload(dumped).path_hit_count
    status_counts = {
        status.value: sum(item.status is status for item in terminals)
        for status in AdaptiveTerminalStatus
    }
    failure_code_counts = Counter(
        item.failure_code for item in terminals if item.failure_code is not None
    )
    initial_failure_count = sum(
        failure_code_counts[code.value] for code in InitialFailureCode
    )
    fusion_overlap_guardrail_count = sum(
        terminal.result is not None
        and any(
            trace.fusion_guardrail_applied
            for trace in terminal.result.operation_trace
        )
        for terminal in terminals
    )
    attempts = sum(item.attempt_accounting.provider_attempt_count for item in terminals)
    retries = sum(item.attempt_accounting.retry_attempt_count for item in terminals)
    upper = sum(
        item.attempt_accounting.conservative_token_upper_bound for item in terminals
    )
    gate = {
        "schema_version": "rcaeval-single-first-adaptive.smoke-gate.v1",
        "evaluation_version": "single-first-adaptive-v1",
        "run_domain": run_domain,
        "candidate_id": args.candidate_id,
        "scheduled": 12,
        "terminalized": len(terminals),
        "status_counts": status_counts,
        "failure_code_counts": dict(sorted(failure_code_counts.items())),
        "fusion_overlap_guardrail_count": fusion_overlap_guardrail_count,
        "semantic_retries": 0,
        "smoke_identity_sha256": sorted(
            hashlib.sha256(case_identity_bytes(identity)).hexdigest()
            for identity in identities
        ),
        "provider_attempts": attempts,
        "transport_retries": retries,
        "conservative_token_upper_bound": upper,
        "private_path_hits": path_hits,
        "passed": (
            len(terminals) == 12
            and status_counts[AdaptiveTerminalStatus.COMPLETED.value] == 12
            and status_counts[AdaptiveTerminalStatus.INVALID_SCHEMA.value] == 0
            and initial_failure_count == 0
            and attempts <= int(budget["provider_attempts"])
            and retries <= int(budget["transport_retries"])
            and upper <= int(budget["conservative_tokens"])
            and path_hits == 0
        ),
    }
    write_private_json_create_once(args.run_root / "evidence/smoke-gate.json", gate)
    print(json.dumps(gate, sort_keys=True))
    return 0 if gate["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
