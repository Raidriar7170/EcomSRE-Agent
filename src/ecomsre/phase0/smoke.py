"""One bounded, explicitly non-canonical live Phase 0 smoke loop."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.environment.readiness import CandidateReadinessPolicy
from ecomsre.evidence.hashes import (
    canonical_json_sha256,
    sha256_bytes,
    sha256_file,
)
from ecomsre.evidence.models import IntegrityManifest
from ecomsre.evidence.store import ObserverEvidenceStore, ReportEvidenceStore
from ecomsre.phase0.models import (
    CounterScope,
    CycleDecision,
    DiagnosticSmokePolicy,
    DiagnosticStatus,
    MeasurementPhase,
    MeasurementSource,
    Outcome,
    SmokeReport,
    SmokeAttemptEvidence,
    SmokeControlAcknowledgement,
    SmokePhaseEvidence,
    TerminalResult,
    WindowCounts,
    WindowDecision,
)
from ecomsre.phase0.statistics import evaluate_diagnostic_run, evaluate_window
from ecomsre.scenarios.ad_service_failure import AdServiceFailureController
from ecomsre.telemetry.http import OwnedHttpClient, PhaseWindow
from ecomsre.telemetry.jaeger import JaegerAdapter
from ecomsre.telemetry.opensearch import OpenSearchAdapter
from ecomsre.telemetry.probe import ProbeAdapter
from ecomsre.telemetry.prometheus import (
    FixtureState,
    FrozenTelemetryQueryCapability,
    PromotionAcquisitionPolicy,
    PrometheusAcquisitionPolicy,
    PrometheusAdapter,
    discover_and_freeze_registry,
    load_query_registry,
    publish_frozen_registry,
    revalidate_frozen_query_capability,
)

class SmokeExecutionError(RuntimeError):
    """A diagnostic smoke failed without claiming formal Phase 0 failure."""

    def __init__(self, reason_code: str, *, status: DiagnosticStatus) -> None:
        self.reason_code = reason_code
        self.status = status
        super().__init__(reason_code)


class EnvironmentStartDisposition(str, Enum):
    PRE_MUTATION_BLOCKED = "PRE_MUTATION_BLOCKED"
    MUTATION_MAY_HAVE_OCCURRED = "MUTATION_MAY_HAVE_OCCURRED"
    OWNED_ENVIRONMENT_STARTED = "OWNED_ENVIRONMENT_STARTED"


class SmokeEnvironmentStart(BaseModel):
    """Typed mutation boundary returned by the production start operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: TerminalResult
    disposition: EnvironmentStartDisposition

    @model_validator(mode="after")
    def require_consistent_disposition(self) -> "SmokeEnvironmentStart":
        if (
            self.result.outcome is Outcome.SUCCESS
        ) is not (
            self.disposition
            is EnvironmentStartDisposition.OWNED_ENVIRONMENT_STARTED
        ):
            raise ValueError("smoke start disposition conflicts with outcome")
        return self


class RecoveryReportRecord(BaseModel):
    """Append-only truth about a bounded post-terminal recovery action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.recovery-report.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=1, le=999)
    disposition: str = Field(min_length=1, max_length=80)
    reason_code: str = Field(min_length=1, max_length=120)
    canonical: Literal[False] = False
    phase0_complete: Literal[False] = False


class RecoverySealIndexEntry(BaseModel):
    """One immutable pointer; the last valid entry is the current seal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.recovery-seal-index.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=1, le=999)
    checksum_path: str
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    current: Literal[True] = True

    @model_validator(mode="after")
    def require_exact_checksum_path(self) -> "RecoverySealIndexEntry":
        expected = f"reports/{self.run_id}/seals/{self.sequence:03d}.sha256"
        if self.checksum_path != expected:
            raise ValueError("recovery seal checksum path is outside the allowlist")
        return self


@dataclass
class SmokeSupervisorState:
    """Mutable facts for exactly one supervised smoke attempt."""

    run_id: str
    environment_start_attempted: bool = False
    start_disposition: EnvironmentStartDisposition | None = None
    mutation_may_have_occurred: bool = False
    environment_started: bool = False
    control_open: bool = False
    fault_may_be_active: bool = False
    reset_attempted: bool = False
    reset_succeeded: bool = False
    stop_authority_fresh: bool = False
    stop_required: bool = False
    stop_attempted: bool = False
    stop_succeeded: bool = False
    owned_volume_cleanup_required: bool = False
    owned_volume_cleanup_attempted: bool = False
    owned_volume_cleanup_succeeded: bool = False
    records: dict[str, Any] = field(default_factory=dict)
    failure_reason_codes: list[str] = field(default_factory=list)
    failure_statuses: list[DiagnosticStatus] = field(default_factory=list)


