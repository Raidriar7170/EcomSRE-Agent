"""Deterministic current-run Phase 4 Domain RCA Judge."""

from __future__ import annotations

import hashlib

from ecomsre.model.gateway import ProviderProtocolError
from ecomsre.phase1.contracts import (
    Evidence,
    EvidenceSource,
    RCADecision,
    RecommendedNextAction,
)
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.contracts import JudgeRequest
from ecomsre.phase2.token_policy import TokenAuthority, canonical_json_bytes
from ecomsre.phase4.contracts import (
    DomainFaultMechanism,
    DomainModelCallAudit,
    DomainRCAResult,
    DomainVariant,
)
from ecomsre.phase4.provider import OpenAICompatibleDomainBackend
from ecomsre.phase4.semantics import (
    classify_domain_evidence_mechanism,
    domain_evidence_supports_mechanism,
    is_business_sli_anomaly,
    is_domain_anomalous_metric,
)


def _need_more(
    request: JudgeRequest,
    *,
    supporting: tuple[Evidence, ...],
    gap: str,
    contradicting: tuple[Evidence, ...] = (),
) -> DomainRCAResult:
    return DomainRCAResult(
        schema_version="phase4.domain-rca-result.v1",
        decision=RCADecision.NEED_MORE_EVIDENCE,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli=request.incident.affected_sli,
        supporting_evidence=tuple(item.evidence_ref for item in supporting),
        contradicting_evidence=tuple(
            item.evidence_ref for item in contradicting
        ),
        missing_evidence=(gap,),
        confidence=0.35,
        decision_rationale=(
            "Additional evidence is required to confirm one domain mechanism."
        ),
        recommended_next_action=(
            RecommendedNextAction.COLLECT_ADDITIONAL_READ_ONLY_TELEMETRY_EVIDENCE
        ),
    )


def _abstain(
    request: JudgeRequest,
    *,
    contradicting: tuple[Evidence, ...],
) -> DomainRCAResult:
    return DomainRCAResult(
        schema_version="phase4.domain-rca-result.v1",
        decision=RCADecision.ABSTAIN,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli=None,
        supporting_evidence=(),
        contradicting_evidence=tuple(
            item.evidence_ref for item in contradicting
        ),
        missing_evidence=(),
        confidence=0.0,
        decision_rationale=(
            "There is no anomalous business metric confirming an incident."
        ),
        recommended_next_action=(
            RecommendedNextAction.PRESERVE_CURRENT_REPLAY_EVIDENCE
        ),
    )


def _causal_chain(
    mechanism: DomainFaultMechanism,
) -> tuple[str, ...]:
    return {
        DomainFaultMechanism.FEATURE_FRESHNESS_LAG: (
            "Feature freshness lag degraded the bounded business SLI.",
            "A non-metric stale feature read corroborated the metric anomaly.",
        ),
        DomainFaultMechanism.MODEL_FEATURE_SCHEMA_MISMATCH: (
            "A model-feature schema mismatch disrupted ranking requests.",
            "A non-metric compatibility observation corroborated the metric anomaly.",
        ),
        DomainFaultMechanism.RANKING_CONFIGURATION_FAILURE: (
            "An invalid ranking configuration disrupted ranking requests.",
            "A bounded diagnostic observation corroborated the metric anomaly.",
        ),
    }[mechanism]


def judge_domain_request(request: JudgeRequest) -> DomainRCAResult:
    """Judge only the canonical evidence view reconstructed from Phase 2 stores."""

    request = JudgeRequest.model_validate(request)
    evidence = request.resolved_evidence_view.evidence
    if any(item.run_id != request.run_id for item in evidence):
        raise ValueError("Domain Judge received cross-run evidence")

    business_anomalies = tuple(item for item in evidence if is_business_sli_anomaly(item))
    root_metrics = tuple(item for item in evidence if is_domain_anomalous_metric(item))
    domain_related = tuple(
        item
        for item in evidence
        if classify_domain_evidence_mechanism(item) is not None
    )
    if not business_anomalies:
        return _abstain(request, contradicting=domain_related)
    if not root_metrics:
        return _need_more(
            request,
            supporting=business_anomalies,
            gap="One typed Feature or Ranking mechanism metric is required.",
        )

    root_services = {item.service for item in root_metrics}
    if len(root_services) != 1:
        return _need_more(
            request,
            supporting=root_metrics,
            gap="A single anomalous Feature or Ranking root service is required.",
        )
    root_service = next(iter(root_services))
    mechanisms = {
        mechanism
        for item in root_metrics
        if (
            mechanism := classify_domain_evidence_mechanism(item)
        ) is not None
    }
    if len(mechanisms) != 1:
        return _need_more(
            request,
            supporting=root_metrics,
            gap="A single typed domain fault mechanism is required.",
        )
    mechanism = next(iter(mechanisms))
    conflicting = tuple(
        item
        for item in evidence
        if item.service == root_service
        and (
            classified := classify_domain_evidence_mechanism(item)
        ) is not None
        and classified is not mechanism
    )
    if conflicting:
        return _need_more(
            request,
            supporting=root_metrics,
            contradicting=conflicting,
            gap=(
                "Conflicting current-run mechanism evidence for the same root "
                "service must be resolved."
            ),
        )
    supporting = tuple(
        item
        for item in evidence
        if item.service == root_service
        and domain_evidence_supports_mechanism(item, mechanism)
    )
    sources = {item.source for item in supporting}
    if EvidenceSource.METRICS not in sources or not (
        sources - {EvidenceSource.METRICS}
    ):
        return _need_more(
            request,
            supporting=supporting,
            gap=(
                "A non-metric observation supporting the typed domain mechanism "
                "is required."
            ),
        )
    contradicting = tuple(
        item
        for item in evidence
        if item.evidence_ref not in {entry.evidence_ref for entry in supporting}
        and item.source is EvidenceSource.CHANGES
    )
    return DomainRCAResult(
        schema_version="phase4.domain-rca-result.v1",
        decision=RCADecision.RCA_CONFIRMED,
        root_service=root_service,
        fault_mechanism=mechanism,
        causal_chain=_causal_chain(mechanism),
        affected_sli=request.incident.affected_sli,
        supporting_evidence=tuple(item.evidence_ref for item in supporting),
        contradicting_evidence=tuple(
            item.evidence_ref for item in contradicting
        ),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale=(
            "Two current-run source classes confirm one domain mechanism for "
            "one anomalous root service."
        ),
        recommended_next_action=(
            RecommendedNextAction.REVIEW_BOUNDED_REPLAY_EVIDENCE
        ),
    )


