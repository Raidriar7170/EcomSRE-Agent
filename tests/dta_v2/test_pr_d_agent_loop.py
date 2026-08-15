from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.agent import (
    AgentFailureCode,
    AgentRunTerminal,
    run_tool_using_agent,
)
from ecomsre.dta_v2.agent_contracts import (
    ActionSelectionDecision,
    ProviderUsage,
    build_alert_context,
)
from ecomsre.dta_v2.agent_provider import ProviderTurn, build_provider_identity
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionParameter,
    DtaDiagnosis,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RunbookId,
    Terminal,
    semantic_sha256,
)
from ecomsre.dta_v2.read_tools import FakeReadBackend
from ecomsre.dta_v2.registry import (
    load_runbook_registry,
    load_scenario_registry,
)
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ReadToolRequest,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_trace_neighborhood_request,
)


ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-5.4-mini-2026-03-17"
START = datetime(2026, 8, 16, 6, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)


class ScriptedProvider:
    def __init__(
        self,
        investigation: list[ReadToolRequest | DtaDiagnosis | BaseException],
        action: ActionSelectionDecision | BaseException | None,
    ) -> None:
        self.investigation = investigation
        self.action = action
        self.identity = build_provider_identity(MODEL)
        self.attempted_calls = 0
        self.accepted_calls: tuple[ProviderTurn, ...] = ()
        self.action_calls = 0
        self.read_tools_enabled_values: list[bool] = []

    def _turn(self, value):
        self.attempted_calls += 1
        if isinstance(value, BaseException):
            raise value
        turn = ProviderTurn(
            function_name=(
                "submit_dta_diagnosis"
                if isinstance(value, DtaDiagnosis)
                else value.tool.value
            ),
            tool_call_id=f"scripted-{self.attempted_calls}",
            raw_response={"id": f"scripted-{self.attempted_calls}"},
            raw_response_sha256=semantic_sha256(
                {"id": f"scripted-{self.attempted_calls}"}
            ),
            raw_arguments={"scripted": True},
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            monotonic_latency_ms=1,
            read_request=value if not isinstance(value, DtaDiagnosis) else None,
            diagnosis=value if isinstance(value, DtaDiagnosis) else None,
        )
        self.accepted_calls = (*self.accepted_calls, turn)
        return turn

    def investigation_turn(self, *, context, transcript, read_tools_enabled):
        del context, transcript
        self.read_tools_enabled_values.append(read_tools_enabled)
        return self._turn(self.investigation.pop(0))

    def action_selection_turn(self, *, diagnosis, candidate_view):
        del diagnosis, candidate_view
        self.action_calls += 1
        assert self.action is not None
        if isinstance(self.action, BaseException):
            raise self.action
        self.attempted_calls += 1
        turn = ProviderTurn(
            function_name="submit_dta_action_selection",
            tool_call_id=f"scripted-{self.attempted_calls}",
            raw_response={"id": f"scripted-{self.attempted_calls}"},
            raw_response_sha256=semantic_sha256(
                {"id": f"scripted-{self.attempted_calls}"}
            ),
            raw_arguments=self.action.model_dump(mode="json"),
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            monotonic_latency_ms=1,
            action_selection=self.action,
        )
        self.accepted_calls = (*self.accepted_calls, turn)
        return turn


def _context(run_id: str, scenario_index: int):
    scenario = load_scenario_registry(
        ROOT / "config/dta-v2/scenarios/agent-visible"
    ).scenarios[scenario_index]
    return build_alert_context(
        scenario=scenario,
        run_id=run_id,
        started_at=START,
        ended_at=END,
    )


def _metrics(run_id: str, service: str):
    return build_query_metrics_request(
        run_id=run_id,
        service=service,
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT),
        max_results=6,
    )


def _runtime(run_id: str, service: str):
    return build_inspect_service_runtime_request(
        run_id=run_id, services=(service,), max_results=1
    )


def _resource(run_id: str, service: str):
    return build_inspect_resource_usage_request(
        run_id=run_id,
        services=(service,),
        sampling_window_seconds=3,
        sample_count=3,
    )


def _trace(run_id: str, service: str):
    return build_trace_neighborhood_request(
        run_id=run_id,
        service=service,
        started_at=START,
        ended_at=END,
        max_spans=10,
    )


def _diagnosis(
    *,
    run_id: str,
    service: str,
    domain: FaultDomain,
    mechanism: FaultMechanism,
    sources: tuple[EvidenceSource, ...],
):
    refs = tuple(
        f"evidence://{run_id}/{source.value.lower()}/{index:04d}"
        for index, source in enumerate(sources, start=1)
    )
    return DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=run_id,
        terminal=Terminal.COMPLETED,
        root_service=service,
        root_entity_ref=f"service:{service}",
        fault_domain=domain,
        mechanism=mechanism,
        confidence=0.9,
        supporting_evidence_refs=refs,
        contradicting_evidence_refs=(),
        evidence_source_types=sources,
        uncertainties=(),
        summary="The bounded typed observations support one mechanism.",
    )


