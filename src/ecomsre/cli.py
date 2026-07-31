"""Non-interactive command-line contract for EcomSRE-Agent."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ecomsre.environment.lifecycle import (
    LifecycleExecution,
    LifecycleRunner,
    ReadinessEvidence,
    cleanup_owned_named_volumes,
    down_environment,
    health_environment,
    status_environment,
    up_environment,
)
from ecomsre.environment.command_runner import AuditedSubprocessRunner
from ecomsre.environment.bootstrap import (
    Arm64ManifestUnavailable,
    ProxyConfigurationUnsafe,
    ProxyDiscoveryUnavailable,
    ImageLockRotationRequired,
    UpstreamCommandFailed,
    bootstrap_image_lock,
)
from ecomsre.environment.live_preflight import (
    FreshStopAuthority,
    collect_direct_stop_docker_snapshot,
    collect_fresh_preflight,
    collect_fresh_stop_authority,
)
from ecomsre.environment.readiness import (
    ReadinessCollectionError,
    _owned_base_urls,
    collect_candidate_initial_readiness,
    collect_fresh_readiness,
)
from ecomsre.environment.manifests import (
    ImageLockManifest,
    ImageLockSourceSetChanged,
    ImageLockStatus,
    load_image_lock,
)
from ecomsre.environment.ownership_authority import (
    AuthenticatedOwnershipContext,
    OwnershipAuthorityError,
    load_authenticated_ownership_context,
)
from ecomsre.environment.preflight import (
    AuthenticatedPreflightEvidence,
    DOCKER_DESKTOP_CONTEXT,
    DiscoveryParseError,
    PreflightCollectionError,
    collect_docker_snapshot,
    is_local_unix_docker_endpoint,
)
from ecomsre.environment.upstream import (
    bootstrap_frozen_upstream,
    verify_frozen_upstream,
)
from ecomsre.phase0.models import (
    DiagnosticSmokePolicy,
    DiagnosticStatus,
    MeasurementPhase,
    Outcome,
    SmokeReport,
    TerminalResult,
)
from ecomsre.phase0.smoke import (
    EnvironmentStartDisposition,
    SmokeEnvironmentStart,
    SmokeExecutionError,
    SmokeSupervisorState,
    execute_diagnostic_cycle,
    finalize_supervised_smoke,
    promote_or_revalidate_registry,
    reseal_recovery_evidence,
    supervise_smoke_attempt,
    validate_current_recovery_seal,
)
from ecomsre.scenarios.ad_service_failure import (
    AdServiceFailureController,
    EvidencePersistenceError,
    ObserverControlEventSink,
    ScenarioState,
)
from ecomsre.scenarios.ground_truth import (
    FlagdGroundTruthRuntime,
    FlagdRuntimeUnavailable,
    FlagdSourceContractError,
    HttpOfrepClient,
    OfrepClient,
    prepare_flagd_runtime,
)
from ecomsre.evidence.store import ObserverEvidenceStore, ReportEvidenceStore
from ecomsre.telemetry.http import OwnedHttpClient


EXIT_CODES = {outcome.value: outcome.exit_code for outcome in Outcome}

PHASE0_COMMANDS = (
    "bootstrap",
    "preflight",
    "up",
    "health",
    "inject",
    "reset",
    "status",
    "accept",
    "smoke",
    "stop",
    "cleanup-owned-volumes",
)

RUN_ID_REQUIRED_COMMANDS = {
    "up",
    "health",
    "inject",
    "reset",
    "status",
    "stop",
    "cleanup-owned-volumes",
}

IMPLEMENTED_COMMANDS = frozenset(
    {
        "bootstrap",
        "preflight",
        "up",
        "health",
        "inject",
        "reset",
        "status",
        "smoke",
        "stop",
        "cleanup-owned-volumes",
    }
)
@dataclass(frozen=True)
class HandlerContext:
    runner: LifecycleRunner
    project_root: Path
    artifacts_root: Path
    preflight_evidence: AuthenticatedPreflightEvidence | None = None
    readiness_evidence: ReadinessEvidence | None = None
    ofrep_client: OfrepClient | None = None


Handler = Callable[[argparse.Namespace, HandlerContext], TerminalResult | SmokeReport]


class CliCommandResult(TerminalResult):
    """Serializable terminal summary retaining lifecycle evidence handles."""

    evidence_paths: tuple[str, ...] = ()
    ownership_context_authenticated: bool = False
    ownership_manifest_sha256: str | None = None


class DirectStopResult(TerminalResult):
    """Terminal stop truth plus non-fatal evidence-persistence detail."""

    evidence_persistence_error: str | None = None
    owned_stop_completed: bool = False


class DirectOwnedVolumeCleanupResult(TerminalResult):
    """Exact cleanup truth, including append-only recovery seal status."""

    removed_volume_names: tuple[str, ...] = ()
    owned_volume_cleanup_completed: bool = False
    recovery_seal_current: bool = False


class SubprocessRunner(AuditedSubprocessRunner):
    """Compatibility name for the single audited execution boundary."""

    def __init__(
        self,
        *,
        cwd: Path,
        artifacts_root: Path | None = None,
        run_id: str | None = None,
    ) -> None:
        root = Path(cwd).resolve()
        super().__init__(
            project_root=root,
            artifacts_root=artifacts_root or root / "artifacts" / "phase0",
            run_id=run_id or secrets.token_hex(16),
        )


class ContractArgumentParser(argparse.ArgumentParser):
    """Map every parser failure to the frozen invalid-invocation code."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            EXIT_CODES["INVALID_INVOCATION"],
            f"{self.prog}: error: {message}\n",
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit, prompt-free CLI parser."""
    parser = ContractArgumentParser(prog="ecomsre")
    areas = parser.add_subparsers(dest="area", required=True)
    phase0 = areas.add_parser("phase0")
    commands = phase0.add_subparsers(dest="command", required=True)

    for name in PHASE0_COMMANDS:
        command = commands.add_parser(name)
        if name in {
            "bootstrap",
            "preflight",
            "up",
            "health",
            "inject",
            "reset",
            "status",
            "accept",
            "smoke",
            "stop",
            "cleanup-owned-volumes",
        }:
            command.add_argument(
                "--run-id",
                required=False,
                type=_parse_run_id,
                metavar="RUN_ID",
            )
        if name == "bootstrap":
            command.add_argument(
                "--rotate-image-lock",
                action="store_true",
            )
            command.add_argument(
                "--expected-old-lock-sha256",
                type=_parse_sha256,
                metavar="SHA256",
            )
            command.add_argument(
                "--rotation-reason",
                choices=("COMPOSE_OVERRIDE_CHANGED",),
            )

    return parser


def build_handler_registry() -> dict[str, Handler]:
    """Return only handlers implemented by the current Phase 0 task."""
    return {
        "bootstrap": _handle_bootstrap,
        "preflight": _handle_preflight,
        "up": _handle_up,
        "health": _handle_health,
        "inject": _handle_inject,
        "reset": _handle_reset,
        "status": _handle_status,
        "smoke": _handle_smoke,
        "stop": _handle_stop,
        "cleanup-owned-volumes": _handle_cleanup_owned_volumes,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: LifecycleRunner | None = None,
    project_root: Path | None = None,
    artifacts_root: Path | None = None,
    preflight_evidence: AuthenticatedPreflightEvidence | None = None,
    readiness_evidence: ReadinessEvidence | None = None,
    ofrep_client: OfrepClient | None = None,
) -> int:
    """Dispatch one Phase 0 command without prompting or hidden fallback."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "run_id"):
        candidate = args.run_id or os.environ.get("ECOMSRE_RUN_ID") or None
        if candidate is None and args.command in {"bootstrap", "preflight", "smoke"}:
            candidate = secrets.token_hex(16)
        if candidate is None and args.command in RUN_ID_REQUIRED_COMMANDS:
            parser.error("run_id is required")
        if candidate is not None and args.run_id is None:
            try:
                args.run_id = _parse_run_id(candidate)
            except argparse.ArgumentTypeError as error:
                parser.error(str(error))
    registry = build_handler_registry()
    handler = registry.get(args.command)
    if handler is None:
        print(
            f"phase0 command {args.command!r} is not implemented",
            file=sys.stderr,
        )
        return EXIT_CODES["INVALID_INVOCATION"]

    root = (
        Path(project_root).resolve()
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    resolved_artifacts_root = (
        Path(artifacts_root).resolve()
        if artifacts_root is not None
        else root / "artifacts" / "phase0"
    )
    context = HandlerContext(
        runner=runner
        or SubprocessRunner(
            cwd=root,
            artifacts_root=resolved_artifacts_root,
            run_id=getattr(args, "run_id", None),
        ),
        project_root=root,
        artifacts_root=resolved_artifacts_root,
        preflight_evidence=preflight_evidence,
        readiness_evidence=readiness_evidence,
        ofrep_client=ofrep_client,
    )
    try:
        result = handler(args, context)
    except OwnershipAuthorityError:
        result = TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="RESOURCE_OWNERSHIP_UNKNOWN",
        )
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ):
        result = TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="CLI_EXECUTION_BLOCKED",
        )
    print(result.model_dump_json())
    return result.exit_code


