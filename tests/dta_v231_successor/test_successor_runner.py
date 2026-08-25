from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23
from ecomsre.dta_v2.v23.discovery_provider_v231 import (
    DISCOVERY_SYSTEM_PROMPT_V231,
)
from ecomsre.dta_v2.v23.evaluation_successor_v231 import (
    SuccessorEvaluationManifestV231,
    SuccessorFixedEvaluationArtifactV231,
    SuccessorLazyTruthStoreV231,
    SuccessorPreExecutionReviewV231,
    build_successor_evaluation_preflight_v231,
    run_successor_evaluation_once_v231,
)
import ecomsre.dta_v2.v23.evaluation_successor_v231 as successor_module


ROOT = Path(__file__).resolve().parents[2]


def _binding(relative: str) -> dict[str, str]:
    path = ROOT / relative
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_successor_preflight_does_not_parse_evaluator_truth() -> None:
    source = inspect.getsource(build_successor_evaluation_preflight_v231)

    assert "load_successor_truth_set_v231(" not in source
    assert "SuccessorTruthSetV231.model_validate" not in source


def test_successor_truth_loader_is_confined_to_post_arm_unlock() -> None:
    source = inspect.getsource(SuccessorLazyTruthStoreV231)
    unlock = source.index("def load_case_after_both_arms")
    load = source.index("load_successor_truth_shard_v231(")

    assert unlock < load
    assert "load_successor_truth_set_v231(" not in source
    assert "strict.case_id != case_id" in source
    assert "treatment.case_id != case_id" in source


def test_successor_truth_unlock_parses_only_the_completed_case_shard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = successor_module.load_successor_truth_shard_v231

    def spy_load(*, index_path: Path, binding):
        calls.append(binding.case_id)
        return original(index_path=index_path, binding=binding)

    monkeypatch.setattr(
        successor_module,
        "load_successor_truth_shard_v231",
        spy_load,
    )
    store = SuccessorLazyTruthStoreV231(
        ROOT / "config/dta-v231-successor/evaluation/truth-index.json"
    )
    arm = SimpleNamespace(case_id="vx-101")

    truth = store.load_case_after_both_arms(
        case_id="vx-101",
        strict=arm,
        treatment=arm,
    )

    assert truth.case_id == "vx-101"
    assert calls == ["vx-101"]
    assert store.loaded_case_ids == ("vx-101",)


def test_successor_runner_has_independent_write_once_boundary() -> None:
    source = inspect.getsource(run_successor_evaluation_once_v231)

    assert '.local/dta-v231-successor' in source
    assert "successor-evaluation.started.json" in source
    assert "successor-evaluation.partial.jsonl" in source
    assert "run_evaluation_case_pair_v231(" in source
    assert 'with output_path.open("x"' in source
    assert 'with output_markdown_path.open("x"' in source
    assert source.index('with output_path.open("x"') < source.index(
        '"status": "COMPLETE"'
    )


def test_successor_artifact_cannot_be_described_as_a_rerun() -> None:
    fields = SuccessorFixedEvaluationArtifactV231.model_fields

    assert fields["predecessor_study_disposition"].default == (
        "BLOCKED_DTA_V231_EVALUATION_DATA"
    )
    assert fields["study_relation"].default == "INDEPENDENT_SUCCESSOR_NOT_RERUN"


