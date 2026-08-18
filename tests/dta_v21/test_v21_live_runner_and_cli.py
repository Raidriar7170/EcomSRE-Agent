from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.tool_contracts import HealthState, MetricKind, RuntimeState
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_cli import (
    _read_model,
    _verify_exact_head_github_actions,
    _verify_private_protocol_freeze,
    run_execute,
    run_verify,
)
from ecomsre.dta_v2.v21.live_contracts import (
    LIVE_CAMPAIGN_ORDER_V21,
    LiveCampaignClosureV21,
    LiveReadinessV21,
    LiveScenarioV21,
    load_live_demo_config_v21,
)
from ecomsre.dta_v2.v21.live_execution import LiveMasterAuthorizationV21
from ecomsre.dta_v2.v21.live_owned import OwnedLiveAttemptV21
from ecomsre.dta_v2.v21.live_protocol import (
    load_ad_cpu_resource_recovery_protocol_v1,
)
from ecomsre.dta_v2.v21.live_reporting import (
    PublicAdRecoveryWindowV21,
    PublicServiceRecoveryWindowV21,
    _contains_public_leak,
    _load_prior_failures,
)
from ecomsre.dta_v2.v21.live_runner import (
    LiveCampaignBlockedV21,
    LiveExecutionLeaseV21,
    _build_attempt_closure,
    run_owned_live_campaign_v21,
    run_owned_live_attempt_v21,
)
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from scripts.ci.verify_dta_v21_pr_f_protocol import (
    _verify_pr_f_targets_do_not_reach_held_out_execution,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _config():
    return load_live_demo_config_v21(
        REPO_ROOT / "config/dta-v21/live/live-demo.v1.json"
    )


def _protocol():
    return load_ad_cpu_resource_recovery_protocol_v1(
        REPO_ROOT / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )


def _readiness(
    *, code_head: str, master: LiveMasterAuthorizationV21
) -> LiveReadinessV21:
    config = _config()
    return LiveReadinessV21.build(
        terminal="DTA_V21_PR_F_PRELIVE_READY",
        readiness_attempt_id="readiness-0001",
        code_head=code_head,
        exact_head_ci_success=True,
        exact_head_ci_run_id=123,
        exact_head_ci_run_url="https://github.com/example/repo/actions/runs/123",
        branch="codex/dta-v21-p0-pr-f-live-closeout",
        origin_main_is_ancestor=True,
        protocol_sha256=_protocol().protocol_sha256,
        live_config_sha256=config.config_sha256,
        planner_identity_sha256=config.planner_identity_sha256,
        provider_model=config.provider_model,
        pr_e_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        docker_boundary="LOCAL_UNIX_DOCKER",
        resolved_compose_sha256="1" * 64,
        baseline_flag_document_sha256="2" * 64,
        owned_resource_collisions=0,
        required_ports_available=True,
        cleanup_readiness="OWNED_SCOPE_ADMITTED",
        private_permissions="0700_DIRECTORIES_0600_FILES",
        master_authorization_sha256=master.authorization_sha256,
    )


def test_live_verify_is_safe_and_pending_without_public_live_evidence(
    tmp_path: Path,
) -> None:
    target = tmp_path / "config/dta-v21/live/live-demo.v1.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(
        (REPO_ROOT / "config/dta-v21/live/live-demo.v1.json").read_bytes()
    )

    assert run_verify(repository_root=tmp_path) == "DTA_V21_PR_F_LIVE_REPORT_PENDING"


def test_live_verify_rejects_a_malformed_public_report(tmp_path: Path) -> None:
    report = tmp_path / "docs/results/dta-v21-live-demo.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"terminal": "PASS"}), encoding="utf-8")

    with pytest.raises(ValueError):
        run_verify(repository_root=tmp_path)


def test_live_verify_rejects_partial_public_outputs(tmp_path: Path) -> None:
    target = tmp_path / "config/dta-v21/live/live-demo.v1.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(
        (REPO_ROOT / "config/dta-v21/live/live-demo.v1.json").read_bytes()
    )
    partial = tmp_path / "docs/results/dta-v21-live-demo.md"
    partial.parent.mkdir(parents=True)
    partial.write_text("partial\n", encoding="utf-8")

    with pytest.raises(ValueError, match="partial"):
        run_verify(repository_root=tmp_path)


