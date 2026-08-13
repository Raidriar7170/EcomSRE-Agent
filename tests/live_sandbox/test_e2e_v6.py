from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ecomsre_live_sandbox.e2e_v3 as e2e_v3
import ecomsre_live_sandbox.e2e_v4 as e2e_v4
from ecomsre_live_sandbox.contracts import (
    CleanupResult,
    DiagnosisResult,
    SLIWindow,
    canonical_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandIdentity,
    DiagnosticFailureCode,
    DiagnosticStage,
)
from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveTraceObservation,
)
from ecomsre_live_sandbox.e2e_v6 import (
    record_human_approval_for_invocation_b,
    run_canonical_invocation_a,
    run_development_probe,
    run_invocation_b,
)
from ecomsre_live_sandbox.e2e_v6_contracts import (
    E2EV6PrivateRoots,
    load_e2e_v6_config,
)
from ecomsre_live_sandbox.image_authority import CachedImage, CachedImageInspection
from ecomsre_live_sandbox.invocation_b_assurance import (
    build_expected_public_result,
    write_fault_time_context_evidence,
)
from ecomsre_live_sandbox.invocation_b_verdicts import (
    invocation_b_verdict_policy_sha256,
)
from ecomsre_rca100.contracts import RCA100InitialDiagnosis, RCA100ReasoningStep


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v6")


@pytest.mark.parametrize(
    ("failure_code", "suffix"),
    (
        (
            DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH,
            "BASELINE_CONFIGURATION_MISMATCH",
        ),
        (
            DiagnosticFailureCode.NO_FAULT_READINESS_FAILED,
            "NO_FAULT_READINESS_FAILED",
        ),
    ),
)
def test_v6_no_fault_failure_mappings_are_version_exact(
    failure_code: DiagnosticFailureCode, suffix: str
) -> None:
    expected = f"BLOCKED_E2E_V6_{suffix}"

    assert e2e_v4._development_failure_verdict(
        failure_code,
        cleanup_verdict="CLEAN",
        schema_suffix="v6",
    ) == expected
    assert e2e_v4._canonical_failure_verdict(
        failure_code,
        cleanup_verdict="CLEAN",
        schema_suffix="v6",
    ) == expected


def test_v6_authority_binds_exact_predecessor_and_unbounded_repair_policies() -> None:
    config = load_e2e_v6_config(CONFIG)
    authority = config.authority

    assert authority.version == "live-fault-a0-controlled-remediation-e2e-v6"
    assert authority.branch == "feature/live-fault-a0-controlled-remediation-e2e-v6"
    assert authority.predecessor_pr == 36
    assert authority.predecessor_head == "5080f495b070669bd016bdddd52705fcd22b4abe"
    assert authority.predecessor_terminal == "BLOCKED_E2E_V5_PRE_LIVE_REVIEW"
    assert authority.predecessor_reason == "INVOCATION_B_ASSURANCE_CLOSURE_REQUIRED"
    assert not hasattr(authority, "maximum_development_integration_probes")
    assert not hasattr(authority, "maximum_canonical_invocation_a_runs")
    assert config.assurance.development_repeat_policy == (
        "REQUIRE_IMPLEMENTATION_OR_CONFIG_CHANGE_AND_PRESERVE_PRIOR_EVIDENCE"
    )
    assert config.assurance.pre_live_policy == "REPAIR_IN_SCOPE_MUST_FIX_UNTIL_ZERO"
    assert authority.versioned_verdict_policy_sha256 == (
        invocation_b_verdict_policy_sha256("v6")
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


def test_v6_private_root_is_fresh_and_uses_monotonic_run_names(tmp_path: Path) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "private-v6")

    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)

    assert roots.probe_root(1).name == "run-0001"
    assert roots.probe_root(12).name == "run-0012"
    assert roots.canonical_attempt(1).name == "attempt-0001"
    assert (roots.control / "private-root-lifecycle.json").is_file()
    with pytest.raises(ValueError, match="earlier-version private root"):
        E2EV6PrivateRoots(
            tmp_path / "live-fault-a0-controlled-remediation-e2e-v5" / "v6"
        ).bind_lifecycle(config.authority, repository_root=config.repository_root)


class FakeController:
    def __init__(self, baseline_sha256: str, fault_sha256: str) -> None:
        self.baseline_sha256 = baseline_sha256
        self.fault_sha256 = fault_sha256
        self.current_sha256 = baseline_sha256
        self.read_failure_count = 0
        self.force_mismatch = False

    def read_current(self) -> object:
        if self.read_failure_count > 0:
            self.read_failure_count -= 1
            raise RuntimeError("fake baseline read failure")
        value = "f" * 64 if self.force_mismatch else self.current_sha256
        return SimpleNamespace(document_sha256=value)

    def restore_baseline(self) -> object:
        self.current_sha256 = self.baseline_sha256
        self.force_mismatch = False
        return self.read_current()

    def inject_fault(self) -> object:
        self.current_sha256 = self.fault_sha256
        return SimpleNamespace(document_sha256=self.fault_sha256)


