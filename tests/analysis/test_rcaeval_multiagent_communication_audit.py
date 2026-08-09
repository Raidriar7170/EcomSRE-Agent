from __future__ import annotations

import ast
import builtins
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    PROJECT_ROOT
    / "scripts"
    / "analysis"
    / "rcaeval_multiagent_communication_audit.py"
)


def _load_module(name: str = "rcaeval_multiagent_communication_audit") -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_module()


def _row(**updates: object) -> dict[str, Any]:
    row: dict[str, Any] = {
        "private_case_key": "private-case-1",
        "candidate": "candidate-5",
        "completed": True,
        "initial_service": "frontend",
        "initial_indicator": "cpu",
        "initial_confidence": 0.6,
        "initial_explanation": "The frontend appears anomalous.",
        "initial_evidence_refs": ("metric:0001",),
        "truth_service": "checkout",
        "truth_indicator": "cpu",
        "metrics_ranking": (
            ("checkout", 1.0),
            ("frontend", 0.4),
            ("catalog", 0.1),
        ),
        "metrics_alternative": {
            "initial_service": "frontend",
            "alternative_service": "checkout",
            "alternative_rank": 1,
            "alternative_score": 1.0,
            "initial_rank_or_none": 2,
            "metrics_margin": 0.6,
        },
        "gate_route": "VERIFY_LOGS",
        "gate_reasons": ("METRICS_RANK_RISK",),
        "gate_initial_unstable": True,
        "logs_evidence": (
            {
                "evidence_ref": "log:0001",
                "service": "frontend",
                "observation": "upstream failure",
            },
            {
                "evidence_ref": "log:0002",
                "service": "checkout",
                "observation": "root exception",
            },
        ),
        "trace_evidence": (),
        "initial_context_evidence_refs": (
            "metric:0001",
            "log:0001",
            "log:0002",
        ),
        "specialist_hypotheses": (),
        "logs_pairwise_verification": {
            "preference": "ALTERNATIVE",
            "initial_role": "UNCERTAIN",
            "alternative_role": "ROOT_CANDIDATE",
            "supporting_evidence_refs": ("log:0002",),
            "contradicting_evidence_refs": (),
            "confidence": 0.8,
            "summary": "Alternative is stronger.",
        },
        "stored_fusion": {
            "action": "KEEP_INITIAL",
            "final_root_service": "frontend",
            "reason_codes": ("LOGS_PAIRWISE_INITIAL_NOT_CONTRADICTED",),
        },
        "final_service": "frontend",
        "final_indicator": "cpu",
        "indicator_action": "KEEP_STRONG_SINGLE_INDICATOR",
    }
    row.update(updates)
    return row


def test_artifact_sufficiency_graph_and_knowledge_matrix_use_real_v2_contract() -> None:
    sufficiency = audit.build_artifact_sufficiency()
    assert sufficiency["initial_diagnosis"]["classification"] == "DIRECTLY_OBSERVABLE"
    assert sufficiency["metrics_ranking"]["classification"] == (
        "DETERMINISTICALLY_RECONSTRUCTABLE"
    )
    assert sufficiency["specialist_input_envelope"]["classification"] == (
        "DETERMINISTICALLY_RECONSTRUCTABLE"
    )

    graph = audit.build_communication_graph()
    assert [item["stage"] for item in graph["stages"]] == [
        "RAW_BOUNDED_EVIDENCE",
        "STRONG_SINGLE_INITIAL",
        "GATE_FEATURES",
        "GATE_ROUTE",
        "METRICS_ALTERNATIVE",
        "SPECIALIST_INPUT",
        "SPECIALIST_OUTPUT",
        "DETERMINISTIC_FUSION",
        "INDICATOR_RESOLUTION",
        "FINAL_DIAGNOSIS",
    ]
    pairwise_edge = next(
        item for item in graph["edges"] if item["edge"] == "METRICS_TO_LOGS_PAIRWISE"
    )
    assert "metrics_alternative_rank" in pairwise_edge["fields_omitted"]
    assert "gate_reason_codes" in pairwise_edge["fields_omitted"]

    matrix = audit.build_communication_knowledge_matrix()
    pairwise = matrix["LOGS_PAIRWISE"]
    assert pairwise["alternative_service"] is True
    assert pairwise["metrics_rank_score"] is False
    assert pairwise["gate_reasons"] is False
    assert pairwise["initial_confidence"] is False
    assert pairwise["logs_evidence"] is True
    fusion = matrix["FUSION"]
    assert fusion["metrics_rank_score"] is True
    assert fusion["gate_reasons"] is True


