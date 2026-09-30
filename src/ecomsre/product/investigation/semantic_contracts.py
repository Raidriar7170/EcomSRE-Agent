"""Read-only research contracts; never Product diagnosis or action authority."""

from typing import Literal
from pydantic import Field
from .contracts import StrictModel


class AnalysisRequest(StrictModel):
    tool: Literal[
        "read_records", "profile_operations", "compare_baseline", "compare_dependencies"
    ]
    target: str = Field(min_length=1, max_length=100)
    window: str = "current"
    source: Literal["traces", "counters"] = "traces"
    reference_id: str | None = None
    group_by: Literal["operation", "span_kind", "purpose"] = "operation"
    detail_level: Literal["summary", "representative_records"] = "summary"
    signal: Literal[
        "error_fraction",
        "error_marker_fraction",
        "explicit_status_error_fraction",
        "duration_ms",
    ] = "error_fraction"
    operation: str | None = None
    direction: (
        Literal["server", "client", "consumer", "producer", "internal"] | None
    ) = None
    neighbors: list[str] = Field(default_factory=list, max_length=3)
    offset: int = Field(default=0, ge=0, le=100000)


class ObservableQuestion(StrictModel):
    field: Literal[
        "error_fraction",
        "error_count",
        "count",
        "absolute_difference",
        "relative_change",
        "z",
        "observed_edges",
        "missing_parents",
    ]
    row_key: str = Field(max_length=300)
    comparator: Literal["gt", "ge", "lt", "le", "eq"]
    threshold: float
    expectations: dict[str, Literal["YES", "NO", "UNSPECIFIED"]] = Field(max_length=4)


class Candidate(StrictModel):
    rationale: str = Field(default="", max_length=600)
    request: AnalysisRequest
    question: ObservableQuestion | None = None


class Hypothesis(StrictModel):
    hypothesis_id: Literal["H1", "H2", "H3", "H4"]
    explanation: str = Field(min_length=1, max_length=700)
    target: str
    support: list[str] = Field(default_factory=list, max_length=12)
    conflicts: list[str] = Field(default_factory=list, max_length=12)
    residuals: list[str] = Field(default_factory=list, max_length=6)


class InvestigationReport(StrictModel):
    ranked_components: list[str] = Field(max_length=4)
    explanation: str = Field(max_length=1600)
    facts: list[str] = Field(max_length=8)
    alternatives: list[str] = Field(max_length=4)
    residuals: list[str] = Field(max_length=6)
    next_observation: str = Field(max_length=600)
    conclusion: Literal["LOCAL_FINDING", "INSUFFICIENT_EVIDENCE"]
    # Explicit assertions are scored alongside a blinded prose review.
    root_confirmed: bool = False
    business_fault_excluded: bool = False
    system_healthy: bool = False


class SemanticDecision(StrictModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=4)
    candidates: list[Candidate] = Field(default_factory=list, max_length=4)
    report: InvestigationReport | None = None
    rationale: str = Field(max_length=600)
