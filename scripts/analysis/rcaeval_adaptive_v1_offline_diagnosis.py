"""Zero-Provider, post-hoc diagnosis of the frozen Adaptive v1 run.

The command reads only already-consumed OB/SS development records.  It never
constructs a Provider, never changes terminal records, and writes case-level
derived material only to an explicit path outside the repository.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from ecomsre_rcaeval.contracts import TerminalRecord, TerminalStatus
from ecomsre_rcaeval.scoring import normalize_indicator
from ecomsre_rcaeval_adaptive.contracts import (
    AdaptiveTerminalRecord,
    AdaptiveTerminalStatus,
    CausalRole,
    EscalationRoute,
    FusionAction,
    IndicatorResolutionAction,
)
from ecomsre_rcaeval_v2.dev3_execution import load_private_schedule
from ecomsre_rcaeval_v2.dev3_provider import RetryDecision, SemanticOperationRecord
from ecomsre_rcaeval_v2.dev3_schedule import Variant
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    ProviderAttemptRecord,
    ProviderAttemptStart,
)
from ecomsre_rcaeval_v2.dev_execution import discover_case_index
from ecomsre_rcaeval_v2.schedule import SplitName


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLASSIFICATION = (
    "POST_HOC_DEVELOPMENT_DIAGNOSTIC",
    "NO_PROVIDER_CALLS",
    "NOT_EXTERNAL_INFERENCE",
)
_PUBLIC_JSON = PROJECT_ROOT / "docs/analysis/rcaeval-adaptive-v1-offline-diagnosis.json"
_PUBLIC_MARKDOWN = PROJECT_ROOT / "docs/analysis/rcaeval-adaptive-v1-offline-diagnosis.md"
_FORBIDDEN_PUBLIC_KEYS = {
    "case_id",
    "run_id",
    "raw_provider_output",
    "private_path",
    "evidence_ref",
    "evidence_refs",
    "api_key",
    "authorization",
    "credentials",
}
_FORBIDDEN_PUBLIC_TEXT = (
    "/users/",
    "/home/",
    "/private/",
    "bearer ",
    "tt-case-",
)
_CONCRETE_REF = re.compile(
    r"(?:metric|log|trace|indicator):[0-9]{4}", re.IGNORECASE
)
_HTTP_429 = "HTTP_429"


@dataclass(frozen=True, slots=True)
class FailureContext:
    operation_role: str
    route: str | None
    route_disposition: str


@dataclass(frozen=True, slots=True)
class RunSidecar:
    operations: tuple[SemanticOperationRecord, ...]
    attempt_starts: tuple[ProviderAttemptStart, ...]
    attempts: tuple[ProviderAttemptRecord, ...]
    retry_decisions: tuple[RetryDecision, ...]


ModelT = TypeVar("ModelT", bound=BaseModel)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("diagnostic ratio counts are invalid")
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def assert_public_payload(payload: object) -> None:
    """Reject identifiers, local paths, raw output, and concrete references."""

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                lowered = str(key).casefold()
                if lowered in _FORBIDDEN_PUBLIC_KEYS:
                    raise ValueError(f"public payload contains forbidden key: {key}")
                walk(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            lowered = value.casefold()
            if any(marker in lowered for marker in _FORBIDDEN_PUBLIC_TEXT):
                raise ValueError("public payload contains forbidden local material")
            if _CONCRETE_REF.search(value):
                raise ValueError("public payload contains forbidden concrete reference")

    walk(payload)


def infer_failure_context(operation_types: Sequence[str]) -> FailureContext:
    """Recover only routes made provable by the persisted operation sequence."""

    operations = tuple(operation_types)
    if not operations or operations[0] != "FINAL_JUDGE":
        raise ValueError("Adaptive failure lacks the Initial operation")
    last = operations[-1]
    if last == "FINAL_JUDGE":
        role = "INITIAL_DIAGNOSIS" if len(operations) == 1 else "FUSION_JUDGE"
    elif last == "LOGS_SPECIALIST":
        role = "LOGS_VERIFIER"
    elif last == "TRACES_SPECIALIST":
        role = "TRACE_CAUSAL_SPECIALIST"
    else:
        raise ValueError("Adaptive failure has an unsupported operation type")
    if len(operations) == 1:
        return FailureContext(
            operation_role=role,
            route=None,
            route_disposition="UNAVAILABLE_INITIAL_FAILED_BEFORE_GATE",
        )
    has_logs = "LOGS_SPECIALIST" in operations
    has_traces = "TRACES_SPECIALIST" in operations
    if last == "LOGS_SPECIALIST" and not has_traces:
        return FailureContext(
            operation_role=role,
            route=None,
            route_disposition="UNAVAILABLE_AMBIGUOUS_LOGS_OR_BOTH",
        )
    if has_logs and has_traces:
        route = "VERIFY_BOTH"
    elif has_traces:
        route = "VERIFY_TRACES"
    elif has_logs and last == "FINAL_JUDGE":
        route = "VERIFY_LOGS"
    else:
        return FailureContext(
            operation_role=role,
            route=None,
            route_disposition="UNAVAILABLE_OPERATION_SEQUENCE_INCOMPLETE",
        )
    return FailureContext(
        operation_role=role,
        route=route,
        route_disposition="KNOWN_FROM_OPERATION_SEQUENCE",
    )


def analyze_arm_order(
    *,
    reference_intervals: Sequence[tuple[datetime, datetime]],
    adaptive_intervals: Sequence[tuple[datetime, datetime]],
    provider_attempt_intervals: Sequence[tuple[datetime, datetime]],
    first_adaptive_429_at: datetime | None,
) -> dict[str, Any]:
    if not reference_intervals or not adaptive_intervals:
        raise ValueError("arm-order analysis requires both execution arms")
    reference_first = min(item[0] for item in reference_intervals)
    reference_last = max(item[1] for item in reference_intervals)
    adaptive_first = min(item[0] for item in adaptive_intervals)
    adaptive_last = max(item[1] for item in adaptive_intervals)
    if any(ended < started for started, ended in (*reference_intervals, *adaptive_intervals)):
        raise ValueError("arm-order interval ends before it starts")
    overlap = reference_first < adaptive_last and adaptive_first < reference_last
    if any(ended < started for started, ended in provider_attempt_intervals):
        raise ValueError("Provider attempt interval ends before it starts")
    output: dict[str, Any] = {
        "timing_basis": "PROVIDER_ATTEMPT_ACTIVITY",
        "strategy_observed": "ALL_STRONG_SINGLE_THEN_ALL_ADAPTIVE",
        "reference_first_started_at_utc": _iso(reference_first),
        "reference_last_ended_at_utc": _iso(reference_last),
        "adaptive_first_started_at_utc": _iso(adaptive_first),
        "adaptive_last_ended_at_utc": _iso(adaptive_last),
        "all_reference_completed_before_adaptive_started": reference_last <= adaptive_first,
        "wall_clock_overlap": overlap,
        "provider_attempts_before_first_adaptive": sum(
            ended <= adaptive_first for _started, ended in provider_attempt_intervals
        ),
        "first_adaptive_429_at_utc": (
            None if first_adaptive_429_at is None else _iso(first_adaptive_429_at)
        ),
        "provider_attempts_before_first_adaptive_429": (
            None
            if first_adaptive_429_at is None
            else sum(
                ended < first_adaptive_429_at
                for _started, ended in provider_attempt_intervals
            )
        ),
    }
    return output


def _load_models(directory: Path, model: type[ModelT]) -> tuple[ModelT, ...]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"required diagnostic directory is invalid: {directory.name}")
    files = tuple(sorted(directory.glob("*.json")))
    output: list[ModelT] = []
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("diagnostic record must be a regular JSON file")
        output.append(model.model_validate_json(path.read_text(encoding="utf-8")))
    return tuple(output)


def _load_sidecar(root: Path, identifier: str) -> RunSidecar:
    run_root = root / "provider-sidecars" / identifier
    return RunSidecar(
        operations=tuple(
            sorted(
                _load_models(run_root / "semantic-operations", SemanticOperationRecord),
                key=lambda item: item.semantic_operation_index,
            )
        ),
        attempt_starts=tuple(
            sorted(
                _load_models(run_root / "provider-attempt-starts", ProviderAttemptStart),
                key=lambda item: item.provider_attempt_index,
            )
        ),
        attempts=tuple(
            sorted(
                _load_models(run_root / "provider-attempts", ProviderAttemptRecord),
                key=lambda item: item.provider_attempt_index,
            )
        ),
        retry_decisions=(
            ()
            if not (run_root / "retry-decisions").exists()
            else tuple(
                sorted(
                    _load_models(run_root / "retry-decisions", RetryDecision),
                    key=lambda item: item.semantic_operation_index,
                )
            )
        ),
    )


def _is_429(attempt: ProviderAttemptRecord) -> bool:
    return attempt.safe_http_status_class == _HTTP_429 or attempt.failure_code == _HTTP_429


def _operation_role(operation: SemanticOperationRecord) -> str:
    if operation.operation_type == "LOGS_SPECIALIST":
        return "LOGS_VERIFIER"
    if operation.operation_type == "TRACES_SPECIALIST":
        return "TRACE_CAUSAL_SPECIALIST"
    if operation.operation_type == "FINAL_JUDGE":
        return "INITIAL_DIAGNOSIS" if operation.semantic_operation_index == 1 else "FUSION_JUDGE"
    raise ValueError("Adaptive v1 diagnosis encountered an unexpected operation role")


def _route_name(route: EscalationRoute) -> str:
    return {
        EscalationRoute.DIRECT_RETURN: "DIRECT_RETURN",
        EscalationRoute.ESCALATE_LOGS: "VERIFY_LOGS",
        EscalationRoute.ESCALATE_TRACES: "VERIFY_TRACES",
        EscalationRoute.ESCALATE_BOTH: "VERIFY_BOTH",
    }[route]


def _counter(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): counter[key] for key in sorted(counter, key=str)}


def _fixed_counter(keys: Sequence[str], counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in keys}


def _build_diagnosis(
    *,
    run_root: Path,
    schedule_path: Path,
    ob_root: Path,
    ss_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    reference_root = run_root / "strong-single-reference"
    adaptive_root = run_root / "adaptive"
    reference = _load_models(reference_root / "terminal-records", TerminalRecord)
    adaptive = _load_models(adaptive_root / "terminal-records", AdaptiveTerminalRecord)
    if len(reference) != 120 or len(adaptive) != 120:
        raise ValueError("offline diagnosis requires the frozen 120-case arms")
    if len({item.case_id for item in reference}) != 120 or len({item.case_id for item in adaptive}) != 120:
        raise ValueError("offline diagnosis terminals are not uniquely paired")
    reference_by_case = {item.case_id: item for item in reference}
    adaptive_by_case = {item.case_id: item for item in adaptive}
    if set(reference_by_case) != set(adaptive_by_case):
        raise ValueError("offline diagnosis arms are not paired by case")
    if any(item.terminal_status is not TerminalStatus.COMPLETED for item in reference):
        raise ValueError("frozen Strong Single reference arm is incomplete")

    schedule = load_private_schedule(schedule_path, allowed_split=SplitName.DEV_VALIDATION)
    identities = tuple(
        item.identity for item in schedule if item.variant is Variant.SINGLE_V1_REFERENCE
    )
    if len(identities) != 120 or len(set(identities)) != 120:
        raise ValueError("offline diagnosis schedule lacks 120 unique reference identities")
    cases = discover_case_index(ob_root, ss_root, set(identities))
    truth_by_case = {case.case_id: case for case in cases.values()}
    if set(truth_by_case) != set(reference_by_case):
        raise ValueError("offline diagnosis truth pairing differs from frozen terminals")
    ordinal_by_case = {
        cases[identity].case_id: ordinal
        for ordinal, identity in enumerate(identities, start=1)
    }

    reference_sidecars = {
        item.run_id: _load_sidecar(reference_root, item.run_id) for item in reference
    }
    adaptive_sidecars = {
        item.run_id: _load_sidecar(adaptive_root, item.run_id) for item in adaptive
    }
    all_attempts = tuple(
        attempt
        for sidecars in (reference_sidecars, adaptive_sidecars)
        for sidecar in sidecars.values()
        for attempt in sidecar.attempts
    )
    adaptive_attempts = tuple(
        attempt for sidecar in adaptive_sidecars.values() for attempt in sidecar.attempts
    )
    first_429 = min(
        (attempt.ended_at_utc for attempt in adaptive_attempts if _is_429(attempt)),
        default=None,
    )
    arm_order = analyze_arm_order(
        reference_intervals=tuple(
            (attempt.started_at_utc, attempt.ended_at_utc)
            for sidecar in reference_sidecars.values()
            for attempt in sidecar.attempts
        ),
        adaptive_intervals=tuple(
            (attempt.started_at_utc, attempt.ended_at_utc)
            for sidecar in adaptive_sidecars.values()
            for attempt in sidecar.attempts
        ),
        provider_attempt_intervals=tuple(
            (attempt.started_at_utc, attempt.ended_at_utc) for attempt in all_attempts
        ),
        first_adaptive_429_at=first_429,
    )

    failure_context: dict[str, FailureContext] = {}
    for terminal in adaptive:
        if terminal.status is AdaptiveTerminalStatus.COMPLETED:
            continue
        failed_operations = adaptive_sidecars[terminal.run_id].operations
        failure_context[terminal.case_id] = infer_failure_context(
            tuple(item.operation_type for item in failed_operations)
        )

    role_429: Counter[str] = Counter()
    episode_role_429: Counter[str] = Counter()
    route_429: Counter[str] = Counter()
    bucket_429: Counter[str] = Counter()
    first_attempt_429 = 0
    retry_attempt_429 = 0
    for terminal in adaptive:
        sidecar = adaptive_sidecars[terminal.run_id]
        operations_by_index = {
            item.semantic_operation_index: item for item in sidecar.operations
        }
        episode_indexes: set[int] = set()
        for attempt in sidecar.attempts:
            if not _is_429(attempt):
                continue
            operation = operations_by_index[attempt.semantic_operation_index]
            role = _operation_role(operation)
            role_429[role] += 1
            new_episode = attempt.semantic_operation_index not in episode_indexes
            if new_episode:
                episode_role_429[role] += 1
                episode_indexes.add(attempt.semantic_operation_index)
            bucket_429[attempt.started_at_utc.strftime("%Y-%m-%dT%H:00Z")] += 1
            if attempt.retry_number == 0:
                first_attempt_429 += 1
            else:
                retry_attempt_429 += 1
            if new_episode:
                if terminal.status is AdaptiveTerminalStatus.COMPLETED:
                    assert terminal.result is not None
                    route_429[
                        _route_name(
                            terminal.result.diagnosis.escalation_decision.route
                        )
                    ] += 1
                else:
                    context = failure_context[terminal.case_id]
                    route_429[context.route or context.route_disposition] += 1
    recovered_429 = sum(
        operation.transport_recovered and operation.first_attempt_failure_code == _HTTP_429
        for sidecar in adaptive_sidecars.values()
        for operation in sidecar.operations
    )
    retry_issued_then_failed_again = sum(
        operation.status == "FAILED"
        and operation.failure_code == _HTTP_429
        and operation.retry_disposition == "RETRY_ISSUED"
        for sidecar in adaptive_sidecars.values()
        for operation in sidecar.operations
    )
    retry_dispositions = Counter(
        decision.disposition
        for sidecar in adaptive_sidecars.values()
        for decision in sidecar.retry_decisions
        if decision.eligible_failure_code == _HTTP_429
    )
    retry_waits = Counter(
        decision.retry_wait_ms
        for sidecar in adaptive_sidecars.values()
        for decision in sidecar.retry_decisions
        if decision.eligible_failure_code == _HTTP_429
    )

    initial_counts: Counter[str] = Counter()
    comparable: list[tuple[AdaptiveTerminalRecord, bool, bool, bool, bool]] = []
    gate_known: Counter[str] = Counter()
    gate_unavailable: Counter[str] = Counter()
    gate_provenance: Counter[str] = Counter()
    proxy_known: list[tuple[str, bool]] = []
    private_rows: list[dict[str, Any]] = []
    for case_id in sorted(adaptive_by_case, key=ordinal_by_case.__getitem__):
        baseline = reference_by_case[case_id]
        terminal = adaptive_by_case[case_id]
        truth = truth_by_case[case_id]
        assert baseline.diagnosis is not None
        baseline_correct = baseline.diagnosis.root_cause_service == truth.root_cause_service
        baseline_pair = baseline_correct and baseline.diagnosis.root_cause_indicator == normalize_indicator(truth.fault)
        private_row: dict[str, Any] = {
            "pair_ordinal": ordinal_by_case[case_id],
            "system": truth.system,
            "reference_root_correct": baseline_correct,
            "reference_pair_correct": baseline_pair,
            "adaptive_completed": terminal.status is AdaptiveTerminalStatus.COMPLETED,
            "http_429_terminal_failure": terminal.failure_code == _HTTP_429,
        }
        if terminal.status is AdaptiveTerminalStatus.COMPLETED:
            assert terminal.result is not None
            diagnosis = terminal.result.diagnosis
            initial_correct = diagnosis.initial_diagnosis.root_cause_service == truth.root_cause_service
            final_correct = diagnosis.final_root_service == truth.root_cause_service
            pair_correct = final_correct and diagnosis.final_indicator == normalize_indicator(truth.fault)
            same = diagnosis.initial_diagnosis.root_cause_service == baseline.diagnosis.root_cause_service
            initial_counts["same_root_service" if same else "different_root_service"] += 1
            if baseline_correct and initial_correct:
                initial_counts["both_correct"] += 1
            elif baseline_correct:
                initial_counts["reference_correct_initial_wrong"] += 1
            elif initial_correct:
                initial_counts["reference_wrong_initial_correct"] += 1
            else:
                initial_counts["both_wrong"] += 1
            route = _route_name(diagnosis.escalation_decision.route)
            gate_known[route] += 1
            gate_provenance["PERSISTED_TRUE"] += 1
            proxy_known.append((route, baseline_correct))
            comparable.append((terminal, baseline_correct, initial_correct, final_correct, pair_correct))
            private_row.update(
                {
                    "route": route,
                    "route_disposition": "KNOWN_FROM_TERMINAL_RESULT",
                    "reference_initial_same_root": same,
                    "initial_root_correct": initial_correct,
                    "final_root_correct": final_correct,
                    "final_pair_correct": pair_correct,
                }
            )
        else:
            context = failure_context[case_id]
            if context.route is None:
                gate_unavailable[context.route_disposition] += 1
                gate_provenance[context.route_disposition] += 1
            else:
                gate_known[context.route] += 1
                gate_provenance["SEQUENCE_INFERRED"] += 1
                proxy_known.append((context.route, baseline_correct))
            private_row.update(
                {
                    "failure_operation_role": context.operation_role,
                    "route": context.route,
                    "route_disposition": context.route_disposition,
                }
            )
        private_rows.append(private_row)

    escalated = [
        item
        for item in comparable
        if item[0].result is not None
        and item[0].result.diagnosis.escalation_decision.route is not EscalationRoute.DIRECT_RETURN
    ]
    direct = [item for item in comparable if item not in escalated]
    initial_wrong = sum(not item[2] for item in comparable)
    escalated_initial_wrong = sum(not item[2] for item in escalated)
    proxy_escalated = [item for item in proxy_known if item[0] != "DIRECT_RETURN"]
    proxy_reference_wrong = sum(not item[1] for item in proxy_known)
    proxy_escalated_reference_wrong = sum(not item[1] for item in proxy_escalated)

    specialist_hypotheses: Counter[str] = Counter()
    specialist_root_correct: Counter[str] = Counter()
    specialist_calls: Counter[str] = Counter()
    specialist_calls_with_correct_root: Counter[str] = Counter()
    specialist_supports_initial: Counter[str] = Counter()
    specialist_alternative: Counter[str] = Counter()
    causal_roles: dict[str, Counter[str]] = {"logs": Counter(), "traces": Counter()}
    final_changed_after_specialists = 0
    for terminal, _baseline_correct, _initial_correct, _final_correct, _pair_correct in comparable:
        assert terminal.result is not None
        diagnosis = terminal.result.diagnosis
        truth = truth_by_case[terminal.case_id]
        by_source: dict[str, list[Any]] = {"logs": [], "traces": []}
        for hypothesis in diagnosis.specialist_hypotheses:
            by_source[hypothesis.source].append(hypothesis)
            causal_roles[hypothesis.source][hypothesis.causal_role.value] += 1
            if hypothesis.causal_role is CausalRole.ROOT_CANDIDATE:
                specialist_hypotheses[hypothesis.source] += 1
                specialist_root_correct[hypothesis.source] += hypothesis.service == truth.root_cause_service
            if (
                hypothesis.causal_role is CausalRole.ROOT_CANDIDATE
                and hypothesis.service == diagnosis.initial_diagnosis.root_cause_service
                and hypothesis.supporting_evidence_refs
            ):
                specialist_supports_initial[hypothesis.source] += 1
            if (
                hypothesis.causal_role is CausalRole.ROOT_CANDIDATE
                and hypothesis.service
                != diagnosis.initial_diagnosis.root_cause_service
            ):
                specialist_alternative[hypothesis.source] += 1
        for source, hypotheses in by_source.items():
            if hypotheses:
                specialist_calls[source] += 1
                specialist_calls_with_correct_root[source] += any(
                    item.causal_role is CausalRole.ROOT_CANDIDATE
                    and item.service == truth.root_cause_service
                    for item in hypotheses
                )
        if diagnosis.specialist_hypotheses and diagnosis.final_root_service != diagnosis.initial_diagnosis.root_cause_service:
            final_changed_after_specialists += 1

    fusion_actions: Counter[str] = Counter()
    correct_overrides = 0
    wrong_overrides = 0
    guardrail_activations = 0
    guardrail_consistent = 0
    guardrail_inconsistent = 0
    keep_final_correct = 0
    for terminal, _baseline_correct, initial_correct, final_correct, _pair_correct in comparable:
        assert terminal.result is not None
        diagnosis = terminal.result.diagnosis
        decision = diagnosis.fusion_decision_or_none
        if decision is not None:
            fusion_actions[decision.action.value] += 1
            keep_final_correct += (
                decision.action is FusionAction.KEEP_INITIAL and final_correct
            )
            if decision.action is FusionAction.OVERRIDE_INITIAL:
                correct_overrides += final_correct and not initial_correct
                wrong_overrides += not final_correct
        traces = tuple(
            item
            for item in terminal.result.operation_trace
            if item.fusion_guardrail_applied
        )
        guardrail_activations += len(traces)
        for trace in traces:
            consistent = (
                decision is not None
                and decision.action is FusionAction.KEEP_INITIAL
                and diagnosis.final_root_service
                == diagnosis.initial_diagnosis.root_cause_service
                and decision.confidence == diagnosis.initial_diagnosis.confidence
                and not decision.contradicting_evidence_refs
                and "OVERLAPPING_EVIDENCE_REJECTED_KEEP_INITIAL"
                in decision.reason_codes
                and trace.overlap_count > 0
            )
            guardrail_consistent += consistent
            guardrail_inconsistent += not consistent
    fusion_rejections = Counter(
        terminal.failure_code or "UNKNOWN"
        for terminal in adaptive
        if terminal.status is not AdaptiveTerminalStatus.COMPLETED
        and failure_context[terminal.case_id].operation_role == "FUSION_JUDGE"
    )

    indicator_actions: Counter[str] = Counter()
    indicator_root_correct: Counter[str] = Counter()
    indicator_pair_correct: Counter[str] = Counter()
    root_correct_total = 0
    pair_correct_total = 0
    for terminal, _baseline_correct, _initial_correct, final_correct, pair_correct in comparable:
        assert terminal.result is not None
        action = terminal.result.diagnosis.indicator_resolution.action.value
        indicator_actions[action] += 1
        if final_correct:
            root_correct_total += 1
            indicator_root_correct[action] += 1
            indicator_pair_correct[action] += pair_correct
        pair_correct_total += pair_correct

    public = {
        "schema_version": "rcaeval-adaptive-v1.offline-diagnosis.v1",
        "classification": list(CLASSIFICATION),
        "provider_calls": 0,
        "scope": {
            "reference_terminals": len(reference),
            "adaptive_terminals": len(adaptive),
            "adaptive_completed": sum(item.status is AdaptiveTerminalStatus.COMPLETED for item in adaptive),
            "adaptive_terminal_failures": sum(item.status is not AdaptiveTerminalStatus.COMPLETED for item in adaptive),
            "paired_cases": len(reference_by_case),
        },
        "arm_order": arm_order,
        "http_429": {
            "terminal_failures": sum(item.failure_code == _HTTP_429 for item in adaptive),
            "episodes": sum(episode_role_429.values()),
            "attempts": first_attempt_429 + retry_attempt_429,
            "first_attempts": first_attempt_429,
            "retry_attempts": retry_attempt_429,
            "recovered_operations": recovered_429,
            "retry_issued_then_failed_again": retry_issued_then_failed_again,
            "episode_role_distribution": _fixed_counter(
                (
                    "INITIAL_DIAGNOSIS",
                    "LOGS_VERIFIER",
                    "TRACE_CAUSAL_SPECIALIST",
                    "FUSION_JUDGE",
                ),
                episode_role_429,
            ),
            "attempt_role_distribution": _fixed_counter(
                (
                    "INITIAL_DIAGNOSIS",
                    "LOGS_VERIFIER",
                    "TRACE_CAUSAL_SPECIALIST",
                    "FUSION_JUDGE",
                ),
                role_429,
            ),
            "route_or_unavailable_distribution": _counter(route_429),
            "timestamp_hour_distribution": _counter(bucket_429),
            "retry_after": {
                "saved_wait_ms_distribution": _counter(retry_waits),
                "retry_disposition_distribution": _counter(retry_dispositions),
                "provider_header_source_persisted": False,
                "disposition": "SOURCE_NOT_DISTINGUISHABLE_FROM_SAVED_SAFE_METADATA",
            },
        },
        "initial_comparison": {
            "comparable_completed_cases": len(comparable),
            "initial_unavailable_cases": len(adaptive) - len(comparable),
            **_fixed_counter(
                (
                    "same_root_service",
                    "different_root_service",
                    "both_correct",
                    "reference_correct_initial_wrong",
                    "reference_wrong_initial_correct",
                    "both_wrong",
                ),
                initial_counts,
            ),
            "interpretation": "POST_HOC_SELECTION_BIASED",
        },
        "gate_behavior": {
            "known_before_terminal_distribution": _fixed_counter(
                ("DIRECT_RETURN", "VERIFY_LOGS", "VERIFY_TRACES", "VERIFY_BOTH"),
                gate_known,
            ),
            "unavailable_distribution": _counter(gate_unavailable),
            "route_provenance_distribution": _counter(gate_provenance),
            "known_route_count": sum(gate_known.values()),
            "unavailable_route_count": sum(gate_unavailable.values()),
            "existing_reporting_default": {
                "route": "VERIFY_BOTH",
                "failure_count": sum(item.status is not AdaptiveTerminalStatus.COMPLETED for item in adaptive),
                "used_as_true_gate_route": False,
                "ignored_in_this_diagnostic": True,
            },
        },
        "escalation_quality": {
            "interpretation": "POST_HOC_SELECTION_BIASED",
            "comparable_completed_cases": len(comparable),
            "escalated_cases": len(escalated),
            "precision": _ratio(escalated_initial_wrong, len(escalated))
            | {"basis": "PERSISTED_ADAPTIVE_INITIAL"},
            "recall": _ratio(escalated_initial_wrong, initial_wrong)
            | {"basis": "PERSISTED_ADAPTIVE_INITIAL"},
            "direct_return_correctness": _ratio(sum(item[2] for item in direct), len(direct)),
            "escalated_initial_correctness": _ratio(sum(item[2] for item in escalated), len(escalated)),
            "historical_strong_single_proxy": {
                "known_route_cases": len(proxy_known),
                "precision": _ratio(
                    proxy_escalated_reference_wrong, len(proxy_escalated)
                ),
                "recall": _ratio(
                    proxy_escalated_reference_wrong, proxy_reference_wrong
                ),
                "basis": "HISTORICAL_STRONG_SINGLE_CORRECTNESS_PROXY",
            },
        },
        "specialist_utility": {
            "interpretation": "POST_HOC_SELECTION_BIASED",
            "completed_result_coverage": _ratio(len(comparable), len(adaptive)),
        }
        | {
            source: {
                "calls_with_persisted_hypotheses": specialist_calls[source],
                "calls_with_correct_root_candidate": specialist_calls_with_correct_root[source],
                "root_candidate_accuracy": _ratio(specialist_root_correct[source], specialist_hypotheses[source]),
                "causal_role_distribution": _fixed_counter(
                    ("ROOT_CANDIDATE", "PROPAGATED_SYMPTOM", "UNCERTAIN"),
                    causal_roles[source],
                ),
                "hypotheses_supporting_initial": specialist_supports_initial[source],
                "alternative_hypotheses": specialist_alternative[source],
            }
            for source in ("logs", "traces")
        }
        | {"final_changed_after_specialists": final_changed_after_specialists},
        "fusion_utility": {
            "interpretation": "POST_HOC_SELECTION_BIASED",
            "completed_result_coverage": _ratio(len(comparable), len(adaptive)),
            "action_distribution": _fixed_counter(
                ("KEEP_INITIAL", "OVERRIDE_INITIAL"), fusion_actions
            ),
            "keep_with_correct_final": keep_final_correct,
            "correct_overrides": correct_overrides,
            "wrong_overrides": wrong_overrides,
            "rejected_fusion_distribution": _counter(fusion_rejections),
            "unsafe_overrides_rejected": guardrail_activations,
            "guardrail_activations": guardrail_activations,
            "guardrail_trace_consistent": guardrail_consistent,
            "guardrail_trace_inconsistent": guardrail_inconsistent,
            "guardrail_recomputation": "TRACE_CONSISTENCY_RECOMPUTED",
            "proposal_overlap_recomputable": False,
        },
        "indicator_resolution": {
            "interpretation": "POST_HOC_SELECTION_BIASED",
            "completed_result_coverage": _ratio(len(comparable), len(adaptive)),
            "action_distribution": _fixed_counter(
                (
                    "KEEP_MODEL_INDICATOR",
                    "USE_DETERMINISTIC_TOP1",
                    "KEEP_MODEL_INDICATOR_WITH_UNCERTAINTY",
                ),
                indicator_actions,
            ),
            "root_correct": root_correct_total,
            "pair_correct": pair_correct_total,
            "pair_success_given_root_correct": _ratio(pair_correct_total, root_correct_total),
            "pair_success_given_root_correct_by_action": {
                action.value: _ratio(
                    indicator_pair_correct[action.value],
                    indicator_root_correct[action.value],
                )
                for action in IndicatorResolutionAction
            },
        },
        "design_inputs": {
            "strong_single_compatible_initial_required": True,
            "direct_return_should_be_default": True,
            "deterministic_fusion_required": True,
            "trace_trigger_should_be_strict": True,
            "provider_capacity_result_is_architecture_confounded": True,
        },
    }
    private = {
        "schema_version": "rcaeval-adaptive-v1.offline-diagnosis-private.v1",
        "classification": list(CLASSIFICATION),
        "cases": private_rows,
    }
    assert_public_payload(public)
    return public, private


def _render_markdown(public: Mapping[str, Any]) -> str:
    scope = public["scope"]
    order = public["arm_order"]
    http = public["http_429"]
    initial = public["initial_comparison"]
    gate = public["gate_behavior"]
    escalation = public["escalation_quality"]
    fusion = public["fusion_utility"]
    indicator = public["indicator_resolution"]
    return f"""# Adaptive v1 offline diagnosis