@pytest.mark.parametrize(
    ("row", "expected"),
    (
        (_row(), "LOGS_CAN_COMPARE"),
        (
            _row(
                logs_evidence=(),
                trace_evidence=(
                    {"evidence_ref": "trace:0001", "service": "frontend"},
                    {"evidence_ref": "trace:0002", "service": "checkout"},
                ),
            ),
            "TRACE_CAN_COMPARE",
        ),
        (
            _row(
                trace_evidence=(
                    {"evidence_ref": "trace:0001", "service": "frontend"},
                    {"evidence_ref": "trace:0002", "service": "checkout"},
                ),
            ),
            "LOGS_AND_TRACE_CAN_COMPARE",
        ),
        (
            _row(logs_evidence=(), trace_evidence=()),
            "NO_SPECIALIST_SOURCE_CAN_COMPARE",
        ),
        (
            _row(
                logs_evidence=(),
                trace_evidence=(),
                metrics_alternative={
                    "initial_service": "frontend",
                    "alternative_service": "catalog",
                    "alternative_rank": 3,
                    "alternative_score": 0.1,
                    "initial_rank_or_none": 2,
                    "metrics_margin": 0.6,
                },
            ),
            "METRICS_ONLY_SUFFICIENT",
        ),
    ),
)
def test_source_visibility_and_sufficiency_classes(
    row: dict[str, Any], expected: str
) -> None:
    result = audit.classify_evidence_sufficiency(row)
    assert result["class"] == expected
    assert result["metrics_top6_truth_visible"] is True


def test_source_sufficiency_is_initial_wrong_and_retrieval_bound() -> None:
    summary = audit._aggregate_source_sufficiency(
        [
            _row(private_case_key="wrong"),
            _row(
                private_case_key="correct",
                initial_service="checkout",
                truth_service="checkout",
            ),
        ]
    )
    assert summary["initial_wrong_count"] == 1
    assert summary["truth_retrieved_count"] == 1
    assert sum(summary["class_counts"].values()) == 1
    with pytest.raises(ValueError, match="True Root"):
        audit.classify_evidence_sufficiency(
            _row(metrics_ranking=(("frontend", 1.0), ("catalog", 0.5)))
        )


def test_failure_taxonomy_obeys_candidate_gate_source_communication_precedence() -> None:
    assert audit.classify_failure_mechanism(
        _row(metrics_ranking=(("frontend", 1.0), ("catalog", 0.5)))
    )["primary"] == "CANDIDATE_NOT_RETRIEVED"
    assert audit.classify_failure_mechanism(
        _row(gate_route="DIRECT_RETURN")
    )["primary"] == "GATE_MISSED_ERROR"
    assert audit.classify_failure_mechanism(
        _row(logs_evidence=(), trace_evidence=())
    )["primary"] == "SOURCE_SIGNAL_ABSENT"
    assert audit.classify_failure_mechanism(
        _row(
            logs_evidence=(),
            trace_evidence=(
                {"evidence_ref": "trace:0001", "service": "frontend"},
                {"evidence_ref": "trace:0002", "service": "checkout"},
            ),
        )
    )["primary"] == "WRONG_SOURCE_SELECTED"
    assert audit.classify_failure_mechanism(
        _row(
            logs_pairwise_verification={
                "preference": "INCONCLUSIVE",
                "initial_role": "UNCERTAIN",
                "alternative_role": "UNCERTAIN",
                "supporting_evidence_refs": (),
                "contradicting_evidence_refs": (),
                "confidence": 0.5,
                "summary": "Insufficient.",
            }
        )
    )["primary"] == "SPECIALIST_REASONING_ERROR_WITH_SUFFICIENT_INPUT"


