"""Real persistence with fake external effects; never a live remediation claim."""

from datetime import timedelta

import pytest

from ecomsre.product.remediation.execution_contracts import (
    RecoveryObservationV1,
    RecoveryPolicyV1,
)
from ecomsre.product.remediation.executor import (
    ProductPaymentConfigurationRollbackExecutor,
)
from ecomsre.product.remediation.recovery import RecoveryRepositoryV1
from tests.product_v040.test_authorization import (
    authorized_material as authorized_material,
    create,
)
from tests.product_v040.test_approval_api import api as api
from tests.product_v040.test_candidates import material as material


class FakeRestore:
    def __init__(self, values):
        self.values = values
        self.calls = 0
        self.crash = False

    def restore_baseline(self, dispatch, *, expires_at):
        self.calls += 1
        api, _, _, provider, repo = self.values
        assert repo.get(dispatch.attempt_id).state.value == "EXECUTING"
        assert repo.clock() < expires_at
        provider.changes.update(
            current_configuration_digest=repo.binding.baseline_configuration_digest,
            fault_still_present=False,
        )
        api[3][0] += timedelta(seconds=1)
        if self.crash:
            raise RuntimeError(
                "fictional private transport credential must never enter evidence"
            )
        return provider.read_current()


class FakeRecovery:
    def __init__(self, values, **changes):
        self.values = values
        self.calls = 0
        self.changes = changes

    def acquire(self, *, started_after, policy):
        self.calls += 1
        start = started_after + timedelta(milliseconds=1)
        end = start + timedelta(seconds=policy.window_seconds)
        self.values[0][3][0] = end
        return RecoveryObservationV1.build(
            **{
                "environment_id": policy.environment_id,
                "policy_sha256": policy.policy_sha256,
                "started_at": start,
                "ended_at": end,
                "elapsed_ms": policy.window_seconds * 1000,
                "infrastructure_passed": True,
                "endpoint_passed": True,
                "business_requests": 100,
                "business_errors": 0,
                "configuration_digest": policy.baseline_configuration_digest,
                "flag_evaluation_restored": True,
                "non_owned_resources_unchanged": True,
                "environment_ownership_digest": policy.environment_ownership_digest,
                "created_at": end,
                **self.changes,
            }
        )


@pytest.fixture
def executable(authorized_material):
    values = authorized_material
    repo = values[4]
    recovery = RecoveryRepositoryV1(repo)
    binding = repo.binding
    policy = RecoveryPolicyV1.build(
        environment_id=binding.environment_id,
        baseline_sha256=binding.baseline_sha256,
        baseline_configuration_digest=binding.baseline_configuration_digest,
        fault_configuration_digest=binding.fault_configuration_digest,
        target_identity_digest=binding.target_identity_digest,
        control_identity_sha256=binding.control_identity_sha256,
        environment_ownership_digest=binding.environment_ownership_digest,
        business_error_ratio_max=0.01,
        minimum_business_requests=10,
        window_seconds=10,
        created_at=binding.created_at,
    )
    recovery.bind_policy(policy)
    attempt = create(values).json()
    values[0][3][0] += timedelta(seconds=1)
    adapter = FakeRestore(values)
    executor = ProductPaymentConfigurationRollbackExecutor(repo, adapter)
    return values, attempt, adapter, executor, recovery, policy


def test_one_real_persisted_fake_restore_and_two_windows(executable):
    values, attempt, adapter, executor, recovery, _ = executable
    applied = executor.run_one(attempt["attempt_id"])
    assert applied.state.value == "APPLIED", values[4].trace(attempt["attempt_id"])
    assert applied.forward_write_count == 1 and adapter.calls == 1
    provider = FakeRecovery(values)
    result = recovery.verify(attempt["attempt_id"], provider)
    assert result.state.value == "RECOVERED", recovery.evaluation(attempt["attempt_id"])
    assert provider.calls == 2
    assert len(recovery.windows(attempt["attempt_id"])) == 2
    assert recovery.evaluation(attempt["attempt_id"]).outcome == "PASS"
    assert recovery.verify(attempt["attempt_id"], provider) == result
    with pytest.raises(Exception):
        executor.run_one(attempt["attempt_id"])
    assert adapter.calls == 1 and provider.calls == 2
    assert values[4].trace(attempt["attempt_id"])[-1].state.value == "RECOVERED"


