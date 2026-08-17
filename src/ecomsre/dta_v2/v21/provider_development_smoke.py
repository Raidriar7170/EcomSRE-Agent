"""Bounded real-Provider compatibility Smoke over fake CPU evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import os
from pathlib import Path
import secrets
from typing import Any, Literal, cast

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.read_tools import BackendResult, FakeReadBackend
from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    LogSeverity,
    METRIC_UNIT_BY_KIND,
    MetricKind,
    MetricRecord,
    QueryMetricsRequest,
    ResourceSample,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    SearchLogsRequest,
    SpanRelationship,
    SpanStatus,
    TraceNeighborhoodRecord,
    TraceNeighborhoodRequest,
    DiagnosticLogRecord,
)
from ecomsre.dta_v2.v21.agent import (
    AgentRunTerminalV21,
    DtaAgentRunResultV21,
    run_evidence_guided_agent_v21,
)
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21, build_alert_context_v21
from ecomsre.dta_v2.v21.agent_provider import OpenAICompatibleDtaAgentProviderV21
from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.registry import (
    load_default_runbook_registry,
    load_default_scenario_registries,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


class ProviderDevelopmentSmokeStatusV21(str, Enum):
    PASS = "PASS"
    BLOCKED_DTA_V21_PROVIDER = "BLOCKED_DTA_V21_PROVIDER"


class ProviderDevelopmentSmokeReportV21(DtaModelV21):
    schema_version: Literal["dta-v21.provider-development-smoke.v1"]
    status: ProviderDevelopmentSmokeStatusV21
    model_id: str = Field(min_length=1, max_length=128)
    model_selection_reason: str = Field(min_length=1, max_length=256)
    arm: Literal[AgentArmV21.EVIDENCE_GUIDED_PLANNER]
    agent_terminal: AgentRunTerminalV21
    provider_turn_count: StrictInt = Field(ge=0, le=6)
    read_tool_dispatch_count: StrictInt = Field(ge=0, le=4)
    diagnosis_terminal: str | None
    candidate_count: StrictInt = Field(ge=0, le=3)
    action_disposition: str | None
    total_input_tokens: StrictInt = Field(ge=0)
    total_output_tokens: StrictInt = Field(ge=0)
    total_latency_ms: StrictInt = Field(ge=0)
    raw_response_sha256: tuple[Sha256V21, ...] = Field(max_length=6)
    agent_result_sha256: Sha256V21
    report_sha256: Sha256V21

    @model_validator(mode="after")
    def require_report_digest(self) -> ProviderDevelopmentSmokeReportV21:
        if (self.status is ProviderDevelopmentSmokeStatusV21.PASS) != (
            self.agent_terminal is not AgentRunTerminalV21.FAILED
        ):
            raise ValueError("Provider Smoke status differs from Agent terminal")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("Provider Smoke report digest differs")
        return self


class _CpuDevelopmentBackendV21(FakeReadBackend):
    """Truth-free typed observations with visible high-CPU symptoms."""

    def execute(self, request):
        self.call_count += 1
        if isinstance(request, QueryMetricsRequest):
            values = {
                MetricKind.ERROR_RATE: 0.35,
                MetricKind.LATENCY_P95_MS: 820.0,
                MetricKind.REQUEST_SUPPORT: 120.0,
                MetricKind.CPU_PERCENT: 96.0,
                MetricKind.MEMORY_BYTES: 48_000_000.0,
                MetricKind.QUEUE_RESOURCE: 2.0,
            }
            return BackendResult(
                tuple(
                    MetricRecord(
                        service=request.service,
                        metric_kind=kind,
                        value=values[kind],
                        unit=METRIC_UNIT_BY_KIND[kind],
                        sample_count=20,
                    )
                    for kind in request.metric_kinds
                )[: request.max_results]
            )
        if isinstance(request, InspectServiceRuntimeRequest):
            return BackendResult(
                tuple(
                    RuntimeRecord(
                        logical_service=service,
                        owned_container_present=True,
                        state=RuntimeState.RUNNING,
                        health=HealthState.HEALTHY,
                        restart_count=0,
                        exit_code=None,
                        endpoint_probe_performed=False,
                        endpoint_state=EndpointState.UNKNOWN,
                    )
                    for service in request.services[: request.max_results]
                )
            )
        if isinstance(request, InspectResourceUsageRequest):
            return BackendResult(
                tuple(
                    ResourceUsageRecord(
                        logical_service=service,
                        sampling_window_seconds=request.sampling_window_seconds,
                        samples=tuple(
                            ResourceSample(
                                offset_ms=(
                                    request.sampling_window_seconds * 1000 * index
                                )
                                // (request.sample_count - 1),
                                cpu_percent=94.0 + min(index, 2),
                                memory_bytes=48_000_000 + (index * 1_000),
                            )
                            for index in range(request.sample_count)
                        ),
                        memory_slope_bytes_per_second=333.0,
                    )
                    for service in request.services
                )
            )
        if isinstance(request, SearchLogsRequest):
            return BackendResult(
                (
                    DiagnosticLogRecord(
                        observed_at=request.ended_at,
                        service=request.service,
                        severity=LogSeverity.WARN,
                        message="sustained local compute pressure observed",
                    ),
                )
            )
        if isinstance(request, TraceNeighborhoodRequest):
            return BackendResult(
                (
                    TraceNeighborhoodRecord(
                        anchor_service=request.service,
                        service_path=(request.service,),
                        relationship=SpanRelationship.ROOT,
                        service=request.service,
                        parent_service=None,
                        operation="request",
                        status=SpanStatus.ERROR,
                        duration_ms=820.0,
                        first_error_location=True,
                    ),
                )
            )
        raise TypeError("unsupported development read request")


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Provider Smoke private directory is unsafe")
    path.chmod(0o700)
    if path.stat().st_mode & 0o777 != 0o700:
        raise PermissionError("Provider Smoke private directory mode differs")


def _write_create_once(path: Path, value: object) -> None:
    _ensure_private_directory(path.parent)
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _build_report(
    *, result: DtaAgentRunResultV21, model_id: str, selection_reason: str
) -> ProviderDevelopmentSmokeReportV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.provider-development-smoke.v1",
        "status": (
            ProviderDevelopmentSmokeStatusV21.BLOCKED_DTA_V21_PROVIDER
            if result.terminal is AgentRunTerminalV21.FAILED
            else ProviderDevelopmentSmokeStatusV21.PASS
        ),
        "model_id": model_id,
        "model_selection_reason": selection_reason,
        "arm": AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        "agent_terminal": result.terminal,
        "provider_turn_count": result.provider_turn_count,
        "read_tool_dispatch_count": result.semantic_read_tool_dispatch_count,
        "diagnosis_terminal": (
            None if result.diagnosis is None else result.diagnosis.terminal.value
        ),
        "candidate_count": (
            0 if result.candidate_set is None else len(result.candidate_set.write_candidates)
        ),
        "action_disposition": (
            None
            if result.action_proposal is None
            else result.action_proposal.disposition.value
        ),
        "total_input_tokens": sum(
            item.usage.input_tokens for item in result.provider_turns
        ),
        "total_output_tokens": sum(
            item.usage.output_tokens for item in result.provider_turns
        ),
        "total_latency_ms": sum(
            item.monotonic_latency_ms for item in result.provider_turns
        ),
        "raw_response_sha256": tuple(
            item.raw_response_sha256 for item in result.provider_turns
        ),
        "agent_result_sha256": result.result_sha256,
    }
    draft = cast(Any, ProviderDevelopmentSmokeReportV21).model_construct(
        **payload, report_sha256="0" * 64
    )
    return ProviderDevelopmentSmokeReportV21.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


def run_provider_development_smoke_v21(
    *, repository_root: Path, provider_env_path: Path, private_root: Path
) -> ProviderDevelopmentSmokeReportV21:
    values = load_private_provider_env(provider_env_path)
    config = OpenAICompatibleConfig.from_environment(values)
    if config is None:
        raise ValueError("Provider configuration is absent")
    selected_reason = (
        "preferred model is the configured Provider model"
        if config.model == "gpt-5.4-2026-03-05"
        else "preferred model is not configured; selected the sole configured compatible model"
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    run_id = secrets.token_hex(16)
    scenarios, _, _ = load_default_scenario_registries(repository_root)
    context = build_alert_context_v21(
        scenario=scenarios.scenarios[0],
        run_id=run_id,
        started_at=now - timedelta(minutes=5),
        ended_at=now,
    )
    provider = OpenAICompatibleDtaAgentProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        config=config,
        timeout_seconds=60.0,
        max_completion_tokens=1600,
    )
    result = run_evidence_guided_agent_v21(
        context=context,
        backend=_CpuDevelopmentBackendV21.healthy(),
        registry=load_default_runbook_registry(repository_root),
        provider=provider,
    )
    report = _build_report(
        result=result,
        model_id=config.model,
        selection_reason=selected_reason,
    )
    attempt_root = private_root / "pr-c" / "provider-smoke" / run_id
    _write_create_once(
        attempt_root / "agent-result.json", result.model_dump(mode="json")
    )
    _write_create_once(
        attempt_root / "sanitized-report.json", report.model_dump(mode="json")
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    report = run_provider_development_smoke_v21(
        repository_root=arguments.repository_root.resolve(),
        provider_env_path=arguments.provider_env,
        private_root=arguments.private_root,
    )
    print(report.model_dump_json(indent=2))
    return 0 if report.status is ProviderDevelopmentSmokeStatusV21.PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "ProviderDevelopmentSmokeReportV21",
    "ProviderDevelopmentSmokeStatusV21",
    "run_provider_development_smoke_v21",
)
