"""Run-bound admission and fixed one-step execution for DTA v2.1 PR-F."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Protocol, Self

from pydantic import Field, model_validator
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v21.agent import AgentRunTerminalV21, DtaAgentRunResultV21
from ecomsre.dta_v2.v21.agent_contracts import (
    AgentArmV21,
    CandidateActionViewV21,
    build_candidate_action_view_v21,
)
from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    ActionProposalV21,
    CandidateSetV21,
    DtaDiagnosisV21,
    DtaModelV21,
    EvidenceSourceV21,
    ResolvedDiagnosisEvidenceViewV21,
    RunbookBackendV21,
    RunbookIdV21,
    RunbookStepIdV21,
    Sha256V21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.live_contracts import (
    LIVE_CAMPAIGN_ORDER_V21,
    LiveCurrentStateV21,
    LiveScenarioV21,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


def _require_utc(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC")


def _semantic(value: object) -> str:
    return semantic_sha256(to_jsonable_python(value))


class LiveMasterAuthorizationV21(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-master-authorization.v1"]
    approver: Literal["Minghong Sun"]
    authorization_source: Literal[
        "USER_EXPLICIT_DTA_V21_PRF_RESOURCE_RECOVERY_AMENDMENT"
    ]
    command_execution: Literal["CODEX_DELEGATED_EXECUTION"]
    authorization_mode: Literal["DTA_V21_PRF_AMENDMENT_STANDING_AUTHORIZATION"]
    codex_autonomous_self_approval: Literal[False]
    additional_human_confirmation_required: Literal[False]
    repository_name: Literal["EcomSRE-Agent"]
    branch: Literal["codex/dta-v21-p0-pr-f-live-closeout"]
    protocol_sha256: Literal[
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    ]
    planner_identity_sha256: Literal[
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    ]
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    scenarios: tuple[LiveScenarioV21, ...]
    local_unix_docker_allowed: Literal[True]
    bounded_provider_allowed: Literal[True]
    maximum_forward_steps_per_positive_slot: Literal[1]
    arbitrary_shell_allowed: Literal[False]
    production_or_cloud_allowed: Literal[False]
    issued_at: datetime
    authorization_sha256: Sha256V21

    @classmethod
    def build(cls, *, issued_at: datetime) -> Self:
        _require_utc(issued_at, label="master authorization issue time")
        payload: dict[str, object] = {
            "schema_version": "dta-v21.pr-f-master-authorization.v1",
            "approver": "Minghong Sun",
            "authorization_source": (
                "USER_EXPLICIT_DTA_V21_PRF_RESOURCE_RECOVERY_AMENDMENT"
            ),
            "command_execution": "CODEX_DELEGATED_EXECUTION",
            "authorization_mode": ("DTA_V21_PRF_AMENDMENT_STANDING_AUTHORIZATION"),
            "codex_autonomous_self_approval": False,
            "additional_human_confirmation_required": False,
            "repository_name": "EcomSRE-Agent",
            "branch": "codex/dta-v21-p0-pr-f-live-closeout",
            "protocol_sha256": (
                "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
            ),
            "planner_identity_sha256": (
                "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
            ),
            "provider_model": "gpt-5.4-mini-2026-03-17",
            "scenarios": LIVE_CAMPAIGN_ORDER_V21,
            "local_unix_docker_allowed": True,
            "bounded_provider_allowed": True,
            "maximum_forward_steps_per_positive_slot": 1,
            "arbitrary_shell_allowed": False,
            "production_or_cloud_allowed": False,
            "issued_at": issued_at,
        }
        return cls.model_validate(
            {**payload, "authorization_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_scope_and_digest(self) -> Self:
        _require_utc(self.issued_at, label="master authorization issue time")
        if self.scenarios != LIVE_CAMPAIGN_ORDER_V21:
            raise ValueError("master authorization scenarios differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected:
            raise ValueError("master authorization SHA-256 mismatch")
        return self


class LiveOperationalAdmissionV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-operational-admission.v1"]
    verdict: Literal["ALLOW"]
    scenario: LiveScenarioV21
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    master_authorization_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    diagnosis_sha256: Sha256V21
    resolved_evidence_sha256: Sha256V21
    candidate_set_sha256: Sha256V21
    candidate_view_sha256: Sha256V21
    proposal_sha256: Sha256V21
    current_state_snapshot_sha256: Sha256V21
    current_mutation_state_sha256: Sha256V21
    registry_sha256: Sha256V21
    runbook_id: RunbookIdV21
    admitted_step: RunbookStepIdV21
    maximum_forward_steps: Literal[1]
    admission_sha256: Sha256V21

    @model_validator(mode="after")
    def require_digest(self) -> Self:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("live Operational Admission SHA-256 mismatch")
        return self


class LiveRunAuthorizationV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-run-authorization.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    scenario: LiveScenarioV21
    master_authorization_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    proposal_sha256: Sha256V21
    current_state_snapshot_sha256: Sha256V21
    current_mutation_state_sha256: Sha256V21
    admission_sha256: Sha256V21
    runbook_id: RunbookIdV21
    admitted_step: RunbookStepIdV21
    maximum_forward_steps: Literal[1]
    issued_at: datetime
    expires_at: datetime
    authorization_sha256: Sha256V21

    @model_validator(mode="after")
    def require_window_and_digest(self) -> Self:
        _require_utc(self.issued_at, label="run authorization issue time")
        _require_utc(self.expires_at, label="run authorization expiry time")
        if self.expires_at - self.issued_at != timedelta(minutes=5):
            raise ValueError("live run authorization window differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected:
            raise ValueError("live run authorization SHA-256 mismatch")
        return self


class LiveNoWriteAdmissionV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-no-write-admission.v1"]
    verdict: Literal["DENY"]
    reason: Literal["NO_FAULT_NON_WRITE_TERMINAL"]
    scenario: Literal[LiveScenarioV21.NO_FAULT]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    master_authorization_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    maximum_forward_steps: Literal[0]
    admission_sha256: Sha256V21

    @model_validator(mode="after")
    def require_digest(self) -> Self:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("live no-write admission SHA-256 mismatch")
        return self


class LivePostWriteStateV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-post-write-state.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    scenario: LiveScenarioV21
    target_service: str
    ad_high_cpu_active: bool
    target_runtime_stopped: bool
    forward_step_count: Literal[1]
    non_owned_changes: Literal[0]
    observed_at: datetime
    state_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {"schema_version": "dta-v21.live-post-write-state.v1", **values}
        return cls.model_validate({**payload, "state_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_digest(self) -> Self:
        _require_utc(self.observed_at, label="post-write observation time")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )
        if self.state_sha256 != expected:
            raise ValueError("live post-write state SHA-256 mismatch")
        return self


class LiveStepReceiptV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-step-receipt.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    proposal_sha256: Sha256V21
    admission_sha256: Sha256V21
    authorization_sha256: Sha256V21
    dispatch_intent_sha256: Sha256V21
    runbook_id: RunbookIdV21
    step_id: RunbookStepIdV21
    forward_step_ordinal: Literal[1]
    outcome: Literal["APPLIED", "MUTATION_POSSIBLE_FAILURE"]
    failure_code: Literal["FIXED_OPERATION_FAILED", "POSTCONDITION_FAILED"] | None
    before_state_sha256: Sha256V21
    after_state_sha256: Sha256V21 | None
    observed_at: datetime
    receipt_sha256: Sha256V21

    @model_validator(mode="after")
    def require_shape_and_digest(self) -> Self:
        _require_utc(self.observed_at, label="step receipt time")
        if (self.outcome == "APPLIED") != (
            self.failure_code is None and self.after_state_sha256 is not None
        ):
            raise ValueError("live step receipt outcome differs from evidence")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("live step receipt SHA-256 mismatch")
        return self


class FixedLiveControlsV21(Protocol):
    def revalidate(self) -> LiveCurrentStateV21: ...

    def disable_ad_high_cpu_flag(self) -> None: ...

    def start_owned_service(self, *, wait_for_health_seconds: int) -> None: ...

    def observe_postcondition(
        self, *, step: RunbookStepIdV21, observed_at: datetime
    ) -> LivePostWriteStateV21: ...


class LiveDispatchIntentV21(DtaModelV21):
    schema_version: Literal["dta-v21.live-dispatch-intent.v1"]
    status: Literal["DISPATCH_INTENT"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    proposal_sha256: Sha256V21
    admission_sha256: Sha256V21
    authorization_sha256: Sha256V21
    runbook_id: RunbookIdV21
    step_id: RunbookStepIdV21
    before_state_sha256: Sha256V21
    recorded_at: datetime
    intent_sha256: Sha256V21

    @model_validator(mode="after")
    def require_digest(self) -> Self:
        _require_utc(self.recorded_at, label="dispatch intent time")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"intent_sha256"})
        )
        if self.intent_sha256 != expected:
            raise ValueError("live dispatch intent SHA-256 mismatch")
        return self


class LiveReceiptJournalV21(Protocol):
    def record_intent(self, intent: LiveDispatchIntentV21) -> None: ...

    def record_postcondition(self, state: LivePostWriteStateV21) -> None: ...

    def append(self, receipt: LiveStepReceiptV21) -> None: ...


class LiveExecutionFailureV21(RuntimeError):
    def __init__(self, receipt: LiveStepReceiptV21) -> None:
        super().__init__(receipt.failure_code or "live execution failure")
        self.receipt = receipt


def _expected_action(
    scenario: LiveScenarioV21,
) -> tuple[str, RunbookIdV21, RunbookStepIdV21]:
    try:
        return {
            LiveScenarioV21.AD_CPU_SATURATION: (
                "ad",
                RunbookIdV21.MITIGATE_CPU_SATURATION,
                RunbookStepIdV21.DISABLE_AD_HIGH_CPU_FLAG,
            ),
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE: (
                "email",
                RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
                RunbookStepIdV21.START_OWNED_SERVICE,
            ),
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE: (
                "product-catalog",
                RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
                RunbookStepIdV21.START_OWNED_SERVICE,
            ),
        }[scenario]
    except KeyError as exc:
        raise ValueError("no-fault does not admit a forward write") from exc


def _evidence_source(reference: str) -> EvidenceSourceV21:
    source = reference.split("/")[3]
    return {
        "metrics": EvidenceSourceV21.METRICS,
        "runtime": EvidenceSourceV21.RUNTIME,
        "resources": EvidenceSourceV21.RESOURCES,
        "traces": EvidenceSourceV21.TRACES,
        "logs": EvidenceSourceV21.LOGS,
        "changes": EvidenceSourceV21.CHANGES,
    }[source]


def admit_live_action_v21(
    *,
    scenario: LiveScenarioV21,
    agent_result: DtaAgentRunResultV21,
    registry: RunbookRegistryV21,
    current_state: LiveCurrentStateV21,
    master_authorization: LiveMasterAuthorizationV21,
    issued_at: datetime,
) -> tuple[LiveOperationalAdmissionV21, LiveRunAuthorizationV21]:
    """Derive child authority from full Agent artifacts and trusted live state."""

    master = LiveMasterAuthorizationV21.model_validate(
        master_authorization.model_dump(mode="python")
    )
    result = DtaAgentRunResultV21.model_validate(agent_result.model_dump(mode="python"))
    if (
        result.terminal is not AgentRunTerminalV21.COMPLETED
        or result.arm is not AgentArmV21.EVIDENCE_GUIDED_PLANNER
        or result.identity.identity_sha256 != master.planner_identity_sha256
        or result.identity.model_id != master.provider_model
        or result.diagnosis is None
        or result.resolved_evidence is None
        or result.candidate_set is None
        or result.candidate_view is None
        or result.action_proposal is None
    ):
        raise ValueError("live admission requires one complete frozen Agent result")
    diagnosis = DtaDiagnosisV21.model_validate(
        result.diagnosis.model_dump(mode="python")
    )
    resolved = ResolvedDiagnosisEvidenceViewV21.model_validate(
        result.resolved_evidence.model_dump(mode="python")
    )
    candidates = CandidateSetV21.model_validate(
        result.candidate_set.model_dump(mode="python")
    )
    candidate_view = CandidateActionViewV21.model_validate(
        result.candidate_view.model_dump(mode="python")
    )
    proposal = ActionProposalV21.model_validate(
        result.action_proposal.model_dump(mode="python")
    )
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    state = LiveCurrentStateV21.model_validate(current_state.model_dump(mode="python"))
    _require_utc(issued_at, label="live child authorization issue time")
    if issued_at < master.issued_at or scenario not in master.scenarios:
        raise ValueError("live master authorization is not applicable")
    target, runbook_id, step = _expected_action(scenario)
    if state.scenario is not scenario or state.target_service != target:
        raise ValueError("live current-state scenario target differs")
    if result.run_id != state.run_id:
        raise ValueError("live Agent result belongs to another run")
    if state.fault_operation_count != 1 or state.prior_forward_step_count != 0:
        raise ValueError("live current-state write count differs")
    if state.non_owned_changes != 0 or state.active_transaction_count != 0:
        raise ValueError("live current-state contains unsafe concurrent changes")
    if scenario is LiveScenarioV21.AD_CPU_SATURATION:
        if not state.ad_high_cpu_active:
            raise ValueError("Ad high-CPU fault flag is not active")
        if state.target_runtime_stopped:
            raise ValueError("Ad target unexpectedly stopped")
    elif not state.target_runtime_stopped:
        raise ValueError("service-unavailable target is not stopped")

    diagnosis_sha = semantic_sha256(diagnosis.model_dump(mode="json"))
    rebuilt_candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=resolved,
        registry=registry,
        exact_target=target,
    )
    if rebuilt_candidates != candidates:
        raise ValueError("live CandidateSet differs from deterministic recomputation")
    if candidate_view != build_candidate_action_view_v21(candidates):
        raise ValueError("live CandidateActionView differs from the CandidateSet")
    candidate_view_sha = _semantic(candidate_view)
    if (
        proposal.run_id != state.run_id
        or diagnosis.run_id != state.run_id
        or resolved.run_id != state.run_id
        or candidates.run_id != state.run_id
        or proposal.disposition is not ActionDispositionV21.EXECUTE_RUNBOOK
        or proposal.diagnosis_sha256 != diagnosis_sha
        or proposal.resolved_evidence_sha256 != resolved.resolved_evidence_sha256
        or proposal.candidate_set_sha256 != candidates.candidate_set_sha256
        or proposal.registry_sha256 != registry.registry_sha256
        or proposal.runbook_id is not runbook_id
        or proposal.target_service != target
    ):
        raise ValueError("live proposal differs from the exact bound Agent artifacts")
    runbook = registry.require(runbook_id)
    exact_candidates = tuple(
        item
        for item in candidates.write_candidates
        if item.runbook_id is runbook_id and item.target_service == target
    )
    if len(exact_candidates) != 1 or len(candidates.write_candidates) != 1:
        raise ValueError("live CandidateSet is not the exact single write candidate")
    if (
        proposal.runbook_sha256 != runbook.semantic_sha256
        or runbook.backend is not RunbookBackendV21.LIVE_ALLOWED
        or runbook.maximum_forward_steps != 1
        or tuple(item.step_id for item in runbook.forward_steps) != (step,)
    ):
        raise ValueError("live Runbook binding differs")
    expected_executor = {
        RunbookIdV21.MITIGATE_CPU_SATURATION: "AdCpuMitigationExecutorV21",
        RunbookIdV21.RESTORE_SERVICE_AVAILABILITY: "OwnedServiceStartExecutorV21",
    }[runbook_id]
    expected_verifier = {
        RunbookIdV21.MITIGATE_CPU_SATURATION: "AdCpuRecoveryVerifierV21",
        RunbookIdV21.RESTORE_SERVICE_AVAILABILITY: "OwnedServiceRecoveryVerifierV21",
    }[runbook_id]
    if (
        runbook.executor_id != expected_executor
        or runbook.verifier_id != expected_verifier
    ):
        raise ValueError("live Runbook Executor or Verifier differs")
    if scenario is LiveScenarioV21.AD_CPU_SATURATION:
        if proposal.parameters:
            raise ValueError("Ad live proposal parameters are forbidden")
    else:
        observed_parameters = tuple(
            (item.name, item.value) for item in proposal.parameters
        )
        if (
            len(observed_parameters) != 1
            or observed_parameters[0][0] != "wait_for_health_seconds"
            or not isinstance(observed_parameters[0][1], int)
            or not 5 <= observed_parameters[0][1] <= 120
        ):
            raise ValueError("service live proposal parameters differ from the Runbook")
    cited_sources = {
        _evidence_source(item) for item in proposal.supporting_evidence_refs
    }
    required_sources = set(runbook.required_evidence_for_target(target))
    if not required_sources.issubset(cited_sources):
        raise ValueError("live proposal lacks required evidence-source coverage")
    resolved_refs = {item.evidence_ref for item in resolved.evidence}
    if not set(proposal.supporting_evidence_refs).issubset(resolved_refs):
        raise ValueError("live proposal cites evidence outside the resolved view")

    admission_payload: dict[str, object] = {
        "schema_version": "dta-v21.live-operational-admission.v1",
        "verdict": "ALLOW",
        "scenario": scenario,
        "run_id": state.run_id,
        "attempt_id": state.attempt_id,
        "master_authorization_sha256": master.authorization_sha256,
        "agent_result_sha256": result.result_sha256,
        "diagnosis_sha256": diagnosis_sha,
        "resolved_evidence_sha256": resolved.resolved_evidence_sha256,
        "candidate_set_sha256": candidates.candidate_set_sha256,
        "candidate_view_sha256": candidate_view_sha,
        "proposal_sha256": proposal.proposal_sha256,
        "current_state_snapshot_sha256": state.snapshot_sha256,
        "current_mutation_state_sha256": state.current_state_sha256,
        "registry_sha256": registry.registry_sha256,
        "runbook_id": runbook_id,
        "admitted_step": step,
        "maximum_forward_steps": 1,
    }
    admission = LiveOperationalAdmissionV21.model_validate(
        {**admission_payload, "admission_sha256": _semantic(admission_payload)}
    )
    authorization_payload: dict[str, object] = {
        "schema_version": "dta-v21.live-run-authorization.v1",
        "run_id": state.run_id,
        "attempt_id": state.attempt_id,
        "scenario": scenario,
        "master_authorization_sha256": master.authorization_sha256,
        "agent_result_sha256": result.result_sha256,
        "proposal_sha256": proposal.proposal_sha256,
        "current_state_snapshot_sha256": state.snapshot_sha256,
        "current_mutation_state_sha256": state.current_state_sha256,
        "admission_sha256": admission.admission_sha256,
        "runbook_id": runbook_id,
        "admitted_step": step,
        "maximum_forward_steps": 1,
        "issued_at": issued_at,
        "expires_at": issued_at + timedelta(minutes=5),
    }
    authorization = LiveRunAuthorizationV21.model_validate(
        {
            **authorization_payload,
            "authorization_sha256": _semantic(authorization_payload),
        }
    )
    return admission, authorization


def deny_no_fault_live_action_v21(
    *,
    agent_result: DtaAgentRunResultV21,
    registry: RunbookRegistryV21,
    attempt_id: str,
    master_authorization: LiveMasterAuthorizationV21,
) -> LiveNoWriteAdmissionV21:
    """Persist a hash-bound DENY for an accepted no-fault Agent terminal."""

    master = LiveMasterAuthorizationV21.model_validate(
        master_authorization.model_dump(mode="python")
    )
    result = DtaAgentRunResultV21.model_validate(agent_result.model_dump(mode="python"))
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    if (
        result.arm is not AgentArmV21.EVIDENCE_GUIDED_PLANNER
        or result.identity.identity_sha256 != master.planner_identity_sha256
        or result.identity.model_id != master.provider_model
        or LiveScenarioV21.NO_FAULT not in master.scenarios
        or result.diagnosis is None
    ):
        raise ValueError("no-fault denial requires the frozen Planner result")
    diagnosis = DtaDiagnosisV21.model_validate(
        result.diagnosis.model_dump(mode="python")
    )
    completed_no_action = result.terminal is AgentRunTerminalV21.COMPLETED
    accepted_abstain = result.terminal is AgentRunTerminalV21.ABSTAIN
    if completed_no_action:
        if (
            result.resolved_evidence is None
            or result.candidate_set is None
            or result.candidate_view is None
            or result.action_proposal is None
            or diagnosis.terminal is not TerminalV21.COMPLETED
            or diagnosis.root_service is not None
            or diagnosis.root_entity_ref is not None
            or diagnosis.fault_domain is not None
            or diagnosis.mechanism is not None
        ):
            raise ValueError("no-fault completed result contains a write shape")
        resolved = ResolvedDiagnosisEvidenceViewV21.model_validate(
            result.resolved_evidence.model_dump(mode="python")
        )
        candidates = CandidateSetV21.model_validate(
            result.candidate_set.model_dump(mode="python")
        )
        candidate_view = CandidateActionViewV21.model_validate(
            result.candidate_view.model_dump(mode="python")
        )
        proposal = ActionProposalV21.model_validate(
            result.action_proposal.model_dump(mode="python")
        )
        rebuilt = filter_runbook_candidates(
            diagnosis=diagnosis,
            diagnosis_evidence=resolved,
            registry=registry,
            exact_target=None,
        )
        if (
            rebuilt != candidates
            or candidate_view != build_candidate_action_view_v21(candidates)
            or candidates.write_candidates
            or proposal.disposition is not ActionDispositionV21.NO_ACTION
            or proposal.run_id != result.run_id
            or proposal.diagnosis_sha256
            != semantic_sha256(diagnosis.model_dump(mode="json"))
            or proposal.resolved_evidence_sha256 != resolved.resolved_evidence_sha256
            or proposal.candidate_set_sha256 != candidates.candidate_set_sha256
            or proposal.registry_sha256 != registry.registry_sha256
            or proposal.runbook_id is not None
            or proposal.target_service is not None
            or proposal.parameters
        ):
            raise ValueError("no-fault completed result is not an exact NO_ACTION")
    elif accepted_abstain:
        if (
            diagnosis.terminal is not TerminalV21.ABSTAIN
            or diagnosis.root_service is not None
            or diagnosis.root_entity_ref is not None
            or diagnosis.fault_domain is not None
            or diagnosis.mechanism is not None
            or any(
                item is not None
                for item in (
                    result.resolved_evidence,
                    result.candidate_set,
                    result.candidate_view,
                    result.action_proposal,
                )
            )
        ):
            raise ValueError("no-fault abstain result contains a write shape")
    else:
        raise ValueError("no-fault result is not an accepted non-write terminal")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.live-no-write-admission.v1",
        "verdict": "DENY",
        "reason": "NO_FAULT_NON_WRITE_TERMINAL",
        "scenario": LiveScenarioV21.NO_FAULT,
        "run_id": result.run_id,
        "attempt_id": attempt_id,
        "master_authorization_sha256": master.authorization_sha256,
        "agent_result_sha256": result.result_sha256,
        "maximum_forward_steps": 0,
    }
    return LiveNoWriteAdmissionV21.model_validate(
        {**payload, "admission_sha256": _semantic(payload)}
    )


def _build_receipt(
    *,
    proposal: ActionProposalV21,
    admission: LiveOperationalAdmissionV21,
    authorization: LiveRunAuthorizationV21,
    dispatch_intent: LiveDispatchIntentV21,
    current_state: LiveCurrentStateV21,
    observed_at: datetime,
    outcome: Literal["APPLIED", "MUTATION_POSSIBLE_FAILURE"],
    failure_code: Literal["FIXED_OPERATION_FAILED", "POSTCONDITION_FAILED"] | None,
    after_state_sha256: str | None,
) -> LiveStepReceiptV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.live-step-receipt.v1",
        "run_id": authorization.run_id,
        "attempt_id": authorization.attempt_id,
        "proposal_sha256": proposal.proposal_sha256,
        "admission_sha256": admission.admission_sha256,
        "authorization_sha256": authorization.authorization_sha256,
        "dispatch_intent_sha256": dispatch_intent.intent_sha256,
        "runbook_id": authorization.runbook_id,
        "step_id": authorization.admitted_step,
        "forward_step_ordinal": 1,
        "outcome": outcome,
        "failure_code": failure_code,
        "before_state_sha256": current_state.snapshot_sha256,
        "after_state_sha256": after_state_sha256,
        "observed_at": observed_at,
    }
    return LiveStepReceiptV21.model_validate(
        {**payload, "receipt_sha256": _semantic(payload)}
    )


def execute_fixed_live_step_v21(
    *,
    proposal: ActionProposalV21,
    current_state: LiveCurrentStateV21,
    admission: LiveOperationalAdmissionV21,
    authorization: LiveRunAuthorizationV21,
    controls: FixedLiveControlsV21,
    receipt_journal: LiveReceiptJournalV21,
    observed_at: datetime,
) -> LiveStepReceiptV21:
    """Dispatch once, observe the postcondition, and journal before returning."""

    _require_utc(observed_at, label="live execution time")
    if not authorization.issued_at <= observed_at < authorization.expires_at:
        raise ValueError("live run authorization is not current")
    if (
        proposal.proposal_sha256 != admission.proposal_sha256
        or proposal.proposal_sha256 != authorization.proposal_sha256
        or current_state.snapshot_sha256 != admission.current_state_snapshot_sha256
        or current_state.snapshot_sha256 != authorization.current_state_snapshot_sha256
        or current_state.current_state_sha256 != admission.current_mutation_state_sha256
        or current_state.current_state_sha256
        != authorization.current_mutation_state_sha256
        or admission.admission_sha256 != authorization.admission_sha256
        or admission.agent_result_sha256 != authorization.agent_result_sha256
        or admission.admitted_step is not authorization.admitted_step
        or admission.runbook_id is not authorization.runbook_id
    ):
        raise ValueError("live execution authority binding differs")
    fresh = controls.revalidate()
    comparable = (
        fresh.run_id,
        fresh.attempt_id,
        fresh.scenario,
        fresh.target_service,
        fresh.owned_target_identity_sha256,
        fresh.daemon_identity_sha256,
        fresh.docker_context_sha256,
        fresh.ownership_scope_sha256,
        fresh.sandbox_identity_sha256,
        fresh.baseline_state_sha256,
        fresh.current_state_sha256,
        fresh.ad_high_cpu_active,
        fresh.target_runtime_stopped,
        fresh.fault_operation_count,
        fresh.prior_forward_step_count,
        fresh.active_transaction_count,
        fresh.non_owned_changes,
    )
    admitted = (
        current_state.run_id,
        current_state.attempt_id,
        current_state.scenario,
        current_state.target_service,
        current_state.owned_target_identity_sha256,
        current_state.daemon_identity_sha256,
        current_state.docker_context_sha256,
        current_state.ownership_scope_sha256,
        current_state.sandbox_identity_sha256,
        current_state.baseline_state_sha256,
        current_state.current_state_sha256,
        current_state.ad_high_cpu_active,
        current_state.target_runtime_stopped,
        current_state.fault_operation_count,
        current_state.prior_forward_step_count,
        current_state.active_transaction_count,
        current_state.non_owned_changes,
    )
    if comparable != admitted:
        raise ValueError("live current state drifted before the forward write")
    intent_payload: dict[str, object] = {
        "schema_version": "dta-v21.live-dispatch-intent.v1",
        "status": "DISPATCH_INTENT",
        "run_id": authorization.run_id,
        "attempt_id": authorization.attempt_id,
        "proposal_sha256": proposal.proposal_sha256,
        "admission_sha256": admission.admission_sha256,
        "authorization_sha256": authorization.authorization_sha256,
        "runbook_id": authorization.runbook_id,
        "step_id": authorization.admitted_step,
        "before_state_sha256": current_state.snapshot_sha256,
        "recorded_at": observed_at,
    }
    dispatch_intent = LiveDispatchIntentV21.model_validate(
        {**intent_payload, "intent_sha256": _semantic(intent_payload)}
    )
    receipt_journal.record_intent(dispatch_intent)
    try:
        if authorization.admitted_step is RunbookStepIdV21.DISABLE_AD_HIGH_CPU_FLAG:
            controls.disable_ad_high_cpu_flag()
        elif authorization.admitted_step is RunbookStepIdV21.START_OWNED_SERVICE:
            parameters = tuple((item.name, item.value) for item in proposal.parameters)
            if (
                len(parameters) != 1
                or parameters[0][0] != "wait_for_health_seconds"
                or not isinstance(parameters[0][1], int)
                or not 5 <= parameters[0][1] <= 120
            ):
                raise ValueError("service fixed operation parameter differs")
            controls.start_owned_service(wait_for_health_seconds=parameters[0][1])
        else:  # pragma: no cover - closed by the authorization model
            raise ValueError("live authorization contains no fixed operation")
    except Exception as exc:
        receipt = _build_receipt(
            proposal=proposal,
            admission=admission,
            authorization=authorization,
            dispatch_intent=dispatch_intent,
            current_state=current_state,
            observed_at=observed_at,
            outcome="MUTATION_POSSIBLE_FAILURE",
            failure_code="FIXED_OPERATION_FAILED",
            after_state_sha256=None,
        )
        receipt_journal.append(receipt)
        raise LiveExecutionFailureV21(receipt) from exc
    try:
        after = controls.observe_postcondition(
            step=authorization.admitted_step, observed_at=observed_at
        )
        receipt_journal.record_postcondition(after)
        if (
            after.run_id != authorization.run_id
            or after.attempt_id != authorization.attempt_id
            or after.scenario is not authorization.scenario
            or after.target_service != current_state.target_service
            or after.non_owned_changes != 0
            or (
                authorization.admitted_step is RunbookStepIdV21.DISABLE_AD_HIGH_CPU_FLAG
                and after.ad_high_cpu_active
            )
            or (
                authorization.admitted_step is RunbookStepIdV21.START_OWNED_SERVICE
                and after.target_runtime_stopped
            )
        ):
            raise ValueError("fixed live operation postcondition did not pass")
    except Exception as exc:
        receipt = _build_receipt(
            proposal=proposal,
            admission=admission,
            authorization=authorization,
            dispatch_intent=dispatch_intent,
            current_state=current_state,
            observed_at=observed_at,
            outcome="MUTATION_POSSIBLE_FAILURE",
            failure_code="POSTCONDITION_FAILED",
            after_state_sha256=None,
        )
        receipt_journal.append(receipt)
        raise LiveExecutionFailureV21(receipt) from exc
    receipt = _build_receipt(
        proposal=proposal,
        admission=admission,
        authorization=authorization,
        dispatch_intent=dispatch_intent,
        current_state=current_state,
        observed_at=observed_at,
        outcome="APPLIED",
        failure_code=None,
        after_state_sha256=after.state_sha256,
    )
    receipt_journal.append(receipt)
    return receipt


__all__ = (
    "FixedLiveControlsV21",
    "LiveExecutionFailureV21",
    "LiveDispatchIntentV21",
    "LiveMasterAuthorizationV21",
    "LiveNoWriteAdmissionV21",
    "LiveOperationalAdmissionV21",
    "LivePostWriteStateV21",
    "LiveReceiptJournalV21",
    "LiveRunAuthorizationV21",
    "LiveStepReceiptV21",
    "admit_live_action_v21",
    "deny_no_fault_live_action_v21",
    "execute_fixed_live_step_v21",
)
