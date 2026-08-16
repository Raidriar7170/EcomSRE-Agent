from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.live_contracts import (
    CleanupTerminal,
    LIVE_CAMPAIGN_ORDER,
    LiveScenario,
    build_live_campaign_attempt_claim,
    build_pre_live_freeze,
    build_recovery_window,
    load_live_demo_config,
    require_repeat_admission,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config/dta-v2/live-demo.v1.json"
NOW = datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def test_frozen_live_config_contains_exact_four_scenarios() -> None:
    config = load_live_demo_config(CONFIG_PATH)

    assert tuple(item.scenario for item in config.scenarios) == (
        LiveScenario.EMAIL,
        LiveScenario.NO_FAULT,
        LiveScenario.PAYMENT,
        LiveScenario.RECOMMENDATION,
    )
    assert config.email_fault_variant == "1000x"
    assert config.required_baseline_windows == 2
    assert config.required_recovery_windows == 2
    assert config.maximum_email_recovery_slope_bytes_per_second == 100_000.0
    assert config.maximum_unsafe_write_attempts == 0
    assert config.maximum_arbitrary_shell_attempts == 0


def test_pre_live_freeze_binds_every_trusted_identity_and_rejects_drift() -> None:
    config = load_live_demo_config(CONFIG_PATH)
    freeze = build_pre_live_freeze(
        code_head="b" * 40,
        agent_identity_sha256=(
            "aa08b5869aaac7e4ad4b1084367fc99a01c6dd05521ea933fddf9b5fb364ca61"
        ),
        model_id="gpt-5.4-2026-03-05",
        prompt_sha256=(
            "42c21be36772f9ae7a6d0dcf6d910e6cdb58b5e5a08a9807487b4ee54f84bcce"
        ),
        tool_schema_sha256=(
            "6b968f29201ce7c87fe56099788ff34abc93dea895c56e553e4c007b22218192"
        ),
        diagnosis_schema_sha256="c" * 64,
        action_selection_schema_sha256="d" * 64,
        action_proposal_schema_sha256="e" * 64,
        registry_sha256="f" * 64,
        candidate_filter_source_sha256="1" * 64,
        admission_source_sha256="2" * 64,
        authorization_source_sha256="3" * 64,
        executor_source_sha256="4" * 64,
        verifier_source_sha256="5" * 64,
        runner_source_sha256="6" * 64,
        reporting_schema_sha256="7" * 64,
        upstream_commit="1755859a9de82c2e5e225be68abc401a5ebf2b4f",
        upstream_tag="3.0.0",
        resolved_compose_sha256="8" * 64,
        image_authority_sha256="9" * 64,
        live_config=config,
    )

    assert freeze.freeze_sha256
    assert freeze.agent_identity_sha256 == (
        "aa08b5869aaac7e4ad4b1084367fc99a01c6dd05521ea933fddf9b5fb364ca61"
    )
    with pytest.raises(ValidationError, match="freeze digest"):
        type(freeze).model_validate(
            {
                **freeze.model_dump(mode="python"),
                "runner_source_sha256": "0" * 64,
            }
        )


def test_exactly_two_ordered_recovery_windows_are_required() -> None:
    first = build_recovery_window(
        ordinal=1,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=30),
        infrastructure_passed=True,
        business_sli_passed=True,
        endpoint_passed=True,
        configuration_restored=True,
        memory_slope_bytes_per_second=0.0,
    )
    second = build_recovery_window(
        ordinal=2,
        started_at=NOW + timedelta(seconds=30),
        ended_at=NOW + timedelta(seconds=60),
        infrastructure_passed=True,
        business_sli_passed=True,
        endpoint_passed=True,
        configuration_restored=True,
        memory_slope_bytes_per_second=0.0,
    )

    assert first.ordinal == 1
    assert second.ordinal == 2
    assert first.window_sha256 != second.window_sha256


def test_repeat_requires_real_change_restored_baseline_and_clean_cleanup() -> None:
    require_repeat_admission(
        prior_change_sha256=SHA,
        next_change_sha256="b" * 64,
        prior_baseline_restored=True,
        prior_cleanup=CleanupTerminal.CLEAN,
    )
    with pytest.raises(ValueError, match="identical"):
        require_repeat_admission(
            prior_change_sha256=SHA,
            next_change_sha256=SHA,
            prior_baseline_restored=True,
            prior_cleanup=CleanupTerminal.CLEAN,
        )
    with pytest.raises(ValueError, match="baseline"):
        require_repeat_admission(
            prior_change_sha256=SHA,
            next_change_sha256="b" * 64,
            prior_baseline_restored=False,
            prior_cleanup=CleanupTerminal.CLEAN,
        )
    with pytest.raises(ValueError, match="cleanup"):
        require_repeat_admission(
            prior_change_sha256=SHA,
            next_change_sha256="b" * 64,
            prior_baseline_restored=True,
            prior_cleanup=CleanupTerminal.BLOCKED,
        )


def test_campaign_claims_bind_exact_safe_order_attempt_and_run_identity() -> None:
    claims = tuple(
        build_live_campaign_attempt_claim(
            campaign_id="campaign-safe-01",
            ordinal=ordinal,  # type: ignore[arg-type]
            change_sha256="c" * 64,
        )
        for ordinal in (1, 2, 3, 4)
    )

    assert tuple(item.scenario for item in claims) == LIVE_CAMPAIGN_ORDER
    assert tuple(item.ordinal for item in claims) == (1, 2, 3, 4)
    assert len({item.attempt_id for item in claims}) == 4
    assert len({item.run_id for item in claims}) == 4
    with pytest.raises(ValidationError):
        type(claims[0]).model_validate(
            {**claims[0].model_dump(mode="python"), "ordinal": 2}
        )
