from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy

import pytest

from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.predicates import build_default_evidence_support_policy_v22
from ecomsre.dta_v2.v23.cli import main
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    CoreOntologySchemaSnapshotV234,
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.discovery_runtime import run_cpu_development_demo_v23
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
    LocalOntologyExpansionStoreV234,
    OntologyExpansionStateV234,
    RegistrationGenerationAuthorizationV234,
    build_registration_generation_authorization_v234,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    TEST_REVIEWER_V23,
    build_review_queue_item_v23,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc)


def _accepted_shadow(local_root: Path):
    review_store = LocalReviewStoreV23(local_root)
    result = run_cpu_development_demo_v23(repository_root=ROOT, hide_cpu=True)
    assert result.provisional_report is not None
    item = build_review_queue_item_v23(
        report=result.provisional_report,
        graph=result.residual_graph,
        source_case_id="fault-map-a",
        queued_at=NOW,
        automated_fixture=True,
    )
    review_store.enqueue(item)
    accepted = review_store.decide(
        report_id=item.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW for ontology expansion authorization.",
        canonical_label="compute-resource-pressure",
        merge_target=None,
        requested_observations=(),
        reviewed_at=NOW,
    )
    assert accepted.shadow_entry is not None
    assert accepted.registration_draft is not None
    return accepted


def test_accept_as_new_alone_does_not_authorize_formal_generation(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "dta-v234"
    _accepted_shadow(local_root)

    store = LocalOntologyExpansionStoreV234(local_root)

    assert store.list_authorization_ids() == ()
    assert store.list_transition_ids() == ()


def test_authorization_binds_shadow_review_seed_snapshot_and_transition(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "dta-v234"
    accepted = _accepted_shadow(local_root)
    assert accepted.shadow_entry is not None

    result = LocalOntologyExpansionStoreV234(local_root).authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note="Generate a first formal registration draft.",
        authorized_at=NOW,
    )

    assert result.authorization.authorized_scope == "FORMAL_DRAFT_ONLY"
    assert result.authorization.simulation is True
    assert result.authorization.source_review_record_id == accepted.review.review_record_id
    assert result.registration_seed.legacy_registration_draft.draft_sha256 == (
        accepted.registration_draft.draft_sha256
    )
    assert result.transition.from_state is OntologyExpansionStateV234.SHADOW_ACCEPTED
    assert result.transition.to_state is (
        OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED
    )
    assert result.transition.authorization_sha256 == (
        result.authorization.authorization_sha256
    )
    assert result.transition.registration_seed_sha256 == (
        result.registration_seed.seed_sha256
    )
    assert result.transition.core_ontology_snapshot_sha256 == (
        result.core_ontology_snapshot.snapshot_sha256
    )
    source_item = LocalReviewStoreV23(local_root).load_item(accepted.review.report_id)
    assert result.transition.source_report_sha256 == source_item.report.report_sha256
    assert result.transition.source_queue_item_sha256 == source_item.queue_item_sha256


def test_real_reviewer_is_not_labelled_as_simulation(tmp_path: Path) -> None:
    local_root = tmp_path / "dta-v234"
    accepted = _accepted_shadow(local_root)
    assert accepted.shadow_entry is not None

    result = LocalOntologyExpansionStoreV234(local_root).authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer="Ada Reviewer",
        authorization_note="Human authorization for a formal draft only.",
        authorized_at=NOW,
    )

    assert result.authorization.simulation is False
    assert result.transition.simulation is False


def test_core_ontology_snapshot_is_runtime_derived_and_deterministic() -> None:
    first = build_core_ontology_schema_snapshot_v234()
    second = build_core_ontology_schema_snapshot_v234()
    effective = build_effective_support_policy_v222()
    frozen = build_default_evidence_support_policy_v22()

    assert first == second
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.core_support_clauses == effective.clauses
    assert first.frozen_core_support_clauses == frozen.clauses
    assert {item.clause_id for item in first.core_support_clauses} - {
        item.clause_id for item in first.frozen_core_support_clauses
    } == {
        "configuration:error-metric-and-first-error-trace",
        "memory-leak:growth-and-healthy",
    }
    assert set(first.core_mechanisms) == {
        MechanismV22.CONFIGURATION_ERROR,
        MechanismV22.SERVICE_UNAVAILABLE,
        MechanismV22.CPU_SATURATION,
        MechanismV22.MEMORY_LEAK,
        MechanismV22.DEPENDENCY_LATENCY,
    }
    assert {item.predicate_kind for item in first.predicate_source_bindings} == set(
        PredicateKindV22
    )
    assert first.authoritative_single_predicate_allowlist == (
        PredicateKindV22.RUNTIME_NOT_RUNNING,
    )
    assert {item.mechanism for item in first.representative_examples} == set(
        first.core_mechanisms
    )


def test_duplicate_authorization_fails_closed(tmp_path: Path) -> None:
    local_root = tmp_path / "dta-v234"
    accepted = _accepted_shadow(local_root)
    assert accepted.shadow_entry is not None
    store = LocalOntologyExpansionStoreV234(local_root)
    store.authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note="SIMULATED HUMAN REVIEW formal-draft authorization.",
        authorized_at=NOW,
    )

    try:
        store.authorize_draft_generation(
            shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
            reviewer=TEST_REVIEWER_V23,
            authorization_note="A second authorization must not replace the first.",
            authorized_at=NOW,
        )
    except ValueError as exc:
        assert "already authorized" in str(exc)
    else:
        raise AssertionError("duplicate authorization did not fail closed")