def _decision(
    *,
    run_id: str,
    runbook: RunbookId,
    service: str,
    sources: tuple[EvidenceSource, ...],
):
    return ActionSelectionDecision(
        schema_version="dta-v2.action-selection-decision.v1",
        disposition=ActionDisposition.EXECUTE_RUNBOOK,
        runbook_id=runbook,
        target_service=service,
        parameters=(
            ()
            if runbook is RunbookId.ROLLBACK_CONFIGURATION
            else (ActionParameter(name="wait_for_health_seconds", value=30),)
        ),
        supporting_evidence_refs=tuple(
            f"evidence://{run_id}/{source.value.lower()}/{index:04d}"
            for index, source in enumerate(sources, start=1)
        ),
        rationale="The exact safe candidate matches all cited evidence.",
    )


@pytest.mark.parametrize(
    (
        "scenario_index",
        "service",
        "domain",
        "mechanism",
        "sources",
        "requests",
        "runbook",
    ),
    (
        (
            0,
            "payment",
            FaultDomain.CONFIGURATION,
            FaultMechanism.CONFIGURATION_ERROR,
            (EvidenceSource.METRICS, EvidenceSource.TRACES),
            (_metrics, _trace),
            RunbookId.ROLLBACK_CONFIGURATION,
        ),
        (
            1,
            "recommendation",
            FaultDomain.SERVICE_RUNTIME,
            FaultMechanism.SERVICE_UNAVAILABLE,
            (EvidenceSource.METRICS, EvidenceSource.RUNTIME),
            (_metrics, _runtime),
            RunbookId.RESTART_SERVICE,
        ),
        (
            2,
            "email",
            FaultDomain.LOCAL_RESOURCE,
            FaultMechanism.MEMORY_LEAK,
            (
                EvidenceSource.METRICS,
                EvidenceSource.RUNTIME,
                EvidenceSource.RESOURCES,
            ),
            (_metrics, _runtime, _resource),
            RunbookId.MITIGATE_MEMORY_LEAK,
        ),
    ),
)
def test_replay_matrix_produces_exact_candidate_bound_proposals(
    scenario_index,
    service,
    domain,
    mechanism,
    sources,
    requests,
    runbook,
) -> None:
    run_id = str(scenario_index + 1) * 32
    diagnosis = _diagnosis(
        run_id=run_id,
        service=service,
        domain=domain,
        mechanism=mechanism,
        sources=sources,
    )
    provider = ScriptedProvider(
        [*(builder(run_id, service) for builder in requests), diagnosis],
        _decision(
            run_id=run_id,
            runbook=runbook,
            service=service,
            sources=sources,
        ),
    )
    result = run_tool_using_agent(
        context=_context(run_id, scenario_index),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminal.COMPLETED
    assert result.failure_code is None
    assert result.diagnosis == diagnosis
    assert result.candidate_set is not None
    assert tuple(item.runbook_id for item in result.candidate_set.write_candidates) == (
        runbook,
    )
    assert result.action_proposal is not None
    assert result.action_proposal.runbook_id is runbook
    assert result.read_tool_dispatch_count == len(sources)
    assert result.provider_turn_count == len(sources) + 2


def test_duplicate_consumes_dispatch_and_fifth_turn_is_diagnosis_only() -> None:
    run_id = "d" * 32
    first = _metrics(run_id, "payment")
    diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=run_id,
        terminal=Terminal.NEED_MORE_EVIDENCE,
        root_service=None,
        root_entity_ref=None,
        fault_domain=None,
        mechanism=None,
        confidence=0.2,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
        evidence_source_types=(),
        uncertainties=("The bounded observations remain insufficient.",),
        summary="More independent evidence is required.",
    )
    provider = ScriptedProvider(
        [first, first, _runtime(run_id, "payment"), _trace(run_id, "payment"), diagnosis],
        None,
    )
    backend = FakeReadBackend.healthy()
    result = run_tool_using_agent(
        context=_context(run_id, 0),
        backend=backend,
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=provider,
    )

    assert result.terminal is AgentRunTerminal.NEED_MORE_EVIDENCE
    assert result.read_tool_dispatch_count == 4
    assert backend.call_count == 3
    assert provider.read_tools_enabled_values == [True, True, True, True, False]
    assert result.evidence_store.observations[1].error_code.value == "DUPLICATE_REQUEST"
    assert provider.action_calls == 0
    assert result.candidate_set is None
    assert result.action_proposal is None


