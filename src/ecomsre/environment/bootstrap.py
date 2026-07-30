"""Audited ARM64 image-lock bootstrap."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ecomsre.environment.command_runner import (
    RegistryRouteCapability,
    create_registry_route_capability,
)
from ecomsre.environment.lifecycle import (
    ComposeAction,
    build_compose_invocation,
    parse_expected_port_bindings,
)
from ecomsre.environment.manifests import (
    ImageLockManifest,
    ImageLockStatus,
    InspectedImage,
    generate_candidate_image_lock,
    load_image_lock,
    verify_acceptance_image_lock,
    write_candidate_image_lock,
)
from ecomsre.environment.preflight import (
    CommandResult,
    docker_host_prefix,
    parse_cached_images,
    parse_resolved_compose_config,
)
from ecomsre.evidence.hashes import (
    canonical_json_sha256,
    sha256_bytes,
    sha256_file,
)
from ecomsre.evidence.models import CommandLog, redact_command_arguments
from ecomsre.evidence.store import EvaluatorEvidenceStore, ObserverEvidenceStore


class Arm64ManifestUnavailable(ValueError):
    """The frozen image index does not expose one native ARM64 manifest."""


class UpstreamCommandFailed(RuntimeError):
    """A registry command failed before manifest semantics were established."""


class ProxyDiscoveryUnavailable(RuntimeError):
    """The audited macOS proxy discovery command did not complete."""


class ProxyConfigurationUnsafe(ValueError):
    """The enabled macOS proxy configuration is malformed or unsafe."""


_REGISTRY_TOTAL_ATTEMPTS = 3
_REGISTRY_RETRY_DELAYS_SECONDS = (1.0, 2.0)
_MANIFEST_OPERATION_DEADLINE_SECONDS = 130.0
_MANIFEST_ATTEMPT_TIMEOUT_SECONDS = 120.0
_PULL_OPERATION_DEADLINE_SECONDS = 620.0
_PULL_ATTEMPT_TIMEOUT_SECONDS = 600.0
_retry_monotonic = time.monotonic
_retry_sleep = time.sleep
_PERMANENT_REGISTRY_FAILURES = (
    (
        "AUTHORIZATION_OR_AUTHENTICATION_FAILURE",
        re.compile(
            r"\b(?:unauthorized|authentication(?: required)?|denied|forbidden)\b"
        ),
    ),
    (
        "IMAGE_OR_MANIFEST_NOT_FOUND",
        re.compile(
            r"\b(?:manifest unknown|name unknown|not found|tag does not exist)\b"
        ),
    ),
    (
        "PLATFORM_OR_FORMAT_UNSUPPORTED",
        re.compile(r"\b(?:no matching manifest|unsupported)\b"),
    ),
    ("DIGEST_FAILURE", re.compile(r"\bdigest\b")),
    ("X509_FAILURE", re.compile(r"\bx509\b")),
    (
        "PROXY_CONFIGURATION_FAILURE",
        re.compile(r"\bproxy (?:configuration|config)\b"),
    ),
)
_TRANSIENT_REGISTRY_TERMINAL_CAUSES = (
    ("UNEXPECTED_EOF", re.compile(r"(?:^|:\s*)unexpected eof$")),
    ("IO_TIMEOUT", re.compile(r"(?:^|:\s*)i/o timeout$")),
    (
        "CONNECTION_RESET",
        re.compile(r"(?:^|:\s*)connection reset(?: by peer)?$"),
    ),
    (
        "TLS_HANDSHAKE_TIMEOUT",
        re.compile(r"(?:^|:\s*)tls handshake timeout$"),
    ),
    (
        "TEMPORARY_DNS_FAILURE",
        re.compile(r"(?:^|:\s*)temporary failure in name resolution$"),
    ),
)
_DOCKER_REQUEST_EOF = re.compile(
    r'^error: failed to do request: (?:head|get) "[^"]+": eof$'
)
_HTTP_TRANSIENT = (
    (
        "HTTP_429",
        re.compile(
            r"(?:^|:\s*)(?:unexpected status code[: ]+429|"
            r"429 too many requests)$"
        ),
    ),
    (
        "HTTP_500",
        re.compile(
            r"(?:^|:\s*)(?:unexpected status code[: ]+500|"
            r"500 internal server error)$"
        ),
    ),
    (
        "HTTP_502",
        re.compile(
            r"(?:^|:\s*)(?:unexpected status code[: ]+502|"
            r"502 bad gateway)$"
        ),
    ),
    (
        "HTTP_503",
        re.compile(
            r"(?:^|:\s*)(?:unexpected status code[: ]+503|"
            r"503 service unavailable)$"
        ),
    ),
    (
        "HTTP_504",
        re.compile(
            r"(?:^|:\s*)(?:unexpected status code[: ]+504|"
            r"504 gateway timeout)$"
        ),
    ),
)
_REGISTRY_RETRY_POLICY = {
    "schema_version": "phase0.registry-retry-policy.v1",
    "max_attempts": _REGISTRY_TOTAL_ATTEMPTS,
    "backoff_seconds": _REGISTRY_RETRY_DELAYS_SECONDS,
    "manifest_operation_deadline_seconds": (
        _MANIFEST_OPERATION_DEADLINE_SECONDS
    ),
    "manifest_attempt_timeout_seconds": _MANIFEST_ATTEMPT_TIMEOUT_SECONDS,
    "pull_operation_deadline_seconds": _PULL_OPERATION_DEADLINE_SECONDS,
    "pull_attempt_timeout_seconds": _PULL_ATTEMPT_TIMEOUT_SECONDS,
    "transient_categories": (
        "EOF",
        "UNEXPECTED_EOF",
        "IO_TIMEOUT",
        "CONNECTION_RESET",
        "TLS_HANDSHAKE_TIMEOUT",
        "TEMPORARY_DNS_FAILURE",
        "HTTP_429",
        "HTTP_500",
        "HTTP_502",
        "HTTP_503",
        "HTTP_504",
    ),
    "permanent_failure_precedence": True,
}
_REGISTRY_RETRY_POLICY_SHA256 = canonical_json_sha256(
    _REGISTRY_RETRY_POLICY
)


def _transient_registry_failure_category(message: str) -> str | None:
    normalized = message.casefold()
    if _permanent_registry_failure_category(normalized) is not None:
        return None
    for raw_line in message.splitlines():
        line = raw_line.strip().casefold()
        if _DOCKER_REQUEST_EOF.fullmatch(line) is not None:
            return "EOF"
        for category, pattern in _TRANSIENT_REGISTRY_TERMINAL_CAUSES:
            if pattern.search(line) is not None:
                return category
        for category, pattern in _HTTP_TRANSIENT:
            if pattern.search(line) is not None:
                return category
    return None


def _permanent_registry_failure_category(message: str) -> str | None:
    normalized = message.casefold()
    for category, pattern in _PERMANENT_REGISTRY_FAILURES:
        if pattern.search(normalized) is not None:
            return category
    return None


class Arm64ManifestSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    image_index_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    resolved_platform_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def parse_arm64_manifest(
    raw: str,
    *,
    local_image: InspectedImage | None = None,
) -> Arm64ManifestSelection:
    try:
        payload_bytes = _manifest_payload_bytes(raw)
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict):
            raise TypeError
        exact_digest = "sha256:" + sha256_bytes(payload_bytes)
        if "manifests" not in payload:
            if local_image is None:
                raise Arm64ManifestUnavailable(
                    "single manifest requires independent local platform proof"
                )
            if (
                local_image.architecture != "arm64"
                or local_image.platform != "linux/arm64"
                or local_image.image_index_digest != exact_digest
                or local_image.resolved_platform_digest != exact_digest
            ):
                raise Arm64ManifestUnavailable(
                    "single manifest local platform proof is not native ARM64"
                )
            return Arm64ManifestSelection(
                image_index_digest=exact_digest,
                resolved_platform_digest=exact_digest,
            )
        manifests = payload["manifests"]
        if not isinstance(manifests, list):
            raise TypeError
        matching = [
            item
            for item in manifests
            if isinstance(item, dict)
            and _is_native_arm64_platform(item.get("platform"))
        ]
        if len(matching) != 1:
            raise Arm64ManifestUnavailable(
                "frozen image index lacks one native linux/arm64 manifest"
            )
        return Arm64ManifestSelection(
            image_index_digest=exact_digest,
            resolved_platform_digest=str(matching[0]["digest"]),
        )
    except Arm64ManifestUnavailable:
        raise
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValidationError,
    ) as error:
        raise Arm64ManifestUnavailable(
            "frozen image index metadata is incomplete"
        ) from error


def _manifest_payload_bytes(raw: str) -> bytes:
    payload = raw.encode("utf-8")
    if payload.endswith(b"\r\n"):
        return payload[:-2]
    if payload.endswith(b"\n"):
        return payload[:-1]
    return payload


def _is_native_arm64_platform(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    variant = str(value.get("variant", "")).lower()
    return (
        str(value.get("os", "")).lower() == "linux"
        and str(value.get("architecture", "")).lower() in {"arm64", "aarch64"}
        and variant in {"", "v8"}
    )


class _Runner(Protocol):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult: ...

    def run_registry_inspect(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        route: RegistryRouteCapability,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class _RegistryCommandEvidence:
    stdout_ref: str
    stdout_content_sha256: str
    stdout_artifact_sha256: str
    stderr_ref: str
    stderr_content_sha256: str
    stderr_artifact_sha256: str
    command_log_ref: str
    command_log_sha256: str


@dataclass(frozen=True)
class _SuccessfulRegistryCommand:
    result: CommandResult
    attempt_artifact_ref: str
    attempt_artifact_sha256: str
    ordinal: int


def _run_registry_command_with_retry(
    *,
    execute: Callable[[float], CommandResult],
    store: ObserverEvidenceStore,
    artifacts_root: Path,
    run_id: str,
    source_index: int,
    source_reference: str,
    operation: str,
    deadline_seconds: float,
    per_attempt_timeout_seconds: float,
    route: RegistryRouteCapability | None = None,
) -> _SuccessfulRegistryCommand:
    if (
        operation not in {"manifest", "pull"}
        or deadline_seconds <= 0
        or per_attempt_timeout_seconds <= 0
    ):
        raise ValueError("registry retry policy invocation is invalid")
    attempt_artifacts: list[dict[str, object]] = []
    operation_started = _retry_monotonic()
    operation_deadline = operation_started + deadline_seconds
    for ordinal in range(1, _REGISTRY_TOTAL_ATTEMPTS + 1):
        before_attempt = _retry_monotonic()
        remaining_seconds = operation_deadline - before_attempt
        if remaining_seconds <= 0:
            _write_registry_retry_summary(
                store=store,
                source_index=source_index,
                source_reference=source_reference,
                operation=operation,
                deadline_seconds=deadline_seconds,
                final_status="DEADLINE_EXHAUSTED",
                reason_category="OPERATION_DEADLINE_EXHAUSTED",
                attempt_artifacts=attempt_artifacts,
                successful_ordinal=None,
                exhausted=False,
            )
            raise UpstreamCommandFailed(f"{operation} failed")
        result = execute(
            min(per_attempt_timeout_seconds, remaining_seconds)
        )
        try:
            evidence = _validate_registry_command_evidence(
                result,
                artifacts_root=artifacts_root,
                run_id=run_id,
            )
        except (OSError, ValueError):
            _write_registry_retry_summary(
                store=store,
                source_index=source_index,
                source_reference=source_reference,
                operation=operation,
                deadline_seconds=deadline_seconds,
                final_status="EVIDENCE_INCOMPLETE",
                reason_category="EVIDENCE_INCOMPLETE",
                attempt_artifacts=attempt_artifacts,
                successful_ordinal=None,
                exhausted=False,
                attempts_started=ordinal,
                failed_ordinal=ordinal,
                evidence_complete=False,
            )
            raise UpstreamCommandFailed(f"{operation} failed") from None

        observed_at = _retry_monotonic()
        reason_category, retryable = _registry_result_disposition(result)
        backoff_seconds = 0.0
        decision = "SUCCESS" if result.exit_code == 0 else "STOP"
        final_status: str | None = None
        exhausted = False
        if result.exit_code != 0 and retryable:
            if ordinal == _REGISTRY_TOTAL_ATTEMPTS:
                final_status = "EXHAUSTED"
                exhausted = True
            else:
                candidate_backoff = _REGISTRY_RETRY_DELAYS_SECONDS[
                    ordinal - 1
                ]
                if operation_deadline - observed_at > candidate_backoff:
                    decision = "RETRY"
                    backoff_seconds = candidate_backoff
                else:
                    final_status = "DEADLINE_EXHAUSTED"
        elif result.exit_code != 0:
            final_status = "NON_RETRYABLE_FAILURE"

        attempt_payload: dict[str, object] = {
            "schema_version": "phase0.registry-command-attempt.v1",
            "policy_schema_version": _REGISTRY_RETRY_POLICY["schema_version"],
            "policy_sha256": _REGISTRY_RETRY_POLICY_SHA256,
            "run_id": run_id,
            "operation": operation,
            "source_reference": source_reference,
            "ordinal": ordinal,
            "max_attempts": _REGISTRY_TOTAL_ATTEMPTS,
            "process_exit_code": result.process_exit_code,
            "process_timed_out": result.process_timed_out,
            "terminal_exit_code": result.exit_code,
            "reason_category": reason_category,
            "decision": decision,
            "command_log_ref": evidence.command_log_ref,
            "command_log_sha256": evidence.command_log_sha256,
            "stdout_ref": evidence.stdout_ref,
            "stdout_content_sha256": evidence.stdout_content_sha256,
            "stdout_artifact_sha256": evidence.stdout_artifact_sha256,
            "stderr_ref": evidence.stderr_ref,
            "stderr_content_sha256": evidence.stderr_content_sha256,
            "stderr_artifact_sha256": evidence.stderr_artifact_sha256,
            "backoff_seconds": backoff_seconds,
            "monotonic_observed_seconds": max(
                0.0,
                observed_at - operation_started,
            ),
        }
        if route is not None:
            attempt_payload.update(
                {
                    "route_raw_sha256": route.raw_sha256,
                    "route_configuration_sha256": (
                        route.configuration_sha256
                    ),
                    "route_environment_sha256": route.environment_sha256,
                }
            )
        attempt_artifact = store.write_immutable(
            (
                f"inputs/bootstrap/{source_index:03d}-{operation}"
                f"-attempt-{ordinal:02d}.json"
            ),
            attempt_payload,
        )
        attempt_ref = attempt_artifact.path.relative_to(
            store.root
        ).as_posix()
        attempt_artifacts.append(
            {
                "ordinal": ordinal,
                "artifact_ref": attempt_ref,
                "artifact_sha256": attempt_artifact.sha256,
            }
        )
        if route is not None:
            store.write_immutable(
                (
                    f"inputs/bootstrap/{source_index:03d}"
                    f"-registry-route-binding-attempt-{ordinal:02d}.json"
                ),
                {
                    "schema_version": (
                        "phase0.registry-route-command-binding.v1"
                    ),
                    "run_id": run_id,
                    "operation": operation,
                    "source_reference": source_reference,
                    "attempt": ordinal,
                    "ordinal": ordinal,
                    "manifest_command_log_sha256": (
                        evidence.command_log_sha256
                    ),
                    "attempt_artifact_ref": attempt_ref,
                    "attempt_artifact_sha256": attempt_artifact.sha256,
                    "exit_code": result.exit_code,
                    "reason_category": reason_category,
                    "decision": decision,
                    "route_raw_sha256": route.raw_sha256,
                    "route_configuration_sha256": (
                        route.configuration_sha256
                    ),
                    "route_environment_sha256": route.environment_sha256,
                    "registry_proxy_mode": route.mode,
                    "registry_proxy_socks_present": route.socks_present,
                },
            )

        if result.exit_code == 0:
            _write_registry_retry_summary(
                store=store,
                source_index=source_index,
                source_reference=source_reference,
                operation=operation,
                deadline_seconds=deadline_seconds,
                final_status=(
                    "SUCCESS"
                    if ordinal == 1
                    else "SUCCESS_AFTER_TRANSIENT"
                ),
                reason_category=reason_category,
                attempt_artifacts=attempt_artifacts,
                successful_ordinal=ordinal,
                exhausted=False,
            )
            return _SuccessfulRegistryCommand(
                result=result,
                attempt_artifact_ref=attempt_ref,
                attempt_artifact_sha256=attempt_artifact.sha256,
                ordinal=ordinal,
            )

        if final_status is not None:
            _write_registry_retry_summary(
                store=store,
                source_index=source_index,
                source_reference=source_reference,
                operation=operation,
                deadline_seconds=deadline_seconds,
                final_status=final_status,
                reason_category=reason_category,
                attempt_artifacts=attempt_artifacts,
                successful_ordinal=None,
                exhausted=exhausted,
            )
            raise UpstreamCommandFailed(f"{operation} failed")

        _retry_sleep(backoff_seconds)
    raise AssertionError("registry retry loop exhausted without terminal result")


def _write_registry_retry_summary(
    *,
    store: ObserverEvidenceStore,
    source_index: int,
    source_reference: str,
    operation: str,
    deadline_seconds: float,
    final_status: str,
    reason_category: str,
    attempt_artifacts: list[dict[str, object]],
    successful_ordinal: int | None,
    exhausted: bool,
    attempts_started: int | None = None,
    failed_ordinal: int | None = None,
    evidence_complete: bool = True,
) -> None:
    complete_attempt_count = len(attempt_artifacts)
    started_count = (
        complete_attempt_count
        if attempts_started is None
        else attempts_started
    )
    if (
        started_count < complete_attempt_count
        or (
            evidence_complete
            and (
                started_count != complete_attempt_count
                or failed_ordinal is not None
            )
        )
        or (
            not evidence_complete
            and (
                failed_ordinal is None
                or failed_ordinal > started_count
            )
        )
    ):
        raise ValueError("registry retry summary accounting is invalid")
    store.write_immutable(
        (
            f"inputs/bootstrap/{source_index:03d}"
            f"-{operation}-retry-summary.json"
        ),
        {
            "schema_version": "phase0.registry-command-retry-summary.v1",
            "policy_schema_version": _REGISTRY_RETRY_POLICY["schema_version"],
            "policy_sha256": _REGISTRY_RETRY_POLICY_SHA256,
            "run_id": store.run_id,
            "source_reference": source_reference,
            "operation": operation,
            "max_attempts": _REGISTRY_TOTAL_ATTEMPTS,
            "deadline_seconds": deadline_seconds,
            "attempt_count": started_count,
            "attempts_started": started_count,
            "complete_attempt_count": complete_attempt_count,
            "failed_ordinal": failed_ordinal,
            "evidence_complete": evidence_complete,
            "final_status": final_status,
            "reason_category": reason_category,
            "attempt_artifacts": attempt_artifacts,
            "successful_ordinal": successful_ordinal,
            "exhausted": exhausted,
        },
    )


def _registry_result_disposition(
    result: CommandResult,
) -> tuple[str, bool]:
    if result.process_timed_out:
        return "PROCESS_TIMEOUT", False
    if result.process_exit_code is None:
        return "PROCESS_START_FAILED", False
    if result.exit_code == 0:
        return "SUCCESS", False
    if not result.stdout and not result.stderr:
        return "EMPTY_FAILURE", False
    combined = "\n".join((result.stderr, result.stdout))
    permanent = _permanent_registry_failure_category(combined)
    if permanent is not None:
        return permanent, False
    transient = _transient_registry_failure_category(result.stderr)
    if transient is not None:
        return transient, True
    return "UNKNOWN_FAILURE", False


def _validate_registry_command_evidence(
    result: CommandResult,
    *,
    artifacts_root: Path,
    run_id: str,
) -> _RegistryCommandEvidence:
    required = (
        result.stdout_artifact,
        result.stdout_sha256,
        result.stderr_artifact,
        result.stderr_sha256,
        result.command_log_artifact,
        result.command_log_sha256,
    )
    if any(value is None for value in required):
        raise ValueError("registry command evidence is incomplete")
    assert result.stdout_artifact is not None
    assert result.stdout_sha256 is not None
    assert result.stderr_artifact is not None
    assert result.stderr_sha256 is not None
    assert result.command_log_artifact is not None
    assert result.command_log_sha256 is not None
    if (
        result.stdout_sha256 != sha256_bytes(result.stdout.encode("utf-8"))
        or result.stderr_sha256
        != sha256_bytes(result.stderr.encode("utf-8"))
    ):
        raise ValueError("registry command stream hashes differ")

    root = Path(artifacts_root).resolve()
    evaluator_root = (root / "evaluator-only" / run_id).resolve()
    observer_root = (root / "observer-visible" / run_id).resolve()
    stdout_candidate = Path(result.stdout_artifact)
    stderr_candidate = Path(result.stderr_artifact)
    command_candidate = Path(result.command_log_artifact)
    if any(
        candidate.is_symlink()
        for candidate in (
            stdout_candidate,
            stderr_candidate,
            command_candidate,
        )
    ):
        raise ValueError("registry command evidence path is a symlink")
    stdout_path = stdout_candidate.resolve(strict=True)
    stderr_path = stderr_candidate.resolve(strict=True)
    command_path = command_candidate.resolve(strict=True)
    if (
        evaluator_root not in stdout_path.parents
        or evaluator_root not in stderr_path.parents
        or observer_root not in command_path.parents
        or sha256_file(command_path) != result.command_log_sha256
    ):
        raise ValueError("registry command evidence path is invalid")

    stdout_relative = stdout_path.relative_to(evaluator_root).as_posix()
    stderr_relative = stderr_path.relative_to(evaluator_root).as_posix()
    command_relative = command_path.relative_to(observer_root).as_posix()
    stdout_payload = _load_registry_evidence_json(stdout_path)
    stderr_payload = _load_registry_evidence_json(stderr_path)
    command_payload = _load_registry_evidence_json(command_path)
    try:
        command_log = CommandLog.model_validate(command_payload)
    except ValidationError as error:
        raise ValueError("registry command log evidence is invalid") from error
    if (
        stdout_payload.get("schema_version")
        != "phase0.command-stream.v1"
        or stdout_payload.get("stream") != "stdout"
        or stdout_payload.get("content") != result.stdout
        or stdout_payload.get("content_sha256") != result.stdout_sha256
        or stderr_payload.get("schema_version")
        != "phase0.command-stream.v1"
        or stderr_payload.get("stream") != "stderr"
        or stderr_payload.get("content") != result.stderr
        or stderr_payload.get("content_sha256") != result.stderr_sha256
    ):
        raise ValueError("registry command stream evidence differs")
    if (
        command_log.run_id != run_id
        or command_log.command != Path(result.arguments[0]).name
        or command_log.arguments
        != redact_command_arguments(result.arguments)
        or command_log.process_exit_code != result.process_exit_code
        or command_log.process_timed_out is not result.process_timed_out
        or command_log.terminal_exit_code != result.exit_code
        or command_log.stdout_artifact != stdout_relative
        or command_log.stdout_sha256 != result.stdout_sha256
        or command_log.stderr_artifact != stderr_relative
        or command_log.stderr_sha256 != result.stderr_sha256
    ):
        raise ValueError("registry command log evidence differs")
    return _RegistryCommandEvidence(
        stdout_ref=stdout_relative,
        stdout_content_sha256=result.stdout_sha256,
        stdout_artifact_sha256=sha256_file(stdout_path),
        stderr_ref=stderr_relative,
        stderr_content_sha256=result.stderr_sha256,
        stderr_artifact_sha256=sha256_file(stderr_path),
        command_log_ref=command_relative,
        command_log_sha256=result.command_log_sha256,
    )


def _load_registry_evidence_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("registry command evidence is invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("registry command evidence is invalid")
    return payload


def bootstrap_image_lock(
    *,
    project_root: Path,
    artifacts_root: Path,
    run_id: str,
    runner: _Runner,
    docker_endpoint: str,
    replace_locked: bool = False,
) -> ImageLockManifest:
    """Resolve exact inputs and publish or verify one local ARM64 lock."""
    root = Path(project_root).resolve()
    lock_path = root / "config" / "phase0" / "image-lock.json"
    existing = load_image_lock(lock_path)
    if existing.status is ImageLockStatus.LOCKED and replace_locked:
        raise FileExistsError("existing image lock is immutable")

    config = build_compose_invocation(
        ComposeAction.CONFIG,
        project_root=root,
        run_id=run_id,
        docker_endpoint=docker_endpoint,
    )
    config_result = runner.run(
        config.arguments,
        timeout_seconds=config.timeout_seconds,
        environment=config.environment,
    )
    resolved = parse_resolved_compose_config(config_result)
    compose_evidence = _compose_command_evidence_link(
        config_result,
        artifacts_root=artifacts_root,
        run_id=run_id,
    )
    port_plan = parse_expected_port_bindings(resolved)

    if existing.status is ImageLockStatus.LOCKED:
        cached = _inspect_local_images(
            runner=runner,
            docker_endpoint=docker_endpoint,
            run_id=run_id,
            sources=existing.allowed_source_references,
        )
        verification = verify_acceptance_image_lock(
            existing,
            cached_images=cached,
            observed_upstream_commit=existing.upstream_commit,
            observed_compose_config_sha256=resolved.sha256,
        )
        if not verification.passed:
            raise ValueError("existing image lock failed live verification")
        return existing

    registry_route = _discover_registry_proxy(
        runner,
        run_id=run_id,
        docker_endpoint=docker_endpoint,
    )
    proxy_environment = {
        "ECOMSRE_RUN_ID": run_id,
        **dict(registry_route.proxy_environment),
    }
    with EvaluatorEvidenceStore(artifacts_root, run_id) as evaluator:
        proxy_environment_artifact = evaluator.write_immutable(
            "lifecycle/bootstrap/registry-route-environment.json",
            {
                "schema_version": "phase0.registry-route-environment.v1",
                "run_id": run_id,
                "environment": proxy_environment,
                "environment_sha256": registry_route.environment_sha256,
                "configuration_sha256": (
                    registry_route.configuration_sha256
                ),
            },
        )
    prefix = docker_host_prefix(docker_endpoint)
    raw_manifests: dict[str, str] = {}
    successful_manifest_attempts: dict[
        str, _SuccessfulRegistryCommand
    ] = {}
    successful_pull_attempts: dict[str, _SuccessfulRegistryCommand] = {}
    with ObserverEvidenceStore(artifacts_root, run_id) as store:
        resolved_payload = json.loads(resolved.stdout)
        services = resolved_payload.get("services", {})
        if not isinstance(services, dict):
            raise ValueError("resolved Compose services are incomplete")
        service_platforms = tuple(
            sorted(
                (
                    str(service),
                    str(config.get("platform", "")),
                )
                for service, config in services.items()
                if isinstance(config, dict)
            )
        )
        store.write_immutable(
            "inputs/bootstrap/resolved-compose.json",
            {
                "schema_version": "phase0.bootstrap-resolved-compose.v1",
                "compose_config_sha256": resolved.sha256,
                **compose_evidence,
                "service_image_mapping": resolved.service_image_mapping,
                "service_platforms": service_platforms,
                "pull_policy": "bootstrap-explicit-pull",
                "required_platform": "linux/arm64",
                "acceptance_pull_policy": "never",
                "registry_proxy_mode": registry_route.mode,
                "registry_proxy_socks_present": (
                    registry_route.socks_present
                ),
                "registry_proxy_source": registry_route.source,
                "registry_proxy_parser_schema": registry_route.parser_schema,
                "registry_proxy_raw_sha256": registry_route.raw_sha256,
                "registry_proxy_configuration_sha256": (
                    registry_route.configuration_sha256
                ),
                "registry_proxy_environment_sha256": (
                    registry_route.environment_sha256
                ),
                "registry_proxy_environment_artifact_sha256": (
                    proxy_environment_artifact.sha256
                ),
                "port_plan": [
                    binding.model_dump(mode="json") for binding in port_plan
                ],
            },
        )
        for index, source in enumerate(resolved.image_references):
            manifest_arguments = (
                *prefix,
                "buildx",
                "imagetools",
                "inspect",
                source,
                "--raw",
            )
            manifest_success = _run_registry_command_with_retry(
                execute=lambda timeout_seconds: runner.run_registry_inspect(
                    manifest_arguments,
                    timeout_seconds=timeout_seconds,
                    route=registry_route,
                ),
                store=store,
                artifacts_root=artifacts_root,
                run_id=run_id,
                source_index=index,
                source_reference=source,
                operation="manifest",
                deadline_seconds=_MANIFEST_OPERATION_DEADLINE_SECONDS,
                per_attempt_timeout_seconds=(
                    _MANIFEST_ATTEMPT_TIMEOUT_SECONDS
                ),
                route=registry_route,
            )
            result = manifest_success.result
            successful_manifest_attempts[source] = manifest_success
            raw_manifests[source] = result.stdout
            store.write_immutable(
                f"inputs/bootstrap/{index:03d}-manifest-raw.json",
                {
                    "schema_version": "phase0.registry-manifest-raw.v1",
                    "source_reference": source,
                    "raw_manifest": result.stdout,
                    "raw_manifest_sha256": sha256_bytes(
                        _manifest_payload_bytes(result.stdout)
                    ),
                    "successful_manifest_attempt_ref": (
                        manifest_success.attempt_artifact_ref
                    ),
                    "successful_manifest_attempt_sha256": (
                        manifest_success.attempt_artifact_sha256
                    ),
                    "successful_manifest_attempt_ordinal": (
                        manifest_success.ordinal
                    ),
                },
            )
            pull_arguments = (
                *prefix,
                "pull",
                "--platform",
                "linux/arm64",
                source,
            )
            pull_success = _run_registry_command_with_retry(
                execute=lambda timeout_seconds: runner.run(
                    pull_arguments,
                    timeout_seconds=timeout_seconds,
                    environment={"ECOMSRE_RUN_ID": run_id},
                ),
                store=store,
                artifacts_root=artifacts_root,
                run_id=run_id,
                source_index=index,
                source_reference=source,
                operation="pull",
                deadline_seconds=_PULL_OPERATION_DEADLINE_SECONDS,
                per_attempt_timeout_seconds=_PULL_ATTEMPT_TIMEOUT_SECONDS,
            )
            successful_pull_attempts[source] = pull_success

    local = _inspect_local_images(
        runner=runner,
        docker_endpoint=docker_endpoint,
        run_id=run_id,
        sources=resolved.image_references,
    )
    source_indexes = {
        source: index for index, source in enumerate(resolved.image_references)
    }
    with ObserverEvidenceStore(artifacts_root, run_id) as store:
        for image in local:
            raw_manifest = raw_manifests.get(image.source_reference)
            registry = (
                parse_arm64_manifest(raw_manifest, local_image=image)
                if raw_manifest is not None
                else None
            )
            if registry is None or (
                image.image_index_digest != registry.image_index_digest
                or image.resolved_platform_digest
                != registry.resolved_platform_digest
            ):
                raise ValueError("registry and local image digest metadata differ")
            manifest_success = successful_manifest_attempts[
                image.source_reference
            ]
            pull_success = successful_pull_attempts[image.source_reference]
            store.write_immutable(
                (
                    "inputs/bootstrap/"
                    f"{source_indexes[image.source_reference]:03d}"
                    "-manifest-selection.json"
                ),
                {
                    "schema_version": "phase0.registry-manifest-selection.v1",
                    "source_reference": image.source_reference,
                    "local_architecture": image.architecture,
                    "local_platform": image.platform,
                    "local_image_id": image.image_id,
                    "local_resolved_platform_digest": (
                        image.resolved_platform_digest
                    ),
                    "local_resolved_platform_digest_source": (
                        "docker_image_inspect_platform_descriptor"
                    ),
                    "registry_local_cross_binding_verified": True,
                    "successful_manifest_attempt_ref": (
                        manifest_success.attempt_artifact_ref
                    ),
                    "successful_manifest_attempt_sha256": (
                        manifest_success.attempt_artifact_sha256
                    ),
                    "successful_manifest_attempt_ordinal": (
                        manifest_success.ordinal
                    ),
                    "successful_pull_attempt_ref": (
                        pull_success.attempt_artifact_ref
                    ),
                    "successful_pull_attempt_sha256": (
                        pull_success.attempt_artifact_sha256
                    ),
                    "successful_pull_attempt_ordinal": pull_success.ordinal,
                    **registry.model_dump(mode="json"),
                },
            )
    candidate = generate_candidate_image_lock(
        images=local,
        resolved_compose=resolved,
        acquired_at=datetime.now(UTC),
    )
    write_candidate_image_lock(lock_path, candidate)
    with ObserverEvidenceStore(artifacts_root, run_id) as store:
        store.write_immutable(
            "inputs/bootstrap/image-lock-attempt-binding.json",
            {
                "schema_version": "phase0.image-lock-attempt-binding.v1",
                "run_id": run_id,
                "image_lock_sha256": sha256_file(lock_path),
                "sources": [
                    {
                        "source_reference": source,
                        "successful_manifest_attempt_ref": (
                            successful_manifest_attempts[
                                source
                            ].attempt_artifact_ref
                        ),
                        "successful_manifest_attempt_sha256": (
                            successful_manifest_attempts[
                                source
                            ].attempt_artifact_sha256
                        ),
                        "successful_manifest_attempt_ordinal": (
                            successful_manifest_attempts[source].ordinal
                        ),
                        "successful_pull_attempt_ref": (
                            successful_pull_attempts[
                                source
                            ].attempt_artifact_ref
                        ),
                        "successful_pull_attempt_sha256": (
                            successful_pull_attempts[
                                source
                            ].attempt_artifact_sha256
                        ),
                        "successful_pull_attempt_ordinal": (
                            successful_pull_attempts[source].ordinal
                        ),
                    }
                    for source in resolved.image_references
                ],
            },
        )
    published = load_image_lock(lock_path)
    verification = verify_acceptance_image_lock(
        published,
        cached_images=local,
        observed_upstream_commit=published.upstream_commit,
        observed_compose_config_sha256=resolved.sha256,
    )
    if not verification.passed:
        raise ValueError("published image lock failed live verification")
    return published


_SCUTIL_PROXY_ARGUMENTS = ("/usr/sbin/scutil", "--proxy")
_SCUTIL_KEY_VALUE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)\s*:\s*(.*)$")
_SCUTIL_COLLECTION = re.compile(r"^<(?:array|dictionary)>\s+\{$")
_PROXY_RELEVANT_KEYS = frozenset(
    {
        "HTTPEnable",
        "HTTPPort",
        "HTTPProxy",
        "HTTPSEnable",
        "HTTPSPort",
        "HTTPSProxy",
        "SOCKSEnable",
        "ProxyAutoConfigEnable",
        "ProxyAutoDiscoveryEnable",
    }
)
_UNSUPPORTED_PROXY_ENABLE_KEYS = (
    "ProxyAutoConfigEnable",
    "ProxyAutoDiscoveryEnable",
)


def _discover_registry_proxy(
    runner: _Runner,
    *,
    run_id: str,
    docker_endpoint: str,
) -> RegistryRouteCapability:
    result = runner.run(
        _SCUTIL_PROXY_ARGUMENTS,
        timeout_seconds=5,
        environment={"ECOMSRE_RUN_ID": run_id},
    )
    if result.exit_code != 0:
        raise ProxyDiscoveryUnavailable("proxy discovery failed")
    raw_sha256 = sha256_bytes(result.stdout.encode("utf-8"))
    if (
        result.stdout_sha256 is not None
        and result.stdout_sha256 != raw_sha256
    ):
        raise ProxyConfigurationUnsafe("proxy discovery evidence differs")
    return _parse_scutil_proxy(
        result.stdout,
        run_id=run_id,
        docker_endpoint=docker_endpoint,
        raw_sha256=raw_sha256,
    )


def _parse_scutil_proxy(
    raw: str,
    *,
    run_id: str,
    docker_endpoint: str,
    raw_sha256: str | None = None,
) -> RegistryRouteCapability:
    try:
        values = _parse_scutil_top_level(raw)
        for key in (
            "HTTPEnable",
            "HTTPSEnable",
            "SOCKSEnable",
            *_UNSUPPORTED_PROXY_ENABLE_KEYS,
        ):
            if key in values and values[key] not in {"0", "1"}:
                raise ProxyConfigurationUnsafe(
                    "proxy enable flag is invalid"
                )
        if any(values.get(key) == "1" for key in _UNSUPPORTED_PROXY_ENABLE_KEYS):
            raise ProxyConfigurationUnsafe(
                "PAC and automatic proxy discovery are unsupported"
            )
        socks_present = values.get("SOCKSEnable") == "1"
        environment: dict[str, str] = {}
        if values.get("HTTPEnable") == "1":
            environment["HTTP_PROXY"] = _scutil_proxy_url(
                values,
                prefix="HTTP",
            )
        if values.get("HTTPSEnable") == "1":
            environment["HTTPS_PROXY"] = _scutil_proxy_url(
                values,
                prefix="HTTPS",
            )
        mode = {
            frozenset(): "DIRECT",
            frozenset({"HTTP_PROXY"}): "LOOPBACK_HTTP",
            frozenset({"HTTPS_PROXY"}): "LOOPBACK_HTTPS",
            frozenset(
                {"HTTP_PROXY", "HTTPS_PROXY"}
            ): "LOOPBACK_HTTP_HTTPS",
        }[frozenset(environment)]
        return create_registry_route_capability(
            run_id=run_id,
            mode=mode,
            socks_present=socks_present,
            docker_endpoint=docker_endpoint,
            raw_sha256=raw_sha256 or sha256_bytes(raw.encode("utf-8")),
            proxy_environment=environment,
        )
    except ProxyConfigurationUnsafe:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise ProxyConfigurationUnsafe(
            "proxy configuration is unsafe"
        ) from error


def _parse_scutil_top_level(raw: str) -> dict[str, str]:
    if "\r" in raw:
        raise ProxyConfigurationUnsafe("proxy output contains CR")
    lines = raw.splitlines()
    if (
        len(lines) < 2
        or lines[0] != "<dictionary> {"
        or lines[-1] != "}"
    ):
        raise ProxyConfigurationUnsafe("proxy output structure is invalid")
    values: dict[str, str] = {}
    depth = 1
    for line in lines[1:-1]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "}":
            depth -= 1
            if depth < 1:
                raise ProxyConfigurationUnsafe(
                    "proxy output nesting is invalid"
                )
            continue
        if depth != 1 and any(
            re.search(rf"\b{re.escape(key)}\s*:", stripped)
            for key in _PROXY_RELEVANT_KEYS
        ):
            raise ProxyConfigurationUnsafe(
                "nested proxy key is not allowed"
            )
        matched = _SCUTIL_KEY_VALUE.fullmatch(stripped)
        if matched is None:
            if "{" in stripped or "}" in stripped:
                raise ProxyConfigurationUnsafe(
                    "proxy output nesting is invalid"
                )
            continue
        key, value = matched.groups()
        if key in _PROXY_RELEVANT_KEYS:
            if depth != 1 or key in values:
                raise ProxyConfigurationUnsafe(
                    "duplicate or nested proxy key is not allowed"
                )
            values[key] = value
        if _SCUTIL_COLLECTION.fullmatch(value):
            depth += 1
        elif "{" in value or "}" in value:
            raise ProxyConfigurationUnsafe(
                "proxy output nesting is invalid"
            )
    if depth != 1:
        raise ProxyConfigurationUnsafe("proxy output nesting is invalid")
    return values


def _scutil_proxy_url(values: dict[str, str], *, prefix: str) -> str:
    host = values[f"{prefix}Proxy"]
    port_text = values[f"{prefix}Port"]
    if (
        not host
        or any(character.isspace() for character in host)
        or any(character in host for character in "@/[]?#%")
        or not port_text.isascii()
        or not port_text.isdecimal()
    ):
        raise ProxyConfigurationUnsafe("enabled proxy endpoint is invalid")
    port = int(port_text)
    try:
        address = ip_address(host)
    except ValueError as error:
        raise ProxyConfigurationUnsafe(
            "enabled proxy host is not a numeric IP"
        ) from error
    if not address.is_loopback or not 1 <= port <= 65_535:
        raise ProxyConfigurationUnsafe(
            "enabled proxy endpoint is not loopback"
        )
    rendered_host = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{rendered_host}:{port}"


def _compose_command_evidence_link(
    result: CommandResult,
    *,
    artifacts_root: Path,
    run_id: str,
) -> dict[str, str]:
    """Bind safe Compose projection to restricted raw stream and command audit."""
    if (
        result.stdout_artifact is None
        or result.stdout_sha256 is None
        or result.command_log_artifact is None
        or result.command_log_sha256 is None
        or result.stdout_sha256 != sha256_bytes(result.stdout.encode("utf-8"))
    ):
        raise ValueError("resolved Compose command evidence is incomplete")
    root = Path(artifacts_root).resolve()
    raw_root = (root / "evaluator-only" / run_id).resolve()
    command_root = (root / "observer-visible" / run_id).resolve()
    raw_path = Path(result.stdout_artifact).resolve(strict=True)
    command_path = Path(result.command_log_artifact).resolve(strict=True)
    if (
        raw_root not in raw_path.parents
        or command_root not in command_path.parents
        or raw_path.is_symlink()
        or command_path.is_symlink()
        or sha256_file(command_path) != result.command_log_sha256
    ):
        raise ValueError("resolved Compose command evidence path is invalid")
    try:
        raw_payload = json.loads(raw_path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("resolved Compose raw stream evidence is invalid") from error
    if (
        not isinstance(raw_payload, dict)
        or raw_payload.get("stream") != "stdout"
        or raw_payload.get("content") != result.stdout
        or raw_payload.get("content_sha256") != result.stdout_sha256
    ):
        raise ValueError("resolved Compose raw stream evidence differs")
    return {
        "compose_raw_stdout_ref": raw_path.relative_to(raw_root).as_posix(),
        "compose_raw_stdout_content_sha256": result.stdout_sha256,
        "compose_raw_stdout_artifact_sha256": sha256_file(raw_path),
        "compose_command_log_ref": command_path.relative_to(
            command_root
        ).as_posix(),
        "compose_command_log_sha256": result.command_log_sha256,
    }


def _inspect_local_images(
    *,
    runner: _Runner,
    docker_endpoint: str,
    run_id: str,
    sources: tuple[str, ...],
) -> tuple[InspectedImage, ...]:
    images: list[InspectedImage] = []
    prefix = docker_host_prefix(docker_endpoint)
    for source in sources:
        arguments = (
            *prefix,
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            source,
        )
        result = runner.run(
            arguments,
            timeout_seconds=30,
            environment={"ECOMSRE_RUN_ID": run_id},
        )
        images.extend(parse_cached_images(result))
    return tuple(images)
