from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from ecomsre.dta_v2.v21.live_final_cli import (
    _pending_disposition,
    _render_final_progress,
    _verify_finalize_state,
    _verify_readme_projection,
    run_final_closeout,
    run_final_finalize,
)
from ecomsre.dta_v2.v21.live_final_closeout import (
    AD_CPU_ATTEMPT_ID_V1,
    AD_CPU_CODE_HEAD_V1,
    AdCpuPlannerProtocolFailureV1,
    PrfFrozenAgentCapabilityCloseoutV1,
    assert_prf_live_execution_open_v1,
    build_prf_frozen_agent_capability_closeout_v1,
    verify_ad_cpu_planner_protocol_failure_v1,
)
from ecomsre.dta_v2.v21.live_capability_cli import run_positive_execute
from ecomsre.dta_v2.v21.live_cli import run_execute as run_legacy_execute
from ecomsre.dta_v2.v21.live_runner import run_owned_live_positive_continuation_v1
from ecomsre.dta_v2.v21.live_final_reporting import (
    PublicLiveCapabilityCloseoutReportV4,
    build_public_live_capability_closeout_report_v4,
    render_public_final_summary_v4,
    render_public_human_brief_v4,
    render_public_interview_brief_v4,
    render_public_live_markdown_v4,
    render_public_readme_block_v4,
    verify_public_text_v4,
)
from ecomsre_live_sandbox.contracts import write_private_json


def _ad_failure() -> AdCpuPlannerProtocolFailureV1:
    return AdCpuPlannerProtocolFailureV1.build(
        code_head=AD_CPU_CODE_HEAD_V1,
        attempt_id=AD_CPU_ATTEMPT_ID_V1,
        semantic_read_dispatch_count=2,
        fault_impact_verified=True,
        fault_impact_sha256="1" * 64,
        agent_result_raw_sha256="2" * 64,
        agent_result_semantic_sha256="3" * 64,
        agent_result_sha256="4" * 64,
        attempt_terminal_raw_sha256="5" * 64,
        attempt_terminal_semantic_sha256="6" * 64,
        attempt_claim_raw_sha256="7" * 64,
        attempt_claim_semantic_sha256="8" * 64,
        environment_admission_sha256="9" * 64,
        baseline_evidence_sha256="a" * 64,
        positive_continuation_admission_sha256="b" * 64,
        positive_continuation_consumption_sha256="c" * 64,
    )


def _closeout(ad: AdCpuPlannerProtocolFailureV1) -> PrfFrozenAgentCapabilityCloseoutV1:
    return PrfFrozenAgentCapabilityCloseoutV1.build(
        historical_ready_blocker_sha256="d" * 64,
        historical_ready_reconciliation_sha256="e" * 64,
        no_fault_capability_miss_sha256="f" * 64,
        ad_cpu_protocol_failure_sha256=ad.record_sha256,
        amendment2_retry_consumption_sha256="0" * 64,
        amendment3_positive_continuation_consumption_sha256="1" * 64,
    )


def test_ad_duplicate_read_contract_is_failure_with_safe_restoration() -> None:
    record = _ad_failure()

    assert record.classification == (
        "AD_CPU_PLANNER_DUPLICATE_READ_PROTOCOL_FAILURE_SAFE_RESTORATION"
    )
    assert record.agent_failure_code == "DUPLICATE_READ_REQUEST"
    assert record.provider_turn_count == 3
    assert record.fault_operation_count == 1
    assert record.forward_step_count == 0
    assert record.recovery_capability_tested is False
    assert record.baseline_restored is True
    assert record.cleanup_clean is True

    payload = record.model_dump(mode="json", exclude={"record_sha256"})
    payload["provider_turn_count"] = 2
    with pytest.raises(ValueError):
        AdCpuPlannerProtocolFailureV1.model_validate(
            {**payload, "record_sha256": record.record_sha256}
        )


def test_final_closeout_records_unattempted_slots_and_zero_passes() -> None:
    closeout = _closeout(_ad_failure())

    assert closeout.email_slot_status == "NOT_ATTEMPTED"
    assert closeout.product_catalog_slot_status == "NOT_ATTEMPTED"
    assert closeout.live_slots_attempted == 2
    assert closeout.live_slots_passed == 0
    assert closeout.positive_slots_attempted == 1
    assert closeout.positive_slots_passed == 0
    assert closeout.remaining_live_execution_authority == 0


