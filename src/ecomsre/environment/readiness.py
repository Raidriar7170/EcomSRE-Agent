"""Fresh production readiness composition for one Phase 0 command process."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.environment.lifecycle import ReadinessEvidence
from ecomsre.environment.ownership_authority import AuthenticatedOwnershipContext
from ecomsre.environment.preflight import AuthenticatedPreflightEvidence
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.evidence.hashes import canonical_json_bytes
from ecomsre.phase0.models import MeasurementPhase
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    OwnedEndpoint,
    OwnedHttpClient,
    PhaseWindow,
)
from ecomsre.telemetry.jaeger import JaegerAdapter
from ecomsre.telemetry.opensearch import OpenSearchAdapter
from ecomsre.telemetry.opensearch_identity import (
    parse_opensearch_service_identity,
)
from ecomsre.telemetry.probe import (
    ReadinessGateName,
    acquire_collector_pipeline_receipt,
    acquire_load_generator_telemetry_receipt,
    build_readiness_handoff,
    create_authenticated_lifecycle_runner,
    derive_current_resource_discovery,
    derive_service_readiness_proof,
    evaluate_backend_readiness,
    evaluate_collector_readiness,
    evaluate_load_generator_readiness,
    evaluate_ownership_resources,
    execute_lifecycle_readiness,
    _jaeger_trace_proves_load_generator_and_getads,
    _parse_service_container_inspect,
)
from ecomsre.environment.ownership import verify_owned_resources
from ecomsre.telemetry.prometheus import _verify_direct_ad_array
from ecomsre.telemetry.prometheus import (
    FixtureState,
    PrometheusAdapter,
    load_query_registry,
    revalidate_frozen_query_capability,
)


class ReadinessCollectionError(RuntimeError):
    """Fresh readiness could not be proven without widening authority."""

    def __init__(
        self,
        reason_code: str,
        *,
        artifact_path: str | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.artifact_path = artifact_path
        super().__init__(reason_code)


class CandidateEndpointPreHttpDiagnostic(BaseModel):
    """Typed endpoint truth when readiness aborts before any HTTP attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    http_status: Literal["NOT_ATTEMPTED"] = "NOT_ATTEMPTED"
    transport_reason: Literal["NOT_ATTEMPTED"] = "NOT_ATTEMPTED"
    raw_artifact: None = None
    parse_reason: Literal["NOT_ATTEMPTED_NO_HTTP_RESPONSE"] = (
        "NOT_ATTEMPTED_NO_HTTP_RESPONSE"
    )
    freshness_reason: str = Field(min_length=1)


class CandidatePropagationPreHttpDiagnostic(BaseModel):
    """Typed propagation truth when no underlying check was attempted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_artifact: None = None
    parse_reason: Literal["NOT_ATTEMPTED"] = "NOT_ATTEMPTED"
    freshness_reason: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


class CandidateReadinessPreHttpFailure(BaseModel):
    """Machine-readable gate matrix for a readiness failure before HTTP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.candidate-readiness-pre-http-failure.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ownership_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: Literal["INITIAL", "CONTROL_MUTATION"]
    reason_code: str = Field(min_length=1)
    failure_detail: str = Field(min_length=1)
    attempt_count: Literal[0] = 0
    endpoint_gates: dict[str, Literal["NOT_EVALUATED"]]
    propagation_gates: dict[str, Literal["NOT_EVALUATED"]]
    endpoint_diagnostics: dict[str, CandidateEndpointPreHttpDiagnostic]
    propagation_diagnostics: dict[
        str,
        CandidatePropagationPreHttpDiagnostic,
    ]
    raw_artifacts: tuple[()] = ()

    @model_validator(mode="after")
    def require_exact_gate_diagnostics(
        self,
    ) -> "CandidateReadinessPreHttpFailure":
        if (
            set(self.endpoint_gates) != _CANDIDATE_ENDPOINT_NAMES
            or set(self.endpoint_diagnostics) != _CANDIDATE_ENDPOINT_NAMES
        ):
            raise ValueError("endpoint diagnostic keys are not exact")
        if (
            set(self.propagation_gates) != _CANDIDATE_PROPAGATION_NAMES
            or set(self.propagation_diagnostics)
            != _CANDIDATE_PROPAGATION_NAMES
        ):
            raise ValueError("propagation diagnostic keys are not exact")
        return self


_CANDIDATE_TRANSPORT_FAILURE_REASONS = frozenset(
    {
        "RESOURCE_OWNERSHIP_UNKNOWN",
        "HTTP_DEADLINE_EXCEEDED",
        "HTTP_TRANSPORT_ERROR",
    }
)
_CANDIDATE_PARSE_FAILURE_REASONS = frozenset(
    {
        "PROMETHEUS_JSON_INVALID",
        "PROMETHEUS_SCHEMA_INVALID",
        "PROMETHEUS_IDENTITY_MISMATCH",
        "JAEGER_JSON_INVALID",
        "JAEGER_SCHEMA_INVALID",
        "OPENSEARCH_JSON_INVALID",
        "OPENSEARCH_SCHEMA_INVALID",
        "OPENSEARCH_EMPTY_HIT_SET",
        "OPENSEARCH_IDENTITY_MISMATCH",
        "OPENSEARCH_TIMESTAMP_INVALID",
        "OPENSEARCH_SERVICE_IDENTITY_MISSING",
        "OPENSEARCH_SERVICE_IDENTITY_TYPE_INVALID",
        "OPENSEARCH_SERVICE_IDENTITY_SHAPE_INVALID",
        "OPENSEARCH_SERVICE_IDENTITY_CONFLICT",
        "OPENSEARCH_SERVICE_IDENTITY_FIELD_UNSUPPORTED",
        "PROBE_JSON_INVALID",
        "PROBE_SCHEMA_INVALID",
    }
)
_CANDIDATE_PARSED_FRESHNESS_CHAINS = frozenset(
    {
        (
            "PROMETHEUS_IDENTITY_MATCH",
            "PASSED",
            "PROMETHEUS_CURRENT_SAMPLE",
        ),
        (
            "PROMETHEUS_IDENTITY_MATCH",
            "FAILED",
            "PROMETHEUS_STALE_SAMPLE",
        ),
        ("JAEGER_IDENTITY_MATCH", "PASSED", "JAEGER_CURRENT_TRACE"),
        (
            "JAEGER_SCHEMA_PARSED",
            "FAILED",
            "JAEGER_CURRENT_TRACE_NOT_FOUND",
        ),
        ("OPENSEARCH_IDENTITY_MATCH", "PASSED", "OPENSEARCH_CURRENT_LOG"),
        ("OPENSEARCH_IDENTITY_MATCH", "FAILED", "OPENSEARCH_STALE_LOG"),
        (
            "PROBE_AD_ARRAY_PARSED",
            "PASSED",
            "PROBE_CURRENT_ATTEMPT_RESPONSE",
        ),
    }
)
_CANDIDATE_LIFECYCLE_FRESHNESS_CHAINS = frozenset(
    {
        ("PASSED", "LOAD_GENERATOR_HEALTHY"),
        ("FAILED", "LOAD_GENERATOR_NOT_HEALTHY"),
        ("PASSED", "OTEL_COLLECTOR_HEALTHY"),
        ("FAILED", "OTEL_COLLECTOR_NOT_HEALTHY"),
    }
)


