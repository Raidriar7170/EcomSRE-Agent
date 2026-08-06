"""Truth projection repair for the review-gated Phase 5B v2 analysis."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from scripts.phase5b_execution.contracts import (
    GroundTruthProjection,
    RawScoredRunRecord,
    ScoredRunEvaluation,
)
from scripts.phase5b_execution.scoring import (
    _SUBSETS_BY_TEMPLATE,
    _score_one,
    _truth_projection,
)


WriteDisposition = Literal["NO_ACTION", "SAFE_REPLAY_REMEDIATION_CANDIDATE"]


def preregistered_subsets_for_template(template_id: str) -> tuple[str, ...]:
    """Return the frozen public subset grouping for one template."""

    selected = _SUBSETS_BY_TEMPLATE.get(template_id)
    if selected is None:
        raise ValueError("template is absent from preregistered subset mapping")
    return tuple(selected)


def project_hidden_truth_v2(
    *,
    payload: Mapping[str, object],
    template_id: str,
    seed_id: str,
    write_disposition: WriteDisposition,
) -> GroundTruthProjection:
    """Validate hidden truth while deriving subsets only from public registration.

    The input mapping is never modified. Decision, root-service, mechanism, and
    all other scoring truth remain exactly as supplied by the sealed v1 pack.
    Only the non-primary difficult-subset grouping is replaced in memory.
    """

    normalized = dict(payload)
    normalized["difficult_subsets"] = list(
        preregistered_subsets_for_template(template_id)
    )
    return _truth_projection(
        payload=normalized,
        template_id=template_id,
        seed_id=seed_id,
        write_disposition=write_disposition,
    )


def score_hidden_record_v2(
    *,
    raw: RawScoredRunRecord,
    payload: Mapping[str, object],
    truth_sha256: str,
    write_disposition: WriteDisposition,
) -> ScoredRunEvaluation:
    """Score one immutable hidden record under the v2 projection contract."""

    raw.verify_record_sha256()
    original_record_sha256 = raw.record_sha256
    truth = project_hidden_truth_v2(
        payload=payload,
        template_id=raw.template_id,
        seed_id=raw.seed_id,
        write_disposition=write_disposition,
    )
    scored = _score_one(
        raw=raw,
        truth=truth,
        truth_sha256=truth_sha256,
        population="HIDDEN",
    )
    raw.verify_record_sha256()
    if raw.record_sha256 != original_record_sha256:
        raise ValueError("v2 analysis changed immutable raw record SHA-256")
    if scored.raw_record_sha256 != original_record_sha256:
        raise ValueError("v2 score is not bound to immutable raw evidence")
    return scored
