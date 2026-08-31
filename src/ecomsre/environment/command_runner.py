"""Single subprocess execution boundary for Phase 0."""

from __future__ import annotations

import os
import re
import secrets
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from ecomsre.environment.preflight import CommandResult
from ecomsre.evidence.hashes import canonical_json_sha256, sha256_bytes
from ecomsre.evidence.models import CommandLog, redact_command_arguments
from ecomsre.evidence.store import EvaluatorEvidenceStore, ObserverEvidenceStore
from ecomsre.phase0.models import Outcome


_Popen = subprocess.Popen
_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REGISTRY_ROUTE_SCHEMA = "phase0.macos-registry-proxy.v1"
_REGISTRY_ROUTE_SOURCE = "MACOS_SCUTIL"
_PROXY_KEYS = frozenset({"HTTP_PROXY", "HTTPS_PROXY"})


@dataclass(frozen=True)
class RegistryRouteCapability:
    """Run-bound route data for one exact registry manifest client command."""

    run_id: str
    mode: str
    socks_present: bool
    source: str
    parser_schema: str
    docker_endpoint: str
    raw_sha256: str
    configuration_sha256: str
    environment_sha256: str
    proxy_environment: tuple[tuple[str, str], ...]


def create_registry_route_capability(
    *,
    run_id: str,
    mode: str,
    socks_present: bool,
    docker_endpoint: str,
    raw_sha256: str,
    proxy_environment: dict[str, str],
) -> RegistryRouteCapability:
    environment = tuple(sorted(proxy_environment.items()))
    environment_sha256 = canonical_json_sha256(
        {
            "ECOMSRE_RUN_ID": run_id,
            **dict(environment),
        }
    )
    payload = {
        "run_id": run_id,
        "mode": mode,
        "socks_present": socks_present,
        "source": _REGISTRY_ROUTE_SOURCE,
        "parser_schema": _REGISTRY_ROUTE_SCHEMA,
        "docker_endpoint": docker_endpoint,
        "raw_sha256": raw_sha256,
        "environment_sha256": environment_sha256,
    }
    route = RegistryRouteCapability(
        run_id=run_id,
        mode=mode,
        socks_present=socks_present,
        source=_REGISTRY_ROUTE_SOURCE,
        parser_schema=_REGISTRY_ROUTE_SCHEMA,
        docker_endpoint=docker_endpoint,
        raw_sha256=raw_sha256,
        configuration_sha256=canonical_json_sha256(payload),
        environment_sha256=environment_sha256,
        proxy_environment=environment,
    )
    _validated_registry_route(route, expected_run_id=run_id)
    return route


