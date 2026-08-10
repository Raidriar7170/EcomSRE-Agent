from __future__ import annotations

from pathlib import Path
import sys

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.rca100.build_repair_report import (  # noqa: E402
    METHOD_STATUS,
    human_brief,
    markdown,
)
from scripts.rca100.evaluator_repair import (  # noqa: E402
    REPAIR_STATE_CHAIN,
    RepairEnvironment,
    advance_repair_state,
    current_repair_state,
)


def _synthetic_public_report() -> dict[str, object]:
    return {
        "status": (
            "RCA100_EVALUATOR_REPAIR_FINAL_REPORT_FROZEN_READY_FOR_"
            "PUBLICATION_REVIEW"
        ),
        "evaluation_method_status": METHOD_STATUS,
        "classification": "RCA100_EXTERNAL_M3_POSITIVE_INCONCLUSIVE",
        "primary": {
            "initial_correct": 40,
            "final_correct": 45,
            "point_difference": 0.05,
            "ci_lower": -0.01,
            "ci_upper": 0.11,
            "mcnemar_exact_p_value": 0.25,
            "damage": 2,
            "rescue": 7,
            "net_rescue": 5,
            "damage_rate": 0.05,
            "damage_rate_denominator": 40,
        },
        "secondary_pair": {
            "initial_correct": 30,
            "final_correct": 33,
            "damage": 2,
            "rescue": 5,
            "net_rescue": 3,
        },
        "m3": {
            "keep": 60,
            "override": 43,
            "correct_override": 7,
            "wrong_override": 2,
        },
        "execution": {
            "completed": 99,
            "provider_attempts": 103,
            "transport_retries": 0,
        },
    }


def _repair_environment(tmp_path: Path) -> tuple[dict[str, str], Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    names = {
        "RCA100_INPUT_SOURCE_ROOT": "input",
        "RCA100_CONTROL_ROOT": "control",
        "RCA100_PRIVATE_SCHEDULE_ROOT": "schedule",
        "RCA100_JOURNAL_ROOT": "journal",
        "RCA100_OUTPUT_ROOT": "output",
        "RCA100_EVALUATOR_SOURCE_ROOT": "evaluator-source",
        "RCA100_EVALUATOR_ROOT": "evaluator",
        "RCA100_EVALUATOR_REPAIR_CONTROL_ROOT": "repair-control",
    }
    environment: dict[str, str] = {}
    for variable, directory in names.items():
        path = tmp_path / directory
        path.mkdir()
        environment[variable] = str(path)
    return environment, repository


def test_repair_state_chain_is_contiguous_and_create_once(tmp_path: Path) -> None:
    assert current_repair_state(tmp_path) is None
    with pytest.raises(ValueError, match="transition is out of order"):
        advance_repair_state(
            tmp_path,
            "REPAIR_IMPLEMENTATION_FROZEN",
            bindings={"synthetic": True},
        )

    for state in REPAIR_STATE_CHAIN:
        advance_repair_state(
            tmp_path,
            state,
            bindings={"synthetic": True},
        )
        assert current_repair_state(tmp_path) == state

    with pytest.raises(ValueError, match="transition is out of order"):
        advance_repair_state(
            tmp_path,
            "REPAIR_FINAL_REPORT_FROZEN",
            bindings={"synthetic": True},
        )


def test_repair_state_chain_rejects_noncontiguous_records(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "REPAIR_SOURCE_SNAPSHOT_LOCKED.json").write_text(
        "{}", encoding="utf-8"
    )
    (state_root / "REPAIR_IMPLEMENTATION_FROZEN.json").write_text(
        "{}", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="non-contiguous"):
        current_repair_state(tmp_path)


def test_repair_environment_requires_disjoint_external_control_root(
    tmp_path: Path,
) -> None:
    environment, repository = _repair_environment(tmp_path)

    repair = RepairEnvironment.from_environment(
        environment, repository_root=repository
    )

    assert repair.repair_control == tmp_path / "repair-control"
    environment["RCA100_EVALUATOR_REPAIR_CONTROL_ROOT"] = environment[
        "RCA100_CONTROL_ROOT"
    ]
    with pytest.raises(ValueError, match="overlap"):
        RepairEnvironment.from_environment(environment, repository_root=repository)


def test_public_summaries_disclose_the_complete_repair_boundary() -> None:
    report = _synthetic_public_report()

    for summary in (markdown(report), human_brief(report)):
        assert "Post-lock Evaluator Repair Disclosure" in summary
        assert METHOD_STATUS in summary
        assert "BLOCKED_PROTOCOL_DRIFT" in summary
        assert "task_to_case_id" in summary
        assert "scorer" in summary
        assert "entity matching" in summary
        assert "statistics" in summary
        assert "denominator" in summary