def _handle_bootstrap(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    rotation_values = (
        bool(getattr(args, "rotate_image_lock", False)),
        getattr(args, "expected_old_lock_sha256", None) is not None,
        getattr(args, "rotation_reason", None) is not None,
    )
    if any(rotation_values) and not all(rotation_values):
        return TerminalResult(
            outcome=Outcome.INVALID_INVOCATION,
            reason_code="IMAGE_LOCK_ROTATION_ARGUMENTS_REQUIRED",
        )
    verification = bootstrap_frozen_upstream(
        context.project_root,
        context.runner,
    )
    if verification.outcome is not Outcome.SUCCESS:
        return TerminalResult(
            outcome=verification.outcome,
            reason_code=verification.reason_codes[0],
        )
    try:
        docker = collect_docker_snapshot(context.runner)
    except PreflightCollectionError:
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="PREFLIGHT_BLOCKED",
        )
    if not docker.daemon_available or not docker.endpoint:
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="PREFLIGHT_BLOCKED",
        )
    try:
        bootstrap_image_lock(
            project_root=context.project_root,
            artifacts_root=context.artifacts_root,
            run_id=args.run_id,
            runner=context.runner,
            docker_endpoint=docker.endpoint,
            rotate_image_lock=getattr(args, "rotate_image_lock", False),
            expected_old_lock_sha256=getattr(
                args,
                "expected_old_lock_sha256",
                None,
            ),
            rotation_reason=getattr(args, "rotation_reason", None),
        )
    except ImageLockRotationRequired:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="IMAGE_LOCK_ROTATION_REQUIRED",
        )
    except ImageLockSourceSetChanged:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code=(
                "IMAGE_LOCK_SOURCE_SET_CHANGED_REQUIRES_FULL_BOOTSTRAP"
            ),
        )
    except Arm64ManifestUnavailable:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="BLOCKED_UPSTREAM_ARM64_UNAVAILABLE",
        )
    except UpstreamCommandFailed:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="UPSTREAM_COMMAND_FAILED",
        )
    except ProxyDiscoveryUnavailable:
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="PROXY_DISCOVERY_UNAVAILABLE",
        )
    except ProxyConfigurationUnsafe:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="PROXY_CONFIGURATION_UNSAFE",
        )
    except DiscoveryParseError as error:
        return TerminalResult(
            outcome=error.outcome,
            reason_code=error.reason_code,
        )
    except (OSError, ValueError):
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="IMAGE_LOCK_LIVE_VERIFICATION_REQUIRED",
        )
    return TerminalResult(
        outcome=Outcome.SUCCESS,
        reason_code="IMAGE_LOCK_VERIFIED",
    )


