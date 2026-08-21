from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    EvidenceUtilityAuditReportV222,
    ShortestAdmissiblePathV222,
    audit_case_set_v222,
    evaluate_development_routing_gate_v222,
)
from ecomsre.dta_v2.v22.evaluation_manifest_v222 import (
    build_evaluation_manifest_v222,
    load_and_verify_evaluation_manifest_v222,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseModifierV22,
    load_practical_case_set_v22,
    load_synthetic_evaluation_source_v222,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.replay_capabilities_v222 import (
    ReplaySourceAvailabilityV222,
    build_replay_capabilities_v222,
)


ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "config/dta-v22-2/evaluation"


def test_v222_evaluation_freeze_has_required_composition_and_derivation_labels() -> None:
    cases = load_practical_case_set_v22(EVALUATION / "cases.json")
    truths = load_practical_truth_set_v22(EVALUATION / "truth.json")
    assert len(cases.cases) == len(truths.truths) == 16
    assert all(
        item.modifier is PracticalCaseModifierV22.V222_EVALUATION_FIXTURE
        and item.capture_kind.value.startswith("SYNTHETIC_")
        and item.derivation
        for item in cases.cases
    )
    composition: dict[str, int] = {}
    for truth in truths.truths:
        key = truth.expected_mechanism or truth.expected_terminal
        composition[key] = composition.get(key, 0) + 1
    assert composition == {
        "ABSTAIN": 3,
        "CONFIGURATION_ERROR": 2,
        "CPU_SATURATION": 2,
        "DEPENDENCY_LATENCY": 2,
        "MEMORY_LEAK": 2,
        "NO_INCIDENT": 3,
        "SERVICE_UNAVAILABLE": 2,
    }
    pairs = {
        pair
        for item in cases.cases
        for pair in item.counterfactual_pair_ids
    }
    assert len(pairs) == 4
    assert all(
        sum(pair in item.counterfactual_pair_ids for item in cases.cases) == 2
        for pair in pairs
    )


def test_v222_evaluation_sources_are_bound_new_and_have_tempting_empty_reads() -> None:
    cases = load_practical_case_set_v22(EVALUATION / "cases.json")
    previous = load_practical_case_set_v22(
        ROOT / "config/dta-v22-sprint/evaluation/cases.json"
    )
    previous_hashes = {
        item.source_sha256 for item in previous.cases if item.source_sha256 is not None
    }
    nonidentical = 0
    tempting_empty = 0
    for spec in cases.cases:
        assert spec.source_path is not None and spec.source_sha256 is not None
        assert (
            hashlib.sha256((ROOT / spec.source_path).read_bytes()).hexdigest()
            == spec.source_sha256
        )
        nonidentical += spec.source_sha256 not in previous_hashes
        source = load_synthetic_evaluation_source_v222(
            spec=spec,
            repository_root=ROOT,
        )
        tempting_empty += not source.normalized_case.capture.logs
        assert materialize_practical_case_v22(
            spec=spec,
            repository_root=ROOT,
        ).case_id == spec.case_id
        capabilities = build_replay_capabilities_v222(
            spec=spec,
            repository_root=ROOT,
        )
        assert all(
            item.availability is ReplaySourceAvailabilityV222.CAPTURED
            for item in capabilities.sources
        )
    assert nonidentical >= 8
    assert tempting_empty >= 4


def test_v222_frozen_utility_audit_has_ten_feasible_core_incidents() -> None:
    frozen = EvidenceUtilityAuditReportV222.model_validate_json(
        (EVALUATION / "utility-audit.json").read_bytes()
    )
    regenerated = audit_case_set_v222(
        repository_root=ROOT,
        case_set_path=EVALUATION / "cases.json",
        truth_path=EVALUATION / "truth.json",
    )
    assert regenerated == frozen
    core = tuple(
        item for item in frozen.cases if item.expected_terminal == "DIAGNOSED"
    )
    assert len(core) == 10
    assert all(
        item.shortest_admissible_path
        in {ShortestAdmissiblePathV222.ONE, ShortestAdmissiblePathV222.TWO}
        for item in core
    )
    assert frozen.infeasible_incident_cases == 0
    assert frozen.oracle_visible_to_provider is False


def test_v222_new_evaluation_top_four_routing_gate_passes_offline() -> None:
    gate = evaluate_development_routing_gate_v222(
        repository_root=ROOT,
        case_set_path=EVALUATION / "cases.json",
        truth_path=EVALUATION / "truth.json",
    )
    assert gate.turn_zero_recall == 1.0
    assert gate.post_first_read_recall == 1.0
    assert gate.gate_passed is True
    assert gate.oracle_visible_to_provider is False


def test_v222_evaluation_manifest_binds_all_final_inputs(tmp_path: Path) -> None:
    manifest = build_evaluation_manifest_v222(
        repository_root=ROOT,
        implementation_commit="0" * 40,
        model="frozen-model",
    )
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    verified = load_and_verify_evaluation_manifest_v222(
        manifest_path=path,
        repository_root=ROOT,
        configured_model="frozen-model",
    )
    assert verified.expected_runs == 64
    assert verified.single_execution_rule == "EXACTLY_ONE_FULL_STUDY_EXECUTION"