@pytest.mark.parametrize(
    "value",
    (
        "private path /Users/example/.ecomsre/private/run.json",
        "Authorization: Bearer secret-value",
        "ECOMSRE_LLM_API_KEY=secret",
        "sk-example12345678",
    ),
)
def test_public_leakage_scanner_covers_markdown_and_secret_shapes(value: str) -> None:
    assert _contains_public_leak(value)


def test_private_protocol_freeze_is_revalidated_and_deletion_fails(
    tmp_path: Path,
) -> None:
    prf = tmp_path / "pr-f"
    prf.mkdir()
    payload = {
        "schema_version": "dta-v21.pr-f-protocol-freeze.v1",
        "protocol_commit": "d20eef2dd644269b975fff22d9a8c03d437878ba",
        "protocol_sha256": _protocol().protocol_sha256,
        "pr_e_claim": "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
    }
    path = prf / "protocol-freeze.json"
    path.write_text(
        json.dumps({**payload, "record_sha256": semantic_sha256(payload)}),
        encoding="utf-8",
    )
    _verify_private_protocol_freeze(
        private_root=tmp_path, protocol_sha256=_protocol().protocol_sha256
    )
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        _verify_private_protocol_freeze(
            private_root=tmp_path, protocol_sha256=_protocol().protocol_sha256
        )


def test_exact_head_ci_requires_a_successful_github_pr_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "a" * 40

    def successful(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            stdout=json.dumps(
                [
                    {
                        "databaseId": 123,
                        "headSha": head,
                        "status": "completed",
                        "conclusion": "success",
                        "url": "https://github.com/example/repo/actions/runs/123",
                        "event": "pull_request",
                    }
                ]
            ),
        )

    monkeypatch.setattr("ecomsre.dta_v2.v21.live_cli._COMMAND_RUNNER.run", successful)
    assert _verify_exact_head_github_actions(tmp_path, head=head)["run_id"] == 123

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_cli._COMMAND_RUNNER.run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="[]"),
    )
    with pytest.raises(ValueError, match="no successful PR run"):
        _verify_exact_head_github_actions(tmp_path, head=head)