def _handle_preflight(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    evidence = _resolve_fresh_preflight(
        context,
        run_id=args.run_id,
    )
    if isinstance(evidence, TerminalResult):
        return evidence
    return TerminalResult(
        outcome=evidence.result.outcome,
        reason_code=(
            "PREFLIGHT_SUPPORTED"
            if evidence.result.outcome is Outcome.SUCCESS
            else evidence.result.reason_codes[0]
        ),
    )


def _handle_up(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    execution = _execute_up(args, context)
    if isinstance(execution, TerminalResult):
        return execution
    return summarize_lifecycle_execution(execution)


def _execute_up(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult | LifecycleExecution:
    """Execute up while retaining authenticated post-mutation stop inputs."""
    lock = _load_lock(context.project_root)
    if lock.status is ImageLockStatus.UNINITIALIZED:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="IMAGE_LOCK_UNINITIALIZED",
        )
    frozen = _verify_upstream(context)
    if frozen is not None:
        return frozen
    evidence = _resolve_fresh_preflight(context, run_id=args.run_id)
    if isinstance(evidence, TerminalResult):
        return evidence
    try:
        prepare_flagd_runtime(
            project_root=context.project_root,
            artifacts_root=context.artifacts_root,
            run_id=args.run_id,
        )
    except FlagdSourceContractError:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="FLAGD_SOURCE_CONTRACT_MISMATCH",
        )
    except (OSError, PreflightCollectionError, ValueError):
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="FLAGD_RUNTIME_UNSAFE",
        )
    try:
        ownership = load_authenticated_ownership_context(
            context.artifacts_root,
            args.run_id,
        )
    except OwnershipAuthorityError:
        if evidence.inputs.ownership_context is not None:
            raise
        ownership = None
    return up_environment(
        context.runner,
        context=ownership,
        preflight_evidence=evidence,
        image_lock=lock,
        project_root=context.project_root,
        artifacts_root=context.artifacts_root,
    )


def _handle_status(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    frozen = _verify_upstream(context)
    if frozen is not None:
        return frozen
    ownership = load_authenticated_ownership_context(
        context.artifacts_root,
        args.run_id,
    )
    preflight, docker_endpoint = _handler_preflight_and_endpoint(
        context,
        run_id=args.run_id,
    )
    if isinstance(preflight, TerminalResult):
        return preflight
    readiness = _resolve_fresh_readiness(
        context,
        preflight=preflight,
        ownership=ownership,
    )
    if isinstance(readiness, TerminalResult):
        return readiness
    if not docker_endpoint:
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="PREFLIGHT_BLOCKED",
        )
    environment_status = status_environment(
        context.runner,
        context=ownership,
        project_root=context.project_root,
        docker_endpoint=docker_endpoint,
    )
    if environment_status.outcome is not Outcome.SUCCESS:
        return environment_status
    environment_health = health_environment(
        context.runner,
        context=ownership,
        project_root=context.project_root,
        docker_endpoint=docker_endpoint,
        readiness=readiness,
    )
    if environment_health.outcome is not Outcome.SUCCESS:
        return environment_health
    endpoint = _owned_ofrep_endpoint(ownership)
    if endpoint is None:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="OFREP_OWNERSHIP_UNKNOWN",
        )
    runtime, failure = _open_control_runtime(
        context,
        run_id=args.run_id,
        endpoint=endpoint,
    )
    if failure is not None:
        return failure
    assert runtime is not None
    try:
        try:
            state = runtime.observe_state(timeout_seconds=30)
        except EvidencePersistenceError:
            return TerminalResult(
                outcome=Outcome.BLOCKED_ENVIRONMENT,
                reason_code="EVIDENCE_PERSISTENCE_FAILED",
            )
    finally:
        runtime.close()
    if state is ScenarioState.UNKNOWN:
        return TerminalResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="CONTROL_STATE_UNKNOWN",
        )
    return TerminalResult(
        outcome=Outcome.SUCCESS,
        reason_code="CONTROL_STATE_CONFIRMED",
    )


def _handle_inject(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    return _handle_control_transition(args, context, inject=True)


def _handle_reset(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    return _handle_control_transition(args, context, inject=False)


def _handle_control_transition(
    args: argparse.Namespace,
    context: HandlerContext,
    *,
    inject: bool,
) -> TerminalResult:
    frozen = _verify_upstream(context)
    if frozen is not None:
        return frozen
    preflight, docker_endpoint = _handler_preflight_and_endpoint(
        context,
        run_id=args.run_id,
    )
    if isinstance(preflight, TerminalResult):
        return preflight
    if not docker_endpoint:
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="PREFLIGHT_BLOCKED",
        )
    ownership = load_authenticated_ownership_context(
        context.artifacts_root,
        args.run_id,
    )
    readiness = _resolve_fresh_readiness(
        context,
        preflight=preflight,
        ownership=ownership,
    )
    if isinstance(readiness, TerminalResult):
        return readiness
    environment_health = health_environment(
        context.runner,
        context=ownership,
        project_root=context.project_root,
        docker_endpoint=docker_endpoint,
        readiness=readiness,
    )
    if environment_health.outcome is not Outcome.SUCCESS:
        return environment_health
    endpoint = _owned_ofrep_endpoint(ownership)
    if endpoint is None:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="OFREP_OWNERSHIP_UNKNOWN",
        )
    runtime, failure = _open_control_runtime(
        context,
        run_id=args.run_id,
        endpoint=endpoint,
    )
    if failure is not None:
        return failure
    assert runtime is not None
    try:
        observer: ObserverEvidenceStore | None = None
        try:
            observer = ObserverEvidenceStore(
                context.artifacts_root,
                args.run_id,
            )
            observer.__enter__()
        except (OSError, ValueError):
            if observer is not None:
                observer.close()
            return TerminalResult(
                outcome=Outcome.BLOCKED_ENVIRONMENT,
                reason_code="EVIDENCE_CAPABILITY_UNAVAILABLE",
            )
        assert observer is not None
        try:
            controller = AdServiceFailureController(
                adapter=runtime,
                observer_sink=ObserverControlEventSink(observer),
                run_id=args.run_id,
                correlation_id=args.run_id,
            )
            execution = controller.inject() if inject else controller.reset()
        except (OSError, ValueError):
            return TerminalResult(
                outcome=Outcome.BLOCKED_ENVIRONMENT,
                reason_code="EVIDENCE_CAPABILITY_UNAVAILABLE",
            )
        finally:
            observer.close()
    finally:
        runtime.close()
    return execution.terminal_result


