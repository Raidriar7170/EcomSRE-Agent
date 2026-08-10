"""No-Provider, no-label audit of all 103 agent-facing RCA100 cases."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt

from ecomsre_rca100.contracts import RCA100Model
from ecomsre_rca100.projection import build_agent_context


EXPECTED_CASE_FILES = {
    "task.json",
    "metrics.parquet",
    "logs.parquet",
    "traces.parquet",
    "events.parquet",
    "alerts.parquet",
    "topology.json",
}


class RCA100SchemaAudit(RCA100Model):
    schema_version: Literal["rca100.no-label-schema-audit.v1"] = (
        "rca100.no-label-schema-audit.v1"
    )
    cases: Literal[103] = 103
    agent_facing_files: Literal[721] = 721
    tasks_parsed: StrictInt = Field(ge=0)
    metrics_parsed: StrictInt = Field(ge=0)
    logs_parsed: StrictInt = Field(ge=0)
    traces_parsed: StrictInt = Field(ge=0)
    topology_parsed: StrictInt = Field(ge=0)
    metrics_ranking_available: StrictInt = Field(ge=0)
    metrics_ranking_unavailable: StrictInt = Field(ge=0)
    metrics_rows: StrictInt = Field(ge=0)
    logs_rows: StrictInt = Field(ge=0)
    traces_rows: StrictInt = Field(ge=0)
    metrics_mapped_rows: StrictInt = Field(ge=0)
    metrics_unmapped_rows: StrictInt = Field(ge=0)
    logs_mapped_rows: StrictInt = Field(ge=0)
    logs_unmapped_rows: StrictInt = Field(ge=0)
    traces_mapped_rows: StrictInt = Field(ge=0)
    traces_unmapped_rows: StrictInt = Field(ge=0)
    valid_metric_series: StrictInt = Field(ge=0)
    ranked_metric_entities: StrictInt = Field(ge=0)
    anchor_source_distribution: dict[str, int]
    maximum_visible_entities: StrictInt = Field(ge=0, le=64)
    maximum_bounded_evidence: StrictInt = Field(ge=0, le=18)
    provider_calls: Literal[0] = 0
    excluded_events: Literal[True] = True
    excluded_full_alerts: Literal[True] = True
    topology_edges_model_visible: Literal[False] = False
    label_files_materialized: Literal[False] = False


def audit_dataset(rca100_root: Path) -> RCA100SchemaAudit:
    cases_root = rca100_root / "cases"
    if (rca100_root / ("answer" + "_key")).exists():
        raise ValueError("label files are materialized in the runtime source")
    case_dirs = tuple(
        path
        for path in sorted(cases_root.iterdir())
        if path.is_dir() and not path.is_symlink()
    )
    expected = tuple(f"t{index:03d}" for index in range(1, 104))
    if tuple(path.name for path in case_dirs) != expected:
        raise ValueError("RCA100 case directory manifest differs from 103 tasks")
    anchors: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    max_entities = max_evidence = 0
    for index, case_root in enumerate(case_dirs, 1):
        files = {path.name for path in case_root.iterdir() if path.is_file()}
        if files != EXPECTED_CASE_FILES or any(path.is_symlink() for path in case_root.iterdir()):
            raise ValueError("RCA100 case file manifest differs from seven files")
        context = build_agent_context(
            case_root, opaque_case_id=f"rca100-case-{index:04d}"
        )
        totals["tasks"] += 1
        totals["metrics"] += 1
        totals["logs"] += int(context.logs.status == "AVAILABLE")
        totals["traces"] += int(context.traces.status == "AVAILABLE")
        totals["topology"] += 1
        totals["metrics_available"] += int(context.metrics.status == "AVAILABLE")
        totals["metrics_rows"] += context.metrics.total_rows
        totals["logs_rows"] += context.logs.total_rows
        totals["traces_rows"] += context.traces.total_rows
        totals["metrics_mapped"] += context.metrics.mapped_rows
        totals["metrics_unmapped"] += context.metrics.unmapped_rows
        totals["logs_mapped"] += context.logs.mapped_rows
        totals["logs_unmapped"] += context.logs.unmapped_rows
        totals["traces_mapped"] += context.traces.mapped_rows
        totals["traces_unmapped"] += context.traces.unmapped_rows
        totals["valid_series"] += context.metrics.valid_series
        totals["ranked_entities"] += context.metrics.ranked_entities
        anchors[context.task.anchor_source] += 1
        max_entities = max(max_entities, len(context.visible_entities))
        max_evidence = max(
            max_evidence,
            len(context.metrics.evidence)
            + len(context.logs.evidence)
            + len(context.traces.evidence),
        )
    return RCA100SchemaAudit(
        tasks_parsed=totals["tasks"],
        metrics_parsed=totals["metrics"],
        logs_parsed=totals["logs"],
        traces_parsed=totals["traces"],
        topology_parsed=totals["topology"],
        metrics_ranking_available=totals["metrics_available"],
        metrics_ranking_unavailable=103 - totals["metrics_available"],
        metrics_rows=totals["metrics_rows"],
        logs_rows=totals["logs_rows"],
        traces_rows=totals["traces_rows"],
        metrics_mapped_rows=totals["metrics_mapped"],
        metrics_unmapped_rows=totals["metrics_unmapped"],
        logs_mapped_rows=totals["logs_mapped"],
        logs_unmapped_rows=totals["logs_unmapped"],
        traces_mapped_rows=totals["traces_mapped"],
        traces_unmapped_rows=totals["traces_unmapped"],
        valid_metric_series=totals["valid_series"],
        ranked_metric_entities=totals["ranked_entities"],
        anchor_source_distribution=dict(sorted(anchors.items())),
        maximum_visible_entities=max_entities,
        maximum_bounded_evidence=max_evidence,
    )


__all__ = ["EXPECTED_CASE_FILES", "RCA100SchemaAudit", "audit_dataset"]
