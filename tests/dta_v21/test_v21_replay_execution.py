from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    ActionParameterV21,
    ActionProposalV21,
    ExecutionBackendV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.registry import (
    RunbookRegistryV21,
    load_default_runbook_registry,
)
from ecomsre.dta_v2.v21.replay import build_replay_diagnosis, resolve_replay_case
from ecomsre.dta_v2.v21.replay_execution import (
    FixedReplayExecutorV21,
    FixedReplayVerifierV21,
    ReplayExecutionReceiptV21,
    admit_runbook_backend,
    execute_and_verify_replay_only,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("run_id", "root", "domain", "mechanism", "sources", "expected"),
    [
        (
            "8" * 32,
            "ad",
            FaultDomainV21.LOCAL_RESOURCE,
            FaultMechanismV21.CPU_SATURATION,
            ("METRICS", "RUNTIME", "RESOURCES"),
            RunbookIdV21.MITIGATE_CPU_SATURATION,
        ),
        (
            "9" * 32,
            "email",
            FaultDomainV21.SERVICE_RUNTIME,
            FaultMechanismV21.SERVICE_UNAVAILABLE,
            ("METRICS", "RUNTIME"),
            RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
        ),
        (
            "a" * 32,
            "product-catalog",
            FaultDomainV21.SERVICE_RUNTIME,
            FaultMechanismV21.SERVICE_UNAVAILABLE,
            ("TRACES", "RUNTIME"),
            RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
        ),
        (
            "b" * 32,
            "shipping",
            FaultDomainV21.DEPENDENCY,
            FaultMechanismV21.DEPENDENCY_LATENCY,
            ("METRICS", "TRACES"),
            RunbookIdV21.RESTORE_DEPENDENCY_LATENCY,
        ),
        (
            "c" * 32,
            "email",
            FaultDomainV21.LOCAL_RESOURCE,
            FaultMechanismV21.MEMORY_LEAK,
            ("METRICS", "RUNTIME", "RESOURCES"),
            RunbookIdV21.MITIGATE_MEMORY_LEAK,
        ),
    ],
)
def test_required_evidence_diagnosis_to_runbook_replays(
    run_id: str,
    root: str,
    domain: FaultDomainV21,
    mechanism: FaultMechanismV21,
    sources: tuple[str, ...],
    expected: RunbookIdV21,
) -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id=run_id,
        terminal=TerminalV21.COMPLETED,
        root_service=root,
        fault_domain=domain,
        mechanism=mechanism,
        evidence_sources=sources,
    )
    resolution = resolve_replay_case(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=load_default_runbook_registry(REPO_ROOT),
        exact_target=root,
    )

    assert resolution.proposal is not None
    assert resolution.proposal.runbook_id is expected


def test_dependency_latency_is_replay_only_and_live_denied() -> None:
    registry = load_default_runbook_registry(REPO_ROOT)
    diagnosis, evidence = build_replay_diagnosis(
        run_id="4" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="shipping",
        fault_domain=FaultDomainV21.DEPENDENCY,
        mechanism=FaultMechanismV21.DEPENDENCY_LATENCY,
        evidence_sources=("TRACES", "METRICS"),
    )
    resolution = resolve_replay_case(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target="shipping",
    )
    assert resolution.proposal is not None
    assert resolution.proposal.runbook_id is RunbookIdV21.RESTORE_DEPENDENCY_LATENCY

    live = admit_runbook_backend(
        runbook=registry.require(RunbookIdV21.RESTORE_DEPENDENCY_LATENCY),
        requested_backend=ExecutionBackendV21.LIVE,
    )
    assert not live.admitted
    assert live.reason == "REPLAY_ONLY_RUNBOOK_DENIED_FOR_LIVE"

    receipt, verification = execute_and_verify_replay_only(
        proposal=resolution.proposal,
        registry=registry,
    )
    assert receipt.no_live_mutation is True
    assert verification.verified is True


def test_no_fault_completes_with_no_action_and_missing_evidence_stops() -> None:
    registry = load_default_runbook_registry(REPO_ROOT)
    no_fault, no_fault_evidence = build_replay_diagnosis(
        run_id="5" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service=None,
        fault_domain=None,
        mechanism=None,
        evidence_sources=("METRICS", "RUNTIME"),
    )
    no_fault_result = resolve_replay_case(
        diagnosis=no_fault,
        diagnosis_evidence=no_fault_evidence,
        registry=registry,
        exact_target=None,
    )
    assert no_fault_result.proposal is not None
    assert no_fault_result.proposal.disposition is ActionDispositionV21.NO_ACTION

    unresolved, unresolved_evidence = build_replay_diagnosis(
        run_id="6" * 32,
        terminal=TerminalV21.NEED_MORE_EVIDENCE,
        root_service=None,
        fault_domain=None,
        mechanism=None,
        evidence_sources=("METRICS",),
    )
    unresolved_result = resolve_replay_case(
        diagnosis=unresolved,
        diagnosis_evidence=unresolved_evidence,
        registry=registry,
        exact_target=None,
    )
    assert unresolved_result.terminal is TerminalV21.NEED_MORE_EVIDENCE
    assert unresolved_result.proposal is None


