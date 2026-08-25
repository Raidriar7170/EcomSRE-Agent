"""Append-only proof for a pre-Docker missing-upstream admission terminal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_preflight_v225 import (
    GitCommandRunnerV225,
    run_read_only_git_v225,
)
from ecomsre.evidence.models import CommandLog
from ecomsre_live_sandbox.contracts import CleanupResult, write_private_json


_UPSTREAM_PATH = "third_party/opentelemetry-demo"
_UPSTREAM_FLAG_PATH = "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
_PINNED_UPSTREAM = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
_FAILED_HEAD = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class CodexExecutionBindingV225:
    rollout_path: Path
    rollout_session_id: str
    rollout_prefix_sha256: str
    rollout_record_ordinals: tuple[int, int, int]
    rollout_record_sha256s: tuple[str, str, str]
    tool_call_id: str
    command_sha256: str
    failure_output_sha256: str


_FROZEN_CODEX_EXECUTION_BINDING = CodexExecutionBindingV225(
    rollout_path=(
        Path.home() / ".codex/sessions/2026/08/24/"
        "rollout-2026-08-24T21-15-48-01a033e9-b29d-7602-aead-acc8813ed57a.jsonl"
    ),
    rollout_session_id="01a033e9-b29d-7602-aead-acc8813ed57a",
    rollout_prefix_sha256=(
        "970194e9fcf37bb307f45daba516b4b15f267453847efa70ea36b6ed6011ae14"
    ),
    rollout_record_ordinals=(2573, 2574, 2575),
    rollout_record_sha256s=(
        "731854d6bf6ca9201f36a665a7109e633a28d4499e43cddb83a074db993a9e6d",
        "55d3775b5bc3dd873a3385b6a6d53d32f9895d6958e5503ada0798f354718a9e",
        "0a83dbf0c910ab643ff1810836e38ecfd7e1264c2bc3971dd58d2fa262ab929e",
    ),
    tool_call_id="call_0XsuPTBFlwvTZR6Q1L5cw4PL",
    command_sha256=("f19e9880a12635d64fa5ab1e8ad8aedb8a87c072a2ea5b3f694846b478a500c7"),
    failure_output_sha256=(
        "5e5adff391b6f43dc28e66b32e0ff6e3f5077cc5dfc79e25dab7116dc7a492a8"
    ),
)


class CommandStreamV225(DtaModelV22):
    schema_version: Literal["phase0.command-stream.v1"]
    stream: Literal["stdout", "stderr"]
    encoding: Literal["utf-8"]
    content: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_stream_digest(self) -> CommandStreamV225:
        if hashlib.sha256(self.content.encode()).hexdigest() != self.content_sha256:
            raise ValueError("admission reconciliation command stream digest differs")
        return self


class CodexExecutionProofV225(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.codex-execution-proof.v1"]
    rollout_session_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    rollout_path: str
    rollout_prefix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rollout_source_size_bytes: int = Field(gt=0)
    rollout_record_ordinals: tuple[int, int, int]
    rollout_record_sha256s: tuple[str, str, str]
    tool_call_id: str = Field(pattern=r"^call_[A-Za-z0-9_]+$")
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_root: str
    private_root: str
    provider_env_path: str
    lease_root: str
    exit_code: Literal[1]
    exception_type: Literal["FileNotFoundError"]
    missing_path: str
    failure_output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    failed_at: datetime
    output_recorded_at: datetime
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_execution_proof(self) -> CodexExecutionProofV225:
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.started_at, self.failed_at, self.output_recorded_at)
        ):
            raise ValueError("Codex execution proof timestamp lacks timezone")
        if not self.started_at <= self.failed_at <= self.output_recorded_at:
            raise ValueError("Codex execution proof timestamps differ")
        first, event, output = self.rollout_record_ordinals
        if (event, output) != (first + 1, first + 2):
            raise ValueError("Codex execution proof ordinals are not adjacent")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"proof_sha256"})
        )
        if self.proof_sha256 != expected:
            raise ValueError("Codex execution proof digest differs")
        return self


class AdmissionNoEffectReconciliationV225(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.admission-no-effect-reconciliation.v1"]
    campaign_id: Literal["campaign-0001"]
    disposition: Literal["NOT_STARTED_MISSING_PINNED_UPSTREAM_NO_DOCKER_BOUNDARY"]
    failed_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    primary_terminal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    admission_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pinned_upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    missing_upstream_path: Literal[
        "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    ]
    initial_evidence_files: tuple[str, ...]
    execution_proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sealed_evidence_files: tuple[str, ...]
    sealed_evidence_directories: tuple[str, ...]
    sealed_evidence_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    docker_boundary_reached: Literal[False]
    baseline_capture_exists: Literal[False]
    fault_capture_exists: Literal[False]
    provider_shadow_exists: Literal[False]
    paired_result_exists: Literal[False]
    replacement_cause: Literal["LOCAL_ENVIRONMENT"]
    baseline_restored: Literal[True]
    cleanup: CleanupResult
    reconciled_at: datetime
    reconciliation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_no_effect_reconciliation(
        self,
    ) -> AdmissionNoEffectReconciliationV225:
        if self.reconciled_at.tzinfo is None or self.reconciled_at.utcoffset() is None:
            raise ValueError("admission reconciliation timestamp lacks timezone")
        if self.cleanup.verdict != "CLEAN":
            raise ValueError("admission reconciliation cleanup is not CLEAN")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"reconciliation_sha256"})
        )
        if self.reconciliation_sha256 != expected:
            raise ValueError("admission reconciliation digest differs")
        return self


def _read_json(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            f"admission reconciliation evidence is not regular: {path.name}"
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("admission reconciliation evidence is not an object")
    return cast(dict[str, object], value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_regular_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"admission reconciliation {label} is not a regular directory")


def _sealed_evidence(
    primary: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    _require_regular_directory(primary, label="primary root")
    descendants = tuple(primary.rglob("*"))
    if any(path.is_symlink() for path in descendants):
        raise ValueError("admission reconciliation sealed evidence contains a symlink")
    files = tuple(
        sorted(
            path.relative_to(primary).as_posix()
            for path in descendants
            if path.is_file()
            and path.relative_to(primary).as_posix()
            != "post-terminal-reconciliation.json"
        )
    )
    directories = tuple(
        sorted(
            path.relative_to(primary).as_posix()
            for path in descendants
            if path.is_dir()
        )
    )
    tree_sha256 = semantic_sha256_v22(
        {
            "directories": directories,
            "files": {relative: _sha256(primary / relative) for relative in files},
        }
    )
    return files, directories, tree_sha256


def _initial_evidence(primary: Path) -> tuple[str, ...]:
    _require_regular_directory(primary, label="primary root")
    if any(path.is_symlink() for path in primary.rglob("*")):
        raise ValueError("admission reconciliation evidence contains a symlink")
    files = tuple(
        sorted(
            path.relative_to(primary).as_posix()
            for path in primary.rglob("*")
            if path.is_file()
        )
    )
    command_logs = tuple(
        primary.glob("git-audit/observer-visible/*/commands/*.command-log.json")
    )
    process_audits = tuple(
        primary.glob("git-audit/observer-visible/*/commands/process-audit.jsonl")
    )
    stdout_streams = tuple(
        primary.glob("git-audit/evaluator-only/*/commands/*.stdout.json")
    )
    stderr_streams = tuple(
        primary.glob("git-audit/evaluator-only/*/commands/*.stderr.json")
    )
    expected = {
        "blocked-terminal.json",
        "owned-sandbox/control/capture-plan.json",
        *(path.relative_to(primary).as_posix() for path in command_logs),
        *(path.relative_to(primary).as_posix() for path in process_audits),
        *(path.relative_to(primary).as_posix() for path in stdout_streams),
        *(path.relative_to(primary).as_posix() for path in stderr_streams),
    }
    if (
        len(command_logs) != 1
        or len(process_audits) != 1
        or len(stdout_streams) != 1
        or len(stderr_streams) != 1
        or set(files) != expected
    ):
        raise ValueError("admission reconciliation file inventory differs")
    return files


def _failed_head_from_audit(
    primary: Path, *, repository: Path
) -> tuple[str, CommandLog]:
    command_log_path = next(
        primary.glob("git-audit/observer-visible/*/commands/*.command-log.json")
    )
    process_audit_path = next(
        primary.glob("git-audit/observer-visible/*/commands/process-audit.jsonl")
    )
    stdout_path = next(
        primary.glob("git-audit/evaluator-only/*/commands/*.stdout.json")
    )
    stderr_path = next(
        primary.glob("git-audit/evaluator-only/*/commands/*.stderr.json")
    )
    command_id = command_log_path.name.removesuffix(".command-log.json")
    run_id = command_log_path.parents[1].name
    expected_run_id = hashlib.sha256(b"campaign-0001").hexdigest()[:32]
    if run_id != expected_run_id:
        raise ValueError("admission reconciliation git audit run id differs")
    expected_observer = primary / "git-audit/observer-visible" / run_id / "commands"
    expected_evaluator = primary / "git-audit/evaluator-only" / run_id / "commands"
    if (
        command_log_path.parent != expected_observer
        or process_audit_path.parent != expected_observer
    ):
        raise ValueError("admission reconciliation observer audit path differs")
    if (
        stdout_path.parent != expected_evaluator
        or stderr_path.parent != expected_evaluator
    ):
        raise ValueError("admission reconciliation evaluator audit path differs")
    if (
        stdout_path.name != f"{command_id}.stdout.json"
        or stderr_path.name != f"{command_id}.stderr.json"
    ):
        raise ValueError("admission reconciliation command stream path differs")

    command_log_raw = _read_json(command_log_path)
    process_lines = process_audit_path.read_text(encoding="utf-8").splitlines()
    if len(process_lines) != 1:
        raise ValueError("admission reconciliation process audit differs")
    process_log_raw = json.loads(process_lines[0])
    command_log = CommandLog.model_validate(command_log_raw)
    process_log = CommandLog.model_validate(process_log_raw)
    if command_log != process_log:
        raise ValueError("admission reconciliation process record differs")
    if (
        command_log.run_id != expected_run_id
        or command_log.command != "git"
        or command_log.arguments != ("git", "rev-parse", "HEAD")
        or command_log.working_directory != str(repository)
        or command_log.timeout_seconds != 30.0
        or command_log.process_timed_out
        or command_log.process_exit_code != 0
        or command_log.classification.value != "SUCCESS"
        or command_log.terminal_exit_code != 0
        or command_log.reason_code != "PROCESS_EXIT_ZERO"
        or command_log.network_access_declared
        or command_log.network_access_scope != "NONE"
        or command_log.filesystem_write_scope != ()
        or command_log.observed_effect_scope != ("NOT_OBSERVED",)
        or command_log.stdout_artifact != f"commands/{command_id}.stdout.json"
        or command_log.stderr_artifact != f"commands/{command_id}.stderr.json"
    ):
        raise ValueError("admission reconciliation git audit differs")

    stdout = CommandStreamV225.model_validate(_read_json(stdout_path))
    stderr = CommandStreamV225.model_validate(_read_json(stderr_path))
    if stdout.stream != "stdout" or stderr.stream != "stderr" or stderr.content != "":
        raise ValueError("admission reconciliation command streams differ")
    if (
        command_log.stdout_sha256 != stdout.content_sha256
        or command_log.stderr_sha256 != stderr.content_sha256
    ):
        raise ValueError("admission reconciliation stream binding differs")
    failed_head = stdout.content.strip()
    if _FAILED_HEAD.fullmatch(failed_head) is None:
        raise ValueError("admission reconciliation failed HEAD differs")
    return failed_head, command_log


def _parse_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Codex execution proof {label} timestamp differs")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError(f"Codex execution proof {label} timestamp lacks timezone")
    return parsed


def _codex_execution_proof(
    *,
    repository: Path,
    private: Path,
    provider_env: Path,
    lease_root: Path,
) -> CodexExecutionProofV225:
    binding = _FROZEN_CODEX_EXECUTION_BINDING
    rollout_path = binding.rollout_path
    if rollout_path.is_symlink():
        raise ValueError("Codex execution proof rollout is a symbolic link")
    rollout = rollout_path.resolve(strict=True)
    metadata = rollout.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o022
    ):
        raise PermissionError("Codex execution proof rollout ownership differs")

    source = rollout.read_bytes()
    raw_lines = source.splitlines(keepends=True)
    records: list[dict[str, object]] = []
    for raw in raw_lines:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Codex execution proof rollout record differs")
        records.append(cast(dict[str, object], value))
    session_ids = {
        cast(dict[str, object], record.get("payload", {})).get("id")
        for record in records
        if record.get("type") == "session_meta"
        and isinstance(record.get("payload"), dict)
    }
    if len(session_ids) != 1:
        raise ValueError("Codex execution proof session identity differs")
    session_id = next(iter(session_ids))
    if not isinstance(session_id, str):
        raise ValueError("Codex execution proof session identity differs")

    expected_command = (
        "PYTHONPATH=src uv run python -m "
        "ecomsre.dta_v2.v22.real_fault_cli_v225 "
        f'--repository-root "{repository}" '
        f'--provider-env "{provider_env}" '
        f'--private-root "{private}" '
        f'--lease-root "{lease_root}"'
    )
    expected_argv = ["/bin/zsh", "-lc", expected_command]
    event_indexes: list[int] = []
    for index, record in enumerate(records):
        candidate_payload = record.get("payload")
        if not isinstance(candidate_payload, dict) or record.get("type") != "event_msg":
            continue
        item = candidate_payload.get("item")
        if (
            candidate_payload.get("type") == "item_completed"
            and isinstance(item, dict)
            and item.get("type") == "CommandExecution"
            and item.get("command") == expected_argv
            and item.get("cwd") == repository.as_uri()
        ):
            event_indexes.append(index)
    if len(event_indexes) != 1:
        raise ValueError("Codex execution proof exact command count differs")
    event_index = event_indexes[0]
    if event_index == 0 or event_index + 1 >= len(records):
        raise ValueError("Codex execution proof record adjacency differs")
    call_record = records[event_index - 1]
    event_record = records[event_index]
    output_record = records[event_index + 1]
    call_payload = call_record.get("payload")
    event_payload = event_record.get("payload")
    output_payload = output_record.get("payload")
    if (
        not isinstance(call_payload, dict)
        or not isinstance(event_payload, dict)
        or not isinstance(output_payload, dict)
    ):
        raise ValueError("Codex execution proof payload differs")
    event_item = event_payload.get("item")
    if not isinstance(event_item, dict):
        raise ValueError("Codex execution proof command event differs")
    call_id = call_payload.get("call_id")
    call_input = call_payload.get("input")
    escaped_command = expected_command.replace('"', '\\"')
    if (
        call_record.get("type") != "response_item"
        or call_payload.get("type") != "custom_tool_call"
        or call_payload.get("name") != "exec"
        or not isinstance(call_id, str)
        or not isinstance(call_input, str)
        or escaped_command not in call_input
        or f'workdir:"{repository}"' not in call_input
        or "--replacement" in call_input
        or "--reconcile-primary-admission" in call_input
    ):
        raise ValueError("Codex execution proof tool call differs")
    stdout = event_item.get("stdout")
    missing_absolute = repository / _UPSTREAM_FLAG_PATH
    missing_error = (
        f"FileNotFoundError: [Errno 2] No such file or directory: '{missing_absolute}'"
    )
    terminal = "RealFaultLiveSequenceError: BLOCKED_DTA_V225_REAL_FAULT_ENVIRONMENT"
    if (
        event_item.get("status") != "failed"
        or event_item.get("exit_code") != 1
        or event_item.get("stderr") != ""
        or not isinstance(stdout, str)
        or stdout.count(missing_error) != 1
        or stdout.count(terminal) != 1
        or stdout.index(missing_error) >= stdout.index(terminal)
        or event_item.get("aggregated_output", stdout) != stdout
        or event_item.get("formatted_output", stdout) != stdout
    ):
        raise ValueError("Codex execution proof failure output differs")
    output_blocks = output_payload.get("output")
    if (
        output_record.get("type") != "response_item"
        or output_payload.get("type") != "custom_tool_call_output"
        or output_payload.get("call_id") != call_id
        or not isinstance(output_blocks, list)
    ):
        raise ValueError("Codex execution proof output record differs")
    structured_outputs: list[dict[str, object]] = []
    for block in output_blocks:
        if not isinstance(block, dict) or block.get("type") != "input_text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.get("output") == stdout:
            structured_outputs.append(cast(dict[str, object], candidate))
    if len(structured_outputs) != 1 or structured_outputs[0].get("exit_code") != 1:
        raise ValueError("Codex execution proof returned command output differs")

    ordinal_values = tuple(
        record.get("ordinal") for record in (call_record, event_record, output_record)
    )
    if not all(isinstance(value, int) for value in ordinal_values):
        raise ValueError("Codex execution proof record ordinal differs")
    ordinals = cast(tuple[int, int, int], ordinal_values)
    record_sha256s = cast(
        tuple[str, str, str],
        tuple(
            hashlib.sha256(raw_lines[index]).hexdigest()
            for index in (event_index - 1, event_index, event_index + 1)
        ),
    )
    proof_payload: dict[str, Any] = {
        "schema_version": "dta-v225-real-fault.codex-execution-proof.v1",
        "rollout_session_id": session_id,
        "rollout_path": str(rollout),
        "rollout_prefix_sha256": hashlib.sha256(
            b"".join(raw_lines[: event_index + 2])
        ).hexdigest(),
        "rollout_source_size_bytes": len(source),
        "rollout_record_ordinals": ordinals,
        "rollout_record_sha256s": record_sha256s,
        "tool_call_id": call_id,
        "command_sha256": hashlib.sha256(expected_command.encode()).hexdigest(),
        "repository_root": str(repository),
        "private_root": str(private),
        "provider_env_path": str(provider_env),
        "lease_root": str(lease_root),
        "exit_code": 1,
        "exception_type": "FileNotFoundError",
        "missing_path": str(missing_absolute),
        "failure_output_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "started_at": _parse_timestamp(call_record.get("timestamp"), label="start"),
        "failed_at": _parse_timestamp(event_record.get("timestamp"), label="failure"),
        "output_recorded_at": _parse_timestamp(
            output_record.get("timestamp"), label="output"
        ),
    }
    draft = cast(Any, CodexExecutionProofV225).model_construct(
        **proof_payload, proof_sha256="0" * 64
    )
    proof = CodexExecutionProofV225.model_validate(
        {
            **proof_payload,
            "proof_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"proof_sha256"})
            ),
        }
    )
    observed_binding = CodexExecutionBindingV225(
        rollout_path=Path(proof.rollout_path),
        rollout_session_id=proof.rollout_session_id,
        rollout_prefix_sha256=proof.rollout_prefix_sha256,
        rollout_record_ordinals=proof.rollout_record_ordinals,
        rollout_record_sha256s=proof.rollout_record_sha256s,
        tool_call_id=proof.tool_call_id,
        command_sha256=proof.command_sha256,
        failure_output_sha256=proof.failure_output_sha256,
    )
    if observed_binding != binding:
        raise ValueError("Codex execution proof frozen binding differs")
    return proof


def _verify_failed_source_order(source: str) -> None:
    markers = (
        'control_root / "capture-plan.json"',
        _UPSTREAM_FLAG_PATH,
        'read_text(encoding="utf-8")',
        'flag_directory = runtime_root / "flagd"',
        "environment = SandboxEnvironment(",
        "environment.verify_local_docker()",
    )
    cursor = -1
    for marker in markers:
        cursor = source.find(marker, cursor + 1)
        if cursor < 0:
            raise ValueError("admission reconciliation source order differs")


def reconcile_missing_upstream_admission_v225(
    *,
    repository_root: Path,
    private_root: Path,
    git_runner: GitCommandRunnerV225,
    provider_env_path: Path,
    lease_root: Path,
) -> AdmissionNoEffectReconciliationV225:
    """Prove one exact admission failed before the Docker boundary."""

    repository = repository_root.resolve(strict=True)
    if private_root.is_symlink():
        raise ValueError("admission reconciliation private root is a symbolic link")
    private = private_root.resolve(strict=True)
    primary = private / "campaign-0001"
    _require_regular_directory(private, label="private root")
    _require_regular_directory(primary, label="primary root")
    reconciliation_path = primary / "post-terminal-reconciliation.json"
    if reconciliation_path.exists() or reconciliation_path.is_symlink():
        raise FileExistsError("admission reconciliation is write-once")
    if (private / "replacement-campaign-claim.json").exists():
        raise PermissionError("admission reconciliation followed replacement claim")
    blocked_path = primary / "blocked-terminal.json"
    capture_plan_path = primary / "owned-sandbox/control/capture-plan.json"
    blocked = _read_json(blocked_path)
    if blocked != {
        "schema_version": "dta-v225-real-fault.blocked-terminal.v1",
        "terminal": "BLOCKED_DTA_V225_REAL_FAULT_ENVIRONMENT",
        "stage": "ADMISSION",
        "baseline_capture_exists": False,
        "fault_capture_exists": False,
        "provider_shadow_exists": False,
        "replacement_cause": "NONE",
        "baseline_restored": False,
        "cleanup": None,
    }:
        raise ValueError("admission reconciliation primary terminal differs")
    files = _initial_evidence(primary)
    failed_head, failed_git_log = _failed_head_from_audit(
        primary, repository=repository
    )
    provider_env = provider_env_path.resolve(strict=True)
    lease = lease_root.resolve(strict=True)
    execution_proof = _codex_execution_proof(
        repository=repository,
        private=private,
        provider_env=provider_env,
        lease_root=lease,
    )
    primary_claim = _read_json(private / "primary-campaign-claim.json")
    if (
        primary_claim.get("schema_version")
        != "dta-v225-real-fault.primary-campaign-claim.v1"
        or primary_claim.get("campaign_id") != "campaign-0001"
    ):
        raise ValueError("admission reconciliation primary claim differs")
    claimed_at = _parse_timestamp(
        primary_claim.get("claimed_at"), label="primary claim"
    )
    if not (
        execution_proof.started_at
        <= claimed_at
        <= failed_git_log.started_at
        <= failed_git_log.ended_at
        <= execution_proof.failed_at
    ):
        raise ValueError("admission reconciliation execution chronology differs")
    upstream_flag = repository / _UPSTREAM_FLAG_PATH
    if upstream_flag.exists() or upstream_flag.is_symlink():
        raise ValueError("admission reconciliation upstream path is no longer absent")
    gitlink = run_read_only_git_v225(
        git_runner, "ls-tree", failed_head, "--", _UPSTREAM_PATH
    )
    if gitlink != f"160000 commit {_PINNED_UPSTREAM}\t{_UPSTREAM_PATH}":
        raise ValueError("admission reconciliation upstream gitlink differs")
    source = run_read_only_git_v225(
        git_runner,
        "show",
        f"{failed_head}:src/ecomsre/dta_v2/owned_capture.py",
    )
    _verify_failed_source_order(source)
    execution_proof_path = primary / "codex-execution-proof.json"
    execution_proof_sha256 = write_private_json(
        execution_proof_path, execution_proof, create_once=True
    )
    sealed_files, sealed_directories, sealed_tree_sha256 = _sealed_evidence(primary)
    cleanup = CleanupResult(
        baseline_restored=True,
        owned_containers=0,
        owned_networks=0,
        owned_volumes=0,
        non_owned_resources_changed=False,
        verdict="CLEAN",
    )
    payload = {
        "schema_version": "dta-v225-real-fault.admission-no-effect-reconciliation.v1",
        "campaign_id": "campaign-0001",
        "disposition": "NOT_STARTED_MISSING_PINNED_UPSTREAM_NO_DOCKER_BOUNDARY",
        "failed_code_head": failed_head,
        "primary_terminal_sha256": _sha256(blocked_path),
        "capture_plan_sha256": _sha256(capture_plan_path),
        "admission_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "pinned_upstream_commit": _PINNED_UPSTREAM,
        "missing_upstream_path": _UPSTREAM_FLAG_PATH,
        "initial_evidence_files": files,
        "execution_proof_sha256": execution_proof_sha256,
        "sealed_evidence_files": sealed_files,
        "sealed_evidence_directories": sealed_directories,
        "sealed_evidence_tree_sha256": sealed_tree_sha256,
        "docker_boundary_reached": False,
        "baseline_capture_exists": False,
        "fault_capture_exists": False,
        "provider_shadow_exists": False,
        "paired_result_exists": False,
        "replacement_cause": "LOCAL_ENVIRONMENT",
        "baseline_restored": True,
        "cleanup": cleanup,
        "reconciled_at": datetime.now(timezone.utc),
    }
    draft = cast(Any, AdmissionNoEffectReconciliationV225).model_construct(
        **payload, reconciliation_sha256="0" * 64
    )
    reconciliation = AdmissionNoEffectReconciliationV225.model_validate(
        {
            **payload,
            "reconciliation_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"reconciliation_sha256"})
            ),
        }
    )
    write_private_json(reconciliation_path, reconciliation, create_once=True)
    return reconciliation


def load_admission_reconciliation_v225(
    path: Path,
) -> AdmissionNoEffectReconciliationV225:
    if path.is_symlink() or not path.is_file():
        raise ValueError("admission reconciliation is not a regular file")
    return AdmissionNoEffectReconciliationV225.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def verify_admission_reconciliation_tree_v225(
    *, primary_root: Path, reconciliation: AdmissionNoEffectReconciliationV225
) -> bool:
    files, directories, tree_sha256 = _sealed_evidence(primary_root)
    return (
        files == reconciliation.sealed_evidence_files
        and directories == reconciliation.sealed_evidence_directories
        and tree_sha256 == reconciliation.sealed_evidence_tree_sha256
    )


__all__ = (
    "AdmissionNoEffectReconciliationV225",
    "CodexExecutionBindingV225",
    "CodexExecutionProofV225",
    "load_admission_reconciliation_v225",
    "reconcile_missing_upstream_admission_v225",
    "verify_admission_reconciliation_tree_v225",
)