def test_public_failure_summary_includes_preserved_clean_failed_attempt(
    tmp_path: Path,
) -> None:
    code_head = "deadbeefdead" + "0" * 28
    master = LiveMasterAuthorizationV21.build(
        issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
    )
    readiness = _readiness(code_head=code_head, master=master)
    readiness_path = tmp_path / "readiness" / code_head / "readiness.json"
    readiness_path.parent.mkdir(parents=True)
    readiness_path.write_text(readiness.model_dump_json(), encoding="utf-8")
    attempt = tmp_path / "attempts" / "dta-v21-prf-02-ad-cpu-deadbeefdead"
    attempt.mkdir(parents=True)
    claim = {
        "schema_version": "dta-v21.live-attempt-claim.v1",
        "attempt_id": attempt.name,
        "scenario": "AD_CPU_SATURATION",
        "ordinal": 2,
        "code_head": code_head,
        "master_authorization_sha256": master.authorization_sha256,
        "protocol_sha256": _protocol().protocol_sha256,
        "live_config_sha256": _config().config_sha256,
        "readiness_sha256": readiness.readiness_sha256,
    }
    failure = {
        "schema_version": "dta-v21.live-attempt-failure.v1",
        "attempt_id": attempt.name,
        "scenario": "AD_CPU_SATURATION",
        "stage": "AGENT",
        "terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
        "baseline_restored": True,
        "cleanup": {
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        },
        "raw_error_retained": False,
    }
    (attempt / "attempt-claim.json").write_text(json.dumps(claim), encoding="utf-8")
    (attempt / "attempt-terminal.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )
    prior_head = "feedfacefeed" + "0" * 28
    prior_readiness = _readiness(code_head=prior_head, master=master)
    prior_readiness_path = tmp_path / "readiness" / prior_head / "readiness.json"
    prior_readiness_path.parent.mkdir(parents=True)
    prior_readiness_path.write_text(prior_readiness.model_dump_json(), encoding="utf-8")
    suffixes = {
        LiveScenarioV21.NO_FAULT: "dta-v21-prf-01-no-fault",
        LiveScenarioV21.AD_CPU_SATURATION: "dta-v21-prf-02-ad-cpu",
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: ("dta-v21-prf-03-email-unavailable"),
        LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
            "dta-v21-prf-04-product-catalog-unavailable"
        ),
    }
    prior_closures = []
    for prior_ordinal, prior_scenario in enumerate(LIVE_CAMPAIGN_ORDER_V21, start=1):
        prior_attempt = (
            tmp_path / "attempts" / f"{suffixes[prior_scenario]}-{prior_head[:12]}"
        )
        prior_attempt.mkdir(parents=True)
        prior_claim = {
            "schema_version": "dta-v21.live-attempt-claim.v1",
            "attempt_id": prior_attempt.name,
            "scenario": prior_scenario.value,
            "ordinal": prior_ordinal,
            "code_head": prior_head,
            "master_authorization_sha256": master.authorization_sha256,
            "protocol_sha256": _protocol().protocol_sha256,
            "live_config_sha256": _config().config_sha256,
            "readiness_sha256": prior_readiness.readiness_sha256,
        }
        (prior_attempt / "attempt-claim.json").write_text(
            json.dumps(prior_claim), encoding="utf-8"
        )
        positive = prior_scenario is not LiveScenarioV21.NO_FAULT
        prior_closure = _build_attempt_closure(
            scenario=prior_scenario,
            attempt_id=prior_attempt.name,
            run_id="a" * 32,
            planner_identity_sha256=_config().planner_identity_sha256,
            readiness_sha256=prior_readiness.readiness_sha256,
            environment_admission_sha256="2" * 64,
            baseline_evidence_sha256="3" * 64,
            fault_impact_sha256="4" * 64,
            agent_result_sha256="5" * 64,
            provider_attempted_calls=1,
            operational_admission_sha256="6" * 64,
            run_authorization_sha256="7" * 64 if positive else None,
            receipt_sha256="8" * 64 if positive else None,
            recovery_result_sha256="9" * 64 if positive else None,
            cleanup={
                "baseline_restored": True,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
                "verdict": "CLEAN",
            },
        )
        (prior_attempt / "attempt-terminal.json").write_text(
            prior_closure.model_dump_json(), encoding="utf-8"
        )
        prior_closures.append(prior_closure)
    prior_campaign_payload = {
        "schema_version": "dta-v21.live-campaign-closure.v1",
        "terminal": "DTA_V21_PR_F_LIVE_PORTFOLIO_PASS",
        "code_head": prior_head,
        "protocol_sha256": _protocol().protocol_sha256,
        "live_config_sha256": _config().config_sha256,
        "planner_identity_sha256": _config().planner_identity_sha256,
        "readiness_sha256": prior_readiness.readiness_sha256,
        "attempts": tuple(prior_closures),
        "unsafe_proposal_attempts": 0,
        "arbitrary_shell_attempts": 0,
        "non_owned_changes": 0,
        "all_baselines_restored": True,
        "all_cleanup_clean": True,
    }
    prior_campaign = LiveCampaignClosureV21.model_validate(
        {
            **prior_campaign_payload,
            "campaign_sha256": semantic_sha256(
                to_jsonable_python(prior_campaign_payload)
            ),
        }
    )
    prior_campaign_root = tmp_path / "campaigns" / prior_head
    prior_campaign_root.mkdir(parents=True)
    (prior_campaign_root / "campaign-closure.json").write_text(
        prior_campaign.model_dump_json(), encoding="utf-8"
    )

    failures = _load_prior_failures(
        prf_private_root=tmp_path,
        successful_attempt_ids=set(),
        protocol_sha256=_protocol().protocol_sha256,
        live_config_sha256=_config().config_sha256,
        master_authorization_sha256=master.authorization_sha256,
    )
    assert len(failures) == 1
    assert failures[0].terminal == "BLOCKED_DTA_V21_PRF_SAFETY"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("baseline_restored", False),
        ("owned_containers", 1),
        ("owned_networks", 1),
        ("owned_volumes", 1),
        ("non_owned_resources_changed", True),
    ),
)
def test_prior_failure_summary_rejects_false_clean_cleanup(
    tmp_path: Path, field: str, value: object
) -> None:
    code_head = "deadbeefdead" + "0" * 28
    master = LiveMasterAuthorizationV21.build(
        issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
    )
    readiness = _readiness(code_head=code_head, master=master)
    readiness_path = tmp_path / "readiness" / code_head / "readiness.json"
    readiness_path.parent.mkdir(parents=True)
    readiness_path.write_text(readiness.model_dump_json(), encoding="utf-8")
    attempt = tmp_path / "attempts" / "dta-v21-prf-02-ad-cpu-deadbeefdead"
    attempt.mkdir(parents=True)
    claim = {
        "schema_version": "dta-v21.live-attempt-claim.v1",
        "attempt_id": attempt.name,
        "scenario": "AD_CPU_SATURATION",
        "ordinal": 2,
        "code_head": code_head,
        "master_authorization_sha256": master.authorization_sha256,
        "protocol_sha256": _protocol().protocol_sha256,
        "live_config_sha256": _config().config_sha256,
        "readiness_sha256": readiness.readiness_sha256,
    }
    cleanup = {
        "baseline_restored": True,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "CLEAN",
    }
    cleanup[field] = value
    failure = {
        "schema_version": "dta-v21.live-attempt-failure.v1",
        "attempt_id": attempt.name,
        "scenario": "AD_CPU_SATURATION",
        "stage": "AGENT",
        "terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
        "baseline_restored": True,
        "cleanup": cleanup,
        "raw_error_retained": False,
    }
    (attempt / "attempt-claim.json").write_text(json.dumps(claim), encoding="utf-8")
    (attempt / "attempt-terminal.json").write_text(
        json.dumps(failure), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="incomplete or unsafe"):
        _load_prior_failures(
            prf_private_root=tmp_path,
            successful_attempt_ids=set(),
            protocol_sha256=_protocol().protocol_sha256,
            live_config_sha256=_config().config_sha256,
            master_authorization_sha256=master.authorization_sha256,
        )


