"""Audit the two development-visible datasets and write a public data card."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval.dataset import (
    DatasetAudit,
    DevSystem,
    audit_dev_dataset,
    discover_dev_cases,
)
from ecomsre_rcaeval_v2.indicator import load_indicator_config
from ecomsre_rcaeval_v2.indicator_evaluation import (
    MetricSchemaAudit,
    MetricValueQualityAudit,
    audit_metric_schemas,
    audit_metric_value_quality,
)
from ecomsre_rcaeval_v2.public_projection import write_public_text_create_once


_FORBIDDEN_MARKERS = ("re2-tt", "tt-case-", "holdout-sanitized", "evaluator-only")


def _reject_tt_paths(*paths: Path) -> None:
    if any(
        marker in str(path).casefold()
        for path in paths
        for marker in _FORBIDDEN_MARKERS
    ):
        raise ValueError("data audit path contains a forbidden TT marker")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_dataset_lock(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("dataset lock must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("dataset lock is invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("dataset lock is invalid")
    return payload


def _verify_source_audit(
    audit: DatasetAudit,
    dataset_lock: dict[str, object],
) -> None:
    systems = dataset_lock.get("systems")
    if not isinstance(systems, dict):
        raise ValueError("dataset lock systems are invalid")
    expected = systems.get(audit.system.value)
    if not isinstance(expected, dict):
        raise ValueError("dataset lock system entry is invalid")
    required = {
        "case_count": audit.case_count,
        "service_count": audit.service_count,
        "fault_count": audit.fault_count,
        "extracted_manifest_sha256": audit.extracted_manifest_sha256,
        "schema_manifest_sha256": audit.schema_manifest_sha256,
    }
    if any(expected.get(key) != value for key, value in required.items()):
        raise ValueError("live development dataset differs from dataset lock")
    expected_traces = audit.case_count if audit.system is DevSystem.RE2_OB else 0
    if audit.traces_cases != expected_traces:
        raise ValueError("live development trace availability differs from protocol")


def render_data_card(
    schema_audit: MetricSchemaAudit,
    value_audit: MetricValueQualityAudit,
    source_audits: tuple[DatasetAudit, ...],
    *,
    dataset_lock_sha256: str,
    formula_config_sha256: str,
) -> str:
    by_system = {item.system.value: item for item in source_audits}
    lines = [
        "# RCAEval RE2 v2 Development Data Card",
        "",
        "Status: `DEVELOPMENT_VISIBLE / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`",
        "",
        "This data card covers only RE2-OB and RE2-SS. It records schema and mapping",
        "evidence for development; it is not an external benchmark claim.",
        "",
        "## Source bindings",
        "",
        f"- Dataset lock SHA-256: `{dataset_lock_sha256}`",
        f"- Indicator formula registry SHA-256: `{formula_config_sha256}`",
        f"- Cases audited: {schema_audit.case_count}",
        "- Telemetry-value use in split selection: No",
        "- Provider calls: 0",
        "",
        "## Metric schema and normalization",
        "",
        "| System | Cases | Schema variants | Unique metric names | Canonical | Auxiliary | Unknown | Ambiguous | Raw truth-indicator coverage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for system in ("RE2-OB", "RE2-SS"):
        schema_item = schema_audit.systems[system]  # type: ignore[index]
        counts = schema_item.disposition_unique_counts
        raw = schema_item.raw_truth_indicator_coverage
        lines.append(
            f"| {system} | {schema_item.case_count} | "
            f"{schema_item.schema_variant_count} | "
            f"{schema_item.unique_metric_names} | {counts['CANONICAL']} | "
            f"{counts['AUXILIARY']} | {counts['UNKNOWN']} | "
            f"{counts['AMBIGUOUS']} | {raw.numerator}/{raw.denominator} "
            f"({raw.value:.4f}) |"
        )
    lines.extend(
        [
            "",
            "The registry uses exact case-sensitive service prefixes and suffixes.",
            "Unknown suffixes remain `UNKNOWN`; ambiguous mappings remain `AMBIGUOUS`.",
            "No Ground Truth is used to create runtime candidates.",
            "",
            "## Live source verification",
            "",
            "| System | Cases | Services | Faults | Metrics schemas | Logs schemas | Traces schemas | Extracted manifest match |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for system in ("RE2-OB", "RE2-SS"):
        source_item = by_system[system]
        lines.append(
            f"| {system} | {source_item.case_count} | "
            f"{source_item.service_count} | {source_item.fault_count} | "
            f"{source_item.metrics_schema_variants} | "
            f"{source_item.logs_schema_variants} | "
            f"{source_item.traces_schema_variants} | Yes |"
        )
    raw = schema_audit.raw_truth_indicator_coverage
    lines.extend(
        [
            "",
            "## Metric value quality",
            "",
            "| System | Rows | Metric cells | Missing timestamps | Missing values | Nonfinite values |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for system in ("RE2-OB", "RE2-SS"):
        value_item = value_audit.systems[system]  # type: ignore[index]
        lines.append(
            f"| {system} | {value_item.row_count} | "
            f"{value_item.metric_cell_count} | "
            f"{value_item.missing_timestamp_count} | "
            f"{value_item.missing_value_count} | "
            f"{value_item.nonfinite_value_count} |"
        )
    lines.extend(
        [
            "",
            "The versioned value policy preserves row order, drops rows with a missing",
            "timestamp, fails closed on a nonfinite timestamp, and replaces each missing",
            "or nonfinite metric value with its previous finite value or zero if none",
            "exists. This matches the frozen v1 deterministic metric reader.",
            "",
            "## Development boundary",
            "",
            f"- Overall raw truth-indicator coverage: {raw.numerator}/{raw.denominator} ({raw.value:.4f})",
            "- Formula selection uses only the frozen 60-case DESIGN split.",
            "- DEV_VALIDATION metric values are not used for formula selection.",
            "- Full case-level formula outcomes remain outside Git.",
            "- Any later validation result remains development-only.",
            "",
        ]
    )
    return "\n".join(lines)


def run_audit(
    *,
    ob_root: Path,
    ss_root: Path,
    dataset_lock_path: Path,
    formula_config_path: Path,
    expected_formula_config_sha256: str,
    data_card_output: Path,
) -> str:
    _reject_tt_paths(ob_root, ss_root, dataset_lock_path, formula_config_path)
    dataset_lock = _load_dataset_lock(dataset_lock_path)
    config = load_indicator_config(
        formula_config_path,
        expected_sha256=expected_formula_config_sha256,
    )
    ob_cases = discover_dev_cases(ob_root, DevSystem.RE2_OB)
    ss_cases = discover_dev_cases(ss_root, DevSystem.RE2_SS)
    if len(ob_cases) != 90 or len(ss_cases) != 90:
        raise ValueError("development data audit requires exactly 90 cases per system")
    source_audits = (
        audit_dev_dataset(
            ob_root,
            DevSystem.RE2_OB,
            expected_cases=90,
            require_locked_distribution=True,
        ),
        audit_dev_dataset(
            ss_root,
            DevSystem.RE2_SS,
            expected_cases=90,
            require_locked_distribution=True,
        ),
    )
    for item in source_audits:
        _verify_source_audit(item, dataset_lock)
    schema_audit = audit_metric_schemas(ob_cases + ss_cases, config)
    value_audit = audit_metric_value_quality(ob_cases + ss_cases)
    data_card = render_data_card(
        schema_audit,
        value_audit,
        source_audits,
        dataset_lock_sha256=_sha256_file(dataset_lock_path),
        formula_config_sha256=config.sha256,
    )
    return write_public_text_create_once(data_card_output, data_card)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit OB/SS development data and write a public data card."
    )
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--dataset-lock", required=True, type=Path)
    parser.add_argument("--formula-config", required=True, type=Path)
    parser.add_argument("--formula-config-sha256", required=True)
    parser.add_argument("--data-card-output", required=True, type=Path)
    return parser


def main(argv: tuple[str, ...] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_audit(
        ob_root=args.ob_root,
        ss_root=args.ss_root,
        dataset_lock_path=args.dataset_lock,
        formula_config_path=args.formula_config,
        expected_formula_config_sha256=args.formula_config_sha256,
        data_card_output=args.data_card_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