class CandidateGateDiagnostic(BaseModel):
    """Final-attempt truth for one post-HTTP readiness gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt: int = Field(ge=1, le=6)
    raw_artifact: str = Field(min_length=1)
    transport_outcome: Literal["PASSED", "FAILED", "NOT_APPLICABLE"]
    transport_reason: str = Field(min_length=1)
    http_outcome: Literal[
        "PASSED",
        "FAILED",
        "NOT_EVALUATED",
        "NOT_APPLICABLE",
    ]
    http_status: int | None = Field(default=None, ge=100, le=599)
    http_reason: str = Field(min_length=1)
    parse_outcome: Literal["PASSED", "FAILED", "NOT_EVALUATED"]
    parse_reason: str = Field(min_length=1)
    freshness_outcome: Literal["PASSED", "FAILED", "NOT_EVALUATED"]
    freshness_reason: str = Field(min_length=1)

    @property
    def passed(self) -> bool:
        return (
            self.transport_outcome in {"PASSED", "NOT_APPLICABLE"}
            and self.http_outcome in {"PASSED", "NOT_APPLICABLE"}
            and self.parse_outcome == "PASSED"
            and self.freshness_outcome == "PASSED"
        )

    @model_validator(mode="after")
    def require_outcome_chain(self) -> "CandidateGateDiagnostic":
        lifecycle_chain = (
            self.transport_outcome == "NOT_APPLICABLE"
            and self.transport_reason == "NOT_APPLICABLE_LIFECYCLE_ARTIFACT"
            and self.http_outcome == "NOT_APPLICABLE"
            and self.http_status is None
            and self.http_reason == "NOT_APPLICABLE_LIFECYCLE_ARTIFACT"
            and self.parse_outcome == "PASSED"
            and self.parse_reason == "LIFECYCLE_VERIFIED_ARTIFACT_PARSED"
            and (self.freshness_outcome, self.freshness_reason)
            in _CANDIDATE_LIFECYCLE_FRESHNESS_CHAINS
        )
        if lifecycle_chain:
            return self

        transport_failure_chain = (
            self.transport_outcome == "FAILED"
            and self.transport_reason in _CANDIDATE_TRANSPORT_FAILURE_REASONS
            and self.http_outcome == "NOT_EVALUATED"
            and self.http_status is None
            and self.http_reason == "NOT_EVALUATED_TRANSPORT_FAILURE"
            and self.parse_outcome == "NOT_EVALUATED"
            and self.parse_reason == "NOT_EVALUATED_TRANSPORT_FAILURE"
            and self.freshness_outcome == "NOT_EVALUATED"
            and self.freshness_reason == "NOT_EVALUATED_TRANSPORT_FAILURE"
        )
        http_failure_chain = (
            self.transport_outcome == "PASSED"
            and self.transport_reason == "TRANSPORT_SUCCEEDED"
            and self.http_outcome == "FAILED"
            and _candidate_http_failure_matches_status(
                self.http_reason,
                self.http_status,
            )
            and self.parse_outcome == "NOT_EVALUATED"
            and self.parse_reason == "NOT_EVALUATED_HTTP_FAILURE"
            and self.freshness_outcome == "NOT_EVALUATED"
            and self.freshness_reason == "NOT_EVALUATED_HTTP_FAILURE"
        )
        parsed_http_chain = (
            self.transport_outcome == "PASSED"
            and self.transport_reason == "TRANSPORT_SUCCEEDED"
            and self.http_outcome == "PASSED"
            and self.http_status is not None
            and 200 <= self.http_status < 300
            and self.http_reason == "HTTP_STATUS_OK"
            and (
                (
                    self.parse_outcome == "FAILED"
                    and self.parse_reason in _CANDIDATE_PARSE_FAILURE_REASONS
                    and self.freshness_outcome == "NOT_EVALUATED"
                    and self.freshness_reason == "NOT_EVALUATED_PARSE_FAILURE"
                )
                or (
                    self.parse_outcome == "PASSED"
                    and (
                        self.parse_reason,
                        self.freshness_outcome,
                        self.freshness_reason,
                    )
                    in _CANDIDATE_PARSED_FRESHNESS_CHAINS
                )
            )
        )
        if transport_failure_chain or http_failure_chain or parsed_http_chain:
            return self
        raise ValueError("candidate diagnostic state chain is invalid")


def _candidate_http_failure_matches_status(
    reason: str,
    status: int | None,
) -> bool:
    if reason in {"HTTP_HEADER_LIMIT_EXCEEDED", "HTTP_BODY_LIMIT_EXCEEDED"}:
        return status is not None
    if reason == "HTTP_REDIRECT_FORBIDDEN":
        return status is not None and 300 <= status <= 399
    if reason == "HTTP_STATUS_ERROR":
        return status is not None and (status < 200 or status >= 400)
    if reason == "HTTP_STATUS_INVALID":
        return status is None or not 200 <= status < 300
    return False


def _candidate_endpoint_reason_family_matches(
    endpoint: str,
    diagnostic: CandidateGateDiagnostic,
) -> bool:
    if diagnostic.parse_outcome == "NOT_EVALUATED":
        return True
    prefix = {
        "prometheus": "PROMETHEUS_",
        "jaeger": "JAEGER_",
        "opensearch": "OPENSEARCH_",
        "probe": "PROBE_",
    }.get(endpoint)
    if prefix is None or not diagnostic.parse_reason.startswith(prefix):
        return False
    return (
        diagnostic.freshness_outcome == "NOT_EVALUATED"
        or diagnostic.freshness_reason.startswith(prefix)
    )


def _candidate_lifecycle_reason_family_matches(
    gate_name: str,
    diagnostic: CandidateGateDiagnostic,
) -> bool:
    prefix = {
        "load_generator_healthy": "LOAD_GENERATOR_",
        "otel_collector_healthy": "OTEL_COLLECTOR_",
    }.get(gate_name)
    return prefix is not None and diagnostic.freshness_reason.startswith(prefix)


class CandidateInitialReadiness(BaseModel):
    """Pre-control endpoint/lifecycle proof that makes no frozen-query claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[
        "phase0.candidate-initial-readiness.v1",
        "phase0.candidate-initial-readiness.v2",
    ] = "phase0.candidate-initial-readiness.v2"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ownership_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: Literal["INITIAL", "CONTROL_MUTATION"] = "INITIAL"
    endpoint_gates: dict[str, bool]
    propagation_authority: Literal["CANDIDATE_OWNED_CURRENT_RUN"]
    attempt_count: int = Field(ge=1, le=6)
    max_attempts: Literal[6] = 6
    window_started_at: datetime
    window_ended_at: datetime
    propagation_gates: dict[str, bool]
    endpoint_diagnostics: dict[str, CandidateGateDiagnostic] | None = None
    propagation_diagnostics: dict[str, CandidateGateDiagnostic] | None = None
    raw_artifacts: tuple[str, ...]
    registry_frozen_claimed: bool = False
    lifecycle_artifact: str
    lifecycle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_artifact: str | None = None
    evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def require_versioned_diagnostics(self) -> "CandidateInitialReadiness":
        if (self.evidence_artifact is None) != (self.evidence_sha256 is None):
            raise ValueError("candidate evidence path and hash must be paired")
        if self.schema_version.endswith(".v1"):
            if (
                self.endpoint_diagnostics is not None
                or self.propagation_diagnostics is not None
            ):
                raise ValueError("candidate readiness v1 cannot contain diagnostics")
            return self
        if set(self.endpoint_gates) != _CANDIDATE_ENDPOINT_NAMES:
            raise ValueError("endpoint gate keys are not exact")
        if set(self.propagation_gates) != _CANDIDATE_PROPAGATION_NAMES:
            raise ValueError("propagation gate keys are not exact")
        if (
            self.endpoint_diagnostics is None
            or set(self.endpoint_diagnostics) != _CANDIDATE_ENDPOINT_NAMES
        ):
            raise ValueError("endpoint diagnostic keys are not exact")
        if (
            self.propagation_diagnostics is None
            or set(self.propagation_diagnostics)
            != _CANDIDATE_PROPAGATION_NAMES
        ):
            raise ValueError("propagation diagnostic keys are not exact")
        for name, gate in self.endpoint_gates.items():
            if gate != self.endpoint_diagnostics[name].passed:
                raise ValueError("endpoint gate and diagnostic disagree")
            diagnostic = self.endpoint_diagnostics[name]
            expected_suffix = (
                f"attempt-{self.attempt_count:02d}-{name}-raw.json"
            )
            if (
                diagnostic.attempt != self.attempt_count
                or diagnostic.raw_artifact not in self.raw_artifacts
                or not diagnostic.raw_artifact.endswith(expected_suffix)
            ):
                raise ValueError("endpoint diagnostic does not map final raw artifact")
            if not _candidate_endpoint_reason_family_matches(name, diagnostic):
                raise ValueError("endpoint diagnostic reason family differs")
        backend_mappings = {
            "prometheus_ad_getads_current": "prometheus",
            "jaeger_load_to_ad_getads_current": "jaeger",
            "opensearch_ad_log_current": "opensearch",
        }
        for gate_name, endpoint_name in backend_mappings.items():
            diagnostic = self.propagation_diagnostics[gate_name]
            endpoint_diagnostic = self.endpoint_diagnostics[endpoint_name]
            if diagnostic != endpoint_diagnostic:
                raise ValueError(
                    "backend propagation diagnostic differs from endpoint diagnostic"
                )
        for gate_name in ("load_generator_healthy", "otel_collector_healthy"):
            if (
                self.propagation_diagnostics[gate_name].raw_artifact
                != self.lifecycle_artifact
            ):
                raise ValueError("lifecycle diagnostic raw mapping differs")
            if not _candidate_lifecycle_reason_family_matches(
                gate_name,
                self.propagation_diagnostics[gate_name],
            ):
                raise ValueError("lifecycle diagnostic reason family differs")
        for name, gate in self.propagation_gates.items():
            diagnostic = self.propagation_diagnostics[name]
            if gate != diagnostic.passed:
                raise ValueError("propagation gate and diagnostic disagree")
            if diagnostic.attempt != self.attempt_count:
                raise ValueError("propagation diagnostic attempt differs")
        return self

    @property
    def ready(self) -> bool:
        return (
            set(self.endpoint_gates)
            == {"prometheus", "jaeger", "opensearch", "probe"}
            and (
                all(self.endpoint_gates.values())
                if self.purpose == "INITIAL"
                else all(
                    self.endpoint_gates[name]
                    for name in ("prometheus", "jaeger", "opensearch")
                )
            )
            and set(self.propagation_gates)
            == {
                "prometheus_ad_getads_current",
                "jaeger_load_to_ad_getads_current",
                "opensearch_ad_log_current",
                "load_generator_healthy",
                "otel_collector_healthy",
            }
            and all(self.propagation_gates.values())
            and bool(self.raw_artifacts)
            and not self.registry_frozen_claimed
        )


