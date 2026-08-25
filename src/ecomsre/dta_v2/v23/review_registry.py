"""File-backed human review and non-actionable shadow registration for DTA v2.3."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import re
from typing import Any, Literal, TypeVar, cast

from pydantic import Field, StrictBool, StrictFloat, model_validator

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    ProvisionalIncidentReportV23,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


TEST_REVIEWER_V23 = "TEST_REVIEWER"


class HumanReviewDecisionV23(str, Enum):
    ACCEPT_AS_NEW = "ACCEPT_AS_NEW"
    MERGE_WITH_EXISTING = "MERGE_WITH_EXISTING"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    REJECT_AS_NOISE = "REJECT_AS_NOISE"
    SAVE_AS_INCIDENT_ONLY = "SAVE_AS_INCIDENT_ONLY"


class ReviewAnomalyProjectionV23(DtaModelV22):
    anomaly_id: str
    kind: GenericAnomalyKindV23
    source: EvidenceSourceV22
    service: str


class ReviewQueueItemV23(DtaModelV22):
    schema_version: Literal["dta-v23.review-queue-item.v1"]
    report: ProvisionalIncidentReportV23
    source_case_id: str
    residual_anomalies: tuple[ReviewAnomalyProjectionV23, ...]
    queued_at: datetime
    automated_fixture: StrictBool
    queue_item_sha256: str

    @model_validator(mode="after")
    def require_item(self) -> "ReviewQueueItemV23":
        _require_utc(self.queued_at, "review queue timestamp")
        ids = tuple(item.anomaly_id for item in self.residual_anomalies)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("review queue anomalies are not canonical")
        if not set(self.report.unexplained_anomaly_ids).issubset(ids):
            raise ValueError("review queue lacks a report anomaly")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"queue_item_sha256"})
        )
        if self.queue_item_sha256 != expected:
            raise ValueError("review queue item digest differs")
        return self


class HumanReviewRecordV23(DtaModelV22):
    schema_version: Literal["dta-v23.human-review-record.v1"]
    review_record_id: str = Field(pattern=r"^review-v23-[0-9a-f]{16}$")
    report_id: str
    decision: HumanReviewDecisionV23
    reviewer: str = Field(min_length=1, max_length=120)
    review_note: str = Field(min_length=1, max_length=2000)
    canonical_label: str | None
    merge_target: str | None
    requested_observations: tuple[str, ...] = Field(max_length=8)
    reviewed_at: datetime
    simulation: StrictBool
    review_sha256: str

    @model_validator(mode="after")
    def require_record(self) -> "HumanReviewRecordV23":
        _require_utc(self.reviewed_at, "review timestamp")
        if self.simulation != (self.reviewer == TEST_REVIEWER_V23):
            raise ValueError("review simulation marker differs from reviewer")
        if self.decision is HumanReviewDecisionV23.ACCEPT_AS_NEW:
            if self.canonical_label is None or self.merge_target is not None:
                raise ValueError("ACCEPT_AS_NEW requires only a canonical label")
            _require_slug(self.canonical_label)
            if self.requested_observations:
                raise ValueError("ACCEPT_AS_NEW cannot request observations")
        elif self.decision is HumanReviewDecisionV23.MERGE_WITH_EXISTING:
            if (
                self.merge_target is None
                or not self.merge_target.startswith("shadow-v23-")
                or self.canonical_label is not None
                or self.requested_observations
            ):
                raise ValueError("MERGE_WITH_EXISTING fields differ")
        elif self.decision is HumanReviewDecisionV23.REQUEST_MORE_EVIDENCE:
            if (
                not self.requested_observations
                or self.canonical_label is not None
                or self.merge_target is not None
            ):
                raise ValueError("REQUEST_MORE_EVIDENCE fields differ")
        elif (
            self.canonical_label is not None
            or self.merge_target is not None
            or self.requested_observations
        ):
            raise ValueError("non-registration review carries registration fields")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("review record digest differs")
        return self


class ShadowFaultEntryV23(DtaModelV22):
    schema_version: Literal["dta-v23.shadow-fault-entry.v1"]
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    status: Literal["SHADOW"]
    canonical_label: str
    broad_fault_domain: ProvisionalFaultDomainV23
    symptom_signature: tuple[str, ...]
    generic_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    required_evidence_sources: tuple[EvidenceSourceV22, ...]
    root_service_roles: tuple[str, ...]
    distinguishing_features: tuple[str, ...]
    confusable_known_mechanisms: tuple[MechanismV22, ...]
    positive_report_ids: tuple[str, ...]
    review_record_id: str
    remediation_authority: Literal["NONE"]
    entry_sha256: str

    @model_validator(mode="after")
    def require_entry(self) -> "ShadowFaultEntryV23":
        _require_slug(self.canonical_label)
        for values, label in (
            (self.symptom_signature, "symptoms"),
            (self.root_service_roles, "root roles"),
            (self.distinguishing_features, "distinguishing features"),
            (self.positive_report_ids, "positive reports"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"shadow {label} are not canonical")
        if self.generic_anomaly_kinds != tuple(
            sorted(set(self.generic_anomaly_kinds), key=lambda item: item.value)
        ):
            raise ValueError("shadow anomaly kinds are not canonical")
        if self.required_evidence_sources != tuple(
            sorted(set(self.required_evidence_sources), key=lambda item: item.value)
        ):
            raise ValueError("shadow sources are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("shadow entry digest differs")
        return self


class ShadowFaultRegistryV23(DtaModelV22):
    schema_version: Literal["dta-v23.shadow-fault-registry.v1"]
    entries: tuple[ShadowFaultEntryV23, ...]
    registry_sha256: str

    @classmethod
    def empty(cls) -> "ShadowFaultRegistryV23":
        payload: dict[str, Any] = {
            "schema_version": "dta-v23.shadow-fault-registry.v1",
            "entries": (),
        }
        return cls.model_validate(
            {**payload, "registry_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_registry(self) -> "ShadowFaultRegistryV23":
        ids = tuple(item.shadow_fault_id for item in self.entries)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("shadow registry entries are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("shadow registry digest differs")
        return self


class RegistrationDraftV23(DtaModelV22):
    schema_version: Literal["dta-v23.registration-draft.v1"]
    proposed_mechanism_slug: str
    broad_fault_domain: ProvisionalFaultDomainV23
    human_definition: str = Field(min_length=1, max_length=2000)
    candidate_generic_anomalies: tuple[GenericAnomalyKindV23, ...]
    candidate_evidence_sources: tuple[EvidenceSourceV22, ...]
    candidate_support_clause_description: str = Field(min_length=1, max_length=2000)
    distinguishing_negative_examples: tuple[str, ...]
    positive_case_ids: tuple[str, ...]
    required_replay_tests: tuple[str, ...]
    suggested_formal_files: tuple[str, ...]
    remediation_registration: Literal["NOT_INCLUDED"]
    draft_sha256: str

    @model_validator(mode="after")
    def require_draft(self) -> "RegistrationDraftV23":
        _require_slug(self.proposed_mechanism_slug)
        for values, label in (
            (self.distinguishing_negative_examples, "negative examples"),
            (self.positive_case_ids, "positive cases"),
            (self.required_replay_tests, "replay tests"),
            (self.suggested_formal_files, "formal files"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"registration {label} are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"draft_sha256"})
        )
        if self.draft_sha256 != expected:
            raise ValueError("registration draft digest differs")
        return self


class ShadowMatchV23(DtaModelV22):
    schema_version: Literal["dta-v23.shadow-match.v1"]
    terminal: Literal["MATCHED_EXPERIMENTAL_FAULT"]
    shadow_fault_id: str
    match_score: StrictFloat = Field(ge=0.0, le=1.0)
    matching_features: tuple[str, ...]
    action_authority: Literal["NONE"]


class ReviewDecisionResultV23(DtaModelV22):
    review: HumanReviewRecordV23
    shadow_entry: ShadowFaultEntryV23 | None
    registration_draft: RegistrationDraftV23 | None


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")


def _require_slug(value: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value):
        raise ValueError("registration label is not a canonical slug")


_ModelT = TypeVar("_ModelT", bound=DtaModelV22)
_SetT = TypeVar("_SetT")


def _hashed_model(
    model: type[_ModelT],
    payload: dict[str, Any],
    field: str,
) -> _ModelT:
    factory = cast(Any, model)
    draft = factory.model_construct(**payload, **{field: "0" * 64})
    return model.model_validate(
        {
            **payload,
            field: semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={field})
            ),
        }
    )


def build_review_queue_item_v23(
    *,
    report: ProvisionalIncidentReportV23,
    graph: ResidualEvidenceGraphV23,
    source_case_id: str,
    queued_at: datetime,
    automated_fixture: bool,
) -> ReviewQueueItemV23:
    by_id = {item.anomaly_id: item for item in graph.generic_anomalies}
    projections = tuple(
        sorted(
            (
                ReviewAnomalyProjectionV23(
                    anomaly_id=anomaly_id,
                    kind=by_id[anomaly_id].kind,
                    source=by_id[anomaly_id].source,
                    service=by_id[anomaly_id].service,
                )
                for anomaly_id in report.unexplained_anomaly_ids
            ),
            key=lambda item: item.anomaly_id,
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.review-queue-item.v1",
        "report": report,
        "source_case_id": source_case_id,
        "residual_anomalies": projections,
        "queued_at": queued_at,
        "automated_fixture": automated_fixture,
    }
    return _hashed_model(ReviewQueueItemV23, payload, "queue_item_sha256")


def build_human_review_record_v23(
    *,
    report: ProvisionalIncidentReportV23,
    decision: HumanReviewDecisionV23,
    reviewer: str,
    review_note: str,
    canonical_label: str | None,
    merge_target: str | None,
    requested_observations: tuple[str, ...],
    reviewed_at: datetime,
) -> HumanReviewRecordV23:
    identity = {
        "report_id": report.report_id,
        "decision": decision.value,
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at.isoformat(),
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.human-review-record.v1",
        "review_record_id": f"review-v23-{semantic_sha256_v22(identity)[:16]}",
        "report_id": report.report_id,
        "decision": decision,
        "reviewer": reviewer.strip(),
        "review_note": review_note.strip(),
        "canonical_label": canonical_label.strip() if canonical_label else None,
        "merge_target": merge_target,
        "requested_observations": tuple(
            sorted(set(item.strip() for item in requested_observations if item.strip()))
        ),
        "reviewed_at": reviewed_at,
        "simulation": reviewer.strip() == TEST_REVIEWER_V23,
    }
    return _hashed_model(HumanReviewRecordV23, payload, "review_sha256")


def _build_shadow_entry(
    *,
    item: ReviewQueueItemV23,
    review: HumanReviewRecordV23,
) -> ShadowFaultEntryV23:
    assert review.canonical_label is not None
    kinds = tuple(
        sorted({value.kind for value in item.residual_anomalies}, key=lambda value: value.value)
    )
    sources = tuple(
        sorted({value.source for value in item.residual_anomalies}, key=lambda value: value.value)
    )
    identity = {
        "canonical_label": review.canonical_label,
        "broad_fault_domain": item.report.broad_fault_domain.value,
        "generic_anomaly_kinds": tuple(value.value for value in kinds),
        "required_evidence_sources": tuple(value.value for value in sources),
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.shadow-fault-entry.v1",
        "shadow_fault_id": f"shadow-v23-{semantic_sha256_v22(identity)[:16]}",
        "status": "SHADOW",
        "canonical_label": review.canonical_label,
        "broad_fault_domain": item.report.broad_fault_domain,
        "symptom_signature": tuple(sorted(set(item.report.observed_symptoms))),
        "generic_anomaly_kinds": kinds,
        "required_evidence_sources": sources,
        "root_service_roles": item.report.suspected_root_services,
        "distinguishing_features": tuple(
            sorted(
                {
                    item.report.mechanism_description,
                    *item.report.observed_symptoms,
                }
            )
        ),
        "confusable_known_mechanisms": (),
        "positive_report_ids": (item.report.report_id,),
        "review_record_id": review.review_record_id,
        "remediation_authority": "NONE",
    }
    return _hashed_model(ShadowFaultEntryV23, payload, "entry_sha256")


def _rebuild_shadow_entry(
    entry: ShadowFaultEntryV23,
    *,
    positive_report_ids: tuple[str, ...],
) -> ShadowFaultEntryV23:
    payload = entry.model_dump(mode="python", exclude={"entry_sha256"})
    payload["positive_report_ids"] = tuple(sorted(set(positive_report_ids)))
    return _hashed_model(ShadowFaultEntryV23, payload, "entry_sha256")


def _build_registration_draft(
    *,
    item: ReviewQueueItemV23,
    review: HumanReviewRecordV23,
) -> RegistrationDraftV23:
    assert review.canonical_label is not None
    kinds = tuple(
        sorted({value.kind for value in item.residual_anomalies}, key=lambda value: value.value)
    )
    sources = tuple(
        sorted({value.source for value in item.residual_anomalies}, key=lambda value: value.value)
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.registration-draft.v1",
        "proposed_mechanism_slug": review.canonical_label,
        "broad_fault_domain": item.report.broad_fault_domain,
        "human_definition": (
            f"{item.report.mechanism_description} Human review note: {review.review_note}"
        ),
        "candidate_generic_anomalies": kinds,
        "candidate_evidence_sources": sources,
        "candidate_support_clause_description": (
            "Require corroborating generic anomalies from the listed evidence sources; "
            "the exact formal predicate clause remains a later human-led decision."
        ),
        "distinguishing_negative_examples": tuple(
            sorted(set(item.report.alternative_hypotheses))
        ),
        "positive_case_ids": (item.source_case_id,),
        "required_replay_tests": (
            "negative-confusable-control",
            "positive-replay-with-valid-evidence-refs",
        ),
        "suggested_formal_files": (
            "src/ecomsre/dta_v2/v22/predicates.py",
            "tests/dta_v22/test_v22_memory_predicates_diagnosis.py",
        ),
        "remediation_registration": "NOT_INCLUDED",
    }
    return _hashed_model(RegistrationDraftV23, payload, "draft_sha256")


def _tokens(*values: str) -> set[str]:
    return {
        token
        for value in values
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) >= 3
    }


def _jaccard(left: set[_SetT], right: set[_SetT]) -> float:
    return len(left & right) / len(left | right) if left or right else 0.0


def _match_shadow_registry(
    *,
    report: ProvisionalIncidentReportV23,
    registry: ShadowFaultRegistryV23,
    report_kinds: set[GenericAnomalyKindV23],
    report_sources: set[EvidenceSourceV22],
) -> tuple[ShadowMatchV23, ...]:
    report_roots = set(report.suspected_root_services)
    report_tokens = _tokens(
        report.provisional_mechanism_label,
        report.mechanism_description,
        *report.observed_symptoms,
    )
    matches: list[ShadowMatchV23] = []
    for entry in registry.entries:
        domain = float(entry.broad_fault_domain is report.broad_fault_domain)
        anomaly = _jaccard(set(entry.generic_anomaly_kinds), report_kinds)
        source = _jaccard(set(entry.required_evidence_sources), report_sources)
        root = _jaccard(set(entry.root_service_roles), report_roots)
        token = _jaccard(
            _tokens(entry.canonical_label, *entry.distinguishing_features),
            report_tokens,
        )
        score = round(
            0.30 * domain + 0.25 * anomaly + 0.20 * source + 0.10 * root + 0.15 * token,
            6,
        )
        features = tuple(
            label
            for label, value in (
                ("BROAD_DOMAIN", domain),
                ("ANOMALY_KIND_JACCARD", anomaly),
                ("SOURCE_JACCARD", source),
                ("ROOT_OR_SERVICE_ROLE_OVERLAP", root),
                ("LABEL_DESCRIPTION_TOKEN_OVERLAP", token),
            )
            if value > 0
        )
        matches.append(
            ShadowMatchV23(
                schema_version="dta-v23.shadow-match.v1",
                terminal="MATCHED_EXPERIMENTAL_FAULT",
                shadow_fault_id=entry.shadow_fault_id,
                match_score=score,
                matching_features=features,
                action_authority="NONE",
            )
        )
    return tuple(
        sorted(matches, key=lambda item: (-item.match_score, item.shadow_fault_id))[:3]
    )


def match_shadow_registry_v23(
    *,
    report: ProvisionalIncidentReportV23,
    graph: ResidualEvidenceGraphV23,
    registry: ShadowFaultRegistryV23,
) -> tuple[ShadowMatchV23, ...]:
    by_id = {item.anomaly_id: item for item in graph.generic_anomalies}
    report_anomalies = tuple(
        by_id[item]
        for item in report.unexplained_anomaly_ids
        if item in by_id
    )
    return _match_shadow_registry(
        report=report,
        registry=registry,
        report_kinds={item.kind for item in report_anomalies},
        report_sources={item.source for item in report_anomalies},
    )


def match_shadow_queue_item_v23(
    *,
    item: ReviewQueueItemV23,
    registry: ShadowFaultRegistryV23,
) -> tuple[ShadowMatchV23, ...]:
    return _match_shadow_registry(
        report=item.report,
        registry=registry,
        report_kinds={value.kind for value in item.residual_anomalies},
        report_sources={value.source for value in item.residual_anomalies},
    )


def match_shadow_report_v23(
    *,
    report: ProvisionalIncidentReportV23,
    registry: ShadowFaultRegistryV23,
) -> tuple[ShadowMatchV23, ...]:
    """Match a short report when no queue-side anomaly projection is available."""

    return _match_shadow_registry(
        report=report,
        registry=registry,
        report_kinds=set(),
        report_sources=set(),
    )


def _registry(entries: tuple[ShadowFaultEntryV23, ...]) -> ShadowFaultRegistryV23:
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.shadow-fault-registry.v1",
        "entries": tuple(sorted(entries, key=lambda item: item.shadow_fault_id)),
    }
    return _hashed_model(ShadowFaultRegistryV23, payload, "registry_sha256")


class LocalReviewStoreV23:
    """A project-local JSON store. It has no database or external write path."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.reports_dir = self.root / "reports"
        self.reviews_dir = self.root / "reviews"
        self.drafts_dir = self.root / "registration-drafts"
        self.registry_path = self.root / "shadow-registry.json"

    def _prepare(self) -> None:
        for path in (self.reports_dir, self.reviews_dir, self.drafts_dir):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_bound(path: Path, value: DtaModelV22) -> None:
        rendered = value.model_dump_json(indent=2) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != rendered:
                raise ValueError(f"local review artifact already differs: {path.name}")
            return
        path.write_text(rendered, encoding="utf-8")

    def enqueue(self, item: ReviewQueueItemV23) -> Path:
        self._prepare()
        path = self.reports_dir / f"{item.report.report_id}.json"
        self._write_bound(path, item)
        return path

    def list_report_ids(self) -> tuple[str, ...]:
        if not self.reports_dir.exists():
            return ()
        return tuple(sorted(path.stem for path in self.reports_dir.glob("report-v23-*.json")))

    def load_item(self, report_id: str) -> ReviewQueueItemV23:
        if not re.fullmatch(r"report-v23-[0-9a-f]{16}", report_id):
            raise ValueError("review report ID is invalid")
        path = self.reports_dir / f"{report_id}.json"
        if not path.is_file():
            raise ValueError("review report is absent")
        return ReviewQueueItemV23.model_validate_json(path.read_bytes())

    def load_registry(self) -> ShadowFaultRegistryV23:
        if not self.registry_path.is_file():
            return ShadowFaultRegistryV23.empty()
        return ShadowFaultRegistryV23.model_validate_json(
            self.registry_path.read_bytes()
        )

    def _save_registry(self, registry: ShadowFaultRegistryV23) -> None:
        self._prepare()
        self.registry_path.write_text(
            registry.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def decide(
        self,
        *,
        report_id: str,
        decision: HumanReviewDecisionV23,
        reviewer: str,
        review_note: str,
        canonical_label: str | None,
        merge_target: str | None,
        requested_observations: tuple[str, ...],
        reviewed_at: datetime,
    ) -> ReviewDecisionResultV23:
        item = self.load_item(report_id)
        if item.automated_fixture and reviewer != TEST_REVIEWER_V23:
            raise ValueError("automated review fixture requires TEST_REVIEWER")
        review = build_human_review_record_v23(
            report=item.report,
            decision=decision,
            reviewer=reviewer,
            review_note=review_note,
            canonical_label=canonical_label,
            merge_target=merge_target,
            requested_observations=requested_observations,
            reviewed_at=reviewed_at,
        )
        self._prepare()
        self._write_bound(
            self.reviews_dir / f"{review.review_record_id}.json",
            review,
        )
        registry = self.load_registry()
        shadow: ShadowFaultEntryV23 | None = None
        draft: RegistrationDraftV23 | None = None
        if decision is HumanReviewDecisionV23.ACCEPT_AS_NEW:
            shadow = _build_shadow_entry(item=item, review=review)
            if any(value.shadow_fault_id == shadow.shadow_fault_id for value in registry.entries):
                raise ValueError("accepted shadow entry already exists")
            registry = _registry((*registry.entries, shadow))
            draft = _build_registration_draft(item=item, review=review)
            self._write_bound(
                self.drafts_dir / f"{draft.proposed_mechanism_slug}.json",
                draft,
            )
            self._save_registry(registry)
        elif decision is HumanReviewDecisionV23.MERGE_WITH_EXISTING:
            existing = next(
                (value for value in registry.entries if value.shadow_fault_id == merge_target),
                None,
            )
            if existing is None:
                raise ValueError("merge target is absent from shadow registry")
            shadow = _rebuild_shadow_entry(
                existing,
                positive_report_ids=(
                    *existing.positive_report_ids,
                    item.report.report_id,
                ),
            )
            registry = _registry(
                tuple(
                    shadow if value.shadow_fault_id == shadow.shadow_fault_id else value
                    for value in registry.entries
                )
            )
            self._save_registry(registry)
        return ReviewDecisionResultV23(
            review=review,
            shadow_entry=shadow,
            registration_draft=draft,
        )


__all__ = (
    "HumanReviewDecisionV23",
    "HumanReviewRecordV23",
    "LocalReviewStoreV23",
    "RegistrationDraftV23",
    "ReviewAnomalyProjectionV23",
    "ReviewDecisionResultV23",
    "ReviewQueueItemV23",
    "ShadowFaultEntryV23",
    "ShadowFaultRegistryV23",
    "ShadowMatchV23",
    "TEST_REVIEWER_V23",
    "build_human_review_record_v23",
    "build_review_queue_item_v23",
    "match_shadow_queue_item_v23",
    "match_shadow_report_v23",
    "match_shadow_registry_v23",
)
