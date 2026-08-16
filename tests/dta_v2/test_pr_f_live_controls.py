from __future__ import annotations

import pytest

from ecomsre.dta_v2.contracts import RunbookId, semantic_sha256
from ecomsre.dta_v2.live_controls import (
    OwnedLiveControls,
    build_email_restart_mutation_proof,
)

from test_admission_policy import RUNBOOK_ROOT, current_state
from ecomsre.dta_v2.registry import load_runbook_registry


class Flags:
    def __init__(self) -> None:
        self.documents: list[object] = []

    def apply(self, document):
        self.documents.append(document)
        return semantic_sha256(document)


class Recommendation:
    def __init__(self) -> None:
        self.starts = 0

    def start(self) -> None:
        self.starts += 1


class Email:
    def __init__(self) -> None:
        self.restarts = 0

    def restart(self):
        self.restarts += 1
        return build_email_restart_mutation_proof(
            owned_container_identity="e" * 64,
            before_started_at="2026-08-16T08:30:00.000000000Z",
            after_started_at="2026-08-16T08:31:00.000000000Z",
        )


@pytest.mark.parametrize(
    ("before_started_at", "after_started_at"),
    (
        (
            "2026-08-16T08:30:00.0Z",
            "2026-08-16T08:30:00.000Z",
        ),
        (
            "2026-08-16T08:31:00.000000000Z",
            "2026-08-16T08:30:00.000000000Z",
        ),
    ),
)
def test_email_restart_proof_requires_strictly_later_docker_started_at(
    before_started_at: str,
    after_started_at: str,
) -> None:
    with pytest.raises(ValueError, match="StartedAt"):
        build_email_restart_mutation_proof(
            owned_container_identity="e" * 64,
            before_started_at=before_started_at,
            after_started_at=after_started_at,
        )


def test_owned_controls_expose_only_fixed_registry_operations() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    snapshot = current_state(registry, RunbookId.MITIGATE_MEMORY_LEAK)
    flags = Flags()
    recommendation = Recommendation()
    email = Email()
    baseline = {"flags": {"paymentFailure": {"defaultVariant": "off"}}}
    email_off = {"flags": {"emailMemoryLeak": {"defaultVariant": "off"}}}
    state_digest = semantic_sha256({"attempt": snapshot.attempt_id})
    controls = OwnedLiveControls(
        current_state=snapshot,
        flag_controller=flags,
        baseline_flag_document=baseline,
        email_disabled_flag_document=email_off,
        recommendation_controller=recommendation,
        email_controller=email,
        state_digest=lambda: state_digest,
        admitted_state_digest=state_digest,
        revalidate_before_write=lambda authorization, observed_at: None,
    )

    controls.restore_payment_configuration()
    controls.start_recommendation_service()
    controls.disable_email_leak_flag()
    controls.restart_email_service()

    assert flags.documents == [baseline, email_off]
    assert recommendation.starts == 1
    assert email.restarts == 1
    assert controls.forward_write_count == 4
    assert not hasattr(controls, "execute")
    assert not hasattr(controls, "run_command")
    assert not hasattr(controls, "mutate_service")


def test_owned_controls_copy_documents_and_count_failed_attempt_once() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    snapshot = current_state(registry, RunbookId.ROLLBACK_CONFIGURATION)

    class FailingFlags(Flags):
        def apply(self, document):
            super().apply(document)
            raise RuntimeError("unsafe implementation detail")

    document = {"flags": {"paymentFailure": {"defaultVariant": "off"}}}
    flags = FailingFlags()
    controls = OwnedLiveControls(
        current_state=snapshot,
        flag_controller=flags,
        baseline_flag_document=document,
        email_disabled_flag_document={
            "flags": {"emailMemoryLeak": {"defaultVariant": "off"}}
        },
        recommendation_controller=Recommendation(),
        email_controller=Email(),
        state_digest=lambda: "a" * 64,
        admitted_state_digest="a" * 64,
        revalidate_before_write=lambda authorization, observed_at: None,
    )
    document["flags"] = {}

    try:
        controls.restore_payment_configuration()
    except RuntimeError:
        pass

    assert flags.documents == [
        {"flags": {"paymentFailure": {"defaultVariant": "off"}}}
    ]
    assert controls.forward_write_count == 1


def test_owned_controls_reject_actual_state_that_differs_from_admission() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    snapshot = current_state(registry, RunbookId.RESTART_SERVICE)

    with pytest.raises(ValueError, match="actual live state"):
        OwnedLiveControls(
            current_state=snapshot,
            flag_controller=Flags(),
            baseline_flag_document={"flags": {"paymentFailure": {}}},
            email_disabled_flag_document={"flags": {"emailMemoryLeak": {}}},
            recommendation_controller=Recommendation(),
            email_controller=Email(),
            state_digest=lambda: "a" * 64,
            admitted_state_digest="b" * 64,
            revalidate_before_write=lambda authorization, observed_at: None,
        )
