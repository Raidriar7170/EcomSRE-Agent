"""Header-only data audit and DESIGN-only indicator formula evaluation."""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Literal

from pydantic import Field, StrictInt

from ecomsre_rcaeval.dataset import DevCase, TelemetryCase
from ecomsre_rcaeval.scoring import normalize_indicator
from ecomsre_rcaeval_v2.contracts import DevSystem, ServiceName, Sha256, V2Model
from ecomsre_rcaeval_v2.indicator import (
    CoverageAtK,
    FormulaEvaluation,
    FormulaId,
    FormulaSelection,
    LoadedIndicatorConfig,
    MetricIndicatorCandidate,
    MetricNameDisposition,
    MetricSample,
    collapse_and_rank_candidates,
    normalize_metric_name,
    score_metric_candidate,
    select_formula,
)
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    SplitAssignment,
    SplitName,
    case_identity_bytes,
)


_DISPOSITIONS = ("CANONICAL", "AUXILIARY", "UNKNOWN", "AMBIGUOUS")
_FORBIDDEN_PATH_MARKERS = (
    "re2-tt",
    "tt-case-",
    "holdout-sanitized",
    "evaluator-only",
    "terminal-journal",
    "ground-truth.json",
    "scored_cases",
    "/attribution/",
)


class SystemMetricSchemaAudit(V2Model):
    system: DevSystem
    case_count: StrictInt = Field(gt=0)
    schema_variant_count: StrictInt = Field(gt=0)
    schema_registry_sha256: Sha256
    metric_column_observations: StrictInt = Field(gt=0)
    unique_metric_names: StrictInt = Field(gt=0)
    disposition_observation_counts: dict[str, StrictInt]
    disposition_unique_counts: dict[str, StrictInt]
    raw_truth_indicator_coverage: CoverageAtK


