"""One-case execution for the Single-first Adaptive RCA Agent."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from time import monotonic
from typing import Protocol, TypeVar

from ecomsre_rcaeval.adapter import (
    ArchitectureContext,
    ArchitectureContextBuilder,
    IncidentManifest,
    incident_for_case,
)
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rcaeval.dataset import DevCase
from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    StdlibOpenAICompatibleTransport,
)
from ecomsre_rcaeval_adaptive.contracts import (
    AdaptiveCaseResult,
    AdaptiveDiagnosis,
    AdaptiveOperationRole,
    AdaptiveOperationTrace,
    AdaptiveTerminalRecord,
    AdaptiveTerminalStatus,
    CausalRole,
    EscalationRoute,
    FusionDecision,
    InitialDiagnosis,
    InitialDiagnosisInput,
    RankedHypothesis,
    RankedHypothesisBatch,
)
from ecomsre_rcaeval_adaptive.fusion import FUSION_PROMPT, FusionInput
from ecomsre_rcaeval_adaptive.gate import GateInputs, GatePolicy, decide_escalation
from ecomsre_rcaeval_adaptive.indicator import (
    IndicatorPolicy,
    resolve_hybrid_indicator,
)
from ecomsre_rcaeval_v2.contracts import (
    BoundedEvidenceSnapshotV2,
    IndicatorCandidateSnapshotV2,
    ProviderUsageDelta,
)
from ecomsre_rcaeval_v2.indicator import (
    FormulaId,
    LoadedIndicatorConfig,
    MetricIndicatorCandidate,
)
from ecomsre_rcaeval_v2.indicator_evaluation import build_runtime_metric_candidates
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev3ProviderProxy,
    Dev3RetryingTransport,
    FailureClass,
    SemanticOperationRecord,
    seal_interrupted_provider_sidecar,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    rebuild_attempt_accounting,
)
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    case_identity_bytes,
)
from ecomsre_rcaeval_adaptive.specialists import (
    INITIAL_PROMPT,
    LOGS_PROMPT,
    TRACES_PROMPT,
    OpenAICompatibleAdaptiveProvider,
)
from ecomsre_rcaeval_v2.provider import ProviderCallDelta, ProviderCounterSnapshot


class AdaptiveDiagnosisProvider(Protocol):
    def usage_snapshot(self) -> ProviderCounterSnapshot: ...

    def usage_delta_since(self, before: ProviderCounterSnapshot) -> ProviderCallDelta: ...

    def diagnose(
        self,
        initial_input: InitialDiagnosisInput,
        *,
        before_output_validation: Callable[[], None] | None = None,
    ) -> InitialDiagnosis: ...

    def specialize(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        source: str,
        initial_diagnosis: InitialDiagnosis,
        *,
        before_output_validation: Callable[[], None] | None = None,
    ) -> RankedHypothesisBatch: ...

    def judge(
        self,
        fusion_input: FusionInput,
        *,
        before_output_validation: Callable[[], None] | None = None,
    ) -> FusionDecision: ...


ResultT = TypeVar("ResultT")


def _candidate_snapshots(
    candidates: tuple[MetricIndicatorCandidate, ...],
) -> tuple[IndicatorCandidateSnapshotV2, ...]:
    return tuple(
        IndicatorCandidateSnapshotV2(
            service=item.service,
            canonical_indicator=item.canonical_indicator,
            metric_name=item.metric_name,
            score=item.score,
            evidence_ref=item.evidence_ref,
        )
        for item in candidates[:6]
    )


def _service_ranking(
    candidates: tuple[MetricIndicatorCandidate, ...],
) -> tuple[tuple[str, float], ...]:
    result: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in candidates:
        if item.service not in seen:
            result.append((item.service, float(item.score)))
            seen.add(item.service)
        if len(result) == 6:
            break
    return tuple(result)


def _bounded_evidence(context: ArchitectureContext) -> tuple[BoundedEvidenceSnapshotV2, ...]:
    source_by_prefix = {"metric": "metrics", "log": "logs", "trace": "traces"}
    output: list[BoundedEvidenceSnapshotV2] = []
    for item in context.evidence:
        prefix = item.evidence_id.partition(":")[0]
        source = source_by_prefix.get(prefix)
        if source is None:
            raise ValueError("Adaptive evidence reference has an invalid source")
        output.append(
            BoundedEvidenceSnapshotV2(
                evidence_ref=item.evidence_id,
                source=source,  # type: ignore[arg-type]
                service=item.service,
                observation=item.summary,
            )
        )
    return tuple(output)


def _metrics_hypotheses(
    candidates: tuple[MetricIndicatorCandidate, ...],
) -> tuple[RankedHypothesis, ...]:
    output: list[RankedHypothesis] = []
    for service, _score in _service_ranking(candidates)[:3]:
        candidate = next(item for item in candidates if item.service == service)
        output.append(
            RankedHypothesis(
                service=service,
                indicator_or_none=candidate.canonical_indicator,
                score=max(0.0, candidate.score),
                causal_role=CausalRole.ROOT_CANDIDATE,
                supporting_evidence_refs=(candidate.evidence_ref,),
                contradicting_evidence_refs=(),
                summary="Deterministic Metrics service anchor.",
                source="metrics",
            )
        )
    if not output:
        raise ValueError("Adaptive Metrics anchor has no ranked service")
    return tuple(output)


def _sum_usage(traces: tuple[AdaptiveOperationTrace, ...]) -> ProviderUsageDelta:
    known = all(item.usage.token_usage_known for item in traces)
    return ProviderUsageDelta(
        model_calls_delta=len(traces),
        prompt_tokens_delta=(
            sum(item.usage.prompt_tokens_delta for item in traces) if known else 0
        ),
        completion_tokens_delta=(
            sum(item.usage.completion_tokens_delta for item in traces) if known else 0
        ),
        total_tokens_delta=(
            sum(item.usage.total_tokens_delta for item in traces) if known else 0
        ),
        token_usage_known=known,
    )


def execute_adaptive_case(
    case: TelemetryCase,
    *,
    run_id: str,
    case_identity_sha256: str,
    provider: AdaptiveDiagnosisProvider,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    gate_policy: GatePolicy,
    indicator_policy: IndicatorPolicy,
) -> AdaptiveCaseResult:
    builder = ArchitectureContextBuilder(case, Architecture.SINGLE, run_id=run_id)
    builder.query_source("metrics")
    builder.query_source("logs")
    context = builder.snapshot()
    incident = incident_for_case(case)
    candidates = build_runtime_metric_candidates(
        case,
        case_identity_sha256=case_identity_sha256,
        formula=indicator_formula,
        config=indicator_config,
    )
    candidate_snapshots = _candidate_snapshots(candidates)
    initial_bounded_evidence = _bounded_evidence(context)
    initial_input = InitialDiagnosisInput(
        incident=incident,
        bounded_evidence=initial_bounded_evidence,
        indicator_candidates=candidate_snapshots,
        visible_services=tuple(
            sorted(
                {
                    *(item.service for item in initial_bounded_evidence),
                    *(item.service for item in candidate_snapshots),
                }
            )
        ),
        visible_evidence_refs=tuple(
            sorted(
                {
                    *(item.evidence_ref for item in initial_bounded_evidence),
                    *(item.evidence_ref for item in candidate_snapshots),
                }
            )
        ),
    )
    operation_trace: list[AdaptiveOperationTrace] = []

    def invoke(
        role: AdaptiveOperationRole,
        source: str | None,
        action: Callable[[], ResultT],
    ) -> ResultT:
        before = provider.usage_snapshot()
        result = action()
        delta = provider.usage_delta_since(before)
        if delta.provider_call_index is None or delta.usage.model_calls_delta != 1:
            raise ValueError("Adaptive Provider operation did not make exactly one call")
        operation_trace.append(
            AdaptiveOperationTrace(
                semantic_operation_index=len(operation_trace) + 1,
                role=role,
                source=source,  # type: ignore[arg-type]
                provider_call_index=delta.provider_call_index,
                usage=delta.usage,
            )
        )
        return result

    initial = invoke(
        AdaptiveOperationRole.INITIAL_DIAGNOSIS,
        None,
        lambda: provider.diagnose(
            initial_input,
            before_output_validation=lambda: None,
        ),
    )
    evidence_services = {
        item.evidence_ref: item.service for item in initial_input.bounded_evidence
    }
    evidence_services.update(
        {item.evidence_ref: item.service for item in initial_input.indicator_candidates}
    )
    metrics_ranking = _service_ranking(candidates)
    metrics_top = metrics_ranking[0][0] if metrics_ranking else None
    log_top = next(
        (
            item.service
            for item in initial_input.bounded_evidence
            if item.evidence_ref.startswith("log:") and item.service != "unknown"
        ),
        None,
    )
    decision = decide_escalation(
        GateInputs(
            initial_diagnosis=initial,
            metrics_service_ranking=metrics_ranking,
            initial_evidence_supports_predicted_service=any(
                evidence_services.get(reference) == initial.root_cause_service
                for reference in initial.evidence_refs
            ),
            cross_source_service_disagreement=(
                metrics_top is not None and log_top is not None and metrics_top != log_top
            ),
            indicator_candidate_available=any(
                item.service == initial.root_cause_service for item in candidates
            ),
            trace_available=case.traces_path is not None,
        ),
        gate_policy,
    )
    hypotheses: list[RankedHypothesis] = []
    if decision.route in {
        EscalationRoute.ESCALATE_TRACES,
        EscalationRoute.ESCALATE_BOTH,
    }:
        builder.query_source("traces")
        context = builder.snapshot()
    if decision.route in {
        EscalationRoute.ESCALATE_LOGS,
        EscalationRoute.ESCALATE_BOTH,
    }:
        batch = invoke(
            AdaptiveOperationRole.LOGS_VERIFIER,
            "logs",
            lambda: provider.specialize(
                incident,
                context,
                "logs",
                initial,
                before_output_validation=lambda: None,
            ),
        )
        hypotheses.extend(batch.hypotheses)
    if decision.route in {
        EscalationRoute.ESCALATE_TRACES,
        EscalationRoute.ESCALATE_BOTH,
    }:
        batch = invoke(
            AdaptiveOperationRole.TRACE_CAUSAL_SPECIALIST,
            "traces",
            lambda: provider.specialize(
                incident,
                context,
                "traces",
                initial,
                before_output_validation=lambda: None,
            ),
        )
        hypotheses.extend(batch.hypotheses)

    fusion: FusionDecision | None = None
    if decision.route is not EscalationRoute.DIRECT_RETURN:
        metrics_hypotheses = _metrics_hypotheses(candidates)
        bounded = list(_bounded_evidence(context))
        known_refs = {item.evidence_ref for item in bounded}
        for item in candidates[:6]:
            if item.evidence_ref not in known_refs:
                bounded.append(
                    BoundedEvidenceSnapshotV2(
                        evidence_ref=item.evidence_ref,
                        source="metrics",
                        service=item.service,
                        observation="Deterministic metric indicator candidate.",
                    )
                )
                known_refs.add(item.evidence_ref)
        fusion_input = FusionInput(
            initial_diagnosis=initial,
            metrics_hypotheses=metrics_hypotheses,
            specialist_hypotheses=tuple(hypotheses),
            bounded_evidence=tuple(bounded),
        )
        fusion = invoke(
            AdaptiveOperationRole.FUSION_JUDGE,
            None,
            lambda: provider.judge(
                fusion_input, before_output_validation=lambda: None
            ),
        )
    final_root = (
        initial.root_cause_service if fusion is None else fusion.final_root_service
    )
    indicator = resolve_hybrid_indicator(
        final_root, initial, candidates, indicator_policy
    )
    refs: list[str] = list(initial.evidence_refs)
    for hypothesis in hypotheses:
        refs.extend(hypothesis.supporting_evidence_refs)
        refs.extend(hypothesis.contradicting_evidence_refs)
    if fusion is not None:
        refs.extend(fusion.supporting_evidence_refs)
        refs.extend(fusion.contradicting_evidence_refs)
    if indicator.evidence_ref is not None:
        refs.append(indicator.evidence_ref)
    diagnosis = AdaptiveDiagnosis(
        initial_diagnosis=initial,
        escalation_decision=decision,
        specialist_hypotheses=tuple(hypotheses),
        fusion_decision_or_none=fusion,
        final_root_service=final_root,
        final_indicator=indicator.final_indicator,
        indicator_resolution=indicator,
        evidence_refs=tuple(dict.fromkeys(refs)),
    )
    traces = tuple(operation_trace)
    return AdaptiveCaseResult(
        run_id=run_id,
        case_id=case.case_id,
        system=case.system,  # type: ignore[arg-type]
        diagnosis=diagnosis,
        operation_trace=traces,
        tool_calls=builder.tool_call_count,
        semantic_operations=len(traces),
        usage=_sum_usage(traces),
    )


def _terminal_bytes(record: AdaptiveTerminalRecord) -> bytes:
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


def write_candidate_config_create_once(
    *,
    run_root: Path,
    candidate_id: str,
    run_domain: str,
    agent_config: Mapping[str, object],
    indicator_policy: IndicatorPolicy,
    implementation_git_sha: str,
) -> Path:
    """Bind one private candidate root to the exact Agent and prompt config."""

    if candidate_id not in {"candidate-1", "candidate-2", "candidate-3"}:
        raise ValueError("adaptive candidate ID is outside the bounded search")
    if run_domain not in {
        "single-first-adaptive-v1-interface-fix-r1",
        "single-first-adaptive-v1-interface-fix-r2",
    }:
        raise ValueError("adaptive run domain is invalid")
    if len(implementation_git_sha) != 40 or any(
        item not in "0123456789abcdef" for item in implementation_git_sha
    ):
        raise ValueError("adaptive implementation Git SHA is invalid")
    prompts = {
        "fusion": FUSION_PROMPT,
        "initial": INITIAL_PROMPT,
        "logs_specialist": LOGS_PROMPT,
        "traces_specialist": TRACES_PROMPT,
    }
    payload = {
        "schema_version": "rcaeval-single-first-adaptive.candidate-config.v1",
        "evaluation_version": "single-first-adaptive-v1",
        "candidate_id": candidate_id,
        "run_domain": run_domain,
        "agent_config": dict(agent_config),
        "prompt_sha256": {
            name: hashlib.sha256(value.encode()).hexdigest()
            for name, value in prompts.items()
        },
        "indicator_policy": indicator_policy.model_dump(mode="json"),
        "implementation_git_sha": implementation_git_sha,
    }
    encoded = (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    output = run_root / "candidate-config.json"
    if output.exists():
        if output.is_symlink() or not output.is_file() or output.read_bytes() != encoded:
            raise ValueError("adaptive candidate config differs from existing root")
        return output
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.parent.chmod(0o700)
    with output.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    output.chmod(0o600)
    return output


def require_clean_implementation_git_sha(repository_root: Path) -> str:
    """Return HEAD only when all candidate-defining tracked scopes are clean."""

    scopes = (
        "config/rcaeval-adaptive-v1",
        "scripts/rcaeval_adaptive",
        "src/ecomsre_rcaeval_adaptive",
        "src/ecomsre_rcaeval_v2/dev3_provider.py",
    )
    status = subprocess.run(
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *scopes,
        ),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0 or status.stdout:
        raise ValueError("adaptive candidate implementation scope is not clean")
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    value = head.stdout.strip()
    if head.returncode != 0 or len(value) != 40 or any(
        item not in "0123456789abcdef" for item in value
    ):
        raise ValueError("adaptive candidate implementation HEAD is invalid")
    return value


def _write_terminal_create_once(path: Path, record: AdaptiveTerminalRecord) -> None:
    payload = _terminal_bytes(record)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _last_semantic_failure(
    sidecar_root: Path,
) -> tuple[AdaptiveTerminalStatus, str | None, str, str | None]:
    paths = sorted((sidecar_root / "semantic-operations").glob("*.json"))
    if not paths:
        return (
            AdaptiveTerminalStatus.RUNTIME_CONTRACT_VIOLATION,
            FailureClass.NON_RETRYABLE_LOCAL_CONTRACT.value,
            "LOCAL_RUNTIME_CONTRACT_FAILURE",
            "INPUT_CONSTRUCTION",
        )
    record = SemanticOperationRecord.model_validate_json(
        paths[-1].read_text(encoding="utf-8")
    )
    status_by_failure_class = {
        FailureClass.NON_RETRYABLE_SCHEMA: AdaptiveTerminalStatus.INVALID_SCHEMA,
        FailureClass.NON_RETRYABLE_PROTOCOL: AdaptiveTerminalStatus.PROTOCOL_VIOLATION,
        FailureClass.NON_RETRYABLE_LOCAL_CONTRACT: (
            AdaptiveTerminalStatus.RUNTIME_CONTRACT_VIOLATION
        ),
        FailureClass.ALLOWLISTED_TRANSPORT_TRANSIENT: (
            AdaptiveTerminalStatus.PROVIDER_FAILURE
        ),
        FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE: (
            AdaptiveTerminalStatus.PROVIDER_FAILURE
        ),
    }
    status = (
        AdaptiveTerminalStatus.PROVIDER_FAILURE
        if record.failure_class is None
        else status_by_failure_class[record.failure_class]
    )
    return (
        status,
        None if record.failure_class is None else record.failure_class.value,
        record.failure_code or "UNKNOWN_ADAPTIVE_FAILURE",
        record.failure_stage,
    )


def _accounting(sidecar_root: Path):
    return rebuild_attempt_accounting(
        (sidecar_root,),
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
    )


def execute_adaptive_scheduled_once(
    *,
    case: TelemetryCase,
    run_id: str,
    case_identity_sha256: str,
    candidate_id: str,
    split: str,
    provider: AdaptiveDiagnosisProvider,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    gate_policy: GatePolicy,
    indicator_policy: IndicatorPolicy,
    terminal_root: Path,
    sidecar_root: Path,
    policy_lock_sha256: str,
) -> AdaptiveTerminalRecord:
    """Execute once; durable sidecars or a terminal permanently consume a run ID."""

    terminal_path = terminal_root / f"{run_id}.json"
    if terminal_path.exists():
        if terminal_path.is_symlink() or not terminal_path.is_file():
            raise ValueError("adaptive terminal path is invalid")
        terminal = AdaptiveTerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        if (
            terminal.run_id != run_id
            or terminal.case_id != case.case_id
            or terminal.candidate_id != candidate_id
            or terminal.split != split
        ):
            raise ValueError("existing adaptive terminal identity differs")
        return terminal

    started_at = datetime.now(timezone.utc)
    monotonic_started = monotonic()
    existing_sidecar_files = sidecar_root.exists() and any(
        path.is_file() for path in sidecar_root.rglob("*")
    )
    if existing_sidecar_files:
        seal_interrupted_provider_sidecar(
            sidecar_root,
            policy_lock_sha256=policy_lock_sha256,
            expected_timeout_seconds=30.0,
            fallback_operation_type="FINAL_JUDGE",
        )
        terminal = AdaptiveTerminalRecord(
            schema_version="rcaeval-single-first-adaptive.terminal.v1",
            evaluation_version="single-first-adaptive-v1",
            candidate_id=candidate_id,
            split=split,  # type: ignore[arg-type]
            run_id=run_id,
            case_id=case.case_id,
            system=case.system,  # type: ignore[arg-type]
            status=AdaptiveTerminalStatus.INTERRUPTED,
            result=None,
            failure_class=FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE.value,
            failure_code="INTERRUPTED_ADAPTIVE_RUN",
            failure_stage="PROVIDER_CALL",
            safe_validation_error=None,
            started_at_utc=started_at,
            ended_at_utc=datetime.now(timezone.utc),
            latency_ms=0.0,
            attempt_accounting=_accounting(sidecar_root),
            policy_lock_sha256=policy_lock_sha256,
        )
        _write_terminal_create_once(terminal_path, terminal)
        return terminal

    result: AdaptiveCaseResult | None = None
    status = AdaptiveTerminalStatus.COMPLETED
    failure_class: str | None = None
    failure_code: str | None = None
    failure_stage: str | None = None
    safe_validation_error = None
    try:
        result = execute_adaptive_case(
            case,
            run_id=run_id,
            case_identity_sha256=case_identity_sha256,
            provider=provider,
            indicator_formula=indicator_formula,
            indicator_config=indicator_config,
            gate_policy=gate_policy,
            indicator_policy=indicator_policy,
        )
    except Exception:
        status, failure_class, failure_code, failure_stage = _last_semantic_failure(
            sidecar_root
        )
        safe_validation_error = getattr(
            provider, "last_safe_validation_error", None
        )
    ended_at = datetime.now(timezone.utc)
    terminal = AdaptiveTerminalRecord(
        schema_version="rcaeval-single-first-adaptive.terminal.v1",
        evaluation_version="single-first-adaptive-v1",
        candidate_id=candidate_id,
        split=split,  # type: ignore[arg-type]
        run_id=run_id,
        case_id=case.case_id,
        system=case.system,  # type: ignore[arg-type]
        status=status,
        result=result,
        failure_class=failure_class,
        failure_code=failure_code,
        failure_stage=failure_stage,
        safe_validation_error=safe_validation_error,
        started_at_utc=started_at,
        ended_at_utc=ended_at,
        latency_ms=float(max(0.0, (monotonic() - monotonic_started) * 1_000)),
        attempt_accounting=_accounting(sidecar_root),
        policy_lock_sha256=policy_lock_sha256,
    )
    _write_terminal_create_once(terminal_path, terminal)
    return terminal


def adaptive_run_id(
    run_domain: str,
    candidate_id: str,
    split: str,
    identity: CaseIdentity,
) -> str:
    if run_domain not in {
        "single-first-adaptive-v1-interface-fix-r1",
        "single-first-adaptive-v1-interface-fix-r2",
    }:
        raise ValueError("adaptive run domain is invalid")
    if candidate_id not in {"candidate-1", "candidate-2", "candidate-3"}:
        raise ValueError("adaptive candidate ID is outside the bounded search")
    if split not in {"DESIGN", "DEV_VALIDATION"}:
        raise ValueError("adaptive split is invalid")
    return hashlib.sha256(
        b"\0".join(
            (
                b"single-first-adaptive-v1",
                run_domain.encode(),
                candidate_id.encode(),
                split.encode(),
                case_identity_bytes(identity),
            )
        )
    ).hexdigest()[:32]


def execute_adaptive_batch(
    identities: tuple[CaseIdentity, ...],
    *,
    cases: Mapping[CaseIdentity, DevCase],
    candidate_id: str,
    run_domain: str,
    split: str,
    provider_config: OpenAICompatibleConfig,
    model: str,
    timeout_seconds: float,
    max_completion_tokens: int,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    gate_policy: GatePolicy,
    indicator_policy: IndicatorPolicy,
    agent_config: Mapping[str, object],
    implementation_git_sha: str,
    run_root: Path,
    policy_lock_sha256: str,
    max_semantic_operations: int,
    max_provider_attempts: int,
    max_transport_retries: int,
    max_conservative_tokens: int,
    progress: Callable[[int, int, AdaptiveTerminalRecord], None] | None = None,
) -> tuple[AdaptiveTerminalRecord, ...]:
    """Execute one bounded candidate arm with one shared attempt ledger."""

    if len(set(identities)) != len(identities):
        raise ValueError("adaptive batch identities must be unique")
    write_candidate_config_create_once(
        run_root=run_root,
        candidate_id=candidate_id,
        run_domain=run_domain,
        agent_config=agent_config,
        indicator_policy=indicator_policy,
        implementation_git_sha=implementation_git_sha,
    )
    operation_ceiling = 4 * len(identities)
    if operation_ceiling > max_semantic_operations:
        raise ValueError("adaptive batch exceeds the frozen semantic-operation cap")
    run_ids = tuple(
        adaptive_run_id(run_domain, candidate_id, split, identity)
        for identity in identities
    )
    sidecar_roots = tuple(
        run_root / "provider-sidecars" / run_id for run_id in run_ids
    )
    budget = AttemptBudget.restore(
        sidecar_roots,
        max_provider_attempts=max_provider_attempts,
        max_retry_attempts=max_transport_retries,
        prompt_token_reservation=29_952,
        max_completion_tokens=max_completion_tokens,
        max_conservative_tokens=max_conservative_tokens,
    )
    terminals: list[AdaptiveTerminalRecord] = []
    semantic_operations = 0
    for index, (identity, run_id, sidecar_root) in enumerate(
        zip(identities, run_ids, sidecar_roots, strict=True), 1
    ):
        case = cases.get(identity)
        if case is None:
            raise ValueError("adaptive scheduled identity is absent from the case index")
        transport = Dev3RetryingTransport(
            StdlibOpenAICompatibleTransport(),
            run_root=sidecar_root,
            budget=budget,
            policy_lock_sha256=policy_lock_sha256,
            expected_timeout_seconds=timeout_seconds,
        )
        inner = OpenAICompatibleAdaptiveProvider(
            config=provider_config,
            expected_model=model,
            timeout_seconds=timeout_seconds,
            max_completion_tokens=max_completion_tokens,
            transport=transport,
        )
        provider = Dev3ProviderProxy(
            inner,
            run_root=sidecar_root,
            policy_lock_sha256=policy_lock_sha256,
        )
        terminal = execute_adaptive_scheduled_once(
            case=dev_case_to_telemetry_case(case),
            run_id=run_id,
            case_identity_sha256=hashlib.sha256(
                case_identity_bytes(identity)
            ).hexdigest(),
            candidate_id=candidate_id,
            split=split,
            provider=provider,  # type: ignore[arg-type]
            indicator_formula=indicator_formula,
            indicator_config=indicator_config,
            gate_policy=gate_policy,
            indicator_policy=indicator_policy,
            terminal_root=run_root / "terminal-records",
            sidecar_root=sidecar_root,
            policy_lock_sha256=policy_lock_sha256,
        )
        terminals.append(terminal)
        semantic_operations += (
            terminal.result.semantic_operations
            if terminal.result is not None
            else len(tuple((sidecar_root / "semantic-operations").glob("*.json")))
        )
        if semantic_operations > max_semantic_operations:
            raise ValueError("adaptive batch exceeded the semantic-operation cap")
        if progress is not None:
            progress(index, len(identities), terminal)
    return tuple(terminals)


__all__ = [
    "AdaptiveDiagnosisProvider",
    "adaptive_run_id",
    "execute_adaptive_case",
    "require_clean_implementation_git_sha",
    "write_candidate_config_create_once",
]
