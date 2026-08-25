from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.v23.cli import main
from ecomsre.dta_v2.v23.discovery_runtime import (
    assert_v23_artifact_is_non_actionable,
    run_cpu_development_demo_v23,
    run_development_leave_one_out_v23,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    HumanReviewRecordV23,
    LocalReviewStoreV23,
    RegistrationDraftV23,
    ShadowFaultRegistryV23,
    build_human_review_record_v23,
    build_review_queue_item_v23,
    match_shadow_registry_v23,
)
from ecomsre.dta_v2.v22.predicates import MechanismV22


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)


def _cpu_item():
    result = run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=True)
    assert result.provisional_report is not None
    return build_review_queue_item_v23(
        report=result.provisional_report,
        graph=result.residual_graph,
        source_case_id="fault-map-a",
        queued_at=NOW,
        automated_fixture=True,
    )


def test_test_reviewer_is_explicitly_simulated() -> None:
    item = _cpu_item()
    record = build_human_review_record_v23(
        report=item.report,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer="TEST_REVIEWER",
        review_note="Simulated acceptance for the bounded development gate.",
        canonical_label="compute-resource-pressure",
        merge_target=None,
        requested_observations=(),
        reviewed_at=NOW,
    )

    assert record.simulation is True
    assert record.reviewer == "TEST_REVIEWER"
    assert record.decision is HumanReviewDecisionV23.ACCEPT_AS_NEW


