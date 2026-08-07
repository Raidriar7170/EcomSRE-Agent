from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre_rcaeval.sanitize import (
    HoldoutSealError,
    seal_holdout,
    verify_sanitized_holdout,
)
from ecomsre_rcaeval.state import (
    HoldoutState,
    transition_state,
)


def _fake_raw_case(root: Path, name: str, *, inject_time: int) -> None:
    encoded, instance = name.rsplit("_", 1)
    _benchmark, service, fault = encoded.split("_", 2)
    case = root / f"{service}_{fault}" / instance
    case.mkdir(parents=True)
    (case / "inject_time.txt").write_text(f"{inject_time}\n", encoding="utf-8")
    (case / "metrics.csv").write_text(
        "time,checkoutservice_cpu\n1,0.1\n", encoding="utf-8"
    )
    (case / "logs.csv").write_text(
        "time,service,message\n1,checkoutservice,timeout\n", encoding="utf-8"
    )
    (case / "traces.csv").write_text(
        "time,service,peer,duration,error\n1,checkoutservice,cartservice,2.0,0\n",
        encoding="utf-8",
    )


def test_synthetic_sealer_creates_only_opaque_agent_visible_cases(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "synthetic-raw"
    _fake_raw_case(raw, "re2tt_checkoutservice_cpu_1", inject_time=100)
    _fake_raw_case(raw, "re2tt_cartservice_delay_2", inject_time=200)
    sanitized = tmp_path / "sanitized"
    evaluator = tmp_path / "evaluator-only"

    result = seal_holdout(
        raw,
        sanitized,
        evaluator,
        expected_cases=2,
        opaque_seed="synthetic-only-seed",
    )

    assert result.case_count == 2
    assert result.agent_manifest_sha256
    assert result.ground_truth_sha256
    assert sorted(path.name for path in sanitized.iterdir()) == [
        "manifest.json",
        "tt-case-0001",
        "tt-case-0002",
    ]
    agent_manifest = json.loads((sanitized / "manifest.json").read_text())
    encoded_manifest = json.dumps(agent_manifest, sort_keys=True)
    for forbidden in (
        "root_cause_service",
        "fault",
        "instance",
        "checkoutservice_cpu",
        "cartservice_delay",
        str(raw),
    ):
        assert forbidden not in encoded_manifest
    assert all(
        set(item) == {
            "case_id",
            "inject_time",
            "modalities",
            "telemetry_checksums",
        }
        for item in agent_manifest["cases"]
    )
    mapping = json.loads((evaluator / "ground-truth.json").read_text())
    assert {item["fault"] for item in mapping["cases"].values()} == {
        "cpu",
        "delay",
    }
    verify_sanitized_holdout(sanitized, expected_cases=2)


def test_sealer_prefers_bounded_simple_metrics_over_raw_metrics(tmp_path: Path) -> None:
    raw = tmp_path / "synthetic-raw"
    _fake_raw_case(raw, "re2tt_checkoutservice_cpu_1", inject_time=100)
    case = raw / "checkoutservice_cpu" / "1"
    (case / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu\n1,0.1\n",
        encoding="utf-8",
    )
    sanitized = tmp_path / "sanitized"

    seal_holdout(
        raw,
        sanitized,
        tmp_path / "evaluator-only",
        expected_cases=1,
        opaque_seed="synthetic-only-seed",
    )

    opaque_root = sanitized / "tt-case-0001"
    assert (opaque_root / "simple_metrics.csv").is_file()
    assert not (opaque_root / "metrics.csv").exists()


def test_sealer_rejects_symlinks_instead_of_exposing_raw_paths(tmp_path: Path) -> None:
    raw = tmp_path / "synthetic-raw"
    _fake_raw_case(raw, "re2tt_checkoutservice_cpu_1", inject_time=100)
    case = raw / "checkoutservice_cpu" / "1"
    (case / "leak.csv").symlink_to(case / "metrics.csv")

    with pytest.raises(HoldoutSealError, match="symlink"):
        seal_holdout(
            raw,
            tmp_path / "sanitized",
            tmp_path / "evaluator-only",
            expected_cases=1,
            opaque_seed="synthetic-only-seed",
        )


def test_sanitized_verifier_rejects_ground_truth_payload(tmp_path: Path) -> None:
    sanitized = tmp_path / "sanitized"
    case = sanitized / "tt-case-0001"
    case.mkdir(parents=True)
    (case / "metrics.csv").write_text("root_cause_service\n", encoding="utf-8")
    (sanitized / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "rcaeval-re2.agent-manifest.v1",
                "cases": [
                    {
                        "case_id": "tt-case-0001",
                        "inject_time": 1,
                        "modalities": ["metrics"],
                        "telemetry_checksums": {"metrics.csv": "0" * 64},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(HoldoutSealError, match="forbidden evaluator marker"):
        verify_sanitized_holdout(sanitized, expected_cases=1)


def test_sealer_rejects_ninety_cases_without_thirty_by_three_strata(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "synthetic-raw"
    for instance in range(1, 91):
        _fake_raw_case(
            raw,
            f"re2tt_checkoutservice_cpu_{instance}",
            inject_time=instance,
        )

    with pytest.raises(HoldoutSealError, match="30 service-fault strata"):
        seal_holdout(
            raw,
            tmp_path / "sanitized",
            tmp_path / "evaluator-only",
            expected_cases=90,
            opaque_seed="synthetic-only-seed",
        )


def test_holdout_state_machine_is_linear_and_fail_closed() -> None:
    states = tuple(HoldoutState)
    current = states[0]
    for target in states[1:]:
        current = transition_state(current, target)
        assert current is target
    with pytest.raises(ValueError, match="invalid holdout transition"):
        transition_state(HoldoutState.DEV_ONLY, HoldoutState.HOLDOUT_SEALED)