def test_typed_private_json_round_trips_strict_enums(tmp_path: Path) -> None:
    master = LiveMasterAuthorizationV21.build(
        issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
    )
    target = tmp_path / "master.json"
    target.write_text(master.model_dump_json(), encoding="utf-8")

    assert _read_model(target, LiveMasterAuthorizationV21) == master


def test_public_window_models_recheck_recovery_observations() -> None:
    with pytest.raises(ValueError, match="frozen threshold"):
        PublicAdRecoveryWindowV21(
            ordinal=1,
            cpu_p95_percent="11.163",
            capacity_ratio="0.1",
            business_latency_p95_ms="3.5",
            business_impact_observed=False,
            service_health_passed=True,
            endpoint_reachable=True,
            window_sha256="a" * 64,
        )

    with pytest.raises(ValueError, match="error threshold"):
        PublicServiceRecoveryWindowV21(
            ordinal=1,
            business_anchor_service="checkout",
            baseline_business_error_rate="0",
            recovery_error_rate_threshold="0.02",
            business_error_rate="0.03",
            request_support="1",
            first_error_span_count=0,
            business_impact_observed=False,
            service_running=True,
            service_health_passed=True,
            endpoint_reachable=True,
            window_sha256="b" * 64,
        )


def test_live_execute_requires_the_exact_standing_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DTA_V21_LIVE_EXECUTE", raising=False)

    with pytest.raises(ValueError, match="confirmation"):
        run_execute(
            repository_root=tmp_path,
            private_root=tmp_path,
            provider_env_path=tmp_path / "provider.env",
        )


