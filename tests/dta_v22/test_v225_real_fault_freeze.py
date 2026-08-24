from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.real_fault_cli_v225 import (
    _claim_campaign_v225,
    _claim_final_execution_v225,
    _parser,
)
from ecomsre.dta_v2.v22.real_fault_study_v225 import (
    build_manifest_v225,
    build_pre_live_freeze_v225,
)
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    write_private_json,
)


def _freeze():
    return build_pre_live_freeze_v225(
        code_head="1" * 40,
        comparator_service="recommendation",
        alias_map_set_sha256="2" * 64,
        flat_prompt_sha256="3" * 64,
        current_prompt_sha256="4" * 64,
        scorer_sha256="5" * 64,
    )


def _manifest():
    return build_manifest_v225(
        pre_live_freeze=_freeze(),
        capture_pair_sha256="6" * 64,
        case_set_sha256="7" * 64,
        truth_set_sha256="8" * 64,
    )


def test_final_manifest_binds_capture_case_and_truth_before_execution() -> None:
    manifest = _manifest()
    changed = build_manifest_v225(
        pre_live_freeze=manifest.pre_live_freeze,
        capture_pair_sha256=manifest.capture_pair_sha256,
        case_set_sha256="9" * 64,
        truth_set_sha256=manifest.truth_set_sha256,
    )

    assert manifest.capture_pair_sha256 == "6" * 64
    assert manifest.case_set_sha256 == "7" * 64
    assert manifest.truth_set_sha256 == "8" * 64
    assert manifest.manifest_sha256 != changed.manifest_sha256


def test_campaign_claim_allows_only_fixed_primary_then_eligible_replacement(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)

    assert _claim_campaign_v225(private_root=private, replacement=False) == "campaign-0001"
    with pytest.raises(FileExistsError):
        _claim_campaign_v225(private_root=private, replacement=False)
    primary = private / "campaign-0001"
    ensure_private_directory(primary)
    write_private_json(
        primary / "blocked-terminal.json",
        {
            "stage": "COMPARATOR_SELECTION",
            "baseline_capture_exists": False,
            "fault_capture_exists": False,
            "provider_shadow_exists": False,
            "replacement_cause": "TELEMETRY",
            "baseline_restored": True,
            "cleanup": {
                "verdict": "CLEAN",
                "non_owned_resources_changed": False,
            },
        },
        create_once=True,
    )

    assert _claim_campaign_v225(private_root=private, replacement=True) == "campaign-0002"
    with pytest.raises(FileExistsError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_replacement_is_forbidden_after_any_provider_shadow(tmp_path: Path) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)
    assert _claim_campaign_v225(private_root=private, replacement=False) == "campaign-0001"
    primary = private / "campaign-0001"
    ensure_private_directory(primary)
    write_private_json(
        primary / "blocked-terminal.json",
        {
            "stage": "COMPARATOR_SELECTION",
            "baseline_capture_exists": False,
            "fault_capture_exists": False,
            "provider_shadow_exists": True,
            "replacement_cause": "TELEMETRY",
            "baseline_restored": True,
            "cleanup": {
                "verdict": "CLEAN",
                "non_owned_resources_changed": False,
            },
        },
        create_once=True,
    )

    with pytest.raises(PermissionError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_replacement_is_forbidden_for_preflight_or_implementation_failure(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)
    assert _claim_campaign_v225(private_root=private, replacement=False) == "campaign-0001"
    primary = private / "campaign-0001"
    ensure_private_directory(primary)
    write_private_json(
        primary / "blocked-terminal.json",
        {
            "stage": "STATIC_PREFLIGHT",
            "baseline_capture_exists": False,
            "fault_capture_exists": False,
            "provider_shadow_exists": False,
            "replacement_cause": "NONE",
            "baseline_restored": True,
            "cleanup": {
                "verdict": "CLEAN",
                "non_owned_resources_changed": False,
            },
        },
        create_once=True,
    )

    with pytest.raises(PermissionError):
        _claim_campaign_v225(private_root=private, replacement=True)


def test_final_execution_claim_is_create_once_and_campaign_id_is_closed(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    ensure_private_directory(private)
    manifest = _manifest()

    _claim_final_execution_v225(
        private_root=private, campaign_id="campaign-0001", manifest=manifest
    )
    claim = json.loads(
        (private / "final-execution-claim.json").read_text(encoding="utf-8")
    )
    assert claim["maximum_execution_count"] == 1
    assert claim["manifest_sha256"] == manifest.manifest_sha256
    with pytest.raises(FileExistsError):
        _claim_final_execution_v225(
            private_root=private, campaign_id="campaign-0001", manifest=manifest
        )
    with pytest.raises(ValueError):
        _claim_final_execution_v225(
            private_root=private, campaign_id="campaign-9999", manifest=manifest
        )


def test_cli_rejects_arbitrary_campaign_ids() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "--repository-root",
                "/repo",
                "--provider-env",
                "/env",
                "--private-root",
                "/private",
                "--lease-root",
                "/lease",
                "--campaign-id",
                "campaign-9999",
            ]
        )