def test_free_and_pairwise_specialist_audits_separate_sufficiency() -> None:
    free_rows = [
        _row(
            candidate="candidate-4",
            logs_pairwise_verification=None,
            specialist_hypotheses=(
                {
                    "service": "checkout",
                    "causal_role": "ROOT_CANDIDATE",
                    "score": 0.9,
                    "supporting_evidence_refs": ("log:0002",),
                    "contradicting_evidence_refs": (),
                    "source": "logs",
                },
                {
                    "service": "frontend",
                    "causal_role": "PROPAGATED_SYMPTOM",
                    "score": 0.4,
                    "supporting_evidence_refs": ("log:0001",),
                    "contradicting_evidence_refs": (),
                    "source": "logs",
                },
            ),
        )
    ]
    free = audit.audit_free_specialist(free_rows)
    assert free["hypothesis_count"] == 2
    assert free["correct_alternative_rank1"] == 1
    assert free["correct_alternative_any_rank"] == 1
    assert free["root_candidate_role_truth_alignment"]["numerator"] == 1

    pairwise_rows = [
        _row(),
        _row(
            private_case_key="private-case-2",
            logs_evidence=(),
            logs_pairwise_verification={
                "preference": "INCONCLUSIVE",
                "initial_role": "UNCERTAIN",
                "alternative_role": "UNCERTAIN",
                "supporting_evidence_refs": (),
                "contradicting_evidence_refs": (),
                "confidence": 0.4,
                "summary": "No source evidence.",
            },
        ),
    ]
    pairwise = audit.audit_pairwise_specialist(pairwise_rows)
    assert pairwise["preference_distribution"] == {
        "INITIAL": 0,
        "ALTERNATIVE": 1,
        "INCONCLUSIVE": 1,
    }
    assert pairwise["inconclusive_insufficient_source"] == 1
    assert pairwise["inconclusive_despite_sufficient_source"] == 0
    assert pairwise["communication_feasibility"]["call_count"] == 2


def test_fusion_replay_and_limited_counterfactual_rules() -> None:
    row = _row()
    assert audit.replay_current_fusion(row) == row["stored_fusion"]
    frontier = audit.evaluate_fusion_frontier([row])
    assert frontier["F0"]["override_count"] == 0
    assert frontier["F1"]["override_count"] == 1
    assert frontier["F1"]["correct_override"] == 1
    assert frontier["F1"]["root_rescue"] == 1
    assert frontier["F2"]["correct_override"] == 1
    assert frontier["F3"]["override_count"] == 0

    no_root_role = _row(
        logs_pairwise_verification={
            **row["logs_pairwise_verification"],
            "alternative_role": "UNCERTAIN",
        }
    )
    role_frontier = audit.evaluate_fusion_frontier([no_root_role])
    assert role_frontier["F1"]["override_count"] == 0
    assert role_frontier["F2"]["override_count"] == 1


def test_current_fusion_replay_covers_visible_refs_trace_and_both_routes() -> None:
    hidden_ref = _row(
        logs_pairwise_verification={
            **_row()["logs_pairwise_verification"],
            "initial_role": "PROPAGATED_SYMPTOM",
            "supporting_evidence_refs": ("log:9999",),
        }
    )
    assert audit.replay_current_fusion(hidden_ref)["reason_codes"] == (
        "LOGS_PAIRWISE_REF_NOT_VISIBLE",
    )
    hidden_without_contradiction = _row(
        logs_pairwise_verification={
            **_row()["logs_pairwise_verification"],
            "supporting_evidence_refs": ("log:9999",),
        }
    )
    assert audit.replay_current_fusion(hidden_without_contradiction)[
        "reason_codes"
    ] == ("LOGS_PAIRWISE_INITIAL_NOT_CONTRADICTED",)

    trace_hypothesis = {
        "service": "checkout",
        "causal_role": "ROOT_CANDIDATE",
        "score": 0.99,
        "supporting_evidence_refs": ("trace:0002",),
        "contradicting_evidence_refs": (),
        "source": "traces",
    }
    initial_contradiction = {
        "service": "frontend",
        "causal_role": "PROPAGATED_SYMPTOM",
        "score": 0.5,
        "supporting_evidence_refs": ("trace:0001",),
        "contradicting_evidence_refs": (),
        "source": "traces",
    }
    trace = _row(
        gate_route="VERIFY_TRACES",
        logs_pairwise_verification=None,
        specialist_hypotheses=(trace_hypothesis, initial_contradiction),
    )
    assert audit.replay_current_fusion(trace)["reason_codes"] == (
        "TRACE_ALTERNATIVE_OVERRIDE",
    )

    both = _row(
        gate_route="VERIFY_BOTH",
        logs_pairwise_verification={
            **_row()["logs_pairwise_verification"],
            "initial_role": "PROPAGATED_SYMPTOM",
        },
        specialist_hypotheses=(trace_hypothesis,),
    )
    assert audit.replay_current_fusion(both)["reason_codes"] == (
        "BOTH_SOURCES_AGREE_OVERRIDE",
    )