class SmokeSupervisorOperations(Protocol):
    """Concrete Phase 0 operations used by the single smoke supervisor."""

    def start_environment(self) -> SmokeEnvironmentStart: ...

    def stabilize_initial_readiness(self, seconds: float) -> None: ...

    def fresh_authority(self, boundary: str) -> Any: ...

    def initial_readiness(self, authority: Any) -> Any: ...

    def open_control(self, authority: Any) -> Any: ...

    def promote(self, authority: Any, control: Any) -> Any: ...

    def frozen_readiness(self, authority: Any) -> Any: ...

    def diagnostic(self, authority: Any, control: Any) -> Any: ...

    def final_readiness(self, authority: Any) -> Any: ...

    def refresh_before_reset(self, control: Any) -> Any: ...

    def reset(self, control: Any) -> Any: ...

    def close_control(self, control: Any) -> None: ...

    def fresh_stop_authority(self) -> Any: ...

    def stop_environment(self, authority: Any) -> Any: ...

    def cleanup_owned_volumes(self, authority: Any) -> Any: ...

    def finalize(self, state: SmokeSupervisorState) -> Any: ...

    def write_minimal_terminal(
        self,
        state: SmokeSupervisorState,
        reason: str,
    ) -> None: ...


def supervise_smoke_attempt(
    *,
    run_id: str,
    operations: SmokeSupervisorOperations,
) -> Any:
    """Own the full up-to-report lifecycle and fail closed after every start."""
    state = SmokeSupervisorState(run_id=run_id)
    control: Any = None
    try:
        state.environment_start_attempted = True
        started = operations.start_environment()
        if not isinstance(started, SmokeEnvironmentStart):
            raise TypeError("smoke start result is not typed")
        state.start_disposition = started.disposition
        state.mutation_may_have_occurred = started.disposition in {
            EnvironmentStartDisposition.MUTATION_MAY_HAVE_OCCURRED,
            EnvironmentStartDisposition.OWNED_ENVIRONMENT_STARTED,
        }
        state.stop_required = state.mutation_may_have_occurred
        state.owned_volume_cleanup_required = state.stop_required
        if started.result.outcome is not Outcome.SUCCESS:
            _record_supervisor_result_failure(
                state,
                started.result,
                default_reason="ENVIRONMENT_START_FAILED",
            )
        else:
            state.environment_started = True
            operations.stabilize_initial_readiness(
                CandidateReadinessPolicy().initial_delay_seconds
            )
            initial = operations.fresh_authority("initial")
            operations.initial_readiness(initial)
            control = operations.open_control(initial)
            state.control_open = True
            state.fault_may_be_active = True
            state.records["promotion"] = operations.promote(initial, control)
            post_promotion = operations.fresh_authority("post-promotion")
            operations.frozen_readiness(post_promotion)
            state.records["diagnostic"] = operations.diagnostic(
                post_promotion,
                control,
            )
            final = operations.fresh_authority("final")
            operations.final_readiness(final)
    except SmokeExecutionError as error:
        _record_supervisor_failure(
            state,
            reason=error.reason_code,
            status=error.status,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        _record_supervisor_failure(
            state,
            reason=str(error) if str(error) else type(error).__name__,
            status=(
                DiagnosticStatus.FAILED
                if state.environment_started
                else DiagnosticStatus.BLOCKED
            ),
        )
    finally:
        if state.control_open:
            try:
                operations.refresh_before_reset(control)
                state.reset_attempted = True
                reset = operations.reset(control)
                state.reset_succeeded = (
                    getattr(reset, "outcome", None) is Outcome.SUCCESS
                )
                if state.reset_succeeded:
                    state.fault_may_be_active = False
                if not state.reset_succeeded:
                    _record_supervisor_result_failure(
                        state,
                        reset,
                        default_reason="SAFE_RESET_FAILED",
                    )
            except SmokeExecutionError as error:
                _record_supervisor_failure(
                    state,
                    reason=error.reason_code,
                    status=error.status,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                _record_supervisor_failure(
                    state,
                    reason=str(error) if str(error) else "SAFE_RESET_FAILED",
                    status=DiagnosticStatus.UNSAFE,
                )
            finally:
                try:
                    operations.close_control(control)
                except (OSError, RuntimeError, TypeError, ValueError) as error:
                    _record_supervisor_failure(
                        state,
                        reason=str(error) if str(error) else "CONTROL_CLOSE_FAILED",
                        status=DiagnosticStatus.UNSAFE,
                    )
                state.control_open = False
        if state.stop_required:
            try:
                stop_authority = operations.fresh_stop_authority()
                state.stop_authority_fresh = True
                if getattr(
                    stop_authority,
                    "evidence_persistence_error",
                    None,
                ):
                    _record_supervisor_failure(
                        state,
                        reason="STOP_AUTHORITY_OBSERVER_PERSISTENCE_FAILED",
                        status=DiagnosticStatus.UNSAFE,
                    )
                state.stop_attempted = True
                stopped = operations.stop_environment(stop_authority)
                state.stop_succeeded = (
                    getattr(stopped, "outcome", None) is Outcome.SUCCESS
                )
                if not state.stop_succeeded:
                    _record_supervisor_result_failure(
                        state,
                        stopped,
                        default_reason="SAFE_STOP_FAILED",
                    )
                else:
                    state.owned_volume_cleanup_attempted = True
                    cleaned = operations.cleanup_owned_volumes(stop_authority)
                    state.owned_volume_cleanup_succeeded = (
                        getattr(cleaned, "outcome", None) is Outcome.SUCCESS
                    )
                    if not state.owned_volume_cleanup_succeeded:
                        _record_supervisor_result_failure(
                            state,
                            cleaned,
                            default_reason="OWNED_VOLUME_CLEANUP_FAILED",
                        )
            except SmokeExecutionError as error:
                _record_supervisor_failure(
                    state,
                    reason=error.reason_code,
                    status=error.status,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                _record_supervisor_failure(
                    state,
                    reason=(
                        str(error) if str(error) else "SAFE_STOP_AUTHORITY_FAILED"
                    ),
                    status=DiagnosticStatus.UNSAFE,
                )
    state.failure_reason_codes = list(dict.fromkeys(state.failure_reason_codes))
    state.failure_statuses = list(dict.fromkeys(state.failure_statuses))
    try:
        return operations.finalize(state)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        reason = str(error) if str(error) else "REPORT_FINALIZATION_FAILED"
        try:
            operations.write_minimal_terminal(state, reason)
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
        raise


def _record_supervisor_result_failure(
    state: SmokeSupervisorState,
    result: Any,
    *,
    default_reason: str,
) -> None:
    outcome = getattr(result, "outcome", None)
    status = {
        Outcome.BLOCKED_ENVIRONMENT: DiagnosticStatus.BLOCKED,
        Outcome.BLOCKED_UPSTREAM: DiagnosticStatus.BLOCKED,
        Outcome.FAILED_ACCEPTANCE: DiagnosticStatus.FAILED,
        Outcome.UNSAFE: DiagnosticStatus.UNSAFE,
        Outcome.MANUAL_INTERVENTION_REQUIRED: DiagnosticStatus.UNSAFE,
        Outcome.INVALID_INVOCATION: DiagnosticStatus.BLOCKED,
    }.get(outcome, DiagnosticStatus.FAILED)
    _record_supervisor_failure(
        state,
        reason=str(getattr(result, "reason_code", default_reason)),
        status=status,
    )


def _record_supervisor_failure(
    state: SmokeSupervisorState,
    *,
    reason: str,
    status: DiagnosticStatus,
) -> None:
    state.failure_reason_codes.append(reason)
    state.failure_statuses.append(status)


def finalize_supervised_smoke(
    *,
    state: SmokeSupervisorState,
    artifacts_root: Path,
    policy: DiagnosticSmokePolicy | None = None,
) -> SmokeReport:
    """Derive and persist the sole non-canonical report from supervisor facts."""
    active_policy = policy or DiagnosticSmokePolicy()
    diagnostic = state.records.get("diagnostic")
    if (
        isinstance(diagnostic, tuple)
        and len(diagnostic) == 3
        and isinstance(diagnostic[0], dict)
        and isinstance(diagnostic[1], dict)
    ):
        phase_decisions = diagnostic[0]
        telemetry = diagnostic[1]
        diagnostic_passed = diagnostic[2] is True
    else:
        phase_decisions = {}
        telemetry = {
            "prometheus": False,
            "jaeger": False,
            "opensearch": False,
            "probe": False,
        }
        diagnostic_passed = False
    promotion = state.records.get("promotion")
    task7_frozen = (
        isinstance(promotion, FrozenTelemetryQueryCapability)
        and promotion.is_authentic()
    )
    failure_reasons = list(state.failure_reason_codes)
    if state.fault_may_be_active and not state.reset_succeeded:
        failure_reasons.append("SAFE_RESET_NOT_CONFIRMED")
    if state.stop_required and not state.stop_succeeded:
        failure_reasons.append("SAFE_STOP_NOT_CONFIRMED")
    if (
        state.owned_volume_cleanup_required
        and not state.owned_volume_cleanup_succeeded
    ):
        failure_reasons.append("OWNED_VOLUME_CLEANUP_NOT_CONFIRMED")
    typed_status = next(
        (
            status
            for status in (
                DiagnosticStatus.UNSAFE,
                DiagnosticStatus.FAILED,
                DiagnosticStatus.BLOCKED,
            )
            if status in state.failure_statuses
        ),
        None,
    )
    if typed_status is not None:
        status = typed_status
    elif (
        not failure_reasons
        and diagnostic_passed
        and all(telemetry.values())
        and task7_frozen
        and state.owned_volume_cleanup_succeeded
    ):
        status = DiagnosticStatus.PASSED
    elif diagnostic is not None:
        status = DiagnosticStatus.FAILED
        if not diagnostic_passed:
            failure_reasons.append("SMOKE_THRESHOLD_FAILED")
        if not all(telemetry.values()):
            failure_reasons.append("SMOKE_TELEMETRY_GATE_FAILED")
    else:
        status = DiagnosticStatus.BLOCKED
    attempt = _build_smoke_attempt_evidence(
        artifacts_root,
        state=state,
    )
    origin_run_id = (
        promotion.registry.promotion_proof.current_run_id
        if task7_frozen and promotion.registry.promotion_proof is not None
        else None
    )
    report = SmokeReport(
        schema_version="phase0.smoke-report.v1",
        run_id=state.run_id,
        canonical=False,
        diagnostic_status=status,
        phase0_complete=False,
        formal_three_cycle_acceptance_executed=False,
        policy=active_policy,
        phase_decisions=phase_decisions,
        telemetry_gate_decisions=telemetry,
        task7_registry_frozen=task7_frozen,
        origin_promotion_run_id=origin_run_id,
        attempts=(attempt,),
        safe_stop_completed=state.stop_succeeded,
        owned_volume_cleanup_completed=(
            state.owned_volume_cleanup_succeeded
        ),
        failure_reason_codes=tuple(dict.fromkeys(failure_reasons)),
    )
    with ReportEvidenceStore(artifacts_root, state.run_id) as reports:
        reports.write_smoke_report(report)
        reports.write_human_summary(report)
        content_hashes = _run_artifact_hashes(
            artifacts_root,
            run_id=state.run_id,
        )
        reports.write_checksums(
            IntegrityManifest(
                schema_version="phase0.integrity.v1",
                run_id=state.run_id,
                content_hashes=content_hashes,
                manifest_sha256=canonical_json_sha256(content_hashes),
            )
        )
    return report


def _build_smoke_attempt_evidence(
    artifacts_root: Path,
    *,
    state: SmokeSupervisorState,
) -> SmokeAttemptEvidence:
    observer = Path(artifacts_root) / "observer-visible" / state.run_id
    phases: list[SmokePhaseEvidence] = []
    for phase in MeasurementPhase:
        phase_root = observer / "cycles" / "001" / phase.value
        decision_path = phase_root / "decision.json"
        if not decision_path.is_file():
            continue
        try:
            decision = WindowDecision.model_validate_json(
                decision_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        raw_paths = tuple(
            str(path)
            for path in sorted(phase_root.rglob("*"))
            if path.is_file()
            and path.name not in {"decision.json", "control-ack.json"}
        )
        fixture_hashes = {
            value
            for path in phase_root.rglob("*.json")
            if path.is_file()
            for value in _json_values_for_key(path, "fixture_sha256")
            if isinstance(value, str) and len(value) == 64
        }
        fixture_sha256 = (
            next(iter(fixture_hashes))
            if len(fixture_hashes) == 1
            else None
        )
        phases.append(
            SmokePhaseEvidence(
                phase=phase,
                attempts=decision.counts.getads_attempts,
                errors=decision.counts.getads_errors,
                error_rate=decision.error_rate,
                wilson_lower=decision.wilson_lower,
                wilson_upper=decision.wilson_upper,
                window_started_at=decision.counts.window_started_at,
                window_ended_at=decision.counts.window_ended_at,
                monotonic_duration_seconds=(
                    decision.counts.monotonic_duration_seconds
                ),
                fixture_sha256=fixture_sha256,
                raw_artifact_refs=raw_paths,
                passed=decision.passed,
                reason_code=decision.reason_code,
            )
        )
    acknowledgements: list[SmokeControlAcknowledgement] = []
    ack_paths = (
        *sorted(
            (
                observer
                / "telemetry"
                / "promotion"
                / "transitions"
            ).glob("*.json")
        ),
        *sorted((observer / "cycles" / "001").glob("*/control-ack.json")),
        observer / "lifecycle" / "smoke-final-reset.json",
    )
    for path in ack_paths:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_bytes())
            acknowledgements.append(
                SmokeControlAcknowledgement(
                    stage=payload["stage"],
                    phase=payload.get("phase"),
                    transition_succeeded=payload["transition_succeeded"],
                    acknowledgement_duration_seconds=payload[
                        "acknowledgement_duration_seconds"
                    ],
                    reason_code=payload["reason_code"],
                    artifact_ref=str(path),
                )
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    initial = tuple(
        str(path)
        for path in sorted(
            path
            for path in (
                observer / "lifecycle" / "initial-readiness"
            ).glob("*/*.json")
            if path.name in {"summary.json", "pre-http-failure.json"}
        )
    )
    post = tuple(
        str(path)
        for path in sorted(
            (observer / "readiness-sessions").glob(
                "post-promotion-*/readiness-evidence.json"
            )
        )
    )
    final = tuple(
        str(path)
        for path in sorted(
            (observer / "readiness-sessions").glob("final-*/readiness-evidence.json")
        )
    )
    promotion = state.records.get("promotion")
    attribution: list[str] = []
    if (
        isinstance(promotion, FrozenTelemetryQueryCapability)
        and promotion.registry.promotion_proof is not None
    ):
        attribution.append(
            promotion.registry.promotion_proof.probe_getads_attribution_artifact
        )
    attribution.extend(
        str(path)
        for path in sorted((observer / "cycles" / "001").glob(
            "*/telemetry/probe/*"
        ))
        if path.is_file()
    )
    return SmokeAttemptEvidence(
        attempt_number=1,
        phase_evidence=tuple(phases),
        control_acknowledgements=tuple(acknowledgements),
        initial_readiness_artifacts=initial,
        post_promotion_readiness_artifacts=post,
        final_readiness_artifacts=final,
        probe_attribution_artifacts=tuple(attribution),
        safe_reset_attempted=state.reset_attempted,
        safe_reset_succeeded=state.reset_succeeded,
        fresh_stop_authority=state.stop_authority_fresh,
        safe_stop_required=state.stop_required,
        safe_stop_attempted=state.stop_attempted,
        safe_stop_succeeded=state.stop_succeeded,
        owned_volume_cleanup_required=state.owned_volume_cleanup_required,
        owned_volume_cleanup_attempted=state.owned_volume_cleanup_attempted,
        owned_volume_cleanup_succeeded=state.owned_volume_cleanup_succeeded,
        failure_reason_codes=tuple(state.failure_reason_codes),
    )


def _json_values_for_key(path: Path, key: str) -> tuple[object, ...]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ()
    found: list[object] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for current_key, nested in value.items():
                if current_key == key:
                    found.append(nested)
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)
    return tuple(found)


class _PromotionWindowProvider:
    """Create short, actual, non-overlapping promotion capture windows."""

    def __init__(
        self,
        *,
        run_id: str,
        controller: AdServiceFailureController,
        store: ObserverEvidenceStore,
        sleep: Callable[[float], None],
        before_mutation: Callable[[MeasurementPhase], None],
        duration_seconds: int = 30,
    ) -> None:
        self._run_id = run_id
        self._controller = controller
        self._store = store
        self._sleep = sleep
        self._before_mutation = before_mutation
        self._duration_seconds = duration_seconds
        self._previous_end = 0.0
        self._sequence = 0

    def __call__(self, phase: MeasurementPhase) -> PhaseWindow:
        remaining = self._previous_end - time.monotonic()
        if remaining >= 0:
            self._sleep(remaining + 0.001)
        execution = _confirmed_transition(
            self._controller,
            phase,
            before_mutation=self._before_mutation,
        )
        now_utc = datetime.now(UTC)
        now_monotonic = time.monotonic()
        window = PhaseWindow(
            run_id=self._run_id,
            cycle_number=1,
            scenario_phase=phase,
            utc_started_at=now_utc,
            utc_ended_at=now_utc + timedelta(seconds=self._duration_seconds),
            monotonic_started_at=now_monotonic,
            monotonic_ended_at=now_monotonic + self._duration_seconds,
        )
        self._previous_end = window.monotonic_ended_at
        self._sequence += 1
        self._store.write_immutable(
            (
                "telemetry/promotion/transitions/"
                f"{self._sequence:02d}-{phase.value}.json"
            ),
            {
                "schema_version": "phase0.promotion-transition.v1",
                "run_id": self._run_id,
                "stage": "promotion",
                "phase": phase.value,
                "transition_succeeded": True,
                "acknowledgement_duration_seconds": (
                    execution.observer_event.monotonic_duration_seconds
                ),
                "reason_code": execution.terminal_result.reason_code,
                "window": window.model_dump(mode="json"),
            },
        )
        return window


def promote_or_revalidate_registry(
    *,
    project_root: Path,
    store: ObserverEvidenceStore,
    client: OwnedHttpClient,
    controller: AdServiceFailureController,
    base_urls: dict[str, str],
    sleep: Callable[[float], None],
    before_mutation: Callable[[MeasurementPhase], None],
) -> FrozenTelemetryQueryCapability:
    """Promote once from UNRESOLVED, otherwise verify origin and revalidate live."""
    registry_path = (
        project_root / "config" / "phase0" / "telemetry-queries-v3.0.0.json"
    )
    loaded = load_query_registry(registry_path)
    promotion_policy = PromotionAcquisitionPolicy()
    provider = _PromotionWindowProvider(
        run_id=store.run_id,
        controller=controller,
        store=store,
        sleep=sleep,
        before_mutation=before_mutation,
        duration_seconds=promotion_policy.phase_window_seconds,
    )
    if loaded.registry.state is FixtureState.UNRESOLVED:
        source_sha256 = sha256_file(registry_path)
        capability = discover_and_freeze_registry(
            registry_path,
            evidence_store=store,
            client=client,
            phase_window_provider=provider,
            base_urls=base_urls,
            retry_policy=promotion_policy,
        )
        publish_frozen_registry(
            registry_path,
            capability=capability,
            expected_source_sha256=source_sha256,
        )
    elif loaded.registry.state is FixtureState.FROZEN:
        capability = None
        for phase in MeasurementPhase:
            window = provider(phase)
            capability = revalidate_frozen_query_capability(
                registry_path,
                evidence_store=store,
                client=client,
                window=window,
                probe_base_url=base_urls["probe"],
            )
        assert capability is not None
    else:
        raise SmokeExecutionError(
            "QUERY_REGISTRY_STATE_INVALID",
            status=DiagnosticStatus.BLOCKED,
        )
    if not capability.is_authentic():
        raise SmokeExecutionError(
            "TASK7_REGISTRY_NOT_FROZEN",
            status=DiagnosticStatus.BLOCKED,
        )
    return capability


def execute_diagnostic_cycle(
    *,
    store: ObserverEvidenceStore,
    client: OwnedHttpClient,
    capability: FrozenTelemetryQueryCapability,
    controller: AdServiceFailureController,
    base_urls: dict[str, str],
    policy: DiagnosticSmokePolicy,
    sleep: Callable[[float], None],
    before_mutation: Callable[[MeasurementPhase], None],
) -> tuple[dict[MeasurementPhase, bool], dict[str, bool], bool]:
    decisions = []
    telemetry = {
        "prometheus": True,
        "jaeger": True,
        "opensearch": True,
        "probe": True,
    }
    for phase in MeasurementPhase:
        transition = _confirmed_transition(
            controller,
            phase,
            before_mutation=before_mutation,
        )
        store.write_immutable(
            f"cycles/001/{phase.value}/control-ack.json",
            {
                "schema_version": "phase0.smoke-control-ack.v1",
                "run_id": store.run_id,
                "stage": "diagnostic",
                "phase": phase.value,
                "transition_succeeded": True,
                "acknowledgement_duration_seconds": (
                    transition.observer_event.monotonic_duration_seconds
                ),
                "reason_code": transition.terminal_result.reason_code,
            },
        )
        sleep(policy.stabilization_seconds)
        window = _fresh_window(
            run_id=store.run_id,
            phase=phase,
            seconds=policy.window_deadline_seconds,
        )
        prefix = f"cycles/001/{phase.value}"
        measurement = PrometheusAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
            acquisition_policy=PrometheusAcquisitionPolicy.diagnostic_smoke(),
        ).measure_getads(
            window=window,
            base_url=base_urls["prometheus"],
            artifact_prefix=prefix,
        )
        probe = ProbeAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).observe(
            window=window,
            base_url=base_urls["probe"],
            artifact_prefix=prefix,
        )
        jaeger = JaegerAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).check_readiness(
            window=window,
            base_url=base_urls["jaeger"],
            artifact_prefix=prefix,
        )
        opensearch = OpenSearchAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).check_readiness(
            window=window,
            base_url=base_urls["opensearch"],
            artifact_prefix=prefix,
        )
        receipts = {
            "prometheus": measurement.is_production_receipt(
                capability=capability, store=store, window=window
            ),
            "probe": probe.is_production_receipt(
                capability=capability, store=store
            ),
            "jaeger": jaeger.is_production_receipt(
                capability=capability, store=store, window=window
            ),
            "opensearch": opensearch.is_production_receipt(
                capability=capability, store=store, window=window
            ),
        }
        observed = {
            "prometheus": measurement.ready,
            "probe": probe.observed,
            "jaeger": jaeger.ready,
            "opensearch": opensearch.ready,
        }
        telemetry = {
            name: telemetry[name] and receipts[name] and observed[name]
            for name in telemetry
        }
        if (
            not measurement.ready
            or measurement.getads_attempts is None
            or measurement.getads_errors is None
            or measurement.start_sample_timestamp is None
            or measurement.end_sample_timestamp is None
        ):
            raise SmokeExecutionError(
                f"{phase.value.upper()}_PROMETHEUS_{measurement.reason.value}",
                status=DiagnosticStatus.FAILED,
            )
        counts = WindowCounts(
            run_id=store.run_id,
            window_id=secrets.token_hex(16),
            scenario_phase=phase,
            source=MeasurementSource.PROMETHEUS_GETADS,
            counter_scope=CounterScope.WINDOW_LOCAL_DELTA,
            window_started_at=measurement.start_sample_timestamp,
            window_ended_at=measurement.end_sample_timestamp,
            query_fixture_version=capability.registry.fixture_version,
            getads_attempts=measurement.getads_attempts,
            getads_errors=measurement.getads_errors,
            monotonic_duration_seconds=(
                measurement.end_sample_timestamp
                - measurement.start_sample_timestamp
            ).total_seconds(),
        )
        decision = evaluate_window(phase, counts, policy.as_phase0_policy())
        store.write_immutable(
            f"{prefix}/decision.json",
            decision.model_dump(mode="json"),
        )
        decisions.append(decision)
    cycle = CycleDecision(
        cycle_number=1,
        baseline=decisions[0],
        fault=decisions[1],
        recovery=decisions[2],
    )
    diagnostic = evaluate_diagnostic_run((cycle,), policy.as_phase0_policy())
    return (
        {
            MeasurementPhase.BASELINE: diagnostic.cycles[0].baseline.passed,
            MeasurementPhase.FAULT: diagnostic.cycles[0].fault.passed,
            MeasurementPhase.RECOVERY: diagnostic.cycles[0].recovery.passed,
        },
        telemetry,
        diagnostic.diagnostic_passed,
    )


