"""Paired schedule and create-once runtime for B0 versus compact C1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import random
import time
from typing import Literal, cast
import urllib.error

from pydantic import AwareDatetime, Field, StrictFloat, StrictInt, model_validator

from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    ProviderProtocolError,
    StdlibOpenAICompatibleTransport,
)
from ecomsre_rca100.contracts import RCA100InitialDiagnosis
from ecomsre_rcaeval.provider import ProviderDiagnosisError
from ecomsre_rcaeval_adaptive.v2_runner import PacedTransport, RequestPacer
from ecomsre_rcaeval_v2.contracts import V2Model
from ecomsre_rcaeval_v2.dev3_provider import Dev3RetryingTransport
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    ProviderAttemptRecord,
    ProviderAttemptStart,
    rebuild_attempt_accounting,
)
from ecomsre_rca_unified.compact_contracts import (
    AllocationBucket,
    CompactBaseContext,
    CompactCandidateContext,
    ResolvedCompactDiagnosis,
    RetrievalReason,
)
from ecomsre_rca_unified.compact_prompt import OpenAICompatibleCompactProvider
from ecomsre_rca_unified.contracts import CanonicalEntityLayer


EVALUATION_VERSION = "compact-evidence-retrieval-strong-single-v1"
SCHEDULE_SEED = 20260814


class Arm(str, Enum):
    B0 = "B0"
    C1 = "C1"


@dataclass(frozen=True, slots=True)
class CaseRef:
    source: Literal["RCA100", "OBSS"]
    source_key: str

    def __post_init__(self) -> None:
        if not self.source_key:
            raise ValueError("compact schedule case key is empty")


@dataclass(frozen=True, slots=True)
class ScheduledArm:
    split: Literal["TUNE", "PREFLIGHT"]
    pair_position: int
    arm_position: int
    opaque_case_id: str
    source: Literal["RCA100", "OBSS"]
    source_key: str
    arm: Arm
    run_id: str


def paired_schedule(
    cases: tuple[CaseRef, ...], *, seed: int = SCHEDULE_SEED
) -> tuple[ScheduledArm, ...]:
    if not cases or len(set(cases)) != len(cases):
        raise ValueError("compact paired schedule cases must be unique and nonempty")
    shuffled = list(cases)
    random.Random(seed).shuffle(shuffled)
    output: list[ScheduledArm] = []
    for pair_position, case in enumerate(shuffled, start=1):
        opaque_case_id = (
            "case-"
            + hashlib.sha256(
                b"\0".join(
                    (
                        EVALUATION_VERSION.encode(),
                        b"TUNE",
                        str(seed).encode(),
                        case.source.encode(),
                        case.source_key.encode(),
                    )
                )
            ).hexdigest()[:20]
        )
        arms = (Arm.B0, Arm.C1) if pair_position % 2 else (Arm.C1, Arm.B0)
        for arm_position, arm in enumerate(arms, start=1):
            run_id = hashlib.sha256(
                b"\0".join(
                    (
                        EVALUATION_VERSION.encode(),
                        b"TUNE",
                        opaque_case_id.encode(),
                        arm.value.encode(),
                    )
                )
            ).hexdigest()[:32]
            output.append(
                ScheduledArm(
                    split="TUNE",
                    pair_position=pair_position,
                    arm_position=arm_position,
                    opaque_case_id=opaque_case_id,
                    source=case.source,
                    source_key=case.source_key,
                    arm=arm,
                    run_id=run_id,
                )
            )
    return tuple(output)


class CompactTerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    TIMEOUT = "TIMEOUT"
    INPUT_PROJECTION_FAILURE = "INPUT_PROJECTION_FAILURE"
    PRIVACY_FAILURE = "PRIVACY_FAILURE"
    INTERRUPTED = "INTERRUPTED"


class CompactRunAttempt(V2Model):
    schema_version: Literal["compact-retrieval.run-attempt.v1"] = (
        "compact-retrieval.run-attempt.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    opaque_case_id: str = Field(pattern=r"^case-[0-9a-f]{20}$")
    pair_position: StrictInt = Field(ge=1)
    arm_position: StrictInt = Field(ge=1, le=2)
    arm: Arm
    schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at_utc: AwareDatetime


class CompactDiagnosisRecord(V2Model):
    root_cause_entity_ref: str = Field(min_length=5, max_length=768)
    entity_layer: CanonicalEntityLayer
    service_ancestor_or_none: str | None = Field(default=None, max_length=768)
    root_provenance: Literal["MODEL_STRONG_SINGLE_B0", "MODEL_COMPACT_C1"]
    fault_type: str = Field(min_length=1, max_length=256)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=18)
    summary: str = Field(min_length=1, max_length=2_000)
    root_candidate_id: str | None = Field(default=None, pattern=r"^C(?:0[1-9]|1[0-2])$")
    selected_candidate_rank: StrictInt | None = Field(default=None, ge=1, le=12)
    selected_allocation_bucket: AllocationBucket | None = None
    selected_retrieval_reasons: tuple[RetrievalReason, ...] = ()

    @model_validator(mode="after")
    def require_arm_metadata(self) -> CompactDiagnosisRecord:
        compact_values = (
            self.root_candidate_id,
            self.selected_candidate_rank,
            self.selected_allocation_bucket,
        )
        if self.root_provenance == "MODEL_COMPACT_C1":
            if (
                any(item is None for item in compact_values)
                or not self.selected_retrieval_reasons
            ):
                raise ValueError("C1 diagnosis lacks candidate metadata")
        elif (
            any(item is not None for item in compact_values)
            or self.selected_retrieval_reasons
        ):
            raise ValueError("B0 diagnosis contains compact candidate metadata")
        return self


class CompactTerminalRecord(V2Model):
    schema_version: Literal["compact-retrieval.terminal.v1"] = (
        "compact-retrieval.terminal.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    opaque_case_id: str = Field(pattern=r"^case-[0-9a-f]{20}$")
    pair_position: StrictInt = Field(ge=1)
    arm_position: StrictInt = Field(ge=1, le=2)
    arm: Arm
    schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: CompactTerminalStatus
    failure_code: str | None = Field(default=None, max_length=128)
    diagnosis: CompactDiagnosisRecord | None = None
    semantic_model_operations: Literal[0, 1]
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
    def require_terminal_consistency(self) -> CompactTerminalRecord:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("compact terminal ended before it started")
        completed = self.status is CompactTerminalStatus.COMPLETED
        if completed:
            if (
                self.diagnosis is None
                or self.failure_code is not None
                or self.semantic_model_operations != 1
                or self.provider_attempts < 1
                or self.request_sha256 is None
            ):
                raise ValueError("completed compact terminal lacks execution evidence")
        elif self.diagnosis is not None or self.failure_code is None:
            raise ValueError("failed compact terminal is inconsistent")
        if self.transport_retries > self.provider_attempts:
            raise ValueError("compact retry accounting is inconsistent")
        if (self.input_tokens_if_known is None) != (
            self.output_tokens_if_known is None
        ):
            raise ValueError("compact terminal token dimensions are incomplete")
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


def write_create_once(path: Path, value: V2Model | Mapping[str, object]) -> str:
    payload_value = (
        value.model_dump(mode="json") if isinstance(value, V2Model) else value
    )
    payload = _canonical_bytes(payload_value)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("create-once compact artifact differs")
        return hashlib.sha256(payload).hexdigest()
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def schedule_payload(records: tuple[ScheduledArm, ...]) -> dict[str, object]:
    return {
        "schema_version": "compact-retrieval.private-schedule.v1",
        "evaluation_version": EVALUATION_VERSION,
        "seed": SCHEDULE_SEED,
        "records": [
            {
                "split": item.split,
                "pair_position": item.pair_position,
                "arm_position": item.arm_position,
                "opaque_case_id": item.opaque_case_id,
                "source": item.source,
                "source_key": item.source_key,
                "arm": item.arm.value,
                "run_id": item.run_id,
            }
            for item in records
        ],
    }


def _failure(error: Exception) -> tuple[CompactTerminalStatus, str]:
    chain: list[BaseException] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
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
            return CompactTerminalStatus.PROVIDER_FAILURE, "HTTP_429"
        if 400 <= http_error.code <= 499:
            return CompactTerminalStatus.PROTOCOL_VIOLATION, "HTTP_4XX_NON_429"
        return CompactTerminalStatus.PROVIDER_FAILURE, "HTTP_5XX"
    if isinstance(error, ProviderDiagnosisError):
        return CompactTerminalStatus.INVALID_SCHEMA, "INVALID_SCHEMA"
    if isinstance(error, ProviderProtocolError):
        return CompactTerminalStatus.PROTOCOL_VIOLATION, "PROVIDER_PROTOCOL_VIOLATION"
    if isinstance(error, (TimeoutError, urllib.error.URLError)):
        return CompactTerminalStatus.TIMEOUT, "PROVIDER_TIMEOUT"
    return CompactTerminalStatus.PROTOCOL_VIOLATION, "LOCAL_RUNTIME_CONTRACT_FAILURE"


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
        prompt_tokens += usage.prompt_tokens
        completion_tokens += usage.completion_tokens
    return prompt_tokens, completion_tokens


def _diagnosis_record(
    value: RCA100InitialDiagnosis | ResolvedCompactDiagnosis,
    *,
    arm: Arm,
    base: CompactBaseContext,
    candidates: CompactCandidateContext | None,
) -> CompactDiagnosisRecord:
    if arm is Arm.B0:
        if not isinstance(value, RCA100InitialDiagnosis):
            raise TypeError("B0 Provider returned a compact selection")
        entity = next(
            item
            for item in base.entities
            if item.entity_ref == value.root_cause_entity_ref
        )
        return CompactDiagnosisRecord(
            root_cause_entity_ref=value.root_cause_entity_ref,
            entity_layer=entity.layer,
            service_ancestor_or_none=entity.service_ancestor_or_none,
            root_provenance="MODEL_STRONG_SINGLE_B0",
            fault_type=value.fault_type,
            confidence=value.confidence,
            evidence_refs=value.evidence_refs,
            summary=value.summary,
        )
    if not isinstance(value, ResolvedCompactDiagnosis) or candidates is None:
        raise TypeError("C1 Provider returned a baseline diagnosis")
    card = next(
        item
        for item in candidates.candidates
        if item.candidate_id == value.root_candidate_id
    )
    return CompactDiagnosisRecord(
        root_cause_entity_ref=value.root_cause_entity_ref,
        entity_layer=card.entity_layer,
        service_ancestor_or_none=card.service_ancestor_or_none,
        root_provenance="MODEL_COMPACT_C1",
        fault_type=value.fault_type,
        confidence=value.confidence,
        evidence_refs=value.evidence_refs,
        summary=value.summary,
        root_candidate_id=value.root_candidate_id,
        selected_candidate_rank=value.selected_candidate_rank,
        selected_allocation_bucket=value.selected_allocation_bucket,
        selected_retrieval_reasons=card.retrieval_reasons,
    )


def execute_scheduled_arm(
    record: ScheduledArm,
    *,
    base: CompactBaseContext,
    candidates: CompactCandidateContext | None,
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
) -> CompactTerminalRecord:
    attempt_path = journal_root / "run-attempts" / f"{record.run_id}.json"
    run_root = journal_root / "runs" / record.run_id
    terminal_path = output_root / "terminals" / f"{record.run_id}.json"
    if terminal_path.exists():
        return CompactTerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
    now = datetime.now(timezone.utc)
    if attempt_path.exists():
        starts = tuple(
            ProviderAttemptStart.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted((run_root / "provider-attempt-starts").glob("*.json"))
        )
        finals = tuple((run_root / "provider-attempts").glob("*.json"))
        retries = sum(item.retry_number == 1 for item in starts)
        interrupted = CompactTerminalRecord(
            run_id=record.run_id,
            opaque_case_id=record.opaque_case_id,
            pair_position=record.pair_position,
            arm_position=record.arm_position,
            arm=record.arm,
            schedule_sha256=schedule_sha256,
            implementation_lock_sha256=implementation_lock_sha256,
            status=CompactTerminalStatus.INTERRUPTED,
            failure_code="INTERRUPTED_NO_REISSUE",
            semantic_model_operations=1 if starts else 0,
            provider_attempts=len(starts),
            transport_retries=retries,
            known_token_lower_bound=0,
            conservative_token_upper_bound=(
                len(starts) * (prompt_token_reservation + max_completion_tokens)
            ),
            latency_seconds=0.0,
            started_at_utc=now,
            ended_at_utc=now,
        )
        if len(finals) > len(starts):
            raise ValueError("interrupted compact attempt accounting is invalid")
        write_create_once(terminal_path, interrupted)
        return interrupted
    attempt = CompactRunAttempt(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        started_at_utc=now,
    )
    write_create_once(attempt_path, attempt)
    started_monotonic = time.monotonic()
    retry_transport = Dev3RetryingTransport(
        PacedTransport(base_transport or StdlibOpenAICompatibleTransport(), pacer),
        run_root=run_root,
        budget=budget,
        policy_lock_sha256=retry_policy_sha256,
        expected_timeout_seconds=timeout_seconds,
    )
    provider = OpenAICompatibleCompactProvider(
        config=provider_config,
        expected_model=expected_model,
        timeout_seconds=timeout_seconds,
        max_completion_tokens=max_completion_tokens,
        transport=retry_transport,
    )
    status = CompactTerminalStatus.COMPLETED
    failure_code: str | None = None
    diagnosis: CompactDiagnosisRecord | None = None
    try:
        raw = provider.diagnose(
            base=base,
            arm=record.arm.value,
            candidates=candidates,
        )
        if provider.calls != 1:
            raise ValueError("compact arm did not execute exactly one model call")
        diagnosis = _diagnosis_record(
            raw, arm=record.arm, base=base, candidates=candidates
        )
    except Exception as error:
        status, failure_code = _failure(error)
    accounting = rebuild_attempt_accounting(
        (run_root,),
        prompt_token_reservation=prompt_token_reservation,
        max_completion_tokens=max_completion_tokens,
    )
    usage = _known_usage(run_root)
    terminal = CompactTerminalRecord(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256=schedule_sha256,
        implementation_lock_sha256=implementation_lock_sha256,
        status=status,
        failure_code=failure_code,
        diagnosis=diagnosis,
        semantic_model_operations=cast(Literal[0, 1], provider.calls),
        provider_attempts=accounting.provider_attempt_count,
        transport_retries=accounting.retry_attempt_count,
        known_token_lower_bound=accounting.known_token_lower_bound,
        conservative_token_upper_bound=accounting.conservative_token_upper_bound,
        input_tokens_if_known=None if usage is None else usage[0],
        output_tokens_if_known=None if usage is None else usage[1],
        request_sha256=provider.last_request_sha256,
        latency_seconds=time.monotonic() - started_monotonic,
        started_at_utc=now,
        ended_at_utc=datetime.now(timezone.utc),
    )
    write_create_once(terminal_path, terminal)
    return terminal


def terminal_status_counts(
    terminals: tuple[CompactTerminalRecord, ...],
) -> Mapping[str, int]:
    return {
        status.value: sum(item.status is status for item in terminals)
        for status in CompactTerminalStatus
    }


__all__ = [
    "Arm",
    "CaseRef",
    "CompactTerminalRecord",
    "CompactTerminalStatus",
    "EVALUATION_VERSION",
    "SCHEDULE_SEED",
    "ScheduledArm",
    "execute_scheduled_arm",
    "paired_schedule",
    "schedule_payload",
    "terminal_status_counts",
    "write_create_once",
]