class FakeEnvironment:
    fail_at: str | None = None
    controller: FakeController | None = None
    next_controller_read_failures = 0
    next_controller_mismatch = False

    def __init__(self, **kwargs: Any) -> None:
        self.runner = kwargs["runner"]
        self.flagd_directory = kwargs["flagd_directory"]

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
            "networks": {
                "default": {"name": "ecomsre-live-sandbox-v1-default"}
            },
        }
        endpoints = SimpleNamespace(
            prometheus="http://127.0.0.1:19090",
            opensearch="http://127.0.0.1:19200",
            jaeger="http://127.0.0.1:11686",
        )
        return SimpleNamespace(endpoints=endpoints, compose_sha256="a" * 64), compose

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
        if FakeEnvironment.fail_at == "compose":
            if self.runner.on_return is not None:
                self.runner.on_return(DiagnosticCommandIdentity.COMPOSE_UP, 1, False)
            raise RuntimeError("fake Compose start failure")
        if self.runner.on_return is not None:
            self.runner.on_return(DiagnosticCommandIdentity.COMPOSE_UP, 0, False)

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        return {
            "container": 25 if require_complete else 0,
            "network": 1 if require_complete else 0,
            "volume": 3 if require_complete else 0,
        }

    def wait_healthy(self, **_: object) -> dict[str, bool]:
        if FakeEnvironment.fail_at == "health":
            raise RuntimeError("fake health timeout")
        return {f"service-{index}": True for index in range(25)}

    def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
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
    controller = FakeController(
        config.sandbox.scenario.baseline_document_sha256,  # type: ignore[attr-defined]
        config.sandbox.scenario.fault_document_sha256,  # type: ignore[attr-defined]
    )
    FakeEnvironment.controller = controller
    controller.read_failure_count = FakeEnvironment.next_controller_read_failures
    controller.force_mismatch = FakeEnvironment.next_controller_mismatch
    return controller


