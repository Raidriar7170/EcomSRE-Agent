from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ecomsre.dta_v2.v21.capture_campaign import build_default_capture_plan_v21
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.evaluation_agents import EvaluationEntryResultV21
from ecomsre.dta_v2.v21.evaluation_campaign import (
    ABLATION_CASE_IDS_V21,
    PlannerAdvantageThresholdsV21,
    build_development_report_v21,
    build_evaluation_freeze_manifest_v21,
    build_held_out_report_v21,
    build_evaluation_preregistration_v21,
    build_evaluation_schedule_v21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    EvaluationArmV21,
    EvaluationPredictionV21,
    PublicCaseBindingV21,
    PublicEvaluationManifestV21,
    build_evaluation_score_v21,
)
from ecomsre.dta_v2.v21.evaluation_freeze_cli import (
    SCHEDULE_SEED_V21,
    preregister_evaluation_v21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.owned_capture import build_evaluator_truth_v21


MODEL = "gpt-5.4-mini-2026-03-17"


class _TestGitRunner:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root

    def run(self, arguments: tuple[str, ...], *, timeout_seconds: float):
        completed = subprocess.run(
            arguments,
            cwd=self.repository_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return SimpleNamespace(
            exit_code=completed.returncode,
            stdout=completed.stdout,
        )


def test_schedule_freezes_36_primary_4_ablation_and_24_held_out_entries() -> None:
    seed = semantic_sha256("dta-v21-p0-master-v1-evaluation-schedule")
    first = build_evaluation_schedule_v21(seed_sha256=seed)
    second = build_evaluation_schedule_v21(seed_sha256=seed)

    assert first == second
    assert len(first.entries) == 64
    assert tuple(item.case_id for item in first.entries[36:40]) != ABLATION_CASE_IDS_V21
    assert {item.case_id for item in first.entries[36:40]} == set(ABLATION_CASE_IDS_V21)
    assert all(int(item.case_id[-3:]) <= 12 for item in first.entries[:40])
    assert all(int(item.case_id[-3:]) >= 13 for item in first.entries[40:])


def test_preregistration_thresholds_are_exact_and_hash_bound() -> None:
    schedule = build_evaluation_schedule_v21(
        seed_sha256=semantic_sha256("frozen schedule")
    )
    preregistration = build_evaluation_preregistration_v21(
        model_id=MODEL,
        max_completion_tokens=1600,
        schedule_sha256=schedule.schedule_sha256,
    )

    assert preregistration.thresholds.mechanism_macro_f1_minimum_delta == 0.10
    assert preregistration.thresholds.planner_mean_input_token_ratio_maximum == 0.75
    assert preregistration.primary_scored_entry_count == 24
    with pytest.raises(ValueError, match="threshold"):
        PlannerAdvantageThresholdsV21(mechanism_macro_f1_minimum_delta=0.05)


def test_preregistration_cli_surface_is_deterministic_and_create_once(
    tmp_path: Path,
) -> None:
    schedule, preregistration = preregister_evaluation_v21(
        evaluation_config_root=tmp_path,
        model_id=MODEL,
        max_completion_tokens=1600,
    )

    assert schedule.seed_sha256 == SCHEDULE_SEED_V21
    assert preregistration.schedule_sha256 == schedule.schedule_sha256
    assert (tmp_path / "schedule.v1.json").stat().st_mode & 0o777 == 0o644
    with pytest.raises(FileExistsError):
        preregister_evaluation_v21(
            evaluation_config_root=tmp_path,
            model_id=MODEL,
            max_completion_tokens=1600,
        )


def test_freeze_manifest_binds_exact_sources_identities_and_answer_free_cases() -> None:
    schedule = build_evaluation_schedule_v21(
        seed_sha256=semantic_sha256("freeze manifest test")
    )
    preregistration = build_evaluation_preregistration_v21(
        model_id=MODEL,
        max_completion_tokens=1600,
        schedule_sha256=schedule.schedule_sha256,
    )
    development = tuple(
        PublicCaseBindingV21(
            case_id=f"dta21-case-{index:03d}",
            case_sha256=semantic_sha256({"case": index}),
            truth_sha256=semantic_sha256({"truth": index}),
            split_sha256=semantic_sha256("DEVELOPMENT"),
        )
        for index in range(1, 13)
    )
    held_out = tuple(
        PublicCaseBindingV21(
            case_id=f"dta21-case-{index:03d}",
            case_sha256=semantic_sha256({"case": index}),
            truth_sha256=semantic_sha256({"truth": index}),
            split_sha256=semantic_sha256("HELD_OUT"),
        )
        for index in range(13, 21)
    )
    public_payload: dict[str, object] = {
        "schema_version": "dta-v21.public-evaluation-manifest.v1",
        "case_schema_version": "dta-v21.agent-visible-replay-case.v1",
        "truth_schema_version": "dta-v21.evaluator-case-truth.v1",
        "development_cases": development,
        "held_out_cases": held_out,
    }
    public_draft = cast(Any, PublicEvaluationManifestV21).model_construct(
        **public_payload, manifest_sha256="0" * 64
    )
    public = PublicEvaluationManifestV21.model_validate(
        {
            **public_payload,
            "manifest_sha256": semantic_sha256(
                public_draft.model_dump(mode="json", exclude={"manifest_sha256"})
            ),
        }
    )

    repository_root = Path(__file__).resolve().parents[2]
    git_runner = _TestGitRunner(repository_root)
    base_code_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = build_evaluation_freeze_manifest_v21(
        repository_root=repository_root,
        base_code_head=base_code_head,
        model_id=MODEL,
        max_completion_tokens=1600,
        public_case_manifest=public,
        schedule=schedule,
        preregistration=preregistration,
        git_runner=git_runner,
    )

    assert len(manifest.agent_identities) == 3
    assert len(manifest.source_bindings) == 13
    assert {item.name for item in manifest.source_bindings} >= {
        "planner.py",
        "planner_contracts.py",
        "evaluation_contracts.py",
        "evaluation_agents.py",
    }
    assert not manifest.held_out_executed
    assert "scenario_family" not in manifest.public_case_manifest.model_dump_json()

    older_head = subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with pytest.raises(ValueError, match="current HEAD"):
        build_evaluation_freeze_manifest_v21(
            repository_root=repository_root,
            base_code_head=older_head,
            model_id=MODEL,
            max_completion_tokens=1600,
            public_case_manifest=public,
            schedule=schedule,
            preregistration=preregistration,
            git_runner=git_runner,
        )


def _entry(case, arm, identity):
    truth = build_evaluator_truth_v21(case)
    completed = truth.expected_terminal.value == "COMPLETED"
    prediction = EvaluationPredictionV21(
        schema_version="dta-v21.evaluation-prediction.v1",
        case_id=case.case_id,
        arm=arm,
        protocol_accepted=True,
        terminal=truth.expected_terminal,
        root_service=truth.expected_root_service if completed else None,
        fault_domain=truth.expected_fault_domain if completed else None,
        mechanism=truth.expected_mechanism if completed else None,
        disposition=truth.expected_disposition if completed else None,
        runbook_id=truth.expected_runbook if completed else None,
        cited_evidence_sources=truth.expected_evidence_sources,
        evidence_refs_valid=True,
        requested_evidence_sources=truth.expected_evidence_sources,
        requested_targets=(
            ()
            if truth.expected_root_service is None
            else (truth.expected_root_service,)
        ),
        duplicate_normalized_calls=0,
        read_tool_dispatches=(
            0
            if arm is EvaluationArmV21.ONE_SHOT_FULL_CONTEXT
            else min(4, len(truth.expected_evidence_sources))
        ),
        context_materialization_reads=(
            4 if arm is EvaluationArmV21.ONE_SHOT_FULL_CONTEXT else 0
        ),
        provider_turns=2,
        input_tokens=100,
        output_tokens=20,
        latency_ms=50,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
    )
    score = build_evaluation_score_v21(prediction=prediction, truth=truth)
    payload = {
        "schema_version": "dta-v21.evaluation-entry-result.v1",
        "execution_id": semantic_sha256({"case": case.case_id, "arm": arm.value})[:32],
        "case_sha256": semantic_sha256({"case": case.case_id}),
        "truth_sha256": truth.truth_sha256,
        "arm": arm,
        "model_id": MODEL,
        "identity_sha256": identity.identity_sha256,
        "agent_result_sha256": semantic_sha256(
            {"agent": case.case_id, "arm": arm.value}
        ),
        "prediction": prediction,
        "score": score,
    }
    draft = EvaluationEntryResultV21.model_construct(**payload, entry_sha256="0" * 64)
    return EvaluationEntryResultV21.model_validate(
        {
            **payload,
            "entry_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"entry_sha256"})
            ),
        }
    )


def _replace_entry(
    entry: EvaluationEntryResultV21, **updates: object
) -> EvaluationEntryResultV21:
    payload = {
        name: getattr(entry, name)
        for name in type(entry).model_fields
        if name != "entry_sha256"
    }
    payload.update(updates)
    draft = EvaluationEntryResultV21.model_construct(
        **payload, entry_sha256="0" * 64
    )
    return EvaluationEntryResultV21.model_validate(
        {
            **payload,
            "entry_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"entry_sha256"})
            ),
        }
    )


