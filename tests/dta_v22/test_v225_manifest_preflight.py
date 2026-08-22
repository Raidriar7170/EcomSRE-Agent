from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V225,
    StudyCombinationV225,
)
from ecomsre.dta_v2.v22.evaluation_manifest_v225 import (
    EvaluationManifestV225,
    FrozenFileBindingV225,
    build_schedule_v225,
    canonical_bindings_sha256_v225,
    schedule_sha256_v225,
    sha256_file_v225,
)
from ecomsre.dta_v2.v22.evaluation_preflight_v225 import (
    verify_agent_visible_inventory_v225,
    verify_current_bindings_v225,
    verify_opaque_lint_report_v225,
    verify_outputs_absent_v225,
)
from ecomsre.dta_v2.v22.provider_payload_lint_report_v225 import (
    build_provider_payload_lint_report_v225,
)
from ecomsre.dta_v2.v22.provider_smoke_v225 import (
    run_protocol_smoke_simulations_v225,
)
from ecomsre.dta_v2.v22.offline_simulation_v225 import (
    simulate_fail_closed_contracts_v225,
)


ROOT = Path(__file__).resolve().parents[2]


def _binding(root: Path, relative: str) -> FrozenFileBindingV225:
    return FrozenFileBindingV225(
        path=relative,
        sha256=sha256_file_v225(root / relative),
    )


def test_v225_prompt_file_matches_frozen_selection_prompt() -> None:
    assert (ROOT / "config/dta-v22-5/prompt.txt").read_text(
        encoding="utf-8"
    ) == SHARED_SELECTION_SYSTEM_PROMPT_V225


def test_v225_schedule_is_balanced_and_digest_is_deterministic() -> None:
    schedule = build_schedule_v225()
    assert len(schedule) == 64
    assert tuple(item.ordinal for item in schedule) == tuple(range(1, 65))
    for case_number in range(1, 17):
        case_id = f"e{case_number:02d}"
        case_entries = tuple(item for item in schedule if item.case_id == case_id)
        assert {item.combination for item in case_entries} == set(StudyCombinationV225)
        assert {item.execution_position for item in case_entries} == {1, 2, 3, 4}
    for start in range(0, 16, 4):
        block = tuple(
            item for item in schedule if start < int(item.case_id[1:]) <= start + 4
        )
        for combination in StudyCombinationV225:
            assert sorted(
                item.execution_position
                for item in block
                if item.combination is combination
            ) == [1, 2, 3, 4]
    assert schedule_sha256_v225(schedule) == schedule_sha256_v225(
        build_schedule_v225()
    )


def test_v225_static_and_rendered_opaque_lint_covers_all_payload_classes() -> None:
    report = build_provider_payload_lint_report_v225(repository_root=ROOT)
    assert report.terminal == "OPAQUE_PROVIDER_IDENTITY_LINT_PASS"
    assert report.evaluation_files_scanned == 16
    assert set(report.rendered_payload_classes) == {
        "bootstrap",
        "post-individual-read",
        "post-bundle-read",
        "terminal-only",
        "repair",
    }
    assert report.forbidden_identity_value_count == 0
    assert report.provider_case_id_count == 0
    assert report.provider_evaluator_metadata_field_count == 0


def test_v225_protocol_smoke_bounds_repairs_retries_and_exact_request_identity() -> None:
    assert run_protocol_smoke_simulations_v225() == {
        "invalid_alias_repair": True,
        "stale_alias_repair": True,
        "http_429_retry_simulation": True,
        "timeout_retry_simulation": True,
        "exact_request_retry_identity": True,
    }


def test_v225_offline_fail_closed_simulation_covers_all_required_states() -> None:
    assert simulate_fail_closed_contracts_v225() == {
        "budget_insufficient_typed_abstain": True,
        "source_failure_typed_abstain": True,
        "preclosure_target_coverage_preserved": True,
        "preclosure_bundle_coverage_preserved": True,
        "partial_journal_recovery_required": False,
    }