def test_successor_preflight_accepts_exact_frozen_surface_without_provider(
    tmp_path: Path,
) -> None:
    review_path = tmp_path / "review.json"
    output = ROOT / ".local/dta-v231-successor-preflight-probe.json"
    output_md = ROOT / ".local/dta-v231-successor-preflight-probe.md"
    manifest = SuccessorEvaluationManifestV231(
        schema_version="dta-v231.successor-evaluation-manifest.v1",
        base_commit="7fe2bff7186cca1cedd2513f7984709057fc19e5",
        branch="codex/dta-v231-successor-evaluation",
        provider_model="gpt-5.4-mini-2026-03-17",
        planned_case_count=24,
        planned_run_count=48,
        planned_execution_count=1,
        predecessor_freeze=_binding(
            "config/dta-v231-successor/predecessor-freeze.json"
        ),
        predecessor_freeze_verifier=_binding(
            "scripts/ci/verify_dta_v231_successor_freeze.py"
        ),
        predecessor_runtime_manifest=_binding(
            "config/dta-v231/evaluation/manifest.json"
        ),
        cases=_binding("config/dta-v231-successor/evaluation/cases.json"),
        truth_index=_binding(
            "config/dta-v231-successor/evaluation/truth-index.json"
        ),
        truth_shards=tuple(
            _binding(
                "config/dta-v231-successor/evaluation/truth/"
                f"vx-{ordinal:03d}.json"
            )
            for ordinal in range(101, 125)
        ),
        ontology_views=_binding(
            "config/dta-v231-successor/evaluation/ontology-views.json"
        ),
        admission_matrix=_binding(
            "config/dta-v231-successor/evaluation/admission-matrix.json"
        ),
        dataset_builder=_binding(
            "scripts/analysis/build_dta_v231_successor_fixed_set.py"
        ),
        admission_matrix_builder=_binding(
            "scripts/analysis/build_dta_v231_successor_admission_matrix.py"
        ),
        successor_runtime_sources=tuple(
            _binding(path)
            for path in (
                "scripts/analysis/run_dta_v231_successor_evaluation.py",
                "src/ecomsre/dta_v2/v23/evaluation_successor_v231.py",
            )
        ),
        strict_system_prompt_sha256=hashlib.sha256(
            DISCOVERY_SYSTEM_PROMPT_V23.encode()
        ).hexdigest(),
        treatment_system_prompt_sha256=hashlib.sha256(
            DISCOVERY_SYSTEM_PROMPT_V231.encode()
        ).hexdigest(),
        output_json=str(output.relative_to(ROOT)),
        output_markdown=str(output_md.relative_to(ROOT)),
        independent_review=str(review_path),
        fixed_at_utc=datetime.now(timezone.utc),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
    review_payload = {
        "schema_version": "dta-v231.successor-pre-execution-review.v1",
        "reviewer_identity": "independent-test-reviewer",
        "reviewer_task": "preflight-contract-probe",
        "reviewed_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "reviewed_admission_matrix_sha256": hashlib.sha256(
            (ROOT / "config/dta-v231-successor/evaluation/admission-matrix.json")
            .read_bytes()
        ).hexdigest(),
        "predecessor_preservation": "PASS",
        "data_admission": "PASS",
        "truth_blinding": "PASS",
        "write_once_execution": "PASS",
        "claim_accuracy": "PASS",
        "must_fix_count": 0,
        "status": "MUST_FIX_0_CLAIM_ACCURACY_PASS",
        "reviewed_at_utc": datetime.now(timezone.utc),
    }
    draft = SuccessorPreExecutionReviewV231.model_construct(
        **review_payload,
        review_sha256="0" * 64,
    )
    review = SuccessorPreExecutionReviewV231.model_validate(
        {
            **review_payload,
            "review_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"review_sha256"})
            ),
        }
    )
    review_path.write_text(review.model_dump_json(indent=2) + "\n")

    arguments = {
        "repository_root": ROOT,
        "cases_path": ROOT / "config/dta-v231-successor/evaluation/cases.json",
        "truth_index_path": ROOT
        / "config/dta-v231-successor/evaluation/truth-index.json",
        "ontology_views_path": ROOT
        / "config/dta-v231-successor/evaluation/ontology-views.json",
        "admission_matrix_path": ROOT
        / "config/dta-v231-successor/evaluation/admission-matrix.json",
        "manifest_path": manifest_path,
        "independent_review_path": review_path,
        "output_path": output,
        "output_markdown_path": output_md,
        "expected_provider_model": "gpt-5.4-mini-2026-03-17",
    }
    if (ROOT / ".local/dta-v231-successor").exists():
        with pytest.raises(
            FileExistsError,
            match="successor write-once boundary already exists",
        ):
            build_successor_evaluation_preflight_v231(**arguments)
        return

    preflight = build_successor_evaluation_preflight_v231(**arguments)

    assert preflight.status == "DTA_V231_SUCCESSOR_EVALUATION_PREFLIGHT_PASS"
    assert preflight.admission_status == "DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS"
    assert preflight.execution_count_before == 0
