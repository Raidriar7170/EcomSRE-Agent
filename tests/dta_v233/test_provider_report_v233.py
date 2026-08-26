from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.candidate_filter import (
    CandidateFilterError,
    filter_runbook_candidates,
)
from ecomsre.dta_v2.v23.contracts_v233 import (
    DiscoverySynthesisResponseV233,
    build_provisional_report_v233,
    build_runtime_hypotheses_v233,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
)
from ecomsre.dta_v2.v23.discovery_provider_v233 import (
    OpenAICompatibleDiscoveryTransportV233,
    build_discovery_synthesis_request_v233,
    call_discovery_provider_v233,
    deterministic_synthesis_response_v233,
    provider_response_payload_v233,
)
from ecomsre.dta_v2.v23.domain_audit_v233 import project_development_case_v233
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    load_evaluation_cases_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    OpenAICompatibleDiscoveryTransportV231,
    _residual_graph_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDispositionV233,
    evaluate_irreconcilable_guard_v233,
)
from ecomsre.dta_v2.v23.review_registry_v233 import (
    build_shadow_projection_v233,
    render_review_display_v233,
)
from ecomsre.dta_v2.v23.runtime_audit_v233 import build_v232_provider_audit_v233


ROOT = Path(__file__).resolve().parents[2]


def _runtime_inputs():
    evaluation_root = ROOT / "config/dta-v232/evaluation"
    cases = load_evaluation_cases_v232(evaluation_root / "cases.json")
    views = load_evaluation_views_v232(evaluation_root / "ontology-views.json")
    spec = cases.require("vx-201")
    view = views.require(spec.case_id)
    projection, memory, _reads = project_development_case_v233(
        repository_root=ROOT,
        spec=spec,
        view_spec=view,
    )
    case = materialize_evaluation_case_v231(repository_root=ROOT, spec=spec)
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view.hidden_mechanism,
    )
    graph = _residual_graph_v231(context=context, memory=memory)
    guard = evaluate_irreconcilable_guard_v233(
        witnesses=(),
        legal_sources=(),
        remaining_reads=0,
        guard_read_used=False,
    )
    hypotheses = build_runtime_hypotheses_v233(
        graph=graph,
        projection=projection,
    )
    request = build_discovery_synthesis_request_v233(
        graph=graph,
        projection=projection,
        guard=guard,
        hypotheses=hypotheses,
        unresolved_dimensions=("CAUSAL_MECHANISM",),
        top_shadow_matches=(),
    )
    return graph, projection, guard, hypotheses, request


def test_provider_response_cannot_emit_runtime_owned_fields() -> None:
    _graph, _projection, _guard, _hypotheses, request = _runtime_inputs()
    valid = provider_response_payload_v233(
        deterministic_synthesis_response_v233(request=request)
    )

    with pytest.raises(ValidationError, match="root_service"):
        DiscoverySynthesisResponseV233.model_validate(
            {**valid, "root_service": "provider-owned-root"}
        )


def test_v233_transport_forces_only_the_minimal_synthesis_schema() -> None:
    OpenAICompatibleDiscoveryTransportV233._v233_mode = True
    try:
        tool = OpenAICompatibleDiscoveryTransportV233._tool()
    finally:
        OpenAICompatibleDiscoveryTransportV233._v233_mode = False

    function = cast(dict[str, Any], tool["function"])
    parameters = cast(dict[str, Any], function["parameters"])
    properties = cast(dict[str, Any], parameters["properties"])
    assert set(properties) == set(DiscoverySynthesisResponseV233.model_fields)
    assert {
        "runtime_selected_root_service",
        "broad_fault_domain",
        "supporting_evidence_refs",
        "action_authority",
    }.isdisjoint(properties)


def test_v233_transport_preserves_the_v231_legacy_tool_schema() -> None:
    OpenAICompatibleDiscoveryTransportV233._v233_mode = False
    OpenAICompatibleDiscoveryTransportV233._v231_mode = True
    OpenAICompatibleDiscoveryTransportV231._v231_mode = False
    try:
        tool = OpenAICompatibleDiscoveryTransportV233._tool()
    finally:
        OpenAICompatibleDiscoveryTransportV233._v231_mode = False

    function = cast(dict[str, Any], tool["function"])
    parameters = cast(dict[str, Any], function["parameters"])
    properties = cast(dict[str, Any], parameters["properties"])
    assert "uncertainty_mode" in properties
    assert "competing_hypotheses" in properties
    assert "suspected_root_services" in properties


def test_two_repairs_parse_minimal_response_without_runtime_drift() -> None:
    graph, projection, guard, _hypotheses, request = _runtime_inputs()
    valid = provider_response_payload_v233(
        deterministic_synthesis_response_v233(request=request)
    )
    calls = 0

    def transport(_body: str) -> str:
        nonlocal calls
        calls += 1
        return "not-json" if calls == 1 else json.dumps(valid)

    outcome = call_discovery_provider_v233(request=request, transport=transport)
    report = build_provisional_report_v233(
        terminal="UNREGISTERED_INCIDENT_SUSPECTED",
        request=request,
        synthesis=outcome.synthesis,
    )

    assert outcome.protocol_repairs == 1
    assert outcome.provider_calls == 2
    assert report.runtime_selected_root_service == projection.selected_root_service
    assert report.broad_fault_domain is projection.selected_domain
    assert report.supporting_evidence_refs == projection.supporting_evidence_refs
    assert report.contradicting_evidence_refs == projection.contradicting_evidence_refs
    assert report.residual_anomaly_ids == graph.residual_anomaly_ids
    assert report.guard_disposition is IrreconcilableGuardDispositionV233.OPEN
    assert report.action_authority == "NONE"


