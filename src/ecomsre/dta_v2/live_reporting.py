"""Private append-only evidence and safe public projection for PR-F."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.agent import DtaAgentRunResult
from ecomsre.dta_v2.agent_evidence import persist_agent_run
from ecomsre.dta_v2.agent_provider import (
    _contains_forbidden_raw_key,
    _contains_forbidden_reasoning,
    _contains_secret_material,
)
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionParameter,
    DtaModel,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RiskLevel,
    RunbookId,
    RunbookParameterSpec,
    RunbookStepId,
    Sha256,
    semantic_sha256,
)
from ecomsre.dta_v2.live_contracts import (
    CleanupTerminal,
    FaultOperation,
    LiveAttemptClosure,
    LiveAttemptCounters,
    LiveAttemptEvent,
    LiveAttemptMode,
    LiveAttemptTerminal,
    LiveScenario,
)
from ecomsre.dta_v2.operational_contracts import (
    AdmissionReasonCode,
    AdmissionVerdict,
    ExecutionTerminal,
    StepReceipt,
    VerificationOutcome,
)
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    verify_private_tree_permissions,
    write_private_json,
)


class PrivateLiveAttemptJournal:
    """Create-once stage/receipt files; CLOSED is owned by the closure artifact."""

    def __init__(self, root: Path, *, forbidden_secrets: tuple[str, ...] = ()) -> None:
        self.root = Path(root)
        if self.root == Path("/") or self.root.is_symlink():
            raise ValueError("private live evidence root is unsafe")
        for secret in forbidden_secrets:
            if not isinstance(secret, str) or not secret:
                raise ValueError("forbidden secrets must be non-empty strings")
        ensure_private_directory(self.root)
        self.forbidden_secrets = forbidden_secrets
        self._event_count = 0
        self._receipt_count = 0

    def _require_safe_value(self, value: object) -> None:
        dumped = (
            value.model_dump(mode="json")
            if isinstance(value, DtaModel)
            else value
        )
        if _contains_forbidden_reasoning(dumped):
            raise ValueError("private live evidence contains hidden reasoning")
        if _contains_forbidden_raw_key(dumped):
            raise ValueError("private live evidence contains Provider configuration")
        if any(
            _contains_secret_material(dumped, secret)
            for secret in self.forbidden_secrets
        ):
            raise ValueError("private live evidence contains a forbidden secret")

    def append_event(self, event: LiveAttemptEvent) -> str:
        event = LiveAttemptEvent.model_validate(event.model_dump(mode="python"))
        if event.ordinal != self._event_count + 1:
            raise ValueError("live journal event ordinal is not append-only")
        self._require_safe_value(event)
        digest = write_private_json(
            self.root / "journal" / f"{event.ordinal:04d}-{event.stage.value}.json",
            event,
            create_once=True,
        )
        self._event_count += 1
        return digest

    def append(self, receipt: StepReceipt) -> None:
        receipt = StepReceipt.model_validate(receipt.model_dump(mode="python"))
        if receipt.step_ordinal != self._receipt_count + 1:
            raise ValueError("step receipt ordinal is not append-only")
        self._require_safe_value(receipt)
        write_private_json(
            self.root / "receipts" / f"{receipt.step_ordinal:04d}.json",
            receipt,
            create_once=True,
        )
        self._receipt_count += 1

    def persist_artifact(self, relative_path: str, value: object) -> str:
        candidate = Path(relative_path)
        if (
            candidate.is_absolute()
            or not candidate.parts
            or ".." in candidate.parts
            or candidate.suffix != ".json"
        ):
            raise ValueError("private live artifact path is unsafe")
        self._require_safe_value(value)
        return write_private_json(
            self.root / "artifacts" / candidate,
            value,
            create_once=True,
        )

    def persist_agent(self, result: DtaAgentRunResult) -> str:
        result = DtaAgentRunResult.model_validate(result.model_dump(mode="python"))
        manifest = persist_agent_run(
            self.root / "agent",
            result,
            forbidden_secrets=self.forbidden_secrets,
        )
        return manifest.manifest_sha256

    def persist_closure(self, closure: LiveAttemptClosure) -> str:
        closure = LiveAttemptClosure.model_validate(
            closure.model_dump(mode="python")
        )
        self._require_safe_value(closure)
        verify_private_tree_permissions(self.root)
        return write_private_json(
            self.root / "live-attempt-closure.json",
            closure,
            create_once=True,
        )

    def recover_created_primary_closure(
        self,
        expected: LiveAttemptClosure,
    ) -> LiveAttemptClosure | None:
        """Accept a post-write exception only when the exact primary is authoritative."""

        expected = LiveAttemptClosure.model_validate(
            expected.model_dump(mode="python")
        )
        path = self.root / "live-attempt-closure.json"
        if not path.exists() and not path.is_symlink():
            return None
        if path.is_symlink() or not path.is_file():
            raise ValueError("primary live closure is not a regular file")
        observed = LiveAttemptClosure.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if observed != expected:
            raise ValueError("created primary live closure differs from the attempt")
        self._require_safe_value(observed)
        verify_private_tree_permissions(self.root)
        return observed

    def persist_closure_fallback(self, closure: LiveAttemptClosure) -> str:
        """Retain a typed failure closure if the primary closure path failed."""

        closure = LiveAttemptClosure.model_validate(
            closure.model_dump(mode="python")
        )
        self._require_safe_value(closure)
        primary = self.root / "live-attempt-closure.json"
        if primary.exists() or primary.is_symlink():
            raise FileExistsError("primary live closure already exists")
        verify_private_tree_permissions(self.root)
        return write_private_json(
            self.root / "live-attempt-closure-fallback.json",
            closure,
            create_once=True,
        )


_PUBLIC_OPAQUE_ID_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{16,64}(?![0-9A-Fa-f])|"
    r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"
)
_PUBLIC_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "base_url",
        "container_id",
        "docker_id",
        "headers",
        "private_path",
        "raw_provider_response",
        "raw_response",
        "request_url",
        "run_id",
        "attempt_id",
    }
)


def _require_public_projection_safe(value: object) -> None:
    pending: list[tuple[object, str | None]] = [(value, None)]
    while pending:
        item, field_name = pending.pop()
        if isinstance(item, dict):
            for key, member in item.items():
                if isinstance(key, str) and key.casefold() in _PUBLIC_FORBIDDEN_KEYS:
                    raise ValueError("public live report contains a private field")
                pending.append((member, key if isinstance(key, str) else None))
        elif isinstance(item, (list, tuple)):
            pending.extend((member, field_name) for member in item)
        elif isinstance(item, str):
            folded = item.casefold()
            if (
                "evidence://" in folded
                or "://" in folded
                or (
                    _PUBLIC_OPAQUE_ID_RE.search(item)
                    and not (field_name or "").casefold().endswith("sha256")
                )
                or folded.startswith(("/users/", "/private/", "/tmp/", "/var/"))
            ):
                raise ValueError("public live report contains a private identity")


class PublicEvidenceReference(DtaModel):
    reference: str = Field(pattern=r"^(METRICS|LOGS|TRACES|RUNTIME|RESOURCES):[0-9]{4}$")
    source: EvidenceSource


class PublicDiagnosis(DtaModel):
    root_service: str | None
    fault_domain: FaultDomain | None
    mechanism: FaultMechanism | None
    evidence_sources: tuple[EvidenceSource, ...]
    evidence_refs: tuple[PublicEvidenceReference, ...]


class PublicCandidateAction(DtaModel):
    runbook_id: RunbookId
    target_service: str
    risk_level: RiskLevel
    parameters: tuple[RunbookParameterSpec, ...]
    required_evidence_sources: tuple[EvidenceSource, ...]


class PublicActionProposal(DtaModel):
    disposition: ActionDisposition
    runbook_id: RunbookId | None
    target_service: str | None
    parameters: tuple[ActionParameter, ...]


class PublicOperationalAdmission(DtaModel):
    verdict: AdmissionVerdict
    reason_codes: tuple[AdmissionReasonCode, ...]


class PublicAuthorizationSummary(DtaModel):
    authorization_mode: Literal["DTA_V2_MASTER_RUN_BOUND_CHILD"]
    runbook_id: RunbookId
    target_service: str
    maximum_forward_steps: StrictInt = Field(ge=1, le=2)


class PublicStepReceipt(DtaModel):
    step_ordinal: StrictInt = Field(ge=1, le=2)
    step_id: RunbookStepId
    target: str
    outcome: str
    error_code: str | None


class PublicRecoveryWindow(DtaModel):
    ordinal: StrictInt = Field(ge=1, le=2)
    infrastructure_passed: StrictBool
    business_sli_passed: StrictBool
    endpoint_passed: StrictBool
    configuration_restored: StrictBool
    memory_slope_bytes_per_second: float | None


class PublicVerifierResult(DtaModel):
    terminal: ExecutionTerminal
    verifier_id: str | None
    outcome: VerificationOutcome | None
    reason_codes: tuple[str, ...]


class PublicLiveAttemptReport(DtaModel):
    """Private-ID-free Stage 14 projection for one exact live attempt."""

    schema_version: Literal["dta-v2.public-live-attempt-report.v2"]
    evidence_mode: LiveAttemptMode
    scenario: LiveScenario
    fault: FaultOperation
    terminal: LiveAttemptTerminal
    failure_code: str | None
    scenario_contract_passed: StrictBool
    tool_call_sequence: tuple[str, ...]
    tool_dispatch_count: StrictInt = Field(ge=0, le=4)
    provider_turn_count: StrictInt = Field(ge=0, le=6)
    diagnosis: PublicDiagnosis | None
    candidate_set: tuple[PublicCandidateAction, ...]
    action_proposal: PublicActionProposal | None
    operational_admission: PublicOperationalAdmission | None
    authorization_present: StrictBool
    authorization: PublicAuthorizationSummary | None
    runbook: RunbookId | None
    step_receipts: tuple[PublicStepReceipt, ...]
    recovery_windows: tuple[PublicRecoveryWindow, ...]
    verifier: PublicVerifierResult | None
    baseline_restored: bool | None
    cleanup_terminal: CleanupTerminal | None
    owned_resources_after: tuple[StrictInt, StrictInt, StrictInt] | None
    non_owned_resources_changed: bool | None
    counters: LiveAttemptCounters
    report_sha256: Sha256

    @model_validator(mode="after")
    def require_safe_report(self) -> PublicLiveAttemptReport:
        payload = self.model_dump(mode="json", exclude={"report_sha256"})
        _require_public_projection_safe(payload)
        expected = semantic_sha256(payload)
        if self.report_sha256 != expected:
            raise ValueError("public live report digest differs")
        return self


def build_public_live_attempt_report(
    closure: LiveAttemptClosure,
) -> PublicLiveAttemptReport:
    closure = LiveAttemptClosure.model_validate(closure.model_dump(mode="python"))
    expected = {
        LiveScenario.PAYMENT: (
            "payment",
            FaultDomain.CONFIGURATION,
            FaultMechanism.CONFIGURATION_ERROR,
            RunbookId.ROLLBACK_CONFIGURATION,
        ),
        LiveScenario.RECOMMENDATION: (
            "recommendation",
            FaultDomain.SERVICE_RUNTIME,
            FaultMechanism.SERVICE_UNAVAILABLE,
            RunbookId.RESTART_SERVICE,
        ),
        LiveScenario.EMAIL: (
            "email",
            FaultDomain.LOCAL_RESOURCE,
            FaultMechanism.MEMORY_LEAK,
            RunbookId.MITIGATE_MEMORY_LEAK,
        ),
    }
    if closure.scenario is LiveScenario.NO_FAULT:
        scenario_contract_passed = (
            closure.proposal_disposition in {None, ActionDisposition.NO_ACTION.value}
            and closure.admission_verdict is AdmissionVerdict.DENY
            and closure.counters.forward_step_count == 0
            and closure.counters.fault_injection_count == 0
        )
    else:
        scenario_contract_passed = (
            (
                closure.root_service,
                closure.fault_domain,
                closure.mechanism,
                closure.runbook_id,
            )
            == expected[closure.scenario]
        )
    public_refs: list[PublicEvidenceReference] = []
    for evidence_ref in closure.evidence_refs:
        parts = evidence_ref.split("/")
        if len(parts) != 5:
            raise ValueError("private evidence reference is malformed")
        source = EvidenceSource(parts[-2].upper())
        public_refs.append(
            PublicEvidenceReference(
                reference=f"{source.value}:{parts[-1]}",
                source=source,
            )
        )
    diagnosis = (
        None
        if closure.diagnosis_sha256 is None
        else PublicDiagnosis(
            root_service=closure.root_service,
            fault_domain=closure.fault_domain,
            mechanism=closure.mechanism,
            evidence_sources=closure.evidence_source_types,
            evidence_refs=tuple(public_refs),
        )
    )
    proposal = (
        None
        if closure.proposal_disposition is None
        else PublicActionProposal(
            disposition=ActionDisposition(closure.proposal_disposition),
            runbook_id=closure.runbook_id,
            target_service=closure.proposal_target_service,
            parameters=closure.proposal_parameters,
        )
    )
    admission = (
        None
        if closure.admission_verdict is None
        else PublicOperationalAdmission(
            verdict=closure.admission_verdict,
            reason_codes=closure.admission_reason_codes,
        )
    )
    verification = closure.verification
    selected_candidate = next(
        (
            item
            for item in closure.candidates
            if item.runbook_id is closure.runbook_id
            and item.target_service == closure.proposal_target_service
        ),
        None,
    )
    authorization = (
        None
        if closure.authorization_sha256 is None
        or closure.runbook_id is None
        or closure.proposal_target_service is None
        or selected_candidate is None
        else PublicAuthorizationSummary(
            authorization_mode="DTA_V2_MASTER_RUN_BOUND_CHILD",
            runbook_id=closure.runbook_id,
            target_service=closure.proposal_target_service,
            maximum_forward_steps=(
                2
                if closure.runbook_id is RunbookId.MITIGATE_MEMORY_LEAK
                else 1
            ),
        )
    )
    verifier = (
        None
        if closure.transaction_terminal is None
        else PublicVerifierResult(
            terminal=closure.transaction_terminal,
            verifier_id=(None if verification is None else verification.verifier_id),
            outcome=(None if verification is None else verification.outcome),
            reason_codes=(
                () if verification is None else verification.reason_codes
            ),
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v2.public-live-attempt-report.v2",
        "evidence_mode": closure.mode,
        "scenario": closure.scenario,
        "fault": closure.fault_operation,
        "terminal": closure.terminal,
        "failure_code": (
            None if closure.failure_code is None else closure.failure_code.value
        ),
        "scenario_contract_passed": scenario_contract_passed,
        "tool_call_sequence": closure.tool_call_sequence,
        "tool_dispatch_count": closure.counters.read_tool_dispatch_count,
        "provider_turn_count": closure.counters.provider_turn_count,
        "diagnosis": diagnosis,
        "candidate_set": tuple(
            PublicCandidateAction(
                runbook_id=item.runbook_id,
                target_service=item.target_service,
                risk_level=item.risk_level,
                parameters=item.parameters,
                required_evidence_sources=item.required_evidence_sources,
            )
            for item in closure.candidates
        ),
        "action_proposal": proposal,
        "operational_admission": admission,
        "authorization_present": closure.authorization_sha256 is not None,
        "authorization": authorization,
        "runbook": closure.runbook_id,
        "step_receipts": tuple(
            PublicStepReceipt(
                step_ordinal=item.step_ordinal,
                step_id=item.step_id,
                target=item.target,
                outcome=item.outcome.value,
                error_code=item.error_code,
            )
            for item in closure.receipts
        ),
        "recovery_windows": tuple(
            PublicRecoveryWindow(
                ordinal=item.ordinal,
                infrastructure_passed=item.infrastructure_passed,
                business_sli_passed=item.business_sli_passed,
                endpoint_passed=item.endpoint_passed,
                configuration_restored=item.configuration_restored,
                memory_slope_bytes_per_second=item.memory_slope_bytes_per_second,
            )
            for item in closure.recovery_windows
        ),
        "verifier": verifier,
        "baseline_restored": closure.baseline_restored,
        "cleanup_terminal": closure.cleanup_terminal,
        "owned_resources_after": (
            None
            if closure.owned_containers_after is None
            or closure.owned_networks_after is None
            or closure.owned_volumes_after is None
            else (
                closure.owned_containers_after,
                closure.owned_networks_after,
                closure.owned_volumes_after,
            )
        ),
        "non_owned_resources_changed": closure.non_owned_resources_changed,
        "counters": closure.counters,
    }
    draft = PublicLiveAttemptReport.model_construct(
        **payload,  # type: ignore[arg-type]
        report_sha256="0" * 64,
    )
    return PublicLiveAttemptReport.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


class PublicLiveCampaignReport(DtaModel):
    schema_version: Literal["dta-v2.public-live-campaign-report.v1"]
    terminal: Literal[
        "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS",
        "DTA_V2_LIVE_DEMO_REVIEW_REQUIRED",
    ]
    attempts: tuple[PublicLiveAttemptReport, ...] = Field(min_length=4, max_length=4)
    total_provider_turns: StrictInt = Field(ge=0)
    total_tool_dispatches: StrictInt = Field(ge=0)
    total_fault_attempts: StrictInt = Field(ge=0)
    total_faults_applied: StrictInt = Field(ge=0)
    total_forward_steps: StrictInt = Field(ge=0)
    total_restoration_writes: StrictInt = Field(ge=0)
    unsafe_write_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    report_sha256: Sha256

    @model_validator(mode="after")
    def require_exact_campaign(self) -> PublicLiveCampaignReport:
        if tuple(item.scenario for item in self.attempts) != (
            LiveScenario.NO_FAULT,
            LiveScenario.PAYMENT,
            LiveScenario.RECOMMENDATION,
            LiveScenario.EMAIL,
        ):
            raise ValueError("public campaign attempt order differs")
        live_pass = all(
            item.evidence_mode is LiveAttemptMode.OWNED_LOCAL
            and item.terminal is LiveAttemptTerminal.LIVE_PASS
            and item.scenario_contract_passed
            for item in self.attempts
        )
        expected_terminal = (
            "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS"
            if live_pass
            else "DTA_V2_LIVE_DEMO_REVIEW_REQUIRED"
        )
        if self.terminal != expected_terminal:
            raise ValueError("public campaign terminal overclaims evidence")
        payload = self.model_dump(mode="json", exclude={"report_sha256"})
        _require_public_projection_safe(payload)
        if self.report_sha256 != semantic_sha256(payload):
            raise ValueError("public campaign report digest differs")
        return self


def build_public_live_campaign_report(
    closures: tuple[LiveAttemptClosure, ...],
) -> PublicLiveCampaignReport:
    reports = tuple(build_public_live_attempt_report(item) for item in closures)
    live_pass = len(reports) == 4 and all(
        item.evidence_mode is LiveAttemptMode.OWNED_LOCAL
        and item.terminal is LiveAttemptTerminal.LIVE_PASS
        and item.scenario_contract_passed
        for item in reports
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v2.public-live-campaign-report.v1",
        "terminal": (
            "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS"
            if live_pass
            else "DTA_V2_LIVE_DEMO_REVIEW_REQUIRED"
        ),
        "attempts": reports,
        "total_provider_turns": sum(item.provider_turn_count for item in reports),
        "total_tool_dispatches": sum(item.tool_dispatch_count for item in reports),
        "total_fault_attempts": sum(
            item.counters.fault_injection_count for item in reports
        ),
        "total_faults_applied": sum(
            item.counters.fault_injection_applied_count for item in reports
        ),
        "total_forward_steps": sum(item.counters.forward_step_count for item in reports),
        "total_restoration_writes": sum(
            item.counters.restoration_write_count for item in reports
        ),
        "unsafe_write_attempts": 0,
        "arbitrary_shell_attempts": 0,
    }
    draft = PublicLiveCampaignReport.model_construct(
        **payload,  # type: ignore[arg-type]
        report_sha256="0" * 64,
    )
    return PublicLiveCampaignReport.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


def render_public_live_demo_markdown(report: PublicLiveCampaignReport) -> str:
    typed = PublicLiveCampaignReport.model_validate(report.model_dump(mode="python"))
    lines = ["# DTA v2 Local Live Demo", "", f"Terminal: `{typed.terminal}`", ""]
    for attempt in typed.attempts:
        diagnosis = attempt.diagnosis
        lines.extend(
            [
                f"## {attempt.scenario.value}",
                "",
                f"- Attempt terminal: `{attempt.terminal.value}`",
                f"- Tool sequence: `{', '.join(attempt.tool_call_sequence) or 'none'}`",
                f"- Tool dispatches / Provider turns: {attempt.tool_dispatch_count} / {attempt.provider_turn_count}",
                f"- Diagnosis: `{('none' if diagnosis is None else f'{diagnosis.root_service} / {diagnosis.fault_domain} / {diagnosis.mechanism}')}`",
                f"- Candidate Runbooks: `{', '.join(item.runbook_id.value for item in attempt.candidate_set) or 'none'}`",
                f"- Proposal / admission / Runbook: `{('none' if attempt.action_proposal is None else attempt.action_proposal.disposition.value)} / {('none' if attempt.operational_admission is None else attempt.operational_admission.verdict.value)} / {('none' if attempt.runbook is None else attempt.runbook.value)}`",
                f"- Step receipts / recovery windows: {len(attempt.step_receipts)} / {len(attempt.recovery_windows)}",
                f"- Baseline / cleanup: `{attempt.baseline_restored} / {attempt.cleanup_terminal}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_public_live_demo_human_brief(report: PublicLiveCampaignReport) -> str:
    typed = PublicLiveCampaignReport.model_validate(report.model_dump(mode="python"))
    outcome = (
        "四个受控本地场景均完成安全闭环。"
        if typed.terminal == "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS"
        else "至少一个受控本地场景仍需审查，不能声明最终通过。"
    )
    return (
        "# DTA v2 本地演示 Human Brief\n\n"
        f"结论：`{typed.terminal}`。{outcome}\n\n"
        f"总计 {typed.total_tool_dispatches} 次只读工具调度、"
        f"{typed.total_provider_turns} 次 Provider turn、"
        f"{typed.total_forward_steps} 个受信 Runbook 前向步骤。"
        "所有动作均限定于本地已证明归属的 Sandbox；这不是生产、任意自主修复或持出恢复准确率证据。\n"
    )


def write_public_live_campaign_artifacts(
    *,
    result_root: Path,
    report: PublicLiveCampaignReport,
) -> None:
    """Write final public files only for an exact retained LIVE acceptance."""

    typed = PublicLiveCampaignReport.model_validate(report.model_dump(mode="python"))
    if typed.terminal != "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS":
        raise ValueError("public success artifacts require exact LIVE acceptance")
    root = Path(result_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("public result root must already be a regular directory")
    artifacts = {
        "dta-v2-live-demo.json": json.dumps(
            typed.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "dta-v2-live-demo.md": render_public_live_demo_markdown(typed),
        "dta-v2-live-demo-human-brief.md": render_public_live_demo_human_brief(typed),
    }
    for name, content in artifacts.items():
        path = root / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o644)
        except FileExistsError as error:
            raise FileExistsError(
                "public live result artifacts are create-once"
            ) from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())


__all__ = [
    "PrivateLiveAttemptJournal",
    "PublicLiveCampaignReport",
    "PublicLiveAttemptReport",
    "build_public_live_campaign_report",
    "build_public_live_attempt_report",
    "render_public_live_demo_human_brief",
    "render_public_live_demo_markdown",
    "write_public_live_campaign_artifacts",
]
