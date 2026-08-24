from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ecomsre.dta_v2.v22.real_fault_reconciliation_v225 as reconciliation_module
from ecomsre.dta_v2.v22.real_fault_cli_v225 import (
    _claim_campaign_v225,
    _claim_final_execution_v225,
    _parser,
    _replacement_cause_v225,
)
from ecomsre.dta_v2.v22.real_fault_preflight_v225 import NoHealthyComparatorV225
from ecomsre.dta_v2.v22.real_fault_reconciliation_v225 import (
    reconcile_missing_upstream_admission_v225,
)
from ecomsre_live_sandbox.environment import DockerBoundaryError
from ecomsre.dta_v2.v22.real_fault_study_v225 import (
    build_manifest_v225,
    build_pre_live_freeze_v225,
)
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    write_private_json,
)


def _freeze():
    return build_pre_live_freeze_v225(
        code_head="1" * 40,
        comparator_service="recommendation",
        alias_map_set_sha256="2" * 64,
        flat_prompt_sha256="3" * 64,
        current_prompt_sha256="4" * 64,
        scorer_sha256="5" * 64,
    )


def _manifest():
    return build_manifest_v225(
        pre_live_freeze=_freeze(),
        capture_pair_sha256="6" * 64,
        case_set_sha256="7" * 64,
        truth_set_sha256="8" * 64,
    )


def test_final_manifest_binds_capture_case_and_truth_before_execution() -> None:
    manifest = _manifest()
    changed = build_manifest_v225(
        pre_live_freeze=manifest.pre_live_freeze,
        capture_pair_sha256=manifest.capture_pair_sha256,
        case_set_sha256="9" * 64,
        truth_set_sha256=manifest.truth_set_sha256,
    )

    assert manifest.capture_pair_sha256 == "6" * 64
    assert manifest.case_set_sha256 == "7" * 64
    assert manifest.truth_set_sha256 == "8" * 64
    assert manifest.manifest_sha256 != changed.manifest_sha256


def test_campaign_claim_allows_only_fixed_primary_then_eligible_replacement(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)

    assert (
        _claim_campaign_v225(private_root=private, replacement=False) == "campaign-0001"
    )
    with pytest.raises(FileExistsError):
        _claim_campaign_v225(private_root=private, replacement=False)
    primary = private / "campaign-0001"
    ensure_private_directory(primary)
    write_private_json(
        primary / "blocked-terminal.json",
        {
            "stage": "COMPARATOR_SELECTION",
            "baseline_capture_exists": False,
            "fault_capture_exists": False,
            "provider_shadow_exists": False,
            "replacement_cause": "TELEMETRY",
            "baseline_restored": True,
            "cleanup": {
                "verdict": "CLEAN",
                "non_owned_resources_changed": False,
            },
        },
        create_once=True,
    )

    assert (
        _claim_campaign_v225(private_root=private, replacement=True) == "campaign-0002"
    )
    with pytest.raises(FileExistsError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_replacement_is_forbidden_after_any_provider_shadow(tmp_path: Path) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)
    assert (
        _claim_campaign_v225(private_root=private, replacement=False) == "campaign-0001"
    )
    primary = private / "campaign-0001"
    ensure_private_directory(primary)
    write_private_json(
        primary / "blocked-terminal.json",
        {
            "stage": "COMPARATOR_SELECTION",
            "baseline_capture_exists": False,
            "fault_capture_exists": False,
            "provider_shadow_exists": True,
            "replacement_cause": "TELEMETRY",
            "baseline_restored": True,
            "cleanup": {
                "verdict": "CLEAN",
                "non_owned_resources_changed": False,
            },
        },
        create_once=True,
    )

    with pytest.raises(PermissionError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_replacement_is_forbidden_for_preflight_or_implementation_failure(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)
    assert (
        _claim_campaign_v225(private_root=private, replacement=False) == "campaign-0001"
    )
    primary = private / "campaign-0001"
    ensure_private_directory(primary)
    write_private_json(
        primary / "blocked-terminal.json",
        {
            "stage": "STATIC_PREFLIGHT",
            "baseline_capture_exists": False,
            "fault_capture_exists": False,
            "provider_shadow_exists": False,
            "replacement_cause": "NONE",
            "baseline_restored": True,
            "cleanup": {
                "verdict": "CLEAN",
                "non_owned_resources_changed": False,
            },
        },
        create_once=True,
    )

    with pytest.raises(PermissionError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_final_execution_claim_is_create_once_and_campaign_id_is_closed(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)
    manifest = _manifest()

    _claim_final_execution_v225(
        private_root=private, campaign_id="campaign-0001", manifest=manifest
    )
    claim = json.loads(
        (private / "final-execution-claim.json").read_text(encoding="utf-8")
    )
    assert claim["maximum_execution_count"] == 1
    assert claim["manifest_sha256"] == manifest.manifest_sha256
    with pytest.raises(FileExistsError):
        _claim_final_execution_v225(
            private_root=private, campaign_id="campaign-0001", manifest=manifest
        )
    with pytest.raises(ValueError):
        _claim_final_execution_v225(
            private_root=private, campaign_id="campaign-9999", manifest=manifest
        )


def test_cli_rejects_arbitrary_campaign_ids() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "--repository-root",
                "/repo",
                "--provider-env",
                "/env",
                "--private-root",
                "/private",
                "--lease-root",
                "/lease",
                "--campaign-id",
                "campaign-9999",
            ]
        )


