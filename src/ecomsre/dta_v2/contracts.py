"""Strict offline contracts for the Diagnosis-to-Action v2 successor."""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Literal

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

if TYPE_CHECKING:
    from ecomsre.dta_v2.registry import RunbookRegistry

Identifier = Annotated[
    str,
    Strict(),
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
RunId = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Sha256 = Annotated[str, Strict(), StringConstraints(pattern=r"^[0-9a-f]{64}$")]

_EVIDENCE_REF_RE = re.compile(
    r"^evidence://(?P<run_id>[0-9a-f]{32})/"
    r"(?P<source>metrics|logs|traces|runtime|resources|changes)/[0-9]{4}$"
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
_EXECUTABLE_TEXT_RE = re.compile(
    r"(?i)(?:&&|\|\||[;`$<>])|"
    r"\b(?:sudo|bash|zsh|docker|kubectl|curl|wget|terraform|ansible)\b|"
    r"\b(?:query_metrics|search_logs|query_trace_neighborhood|"
    r"inspect_service_runtime|inspect_resource_usage)\s*\("
)
_EVALUATOR_MARKERS = (
    "expected root",
    "expected root service",
    "expected fault mechanism",
    "expected mechanism",
    "expected runbook",
    "ground truth",
    "scenario truth",
    "scenario label",
    "answer key",
    "evaluator only",
    "evaluator root service",
    "evaluator path",
    "executor",
    "injected fault",
    "verifier",
)
_KNOWN_SCENARIO_CONTROL_MARKERS = (
    "defaultvariant",
    "emailmemoryleak",
    "paymentfailure",
)


class DtaModel(BaseModel):
    """Immutable, closed-world value object for the v2 successor."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )


def semantic_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trimmed(
    value: object,
    *,
    field_name: str,
    maximum: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must not be empty")
    if maximum is not None and len(trimmed) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return trimmed


def _reject_executable_text(value: str, *, field_name: str) -> str:
    if "evidence://" in value.casefold():
        raise ValueError(f"{field_name} must not contain an Evidence reference")
    if _EXECUTABLE_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain executable text")
    return value


def _reject_evaluator_markers(value: object) -> None:
    serialized = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", serialized)
    if any(marker in normalized for marker in _EVALUATOR_MARKERS):
        raise ValueError("agent-visible content contains an evaluator marker")
    compact = re.sub(r"\s+", "", serialized)
    if any(marker in compact for marker in _KNOWN_SCENARIO_CONTROL_MARKERS):
        raise ValueError("agent-visible content contains a scenario-control marker")


def _safe_text(value: object, *, field_name: str, maximum: int = 1000) -> str:
    text = _trimmed(value, field_name=field_name, maximum=maximum)
    _reject_evaluator_markers(text)
    return _reject_executable_text(text, field_name=field_name)


def _validate_evidence_refs(
    values: tuple[str, ...],
    *,
    run_id: str,
    label: str,
) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicate evidence references")
    for value in values:
        match = _EVIDENCE_REF_RE.fullmatch(value)
        if match is None:
            raise ValueError(f"{label} contains an invalid evidence reference")
        if match.group("run_id") != run_id:
            raise ValueError(f"{label} is outside the current run")
    return values


def _evidence_source(value: str) -> EvidenceSource:
    match = _EVIDENCE_REF_RE.fullmatch(value)
    if match is None:
        raise ValueError("invalid evidence reference")
    return _SOURCE_BY_REF[match.group("source")]


def _evidence_ref_order(value: str) -> tuple[int, str]:
    source = _evidence_source(value)
    return (_EVIDENCE_SOURCE_ORDER[source], value)


class Terminal(str, Enum):
    COMPLETED = "COMPLETED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    ABSTAIN = "ABSTAIN"


class EvidenceSource(str, Enum):
    METRICS = "METRICS"
    LOGS = "LOGS"
    TRACES = "TRACES"
    RUNTIME = "RUNTIME"
    RESOURCES = "RESOURCES"
    CHANGES = "CHANGES"


_SOURCE_BY_REF = {
    "metrics": EvidenceSource.METRICS,
    "logs": EvidenceSource.LOGS,
    "traces": EvidenceSource.TRACES,
    "runtime": EvidenceSource.RUNTIME,
    "resources": EvidenceSource.RESOURCES,
    "changes": EvidenceSource.CHANGES,
}
_EVIDENCE_SOURCE_ORDER = {
    source: index for index, source in enumerate(EvidenceSource)
}


class ResolvedEvidence(DtaModel):
    evidence_ref: str
    source: EvidenceSource
    artifact_sha256: Sha256

    @model_validator(mode="after")
    def require_reference_source_binding(self) -> ResolvedEvidence:
        match = _EVIDENCE_REF_RE.fullmatch(self.evidence_ref)
        if match is None:
            raise ValueError("resolved evidence contains an invalid reference")
        if self.source is not _SOURCE_BY_REF[match.group("source")]:
            raise ValueError("resolved evidence source differs from its reference")
        return self


class ResolvedDiagnosisEvidenceView(DtaModel):
    schema_version: Literal["dta-v2.resolved-diagnosis-evidence-view.v1"]
    run_id: RunId
    evidence: tuple[ResolvedEvidence, ...] = Field(min_length=1, max_length=64)
    resolved_evidence_sha256: Sha256

    @model_validator(mode="after")
    def require_resolution_semantics(self) -> ResolvedDiagnosisEvidenceView:
        refs = tuple(item.evidence_ref for item in self.evidence)
        if len(refs) != len(set(refs)):
            raise ValueError("resolved evidence contains duplicate references")
        if refs != tuple(sorted(refs, key=_evidence_ref_order)):
            raise ValueError("resolved evidence is not canonically ordered")
        _validate_evidence_refs(refs, run_id=self.run_id, label="resolved evidence")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"resolved_evidence_sha256"})
        )
        if self.resolved_evidence_sha256 != expected:
            raise ValueError("resolved evidence digest does not bind the view")
        return self


def build_resolved_diagnosis_evidence_view(
    *,
    run_id: str,
    evidence: tuple[ResolvedEvidence, ...],
) -> ResolvedDiagnosisEvidenceView:
    ordered = tuple(
        sorted(evidence, key=lambda item: _evidence_ref_order(item.evidence_ref))
    )
    typed_payload: dict[str, object] = {
        "schema_version": "dta-v2.resolved-diagnosis-evidence-view.v1",
        "run_id": run_id,
        "evidence": ordered,
    }
    digest_payload = {
        **typed_payload,
        "evidence": [item.model_dump(mode="json") for item in ordered],
    }
    return ResolvedDiagnosisEvidenceView.model_validate(
        {
            **typed_payload,
            "resolved_evidence_sha256": semantic_sha256(digest_payload),
        }
    )


class FaultDomain(str, Enum):
    APPLICATION = "APPLICATION"
    CONFIGURATION = "CONFIGURATION"
    SERVICE_RUNTIME = "SERVICE_RUNTIME"
    LOCAL_RESOURCE = "LOCAL_RESOURCE"
    NETWORK = "NETWORK"
    DEPENDENCY = "DEPENDENCY"
    QUEUE = "QUEUE"
    UNKNOWN = "UNKNOWN"


class FaultMechanism(str, Enum):
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    MEMORY_LEAK = "MEMORY_LEAK"
    UNKNOWN = "UNKNOWN"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RunbookId(str, Enum):
    ROLLBACK_CONFIGURATION = "ROLLBACK_CONFIGURATION"
    RESTART_SERVICE = "RESTART_SERVICE"
    MITIGATE_MEMORY_LEAK = "MITIGATE_MEMORY_LEAK"


class Precondition(str, Enum):
    LOCAL_DOCKER_ONLY = "LOCAL_DOCKER_ONLY"
    OWNED_SERVICE = "OWNED_SERVICE"
    CONFIGURATION_DRIFT_VISIBLE = "CONFIGURATION_DRIFT_VISIBLE"
    SERVICE_NOT_HEALTHY = "SERVICE_NOT_HEALTHY"
    LEAK_FLAG_ACTIVE = "LEAK_FLAG_ACTIVE"
    BASELINE_HASH_BOUND = "BASELINE_HASH_BOUND"


class RunbookStepId(str, Enum):
    RESTORE_BASELINE_CONFIGURATION = "RESTORE_BASELINE_CONFIGURATION"
    RESTART_OWNED_SERVICE = "RESTART_OWNED_SERVICE"
    DISABLE_LEAK_FLAG = "DISABLE_LEAK_FLAG"


class ActionDisposition(str, Enum):
    EXECUTE_RUNBOOK = "EXECUTE_RUNBOOK"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    NO_ACTION = "NO_ACTION"


class CandidateRunbook(DtaModel):
    runbook_id: RunbookId
    runbook_sha256: Sha256
    target_service: Identifier
    risk_level: RiskLevel
    parameter_names: tuple[Identifier, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def require_unique_parameter_names(self) -> CandidateRunbook:
        if len(self.parameter_names) != len(set(self.parameter_names)):
            raise ValueError("candidate parameter names contain duplicates")
        return self


class CandidateSet(DtaModel):
    schema_version: Literal["dta-v2.candidate-set.v2"]
    run_id: RunId
    diagnosis_sha256: Sha256
    resolved_evidence_sha256: Sha256
    registry_sha256: Sha256
    write_candidates: tuple[CandidateRunbook, ...] = Field(max_length=3)
    allowed_nonwrite_dispositions: tuple[ActionDisposition, ...]
    candidate_set_sha256: Sha256

    @model_validator(mode="after")
    def require_candidate_set_semantics(self) -> CandidateSet:
        ids = tuple(candidate.runbook_id for candidate in self.write_candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("candidate set contains duplicate runbooks")
        if self.write_candidates != tuple(
            sorted(
                self.write_candidates,
                key=lambda item: (item.runbook_id.value, item.target_service),
            )
        ):
            raise ValueError("candidate set is not canonically ordered")
        if self.allowed_nonwrite_dispositions != (
            ActionDisposition.ESCALATE_HUMAN,
            ActionDisposition.NO_ACTION,
        ):
            raise ValueError("candidate set must preserve fail-closed dispositions")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"candidate_set_sha256"})
        )
        if self.candidate_set_sha256 != expected:
            raise ValueError("candidate set digest does not bind the candidates")
        return self


def build_candidate_set(
    *,
    run_id: str,
    diagnosis_sha256: str,
    resolved_evidence_sha256: str,
    registry_sha256: str,
    write_candidates: tuple[CandidateRunbook, ...],
) -> CandidateSet:
    nonwrite = (
        ActionDisposition.ESCALATE_HUMAN,
        ActionDisposition.NO_ACTION,
    )
    typed_payload: dict[str, object] = {
        "schema_version": "dta-v2.candidate-set.v2",
        "run_id": run_id,
        "diagnosis_sha256": diagnosis_sha256,
        "resolved_evidence_sha256": resolved_evidence_sha256,
        "registry_sha256": registry_sha256,
        "write_candidates": write_candidates,
        "allowed_nonwrite_dispositions": nonwrite,
    }
    digest_payload = {
        **typed_payload,
        "write_candidates": [
            candidate.model_dump(mode="json") for candidate in write_candidates
        ],
        "allowed_nonwrite_dispositions": [item.value for item in nonwrite],
    }
    return CandidateSet.model_validate(
        {
            **typed_payload,
            "candidate_set_sha256": semantic_sha256(digest_payload),
        }
    )


class DtaDiagnosis(DtaModel):
    schema_version: Literal["dta-v2.diagnosis.v1"]
    run_id: RunId
    terminal: Terminal
    root_service: Identifier | None = None
    root_entity_ref: Identifier | None = None
    fault_domain: FaultDomain | None = None
    mechanism: FaultMechanism | None = None
    confidence: StrictFloat | None = Field(default=None, ge=0, le=1)
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    evidence_source_types: tuple[EvidenceSource, ...] = Field(max_length=6)
    uncertainties: tuple[str, ...] = Field(max_length=16)
    summary: str = Field(min_length=1, max_length=1000)

    @field_validator("uncertainties")
    @classmethod
    def require_safe_uncertainties(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            _safe_text(value, field_name="uncertainty") for value in values
        )

    @field_validator("summary", mode="before")
    @classmethod
    def require_safe_summary(cls, value: object) -> str:
        return _safe_text(value, field_name="summary")

    @model_validator(mode="after")
    def require_terminal_semantics(self) -> DtaDiagnosis:
        support = _validate_evidence_refs(
            self.supporting_evidence_refs,
            run_id=self.run_id,
            label="supporting evidence",
        )
        contradict = _validate_evidence_refs(
            self.contradicting_evidence_refs,
            run_id=self.run_id,
            label="contradicting evidence",
        )
        if set(support).intersection(contradict):
            raise ValueError("evidence cannot both support and contradict a diagnosis")
        refs = support + contradict
        actual_sources = {_evidence_source(ref) for ref in refs}
        if set(self.evidence_source_types) != actual_sources:
            raise ValueError("evidence source accounting does not match references")
        if len(self.evidence_source_types) != len(set(self.evidence_source_types)):
            raise ValueError("evidence source accounting contains duplicates")
        if support != tuple(sorted(support, key=_evidence_ref_order)):
            raise ValueError("supporting evidence is not canonically ordered")
        if contradict != tuple(sorted(contradict, key=_evidence_ref_order)):
            raise ValueError("contradicting evidence is not canonically ordered")
        if self.evidence_source_types != tuple(
            sorted(
                self.evidence_source_types,
                key=lambda item: _EVIDENCE_SOURCE_ORDER[item],
            )
        ):
            raise ValueError("evidence source accounting is not canonically ordered")

        claims = (
            self.root_service,
            self.root_entity_ref,
            self.fault_domain,
            self.mechanism,
        )
        if self.terminal is Terminal.COMPLETED:
            if any(value is None for value in claims):
                raise ValueError("completed diagnosis requires root, domain, and mechanism")
            if self.confidence is None or not support:
                raise ValueError("completed diagnosis requires confidence and evidence")
            if self.root_entity_ref != f"service:{self.root_service}":
                raise ValueError("root entity must bind the diagnosed service")
        else:
            if any(value is not None for value in claims):
                raise ValueError("noncompleted diagnosis cannot claim a root or mechanism")
            if self.terminal is Terminal.NEED_MORE_EVIDENCE and not self.uncertainties:
                raise ValueError("need-more diagnosis requires one uncertainty")
        return self


class RunbookParameterType(str, Enum):
    STRING = "STRING"
    INTEGER = "INTEGER"


class RunbookParameterSpec(DtaModel):
    name: Identifier
    parameter_type: RunbookParameterType
    required: StrictBool = True
    minimum: StrictInt | None = None
    maximum: StrictInt | None = None
    allowed_values: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("name")
    @classmethod
    def reject_authority_names(cls, value: str) -> str:
        if value.casefold() in _UNSAFE_PARAMETER_NAMES:
            raise ValueError("forbidden parameter name")
        return value

    @model_validator(mode="after")
    def require_type_bounds(self) -> RunbookParameterSpec:
        if self.parameter_type is RunbookParameterType.INTEGER:
            if self.allowed_values:
                raise ValueError("integer parameter cannot declare string values")
            if self.minimum is not None and self.maximum is not None:
                if self.minimum > self.maximum:
                    raise ValueError("parameter minimum exceeds maximum")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("string parameter cannot declare numeric bounds")
        return self


class RunbookStepSpec(DtaModel):
    step_id: RunbookStepId
    parameter_names: tuple[Identifier, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def require_unique_parameter_names(self) -> RunbookStepSpec:
        if len(self.parameter_names) != len(set(self.parameter_names)):
            raise ValueError("runbook step parameter names contain duplicates")
        return self


class RunbookPartialFailurePolicy(DtaModel):
    terminal: Literal["PARTIALLY_APPLIED"]
    disposition: Literal["ESCALATE_HUMAN"]
    preserve_completed_steps: Literal[True]
    completed_step_compensation_allowed: Literal[False]
    additional_forward_write_allowed: Literal[False]
    step_receipt_required: Literal[True]


class RunbookSpec(DtaModel):
    schema_version: Literal["dta-v2.runbook-spec.v1"]
    runbook_id: RunbookId
    version: Literal["v1"]
    supported_fault_domains: tuple[FaultDomain, ...] = Field(min_length=1)
    supported_mechanisms: tuple[FaultMechanism, ...] = Field(min_length=1)
    target_services: tuple[Identifier, ...] = Field(min_length=1)
    risk_level: RiskLevel
    required_evidence_sources: tuple[EvidenceSource, ...] = Field(min_length=1)
    parameters: tuple[RunbookParameterSpec, ...] = Field(max_length=8)
    preconditions: tuple[Precondition, ...] = Field(min_length=1, max_length=8)
    forward_steps: tuple[RunbookStepSpec, ...] = Field(min_length=1, max_length=2)
    executor_id: Identifier
    verifier_id: Identifier
    maximum_forward_steps: StrictInt = Field(ge=1, le=2)
    failure_policy: Literal["ESCALATE_HUMAN"]
    partial_failure_policy: RunbookPartialFailurePolicy | None = None

    @model_validator(mode="after")
    def require_unique_contract_items(self) -> RunbookSpec:
        for values, label in (
            (self.supported_fault_domains, "supported fault domains"),
            (self.supported_mechanisms, "supported mechanisms"),
            (self.target_services, "target services"),
            (self.required_evidence_sources, "required evidence sources"),
            (self.preconditions, "preconditions"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} contains duplicates")
        names = tuple(parameter.name for parameter in self.parameters)
        if len(names) != len(set(names)):
            raise ValueError("runbook parameters contain duplicates")
        if len(self.forward_steps) != self.maximum_forward_steps:
            raise ValueError("forward steps differ from the declared step cap")
        if self.maximum_forward_steps > 1 and self.partial_failure_policy is None:
            raise ValueError("multi-step runbook requires a partial failure policy")
        if self.maximum_forward_steps == 1 and self.partial_failure_policy is not None:
            raise ValueError("single-step runbook must not define a partial failure policy")
        declared = set(names)
        for step in self.forward_steps:
            if not set(step.parameter_names).issubset(declared):
                raise ValueError("runbook step references an undeclared parameter")
        return self


class ActionParameter(DtaModel):
    name: Identifier
    value: str | StrictInt | StrictFloat | StrictBool

    @field_validator("name")
    @classmethod
    def reject_authority_names(cls, value: str) -> str:
        if value.casefold() in _UNSAFE_PARAMETER_NAMES:
            raise ValueError("forbidden parameter name")
        return value

    @field_validator("value")
    @classmethod
    def reject_executable_values(
        cls,
        value: str | int | float | bool,
    ) -> str | int | float | bool:
        if not isinstance(value, str):
            return value
        if value.startswith(("/", "./", "../")) or "://" in value:
            raise ValueError("parameter value must not contain a path or URL")
        return _reject_executable_text(value, field_name="parameter value")


class ActionProposal(DtaModel):
    schema_version: Literal["dta-v2.action-proposal.v2"]
    run_id: RunId
    disposition: ActionDisposition
    candidate_set_sha256: Sha256
    diagnosis_sha256: Sha256
    resolved_evidence_sha256: Sha256
    registry_sha256: Sha256
    runbook_id: RunbookId | None = None
    runbook_sha256: Sha256 | None = None
    target_service: Identifier | None = None
    parameters: tuple[ActionParameter, ...] = Field(max_length=8)
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    rationale: str = Field(min_length=1, max_length=1000)
    proposal_sha256: Sha256

    @field_validator("rationale", mode="before")
    @classmethod
    def require_safe_rationale(cls, value: object) -> str:
        return _safe_text(value, field_name="rationale")

    @model_validator(mode="after")
    def require_proposal_semantics(self) -> ActionProposal:
        evidence_refs = _validate_evidence_refs(
            self.supporting_evidence_refs,
            run_id=self.run_id,
            label="proposal evidence",
        )
        names = tuple(parameter.name for parameter in self.parameters)
        if len(names) != len(set(names)):
            raise ValueError("proposal parameters contain duplicates")
        if names != tuple(sorted(names)):
            raise ValueError("proposal parameters are not canonically ordered")
        if evidence_refs != tuple(sorted(evidence_refs, key=_evidence_ref_order)):
            raise ValueError("proposal evidence is not canonically ordered")
        if self.disposition is ActionDisposition.EXECUTE_RUNBOOK:
            if (
                self.runbook_id is None
                or self.runbook_sha256 is None
                or self.target_service is None
            ):
                raise ValueError("execute proposal requires a runbook and target")
            if not self.supporting_evidence_refs:
                raise ValueError("execute proposal requires supporting evidence")
        elif (
            self.runbook_id is not None
            or self.runbook_sha256 is not None
            or self.target_service is not None
            or self.parameters
        ):
            raise ValueError("nonexecute proposal must not carry runbook authority")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"proposal_sha256"})
        )
        if self.proposal_sha256 != expected:
            raise ValueError("proposal digest does not bind the proposal")
        return self


def build_action_proposal(
    *,
    candidate_set: CandidateSet,
    diagnosis: DtaDiagnosis,
    registry: RunbookRegistry,
    diagnosis_evidence: ResolvedDiagnosisEvidenceView,
    disposition: ActionDisposition,
    runbook_id: RunbookId | None,
    target_service: str | None,
    parameters: tuple[ActionParameter, ...],
    supporting_evidence_refs: tuple[str, ...],
    rationale: str,
) -> ActionProposal:
    ordered_parameters = tuple(sorted(parameters, key=lambda item: item.name))
    ordered_evidence_refs = tuple(
        sorted(supporting_evidence_refs, key=_evidence_ref_order)
    )
    typed_payload: dict[str, object] = {
        "schema_version": "dta-v2.action-proposal.v2",
        "run_id": diagnosis.run_id,
        "disposition": disposition,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "diagnosis_sha256": candidate_set.diagnosis_sha256,
        "resolved_evidence_sha256": candidate_set.resolved_evidence_sha256,
        "registry_sha256": candidate_set.registry_sha256,
        "runbook_id": runbook_id,
        "runbook_sha256": (
            None
            if runbook_id is None
            else semantic_sha256(
                registry.require(runbook_id).model_dump(mode="json")
            )
        ),
        "target_service": target_service,
        "parameters": ordered_parameters,
        "supporting_evidence_refs": ordered_evidence_refs,
        "rationale": rationale,
    }
    digest_payload = {
        **typed_payload,
        "disposition": disposition.value,
        "runbook_id": None if runbook_id is None else runbook_id.value,
        "parameters": [
            item.model_dump(mode="json") for item in ordered_parameters
        ],
        "supporting_evidence_refs": list(ordered_evidence_refs),
    }
    proposal = ActionProposal.model_validate(
        {
            **typed_payload,
            "proposal_sha256": semantic_sha256(digest_payload),
        }
    )
    validate_action_proposal_binding(
        proposal=proposal,
        candidate_set=candidate_set,
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=diagnosis_evidence,
    )
    return proposal


def validate_action_proposal_binding(
    *,
    proposal: ActionProposal,
    candidate_set: CandidateSet,
    diagnosis: DtaDiagnosis,
    registry: RunbookRegistry,
    diagnosis_evidence: ResolvedDiagnosisEvidenceView,
) -> None:
    """Bind a structural proposal to trusted candidate and evidence artifacts."""

    from ecomsre.dta_v2.candidate_filter import filter_runbook_candidates
    from ecomsre.dta_v2.registry import RunbookRegistry

    proposal = ActionProposal.model_validate(proposal.model_dump(mode="python"))
    candidate_set = CandidateSet.model_validate(
        candidate_set.model_dump(mode="python")
    )
    diagnosis = DtaDiagnosis.model_validate(diagnosis.model_dump(mode="python"))
    diagnosis_evidence = ResolvedDiagnosisEvidenceView.model_validate(
        diagnosis_evidence.model_dump(mode="python")
    )
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    expected_candidate_set = filter_runbook_candidates(
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=diagnosis_evidence,
    )
    if candidate_set != expected_candidate_set:
        raise ValueError("candidate set is not derived from the frozen registry")

    diagnosis_sha256 = semantic_sha256(diagnosis.model_dump(mode="json"))
    if not (
        proposal.run_id
        == candidate_set.run_id
        == diagnosis.run_id
        == diagnosis_evidence.run_id
    ):
        raise ValueError("proposal artifacts belong to different runs")
    if (
        proposal.diagnosis_sha256 != diagnosis_sha256
        or candidate_set.diagnosis_sha256 != diagnosis_sha256
    ):
        raise ValueError("proposal diagnosis digest is not authoritative")
    if proposal.candidate_set_sha256 != candidate_set.candidate_set_sha256:
        raise ValueError("proposal candidate digest is not authoritative")
    if (
        proposal.registry_sha256 != registry.registry_sha256
        or candidate_set.registry_sha256 != registry.registry_sha256
    ):
        raise ValueError("proposal registry digest is not authoritative")
    if (
        proposal.resolved_evidence_sha256
        != diagnosis_evidence.resolved_evidence_sha256
        or candidate_set.resolved_evidence_sha256
        != diagnosis_evidence.resolved_evidence_sha256
    ):
        raise ValueError("candidate set is outside the resolved evidence snapshot")

    resolved_by_ref = {
        item.evidence_ref: item for item in diagnosis_evidence.evidence
    }
    resolved_refs = set(resolved_by_ref)
    proposal_refs = set(proposal.supporting_evidence_refs)
    if not proposal_refs.issubset(resolved_refs):
        raise ValueError("proposal cites unresolved evidence")
    if not proposal_refs.issubset(set(diagnosis.supporting_evidence_refs)):
        raise ValueError("proposal evidence is not supporting diagnosis evidence")

    if proposal.disposition is not ActionDisposition.EXECUTE_RUNBOOK:
        if proposal.disposition not in candidate_set.allowed_nonwrite_dispositions:
            raise ValueError("proposal disposition is outside the candidate set")
        return

    if proposal.runbook_id is None:
        raise ValueError("execute proposal does not identify a runbook")
    runbook = registry.require(proposal.runbook_id)
    runbook_sha256 = semantic_sha256(runbook.model_dump(mode="json"))
    if proposal.runbook_sha256 != runbook_sha256:
        raise ValueError("proposal runbook digest is not authoritative")
    matching = tuple(
        candidate
        for candidate in candidate_set.write_candidates
        if candidate.runbook_id is proposal.runbook_id
        and candidate.target_service == proposal.target_service
    )
    if len(matching) != 1:
        raise ValueError("proposal runbook and target are outside the candidate set")
    candidate = matching[0]
    if candidate.runbook_sha256 != runbook_sha256:
        raise ValueError("candidate runbook digest is not authoritative")
    if candidate.risk_level is not runbook.risk_level:
        raise ValueError("candidate risk differs from the trusted runbook")
    if (
        diagnosis.root_service != proposal.target_service
        or proposal.target_service not in runbook.target_services
        or diagnosis.fault_domain not in runbook.supported_fault_domains
        or diagnosis.mechanism not in runbook.supported_mechanisms
    ):
        raise ValueError("proposal target is incompatible with the diagnosis")
    expected_names = tuple(parameter.name for parameter in runbook.parameters)
    if candidate.parameter_names != expected_names:
        raise ValueError("candidate parameters differ from the trusted runbook")
    proposal_sources = {
        resolved_by_ref[reference].source
        for reference in proposal.supporting_evidence_refs
    }
    if not set(runbook.required_evidence_sources).issubset(proposal_sources):
        raise ValueError("proposal does not cover required evidence sources")
    _validate_action_parameters(proposal.parameters, runbook.parameters)


def _validate_action_parameters(
    values: tuple[ActionParameter, ...],
    specifications: tuple[RunbookParameterSpec, ...],
) -> None:
    supplied = {item.name: item.value for item in values}
    expected = {item.name: item for item in specifications}
    if set(supplied) - set(expected):
        raise ValueError("proposal contains an unknown runbook parameter")
    if any(item.required and item.name not in supplied for item in specifications):
        raise ValueError("proposal omits a required runbook parameter")
    for name, value in supplied.items():
        specification = expected[name]
        if specification.parameter_type is RunbookParameterType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("proposal parameter has the wrong type")
            if specification.minimum is not None and value < specification.minimum:
                raise ValueError("proposal parameter is below its minimum")
            if specification.maximum is not None and value > specification.maximum:
                raise ValueError("proposal parameter exceeds its maximum")
        else:
            if not isinstance(value, str):
                raise ValueError("proposal parameter has the wrong type")
            if specification.allowed_values and value not in specification.allowed_values:
                raise ValueError("proposal parameter is outside its allowed values")


ReadToolName = Literal[
    "query_metrics",
    "search_logs",
    "query_trace_neighborhood",
    "inspect_service_runtime",
    "inspect_resource_usage",
]


class ScenarioSpec(DtaModel):
    schema_version: Literal["dta-v2.scenario.v1"]
    scenario_id: Identifier
    alert_summary: str = Field(min_length=1, max_length=1000)
    candidate_services: tuple[Identifier, ...] = Field(min_length=1, max_length=8)
    allowed_read_tools: tuple[ReadToolName, ...] = Field(min_length=1, max_length=5)
    maximum_read_tool_dispatches: Literal[4]
    maximum_repeated_identical_calls: Literal[0]

    @field_validator("alert_summary", mode="before")
    @classmethod
    def require_safe_alert(cls, value: object) -> str:
        return _safe_text(value, field_name="alert_summary")

    @model_validator(mode="after")
    def require_unique_scenario_scope(self) -> ScenarioSpec:
        _reject_evaluator_markers(self.model_dump(mode="json"))
        if len(self.candidate_services) != len(set(self.candidate_services)):
            raise ValueError("candidate services contain duplicates")
        if len(self.allowed_read_tools) != len(set(self.allowed_read_tools)):
            raise ValueError("allowed read tools contain duplicates")
        return self