def _open_control_runtime(
    context: HandlerContext,
    *,
    run_id: str,
    endpoint: str,
) -> tuple[FlagdGroundTruthRuntime | None, TerminalResult | None]:
    try:
        runtime = FlagdGroundTruthRuntime.open_existing(
            project_root=context.project_root,
            artifacts_root=context.artifacts_root,
            run_id=run_id,
            ofrep_client=context.ofrep_client or HttpOfrepClient(),
            ofrep_endpoint=endpoint,
        )
    except FlagdSourceContractError:
        return (
            None,
            TerminalResult(
                outcome=Outcome.BLOCKED_UPSTREAM,
                reason_code="FLAGD_SOURCE_CONTRACT_MISMATCH",
            ),
        )
    except FlagdRuntimeUnavailable:
        return (
            None,
            TerminalResult(
                outcome=Outcome.BLOCKED_ENVIRONMENT,
                reason_code="CONTROL_RUNTIME_UNAVAILABLE",
            ),
        )
    except OSError:
        return (
            None,
            TerminalResult(
                outcome=Outcome.BLOCKED_ENVIRONMENT,
                reason_code="EVIDENCE_CAPABILITY_UNAVAILABLE",
            ),
        )
    except ValueError:
        return (
            None,
            TerminalResult(
                outcome=Outcome.UNSAFE,
                reason_code="CONTROL_RUNTIME_UNSAFE",
            ),
        )
    return runtime, None


def _owned_ofrep_endpoint(
    context: AuthenticatedOwnershipContext,
) -> str | None:
    published_ports: set[int] = set()
    for resource in context.manifest.resources:
        evidence = set(resource.identity_evidence)
        if (
            resource.kind != "port"
            or resource.labels.get("com.docker.compose.service") != "flagd"
            or "service:flagd" not in evidence
            or "target_port:8016" not in evidence
            or "protocol:tcp" not in evidence
        ):
            continue
        matches = [
            value.removeprefix("published_port:")
            for value in evidence
            if value.startswith("published_port:")
        ]
        if len(matches) != 1 or not matches[0].isdigit():
            return None
        published = int(matches[0])
        if not 1 <= published <= 65535:
            return None
        published_ports.add(published)
    if len(published_ports) != 1:
        return None
    return f"http://127.0.0.1:{published_ports.pop()}"


def _handle_health(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    frozen = _verify_upstream(context)
    if frozen is not None:
        return frozen
    ownership = load_authenticated_ownership_context(
        context.artifacts_root,
        args.run_id,
    )
    preflight, docker_endpoint = _handler_preflight_and_endpoint(
        context,
        run_id=args.run_id,
    )
    if isinstance(preflight, TerminalResult):
        return preflight
    if not docker_endpoint:
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="PREFLIGHT_BLOCKED",
        )
    readiness = _resolve_fresh_readiness(
        context,
        preflight=preflight,
        ownership=ownership,
    )
    if isinstance(readiness, TerminalResult):
        return readiness
    return health_environment(
        context.runner,
        context=ownership,
        project_root=context.project_root,
        docker_endpoint=docker_endpoint,
        readiness=readiness,
    )