def test_development_report_covers_all_required_groups_and_ablation() -> None:
    plan = build_default_capture_plan_v21(base_head="a" * 40)
    development = tuple(case for case in plan.cases if int(case.case_id[-3:]) <= 12)
    truths = {case.case_id: build_evaluator_truth_v21(case) for case in development}
    identities = build_three_arm_identities_v21(
        model_id=MODEL, max_completion_tokens=1600
    )
    identity_by_arm = {item.arm.value: item for item in identities}
    entries = []
    for case in development:
        for arm in (
            EvaluationArmV21.ONE_SHOT_FULL_CONTEXT,
            EvaluationArmV21.FLAT_ADAPTIVE,
            EvaluationArmV21.EVIDENCE_GUIDED_PLANNER,
        ):
            entries.append(_entry(case, arm, identity_by_arm[arm.value]))
    for case_id in ABLATION_CASE_IDS_V21:
        case = next(item for item in development if item.case_id == case_id)
        entries.append(
            _entry(
                case,
                EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION,
                identity_by_arm[EvaluationArmV21.EVIDENCE_GUIDED_PLANNER.value],
            )
        )

    report = build_development_report_v21(
        entries=tuple(entries), truths=truths, identities=identities
    )

    assert report.primary_entry_count == 36
    assert report.ablation_entry_count == 4
    assert {item.group_type for item in report.aggregates} == {
        "OVERALL",
        "ARM",
        "SPLIT",
        "MECHANISM",
        "GENERALIZATION_SLICE",
    }
    overall = next(item for item in report.aggregates if item.group_type == "OVERALL")
    split = next(item for item in report.aggregates if item.group_type == "SPLIT")
    assert overall.scored_entries == 36
    assert split.group_value == "DEVELOPMENT"
    assert split.scored_entries == 36
    assert sum(
        item.scored_entries
        for item in report.aggregates
        if item.group_type == "MECHANISM"
    ) == 36
    assert sum(
        item.scored_entries
        for item in report.aggregates
        if item.group_type == "GENERALIZATION_SLICE"
    ) == 36
    assert overall.protocol_acceptance_rate == 1.0
    assert overall.unsafe_proposal_attempts == 0
    assert all(item.action_precision for item in report.aggregates)


