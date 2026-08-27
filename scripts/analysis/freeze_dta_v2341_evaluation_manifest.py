#!/usr/bin/env python3
"""Freeze the DTA v2.3.4.1 data, Runtime, Provider, and scorer surface."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, cast

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.evaluation_study_v2341 import (
    EvaluationArmV2341,
    EvaluationManifestV2341,
    ManifestFileBindingV2341,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341,
    RegistrationAliasSelectionV2341,
)


FROZEN_PATHS = (
    "config/dta-v2341/evaluation/core-schema-snapshot.json",
    "config/dta-v2341/evaluation/tasks.json",
    "config/dta-v2341/evaluation/truth.json",
    "docs/analysis/dta-v2341-evaluation-data-admission.json",
    "docs/analysis/dta-v2341-provider-smoke-pass-freeze.json",
    "docs/analysis/dta-v2341-provider-smoke.json",
    "docs/analysis/dta-v2341-runtime-preflight.json",
    "scripts/analysis/freeze_dta_v2341_evaluation_manifest.py",
    "scripts/analysis/generate_dta_v2341_evaluation_data.py",
    "scripts/analysis/run_dta_v2341_evaluation.py",
    "scripts/analysis/run_dta_v2341_evaluation_data_admission.py",
    "scripts/analysis/run_dta_v2341_runtime_preflight.py",
    "src/ecomsre/dta_v2/v23/cli.py",
    "src/ecomsre/dta_v2/v23/evaluation_data_v2341.py",
    "src/ecomsre/dta_v2/v23/evaluation_study_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_alias_provider_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_assembler_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_catalog_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_compiler_v234.py",
    "src/ecomsre/dta_v2/v23/registration_contracts_v234.py",
    "src/ecomsre/dta_v2/v23/registration_validator_v234.py",
    "tests/dta_v2341/test_evaluation_study_v2341.py",
    "tests/dta_v2341/test_provider_smoke_v2341.py",
    "tests/dta_v2341/test_registration_alias_protocol_v2341.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument(
        "--freeze",
        required=True,
        choices=("DTA_V2341_RUNTIME_PREFLIGHT_PASS",),
    )
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output = root / "config/dta-v2341/evaluation/manifest.json"
    if output.exists():
        raise FileExistsError("v2.3.4.1 evaluation surface was already frozen")
    preflight = root / "docs/analysis/dta-v2341-runtime-preflight.json"
    if args.freeze not in preflight.read_text(encoding="utf-8"):
        raise ValueError("BLOCKED_DTA_V2341_RUNTIME_PREFLIGHT")
    values = load_private_provider_env(args.provider_env)
    bindings: list[ManifestFileBindingV2341] = []
    for relative in FROZEN_PATHS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(
                f"v2.3.4.1 freeze input is absent: {relative}"
            )
        bindings.append(
            ManifestFileBindingV2341(path=relative, sha256=_sha256(path))
        )
    fixed_at = datetime.now(timezone.utc)
    ordered_bindings = tuple(sorted(bindings, key=lambda item: item.path))
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.evaluation-manifest.v1",
        "predecessor_head": "edb313655c4be64295012c383cfa19ed48ccb894",
        "branch": "codex/dta-v2341-registration-alias-protocol",
        "provider_model": values["ECOMSRE_LLM_MODEL"],
        "planned_task_count": 16,
        "planned_run_count": 32,
        "planned_execution_count": 1,
        "current_execution_count": 0,
        "arms": tuple(EvaluationArmV2341),
        "frozen_files": ordered_bindings,
        "provider_prompt_sha256": hashlib.sha256(
            REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341.encode("utf-8")
        ).hexdigest(),
        "alias_response_schema_sha256": semantic_sha256_v22(
            RegistrationAliasSelectionV2341.model_json_schema()
        ),
        "provider_smoke_output": (
            "docs/analysis/dta-v2341-provider-smoke.json"
        ),
        "output_json": (
            "docs/results/dta-v2341-registration-assistance-evaluation.json"
        ),
        "output_markdown": (
            "docs/results/dta-v2341-registration-assistance-evaluation.md"
        ),
        "independent_review": (
            "docs/external-reviews/dta-v2341-pre-execution-review.md"
        ),
        "fixed_at_utc": fixed_at,
    }
    prototype = EvaluationManifestV2341.model_construct(
        **payload,
        manifest_sha256="0" * 64,
    )
    manifest_sha256 = semantic_sha256_v22(
        prototype.model_dump(mode="json", exclude={"manifest_sha256"})
    )
    manifest = cast(
        EvaluationManifestV2341,
        EvaluationManifestV2341.model_validate(
            {
                **payload,
                "manifest_sha256": manifest_sha256,
            }
        ),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(manifest.model_dump_json(indent=2) + "\n")
    print("DTA_V2341_EVALUATION_SURFACE_FROZEN")
    print(f"manifest_sha256={_sha256(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
