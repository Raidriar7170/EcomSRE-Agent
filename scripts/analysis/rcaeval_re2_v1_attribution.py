"""Post-hoc attribution of the frozen RCAEval RE2 v1 negative result.

This module is deliberately read-only with respect to the frozen benchmark. It
does not import the Provider adapter and cannot execute, retry, or regenerate a
holdout run. Case-level material is written only to an explicitly supplied
private directory outside Git; tracked outputs contain aggregates only.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
import csv
import json
from pathlib import Path
import re
from statistics import mean, median
from typing import Any

from ecomsre_rcaeval.artifacts import (  # type: ignore[import-not-found]
    canonical_json_bytes,
    read_json_object,
    sha256_file,
)
from ecomsre_rcaeval.contracts import (  # type: ignore[import-not-found]
    Architecture,
    CanonicalIndicator,
    GroundTruth,
    ScheduledRun,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.dataset import (  # type: ignore[import-not-found]
    DevSystem,
    TelemetryCase,
    discover_dev_cases,
    load_sanitized_cases,
)
from ecomsre_rcaeval.execution import (  # type: ignore[import-not-found]
    load_terminal_records,
    validate_attempt_markers,
)
from ecomsre_rcaeval.reporting import load_ground_truth  # type: ignore[import-not-found]
from ecomsre_rcaeval.scoring import (  # type: ignore[import-not-found]
    normalize_indicator,
    score_terminal_records,
)
from ecomsre_rcaeval.tools import (  # type: ignore[import-not-found]
    RCAEvalToolset,
    SourceStatus,
    ToolEvidence,
)


CLASSIFICATION = (
    "POST_HOC_EXPLORATORY",
    "NOT_PRIMARY_INFERENCE",
    "NO_HOLDOUT_RERUN",
)
READY_STATE = "POST_HOC_ATTRIBUTION_REPORT_READY_FOR_HUMAN_REVIEW"
UNOBSERVABLE = "UNOBSERVABLE_FROM_FROZEN_ARTIFACTS"
IMPLEMENTATION_COMMIT = "3a03995037ce410488a4364f8a485b27c80f0ac0"
EXPECTED_BINDINGS = {
    "protocol_freeze_sha256": (
        "cb5e31a0a20a3d7a4a2c10c6e2454ca19deb16d6faab01bf83e805b0840f1a2f"
    ),
    "terminal_records_lock_sha256": (
        "4eaeb2a1b68413ea6bea86391d8663baf49228484b2a935d2f0256dece321ab0"
    ),
    "unblinding_lock_sha256": (
        "19c52b02b07ed63c7592335062acd2cc638c025cd9346e491cbf30c9ee9cbe89"
    ),
    "final_report_sha256": (
        "f40be2375ccd80b9cdd831577079043d4370a02924c19073b0ec8cf8b3232155"
    ),
}
ALLOWED_PUBLIC_PATHS = (
    "scripts/analysis/rcaeval_re2_v1_attribution.py",
    "tests/analysis/test_rcaeval_re2_v1_attribution.py",
    "docs/results/live-telemetry-instrumentation-v2-human-brief.md",
    "docs/results/live-telemetry-instrumentation-v2.json",
    "docs/results/live-telemetry-instrumentation-v2.md",
    "docs/results/live-telemetry-instrumentation-v3-human-brief.md",
    "docs/results/live-telemetry-instrumentation-v3.json",
    "docs/results/live-telemetry-instrumentation-v3.md",
    "docs/results/rcaeval-re2-v1-attribution-aggregate.json",
    "docs/results/rcaeval-re2-v1-attribution-summary.md",
    "docs/review-evidence/rcaeval-re2-v1-attribution/current-disposition.json",
)
_FORBIDDEN_PUBLIC_MARKERS = (
    "tt-case-",
    "case_id",
    "run_id",
    "instance",
    "scored_cases",
    "ground-truth",
    "evaluator-only",
    "/users/",
    "/home/",
    "/private/",
    "bearer",
    "authorization",
    "api_key",
)
_ARCHITECTURES = tuple(Architecture)
_SOURCES = ("metrics", "logs", "traces")
_INDICATORS = ("cpu", "mem", "diskio", "latency", "socket")


def ratio(numerator: int, denominator: int) -> dict[str, int | float]:
    """Return an exact auditable count ratio."""

    if denominator <= 0:
        raise ValueError("ratio denominator must be positive")
    if numerator < 0 or numerator > denominator:
        raise ValueError("ratio numerator must be within the denominator")
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator,
    }


def validate_allowed_paths(paths: Iterable[str]) -> None:
    unexpected = set(paths) - set(ALLOWED_PUBLIC_PATHS)
    if unexpected:
        raise ValueError(f"frozen or undeclared path in attribution diff: {sorted(unexpected)}")


def assert_public_payload(payload: object) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    matches = [marker for marker in _FORBIDDEN_PUBLIC_MARKERS if marker in encoded]
    if matches:
        raise ValueError(f"public payload contains forbidden marker: {matches[0]}")


def _metric_indicator(name: str) -> CanonicalIndicator | None:
    """Apply the frozen project's existing canonical metric-name markers."""

    lowered = name.casefold()
    for marker, indicator in (
        ("disk", "diskio"),
        ("lat", "latency"),
        ("socket", "socket"),
        ("mem", "mem"),
        ("cpu", "cpu"),
    ):
        if marker in lowered:
            return indicator  # type: ignore[return-value]
    return None


