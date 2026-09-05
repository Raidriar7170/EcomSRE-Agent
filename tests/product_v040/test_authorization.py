"""Fresh state and transactional admission using only a read-only fake provider."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from ecomsre.product.remediation.attempt_contracts import AttemptRequestV1
from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
from ecomsre.product.remediation.state import StateObservationV1, TrustedStateBindingV1
from tests.product_v040.test_approval_api import api as api, candidate, approve, headers
from tests.product_v040.test_candidates import material as material


class FakeStateProvider:
    def __init__(self, binding, clock):
        self.binding = binding
        self.clock = clock
        self.changes = {}
        self.reads = 0

    def read_current(self):
        self.reads += 1
        now = self.clock()
        return StateObservationV1.build(
            **{
                "environment_id": self.binding.environment_id,
                "environment_owned": True,
                "local_control_trusted": True,
                "environment_ownership_digest": self.binding.environment_ownership_digest,
                "target_identity_digest": self.binding.target_identity_digest,
                "control_identity_sha256": self.binding.control_identity_sha256,
                "target_logical_service": "payment",
                "baseline_configuration_digest": self.binding.baseline_configuration_digest,
                "current_configuration_digest": self.binding.fault_configuration_digest,
                "fault_still_present": True,
                "observed_at": now,
                "created_at": now,
                **self.changes,
            }
        )


@pytest.fixture
def authorized_material(api):
    _, app, _, now = api
    item = candidate(api)
    approval = approve(api, item).json()
    binding = TrustedStateBindingV1.build(
        environment_id=item["environment_id"],
        environment_ownership_digest="a" * 64,
        target_identity_digest="b" * 64,
        identity_map_sha256=item["identity_map_sha256"],
        control_identity_sha256="c" * 64,
        baseline_id=item["baseline_id"],
        baseline_sha256=item["baseline_sha256"],
        baseline_configuration_digest="d" * 64,
        fault_configuration_digest="e" * 64,
        registry_sha256=item["registry_sha256"],
        created_at=now[0],
    )
    provider = FakeStateProvider(binding, lambda: now[0])
    now[0] += timedelta(seconds=1)
    repository = RemediationAttemptRepositoryV1(
        app.state.remediation, provider=provider, binding=binding
    )
    app.state.remediation_attempts = repository
    return api, item, approval, provider, repository


def create(values, key="attempt-1"):
    api, item, approval, _, _ = values
    return api[0].post(
        f"/v1/remediation-candidates/{item['candidate_id']}/attempts",
        json={"approval_id": approval["approval_id"]},
        headers=headers(key),
    )


def test_fresh_state_authorizes_once_and_replay_survives_provider_loss(
    authorized_material,
):
    api, item, approval, provider, repository = authorized_material
    response = create(authorized_material)
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert attempt["state"] == "AUTHORIZED" and attempt["forward_write_count"] == 0
    assert attempt["write_intent_id"] is None
    assert [event.state.value for event in repository.trace(attempt["attempt_id"])][
        ::7
    ] == ["CANDIDATE_CREATED", "AUTHORIZED"]
    reads = provider.reads
    repository.provider = None
    assert create(authorized_material).json() == attempt
    assert provider.reads == reads
    again = create(authorized_material, key="second")
    assert again.json()["safe_error_code"] == "APPROVAL_ALREADY_CONSUMED"
    with repository.store.connect() as connection:
        for table in (
            "remediation_authorizations",
            "remediation_approval_consumptions",
            "remediation_current_state_snapshots",
        ):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
            )
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_write_intents"
            ).fetchone()[0]
            == 0
        )
    assert (
        api[0].get("/v1/remediation-attempts/" + attempt["attempt_id"]).json()
        == attempt
    )
    assert approval["approval_id"] == attempt["approval_id"]
    assert item["candidate_id"] == attempt["candidate_id"]


@pytest.mark.parametrize(
    "patch,reason",
    [
        ({"environment_owned": False}, "ENVIRONMENT_NOT_OWNED"),
        ({"environment_ownership_digest": "0" * 64}, "ENVIRONMENT_NOT_OWNED"),
        ({"local_control_trusted": False}, "REMOTE_OR_UNTRUSTED_CONTROL"),
        ({"control_identity_sha256": "0" * 64}, "REMOTE_OR_UNTRUSTED_CONTROL"),
        ({"target_logical_service": "checkout"}, "TARGET_IDENTITY_MISMATCH"),
        ({"target_identity_digest": "0" * 64}, "TARGET_IDENTITY_MISMATCH"),
        ({"baseline_configuration_digest": "0" * 64}, "BASELINE_MISMATCH"),
        ({"current_configuration_digest": "0" * 64}, "STATE_DRIFTED"),
        ({"current_configuration_digest": "d" * 64}, "CONFIGURATION_DRIFT_NOT_VISIBLE"),
        ({"fault_still_present": False}, "FAULT_NO_LONGER_PRESENT"),
    ],
)
def test_state_denials_persist_zero_authority(authorized_material, patch, reason):
    _, _, _, provider, repository = authorized_material
    provider.changes.update(patch)
    response = create(authorized_material)
    assert response.status_code == 200, response.text
    assert response.json()["safe_error_code"] == reason
    assert response.json()["authorization_id"] is None
    assert response.json()["forward_write_count"] == 0
    with repository.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_authorizations"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_approval_consumptions"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("seconds", [-31, -1, 1])
def test_snapshot_must_be_new_after_approval_and_not_future(
    authorized_material, seconds
):
    api, _, _, provider, _ = authorized_material
    stamp = api[3][0] + timedelta(seconds=seconds)
    provider.changes.update(observed_at=stamp, created_at=stamp)
    assert create(authorized_material).json()["safe_error_code"] == "STATE_STALE"


def test_two_distinct_approvals_cannot_create_two_active_attempts(authorized_material):
    api, item, _, _, repository = authorized_material
    second_approval = approve(api, item, key="approval-2").json()
    api[3][0] += timedelta(seconds=1)
    first = create(authorized_material).json()
    second = api[0].post(
        f"/v1/remediation-candidates/{item['candidate_id']}/attempts",
        json={"approval_id": second_approval["approval_id"]},
        headers=headers("attempt-2"),
    )
    assert second.json()["safe_error_code"] == "SECOND_ACTIVE_TRANSACTION"
    assert first["state"] == "AUTHORIZED"
    assert len(repository.trace(first["attempt_id"])) == 8


def test_concurrent_attempt_reuses_single_authorization(authorized_material):
    _, item, approval, _, repository = authorized_material
    request = AttemptRequestV1(approval_id=approval["approval_id"])
    with ThreadPoolExecutor(max_workers=5) as pool:
        attempts = list(
            pool.map(
                lambda _: repository.create(
                    item["candidate_id"], request, "concurrent"
                ),
                range(10),
            )
        )
    assert len({item.attempt_id for item in attempts}) == 1
    with repository.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_authorizations"
            ).fetchone()[0]
            == 1
        )


def test_default_api_has_no_trusted_state_provider_and_requires_auth(api):
    item = candidate(api)
    approval = approve(api, item).json()
    uri = f"/v1/remediation-candidates/{item['candidate_id']}/attempts"
    payload = {"approval_id": approval["approval_id"]}
    assert (
        api[0].post(uri, json=payload, headers={"Idempotency-Key": "a"}).status_code
        == 401
    )
    denied = api[0].post(uri, json=payload, headers=headers("b"))
    assert denied.status_code == 200, denied.text
    assert denied.json()["safe_error_code"] == "REMOTE_OR_UNTRUSTED_CONTROL"
    assert (
        api[0]
        .post(uri, json={**payload, "url": "http://untrusted"}, headers=headers("c"))
        .status_code
        == 422
    )


def claim(values):
    attempt = create(values).json()
    return values[4].claim(attempt["attempt_id"])


def commit_intent(values, claimed):
    values[0][3][0] += timedelta(seconds=1)
    return values[4].commit_write_intent(
        claimed.attempt_id,
        lease_owner=claimed.active_lease_owner,
        lease_generation=claimed.lease_generation,
    )


def test_intent_consumes_authorization_once_and_never_performs_mutation(
    authorized_material,
):
    from ecomsre.product.errors import ProductError

    _, _, _, provider, repository = authorized_material
    claimed = claim(authorized_material)
    committed = commit_intent(authorized_material, claimed)
    assert committed.state.value == "WRITE_INTENT_COMMITTED"
    assert committed.forward_write_count == 0
    with pytest.raises(ProductError) as error:
        commit_intent(authorized_material, claimed)
    assert error.value.code == "REMEDIATION_PRIOR_WRITE_INTENT"
    with repository.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_write_intents"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_authorization_consumptions"
            ).fetchone()[0]
            == 1
        )
    assert not hasattr(provider, "restore_baseline")
    assert create(authorized_material).json()["attempt_id"] == committed.attempt_id
    assert create(authorized_material).json()["state"] == "WRITE_INTENT_COMMITTED"


def test_expired_pre_intent_lease_reclaims_and_fences_old_owner(authorized_material):
    from ecomsre.product.errors import ProductError

    api, _, _, _, repository = authorized_material
    first = claim(authorized_material)
    with pytest.raises(ProductError) as error:
        repository.claim(first.attempt_id)
    assert error.value.code == "REMEDIATION_LEASE_ACTIVE"
    api[3][0] += timedelta(seconds=31)
    second = repository.claim(first.attempt_id)
    assert second.lease_generation == first.lease_generation + 1
    assert second.active_lease_owner != first.active_lease_owner
    with pytest.raises(ProductError) as error:
        repository.commit_write_intent(
            first.attempt_id,
            lease_owner=first.active_lease_owner,
            lease_generation=first.lease_generation,
        )
    assert error.value.code == "REMEDIATION_LEASE_LOST"
    assert commit_intent(authorized_material, second).write_intent_id is not None


def test_expired_authorization_cannot_claim_or_create_intent(authorized_material):
    api, _, _, _, repository = authorized_material
    attempt = create(authorized_material).json()
    api[3][0] += timedelta(seconds=120)
    denied = repository.claim(attempt["attempt_id"])
    assert denied.state.value == "AUTHORIZATION_EXPIRED"
    assert denied.write_intent_id is None and denied.forward_write_count == 0


@pytest.mark.parametrize("baseline_restored", [False, True])
def test_crash_after_intent_never_reclaims_or_recovers_without_receipt(
    authorized_material, baseline_restored
):
    from ecomsre.product.errors import ProductError

    api, _, _, provider, repository = authorized_material
    committed = commit_intent(authorized_material, claim(authorized_material))
    if baseline_restored:
        provider.changes.update(
            current_configuration_digest=provider.binding.baseline_configuration_digest,
            fault_still_present=False,
        )
    api[3][0] += timedelta(seconds=31)
    restarted = RemediationAttemptRepositoryV1(
        repository.approvals, provider=provider, binding=provider.binding
    )
    with pytest.raises(ProductError) as error:
        restarted.claim(committed.attempt_id)
    assert error.value.code == "REMEDIATION_RECONCILIATION_REQUIRED"
    reconciled = restarted.reconcile_expired_intent(committed.attempt_id)
    assert reconciled.state.value == "OUTCOME_UNKNOWN"
    assert reconciled.final_disposition == "ESCALATE_HUMAN"
    assert restarted.reconcile_expired_intent(committed.attempt_id) == reconciled
    assert restarted.get(committed.attempt_id).forward_write_count == 0
    with pytest.raises(ProductError):
        restarted.cancel_before_write(committed.attempt_id)


def test_pre_intent_cancel_is_terminal_and_keeps_approval_consumed(authorized_material):
    from ecomsre.product.errors import ProductError

    repository = authorized_material[4]
    first = claim(authorized_material)
    cancelled = repository.cancel_before_write(first.attempt_id)
    assert cancelled.state.value == "CANCELLED_BEFORE_WRITE"
    assert cancelled.final_disposition == "NO_WRITE"
    with pytest.raises(ProductError):
        repository.claim(first.attempt_id)
    assert (
        create(authorized_material, key="second").json()["safe_error_code"]
        == "APPROVAL_ALREADY_CONSUMED"
    )


def test_state_drift_immediately_before_intent_denies(authorized_material):
    claimed = claim(authorized_material)
    authorized_material[3].changes["current_configuration_digest"] = "f" * 64
    denied = commit_intent(authorized_material, claimed)
    assert denied.state.value == "STATE_DRIFTED"
    assert denied.write_intent_id is None


def test_revocation_before_intent_denies(authorized_material):
    api, item, approval, _, _ = authorized_material
    claimed = claim(authorized_material)
    response = api[0].post(
        f"/v1/remediation-candidates/{item['candidate_id']}/revocations",
        json={"approval_id": approval["approval_id"]},
        headers=headers("revoke"),
    )
    assert response.status_code == 200
    denied = commit_intent(authorized_material, claimed)
    assert denied.state.value == "APPROVAL_REVOKED"
    assert denied.write_intent_id is None


def test_changed_authorization_parent_is_rejected(authorized_material):
    import json
    from ecomsre.product.errors import ProductError
    from ecomsre.product.remediation.authorization import AttemptAuthorizationV1
    from ecomsre.product.remediation.repository import canonical

    repository = authorized_material[4]
    attempt = create(authorized_material).json()
    with repository.store.connect() as connection:
        raw = json.loads(
            connection.execute(
                "SELECT payload_json FROM remediation_authorizations"
            ).fetchone()[0]
        )
        raw.pop("authorization_sha256")
        changed = AttemptAuthorizationV1.build(**{**raw, "diagnosis_sha256": "0" * 64})
        connection.execute(
            "UPDATE remediation_authorizations SET payload_json = ?, authorization_sha256 = ?",
            (canonical(changed), changed.authorization_sha256),
        )
    with pytest.raises(ProductError) as error:
        repository.claim(attempt["attempt_id"])
    assert error.value.code == "REMEDIATION_AUTHORIZATION_BINDING_MISMATCH"


def test_authorization_cannot_broaden_risk_steps_or_parameters(authorized_material):
    import json
    from pydantic import ValidationError
    from ecomsre.product.remediation.authorization import AttemptAuthorizationV1

    create(authorized_material)
    with authorized_material[4].store.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM remediation_authorizations"
            ).fetchone()[0]
        )
    payload.pop("authorization_sha256")
    for patch in (
        {"risk_level": "HIGH"},
        {"maximum_forward_steps": 2},
        {"parameters_sha256": "0" * 64},
    ):
        with pytest.raises(ValidationError):
            AttemptAuthorizationV1.build(**{**payload, **patch})


def test_trace_rejects_event_chain_corruption(authorized_material):
    from ecomsre.product.errors import ProductError

    repository = authorized_material[4]
    attempt = create(authorized_material).json()
    with repository.store.connect() as connection:
        connection.execute(
            "DELETE FROM remediation_decision_trace_events WHERE ordinal = 2"
        )
    with pytest.raises(ProductError) as error:
        repository.trace(attempt["attempt_id"])
    assert error.value.code == "REMEDIATION_TRACE_BINDING_MISMATCH"


def test_slow_candidate_validation_cannot_authorize_stale_state(
    authorized_material, monkeypatch
):
    api, _, _, _, repository = authorized_material
    original = repository._current_candidate

    def slow(candidate):
        original(candidate)
        api[3][0] += timedelta(seconds=31)

    monkeypatch.setattr(repository, "_current_candidate", slow)
    denied = create(authorized_material)
    assert denied.json()["safe_error_code"] == "STATE_STALE"
    assert denied.json()["authorization_id"] is None


@pytest.mark.parametrize("delay", [31, 121])
def test_slow_pre_intent_validation_cannot_outlive_lease_or_authorization(
    authorized_material, monkeypatch, delay
):
    from ecomsre.product.errors import ProductError

    api, _, _, _, repository = authorized_material
    claimed = claim(authorized_material)
    original = repository._current_candidate

    def slow(candidate):
        original(candidate)
        api[3][0] += timedelta(seconds=delay)

    monkeypatch.setattr(repository, "_current_candidate", slow)
    try:
        result = commit_intent(authorized_material, claimed)
        assert result.write_intent_id is None
        assert result.safe_error_code.value in {
            "STATE_STALE",
            "AUTHORIZATION_EXPIRED",
            "APPROVAL_EXPIRED",
        }
    except ProductError as error:
        assert error.code == "REMEDIATION_LEASE_LOST"
    with repository.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_write_intents"
            ).fetchone()[0]
            == 0
        )


def test_final_intent_gate_rechecks_after_snapshot_work(
    authorized_material, monkeypatch
):
    from ecomsre.product.errors import ProductError

    api, _, _, _, repository = authorized_material
    claimed = claim(authorized_material)
    original = repository._snapshot

    def slow(*args, **kwargs):
        value = original(*args, **kwargs)
        api[3][0] += timedelta(seconds=31)
        return value

    monkeypatch.setattr(repository, "_snapshot", slow)
    with pytest.raises(ProductError) as error:
        commit_intent(authorized_material, claimed)
    assert error.value.code == "REMEDIATION_LEASE_LOST"
    with repository.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM remediation_write_intents"
            ).fetchone()[0]
            == 0
        )


def test_consumed_approval_public_status_is_not_active(authorized_material):
    api, _, approval, _, _ = authorized_material
    create(authorized_material)
    response = api[0].get("/v1/remediation-approvals/" + approval["approval_id"])
    assert response.json()["status"] == "CONSUMED"
    assert response.json()["action_authority"] == "NONE"


@pytest.mark.parametrize("corruption", ["last_event", "all_events", "old_revision"])
def test_trace_requires_current_tail_and_resolvable_history(
    authorized_material, corruption
):
    from ecomsre.product.errors import ProductError

    repository = authorized_material[4]
    claimed = claim(authorized_material)
    with repository.store.connect() as connection:
        if corruption == "last_event":
            connection.execute(
                "DELETE FROM remediation_decision_trace_events WHERE ordinal = (SELECT max(ordinal) FROM remediation_decision_trace_events)"
            )
        elif corruption == "all_events":
            connection.execute("DELETE FROM remediation_decision_trace_events")
        else:
            connection.execute(
                "DELETE FROM remediation_attempt_revisions WHERE revision = 0"
            )
    with pytest.raises(ProductError) as error:
        repository.trace(claimed.attempt_id)
    assert error.value.code == "REMEDIATION_TRACE_BINDING_MISMATCH"


def test_reconciliation_records_read_only_evidence_reference(authorized_material):
    api, _, _, provider, repository = authorized_material
    committed = commit_intent(authorized_material, claim(authorized_material))
    provider.changes.update(
        current_configuration_digest=provider.binding.baseline_configuration_digest,
        fault_still_present=False,
    )
    api[3][0] += timedelta(seconds=31)
    result = repository.reconcile_expired_intent(committed.attempt_id)
    event = repository.trace(result.attempt_id)[-1]
    assert len(event.evidence_refs) == 1
    observed = repository._observation(event.evidence_refs[0])
    assert (
        observed.current_configuration_digest
        == provider.binding.baseline_configuration_digest
    )
    assert result.state.value == "OUTCOME_UNKNOWN"


def test_removed_active_product_baseline_denies_with_baseline_reason(
    authorized_material,
):
    repository = authorized_material[4]
    with repository.store.connect() as connection:
        connection.execute("UPDATE baseline_versions SET active = 0")
    denied = create(authorized_material).json()
    assert denied["safe_error_code"] == "BASELINE_MISMATCH"
    assert denied["authorization_id"] is None


def test_attempt_conflicting_idempotency_and_unsupported_transition(
    authorized_material,
):
    from ecomsre.product.errors import ProductError
    from ecomsre.product.remediation.attempt_contracts import AttemptStateV1

    api, item, _, _, repository = authorized_material
    attempt = create(authorized_material).json()
    second = approve(api, item, key="different-approval").json()
    response = api[0].post(
        f"/v1/remediation-candidates/{item['candidate_id']}/attempts",
        json={"approval_id": second["approval_id"]},
        headers=headers("attempt-1"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    with repository.store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ProductError) as error:
            repository._update(
                connection,
                repository._read(connection, attempt["attempt_id"]),
                state=AttemptStateV1.EXECUTING,
                write_intent_id="intent-" + "0" * 24,
                write_intent_sha256="0" * 64,
            )
        assert error.value.code == "REMEDIATION_TRANSITION_DENIED"


def test_extension_v1_migrates_without_rewriting_prior_rows(material):
    from ecomsre.product.remediation.migrations import STATEMENTS, MIGRATION_SHA256
    from ecomsre.product.remediation.repository import (
        RemediationRepositoryV1,
        canonical,
    )
    from tests.product_v040.test_candidates import persist_material

    persist_material(material)
    store = material["objects"].metadata_store
    with store.connect() as connection:
        for statement in STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "CREATE TABLE remediation_schema_migrations (version INTEGER PRIMARY KEY, migration_sha256 TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO remediation_schema_migrations VALUES (1, ?)",
            (MIGRATION_SHA256,),
        )
        registry = canonical(material["registry"])
        connection.execute(
            "INSERT INTO remediation_registry_versions VALUES (?, ?)",
            (material["registry"].registry_sha256, registry),
        )
    repository = RemediationRepositoryV1(store, material["objects"])
    with repository.store.connect() as connection:
        assert (
            connection.execute("SELECT max(version) FROM schema_migrations").fetchone()[
                0
            ]
            == 9
        )
        assert (
            connection.execute(
                "SELECT max(version) FROM remediation_schema_migrations"
            ).fetchone()[0]
            == 3
        )
        assert (
            connection.execute(
                "SELECT payload_json FROM remediation_registry_versions"
            ).fetchone()[0]
            == registry
        )
        assert (
            connection.execute(
                "SELECT migration_sha256 FROM remediation_schema_migrations WHERE version = 1"
            ).fetchone()[0]
            == MIGRATION_SHA256
        )
