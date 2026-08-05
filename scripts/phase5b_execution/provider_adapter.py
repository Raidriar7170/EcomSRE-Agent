"""One-call frozen Phase 5A adapter for a scheduled Phase 5B run."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import time
from typing import Protocol, cast

from pydantic import ValidationError

from ecomsre.backends.replay import ReplayCase
from ecomsre.model.gateway import ProviderProtocolError
from ecomsre.phase5a.provider import Phase5AProviderCompletion
from ecomsre.phase5a.workflows import (
    DiagnosisVariantV2,
    DiagnosisWorkflowTraceV2,
    run_diagnosis_v2,
)

from scripts.phase5b_execution.contracts import (
    EvidenceClass,
    InvestigatedSource,
    ObservedDiagnosisRecord,
    ProviderUsageRecord,
    RawScoredRunRecord,
    ScoredRunRequest,
    TerminalStatus,
    seal_raw_record,
)
from scripts.phase5b_execution.worker import load_worker_instance


_MAX_COMPLETION_TOKENS = 2048


class Phase5BProviderBackend(Protocol):
    @property
    def calls(self) -> int: ...

    def request_bytes(
        self,
        *,
        envelope: Mapping[str, object],
        max_completion_tokens: int,
    ) -> int: ...

    def complete(
        self,
        *,
        envelope: Mapping[str, object],
        max_completion_tokens: int,
    ) -> Phase5AProviderCompletion: ...


def _remap_run_identity(value: object, inner_run_id: str, outer_run_id: str) -> object:
    if isinstance(value, dict):
        return {
            str(key): _remap_run_identity(item, inner_run_id, outer_run_id)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _remap_run_identity(item, inner_run_id, outer_run_id)
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            _remap_run_identity(item, inner_run_id, outer_run_id)
            for item in value
        ]
    if isinstance(value, str):
        return value.replace(inner_run_id, outer_run_id)
    return value


def _provider_envelope(
    *,
    trace: DiagnosisWorkflowTraceV2,
    replay_case: ReplayCase,
    outer_run_id: str,
) -> dict[str, object]:
    inner_run_id = trace.run_id
    records = trace.tool_call_records
    evidence = tuple(
        item
        for record in records
        for item in record.evidence
    )
    raw = {
        "schema_version": "phase5a.provider-input-envelope.v2",
        "run_id": outer_run_id,
        "variant": trace.variant.value,
        "incident": replay_case.incident.model_dump(mode="json"),
        "findings": [
            item.model_dump(mode="json")
            for item in trace.findings
        ],
        "source_observations": [
            item.model_dump(mode="json")
            for item in trace.source_observations
        ],
        "evidence": [item.model_dump(mode="json") for item in evidence],
        "response_schema": "phase5a.diagnosis-result.v2",
    }
    remapped = _remap_run_identity(raw, inner_run_id, outer_run_id)
    if not isinstance(remapped, dict):
        raise AssertionError("provider envelope must remain an object")
    return remapped


def _usage(
    *,
    workflow_model_calls: int,
    workflow_tool_calls: int,
    workflow_tokens: int,
    completion: Phase5AProviderCompletion | None,
    provider_call_made: bool,
    evidence_class: EvidenceClass,
) -> ProviderUsageRecord:
    provider_tokens = completion.total_tokens if completion is not None else 0
    model_calls = workflow_model_calls + (1 if provider_call_made else 0)
    output_tokens = completion.output_tokens if completion is not None else 0
    combined_tokens = workflow_tokens + provider_tokens
    return ProviderUsageRecord(
        model_calls=model_calls,
        tool_calls=workflow_tool_calls,
        input_tokens=completion.input_tokens if completion is not None else 0,
        output_tokens=output_tokens,
        total_tokens=provider_tokens,
        workflow_tokens=workflow_tokens,
        combined_tokens=combined_tokens,
        provider_network_calls=(
            1
            if provider_call_made and evidence_class != "MOCK_EXECUTION_REHEARSAL"
            else 0
        ),
        provider_usage_known=completion is not None,
        within_budget=(
            model_calls <= 8
            and workflow_tool_calls <= 8
            and output_tokens <= _MAX_COMPLETION_TOKENS
            and combined_tokens <= 32_000
        ),
    )


def _failure_record(
    *,
    request: ScoredRunRequest,
    status: TerminalStatus,
    code: str,
    stage: str,
    usage: ProviderUsageRecord,
    evidence_class: EvidenceClass,
    latency_ms: int,
    investigated_sources: tuple[InvestigatedSource, ...] = (),
    targeted_refinement_used: bool = False,
) -> RawScoredRunRecord:
    return seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=status,
        observed_diagnosis=None,
        investigated_sources=investigated_sources,
        targeted_refinement_used=targeted_refinement_used,
        usage=usage,
        evidence_class=evidence_class,
        provider_attempted=(
            evidence_class != "MOCK_EXECUTION_REHEARSAL"
            and usage.provider_network_calls == 1
        ),
        latency_ms=latency_ms,
        failure_code=code,
        failure_stage=stage,
    )


def execute_scored_run(
    *,
    project_root: Path,
    request: ScoredRunRequest,
    backend: Phase5BProviderBackend,
    environment: Mapping[str, str],
    materialized_root: Path,
    evidence_class: EvidenceClass,
) -> RawScoredRunRecord:
    started_ns = time.monotonic_ns()
    try:
        replay_case = load_worker_instance(
            project_root=project_root,
            request=request,
            environment=environment,
            materialized_root=materialized_root,
        )
        trace = run_diagnosis_v2(
            project_root=project_root,
            replay_case=replay_case,
            variant=DiagnosisVariantV2(request.variant),
        )
    except Exception:
        return _failure_record(
            request=request,
            status=TerminalStatus.WORKFLOW_FAILURE,
            code="WORKFLOW_FAILURE",
            stage="OFFLINE_WORKFLOW",
            usage=ProviderUsageRecord(
                model_calls=0,
                tool_calls=0,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                workflow_tokens=0,
                combined_tokens=0,
                provider_network_calls=0,
                provider_usage_known=False,
            ),
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
        )
    snapshot = trace.final_budget_snapshot
    investigated_sources = cast(
        tuple[InvestigatedSource, ...],
        tuple(source.value for source in trace.investigated_sources),
    )
    targeted_refinement_used = trace.targeted_refinement_used
    workflow_model_calls = snapshot.charged_model_calls
    workflow_tool_calls = snapshot.charged_tool_calls
    workflow_tokens = snapshot.cumulative_tokens
    base_usage = _usage(
        workflow_model_calls=workflow_model_calls,
        workflow_tool_calls=workflow_tool_calls,
        workflow_tokens=workflow_tokens,
        completion=None,
        provider_call_made=False,
        evidence_class=evidence_class,
    )
    if trace.status != "COMPLETED":
        return _failure_record(
            request=request,
            status=TerminalStatus.WORKFLOW_FAILURE,
            code="WORKFLOW_FAILURE",
            stage="OFFLINE_WORKFLOW",
            usage=base_usage,
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    envelope = _provider_envelope(
        trace=trace,
        replay_case=replay_case,
        outer_run_id=request.run_id,
    )
    try:
        reserved_provider_tokens = backend.request_bytes(
            envelope=envelope,
            max_completion_tokens=_MAX_COMPLETION_TOKENS,
        ) + _MAX_COMPLETION_TOKENS
    except Exception:
        return _failure_record(
            request=request,
            status=TerminalStatus.PROVIDER_PROTOCOL_FAILURE,
            code="PROVIDER_REQUEST_INVALID",
            stage="RESPONSE_PROTOCOL",
            usage=base_usage,
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    if (
        workflow_model_calls + 1 > 8
        or workflow_tool_calls > 8
        or workflow_tokens + reserved_provider_tokens > 32_000
    ):
        return _failure_record(
            request=request,
            status=TerminalStatus.BUDGET_FAILURE,
            code="OUTER_BUDGET_ADMISSION_REJECTED",
            stage="BUDGET_ADMISSION",
            usage=base_usage,
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    try:
        completion = backend.complete(
            envelope=envelope,
            max_completion_tokens=_MAX_COMPLETION_TOKENS,
        )
    except (ConnectionError, TimeoutError):
        return _failure_record(
            request=request,
            status=TerminalStatus.PROVIDER_TRANSPORT_FAILURE,
            code="PROVIDER_TRANSPORT_FAILURE",
            stage="HTTP_TRANSPORT",
            usage=_usage(
                workflow_model_calls=workflow_model_calls,
                workflow_tool_calls=workflow_tool_calls,
                workflow_tokens=workflow_tokens,
                completion=None,
                provider_call_made=True,
                evidence_class=evidence_class,
            ),
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    except ProviderProtocolError:
        return _failure_record(
            request=request,
            status=TerminalStatus.PROVIDER_PROTOCOL_FAILURE,
            code="PROVIDER_PROTOCOL_FAILURE",
            stage="RESPONSE_PROTOCOL",
            usage=_usage(
                workflow_model_calls=workflow_model_calls,
                workflow_tool_calls=workflow_tool_calls,
                workflow_tokens=workflow_tokens,
                completion=None,
                provider_call_made=True,
                evidence_class=evidence_class,
            ),
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    except Exception:
        return _failure_record(
            request=request,
            status=TerminalStatus.INVALID_SCHEMA,
            code="INVALID_SCHEMA",
            stage="DIAGNOSIS_VALIDATION",
            usage=_usage(
                workflow_model_calls=workflow_model_calls,
                workflow_tool_calls=workflow_tool_calls,
                workflow_tokens=workflow_tokens,
                completion=None,
                provider_call_made=True,
                evidence_class=evidence_class,
            ),
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    usage = _usage(
        workflow_model_calls=workflow_model_calls,
        workflow_tool_calls=workflow_tool_calls,
        workflow_tokens=workflow_tokens,
        completion=completion,
        provider_call_made=True,
        evidence_class=evidence_class,
    )
    if completion.result.run_id != request.run_id:
        return _failure_record(
            request=request,
            status=TerminalStatus.SEMANTIC_FAILURE,
            code="RUN_ID_MISMATCH",
            stage="DIAGNOSIS_VALIDATION",
            usage=usage,
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    if usage.combined_tokens > 32_000:
        return _failure_record(
            request=request,
            status=TerminalStatus.BUDGET_FAILURE,
            code="OUTER_BUDGET_USAGE_EXCEEDED",
            stage="BUDGET_RECONCILIATION",
            usage=usage,
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    evidence_refs = {
        cast(str, item["evidence_ref"])
        for item in cast(list[dict[str, object]], envelope["evidence"])
    }
    cited = {
        *completion.result.supporting_evidence,
        *completion.result.contradicting_evidence,
    }
    if not cited <= evidence_refs:
        return _failure_record(
            request=request,
            status=TerminalStatus.UNRESOLVED_EVIDENCE_REFERENCE,
            code="UNRESOLVED_EVIDENCE_REFERENCE",
            stage="EVIDENCE_VALIDATION",
            usage=usage,
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    payload = completion.result.model_dump(mode="json")
    try:
        observed = ObservedDiagnosisRecord(
            run_id=request.run_id,
            decision=payload["decision"],
            root_service=payload["root_service"],
            fault_mechanism=payload["fault_mechanism"],
            causal_chain=tuple(payload["causal_chain"]),
            affected_sli=payload["affected_sli"],
            supporting_evidence=tuple(payload["supporting_evidence"]),
            contradicting_evidence=tuple(payload["contradicting_evidence"]),
            missing_evidence=tuple(payload["missing_evidence"]),
            confidence=payload["confidence"],
            decision_rationale=payload["decision_rationale"],
            recommended_next_action=payload["recommended_next_action"],
        )
    except (ValidationError, ValueError, TypeError):
        return _failure_record(
            request=request,
            status=TerminalStatus.SEMANTIC_FAILURE,
            code="SEMANTIC_FAILURE",
            stage="DIAGNOSIS_VALIDATION",
            usage=usage,
            evidence_class=evidence_class,
            latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
            investigated_sources=investigated_sources,
            targeted_refinement_used=targeted_refinement_used,
        )
    return seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=TerminalStatus.COMPLETED,
        observed_diagnosis=observed,
        investigated_sources=investigated_sources,
        targeted_refinement_used=targeted_refinement_used,
        usage=usage,
        evidence_class=evidence_class,
        provider_attempted=evidence_class != "MOCK_EXECUTION_REHEARSAL",
        latency_ms=max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
        failure_code=None,
        failure_stage=None,
    )
