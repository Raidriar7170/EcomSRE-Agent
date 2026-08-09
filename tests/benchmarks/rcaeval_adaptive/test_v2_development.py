from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from ecomsre_rcaeval_adaptive.contracts import AdaptiveTerminalStatus
from ecomsre_rcaeval_adaptive.evaluation import BaselineOutcome
from ecomsre_rcaeval_adaptive.v2_runner import AdaptiveV2TerminalRecord
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptAccountingSummary
from ecomsre_rcaeval_v2.schedule import CaseIdentity

_SCRIPT_PATH = Path(__file__).parents[3] / "scripts/rcaeval_adaptive/run_v2_development.py"
_SPEC = importlib.util.spec_from_file_location("test_run_v2_development", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
PROJECT_ROOT = _MODULE.PROJECT_ROOT
_aggregate = _MODULE._aggregate
_gate_disposition = _MODULE._gate_disposition
_validate_private_run_root = _MODULE._validate_private_run_root
_validate_regression_authorization = _MODULE._validate_regression_authorization
_validate_tune_lineage = _MODULE._validate_tune_lineage
main = _MODULE.main


def _identity(instance: str, *, fault: str = "cpu") -> CaseIdentity:
    return CaseIdentity.model_validate(
        {
            "system": "RE2-OB",
            "root_cause_service": "checkoutservice",
            "fault": fault,
            "instance": instance,
        }
    )


def _failed_terminal(
    ordinal: int, failure_code: str
) -> AdaptiveV2TerminalRecord:
    now = datetime.now(timezone.utc)
    return AdaptiveV2TerminalRecord(
        schema_version="rcaeval-single-first-adaptive.terminal.v2",
        evaluation_version="single-first-adaptive-v2",
        candidate_id="candidate-1",
        split="TUNE_SET",
        run_id=f"{ordinal:032x}",
        case_id=f"case-{ordinal}",
        system="RE2-OB",
        status=AdaptiveTerminalStatus.PROVIDER_FAILURE,
        result=None,
        failure_class="ALLOWLISTED_TRANSPORT_TRANSIENT",
        failure_code=failure_code,
        failure_stage="PROVIDER_CALL",
        started_at_utc=now,
        ended_at_utc=now,
        latency_ms=10.0,
        attempt_accounting=AttemptAccountingSummary(
            provider_attempt_count=2,
            retry_attempt_count=1,
            known_token_lower_bound=0,
            unknown_attempt_count=2,
            unknown_reserved_tokens=64000,
            conservative_token_upper_bound=64000,
            orphan_attempt_count=0,
            completed_attempt_usage_coverage_numerator=0,
            completed_attempt_usage_coverage_denominator=0,
            failed_attempt_disposition_coverage_numerator=2,
            failed_attempt_disposition_coverage_denominator=2,
        ),
        policy_lock_sha256="a" * 64,
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_capacity_failures_are_separate_from_algorithm_quality() -> None:
    identities = (_identity("1"), _identity("2", fault="mem"))
    baseline = {
        identities[0]: BaselineOutcome(
            identity=identities[0], root_correct=True, pair_correct=True
        ),
        identities[1]: BaselineOutcome(
            identity=identities[1], root_correct=False, pair_correct=False
        ),
    }

    aggregate, _ = _aggregate(
        identities,
        (_failed_terminal(1, "HTTP_429"), _failed_terminal(2, "TLS_TRANSIENT")),
        baseline,
    )

    assert aggregate["completion_coverage"] == {
        "numerator": 0,
        "denominator": 2,
        "value": 0.0,
    }
    assert aggregate["provider_failure_count"] == 2
    assert aggregate["provider_failure_code_distribution"] == {
        "HTTP_429": 1,
        "TLS_TRANSIENT": 1,
    }
    assert aggregate["algorithm_quality_evaluable"] is False
    assert aggregate["completed_only_root_service_accuracy"]["value"] is None
    assert aggregate["completed_only_pair_accuracy"]["value"] is None
    assert aggregate["mean_semantic_operations_completed_only"] is None
    assert _gate_disposition("tune", aggregate) == "PROVIDER_CAPACITY_BLOCKED"


def test_private_run_root_rejects_git_and_symlink_targets(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside Git"):
        _validate_private_run_root(PROJECT_ROOT / "private-output")

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _validate_private_run_root(linked)

    assert _validate_private_run_root(tmp_path / "private-output") == (
        tmp_path / "private-output"
    ).resolve()


def test_failed_tune_cannot_reach_regression_schedule_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tune_root = tmp_path / "tune"
    tune_result = tune_root / "development-result.json"
    agent_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/agent.json"
    model_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/model-lock.json"
    implementation_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(_MODULE, "_clean_implementation_sha", lambda: implementation_sha)
    _write_json(
        tune_root / "candidate-lock.json",
        {
            "schema_version": "rcaeval-single-first-adaptive.candidate-lock.v2",
            "candidate_id": "candidate-1",
            "implementation_git_sha": implementation_sha,
            "agent_config_sha256": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
            "model_lock_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "phase": "TUNE_SET",
        },
    )
    _write_json(
        tune_result,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {
                "gate_passed": False,
                "gate_disposition": "PROVIDER_CAPACITY_BLOCKED",
                "algorithm_quality_evaluable": False,
            },
        },
    )

    with pytest.raises(ValueError, match="passed TUNE result"):
        main(
            (
                "--phase",
                "regression",
                "--candidate-id",
                "candidate-1",
                "--ob-root",
                str(tmp_path / "missing-ob"),
                "--ss-root",
                str(tmp_path / "missing-ss"),
                "--schedule",
                str(tmp_path / "missing-schedule.json"),
                "--env-file",
                str(tmp_path / "missing.env"),
                "--run-root",
                str(tmp_path / "regression"),
                "--reference-terminal-root",
                str(tmp_path / "missing-reference"),
                "--tune-result",
                str(tune_result),
            )
        )


def test_tune_lineage_is_bounded_and_ordered(tmp_path: Path) -> None:
    first = tmp_path / "candidate-1" / "development-result.json"
    _write_json(
        first,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {"scheduled": 60, "gate_passed": False},
        },
    )

    assert _validate_tune_lineage("candidate-2", (first,)) == ("candidate-1",)
    with pytest.raises(ValueError, match="lineage"):
        _validate_tune_lineage("candidate-2", ())
    with pytest.raises(ValueError, match="lineage"):
        _validate_tune_lineage("candidate-1", (first,))
    with pytest.raises(ValueError, match="lineage"):
        _validate_tune_lineage("1", ())

    passed = tmp_path / "passed" / "development-result.json"
    _write_json(
        passed,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {"scheduled": 60, "gate_passed": True},
        },
    )
    with pytest.raises(ValueError, match="already passed"):
        _validate_tune_lineage("candidate-2", (passed,))