def test_provider_authored_lists_are_canonicalized_before_strict_validation() -> None:
    _graph, _projection, _guard, _hypotheses, request = _runtime_inputs()
    valid = provider_response_payload_v233(
        deterministic_synthesis_response_v233(request=request)
    )
    valid["unresolved_questions"] = ["z-question", "a-question", "z-question"]
    valid["recommended_next_observations"] = [
        "z-observation",
        "a-observation",
        "z-observation",
    ]

    outcome = call_discovery_provider_v233(
        request=request,
        transport=lambda _body: json.dumps(valid),
    )

    assert outcome.synthesis.unresolved_questions == (
        "a-question",
        "z-question",
    )
    assert outcome.synthesis.recommended_next_observations == (
        "a-observation",
        "z-observation",
    )


def test_protocol_fails_after_exactly_two_repairs() -> None:
    _graph, _projection, _guard, _hypotheses, request = _runtime_inputs()
    calls = 0

    def invalid_transport(_body: str) -> str:
        nonlocal calls
        calls += 1
        return "{}"

    with pytest.raises(DiscoveryProviderProtocolFailureV23):
        call_discovery_provider_v233(request=request, transport=invalid_transport)

    assert calls == 3


def test_three_exact_transport_retries_are_bounded() -> None:
    _graph, _projection, _guard, _hypotheses, request = _runtime_inputs()
    valid = provider_response_payload_v233(
        deterministic_synthesis_response_v233(request=request)
    )
    calls = 0

    def transport(_body: str) -> str:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise DiscoveryProviderTransportErrorV23("transient")
        return json.dumps(valid)

    outcome = call_discovery_provider_v233(request=request, transport=transport)

    assert calls == 4
    assert outcome.transport_retries == 3
    assert outcome.protocol_repairs == 0


def test_closed_guard_cannot_build_report() -> None:
    _graph, _projection, guard, _hypotheses, request = _runtime_inputs()
    synthesis = deterministic_synthesis_response_v233(request=request)
    closed = guard.model_copy(
        update={"disposition": IrreconcilableGuardDispositionV233.IRRECONCILABLE}
    )
    unsafe_request = request.model_copy(update={"guard_decision": closed})

    with pytest.raises(ValueError, match="OPEN"):
        build_provisional_report_v233(
            terminal="UNREGISTERED_INCIDENT_SUSPECTED",
            request=unsafe_request,
            synthesis=synthesis,
        )


def test_review_and_shadow_projections_preserve_runtime_ownership() -> None:
    _graph, projection, _guard, _hypotheses, request = _runtime_inputs()
    report = build_provisional_report_v233(
        terminal="UNREGISTERED_INCIDENT_SUSPECTED",
        request=request,
        synthesis=deterministic_synthesis_response_v233(request=request),
    )

    display = render_review_display_v233(report)
    shadow = build_shadow_projection_v233(
        report=report,
        canonical_label="development-only-incident",
        review_record_id="review-v233-0000000000000000",
    )

    assert display["runtime_selected_root"] == projection.selected_root_service
    assert display["runtime_selected_broad_domain"] == projection.selected_domain.value
    assert display["action_authority"] == "NONE"
    assert shadow.runtime_selected_domain is projection.selected_domain
    assert shadow.domain_candidate_scores == projection.domain_scores
    assert shadow.guard_witness_ids == report.contradiction_witness_ids
    assert shadow.remediation_authority == "NONE"


def test_candidate_filter_explicitly_rejects_v233_provisional_report() -> None:
    _graph, _projection, _guard, _hypotheses, request = _runtime_inputs()
    report = build_provisional_report_v233(
        terminal="UNREGISTERED_INCIDENT_SUSPECTED",
        request=request,
        synthesis=deterministic_synthesis_response_v233(request=request),
    )

    with pytest.raises(CandidateFilterError, match="provisional"):
        filter_runbook_candidates(
            diagnosis=cast(Any, report),
            registry=cast(Any, None),
            diagnosis_evidence=cast(Any, None),
        )


def test_v232_provider_development_gate_is_runtime_bound() -> None:
    audit = build_v232_provider_audit_v233(repository_root=ROOT)

    assert audit.report_eligible_cases == 14
    assert audit.initial_protocol_valid >= 14 * 0.95
    assert audit.post_repair_success == 14
    assert audit.root_domain_evidence_drift == 0
    assert audit.action_authority_violations == 0
    assert audit.registered_known_unchanged == 4
    assert audit.no_incident_unchanged == 3
    assert audit.provider_synthesis_iteration_count == 1