def _no_fault_evidence(
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
            "schema_version": "live-e2e.source-results.v6",
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
    return SimpleNamespace(
        metrics_status="AVAILABLE",
        logs_status="AVAILABLE",
        traces_status="AVAILABLE",
        source_counts=counts,
        invalid_refs=0,
        visible_service_count=4,
        scenario_truth_leaked=False,
        projection_sha256="c" * 64,
        readiness=SimpleNamespace(**readiness_payload),
    )


def _failing_no_fault_evidence(
    _: object,
    __: object,
    run_root: Path,
    tracker: object,
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
            "schema_version": "live-e2e.source-results.v6",
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

    def fail_readiness() -> None:
        write_private_json(
            run_root / "no-fault-readiness.json",
            {
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
                "control_truth_findings": ["injected readiness failure"],
                "private_permissions_valid": True,
                "passed": False,
                "reason_codes": ["CONTROL_TRUTH_LEAK"],
                "semantic_sha256": "c" * 64,
            },
            create_once=True,
        )
        raise RuntimeError("injected no-fault readiness failure")

    tracker.execute(  # type: ignore[attr-defined]
        DiagnosticStage.NO_FAULT_READINESS_EVALUATED,
        fail_readiness,
        failure_code=DiagnosticFailureCode.NO_FAULT_READINESS_FAILED,
    )
    raise AssertionError("unreachable")


def _fake_worktree(_: object, clean_required: bool) -> str:
    assert clean_required is True
    return "d" * 40


def _prepare_v6_canonical_admission(
    config: object, roots: E2EV6PrivateRoots
) -> None:
    FakeEnvironment.fail_at = None
    FakeEnvironment.next_controller_read_failures = 0
    FakeEnvironment.next_controller_mismatch = False
    development = run_development_probe(
        config,  # type: ignore[arg-type]
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_no_fault_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )
    assert development["verdict"] == config.authority.development_success_terminal  # type: ignore[attr-defined]
    for name, payload in (
        (
            "exact-head-ci.json",
            {
                "schema_version": "live-e2e.exact-head-ci.v6",
                "implementation_commit": "d" * 40,
                "workflows": {
                    "Agent mainline": {"run_id": 101, "conclusion": "SUCCESS"},
                    "RCAEval RE2 v2 development": {
                        "run_id": 102,
                        "conclusion": "SUCCESS",
                    },
                },
            },
        ),
        (
            "pre-live-review.json",
            {
                "schema_version": "live-e2e.pre-live-review.v6",
                "implementation_commit": "d" * 40,
                "verdict": "PRE_LIVE_PASS",
                "must_fix_count": 0,
            },
        ),
    ):
        write_private_json(roots.control / name, payload, create_once=True)


def _prepare_approved_v6(config: object, roots: E2EV6PrivateRoots) -> None:
    _prepare_v6_canonical_admission(config, roots)
    canonical = run_canonical_invocation_a(
        config,  # type: ignore[arg-type]
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=_no_fault_evidence,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )
    assert canonical["verdict"] == config.authority.invocation_a_terminal  # type: ignore[attr-defined]
    request = json.loads((roots.control / "approval-request.json").read_text())
    record_human_approval_for_invocation_b(
        config,  # type: ignore[arg-type]
        roots,
        approver="Human Reviewer",
        phrase=(
            f"APPROVE {request['scenario_id']} {request['plan_template_sha256']}"
        ),
    )


@pytest.mark.parametrize("run_kind", ("development", "canonical"))
@pytest.mark.parametrize(
    ("failure_kind", "expected_suffix", "expected_stage"),
    (
        (
            "baseline-mismatch",
            "BASELINE_CONFIGURATION_MISMATCH",
            DiagnosticStage.BASELINE_CONFIGURATION_VERIFIED,
        ),
        (
            "no-fault-readiness",
            "NO_FAULT_READINESS_FAILED",
            DiagnosticStage.NO_FAULT_READINESS_EVALUATED,
        ),
    ),
)
def test_v6_executes_version_exact_no_fault_failure_terminal(
    tmp_path: Path,
    run_kind: str,
    failure_kind: str,
    expected_suffix: str,
    expected_stage: DiagnosticStage,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / f"{run_kind}-{failure_kind}")
    FakeEnvironment.fail_at = None
    FakeEnvironment.next_controller_read_failures = 0
    FakeEnvironment.next_controller_mismatch = False
    if run_kind == "canonical":
        _prepare_v6_canonical_admission(config, roots)
    FakeEnvironment.next_controller_mismatch = failure_kind == "baseline-mismatch"
    evidence_collector = (
        _failing_no_fault_evidence
        if failure_kind == "no-fault-readiness"
        else _no_fault_evidence
    )
    runner = (
        run_canonical_invocation_a
        if run_kind == "canonical"
        else run_development_probe
    )

    terminal = runner(
        config,
        roots,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        evidence_collector=evidence_collector,
        sleep=lambda _: None,
        worktree_verifier=_fake_worktree,
    )

    assert terminal["verdict"] == f"BLOCKED_E2E_V6_{expected_suffix}"
    assert "E2E_V5" not in str(terminal["verdict"])
    assert terminal["failed_stage"] == expected_stage.value
    assert terminal["failure_code"] == expected_suffix
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["provider_calls"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0


def test_v6_canonical_promotion_failure_is_rolled_back_and_retry_advances(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "private-v6")
    _prepare_v6_canonical_admission(config, roots)

    def fail_during_promotion(
        path: Path, value: object, *, create_once: bool
    ) -> str:
        if path.name == "plan-template.json":
            raise OSError("injected canonical promotion failure")
        return write_private_json(path, value, create_once=create_once)

    with pytest.raises(OSError, match="promotion failure"):
        run_canonical_invocation_a(
            config,
            roots,
            environment_factory=FakeEnvironment,
            controller_factory=_controller,
            evidence_collector=_no_fault_evidence,
            sleep=lambda _: None,
            worktree_verifier=_fake_worktree,
            canonical_control_writer=fail_during_promotion,
        )

    attempt_1 = roots.canonical_attempt(1)
    terminal = json.loads((attempt_1 / "terminal.json").read_text())
    history = json.loads(
        (roots.control / "canonical-history.json").read_text()
    )
    assert terminal["verdict"] == config.authority.invocation_a_terminal
    assert history["attempts"] == [
        {
            "attempt_relative_path": "canonical/attempt-0001",
            "verdict": config.authority.invocation_a_terminal,
        }
    ]
    assert all(
        (attempt_1 / name).is_file()
        for name in (
            "scenario-lock.json",
            "plan-template.json",
            "approval-request.json",
        )
    )
    assert all(
        not (roots.control / name).exists()
        for name in (
            "scenario-lock.json",
            "plan-template.json",
            "approval-request.json",
            "canonical-accepted.json",
        )
    )

    e2e_v4._consume_canonical_budget(
        config,
        roots,
        implementation_commit="e" * 40,
    )
    active = json.loads((roots.control / "canonical-active.json").read_text())
    assert active["attempt_index"] == 2
    assert (roots.canonical_attempt(2) / "started.json").is_file()


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.usage_known = True
        self.last_usage_tokens = 1
        self.last_request_sha256 = "0" * 64
        self.live_context_sha256: str | None = None
        self.live_context: object | None = None

    def diagnose(self, context: object) -> object:
        self.calls += 1
        if self.calls == 2:
            self.live_context_sha256 = canonical_sha256(context)
            self.live_context = context
        return {"provider_call": self.calls}


def test_v6_serializes_real_provider_preflight_model_before_next_stage_failure(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "real-provider-preflight-model")
    _prepare_approved_v6(config, roots)
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref="apm|apm.service|synthetic",
        fault_type="synthetic preflight",
        confidence=0.8,
        evidence_refs=("log:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="Synthetic typed output for Provider admission.",
                entity_ref_or_none="apm|apm.service|synthetic",
                evidence_refs=("log:0001",),
            ),
        ),
        summary="Synthetic typed Provider preflight output.",
    )

    class RealModelPreflightProvider(FakeProvider):
        def diagnose(self, _: object) -> RCA100InitialDiagnosis:
            self.calls += 1
            return diagnosis

    def stop_after_preflight(_: float) -> None:
        raise RuntimeError("stop after the persisted synthetic preflight")

    provider = RealModelPreflightProvider()
    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=stop_after_preflight,
        public_writer=lambda *_: (),
    )

    artifact = json.loads(
        (roots.provider / "synthetic-preflight-v2.json").read_text(encoding="utf-8")
    )
    assert artifact["diagnosis"] == diagnosis.model_dump(mode="json")
    assert terminal["provider_preflight_passed"] is True
    assert terminal["provider_calls"] == 1
    assert terminal["exception_type"] == "RuntimeError"