def test_incompatible_completed_diagnosis_can_select_no_action() -> None:
    run_id = "e" * 32
    diagnosis = _diagnosis(
        run_id=run_id,
        service="payment",
        domain=FaultDomain.UNKNOWN,
        mechanism=FaultMechanism.UNKNOWN,
        sources=(EvidenceSource.METRICS,),
    )
    decision = ActionSelectionDecision(
        schema_version="dta-v2.action-selection-decision.v1",
        disposition=ActionDisposition.NO_ACTION,
        runbook_id=None,
        target_service=None,
        parameters=(),
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        rationale="No visible write candidate is compatible.",
    )
    result = run_tool_using_agent(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=ScriptedProvider([_metrics(run_id, "payment"), diagnosis], decision),
    )
    assert result.terminal is AgentRunTerminal.COMPLETED
    assert result.candidate_set is not None
    assert result.candidate_set.write_candidates == ()
    assert result.action_proposal is not None
    assert result.action_proposal.disposition is ActionDisposition.NO_ACTION


def test_unresolved_diagnosis_and_provider_failure_are_typed_failures() -> None:
    run_id = "f" * 32
    diagnosis = _diagnosis(
        run_id=run_id,
        service="payment",
        domain=FaultDomain.CONFIGURATION,
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        sources=(EvidenceSource.METRICS, EvidenceSource.TRACES),
    )
    unresolved = run_tool_using_agent(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=ScriptedProvider([_metrics(run_id, "payment"), diagnosis], None),
    )
    assert unresolved.terminal is AgentRunTerminal.FAILED
    assert unresolved.failure_code is AgentFailureCode.DIAGNOSIS_BINDING_FAILURE
    assert unresolved.action_proposal is None

    failed = run_tool_using_agent(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=ScriptedProvider([ConnectionError("secret transport detail")], None),
    )
    assert failed.terminal is AgentRunTerminal.FAILED
    assert failed.failure_code is AgentFailureCode.PROVIDER_TRANSPORT_FAILURE
    assert failed.diagnosis is None
    assert "secret transport detail" not in failed.model_dump_json()


def test_out_of_range_action_parameter_is_typed_binding_failure() -> None:
    run_id = "9" * 32
    diagnosis = _diagnosis(
        run_id=run_id,
        service="recommendation",
        domain=FaultDomain.SERVICE_RUNTIME,
        mechanism=FaultMechanism.SERVICE_UNAVAILABLE,
        sources=(EvidenceSource.METRICS, EvidenceSource.RUNTIME),
    )
    valid = _decision(
        run_id=run_id,
        runbook=RunbookId.RESTART_SERVICE,
        service="recommendation",
        sources=(EvidenceSource.METRICS, EvidenceSource.RUNTIME),
    )
    invalid = valid.model_copy(
        update={
            "parameters": (
                ActionParameter(name="wait_for_health_seconds", value=121),
            )
        }
    )
    result = run_tool_using_agent(
        context=_context(run_id, 1),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=ScriptedProvider(
            [_metrics(run_id, "recommendation"), _runtime(run_id, "recommendation"), diagnosis],
            invalid,
        ),
    )
    assert result.terminal is AgentRunTerminal.FAILED
    assert result.failure_code is AgentFailureCode.ACTION_SELECTION_BINDING_FAILURE
    assert result.action_proposal is None


def test_provider_turn_count_contract_rejects_forged_gaps() -> None:
    run_id = "a" * 32
    diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=run_id,
        terminal=Terminal.ABSTAIN,
        root_service=None,
        root_entity_ref=None,
        fault_domain=None,
        mechanism=None,
        confidence=0.1,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
        evidence_source_types=(),
        uncertainties=(),
        summary="The bounded observations show no confirmed incident.",
    )
    result = run_tool_using_agent(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=ScriptedProvider([diagnosis], None),
    )
    forged = result.model_dump(mode="python")
    forged["provider_turn_count"] = 6
    forged["result_sha256"] = semantic_sha256(
        {key: value for key, value in result.model_dump(mode="json").items() if key != "result_sha256"}
        | {"provider_turn_count": 6}
    )
    with pytest.raises(ValueError, match="turn count"):
        type(result).model_validate(forged)

    failed = run_tool_using_agent(
        context=_context(run_id, 0),
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=ScriptedProvider([ConnectionError("transport")], None),
    )
    assert failed.provider_turn_count == len(failed.provider_turns) + 1
    forged_failure = failed.model_dump(mode="python")
    forged_failure["provider_turn_count"] = 2
    forged_failure["result_sha256"] = semantic_sha256(
        {key: value for key, value in failed.model_dump(mode="json").items() if key != "result_sha256"}
        | {"provider_turn_count": 2}
    )
    with pytest.raises(ValueError, match="turn count"):
        type(failed).model_validate(forged_failure)
