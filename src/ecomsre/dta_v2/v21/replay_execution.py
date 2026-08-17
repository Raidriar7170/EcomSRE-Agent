"""Replay-only executor and verifier contracts; this module performs no live write."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import model_validator

from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    ActionProposalV21,
    DtaModelV21,
    ExecutionBackendV21,
    RunbookBackendV21,
    RunbookIdV21,
    RunbookSpecV21,
    RunbookStepIdV21,
    Sha256V21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


class BackendAdmissionV21(DtaModelV21):
    schema_version: Literal["dta-v21.backend-admission.v1"]
    runbook_id: RunbookIdV21
    runbook_sha256: Sha256V21
    requested_backend: ExecutionBackendV21
    admitted: bool
    reason: Literal[
        "REPLAY_BACKEND_ADMITTED",
        "REPLAY_ONLY_RUNBOOK_DENIED_FOR_LIVE",
        "LIVE_BACKEND_ELIGIBLE_REQUIRES_LATER_OPERATIONAL_AUTHORITY",
    ]
    admission_sha256: Sha256V21

    @model_validator(mode="after")
    def require_decision_and_digest(self) -> BackendAdmissionV21:
        expected_admitted = self.reason != "REPLAY_ONLY_RUNBOOK_DENIED_FOR_LIVE"
        if self.admitted is not expected_admitted:
            raise ValueError("backend admission reason and decision differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("backend admission digest does not bind the decision")
        return self


class ReplayExecutionReceiptV21(DtaModelV21):
    schema_version: Literal["dta-v21.replay-execution-receipt.v1"]
    run_id: str
    proposal_sha256: Sha256V21
    runbook_id: RunbookIdV21
    runbook_sha256: Sha256V21
    target_service: str
    executor_id: str
    ordered_steps: tuple[RunbookStepIdV21, ...]
    status: Literal["COMPLETED"]
    no_live_mutation: Literal[True]
    receipt_sha256: Sha256V21

    @model_validator(mode="after")
    def require_digest(self) -> ReplayExecutionReceiptV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("replay receipt digest does not bind the receipt")
        return self


class ReplayVerificationV21(DtaModelV21):
    schema_version: Literal["dta-v21.replay-verification.v1"]
    receipt_sha256: Sha256V21
    verifier_id: str
    verified: Literal[True]
    no_live_mutation: Literal[True]
    verification_sha256: Sha256V21

    @model_validator(mode="after")
    def require_digest(self) -> ReplayVerificationV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"verification_sha256"})
        )
        if self.verification_sha256 != expected:
            raise ValueError("replay verification digest does not bind the result")
        return self


class ReplayExecutorV21(Protocol):
    def execute(
        self, *, proposal: ActionProposalV21, runbook: RunbookSpecV21
    ) -> ReplayExecutionReceiptV21: ...


class ReplayVerifierV21(Protocol):
    def verify(
        self, *, receipt: ReplayExecutionReceiptV21, runbook: RunbookSpecV21
    ) -> ReplayVerificationV21: ...


def admit_runbook_backend(
    *, runbook: RunbookSpecV21, requested_backend: ExecutionBackendV21
) -> BackendAdmissionV21:
    if requested_backend is ExecutionBackendV21.REPLAY:
        admitted = True
        reason = "REPLAY_BACKEND_ADMITTED"
    elif runbook.backend is RunbookBackendV21.REPLAY_ONLY:
        admitted = False
        reason = "REPLAY_ONLY_RUNBOOK_DENIED_FOR_LIVE"
    else:
        admitted = True
        reason = "LIVE_BACKEND_ELIGIBLE_REQUIRES_LATER_OPERATIONAL_AUTHORITY"
    payload: dict[str, object] = {
        "schema_version": "dta-v21.backend-admission.v1",
        "runbook_id": runbook.runbook_id,
        "runbook_sha256": runbook.semantic_sha256,
        "requested_backend": requested_backend,
        "admitted": admitted,
        "reason": reason,
    }
    digest_payload = {
        **payload,
        "runbook_id": runbook.runbook_id.value,
        "requested_backend": requested_backend.value,
    }
    return BackendAdmissionV21.model_validate(
        {**payload, "admission_sha256": semantic_sha256(digest_payload)}
    )


class FixedReplayExecutorV21:
    """Materialize a typed receipt only; never dispatch an external operation."""

    def execute(
        self, *, proposal: ActionProposalV21, runbook: RunbookSpecV21
    ) -> ReplayExecutionReceiptV21:
        if proposal.disposition is not ActionDispositionV21.EXECUTE_RUNBOOK:
            raise ValueError("replay execution requires an execute proposal")
        if runbook.backend is not RunbookBackendV21.REPLAY_ONLY:
            raise ValueError("fixed replay-only executor rejects live-allowed Runbooks")
        if proposal.runbook_id is not runbook.runbook_id:
            raise ValueError("proposal Runbook differs from the trusted registry")
        if proposal.runbook_sha256 != runbook.semantic_sha256:
            raise ValueError("proposal Runbook hash differs from the trusted registry")
        if proposal.target_service not in runbook.target_services:
            raise ValueError("proposal target is outside the trusted Runbook")
        payload: dict[str, object] = {
            "schema_version": "dta-v21.replay-execution-receipt.v1",
            "run_id": proposal.run_id,
            "proposal_sha256": proposal.proposal_sha256,
            "runbook_id": runbook.runbook_id,
            "runbook_sha256": runbook.semantic_sha256,
            "target_service": proposal.target_service,
            "executor_id": runbook.executor_id,
            "ordered_steps": tuple(item.step_id for item in runbook.forward_steps),
            "status": "COMPLETED",
            "no_live_mutation": True,
        }
        digest_payload = {
            **payload,
            "runbook_id": runbook.runbook_id.value,
            "ordered_steps": [item.step_id.value for item in runbook.forward_steps],
        }
        return ReplayExecutionReceiptV21.model_validate(
            {**payload, "receipt_sha256": semantic_sha256(digest_payload)}
        )


class FixedReplayVerifierV21:
    """Verify the fixed ordered replay receipt against the trusted Runbook."""

    def verify(
        self, *, receipt: ReplayExecutionReceiptV21, runbook: RunbookSpecV21
    ) -> ReplayVerificationV21:
        if receipt.runbook_id is not runbook.runbook_id:
            raise ValueError("receipt Runbook differs from the trusted registry")
        if receipt.runbook_sha256 != runbook.semantic_sha256:
            raise ValueError("receipt Runbook hash differs from the trusted registry")
        if receipt.executor_id != runbook.executor_id:
            raise ValueError("receipt executor differs from the trusted Runbook")
        if receipt.ordered_steps != tuple(
            item.step_id for item in runbook.forward_steps
        ):
            raise ValueError("receipt step order differs from the trusted Runbook")
        payload: dict[str, object] = {
            "schema_version": "dta-v21.replay-verification.v1",
            "receipt_sha256": receipt.receipt_sha256,
            "verifier_id": runbook.verifier_id,
            "verified": True,
            "no_live_mutation": True,
        }
        return ReplayVerificationV21.model_validate(
            {**payload, "verification_sha256": semantic_sha256(payload)}
        )


def execute_and_verify_replay_only(
    *, proposal: ActionProposalV21, registry: RunbookRegistryV21
) -> tuple[ReplayExecutionReceiptV21, ReplayVerificationV21]:
    if proposal.runbook_id is None:
        raise ValueError("replay execution requires a Runbook")
    runbook = registry.require(proposal.runbook_id)
    admission = admit_runbook_backend(
        runbook=runbook,
        requested_backend=ExecutionBackendV21.REPLAY,
    )
    if not admission.admitted:
        raise ValueError("trusted replay backend denied the Runbook")
    receipt = FixedReplayExecutorV21().execute(proposal=proposal, runbook=runbook)
    verification = FixedReplayVerifierV21().verify(receipt=receipt, runbook=runbook)
    return receipt, verification


__all__ = (
    "BackendAdmissionV21",
    "FixedReplayExecutorV21",
    "FixedReplayVerifierV21",
    "ReplayExecutionReceiptV21",
    "ReplayExecutorV21",
    "ReplayVerificationV21",
    "ReplayVerifierV21",
    "admit_runbook_backend",
    "execute_and_verify_replay_only",
)
