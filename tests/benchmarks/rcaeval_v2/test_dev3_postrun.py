from __future__ import annotations

from pathlib import Path
import json

import pytest
from pydantic import ValidationError

from ecomsre_rcaeval_v2.dev3_completion import (
    DesignCompletionAmendmentLock,
    DesignCompletionFinalizationLock,
    _validate_amendment_paths,
    _validate_finalization_paths,
    _validate_original_inconsistent_gate,
)
from ecomsre_rcaeval_v2.dev3_postrun import (
    PostRunEvaluationLock,
    _fixed_evidence_bindings,
    _validate_postrun_paths,
)


_HASH = "a" * 64


def _lock_payload() -> dict[str, object]:
    return {
        "schema_version": (
            "rcaeval-re2-v2-dev3.postrun-evaluation-successor-lock.v1"
        ),
        "protocol_id": "rcaeval-re2-v2-dev.3",
        "parent_evaluation_root_lock_sha256": _HASH,
        "parent_implementation_commit": "b" * 40,
        "evaluation_commit": "c" * 40,
        "draft_pr_number": 17,
        "draft_pr_url": "https://github.invalid/example/pull/17",
        "required_ci_checks": (
            {
                "workflow": "Agent mainline",
                "name": "test",
                "state": "SUCCESS",
                "bucket": "pass",
                "link": "https://github.invalid/check/1",
            },
        ),
        "changed_paths": ("src/ecomsre_rcaeval_v2/evaluation.py",),
        "postrun_diff_sha256": _HASH,
        "source_tree_hashes": {"runtime": _HASH},
        "config_hashes": {"protocol.json": _HASH},
        "smoke_schedule_sha256": _HASH,
        "design_schedule_sha256": _HASH,
        "validation_schedule_sha256": _HASH,
        "schedule_set_sha256": _HASH,
        "private_schedule_root_identity_sha256": _HASH,
        "private_output_root_identity_sha256": _HASH,
        "smoke_journal_root_identity_sha256": _HASH,
        "design_journal_root_identity_sha256": _HASH,
        "dev2_failure_audit_lock_sha256": _HASH,
        "schedule_admission_lock_sha256": _HASH,
        "f0_public_sha256": _HASH,
        "f0_private_sha256": _HASH,
        "schedule_admission_gate_sha256": _HASH,
        "provider_smoke_gate_sha256": _HASH,
        "smoke_journal_tree_sha256": _HASH,
        "design_journal_tree_sha256": _HASH,
        "combined_design_journal_tree_sha256": _HASH,
        "created_at_utc": "2026-08-10T00:00:00+00:00",
        "provider_access_authorized": False,
        "provider_calls_authorized": 0,
    }


def _amendment_payload() -> dict[str, object]:
    return {
        "schema_version": (
            "rcaeval-re2-v2-dev3.design-completion-amendment-lock.v1"
        ),
        "protocol_id": "rcaeval-re2-v2-dev.3",
        "postrun_evaluation_lock_sha256": _HASH,
        "postrun_evaluation_commit": "b" * 40,
        "amendment_commit": "c" * 40,
        "draft_pr_number": 17,
        "draft_pr_url": "https://github.invalid/example/pull/17",
        "required_ci_checks": (),
        "changed_paths": ("src/ecomsre_rcaeval_v2/dev3_evidence.py",),
        "amendment_diff_sha256": _HASH,
        "source_tree_hashes": {"runtime": _HASH},
        "config_hashes": {"protocol.json": _HASH},
        "frozen_evidence_hashes": {"design_journal_tree_sha256": _HASH},
        "original_design_output_hashes": {"design_gate_sha256": _HASH},
        "created_at_utc": "2026-08-10T00:00:00+00:00",
        "provider_access_authorized": False,
        "provider_calls_authorized": 0,
    }