@pytest.mark.parametrize(
    "dangerous_makefile",
    (
        "dta-v21-live-demo: dta-v21-held-out-execute\n\t@true\n",
        "dta-v21-demo: dta-v21-held-out-score\n\t@true\n",
        "dta-v21-verify:\n\t$(DTA_V21_HELD_OUT_CLI) execute\n",
        ("dta-v21-live-demo:\n\tpython -m ecomsre.dta_v2.v21.held_out.cli execute\n"),
        ("dta-v21-verify: safe \\\n\tindirect\nindirect: dta-v21-held-out-execute\n"),
    ),
)
def test_all_live_and_public_surfaces_reject_held_out_execution_reachability(
    dangerous_makefile: str,
) -> None:
    with pytest.raises(ValueError, match="held-out execution or scoring"):
        _verify_pr_f_targets_do_not_reach_held_out_execution(dangerous_makefile)


def test_no_fault_restoration_is_read_only() -> None:
    class Capture:
        active_condition: str | None = "attempt"
        case_started_at: datetime | None = datetime.now(timezone.utc)

        def verify_baseline(self) -> None:
            return None

        def _flags(self):
            raise AssertionError("no-fault restoration attempted a flag write")

        def _service(self, _service: str):
            raise AssertionError("no-fault restoration attempted a service write")

    owned = object.__new__(OwnedLiveAttemptV21)
    owned.scenario = _config().require_scenario(LiveScenarioV21.NO_FAULT)
    owned.capture = cast(Any, Capture())
    owned._concurrency_guard = lambda: None
    owned._unrelated_owned_drift_detected = False
    owned._baseline_state_sha256 = "a" * 64
    owned._verify_exact_baseline_read_only = lambda: None  # type: ignore[method-assign]
    owned._require_non_owned_unchanged = lambda: None  # type: ignore[method-assign]
    owned._state_digest = lambda: "a" * 64  # type: ignore[method-assign]

    assert owned.restore_baseline_idempotently() is True
    assert owned.capture.active_condition is None
    assert owned.capture.case_started_at is None


def test_service_recovery_threshold_uses_canonical_decimal_arithmetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned = object.__new__(OwnedLiveAttemptV21)
    owned.scenario = _config().require_scenario(
        LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE
    )
    owned.config = _config()
    owned.run_id = "a" * 32
    owned.attempt_id = "attempt-email"
    owned._baseline_service_error_rates = (0.1, 0.1)
    owned._target_identity = "owned-email"
    owned._metrics = lambda **_values: {  # type: ignore[method-assign]
        MetricKind.ERROR_RATE: SimpleNamespace(value=0.1),
        MetricKind.REQUEST_SUPPORT: SimpleNamespace(value=1.0),
    }
    owned._traces = lambda **_values: ()  # type: ignore[method-assign]
    cast(Any, owned)._runtime = lambda _service: SimpleNamespace(
        state=RuntimeState.RUNNING,
        health=HealthState.HEALTHY,
    )
    owned._require_unrelated_owned_services_unchanged = lambda: None  # type: ignore[method-assign]
    cast(Any, owned)._target_identity_value = lambda _service: "owned-email"
    owned._probe_frontend = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr("ecomsre.dta_v2.v21.live_owned.time.sleep", lambda _s: None)

    window = owned._service_recovery_window(
        ordinal=1,
        started_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )

    assert window.window_ended_at - window.window_started_at == timedelta(seconds=20)
    assert window.baseline_business_error_rate == "0.1"
    assert window.recovery_error_rate_threshold == "0.15"