def test_held_out_report_has_exact_primary_aggregates_and_frozen_decision() -> None:
    plan = build_default_capture_plan_v21(base_head="a" * 40)
    held_out = tuple(case for case in plan.cases if int(case.case_id[-3:]) >= 13)
    truths = {case.case_id: build_evaluator_truth_v21(case) for case in held_out}
    identities = build_three_arm_identities_v21(
        model_id=MODEL, max_completion_tokens=1600
    )
    identity_by_arm = {item.arm.value: item for item in identities}
    entries = tuple(
        _entry(case, arm, identity_by_arm[arm.value])
        for case in held_out
        for arm in (
            EvaluationArmV21.ONE_SHOT_FULL_CONTEXT,
            EvaluationArmV21.FLAT_ADAPTIVE,
            EvaluationArmV21.EVIDENCE_GUIDED_PLANNER,
        )
    )
    schedule = build_evaluation_schedule_v21(
        seed_sha256=semantic_sha256("held-out report schedule")
    )
    preregistration = build_evaluation_preregistration_v21(
        model_id=MODEL,
        max_completion_tokens=1600,
        schedule_sha256=schedule.schedule_sha256,
    )
    case_bindings = {
        case.case_id: PublicCaseBindingV21(
            case_id=case.case_id,
            case_sha256=next(
                item.case_sha256
                for item in entries
                if item.prediction.case_id == case.case_id
            ),
            truth_sha256=truths[case.case_id].truth_sha256,
            split_sha256=semantic_sha256("HELD_OUT"),
        )
        for case in held_out
    }

    report = build_held_out_report_v21(
        entries=entries,
        truths=truths,
        case_bindings=case_bindings,
        identities=identities,
        preregistration=preregistration,
        truth_isolation_verified=True,
        scorer_verified=True,
    )

    assert report.primary_entry_count == 24
    assert report.claim_decision.marker == (
        "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
    )
    assert report.claim_decision.non_owned_mutations_zero
    assert next(
        item for item in report.aggregates if item.group_type == "SPLIT"
    ).group_value == "HELD_OUT"

    with pytest.raises(ValueError, match="crossed matrix"):
        build_held_out_report_v21(
            entries=(*entries[:-1], entries[0]),
            truths=truths,
            case_bindings=case_bindings,
            identities=identities,
            preregistration=preregistration,
            truth_isolation_verified=True,
            scorer_verified=True,
        )

    mismatched_bindings = dict(case_bindings)
    first_case_id = held_out[0].case_id
    mismatched_bindings[first_case_id] = PublicCaseBindingV21(
        case_id=first_case_id,
        case_sha256="0" * 64,
        truth_sha256=truths[first_case_id].truth_sha256,
        split_sha256=semantic_sha256("HELD_OUT"),
    )
    with pytest.raises(ValueError, match="case or truth binding"):
        build_held_out_report_v21(
            entries=entries,
            truths=truths,
            case_bindings=mismatched_bindings,
            identities=identities,
            preregistration=preregistration,
            truth_isolation_verified=True,
            scorer_verified=True,
        )

    with pytest.raises(ValueError, match="verified gates"):
        build_held_out_report_v21(
            entries=entries,
            truths=truths,
            case_bindings=case_bindings,
            identities=identities,
            preregistration=preregistration,
            truth_isolation_verified=False,
            scorer_verified=True,
        )

    with pytest.raises(ValueError, match="entry identity"):
        build_held_out_report_v21(
            entries=(
                _replace_entry(entries[0], identity_sha256="0" * 64),
                *entries[1:],
            ),
            truths=truths,
            case_bindings=case_bindings,
            identities=identities,
            preregistration=preregistration,
            truth_isolation_verified=True,
            scorer_verified=True,
        )
