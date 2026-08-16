from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.agent_provider import build_provider_identity
from ecomsre.dta_v2.capture_campaign import CaptureOperationFailure
from ecomsre.dta_v2.contracts import RunbookId, semantic_sha256
from ecomsre.dta_v2.live_controls import OwnedLiveControls
from ecomsre.dta_v2.live_contracts import (
    LiveAttemptClosure,
    LiveAttemptMode,
    LiveAttemptTerminal,
    LiveScenario,
    build_live_campaign_attempt_claim,
    build_pre_live_freeze,
    load_live_demo_config,
)
from ecomsre.dta_v2.live_owned import (
    OwnedLiveCampaign,
    OwnedLivePreflight,
    OwnedSandboxLiveLifecycle,
    _clean_semantic_manifest,
    build_frozen_provider_config,
)
from ecomsre.dta_v2.owned_capture import OwnedEmailController
from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.registry import load_runbook_registry
from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    RuntimeRecord,
    RuntimeState,
)
from ecomsre_live_sandbox.contracts import write_private_json

from test_admission_policy import master_authorization
from test_admission_policy import current_state as admitted_current_state
from test_pr_f_live_runner import FakeLifecycle, _no_fault_agent, _positive_agent, _run


ROOT = Path(__file__).resolve().parents[2]
FROZEN_MODEL = "gpt-5.4-2026-03-05"
_OWNED_EMAIL_IDENTITY = "e" * 64
_EMAIL_STARTED_BEFORE = "2026-08-16T08:30:00.000000000Z"
_EMAIL_STARTED_AFTER = "2026-08-16T08:31:00.000000000Z"


class _EmailRestartDocker:
    def __init__(self, started_at: tuple[str, ...]) -> None:
        self._started_at = iter(started_at)

    def _owned_container_identity(self, service: str) -> str:
        assert service == "email"
        return _OWNED_EMAIL_IDENTITY

    def _owned_container_started_at(self, service: str, identity: str) -> str:
        assert service == "email"
        assert identity == _OWNED_EMAIL_IDENTITY
        return next(self._started_at)

    def _runtime_for(self, service: str) -> RuntimeRecord:
        assert service == "email"
        return RuntimeRecord(
            logical_service="email",
            owned_container_present=True,
            state=RuntimeState.RUNNING,
            health=HealthState.HEALTHY,
            restart_count=0,
            exit_code=None,
            endpoint_probe_performed=True,
            endpoint_state=EndpointState.READY,
        )


def _email_restart_controller(
    started_at: tuple[str, ...],
) -> tuple[OwnedEmailController, list[str]]:
    backend = SimpleNamespace(
        config=SimpleNamespace(
            docker_endpoint="unix:///var/run/docker.sock",
            timeout_seconds=1.0,
        ),
        docker=_EmailRestartDocker(started_at),
    )
    controller = OwnedEmailController(backend)  # type: ignore[arg-type]
    posts: list[str] = []
    controller.client = SimpleNamespace(post=posts.append)  # type: ignore[assignment]
    return controller, posts


def _freeze():
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")
    registry = load_runbook_registry(ROOT / "config/dta-v2/runbooks")
    identity = build_provider_identity(FROZEN_MODEL)
    return build_pre_live_freeze(
        code_head="b" * 40,
        agent_identity_sha256=identity.identity_sha256,
        model_id=identity.model_id,
        prompt_sha256=identity.prompt_sha256,
        tool_schema_sha256=identity.tool_schema_sha256,
        diagnosis_schema_sha256=identity.diagnosis_schema_sha256,
        action_selection_schema_sha256=identity.action_selection_schema_sha256,
        action_proposal_schema_sha256=identity.action_proposal_schema_sha256,
        registry_sha256=registry.registry_sha256,
        candidate_filter_source_sha256="1" * 64,
        admission_source_sha256="2" * 64,
        authorization_source_sha256="3" * 64,
        executor_source_sha256="4" * 64,
        verifier_source_sha256="5" * 64,
        runner_source_sha256="6" * 64,
        reporting_schema_sha256="7" * 64,
        upstream_commit=config.upstream_commit,
        upstream_tag=config.upstream_tag,
        resolved_compose_sha256="8" * 64,
        image_authority_sha256="9" * 64,
        live_config=config,
    )


