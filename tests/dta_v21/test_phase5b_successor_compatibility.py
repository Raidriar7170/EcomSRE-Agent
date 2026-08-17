from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.ci.verify_phase5b_execution_historical_bindings import (
    SUCCESSOR_MAKEFILE_SHA256,
    _historical_git_blob,
    _verify_successor_makefile_bytes,
    verify_historical_execution_bindings,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase5b_execution_harness_remains_historically_bound() -> None:
    manifest = verify_historical_execution_bindings(REPO_ROOT)

    assert manifest.evaluation_version == "phase5b.v1"
    assert len(manifest.harness_files) == 28
    assert manifest.harness_files["Makefile"] == (
        "8f6e1c07d8bd5a139390c3a6e1f883e2736658b6483e02ef9fa9421ad5984eaa"
    )


@pytest.mark.parametrize(
    "changed",
    (
        lambda current: current + b"\n",
        lambda current: current.replace(
            b"DTA_V21_HISTORICAL_BINDINGS_CLI :=",
            b"DTA_V21_EVIL := $(shell arbitrary-host-command)\n"
            b"DTA_V21_HISTORICAL_BINDINGS_CLI :=",
            1,
        ),
        lambda current: current.replace(
            b"\t$(DTA_V21_HISTORICAL_BINDINGS_CLI)\n",
            b"\tarbitrary-host-command\n",
            1,
        ),
        lambda current: current.replace(
            b"# BEGIN DTA_V21_SUCCESSOR_TARGETS",
            b"# BEGIN DRIFTED_SUCCESSOR_TARGETS",
            1,
        ),
    ),
)
def test_successor_makefile_rejects_any_suffix_byte_drift(
    changed: Callable[[bytes], bytes],
) -> None:
    historical = _historical_git_blob(REPO_ROOT, "Makefile")
    current = (REPO_ROOT / "Makefile").read_bytes()

    with pytest.raises(ValueError, match="successor Makefile bytes changed"):
        _verify_successor_makefile_bytes(
            historical,
            changed(current),
            expected_historical_sha256=(
                "8f6e1c07d8bd5a139390c3a6e1f883e2736658b6483e02ef9fa9421ad5984eaa"
            ),
            expected_current_sha256=SUCCESSOR_MAKEFILE_SHA256,
        )


def test_successor_makefile_rejects_historical_prefix_drift() -> None:
    historical = _historical_git_blob(REPO_ROOT, "Makefile")
    current = (REPO_ROOT / "Makefile").read_bytes()

    with pytest.raises(ValueError, match="historical execution harness drift"):
        _verify_successor_makefile_bytes(
            b"X" + historical[1:],
            current,
            expected_historical_sha256=(
                "8f6e1c07d8bd5a139390c3a6e1f883e2736658b6483e02ef9fa9421ad5984eaa"
            ),
            expected_current_sha256=SUCCESSOR_MAKEFILE_SHA256,
        )