def test_replacement_cause_requires_explicit_trusted_failure_types() -> None:
    assert _replacement_cause_v225(stage="ADMISSION", error=KeyError("bug")) == "NONE"
    assert (
        _replacement_cause_v225(stage="COMPARATOR_SELECTION", error=RuntimeError("bug"))
        == "NONE"
    )
    assert (
        _replacement_cause_v225(
            stage="ADMISSION", error=DockerBoundaryError("local Docker unavailable")
        )
        == "LOCAL_ENVIRONMENT"
    )
    assert (
        _replacement_cause_v225(
            stage="COMPARATOR_SELECTION",
            error=NoHealthyComparatorV225("target-complete telemetry unavailable"),
        )
        == "TELEMETRY"
    )


def _failed_admission_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[Path, Path, str, Path, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    private = tmp_path / "private"
    ensure_private_directory(private)
    primary = private / "campaign-0001"
    ensure_private_directory(primary)
    write_private_json(
        private / "primary-campaign-claim.json",
        {
            "schema_version": "dta-v225-real-fault.primary-campaign-claim.v1",
            "campaign_id": "campaign-0001",
            "claimed_at": "2026-08-24T15:39:32.300000+00:00",
        },
        create_once=True,
    )
    write_private_json(
        primary / "blocked-terminal.json",
        {
            "schema_version": "dta-v225-real-fault.blocked-terminal.v1",
            "terminal": "BLOCKED_DTA_V225_REAL_FAULT_ENVIRONMENT",
            "stage": "ADMISSION",
            "baseline_capture_exists": False,
            "fault_capture_exists": False,
            "provider_shadow_exists": False,
            "replacement_cause": "NONE",
            "baseline_restored": False,
            "cleanup": None,
        },
        create_once=True,
    )
    control = primary / "owned-sandbox/control"
    ensure_private_directory(control)
    write_private_json(
        control / "capture-plan.json", {"plan": "frozen"}, create_once=True
    )
    ensure_private_directory(primary / "owned-sandbox/runtime")
    ensure_private_directory(primary / "owned-sandbox/cases")

    failed_head = "7" * 40
    run_id = hashlib.sha256(b"campaign-0001").hexdigest()[:32]
    command_id = "00000000000000000001-deadbeefdeadbeef"
    observer = primary / f"git-audit/observer-visible/{run_id}/commands"
    evaluator = primary / f"git-audit/evaluator-only/{run_id}/commands"
    ensure_private_directory(observer)
    ensure_private_directory(evaluator)
    stdout = f"{failed_head}\n"
    stdout_sha256 = hashlib.sha256(stdout.encode()).hexdigest()
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    command_log = {
        "schema_version": "phase0.command-log.v2",
        "run_id": run_id,
        "command": "git",
        "arguments": ["git", "rev-parse", "HEAD"],
        "working_directory": str(repository),
        "started_at": "2026-08-24T15:39:32.648703+00:00",
        "ended_at": "2026-08-24T15:39:32.658879+00:00",
        "monotonic_started_seconds": 100.0,
        "monotonic_ended_seconds": 100.01,
        "timeout_seconds": 30.0,
        "process_timed_out": False,
        "classification": "SUCCESS",
        "terminal_exit_code": 0,
        "reason_code": "PROCESS_EXIT_ZERO",
        "process_exit_code": 0,
        "stdout_artifact": f"commands/{command_id}.stdout.json",
        "stderr_artifact": f"commands/{command_id}.stderr.json",
        "network_access_declared": False,
        "network_access_scope": "NONE",
        "filesystem_write_scope": [],
        "observed_effect_scope": ["NOT_OBSERVED"],
        "stdout_sha256": stdout_sha256,
        "stderr_sha256": empty_sha256,
    }
    write_private_json(
        observer / f"{command_id}.command-log.json", command_log, create_once=True
    )
    write_private_json(observer / "process-audit.jsonl", command_log, create_once=True)
    write_private_json(
        evaluator / f"{command_id}.stdout.json",
        {
            "schema_version": "phase0.command-stream.v1",
            "stream": "stdout",
            "encoding": "utf-8",
            "content": stdout,
            "content_sha256": stdout_sha256,
        },
        create_once=True,
    )
    write_private_json(
        evaluator / f"{command_id}.stderr.json",
        {
            "schema_version": "phase0.command-stream.v1",
            "stream": "stderr",
            "encoding": "utf-8",
            "content": "",
            "content_sha256": empty_sha256,
        },
        create_once=True,
    )
    provider_env = tmp_path / "provider.env"
    provider_env.write_text("private fixture", encoding="utf-8")
    lease_root = tmp_path / "lease"
    lease_root.mkdir()
    rollout = tmp_path / "rollout.jsonl"
    command = (
        "PYTHONPATH=src uv run python -m "
        "ecomsre.dta_v2.v22.real_fault_cli_v225 "
        f'--repository-root "{repository}" '
        f'--provider-env "{provider_env}" '
        f'--private-root "{private}" '
        f'--lease-root "{lease_root}"'
    )
    call_id = "call_fixture_real_fault"
    missing = repository / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    failure_output = (
        "Traceback (most recent call last):\n"
        f"FileNotFoundError: [Errno 2] No such file or directory: '{missing}'\n"
        "RealFaultLiveSequenceError: BLOCKED_DTA_V225_REAL_FAULT_ENVIRONMENT\n"
    )
    records = (
        {
            "timestamp": "2026-08-24T15:00:00.000Z",
            "ordinal": 1,
            "type": "session_meta",
            "payload": {"id": "01a00000-0000-7000-8000-000000000001"},
        },
        {
            "timestamp": "2026-08-24T15:39:32.247Z",
            "ordinal": 2573,
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": call_id,
                "name": "exec",
                "input": (
                    "const r = await tools.exec_command({"
                    f'cmd:"{command.replace(chr(34), chr(92) + chr(34))}",'
                    f'workdir:"{repository}",tty:false}});'
                ),
            },
        },
        {
            "timestamp": "2026-08-24T15:39:32.703Z",
            "ordinal": 2574,
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CommandExecution",
                    "status": "failed",
                    "exit_code": 1,
                    "cwd": repository.as_uri(),
                    "command": ["/bin/zsh", "-lc", command],
                    "stdout": failure_output,
                    "stderr": "",
                    "aggregated_output": failure_output,
                    "formatted_output": failure_output,
                },
            },
        },
        {
            "timestamp": "2026-08-24T15:39:33.622Z",
            "ordinal": 2575,
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": call_id,
                "output": [
                    {"type": "input_text", "text": "Script completed\n"},
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {"exit_code": 1, "output": failure_output},
                            sort_keys=True,
                        ),
                    },
                ],
            },
        },
    )
    rollout.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    if monkeypatch is not None:
        raw_lines = rollout.read_bytes().splitlines(keepends=True)
        monkeypatch.setattr(
            reconciliation_module,
            "_FROZEN_CODEX_EXECUTION_BINDING",
            reconciliation_module.CodexExecutionBindingV225(
                rollout_path=rollout.resolve(strict=True),
                rollout_session_id="01a00000-0000-7000-8000-000000000001",
                rollout_prefix_sha256=hashlib.sha256(b"".join(raw_lines)).hexdigest(),
                rollout_record_ordinals=(2573, 2574, 2575),
                rollout_record_sha256s=tuple(
                    hashlib.sha256(raw).hexdigest() for raw in raw_lines[1:4]
                ),
                tool_call_id=call_id,
                command_sha256=hashlib.sha256(command.encode()).hexdigest(),
                failure_output_sha256=hashlib.sha256(
                    failure_output.encode()
                ).hexdigest(),
            ),
        )
    return repository, private, failed_head, rollout, provider_env, lease_root


