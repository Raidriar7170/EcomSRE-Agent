#!/usr/bin/env python3
"""Freeze the DTA v2.3.1 fixed-study inputs and semantic runtime sources."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23
from ecomsre.dta_v2.v23.discovery_provider_v231 import DISCOVERY_SYSTEM_PROMPT_V231


BASE_COMMIT = "7fe2bff7186cca1cedd2513f7984709057fc19e5"
BRANCH = "codex/dta-v231-conflict-aware-discovery"


def _binding(root: Path, path: Path) -> dict[str, str]:
    relative = path.relative_to(root).as_posix()
    return {
        "path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _runtime_sources(root: Path) -> tuple[Path, ...]:
    sources = {
        *(root / "src/ecomsre/dta_v2/v22").glob("*.py"),
        *(root / "src/ecomsre/dta_v2/v23").glob("*.py"),
        root / "src/ecomsre/dta_v2/provider_env.py",
        *(root / "src/ecomsre/model").glob("*.py"),
    }
    return tuple(sorted(sources))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    args = parser.parse_args()

    root = args.repository_root.resolve()
    provider_values = load_private_provider_env(args.provider_env)
    config = root / "config/dta-v231/evaluation"
    runtime_sources = _runtime_sources(root)
    if len(runtime_sources) < 10 or not all(path.is_file() for path in runtime_sources):
        raise ValueError("v2.3.1 runtime dependency enumeration is incomplete")
    payload = {
        "schema_version": "dta-v231.evaluation-manifest.v1",
        "base_commit": BASE_COMMIT,
        "branch": BRANCH,
        "provider_model": provider_values["ECOMSRE_LLM_MODEL"],
        "planned_case_count": 24,
        "planned_run_count": 48,
        "planned_execution_count": 1,
        "cases": _binding(root, config / "cases.json"),
        "truth": _binding(root, config / "truth.json"),
        "ontology_views": _binding(root, config / "ontology-views.json"),
        "dataset_builder": _binding(
            root,
            root / "scripts/analysis/build_dta_v231_fixed_set.py",
        ),
        "runtime_sources": [
            _binding(root, path)
            for path in runtime_sources
        ],
        "strict_system_prompt_sha256": hashlib.sha256(
            DISCOVERY_SYSTEM_PROMPT_V23.encode("utf-8")
        ).hexdigest(),
        "treatment_system_prompt_sha256": hashlib.sha256(
            DISCOVERY_SYSTEM_PROMPT_V231.encode("utf-8")
        ).hexdigest(),
        "output_json": "docs/results/dta-v231-conflict-aware-evaluation.json",
        "output_markdown": "docs/results/dta-v231-conflict-aware-evaluation.md",
        "fixed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (config / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
