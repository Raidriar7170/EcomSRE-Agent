from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.phase5b_analysis_v2.protocol import (
    load_analysis_protocol,
    verify_regular_file_sha256,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_v2_protocol_binds_immutable_v1_evidence_and_zero_provider_calls() -> None:
    protocol = load_analysis_protocol(PROJECT_ROOT)

    assert protocol.analysis_version == "phase5b.v2-analysis-contract-repair"
    assert protocol.input_evaluation_version == "phase5b.v1"
    assert (
        protocol.execution_report_sha256
        == "9b8763069df52d0ae66c3b75df3c578db15ed9789b2a69379893b5abaa78837f"
    )
    assert (
        protocol.unblinding_record_sha256
        == "3f16f29cdd178ad39c199e40af09f9e068ef82b7d95235ff7622ecac630f47b4"
    )
    assert (
        protocol.ground_truth_pack_sha256
        == "e9743db596bf580dd5a9b31488e502e472fa9169ea57cbbb8234285c617f6aa5"
    )
    assert protocol.main_run_count == 180
    assert protocol.ablation_gap_count == 38
    assert protocol.provider_calls == 0
    assert protocol.private_difficult_subsets_used is False
    assert protocol.subset_mapping_source.endswith("::_SUBSETS_BY_TEMPLATE")
    assert protocol.analysis_executed is False
    assert protocol.review_required is True


def test_v2_protocol_hashes_match_the_frozen_v1_repository_files() -> None:
    protocol = load_analysis_protocol(PROJECT_ROOT)

    assert (
        verify_regular_file_sha256(
            PROJECT_ROOT / "config/phase5b-execution/execution-freeze.v1.json",
            expected_sha256=protocol.execution_freeze_sha256,
        )
        == protocol.execution_freeze_sha256
    )
    assert (
        verify_regular_file_sha256(
            PROJECT_ROOT / "config/phase5b/execution-schedule.v1.json",
            expected_sha256=protocol.execution_schedule_sha256,
        )
        == protocol.execution_schedule_sha256
    )
    assert (
        verify_regular_file_sha256(
            PROJECT_ROOT / "config/phase5b/freeze-manifest.v1.json",
            expected_sha256=protocol.protocol_freeze_manifest_sha256,
        )
        == protocol.protocol_freeze_manifest_sha256
    )
    assert (
        verify_regular_file_sha256(
            PROJECT_ROOT / "config/phase5b-seal/hidden-pack-seal.v1.json",
            expected_sha256=protocol.hidden_pack_seal_record_sha256,
        )
        == protocol.hidden_pack_seal_record_sha256
    )


def test_v2_binding_fails_closed_on_any_file_byte_drift(tmp_path: Path) -> None:
    bound = tmp_path / "bound.json"
    bound.write_bytes(b"immutable\n")
    expected = hashlib.sha256(bound.read_bytes()).hexdigest()

    verify_regular_file_sha256(bound, expected_sha256=expected)
    bound.write_bytes(b"drifted\n")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_regular_file_sha256(bound, expected_sha256=expected)


def test_v2_binding_rejects_symlinked_input(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"immutable\n")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="regular non-symlink"):
        verify_regular_file_sha256(
            link,
            expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        )