def _evidence_score(item: ToolEvidence) -> float | None:
    number = r"([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)"
    patterns = (
        rf"anomaly-score={number}",
        rf"combined-score={number}",
        r"^count=([0-9]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, item.summary)
        if match is not None:
            return float(match.group(1))
    return None


def _source_projection(
    *,
    status: SourceStatus,
    evidence: Sequence[ToolEvidence],
    truth_service: str,
) -> dict[str, Any]:
    if status is SourceStatus.SOURCE_UNAVAILABLE:
        return {
            "status": "SOURCE_UNAVAILABLE",
            "evidence_count": 0,
            "truth_service_rank": None,
            "coverage_at_1": None,
            "coverage_at_3": None,
            "coverage_at_6": None,
            "top_1_service": None,
            "top_1_top_2_score_margin": None,
            "unique_service_count": 0,
            "evidence": [],
        }
    services = [item.service for item in evidence]
    rank = next(
        (index for index, service in enumerate(services, start=1) if service == truth_service),
        None,
    )
    scores = [_evidence_score(item) for item in evidence[:2]]
    margin = None
    if len(scores) == 2 and scores[0] is not None and scores[1] is not None:
        margin = scores[0] - scores[1]
    return {
        "status": "AVAILABLE",
        "evidence_count": len(evidence),
        "truth_service_rank": rank,
        "coverage_at_1": rank is not None and rank <= 1,
        "coverage_at_3": rank is not None and rank <= 3,
        "coverage_at_6": rank is not None and rank <= 6,
        "top_1_service": services[0] if services else None,
        "top_1_top_2_score_margin": margin,
        "unique_service_count": len(set(services)),
        "evidence": [
            {"service": item.service, "name": item.name, "rank": index}
            for index, item in enumerate(evidence, start=1)
        ],
    }


def _metric_columns(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("metrics CSV has no header") from error
    return [name for name in header if name != "time"]


def deterministic_projection(
    case: TelemetryCase,
    *,
    truth_service: str,
    truth_indicator: CanonicalIndicator,
) -> dict[str, Any]:
    """Reconstruct the exact frozen top-6 tool projections without a Provider."""

    tools = RCAEvalToolset(case)
    metrics = tools.rank_metric_anomalies(top_k=6)
    logs = tools.summarize_log_patterns(top_k=6)
    traces = tools.summarize_trace_diagnostics(top_k=6)
    columns = _metric_columns(case.metrics_path)
    raw_matches = [
        name
        for name in columns
        if name.rsplit("_", 1)[0] == truth_service
        and _metric_indicator(name) == truth_indicator
    ]
    projected_matches = [
        item.name
        for item in metrics.evidence
        if item.service == truth_service
        and _metric_indicator(item.name) == truth_indicator
    ]
    return {
        "metrics": _source_projection(
            status=metrics.status,
            evidence=metrics.evidence,
            truth_service=truth_service,
        ),
        "logs": _source_projection(
            status=logs.status,
            evidence=logs.evidence,
            truth_service=truth_service,
        ),
        "traces": _source_projection(
            status=traces.status,
            evidence=traces.evidence,
            truth_service=truth_service,
        ),
        "indicator": {
            "raw_present": bool(raw_matches),
            "top6_present": bool(projected_matches),
            "raw_matching_metric_count": len(raw_matches),
            "top6_matching_metric_count": len(projected_matches),
        },
        "tool_calls": tools.call_count,
    }


def _records_by_case(
    records: Sequence[TerminalRecord],
) -> dict[str, dict[Architecture, TerminalRecord]]:
    grouped: dict[str, dict[Architecture, TerminalRecord]] = defaultdict(dict)
    for record in records:
        if record.architecture in grouped[record.case_id]:
            raise ValueError("duplicate architecture arm for one case")
        grouped[record.case_id][record.architecture] = record
    if any(set(items) != set(_ARCHITECTURES) for items in grouped.values()):
        raise ValueError("each attribution case requires all three architecture arms")
    return dict(grouped)


def _scored_lookup(
    records: Sequence[TerminalRecord], truth: Mapping[str, GroundTruth]
) -> dict[tuple[str, Architecture], tuple[bool, bool]]:
    scored, _ = score_terminal_records(tuple(records), dict(truth))
    return {
        (item.case_id, item.architecture): (
            item.root_service_correct,
            item.root_cause_pair_correct,
        )
        for item in scored
    }


def build_architecture_decomposition(
    records: Sequence[TerminalRecord], truth: Mapping[str, GroundTruth]
) -> dict[str, Any]:
    scored = _scored_lookup(records, truth)
    result: dict[str, Any] = {}
    raw_counts: dict[Architecture, dict[str, int]] = {}
    for architecture in _ARCHITECTURES:
        architecture_records = [
            record for record in records if record.architecture is architecture
        ]
        denominator = len(architecture_records)
        completed = sum(
            record.terminal_status is TerminalStatus.COMPLETED
            for record in architecture_records
        )
        failures = denominator - completed
        service_correct = sum(
            scored[(record.case_id, architecture)][0]
            for record in architecture_records
        )
        pair_correct = sum(
            scored[(record.case_id, architecture)][1]
            for record in architecture_records
        )
        completed_wrong = completed - service_correct
        raw_counts[architecture] = {
            "denominator": denominator,
            "completed": completed,
            "failures": failures,
            "service_correct": service_correct,
            "pair_correct": pair_correct,
            "completed_wrong": completed_wrong,
        }
        result[architecture.value] = {
            "runs": denominator,
            "completed": completed,
            "terminal_failures": failures,
            "completed_but_root_service_wrong": completed_wrong,
            "root_service_accuracy": ratio(service_correct, denominator),
            "root_cause_pair_accuracy": ratio(pair_correct, denominator),
            "completed_only_root_service_accuracy": ratio(service_correct, completed),
            "reliability_ceiling_if_all_failures_correct": ratio(
                service_correct + failures, denominator
            ),
        }
    single = raw_counts[Architecture.SINGLE]
    for architecture in _ARCHITECTURES:
        current = raw_counts[architecture]
        excess_failures = current["failures"] - single["failures"]
        excess_completed_wrong = (
            current["completed_wrong"] - single["completed_wrong"]
        )
        total_gap = single["service_correct"] - current["service_correct"]
        result[architecture.value]["vs_single"] = {
            "excess_terminal_failures": excess_failures,
            "excess_completed_wrong": excess_completed_wrong,
            "total_correct_gap": total_gap,
            "reconciled": excess_failures + excess_completed_wrong == total_gap,
        }
        if not result[architecture.value]["vs_single"]["reconciled"]:
            raise ValueError("terminal/completed-wrong decomposition did not reconcile")
    return result


def build_pairwise_outcomes(
    records: Sequence[TerminalRecord], truth: Mapping[str, GroundTruth]
) -> dict[str, Any]:
    grouped = _records_by_case(records)
    scored = _scored_lookup(records, truth)
    labels = ("wrong", "correct")
    counts = {
        f"single_{single}__fixed_{fixed}__dynamic_{dynamic}": 0
        for single in labels
        for fixed in labels
        for dynamic in labels
    }
    same_source_count = 0
    simplified = Counter(
        {
            "single_correct_fixed_wrong": 0,
            "single_correct_dynamic_wrong": 0,
            "single_wrong_fixed_correct": 0,
            "single_wrong_dynamic_correct": 0,
        }
    )
    for case_id, arms in grouped.items():
        correctness = {
            architecture: scored[(case_id, architecture)][0]
            for architecture in _ARCHITECTURES
        }
        key = "__".join(
            f"{architecture.value}_{'correct' if correctness[architecture] else 'wrong'}"
            for architecture in _ARCHITECTURES
        )
        counts[key] += 1
        if correctness[Architecture.SINGLE] and not correctness[Architecture.FIXED]:
            simplified["single_correct_fixed_wrong"] += 1
            if (
                arms[Architecture.SINGLE].terminal_status is TerminalStatus.COMPLETED
                and arms[Architecture.FIXED].terminal_status is TerminalStatus.COMPLETED
            ):
                same_source_count += 1
        if correctness[Architecture.SINGLE] and not correctness[Architecture.DYNAMIC]:
            simplified["single_correct_dynamic_wrong"] += 1
        if not correctness[Architecture.SINGLE] and correctness[Architecture.FIXED]:
            simplified["single_wrong_fixed_correct"] += 1
        if not correctness[Architecture.SINGLE] and correctness[Architecture.DYNAMIC]:
            simplified["single_wrong_dynamic_correct"] += 1
    denominator = len(grouped)
    return {
        "eight_way": counts,
        "simplified": dict(simplified),
        "same_source_set_semantic_degradation": {
            "classification": "SAME_SOURCE_SET_SEMANTIC_DEGRADATION",
            "count": same_source_count,
            "denominator": denominator,
            "rate": ratio(same_source_count, denominator),
            "evidence_level": (
                "LEVEL_1_DIRECT + LEVEL_2_DETERMINISTIC_RECONSTRUCTION"
            ),
            "internal_mechanism": UNOBSERVABLE,
        },
    }


def _mean_or_none(values: Sequence[int | float]) -> float | None:
    return mean(values) if values else None


def _distribution(values: Sequence[int | float]) -> dict[str, int | float | None]:
    return {
        "count": len(values),
        "mean": _mean_or_none(values),
        "median": median(values) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def build_terminal_failures(records: Sequence[TerminalRecord]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for architecture in _ARCHITECTURES:
        items = [record for record in records if record.architecture is architecture]
        failures = [
            record
            for record in items
            if record.terminal_status is not TerminalStatus.COMPLETED
        ]
        statuses = Counter(record.terminal_status.value for record in items)
        failure_codes = Counter(
            record.failure_code for record in failures if record.failure_code is not None
        )
        provider_failures = statuses[TerminalStatus.PROVIDER_FAILURE.value]
        invalid_schema = statuses[TerminalStatus.INVALID_SCHEMA.value]
        result[architecture.value] = {
            "terminal_failure_count": len(failures),
            "terminal_failure_rate": ratio(len(failures), len(items)),
            "status_counts": dict(sorted(statuses.items())),
            "failure_code_counts": dict(sorted(failure_codes.items())),
            "invalid_schema_count": invalid_schema,
            "provider_failure_count": provider_failures,
            "other_terminal_failure_count": (
                len(failures) - invalid_schema - provider_failures
            ),
            "total_model_calls": sum(record.model_calls for record in items),
            "mean_model_calls": mean(record.model_calls for record in items),
            "total_tool_calls": sum(record.tool_calls for record in items),
            "mean_tool_calls": mean(record.tool_calls for record in items),
        }
    result["causal_boundary"] = (
        "Provider operation count and run-level failure rate are co-observed; "
        "causality and the exact failing operation are not identified."
    )
    result["exact_provider_failure_stage"] = UNOBSERVABLE
    result["exact_schema_field_failure"] = UNOBSERVABLE
    return result


def _citation_counts(record: TerminalRecord) -> dict[str, int]:
    counts = {source: 0 for source in _SOURCES}
    if record.diagnosis is None:
        return counts
    for reference in record.diagnosis.evidence_refs:
        prefix = reference.split(":", 1)[0]
        source = {"metric": "metrics", "log": "logs", "trace": "traces"}[prefix]
        counts[source] += 1
    return counts


def build_citation_behavior(
    records: Sequence[TerminalRecord], truth: Mapping[str, GroundTruth]
) -> dict[str, Any]:
    scored = _scored_lookup(records, truth)
    result: dict[str, Any] = {}
    for architecture in _ARCHITECTURES:
        by_outcome: dict[str, Any] = {}
        for label, desired in (("correct", True), ("incorrect", False)):
            items = [
                record
                for record in records
                if record.architecture is architecture
                and record.terminal_status is TerminalStatus.COMPLETED
                and scored[(record.case_id, architecture)][0] is desired
            ]
            buckets: Counter[str] = Counter()
            evidence_counts: list[int] = []
            for record in items:
                citations = _citation_counts(record)
                used = [source for source, count in citations.items() if count]
                bucket = used[0] if len(used) == 1 else "multiple_sources"
                buckets[bucket] += 1
                assert record.diagnosis is not None
                evidence_counts.append(len(record.diagnosis.evidence_refs))
            by_outcome[label] = {
                "completed_runs": len(items),
                "citation_bucket_counts": dict(sorted(buckets.items())),
                "evidence_ref_count": _distribution(evidence_counts),
            }
        result[architecture.value] = by_outcome
    result["interpretation_boundary"] = (
        "These are final Diagnosis citations, not proof of every queried source."
    )
    return result


def _case_projection_coverage(projection: Mapping[str, Any]) -> tuple[bool, int]:
    present = 0
    for source in _SOURCES:
        if projection[source]["coverage_at_6"] is True:
            present += 1
    return present > 0, present


def build_case_rows(
    records: Sequence[TerminalRecord],
    truth: Mapping[str, GroundTruth],
    projections: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped = _records_by_case(records)
    scored = _scored_lookup(records, truth)
    rows: list[dict[str, Any]] = []
    for case_id in sorted(grouped):
        arms = grouped[case_id]
        case_truth = truth[case_id]
        projection = projections[case_id]
        flags: dict[str, Any] = {}
        for architecture in _ARCHITECTURES:
            record = arms[architecture]
            service_correct, pair_correct = scored[(case_id, architecture)]
            flags[f"{architecture.value}_correct"] = service_correct
            flags[f"{architecture.value}_completed"] = (
                record.terminal_status is TerminalStatus.COMPLETED
            )
            flags[f"{architecture.value}_pair_correct"] = pair_correct
        any_coverage, source_coverage_count = _case_projection_coverage(projection)
        for architecture in _ARCHITECTURES:
            record = arms[architecture]
            service_correct, pair_correct = scored[(case_id, architecture)]
            citations = _citation_counts(record)
            if service_correct:
                failure_attribution = "ROOT_SERVICE_CORRECT"
            elif record.terminal_status is not TerminalStatus.COMPLETED:
                failure_attribution = "TERMINAL_FAILURE"
            elif not any_coverage:
                failure_attribution = "TOOL_PROJECTION_COVERAGE_FAILURE"
            elif architecture is Architecture.DYNAMIC:
                failure_attribution = "ROUTE_OR_REASONING_UNRESOLVED"
            else:
                failure_attribution = "REASONING_OR_FUSION_FAILURE"
            diagnosis = record.diagnosis
            row = {
                "case_id": case_id,
                "architecture": architecture.value,
                "terminal_status": record.terminal_status.value,
                "failure_code": record.failure_code,
                "root_service_correct": service_correct,
                "root_cause_pair_correct": pair_correct,
                "predicted_service": (
                    diagnosis.root_cause_service if diagnosis is not None else None
                ),
                "predicted_indicator": (
                    diagnosis.root_cause_indicator if diagnosis is not None else None
                ),
                "truth_service": case_truth.root_cause_service,
                "truth_fault": case_truth.fault,
                "truth_indicator": normalize_indicator(case_truth.fault),
                "tool_calls": record.tool_calls,
                "model_calls": record.model_calls,
                "known_provider_tokens": record.known_provider_tokens,
                "latency_seconds": record.latency_seconds,
                "evidence_ref_count": (
                    len(diagnosis.evidence_refs) if diagnosis is not None else 0
                ),
                "cited_metric_count": citations["metrics"],
                "cited_log_count": citations["logs"],
                "cited_trace_count": citations["traces"],
                **flags,
                "metrics_truth_service_rank": projection["metrics"][
                    "truth_service_rank"
                ],
                "logs_truth_service_rank": projection["logs"]["truth_service_rank"],
                "traces_truth_service_rank": projection["traces"][
                    "truth_service_rank"
                ],
                "truth_service_present_in_any_top6": any_coverage,
                "truth_service_top6_source_count": source_coverage_count,
                "truth_indicator_raw_present": projection["indicator"]["raw_present"],
                "truth_indicator_top6_present": projection["indicator"][
                    "top6_present"
                ],
                "root_service_failure_attribution": failure_attribution,
                "dynamic_exact_skipped_source": (
                    UNOBSERVABLE if architecture is Architecture.DYNAMIC else None
                ),
                "deterministic_projection": projection,
            }
            rows.append(row)
    return rows


def build_tool_coverage(
    projections: Mapping[str, Mapping[str, Any]],
    case_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total = len(projections)
    result: dict[str, Any] = {}
    for source in _SOURCES:
        available = [
            projection[source]
            for projection in projections.values()
            if projection[source]["status"] == "AVAILABLE"
        ]
        if not available:
            result[source] = {
                "status": "SOURCE_UNAVAILABLE",
                "case_count": total,
            }
            continue
        result[source] = {
            "status": "AVAILABLE",
            "coverage_at_1": ratio(
                sum(item["coverage_at_1"] is True for item in available),
                len(available),
            ),
            "coverage_at_3": ratio(
                sum(item["coverage_at_3"] is True for item in available),
                len(available),
            ),
            "coverage_at_6": ratio(
                sum(item["coverage_at_6"] is True for item in available),
                len(available),
            ),
            "unique_service_count": _distribution(
                [item["unique_service_count"] for item in available]
            ),
            "top_1_top_2_score_margin": _distribution(
                [
                    item["top_1_top_2_score_margin"]
                    for item in available
                    if item["top_1_top_2_score_margin"] is not None
                ]
            ),
        }
    any_count = sum(
        _case_projection_coverage(projection)[0]
        for projection in projections.values()
    )
    result["any_source_coverage_at_6"] = ratio(any_count, total)
    result["all_source_absence_rate"] = ratio(total - any_count, total)
    fixed_incorrect = [
        row
        for row in case_rows
        if row["architecture"] == Architecture.FIXED.value
        and not row["root_service_correct"]
    ]
    fixed_terminal_failures = [
        row
        for row in fixed_incorrect
        if row["terminal_status"] != TerminalStatus.COMPLETED.value
    ]
    fixed_completed_wrong = [
        row
        for row in fixed_incorrect
        if row["terminal_status"] == TerminalStatus.COMPLETED.value
    ]
    absent = sum(
        not row["truth_service_present_in_any_top6"]
        for row in fixed_completed_wrong
    )
    multiple = sum(
        row["truth_service_top6_source_count"] >= 2
        for row in fixed_completed_wrong
    )
    result["fixed_completed_wrong_attribution"] = {
        "all_root_service_incorrect_runs": len(fixed_incorrect),
        "completed_wrong_runs": len(fixed_completed_wrong),
        "terminal_failure_runs": len(fixed_terminal_failures),
        "truth_service_absent_from_all_three": absent,
        "truth_service_present_in_at_least_one": len(fixed_completed_wrong) - absent,
        "truth_service_present_in_multiple": multiple,
        "coverage_failure_rate": ratio(absent, len(fixed_completed_wrong)),
        "visible_but_wrong_rate": ratio(
            len(fixed_completed_wrong) - absent, len(fixed_completed_wrong)
        ),
    }
    return result


def build_dynamic_routing(
    records: Sequence[TerminalRecord], truth: Mapping[str, GroundTruth]
) -> dict[str, Any]:
    grouped = _records_by_case(records)
    scored = _scored_lookup(records, truth)
    dynamic = [
        record for record in records if record.architecture is Architecture.DYNAMIC
    ]
    single = [
        record for record in records if record.architecture is Architecture.SINGLE
    ]
    distribution = Counter(record.tool_calls for record in dynamic)
    other = sum(count for calls, count in distribution.items() if calls not in {2, 3})
    subgroup: dict[str, Any] = {}
    for calls in sorted(set(distribution) | {2, 3}):
        items = [record for record in dynamic if record.tool_calls == calls]
        correct = sum(
            scored[(record.case_id, Architecture.DYNAMIC)][0] for record in items
        )
        failures = sum(
            record.terminal_status is not TerminalStatus.COMPLETED for record in items
        )
        subgroup[str(calls)] = {
            "runs": len(items),
            "root_service_accuracy": (
                ratio(correct, len(items)) if items else None
            ),
            "terminal_failure_rate": (
                ratio(failures, len(items)) if items else None
            ),
            "classification": "DESCRIPTIVE_ONLY",
        }
    paired_reductions = []
    for arms in grouped.values():
        single_calls = arms[Architecture.SINGLE].tool_calls
        dynamic_calls = arms[Architecture.DYNAMIC].tool_calls
        paired_reductions.append((single_calls - dynamic_calls) / single_calls)
    single_total = sum(record.tool_calls for record in single)
    dynamic_total = sum(record.tool_calls for record in dynamic)
    three_count = distribution[3]
    verdict = (
        "DYNAMIC_ROUTE_DEGENERACY_SUPPORTED"
        if three_count / len(dynamic) >= 0.75
        else "DYNAMIC_ROUTE_DEGENERACY_NOT_SUPPORTED"
    )
    return {
        "total_tool_calls": dynamic_total,
        "tool_call_distribution": {
            "2": distribution[2],
            "3": three_count,
            "other": other,
        },
        "all_three_sources_rate": ratio(three_count, len(dynamic)),
        "mean_tool_reduction_vs_single": ratio(
            single_total - dynamic_total, single_total
        ),
        "median_paired_tool_reduction_vs_single": {
            "paired_case_count": len(paired_reductions),
            "value": median(paired_reductions),
        },
        "route_subgroups": subgroup,
        "exact_skipped_source": UNOBSERVABLE,
        "verdict": verdict,
        "interpretation_boundary": (
            "The verdict concerns source-acquisition reduction under this frozen "
            "routing policy and distribution only."
        ),
    }


def build_indicator_pipeline(
    records: Sequence[TerminalRecord],
    truth: Mapping[str, GroundTruth],
    projections: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    grouped = _records_by_case(records)
    by_fault: dict[str, Any] = {}
    for fault in ("cpu", "mem", "disk", "delay", "loss", "socket"):
        case_ids = [case_id for case_id, item in truth.items() if item.fault == fault]
        raw_count = sum(projections[case_id]["indicator"]["raw_present"] for case_id in case_ids)
        top6_count = sum(
            projections[case_id]["indicator"]["top6_present"] for case_id in case_ids
        )
        final: dict[str, Any] = {}
        selection_distribution: dict[str, dict[str, int]] = {}
        for architecture in _ARCHITECTURES:
            selected: Counter[str] = Counter()
            projected_completed = 0
            projected_final_correct = 0
            for case_id in case_ids:
                record = grouped[case_id][architecture]
                if record.diagnosis is None:
                    selected["NO_DIAGNOSIS"] += 1
                else:
                    selected[record.diagnosis.root_cause_indicator] += 1
                if (
                    projections[case_id]["indicator"]["top6_present"]
                    and record.terminal_status is TerminalStatus.COMPLETED
                ):
                    projected_completed += 1
                    assert record.diagnosis is not None
                    if record.diagnosis.root_cause_indicator == normalize_indicator(
                        truth[case_id].fault
                    ):
                        projected_final_correct += 1
            final[architecture.value] = {
                "projected_and_completed_runs": projected_completed,
                "final_indicator_correct": projected_final_correct,
                "final_selection_accuracy_given_projected_and_completed": (
                    ratio(projected_final_correct, projected_completed)
                    if projected_completed
                    else None
                ),
            }
            selection_distribution[architecture.value] = dict(sorted(selected.items()))
        by_fault[fault] = {
            "truth_indicator": normalize_indicator(truth[case_ids[0]].fault),
            "cases": len(case_ids),
            "raw_schema_coverage": ratio(raw_count, len(case_ids)),
            "top6_projection_coverage": ratio(top6_count, len(case_ids)),
            "final_reasoning": final,
            "final_selection_distribution": selection_distribution,
        }

    confusion: dict[str, Any] = {}
    for architecture in _ARCHITECTURES:
        matrix: dict[str, Counter[str]] = defaultdict(Counter)
        for case_id, arms in grouped.items():
            truth_indicator = normalize_indicator(truth[case_id].fault)
            diagnosis = arms[architecture].diagnosis
            predicted = (
                diagnosis.root_cause_indicator if diagnosis is not None else "NO_DIAGNOSIS"
            )
            matrix[truth_indicator][predicted] += 1
        confusion[architecture.value] = {
            indicator: {
                predicted: matrix[indicator][predicted]
                for predicted in (*_INDICATORS, "NO_DIAGNOSIS")
            }
            for indicator in _INDICATORS
        }

    special: dict[str, Any] = {}
    for fault in ("mem", "socket"):
        item = by_fault[fault]
        raw_value = item["raw_schema_coverage"]["value"]
        projected_value = item["top6_projection_coverage"]["value"]
        final_total = sum(
            item["final_reasoning"][architecture.value]["final_indicator_correct"]
            for architecture in _ARCHITECTURES
        )
        if raw_value == 0:
            classification = "RAW_SIGNAL_GAP"
        elif projected_value == 0:
            classification = "TOOL_RANKING_GAP"
        elif raw_value < 1 or projected_value < raw_value:
            classification = "MIXED"
        elif final_total == 0:
            classification = "MODEL_INDICATOR_REASONING_GAP"
        else:
            classification = "MIXED"
        special[fault] = {
            "classification": classification,
            "raw_schema_coverage": item["raw_schema_coverage"],
            "top6_projection_coverage": item["top6_projection_coverage"],
            "final_selection_distribution": item["final_selection_distribution"],
        }
    return {
        "fault_funnels": by_fault,
        "memory_and_socket": special,
        "predicted_indicator_confusion_matrix": confusion,
    }


def _column_value(row: Mapping[str, str], names: Sequence[str]) -> str | None:
    lowered = {key.casefold(): key for key in row}
    for name in names:
        key = lowered.get(name.casefold())
        if key is not None:
            return row.get(key)
    return None


def _normalized_timestamp(raw: str | None) -> float:
    if raw is None:
        raise ValueError("telemetry timestamp is absent")
    value = float(raw)
    if value >= 1e17:
        return value / 1e9
    if value >= 1e14:
        return value / 1e6
    if value >= 1e11:
        return value / 1e3
    return value


def _csv_window_stats(
    path: Path,
    *,
    inject_time: int,
    timestamp_names: Sequence[str],
    service_names: Sequence[str],
) -> tuple[int, int]:
    rows = 0
    services: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("telemetry CSV has no header")
        for row in reader:
            timestamp = _normalized_timestamp(_column_value(row, timestamp_names))
            if inject_time - 600 <= timestamp <= inject_time + 600:
                rows += 1
                service = _column_value(row, service_names)
                if service:
                    services.add(service)
    return rows, len(services)


def _raw_complexity(case: TelemetryCase) -> dict[str, Any]:
    metric_columns = _metric_columns(case.metrics_path)
    metric_services = {
        name.rsplit("_", 1)[0] for name in metric_columns if "_" in name
    }
    log_rows, log_services = _csv_window_stats(
        case.logs_path,
        inject_time=case.inject_time,
        timestamp_names=("timestamp", "time"),
        service_names=("service", "serviceName", "container_name"),
    )
    if case.traces_path is None:
        trace_rows: int | None = None
        trace_services: int | None = None
    else:
        trace_rows, trace_services = _csv_window_stats(
            case.traces_path,
            inject_time=case.inject_time,
            timestamp_names=(
                "startTimeMillis",
                "startTime",
                "start_time",
                "timestamp",
                "time",
            ),
            service_names=("service", "serviceName"),
        )
    return {
        "services": len(metric_services),
        "metric_columns": len(metric_columns),
        "log_rows_in_window": log_rows,
        "trace_rows_in_window": trace_rows,
        "unique_log_services": log_services,
        "unique_trace_services": trace_services,
    }


def _case_statistics_job(
    job: tuple[TelemetryCase, str, CanonicalIndicator],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    case, truth_service, truth_indicator = job
    return (
        case.case_id,
        deterministic_projection(
            case,
            truth_service=truth_service,
            truth_indicator=truth_indicator,
        ),
        _raw_complexity(case),
    )


def _project_cases(
    cases: Sequence[TelemetryCase],
    truth_by_case: Mapping[str, tuple[str, CanonicalIndicator]],
    *,
    workers: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if workers <= 0:
        raise ValueError("attribution workers must be positive")
    jobs = [
        (case, truth_by_case[case.case_id][0], truth_by_case[case.case_id][1])
        for case in cases
    ]
    if workers == 1:
        results = map(_case_statistics_job, jobs)
        materialized = list(results)
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            materialized = list(executor.map(_case_statistics_job, jobs, chunksize=1))
    projections = {case_id: projection for case_id, projection, _ in materialized}
    complexities = {case_id: complexity for case_id, _, complexity in materialized}
    return projections, complexities


def _cross_system_item(
    cases: Sequence[TelemetryCase],
    truth_by_case: Mapping[str, tuple[str, CanonicalIndicator]],
    *,
    existing_projections: Mapping[str, Mapping[str, Any]] | None = None,
    existing_complexities: Mapping[str, Mapping[str, Any]] | None = None,
    workers: int = 1,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if existing_projections is None:
        projections, complexity_by_case = _project_cases(
            cases, truth_by_case, workers=workers
        )
    else:
        projections = {
            case_id: dict(projection)
            for case_id, projection in existing_projections.items()
        }
        if existing_complexities is None:
            complexity_by_case = {
                case.case_id: _raw_complexity(case) for case in cases
            }
        else:
            complexity_by_case = {
                case_id: dict(complexity)
                for case_id, complexity in existing_complexities.items()
            }
    complexities = [complexity_by_case[case.case_id] for case in cases]
    agreements = 0
    agreement_denominator = 0
    for case in cases:
        projection = projections[case.case_id]
        top_services = [
            projection[source]["top_1_service"]
            for source in _SOURCES
            if projection[source]["status"] == "AVAILABLE"
            and projection[source]["top_1_service"] is not None
        ]
        if len(top_services) >= 2:
            agreement_denominator += 1
            agreements += len(set(top_services)) == 1
    total = len(cases)
    coverage: dict[str, Any] = {}
    for source in _SOURCES:
        available = [
            projection[source]
            for projection in projections.values()
            if projection[source]["status"] == "AVAILABLE"
        ]
        coverage[source] = (
            {
                "status": "AVAILABLE",
                "root_service_coverage_at_6": ratio(
                    sum(item["coverage_at_6"] is True for item in available),
                    len(available),
                ),
            }
            if available
            else {"status": "SOURCE_UNAVAILABLE", "case_count": total}
        )
    any_coverage = sum(
        _case_projection_coverage(projection)[0]
        for projection in projections.values()
    )
    raw_indicator = sum(
        projection["indicator"]["raw_present"] for projection in projections.values()
    )
    top6_indicator = sum(
        projection["indicator"]["top6_present"] for projection in projections.values()
    )
    metrics_margins = [
        projection["metrics"]["top_1_top_2_score_margin"]
        for projection in projections.values()
        if projection["metrics"]["top_1_top_2_score_margin"] is not None
    ]
    trace_rows = [
        item["trace_rows_in_window"]
        for item in complexities
        if item["trace_rows_in_window"] is not None
    ]
    trace_services = [
        item["unique_trace_services"]
        for item in complexities
        if item["unique_trace_services"] is not None
    ]
    trace_rows_distribution: dict[str, Any]
    if trace_rows:
        trace_rows_distribution = _distribution(trace_rows)
    else:
        trace_rows_distribution = {
            "status": "SOURCE_UNAVAILABLE",
            "case_count": total,
        }
    trace_services_distribution: dict[str, Any]
    if trace_services:
        trace_services_distribution = _distribution(trace_services)
    else:
        trace_services_distribution = {
            "status": "SOURCE_UNAVAILABLE",
            "case_count": total,
        }
    aggregate: dict[str, Any] = {
        "case_count": total,
        "services_per_case": _distribution([item["services"] for item in complexities]),
        "metric_columns_per_case": _distribution(
            [item["metric_columns"] for item in complexities]
        ),
        "log_rows_in_window": _distribution(
            [item["log_rows_in_window"] for item in complexities]
        ),
        "trace_rows_in_window": trace_rows_distribution,
        "unique_log_services": _distribution(
            [item["unique_log_services"] for item in complexities]
        ),
        "unique_trace_services": trace_services_distribution,
        "root_service_coverage": coverage,
        "any_source_root_service_coverage_at_6": ratio(any_coverage, total),
        "truth_indicator_raw_coverage": ratio(raw_indicator, total),
        "truth_indicator_top6_coverage": ratio(top6_indicator, total),
        "metrics_top_1_top_2_margin": _distribution(metrics_margins),
        "cross_source_top_service_agreement": ratio(
            agreements, agreement_denominator
        ),
    }
    return aggregate, projections


def _build_observability_audit(source_bindings: Mapping[str, str]) -> dict[str, Any]:
    terminal_fields = (
        "run_id",
        "case_id",
        "architecture",
        "terminal_status",
        "diagnosis",
        "failure_code",
        "tool_calls",
        "model_calls",
        "known_provider_tokens",
        "latency_seconds",
    )
    absent = {
        "metrics_specialist_assessment": False,
        "logs_specialist_assessment": False,
        "traces_specialist_assessment": False,
        "commander_decision": False,
        "judge_input_context": False,
        "raw_provider_response": False,
        "provider_operation_name": False,
        "per_provider_call_independent_status": False,
    }
    return {
        "schema_version": "rcaeval-re2.post-hoc-observability-audit.v1",
        "classification": list(CLASSIFICATION),
        "source_bindings": dict(source_bindings),
        "terminal_record_fields": {field: True for field in terminal_fields},
        "persisted_intermediate_outputs": absent,
        "conclusions": {
            "specialist_candidate_accuracy": UNOBSERVABLE,
            "judge_follow_rate": UNOBSERVABLE,
            "exact_provider_failure_stage": UNOBSERVABLE,
            "exact_schema_field_failure": UNOBSERVABLE,
        },
        "evidence_levels": {
            "LEVEL_1_DIRECT": (
                "Frozen Terminal Records, locks, attempts, and Final Report."
            ),
            "LEVEL_2_DETERMINISTIC_RECONSTRUCTION": (
                "Frozen Sanitized Telemetry through the frozen top-6 tool code."
            ),
            "LEVEL_3_INDIRECT_INFERENCE": (
                "Multiple aggregate facts without observable internal model state."
            ),
            "UNOBSERVABLE": "The frozen artifacts do not identify the mechanism.",
        },
    }


def _hypothesis_matrix(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    failures = aggregate["terminal_failures"]
    pairwise = aggregate["pairwise_outcomes"]
    routing = aggregate["dynamic_routing"]
    coverage = aggregate["tool_coverage"]
    indicator = aggregate["indicator_pipeline"]
    cross = aggregate["cross_system_deterministic_comparison"]
    architecture = aggregate["architecture_decomposition"]
    same_source = pairwise["same_source_set_semantic_degradation"]["count"]
    fixed_rescues = pairwise["simplified"]["single_wrong_fixed_correct"]
    dynamic_rescues = pairwise["simplified"]["single_wrong_dynamic_correct"]
    fixed_absent = coverage["fixed_completed_wrong_attribution"][
        "truth_service_absent_from_all_three"
    ]
    fixed_visible = coverage["fixed_completed_wrong_attribution"][
        "truth_service_present_in_at_least_one"
    ]
    tt_any = cross["RE2-TT"]["any_source_root_service_coverage_at_6"]["value"]
    ob_any = cross["RE2-OB"]["any_source_root_service_coverage_at_6"]["value"]
    ss_any = cross["RE2-SS"]["any_source_root_service_coverage_at_6"]["value"]
    h6_result = "supported" if tt_any + 0.05 < min(ob_any, ss_any) else "mixed"
    return {
        "H1": {
            "hypothesis": "Multi-call reliability amplification",
            "result": "supported",
            "confidence": "high",
            "evidence_level": "LEVEL_1_DIRECT",
            "supporting_observations": [
                (
                    "Fixed and Dynamic used more model calls and had "
                    f"{failures['fixed']['terminal_failure_count']} and "
                    f"{failures['dynamic']['terminal_failure_count']} failures, versus "
                    f"{failures['single']['terminal_failure_count']} for Single."
                )
            ],
            "contradicting_observations": [
                "The frozen artifacts do not expose a per-operation hazard rate."
            ],
            "what_cannot_be_concluded": (
                "More calls are not proven causal, and the failing operation is unobservable."
            ),
            "recommended_next_experiment": (
                "Persist per-operation status and estimate transport/schema hazard by stage "
                "on development data."
            ),
        },
        "H2": {
            "hypothesis": "Same-source Multi-Agent semantic degradation",
            "result": "supported" if same_source else "not_supported",
            "confidence": "high" if same_source else "medium",
            "evidence_level": (
                "LEVEL_1_DIRECT + LEVEL_2_DETERMINISTIC_RECONSTRUCTION"
            ),
            "supporting_observations": [
                f"{same_source} both-completed outcomes were Single-correct and Fixed-wrong."
            ],
            "contradicting_observations": [
                f"Fixed recovered {fixed_rescues} Single-wrong outcomes."
            ],
            "what_cannot_be_concluded": (
                "Specialist anchoring, Judge anchoring, context redundancy, and label bias "
                "cannot be separated."
            ),
            "recommended_next_experiment": (
                "Persist intermediate assessments and run architecture-blind Judge ablations "
                "on development data."
            ),
        },
        "H3": {
            "hypothesis": "Dynamic route degeneracy",
            "result": (
                "supported"
                if routing["verdict"] == "DYNAMIC_ROUTE_DEGENERACY_SUPPORTED"
                else "not_supported"
            ),
            "confidence": "high",
            "evidence_level": "LEVEL_1_DIRECT",
            "supporting_observations": [
                f"{routing['tool_call_distribution']['3']} of 90 Dynamic runs used three tools."
            ],
            "contradicting_observations": [
                f"{routing['tool_call_distribution']['other']} runs used one tool, but all were terminal failures; no two-tool route was observed."
            ],
            "what_cannot_be_concluded": (
                "The exact skipped source and the counterfactual accuracy of another route "
                "are unobservable."
            ),
            "recommended_next_experiment": (
                "Persist Commander decisions and evaluate truly sequential routes on development data."
            ),
        },
        "H4": {
            "hypothesis": "Root-service tool projection misses",
            "result": "mixed" if fixed_absent and fixed_visible else (
                "supported" if fixed_absent else "not_supported"
            ),
            "confidence": "high",
            "evidence_level": "LEVEL_2_DETERMINISTIC_RECONSTRUCTION",
            "supporting_observations": (
                [
                    f"{fixed_absent} Fixed completed-wrong runs lacked the truth service in all top-6 projections."
                ]
                if fixed_absent
                else []
            ),
            "contradicting_observations": [
                f"{fixed_visible} Fixed completed-wrong runs had the truth service visible in at least one projection."
            ],
            "what_cannot_be_concluded": (
                "Visibility does not prove the model attended to or correctly interpreted the evidence."
            ),
            "recommended_next_experiment": (
                "Measure source-specific recall and feed ranked supporting and contradicting evidence."
            ),
        },
        "H5": {
            "hypothesis": "Indicator pipeline failure",
            "result": "supported",
            "confidence": "high",
            "evidence_level": "LEVEL_2_DETERMINISTIC_RECONSTRUCTION",
            "supporting_observations": [
                (
                    "Memory classification is "
                    f"{indicator['memory_and_socket']['mem']['classification']}; Socket "
                    f"classification is {indicator['memory_and_socket']['socket']['classification']}."
                )
            ],
            "contradicting_observations": [
                "Other fault families retain non-zero pair accuracy."
            ],
            "what_cannot_be_concluded": (
                "No Provider-internal reasoning trace identifies why a visible indicator was rejected."
            ),
            "recommended_next_experiment": (
                "Add deterministic metric-to-indicator candidates and test the full raw/top-6/final funnel."
            ),
        },
        "H6": {
            "hypothesis": "Cross-system tool distribution shift",
            "result": h6_result,
            "confidence": "medium",
            "evidence_level": "LEVEL_2_DETERMINISTIC_RECONSTRUCTION",
            "supporting_observations": [
                (
                    "Truth-indicator Metrics top-6 coverage is "
                    f"TT={cross['RE2-TT']['truth_indicator_top6_coverage']['value']}, "
                    f"OB={cross['RE2-OB']['truth_indicator_top6_coverage']['value']}, "
                    f"SS={cross['RE2-SS']['truth_indicator_top6_coverage']['value']}."
                )
            ],
            "contradicting_observations": [
                f"Any-source root-service Coverage@6 is TT={tt_any}, OB={ob_any}, SS={ss_any}; system schemas and trace availability also differ."
            ],
            "what_cannot_be_concluded": (
                "No full-protocol OB/SS Provider accuracy exists, so performance-point impact is unknown."
            ),
            "recommended_next_experiment": (
                "Create a development-only semantic evaluation shared across OB, SS, and TT-like cases."
            ),
        },
        "H7": {
            "hypothesis": "Specialist-to-Judge anchoring",
            "result": "unobservable",
            "confidence": "low",
            "evidence_level": "UNOBSERVABLE",
            "supporting_observations": [
                "Same-source semantic degradation is compatible with anchoring."
            ],
            "contradicting_observations": [
                "No frozen SpecialistAssessment or Judge input is persisted."
            ],
            "what_cannot_be_concluded": UNOBSERVABLE,
            "recommended_next_experiment": (
                "Persist SpecialistAssessment, CommanderDecision, and exact Judge input."
            ),
        },
        "H8": {
            "hypothesis": "Architecture-aware Judge bias",
            "result": "unobservable",
            "confidence": "none",
            "evidence_level": "UNOBSERVABLE",
            "supporting_observations": [],
            "contradicting_observations": [
                "No architecture-blind ablation is part of the frozen run."
            ],
            "what_cannot_be_concluded": UNOBSERVABLE,
            "recommended_next_experiment": (
                "Blind architecture labels while holding evidence and prompt content constant."
            ),
        },
        "H9": {
            "hypothesis": "Model capability is the primary bottleneck",
            "result": "mixed",
            "confidence": "medium",
            "evidence_level": "LEVEL_3_INDIRECT_INFERENCE",
            "supporting_observations": [
                "Single pair accuracy remains materially below its root-service accuracy."
            ],
            "contradicting_observations": [
                (
                    "Single root-service correctness is "
                    f"{architecture['single']['root_service_accuracy']['numerator']}/90, "
                    "while both Multi-Agent arms are lower; architecture therefore adds a "
                    "separate observed degradation."
                ),
                f"Dynamic recovered {dynamic_rescues} Single-wrong outcomes but damaged more Single-correct outcomes.",
            ],
            "what_cannot_be_concluded": (
                "The frozen comparison does not isolate model capacity from prompting and architecture."
            ),
            "recommended_next_experiment": (
                "Use development-only controlled prompts with identical evidence and architecture-blind scoring."
            ),
        },
    }


def _recommendations() -> list[dict[str, str]]:
    return [
        {
            "priority": "P0",
            "recommendation": (
                "Persist SpecialistAssessment, CommanderDecision, Judge inputs, and per-operation status."
            ),
            "observed_failure_mechanism": (
                "Internal stage attribution is currently unobservable despite elevated Multi-Agent failures."
            ),
            "expected_benefit": "Makes stage-specific reliability and anchoring hypotheses directly testable.",
            "risk": "More restricted review storage increases leakage-control obligations.",
            "required_development_set_test": (
                "Hash-bound replay verifies complete stage records without Agent-visible truth leakage."
            ),
            "new_external_holdout_required": "No for instrumentation validation; yes for final performance claims.",
        },
        {
            "priority": "P0",
            "recommendation": "Repair and directly test the metric indicator pipeline.",
            "observed_failure_mechanism": (
                "The raw/top-6/final funnel and zero Memory/Socket pair scores expose indicator loss."
            ),
            "expected_benefit": "Raises pair accuracy without changing root-service selection.",
            "risk": "Rule-based mappings may overfit benchmark naming conventions.",
            "required_development_set_test": (
                "Per-fault raw coverage, top-6 coverage, confusion, and unseen-name robustness."
            ),
            "new_external_holdout_required": "Yes before any new external pair-accuracy claim.",
        },
        {
            "priority": "P1",
            "recommendation": (
                "Use Single-first adaptive escalation with Metrics-only termination and truly sequential routing."
            ),
            "observed_failure_mechanism": (
                "Most Dynamic runs acquired all three sources while Multi-stage semantics damaged Single-correct cases."
            ),
            "expected_benefit": "Protects strong Single outcomes and spends extra calls only on uncertainty.",
            "risk": "An incorrect confidence gate may suppress useful escalation.",
            "required_development_set_test": (
                "Calibrated escalation precision/recall, route counts, accuracy, and failure accounting."
            ),
            "new_external_holdout_required": "Yes for architecture superiority or cost-quality claims.",
        },
        {
            "priority": "P1",
            "recommendation": (
                "Provide top-k hypotheses with supporting/contradicting evidence and an architecture-blind Judge."
            ),
            "observed_failure_mechanism": (
                "Truth services were often visible when Fixed still selected the wrong service."
            ),
            "expected_benefit": "Reduces premature commitment and tests whether labels influence fusion.",
            "risk": "Larger structured context may increase cost and schema failures.",
            "required_development_set_test": (
                "Same-evidence blind ablation with rescue/damage pairwise accounting."
            ),
            "new_external_holdout_required": "Yes for final architecture claims.",
        },
        {
            "priority": "P2",
            "recommendation": (
                "Replace homogeneous model-only specialists with algorithmic metric, log-delta, and trace-root rankers."
            ),
            "observed_failure_mechanism": (
                "Source projection and final fusion failures require source-specific, testable hypotheses."
            ),
            "expected_benefit": "Adds genuinely heterogeneous signals and deterministic failure localization.",
            "risk": "Specialist heuristics may encode dataset-specific assumptions.",
            "required_development_set_test": (
                "Source-isolated recall, calibration, causal-role labels, and cross-system robustness."
            ),
            "new_external_holdout_required": "Yes after development selection is frozen.",
        },
        {
            "priority": "P3",
            "recommendation": (
                "Adopt strict structured output where supported, transport-only retry under a new protocol, "
                "parallel Fixed specialists, and reduced repeated context."
            ),
            "observed_failure_mechanism": (
                "Multi-Agent arms amplify Provider operations, latency, tokens, and terminal failures."
            ),
            "expected_benefit": "Improves run reliability and cost without hiding semantic failures.",
            "risk": "Retries change the estimand and parallelism can create new rate-limit behavior.",
            "required_development_set_test": (
                "Per-operation failure, retry disposition, latency, token, and schema accounting."
            ),
            "new_external_holdout_required": "Yes because retry and execution semantics define a new protocol.",
        },
    ]


def _format_ratio(item: Mapping[str, int | float]) -> str:
    return f"{item['numerator']}/{item['denominator']} ({float(item['value']):.4f})"


def render_summary(aggregate: Mapping[str, Any]) -> str:
    architecture = aggregate["architecture_decomposition"]
    failures = aggregate["terminal_failures"]
    pairwise = aggregate["pairwise_outcomes"]
    routing = aggregate["dynamic_routing"]
    indicator = aggregate["indicator_pipeline"]
    cross = aggregate["cross_system_deterministic_comparison"]
    hypotheses = aggregate["hypothesis_matrix"]
    fixed_vs = architecture["fixed"]["vs_single"]
    dynamic_vs = architecture["dynamic"]["vs_single"]
    same_source = pairwise["same_source_set_semantic_degradation"]
    recommendations = aggregate["recommended_next_experiments"]
    lines = [
        "# RCAEval RE2 v1 frozen-result post-hoc attribution",
        "",
        "> **POST_HOC_EXPLORATORY · NOT_PRIMARY_INFERENCE · NO_HOLDOUT_RERUN**",
        "",
        "This report attributes the already frozen negative result. It made no Provider calls, "
        "did not rerun or retry RE2-TT, and did not change prompts, models, tools, records, "
        "locks, the Final Report, or the primary inference.",
        "",
        "## Frozen result remains unchanged",
        "",
        "- Dynamic − Single Root Service AC@1: **-0.1889**.",
        "- Frozen 95% CI: **[-0.2889, -0.0889]**.",
        "- Primary superiority supported: **No**.",
        "- Cost-quality supported: **No**.",
        "- Frozen records / attempt markers: **270 / 270**; attribution Provider calls: **0**.",
        "",
        "## Architecture decomposition",
        "",
        "| Architecture | Root service | Pair | Completed-only service | Failures | Reliability ceiling* |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("single", "fixed", "dynamic"):
        item = architecture[name]
        lines.append(
            f"| {name.title()} | {_format_ratio(item['root_service_accuracy'])} | "
            f"{_format_ratio(item['root_cause_pair_accuracy'])} | "
            f"{_format_ratio(item['completed_only_root_service_accuracy'])} | "
            f"{item['terminal_failures']} | "
            f"{_format_ratio(item['reliability_ceiling_if_all_failures_correct'])} |"
        )
    lines.extend(
        [
            "",
            "* Exploratory upper bound that assumes every terminal failure becomes correct; it is not model performance.",
            "",
            f"Fixed's 17-case correct-count gap decomposes into {fixed_vs['excess_terminal_failures']} excess terminal failures plus {fixed_vs['excess_completed_wrong']} excess completed-but-wrong outcomes. "
            f"Dynamic's 17-case gap decomposes into {dynamic_vs['excess_terminal_failures']} plus {dynamic_vs['excess_completed_wrong']}. Both identities reconcile exactly.",
            "",
            "## Six required questions",
            "",
            "### Q1. How much of the negative result comes from Terminal Failure?",
            "",
            f"Single / Fixed / Dynamic terminal failures are {failures['single']['terminal_failure_count']} / "
            f"{failures['fixed']['terminal_failure_count']} / {failures['dynamic']['terminal_failure_count']}. "
            f"Relative to Single, Fixed adds {fixed_vs['excess_terminal_failures']} failures and Dynamic adds "
            f"{dynamic_vs['excess_terminal_failures']}. Even the all-failures-correct ceilings are "
            f"{_format_ratio(architecture['fixed']['reliability_ceiling_if_all_failures_correct'])} and "
            f"{_format_ratio(architecture['dynamic']['reliability_ceiling_if_all_failures_correct'])}; failures alone therefore do not fully explain either 17-case gap.",
            "",
            "### Q2. Do Multi-Agent arms still degrade on Completed Runs only?",
            "",
            f"Yes. Completed-only Root Service accuracy is Single {_format_ratio(architecture['single']['completed_only_root_service_accuracy'])}, "
            f"Fixed {_format_ratio(architecture['fixed']['completed_only_root_service_accuracy'])}, and "
            f"Dynamic {_format_ratio(architecture['dynamic']['completed_only_root_service_accuracy'])}. This is descriptive post-hoc evidence, not a new primary inference.",
            "",
            "### Q3. Does Fixed turn Single-correct answers wrong with the same three sources?",
            "",
            f"Yes: {same_source['count']} of {same_source['denominator']} paired outcomes were both completed, Single-correct, and Fixed-wrong. "
            "Because Single and Fixed receive Metrics, Logs, and Traces, these are classified as SAME_SOURCE_SET_SEMANTIC_DEGRADATION. The precise internal mechanism remains UNOBSERVABLE_FROM_FROZEN_ARTIFACTS.",
            "",
            "### Q4. Did Dynamic materially save tools?",
            "",
            f"Dynamic has {routing['tool_call_distribution']['2']} two-tool runs, "
            f"{routing['tool_call_distribution']['3']} three-tool runs, and "
            f"{routing['tool_call_distribution']['other']} other runs. Total calls are "
            f"{routing['total_tool_calls']}; mean reduction versus Single is "
            f"{_format_ratio(routing['mean_tool_reduction_vs_single'])}, and the paired median reduction is "
            f"{routing['median_paired_tool_reduction_vs_single']['value']:.4f}. This supports "
            f"{routing['verdict']} for the frozen distribution only. The exact skipped source is UNOBSERVABLE_FROM_FROZEN_ARTIFACTS.",
            "",
            "### Q5. Where does Root Cause Pair accuracy fail?",
            "",
            "The aggregate JSON reports each fault's raw-schema → Metrics top-6 → final-selection funnel and full indicator confusion matrices. "
            f"Memory is classified **{indicator['memory_and_socket']['mem']['classification']}**: raw "
            f"{_format_ratio(indicator['memory_and_socket']['mem']['raw_schema_coverage'])}, top-6 "
            f"{_format_ratio(indicator['memory_and_socket']['mem']['top6_projection_coverage'])}. "
            f"Socket is classified **{indicator['memory_and_socket']['socket']['classification']}**: raw "
            f"{_format_ratio(indicator['memory_and_socket']['socket']['raw_schema_coverage'])}, top-6 "
            f"{_format_ratio(indicator['memory_and_socket']['socket']['top6_projection_coverage'])}. "
            "This separates raw signal gaps, ranking losses, and final indicator reasoning without inventing Provider traces.",
            "",
            "### Q6. Is Train Ticket deterministically harder than OB / SS at the tool layer?",
            "",
            f"Any-source root-service Coverage@6 is TT {_format_ratio(cross['RE2-TT']['any_source_root_service_coverage_at_6'])}, "
            f"OB {_format_ratio(cross['RE2-OB']['any_source_root_service_coverage_at_6'])}, and "
            f"SS {_format_ratio(cross['RE2-SS']['any_source_root_service_coverage_at_6'])}; by contrast, truth-indicator Metrics top-6 coverage is TT "
            f"{_format_ratio(cross['RE2-TT']['truth_indicator_top6_coverage'])}, OB "
            f"{_format_ratio(cross['RE2-OB']['truth_indicator_top6_coverage'])}, and SS "
            f"{_format_ratio(cross['RE2-SS']['truth_indicator_top6_coverage'])}. "
            f"The adjudicated distribution-shift verdict is **{hypotheses['H6']['result']}**. SS Traces remain SOURCE_UNAVAILABLE, not zero-valued evidence. "
            "Without full-protocol OB/SS Provider evaluation, no accuracy-point effect can be claimed.",
            "",
            "## Hypothesis matrix",
            "",
            "| ID | Result | Confidence | Evidence level |",
            "|---|---|---|---|",
        ]
    )
    for identifier in sorted(hypotheses):
        item = hypotheses[identifier]
        lines.append(
            f"| {identifier} | {item['result']} | {item['confidence']} | {item['evidence_level']} |"
        )
    lines.extend(["", "Detailed supporting and contradicting observations, limits, and next experiments are in the aggregate JSON.", ""])
    for identifier in sorted(hypotheses):
        item = hypotheses[identifier]
        lines.extend(
            [
                f"### {identifier} — {item['hypothesis']}",
                "",
                f"- Supporting: {'; '.join(item['supporting_observations']) or 'None in frozen artifacts.'}",
                f"- Contradicting: {'; '.join(item['contradicting_observations']) or 'None directly observable.'}",
                f"- Cannot conclude: {item['what_cannot_be_concluded']}",
                f"- Next experiment: {item['recommended_next_experiment']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Evidence gaps",
            "",
            "Specialist candidate accuracy, Judge follow-rate, the exact Provider failure stage, exact invalid-schema field, Dynamic skipped source, Specialist/Judge anchoring, and architecture-aware Judge bias are UNOBSERVABLE_FROM_FROZEN_ARTIFACTS.",
            "",
            "## Evidence-ranked next-version recommendations",
            "",
        ]
    )
    for priority in ("P0", "P1", "P2", "P3"):
        lines.extend([f"### {priority}", ""])
        for item in recommendations:
            if item["priority"] != priority:
                continue
            lines.extend(
                [
                    f"- **{item['recommendation']}** Observed mechanism: {item['observed_failure_mechanism']} "
                    f"Expected benefit: {item['expected_benefit']} Risk: {item['risk']} "
                    f"Development test: {item['required_development_set_test']} "
                    f"New external holdout: {item['new_external_holdout_required']}",
                    "",
                ]
            )
    lines.extend(
        [
            "## Review disposition",
            "",
            f"`{READY_STATE}`",
            "",
            "Human review is required. This report does not authorize merge, rerun, retry, release, or a replacement primary claim.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_private_json(path: Path, payload: object) -> None:
    path.write_bytes(canonical_json_bytes(payload))
    path.chmod(0o600)


def _write_private_rows(root: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    jsonl_path = root / "case-attribution.jsonl"
    jsonl = b"".join(
        (
            json.dumps(
                dict(row),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    jsonl_path.write_bytes(jsonl)
    jsonl_path.chmod(0o600)
    csv_path = root / "case-attribution.csv"
    fieldnames = [key for key in rows[0] if key != "deterministic_projection"]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    csv_path.chmod(0o600)
    failure_path = root / "terminal-failure-attribution.csv"
    failure_fields = (
        "case_id",
        "architecture",
        "terminal_status",
        "failure_code",
        "model_calls",
        "tool_calls",
        "known_provider_tokens",
        "latency_seconds",
    )
    with failure_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=failure_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            row for row in rows if row["terminal_status"] != TerminalStatus.COMPLETED.value
        )
    failure_path.chmod(0o600)


def _write_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _source_bindings(control_root: Path, final_report: Path) -> dict[str, str]:
    observed = {
        "implementation_commit": IMPLEMENTATION_COMMIT,
        "protocol_freeze_sha256": sha256_file(
            control_root / "locks/protocol-freeze.json"
        ),
        "terminal_records_lock_sha256": sha256_file(
            control_root / "locks/terminal-records-lock.json"
        ),
        "unblinding_lock_sha256": sha256_file(
            control_root / "locks/unblinding.json"
        ),
        "final_report_sha256": sha256_file(final_report),
    }
    if any(observed[key] != expected for key, expected in EXPECTED_BINDINGS.items()):
        raise ValueError("frozen attribution source binding differs from the authorized snapshot")
    return observed


def _load_frozen_schedule(path: Path) -> tuple[ScheduledRun, ...]:
    """Validate the frozen JSON through Pydantic's strict JSON-mode parser."""

    payload = read_json_object(path)
    if set(payload) != {"schema_version", "records"}:
        raise ValueError("holdout schedule has unexpected fields")
    if payload["schema_version"] != "rcaeval-re2.holdout-schedule.v1":
        raise ValueError("holdout schedule schema version is invalid")
    raw_records = payload["records"]
    if not isinstance(raw_records, list):
        raise ValueError("holdout schedule records are invalid")
    return tuple(
        ScheduledRun.model_validate_json(canonical_json_bytes(item))
        for item in raw_records
    )


def _frozen_reconciliation(
    report: Mapping[str, Any], architecture: Mapping[str, Any]
) -> dict[str, Any]:
    for name in ("single", "fixed", "dynamic"):
        observed = architecture[name]
        frozen = report["architectures"][name]
        if (
            observed["root_service_accuracy"]["numerator"]
            != frozen["root_service_correct"]
            or observed["root_cause_pair_accuracy"]["numerator"]
            != frozen["root_cause_pair_correct"]
            or observed["terminal_failures"] != frozen["terminal_failures"]
        ):
            raise ValueError("attribution counts do not reconcile with the Final Report")
    point = (
        architecture["dynamic"]["root_service_accuracy"]["value"]
        - architecture["single"]["root_service_accuracy"]["value"]
    )
    if point != report["primary"]["point_estimate"]:
        raise ValueError("primary point estimate differs from frozen Final Report")
    return {
        "primary_point_estimate": report["primary"]["point_estimate"],
        "primary_ci": {
            "lower": report["primary"]["ci_lower"],
            "upper": report["primary"]["ci_upper"],
        },
        "primary_superiority_supported": report["primary_superiority_supported"],
        "cost_quality_supported": report["cost_quality_supported"],
        "ci_source": "FROZEN_FINAL_REPORT_NOT_RECOMPUTED_BY_ATTRIBUTION",
        "unchanged": True,
    }


def run_attribution(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = args.repo_root.resolve()
    private_root = args.private_root.resolve()
    if repo_root == private_root or repo_root in private_root.parents:
        raise ValueError("private attribution root must be outside Git")
    private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if private_root.is_symlink() or not private_root.is_dir():
        raise ValueError("private attribution root must be a regular directory")
    private_root.chmod(0o700)

    source_bindings = _source_bindings(args.control_root, args.final_report)
    schedule = _load_frozen_schedule(
        args.control_root / "locks/holdout-schedule.json"
    )
    records = load_terminal_records(schedule, args.journal_root)
    attempts_root = validate_attempt_markers(schedule, args.journal_root)
    truth = load_ground_truth(args.evaluator_root / "ground-truth.json")
    tt_cases = load_sanitized_cases(args.sanitized_root)
    if (
        len(schedule) != 270
        or len(records) != 270
        or len(truth) != 90
        or len(tt_cases) != 90
        or len({record.run_id for record in records}) != 270
    ):
        raise ValueError("frozen attribution accounting is incomplete")
    attempts = tuple(attempts_root.glob("*.json"))
    if len(attempts) != 270:
        raise ValueError("frozen semantic attempt accounting is incomplete")
    _records_by_case(records)

    tt_truth = {
        case.case_id: (
            truth[case.case_id].root_cause_service,
            normalize_indicator(truth[case.case_id].fault),
        )
        for case in tt_cases
    }
    projections, tt_complexities = _project_cases(
        tt_cases, tt_truth, workers=args.workers
    )
    case_rows = build_case_rows(records, truth, projections)
    if len(case_rows) != 270:
        raise ValueError("private case attribution table is incomplete")

    architecture = build_architecture_decomposition(records, truth)
    pairwise = build_pairwise_outcomes(records, truth)
    terminal_failures = build_terminal_failures(records)
    tool_coverage = build_tool_coverage(projections, case_rows)
    indicator_pipeline = build_indicator_pipeline(records, truth, projections)
    dynamic_routing = build_dynamic_routing(records, truth)
    citation_behavior = build_citation_behavior(records, truth)

    ob_cases = discover_dev_cases(args.ob_root, DevSystem.RE2_OB)
    ss_cases = discover_dev_cases(args.ss_root, DevSystem.RE2_SS)
    ob_truth = {
        case.case_id: (case.root_cause_service, normalize_indicator(case.fault))
        for case in ob_cases
    }
    ss_truth = {
        case.case_id: (case.root_cause_service, normalize_indicator(case.fault))
        for case in ss_cases
    }
    ob_comparison, _ = _cross_system_item(
        ob_cases, ob_truth, workers=args.workers
    )
    ss_comparison, _ = _cross_system_item(
        ss_cases, ss_truth, workers=args.workers
    )
    tt_comparison, _ = _cross_system_item(
        tt_cases,
        tt_truth,
        existing_projections=projections,
        existing_complexities=tt_complexities,
        workers=args.workers,
    )
    cross_system = {
        "RE2-OB": ob_comparison,
        "RE2-SS": ss_comparison,
        "RE2-TT": tt_comparison,
        "performance_boundary": (
            "Deterministic tool statistics only; no OB/SS Provider evaluation was run."
        ),
    }

    final_report = read_json_object(args.final_report)
    aggregate: dict[str, Any] = {
        "schema_version": "rcaeval-re2.post-hoc-attribution.v1",
        "classification": list(CLASSIFICATION),
        "source_bindings": source_bindings,
        "run_accounting": {
            "scheduled_runs": len(schedule),
            "terminal_records": len(records),
            "attempt_markers": len(attempts),
            "cases": len(truth),
            "architecture_arms_per_case": 3,
            "duplicate_runs": len(records) - len({record.run_id for record in records}),
            "provider_calls_during_attribution": 0,
        },
        "frozen_result_reconciliation": _frozen_reconciliation(
            final_report, architecture
        ),
        "architecture_decomposition": architecture,
        "pairwise_outcomes": pairwise,
        "terminal_failures": terminal_failures,
        "tool_coverage": tool_coverage,
        "citation_behavior": citation_behavior,
        "indicator_pipeline": indicator_pipeline,
        "dynamic_routing": dynamic_routing,
        "cross_system_deterministic_comparison": cross_system,
        "hypothesis_matrix": {},
        "evidence_gaps": [
            {"question": "Specialist candidate accuracy", "result": UNOBSERVABLE},
            {"question": "Judge follow-rate", "result": UNOBSERVABLE},
            {"question": "Exact Provider failure stage", "result": UNOBSERVABLE},
            {"question": "Exact invalid-schema field", "result": UNOBSERVABLE},
            {"question": "Dynamic exact skipped source", "result": UNOBSERVABLE},
            {"question": "Specialist-to-Judge anchoring", "result": UNOBSERVABLE},
            {"question": "Architecture-aware Judge bias", "result": UNOBSERVABLE},
        ],
        "recommended_next_experiments": _recommendations(),
    }
    aggregate["hypothesis_matrix"] = _hypothesis_matrix(aggregate)
    assert_public_payload(aggregate)

    audit = _build_observability_audit(source_bindings)
    _write_private_json(private_root / "attribution-observability-audit.json", audit)
    _write_private_rows(private_root, case_rows)

    aggregate_bytes = canonical_json_bytes(aggregate)
    summary = render_summary(aggregate)
    disposition = {
        "schema_version": "rcaeval-re2.post-hoc-attribution-disposition.v1",
        "state": READY_STATE,
        "classification": list(CLASSIFICATION),
        "frozen_result_unchanged": True,
        "provider_calls_during_attribution": 0,
        "holdout_rerun": False,
        "terminal_retry": False,
        "private_case_analysis_committed": False,
        "human_review_required": True,
        "merge_authorized": False,
        "source_bindings": source_bindings,
    }
    assert_public_payload(disposition)
    public_paths = (args.aggregate_output, args.summary_output, args.disposition_output)
    for path in public_paths:
        try:
            relative = path.resolve().relative_to(repo_root).as_posix()
        except ValueError as error:
            raise ValueError("public output must remain inside the repository") from error
        validate_allowed_paths((relative,))
    _write_public(args.aggregate_output, aggregate_bytes)
    _write_public(args.summary_output, summary.encode("utf-8"))
    _write_public(args.disposition_output, canonical_json_bytes(disposition))
    return aggregate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Attribute the frozen RCAEval RE2 v1 result without Provider calls or reruns"
        )
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--sanitized-root", type=Path, required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    parser.add_argument("--ob-root", type=Path, required=True)
    parser.add_argument("--ss-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--aggregate-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--disposition-output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    run_attribution(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