def _finalization_payload() -> dict[str, object]:
    return {
        "schema_version": (
            "rcaeval-re2-v2-dev3.design-completion-finalization-lock.v1"
        ),
        "protocol_id": "rcaeval-re2-v2-dev.3",
        "completion_amendment_lock_sha256": _HASH,
        "completion_amendment_commit": "b" * 40,
        "finalization_commit": "c" * 40,
        "draft_pr_number": 17,
        "draft_pr_url": "https://github.invalid/example/pull/17",
        "required_ci_checks": (),
        "changed_paths": ("src/ecomsre_rcaeval_v2/dev3_evidence.py",),
        "finalization_diff_sha256": _HASH,
        "source_tree_hashes": {"runtime": _HASH},
        "config_hashes": {"protocol.json": _HASH},
        "frozen_evidence_hashes": {"design_journal_tree_sha256": _HASH},
        "original_design_output_hashes": {"design_gate_sha256": _HASH},
        "created_at_utc": "2026-08-10T00:00:00+00:00",
        "provider_access_authorized": False,
        "provider_calls_authorized": 0,
    }


def test_postrun_diff_is_exactly_bounded_to_the_projection_repair() -> None:
    _validate_postrun_paths(
        (
            "src/ecomsre_rcaeval_v2/evaluation.py",
            "src/ecomsre_rcaeval_v2/dev3_postrun.py",
            "scripts/rcaeval_v2/evaluate_dev3_design.py",
            "scripts/rcaeval_v2/prepare_dev3_postrun_evaluation.py",
            "scripts/rcaeval_v2/publish_dev3_results.py",
            "tests/benchmarks/rcaeval_v2/test_evaluation.py",
            "tests/benchmarks/rcaeval_v2/test_dev3_postrun.py",
            "tests/benchmarks/rcaeval_v2/test_dev3_provider_gates.py",
        )
    )
    with pytest.raises(ValueError, match="unauthorized path"):
        _validate_postrun_paths(
            (
                "src/ecomsre_rcaeval_v2/evaluation.py",
                "src/ecomsre_rcaeval_v2/dev4_provider.py",
            )
        )
    with pytest.raises(ValueError, match="lacks the aggregate projection repair"):
        _validate_postrun_paths(("src/ecomsre_rcaeval_v2/dev3_postrun.py",))


@pytest.mark.parametrize(
    ("name", "value"),
    (("provider_access_authorized", True), ("provider_calls_authorized", 1)),
)
def test_postrun_lock_cannot_authorize_provider_access(
    name: str, value: object
) -> None:
    payload = _lock_payload()
    payload[name] = value
    with pytest.raises(ValidationError):
        PostRunEvaluationLock.model_validate(payload)


def test_postrun_lock_binds_all_frozen_journal_trees(tmp_path: Path) -> None:
    control = tmp_path / "control"
    output = tmp_path / "output"
    smoke = tmp_path / "smoke"
    design = tmp_path / "design"
    combined = tmp_path / "combined"
    files = (
        control / "locks/dev2-provider-failure-audit.json",
        control / "locks/schedule-admission-lock.json",
        control / "evidence/f0-public.json",
        output / "evidence/f0-private.json",
        control / "evidence/schedule-admission-gate.json",
        control / "evidence/provider-smoke-gate.json",
        smoke / "run.json",
        design / "run.json",
        combined / "run.json",
    )
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    before = _fixed_evidence_bindings(
        control_root=control,
        output_root=output,
        smoke_journal_root=smoke,
        design_journal_root=design,
        combined_root=combined,
    )
    (design / "run.json").write_text('{"changed":true}\n', encoding="utf-8")
    after = _fixed_evidence_bindings(
        control_root=control,
        output_root=output,
        smoke_journal_root=smoke,
        design_journal_root=design,
        combined_root=combined,
    )

    assert before["smoke_journal_tree_sha256"] == after[
        "smoke_journal_tree_sha256"
    ]
    assert before["combined_design_journal_tree_sha256"] == after[
        "combined_design_journal_tree_sha256"
    ]
    assert before["design_journal_tree_sha256"] != after[
        "design_journal_tree_sha256"
    ]


