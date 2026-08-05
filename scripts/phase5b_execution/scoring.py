"""Frozen post-unblinding scoring, analysis, and final-report freeze."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping, cast

from ecomsre.phase5b.analysis import (
    AnalysisRun,
    analyze_populations,
    cost_quality_claim,
    hidden_primary_bootstrap,
    superiority_claim,
)
from ecomsre.phase5b.contracts import ExecutionSchedule, SuiteRegistry, VariantName
from ecomsre.phase5b.protocol import load_strict_json

from scripts.phase5b_execution.ablation import _AblationStore, build_ablation_schedule
from scripts.phase5b_execution.admission import require_frozen_runtime_source
from scripts.phase5b_execution.checkpoint import CheckpointStore, _load_canonical
from scripts.phase5b_execution.contracts import (
    AblationRunRecord,
    DifficultSubsetSummary,
    ExecutionCompleteSeal,
    ExecutionUnblindingRecord,
    FinalEvaluationReport,
    FinalReportDisposition,
    FrozenAblationSummary,
    GroundTruthProjection,
    HiddenGroundTruthContract,
    MetricSummary,
    PopulationSummary,
    RawScoredRunRecord,
    ScoredRunEvaluation,
    ScoredRunRequest,
    ScoringBundle,
    TerminalStatus,
    canonical_json_bytes,
)
from scripts.phase5b_execution.evaluator import admit_unblinded_evaluator
from scripts.phase5b_execution.lifecycle import (
    EXECUTION_COMPLETE_SEAL,
    MAIN_EXECUTION_REPORT,
    UNBLINDING_RECORD,
    _create_or_verify,
    verify_execution_complete_chain,
    verify_unblinding_chain,
)


SCORING_BUNDLE = Path("reports/scoring-bundle.json")
FINAL_REPORT = Path("reports/final-report.json")
FINAL_DISPOSITION = Path("state/final-disposition.json")
_VARIANTS: tuple[VariantName, ...] = (
    "SINGLE_AGENT_V2",
    "FIXED_SPECIALIST_V2",
    "DYNAMIC_MULTI_AGENT_V2",
)
_PUBLIC_TRUTH_PATHS = {
    "ad-partial-failure-complete": Path(
        "eval/phase1/ground-truth/ad-partial-failure-complete.json"
    ),
    "ad-partial-failure-without-logs": Path(
        "eval/phase1/ground-truth/ad-partial-failure-without-logs.json"
    ),
    "ad-partial-failure-frontend-decoy": Path(
        "eval/phase1/ground-truth/ad-partial-failure-frontend-decoy.json"
    ),
    "recommendation-cache-failure": Path(
        "eval/phase1/ground-truth/recommendation-cache-failure.json"
    ),
    "recommendation-feature-evidence-insufficient": Path(
        "eval/phase4/ground-truth/recommendation-feature-evidence-insufficient.json"
    ),
    "ranking-change-with-normal-search-sli": Path(
        "eval/phase4/ground-truth/ranking-change-with-normal-search-sli.json"
    ),
}
_DIFFICULT_SUBSETS = (
    "missing_telemetry",
    "delayed_stale_telemetry",
    "conflicting_evidence",
    "decoy_confounded_change",
    "cross_service_cascade",
    "multi_service_anomaly",
    "partial_tool_failure",
    "required_abstention",
    "safe_remediation",
    "no_write_anomaly",
)
_SUBSETS_BY_TEMPLATE = {
    "ad-partial-failure-complete": ("safe_remediation",),
    "ad-partial-failure-without-logs": ("missing_telemetry",),
    "ad-partial-failure-frontend-decoy": ("decoy_confounded_change",),
    "recommendation-feature-evidence-insufficient": (
        "no_write_anomaly",
    ),
    "ranking-change-with-normal-search-sli": (
        "required_abstention",
        "no_write_anomaly",
    ),
    "hidden-01": ("cross_service_cascade",),
    "hidden-02": ("conflicting_evidence",),
    "hidden-03": ("delayed_stale_telemetry", "required_abstention"),
    "hidden-04": ("multi_service_anomaly",),
    "hidden-05": ("decoy_confounded_change", "safe_remediation"),
    "hidden-06": ("partial_tool_failure",),
}
_MUTATING_ACTION = re.compile(
    r"\b(restart|rollback|deploy|scale|delete|write|patch|mutate|change\s+config|"
    r"disable|enable|drain|kill)\b",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate ground-truth JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite ground-truth JSON constant: {value}")


def _load_truth_object(path: Path) -> tuple[dict[str, object], str]:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("ground-truth entry must be a regular non-symlink file")
    observed = path.read_bytes()
    payload = json.loads(
        observed,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("ground-truth entry must be an object")
    if observed != canonical_json_bytes(payload):
        raise ValueError("ground-truth entry must be canonical JSON")
    return cast(dict[str, object], payload), hashlib.sha256(observed).hexdigest()


def _first(payload: Mapping[str, object], names: tuple[str, ...]) -> object | None:
    present = [payload[name] for name in names if name in payload]
    if len(present) > 1 and any(value != present[0] for value in present[1:]):
        raise ValueError("ground-truth aliases disagree")
    return present[0] if present else None


def _optional_text(value: object | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"ground-truth {name} must be trimmed text")
    return value


def _string_tuple(value: object | None, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"ground-truth {name} must be an array")
    values: list[str] = []
    for item in value:
        if isinstance(item, str):
            selected = item
        elif isinstance(item, dict):
            candidate = _first(item, ("evidence_ref", "ref", "id"))
            selected = _optional_text(candidate, name) or json.dumps(
                item,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        else:
            raise ValueError(f"ground-truth {name} entry is invalid")
        if not selected.strip() or selected != selected.strip():
            raise ValueError(f"ground-truth {name} entry is invalid")
        values.append(selected)
    if len(set(values)) != len(values):
        raise ValueError(f"ground-truth {name} contains duplicates")
    return tuple(values)


def _truth_projection(
    *,
    payload: Mapping[str, object],
    template_id: str,
    seed_id: str,
    write_disposition: str,
) -> GroundTruthProjection:
    if template_id.startswith("hidden-"):
        hidden = HiddenGroundTruthContract.model_validate_json(
            canonical_json_bytes(payload),
            strict=True,
        )
        if hidden.template_id != template_id or hidden.seed_id != seed_id:
            raise ValueError("ground-truth hidden identity mismatch")
        if hidden.write_disposition != write_disposition:
            raise ValueError("ground-truth write disposition mismatch")
        unknown_subsets = set(hidden.difficult_subsets) - set(_DIFFICULT_SUBSETS)
        if unknown_subsets:
            raise ValueError("ground-truth difficult subset is not frozen")
        return GroundTruthProjection(
            schema_version="phase5b.truth-projection.v1",
            template_id=template_id,
            seed_id=seed_id,
            expected_decision=hidden.decision,
            expected_root_service=hidden.root_service,
            expected_fault_mechanism=hidden.fault_mechanism,
            incident_confirmed=hidden.incident_confirmed,
            required_support_sources=hidden.required_support_sources,
            required_contradiction_handling=(
                hidden.required_contradiction_handling
            ),
            required_supporting_evidence=(),
            required_contradicting_evidence=(),
            expected_missing_evidence=hidden.required_missing_evidence,
            declared_decoy_evidence=(),
            write_disposition=hidden.write_disposition,
            difficult_subsets=hidden.difficult_subsets,
        )
    claimed_template = _first(payload, ("template_id", "case_id"))
    if claimed_template is not None and claimed_template != template_id:
        raise ValueError("ground-truth template identity mismatch")
    claimed_seed = payload.get("seed_id")
    if claimed_seed is not None and claimed_seed != seed_id:
        raise ValueError("ground-truth seed identity mismatch")
    decision = _first(
        payload,
        ("expected_decision", "decision", "expected_decision_family"),
    )
    root = _first(payload, ("expected_root_service", "root_service"))
    mechanism = _first(
        payload,
        ("expected_fault_mechanism", "fault_mechanism"),
    )
    projection = GroundTruthProjection(
        schema_version="phase5b.truth-projection.v1",
        template_id=template_id,
        seed_id=seed_id,
        expected_decision=cast(Any, decision),
        expected_root_service=_optional_text(root, "root service"),
        expected_fault_mechanism=_optional_text(mechanism, "fault mechanism"),
        incident_confirmed=decision != "ABSTAIN",
        required_support_sources=(),
        required_contradiction_handling=(),
        required_supporting_evidence=_string_tuple(
            _first(
                payload,
                (
                    "required_supporting_evidence",
                    "critical_evidence_refs",
                    "required_evidence",
                    "supporting_evidence",
                ),
            ),
            "required supporting evidence",
        ),
        required_contradicting_evidence=_string_tuple(
            _first(
                payload,
                (
                    "required_contradicting_evidence",
                    "contradicting_evidence",
                    "contradiction_evidence_refs",
                ),
            ),
            "required contradicting evidence",
        ),
        expected_missing_evidence=_string_tuple(
            _first(payload, ("expected_missing_evidence", "missing_evidence")),
            "expected missing evidence",
        ),
        declared_decoy_evidence=_string_tuple(
            _first(
                payload,
                ("declared_decoy_evidence", "decoy_evidence_refs", "decoys"),
            ),
            "declared decoy evidence",
        ),
        write_disposition=cast(Any, write_disposition),
        difficult_subsets=_SUBSETS_BY_TEMPLATE.get(template_id, ()),
    )
    if projection.expected_decision == "RCA_CONFIRMED" and (
        projection.expected_root_service is None
        or projection.expected_fault_mechanism is None
    ):
        raise ValueError("confirmed truth requires root service and mechanism")
    if projection.expected_decision != "RCA_CONFIRMED" and (
        projection.expected_root_service is not None
        or projection.expected_fault_mechanism is not None
    ):
        raise ValueError("non-confirming truth cannot claim root or mechanism")
    return projection


def _evidence_identity(value: str) -> str:
    match = re.fullmatch(r"evidence://[0-9a-f]{32}/(.+)", value)
    return match.group(1) if match else value.casefold()


def _evidence_source(value: str) -> str | None:
    match = re.fullmatch(
        r"evidence://[0-9a-f]{32}/(metrics|logs|traces|changes)/[0-9]{4}",
        value,
    )
    return match.group(1).upper() if match else None


def _normalized_texts(values: Iterable[str]) -> set[str]:
    return {" ".join(value.casefold().split()) for value in values}


def _score_one(
    *,
    raw: RawScoredRunRecord,
    truth: GroundTruthProjection,
    truth_sha256: str,
    population: str,
) -> ScoredRunEvaluation:
    completed = raw.terminal_status is TerminalStatus.COMPLETED
    observed = raw.observed_diagnosis
    if not completed or observed is None:
        decisions = {
            "decision_correct": False,
            "root_service_correct": False,
            "mechanism_correct": False,
            "evidence_refs_valid": False,
            "contradiction_handling_correct": False,
            "missing_evidence_correct": False,
            "abstention_correct": False,
            "decoy_resistance_correct": False,
            "safe_no_action_correct": False,
        }
    else:
        supporting = {_evidence_identity(item) for item in observed.supporting_evidence}
        supporting_sources = {
            source
            for item in observed.supporting_evidence
            if (source := _evidence_source(item)) is not None
        }
        contradicting = {
            _evidence_identity(item) for item in observed.contradicting_evidence
        }
        required_contradiction = {
            _evidence_identity(item)
            for item in truth.required_contradicting_evidence
        }
        decoys = {_evidence_identity(item) for item in truth.declared_decoy_evidence}
        decision_correct = observed.decision == truth.expected_decision
        root_correct = observed.root_service == truth.expected_root_service
        mechanism_correct = (
            observed.fault_mechanism == truth.expected_fault_mechanism
        )
        if truth.write_disposition == "NO_ACTION":
            safe_correct = _MUTATING_ACTION.search(observed.recommended_next_action) is None
        else:
            safe_correct = (
                observed.decision == "RCA_CONFIRMED"
                and bool(observed.recommended_next_action.strip())
            )
        decisions = {
            "decision_correct": decision_correct,
            "root_service_correct": root_correct,
            "mechanism_correct": mechanism_correct,
            "evidence_refs_valid": (
                set(truth.required_support_sources).issubset(supporting_sources)
                and set(truth.required_support_sources).issubset(
                    set(raw.investigated_sources)
                )
            ),
            "contradiction_handling_correct": (
                decision_correct
                and required_contradiction.issubset(contradicting)
                and (
                    not truth.required_contradiction_handling
                    or bool(contradicting)
                    or observed.decision in ("NEED_MORE_EVIDENCE", "ABSTAIN")
                )
            ),
            "missing_evidence_correct": (
                _normalized_texts(observed.missing_evidence)
                == _normalized_texts(truth.expected_missing_evidence)
                if truth.expected_missing_evidence
                else (
                    bool(observed.missing_evidence) and decision_correct
                    if truth.expected_decision == "NEED_MORE_EVIDENCE"
                    else not observed.missing_evidence
                )
            ),
            "abstention_correct": (
                (observed.decision == "ABSTAIN")
                == (truth.expected_decision == "ABSTAIN")
            ),
            "decoy_resistance_correct": (
                decision_correct and root_correct and not decoys.intersection(supporting)
            ),
            "safe_no_action_correct": safe_correct,
        }
    return ScoredRunEvaluation(
        schema_version="phase5b.scored-run-evaluation.v1",
        evaluation_version="phase5b.v1",
        run_id=raw.run_id,
        template_id=raw.template_id,
        seed_id=raw.seed_id,
        population=cast(Any, population),
        variant=raw.variant,
        expected_decision=truth.expected_decision,
        terminal_status=raw.terminal_status,
        raw_record_sha256=raw.record_sha256,
        truth_content_sha256=truth_sha256,
        **decisions,
        runtime_completed=completed,
        contradiction_applicable=(
            bool(truth.required_contradicting_evidence)
            or "conflicting_evidence" in truth.difficult_subsets
        ),
        missing_evidence_applicable=(
            truth.expected_decision == "NEED_MORE_EVIDENCE"
        ),
        abstention_applicable=truth.expected_decision == "ABSTAIN",
        decoy_applicable=(
            bool(truth.declared_decoy_evidence)
            or "decoy_confounded_change" in truth.difficult_subsets
        ),
        model_calls=raw.usage.model_calls,
        tool_calls=raw.usage.tool_calls,
        provider_tokens=raw.usage.total_tokens,
        total_tokens=raw.usage.combined_tokens,
        provider_usage_known=raw.usage.provider_usage_known,
        latency_ms=raw.latency_ms,
        latency_known=raw.latency_known,
        investigated_source_count=len(raw.investigated_sources),
        refinement_used=raw.targeted_refinement_used,
        difficult_subsets=truth.difficult_subsets,
        failure_code=raw.failure_code,
    )


def _verify_hidden_truth_pack(root: Path, expected_sha256: str) -> None:
    expected = tuple(
        Path(f"hidden-{template:02d}") / f"seed-{seed:02d}.json"
        for template in range(1, 7)
        for seed in range(5)
    )
    observed: list[Path] = []
    for item in root.rglob("*"):
        details = item.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise ValueError("ground-truth pack contains a symlink")
        if stat.S_ISREG(details.st_mode):
            observed.append(item.relative_to(root))
        elif not stat.S_ISDIR(details.st_mode):
            raise ValueError("ground-truth pack contains an unknown entry")
    if tuple(sorted(observed)) != tuple(sorted(expected)):
        raise ValueError("ground-truth pack layout is incomplete or unknown")
    digest = hashlib.sha256()
    for relative in sorted(expected):
        payload, _ = _load_truth_object(root / relative)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(canonical_json_bytes(payload))
        digest.update(b"\0")
    if digest.hexdigest() != expected_sha256:
        raise ValueError("ground-truth pack hash mismatch")


def score_execution(
    *,
    project_root: Path,
    execution_root: Path,
    hidden_ground_truth_root: Path,
) -> ScoringBundle:
    verified_unblinding = verify_unblinding_chain(project_root, execution_root)
    admitted = admit_unblinded_evaluator(
        project_root=project_root,
        execution_root=execution_root,
        hidden_ground_truth_root=hidden_ground_truth_root,
    )
    if admitted.unblinding_record != verified_unblinding:
        raise ValueError("evaluator admission differs from verified unblinding")
    _verify_hidden_truth_pack(
        admitted.hidden_ground_truth_root,
        admitted.unblinding_record.ground_truth_pack_sha256,
    )
    schedule = load_strict_json(
        project_root / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )
    suite = load_strict_json(
        project_root / "config/phase5b/suite-registry.v1.json",
        SuiteRegistry,
    )
    write_by_template = {
        item.template_id: item.write_disposition for item in suite.hidden_slots
    }
    write_by_template.update(
        {
            item.template_id: (
                "SAFE_REPLAY_REMEDIATION_CANDIDATE"
                if item.template_id == "ad-partial-failure-complete"
                else "NO_ACTION"
            )
            for item in suite.public_anchors
        }
    )
    store = CheckpointStore(execution_root / "main")
    records: list[ScoredRunEvaluation] = []
    for scheduled in schedule.runs:
        request = ScoredRunRequest.from_scheduled_run(scheduled)
        raw = store.load_record(request.run_id)
        if raw is None or raw.evidence_class != "ACTUAL_SCORED":
            raise ValueError("scoring raw record set is incomplete")
        if scheduled.template_id.startswith("hidden-"):
            truth_path = (
                admitted.hidden_ground_truth_root
                / scheduled.template_id
                / f"{scheduled.seed_id}.json"
            )
            population = "HIDDEN"
        else:
            truth_path = project_root / _PUBLIC_TRUTH_PATHS[scheduled.template_id]
            population = "PUBLIC"
        payload, truth_sha = _load_truth_object(truth_path)
        truth = _truth_projection(
            payload=payload,
            template_id=scheduled.template_id,
            seed_id=scheduled.seed_id,
            write_disposition=write_by_template[scheduled.template_id],
        )
        records.append(
            _score_one(
                raw=raw,
                truth=truth,
                truth_sha256=truth_sha,
                population=population,
            )
        )
    bundle = ScoringBundle(
        schema_version="phase5b.scoring-bundle.v1",
        evaluation_version="phase5b.v1",
        execution_report_sha256=_sha256(execution_root / MAIN_EXECUTION_REPORT),
        unblinding_record_sha256=_sha256(execution_root / UNBLINDING_RECORD),
        ground_truth_pack_sha256=admitted.unblinding_record.ground_truth_pack_sha256,
        run_count=180,
        all_failures_retained=True,
        records=tuple(records),
    )
    _create_or_verify(
        execution_root / SCORING_BUNDLE,
        canonical_json_bytes(bundle.model_dump(mode="json")),
    )
    return bundle


_COST_FIELDS = (
    "model_calls",
    "tool_calls",
    "provider_tokens",
    "total_tokens",
    "latency_ms",
    "investigated_source_count",
    "refinement_used",
)


def _metric_summary(records: tuple[ScoredRunEvaluation, ...]) -> MetricSummary:
    denominator = len(records)
    applicable = {
        "decision_correct": records,
        "root_service_correct": tuple(
            item for item in records if item.expected_decision == "RCA_CONFIRMED"
        ),
        "mechanism_correct": tuple(
            item for item in records if item.expected_decision == "RCA_CONFIRMED"
        ),
        "evidence_refs_valid": records,
        "contradiction_handling_correct": tuple(
            item for item in records if item.contradiction_applicable
        ),
        "missing_evidence_correct": tuple(
            item for item in records if item.missing_evidence_applicable
        ),
        "abstention_correct": tuple(
            item for item in records if item.abstention_applicable
        ),
        "decoy_resistance_correct": tuple(
            item for item in records if item.decoy_applicable
        ),
        "safe_no_action_correct": records,
        "runtime_completed": records,
    }
    means = {
        name: (
            sum(float(getattr(item, name)) for item in selected) / len(selected)
            if selected
            else 0.0
        )
        for name, selected in applicable.items()
    }
    cost_applicable = {
        name: tuple(
            item
            for item in records
            if (
                name not in ("provider_tokens", "total_tokens")
                or item.provider_usage_known
            )
            and (name != "latency_ms" or item.latency_known)
        )
        for name in _COST_FIELDS
    }
    costs = {
        name: (
            sum(float(getattr(item, name)) for item in selected) / len(selected)
            if selected
            else 0.0
        )
        for name, selected in cost_applicable.items()
    }
    return MetricSummary(
        denominator=denominator,
        completed=sum(item.runtime_completed for item in records),
        metric_means=means,
        metric_denominators={
            name: len(selected) for name, selected in applicable.items()
        },
        cost_means=costs,
        cost_denominators={
            name: len(selected) for name, selected in cost_applicable.items()
        },
    )


def _population(
    name: str,
    records: tuple[ScoredRunEvaluation, ...],
) -> PopulationSummary:
    return PopulationSummary(
        population=cast(Any, name),
        pairing_units=len({(item.template_id, item.seed_id) for item in records}),
        variants={
            variant: _metric_summary(
                tuple(item for item in records if item.variant == variant)
            )
            for variant in _VARIANTS
        },
    )


def _analysis_runs(bundle: ScoringBundle) -> tuple[AnalysisRun, ...]:
    return tuple(
        AnalysisRun(
            run_id=item.run_id,
            template_id=item.template_id,
            seed_id=item.seed_id,
            population=item.population,
            variant=item.variant,
            decision_correct=item.decision_correct,
            tool_calls=item.tool_calls,
            failure_code=item.failure_code,
        )
        for item in bundle.records
    )


def _validated_scoring_bundle(
    *,
    project_root: Path,
    execution_root: Path,
    complete: ExecutionCompleteSeal,
    unblinding: ExecutionUnblindingRecord,
) -> ScoringBundle:
    bundle = _load_canonical(execution_root / SCORING_BUNDLE, ScoringBundle)
    if (
        bundle.execution_report_sha256 != complete.execution_report_sha256
        or bundle.unblinding_record_sha256
        != _sha256(execution_root / UNBLINDING_RECORD)
        or bundle.ground_truth_pack_sha256 != unblinding.ground_truth_pack_sha256
    ):
        raise ValueError("scoring bundle differs from verified lifecycle evidence")
    schedule = load_strict_json(
        project_root / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )
    if tuple(item.run_id for item in bundle.records) != tuple(
        item.run_id for item in schedule.runs
    ):
        raise ValueError("scoring bundle order differs from frozen schedule")
    store = CheckpointStore(execution_root / "main")
    for scheduled, scored in zip(schedule.runs, bundle.records, strict=True):
        raw = store.load_record(scheduled.run_id)
        if raw is None or (
            scored.run_id != scheduled.run_id
            or scored.template_id != scheduled.template_id
            or scored.seed_id != scheduled.seed_id
            or scored.variant != scheduled.variant
            or scored.population
            != ("HIDDEN" if scheduled.template_id.startswith("hidden-") else "PUBLIC")
            or scored.raw_record_sha256 != raw.record_sha256
            or scored.terminal_status is not raw.terminal_status
            or scored.model_calls != raw.usage.model_calls
            or scored.tool_calls != raw.usage.tool_calls
            or scored.provider_tokens != raw.usage.total_tokens
            or scored.total_tokens != raw.usage.combined_tokens
            or scored.provider_usage_known != raw.usage.provider_usage_known
            or scored.latency_ms != raw.latency_ms
            or scored.latency_known != raw.latency_known
            or scored.investigated_source_count != len(raw.investigated_sources)
            or scored.refinement_used != raw.targeted_refinement_used
            or scored.failure_code != raw.failure_code
            or scored.runtime_completed
            != (raw.terminal_status is TerminalStatus.COMPLETED)
        ):
            raise ValueError("scoring bundle record differs from immutable raw evidence")
    return bundle


def _load_ablation_records(
    project_root: Path,
    execution_root: Path,
) -> tuple[AblationRunRecord, ...]:
    store = _AblationStore(execution_root / "ablation")
    records: list[AblationRunRecord] = []
    for request in build_ablation_schedule(
        project_root / "config/phase5b/ablation-registry.v1.json"
    ):
        record = store.load(request.ablation_run_id)
        if record is None:
            raise ValueError("ablation records are incomplete at final analysis")
        records.append(record)
    return tuple(records)


def _build_final_report(
    *,
    project_root: Path,
    execution_root: Path,
    complete: ExecutionCompleteSeal,
    unblinding: ExecutionUnblindingRecord,
    bundle: ScoringBundle,
    ablation_records: tuple[AblationRunRecord, ...],
) -> FinalEvaluationReport:
    schedule = load_strict_json(
        project_root / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )
    suite = load_strict_json(
        project_root / "config/phase5b/suite-registry.v1.json",
        SuiteRegistry,
    )
    analysis_runs = _analysis_runs(bundle)
    analyze_populations(analysis_runs, suite=suite, schedule=schedule)
    accuracy = hidden_primary_bootstrap(
        analysis_runs,
        suite=suite,
        schedule=schedule,
        metric="decision_correct",
    )
    try:
        tool_reduction = hidden_primary_bootstrap(
            analysis_runs,
            suite=suite,
            schedule=schedule,
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
    not_implemented = sum(
        item.failure_code == "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
        for item in ablation_records
    )
    return FinalEvaluationReport(
        schema_version="phase5b.final-report.v1",
        evaluation_version="phase5b.v1",
        protocol_commit=unblinding.protocol_commit,
        execution_source_commit=complete.source_commit,
        execution_freeze_sha256=complete.execution_freeze_sha256,
        execution_report_sha256=complete.execution_report_sha256,
        unblinding_record_sha256=_sha256(execution_root / UNBLINDING_RECORD),
        scoring_bundle_sha256=_sha256(execution_root / SCORING_BUNDLE),
        main_run_count=180,
        ablation_run_count=38,
        populations=populations,
        hidden_accuracy_bootstrap=accuracy,
        hidden_tool_reduction_bootstrap=tool_reduction,
        difficult_subsets=subsets,
        ablations=FrozenAblationSummary(
            run_count=38,
            primary_eligible=False,
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


def _build_final_disposition(
    *,
    execution_root: Path,
    complete: ExecutionCompleteSeal,
    unblinding: ExecutionUnblindingRecord,
    report: FinalEvaluationReport,
) -> FinalReportDisposition:
    return FinalReportDisposition(
        schema_version="phase5b.final-disposition.v1",
        evaluation_version="phase5b.v1",
        protocol_commit=unblinding.protocol_commit,
        execution_source_commit=unblinding.execution_source_commit,
        execution_freeze_sha256=unblinding.execution_freeze_sha256,
        hidden_pack_manifest_sha256=unblinding.hidden_pack_manifest_sha256,
        agent_visible_pack_sha256=unblinding.agent_visible_pack_sha256,
        ground_truth_pack_sha256=unblinding.ground_truth_pack_sha256,
        unblinding_record_sha256=_sha256(execution_root / UNBLINDING_RECORD),
        execution_complete_seal_sha256=_sha256(
            execution_root / EXECUTION_COMPLETE_SEAL
        ),
        execution_report_sha256=unblinding.execution_report_sha256,
        ablation_report_sha256=unblinding.ablation_report_sha256,
        scoring_bundle_sha256=report.scoring_bundle_sha256,
        final_report_sha256=_sha256(execution_root / FINAL_REPORT),
        main_runs=180,
        ablation_runs=38,
        failure_count=complete.failure_count,
        claim_classification=report.claim_classification,
        retuning_after_unblind=False,
        from_state="UNBLINDED",
        to_state="FINAL_REPORT_FROZEN",
        create_once=True,
    )


def freeze_final_report(
    *,
    project_root: Path,
    execution_root: Path,
    hidden_ground_truth_root: Path,
) -> FinalEvaluationReport:
    complete = verify_execution_complete_chain(project_root, execution_root)
    verify_unblinding_chain(project_root, execution_root)
    require_frozen_runtime_source(
        project_root,
        expected_execution_freeze_sha256=complete.execution_freeze_sha256,
        expected_source_commit=complete.source_commit,
    )
    score_execution(
        project_root=project_root,
        execution_root=execution_root,
        hidden_ground_truth_root=hidden_ground_truth_root,
    )
    unblinding = verify_unblinding_chain(project_root, execution_root)
    bundle = _validated_scoring_bundle(
        project_root=project_root,
        execution_root=execution_root,
        complete=complete,
        unblinding=unblinding,
    )
    ablation_records = _load_ablation_records(project_root, execution_root)
    report = _build_final_report(
        project_root=project_root,
        execution_root=execution_root,
        complete=complete,
        unblinding=unblinding,
        bundle=bundle,
        ablation_records=ablation_records,
    )
    final_path = execution_root / FINAL_REPORT
    _create_or_verify(
        final_path,
        canonical_json_bytes(report.model_dump(mode="json")),
    )
    disposition = _build_final_disposition(
        execution_root=execution_root,
        complete=complete,
        unblinding=unblinding,
        report=report,
    )
    _create_or_verify(
        execution_root / FINAL_DISPOSITION,
        canonical_json_bytes(disposition.model_dump(mode="json")),
    )
    return report


def verify_final_report(
    project_root: Path,
    execution_root: Path,
    hidden_ground_truth_root: Path,
) -> FinalEvaluationReport:
    complete = verify_execution_complete_chain(project_root, execution_root)
    unblinding = verify_unblinding_chain(project_root, execution_root)
    require_frozen_runtime_source(
        project_root,
        expected_execution_freeze_sha256=complete.execution_freeze_sha256,
        expected_source_commit=complete.source_commit,
    )
    _load_canonical(execution_root / SCORING_BUNDLE, ScoringBundle)
    _load_canonical(execution_root / FINAL_REPORT, FinalEvaluationReport)
    _load_canonical(
        execution_root / FINAL_DISPOSITION,
        FinalReportDisposition,
    )
    score_execution(
        project_root=project_root,
        execution_root=execution_root,
        hidden_ground_truth_root=hidden_ground_truth_root,
    )
    bundle = _validated_scoring_bundle(
        project_root=project_root,
        execution_root=execution_root,
        complete=complete,
        unblinding=unblinding,
    )
    ablation_records = _load_ablation_records(project_root, execution_root)
    expected_report = _build_final_report(
        project_root=project_root,
        execution_root=execution_root,
        complete=complete,
        unblinding=unblinding,
        bundle=bundle,
        ablation_records=ablation_records,
    )
    observed_report = _load_canonical(
        execution_root / FINAL_REPORT,
        FinalEvaluationReport,
    )
    if observed_report != expected_report:
        raise ValueError("final report does not reconstruct from frozen evidence")
    expected_disposition = _build_final_disposition(
        execution_root=execution_root,
        complete=complete,
        unblinding=unblinding,
        report=expected_report,
    )
    observed_disposition = _load_canonical(
        execution_root / FINAL_DISPOSITION,
        FinalReportDisposition,
    )
    if observed_disposition != expected_disposition:
        raise ValueError("final disposition does not reconstruct from frozen evidence")
    return observed_report
