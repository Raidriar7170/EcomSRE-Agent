#!/usr/bin/env python3
"""Freeze the admitted DTA v2.3.3 execution surface before Provider use."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from ecomsre.dta_v2.v23.discovery_provider_v233 import (
    DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233,
)
from ecomsre.dta_v2.v23.evaluation_data_v233 import load_admission_matrix_v233
from ecomsre.dta_v2.v23.evaluation_study_v233 import EvaluationManifestV233
from ecomsre.dta_v2.v23.evaluation_v233 import EvaluationPolicyV233
from ecomsre.dta_v2.v23.runtime_preflight_v233 import RuntimePreflightV233


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-model", required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    branch = subprocess.run(
        ("git", "branch", "--show-current"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "codex/dta-v233-domain-witness-guard":
        raise ValueError("v2.3.3 manifest may only freeze on its named branch")
    evaluation_root = root / "config/dta-v233/evaluation"
    output = evaluation_root / "manifest.json"
    if output.exists():
        raise FileExistsError("v2.3.3 manifest is write-once")
    admission = load_admission_matrix_v233(evaluation_root / "admission-matrix.json")
    preflight = RuntimePreflightV233.model_validate_json(
        (root / "docs/analysis/dta-v233-runtime-preflight.json").read_bytes()
    )
    if admission.terminal != "DTA_V233_EVALUATION_DATA_PASS":
        raise ValueError("v2.3.3 admission matrix did not pass")
    if preflight.terminal != "DTA_V233_RUNTIME_PREFLIGHT_PASS":
        raise ValueError("v2.3.3 runtime preflight did not pass")
    subprocess.run(
        ("python", "scripts/ci/verify_dta_v233_history.py"),
        cwd=root,
        check=True,
    )
    historical = json.loads(
        (root / "config/dta-v233/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )
    historical_paths = tuple(
        str(item["path"]) for item in historical.get("bindings", ())
    )
    repair_candidates = (
        "docs/analysis/dta-v233-provider-smoke.json",
        "docs/analysis/dta-v233-provider-smoke-repair-1.json",
        "docs/analysis/dta-v233-provider-smoke-repair-2.json",
        "docs/analysis/dta-v233-provider-smoke-repair-2-diagnostic.json",
        "docs/analysis/dta-v233-provider-smoke-repair-2-totality-addendum.json",
    )
    repair_paths = tuple(
        path for path in repair_candidates if (root / path).is_file()
    )
    frozen_paths = tuple(
        sorted(
            {
                "config/dta-v233/evaluation/admission-matrix.json",
                "config/dta-v233/evaluation/cases.json",
                "config/dta-v233/evaluation/ontology-views.json",
                "config/dta-v233/evaluation/strata.json",
                "config/dta-v233/evaluation/truth.json",
                "config/dta-v233/historical-results.v1.json",
                "docs/analysis/dta-v233-runtime-preflight.json",
                "scripts/analysis/build_dta_v233_admission_matrix.py",
                "scripts/analysis/build_dta_v233_fixed_set.py",
                "scripts/analysis/freeze_dta_v233_manifest.py",
                "scripts/analysis/run_dta_v233_evaluation.py",
                "scripts/analysis/run_dta_v233_provider_smoke.py",
                "scripts/analysis/run_dta_v233_runtime_preflight.py",
                "scripts/ci/verify_dta_v233_history.py",
                "src/ecomsre/dta_v2/v23/contracts_v233.py",
                "src/ecomsre/dta_v2/v23/contradiction_witness_v233.py",
                "src/ecomsre/dta_v2/v23/discovery_provider_v233.py",
                "src/ecomsre/dta_v2/v23/discovery_runtime_v233.py",
                "src/ecomsre/dta_v2/v23/domain_audit_v233.py",
                "src/ecomsre/dta_v2/v23/domain_projection_v233.py",
                "src/ecomsre/dta_v2/v23/evaluation_data_v233.py",
                "src/ecomsre/dta_v2/v23/evaluation_study_v233.py",
                "src/ecomsre/dta_v2/v23/evaluation_v233.py",
                "src/ecomsre/dta_v2/v23/irreconcilable_guard_v233.py",
                "src/ecomsre/dta_v2/v23/runtime_preflight_v233.py",
                "src/ecomsre/dta_v2/v23/witness_audit_v233.py",
                *historical_paths,
                *repair_paths,
            }
        )
    )
    bindings = []
    for relative in frozen_paths:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"v2.3.3 freeze input is absent: {relative}")
        bindings.append({"path": relative, "sha256": _sha256(path)})
    manifest = EvaluationManifestV233.model_validate(
        {
            "schema_version": "dta-v233.evaluation-manifest.v1",
            "base_commit": "447e7a8ed4c8b9d592c16d181f5709bdfdc3d4cb",
            "branch": branch,
            "provider_model": args.provider_model,
            "planned_case_count": 28,
            "planned_run_count": 84,
            "planned_execution_count": 1,
            "policies": tuple(EvaluationPolicyV233),
            "frozen_files": tuple(bindings),
            "provider_system_prompt_sha256": hashlib.sha256(
                DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233.encode("utf-8")
            ).hexdigest(),
            "provider_smoke_output": "docs/analysis/dta-v233-provider-smoke.json",
            "output_json": "docs/results/dta-v233-domain-guard-evaluation.json",
            "output_markdown": "docs/results/dta-v233-domain-guard-evaluation.md",
            "independent_review": "docs/external-reviews/dta-v233-pre-execution-review.md",
            "fixed_at_utc": datetime.now(timezone.utc),
        }
    )
    with output.open("x", encoding="utf-8") as handle:
        handle.write(manifest.model_dump_json(indent=2) + "\n")
    print("DTA_V233_EVALUATION_SURFACE_FROZEN")
    print(f"manifest_sha256={_sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