def test_crash_after_mutation_is_unknown_and_never_retried(executable):
    values, attempt, adapter, executor, recovery, _ = executable
    adapter.crash = True
    result = executor.run_one(attempt["attempt_id"])
    assert result.state.value == "OUTCOME_UNKNOWN"
    assert result.forward_write_count is None
    assert recovery.receipt(attempt["attempt_id"]) is None
    assert (
        values[3].read_current().current_configuration_digest
        == values[4].binding.baseline_configuration_digest
    )
    with pytest.raises(Exception):
        executor.run_one(attempt["attempt_id"])
    assert adapter.calls == 1
    assert "credential" not in result.model_dump_json()


def test_receipt_persistence_failure_consumes_dispatch(executable, monkeypatch):
    values, attempt, adapter, executor, recovery, _ = executable

    def broken(_):
        raise OSError("private write failure")

    monkeypatch.setattr(executor, "persist_receipt", broken)
    result = executor.run_one(attempt["attempt_id"])
    assert (
        result.state.value == "OUTCOME_UNKNOWN"
        and result.final_disposition == "ESCALATE_HUMAN"
    )
    assert adapter.calls == 1 and recovery.receipt(attempt["attempt_id"]) is None
    with pytest.raises(Exception):
        executor.run_one(attempt["attempt_id"])
    assert adapter.calls == 1


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"endpoint_passed": False}, "ENDPOINT_FAILED"),
        ({"infrastructure_passed": False}, "INFRASTRUCTURE_FAILED"),
        ({"business_errors": 2}, "BUSINESS_SLI_FAILED"),
        ({"business_requests": 0}, "BUSINESS_SLI_FAILED"),
        ({"configuration_digest": "f" * 64}, "CONFIGURATION_NOT_RESTORED"),
        ({"flag_evaluation_restored": False}, "FLAG_NOT_RESTORED"),
        ({"non_owned_resources_unchanged": False}, "NON_OWNED_DRIFT"),
    ],
)
def test_applied_but_failed_verification_never_acts_again(executable, changes, reason):
    values, attempt, adapter, executor, recovery, _ = executable
    assert executor.run_one(attempt["attempt_id"]).state.value == "APPLIED"
    result = recovery.verify(attempt["attempt_id"], FakeRecovery(values, **changes))
    assert result.state.value == "VERIFICATION_FAILED"
    assert reason in recovery.evaluation(attempt["attempt_id"]).reason_codes
    assert adapter.calls == 1


def test_restart_after_intent_cannot_dispatch(executable):
    values, attempt, adapter, executor, _, _ = executable
    repo = values[4]
    lease = repo.claim(attempt["attempt_id"])
    repo.commit_write_intent(
        attempt["attempt_id"],
        lease_owner=lease.active_lease_owner,
        lease_generation=lease.lease_generation,
    )
    with pytest.raises(Exception):
        executor.run_one(attempt["attempt_id"])
    assert adapter.calls == 0


def test_ticking_clock_does_not_require_equal_snapshot_and_intent_time(executable):
    values, attempt, adapter, executor, _, _ = executable
    now = values[0][3]

    def ticking():
        now[0] += timedelta(microseconds=1)
        return now[0]

    values[4].clock = ticking
    values[4].approvals.clock = ticking
    values[3].clock = ticking
    assert executor.run_one(attempt["attempt_id"]).state.value == "APPLIED"
    assert adapter.calls == 1


def test_receipt_after_lease_loss_is_unknown(executable):
    values, attempt, adapter, executor, recovery, _ = executable
    original = adapter.restore_baseline

    def delayed(dispatch, *, expires_at):
        result = original(dispatch, expires_at=expires_at)
        values[0][3][0] += timedelta(seconds=31)
        return result

    adapter.restore_baseline = delayed
    result = executor.run_one(attempt["attempt_id"])
    assert result.state.value == "OUTCOME_UNKNOWN"
    assert adapter.calls == 1 and recovery.receipt(attempt["attempt_id"]) is None


