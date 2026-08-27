#!/usr/bin/env python3
"""Freeze the DTA v2.3.4 Provider, data, scorer, and gate surface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation_v234 import (
    EvaluationArmV234,
    EvaluationManifestV234,
    ManifestFileBindingV234,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    REGISTRATION_DRAFT_SYSTEM_PROMPT_V234,
)


FROZEN_PATHS = (
    "config/dta-v234/evaluation/core-schema-snapshot.json",
    "config/dta-v234/evaluation/tasks.json",
    "config/dta-v234/evaluation/truth.json",
    "config/dta-v234/historical-results.v1.json",
    "docs/analysis/dta-v234-core-ontology-snapshot.json",
    "docs/analysis/dta-v234-registration-audit.json",
    "docs/analysis/dta-v234-runtime-preflight.json",
    "scripts/analysis/generate_dta_v234_evaluation_data.py",
    "scripts/analysis/freeze_dta_v234_manifest.py",
    "scripts/analysis/run_dta_v234_evaluation.py",
    "scripts/analysis/run_dta_v234_evaluation_data_audit.py",
    "scripts/analysis/run_dta_v234_provider_smoke.py",
    "scripts/analysis/run_dta_v234_runtime_preflight.py",
    "scripts/ci/verify_dta_v234_history.py",
    "src/ecomsre/dta_v2/v23/core_ontology_snapshot_v234.py",
    "src/ecomsre/dta_v2/v23/evaluation_v234.py",
    "src/ecomsre/dta_v2/v23/ontology_expansion_v234.py",
    "src/ecomsre/dta_v2/v23/registration_compiler_v234.py",
    "src/ecomsre/dta_v2/v23/registration_contracts_v234.py",
    "src/ecomsre/dta_v2/v23/registration_provider_v234.py",
    "src/ecomsre/dta_v2/v23/registration_validator_v234.py",
    "tests/dta_v23/test_increment4_evaluation_v234.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-model", required=True)
    parser.add_argument(
        "--freeze-once",
        required=True,
        choices=("DTA_V234_RUNTIME_PREFLIGHT_PASS",),
    )
    parser.add_argument("--repair-ordinal", type=int, choices=(1, 2), default=0)
    parser.add_argument(
        "--repair-code",
        choices=(
            "V234_PROTOCOL_FEEDBACK_AND_MODE_BINDING",
            "V234_SMOKE_RESUME_ISOLATION",
        ),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = root / "config/dta-v234/evaluation/manifest.json"
    additional_paths: list[str] = []
    if args.repair_ordinal:
        if args.repair_code is None or not output.is_file():
            raise ValueError("v2.3.4 smoke repair requires a prior manifest and fix code")
        local_root = root / ".local/dta-v234"
        sentinel = local_root / "provider-smoke.started.json"
        partial = local_root / (
            "provider-smoke.partial.jsonl"
            if args.repair_ordinal == 1
            else f"provider-smoke-resume-{args.repair_ordinal - 1}.partial.jsonl"
        )
        if not sentinel.is_file() or not partial.is_file():
            raise FileNotFoundError("v2.3.4 failed smoke evidence is absent")
        old_manifest = output.read_bytes()
        old_manifest_sha = hashlib.sha256(old_manifest).hexdigest()
        superseded_relative = (
            f"docs/analysis/dta-v234-manifest-provider-smoke-fix{args.repair_ordinal}-superseded.json"
        )
        superseded = root / superseded_relative
        with superseded.open("xb") as handle:
            handle.write(old_manifest)
        failed_runs = [
            json.loads(line)
            for line in partial.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        diagnostic_payload = {
            "schema_version": "dta-v234.provider-smoke-repair-diagnostic.v1",
            "execution_count": 1,
            "repair_ordinal": args.repair_ordinal,
            "prior_manifest_sha256": old_manifest_sha,
            "prior_sentinel_sha256": _sha256(sentinel),
            "failed_partial_sha256": _sha256(partial),
            "failed_roles": [
                {
                    "task_id": item["task_id"],
                    "role": item["role"],
                    "provider_error_code": item["provider_error_code"],
                }
                for item in failed_runs
                if item.get("provider_error_code") is not None
            ],
            "raw_provider_artifacts_scope": ".local/dta-v234/provider-raw/smoke",
            "fixed_evaluation_execution_count": 0,
        }
        diagnostic_relative = (
            f"docs/analysis/dta-v234-provider-smoke-fix{args.repair_ordinal}-diagnostic.json"
        )
        diagnostic = root / diagnostic_relative
        with diagnostic.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(diagnostic_payload, sort_keys=True, indent=2) + "\n")
        record_payload = {
            "schema_version": "dta-v234.provider-smoke-repair.v1",
            "execution_count": 1,
            "repair_ordinal": args.repair_ordinal,
            "prior_manifest_sha256": old_manifest_sha,
            "prior_sentinel_sha256": _sha256(sentinel),
            "failed_partial_sha256": _sha256(partial),
            "superseded_manifest_sha256": _sha256(superseded),
            "diagnostic_sha256": _sha256(diagnostic),
            "fix_code": args.repair_code,
            "fix_files": (
                [
                    "scripts/analysis/freeze_dta_v234_manifest.py",
                    "scripts/analysis/run_dta_v234_provider_smoke.py",
                    "src/ecomsre/dta_v2/v23/evaluation_v234.py",
                    "src/ecomsre/dta_v2/v23/registration_provider_v234.py",
                ]
                if args.repair_ordinal == 1
                else [
                    "scripts/analysis/freeze_dta_v234_manifest.py",
                    "src/ecomsre/dta_v2/v23/evaluation_v234.py",
                ]
            ),
            "fixed_evaluation_execution_count": 0,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        record_relative = (
            f"docs/analysis/dta-v234-provider-smoke-repair-{args.repair_ordinal}.json"
        )
        record = root / record_relative
        with record.open("x", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        **record_payload,
                        "record_sha256": semantic_sha256_v22(record_payload),
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
        additional_paths.extend(
            (superseded_relative, diagnostic_relative, record_relative)
        )
        if args.repair_ordinal == 2:
            additional_paths.extend(
                (
                    "docs/analysis/dta-v234-manifest-provider-smoke-fix1-superseded.json",
                    "docs/analysis/dta-v234-provider-smoke-fix1-diagnostic.json",
                    "docs/analysis/dta-v234-provider-smoke-repair-1.json",
                )
            )
    elif output.exists():
        raise FileExistsError("v2.3.4 evaluation surface was already frozen")
    bindings = []
    for relative in (*FROZEN_PATHS, *additional_paths):
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"v2.3.4 freeze input is absent: {relative}")
        bindings.append(ManifestFileBindingV234(path=relative, sha256=_sha256(path)))
    manifest = EvaluationManifestV234(
        schema_version="dta-v234.evaluation-manifest.v1",
        base_commit="da423b9104ac532f0bf323f314d37b527671c679",
        branch="codex/dta-v234-human-ontology-expansion",
        provider_model=args.provider_model,
        planned_task_count=16,
        planned_run_count=32,
        planned_execution_count=1,
        arms=(
            EvaluationArmV234.V23_TEMPLATE_REGISTRATION_SEED,
            EvaluationArmV234.V234_LLM_FORMAL_REGISTRATION,
        ),
        frozen_files=tuple(sorted(bindings, key=lambda item: item.path)),
        provider_system_prompt_sha256=hashlib.sha256(
            REGISTRATION_DRAFT_SYSTEM_PROMPT_V234.encode("utf-8")
        ).hexdigest(),
        provider_smoke_output="docs/analysis/dta-v234-provider-smoke.json",
        output_json="docs/results/dta-v234-registration-assistance-evaluation.json",
        output_markdown="docs/results/dta-v234-registration-assistance-evaluation.md",
        independent_review="docs/external-reviews/dta-v234-pre-execution-review.md",
        fixed_at_utc=datetime.now(timezone.utc),
    )
    rendered = manifest.model_dump_json(indent=2) + "\n"
    if args.repair_ordinal:
        output.write_text(rendered, encoding="utf-8")
    else:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
    print("DTA_V234_EVALUATION_SURFACE_FROZEN")
    print(f"manifest_sha256={_sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