class _ReconciliationGitRunner:
    def __init__(self, failed_head: str) -> None:
        self.failed_head = failed_head

    def run(self, arguments: tuple[str, ...], *, timeout_seconds: float):
        assert timeout_seconds == 30
        if arguments == (
            "git",
            "ls-tree",
            self.failed_head,
            "--",
            "third_party/opentelemetry-demo",
        ):
            return SimpleNamespace(
                exit_code=0,
                stdout=(
                    "160000 commit 1755859a9de82c2e5e225be68abc401a5ebf2b4f"
                    "\tthird_party/opentelemetry-demo\n"
                ),
            )
        assert arguments == (
            "git",
            "show",
            f"{self.failed_head}:src/ecomsre/dta_v2/owned_capture.py",
        )
        return SimpleNamespace(
            exit_code=0,
            stdout="""
write_private_json(control_root / "capture-plan.json", self.plan, create_once=True)
upstream_raw = json.loads(
    (self.repository_root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json")
    .read_text(encoding="utf-8")
)
flag_directory = runtime_root / "flagd"
environment = SandboxEnvironment(repository_root=self.repository_root)
environment.verify_local_docker()
""",
        )


def test_admission_reconciliation_uses_only_the_frozen_codex_anchor() -> None:
    signature = inspect.signature(reconcile_missing_upstream_admission_v225)
    binding = reconciliation_module._FROZEN_CODEX_EXECUTION_BINDING

    assert "codex_rollout_path" not in signature.parameters
    assert binding.rollout_path == (
        Path.home() / ".codex/sessions/2026/08/24/"
        "rollout-2026-08-24T21-15-48-01a033e9-b29d-7602-aead-acc8813ed57a.jsonl"
    )
    assert binding.rollout_session_id == "01a033e9-b29d-7602-aead-acc8813ed57a"
    assert binding.rollout_record_ordinals == (2573, 2574, 2575)
    assert binding.rollout_prefix_sha256 == (
        "970194e9fcf37bb307f45daba516b4b15f267453847efa70ea36b6ed6011ae14"
    )
    assert binding.rollout_record_sha256s == (
        "731854d6bf6ca9201f36a665a7109e633a28d4499e43cddb83a074db993a9e6d",
        "55d3775b5bc3dd873a3385b6a6d53d32f9895d6958e5503ada0798f354718a9e",
        "0a83dbf0c910ab643ff1810836e38ecfd7e1264c2bc3971dd58d2fa262ab929e",
    )


