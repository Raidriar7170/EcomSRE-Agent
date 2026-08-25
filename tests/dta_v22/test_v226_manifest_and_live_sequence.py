from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.real_fault_cli_v226 import (
    RealFaultLiveSequenceErrorV226,
    run_live_capture_sequence_v226,
)
from ecomsre.dta_v2.v22.real_fault_manifest_v226 import (
    PREDECESSOR_ALIASES_V225,
    build_alias_map_set_v226,
    build_case_set_v226,
    build_manifest_v226,
    build_pre_live_freeze_v226,
    build_public_alias_map_set_v226,
    build_truth_set_v226,
    generate_opaque_service_aliases_v226,
)
from ecomsre.dta_v2.v22.real_fault_study_v226 import RealFaultCaseTruthV226


ROOT = Path(__file__).resolve().parents[2]


def _capture(case_id: str):
    from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1

    return RealFaultOpaqueCaptureV1.model_validate_json(
        (ROOT / f"config/dta-v225-real-fault/captures/{case_id}.json").read_bytes()
    )


def test_v226_manifest_freezes_new_swapped_aliases_and_exact_schedule() -> None:
    aliases = generate_opaque_service_aliases_v226()
    maps = build_alias_map_set_v226(comparator_service="recommendation")
    public = build_public_alias_map_set_v226(private_maps=maps)

    assert aliases != PREDECESSOR_ALIASES_V225
    assert maps.aliases == aliases == public.aliases
    assert maps.maps[0].alias_for("ad") != maps.maps[1].alias_for("ad")
    assert public.exact_two_way_swap is True

    freeze = build_pre_live_freeze_v226(
        code_head="1" * 40,
        provider_model="gpt-5.4-mini-2026-03-17",
        comparator_service="recommendation",
        alias_map_set_sha256=maps.set_sha256,
        selection_prompt_sha256="2" * 64,
        terminalizer_sha256="3" * 64,
        scorer_sha256="4" * 64,
        provider_development_summary_sha256="5" * 64,
        provider_gate_iteration_sha256=(
            "d0ca56b5b6d03faf8135fc7d5dca1568ef911935c6d2655902b1716749e9dbec"
        ),
        pre_live_review_sha256="6" * 64,
    )
    captures = tuple(
        _capture(case_id)
        for case_id in (
            "fault-map-a",
            "fault-map-b",
            "baseline-map-a",
            "baseline-map-b",
        )
    )
    case_set = build_case_set_v226(captures=captures)
    truths = tuple(
        RealFaultCaseTruthV226(
            schema_version="dta-v226-real-fault.case-truth.v1",
            case_id=case_id,
            case_kind="AD_CPU_FAULT" if case_id.startswith("fault-") else "BASELINE",
            expected_root_alias=(aliases[0] if case_id.startswith("fault-") else None),
            expected_fault_domain=(
                "LOCAL_RESOURCE" if case_id.startswith("fault-") else None
            ),
            expected_mechanism=(
                "CPU_SATURATION" if case_id.startswith("fault-") else None
            ),
        )
        for case_id in (
            "fault-map-a",
            "fault-map-b",
            "baseline-map-a",
            "baseline-map-b",
        )
    )
    truth_set = build_truth_set_v226(truths=truths)
    manifest = build_manifest_v226(
        pre_live_freeze=freeze,
        capture_pair_sha256="7" * 64,
        case_set_sha256=case_set.case_set_sha256,
        truth_set_sha256=truth_set.truth_set_sha256,
        execution_id="exec-v226-0123456789abcdef",
    )

    assert len(freeze.schedule) == 8
    assert freeze.maximum_final_execution_count == 1
    assert manifest.manifest_sha256 == manifest.recompute_sha256()


class _AdmissionFailedLifecycle:
    run_id = "a" * 32

    def __init__(self) -> None:
        self.cleanup_calls = 0

    def admit_start_and_wait(self) -> None:
        raise RuntimeError("injected admission failure")

    def restore_and_cleanup(self):
        self.cleanup_calls += 1
        return True, {
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        }


def test_v226_live_sequence_always_attempts_cleanup_after_admission_failure() -> None:
    lifecycle = _AdmissionFailedLifecycle()

    with pytest.raises(RealFaultLiveSequenceErrorV226) as captured:
        run_live_capture_sequence_v226(
            lifecycle=lifecycle,
            campaign_id="campaign-0001",
            code_head="1" * 40,
            model_id="gpt-5.4-mini-2026-03-17",
            prepare=lambda _comparator: pytest.fail("prepare must not run"),
            current_provider_factory=lambda: pytest.fail("Provider must not run"),
            physical_observer=lambda _capture: pytest.fail("capture must not run"),
        )

    assert lifecycle.cleanup_calls == 1
    assert captured.value.stage == "ADMISSION"
    assert captured.value.baseline_restored is True
    assert captured.value.cleanup is not None
    assert captured.value.cleanup.verdict == "CLEAN"
