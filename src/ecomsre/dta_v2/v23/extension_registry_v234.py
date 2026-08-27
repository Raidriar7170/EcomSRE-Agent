"""Versioned Extension Ontology registry, promotion, and revocation for v2.3.4."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import os
from pathlib import Path
import tempfile
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    RegistrationImplementationModeV234,
    hashed_model_v234,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import OntologyExpansionStateV234
from ecomsre.dta_v2.v23.registration_evaluator_v234 import (
    ExtensionShadowEvaluationResultV234,
    ExtensionShadowEvaluationStatusV234,
)
from ecomsre.dta_v2.v23.registration_review_v234 import (
    OntologyDraftReviewDecisionV234,
    OntologyDraftReviewRecordV234,
    build_ontology_draft_review_v234,
)
from ecomsre.dta_v2.v23.registration_store_v234 import (
    RegistrationLifecycleTransitionV234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    RegistrationDraftValidationV234,
    promoted_extension_slug_collides_v234,
)
from ecomsre.dta_v2.v23.review_registry import TEST_REVIEWER_V23
from ecomsre.dta_v2.v23.review_registry import ShadowFaultEntryV23


class OntologyPromotionDecisionV234(str, Enum):
    PROMOTE_TO_EXTENSION_ONTOLOGY = "PROMOTE_TO_EXTENSION_ONTOLOGY"
    REJECT_PROMOTION = "REJECT_PROMOTION"
    REVOKE_EXTENSION_REGISTRATION = "REVOKE_EXTENSION_REGISTRATION"


class OntologyPromotionReviewRecordV234(DtaModelV22):
    schema_version: Literal["dta-v234.ontology-promotion-review.v1"]
    review_record_id: str = Field(pattern=r"^promotion-review-v234-[0-9a-f]{16}$")
    decision: OntologyPromotionDecisionV234
    registration_id: str
    draft_id: str
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=120)
    review_note: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime
    simulation: StrictBool
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_record(self) -> "OntologyPromotionReviewRecordV234":
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() != timedelta(0):
            raise ValueError("promotion review timestamp must be UTC")
        if self.simulation != (self.reviewer == TEST_REVIEWER_V23):
            raise ValueError("promotion simulation marker differs from reviewer")
        if self.simulation and "SIMULATED HUMAN REVIEW" not in self.review_note:
            raise ValueError("simulated promotion review lacks the explicit label")
        expected_id = f"promotion-review-v234-{semantic_sha256_v22({'decision': self.decision.value, 'registration_id': self.registration_id, 'draft_sha256': self.draft_sha256, 'compiled_sha256': self.compiled_sha256, 'shadow_result_sha256': self.shadow_result_sha256, 'reviewer': self.reviewer, 'reviewed_at': self.reviewed_at.isoformat()})[:16]}"
        if self.review_record_id != expected_id:
            raise ValueError("promotion review identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("promotion review digest differs")
        return self


class ExtensionOntologyEntryStatusV234(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class ExtensionOntologyEntryV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-ontology-entry.v1"]
    registration_id: str
    draft_id: str
    shadow_fault_id: str
    mechanism_slug: str
    broad_fault_domain: ProvisionalFaultDomainV23
    compiled_registration: CompiledFaultRegistrationV234
    test_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    promotion_review_record: OntologyPromotionReviewRecordV234
    promotion_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ExtensionOntologyEntryStatusV234
    remediation_authority: Literal["NONE"]
    revocation_record_sha256: str | None
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_entry(self) -> "ExtensionOntologyEntryV234":
        if (
            self.registration_id != self.compiled_registration.registration_id
            or self.draft_id != self.compiled_registration.source_draft_id
            or self.mechanism_slug
            != self.compiled_registration.mechanism.mechanism_slug
            or self.broad_fault_domain
            is not self.compiled_registration.mechanism.broad_fault_domain
            or self.promotion_record_sha256
            != self.promotion_review_record.review_sha256
            or self.promotion_review_record.registration_id != self.registration_id
            or self.promotion_review_record.compiled_sha256
            != self.compiled_registration.compiled_sha256
            or self.promotion_review_record.shadow_result_sha256
            != self.test_result_sha256
        ):
            raise ValueError("extension ontology entry bindings differ")
        if self.status is ExtensionOntologyEntryStatusV234.ACTIVE:
            if self.revocation_record_sha256 is not None:
                raise ValueError("active extension entry carries a revocation")
        elif self.revocation_record_sha256 is None:
            raise ValueError("revoked extension entry lacks a revocation record")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("extension ontology entry digest differs")
        return self


class ExtensionRegistryCommitV234(DtaModelV22):
    """Transition commit atomically published with the registry it authorizes."""

    schema_version: Literal["dta-v234.extension-registry-commit.v1"]
    transition_id: str = Field(pattern=r"^registry-transition-v234-[0-9a-f]{16}$")
    registration_id: str
    draft_id: str
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    from_state: Literal[
        OntologyExpansionStateV234.PROMOTION_READY,
        OntologyExpansionStateV234.PROMOTED_EXTENSION,
    ]
    to_state: Literal[
        OntologyExpansionStateV234.PROMOTED_EXTENSION,
        OntologyExpansionStateV234.REVOKED,
    ]
    draft_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extension_entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_transition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transitioned_at: datetime
    simulation: StrictBool
    transition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_commit(self) -> "ExtensionRegistryCommitV234":
        if self.transitioned_at.tzinfo is None or self.transitioned_at.utcoffset() != timedelta(0):
            raise ValueError("extension registry commit timestamp must be UTC")
        if (self.from_state, self.to_state) not in {
            (
                OntologyExpansionStateV234.PROMOTION_READY,
                OntologyExpansionStateV234.PROMOTED_EXTENSION,
            ),
            (
                OntologyExpansionStateV234.PROMOTED_EXTENSION,
                OntologyExpansionStateV234.REVOKED,
            ),
        }:
            raise ValueError("extension registry commit transition is not allowed")
        identity = {
            "registration_id": self.registration_id,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "decision_review_sha256": self.decision_review_sha256,
            "extension_entry_sha256": self.extension_entry_sha256,
            "previous_transition_sha256": self.previous_transition_sha256,
        }
        expected_id = (
            "registry-transition-v234-" + semantic_sha256_v22(identity)[:16]
        )
        if self.transition_id != expected_id:
            raise ValueError("extension registry commit identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected:
            raise ValueError("extension registry commit digest differs")
        return self


class ExtensionOntologyRegistryV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-ontology-registry.v1"]
    entries: tuple[ExtensionOntologyEntryV234, ...]
    transition_commits: tuple[ExtensionRegistryCommitV234, ...]
    registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def empty(cls) -> "ExtensionOntologyRegistryV234":
        payload: dict[str, Any] = {
            "schema_version": "dta-v234.extension-ontology-registry.v1",
            "entries": (),
            "transition_commits": (),
        }
        return hashed_model_v234(cls, payload, "registry_sha256")

    @model_validator(mode="after")
    def require_registry(self) -> "ExtensionOntologyRegistryV234":
        ids = tuple(item.registration_id for item in self.entries)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("extension ontology registry entries are not canonical")
        commit_ids = tuple(item.transition_id for item in self.transition_commits)
        if commit_ids != tuple(sorted(set(commit_ids))):
            raise ValueError("extension registry commits are not canonical")
        for entry in self.entries:
            promotions = tuple(
                item
                for item in self.transition_commits
                if item.registration_id == entry.registration_id
                and item.to_state is OntologyExpansionStateV234.PROMOTED_EXTENSION
            )
            revocations = tuple(
                item
                for item in self.transition_commits
                if item.registration_id == entry.registration_id
                and item.to_state is OntologyExpansionStateV234.REVOKED
            )
            if len(promotions) != 1:
                raise ValueError("extension entry lacks one promotion commit")
            promotion = promotions[0]
            if (
                promotion.draft_id != entry.draft_id
                or promotion.shadow_fault_id != entry.shadow_fault_id
                or promotion.draft_review_sha256
                != entry.promotion_review_record.draft_review_sha256
                or promotion.shadow_result_sha256 != entry.test_result_sha256
                or promotion.decision_review_sha256
                != entry.promotion_review_record.review_sha256
            ):
                raise ValueError("extension promotion commit bindings differ")
            if entry.status is ExtensionOntologyEntryStatusV234.ACTIVE:
                if revocations or promotion.extension_entry_sha256 != entry.entry_sha256:
                    raise ValueError("active extension registry commit differs")
            elif (
                len(revocations) != 1
                or revocations[0].extension_entry_sha256 != entry.entry_sha256
                or revocations[0].previous_transition_sha256
                != promotion.transition_sha256
                or revocations[0].decision_review_sha256
                != entry.revocation_record_sha256
            ):
                raise ValueError("revoked extension registry commit differs")
        committed_registration_ids = {
            item.registration_id for item in self.transition_commits
        }
        if committed_registration_ids != set(ids):
            raise ValueError("extension registry contains orphan transition commits")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("extension ontology registry digest differs")
        return self


def _build_registry_commit_v234(
    *,
    registration_id: str,
    draft_id: str,
    shadow_fault_id: str,
    from_state: OntologyExpansionStateV234,
    to_state: OntologyExpansionStateV234,
    draft_review_sha256: str,
    shadow_result_sha256: str,
    decision_review_sha256: str,
    extension_entry_sha256: str,
    previous_transition_sha256: str,
    transitioned_at: datetime,
    simulation: bool,
) -> ExtensionRegistryCommitV234:
    identity = {
        "registration_id": registration_id,
        "from_state": from_state.value,
        "to_state": to_state.value,
        "decision_review_sha256": decision_review_sha256,
        "extension_entry_sha256": extension_entry_sha256,
        "previous_transition_sha256": previous_transition_sha256,
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.extension-registry-commit.v1",
        "transition_id": (
            "registry-transition-v234-" + semantic_sha256_v22(identity)[:16]
        ),
        "registration_id": registration_id,
        "draft_id": draft_id,
        "shadow_fault_id": shadow_fault_id,
        "from_state": from_state,
        "to_state": to_state,
        "draft_review_sha256": draft_review_sha256,
        "shadow_result_sha256": shadow_result_sha256,
        "decision_review_sha256": decision_review_sha256,
        "extension_entry_sha256": extension_entry_sha256,
        "previous_transition_sha256": previous_transition_sha256,
        "transitioned_at": transitioned_at,
        "simulation": simulation,
    }
    return hashed_model_v234(
        ExtensionRegistryCommitV234,
        payload,
        "transition_sha256",
    )


class ExtensionOntologyTransitionRecordV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-ontology-transition.v1"]
    transition_id: str = Field(pattern=r"^extension-transition-v234-[0-9a-f]{16}$")
    draft_id: str
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    registration_id: str | None
    from_state: OntologyExpansionStateV234
    to_state: OntologyExpansionStateV234
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    draft_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    decision_review_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    extension_entry_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    previous_transition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transitioned_at: datetime
    simulation: StrictBool
    transition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_transition(self) -> "ExtensionOntologyTransitionRecordV234":
        if self.transitioned_at.tzinfo is None or self.transitioned_at.utcoffset() != timedelta(0):
            raise ValueError("extension ontology transition timestamp must be UTC")
        allowed = {
            (
                OntologyExpansionStateV234.DRAFT_VALIDATED,
                OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED,
            ),
            (
                OntologyExpansionStateV234.DRAFT_VALIDATED,
                OntologyExpansionStateV234.REJECTED,
            ),
            (
                OntologyExpansionStateV234.DRAFT_INVALID,
                OntologyExpansionStateV234.REJECTED,
            ),
            (
                OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED,
                OntologyExpansionStateV234.SHADOW_EVALUATION_FAILED,
            ),
            (
                OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED,
                OntologyExpansionStateV234.PROMOTION_READY,
            ),
            (
                OntologyExpansionStateV234.PROMOTION_READY,
                OntologyExpansionStateV234.PROMOTED_EXTENSION,
            ),
            (
                OntologyExpansionStateV234.PROMOTED_EXTENSION,
                OntologyExpansionStateV234.REVOKED,
            ),
        }
        if (self.from_state, self.to_state) not in allowed:
            raise ValueError("extension ontology transition is not allowed")
        if self.to_state in {
            OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED,
            OntologyExpansionStateV234.REJECTED,
        }:
            if any(
                value is not None
                for value in (
                    self.registration_id,
                    self.compiled_sha256,
                    self.shadow_result_sha256,
                    self.decision_review_sha256,
                    self.extension_entry_sha256,
                )
            ):
                raise ValueError("shadow-approval transition carries later artifacts")
        elif self.to_state in {
            OntologyExpansionStateV234.SHADOW_EVALUATION_FAILED,
            OntologyExpansionStateV234.PROMOTION_READY,
        }:
            if (
                self.registration_id is None
                or self.compiled_sha256 is None
                or self.shadow_result_sha256 is None
                or self.decision_review_sha256 is not None
                or self.extension_entry_sha256 is not None
            ):
                raise ValueError("shadow-result transition artifacts differ")
        elif any(
            value is None
            for value in (
                self.registration_id,
                self.compiled_sha256,
                self.shadow_result_sha256,
                self.decision_review_sha256,
                self.extension_entry_sha256,
            )
        ):
            raise ValueError("extension registry transition lacks bound artifacts")
        identity = {
            "draft_id": self.draft_id,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "previous_transition_sha256": self.previous_transition_sha256,
            "draft_review_sha256": self.draft_review_sha256,
            "shadow_result_sha256": self.shadow_result_sha256,
            "decision_review_sha256": self.decision_review_sha256,
            "extension_entry_sha256": self.extension_entry_sha256,
        }
        expected_id = (
            "extension-transition-v234-" + semantic_sha256_v22(identity)[:16]
        )
        if self.transition_id != expected_id:
            raise ValueError("extension ontology transition identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected:
            raise ValueError("extension ontology transition digest differs")
        return self


def _build_promotion_review_v234(
    *,
    decision: OntologyPromotionDecisionV234,
    compiled: CompiledFaultRegistrationV234,
    validation_sha256: str,
    draft_review_sha256: str,
    shadow_result_sha256: str,
    reviewer: str,
    review_note: str,
    reviewed_at: datetime,
) -> OntologyPromotionReviewRecordV234:
    identity = {
        "decision": decision.value,
        "registration_id": compiled.registration_id,
        "draft_sha256": compiled.source_draft_sha256,
        "compiled_sha256": compiled.compiled_sha256,
        "shadow_result_sha256": shadow_result_sha256,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at.isoformat(),
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.ontology-promotion-review.v1",
        "review_record_id": (
            f"promotion-review-v234-{semantic_sha256_v22(identity)[:16]}"
        ),
        "decision": decision,
        "registration_id": compiled.registration_id,
        "draft_id": compiled.source_draft_id,
        "draft_sha256": compiled.source_draft_sha256,
        "validation_sha256": validation_sha256,
        "compiled_sha256": compiled.compiled_sha256,
        "draft_review_sha256": draft_review_sha256,
        "shadow_result_sha256": shadow_result_sha256,
        "reviewer": reviewer,
        "review_note": review_note,
        "reviewed_at": reviewed_at,
        "simulation": reviewer == TEST_REVIEWER_V23,
    }
    return hashed_model_v234(
        OntologyPromotionReviewRecordV234,
        payload,
        "review_sha256",
    )


class LocalExtensionOntologyStoreV234:
    """Project-local versioned registry; no v2.2 or Runbook write path exists."""

    def __init__(self, root: Path, *, repository_root: Path | None = None) -> None:
        unresolved_root = root.absolute()
        if unresolved_root.parts[-2:] != (".local", "dta-v234"):
            raise ValueError("extension local root must end with .local/dta-v234")
        self.root = unresolved_root.resolve()
        if self.root.parts[-2:] != (".local", "dta-v234"):
            raise ValueError("extension local root cannot escape .local/dta-v234")
        if repository_root is None:
            self.registry_path = self.root / "extension-ontology" / "registry.json"
        else:
            resolved_repository = repository_root.resolve()
            if not (resolved_repository / ".git").exists():
                raise ValueError("extension repository root is not a Git worktree")
            self.registry_path = (
                resolved_repository
                / "config/dta-v234/extension-ontology/registry.json"
            )
        self.registry_dir = self.registry_path.parent
        self.versions_dir = self.registry_dir / "versions"
        self.reviews_dir = self.root / "promotion-reviews"
        self.draft_reviews_dir = self.root / "draft-reviews"
        self.shadow_results_dir = self.root / "shadow-evaluations"
        self.transitions_dir = self.root / "extension-ontology-transitions"

    def list_transitions(
        self, *, draft_id: str | None = None
    ) -> tuple[ExtensionOntologyTransitionRecordV234, ...]:
        if not self.transitions_dir.is_dir():
            return ()
        values = tuple(
            ExtensionOntologyTransitionRecordV234.model_validate_json(
                path.read_bytes()
            )
            for path in sorted(
                self.transitions_dir.glob("extension-transition-v234-*.json")
            )
        )
        if draft_id is None:
            return values
        return tuple(item for item in values if item.draft_id == draft_id)

    def _registration_validation_transition(
        self,
        *,
        draft_id: str,
        draft_sha256: str,
        validation_sha256: str,
        state: OntologyExpansionStateV234,
    ) -> RegistrationLifecycleTransitionV234:
        directory = self.root / "registration-lifecycle-transitions"
        matches = []
        if directory.is_dir():
            for path in sorted(directory.glob("registration-transition-v234-*.json")):
                item = RegistrationLifecycleTransitionV234.model_validate_json(
                    path.read_bytes()
                )
                if (
                    item.to_state is state
                    and item.draft_sha256 == draft_sha256
                    and item.validation_sha256 == validation_sha256
                ):
                    matches.append(item)
        if len(matches) != 1:
            raise ValueError(f"bound {state.value} transition is absent")
        if draft_id not in {
            path.stem
            for path in (self.root / "formal-registration-drafts").glob("*.json")
        }:
            raise ValueError("bound formal registration draft is absent")
        return matches[0]

    def _record_transition(
        self,
        *,
        draft_id: str,
        shadow_fault_id: str,
        registration_id: str | None,
        from_state: OntologyExpansionStateV234,
        to_state: OntologyExpansionStateV234,
        draft_sha256: str,
        validation_sha256: str,
        compiled_sha256: str | None,
        draft_review_sha256: str,
        shadow_result_sha256: str | None,
        decision_review_sha256: str | None,
        extension_entry_sha256: str | None,
        transitioned_at: datetime,
        simulation: bool,
    ) -> ExtensionOntologyTransitionRecordV234:
        existing = self.list_transitions(draft_id=draft_id)
        if any(item.to_state is to_state for item in existing):
            raise ValueError("extension ontology lifecycle stage is already recorded")
        if from_state in {
            OntologyExpansionStateV234.DRAFT_VALIDATED,
            OntologyExpansionStateV234.DRAFT_INVALID,
        }:
            previous_sha = self._registration_validation_transition(
                draft_id=draft_id,
                draft_sha256=draft_sha256,
                validation_sha256=validation_sha256,
                state=from_state,
            ).transition_sha256
        else:
            previous = tuple(item for item in existing if item.to_state is from_state)
            if len(previous) != 1:
                raise ValueError("extension ontology prior lifecycle stage is absent")
            previous_sha = previous[0].transition_sha256
        identity = {
            "draft_id": draft_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "previous_transition_sha256": previous_sha,
            "draft_review_sha256": draft_review_sha256,
            "shadow_result_sha256": shadow_result_sha256,
            "decision_review_sha256": decision_review_sha256,
            "extension_entry_sha256": extension_entry_sha256,
        }
        payload: dict[str, Any] = {
            "schema_version": "dta-v234.extension-ontology-transition.v1",
            "transition_id": (
                "extension-transition-v234-" + semantic_sha256_v22(identity)[:16]
            ),
            "draft_id": draft_id,
            "shadow_fault_id": shadow_fault_id,
            "registration_id": registration_id,
            "from_state": from_state,
            "to_state": to_state,
            "draft_sha256": draft_sha256,
            "validation_sha256": validation_sha256,
            "compiled_sha256": compiled_sha256,
            "draft_review_sha256": draft_review_sha256,
            "shadow_result_sha256": shadow_result_sha256,
            "decision_review_sha256": decision_review_sha256,
            "extension_entry_sha256": extension_entry_sha256,
            "previous_transition_sha256": previous_sha,
            "transitioned_at": transitioned_at,
            "simulation": simulation,
        }
        transition = hashed_model_v234(
            ExtensionOntologyTransitionRecordV234,
            payload,
            "transition_sha256",
        )
        self._write_once(
            self.transitions_dir / f"{transition.transition_id}.json",
            transition,
        )
        return transition

    def _require_extension_predecessor(
        self, *, draft_id: str, state: OntologyExpansionStateV234
    ) -> ExtensionOntologyTransitionRecordV234:
        matches = tuple(
            item
            for item in self.list_transitions(draft_id=draft_id)
            if item.to_state is state
        )
        if len(matches) != 1:
            raise ValueError("extension ontology prior lifecycle stage is absent")
        return matches[0]

    def load_registry(self) -> ExtensionOntologyRegistryV234:
        if not self.registry_path.is_file():
            return ExtensionOntologyRegistryV234.empty()
        return ExtensionOntologyRegistryV234.model_validate_json(
            self.registry_path.read_bytes()
        )

    def active_mechanism_slugs(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                item.mechanism_slug
                for item in self.load_registry().entries
                if item.status is ExtensionOntologyEntryStatusV234.ACTIVE
            )
        )

    def save_draft_review(
        self, review: OntologyDraftReviewRecordV234
    ) -> Path:
        path = self.draft_reviews_dir / f"{review.draft_id}.json"
        self._write_once(path, review)
        approved = (
            review.decision
            is OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION
        )
        registration_state = (
            OntologyExpansionStateV234.DRAFT_VALIDATED
            if approved
            else next(
                (
                    item.to_state
                    for item in (
                        RegistrationLifecycleTransitionV234.model_validate_json(
                            transition_path.read_bytes()
                        )
                        for transition_path in sorted(
                            (self.root / "registration-lifecycle-transitions").glob(
                                "registration-transition-v234-*.json"
                            )
                        )
                    )
                    if item.draft_sha256 == review.draft_sha256
                    and item.validation_sha256 == review.validation_sha256
                    and item.to_state
                    in {
                        OntologyExpansionStateV234.DRAFT_VALIDATED,
                        OntologyExpansionStateV234.DRAFT_INVALID,
                    }
                ),
                OntologyExpansionStateV234.DRAFT_VALIDATED,
            )
        )
        self._record_transition(
            draft_id=review.draft_id,
            shadow_fault_id=review.shadow_fault_id,
            registration_id=None,
            from_state=registration_state,
            to_state=(
                OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED
                if approved
                else OntologyExpansionStateV234.REJECTED
            ),
            draft_sha256=review.draft_sha256,
            validation_sha256=review.validation_sha256,
            compiled_sha256=None,
            draft_review_sha256=review.review_sha256,
            shadow_result_sha256=None,
            decision_review_sha256=None,
            extension_entry_sha256=None,
            transitioned_at=review.reviewed_at,
            simulation=review.simulation,
        )
        return path

    def load_draft_review(self, draft_id: str) -> OntologyDraftReviewRecordV234:
        path = self.draft_reviews_dir / f"{draft_id}.json"
        if not path.is_file():
            raise ValueError("ontology draft review is absent")
        return OntologyDraftReviewRecordV234.model_validate_json(path.read_bytes())

    def save_shadow_result(
        self, result: ExtensionShadowEvaluationResultV234
    ) -> Path:
        path = self.shadow_results_dir / f"{result.source_draft_id}.json"
        self._write_once(path, result)
        self._record_transition(
            draft_id=result.source_draft_id,
            shadow_fault_id=result.shadow_fault_id,
            registration_id=result.registration_id,
            from_state=OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED,
            to_state=(
                OntologyExpansionStateV234.PROMOTION_READY
                if result.status is ExtensionShadowEvaluationStatusV234.PROMOTION_READY
                else OntologyExpansionStateV234.SHADOW_EVALUATION_FAILED
            ),
            draft_sha256=result.source_draft_sha256,
            validation_sha256=self.load_draft_review(
                result.source_draft_id
            ).validation_sha256,
            compiled_sha256=result.source_compiled_sha256,
            draft_review_sha256=result.draft_review_sha256,
            shadow_result_sha256=result.shadow_result_sha256,
            decision_review_sha256=None,
            extension_entry_sha256=None,
            transitioned_at=result.evaluated_at,
            simulation=result.simulation,
        )
        return path

    def load_shadow_result(
        self, draft_id: str
    ) -> ExtensionShadowEvaluationResultV234:
        path = self.shadow_results_dir / f"{draft_id}.json"
        if not path.is_file():
            raise ValueError("extension shadow evaluation is absent")
        return ExtensionShadowEvaluationResultV234.model_validate_json(
            path.read_bytes()
        )

    @staticmethod
    def _write_once(path: Path, value: DtaModelV22) -> None:
        rendered = value.model_dump_json(indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
                raise ValueError(f"versioned extension artifact already differs: {path.name}")
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)

    def _write_registry(self, registry: ExtensionOntologyRegistryV234) -> None:
        rendered = registry.model_dump_json(indent=2) + "\n"
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".registry-", suffix=".json", dir=self.registry_dir
        )
        temp_path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.registry_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        self._write_once(
            self.versions_dir / f"{registry.registry_sha256}.json",
            registry,
        )

    def promote(
        self,
        *,
        compiled: CompiledFaultRegistrationV234,
        validation: RegistrationDraftValidationV234,
        draft_review: OntologyDraftReviewRecordV234,
        shadow_result: ExtensionShadowEvaluationResultV234,
        shadow: ShadowFaultEntryV23,
        decision: OntologyPromotionDecisionV234,
        reviewer: str,
        review_note: str,
        reviewed_at: datetime,
    ) -> tuple[ExtensionOntologyEntryV234, OntologyPromotionReviewRecordV234]:
        if decision is not OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY:
            raise ValueError("promotion store requires PROMOTE_TO_EXTENSION_ONTOLOGY")
        if (
            validation.status is not DraftValidationStatusV234.VALID
            or validation.classification
            is not RegistrationImplementationModeV234.DECLARATIVE_READY
            or validation.draft_id != compiled.source_draft_id
            or validation.draft_sha256 != compiled.source_draft_sha256
            or validation.validation_sha256 != compiled.source_validation_sha256
        ):
            raise ValueError("promotion validation differs from compiled registration")
        if (
            draft_review.decision
            is not OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION
            or draft_review.draft_id != compiled.source_draft_id
            or draft_review.draft_sha256 != compiled.source_draft_sha256
            or draft_review.validation_sha256 != validation.validation_sha256
            or draft_review.shadow_fault_id != shadow.shadow_fault_id
        ):
            raise ValueError("promotion lacks a bound draft approval")
        if (
            shadow_result.status
            is not ExtensionShadowEvaluationStatusV234.PROMOTION_READY
            or shadow_result.registration_id != compiled.registration_id
            or shadow_result.source_draft_id != compiled.source_draft_id
            or shadow_result.source_draft_sha256 != compiled.source_draft_sha256
            or shadow_result.source_compiled_sha256 != compiled.compiled_sha256
            or shadow_result.draft_review_sha256 != draft_review.review_sha256
            or shadow_result.shadow_fault_id != shadow.shadow_fault_id
            or shadow_result.shadow_entry_sha256 != shadow.entry_sha256
            or shadow_result.accepted_positive_report_ids
            != shadow.positive_report_ids
        ):
            raise ValueError("promotion lacks an unchanged passing shadow result")
        ready_transition = self._require_extension_predecessor(
            draft_id=compiled.source_draft_id,
            state=OntologyExpansionStateV234.PROMOTION_READY,
        )
        if (
            ready_transition.shadow_result_sha256
            != shadow_result.shadow_result_sha256
            or ready_transition.draft_review_sha256 != draft_review.review_sha256
        ):
            raise ValueError("promotion lifecycle predecessor differs")
        registry = self.load_registry()
        if any(item.registration_id == compiled.registration_id for item in registry.entries):
            raise ValueError("extension registration already exists")
        active_slugs = tuple(
            item.mechanism_slug
            for item in registry.entries
            if item.status is ExtensionOntologyEntryStatusV234.ACTIVE
        )
        if promoted_extension_slug_collides_v234(
            compiled.mechanism.mechanism_slug,
            active_slugs,
        ):
            raise ValueError("active extension mechanism collision")
        review = _build_promotion_review_v234(
            decision=decision,
            compiled=compiled,
            validation_sha256=validation.validation_sha256,
            draft_review_sha256=draft_review.review_sha256,
            shadow_result_sha256=shadow_result.shadow_result_sha256,
            reviewer=reviewer,
            review_note=review_note,
            reviewed_at=reviewed_at,
        )
        entry = hashed_model_v234(
            ExtensionOntologyEntryV234,
            {
                "schema_version": "dta-v234.extension-ontology-entry.v1",
                "registration_id": compiled.registration_id,
                "draft_id": compiled.source_draft_id,
                "shadow_fault_id": shadow.shadow_fault_id,
                "mechanism_slug": compiled.mechanism.mechanism_slug,
                "broad_fault_domain": compiled.mechanism.broad_fault_domain,
                "compiled_registration": compiled,
                "test_result_sha256": shadow_result.shadow_result_sha256,
                "promotion_review_record": review,
                "promotion_record_sha256": review.review_sha256,
                "status": ExtensionOntologyEntryStatusV234.ACTIVE,
                "remediation_authority": "NONE",
                "revocation_record_sha256": None,
            },
            "entry_sha256",
        )
        promotion_commit = _build_registry_commit_v234(
            registration_id=compiled.registration_id,
            draft_id=compiled.source_draft_id,
            shadow_fault_id=shadow.shadow_fault_id,
            from_state=OntologyExpansionStateV234.PROMOTION_READY,
            to_state=OntologyExpansionStateV234.PROMOTED_EXTENSION,
            draft_review_sha256=draft_review.review_sha256,
            shadow_result_sha256=shadow_result.shadow_result_sha256,
            decision_review_sha256=review.review_sha256,
            extension_entry_sha256=entry.entry_sha256,
            previous_transition_sha256=ready_transition.transition_sha256,
            transitioned_at=reviewed_at,
            simulation=review.simulation,
        )
        updated = hashed_model_v234(
            ExtensionOntologyRegistryV234,
            {
                "schema_version": "dta-v234.extension-ontology-registry.v1",
                "entries": tuple(
                    sorted((*registry.entries, entry), key=lambda item: item.registration_id)
                ),
                "transition_commits": tuple(
                    sorted(
                        (*registry.transition_commits, promotion_commit),
                        key=lambda item: item.transition_id,
                    )
                ),
            },
            "registry_sha256",
        )
        self._write_once(self.reviews_dir / f"{review.review_record_id}.json", review)
        self._write_registry(updated)
        return entry, review

    def revoke(
        self,
        *,
        registration_id: str,
        reviewer: str,
        review_note: str,
        reviewed_at: datetime,
    ) -> tuple[ExtensionOntologyEntryV234, OntologyPromotionReviewRecordV234]:
        registry = self.load_registry()
        current = next(
            (item for item in registry.entries if item.registration_id == registration_id),
            None,
        )
        if current is None:
            raise ValueError("extension registration is absent")
        if current.status is not ExtensionOntologyEntryStatusV234.ACTIVE:
            raise ValueError("extension registration is already revoked")
        promotion_commits = tuple(
            item
            for item in registry.transition_commits
            if item.registration_id == current.registration_id
            and item.to_state is OntologyExpansionStateV234.PROMOTED_EXTENSION
        )
        if len(promotion_commits) != 1:
            raise ValueError("revocation promotion commit is absent")
        promotion_commit = promotion_commits[0]
        if promotion_commit.extension_entry_sha256 != current.entry_sha256:
            raise ValueError("revocation promotion commit differs")
        review = _build_promotion_review_v234(
            decision=OntologyPromotionDecisionV234.REVOKE_EXTENSION_REGISTRATION,
            compiled=current.compiled_registration,
            validation_sha256=current.promotion_review_record.validation_sha256,
            draft_review_sha256=current.promotion_review_record.draft_review_sha256,
            shadow_result_sha256=current.test_result_sha256,
            reviewer=reviewer,
            review_note=review_note,
            reviewed_at=reviewed_at,
        )
        payload = current.model_dump(mode="python", exclude={"entry_sha256"})
        payload.update(
            status=ExtensionOntologyEntryStatusV234.REVOKED,
            revocation_record_sha256=review.review_sha256,
        )
        revoked = hashed_model_v234(
            ExtensionOntologyEntryV234,
            payload,
            "entry_sha256",
        )
        revocation_commit = _build_registry_commit_v234(
            registration_id=current.registration_id,
            draft_id=current.draft_id,
            shadow_fault_id=current.shadow_fault_id,
            from_state=OntologyExpansionStateV234.PROMOTED_EXTENSION,
            to_state=OntologyExpansionStateV234.REVOKED,
            draft_review_sha256=current.promotion_review_record.draft_review_sha256,
            shadow_result_sha256=current.test_result_sha256,
            decision_review_sha256=review.review_sha256,
            extension_entry_sha256=revoked.entry_sha256,
            previous_transition_sha256=promotion_commit.transition_sha256,
            transitioned_at=reviewed_at,
            simulation=review.simulation,
        )
        entries = tuple(
            revoked if item.registration_id == registration_id else item
            for item in registry.entries
        )
        updated = hashed_model_v234(
            ExtensionOntologyRegistryV234,
            {
                "schema_version": "dta-v234.extension-ontology-registry.v1",
                "entries": entries,
                "transition_commits": tuple(
                    sorted(
                        (*registry.transition_commits, revocation_commit),
                        key=lambda item: item.transition_id,
                    )
                ),
            },
            "registry_sha256",
        )
        self._write_once(self.reviews_dir / f"{review.review_record_id}.json", review)
        self._write_registry(updated)
        return revoked, review


__all__ = (
    "ExtensionOntologyEntryStatusV234",
    "ExtensionOntologyEntryV234",
    "ExtensionOntologyRegistryV234",
    "ExtensionRegistryCommitV234",
    "ExtensionOntologyTransitionRecordV234",
    "LocalExtensionOntologyStoreV234",
    "OntologyDraftReviewDecisionV234",
    "OntologyDraftReviewRecordV234",
    "OntologyPromotionDecisionV234",
    "OntologyPromotionReviewRecordV234",
    "build_ontology_draft_review_v234",
)
