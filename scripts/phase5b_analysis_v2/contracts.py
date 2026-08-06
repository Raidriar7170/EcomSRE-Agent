"""Typed create-once outputs for the Phase 5B v2 analysis-only repair."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictInt

from scripts.phase5b_execution.contracts import (
    ExecutionModel,
    FinalEvaluationReport,
    ScoringBundle,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
ANALYSIS_VERSION: Literal["phase5b.v2-analysis-contract-repair"] = (
    "phase5b.v2-analysis-contract-repair"
)
SUBSET_MAPPING_SOURCE: Literal[
    "scripts/phase5b_execution/scoring.py::_SUBSETS_BY_TEMPLATE"
] = "scripts/phase5b_execution/scoring.py::_SUBSETS_BY_TEMPLATE"


class V2ScoringBundle(ScoringBundle):
    """A v2-labeled bundle over the immutable Phase 5B v1 records."""

    schema_version: Literal["phase5b.scoring-bundle.v2"]  # type: ignore[assignment]
    analysis_version: Literal["phase5b.v2-analysis-contract-repair"]
    input_evaluation_version: Literal["phase5b.v1"]
    subset_mapping_source: Literal[
        "scripts/phase5b_execution/scoring.py::_SUBSETS_BY_TEMPLATE"
    ]
    private_difficult_subsets_used: Literal[False]
    provider_calls: Literal[0]


class V2FinalEvaluationReport(FinalEvaluationReport):
    """A v2-labeled final report preserving all frozen v1 metric contracts."""

    schema_version: Literal["phase5b.final-report.v2"]  # type: ignore[assignment]
    analysis_version: Literal["phase5b.v2-analysis-contract-repair"]
    input_evaluation_version: Literal["phase5b.v1"]
    subset_mapping_source: Literal[
        "scripts/phase5b_execution/scoring.py::_SUBSETS_BY_TEMPLATE"
    ]
    private_difficult_subsets_used: Literal[False]
    provider_calls: Literal[0]


class V2AnalysisAttempt(ExecutionModel):
    """Exclusive marker written before any v2 scoring or bootstrap work."""

    schema_version: Literal["phase5b.analysis-attempt.v2"]
    status: Literal["PHASE5B_V2_ANALYSIS_ATTEMPTED"]
    analysis_version: Literal["phase5b.v2-analysis-contract-repair"]
    analysis_protocol_sha256: str = Field(pattern=_SHA256_PATTERN)
    analysis_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    review_disposition_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    unblinding_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    raw_record_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_calls: Literal[0]
    analysis_executed: Literal[False]
    create_once: Literal[True]


class V2FinalDisposition(ExecutionModel):
    """Create-once v2 completion record bound to all immutable v1 evidence."""

    schema_version: Literal["phase5b.final-disposition.v2"]
    status: Literal["PHASE5B_V2_FINAL_REPORT_FROZEN"]
    analysis_version: Literal["phase5b.v2-analysis-contract-repair"]
    input_evaluation_version: Literal["phase5b.v1"]
    analysis_protocol_sha256: str = Field(pattern=_SHA256_PATTERN)
    analysis_attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    unblinding_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    raw_record_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    scoring_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    final_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    main_runs: Literal[180]
    ablation_runs: Literal[38]
    failure_count: StrictInt = Field(ge=0, le=218)
    provider_calls: Literal[0]
    provider_reruns: Literal[0]
    diagnosis_output_modified: Literal[False]
    decision_root_mechanism_truth_modified: Literal[False]
    post_unblind_tuning: Literal[False]
    private_difficult_subsets_used: Literal[False]
    subset_mapping_source: Literal[
        "scripts/phase5b_execution/scoring.py::_SUBSETS_BY_TEMPLATE"
    ]
    primary_population: Literal["HIDDEN_ONLY"]
    claim_classification: Literal[
        "HIDDEN_ACCURACY_SUPERIORITY_SUPPORTED",
        "HIDDEN_COST_QUALITY_ADVANTAGE_SUPPORTED",
        "NO_PREREGISTERED_ADVANTAGE_SUPPORTED",
    ]
    scoring_bundle_created: Literal[True]
    final_report_created: Literal[True]
    analysis_executed: Literal[True]
    create_once: Literal[True]
