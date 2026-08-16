from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from ecomsre.dta_v2.evaluation_contracts import EvaluationArm, build_held_out_seal
from ecomsre.dta_v2.held_out_campaign import (
    build_frozen_input_hashes,
    build_held_out_schedule,
)


ROOT = Path(__file__).resolve().parents[2]


def _seal():
    return build_held_out_seal(
        base_head="a" * 40,
        model_id="gpt-5.4-2026-03-05",
        agent_identity_sha256="1" * 64,
        one_shot_prompt_sha256="2" * 64,
        adaptive_prompt_sha256="3" * 64,
        tool_schema_sha256="4" * 64,
        budgets_sha256="5" * 64,
        diagnosis_schema_sha256="6" * 64,
        runbook_registry_sha256="7" * 64,
        candidate_filter_sha256="8" * 64,
        action_schema_sha256="9" * 64,
        scorer_sha256="a" * 64,
        held_out_case_sha256s=("b" * 64, "c" * 64, "d" * 64),
        evaluator_truth_sha256s=("e" * 64, "f" * 64, "0" * 64),
    )


def test_held_out_schedule_is_exact_sealed_six_entry_freeze() -> None:
    seal = _seal()
    schedule = build_held_out_schedule(
        execution_id="1" * 32,
        seal=seal,
        case_bindings=(
            ("dta-case-007", "b" * 64, "e" * 64),
            ("dta-case-008", "c" * 64, "f" * 64),
            ("dta-case-009", "d" * 64, "0" * 64),
        ),
    )

    assert len(schedule.entries) == 6
    assert tuple(item.ordinal for item in schedule.entries) == tuple(range(1, 7))
    assert Counter(item.case_id for item in schedule.entries) == {
        "dta-case-007": 2,
        "dta-case-008": 2,
        "dta-case-009": 2,
    }
    assert all(
        schedule.entries[index].arm is EvaluationArm.ONE_SHOT_FULL_CONTEXT
        and schedule.entries[index + 1].arm is EvaluationArm.ADAPTIVE_TOOL_USING
        for index in range(0, 6, 2)
    )
    assert schedule.seal_sha256 == seal.seal_sha256
    assert schedule.schedule_sha256 != "0" * 64


def test_held_out_schedule_rejects_digest_projection_drift() -> None:
    with pytest.raises(ValueError, match="projection"):
        build_held_out_schedule(
            execution_id="1" * 32,
            seal=_seal(),
            case_bindings=(
                ("dta-case-007", "b" * 64, "e" * 64),
                ("dta-case-008", "c" * 64, "f" * 64),
                ("dta-case-009", "1" * 64, "0" * 64),
            ),
        )


def test_frozen_input_hashes_bind_distinct_arms_and_source_contracts() -> None:
    frozen = build_frozen_input_hashes(ROOT)

    assert frozen.one_shot_prompt_sha256 != frozen.adaptive_prompt_sha256
    assert frozen.candidate_filter_sha256 != "0" * 64
    assert frozen.scorer_sha256 != "0" * 64
    assert frozen.budgets_sha256 != "0" * 64
