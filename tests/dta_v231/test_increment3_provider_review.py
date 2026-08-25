from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v23.conflict_model_v231 import assess_conflict_v231
from ecomsre.dta_v2.v23.contracts_v231 import (
    ProvisionalIncidentReportV231,
    ReportUncertaintyModeV231,
    ReviewRecommendationV231,
    build_competing_hypothesis_set_v231,
    build_competing_report_v231,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
)
from ecomsre.dta_v2.v23.discovery_provider_v231 import (
    DISCOVERY_SYSTEM_PROMPT_V231,
    build_discovery_provider_request_v231,
    call_discovery_provider_v231,
    provider_response_payload_v231,
)
from ecomsre.dta_v2.v23.evaluation import (
    FixedEvaluationArtifactV23,
    _build_common_context_v23,
    load_evaluation_case_set_v23,
    load_evaluation_ontology_views_v23,
    materialize_evaluation_case_v23,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    _run_strict_arm_v231,
    run_conflict_aware_arm_v231,
)
from ecomsre.dta_v2.v23.ontology_view import build_active_ontology_view_v23
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    ShadowFaultRegistryV23,
)
from ecomsre.dta_v2.v23.review_registry_v231 import (
    HumanReviewRecordV231,
    LocalReviewStoreV231,
    ShadowFaultEntryV231,
    build_review_queue_item_v231,
    decide_review_v231,
    project_legacy_shadow_registry_v231,
    render_review_display_v231,
)
from ecomsre.dta_v2.v23.cli import main


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def context():
    artifact = FixedEvaluationArtifactV23.model_validate_json(
        (ROOT / "docs/results/dta-v23-open-world-evaluation.json").read_bytes()
    )
    pair = next(item for item in artifact.pairs if item.case_id == "ow-011")
    graph = pair.open_world.residual_graph
    assert graph is not None
    assessment = assess_conflict_v231(
        graph=graph,
        legal_sources=(),
        remaining_reads=0,
    )
    hypotheses = build_competing_hypothesis_set_v231(
        graph=graph,
        assessment=assessment,
    )
    report = build_competing_report_v231(
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
    )
    return graph, assessment, hypotheses, report


def test_v231_provider_prompt_and_request_bind_competing_state(context) -> None:
    graph, assessment, hypotheses, _report = context
    request = build_discovery_provider_request_v231(
        active_ontology=build_active_ontology_view_v23(
            candidate_services=graph.candidate_services
        ),
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
        top_shadow_matches=(),
    )

    assert "Do not force a single mechanism" in DISCOVERY_SYSTEM_PROMPT_V231
    assert "cannot authorize remediation" in DISCOVERY_SYSTEM_PROMPT_V231
    assert request.conflict_assessment["assessment_sha256"] == assessment.assessment_sha256
    assert len(request.competing_hypotheses) >= 2
    assert request.validation_graph == graph


def test_v231_provider_validates_the_evidence_bound_report(context) -> None:
    graph, assessment, hypotheses, report = context
    request = build_discovery_provider_request_v231(
        active_ontology=build_active_ontology_view_v23(
            candidate_services=graph.candidate_services
        ),
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
        top_shadow_matches=(),
    )
    bodies: list[str] = []

    def transport(body: str) -> str:
        bodies.append(body)
        return json.dumps(provider_response_payload_v231(report))

    outcome = call_discovery_provider_v231(request=request, transport=transport)

    assert outcome.report == report
    assert outcome.provider_calls == 1
    assert outcome.protocol_repairs == 0
    assert outcome.transport_retries == 0
    assert len(bodies) == 1


def test_v231_provider_canonicalizes_only_set_semantic_fields(context) -> None:
    graph, assessment, hypotheses, report = context
    request = build_discovery_provider_request_v231(
        active_ontology=build_active_ontology_view_v23(
            candidate_services=graph.candidate_services
        ),
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
        top_shadow_matches=(),
    )
    payload = provider_response_payload_v231(report)
    payload["supporting_evidence_refs"] = list(
        reversed(payload["supporting_evidence_refs"])
    )
    payload["suspected_root_services"] = list(
        reversed(payload["suspected_root_services"])
    )

    outcome = call_discovery_provider_v231(
        request=request,
        transport=lambda _body: json.dumps(payload),
    )

    assert outcome.protocol_repairs == 0
    assert outcome.report.supporting_evidence_refs == tuple(
        sorted(set(payload["supporting_evidence_refs"]))
    )
    assert outcome.report.competing_hypotheses == hypotheses.hypotheses