def test_provider_env_supplies_only_origin_and_key_while_model_stays_frozen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider.env"
    path.write_text(
        "ECOMSRE_LLM_BASE_URL=https://provider.example/v1\n"
        "ECOMSRE_LLM_API_KEY=private-test-key\n"
        "ECOMSRE_LLM_MODEL=gpt-5.4-mini-2026-03-17\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    provider_config = build_frozen_provider_config(path, freeze=_freeze())

    assert provider_config.base_url == "https://provider.example/v1"
    assert provider_config.api_key == "private-test-key"
    assert provider_config.model == FROZEN_MODEL
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_provider_env_permissions_and_frozen_identity_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider.env"
    path.write_text(
        "ECOMSRE_LLM_BASE_URL=https://provider.example/v1\n"
        "ECOMSRE_LLM_API_KEY=private-test-key\n"
        "ECOMSRE_LLM_MODEL=ignored-model\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o644)
    with pytest.raises(ValueError, match="0600"):
        build_frozen_provider_config(path, freeze=_freeze())

    os.chmod(path, 0o600)
    forged = _freeze().model_copy(update={"model_id": "other-model"})
    with pytest.raises(ValueError):
        build_frozen_provider_config(path, freeze=forged)


@pytest.mark.parametrize(
    "payload",
    (
        "ECOMSRE_LLM_BASE_URL=https://provider.example/v1\n"
        "ECOMSRE_LLM_API_KEY=$(unsafe)\n"
        "ECOMSRE_LLM_MODEL=model\n",
        "ECOMSRE_LLM_BASE_URL=https://provider.example/v1\n"
        "ECOMSRE_LLM_API_KEY=one\n"
        "ECOMSRE_LLM_API_KEY=two\n"
        "ECOMSRE_LLM_MODEL=model\n",
        "ECOMSRE_LLM_BASE_URL=https://provider.example/v1\n"
        "ECOMSRE_LLM_API_KEY=private-test-key\n"
        "ECOMSRE_LLM_MODEL=model\nEXTRA=value\n",
    ),
)
def test_dta_provider_env_parser_rejects_shell_duplicate_and_extra_keys(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "provider.env"
    path.write_text(payload, encoding="utf-8")
    os.chmod(path, 0o600)

    with pytest.raises(ValueError):
        load_private_provider_env(path)


def test_dta_provider_env_parser_accepts_exact_export_assignments(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider.env"
    path.write_text(
        "export ECOMSRE_LLM_BASE_URL=https://provider.example/v1\n"
        "export ECOMSRE_LLM_MODEL=ignored-by-frozen-config\n"
        "export ECOMSRE_LLM_API_KEY=private-test-key\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)

    values = load_private_provider_env(path)

    assert set(values) == {
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    }
    assert values["ECOMSRE_LLM_MODEL"] == "ignored-by-frozen-config"


def test_owned_lifecycle_surface_has_no_generic_command_or_mutation_entrypoint() -> None:
    public = {
        name
        for name in dir(OwnedSandboxLiveLifecycle)
        if not name.startswith("_")
    }

    assert "execute" not in public
    assert "run_command" not in public
    assert "mutate" not in public
    assert {
        "capture_baseline",
        "inject_fault",
        "run_agent",
        "controls",
        "capture_recovery_windows",
        "restore_baseline",
        "cleanup_owned",
    }.issubset(public)


def test_owned_email_restart_returns_safe_started_at_mutation_proof() -> None:
    controller, posts = _email_restart_controller(
        (_EMAIL_STARTED_BEFORE, _EMAIL_STARTED_AFTER)
    )

    proof = controller.restart()

    assert proof.logical_service == "email"
    assert proof.owned_container_identity_sha256 == semantic_sha256(
        {"owned_container_identity": _OWNED_EMAIL_IDENTITY}
    )
    assert proof.before_started_at == _EMAIL_STARTED_BEFORE
    assert proof.after_started_at == _EMAIL_STARTED_AFTER
    assert proof.proof_sha256 == semantic_sha256(
        proof.model_dump(mode="json", exclude={"proof_sha256"})
    )
    assert _OWNED_EMAIL_IDENTITY not in proof.model_dump_json()
    assert posts == [f"/containers/{_OWNED_EMAIL_IDENTITY}/restart?t=15"]


def test_owned_email_restart_rejects_unchanged_docker_started_at() -> None:
    controller, posts = _email_restart_controller(
        (_EMAIL_STARTED_BEFORE, _EMAIL_STARTED_BEFORE)
    )

    with pytest.raises(CaptureOperationFailure) as captured:
        controller.restart()

    assert captured.value.operation.value == "EMAIL_RESTART_NOT_OBSERVED"
    assert posts == [f"/containers/{_OWNED_EMAIL_IDENTITY}/restart?t=15"]


def test_owned_controls_bind_email_restart_proof_into_post_operation_state() -> None:
    registry = load_runbook_registry(ROOT / "config/dta-v2/runbooks")
    snapshot = admitted_current_state(registry, RunbookId.MITIGATE_MEMORY_LEAK)
    controller, _ = _email_restart_controller(
        (_EMAIL_STARTED_BEFORE, _EMAIL_STARTED_AFTER)
    )
    base_digest = semantic_sha256({"live_state": "email-flag-off"})
    controls = OwnedLiveControls(
        current_state=snapshot,
        flag_controller=SimpleNamespace(apply=lambda document: base_digest),
        baseline_flag_document={"flags": {"emailMemoryLeak": {"value": "off"}}},
        email_disabled_flag_document={
            "flags": {"emailMemoryLeak": {"value": "off"}}
        },
        recommendation_controller=SimpleNamespace(start=lambda: None),
        email_controller=controller,
        state_digest=lambda: base_digest,
        admitted_state_digest=base_digest,
        revalidate_before_write=lambda authorization, observed_at: None,
    )

    before = controls.state_digest()
    controls.restart_email_service()
    after = controls.state_digest()

    proof = controls.email_restart_mutation_proof
    assert proof is not None
    assert after == semantic_sha256(
        {
            "trusted_live_state_sha256": before,
            "email_restart_mutation_proof_sha256": proof.proof_sha256,
        }
    )
    assert after != before
    assert _OWNED_EMAIL_IDENTITY not in proof.model_dump_json()


def test_current_state_uses_semantic_compose_identity_without_weakening_ownership() -> None:
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")
    registry = load_runbook_registry(ROOT / "config/dta-v2/runbooks")
    scenario = next(
        item for item in config.scenarios if item.scenario is LiveScenario.PAYMENT
    )
    authority = SimpleNamespace(
        docker_context_sha256="1" * 64,
        daemon_identity_sha256="2" * 64,
        ownership_scope_sha256="3" * 64,
    )
    environment = SimpleNamespace(
        compose_project="ecomsre-live-sandbox-v1",
        sandbox_id="11111111-1111-1111-1111-111111111111",
    )
    baseline = {"checkoutService": {"enabled": True}}
    fault = {"checkoutService": {"enabled": False}}
    capture = SimpleNamespace(
        _bundle=lambda: SimpleNamespace(environment=environment),
        _baseline=lambda: baseline,
    )
    lifecycle = object.__new__(OwnedSandboxLiveLifecycle)
    lifecycle.preflight = SimpleNamespace(capture=capture)
    lifecycle.scenario = scenario
    lifecycle.registry = registry
    lifecycle._active_fault_document = fault
    lifecycle._utc_now = lambda: datetime(2026, 8, 16, tzinfo=timezone.utc)
    lifecycle._refresh_backend = lambda: SimpleNamespace(authority=authority)
    lifecycle._runtime = lambda service: RuntimeRecord(
        logical_service=service,
        owned_container_present=True,
        state=RuntimeState.RUNNING,
        health=HealthState.HEALTHY,
        restart_count=0,
        exit_code=None,
        endpoint_probe_performed=True,
        endpoint_state=EndpointState.READY,
    )
    lifecycle._require_flags = lambda: SimpleNamespace(
        verify=lambda document: semantic_sha256(document)
    )
    validated: list[object] = []

    def require_execution_snapshot(snapshot):
        validated.append(snapshot)
        return "4" * 64

    lifecycle._require_execution_snapshot = require_execution_snapshot

    snapshot = lifecycle.current_state(
        scenario=scenario,
        agent_result=_positive_agent(LiveScenario.PAYMENT),
        attempt_id="owned-payment-attempt",
    )

    assert snapshot.sandbox_identity == environment.compose_project
    assert snapshot.sandbox_identity != environment.sandbox_id
    assert snapshot.docker_context_identity == authority.docker_context_sha256
    assert snapshot.daemon_identity == authority.daemon_identity_sha256
    assert snapshot.ownership_digest == authority.ownership_scope_sha256
    assert validated == [snapshot]


def test_generic_caller_cannot_construct_owned_live_lifecycle(tmp_path: Path) -> None:
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")
    registry = load_runbook_registry(ROOT / "config/dta-v2/runbooks")
    with pytest.raises(TypeError, match="campaign-issued"):
        OwnedSandboxLiveLifecycle(
            claim=object(),  # type: ignore[arg-type]
            preflight=object(),  # type: ignore[arg-type]
            provider_env_path=tmp_path / "provider.env",
            freeze=_freeze(),
            config=config,
            scenario=config.scenarios[0],
            registry=registry,
        )


def test_environment_admission_reverifies_images_without_rewriting_create_once_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze = _freeze()

    class Resolved:
        compose_sha256 = freeze.resolved_compose_sha256

    class Environment:
        def __init__(self) -> None:
            self.inspection_count = 0

        def verify_local_docker(self) -> None:
            pass

        def verify_upstream(self) -> None:
            pass

        def resolve(self):
            return Resolved(), {}

        def inspect_cached_images(self, resolved) -> object:
            assert resolved.compose_sha256 == freeze.resolved_compose_sha256
            self.inspection_count += 1
            return object()

        def verify_cached_images(self, resolved, control_root):
            raise FileExistsError("create-once image lock already exists")

    environment = Environment()

    class Capture:
        private_root = tmp_path

        def _environment(self):
            return environment

    preflight = OwnedLivePreflight(
        capture=Capture(),  # type: ignore[arg-type]
        resolved_compose_sha256=freeze.resolved_compose_sha256,
        image_authority_sha256=freeze.image_authority_sha256,
    )
    lifecycle = object.__new__(OwnedSandboxLiveLifecycle)
    lifecycle.preflight = preflight
    lifecycle.freeze = freeze
    monkeypatch.setattr(
        "ecomsre.dta_v2.live_owned._file_sha256",
        lambda path: freeze.image_authority_sha256,
    )

    lifecycle.admit_environment()

    assert environment.inspection_count == 1


def test_pre_live_freeze_rejects_untracked_or_tracked_dirty_state_before_hashing(
    tmp_path: Path,
) -> None:
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")

    class Result:
        stdout = "?? unsafe-untracked-file\n"

    class DirtyRunner:
        calls = 0

        def run(self, arguments, *, cwd):
            del arguments, cwd
            self.calls += 1
            return Result()

    runner = DirtyRunner()
    with pytest.raises(ValueError, match="exactly clean"):
        _clean_semantic_manifest(tmp_path, config=config, runner=runner)  # type: ignore[arg-type]
    assert runner.calls == 1


def test_pre_fault_start_failure_can_prove_unchanged_baseline_without_write(
    tmp_path: Path,
) -> None:
    baseline = {"flags": {"emailMemoryLeak": {"defaultVariant": "off"}}}
    flag_file = tmp_path / "baseline.json"
    flag_file.write_text(json.dumps(baseline), encoding="utf-8")

    class Environment:
        def verify_owned_resources(self, *, require_complete):
            assert require_complete is False
            return {"container": 3, "network": 1, "volume": 3}

    class Capture:
        def _flag_file(self):
            return flag_file

        def _baseline(self):
            return baseline

        def _environment(self):
            return Environment()

    lifecycle = object.__new__(OwnedSandboxLiveLifecycle)
    lifecycle._fault_attempted = False
    lifecycle._restoration_write_count = 0
    lifecycle.preflight = SimpleNamespace(capture=Capture())

    assert lifecycle.restore_baseline(None) is True
    assert lifecycle.restoration_write_count == 0


def test_owned_fault_revalidation_rejects_actual_baseline_state_drift() -> None:
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")
    payment = next(
        item for item in config.scenarios if item.scenario is LiveScenario.PAYMENT
    )
    lifecycle = object.__new__(OwnedSandboxLiveLifecycle)
    lifecycle.scenario = payment
    lifecycle._admitted_baseline_state_digest = "a" * 64
    lifecycle._require_baseline_mutation_state = (  # type: ignore[method-assign]
        lambda: "b" * 64
    )

    with pytest.raises(RuntimeError, match="baseline|state|precondition"):
        lifecycle.revalidate_before_fault(payment)


def test_email_partial_failure_never_issues_a_third_restoration_write() -> None:
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")
    email_spec = next(item for item in config.scenarios if item.scenario.value == "EMAIL")

    class Flags:
        def verify(self, document):
            del document
            return "a" * 64

    class Capture:
        def _baseline(self):
            return {"flags": {"emailMemoryLeak": {"defaultVariant": "off"}}}

    lifecycle = object.__new__(OwnedSandboxLiveLifecycle)
    lifecycle._fault_attempted = True
    lifecycle._restoration_write_count = 0
    lifecycle._last_controls = SimpleNamespace(forward_write_count=2)
    lifecycle.scenario = email_spec
    lifecycle.preflight = SimpleNamespace(capture=Capture())
    lifecycle._flags = Flags()
    lifecycle._refresh_backend = lambda: None  # type: ignore[method-assign]
    lifecycle._runtime = lambda service: (  # type: ignore[method-assign]
        SimpleNamespace(state=RuntimeState.RUNNING, health=HealthState.HEALTHY)
        if service == "recommendation"
        else SimpleNamespace(state=RuntimeState.EXITED, health=HealthState.UNHEALTHY)
    )

    assert lifecycle.restore_baseline(None) is False
    assert lifecycle.restoration_write_count == 0


def _owned_resume_closure(
    closure: LiveAttemptClosure,
    *,
    claim,
    mode: LiveAttemptMode = LiveAttemptMode.OWNED_LOCAL,
    attempt_id: str | None = None,
    run_id: str | None = None,
) -> LiveAttemptClosure:
    payload = closure.model_dump(mode="json", exclude={"closure_sha256"})
    payload.update(
        {
            "mode": mode,
            "terminal": (
                LiveAttemptTerminal.LIVE_PASS
                if mode is LiveAttemptMode.OWNED_LOCAL
                else LiveAttemptTerminal.OFFLINE_PASS
            ),
            "attempt_id": attempt_id or claim.attempt_id,
            "run_id": run_id or claim.run_id,
        }
    )
    return LiveAttemptClosure.model_validate_json(
        json.dumps(
            {
                **payload,
                "closure_sha256": semantic_sha256(payload),
            },
            separators=(",", ":"),
        )
    )


def _campaign_with_prior_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closure: LiveAttemptClosure,
):
    config = load_live_demo_config(ROOT / "config/dta-v2/live-demo.v1.json")
    registry = load_runbook_registry(ROOT / "config/dta-v2/runbooks")
    change_sha256 = "c" * 64
    monkeypatch.setattr(
        "ecomsre.dta_v2.live_owned._clean_semantic_manifest",
        lambda root, *, config, runner=None: ("b" * 40, change_sha256),
    )
    campaign = OwnedLiveCampaign(
        repository_root=ROOT,
        private_root=tmp_path / "private",
        campaign_id="campaign-resume",
        provider_env_path=tmp_path / "provider.env",
        config=config,
        registry=registry,
        master_authorization=master_authorization(registry),
    )
    claim = build_live_campaign_attempt_claim(
        campaign_id=campaign.campaign_id,
        ordinal=1,
        change_sha256=change_sha256,
    )
    write_private_json(
        campaign.campaign_root / "change.json",
        {
            "schema_version": "dta-v2.live-campaign-change.v1",
            "campaign_id": campaign.campaign_id,
            "change_sha256": change_sha256,
        },
        create_once=True,
    )
    write_private_json(
        campaign.campaign_root / "claims/01-NO_FAULT.json",
        claim,
        create_once=True,
    )
    write_private_json(
        campaign.campaign_root / "closures/01-NO_FAULT.json",
        closure,
        create_once=True,
    )
    return campaign, claim


def test_owned_campaign_resume_requires_and_accepts_exact_prior_claim_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _run(
        tmp_path / "source",
        LiveScenario.NO_FAULT,
        FakeLifecycle(LiveScenario.NO_FAULT, _no_fault_agent()),
    )
    claim = build_live_campaign_attempt_claim(
        campaign_id="campaign-resume",
        ordinal=1,
        change_sha256="c" * 64,
    )
    exact = _owned_resume_closure(source, claim=claim)
    campaign, _ = _campaign_with_prior_closure(tmp_path, monkeypatch, exact)

    next_claim = campaign._claim_next()

    assert next_claim.ordinal == 2
    assert next_claim.scenario is LiveScenario.PAYMENT


def test_owned_campaign_resume_accepts_clean_restored_pre_agent_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OwnedFailureLifecycle(FakeLifecycle):
        mode = LiveAttemptMode.OWNED_LOCAL

    claim = build_live_campaign_attempt_claim(
        campaign_id="campaign-resume",
        ordinal=1,
        change_sha256="c" * 64,
    )
    closure = _run(
        tmp_path / "source",
        LiveScenario.NO_FAULT,
        OwnedFailureLifecycle(
            LiveScenario.NO_FAULT,
            _no_fault_agent(),
            fail_stage="agent",
        ),
        owned_claim=claim,
    )
    assert closure.baseline_restored is True
    assert closure.cleanup_terminal is not None
    assert closure.cleanup_terminal.value == "CLEAN"
    campaign, _ = _campaign_with_prior_closure(tmp_path, monkeypatch, closure)

    next_claim = campaign._claim_next()

    assert next_claim.ordinal == 2
    assert next_claim.scenario is LiveScenario.PAYMENT


@pytest.mark.parametrize("drift", ("mode", "attempt_id", "run_id", "scenario"))
def test_owned_campaign_resume_rejects_prior_closure_lineage_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    scenario = LiveScenario.PAYMENT if drift == "scenario" else LiveScenario.NO_FAULT
    source = _run(
        tmp_path / "source",
        scenario,
        FakeLifecycle(
            scenario,
            _positive_agent(scenario) if scenario is not LiveScenario.NO_FAULT else _no_fault_agent(),
        ),
    )
    claim = build_live_campaign_attempt_claim(
        campaign_id="campaign-resume",
        ordinal=1,
        change_sha256="c" * 64,
    )
    closure = _owned_resume_closure(
        source,
        claim=claim,
        mode=(
            LiveAttemptMode.FAKE_REPLAY
            if drift == "mode"
            else LiveAttemptMode.OWNED_LOCAL
        ),
        attempt_id="other-attempt" if drift == "attempt_id" else None,
        run_id="f" * 32 if drift == "run_id" else None,
    )
    campaign, _ = _campaign_with_prior_closure(tmp_path, monkeypatch, closure)

    with pytest.raises(ValueError, match="lineage|claim|OWNED_LOCAL"):
        campaign._claim_next()
