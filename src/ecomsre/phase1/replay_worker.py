"""Isolated subprocess entrypoint for scripted replay Agent execution.

This file deliberately imports only the standard library until capability
guards and a src-only import path have been installed.
"""

from __future__ import annotations

import builtins
import _io  # type: ignore[import-not-found]
import ctypes
import fcntl
import hashlib
import importlib
import io
import json
import multiprocessing
import os
import posix
import socket
import subprocess
import sys
from pathlib import Path
from typing import IO, Any, Callable, cast

_MAX_WORKER_REQUEST_BYTES = 64 * 1024


class _CapabilityDenied(PermissionError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate worker request key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite worker request constant: {value}")


def _directory_fd_path(directory_fd: int) -> Path:
    try:
        target = os.readlink(f"/proc/self/fd/{directory_fd}")
    except OSError:
        target = ""
    if target:
        return Path(target)
    try:
        raw_path = fcntl.fcntl(directory_fd, 50, b"\0" * 1024)
    except (OSError, TypeError, ValueError) as error:
        raise _CapabilityDenied("directory capability cannot be resolved") from error
    if not isinstance(raw_path, bytes):
        raise _CapabilityDenied("directory capability cannot be resolved")
    decoded = os.fsdecode(raw_path.split(b"\0", 1)[0])
    if not decoded:
        raise _CapabilityDenied("directory capability cannot be resolved")
    return Path(decoded)


def _candidate_path(candidate: object, *, dir_fd: int | None = None) -> Path | None:
    if isinstance(candidate, int):
        return None
    try:
        path = Path(os.fsdecode(candidate))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if path.is_absolute():
        return path.resolve(strict=False)
    base = Path.cwd() if dir_fd is None else _directory_fd_path(dir_fd)
    return (base / path).resolve(strict=False)


def _guard_candidate(
    candidate: object,
    *,
    evaluator_root: Path,
    dir_fd: int | None = None,
) -> None:
    resolved = _candidate_path(candidate, dir_fd=dir_fd)
    if resolved is not None and (
        resolved == evaluator_root or evaluator_root in resolved.parents
    ):
        raise _CapabilityDenied("evaluator filesystem capability denied")


def _install_guards(project_root: Path) -> None:
    evaluator_root = (project_root / "eval/phase1").resolve(strict=False)
    original_import = cast(Callable[..., Any], builtins.__import__)
    original_import_module = cast(Callable[..., Any], importlib.import_module)
    original_builtin_open = cast(Callable[..., Any], builtins.open)
    original_io_core_open = cast(Callable[..., Any], _io.open)
    original_io_open = cast(Callable[..., Any], io.open)
    original_os_open = cast(Callable[..., int], os.open)
    original_posix_open = cast(Callable[..., int], posix.open)
    original_os_listdir = cast(Callable[..., list[str]], os.listdir)
    original_os_scandir = cast(Callable[..., Any], os.scandir)
    original_path_open = cast(Callable[..., Any], Path.open)
    original_path_read_bytes = cast(Callable[..., bytes], Path.read_bytes)
    original_path_read_text = cast(Callable[..., str], Path.read_text)
    original_path_iterdir = cast(Callable[..., Any], Path.iterdir)
    original_path_glob = cast(Callable[..., Any], Path.glob)
    original_path_rglob = cast(Callable[..., Any], Path.rglob)

    def guard_import_name(name: object) -> None:
        if not isinstance(name, str):
            return
        if (
            name == "eval"
            or name.startswith("eval.")
            or name == "ecomsre.phase1.cli"
            or name.startswith("ecomsre.phase1.cli.")
        ):
            raise _CapabilityDenied("worker import capability denied")

    def audit_guard(event: str, arguments: tuple[object, ...]) -> None:
        if event == "open" and arguments:
            _guard_candidate(arguments[0], evaluator_root=evaluator_root)
        elif event == "import" and arguments:
            guard_import_name(arguments[0])
        elif (
            event.startswith("ctypes.")
            or event == "subprocess.Popen"
            or event.startswith("os.exec")
            or event.startswith("os.spawn")
            or event.startswith("os.posix_spawn")
            or event in {"os.fork", "os.forkpty", "os.system"}
            or event.startswith("socket.")
        ):
            raise _CapabilityDenied("worker runtime capability denied")

    def guarded_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        guard_import_name(name)
        return original_import(name, globals, locals, fromlist, level)

    def guarded_import_module(
        name: str,
        package: str | None = None,
    ) -> object:
        guard_import_name(name)
        return original_import_module(name, package)

    def guarded_builtin_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        _guard_candidate(file, evaluator_root=evaluator_root)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_io_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        _guard_candidate(file, evaluator_root=evaluator_root)
        return original_io_open(file, *args, **kwargs)

    def guarded_io_core_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        _guard_candidate(file, evaluator_root=evaluator_root)
        return original_io_core_open(file, *args, **kwargs)

    def guarded_os_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        _guard_candidate(path, evaluator_root=evaluator_root, dir_fd=dir_fd)
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    def guarded_posix_open(
        path: Any,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        _guard_candidate(path, evaluator_root=evaluator_root, dir_fd=dir_fd)
        return original_posix_open(path, flags, mode, dir_fd=dir_fd)

    def guarded_os_listdir(path: Any = ".") -> list[str]:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_os_listdir(path)

    def guarded_os_scandir(path: Any = ".") -> Any:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_os_scandir(path)

    def guarded_path_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_path_open(path, *args, **kwargs)

    def guarded_path_read_bytes(path: Path) -> bytes:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_path_read_bytes(path)

    def guarded_path_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_path_read_text(path, *args, **kwargs)

    def guarded_path_iterdir(path: Path) -> Any:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_path_iterdir(path)

    def guarded_path_glob(path: Path, *args: Any, **kwargs: Any) -> Any:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_path_glob(path, *args, **kwargs)

    def guarded_path_rglob(path: Path, *args: Any, **kwargs: Any) -> Any:
        _guard_candidate(path, evaluator_root=evaluator_root)
        return original_path_rglob(path, *args, **kwargs)

    def deny_runtime_capability(*_args: Any, **_kwargs: Any) -> Any:
        raise _CapabilityDenied("worker runtime capability denied")

    sys.addaudithook(audit_guard)
    builtins.__import__ = guarded_import  # type: ignore[assignment]
    importlib.import_module = guarded_import_module  # type: ignore[assignment]
    builtins.open = guarded_builtin_open
    _io.open = guarded_io_core_open
    io.open = guarded_io_open
    os.open = guarded_os_open
    posix.open = guarded_posix_open
    os.listdir = guarded_os_listdir  # type: ignore[assignment]
    os.scandir = guarded_os_scandir
    Path.open = guarded_path_open  # type: ignore[method-assign,assignment]
    Path.read_bytes = guarded_path_read_bytes  # type: ignore[method-assign,assignment]
    Path.read_text = guarded_path_read_text  # type: ignore[method-assign,assignment]
    Path.iterdir = guarded_path_iterdir  # type: ignore[method-assign,assignment]
    Path.glob = guarded_path_glob  # type: ignore[method-assign,assignment]
    Path.rglob = guarded_path_rglob  # type: ignore[method-assign,assignment]
    socket.socket.connect = deny_runtime_capability  # type: ignore[method-assign]
    socket.socket.connect_ex = deny_runtime_capability  # type: ignore[method-assign]
    socket.create_connection = deny_runtime_capability
    ctypes.CDLL = deny_runtime_capability  # type: ignore[misc,assignment]
    ctypes.PyDLL = deny_runtime_capability  # type: ignore[misc,assignment]
    ctypes.cdll.LoadLibrary = deny_runtime_capability  # type: ignore[method-assign]
    ctypes.pydll.LoadLibrary = deny_runtime_capability  # type: ignore[method-assign]
    subprocess.Popen = deny_runtime_capability  # type: ignore[assignment,misc]
    multiprocessing.Process.start = deny_runtime_capability  # type: ignore[method-assign]
    for capability_name in (
        "system",
        "fork",
        "forkpty",
        "posix_spawn",
        "posix_spawnp",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    ):
        if hasattr(os, capability_name):
            setattr(os, capability_name, deny_runtime_capability)


def _sanitize_import_path(project_root: Path) -> None:
    src_root = (project_root / "src").resolve(strict=True)
    root = project_root.resolve(strict=True)
    tests_root = (project_root / "tests").resolve(strict=True)
    eval_root = (project_root / "eval").resolve(strict=True)
    retained: list[str] = []
    for entry in sys.path:
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve(strict=False)
        except (OSError, ValueError):
            continue
        if (
            resolved == root
            or resolved == tests_root
            or tests_root in resolved.parents
            or resolved == eval_root
            or eval_root in resolved.parents
        ):
            continue
        retained.append(str(resolved))
    sys.path[:] = [str(src_root), *retained]


def _isolated_sys_path(project_root: Path) -> bool:
    root = project_root.resolve(strict=True)
    tests_root = (root / "tests").resolve(strict=True)
    eval_root = (root / "eval").resolve(strict=True)
    for entry in sys.path:
        if not entry:
            return False
        resolved = Path(entry).resolve(strict=False)
        if (
            resolved == root
            or resolved == tests_root
            or tests_root in resolved.parents
            or resolved == eval_root
            or eval_root in resolved.parents
        ):
            return False
    return True


def _denied(operation: Callable[[], Any]) -> str:
    try:
        resource = operation()
    except _CapabilityDenied:
        return "DENIED"
    if isinstance(resource, int):
        os.close(resource)
    elif hasattr(resource, "close"):
        resource.close()
    return "ALLOWED"


def _probe(project_root: Path) -> dict[str, object]:
    importlib.import_module("ecomsre.phase1.agent")
    target = (
        project_root
        / "eval/phase1/ground-truth/ad-partial-failure-complete.json"
    )

    def socket_connect() -> None:
        connection = socket.socket()
        try:
            connection.connect(("127.0.0.1", 9))
        finally:
            connection.close()

    def ctypes_cdll() -> None:
        importlib.import_module("ctypes")
        ctypes.CDLL(None)

    def subprocess_popen() -> None:
        importlib.import_module("subprocess")
        subprocess.Popen(["/usr/bin/true"])

    def subprocess_run() -> None:
        importlib.import_module("subprocess")
        run_call = cast(Callable[..., Any], getattr(subprocess, "run"))
        run_call(
            ["/usr/bin/true"],
            check=False,
        )

    def multiprocessing_start() -> None:
        importlib.import_module("multiprocessing")
        process = multiprocessing.Process()
        try:
            process.start()
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join()

    return {
        "isolated_sys_path": _isolated_sys_path(project_root),
        "import_eval": _denied(lambda: importlib.import_module("eval.phase1.run")),
        "builtins_open": _denied(lambda: builtins.open(target, "rb")),
        "io_core_open": _denied(
            lambda: cast(Any, __import__("_io")).open(target, "rb")
        ),
        "io_open": _denied(lambda: io.open(target, "rb")),
        "os_open": _denied(lambda: os.open(target, os.O_RDONLY)),
        "posix_open": _denied(
            lambda: cast(Any, __import__("posix")).open(
                target,
                os.O_RDONLY,
            )
        ),
        "path_open": _denied(lambda: target.open("rb")),
        "path_read_bytes": _denied(target.read_bytes),
        "path_read_text": _denied(lambda: target.read_text(encoding="utf-8")),
        "import_ctypes": _denied(lambda: importlib.import_module("ctypes")),
        "import_subprocess": _denied(
            lambda: importlib.import_module("subprocess")
        ),
        "import_multiprocessing": _denied(
            lambda: importlib.import_module("multiprocessing")
        ),
        "ctypes_cdll": _denied(ctypes_cdll),
        "subprocess_popen": _denied(subprocess_popen),
        "subprocess_run": _denied(subprocess_run),
        "multiprocessing_start": _denied(multiprocessing_start),
        "os_system": _denied(lambda: os.system("true")),
        "os_fork": _denied(os.fork),
        "os_posix_spawn": _denied(
            lambda: os.posix_spawn("/usr/bin/true", ["true"], {})
        ),
        "socket_connect": _denied(socket_connect),
        "os_listdir": _denied(lambda: os.listdir(target.parent)),
        "os_scandir": _denied(lambda: os.scandir(target.parent)),
        "path_iterdir": _denied(lambda: tuple(target.parent.iterdir())),
        "path_glob": _denied(lambda: tuple(target.parent.glob("*.json"))),
    }


def _read_bounded_worker_request(stream: IO[bytes]) -> bytes:
    content = stream.read(_MAX_WORKER_REQUEST_BYTES + 1)
    if len(content) > _MAX_WORKER_REQUEST_BYTES:
        raise ValueError("worker request exceeds size limit")
    return content


def _run(project_root: Path, case_id: str) -> dict[str, object]:
    from ecomsre.backends.replay import (
        ReplayObservabilityBackend,
        load_replay_case,
    )
    from ecomsre.model.scripted import ScriptedModelGateway
    from ecomsre.phase1.agent import SingleAgent
    from ecomsre.phase1.contracts import InvestigationRequest, ModelConfiguration
    from ecomsre.phase1.runtime_config import load_agent_settings

    settings = load_agent_settings(project_root)
    replay_case = load_replay_case(
        project_root / "config/phase1/replay-cases/agent-visible",
        case_id,
    )
    run_id = hashlib.sha256(
        f"phase1:evaluation:{case_id}".encode("utf-8")
    ).hexdigest()[:32]
    agent = SingleAgent(
        gateway=ScriptedModelGateway(),
        backend=ReplayObservabilityBackend(replay_case),
        model_configuration=ModelConfiguration(
            model_name="scripted-replay-v1",
            temperature=0.0,
            model_timeout_seconds=settings.model_timeout_seconds,
        ),
        tool_timeout_seconds=settings.tool_timeout_seconds,
    )
    report = agent.run(
        InvestigationRequest(
            schema_version="phase1.investigation-request.v1",
            request_id=f"evaluation-{case_id}",
            run_id=run_id,
            agent_id="single-agent",
            task_id="root-cause-analysis",
            incident=replay_case.incident,
            budgets=settings.budgets,
        )
    )
    return report.model_dump(mode="json")


def main() -> int:
    request = json.loads(
        _read_bounded_worker_request(sys.stdin.buffer).decode(
            "utf-8",
            errors="strict",
        ),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(request, dict) or set(request) not in (
        {"mode", "project_root"},
        {"mode", "project_root", "case_id"},
    ):
        raise ValueError("worker request fields are not exact")
    project_root_value = request["project_root"]
    if not isinstance(project_root_value, str):
        raise ValueError("worker project_root is invalid")
    project_root = Path(project_root_value).resolve(strict=True)
    _sanitize_import_path(project_root)
    _install_guards(project_root)
    mode = request["mode"]
    if mode == "probe" and set(request) == {"mode", "project_root"}:
        response = _probe(project_root)
    elif mode == "run" and set(request) == {"mode", "project_root", "case_id"}:
        case_id = request["case_id"]
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("worker case_id is invalid")
        response = _run(project_root, case_id)
    else:
        raise ValueError("worker mode is invalid")
    sys.stdout.write(
        json.dumps(
            response,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