@pytest.mark.parametrize(
    ("failure_kind", "expected_code", "cleanup"),
    (
        ("compose", "COMPOSE_UP_FAILED", "CLEAN"),
        ("health", "SERVICE_HEALTH_TIMEOUT", "CLEAN"),
        ("baseline-read", "BASELINE_CONFIGURATION_UNAVAILABLE", "CLEAN"),
        ("baseline-mismatch", "BASELINE_CONFIGURATION_MISMATCH", "CLEAN"),
        ("unclassified", "UNCLASSIFIED_RUNTIME_FAILURE", "NOT_REQUIRED"),
    ),
)
def test_v6_executes_post_preflight_failure_mapping_without_provider_misclassification(
    tmp_path: Path,
    failure_kind: str,
    expected_code: str,
    cleanup: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / failure_kind)
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = failure_kind if failure_kind in {"compose", "health"} else None
    if failure_kind == "baseline-read":
        FakeEnvironment.next_controller_read_failures = 1
    elif failure_kind == "baseline-mismatch":
        FakeEnvironment.next_controller_mismatch = True
    provider = FakeProvider()

    def invocation_sleep(_: float) -> None:
        if failure_kind == "unclassified":
            raise RuntimeError("fake unclassified post-preflight failure")

    def assert_sealed_before_public(
        _config: object, sealed_terminal: dict[str, object]
    ) -> tuple[str, ...]:
        terminal_path = roots.invocation_b / "terminal.json"
        assert terminal_path.is_file()
        assert json.loads(terminal_path.read_text(encoding="utf-8")) == sealed_terminal
        return ()

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=invocation_sleep,
        public_writer=assert_sealed_before_public,
    )

    assert terminal["verdict"] == f"BLOCKED_E2E_V6_{expected_code}"
    assert terminal["verdict"] != "BLOCKED_PROVIDER_PREFLIGHT"
    assert terminal["provider_preflight_passed"] is True
    assert terminal["provider_calls"] == 1
    assert terminal["model_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["failure_code"] == expected_code
    assert terminal["cleanup_verdict"] == cleanup
    assert terminal["last_completed_stage"] is not None
    if failure_kind == "unclassified":
        assert terminal["failed_stage"] == "LOCAL_DOCKER_VERIFIED"
        assert terminal["last_completed_stage"] == "WORKTREE_VERIFIED"
    events = tuple(
        json.loads(line)
        for line in (roots.invocation_b / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert not any(
        {"FAILED", "PASSED"}
        <= {event["status"] for event in events if event["stage"] == stage}
        for stage in {event["stage"] for event in events}
    )
    assert (roots.invocation_b / "terminal.json").is_file()


def test_v6_source_batch_failure_preserves_tracker_root_and_remains_verifiable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "source-batch-failure")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = None
    FakeEnvironment.next_controller_read_failures = 0
    FakeEnvironment.next_controller_mismatch = False
    provider = FakeProvider()
    snapshots = iter(
        (
            {"frontend": (100.0, 1.0, 20.0)},
            {"frontend": (100.0, 30.0, 40.0)},
        )
    )

    monkeypatch.setattr(
        e2e_v3,
        "_capture_sli_window",
        lambda *_, phase: _sli_window(phase),
    )
    monkeypatch.setattr(e2e_v3, "fault_impact_passed", lambda *_: True)
    monkeypatch.setattr(
        e2e_v3,
        "_broad_metric_snapshot",
        lambda *_, **__: next(snapshots),
    )

    def fail_source_batch(*_: object, **__: object) -> object:
        raise RuntimeError("injected source batch failure before result sealing")

    monkeypatch.setattr(e2e_v3, "collect_ordered_source_batch", fail_source_batch)

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    assert terminal["verdict"] == "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["failed_stage"] == "SOURCE_CAPTURE_WINDOW_STARTED"
    assert terminal["last_completed_stage"] == "BASELINE_CONFIGURATION_VERIFIED"
    assert terminal["failure_code"] == "UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["provider_calls"] == 1
    assert terminal["model_calls"] == 0
    assert terminal["fault_injections"] == 1
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert "source_availability" not in terminal
    assert "source_counts" not in terminal
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert build_expected_public_result(config, terminal)["verdict"] == (
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )


def test_v6_post_source_seal_failure_preserves_complete_source_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "post-source-seal-failure")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = None
    provider = FakeProvider()
    snapshots = iter(
        (
            {"frontend": (100.0, 1.0, 20.0)},
            {"frontend": (100.0, 30.0, 40.0)},
        )
    )
    monkeypatch.setattr(
        e2e_v3,
        "_capture_sli_window",
        lambda *_, phase: _sli_window(phase),
    )
    monkeypatch.setattr(e2e_v3, "fault_impact_passed", lambda *_: True)
    monkeypatch.setattr(
        e2e_v3,
        "_broad_metric_snapshot",
        lambda *_, **__: next(snapshots),
    )

    def fail_after_source_seal(*_: object, **kwargs: object) -> object:
        run_root = kwargs["run_root"]
        assert isinstance(run_root, Path)
        tracker: Any = kwargs["tracker"]
        counts = {"METRICS": 5, "LOGS": 6, "TRACES": 4}
        write_private_json(
            run_root / "source-results.json",
            {
                "schema_version": "live-e2e.source-results.v6",
                "results": [
                    {"source": source, "status": "AVAILABLE"}
                    for source in ("METRICS", "LOGS", "TRACES")
                ],
                "source_counts": counts,
                "invalid_ref_count": 0,
                "all_refs_resolve": True,
                "source_results_sha256": "b" * 64,
                "combined_resolver_sha256": "a" * 64,
            },
            create_once=True,
        )
        tracker.pass_stage(DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED)
        raise RuntimeError("injected failure after source result sealing")

    monkeypatch.setattr(e2e_v3, "collect_ordered_source_batch", fail_after_source_seal)

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    assert terminal["verdict"] == "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["failed_stage"] == "SOURCE_AVAILABILITY_GATE_EVALUATED"
    assert terminal["last_completed_stage"] == "EVIDENCE_RESOLUTION_COMPLETED"
    assert terminal["failure_code"] == "UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["source_availability"] == {
        "METRICS": "AVAILABLE",
        "LOGS": "AVAILABLE",
        "TRACES": "AVAILABLE",
    }
    assert terminal["source_counts"] == {"METRICS": 5, "LOGS": 6, "TRACES": 4}
    assert terminal["invalid_refs"] == 0
    assert terminal["all_refs_resolve"] is True
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert build_expected_public_result(config, terminal)["verdict"] == (
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )


def test_v6_executes_realizable_public_result_verification_failure_terminal(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "public-result-verification")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = "compose"
    FakeEnvironment.next_controller_read_failures = 0
    FakeEnvironment.next_controller_mismatch = False
    provider = FakeProvider()

    def reject_pre_seal_result(*_: object) -> None:
        raise ValueError("injected deterministic public verifier failure")

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
        pre_seal_terminal_verifier=reject_pre_seal_result,
    )

    assert terminal["verdict"] == "BLOCKED_PUBLIC_RESULT_VERIFICATION"
    assert terminal["public_result_source_verdict"] == (
        "BLOCKED_E2E_V6_COMPOSE_UP_FAILED"
    )
    assert terminal["public_result_source_failed_stage"] == (
        "COMPOSE_START_RETURNED"
    )
    assert terminal["public_result_source_failure_code"] == "COMPOSE_UP_FAILED"
    assert terminal["failed_stage"] == "PUBLIC_RESULT_VERIFICATION"
    assert terminal["failure_code"] == "PUBLIC_RESULT_VERIFICATION_FAILED"
    assert terminal["provider_calls"] == 1
    assert terminal["model_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert build_expected_public_result(config, terminal)["verdict"] == (
        "BLOCKED_PUBLIC_RESULT_VERIFICATION"
    )
    sealed = json.loads((roots.invocation_b / "terminal.json").read_text())
    assert sealed == terminal


