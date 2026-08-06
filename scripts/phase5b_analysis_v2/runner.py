"""Review-gated runner boundaries for the Phase 5B v2 analysis."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
from pathlib import Path
import stat

from scripts.phase5b_analysis_v2.contracts import (
    ANALYSIS_VERSION,
    SUBSET_MAPPING_SOURCE,
    V2AnalysisAttempt,
    V2FinalDisposition,
    V2FinalEvaluationReport,
)
from scripts.phase5b_analysis_v2.evaluator import (
    V1AnalysisInputs,
    build_v2_scoring_bundle,
    verify_v1_analysis_inputs,
)
from scripts.phase5b_analysis_v2.freeze import (
    ANALYSIS_FREEZE_RELATIVE,
    REVIEW_DISPOSITION_RELATIVE,
    verify_review_disposition,
)
from scripts.phase5b_analysis_v2.protocol import ANALYSIS_PROTOCOL_RELATIVE
from scripts.phase5b_analysis_v2.reporting import build_v2_final_report
from scripts.phase5b_execution.checkpoint import _atomic_create
from scripts.phase5b_execution.contracts import canonical_json_bytes
from scripts.phase5b_execution.lifecycle import _create_or_verify


V2_ANALYSIS_AUTHORIZATION = "AUTHORIZE_PHASE5B_V2_ANALYSIS_ONLY"
V1_EXECUTION_AUTHORIZATION = "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION"
V2_ANALYSIS_ATTEMPT = Path("state/analysis-attempt.v2.json")
V2_SCORING_BUNDLE = Path("reports/scoring-bundle.v2.json")
V2_FINAL_REPORT = Path("reports/final-report.v2.json")
V2_FINAL_DISPOSITION = Path("state/final-disposition.v2.json")
_V2_OUTPUT_FILES = frozenset(
    (
        V2_ANALYSIS_ATTEMPT,
        V2_SCORING_BUNDLE,
        V2_FINAL_REPORT,
        V2_FINAL_DISPOSITION,
    )
)


def reject_forbidden_environment(environment: Mapping[str, str]) -> None:
    """Reject whole-pack locators and builder/evaluator role injection."""

    if any(
        marker in name.upper()
        for name in environment
        for marker in ("HIDDEN_PACK_ROOT", "BUILDER", "EVALUATOR")
    ):
        raise PermissionError("forbidden environment is present")


def require_v2_analysis_authorization(environment: Mapping[str, str]) -> None:
    """Require the exact, review-gated v2-only analysis authorization."""

    reject_forbidden_environment(environment)
    if (
        environment.get("PHASE5B_V2_ANALYSIS_AUTHORIZATION")
        != V2_ANALYSIS_AUTHORIZATION
    ):
        raise PermissionError("exact v2 analysis authorization is required")
    if environment.get("PHASE5B_EXECUTION_AUTHORIZATION") != V1_EXECUTION_AUTHORIZATION:
        raise PermissionError("exact v1 execution authorization is required")


def require_review_binding(
    *,
    reviewed_raw_record_manifest_sha256: str,
    admitted_raw_record_manifest_sha256: str,
) -> None:
    """Bind live preflight admission to the reviewed immutable raw manifest."""

    if reviewed_raw_record_manifest_sha256 != admitted_raw_record_manifest_sha256:
        raise ValueError("reviewed raw-record manifest differs from live admission")


def create_exclusive_analysis_attempt(path: Path, payload: bytes) -> None:
    """Create the pre-analysis marker exactly once; never verify-and-reuse it."""

    _atomic_create(path, payload)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def validate_separate_output_root(
    output_root: Path,
    protected_roots: Iterable[Path],
) -> Path:
    """Validate that v2 output cannot overlap any immutable input tree."""

    if not output_root.is_absolute():
        raise ValueError("v2 output root must be absolute")
    if output_root.exists() or output_root.is_symlink():
        details = output_root.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise ValueError("v2 output root must be a real directory")
        selected = output_root.resolve(strict=True)
    else:
        selected = output_root.resolve(strict=False)
    protected = tuple(root.resolve(strict=True) for root in protected_roots)
    if any(
        _is_within(selected, root) or _is_within(root, selected) for root in protected
    ):
        raise ValueError("v2 output root must remain separate from immutable inputs")
    return selected


def _verify_existing_output_surface(output_root: Path) -> None:
    if not output_root.exists():
        return
    observed_files: set[Path] = set()
    for item in output_root.rglob("*"):
        details = item.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise ValueError("v2 output surface cannot contain symlinks")
        if stat.S_ISREG(details.st_mode):
            relative = item.relative_to(output_root)
            if relative not in _V2_OUTPUT_FILES:
                raise ValueError("v2 output surface contains an unexpected file")
            observed_files.add(relative)
        elif not stat.S_ISDIR(details.st_mode):
            raise ValueError("v2 output surface contains an unknown entry")
    if observed_files and V2_ANALYSIS_ATTEMPT not in observed_files:
        raise ValueError("v2 output exists without an analysis-attempt marker")


def preflight_v2_analysis(
    *,
    project_root: Path,
    v1_source_root: Path,
    v1_execution_root: Path,
    hidden_ground_truth_root: Path,
) -> V1AnalysisInputs:
    """Verify all immutable inputs without scoring or creating output."""

    return verify_v1_analysis_inputs(
        project_root=project_root,
        v1_source_root=v1_source_root,
        v1_execution_root=v1_execution_root,
        hidden_ground_truth_root=hidden_ground_truth_root,
    )


def run_v2_analysis(
    *,
    project_root: Path,
    v1_source_root: Path,
    v1_execution_root: Path,
    hidden_ground_truth_root: Path,
    output_root: Path,
    environment: Mapping[str, str],
) -> tuple[V2FinalEvaluationReport, V2FinalDisposition]:
    """Run the one-time analysis-only repair after explicit human approval."""

    require_v2_analysis_authorization(environment)
    inputs = preflight_v2_analysis(
        project_root=project_root,
        v1_source_root=v1_source_root,
        v1_execution_root=v1_execution_root,
        hidden_ground_truth_root=hidden_ground_truth_root,
    )
    reviewed = verify_review_disposition(project_root)
    require_review_binding(
        reviewed_raw_record_manifest_sha256=reviewed.raw_record_manifest_sha256,
        admitted_raw_record_manifest_sha256=inputs.raw_record_manifest_sha256,
    )
    selected_output = validate_separate_output_root(
        output_root,
        (
            project_root,
            inputs.v1_source_root,
            inputs.v1_execution_root,
            inputs.hidden_ground_truth_root,
        ),
    )
    _verify_existing_output_surface(selected_output)

    protocol_sha256 = hashlib.sha256(
        (project_root / ANALYSIS_PROTOCOL_RELATIVE).read_bytes()
    ).hexdigest()
    freeze_sha256 = hashlib.sha256(
        (project_root / ANALYSIS_FREEZE_RELATIVE).read_bytes()
    ).hexdigest()
    review_sha256 = hashlib.sha256(
        (project_root / REVIEW_DISPOSITION_RELATIVE).read_bytes()
    ).hexdigest()
    attempt = V2AnalysisAttempt(
        schema_version="phase5b.analysis-attempt.v2",
        status="PHASE5B_V2_ANALYSIS_ATTEMPTED",
        analysis_version=ANALYSIS_VERSION,
        analysis_protocol_sha256=protocol_sha256,
        analysis_freeze_sha256=freeze_sha256,
        review_disposition_sha256=review_sha256,
        execution_report_sha256=inputs.protocol.execution_report_sha256,
        unblinding_record_sha256=inputs.protocol.unblinding_record_sha256,
        ground_truth_pack_sha256=inputs.protocol.ground_truth_pack_sha256,
        raw_record_manifest_sha256=inputs.raw_record_manifest_sha256,
        provider_calls=0,
        analysis_executed=False,
        create_once=True,
    )
    attempt_bytes = canonical_json_bytes(attempt.model_dump(mode="json"))
    create_exclusive_analysis_attempt(
        selected_output / V2_ANALYSIS_ATTEMPT,
        attempt_bytes,
    )

    bundle = build_v2_scoring_bundle(inputs)
    report = build_v2_final_report(inputs=inputs, bundle=bundle)
    bundle_bytes = canonical_json_bytes(bundle.model_dump(mode="json"))
    report_bytes = canonical_json_bytes(report.model_dump(mode="json"))
    _create_or_verify(selected_output / V2_SCORING_BUNDLE, bundle_bytes)
    _create_or_verify(selected_output / V2_FINAL_REPORT, report_bytes)

    disposition = V2FinalDisposition(
        schema_version="phase5b.final-disposition.v2",
        status="PHASE5B_V2_FINAL_REPORT_FROZEN",
        analysis_version=ANALYSIS_VERSION,
        input_evaluation_version="phase5b.v1",
        analysis_protocol_sha256=protocol_sha256,
        analysis_attempt_sha256=hashlib.sha256(attempt_bytes).hexdigest(),
        execution_report_sha256=inputs.protocol.execution_report_sha256,
        unblinding_record_sha256=inputs.protocol.unblinding_record_sha256,
        ground_truth_pack_sha256=inputs.protocol.ground_truth_pack_sha256,
        raw_record_manifest_sha256=inputs.raw_record_manifest_sha256,
        scoring_bundle_sha256=hashlib.sha256(bundle_bytes).hexdigest(),
        final_report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        main_runs=180,
        ablation_runs=38,
        failure_count=inputs.complete.failure_count,
        provider_calls=0,
        provider_reruns=0,
        diagnosis_output_modified=False,
        decision_root_mechanism_truth_modified=False,
        post_unblind_tuning=False,
        private_difficult_subsets_used=False,
        subset_mapping_source=SUBSET_MAPPING_SOURCE,
        primary_population="HIDDEN_ONLY",
        claim_classification=report.claim_classification,
        scoring_bundle_created=True,
        final_report_created=True,
        analysis_executed=True,
        create_once=True,
    )
    _create_or_verify(
        selected_output / V2_FINAL_DISPOSITION,
        canonical_json_bytes(disposition.model_dump(mode="json")),
    )
    return report, disposition
