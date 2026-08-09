"""Independent one-call runtime for deterministic Metrics M3 arbitration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from typing import Literal, Protocol, cast

from pydantic import AwareDatetime, Field, StrictFloat, StrictInt, model_validator

from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
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
from ecomsre_rcaeval_adaptive.contracts import AdaptiveTerminalStatus, V2Model
from ecomsre_rcaeval_adaptive.metrics_arbitration import (
    MetricsArbitratedDiagnosis,
    MetricsArbitrationPolicy,
    MetricsServiceRank,
    arbitrate_diagnosis,
    decide_metrics_arbitration,
)
from ecomsre_rcaeval_adaptive.v2_runner import PacedTransport, RequestPacer
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.dev3_execution import new_v1_reference_provider
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev3ProviderProxy,
    Dev3RetryingTransport,
    FailureClass,
    SemanticOperationRecord,
    SemanticOperationStart,
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


class MetricsArbitrationDiagnosisProvider(Protocol):
    @property
    def calls(self) -> int: ...

    def diagnose(
        self,
        incident: IncidentManifest,
        context: ArchitectureContext,
        architecture: Architecture,
    ) -> Diagnosis: ...


class MetricsArbitrationOperationTrace(V2Model):
    semantic_operation_index: Literal[1] = 1
    role: Literal["INITIAL_DIAGNOSIS"] = "INITIAL_DIAGNOSIS"
    provider_call_index: Literal[1] = 1


class MetricsArbitrationCaseResult(V2Model):
    schema_version: Literal[
        "rcaeval-metrics-arbitration.case-result.v1"
    ] = "rcaeval-metrics-arbitration.case-result.v1"
    evaluation_version: Literal[
        "metrics-arbitration-v1"
    ] = "metrics-arbitration-v1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: Literal["RE2-OB", "RE2-SS"]
    diagnosis: MetricsArbitratedDiagnosis
    metrics_service_ranking: tuple[MetricsServiceRank, ...] = Field(
        min_length=1, max_length=6
    )
    operation_trace: tuple[MetricsArbitrationOperationTrace, ...]
    tool_calls: Literal[3] = 3
    semantic_operations: Literal[1] = 1
    specialist_calls: Literal[0] = 0
    fusion_model_calls: Literal[0] = 0

    @model_validator(mode="after")
    def require_one_call_runtime(self) -> MetricsArbitrationCaseResult:
        if len(self.operation_trace) != 1:
            raise ValueError("Metrics arbitration requires exactly one operation")
        if self.operation_trace[0].role != "INITIAL_DIAGNOSIS":
            raise ValueError("Metrics arbitration operation role differs")
        expected = decide_metrics_arbitration(
            initial_root_service=self.diagnosis.initial_diagnosis.root_cause_service,
            ranking=self.metrics_service_ranking,
            policy=MetricsArbitrationPolicy(),
        )
        if self.diagnosis.arbitration_decision != expected:
            raise ValueError("Metrics arbitration ranking differs from decision")
        return self


class MetricsArbitrationTerminalRecord(V2Model):
    schema_version: Literal[
        "rcaeval-metrics-arbitration.terminal.v1"
    ] = "rcaeval-metrics-arbitration.terminal.v1"
    evaluation_version: Literal[
        "metrics-arbitration-v1"
    ] = "metrics-arbitration-v1"
    split: Literal["TUNE_SET", "REGRESSION_SET"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: Literal["RE2-OB", "RE2-SS"]
    status: AdaptiveTerminalStatus
    result: MetricsArbitrationCaseResult | None
    failure_class: str | None = Field(default=None, max_length=128)
    failure_code: str | None = Field(default=None, max_length=128)
    failure_stage: str | None = Field(default=None, max_length=64)
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime
    latency_ms: StrictFloat = Field(ge=0.0)
    attempt_accounting: AttemptAccountingSummary
    policy_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_operations_attempted: StrictInt = Field(ge=0, le=1)
    specialist_calls_attempted: Literal[0] = 0
    fusion_model_calls_attempted: Literal[0] = 0

    @model_validator(mode="after")
    def require_terminal_consistency(self) -> MetricsArbitrationTerminalRecord:
        completed = self.status is AdaptiveTerminalStatus.COMPLETED
        if completed != (self.result is not None):
            raise ValueError("Metrics arbitration completion differs from result")
        if completed and any(
            value is not None
            for value in (self.failure_class, self.failure_code, self.failure_stage)
        ):
            raise ValueError("completed Metrics arbitration terminal has failure")
        if not completed and self.failure_code is None:
            raise ValueError("failed Metrics arbitration terminal lacks failure code")
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("Metrics arbitration terminal ended before start")
        if completed:
            assert self.result is not None
            if self.semantic_operations_attempted != 1:
                raise ValueError("completed Metrics arbitration did not attempt one call")
            if (
                self.result.run_id != self.run_id
                or self.result.case_id != self.case_id
                or self.result.system != self.system
            ):
                raise ValueError("Metrics arbitration terminal identity differs")
            if (
                self.attempt_accounting.provider_attempt_count not in {1, 2}
                or self.attempt_accounting.retry_attempt_count > 1
            ):
                raise ValueError("completed Metrics arbitration attempt count differs")
        return self


def _metrics_ranking(
    context: ArchitectureContext,
    case: TelemetryCase,
    *,
    identity_sha256: str,
    formula: FormulaId,
    config: LoadedIndicatorConfig,
) -> tuple[MetricsServiceRank, ...]:
    candidates = build_runtime_metric_candidates(
        case,
        case_identity_sha256=identity_sha256,
        formula=formula,
        config=config,
    )
    scores: list[tuple[str, float]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.service not in seen:
            scores.append((candidate.service, float(candidate.score)))
            seen.add(candidate.service)
        if len(scores) == 6:
            break
    evidence_by_service: dict[str, list[str]] = {}
    for item in context.evidence:
        if item.evidence_id.startswith("metric:"):
            evidence_by_service.setdefault(item.service, []).append(item.evidence_id)
    output: list[MetricsServiceRank] = []
    for rank, (service, score) in enumerate(scores, start=1):
        evidence_refs = tuple(dict.fromkeys(evidence_by_service.get(service, ())))
        if rank == 1 and not evidence_refs:
            raise ValueError("ranked Metrics service lacks legal Metrics evidence")
        output.append(
            MetricsServiceRank(
                service=service,
                rank=rank,
                score=score,
                supporting_metrics_evidence_refs=evidence_refs,
            )
        )
    if not output:
        raise ValueError("Metrics arbitration produced no service ranking")
    return tuple(output)


def execute_metrics_arbitration_case(
    case: TelemetryCase,
    *,
    run_id: str,
    identity_sha256: str,
    provider: MetricsArbitrationDiagnosisProvider,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    policy: MetricsArbitrationPolicy,
) -> MetricsArbitrationCaseResult:
    """Run all three tools, one Strong Single call, then deterministic M3."""

    builder = ArchitectureContextBuilder(case, Architecture.SINGLE, run_id=run_id)
    for source in ("metrics", "logs", "traces"):
        builder.query_source(source)  # type: ignore[arg-type]
    context = builder.snapshot()
    ranking = _metrics_ranking(
        context,
        case,
        identity_sha256=identity_sha256,
        formula=indicator_formula,
        config=indicator_config,
    )
    before = provider.calls
    initial = provider.diagnose(
        incident_for_case(case), context, Architecture.SINGLE
    )
    after = provider.calls
    if before != 0 or after != 1:
        raise ValueError("Metrics arbitration Initial made an invalid call count")
    diagnosis = arbitrate_diagnosis(initial, ranking, policy)
    return MetricsArbitrationCaseResult(
        run_id=run_id,
        case_id=case.case_id,
        system=case.system,  # type: ignore[arg-type]
        diagnosis=diagnosis,
        metrics_service_ranking=ranking,
        operation_trace=(MetricsArbitrationOperationTrace(),),
    )


def metrics_arbitration_run_id(split: str, identity: CaseIdentity) -> str:
    if split not in {"TUNE_SET", "REGRESSION_SET"}:
        raise ValueError("Metrics arbitration split is invalid")
    return hashlib.sha256(
        b"\0".join(
            (
                b"metrics-arbitration-v1",
                split.encode("utf-8"),
                case_identity_bytes(identity),
            )
        )
    ).hexdigest()[:32]


def _attempt_accounting(
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
    if record.status == "COMPLETED":
        return (
            AdaptiveTerminalStatus.RUNTIME_CONTRACT_VIOLATION,
            FailureClass.NON_RETRYABLE_LOCAL_CONTRACT.value,
            "POST_PROVIDER_M3_CONTRACT_FAILURE",
            "DETERMINISTIC_ARBITRATION",
        )
    statuses = {
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
        else statuses[record.failure_class]
    )
    return (
        status,
        None if record.failure_class is None else record.failure_class.value,
        record.failure_code or "UNKNOWN_METRICS_ARBITRATION_FAILURE",
        record.failure_stage,
    )


def _operation_attempt_count(sidecar_root: Path) -> int:
    starts = tuple(
        SemanticOperationStart.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(
            (sidecar_root / "semantic-operation-starts").glob("*.json")
        )
    )
    if len(starts) > 1 or any(item.operation_type != "FINAL_JUDGE" for item in starts):
        raise ValueError("Metrics arbitration attempted a non-Initial operation")
    return len(starts)


def _write_create_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _terminal_bytes(record: MetricsArbitrationTerminalRecord) -> bytes:
    return (
        json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def execute_metrics_arbitration_scheduled_once(
    *,
    case: TelemetryCase,
    identity_sha256: str,
    run_id: str,
    split: Literal["TUNE_SET", "REGRESSION_SET"],
    provider: MetricsArbitrationDiagnosisProvider,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    policy: MetricsArbitrationPolicy,
    terminal_root: Path,
    sidecar_root: Path,
    policy_lock_sha256: str,
    max_completion_tokens: int,
) -> MetricsArbitrationTerminalRecord:
    terminal_path = terminal_root / f"{run_id}.json"
    if terminal_path.exists():
        terminal = MetricsArbitrationTerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        if (
            terminal.run_id != run_id
            or terminal.case_id != case.case_id
            or terminal.system != case.system
            or terminal.split != split
            or terminal.policy_lock_sha256 != policy_lock_sha256
        ):
            raise ValueError("existing Metrics arbitration terminal identity differs")
        return terminal
    started_at = datetime.now(timezone.utc)
    monotonic_started = monotonic()
    result: MetricsArbitrationCaseResult | None
    failure_class: str | None
    failure_code: str | None
    failure_stage: str | None
    if sidecar_root.exists() and any(path.is_file() for path in sidecar_root.rglob("*")):
        seal_interrupted_provider_sidecar(
            sidecar_root,
            policy_lock_sha256=policy_lock_sha256,
            expected_timeout_seconds=30.0,
            fallback_operation_type="FINAL_JUDGE",
        )
        status = AdaptiveTerminalStatus.INTERRUPTED
        result = None
        failure_class = FailureClass.UNKNOWN_INSUFFICIENT_EVIDENCE.value
        failure_code = "INTERRUPTED_METRICS_ARBITRATION_RUN"
        failure_stage = "PROVIDER_CALL"
    else:
        try:
            result = execute_metrics_arbitration_case(
                case,
                run_id=run_id,
                identity_sha256=identity_sha256,
                provider=cast(MetricsArbitrationDiagnosisProvider, provider),
                indicator_formula=indicator_formula,
                indicator_config=indicator_config,
                policy=policy,
            )
            status = AdaptiveTerminalStatus.COMPLETED
            failure_class = failure_code = failure_stage = None
        except Exception:
            result = None
            status, failure_class, failure_code, failure_stage = _last_failure(
                sidecar_root
            )
    terminal = MetricsArbitrationTerminalRecord(
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
        latency_ms=float(max(0.0, (monotonic() - monotonic_started) * 1_000)),
        attempt_accounting=_attempt_accounting(sidecar_root, max_completion_tokens),
        policy_lock_sha256=policy_lock_sha256,
        semantic_operations_attempted=_operation_attempt_count(sidecar_root),
    )
    _write_create_once(terminal_path, _terminal_bytes(terminal))
    return terminal


def execute_metrics_arbitration_batch(
    identities: tuple[CaseIdentity, ...],
    *,
    cases: Mapping[CaseIdentity, DevCase],
    split: Literal["TUNE_SET", "REGRESSION_SET"],
    provider_config: OpenAICompatibleConfig,
    timeout_seconds: float,
    max_completion_tokens: int,
    indicator_formula: FormulaId,
    indicator_config: LoadedIndicatorConfig,
    policy: MetricsArbitrationPolicy,
    run_root: Path,
    policy_lock_sha256: str,
    minimum_interval_seconds: float,
    progress: Callable[[int, int, MetricsArbitrationTerminalRecord], None]
    | None = None,
) -> tuple[MetricsArbitrationTerminalRecord, ...]:
    if len(set(identities)) != len(identities):
        raise ValueError("Metrics arbitration identities must be unique")
    run_ids = tuple(metrics_arbitration_run_id(split, item) for item in identities)
    sidecars = tuple(run_root / "provider-sidecars" / item for item in run_ids)
    max_operations = len(identities)
    budget = AttemptBudget.restore(
        sidecars,
        max_provider_attempts=max_operations * 2,
        max_retry_attempts=max_operations,
        prompt_token_reservation=29_952,
        max_completion_tokens=max_completion_tokens,
        max_conservative_tokens=(
            max_operations * (29_952 + max_completion_tokens) * 2
        ),
    )
    pacer = RequestPacer(minimum_interval_seconds)
    output: list[MetricsArbitrationTerminalRecord] = []
    for index, (identity, run_id, sidecar) in enumerate(
        zip(identities, run_ids, sidecars, strict=True), start=1
    ):
        case = cases[identity]
        terminal_path = run_root / "terminal-records" / f"{run_id}.json"
        if terminal_path.exists():
            terminal = MetricsArbitrationTerminalRecord.model_validate_json(
                terminal_path.read_text(encoding="utf-8")
            )
            expected_case = cases[identity]
            if (
                terminal.run_id != run_id
                or terminal.case_id != expected_case.case_id
                or terminal.system != expected_case.system
                or terminal.split != split
                or terminal.policy_lock_sha256 != policy_lock_sha256
            ):
                raise ValueError("reused Metrics arbitration terminal differs")
        else:
            transport = Dev3RetryingTransport(
                PacedTransport(StdlibOpenAICompatibleTransport(), pacer),
                run_root=sidecar,
                budget=budget,
                policy_lock_sha256=policy_lock_sha256,
                expected_timeout_seconds=timeout_seconds,
            )
            provider = Dev3ProviderProxy(
                new_v1_reference_provider(provider_config, transport=transport),
                run_root=sidecar,
                policy_lock_sha256=policy_lock_sha256,
            )
            terminal = execute_metrics_arbitration_scheduled_once(
                case=dev_case_to_telemetry_case(case),
                identity_sha256=hashlib.sha256(
                    case_identity_bytes(identity)
                ).hexdigest(),
                run_id=run_id,
                split=split,
                provider=cast(MetricsArbitrationDiagnosisProvider, provider),
                indicator_formula=indicator_formula,
                indicator_config=indicator_config,
                policy=policy,
                terminal_root=run_root / "terminal-records",
                sidecar_root=sidecar,
                policy_lock_sha256=policy_lock_sha256,
                max_completion_tokens=max_completion_tokens,
            )
        output.append(terminal)
        if progress is not None:
            progress(index, len(identities), terminal)
    return tuple(output)


def aggregate_metrics_arbitration(
    rows: Sequence[Mapping[str, object]], *, scheduled: int | None = None
) -> dict[str, object]:
    """Aggregate already evaluator-projected case rows without reading truth here."""

    denominator = len(rows) if scheduled is None else scheduled
    if denominator <= 0 or len(rows) > denominator:
        raise ValueError("Metrics arbitration aggregate denominator is invalid")
    completed_rows = tuple(row for row in rows if row.get("completed") is True)
    completed = len(completed_rows)
    initial_root_correct = sum(
        row.get("initial_root_correct") is True for row in completed_rows
    )
    root_damage = sum(
        row.get("initial_root_correct") is True
        and row.get("final_root_correct") is False
        for row in completed_rows
    )
    root_rescue = sum(
        row.get("initial_root_correct") is False
        and row.get("final_root_correct") is True
        for row in completed_rows
    )
    pair_damage = sum(
        row.get("initial_pair_correct") is True
        and row.get("final_pair_correct") is False
        for row in completed_rows
    )
    pair_rescue = sum(
        row.get("initial_pair_correct") is False
        and row.get("final_pair_correct") is True
        for row in completed_rows
    )
    semantic_operations = sum(
        value
        for row in completed_rows
        if type(value := row.get("semantic_operations")) is int
    )
    return {
        "scheduled": denominator,
        "terminalized": len(rows),
        "completed": completed,
        "http_429_terminal_failures": sum(
            row.get("failure_code") == "HTTP_429" for row in rows
        ),
        "disqualifying_failure_count": sum(
            row.get("disqualifying_failure") is True for row in rows
        ),
        "initial_root_correct": initial_root_correct,
        "initial_pair_correct": sum(
            row.get("initial_pair_correct") is True for row in completed_rows
        ),
        "final_root_correct": sum(
            row.get("final_root_correct") is True for row in completed_rows
        ),
        "final_pair_correct": sum(
            row.get("final_pair_correct") is True for row in completed_rows
        ),
        "same_run_root_damage": root_damage,
        "same_run_root_rescue": root_rescue,
        "same_run_root_net_rescue": root_rescue - root_damage,
        "same_run_root_damage_rate": {
            "numerator": root_damage,
            "denominator": initial_root_correct,
            "value": 0.0 if initial_root_correct == 0 else root_damage / initial_root_correct,
        },
        "same_run_pair_damage": pair_damage,
        "same_run_pair_rescue": pair_rescue,
        "same_run_pair_net_rescue": pair_rescue - pair_damage,
        "semantic_operations": semantic_operations,
        "mean_semantic_operations": (
            0.0 if completed == 0 else semantic_operations / completed
        ),
        "mean_semantic_operations_basis": "COMPLETED_ONLY",
        "specialist_calls": 0,
        "fusion_model_calls": 0,
    }


def evaluate_metrics_arbitration_gate(
    phase: Literal["smoke", "tune", "regression"],
    aggregate: Mapping[str, object],
) -> bool:
    """Evaluate the frozen Smoke/TUNE/Regression acceptance inequalities."""

    def integer(key: str, default: int = 0) -> int:
        value = aggregate.get(key)
        return value if type(value) is int else default

    def number(key: str, default: float = -1.0) -> float:
        value = aggregate.get(key)
        return (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else default
        )

    completed = integer("completed")
    scheduled = integer("scheduled")
    terminalized = integer("terminalized")
    http_429 = integer("http_429_terminal_failures")
    disqualifying = integer("disqualifying_failure_count")
    semantic = number("mean_semantic_operations")
    specialists = integer("specialist_calls", -1)
    fusion = integer("fusion_model_calls", -1)
    common = (
        terminalized == scheduled
        and disqualifying == 0
        and semantic == 1.0
        and specialists == 0
        and fusion == 0
    )
    if phase == "smoke":
        return common and scheduled == 12 and completed >= 11 and http_429 <= 1
    root_damage = integer("same_run_root_damage")
    root_rescue = integer("same_run_root_rescue")
    root_net = integer("same_run_root_net_rescue", -10**9)
    pair_damage = integer("same_run_pair_damage")
    pair_rescue = integer("same_run_pair_rescue")
    pair_net = integer("same_run_pair_net_rescue", -10**9)
    if phase == "tune":
        return (
            common
            and scheduled == 60
            and completed >= 58
            and http_429 <= 3
            and integer("final_root_correct") >= 51
            and integer("final_pair_correct") >= 27
            and root_rescue > root_damage
            and root_net >= 1
            and root_damage <= 2
            and pair_rescue >= pair_damage
            and pair_net >= 0
        )
    damage_rate = aggregate.get("same_run_root_damage_rate", {})
    raw_damage_rate = (
        damage_rate.get("value") if isinstance(damage_rate, Mapping) else None
    )
    damage_rate_value = (
        float(raw_damage_rate)
        if isinstance(raw_damage_rate, (int, float))
        and not isinstance(raw_damage_rate, bool)
        else 1.0
    )
    return (
        common
        and scheduled == 120
        and completed >= 114
        and http_429 <= 6
        and integer("final_root_correct") >= 97
        and integer("final_pair_correct") >= 50
        and root_rescue >= root_damage
        and root_net >= 0
        and damage_rate_value <= 0.05
        and pair_rescue >= pair_damage
        and pair_net >= 0
    )


__all__ = [
    "MetricsArbitrationCaseResult",
    "MetricsArbitrationDiagnosisProvider",
    "MetricsArbitrationOperationTrace",
    "MetricsArbitrationTerminalRecord",
    "aggregate_metrics_arbitration",
    "evaluate_metrics_arbitration_gate",
    "execute_metrics_arbitration_batch",
    "execute_metrics_arbitration_case",
    "execute_metrics_arbitration_scheduled_once",
    "metrics_arbitration_run_id",
]
