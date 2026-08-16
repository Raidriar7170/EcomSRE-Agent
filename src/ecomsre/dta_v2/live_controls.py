"""Fixed trusted adapters around the existing owned Sandbox controllers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ecomsre.dta_v2.authorization import AttemptAuthorizationRecord
from ecomsre.dta_v2.contracts import DtaModel, Sha256, semantic_sha256
from ecomsre.dta_v2.operational_contracts import CurrentStateSnapshot


def _parse_docker_started_at(value: str) -> datetime:
    if value != value.strip() or not value.endswith("Z"):
        raise ValueError("Docker StartedAt must be canonical UTC text")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise ValueError("Docker StartedAt is invalid") from error
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("Docker StartedAt must be UTC")
    return parsed


class EmailRestartMutationProof(DtaModel):
    """Safe proof that one exact owned Email container really restarted."""

    schema_version: Literal["dta-v2.email-restart-mutation-proof.v1"]
    logical_service: Literal["email"]
    owned_container_identity_sha256: Sha256
    before_started_at: str = Field(min_length=20, max_length=64)
    after_started_at: str = Field(min_length=20, max_length=64)
    proof_sha256: Sha256

    @model_validator(mode="after")
    def require_mutation(self) -> EmailRestartMutationProof:
        before = _parse_docker_started_at(self.before_started_at)
        after = _parse_docker_started_at(self.after_started_at)
        if after <= before:
            raise ValueError("Email restart did not advance Docker StartedAt")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"proof_sha256"})
        )
        if self.proof_sha256 != expected:
            raise ValueError("Email restart proof digest does not bind mutation")
        return self


def build_email_restart_mutation_proof(
    *,
    owned_container_identity: str,
    before_started_at: str,
    after_started_at: str,
) -> EmailRestartMutationProof:
    if (
        len(owned_container_identity) != 64
        or any(character not in "0123456789abcdef" for character in owned_container_identity)
    ):
        raise ValueError("owned Email container identity is invalid")
    payload = {
        "schema_version": "dta-v2.email-restart-mutation-proof.v1",
        "logical_service": "email",
        "owned_container_identity_sha256": semantic_sha256(
            {"owned_container_identity": owned_container_identity}
        ),
        "before_started_at": before_started_at,
        "after_started_at": after_started_at,
    }
    return EmailRestartMutationProof.model_validate(
        {**payload, "proof_sha256": semantic_sha256(payload)}
    )


class ExactFlagControl(Protocol):
    def apply(self, document: Mapping[str, object]) -> str: ...


class RecommendationControl(Protocol):
    def start(self) -> None: ...


class EmailControl(Protocol):
    def restart(self) -> EmailRestartMutationProof: ...


class OwnedLiveControls:
    """Expose only the four Registry-owned operations used by PR-F."""

    def __init__(
        self,
        *,
        current_state: CurrentStateSnapshot,
        flag_controller: ExactFlagControl,
        baseline_flag_document: Mapping[str, object],
        email_disabled_flag_document: Mapping[str, object],
        recommendation_controller: RecommendationControl,
        email_controller: EmailControl,
        state_digest: Callable[[], str],
        admitted_state_digest: str,
        revalidate_before_write: Callable[
            [AttemptAuthorizationRecord, datetime], None
        ],
    ) -> None:
        snapshot = CurrentStateSnapshot.model_validate(
            current_state.model_dump(mode="python")
        )
        baseline = deepcopy(dict(baseline_flag_document))
        email_disabled = deepcopy(dict(email_disabled_flag_document))
        if not baseline or not email_disabled:
            raise ValueError("trusted flag documents must be non-empty")
        self.source_snapshot_sha256 = snapshot.snapshot_sha256
        self.run_id = snapshot.run_id
        self.attempt_id = snapshot.attempt_id
        self.target = snapshot.target_logical_service
        self.ownership_digest = snapshot.ownership_digest
        self.forward_write_count = snapshot.prior_forward_step_count
        self.transaction_started = False
        self._flag_controller = flag_controller
        self._baseline_flag_document = baseline
        self._email_disabled_flag_document = email_disabled
        self._recommendation_controller = recommendation_controller
        self._email_controller = email_controller
        self._state_digest = state_digest
        self._revalidate_before_write = revalidate_before_write
        self.email_restart_mutation_proof: EmailRestartMutationProof | None = None
        self.initial_state_digest = self.state_digest()
        if self.initial_state_digest != admitted_state_digest:
            raise ValueError("actual live state differs from the admitted state")

    def state_digest(self) -> str:
        digest = self._state_digest()
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("trusted control state digest is invalid")
        if self.email_restart_mutation_proof is None:
            return digest
        return semantic_sha256(
            {
                "trusted_live_state_sha256": digest,
                "email_restart_mutation_proof_sha256": (
                    self.email_restart_mutation_proof.proof_sha256
                ),
            }
        )

    def revalidate_before_write(
        self,
        authorization: AttemptAuthorizationRecord,
        observed_at: datetime,
    ) -> None:
        child = AttemptAuthorizationRecord.model_validate(
            authorization.model_dump(mode="python")
        )
        if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
            raise ValueError("write revalidation time must be UTC")
        if (
            child.run_id != self.run_id
            or child.attempt_id != self.attempt_id
            or child.target_service != self.target
            or not child.issued_at <= observed_at < child.expires_at
        ):
            raise ValueError("write revalidation binding differs")
        self._revalidate_before_write(child, observed_at)

    def _attempt(self, operation: Callable[[], object]) -> None:
        self.forward_write_count += 1
        operation()

    def restore_payment_configuration(self) -> None:
        self._attempt(
            lambda: self._flag_controller.apply(
                deepcopy(self._baseline_flag_document)
            )
        )

    def start_recommendation_service(self) -> None:
        self._attempt(self._recommendation_controller.start)

    def disable_email_leak_flag(self) -> None:
        self._attempt(
            lambda: self._flag_controller.apply(
                deepcopy(self._email_disabled_flag_document)
            )
        )

    def restart_email_service(self) -> None:
        def restart() -> None:
            proof = self._email_controller.restart()
            self.email_restart_mutation_proof = (
                EmailRestartMutationProof.model_validate(
                    proof.model_dump(mode="python")
                )
            )

        self._attempt(restart)
