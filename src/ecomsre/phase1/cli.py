"""Non-interactive Phase 1 replay, evaluation, and provider commands."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType

from ecomsre.backends.replay import (
    ReplayObservabilityBackend,
    load_replay_case,
)
from ecomsre.model.gateway import (
    ModelGateway,
    OpenAICompatibleConfig,
    OpenAICompatibleGateway,
    OpenAICompatibleTransport,
    StdlibOpenAICompatibleTransport,
)
from ecomsre.model.scripted import ScriptedModelGateway
from ecomsre.phase1.agent import SingleAgent
from ecomsre.phase1.contracts import (
    AgentRunReport,
    InvestigationRequest,
    ModelConfiguration,
    RCADecision,
)
from ecomsre.phase1.runtime_config import load_agent_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VISIBLE_REPLAY_ROOT = Path("config/phase1/replay-cases/agent-visible")
DEFAULT_ARTIFACT_ROOT = PROJECT_ROOT / "artifacts/phase1"
_PROVIDER_CASES = (
    ("ad-partial-failure-complete", RCADecision.RCA_CONFIRMED),
    ("no-real-incident", RCADecision.ABSTAIN),
)
_PROVIDER_ENVIRONMENT_NAMES = (
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_MODEL",
)


def _strict_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _compact_json(payload: object) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_evaluator_module(project_root: Path) -> ModuleType:
    module_name = "_ecomsre_phase1_evaluator_cli"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = Path(project_root) / "eval/phase1/run.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 1 evaluator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _owned_directory(path: Path) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as error:
        raise ValueError("artifact directory is inaccessible") from error
    if path.is_symlink() or not stat.S_ISDIR(details.st_mode):
        raise ValueError("artifact directory must be a real directory")
    if details.st_uid != os.geteuid():
        raise ValueError("artifact directory must be owned by this user")
    return details


def _ensure_owned_directory(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("artifact root must be absolute")
    missing: list[str] = []
    cursor = path
    while True:
        try:
            _owned_directory(cursor)
            break
        except ValueError:
            try:
                cursor.lstat()
            except FileNotFoundError:
                if cursor.parent == cursor:
                    raise ValueError(
                        "artifact directory has no safe existing ancestor"
                    ) from None
                missing.append(cursor.name)
                cursor = cursor.parent
                continue
            raise
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(cursor, flags)
    try:
        for name in reversed(missing):
            if not name or name in {".", ".."}:
                raise ValueError("artifact directory component is invalid")
            try:
                os.mkdir(name, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            next_fd = os.open(name, flags, dir_fd=directory_fd)
            details = os.fstat(next_fd)
            if (
                not stat.S_ISDIR(details.st_mode)
                or details.st_uid != os.geteuid()
            ):
                os.close(next_fd)
                raise ValueError("artifact directory is unsafe")
            os.close(directory_fd)
            directory_fd = next_fd
    finally:
        os.close(directory_fd)


def _open_child_directory(directory_fd: int, name: str) -> int:
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError("artifact relative path is invalid")
    try:
        os.mkdir(name, mode=0o700, dir_fd=directory_fd)
    except FileExistsError:
        pass
    try:
        child_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    except OSError as error:
        raise ValueError("artifact directory is unsafe") from error
    details = os.fstat(child_fd)
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
    ):
        os.close(child_fd)
        raise ValueError("artifact directory is unsafe")
    return child_fd


def atomic_write_json(
    *,
    artifact_root: Path,
    relative_path: Path,
    payload: object,
) -> Path:
    """Atomically replace one canonical JSON artifact below a safe root."""

    root = Path(artifact_root)
    relative = Path(relative_path)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError("artifact path must be a safe relative path")
    if any("/" in part or "\\" in part for part in relative.parts):
        raise ValueError("artifact path must be a safe relative path")
    content = _strict_json_bytes(payload)
    _ensure_owned_directory(root)
    directory_fd = os.open(
        root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        for component in relative.parts[:-1]:
            next_fd = _open_child_directory(directory_fd, component)
            os.close(directory_fd)
            directory_fd = next_fd
        filename = relative.parts[-1]
        try:
            existing = os.stat(
                filename,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.geteuid()
        ):
            raise ValueError("artifact target is unsafe")

        temporary_name = f".{filename}.{secrets.token_hex(12)}.tmp"
        temporary_fd = -1
        try:
            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            written = 0
            while written < len(content):
                written += os.write(temporary_fd, content[written:])
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = -1
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        except Exception:
            if temporary_fd >= 0:
                os.close(temporary_fd)
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            raise
    finally:
        os.close(directory_fd)
    return root / relative


def stable_run_id(namespace: str, case_id: str) -> str:
    material = f"phase1:{namespace}:{case_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def _command_attempt(
    command: str,
    *,
    status: str,
) -> dict[str, object]:
    return {
        "schema_version": "phase1.command-attempt.v1",
        "command": command,
        "error_code": (
            "PHASE1_COMMAND_FAILED" if status == "FAILED" else None
        ),
        "status": status,
    }


def _write_command_attempt(
    *,
    artifact_root: Path,
    relative_path: Path,
    command: str,
    status: str,
) -> Path:
    return atomic_write_json(
        artifact_root=artifact_root,
        relative_path=relative_path,
        payload=_command_attempt(command, status=status),
    )


def _record_command_failure(
    *,
    artifact_root: Path,
    relative_path: Path,
    command: str,
) -> int:
    _write_command_attempt(
        artifact_root=artifact_root,
        relative_path=relative_path,
        command=command,
        status="FAILED",
    )
    print(
        _compact_json(
            {
                "status": "FAILED",
                "error_code": "PHASE1_COMMAND_FAILED",
            }
        )
    )
    return 1


def run_case(
    *,
    project_root: Path,
    case_id: str,
    namespace: str,
    gateway: ModelGateway,
    model_name: str,
) -> AgentRunReport:
    settings = load_agent_settings(project_root)
    replay_case = load_replay_case(
        Path(project_root) / VISIBLE_REPLAY_ROOT,
        case_id,
    )
    run_id = stable_run_id(namespace, case_id)
    agent = SingleAgent(
        gateway=gateway,
        backend=ReplayObservabilityBackend(replay_case),
        model_configuration=ModelConfiguration(
            model_name=model_name,
            temperature=0.0,
            model_timeout_seconds=settings.model_timeout_seconds,
        ),
        tool_timeout_seconds=settings.tool_timeout_seconds,
    )
    return agent.run(
        InvestigationRequest(
            schema_version="phase1.investigation-request.v1",
            request_id=f"{namespace}-{case_id}",
            run_id=run_id,
            agent_id="single-agent",
            task_id="root-cause-analysis",
            incident=replay_case.incident,
            budgets=settings.budgets,
        )
    )


def _provider_case_result(
    case_id: str,
    report: AgentRunReport,
) -> dict[str, object]:
    final = report.final_rca
    return {
        "case_id": case_id,
        "run_id": report.run_id,
        "terminal_status": report.terminal_status.value,
        "schema_valid": report.schema_valid,
        "evidence_references_valid": report.evidence_references_valid,
        "decision": final.decision.value if final is not None else None,
        "root_service": final.root_service if final is not None else None,
        "fault_mechanism": (
            final.fault_mechanism.value
            if final is not None and final.fault_mechanism is not None
            else None
        ),
        "agent_run_report": report.model_dump(mode="json"),
    }


def run_provider_smoke(
    *,
    project_root: Path,
    environment: Mapping[str, str],
    transport: OpenAICompatibleTransport | None = None,
) -> dict[str, object]:
    """Run the opt-in two-decision provider gate without embedding a key."""

    if any(
        not isinstance(environment.get(name), str)
        or not environment.get(name, "").strip()
        for name in _PROVIDER_ENVIRONMENT_NAMES
    ):
        return {
            "schema_version": "phase1.provider-smoke-report.v1",
            "status": "SKIPPED_NOT_CONFIGURED",
            "provider": "openai-compatible",
            "model": None,
            "case_results": [],
            "requirements": {
                "validated_confirmed": False,
                "validated_non_confirmed": False,
            },
        }
    config = OpenAICompatibleConfig.from_environment(environment)
    if config is None:  # Defensive: the explicit completeness check is above.
        raise RuntimeError("complete provider configuration was not loaded")
    effective_transport = (
        transport
        if transport is not None
        else StdlibOpenAICompatibleTransport()
    )
    case_results: list[dict[str, object]] = []
    validated_confirmed = False
    validated_non_confirmed = False
    for case_id, required_decision in _PROVIDER_CASES:
        report = run_case(
            project_root=project_root,
            case_id=case_id,
            namespace="provider-smoke",
            gateway=OpenAICompatibleGateway(
                config=config,
                transport=effective_transport,
            ),
            model_name=config.model,
        )
        result = _provider_case_result(case_id, report)
        case_results.append(result)
        valid = (
            report.terminal_status.value == "COMPLETED"
            and report.schema_valid
            and report.evidence_references_valid
            and report.final_rca is not None
            and report.final_rca.decision is required_decision
        )
        if required_decision is RCADecision.RCA_CONFIRMED:
            validated_confirmed = valid
        else:
            validated_non_confirmed = valid
        if not valid:
            break
    passed = validated_confirmed and validated_non_confirmed
    return {
        "schema_version": "phase1.provider-smoke-report.v1",
        "status": "PASSED" if passed else "FAILED",
        "provider": "openai-compatible",
        "model": config.model,
        "case_results": case_results,
        "requirements": {
            "validated_confirmed": validated_confirmed,
            "validated_non_confirmed": validated_non_confirmed,
        },
    }


def _command_replay_smoke(
    *,
    project_root: Path,
    artifact_root: Path,
) -> int:
    command = "replay-smoke"
    case_id = "ad-partial-failure-complete"
    run_id = stable_run_id(command, case_id)
    relative_path = Path(f"reports/{run_id}/agent-run-report.json")
    _write_command_attempt(
        artifact_root=artifact_root,
        relative_path=relative_path,
        command=command,
        status="IN_PROGRESS",
    )
    try:
        report = run_case(
            project_root=project_root,
            case_id=case_id,
            namespace=command,
            gateway=ScriptedModelGateway(),
            model_name="scripted-replay-v1",
        )
        passed = (
            report.run_id == run_id
            and report.terminal_status.value == "COMPLETED"
            and report.schema_valid
            and report.evidence_references_valid
        )
        report_path = atomic_write_json(
            artifact_root=artifact_root,
            relative_path=relative_path,
            payload=report.model_dump(mode="json"),
        )
        if not passed:
            print(
                _compact_json(
                    {
                        "case_id": case_id,
                        "decision": (
                            report.final_rca.decision.value
                            if report.final_rca is not None
                            else None
                        ),
                        "report_path": str(report_path),
                        "status": "FAILED",
                    }
                )
            )
            return 1
    except Exception:
        return _record_command_failure(
            artifact_root=artifact_root,
            relative_path=relative_path,
            command=command,
        )
    print(
        _compact_json(
            {
                "case_id": case_id,
                "decision": (
                    report.final_rca.decision.value
                    if report.final_rca is not None
                    else None
                ),
                "report_path": str(report_path),
                "status": "PASSED",
            }
        )
    )
    return 0


def _command_eval(*, project_root: Path, artifact_root: Path) -> int:
    command = "eval"
    relative_path = Path("evaluation/evaluation-report.json")
    _write_command_attempt(
        artifact_root=artifact_root,
        relative_path=relative_path,
        command=command,
        status="IN_PROGRESS",
    )
    try:
        evaluator = _load_evaluator_module(project_root)
        report = evaluator.run_evaluation(project_root)
        if not isinstance(report, dict) or report.get("status") not in {
            "PASSED",
            "FAILED",
        }:
            raise ValueError("evaluator returned an invalid report")
        atomic_write_json(
            artifact_root=artifact_root,
            relative_path=relative_path,
            payload=report,
        )
    except Exception:
        return _record_command_failure(
            artifact_root=artifact_root,
            relative_path=relative_path,
            command=command,
        )
    print(_compact_json(report))
    return 0 if report["status"] == "PASSED" else 1


def _command_provider_smoke(
    *,
    project_root: Path,
    artifact_root: Path,
    environment: Mapping[str, str],
    transport: OpenAICompatibleTransport | None,
) -> int:
    command = "provider-smoke"
    relative_path = Path("provider-smoke/provider-smoke-report.json")
    _write_command_attempt(
        artifact_root=artifact_root,
        relative_path=relative_path,
        command=command,
        status="IN_PROGRESS",
    )
    try:
        report = run_provider_smoke(
            project_root=project_root,
            environment=environment,
            transport=transport,
        )
        if not isinstance(report, dict) or report.get("status") not in {
            "PASSED",
            "FAILED",
            "SKIPPED_NOT_CONFIGURED",
        }:
            raise ValueError("provider smoke returned an invalid report")
        atomic_write_json(
            artifact_root=artifact_root,
            relative_path=relative_path,
            payload=report,
        )
    except Exception:
        return _record_command_failure(
            artifact_root=artifact_root,
            relative_path=relative_path,
            command=command,
        )
    print(_compact_json(report))
    print(report["status"])
    return 0 if report["status"] in {"PASSED", "SKIPPED_NOT_CONFIGURED"} else 1


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    artifact_root: Path | None = None,
    environment: Mapping[str, str] | None = None,
    transport: OpenAICompatibleTransport | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or arguments[0] not in {
        "replay-smoke",
        "eval",
        "provider-smoke",
    }:
        print(
            _compact_json(
                {
                    "status": "FAILED",
                    "error_code": "INVALID_COMMAND",
                }
            )
        )
        return 2
    root = Path(project_root)
    output_root = (
        root / "artifacts/phase1"
        if artifact_root is None
        else Path(artifact_root)
    )
    source_environment = os.environ if environment is None else environment
    try:
        if arguments[0] == "replay-smoke":
            return _command_replay_smoke(
                project_root=root,
                artifact_root=output_root,
            )
        if arguments[0] == "eval":
            return _command_eval(
                project_root=root,
                artifact_root=output_root,
            )
        return _command_provider_smoke(
            project_root=root,
            artifact_root=output_root,
            environment=source_environment,
            transport=transport,
        )
    except Exception:
        print(
            _compact_json(
                {
                    "status": "FAILED",
                    "error_code": "PHASE1_COMMAND_FAILED",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
