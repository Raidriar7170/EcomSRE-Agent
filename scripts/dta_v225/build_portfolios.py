"""Deterministically build DTA v2.2.5 opaque development/evaluation bytes."""

from __future__ import annotations

import json
from pathlib import Path

from ecomsre.dta_v2.v22.evaluation_builder_v225 import (
    build_fixed_evaluation_portfolio_v225,
    build_normalized_development_portfolio_v225,
)
from ecomsre.dta_v2.v22.opaque_identity_v225 import (
    generate_opaque_identity_plan_v225,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    plan = generate_opaque_identity_plan_v225(
        service_count=64,
        operation_count=64,
        change_count=32,
        pair_count=32,
    )
    blueprint_root = ROOT / "config/dta-v22-3/evaluation"
    build_normalized_development_portfolio_v225(
        repository_root=ROOT,
        previous_case_set_path=blueprint_root / "cases.json",
        previous_truth_path=blueprint_root / "truth.json",
        output_root=ROOT / "config/dta-v22-5/development",
        identity_plan=plan,
    )
    build_fixed_evaluation_portfolio_v225(
        repository_root=ROOT,
        blueprint_case_set_path=blueprint_root / "cases.json",
        blueprint_truth_path=blueprint_root / "truth.json",
        output_root=ROOT / "config/dta-v22-5/evaluation",
        identity_plan=plan,
    )
    _write_once(
        ROOT / "config/dta-v22-5/evaluation/opaque-identity-plan.json",
        plan.model_dump(mode="json"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