def _confirmed_transition(
    controller: AdServiceFailureController,
    phase: MeasurementPhase,
    *,
    before_mutation: Callable[[MeasurementPhase], None],
):
    before_mutation(phase)
    execution = (
        controller.inject()
        if phase is MeasurementPhase.FAULT
        else controller.reset()
    )
    if execution.terminal_result.outcome is Outcome.SUCCESS:
        return execution
    status = (
        DiagnosticStatus.UNSAFE
        if execution.terminal_result.outcome
        in {Outcome.UNSAFE, Outcome.MANUAL_INTERVENTION_REQUIRED}
        else DiagnosticStatus.FAILED
    )
    raise SmokeExecutionError(execution.terminal_result.reason_code, status=status)


def _fresh_window(
    *,
    run_id: str,
    phase: MeasurementPhase,
    seconds: int,
) -> PhaseWindow:
    now_utc = datetime.now(UTC)
    now_monotonic = time.monotonic()
    return PhaseWindow(
        run_id=run_id,
        cycle_number=1,
        scenario_phase=phase,
        utc_started_at=now_utc,
        utc_ended_at=now_utc + timedelta(seconds=seconds),
        monotonic_started_at=now_monotonic,
        monotonic_ended_at=now_monotonic + seconds,
    )


def _run_artifact_hashes(
    artifacts_root: Path,
    *,
    run_id: str,
    excluded_report_path: str | None = None,
) -> dict[str, str]:
    """Hash final artifacts except the active seal and append-only seal index."""
    root = Path(artifacts_root)
    content_hashes: dict[str, str] = {}
    for zone in ("observer-visible", "evaluator-only", "reports"):
        run_root = root / zone / run_id
        if not run_root.is_dir() or run_root.is_symlink():
            raise ValueError(f"smoke evidence zone is unavailable: {zone}")
        for path in sorted(run_root.rglob("*")):
            if path.is_symlink():
                raise ValueError("smoke evidence cannot contain symlinks")
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if zone == "reports" and (
                    path.name == "seal-index.jsonl"
                    or relative == excluded_report_path
                ):
                    continue
                content_hashes[relative] = sha256_file(path)
    if not content_hashes:
        raise ValueError("smoke evidence hash set is empty")
    return content_hashes


