"""Closed-world Phase 4 domain contracts without Phase 1 schema drift."""

from __future__ import annotations

from enum import Enum
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from ecomsre.phase1.contracts import (
    MAX_CAUSAL_CHAIN_ITEMS,
    MAX_EVIDENCE_REFS,
    MAX_MISSING_EVIDENCE_ITEMS,
    MAX_SERVICE_LENGTH,
    MAX_SLI_LENGTH,
    MAX_TEXT_ENTRY_LENGTH,
    Phase1Model,
    RCADecision,
    RecommendedNextAction,
    ToolCallRecord,
    _reject_executable_text,
    _trimmed,
    _validate_evidence_ref,
    _validate_text_tuple,
)
from ecomsre.phase2.comparison_adapter import (
    ModelCallAuditRecord,
    ToolCallAuditRecord,
)
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    BudgetAuditEvent,
    BudgetSnapshot,
    SpecialistFinding,
)


_EVALUATOR_MARKER = re.compile(
    r"\b(?:case[ _-]*id|ground[ _-]+truth|answer[ _-]+key|"
    r"expected[ _-]+(?:answer|label|decision|root|mechanism)|"
    r"evaluator[ _-]+(?:truth|label|only))\b",
    flags=re.IGNORECASE,
)


def _reject_domain_text(value: str, *, field_name: str) -> str:
    safe = _reject_executable_text(value, field_name=field_name)
    if _EVALUATOR_MARKER.search(safe):
        raise ValueError(f"{field_name} contains an evaluator marker")
    return safe


class DomainFaultMechanism(str, Enum):
    """The exact replay-only Phase 4 domain mechanism allowlist."""

    FEATURE_FRESHNESS_LAG = "feature_freshness_lag"
    MODEL_FEATURE_SCHEMA_MISMATCH = "model_feature_schema_mismatch"
    RANKING_CONFIGURATION_FAILURE = "ranking_configuration_failure"


class DomainVariant(str, Enum):
    FIXED_SPECIALIST_WORKFLOW = "FIXED_SPECIALIST_WORKFLOW"
    DYNAMIC_MULTI_AGENT = "DYNAMIC_MULTI_AGENT"


class DomainRemediationOutcome(str, Enum):
    NO_ACTION = "NO_ACTION"
    NO_SUPPORTED_REMEDIATION = "NO_SUPPORTED_REMEDIATION"


class DomainRemediationDisposition(Phase1Model):
    schema_version: Literal["phase4.remediation-disposition.v1"]
    outcome: DomainRemediationOutcome
    remediation_action: None = None
    live_mutation: Literal[False]
    remediation_backend: Literal["NONE"]


