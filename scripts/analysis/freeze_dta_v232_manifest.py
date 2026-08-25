#!/usr/bin/env python3
"""Freeze the admitted DTA v2.3.2 data and execution surface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
from typing import Any

from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
)
from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23
from ecomsre.dta_v2.v23.discovery_provider_v231 import (
    DISCOVERY_SYSTEM_PROMPT_V231,
)
from ecomsre.dta_v2.v23.evaluation_data_v232 import AdmissionMatrixV232
from ecomsre.dta_v2.v23.evaluation_study_v232 import EvaluationManifestV232
from ecomsre.dta_v2.v23.runtime_preflight_v232 import (
    RuntimeTotalityPreflightV232,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"v2.3.2 freeze input is absent: {relative}")
    return {"path": relative, "sha256": _sha256(path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--provider-model",
        default="gpt-5.4-mini-2026-03-17",
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    branch = subprocess.run(
        ("git", "branch", "--show-current"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "codex/dta-v232-anomaly-totality-successor":
        raise ValueError("v2.3.2 manifest may only freeze on its named branch")
    evaluation_root = root / "config/dta-v232/evaluation"
    matrix = AdmissionMatrixV232.model_validate_json(
        (evaluation_root / "admission-matrix.json").read_bytes()
    )
    preflight = RuntimeTotalityPreflightV232.model_validate_json(
        (
            root / "docs/analysis/dta-v232-runtime-totality-preflight.json"
        ).read_bytes()
    )
    if matrix.status != "DTA_V232_SUCCESSOR_EVALUATION_DATA_PASS":
        raise ValueError("v2.3.2 admission matrix did not pass")
    if preflight.status != "DTA_V232_RUNTIME_TOTALITY_PREFLIGHT_PASS":
        raise ValueError("v2.3.2 totality preflight did not pass")
    fixed_sources = tuple(
        sorted(
            (
                "scripts/analysis/build_dta_v232_admission_matrix.py",
                "scripts/analysis/build_dta_v232_fixed_set.py",
                "scripts/analysis/freeze_dta_v232_manifest.py",
                "scripts/analysis/run_dta_v232_evaluation.py",
                "scripts/analysis/run_dta_v232_provider_smoke.py",
                "scripts/analysis/run_dta_v232_runtime_totality_preflight.py",
                "src/ecomsre/dta_v2/v23/anomaly_interpretation_v232.py",
                "src/ecomsre/dta_v2/v23/conflict_model_v232.py",
                "src/ecomsre/dta_v2/v23/discovery_runtime_v232.py",
                "src/ecomsre/dta_v2/v23/evaluation_data_v232.py",
                "src/ecomsre/dta_v2/v23/evaluation_study_v232.py",
                "src/ecomsre/dta_v2/v23/evaluation_v232.py",
                "src/ecomsre/dta_v2/v23/novelty_gate_v232.py",
                "src/ecomsre/dta_v2/v23/runtime_preflight_v232.py",
            )
        )
    )
    predecessor_sources = tuple(
        sorted(
            (
                "src/ecomsre/dta_v2/v23/conflict_model_v231.py",
                "src/ecomsre/dta_v2/v23/discovery_provider.py",
                "src/ecomsre/dta_v2/v23/discovery_provider_v231.py",
                "src/ecomsre/dta_v2/v23/discovery_runtime_v231.py",
                "src/ecomsre/dta_v2/v23/discriminating_router_v231.py",
                "src/ecomsre/dta_v2/v23/evaluation_v231.py",
                "src/ecomsre/dta_v2/v23/novelty_gate_v231.py",
            )
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.evaluation-manifest.v1",
        "base_commit": "7fe2bff7186cca1cedd2513f7984709057fc19e5",
        "branch": "codex/dta-v232-anomaly-totality-successor",
        "provider_model": args.provider_model,
        "planned_case_count": 24,
        "planned_run_count": 48,
        "planned_execution_count": 1,
        "predecessor_evaluation_data": "BLOCKED_DTA_V231_EVALUATION_DATA",
        "predecessor_repository_acceptance": (
            "BLOCKED_DTA_V231_REPOSITORY_ACCEPTANCE"
        ),
        "study_relation": "INDEPENDENT_SUCCESSOR_NOT_RERUN",
        "registry_sha256": (
            DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.registry_sha256
        ),
        "history_ledger": _binding(
            root, "config/dta-v232/historical-results.v1.json"
        ),
        "history_verifier": _binding(
            root, "scripts/ci/verify_dta_v232_history.py"
        ),
        "cases": _binding(root, "config/dta-v232/evaluation/cases.json"),
        "truth_index": _binding(
            root, "config/dta-v232/evaluation/truth.json"
        ),
        "truth_shards": tuple(
            _binding(
                root,
                f"config/dta-v232/evaluation/truth/vx-{ordinal:03d}.json",
            )
            for ordinal in range(201, 225)
        ),
        "ontology_views": _binding(
            root, "config/dta-v232/evaluation/ontology-views.json"
        ),
        "strata": _binding(root, "config/dta-v232/evaluation/strata.json"),
        "admission_matrix": _binding(
            root, "config/dta-v232/evaluation/admission-matrix.json"
        ),
        "runtime_totality_preflight": _binding(
            root, "docs/analysis/dta-v232-runtime-totality-preflight.json"
        ),
        "fixed_surface_sources": tuple(
            _binding(root, path) for path in fixed_sources
        ),
        "frozen_predecessor_sources": tuple(
            _binding(root, path) for path in predecessor_sources
        ),
        "strict_system_prompt_sha256": hashlib.sha256(
            DISCOVERY_SYSTEM_PROMPT_V23.encode("utf-8")
        ).hexdigest(),
        "treatment_system_prompt_sha256": hashlib.sha256(
            DISCOVERY_SYSTEM_PROMPT_V231.encode("utf-8")
        ).hexdigest(),
        "provider_smoke_output": (
            "docs/analysis/dta-v232-provider-smoke.json"
        ),
        "output_json": (
            "docs/results/dta-v232-conflict-aware-evaluation.json"
        ),
        "output_markdown": (
            "docs/results/dta-v232-conflict-aware-evaluation.md"
        ),
        "independent_review": (
            "docs/external-reviews/dta-v232-pre-execution-review.md"
        ),
        "fixed_at_utc": datetime.now(timezone.utc),
    }
    manifest = EvaluationManifestV232.model_validate(payload)
    output = evaluation_root / "manifest.json"
    with output.open("x", encoding="utf-8") as handle:
        handle.write(manifest.model_dump_json(indent=2) + "\n")
    print("DTA_V232_EVALUATION_SURFACE_FROZEN")
    print(f"manifest_sha256={_sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
