"""Build byte-new opaque development and fixed evaluation portfolios."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from ecomsre.dta_v2.v22.evaluation_strata_v225 import EvaluatorStrataV225
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import audit_case_set_v222
from ecomsre.dta_v2.v22.opaque_identity_v225 import OpaqueIdentityPlanV225
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    ReplayCaseTargetCoverageV225,
    ReplayTargetCoverageSetV225,
    build_replay_target_coverage_v225,
    normal_resource_record_v225,
)


PortfolioPhaseV225 = Literal["development", "evaluation"]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))


def _replace_exact_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_exact_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_exact_strings(item, replacements)
            for key, item in value.items()
        }
    return value


def _collect_keyed_strings(value: object, key: str) -> tuple[str, ...]:
    values: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for raw_key, child in item.items():
                if raw_key == key and isinstance(child, str):
                    values.add(child)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(sorted(values))


def _rebind_agent_visible(
    *,
    source_bytes: bytes,
    case_id: str,
    candidate_services: tuple[str, str],
    operation_ids: tuple[str, ...],
    change_ids: tuple[str, ...],
    phase: PortfolioPhaseV225,
) -> tuple[dict[str, Any], dict[str, str]]:
    raw = cast(dict[str, Any], json.loads(source_bytes))
    normalized = cast(dict[str, Any], raw["normalized_case"])
    old_candidates = tuple(cast(list[str], normalized["candidate_services"]))
    if len(old_candidates) != 2:
        raise ValueError("v2.2.5 blueprint must have exactly two candidate services")
    service_map = dict(zip(old_candidates, candidate_services, strict=True))
    operations = _collect_keyed_strings(raw, "operation")
    changes = _collect_keyed_strings(raw, "opaque_change_id")
    if len(operations) > len(operation_ids) or len(changes) > len(change_ids):
        raise ValueError("v2.2.5 opaque identity pool is too small for a blueprint")
    replacements = {
        **service_map,
        **dict(zip(operations, operation_ids, strict=False)),
        **dict(zip(changes, change_ids, strict=False)),
    }
    rebound = cast(dict[str, Any], _replace_exact_strings(raw, replacements))
    rebound_normalized = cast(dict[str, Any], rebound["normalized_case"])
    rebound_normalized["case_id"] = case_id
    rebound_normalized["source_bytes_sha256"] = hashlib.sha256(
        source_bytes
        + f"|dta-v22.5|{phase}|{case_id}|opaque-v1".encode("utf-8")
    ).hexdigest()
    rebound_normalized["normalization_notes"] = [
        "Synthetic/derived DTA v2.2.5 opaque replay fixture; no Docker capture.",
        "Service, operation, and change identities come from a neutral ordinal pool.",
        "Provider projection excludes case, truth, pair, derivation, and source metadata.",
    ]
    return rebound, service_map


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
) -> ReplayCaseTargetCoverageV225:
    normalized = cast(dict[str, Any], raw["normalized_case"])
    candidates = tuple(cast(list[str], normalized["candidate_services"]))
    capture = ReplayCaptureV22.model_validate_json(json.dumps(normalized["capture"]))
    captured_sources = {
        EvidenceSourceV22(item) for item in cast(list[str], raw["captured_sources"])
    }
    resource_targets = tuple(sorted(item.service for item in capture.resources))
    return ReplayCaseTargetCoverageV225(
        case_id=case_id,
        sources=tuple(
            build_replay_target_coverage_v225(
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


def _strata(case_prefix: str) -> EvaluatorStrataV225:
    def case(index: int) -> str:
        return f"{case_prefix}{index:02d}"

    return EvaluatorStrataV225.build(
        resource_ambiguity_incidents=tuple(case(index) for index in range(1, 9)),
        resource_normal_controls=(case(9), case(10)),
        abstention_controls=(case(11), case(12)),
        configuration_incidents=(case(13), case(14)),
        service_unavailable_incidents=(case(15),),
        dependency_incidents=(case(16),),
        cpu_incidents=tuple(case(index) for index in range(1, 5)),
        memory_incidents=tuple(case(index) for index in range(5, 9)),
    )


def _resource_design(case_prefix: str) -> tuple[tuple[str, str | None, int | None, int], ...]:
    def case(index: int) -> str:
        return f"{case_prefix}{index:02d}"

    return (
        (case(1), "CPU_SATURATION", 1, 0),
        (case(2), "CPU_SATURATION", 0, 0),
        (case(3), "CPU_SATURATION", 1, 1),
        (case(4), "CPU_SATURATION", 0, 1),
        (case(5), "MEMORY_LEAK", 1, 2),
        (case(6), "MEMORY_LEAK", 0, 2),
        (case(7), "MEMORY_LEAK", 1, 3),
        (case(8), "MEMORY_LEAK", 0, 3),
        (case(9), None, None, 4),
        (case(10), None, None, 5),
    )


def _pair_services(plan: OpaqueIdentityPlanV225, ordinal: int) -> tuple[str, str]:
    pair = tuple(
        sorted((plan.services[2 * ordinal], plan.services[2 * ordinal + 1]))
    )
    return cast(tuple[str, str], pair)


def build_opaque_portfolio_v225(
    *,
    repository_root: Path,
    blueprint_case_set_path: Path,
    blueprint_truth_path: Path,
    output_root: Path,
    identity_plan: OpaqueIdentityPlanV225,
    phase: PortfolioPhaseV225,
) -> None:
    """Create one 16-case portfolio; evaluator metadata never enters agent files."""

    expected_outputs = tuple(
        output_root / name
        for name in ("cases.json", "truth.json", "coverage.json", "utility-audit.json", "strata.json")
    )
    if any(path.exists() for path in expected_outputs) or (output_root / "agent-visible").exists():
        raise FileExistsError(f"v2.2.5 {phase} portfolio already exists")
    blueprint_cases = {
        item.case_id: item
        for item in load_practical_case_set_v22(blueprint_case_set_path).cases
    }
    blueprint_truths = {
        item.case_id: item
        for item in load_practical_truth_set_v22(blueprint_truth_path).truths
    }
    case_prefix = "d" if phase == "development" else "e"
    pair_offset = 0 if phase == "development" else 16
    operation_offset = 0 if phase == "development" else 32
    change_offset = 0 if phase == "development" else 16
    resource_template_path = repository_root / cast(
        str, blueprint_cases["d05"].source_path
    )
    resource_template_bytes = resource_template_path.read_bytes()
    cases: list[dict[str, object]] = []
    truths: list[dict[str, object]] = []
    coverages: list[ReplayCaseTargetCoverageV225] = []

    for case_id, mechanism, root_ordinal, local_pair in _resource_design(case_prefix):
        case_index = int(case_id[1:]) - 1
        pair_ordinal = pair_offset + local_pair
        candidates = _pair_services(identity_plan, pair_ordinal)
        raw, _ = _rebind_agent_visible(
            source_bytes=resource_template_bytes,
            case_id=case_id,
            candidate_services=candidates,
            operation_ids=(identity_plan.operations[operation_offset + case_index],),
            change_ids=(identity_plan.changes[change_offset + case_index],),
            phase=phase,
        )
        normalized = cast(dict[str, Any], raw["normalized_case"])
        capture = cast(dict[str, Any], normalized["capture"])
        resources = [normal_resource_record_v225(service=item) for item in candidates]
        if mechanism is not None and root_ordinal is not None:
            resources[root_ordinal] = (
                _cpu_record(service=candidates[root_ordinal])
                if mechanism == "CPU_SATURATION"
                else _memory_record(service=candidates[root_ordinal])
            )
        capture["resources"] = [item.model_dump(mode="json") for item in resources]
        relative = f"config/dta-v22-5/{phase}/agent-visible/{case_id}.json"
        output = repository_root / relative
        _write_once(output, raw)
        cases.append(
            {
                "case_id": case_id,
                "modifier": "V222_EVALUATION_FIXTURE",
                "capture_kind": "SYNTHETIC_COUNTERFACTUAL_DERIVED",
                "bootstrap_insufficient_expected": mechanism is not None,
                "counterfactual_pair_ids": [identity_plan.pairs[pair_ordinal]],
                "source_path": relative,
                "source_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "derivation": (
                    "DTA v2.2.5 opaque synthetic resource portfolio case from a "
                    "truth-isolated v2.2.3 blueprint."
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

    nonresource = (
        (11, "d14", "ABSTAIN"),
        (12, "d15", "ABSTAIN"),
        (13, "d01", "CONFIGURATION_ERROR"),
        (14, "d02", "CONFIGURATION_ERROR"),
        (15, "d03", "SERVICE_UNAVAILABLE"),
        (16, "d09", "DEPENDENCY_LATENCY"),
    )
    for local_index, blueprint_id, expected in nonresource:
        case_id = f"{case_prefix}{local_index:02d}"
        case_index = local_index - 1
        pair_ordinal = pair_offset + local_index - 5
        candidates = _pair_services(identity_plan, pair_ordinal)
        spec = blueprint_cases[blueprint_id]
        source_path = repository_root / cast(str, spec.source_path)
        raw, service_map = _rebind_agent_visible(
            source_bytes=source_path.read_bytes(),
            case_id=case_id,
            candidate_services=candidates,
            operation_ids=(identity_plan.operations[operation_offset + case_index],),
            change_ids=(identity_plan.changes[change_offset + case_index],),
            phase=phase,
        )
        relative = f"config/dta-v22-5/{phase}/agent-visible/{case_id}.json"
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
                    "DTA v2.2.5 opaque synthetic nonresource portfolio case from "
                    f"truth-isolated blueprint {blueprint_id}."
                ),
            }
        )
        old_truth = blueprint_truths[blueprint_id]
        truths.append(
            {
                "case_id": case_id,
                "expected_terminal": "ABSTAIN" if expected == "ABSTAIN" else "DIAGNOSED",
                "expected_root_service": (
                    None
                    if old_truth.expected_root_service is None
                    else service_map[old_truth.expected_root_service]
                ),
                "expected_mechanism": None if expected == "ABSTAIN" else expected,
                "evidence_applicable": expected != "ABSTAIN",
            }
        )
        coverages.append(_case_coverage(raw=raw, case_id=case_id))

    cases.sort(key=lambda item: cast(str, item["case_id"]))
    truths.sort(key=lambda item: cast(str, item["case_id"]))
    coverages.sort(key=lambda item: item.case_id)
    case_path, truth_path, coverage_path, utility_path, strata_path = expected_outputs
    _write_once(case_path, {"schema_version": "dta-v22.practical-case-set.v1", "cases": cases})
    _write_once(truth_path, {"schema_version": "dta-v22.practical-truth-set.v1", "truths": truths})
    _write_once(
        coverage_path,
        ReplayTargetCoverageSetV225(
            schema_version="dta-v22.5.replay-target-coverage-set.v1",
            cases=tuple(coverages),
        ).model_dump(mode="json"),
    )
    utility = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_path,
        truth_path=truth_path,
    )
    _write_once(utility_path, utility.model_dump(mode="json"))
    _write_once(strata_path, _strata(case_prefix).model_dump(mode="json"))


def build_normalized_development_portfolio_v225(
    *,
    repository_root: Path,
    previous_case_set_path: Path,
    previous_truth_path: Path,
    output_root: Path,
    identity_plan: OpaqueIdentityPlanV225,
) -> None:
    build_opaque_portfolio_v225(
        repository_root=repository_root,
        blueprint_case_set_path=previous_case_set_path,
        blueprint_truth_path=previous_truth_path,
        output_root=output_root,
        identity_plan=identity_plan,
        phase="development",
    )


def build_fixed_evaluation_portfolio_v225(
    *,
    repository_root: Path,
    blueprint_case_set_path: Path,
    blueprint_truth_path: Path,
    output_root: Path,
    identity_plan: OpaqueIdentityPlanV225,
) -> None:
    build_opaque_portfolio_v225(
        repository_root=repository_root,
        blueprint_case_set_path=blueprint_case_set_path,
        blueprint_truth_path=blueprint_truth_path,
        output_root=output_root,
        identity_plan=identity_plan,
        phase="evaluation",
    )


__all__ = (
    "build_fixed_evaluation_portfolio_v225",
    "build_normalized_development_portfolio_v225",
    "build_opaque_portfolio_v225",
)