def test_missing_upstream_admission_can_be_reconciled_without_rewriting_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, private, failed_head, rollout, provider_env, lease_root = (
        _failed_admission_fixture(tmp_path, monkeypatch)
    )

    reconciliation = reconcile_missing_upstream_admission_v225(
        repository_root=repository,
        private_root=private,
        git_runner=_ReconciliationGitRunner(failed_head),
        provider_env_path=provider_env,
        lease_root=lease_root,
    )

    assert reconciliation.failed_code_head == failed_head
    assert reconciliation.docker_boundary_reached is False
    assert reconciliation.baseline_restored is True
    assert reconciliation.cleanup.verdict == "CLEAN"
    assert reconciliation.cleanup.non_owned_resources_changed is False
    blocked = json.loads(
        (private / "campaign-0001/blocked-terminal.json").read_text(encoding="utf-8")
    )
    assert blocked["baseline_restored"] is False
    assert blocked["cleanup"] is None
    assert (
        _claim_campaign_v225(private_root=private, replacement=True) == "campaign-0002"
    )


def test_reconciled_replacement_rejects_any_late_result_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, private, failed_head, rollout, provider_env, lease_root = (
        _failed_admission_fixture(tmp_path, monkeypatch)
    )
    reconcile_missing_upstream_admission_v225(
        repository_root=repository,
        private_root=private,
        git_runner=_ReconciliationGitRunner(failed_head),
        provider_env_path=provider_env,
        lease_root=lease_root,
    )
    write_private_json(
        private
        / "campaign-0001/owned-sandbox/runtime/post-terminal-reconciliation.json",
        {"late": "result"},
        create_once=True,
    )

    with pytest.raises(PermissionError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_admission_reconciliation_rejects_tampered_git_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, private, failed_head, rollout, provider_env, lease_root = (
        _failed_admission_fixture(tmp_path, monkeypatch)
    )
    command_log_path = next(
        private.glob(
            "campaign-0001/git-audit/observer-visible/*/commands/*.command-log.json"
        )
    )
    process_audit_path = next(
        private.glob(
            "campaign-0001/git-audit/observer-visible/*/commands/process-audit.jsonl"
        )
    )
    tampered = json.loads(command_log_path.read_text(encoding="utf-8"))
    tampered["run_id"] = "9" * 32
    write_private_json(command_log_path, tampered, create_once=False)
    write_private_json(process_audit_path, tampered, create_once=False)

    with pytest.raises(ValueError, match="git audit differs"):
        reconcile_missing_upstream_admission_v225(
            repository_root=repository,
            private_root=private,
            git_runner=_ReconciliationGitRunner(failed_head),
            provider_env_path=provider_env,
            lease_root=lease_root,
        )


def test_admission_reconciliation_rejects_tampered_stream_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, private, failed_head, rollout, provider_env, lease_root = (
        _failed_admission_fixture(tmp_path, monkeypatch)
    )
    command_log_path = next(
        private.glob(
            "campaign-0001/git-audit/observer-visible/*/commands/*.command-log.json"
        )
    )
    process_audit_path = next(
        private.glob(
            "campaign-0001/git-audit/observer-visible/*/commands/process-audit.jsonl"
        )
    )
    tampered = json.loads(command_log_path.read_text(encoding="utf-8"))
    tampered["stdout_sha256"] = "9" * 64
    write_private_json(command_log_path, tampered, create_once=False)
    write_private_json(process_audit_path, tampered, create_once=False)

    with pytest.raises(ValueError, match="stream binding differs"):
        reconcile_missing_upstream_admission_v225(
            repository_root=repository,
            private_root=private,
            git_runner=_ReconciliationGitRunner(failed_head),
            provider_env_path=provider_env,
            lease_root=lease_root,
        )


def test_reconciled_replacement_rejects_primary_root_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, private, failed_head, rollout, provider_env, lease_root = (
        _failed_admission_fixture(tmp_path, monkeypatch)
    )
    reconcile_missing_upstream_admission_v225(
        repository_root=repository,
        private_root=private,
        git_runner=_ReconciliationGitRunner(failed_head),
        provider_env_path=provider_env,
        lease_root=lease_root,
    )
    primary = private / "campaign-0001"
    moved = private / "campaign-0001-real"
    primary.rename(moved)
    primary.symlink_to(moved, target_is_directory=True)

    with pytest.raises(PermissionError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_admission_reconciliation_rejects_unbound_codex_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, private, failed_head, rollout, provider_env, lease_root = (
        _failed_admission_fixture(tmp_path, monkeypatch)
    )
    rollout.write_text(
        rollout.read_text(encoding="utf-8").replace(
            "demo.flagd.json", "different.flagd.json"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="failure output differs"):
        reconcile_missing_upstream_admission_v225(
            repository_root=repository,
            private_root=private,
            git_runner=_ReconciliationGitRunner(failed_head),
            provider_env_path=provider_env,
            lease_root=lease_root,
        )
