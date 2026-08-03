"""Generic deterministic evidence-driven model policy for replay."""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, datetime

from ecomsre.phase1.contracts import (
    ChangesAction,
    Evidence,
    EvidenceSource,
    FaultMechanism,
    FinalAction,
    LogsAction,
    MetricsAction,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    RCADecision,
    RCAResult,
    RecommendedNextAction,
    TracesAction,
)
from ecomsre.phase1.semantics import (
    classify_evidence_mechanism,
    evidence_supports_mechanism,
    is_anomalous_metric_evidence,
)
from ecomsre.phase1.validator import revalidate_phase1_model


def _mechanism(item: Evidence) -> FaultMechanism | None:
    return classify_evidence_mechanism(item)


def _is_anomalous_metric(item: Evidence) -> bool:
    return is_anomalous_metric_evidence(item)


def _attempted(request: ModelRequest, action_type: str) -> bool:
    return any(entry.action.action_type == action_type for entry in request.transcript)


def _evidence_for(
    request: ModelRequest,
    source: EvidenceSource,
) -> tuple[Evidence, ...]:
    return tuple(item for item in request.evidence if item.source is source)


def _matching(
    request: ModelRequest,
    *,
    service: str,
    mechanism: FaultMechanism,
) -> tuple[Evidence, ...]:
    return tuple(
        item
        for item in request.evidence
        if item.service == service and evidence_supports_mechanism(item, mechanism)
    )


def _candidate_mechanisms(
    request: ModelRequest,
    *,
    service: str,
) -> tuple[FaultMechanism, ...]:
    return tuple(
        sorted(
            {
                mechanism
                for item in request.evidence
                if item.service == service
                and (mechanism := _mechanism(item)) is not None
            },
            key=lambda mechanism: mechanism.value,
        )
    )


def _query(
    request: ModelRequest,
    action_type: str,
    *,
    service: str | None,
) -> MetricsAction | LogsAction | TracesAction | ChangesAction:
    if action_type == "metrics":
        return MetricsAction(
            action_type="metrics",
            started_at=request.incident.started_at,
            ended_at=request.incident.ended_at,
            service=service,
        )
    if action_type == "logs":
        return LogsAction(
            action_type="logs",
            started_at=request.incident.started_at,
            ended_at=request.incident.ended_at,
            service=service,
        )
    if action_type == "traces":
        return TracesAction(
            action_type="traces",
            started_at=request.incident.started_at,
            ended_at=request.incident.ended_at,
            service=service,
        )
    if action_type == "changes":
        return ChangesAction(
            action_type="changes",
            started_at=request.incident.started_at,
            ended_at=request.incident.ended_at,
            service=service,
        )
    raise ValueError("unsupported scripted action type")


def _confirmed(
    request: ModelRequest,
    *,
    service: str,
    mechanism: FaultMechanism,
    supporting: Iterable[Evidence],
) -> FinalAction:
    support = tuple(supporting)
    result = RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.RCA_CONFIRMED,
        root_service=service,
        fault_mechanism=mechanism,
        causal_chain=(
            f"{service} emitted correlated {mechanism} observations.",
            f"The correlated observations affected {request.incident.affected_sli}.",
        ),
        affected_sli=request.incident.affected_sli,
        supporting_evidence=tuple(item.evidence_ref for item in support),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale=(
            "Independent read-only observation sources confirm the bounded root cause."
        ),
        recommended_next_action=(RecommendedNextAction.REVIEW_BOUNDED_REPLAY_EVIDENCE),
    )
    return FinalAction(action_type="final", result=result)


def _need_more(
    request: ModelRequest,
    *,
    service: str | None,
    mechanism: FaultMechanism | None,
    supporting: Iterable[Evidence],
    missing: str,
) -> FinalAction:
    support = tuple(supporting)
    result = RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.NEED_MORE_EVIDENCE,
        root_service=service,
        fault_mechanism=mechanism,
        causal_chain=(),
        affected_sli=request.incident.affected_sli,
        supporting_evidence=tuple(item.evidence_ref for item in support),
        contradicting_evidence=(),
        missing_evidence=(missing,),
        confidence=0.35,
        decision_rationale=(
            "Additional evidence is missing for a confirmed bounded root cause."
        ),
        recommended_next_action=(
            RecommendedNextAction.COLLECT_ADDITIONAL_READ_ONLY_TELEMETRY_EVIDENCE
        ),
    )
    return FinalAction(action_type="final", result=result)


def _abstain(
    request: ModelRequest,
    normal_metrics: tuple[Evidence, ...],
) -> FinalAction:
    result = RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.ABSTAIN,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli=request.incident.affected_sli,
        supporting_evidence=(),
        contradicting_evidence=tuple(item.evidence_ref for item in normal_metrics),
        missing_evidence=(),
        confidence=0.15,
        decision_rationale=("The read-only observations do not establish an incident."),
        recommended_next_action=(
            RecommendedNextAction.CONTINUE_MONITORING_AFFECTED_SLI
        ),
    )
    return FinalAction(action_type="final", result=result)