Classification: `POST_HOC_DEVELOPMENT_DIAGNOSTIC`, `NO_PROVIDER_CALLS`, `NOT_EXTERNAL_INFERENCE`.

This report recomputes aggregates from the preserved, already-consumed OB/SS development records. It does not rerun any case and does not provide external inference.

## Execution order and Provider capacity

- Strong Single terminals: {scope['reference_terminals']}; Adaptive terminals: {scope['adaptive_terminals']}.
- Adaptive completed: {scope['adaptive_completed']}; retained terminal failures: {scope['adaptive_terminal_failures']}.
- All Strong Single work completed before Adaptive began: {str(order['all_reference_completed_before_adaptive_started']).lower()}.
- Wall-clock arm overlap: {str(order['wall_clock_overlap']).lower()}.
- Strong Single window: {order['reference_first_started_at_utc']} to {order['reference_last_ended_at_utc']}.
- Adaptive window: {order['adaptive_first_started_at_utc']} to {order['adaptive_last_ended_at_utc']}.
- Provider attempts before Adaptive began: {order['provider_attempts_before_first_adaptive']}.
- First Adaptive HTTP 429: {order['first_adaptive_429_at_utc']}; prior cumulative attempts: {order['provider_attempts_before_first_adaptive_429']}.
- HTTP 429 episodes: {http['episodes']}; attempts: {http['attempts']} ({http['first_attempts']} first attempts, {http['retry_attempts']} retries); recovered operations: {http['recovered_operations']}; retry-issued then failed again: {http['retry_issued_then_failed_again']}.
- Saved retry waits do not preserve whether the value came from a Provider header, so that source is not inferred.