def test_metrics_arbitration_frontier_keeps_initial_indicator() -> None:
    rows = [
        _row(),
        _row(
            private_case_key="private-case-2",
            initial_service="checkout",
            truth_service="checkout",
            metrics_ranking=(("frontend", 1.0), ("checkout", 0.4)),
            metrics_alternative={
                "initial_service": "checkout",
                "alternative_service": "frontend",
                "alternative_rank": 1,
                "alternative_score": 1.0,
                "initial_rank_or_none": 2,
                "metrics_margin": 0.6,
            },
            gate_route="DIRECT_RETURN",
            final_service="checkout",
            stored_fusion={
                "action": "KEEP_INITIAL",
                "final_root_service": "checkout",
                "reason_codes": ("KEEP_DIRECT",),
            },
        ),
    ]
    frontier = audit.evaluate_metrics_frontier(rows)
    assert frontier["M0"]["initial_root"] == 1
    assert frontier["M0"]["final_root"] == 1
    assert frontier["M2"]["override_count"] == 2
    assert frontier["M2"]["root_rescue"] == 1
    assert frontier["M2"]["root_damage"] == 1
    assert frontier["M2"]["pair_rescue"] == 1
    assert frontier["M2"]["pair_damage"] == 1
    assert frontier["M2"]["pair_net_rescue"] == 0
    assert frontier["M2"]["final_pair"] == frontier["M2"]["final_root"]


def test_trace_support_gate_requires_visibility_count_and_rate() -> None:
    visible = _row(
        logs_evidence=(),
        trace_evidence=(
            {"evidence_ref": "trace:0001", "service": "frontend"},
            {"evidence_ref": "trace:0002", "service": "checkout"},
        ),
    )
    report = audit.evaluate_trace_opportunity(
        [visible | {"private_case_key": f"private-{index}"} for index in range(4)]
        + [
            visible
            | {
                "private_case_key": "wrong-alternative",
                "metrics_alternative": {
                    **visible["metrics_alternative"],
                    "alternative_service": "catalog",
                },
            }
        ]
    )
    assert report["support_gate"] is True
    assert report["alternative_trace_visible"]["numerator"] == 4
    assert report["alternative_trace_visible"]["denominator"] == 4
    assert report["initial_alternative_trace_co_visible"] == 4
    assert report["causal_sufficiency"]["caller_callee_available"] is False


def test_message_contract_ablation_and_redundancy() -> None:
    rows = [_row(private_case_key=f"private-{index}") for index in range(4)]
    result = audit.evaluate_message_contracts(rows)
    assert result["C0"]["candidate_provenance_completeness"]["numerator"] == 0
    assert result["C2"]["candidate_provenance_completeness"]["numerator"] == 4
    assert result["C3"]["candidate_provenance_completeness"]["numerator"] == 0
    assert result["C4"]["field_completeness"]["numerator"] > result["C0"]["field_completeness"]["numerator"]
    assert set(audit._MESSAGE_CONTRACT_FIELDS["C0"]) == {
        "initial_service",
        "alternative_service",
        "initial_indicator",
        "logs_evidence",
        "visible_refs",
    }
    assert "gate_reason_codes" in audit._MESSAGE_CONTRACT_FIELDS["C1"]
    assert "alternative_rank" in audit._MESSAGE_CONTRACT_FIELDS["C2"]
    assert "initial_explanation" in audit._MESSAGE_CONTRACT_FIELDS["C3"]
    assert "bounded_metrics_evidence" in audit._MESSAGE_CONTRACT_FIELDS["C4"]
    assert "trace_evidence" in audit._MESSAGE_CONTRACT_FIELDS["C5"]
    assert "logs_evidence" not in audit._MESSAGE_CONTRACT_FIELDS["C5"]
    assert "bounded_metrics_evidence" not in audit._MESSAGE_CONTRACT_FIELDS["C5"]
    assert result["communication_repair_eligible"] is True
    assert result["cross_source_verifier_redundant"] is True