def test_append_only_closeout_blocks_live_execution(tmp_path: Path) -> None:
    private = tmp_path / "private"
    record = _closeout(_ad_failure())
    write_private_json(
        private / "pr-f/final-capability-closeout/closeout.v1.json",
        record,
        create_once=True,
    )

    with pytest.raises(
        RuntimeError,
        match="^BLOCKED_DTA_V21_PRF_LIVE_EXECUTION_CLOSED$",
    ):
        assert_prf_live_execution_open_v1(private_root=private)


def test_all_live_entrypoints_block_before_other_dependencies(tmp_path: Path) -> None:
    private = tmp_path / "private"
    record = _closeout(_ad_failure())
    write_private_json(
        private / "pr-f/final-capability-closeout/closeout.v1.json",
        record,
        create_once=True,
    )
    expected = "^BLOCKED_DTA_V21_PRF_LIVE_EXECUTION_CLOSED$"

    with pytest.raises(RuntimeError, match=expected):
        run_positive_execute(
            repository_root=tmp_path / "missing-repository",
            private_root=private,
            provider_env_path=tmp_path / "missing-provider.env",
        )
    with pytest.raises(RuntimeError, match=expected):
        run_legacy_execute(
            repository_root=tmp_path / "missing-repository",
            private_root=private,
            provider_env_path=tmp_path / "missing-provider.env",
        )
    with pytest.raises(RuntimeError, match=expected):
        run_owned_live_positive_continuation_v1(
            repository_root=tmp_path / "missing-repository",
            prf_private_root=private / "pr-f",
            provider_env_path=tmp_path / "missing-provider.env",
            config=None,  # type: ignore[arg-type]
            registry=None,  # type: ignore[arg-type]
            protocol=None,  # type: ignore[arg-type]
            master_authorization=None,  # type: ignore[arg-type]
            readiness=None,  # type: ignore[arg-type]
            v3_readiness=None,  # type: ignore[arg-type]
            capability_miss=None,  # type: ignore[arg-type]
            readiness_identity=None,  # type: ignore[arg-type]
            readiness_raw_compose={},
            readiness_flagd_directory=tmp_path,
            code_head=AD_CPU_CODE_HEAD_V1,
        )


def test_accepted_private_ad_failure_rebuilds_exactly() -> None:
    configured = os.environ.get("DTA_V21_ACCEPTED_PRIVATE_ROOT")
    if configured is None:
        pytest.skip("DTA_V21_ACCEPTED_PRIVATE_ROOT is not configured")
    private = Path(configured)
    repository = Path(__file__).resolve().parents[2]

    ad = verify_ad_cpu_planner_protocol_failure_v1(
        repository_root=repository,
        private_root=private,
    )
    closeout = build_prf_frozen_agent_capability_closeout_v1(
        repository_root=repository,
        private_root=private,
        ad_failure=ad,
    )

    assert ad.agent_failure_code == "DUPLICATE_READ_REQUEST"
    assert ad.provider_turn_count == 3
    assert ad.semantic_read_dispatch_count == 2
    assert ad.fault_impact_verified is True
    assert ad.fault_operation_count == 1
    assert ad.forward_step_count == 0
    assert closeout.email_slot_status == "NOT_ATTEMPTED"
    assert closeout.product_catalog_slot_status == "NOT_ATTEMPTED"


def test_public_v4_report_rebuilds_deterministically_from_accepted_private() -> None:
    configured = os.environ.get("DTA_V21_ACCEPTED_PRIVATE_ROOT")
    if configured is None:
        pytest.skip("DTA_V21_ACCEPTED_PRIVATE_ROOT is not configured")
    repository = Path(__file__).resolve().parents[2]
    def build() -> PublicLiveCapabilityCloseoutReportV4:
        return build_public_live_capability_closeout_report_v4(
            repository_root=repository,
            private_root=Path(configured),
            closeout_source_code_head="1" * 40,
            candidate_scope_sha256="2" * 64,
            base_readme_sha256="3" * 64,
            base_progress_raw_sha256="4" * 64,
            base_progress_semantic_sha256="5" * 64,
        )

    first = build_public_live_capability_closeout_report_v4(
        repository_root=repository,
        private_root=Path(configured),
        closeout_source_code_head="1" * 40,
        candidate_scope_sha256="2" * 64,
        base_readme_sha256="3" * 64,
        base_progress_raw_sha256="4" * 64,
        base_progress_semantic_sha256="5" * 64,
    )
    second = build()

    assert first == second
    assert first.live_slots_attempted == 2
    assert first.live_slots_passed == 0
    assert first.positive_slots_attempted == 1
    assert first.positive_slots_passed == 0
    assert first.email.status == "NOT_ATTEMPTED"
    assert first.product_catalog.status == "NOT_ATTEMPTED"
    for text in (
        render_public_live_markdown_v4(first),
        render_public_final_summary_v4(first),
        render_public_human_brief_v4(first),
        render_public_interview_brief_v4(first),
    ):
        verify_public_text_v4(text)
        assert "/Users/" not in text
        assert "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS" not in text


