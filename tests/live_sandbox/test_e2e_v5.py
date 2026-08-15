from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ecomsre_live_sandbox import e2e_v1, e2e_v3
from ecomsre_live_sandbox.contracts import (
    CleanupResult,
    canonical_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandIdentity,
    DiagnosticFailureCode,
    V5_DIAGNOSTIC_FAILURE_CODES,
    V5_DIAGNOSTIC_STAGES,
)
from ecomsre_live_sandbox.e2e_v5 import (
    _public_result_v5,
    run_canonical_invocation_a,
    run_development_probe,
    run_invocation_b,
    verify_public_result,
)
from ecomsre_live_sandbox.e2e_v5_contracts import (
    E2EV5PrivateRoots,
    load_e2e_v5_config,
)
from ecomsre_live_sandbox.image_authority import CachedImage, CachedImageInspection
from ecomsre_live_sandbox.invocation_b_verdicts import (
    get_invocation_b_verdict_policy,
)


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v5")


def test_v5_authority_binds_exact_successor_and_separated_contracts() -> None:
    config = load_e2e_v5_config(CONFIG)
    authority = config.authority

    assert authority.version == "live-fault-a0-controlled-remediation-e2e-v5"
    assert authority.branch == "feature/live-fault-a0-controlled-remediation-e2e-v5"
    assert authority.predecessor_pr == 35
    assert authority.predecessor_head == "47a797137dbea2bf1b639196779ed9381eabd939"
    assert authority.predecessor_terminal == (
        "BLOCKED_E2E_V4_DEVELOPMENT_INTEGRATION_EXHAUSTED"
    )
    assert authority.maximum_development_integration_probes == 2
    assert authority.maximum_canonical_invocation_a_runs == 1
    assert config.no_fault_readiness.anomaly_evidence_required is False
    assert config.no_fault_readiness.a0_context_allowed is False
    assert config.fault_projection.diagnostic_admission_policy == (
        "METRICS_PLUS_LOGS_OR_TRACES"
    )
    assert config.diagnostics.required_stages == tuple(
        stage.value for stage in V5_DIAGNOSTIC_STAGES
    )
    assert config.diagnostics.failure_codes == tuple(
        code.value for code in V5_DIAGNOSTIC_FAILURE_CODES
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


def test_v5_private_root_rejects_earlier_lifecycle_reuse(tmp_path: Path) -> None:
    config = load_e2e_v5_config(CONFIG)
    fresh = E2EV5PrivateRoots(tmp_path / "private-v5")
    fresh.bind_lifecycle(config.authority, repository_root=config.repository_root)

    assert (fresh.root.stat().st_mode & 0o777) == 0o700
    assert (fresh.control / "private-root-lifecycle.json").is_file()
    assert fresh.probe_root(1) != fresh.probe_root(2)
    with pytest.raises(ValueError, match="earlier-version private root"):
        E2EV5PrivateRoots(
            tmp_path / "live-fault-a0-controlled-remediation-e2e-v4" / "v5"
        ).bind_lifecycle(config.authority, repository_root=config.repository_root)


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
            containers=frozenset(), networks=frozenset(), volumes=frozenset()
        )

    def start(self) -> None:
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


def _evidence(
    _: object,
    __: object,
    run_root: Path,
    *___: object,
    services_healthy_count: int,
    baseline_exact: bool,
    **____: object,
) -> object:
    assert services_healthy_count == 25
    assert baseline_exact is True
    statuses = {"METRICS": "AVAILABLE", "LOGS": "AVAILABLE", "TRACES": "AVAILABLE"}
    counts = {"METRICS": 5, "LOGS": 28, "TRACES": 14}
    write_private_json(
        run_root / "source-results.json",
        {
            "schema_version": "live-e2e.source-results.v5",
            "results": [
                {
                    "source": source,
                    "status": statuses[source],
                    "target_record_count": counts[source],
                }
                for source in statuses
            ],
            "source_counts": counts,
            "all_refs_resolve": True,
            "invalid_ref_count": 0,
            "combined_resolver_sha256": "a" * 64,
            "source_results_sha256": "b" * 64,
        },
        create_once=True,
    )
    readiness_payload = {
        "schema_version": "live-e2e.no-fault-readiness.v5",
        "run_id": run_root.name,
        "services_healthy_count": 25,
        "baseline_exact": True,
        "source_statuses": statuses,
        "source_counts": counts,
        "invalid_refs": 0,
        "all_refs_resolve": True,
        "broad_metric_service_count": 4,
        "logs_query_contract_completed": True,
        "traces_query_contract_completed": True,
        "control_truth_findings": [],
        "private_permissions_valid": True,
        "passed": True,
        "reason_codes": [],
        "semantic_sha256": "c" * 64,
    }
    write_private_json(
        run_root / "no-fault-readiness.json",
        readiness_payload,
        create_once=True,
    )
    readiness = SimpleNamespace(**readiness_payload)
    return SimpleNamespace(
        metrics_status="AVAILABLE",
        logs_status="AVAILABLE",
        traces_status="AVAILABLE",
        source_counts=counts,
        invalid_refs=0,
        visible_service_count=4,
        scenario_truth_leaked=False,
        projection_sha256="c" * 64,
        readiness=readiness,
    )


