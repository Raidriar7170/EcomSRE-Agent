#!/usr/bin/env python3
"""Freeze the admitted successor data and its immutable execution surface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23
from ecomsre.dta_v2.v23.discovery_provider_v231 import DISCOVERY_SYSTEM_PROMPT_V231
from ecomsre.dta_v2.v23.evaluation_successor_v231 import (
    AdmissionMatrixV231Successor,
    SuccessorEvaluationManifestV231,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"successor freeze input is absent: {relative}")
    return {"path": relative, "sha256": _sha256(path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.repository_root.resolve()
    branch = subprocess.run(
        ("git", "branch", "--show-current"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "codex/dta-v231-successor-evaluation":
        raise ValueError("successor manifest may only freeze on its named branch")
    freeze = json.loads(
        (root / "config/dta-v231-successor/predecessor-freeze.json").read_text(
            encoding="utf-8"
        )
    )
    if freeze["predecessor_engineering_state"] != (
        "BLOCKED_DTA_V231_EVALUATION_DATA"
    ):
        raise ValueError("successor predecessor state differs")
    evaluation_root = root / "config/dta-v231-successor/evaluation"
    matrix = AdmissionMatrixV231Successor.model_validate_json(
        (evaluation_root / "admission-matrix.json").read_bytes()
    )
    if matrix.status != "DTA_V231_SUCCESSOR_EVALUATION_DATA_PASS":
        raise ValueError("successor admission matrix did not pass")
    strict_prompt_sha256 = hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V23.encode("utf-8")
    ).hexdigest()
    treatment_prompt_sha256 = hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V231.encode("utf-8")
    ).hexdigest()
    if (
        strict_prompt_sha256 != freeze["strict_system_prompt_sha256"]
        or treatment_prompt_sha256 != freeze["treatment_system_prompt_sha256"]
    ):
        raise ValueError("successor Provider prompt freeze differs")
    runtime_paths = (
        "scripts/analysis/run_dta_v231_successor_evaluation.py",
        "src/ecomsre/dta_v2/v23/evaluation_successor_v231.py",
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.successor-evaluation-manifest.v1",
        "base_commit": "7fe2bff7186cca1cedd2513f7984709057fc19e5",
        "branch": "codex/dta-v231-successor-evaluation",
        "provider_model": freeze["provider_model"],
        "planned_case_count": 24,
        "planned_run_count": 48,
        "planned_execution_count": 1,
        "predecessor_study_disposition": "BLOCKED_DTA_V231_EVALUATION_DATA",
        "study_relation": "INDEPENDENT_SUCCESSOR_NOT_RERUN",
        "predecessor_freeze": _binding(
            root, "config/dta-v231-successor/predecessor-freeze.json"
        ),
        "predecessor_freeze_verifier": _binding(
            root, "scripts/ci/verify_dta_v231_successor_freeze.py"
        ),
        "predecessor_runtime_manifest": _binding(
            root, "config/dta-v231/evaluation/manifest.json"
        ),
        "cases": _binding(
            root, "config/dta-v231-successor/evaluation/cases.json"
        ),
        "truth_index": _binding(
            root, "config/dta-v231-successor/evaluation/truth-index.json"
        ),
        "truth_shards": tuple(
            _binding(
                root,
                (
                    "config/dta-v231-successor/evaluation/truth/"
                    f"vx-{ordinal:03d}.json"
                ),
            )
            for ordinal in range(101, 125)
        ),
        "ontology_views": _binding(
            root, "config/dta-v231-successor/evaluation/ontology-views.json"
        ),
        "admission_matrix": _binding(
            root, "config/dta-v231-successor/evaluation/admission-matrix.json"
        ),
        "dataset_builder": _binding(
            root, "scripts/analysis/build_dta_v231_successor_fixed_set.py"
        ),
        "admission_matrix_builder": _binding(
            root, "scripts/analysis/build_dta_v231_successor_admission_matrix.py"
        ),
        "successor_runtime_sources": tuple(
            _binding(root, path) for path in sorted(runtime_paths)
        ),
        "strict_system_prompt_sha256": strict_prompt_sha256,
        "treatment_system_prompt_sha256": treatment_prompt_sha256,
        "output_json": (
            "docs/results/dta-v231-successor-conflict-aware-evaluation.json"
        ),
        "output_markdown": (
            "docs/results/dta-v231-successor-conflict-aware-evaluation.md"
        ),
        "independent_review": (
            "docs/external-reviews/"
            "dta-v231-successor-pre-execution-review.json"
        ),
        "fixed_at_utc": datetime.now(timezone.utc),
    }
    manifest = SuccessorEvaluationManifestV231.model_validate(payload)
    output = evaluation_root / "manifest.json"
    with output.open("x", encoding="utf-8") as handle:
        handle.write(manifest.model_dump_json(indent=2) + "\n")
    print("DTA_V231_SUCCESSOR_EVALUATION_SURFACE_FROZEN")
    print(f"manifest_sha256={_sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