class AuditedSubprocessRunner:
    """Execute one argv tuple and persist immutable process-layer evidence."""

    def __init__(
        self,
        *,
        project_root: Path,
        artifacts_root: Path,
        run_id: str,
    ) -> None:
        if _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("audited runner requires an opaque run id")
        self.project_root = Path(project_root).resolve()
        self.artifacts_root = Path(artifacts_root).resolve()
        self.run_id = run_id
        self.temp_directory = _prepare_temp_directory(self.project_root, run_id)

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        if not isinstance(arguments, tuple) or not arguments:
            raise ValueError("subprocess arguments must be a non-empty tuple")
        if timeout_seconds <= 0:
            raise ValueError("subprocess timeout must be positive")
        invocation_environment = dict(environment or {})
        if set(invocation_environment) - {"ECOMSRE_RUN_ID"}:
            raise ValueError("subprocess invocation environment is not allowlisted")
        if invocation_environment.get("ECOMSRE_RUN_ID", self.run_id) != self.run_id:
            raise ValueError("subprocess run id differs from audited runner")
        return self._run_validated(
            arguments,
            timeout_seconds=timeout_seconds,
            invocation_environment=invocation_environment,
        )

    def run_registry_inspect(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        route: RegistryRouteCapability,
    ) -> CommandResult:
        if timeout_seconds <= 0:
            raise ValueError("subprocess timeout must be positive")
        if not _is_exact_registry_inspect(
            arguments,
            docker_endpoint=route.docker_endpoint,
        ):
            raise ValueError("registry route command is not allowlisted")
        proxy_environment = _validated_registry_route(
            route,
            expected_run_id=self.run_id,
        )
        return self._run_validated(
            arguments,
            timeout_seconds=timeout_seconds,
            invocation_environment={
                "ECOMSRE_RUN_ID": self.run_id,
                **proxy_environment,
            },
        )

    def _run_validated(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        invocation_environment: dict[str, str],
    ) -> CommandResult:
        process_environment = {
            "PATH": _SAFE_PATH,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(self.temp_directory),
            **invocation_environment,
        }
        started_at = datetime.now(UTC)
        monotonic_started = time.monotonic()
        timed_out = False
        process_exit_code: int | None = None
        start_failed = False
        stdout = ""
        stderr = ""
        try:
            process = _Popen(
                arguments,
                cwd=self.project_root,
                env=process_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                start_new_session=True,
            )
        except OSError as error:
            start_failed = True
            stderr = f"{type(error).__name__}: {error}"
        else:
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
                process_exit_code = process.returncode
            except subprocess.TimeoutExpired as error:
                timed_out = True
                trailing_stdout, trailing_stderr = _terminate_process_group(process)
                stdout = _completed_timeout_stream(error.stdout, trailing_stdout)
                stderr = _completed_timeout_stream(error.stderr, trailing_stderr)
        monotonic_ended = time.monotonic()
        ended_at = datetime.now(UTC)
        command_id = f"{time.monotonic_ns():020d}-{secrets.token_hex(8)}"
        stdout_relative = f"commands/{command_id}.stdout.json"
        stderr_relative = f"commands/{command_id}.stderr.json"
        stdout_hash = sha256_bytes(stdout.encode("utf-8"))
        stderr_hash = sha256_bytes(stderr.encode("utf-8"))
        classification, reason_code = _classify_process(
            arguments=arguments,
            start_failed=start_failed,
            timed_out=timed_out,
            process_exit_code=process_exit_code,
            stdout=stdout,
            stderr=stderr,
        )
        network_scope = _network_access_scope(arguments)
        command_log_relative = f"commands/{command_id}.command-log.json"
        observed_effect_scope = (
            ("process-start-failed",)
            if start_failed
            else (("process-group-terminated",) if timed_out else ("NOT_OBSERVED",))
        )
        with EvaluatorEvidenceStore(
            self.artifacts_root,
            self.run_id,
        ) as evaluator:
            stdout_artifact = evaluator.write_immutable(
                stdout_relative,
                {
                    "schema_version": "phase0.command-stream.v1",
                    "stream": "stdout",
                    "encoding": "utf-8",
                    "content": stdout,
                    "content_sha256": stdout_hash,
                },
            )
            stderr_artifact = evaluator.write_immutable(
                stderr_relative,
                {
                    "schema_version": "phase0.command-stream.v1",
                    "stream": "stderr",
                    "encoding": "utf-8",
                    "content": stderr,
                    "content_sha256": stderr_hash,
                },
            )
        command_log = CommandLog(
            schema_version="phase0.command-log.v2",
            run_id=self.run_id,
            command=Path(arguments[0]).name,
            arguments=redact_command_arguments(arguments),
            working_directory=str(self.project_root),
            started_at=started_at,
            ended_at=ended_at,
            monotonic_started_seconds=monotonic_started,
            monotonic_ended_seconds=monotonic_ended,
            timeout_seconds=timeout_seconds,
            process_exit_code=process_exit_code,
            process_timed_out=timed_out,
            classification=classification,
            terminal_exit_code=classification.exit_code,
            reason_code=reason_code,
            network_access_declared=network_scope.startswith("EXTERNAL_"),
            network_access_scope=network_scope,
            filesystem_write_scope=_filesystem_write_scope(
                arguments,
                run_id=self.run_id,
            ),
            observed_effect_scope=observed_effect_scope,
            stdout_artifact=stdout_relative,
            stdout_sha256=stdout_hash,
            stderr_artifact=stderr_relative,
            stderr_sha256=stderr_hash,
        )
        with ObserverEvidenceStore(self.artifacts_root, self.run_id) as store:
            command_log_artifact = store.write_immutable(
                command_log_relative,
                command_log.model_dump(mode="json"),
            )
            store.append_event(
                "commands/process-audit.jsonl",
                command_log.model_dump(mode="json"),
            )
        os.chmod(stdout_artifact.path, 0o400)
        os.chmod(stderr_artifact.path, 0o400)
        os.chmod(command_log_artifact.path, 0o400)
        return CommandResult(
            arguments=arguments,
            exit_code=(
                124
                if timed_out
                else (
                    classification.exit_code
                    if start_failed
                    else int(process_exit_code or 0)
                )
            ),
            stdout=stdout,
            stderr=stderr,
            process_exit_code=process_exit_code,
            process_timed_out=timed_out,
            stdout_artifact=str(stdout_artifact.path),
            stdout_sha256=stdout_hash,
            stderr_artifact=str(stderr_artifact.path),
            stderr_sha256=stderr_hash,
            command_log_artifact=str(command_log_artifact.path),
            command_log_sha256=command_log_artifact.sha256,
        )


def read_git_object_bytes(
    repository: Path,
    *,
    revision: str,
    relative_path: str,
) -> bytes:
    """Read one committed Git object through the centralized process boundary."""

    result = _run_read_only_git_object_command(
        repository,
        ("git", "show", f"{revision}:{relative_path}"),
        revision=revision,
        relative_path=relative_path,
    )
    return result


