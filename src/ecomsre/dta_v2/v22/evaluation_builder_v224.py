"""Build normalized development and fixed evaluation portfolios for DTA v2.2.4."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import audit_case_set_v222
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.read_contracts import (
    ResourceSampleV22,
    ResourceUsageRecordV22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22
from ecomsre.dta_v2.v22.replay_target_coverage_v224 import (
    ReplayCaseTargetCoverageV224,
    ReplayTargetCoverageSetV224,
    build_replay_target_coverage_v224,
    complete_resource_records_v224,
    normal_resource_record_v224,
)


_RESOURCE_DEVELOPMENT_CASES = {"d05", "d06", "d07", "d08", "d13"}
_RESOURCE_EVALUATION_CASES = {f"e{index:02d}" for index in range(1, 11)}


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))


def normalize_development_source_v224(
    *, source_bytes: bytes, require_resource_complete: bool
) -> tuple[dict[str, object], ReplayCaseTargetCoverageV224]:
    raw = cast(dict[str, Any], json.loads(source_bytes))
    normalized = cast(dict[str, Any], raw["normalized_case"])
    case_id = cast(str, normalized["case_id"])
    candidates = tuple(cast(list[str], normalized["candidate_services"]))
    capture = ReplayCaptureV22.model_validate_json(
        json.dumps(normalized["capture"])
    )
    if require_resource_complete:
        resources = complete_resource_records_v224(
            candidate_services=candidates,
            records=capture.resources,
        )
        capture = ReplayCaptureV22(
            **{
                **capture.model_dump(mode="python"),
                "resources": resources,
            }
        )
    normalized["capture"] = capture.model_dump(mode="json")
    normalized["source_bytes_sha256"] = hashlib.sha256(
        source_bytes + b"|dta-v22.4-development"
    ).hexdigest()
    normalized["normalization_notes"] = [
        *cast(list[str], normalized["normalization_notes"]),
        "Synthetic/derived DTA v2.2.4 development copy; no Docker capture.",
        "Healthy candidate Resources targets use explicit normal records.",
    ]
    captured_sources = {
        EvidenceSourceV22(item) for item in cast(list[str], raw["captured_sources"])
    }
    resource_targets = tuple(sorted(item.service for item in capture.resources))
    source_coverages = tuple(
        build_replay_target_coverage_v224(
            source=source,
            candidate_services=candidates,
            covered_target_services=(
                resource_targets
                if source is EvidenceSourceV22.RESOURCES
                else candidates
                if source in captured_sources
                else ()
            ),
        )
        for source in EvidenceSourceV22
    )
    return raw, ReplayCaseTargetCoverageV224(
        case_id=case_id,
        sources=source_coverages,
    )


def build_normalized_development_portfolio_v224(
    *,
    repository_root: Path,
    previous_case_set_path: Path,
    previous_truth_path: Path,
    output_root: Path,
) -> None:
    case_path = output_root / "cases.json"
    truth_path = output_root / "truth.json"
    coverage_path = output_root / "coverage.json"
    utility_path = output_root / "utility-audit.json"
    if any(path.exists() for path in (case_path, truth_path, coverage_path, utility_path)):
        raise FileExistsError("v2.2.4 development portfolio already exists")
    previous = load_practical_case_set_v22(previous_case_set_path)
    truths = load_practical_truth_set_v22(previous_truth_path)
    cases: list[dict[str, object]] = []
    coverages: list[ReplayCaseTargetCoverageV224] = []
    for spec in previous.cases:
        if spec.source_path is None:
            raise ValueError("v2.2.4 development normalization requires source bytes")
        source_bytes = (repository_root / spec.source_path).read_bytes()
        normalized, coverage = normalize_development_source_v224(
            source_bytes=source_bytes,
            require_resource_complete=spec.case_id in _RESOURCE_DEVELOPMENT_CASES,
        )
        relative = f"config/dta-v22-4/development/agent-visible/{spec.case_id}.json"
        output = repository_root / relative
        _write_once(output, normalized)
        source_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        cases.append(
            {
                **spec.model_dump(mode="json"),
                "source_path": relative,
                "source_sha256": source_sha,
                "derivation": (
                    "Normalized synthetic/derived DTA v2.2.4 development copy of "
                    f"the frozen v2.2.3 case {spec.case_id}; explicit target coverage."
                ),
            }
        )
        coverages.append(coverage)
    _write_once(
        case_path,
        {"schema_version": "dta-v22.practical-case-set.v1", "cases": cases},
    )
    _write_once(truth_path, truths.model_dump(mode="json"))
    coverage_set = ReplayTargetCoverageSetV224(
        schema_version="dta-v22.4.replay-target-coverage-set.v1",
        cases=tuple(coverages),
    )
    _write_once(coverage_path, coverage_set.model_dump(mode="json"))
    utility = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_path,
        truth_path=truth_path,
    )
    _write_once(utility_path, utility.model_dump(mode="json"))


def _replace_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_strings(item, replacements) for key, item in value.items()
        }
    return value


def _cpu_record(*, service: str) -> ResourceUsageRecordV22:
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=96.0,
                memory_bytes=100_000_000,
            )
            for offset in (0, 2_500, 5_000, 7_500, 10_000)
        ),
        memory_slope_bytes_per_second=0.0,
    )


def _memory_record(*, service: str) -> ResourceUsageRecordV22:
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=20.0,
                memory_bytes=100_000_000 + index * 5_000_000,
            )
            for index, offset in enumerate((0, 2_500, 5_000, 7_500, 10_000))
        ),
        memory_slope_bytes_per_second=2_000_000.0,
    )


def _case_coverage(
    *, raw: dict[str, Any], case_id: str
) -> ReplayCaseTargetCoverageV224:
    normalized = cast(dict[str, Any], raw["normalized_case"])
    candidates = tuple(cast(list[str], normalized["candidate_services"]))
    capture = ReplayCaptureV22.model_validate_json(json.dumps(normalized["capture"]))
    captured_sources = {
        EvidenceSourceV22(item) for item in cast(list[str], raw["captured_sources"])
    }
    resource_targets = tuple(sorted(item.service for item in capture.resources))
    return ReplayCaseTargetCoverageV224(
        case_id=case_id,
        sources=tuple(
            build_replay_target_coverage_v224(
                source=source,
                candidate_services=candidates,
                covered_target_services=(
                    resource_targets
                    if source is EvidenceSourceV22.RESOURCES
                    else candidates
                    if source in captured_sources
                    else ()
                ),
            )
            for source in EvidenceSourceV22
        ),
    )


def _resource_evaluation_design() -> tuple[tuple[str, str | None, int | None, str], ...]:
    return (
        ("e01", "CPU_SATURATION", 1, "v224-cf-cpu-one"),
        ("e02", "CPU_SATURATION", 0, "v224-cf-cpu-one"),
        ("e03", "CPU_SATURATION", 1, "v224-cf-cpu-two"),
        ("e04", "CPU_SATURATION", 0, "v224-cf-cpu-two"),
        ("e05", "MEMORY_LEAK", 1, "v224-cf-memory-one"),
        ("e06", "MEMORY_LEAK", 0, "v224-cf-memory-one"),
        ("e07", "MEMORY_LEAK", 1, "v224-cf-memory-two"),
        ("e08", "MEMORY_LEAK", 0, "v224-cf-memory-two"),
        ("e09", None, None, "v224-resource-normal-one"),
        ("e10", None, None, "v224-resource-normal-two"),
    )


def _resource_pair_services(pair_id: str) -> tuple[str, str]:
    stem = pair_id.removeprefix("v224-").replace("cf-", "")
    return (f"eval-{stem}-a", f"eval-{stem}-b")


def build_fixed_evaluation_portfolio_v224(
    *,
    repository_root: Path,
    development_case_set_path: Path,
    development_truth_path: Path,
    output_root: Path,
) -> None:
    """Create the new fixed 16-case evaluation inputs without a final manifest."""

    outputs = tuple(
        output_root / name
        for name in ("cases.json", "truth.json", "coverage.json", "utility-audit.json")
    )
    if any(path.exists() for path in outputs):
        raise FileExistsError("v2.2.4 evaluation portfolio already exists")
    development = load_practical_case_set_v22(development_case_set_path)
    development_truths = {
        item.case_id: item
        for item in load_practical_truth_set_v22(development_truth_path).truths
    }
    development_specs = {item.case_id: item for item in development.cases}
    resource_template_path = repository_root / cast(
        str, development_specs["d05"].source_path
    )
    resource_template_bytes = resource_template_path.read_bytes()
    resource_template = cast(dict[str, Any], json.loads(resource_template_bytes))
    old_resource_candidates = tuple(
        cast(
            list[str],
            cast(dict[str, Any], resource_template["normalized_case"])[
                "candidate_services"
            ],
        )
    )
    cases: list[dict[str, object]] = []
    truths: list[dict[str, object]] = []
    coverages: list[ReplayCaseTargetCoverageV224] = []

    for case_id, mechanism, root_ordinal, pair_id in _resource_evaluation_design():
        candidates = _resource_pair_services(pair_id)
        raw = cast(
            dict[str, Any],
            _replace_strings(
                json.loads(resource_template_bytes),
                dict(zip(old_resource_candidates, candidates, strict=True)),
            ),
        )
        normalized = cast(dict[str, Any], raw["normalized_case"])
        capture = cast(dict[str, Any], normalized["capture"])
        normalized["case_id"] = case_id
        normalized["source_bytes_sha256"] = hashlib.sha256(
            resource_template_bytes + f"|v224-evaluation|{case_id}".encode()
        ).hexdigest()
        normalized["normalization_notes"] = [
            "Synthetic/derived DTA v2.2.4 fixed evaluation fixture; no Docker capture.",
            "Every candidate has an explicit normal or anomalous ResourceUsageRecord.",
            "Bootstrap runtime and metric coverage are symmetric across candidates.",
        ]
        resources = [normal_resource_record_v224(service=item) for item in candidates]
        if mechanism is not None and root_ordinal is not None:
            fault = (
                _cpu_record(service=candidates[root_ordinal])
                if mechanism == "CPU_SATURATION"
                else _memory_record(service=candidates[root_ordinal])
            )
            resources[root_ordinal] = fault
        capture["resources"] = [item.model_dump(mode="json") for item in resources]
        relative = f"config/dta-v22-4/evaluation/agent-visible/{case_id}.json"
        output = repository_root / relative
        _write_once(output, raw)
        cases.append(
            {
                "case_id": case_id,
                "modifier": "V222_EVALUATION_FIXTURE",
                "capture_kind": "SYNTHETIC_COUNTERFACTUAL_DERIVED",
                "bootstrap_insufficient_expected": mechanism is not None,
                "counterfactual_pair_ids": [pair_id],
                "source_path": relative,
                "source_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "derivation": (
                    "New synthetic/derived DTA v2.2.4 fixed resource evaluation case; "
                    "explicit target-complete Resources records."
                ),
            }
        )
        truths.append(
            {
                "case_id": case_id,
                "expected_terminal": "NO_INCIDENT" if mechanism is None else "DIAGNOSED",
                "expected_root_service": (
                    None if root_ordinal is None else candidates[root_ordinal]
                ),
                "expected_mechanism": mechanism,
                "evidence_applicable": mechanism is not None,
            }
        )
        coverages.append(_case_coverage(raw=raw, case_id=case_id))

    nonresource_design = (
        ("e11", "d14", "ABSTAIN"),
        ("e12", "d15", "ABSTAIN"),
        ("e13", "d01", "CONFIGURATION_ERROR"),
        ("e14", "d02", "CONFIGURATION_ERROR"),
        ("e15", "d03", "SERVICE_UNAVAILABLE"),
        ("e16", "d09", "DEPENDENCY_LATENCY"),
    )
    for case_id, source_id, expected in nonresource_design:
        spec = development_specs[source_id]
        source_path = repository_root / cast(str, spec.source_path)
        source_bytes = source_path.read_bytes()
        base = cast(dict[str, Any], json.loads(source_bytes))
        base_normalized = cast(dict[str, Any], base["normalized_case"])
        old_candidates = tuple(cast(list[str], base_normalized["candidate_services"]))
        candidates = (f"eval-{case_id}-a", f"eval-{case_id}-b")
        replacements = dict(zip(old_candidates, candidates, strict=True))
        raw = cast(dict[str, Any], _replace_strings(base, replacements))
        normalized = cast(dict[str, Any], raw["normalized_case"])
        normalized["case_id"] = case_id
        normalized["source_bytes_sha256"] = hashlib.sha256(
            source_bytes + f"|v224-evaluation|{case_id}".encode()
        ).hexdigest()
        normalized["normalization_notes"] = [
            "Synthetic/derived DTA v2.2.4 fixed evaluation fixture; no Docker capture.",
            "Service identities were deterministically rebound from a development blueprint.",
        ]
        relative = f"config/dta-v22-4/evaluation/agent-visible/{case_id}.json"
        output = repository_root / relative
        _write_once(output, raw)
        cases.append(
            {
                **spec.model_dump(mode="json"),
                "case_id": case_id,
                "modifier": "V222_EVALUATION_FIXTURE",
                "counterfactual_pair_ids": [],
                "source_path": relative,
                "source_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "derivation": (
                    "New synthetic/derived DTA v2.2.4 fixed nonresource control "
                    f"from development blueprint {source_id}."
                ),
            }
        )
        previous_truth = development_truths[source_id]
        expected_root = previous_truth.expected_root_service
        truths.append(
            {
                "case_id": case_id,
                "expected_terminal": (
                    "ABSTAIN" if expected == "ABSTAIN" else "DIAGNOSED"
                ),
                "expected_root_service": (
                    None if expected_root is None else replacements[expected_root]
                ),
                "expected_mechanism": None if expected == "ABSTAIN" else expected,
                "evidence_applicable": expected != "ABSTAIN",
            }
        )
        coverages.append(_case_coverage(raw=raw, case_id=case_id))

    cases.sort(key=lambda item: cast(str, item["case_id"]))
    truths.sort(key=lambda item: cast(str, item["case_id"]))
    coverages.sort(key=lambda item: item.case_id)
    case_path, truth_path, coverage_path, utility_path = outputs
    _write_once(
        case_path,
        {"schema_version": "dta-v22.practical-case-set.v1", "cases": cases},
    )
    _write_once(
        truth_path,
        {"schema_version": "dta-v22.practical-truth-set.v1", "truths": truths},
    )
    _write_once(
        coverage_path,
        ReplayTargetCoverageSetV224(
            schema_version="dta-v22.4.replay-target-coverage-set.v1",
            cases=tuple(coverages),
        ).model_dump(mode="json"),
    )
    utility = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_path,
        truth_path=truth_path,
    )
    _write_once(utility_path, utility.model_dump(mode="json"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_evaluation_manifest_v224(
    *,
    repository_root: Path,
    output_path: Path,
    implementation_commit: str,
    provider_model: str,
    prompt_sha256: str,
    minimum_request_interval_seconds: float,
) -> None:
    if output_path.exists():
        raise FileExistsError("v2.2.4 evaluation manifest already exists")
    evaluation = repository_root / "config/dta-v22-4/evaluation"
    case_path = evaluation / "cases.json"
    truth_path = evaluation / "truth.json"
    coverage_path = evaluation / "coverage.json"
    utility_path = evaluation / "utility-audit.json"
    prior_path = repository_root / "config/dta-v22-3/development-predicate-yield-prior.json"
    development_result = (
        repository_root / "docs/results/dta-v22-4-ambiguity-bundle-development.json"
    )
    history = repository_root / "config/dta-v22-4/historical-results.v1.json"
    implementation_paths = (
        "src/ecomsre/dta_v2/v22/replay_target_coverage_v224.py",
        "src/ecomsre/dta_v2/v22/contrastive_actions_v224.py",
        "src/ecomsre/dta_v2/v22/ambiguity_set_v224.py",
        "src/ecomsre/dta_v2/v22/no_incident_set_closure_v224.py",
        "src/ecomsre/dta_v2/v22/ambiguity_dispatch_v224.py",
        "src/ecomsre/dta_v2/v22/ambiguity_bundle_campaign_v224.py",
        "src/ecomsre/dta_v2/v22/ambiguity_bundle_scorer_v224.py",
        "src/ecomsre/dta_v2/v22/selection_provider_v223.py",
    )
    cases = load_practical_case_set_v22(case_path)
    payload = {
        "schema_version": "dta-v22.4.evaluation-manifest.v1",
        "base_commit": "9c601bd5d802fbe31990348c228e094985044a0b",
        "implementation_commit": implementation_commit,
        "implementation_sources": [
            {"path": path, "sha256": _sha256(repository_root / path)}
            for path in implementation_paths
        ],
        "case_set": {
            "path": "config/dta-v22-4/evaluation/cases.json",
            "sha256": _sha256(case_path),
        },
        "truth_set": {
            "path": "config/dta-v22-4/evaluation/truth.json",
            "sha256": _sha256(truth_path),
        },
        "target_coverage": {
            "path": "config/dta-v22-4/evaluation/coverage.json",
            "sha256": _sha256(coverage_path),
        },
        "utility_audit": {
            "path": "config/dta-v22-4/evaluation/utility-audit.json",
            "sha256": _sha256(utility_path),
        },
        "predicate_yield_prior": {
            "path": "config/dta-v22-3/development-predicate-yield-prior.json",
            "sha256": _sha256(prior_path),
        },
        "development_result": {
            "path": "docs/results/dta-v22-4-ambiguity-bundle-development.json",
            "sha256": _sha256(development_result),
        },
        "historical_results_manifest": {
            "path": "config/dta-v22-4/historical-results.v1.json",
            "sha256": _sha256(history),
        },
        "agent_visible_sources": [
            {"path": cast(str, item.source_path), "sha256": cast(str, item.source_sha256)}
            for item in cases.cases
        ],
        "composition": {
            "CPU_SATURATION": 4,
            "MEMORY_LEAK": 4,
            "NO_INCIDENT": 2,
            "ABSTAIN": 2,
            "CONFIGURATION_ERROR": 2,
            "SERVICE_UNAVAILABLE": 1,
            "DEPENDENCY_LATENCY": 1,
        },
        "resource_ambiguity_incidents": 8,
        "resource_all_normal_controls": 2,
        "counterfactual_resource_pairs": 4,
        "non_byte_identical_to_v223": 16,
        "expected_cases": 16,
        "expected_runs": 64,
        "combinations": [
            "TARGET_ONE",
            "TARGET_SET",
            "BUNDLE_ONE",
            "BUNDLE_SET",
        ],
        "schedule_rule": "DETERMINISTIC_BALANCED_ROTATION_INTERLEAVED_BY_CASE",
        "truth_isolation_rule": "LOAD_ONLY_AFTER_ALL_FOUR_CASE_RUNS",
        "single_execution_rule": "EXACTLY_ONE_FULL_STUDY_EXECUTION",
        "full_study_execution_count": 1,
        "execution_state": "NOT_STARTED",
        "provider_model": provider_model,
        "prompt_sha256": prompt_sha256,
        "minimum_request_interval_seconds": minimum_request_interval_seconds,
        "maximum_protocol_repairs_per_case": 2,
        "maximum_transport_retries_per_exact_request": 3,
        "docker_calls": 0,
        "runbook_calls": 0,
        "agent_writes": 0,
    }
    _write_once(output_path, payload)


__all__ = (
    "build_evaluation_manifest_v224",
    "build_fixed_evaluation_portfolio_v224",
    "build_normalized_development_portfolio_v224",
    "normalize_development_source_v224",
)
