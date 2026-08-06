"""Evaluator-only unblinding, scoring, and preregistered report assembly."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import mean, median

from pydantic import ValidationError

from ecomsre_rcaeval.artifacts import (
    canonical_json_bytes,
    read_json_object,
)
from ecomsre_rcaeval.contracts import (
    Architecture,
    GroundTruth,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.scoring import score_terminal_records
from ecomsre_rcaeval.statistics import (
    BootstrapMetric,
    ScoredObservation,
    hierarchical_paired_bootstrap,
)


def _validate_truth_distribution(truth: dict[str, GroundTruth]) -> None:
    strata: dict[tuple[str, str], set[str]] = {}
    services: set[str] = set()
    faults: set[str] = set()
    for item in truth.values():
        services.add(item.root_cause_service)
        faults.add(item.fault)
        strata.setdefault((item.root_cause_service, item.fault), set()).add(
            item.instance
        )
    if (
        len(strata) != 30
        or len(services) != 5
        or faults != {"cpu", "mem", "disk", "delay", "loss", "socket"}
        or any(len(instances) != 3 for instances in strata.values())
    ):
        raise ValueError(
            "RCAEval holdout Ground Truth requires 30 strata by three instances"
        )


def load_ground_truth(path: Path, *, expected_cases: int = 90) -> dict[str, GroundTruth]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("evaluator-only Ground Truth mapping is invalid")
    try:
        payload = read_json_object(path)
    except ValueError as error:
        raise ValueError("evaluator-only Ground Truth mapping is unreadable") from error
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "cases"}:
        raise ValueError("evaluator-only Ground Truth mapping has unexpected fields")
    if payload.get("schema_version") != "rcaeval-re2.ground-truth-mapping.v1":
        raise ValueError("evaluator-only Ground Truth mapping schema is invalid")
    cases = payload.get("cases")
    if not isinstance(cases, dict) or len(cases) != expected_cases:
        raise ValueError("evaluator-only Ground Truth case count is invalid")
    truth: dict[str, GroundTruth] = {}
    for case_id, item in cases.items():
        if not isinstance(case_id, str) or not isinstance(item, dict):
            raise ValueError("evaluator-only Ground Truth case is invalid")
        try:
            truth[case_id] = GroundTruth(case_id=case_id, **item)
        except (TypeError, ValidationError) as error:
            raise ValueError("evaluator-only Ground Truth case is invalid") from error
    expected_ids = {f"tt-case-{index:04d}" for index in range(1, expected_cases + 1)}
    if set(truth) != expected_ids:
        raise ValueError("evaluator-only Ground Truth opaque case set is invalid")
    if expected_cases == 90:
        _validate_truth_distribution(truth)
    return truth


def _observations(
    records: tuple[TerminalRecord, ...],
    truth: dict[str, GroundTruth],
) -> tuple[ScoredObservation, ...]:
    scored, _ = score_terminal_records(records, truth)
    return tuple(
        ScoredObservation(
            stratum=(
                f"{truth[item.case_id].root_cause_service}_{truth[item.case_id].fault}"
            ),
            instance=truth[item.case_id].instance,
            architecture=item.architecture,
            root_service_correct=item.root_service_correct,
            tool_calls=item.tool_calls,
        )
        for item in scored
    )


def build_final_report(
    records: tuple[TerminalRecord, ...],
    truth: dict[str, GroundTruth],
    *,
    bootstrap_replicates: int = 10_000,
    bootstrap_seed: int = 20_260_806,
) -> dict[str, object]:
    if len(records) != 270 or len(truth) != 90:
        raise ValueError("final report requires the complete 90 by three-arm run set")
    _validate_truth_distribution(truth)
    scored, summaries = score_terminal_records(records, truth)
    if set(summaries) != set(Architecture) or any(
        summary.denominator != 90 for summary in summaries.values()
    ):
        raise ValueError("final report architecture denominator is incomplete")
    observations = _observations(records, truth)
    primary = hierarchical_paired_bootstrap(
        observations,
        left=Architecture.DYNAMIC,
        right=Architecture.SINGLE,
        metric=BootstrapMetric.ROOT_SERVICE_AC1,
        replicates=bootstrap_replicates,
        seed=bootstrap_seed,
    )
    cost_accuracy = hierarchical_paired_bootstrap(
        observations,
        left=Architecture.DYNAMIC,
        right=Architecture.SINGLE,
        metric=BootstrapMetric.ROOT_SERVICE_AC1,
        replicates=bootstrap_replicates,
        seed=bootstrap_seed,
    )
    tool_reduction = hierarchical_paired_bootstrap(
        observations,
        left=Architecture.DYNAMIC,
        right=Architecture.SINGLE,
        metric=BootstrapMetric.RELATIVE_TOOL_REDUCTION,
        replicates=bootstrap_replicates,
        seed=bootstrap_seed,
    )
    terminal_taxonomy = Counter(record.terminal_status.value for record in records)
    architecture_details: dict[str, object] = {}
    for architecture in Architecture:
        architecture_records = tuple(
            record for record in records if record.architecture is architecture
        )
        known_tokens = tuple(
            record.known_provider_tokens
            for record in architecture_records
            if record.known_provider_tokens is not None
        )
        summary = summaries[architecture]
        architecture_details[architecture.value] = {
            **summary.model_dump(mode="json"),
            "runtime_completion": sum(
                record.terminal_status is TerminalStatus.COMPLETED
                for record in architecture_records
            )
            / len(architecture_records),
            "evidence_validity": sum(
                record.terminal_status is TerminalStatus.COMPLETED
                and record.diagnosis is not None
                and bool(record.diagnosis.evidence_refs)
                for record in architecture_records
            )
            / len(architecture_records),
            "mean_tool_calls": mean(record.tool_calls for record in architecture_records),
            "median_tool_calls": median(
                record.tool_calls for record in architecture_records
            ),
            "mean_model_calls": mean(
                record.model_calls for record in architecture_records
            ),
            "median_model_calls": median(
                record.model_calls for record in architecture_records
            ),
            "known_provider_token_records": len(known_tokens),
            "mean_known_provider_tokens": mean(known_tokens) if known_tokens else None,
            "mean_latency_seconds": mean(
                record.latency_seconds for record in architecture_records
            ),
            "median_latency_seconds": median(
                record.latency_seconds for record in architecture_records
            ),
        }
    cost_quality_supported = (
        cost_accuracy.ci_lower >= -0.05
        and tool_reduction.point_estimate >= 0.2
        and tool_reduction.ci_lower > 0.0
    )
    fault_subgroups: dict[str, object] = {}
    for fault in ("cpu", "mem", "disk", "delay", "loss", "socket"):
        by_architecture: dict[str, object] = {}
        for architecture in Architecture:
            items = tuple(
                item
                for item in scored
                if item.architecture is architecture
                and truth[item.case_id].fault == fault
            )
            if len(items) != 15:
                raise ValueError("final report fault subgroup denominator is incomplete")
            by_architecture[architecture.value] = {
                "denominator": len(items),
                "root_service_ac1": sum(
                    item.root_service_correct for item in items
                )
                / len(items),
                "root_cause_pair_ac1": sum(
                    item.root_cause_pair_correct for item in items
                )
                / len(items),
            }
        fault_subgroups[fault] = by_architecture
    return {
        "schema_version": "rcaeval-re2.final-report.v1",
        "protocol_id": "rcaeval-re2-external-v1",
        "primary": primary.model_dump(mode="json"),
        "primary_superiority_supported": primary.ci_lower > 0.0,
        "cost_quality_accuracy": cost_accuracy.model_dump(mode="json"),
        "cost_quality_tool_reduction": tool_reduction.model_dump(mode="json"),
        "cost_quality_supported": cost_quality_supported,
        "architectures": architecture_details,
        "terminal_taxonomy": dict(sorted(terminal_taxonomy.items())),
        "fault_subgroups_descriptive_only": fault_subgroups,
        "scored_cases": [item.model_dump(mode="json") for item in scored],
    }


def verify_final_report(
    report_path: Path,
    records: tuple[TerminalRecord, ...],
    truth: dict[str, GroundTruth],
    *,
    bootstrap_replicates: int = 10_000,
    bootstrap_seed: int = 20_260_806,
) -> None:
    if not report_path.is_file() or report_path.is_symlink():
        raise ValueError("final report is not a regular file")
    try:
        observed = read_json_object(report_path)
    except ValueError as error:
        raise ValueError("final report is invalid JSON") from error
    expected = build_final_report(
        records,
        truth,
        bootstrap_replicates=bootstrap_replicates,
        bootstrap_seed=bootstrap_seed,
    )
    if canonical_json_bytes(observed) != canonical_json_bytes(expected):
        raise ValueError("final report differs from recomputed frozen analysis")
