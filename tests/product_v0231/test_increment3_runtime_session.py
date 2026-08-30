from __future__ import annotations

import inspect
from pathlib import Path
import subprocess

from pydantic import ValidationError
import pytest

from ecomsre.product.pilot import nofault_acceptance_v0231
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NOFAULT_CAPABILITY_LIMITED_V023,
    NOFAULT_FULLY_SUPPORTED_V023,
    NOFAULT_NOT_SUPPORTED_V023,
)
from ecomsre.product.pilot.runtime_session_v0231 import (
    RuntimeAuthorityContinuityProofV0231,
    RuntimeContinuationSessionCompletionV0231,
    RuntimeContinuationSessionLedgerV0231,
    RuntimeContinuationSessionStartV0231,
)
from ecomsre.product.pilot.nofault_acceptance_v0231 import (
    NOFAULT_CAPABILITY_LIMITED_V0231,
    NOFAULT_FULLY_SUPPORTED_V0231,
    NOFAULT_NOT_SUPPORTED_V0231,
    NoFaultCampaignV0231,
    NoFaultProfileBindingV0231,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ReadOnlyFlagdBaselineObserverV0231,
)
from ecomsre_live_sandbox.contracts import canonical_json_bytes
from scripts.product_v0231.run_live_authority_restart import (
    _PUBLICATION_OUTPUTS,
    _freeze_publication_bundle,
    main,
    recover_publication_v0231,
)


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
HEAD = "b" * 40


def _start(ordinal: int) -> RuntimeContinuationSessionStartV0231:
    return RuntimeContinuationSessionStartV0231.build(
        session_ordinal=ordinal,
        continuity_descriptor_sha256=SHA,
        execution_head=HEAD,
        private_root_locator=(
            f".local/product-v0231/continuation-sessions/session-{ordinal}"
        ),
        stage="COMPOSE_VERIFIED",
        pre_start_compose_sha256=SHA,
        incident_count_before=0,
        diagnosis_count_before=0,
    )


def _success(
    start: RuntimeContinuationSessionStartV0231,
) -> RuntimeContinuationSessionCompletionV0231:
    return RuntimeContinuationSessionCompletionV0231.build(
        session_ordinal=start.session_ordinal,
        start_sha256=start.start_sha256,
        continuity_descriptor_sha256=SHA,
        execution_head=HEAD,
        private_root_locator=start.private_root_locator,
        stage="CLOSED",
        pre_start_compose_sha256=SHA,
        post_start_read_authority_sha256="1" * 64,
        post_start_pilot_authority_sha256="2" * 64,
        post_start_connector_binding_sha256="3" * 64,
        product_process_launches=(
            {"launch_ordinal": 1},
            {"launch_ordinal": 2},
        ),
        incident_count_before=0,
        incident_count_after=1,
        diagnosis_count_before=0,
        diagnosis_count_after=1,
        cleanup="CLEAN",
        runtime_terminal=("ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS"),
        restart_terminal="ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS",
        nofault_terminal="ECOMSRE_PRODUCT_V0231_NOFAULT_FULLY_SUPPORTED",
        terminal="ECOMSRE_PRODUCT_V0231_NOFAULT_ACCEPTANCE_COMPLETE",
        failure_class=None,
    )


def _failure(
    start: RuntimeContinuationSessionStartV0231,
    failure_class: str,
    *,
    cleanup: str = "CLEAN",
) -> RuntimeContinuationSessionCompletionV0231:
    return RuntimeContinuationSessionCompletionV0231.build(
        session_ordinal=start.session_ordinal,
        start_sha256=start.start_sha256,
        continuity_descriptor_sha256=SHA,
        execution_head=HEAD,
        private_root_locator=start.private_root_locator,
        stage="CLOSED",
        pre_start_compose_sha256=SHA,
        product_process_launches=(),
        incident_count_before=0,
        incident_count_after=0,
        diagnosis_count_before=0,
        diagnosis_count_after=0,
        cleanup=cleanup,
        terminal="BLOCKED_ECOMSRE_PRODUCT_V0231_SESSION_1",
        failure_class=failure_class,
    )


def test_single_successful_session_ledger_is_self_sealed_and_measured() -> None:
    start = _start(1)
    completion = _success(start)
    ledger = RuntimeContinuationSessionLedgerV0231.build(
        starts=(start,), completions=(completion,)
    )

    assert ledger.live_session_count == 1
    assert ledger.accepted_incident_count == 1
    assert ledger.diagnosis_count == 1
    assert completion.cleanup == "CLEAN"