def reseal_recovery_evidence(
    *,
    artifacts_root: Path,
    run_id: str,
    sequence: int,
    disposition: str,
    reason_code: str,
) -> RecoverySealIndexEntry:
    """Append a bounded recovery report, versioned checksum, and current pointer."""
    index_path = Path(artifacts_root) / "reports" / run_id / "seal-index.jsonl"
    prior_index_bytes, prior_entries = _validated_recovery_seal_index(
        index_path,
        run_id=run_id,
    )
    expected_sequence = (
        prior_entries[-1].sequence + 1 if prior_entries else 1
    )
    if sequence != expected_sequence:
        raise ValueError("recovery seal must use the next append-only sequence")
    recovery = RecoveryReportRecord(
        schema_version="phase0.recovery-report.v1",
        run_id=run_id,
        sequence=sequence,
        disposition=disposition,
        reason_code=reason_code,
    )
    with ReportEvidenceStore(artifacts_root, run_id) as reports:
        reports.write_recovery_report(sequence=sequence, value=recovery)
        current_checksum_relative = (
            f"reports/{run_id}/seals/{sequence:03d}.sha256"
        )
        content_hashes = _run_artifact_hashes(
            artifacts_root,
            run_id=run_id,
            excluded_report_path=current_checksum_relative,
        )
        manifest = IntegrityManifest(
            schema_version="phase0.integrity.v1",
            run_id=run_id,
            content_hashes=content_hashes,
            manifest_sha256=canonical_json_sha256(content_hashes),
        )
        checksum = reports.write_versioned_checksums(
            manifest,
            sequence=sequence,
        )
        entry = RecoverySealIndexEntry(
            schema_version="phase0.recovery-seal-index.v1",
            run_id=run_id,
            sequence=sequence,
            checksum_path=checksum.path.relative_to(
                Path(artifacts_root)
            ).as_posix(),
            checksum_sha256=checksum.sha256,
            content_manifest_sha256=manifest.manifest_sha256,
            prior_index_sha256=sha256_bytes(prior_index_bytes),
        )
        reports.append_seal_index(entry)
    return entry


