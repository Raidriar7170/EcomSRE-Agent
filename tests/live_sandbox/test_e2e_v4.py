from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ecomsre_live_sandbox.e2e_v4 as e2e_v4_module

from ecomsre_live_sandbox.contracts import CleanupResult, write_private_json
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandIdentity,
    V4_DIAGNOSTIC_FAILURE_CODES,
    V4_DIAGNOSTIC_STAGES,
)
from ecomsre_live_sandbox.e2e_v4 import (
    _complete_development_budget,
    _canonical_failure_verdict,
    _consume_development_budget,
    _require_development_pass,
    NoFaultEvidence,
    record_human_approval_for_invocation_b,
    run_canonical_invocation_a,
    run_development_probe,
    run_invocation_b,
)
from ecomsre_live_sandbox.e2e_v4_contracts import (
    E2EV4PrivateRoots,
    load_e2e_v4_config,
)
from ecomsre_live_sandbox.image_authority import CachedImage, CachedImageInspection


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v4")


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_v4_authority_binds_exact_successor_and_frozen_boundaries() -> None:
    config = load_e2e_v4_config(CONFIG)
    authority = config.authority

    assert authority.version == "live-fault-a0-controlled-remediation-e2e-v4"
    assert authority.branch == "feature/live-fault-a0-controlled-remediation-e2e-v4"
    assert authority.predecessor_pr == 34
    assert authority.predecessor_head == "39b3f7be00cc4a50d606cd5b96198c24cdd69a07"
    assert authority.telemetry_authority_pr == 31
    assert authority.telemetry_authority_head == (
        "e28a1091acba7365d7f4deb2aa61fd39e90ae3ae"
    )
    assert authority.maximum_development_integration_probes == 3
    assert authority.maximum_canonical_invocation_a_runs == 1
    assert authority.maximum_complete_live_runs == 1
    assert config.sandbox.diagnosis.architecture == "A0"
    assert config.sandbox.diagnosis.decision == "STRONG_SINGLE_HIERARCHICAL"
    assert config.sandbox.environment.upstream_commit == (
        "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    )
    assert config.sandbox.environment.upstream_tag == "3.0.0"
    assert config.diagnostics.required_stages == tuple(
        stage.value for stage in V4_DIAGNOSTIC_STAGES
    )
    assert config.diagnostics.failure_codes == tuple(
        code.value for code in V4_DIAGNOSTIC_FAILURE_CODES
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


def test_v4_private_root_is_fresh_private_and_run_scoped(tmp_path: Path) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")

    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)

    assert _mode(roots.root) == 0o700
    assert _mode(roots.control / "private-root-lifecycle.json") == 0o600
    assert roots.probe_root(1) != roots.probe_root(2)
    assert roots.probe_root(2) != roots.probe_root(3)
    assert not (roots.control / "scenario-lock.json").exists()
    assert not (roots.control / "approval-request.json").exists()


def test_runtime_pass_lock_scope_includes_a0_provider_and_excludes_tests_docs() -> None:
    config = load_e2e_v4_config(CONFIG)
    paths = set(e2e_v4_module._runtime_config_paths(config).values())

    assert Path("src/ecomsre_rca100/prompt.py") in paths
    assert Path("src/ecomsre_rca_unified/runtime.py") in paths
    assert Path("src/ecomsre/model/gateway.py") in paths
    assert Path("src/ecomsre_live_sandbox/contracts.py") in paths
    assert all(path.parts[0] not in {"tests", "docs"} for path in paths)


def test_canonical_source_batch_failure_uses_legal_v4_terminal() -> None:
    assert _canonical_failure_verdict(
        e2e_v4_module.DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
        cleanup_verdict="CLEAN",
    ) == "BLOCKED_E2E_V4_SOURCE_BATCH_FAILED"


def test_development_budget_denies_identical_rerun_and_exhausts_at_three(
    tmp_path: Path,
) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)

    index, run_id = _consume_development_budget(
        config,
        roots,
        implementation_commit="1" * 40,
        runtime_config_aggregate="a" * 64,
    )
    assert (index, run_id) == (1, "probe-01")
    _complete_development_budget(roots, run_id=run_id, verdict="FAILED_ONE")

    with pytest.raises(RuntimeError, match="identical development rerun"):
        _consume_development_budget(
            config,
            roots,
            implementation_commit="1" * 40,
            runtime_config_aggregate="a" * 64,
        )

    for expected_index, aggregate in ((2, "b" * 64), (3, "c" * 64)):
        index, run_id = _consume_development_budget(
            config,
            roots,
            implementation_commit=str(expected_index) * 40,
            runtime_config_aggregate=aggregate,
        )
        assert index == expected_index
        _complete_development_budget(
            roots,
            run_id=run_id,
            verdict=f"FAILED_{expected_index}",
        )

    with pytest.raises(RuntimeError, match="development integration budget is exhausted"):
        _consume_development_budget(
            config,
            roots,
            implementation_commit="4" * 40,
            runtime_config_aggregate="d" * 64,
        )