def test_unresolved_replay_rejects_run_and_target_drift() -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id="d" * 32,
        terminal=TerminalV21.NEED_MORE_EVIDENCE,
        root_service=None,
        fault_domain=None,
        mechanism=None,
        evidence_sources=("METRICS",),
    )
    _, foreign_evidence = build_replay_diagnosis(
        run_id="e" * 32,
        terminal=TerminalV21.NEED_MORE_EVIDENCE,
        root_service=None,
        fault_domain=None,
        mechanism=None,
        evidence_sources=("METRICS",),
    )
    registry = load_default_runbook_registry(REPO_ROOT)

    with pytest.raises(ValueError, match="another run"):
        resolve_replay_case(
            diagnosis=diagnosis,
            diagnosis_evidence=foreign_evidence,
            registry=registry,
            exact_target=None,
        )
    with pytest.raises(ValueError, match="exact target"):
        resolve_replay_case(
            diagnosis=diagnosis,
            diagnosis_evidence=evidence,
            registry=registry,
            exact_target="shipping",
        )


def _memory_leak_replay() -> tuple[ActionProposalV21, RunbookRegistryV21]:
    registry = load_default_runbook_registry(REPO_ROOT)
    diagnosis, evidence = build_replay_diagnosis(
        run_id="f" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="email",
        fault_domain=FaultDomainV21.LOCAL_RESOURCE,
        mechanism=FaultMechanismV21.MEMORY_LEAK,
        evidence_sources=("METRICS", "RUNTIME", "RESOURCES"),
    )
    resolution = resolve_replay_case(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target="email",
    )
    assert resolution.proposal is not None
    return resolution.proposal, registry


def test_replay_executor_rejects_out_of_range_typed_parameter() -> None:
    proposal, registry = _memory_leak_replay()
    runbook = registry.require(RunbookIdV21.MITIGATE_MEMORY_LEAK)
    python_payload = proposal.model_dump(mode="python", exclude={"proposal_sha256"})
    json_payload = proposal.model_dump(mode="json", exclude={"proposal_sha256"})
    parameter = ActionParameterV21(name="wait_for_health_seconds", value=999)
    python_payload["parameters"] = (parameter,)
    json_payload["parameters"] = [parameter.model_dump(mode="json")]
    tampered = ActionProposalV21.model_validate(
        {
            **python_payload,
            "proposal_sha256": semantic_sha256(json_payload),
        }
    )

    with pytest.raises(ValueError, match="maximum"):
        FixedReplayExecutorV21().execute(proposal=tampered, runbook=runbook)


def test_replay_verifier_rejects_forged_target_and_proposal_drift() -> None:
    proposal, registry = _memory_leak_replay()
    runbook = registry.require(RunbookIdV21.MITIGATE_MEMORY_LEAK)
    receipt = FixedReplayExecutorV21().execute(proposal=proposal, runbook=runbook)
    python_payload = receipt.model_dump(mode="python", exclude={"receipt_sha256"})
    json_payload = receipt.model_dump(mode="json", exclude={"receipt_sha256"})
    python_payload["target_service"] = "product-catalog"
    json_payload["target_service"] = "product-catalog"
    forged = ReplayExecutionReceiptV21.model_validate(
        {**python_payload, "receipt_sha256": semantic_sha256(json_payload)}
    )

    with pytest.raises(ValueError, match="target"):
        FixedReplayVerifierV21().verify(
            receipt=forged,
            proposal=proposal,
            runbook=runbook,
        )

    python_payload = receipt.model_dump(mode="python", exclude={"receipt_sha256"})
    json_payload = receipt.model_dump(mode="json", exclude={"receipt_sha256"})
    python_payload["proposal_sha256"] = "0" * 64
    json_payload["proposal_sha256"] = "0" * 64
    forged_continuity = ReplayExecutionReceiptV21.model_validate(
        {**python_payload, "receipt_sha256": semantic_sha256(json_payload)}
    )
    with pytest.raises(ValueError, match="bound proposal"):
        FixedReplayVerifierV21().verify(
            receipt=forged_continuity,
            proposal=proposal,
            runbook=runbook,
        )