def test_v231_provider_repairs_instead_of_silently_rewriting_confidence(context) -> None:
    graph, assessment, hypotheses, report = context
    request = build_discovery_provider_request_v231(
        active_ontology=build_active_ontology_view_v23(
            candidate_services=graph.candidate_services
        ),
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
        top_shadow_matches=(),
    )
    payload = provider_response_payload_v231(report)
    payload.update(
        confidence=0.95,
        confidence_band="HIGH",
        review_recommendation="CONSIDER_SHADOW_REGISTRATION",
    )

    valid = provider_response_payload_v231(report)
    bodies: list[str] = []

    def transport(body: str) -> str:
        bodies.append(body)
        return json.dumps(payload if len(bodies) == 1 else valid)

    outcome = call_discovery_provider_v231(request=request, transport=transport)

    assert outcome.protocol_repairs == 1
    assert outcome.report == report
    assert len(bodies) == 2


def test_v231_provider_repairs_and_exact_transport_retries_remain_bounded(context) -> None:
    graph, assessment, hypotheses, report = context
    request = build_discovery_provider_request_v231(
        active_ontology=build_active_ontology_view_v23(
            candidate_services=graph.candidate_services
        ),
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
        top_shadow_matches=(),
    )
    invalid_bodies: list[str] = []

    def invalid(body: str) -> str:
        invalid_bodies.append(body)
        return json.dumps({"terminal": "INVALID"})

    with pytest.raises(DiscoveryProviderProtocolFailureV23):
        call_discovery_provider_v231(request=request, transport=invalid)
    assert len(invalid_bodies) == 3
    assert len(set(invalid_bodies)) == 3

    retry_bodies: list[str] = []

    def flaky(body: str) -> str:
        retry_bodies.append(body)
        if len(retry_bodies) <= 3:
            raise DiscoveryProviderTransportErrorV23("transient")
        return json.dumps(provider_response_payload_v231(report))

    outcome = call_discovery_provider_v231(request=request, transport=flaky)
    assert outcome.transport_retries == 3
    assert len(set(retry_bodies)) == 1


def test_review_display_defaults_competing_reports_to_more_evidence(context) -> None:
    graph, _assessment, _hypotheses, report = context
    item = build_review_queue_item_v231(
        report=report,
        graph=graph,
        source_case_id="ow-011-development",
        queued_at=NOW,
        automated_fixture=True,
    )
    display = render_review_display_v231(item)

    assert display["leading_hypothesis"]
    alternatives = display["alternatives"]
    assert isinstance(alternatives, list) and len(alternatives) >= 1
    assert display["shared_supporting_evidence"]
    assert display["unresolved_questions"]
    assert display["recommended_discriminating_reads"] == list(
        report.recommended_discriminating_observations
    )
    assert display["default_recommendation"] == (
        ReviewRecommendationV231.REQUEST_MORE_EVIDENCE.value
    )


def test_explicit_accept_as_new_preserves_competing_state_in_shadow(context) -> None:
    graph, _assessment, _hypotheses, report = context
    item = build_review_queue_item_v231(
        report=report,
        graph=graph,
        source_case_id="ow-011-development",
        queued_at=NOW,
        automated_fixture=True,
    )
    accepted = decide_review_v231(
        item=item,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer="TEST_REVIEWER",
        review_note="Simulated explicit acceptance for compatibility coverage.",
        canonical_label="competing-pool-dependency-pattern",
        requested_observations=(),
        reviewed_at=NOW,
    )

    assert accepted.shadow_entry is not None
    shadow = accepted.shadow_entry
    assert shadow.leading_hypothesis_id == report.preferred_hypothesis_id
    assert len(shadow.alternative_hypotheses) >= 1
    assert shadow.unresolved_dimensions
    assert shadow.uncertainty_mode is ReportUncertaintyModeV231.COMPETING_HYPOTHESES
    assert shadow.remediation_authority == "NONE"

    non_accept = decide_review_v231(
        item=item,
        decision=HumanReviewDecisionV23.REQUEST_MORE_EVIDENCE,
        reviewer="TEST_REVIEWER",
        review_note="Simulated request for the deciding observation.",
        canonical_label=None,
        requested_observations=("one bounded trace comparison",),
        reviewed_at=NOW,
    )
    assert non_accept.shadow_entry is None


def test_legacy_shadow_registry_projects_without_losing_non_actionable_boundary() -> None:
    legacy = ShadowFaultRegistryV23.model_validate_json(
        (ROOT / "config/dta-v23/examples/shadow-registry.json").read_bytes()
    )
    projected = project_legacy_shadow_registry_v231(legacy)

    assert len(projected.entries) == len(legacy.entries)
    assert projected.entries[0].uncertainty_mode is (
        ReportUncertaintyModeV231.SINGLE_LEADING_HYPOTHESIS
    )
    assert projected.entries[0].alternative_hypotheses == ()
    assert projected.entries[0].remediation_authority == "NONE"


