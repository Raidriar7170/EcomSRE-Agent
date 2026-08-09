from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

import pytest
import ecomsre_rcaeval_v2.dev3_evaluation_root as evaluation_root

from ecomsre_rcaeval_v2.dev3_admission import (
    ScheduleAdmissionLock,
    write_admission_lock,
)
from ecomsre_rcaeval_v2.dev3_audit import (
    Dev2FailureAuditLock,
    audit_tree_sha256,
    write_audit_lock_create_once,
)
from ecomsre_rcaeval_v2.dev3_evaluation_root import (
    EVALUATION_LOCK_NAME,
    prepare_evaluation_root,
    verify_evaluation_root,
    verify_provider_ready,
)
from ecomsre_rcaeval_v2.dev3_paths import tree_sha256
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev2FailureEvidence,
    audit_dev2_failures,
)


CONFIG_NAMES = (
    "protocol.json",
    "dataset-lock.json",
    "split-lock.json",
    "model-prompt-lock.json",
    "budget-lock.json",
    "indicator-lock.json",
    "schedule-generation.json",
    "evaluation-policy.json",
    "transport-retry-policy.json",
)
SCHEDULE_NAMES = (
    "smoke-schedule.json",
    "design-schedule.json",
    "dev-validation-schedule.json",
    "schedule-set-lock.json",
)


@pytest.fixture(autouse=True)
def _authorize_fixture_draft_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        evaluation_root,
        "_remote_authorization",
        lambda _repo, _commit: {
            "draft_pr_number": 99,
            "draft_pr_url": "https://github.invalid/example/pull/99",
            "required_ci_checks": (
                {
                    "workflow": "Agent mainline",
                    "name": "test",
                    "state": "SUCCESS",
                    "bucket": "pass",
                    "link": "https://github.invalid/check/1",
                },
                {
                    "workflow": "RCAEval RE2 v2 development",
                    "name": "test",
                    "state": "SUCCESS",
                    "bucket": "pass",
                    "link": "https://github.invalid/check/2",
                },
            ),
        },
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_private_schedule_root_must_be_disjoint_from_control_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="pairwise disjoint"):
        evaluation_root._validate_roots(
            tmp_path / "repo",
            tmp_path / "control",
            tmp_path / "control/schedules",
            tmp_path / "output",
            tmp_path / "smoke",
            tmp_path / "design",
        )


def test_implementation_diff_whitelist_rejects_old_protocol_and_dev4_paths() -> None:
    assert evaluation_root._is_dev3_implementation_path(
        "src/ecomsre_rcaeval_v2/dev3_provider.py"
    )
    assert not evaluation_root._is_dev3_implementation_path(
        "config/rcaeval-re2-v2-dev2/protocol.json"
    )
    assert not evaluation_root._is_dev3_implementation_path(
        "src/ecomsre_rcaeval_v2/dev4_provider.py"
    )