def _handle_smoke(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult | SmokeReport:
    """Delegate the entire up-to-report attempt to one fail-closed supervisor."""
    return supervise_smoke_attempt(
        run_id=args.run_id,
        operations=_ProductionSmokeOperations(
            args=args,
            context=context,
        ),
    )


@dataclass
class _SmokeControlHandle:
    runtime: FlagdGroundTruthRuntime
    observer: ObserverEvidenceStore
    controller: AdServiceFailureController
    telemetry_store: ObserverEvidenceStore | None = None
    client: OwnedHttpClient | None = None
    base_urls: dict[str, str] | None = None
    capability: object | None = None


class _ProductionSmokeOperations:
    """Concrete production operations for the single bounded supervisor."""

    def __init__(self, *, args: argparse.Namespace, context: HandlerContext) -> None:
        self.args = args
        self.context = context
        self.policy = DiagnosticSmokePolicy()
        self.latest_authority: tuple[
            AuthenticatedPreflightEvidence,
            AuthenticatedOwnershipContext,
        ] | None = None
        self.stop_ownership: AuthenticatedOwnershipContext | None = None
        self.stop_docker_endpoint: str | None = None
        self.stop_daemon_id: str | None = None

    def start_environment(self) -> SmokeEnvironmentStart:
        execution = _execute_up(self.args, self.context)
        if isinstance(execution, TerminalResult):
            return SmokeEnvironmentStart(
                result=execution,
                disposition=(
                    EnvironmentStartDisposition.PRE_MUTATION_BLOCKED
                ),
            )
        if (
            execution.ownership_context is not None
            and execution.docker_endpoint
            and execution.daemon_id
        ):
            self.stop_ownership = execution.ownership_context
            self.stop_docker_endpoint = execution.docker_endpoint
            self.stop_daemon_id = execution.daemon_id
        result = summarize_lifecycle_execution(execution)
        if result.outcome is Outcome.SUCCESS:
            disposition = (
                EnvironmentStartDisposition.OWNED_ENVIRONMENT_STARTED
            )
        elif execution.mutation_may_have_occurred:
            disposition = (
                EnvironmentStartDisposition.MUTATION_MAY_HAVE_OCCURRED
            )
        else:
            disposition = EnvironmentStartDisposition.PRE_MUTATION_BLOCKED
        return SmokeEnvironmentStart(
            result=result,
            disposition=disposition,
        )

    def fresh_authority(
        self,
        boundary: str,
    ) -> tuple[AuthenticatedPreflightEvidence, AuthenticatedOwnershipContext]:
        evidence = _resolve_fresh_preflight(
            self.context,
            run_id=self.args.run_id,
        )
        if isinstance(evidence, TerminalResult):
            raise RuntimeError(f"{boundary}:{evidence.reason_code}")
        ownership = load_authenticated_ownership_context(
            self.context.artifacts_root,
            self.args.run_id,
        )
        if evidence.run_id != ownership.run_id:
            raise ValueError(f"{boundary}:SMOKE_AUTHORITY_RUN_MISMATCH")
        self.latest_authority = (evidence, ownership)
        return self.latest_authority

    def initial_readiness(self, authority: object) -> object:
        preflight, ownership = self._authority(authority)
        return collect_candidate_initial_readiness(
            project_root=self.context.project_root,
            artifacts_root=self.context.artifacts_root,
            preflight=preflight,
            ownership=ownership,
        )

    def open_control(self, authority: object) -> _SmokeControlHandle:
        _preflight, ownership = self._authority(authority)
        endpoint = _owned_ofrep_endpoint(ownership)
        if endpoint is None:
            raise ValueError("OFREP_OWNERSHIP_UNKNOWN")
        runtime, failure = _open_control_runtime(
            self.context,
            run_id=self.args.run_id,
            endpoint=endpoint,
        )
        if failure is not None or runtime is None:
            raise RuntimeError(
                failure.reason_code if failure is not None else "CONTROL_OPEN_FAILED"
            )
        observer: ObserverEvidenceStore | None = None
        try:
            observer = ObserverEvidenceStore(
                self.context.artifacts_root,
                self.args.run_id,
            )
            observer.__enter__()
            controller = AdServiceFailureController(
                adapter=runtime,
                observer_sink=ObserverControlEventSink(observer),
                run_id=self.args.run_id,
                correlation_id=self.args.run_id,
            )
            return _SmokeControlHandle(
                runtime=runtime,
                observer=observer,
                controller=controller,
            )
        except BaseException:
            if observer is not None:
                observer.close()
            runtime.close()
            raise

    def promote(self, authority: object, control: object) -> object:
        _preflight, ownership = self._authority(authority)
        handle = self._control(control)
        handle.telemetry_store = ObserverEvidenceStore(
            self.context.artifacts_root,
            self.args.run_id,
        )
        handle.telemetry_store.__enter__()
        handle.client = OwnedHttpClient(context=ownership)
        handle.base_urls = _owned_base_urls(ownership)
        try:
            handle.capability = promote_or_revalidate_registry(
                project_root=self.context.project_root,
                store=handle.telemetry_store,
                client=handle.client,
                controller=handle.controller,
                base_urls=handle.base_urls,
                sleep=time.sleep,
                before_mutation=lambda phase: self._refresh_control_mutation(
                    stage="promotion",
                    phase=phase,
                ),
            )
        except SmokeExecutionError:
            raise
        except (RuntimeError, ValueError) as error:
            raise SmokeExecutionError(
                "BLOCKED_TELEMETRY_FIXTURE_UNRESOLVED",
                status=DiagnosticStatus.BLOCKED,
            ) from error
        return handle.capability

    def frozen_readiness(self, authority: object) -> object:
        preflight, ownership = self._authority(authority)
        return collect_fresh_readiness(
            project_root=self.context.project_root,
            artifacts_root=self.context.artifacts_root,
            preflight=preflight,
            ownership=ownership,
            boundary="post-promotion",
        )

    def diagnostic(self, authority: object, control: object) -> object:
        self._authority(authority)
        handle = self._control(control)
        if (
            handle.telemetry_store is None
            or handle.client is None
            or handle.base_urls is None
            or handle.capability is None
        ):
            raise RuntimeError("PROMOTION_CAPABILITY_UNAVAILABLE")
        return execute_diagnostic_cycle(
            store=handle.telemetry_store,
            client=handle.client,
            capability=handle.capability,
            controller=handle.controller,
            base_urls=handle.base_urls,
            policy=self.policy,
            sleep=time.sleep,
            before_mutation=lambda phase: self._refresh_control_mutation(
                stage="diagnostic",
                phase=phase,
            ),
        )

    def final_readiness(self, authority: object) -> object:
        preflight, ownership = self._authority(authority)
        return collect_fresh_readiness(
            project_root=self.context.project_root,
            artifacts_root=self.context.artifacts_root,
            preflight=preflight,
            ownership=ownership,
            boundary="final",
        )

    def reset(self, control: object) -> TerminalResult:
        handle = self._control(control)
        execution = handle.controller.reset()
        handle.observer.write_immutable(
            "lifecycle/smoke-final-reset.json",
            {
                "schema_version": "phase0.smoke-control-ack.v1",
                "run_id": self.args.run_id,
                "stage": "finalization",
                "phase": None,
                "transition_succeeded": (
                    execution.terminal_result.outcome is Outcome.SUCCESS
                ),
                "acknowledgement_duration_seconds": (
                    execution.observer_event.monotonic_duration_seconds
                ),
                "reason_code": execution.terminal_result.reason_code,
            },
        )
        return execution.terminal_result

    def refresh_before_reset(self, control: object) -> object:
        self._control(control)
        return self._refresh_control_mutation(
            stage="finalization",
            phase=MeasurementPhase.RECOVERY,
        )

    def _refresh_control_mutation(
        self,
        *,
        stage: str,
        phase: MeasurementPhase,
    ) -> object:
        preflight, ownership = self.fresh_authority(
            f"control-{stage}-{phase.value}"
        )
        return collect_candidate_initial_readiness(
            project_root=self.context.project_root,
            artifacts_root=self.context.artifacts_root,
            preflight=preflight,
            ownership=ownership,
            purpose="CONTROL_MUTATION",
        )

    def close_control(self, control: object) -> None:
        handle = self._control(control)
        if handle.telemetry_store is not None:
            handle.telemetry_store.close()
        handle.observer.close()
        handle.runtime.close()

    def fresh_stop_authority(self) -> FreshStopAuthority:
        if (
            self.stop_ownership is None
            or self.stop_docker_endpoint is None
            or self.stop_daemon_id is None
        ):
            raise RuntimeError("STOP_AUTHORITY_INPUT_UNAVAILABLE")
        return collect_fresh_stop_authority(
            project_root=self.context.project_root,
            artifacts_root=self.context.artifacts_root,
            runner=self.context.runner,
            ownership=self.stop_ownership,
            expected_docker_endpoint=self.stop_docker_endpoint,
            expected_daemon_id=self.stop_daemon_id,
        )

    def stop_environment(self, authority: object) -> TerminalResult:
        if (
            not isinstance(authority, FreshStopAuthority)
            or self.stop_ownership is None
        ):
            raise TypeError("fresh stop authority is invalid")
        ownership = self.stop_ownership
        if not authority.is_authentic(ownership):
            raise ValueError("fresh stop authority is unauthenticated")
        return down_environment(
            self.context.runner,
            context=ownership,
            project_root=self.context.project_root,
            docker_endpoint=authority.docker_endpoint,
        )

    def cleanup_owned_volumes(self, authority: object) -> TerminalResult:
        if (
            not isinstance(authority, FreshStopAuthority)
            or self.stop_ownership is None
        ):
            raise TypeError("fresh stop authority is invalid")
        ownership = self.stop_ownership
        if not authority.is_authentic(ownership):
            raise ValueError("fresh stop authority is unauthenticated")
        execution = cleanup_owned_named_volumes(
            self.context.runner,
            context=ownership,
            project_root=self.context.project_root,
            docker_endpoint=authority.docker_endpoint,
            expected_daemon_id=authority.daemon_id,
        )
        return execution.result

    def finalize(self, state: SmokeSupervisorState) -> SmokeReport:
        return finalize_supervised_smoke(
            state=state,
            artifacts_root=self.context.artifacts_root,
            policy=self.policy,
        )

    def write_minimal_terminal(
        self,
        state: SmokeSupervisorState,
        reason: str,
    ) -> None:
        with ReportEvidenceStore(
            self.context.artifacts_root,
            self.args.run_id,
        ) as reports:
            reports.write_minimal_terminal(
                run_id=self.args.run_id,
                reason_code=reason,
                environment_started=state.environment_started,
                reset_attempted=state.reset_attempted,
                stop_attempted=state.stop_attempted,
            )

    @staticmethod
    def _authority(
        authority: object,
    ) -> tuple[AuthenticatedPreflightEvidence, AuthenticatedOwnershipContext]:
        if (
            not isinstance(authority, tuple)
            or len(authority) != 2
            or not isinstance(authority[0], AuthenticatedPreflightEvidence)
            or not isinstance(authority[1], AuthenticatedOwnershipContext)
        ):
            raise TypeError("smoke authority is invalid")
        return authority

    @staticmethod
    def _control(control: object) -> _SmokeControlHandle:
        if not isinstance(control, _SmokeControlHandle):
            raise TypeError("smoke control handle is invalid")
        return control


def _handle_stop(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    ownership = load_authenticated_ownership_context(
        context.artifacts_root,
        args.run_id,
    )
    try:
        docker = collect_direct_stop_docker_snapshot(context.runner)
    except (OSError, PreflightCollectionError, ValueError):
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="FRESH_STOP_DOCKER_SNAPSHOT_UNAVAILABLE",
        )
    if docker.context_name != DOCKER_DESKTOP_CONTEXT:
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="FRESH_STOP_DOCKER_CONTEXT_UNSUPPORTED",
        )
    if (
        not isinstance(docker.docker_endpoint, str)
        or not is_local_unix_docker_endpoint(docker.docker_endpoint)
    ):
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="FRESH_STOP_DOCKER_ENDPOINT_UNSAFE",
        )
    if not docker.daemon_available:
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="FRESH_STOP_DAEMON_UNAVAILABLE",
        )
    if not docker.daemon_id:
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="FRESH_STOP_DAEMON_ID_UNAVAILABLE",
        )
    try:
        authority = collect_fresh_stop_authority(
            project_root=context.project_root,
            artifacts_root=context.artifacts_root,
            runner=context.runner,
            ownership=ownership,
            expected_docker_endpoint=docker.docker_endpoint,
            expected_daemon_id=docker.daemon_id,
        )
    except (OSError, OwnershipAuthorityError, ValueError):
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="FRESH_STOP_AUTHORITY_UNAVAILABLE",
        )
    if not authority.is_authentic(ownership):
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="FRESH_STOP_AUTHORITY_UNAVAILABLE",
        )
    stopped = down_environment(
        context.runner,
        context=ownership,
        project_root=context.project_root,
        docker_endpoint=authority.docker_endpoint,
    )
    if stopped.outcome is not Outcome.SUCCESS:
        return DirectStopResult(
            **stopped.model_dump(),
            evidence_persistence_error=authority.evidence_persistence_error,
        )
    report_root = context.artifacts_root / "reports" / args.run_id
    smoke_report = report_root / "smoke-report.json"
    if not smoke_report.exists() and not smoke_report.is_symlink():
        return DirectStopResult(
            **stopped.model_dump(),
            evidence_persistence_error=authority.evidence_persistence_error,
            owned_stop_completed=True,
        )
    initial_checksum = report_root / "checksums.sha256"
    try:
        if (
            smoke_report.is_symlink()
            or not smoke_report.is_file()
            or initial_checksum.is_symlink()
            or not initial_checksum.is_file()
        ):
            raise ValueError("terminal smoke bundle is incomplete")
        sequence = _next_recovery_seal_sequence(report_root)
        reseal_recovery_evidence(
            artifacts_root=context.artifacts_root,
            run_id=args.run_id,
            sequence=sequence,
            disposition="SAFE_STOP_COMPLETED",
            reason_code="BOUNDED_RECOVERY_STOP",
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return DirectStopResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="RECOVERY_EVIDENCE_PERSISTENCE_FAILED",
            evidence_persistence_error=authority.evidence_persistence_error,
            owned_stop_completed=True,
        )
    return DirectStopResult(
        **stopped.model_dump(),
        evidence_persistence_error=authority.evidence_persistence_error,
        owned_stop_completed=True,
    )