def test_v225_binding_verification_rejects_one_byte_source_and_implementation_drift(
    tmp_path: Path,
) -> None:
    names = (
        "prompt.txt",
        "cases.json",
        "truth.json",
        "coverage.json",
        "utility.json",
        "strata.json",
        "identity.json",
        "lint.json",
        "history.json",
        "prior.json",
        "development.json",
        "runtime.py",
    )
    for name in names:
        (tmp_path / name).write_bytes(f"source-{name}\n".encode())
    bindings = {name: _binding(tmp_path, name) for name in names}
    binding = bindings["runtime.py"]
    manifest = EvaluationManifestV225.model_construct(
        prompt=bindings["prompt.txt"],
        case_set=bindings["cases.json"],
        truth_set=bindings["truth.json"],
        target_coverage=bindings["coverage.json"],
        utility_audit=bindings["utility.json"],
        evaluator_strata=bindings["strata.json"],
        opaque_identity_plan=bindings["identity.json"],
        opaque_lint_report=bindings["lint.json"],
        historical_results_manifest=bindings["history.json"],
        predicate_yield_prior=bindings["prior.json"],
        development_result=bindings["development.json"],
        agent_visible_sources=(),
        implementation_sources=(binding,),
        v22_runtime_tree_sha256=canonical_bindings_sha256_v225((binding,)),
    )
    verify_current_bindings_v225(manifest=manifest, repository_root=tmp_path)
    (tmp_path / "runtime.py").write_bytes(b"source-b\n")
    with pytest.raises(ValueError, match="frozen binding differs"):
        verify_current_bindings_v225(manifest=manifest, repository_root=tmp_path)


def test_v225_preflight_rejects_unlisted_agent_visible_file(tmp_path: Path) -> None:
    source_root = tmp_path / "config/dta-v22-5/evaluation/agent-visible"
    source_root.mkdir(parents=True)
    bindings = []
    for index in range(1, 17):
        relative = f"config/dta-v22-5/evaluation/agent-visible/e{index:02d}.json"
        (tmp_path / relative).write_text("{}\n", encoding="utf-8")
        bindings.append(_binding(tmp_path, relative))
    (source_root / "unlisted.json").write_text("{}\n", encoding="utf-8")
    manifest = EvaluationManifestV225.model_construct(
        agent_visible_sources=tuple(bindings),
        case_set=bindings[0],
    )
    with pytest.raises(ValueError, match="unlisted agent-visible"):
        verify_agent_visible_inventory_v225(
            manifest=manifest, repository_root=tmp_path
        )


def test_v225_preflight_rejects_existing_final_output(tmp_path: Path) -> None:
    relative = "result.json"
    (tmp_path / relative).write_text("{}\n", encoding="utf-8")
    manifest = EvaluationManifestV225.model_construct(
        expected_output_paths=(relative, "result.md", "result.json.partial.jsonl")
    )
    with pytest.raises(ValueError, match="final output already exists"):
        verify_outputs_absent_v225(manifest=manifest, repository_root=tmp_path)


def test_v225_preflight_rejects_opaque_lint_failure(tmp_path: Path) -> None:
    relative = "lint.json"
    (tmp_path / relative).write_text(
        json.dumps(
            {
                "terminal": "OPAQUE_PROVIDER_IDENTITY_LINT_FAIL",
                "forbidden_identity_value_count": 1,
                "provider_case_id_count": 0,
                "provider_evaluator_metadata_field_count": 0,
                "rendered_payload_classes": [],
                "evaluation_files_scanned": 16,
            }
        ),
        encoding="utf-8",
    )
    manifest = EvaluationManifestV225.model_construct(
        opaque_lint_report=_binding(tmp_path, relative)
    )
    with pytest.raises(ValueError, match="opaque identity lint did not pass"):
        verify_opaque_lint_report_v225(manifest=manifest, repository_root=tmp_path)
