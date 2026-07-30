"""Pydantic schemas for the Phase 0 evidence bundle."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.phase0.models import MeasurementPhase, Outcome


RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
REDACTED = "[REDACTED]"
_SECRET_NAMES = {
    "api-key",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "password",
    "private-key",
    "secret",
    "token",
}
_URI_CANDIDATE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^\s\"'<>]+)")
_BEARER_VALUE = re.compile(r"(?i)^(bearer)\s+(.+)$")
_MAX_REDACTION_ARGUMENT_CHARS = 64 * 1024
_MAX_JSON_REDACTION_DEPTH = 64
_MAX_JSON_REDACTION_NODES = 10_000


class _RedactionLimitExceeded(RuntimeError):
    pass


class FrozenDict(dict[str, str]):
    """A JSON-serializable mapping that cannot be mutated in place."""

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("content hash mapping is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def new_run_id() -> str:
    """Return a collision-resistant, opaque, path-safe run identifier."""
    return uuid.uuid4().hex


class EvidenceModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def require_utc_timestamps(self) -> "EvidenceModel":
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if isinstance(value, datetime) and (
                value.utcoffset() is None or value.utcoffset().total_seconds() != 0
            ):
                raise ValueError(f"{field_name} must be a UTC timestamp")
        return self


class CanonicalState(str, Enum):
    CANONICAL = "CANONICAL"
    NON_CANONICAL = "NON_CANONICAL"


class RunManifest(EvidenceModel):
    schema_version: Literal["phase0.run-manifest.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    scenario_instance_ref: str = Field(pattern=RUN_ID_PATTERN.pattern)
    canonical_state: CanonicalState
    started_at: datetime
    ended_at: datetime
    final_outcome: Outcome
    exit_code: int

    @model_validator(mode="after")
    def require_consistent_terminal_state(self) -> "RunManifest":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at precedes started_at")
        if self.exit_code != self.final_outcome.exit_code:
            raise ValueError(f"{self.final_outcome.value} exit code is inconsistent")
        if self.final_outcome is Outcome.INVALID_INVOCATION:
            raise ValueError("INVALID_INVOCATION does not produce a run manifest")
        if (
            self.final_outcome is Outcome.SUCCESS
            and self.canonical_state is not CanonicalState.CANONICAL
        ):
            raise ValueError("SUCCESS requires a canonical run manifest")
        return self


class MachineManifest(EvidenceModel):
    schema_version: Literal["phase0.machine-manifest.v1"]
    macos_version: str
    macos_build: str
    host_architecture: str
    cpu_model: str
    cpu_count: int = Field(ge=1)
    total_memory_bytes: int = Field(ge=0)
    available_memory_bytes: int = Field(ge=0)
    available_disk_bytes: int = Field(ge=0)
    docker_client_version: str
    docker_server_version: str
    docker_desktop_version: str
    docker_engine: str
    compose_version: str
    docker_cpu_count: float = Field(ge=0)
    docker_memory_bytes: int = Field(ge=0)
    docker_disk_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def require_consistent_capacity(self) -> "MachineManifest":
        if self.available_memory_bytes > self.total_memory_bytes:
            raise ValueError("available memory exceeds total memory")
        return self


class EnvironmentManifest(EvidenceModel):
    schema_version: Literal["phase0.environment-manifest.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    owned_resources: tuple[dict[str, Any], ...]
    ports: tuple[dict[str, Any], ...]
    startup_state: str
    readiness_state: str
    external_runtime_dependency_observations: tuple[dict[str, Any], ...]
    disposition: str


class FrozenInputs(EvidenceModel):
    schema_version: Literal["phase0.frozen-inputs.v1"]
    upstream_tag: str
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_arm64_digests: dict[str, str]
    compose_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_valid_resolved_digests(self) -> "FrozenInputs":
        if not self.resolved_arm64_digests or any(
            re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            for digest in self.resolved_arm64_digests.values()
        ):
            raise ValueError("resolved arm64 digests must be frozen SHA-256 values")
        return self


class PhaseRecord(EvidenceModel):
    schema_version: Literal["phase0.phase-record.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    scenario_phase: MeasurementPhase
    cycle_number: int = Field(ge=1)
    utc_started_at: datetime
    utc_ended_at: datetime
    monotonic_duration_seconds: float = Field(ge=0)
    probe_fixture_version: str
    query_fixture_version: str
    freshness_start: datetime
    freshness_end: datetime

    @model_validator(mode="after")
    def require_ordered_phase_times(self) -> "PhaseRecord":
        if self.utc_ended_at < self.utc_started_at:
            raise ValueError("phase end precedes phase start")
        if self.freshness_end < self.freshness_start:
            raise ValueError("freshness end precedes freshness start")
        return self


class QueryEvidence(EvidenceModel):
    schema_version: Literal["phase0.query-evidence.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    backend: str
    raw_query: str
    raw_response_artifact: str
    started_at: datetime
    ended_at: datetime
    status_code: int = Field(ge=0, le=599)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_ordered_query_times(self) -> "QueryEvidence":
        if self.ended_at < self.started_at:
            raise ValueError("query end precedes query start")
        return self


class StatisticalEvidence(EvidenceModel):
    schema_version: Literal["phase0.statistical-evidence.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    cycle_number: int = Field(ge=1)
    scenario_phase: MeasurementPhase
    getads_attempts: int = Field(ge=0)
    getads_errors: int = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    wilson_lower: float = Field(ge=0, le=1)
    wilson_upper: float = Field(ge=0, le=1)
    threshold_passed: bool
    sample_timeout: bool

    @model_validator(mode="after")
    def require_consistent_statistics(self) -> "StatisticalEvidence":
        if self.getads_errors > self.getads_attempts:
            raise ValueError("GetAds errors cannot exceed attempts")
        expected_rate = (
            self.getads_errors / self.getads_attempts if self.getads_attempts else 0.0
        )
        if abs(self.error_rate - expected_rate) > 1e-12:
            raise ValueError("error rate conflicts with observed counts")
        if not self.wilson_lower <= self.error_rate <= self.wilson_upper:
            raise ValueError("Wilson interval does not contain error rate")
        if self.sample_timeout and self.threshold_passed:
            raise ValueError("sample timeout cannot pass a threshold")
        return self


class CommandLog(EvidenceModel):
    schema_version: Literal["phase0.command-log.v2"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    command: str
    arguments: tuple[str, ...]
    working_directory: str
    started_at: datetime
    ended_at: datetime
    monotonic_started_seconds: float = Field(ge=0)
    monotonic_ended_seconds: float = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    process_exit_code: int | None
    process_timed_out: bool
    classification: Outcome
    terminal_exit_code: int
    reason_code: str = Field(min_length=1)
    stdout_artifact: str = Field(min_length=1)
    stdout_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stderr_artifact: str = Field(min_length=1)
    stderr_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    network_access_declared: bool
    network_access_scope: Literal[
        "NONE",
        "LOCAL_DOCKER_DAEMON",
        "EXTERNAL_GIT",
        "EXTERNAL_REGISTRY",
    ]
    filesystem_write_scope: tuple[str, ...]
    observed_effect_scope: tuple[str, ...]

    @model_validator(mode="after")
    def reject_unsanitized_secrets(self) -> "CommandLog":
        if _has_unsanitized_secret(self.arguments):
            raise ValueError("command log contains unsanitized secret arguments")
        if self.process_timed_out and self.process_exit_code is not None:
            raise ValueError("timed-out process cannot have an exit code")
        if self.terminal_exit_code != self.classification.exit_code:
            raise ValueError("terminal exit code conflicts with classification")
        if self.ended_at < self.started_at:
            raise ValueError("command ended_at precedes started_at")
        if self.monotonic_ended_seconds < self.monotonic_started_seconds:
            raise ValueError("command monotonic end precedes start")
        return self


class CycleReport(EvidenceModel):
    cycle_number: int = Field(ge=1)
    passed: bool
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def require_cycle_reason_consistency(self) -> "CycleReport":
        if self.passed and self.reason_codes:
            raise ValueError("passing cycle cannot contain failure reasons")
        if not self.passed and not self.reason_codes:
            raise ValueError("failed cycle requires a reason code")
        return self


class FinalReport(EvidenceModel):
    schema_version: Literal["phase0.final-report.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    canonical_state: CanonicalState
    cycle_decisions: tuple[CycleReport, ...]
    telemetry_gate_decisions: dict[str, bool]
    overall_outcome: Outcome
    exit_code: int
    failure_reason_codes: tuple[str, ...]
    environment_disposition: str

    @model_validator(mode="after")
    def require_consistent_acceptance_result(self) -> "FinalReport":
        if self.exit_code != self.overall_outcome.exit_code:
            raise ValueError(f"{self.overall_outcome.value} exit code is inconsistent")
        if self.overall_outcome is Outcome.INVALID_INVOCATION:
            raise ValueError("INVALID_INVOCATION does not produce a final report")

        cycle_numbers = [cycle.cycle_number for cycle in self.cycle_decisions]
        if len(cycle_numbers) != len(set(cycle_numbers)):
            raise ValueError("cycle decisions contain duplicate cycle numbers")

        if self.overall_outcome is Outcome.SUCCESS:
            required_telemetry = {"prometheus", "jaeger", "opensearch"}
            if self.canonical_state is not CanonicalState.CANONICAL:
                raise ValueError("SUCCESS requires canonical evidence")
            if cycle_numbers != [1, 2, 3] or not all(
                cycle.passed for cycle in self.cycle_decisions
            ):
                raise ValueError("SUCCESS requires three passing cycles")
            if not required_telemetry.issubset(
                self.telemetry_gate_decisions
            ) or not all(
                self.telemetry_gate_decisions[name] for name in required_telemetry
            ):
                raise ValueError("SUCCESS requires all telemetry gates")
            if self.failure_reason_codes:
                raise ValueError("SUCCESS cannot contain failure reasons")
            if self.environment_disposition != "STOPPED":
                raise ValueError("SUCCESS requires a stopped environment")
        elif not self.failure_reason_codes:
            raise ValueError("non-success final report requires failure reasons")
        return self


class IntegrityManifest(EvidenceModel):
    schema_version: Literal["phase0.integrity.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    content_hashes: dict[str, str]
    manifest_sha256: str

    @model_validator(mode="after")
    def require_valid_content_hashes(self) -> "IntegrityManifest":
        from ecomsre.evidence.hashes import canonical_json_sha256

        if not self.content_hashes:
            raise ValueError("integrity manifest requires content hashes")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in self.content_hashes.values()
        ):
            raise ValueError("integrity manifest contains an invalid SHA-256")
        if self.manifest_sha256 != canonical_json_sha256(self.content_hashes):
            raise ValueError("integrity manifest hash is inconsistent")
        object.__setattr__(
            self,
            "content_hashes",
            FrozenDict(self.content_hashes),
        )
        return self


class ControlEvent(EvidenceModel):
    schema_version: Literal["phase0.control-event.v1"]
    run_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    event_id: str = Field(pattern=RUN_ID_PATTERN.pattern)
    occurred_at: datetime
    control_action: str = Field(min_length=1)
    feature_flag_key: str = Field(min_length=1)
    feature_flag_value: str = Field(min_length=1)


def _has_unsanitized_secret(arguments: tuple[str, ...]) -> bool:
    if redact_command_arguments(arguments) != arguments:
        return True
    if not arguments:
        return False
    key, separator, _value = arguments[-1].partition("=")
    return not separator and key.startswith("-") and _is_secret_name(key)


def redact_command_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    """Redact credential syntax without changing the argument count."""
    redacted: list[str] = []
    redact_next = False
    for argument in arguments:
        if redact_next:
            redacted.append(REDACTED)
            redact_next = False
            continue
        if len(argument) > _MAX_REDACTION_ARGUMENT_CHARS and argument != REDACTED:
            redacted.append(REDACTED)
            continue

        key, separator, _value = argument.partition("=")
        if _is_secret_name(key) and (bool(separator) or key.startswith("-")):
            if separator:
                redacted.append(f"{key}={REDACTED}")
            else:
                redacted.append(argument)
                redact_next = True
            continue
        redacted.append(_redact_argument_value(argument))
    return tuple(redacted)


def _is_secret_name(value: str) -> bool:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        value.lstrip("-").casefold(),
    ).strip("-")
    components = set(normalized.split("-"))
    return (
        normalized in _SECRET_NAMES
        or "apikey" in normalized
        or normalized
        in {
            "database-url",
            "db-url",
            "dsn",
            "connection-string",
            "connectionstring",
        }
        or bool(
            components
            & {
                "auth",
                "authorization",
                "credential",
                "password",
                "secret",
                "token",
            }
        )
        or {"private", "key"}.issubset(components)
    )


def _redact_argument_value(
    argument: str,
    *,
    allow_json: bool = True,
) -> str:
    if len(argument) > _MAX_REDACTION_ARGUMENT_CHARS:
        return REDACTED
    header_name, colon, _header_value = argument.partition(":")
    if colon and _is_secret_name(header_name):
        return f"{header_name}:{' ' if argument.startswith(header_name + ': ') else ''}{REDACTED}"

    bearer = _BEARER_VALUE.fullmatch(argument.strip())
    if bearer is not None and bearer.group(2) != REDACTED:
        return f"{bearer.group(1)} {REDACTED}"

    uri_redacted = _redact_uri_userinfo(argument)
    connection_redacted = _redact_connection_string(uri_redacted)
    if not allow_json:
        return connection_redacted
    return _redact_embedded_json(connection_redacted)


def _redact_connection_string(argument: str) -> str:
    segments = _split_connection_segments(argument)
    if segments is None:
        if re.search(
            r"(?i)(?:password|pwd|user\s*id|uid|credential|token)\s*=",
            argument,
        ) and re.search(
            r"(?i)(?:server|host|database|data\s*source|driver)\s*=",
            argument,
        ):
            return REDACTED
        return argument
    if len(segments) < 2:
        return argument
    parsed = [segment.partition("=") for segment in segments]
    normalized_keys = {
        re.sub(r"[^a-z0-9]+", "-", key.strip().casefold()).strip("-")
        for key, separator, _value in parsed
        if separator
    }
    context_keys = {
        "server",
        "host",
        "database",
        "data-source",
        "driver",
    }
    secret_keys = {
        "user",
        "user-id",
        "username",
        "uid",
        "password",
        "pwd",
        "credential",
        "token",
    }
    if not normalized_keys & context_keys or not normalized_keys & secret_keys:
        return argument
    redacted: list[str] = []
    for segment, (key, separator, value) in zip(
        segments,
        parsed,
        strict=True,
    ):
        normalized = re.sub(
            r"[^a-z0-9]+",
            "-",
            key.strip().casefold(),
        ).strip("-")
        if separator and normalized in secret_keys and value != REDACTED:
            redacted.append(f"{key}={REDACTED}")
        else:
            redacted.append(segment)
    return ";".join(redacted)


def _split_connection_segments(argument: str) -> list[str] | None:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    in_braces = False
    index = 0
    while index < len(argument):
        character = argument[index]
        if quote is not None:
            current.append(character)
            if character == "\\" and index + 1 < len(argument):
                index += 1
                current.append(argument[index])
            elif character == quote:
                quote = None
        elif in_braces:
            current.append(character)
            if character == "}" and index + 1 < len(argument):
                if argument[index + 1] == "}":
                    index += 1
                    current.append(argument[index])
                else:
                    in_braces = False
            elif character == "}":
                in_braces = False
        elif character in {'"', "'"}:
            quote = character
            current.append(character)
        elif character == "{":
            in_braces = True
            current.append(character)
        elif character == ";":
            segments.append("".join(current))
            current = []
        else:
            current.append(character)
        index += 1
    if quote is not None or in_braces:
        return None
    segments.append("".join(current))
    return segments


def _redact_uri_userinfo(value: str) -> str:
    def redact_match(match: re.Match[str]) -> str:
        scheme = match.group(1)
        remainder = match.group(2)
        authority_end = len(remainder)
        for delimiter in ("/", "?", "#"):
            position = remainder.find(delimiter)
            if position >= 0:
                authority_end = min(authority_end, position)
        authority = remainder[:authority_end]
        tail = remainder[authority_end:]
        decoded_authority = authority
        for _ in range(2):
            decoded = unquote(decoded_authority)
            if decoded == decoded_authority:
                break
            decoded_authority = decoded
        if "@" not in decoded_authority:
            return match.group(0)
        host = decoded_authority.rsplit("@", 1)[1]
        if not host:
            return REDACTED
        return f"{scheme}{REDACTED}@{host}{tail}"

    return _URI_CANDIDATE.sub(redact_match, value)


def _redact_embedded_json(argument: str) -> str:
    stripped = argument.strip()
    if not stripped.startswith(("{", "[", '"')):
        return argument
    try:
        payload = json.loads(argument)
    except RecursionError:
        return REDACTED
    except (json.JSONDecodeError, TypeError):
        return argument

    node_count = 0

    def redact(item: Any, *, depth: int) -> Any:
        nonlocal node_count
        node_count += 1
        if depth > _MAX_JSON_REDACTION_DEPTH or node_count > _MAX_JSON_REDACTION_NODES:
            raise _RedactionLimitExceeded
        if isinstance(item, dict):
            return {
                key: (
                    REDACTED
                    if _is_secret_name(str(key))
                    else redact(value, depth=depth + 1)
                )
                for key, value in item.items()
            }
        if isinstance(item, list):
            return [redact(value, depth=depth + 1) for value in item]
        if isinstance(item, str):
            return _redact_argument_value(item, allow_json=False)
        return item

    try:
        sanitized = redact(payload, depth=0)
    except _RedactionLimitExceeded:
        return REDACTED
    return json.dumps(sanitized, sort_keys=True, separators=(",", ":"))