def test_missing_shadow_and_mismatched_source_report_fail_closed(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "dta-v234"
    accepted = _accepted_shadow(local_root)
    assert accepted.shadow_entry is not None

    with pytest.raises(ValueError, match="shadow fault is absent"):
        LocalOntologyExpansionStoreV234(local_root).authorize_draft_generation(
            shadow_fault_id="shadow-v23-0000000000000000",
            reviewer=TEST_REVIEWER_V23,
            authorization_note="This shadow does not exist.",
            authorized_at=NOW,
        )

    forged_shadow = accepted.shadow_entry.model_copy(
        update={"positive_report_ids": ("report-v23-not-the-source",)}
    )
    with pytest.raises(ValueError, match="source report differs"):
        build_registration_generation_authorization_v234(
            shadow=forged_shadow,
            source_review=accepted.review,
            reviewer=TEST_REVIEWER_V23,
            authorization_note="Forged source binding must fail.",
            authorized_at=NOW,
        )


def test_missing_persisted_source_report_blocks_authorization(tmp_path: Path) -> None:
    local_root = tmp_path / "dta-v234"
    accepted = _accepted_shadow(local_root)
    assert accepted.shadow_entry is not None
    review_store = LocalReviewStoreV23(local_root)
    (review_store.reports_dir / f"{accepted.review.report_id}.json").unlink()

    with pytest.raises(ValueError, match="review report is absent"):
        LocalOntologyExpansionStoreV234(local_root).authorize_draft_generation(
            shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
            reviewer=TEST_REVIEWER_V23,
            authorization_note="Missing source report must fail closed.",
            authorized_at=NOW,
        )

def test_authorization_result_rejects_individually_valid_cross_bound_children(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_shadow = _accepted_shadow(first_root).shadow_entry
    second_shadow = _accepted_shadow(second_root).shadow_entry
    assert first_shadow is not None
    assert second_shadow is not None
    first = LocalOntologyExpansionStoreV234(first_root).authorize_draft_generation(
        shadow_fault_id=first_shadow.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note="First valid authorization.",
        authorized_at=NOW,
    )
    second = LocalOntologyExpansionStoreV234(second_root).authorize_draft_generation(
        shadow_fault_id=second_shadow.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note="Second valid authorization.",
        authorized_at=NOW + timedelta(seconds=1),
    )
    payload = first.model_dump(mode="python")
    payload["authorization"] = second.authorization

    with pytest.raises(ValueError, match="semantic bindings differ"):
        DraftGenerationAuthorizationResultV234.model_validate(payload)


def test_incomplete_transition_without_authorization_fails_closed(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "dta-v234"
    accepted = _accepted_shadow(local_root)
    assert accepted.shadow_entry is not None
    store = LocalOntologyExpansionStoreV234(local_root)
    result = store.authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note="First valid authorization.",
        authorized_at=NOW,
    )
    (store.authorizations_dir / f"{result.authorization.authorization_id}.json").unlink()

    with pytest.raises(ValueError, match="draft-generation transition"):
        store.authorize_draft_generation(
            shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
            reviewer=TEST_REVIEWER_V23,
            authorization_note="Orphaned transition must fail closed.",
            authorized_at=NOW + timedelta(seconds=1),
        )


def test_increment1_ontology_cli_lists_authorizes_and_snapshots(
    tmp_path: Path,
    capsys,
) -> None:
    local_root = tmp_path / "dta-v234"
    accepted = _accepted_shadow(local_root)
    assert accepted.shadow_entry is not None

    assert main(("ontology", "list", "--local-root", str(local_root))) == 0
    assert accepted.shadow_entry.shadow_fault_id in capsys.readouterr().out

    assert main(
        (
            "ontology",
            "authorize-draft",
            accepted.shadow_entry.shadow_fault_id,
            "--reviewer",
            TEST_REVIEWER_V23,
            "--note",
            "SIMULATED HUMAN REVIEW formal-draft authorization.",
            "--local-root",
            str(local_root),
        )
    ) == 0
    authorized = capsys.readouterr().out
    assert '"authorized_scope": "FORMAL_DRAFT_ONLY"' in authorized
    assert '"to_state": "DRAFT_GENERATION_AUTHORIZED"' in authorized
    assert '"simulation": true' in authorized

    assert main(("ontology", "snapshot")) == 0
    snapshot = capsys.readouterr().out
    assert '"dta-v234.core-ontology-schema-snapshot.v1"' in snapshot
    assert '"RUNTIME_NOT_RUNNING"' in snapshot


def test_committed_increment1_artifacts_are_bound_and_simulated() -> None:
    snapshot = CoreOntologySchemaSnapshotV234.model_validate_json(
        (ROOT / "docs/analysis/dta-v234-core-ontology-snapshot.json").read_bytes()
    )
    authorization = RegistrationGenerationAuthorizationV234.model_validate_json(
        (ROOT / "config/dta-v234/examples/authorization.json").read_bytes()
    )

    assert snapshot == build_core_ontology_schema_snapshot_v234()
    assert authorization.reviewer == TEST_REVIEWER_V23
    assert authorization.simulation is True
    assert "SIMULATED HUMAN REVIEW" in authorization.authorization_note


def test_v234_history_verifier_runs_the_transitive_chain() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/ci/verify_dta_v234_history.py"))
    namespace["verify"]()
