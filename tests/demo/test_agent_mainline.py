"""Focused proof for the real Phase 2 -> Phase 3 demo chain."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ecomsre.demo.runtime as demo_runtime
import ecomsre.phase3.workflow as phase3_workflow
from ecomsre.phase1.contracts import FaultMechanism, RCADecision
from ecomsre.phase2.contracts import Phase2Variant
from ecomsre.phase2.provider import OpenAICompatiblePhase2Backend
from ecomsre.phase2.scripted import ScriptedModelBackend
from ecomsre.phase3.contracts import (
    ActionType,
    ApprovalMode,
    PolicyOutcome,
    TerminalOutcome,
    VerificationOutcome,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_demo_runs_the_real_dynamic_diagnosis_and_remediation_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {
        "loader": 0,
        "phase2": 0,
        "handoff": 0,
        "planner": 0,
        "policy": 0,
        "phase3": 0,
    }
    real_loader = demo_runtime.load_replay_case
    real_phase2 = demo_runtime.run_replay_workflow
    real_handoff = demo_runtime.build_diagnosis_handoff
    real_planner = demo_runtime.plan_remediation
    real_phase3 = demo_runtime.run_remediation_replay
    real_policy = phase3_workflow.evaluate_policy

    def load_case(allowed_root: Path, case_id: str):  # type: ignore[no-untyped-def]
        calls["loader"] += 1
        assert allowed_root == (
            PROJECT_ROOT / "config/phase1/replay-cases/agent-visible"
        )
        assert "ground-truth" not in allowed_root.as_posix()
        return real_loader(allowed_root, case_id)

    def run_phase2(**kwargs):  # type: ignore[no-untyped-def]
        calls["phase2"] += 1
        assert kwargs["variant"] is Phase2Variant.DYNAMIC_MULTI_AGENT
        assert kwargs["allow_refinement"] is True
        assert isinstance(kwargs["model_backend"], ScriptedModelBackend)
        assert kwargs["model_backend"].evidence_confirmation_enabled is True
        return real_phase2(**kwargs)

    def handoff(**kwargs):  # type: ignore[no-untyped-def]
        calls["handoff"] += 1
        return real_handoff(**kwargs)

    def planner(**kwargs):  # type: ignore[no-untyped-def]
        calls["planner"] += 1
        return real_planner(**kwargs)

    def policy(**kwargs):  # type: ignore[no-untyped-def]
        calls["policy"] += 1
        return real_policy(**kwargs)

    def run_phase3(**kwargs):  # type: ignore[no-untyped-def]
        calls["phase3"] += 1
        return real_phase3(**kwargs)

    monkeypatch.setattr(demo_runtime, "load_replay_case", load_case)
    monkeypatch.setattr(demo_runtime, "run_replay_workflow", run_phase2)
    monkeypatch.setattr(demo_runtime, "build_diagnosis_handoff", handoff)
    monkeypatch.setattr(demo_runtime, "plan_remediation", planner)
    monkeypatch.setattr(phase3_workflow, "evaluate_policy", policy)
    monkeypatch.setattr(demo_runtime, "run_remediation_replay", run_phase3)

    report = demo_runtime.run_agent_mainline_demo(PROJECT_ROOT)

    assert calls == {
        "loader": 1,
        "phase2": 1,
        "handoff": 1,
        "planner": 1,
        "policy": 1,
        "phase3": 1,
    }
    assert report.case == "ad-partial-failure-complete"
    assert report.diagnosis.variant is Phase2Variant.DYNAMIC_MULTI_AGENT
    assert report.diagnosis.backend == "SCRIPTED_REPLAY"
    assert report.diagnosis.decision is RCADecision.RCA_CONFIRMED
    assert report.diagnosis.root_service == "ad"
    assert (
        report.diagnosis.fault_mechanism is FaultMechanism.RUNTIME_CONFIGURATION_FAILURE
    )
    assert report.diagnosis.supporting_evidence_count >= 2
    assert (
        report.remediation.selected_action
        is ActionType.RESTORE_FROZEN_SERVICE_CONFIGURATION
    )
    assert report.remediation.policy_decision is PolicyOutcome.ALLOW
    assert report.remediation.approval_mode is ApprovalMode.LOCAL_TEST_AUTO_APPROVAL
    assert report.remediation.forward_mutation_count == 1
    assert report.remediation.verification_result is VerificationOutcome.VERIFIED
    assert report.remediation.rollback_count == 0
    assert report.remediation.terminal_status is TerminalOutcome.REMEDIATION_VERIFIED
    assert report.execution_boundary.provider_called is False
    assert report.execution_boundary.docker_called is False
    assert report.execution_boundary.live_execution is False
    assert report.execution_boundary.evaluator_truth_read is False


def test_demo_report_is_exactly_deterministic_and_digest_bound() -> None:
    first = demo_runtime.run_agent_mainline_demo(PROJECT_ROOT)
    second = demo_runtime.run_agent_mainline_demo(PROJECT_ROOT)

    assert first == second
    assert len(first.semantic_sha256) == 64
    assert (
        demo_runtime.AgentMainlineReport.model_validate_json(
            demo_runtime.canonical_report_bytes(first)
        )
        == first
    )


def test_demo_cannot_read_evaluator_only_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        candidate = path.resolve(strict=False)
        assert "ground-truth" not in candidate.parts
        assert PROJECT_ROOT / "eval" not in candidate.parents
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    report = demo_runtime.run_agent_mainline_demo(PROJECT_ROOT)

    assert report.execution_boundary.evaluator_truth_read is False


def test_demo_denies_provider_and_subprocess_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_boundary(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise AssertionError("offline demo crossed a denied execution boundary")

    monkeypatch.setattr(OpenAICompatiblePhase2Backend, "complete", deny_boundary)
    monkeypatch.setattr(subprocess, "run", deny_boundary)

    report = demo_runtime.run_agent_mainline_demo(PROJECT_ROOT)

    assert report.execution_boundary.provider_called is False
    assert report.execution_boundary.docker_called is False
