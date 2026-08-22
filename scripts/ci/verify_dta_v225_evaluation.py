"""Fail-closed verification for DTA v2.2.5 opaque fixed portfolios."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.evaluation_strata_v225 import EvaluatorStrataV225
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    EvidenceUtilityAuditReportV222,
)
from ecomsre.dta_v2.v22.opaque_identity_v225 import OpaqueIdentityPlanV225
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    lint_static_identity_surface_v225,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    ReplayTargetCoverageModeV225,
    load_replay_target_coverage_set_v225,
    require_capture_matches_target_coverage_v225,
)


ROOT = Path(__file__).resolve().parents[2]
INVALID_V224_SOURCE_HASHES = {
    "026e8bfc31feaadd6d6ba723548aa771b01bf840a9a5b936526317df9dea69b4",
    "04ef214975a4f20f26ed7e20758535fccb2a7305767ed1cabe61d38e632862d2",
    "08cd89d0118d8da4d3d1d511ff3af3c09f93a9100313109a57de4b3c7b37e7b6",
    "1ef160ce7370bfed2820b7f26170da4d6b74d023a3dc67613a245a3b78e0f48c",
    "380a5afc42b5b8097177271084cbd83068ceb9b9a065b7aa6722e553941a6157",
    "5f9a10a2750da28e0c6fc6c81ee6ca04c883f29f27b84ba40246e552ad6174d5",
    "667836a01d936acb66bb5ab1909ec7a8c05167f0cee1470771e0d4da53d829c4",
    "72d7918e140e01b4a27904272fa00e280fd166727e884f0054ab04a5f74b2dde",
    "79c17948f60faffc33e100daa29677850ae391214e4d1221720e268c85447ce0",
    "7a302a9abb9d776201244dc65601173e9f02a1192cf0b285603f5297f21ff543",
    "92492ad5088f010873cfbd68d2f88ef8e8df0d5887c1e699d880977854543986",
    "a0f90e6cad0b9b46c804cd87470b2ceab270185f23d3c1694116af1f377fa3e1",
    "a78a5fc4353cb4d6a5fa217ecd682131c60d31449654cc11a2ac1398167de34c",
    "ae335d919420ce7e43d91b38c19ecb53d33b42c424caf92bcccde8c6677e6324",
    "c751592b2e49abe62288ad9a07df532d7d07c56035b0999358ae64d0f208792b",
    "f27de249c967eef906972c8805b9b8a620eedf23f59d8c138b83b39f29c12af6",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _target_symmetric(values: tuple[object, ...]) -> bool:
    normalized: dict[str, list[str]] = defaultdict(list)
    for value in values:
        payload = cast(dict[str, Any], value.model_dump(mode="json"))  # type: ignore[attr-defined]
        service = cast(str, payload["service"])
        payload.pop("service", None)
        normalized[service].append(json.dumps(payload, sort_keys=True))
    return len({tuple(sorted(items)) for items in normalized.values()}) == 1


def verify_fixed_evaluation_v225(
    *, repository_root: Path = ROOT, phase: str = "evaluation"
) -> dict[str, object]:
    if phase not in {"development", "evaluation"}:
        raise ValueError("v2.2.5 portfolio phase differs")
    root = repository_root / f"config/dta-v22-5/{phase}"
    cases = load_practical_case_set_v22(root / "cases.json")
    truths = load_practical_truth_set_v22(root / "truth.json")
    coverage = load_replay_target_coverage_set_v225(root / "coverage.json")
    utility = EvidenceUtilityAuditReportV222.model_validate_json(
        (root / "utility-audit.json").read_bytes()
    )
    strata = EvaluatorStrataV225.model_validate_json((root / "strata.json").read_bytes())
    prefix = "d" if phase == "development" else "e"
    expected_ids = tuple(f"{prefix}{index:02d}" for index in range(1, 17))
    if tuple(item.case_id for item in cases.cases) != expected_ids:
        raise ValueError("v2.2.5 case IDs differ")
    if tuple(item.case_id for item in truths.truths) != expected_ids:
        raise ValueError("v2.2.5 truth IDs differ")
    if strata.all_case_ids != expected_ids:
        raise ValueError("v2.2.5 evaluator strata case IDs differ")
    truth_by_id = {item.case_id: item for item in truths.truths}
    composition = Counter(
        item.expected_terminal
        if item.expected_terminal != "DIAGNOSED"
        else cast(str, item.expected_mechanism)
        for item in truths.truths
    )
    if dict(composition) != {
        "CPU_SATURATION": 4,
        "MEMORY_LEAK": 4,
        "NO_INCIDENT": 2,
        "ABSTAIN": 2,
        "CONFIGURATION_ERROR": 2,
        "SERVICE_UNAVAILABLE": 1,
        "DEPENDENCY_LATENCY": 1,
    }:
        raise ValueError("v2.2.5 portfolio composition differs")

    resource_ids = set(strata.resource_case_ids)
    pair_roots: dict[str, list[int]] = defaultdict(list)
    first_normal_paths = 0
    source_hashes: set[str] = set()
    identity_values: set[str] = set()
    for spec in cases.cases:
        source_path = repository_root / cast(str, spec.source_path)
        raw = json.loads(source_path.read_bytes())
        lint = lint_static_identity_surface_v225(raw, surface_class=spec.case_id)
        if lint.forbidden_identity_values:
            raise ValueError("v2.2.5 static opaque identity lint failed")
        identity_values.update(lint.identity_values_scanned)
        source_hash = _sha256(source_path)
        if source_hash != spec.source_sha256:
            raise ValueError("v2.2.5 agent-visible source digest differs")
        source_hashes.add(source_hash)
        case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
        source = coverage.require(spec.case_id).require(EvidenceSourceV22.RESOURCES)
        require_capture_matches_target_coverage_v225(coverage=source, capture=case.capture)
        if spec.case_id not in resource_ids:
            continue
        if len(case.candidate_services) != 2 or len(case.capture.resources) != 2:
            raise ValueError("v2.2.5 resource case lacks two complete targets")
        if source.coverage_mode is not ReplayTargetCoverageModeV225.TARGET_COMPLETE:
            raise ValueError("v2.2.5 resource case is not TARGET_COMPLETE")
        if not _target_symmetric(case.capture.metrics) or not _target_symmetric(
            case.capture.runtime
        ):
            raise ValueError("v2.2.5 resource bootstrap is target-asymmetric")
        bundle = contrastive_resource_action_if_eligible_v225(
            coverage=source,
            resources_enabled=True,
            unresolved_resource_hypotheses=4,
            remaining_budget=3.0,
            bundle_mode=True,
        )
        if bundle is None or bundle.target_services != case.candidate_services:
            raise ValueError("v2.2.5 resource case lacks a one-read bundle path")
        truth = truth_by_id[spec.case_id]
        if truth.expected_root_service is None:
            first_normal_paths += 1
        else:
            root_index = case.candidate_services.index(truth.expected_root_service)
            if root_index == 1:
                first_normal_paths += 1
            for pair_id in spec.counterfactual_pair_ids:
                pair_roots[pair_id].append(root_index)
    if len(pair_roots) != 4 or any(
        sorted(values) != [0, 1] for values in pair_roots.values()
    ):
        raise ValueError("v2.2.5 counterfactual roots do not swap over opaque pairs")
    if first_normal_paths < 6:
        raise ValueError("v2.2.5 portfolio lacks two-read individual paths")
    if utility.infeasible_incident_cases != 0:
        raise ValueError("v2.2.5 portfolio contains infeasible incidents")
    if phase == "evaluation" and not source_hashes.isdisjoint(
        INVALID_V224_SOURCE_HASHES
    ):
        raise ValueError("v2.2.5 evaluation reuses INVALID v2.2.4 source bytes")
    if len(source_hashes) != 16:
        raise ValueError("v2.2.5 agent-visible source bytes are not unique")
    if phase == "evaluation":
        OpaqueIdentityPlanV225.model_validate_json(
            (root / "opaque-identity-plan.json").read_bytes()
        )
    return {
        "status": "DTA_V22_5_OPAQUE_FIXED_PORTFOLIO_VERIFIED",
        "phase": phase,
        "cases": len(cases.cases),
        "resource_cases": len(resource_ids),
        "counterfactual_resource_pairs": len(pair_roots),
        "new_bytes_vs_invalid_v224": len(source_hashes),
        "identity_values_scanned": len(identity_values),
        "forbidden_identity_values": 0,
        "infeasible_incident_cases": utility.infeasible_incident_cases,
        "first_normal_two_read_paths": first_normal_paths,
        "agent_writes": 0,
    }


def main() -> int:
    print(json.dumps(verify_fixed_evaluation_v225(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_fixed_evaluation_v225",)