@pytest.mark.parametrize(
    "claim",
    (
        "Four of four passed.",
        "No-Fault passed.",
        "Ad recovered.",
        "The live portfolio passed.",
        "DTA_V21_P0_ENGINEERING_ACCEPTANCE_PASS",
        "private evidence: /Users/example/.ecomsre/private",
    ),
)
def test_public_v4_rejects_pass_overclaims_and_private_paths(claim: str) -> None:
    with pytest.raises(ValueError):
        verify_public_text_v4(claim)


def test_readme_projection_scans_only_hash_bound_v4_block() -> None:
    base = (
        "# Existing docs\n\nConfigure `provider.env` outside the repo.\n\n"
        "## One-command offline demo\n"
    )
    report = PublicLiveCapabilityCloseoutReportV4.model_construct(
        terminal=(
            "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_FROZEN_AGENT_"
            "CAPABILITY_LIMITATIONS"
        ),
        base_readme_sha256=hashlib.sha256(base.encode()).hexdigest(),
    )
    projected = base.replace(
        "## One-command offline demo",
        render_public_readme_block_v4(report) + "\n## One-command offline demo",
    )

    _verify_readme_projection(current=projected, report=report)


def test_make_live_entrypoint_checks_closeout_before_provider_inputs() -> None:
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text(
        encoding="utf-8"
    )
    recipe = makefile.split("dta-v21-live-demo:", 1)[1].split("\n\n", 1)[0]

    assert "guard-live-execution" in recipe
    assert recipe.index("guard-live-execution") < recipe.index("DTA_V21_PROVIDER_ENV")
    assert (
        "dta-v21-demo: dta-v21-replay-verify "
        "dta-v21-final-capability-report-verify"
    ) in makefile


def test_final_projection_requires_exact_review_tuple(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="acceptance gates"):
        run_final_finalize(
            repository_root=tmp_path,
            exact_head_ci_sha="1" * 40,
            independent_review_head="2" * 40,
            independent_review_confirmation=(
                "MUST_FIX_0_SHOULD_FIX_0_CLAIM_ACCURACY_PASS"
            ),
            active_pr=55,
        )


def test_final_terminal_requires_exact_clean_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git_stub(_root: Path, *arguments: str) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return "1" * 40
        if arguments == ("branch", "--show-current"):
            return "main"
        if arguments == ("status", "--porcelain=v1", "--untracked-files=all"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_final_cli._git", git_stub
    )
    with pytest.raises(ValueError, match="exact-main"):
        run_final_closeout(
            repository_root=tmp_path,
            exact_main_ci_sha="2" * 40,
        )


def test_final_projection_accepts_only_resumable_state_sequence() -> None:
    repository = Path(__file__).resolve().parents[2]
    report = PublicLiveCapabilityCloseoutReportV4.model_validate_json(
        (repository / "docs/results/dta-v21-live-capability-closeout.json").read_text(
            encoding="utf-8"
        )
    )
    open_progress = (
        repository / "docs/analysis/dta-v21-p0-master-progress.json"
    ).read_text(encoding="utf-8")
    pending = _pending_disposition(report)
    pending.pop("disposition_sha256")
    merged_head = "1" * 40
    final_progress = _render_final_progress(
        open_progress_text=open_progress,
        report=report,
        merged_main_head=merged_head,
    )

    assert _verify_finalize_state(
        progress_text=open_progress,
        disposition=pending,
        report=report,
        merged_main_head=merged_head,
    ) == "OPEN_PROGRESS_PENDING_DISPOSITION"
    assert _verify_finalize_state(
        progress_text=final_progress,
        disposition=pending,
        report=report,
        merged_main_head=merged_head,
    ) == "FINAL_PROGRESS_PENDING_DISPOSITION"