def _fake_worktree(_: object, clean_required: bool) -> str:
    assert clean_required is True
    return "d" * 40


def _pass_development(config: object, roots: E2EV5PrivateRoots) -> dict[str, object]:
    return run_development_probe(
        config,  # type: ignore[arg-type]
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )


def _write_canonical_admission(roots: E2EV5PrivateRoots) -> None:
    write_private_json(
        roots.control / "exact-head-ci.json",
        {
            "schema_version": "live-e2e.exact-head-ci.v5",
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
            "schema_version": "live-e2e.pre-live-review.v5",
            "implementation_commit": "d" * 40,
            "verdict": "PRE_LIVE_PASS",
            "must_fix_count": 0,
        },
        create_once=True,
    )


def test_v5_development_uses_readiness_and_never_builds_a0_context(
    tmp_path: Path,
) -> None:
    config = load_e2e_v5_config(CONFIG)
    roots = E2EV5PrivateRoots(tmp_path / "private-v5")

    terminal = _pass_development(config, roots)

    assert terminal["verdict"] == config.authority.development_success_terminal
    assert terminal["no_fault_readiness"] is True
    assert terminal["a0_context_builder_calls"] == 0
    assert terminal["all_three_terminals_retained"] is True
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["fault_injections"] == 0
    assert terminal["provider_calls"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert (roots.control / "development-pass-lock.json").is_file()
    assert (roots.probe_root(1) / "no-fault-readiness.json").is_file()
    assert not (roots.probe_root(1) / "projection-summary.json").exists()
    assert not (roots.control / "scenario-lock.json").exists()


def test_v5_canonical_stops_at_real_human_preauthorization_boundary(
    tmp_path: Path,
) -> None:
    config = load_e2e_v5_config(CONFIG)
    roots = E2EV5PrivateRoots(tmp_path / "private-v5")
    _pass_development(config, roots)
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
    assert terminal["no_fault_readiness"] is True
    assert terminal["a0_context_builder_calls"] == 0
    assert terminal["scenario_lock_created"] is True
    assert terminal["plan_template_created"] is True
    assert terminal["approval_request_created"] is True
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["codex_self_approved"] is False
    assert terminal["human_approval_record_present"] is False
    assert "scripts.live_sandbox.e2e_v5" in str(terminal["approval_command"])
    lock = json.loads((roots.control / "scenario-lock.json").read_text())
    assert len(lock["canonical_source_results_sha256"]) == 64
    assert len(lock["canonical_no_fault_readiness_sha256"]) == 64
    assert not (roots.control / "human-approval.json").exists()

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


def test_v5_provider_preflight_and_live_batch_keep_builder_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v5_config(CONFIG)

    def forbidden_builder(**_: object) -> object:
        raise AssertionError("synthetic Provider preflight must not call live A0 builder")

    monkeypatch.setattr(e2e_v1, "build_live_a0_context", forbidden_builder)
    context = e2e_v1._synthetic_provider_context(config)  # type: ignore[arg-type]

    assert len(context.visible_entities) == 3
    assert e2e_v3._schema_suffix(config) == "v5"


def test_v5_public_verifier_recomputes_success_aggregates() -> None:
    config = load_e2e_v5_config(CONFIG)
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
        "all_refs_resolve": True,
        "projection_broad_counts": {"metrics": 5, "logs": 28, "traces": 14},
        "projection_diagnostic_counts": {"metrics": 3, "logs": 3, "traces": 0},
        "empty_model_streams": ["TRACES"],
        "projection_reason_codes": ["NO_DIAGNOSTIC_TRACES"],
        "visible_service_count": 3,
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
    public = _public_result_v5(config, terminal)
    verify_public_result(config, public)

    for key, forged_value in (
        ("fault_injections", 0),
        ("all_refs_resolve", False),
        ("projection_diagnostic_counts", {"metrics": 3, "logs": 0, "traces": 0}),
        ("cleanup", {**terminal["cleanup"], "owned_containers": 1}),
    ):
        forged = {**public, key: forged_value}
        core = dict(forged)
        core.pop("semantic_sha256")
        forged["semantic_sha256"] = canonical_sha256(core)
        with pytest.raises(ValueError, match="success aggregates"):
            verify_public_result(config, forged)


def test_v5_runtime_failure_verdicts_are_not_provider_preflight() -> None:
    policy = get_invocation_b_verdict_policy("v5")

    for failure_code in (
        DiagnosticFailureCode.COMPOSE_UP_FAILED,
        DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
        DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE,
        DiagnosticFailureCode.UNCLASSIFIED_RUNTIME_FAILURE,
    ):
        assert policy.terminal_for(failure_code) != policy.provider_preflight_failed
