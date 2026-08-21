"""Fail-closed verification for the fixed DTA v2.2.4 evaluation inputs."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from ecomsre.dta_v2.v22.contrastive_actions_v224 import (
    contrastive_resource_action_if_eligible_v224,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    EvidenceUtilityAuditReportV222,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.replay_target_coverage_v224 import (
    ReplayTargetCoverageModeV224,
    load_replay_target_coverage_set_v224,
    require_capture_matches_target_coverage_v224,
)


ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "config/dta-v22-4/evaluation"


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


def verify_fixed_evaluation_v224(*, repository_root: Path = ROOT) -> dict[str, object]:
    evaluation = repository_root / "config/dta-v22-4/evaluation"
    cases = load_practical_case_set_v22(evaluation / "cases.json")
    truths = load_practical_truth_set_v22(evaluation / "truth.json")
    coverage = load_replay_target_coverage_set_v224(evaluation / "coverage.json")
    utility = EvidenceUtilityAuditReportV222.model_validate_json(
        (evaluation / "utility-audit.json").read_bytes()
    )
    expected_ids = tuple(f"e{index:02d}" for index in range(1, 17))
    if tuple(item.case_id for item in cases.cases) != expected_ids:
        raise ValueError("v2.2.4 evaluation IDs differ")
    if tuple(item.case_id for item in truths.truths) != expected_ids:
        raise ValueError("v2.2.4 truth IDs differ")
    truth_by_id = {item.case_id: item for item in truths.truths}
    composition = Counter(
        item.expected_terminal
        if item.expected_terminal != "DIAGNOSED"
        else cast(str, item.expected_mechanism)
        for item in truths.truths
    )
    expected_composition = {
        "CPU_SATURATION": 4,
        "MEMORY_LEAK": 4,
        "NO_INCIDENT": 2,
        "ABSTAIN": 2,
        "CONFIGURATION_ERROR": 2,
        "SERVICE_UNAVAILABLE": 1,
        "DEPENDENCY_LATENCY": 1,
    }
    if dict(composition) != expected_composition:
        raise ValueError("v2.2.4 evaluation composition differs")

    resource_ids = set(expected_ids[:10])
    pair_roots: dict[str, list[int]] = defaultdict(list)
    first_normal_paths = 0
    for spec in cases.cases:
        case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
        source = coverage.require(spec.case_id).require(EvidenceSourceV22.RESOURCES)
        require_capture_matches_target_coverage_v224(coverage=source, capture=case.capture)
        if spec.case_id not in resource_ids:
            continue
        if len(case.candidate_services) != 2 or len(case.capture.resources) != 2:
            raise ValueError("v2.2.4 resource case does not contain two complete targets")
        if source.coverage_mode is not ReplayTargetCoverageModeV224.TARGET_COMPLETE:
            raise ValueError("v2.2.4 resource case is not TARGET_COMPLETE")
        if not _target_symmetric(case.capture.metrics) or not _target_symmetric(
            case.capture.runtime
        ):
            raise ValueError("v2.2.4 resource bootstrap is target-asymmetric")
        bundle = contrastive_resource_action_if_eligible_v224(
            coverage=source,
            resources_enabled=True,
            unresolved_resource_hypotheses=4,
            remaining_budget=3.0,
            bundle_mode=True,
        )
        if bundle is None or bundle.target_services != case.candidate_services:
            raise ValueError("v2.2.4 resource case lacks a one-read bundle path")
        truth = truth_by_id[spec.case_id]
        if truth.expected_root_service is None:
            first_normal_paths += 1
        else:
            root_index = case.candidate_services.index(truth.expected_root_service)
            if root_index == 1:
                first_normal_paths += 1
            for pair_id in spec.counterfactual_pair_ids:
                if pair_id.startswith("v224-cf-"):
                    pair_roots[pair_id].append(root_index)
    if len(pair_roots) != 4 or any(sorted(values) != [0, 1] for values in pair_roots.values()):
        raise ValueError("v2.2.4 counterfactual resource pairs do not alternate targets")
    if first_normal_paths < 6:
        raise ValueError("v2.2.4 evaluation lacks two-read individual paths")
    if utility.infeasible_incident_cases != 0:
        raise ValueError("v2.2.4 evaluation contains infeasible incidents")

    old_cases = load_practical_case_set_v22(
        repository_root / "config/dta-v22-3/evaluation/cases.json"
    )
    old_hashes = {
        _sha256(repository_root / cast(str, item.source_path))
        for item in old_cases.cases
    }
    new_hashes = {
        _sha256(repository_root / cast(str, item.source_path)) for item in cases.cases
    }
    nonidentical = sum(item not in old_hashes for item in new_hashes)
    if nonidentical < 12:
        raise ValueError("v2.2.4 evaluation reuses too many v2.2.3 source bytes")
    return {
        "status": "DTA_V22_4_FIXED_EVALUATION_VERIFIED",
        "cases": len(cases.cases),
        "resource_cases": len(resource_ids),
        "counterfactual_resource_pairs": len(pair_roots),
        "non_byte_identical_to_v223": nonidentical,
        "infeasible_incident_cases": utility.infeasible_incident_cases,
        "first_normal_two_read_paths": first_normal_paths,
        "agent_writes": 0,
    }


def main() -> int:
    print(json.dumps(verify_fixed_evaluation_v224(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_fixed_evaluation_v224",)
