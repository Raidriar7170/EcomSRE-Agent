from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from ecomsre_rcaeval_adaptive.contracts import (
    AdaptiveTerminalStatus,
    EscalationRoute,
)
from ecomsre_rcaeval_adaptive.evaluation import (
    CandidateMetrics,
    CaseOutcome,
    aggregate_outcomes,
    load_candidate_freeze,
    select_candidate,
    validate_smoke_strata,
)
from ecomsre_rcaeval_v2.schedule import CaseIdentity


def _outcome(
    index: int,
    *,
    baseline: bool,
    initial: bool,
    adaptive: bool,
    escalated: bool,
) -> CaseOutcome:
    return CaseOutcome(
        case_key=f"case-{index}",
        baseline_root_correct=baseline,
        baseline_pair_correct=baseline,
        initial_root_correct=initial,
        adaptive_root_correct=adaptive,
        adaptive_pair_correct=adaptive,
        completed=True,
        terminal_status=AdaptiveTerminalStatus.COMPLETED,
        route=(
            EscalationRoute.ESCALATE_LOGS
            if escalated
            else EscalationRoute.DIRECT_RETURN
        ),
        tool_calls=2,
        semantic_operations=3 if escalated else 1,
        provider_attempts=3 if escalated else 1,
        transport_retries=0,
        known_token_lower_bound=100,
        conservative_token_upper_bound=100,
        latency_ms=10.0,
    )


def test_damage_rescue_and_escalation_metrics_use_paired_raw_counts() -> None:
    aggregate = aggregate_outcomes(
        (
            _outcome(1, baseline=True, initial=True, adaptive=False, escalated=True),
            _outcome(2, baseline=False, initial=False, adaptive=True, escalated=True),
            _outcome(3, baseline=True, initial=True, adaptive=True, escalated=False),
            _outcome(4, baseline=False, initial=False, adaptive=False, escalated=False),
        )
    )

    assert aggregate.damage == 1
    assert aggregate.rescue == 1
    assert aggregate.net_rescue == 0
    assert aggregate.zero_escalation.numerator == 2
    assert aggregate.escalation_precision.numerator == 1
    assert aggregate.escalation_precision.denominator == 2
    assert aggregate.escalation_recall.numerator == 1
    assert aggregate.escalation_recall.denominator == 2


def test_damage_rescue_endpoint_is_root_cause_pair_not_service() -> None:
    outcome = _outcome(
        1, baseline=True, initial=True, adaptive=True, escalated=False
    ).model_copy(
        update={
            "baseline_root_correct": True,
            "baseline_pair_correct": True,
            "adaptive_root_correct": True,
            "adaptive_pair_correct": False,
        }
    )

    aggregate = aggregate_outcomes((outcome,))

    assert aggregate.damage == 1
    assert aggregate.rescue == 0


def test_candidate_selection_uses_required_lexicographic_order() -> None:
    candidate_1 = CandidateMetrics(
        candidate_id="candidate-1",
        minimum_gate_passed=True,
        root_service_correct=52,
        pair_correct=30,
        net_rescue=3,
        damage=2,
        mean_semantic_operations=2.4,
        zero_escalation=30,
    )
    candidate_2 = candidate_1.model_copy(
        update={"candidate_id": "candidate-2", "root_service_correct": 53}
    )
    failed = candidate_1.model_copy(
        update={"candidate_id": "candidate-3", "minimum_gate_passed": False}
    )

    assert select_candidate((candidate_1, candidate_2, failed)) == candidate_2


def test_smoke_strata_require_each_system_fault_pair_exactly_once() -> None:
    faults = ("cpu", "mem", "disk", "delay", "loss", "socket")
    identities = tuple(
        CaseIdentity(
            system=system,
            root_cause_service="checkoutservice",
            fault=fault,
            instance=f"{system.casefold()}-{fault}",
        )
        for system in ("RE2-OB", "RE2-SS")
        for fault in faults
    )
    validate_smoke_strata(identities)

    skewed = identities[:-1] + (
        identities[0].model_copy(update={"instance": "duplicate-stratum"}),
    )
    with pytest.raises(ValueError, match="both systems and all six faults"):
        validate_smoke_strata(skewed)


def test_validation_access_fails_closed_before_freeze_exists(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="candidate freeze"):
        load_candidate_freeze(
            tmp_path / "missing-freeze.json",
            expected_candidate_id="candidate-1",
            config_paths={
                "agent": tmp_path / "agent.json",
                "evaluation": tmp_path / "evaluation.json",
                "model_lock": tmp_path / "model-lock.json",
            },
            repository_root=tmp_path,
        )


def test_candidate_freeze_binds_all_agent_config_hashes(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q"), cwd=tmp_path, check=True)
    config_paths = {
        "agent": tmp_path / "config/rcaeval-adaptive-v1/agent.json",
        "evaluation": tmp_path / "config/rcaeval-adaptive-v1/evaluation.json",
        "model_lock": tmp_path / "config/rcaeval-adaptive-v1/model-lock.json",
    }
    for path in config_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for name, path in config_paths.items():
        path.write_text(json.dumps({"name": name}) + "\n", encoding="utf-8")
    runtime_path = tmp_path / "src/ecomsre_rcaeval_adaptive/runtime.py"
    script_path = tmp_path / "scripts/rcaeval_adaptive/run.py"
    runtime_path.parent.mkdir(parents=True)
    script_path.parent.mkdir(parents=True)
    runtime_path.write_text("VALUE = 1\n", encoding="utf-8")
    script_path.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=tmp_path, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Adaptive Test",
            "-c",
            "user.email=adaptive@example.invalid",
            "commit",
            "-qm",
            "implementation",
        ),
        cwd=tmp_path,
        check=True,
    )
    implementation_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in config_paths.items()
    }
    freeze = tmp_path / "config/rcaeval-adaptive-v1/adaptive-candidate.json"
    freeze.write_text(
        json.dumps(
            {
                "schema_version": "rcaeval-single-first-adaptive.candidate-freeze.v1",
                "evaluation_version": "single-first-adaptive-v1",
                "implementation_git_sha": implementation_sha,
                "candidate_id": "candidate-1",
                "config_sha256": hashes,
                "model": "gpt-5.4-mini-2026-03-17",
                "design_metrics": {
                    "candidate_id": "candidate-1",
                    "minimum_gate_passed": True,
                    "root_service_correct": 52,
                    "pair_correct": 30,
                    "net_rescue": 2,
                    "damage": 2,
                    "mean_semantic_operations": 2.5,
                    "zero_escalation": 30,
                },
                "validation_split_id": "DEV_VALIDATION",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "add", str(freeze)), cwd=tmp_path, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Adaptive Test",
            "-c",
            "user.email=adaptive@example.invalid",
            "commit",
            "-qm",
            "freeze",
        ),
        cwd=tmp_path,
        check=True,
    )

    loaded = load_candidate_freeze(
        freeze,
        expected_candidate_id="candidate-1",
        config_paths=config_paths,
        repository_root=tmp_path,
    )

    assert loaded.config_sha256 == hashes

    original_freeze = freeze.read_text(encoding="utf-8")
    freeze.write_text(original_freeze + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tracked HEAD blob"):
        load_candidate_freeze(
            freeze,
            expected_candidate_id="candidate-1",
            config_paths=config_paths,
            repository_root=tmp_path,
        )
    freeze.write_text(original_freeze, encoding="utf-8")

    runtime_path.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="runtime differs"):
        load_candidate_freeze(
            freeze,
            expected_candidate_id="candidate-1",
            config_paths=config_paths,
            repository_root=tmp_path,
        )