def test_development_pass_prevents_another_probe(tmp_path: Path) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    _, run_id = _consume_development_budget(
        config,
        roots,
        implementation_commit="1" * 40,
        runtime_config_aggregate="a" * 64,
    )
    _complete_development_budget(
        roots,
        run_id=run_id,
        verdict=config.authority.development_success_terminal,
    )

    with pytest.raises(RuntimeError, match="already passed"):
        _consume_development_budget(
            config,
            roots,
            implementation_commit="2" * 40,
            runtime_config_aggregate="a" * 64,
        )

    index, run_id = _consume_development_budget(
        config,
        roots,
        implementation_commit="2" * 40,
        runtime_config_aggregate="b" * 64,
    )
    assert (index, run_id) == (2, "probe-02")


class FakeController:
    def __init__(self, baseline_sha256: str) -> None:
        self.baseline_sha256 = baseline_sha256

    def read_current(self) -> object:
        return SimpleNamespace(document_sha256=self.baseline_sha256)

    def restore_baseline(self) -> object:
        return self.read_current()


class FakeEnvironment:
    last: "FakeEnvironment | None" = None

    def __init__(self, **kwargs: Any) -> None:
        self.runner = kwargs["runner"]
        self.flagd_directory = kwargs["flagd_directory"]
        self.cleanup_calls = 0
        FakeEnvironment.last = self

    def verify_local_docker(self) -> dict[str, str]:
        return {
            "context": "desktop-linux",
            "endpoint": "unix://opaque",
            "daemon_id": "opaque",
        }

    def verify_upstream(self) -> None:
        return None

    def resolve(self) -> tuple[object, dict[str, object]]:
        volume = {
            "type": "bind",
            "source": str(self.flagd_directory),
            "target": "/etc/flagd",
            "read_only": True,
        }
        compose = {
            "services": {
                "flagd": {
                    "image": "example.invalid/flagd:3.0.0",
                    "volumes": [volume],
                },
                "flagd-ui": {
                    "image": "example.invalid/flagd-ui:3.0.0",
                    "volumes": [{**volume, "target": "/app/data"}],
                },
            },
            "networks": {"default": {"name": "ecomsre-live-sandbox-v1-default"}},
        }
        return SimpleNamespace(endpoints=SimpleNamespace(), compose_sha256="a" * 64), compose

    def inspect_cached_images(self, *_: object) -> CachedImageInspection:
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
        return None

    def snapshot_all_resources(self) -> object:
        return SimpleNamespace(
            containers=frozenset(),
            networks=frozenset(),
            volumes=frozenset(),
        )

    def start(self) -> None:
        from ecomsre_live_sandbox.e2e_diagnostics import DiagnosticCommandIdentity

        if self.runner.on_start is not None:
            self.runner.on_start(DiagnosticCommandIdentity.COMPOSE_UP)
        if self.runner.on_return is not None:
            self.runner.on_return(DiagnosticCommandIdentity.COMPOSE_UP, 0, False)

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        return {
            "container": 25 if require_complete else 0,
            "network": 1 if require_complete else 0,
            "volume": 3 if require_complete else 0,
        }

    def wait_healthy(self, **_: object) -> dict[str, bool]:
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
            verdict="CLEAN",
        )


def _controller(config: object, *_: object, **__: object) -> FakeController:
    return FakeController(config.sandbox.scenario.baseline_document_sha256)  # type: ignore[attr-defined]


def _evidence(*_: object, **__: object) -> NoFaultEvidence:
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


def _fake_worktree(_: object, clean_required: bool) -> str:
    assert clean_required is True
    return "d" * 40


def _pass_development(config: object, roots: E2EV4PrivateRoots) -> dict[str, object]:
    return run_development_probe(
        config,  # type: ignore[arg-type]
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )


def _write_canonical_admission(roots: E2EV4PrivateRoots) -> None:
    write_private_json(
        roots.control / "exact-head-ci.json",
        {
            "schema_version": "live-e2e.exact-head-ci.v4",
            "implementation_commit": "d" * 40,
            "workflows": {
                "Agent mainline": {"run_id": 101, "conclusion": "SUCCESS"},
                "RCAEval RE2 v2 development": {
                    "run_id": 102,
                    "conclusion": "SUCCESS",
                },
            },
        },
        create_once=True,
    )
    write_private_json(
        roots.control / "pre-live-review.json",
        {
            "schema_version": "live-e2e.pre-live-review.v4",
            "implementation_commit": "d" * 40,
            "verdict": "PRE_LIVE_PASS",
            "must_fix_count": 0,
        },
        create_once=True,
    )


def test_development_probe_passes_without_protected_actions(tmp_path: Path) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")

    terminal = _pass_development(config, roots)

    assert terminal["verdict"] == config.authority.development_success_terminal
    assert terminal["all_three_terminals_retained"] is True
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["fault_injections"] == 0
    assert terminal["provider_calls"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["approval_records"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert (roots.control / "development-pass-lock.json").is_file()
    assert not (roots.control / "scenario-lock.json").exists()
    assert not (roots.control / "approval-request.json").exists()
    assert not (roots.control / "human-approval.json").exists()
    assert FakeEnvironment.last is not None and FakeEnvironment.last.cleanup_calls == 1

    with pytest.raises(RuntimeError, match="already passed"):
        _pass_development(config, roots)


def test_runtime_config_change_invalidates_development_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")
    _pass_development(config, roots)

    hashes, _ = e2e_v4_module._runtime_config_aggregate(config)
    monkeypatch.setattr(
        e2e_v4_module,
        "_runtime_config_aggregate",
        lambda _: (hashes, "f" * 64),
    )
    with pytest.raises(RuntimeError, match="stale or invalid"):
        _require_development_pass(
            config,
            roots,
            implementation_commit="d" * 40,
        )


def test_canonical_requires_ci_review_then_creates_only_preauthorization_artifacts(
    tmp_path: Path,
) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")
    development = _pass_development(config, roots)
    assert development["verdict"] == config.authority.development_success_terminal

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

    _write_canonical_admission(roots)
    terminal = run_canonical_invocation_a(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )

    assert terminal["verdict"] == config.authority.invocation_a_terminal
    assert terminal["scenario_lock_created"] is True
    assert terminal["plan_template_created"] is True
    assert terminal["approval_request_created"] is True
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["codex_self_approved"] is False
    assert terminal["human_approval_record_present"] is False
    assert terminal["fault_injections"] == 0
    assert terminal["provider_calls"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert "scripts.live_sandbox.e2e_v4" in str(terminal["approval_command"])
    assert not (roots.control / "human-approval.json").exists()


def test_invocation_b_requires_human_record_before_provider(tmp_path: Path) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")
    _pass_development(config, roots)
    _write_canonical_admission(roots)
    canonical = run_canonical_invocation_a(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )
    assert canonical["verdict"] == config.authority.invocation_a_terminal
    provider_calls = 0

    def forbidden_provider(_: object) -> object:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("Provider must remain unreachable without human approval")

    with pytest.raises(RuntimeError, match="HumanApprovalRecord"):
        run_invocation_b(
            config,
            roots,
            provider_factory=forbidden_provider,
            environment_factory=FakeEnvironment,
            worktree_verifier=_fake_worktree,
            sleep=lambda _: None,
            public_writer=lambda *_: (),
        )

    assert provider_calls == 0
    assert not (roots.invocation_b / "started.json").exists()


def test_v4_provider_preflight_failure_stops_before_fault_and_docker(
    tmp_path: Path,
) -> None:
    config = load_e2e_v4_config(CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")
    _pass_development(config, roots)
    _write_canonical_admission(roots)
    run_canonical_invocation_a(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )
    request = json.loads((roots.control / "approval-request.json").read_text())
    phrase = f"APPROVE {request['scenario_id']} {request['plan_template_sha256']}"
    record = record_human_approval_for_invocation_b(
        config,
        roots,
        approver="Human Reviewer",
        phrase=phrase,
    )
    assert record.mode == "HUMAN"

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

    assert terminal["schema_version"] == "live-e2e.invocation-b-terminal.v4"
    assert terminal["verdict"] == "BLOCKED_PROVIDER_PREFLIGHT"
    assert terminal["provider_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["compose_start_requested"] is False
