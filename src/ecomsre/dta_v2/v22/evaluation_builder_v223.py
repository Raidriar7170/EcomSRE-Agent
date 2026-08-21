"""Deterministically derive and freeze the new v2.2.3 evaluation portfolio."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence, cast

from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V223,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    ShortestAdmissiblePathV222,
    audit_case_set_v222,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22


_PAIR_SLUG = {
    "cf-config": "pair-config",
    "cf-service": "pair-service",
    "cf-cpu": "pair-resource",
    "cf-dependency": "pair-dependency",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))


def _replace_services(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace_services(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace_services(item, mapping) for key, item in value.items()}
    return value


def _service_mapping(
    *,
    old_services: tuple[str, ...],
    old_pair_ids: tuple[str, ...],
    new_case_id: str,
) -> dict[str, str]:
    slug = (
        _PAIR_SLUG[old_pair_ids[0]]
        if old_pair_ids and old_pair_ids[0] in _PAIR_SLUG
        else f"case-{new_case_id}"
    )
    return {
        service: f"{slug}-{chr(ord('a') + index)}"
        for index, service in enumerate(old_services)
    }


def build_evaluation_set_v223(
    *,
    repository_root: Path,
    previous_case_set_path: Path,
    previous_truth_path: Path,
    output_root: Path,
    provider_model: str,
    implementation_commit: str,
) -> None:
    """Create all D7 files once, then fail closed on composition drift."""

    case_set_path = output_root / "cases.json"
    truth_path = output_root / "truth.json"
    utility_path = output_root / "utility-audit.json"
    manifest_path = output_root / "manifest.json"
    if any(path.exists() for path in (case_set_path, truth_path, utility_path, manifest_path)):
        raise FileExistsError("v2.2.3 evaluation freeze path already exists")

    previous_cases = load_practical_case_set_v22(previous_case_set_path)
    previous_truths = {
        item.case_id: item
        for item in load_practical_truth_set_v22(previous_truth_path).truths
    }
    cases: list[dict[str, object]] = []
    truths: list[dict[str, object]] = []
    source_entries: list[dict[str, str]] = []
    old_source_hashes = {
        cast(str, item.source_sha256) for item in previous_cases.cases
    }
    new_source_hashes: set[str] = set()
    for index, old_spec in enumerate(previous_cases.cases, 1):
        if old_spec.source_path is None:
            raise ValueError("v2.2.3 derivation requires frozen source bytes")
        old_path = repository_root / old_spec.source_path
        old_raw = cast(dict[str, Any], json.loads(old_path.read_bytes()))
        normalized = cast(dict[str, Any], old_raw["normalized_case"])
        old_services = tuple(cast(list[str], normalized["candidate_services"]))
        new_case_id = f"d{index:02d}"
        mapping = _service_mapping(
            old_services=old_services,
            old_pair_ids=old_spec.counterfactual_pair_ids,
            new_case_id=new_case_id,
        )
        new_raw = cast(dict[str, Any], _replace_services(old_raw, mapping))
        new_normalized = cast(dict[str, Any], new_raw["normalized_case"])
        new_normalized["case_id"] = new_case_id
        new_normalized["source_bytes_sha256"] = hashlib.sha256(
            old_path.read_bytes() + new_case_id.encode("utf-8")
        ).hexdigest()
        new_normalized["normalization_notes"] = [
            "Synthetic/derived DTA v2.2.3 evaluation fixture; no Docker capture.",
            f"Service identities deterministically rebound from prior development blueprint {old_spec.case_id}.",
        ]
        relative = f"config/dta-v22-3/evaluation/agent-visible/{new_case_id}.json"
        source_path = repository_root / relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_bytes = _json_bytes(new_raw)
        with source_path.open("xb") as handle:
            handle.write(source_bytes)
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        if source_sha in old_source_hashes:
            raise ValueError("v2.2.3 derived source repeats prior final bytes")
        new_source_hashes.add(source_sha)
        pair_ids = tuple(
            f"v223-{item}" for item in old_spec.counterfactual_pair_ids
        )
        cases.append(
            {
                "bootstrap_insufficient_expected": old_spec.bootstrap_insufficient_expected,
                "capture_kind": old_spec.capture_kind.value,
                "case_id": new_case_id,
                "counterfactual_pair_ids": pair_ids,
                "derivation": (
                    "Deterministic synthetic/derived v2.2.3 replay fixture from the "
                    f"truth-isolated {old_spec.case_id} development blueprint with rebound services."
                ),
                "modifier": old_spec.modifier.value,
                "source_path": relative,
                "source_sha256": source_sha,
            }
        )
        old_truth = previous_truths[old_spec.case_id]
        truths.append(
            {
                "case_id": new_case_id,
                "evidence_applicable": old_truth.evidence_applicable,
                "expected_mechanism": old_truth.expected_mechanism,
                "expected_root_service": (
                    None
                    if old_truth.expected_root_service is None
                    else mapping[old_truth.expected_root_service]
                ),
                "expected_terminal": old_truth.expected_terminal,
            }
        )
        source_entries.append({"path": relative, "sha256": source_sha})

    if len(new_source_hashes) != 16:
        raise ValueError("v2.2.3 derived sources are not byte-unique")
    _write_once(
        case_set_path,
        {"schema_version": "dta-v22.practical-case-set.v1", "cases": cases},
    )
    _write_once(
        truth_path,
        {"schema_version": "dta-v22.practical-truth-set.v1", "truths": truths},
    )
    utility = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_set_path,
        truth_path=truth_path,
    )
    _write_once(utility_path, utility.model_dump(mode="json"))

    expected_counts = {
        "CONFIGURATION_ERROR": 2,
        "SERVICE_UNAVAILABLE": 2,
        "CPU_SATURATION": 2,
        "MEMORY_LEAK": 2,
        "DEPENDENCY_LATENCY": 2,
        "NO_INCIDENT": 3,
        "ABSTAIN": 3,
    }
    observed: dict[str, int] = {key: 0 for key in expected_counts}
    for truth in truths:
        label = cast(str, truth["expected_terminal"])
        if label == "DIAGNOSED":
            label = cast(str, truth["expected_mechanism"])
        observed[label] += 1
    if observed != expected_counts:
        raise ValueError("v2.2.3 evaluation composition differs")
    feasible_incidents = sum(
        item.expected_terminal == "DIAGNOSED"
        and item.shortest_admissible_path
        in {ShortestAdmissiblePathV222.ONE, ShortestAdmissiblePathV222.TWO}
        for item in utility.cases
    )
    ambiguous_incidents = sum(
        item.expected_terminal == "DIAGNOSED"
        and any(action.support_clause_became_admissible for action in item.actions)
        and any(action.read_status == "SUCCESS_EMPTY" for action in item.actions)
        for item in utility.cases
    )
    frozen_pair_ids = {
        pair for item in cases for pair in cast(tuple[str, ...], item["counterfactual_pair_ids"])
    }
    if (
        feasible_incidents != 10
        or ambiguous_incidents < 4
        or len(frozen_pair_ids) != 4
    ):
        raise ValueError("v2.2.3 evaluation scenario properties differ")

    prior_path = repository_root / "config/dta-v22-3/development-predicate-yield-prior.json"
    development_path = repository_root / "docs/results/dta-v22-3-admission-dispatch-development.json"
    manifest: dict[str, object] = {
        "schema_version": "dta-v22.3.evaluation-manifest.v1",
        "base_commit": "bb85500fd4aa1777e2ac186f04b4b887c3a1023b",
        "implementation_commit": implementation_commit,
        "provider_model": provider_model,
        "prompt_sha256": hashlib.sha256(
            SHARED_SELECTION_SYSTEM_PROMPT_V223.encode("utf-8")
        ).hexdigest(),
        "case_set": {"path": str(case_set_path.relative_to(repository_root)), "sha256": _sha256(case_set_path)},
        "truth_set": {"path": str(truth_path.relative_to(repository_root)), "sha256": _sha256(truth_path)},
        "utility_audit": {"path": str(utility_path.relative_to(repository_root)), "sha256": _sha256(utility_path)},
        "predicate_yield_prior": {"path": str(prior_path.relative_to(repository_root)), "sha256": _sha256(prior_path)},
        "development_result": {"path": str(development_path.relative_to(repository_root)), "sha256": _sha256(development_path)},
        "agent_visible_sources": source_entries,
        "historical_results_manifest": {
            "path": "config/dta-v22-3/historical-results.v1.json",
            "sha256": _sha256(repository_root / "config/dta-v22-3/historical-results.v1.json"),
        },
        "implementation_sources": [
            {
                "path": path,
                "sha256": _sha256(repository_root / path),
            }
            for path in (
                "src/ecomsre/dta_v2/v22/gap_router_v223.py",
                "src/ecomsre/dta_v2/v22/no_incident_closure_v223.py",
                "src/ecomsre/dta_v2/v22/dispatch_policy_v223.py",
                "src/ecomsre/dta_v2/v22/admission_dispatch_campaign_v223.py",
                "src/ecomsre/dta_v2/v22/admission_dispatch_scorer_v223.py",
                "src/ecomsre/dta_v2/v22/selection_provider_v223.py",
            )
        ],
        "expected_cases": 16,
        "expected_runs": 64,
        "composition": expected_counts,
        "resource_silent_incidents": 4,
        "action_ambiguity_incidents": ambiguous_incidents,
        "incident_path_one_or_two": feasible_incidents,
        "counterfactual_pairs": len(frozen_pair_ids),
        "non_byte_identical_to_previous": 16,
        "combinations": ["MODEL_LEGACY", "MODEL_CLOSED", "AUTO_LEGACY", "AUTO_CLOSED"],
        "schedule_rule": "DETERMINISTIC_BALANCED_ROTATION_INTERLEAVED_BY_CASE",
        "truth_isolation_rule": "LOAD_ONLY_AFTER_ALL_FOUR_CASE_RUNS",
        "maximum_protocol_repairs_per_case": 2,
        "maximum_transport_retries_per_exact_request": 3,
        "minimum_request_interval_seconds": 0.5,
        "full_study_execution_count": 1,
        "execution_state": "NOT_STARTED",
        "single_execution_rule": "EXACTLY_ONE_FULL_STUDY_EXECUTION",
        "docker_calls": 0,
        "runbook_calls": 0,
        "agent_writes": 0,
    }
    _write_once(manifest_path, manifest)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build frozen DTA v2.2.3 evaluation")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--previous-case-set", type=Path, required=True)
    parser.add_argument("--previous-truth", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--provider-model", required=True)
    parser.add_argument("--implementation-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    build_evaluation_set_v223(
        repository_root=args.repository_root.resolve(),
        previous_case_set_path=args.previous_case_set.resolve(),
        previous_truth_path=args.previous_truth.resolve(),
        output_root=args.output_root.resolve(),
        provider_model=args.provider_model,
        implementation_commit=args.implementation_commit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("build_evaluation_set_v223", "main")