def test_v6_cleanup_failure_preserves_compose_root_failure_identity(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "compose-and-cleanup-failure")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = "compose"
    FakeEnvironment.next_controller_read_failures = 0
    FakeEnvironment.next_controller_mismatch = False
    provider = FakeProvider()

    class BlockedCleanupEnvironment(FakeEnvironment):
        def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
            if self.runner.on_start is not None:
                self.runner.on_start(DiagnosticCommandIdentity.COMPOSE_DOWN)
            if self.runner.on_return is not None:
                self.runner.on_return(DiagnosticCommandIdentity.COMPOSE_DOWN, 1, False)
            return CleanupResult(
                baseline_restored=baseline_restored,
                owned_containers=1,
                owned_networks=0,
                owned_volumes=0,
                non_owned_resources_changed=False,
                verdict="BLOCKED",
            )

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=BlockedCleanupEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    assert terminal["verdict"] == "BLOCKED_CLEANUP_INCOMPLETE"
    assert terminal["failed_stage"] == "COMPOSE_START_RETURNED"
    assert terminal["last_completed_stage"] == "COMPOSE_START_REQUESTED"
    assert terminal["failure_code"] == "COMPOSE_UP_FAILED"
    assert terminal["cleanup_failure_code"] == "CLEANUP_FAILED"
    assert terminal["provider_calls"] == 1
    assert terminal["model_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["cleanup_verdict"] == "BLOCKED"
    assert build_expected_public_result(config, terminal)["verdict"] == (
        "BLOCKED_CLEANUP_INCOMPLETE"
    )


def test_v6_provider_preflight_failure_seals_verifier_accepted_identity(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "provider-preflight")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = None
    FakeEnvironment.next_controller_read_failures = 0
    FakeEnvironment.next_controller_mismatch = False

    def failing_provider(_: object) -> object:
        raise RuntimeError("injected Provider preflight failure")

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=failing_provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    assert terminal["verdict"] == "BLOCKED_PROVIDER_PREFLIGHT"
    assert terminal["failed_stage"] == "PROVIDER_PREFLIGHT"
    assert terminal["last_completed_stage"] == "WORKTREE_VERIFIED"
    assert terminal["failure_code"] == "PROVIDER_PREFLIGHT_FAILED"
    assert terminal["provider_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["cleanup_verdict"] == "NOT_REQUIRED"
    assert build_expected_public_result(config, terminal)["verdict"] == (
        "BLOCKED_PROVIDER_PREFLIGHT"
    )


def _sli_window(phase: str) -> SLIWindow:
    started = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    errors = 1.0 if phase == "BASELINE" else 30.0
    return SLIWindow(
        phase=phase,  # type: ignore[arg-type]
        started_at=started,
        ended_at=started + timedelta(seconds=30),
        request_count=100.0,
        error_count=errors,
        error_rate=errors / 100.0,
        p95_latency_ms=20.0 if phase == "BASELINE" else 40.0,
        runtime_health=1.0,
        sample_count=3,
    )


def _patch_live_fault_projection_inputs(
    monkeypatch: pytest.MonkeyPatch,
    roots: E2EV6PrivateRoots,
) -> None:
    def collect_source_batch(*_: object, **kwargs: object) -> object:
        run_root = kwargs["run_root"]
        assert isinstance(run_root, Path)
        counts = {"METRICS": 5, "LOGS": 6, "TRACES": 4}
        write_private_json(
            run_root / "source-results.json",
            {
                "schema_version": "live-e2e.source-results.v6",
                "results": [
                    {"source": source, "status": "AVAILABLE"}
                    for source in ("METRICS", "LOGS", "TRACES")
                ],
                "source_counts": counts,
                "invalid_ref_count": 0,
                "all_refs_resolve": True,
                "source_results_sha256": "b" * 64,
                "combined_resolver_sha256": "a" * 64,
            },
            create_once=True,
        )
        results = tuple(
            SimpleNamespace(
                source=source,
                status=SimpleNamespace(value="AVAILABLE"),
                target_record_count=counts[source],
                invalid_ref_count=0,
            )
            for source in ("METRICS", "LOGS", "TRACES")
        )
        return SimpleNamespace(
            source_results=results,
            source_counts=counts,
            invalid_ref_count=0,
        )

    snapshots = iter(
        (
            {
                "checkout": (100.0, 1.0, 20.0),
                "currency": (100.0, 1.0, 20.0),
                "frontend": (100.0, 1.0, 20.0),
            },
            {
                "checkout": (100.0, 30.0, 40.0),
                "currency": (100.0, 20.0, 35.0),
                "frontend": (100.0, 10.0, 30.0),
            },
        )
    )
    observed_at = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)

    def broad_logs(*_: object, **__: object) -> tuple[LiveLogObservation, ...]:
        return tuple(
            LiveLogObservation(
                observed_at=observed_at,
                service_name=service,
                severity="ERROR",
                body="observed request error",
            )
            for service in ("checkout", "currency", "frontend")
        )

    def seal_resolver(
        _: object,
        *,
        metrics: tuple[object, ...],
        logs: tuple[LiveLogObservation, ...],
        traces: tuple[LiveTraceObservation, ...],
        **__: object,
    ) -> tuple[
        tuple[object, ...],
        tuple[object, ...],
        tuple[object, ...],
        frozenset[str],
    ]:
        bound_metrics = tuple(
            item.model_copy(update={"evidence_ref": f"metric:{index:04d}"})
            for index, item in enumerate(metrics, 1)
        )
        bound_logs = tuple(
            item.model_copy(update={"evidence_ref": f"log:{index:04d}"})
            for index, item in enumerate(logs, 1)
        )
        refs = frozenset(
            [item.evidence_ref for item in bound_metrics]
            + [item.evidence_ref for item in bound_logs]
        )
        return bound_metrics, bound_logs, traces, refs  # type: ignore[return-value]

    monkeypatch.setattr(e2e_v3, "collect_ordered_source_batch", collect_source_batch)
    monkeypatch.setattr(
        e2e_v3,
        "_capture_sli_window",
        lambda *_, phase: _sli_window(phase),
    )
    monkeypatch.setattr(e2e_v3, "fault_impact_passed", lambda *_: True)
    monkeypatch.setattr(
        e2e_v3,
        "_broad_metric_snapshot",
        lambda *_, **__: next(snapshots),
    )
    monkeypatch.setattr(e2e_v3, "_capture_broad_logs", broad_logs)
    monkeypatch.setattr(e2e_v3, "_capture_broad_traces", lambda *_, **__: ())
    monkeypatch.setattr(e2e_v3, "_seal_model_evidence_resolver", seal_resolver)
    monkeypatch.setattr(
        e2e_v3,
        "_write_model_evidence_index",
        lambda *_args, **_kwargs: None,
    )


def test_v6_post_projection_evidence_failure_preserves_unclassified_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "post-projection-runtime")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = None
    provider = FakeProvider()
    _patch_live_fault_projection_inputs(monkeypatch, roots)

    def fail_context_evidence(**_: object) -> object:
        raise RuntimeError("injected context evidence write failure")

    monkeypatch.setattr(
        e2e_v3,
        "write_fault_time_context_evidence",
        fail_context_evidence,
    )

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    assert terminal["verdict"] == "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["failed_stage"] == "POST_PROJECTION_RUNTIME"
    assert terminal["last_completed_stage"] == "MULTISERVICE_PROJECTION_COMPLETED"
    assert terminal["failure_code"] == "UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["provider_calls"] == 1
    assert terminal["model_calls"] == 0
    assert terminal["a0_context_builder_calls"] == 1
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert build_expected_public_result(config, terminal)["verdict"] == (
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )


