"""Frozen v2 report construction over the repaired in-memory scoring bundle."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from ecomsre.phase5b.analysis import (
    analyze_populations,
    cost_quality_claim,
    hidden_primary_bootstrap,
    superiority_claim,
)

from scripts.phase5b_analysis_v2.contracts import (
    ANALYSIS_VERSION,
    SUBSET_MAPPING_SOURCE,
    V2FinalEvaluationReport,
    V2ScoringBundle,
)
from scripts.phase5b_analysis_v2.evaluator import V1AnalysisInputs
from scripts.phase5b_execution.contracts import (
    DifficultSubsetSummary,
    FrozenAblationSummary,
    TerminalStatus,
    canonical_json_bytes,
)
from scripts.phase5b_execution.scoring import (
    _DIFFICULT_SUBSETS,
    _VARIANTS,
    _analysis_runs,
    _load_ablation_records,
    _metric_summary,
    _population,
)


def build_v2_final_report(
    *,
    inputs: V1AnalysisInputs,
    bundle: V2ScoringBundle,
) -> V2FinalEvaluationReport:
    """Apply the frozen v1 statistics to v2-projected immutable evidence."""

    analysis_runs = _analysis_runs(bundle)
    analyze_populations(
        analysis_runs,
        suite=inputs.suite,
        schedule=inputs.schedule,
    )
    accuracy = hidden_primary_bootstrap(
        analysis_runs,
        suite=inputs.suite,
        schedule=inputs.schedule,
        metric="decision_correct",
    )
    try:
        tool_reduction = hidden_primary_bootstrap(
            analysis_runs,
            suite=inputs.suite,
            schedule=inputs.schedule,
            metric="relative_tool_reduction",
        )
    except ValueError:
        tool_reduction = None
    if superiority_claim(accuracy):
        classification = "HIDDEN_ACCURACY_SUPERIORITY_SUPPORTED"
    elif tool_reduction is not None and cost_quality_claim(accuracy, tool_reduction):
        classification = "HIDDEN_COST_QUALITY_ADVANTAGE_SUPPORTED"
    else:
        classification = "NO_PREREGISTERED_ADVANTAGE_SUPPORTED"

    hidden = tuple(item for item in bundle.records if item.population == "HIDDEN")
    public = tuple(item for item in bundle.records if item.population == "PUBLIC")
    populations = (
        _population("HIDDEN_ONLY_PRIMARY", hidden),
        _population("FULL_SUITE_SECONDARY", bundle.records),
        _population("PUBLIC_ANCHOR_DESCRIPTIVE", public),
    )
    subsets = tuple(
        DifficultSubsetSummary(
            subset=name,
            run_count=len(selected),
            variants={
                variant: _metric_summary(
                    tuple(item for item in selected if item.variant == variant)
                )
                for variant in _VARIANTS
            },
        )
        for name in _DIFFICULT_SUBSETS
        for selected in (
            tuple(item for item in bundle.records if name in item.difficult_subsets),
        )
    )
    ablation_records = _load_ablation_records(
        inputs.v1_source_root,
        inputs.v1_execution_root,
    )
    not_implemented = sum(
        item.failure_code == "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
        for item in ablation_records
    )
    bundle_sha256 = hashlib.sha256(
        canonical_json_bytes(bundle.model_dump(mode="json"))
    ).hexdigest()
    return V2FinalEvaluationReport(
        schema_version="phase5b.final-report.v2",
        evaluation_version="phase5b.v1",
        analysis_version=ANALYSIS_VERSION,
        input_evaluation_version="phase5b.v1",
        subset_mapping_source=SUBSET_MAPPING_SOURCE,
        private_difficult_subsets_used=False,
        provider_calls=0,
        protocol_commit=inputs.unblinding.protocol_commit,
        execution_source_commit=inputs.complete.source_commit,
        execution_freeze_sha256=inputs.complete.execution_freeze_sha256,
        execution_report_sha256=inputs.protocol.execution_report_sha256,
        unblinding_record_sha256=inputs.protocol.unblinding_record_sha256,
        scoring_bundle_sha256=bundle_sha256,
        main_run_count=180,
        ablation_run_count=38,
        main_evaluation_ready=True,
        ablation_slot_count=38,
        ablation_implementation_available=False,
        ablation_evidence_available=False,
        ablation_primary_eligible=False,
        ablation_disposition="ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS",
        populations=populations,
        hidden_accuracy_bootstrap=accuracy,
        hidden_tool_reduction_bootstrap=tool_reduction,
        difficult_subsets=subsets,
        ablations=FrozenAblationSummary(
            run_count=38,
            primary_eligible=False,
            ablation_slot_count=38,
            ablation_implementation_available=False,
            ablation_evidence_available=False,
            ablation_primary_eligible=False,
            ablation_disposition="ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS",
            implemented_run_count=38 - not_implemented,
            terminal_failure_count=sum(
                item.terminal_status is not TerminalStatus.COMPLETED
                for item in ablation_records
            ),
            provider_network_calls=sum(
                item.usage.provider_network_calls for item in ablation_records
            ),
            evidence_disposition=(
                "PRIMARY_INELIGIBLE_AND_NOT_IMPLEMENTED"
                if not_implemented
                else "PRIMARY_INELIGIBLE"
            ),
            remediation_metrics_status=(
                "NOT_EVALUABLE" if not_implemented else "EVALUATED"
            ),
            remediation_metrics={
                "correct_no_action_rate": None,
                "safe_action_accuracy": None,
                "unsafe_action_block_rate": None,
                "verification_accuracy": None,
                "rollback_success_rate": None,
            },
        ),
        claim_classification=cast(Any, classification),
        bootstrap_replicates=10000,
        bootstrap_rng_seed=20260804,
        confidence_interval=0.95,
        all_failures_retained=True,
        hidden_retry=False,
        scripted_fallback=False,
        replay_only=True,
        post_unblinding_tuning=False,
        live_mutation=False,
        production_claim=False,
    )