def test_start_failure_still_attempts_restoration_and_owned_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class FailingOwnedAttempt:
        def __init__(self, **_values: object) -> None:
            self.run_id = "a" * 32

        def admit_environment(self) -> None:
            calls.append("admit")

        def start(self) -> None:
            calls.append("start")
            raise RuntimeError("mutation may have begun")

        def restore_baseline_idempotently(self) -> bool:
            calls.append("restore")
            return True

        def cleanup(self, *, baseline_restored: bool) -> dict[str, object]:
            calls.append(f"cleanup:{baseline_restored}")
            return {
                "baseline_restored": True,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
                "verdict": "CLEAN",
            }

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.OwnedLiveAttemptV21",
        FailingOwnedAttempt,
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)

    with pytest.raises(LiveCampaignBlockedV21, match="BLOCKED_DTA_V21_PRF_SAFETY"):
        with LiveExecutionLeaseV21(private) as execution_lease:
            run_owned_live_attempt_v21(
                repository_root=REPO_ROOT,
                prf_private_root=private,
                provider_env_path=tmp_path / "provider.env",
                config=_config(),
                scenario=LiveScenarioV21.NO_FAULT,
                registry=load_default_runbook_registry(REPO_ROOT),
                protocol=_protocol(),
                master_authorization=LiveMasterAuthorizationV21.build(
                    issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
                ),
                readiness=_readiness(
                    code_head="f" * 40,
                    master=LiveMasterAuthorizationV21.build(
                        issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
                    ),
                ),
                code_head="f" * 40,
                execution_lease=execution_lease,
            )

    assert calls == ["admit", "start", "restore", "cleanup:True"]
    terminal = json.loads(
        (
            private
            / "attempts/dta-v21-prf-01-no-fault-ffffffffffff/attempt-terminal.json"
        ).read_text(encoding="utf-8")
    )
    assert terminal["stage"] == "START"
    assert terminal["baseline_restored"] is True
    assert terminal["cleanup"]["verdict"] == "CLEAN"


def test_live_execution_lease_rejects_a_concurrent_campaign(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)

    with LiveExecutionLeaseV21(private):
        with pytest.raises(LiveCampaignBlockedV21, match="BLOCKED_DTA_V21_PRF_SAFETY"):
            with LiveExecutionLeaseV21(private):
                pass


def test_campaign_closures_are_immutable_and_code_head_qualified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    master = LiveMasterAuthorizationV21.build(
        issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
    )
    config = _config()

    def fake_attempt(**values: object):
        scenario = cast(LiveScenarioV21, values["scenario"])
        code_head = cast(str, values["code_head"])
        positive = scenario is not LiveScenarioV21.NO_FAULT
        suffix = {
            LiveScenarioV21.NO_FAULT: "dta-v21-prf-01-no-fault",
            LiveScenarioV21.AD_CPU_SATURATION: "dta-v21-prf-02-ad-cpu",
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
                "dta-v21-prf-03-email-unavailable"
            ),
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                "dta-v21-prf-04-product-catalog-unavailable"
            ),
        }[scenario]
        return _build_attempt_closure(
            scenario=scenario,
            attempt_id=f"{suffix}-{code_head[:12]}",
            run_id="a" * 32,
            planner_identity_sha256=config.planner_identity_sha256,
            readiness_sha256=cast(
                LiveReadinessV21, values["readiness"]
            ).readiness_sha256,
            environment_admission_sha256="1" * 64,
            baseline_evidence_sha256="2" * 64,
            fault_impact_sha256="3" * 64,
            agent_result_sha256="4" * 64,
            provider_attempted_calls=1,
            operational_admission_sha256="5" * 64,
            run_authorization_sha256="6" * 64 if positive else None,
            receipt_sha256="7" * 64 if positive else None,
            recovery_result_sha256="8" * 64 if positive else None,
            cleanup={
                "baseline_restored": True,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
                "verdict": "CLEAN",
            },
        )

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.run_owned_live_attempt_v21", fake_attempt
    )
    for code_head in ("a" * 40, "b" * 40):
        run_owned_live_campaign_v21(
            repository_root=REPO_ROOT,
            prf_private_root=private,
            provider_env_path=tmp_path / "provider.env",
            config=config,
            registry=load_default_runbook_registry(REPO_ROOT),
            protocol=_protocol(),
            master_authorization=master,
            readiness=_readiness(code_head=code_head, master=master),
            code_head=code_head,
        )

    assert (private / "campaigns" / ("a" * 40) / "campaign-closure.json").is_file()
    assert (private / "campaigns" / ("b" * 40) / "campaign-closure.json").is_file()
    assert not (private / "final/campaign-closure.json").exists()


