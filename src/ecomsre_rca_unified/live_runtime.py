"""Create-once one-shot runtime records for the paired live comparison."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import time
from typing import Callable, Literal
import urllib.error

from pydantic import AwareDatetime, Field, StrictFloat, StrictInt, model_validator

from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    ProviderProtocolError,
    StdlibOpenAICompatibleTransport,
)
from ecomsre_rca100.contracts import RCA100InitialDiagnosis
from ecomsre_rcaeval_adaptive.v2_runner import PacedTransport, RequestPacer
from ecomsre_rcaeval_v2.contracts import V2Model
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev3ProviderProxy,
    Dev3RetryingTransport,
    seal_interrupted_provider_sidecar,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    ProviderAttemptRecord,
    rebuild_attempt_accounting,
)
from ecomsre_rca_unified.hierarchical_context import (
    EvidenceSource,
    HierarchicalContext,
    LiveBaseContext,
    classify_live_fault_ontology,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer, FaultOntologyClass
from ecomsre_rca_unified.live_comparison import (
    Arm,
    OpenAICompatibleLiveComparisonProvider,
    ScheduledArm,
    execute_arm,
)


class CrossLifecycleRequestPacer(RequestPacer):
    """Conservatively preserve minimum spacing across command and crash boundaries."""

    def __init__(
        self,
        minimum_interval_seconds: float,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(minimum_interval_seconds)
        self._cold_start = True
        self._sleep_fn = sleep_fn

    def wait(self) -> None:
        if self._cold_start:
            self._sleep_fn(self.minimum_interval_seconds)
            self._cold_start = False
        super().wait()


class LiveTerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    TIMEOUT = "TIMEOUT"
    INPUT_PROJECTION_FAILURE = "INPUT_PROJECTION_FAILURE"
    PRIVACY_FAILURE = "PRIVACY_FAILURE"
    INTERRUPTED = "INTERRUPTED"
    NOT_ADMITTED = "NOT_ADMITTED"


class LiveRunAttempt(V2Model):
    schema_version: Literal["strong-single-live.run-attempt.v1"] = (
        "strong-single-live.run-attempt.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    opaque_case_id: str = Field(pattern=r"^case-[0-9a-f]{20}$")
    split: Literal["TUNE", "REGRESSION", "PREFLIGHT"]
    pair_position: StrictInt = Field(ge=1)
    arm_position: StrictInt = Field(ge=1, le=2)
    arm: Arm
    schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at_utc: AwareDatetime


class RootVisibilitySummary(V2Model):
    visible_sources: tuple[EvidenceSource, ...]
    direct_evidence_count: StrictInt = Field(ge=0)
    alert_entity: bool


class RuntimeDiagnosisMetadata(V2Model):
    entity_layer: CanonicalEntityLayer
    service_ancestor_or_none: str | None
    root_provenance: Literal["MODEL_STRONG_SINGLE_B0", "MODEL_STRONG_SINGLE_H1"]
    fault_ontology_class: FaultOntologyClass
    visibility_summary: RootVisibilitySummary


class LiveTerminalRecord(V2Model):
    schema_version: Literal["strong-single-live.terminal.v1"] = (
        "strong-single-live.terminal.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    opaque_case_id: str = Field(pattern=r"^case-[0-9a-f]{20}$")
    split: Literal["TUNE", "REGRESSION", "PREFLIGHT"]
    pair_position: StrictInt = Field(ge=1)
    arm_position: StrictInt = Field(ge=1, le=2)
    arm: Arm
    schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: LiveTerminalStatus
    failure_code: str | None = Field(default=None, max_length=128)
    diagnosis: RCA100InitialDiagnosis | None = None
    diagnosis_metadata: RuntimeDiagnosisMetadata | None = None
    semantic_model_operations: StrictInt = Field(ge=0, le=1)
    specialist_calls: Literal[0] = 0
    fusion_model_calls: Literal[0] = 0
    provider_attempts: StrictInt = Field(ge=0, le=2)
    transport_retries: StrictInt = Field(ge=0, le=1)
    known_token_lower_bound: StrictInt = Field(ge=0)
    conservative_token_upper_bound: StrictInt = Field(ge=0)
    input_tokens_if_known: StrictInt | None = Field(default=None, ge=0)
    output_tokens_if_known: StrictInt | None = Field(default=None, ge=0)
    request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    latency_seconds: StrictFloat = Field(ge=0)
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime

    @model_validator(mode="after")
    def require_terminal_consistency(self) -> LiveTerminalRecord:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("live terminal ended before it started")
        if self.transport_retries > self.provider_attempts:
            raise ValueError("live terminal retry accounting is inconsistent")
        completed = self.status is LiveTerminalStatus.COMPLETED
        if completed:
            if (
                self.diagnosis is None
                or self.diagnosis_metadata is None
                or self.failure_code is not None
                or self.semantic_model_operations != 1
                or self.provider_attempts < 1
                or self.request_sha256 is None
            ):
                raise ValueError("completed live terminal lacks execution evidence")
        else:
            if self.diagnosis is not None or self.diagnosis_metadata is not None:
                raise ValueError("failed live terminal contains diagnosis")
            if self.failure_code is None:
                raise ValueError("failed live terminal lacks a failure code")
        if (self.input_tokens_if_known is None) != (
            self.output_tokens_if_known is None
        ):
            raise ValueError("live terminal token dimensions are incomplete")
        if self.status is LiveTerminalStatus.NOT_ADMITTED and any(
            (
                self.semantic_model_operations,
                self.provider_attempts,
                self.transport_retries,
                self.known_token_lower_bound,
                self.conservative_token_upper_bound,
            )
        ):
            raise ValueError("not-admitted terminal contains execution accounting")
        return self


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_create_once(path: Path, value: V2Model) -> None:
    payload = _canonical_bytes(value.model_dump(mode="json"))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("create-once live runtime artifact differs")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _failure(error: Exception) -> tuple[LiveTerminalStatus, str]:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    chain: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    http_error = next(
        (item for item in chain if isinstance(item, urllib.error.HTTPError)), None
    )
    if isinstance(http_error, urllib.error.HTTPError):
        if http_error.code == 429:
            return LiveTerminalStatus.PROVIDER_FAILURE, "HTTP_429"
        if 400 <= http_error.code <= 499:
            return LiveTerminalStatus.PROTOCOL_VIOLATION, "HTTP_4XX_NON_429"
        if 500 <= http_error.code <= 599:
            return LiveTerminalStatus.PROVIDER_FAILURE, "HTTP_5XX"
        return (
            LiveTerminalStatus.PROTOCOL_VIOLATION,
            "HTTP_OTHER",
        )
    if any(isinstance(item, TimeoutError) for item in chain):
        return LiveTerminalStatus.TIMEOUT, "PROVIDER_TIMEOUT"
    if any(isinstance(item, ProviderProtocolError) for item in chain):
        return LiveTerminalStatus.PROTOCOL_VIOLATION, "PROVIDER_PROTOCOL_VIOLATION"
    if any(isinstance(item, (ConnectionError, OSError)) for item in chain):
        return LiveTerminalStatus.PROVIDER_FAILURE, "PROVIDER_TRANSPORT_FAILURE"
    return LiveTerminalStatus.INVALID_SCHEMA, "PROVIDER_OUTPUT_INVALID_SCHEMA"


def _known_usage(run_root: Path) -> tuple[int, int] | None:
    records = tuple(
        ProviderAttemptRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((run_root / "provider-attempts").glob("*.json"))
    )
    if not records:
        return None
    prompt_tokens = 0
    completion_tokens = 0
    for record in records:
        usage = record.usage_tokens_if_known
        if usage is None:
            return None
        prompt_tokens += int(usage.prompt_tokens)
        completion_tokens += int(usage.completion_tokens)
    return prompt_tokens, completion_tokens


def _wire_request_sha256(run_root: Path) -> str | None:
    attempts = tuple(
        ProviderAttemptRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((run_root / "provider-attempts").glob("*.json"))
    )
    if not attempts:
        return None
    hashes = {item.request_sha256 for item in attempts}
    if len(hashes) != 1:
        raise ValueError("live Provider retry request identity differs")
    return next(iter(hashes))


def _attempt_for(
    record: ScheduledArm,
    *,
    schedule_sha256: str,
    implementation_lock_sha256: str,
    started_at: datetime,
) -> LiveRunAttempt:
    return LiveRunAttempt(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        split=record.split,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        started_at_utc=started_at,
    )


def _diagnosis_metadata(
    diagnosis: RCA100InitialDiagnosis,
    *,
    base: LiveBaseContext,
    arm: Arm,
    hierarchy: HierarchicalContext | None,
) -> RuntimeDiagnosisMetadata:
    root = diagnosis.root_cause_entity_ref
    base_entities = {item.entity_ref: item for item in base.entities}
    base_entity = base_entities.get(root)
    visible_sources: set[EvidenceSource] = {
        item.source for item in base.evidence if item.entity_ref == root
    }
    if base.alert_entity_ref == root:
        visible_sources.add("ALERTS")
    if arm is Arm.H1:
        if hierarchy is None:
            raise ValueError("H1 diagnosis metadata lacks hierarchical context")
        card = next(
            (item for item in hierarchy.entity_cards if item.entity_ref == root),
            None,
        )
        if card is None:
            raise ValueError("H1 diagnosis metadata root is absent from entity cards")
        layer = card.layer
        service_ancestor = card.service_ancestor_or_none
        visible_sources.update(card.visible_sources)
    else:
        if base_entity is None:
            raise ValueError("B0 diagnosis metadata root is absent from base context")
        layer = base_entity.layer
        service_ancestor = base_entity.service_ancestor_or_none
    source_order = {name: index for index, name in enumerate(("METRICS", "LOGS", "TRACES", "EVENTS", "ALERTS"))}
    return RuntimeDiagnosisMetadata(
        entity_layer=layer,
        service_ancestor_or_none=service_ancestor,
        root_provenance=(
            "MODEL_STRONG_SINGLE_B0" if arm is Arm.B0 else "MODEL_STRONG_SINGLE_H1"
        ),
        fault_ontology_class=classify_live_fault_ontology(diagnosis.fault_type),
        visibility_summary=RootVisibilitySummary(
            visible_sources=tuple(sorted(visible_sources, key=source_order.__getitem__)),
            direct_evidence_count=sum(
                item.entity_ref == root for item in base.evidence
            ),
            alert_entity=base.alert_entity_ref == root,
        ),
    )


def _record_paths(
    *, journal_root: Path, output_root: Path, record: ScheduledArm
) -> tuple[Path, Path, Path]:
    return (
        journal_root / "run-attempts" / f"{record.run_id}.json",
        journal_root / "runs" / record.run_id,
        output_root / "terminals" / f"{record.run_id}.json",
    )


def validate_terminal_binding(
    record: ScheduledArm,
    terminal: LiveTerminalRecord,
    attempt: LiveRunAttempt | None,
    *,
    schedule_sha256: str,
    implementation_lock_sha256: str,
) -> None:
    expected = (
        record.run_id,
        record.opaque_case_id,
        record.split,
        record.pair_position,
        record.arm_position,
        record.arm,
        schedule_sha256,
        implementation_lock_sha256,
    )
    terminal_observed = (
        terminal.run_id,
        terminal.opaque_case_id,
        terminal.split,
        terminal.pair_position,
        terminal.arm_position,
        terminal.arm,
        terminal.schedule_sha256,
        terminal.implementation_lock_sha256,
    )
    if terminal_observed != expected:
        raise ValueError("live terminal schedule/implementation binding differs")
    if attempt is None and terminal.status is LiveTerminalStatus.NOT_ADMITTED:
        return
    if attempt is None:
        raise ValueError("live terminal run-attempt binding is missing")
    attempt_observed = (
        attempt.run_id,
        attempt.opaque_case_id,
        attempt.split,
        attempt.pair_position,
        attempt.arm_position,
        attempt.arm,
        attempt.schedule_sha256,
        attempt.implementation_lock_sha256,
    )
    if attempt_observed != expected or attempt.started_at_utc != terminal.started_at_utc:
        raise ValueError("live run-attempt schedule/implementation binding differs")


def seal_interrupted_live_arm(
    record: ScheduledArm,
    *,
    journal_root: Path,
    output_root: Path,
    schedule_sha256: str,
    implementation_lock_sha256: str,
    timeout_seconds: float,
    max_completion_tokens: int,
    prompt_token_reservation: int,
    retry_policy_sha256: str,
) -> LiveTerminalRecord:
    """Seal one durable attempt without constructing or calling a Provider."""

    attempt_path, run_root, terminal_path = _record_paths(
        journal_root=journal_root,
        output_root=output_root,
        record=record,
    )
    if not attempt_path.exists():
        raise ValueError("interrupted live arm lacks a durable run-attempt")
    attempt = LiveRunAttempt.model_validate_json(
        attempt_path.read_text(encoding="utf-8")
    )
    if terminal_path.exists():
        terminal = LiveTerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        validate_terminal_binding(
            record,
            terminal,
            attempt,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
        )
        return terminal
    expected_attempt = _attempt_for(
        record,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        started_at=attempt.started_at_utc,
    )
    if attempt != expected_attempt:
        raise ValueError("live run-attempt schedule/implementation binding differs")
    semantic_model_operations = 0
    if run_root.exists():
        seal_interrupted_provider_sidecar(
            run_root,
            policy_lock_sha256=retry_policy_sha256,
            expected_timeout_seconds=timeout_seconds,
            fallback_operation_type=None,
        )
        semantic_model_operations = len(
            tuple((run_root / "semantic-operations").glob("*.json"))
        )
        if semantic_model_operations != 1:
            raise ValueError("interrupted Provider sidecar semantic count differs")
    accounting = rebuild_attempt_accounting(
        (run_root,) if run_root.exists() else (),
        prompt_token_reservation=prompt_token_reservation,
        max_completion_tokens=max_completion_tokens,
    )
    terminal = LiveTerminalRecord(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        split=record.split,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        status=LiveTerminalStatus.INTERRUPTED,
        failure_code="INTERRUPTED_NO_REISSUE",
        semantic_model_operations=semantic_model_operations,
        provider_attempts=accounting.provider_attempt_count,
        transport_retries=accounting.retry_attempt_count,
        known_token_lower_bound=accounting.known_token_lower_bound,
        conservative_token_upper_bound=accounting.conservative_token_upper_bound,
        request_sha256=(
            None if not run_root.exists() else _wire_request_sha256(run_root)
        ),
        latency_seconds=max(
            0.0,
            (datetime.now(timezone.utc) - attempt.started_at_utc).total_seconds(),
        ),
        started_at_utc=attempt.started_at_utc,
        ended_at_utc=datetime.now(timezone.utc),
    )
    _write_create_once(terminal_path, terminal)
    return terminal


def execute_live_arm(
    record: ScheduledArm,
    *,
    base: LiveBaseContext,
    hierarchy: HierarchicalContext | None,
    journal_root: Path,
    output_root: Path,
    schedule_sha256: str,
    implementation_lock_sha256: str,
    provider_config: OpenAICompatibleConfig,
    expected_model: str,
    timeout_seconds: float,
    max_completion_tokens: int,
    prompt_token_reservation: int,
    pacer: RequestPacer,
    budget: AttemptBudget,
    retry_policy_sha256: str,
    base_transport: OpenAICompatibleTransport | None = None,
) -> LiveTerminalRecord:
    """Execute or recover one immutable arm without ever reissuing an orphan."""

    attempt_path, run_root, terminal_path = _record_paths(
        journal_root=journal_root,
        output_root=output_root,
        record=record,
    )
    if terminal_path.exists():
        terminal = LiveTerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        attempt = (
            None
            if not attempt_path.exists()
            else LiveRunAttempt.model_validate_json(
                attempt_path.read_text(encoding="utf-8")
            )
        )
        validate_terminal_binding(
            record,
            terminal,
            attempt,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
        )
        return terminal
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    if attempt_path.exists():
        return seal_interrupted_live_arm(
            record,
            journal_root=journal_root,
            output_root=output_root,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
            timeout_seconds=timeout_seconds,
            max_completion_tokens=max_completion_tokens,
            prompt_token_reservation=prompt_token_reservation,
            retry_policy_sha256=retry_policy_sha256,
        )
    _write_create_once(
        attempt_path,
        _attempt_for(
            record,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
            started_at=started_at,
        ),
    )
    retry_transport = Dev3RetryingTransport(
        PacedTransport(base_transport or StdlibOpenAICompatibleTransport(), pacer),
        run_root=run_root,
        budget=budget,
        policy_lock_sha256=retry_policy_sha256,
        expected_timeout_seconds=timeout_seconds,
    )
    provider = OpenAICompatibleLiveComparisonProvider(
        config=provider_config,
        expected_model=expected_model,
        timeout_seconds=timeout_seconds,
        max_completion_tokens=max_completion_tokens,
        transport=retry_transport,
    )
    provider_proxy = Dev3ProviderProxy(
        provider,
        run_root=run_root,
        policy_lock_sha256=retry_policy_sha256,
    )
    status = LiveTerminalStatus.COMPLETED
    failure_code: str | None = None
    diagnosis: RCA100InitialDiagnosis | None = None
    diagnosis_metadata: RuntimeDiagnosisMetadata | None = None
    try:
        diagnosis = execute_arm(
            base=base,
            arm=record.arm,
            hierarchy=hierarchy,
            provider=provider_proxy,  # type: ignore[arg-type]
        ).diagnosis
        diagnosis_metadata = _diagnosis_metadata(
            diagnosis,
            base=base,
            arm=record.arm,
            hierarchy=hierarchy,
        )
    except Exception as error:
        status, failure_code = _failure(error)
        diagnosis = None
        diagnosis_metadata = None
    accounting = rebuild_attempt_accounting(
        (run_root,),
        prompt_token_reservation=prompt_token_reservation,
        max_completion_tokens=max_completion_tokens,
    )
    attempts = tuple(
        ProviderAttemptRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted((run_root / "provider-attempts").glob("*.json"))
    )
    if status is not LiveTerminalStatus.COMPLETED:
        safe_http_classes = {
            item.safe_http_status_class for item in attempts
        }
        if "HTTP_429" in safe_http_classes:
            status = LiveTerminalStatus.PROVIDER_FAILURE
            failure_code = "HTTP_429"
        elif "HTTP_4XX_NON_429" in safe_http_classes:
            status = LiveTerminalStatus.PROTOCOL_VIOLATION
            failure_code = "HTTP_4XX_NON_429"
        elif "HTTP_5XX" in safe_http_classes:
            status = LiveTerminalStatus.PROVIDER_FAILURE
            failure_code = "HTTP_5XX"
    usage = _known_usage(run_root)
    terminal = LiveTerminalRecord(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        split=record.split,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        status=status,
        failure_code=failure_code,
        diagnosis=diagnosis,
        diagnosis_metadata=diagnosis_metadata,
        semantic_model_operations=provider.calls,
        provider_attempts=accounting.provider_attempt_count,
        transport_retries=accounting.retry_attempt_count,
        known_token_lower_bound=accounting.known_token_lower_bound,
        conservative_token_upper_bound=accounting.conservative_token_upper_bound,
        input_tokens_if_known=None if usage is None else usage[0],
        output_tokens_if_known=None if usage is None else usage[1],
        request_sha256=_wire_request_sha256(run_root),
        latency_seconds=time.monotonic() - started_monotonic,
        started_at_utc=started_at,
        ended_at_utc=datetime.now(timezone.utc),
    )
    _write_create_once(terminal_path, terminal)
    return terminal


def terminalize_local_failure(
    record: ScheduledArm,
    *,
    status: Literal["INPUT_PROJECTION_FAILURE", "PRIVACY_FAILURE"],
    journal_root: Path,
    output_root: Path,
    schedule_sha256: str,
    implementation_lock_sha256: str,
) -> LiveTerminalRecord:
    attempt_path, _run_root, terminal_path = _record_paths(
        journal_root=journal_root,
        output_root=output_root,
        record=record,
    )
    if terminal_path.exists():
        terminal = LiveTerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        attempt = (
            None
            if not attempt_path.exists()
            else LiveRunAttempt.model_validate_json(
                attempt_path.read_text(encoding="utf-8")
            )
        )
        validate_terminal_binding(
            record,
            terminal,
            attempt,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
        )
        return terminal
    now = datetime.now(timezone.utc)
    _write_create_once(
        attempt_path,
        _attempt_for(
            record,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
            started_at=now,
        ),
    )
    terminal = LiveTerminalRecord(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        split=record.split,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        status=LiveTerminalStatus(status),
        failure_code=status,
        semantic_model_operations=0,
        provider_attempts=0,
        transport_retries=0,
        known_token_lower_bound=0,
        conservative_token_upper_bound=0,
        latency_seconds=0.0,
        started_at_utc=now,
        ended_at_utc=now,
    )
    _write_create_once(terminal_path, terminal)
    return terminal


def terminalize_not_admitted(
    record: ScheduledArm,
    *,
    output_root: Path,
    schedule_sha256: str,
    implementation_lock_sha256: str,
) -> LiveTerminalRecord:
    if record.split != "REGRESSION":
        raise ValueError("not-admitted disposition is Regression-only")
    now = datetime.now(timezone.utc)
    terminal_path = output_root / "terminals" / f"{record.run_id}.json"
    if terminal_path.exists():
        terminal = LiveTerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        validate_terminal_binding(
            record,
            terminal,
            None,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
        )
        return terminal
    terminal = LiveTerminalRecord(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        split=record.split,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        status=LiveTerminalStatus.NOT_ADMITTED,
        failure_code="NOT_ADMITTED_AFTER_HTTP429",
        semantic_model_operations=0,
        provider_attempts=0,
        transport_retries=0,
        known_token_lower_bound=0,
        conservative_token_upper_bound=0,
        latency_seconds=0.0,
        started_at_utc=now,
        ended_at_utc=now,
    )
    _write_create_once(terminal_path, terminal)
    return terminal


def terminal_status_counts(
    terminals: tuple[LiveTerminalRecord, ...],
) -> Mapping[str, int]:
    return {
        status.value: sum(item.status is status for item in terminals)
        for status in LiveTerminalStatus
    }


__all__ = [
    "LiveRunAttempt",
    "LiveTerminalRecord",
    "LiveTerminalStatus",
    "RootVisibilitySummary",
    "RuntimeDiagnosisMetadata",
    "execute_live_arm",
    "seal_interrupted_live_arm",
    "terminal_status_counts",
    "terminalize_local_failure",
    "terminalize_not_admitted",
    "validate_terminal_binding",
]
