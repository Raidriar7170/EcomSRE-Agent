from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ecomsre_live_sandbox.e2e_v3 as e2e_v3_module

from ecomsre_live_sandbox.contracts import (
    CleanupResult,
    canonical_sha256,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandIdentity,
    DiagnosticFailureCode,
    DiagnosticStage,
)
from ecomsre_live_sandbox.e2e_v3_contracts import (
    E2EV3PrivateRoots,
    load_e2e_v3_config,
)
from ecomsre_live_sandbox.e2e_v3 import (
    NoFaultEvidence,
    _public_result_v3,
    record_human_approval_for_invocation_b,
    run_canonical_invocation_a,
    run_diagnostic_preflight,
    run_invocation_b,
    verify_public_result,
)
from ecomsre_live_sandbox.image_authority import CachedImage, CachedImageInspection


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v3")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_v3_authority_binds_predecessor_v3_a0_and_exact_budgets() -> None:
    config = load_e2e_v3_config(CONFIG)
    authority = config.authority

    assert authority.version == "live-fault-a0-controlled-remediation-e2e-v3"
    assert authority.branch == "feature/live-fault-a0-controlled-remediation-e2e-v3"
    assert authority.predecessor_pr == 33
    assert authority.predecessor_head == "925aa7ae96ca7d46a9496c6319ec465c917b84d3"
    assert authority.predecessor_terminal == (
        "BLOCKED_E2E_V2_DIAGNOSTIC_PREFLIGHT_NOT_PASSED"
    )
    assert authority.predecessor_reason == "IMAGE_LOCK_LIFECYCLE_SCOPE_CONFLICT"
    assert authority.telemetry_authority_pr == 31
    assert authority.telemetry_authority_head == (
        "e28a1091acba7365d7f4deb2aa61fd39e90ae3ae"
    )
    assert authority.telemetry_authority_semantic_sha256 == (
        "ff299ed1ed0f7433702991fecfb1290e3439ed228b90796860c7dfd42cd4917c"
    )
    assert authority.maximum_no_fault_diagnostic_probes == 1
    assert authority.maximum_canonical_invocation_a_runs == 1
    assert authority.maximum_provider_calls == 2
    assert authority.maximum_forward_mutations == 1
    assert authority.maximum_rollbacks == 1
    assert authority.maximum_complete_live_runs == 1
    assert config.sandbox.diagnosis.architecture == "A0"
    assert config.sandbox.diagnosis.decision == "STRONG_SINGLE_HIERARCHICAL"
    assert config.sandbox.environment.upstream_commit == (
        "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    )
    assert config.sandbox.environment.upstream_tag == "3.0.0"


def test_v3_policy_hashes_and_closed_diagnostic_vocabularies_are_exact() -> None:
    config = load_e2e_v3_config(CONFIG)
    authority = config.authority

    assert authority.diagnostics_policy_sha256 == file_sha256(CONFIG / "diagnostics.json")
    assert authority.projection_policy_sha256 == file_sha256(CONFIG / "projection.json")
    assert authority.reporting_policy_sha256 == file_sha256(CONFIG / "reporting.json")
    assert authority.image_authority_policy_sha256 == file_sha256(
        CONFIG / "image-authority.json.schema-or-policy"
    )
    assert config.diagnostics.required_stages == tuple(
        stage.value for stage in DiagnosticStage
    )
    assert config.diagnostics.failure_codes == tuple(
        code.value for code in DiagnosticFailureCode
    )
    assert config.diagnostics.command_identities == tuple(
        identity.value for identity in DiagnosticCommandIdentity
    )
    aggregate = hashlib.sha256(
        json.dumps(
            authority.frozen_input_hashes,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert authority.frozen_input_aggregate_sha256 == aggregate


def test_no_fault_evidence_uses_the_frozen_v3_capture_window() -> None:
    source = inspect.getsource(e2e_v3_module._collect_no_fault_evidence)

    assert "_capture_sli_window" not in source
    assert "v3.readiness.capture_window_seconds" in source
    assert "v3.readiness.ingestion_grace_seconds" in source


def test_fresh_v3_private_root_is_bound_without_disclosing_its_path(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)
    root = tmp_path / "fresh-v3"
    roots = E2EV3PrivateRoots(root)

    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)

    lifecycle = json.loads((roots.control / "private-root-lifecycle.json").read_text())
    assert str(root.resolve()) not in json.dumps(lifecycle)
    assert lifecycle["private_root_absolute_path_sha256"] == hashlib.sha256(
        str(root.resolve()).encode("utf-8")
    ).hexdigest()
    assert lifecycle["predecessor_pr"] == 33
    assert lifecycle["telemetry_authority_pr"] == 31
    assert _mode(root) == 0o700
    assert _mode(roots.control / "private-root-lifecycle.json") == 0o600
    assert all(_mode(path) == 0o700 for path in roots.top_level_directories())


def test_earlier_private_roots_and_preexisting_unbound_content_are_rejected(
    tmp_path: Path,
) -> None:
    config = load_e2e_v3_config(CONFIG)
    v1 = tmp_path / "live-fault-a0-controlled-remediation-e2e-v1"
    v1.mkdir()
    (v1 / "terminal.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="earlier-version private root"):
        E2EV3PrivateRoots(v1).bind_lifecycle(
            config.authority, repository_root=config.repository_root
        )

    unknown = tmp_path / "unknown"
    unknown.mkdir()
    (unknown / "old.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unbound content"):
        E2EV3PrivateRoots(unknown).bind_lifecycle(
            config.authority, repository_root=config.repository_root
        )


def test_private_root_inside_repository_and_symlink_are_rejected(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)

    with pytest.raises(ValueError, match="outside the Git repository"):
        E2EV3PrivateRoots(config.repository_root / ".private-v2").bind_lifecycle(
            config.authority, repository_root=config.repository_root
        )

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        E2EV3PrivateRoots(link).bind_lifecycle(
            config.authority, repository_root=config.repository_root
        )


class FakeController:
    def __init__(self, baseline_sha256: str) -> None:
        self.baseline_sha256 = baseline_sha256
        self.restore_calls = 0

    def read_current(self) -> object:
        return SimpleNamespace(document_sha256=self.baseline_sha256)

    def restore_baseline(self) -> object:
        self.restore_calls += 1
        return self.read_current()


class FakeEnvironment:
    last: "FakeEnvironment | None" = None
    fail_at: str | None = None
    cleanup_verdict = "CLEAN"

    def __init__(self, **kwargs: Any) -> None:
        self.runner = kwargs["runner"]
        self.flagd_directory = kwargs["flagd_directory"]
        self.cleanup_calls = 0
        FakeEnvironment.last = self

    def _fail(self, name: str) -> None:
        if self.fail_at == name:
            raise RuntimeError(f"fake {name} failure")

    def verify_local_docker(self) -> dict[str, str]:
        self._fail("docker")
        return {"context": "desktop-linux", "endpoint": "unix://opaque", "daemon_id": "opaque"}

    def verify_upstream(self) -> None:
        self._fail("upstream")

    def resolve(self) -> tuple[object, dict[str, object]]:
        self._fail("resolve")
        volume = {
            "type": "bind",
            "source": str(self.flagd_directory),
            "target": "/etc/flagd",
            "read_only": True,
        }
        compose = {
            "services": {
                "flagd": {"image": "example.invalid/flagd:3.0.0", "volumes": [volume]},
                "flagd-ui": {
                    "image": "example.invalid/flagd-ui:3.0.0",
                    "volumes": [{**volume, "target": "/app/data"}],
                },
            },
            "networks": {"default": {"name": "ecomsre-live-sandbox-v1-default"}},
        }
        return SimpleNamespace(endpoints=SimpleNamespace(), compose_sha256="a" * 64), compose

    def inspect_cached_images(self, *_: object) -> CachedImageInspection:
        self._fail("images")
        image_id = "sha256:" + "1" * 64
        return CachedImageInspection(
            historical_image_lock_sha256="2" * 64,
            upstream_commit="1755859a9de82c2e5e225be68abc401a5ebf2b4f",
            upstream_tag="3.0.0",
            platform="linux/arm64",
            images=(
                CachedImage(
                    source_reference="example.invalid/flagd:3.0.0",
                    image_id=image_id,
                    image_index_digest="sha256:" + "3" * 64,
                    resolved_platform_digest=image_id,
                    raw_inspect_sha256="4" * 64,
                ),
            ),
        )

    def verify_ports_available(self) -> None:
        self._fail("ports")

    def snapshot_all_resources(self) -> object:
        self._fail("snapshot")
        return SimpleNamespace(containers=frozenset(), networks=frozenset(), volumes=frozenset())

    def start(self) -> None:
        from ecomsre_live_sandbox.e2e_diagnostics import DiagnosticCommandIdentity

        if self.runner.on_start is not None:
            self.runner.on_start(DiagnosticCommandIdentity.COMPOSE_UP)
        if self.runner.on_return is not None:
            self.runner.on_return(DiagnosticCommandIdentity.COMPOSE_UP, 0, False)
        self._fail("start")

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        self._fail("inventory")
        return {
            "container": 25 if require_complete else 0,
            "network": 1 if require_complete else 0,
            "volume": 3 if require_complete else 0,
        }

    def wait_healthy(self, **_: object) -> dict[str, bool]:
        self._fail("health")
        return {f"service-{index}": True for index in range(25)}

    def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
        from ecomsre_live_sandbox.e2e_diagnostics import DiagnosticCommandIdentity

        self.cleanup_calls += 1
        if self.runner.on_start is not None:
            self.runner.on_start(DiagnosticCommandIdentity.COMPOSE_DOWN)
        if self.runner.on_return is not None:
            self.runner.on_return(DiagnosticCommandIdentity.COMPOSE_DOWN, 0, False)
        return CleanupResult(
            baseline_restored=baseline_restored,
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            non_owned_resources_changed=False,
            verdict=self.cleanup_verdict,
        )


def _controller(config: object, *_: object, **__: object) -> FakeController:
    return FakeController(config.sandbox.scenario.baseline_document_sha256)  # type: ignore[attr-defined]


def _evidence(*_: object, **__: object) -> NoFaultEvidence:
    if FakeEnvironment.fail_at == "metrics":
        raise RuntimeError("fake metrics failure")
    return NoFaultEvidence(
        metrics_status="AVAILABLE",
        logs_status="AVAILABLE",
        traces_status="AVAILABLE",
        source_counts={"METRICS": 5, "LOGS": 28, "TRACES": 14},
        invalid_refs=0,
        visible_service_count=4,
        scenario_truth_leaked=False,
        projection_sha256="c" * 64,
    )


def _fake_diagnostic_worktree(config: object, clean_required: bool) -> str:
    assert clean_required is False
    return "d" * 40


def test_diagnostic_probe_passes_without_fault_provider_lock_or_approval(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    FakeEnvironment.fail_at = None
    FakeEnvironment.cleanup_verdict = "CLEAN"

    terminal = run_diagnostic_preflight(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_diagnostic_worktree,
    )

    assert terminal["verdict"] == "LIVE_E2E_V3_DIAGNOSTIC_PREFLIGHT_PASSED"
    assert terminal["fault_injections"] == 0
    assert terminal["provider_calls"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["compose_start_requested"] is True
    assert terminal["compose_start_returned"] is True
    assert terminal["services_healthy"] is True
    assert terminal["baseline_verified"] is True
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert (roots.diagnostics / "probe-01" / "image-verification.json").is_file()
    assert (roots.control / "image-authority.json").is_file()
    assert not (roots.control / "scenario-lock.json").exists()
    assert not (roots.control / "plan-template.json").exists()
    assert not (roots.control / "approval-request.json").exists()
    assert not (roots.control / "human-approval.json").exists()
    assert FakeEnvironment.last is not None and FakeEnvironment.last.cleanup_calls == 1

    with pytest.raises(RuntimeError, match="already passed"):
        run_diagnostic_preflight(
            config,
            roots,
            environment_factory=FakeEnvironment,
            controller_factory=_controller,
            evidence_collector=_evidence,
            sleep=lambda _: None,
            worktree_verifier=_fake_diagnostic_worktree,
        )


@pytest.mark.parametrize(
    ("fail_at", "failed_stage", "failure_code", "cleanup_calls"),
    (
        ("docker", "LOCAL_DOCKER_VERIFIED", "DOCKER_AUTHORITY_UNAVAILABLE", 0),
        ("upstream", "UPSTREAM_PIN_VERIFIED", "UPSTREAM_PIN_DRIFT", 0),
        ("resolve", "COMPOSE_RESOLUTION_STARTED", "COMPOSE_RESOLUTION_FAILED", 0),
        ("images", "IMAGE_AUTHORITY_LOAD_STARTED", "IMAGE_AUTHORITY_MISMATCH", 0),
        ("ports", "PORT_PREFLIGHT_STARTED", "PORT_CONFLICT", 0),
        ("snapshot", "DOCKER_BASELINE_SNAPSHOT_CAPTURED", "DOCKER_BASELINE_SNAPSHOT_FAILED", 0),
        ("start", "COMPOSE_START_RETURNED", "COMPOSE_UP_FAILED", 1),
        ("inventory", "OWNED_RESOURCE_INVENTORY_VERIFIED", "OWNED_RESOURCE_INVENTORY_INCOMPLETE", 1),
        ("health", "SERVICE_HEALTH_WAIT_STARTED", "SERVICE_HEALTH_TIMEOUT", 1),
        ("metrics", "METRICS_PREFLIGHT_STARTED", "METRICS_PREFLIGHT_FAILED", 1),
    ),
)
def test_diagnostic_failure_maps_to_stable_stage_and_preserves_cleanup(
    tmp_path: Path,
    fail_at: str,
    failed_stage: str,
    failure_code: str,
    cleanup_calls: int,
) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / fail_at)
    FakeEnvironment.fail_at = fail_at
    FakeEnvironment.cleanup_verdict = "CLEAN"

    terminal = run_diagnostic_preflight(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_diagnostic_worktree,
    )

    expected_verdict = (
        "BLOCKED_E2E_V3_IMAGE_AUTHORITY_MISMATCH"
        if fail_at == "images"
        else "BLOCKED_E2E_V3_DIAGNOSTIC_PREFLIGHT_NOT_PASSED"
    )
    assert terminal["verdict"] == expected_verdict
    assert terminal["failed_stage"] == failed_stage
    assert terminal["failure_code"] == failure_code
    assert terminal["cleanup_verdict"] in {"CLEAN", "NOT_REQUIRED"}
    assert FakeEnvironment.last is not None and FakeEnvironment.last.cleanup_calls == cleanup_calls


def test_cleanup_failure_does_not_overwrite_the_root_failure(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    FakeEnvironment.fail_at = "health"
    FakeEnvironment.cleanup_verdict = "BLOCKED"

    terminal = run_diagnostic_preflight(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_diagnostic_worktree,
    )

    assert terminal["failed_stage"] == "SERVICE_HEALTH_WAIT_STARTED"
    assert terminal["failure_code"] == "SERVICE_HEALTH_TIMEOUT"
    assert terminal["cleanup_verdict"] == "BLOCKED"
    assert terminal["cleanup_failure_code"] == "CLEANUP_FAILED"


def _fake_worktree(config: object, clean_required: bool) -> str:
    assert clean_required is True
    return "d" * 40


def _write_canonical_admission(roots: E2EV3PrivateRoots) -> None:
    write_private_json(
        roots.control / "exact-head-ci.json",
        {
            "schema_version": "live-e2e.exact-head-ci.v3",
            "implementation_commit": "d" * 40,
            "workflows": {
                "Agent mainline": {"run_id": 101, "conclusion": "SUCCESS"},
                "RCAEval RE2 v2 development": {"run_id": 102, "conclusion": "SUCCESS"},
            },
        },
        create_once=True,
    )
    write_private_json(
        roots.control / "pre-live-review.json",
        {
            "schema_version": "live-e2e.pre-live-review.v3",
            "implementation_commit": "d" * 40,
            "verdict": "PRE_LIVE_PASS",
            "must_fix_count": 0,
        },
        create_once=True,
    )


def _pass_diagnostic(config: object, roots: E2EV3PrivateRoots) -> None:
    FakeEnvironment.fail_at = None
    FakeEnvironment.cleanup_verdict = "CLEAN"
    terminal = run_diagnostic_preflight(
        config,  # type: ignore[arg-type]
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_diagnostic_worktree,
    )
    assert terminal["verdict"] == "LIVE_E2E_V3_DIAGNOSTIC_PREFLIGHT_PASSED"


def test_canonical_requires_diagnostic_pass_and_exact_head_ci(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)

    with pytest.raises(RuntimeError, match="diagnostic PASS"):
        run_canonical_invocation_a(
            config,
            roots,
            environment_factory=FakeEnvironment,
            controller_factory=_controller,
            evidence_collector=_evidence,
            sleep=lambda _: None,
            worktree_verifier=_fake_worktree,
        )

    _pass_diagnostic(config, roots)
    with pytest.raises(RuntimeError, match="exact-head CI marker"):
        run_canonical_invocation_a(
            config,
            roots,
            environment_factory=FakeEnvironment,
            controller_factory=_controller,
            evidence_collector=_evidence,
            sleep=lambda _: None,
            worktree_verifier=_fake_worktree,
        )


def test_canonical_creates_lock_plan_and_request_only_after_clean_cleanup(
    tmp_path: Path,
) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    _pass_diagnostic(config, roots)
    _write_canonical_admission(roots)
    FakeEnvironment.fail_at = None
    FakeEnvironment.cleanup_verdict = "CLEAN"

    terminal = run_canonical_invocation_a(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )

    assert terminal["verdict"] == "LIVE_E2E_V3_HUMAN_PREAUTHORIZATION_REQUIRED"
    assert terminal["run_count"] == 1
    assert terminal["scenario_lock_created"] is True
    assert terminal["plan_template_created"] is True
    assert terminal["approval_request_created"] is True
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["fault_injections"] == 0
    assert terminal["provider_calls"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["codex_self_approved"] is False
    assert (roots.control / "scenario-lock.json").is_file()
    assert (roots.control / "plan-template.json").is_file()
    assert (roots.control / "approval-request.json").is_file()
    assert (roots.invocation_a / "image-verification.json").is_file()
    diagnostic_verification = json.loads(
        (roots.diagnostics / "probe-01" / "image-verification.json").read_text()
    )
    canonical_verification = json.loads(
        (roots.invocation_a / "image-verification.json").read_text()
    )
    assert diagnostic_verification["compose_instance_sha256"] != (
        canonical_verification["compose_instance_sha256"]
    )
    assert diagnostic_verification["compose_structure_sha256"] == (
        canonical_verification["compose_structure_sha256"]
    )
    assert not (roots.control / "human-approval.json").exists()

    with pytest.raises(RuntimeError, match="already consumed"):
        run_canonical_invocation_a(
            config,
            roots,
            environment_factory=FakeEnvironment,
            controller_factory=_controller,
            evidence_collector=_evidence,
            sleep=lambda _: None,
            worktree_verifier=_fake_worktree,
        )


def test_canonical_failure_never_creates_approval_request(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    _pass_diagnostic(config, roots)
    _write_canonical_admission(roots)
    FakeEnvironment.fail_at = "health"
    FakeEnvironment.cleanup_verdict = "CLEAN"

    terminal = run_canonical_invocation_a(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )

    assert terminal["verdict"] == "BLOCKED_E2E_V3_SERVICE_HEALTH_TIMEOUT"
    assert terminal["failed_stage"] == "SERVICE_HEALTH_WAIT_STARTED"
    assert terminal["approval_request_created"] is False
    assert not (roots.control / "scenario-lock.json").exists()
    assert not (roots.control / "approval-request.json").exists()


def test_human_approval_cannot_be_recorded_before_canonical_success(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)

    with pytest.raises(RuntimeError, match="canonical Invocation A"):
        record_human_approval_for_invocation_b(
            config,
            roots,
            approver="Human Reviewer",
            phrase="APPROVE invalid invalid",
        )


def _canonical_success(config: object, roots: E2EV3PrivateRoots) -> dict[str, object]:
    _pass_diagnostic(config, roots)
    _write_canonical_admission(roots)
    FakeEnvironment.fail_at = None
    FakeEnvironment.cleanup_verdict = "CLEAN"
    return run_canonical_invocation_a(
        config,  # type: ignore[arg-type]
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )


def test_invocation_b_rejects_missing_human_record_before_provider(tmp_path: Path) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    terminal = _canonical_success(config, roots)
    assert terminal["verdict"] == config.authority.invocation_a_terminal
    provider_factory_calls = 0

    def forbidden_provider(_: object) -> object:
        nonlocal provider_factory_calls
        provider_factory_calls += 1
        raise AssertionError("Provider must remain unreachable without human approval")

    with pytest.raises(RuntimeError, match="HumanApprovalRecord"):
        run_invocation_b(
            config,
            roots,
            provider_factory=forbidden_provider,
            environment_factory=FakeEnvironment,
            worktree_verifier=_fake_worktree,
            sleep=lambda _: None,
        )

    assert provider_factory_calls == 0
    assert not (roots.invocation_b / "started.json").exists()


def test_provider_preflight_failure_is_terminal_before_fault_or_compose_start(
    tmp_path: Path,
) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    canonical = _canonical_success(config, roots)
    request = json.loads((roots.control / "approval-request.json").read_text())
    phrase = f"APPROVE {request['scenario_id']} {request['plan_template_sha256']}"
    record = record_human_approval_for_invocation_b(
        config,
        roots,
        approver="Human Reviewer",
        phrase=phrase,
    )
    assert record.mode == "HUMAN"
    FakeEnvironment.fail_at = None
    FakeEnvironment.cleanup_verdict = "CLEAN"

    def failing_provider(_: object) -> object:
        raise RuntimeError("synthetic provider unavailable")

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=failing_provider,
        environment_factory=FakeEnvironment,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    assert canonical["implementation_commit"] == "d" * 40
    assert terminal["verdict"] == "BLOCKED_PROVIDER_PREFLIGHT"
    assert terminal["provider_calls"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["compose_start_requested"] is False
    assert terminal["cleanup_verdict"] == "NOT_REQUIRED"
    assert not (roots.invocation_b / "image-verification.json").exists()
    assert FakeEnvironment.last is not None and FakeEnvironment.last.cleanup_calls == 0


def test_invocation_b_writes_its_own_image_verification_after_provider_preflight(
    tmp_path: Path,
) -> None:
    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / "private")
    _canonical_success(config, roots)
    request = json.loads((roots.control / "approval-request.json").read_text())
    record_human_approval_for_invocation_b(
        config,
        roots,
        approver="Human Reviewer",
        phrase=f"APPROVE {request['scenario_id']} {request['plan_template_sha256']}",
    )

    class Provider:
        calls = 0
        usage_known = True
        last_usage_tokens = 1
        last_request_sha256 = "0" * 64

        def diagnose(self, _: object) -> dict[str, str]:
            self.calls += 1
            return {"preflight": "only"}

    FakeEnvironment.fail_at = "ports"
    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: Provider(),
        environment_factory=FakeEnvironment,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    diagnostic = json.loads(
        (roots.diagnostics / "probe-01" / "image-verification.json").read_text()
    )
    canonical = json.loads(
        (roots.invocation_a / "image-verification.json").read_text()
    )
    live = json.loads((roots.invocation_b / "image-verification.json").read_text())
    assert terminal["provider_calls"] == 1
    assert terminal["failed_stage"] == "FAULT_CONTROLLER_PREPARATION_STARTED"
    assert terminal["failure_code"] == "FAULT_CONTROLLER_PREPARATION_FAILED"
    assert len({diagnostic["verification_sha256"], canonical["verification_sha256"], live["verification_sha256"]}) == 3
    assert len({diagnostic["compose_instance_sha256"], canonical["compose_instance_sha256"], live["compose_instance_sha256"]}) == 3
    assert len({diagnostic["compose_structure_sha256"], canonical["compose_structure_sha256"], live["compose_structure_sha256"]}) == 1


@pytest.mark.parametrize(
    ("failure_kind", "failed_stage", "failure_code"),
    (
        (
            "controller",
            "FAULT_CONTROLLER_PREPARATION_STARTED",
            "FAULT_CONTROLLER_PREPARATION_FAILED",
        ),
        (
            "baseline-read",
            "BASELINE_CONFIGURATION_READ_STARTED",
            "BASELINE_CONFIGURATION_UNAVAILABLE",
        ),
        (
            "baseline-mismatch",
            "BASELINE_CONFIGURATION_VERIFIED",
            "BASELINE_CONFIGURATION_MISMATCH",
        ),
        ("logs", "LOGS_PREFLIGHT_STARTED", "LOGS_PREFLIGHT_FAILED"),
        ("traces", "TRACES_PREFLIGHT_STARTED", "TRACES_PREFLIGHT_FAILED"),
        (
            "projection",
            "MULTISERVICE_PROJECTION_STARTED",
            "MULTISERVICE_PROJECTION_FAILED",
        ),
    ),
)
def test_additional_major_stages_retain_specific_failure_codes(
    tmp_path: Path,
    failure_kind: str,
    failed_stage: str,
    failure_code: str,
) -> None:
    from ecomsre_live_sandbox.e2e_diagnostics import (
        DiagnosticFailureCode,
        DiagnosticStage,
    )

    config = load_e2e_v3_config(CONFIG)
    roots = E2EV3PrivateRoots(tmp_path / failure_kind)
    FakeEnvironment.fail_at = None
    FakeEnvironment.cleanup_verdict = "CLEAN"

    def controller_factory(config: object, *_: object, **__: object) -> FakeController:
        if failure_kind == "controller":
            raise RuntimeError("controller preparation failed")
        controller = _controller(config)
        if failure_kind == "baseline-read":
            original_read = controller.read_current
            reads = 0

            def fail_once() -> object:
                nonlocal reads
                reads += 1
                if reads == 1:
                    raise RuntimeError("baseline unavailable")
                return original_read()

            controller.read_current = fail_once  # type: ignore[method-assign]
        elif failure_kind == "baseline-mismatch":
            controller.baseline_sha256 = "f" * 64
        return controller

    def evidence_collector(
        _config: object,
        _roots: object,
        _run_root: object,
        tracker: object,
        *_: object,
    ) -> NoFaultEvidence:
        if failure_kind in {"logs", "traces", "projection"}:
            stage = {
                "logs": DiagnosticStage.LOGS_PREFLIGHT_STARTED,
                "traces": DiagnosticStage.TRACES_PREFLIGHT_STARTED,
                "projection": DiagnosticStage.MULTISERVICE_PROJECTION_STARTED,
            }[failure_kind]
            code = {
                "logs": DiagnosticFailureCode.LOGS_PREFLIGHT_FAILED,
                "traces": DiagnosticFailureCode.TRACES_PREFLIGHT_FAILED,
                "projection": DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED,
            }[failure_kind]
            error = RuntimeError(f"{failure_kind} unavailable")
            tracker.fail_external(error, stage=stage, failure_code=code)  # type: ignore[attr-defined]
            raise error
        return _evidence()

    terminal = run_diagnostic_preflight(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=controller_factory,
        evidence_collector=evidence_collector,
        sleep=lambda _: None,
        worktree_verifier=_fake_diagnostic_worktree,
    )

    assert terminal["failed_stage"] == failed_stage
    assert terminal["failure_code"] == failure_code
    assert terminal["cleanup_verdict"] == (
        "NOT_REQUIRED" if failure_kind == "controller" else "CLEAN"
    )


def test_v3_runtime_never_invokes_the_consumed_telemetry_canonical_preflight() -> None:
    source = Path("src/ecomsre_live_sandbox/e2e_v3.py").read_text(encoding="utf-8")

    assert "run_canonical_preflight" not in source
    assert "run_canonical_instrumentation" not in source


def test_public_verifier_rejects_forged_success_aggregates() -> None:
    config = load_e2e_v3_config(CONFIG)
    terminal = {
        "verdict": config.authority.invocation_b_success,
        "implementation_commit": "d" * 40,
        "source_availability": {
            "METRICS": "AVAILABLE",
            "LOGS": "AVAILABLE",
            "TRACES": "AVAILABLE",
        },
        "source_counts": {"METRICS": 5, "LOGS": 28, "TRACES": 14},
        "invalid_refs": 0,
        "visible_service_count": 4,
        "fault_injections": 1,
        "provider_calls": 2,
        "model_calls": 1,
        "forward_mutations": 1,
        "rollback_mutations": 0,
        "fault_impact_passed": True,
        "diagnosis_gate": True,
        "diagnosis_correct": True,
        "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        "policy_verdict": "ALLOW",
        "recovery_verification_passed": True,
        "cleanup": {
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        },
    }
    public = _public_result_v3(config, terminal)
    verify_public_result(config, public)

    public["forward_mutations"] = 0
    core = dict(public)
    core.pop("semantic_sha256")
    public["semantic_sha256"] = canonical_sha256(core)
    with pytest.raises(ValueError, match="success aggregates"):
        verify_public_result(config, public)