The Provider-capacity and temporal-order effects are confounded with architecture. Fixed-denominator failures remain valid for the executed protocol, but they are not a clean architecture-only reliability comparison.

## Agent behavior

- Initial comparison coverage: {initial['comparable_completed_cases']} completed Adaptive cases; interpretation: `{initial['interpretation']}`.
- Same Initial and Strong Single root: {initial.get('same_root_service', 0)}; different: {initial.get('different_root_service', 0)}.
- Both correct: {initial.get('both_correct', 0)}; Strong Single correct / Adaptive Initial wrong: {initial.get('reference_correct_initial_wrong', 0)}; Strong Single wrong / Adaptive Initial correct: {initial.get('reference_wrong_initial_correct', 0)}.
- Real Gate route known: {gate['known_route_count']}; unavailable: {gate['unavailable_route_count']}.
- The existing failure-reporting default assigned `VERIFY_BOTH` to {gate['existing_reporting_default']['failure_count']} failed terminals; it is not treated as a real Gate route here.
- Escalation precision: {escalation['precision']['numerator']}/{escalation['precision']['denominator']}; recall: {escalation['recall']['numerator']}/{escalation['recall']['denominator']}.
- Fusion actions: {json.dumps(fusion['action_distribution'], sort_keys=True)}; correct overrides: {fusion['correct_overrides']}; wrong overrides: {fusion['wrong_overrides']}; guardrail activations: {fusion['guardrail_activations']}.
- Indicator actions: {json.dumps(indicator['action_distribution'], sort_keys=True)}; Pair success conditional on correct Root: {indicator['pair_success_given_root_correct']['numerator']}/{indicator['pair_success_given_root_correct']['denominator']}.

