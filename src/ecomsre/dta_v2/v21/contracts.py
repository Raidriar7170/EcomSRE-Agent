"""Closed-world contracts for the DTA v2.1 successor."""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from ecomsre.dta_v2.contracts import semantic_sha256


IdentifierV21 = Annotated[
    str,
    Strict(),
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
RunIdV21 = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Sha256V21 = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_EVIDENCE_REF_RE = re.compile(
    r"^evidence://(?P<run_id>[0-9a-f]{32})/"
    r"(?P<source>metrics|logs|traces|runtime|resources|changes)/[0-9]{4}$"
)
_EVALUATOR_MARKERS = (
    "expected root",
    "expected mechanism",
    "expected runbook",
    "ground truth",
    "scenario truth",
    "answer key",
    "evaluator only",
    "injected fault",
    "held out",
)
_SCENARIO_CONTROL_MARKERS = (
    "adhighcpu",
    "emailmemoryleak",
    "intlshippingslowdown",
    "paymentfailure",
)
_EXECUTABLE_TEXT_RE = re.compile(
    r"(?i)(?:&&|\|\||[`$<>])|"
    r"\b(?:sudo|bash|zsh|docker|kubectl|curl|wget|terraform|ansible)\b"
)
_UNSAFE_PARAMETER_NAMES = {
    "argv",
    "command",
    "container_id",
    "docker_args",
    "endpoint",
    "executor",
    "file_path",
    "path",
    "risk",
    "risk_level",
    "shell",
    "shell_command",
    "url",
    "verifier",
}


class DtaModelV21(BaseModel):
    """Immutable strict value object with no undeclared fields."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )


def _safe_text(value: object, *, field_name: str, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    if len(text) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    compact = re.sub(r"\s+", "", text.casefold())
    if any(marker in normalized for marker in _EVALUATOR_MARKERS):
        raise ValueError(f"{field_name} contains evaluator truth")
    if any(marker in compact for marker in _SCENARIO_CONTROL_MARKERS):
        raise ValueError(f"{field_name} contains a scenario-control field")
    if _EXECUTABLE_TEXT_RE.search(text):
        raise ValueError(f"{field_name} contains executable text")
    return text


class TerminalV21(str, Enum):
    COMPLETED = "COMPLETED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    ABSTAIN = "ABSTAIN"


class EvidenceSourceV21(str, Enum):
    METRICS = "METRICS"
    LOGS = "LOGS"
    TRACES = "TRACES"
    RUNTIME = "RUNTIME"
    RESOURCES = "RESOURCES"
    CHANGES = "CHANGES"


_SOURCE_BY_REF = {
    "metrics": EvidenceSourceV21.METRICS,
    "logs": EvidenceSourceV21.LOGS,
    "traces": EvidenceSourceV21.TRACES,
    "runtime": EvidenceSourceV21.RUNTIME,
    "resources": EvidenceSourceV21.RESOURCES,
    "changes": EvidenceSourceV21.CHANGES,
}
_EVIDENCE_SOURCE_ORDER = {
    source: index for index, source in enumerate(EvidenceSourceV21)
}


def evidence_source_from_ref(reference: str) -> EvidenceSourceV21:
    match = _EVIDENCE_REF_RE.fullmatch(reference)
    if match is None:
        raise ValueError("invalid evidence reference")
    return _SOURCE_BY_REF[match.group("source")]


def validate_evidence_refs(
    values: tuple[str, ...], *, run_id: str, label: str
) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate evidence references")
    for value in values:
        match = _EVIDENCE_REF_RE.fullmatch(value)
        if match is None:
            raise ValueError(f"{label} contains an invalid evidence reference")
        if match.group("run_id") != run_id:
            raise ValueError(f"{label} is outside the current run")
    ordered = tuple(
        sorted(
            values,
            key=lambda ref: (
                _EVIDENCE_SOURCE_ORDER[evidence_source_from_ref(ref)],
                ref,
            ),
        )
    )
    if values != ordered:
        raise ValueError(f"{label} is not canonically ordered")
    return values


class FaultDomainV21(str, Enum):
    APPLICATION = "APPLICATION"
    CONFIGURATION = "CONFIGURATION"
    SERVICE_RUNTIME = "SERVICE_RUNTIME"
    LOCAL_RESOURCE = "LOCAL_RESOURCE"
    NETWORK = "NETWORK"
    DEPENDENCY = "DEPENDENCY"
    QUEUE = "QUEUE"
    UNKNOWN = "UNKNOWN"


class FaultMechanismV21(str, Enum):
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    MEMORY_LEAK = "MEMORY_LEAK"
    CPU_SATURATION = "CPU_SATURATION"
    DEPENDENCY_LATENCY = "DEPENDENCY_LATENCY"
    UNKNOWN = "UNKNOWN"


class RiskLevelV21(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RunbookBackendV21(str, Enum):
    LIVE_ALLOWED = "LIVE_ALLOWED"
    REPLAY_ONLY = "REPLAY_ONLY"


class ExecutionBackendV21(str, Enum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"


class RunbookIdV21(str, Enum):
    ROLLBACK_CONFIGURATION = "ROLLBACK_CONFIGURATION"
    RESTART_SERVICE = "RESTART_SERVICE"
    MITIGATE_MEMORY_LEAK = "MITIGATE_MEMORY_LEAK"
    MITIGATE_CPU_SATURATION = "MITIGATE_CPU_SATURATION"
    RESTORE_SERVICE_AVAILABILITY = "RESTORE_SERVICE_AVAILABILITY"
    RESTORE_DEPENDENCY_LATENCY = "RESTORE_DEPENDENCY_LATENCY"


class PreconditionV21(str, Enum):
    LOCAL_DOCKER_ONLY = "LOCAL_DOCKER_ONLY"
    OWNED_SERVICE = "OWNED_SERVICE"
    CONFIGURATION_DRIFT_VISIBLE = "CONFIGURATION_DRIFT_VISIBLE"
    SERVICE_NOT_HEALTHY = "SERVICE_NOT_HEALTHY"
    LEAK_FLAG_ACTIVE = "LEAK_FLAG_ACTIVE"
    CPU_FLAG_ACTIVE = "CPU_FLAG_ACTIVE"
    BASELINE_HASH_BOUND = "BASELINE_HASH_BOUND"
    CAUSAL_DEPENDENCY_EVIDENCE = "CAUSAL_DEPENDENCY_EVIDENCE"


class RunbookStepIdV21(str, Enum):
    RESTORE_BASELINE_CONFIGURATION = "RESTORE_BASELINE_CONFIGURATION"
    RESTART_OWNED_SERVICE = "RESTART_OWNED_SERVICE"
    DISABLE_LEAK_FLAG = "DISABLE_LEAK_FLAG"
    DISABLE_AD_HIGH_CPU_FLAG = "DISABLE_AD_HIGH_CPU_FLAG"
    START_OWNED_SERVICE = "START_OWNED_SERVICE"
    RESTORE_DEPENDENCY_LATENCY_REPLAY = "RESTORE_DEPENDENCY_LATENCY_REPLAY"


class RunbookParameterTypeV21(str, Enum):
    STRING = "STRING"
    INTEGER = "INTEGER"


class ActionDispositionV21(str, Enum):
    EXECUTE_RUNBOOK = "EXECUTE_RUNBOOK"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    NO_ACTION = "NO_ACTION"


ReadToolNameV21 = Literal[
    "query_metrics",
    "search_logs",
    "query_trace_neighborhood",
    "inspect_service_runtime",
    "inspect_resource_usage",
]


class ResolvedEvidenceV21(DtaModelV21):
    evidence_ref: str
    source: EvidenceSourceV21
    service_scope: tuple[IdentifierV21, ...] = Field(min_length=1, max_length=8)
    artifact_sha256: Sha256V21

    @model_validator(mode="after")
    def require_reference_and_scope_binding(self) -> ResolvedEvidenceV21:
        if evidence_source_from_ref(self.evidence_ref) is not self.source:
            raise ValueError("resolved evidence source differs from its reference")
        if len(self.service_scope) != len(set(self.service_scope)):
            raise ValueError("resolved evidence service scope contains duplicates")
        if self.service_scope != tuple(sorted(self.service_scope)):
            raise ValueError("resolved evidence service scope is not canonical")
        return self


class ResolvedDiagnosisEvidenceViewV21(DtaModelV21):
    schema_version: Literal["dta-v21.resolved-diagnosis-evidence-view.v1"]
    run_id: RunIdV21
    evidence: tuple[ResolvedEvidenceV21, ...] = Field(min_length=1, max_length=64)
    resolved_evidence_sha256: Sha256V21

    @model_validator(mode="after")
    def require_digest_and_canonical_order(self) -> ResolvedDiagnosisEvidenceViewV21:
        refs = tuple(item.evidence_ref for item in self.evidence)
        validate_evidence_refs(refs, run_id=self.run_id, label="resolved evidence")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"resolved_evidence_sha256"})
        )
        if self.resolved_evidence_sha256 != expected:
            raise ValueError("resolved evidence digest does not bind the view")
        return self


def build_resolved_diagnosis_evidence_view_v21(
    *, run_id: str, evidence: tuple[ResolvedEvidenceV21, ...]
) -> ResolvedDiagnosisEvidenceViewV21:
    ordered = tuple(
        sorted(
            evidence,
            key=lambda item: (
                _EVIDENCE_SOURCE_ORDER[item.source],
                item.evidence_ref,
            ),
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.resolved-diagnosis-evidence-view.v1",
        "run_id": run_id,
        "evidence": ordered,
    }
    digest_payload = {
        **payload,
        "evidence": [item.model_dump(mode="json") for item in ordered],
    }
    return ResolvedDiagnosisEvidenceViewV21.model_validate(
        {**payload, "resolved_evidence_sha256": semantic_sha256(digest_payload)}
    )


class DtaDiagnosisV21(DtaModelV21):
    schema_version: Literal["dta-v21.diagnosis.v1"]
    run_id: RunIdV21
    terminal: TerminalV21
    root_service: IdentifierV21 | None = None
    root_entity_ref: IdentifierV21 | None = None
    fault_domain: FaultDomainV21 | None = None
    mechanism: FaultMechanismV21 | None = None
    confidence: StrictFloat | None = Field(default=None, ge=0, le=1)
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    evidence_source_types: tuple[EvidenceSourceV21, ...] = Field(max_length=6)
    uncertainties: tuple[str, ...] = Field(max_length=16)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("summary", mode="before")
    @classmethod
    def require_safe_summary(cls, value: object) -> str:
        return _safe_text(value, field_name="summary")

    @field_validator("uncertainties")
    @classmethod
    def require_safe_uncertainties(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_text(value, field_name="uncertainty") for value in values)

    @model_validator(mode="after")
    def require_terminal_and_evidence_semantics(self) -> DtaDiagnosisV21:
        support = validate_evidence_refs(
            self.supporting_evidence_refs,
            run_id=self.run_id,
            label="supporting evidence",
        )
        contradict = validate_evidence_refs(
            self.contradicting_evidence_refs,
            run_id=self.run_id,
            label="contradicting evidence",
        )
        if set(support).intersection(contradict):
            raise ValueError("evidence cannot both support and contradict a diagnosis")
        actual_sources = {evidence_source_from_ref(ref) for ref in support + contradict}
        if set(self.evidence_source_types) != actual_sources:
            raise ValueError("evidence source accounting does not match references")
        if self.evidence_source_types != tuple(
            sorted(
                self.evidence_source_types,
                key=lambda item: _EVIDENCE_SOURCE_ORDER[item],
            )
        ):
            raise ValueError("evidence source accounting is not canonical")

        claims = (
            self.root_service,
            self.root_entity_ref,
            self.fault_domain,
            self.mechanism,
        )
        all_claims = all(value is not None for value in claims)
        no_claims = all(value is None for value in claims)
        if self.terminal is TerminalV21.COMPLETED:
            if not support:
                raise ValueError("completed diagnosis requires supporting evidence")
            if not (all_claims or no_claims):
                raise ValueError("completed diagnosis has a partial fault claim")
            if all_claims:
                if self.root_entity_ref != f"service:{self.root_service}":
                    raise ValueError("root entity must bind the diagnosed service")
                if self.confidence is None:
                    raise ValueError("fault diagnosis requires confidence telemetry")
            elif self.confidence is not None:
                raise ValueError("no-fault diagnosis cannot report fault confidence")
        else:
            if not no_claims or self.confidence is not None:
                raise ValueError("noncompleted diagnosis cannot claim a fault")
            if not self.uncertainties:
                raise ValueError("noncompleted diagnosis requires an uncertainty")
        return self


class RunbookParameterSpecV21(DtaModelV21):
    name: IdentifierV21
    parameter_type: RunbookParameterTypeV21
    required: StrictBool = True
    minimum: StrictInt | None = None
    maximum: StrictInt | None = None
    allowed_values: tuple[str, ...] = Field(default=(), max_length=16)
    default_value: str | StrictInt | None = None

    @field_validator("name")
    @classmethod
    def reject_authority_names(cls, value: str) -> str:
        if value.casefold() in _UNSAFE_PARAMETER_NAMES:
            raise ValueError("forbidden parameter name")
        return value

    @model_validator(mode="after")
    def require_typed_bounds_and_default(self) -> RunbookParameterSpecV21:
        if self.parameter_type is RunbookParameterTypeV21.INTEGER:
            if self.allowed_values:
                raise ValueError("integer parameter cannot declare string values")
            if (
                self.minimum is not None
                and self.maximum is not None
                and self.minimum > self.maximum
            ):
                raise ValueError("parameter minimum exceeds maximum")
            if self.default_value is not None:
                if isinstance(self.default_value, bool) or not isinstance(
                    self.default_value, int
                ):
                    raise ValueError("integer parameter has a non-integer default")
                if self.minimum is not None and self.default_value < self.minimum:
                    raise ValueError("parameter default is below minimum")
                if self.maximum is not None and self.default_value > self.maximum:
                    raise ValueError("parameter default exceeds maximum")
        else:
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("string parameter cannot declare numeric bounds")
            if self.default_value is not None and not isinstance(
                self.default_value, str
            ):
                raise ValueError("string parameter has a non-string default")
            if self.allowed_values and self.default_value not in self.allowed_values:
                raise ValueError("parameter default is outside allowed values")
        if self.required and self.default_value is None:
            raise ValueError("required replay parameter needs a trusted default")
        return self


class RunbookStepSpecV21(DtaModelV21):
    step_id: RunbookStepIdV21
    parameter_names: tuple[IdentifierV21, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def require_unique_parameters(self) -> RunbookStepSpecV21:
        if len(self.parameter_names) != len(set(self.parameter_names)):
            raise ValueError("runbook step parameter names contain duplicates")
        return self


class RunbookFailurePolicyV21(DtaModelV21):
    terminal: Literal["ESCALATE_HUMAN"]
    preserve_completed_steps: Literal[True]
    additional_forward_write_allowed: Literal[False]
    step_receipt_required: Literal[True]


class RunbookSpecV21(DtaModelV21):
    schema_version: Literal["dta-v21.runbook-spec.v1"]
    runbook_id: RunbookIdV21
    version: Literal["v1"]
    supported_fault_domains: tuple[FaultDomainV21, ...] = Field(min_length=1)
    supported_mechanisms: tuple[FaultMechanismV21, ...] = Field(min_length=1)
    target_services: tuple[IdentifierV21, ...] = Field(min_length=1)
    required_evidence_sources: tuple[EvidenceSourceV21, ...] = Field(min_length=1)
    risk_level: RiskLevelV21
    parameters: tuple[RunbookParameterSpecV21, ...] = Field(max_length=8)
    preconditions: tuple[PreconditionV21, ...] = Field(min_length=1, max_length=8)
    forward_steps: tuple[RunbookStepSpecV21, ...] = Field(min_length=1, max_length=2)
    executor_id: IdentifierV21
    verifier_id: IdentifierV21
    maximum_forward_steps: StrictInt = Field(ge=1, le=2)
    failure_policy: RunbookFailurePolicyV21
    backend: RunbookBackendV21
    semantic_sha256: Sha256V21

    @model_validator(mode="after")
    def require_complete_bound_contract(self) -> RunbookSpecV21:
        for values, label in (
            (self.supported_fault_domains, "supported fault domains"),
            (self.supported_mechanisms, "supported mechanisms"),
            (self.target_services, "target services"),
            (self.required_evidence_sources, "required evidence sources"),
            (self.preconditions, "preconditions"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} contains duplicates")
        if self.target_services != tuple(sorted(self.target_services)):
            raise ValueError("target services are not canonical")
        parameter_names = tuple(item.name for item in self.parameters)
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("runbook parameter names contain duplicates")
        if len(self.forward_steps) != self.maximum_forward_steps:
            raise ValueError("forward steps differ from the declared step cap")
        for step in self.forward_steps:
            if not set(step.parameter_names).issubset(set(parameter_names)):
                raise ValueError("runbook step references an undeclared parameter")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"semantic_sha256"})
        )
        if self.semantic_sha256 != expected:
            raise ValueError("runbook semantic hash does not bind the contract")
        return self


class CandidateRunbookV21(DtaModelV21):
    schema_version: Literal["dta-v21.candidate-runbook.v1"]
    runbook_id: RunbookIdV21
    runbook_sha256: Sha256V21
    target_service: IdentifierV21
    risk_level: RiskLevelV21
    backend: RunbookBackendV21
    parameters: tuple[RunbookParameterSpecV21, ...] = Field(max_length=8)
    required_evidence_sources: tuple[EvidenceSourceV21, ...] = Field(min_length=1)


class CandidateSetV21(DtaModelV21):
    schema_version: Literal["dta-v21.candidate-set.v1"]
    run_id: RunIdV21
    diagnosis_sha256: Sha256V21
    resolved_evidence_sha256: Sha256V21
    registry_sha256: Sha256V21
    exact_target: IdentifierV21 | None
    write_candidates: tuple[CandidateRunbookV21, ...] = Field(max_length=3)
    allowed_nonwrite_dispositions: tuple[ActionDispositionV21, ...]
    candidate_set_sha256: Sha256V21

    @model_validator(mode="after")
    def require_bound_canonical_candidates(self) -> CandidateSetV21:
        if self.write_candidates != tuple(
            sorted(
                self.write_candidates,
                key=lambda item: (item.runbook_id.value, item.target_service),
            )
        ):
            raise ValueError("candidate set is not canonical")
        if len({item.runbook_id for item in self.write_candidates}) != len(
            self.write_candidates
        ):
            raise ValueError("candidate set contains duplicate runbooks")
        if any(
            item.target_service != self.exact_target for item in self.write_candidates
        ):
            raise ValueError("candidate differs from the exact target")
        if self.allowed_nonwrite_dispositions != (
            ActionDispositionV21.ESCALATE_HUMAN,
            ActionDispositionV21.NO_ACTION,
        ):
            raise ValueError("candidate set lacks fail-closed dispositions")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"candidate_set_sha256"})
        )
        if self.candidate_set_sha256 != expected:
            raise ValueError("candidate set digest does not bind the candidates")
        return self


class ActionParameterV21(DtaModelV21):
    name: IdentifierV21
    value: str | StrictInt

    @field_validator("name")
    @classmethod
    def reject_authority_names(cls, value: str) -> str:
        if value.casefold() in _UNSAFE_PARAMETER_NAMES:
            raise ValueError("forbidden parameter name")
        return value


class ActionProposalV21(DtaModelV21):
    schema_version: Literal["dta-v21.action-proposal.v1"]
    run_id: RunIdV21
    disposition: ActionDispositionV21
    candidate_set_sha256: Sha256V21
    diagnosis_sha256: Sha256V21
    resolved_evidence_sha256: Sha256V21
    registry_sha256: Sha256V21
    runbook_id: RunbookIdV21 | None = None
    runbook_sha256: Sha256V21 | None = None
    target_service: IdentifierV21 | None = None
    parameters: tuple[ActionParameterV21, ...] = Field(max_length=8)
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    rationale: str = Field(min_length=1, max_length=1000)
    proposal_sha256: Sha256V21

    @field_validator("rationale", mode="before")
    @classmethod
    def require_safe_rationale(cls, value: object) -> str:
        return _safe_text(value, field_name="rationale")

    @model_validator(mode="after")
    def require_bound_semantics(self) -> ActionProposalV21:
        validate_evidence_refs(
            self.supporting_evidence_refs,
            run_id=self.run_id,
            label="proposal evidence",
        )
        names = tuple(item.name for item in self.parameters)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("proposal parameters are not canonical and unique")
        if self.disposition is ActionDispositionV21.EXECUTE_RUNBOOK:
            if (
                self.runbook_id is None
                or self.runbook_sha256 is None
                or self.target_service is None
            ):
                raise ValueError("execute proposal requires a bound runbook and target")
            if not self.supporting_evidence_refs:
                raise ValueError("execute proposal requires supporting evidence")
        elif (
            any(
                value is not None
                for value in (self.runbook_id, self.runbook_sha256, self.target_service)
            )
            or self.parameters
        ):
            raise ValueError("nonexecute proposal carries runbook authority")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"proposal_sha256"})
        )
        if self.proposal_sha256 != expected:
            raise ValueError("proposal digest does not bind the proposal")
        return self


class ScenarioSpecV21(DtaModelV21):
    schema_version: Literal["dta-v21.scenario.v1"]
    scenario_id: IdentifierV21
    alert_summary: str = Field(min_length=1, max_length=1000)
    candidate_services: tuple[IdentifierV21, ...] = Field(min_length=1, max_length=8)
    allowed_read_tools: tuple[ReadToolNameV21, ...] = Field(min_length=1, max_length=5)
    maximum_read_tool_dispatches: Literal[4]
    maximum_repeated_identical_calls: Literal[0]

    @field_validator("alert_summary", mode="before")
    @classmethod
    def require_safe_alert(cls, value: object) -> str:
        return _safe_text(value, field_name="alert summary")

    @model_validator(mode="after")
    def require_unique_observer_scope(self) -> ScenarioSpecV21:
        if len(self.candidate_services) != len(set(self.candidate_services)):
            raise ValueError("candidate services contain duplicates")
        if len(self.allowed_read_tools) != len(set(self.allowed_read_tools)):
            raise ValueError("allowed read tools contain duplicates")
        return self


class ScenarioEvaluationContractV21(DtaModelV21):
    schema_version: Literal["dta-v21.scenario-evaluation-contract.v1"]
    scenario_id: IdentifierV21
    terminal: TerminalV21
    root_service: IdentifierV21 | None
    fault_domain: FaultDomainV21 | None
    fault_mechanism: FaultMechanismV21 | None
    required_evaluator_evidence: tuple[EvidenceSourceV21, ...] = Field(max_length=6)
    expected_runbook: RunbookIdV21 | None
    runbook_backend: RunbookBackendV21 | None
    live_required: StrictBool
    forward_writes: StrictInt = Field(ge=0, le=1)

    @model_validator(mode="after")
    def require_evaluator_contract_consistency(self) -> ScenarioEvaluationContractV21:
        claims = (self.root_service, self.fault_domain, self.fault_mechanism)
        if self.expected_runbook is None:
            if (
                any(item is not None for item in claims)
                or self.runbook_backend is not None
            ):
                raise ValueError("nonaction evaluator contract contains action truth")
            if self.forward_writes != 0:
                raise ValueError("nonaction evaluator contract authorizes a write")
        else:
            if any(item is None for item in claims) or self.runbook_backend is None:
                raise ValueError("action evaluator contract lacks typed truth")
        if self.runbook_backend is RunbookBackendV21.REPLAY_ONLY and self.live_required:
            raise ValueError("replay-only scenario cannot require a live Agent write")
        if self.forward_writes == 1 and not self.live_required:
            raise ValueError("forward write requires a live scenario")
        return self


class LegacyDevelopmentAnchorV21(DtaModelV21):
    schema_version: Literal["dta-v21.legacy-development-anchor.v1"]
    anchor_id: IdentifierV21
    source_path: str
    source_sha256: Sha256V21
    root_service: IdentifierV21 | None
    fault_domain: FaultDomainV21 | None
    fault_mechanism: FaultMechanismV21 | None
    expected_runbook: RunbookIdV21 | None

    @field_validator("source_path")
    @classmethod
    def require_frozen_v2_relative_path(cls, value: str) -> str:
        if (
            not value.startswith("config/dta-v2/")
            or value.startswith("/")
            or ".." in value.split("/")
        ):
            raise ValueError("legacy anchor path is outside frozen DTA v2 config")
        return value


class CrossedMatrixReportV21(DtaModelV21):
    schema_version: Literal["dta-v21.crossed-matrix-report.v1"]
    status: Literal["PASS"]
    checks: dict[str, StrictBool]
    report_sha256: Sha256V21

    @model_validator(mode="after")
    def require_all_checks_and_digest(self) -> CrossedMatrixReportV21:
        if not self.checks or not all(self.checks.values()):
            raise ValueError("crossed matrix report contains a failed check")
        if tuple(self.checks) != tuple(sorted(self.checks)):
            raise ValueError("crossed matrix checks are not canonical")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("crossed matrix digest does not bind the report")
        return self


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = (
    "ActionDispositionV21",
    "ActionParameterV21",
    "ActionProposalV21",
    "CandidateRunbookV21",
    "CandidateSetV21",
    "CrossedMatrixReportV21",
    "DtaDiagnosisV21",
    "DtaModelV21",
    "EvidenceSourceV21",
    "ExecutionBackendV21",
    "FaultDomainV21",
    "FaultMechanismV21",
    "LegacyDevelopmentAnchorV21",
    "PreconditionV21",
    "ResolvedDiagnosisEvidenceViewV21",
    "ResolvedEvidenceV21",
    "RiskLevelV21",
    "RunbookBackendV21",
    "RunbookFailurePolicyV21",
    "RunbookIdV21",
    "RunbookParameterSpecV21",
    "RunbookParameterTypeV21",
    "RunbookSpecV21",
    "RunbookStepIdV21",
    "RunbookStepSpecV21",
    "ScenarioEvaluationContractV21",
    "ScenarioSpecV21",
    "Sha256V21",
    "TerminalV21",
    "build_resolved_diagnosis_evidence_view_v21",
    "canonical_json_bytes",
    "evidence_source_from_ref",
    "semantic_sha256",
    "validate_evidence_refs",
)