def test_second_session_requires_retryable_preincident_first_failure() -> None:
    first = _start(1)
    second = _start(2)
    hard_failure = _failure(first, "RUNTIME_AUTHORITY_MISMATCH")

    with pytest.raises(ValidationError, match="not retryable"):
        RuntimeContinuationSessionLedgerV0231.build(
            starts=(first, second),
            completions=(hard_failure, _success(second)),
        )

    retryable = _failure(first, "SANDBOX_READINESS_TRANSIENT")
    ledger = RuntimeContinuationSessionLedgerV0231.build(
        starts=(first, second),
        completions=(retryable, _success(second)),
    )
    assert ledger.live_session_count == 2

    unclean = _failure(first, "SANDBOX_READINESS_TRANSIENT", cleanup="BLOCKED")
    with pytest.raises(ValidationError, match="not retryable"):
        RuntimeContinuationSessionLedgerV0231.build(
            starts=(first, second),
            completions=(unclean, _success(second)),
        )


def test_increment3_runner_has_no_fault_mutation_surface() -> None:
    source = (ROOT / "scripts/product_v0231/run_live_authority_restart.py").read_text(
        encoding="utf-8"
    )

    assert "issue_fault" not in source
    assert "/v1/fault-families" not in source
    assert '"labels": {"fault": nofault_profile.incident_fault_label}' in source
    assert source.index('"restart-checkpoint.json"') < source.index('"/v1/incidents"')

    lifecycle_source = inspect.getsource(AuthorityContinuousSandboxLifecycleV0231)
    assert "SandboxFaultController" not in lifecycle_source
    assert "inject_fault" not in lifecycle_source
    assert not hasattr(ReadOnlyFlagdBaselineObserverV0231, "inject_fault")
    assert not hasattr(ReadOnlyFlagdBaselineObserverV0231, "restore_baseline")


