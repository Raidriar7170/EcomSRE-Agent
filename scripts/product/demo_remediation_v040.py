"""Standalone deterministic fixture demo. No network, Docker or live adapter."""

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ecomsre.product.remediation.approval import ApprovalRequestV1
from ecomsre.product.remediation.attempt_contracts import AttemptRequestV1
from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
from ecomsre.product.remediation.execution_contracts import (
    ExecutorDispatchV1,
    RecoveryObservationV1,
    RecoveryPolicyV1,
)
from ecomsre.product.remediation.executor import (
    ProductPaymentConfigurationRollbackExecutor,
)
from ecomsre.product.remediation.recovery import RecoveryRepositoryV1
from ecomsre.product.remediation.repository import RemediationRepositoryV1
from ecomsre.product.remediation.state import StateObservationV1, TrustedStateBindingV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


FIXTURE_SHA256 = "0ebbd7a769ae8df8ecc8c258d587217ca3d311de07f37fd336961043d2cc2e11"
TABLES = (
    "environments",
    "services",
    "environment_capability_matrices",
    "baseline_versions",
    "incidents",
    "diagnosis_results",
    "diagnosis_evidence_links",
    "diagnosis_evidence_indexes",
)


class FixtureBackend:
    def __init__(self, binding: TrustedStateBindingV1) -> None:
        self.binding = binding
        self.now = datetime(2026, 8, 20, tzinfo=UTC)
        self.mutations = 0

    def read_current(self) -> StateObservationV1:
        self.now += timedelta(milliseconds=1)
        b = self.binding
        return StateObservationV1.build(
            environment_id=b.environment_id,
            environment_owned=True,
            local_control_trusted=True,
            environment_ownership_digest=b.environment_ownership_digest,
            target_identity_digest=b.target_identity_digest,
            control_identity_sha256=b.control_identity_sha256,
            target_logical_service="payment",
            baseline_configuration_digest=b.baseline_configuration_digest,
            current_configuration_digest=b.baseline_configuration_digest
            if self.mutations
            else b.fault_configuration_digest,
            fault_still_present=not bool(self.mutations),
            observed_at=self.now,
            created_at=self.now,
        )

    def restore_baseline(
        self, dispatch: ExecutorDispatchV1, *, expires_at: datetime
    ) -> StateObservationV1:
        if self.mutations or self.now >= expires_at:
            raise ValueError("fixture write denied")
        self.mutations += 1
        return self.read_current()

    def acquire(
        self, *, started_after: datetime, policy: RecoveryPolicyV1
    ) -> RecoveryObservationV1:
        start = started_after + timedelta(milliseconds=1)
        end = start + timedelta(seconds=policy.window_seconds)
        self.now = end
        return RecoveryObservationV1.build(
            environment_id=policy.environment_id,
            policy_sha256=policy.policy_sha256,
            started_at=start,
            ended_at=end,
            elapsed_ms=policy.window_seconds * 1000,
            infrastructure_passed=True,
            endpoint_passed=True,
            business_requests=100,
            business_errors=0,
            configuration_digest=policy.baseline_configuration_digest,
            flag_evaluation_restored=True,
            non_owned_resources_unchanged=True,
            environment_ownership_digest=policy.environment_ownership_digest,
            created_at=end,
        )


def run_demo() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parents[2]
        / "examples/product/remediation-fixture.v1.json"
    )
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError("fixture bytes differ")
    fixture = json.loads(raw)
    with TemporaryDirectory(prefix="ecomsre-v040-fixture-") as temporary:
        root = Path(temporary)
        store = SqliteStoreV1(root / "product.sqlite3")
        objects = ContentAddressedObjectStoreV1(root / "objects", metadata_store=store)
        for reference, payload in fixture["objects"].items():
            if objects.put_json(payload).object_sha256 != reference:
                raise ValueError("fixture CAS binding differs")
        with store.connect() as connection:
            for table in TABLES:
                for row in fixture["rows"][table]:
                    placeholders = ",".join("?" for _ in row)
                    connection.execute(
                        f"INSERT INTO {table} VALUES ({placeholders})", row
                    )
        approvals = RemediationRepositoryV1(store, objects)
        candidate = approvals.create_candidates(
            "inc-" + "5" * 24, "fixture-candidate"
        ).candidates[0]
        binding = TrustedStateBindingV1.build(
            environment_id=candidate.environment_id,
            environment_ownership_digest="a" * 64,
            target_identity_digest="b" * 64,
            identity_map_sha256=candidate.identity_map_sha256,
            control_identity_sha256="c" * 64,
            baseline_id=candidate.baseline_id,
            baseline_sha256=candidate.baseline_sha256,
            baseline_configuration_digest="d" * 64,
            fault_configuration_digest="e" * 64,
            registry_sha256=candidate.registry_sha256,
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
        backend = FixtureBackend(binding)
        approvals.clock = lambda: backend.now
        approval = approvals.approve(
            candidate.candidate_id,
            ApprovalRequestV1.model_validate(
                {
                    "approver": "LOCAL_OPERATOR",
                    "authorization_source": "USER_EXPLICIT_OPERATOR_AUTHORIZATION",
                    "decision": "APPROVE",
                    "scope": {
                        "runbook_id": "ROLLBACK_CONFIGURATION",
                        "target_logical_service": "payment",
                        "maximum_forward_steps": 1,
                    },
                    "ttl_seconds": 120,
                }
            ),
            "fixture-approval",
        )
        attempts = RemediationAttemptRepositoryV1(
            approvals, provider=backend, binding=binding
        )
        recovery = RecoveryRepositoryV1(attempts)
        recovery.bind_policy(
            RecoveryPolicyV1.build(
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
        )
        attempt = attempts.create(
            candidate.candidate_id,
            AttemptRequestV1(approval_id=approval.approval_id),
            "fixture-attempt",
        )
        executor = ProductPaymentConfigurationRollbackExecutor(attempts, backend)
        applied = executor.run_one(attempt.attempt_id)
        if applied.state.value != "APPLIED":
            raise ValueError("fixture did not apply")
        final = recovery.verify(attempt.attempt_id, backend)
        return {
            "mode": "OFFLINE_SYNTHETIC_FIXTURE",
            "terminal": final.state.value,
            "forward_fake_mutations": backend.mutations,
            "recovery_windows": len(recovery.windows(attempt.attempt_id)),
            "live_campaigns": 0,
            "live_mutations": 0,
            "provider_calls": 0,
            "diagnosis_action_authority": "NONE",
            "fixture_sha256": FIXTURE_SHA256,
            "final_disposition": final.final_disposition,
        }


if __name__ == "__main__":
    print(json.dumps(run_demo(), sort_keys=True, indent=2))