def test_proven_adapter_rejection_is_execution_failed(executable):
    from ecomsre.product.remediation.executor import RestoreNotAppliedV1

    values, attempt, adapter, executor, recovery, _ = executable

    def rejected(dispatch, *, expires_at):
        raise RestoreNotAppliedV1(values[3].read_current())

    adapter.restore_baseline = rejected
    result = executor.run_one(attempt["attempt_id"])
    assert result.state.value == "EXECUTION_FAILED"
    assert result.forward_write_count == 0 and adapter.calls == 0
    assert recovery.receipt(attempt["attempt_id"]).outcome == "FAILED"


def test_unrelated_receipt_cas_cannot_mint_applied(executable):
    from ecomsre.product.remediation.execution_contracts import StepReceiptV1

    values, attempt, adapter, executor, recovery, _ = executable
    repo = values[4]
    lease = repo.claim(attempt["attempt_id"])
    committed = repo.commit_write_intent(
        attempt["attempt_id"],
        lease_owner=lease.active_lease_owner,
        lease_generation=lease.lease_generation,
    )
    dispatch, _ = executor._reserve(committed)
    reference = repo.objects.put_json({"not_state_evidence": True}).object_sha256
    receipt = StepReceiptV1.build(
        receipt_id="receipt-" + "1" * 24,
        attempt_id=attempt["attempt_id"],
        write_intent_id=dispatch.write_intent_id,
        write_intent_sha256=dispatch.write_intent_sha256,
        dispatch_sha256=dispatch.dispatch_sha256,
        before_state_digest=repo.binding.fault_configuration_digest,
        after_state_digest=repo.binding.baseline_configuration_digest,
        started_at=repo.clock(),
        ended_at=repo.clock(),
        elapsed_ms=0,
        outcome="APPLIED",
        supporting_evidence_refs=(reference,),
        created_at=repo.clock(),
    )
    with pytest.raises(Exception):
        executor.persist_receipt(receipt)
    assert recovery.receipt(attempt["attempt_id"]) is None and adapter.calls == 0


def test_standalone_demo_is_fixture_only():
    from scripts.product.demo_remediation_v040 import run_demo

    result = run_demo()
    assert result["mode"] == "OFFLINE_SYNTHETIC_FIXTURE"
    assert result["terminal"] == "RECOVERED" and result["forward_fake_mutations"] == 1
    assert (
        result["live_campaigns"]
        == result["live_mutations"]
        == result["provider_calls"]
        == 0
    )


def test_metrics_have_no_high_cardinality_or_external_failure_effect(
    executable, monkeypatch
):
    values, attempt, _, executor, recovery, _ = executable
    executor.run_one(attempt["attempt_id"])
    recovery.verify(attempt["attempt_id"], FakeRecovery(values))
    metrics = values[0][1].state.metrics
    body = metrics.render()
    assert "ecomsre_remediation_forward_steps_total 1" in body
    assert attempt["attempt_id"] not in body
    assert 'ecomsre_remediation_verification_total{outcome="PASS"} 1' in body

    def broken():
        raise OSError("secret should not leak")

    monkeypatch.setattr(metrics, "_remediation_render", broken)
    assert "secret" not in metrics.render()
    assert values[4].get(attempt["attempt_id"]).state.value == "RECOVERED"