def test_metrics_robust_selector_requires_candidate_5_primary_gate() -> None:
    def frontier(net: int, rescue: int, damage: int) -> dict[str, Any]:
        return {
            name: {"net_rescue": 0, "root_rescue": 0, "root_damage": 0}
            for name in ("M0", "M1", "M2", "M3")
        } | {
            "M2": {
                "net_rescue": net,
                "root_rescue": rescue,
                "root_damage": damage,
            }
        }

    rejected = {
        "candidate-3": frontier(2, 2, 0),
        "candidate-4": frontier(1, 1, 0),
        "candidate-5": frontier(0, 0, 0),
    }
    assert audit._select_robust_metrics(rejected)["supported_rules"] == ()


def test_private_output_boundary_requires_git_external_root(tmp_path: Path) -> None:
    public_paths = (
        PROJECT_ROOT / "docs/analysis/public.json",
        PROJECT_ROOT / "docs/analysis/public.md",
    )
    with pytest.raises(ValueError, match="Git-external"):
        audit.validate_output_boundaries(PROJECT_ROOT / "private", public_paths)
    audit.validate_output_boundaries(tmp_path / "private", public_paths)


def test_architecture_decision_rules_are_exclusive() -> None:
    supported_metrics = {
        "supported_rules": ("M2",),
        "selected_rule": "M2",
    }
    trace = {"support_gate": True, "genuine_causal_information": True}
    communication = {
        "communication_repair_eligible": True,
        "cross_source_verifier_redundant": False,
    }
    fusion = {"positive_net_rescue": True}
    assert audit.select_architecture(
        supported_metrics, trace, communication, fusion
    )["decision"] == "METRICS_ARBITRATION"
    assert audit.select_architecture(
        {"supported_rules": (), "selected_rule": None},
        trace,
        communication,
        fusion,
    )["decision"] == "METRICS_PLUS_TRACE_VERIFICATION"
    assert audit.select_architecture(
        {"supported_rules": (), "selected_rule": None},
        {"support_gate": False, "genuine_causal_information": False},
        communication,
        fusion,
    )["decision"] == "COMMUNICATION_REPAIRED_CROSS_SOURCE_VERIFIER"
    assert audit.select_architecture(
        {"supported_rules": (), "selected_rule": None},
        {"support_gate": False, "genuine_causal_information": False},
        communication | {"cross_source_verifier_redundant": True},
        fusion,
    )["decision"] == "STRONG_SINGLE_RECOMMENDED"


@pytest.mark.parametrize(
    "payload",
    (
        {"case_id": "private-case"},
        {"nested": {"run_id": "0" * 32}},
        {"service": "checkout"},
        {"text": "metric:0001"},
        {"path": "/Users/example/private"},
    ),
)
def test_public_leakage_boundary_fails_closed(payload: object) -> None:
    with pytest.raises(ValueError, match="public payload"):
        audit.assert_public_payload(payload)


def test_public_rates_always_have_numerator_denominator_and_value() -> None:
    report = audit.build_public_report(
        {
            "candidate-3": [_row(candidate="candidate-3")],
            "candidate-4": [_row(candidate="candidate-4")],
            "candidate-5": [_row()],
        }
    )
    audit.assert_public_payload(report)
    audit.assert_rate_contract(report)


def test_no_provider_module_import_or_constructor_path(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(
        "provider" in name.casefold() or name == "ecomsre.model.gateway"
        for name in imported
    )

    original_import = builtins.__import__

    def reject_provider_import(name: str, *args: object, **kwargs: object) -> object:
        if "provider" in name.casefold() or name == "ecomsre.model.gateway":
            raise AssertionError("Provider construction/import is forbidden")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_provider_import)
    isolated = _load_module("rcaeval_multiagent_communication_audit_no_provider")
    assert isolated.evaluate_metrics_frontier([_row()])["M0"]["override_count"] == 0


def test_deterministic_output_stability() -> None:
    rows = {
        "candidate-3": [_row(candidate="candidate-3")],
        "candidate-4": [_row(candidate="candidate-4")],
        "candidate-5": [_row()],
    }
    first = audit.build_public_report(rows)
    second = audit.build_public_report(rows)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
