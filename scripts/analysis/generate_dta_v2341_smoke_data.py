#!/usr/bin/env python3
"""Generate and freeze the fresh DTA v2.3.4.1 smoke-only surface."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.provider_smoke_v2341 import (
    RegistrationSmokeManifestFileV2341,
    RegistrationSmokeManifestV2341,
    build_smoke_data_v2341,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341,
    RegistrationAliasSelectionV2341,
)


FROZEN_PATHS = (
    "config/dta-v2341/smoke/tasks.json",
    "config/dta-v2341/smoke/truth.json",
    "docs/analysis/dta-v2341-catalog-feasibility.json",
    "scripts/analysis/generate_dta_v2341_smoke_data.py",
    "scripts/analysis/run_dta_v2341_catalog_feasibility.py",
    "scripts/analysis/run_dta_v2341_provider_smoke.py",
    "src/ecomsre/dta_v2/v23/cli.py",
    "src/ecomsre/dta_v2/v23/provider_smoke_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_alias_provider_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_assembler_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_catalog_v2341.py",
    "src/ecomsre/dta_v2/v23/registration_compiler_v234.py",
    "src/ecomsre/dta_v2/v23/registration_contracts_v234.py",
    "src/ecomsre/dta_v2/v23/registration_validator_v234.py",
    "tests/dta_v2341/test_provider_smoke_v2341.py",
    "tests/dta_v2341/test_registration_alias_protocol_v2341.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bound(path: Path, rendered: str) -> None:
    if path.exists():
        if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
            raise ValueError(f"v2.3.4.1 frozen smoke artifact differs: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(rendered)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    output_root = root / "config/dta-v2341/smoke"
    tasks, truths = build_smoke_data_v2341(repository_root=root)
    _write_bound(output_root / "tasks.json", tasks.model_dump_json(indent=2) + "\n")
    _write_bound(output_root / "truth.json", truths.model_dump_json(indent=2) + "\n")

    values = load_private_provider_env(args.provider_env)
    bindings = []
    for relative in FROZEN_PATHS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"v2.3.4.1 smoke freeze input is absent: {relative}")
        bindings.append(
            RegistrationSmokeManifestFileV2341(
                path=relative,
                sha256=_sha256(path),
            )
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.registration-smoke-manifest.v1",
        "predecessor_head": "edb313655c4be64295012c383cfa19ed48ccb894",
        "branch": "codex/dta-v2341-registration-alias-protocol",
        "provider_model": values["ECOMSRE_LLM_MODEL"],
        "planned_task_count": 8,
        "planned_provider_called_task_count": 6,
        "planned_execution_count": 1,
        "current_execution_count": 0,
        "fixed_evaluation_execution_count": 0,
        "frozen_files": tuple(sorted(bindings, key=lambda item: item.path)),
        "provider_prompt_sha256": hashlib.sha256(
            REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341.encode("utf-8")
        ).hexdigest(),
        "alias_response_schema_sha256": semantic_sha256_v22(
            RegistrationAliasSelectionV2341.model_json_schema()
        ),
        "output_path": "docs/analysis/dta-v2341-provider-smoke.json",
        "terminal": "DTA_V2341_SMOKE_SURFACE_FROZEN",
    }
    draft = RegistrationSmokeManifestV2341.model_construct(
        **payload, manifest_sha256="0" * 64
    )
    manifest = RegistrationSmokeManifestV2341.model_validate(
        {
            **payload,
            "manifest_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"manifest_sha256"})
            ),
        }
    )
    _write_bound(
        output_root / "manifest.json",
        manifest.model_dump_json(indent=2) + "\n",
    )
    print("DTA_V2341_SMOKE_SURFACE_FROZEN")
    print(f"task_set_sha256={tasks.task_set_sha256}")
    print(f"manifest_sha256={manifest.manifest_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
