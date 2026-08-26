"""Human-authorized ontology expansion state for DTA v2.3.4."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import re
from typing import Any, Literal, TypeVar, cast

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    CoreOntologySchemaSnapshotV234,
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    HumanReviewRecordV23,
    LocalReviewStoreV23,
    RegistrationDraftV23,
    ReviewQueueItemV23,
    ShadowFaultEntryV23,
    ShadowFaultRegistryV23,
    TEST_REVIEWER_V23,
)


class OntologyExpansionStateV234(str, Enum):
    SHADOW_ACCEPTED = "SHADOW_ACCEPTED"
    DRAFT_GENERATION_AUTHORIZED = "DRAFT_GENERATION_AUTHORIZED"
    DRAFT_GENERATED = "DRAFT_GENERATED"
    DRAFT_INVALID = "DRAFT_INVALID"
    DRAFT_VALIDATED = "DRAFT_VALIDATED"
    PATCH_RENDERED = "PATCH_RENDERED"
    SHADOW_EVALUATION_APPROVED = "SHADOW_EVALUATION_APPROVED"
    SHADOW_EVALUATION_FAILED = "SHADOW_EVALUATION_FAILED"
    PROMOTION_READY = "PROMOTION_READY"
    PROMOTED_EXTENSION = "PROMOTED_EXTENSION"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class AcceptedReportBindingV234(DtaModelV22):
    report_id: str = Field(pattern=r"^report-v23-[0-9a-f]{16}$")
    source_case_id: str
    report_sha256: str
    queue_item_sha256: str


class RegistrationSeedV234(DtaModelV22):
    """A binding adapter around the unchanged historical RegistrationDraftV23."""

    schema_version: Literal["dta-v234.registration-seed.v1"]
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    source_review_record_id: str = Field(pattern=r"^review-v23-[0-9a-f]{16}$")
    source_report_id: str = Field(pattern=r"^report-v23-[0-9a-f]{16}$")
    legacy_registration_draft: RegistrationDraftV23
    positive_report_ids: tuple[str, ...] = Field(min_length=1)
    accepted_report_bindings: tuple[AcceptedReportBindingV234, ...] = Field(
        min_length=1
    )
    remediation_registration: Literal["NOT_INCLUDED"]
    seed_sha256: str

    @model_validator(mode="after")
    def require_seed(self) -> "RegistrationSeedV234":
        if self.positive_report_ids != tuple(sorted(set(self.positive_report_ids))):
            raise ValueError("registration seed positive reports are not canonical")
        binding_ids = tuple(item.report_id for item in self.accepted_report_bindings)
        if binding_ids != self.positive_report_ids:
            raise ValueError("registration seed accepted-report bindings differ")
        if self.source_report_id not in self.positive_report_ids:
            raise ValueError("registration seed source report is not accepted")
        if self.legacy_registration_draft.remediation_registration != "NOT_INCLUDED":
            raise ValueError("registration seed includes remediation")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"seed_sha256"})
        )
        if self.seed_sha256 != expected:
            raise ValueError("registration seed digest differs")
        return self


class RegistrationGenerationAuthorizationV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-generation-authorization.v1"]
    authorization_id: str = Field(pattern=r"^authorization-v234-[0-9a-f]{16}$")
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    source_review_record_id: str = Field(pattern=r"^review-v23-[0-9a-f]{16}$")
    reviewer: str = Field(min_length=1, max_length=120)
    authorization_note: str = Field(min_length=1, max_length=2000)
    authorized_scope: Literal["FORMAL_DRAFT_ONLY"]
    authorized_at: datetime
    simulation: StrictBool
    authorization_sha256: str

    @model_validator(mode="after")
    def require_authorization(self) -> "RegistrationGenerationAuthorizationV234":
        _require_utc_v234(self.authorized_at, "authorization timestamp")
        if self.simulation != (self.reviewer == TEST_REVIEWER_V23):
            raise ValueError("authorization simulation marker differs from reviewer")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected:
            raise ValueError("registration authorization digest differs")
        return self


class OntologyExpansionTransitionRecordV234(DtaModelV22):
    schema_version: Literal["dta-v234.ontology-expansion-transition.v1"]
    transition_id: str = Field(pattern=r"^transition-v234-[0-9a-f]{16}$")
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    from_state: OntologyExpansionStateV234
    to_state: OntologyExpansionStateV234
    source_review_sha256: str
    source_report_sha256: str
    source_queue_item_sha256: str
    shadow_entry_sha256: str
    authorization_sha256: str
    registration_seed_sha256: str
    core_ontology_snapshot_sha256: str
    transitioned_at: datetime
    simulation: StrictBool
    transition_sha256: str

    @model_validator(mode="after")
    def require_transition(self) -> "OntologyExpansionTransitionRecordV234":
        _require_utc_v234(self.transitioned_at, "transition timestamp")
        if (self.from_state, self.to_state) not in _ALLOWED_TRANSITIONS_V234:
            raise ValueError("ontology expansion transition is not allowed")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected:
            raise ValueError("ontology expansion transition digest differs")
        return self


class DraftGenerationAuthorizationResultV234(DtaModelV22):
    authorization: RegistrationGenerationAuthorizationV234
    registration_seed: RegistrationSeedV234
    core_ontology_snapshot: CoreOntologySchemaSnapshotV234
    transition: OntologyExpansionTransitionRecordV234

    @model_validator(mode="after")
    def require_bound_result(self) -> "DraftGenerationAuthorizationResultV234":
        shadow_ids = {
            self.authorization.shadow_fault_id,
            self.registration_seed.shadow_fault_id,
            self.transition.shadow_fault_id,
        }
        if len(shadow_ids) != 1:
            raise ValueError("draft authorization result shadow bindings differ")
        if (
            self.authorization.source_review_record_id
            != self.registration_seed.source_review_record_id
            or self.transition.authorization_sha256
            != self.authorization.authorization_sha256
            or self.transition.registration_seed_sha256
            != self.registration_seed.seed_sha256
            or self.transition.core_ontology_snapshot_sha256
            != self.core_ontology_snapshot.snapshot_sha256
            or self.transition.simulation != self.authorization.simulation
        ):
            raise ValueError("draft authorization result semantic bindings differ")
        source_binding = next(
            item
            for item in self.registration_seed.accepted_report_bindings
            if item.report_id == self.registration_seed.source_report_id
        )
        if (
            self.transition.source_report_sha256 != source_binding.report_sha256
            or self.transition.source_queue_item_sha256
            != source_binding.queue_item_sha256
        ):
            raise ValueError("draft authorization result source-report bindings differ")
        return self


_ALLOWED_TRANSITIONS_V234 = frozenset(
    {
        (
            OntologyExpansionStateV234.SHADOW_ACCEPTED,
            OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED,
        ),
        (
            OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED,
            OntologyExpansionStateV234.DRAFT_GENERATED,
        ),
        (
            OntologyExpansionStateV234.DRAFT_GENERATED,
            OntologyExpansionStateV234.DRAFT_INVALID,
        ),
        (
            OntologyExpansionStateV234.DRAFT_GENERATED,
            OntologyExpansionStateV234.DRAFT_VALIDATED,
        ),
        (
            OntologyExpansionStateV234.DRAFT_VALIDATED,
            OntologyExpansionStateV234.PATCH_RENDERED,
        ),
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
)


_ModelT = TypeVar("_ModelT", bound=DtaModelV22)


def _require_utc_v234(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")


def _hashed_model_v234(
    model: type[_ModelT],
    payload: dict[str, Any],
    digest_field: str,
) -> _ModelT:
    factory = cast(Any, model)
    draft = factory.model_construct(**payload, **{digest_field: "0" * 64})
    rendered = draft.model_dump(mode="json", exclude={digest_field})
    return model.model_validate(
        {
            **payload,
            digest_field: semantic_sha256_v22(rendered),
        }
    )


def build_registration_seed_v234(
    *,
    shadow: ShadowFaultEntryV23,
    source_review: HumanReviewRecordV23,
    legacy_draft: RegistrationDraftV23,
    accepted_reports: tuple[ReviewQueueItemV23, ...],
) -> RegistrationSeedV234:
    if source_review.decision is not HumanReviewDecisionV23.ACCEPT_AS_NEW:
        raise ValueError("registration seed requires ACCEPT_AS_NEW")
    if shadow.review_record_id != source_review.review_record_id:
        raise ValueError("shadow fault differs from source review")
    if source_review.report_id not in shadow.positive_report_ids:
        raise ValueError("shadow fault does not bind the source review report")
    if legacy_draft.proposed_mechanism_slug != shadow.canonical_label:
        raise ValueError("legacy registration draft differs from shadow label")
    if legacy_draft.broad_fault_domain is not shadow.broad_fault_domain:
        raise ValueError("legacy registration draft differs from shadow domain")
    canonical_reports = tuple(sorted(accepted_reports, key=lambda item: item.report.report_id))
    report_ids = tuple(item.report.report_id for item in canonical_reports)
    if report_ids != shadow.positive_report_ids:
        raise ValueError("accepted report artifacts differ from shadow fault")
    bindings = tuple(
        AcceptedReportBindingV234(
            report_id=item.report.report_id,
            source_case_id=item.source_case_id,
            report_sha256=item.report.report_sha256,
            queue_item_sha256=item.queue_item_sha256,
        )
        for item in canonical_reports
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-seed.v1",
        "shadow_fault_id": shadow.shadow_fault_id,
        "source_review_record_id": source_review.review_record_id,
        "source_report_id": source_review.report_id,
        "legacy_registration_draft": legacy_draft,
        "positive_report_ids": shadow.positive_report_ids,
        "accepted_report_bindings": bindings,
        "remediation_registration": "NOT_INCLUDED",
    }
    return _hashed_model_v234(RegistrationSeedV234, payload, "seed_sha256")


def build_registration_generation_authorization_v234(
    *,
    shadow: ShadowFaultEntryV23,
    source_review: HumanReviewRecordV23,
    reviewer: str,
    authorization_note: str,
    authorized_at: datetime,
) -> RegistrationGenerationAuthorizationV234:
    reviewer = reviewer.strip()
    authorization_note = authorization_note.strip()
    if source_review.decision is not HumanReviewDecisionV23.ACCEPT_AS_NEW:
        raise ValueError("draft generation requires an ACCEPT_AS_NEW source review")
    if shadow.review_record_id != source_review.review_record_id:
        raise ValueError("draft authorization source review differs from shadow fault")
    if source_review.report_id not in shadow.positive_report_ids:
        raise ValueError("draft authorization source report differs from shadow fault")
    identity = {
        "shadow_fault_id": shadow.shadow_fault_id,
        "source_review_record_id": source_review.review_record_id,
        "reviewer": reviewer,
        "authorized_at": authorized_at.isoformat(),
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-generation-authorization.v1",
        "authorization_id": (
            f"authorization-v234-{semantic_sha256_v22(identity)[:16]}"
        ),
        "shadow_fault_id": shadow.shadow_fault_id,
        "source_review_record_id": source_review.review_record_id,
        "reviewer": reviewer,
        "authorization_note": authorization_note,
        "authorized_scope": "FORMAL_DRAFT_ONLY",
        "authorized_at": authorized_at,
        "simulation": reviewer == TEST_REVIEWER_V23,
    }
    return _hashed_model_v234(
        RegistrationGenerationAuthorizationV234,
        payload,
        "authorization_sha256",
    )


def build_draft_authorization_transition_v234(
    *,
    shadow: ShadowFaultEntryV23,
    source_review: HumanReviewRecordV23,
    source_report: ReviewQueueItemV23,
    authorization: RegistrationGenerationAuthorizationV234,
    registration_seed: RegistrationSeedV234,
    core_ontology_snapshot: CoreOntologySchemaSnapshotV234,
) -> OntologyExpansionTransitionRecordV234:
    identity = {
        "shadow_fault_id": shadow.shadow_fault_id,
        "from_state": OntologyExpansionStateV234.SHADOW_ACCEPTED.value,
        "to_state": OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED.value,
        "authorization_sha256": authorization.authorization_sha256,
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.ontology-expansion-transition.v1",
        "transition_id": f"transition-v234-{semantic_sha256_v22(identity)[:16]}",
        "shadow_fault_id": shadow.shadow_fault_id,
        "from_state": OntologyExpansionStateV234.SHADOW_ACCEPTED,
        "to_state": OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED,
        "source_review_sha256": source_review.review_sha256,
        "source_report_sha256": source_report.report.report_sha256,
        "source_queue_item_sha256": source_report.queue_item_sha256,
        "shadow_entry_sha256": shadow.entry_sha256,
        "authorization_sha256": authorization.authorization_sha256,
        "registration_seed_sha256": registration_seed.seed_sha256,
        "core_ontology_snapshot_sha256": core_ontology_snapshot.snapshot_sha256,
        "transitioned_at": authorization.authorized_at,
        "simulation": authorization.simulation,
    }
    return _hashed_model_v234(
        OntologyExpansionTransitionRecordV234,
        payload,
        "transition_sha256",
    )


class LocalOntologyExpansionStoreV234:
    """File-backed immutable authorization records under the caller's local root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.authorizations_dir = self.root / "ontology-authorizations"
        self.transitions_dir = self.root / "ontology-transitions"
        self.seeds_dir = self.root / "registration-seeds"
        self.snapshot_path = self.root / "core-ontology-snapshot.json"
        self.review_store = LocalReviewStoreV23(self.root)

    def _prepare(self) -> None:
        for path in (
            self.authorizations_dir,
            self.transitions_dir,
            self.seeds_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_bound(path: Path, value: DtaModelV22) -> None:
        rendered = value.model_dump_json(indent=2) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != rendered:
                raise ValueError(f"local ontology artifact already differs: {path.name}")
            return
        path.write_text(rendered, encoding="utf-8")

    def list_authorization_ids(self) -> tuple[str, ...]:
        if not self.authorizations_dir.exists():
            return ()
        return tuple(
            sorted(path.stem for path in self.authorizations_dir.glob("authorization-v234-*.json"))
        )

    def list_transition_ids(self) -> tuple[str, ...]:
        if not self.transitions_dir.exists():
            return ()
        return tuple(
            sorted(path.stem for path in self.transitions_dir.glob("transition-v234-*.json"))
        )

    def list_shadow_faults(self) -> tuple[ShadowFaultEntryV23, ...]:
        return self.review_store.load_registry().entries

    def load_shadow_fault(self, shadow_fault_id: str) -> ShadowFaultEntryV23:
        shadow = next(
            (
                item
                for item in self.list_shadow_faults()
                if item.shadow_fault_id == shadow_fault_id
            ),
            None,
        )
        if shadow is None:
            raise ValueError("ontology expansion shadow fault is absent")
        return shadow

    def load_authorization_result(
        self,
        authorization_id: str,
    ) -> DraftGenerationAuthorizationResultV234:
        if not re.fullmatch(r"authorization-v234-[0-9a-f]{16}", authorization_id):
            raise ValueError("ontology expansion authorization ID is invalid")
        authorization_path = self.authorizations_dir / f"{authorization_id}.json"
        if not authorization_path.is_file():
            raise ValueError("ontology expansion authorization is absent")
        authorization = RegistrationGenerationAuthorizationV234.model_validate_json(
            authorization_path.read_bytes()
        )
        seed_path = self.seeds_dir / f"{authorization.shadow_fault_id}.json"
        if not seed_path.is_file() or not self.snapshot_path.is_file():
            raise ValueError("ontology expansion authorization context is incomplete")
        seed = RegistrationSeedV234.model_validate_json(seed_path.read_bytes())
        snapshot = CoreOntologySchemaSnapshotV234.model_validate_json(
            self.snapshot_path.read_bytes()
        )
        transitions = tuple(
            transition
            for transition_id in self.list_transition_ids()
            for transition in (
                OntologyExpansionTransitionRecordV234.model_validate_json(
                    (self.transitions_dir / f"{transition_id}.json").read_bytes()
                ),
            )
            if transition.authorization_sha256 == authorization.authorization_sha256
            and transition.to_state
            is OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED
        )
        if len(transitions) != 1:
            raise ValueError("ontology expansion authorization transition is incomplete")
        return DraftGenerationAuthorizationResultV234(
            authorization=authorization,
            registration_seed=seed,
            core_ontology_snapshot=snapshot,
            transition=transitions[0],
        )

    def load_accepted_reports(
        self,
        authorization_result: DraftGenerationAuthorizationResultV234,
    ) -> tuple[ReviewQueueItemV23, ...]:
        reports = tuple(
            self.review_store.load_item(report_id)
            for report_id in authorization_result.registration_seed.positive_report_ids
        )
        bindings = {
            item.report_id: item
            for item in authorization_result.registration_seed.accepted_report_bindings
        }
        if any(
            bindings[item.report.report_id].report_sha256 != item.report.report_sha256
            or bindings[item.report.report_id].queue_item_sha256
            != item.queue_item_sha256
            for item in reports
        ):
            raise ValueError("ontology expansion accepted reports differ from seed")
        return reports

    def _load_source_review(self, review_record_id: str) -> HumanReviewRecordV23:
        path = self.review_store.reviews_dir / f"{review_record_id}.json"
        if not path.is_file():
            raise ValueError("shadow source review record is absent")
        return HumanReviewRecordV23.model_validate_json(path.read_bytes())

    def _load_legacy_draft(self, shadow: ShadowFaultEntryV23) -> RegistrationDraftV23:
        path = self.review_store.drafts_dir / f"{shadow.canonical_label}.json"
        if not path.is_file():
            raise ValueError("shadow registration seed draft is absent")
        return RegistrationDraftV23.model_validate_json(path.read_bytes())

    def _require_not_already_authorized(self, shadow_fault_id: str) -> None:
        for authorization_id in self.list_authorization_ids():
            authorization = RegistrationGenerationAuthorizationV234.model_validate_json(
                (self.authorizations_dir / f"{authorization_id}.json").read_bytes()
            )
            if authorization.shadow_fault_id == shadow_fault_id:
                raise ValueError("shadow fault is already authorized for draft generation")

    def _require_authorizable_transition_state(self, shadow_fault_id: str) -> None:
        for transition_id in self.list_transition_ids():
            transition = OntologyExpansionTransitionRecordV234.model_validate_json(
                (self.transitions_dir / f"{transition_id}.json").read_bytes()
            )
            if transition.shadow_fault_id != shadow_fault_id:
                continue
            if transition.to_state in {
                OntologyExpansionStateV234.PROMOTED_EXTENSION,
                OntologyExpansionStateV234.REVOKED,
            }:
                raise ValueError("promoted or revoked registration cannot be authorized")
            if (
                transition.to_state
                is OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED
            ):
                raise ValueError("shadow fault already has a draft-generation transition")

    def authorize_draft_generation(
        self,
        *,
        shadow_fault_id: str,
        reviewer: str,
        authorization_note: str,
        authorized_at: datetime,
    ) -> DraftGenerationAuthorizationResultV234:
        registry = ShadowFaultRegistryV23.model_validate(
            self.review_store.load_registry().model_dump(mode="python")
        )
        shadow = next(
            (item for item in registry.entries if item.shadow_fault_id == shadow_fault_id),
            None,
        )
        if shadow is None:
            raise ValueError("draft authorization shadow fault is absent")
        self._require_not_already_authorized(shadow_fault_id)
        self._require_authorizable_transition_state(shadow_fault_id)
        source_review = self._load_source_review(shadow.review_record_id)
        legacy_draft = self._load_legacy_draft(shadow)
        accepted_reports = tuple(
            self.review_store.load_item(report_id)
            for report_id in shadow.positive_report_ids
        )
        registration_seed = build_registration_seed_v234(
            shadow=shadow,
            source_review=source_review,
            legacy_draft=legacy_draft,
            accepted_reports=accepted_reports,
        )
        source_report = next(
            item
            for item in accepted_reports
            if item.report.report_id == source_review.report_id
        )
        core_snapshot = build_core_ontology_schema_snapshot_v234()
        authorization = build_registration_generation_authorization_v234(
            shadow=shadow,
            source_review=source_review,
            reviewer=reviewer,
            authorization_note=authorization_note,
            authorized_at=authorized_at,
        )
        transition = build_draft_authorization_transition_v234(
            shadow=shadow,
            source_review=source_review,
            source_report=source_report,
            authorization=authorization,
            registration_seed=registration_seed,
            core_ontology_snapshot=core_snapshot,
        )
        self._prepare()
        self._write_bound(
            self.seeds_dir / f"{shadow.shadow_fault_id}.json",
            registration_seed,
        )
        self._write_bound(self.snapshot_path, core_snapshot)
        self._write_bound(
            self.transitions_dir / f"{transition.transition_id}.json",
            transition,
        )
        # Publish the authorization last; it is the Provider-visible commit marker.
        self._write_bound(
            self.authorizations_dir / f"{authorization.authorization_id}.json",
            authorization,
        )
        return DraftGenerationAuthorizationResultV234(
            authorization=authorization,
            registration_seed=registration_seed,
            core_ontology_snapshot=core_snapshot,
            transition=transition,
        )


__all__ = (
    "AcceptedReportBindingV234",
    "DraftGenerationAuthorizationResultV234",
    "LocalOntologyExpansionStoreV234",
    "OntologyExpansionStateV234",
    "OntologyExpansionTransitionRecordV234",
    "RegistrationGenerationAuthorizationV234",
    "RegistrationSeedV234",
    "build_draft_authorization_transition_v234",
    "build_registration_generation_authorization_v234",
    "build_registration_seed_v234",
)
