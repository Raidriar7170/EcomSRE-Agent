"""Evidence-bound broad-domain projection for DTA v2.3.3."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any

from pydantic import Field, StrictFloat, model_validator

from ecomsre.dta_v2.v22.memory import (
    ResourceSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    GenericAnomalyV23,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class DomainProjectionStatusV233(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class DomainEvidenceVoteV233(DtaModelV22):
    schema_version: str = "dta-v233.domain-evidence-vote.v1"
    domain: ProvisionalFaultDomainV23
    root_service: str
    anomaly_id: str
    evidence_refs: tuple[str, ...]
    source: EvidenceSourceV22
    strength: SignalStrengthV22
    base_weight: StrictFloat
    combination_bonus: StrictFloat
    negative_weight: StrictFloat = Field(le=0.0)
    reason_codes: tuple[str, ...] = Field(min_length=1)
    vote_score: StrictFloat

    @model_validator(mode="after")
    def require_vote(self) -> "DomainEvidenceVoteV233":
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("v2.3.3 domain vote evidence refs are not canonical")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("v2.3.3 domain vote reasons are not canonical")
        if round(
            self.base_weight + self.combination_bonus + self.negative_weight,
            6,
        ) != round(self.vote_score, 6):
            raise ValueError("v2.3.3 domain vote score differs")
        return self


class DomainScoreV233(DtaModelV22):
    domain: ProvisionalFaultDomainV23
    score: StrictFloat
    vote_count: int = Field(ge=0)
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]


class DomainProjectionV233(DtaModelV22):
    schema_version: str = "dta-v233.domain-projection.v1"
    candidate_root_services: tuple[str, ...]
    selected_root_service: str | None
    domain_scores: tuple[DomainScoreV233, ...]
    selected_domain: ProvisionalFaultDomainV23
    runner_up_domain: ProvisionalFaultDomainV23 | None
    score_margin: StrictFloat
    status: DomainProjectionStatusV233
    supporting_anomaly_ids: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...] = Field(min_length=1)
    projection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_projection(self) -> "DomainProjectionV233":
        if self.candidate_root_services != tuple(
            sorted(set(self.candidate_root_services))
        ):
            raise ValueError("v2.3.3 projection roots are not canonical")
        if self.selected_root_service is not None and self.selected_root_service not in set(
            self.candidate_root_services
        ):
            raise ValueError("v2.3.3 selected root is not a candidate")
        expected_domains = tuple(
            sorted(ProvisionalFaultDomainV23, key=lambda item: item.value)
        )
        if tuple(item.domain for item in self.domain_scores) != expected_domains:
            raise ValueError("v2.3.3 projection is not total over domains")
        for values, label in (
            (self.supporting_anomaly_ids, "anomaly IDs"),
            (self.supporting_evidence_refs, "support refs"),
            (self.contradicting_evidence_refs, "contradiction refs"),
            (self.reason_codes, "reasons"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"v2.3.3 projection {label} are not canonical")
        if self.status is DomainProjectionStatusV233.RESOLVED:
            if self.selected_domain is ProvisionalFaultDomainV23.UNKNOWN:
                raise ValueError("resolved v2.3.3 projection selected UNKNOWN")
            if self.score_margin < 1.0:
                raise ValueError("resolved v2.3.3 projection margin is too small")
        elif self.selected_domain is not ProvisionalFaultDomainV23.UNKNOWN:
            raise ValueError("unresolved v2.3.3 projection selected a domain")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"projection_sha256"})
        )
        if self.projection_sha256 != expected:
            raise ValueError("v2.3.3 projection digest differs")
        return self


# Frozen before the v2.3.3 fixed evaluation is built. ``wait`` is the bounded
# morphological form of the Goal's ``waiting`` token.
CONCURRENCY_LEXICON_V233 = frozenset(
    {"backlog", "permit", "pool", "queue", "semaphore", "thread", "throttle", "wait", "worker"}
)
NETWORK_PATTERNS_V233 = (
    "connection reset",
    "dns",
    "socket",
    "tls",
)
EXTERNAL_PATTERNS_V233 = (
    "http 429",
    "rate limit",
    "upstream external",
)


_BASE_DOMAIN_BY_KIND_V233: dict[
    GenericAnomalyKindV23,
    tuple[tuple[ProvisionalFaultDomainV23, float], ...],
] = {
    GenericAnomalyKindV23.METRIC_QUEUE_LAG_OUTLIER: (
        (ProvisionalFaultDomainV23.CONCURRENCY, 3.0),
    ),
    GenericAnomalyKindV23.METRIC_ERROR_OUTLIER: (
        (ProvisionalFaultDomainV23.RUNTIME, 1.5),
        (ProvisionalFaultDomainV23.RESOURCE, 0.5),
    ),
    GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER: (
        (ProvisionalFaultDomainV23.DEPENDENCY, 1.5),
    ),
    GenericAnomalyKindV23.RUNTIME_NOT_RUNNING: (
        (ProvisionalFaultDomainV23.RUNTIME, 3.0),
    ),
    GenericAnomalyKindV23.RUNTIME_UNHEALTHY: (
        (ProvisionalFaultDomainV23.RUNTIME, 3.0),
    ),
    GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY: (
        (ProvisionalFaultDomainV23.RUNTIME, 1.5),
    ),
    GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER: (
        (ProvisionalFaultDomainV23.RESOURCE, 3.0),
    ),
    GenericAnomalyKindV23.RESOURCE_MEMORY_TREND: (
        (ProvisionalFaultDomainV23.RESOURCE, 3.0),
    ),
    GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION: (
        (ProvisionalFaultDomainV23.DEPENDENCY, 2.0),
    ),
    GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER: (
        (ProvisionalFaultDomainV23.DEPENDENCY, 2.0),
    ),
    GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION: (
        (ProvisionalFaultDomainV23.CONFIGURATION, 0.75),
    ),
    GenericAnomalyKindV23.SOURCE_COVERAGE_GAP: (),
    GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN: (),
    GenericAnomalyKindV23.LOG_ERROR_CLUSTER: (),
}


def _observed(anomaly: GenericAnomalyV23) -> dict[str, str]:
    return {item.key: item.value for item in anomaly.observed_values}


def _vote(
    *,
    domain: ProvisionalFaultDomainV23,
    root: str,
    anomaly_id: str,
    refs: tuple[str, ...],
    source: EvidenceSourceV22,
    strength: SignalStrengthV22,
    base: float = 0.0,
    bonus: float = 0.0,
    negative: float = 0.0,
    reasons: tuple[str, ...],
) -> DomainEvidenceVoteV233:
    return DomainEvidenceVoteV233(
        domain=domain,
        root_service=root,
        anomaly_id=anomaly_id,
        evidence_refs=tuple(sorted(set(refs))),
        source=source,
        strength=strength,
        base_weight=float(base),
        combination_bonus=float(bonus),
        negative_weight=float(negative),
        reason_codes=tuple(sorted(set(reasons))),
        vote_score=float(base + bonus + negative),
    )


def _coverage_complete(
    graph: ResidualEvidenceGraphV23,
    source: EvidenceSourceV22,
) -> bool:
    coverage = next(item for item in graph.source_coverage if item.source is source)
    return bool(coverage.queried) and set(coverage.covered_services) == set(
        graph.candidate_services
    ) and coverage.failed_observations == 0


def _normal_resource_refs(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    root: str,
) -> tuple[str, ...]:
    if not _coverage_complete(graph, EvidenceSourceV22.RESOURCES):
        return ()
    facts = tuple(
        item
        for item in memory.salient_facts
        if item.source is EvidenceSourceV22.RESOURCES
        and item.service == root
        and isinstance(item.payload, ResourceSalientPayloadV22)
    )
    if not facts or any(item.signal_strength is not SignalStrengthV22.NONE for item in facts):
        return ()
    return tuple(sorted({ref for item in facts for ref in item.evidence_refs}))


def _base_votes(
    *,
    anomaly: GenericAnomalyV23,
    memory: SalientEvidenceMemoryV22,
) -> list[DomainEvidenceVoteV233]:
    votes: list[DomainEvidenceVoteV233] = []
    for domain, weight in _BASE_DOMAIN_BY_KIND_V233[anomaly.kind]:
        votes.append(
            _vote(
                domain=domain,
                root=anomaly.service,
                anomaly_id=anomaly.anomaly_id,
                refs=anomaly.evidence_refs,
                source=anomaly.source,
                strength=anomaly.strength,
                base=weight,
                reasons=(f"BASE_{anomaly.kind.value}",),
            )
        )
    if anomaly.kind is GenericAnomalyKindV23.LOG_ERROR_CLUSTER:
        interpreted = DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.interpret(
            anomaly=anomaly,
            memory=memory,
        )
        for domain in interpreted.candidate_domains:
            weight = 0.0 if domain is ProvisionalFaultDomainV23.UNKNOWN else 2.5
            votes.append(
                _vote(
                    domain=domain,
                    root=anomaly.service,
                    anomaly_id=anomaly.anomaly_id,
                    refs=anomaly.evidence_refs,
                    source=anomaly.source,
                    strength=anomaly.strength,
                    base=weight,
                    reasons=(
                        "BOUND_LOG_CATEGORY",
                        *interpreted.reason_codes,
                    ),
                )
            )
    if anomaly.kind in {
        GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN,
        GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
    }:
        text = " ".join(_observed(anomaly).values()).casefold()
        tokens = set(re.findall(r"[a-z0-9]+", text))
        if anomaly.kind is GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN and tokens.intersection(
            CONCURRENCY_LEXICON_V233
        ):
            votes.append(
                _vote(
                    domain=ProvisionalFaultDomainV23.CONCURRENCY,
                    root=anomaly.service,
                    anomaly_id=anomaly.anomaly_id,
                    refs=anomaly.evidence_refs,
                    source=anomaly.source,
                    strength=anomaly.strength,
                    reasons=("CONCURRENCY_LEXICON_MATCH",),
                )
            )
        if any(pattern in text for pattern in NETWORK_PATTERNS_V233):
            votes.append(
                _vote(
                    domain=ProvisionalFaultDomainV23.NETWORK,
                    root=anomaly.service,
                    anomaly_id=anomaly.anomaly_id,
                    refs=anomaly.evidence_refs,
                    source=anomaly.source,
                    strength=anomaly.strength,
                    base=3.0,
                    reasons=("EXPLICIT_NETWORK_PATTERN",),
                )
            )
        if any(pattern in text for pattern in EXTERNAL_PATTERNS_V233):
            votes.append(
                _vote(
                    domain=ProvisionalFaultDomainV23.EXTERNAL,
                    root=anomaly.service,
                    anomaly_id=anomaly.anomaly_id,
                    refs=anomaly.evidence_refs,
                    source=anomaly.source,
                    strength=anomaly.strength,
                    base=3.0,
                    reasons=("EXPLICIT_EXTERNAL_PATTERN",),
                )
            )
    return votes


def _votes_for_root(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    root: str,
) -> tuple[DomainEvidenceVoteV233, ...]:
    residual_ids = set(graph.residual_anomaly_ids)
    anomalies = tuple(
        item
        for item in graph.generic_anomalies
        if item.anomaly_id in residual_ids and item.service == root
    )
    votes = [
        vote
        for anomaly in anomalies
        for vote in _base_votes(anomaly=anomaly, memory=memory)
    ]
    by_kind: dict[GenericAnomalyKindV23, tuple[GenericAnomalyV23, ...]] = {
        kind: tuple(item for item in anomalies if item.kind is kind)
        for kind in GenericAnomalyKindV23
    }
    change = by_kind[GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION]
    error_support = (
        *by_kind[GenericAnomalyKindV23.METRIC_ERROR_OUTLIER],
        *by_kind[GenericAnomalyKindV23.LOG_ERROR_CLUSTER],
    )
    if change and error_support:
        refs = tuple(
            sorted(
                {
                    ref
                    for item in (*change, *error_support)
                    for ref in item.evidence_refs
                }
            )
        )
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.CONFIGURATION,
                root=root,
                anomaly_id=change[0].anomaly_id,
                refs=refs,
                source=EvidenceSourceV22.CHANGES,
                strength=SignalStrengthV22.STRONG,
                bonus=2.5,
                reasons=("CONFIGURATION_CROSS_SOURCE_COMBINATION",),
            )
        )
    runtime_error = (
        *by_kind[GenericAnomalyKindV23.METRIC_ERROR_OUTLIER],
        *by_kind[GenericAnomalyKindV23.LOG_ERROR_CLUSTER],
        *by_kind[GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN],
    )
    restarts = by_kind[GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY]
    if restarts and runtime_error:
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.RUNTIME,
                root=root,
                anomaly_id=restarts[0].anomaly_id,
                refs=tuple(
                    sorted(
                        {
                            ref
                            for item in (*restarts, *runtime_error)
                            for ref in item.evidence_refs
                        }
                    )
                ),
                source=EvidenceSourceV22.RUNTIME,
                strength=SignalStrengthV22.STRONG,
                bonus=1.5,
                reasons=("RUNTIME_RESTART_CORROBORATED",),
            )
        )
    trace = (
        *by_kind[GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION],
        *by_kind[GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER],
    )
    dependency_support = (
        *by_kind[GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER],
        *tuple(
            item
            for item in by_kind[GenericAnomalyKindV23.LOG_ERROR_CLUSTER]
            if any(
                vote.domain is ProvisionalFaultDomainV23.DEPENDENCY
                and vote.anomaly_id == item.anomaly_id
                for vote in votes
            )
        ),
    )
    if trace and dependency_support:
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.DEPENDENCY,
                root=root,
                anomaly_id=trace[0].anomaly_id,
                refs=tuple(
                    sorted(
                        {
                            ref
                            for item in (*trace, *dependency_support)
                            for ref in item.evidence_refs
                        }
                    )
                ),
                source=EvidenceSourceV22.TRACES,
                strength=SignalStrengthV22.STRONG,
                bonus=1.5,
                reasons=("DEPENDENCY_CROSS_SOURCE_COMBINATION",),
            )
        )
    unknown_logs = by_kind[GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN]
    abnormal_latency_or_error = (
        *by_kind[GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER],
        *by_kind[GenericAnomalyKindV23.METRIC_ERROR_OUTLIER],
        *by_kind[GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER],
        *by_kind[GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION],
    )
    lexical_logs = tuple(
        item
        for item in unknown_logs
        if set(
            re.findall(
                r"[a-z0-9]+",
                " ".join(_observed(item).values()).casefold(),
            )
        ).intersection(CONCURRENCY_LEXICON_V233)
    )
    normal_resource_refs = _normal_resource_refs(
        graph=graph,
        memory=memory,
        root=root,
    )
    if (
        lexical_logs
        and abnormal_latency_or_error
        and root in set(graph.healthy_runtime_services)
        and normal_resource_refs
    ):
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.CONCURRENCY,
                root=root,
                anomaly_id=lexical_logs[0].anomaly_id,
                refs=tuple(
                    sorted(
                        {
                            *normal_resource_refs,
                            *(
                                ref
                                for item in (*lexical_logs, *abnormal_latency_or_error)
                                for ref in item.evidence_refs
                            ),
                        }
                    )
                ),
                source=EvidenceSourceV22.LOGS,
                strength=SignalStrengthV22.STRONG,
                bonus=3.25,
                reasons=("CONCURRENCY_CROSS_SOURCE_COMBINATION",),
            )
        )

    negative_anchor = anomalies[0].anomaly_id if anomalies else f"coverage:{root}"
    if normal_resource_refs:
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.RESOURCE,
                root=root,
                anomaly_id=negative_anchor,
                refs=normal_resource_refs,
                source=EvidenceSourceV22.RESOURCES,
                strength=SignalStrengthV22.STRONG,
                negative=-3.0,
                reasons=("COMPLETE_NORMAL_RESOURCE_COVERAGE",),
            )
        )
    direct_runtime_failure = bool(
        by_kind[GenericAnomalyKindV23.RUNTIME_NOT_RUNNING]
        or by_kind[GenericAnomalyKindV23.RUNTIME_UNHEALTHY]
        or by_kind[GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY]
    )
    if root in set(graph.healthy_runtime_services) and not direct_runtime_failure:
        runtime_refs = tuple(
            sorted(
                {
                    ref
                    for item in memory.salient_facts
                    if item.source is EvidenceSourceV22.RUNTIME
                    and item.service == root
                    for ref in item.evidence_refs
                }
            )
        )
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.RUNTIME,
                root=root,
                anomaly_id=negative_anchor,
                refs=runtime_refs,
                source=EvidenceSourceV22.RUNTIME,
                strength=SignalStrengthV22.STRONG,
                negative=-1.5,
                reasons=("STRONG_HEALTHY_RUNTIME",),
            )
        )
    if _coverage_complete(graph, EvidenceSourceV22.CHANGES) and not change:
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.CONFIGURATION,
                root=root,
                anomaly_id=negative_anchor,
                refs=(),
                source=EvidenceSourceV22.CHANGES,
                strength=SignalStrengthV22.STRONG,
                negative=-1.0,
                reasons=("COMPLETE_NO_CHANGE_COVERAGE",),
            )
        )
    if _coverage_complete(graph, EvidenceSourceV22.TRACES) and not trace:
        votes.append(
            _vote(
                domain=ProvisionalFaultDomainV23.DEPENDENCY,
                root=root,
                anomaly_id=negative_anchor,
                refs=(),
                source=EvidenceSourceV22.TRACES,
                strength=SignalStrengthV22.STRONG,
                negative=-1.5,
                reasons=("COMPLETE_TRACE_WITHOUT_DEPENDENCY_PATH",),
            )
        )
    return tuple(
        sorted(
            votes,
            key=lambda item: (
                item.domain.value,
                item.anomaly_id,
                item.reason_codes,
                item.evidence_refs,
            ),
        )
    )


def _scores(
    votes: tuple[DomainEvidenceVoteV233, ...],
) -> dict[ProvisionalFaultDomainV23, float]:
    result = {domain: 0.0 for domain in ProvisionalFaultDomainV23}
    for vote in votes:
        result[vote.domain] += vote.vote_score
    return {domain: round(score, 6) for domain, score in result.items()}


def project_domain_v233(
    *,
    graph: ResidualEvidenceGraphV23,
    memory: SalientEvidenceMemoryV22,
    candidate_root_services: tuple[str, ...] | None = None,
) -> DomainProjectionV233:
    """Project one root-specific broad domain without evaluator or Provider input."""

    roots = tuple(
        sorted(
            set(candidate_root_services or ())
            or {
                item.service
                for item in graph.generic_anomalies
                if item.anomaly_id in set(graph.residual_anomaly_ids)
            }
            or set(graph.candidate_services)
        )
    )
    if not set(roots).issubset(graph.candidate_services):
        raise ValueError("v2.3.3 projection roots escape graph candidates")
    votes_by_root = {
        root: _votes_for_root(graph=graph, memory=memory, root=root) for root in roots
    }
    scores_by_root = {root: _scores(votes) for root, votes in votes_by_root.items()}
    substantive = tuple(
        domain
        for domain in ProvisionalFaultDomainV23
        if domain is not ProvisionalFaultDomainV23.UNKNOWN
    )
    selected_root = (
        None
        if not roots
        else min(
            roots,
            key=lambda root: (
                -max(scores_by_root[root][domain] for domain in substantive),
                -sum(max(0.0, scores_by_root[root][domain]) for domain in substantive),
                root,
            ),
        )
    )
    selected_votes = () if selected_root is None else votes_by_root[selected_root]
    scores = _scores(selected_votes)
    ranked = tuple(
        sorted(substantive, key=lambda domain: (-scores[domain], domain.value))
    )
    top = ranked[0]
    runner_up = ranked[1]
    margin = round(scores[top] - scores[runner_up], 6)
    selected_domain: ProvisionalFaultDomainV23
    if scores[top] >= 3.0 and margin >= 1.0:
        status = DomainProjectionStatusV233.RESOLVED
        selected_domain = top
    elif scores[top] > 0.0:
        status = DomainProjectionStatusV233.AMBIGUOUS
        selected_domain = ProvisionalFaultDomainV23.UNKNOWN
    else:
        status = DomainProjectionStatusV233.UNSUPPORTED
        selected_domain = ProvisionalFaultDomainV23.UNKNOWN
    support_domain = top
    support_votes = tuple(
        vote
        for vote in selected_votes
        if vote.domain is support_domain and vote.vote_score > 0.0
    )
    negative_votes = tuple(vote for vote in selected_votes if vote.vote_score < 0.0)
    domain_scores = tuple(
        DomainScoreV233(
            domain=domain,
            score=float(scores[domain]),
            vote_count=sum(vote.domain is domain for vote in selected_votes),
            supporting_evidence_refs=tuple(
                sorted(
                    {
                        ref
                        for vote in selected_votes
                        if vote.domain is domain and vote.vote_score > 0.0
                        for ref in vote.evidence_refs
                    }
                )
            ),
            contradicting_evidence_refs=tuple(
                sorted(
                    {
                        ref
                        for vote in selected_votes
                        if vote.domain is domain and vote.vote_score < 0.0
                        for ref in vote.evidence_refs
                    }
                )
            ),
        )
        for domain in sorted(ProvisionalFaultDomainV23, key=lambda item: item.value)
    )
    reasons = {
        *(
            reason
            for vote in (*support_votes, *negative_votes)
            for reason in vote.reason_codes
        ),
        f"PROJECTION_{status.value}",
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.domain-projection.v1",
        "candidate_root_services": roots,
        "selected_root_service": selected_root,
        "domain_scores": domain_scores,
        "selected_domain": selected_domain,
        "runner_up_domain": runner_up,
        "score_margin": float(margin),
        "status": status,
        "supporting_anomaly_ids": tuple(
            sorted({vote.anomaly_id for vote in support_votes})
        ),
        "supporting_evidence_refs": tuple(
            sorted({ref for vote in support_votes for ref in vote.evidence_refs})
        ),
        "contradicting_evidence_refs": tuple(
            sorted({ref for vote in negative_votes for ref in vote.evidence_refs})
        ),
        "reason_codes": tuple(sorted(reasons)),
    }
    draft = DomainProjectionV233.model_construct(
        **payload,
        projection_sha256="0" * 64,
    )
    return DomainProjectionV233.model_validate(
        {
            **payload,
            "projection_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"projection_sha256"})
            ),
        }
    )


__all__ = (
    "CONCURRENCY_LEXICON_V233",
    "DomainEvidenceVoteV233",
    "DomainProjectionStatusV233",
    "DomainProjectionV233",
    "DomainScoreV233",
    "project_domain_v233",
)
