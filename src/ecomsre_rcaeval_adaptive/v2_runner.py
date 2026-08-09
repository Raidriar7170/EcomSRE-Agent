"""Bounded execution for Single-first Adaptive v2."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import monotonic, sleep
from typing import Literal, Protocol, TypeVar

from pydantic import AwareDatetime, Field, StrictFloat, StrictInt, model_validator

from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    StdlibOpenAICompatibleTransport,
)
from ecomsre_rcaeval.adapter import (
    ArchitectureContext,
    ArchitectureContextBuilder,
    IncidentManifest,
    incident_for_case,
)
from ecomsre_rcaeval.contracts import Architecture, Diagnosis
from ecomsre_rcaeval.dataset import DevCase, TelemetryCase
from ecomsre_rcaeval_adaptive.contracts import (
    AdaptiveTerminalStatus,
    InitialDiagnosis,
    RankedHypothesis,
    RankedHypothesisBatch,
    V2Model,
)
from ecomsre_rcaeval_adaptive.runner import (
    _bounded_evidence,
    _service_ranking,
    _specialist_input,
)
from ecomsre_rcaeval_adaptive.specialists import OpenAICompatibleAdaptiveProvider
from ecomsre_rcaeval_adaptive.v2 import (
    AdaptiveV2Route,
    DeterministicFusionDecision,
    DeterministicFusionPolicy,
    StrongSingleIndicatorPolicy,
    StrongSingleIndicatorResolution,
    V2GateDecision,
    V2GateInputs,
    V2GatePolicy,
    decide_v2_gate,
    deterministic_fusion,
    expected_semantic_operations,
    resolve_strong_single_indicator,
)
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.dev3_execution import new_v1_reference_provider
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev3ProviderProxy,
    Dev3RetryingTransport,
    FailureClass,
    SemanticOperationRecord,
    seal_interrupted_provider_sidecar,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptAccountingSummary,
    AttemptBudget,
    rebuild_attempt_accounting,
)
from ecomsre_rcaeval_v2.indicator import FormulaId, LoadedIndicatorConfig
from ecomsre_rcaeval_v2.indicator_evaluation import build_runtime_metric_candidates
from ecomsre_rcaeval_v2.schedule import CaseIdentity, case_identity_bytes


class AdaptiveV2OperationTrace(V2Model):
    semantic_operation_index: StrictInt = Field(ge=1, le=3)
    role: Literal["INITIAL_DIAGNOSIS", "LOGS_VERIFIER", "TRACE_VERIFIER"]
    source: Literal["logs", "traces"] | None
    provider_call_index: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def require_role_source(self) -> AdaptiveV2OperationTrace:
        expected = {
            "INITIAL_DIAGNOSIS": None,
            "LOGS_VERIFIER": "logs",
            "TRACE_VERIFIER": "traces",
        }
        if self.source != expected[self.role]:
            raise ValueError("Adaptive v2 operation role differs from source")
        return self


class AdaptiveV2Diagnosis(V2Model):
    initial_diagnosis: Diagnosis
    gate_decision: V2GateDecision
    specialist_hypotheses: tuple[RankedHypothesis, ...] = Field(max_length=6)
    fusion_decision: DeterministicFusionDecision
    indicator_resolution: StrongSingleIndicatorResolution
    final_root_service: str
    final_indicator: Literal["cpu", "mem", "diskio", "latency", "socket"]

    @model_validator(mode="after")
    def require_final_consistency(self) -> AdaptiveV2Diagnosis:
        if self.final_root_service != self.fusion_decision.final_root_service:
            raise ValueError("Adaptive v2 final Root differs from deterministic Fusion")
        if self.final_indicator != self.indicator_resolution.final_indicator:
            raise ValueError("Adaptive v2 final Indicator differs from resolution")
        return self


class AdaptiveV2CaseResult(V2Model):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: Literal["RE2-OB", "RE2-SS"]
    diagnosis: AdaptiveV2Diagnosis
    operation_trace: tuple[AdaptiveV2OperationTrace, ...] = Field(
        min_length=1, max_length=3
    )
    tool_calls: Literal[3]
    semantic_operations: StrictInt = Field(ge=1, le=3)

    @model_validator(mode="after")
    def require_exact_cost(self) -> AdaptiveV2CaseResult:
        expected = expected_semantic_operations(self.diagnosis.gate_decision.route)
        if (
            self.semantic_operations != expected
            or len(self.operation_trace) != expected
        ):
            raise ValueError("Adaptive v2 semantic cost differs from route")
        if tuple(
            item.semantic_operation_index for item in self.operation_trace
        ) != tuple(range(1, expected + 1)):
            raise ValueError("Adaptive v2 operation trace is not contiguous")
        return self


class AdaptiveV2TerminalRecord(V2Model):
    schema_version: Literal["rcaeval-single-first-adaptive.terminal.v2"]
    evaluation_version: Literal["single-first-adaptive-v2"]
    candidate_id: str = Field(pattern=r"^candidate-[1-5]$")
    split: Literal["TUNE_SET", "REGRESSION_SET"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: Literal["RE2-OB", "RE2-SS"]
    status: AdaptiveTerminalStatus
    result: AdaptiveV2CaseResult | None
    failure_class: str | None = Field(default=None, max_length=128)
    failure_code: str | None = Field(default=None, max_length=128)
    failure_stage: str | None = Field(default=None, max_length=64)
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime
    latency_ms: StrictFloat = Field(ge=0.0)
    attempt_accounting: AttemptAccountingSummary
    policy_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_terminal_consistency(self) -> AdaptiveV2TerminalRecord:
        completed = self.status is AdaptiveTerminalStatus.COMPLETED
        if completed != (self.result is not None):
            raise ValueError("Adaptive v2 terminal completion differs from result")
        if completed and any(
            item is not None
            for item in (self.failure_class, self.failure_code, self.failure_stage)
        ):
            raise ValueError("completed Adaptive v2 terminal has failure fields")
        if not completed and self.failure_code is None:
            raise ValueError("failed Adaptive v2 terminal lacks failure code")
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("Adaptive v2 terminal ended before it started")
        return self


class V2DiagnosisProvider(Protocol):
    @property
    def calls(self) -> int: ...

    def diagnose(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        architecture: Architecture,
    ) -> Diagnosis: ...

    def specialize(
        self,
        specialist_input: object,
        *,
        before_output_validation: Callable[[], None] | None = None,
    ) -> RankedHypothesisBatch: ...


class StrongSingleSpecialistProvider:
    """One semantic surface: frozen Strong Single plus selective v1 verifiers."""

    def __init__(self, strong_single: object, specialists: object) -> None:
        self._strong_single = strong_single
        self._specialists = specialists

    @property
    def calls(self) -> int:
        strong = getattr(self._strong_single, "calls")
        specialist = getattr(self._specialists, "usage_snapshot")().call_count
        if type(strong) is not int or type(specialist) is not int:
            raise TypeError("Adaptive v2 Provider counters are invalid")
        return strong + specialist

    @property
    def last_usage_tokens(self) -> None:
        return None

    @property
    def last_safe_validation_error(self) -> object | None:
        return getattr(self._specialists, "last_safe_validation_error", None)

    def diagnose(self, *args: object, **kwargs: object) -> object:
        return getattr(self._strong_single, "diagnose")(*args, **kwargs)

    def specialize(self, *args: object, **kwargs: object) -> object:
        return getattr(self._specialists, "specialize")(*args, **kwargs)


ResultT = TypeVar("ResultT")


def _initial_for_specialist(initial: Diagnosis) -> InitialDiagnosis:
    return InitialDiagnosis(
        root_cause_service=initial.root_cause_service,
        model_proposed_indicator=initial.root_cause_indicator,
        confidence=0.0 if initial.confidence is None else initial.confidence,
        evidence_refs=initial.evidence_refs,
        explanation=initial.explanation,
        uncertainty_flags=(),
    )


def execute_v2_case(
    case: TelemetryCase,
    *,
    run_id: str,
    identity_sha256: str,
    provider: V2DiagnosisProvider,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    gate_policy: V2GatePolicy,
    fusion_policy: DeterministicFusionPolicy,
    indicator_policy: StrongSingleIndicatorPolicy,
) -> AdaptiveV2CaseResult:
    """Execute one case; deterministic Gate/Fusion never call the Provider."""

    builder = ArchitectureContextBuilder(case, Architecture.SINGLE, run_id=run_id)
    for source in ("metrics", "logs", "traces"):
        builder.query_source(source)  # type: ignore[arg-type]
    context = builder.snapshot()
    incident = incident_for_case(case)
    candidates = build_runtime_metric_candidates(
        case,
        case_identity_sha256=identity_sha256,
        formula=indicator_formula,
        config=indicator_config,
    )
    ranking = _service_ranking(candidates)
    trace: list[AdaptiveV2OperationTrace] = []

    def invoke(
        role: Literal["INITIAL_DIAGNOSIS", "LOGS_VERIFIER", "TRACE_VERIFIER"],
        source: Literal["logs", "traces"] | None,
        operation: Callable[[], ResultT],
    ) -> ResultT:
        before = provider.calls
        result = operation()
        after = provider.calls
        if after != before + 1:
            raise ValueError(
                "Adaptive v2 semantic operation made an invalid call count"
            )
        trace.append(
            AdaptiveV2OperationTrace(
                semantic_operation_index=len(trace) + 1,
                role=role,
                source=source,
                provider_call_index=after,
            )
        )
        return result

    initial = invoke(
        "INITIAL_DIAGNOSIS",
        None,
        lambda: provider.diagnose(incident, context, Architecture.SINGLE),
    )
    bounded = _bounded_evidence(context)
    service_by_ref = {item.evidence_ref: item.service for item in bounded}
    log_services = tuple(
        dict.fromkeys(
            item.service
            for item in bounded
            if item.source == "logs" and item.service != "unknown"
        )
    )
    trace_services = tuple(
        dict.fromkeys(
            item.service
            for item in bounded
            if item.source == "traces" and item.service != "unknown"
        )
    )
    metrics_top = ranking[0][0]
    logs_oppose = (
        initial.root_cause_service not in log_services[:2]
        and metrics_top in log_services[:2]
    )
    propagation_conflict = (
        bool(trace_services)
        and initial.root_cause_service not in trace_services[:2]
        and metrics_top in trace_services[:2]
    )
    gate = decide_v2_gate(
        V2GateInputs(
            initial_diagnosis=initial,
            metrics_service_ranking=ranking,
            diagnosis_evidence_supports_service=any(
                service_by_ref.get(reference) == initial.root_cause_service
                for reference in initial.evidence_refs
            ),
            logs_explicitly_oppose_initial=logs_oppose,
            propagation_conflict=propagation_conflict,
            trace_available=case.traces_path is not None,
            indicator_candidate_available=any(
                item.service == initial.root_cause_service for item in candidates
            ),
        ),
        gate_policy,
    )
    hypotheses: list[RankedHypothesis] = []
    specialist_initial = _initial_for_specialist(initial)
    if gate.route in {AdaptiveV2Route.VERIFY_LOGS, AdaptiveV2Route.VERIFY_BOTH}:
        specialist_input = _specialist_input(
            context, "logs", incident, specialist_initial
        )
        batch = invoke(
            "LOGS_VERIFIER",
            "logs",
            lambda: provider.specialize(
                specialist_input, before_output_validation=lambda: None
            ),
        )
        hypotheses.extend(batch.hypotheses)
    if gate.route in {AdaptiveV2Route.VERIFY_TRACES, AdaptiveV2Route.VERIFY_BOTH}:
        specialist_input = _specialist_input(
            context, "traces", incident, specialist_initial
        )
        batch = invoke(
            "TRACE_VERIFIER",
            "traces",
            lambda: provider.specialize(
                specialist_input, before_output_validation=lambda: None
            ),
        )
        hypotheses.extend(batch.hypotheses)
    fusion = deterministic_fusion(
        initial=initial,
        gate=gate,
        metrics_service_ranking=tuple(
            (service, float(score)) for service, score in ranking
        ),
        specialist_hypotheses=tuple(hypotheses),
        policy=fusion_policy,
    )
    indicator = resolve_strong_single_indicator(
        final_root_service=fusion.final_root_service,
        initial=initial,
        candidates=candidates,
        policy=indicator_policy,
    )
    diagnosis = AdaptiveV2Diagnosis(
        initial_diagnosis=initial,
        gate_decision=gate,
        specialist_hypotheses=tuple(hypotheses),
        fusion_decision=fusion,
        indicator_resolution=indicator,
        final_root_service=fusion.final_root_service,
        final_indicator=indicator.final_indicator,
    )
    return AdaptiveV2CaseResult(
        run_id=run_id,
        case_id=case.case_id,
        system=case.system,  # type: ignore[arg-type]
        diagnosis=diagnosis,
        operation_trace=tuple(trace),
        tool_calls=3,
        semantic_operations=len(trace),
    )


class RequestPacer:
    def __init__(self, minimum_interval_seconds: float) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("Provider pacing interval must be nonnegative")
        self.minimum_interval_seconds = float(minimum_interval_seconds)
        self._last_start: float | None = None

    def wait(self) -> None:
        now = monotonic()
        if self._last_start is not None:
            remaining = self.minimum_interval_seconds - (now - self._last_start)
            if remaining > 0:
                sleep(remaining)
        self._last_start = monotonic()


class PacedTransport:
    def __init__(
        self, delegate: OpenAICompatibleTransport, pacer: RequestPacer
    ) -> None:
        self._delegate = delegate
        self._pacer = pacer

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self._pacer.wait()
        return self._delegate.post_json(
            url=url,
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )


def adaptive_v2_run_id(candidate_id: str, split: str, identity: CaseIdentity) -> str:
    if candidate_id not in {
        "candidate-1",
        "candidate-2",
        "candidate-3",
        "candidate-4",
        "candidate-5",
    }:
        raise ValueError("Adaptive v2 candidate is outside the bounded search")
    if split not in {"TUNE_SET", "REGRESSION_SET"}:
        raise ValueError("Adaptive v2 split is invalid")
    return hashlib.sha256(
        b"\0".join(
            (
                b"single-first-adaptive-v2",
                candidate_id.encode(),
                split.encode(),
                case_identity_bytes(identity),
            )
        )
    ).hexdigest()[:32]


def _accounting(
    sidecar_root: Path, max_completion_tokens: int
) -> AttemptAccountingSummary:
    return rebuild_attempt_accounting(
        (sidecar_root,),
        prompt_token_reservation=29_952,
        max_completion_tokens=max_completion_tokens,
    )


def _last_failure(
    sidecar_root: Path,
) -> tuple[AdaptiveTerminalStatus, str | None, str, str | None]:
    paths = tuple(sorted((sidecar_root / "semantic-operations").glob("*.json")))
    if not paths:
        return (
            AdaptiveTerminalStatus.RUNTIME_CONTRACT_VIOLATION,
            FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE.value,
            "MISSING_SEMANTIC_FAILURE_RECORD",
            "INPUT_CONSTRUCTION",
        )
    record = SemanticOperationRecord.model_validate_json(
        paths[-1].read_text(encoding="utf-8")
    )
    statuses = {
        FailureClass.NON_RETRYABLE_SCHEMA: AdaptiveTerminalStatus.INVALID_SCHEMA,
        FailureClass.NON_RETRYABLE_PROTOCOL: AdaptiveTerminalStatus.PROTOCOL_VIOLATION,
        FailureClass.NON_RETRYABLE_LOCAL_CONTRACT: AdaptiveTerminalStatus.RUNTIME_CONTRACT_VIOLATION,
        FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT: AdaptiveTerminalStatus.PROVIDER_FAILURE,
        FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE: AdaptiveTerminalStatus.PROVIDER_FAILURE,
    }
    status = (
        AdaptiveTerminalStatus.PROVIDER_FAILURE
        if record.failure_class is None
        else statuses[record.failure_class]
    )
    return (
        status,
        None if record.failure_class is None else record.failure_class.value,
        record.failure_code or "UNKNOWN_ADAPTIVE_V2_FAILURE",
        record.failure_stage,
    )


def _terminal_bytes(record: AdaptiveV2TerminalRecord) -> bytes:
    return (
        json.dumps(
            record.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _write_create_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def execute_v2_scheduled_once(
    *,
    case: TelemetryCase,
    identity_sha256: str,
    run_id: str,
    candidate_id: str,
    split: Literal["TUNE_SET", "REGRESSION_SET"],
    provider: V2DiagnosisProvider,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    gate_policy: V2GatePolicy,
    fusion_policy: DeterministicFusionPolicy,
    indicator_policy: StrongSingleIndicatorPolicy,
    terminal_root: Path,
    sidecar_root: Path,
    policy_lock_sha256: str,
    max_completion_tokens: int,
) -> AdaptiveV2TerminalRecord:
    terminal_path = terminal_root / f"{run_id}.json"
    if terminal_path.exists():
        terminal = AdaptiveV2TerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        if terminal.run_id != run_id or terminal.case_id != case.case_id:
            raise ValueError("existing Adaptive v2 terminal identity differs")
        return terminal
    started_at = datetime.now(timezone.utc)
    monotonic_started = monotonic()
    result: AdaptiveV2CaseResult | None
    failure_class: str | None
    failure_code: str | None
    failure_stage: str | None
    if sidecar_root.exists() and any(
        path.is_file() for path in sidecar_root.rglob("*")
    ):
        seal_interrupted_provider_sidecar(
            sidecar_root,
            policy_lock_sha256=policy_lock_sha256,
            expected_timeout_seconds=30.0,
            fallback_operation_type="FINAL_JUDGE",
        )
        status = AdaptiveTerminalStatus.INTERRUPTED
        result = None
        failure_class = FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE.value
        failure_code = "INTERRUPTED_ADAPTIVE_V2_RUN"
        failure_stage = "PROVIDER_CALL"
    else:
        try:
            result = execute_v2_case(
                case,
                run_id=run_id,
                identity_sha256=identity_sha256,
                provider=provider,
                indicator_formula=indicator_formula,
                indicator_config=indicator_config,
                gate_policy=gate_policy,
                fusion_policy=fusion_policy,
                indicator_policy=indicator_policy,
            )
            status = AdaptiveTerminalStatus.COMPLETED
            failure_class = failure_code = failure_stage = None
        except Exception:
            result = None
            status, failure_class, failure_code, failure_stage = _last_failure(
                sidecar_root
            )
    terminal = AdaptiveV2TerminalRecord(
        schema_version="rcaeval-single-first-adaptive.terminal.v2",
        evaluation_version="single-first-adaptive-v2",
        candidate_id=candidate_id,
        split=split,
        run_id=run_id,
        case_id=case.case_id,
        system=case.system,  # type: ignore[arg-type]
        status=status,
        result=result,
        failure_class=failure_class,
        failure_code=failure_code,
        failure_stage=failure_stage,
        started_at_utc=started_at,
        ended_at_utc=datetime.now(timezone.utc),
        latency_ms=max(0.0, (monotonic() - monotonic_started) * 1_000),
        attempt_accounting=_accounting(sidecar_root, max_completion_tokens),
        policy_lock_sha256=policy_lock_sha256,
    )
    _write_create_once(terminal_path, _terminal_bytes(terminal))
    return terminal


def execute_v2_batch(
    identities: tuple[CaseIdentity, ...],
    *,
    cases: Mapping[CaseIdentity, DevCase],
    candidate_id: str,
    split: Literal["TUNE_SET", "REGRESSION_SET"],
    provider_config: OpenAICompatibleConfig,
    model: str,
    timeout_seconds: float,
    max_completion_tokens: int,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    gate_policy: V2GatePolicy,
    fusion_policy: DeterministicFusionPolicy,
    indicator_policy: StrongSingleIndicatorPolicy,
    run_root: Path,
    policy_lock_sha256: str,
    minimum_interval_seconds: float,
    progress: Callable[[int, int, AdaptiveV2TerminalRecord], None] | None = None,
) -> tuple[AdaptiveV2TerminalRecord, ...]:
    if len(set(identities)) != len(identities):
        raise ValueError("Adaptive v2 identities must be unique")
    run_ids = tuple(
        adaptive_v2_run_id(candidate_id, split, item) for item in identities
    )
    sidecars = tuple(run_root / "provider-sidecars" / item for item in run_ids)
    max_operations = len(identities) * 3
    budget = AttemptBudget.restore(
        sidecars,
        max_provider_attempts=max_operations * 2,
        max_retry_attempts=max_operations,
        prompt_token_reservation=29_952,
        max_completion_tokens=max_completion_tokens,
        max_conservative_tokens=max_operations * (29_952 + max_completion_tokens) * 2,
    )
    pacer = RequestPacer(minimum_interval_seconds)
    output: list[AdaptiveV2TerminalRecord] = []
    for index, (identity, run_id, sidecar) in enumerate(
        zip(identities, run_ids, sidecars, strict=True), start=1
    ):
        case = cases[identity]
        transport = Dev3RetryingTransport(
            PacedTransport(StdlibOpenAICompatibleTransport(), pacer),
            run_root=sidecar,
            budget=budget,
            policy_lock_sha256=policy_lock_sha256,
            expected_timeout_seconds=timeout_seconds,
        )
        composite = StrongSingleSpecialistProvider(
            new_v1_reference_provider(provider_config, transport=transport),
            OpenAICompatibleAdaptiveProvider(
                config=provider_config,
                expected_model=model,
                timeout_seconds=timeout_seconds,
                max_completion_tokens=max_completion_tokens,
                transport=transport,
            ),
        )
        provider = Dev3ProviderProxy(
            composite, run_root=sidecar, policy_lock_sha256=policy_lock_sha256
        )
        terminal = execute_v2_scheduled_once(
            case=dev_case_to_telemetry_case(case),
            identity_sha256=hashlib.sha256(case_identity_bytes(identity)).hexdigest(),
            run_id=run_id,
            candidate_id=candidate_id,
            split=split,
            provider=provider,  # type: ignore[arg-type]
            indicator_formula=indicator_formula,
            indicator_config=indicator_config,
            gate_policy=gate_policy,
            fusion_policy=fusion_policy,
            indicator_policy=indicator_policy,
            terminal_root=run_root / "terminal-records",
            sidecar_root=sidecar,
            policy_lock_sha256=policy_lock_sha256,
            max_completion_tokens=max_completion_tokens,
        )
        output.append(terminal)
        if progress is not None:
            progress(index, len(identities), terminal)
    return tuple(output)


__all__ = [
    "AdaptiveV2CaseResult",
    "AdaptiveV2Diagnosis",
    "AdaptiveV2OperationTrace",
    "AdaptiveV2TerminalRecord",
    "PacedTransport",
    "RequestPacer",
    "StrongSingleSpecialistProvider",
    "adaptive_v2_run_id",
    "execute_v2_batch",
    "execute_v2_case",
    "execute_v2_scheduled_once",
]