class MetricSchemaAudit(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.metric-schema-audit.v1"]
    classification: tuple[
        Literal["DEVELOPMENT_VISIBLE"],
        Literal["NOT_EXTERNAL_HOLDOUT"],
        Literal["NOT_PRIMARY_INFERENCE"],
    ]
    case_count: StrictInt = Field(gt=0)
    systems: dict[DevSystem, SystemMetricSchemaAudit]
    raw_truth_indicator_coverage: CoverageAtK


class SystemMetricValueQuality(V2Model):
    system: DevSystem
    case_count: StrictInt = Field(gt=0)
    row_count: StrictInt = Field(gt=0)
    metric_cell_count: StrictInt = Field(gt=0)
    missing_timestamp_count: StrictInt = Field(ge=0)
    missing_value_count: StrictInt = Field(ge=0)
    nonfinite_value_count: StrictInt = Field(ge=0)


class MetricValueQualityAudit(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.metric-value-quality.v1"]
    classification: tuple[
        Literal["DEVELOPMENT_VISIBLE"],
        Literal["NOT_EXTERNAL_HOLDOUT"],
        Literal["NOT_PRIMARY_INFERENCE"],
    ]
    case_count: StrictInt = Field(gt=0)
    systems: dict[DevSystem, SystemMetricValueQuality]


class FormulaCaseOutcome(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.formula-case-outcome.v1"]
    case_identity_sha256: Sha256
    system: DevSystem
    root_cause_service: ServiceName
    fault: Literal["cpu", "mem", "disk", "delay", "loss", "socket"]
    formula: FormulaId
    raw_truth_indicator_present: bool
    truth_indicator_global_rank: StrictInt | None = Field(default=None, ge=1)
    truth_indicator_top6_present: bool
    ranked_candidate_count: StrictInt = Field(ge=0)
    eligible_unknown_count: StrictInt = Field(ge=0)
    ambiguous_count: StrictInt = Field(ge=0)
    auxiliary_metric_count: StrictInt = Field(ge=0)


def _reject_forbidden_path(path: Path) -> None:
    normalized = str(path).casefold()
    if any(marker in normalized for marker in _FORBIDDEN_PATH_MARKERS):
        raise ValueError("indicator evaluation path contains a forbidden TT marker")


def _metric_header(path: Path) -> tuple[str, ...]:
    _reject_forbidden_path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("metrics input must be a regular file")
    with path.open("r", encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle), None)
    if (
        not header
        or len(header) != len(set(header))
        or header.count("time") != 1
        or any(not name for name in header)
    ):
        raise ValueError("metrics header must contain unique names and one time column")
    return tuple(name for name in header if name != "time")


def _coverage(numerator: int, denominator: int) -> CoverageAtK:
    if denominator <= 0:
        raise ValueError("coverage denominator must be positive")
    return CoverageAtK(
        numerator=numerator,
        denominator=denominator,
        value=float(numerator / denominator),
    )


def _case_identity(case: DevCase) -> CaseIdentity:
    return CaseIdentity(
        system=case.system,  # type: ignore[arg-type]
        root_cause_service=case.root_cause_service,
        fault=case.fault,
        instance=case.instance,
    )


def _disposition_counts(values: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(values)
    return {name: counts[name] for name in _DISPOSITIONS}


def audit_metric_schemas(
    cases: tuple[DevCase, ...], config: LoadedIndicatorConfig
) -> MetricSchemaAudit:
    """Audit all metric headers without reading a telemetry value row."""

    if not cases:
        raise ValueError("metric schema audit requires development cases")
    grouped: dict[str, list[DevCase]] = {}
    raw_truth_hits = 0
    for case in cases:
        if case.system not in {"RE2-OB", "RE2-SS"}:
            raise ValueError("metric schema audit allows only OB/SS")
        grouped.setdefault(case.system, []).append(case)

    systems: dict[str, SystemMetricSchemaAudit] = {}
    for system in sorted(grouped):
        system_cases = grouped[system]
        headers: list[tuple[str, ...]] = []
        observed_dispositions: list[str] = []
        unique_normalizations: dict[str, str] = {}
        system_truth_hits = 0
        for case in system_cases:
            header = _metric_header(case.metrics_path)
            headers.append(header)
            normalizations = tuple(
                normalize_metric_name(system, name, config) for name in header
            )
            observed_dispositions.extend(
                item.disposition.value for item in normalizations
            )
            for item in normalizations:
                unique_normalizations[item.metric_name] = item.disposition.value
            truth_indicator = normalize_indicator(case.fault)
            truth_present = any(
                item.disposition is MetricNameDisposition.CANONICAL
                and item.service == case.root_cause_service
                and item.canonical_indicator == truth_indicator
                for item in normalizations
            )
            system_truth_hits += truth_present
        raw_truth_hits += system_truth_hits
        unique_headers = tuple(sorted(set(headers)))
        schema_payload = json.dumps(
            [list(header) for header in unique_headers],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        systems[system] = SystemMetricSchemaAudit(
            system=system,  # type: ignore[arg-type]
            case_count=len(system_cases),
            schema_variant_count=len(unique_headers),
            schema_registry_sha256=hashlib.sha256(schema_payload).hexdigest(),
            metric_column_observations=sum(len(header) for header in headers),
            unique_metric_names=len(unique_normalizations),
            disposition_observation_counts=_disposition_counts(
                tuple(observed_dispositions)
            ),
            disposition_unique_counts=_disposition_counts(
                tuple(unique_normalizations.values())
            ),
            raw_truth_indicator_coverage=_coverage(
                system_truth_hits, len(system_cases)
            ),
        )
    return MetricSchemaAudit(
        schema_version="rcaeval-re2-v2-dev.metric-schema-audit.v1",
        classification=(
            "DEVELOPMENT_VISIBLE",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ),
        case_count=len(cases),
        systems=systems,  # type: ignore[arg-type]
        raw_truth_indicator_coverage=_coverage(raw_truth_hits, len(cases)),
    )


def audit_metric_value_quality(
    cases: tuple[DevCase, ...],
) -> MetricValueQualityAudit:
    """Count raw missing/nonfinite values without computing anomaly scores."""

    if not cases:
        raise ValueError("metric value audit requires development cases")
    grouped: dict[str, list[DevCase]] = {}
    for case in cases:
        if case.system not in {"RE2-OB", "RE2-SS"}:
            raise ValueError("metric value audit allows only OB/SS")
        grouped.setdefault(case.system, []).append(case)
    systems: dict[str, SystemMetricValueQuality] = {}
    for system in sorted(grouped):
        row_count = 0
        metric_cell_count = 0
        missing_timestamp_count = 0
        missing_value_count = 0
        nonfinite_value_count = 0
        for case in grouped[system]:
            names = _metric_header(case.metrics_path)
            with case.metrics_path.open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != ("time", *names):
                    raise ValueError("metrics header changed between reads")
                for row in reader:
                    row_count += 1
                    metric_cell_count += len(names)
                    raw_time = row.get("time")
                    if raw_time is None or raw_time.strip() == "":
                        missing_timestamp_count += 1
                    else:
                        try:
                            timestamp = float(raw_time)
                        except ValueError as error:
                            raise ValueError("metrics timestamp must be finite") from error
                        if not math.isfinite(timestamp):
                            raise ValueError("metrics timestamp must be finite")
                    for name in names:
                        raw_value = row.get(name)
                        if raw_value is None or raw_value.strip() == "":
                            missing_value_count += 1
                            continue
                        try:
                            value = float(raw_value)
                        except ValueError:
                            nonfinite_value_count += 1
                            continue
                        if not math.isfinite(value):
                            nonfinite_value_count += 1
        systems[system] = SystemMetricValueQuality(
            system=system,  # type: ignore[arg-type]
            case_count=len(grouped[system]),
            row_count=row_count,
            metric_cell_count=metric_cell_count,
            missing_timestamp_count=missing_timestamp_count,
            missing_value_count=missing_value_count,
            nonfinite_value_count=nonfinite_value_count,
        )
    return MetricValueQualityAudit(
        schema_version="rcaeval-re2-v2-dev.metric-value-quality.v1",
        classification=(
            "DEVELOPMENT_VISIBLE",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ),
        case_count=len(cases),
        systems=systems,  # type: ignore[arg-type]
    )


def read_metric_samples(
    path: Path, config: LoadedIndicatorConfig
) -> dict[str, tuple[MetricSample, ...]]:
    """Read a metric table under the pre-registered fail-closed value policy."""

    if not isinstance(config, LoadedIndicatorConfig):
        raise TypeError("metric reader requires a hash-verified indicator config")
    names = _metric_header(path)
    columns: dict[str, list[MetricSample]] = {name: [] for name in names}
    previous: dict[str, float] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != ("time", *names):
            raise ValueError("metrics header changed between reads")
        kept_row_count = 0
        for row in reader:
            raw_time = row.get("time")
            if raw_time is None or raw_time.strip() == "":
                continue
            try:
                timestamp = float(raw_time)
            except ValueError as error:
                raise ValueError("metrics timestamp must be finite") from error
            if not math.isfinite(timestamp):
                raise ValueError("metrics timestamp must be finite")
            kept_row_count += 1
            for name in names:
                raw_value = row.get(name)
                try:
                    value = float(raw_value) if raw_value is not None else math.nan
                except ValueError:
                    value = math.nan
                if not math.isfinite(value):
                    value = previous.get(name, 0.0)
                previous[name] = value
                columns[name].append(
                    MetricSample(timestamp=float(timestamp), value=float(value))
                )
    if kept_row_count == 0:
        raise ValueError("metrics input contains no usable timestamped rows")
    return {name: tuple(samples) for name, samples in columns.items()}


def build_runtime_metric_candidates(
    case: TelemetryCase,
    *,
    case_identity_sha256: str,
    formula: FormulaId,
    config: LoadedIndicatorConfig,
) -> tuple[MetricIndicatorCandidate, ...]:
    """Build runtime candidates from telemetry plus an opaque tie-break identity."""

    if case.system not in {"RE2-OB", "RE2-SS"}:
        raise ValueError("runtime indicator candidates allow only OB/SS")
    samples_by_name = read_metric_samples(case.metrics_path, config)
    raw_candidates = tuple(
        score_metric_candidate(
            case.system,
            metric_name,
            samples,
            float(case.inject_time),
            formula,
            f"indicator-column:{index:04d}",
            case_identity_sha256,
            config,
        )
        for index, (metric_name, samples) in enumerate(
            samples_by_name.items(), start=1
        )
    )
    ranked = collapse_and_rank_candidates(raw_candidates, config)
    return tuple(
        item.model_copy(update={"evidence_ref": f"indicator:{index:04d}"})
        for index, item in enumerate(ranked, 1)
    )


def _evaluate_case_formula(
    case: DevCase,
    formula: FormulaId,
    config: LoadedIndicatorConfig,
) -> FormulaCaseOutcome:
    identity = _case_identity(case)
    identity_sha256 = hashlib.sha256(case_identity_bytes(identity)).hexdigest()
    samples_by_name = read_metric_samples(case.metrics_path, config)
    truth_indicator = normalize_indicator(case.fault)
    raw_candidates = tuple(
        score_metric_candidate(
            case.system,
            metric_name,
            samples,
            float(case.inject_time),
            formula,
            f"metric:{index:04d}",
            identity_sha256,
            config,
        )
        for index, (metric_name, samples) in enumerate(
            samples_by_name.items(), start=1
        )
    )
    ranked = collapse_and_rank_candidates(raw_candidates, config)
    matching = tuple(
        item
        for item in ranked
        if item.service == case.root_cause_service
        and item.canonical_indicator == truth_indicator
    )
    if len(matching) > 1:
        raise ValueError("canonical truth indicator was not collapsed")
    rank = matching[0].rank_global if matching else None
    raw_truth_present = any(
        item.normalization.disposition is MetricNameDisposition.CANONICAL
        and item.normalization.service == case.root_cause_service
        and item.normalization.canonical_indicator == truth_indicator
        for item in raw_candidates
    )
    return FormulaCaseOutcome(
        schema_version="rcaeval-re2-v2-dev.formula-case-outcome.v1",
        case_identity_sha256=identity_sha256,
        system=case.system,  # type: ignore[arg-type]
        root_cause_service=case.root_cause_service,
        fault=case.fault,
        formula=formula,
        raw_truth_indicator_present=raw_truth_present,
        truth_indicator_global_rank=rank,
        truth_indicator_top6_present=rank is not None and rank <= 6,
        ranked_candidate_count=len(ranked),
        eligible_unknown_count=sum(item.eligible_unknown for item in raw_candidates),
        ambiguous_count=sum(
            item.normalization.disposition is MetricNameDisposition.AMBIGUOUS
            for item in raw_candidates
        ),
        auxiliary_metric_count=sum(
            item.normalization.disposition is MetricNameDisposition.AUXILIARY
            for item in raw_candidates
        )
    )


def _evaluate_case(
    case: DevCase, config: LoadedIndicatorConfig
) -> tuple[FormulaCaseOutcome, ...]:
    return tuple(
        _evaluate_case_formula(case, formula, config) for formula in FormulaId
    )


def _aggregate_formula(
    formula: FormulaId, outcomes: tuple[FormulaCaseOutcome, ...]
) -> FormulaEvaluation:
    selected = tuple(item for item in outcomes if item.formula is formula)
    if not selected:
        raise ValueError("formula aggregate contains no cases")
    faults = tuple(sorted({item.fault for item in selected}))
    per_fault: dict[str, CoverageAtK] = {
        fault: _coverage(
            sum(
                item.truth_indicator_top6_present
                for item in selected
                if item.fault == fault
            ),
            sum(item.fault == fault for item in selected),
        )
        for fault in faults
    }
    memory = tuple(item for item in selected if item.fault == "mem")
    socket = tuple(item for item in selected if item.fault == "socket")
    return FormulaEvaluation(
        formula=formula,
        macro_truth_indicator_coverage_at_6=float(
            fmean(item.value for item in per_fault.values())
        ),
        overall_coverage_at_6=_coverage(
            sum(item.truth_indicator_top6_present for item in selected),
            len(selected),
        ),
        memory_coverage_at_6=_coverage(
            sum(item.truth_indicator_top6_present for item in memory), len(memory)
        ),
        socket_coverage_at_6=_coverage(
            sum(item.truth_indicator_top6_present for item in socket), len(socket)
        ),
        per_fault_coverage_at_6=per_fault,
        eligible_unknown_count=sum(item.eligible_unknown_count for item in selected),
        ambiguous_count=sum(item.ambiguous_count for item in selected),
        auxiliary_metric_count=sum(item.auxiliary_metric_count for item in selected),
    )


def evaluate_frozen_formula(
    cases: tuple[DevCase, ...],
    formula: FormulaId,
    config: LoadedIndicatorConfig,
) -> tuple[tuple[FormulaCaseOutcome, ...], FormulaEvaluation]:
    """Reverify one inherited formula without evaluating or selecting alternatives."""

    if not cases:
        raise ValueError("frozen formula reverification requires DESIGN cases")
    identities = tuple(_case_identity(case) for case in cases)
    if len(set(identities)) != len(identities):
        raise ValueError("frozen formula reverification has duplicate identities")
    outcomes = tuple(
        _evaluate_case_formula(case, formula, config) for case in cases
    )
    return outcomes, _aggregate_formula(formula, outcomes)


def evaluate_design_formulas(
    cases: tuple[DevCase, ...],
    assignments: tuple[SplitAssignment, ...],
    config: LoadedIndicatorConfig,
) -> tuple[
    tuple[FormulaCaseOutcome, ...],
    tuple[FormulaEvaluation, ...],
    FormulaSelection,
]:
    """Evaluate only DESIGN rows; validation metric files are never opened."""

    case_by_identity: dict[CaseIdentity, DevCase] = {}
    for case in cases:
        identity = _case_identity(case)
        if identity in case_by_identity:
            raise ValueError("duplicate development case identity")
        case_by_identity[identity] = case
    assignment_identities = {item.identity for item in assignments}
    if assignment_identities != set(case_by_identity):
        raise ValueError("split assignments and development cases differ")
    design = tuple(
        item for item in assignments if item.split is SplitName.DESIGN
    )
    if not design:
        raise ValueError("formula evaluation requires DESIGN cases")
    outcomes = tuple(
        outcome
        for assignment in design
        for outcome in _evaluate_case(case_by_identity[assignment.identity], config)
    )
    evaluations = tuple(
        _aggregate_formula(formula, outcomes) for formula in FormulaId
    )
    return outcomes, evaluations, select_formula(evaluations, config)
