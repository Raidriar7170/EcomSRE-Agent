from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval_v2.locks import (
    verify_evaluation_lock,
    verify_model_prompt_lock,
)


ROOT = Path(__file__).parents[3]
CONFIG = ROOT / "config" / "rcaeval-re2-v2-dev"
V1_CONFIG = ROOT / "config" / "rcaeval-re2-v1"
PROTOCOL_SHA256 = "110d95c388597d417bf0dc15b16c177e1d0dbdc60fb686b8f02edf3a244236ad"
SPLIT_LOCK_SHA256 = "14e88500098a282e89d4b1cee96d5c622aca9c91f35f73a6811cad9526909cba"
INDICATOR_FORMULA_SHA256 = "51a8373e72e924151d9e8749ffc6b2959eadee59cc0b11510f9d8f6d6ed2455a"
EVALUATION_LOCK_SHA256 = "69642f1bd675b7b3532651e434434fc42897f5f052665150597094d69d8cf992"


def _load(name: str) -> dict[str, object]:
    return json.loads((CONFIG / name).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_protocol_lock_has_exact_development_boundary_and_v1_bindings() -> None:
    protocol = _load("protocol.json")
    assert _sha(CONFIG / "protocol.json") == PROTOCOL_SHA256
    assert protocol["classification"] == [
        "DEVELOPMENT_VISIBLE",
        "NOT_EXTERNAL_HOLDOUT",
        "NOT_PRIMARY_INFERENCE",
    ]
    assert protocol["allowed_systems"] == ["RE2-OB", "RE2-SS"]
    assert protocol["forbidden_systems"] == ["RE2-TT"]
    assert protocol["semantic_attempts_per_run"] == 1
    assert protocol["semantic_retry"] == "FORBIDDEN"
    assert protocol["result_driven_retry"] == "FORBIDDEN"
    assert protocol["transport_retry"] == "FORBIDDEN"
    assert protocol["fallback"] == "NO_FALLBACK"
    assert protocol["terminal_denominator"] == "ALL_SCHEDULED_RUNS"
    assert protocol["v2_scope"] == "OBSERVABILITY_AND_INDICATOR_PIPELINE_ONLY"
    assert protocol["run_counts"] == {
        "design": 360,
        "dev_validation": 480,
        "total": 840,
    }
    assert protocol["cross_hash_policy"]["evaluation_root"] == (
        "evaluation-lock.json"
    )
    bindings = protocol["v1_bindings"]
    assert bindings["frozen_implementation_commit"] == "3a03995037ce410488a4364f8a485b27c80f0ac0"
    assert bindings["attribution_commit"] == "3991102f6fb228568d6c620c386340a3956ac949"
    assert bindings["pr13_merge_commit"] == "095dfd95964df9d77da06dcfb1b31023185b3f41"
    assert bindings["frozen_scope_mutation"] == "FORBIDDEN"
    assert bindings["frozen_result_reinterpretation"] == "FORBIDDEN"


def test_dataset_and_budget_locks_cross_bind_protocol_and_exact_v1_sources() -> None:
    dataset = _load("dataset-lock.json")
    budget = _load("budget-lock.json")
    formulas = _load("indicator-candidate-formulas.json")
    assert _sha(CONFIG / "indicator-candidate-formulas.json") == (
        INDICATOR_FORMULA_SHA256
    )
    assert dataset["protocol_sha256"] == PROTOCOL_SHA256
    assert budget["protocol_sha256"] == PROTOCOL_SHA256
    assert formulas["protocol_id"] == "rcaeval-re2-v2-dev-v1"
    assert formulas["protocol_sha256"] == PROTOCOL_SHA256
    assert formulas["dataset_lock_sha256"] == _sha(CONFIG / "dataset-lock.json")
    assert dataset["expected_total_cases"] == 180
    assert dataset["identity_fields"] == [
        "system",
        "root_cause_service",
        "fault",
        "instance",
    ]
    assert dataset["telemetry_values_in_identity"] is False
    assert dataset["model_outputs_in_identity"] is False
    systems = dataset["systems"]
    assert systems["RE2-OB"]["traces"] == "REQUIRED"
    assert systems["RE2-SS"]["traces"] == "FORBIDDEN"
    assert budget["global"] == {
        "design_architecture_runs": 360,
        "dev_validation_architecture_runs": 480,
        "hard_max_provider_operations": 2600,
        "max_architecture_runs": 840,
        "max_estimated_tokens": 26880000,
        "provider_operation_headroom": 80,
        "worst_case_provider_operations": 2520,
    }
    assert budget["operation_budget"] == {
        "design": 1200,
        "dev_validation": 1320,
        "smoke_additional": 0,
    }


def test_v1_config_hashes_are_live_exact_and_all_locks_are_placeholder_free() -> None:
    protocol = _load("protocol.json")
    expected = protocol["v1_config_hashes"]
    assert expected == {
        path.name: _sha(path) for path in sorted(V1_CONFIG.glob("*.json"))
    }
    for name in (
        "protocol.json",
        "dataset-lock.json",
        "budget-lock.json",
        "indicator-candidate-formulas.json",
    ):
        text = (CONFIG / name).read_text(encoding="utf-8")
        assert "TBD" not in text
        assert "0" * 64 not in text
        assert "/Users/" not in text
        assert "/home/" not in text
        assert "/private/" not in text


def test_split_lock_is_frozen_to_the_first_real_ob_ss_assignment() -> None:
    split = _load("split-lock.json")
    assert _sha(CONFIG / "split-lock.json") == SPLIT_LOCK_SHA256
    assert split["counts"] == {
        "design": 60,
        "design_re2_ob": 30,
        "design_re2_ss": 30,
        "dev_validation": 120,
        "dev_validation_re2_ob": 60,
        "dev_validation_re2_ss": 60,
        "strata": 60,
        "total": 180,
    }
    assert split["assignment_manifest_sha256"] == (
        "917b1043b1444b1099418ebf0ac5308692dceabe9e0baaa230a4020b83841b40"
    )
    assert split["protocol_sha256"] == PROTOCOL_SHA256
    assert split["dataset_lock_sha256"] == _sha(CONFIG / "dataset-lock.json")


def test_model_prompt_lock_is_exactly_bound_to_live_prompts_and_schemas() -> None:
    lock = verify_model_prompt_lock()

    assert lock["model"] == "gpt-5.4-mini-2026-03-17"
    assert lock["temperature"] == 0.0
    assert lock["top_p"] == 1.0
    assert lock["max_completion_tokens"] == 2048
    assert lock["retry"] == {
        "semantic": "FORBIDDEN",
        "transport": "FORBIDDEN",
        "fallback": "NO_FALLBACK",
    }


def test_evaluation_root_lock_binds_every_prerequisite_and_is_fail_closed() -> None:
    lock = verify_evaluation_lock()

    assert _sha(CONFIG / "evaluation-lock.json") == EVALUATION_LOCK_SHA256
    assert lock["indicator_selection"]["formula"] == "F0"
    assert lock["run_counts"] == {
        "design": 360,
        "dev_validation": 480,
        "total": 840,
    }
    assert lock["freeze_timing"] == {
        "created_after_provider_smoke_termination": True,
        "negative_gate_evidence_only": True,
        "retroactive_provider_authorization": False,
    }
