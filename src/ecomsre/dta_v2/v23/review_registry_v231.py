"""Human review and legacy-compatible non-actionable shadow records for v2.3.1."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any, Literal, TypeVar, cast

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.contracts_v231 import (
    ProvisionalIncidentReportV231,
    ReportUncertaintyModeV231,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    ReviewAnomalyProjectionV23,
    ShadowFaultRegistryV23,
    TEST_REVIEWER_V23,
)


class ReviewQueueItemV231(DtaModelV22):
    schema_version: Literal["dta-v231.review-queue-item.v1"]
    report: ProvisionalIncidentReportV231
    source_case_id: str
    residual_anomalies: tuple[ReviewAnomalyProjectionV23, ...]
    unresolved_dimensions: tuple[str, ...]
    queued_at: datetime
    automated_fixture: StrictBool
    queue_item_sha256: str

    @model_validator(mode="after")
    def require_item(self) -> "ReviewQueueItemV231":
        _require_utc(self.queued_at, "v2.3.1 review queue timestamp")
        ids = tuple(item.anomaly_id for item in self.residual_anomalies)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.1 review anomalies are not canonical")
        if not set(self.report.unexplained_anomaly_ids).issubset(ids):
            raise ValueError("v2.3.1 review queue lacks a report anomaly")
        if self.unresolved_dimensions != tuple(sorted(set(self.unresolved_dimensions))):
            raise ValueError("v2.3.1 review unresolved dimensions are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"queue_item_sha256"})
        )
        if self.queue_item_sha256 != expected:
            raise ValueError("v2.3.1 review queue digest differs")
        return self


class HumanReviewRecordV231(DtaModelV22):
    schema_version: Literal["dta-v231.human-review-record.v1"]
    review_record_id: str = Field(pattern=r"^review-v231-[0-9a-f]{16}$")
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
    def require_review(self) -> "HumanReviewRecordV231":
        _require_utc(self.reviewed_at, "v2.3.1 review timestamp")
        if self.simulation != (self.reviewer == TEST_REVIEWER_V23):
            raise ValueError("v2.3.1 review simulation marker differs")
        if self.decision is HumanReviewDecisionV23.ACCEPT_AS_NEW:
            if self.canonical_label is None or self.merge_target is not None:
                raise ValueError("ACCEPT_AS_NEW requires only a canonical label")
            _require_slug(self.canonical_label)
        elif self.decision is HumanReviewDecisionV23.MERGE_WITH_EXISTING:
            if self.merge_target is None or self.canonical_label is not None:
                raise ValueError("MERGE_WITH_EXISTING requires only a merge target")
        elif self.canonical_label is not None or self.merge_target is not None:
            raise ValueError("non-registration review carries registration fields")
        if (
            self.decision is HumanReviewDecisionV23.REQUEST_MORE_EVIDENCE
            and not self.requested_observations
        ):
            raise ValueError("REQUEST_MORE_EVIDENCE lacks requested observations")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("v2.3.1 review digest differs")
        return self


class ShadowFaultEntryV231(DtaModelV22):
    schema_version: Literal["dta-v231.shadow-fault-entry.v1"]
    shadow_fault_id: str = Field(pattern=r"^shadow-v(?:23|231)-[0-9a-f]{16}$")
    status: Literal["SHADOW"]
    canonical_label: str
    broad_fault_domain: ProvisionalFaultDomainV23
    leading_hypothesis_id: str
    leading_hypothesis: str
    alternative_hypotheses: tuple[str, ...]
    unresolved_dimensions: tuple[str, ...]
    uncertainty_mode: ReportUncertaintyModeV231
    positive_report_ids: tuple[str, ...]
    review_record_id: str
    legacy_source_entry_sha256: str | None
    remediation_authority: Literal["NONE"]
    entry_sha256: str

    @model_validator(mode="after")
    def require_entry(self) -> "ShadowFaultEntryV231":
        _require_slug(self.canonical_label)
        for values, label in (
            (self.alternative_hypotheses, "alternatives"),
            (self.unresolved_dimensions, "unresolved dimensions"),
            (self.positive_report_ids, "positive reports"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.1 shadow {label} are not canonical")
        if (
            self.uncertainty_mode is ReportUncertaintyModeV231.COMPETING_HYPOTHESES
            and not self.alternative_hypotheses
        ):
            raise ValueError("v2.3.1 competing shadow lacks alternatives")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("v2.3.1 shadow entry digest differs")
        return self


class ShadowFaultRegistryV231(DtaModelV22):
    schema_version: Literal["dta-v231.shadow-fault-registry.v1"]
    entries: tuple[ShadowFaultEntryV231, ...]
    registry_sha256: str

    @model_validator(mode="after")
    def require_registry(self) -> "ShadowFaultRegistryV231":
        ids = tuple(item.shadow_fault_id for item in self.entries)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.1 shadow registry is not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("v2.3.1 shadow registry digest differs")
        return self

    @classmethod
    def empty(cls) -> "ShadowFaultRegistryV231":
        return _hashed(
            cls,
            {"schema_version": "dta-v231.shadow-fault-registry.v1", "entries": ()},
            "registry_sha256",
        )


class ReviewDecisionResultV231(DtaModelV22):
    review: HumanReviewRecordV231
    shadow_entry: ShadowFaultEntryV231 | None


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_slug(value: str) -> None:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError("shadow canonical label is not a lowercase slug")


_ModelV231 = TypeVar("_ModelV231", bound=DtaModelV22)


def _hashed(
    model_type: type[_ModelV231], payload: dict[str, Any], field: str
) -> _ModelV231:
    draft = cast(Any, model_type).model_construct(
        **payload, **{field: "0" * 64}
    )
    return model_type.model_validate(
        {
            **payload,
            field: semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={field})
            ),
        }
    )


def _unresolved_dimensions(report: ProvisionalIncidentReportV231) -> tuple[str, ...]:
    domains = {item.broad_fault_domain for item in report.competing_hypotheses}
    roots = {
        service
        for item in report.competing_hypotheses
        for service in item.suspected_root_services
    }
    values = {"CAUSAL_MECHANISM"}
    if len(domains) > 1:
        values.add("BROAD_FAULT_DOMAIN")
    if len(roots) > 1:
        values.add("ROOT_SERVICE")
    return tuple(sorted(values))


def build_review_queue_item_v231(
    *,
    report: ProvisionalIncidentReportV231,
    graph: ResidualEvidenceGraphV23,
    source_case_id: str,
    queued_at: datetime,
    automated_fixture: bool,
) -> ReviewQueueItemV231:
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
    payload = {
        "schema_version": "dta-v231.review-queue-item.v1",
        "report": report,
        "source_case_id": source_case_id,
        "residual_anomalies": projections,
        "unresolved_dimensions": _unresolved_dimensions(report),
        "queued_at": queued_at,
        "automated_fixture": automated_fixture,
    }
    return _hashed(ReviewQueueItemV231, payload, "queue_item_sha256")


def render_review_display_v231(item: ReviewQueueItemV231) -> dict[str, object]:
    report = item.report
    leading = next(
        value
        for value in report.competing_hypotheses
        if value.hypothesis_id == report.preferred_hypothesis_id
    )
    alternatives = tuple(
        value
        for value in report.competing_hypotheses
        if value.hypothesis_id != report.preferred_hypothesis_id
    )
    return {
        "report_id": report.report_id,
        "leading_hypothesis": leading.model_dump(mode="json"),
        "alternatives": [item.model_dump(mode="json") for item in alternatives],
        "shared_supporting_evidence": list(report.supporting_evidence_refs),
        "contradictions": list(report.contradicting_evidence_refs),
        "unresolved_questions": list(report.unresolved_questions),
        "unresolved_dimensions": list(item.unresolved_dimensions),
        "recommended_discriminating_reads": list(
            report.recommended_discriminating_observations
        ),
        "confidence_band": report.confidence_band.value,
        "default_recommendation": report.review_recommendation.value,
        "action_authority": "NONE",
    }


def _build_review(
    *,
    item: ReviewQueueItemV231,
    decision: HumanReviewDecisionV23,
    reviewer: str,
    review_note: str,
    canonical_label: str | None,
    merge_target: str | None,
    requested_observations: tuple[str, ...],
    reviewed_at: datetime,
) -> HumanReviewRecordV231:
    identity = {
        "report_id": item.report.report_id,
        "decision": decision.value,
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at.isoformat(),
    }
    payload = {
        "schema_version": "dta-v231.human-review-record.v1",
        "review_record_id": f"review-v231-{semantic_sha256_v22(identity)[:16]}",
        "report_id": item.report.report_id,
        "decision": decision,
        "reviewer": reviewer.strip(),
        "review_note": review_note.strip(),
        "canonical_label": canonical_label.strip() if canonical_label else None,
        "merge_target": merge_target,
        "requested_observations": tuple(
            sorted(set(value.strip() for value in requested_observations if value.strip()))
        ),
        "reviewed_at": reviewed_at,
        "simulation": reviewer.strip() == TEST_REVIEWER_V23,
    }
    return _hashed(HumanReviewRecordV231, payload, "review_sha256")


def _build_shadow(
    *,
    item: ReviewQueueItemV231,
    review: HumanReviewRecordV231,
) -> ShadowFaultEntryV231:
    assert review.canonical_label is not None
    report = item.report
    leading = next(
        value
        for value in report.competing_hypotheses
        if value.hypothesis_id == report.preferred_hypothesis_id
    )
    alternatives = tuple(
        sorted(
            f"{value.provisional_label}: {', '.join(value.suspected_root_services)}"
            for value in report.competing_hypotheses
            if value.hypothesis_id != report.preferred_hypothesis_id
        )
    )
    identity = {
        "canonical_label": review.canonical_label,
        "report_id": report.report_id,
        "leading_hypothesis_id": leading.hypothesis_id,
    }
    payload = {
        "schema_version": "dta-v231.shadow-fault-entry.v1",
        "shadow_fault_id": f"shadow-v231-{semantic_sha256_v22(identity)[:16]}",
        "status": "SHADOW",
        "canonical_label": review.canonical_label,
        "broad_fault_domain": leading.broad_fault_domain,
        "leading_hypothesis_id": leading.hypothesis_id,
        "leading_hypothesis": leading.provisional_label,
        "alternative_hypotheses": alternatives,
        "unresolved_dimensions": item.unresolved_dimensions,
        "uncertainty_mode": report.uncertainty_mode,
        "positive_report_ids": (report.report_id,),
        "review_record_id": review.review_record_id,
        "legacy_source_entry_sha256": None,
        "remediation_authority": "NONE",
    }
    return _hashed(ShadowFaultEntryV231, payload, "entry_sha256")


def decide_review_v231(
    *,
    item: ReviewQueueItemV231,
    decision: HumanReviewDecisionV23,
    reviewer: str,
    review_note: str,
    canonical_label: str | None,
    requested_observations: tuple[str, ...],
    reviewed_at: datetime,
    merge_target: str | None = None,
) -> ReviewDecisionResultV231:
    if item.automated_fixture and reviewer != TEST_REVIEWER_V23:
        raise ValueError("automated v2.3.1 review requires TEST_REVIEWER")
    review = _build_review(
        item=item,
        decision=decision,
        reviewer=reviewer,
        review_note=review_note,
        canonical_label=canonical_label,
        merge_target=merge_target,
        requested_observations=requested_observations,
        reviewed_at=reviewed_at,
    )
    shadow = (
        _build_shadow(item=item, review=review)
        if decision is HumanReviewDecisionV23.ACCEPT_AS_NEW
        else None
    )
    return ReviewDecisionResultV231(review=review, shadow_entry=shadow)


def project_legacy_shadow_registry_v231(
    legacy: ShadowFaultRegistryV23,
) -> ShadowFaultRegistryV231:
    projected = []
    for entry in legacy.entries:
        payload = {
            "schema_version": "dta-v231.shadow-fault-entry.v1",
            "shadow_fault_id": entry.shadow_fault_id,
            "status": "SHADOW",
            "canonical_label": entry.canonical_label,
            "broad_fault_domain": entry.broad_fault_domain,
            "leading_hypothesis_id": f"legacy:{entry.shadow_fault_id}",
            "leading_hypothesis": entry.canonical_label,
            "alternative_hypotheses": (),
            "unresolved_dimensions": (),
            "uncertainty_mode": ReportUncertaintyModeV231.SINGLE_LEADING_HYPOTHESIS,
            "positive_report_ids": entry.positive_report_ids,
            "review_record_id": entry.review_record_id,
            "legacy_source_entry_sha256": entry.entry_sha256,
            "remediation_authority": "NONE",
        }
        projected.append(_hashed(ShadowFaultEntryV231, payload, "entry_sha256"))
    return _registry(tuple(projected))


def _registry(entries: tuple[ShadowFaultEntryV231, ...]) -> ShadowFaultRegistryV231:
    return _hashed(
        ShadowFaultRegistryV231,
        {
            "schema_version": "dta-v231.shadow-fault-registry.v1",
            "entries": tuple(sorted(entries, key=lambda item: item.shadow_fault_id)),
        },
        "registry_sha256",
    )


class LocalReviewStoreV231:
    """Project-local v2.3.1 store; review decisions remain explicit CLI inputs."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.reports_dir = self.root / "reports-v231"
        self.reviews_dir = self.root / "reviews-v231"
        self.registry_path = self.root / "shadow-registry-v231.json"

    def _prepare(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.reviews_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_bound(path: Path, value: DtaModelV22) -> None:
        rendered = value.model_dump_json(indent=2) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != rendered:
                raise ValueError(f"local v2.3.1 review artifact differs: {path.name}")
            return
        path.write_text(rendered, encoding="utf-8")

    def enqueue(self, item: ReviewQueueItemV231) -> Path:
        self._prepare()
        path = self.reports_dir / f"{item.report.report_id}.json"
        self._write_bound(path, item)
        return path

    def list_report_ids(self) -> tuple[str, ...]:
        if not self.reports_dir.exists():
            return ()
        return tuple(
            sorted(path.stem for path in self.reports_dir.glob("report-v231-*.json"))
        )

    def load_item(self, report_id: str) -> ReviewQueueItemV231:
        if not re.fullmatch(r"report-v231-[0-9a-f]{16}", report_id):
            raise ValueError("v2.3.1 review report ID is invalid")
        path = self.reports_dir / f"{report_id}.json"
        if not path.is_file():
            raise ValueError("v2.3.1 review report is absent")
        return ReviewQueueItemV231.model_validate_json(path.read_bytes())

    def load_registry(self) -> ShadowFaultRegistryV231:
        if not self.registry_path.is_file():
            return ShadowFaultRegistryV231.empty()
        return ShadowFaultRegistryV231.model_validate_json(
            self.registry_path.read_bytes()
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
    ) -> ReviewDecisionResultV231:
        item = self.load_item(report_id)
        result = decide_review_v231(
            item=item,
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
            self.reviews_dir / f"{result.review.review_record_id}.json",
            result.review,
        )
        if result.shadow_entry is not None:
            registry = self.load_registry()
            if any(
                value.shadow_fault_id == result.shadow_entry.shadow_fault_id
                for value in registry.entries
            ):
                raise ValueError("accepted v2.3.1 shadow entry already exists")
            updated = _registry((*registry.entries, result.shadow_entry))
            self.registry_path.write_text(
                updated.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
        return result


__all__ = (
    "HumanReviewRecordV231",
    "LocalReviewStoreV231",
    "ReviewDecisionResultV231",
    "ReviewQueueItemV231",
    "ShadowFaultEntryV231",
    "ShadowFaultRegistryV231",
    "build_review_queue_item_v231",
    "decide_review_v231",
    "project_legacy_shadow_registry_v231",
    "render_review_display_v231",
)
