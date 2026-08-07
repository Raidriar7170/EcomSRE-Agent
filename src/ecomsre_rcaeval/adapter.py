"""Leakage-safe RCAEval telemetry adapter for the three architecture arms."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal

from pydantic import Field, StrictInt

from ecomsre_rcaeval.artifacts import sha256_file
from ecomsre_rcaeval.contracts import (
    Architecture,
    CommanderDecision,
    RCAEvalModel,
    SpecialistAssessment,
)
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rcaeval.tools import (
    RCAEvalToolset,
    SourceStatus,
    ToolEvidence,
    ToolResponse,
)
from ecomsre.phase1.contracts import Evidence, EvidenceSource
from ecomsre.phase1.evidence import EvidenceStore


SourceName = Literal["metrics", "logs", "traces"]
IncidentText = Literal[
    "A service-level anomaly was detected in the benchmark system around T0. "
    "Investigate the available telemetry, identify the most likely root-cause "
    "service and fault indicator, and cite the evidence used."
]

INCIDENT_TEMPLATE: IncidentText = (
    "A service-level anomaly was detected in the benchmark system around T0. "
    "Investigate the available telemetry, identify the most likely root-cause "
    "service and fault indicator, and cite the evidence used."
)

_EVALUATOR_ONLY_KEYS = frozenset(
    {"fault", "ground_truth", "instance", "raw_path", "root_cause_service"}
)
SOURCE_ORDER: tuple[SourceName, ...] = ("metrics", "logs", "traces")
_EVIDENCE_SOURCE = {
    "metrics": EvidenceSource.METRICS,
    "logs": EvidenceSource.LOGS,
    "traces": EvidenceSource.TRACES,
}


def _assert_agent_visible(payload: RCAEvalModel, case: TelemetryCase) -> None:
    value = payload.model_dump(mode="json")
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if _EVALUATOR_ONLY_KEYS.intersection(item):
                raise ValueError("Agent-visible payload contains evaluator-only fields")
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    forbidden_paths = [
        str(case.root.resolve()),
        str(case.metrics_path.resolve()),
        str(case.logs_path.resolve()),
    ]
    if case.traces_path is not None:
        forbidden_paths.append(str(case.traces_path.resolve()))
    if any(path in encoded for path in forbidden_paths):
        raise ValueError("Agent-visible payload contains a raw telemetry path")


class IncidentManifest(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.incident.v1"] = "rcaeval-re2.incident.v1"
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: str = Field(pattern=r"^RE2-(OB|SS|TT)$")
    anomaly_timestamp: StrictInt = Field(ge=0)
    modalities: tuple[SourceName, ...]
    incident: IncidentText = INCIDENT_TEMPLATE


class SourceObservation(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.source-observation.v1"] = (
        "rcaeval-re2.source-observation.v1"
    )
    source: SourceName
    status: SourceStatus
    reason: str | None = None


class CommanderStage(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.commander-stage.v1"] = (
        "rcaeval-re2.commander-stage.v1"
    )
    stage: StrictInt = Field(ge=1, le=2)
    selected_sources: tuple[SourceName, ...]
    rationale: str = Field(min_length=1, max_length=1_000)


class ArchitectureContext(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.architecture-context.v3"] = (
        "rcaeval-re2.architecture-context.v3"
    )
    context_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str
    architecture: Architecture
    evidence: tuple[ToolEvidence, ...] = Field(max_length=64)
    canonical_evidence: tuple[Evidence, ...] = Field(max_length=64)
    specialist_assessments: tuple[SpecialistAssessment, ...] = Field(max_length=3)
    source_observations: tuple[SourceObservation, ...]
    investigated_sources: tuple[SourceName, ...]
    commander_stages: tuple[CommanderStage, ...] = Field(max_length=2)
    tool_call_count: StrictInt = Field(ge=0, le=8)
    targeted_refinement_used: bool


def incident_for_case(case: TelemetryCase) -> IncidentManifest:
    modalities: tuple[SourceName, ...] = (
        ("metrics", "logs")
        if case.traces_path is None
        else ("metrics", "logs", "traces")
    )
    incident = IncidentManifest(
        case_id=case.case_id,
        system=case.system,
        anomaly_timestamp=case.inject_time,
        modalities=modalities,
    )
    _assert_agent_visible(incident, case)
    return incident


def _query_source(tools: RCAEvalToolset, source: SourceName) -> ToolResponse:
    if source == "metrics":
        return tools.rank_metric_anomalies(top_k=6)
    if source == "logs":
        return tools.summarize_log_patterns(top_k=6)
    return tools.summarize_trace_diagnostics(top_k=6)


def _source_path(case: TelemetryCase, source: SourceName):
    if source == "metrics":
        return case.metrics_path
    if source == "logs":
        return case.logs_path
    return case.traces_path


class ArchitectureContextBuilder:
    """Build one run-local context while charging each native tool exactly once."""

    def __init__(
        self,
        case: TelemetryCase,
        architecture: Architecture,
        *,
        run_id: str | None = None,
    ) -> None:
        self.case = case
        self.architecture = architecture
        self.run_id = run_id or hashlib.sha256(
            b"\0".join(
                (
                    b"rcaeval-re2.context-run.v1",
                    case.case_id.encode("utf-8"),
                    architecture.value.encode("utf-8"),
                )
            )
        ).hexdigest()[:32]
        self._tools = RCAEvalToolset(case)
        self._store = EvidenceStore(self.run_id)
        self._responses: dict[SourceName, ToolResponse] = {}
        self._canonical: dict[SourceName, tuple[Evidence, ...]] = {}

    @property
    def tool_call_count(self) -> int:
        return self._tools.call_count

    def query_source(self, source: SourceName) -> ToolResponse:
        if source in self._responses:
            raise ValueError("RCAEval source was already queried in this run")
        response = _query_source(self._tools, source)
        path = _source_path(self.case, source)
        source_hash = "0" * 64 if path is None else sha256_file(path)
        mapped: list[Evidence] = []
        for index, item in enumerate(response.evidence, start=1):
            mapped.append(
                self._store.add(
                    source=_EVIDENCE_SOURCE[source],
                    observation_type="rcaeval_bounded_observation",
                    attributes={
                        "external_evidence_id": item.evidence_id,
                        "observation_name": item.name,
                    },
                    raw_artifact_ref=f"{source}.json#{index}",
                    raw_artifact_sha256=source_hash,
                    limitations=(
                        "Bounded deterministic RCAEval adapter projection.",
                    ),
                    summary=item.summary,
                    started_at=datetime.fromtimestamp(
                        item.started_at, tz=timezone.utc
                    ),
                    ended_at=datetime.fromtimestamp(item.ended_at, tz=timezone.utc),
                    service=item.service,
                )
            )
        self._responses[source] = response
        self._canonical[source] = tuple(mapped)
        return response

    def snapshot(
        self,
        *,
        specialist_assessments: tuple[SpecialistAssessment, ...] = (),
        commander_decision: CommanderDecision | None = None,
    ) -> ArchitectureContext:
        sources = tuple(source for source in SOURCE_ORDER if source in self._responses)
        stages: tuple[CommanderStage, ...] = ()
        if self.architecture is Architecture.DYNAMIC:
            stages = (
                CommanderStage(
                    stage=1,
                    selected_sources=("metrics",),
                    rationale="Begin with Metrics before planning bounded follow-up.",
                ),
            )
            if commander_decision is not None:
                stages += (
                    CommanderStage(
                        stage=2,
                        selected_sources=commander_decision.selected_sources,
                        rationale=commander_decision.rationale,
                    ),
                )
        context_id = hashlib.sha256(
            b"\0".join(
                (
                    b"rcaeval-re2.architecture-context.v3",
                    self.run_id.encode("utf-8"),
                    self.case.case_id.encode("utf-8"),
                    self.architecture.value.encode("utf-8"),
                    ",".join(sources).encode("utf-8"),
                )
            )
        ).hexdigest()[:32]
        context = ArchitectureContext(
            context_id=context_id,
            run_id=self.run_id,
            case_id=self.case.case_id,
            architecture=self.architecture,
            evidence=tuple(
                item
                for source in sources
                for item in self._responses[source].evidence
            ),
            canonical_evidence=tuple(
                item for source in sources for item in self._canonical[source]
            ),
            specialist_assessments=specialist_assessments,
            source_observations=tuple(
                SourceObservation(
                    source=source,
                    status=self._responses[source].status,
                    reason=self._responses[source].reason,
                )
                for source in sources
            ),
            investigated_sources=sources,
            commander_stages=stages,
            tool_call_count=self.tool_call_count,
            targeted_refinement_used=False,
        )
        _assert_agent_visible(context, self.case)
        return context


def prepare_architecture_context(
    case: TelemetryCase,
    architecture: Architecture,
    *,
    run_id: str | None = None,
    sources: tuple[SourceName, ...] | None = None,
) -> ArchitectureContext:
    """Convenience constructor used by tests and Single/Fixed preparation."""

    selected = sources
    if selected is None:
        selected = ("metrics",) if architecture is Architecture.DYNAMIC else SOURCE_ORDER
    if len(selected) != len(set(selected)) or any(item not in SOURCE_ORDER for item in selected):
        raise ValueError("RCAEval context sources are invalid")
    builder = ArchitectureContextBuilder(case, architecture, run_id=run_id)
    for source in selected:
        builder.query_source(source)
    return builder.snapshot()