def validate_current_recovery_seal(
    artifacts_root: Path,
    *,
    run_id: str,
) -> bool:
    """Validate the last append-only seal pointer against every final artifact."""
    root = Path(artifacts_root)
    index = root / "reports" / run_id / "seal-index.jsonl"
    try:
        _index_bytes, entries = _validated_recovery_seal_index(
            index,
            run_id=run_id,
        )
        if not entries:
            return False
        entry = entries[-1]
        expected_relative = f"reports/{run_id}/seals/{entry.sequence:03d}.sha256"
        if entry.checksum_path != expected_relative:
            return False
        checksum_path = root / entry.checksum_path
        if (
            not checksum_path.is_file()
            or checksum_path.is_symlink()
            or sha256_file(checksum_path) != entry.checksum_sha256
        ):
            return False
        recorded: dict[str, str] = {}
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            digest, separator, relative_path = line.partition("  ")
            if (
                separator != "  "
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not relative_path
                or relative_path in recorded
            ):
                return False
            recorded[relative_path] = digest
        actual = _run_artifact_hashes(
            root,
            run_id=run_id,
            excluded_report_path=entry.checksum_path,
        )
        return (
            recorded == actual
            and canonical_json_sha256(recorded)
            == entry.content_manifest_sha256
        )
    except (OSError, ValueError):
        return False


def _validated_recovery_seal_index(
    index_path: Path,
    *,
    run_id: str,
) -> tuple[bytes, tuple[RecoverySealIndexEntry, ...]]:
    if not index_path.exists() and not index_path.is_symlink():
        return b"", ()
    if index_path.is_symlink() or not index_path.is_file():
        raise ValueError("recovery seal index is invalid")
    raw = index_path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise ValueError("recovery seal index is incomplete")
    entries: list[RecoverySealIndexEntry] = []
    prior = b""
    for expected_sequence, line in enumerate(
        raw.splitlines(keepends=True),
        start=1,
    ):
        if not line.endswith(b"\n") or line == b"\n":
            raise ValueError("recovery seal index line is invalid")
        entry = RecoverySealIndexEntry.model_validate_json(line[:-1])
        if (
            entry.run_id != run_id
            or entry.sequence != expected_sequence
            or entry.prior_index_sha256 != sha256_bytes(prior)
        ):
            raise ValueError("recovery seal index chain is invalid")
        entries.append(entry)
        prior += line
    return raw, tuple(entries)
