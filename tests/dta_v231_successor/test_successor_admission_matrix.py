from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v23.evaluation import (
    load_evaluation_case_set_v23,
    materialize_evaluation_case_v23,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    load_evaluation_case_set_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.evaluation_successor_v231 import (
    AdmissionStratumV231Successor,
    build_admission_matrix_v231_successor,
    load_successor_case_set_v231,
    load_successor_truth_set_v231,
    load_successor_views_v231,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/dta-v231-successor/evaluation"


def _matrix():
    return build_admission_matrix_v231_successor(
        repository_root=ROOT,
        cases=load_successor_case_set_v231(CONFIG / "cases.json"),
        truths=load_successor_truth_set_v231(CONFIG / "truth-index.json"),
        views=load_successor_views_v231(CONFIG / "ontology-views.json"),
    )


def test_successor_admission_matrix_proves_every_stratum_before_provider() -> None:
    matrix = _matrix()

    assert matrix.status == "DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS"
    assert matrix.case_count == 24
    assert matrix.provider_calls == 0
    assert all(item.contract_pass for item in matrix.entries)
    assert all(item.common_action_ids == tuple(sorted(set(item.common_action_ids))) for item in matrix.entries)
    assert all(len(item.support_policy_sha256) == 64 for item in matrix.entries)
    assert all(len(item.known_admission_sha256) == 64 for item in matrix.entries)
    novelty = tuple(
        item
        for item in matrix.entries
        if item.stratum
        in {
            AdmissionStratumV231Successor.NOVEL_HIDDEN,
            AdmissionStratumV231Successor.NOVEL_UNREGISTERED,
        }
    )
    assert len(novelty) == 14
    assert all(item.active_known_terminal_count == 0 for item in novelty)
    assert all(not item.no_incident_admissible for item in novelty)
    conflict_prone = tuple(item for item in novelty if item.conflict_prone_case)
    assert len(conflict_prone) == 8
    assert all(item.conflict_prone_design_pass for item in conflict_prone)
    assert all(item.initial_conflict_type.value == "RESOLVABLE_CONFLICT" for item in conflict_prone)
    assert all(item.initial_novelty_disposition == "DISCOVERY_READ_REQUIRED" for item in conflict_prone)
    assert all(item.discriminating_plan_sha256 is not None for item in conflict_prone)
    assert all(item.selected_discriminating_source is not None for item in conflict_prone)


def test_successor_registered_no_incident_and_conflict_controls_are_exact() -> None:
    matrix = _matrix()
    known = tuple(
        item
        for item in matrix.entries
        if item.stratum is AdmissionStratumV231Successor.REGISTERED_KNOWN
    )
    no_incident = tuple(
        item
        for item in matrix.entries
        if item.stratum is AdmissionStratumV231Successor.NO_INCIDENT
    )
    conflicts = tuple(
        item
        for item in matrix.entries
        if item.stratum is AdmissionStratumV231Successor.INSUFFICIENT_IRRECONCILABLE
    )

    assert len(known) == 4
    assert all(item.active_known_terminal_count == 1 for item in known)
    assert all(item.accepted_active_known_support_count == 1 for item in known)
    assert all(item.expected_known_terminal_matched for item in known)
    assert all(len(item.matched_clause_ids) == 1 for item in known)
    assert all(item.supporting_evidence_refs for item in known)
    assert len(no_incident) == 3
    assert all(item.active_known_terminal_count == 0 for item in no_incident)
    assert all(item.no_incident_admissible for item in no_incident)
    assert len(conflicts) == 3
    assert all(item.active_known_terminal_count == 0 for item in conflicts)
    assert all(not item.no_incident_admissible for item in conflicts)
    assert all(item.registered_support_incomplete for item in conflicts)
    assert all(item.accepted_active_known_support_count == 0 for item in conflicts)
    assert all(
        item.rejected_active_known_support_count
        == item.active_known_hypothesis_count
        for item in conflicts
    )
    assert all(item.explicit_unresolvable_contradiction for item in conflicts)
    assert all(len(item.contradiction_proof_action_ids) == 1 for item in conflicts)
    assert all(item.contradiction_witness_ids for item in conflicts)
    assert all(item.contradiction_evidence_refs for item in conflicts)


def test_successor_counterfactual_pairs_preserve_strata_and_swap_targets() -> None:
    matrix = _matrix()

    assert len(matrix.counterfactual_pairs) >= 4
    assert all(item.stratum_preserved for item in matrix.counterfactual_pairs)
    assert all(item.target_roles == ("TARGET_HIGH", "TARGET_LOW") for item in matrix.counterfactual_pairs)
    assert all(item.admission_shape_preserved for item in matrix.counterfactual_pairs)
    assert all(len(item.admission_shape_sha256) == 64 for item in matrix.counterfactual_pairs)
    assert all(item.discriminating_plan_shape_preserved for item in matrix.counterfactual_pairs)
    assert all(len(item.discriminating_plan_shape_sha256) == 64 for item in matrix.counterfactual_pairs)


def test_successor_bytes_are_disjoint_from_both_predecessor_studies() -> None:
    old_v23 = load_evaluation_case_set_v23(
        ROOT / "config/dta-v23/evaluation/cases.json"
    )
    blocked_v231 = load_evaluation_case_set_v231(
        ROOT / "config/dta-v231/evaluation/cases.json"
    )
    successor = load_successor_case_set_v231(CONFIG / "cases.json")
    old_hashes = {
        materialize_evaluation_case_v23(repository_root=ROOT, spec=spec)
        .source_bytes_sha256
        for spec in old_v23.cases
    }
    blocked_hashes = {
        materialize_evaluation_case_v231(repository_root=ROOT, spec=spec)
        .source_bytes_sha256
        for spec in blocked_v231.cases
    }
    successor_hashes = {
        materialize_evaluation_case_v231(repository_root=ROOT, spec=spec)
        .source_bytes_sha256
        for spec in successor.cases
    }

    assert len(successor_hashes) == 24
    assert old_hashes.isdisjoint(successor_hashes)
    assert blocked_hashes.isdisjoint(successor_hashes)
