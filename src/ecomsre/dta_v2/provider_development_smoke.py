"""Bounded real-Provider development Smoke over replay-only read tools."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.agent import (
    AgentFailureCode,
    AgentRunTerminal,
    run_tool_using_agent,
)
from ecomsre.dta_v2.agent_contracts import build_alert_context
from ecomsre.dta_v2.agent_evidence import (
    _write_private_json,
    persist_agent_run,
)
from ecomsre.dta_v2.agent_provider import OpenAICompatibleDtaAgentProvider
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    DtaModel,
    RunId,
    RunbookId,
    Sha256,
    Terminal,
    semantic_sha256,
)
from ecomsre.dta_v2.read_tools import BackendResult, FakeReadBackend
from ecomsre.dta_v2.registry import (
    load_runbook_registry,
    load_scenario_registry,
)
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    LogSeverity,
    ReadToolRequest,
    SearchLogsRequest,
    SpanRelationship,
    SpanStatus,
    TraceNeighborhoodRecord,
    TraceNeighborhoodRequest,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


class DevelopmentSmokeTerminal(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class ProhibitedActionCounters(DtaModel):
    docker_calls: Literal[0] = 0
    fault_injections: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    executor_calls: Literal[0] = 0
    verifier_calls: Literal[0] = 0
    forward_writes: Literal[0] = 0
    configuration_mutations: Literal[0] = 0
    service_mutations: Literal[0] = 0
    public_writes: Literal[0] = 0


class ProviderDevelopmentSmokeReport(DtaModel):
    schema_version: Literal["dta-v2.provider-development-smoke.v1"]
    smoke_id: RunId
    terminal: DevelopmentSmokeTerminal
    model_id: str = Field(min_length=1, max_length=128)
    identity_sha256: Sha256
    prompt_sha256: Sha256
    tool_schema_sha256: Sha256
    diagnosis_schema_sha256: Sha256
    action_selection_schema_sha256: Sha256
    action_proposal_schema_sha256: Sha256
    provider_adapter_version: str
    temperature: float
    provider_turn_count: StrictInt = Field(ge=0, le=6)
    read_tool_dispatch_count: StrictInt = Field(ge=0, le=4)
    agent_terminal: AgentRunTerminal
    failure_code: AgentFailureCode | None
    diagnosis_terminal: Terminal | None
    proposal_disposition: ActionDisposition | None
    selected_runbook: RunbookId | None
    agent_result_sha256: Sha256
    evidence_manifest_sha256: Sha256
    rejected_provider_response_file_sha256: Sha256 | None = None
    prohibited_action_counters: ProhibitedActionCounters
    report_sha256: Sha256

    @model_validator(mode="after")
    def require_smoke_report(self) -> ProviderDevelopmentSmokeReport:
        passed = (
            self.terminal is DevelopmentSmokeTerminal.PASS
            and self.agent_terminal is AgentRunTerminal.COMPLETED
            and self.failure_code is None
            and self.diagnosis_terminal is Terminal.COMPLETED
            and self.proposal_disposition is ActionDisposition.EXECUTE_RUNBOOK
            and self.selected_runbook is RunbookId.ROLLBACK_CONFIGURATION
            and 1 <= self.provider_turn_count <= 6
            and 1 <= self.read_tool_dispatch_count <= 4
        )
        if self.terminal is DevelopmentSmokeTerminal.PASS and not passed:
            raise ValueError("Provider development Smoke PASS semantics differ")
        if (
            self.rejected_provider_response_file_sha256 is not None
            and (
                self.terminal is not DevelopmentSmokeTerminal.FAIL
                or self.failure_code is not AgentFailureCode.PROVIDER_PROTOCOL_FAILURE
            )
        ):
            raise ValueError("rejected Provider response is not bound to a failure")
        digest_payload = self.model_dump(mode="json", exclude={"report_sha256"})
        accepted_digests = {semantic_sha256(digest_payload)}
        if self.rejected_provider_response_file_sha256 is None:
            legacy_payload = dict(digest_payload)
            legacy_payload.pop("rejected_provider_response_file_sha256")
            accepted_digests.add(semantic_sha256(legacy_payload))
        if self.report_sha256 not in accepted_digests:
            raise ValueError("Provider development Smoke digest differs")
        return self


class _PaymentDevelopmentReplayBackend(FakeReadBackend):
    """Scenario-faithful private replay data without encoded expected actions."""

    def execute(self, request: ReadToolRequest) -> BackendResult:
        if isinstance(request, SearchLogsRequest):
            self.call_count += 1
            return BackendResult(
                (
                    DiagnosticLogRecord(
                        observed_at=request.ended_at,
                        service=request.service,
                        severity=LogSeverity.ERROR,
                        message=(
                            "Downstream payment reports a bounded configuration "
                            "mismatch."
                        ),
                    ),
                )
            )
        if isinstance(request, TraceNeighborhoodRequest):
            self.call_count += 1
            parent = "checkout" if request.service == "payment" else request.service
            path = (
                ("checkout", "payment")
                if request.service == "payment"
                else (request.service, "payment")
            )
            return BackendResult(
                (
                    TraceNeighborhoodRecord(
                        anchor_service=request.service,
                        service_path=path,
                        relationship=SpanRelationship.CHILD,
                        service="payment",
                        parent_service=parent,
                        operation="configuration-lookup",
                        status=SpanStatus.ERROR,
                        duration_ms=12.5,
                        first_error_location=True,
                    ),
                )
            )
        return super().execute(request)


def run_provider_development_smoke(
    *,
    repository_root: Path,
    private_root: Path,
    smoke_id: str,
    config: OpenAICompatibleConfig,
    provider: OpenAICompatibleDtaAgentProvider | None = None,
) -> ProviderDevelopmentSmokeReport:
    """Use the real Provider adapter with in-memory replay reads and zero writes."""

    repository = Path(repository_root).resolve()
    scenario_registry = load_scenario_registry(
        repository / "config/dta-v2/scenarios/agent-visible"
    )
    scenario = next(
        item for item in scenario_registry.scenarios if item.scenario_id == "dta-dev-001"
    )
    ended_at = datetime.now(timezone.utc)
    context = build_alert_context(
        scenario=scenario,
        run_id=smoke_id,
        started_at=ended_at - timedelta(minutes=5),
        ended_at=ended_at,
    )
    effective_provider = provider or OpenAICompatibleDtaAgentProvider(
        config=config,
        timeout_seconds=120.0,
        max_completion_tokens=2048,
    )
    if effective_provider.identity.model_id != config.model:
        raise ValueError("Provider development Smoke model differs from configuration")
    result = run_tool_using_agent(
        context=context,
        backend=_PaymentDevelopmentReplayBackend(),
        registry=load_runbook_registry(repository / "config/dta-v2/runbooks"),
        provider=effective_provider,
    )
    evidence_manifest = persist_agent_run(
        Path(private_root) / "agent",
        result,
        forbidden_secrets=(config.api_key,),
    )
    rejected_provider_response_file_sha256: str | None = None
    if (
        result.terminal is AgentRunTerminal.FAILED
        and result.failure_code is AgentFailureCode.PROVIDER_PROTOCOL_FAILURE
        and result.provider_turn_count == len(result.provider_turns) + 1
        and effective_provider.last_safe_raw_response is not None
    ):
        rejected_provider_response_file_sha256 = _write_private_json(
            Path(private_root) / "rejected-provider-response.json",
            effective_provider.last_safe_raw_response,
        )
    proposal = result.action_proposal
    passed = (
        result.terminal is AgentRunTerminal.COMPLETED
        and proposal is not None
        and proposal.disposition is ActionDisposition.EXECUTE_RUNBOOK
        and proposal.runbook_id is RunbookId.ROLLBACK_CONFIGURATION
        and result.read_tool_dispatch_count > 0
        and effective_provider.attempted_calls > 0
    )
    identity = result.identity
    payload: dict[str, object] = {
        "schema_version": "dta-v2.provider-development-smoke.v1",
        "smoke_id": smoke_id,
        "terminal": (
            DevelopmentSmokeTerminal.PASS
            if passed
            else DevelopmentSmokeTerminal.FAIL
        ),
        "model_id": identity.model_id,
        "identity_sha256": identity.identity_sha256,
        "prompt_sha256": identity.prompt_sha256,
        "tool_schema_sha256": identity.tool_schema_sha256,
        "diagnosis_schema_sha256": identity.diagnosis_schema_sha256,
        "action_selection_schema_sha256": (
            identity.action_selection_schema_sha256
        ),
        "action_proposal_schema_sha256": identity.action_proposal_schema_sha256,
        "provider_adapter_version": identity.provider_adapter_version,
        "temperature": identity.temperature,
        "provider_turn_count": result.provider_turn_count,
        "read_tool_dispatch_count": result.read_tool_dispatch_count,
        "agent_terminal": result.terminal,
        "failure_code": result.failure_code,
        "diagnosis_terminal": (
            None if result.diagnosis is None else result.diagnosis.terminal
        ),
        "proposal_disposition": None if proposal is None else proposal.disposition,
        "selected_runbook": None if proposal is None else proposal.runbook_id,
        "agent_result_sha256": result.result_sha256,
        "evidence_manifest_sha256": evidence_manifest.manifest_sha256,
        "rejected_provider_response_file_sha256": (
            rejected_provider_response_file_sha256
        ),
        "prohibited_action_counters": ProhibitedActionCounters().model_dump(
            mode="json"
        ),
    }
    report = ProviderDevelopmentSmokeReport.model_validate(
        {**payload, "report_sha256": semantic_sha256(payload)}
    )
    _write_private_json(
        Path(private_root) / "development-smoke-report.json", report
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one private DTA v2 replay-only Provider development Smoke."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    smoke = subparsers.add_parser("development-smoke")
    smoke.add_argument("--repository-root", type=Path, required=True)
    smoke.add_argument("--private-root", type=Path, required=True)
    smoke.add_argument("--smoke-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = OpenAICompatibleConfig.from_environment()
    if config is None:
        print(json.dumps({"terminal": "BLOCKED_PROVIDER_CREDENTIALS"}))
        return 2
    report = run_provider_development_smoke(
        repository_root=args.repository_root,
        private_root=args.private_root,
        smoke_id=args.smoke_id,
        config=config,
    )
    print(
        json.dumps(
            {
                "terminal": report.terminal.value,
                "model_id": report.model_id,
                "provider_turn_count": report.provider_turn_count,
                "read_tool_dispatch_count": report.read_tool_dispatch_count,
                "selected_runbook": (
                    None
                    if report.selected_runbook is None
                    else report.selected_runbook.value
                ),
                "report_sha256": report.report_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if report.terminal is DevelopmentSmokeTerminal.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DevelopmentSmokeTerminal",
    "ProhibitedActionCounters",
    "ProviderDevelopmentSmokeReport",
    "run_provider_development_smoke",
]
