from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.phase5b_analysis_v2.runner import (
    create_exclusive_analysis_attempt,
    reject_forbidden_environment,
    require_review_binding,
    require_v2_analysis_authorization,
    run_v2_analysis,
    validate_separate_output_root,
)


AUTHORIZED_ENVIRONMENT = {
    "PHASE5B_EXECUTION_AUTHORIZATION": "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION",
    "PHASE5B_V2_ANALYSIS_AUTHORIZATION": "AUTHORIZE_PHASE5B_V2_ANALYSIS_ONLY",
}


def test_v2_analysis_requires_exact_separate_authorization() -> None:
    with pytest.raises(PermissionError, match="v2 analysis authorization"):
        require_v2_analysis_authorization(
            {
                "PHASE5B_EXECUTION_AUTHORIZATION": (
                    "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION"
                )
            }
        )

    require_v2_analysis_authorization(AUTHORIZED_ENVIRONMENT)


def test_v2_analysis_requires_exact_v1_execution_authorization() -> None:
    with pytest.raises(PermissionError, match="v1 execution authorization"):
        require_v2_analysis_authorization(
            {
                "PHASE5B_V2_ANALYSIS_AUTHORIZATION": (
                    "AUTHORIZE_PHASE5B_V2_ANALYSIS_ONLY"
                )
            }
        )


def test_read_only_preflight_rejects_forbidden_environment() -> None:
    with pytest.raises(PermissionError, match="forbidden environment"):
        reject_forbidden_environment({"PHASE5B_EVALUATOR_MODE": "present"})


@pytest.mark.parametrize(
    "forbidden_name",
    (
        "PHASE5B_HIDDEN_PACK_ROOT",
        "ECOMSRE_PHASE5B_HIDDEN_PACK_ROOT",
        "PHASE5B_BUILDER_TOKEN",
        "PHASE5B_EVALUATOR_MODE",
    ),
)
def test_v2_analysis_rejects_forbidden_role_or_pack_environment(
    forbidden_name: str,
) -> None:
    with pytest.raises(PermissionError, match="forbidden environment"):
        require_v2_analysis_authorization(
            {
                "PHASE5B_EXECUTION_AUTHORIZATION": (
                    "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION"
                ),
                "PHASE5B_V2_ANALYSIS_AUTHORIZATION": (
                    "AUTHORIZE_PHASE5B_V2_ANALYSIS_ONLY"
                ),
                forbidden_name: "present",
            }
        )


def test_v2_output_root_must_be_separate_from_every_input_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    v1_source = tmp_path / "v1-source"
    v1_execution = tmp_path / "v1-execution"
    truth = tmp_path / "truth"
    for root in (project, v1_source, v1_execution, truth):
        root.mkdir()

    protected = (project, v1_source, v1_execution, truth)
    for root in protected:
        with pytest.raises(ValueError, match="separate from immutable inputs"):
            validate_separate_output_root(root / "nested-output", protected)

    selected = tmp_path / "v2-output"
    assert validate_separate_output_root(selected, protected) == selected


def test_v2_output_root_rejects_symlink(tmp_path: Path) -> None:
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    linked_output = tmp_path / "linked-output"
    linked_output.symlink_to(real_output, target_is_directory=True)

    with pytest.raises(ValueError, match="real directory"):
        validate_separate_output_root(linked_output, ())


def test_review_binding_rejects_raw_manifest_drift() -> None:
    require_review_binding(
        reviewed_raw_record_manifest_sha256="a" * 64,
        admitted_raw_record_manifest_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="reviewed raw-record manifest"):
        require_review_binding(
            reviewed_raw_record_manifest_sha256="a" * 64,
            admitted_raw_record_manifest_sha256="b" * 64,
        )


def test_analysis_attempt_is_exclusive_after_partial_first_attempt(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "state" / "analysis-attempt.v2.json"
    create_exclusive_analysis_attempt(marker, b"first-attempt\n")

    assert marker.read_bytes() == b"first-attempt\n"
    with pytest.raises(FileExistsError):
        create_exclusive_analysis_attempt(marker, b"second-attempt\n")
    assert marker.read_bytes() == b"first-attempt\n"


def test_runner_verifies_reviewed_manifest_before_any_scoring(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    admitted = SimpleNamespace(raw_record_manifest_sha256="a" * 64)
    reviewed = SimpleNamespace(raw_record_manifest_sha256="b" * 64)
    monkeypatch.setattr(
        "scripts.phase5b_analysis_v2.runner.preflight_v2_analysis",
        lambda **_: admitted,
    )
    monkeypatch.setattr(
        "scripts.phase5b_analysis_v2.runner.verify_review_disposition",
        lambda _: reviewed,
    )
    monkeypatch.setattr(
        "scripts.phase5b_analysis_v2.runner.build_v2_scoring_bundle",
        lambda _: pytest.fail("scoring ran before review binding"),
    )

    with pytest.raises(ValueError, match="reviewed raw-record manifest"):
        run_v2_analysis(
            project_root=tmp_path,
            v1_source_root=tmp_path,
            v1_execution_root=tmp_path,
            hidden_ground_truth_root=tmp_path,
            output_root=tmp_path / "output",
            environment=AUTHORIZED_ENVIRONMENT,
        )
