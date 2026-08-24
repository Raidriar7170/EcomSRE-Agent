"""Diagnosis-only v2-style Flat Adaptive arm for one opaque real capture."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal, Protocol

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.agent_contracts import (
    AgentVisibleObservation,
    build_agent_visible_observation,
)
from ecomsre.dta_v2.read_tools import InvestigationReadTools
from ecomsre.dta_v2.tool_contracts import (
    InspectResourceUsageRequest,
    ObservationStatus,
    QueryMetricsRequest,
    ReadToolRequest,
    ResourceUsageRecord,
    SearchLogsRequest,
    ToolName,
    TraceNeighborhoodRequest,
    revalidate_read_tool_request,
)
from ecomsre.dta_v2.v21.agent_contracts import AlertContextV21
from ecomsre.dta_v2.v21.agent_provider import ProviderTurnV21
from ecomsre.dta_v2.v21.context_projection import build_prior_request_history_v21
from ecomsre.dta_v2.v21.contracts import (
    DtaDiagnosisV21,
    DtaModelV21,
    FaultDomainV21,
    FaultMechanismV21,
    TerminalV21,
)
from ecomsre.dta_v2.v22.real_capture_backend_v225 import (
    RealCaptureSnapshotBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultBootstrapV1,
    RealFaultOpaqueCaptureV1,
    build_common_bootstrap_v225,
    require_provider_payload_opaque_v225,
)
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v225 import (
    RealFaultArmRun,
    RealFaultArmStatus,
    RealFaultShadowPrediction,
    RealFaultStudyArm,
    build_real_fault_arm_run_v225,
)


REAL_FAULT_FLAT_SYSTEM_PROMPT_V225 = """You are the v2-style Flat Adaptive diagnosis-only arm using the v2.1 CPU-capable ontology. Choose whether to read, which read-only tool and opaque target set to inspect, or submit one supported Diagnosis. Candidate identifiers are opaque. The generic task is: investigate whether one candidate currently has an operational fault and gather only the evidence needed for a supported terminal. Never propose or execute an action, Runbook, command, remediation, or write."""


class FlatComparisonProviderV225(Protocol):
    @property
    def attempted_calls(self) -> int: ...

    @property
    def transport_retry_count(self) -> int: ...

    def investigation_turn(
        self,
        *,
        context: AlertContextV21,
        visible_state: object,
        read_tools_enabled: bool,
    ) -> ProviderTurnV21: ...


class RealFaultFlatStateV225(DtaModelV21):
    schema_version: Literal["dta-v225-real-fault.flat-state.v1"]
    alert_context: AlertContextV21
    common_bootstrap: RealFaultBootstrapV1
    bootstrap_evidence_refs: tuple[str, ...]
    adaptive_observations: tuple[AgentVisibleObservation, ...] = Field(max_length=4)
    prior_requests: tuple[ReadToolRequest, ...] = Field(max_length=4)
    remaining_semantic_actions: StrictInt = Field(ge=0, le=4)
    remaining_target_equivalent_reads: StrictInt = Field(ge=0, le=4)

    @model_validator(mode="after")
    def require_state(self) -> RealFaultFlatStateV225:
        if len(self.adaptive_observations) != len(self.prior_requests):
            raise ValueError("Flat adaptive observation and request history differ")
        if self.remaining_semantic_actions != 4 - len(self.prior_requests):
            raise ValueError("Flat semantic action budget differs")
        used_targets = sum(len(_targets(item)) for item in self.prior_requests)
        if self.remaining_target_equivalent_reads != 4 - used_targets:
            raise ValueError("Flat target-equivalent read budget differs")
        return self


def _targets(request: ReadToolRequest) -> tuple[str, ...]:
    if isinstance(
        request,
        (QueryMetricsRequest, SearchLogsRequest, TraceNeighborhoodRequest),
    ):
        return (request.service,)
    return request.services


def _run_id(capture: RealFaultOpaqueCaptureV1) -> str:
    return capture.opaque_capture_sha256[:32]


def _context(capture: RealFaultOpaqueCaptureV1) -> AlertContextV21:
    ended_at = capture.capture.captured_at
    return AlertContextV21(
        schema_version="dta-v21.alert-context.v1",
        run_id=_run_id(capture),
        scenario_id=capture.case_id,
        alert_summary=(
            "Investigate whether one of the candidate services has a current operational "
            "fault. Gather only the evidence needed for a supported terminal."
        ),
        candidate_services=capture.candidate_aliases,
        allowed_read_tools=tuple(ToolName),
        maximum_read_tool_dispatches=4,
        maximum_repeated_identical_calls=0,
        maximum_provider_investigation_turns=5,
        maximum_action_selection_turns=1,
        started_at=ended_at.replace(microsecond=0) - timedelta(minutes=5),
        ended_at=ended_at,
    )


def _bootstrap_refs(run_id: str) -> tuple[str, ...]:
    return (
        f"evidence://{run_id}/metrics/0000",
        f"evidence://{run_id}/runtime/0000",
    )


def _resource_observations(state: RealFaultFlatStateV225) -> tuple[AgentVisibleObservation, ...]:
    return tuple(
        item
        for item in state.adaptive_observations
        if item.tool is ToolName.INSPECT_RESOURCE_USAGE
        and item.status is ObservationStatus.SUCCESS
    )


def _evidence_clause_valid(
    *,
    diagnosis: DtaDiagnosisV21,
    state: RealFaultFlatStateV225,
    baseline_capture: RealFaultOpaqueCaptureV1,
) -> bool:
    visible_refs = set(state.bootstrap_evidence_refs) | {
        item.evidence_ref for item in state.adaptive_observations
    }
    cited = set(diagnosis.supporting_evidence_refs)
    if not cited or not cited.issubset(visible_refs):
        return False
    resources = _resource_observations(state)
    if diagnosis.terminal is not TerminalV21.COMPLETED:
        return False
    if diagnosis.root_service is None:
        covered = {
            record.logical_service
            for observation in resources
            if observation.evidence_ref in cited
            for record in observation.results
            if isinstance(record, ResourceUsageRecord)
            and max(sample.cpu_percent for sample in record.samples) < 80.0
            and record.memory_slope_bytes_per_second < 1.0
        }
        return covered == set(state.alert_context.candidate_services) and all(
            item.healthy for item in state.common_bootstrap.runtime
        )
    if (
        diagnosis.fault_domain is not FaultDomainV21.LOCAL_RESOURCE
        or diagnosis.mechanism is not FaultMechanismV21.CPU_SATURATION
    ):
        return False
    baseline = next(
        item
        for item in baseline_capture.capture.resources
        if item.service == diagnosis.root_service
    )
    baseline_p95 = max(item.cpu_percent for item in baseline.samples)
    return any(
        observation.evidence_ref in cited
        and any(
            isinstance(record, ResourceUsageRecord)
            and record.logical_service == diagnosis.root_service
            and max(sample.cpu_percent for sample in record.samples) >= 80.0
            and max(sample.cpu_percent for sample in record.samples)
            >= max(1.0, baseline_p95) * 2.0
            for record in observation.results
        )
        for observation in resources
    )


def _prediction(
    *,
    diagnosis: DtaDiagnosisV21,
    state: RealFaultFlatStateV225,
    baseline_capture: RealFaultOpaqueCaptureV1,
) -> RealFaultShadowPrediction:
    terminal: Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN", "FAILED"]
    if diagnosis.terminal is TerminalV21.COMPLETED and diagnosis.root_service is not None:
        terminal = "DIAGNOSED"
    elif diagnosis.terminal is TerminalV21.COMPLETED:
        terminal = "NO_INCIDENT"
    else:
        terminal = "ABSTAIN"
    return RealFaultShadowPrediction(
        schema_version="dta-v225-real-fault.shadow-prediction.v1",
        terminal=terminal,
        root_service_alias=diagnosis.root_service,
        fault_domain=(
            None if diagnosis.fault_domain is None else diagnosis.fault_domain.value
        ),
        mechanism=None if diagnosis.mechanism is None else diagnosis.mechanism.value,
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        evidence_clause_valid=_evidence_clause_valid(
            diagnosis=diagnosis,
            state=state,
            baseline_capture=baseline_capture,
        ),
    )


def _failed_prediction() -> RealFaultShadowPrediction:
    return RealFaultShadowPrediction(
        schema_version="dta-v225-real-fault.shadow-prediction.v1",
        terminal="FAILED",
        root_service_alias=None,
        fault_domain=None,
        mechanism=None,
        supporting_evidence_refs=(),
        evidence_clause_valid=False,
    )


def run_v2_style_flat_adaptive_v225(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    model_id: str,
    provider: FlatComparisonProviderV225,
) -> RealFaultArmRun:
    """Run free read-tool/target selection and stop before all action semantics."""

    if capture.alias_map_name != baseline_capture.alias_map_name:
        raise ValueError("Flat case and baseline alias maps differ")
    context = _context(capture)
    bootstrap = build_common_bootstrap_v225(capture)
    backend = RealCaptureSnapshotBackendV225(run_id=context.run_id, capture=capture)
    tools = InvestigationReadTools(run_id=context.run_id, backend=backend)
    turns: list[ProviderTurnV21] = []
    requests: list[ReadToolRequest] = []
    target_reads = 0
    diagnosis: DtaDiagnosisV21 | None = None
    state: RealFaultFlatStateV225 | None = None
    status = RealFaultArmStatus.VALID_TERMINAL

    try:
        while diagnosis is None and len(turns) < 5:
            snapshot = tools.snapshot()
            state = RealFaultFlatStateV225(
                schema_version="dta-v225-real-fault.flat-state.v1",
                alert_context=context,
                common_bootstrap=bootstrap,
                bootstrap_evidence_refs=_bootstrap_refs(context.run_id),
                adaptive_observations=tuple(
                    build_agent_visible_observation(item)
                    for item in snapshot.observations
                ),
                prior_requests=build_prior_request_history_v21(snapshot),
                remaining_semantic_actions=4 - len(requests),
                remaining_target_equivalent_reads=4 - target_reads,
            )
            require_provider_payload_opaque_v225(
                {
                    "system_prompt": REAL_FAULT_FLAT_SYSTEM_PROMPT_V225,
                    "visible_state": state.model_dump(mode="json"),
                }
            )
            turn = provider.investigation_turn(
                context=context,
                visible_state=state,
                read_tools_enabled=len(requests) < 4 and target_reads < 4,
            )
            turns.append(turn)
            if turn.action_selection is not None or turn.plan_decision is not None:
                raise ValueError("Flat comparison Provider entered a forbidden stage")
            if turn.diagnosis is not None:
                diagnosis = DtaDiagnosisV21.model_validate(
                    turn.diagnosis.model_dump(mode="python")
                )
                if diagnosis.run_id != context.run_id:
                    raise ValueError("Flat Diagnosis belongs to another run")
                if diagnosis.root_service is not None and diagnosis.root_service not in set(
                    context.candidate_services
                ):
                    raise ValueError("Flat Diagnosis root is outside opaque candidates")
                break
            if turn.read_request is None:
                raise ValueError("Flat comparison turn lacks a read or Diagnosis")
            request = revalidate_read_tool_request(turn.read_request)
            request_targets = _targets(request)
            if (
                request.run_id != context.run_id
                or request.tool not in context.allowed_read_tools
                or not set(request_targets).issubset(context.candidate_services)
                or len(requests) >= 4
                or target_reads + len(request_targets) > 4
            ):
                raise ValueError("Flat read exceeds the comparison budget or scope")
            tools.dispatch(request)
            requests.append(request)
            target_reads += len(request_targets)
        if diagnosis is None or state is None:
            raise ValueError("Flat comparison exhausted the Provider-turn budget")
        snapshot = tools.snapshot()
        state = RealFaultFlatStateV225(
            schema_version="dta-v225-real-fault.flat-state.v1",
            alert_context=context,
            common_bootstrap=bootstrap,
            bootstrap_evidence_refs=_bootstrap_refs(context.run_id),
            adaptive_observations=tuple(
                build_agent_visible_observation(item) for item in snapshot.observations
            ),
            prior_requests=build_prior_request_history_v21(snapshot),
            remaining_semantic_actions=4 - len(requests),
            remaining_target_equivalent_reads=4 - target_reads,
        )
        prediction = _prediction(
            diagnosis=diagnosis,
            state=state,
            baseline_capture=baseline_capture,
        )
    except (ConnectionError, TimeoutError):
        status = RealFaultArmStatus.TRANSPORT_FAILED
        prediction = _failed_prediction()
    except (TypeError, ValueError):
        status = RealFaultArmStatus.PROTOCOL_FAILED
        prediction = _failed_prediction()

    input_tokens = sum(item.usage.input_tokens for item in turns)
    output_tokens = sum(item.usage.output_tokens for item in turns)
    resource_requests = tuple(
        item for item in requests if isinstance(item, InspectResourceUsageRequest)
    )
    covered = {
        target for item in resource_requests for target in item.services
    }
    observations = tools.snapshot().observations
    resource_observations = tuple(
        item for item in observations if item.tool is ToolName.INSPECT_RESOURCE_USAGE
    )
    predicate_yield = sum(
        any(
            isinstance(record, ResourceUsageRecord)
            and max(sample.cpu_percent for sample in record.samples) >= 80.0
            for record in item.results
        )
        for item in resource_observations
    )
    return build_real_fault_arm_run_v225(
        case_id=capture.case_id,
        arm=RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE,
        case_bytes_sha256=capture.opaque_capture_sha256,
        model_id=model_id,
        status=status,
        prediction=prediction,
        first_useful_evidence_ordinal=(1 if resource_observations else None),
        resources_requested=bool(resource_requests),
        resource_read_shape=(
            "NONE"
            if not resource_requests
            else "MULTI_TARGET"
            if any(len(item.services) > 1 for item in resource_requests)
            else "SINGLE_TARGET"
        ),
        all_candidates_covered=covered == set(capture.candidate_aliases),
        semantic_evidence_actions=len(requests),
        target_equivalent_reads=target_reads,
        provider_turns=len(turns),
        provider_calls=provider.attempted_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        latency_ms=float(sum(item.monotonic_latency_ms for item in turns)),
        protocol_failures=int(status is RealFaultArmStatus.PROTOCOL_FAILED),
        transport_retries=provider.transport_retry_count,
        duplicate_read_attempts=backend.duplicate_request_count,
        empty_read_count=sum(
            item.status is ObservationStatus.SUCCESS and item.result_count == 0
            for item in observations
        ),
        predicate_yield_count=predicate_yield,
        bundle_resources_reads=0,
    )


__all__ = (
    "FlatComparisonProviderV225",
    "REAL_FAULT_FLAT_SYSTEM_PROMPT_V225",
    "RealFaultFlatStateV225",
    "run_v2_style_flat_adaptive_v225",
)
