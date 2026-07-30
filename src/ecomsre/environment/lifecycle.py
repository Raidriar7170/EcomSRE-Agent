"""Owned, image-bound, no-pull Docker Compose lifecycle adapters."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.environment.manifests import (
    UPSTREAM_COMMIT,
    ImageLockManifest,
    ImageLockStatus,
    LockVerification,
    ResolvedComposeConfig,
    verify_acceptance_image_lock,
)
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipError,
    OwnershipManifest,
    verify_owned_resources,
)
from ecomsre.environment.ownership_authority import (
    AuthenticatedOwnershipContext,
    OwnershipAuthorityError,
    OwnershipIntent,
    create_ownership_authority_artifacts,
    create_ownership_intent_artifacts,
    load_authenticated_ownership_context,
    load_authenticated_ownership_intent,
)
from ecomsre.environment.preflight import (
    AuthenticatedPreflightEvidence,
    CommandResult,
    DOCKER_DESKTOP_CONTEXT,
    DiscoveryParseError,
    docker_host_prefix,
    is_local_unix_docker_endpoint,
    parse_cached_images,
)
from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    sha256_bytes,
)
from ecomsre.evidence.store import (
    EvaluatorEvidenceStore,
    ObserverEvidenceStore,
)
from ecomsre.phase0.models import Outcome, TerminalResult


_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
_COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
_COMPOSE_RESOURCE_KINDS = frozenset({"container", "network", "volume", "port"})
_HOST_PORT_ALLOWLIST = {
    "frontend-proxy": frozenset({8080}),
    "prometheus": frozenset({9090}),
    "jaeger": frozenset({16686}),
    "opensearch": frozenset({9200}),
    "flagd": frozenset({8016}),
}
_COMPOSE_FILES = (
    "third_party/opentelemetry-demo/compose.yaml",
    "third_party/opentelemetry-demo/compose.observability.yaml",
    "config/phase0/compose.phase0.yaml",
)
_REQUIRED_NAMED_VOLUME_PLAN = {
    "astronomy-db": ("astronomy-db-data", "/var/lib/postgresql"),
    "jaeger": ("jaeger-data", "/tmp"),
    "prometheus": ("prometheus-data", "/prometheus"),
}
_OBSERVER_COMPOSE_LABEL_ALLOWLIST = frozenset(
    {
        _COMPOSE_PROJECT_LABEL,
        _COMPOSE_SERVICE_LABEL,
        PROJECT_LABEL,
        RUN_LABEL,
    }
)


class ComposeAction(str, Enum):
    CONFIG = "config"
    UP = "up"
    STATUS = "status"
    HEALTH = "health"
    DOWN = "down"


class ComposeInvocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: str = Field(min_length=1)
    arguments: tuple[str, ...] = Field(min_length=1)
    environment: dict[str, str]
    timeout_seconds: float = Field(gt=0)
    read_only: bool

    @model_validator(mode="before")
    @classmethod
    def require_argument_tuple(cls, value: object) -> object:
        if not isinstance(value, dict) or not isinstance(value.get("arguments"), tuple):
            raise ValueError("lifecycle arguments must be an allowlisted tuple")
        return value

    @model_validator(mode="after")
    def require_exact_allowlisted_argv(self) -> "ComposeInvocation":
        run_id = self.environment.get("ECOMSRE_RUN_ID")
        if (
            set(self.environment) != {"ECOMSRE_RUN_ID"}
            or _RUN_ID.fullmatch(run_id or "") is None
            or not _invocation_is_allowlisted(self, run_id=run_id or "")
        ):
            raise ValueError(
                "lifecycle invocation is not an exact allowlisted argv tuple"
            )
        return self


class LifecycleRunner(Protocol):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult: ...


class ReadinessEvidence(BaseModel):
    """Task 7 handoff; every declared readiness surface must be complete."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.readiness-evidence.v1"]
    run_id: str = Field(pattern=_RUN_ID.pattern)
    ownership_resources_complete: bool
    load_generator_ready: bool
    collector_ready: bool
    prometheus_fresh: bool
    jaeger_fresh: bool
    opensearch_fresh: bool

    @property
    def all_passed(self) -> bool:
        return all(
            (
                self.ownership_resources_complete,
                self.load_generator_ready,
                self.collector_ready,
                self.prometheus_fresh,
                self.jaeger_fresh,
                self.opensearch_fresh,
            )
        )