def test_prepare_rejects_preserved_root_nesting_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, control, _schedule, output, smoke, design, source_base = _fixture(tmp_path)
    old_v1 = tmp_path / "old-dev-v1"
    private_schedule = old_v1 / "nested-private-schedules"
    private_schedule.mkdir(parents=True)
    for name in SCHEDULE_NAMES:
        (private_schedule / name).write_text(
            json.dumps({"name": name}) + "\n", encoding="utf-8"
        )
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    with pytest.raises(ValueError, match="pairwise disjoint"):
        prepare_evaluation_root(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            source_base_commit=source_base,
            preserved_roots={
                "v2_dev_v1": old_v1,
                "v2_dev1_control": tmp_path / "old-dev1-control",
                "v2_dev1_output": tmp_path / "old-dev1-output",
                "v2_dev2_control": tmp_path / "old-dev2-control",
                "v2_dev2_schedule": tmp_path / "old-dev2-schedule",
                "v2_dev2_output": tmp_path / "old-dev2-output",
                "v2_dev2_smoke": tmp_path / "old-dev2-smoke",
                "v2_dev2_design": tmp_path / "old-dev2-design",
            },
        )
    assert not (control / "locks" / EVALUATION_LOCK_NAME).exists()
    assert not (private_schedule / ".evaluation-root-authority.json").exists()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path, str]:
    repo = tmp_path / "repo"
    control = tmp_path / "control"
    private_schedule = tmp_path / "private-schedules"
    output = tmp_path / "output"
    smoke = tmp_path / "smoke-journal"
    design = tmp_path / "design-journal"
    config = repo / "config" / "rcaeval-re2-v2-dev3"
    for directory in (
        config,
        repo / "config/rcaeval-re2-v1",
        repo / "src/ecomsre_rcaeval_v2",
        repo / "scripts/rcaeval_v2",
        repo / "tests/benchmarks/rcaeval_v2",
        repo / ".github/workflows",
        private_schedule,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (repo / "src/ecomsre_rcaeval_v2/runtime.py").write_text("DEV3 = True\n", encoding="utf-8")
    (repo / "scripts/rcaeval_v2/run.py").write_text("# dev3\n", encoding="utf-8")
    (repo / "tests/benchmarks/rcaeval_v2/test_dev3.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (repo / ".github/workflows/rcaeval-v2-dev.yml").write_text("name: dev3\n", encoding="utf-8")
    for name in CONFIG_NAMES:
        payload = {
            "name": name,
            **({"source_base_commit": "PENDING"} if name == "protocol.json" else {}),
        }
        (config / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    (repo / "config/rcaeval-re2-v1/schedule-generation.json").write_text(
        json.dumps({"expected_schedule_sha256": "1" * 64}) + "\n",
        encoding="utf-8",
    )
    for name in SCHEDULE_NAMES:
        (private_schedule / name).write_text(json.dumps({"name": name}) + "\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "add", "--", ".github", "config", "scripts", "src", "tests")
    _git(repo, "commit", "-m", "base fixture")
    source_base = _git(repo, "rev-parse", "HEAD")
    (config / "protocol.json").write_text(
        json.dumps({"name": "protocol.json", "source_base_commit": source_base})
        + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", "--", str(config / "protocol.json"))
    _git(repo, "commit", "-m", "implementation fixture")
    return repo, control, private_schedule, output, smoke, design, source_base


def _preserved_roots(parent: Path) -> dict[str, Path]:
    return {
        "v2_dev_v1": parent / "old-dev-v1",
        "v2_dev1_control": parent / "old-dev1-control",
        "v2_dev1_output": parent / "old-dev1-output",
        "v2_dev2_control": parent / "old-dev2-control",
        "v2_dev2_schedule": parent / "old-dev2-schedule",
        "v2_dev2_output": parent / "old-dev2-output",
        "v2_dev2_smoke": parent / "old-dev2-smoke",
        "v2_dev2_design": parent / "old-dev2-design",
    }


def _admission(
    evaluation, parent: Path, control: Path
) -> ScheduleAdmissionLock:
    roots = _preserved_roots(parent)
    old_v1 = roots["v2_dev_v1"]
    old_dev1_control = roots["v2_dev1_control"]
    old_dev1_output = roots["v2_dev1_output"]
    old_dev2_control = roots["v2_dev2_control"]
    old_dev2_schedule = roots["v2_dev2_schedule"]
    old_dev2_smoke = roots["v2_dev2_smoke"]
    old_dev2_design = roots["v2_dev2_design"]
    (old_v1 / "runs").mkdir(parents=True, exist_ok=True)
    (old_v1 / "schedule").mkdir(parents=True, exist_ok=True)
    (old_dev1_control / "schedules").mkdir(parents=True, exist_ok=True)
    old_dev1_output.mkdir(parents=True, exist_ok=True)
    (old_dev2_control / "evidence").mkdir(parents=True, exist_ok=True)
    old_dev2_schedule.mkdir(parents=True, exist_ok=True)
    roots["v2_dev2_output"].mkdir(parents=True, exist_ok=True)
    old_dev2_smoke.mkdir(parents=True, exist_ok=True)
    old_dev2_design.mkdir(parents=True, exist_ok=True)
    (old_v1 / "runs/terminal.json").write_text("preserved-v1\n", encoding="utf-8")
    (old_dev1_output / "terminal.json").write_text(
        "preserved-dev1\n", encoding="utf-8"
    )
    schedule_paths = {
        "v2_dev_v1_design": old_v1 / "schedule/design-schedule.json",
        "v2_dev_v1_validation": old_v1
        / "schedule/dev-validation-schedule.json",
        "v2_dev1_design": old_dev1_control / "schedules/design-schedule.json",
        "v2_dev1_validation": old_dev1_control
        / "schedules/dev-validation-schedule.json",
        "v2_dev2_design": old_dev2_schedule / "design-schedule.json",
        "v2_dev2_validation": old_dev2_schedule / "dev-validation-schedule.json",
    }
    for name, path in schedule_paths.items():
        path.write_text(f"{name}\n", encoding="utf-8")
    dev2_smoke_schedule = old_dev2_schedule / "smoke-schedule.json"
    dev2_smoke_schedule.write_text("dev2-smoke-schedule\n", encoding="utf-8")
    dev2_gate = old_dev2_control / "evidence/provider-smoke-gate.json"
    dev2_gate.write_text("dev2-smoke-gate\n", encoding="utf-8")
    (old_dev2_smoke / "terminal.json").write_text(
        "preserved-dev2-smoke\n", encoding="utf-8"
    )
    (old_dev2_design / "authority.json").write_text(
        "preserved-dev2-design\n", encoding="utf-8"
    )
    failure = Dev2FailureEvidence(
        architecture_family="V2",
        variant="fixed_v2_dev2",
        operation_type="METRICS_SPECIALIST",
        operation_stage="PROVIDER_CALL",
        failure_code="PROVIDER_TRANSPORT_FAILURE",
        safe_http_status_class=None,
        provider_attempt_index=1,
        provider_call_index=1,
        latency_bucket="<10s",
        valid_response_received=False,
        usage_object_received=False,
        token_usage_known=False,
        timestamp_bucket="UNKNOWN",
        canonical_request_sha256=None,
    )
    audit = Dev2FailureAuditLock(
        schema_version="rcaeval-re2-v2-dev3.failure-audit-lock.v1",
        protocol_id="rcaeval-re2-v2-dev.3",
        audited_at_utc=datetime(2026, 8, 10, tzinfo=timezone.utc),
        evaluation_root_lock_sha256=hashlib.sha256(
            (control / "locks/evaluation-root-lock.json").read_bytes()
        ).hexdigest(),
        dev2_smoke_gate_sha256=hashlib.sha256(dev2_gate.read_bytes()).hexdigest(),
        dev2_smoke_schedule_sha256=hashlib.sha256(
            dev2_smoke_schedule.read_bytes()
        ).hexdigest(),
        dev2_smoke_journal_tree_sha256=audit_tree_sha256(old_dev2_smoke),
        audit=audit_dev2_failures((failure,) * 5),
    )
    audit_path = control / "locks/dev2-provider-failure-audit.json"
    write_audit_lock_create_once(audit_path, audit)
    return ScheduleAdmissionLock.model_validate(
        {
            "schema_version": "rcaeval-re2-v2-dev3.schedule-admission-lock.v1",
            "protocol_id": "rcaeval-re2-v2-dev.3",
            "implementation_commit": evaluation.implementation_commit,
            "split_lock_sha256": evaluation.split_lock_sha256,
            "dev2_failure_audit_lock_sha256": hashlib.sha256(
                audit_path.read_bytes()
            ).hexdigest(),
            "retry_policy_lock_sha256": evaluation.retry_policy_lock_sha256,
            "smoke_schedule_sha256": evaluation.smoke_schedule_sha256,
            "design_schedule_sha256": evaluation.design_schedule_sha256,
            "validation_schedule_sha256": evaluation.validation_schedule_sha256,
            "schedule_set_sha256": evaluation.schedule_set_sha256,
            "v1_external_schedule_sha256": "1" * 64,
            "private_schedule_root_identity_sha256": evaluation.private_schedule_root_identity_sha256,
            "private_output_root_identity_sha256": evaluation.private_output_root_identity_sha256,
            "smoke_journal_root_identity_sha256": evaluation.smoke_journal_root_identity_sha256,
            "design_journal_root_identity_sha256": evaluation.design_journal_root_identity_sha256,
            "preserved_schedule_hashes": {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in schedule_paths.items()
            },
            "preserved_root_identity_sha256": {
                name: hashlib.sha256(str(path.resolve()).encode()).hexdigest()
                for name, path in roots.items()
            },
            "preserved_evidence_hashes": {
                f"{name}_tree": tree_sha256(path) for name, path in roots.items()
            },
            "smoke": {"admitted": 72, "rejected": 0},
            "design": {"admitted": 360, "rejected": 0},
            "dev_validation_metadata": {"admitted": 480, "rejected": 0, "values_accessed": False},
            "v1_contract_construction": {"admitted": 216, "call_position_min": 1, "call_position_max": 3},
            "v2_contract_construction": {"admitted": 216, "family_position_min": 1, "family_position_max": 3},
            "run_id_checks": {"all": True},
            "old_new_overlap_checks": {"overlap_count": 0},
            "budget_checks": {"within_locked_caps": True},
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "run_attempts_created": 0,
            "operation_attempts_created": 0,
            "provider_attempts_created": 0,
            "verdict": "V2_DEV3_ADMISSION_REHEARSAL_PASSED",
        }
    )


def test_provider_ready_requires_evaluation_then_matching_admission_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    evaluation = prepare_evaluation_root(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        source_base_commit=source_base,
        preserved_roots=_preserved_roots(tmp_path),
    )
    assert evaluation == verify_evaluation_root(
        control, private_schedule, output, smoke, design, project_root=repo
    )
    with pytest.raises(ValueError, match="failure audit"):
        verify_provider_ready(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            preserved_roots=_preserved_roots(tmp_path),
        )
    admission = _admission(evaluation, tmp_path, control)
    write_admission_lock(control / "locks/schedule-admission-lock.json", admission)
    verified_evaluation, verified_admission = verify_provider_ready(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        preserved_roots=_preserved_roots(tmp_path),
    )
    assert verified_evaluation == evaluation
    assert verified_admission == admission
    lock_text = (control / "locks" / EVALUATION_LOCK_NAME).read_text(encoding="utf-8")
    assert str(output.resolve()) not in lock_text
    assert str(private_schedule.resolve()) not in lock_text
    assert evaluation.private_schedule_root_identity_sha256 == hashlib.sha256(
        str(private_schedule.resolve()).encode()
    ).hexdigest()
    assert evaluation.private_output_root_identity_sha256 == hashlib.sha256(
        str(output.resolve()).encode()
    ).hexdigest()


def test_provider_ready_fails_closed_on_schedule_or_source_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    evaluation = prepare_evaluation_root(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        source_base_commit=source_base,
        preserved_roots=_preserved_roots(tmp_path),
    )
    write_admission_lock(
        control / "locks/schedule-admission-lock.json",
        _admission(evaluation, tmp_path, control),
    )
    (private_schedule / "smoke-schedule.json").write_text('{"drift":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="drift"):
        verify_provider_ready(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            preserved_roots=_preserved_roots(tmp_path),
        )


def test_provider_ready_fails_closed_on_preserved_terminal_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    evaluation = prepare_evaluation_root(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        source_base_commit=source_base,
        preserved_roots=_preserved_roots(tmp_path),
    )
    admission = _admission(evaluation, tmp_path, control)
    write_admission_lock(
        control / "locks/schedule-admission-lock.json", admission
    )
    (tmp_path / "old-dev1-output/terminal.json").write_text(
        "mutated\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="preserved terminal evidence drift"):
        verify_provider_ready(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            preserved_roots=_preserved_roots(tmp_path),
        )


def test_evaluation_root_fails_closed_on_dirty_tree_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    (repo / "src/ecomsre_rcaeval_v2/runtime.py").write_text("dirty = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        prepare_evaluation_root(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            source_base_commit=source_base,
            preserved_roots=_preserved_roots(tmp_path),
        )
    assert not (control / "locks" / EVALUATION_LOCK_NAME).exists()
    assert not output.exists()
