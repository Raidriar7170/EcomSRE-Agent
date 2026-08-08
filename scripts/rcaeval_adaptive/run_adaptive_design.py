"""Run and score one bounded 60-case Adaptive DESIGN candidate."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from ecomsre_rcaeval_adaptive.contracts import EscalationRoute
from ecomsre_rcaeval_adaptive.evaluation import (
    CandidateMetrics,
    aggregate_outcomes,
    load_design_baseline,
    score_adaptive_terminals,
)
from ecomsre_rcaeval_adaptive.gate import GatePolicy
from ecomsre_rcaeval_adaptive.indicator import IndicatorPolicy
from ecomsre_rcaeval_adaptive.runner import execute_adaptive_batch
from ecomsre_rcaeval_v2.dev3_evidence import verify_provider_sidecar
from ecomsre_rcaeval_v2.dev3_execution import (
    discover_case_index,
    load_private_schedule,
)
from ecomsre_rcaeval_v2.dev3_schedule import Variant
from ecomsre_rcaeval_v2.dev_execution import provider_config_from_env_file
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.public_projection import (
    assert_public_payload,
    write_private_json_create_once,
)
from ecomsre_rcaeval_v2.schedule import SplitName


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config/rcaeval-adaptive-v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("adaptive JSON root must be an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--design-schedule", required=True, type=Path)
    parser.add_argument("--baseline-outcomes", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    args = parser.parse_args(argv)

    smoke_gate = _load(args.run_root / "evidence/smoke-gate.json")
    if smoke_gate.get("candidate_id") != args.candidate_id or smoke_gate.get("passed") is not True:
        raise ValueError("adaptive DESIGN requires a passing same-candidate smoke")
    agent_path = CONFIG_ROOT / "agent.json"
    agent = _load(agent_path)
    model_path = CONFIG_ROOT / "model-lock.json"
    model = _load(model_path)
    evaluation_path = CONFIG_ROOT / "evaluation.json"
    evaluation = _load(evaluation_path)
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
        args.design_schedule, allowed_split=SplitName.DESIGN
    )
    identities = tuple(
        record.identity
        for record in schedule
        if record.variant is Variant.SINGLE_V1_REFERENCE
    )
    if len(identities) != 60 or len(set(identities)) != 60:
        raise ValueError("adaptive DESIGN must contain 60 unique cases")
    cases = discover_case_index(args.ob_root, args.ss_root, set(identities))
    raw_budgets = evaluation["phase_budgets"]
    if not isinstance(raw_budgets, dict) or not isinstance(
        raw_budgets.get("design"), dict
    ):
        raise ValueError("adaptive DESIGN budget config is invalid")
    budget = raw_budgets["design"]
    terminals = execute_adaptive_batch(
        identities,
        cases=cases,
        candidate_id=args.candidate_id,
        split="DESIGN",
        provider_config=provider_config_from_env_file(args.env_file),
        model=str(model["model"]),
        timeout_seconds=float(model["timeout_seconds"]),
        max_completion_tokens=int(model["max_completion_tokens"]),
        indicator_formula=FormulaId.F0,
        indicator_config=formula,
        gate_policy=GatePolicy.model_validate(agent["gate"]),
        indicator_policy=IndicatorPolicy(
            deterministic_margin_threshold=float(
                agent["indicator_resolver"]["deterministic_margin_threshold"]
            )
        ),
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
        sidecar = args.run_root / "provider-sidecars" / terminal.run_id
        verify_provider_sidecar(
            sidecar,
            expected_semantic_operations=len(
                tuple((sidecar / "semantic-operations").glob("*.json"))
            ),
            expected_policy_lock_sha256=policy_sha,
        )
    baseline = load_design_baseline(args.baseline_outcomes)
    outcomes = score_adaptive_terminals(
        identities, terminals, baseline=baseline
    )
    aggregate = aggregate_outcomes(outcomes)
    routes = Counter(item.route for item in outcomes)
    target = evaluation["design_target"]
    target_met = (
        aggregate.root_service_correct >= int(target["root_service_correct_min"])
        and aggregate.pair_correct >= int(target["pair_correct_min"])
        and aggregate.damage <= int(target["damage_max"])
        and aggregate.rescue >= int(target["rescue_min"])
        and aggregate.net_rescue >= int(target["net_rescue_min"])
        and routes[EscalationRoute.DIRECT_RETURN] >= int(target["direct_return_min"])
        and aggregate.mean_semantic_operations
        <= float(target["mean_semantic_operations_max"])
        and (
            routes[EscalationRoute.ESCALATE_TRACES]
            + routes[EscalationRoute.ESCALATE_BOTH]
        )
        / 60
        <= float(target["trace_route_rate_max"])
    )
    candidate = CandidateMetrics(
        candidate_id=args.candidate_id,
        minimum_gate_passed=aggregate.minimum_gate_passed,
        root_service_correct=aggregate.root_service_correct,
        pair_correct=aggregate.pair_correct,
        net_rescue=aggregate.net_rescue,
        damage=aggregate.damage,
        mean_semantic_operations=aggregate.mean_semantic_operations,
        zero_escalation=aggregate.zero_escalation.numerator,
    )
    status_counts = Counter(item.status.value for item in terminals)
    public_aggregate = {
        "schema_version": "rcaeval-single-first-adaptive.design-aggregate.v1",
        "evaluation_version": "single-first-adaptive-v1",
        "claim_boundary": "DEVELOPMENT_VISIBLE_DESIGN_NOT_EXTERNAL_HOLDOUT",
        "candidate_id": args.candidate_id,
        "config_sha256": {
            "agent": _sha(agent_path),
            "evaluation": _sha(evaluation_path),
            "model_lock": _sha(model_path),
        },
        "baseline": {
            "variant": "single_v1_reference",
            "root_service_correct": sum(item.root_correct for item in baseline.values()),
            "pair_correct": sum(item.pair_correct for item in baseline.values()),
        },
        "adaptive": aggregate.model_dump(mode="json"),
        "terminal_status_counts": dict(sorted(status_counts.items())),
        "target_met": target_met,
        "candidate_metrics": candidate.model_dump(mode="json"),
    }
    assert_public_payload(public_aggregate)
    write_private_json_create_once(
        args.run_root / "evidence/design-outcomes.json",
        {
            "schema_version": "rcaeval-single-first-adaptive.private-outcomes.v1",
            "outcomes": [item.model_dump(mode="json") for item in outcomes],
        },
    )
    write_private_json_create_once(
        args.run_root / "evidence/design-aggregate.json", public_aggregate
    )
    print(json.dumps(public_aggregate, sort_keys=True))
    return 0 if aggregate.minimum_gate_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
