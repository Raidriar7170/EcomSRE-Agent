"""Paired development metrics and bounded candidate selection."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Literal, Mapping

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre_rcaeval_adaptive.contracts import (
    AdaptiveTerminalRecord,
    AdaptiveTerminalStatus,
    EscalationRoute,
    V2Model,
)
from ecomsre_rcaeval_v2.schedule import CaseIdentity


class CountRate(V2Model):
    numerator: StrictInt = Field(ge=0)
    denominator: StrictInt = Field(ge=0)
    value: StrictFloat = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_exact_value(self) -> CountRate:
        expected = 0.0 if self.denominator == 0 else self.numerator / self.denominator
        if self.numerator > self.denominator or self.value != expected:
            raise ValueError("count rate differs from raw counts")
        return self


def _rate(numerator: int, denominator: int) -> CountRate:
    return CountRate(
        numerator=numerator,
        denominator=denominator,
        value=0.0 if denominator == 0 else numerator / denominator,
    )


class CaseOutcome(V2Model):
    case_key: str = Field(min_length=1, max_length=256)
    baseline_root_correct: StrictBool
    baseline_pair_correct: StrictBool
    initial_root_correct: StrictBool
    adaptive_root_correct: StrictBool
    adaptive_pair_correct: StrictBool
    completed: StrictBool
    terminal_status: AdaptiveTerminalStatus
    route: EscalationRoute
    tool_calls: StrictInt = Field(ge=0)
    semantic_operations: StrictInt = Field(ge=0)
    provider_attempts: StrictInt = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    known_token_lower_bound: StrictInt = Field(ge=0)
    conservative_token_upper_bound: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0.0)


class DesignAggregate(V2Model):
    scheduled: StrictInt = Field(ge=0)
    completed: StrictInt = Field(ge=0)
    root_service_correct: StrictInt = Field(ge=0)
    pair_correct: StrictInt = Field(ge=0)
    damage: StrictInt = Field(ge=0)
    damage_rate: CountRate
    rescue: StrictInt = Field(ge=0)
    rescue_rate: CountRate
    net_rescue: StrictInt
    zero_escalation: CountRate
    escalation_precision: CountRate
    escalation_recall: CountRate
    route_distribution: dict[EscalationRoute, StrictInt]
    mean_tools: StrictFloat = Field(ge=0.0)
    mean_semantic_operations: StrictFloat = Field(ge=0.0)
    provider_attempts: StrictInt = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    known_token_lower_bound: StrictInt = Field(ge=0)
    conservative_token_upper_bound: StrictInt = Field(ge=0)
    mean_latency_ms: StrictFloat = Field(ge=0.0)
    disqualifying_failure_count: StrictInt = Field(ge=0)
    minimum_gate_passed: StrictBool


def aggregate_outcomes(outcomes: tuple[CaseOutcome, ...]) -> DesignAggregate:
    if len({item.case_key for item in outcomes}) != len(outcomes):
        raise ValueError("adaptive evaluation outcomes must be uniquely paired")
    scheduled = len(outcomes)
    completed = sum(item.completed for item in outcomes)
    root_correct = sum(item.completed and item.adaptive_root_correct for item in outcomes)
    pair_correct = sum(item.completed and item.adaptive_pair_correct for item in outcomes)
    # Preserve the authoritative dev.3 Damage/Rescue endpoint: root-cause pair.
    baseline_correct = sum(item.baseline_pair_correct for item in outcomes)
    baseline_wrong = scheduled - baseline_correct
    damage = sum(
        item.baseline_pair_correct
        and (not item.completed or not item.adaptive_pair_correct)
        for item in outcomes
    )
    rescue = sum(
        not item.baseline_pair_correct
        and item.completed
        and item.adaptive_pair_correct
        for item in outcomes
    )
    escalated = tuple(
        item for item in outcomes if item.route is not EscalationRoute.DIRECT_RETURN
    )
    initial_wrong = tuple(item for item in outcomes if not item.initial_root_correct)
    escalated_initial_wrong = sum(not item.initial_root_correct for item in escalated)
    routes = Counter(item.route for item in outcomes)
    denominator = max(scheduled, 1)
    mean_semantic = sum(item.semantic_operations for item in outcomes) / denominator
    disqualifying_failures = sum(
        item.terminal_status
        in {
            AdaptiveTerminalStatus.INVALID_SCHEMA,
            AdaptiveTerminalStatus.PROTOCOL_VIOLATION,
            AdaptiveTerminalStatus.RUNTIME_CONTRACT_VIOLATION,
            AdaptiveTerminalStatus.INTERRUPTED,
        }
        for item in outcomes
    )
    aggregate = DesignAggregate(
        scheduled=scheduled,
        completed=completed,
        root_service_correct=root_correct,
        pair_correct=pair_correct,
        damage=damage,
        damage_rate=_rate(damage, baseline_correct),
        rescue=rescue,
        rescue_rate=_rate(rescue, baseline_wrong),
        net_rescue=rescue - damage,
        zero_escalation=_rate(
            routes[EscalationRoute.DIRECT_RETURN], scheduled
        ),
        escalation_precision=_rate(escalated_initial_wrong, len(escalated)),
        escalation_recall=_rate(escalated_initial_wrong, len(initial_wrong)),
        route_distribution={route: routes[route] for route in EscalationRoute},
        mean_tools=sum(item.tool_calls for item in outcomes) / denominator,
        mean_semantic_operations=mean_semantic,
        provider_attempts=sum(item.provider_attempts for item in outcomes),
        transport_retries=sum(item.transport_retries for item in outcomes),
        known_token_lower_bound=sum(
            item.known_token_lower_bound for item in outcomes
        ),
        conservative_token_upper_bound=sum(
            item.conservative_token_upper_bound for item in outcomes
        ),
        mean_latency_ms=sum(item.latency_ms for item in outcomes) / denominator,
        disqualifying_failure_count=disqualifying_failures,
        minimum_gate_passed=(
            completed >= 58
            and root_correct >= 50
            and pair_correct >= 28
            and damage <= 3
            and rescue > damage
            and routes[EscalationRoute.DIRECT_RETURN] >= 24
            and mean_semantic <= 3.0
            and disqualifying_failures == 0
        ),
    )
    return aggregate


class CandidateMetrics(V2Model):
    candidate_id: str = Field(pattern=r"^candidate-[1-3]$")
    minimum_gate_passed: StrictBool
    root_service_correct: StrictInt = Field(ge=0, le=60)
    pair_correct: StrictInt = Field(ge=0, le=60)
    net_rescue: StrictInt
    damage: StrictInt = Field(ge=0, le=60)
    mean_semantic_operations: StrictFloat = Field(ge=0.0)
    zero_escalation: StrictInt = Field(ge=0, le=60)


class AdaptiveCandidateFreeze(V2Model):
    schema_version: Literal[
        "rcaeval-single-first-adaptive.candidate-freeze.v1"
    ]
    evaluation_version: Literal["single-first-adaptive-v1"]
    implementation_git_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_id: str = Field(pattern=r"^candidate-[1-3]$")
    config_sha256: dict[Literal["agent", "evaluation", "model_lock"], str]
    model: Literal["gpt-5.4-mini-2026-03-17"]
    design_metrics: CandidateMetrics
    validation_split_id: Literal["DEV_VALIDATION"]

    @model_validator(mode="after")
    def require_selected_candidate_passed(self) -> AdaptiveCandidateFreeze:
        if (
            set(self.config_sha256) != {"agent", "evaluation", "model_lock"}
            or any(
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in self.config_sha256.values()
            )
            or self.design_metrics.candidate_id != self.candidate_id
            or not self.design_metrics.minimum_gate_passed
        ):
            raise ValueError("adaptive candidate freeze is not validation-ready")
        return self


def load_candidate_freeze(
    path: Path,
    *,
    expected_candidate_id: str,
    config_paths: Mapping[str, Path],
    repository_root: Path,
) -> AdaptiveCandidateFreeze:
    """Authorize validation before any validation schedule or case path is opened."""

    if not path.exists():
        raise FileNotFoundError("adaptive candidate freeze is required")
    if path.is_symlink() or not path.is_file():
        raise ValueError("adaptive candidate freeze path is invalid")
    repository_root = repository_root.resolve()
    expected_path = (
        repository_root
        / "config/rcaeval-adaptive-v1/adaptive-candidate.json"
    )
    if path.resolve() != expected_path:
        raise ValueError("adaptive candidate freeze path is not canonical")
    freeze_bytes = path.read_bytes()
    freeze = AdaptiveCandidateFreeze.model_validate_json(
        freeze_bytes
    )
    if freeze.candidate_id != expected_candidate_id:
        raise ValueError("adaptive candidate freeze selects a different candidate")
    if set(config_paths) != {"agent", "evaluation", "model_lock"}:
        raise ValueError("adaptive candidate freeze config scope is incomplete")
    observed = {
        name: hashlib.sha256(config_path.read_bytes()).hexdigest()
        for name, config_path in config_paths.items()
        if not config_path.is_symlink() and config_path.is_file()
    }
    if observed != freeze.config_sha256:
        raise ValueError("adaptive candidate freeze config hash drift")

    freeze_relative = path.resolve().relative_to(repository_root).as_posix()
    tracked = subprocess.run(
        ("git", "show", f"HEAD:{freeze_relative}"),
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if tracked.returncode != 0 or tracked.stdout != freeze_bytes:
        raise ValueError("adaptive candidate freeze differs from tracked HEAD blob")
    ancestor = subprocess.run(
        (
            "git",
            "merge-base",
            "--is-ancestor",
            freeze.implementation_git_sha,
            "HEAD",
        ),
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise ValueError("adaptive candidate implementation commit is not an ancestor")
    runtime_scopes = (
        "src/ecomsre_rcaeval_adaptive",
        "src/ecomsre_rcaeval_v2/dev3_provider.py",
        "scripts/rcaeval_adaptive",
    )
    for runtime_scope in runtime_scopes:
        present = subprocess.run(
            (
                "git",
                "cat-file",
                "-e",
                f"{freeze.implementation_git_sha}:{runtime_scope}",
            ),
            cwd=repository_root,
            check=False,
            capture_output=True,
        )
        if present.returncode != 0:
            raise ValueError("adaptive candidate frozen runtime scope is absent")
    runtime_diff = subprocess.run(
        (
            "git",
            "diff",
            "--quiet",
            freeze.implementation_git_sha,
            "--",
            *runtime_scopes,
        ),
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    runtime_status = subprocess.run(
        (
            "git",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *runtime_scopes,
        ),
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if (
        runtime_diff.returncode != 0
        or runtime_status.returncode != 0
        or runtime_status.stdout
    ):
        raise ValueError("adaptive candidate runtime differs from frozen commit")
    return freeze


def select_candidate(candidates: tuple[CandidateMetrics, ...]) -> CandidateMetrics | None:
    passing = tuple(item for item in candidates if item.minimum_gate_passed)
    if not passing:
        return None
    return max(
        passing,
        key=lambda item: (
            item.root_service_correct,
            item.pair_correct,
            item.net_rescue,
            -item.damage,
            -item.mean_semantic_operations,
            item.zero_escalation,
        ),
    )


class BaselineOutcome(V2Model):
    identity: CaseIdentity
    root_correct: StrictBool
    pair_correct: StrictBool


_TRUTH_INDICATOR = {
    "cpu": "cpu",
    "mem": "mem",
    "disk": "diskio",
    "delay": "latency",
    "loss": "latency",
    "socket": "socket",
}

_SMOKE_SYSTEMS = ("RE2-OB", "RE2-SS")
_SMOKE_FAULTS = ("cpu", "mem", "disk", "delay", "loss", "socket")


def validate_smoke_strata(identities: tuple[CaseIdentity, ...]) -> None:
    """Require one DESIGN smoke identity for each system/fault stratum."""

    observed = Counter((identity.system, identity.fault) for identity in identities)
    required = Counter(
        (system, fault)
        for system in _SMOKE_SYSTEMS
        for fault in _SMOKE_FAULTS
    )
    if len(identities) != 12 or len(set(identities)) != 12 or observed != required:
        raise ValueError(
            "adaptive smoke must cover both systems and all six faults exactly once"
        )


def load_design_baseline(path: Path) -> dict[CaseIdentity, BaselineOutcome]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("adaptive baseline outcome file is invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("outcomes") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError("adaptive baseline outcome set is invalid")
    output: dict[CaseIdentity, BaselineOutcome] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("variant") != "single_v1_reference":
            continue
        identity = CaseIdentity.model_validate(
            {
                "system": row.get("system"),
                "root_cause_service": row.get("root_cause_service"),
                "fault": row.get("fault"),
                "instance": row.get("instance"),
            }
        )
        predicted_service = row.get("predicted_service")
        root_correct = predicted_service == identity.root_cause_service
        pair_correct = root_correct and row.get("predicted_indicator") == (
            _TRUTH_INDICATOR[identity.fault]
        )
        if identity in output:
            raise ValueError("adaptive baseline contains a duplicate identity")
        output[identity] = BaselineOutcome(
            identity=identity,
            root_correct=root_correct,
            pair_correct=pair_correct,
        )
    if len(output) != 60:
        raise ValueError("adaptive DESIGN baseline must contain exactly 60 cases")
    return output


def score_adaptive_terminals(
    identities: tuple[CaseIdentity, ...],
    terminals: tuple[AdaptiveTerminalRecord, ...],
    *,
    baseline: Mapping[CaseIdentity, BaselineOutcome],
) -> tuple[CaseOutcome, ...]:
    if len(identities) != len(terminals) or len(set(identities)) != len(identities):
        raise ValueError("adaptive terminal scoring identity count differs")
    output: list[CaseOutcome] = []
    for identity, terminal in zip(identities, terminals, strict=True):
        reference = baseline.get(identity)
        if reference is None:
            raise ValueError("adaptive terminal lacks a paired baseline outcome")
        result = terminal.result
        completed = terminal.status is AdaptiveTerminalStatus.COMPLETED
        if completed != (result is not None):
            raise ValueError("adaptive terminal completion differs from result")
        initial_root = (
            False
            if result is None
            else result.diagnosis.initial_diagnosis.root_cause_service
            == identity.root_cause_service
        )
        adaptive_root = (
            False
            if result is None
            else result.diagnosis.final_root_service == identity.root_cause_service
        )
        adaptive_pair = (
            adaptive_root
            and result is not None
            and result.diagnosis.final_indicator == _TRUTH_INDICATOR[identity.fault]
        )
        route = (
            EscalationRoute.ESCALATE_BOTH
            if result is None
            else result.diagnosis.escalation_decision.route
        )
        accounting = terminal.attempt_accounting
        output.append(
            CaseOutcome(
                case_key="\0".join(
                    (
                        identity.system,
                        identity.root_cause_service,
                        identity.fault,
                        identity.instance,
                    )
                ),
                baseline_root_correct=reference.root_correct,
                baseline_pair_correct=reference.pair_correct,
                initial_root_correct=initial_root,
                adaptive_root_correct=adaptive_root,
                adaptive_pair_correct=adaptive_pair,
                completed=completed,
                terminal_status=terminal.status,
                route=route,
                tool_calls=0 if result is None else result.tool_calls,
                semantic_operations=(
                    4 if result is None else result.semantic_operations
                ),
                provider_attempts=accounting.provider_attempt_count,
                transport_retries=accounting.retry_attempt_count,
                known_token_lower_bound=accounting.known_token_lower_bound,
                conservative_token_upper_bound=(
                    accounting.conservative_token_upper_bound
                ),
                latency_ms=terminal.latency_ms,
            )
        )
    return tuple(output)


__all__ = [
    "AdaptiveCandidateFreeze",
    "BaselineOutcome",
    "CandidateMetrics",
    "CaseOutcome",
    "CountRate",
    "DesignAggregate",
    "aggregate_outcomes",
    "load_design_baseline",
    "load_candidate_freeze",
    "score_adaptive_terminals",
    "select_candidate",
    "validate_smoke_strata",
]