Specialist, Fusion, and Indicator outputs exist only for the {scope['adaptive_completed']}/{scope['adaptive_terminals']} completed Adaptive records. Their aggregates are `POST_HOC_SELECTION_BIASED`; failed-record semantic outputs are unavailable and are not imputed.

## v2 implications

Use the Strong Single-compatible Initial, make direct return the conservative default, replace LLM Fusion with deterministic Fusion, and keep Trace verification behind a strict latency/socket or propagation trigger. These are development recommendations, not external claims.
"""


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--validation-schedule", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--public-json", type=Path, default=_PUBLIC_JSON)
    parser.add_argument("--public-markdown", type=Path, default=_PUBLIC_MARKDOWN)
    args = parser.parse_args(argv)
    provider_environment = sorted(key for key in os.environ if key.startswith("OPENAI_"))
    if provider_environment:
        raise ValueError("offline diagnosis requires all OPENAI environment variables unset")
    private_output = args.private_output.resolve()
    if private_output == PROJECT_ROOT or private_output.is_relative_to(PROJECT_ROOT):
        raise ValueError("case-level diagnostic output must remain outside Git")
    public, private = _build_diagnosis(
        run_root=args.run_root,
        schedule_path=args.validation_schedule,
        ob_root=args.ob_root,
        ss_root=args.ss_root,
    )
    markdown = _render_markdown(public)
    assert_public_payload(markdown)
    _write_json(args.public_json, public)
    args.public_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.public_markdown.write_text(markdown, encoding="utf-8")
    _write_json(private_output, private)
    print("POST_HOC_DEVELOPMENT_DIAGNOSTIC NO_PROVIDER_CALLS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