@pytest.mark.parametrize(
    "case,reason",
    [
        ("one", "WINDOW_COUNT"),
        ("overlap", "WINDOW_OVERLAP"),
        ("missing-receipt", "RECEIPT_INVALID"),
        ("invalid-receipt-hash", "RECEIPT_INVALID"),
        ("missing-evidence", "EVIDENCE_UNRESOLVED"),
        ("wrong-window-parent", "WINDOW_BINDING"),
        ("future", "WINDOW_NOT_FRESH"),
    ],
)
def test_verifier_rejects_incomplete_or_rebound_evidence(executable, case, reason):
    from ecomsre.product.remediation.execution_contracts import RecoveryWindowV1
    from ecomsre.product.remediation.verifier import evaluate

    values, attempt, _, executor, recovery, policy = executable
    repo = values[4]
    executor.run_one(attempt["attempt_id"])
    recovery.verify(attempt["attempt_id"], FakeRecovery(values))
    windows = list(recovery.windows(attempt["attempt_id"]))
    receipt = recovery.receipt(attempt["attempt_id"])
    if case == "one":
        windows.pop()
    elif case == "overlap":
        payload = windows[1].model_dump(mode="python", exclude={"window_sha256"})
        payload["started_at"] = windows[0].started_at
        windows[1] = RecoveryWindowV1.build(**payload)
    elif case == "missing-receipt":
        receipt = None
    elif case == "invalid-receipt-hash":
        receipt = receipt.model_copy(update={"receipt_sha256": "0" * 64})
    elif case in {"missing-evidence", "wrong-window-parent"}:
        payload = windows[1].model_dump(mode="python", exclude={"window_sha256"})
        payload.update(
            {"supporting_evidence_refs": ("0" * 64,)}
            if case == "missing-evidence"
            else {"attempt_id": "attempt-" + "0" * 24}
        )
        windows[1] = RecoveryWindowV1.build(**payload)
    result = evaluate(
        attempt_id=attempt["attempt_id"],
        receipt=receipt,
        windows=windows,
        policy=policy,
        resolve=repo.objects.read_bytes,
        now=repo.clock() - timedelta(seconds=1) if case == "future" else repo.clock(),
    )
    assert result.outcome == "FAIL" and reason in result.reason_codes


def test_recovery_reentry_cannot_acquire_extra_windows(executable):
    values, attempt, _, executor, recovery, _ = executable
    executor.run_one(attempt["attempt_id"])

    class Reentrant(FakeRecovery):
        def acquire(self, **kwargs):
            from ecomsre.product.errors import ProductError

            with pytest.raises(ProductError) as error:
                recovery.verify(attempt["attempt_id"], self)
            assert error.value.code == "REMEDIATION_RECOVERY_IN_PROGRESS"
            return super().acquire(**kwargs)

    provider = Reentrant(values)
    assert recovery.verify(attempt["attempt_id"], provider).state.value == "RECOVERED"
    assert provider.calls == 2


def test_resealed_persisted_receipt_cannot_swap_dispatch(executable):
    from ecomsre.product.remediation.execution_contracts import StepReceiptV1
    from ecomsre.product.remediation.repository import canonical

    values, attempt, _, executor, recovery, _ = executable
    executor.run_one(attempt["attempt_id"])
    receipt = recovery.receipt(attempt["attempt_id"])
    payload = receipt.model_dump(mode="python", exclude={"receipt_sha256"})
    payload["dispatch_sha256"] = "0" * 64
    forged = StepReceiptV1.build(**payload)
    with values[4].store.connect() as connection:
        connection.execute(
            "UPDATE remediation_step_receipts SET receipt_sha256 = ?, payload_json = ? WHERE attempt_id = ?",
            (forged.receipt_sha256, canonical(forged), attempt["attempt_id"]),
        )
    provider = FakeRecovery(values)
    with pytest.raises(Exception):
        recovery.verify(attempt["attempt_id"], provider)
    assert (
        provider.calls == 0
        and values[4].get(attempt["attempt_id"]).state.value == "APPLIED"
    )


@pytest.mark.parametrize(
    "patch", [{"baseline_sha256": "0" * 64}, {"business_error_ratio_max": 1.0}]
)
def test_resealed_policy_cannot_change_bound_or_threshold_after_dispatch(
    executable, patch
):
    from ecomsre.product.remediation.repository import canonical

    values, attempt, _, executor, recovery, policy = executable
    executor.run_one(attempt["attempt_id"])
    forged = RecoveryPolicyV1.build(
        **{**policy.model_dump(mode="python", exclude={"policy_sha256"}), **patch}
    )
    with values[4].store.connect() as connection:
        connection.execute(
            "UPDATE remediation_recovery_policies SET policy_sha256 = ?, payload_json = ? WHERE environment_id = ?",
            (forged.policy_sha256, canonical(forged), policy.environment_id),
        )
    provider = FakeRecovery(values)
    with pytest.raises(Exception):
        recovery.verify(attempt["attempt_id"], provider)
    assert (
        provider.calls == 0
        and values[4].get(attempt["attempt_id"]).state.value == "APPLIED"
    )
