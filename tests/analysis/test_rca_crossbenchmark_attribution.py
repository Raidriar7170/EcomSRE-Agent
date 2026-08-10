from __future__ import annotations

import hashlib
import importlib.util
import json
from argparse import Namespace
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from ecomsre_rca_unified.contracts import (
    ArchitectureOption,
    CanonicalEntityLayer,
    FrontierOutcome,
    RootProvenance,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts/analysis/rca_crossbenchmark_attribution.py"


def _load_module(name: str = "rca_crossbenchmark_attribution_test") -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


attribution = _load_module()


def test_tree_digest_is_content_path_and_order_bound(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    (root / "b").write_text("two", encoding="utf-8")
    (root / "a").write_text("one", encoding="utf-8")
    digest, count, byte_count = attribution.tree_digest(root)
    expected = hashlib.sha256()
    for path in (root / "a", root / "b"):
        payload = path.read_bytes()
        expected.update(
            f"{hashlib.sha256(payload).hexdigest()}  {path.resolve()}\n".encode()
        )
    assert digest == expected.hexdigest()
    assert (count, byte_count) == (2, 6)


def test_create_once_json_reuses_exact_and_rejects_drift(tmp_path: Path) -> None:
    target = tmp_path / "private" / "lock.json"
    attribution.write_json_create_once(target, {"value": 1})
    attribution.write_json_create_once(target, {"value": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"value": 1}
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="differs"):
        attribution.write_json_create_once(target, {"value": 2})


def test_provider_environment_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    attribution.assert_no_provider_environment()
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    with pytest.raises(ValueError, match="Provider environment"):
        attribution.assert_no_provider_environment()


def test_public_scan_rejects_case_and_private_material() -> None:
    attribution.assert_public_payload({"aggregate": {"denominator": 3}})
    with pytest.raises(ValueError, match="forbidden"):
        attribution.assert_public_payload({"case_id": "synthetic"})
    with pytest.raises(ValueError, match="forbidden"):
        attribution.assert_public_payload({"note": "/Users/synthetic/private"})


def test_visibility_aggregate_covers_all_goal_per_source_dimensions() -> None:
    unified = SimpleNamespace(
        ground_truth_equivalent_entities=frozenset({"root"}),
        initial_entity="symptom",
        metrics_candidates=(SimpleNamespace(entity="symptom"),),
    )
    source_values = {
        "metrics": True,
        "logs": False,
        "traces": False,
        "events": False,
        "alerts": False,
        "topology": True,
    }
    visibility_record = {
        "ground_truth_visible": {
            **source_values,
            "catalog": True,
            "any_model_visible": True,
            "causal": False,
        },
        "ground_truth_service_visible": {
            **source_values,
            "catalog": True,
            "any_model_visible": True,
            "causal": False,
        },
        "initial_visible": {
            "metrics": True,
            "logs": False,
            "traces": False,
            "events": False,
            "alerts": True,
            "topology": True,
        },
        "metrics_top1_visible": {
            "metrics": True,
            "logs": False,
            "traces": False,
            "events": False,
            "alerts": True,
            "topology": True,
        },
        "initial_and_ground_truth_co_visible": {
            source: source_values[source]
            and source in {"metrics", "topology"}
            for source in source_values
        },
        "metrics_top1_and_ground_truth_co_visible": {
            source: source_values[source]
            and source in {"metrics", "topology"}
            for source in source_values
        },
    }
    report = attribution._visibility_aggregate(
        [
            SimpleNamespace(
                unified=unified,
                visibility_record=visibility_record,
            )
        ]
    )
    assert set(report["per_source"]) == {
        "metrics",
        "logs",
        "traces",
        "events",
        "alerts",
        "topology",
    }
    assert set(report["per_source"]["metrics"]) == {
        "ground_truth_exact_visible",
        "ground_truth_service_visible",
        "initial_visible",
        "metrics_top1_visible",
        "initial_and_ground_truth_co_visible",
        "metrics_top1_and_ground_truth_co_visible",
    }
    assert report["per_source"]["metrics"]["initial_visible"]["numerator"] == 1
    assert (
        report["per_source"]["logs"]["initial_and_ground_truth_co_visible"]
        ["numerator"]
        == 0
    )


def test_option_summary_reports_layer_downstream_and_tool_use_denominators() -> None:
    outcome = FrontierOutcome(
        private_case_key="private",
        option=ArchitectureOption.A0,
        initial_entity="symptom",
        final_entity="symptom",
        fault_type="memory pressure",
        root_provenance=RootProvenance.MODEL_INITIAL,
        decision_reason="KEEP",
        override=False,
        initial_exact_correct=False,
        final_exact_correct=False,
        initial_service_correct=False,
        final_service_correct=False,
        initial_pair_correct=False,
        final_pair_correct=False,
    )
    adapted = SimpleNamespace(
        unified=SimpleNamespace(
            ground_truth_layer=CanonicalEntityLayer.SERVICE,
            initial_entity="symptom",
            initial_layer=CanonicalEntityLayer.POD,
            m3_final_entity="symptom",
            m3_final_layer=CanonicalEntityLayer.POD,
            metrics_candidates=(),
        ),
        propagation_record={
            "initial_role": "DOWNSTREAM_SYMPTOM",
            "m3_role": "DOWNSTREAM_SYMPTOM",
            "metrics_candidate_roles": [],
        },
    )
    summary = attribution._public_outcome_summary(
        [outcome], [adapted], ArchitectureOption.A0
    )
    assert summary["entity_layer_error"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert summary["downstream_symptom_selection"]["numerator"] == 1
    assert summary["expected_tool_use"]["case_denominator"] == 1
    assert summary["expected_tool_use"]["external_tool_calls"]["total"] == 0


def test_corrected_v3_verifier_requires_complete_goal_coverage() -> None:
    rate_103 = {"numerator": 0, "denominator": 103, "value": 0.0}
    dimensions = {
        name: dict(rate_103)
        for name in (
            "ground_truth_exact_visible",
            "ground_truth_service_visible",
            "initial_visible",
            "metrics_top1_visible",
            "initial_and_ground_truth_co_visible",
            "metrics_top1_and_ground_truth_co_visible",
        )
    }
    propagation = {
        "visibility": {
            "per_source": {
                source: dimensions
                for source in (
                    "metrics",
                    "logs",
                    "traces",
                    "events",
                    "alerts",
                    "topology",
                )
            }
        },
        "cross_benchmark_contrast": {
            "fault_family": {},
            "propagation_length": {},
        },
        "strong_single_failure_decomposition": {
            name: dict(rate_103)
            for name in attribution._STRONG_SINGLE_FAILURE_CLASSES
        },
        "historical_m3_override_decomposition": {
            name: dict(rate_103) for name in attribution._M3_OVERRIDE_CLASSES
        },
        "fault_phrase_relation": {
            name: dict(rate_103) for name in attribution._FAULT_RELATION_CLASSES
        },
    }

    def summary(denominator: int) -> dict[str, object]:
        return {
            "denominator": denominator,
            "entity_layer_error": {
                "numerator": 0,
                "denominator": denominator,
                "value": 0.0,
            },
            "downstream_symptom_selection": {
                "numerator": 0,
                "denominator": denominator,
                "value": 0.0,
            },
            "expected_tool_use": {"case_denominator": denominator},
        }

    frontier = {
        "options": {
            option: {
                "all_consumed": summary(463),
                "rca100": summary(103),
                "obss_fixtures": {"synthetic": summary(60)},
            }
            for option in ("A0", "A1", "A2", "A3", "A4")
        }
    }
    attribution._verify_corrected_v3_goal_coverage(propagation, frontier)
    del propagation["cross_benchmark_contrast"]["propagation_length"]
    with pytest.raises(ValueError, match="propagation_length"):
        attribution._verify_corrected_v3_goal_coverage(propagation, frontier)


def test_parser_exposes_append_only_corrected_v3_lifecycle() -> None:
    args = attribution._parser().parse_args(
        [
            "freeze-corrected-v3-implementation",
            "--private-root",
            "/tmp/synthetic-private",
        ]
    )
    assert args.handler is attribution.freeze_corrected_v3_implementation


def test_script_has_no_provider_import_or_network_path() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8").casefold()
    assert "new_v1_reference_provider" not in text
    assert "stdlibopenaicompatibletransport" not in text
    assert "requests." not in text
    assert "urllib" not in text


def test_methodology_freeze_binds_input_lock_frontier_and_methodology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    frontier = tmp_path / "frontier.json"
    frontier.write_text('{"options": {"A0": {}}}\n', encoding="utf-8")
    methodology = tmp_path / "methodology.json"
    methodology.write_text(
        (
            PROJECT_ROOT
            / "config/rca-crossbenchmark-architecture-convergence-v1/methodology.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    attribution.write_json_create_once(
        private_root / "locks/input-and-frontier-lock.json",
        {
            "frontier": {"sha256": attribution.sha256_file(frontier)},
            "provider_calls": 0,
            "semantic_operations": 0,
        },
    )
    attribution.write_json_create_once(
        private_root / "state/INPUTS_AND_FRONTIER_FROZEN.json",
        {
            "state": "INPUTS_AND_FRONTIER_FROZEN",
            "lock_sha256": attribution.sha256_file(
                private_root / "locks/input-and-frontier-lock.json"
            ),
        },
    )
    monkeypatch.setattr(attribution, "_git_head", lambda: attribution.EXPECTED_BASE_COMMIT)
    assert (
        attribution.freeze_methodology(
            Namespace(
                private_root=private_root,
                frontier=frontier,
                methodology=methodology,
            )
        )
        == 0
    )
    lock = json.loads(
        (private_root / "locks/attribution-methods-lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert lock["input_frontier_lock_sha256"] == attribution.sha256_file(
        private_root / "locks/input-and-frontier-lock.json"
    )
    assert lock["frontier_sha256"] == attribution.sha256_file(frontier)
    assert lock["methodology_sha256"] == attribution.sha256_file(methodology)
    assert lock["provider_calls"] == 0
    state = json.loads(
        (private_root / "state/ATTRIBUTION_METHODS_FROZEN.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["state"] == "ATTRIBUTION_METHODS_FROZEN"


def test_methodology_freeze_rejects_input_state_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    frontier = tmp_path / "frontier.json"
    frontier.write_text('{"options": {"A0": {}}}\n', encoding="utf-8")
    methodology = (
        PROJECT_ROOT
        / "config/rca-crossbenchmark-architecture-convergence-v1/methodology.json"
    )
    attribution.write_json_create_once(
        private_root / "locks/input-and-frontier-lock.json",
        {"frontier": {"sha256": attribution.sha256_file(frontier)}},
    )
    attribution.write_json_create_once(
        private_root / "state/INPUTS_AND_FRONTIER_FROZEN.json",
        {"state": "INPUTS_AND_FRONTIER_FROZEN", "lock_sha256": "wrong"},
    )
    monkeypatch.setattr(attribution, "_git_head", lambda: attribution.EXPECTED_BASE_COMMIT)
    with pytest.raises(ValueError, match="input/frontier state binding"):
        attribution.freeze_methodology(
            Namespace(
                private_root=private_root,
                frontier=frontier,
                methodology=methodology,
            )
        )