def test_successful_session_rejects_missing_incident() -> None:
    start = _start(1)
    payload = _success(start).model_dump(mode="json")
    payload["incident_count_after"] = 0
    payload["completion_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="successful Runtime continuation"):
        RuntimeContinuationSessionCompletionV0231.model_validate(payload)


def test_runtime_authority_proof_requires_all_nine_equal_components() -> None:
    names = (
        "config_bundle_sha256",
        "connector_binding_sha256",
        "daemon_identity_sha256",
        "docker_context_sha256",
        "ownership_scope_sha256",
        "pilot_runtime_authority_sha256",
        "read_authority_sha256",
        "resolved_endpoints_sha256",
        "resolved_sandbox_sha256",
    )
    proof = RuntimeAuthorityContinuityProofV0231.build(
        continuity_descriptor_sha256=SHA,
        components={
            name: {"expected": SHA, "observed": SHA, "equal": True} for name in names
        },
        runtime_snapshot_before_sha256=SHA,
        runtime_snapshot_after_sha256="b" * 64,
        runtime_snapshot_authority_sha256=SHA,
        terminal="ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS",
    )
    assert proof.proof_sha256

    with pytest.raises(ValidationError, match="component set differs"):
        RuntimeAuthorityContinuityProofV0231.build(
            continuity_descriptor_sha256=SHA,
            components={
                "read_authority_sha256": proof.components["read_authority_sha256"]
            },
            runtime_snapshot_before_sha256=SHA,
            runtime_snapshot_after_sha256="b" * 64,
            runtime_snapshot_authority_sha256=SHA,
            terminal="ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS",
        )

    with pytest.raises(ValidationError, match="snapshot authority binding"):
        RuntimeAuthorityContinuityProofV0231.build(
            continuity_descriptor_sha256=SHA,
            components={
                name: {"expected": SHA, "observed": SHA, "equal": True}
                for name in names
            },
            runtime_snapshot_before_sha256=SHA,
            runtime_snapshot_after_sha256="b" * 64,
            runtime_snapshot_authority_sha256="c" * 64,
            terminal="ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS",
        )


def test_session_private_locator_rejects_parent_traversal() -> None:
    with pytest.raises(ValidationError, match="private locator differs"):
        RuntimeContinuationSessionStartV0231.build(
            session_ordinal=1,
            continuity_descriptor_sha256=SHA,
            execution_head=HEAD,
            private_root_locator=(
                ".local/product-v0231/continuation-sessions/../escape"
            ),
            stage="COMPOSE_VERIFIED",
            pre_start_compose_sha256=SHA,
            incident_count_before=0,
            diagnosis_count_before=0,
        )


def test_v023_terminal_map_is_exhaustive_and_exact() -> None:
    assert nofault_acceptance_v0231._TERMINAL_MAP_V0231 == {
        NOFAULT_FULLY_SUPPORTED_V023: NOFAULT_FULLY_SUPPORTED_V0231,
        NOFAULT_CAPABILITY_LIMITED_V023: NOFAULT_CAPABILITY_LIMITED_V0231,
        NOFAULT_NOT_SUPPORTED_V023: NOFAULT_NOT_SUPPORTED_V0231,
    }


def test_publication_recovery_is_offline_partial_safe_and_idempotent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "successor"
    root.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=root, check=True)
    subprocess.run(
        ("git", "config", "user.email", "product-v0231-test@example.invalid"),
        cwd=root,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Product v0.2.3.1 Test"),
        cwd=root,
        check=True,
    )
    marker = root / "marker.txt"
    marker.write_text("successor\n", encoding="utf-8")
    subprocess.run(("git", "add", "marker.txt"), cwd=root, check=True)
    subprocess.run(("git", "commit", "-qm", "test head"), cwd=root, check=True)
    execution_head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    private_root = root / ".local/product-v0231/continuation-sessions/session-1"
    private_files = {
        "acceptance.json": canonical_json_bytes({"kind": "acceptance"}),
        "session-completion.json": canonical_json_bytes({"kind": "completion"}),
    }
    public_files = {
        locator: canonical_json_bytes({"path": locator})
        if locator.endswith(".json")
        else f"# {locator}\n".encode()
        for locator in _PUBLICATION_OUTPUTS
    }
    _freeze_publication_bundle(
        private_root=private_root,
        execution_head=execution_head,
        private_files=private_files,
        public_files=public_files,
    )
    partial_locator = _PUBLICATION_OUTPUTS[0]
    partial_path = root / partial_locator
    partial_path.parent.mkdir(parents=True)
    partial_path.write_bytes(public_files[partial_locator])
    linked_stale_temporary = partial_path.parent / (
        f".{partial_path.name}.product-v0231-atomic.tmp"
    )
    linked_stale_temporary.write_bytes(public_files[partial_locator])
    stale_target = root / _PUBLICATION_OUTPUTS[1]
    stale_target.parent.mkdir(parents=True, exist_ok=True)
    stale_temporary = stale_target.parent / (
        f".{stale_target.name}.product-v0231-atomic.tmp"
    )
    stale_temporary.write_bytes(b"truncated")

    first = recover_publication_v0231(project_root=root)
    second = recover_publication_v0231(project_root=root)
    assert first == second
    assert first["terminal"] == "ECOMSRE_PRODUCT_V0231_PUBLICATION_RECOVERY_PASS"
    assert first["published_output_count"] == len(_PUBLICATION_OUTPUTS)
    assert not linked_stale_temporary.exists()
    assert not stale_temporary.exists()
    assert all(
        (root / locator).read_bytes() == public_files[locator]
        for locator in _PUBLICATION_OUTPUTS
    )
    assert all(
        (private_root / name).read_bytes() == payload
        for name, payload in private_files.items()
    )

    assert main(("--project-root", str(root), "--recover-publication")) == 0
    assert capsys.readouterr().out.strip() == (
        "ECOMSRE_PRODUCT_V0231_PUBLICATION_RECOVERY_PASS"
    )


def test_tracked_campaign_and_profile_binding_are_self_sealed() -> None:
    profile = NoFaultProfileBindingV0231.model_validate_json(
        (
            ROOT / "config/product-v0231/continuity/nofault-profile-binding.json"
        ).read_bytes()
    )
    campaign = NoFaultCampaignV0231.model_validate_json(
        (ROOT / "config/product-v0231/continuity/campaign.json").read_bytes()
    )

    assert campaign.profile_binding_sha256 == profile.binding_sha256
    assert campaign.fault_attempt_limit == 0
    assert campaign.knowledge_loop_campaign_limit == 0