class ExpectedPortBinding(BaseModel):
    """Resolved Compose port intent before Docker assigns dynamic host ports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str = Field(min_length=1)
    container_name: str = Field(min_length=1)
    target_port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp", "udp"]
    published_port: int | None = Field(default=None, ge=1, le=65535)
    host_ip: str | None = None

    @model_validator(mode="after")
    def require_explicit_host_only_with_published(
        self,
    ) -> "ExpectedPortBinding":
        if self.published_port is None and self.host_ip is not None:
            raise ValueError("target-only port cannot predeclare a host binding")
        if self.published_port is not None and not self.host_ip:
            raise ValueError("explicit published port requires a host binding")
        if self.published_port is not None and self.host_ip not in {
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("published port host binding must be loopback")
        return self


class _ObservedPortBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    container_name: str
    container_id: str
    host_ip: str
    host_family: Literal["ipv4", "ipv6"]
    published_port: int = Field(ge=1, le=65535)
    target_port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp", "udp"]
    raw_binding: str


class LifecycleArtifactPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts_root: Path = Field(exclude=True)
    ownership_intent: Path | None = None
    ownership_manifest: Path | None = None
    ownership_anchor: Path | None = None
    resolved_compose: Path | None = None
    resolved_compose_raw: Path | None = None
    command_log: Path | None = None
    manual_diagnostic: Path | None = None


class LifecycleExecution(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    result: TerminalResult
    resolved_compose: ResolvedComposeConfig | None = None
    image_verification: LockVerification | None = None
    command_results: tuple[CommandResult, ...] = ()
    artifact_paths: LifecycleArtifactPaths | None = None
    ownership_context: AuthenticatedOwnershipContext | None = None
    docker_endpoint: str | None = None
    daemon_id: str | None = None

    @model_validator(mode="after")
    def require_success_artifacts(self) -> "LifecycleExecution":
        if self.result.outcome is Outcome.SUCCESS and self.resolved_compose is not None:
            if (
                self.image_verification is None
                or not self.image_verification.passed
                or self.artifact_paths is None
                or self.ownership_context is None
                or self.artifact_paths.ownership_manifest is None
                or self.artifact_paths.resolved_compose is None
                or self.artifact_paths.resolved_compose_raw is None
                or self.artifact_paths.command_log is None
            ):
                raise ValueError(
                    "successful up requires complete authenticated artifacts"
                )
        return self


def build_compose_invocation(
    action: ComposeAction,
    *,
    project_root: Path,
    run_id: str,
    docker_endpoint: str,
) -> ComposeInvocation:
    root = Path(project_root).resolve()
    upstream = root / "third_party" / "opentelemetry-demo"
    base = (
        *docker_host_prefix(docker_endpoint),
        "compose",
        "--project-name",
        PROJECT_NAMESPACE,
        "--project-directory",
        str(upstream),
        "--env-file",
        str(upstream / ".env"),
        "--file",
        str(upstream / "compose.yaml"),
        "--file",
        str(upstream / "compose.observability.yaml"),
        "--file",
        str(root / "config" / "phase0" / "compose.phase0.yaml"),
    )
    suffixes = {
        ComposeAction.CONFIG: ("config", "--format", "json"),
        ComposeAction.UP: (
            "up",
            "--detach",
            "--pull",
            "never",
            "--no-build",
        ),
        ComposeAction.STATUS: ("ps", "--all", "--format", "json"),
        ComposeAction.HEALTH: ("ps", "--all", "--format", "json"),
        ComposeAction.DOWN: ("down",),
    }
    return ComposeInvocation(
        purpose=action.value,
        arguments=base + suffixes[action],
        environment={"ECOMSRE_RUN_ID": run_id},
        timeout_seconds=300 if action in {ComposeAction.UP, ComposeAction.DOWN} else 30,
        read_only=action
        in {
            ComposeAction.CONFIG,
            ComposeAction.STATUS,
            ComposeAction.HEALTH,
        },
    )


def build_image_inspection_invocations(
    image_lock: ImageLockManifest,
    *,
    run_id: str,
    docker_endpoint: str,
) -> tuple[ComposeInvocation, ...]:
    """Build one auditable local inspection for every exact locked source."""
    return tuple(
        ComposeInvocation(
            purpose="inspect_image",
            arguments=(
                *docker_host_prefix(docker_endpoint),
                "image",
                "inspect",
                "--platform",
                "linux/arm64",
                source,
            ),
            environment={"ECOMSRE_RUN_ID": run_id},
            timeout_seconds=30,
            read_only=True,
        )
        for source in image_lock.allowed_source_references
    )


def build_ownership_discovery_invocations(
    *,
    project_root: Path,
    run_id: str,
    docker_endpoint: str,
) -> tuple[ComposeInvocation, ...]:
    """Build broad potential-target and exact-owned discovery for each kind."""
    del project_root
    invocations: list[ComposeInvocation] = []
    for kind in ("container", "network", "volume"):
        for scope in ("potential", "owned"):
            purpose = f"{scope}_{kind}s"
            invocations.append(
                ComposeInvocation(
                    purpose=purpose,
                    arguments=_discovery_arguments(
                        purpose,
                        run_id=run_id,
                        docker_endpoint=docker_endpoint,
                    ),
                    environment={"ECOMSRE_RUN_ID": run_id},
                    timeout_seconds=30,
                    read_only=True,
                )
            )
    return tuple(invocations)


def build_daemon_revalidation_invocations(
    *,
    run_id: str,
    docker_endpoint: str,
) -> tuple[ComposeInvocation, ComposeInvocation]:
    """Bind the mutable context name back to the authenticated socket."""
    return (
        ComposeInvocation(
            purpose="revalidate_context",
            arguments=(
                "docker",
                "--context",
                DOCKER_DESKTOP_CONTEXT,
                "context",
                "inspect",
                DOCKER_DESKTOP_CONTEXT,
                "--format",
                "{{json .}}",
            ),
            environment={"ECOMSRE_RUN_ID": run_id},
            timeout_seconds=10,
            read_only=True,
        ),
        ComposeInvocation(
            purpose="revalidate_daemon",
            arguments=(
                *docker_host_prefix(docker_endpoint),
                "info",
                "--format",
                "{{json .}}",
            ),
            environment={"ECOMSRE_RUN_ID": run_id},
            timeout_seconds=10,
            read_only=True,
        ),
    )


def up_environment(
    runner: LifecycleRunner,
    *,
    context: AuthenticatedOwnershipContext | None,
    image_lock: ImageLockManifest,
    project_root: Path,
    artifacts_root: Path | None,
    preflight_evidence: AuthenticatedPreflightEvidence | object | None = None,
) -> LifecycleExecution:
    """Perform a two-phase intent -> mutation -> exact ownership closeout."""
    if image_lock.status is not ImageLockStatus.LOCKED:
        return LifecycleExecution(
            result=_terminal(
                Outcome.BLOCKED_UPSTREAM,
                "IMAGE_LOCK_UNINITIALIZED",
            )
        )

    if (
        not isinstance(
            preflight_evidence,
            AuthenticatedPreflightEvidence,
        )
        or not preflight_evidence.is_current()
        or artifacts_root is None
    ):
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "PREFLIGHT_EVIDENCE_INVALID",
            )
        )
    run_id = preflight_evidence.run_id
    docker_endpoint = preflight_evidence.inputs.docker.endpoint
    if not _preflight_matches_requested_ownership(
        preflight_evidence,
        context=context,
        image_lock=image_lock,
    ):
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "RESOURCE_OWNERSHIP_UNKNOWN",
            )
        )

    command_results: list[CommandResult] = []
    config_invocation = build_compose_invocation(
        ComposeAction.CONFIG,
        project_root=project_root,
        run_id=run_id,
        docker_endpoint=docker_endpoint,
    )
    config_result = _execute(runner, config_invocation)
    command_results.append(config_result)
    if config_result.exit_code != 0:
        return LifecycleExecution(
            result=_terminal(
                Outcome.BLOCKED_UPSTREAM,
                "COMPOSE_CONFIG_UNAVAILABLE",
            ),
            command_results=tuple(command_results),
        )
    try:
        resolved = ResolvedComposeConfig.from_stdout(config_result.stdout)
    except ValueError:
        return LifecycleExecution(
            result=_terminal(
                Outcome.BLOCKED_UPSTREAM,
                "COMPOSE_CONFIG_HASH_MISMATCH",
            ),
            command_results=tuple(command_results),
        )
    if resolved.sha256 != image_lock.compose_config_sha256 or set(
        resolved.image_references
    ) != set(image_lock.allowed_source_references):
        return LifecycleExecution(
            result=_terminal(
                Outcome.BLOCKED_UPSTREAM,
                "COMPOSE_CONFIG_HASH_MISMATCH",
            ),
            resolved_compose=resolved,
            command_results=tuple(command_results),
        )
    try:
        parse_expected_port_bindings(resolved)
    except ValueError:
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "UNSAFE_PORT_EXPOSURE",
            ),
            resolved_compose=resolved,
            command_results=tuple(command_results),
        )
    try:
        _require_explicit_volume_plan(resolved, run_id=run_id)
    except ValueError:
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "UNSAFE_VOLUME_PLAN",
            ),
            resolved_compose=resolved,
            command_results=tuple(command_results),
        )

    cached_images = []
    image_parse_failed = False
    for invocation in build_image_inspection_invocations(
        image_lock,
        run_id=run_id,
        docker_endpoint=docker_endpoint,
    ):
        result = _execute(runner, invocation)
        command_results.append(result)
        try:
            cached_images.extend(parse_cached_images(result))
        except DiscoveryParseError:
            image_parse_failed = True
    verification = (
        None
        if image_parse_failed
        else verify_acceptance_image_lock(
            image_lock,
            cached_images=tuple(cached_images),
            observed_upstream_commit=UPSTREAM_COMMIT,
            observed_compose_config_sha256=resolved.sha256,
        )
    )
    if verification is None or not verification.passed:
        return LifecycleExecution(
            result=_terminal(
                Outcome.BLOCKED_UPSTREAM,
                "IMAGE_LOCK_MISMATCH",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
        )

    if not preflight_evidence.is_current():
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "PREFLIGHT_EVIDENCE_INVALID",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
        )

    artifact_paths: LifecycleArtifactPaths | None = None
    if context is None:
        created_at = datetime.now(UTC)
        intent = OwnershipIntent(
            schema_version="phase0.ownership-intent.v1",
            run_id=run_id,
            project_name=PROJECT_NAMESPACE,
            canonical_labels=_canonical_labels(run_id),
            expected_compose_files=_COMPOSE_FILES,
            expected_compose_sha256=resolved.sha256,
            expected_image_sources=resolved.image_references,
            pull_policy="never",
            build_policy="no-build",
            resources=(),
            created_at=created_at,
        )
        try:
            intent_paths = create_ownership_intent_artifacts(
                Path(artifacts_root),
                intent,
            )
            authenticated_intent = load_authenticated_ownership_intent(
                Path(artifacts_root),
                run_id,
            )
        except (OSError, OwnershipAuthorityError, ValueError):
            return LifecycleExecution(
                result=_terminal(
                    Outcome.UNSAFE,
                    "OWNERSHIP_INTENT_UNAVAILABLE",
                ),
                resolved_compose=resolved,
                image_verification=verification,
                command_results=tuple(command_results),
            )
        if (
            not authenticated_intent.is_authentic()
            or authenticated_intent.intent != intent
        ):
            return LifecycleExecution(
                result=_terminal(
                    Outcome.UNSAFE,
                    "OWNERSHIP_INTENT_UNAVAILABLE",
                ),
                resolved_compose=resolved,
                image_verification=verification,
                command_results=tuple(command_results),
            )
        artifact_paths = _artifact_paths(
            Path(artifacts_root),
            run_id,
            ownership_intent=intent_paths.intent_path,
        )
    else:
        artifact_paths = _artifact_paths(
            Path(artifacts_root),
            run_id,
            ownership_intent=(
                Path(artifacts_root)
                / "observer-visible"
                / run_id
                / "ownership-intent.json"
            ),
        )

    if not _revalidate_daemon_binding(
        runner,
        evidence=preflight_evidence,
        command_results=command_results,
    ):
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "DAEMON_BINDING_CHANGED",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
        )

    try:
        current_resources = _discover_verified_resources(
            runner,
            project_root=project_root,
            run_id=run_id,
            docker_endpoint=docker_endpoint,
            command_results=command_results,
        )
        if context is None:
            if current_resources:
                raise OwnershipError("fresh preflight snapshot is no longer empty")
        else:
            verify_owned_resources(
                current_resources,
                _compose_manifest(context),
            )
    except (
        DiscoveryParseError,
        OwnershipError,
        ValueError,
    ):
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "PREFLIGHT_SNAPSHOT_CHANGED",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
        )

    if not preflight_evidence.is_current():
        return LifecycleExecution(
            result=_terminal(
                Outcome.UNSAFE,
                "PREFLIGHT_EVIDENCE_INVALID",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
        )

    up_invocation = build_compose_invocation(
        ComposeAction.UP,
        project_root=project_root,
        run_id=run_id,
        docker_endpoint=docker_endpoint,
    )
    up_result = _execute(runner, up_invocation)
    command_results.append(up_result)
    if up_result.exit_code != 0:
        failure_context: AuthenticatedOwnershipContext | None = None
        try:
            failure_resources = _discover_verified_resources(
                runner,
                project_root=project_root,
                run_id=run_id,
                docker_endpoint=docker_endpoint,
                command_results=command_results,
            )
            if context is None:
                create_ownership_authority_artifacts(
                    Path(artifacts_root),
                    OwnershipManifest(
                        run_id=run_id,
                        resources=failure_resources,
                    ),
                    created_at=datetime.now(UTC),
                )
                failure_context = load_authenticated_ownership_context(
                    Path(artifacts_root),
                    run_id,
                )
            else:
                verify_owned_resources(
                    failure_resources,
                    _compose_manifest(context),
                )
                failure_context = context
        except (
            DiscoveryParseError,
            OSError,
            OwnershipAuthorityError,
            OwnershipError,
            ValueError,
        ):
            failure_context = None
        assert artifact_paths is not None
        artifact_paths = _refresh_existing_artifact_paths(
            artifact_paths,
            run_id=run_id,
        )
        artifact_paths = _persist_command_log_best_effort(
            artifact_paths,
            command_results,
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
        )
        artifact_paths = _persist_manual_diagnostic_best_effort(
            artifact_paths,
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            command_results=command_results,
            reason_code="COMPOSE_UP_MUTATION_UNCERTAIN",
        )
        return LifecycleExecution(
            result=_terminal(
                Outcome.MANUAL_INTERVENTION_REQUIRED,
                "COMPOSE_UP_MUTATION_UNCERTAIN",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
            ownership_context=failure_context,
            docker_endpoint=(
                docker_endpoint if failure_context is not None else None
            ),
            daemon_id=(
                preflight_evidence.inputs.docker.daemon_id
                if failure_context is not None
                else None
            ),
        )

    try:
        discovered = _discover_verified_resources(
            runner,
            project_root=project_root,
            run_id=run_id,
            docker_endpoint=docker_endpoint,
            command_results=command_results,
        )
    except (DiscoveryParseError, ValueError):
        assert artifact_paths is not None
        artifact_paths = _persist_manual_diagnostic_best_effort(
            _refresh_existing_artifact_paths(artifact_paths, run_id=run_id),
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            command_results=command_results,
            reason_code="POST_UP_DISCOVERY_FAILED",
        )
        return LifecycleExecution(
            result=_terminal(
                Outcome.MANUAL_INTERVENTION_REQUIRED,
                "POST_UP_DISCOVERY_FAILED",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
            docker_endpoint=docker_endpoint,
            daemon_id=preflight_evidence.inputs.docker.daemon_id,
        )

    try:
        _require_resolved_resource_completeness(discovered, resolved)
    except (OwnershipError, ValueError):
        assert artifact_paths is not None
        artifact_paths = _persist_manual_diagnostic_best_effort(
            _refresh_existing_artifact_paths(artifact_paths, run_id=run_id),
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            command_results=command_results,
            reason_code="POST_UP_RESOURCE_COMPLETENESS_FAILED",
        )
        return LifecycleExecution(
            result=_terminal(
                Outcome.MANUAL_INTERVENTION_REQUIRED,
                "POST_UP_RESOURCE_COMPLETENESS_FAILED",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
            docker_endpoint=docker_endpoint,
            daemon_id=preflight_evidence.inputs.docker.daemon_id,
        )

    try:
        _create_or_verify_post_up_ownership_manifest(
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            discovered=discovered,
            context=context,
        )
    except (
        OSError,
        OwnershipAuthorityError,
        OwnershipError,
        ValueError,
    ):
        assert artifact_paths is not None
        artifact_paths = _persist_manual_diagnostic_best_effort(
            _refresh_existing_artifact_paths(artifact_paths, run_id=run_id),
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            command_results=command_results,
            reason_code="POST_UP_OWNERSHIP_AUTHENTICATION_FAILED",
            failure_stage="ownership_manifest",
        )
        return LifecycleExecution(
            result=_terminal(
                Outcome.MANUAL_INTERVENTION_REQUIRED,
                "POST_UP_OWNERSHIP_AUTHENTICATION_FAILED",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
            docker_endpoint=docker_endpoint,
            daemon_id=preflight_evidence.inputs.docker.daemon_id,
        )

    try:
        context = _load_and_authenticate_post_up_ownership_context(
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            context=context,
        )
    except (OSError, OwnershipAuthorityError, ValueError):
        assert artifact_paths is not None
        artifact_paths = _persist_manual_diagnostic_best_effort(
            _refresh_existing_artifact_paths(artifact_paths, run_id=run_id),
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            command_results=command_results,
            reason_code="POST_UP_OWNERSHIP_AUTHENTICATION_FAILED",
            failure_stage="ownership_context_authentication",
        )
        return LifecycleExecution(
            result=_terminal(
                Outcome.MANUAL_INTERVENTION_REQUIRED,
                "POST_UP_OWNERSHIP_AUTHENTICATION_FAILED",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
            docker_endpoint=docker_endpoint,
            daemon_id=preflight_evidence.inputs.docker.daemon_id,
        )

    assert context is not None
    assert artifact_paths is not None
    artifact_paths = _refresh_existing_artifact_paths(
        artifact_paths,
        run_id=run_id,
    )
    try:
        artifact_paths = _persist_evaluator_success_artifact(
            artifact_paths,
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            resolved=resolved,
        )
        artifact_paths = _persist_observer_success_artifacts(
            artifact_paths,
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            resolved=resolved,
            command_results=command_results,
        )
    except (OSError, ValueError):
        artifact_paths = _refresh_existing_artifact_paths(
            artifact_paths,
            run_id=run_id,
        )
        artifact_paths = _persist_manual_diagnostic_best_effort(
            artifact_paths,
            artifacts_root=Path(artifacts_root),
            run_id=run_id,
            command_results=command_results,
            reason_code="POST_UP_EVIDENCE_PERSISTENCE_FAILED",
        )
        return LifecycleExecution(
            result=_terminal(
                Outcome.MANUAL_INTERVENTION_REQUIRED,
                "POST_UP_EVIDENCE_PERSISTENCE_FAILED",
            ),
            resolved_compose=resolved,
            image_verification=verification,
            command_results=tuple(command_results),
            artifact_paths=artifact_paths,
            ownership_context=context,
            docker_endpoint=docker_endpoint,
            daemon_id=preflight_evidence.inputs.docker.daemon_id,
        )

    return LifecycleExecution(
        result=_terminal(Outcome.SUCCESS, "ENVIRONMENT_STARTED"),
        resolved_compose=resolved,
        image_verification=verification,
        command_results=tuple(command_results),
        artifact_paths=artifact_paths,
        ownership_context=context,
        docker_endpoint=docker_endpoint,
        daemon_id=preflight_evidence.inputs.docker.daemon_id,
    )


def status_environment(
    runner: LifecycleRunner,
    *,
    context: AuthenticatedOwnershipContext,
    project_root: Path,
    docker_endpoint: str,
) -> TerminalResult:
    return _read_environment_state(
        runner,
        action=ComposeAction.STATUS,
        context=context,
        project_root=project_root,
        docker_endpoint=docker_endpoint,
    )


def health_environment(
    runner: LifecycleRunner,
    *,
    context: AuthenticatedOwnershipContext,
    project_root: Path,
    docker_endpoint: str,
    readiness: ReadinessEvidence | None = None,
) -> TerminalResult:
    if not _context_is_authentic(context):
        return _terminal(Outcome.UNSAFE, "RESOURCE_OWNERSHIP_UNKNOWN")
    if readiness is None:
        return _terminal(
            Outcome.BLOCKED_ENVIRONMENT,
            "READINESS_EVIDENCE_UNAVAILABLE",
        )
    if readiness.run_id != context.run_id or not readiness.all_passed:
        return _terminal(
            Outcome.BLOCKED_ENVIRONMENT,
            "READINESS_INCOMPLETE",
        )
    return _read_environment_state(
        runner,
        action=ComposeAction.HEALTH,
        context=context,
        project_root=project_root,
        docker_endpoint=docker_endpoint,
    )


def down_environment(
    runner: LifecycleRunner,
    *,
    context: AuthenticatedOwnershipContext,
    project_root: Path,
    docker_endpoint: str,
) -> TerminalResult:
    if not _context_is_authentic(context):
        return _terminal(Outcome.UNSAFE, "RESOURCE_OWNERSHIP_UNKNOWN")
    command_results: list[CommandResult] = []
    try:
        discovered = _discover_verified_resources(
            runner,
            project_root=project_root,
            run_id=context.run_id,
            docker_endpoint=docker_endpoint,
            command_results=command_results,
        )
        verify_owned_resources(discovered, _compose_manifest(context))
    except (DiscoveryParseError, OwnershipError, ValueError):
        return _terminal(Outcome.UNSAFE, "RESOURCE_OWNERSHIP_UNKNOWN")

    down_invocation = build_compose_invocation(
        ComposeAction.DOWN,
        project_root=project_root,
        run_id=context.run_id,
        docker_endpoint=docker_endpoint,
    )
    result = _execute(runner, down_invocation)
    if result.exit_code != 0:
        return _terminal(
            Outcome.MANUAL_INTERVENTION_REQUIRED,
            "OWNED_COMPOSE_DOWN_FAILED",
        )
    return _terminal(Outcome.SUCCESS, "OWNED_ENVIRONMENT_STOPPED")


def _read_environment_state(
    runner: LifecycleRunner,
    *,
    action: ComposeAction,
    context: AuthenticatedOwnershipContext,
    project_root: Path,
    docker_endpoint: str,
) -> TerminalResult:
    if not _context_is_authentic(context):
        return _terminal(Outcome.UNSAFE, "RESOURCE_OWNERSHIP_UNKNOWN")
    invocation = build_compose_invocation(
        action,
        project_root=project_root,
        run_id=context.run_id,
        docker_endpoint=docker_endpoint,
    )
    result = _execute(runner, invocation)
    if result.exit_code != 0:
        return _terminal(Outcome.BLOCKED_ENVIRONMENT, "COMPOSE_STATUS_FAILED")
    if action is ComposeAction.HEALTH:
        try:
            records = _parse_json_records(result.stdout)
        except ValueError:
            return _terminal(
                Outcome.BLOCKED_ENVIRONMENT,
                "ENVIRONMENT_HEALTH_UNKNOWN",
            )
        if not records or any(
            str(record.get("State", "")).lower() != "running"
            or str(record.get("Health", "")).lower() not in {"", "healthy"}
            for record in records
        ):
            return _terminal(
                Outcome.BLOCKED_ENVIRONMENT,
                "ENVIRONMENT_UNHEALTHY",
            )
        return _terminal(Outcome.SUCCESS, "ENVIRONMENT_HEALTHY")
    return _terminal(Outcome.SUCCESS, "ENVIRONMENT_STATUS_CAPTURED")


def _invocation_is_allowlisted(
    invocation: ComposeInvocation,
    *,
    run_id: str,
) -> bool:
    purpose = invocation.purpose
    arguments = invocation.arguments
    if purpose == "revalidate_context":
        return invocation.read_only and arguments == (
            "docker",
            "--context",
            DOCKER_DESKTOP_CONTEXT,
            "context",
            "inspect",
            DOCKER_DESKTOP_CONTEXT,
            "--format",
            "{{json .}}",
        )
    if purpose == "revalidate_daemon":
        return (
            invocation.read_only
            and len(arguments) == 6
            and arguments[:2] == ("docker", "--host")
            and is_local_unix_docker_endpoint(arguments[2])
            and arguments[3:] == ("info", "--format", "{{json .}}")
        )
    if (
        len(arguments) < 3
        or arguments[:2] != ("docker", "--host")
        or not is_local_unix_docker_endpoint(arguments[2])
    ):
        return False
    docker_prefix = arguments[:3]
    if purpose in {action.value for action in ComposeAction}:
        if len(arguments) < 17 or arguments[:6] != (
            *docker_prefix,
            "compose",
            "--project-name",
            PROJECT_NAMESPACE,
        ):
            return False
        project_directory = Path(arguments[7])
        if (
            not project_directory.is_absolute()
            or project_directory.name != "opentelemetry-demo"
            or project_directory.parent.name != "third_party"
            or ".." in project_directory.parts
        ):
            return False
        try:
            project_root = project_directory.parents[1]
        except IndexError:
            return False
        base = (
            *docker_prefix,
            "compose",
            "--project-name",
            PROJECT_NAMESPACE,
            "--project-directory",
            str(project_directory),
            "--env-file",
            str(project_directory / ".env"),
            "--file",
            str(project_directory / "compose.yaml"),
            "--file",
            str(project_directory / "compose.observability.yaml"),
            "--file",
            str(project_root / "config" / "phase0" / "compose.phase0.yaml"),
        )
        suffixes = {
            "config": ("config", "--format", "json"),
            "up": ("up", "--detach", "--pull", "never", "--no-build"),
            "status": ("ps", "--all", "--format", "json"),
            "health": ("ps", "--all", "--format", "json"),
            "down": ("down",),
        }
        expected_read_only = purpose in {"config", "status", "health"}
        return (
            arguments == base + suffixes[purpose]
            and invocation.read_only is expected_read_only
        )
    if purpose == "inspect_image":
        return (
            invocation.read_only
            and len(arguments) == 8
            and arguments[:7]
            == (
                *docker_prefix,
                "image",
                "inspect",
                "--platform",
                "linux/arm64",
            )
            and bool(arguments[7])
            and not arguments[7].startswith("-")
            and not any(character.isspace() for character in arguments[7])
        )
    if purpose in {
        f"{scope}_{kind}s"
        for scope in ("potential", "owned")
        for kind in ("container", "network", "volume")
    }:
        return invocation.read_only and arguments == _discovery_arguments(
            purpose,
            run_id=run_id,
            docker_endpoint=arguments[2],
        )
    return False


def _discovery_arguments(
    purpose: str,
    *,
    run_id: str,
    docker_endpoint: str,
) -> tuple[str, ...]:
    scope, plural = purpose.split("_", 1)
    kind = plural.removesuffix("s")
    if scope not in {"potential", "owned"} or kind not in {
        "container",
        "network",
        "volume",
    }:
        raise ValueError("discovery purpose is not allowlisted")
    command = [*docker_host_prefix(docker_endpoint), kind, "ls"]
    if kind == "container":
        command.append("--all")
    if kind in {"container", "network"}:
        command.append("--no-trunc")
    filters = [
        f"label={_COMPOSE_PROJECT_LABEL}={PROJECT_NAMESPACE}",
    ]
    if scope == "owned":
        filters.extend(
            (
                f"label={PROJECT_LABEL}={PROJECT_NAMESPACE}",
                f"label={RUN_LABEL}={run_id}",
            )
        )
    for value in filters:
        command.extend(("--filter", value))
    command.extend(("--format", "{{json .}}"))
    return tuple(command)


def _discover_verified_resources(
    runner: LifecycleRunner,
    *,
    project_root: Path,
    run_id: str,
    docker_endpoint: str,
    command_results: list[CommandResult],
) -> tuple[OwnedResource, ...]:
    del project_root
    grouped: dict[tuple[str, str], tuple[OwnedResource, ...]] = {}
    for invocation in build_ownership_discovery_invocations(
        project_root=Path("."),
        run_id=run_id,
        docker_endpoint=docker_endpoint,
    ):
        result = _execute(runner, invocation)
        command_results.append(result)
        if result.exit_code != 0:
            raise ValueError("ownership discovery failed")
        scope, plural = invocation.purpose.split("_", 1)
        kind = plural.removesuffix("s")
        required_labels = {
            _COMPOSE_PROJECT_LABEL: PROJECT_NAMESPACE,
        }
        if scope == "owned":
            required_labels.update(_canonical_labels(run_id))
        grouped[(scope, kind)] = _parse_owned_resources(
            result.stdout,
            kind=kind,
            required_labels=required_labels,
        )

    owned: list[OwnedResource] = []
    for kind in ("container", "network", "volume"):
        potential = grouped[("potential", kind)]
        proven = grouped[("owned", kind)]
        if potential != proven:
            raise ValueError("potential and owned resource sets differ")
        owned.extend(proven)
    return tuple(
        sorted(
            owned,
            key=lambda resource: (
                resource.kind,
                resource.name,
                resource.resource_id,
            ),
        )
    )


def parse_expected_port_bindings(
    resolved: ResolvedComposeConfig,
) -> tuple[ExpectedPortBinding, ...]:
    """Parse explicit and target-only ports from canonical Compose JSON."""
    try:
        payload = json.loads(resolved.stdout)
        services = payload["services"]
        if not isinstance(services, dict) or not services:
            raise ValueError
        expected: list[ExpectedPortBinding] = []
        for service_name, service in services.items():
            if (
                not isinstance(service_name, str)
                or not service_name
                or not isinstance(service, dict)
            ):
                raise ValueError
            container_name = str(service.get("container_name", ""))
            if not container_name:
                raise ValueError
            ports = service.get("ports", [])
            if not isinstance(ports, list):
                raise ValueError
            for port in ports:
                if not isinstance(port, dict):
                    raise ValueError
                protocol = str(port.get("protocol", "tcp")).lower()
                target = port.get("target")
                target_text = str(target)
                if (
                    protocol not in {"tcp", "udp"}
                    or isinstance(target, bool)
                    or not target_text.isdecimal()
                    or not 1 <= int(target_text) <= 65535
                ):
                    raise ValueError
                published = port.get("published")
                if published is None:
                    expected.append(
                        ExpectedPortBinding(
                            service=service_name,
                            container_name=container_name,
                            target_port=int(target_text),
                            protocol=protocol,
                        )
                    )
                    continue
                published_text = str(published)
                if (
                    isinstance(published, bool)
                    or not published_text.isdecimal()
                    or not 1 <= int(published_text) <= 65535
                ):
                    raise ValueError
                host_ip, _host_family = _normalize_host_binding(
                    str(port.get("host_ip") or "0.0.0.0")
                )
                expected.append(
                    ExpectedPortBinding(
                        service=service_name,
                        container_name=container_name,
                        target_port=int(target_text),
                        protocol=protocol,
                        published_port=int(published_text),
                        host_ip=host_ip,
                    )
                )
        identities = {
            (
                binding.service,
                binding.container_name,
                binding.target_port,
                binding.protocol,
                binding.published_port,
                binding.host_ip,
            )
            for binding in expected
        }
        if len(identities) != len(expected):
            raise ValueError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "resolved Compose port metadata is incomplete or ambiguous: "
            f"{error}"
        ) from error
    bindings = tuple(
        sorted(
            expected,
            key=lambda binding: (
                binding.service,
                binding.container_name,
                binding.target_port,
                binding.protocol,
                binding.published_port or 0,
                binding.host_ip or "",
            ),
        )
    )
    _audit_minimal_loopback_port_plan(bindings)
    return bindings


def _audit_minimal_loopback_port_plan(
    bindings: tuple[ExpectedPortBinding, ...],
) -> None:
    for binding in bindings:
        allowed_targets = _HOST_PORT_ALLOWLIST.get(binding.service)
        if allowed_targets is None or binding.target_port not in allowed_targets:
            raise ValueError("published port service/target is outside allowlist")
        if (
            binding.published_port is None
            or binding.host_ip not in {"127.0.0.1", "::1"}
        ):
            raise ValueError("published port must use an explicit loopback binding")


def _require_resolved_resource_completeness(
    discovered: tuple[OwnedResource, ...],
    resolved: ResolvedComposeConfig,
) -> None:
    try:
        payload = json.loads(resolved.stdout)
        services = payload["services"]
        if not isinstance(services, dict) or not services:
            raise ValueError
        expected_containers = {
            (str(service_name), str(service["container_name"]))
            for service_name, service in services.items()
            if isinstance(service_name, str)
            and service_name
            and isinstance(service, dict)
            and service.get("container_name")
        }
        if len(expected_containers) != len(services):
            raise ValueError
        expected_ports = parse_expected_port_bindings(resolved)
        volumes = payload.get("volumes", {})
        if not isinstance(volumes, dict):
            raise ValueError
        expected_volumes = {
            str(definition.get("name") or f"{PROJECT_NAMESPACE}_{name}")
            for name, definition in volumes.items()
            if isinstance(definition, dict)
        }
        if len(expected_volumes) != len(volumes):
            raise ValueError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "resolved Compose lacks resource completeness metadata"
        ) from error
    actual = {
        "network": {
            resource.name for resource in discovered if resource.kind == "network"
        },
        "volume": {
            resource.name for resource in discovered if resource.kind == "volume"
        },
    }
    actual_containers = {
        (
            resource.labels.get(_COMPOSE_SERVICE_LABEL, ""),
            resource.name,
        ): resource.resource_id
        for resource in discovered
        if resource.kind == "container"
    }
    if len(actual_containers) != sum(
        resource.kind == "container" for resource in discovered
    ):
        raise ValueError("container identity is duplicate or ambiguous")
    actual_ports: list[_ObservedPortBinding] = []
    for resource in discovered:
        if resource.kind != "port":
            continue
        binding, owner_id = _port_binding_identity(resource)
        owner_key = (binding.service, binding.container_name)
        if actual_containers.get(owner_key) != owner_id:
            raise ValueError("published port owner identity is inconsistent")
        actual_ports.append(binding)
    _match_expected_port_bindings(expected_ports, tuple(actual_ports))
    if (
        set(actual_containers) != expected_containers
        or actual["network"] != {PROJECT_NAMESPACE}
        or actual["volume"] != expected_volumes
    ):
        raise ValueError("post-up resource inventory is incomplete")


def _port_binding_identity(
    resource: OwnedResource,
) -> tuple[_ObservedPortBinding, str]:
    evidence: dict[str, str] = {}
    for item in resource.identity_evidence:
        key, separator, value = item.partition(":")
        if not separator or key in evidence:
            raise ValueError("published port identity evidence is ambiguous")
        evidence[key] = value
    required = {
        "port",
        "container",
        "container_name",
        "service",
        "host_ip",
        "host_family",
        "published_port",
        "target_port",
        "protocol",
        "binding",
        "raw_binding",
    }
    if set(evidence) != required:
        raise ValueError("published port identity evidence is incomplete")
    protocol = evidence["protocol"]
    published_text = evidence["published_port"]
    target_text = evidence["target_port"]
    expected_binding = (
        f"{evidence['host_ip']}:{published_text}->{target_text}/{protocol}"
    )
    binding_payload = {
        "service": evidence["service"],
        "container_name": evidence["container_name"],
        "container_id": evidence["container"],
        "host_ip": evidence["host_ip"],
        "host_family": evidence["host_family"],
        "published_port": int(published_text),
        "target_port": int(target_text),
        "protocol": protocol,
    }
    expected_port_id = (
        f"port-binding:{sha256_bytes(canonical_json_bytes(binding_payload))}"
    )
    expected_name = (
        f"{evidence['service']}:{target_text}->{published_text}/"
        f"{protocol}@{evidence['host_family']}"
    )
    if (
        protocol not in {"tcp", "udp"}
        or not published_text.isdecimal()
        or not target_text.isdecimal()
        or evidence["host_family"] not in {"ipv4", "ipv6"}
        or resource.name != expected_name
        or resource.resource_id != expected_port_id
        or evidence["port"] != expected_port_id
        or evidence["binding"] != expected_binding
        or resource.labels.get(_COMPOSE_SERVICE_LABEL) != evidence["service"]
    ):
        raise ValueError("published port identity evidence conflicts")
    return (
        _ObservedPortBinding(
            service=evidence["service"],
            container_name=evidence["container_name"],
            container_id=evidence["container"],
            host_ip=evidence["host_ip"],
            host_family=evidence["host_family"],
            published_port=int(published_text),
            target_port=int(target_text),
            protocol=protocol,
            raw_binding=evidence["raw_binding"],
        ),
        evidence["container"],
    )


def _match_expected_port_bindings(
    expected: tuple[ExpectedPortBinding, ...],
    actual: tuple[_ObservedPortBinding, ...],
) -> None:
    actual_identities = {
        (
            binding.service,
            binding.container_name,
            binding.container_id,
            binding.host_ip,
            binding.host_family,
            binding.published_port,
            binding.target_port,
            binding.protocol,
            binding.raw_binding,
        )
        for binding in actual
    }
    if len(actual_identities) != len(actual):
        raise ValueError("published port binding is duplicate or ambiguous")
    unmatched = list(actual)
    for expected_binding in expected:
        candidates = [
            binding
            for binding in unmatched
            if binding.service == expected_binding.service
            and binding.container_name == expected_binding.container_name
            and binding.target_port == expected_binding.target_port
            and binding.protocol == expected_binding.protocol
            and (
                expected_binding.published_port is None
                or binding.published_port == expected_binding.published_port
            )
        ]
        if not candidates:
            raise ValueError("expected published port binding is missing")
        if (
            expected_binding.published_port is None
            and len({binding.published_port for binding in candidates}) != 1
        ):
            raise ValueError("target-only port has ambiguous runtime assignments")
        _require_equivalent_host_bindings(
            expected_binding,
            tuple(candidates),
        )
        for candidate in candidates:
            unmatched.remove(candidate)
    if unmatched:
        raise ValueError("unexpected published port binding was observed")


def _require_equivalent_host_bindings(
    expected: ExpectedPortBinding,
    actual: tuple[_ObservedPortBinding, ...],
) -> None:
    hosts = {(binding.host_ip, binding.host_family) for binding in actual}
    if len(hosts) != len(actual):
        raise ValueError("published port host binding is duplicate")
    if expected.published_port is None or expected.host_ip is None:
        raise ValueError("published port lacks explicit loopback intent")
    assert expected.host_ip is not None
    expected_host, expected_family = _normalize_host_binding(expected.host_ip)
    if (
        expected_host in {"127.0.0.1", "::1"}
        and hosts == {(expected_host, expected_family)}
    ):
        return
    raise ValueError("explicit published host binding differs")


def _normalize_host_binding(host_ip: str) -> tuple[str, str]:
    normalized = host_ip.strip()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("host binding is invalid")
    return normalized, ("ipv6" if ":" in normalized else "ipv4")


def _compose_manifest(
    context: AuthenticatedOwnershipContext,
) -> OwnershipManifest:
    return OwnershipManifest(
        run_id=context.run_id,
        resources=tuple(
            resource
            for resource in context.manifest.resources
            if resource.kind in _COMPOSE_RESOURCE_KINDS
        ),
    )


def _execute(
    runner: LifecycleRunner,
    invocation: ComposeInvocation,
) -> CommandResult:
    try:
        result = runner.run(
            invocation.arguments,
            timeout_seconds=invocation.timeout_seconds,
            environment=invocation.environment,
        )
        if result.arguments != invocation.arguments:
            raise ValueError("runner result arguments do not match invocation")
    except subprocess.TimeoutExpired as error:
        stdout = (
            error.stdout.decode("utf-8", errors="replace")
            if isinstance(error.stdout, bytes)
            else str(error.stdout or "")
        )
        stderr = (
            error.stderr.decode("utf-8", errors="replace")
            if isinstance(error.stderr, bytes)
            else str(error.stderr or "")
        )
        return CommandResult(
            arguments=invocation.arguments,
            exit_code=124,
            stdout=stdout,
            stderr=f"LIFECYCLE_TIMEOUT\n{stderr}",
        )
    except Exception as error:
        return CommandResult(
            arguments=invocation.arguments,
            exit_code=126,
            stdout="",
            stderr=f"LIFECYCLE_RUNNER_EXCEPTION:{type(error).__name__}",
        )
    return result


def _revalidate_daemon_binding(
    runner: LifecycleRunner,
    *,
    evidence: AuthenticatedPreflightEvidence,
    command_results: list[CommandResult],
) -> bool:
    docker = evidence.inputs.docker
    invocations = build_daemon_revalidation_invocations(
        run_id=evidence.run_id,
        docker_endpoint=docker.endpoint,
    )
    results = tuple(_execute(runner, invocation) for invocation in invocations)
    command_results.extend(results)
    if any(result.exit_code != 0 for result in results):
        return False
    try:
        context_payload = json.loads(results[0].stdout)
        info_payload = json.loads(results[1].stdout)
        if not isinstance(context_payload, dict) or not isinstance(
            info_payload,
            dict,
        ):
            return False
        endpoints = context_payload.get("Endpoints")
        if not isinstance(endpoints, dict):
            return False
        docker_endpoint = endpoints.get("docker")
        if not isinstance(docker_endpoint, dict):
            return False
        observed_endpoint = str(docker_endpoint.get("Host", ""))
        observed_architecture = str(info_payload.get("Architecture", "")).lower()
        if observed_architecture == "aarch64":
            observed_architecture = "arm64"
        return (
            str(context_payload.get("Name", ""))
            == docker.context_name
            == DOCKER_DESKTOP_CONTEXT
            and observed_endpoint == docker.endpoint
            and is_local_unix_docker_endpoint(observed_endpoint)
            and str(info_payload.get("ID", "")) == docker.daemon_id
            and str(info_payload.get("OSType", "")).lower() == docker.server_os_type
            and observed_architecture == docker.server_architecture
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


def _context_is_authentic(
    context: AuthenticatedOwnershipContext | None,
) -> bool:
    return (
        isinstance(context, AuthenticatedOwnershipContext)
        and context.is_authentic()
        and context.project_name == PROJECT_NAMESPACE
        and context.manifest.run_id == context.run_id
        and context.canonical_labels == _canonical_labels(context.run_id)
    )


def _preflight_matches_requested_ownership(
    evidence: AuthenticatedPreflightEvidence,
    *,
    context: AuthenticatedOwnershipContext | None,
    image_lock: ImageLockManifest,
) -> bool:
    inputs = evidence.inputs
    if (
        evidence.result.outcome is not Outcome.SUCCESS
        or evidence.run_id
        != (
            context.run_id
            if isinstance(context, AuthenticatedOwnershipContext)
            else evidence.run_id
        )
        or inputs.observed_upstream_commit != UPSTREAM_COMMIT
        or inputs.observed_compose_config_sha256 != image_lock.compose_config_sha256
        or inputs.expected_compose_config_sha256 != image_lock.compose_config_sha256
        or not inputs.image_lock_verification.passed
        or inputs.pull_policy != "never"
        or inputs.docker.context_name != DOCKER_DESKTOP_CONTEXT
        or not is_local_unix_docker_endpoint(inputs.docker.endpoint)
        or not inputs.docker.daemon_id.strip()
        or inputs.docker.server_os_type != "linux"
        or inputs.docker.server_architecture != "arm64"
    ):
        return False
    preflight_context = inputs.ownership_context
    if context is None:
        return preflight_context is None
    return (
        _context_is_authentic(context)
        and isinstance(
            preflight_context,
            AuthenticatedOwnershipContext,
        )
        and _context_is_authentic(preflight_context)
        and preflight_context.run_id == context.run_id
        and preflight_context.manifest_sha256 == context.manifest_sha256
        and preflight_context.manifest == context.manifest
    )


def _parse_owned_resources(
    stdout: str,
    *,
    kind: str,
    required_labels: dict[str, str],
) -> tuple[OwnedResource, ...]:
    resources: list[OwnedResource] = []
    for record in _parse_json_records(stdout):
        name = str(record.get("Names") or record.get("Name") or "")
        resource_id = str(record.get("ID") or (name if kind == "volume" else ""))
        labels = _parse_labels(str(record.get("Labels", "")))
        if (
            not name
            or not resource_id
            or any(labels.get(key) != value for key, value in required_labels.items())
        ):
            raise ValueError("resource listing lacks exact ownership identity")
        identity_evidence = (f"{kind}:{resource_id}",)
        compose_service = ""
        if kind == "container":
            compose_service = labels.get(_COMPOSE_SERVICE_LABEL, "")
            if not compose_service:
                raise ValueError("container listing lacks Compose service identity")
            identity_evidence = (
                f"container:{resource_id}",
                f"container_name:{name}",
                f"service:{compose_service}",
            )
        resources.append(
            OwnedResource(
                kind=kind,
                name=name,
                resource_id=resource_id,
                labels=labels,
                identity_evidence=identity_evidence,
            )
        )
        if kind == "container":
            for match in _parse_container_port_segments(str(record.get("Ports", ""))):
                protocol = match.group("protocol")
                host_port = int(match.group("host_port"))
                raw_host_ip = match.group("host")
                host_ip, host_family = _normalize_host_binding(raw_host_ip)
                target_port = int(match.group("container_port"))
                binding_payload = {
                    "service": compose_service,
                    "container_name": name,
                    "container_id": resource_id,
                    "host_ip": host_ip,
                    "host_family": host_family,
                    "published_port": host_port,
                    "target_port": target_port,
                    "protocol": protocol,
                }
                port_id = (
                    "port-binding:"
                    f"{sha256_bytes(canonical_json_bytes(binding_payload))}"
                )
                port_name = (
                    f"{compose_service}:{target_port}->{host_port}/"
                    f"{protocol}@{host_family}"
                )
                raw_binding = f"{raw_host_ip}:{host_port}->{target_port}/{protocol}"
                resources.append(
                    OwnedResource(
                        kind="port",
                        name=port_name,
                        resource_id=port_id,
                        labels=labels,
                        identity_evidence=(
                            f"port:{port_id}",
                            f"container:{resource_id}",
                            f"container_name:{name}",
                            f"service:{compose_service}",
                            f"host_ip:{host_ip}",
                            f"host_family:{host_family}",
                            f"published_port:{host_port}",
                            f"target_port:{target_port}",
                            f"protocol:{protocol}",
                            (
                                f"binding:{host_ip}:"
                                f"{host_port}->{target_port}"
                                f"/{protocol}"
                            ),
                            f"raw_binding:{raw_binding}",
                        ),
                    )
                )
    if len(_resource_identities(tuple(resources))) != len(resources):
        raise ValueError("resource listing contains duplicate identities")
    return tuple(
        sorted(
            resources,
            key=lambda resource: (
                resource.kind,
                resource.name,
                resource.resource_id,
            ),
        )
    )


def _parse_container_port_segments(
    value: str,
) -> tuple[re.Match[str], ...]:
    published_pattern = re.compile(
        r"(?P<host>\[[^\]]+\]|[^,:]+):"
        r"(?P<host_port>\d+)->"
        r"(?P<container_port>\d+)/(?P<protocol>tcp|udp)"
    )
    exposure_pattern = re.compile(r"\d+(?:-\d+)?/(?:tcp|udp)")
    matches: list[re.Match[str]] = []
    for raw_segment in value.split(","):
        segment = raw_segment.strip()
        if not segment:
            continue
        if "->" in segment:
            match = published_pattern.fullmatch(segment)
            if match is None:
                raise DiscoveryParseError(
                    "Docker port listing contains an unknown arrow segment"
                )
            matches.append(match)
        elif exposure_pattern.fullmatch(segment) is None:
            raise DiscoveryParseError(
                "Docker port listing contains an unknown exposure segment"
            )
    return tuple(matches)


def _resource_identities(
    resources: tuple[OwnedResource, ...],
) -> set[tuple[str, str, str]]:
    return {
        (resource.kind, resource.resource_id, resource.name) for resource in resources
    }


def _parse_json_records(stdout: str) -> tuple[dict[str, object], ...]:
    if not stdout.strip():
        return ()
    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            return (payload,)
        if isinstance(payload, list) and all(
            isinstance(item, dict) for item in payload
        ):
            return tuple(payload)
    except json.JSONDecodeError:
        pass
    records: list[dict[str, object]] = []
    try:
        for line in stdout.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError
            records.append(record)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("resource listing is malformed") from error
    return tuple(records)


def _parse_labels(value: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for item in value.split(","):
        key, separator, label_value = item.partition("=")
        if separator and key:
            labels[key] = label_value
    return labels


def _artifact_paths(
    root: Path,
    run_id: str,
    *,
    ownership_intent: Path | None,
) -> LifecycleArtifactPaths:
    return LifecycleArtifactPaths(
        artifacts_root=root,
        ownership_intent=_existing_regular_artifact(
            root,
            ownership_intent,
        ),
    )


def _create_or_verify_post_up_ownership_manifest(
    *,
    artifacts_root: Path,
    run_id: str,
    discovered: tuple[OwnedResource, ...],
    context: AuthenticatedOwnershipContext | None,
) -> None:
    """Create the post-up manifest, or verify it against existing authority."""
    if context is None:
        create_ownership_authority_artifacts(
            artifacts_root,
            OwnershipManifest(
                run_id=run_id,
                resources=discovered,
            ),
            created_at=datetime.now(UTC),
        )
        return
    verify_owned_resources(discovered, _compose_manifest(context))


def _load_and_authenticate_post_up_ownership_context(
    *,
    artifacts_root: Path,
    run_id: str,
    context: AuthenticatedOwnershipContext | None,
) -> AuthenticatedOwnershipContext:
    """Load and authenticate context only after manifest handling succeeded."""
    authenticated = (
        load_authenticated_ownership_context(artifacts_root, run_id)
        if context is None
        else context
    )
    if not _context_is_authentic(authenticated):
        raise OwnershipAuthorityError("post-up ownership context is not authentic")
    return authenticated


def _persist_evaluator_success_artifact(
    paths: LifecycleArtifactPaths,
    *,
    artifacts_root: Path,
    run_id: str,
    resolved: ResolvedComposeConfig,
) -> LifecycleArtifactPaths:
    raw_payload = {
        "schema_version": "phase0.resolved-compose-raw.v1",
        "stdout": resolved.stdout,
        "sha256": resolved.sha256,
        "image_sources": resolved.image_references,
        "compose_files": _COMPOSE_FILES,
        "pull_policy": "never",
        "build_policy": "no-build",
    }
    with EvaluatorEvidenceStore(artifacts_root, run_id) as evaluator:
        raw_artifact = evaluator.write_immutable(
            "lifecycle/resolved-compose.json",
            raw_payload,
        )
    return paths.model_copy(update={"resolved_compose_raw": raw_artifact.path})


def _persist_observer_success_artifacts(
    paths: LifecycleArtifactPaths,
    *,
    artifacts_root: Path,
    run_id: str,
    resolved: ResolvedComposeConfig,
    command_results: list[CommandResult],
) -> LifecycleArtifactPaths:
    observer_payload = {
        "schema_version": "phase0.resolved-compose-summary.v1",
        "sanitized_config": _project_resolved_compose_for_observer(resolved.stdout),
        "sha256": resolved.sha256,
        "image_sources": resolved.image_references,
        "compose_files": _COMPOSE_FILES,
        "pull_policy": "never",
        "build_policy": "no-build",
    }
    with ObserverEvidenceStore(artifacts_root, run_id) as observer:
        resolved_artifact = observer.write_immutable(
            "lifecycle/resolved-compose.json",
            observer_payload,
        )
        command_artifact = observer.write_immutable(
            "lifecycle/commands.json",
            _command_log_payload(command_results),
        )
    return paths.model_copy(
        update={
            "resolved_compose": resolved_artifact.path,
            "command_log": command_artifact.path,
        }
    )


def _persist_command_log_best_effort(
    paths: LifecycleArtifactPaths,
    command_results: list[CommandResult],
    *,
    artifacts_root: Path,
    run_id: str,
) -> LifecycleArtifactPaths:
    try:
        with ObserverEvidenceStore(artifacts_root, run_id) as observer:
            artifact = observer.write_immutable(
                "lifecycle/commands.json",
                _command_log_payload(command_results),
            )
    except (OSError, ValueError):
        return _refresh_existing_artifact_paths(paths, run_id=run_id)
    return paths.model_copy(update={"command_log": artifact.path})


def _persist_manual_diagnostic_best_effort(
    paths: LifecycleArtifactPaths,
    *,
    artifacts_root: Path,
    run_id: str,
    command_results: list[CommandResult],
    reason_code: str,
    failure_stage: str | None = None,
) -> LifecycleArtifactPaths:
    try:
        with ObserverEvidenceStore(artifacts_root, run_id) as observer:
            payload: dict[str, object] = {
                "schema_version": "phase0.manual-diagnostic.v1",
                "reason_code": reason_code,
                "automatic_down_attempted": False,
                "commands": _command_log_payload(command_results),
            }
            if failure_stage is not None:
                payload["failure_stage"] = failure_stage
            artifact = observer.write_immutable(
                "lifecycle/manual-diagnostic.json",
                payload,
            )
    except (OSError, ValueError):
        return _refresh_existing_artifact_paths(paths, run_id=run_id)
    return paths.model_copy(update={"manual_diagnostic": artifact.path})


def _command_log_payload(
    command_results: list[CommandResult],
) -> dict[str, object]:
    return {
        "schema_version": "phase0.command-log-index.v1",
        "records": [
            {
                "arguments": result.arguments,
                "exit_code": result.exit_code,
                "stdout_sha256": sha256_bytes(result.stdout.encode("utf-8")),
                "stderr_sha256": sha256_bytes(result.stderr.encode("utf-8")),
                "audit_status": (
                    "COMMAND_LOG_V2"
                    if result.command_log_artifact is not None
                    else "FIXTURE_UNAVAILABLE"
                ),
                "command_log_artifact": result.command_log_artifact,
                "command_log_sha256": result.command_log_sha256,
            }
            for result in command_results
        ],
    }


def _refresh_existing_artifact_paths(
    paths: LifecycleArtifactPaths,
    *,
    run_id: str,
) -> LifecycleArtifactPaths:
    root = paths.artifacts_root
    observer = root / "observer-visible" / run_id
    evaluator = root / "evaluator-only" / run_id
    return paths.model_copy(
        update={
            "ownership_intent": _existing_regular_artifact(
                root,
                observer / "ownership-intent.json",
            ),
            "ownership_manifest": _existing_regular_artifact(
                root,
                observer / "resource-ownership.json",
            ),
            "ownership_anchor": _existing_regular_artifact(
                root,
                evaluator / "ownership-anchor.json",
            ),
            "resolved_compose": _existing_regular_artifact(
                root,
                observer / "lifecycle" / "resolved-compose.json",
            ),
            "resolved_compose_raw": _existing_regular_artifact(
                root,
                evaluator / "lifecycle" / "resolved-compose.json",
            ),
            "command_log": _existing_regular_artifact(
                root,
                observer / "lifecycle" / "commands.json",
            ),
            "manual_diagnostic": _existing_regular_artifact(
                root,
                observer / "lifecycle" / "manual-diagnostic.json",
            ),
        }
    )


def _project_resolved_compose_for_observer(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ValueError("resolved Compose must be an object")
    services = payload.get("services")
    if not isinstance(services, dict):
        raise ValueError("resolved Compose services are unavailable")
    projected_services: dict[str, object] = {}
    for logical_service, raw_service in sorted(services.items()):
        if not isinstance(logical_service, str) or not isinstance(raw_service, dict):
            raise ValueError("resolved Compose service is malformed")
        projected: dict[str, object] = {"logical_service": logical_service}
        for field in ("image", "container_name", "platform"):
            value = raw_service.get(field)
            if isinstance(value, str) and value:
                projected[field] = value
        raw_labels = raw_service.get("labels", {})
        if isinstance(raw_labels, dict):
            labels = {
                str(key): str(value)
                for key, value in raw_labels.items()
                if str(key) in _OBSERVER_COMPOSE_LABEL_ALLOWLIST
            }
            if labels:
                projected["labels"] = labels
        raw_ports = raw_service.get("ports", [])
        if isinstance(raw_ports, list):
            ports: list[dict[str, object]] = []
            for raw_port in raw_ports:
                if not isinstance(raw_port, dict):
                    continue
                host_ip = raw_port.get("host_ip")
                published = raw_port.get("published")
                target = raw_port.get("target")
                protocol = raw_port.get("protocol", "tcp")
                if (
                    host_ip in {"127.0.0.1", "::1"}
                    and published is not None
                    and isinstance(target, int)
                    and protocol in {"tcp", "udp"}
                ):
                    ports.append(
                        {
                            "host_ip": host_ip,
                            "published": str(published),
                            "target": target,
                            "protocol": protocol,
                        }
                    )
            if ports:
                projected["ports"] = ports
        projected_services[logical_service] = projected
    return {"services": projected_services}


def _require_explicit_volume_plan(
    resolved: ResolvedComposeConfig,
    *,
    run_id: str,
) -> None:
    payload = json.loads(resolved.stdout)
    if not isinstance(payload, dict):
        raise ValueError("resolved Compose must be an object")
    services = payload.get("services")
    volumes = payload.get("volumes")
    if not isinstance(services, dict) or not isinstance(volumes, dict):
        raise ValueError("resolved Compose named volume plan is unavailable")
    expected_labels = {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: run_id,
    }
    for volume_name, volume in volumes.items():
        if not isinstance(volume_name, str) or not isinstance(volume, dict):
            raise ValueError("resolved Compose named volume is malformed")
        labels = volume.get("labels")
        if not isinstance(labels, dict) or any(
            labels.get(key) != value for key, value in expected_labels.items()
        ):
            raise ValueError(f"named volume labels are invalid: {volume_name}")
        if volume.get("name") != f"{PROJECT_NAMESPACE}-{run_id}-{volume_name}":
            raise ValueError(f"named volume identity is invalid: {volume_name}")
    for service_name, service in services.items():
        if not isinstance(service_name, str) or not isinstance(service, dict):
            raise ValueError("resolved Compose service is malformed")
        mounts = service.get("volumes", [])
        if not isinstance(mounts, list):
            raise ValueError(f"service volume plan is malformed: {service_name}")
        for mount in mounts:
            if not isinstance(mount, dict):
                raise ValueError(f"service volume mount is ambiguous: {service_name}")
            mount_type = mount.get("type")
            source = mount.get("source")
            if mount_type == "volume":
                if not isinstance(source, str) or not source or source not in volumes:
                    raise ValueError(
                        f"service volume source is undeclared: {service_name}"
                    )
            elif mount_type not in {"bind", "tmpfs"}:
                raise ValueError(
                    f"service volume mount type is unknown: {service_name}"
                )
    for service_name, (volume_name, target) in _REQUIRED_NAMED_VOLUME_PLAN.items():
        service = services.get(service_name)
        volume = volumes.get(volume_name)
        if not isinstance(service, dict) or not isinstance(volume, dict):
            raise ValueError(f"required named volume is missing: {service_name}")
        declared_name = volume.get("name")
        if declared_name != f"{PROJECT_NAMESPACE}-{run_id}-{volume_name}":
            raise ValueError(f"named volume identity is invalid: {volume_name}")
        mounts = service.get("volumes")
        if not isinstance(mounts, list):
            raise ValueError(f"required volume mount is missing: {service_name}")
        exact = [
            mount
            for mount in mounts
            if isinstance(mount, dict)
            and mount.get("type") == "volume"
            and mount.get("source") == volume_name
            and mount.get("target") == target
        ]
        if len(exact) != 1 or any(
            not isinstance(mount, dict) or not mount.get("source")
            for mount in mounts
        ):
            raise ValueError(f"required volume mount is unsafe: {service_name}")


def _existing_regular_artifact(
    root: Path,
    candidate: Path | None,
) -> Path | None:
    if candidate is None:
        return None
    root_absolute = root.absolute()
    candidate_absolute = candidate.absolute()
    try:
        relative = candidate_absolute.relative_to(root_absolute)
        directories = (root_absolute,) + tuple(
            root_absolute.joinpath(*relative.parts[:index])
            for index in range(1, len(relative.parts))
        )
        for directory in directories:
            directory_metadata = directory.lstat()
            if (
                stat.S_ISLNK(directory_metadata.st_mode)
                or not stat.S_ISDIR(directory_metadata.st_mode)
                or directory_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(directory_metadata.st_mode) & 0o022
            ):
                return None
        metadata = candidate_absolute.lstat()
    except (OSError, ValueError):
        return None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        return None
    return candidate_absolute


def _canonical_labels(run_id: str) -> dict[str, str]:
    return {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: run_id,
    }


def _terminal(outcome: Outcome, reason_code: str) -> TerminalResult:
    return TerminalResult(outcome=outcome, reason_code=reason_code)