def _handle_cleanup_owned_volumes(
    args: argparse.Namespace,
    context: HandlerContext,
) -> DirectOwnedVolumeCleanupResult:
    ownership = load_authenticated_ownership_context(
        context.artifacts_root,
        args.run_id,
    )
    try:
        docker = collect_direct_stop_docker_snapshot(context.runner)
    except (OSError, PreflightCollectionError, ValueError):
        return _finalize_owned_volume_cleanup(
            context=context,
            run_id=args.run_id,
            result=TerminalResult(
                outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                reason_code="FRESH_STOP_DOCKER_SNAPSHOT_UNAVAILABLE",
            ),
            removed_volume_names=(),
        )
    snapshot_failure = None
    if docker.context_name != DOCKER_DESKTOP_CONTEXT:
        snapshot_failure = "FRESH_STOP_DOCKER_CONTEXT_UNSUPPORTED"
    elif (
        not isinstance(docker.docker_endpoint, str)
        or not is_local_unix_docker_endpoint(docker.docker_endpoint)
    ):
        snapshot_failure = "FRESH_STOP_DOCKER_ENDPOINT_UNSAFE"
    elif not docker.daemon_available:
        snapshot_failure = "FRESH_STOP_DAEMON_UNAVAILABLE"
    elif not docker.daemon_id:
        snapshot_failure = "FRESH_STOP_DAEMON_ID_UNAVAILABLE"
    if snapshot_failure is not None:
        return _finalize_owned_volume_cleanup(
            context=context,
            run_id=args.run_id,
            result=TerminalResult(
                outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                reason_code=snapshot_failure,
            ),
            removed_volume_names=(),
        )
    try:
        execution = cleanup_owned_named_volumes(
            context.runner,
            context=ownership,
            project_root=context.project_root,
            docker_endpoint=docker.docker_endpoint,
            expected_daemon_id=docker.daemon_id,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return _finalize_owned_volume_cleanup(
            context=context,
            run_id=args.run_id,
            result=TerminalResult(
                outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                reason_code="OWNED_VOLUME_CLEANUP_FAILED",
            ),
            removed_volume_names=(),
        )
    return _finalize_owned_volume_cleanup(
        context=context,
        run_id=args.run_id,
        result=execution.result,
        removed_volume_names=execution.removed_volume_names,
    )


def _finalize_owned_volume_cleanup(
    *,
    context: HandlerContext,
    run_id: str,
    result: TerminalResult,
    removed_volume_names: tuple[str, ...],
) -> DirectOwnedVolumeCleanupResult:
    completed = result.outcome is Outcome.SUCCESS
    report_root = context.artifacts_root / "reports" / run_id
    smoke_report = report_root / "smoke-report.json"
    if not smoke_report.exists() and not smoke_report.is_symlink():
        return DirectOwnedVolumeCleanupResult(
            **result.model_dump(),
            removed_volume_names=removed_volume_names,
            owned_volume_cleanup_completed=completed,
        )
    initial_checksum = report_root / "checksums.sha256"
    try:
        if (
            smoke_report.is_symlink()
            or not smoke_report.is_file()
            or initial_checksum.is_symlink()
            or not initial_checksum.is_file()
        ):
            raise ValueError("terminal smoke bundle is incomplete")
        sequence = _next_recovery_seal_sequence(report_root)
        reseal_recovery_evidence(
            artifacts_root=context.artifacts_root,
            run_id=run_id,
            sequence=sequence,
            disposition=(
                "OWNED_VOLUME_CLEANUP_COMPLETED"
                if completed
                else "OWNED_VOLUME_CLEANUP_INCOMPLETE"
            ),
            reason_code=result.reason_code,
        )
        if not validate_current_recovery_seal(
            context.artifacts_root,
            run_id=run_id,
        ):
            raise ValueError("recovery seal validation failed")
    except (OSError, RuntimeError, TypeError, ValueError):
        return DirectOwnedVolumeCleanupResult(
            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
            reason_code="RECOVERY_EVIDENCE_PERSISTENCE_FAILED",
            removed_volume_names=removed_volume_names,
            owned_volume_cleanup_completed=completed,
        )
    return DirectOwnedVolumeCleanupResult(
        **result.model_dump(),
        removed_volume_names=removed_volume_names,
        owned_volume_cleanup_completed=completed,
        recovery_seal_current=True,
    )


def _next_recovery_seal_sequence(report_root: Path) -> int:
    index = report_root / "seal-index.jsonl"
    if not index.exists() and not index.is_symlink():
        return 1
    if index.is_symlink() or not index.is_file():
        raise ValueError("recovery seal index is unsafe")
    lines = [
        line
        for line in index.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not lines:
        raise ValueError("recovery seal index is empty")
    payload = json.loads(lines[-1])
    sequence = payload.get("sequence")
    if not isinstance(sequence, int) or sequence < 1 or sequence >= 999:
        raise ValueError("recovery seal sequence is invalid")
    return sequence + 1


def _current_docker_endpoint(
    context: HandlerContext,
    run_id: str,
) -> str | None:
    evidence = _resolve_fresh_preflight(context, run_id=run_id)
    if isinstance(evidence, TerminalResult):
        return None
    return evidence.inputs.docker.endpoint


def _resolve_fresh_preflight(
    context: HandlerContext,
    *,
    run_id: str,
) -> AuthenticatedPreflightEvidence | TerminalResult:
    injected = context.preflight_evidence
    if (
        injected is not None
        and getattr(injected, "run_id", None) == run_id
        and callable(getattr(injected, "is_current", None))
        and injected.is_current()
    ):
        return injected
    try:
        evidence = collect_fresh_preflight(
            project_root=context.project_root,
            artifacts_root=context.artifacts_root,
            run_id=run_id,
            runner=context.runner,
        )
    except OwnershipAuthorityError:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="RESOURCE_OWNERSHIP_UNKNOWN",
        )
    except DiscoveryParseError as error:
        return TerminalResult(
            outcome=error.outcome,
            reason_code=error.reason_code,
        )
    except (OSError, PreflightCollectionError, ValueError):
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="PREFLIGHT_BLOCKED",
        )
    if evidence.result.outcome is not Outcome.SUCCESS:
        return TerminalResult(
            outcome=evidence.result.outcome,
            reason_code=evidence.result.reason_codes[0],
        )
    return evidence