def test_passed_tune_binding_authorizes_same_runtime(tmp_path: Path) -> None:
    tune_root = tmp_path / "tune"
    tune_result = tune_root / "development-result.json"
    agent_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/agent.json"
    model_path = PROJECT_ROOT / "config/rcaeval-adaptive-v2/model-lock.json"
    implementation_sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _write_json(
        tune_root / "candidate-lock.json",
        {
            "schema_version": "rcaeval-single-first-adaptive.candidate-lock.v2",
            "candidate_id": "candidate-1",
            "implementation_git_sha": implementation_sha,
            "agent_config_sha256": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
            "model_lock_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "phase": "TUNE_SET",
        },
    )
    _write_json(
        tune_result,
        {
            "schema_version": "rcaeval-single-first-adaptive.development-result.v2",
            "candidate_id": "candidate-1",
            "phase": "TUNE_SET",
            "aggregate": {
                "scheduled": 60,
                "completed": 60,
                "algorithm_quality_evaluable": True,
                "root_service_correct": 51,
                "pair_correct": 29,
                "damage": 0,
                "damage_rate": {"numerator": 0, "denominator": 29, "value": 0.0},
                "rescue": 0,
                "wrong_overrides": 0,
                "correct_overrides": 0,
                "disqualifying_failure_count": 0,
                "direct_return": 36,
                "mean_semantic_operations": 1.8,
                "trace_routes": 12,
                "gate_passed": True,
                "gate_disposition": "PASSED",
            },
        },
    )

    _validate_regression_authorization(
        candidate_id="candidate-1",
        tune_result_path=tune_result,
        current_implementation_sha=implementation_sha,
        agent_config_sha256=hashlib.sha256(agent_path.read_bytes()).hexdigest(),
        model_lock_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
    )