class DomainModelCallAudit(Phase1Model):
    schema_version: Literal["phase4.domain-model-call-audit.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    variant: DomainVariant
    provider_identity: str = Field(min_length=1, max_length=128)
    model_snapshot: str = Field(min_length=1, max_length=128)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    local_input_tokens: StrictInt = Field(gt=0)
    provider_prompt_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(gt=0)
    no_retry: Literal[True]
    scripted_fallback: Literal[False]


class DomainRCAResult(Phase1Model):
    """Immutable Phase 4 RCA result over current-run evidence references."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )

    schema_version: Literal["phase4.domain-rca-result.v1"]
    decision: RCADecision
    root_service: str | None = Field(default=None, max_length=MAX_SERVICE_LENGTH)
    fault_mechanism: DomainFaultMechanism | None = None
    causal_chain: tuple[str, ...] = Field(max_length=MAX_CAUSAL_CHAIN_ITEMS)
    affected_sli: str | None = Field(default=None, max_length=MAX_SLI_LENGTH)
    supporting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    contradicting_evidence: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    missing_evidence: tuple[str, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    confidence: StrictFloat = Field(ge=0, le=1)
    decision_rationale: str = Field(min_length=1, max_length=1000)
    recommended_next_action: RecommendedNextAction

    @field_validator(
        "causal_chain",
        "supporting_evidence",
        "contradicting_evidence",
        "missing_evidence",
        mode="before",
    )
    @classmethod
    def parse_json_arrays(cls, value: object) -> tuple[object, ...]:
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        raise ValueError("Domain RCA collection fields must be JSON arrays")

    @field_validator("decision", mode="before")
    @classmethod
    def parse_exact_decision(cls, value: object) -> RCADecision:
        if isinstance(value, RCADecision):
            return value
        if type(value) is str:
            return RCADecision(value)
        raise ValueError("decision must be an exact RCA decision")

    @field_validator("fault_mechanism", mode="before")
    @classmethod
    def parse_exact_mechanism(
        cls,
        value: object | None,
    ) -> DomainFaultMechanism | None:
        if value is None or isinstance(value, DomainFaultMechanism):
            return value
        if type(value) is str:
            return DomainFaultMechanism(value)
        raise ValueError("fault_mechanism must use the domain allowlist")

    @field_validator("recommended_next_action", mode="before")
    @classmethod
    def parse_read_only_action(cls, value: object) -> RecommendedNextAction:
        if isinstance(value, RecommendedNextAction):
            return value
        if type(value) is str:
            return RecommendedNextAction(value)
        raise ValueError("recommended_next_action must be a typed read-only action")

    @field_validator("root_service", "affected_sli", mode="before")
    @classmethod
    def trim_optional_claim(cls, value: object | None) -> str | None:
        if value is None:
            return None
        bounded = _trimmed(value, field_name="domain claim")
        return _reject_domain_text(bounded, field_name="domain claim")

    @field_validator("causal_chain", "missing_evidence")
    @classmethod
    def require_bounded_safe_entries(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        bounded = _validate_text_tuple(
            values,
            field_name="domain explanation",
            maximum=MAX_TEXT_ENTRY_LENGTH,
        )
        return tuple(
            _reject_domain_text(value, field_name="domain explanation")
            for value in bounded
        )

    @field_validator("supporting_evidence", "contradicting_evidence")
    @classmethod
    def require_exact_unique_references(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        validated = tuple(_validate_evidence_ref(value) for value in values)
        if len(validated) != len(set(validated)):
            raise ValueError("domain RCA contains duplicate evidence references")
        return validated

    @field_validator("decision_rationale", mode="before")
    @classmethod
    def require_safe_rationale(cls, value: object) -> str:
        bounded = _trimmed(
            value,
            field_name="decision_rationale",
            maximum=1000,
        )
        return _reject_domain_text(
            bounded,
            field_name="decision_rationale",
        )

    @model_validator(mode="after")
    def require_decision_semantics(self) -> DomainRCAResult:
        overlapping = set(self.supporting_evidence).intersection(
            self.contradicting_evidence
        )
        if overlapping:
            raise ValueError("evidence cannot both support and contradict the RCA")

        if self.decision is RCADecision.RCA_CONFIRMED:
            if self.root_service not in {"feature", "ranking"}:
                raise ValueError("confirmed Domain RCA requires feature or ranking")
            if self.fault_mechanism is None:
                raise ValueError("confirmed Domain RCA requires one mechanism")
            if not self.causal_chain or self.affected_sli is None:
                raise ValueError("confirmed Domain RCA requires a causal SLI chain")
            if len(self.supporting_evidence) < 2:
                raise ValueError("confirmed Domain RCA requires two evidence refs")
            sources = {
                reference.split("/")[3] for reference in self.supporting_evidence
            }
            if len(sources) < 2:
                raise ValueError("confirmed Domain RCA requires two evidence sources")
            if self.missing_evidence:
                raise ValueError("confirmed Domain RCA cannot retain evidence gaps")
        elif self.decision is RCADecision.NEED_MORE_EVIDENCE:
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("need-more cannot claim a root service or mechanism")
            if self.causal_chain:
                raise ValueError("need-more cannot claim a causal chain")
            if not self.missing_evidence:
                raise ValueError("need-more requires one bounded evidence gap")
        else:
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("abstain cannot claim a root service or mechanism")
            if self.causal_chain or self.supporting_evidence or self.missing_evidence:
                raise ValueError("abstain cannot claim causal support or a gap")
        return self


class DomainGroundTruth(BaseModel):
    """Evaluator-only expected result for one Phase 4 case."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )

    schema_version: Literal["phase4.domain-ground-truth.v1"]
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    expected_decision: RCADecision
    expected_root_service: str | None = Field(
        default=None,
        max_length=MAX_SERVICE_LENGTH,
    )
    expected_fault_mechanism: DomainFaultMechanism | None = None
    decoy_evidence: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("expected_decision", mode="before")
    @classmethod
    def parse_expected_decision(cls, value: object) -> RCADecision:
        if isinstance(value, RCADecision):
            return value
        if type(value) is str:
            return RCADecision(value)
        raise ValueError("expected_decision must be exact")

    @field_validator("expected_fault_mechanism", mode="before")
    @classmethod
    def parse_expected_mechanism(
        cls,
        value: object | None,
    ) -> DomainFaultMechanism | None:
        if value is None or isinstance(value, DomainFaultMechanism):
            return value
        if type(value) is str:
            return DomainFaultMechanism(value)
        raise ValueError("expected_fault_mechanism must be exact")

    @field_validator("decoy_evidence")
    @classmethod
    def require_bounded_decoy_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_text_tuple(
            values,
            field_name="decoy evidence identifier",
            maximum=256,
        )

    @field_validator("decoy_evidence", mode="before")
    @classmethod
    def parse_json_decoy_ids(cls, value: object) -> tuple[object, ...]:
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        raise ValueError("decoy_evidence must be a JSON array")

    @model_validator(mode="after")
    def require_expected_shape(self) -> DomainGroundTruth:
        if self.expected_decision is RCADecision.RCA_CONFIRMED:
            if self.expected_root_service not in {"feature", "ranking"}:
                raise ValueError("confirmed truth requires feature or ranking")
            if self.expected_fault_mechanism is None:
                raise ValueError("confirmed truth requires a domain mechanism")
        elif (
            self.expected_root_service is not None
            or self.expected_fault_mechanism is not None
        ):
            raise ValueError("unconfirmed truth cannot name a root or mechanism")
        return self


class DomainWorkflowTrace(Phase1Model):
    """One deterministic Fixed or Dynamic Phase 4 replay run."""

    schema_version: Literal["phase4.domain-workflow-trace.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    variant: DomainVariant
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    status: Literal["COMPLETED", "FAILED"]
    final_rca: DomainRCAResult | None = None
    admitted_graph: AdmittedInvestigationGraph | None = None
    findings: tuple[SpecialistFinding, ...] = ()
    tool_call_records: tuple[ToolCallRecord, ...] = ()
    model_call_audits: tuple[ModelCallAuditRecord, ...] = ()
    domain_model_call_audits: tuple[DomainModelCallAudit, ...] = ()
    tool_call_audits: tuple[ToolCallAuditRecord, ...] = ()
    budget_audit_events: tuple[BudgetAuditEvent, ...] = ()
    final_budget_snapshot: BudgetSnapshot
    remediation_disposition: DomainRemediationDisposition
    model_mode: Literal["SCRIPTED_REPLAY", "REAL_PROVIDER"]
    live_environment: Literal[False]
    phase5_entered: Literal[False]
    terminal_reason: str | None = Field(default=None, max_length=256)

    @field_validator("variant", mode="before")
    @classmethod
    def parse_domain_variant(cls, value: object) -> DomainVariant:
        if isinstance(value, DomainVariant):
            return value
        if type(value) is str:
            return DomainVariant(value)
        raise ValueError("variant must be an exact Domain variant")

    @model_validator(mode="after")
    def require_trace_consistency(self) -> DomainWorkflowTrace:
        if self.status == "COMPLETED":
            if self.final_rca is None or self.admitted_graph is None:
                raise ValueError("completed Domain workflow requires graph and RCA")
            if self.terminal_reason is not None:
                raise ValueError("completed Domain workflow cannot have a failure")
            if (
                self.final_budget_snapshot.active_capacity_slot_ids
                or self.final_budget_snapshot.active_specialist_authorization_ids
                or self.final_budget_snapshot.active_lease_ids
            ):
                raise ValueError("completed Domain workflow retains active budget state")
        elif self.final_rca is not None or self.terminal_reason is None:
            raise ValueError("failed Domain workflow requires only a terminal reason")
        if (
            self.final_budget_snapshot.run_id != self.run_id
            or self.final_budget_snapshot.case_id != self.case_id
            or self.final_budget_snapshot.variant.value != self.variant.value
        ):
            raise ValueError("Domain trace differs from its Phase 2 budget scope")
        if any(record.run_id != self.run_id for record in self.tool_call_records):
            raise ValueError("Domain trace contains a cross-run tool record")
        if any(
            record.run_id != self.run_id
            for record in self.domain_model_call_audits
        ):
            raise ValueError("Domain trace contains a cross-run Domain model audit")
        if self.final_rca is not None:
            current_run_prefix = f"evidence://{self.run_id}/"
            cited = (
                *self.final_rca.supporting_evidence,
                *self.final_rca.contradicting_evidence,
            )
            if any(
                not reference.startswith(current_run_prefix)
                for reference in cited
            ):
                raise ValueError("Domain trace contains cross-run evidence")
        return self