def _resolve_fresh_readiness(
    context: HandlerContext,
    *,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
) -> ReadinessEvidence | TerminalResult:
    injected = context.readiness_evidence
    if (
        not isinstance(context.runner, AuditedSubprocessRunner)
        and isinstance(injected, ReadinessEvidence)
        and injected.run_id == ownership.run_id
        and injected.all_passed
    ):
        return injected
    try:
        readiness = collect_fresh_readiness(
            project_root=context.project_root,
            artifacts_root=context.artifacts_root,
            preflight=preflight,
            ownership=ownership,
        )
    except ReadinessCollectionError as error:
        reason = str(error) or "READINESS_INCOMPLETE"
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code=reason,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="READINESS_INCOMPLETE",
        )
    return readiness


def _handler_preflight_and_endpoint(
    context: HandlerContext,
    *,
    run_id: str,
) -> tuple[object | TerminalResult, str | None]:
    if (
        not isinstance(context.runner, AuditedSubprocessRunner)
        and context.preflight_evidence is not None
    ):
        return (
            context.preflight_evidence,
            _current_docker_endpoint(context, run_id),
        )
    evidence = _resolve_fresh_preflight(context, run_id=run_id)
    if isinstance(evidence, TerminalResult):
        return evidence, None
    return evidence, evidence.inputs.docker.endpoint