def test_v6_live_provider_failure_preserves_unclassified_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "live-diagnosis-runtime")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = None
    provider = FakeProvider()
    _patch_live_fault_projection_inputs(monkeypatch, roots)

    def fail_live_diagnosis(current_provider: FakeProvider, context: object) -> object:
        current_provider.diagnose(context)
        raise RuntimeError("injected live Provider transport failure")

    monkeypatch.setattr(e2e_v3, "_diagnosis_from_initial", fail_live_diagnosis)

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    assert terminal["verdict"] == "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["failed_stage"] == "LIVE_DIAGNOSIS_RUNTIME"
    assert terminal["last_completed_stage"] == "MULTISERVICE_PROJECTION_COMPLETED"
    assert terminal["failure_code"] == "UNCLASSIFIED_RUNTIME_FAILURE"
    assert terminal["provider_calls"] == 2
    assert terminal["model_calls"] == 0
    assert terminal["fault_injections"] == 1
    assert terminal["forward_mutations"] == 0
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert build_expected_public_result(config, terminal)["verdict"] == (
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )


def test_v6_executes_exactly_one_fault_time_context_and_binds_provider_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    roots = E2EV6PrivateRoots(tmp_path / "exactly-once")
    _prepare_approved_v6(config, roots)
    FakeEnvironment.fail_at = None
    provider = FakeProvider()
    builder_calls = 0
    original_builder = e2e_v3.build_fault_time_a0_context

    def builder_spy(**kwargs: object) -> object:
        nonlocal builder_calls
        builder_calls += 1
        return original_builder(**kwargs)  # type: ignore[arg-type]

    def collect_source_batch(*_: object, **kwargs: object) -> object:
        run_root = kwargs["run_root"]
        assert isinstance(run_root, Path)
        counts = {"METRICS": 5, "LOGS": 6, "TRACES": 4}
        write_private_json(
            run_root / "source-results.json",
            {
                "schema_version": "live-e2e.source-results.v6",
                "results": [
                    {"source": source, "status": "AVAILABLE"}
                    for source in ("METRICS", "LOGS", "TRACES")
                ],
                "source_counts": counts,
                "invalid_ref_count": 0,
                "all_refs_resolve": True,
                "source_results_sha256": "b" * 64,
                "combined_resolver_sha256": "a" * 64,
            },
            create_once=True,
        )
        results = tuple(
            SimpleNamespace(
                source=source,
                status=SimpleNamespace(value="AVAILABLE"),
                target_record_count=counts[source],
                invalid_ref_count=0,
            )
            for source in ("METRICS", "LOGS", "TRACES")
        )
        return SimpleNamespace(
            source_results=results,
            source_counts=counts,
            invalid_ref_count=0,
        )

    snapshots = iter(
        (
            {
                "checkout": (100.0, 1.0, 20.0),
                "currency": (100.0, 1.0, 20.0),
                "frontend": (100.0, 1.0, 20.0),
            },
            {
                "checkout": (100.0, 30.0, 40.0),
                "currency": (100.0, 20.0, 35.0),
                "frontend": (100.0, 10.0, 30.0),
            },
        )
    )

    def broad_metrics(*_: object, **__: object) -> object:
        return next(snapshots)

    observed_at = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)

    def broad_logs(*_: object, **__: object) -> tuple[LiveLogObservation, ...]:
        return tuple(
            LiveLogObservation(
                observed_at=observed_at,
                service_name=service,
                severity="ERROR",
                body="observed request error",
            )
            for service in ("checkout", "currency", "frontend")
        )

    def seal_resolver(
        _: object,
        *,
        metrics: tuple[object, ...],
        logs: tuple[LiveLogObservation, ...],
        traces: tuple[LiveTraceObservation, ...],
        **__: object,
    ) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...], frozenset[str]]:
        bound_metrics = tuple(
            item.model_copy(update={"evidence_ref": f"metric:{index:04d}"})
            for index, item in enumerate(metrics, 1)
        )
        bound_logs = tuple(
            item.model_copy(update={"evidence_ref": f"log:{index:04d}"})
            for index, item in enumerate(logs, 1)
        )
        refs = frozenset(
            [item.evidence_ref for item in bound_metrics]
            + [item.evidence_ref for item in bound_logs]
        )
        return bound_metrics, bound_logs, traces, refs  # type: ignore[return-value]

    def diagnosis_from_provider(current_provider: FakeProvider, context: object) -> DiagnosisResult:
        current_provider.diagnose(context)
        return DiagnosisResult(
            terminal="COMPLETED",
            root_service="frontend",
            root_entity_ref="apm|apm.service|frontend",
            fault_type_raw="UNKNOWN",
            fault_class="UNKNOWN",
            confidence=0.1,
            evidence_refs=("metric:0001", "log:0001"),
            evidence_source_types=("METRICS", "LOGS"),
            summary="typed fake diagnosis that intentionally fails the Gate",
            semantic_model_calls=1,
            specialist_calls=0,
            fusion_calls=0,
            provider_attempts=1,
            transport_retries=0,
            usage_tokens=1,
        )

    monkeypatch.setattr(e2e_v3, "build_fault_time_a0_context", builder_spy)
    monkeypatch.setattr(e2e_v3, "collect_ordered_source_batch", collect_source_batch)
    monkeypatch.setattr(e2e_v3, "_capture_sli_window", lambda *_, phase: _sli_window(phase))
    monkeypatch.setattr(e2e_v3, "fault_impact_passed", lambda *_: True)
    monkeypatch.setattr(e2e_v3, "_broad_metric_snapshot", broad_metrics)
    monkeypatch.setattr(e2e_v3, "_capture_broad_logs", broad_logs)
    monkeypatch.setattr(e2e_v3, "_capture_broad_traces", lambda *_, **__: ())
    monkeypatch.setattr(e2e_v3, "_seal_model_evidence_resolver", seal_resolver)
    monkeypatch.setattr(e2e_v3, "_write_model_evidence_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(e2e_v3, "_diagnosis_from_initial", diagnosis_from_provider)
    monkeypatch.setattr(
        e2e_v3,
        "evaluate_diagnosis_gate",
        lambda *_: SimpleNamespace(passed=False),
    )

    terminal = run_invocation_b(
        config,
        roots,
        provider_factory=lambda _: provider,
        environment_factory=FakeEnvironment,
        controller_factory=_controller,
        worktree_verifier=_fake_worktree,
        sleep=lambda _: None,
        public_writer=lambda *_: (),
    )

    context_path = roots.invocation_b / "fault-time-a0-context.json"
    metadata_path = roots.invocation_b / "fault-time-a0-context-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert terminal["verdict"] == "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
    assert builder_calls == 1
    assert terminal["a0_context_builder_calls"] == 1
    assert context_path.is_file()
    assert metadata_path.is_file()
    assert metadata["builder_call_count"] == 1
    assert metadata["context_sha256"] == provider.live_context_sha256
    assert metadata["provider_live_context_sha256"] == provider.live_context_sha256
    assert terminal["fault_time_a0_context_sha256"] == provider.live_context_sha256
    assert terminal["provider_live_context_sha256"] == provider.live_context_sha256
    assert terminal["provider_calls"] == 2
    assert terminal["model_calls"] == 1
    assert terminal["fault_injections"] == 1
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert provider.live_context is not None
    with pytest.raises(FileExistsError, match="create-once"):
        write_fault_time_context_evidence(
            private_root=roots.root,
            invocation_b_root=roots.invocation_b,
            context=provider.live_context,
            terminal=terminal,
        )
    build_expected_public_result(config, terminal)
    assert builder_calls == 1