def resolve_git_object_id(
    repository: Path,
    *,
    revision: str,
    relative_path: str,
) -> str:
    """Resolve one committed Git blob identifier without mutating the checkout."""

    output = _run_read_only_git_object_command(
        repository,
        ("git", "rev-parse", f"{revision}:{relative_path}"),
        revision=revision,
        relative_path=relative_path,
    )
    object_id = output.decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", object_id) is None:
        raise ValueError("Git object id differs")
    return object_id


def _run_read_only_git_object_command(
    repository: Path,
    arguments: tuple[str, ...],
    *,
    revision: str,
    relative_path: str,
) -> bytes:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Git revision differs")
    relative = PurePosixPath(relative_path)
    if (
        not relative_path
        or relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
        or "\\" in relative_path
        or ":" in relative_path
        or relative_path != relative.as_posix()
    ):
        raise ValueError("Git object path differs")
    project = Path(repository).resolve(strict=True)
    process = _Popen(
        arguments,
        cwd=project,
        env={
            "PATH": _SAFE_PATH,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired as error:
        _terminate_process_group(process)
        raise ValueError("Git object read timed out") from error
    if process.returncode != 0 or stderr:
        raise ValueError("Git object read failed")
    return stdout


def _validated_registry_route(
    route: RegistryRouteCapability,
    *,
    expected_run_id: str,
) -> dict[str, str]:
    if not isinstance(route, RegistryRouteCapability):
        raise ValueError("registry route capability is invalid")
    if (
        route.run_id != expected_run_id
        or _RUN_ID.fullmatch(route.run_id) is None
        or not isinstance(route.socks_present, bool)
        or route.source != _REGISTRY_ROUTE_SOURCE
        or route.parser_schema != _REGISTRY_ROUTE_SCHEMA
        or not _is_safe_local_docker_endpoint(route.docker_endpoint)
        or _SHA256.fullmatch(route.raw_sha256) is None
        or _SHA256.fullmatch(route.configuration_sha256) is None
        or _SHA256.fullmatch(route.environment_sha256) is None
    ):
        raise ValueError("registry route capability is invalid")
    try:
        environment = dict(route.proxy_environment)
    except (TypeError, ValueError) as error:
        raise ValueError("registry route capability is invalid") from error
    if (
        len(environment) != len(route.proxy_environment)
        or set(environment) - _PROXY_KEYS
        or tuple(sorted(environment.items())) != route.proxy_environment
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in route.proxy_environment
        )
    ):
        raise ValueError("registry route capability is invalid")
    expected_mode = {
        frozenset(): "DIRECT",
        frozenset({"HTTP_PROXY"}): "LOOPBACK_HTTP",
        frozenset({"HTTPS_PROXY"}): "LOOPBACK_HTTPS",
        frozenset({"HTTP_PROXY", "HTTPS_PROXY"}): "LOOPBACK_HTTP_HTTPS",
    }.get(frozenset(environment))
    if (
        route.mode != expected_mode
        or any(not _is_safe_loopback_proxy_url(value) for value in environment.values())
        or canonical_json_sha256(
            {
                "ECOMSRE_RUN_ID": route.run_id,
                **environment,
            }
        )
        != route.environment_sha256
    ):
        raise ValueError("registry route capability is invalid")
    configuration_payload = {
        "run_id": route.run_id,
        "mode": route.mode,
        "socks_present": route.socks_present,
        "source": route.source,
        "parser_schema": route.parser_schema,
        "docker_endpoint": route.docker_endpoint,
        "raw_sha256": route.raw_sha256,
        "environment_sha256": route.environment_sha256,
    }
    if canonical_json_sha256(configuration_payload) != route.configuration_sha256:
        raise ValueError("registry route capability is invalid")
    return environment


def _is_exact_registry_inspect(
    arguments: tuple[str, ...],
    *,
    docker_endpoint: str,
) -> bool:
    if not isinstance(arguments, tuple) or not arguments:
        return False
    if Path(arguments[0]).name != "docker":
        return False
    if (
        len(arguments) != 8
        or arguments[1] != "--host"
        or arguments[2] != docker_endpoint
        or not _is_safe_local_docker_endpoint(arguments[2])
    ):
        return False
    command = arguments[3:]
    source = command[3]
    return (
        command[:3] == ("buildx", "imagetools", "inspect")
        and command[4] == "--raw"
        and source != ""
        and not source.startswith("-")
        and not any(character.isspace() for character in source)
    )


def _is_safe_local_docker_endpoint(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "unix"
        and parsed.netloc == ""
        and parsed.path.startswith("/")
        and parsed.path != "/"
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _is_safe_loopback_proxy_url(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        address = ip_address(host) if host is not None and "%" not in host else None
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and address is not None
        and address.is_loopback
        and port is not None
        and 1 <= port <= 65_535
    )


def _prepare_temp_directory(project_root: Path, run_id: str) -> Path:
    root = project_root / ".ecomsre-tmp"
    if root.exists():
        metadata = os.lstat(root)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ValueError("project temporary root is unsafe")
        os.chmod(root, 0o700)
    else:
        root.mkdir(mode=0o700)
    child = root / run_id
    child.mkdir(mode=0o700, exist_ok=True)
    metadata = os.lstat(child)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ValueError("run temporary directory is unsafe")
    return child


def _completed_timeout_stream(
    initial: str | bytes | None,
    trailing: str | bytes | None,
) -> str:
    value = trailing if trailing is not None else initial
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _terminate_process_group(
    process: subprocess.Popen[str],
) -> tuple[str | bytes | None, str | bytes | None]:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def _classify_process(
    *,
    arguments: tuple[str, ...],
    start_failed: bool,
    timed_out: bool,
    process_exit_code: int | None,
    stdout: str,
    stderr: str,
) -> tuple[Outcome, str]:
    if _is_exact_scutil_proxy(arguments) and (
        start_failed or timed_out or process_exit_code != 0
    ):
        return Outcome.BLOCKED_ENVIRONMENT, "PROXY_DISCOVERY_UNAVAILABLE"
    if start_failed:
        return Outcome.BLOCKED_ENVIRONMENT, "PROCESS_START_FAILED"
    if timed_out:
        return Outcome.BLOCKED_ENVIRONMENT, "PROCESS_TIMEOUT"
    if process_exit_code == 0:
        return Outcome.SUCCESS, "PROCESS_EXIT_ZERO"
    if _is_expected_lsof_no_match(
        arguments=arguments,
        process_exit_code=process_exit_code,
        stdout=stdout,
        stderr=stderr,
    ):
        return Outcome.SUCCESS, "PROCESS_EXPECTED_NO_MATCH"
    executable = Path(arguments[0]).name
    remaining = arguments[1:]
    if executable == "git":
        return Outcome.BLOCKED_UPSTREAM, "UPSTREAM_COMMAND_FAILED"
    if executable == "docker":
        if "down" in remaining:
            return (
                Outcome.MANUAL_INTERVENTION_REQUIRED,
                "OWNED_STOP_COMMAND_FAILED",
            )
        if any(command in {"up", "start"} for command in remaining):
            return Outcome.BLOCKED_ENVIRONMENT, "ENVIRONMENT_COMMAND_FAILED"
        if "pull" in remaining or "imagetools" in remaining:
            return Outcome.BLOCKED_UPSTREAM, "UPSTREAM_COMMAND_FAILED"
        return Outcome.BLOCKED_ENVIRONMENT, "DOCKER_READ_COMMAND_FAILED"
    return Outcome.FAILED_ACCEPTANCE, "PROCESS_EXIT_NONZERO"


def _is_exact_scutil_proxy(arguments: tuple[str, ...]) -> bool:
    return arguments == ("/usr/sbin/scutil", "--proxy")


def _is_expected_lsof_no_match(
    *,
    arguments: tuple[str, ...],
    process_exit_code: int | None,
    stdout: str,
    stderr: str,
) -> bool:
    if process_exit_code != 1 or stdout != "" or stderr != "":
        return False
    if len(arguments) != 6 or Path(arguments[0]).name != "lsof":
        return False
    if arguments[1:4] != ("-nP", "-F", "pcn"):
        return False
    if arguments[5] != "-sTCP:LISTEN":
        return False
    port_argument = re.fullmatch(r"-iTCP:([1-9][0-9]{0,4})", arguments[4])
    return port_argument is not None and int(port_argument.group(1)) <= 65_535


def _network_access_scope(
    arguments: tuple[str, ...],
) -> str:
    executable = Path(arguments[0]).name
    remaining = arguments[1:]
    if executable == "git" and any(
        command in {"clone", "fetch", "pull"} for command in remaining
    ):
        return "EXTERNAL_GIT"
    if executable == "docker":
        if "pull" in remaining or ("buildx" in remaining and "imagetools" in remaining):
            return "EXTERNAL_REGISTRY"
        return "LOCAL_DOCKER_DAEMON"
    return "NONE"


def _filesystem_write_scope(
    arguments: tuple[str, ...],
    *,
    run_id: str,
) -> tuple[str, ...]:
    executable = Path(arguments[0]).name
    remaining = arguments[1:]
    if executable == "docker" and "pull" in remaining:
        return ("docker-image-cache",)
    if executable == "docker" and any(
        command in {"up", "down"} for command in remaining
    ):
        return (f"docker-project:ecomsre-phase0:{run_id}",)
    return ()