def test_review_cli_renders_and_decides_a_v231_competing_report(
    context,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph, _assessment, _hypotheses, report = context
    store = LocalReviewStoreV231(tmp_path / "review-cli")
    item = build_review_queue_item_v231(
        report=report,
        graph=graph,
        source_case_id="ow-011-development",
        queued_at=NOW,
        automated_fixture=True,
    )
    store.enqueue(item)

    assert main(
        (
            "review",
            "show",
            report.report_id,
            "--local-root",
            str(store.root),
        )
    ) == 0
    rendered = capsys.readouterr().out
    assert "leading_hypothesis" in rendered
    assert "REQUEST_MORE_EVIDENCE" in rendered

    assert main(
        (
            "review",
            "decide",
            report.report_id,
            "--decision",
            "REQUEST_MORE_EVIDENCE",
            "--reviewer",
            "TEST_REVIEWER",
            "--request-observation",
            "one bounded trace comparison",
            "--note",
            "Simulated explicit review from the CLI.",
            "--local-root",
            str(store.root),
        )
    ) == 0
    assert '"shadow_entry": null' in capsys.readouterr().out


def test_protocol_failure_preserves_provider_call_and_repair_cost() -> None:
    specs = load_evaluation_case_set_v23(
        ROOT / "config/dta-v23/evaluation/cases.json"
    )
    spec = next(item for item in specs.cases if item.case_id == "ow-011")
    case = materialize_evaluation_case_v23(repository_root=ROOT, spec=spec)
    case = case.model_copy(update={"case_id": "vx-999"})
    context = _build_common_context_v23(case=case, hidden_mechanism=None)

    class CountingInvalidTransport:
        provider_calls = 0
        protocol_repairs = 0
        transport_retries = 0
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        latency_ms = 0.0

        def __call__(self, body: str) -> str:
            self.provider_calls += 1
            parsed = json.loads(body)
            repair = parsed.get("protocol_repair")
            if isinstance(repair, dict):
                self.protocol_repairs += 1
            return json.dumps({"terminal": "INVALID"})

    transport = CountingInvalidTransport()
    run = run_conflict_aware_arm_v231(
        context,
        provider_transport=transport,
    )

    assert run.final_disposition == "PROVIDER_FAILED"
    assert run.provider_error_code == "PROTOCOL_FAILED"
    assert run.provider_cost.provider_calls == 3
    assert run.provider_cost.protocol_repairs == 2


def test_strict_protocol_failure_reconciles_telemetry_without_changing_behavior() -> None:
    specs = load_evaluation_case_set_v23(
        ROOT / "config/dta-v23/evaluation/cases.json"
    )
    spec = next(item for item in specs.cases if item.case_id == "ow-003")
    case = materialize_evaluation_case_v23(repository_root=ROOT, spec=spec)
    views = load_evaluation_ontology_views_v23(
        ROOT / "config/dta-v23/evaluation/ontology-views.json"
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=views.require(spec.case_id).hidden_mechanism,
    )

    class CountingInvalidTransport:
        provider_calls = 0
        protocol_repairs = 0
        transport_retries = 0
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        latency_ms = 0.0

        def __call__(self, body: str) -> str:
            self.provider_calls += 1
            parsed = json.loads(body)
            if isinstance(parsed.get("protocol_repair"), dict):
                self.protocol_repairs += 1
            return json.dumps({"terminal": "INVALID"})

    run = _run_strict_arm_v231(
        context=context,
        provider_transport=CountingInvalidTransport(),
    )

    assert run.final_disposition == "PROVIDER_FAILED"
    assert run.provider_error_code == "PROTOCOL_FAILED"
    assert run.provider_cost.provider_calls == 3
    assert run.provider_cost.protocol_repairs == 2


def test_exact_goal_named_compatibility_examples_validate() -> None:
    examples = ROOT / "config/dta-v231/examples"

    report = ProvisionalIncidentReportV231.model_validate_json(
        (examples / "competing-report.json").read_bytes()
    )
    review = HumanReviewRecordV231.model_validate_json(
        (examples / "review-record.json").read_bytes()
    )
    shadow = ShadowFaultEntryV231.model_validate_json(
        (examples / "shadow-entry.json").read_bytes()
    )

    assert report.action_authority == "NONE"
    assert review.report_id == report.report_id
    assert report.report_id in shadow.positive_report_ids
    assert shadow.remediation_authority == "NONE"