@pytest.mark.parametrize(
    ("decision", "label", "merge_target", "requested"),
    (
        (HumanReviewDecisionV23.ACCEPT_AS_NEW, None, None, ()),
        (HumanReviewDecisionV23.MERGE_WITH_EXISTING, None, None, ()),
        (HumanReviewDecisionV23.REQUEST_MORE_EVIDENCE, None, None, ()),
        (HumanReviewDecisionV23.REJECT_AS_NOISE, "unexpected", None, ()),
        (HumanReviewDecisionV23.SAVE_AS_INCIDENT_ONLY, None, "unexpected", ()),
    ),
)
def test_review_decision_specific_fields_fail_closed(
    decision: HumanReviewDecisionV23,
    label: str | None,
    merge_target: str | None,
    requested: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        build_human_review_record_v23(
            report=_cpu_item().report,
            decision=decision,
            reviewer="TEST_REVIEWER",
            review_note="Simulated invalid decision contract.",
            canonical_label=label,
            merge_target=merge_target,
            requested_observations=requested,
            reviewed_at=NOW,
        )


def test_accept_as_new_creates_shadow_and_registration_draft(tmp_path: Path) -> None:
    store = LocalReviewStoreV23(tmp_path / "dta-v23")
    item = _cpu_item()
    store.enqueue(item)

    result = store.decide(
        report_id=item.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer="TEST_REVIEWER",
        review_note="Simulated acceptance for contract coverage.",
        canonical_label="compute-resource-pressure",
        merge_target=None,
        requested_observations=(),
        reviewed_at=NOW,
    )

    assert result.review.simulation is True
    assert result.shadow_entry is not None
    assert result.shadow_entry.status == "SHADOW"
    assert result.shadow_entry.remediation_authority == "NONE"
    assert result.registration_draft is not None
    assert result.registration_draft.remediation_registration == "NOT_INCLUDED"
    assert store.load_registry().entries == (result.shadow_entry,)
    assert store.list_report_ids() == (item.report.report_id,)
    assert (store.root / "reviews" / f"{result.review.review_record_id}.json").is_file()
    assert (
        store.root
        / "registration-drafts"
        / f"{result.registration_draft.proposed_mechanism_slug}.json"
    ).is_file()

    with pytest.raises(TypeError, match="non-actionable"):
        assert_v23_artifact_is_non_actionable(result.shadow_entry)
    with pytest.raises(TypeError, match="non-actionable"):
        assert_v23_artifact_is_non_actionable(result.registration_draft)


def test_disjoint_development_report_matches_accepted_shadow(tmp_path: Path) -> None:
    store = LocalReviewStoreV23(tmp_path / "dta-v23")
    accepted = _cpu_item()
    store.enqueue(accepted)
    result = store.decide(
        report_id=accepted.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer="TEST_REVIEWER",
        review_note="Simulated acceptance for retrieval coverage.",
        canonical_label="compute-resource-pressure",
        merge_target=None,
        requested_observations=(),
        reviewed_at=NOW,
    )
    assert result.shadow_entry is not None

    disjoint = run_development_leave_one_out_v23(
        repository_root=ROOT,
        case_id="d03",
        hidden_mechanism=MechanismV22.MEMORY_LEAK,
    )
    assert disjoint.provisional_report is not None
    matches = match_shadow_registry_v23(
        report=disjoint.provisional_report,
        graph=disjoint.residual_graph,
        registry=store.load_registry(),
    )

    assert matches
    assert len(matches) <= 3
    assert matches[0].terminal == "MATCHED_EXPERIMENTAL_FAULT"
    assert matches[0].shadow_fault_id == result.shadow_entry.shadow_fault_id
    assert matches[0].match_score > 0.0
    assert matches[0].action_authority == "NONE"


@pytest.mark.parametrize(
    "decision",
    (
        HumanReviewDecisionV23.REQUEST_MORE_EVIDENCE,
        HumanReviewDecisionV23.REJECT_AS_NOISE,
        HumanReviewDecisionV23.SAVE_AS_INCIDENT_ONLY,
    ),
)
def test_non_accept_review_decisions_do_not_register(
    tmp_path: Path,
    decision: HumanReviewDecisionV23,
) -> None:
    store = LocalReviewStoreV23(tmp_path / decision.value)
    item = _cpu_item()
    store.enqueue(item)
    result = store.decide(
        report_id=item.report.report_id,
        decision=decision,
        reviewer="TEST_REVIEWER",
        review_note="Simulated non-registration decision.",
        canonical_label=None,
        merge_target=None,
        requested_observations=("another bounded resource window",)
        if decision is HumanReviewDecisionV23.REQUEST_MORE_EVIDENCE
        else (),
        reviewed_at=NOW,
    )

    assert result.shadow_entry is None
    assert result.registration_draft is None
    assert store.load_registry().entries == ()


def test_merge_with_existing_records_review_without_new_entry(tmp_path: Path) -> None:
    store = LocalReviewStoreV23(tmp_path / "merge")
    first = _cpu_item()
    store.enqueue(first)
    accepted = store.decide(
        report_id=first.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer="TEST_REVIEWER",
        review_note="Simulated seed entry.",
        canonical_label="compute-resource-pressure",
        merge_target=None,
        requested_observations=(),
        reviewed_at=NOW,
    )
    assert accepted.shadow_entry is not None

    second = run_development_leave_one_out_v23(
        repository_root=ROOT,
        case_id="d03",
        hidden_mechanism=MechanismV22.MEMORY_LEAK,
    )
    assert second.provisional_report is not None
    second_item = build_review_queue_item_v23(
        report=second.provisional_report,
        graph=second.residual_graph,
        source_case_id="d03",
        queued_at=NOW,
        automated_fixture=True,
    )
    store.enqueue(second_item)
    merged = store.decide(
        report_id=second_item.report.report_id,
        decision=HumanReviewDecisionV23.MERGE_WITH_EXISTING,
        reviewer="TEST_REVIEWER",
        review_note="Simulated merge decision.",
        canonical_label=None,
        merge_target=accepted.shadow_entry.shadow_fault_id,
        requested_observations=(),
        reviewed_at=NOW,
    )

    assert merged.shadow_entry is not None
    assert merged.registration_draft is None
    assert second_item.report.report_id in merged.shadow_entry.positive_report_ids
    assert len(store.load_registry().entries) == 1


def test_review_cli_requires_explicit_reviewer(tmp_path: Path) -> None:
    store = LocalReviewStoreV23(tmp_path / "cli")
    item = _cpu_item()
    store.enqueue(item)

    with pytest.raises(SystemExit):
        main(
            (
                "review",
                "decide",
                item.report.report_id,
                "--decision",
                "ACCEPT_AS_NEW",
                "--label",
                "compute-resource-pressure",
                "--note",
                "missing reviewer must fail",
                "--local-root",
                str(store.root),
            )
        )


def test_committed_examples_are_typed_and_simulated() -> None:
    examples = ROOT / "config/dta-v23/examples"
    review = HumanReviewRecordV23.model_validate_json(
        (examples / "review-record.json").read_bytes()
    )
    registry = ShadowFaultRegistryV23.model_validate_json(
        (examples / "shadow-registry.json").read_bytes()
    )
    draft = RegistrationDraftV23.model_validate_json(
        (examples / "registration-draft.json").read_bytes()
    )

    assert review.reviewer == "TEST_REVIEWER"
    assert review.simulation is True
    assert registry.entries[0].remediation_authority == "NONE"
    assert draft.remediation_registration == "NOT_INCLUDED"


def test_review_and_shadow_cli_round_trip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = LocalReviewStoreV23(tmp_path / "cli-round-trip")
    item = _cpu_item()
    report_path = store.enqueue(item)

    assert main(("review", "list", "--local-root", str(store.root))) == 0
    assert item.report.report_id in capsys.readouterr().out
    assert main(
        (
            "review",
            "show",
            item.report.report_id,
            "--local-root",
            str(store.root),
        )
    ) == 0
    assert item.report.report_id in capsys.readouterr().out
    assert main(
        (
            "review",
            "decide",
            item.report.report_id,
            "--decision",
            "ACCEPT_AS_NEW",
            "--reviewer",
            "TEST_REVIEWER",
            "--label",
            "compute-resource-pressure",
            "--note",
            "Simulated CLI acceptance.",
            "--local-root",
            str(store.root),
        )
    ) == 0
    assert '"simulation": true' in capsys.readouterr().out
    assert main(
        (
            "shadow",
            "match",
            "--report",
            str(report_path),
            "--local-root",
            str(store.root),
        )
    ) == 0
    assert "MATCHED_EXPERIMENTAL_FAULT" in capsys.readouterr().out
