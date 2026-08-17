from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.agent_contracts import ProviderUsage
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ToolName,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21
from ecomsre.dta_v2.v21.agent_provider import ProviderTurnV21
from ecomsre.dta_v2.v21.capture_campaign import build_default_capture_plan_v21
from ecomsre.dta_v2.v21.contracts import (
    DtaDiagnosisV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.evaluation_campaign import (
    build_evaluation_preregistration_v21,
    build_evaluation_schedule_v21,
)
from ecomsre.dta_v2.v21.evaluation_cli import (
    publish_development_report_v21,
    run_development_evaluation_v21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    PublicCaseBindingV21,
    PublicEvaluationManifestV21,
    ReplayObservationFixtureV21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.owned_capture import build_evaluator_truth_v21
from ecomsre.dta_v2.v21.planner_contracts import (
    DiagnosticHypothesisV21,
    HypothesisStatusV21,
    PlannerNextStepV21,
    build_evidence_plan_decision_v21,
)
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_live_sandbox.contracts import write_private_json


ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-5.4-mini-2026-03-17"
START = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
END = START + timedelta(seconds=30)


def _digest(model_type, payload, field):
    draft = model_type.model_construct(**payload, **{field: "0" * 64})
    return semantic_sha256(draft.model_dump(mode="json", exclude={field}))


def _fixture(request):
    scope = request.services if hasattr(request, "services") else (request.service,)
    payload = {
        "schema_version": "dta-v21.replay-observation-fixture.v1",
        "tool": request.tool,
        "service_scope": tuple(sorted(scope)),
        "records": (),
        "truncated": False,
        "error_code": None,
    }
    return ReplayObservationFixtureV21.model_validate(
        {
            **payload,
            "fixture_sha256": _digest(
                ReplayObservationFixtureV21, payload, "fixture_sha256"
            ),
        }
    )


def _request(tool, service, run_id):
    if tool is ToolName.QUERY_METRICS:
        return build_query_metrics_request(
            run_id=run_id,
            service=service,
            started_at=START,
            ended_at=END,
            metric_kinds=(MetricKind.ERROR_RATE,),
            max_results=1,
        )
    if tool is ToolName.SEARCH_LOGS:
        return build_search_logs_request(
            run_id=run_id,
            service=service,
            started_at=START,
            ended_at=END,
            max_records=4,
        )
    if tool is ToolName.QUERY_TRACE_NEIGHBORHOOD:
        return build_trace_neighborhood_request(
            run_id=run_id,
            service=service,
            started_at=START,
            ended_at=END,
            max_spans=4,
        )
    if tool is ToolName.INSPECT_SERVICE_RUNTIME:
        return build_inspect_service_runtime_request(
            run_id=run_id, services=(service,), max_results=1
        )
    return build_inspect_resource_usage_request(
        run_id=run_id,
        services=(service,),
        sampling_window_seconds=5,
        sample_count=3,
    )


def _prepare_development(root: Path):
    plan = build_default_capture_plan_v21(base_head="a" * 40)
    service_by_family = {
        "PAYMENT_CONFIGURATION": "payment",
        "EMAIL_MEMORY_LEAK": "email",
        "RECOMMENDATION_UNAVAILABLE": "recommendation",
        "AD_CPU_SATURATION": "ad",
        "EMAIL_UNAVAILABLE": "email",
        "PRODUCT_CATALOG_UNAVAILABLE": "product-catalog",
        "SHIPPING_DEPENDENCY_LATENCY": "shipping",
        "NO_FAULT": "payment",
        "MISSING_CONFLICTING_EVIDENCE": "email",
    }
    bindings = []
    for plan_case in plan.cases[:12]:
        run_id = semantic_sha256(plan_case.case_id)[:32]
        service = service_by_family[plan_case.operational_family.value]
        fixtures = tuple(
            sorted(
                (
                    _fixture(_request(tool, service, run_id))
                    for tool in plan_case.full_context_tools
                ),
                key=lambda item: item.tool.value,
            )
        )
        payload = {
            "schema_version": "dta-v21.agent-visible-replay-case.v1",
            "case_id": plan_case.case_id,
            "scenario_id": plan_case.scenario_id,
            "captured_started_at": START,
            "captured_ended_at": END,
            "observations": fixtures,
            "full_context_tools": plan_case.full_context_tools,
        }
        case = AgentVisibleReplayCaseV21.model_validate(
            {
                **payload,
                "case_sha256": _digest(
                    AgentVisibleReplayCaseV21, payload, "case_sha256"
                ),
            }
        )
        truth = build_evaluator_truth_v21(plan_case)
        write_private_json(
            root / "agent-visible" / f"{case.case_id}.json",
            case,
            create_once=True,
        )
        write_private_json(
            root / "evaluator-truth" / f"{case.case_id}.json",
            truth,
            create_once=True,
        )
        bindings.append(
            PublicCaseBindingV21(
                case_id=case.case_id,
                case_sha256=case.case_sha256,
                truth_sha256=truth.truth_sha256,
                split_sha256=semantic_sha256("DEVELOPMENT"),
            )
        )
    held_out = tuple(
        PublicCaseBindingV21(
            case_id=f"dta21-case-{index:03d}",
            case_sha256=semantic_sha256({"held-case": index}),
            truth_sha256=semantic_sha256({"held-truth": index}),
            split_sha256=semantic_sha256("HELD_OUT"),
        )
        for index in range(13, 21)
    )
    payload = {
        "schema_version": "dta-v21.public-evaluation-manifest.v1",
        "case_schema_version": "dta-v21.agent-visible-replay-case.v1",
        "truth_schema_version": "dta-v21.evaluator-case-truth.v1",
        "development_cases": tuple(bindings),
        "held_out_cases": held_out,
    }
    return PublicEvaluationManifestV21.model_validate(
        {
            **payload,
            "manifest_sha256": _digest(
                PublicEvaluationManifestV21, payload, "manifest_sha256"
            ),
        }
    )


class _AlwaysAbstainProvider:
    def __init__(self, arm):
        self.identity = next(
            item
            for item in build_three_arm_identities_v21(
                model_id=MODEL, max_completion_tokens=1600
            )
            if item.arm is arm
        )
        self.attempted_calls = 0

    def investigation_turn(self, *, context, visible_state, read_tools_enabled):
        del visible_state, read_tools_enabled
        self.attempted_calls += 1
        if self.identity.arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER:
            service = context.candidate_services[0]
            hypothesis = DiagnosticHypothesisV21(
                hypothesis_id="h1",
                root_service=service,
                fault_domain=FaultDomainV21.CONFIGURATION,
                fault_mechanism=FaultMechanismV21.CONFIGURATION_ERROR,
                status=HypothesisStatusV21.ACTIVE,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
                unresolved_evidence_sources=(EvidenceSourceV21.METRICS,),
            )
            plan = build_evidence_plan_decision_v21(
                run_id=context.run_id,
                turn_ordinal=1,
                hypotheses=(hypothesis,),
                next_step=PlannerNextStepV21.ABSTAIN,
                evidence_gap_sources=(EvidenceSourceV21.METRICS,),
                read_request=None,
                diagnosis=None,
                bounded_rationale="The visible evidence is insufficient.",
            )
            return ProviderTurnV21(
                function_name="scripted_plan",
                tool_call_id="scripted-1",
                raw_response_sha256=semantic_sha256("scripted-plan"),
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                monotonic_latency_ms=1,
                plan_decision=plan,
            )
        diagnosis = DtaDiagnosisV21(
            schema_version="dta-v21.diagnosis.v1",
            run_id=context.run_id,
            terminal=TerminalV21.ABSTAIN,
            root_service=None,
            root_entity_ref=None,
            fault_domain=None,
            mechanism=None,
            confidence=None,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
            evidence_source_types=(),
            uncertainties=("The visible evidence is insufficient.",),
            summary="The bounded evaluation entry abstained.",
        )
        return ProviderTurnV21(
            function_name="scripted_diagnosis",
            tool_call_id="scripted-1",
            raw_response_sha256=semantic_sha256("scripted-diagnosis"),
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            monotonic_latency_ms=1,
            diagnosis=diagnosis,
        )

    def action_selection_turn(self, *, diagnosis, resolved_evidence, candidate_view):
        raise AssertionError("abstention must not reach Action Selection")


def test_development_cli_runs_exact_40_and_rejects_identical_rerun(
    tmp_path: Path,
) -> None:
    development_root = tmp_path / "development"
    public_manifest = _prepare_development(development_root)
    schedule = build_evaluation_schedule_v21(
        seed_sha256=semantic_sha256("development runner test")
    )
    preregistration = build_evaluation_preregistration_v21(
        model_id=MODEL,
        max_completion_tokens=1600,
        schedule_sha256=schedule.schedule_sha256,
    )
    provider_env = tmp_path / "provider.env"
    provider_env.write_text(
        "ECOMSRE_LLM_BASE_URL=https://example.com/v1\n"
        "ECOMSRE_LLM_API_KEY=test-key\n"
        f"ECOMSRE_LLM_MODEL={MODEL}\n",
        encoding="utf-8",
    )
    provider_env.chmod(0o600)

    report, receipt = run_development_evaluation_v21(
        repository_root=ROOT,
        provider_env_path=provider_env,
        development_root=development_root,
        private_attempts_root=tmp_path / "attempts",
        attempt_id="a" * 32,
        public_manifest=public_manifest,
        schedule=schedule,
        preregistration=preregistration,
        provider_factory=lambda arm, config: (
            _AlwaysAbstainProvider(arm)
            if isinstance(config, OpenAICompatibleConfig)
            else pytest.fail("invalid Provider config")
        ),
    )

    assert receipt.entry_count == 40
    assert len(receipt.entry_sha256s) == 40
    assert report.primary_entry_count == 36
    assert report.ablation_entry_count == 4
    report_path = tmp_path / "public/development-report.json"
    disposition_path = tmp_path / "public/current-disposition.json"
    disposition = publish_development_report_v21(
        report=report,
        report_path=report_path,
        disposition_path=disposition_path,
    )
    assert disposition.held_out_executed is False
    assert disposition.report_sha256 == report.report_sha256
    assert report_path.stat().st_mode & 0o777 == 0o644
    with pytest.raises(ValueError, match="identical development"):
        run_development_evaluation_v21(
            repository_root=ROOT,
            provider_env_path=provider_env,
            development_root=development_root,
            private_attempts_root=tmp_path / "attempts",
            attempt_id="b" * 32,
            public_manifest=public_manifest,
            schedule=schedule,
            preregistration=preregistration,
            provider_factory=lambda arm, config: _AlwaysAbstainProvider(arm),
        )
