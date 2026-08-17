"""Runtime admission and trace construction for evidence-guided plans."""

from __future__ import annotations

from ecomsre.dta_v2.tool_contracts import (
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    QueryMetricsRequest,
    SearchLogsRequest,
    TraceNeighborhoodRequest,
)
from ecomsre.dta_v2.v21.agent_contracts import AlertContextV21
from ecomsre.dta_v2.v21.planner_contracts import (
    EvidencePlanDecisionV21,
    PlannerNextStepV21,
)


def _requested_services(decision: EvidencePlanDecisionV21) -> tuple[str, ...]:
    request = decision.read_request
    if request is None:
        return ()
    if isinstance(
        request,
        (QueryMetricsRequest, SearchLogsRequest, TraceNeighborhoodRequest),
    ):
        return (request.service,)
    if isinstance(request, (InspectServiceRuntimeRequest, InspectResourceUsageRequest)):
        return request.services
    raise TypeError("unsupported Planner read request")


def validate_plan_decision_v21(
    *,
    decision: EvidencePlanDecisionV21,
    context: AlertContextV21,
    seen_request_sha256: tuple[str, ...],
    completed_read_dispatches: int,
) -> EvidencePlanDecisionV21:
    decision = EvidencePlanDecisionV21.model_validate(
        decision.model_dump(mode="python")
    )
    context = AlertContextV21.model_validate(context.model_dump(mode="python"))
    if decision.run_id != context.run_id:
        raise ValueError("Planner decision belongs to another run")
    if decision.turn_ordinal < 1 or decision.turn_ordinal > 5:
        raise ValueError("Planner turn is outside the investigation budget")
    for hypothesis in decision.hypotheses:
        if hypothesis.root_service not in context.candidate_services:
            raise ValueError("Planner hypothesis is outside candidate services")
    if decision.next_step is not PlannerNextStepV21.REQUEST_EVIDENCE:
        return decision
    request = decision.read_request
    assert request is not None
    if request.tool not in context.allowed_read_tools:
        raise ValueError("Planner requested a tool outside the allowlist")
    if not set(_requested_services(decision)).issubset(context.candidate_services):
        raise ValueError("Planner requested a service outside candidate services")
    if completed_read_dispatches >= context.maximum_read_tool_dispatches:
        raise ValueError("Planner read budget is exhausted")
    if request.normalized_request_sha256 in set(seen_request_sha256):
        raise ValueError("Planner requested a duplicate normalized call")
    return decision


__all__ = ("validate_plan_decision_v21",)
