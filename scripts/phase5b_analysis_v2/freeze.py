"""Strict repository freeze and review disposition for Phase 5B v2."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import Field

from scripts.phase5b_analysis_v2.protocol import (
    ANALYSIS_PROTOCOL_RELATIVE,
    load_analysis_protocol,
    verify_regular_file_sha256,
)
from scripts.phase5b_execution.checkpoint import _load_canonical
from scripts.phase5b_execution.contracts import ExecutionModel, canonical_json_bytes
from scripts.phase5b_execution.scoring import _SUBSETS_BY_TEMPLATE


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
ANALYSIS_FREEZE_RELATIVE = Path("config/phase5b-analysis-v2/analysis-freeze.v2.json")
REVIEW_DISPOSITION_RELATIVE = Path(
    "docs/review-evidence/phase5b-v2-analysis-contract/current-disposition.json"
)
V1_TERMINATION_RELATIVE = Path(
    "docs/review-evidence/phase5b-v1-termination/current-disposition.json"
)
V1_SCORING_SOURCE_RELATIVE = Path("scripts/phase5b_execution/scoring.py")
_HARNESS_FILES = frozenset(
    Path("scripts/phase5b_analysis_v2") / name
    for name in (
        "__init__.py",
        "analysis.py",
        "cli.py",
        "contracts.py",
        "evaluator.py",
        "freeze.py",
        "protocol.py",
        "reporting.py",
        "runner.py",
    )
)


class FrozenHarnessFile(ExecutionModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class AnalysisFreeze(ExecutionModel):
    schema_version: Literal["phase5b.analysis-freeze.v2"]
    analysis_version: Literal["phase5b.v2-analysis-contract-repair"]
    analysis_protocol_sha256: str = Field(pattern=_SHA256_PATTERN)
    v1_scoring_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_subset_mapping_sha256: str = Field(pattern=_SHA256_PATTERN)
    harness_files: tuple[FrozenHarnessFile, ...]
    provider_calls: Literal[0]
    raw_records_read_only: Literal[True]
    analysis_executed: Literal[False]
    review_required: Literal[True]


class ReviewDisposition(ExecutionModel):
    schema_version: Literal["phase5b.analysis-review-disposition.v2"]
    status: Literal["PHASE5B_V2_ANALYSIS_CONTRACT_REPAIR_READY_FOR_REVIEW"]
    analysis_version: Literal["phase5b.v2-analysis-contract-repair"]
    v1_termination_status: Literal[
        "PHASE5B_V1_TERMINATED_GROUND_TRUTH_CONTRACT_MISMATCH"
    ]
    v1_termination_disposition_sha256: str = Field(pattern=_SHA256_PATTERN)
    analysis_protocol_sha256: str = Field(pattern=_SHA256_PATTERN)
    analysis_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    unblinding_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    raw_record_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    main_terminal: Literal[180]
    ablation_gap_terminal: Literal[38]
    ground_truth_records_admitted: Literal[30]
    provider_calls: Literal[0]
    provider_reruns: Literal[0]
    post_unblind_tuning: Literal[False]
    diagnosis_output_modified: Literal[False]
    decision_root_mechanism_truth_modified: Literal[False]
    prompt_agent_runtime_schedule_budgets_modified: Literal[False]
    private_difficult_subsets_used: Literal[False]
    scoring_bundle_created: Literal[False]
    final_report_created: Literal[False]
    analysis_executed: Literal[False]
    review_required: Literal[True]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _public_mapping_sha256() -> str:
    payload = {
        template_id: list(subsets)
        for template_id, subsets in sorted(_SUBSETS_BY_TEMPLATE.items())
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def verify_analysis_freeze(project_root: Path) -> AnalysisFreeze:
    """Verify every v2 evaluator source and its public subset mapping."""

    freeze = _load_canonical(
        project_root / ANALYSIS_FREEZE_RELATIVE,
        AnalysisFreeze,
    )
    observed_paths = tuple(Path(item.path) for item in freeze.harness_files)
    if len(observed_paths) != len(set(observed_paths)) or set(observed_paths) != set(
        _HARNESS_FILES
    ):
        raise ValueError("v2 analysis freeze harness file set differs")
    for item in freeze.harness_files:
        verify_regular_file_sha256(
            project_root / item.path,
            expected_sha256=item.sha256,
        )
    verify_regular_file_sha256(
        project_root / ANALYSIS_PROTOCOL_RELATIVE,
        expected_sha256=freeze.analysis_protocol_sha256,
    )
    verify_regular_file_sha256(
        project_root / V1_SCORING_SOURCE_RELATIVE,
        expected_sha256=freeze.v1_scoring_source_sha256,
    )
    if freeze.public_subset_mapping_sha256 != _public_mapping_sha256():
        raise ValueError("public preregistered subset mapping SHA-256 differs")
    return freeze


def verify_review_disposition(project_root: Path) -> ReviewDisposition:
    """Verify review readiness without reading or scoring hidden records."""

    freeze = verify_analysis_freeze(project_root)
    protocol = load_analysis_protocol(project_root)
    disposition = _load_canonical(
        project_root / REVIEW_DISPOSITION_RELATIVE,
        ReviewDisposition,
    )
    if (
        disposition.analysis_protocol_sha256 != freeze.analysis_protocol_sha256
        or disposition.analysis_freeze_sha256
        != _sha256(project_root / ANALYSIS_FREEZE_RELATIVE)
        or disposition.v1_termination_disposition_sha256
        != _sha256(project_root / V1_TERMINATION_RELATIVE)
        or disposition.execution_report_sha256 != protocol.execution_report_sha256
        or disposition.unblinding_record_sha256 != protocol.unblinding_record_sha256
        or disposition.ground_truth_pack_sha256 != protocol.ground_truth_pack_sha256
    ):
        raise ValueError("v2 review disposition binding differs")
    return disposition
