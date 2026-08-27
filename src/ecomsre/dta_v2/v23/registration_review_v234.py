"""Explicit human review contract for DTA v2.3.4 registration drafts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    FormalFaultRegistrationDraftV234,
    RegistrationImplementationModeV234,
    hashed_model_v234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    RegistrationDraftValidationV234,
)
from ecomsre.dta_v2.v23.review_registry import TEST_REVIEWER_V23


class OntologyDraftReviewDecisionV234(str, Enum):
    APPROVE_SHADOW_EVALUATION = "APPROVE_SHADOW_EVALUATION"
    REQUEST_DRAFT_REVISION = "REQUEST_DRAFT_REVISION"
    REJECT_REGISTRATION_DRAFT = "REJECT_REGISTRATION_DRAFT"


class OntologyDraftReviewRecordV234(DtaModelV22):
    schema_version: Literal["dta-v234.ontology-draft-review.v1"]
    review_record_id: str = Field(pattern=r"^draft-review-v234-[0-9a-f]{16}$")
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    authorization_id: str = Field(pattern=r"^authorization-v234-[0-9a-f]{16}$")
    draft_id: str
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_mode: RegistrationImplementationModeV234
    decision: OntologyDraftReviewDecisionV234
    reviewer: str = Field(min_length=1, max_length=120)
    review_note: str = Field(min_length=1, max_length=2000)
    requested_changes: tuple[str, ...] = Field(max_length=16)
    reviewed_at: datetime
    simulation: StrictBool
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_review(self) -> "OntologyDraftReviewRecordV234":
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() != timedelta(0):
            raise ValueError("draft review timestamp must be UTC")
        if self.simulation != (self.reviewer == TEST_REVIEWER_V23):
            raise ValueError("draft review simulation marker differs from reviewer")
        if self.simulation and "SIMULATED HUMAN REVIEW" not in self.review_note:
            raise ValueError("simulated draft review lacks the explicit label")
        if self.requested_changes != tuple(sorted(set(self.requested_changes))):
            raise ValueError("draft review requested changes are not canonical")
        if self.decision is OntologyDraftReviewDecisionV234.REQUEST_DRAFT_REVISION:
            if not self.requested_changes:
                raise ValueError("draft revision review requires requested changes")
        elif self.requested_changes:
            raise ValueError("non-revision draft review carries requested changes")
        if self.decision is OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION:
            if self.implementation_mode is not RegistrationImplementationModeV234.DECLARATIVE_READY:
                raise ValueError("only DECLARATIVE_READY drafts may enter shadow evaluation")
        expected_id = f"draft-review-v234-{semantic_sha256_v22({'shadow_fault_id': self.shadow_fault_id, 'authorization_id': self.authorization_id, 'draft_sha256': self.draft_sha256, 'validation_sha256': self.validation_sha256, 'decision': self.decision.value, 'reviewer': self.reviewer, 'reviewed_at': self.reviewed_at.isoformat()})[:16]}"
        if self.review_record_id != expected_id:
            raise ValueError("draft review identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("draft review digest differs")
        return self


def build_ontology_draft_review_v234(
    *,
    draft: FormalFaultRegistrationDraftV234,
    validation: RegistrationDraftValidationV234,
    decision: OntologyDraftReviewDecisionV234,
    reviewer: str,
    review_note: str,
    requested_changes: tuple[str, ...],
    reviewed_at: datetime,
) -> OntologyDraftReviewRecordV234:
    if (
        validation.draft_id != draft.draft_id
        or validation.draft_sha256 != draft.draft_sha256
        or validation.classification is not draft.implementation_mode
    ):
        raise ValueError("draft review validation differs from the draft")
    if (
        decision is OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION
        and validation.status is not DraftValidationStatusV234.VALID
    ):
        raise ValueError("draft review cannot approve an invalid validation")
    identity = {
        "shadow_fault_id": draft.shadow_fault_id,
        "authorization_id": draft.authorization_id,
        "draft_sha256": draft.draft_sha256,
        "validation_sha256": validation.validation_sha256,
        "decision": decision.value,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at.isoformat(),
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.ontology-draft-review.v1",
        "review_record_id": (
            f"draft-review-v234-{semantic_sha256_v22(identity)[:16]}"
        ),
        "shadow_fault_id": draft.shadow_fault_id,
        "authorization_id": draft.authorization_id,
        "draft_id": draft.draft_id,
        "draft_sha256": draft.draft_sha256,
        "validation_sha256": validation.validation_sha256,
        "implementation_mode": draft.implementation_mode,
        "decision": decision,
        "reviewer": reviewer,
        "review_note": review_note,
        "requested_changes": tuple(sorted(set(requested_changes))),
        "reviewed_at": reviewed_at,
        "simulation": reviewer == TEST_REVIEWER_V23,
    }
    return hashed_model_v234(OntologyDraftReviewRecordV234, payload, "review_sha256")


__all__ = (
    "OntologyDraftReviewDecisionV234",
    "OntologyDraftReviewRecordV234",
    "build_ontology_draft_review_v234",
)
