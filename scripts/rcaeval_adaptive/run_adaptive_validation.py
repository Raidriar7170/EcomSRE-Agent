"""Run the frozen one-shot 120x2 DEV_VALIDATION comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from ecomsre.model.gateway import StdlibOpenAICompatibleTransport
from ecomsre_rcaeval.contracts import (
    Architecture,
    ScheduledRun,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.runner import execute_scheduled_once
from ecomsre_rcaeval_adaptive.evaluation import (
    BaselineOutcome,
    aggregate_outcomes,
    load_candidate_freeze,
    score_adaptive_terminals,
)
from ecomsre_rcaeval_adaptive.gate import GatePolicy
from ecomsre_rcaeval_adaptive.indicator import IndicatorPolicy
from ecomsre_rcaeval_adaptive.runner import execute_adaptive_batch
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.dev3_execution import (
    discover_case_index,
    load_private_schedule,
    new_v1_reference_provider,
    provider_config_from_env_file,
)
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev3ProviderProxy,
    Dev3RetryingTransport,
    seal_interrupted_provider_sidecar,
)
from ecomsre_rcaeval_v2.dev3_schedule import Variant
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptBudget
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.public_projection import (
    assert_public_payload,
    write_private_json_create_once,
)
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    SplitName,
    case_identity_bytes,
)
from ecomsre_rcaeval_v2.statistics import (
    PairedObservation,
    hierarchical_paired_bootstrap,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config/rcaeval-adaptive-v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("adaptive JSON root must be an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_run_id(candidate_id: str, identity: CaseIdentity) -> str:
    return hashlib.sha256(
        b"\0".join(
            (
                b"single-first-adaptive-v1",
                candidate_id.encode(),
                b"DEV_VALIDATION",
                b"strong-single-reference",
                case_identity_bytes(identity),
            )
        )
    ).hexdigest()[:32]


def _run_reference(
    identities: tuple[CaseIdentity, ...],
    *,
    cases,
    candidate_id: str,
    provider_config,
    run_root: Path,
    policy_sha: str,
    timeout_seconds: float,
) -> tuple[TerminalRecord, ...]:
    run_ids = tuple(_reference_run_id(candidate_id, item) for item in identities)
    sidecars = tuple(run_root / "provider-sidecars" / item for item in run_ids)
    budget = AttemptBudget.restore(
        sidecars,
        max_provider_attempts=240,
        max_retry_attempts=24,
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
        max_conservative_tokens=7_680_000,
    )
    output: list[TerminalRecord] = []
    for index, (identity, run_id, sidecar) in enumerate(
        zip(identities, run_ids, sidecars, strict=True), 1
    ):
        case = cases[identity]
        terminal_path = run_root / "terminal-records" / f"{run_id}.json"
        if not terminal_path.exists() and sidecar.exists() and any(sidecar.rglob("*")):
            seal_interrupted_provider_sidecar(
                sidecar,
                policy_lock_sha256=policy_sha,
                expected_timeout_seconds=timeout_seconds,
                fallback_operation_type="FINAL_JUDGE",
            )
            raise RuntimeError("BLOCKED_INTERRUPTED_STRONG_SINGLE_REFERENCE")
        transport = Dev3RetryingTransport(
            StdlibOpenAICompatibleTransport(),
            run_root=sidecar,
            budget=budget,
            policy_lock_sha256=policy_sha,
            expected_timeout_seconds=timeout_seconds,
        )
        provider = Dev3ProviderProxy(
            new_v1_reference_provider(provider_config, transport=transport),
            run_root=sidecar,
            policy_lock_sha256=policy_sha,
        )
        terminal = execute_scheduled_once(
            ScheduledRun(
                run_id=run_id,
                case_id=case.case_id,
                architecture=Architecture.SINGLE,
                call_position=1,
                schedule_seed=20260811,
            ),
            dev_case_to_telemetry_case(case),
            cast(Any, provider),
            run_root / "terminal-records",
        )
        output.append(terminal)
        print(f"reference {index}/{len(identities)} {terminal.terminal_status.value}", flush=True)
    return tuple(output)


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-freeze", required=True, type=Path)
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--validation-schedule", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    args = parser.parse_args(argv)

    config_paths = {
        "agent": CONFIG_ROOT / "agent.json",
        "evaluation": CONFIG_ROOT / "evaluation.json",
        "model_lock": CONFIG_ROOT / "model-lock.json",
    }
    agent = _load(config_paths["agent"])
    evaluation = _load(config_paths["evaluation"])
    model = _load(config_paths["model_lock"])
    # This authorization intentionally precedes any validation schedule/case read.
    load_candidate_freeze(
        args.candidate_freeze,
        expected_candidate_id=args.candidate_id,
        config_paths=config_paths,
        repository_root=PROJECT_ROOT,
    )

    schedule = load_private_schedule(
        args.validation_schedule, allowed_split=SplitName.DEV_VALIDATION
    )
    identities = tuple(
        item.identity
        for item in schedule
        if item.variant is Variant.SINGLE_V1_REFERENCE
    )
    if len(identities) != 120 or len(set(identities)) != 120:
        raise ValueError("adaptive validation requires 120 unique frozen identities")
    cases = discover_case_index(args.ob_root, args.ss_root, set(identities))
    provider_config = provider_config_from_env_file(args.env_file)
    policy_path = PROJECT_ROOT / str(
        model["inherited_transport_retry_policy_path"]
    )
    policy_sha = _sha(policy_path)
    if policy_sha != model["transport_retry_policy_sha256"]:
        raise ValueError("adaptive validation transport policy hash drift")
    reference_root = args.run_root / "strong-single-reference"
    reference = _run_reference(
        identities,
        cases=cases,
        candidate_id=args.candidate_id,
        provider_config=provider_config,
        run_root=reference_root,
        policy_sha=policy_sha,
        timeout_seconds=float(model["timeout_seconds"]),
    )
    baseline = {
        identity: BaselineOutcome(
            identity=identity,
            root_correct=(
                terminal.terminal_status is TerminalStatus.COMPLETED
                and terminal.diagnosis is not None
                and terminal.diagnosis.root_cause_service
                == identity.root_cause_service
            ),
            pair_correct=(
                terminal.terminal_status is TerminalStatus.COMPLETED
                and terminal.diagnosis is not None
                and terminal.diagnosis.root_cause_service
                == identity.root_cause_service
                and terminal.diagnosis.root_cause_indicator
                == {
                    "cpu": "cpu",
                    "mem": "mem",
                    "disk": "diskio",
                    "delay": "latency",
                    "loss": "latency",
                    "socket": "socket",
                }[identity.fault]
            ),
        )
        for identity, terminal in zip(identities, reference, strict=True)
    }
    formula_path = PROJECT_ROOT / str(model["inherited_indicator_config_path"])
    raw_budget = evaluation["phase_budgets"]["validation"]
    adaptive = execute_adaptive_batch(
        identities,
        cases=cases,
        candidate_id=args.candidate_id,
        split="DEV_VALIDATION",
        provider_config=provider_config,
        model=str(model["model"]),
        timeout_seconds=float(model["timeout_seconds"]),
        max_completion_tokens=int(model["max_completion_tokens"]),
        indicator_formula=FormulaId.F0,
        indicator_config=load_indicator_config(
            formula_path,
            expected_sha256=str(model["inherited_indicator_config_sha256"]),
        ),
        gate_policy=GatePolicy.model_validate(agent["gate"]),
        indicator_policy=IndicatorPolicy(
            deterministic_margin_threshold=float(
                agent["indicator_resolver"]["deterministic_margin_threshold"]
            )
        ),
        run_root=args.run_root / "adaptive",
        policy_lock_sha256=policy_sha,
        max_semantic_operations=480,
        max_provider_attempts=960,
        max_transport_retries=96,
        max_conservative_tokens=30_720_000,
        progress=lambda index, total, terminal: print(
            f"adaptive {index}/{total} {terminal.status.value}", flush=True
        ),
    )
    outcomes = score_adaptive_terminals(identities, adaptive, baseline=baseline)
    aggregate = aggregate_outcomes(outcomes)
    root_ci = hierarchical_paired_bootstrap(
        tuple(
            PairedObservation(
                system=identity.system,
                service=identity.root_cause_service,
                fault=identity.fault,
                baseline=float(reference_outcome.root_correct),
                candidate=float(outcome.adaptive_root_correct),
            )
            for identity, reference_outcome, outcome in zip(
                identities, baseline.values(), outcomes, strict=True
            )
        )
    )
    pair_ci = hierarchical_paired_bootstrap(
        tuple(
            PairedObservation(
                system=identity.system,
                service=identity.root_cause_service,
                fault=identity.fault,
                baseline=float(reference_outcome.pair_correct),
                candidate=float(outcome.adaptive_pair_correct),
            )
            for identity, reference_outcome, outcome in zip(
                identities, baseline.values(), outcomes, strict=True
            )
        )
    )
    gate = evaluation["validation_positive_gate"]
    positive = (
        root_ci.point_estimate >= float(gate["root_difference_min"])
        and root_ci.lower_95 >= float(gate["root_difference_ci_lower_min"])
        and pair_ci.point_estimate >= float(gate["pair_difference_min"])
        and aggregate.damage_rate.value <= float(gate["damage_rate_max"])
        and aggregate.rescue > aggregate.damage
        and aggregate.zero_escalation.value >= float(gate["direct_return_rate_min"])
        and aggregate.mean_semantic_operations
        <= float(gate["mean_semantic_operations_max"])
        and aggregate.completed / 120 >= float(gate["completion_rate_min"])
    )
    public = {
        "schema_version": "rcaeval-single-first-adaptive.validation-aggregate.v1",
        "evaluation_version": "single-first-adaptive-v1",
        "candidate_id": args.candidate_id,
        "scheduled_per_variant": 120,
        "variants": 2,
        "reference_root_correct": sum(item.root_correct for item in baseline.values()),
        "reference_pair_correct": sum(item.pair_correct for item in baseline.values()),
        "adaptive": aggregate.model_dump(mode="json"),
        "root_difference": root_ci.__dict__,
        "pair_difference": pair_ci.__dict__,
        "positive_result": positive,
        "budget_semantic_operations": raw_budget["semantic_operations"],
    }
    assert_public_payload(public)
    write_private_json_create_once(args.run_root / "evidence/validation-aggregate.json", public)
    write_private_json_create_once(
        args.run_root / "evidence/validation-outcomes.json",
        {
            "schema_version": "rcaeval-single-first-adaptive.private-outcomes.v1",
            "outcomes": [item.model_dump(mode="json") for item in outcomes],
        },
    )
    print(json.dumps(public, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