def invoke_provider_domain_judge(
    *,
    request: JudgeRequest,
    ledger: BudgetLedger,
    authority: TokenAuthority,
    judge_capacity_slot_id: str,
    variant: DomainVariant,
    backend: OpenAICompatibleDomainBackend,
) -> tuple[DomainRCAResult, DomainModelCallAudit]:
    """Charge exactly one no-retry provider Domain Judge call."""

    request = JudgeRequest.model_validate(request)
    envelope: dict[str, object] = {
        "schema_version": "phase4.domain-judge-envelope.v1",
        "model_snapshot": authority.core.model_snapshot,
        "request": request.model_dump(mode="json"),
        "response_schema": DomainRCAResult.model_json_schema(mode="validation"),
    }
    request_bytes = canonical_json_bytes(envelope)
    exact_input_tokens = len(
        authority.encoding.encode(
            request_bytes.decode("utf-8"),
            allowed_special=set(),
            disallowed_special="all",
        )
    )
    available = (
        ledger.snapshot().remaining_tokens
        + ledger.reserved_floor_for(judge_capacity_slot_id)
    )
    max_completion_tokens = min(4096, available - exact_input_tokens)
    if exact_input_tokens <= 0 or max_completion_tokens <= 0:
        raise ValueError("Domain Judge does not fit the existing budget")
    lease, _ = ledger.expand_exact_model_lease(
        expected_snapshot_sequence=ledger.snapshot().sequence,
        source_record_id=judge_capacity_slot_id,
        exact_input_tokens=exact_input_tokens,
        minimum_completion_tokens=1,
        max_completion_tokens=max_completion_tokens,
    )
    try:
        completion = backend.complete(
            envelope=envelope,
            max_completion_tokens=max_completion_tokens,
        )
        result = completion.result
        deterministic = judge_domain_request(request)
        if (
            result.decision is not deterministic.decision
            or result.root_service != deterministic.root_service
            or result.fault_mechanism is not deterministic.fault_mechanism
            or result.affected_sli != deterministic.affected_sli
        ):
            raise ProviderProtocolError(
                "provider Domain result conflicts with deterministic policy"
            )
        visible_refs = {
            item.evidence_ref for item in request.resolved_evidence_view.evidence
        }
        cited = {
            *result.supporting_evidence,
            *result.contradicting_evidence,
        }
        if not cited <= visible_refs:
            raise ProviderProtocolError(
                "provider Domain result cites evidence outside the current run"
            )
        if not set(result.supporting_evidence) <= set(
            deterministic.supporting_evidence
        ):
            raise ProviderProtocolError(
                "provider supporting evidence is not evidence-native"
            )
        if not set(result.contradicting_evidence) <= set(
            deterministic.contradicting_evidence
        ):
            raise ProviderProtocolError(
                "provider contradicting evidence conflicts with policy"
            )
        total_tokens = exact_input_tokens + completion.output_tokens
        _, final_snapshot = ledger.charge_exact_model_lease(
            expected_snapshot_sequence=ledger.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=lease.owner_role,
            owner_node_id=lease.owner_node_id,
            source_record_id=judge_capacity_slot_id,
            input_tokens=exact_input_tokens,
            output_tokens=completion.output_tokens,
            total_tokens=total_tokens,
        )
    except Exception:
        try:
            ledger.return_exact_model_lease(
                expected_snapshot_sequence=ledger.snapshot().sequence,
                lease_id=lease.lease_id,
                owner_role=lease.owner_role,
                owner_node_id=lease.owner_node_id,
                source_record_id=judge_capacity_slot_id,
            )
        except Exception:
            pass
        raise
    response_bytes = canonical_json_bytes(result.model_dump(mode="json"))
    return (
        result,
        DomainModelCallAudit(
            schema_version="phase4.domain-model-call-audit.v1",
            run_id=request.run_id,
            case_id=final_snapshot.case_id,
            variant=variant,
            provider_identity=backend.provider_identity,
            model_snapshot=backend.model,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            response_sha256=hashlib.sha256(response_bytes).hexdigest(),
            local_input_tokens=exact_input_tokens,
            provider_prompt_tokens=completion.provider_prompt_tokens,
            output_tokens=completion.output_tokens,
            total_tokens=total_tokens,
            no_retry=True,
            scripted_fallback=False,
        ),
    )