def _verify_upstream(context: HandlerContext) -> TerminalResult | None:
    verification = verify_frozen_upstream(
        context.project_root,
        context.runner,
    )
    if verification.outcome is Outcome.SUCCESS:
        return None
    return TerminalResult(
        outcome=verification.outcome,
        reason_code=verification.reason_codes[0],
    )


def _load_lock(project_root: Path) -> ImageLockManifest:
    return load_image_lock(project_root / "config" / "phase0" / "image-lock.json")


def summarize_lifecycle_execution(
    execution: LifecycleExecution,
) -> CliCommandResult:
    """Keep evidence paths and authenticated context facts in CLI output."""
    paths: tuple[str, ...] = ()
    if execution.artifact_paths is not None:
        root = execution.artifact_paths.artifacts_root
        paths = tuple(
            str(path)
            for path in (
                execution.artifact_paths.ownership_intent,
                execution.artifact_paths.ownership_manifest,
                execution.artifact_paths.ownership_anchor,
                execution.artifact_paths.resolved_compose,
                execution.artifact_paths.command_log,
                execution.artifact_paths.manual_diagnostic,
            )
            if path is not None and _is_trusted_evidence_path(root, path)
        )
    ownership = execution.ownership_context
    return CliCommandResult(
        **execution.result.model_dump(),
        evidence_paths=paths,
        ownership_context_authenticated=(
            ownership is not None and ownership.is_authentic()
        ),
        ownership_manifest_sha256=(
            ownership.manifest_sha256 if ownership is not None else None
        ),
    )


def _is_trusted_evidence_path(root: Path, path: Path) -> bool:
    """Return true only for an owned regular file below the declared root."""
    root_absolute = root.absolute()
    path_absolute = path.absolute()
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError:
        return False
    if not relative.parts:
        return False
    directories = (root_absolute,) + tuple(
        root_absolute.joinpath(*relative.parts[:index])
        for index in range(1, len(relative.parts))
    )
    try:
        for directory in directories:
            metadata = directory.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                return False
        metadata = path_absolute.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and not stat.S_IMODE(metadata.st_mode) & 0o022
    )


def _parse_run_id(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise argparse.ArgumentTypeError(
            "run_id must be exactly 32 lowercase hexadecimal characters"
        )
    return value


def _parse_sha256(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError(
            "expected old lock sha256 must be 64 lowercase hexadecimal characters"
        )
    return value


if __name__ == "__main__":
    raise SystemExit(main())