def test_completion_amendment_is_bounded_and_cannot_authorize_provider() -> None:
    _validate_amendment_paths(
        (
            "src/ecomsre_rcaeval_v2/dev3_completion.py",
            "src/ecomsre_rcaeval_v2/dev3_evidence.py",
            "scripts/rcaeval_v2/correct_dev3_design_gate.py",
            "scripts/rcaeval_v2/prepare_dev3_completion_amendment.py",
            "scripts/rcaeval_v2/publish_dev3_results.py",
            "tests/benchmarks/rcaeval_v2/test_dev3_postrun.py",
            "tests/benchmarks/rcaeval_v2/test_dev3_provider_gates.py",
        )
    )
    with pytest.raises(ValueError, match="unauthorized path"):
        _validate_amendment_paths(
            (
                "src/ecomsre_rcaeval_v2/dev3_completion.py",
                "src/ecomsre_rcaeval_v2/dev3_evidence.py",
                "src/ecomsre_rcaeval_v2/dev4_provider.py",
            )
        )
    for name, value in (
        ("provider_access_authorized", True),
        ("provider_calls_authorized", 1),
    ):
        payload = _amendment_payload()
        payload[name] = value
        with pytest.raises(ValidationError):
            DesignCompletionAmendmentLock.model_validate(payload)


def test_completion_amendment_preserves_the_exact_original_mismatch(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    gate = {
        "state": "V2_DEV3_DESIGN_GATE_PASSED",
        "checks": {
            "terminal_accounting": {"passed": True},
            "semantic_failure_attribution": {
                "numerator": 7,
                "denominator": 7,
                "passed": True,
            },
            "final_judge_schema_dev3": {
                "invalid_schema_count": 7,
                "passed": False,
            },
        },
        "source_bindings": {
            "postrun_evaluation_lock_sha256": _HASH,
        },
    }
    aggregate = {
        "exact_failure_taxonomy": [
            {
                "operation_type": "FINAL_JUDGE",
                "failure_stage": "OUTPUT_VALIDATION",
                "failure_code": "PROVIDER_OUTPUT_INVALID_SCHEMA",
                "count": 7,
            }
        ],
        "source_bindings": {
            "postrun_evaluation_lock_sha256": _HASH,
        },
    }
    (evidence / "design-gate.json").write_text(
        json.dumps(gate), encoding="utf-8"
    )
    (evidence / "design-aggregate.json").write_text(
        json.dumps(aggregate), encoding="utf-8"
    )

    _validate_original_inconsistent_gate(
        tmp_path, postrun_lock_sha256=_HASH
    )

    aggregate["exact_failure_taxonomy"][0]["count"] = 6
    (evidence / "design-aggregate.json").write_text(
        json.dumps(aggregate), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="lack exact attribution"):
        _validate_original_inconsistent_gate(
            tmp_path, postrun_lock_sha256=_HASH
        )


def test_completion_finalization_is_bounded_and_cannot_authorize_provider() -> None:
    _validate_finalization_paths(
        (
            "src/ecomsre_rcaeval_v2/dev3_completion.py",
            "src/ecomsre_rcaeval_v2/dev3_evidence.py",
            "scripts/rcaeval_v2/correct_dev3_design_gate.py",
            "scripts/rcaeval_v2/prepare_dev3_completion_finalization.py",
            "scripts/rcaeval_v2/publish_dev3_results.py",
            "tests/benchmarks/rcaeval_v2/test_dev3_postrun.py",
            "tests/benchmarks/rcaeval_v2/test_dev3_provider_gates.py",
        )
    )
    with pytest.raises(ValueError, match="unauthorized path"):
        _validate_finalization_paths(
            (
                "src/ecomsre_rcaeval_v2/dev3_completion.py",
                "src/ecomsre_rcaeval_v2/dev3_evidence.py",
                "src/ecomsre_rcaeval_v2/dev4_provider.py",
            )
        )
    for name, value in (
        ("provider_access_authorized", True),
        ("provider_calls_authorized", 1),
    ):
        payload = _finalization_payload()
        payload[name] = value
        with pytest.raises(ValidationError):
            DesignCompletionFinalizationLock.model_validate(payload)
