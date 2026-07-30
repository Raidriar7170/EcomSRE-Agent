"""Non-interactive command-line contract for EcomSRE-Agent."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ecomsre.environment.lifecycle import (
    LifecycleExecution,
    LifecycleRunner,
    ReadinessEvidence,
    down_environment,
    health_environment,
    status_environment,
    up_environment,
)
from ecomsre.environment.manifests import (
    ImageLockManifest,
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
    CommandResult,
)
from ecomsre.environment.upstream import (
    bootstrap_frozen_upstream,
    verify_frozen_upstream,
)
from ecomsre.phase0.models import Outcome, TerminalResult
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
from ecomsre.evidence.store import ObserverEvidenceStore


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
    "stop",
)

RUN_ID_REQUIRED_COMMANDS = {
    "up",
    "health",
    "inject",
    "reset",
    "status",
    "stop",
}

IMPLEMENTED_COMMANDS = frozenset(
    {"bootstrap", "up", "health", "inject", "reset", "status", "stop"}
)
_SAFE_PROCESS_ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TMPDIR": "/tmp",
}
_ALLOWED_INVOCATION_ENVIRONMENT = frozenset({"ECOMSRE_RUN_ID"})


@dataclass(frozen=True)
class HandlerContext:
    runner: LifecycleRunner
    project_root: Path
    artifacts_root: Path
    preflight_evidence: AuthenticatedPreflightEvidence | None = None
    readiness_evidence: ReadinessEvidence | None = None
    ofrep_client: OfrepClient | None = None


Handler = Callable[[argparse.Namespace, HandlerContext], TerminalResult]


class CliCommandResult(TerminalResult):
    """Serializable terminal summary retaining lifecycle evidence handles."""

    evidence_paths: tuple[str, ...] = ()
    ownership_context_authenticated: bool = False
    ownership_manifest_sha256: str | None = None


class SubprocessRunner:
    """Execute audited argument tuples without a shell."""

    def __init__(self, *, cwd: Path) -> None:
        self._cwd = Path(cwd)

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        invocation_environment = dict(environment or {})
        if not set(invocation_environment).issubset(_ALLOWED_INVOCATION_ENVIRONMENT):
            raise ValueError("subprocess invocation environment is not allowlisted")
        process_environment = {
            **_SAFE_PROCESS_ENVIRONMENT,
            **invocation_environment,
        }
        completed = subprocess.run(
            list(arguments),
            cwd=self._cwd,
            env=process_environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
        return CommandResult(
            arguments=arguments,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
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
        if name in {"up", "health", "inject", "reset", "status", "accept", "stop"}:
            command.add_argument(
                "--run-id",
                required=False,
                type=_parse_run_id,
                metavar="RUN_ID",
            )

    return parser


def build_handler_registry() -> dict[str, Handler]:
    """Return only handlers implemented by the current Phase 0 task."""
    return {
        "bootstrap": _handle_bootstrap,
        "up": _handle_up,
        "health": _handle_health,
        "inject": _handle_inject,
        "reset": _handle_reset,
        "status": _handle_status,
        "stop": _handle_stop,
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
        candidate = args.run_id or os.environ.get("ECOMSRE_RUN_ID")
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
    context = HandlerContext(
        runner=runner or SubprocessRunner(cwd=root),
        project_root=root,
        artifacts_root=(
            Path(artifacts_root).resolve()
            if artifacts_root is not None
            else root / "artifacts" / "phase0"
        ),
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
    del args
    verification = bootstrap_frozen_upstream(
        context.project_root,
        context.runner,
    )
    if verification.outcome is not Outcome.SUCCESS:
        return TerminalResult(
            outcome=verification.outcome,
            reason_code=verification.reason_codes[0],
        )
    lock = _load_lock(context.project_root)
    if lock.status is ImageLockStatus.UNINITIALIZED:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="IMAGE_LOCK_UNINITIALIZED",
        )
    return TerminalResult(
        outcome=Outcome.BLOCKED_UPSTREAM,
        reason_code="IMAGE_LOCK_LIVE_VERIFICATION_REQUIRED",
    )


def _handle_up(
    args: argparse.Namespace,
    context: HandlerContext,
) -> TerminalResult:
    lock = _load_lock(context.project_root)
    if lock.status is ImageLockStatus.UNINITIALIZED:
        return TerminalResult(
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="IMAGE_LOCK_UNINITIALIZED",
        )
    frozen = _verify_upstream(context)
    if frozen is not None:
        return frozen
    evidence = context.preflight_evidence
    if evidence is None or evidence.run_id != args.run_id or not evidence.is_current():
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="PREFLIGHT_EVIDENCE_INVALID",
        )
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
    except (OSError, ValueError):
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
    execution = up_environment(
        context.runner,
        context=ownership,
        preflight_evidence=evidence,
        image_lock=lock,
        project_root=context.project_root,
        artifacts_root=context.artifacts_root,
    )
    return summarize_lifecycle_execution(execution)


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
    docker_endpoint = _current_docker_endpoint(context, args.run_id)
    if docker_endpoint is None:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="PREFLIGHT_EVIDENCE_INVALID",
        )
    readiness = context.readiness_evidence
    if (
        not isinstance(readiness, ReadinessEvidence)
        or readiness.run_id != args.run_id
        or not readiness.all_passed
    ):
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="READINESS_INCOMPLETE",
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
    docker_endpoint = _current_docker_endpoint(context, args.run_id)
    if docker_endpoint is None:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="PREFLIGHT_EVIDENCE_INVALID",
        )
    ownership = load_authenticated_ownership_context(
        context.artifacts_root,
        args.run_id,
    )
    readiness = context.readiness_evidence
    if (
        not isinstance(readiness, ReadinessEvidence)
        or readiness.run_id != args.run_id
        or not readiness.all_passed
    ):
        return TerminalResult(
            outcome=Outcome.BLOCKED_ENVIRONMENT,
            reason_code="READINESS_INCOMPLETE",
        )
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
    docker_endpoint = _current_docker_endpoint(context, args.run_id)
    if docker_endpoint is None:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="PREFLIGHT_EVIDENCE_INVALID",
        )
    return health_environment(
        context.runner,
        context=ownership,
        project_root=context.project_root,
        docker_endpoint=docker_endpoint,
        readiness=context.readiness_evidence,
    )


def _handle_stop(
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
    docker_endpoint = _current_docker_endpoint(context, args.run_id)
    if docker_endpoint is None:
        return TerminalResult(
            outcome=Outcome.UNSAFE,
            reason_code="PREFLIGHT_EVIDENCE_INVALID",
        )
    return down_environment(
        context.runner,
        context=ownership,
        project_root=context.project_root,
        docker_endpoint=docker_endpoint,
    )


def _current_docker_endpoint(
    context: HandlerContext,
    run_id: str,
) -> str | None:
    evidence = context.preflight_evidence
    if (
        not isinstance(evidence, AuthenticatedPreflightEvidence)
        or evidence.run_id != run_id
        or not evidence.is_current()
        or evidence.result.outcome is not Outcome.SUCCESS
    ):
        return None
    return evidence.inputs.docker.endpoint


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


if __name__ == "__main__":
    raise SystemExit(main())
