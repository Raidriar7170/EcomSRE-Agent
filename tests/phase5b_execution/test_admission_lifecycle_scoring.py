from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace
import os
import pytest

from ecomsre.phase5b.contracts import ExecutionSchedule
from ecomsre.phase5b.protocol import load_strict_json
from ecomsre.model.gateway import OpenAICompatibleConfig

import scripts.phase5b_execution.admission as admission
import scripts.phase5b_execution.canary as canary_module
import scripts.phase5b_execution.cli as execution_cli
import scripts.phase5b_execution.lifecycle as lifecycle
import scripts.phase5b_execution.scoring as scoring_module
from scripts.phase5b_execution.ablation import (
    UnsupportedFrozenAblationExecutor,
    run_ablation_schedule,
)
from scripts.phase5b_execution.cli import run_final_analysis, run_provider_preflight
from scripts.phase5b_execution.canary import run_provider_canary, verify_canary_chain
from scripts.phase5b_execution.checkpoint import _atomic_create, _load_canonical
from scripts.phase5b_execution.contracts import (
    ExecutionAttemptMarker,
    ExecutionStartedRecord,
    PROVIDER_CANARY_RUN_ID,
    ProviderCanaryRecord,
    ProviderUsageRecord,
    ScoredRunRequest,
    TerminalStatus,
    canonical_json_bytes,
    seal_raw_record,
)
from scripts.phase5b_execution.runner import run_frozen_schedule
from scripts.phase5b_execution.scoring import (
    _metric_summary,
    _score_one,
    _truth_projection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_provider_preflight_returns_only_safe_configuration_facts() -> None:
    environment = {
        "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
        "ECOMSRE_LLM_API_KEY": "never-print-this-secret",
        "ECOMSRE_LLM_MODEL": "gpt-5.4-mini-2026-03-17",
    }

    report = run_provider_preflight(environment)

    serialized = str(report)
    assert report["base_url_configured"] is True
    assert report["api_key_configured"] is True
    assert report["frozen_model"] is True
    assert "never-print-this-secret" not in serialized
    assert "provider.invalid" not in serialized
    assert report["provider_calls"] == 0


def test_provider_preflight_rejects_premature_truth_locator() -> None:
    with pytest.raises(PermissionError, match="forbidden before unblinding"):
        run_provider_preflight({"PHASE5B_GROUND_TRUTH_ROOT": "/forbidden"})


def test_provider_canary_requires_exact_execution_authorization(
    tmp_path: Path,
) -> None:
    with pytest.raises(PermissionError, match="exact Phase 5B"):
        run_provider_canary(
            project_root=PROJECT_ROOT,
            execution_root=tmp_path,
            environment={"PHASE5B_EXECUTION_AUTHORIZATION": "ALLOW_ALL"},
        )


def test_post_unblinding_analysis_still_rejects_builder_locator(
    tmp_path: Path,
) -> None:
    with pytest.raises(PermissionError, match="whole-pack locators"):
        run_final_analysis(
            output_root=tmp_path,
            environment={
                "PHASE5B_EXECUTION_AUTHORIZATION": (
                    "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION"
                ),
                "PHASE5B_GROUND_TRUTH_ROOT": "/truth",
                "PHASE5B_BUILDER_ROOT": "/forbidden",
            },
        )


@pytest.mark.parametrize(
    ("head", "origin", "branch", "status", "message"),
    [
        ("a" * 40, "b" * 40, "phase5b/v1-frozen-results", "", "origin/main"),
        ("a" * 40, "a" * 40, "wrong", "", "frozen-results branch"),
        (
            "a" * 40,
            "a" * 40,
            "phase5b/v1-frozen-results",
            " M scripts/x.py",
            "changes or untracked",
        ),
    ],
)
def test_merged_source_gate_fails_closed_before_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    head: str,
    origin: str,
    branch: str,
    status: str,
    message: str,
) -> None:
    monkeypatch.setattr(admission, "verify_execution_freeze_manifest", lambda _root: object())
    monkeypatch.setattr(admission, "verify_freeze_manifest", lambda *_args: object())
    monkeypatch.setattr(admission, "verify_public_execution_seal", lambda _root: {})

    def fake_git(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return head
        if args == ("rev-parse", "origin/main"):
            return origin
        if args == ("branch", "--show-current"):
            return branch
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return status
        return ""

    monkeypatch.setattr(admission, "_git", fake_git)

    with pytest.raises(ValueError, match=message):
        admission.require_merged_execution_source(tmp_path)


def test_runtime_source_rejects_head_drift_and_untracked_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "execution-freeze.json"
    manifest.write_bytes(b"{}\n")
    monkeypatch.setattr(admission, "EXECUTION_FREEZE_RELATIVE", manifest)
    monkeypatch.setattr(admission, "verify_execution_freeze_manifest", lambda _root: object())
    monkeypatch.setattr(admission, "verify_freeze_manifest", lambda *_args: object())
    monkeypatch.setattr(admission, "sha256_regular_file", lambda _path: "a" * 64)

    def wrong_head(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "b" * 40
        if args == ("branch", "--show-current"):
            return "phase5b/v1-frozen-results"
        return ""

    monkeypatch.setattr(admission, "_git", wrong_head)
    with pytest.raises(ValueError, match="HEAD"):
        admission.require_frozen_runtime_source(
            tmp_path,
            expected_execution_freeze_sha256="a" * 64,
            expected_source_commit="c" * 40,
        )

    def untracked(_root: Path, *args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "c" * 40
        if args == ("branch", "--show-current"):
            return "phase5b/v1-frozen-results"
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return "?? sitecustomize.py"
        return ""

    monkeypatch.setattr(admission, "_git", untracked)
    with pytest.raises(ValueError, match="untracked"):
        admission.require_frozen_runtime_source(
            tmp_path,
            expected_execution_freeze_sha256="a" * 64,
            expected_source_commit="c" * 40,
        )


def test_canonical_loader_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    record = ProviderCanaryRecord(
        schema_version="phase5b.provider-canary-record.v1",
        evaluation_version="phase5b.v1",
        public_template_id="ad-partial-failure-complete",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
        terminal_status=TerminalStatus.WORKFLOW_FAILURE,
        raw_record_sha256="a" * 64,
        provider_configuration_sha256="b" * 64,
        provider_network_calls=0,
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        provider_usage_known=False,
        typed_protocol_pass=False,
        no_retry=True,
        scripted_fallback=False,
    )
    target.write_bytes(canonical_json_bytes(record.model_dump(mode="json")))
    alias = tmp_path / "alias.json"
    os.symlink(target, alias)

    with pytest.raises(ValueError, match="regular non-symlink"):
        _load_canonical(alias, ProviderCanaryRecord)


def test_canary_summary_without_raw_chain_is_rejected(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    summary = ProviderCanaryRecord(
        schema_version="phase5b.provider-canary-record.v1",
        evaluation_version="phase5b.v1",
        public_template_id="ad-partial-failure-complete",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
        terminal_status=TerminalStatus.COMPLETED,
        raw_record_sha256="a" * 64,
        provider_configuration_sha256="b" * 64,
        provider_network_calls=1,
        model_calls=1,
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
        provider_usage_known=True,
        typed_protocol_pass=True,
        no_retry=True,
        scripted_fallback=False,
    )
    (state / "provider-canary-record.json").write_bytes(
        canonical_json_bytes(summary.model_dump(mode="json"))
    )

    with pytest.raises((FileNotFoundError, ValueError)):
        verify_canary_chain(tmp_path)


def test_canary_pass_requires_known_provider_usage() -> None:
    with pytest.raises(ValueError, match="canary pass"):
        ProviderCanaryRecord(
            schema_version="phase5b.provider-canary-record.v1",
            evaluation_version="phase5b.v1",
            public_template_id="ad-partial-failure-complete",
            seed_id="seed-00",
            variant="SINGLE_AGENT_V2",
            terminal_status=TerminalStatus.COMPLETED,
            raw_record_sha256="a" * 64,
            provider_configuration_sha256="b" * 64,
            provider_network_calls=1,
            model_calls=1,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            provider_usage_known=False,
            typed_protocol_pass=True,
            no_retry=True,
            scripted_fallback=False,
        )


def test_canary_crash_recovery_cannot_launder_provider_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    request = ScoredRunRequest(
        run_id=PROVIDER_CANARY_RUN_ID,
        template_id="ad-partial-failure-complete",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
    )
    config_a = OpenAICompatibleConfig(
        base_url="https://provider-a.invalid/v1",
        api_key="key-a",
        model="gpt-5.4-mini-2026-03-17",
    )
    marker = ExecutionAttemptMarker(
        run_id=request.run_id,
        request_sha256=request.request_sha256(),
        evidence_class="UNSCORED_PROVIDER_CANARY",
        provider_configuration_sha256=(
            admission.provider_configuration_fingerprint(config_a)
        ),
        started_at_utc=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    raw = seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=TerminalStatus.PROVIDER_TRANSPORT_FAILURE,
        observed_diagnosis=None,
        usage=ProviderUsageRecord(
            model_calls=1,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            workflow_tokens=0,
            combined_tokens=0,
            provider_network_calls=1,
            provider_usage_known=False,
        ),
        evidence_class="UNSCORED_PROVIDER_CANARY",
        provider_attempted=True,
        latency_ms=1,
        failure_code="PROVIDER_TIMEOUT",
        failure_stage="HTTP_TRANSPORT",
    )
    _atomic_create(tmp_path / canary_module.CANARY_ATTEMPT, marker.canonical_bytes())
    _atomic_create(tmp_path / canary_module.CANARY_RAW_RECORD, raw.canonical_bytes())
    monkeypatch.setattr(
        canary_module,
        "require_merged_execution_source",
        lambda _root: ("1" * 40, "1" * 40),
    )

    with pytest.raises(ValueError, match="recovery marker"):
        run_provider_canary(
            project_root=tmp_path,
            execution_root=tmp_path,
            environment={
                "PHASE5B_EXECUTION_AUTHORIZATION": (
                    "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION"
                ),
                "ECOMSRE_LLM_BASE_URL": "https://provider-b.invalid/v1",
                "ECOMSRE_LLM_API_KEY": "key-b",
                "ECOMSRE_LLM_MODEL": "gpt-5.4-mini-2026-03-17",
            },
        )


def test_execution_start_rejects_prepositioned_scored_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_root = tmp_path / "execution"
    state = execution_root / "state"
    state.mkdir(parents=True, mode=0o700)
    (state / "provider-canary-record.json").write_bytes(b"canary\n")
    (state / "provider-canary-raw.json").write_bytes(b"raw\n")
    injected = execution_root / "main" / "raw"
    injected.mkdir(parents=True, mode=0o700)
    (injected / f"{'a' * 32}.json").write_bytes(b"{}\n")
    monkeypatch.setattr(
        lifecycle,
        "verify_canary_chain",
        lambda _root, **_kwargs: SimpleNamespace(
            typed_protocol_pass=True,
            provider_configuration_sha256="6" * 64,
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "load_execution_freeze_manifest",
        lambda _path: SimpleNamespace(
            hidden_pack_manifest_sha256="4" * 64,
            agent_visible_pack_sha256="5" * 64,
            main_evaluation_ready=True,
            ablation_slot_count=38,
            ablation_implementation_available=False,
            ablation_evidence_available=False,
            ablation_primary_eligible=False,
            ablation_disposition=(
                "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
            ),
        ),
    )
    monkeypatch.setattr(lifecycle, "sha256_regular_file", lambda _path: "2" * 64)

    with pytest.raises(ValueError, match="pristine canary-only"):
        lifecycle.create_execution_started_record(
            project_root=tmp_path,
            execution_root=execution_root,
            source_commit="1" * 40,
            origin_main_commit="1" * 40,
            provider_configuration_sha256="6" * 64,
        )


def test_ablation_entry_requires_exact_main_terminal_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = ExecutionStartedRecord(
        schema_version="phase5b.execution-started.v1",
        evaluation_version="phase5b.v1",
        source_commit="1" * 40,
        origin_main_commit="1" * 40,
        execution_freeze_sha256="2" * 64,
        hidden_pack_seal_sha256="3" * 64,
        hidden_pack_manifest_sha256="4" * 64,
        agent_visible_pack_sha256="5" * 64,
        canary_record_sha256="6" * 64,
        provider_configuration_sha256="7" * 64,
        from_state="HIDDEN_PACK_SEALED",
        to_state="EXECUTION_STARTED",
        completed_main_runs=0,
        completed_ablation_runs=0,
        frozen_files_unchanged=True,
        ground_truth_read=False,
        create_once=True,
    )
    monkeypatch.setattr(
        execution_cli, "_require_actual_execution_authorization", lambda _env: None
    )
    monkeypatch.setattr(
        execution_cli, "_require_external_output_root", lambda _root: tmp_path
    )
    monkeypatch.setattr(
        execution_cli, "_verify_visible_execution_inputs", lambda _env: None
    )
    monkeypatch.setattr(
        execution_cli,
        "verify_execution_started_chain",
        lambda _project_root, _execution_root: started,
    )
    monkeypatch.setattr(
        execution_cli, "_runtime_integrity_guard", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        execution_cli,
        "verify_main_execution_complete",
        lambda *_args: (_ for _ in ()).throw(ValueError("main execution incomplete")),
    )

    with pytest.raises(ValueError, match="main execution incomplete"):
        execution_cli.run_actual_ablation_execution(
            output_root=tmp_path,
            environment={},
        )


def test_lifecycle_seals_exact_180_plus_38_terminal_records_create_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution_root = tmp_path / "execution"
    schedule = load_strict_json(
        PROJECT_ROOT / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )

    class ActualTerminalExecutor:
        def __call__(self, request: ScoredRunRequest):
            return seal_raw_record(
                run_id=request.run_id,
                template_id=request.template_id,
                seed_id=request.seed_id,
                variant=request.variant,
                terminal_status=TerminalStatus.WORKFLOW_FAILURE,
                observed_diagnosis=None,
                usage=ProviderUsageRecord(
                    model_calls=0,
                    tool_calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    workflow_tokens=0,
                    combined_tokens=0,
                    provider_network_calls=0,
                    provider_usage_known=False,
                ),
                evidence_class="ACTUAL_SCORED",
                provider_attempted=False,
                latency_ms=0,
                failure_code="WORKFLOW_FAILURE",
                failure_stage="OFFLINE_WORKFLOW",
            )

    run_frozen_schedule(
        schedule=schedule,
        output_root=execution_root / "main",
        executor=ActualTerminalExecutor(),
        sleeper=lambda _seconds: None,
        evidence_class="ACTUAL_SCORED",
    )
    run_ablation_schedule(
        registry_path=PROJECT_ROOT / "config/phase5b/ablation-registry.v1.json",
        output_root=execution_root / "ablation",
        executor=UnsupportedFrozenAblationExecutor(),
        sleeper=lambda _seconds: None,
        evidence_class="ACTUAL_SCORED",
    )
    state_dir = execution_root / "state"
    state_dir.mkdir(mode=0o700)
    canary_path = execution_root / lifecycle.CANARY_RECORD
    canary_path.write_bytes(b"synthetic-canary\n")
    fake_freeze = tmp_path / "execution-freeze.json"
    fake_freeze.write_bytes(b"{}\n")
    monkeypatch.setattr(lifecycle, "EXECUTION_FREEZE_RELATIVE", fake_freeze)
    real_sha256_regular_file = lifecycle.sha256_regular_file

    def lifecycle_sha(path: Path) -> str:
        if path == fake_freeze:
            return "2" * 64
        if path.as_posix().endswith("config/phase5b-seal/hidden-pack-seal.v1.json"):
            return "3" * 64
        return real_sha256_regular_file(path)

    monkeypatch.setattr(lifecycle, "sha256_regular_file", lifecycle_sha)
    fake_freeze_record = SimpleNamespace(
        protocol_commit="7" * 40,
        protocol_freeze_manifest_sha256="8" * 64,
        hidden_pack_manifest_sha256="4" * 64,
        agent_visible_pack_sha256="5" * 64,
        ground_truth_pack_sha256="b" * 64,
    )
    monkeypatch.setattr(
        lifecycle,
        "load_execution_freeze_manifest",
        lambda _path: fake_freeze_record,
    )
    monkeypatch.setattr(
        lifecycle,
        "verify_canary_chain",
        lambda _execution_root, **_kwargs: SimpleNamespace(
            typed_protocol_pass=True,
            provider_configuration_sha256="6" * 64,
        ),
    )
    started = ExecutionStartedRecord(
        schema_version="phase5b.execution-started.v1",
        evaluation_version="phase5b.v1",
        source_commit="1" * 40,
        origin_main_commit="1" * 40,
        execution_freeze_sha256="2" * 64,
        hidden_pack_seal_sha256="3" * 64,
        hidden_pack_manifest_sha256="4" * 64,
        agent_visible_pack_sha256="5" * 64,
        canary_record_sha256=hashlib.sha256(canary_path.read_bytes()).hexdigest(),
        provider_configuration_sha256="6" * 64,
        from_state="HIDDEN_PACK_SEALED",
        to_state="EXECUTION_STARTED",
        completed_main_runs=0,
        completed_ablation_runs=0,
        frozen_files_unchanged=True,
        ground_truth_read=False,
        create_once=True,
    )
    state_path = execution_root / lifecycle.EXECUTION_STARTED_RECORD
    state_path.write_bytes(canonical_json_bytes(started.model_dump(mode="json")))

    seal = lifecycle.seal_execution_complete(
        project_root=PROJECT_ROOT,
        execution_root=execution_root,
    )

    assert seal.completed_main_runs == 180
    assert seal.completed_ablation_runs == 38
    assert seal.main_evaluation_ready is True
    assert seal.ablation_slot_count == 38
    assert seal.ablation_implementation_available is False
    assert seal.ablation_evidence_available is False
    assert seal.ablation_primary_eligible is False
    assert (
        seal.ablation_disposition
        == "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    )
    assert seal.failure_count == 218
    assert seal.provider_network_calls == 0
    assert seal.ground_truth_read is False
    assert (execution_root / lifecycle.MAIN_EXECUTION_REPORT).exists()
    assert (execution_root / lifecycle.ABLATION_EXECUTION_REPORT).exists()
    assert lifecycle.verify_execution_complete_chain(PROJECT_ROOT, execution_root) == seal
    main_report_path = execution_root / lifecycle.MAIN_EXECUTION_REPORT
    main_report_bytes = main_report_path.read_bytes()
    symlink_target = tmp_path / "main-report-target.json"
    symlink_target.write_bytes(main_report_bytes)
    main_report_path.unlink()
    os.symlink(symlink_target, main_report_path)
    with pytest.raises(ValueError, match="regular non-symlink"):
        lifecycle.verify_execution_complete_chain(PROJECT_ROOT, execution_root)
    main_report_path.unlink()
    main_report_path.write_bytes(main_report_bytes)
    seal_path = execution_root / lifecycle.EXECUTION_COMPLETE_SEAL
    forged = seal.model_copy(update={"provider_network_calls": 1})
    seal_path.write_bytes(canonical_json_bytes(forged.model_dump(mode="json")))
    with pytest.raises(ValueError, match="does not reconstruct"):
        lifecycle.verify_execution_complete_chain(PROJECT_ROOT, execution_root)
    seal_path.write_bytes(canonical_json_bytes(seal.model_dump(mode="json")))

    unblinding = lifecycle.create_execution_unblinding_record(
        project_root=PROJECT_ROOT,
        execution_root=execution_root,
    )
    repeated = lifecycle.create_execution_unblinding_record(
        project_root=PROJECT_ROOT,
        execution_root=execution_root,
    )

    assert repeated == unblinding
    assert unblinding.execution_source_commit == started.source_commit
    assert unblinding.execution_freeze_sha256 == seal.execution_freeze_sha256
    assert unblinding.completed_main_runs == 180
    assert unblinding.completed_ablation_runs == 38
    assert unblinding.main_evaluation_ready is True
    assert unblinding.ablation_slot_count == 38
    assert unblinding.ablation_implementation_available is False
    assert unblinding.ablation_evidence_available is False
    assert unblinding.ablation_primary_eligible is False
    assert (
        unblinding.ablation_disposition
        == "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    )
    unblinding_path = execution_root / lifecycle.UNBLINDING_RECORD
    forged_unblinding = unblinding.model_copy(update={"protocol_commit": "c" * 40})
    unblinding_path.write_bytes(
        canonical_json_bytes(forged_unblinding.model_dump(mode="json"))
    )
    with pytest.raises(ValueError, match="does not reconstruct"):
        lifecycle.verify_unblinding_chain(PROJECT_ROOT, execution_root)


def test_terminal_failure_scores_false_and_remains_in_denominator() -> None:
    request = ScoredRunRequest(
        run_id="a" * 32,
        template_id="hidden-01",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
    )
    raw = seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=TerminalStatus.PROVIDER_TRANSPORT_FAILURE,
        observed_diagnosis=None,
        usage=ProviderUsageRecord(
            model_calls=1,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            workflow_tokens=0,
            combined_tokens=0,
            provider_network_calls=1,
            provider_usage_known=False,
        ),
        evidence_class="ACTUAL_SCORED",
        provider_attempted=True,
        latency_ms=1,
        failure_code="PROVIDER_TIMEOUT",
        failure_stage="HTTP_TRANSPORT",
        recorded_at_utc=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    truth = _truth_projection(
        payload={
            "schema_version": "phase5b.hidden-ground-truth.v1",
            "evaluation_version": "phase5b.v1",
            "template_id": "hidden-01",
            "seed_id": "seed-00",
            "decision": "RCA_CONFIRMED",
            "incident_confirmed": True,
            "root_service": "checkout",
            "fault_mechanism": "timeout",
            "causal_chain": ["checkout stalls", "latency degrades"],
            "affected_sli": "checkout latency",
            "required_support_sources": ["METRICS", "LOGS"],
            "required_contradiction_handling": [],
            "required_missing_evidence": [],
            "write_disposition": "NO_ACTION",
            "difficult_subsets": ["cross_service_cascade"],
        },
        template_id="hidden-01",
        seed_id="seed-00",
        write_disposition="NO_ACTION",
    )

    scored = _score_one(
        raw=raw,
        truth=truth,
        truth_sha256="b" * 64,
        population="HIDDEN",
    )

    assert scored.decision_correct is False
    assert scored.runtime_completed is False
    assert scored.failure_code == "PROVIDER_TIMEOUT"


def test_hidden_truth_projection_uses_exact_frozen_contract_fields() -> None:
    truth = _truth_projection(
        payload={
            "schema_version": "phase5b.hidden-ground-truth.v1",
            "evaluation_version": "phase5b.v1",
            "template_id": "hidden-01",
            "seed_id": "seed-00",
            "decision": "RCA_CONFIRMED",
            "incident_confirmed": True,
            "root_service": "checkout",
            "fault_mechanism": "timeout",
            "causal_chain": ["checkout stalls", "latency degrades"],
            "affected_sli": "checkout latency",
            "required_support_sources": ["METRICS", "LOGS"],
            "required_contradiction_handling": [],
            "required_missing_evidence": [],
            "write_disposition": "NO_ACTION",
            "difficult_subsets": ["cross_service_cascade"],
        },
        template_id="hidden-01",
        seed_id="seed-00",
        write_disposition="NO_ACTION",
    )

    assert truth.required_support_sources == ("METRICS", "LOGS")
    assert truth.difficult_subsets == ("cross_service_cascade",)
    with pytest.raises(ValueError, match="write disposition"):
        _truth_projection(
            payload={
                **{
                    "schema_version": "phase5b.hidden-ground-truth.v1",
                    "evaluation_version": "phase5b.v1",
                    "template_id": "hidden-01",
                    "seed_id": "seed-00",
                    "decision": "ABSTAIN",
                    "incident_confirmed": False,
                    "root_service": None,
                    "fault_mechanism": None,
                    "causal_chain": [],
                    "affected_sli": "checkout latency",
                    "required_support_sources": [],
                    "required_contradiction_handling": ["Reject stale signal."],
                    "required_missing_evidence": [],
                    "write_disposition": "SAFE_REPLAY_REMEDIATION_CANDIDATE",
                    "difficult_subsets": ["required_abstention"],
                }
            },
            template_id="hidden-01",
            seed_id="seed-00",
            write_disposition="NO_ACTION",
        )


def test_unknown_provider_usage_is_not_counted_as_zero_cost() -> None:
    request = ScoredRunRequest(
        run_id="c" * 32,
        template_id="hidden-01",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
    )
    raw = seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=TerminalStatus.PROVIDER_TRANSPORT_FAILURE,
        observed_diagnosis=None,
        usage=ProviderUsageRecord(
            model_calls=1,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            workflow_tokens=0,
            combined_tokens=0,
            provider_network_calls=1,
            provider_usage_known=False,
        ),
        evidence_class="ACTUAL_SCORED",
        provider_attempted=True,
        latency_ms=0,
        failure_code="PROVIDER_TIMEOUT",
        failure_stage="HTTP_TRANSPORT",
    )
    truth = _truth_projection(
        payload={
            "schema_version": "phase5b.hidden-ground-truth.v1",
            "evaluation_version": "phase5b.v1",
            "template_id": "hidden-01",
            "seed_id": "seed-00",
            "decision": "ABSTAIN",
            "incident_confirmed": False,
            "root_service": None,
            "fault_mechanism": None,
            "causal_chain": [],
            "affected_sli": "synthetic sli",
            "required_support_sources": [],
            "required_contradiction_handling": [],
            "required_missing_evidence": [],
            "write_disposition": "NO_ACTION",
            "difficult_subsets": ["required_abstention"],
        },
        template_id="hidden-01",
        seed_id="seed-00",
        write_disposition="NO_ACTION",
    )
    scored = _score_one(
        raw=raw,
        truth=truth,
        truth_sha256="d" * 64,
        population="HIDDEN",
    )
    summary = _metric_summary((scored,))

    assert summary.cost_denominators["provider_tokens"] == 0
    assert summary.cost_denominators["total_tokens"] == 0


def test_final_report_builder_emits_main_readiness_and_ablation_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scoring_module, "load_strict_json", lambda *_args: object())
    monkeypatch.setattr(scoring_module, "_analysis_runs", lambda _bundle: ())
    monkeypatch.setattr(
        scoring_module,
        "analyze_populations",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scoring_module,
        "hidden_primary_bootstrap",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(scoring_module, "superiority_claim", lambda _result: False)
    monkeypatch.setattr(
        scoring_module,
        "cost_quality_claim",
        lambda *_args: False,
    )
    monkeypatch.setattr(scoring_module, "_population", lambda *_args: object())
    monkeypatch.setattr(scoring_module, "_DIFFICULT_SUBSETS", ())
    monkeypatch.setattr(scoring_module, "_sha256", lambda _path: "c" * 64)

    captured: dict[str, object] = {}

    def capture_report(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(scoring_module, "FinalEvaluationReport", capture_report)
    ablation_records = tuple(
        SimpleNamespace(
            failure_code="ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS",
            terminal_status=TerminalStatus.WORKFLOW_FAILURE,
            usage=SimpleNamespace(provider_network_calls=0),
        )
        for _ in range(38)
    )

    scoring_module._build_final_report(
        project_root=tmp_path,
        execution_root=tmp_path,
        complete=SimpleNamespace(
            source_commit="a" * 40,
            execution_freeze_sha256="b" * 64,
            execution_report_sha256="d" * 64,
        ),
        unblinding=SimpleNamespace(protocol_commit="e" * 40),
        bundle=SimpleNamespace(records=()),
        ablation_records=ablation_records,
    )

    assert captured["main_evaluation_ready"] is True
    assert captured["ablation_slot_count"] == 38
    assert captured["ablation_implementation_available"] is False
    assert captured["ablation_evidence_available"] is False
    assert captured["ablation_primary_eligible"] is False
    assert captured["ablation_disposition"] == (
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    )


def test_final_report_verification_stops_on_source_drift_before_truth_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete = SimpleNamespace(
        execution_freeze_sha256="a" * 64,
        source_commit="b" * 40,
    )
    monkeypatch.setattr(
        scoring_module,
        "verify_execution_complete_chain",
        lambda _project_root, _execution_root: complete,
    )
    monkeypatch.setattr(
        scoring_module,
        "verify_unblinding_chain",
        lambda _project_root, _execution_root: SimpleNamespace(),
    )
    monkeypatch.setattr(
        scoring_module,
        "require_frozen_runtime_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("source drift")),
    )
    truth_scored = False

    def forbidden_score(**_kwargs):
        nonlocal truth_scored
        truth_scored = True
        raise AssertionError("truth scoring should not run")

    monkeypatch.setattr(scoring_module, "score_execution", forbidden_score)

    with pytest.raises(ValueError, match="source drift"):
        scoring_module.verify_final_report(tmp_path, tmp_path, tmp_path / "truth")
    assert truth_scored is False


def test_final_report_verify_never_creates_missing_scoring_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    complete = SimpleNamespace(
        execution_freeze_sha256="a" * 64,
        source_commit="b" * 40,
    )
    monkeypatch.setattr(
        scoring_module,
        "verify_execution_complete_chain",
        lambda *_args: complete,
    )
    monkeypatch.setattr(
        scoring_module,
        "verify_unblinding_chain",
        lambda *_args: SimpleNamespace(),
    )
    monkeypatch.setattr(
        scoring_module, "require_frozen_runtime_source", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        scoring_module,
        "_load_canonical",
        lambda *_args: (_ for _ in ()).throw(FileNotFoundError("scoring bundle")),
    )
    truth_scored = False

    def forbidden_score(**_kwargs):
        nonlocal truth_scored
        truth_scored = True

    monkeypatch.setattr(scoring_module, "score_execution", forbidden_score)

    with pytest.raises(FileNotFoundError, match="scoring bundle"):
        scoring_module.verify_final_report(tmp_path, tmp_path, tmp_path / "truth")
    assert truth_scored is False


def _stub_final_verification_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, object, object]:
    complete = SimpleNamespace(
        execution_freeze_sha256="a" * 64,
        source_commit="b" * 40,
    )
    unblinding = object()
    bundle = object()
    monkeypatch.setattr(
        scoring_module,
        "verify_execution_complete_chain",
        lambda *_args: complete,
    )
    monkeypatch.setattr(
        scoring_module,
        "verify_unblinding_chain",
        lambda *_args: unblinding,
    )
    monkeypatch.setattr(
        scoring_module, "require_frozen_runtime_source", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(scoring_module, "score_execution", lambda **_kwargs: bundle)
    monkeypatch.setattr(
        scoring_module,
        "_validated_scoring_bundle",
        lambda **_kwargs: bundle,
    )
    monkeypatch.setattr(
        scoring_module,
        "_load_ablation_records",
        lambda *_args: (),
    )
    return complete, unblinding, bundle


def test_final_report_metric_tamper_is_rejected_even_with_other_files_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_final_verification_dependencies(monkeypatch)
    expected_report = object()
    tampered_report = object()
    monkeypatch.setattr(
        scoring_module,
        "_build_final_report",
        lambda **_kwargs: expected_report,
    )
    monkeypatch.setattr(
        scoring_module,
        "_load_canonical",
        lambda *_args: tampered_report,
    )

    with pytest.raises(ValueError, match="final report does not reconstruct"):
        scoring_module.verify_final_report(tmp_path, tmp_path, tmp_path / "truth")


def test_final_disposition_field_tamper_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_final_verification_dependencies(monkeypatch)
    expected_report = object()
    expected_disposition = object()
    tampered_disposition = object()
    monkeypatch.setattr(
        scoring_module,
        "_build_final_report",
        lambda **_kwargs: expected_report,
    )
    monkeypatch.setattr(
        scoring_module,
        "_build_final_disposition",
        lambda **_kwargs: expected_disposition,
    )

    def load_final(path: Path, _model):
        if path == tmp_path / scoring_module.FINAL_REPORT:
            return expected_report
        return tampered_disposition

    monkeypatch.setattr(scoring_module, "_load_canonical", load_final)

    with pytest.raises(ValueError, match="final disposition does not reconstruct"):
        scoring_module.verify_final_report(tmp_path, tmp_path, tmp_path / "truth")
