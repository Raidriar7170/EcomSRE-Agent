"""Strict protocol and immutable-input bindings for Phase 5B analysis v2."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
ANALYSIS_PROTOCOL_RELATIVE = Path(
    "config/phase5b-analysis-v2/analysis-protocol.v2.json"
)


class AnalysisProtocol(BaseModel):
    """Review-gated contract for the analysis-only repair."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["phase5b.analysis-protocol.v2"]
    analysis_version: Literal["phase5b.v2-analysis-contract-repair"]
    input_evaluation_version: Literal["phase5b.v1"]
    execution_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    unblinding_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_schedule_sha256: str = Field(pattern=_SHA256_PATTERN)
    protocol_freeze_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    agent_visible_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_seal_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    main_run_count: Literal[180]
    ablation_gap_count: Literal[38]
    provider_calls: Literal[0]
    raw_records_read_only: Literal[True]
    diagnosis_output_modified: Literal[False]
    decision_root_mechanism_truth_modified: Literal[False]
    prompt_agent_runtime_schedule_budgets_modified: Literal[False]
    private_difficult_subsets_used: Literal[False]
    subset_mapping_source: Literal[
        "scripts/phase5b_execution/scoring.py::_SUBSETS_BY_TEMPLATE"
    ]
    primary_population: Literal["HIDDEN_ONLY"]
    bootstrap_replicates: Literal[10000]
    bootstrap_rng_seed: Literal[20260804]
    confidence_interval: StrictFloat
    output_root_must_be_separate: Literal[True]
    analysis_authorization: Literal["AUTHORIZE_PHASE5B_V2_ANALYSIS_ONLY"]
    analysis_executed: Literal[False]
    review_required: Literal[True]


def verify_regular_file_sha256(path: Path, *, expected_sha256: str) -> str:
    """Verify one immutable regular file without following symlinks."""

    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("bound input must be a regular non-symlink file")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected_sha256:
        raise ValueError("bound input SHA-256 mismatch")
    return observed


def load_analysis_protocol(project_root: Path) -> AnalysisProtocol:
    """Load the strict v2 protocol without mutating v1 state."""

    path = project_root / ANALYSIS_PROTOCOL_RELATIVE
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("analysis protocol must be a regular non-symlink file")
    protocol = AnalysisProtocol.model_validate_json(path.read_bytes(), strict=True)
    if protocol.confidence_interval != 0.95:
        raise ValueError("analysis confidence interval is not frozen")
    return protocol