class CandidateReadinessPolicy(BaseModel):
    """Frozen budget for pre-Task7 current-run propagation evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_delay_seconds: Literal[65.0] = 65.0
    max_attempts: Literal[6] = 6
    retry_interval_seconds: Literal[5.0] = 5.0
    window_seconds: Literal[60] = 60


_CANDIDATE_ENDPOINT_NAMES = frozenset(
    ("prometheus", "jaeger", "opensearch", "probe")
)
_CANDIDATE_PROPAGATION_NAMES = frozenset(
    (
        "prometheus_ad_getads_current",
        "jaeger_load_to_ad_getads_current",
        "opensearch_ad_log_current",
        "load_generator_healthy",
        "otel_collector_healthy",
    )
)


def collect_candidate_initial_readiness(
    *,
    project_root: Path,
    artifacts_root: Path,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
    purpose: Literal["INITIAL", "CONTROL_MUTATION"] = "INITIAL",
    retry_sleep: Callable[[float], None] = time.sleep,
) -> CandidateInitialReadiness:
    """Prove lifecycle ownership and candidate endpoints before any control write."""
    prefix = f"lifecycle/initial-readiness/{time.monotonic_ns()}"
    if (
        not preflight.is_current()
        or not ownership.is_authentic()
        or preflight.run_id != ownership.run_id
    ):
        reason_code = "INITIAL_READINESS_AUTHORITY_INVALID"
        artifact_path = None
        if _candidate_pre_http_persistence_allowed(
            preflight=preflight,
            ownership=ownership,
        ):
            failure = _persist_candidate_pre_http_failure(
                artifacts_root=artifacts_root,
                ownership=ownership,
                preflight=preflight,
                purpose=purpose,
                prefix=prefix,
                reason_code=reason_code,
                failure_detail=reason_code,
            )
            artifact_path = str(failure.path)
        raise ReadinessCollectionError(
            reason_code,
            artifact_path=artifact_path,
        )
    policy = CandidateReadinessPolicy()
    try:
        (
            lifecycle_artifact,
            lifecycle_sha256,
            lifecycle_gates,
        ) = _verify_initial_lifecycle_ownership(
            project_root=project_root,
            artifacts_root=artifacts_root,
            preflight=preflight,
            ownership=ownership,
        )
        base_urls = _owned_base_urls(ownership)
        client = OwnedHttpClient(context=ownership)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        reason_code = _candidate_pre_http_reason_code(error)
        artifact_path = None
        if _candidate_pre_http_persistence_allowed(
            preflight=preflight,
            ownership=ownership,
        ):
            failure = _persist_candidate_pre_http_failure(
                artifacts_root=artifacts_root,
                ownership=ownership,
                preflight=preflight,
                purpose=purpose,
                prefix=prefix,
                reason_code=reason_code,
                failure_detail=str(error) or type(error).__name__,
            )
            artifact_path = str(failure.path)
        raise ReadinessCollectionError(
            reason_code,
            artifact_path=artifact_path,
        ) from error
    started_at = datetime.now(UTC)
    monotonic_started = time.monotonic()
    window = PhaseWindow(
        run_id=ownership.run_id,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=started_at,
        utc_ended_at=started_at + timedelta(seconds=policy.window_seconds),
        monotonic_started_at=monotonic_started,
        monotonic_ended_at=monotonic_started + policy.window_seconds,
    )
    gates = {name: False for name in ("prometheus", "jaeger", "opensearch", "probe")}
    propagation = {
        "prometheus_ad_getads_current": False,
        "jaeger_load_to_ad_getads_current": False,
        "opensearch_ad_log_current": False,
        **lifecycle_gates,
    }
    raw_artifacts: list[str] = []
    endpoint_diagnostics: dict[str, CandidateGateDiagnostic] = {}
    propagation_diagnostics: dict[str, CandidateGateDiagnostic] = {}
    attempt_count = 0
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        for attempt in range(1, policy.max_attempts + 1):
            attempt_count = attempt
            exchanges = _candidate_signal_exchanges(
                client=client,
                base_urls=base_urls,
                window=window,
            )
            for name, exchange in exchanges.items():
                raw = store.write_immutable(
                    f"{prefix}/attempt-{attempt:02d}-{name}-raw.json",
                    {
                        "schema_version": "phase0.candidate-signal-raw.v1",
                        "run_id": ownership.run_id,
                        "attempt": attempt,
                        "endpoint": name,
                        "request_method": exchange.request.method,
                        "request_target": exchange.request.target,
                        "request_started_at": exchange.started_at.isoformat(),
                        "response_ended_at": exchange.ended_at.isoformat(),
                        "http_status": exchange.status_code,
                        "transport_reason": exchange.reason.value,
                        "raw_response_base64": base64.b64encode(
                            exchange.raw_body
                        ).decode("ascii"),
                        "raw_response_sha256": exchange.raw_sha256,
                    },
                )
                raw_artifact = str(raw.path)
                raw_artifacts.append(raw_artifact)
                diagnostic = _candidate_endpoint_diagnostic(
                    name,
                    exchange,
                    attempt=attempt,
                    raw_artifact=raw_artifact,
                    window=window,
                )
                endpoint_diagnostics[name] = diagnostic
                gates[name] = diagnostic.passed
            propagation.update(
                {
                    "prometheus_ad_getads_current": gates["prometheus"],
                    "jaeger_load_to_ad_getads_current": gates["jaeger"],
                    "opensearch_ad_log_current": gates["opensearch"],
                }
            )
            propagation_diagnostics = {
                "prometheus_ad_getads_current": endpoint_diagnostics[
                    "prometheus"
                ],
                "jaeger_load_to_ad_getads_current": endpoint_diagnostics[
                    "jaeger"
                ],
                "opensearch_ad_log_current": endpoint_diagnostics[
                    "opensearch"
                ],
                "load_generator_healthy": _candidate_lifecycle_diagnostic(
                    gate_name="load_generator_healthy",
                    passed=propagation["load_generator_healthy"],
                    attempt=attempt,
                    lifecycle_artifact=lifecycle_artifact,
                ),
                "otel_collector_healthy": _candidate_lifecycle_diagnostic(
                    gate_name="otel_collector_healthy",
                    passed=propagation["otel_collector_healthy"],
                    attempt=attempt,
                    lifecycle_artifact=lifecycle_artifact,
                ),
            }
            endpoints_ready = (
                all(gates.values())
                if purpose == "INITIAL"
                else all(
                    gates[name]
                    for name in ("prometheus", "jaeger", "opensearch")
                )
            )
            if endpoints_ready and all(propagation.values()):
                break
            if (
                attempt < policy.max_attempts
                and time.monotonic() + policy.retry_interval_seconds
                < window.monotonic_ended_at
            ):
                retry_sleep(policy.retry_interval_seconds)
        candidate_summary = CandidateInitialReadiness(
            schema_version="phase0.candidate-initial-readiness.v2",
            run_id=ownership.run_id,
            preflight_sha256=preflight.content_sha256,
            ownership_manifest_sha256=ownership.manifest_sha256,
            purpose=purpose,
            endpoint_gates=gates,
            propagation_authority="CANDIDATE_OWNED_CURRENT_RUN",
            attempt_count=attempt_count,
            max_attempts=policy.max_attempts,
            window_started_at=window.utc_started_at,
            window_ended_at=window.utc_ended_at,
            propagation_gates=propagation,
            endpoint_diagnostics=endpoint_diagnostics,
            propagation_diagnostics=propagation_diagnostics,
            raw_artifacts=tuple(raw_artifacts),
            registry_frozen_claimed=False,
            lifecycle_artifact=lifecycle_artifact,
            lifecycle_sha256=lifecycle_sha256,
        )
        summary = store.write_immutable(
            f"{prefix}/summary.json",
            candidate_summary.model_dump(
                mode="json",
                exclude={"evidence_artifact", "evidence_sha256"},
            ),
        )
    evidence = CandidateInitialReadiness(
        schema_version="phase0.candidate-initial-readiness.v2",
        run_id=ownership.run_id,
        preflight_sha256=preflight.content_sha256,
        ownership_manifest_sha256=ownership.manifest_sha256,
        purpose=purpose,
        endpoint_gates=gates,
        propagation_authority="CANDIDATE_OWNED_CURRENT_RUN",
        attempt_count=attempt_count,
        window_started_at=window.utc_started_at,
        window_ended_at=window.utc_ended_at,
        propagation_gates=propagation,
        endpoint_diagnostics=endpoint_diagnostics,
        propagation_diagnostics=propagation_diagnostics,
        raw_artifacts=tuple(raw_artifacts),
        registry_frozen_claimed=False,
        lifecycle_artifact=lifecycle_artifact,
        lifecycle_sha256=lifecycle_sha256,
        evidence_artifact=str(summary.path),
        evidence_sha256=summary.sha256,
    )
    if not evidence.ready:
        raise ReadinessCollectionError(
            "INITIAL_CANDIDATE_READINESS_INCOMPLETE",
            artifact_path=str(summary.path),
        )
    return evidence


def _candidate_pre_http_reason_code(error: BaseException) -> str:
    detail = str(error)
    if detail == "lifecycle runner authority is invalid":
        return "INITIAL_READINESS_LIFECYCLE_AUTHORITY_INVALID"
    if detail == "lifecycle runner Docker binding is invalid":
        return "INITIAL_READINESS_LIFECYCLE_DOCKER_BINDING_INVALID"
    return "INITIAL_READINESS_PRE_HTTP_FAILURE"


def _candidate_pre_http_persistence_allowed(
    *,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
) -> bool:
    return (
        preflight.is_authentic()
        and ownership.is_authentic()
        and preflight.run_id == ownership.run_id
    )


def _persist_candidate_pre_http_failure(
    *,
    artifacts_root: Path,
    ownership: AuthenticatedOwnershipContext,
    preflight: AuthenticatedPreflightEvidence,
    purpose: Literal["INITIAL", "CONTROL_MUTATION"],
    prefix: str,
    reason_code: str,
    failure_detail: str,
):
    if not _candidate_pre_http_persistence_allowed(
        preflight=preflight,
        ownership=ownership,
    ):
        raise ValueError("pre-HTTP failure evidence authority is invalid")
    diagnostic = CandidateEndpointPreHttpDiagnostic(
        freshness_reason=reason_code,
    )
    propagation_diagnostic = CandidatePropagationPreHttpDiagnostic()
    failure = CandidateReadinessPreHttpFailure(
        schema_version="phase0.candidate-readiness-pre-http-failure.v1",
        run_id=ownership.run_id,
        preflight_sha256=preflight.content_sha256,
        ownership_manifest_sha256=ownership.manifest_sha256,
        purpose=purpose,
        reason_code=reason_code,
        failure_detail=failure_detail,
        endpoint_gates={
            name: "NOT_EVALUATED" for name in _CANDIDATE_ENDPOINT_NAMES
        },
        propagation_gates={
            name: "NOT_EVALUATED" for name in _CANDIDATE_PROPAGATION_NAMES
        },
        endpoint_diagnostics={
            name: diagnostic for name in _CANDIDATE_ENDPOINT_NAMES
        },
        propagation_diagnostics={
            name: propagation_diagnostic
            for name in _CANDIDATE_PROPAGATION_NAMES
        },
    )
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        return store.write_immutable(
            f"{prefix}/pre-http-failure.json",
            failure,
        )


def _candidate_signal_exchanges(
    *,
    client: OwnedHttpClient,
    base_urls: dict[str, str],
    window: PhaseWindow,
) -> dict[str, object]:
    prometheus_query = (
        'traces_span_metrics_calls_total{service_name="ad",'
        'span_name="oteldemo.AdService/GetAds"}'
    )
    opensearch_body = canonical_json_bytes(
        {
            "size": 100,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"resource.service.name": "ad"}},
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": window.utc_started_at.isoformat(),
                                    "lte": window.utc_ended_at.isoformat(),
                                }
                            }
                        },
                    ]
                }
            },
        }
    )
    targets = {
        "prometheus": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["prometheus"],
                service="prometheus",
                target_port=9090,
            ),
            method="GET",
            target=f"/api/v1/query?query={quote(prometheus_query, safe='')}",
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
        "jaeger": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["jaeger"],
                service="jaeger",
                target_port=16686,
            ),
            method="GET",
            target="/jaeger/ui/api/traces?"
            + urlencode(
                {
                    "service": "load-generator",
                    "operation": "user_get_ads",
                    "start": int(
                        window.utc_started_at.timestamp() * 1_000_000
                    ),
                    "end": int(window.utc_ended_at.timestamp() * 1_000_000),
                    "limit": 100,
                }
            ),
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
        "opensearch": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["opensearch"],
                service="opensearch",
                target_port=9200,
            ),
            method="POST",
            target="/otel-logs-*/_search",
            headers=(
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(opensearch_body))),
            ),
            body=opensearch_body,
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
        "probe": HttpRequest(
            endpoint=OwnedEndpoint(
                base_url=base_urls["probe"],
                service="frontend-proxy",
                target_port=8080,
            ),
            method="GET",
            target="/api/data?contextKeys=telescopes",
            absolute_deadline_monotonic=window.monotonic_ended_at,
        ),
    }
    return {name: client.request(request) for name, request in targets.items()}


def _candidate_endpoint_diagnostic(
    name: str,
    exchange: HttpExchange,
    *,
    attempt: int,
    raw_artifact: str,
    window: PhaseWindow,
) -> CandidateGateDiagnostic:
    transport_failures = {
        HttpReason.RESOURCE_OWNERSHIP_UNKNOWN,
        HttpReason.HTTP_DEADLINE_EXCEEDED,
        HttpReason.HTTP_TRANSPORT_ERROR,
    }
    if exchange.reason in transport_failures:
        return CandidateGateDiagnostic(
            attempt=attempt,
            raw_artifact=raw_artifact,
            transport_outcome="FAILED",
            transport_reason=exchange.reason.value,
            http_outcome="NOT_EVALUATED",
            http_status=None,
            http_reason="NOT_EVALUATED_TRANSPORT_FAILURE",
            parse_outcome="NOT_EVALUATED",
            parse_reason="NOT_EVALUATED_TRANSPORT_FAILURE",
            freshness_outcome="NOT_EVALUATED",
            freshness_reason="NOT_EVALUATED_TRANSPORT_FAILURE",
        )
    if exchange.reason is not HttpReason.OK:
        return CandidateGateDiagnostic(
            attempt=attempt,
            raw_artifact=raw_artifact,
            transport_outcome="PASSED",
            transport_reason="TRANSPORT_SUCCEEDED",
            http_outcome="FAILED",
            http_status=exchange.status_code,
            http_reason=exchange.reason.value,
            parse_outcome="NOT_EVALUATED",
            parse_reason="NOT_EVALUATED_HTTP_FAILURE",
            freshness_outcome="NOT_EVALUATED",
            freshness_reason="NOT_EVALUATED_HTTP_FAILURE",
        )
    if exchange.status_code is None or not 200 <= exchange.status_code < 300:
        return CandidateGateDiagnostic(
            attempt=attempt,
            raw_artifact=raw_artifact,
            transport_outcome="PASSED",
            transport_reason="TRANSPORT_SUCCEEDED",
            http_outcome="FAILED",
            http_status=exchange.status_code,
            http_reason="HTTP_STATUS_INVALID",
            parse_outcome="NOT_EVALUATED",
            parse_reason="NOT_EVALUATED_HTTP_FAILURE",
            freshness_outcome="NOT_EVALUATED",
            freshness_reason="NOT_EVALUATED_HTTP_FAILURE",
        )
    try:
        payload = json.loads(exchange.raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        parse_outcome = "FAILED"
        parse_reason = f"{name.upper()}_JSON_INVALID"
        freshness_outcome = "NOT_EVALUATED"
        freshness_reason = "NOT_EVALUATED_PARSE_FAILURE"
    else:
        (
            parse_outcome,
            parse_reason,
            freshness_outcome,
            freshness_reason,
        ) = _candidate_payload_diagnostic(
            name,
            payload,
            body=exchange.raw_body,
            window=window,
        )
    return CandidateGateDiagnostic(
        attempt=attempt,
        raw_artifact=raw_artifact,
        transport_outcome="PASSED",
        transport_reason="TRANSPORT_SUCCEEDED",
        http_outcome="PASSED",
        http_status=exchange.status_code,
        http_reason="HTTP_STATUS_OK",
        parse_outcome=parse_outcome,
        parse_reason=parse_reason,
        freshness_outcome=freshness_outcome,
        freshness_reason=freshness_reason,
    )


def _candidate_payload_diagnostic(
    name: str,
    payload: object,
    *,
    body: bytes,
    window: PhaseWindow,
) -> tuple[str, str, str, str]:
    if name == "prometheus":
        return _candidate_prometheus_diagnostic(payload, window)
    if name == "jaeger":
        if not isinstance(payload, dict) or not isinstance(
            payload.get("data"), list
        ):
            return (
                "FAILED",
                "JAEGER_SCHEMA_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        if (
            _jaeger_trace_proves_load_generator_and_getads(
                payload,
                window=window,
            )
            is None
        ):
            return (
                "PASSED",
                "JAEGER_SCHEMA_PARSED",
                "FAILED",
                "JAEGER_CURRENT_TRACE_NOT_FOUND",
            )
        return (
            "PASSED",
            "JAEGER_IDENTITY_MATCH",
            "PASSED",
            "JAEGER_CURRENT_TRACE",
        )
    if name == "opensearch":
        return _candidate_opensearch_diagnostic(payload, window)
    if name != "probe":
        raise ValueError("candidate endpoint name is unknown")
    try:
        _verify_direct_ad_array(body)
    except (TypeError, ValueError):
        return (
            "FAILED",
            "PROBE_SCHEMA_INVALID",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        )
    return (
        "PASSED",
        "PROBE_AD_ARRAY_PARSED",
        "PASSED",
        "PROBE_CURRENT_ATTEMPT_RESPONSE",
    )


def _candidate_prometheus_diagnostic(
    payload: object,
    window: PhaseWindow,
) -> tuple[str, str, str, str]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return (
            "FAILED",
            "PROMETHEUS_SCHEMA_INVALID",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        )
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "vector":
        return (
            "FAILED",
            "PROMETHEUS_SCHEMA_INVALID",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        )
    result = data.get("result")
    if not isinstance(result, list):
        return (
            "FAILED",
            "PROMETHEUS_SCHEMA_INVALID",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        )
    identity_seen = False
    stale_seen = False
    for item in result:
        if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
            return (
                "FAILED",
                "PROMETHEUS_SCHEMA_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        metric = item["metric"]
        value = item.get("value")
        if (
            metric.get("service_name") != "ad"
            or metric.get("span_name") != "oteldemo.AdService/GetAds"
        ):
            continue
        identity_seen = True
        if (
            not isinstance(value, list)
            or len(value) != 2
            or isinstance(value[0], bool)
            or not isinstance(value[0], (int, float))
        ):
            return (
                "FAILED",
                "PROMETHEUS_SCHEMA_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        try:
            observed = datetime.fromtimestamp(value[0], tz=UTC)
        except (OSError, OverflowError, ValueError):
            return (
                "FAILED",
                "PROMETHEUS_SCHEMA_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        if window.utc_started_at <= observed <= window.utc_ended_at:
            return (
                "PASSED",
                "PROMETHEUS_IDENTITY_MATCH",
                "PASSED",
                "PROMETHEUS_CURRENT_SAMPLE",
            )
        stale_seen = True
    if identity_seen and stale_seen:
        return (
            "PASSED",
            "PROMETHEUS_IDENTITY_MATCH",
            "FAILED",
            "PROMETHEUS_STALE_SAMPLE",
        )
    return (
        "FAILED",
        "PROMETHEUS_IDENTITY_MISMATCH",
        "NOT_EVALUATED",
        "NOT_EVALUATED_PARSE_FAILURE",
    )


def _candidate_opensearch_diagnostic(
    payload: object,
    window: PhaseWindow,
) -> tuple[str, str, str, str]:
    if not isinstance(payload, dict):
        return (
            "FAILED",
            "OPENSEARCH_SCHEMA_INVALID",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        )
    hits = payload.get("hits")
    if not isinstance(hits, dict) or not isinstance(hits.get("hits"), list):
        return (
            "FAILED",
            "OPENSEARCH_SCHEMA_INVALID",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        )
    if not hits["hits"]:
        return (
            "FAILED",
            "OPENSEARCH_EMPTY_HIT_SET",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        )
    identity_seen = False
    stale_seen = False
    for hit in hits["hits"]:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict):
            return (
                "FAILED",
                "OPENSEARCH_SCHEMA_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        identity = parse_opensearch_service_identity(
            source,
            field="resource.service.name",
        )
        if not identity.parsed:
            return (
                "FAILED",
                identity.reason.value,
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        if identity.value != "ad":
            continue
        identity_seen = True
        timestamp = source.get("@timestamp")
        if not isinstance(timestamp, str):
            return (
                "FAILED",
                "OPENSEARCH_TIMESTAMP_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        try:
            observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return (
                "FAILED",
                "OPENSEARCH_TIMESTAMP_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        if observed.utcoffset() is None:
            return (
                "FAILED",
                "OPENSEARCH_TIMESTAMP_INVALID",
                "NOT_EVALUATED",
                "NOT_EVALUATED_PARSE_FAILURE",
            )
        if window.utc_started_at <= observed <= window.utc_ended_at:
            return (
                "PASSED",
                "OPENSEARCH_IDENTITY_MATCH",
                "PASSED",
                "OPENSEARCH_CURRENT_LOG",
            )
        stale_seen = True
    if identity_seen and stale_seen:
        return (
            "PASSED",
            "OPENSEARCH_IDENTITY_MATCH",
            "FAILED",
            "OPENSEARCH_STALE_LOG",
        )
    return (
        "FAILED",
        "OPENSEARCH_IDENTITY_MISMATCH",
        "NOT_EVALUATED",
        "NOT_EVALUATED_PARSE_FAILURE",
    )


def _candidate_lifecycle_diagnostic(
    *,
    gate_name: str,
    passed: bool,
    attempt: int,
    lifecycle_artifact: str,
) -> CandidateGateDiagnostic:
    service = {
        "load_generator_healthy": "LOAD_GENERATOR",
        "otel_collector_healthy": "OTEL_COLLECTOR",
    }.get(gate_name)
    if service is None:
        raise ValueError("candidate lifecycle gate name is unknown")
    return CandidateGateDiagnostic(
        attempt=attempt,
        raw_artifact=lifecycle_artifact,
        transport_outcome="NOT_APPLICABLE",
        transport_reason="NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
        http_outcome="NOT_APPLICABLE",
        http_status=None,
        http_reason="NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
        parse_outcome="PASSED",
        parse_reason="LIFECYCLE_VERIFIED_ARTIFACT_PARSED",
        freshness_outcome="PASSED" if passed else "FAILED",
        freshness_reason=(
            f"{service}_HEALTHY" if passed else f"{service}_NOT_HEALTHY"
        ),
    )


def _verify_initial_lifecycle_ownership(
    *,
    project_root: Path,
    artifacts_root: Path,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
) -> tuple[str, str, dict[str, bool]]:
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        prefix = f"lifecycle/initial-ownership/{time.monotonic_ns()}"
        runner = create_authenticated_lifecycle_runner(
            preflight=preflight,
            context=ownership,
            project_root=project_root,
            artifacts_root=artifacts_root,
        )
        execution = execute_lifecycle_readiness(
            runner,
            preflight=preflight,
            context=ownership,
        )
        discovery = derive_current_resource_discovery(
            ownership,
            store,
            execution,
            artifact_prefix=prefix,
        )
        verify_owned_resources(discovery.resources, ownership.manifest)
        service_gates = _candidate_lifecycle_service_gates(
            ownership,
            execution,
        )
        artifact = store.write_immutable(
            f"{prefix}/verified.json",
            {
                "schema_version": "phase0.initial-lifecycle-ownership.v1",
                "run_id": ownership.run_id,
                "manifest_sha256": ownership.manifest_sha256,
                "resource_count": len(discovery.resources),
                "service_gates": service_gates,
                "discovery_artifact": discovery.evidence_artifact,
                "discovery_sha256": discovery.evidence_sha256,
            },
        )
    return str(artifact.path), artifact.sha256, service_gates


def _candidate_lifecycle_service_gates(
    ownership: AuthenticatedOwnershipContext,
    execution: object,
) -> dict[str, bool]:
    by_purpose = dict(getattr(execution, "command_results", ()))
    gates: dict[str, bool] = {}
    for service, gate in (
        ("load-generator", "load_generator_healthy"),
        ("otel-collector", "otel_collector_healthy"),
    ):
        resources = [
            resource
            for resource in ownership.manifest.resources
            if resource.kind == "container"
            and resource.labels.get("com.docker.compose.service") == service
        ]
        result = by_purpose.get(f"{service}_status")
        state = (
            _parse_service_container_inspect(
                result.stdout,
                resource=resources[0],
            )
            if len(resources) == 1 and result is not None
            else None
        )
        gates[gate] = _container_state_is_ready(state)
    return gates


def _container_state_is_ready(state: object) -> bool:
    """Require configured healthchecks to pass; running-only services stay eligible."""
    if (
        not isinstance(state, dict)
        or state.get("Running") is not True
        or state.get("Status") != "running"
    ):
        return False
    health = state.get("Health")
    return health is None or (
        isinstance(health, dict) and health.get("Status") == "healthy"
    )


def collect_fresh_readiness(
    *,
    project_root: Path,
    artifacts_root: Path,
    preflight: AuthenticatedPreflightEvidence,
    ownership: AuthenticatedOwnershipContext,
    boundary: str = "unspecified",
) -> ReadinessEvidence:
    if (
        not preflight.is_current()
        or not ownership.is_authentic()
        or preflight.run_id != ownership.run_id
    ):
        raise ReadinessCollectionError("READINESS_AUTHORITY_INVALID")
    registry_path = (
        Path(project_root)
        / "config"
        / "phase0"
        / "telemetry-queries-v3.0.0.json"
    )
    loaded = load_query_registry(registry_path)
    if loaded.registry.state is not FixtureState.FROZEN:
        raise ReadinessCollectionError("BLOCKED_TELEMETRY_FIXTURE_UNRESOLVED")
    base_urls = _owned_base_urls(ownership)
    now_utc = datetime.now(UTC)
    now_monotonic = time.monotonic()
    window = PhaseWindow(
        run_id=ownership.run_id,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=now_utc,
        utc_ended_at=now_utc + timedelta(seconds=180),
        monotonic_started_at=now_monotonic,
        monotonic_ended_at=now_monotonic + 180,
    )
    if boundary not in {"post-promotion", "final", "unspecified"}:
        raise ReadinessCollectionError("READINESS_BOUNDARY_INVALID")
    session_prefix = f"readiness-sessions/{boundary}-{time.monotonic_ns()}"
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        client = OwnedHttpClient(context=ownership)
        capability = revalidate_frozen_query_capability(
            registry_path,
            evidence_store=store,
            client=client,
            window=window,
            probe_base_url=base_urls["probe"],
        )
        lifecycle_runner = create_authenticated_lifecycle_runner(
            preflight=preflight,
            context=ownership,
            project_root=project_root,
            artifacts_root=artifacts_root,
        )
        execution = execute_lifecycle_readiness(
            lifecycle_runner,
            preflight=preflight,
            context=ownership,
        )
        discovery = derive_current_resource_discovery(
            ownership,
            store,
            execution,
            artifact_prefix=session_prefix,
        )
        load_receipt = acquire_load_generator_telemetry_receipt(
            client=client,
            evidence_store=store,
            registry_capability=capability,
            window=window,
            jaeger_base_url=base_urls["jaeger"],
            artifact_prefix=session_prefix,
        )
        load_proof = derive_service_readiness_proof(
            ownership,
            store,
            execution,
            service="load-generator",
            telemetry_receipt=load_receipt,
            registry_capability=capability,
            window=window,
            artifact_prefix=session_prefix,
        )
        prometheus = PrometheusAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).measure_getads(
            window=window,
            base_url=base_urls["prometheus"],
            artifact_prefix=session_prefix,
        )
        jaeger = JaegerAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).check_readiness(
            window=window,
            base_url=base_urls["jaeger"],
            artifact_prefix=session_prefix,
        )
        opensearch = OpenSearchAdapter(
            client=client,
            evidence_store=store,
            fixture=capability,
        ).check_readiness(
            window=window,
            base_url=base_urls["opensearch"],
            artifact_prefix=session_prefix,
        )
        collector_receipt = acquire_collector_pipeline_receipt(
            client=client,
            evidence_store=store,
            registry_capability=capability,
            window=window,
            context=ownership,
            execution=execution,
            prometheus=prometheus,
            jaeger=jaeger,
            opensearch=opensearch,
            artifact_prefix=session_prefix,
        )
        collector_proof = derive_service_readiness_proof(
            ownership,
            store,
            execution,
            service="otel-collector",
            telemetry_receipt=collector_receipt,
            registry_capability=capability,
            window=window,
            artifact_prefix=session_prefix,
        )
        gates = (
            evaluate_ownership_resources(
                ownership,
                discovery,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_load_generator_readiness(
                ownership,
                load_proof,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_collector_readiness(
                ownership,
                collector_proof,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_backend_readiness(
                ReadinessGateName.PROMETHEUS_FRESH,
                window,
                prometheus,
                context=ownership,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_backend_readiness(
                ReadinessGateName.JAEGER_FRESH,
                window,
                jaeger,
                context=ownership,
                registry_capability=capability,
                evidence_store=store,
            ),
            evaluate_backend_readiness(
                ReadinessGateName.OPENSEARCH_FRESH,
                window,
                opensearch,
                context=ownership,
                registry_capability=capability,
                evidence_store=store,
            ),
        )
        handoff = build_readiness_handoff(
            context=ownership,
            gates=gates,
            evidence_store=store,
            registry_capability=capability,
            artifact_prefix=session_prefix,
        )
    if not handoff.ready or handoff.evidence is None:
        raise ReadinessCollectionError(handoff.reason)
    return handoff.evidence


def _owned_base_urls(
    ownership: AuthenticatedOwnershipContext,
) -> dict[str, str]:
    required = {
        "prometheus": ("prometheus", 9090),
        "jaeger": ("jaeger", 16686),
        "opensearch": ("opensearch", 9200),
        "probe": ("frontend-proxy", 8080),
    }
    resolved: dict[str, str] = {}
    for logical, (service, target) in required.items():
        matches: set[int] = set()
        for resource in ownership.manifest.resources:
            evidence = set(resource.identity_evidence)
            if (
                resource.kind != "port"
                or f"service:{service}" not in evidence
                or f"target_port:{target}" not in evidence
                or "protocol:tcp" not in evidence
                or not (
                    "host_ip:127.0.0.1" in evidence
                    or "host_ip:::1" in evidence
                )
            ):
                continue
            values = [
                value.removeprefix("published_port:")
                for value in evidence
                if value.startswith("published_port:")
            ]
            if len(values) == 1 and values[0].isdigit():
                matches.add(int(values[0]))
        if len(matches) != 1:
            raise ReadinessCollectionError(
                f"READINESS_ENDPOINT_UNPROVEN:{logical}"
            )
        resolved[logical] = f"http://127.0.0.1:{matches.pop()}"
    return resolved