def _select_action(
    request: ModelRequest,
) -> MetricsAction | LogsAction | TracesAction | ChangesAction | FinalAction:
    if not _attempted(request, "metrics"):
        return _query(request, "metrics", service=None)

    metrics = _evidence_for(request, EvidenceSource.METRICS)
    anomalous_metrics = tuple(item for item in metrics if _is_anomalous_metric(item))
    if anomalous_metrics:
        service = sorted(item.service for item in anomalous_metrics)[0]
        candidates = _candidate_mechanisms(request, service=service)
        if not candidates:
            for action_type in ("traces", "logs", "changes"):
                if not _attempted(request, action_type):
                    query_service = None if action_type == "changes" else service
                    return _query(
                        request,
                        action_type,
                        service=query_service,
                    )
            anomalous_evidence = tuple(
                item
                for item in request.evidence
                if item.service == service and _is_anomalous_metric(item)
            )
            return _need_more(
                request,
                service=service,
                mechanism=None,
                supporting=anomalous_evidence,
                missing=(
                    "A canonical fault mechanism matching the anomalous "
                    "read-only observations is missing."
                ),
            )
        if len(candidates) > 1:
            for action_type in ("traces", "logs", "changes"):
                if not _attempted(request, action_type):
                    query_service = None if action_type == "changes" else service
                    return _query(
                        request,
                        action_type,
                        service=query_service,
                    )
            return _need_more(
                request,
                service=service,
                mechanism=None,
                supporting=anomalous_metrics,
                missing=(
                    "The read-only observations contain conflicting fault "
                    "mechanism signals."
                ),
            )
        mechanism = candidates[0]
    else:
        if not _attempted(request, "changes"):
            return _query(request, "changes", service=None)
        metrics_attempt = next(
            entry
            for entry in request.transcript
            if entry.action.action_type == "metrics"
        )
        if metrics_attempt.status == "ERROR" or not metrics:
            return _need_more(
                request,
                service=None,
                mechanism=None,
                supporting=(),
                missing="Metrics observations establishing SLI state are missing.",
            )
        return _abstain(request, metrics)

    matching = _matching(
        request,
        service=service,
        mechanism=mechanism,
    )
    matching_sources = {item.source for item in matching}

    if not _attempted(request, "traces"):
        return _query(request, "traces", service=service)

    if mechanism is FaultMechanism.RUNTIME_CONFIGURATION_FAILURE:
        if not _attempted(request, "logs"):
            return _query(request, "logs", service=service)
        if not _attempted(request, "changes"):
            return _query(request, "changes", service=None)
        matching = _matching(
            request,
            service=service,
            mechanism=mechanism,
        )
        matching_sources = {item.source for item in matching}
        if EvidenceSource.CHANGES in matching_sources and len(matching_sources) >= 2:
            return _confirmed(
                request,
                service=service,
                mechanism=mechanism,
                supporting=matching,
            )
        return _need_more(
            request,
            service=service,
            mechanism=mechanism,
            supporting=matching,
            missing=(
                "Changes observations matching the runtime configuration "
                "transition are missing."
            ),
        )

    if (
        EvidenceSource.METRICS in matching_sources
        and EvidenceSource.TRACES in matching_sources
    ):
        if mechanism is FaultMechanism.REQUEST_PROCESSING_FAILURE and not _attempted(
            request, "changes"
        ):
            return _query(request, "changes", service=None)
        return _confirmed(
            request,
            service=service,
            mechanism=mechanism,
            supporting=matching,
        )

    if not _attempted(request, "logs"):
        return _query(request, "logs", service=service)
    matching = _matching(
        request,
        service=service,
        mechanism=mechanism,
    )
    if len({item.source for item in matching}) >= 2:
        return _confirmed(
            request,
            service=service,
            mechanism=mechanism,
            supporting=matching,
        )
    if not _attempted(request, "changes"):
        return _query(request, "changes", service=None)
    return _need_more(
        request,
        service=service,
        mechanism=mechanism,
        supporting=matching,
        missing="A second independent matching observation source is missing.",
    )


class ScriptedModelGateway:
    """Stateless policy based only on the typed request evidence surface."""

    provider_name = "scripted"

    def complete(self, request: ModelRequest) -> ModelResponse:
        validated_request = revalidate_phase1_model(request, ModelRequest)
        started_at = datetime.now(UTC)
        monotonic_start = time.monotonic()
        action = _select_action(validated_request)
        request_size = len(validated_request.model_dump_json())
        action_size = len(action.model_dump_json())
        usage = ModelUsage(
            input_tokens=max(1, request_size // 4),
            output_tokens=max(1, action_size // 4),
            total_tokens=max(1, request_size // 4) + max(1, action_size // 4),
        )
        response = ModelResponse(
            schema_version="phase1.model-response.v1",
            request_id=validated_request.request_id,
            response_id=f"{validated_request.request_id}-response",
            run_id=validated_request.run_id,
            agent_id=validated_request.agent_id,
            incident_id=validated_request.incident_id,
            task_id=validated_request.task_id,
            provider_name=self.provider_name,
            model_name=validated_request.model_name,
            action=action,
            usage=usage,
            started_at=started_at,
            ended_at=datetime.now(UTC),
            monotonic_duration_seconds=time.monotonic() - monotonic_start,
            error_code=None,
        )
        return revalidate_phase1_model(response, ModelResponse)
