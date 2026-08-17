"""Bounded real-Provider compatibility Smoke over fake CPU evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
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
from ecomsre.dta_v2.v21.agent_contracts import (
    AgentArmV21,
    AgentIdentityManifestV21,
    build_alert_context_v21,
)
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


class ProviderSmokeAttemptManifestV21(DtaModelV21):
    schema_version: Literal["dta-v21.provider-smoke-attempt-manifest.v1"]
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: datetime
    identity_sha256: Sha256V21
    fixture_source_sha256: Sha256V21
    provider_configuration_sha256: Sha256V21
    protocol_revision_sha256: Sha256V21
    previous_attempt_manifest_sha256: Sha256V21 | None
    manifest_sha256: Sha256V21

    @model_validator(mode="after")
    def require_manifest_binding(self) -> ProviderSmokeAttemptManifestV21:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(0):
            raise ValueError("Provider Smoke attempt timestamp must use UTC")
        revision = semantic_sha256(
            {
                "identity_sha256": self.identity_sha256,
                "fixture_source_sha256": self.fixture_source_sha256,
                "provider_configuration_sha256": self.provider_configuration_sha256,
            }
        )
        if self.protocol_revision_sha256 != revision:
            raise ValueError("Provider Smoke protocol revision differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("Provider Smoke attempt manifest digest differs")
        return self


class ProviderSmokeAttemptReceiptV21(DtaModelV21):
    schema_version: Literal["dta-v21.provider-smoke-attempt-receipt.v1"]
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_manifest_sha256: Sha256V21
    status: ProviderDevelopmentSmokeStatusV21
    provider_attempt_count: StrictInt = Field(ge=0, le=6)
    raw_response_sha256: tuple[Sha256V21, ...] = Field(max_length=6)
    agent_result_sha256: Sha256V21
    provider_report_sha256: Sha256V21
    agent_result_file_sha256: Sha256V21
    provider_report_file_sha256: Sha256V21
    receipt_sha256: Sha256V21

    @model_validator(mode="after")
    def require_receipt_binding(self) -> ProviderSmokeAttemptReceiptV21:
        if len(self.raw_response_sha256) > self.provider_attempt_count:
            raise ValueError("Provider Smoke response hashes exceed attempts")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("Provider Smoke attempt receipt digest differs")
        return self


class ProviderSmokePublicLedgerV21(DtaModelV21):
    schema_version: Literal["dta-v21.provider-smoke-public-ledger.v1"]
    legacy_unbound_attempt_count: StrictInt = Field(ge=0)
    verified_attempts: tuple[ProviderSmokeAttemptReceiptV21, ...] = Field(
        min_length=1
    )
    ledger_sha256: Sha256V21

    @model_validator(mode="after")
    def require_public_ledger_binding(self) -> ProviderSmokePublicLedgerV21:
        attempt_ids = tuple(item.attempt_id for item in self.verified_attempts)
        if attempt_ids != tuple(sorted(attempt_ids)) or len(attempt_ids) != len(
            set(attempt_ids)
        ):
            raise ValueError("Provider Smoke public attempts are not canonical")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("Provider Smoke public ledger digest differs")
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


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Provider Smoke evidence file is missing or unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_attempt_manifests(
    attempt_parent: Path,
) -> tuple[ProviderSmokeAttemptManifestV21, ...]:
    if not attempt_parent.exists():
        return ()
    if attempt_parent.is_symlink() or not attempt_parent.is_dir():
        raise ValueError("Provider Smoke attempt parent is unsafe")
    manifests: list[ProviderSmokeAttemptManifestV21] = []
    for path in sorted(attempt_parent.glob("*/attempt-manifest.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Provider Smoke attempt manifest is unsafe")
        manifests.append(
            ProviderSmokeAttemptManifestV21.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        )
    revisions = tuple(item.protocol_revision_sha256 for item in manifests)
    if len(revisions) != len(set(revisions)):
        raise ValueError("Provider Smoke ledger contains an identical rerun")
    ordered = tuple(
        sorted(manifests, key=lambda item: (item.created_at, item.attempt_id))
    )
    for index, manifest in enumerate(ordered):
        expected_previous = None if index == 0 else ordered[index - 1].manifest_sha256
        if manifest.previous_attempt_manifest_sha256 != expected_previous:
            raise ValueError("Provider Smoke attempt manifest chain differs")
    return ordered


def verify_provider_smoke_attempt_receipt_v21(
    attempt_root: Path,
) -> ProviderSmokeAttemptReceiptV21:
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        raise ValueError("Provider Smoke attempt root is missing or unsafe")
    manifest_path = attempt_root / "attempt-manifest.json"
    receipt_path = attempt_root / "attempt-receipt.json"
    agent_result_path = attempt_root / "agent-result.json"
    report_path = attempt_root / "sanitized-report.json"
    manifest = ProviderSmokeAttemptManifestV21.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    receipt = ProviderSmokeAttemptReceiptV21.model_validate_json(
        receipt_path.read_text(encoding="utf-8")
    )
    if manifest.attempt_id != attempt_root.name or receipt.attempt_id != manifest.attempt_id:
        raise ValueError("Provider Smoke receipt attempt binding differs")
    if receipt.attempt_manifest_sha256 != manifest.manifest_sha256:
        raise ValueError("Provider Smoke receipt manifest binding differs")
    if receipt.agent_result_file_sha256 != _file_sha256(agent_result_path):
        raise ValueError("Provider Smoke Agent result file digest differs")
    if receipt.provider_report_file_sha256 != _file_sha256(report_path):
        raise ValueError("Provider Smoke report file digest differs")
    agent_result = json.loads(agent_result_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(agent_result, dict) or not isinstance(report, dict):
        raise ValueError("Provider Smoke private evidence is not an object")
    if agent_result.get("result_sha256") != receipt.agent_result_sha256:
        raise ValueError("Provider Smoke Agent result binding differs")
    if report.get("report_sha256") != receipt.provider_report_sha256:
        raise ValueError("Provider Smoke report semantic binding differs")
    if report.get("status") != receipt.status.value:
        raise ValueError("Provider Smoke receipt status differs")
    if tuple(report.get("raw_response_sha256", ())) != receipt.raw_response_sha256:
        raise ValueError("Provider Smoke response hash binding differs")
    return receipt


def verify_provider_smoke_private_ledger_v21(
    private_root: Path,
) -> tuple[ProviderSmokeAttemptReceiptV21, ...]:
    attempt_parent = private_root / "pr-c" / "provider-smoke"
    manifests = _load_attempt_manifests(attempt_parent)
    return tuple(
        verify_provider_smoke_attempt_receipt_v21(
            attempt_parent / manifest.attempt_id
        )
        for manifest in manifests
    )


def _start_provider_smoke_attempt_v21(
    *,
    repository_root: Path,
    private_root: Path,
    attempt_id: str,
    created_at: datetime,
    identity: AgentIdentityManifestV21,
    config: OpenAICompatibleConfig,
    timeout_seconds: float,
    max_completion_tokens: int,
) -> tuple[ProviderSmokeAttemptManifestV21, Path]:
    attempt_parent = private_root / "pr-c" / "provider-smoke"
    previous = _load_attempt_manifests(attempt_parent)
    expected_source = (
        repository_root / "src/ecomsre/dta_v2/v21/provider_development_smoke.py"
    ).resolve()
    if expected_source != Path(__file__).resolve():
        raise ValueError("Provider Smoke source is outside the repository")
    fixture_source_sha256 = _file_sha256(expected_source)
    provider_configuration_sha256 = semantic_sha256(
        {
            "base_url_sha256": semantic_sha256(config.base_url),
            "model": config.model,
            "timeout_seconds": timeout_seconds,
            "max_completion_tokens": max_completion_tokens,
        }
    )
    revision = semantic_sha256(
        {
            "identity_sha256": identity.identity_sha256,
            "fixture_source_sha256": fixture_source_sha256,
            "provider_configuration_sha256": provider_configuration_sha256,
        }
    )
    if any(item.protocol_revision_sha256 == revision for item in previous):
        raise ValueError("identical Provider Smoke rerun is forbidden")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.provider-smoke-attempt-manifest.v1",
        "attempt_id": attempt_id,
        "created_at": created_at,
        "identity_sha256": identity.identity_sha256,
        "fixture_source_sha256": fixture_source_sha256,
        "provider_configuration_sha256": provider_configuration_sha256,
        "protocol_revision_sha256": revision,
        "previous_attempt_manifest_sha256": (
            None if not previous else previous[-1].manifest_sha256
        ),
    }
    draft = cast(Any, ProviderSmokeAttemptManifestV21).model_construct(
        **payload, manifest_sha256="0" * 64
    )
    manifest = ProviderSmokeAttemptManifestV21.model_validate(
        {
            **payload,
            "manifest_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"manifest_sha256"})
            ),
        }
    )
    attempt_root = attempt_parent / manifest.attempt_id
    _write_create_once(
        attempt_root / "attempt-manifest.json", manifest.model_dump(mode="json")
    )
    return manifest, attempt_root


def _build_report(
    *,
    result: DtaAgentRunResultV21,
    model_id: str,
    selection_reason: str,
    raw_response_sha256: tuple[str, ...],
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
        "raw_response_sha256": raw_response_sha256,
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
    *,
    repository_root: Path,
    provider_env_path: Path,
    private_root: Path,
    attempt_id: str,
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
    scenarios, _, _ = load_default_scenario_registries(repository_root)
    context = build_alert_context_v21(
        scenario=scenarios.scenarios[0],
        run_id=attempt_id,
        started_at=now - timedelta(minutes=5),
        ended_at=now,
    )
    provider = OpenAICompatibleDtaAgentProviderV21(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        config=config,
        timeout_seconds=60.0,
        max_completion_tokens=1600,
    )
    attempt_manifest, attempt_root = _start_provider_smoke_attempt_v21(
        repository_root=repository_root,
        private_root=private_root,
        attempt_id=attempt_id,
        created_at=now,
        identity=provider.identity,
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
        raw_response_sha256=tuple(
            item
            for item in provider.raw_response_sha256_by_attempt
            if item is not None
        ),
    )
    agent_result_path = attempt_root / "agent-result.json"
    report_path = attempt_root / "sanitized-report.json"
    _write_create_once(agent_result_path, result.model_dump(mode="json"))
    _write_create_once(report_path, report.model_dump(mode="json"))
    receipt_payload: dict[str, object] = {
        "schema_version": "dta-v21.provider-smoke-attempt-receipt.v1",
        "attempt_id": attempt_id,
        "attempt_manifest_sha256": attempt_manifest.manifest_sha256,
        "status": report.status,
        "provider_attempt_count": provider.attempted_calls,
        "raw_response_sha256": report.raw_response_sha256,
        "agent_result_sha256": result.result_sha256,
        "provider_report_sha256": report.report_sha256,
        "agent_result_file_sha256": _file_sha256(agent_result_path),
        "provider_report_file_sha256": _file_sha256(report_path),
    }
    receipt_draft = cast(Any, ProviderSmokeAttemptReceiptV21).model_construct(
        **receipt_payload, receipt_sha256="0" * 64
    )
    receipt = ProviderSmokeAttemptReceiptV21.model_validate(
        {
            **receipt_payload,
            "receipt_sha256": semantic_sha256(
                receipt_draft.model_dump(mode="json", exclude={"receipt_sha256"})
            ),
        }
    )
    _write_create_once(
        attempt_root / "attempt-receipt.json", receipt.model_dump(mode="json")
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    report = run_provider_development_smoke_v21(
        repository_root=arguments.repository_root.resolve(),
        provider_env_path=arguments.provider_env,
        private_root=arguments.private_root,
        attempt_id=arguments.attempt_id,
    )
    print(report.model_dump_json(indent=2))
    return 0 if report.status is ProviderDevelopmentSmokeStatusV21.PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "ProviderDevelopmentSmokeReportV21",
    "ProviderDevelopmentSmokeStatusV21",
    "ProviderSmokeAttemptManifestV21",
    "ProviderSmokeAttemptReceiptV21",
    "ProviderSmokePublicLedgerV21",
    "run_provider_development_smoke_v21",
    "verify_provider_smoke_attempt_receipt_v21",
    "verify_provider_smoke_private_ledger_v21",
)