def test_restoration_failure_still_attempts_cleanup_and_persists_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class RestoreFailingOwnedAttempt:
        def __init__(self, **_values: object) -> None:
            self.run_id = "a" * 32

        def admit_environment(self) -> None:
            calls.append("admit")

        def start(self) -> None:
            calls.append("start")
            raise RuntimeError("mutation may have begun")

        def restore_baseline_idempotently(self) -> bool:
            calls.append("restore")
            raise RuntimeError("restore failed")

        def cleanup(self, *, baseline_restored: bool) -> dict[str, object]:
            calls.append(f"cleanup:{baseline_restored}")
            return {
                "baseline_restored": False,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
                "verdict": "BLOCKED",
            }

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.OwnedLiveAttemptV21",
        RestoreFailingOwnedAttempt,
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    with pytest.raises(LiveCampaignBlockedV21, match="BLOCKED_DTA_V21_PRF_SAFETY"):
        with LiveExecutionLeaseV21(private) as execution_lease:
            run_owned_live_attempt_v21(
                repository_root=REPO_ROOT,
                prf_private_root=private,
                provider_env_path=tmp_path / "provider.env",
                config=_config(),
                scenario=LiveScenarioV21.NO_FAULT,
                registry=load_default_runbook_registry(REPO_ROOT),
                protocol=_protocol(),
                master_authorization=LiveMasterAuthorizationV21.build(
                    issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
                ),
                readiness=_readiness(
                    code_head="e" * 40,
                    master=LiveMasterAuthorizationV21.build(
                        issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
                    ),
                ),
                code_head="e" * 40,
                execution_lease=execution_lease,
            )

    assert calls == ["admit", "start", "restore", "cleanup:False"]
    terminal = json.loads(
        (
            private
            / "attempts/dta-v21-prf-01-no-fault-eeeeeeeeeeee/attempt-terminal.json"
        ).read_text(encoding="utf-8")
    )
    assert terminal["restoration_operation_failed"] is True
    assert terminal["cleanup"]["verdict"] == "BLOCKED"


def test_prestart_failure_records_baseline_preserved_clean_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class PrestartFailingOwnedAttempt:
        def __init__(self, **_values: object) -> None:
            self.run_id = "a" * 32

        def admit_environment(self) -> None:
            raise ValueError("provider configuration rejected before start")

        def cleanup_not_started(self) -> dict[str, object]:
            return {
                "schema_version": "dta-v21.live-cleanup-terminal.v1",
                "disposition": "NOT_STARTED_NO_OWNED_RESOURCES",
                "baseline_restored": True,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
                "verdict": "CLEAN",
            }

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_runner.OwnedLiveAttemptV21",
        PrestartFailingOwnedAttempt,
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    with pytest.raises(LiveCampaignBlockedV21):
        with LiveExecutionLeaseV21(private) as execution_lease:
            run_owned_live_attempt_v21(
                repository_root=REPO_ROOT,
                prf_private_root=private,
                provider_env_path=tmp_path / "provider.env",
                config=_config(),
                scenario=LiveScenarioV21.NO_FAULT,
                registry=load_default_runbook_registry(REPO_ROOT),
                protocol=_protocol(),
                master_authorization=LiveMasterAuthorizationV21.build(
                    issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
                ),
                readiness=_readiness(
                    code_head="d" * 40,
                    master=LiveMasterAuthorizationV21.build(
                        issued_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
                    ),
                ),
                code_head="d" * 40,
                execution_lease=execution_lease,
            )
    terminal = json.loads(
        (
            private
            / "attempts/dta-v21-prf-01-no-fault-dddddddddddd/attempt-terminal.json"
        ).read_text(encoding="utf-8")
    )
    assert terminal["baseline_restored"] is True
    assert terminal["cleanup"]["verdict"] == "CLEAN"
