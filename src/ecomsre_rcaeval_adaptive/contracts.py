"""Strict contracts for the Single-first Adaptive RCA Agent."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre_rcaeval.adapter import IncidentManifest
from ecomsre_rcaeval_v2.contracts import (
    BoundedEvidenceSnapshotV2,
    CanonicalIndicator,
    IndicatorCandidateSnapshotV2,
    ServiceName,
    SourceName,
    V2Model,
    ProviderUsageDelta,
    SafeValidationError,
)
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptAccountingSummary


class UncertaintyFlag(str, Enum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    METRICS_CONFLICT = "METRICS_CONFLICT"
    LOGS_CONFLICT = "LOGS_CONFLICT"
    NETWORK_OR_TRACE_AMBIGUITY = "NETWORK_OR_TRACE_AMBIGUITY"
    INDICATOR_UNCERTAIN = "INDICATOR_UNCERTAIN"


class EscalationRoute(str, Enum):
    DIRECT_RETURN = "DIRECT_RETURN"
    ESCALATE_LOGS = "ESCALATE_LOGS"
    ESCALATE_TRACES = "ESCALATE_TRACES"
    ESCALATE_BOTH = "ESCALATE_BOTH"


class GateReasonCode(str, Enum):
    DIRECT_CONFIDENT_METRICS_ALIGNED = "DIRECT_CONFIDENT_METRICS_ALIGNED"
    CONFIDENCE_BELOW_DIRECT_THRESHOLD = "CONFIDENCE_BELOW_DIRECT_THRESHOLD"
    CONFIDENCE_BELOW_LOW_THRESHOLD = "CONFIDENCE_BELOW_LOW_THRESHOLD"
    METRICS_RANK_WEAK = "METRICS_RANK_WEAK"
    METRICS_MARGIN_LOW = "METRICS_MARGIN_LOW"
    CROSS_SOURCE_CONFLICT = "CROSS_SOURCE_CONFLICT"
    PREDICTED_SERVICE_EVIDENCE_WEAK = "PREDICTED_SERVICE_EVIDENCE_WEAK"
    INDICATOR_CANDIDATE_MISSING = "INDICATOR_CANDIDATE_MISSING"
    NETWORK_OR_TRACE_AMBIGUITY = "NETWORK_OR_TRACE_AMBIGUITY"
    TRACE_UNAVAILABLE = "TRACE_UNAVAILABLE"


class CausalRole(str, Enum):
    ROOT_CANDIDATE = "ROOT_CANDIDATE"
    PROPAGATED_SYMPTOM = "PROPAGATED_SYMPTOM"
    UNCERTAIN = "UNCERTAIN"


class LogsPairwisePreference(str, Enum):
    INITIAL = "INITIAL"
    ALTERNATIVE = "ALTERNATIVE"
    INCONCLUSIVE = "INCONCLUSIVE"


class FusionAction(str, Enum):
    KEEP_INITIAL = "KEEP_INITIAL"
    OVERRIDE_INITIAL = "OVERRIDE_INITIAL"


class IndicatorResolutionAction(str, Enum):
    KEEP_MODEL_INDICATOR = "KEEP_MODEL_INDICATOR"
    USE_DETERMINISTIC_TOP1 = "USE_DETERMINISTIC_TOP1"
    KEEP_MODEL_INDICATOR_WITH_UNCERTAINTY = (
        "KEEP_MODEL_INDICATOR_WITH_UNCERTAINTY"
    )


class AdaptiveOperationRole(str, Enum):
    INITIAL_DIAGNOSIS = "INITIAL_DIAGNOSIS"
    LOGS_VERIFIER = "LOGS_VERIFIER"
    TRACE_CAUSAL_SPECIALIST = "TRACE_CAUSAL_SPECIALIST"
    FUSION_JUDGE = "FUSION_JUDGE"


class AdaptiveTerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    RUNTIME_CONTRACT_VIOLATION = "RUNTIME_CONTRACT_VIOLATION"
    INTERRUPTED = "INTERRUPTED"


class InitialFailureCode(str, Enum):
    INITIAL_JSON_OR_SCHEMA_INVALID = "INITIAL_JSON_OR_SCHEMA_INVALID"
    INITIAL_SERVICE_NOT_VISIBLE = "INITIAL_SERVICE_NOT_VISIBLE"
    INITIAL_EVIDENCE_REF_NOT_VISIBLE = "INITIAL_EVIDENCE_REF_NOT_VISIBLE"
    INITIAL_DUPLICATE_EVIDENCE_REF = "INITIAL_DUPLICATE_EVIDENCE_REF"
    INITIAL_UNCERTAINTY_FLAG_INVALID = "INITIAL_UNCERTAINTY_FLAG_INVALID"


class SpecialistFailureCode(str, Enum):
    SPECIALIST_JSON_OR_SCHEMA_INVALID = "SPECIALIST_JSON_OR_SCHEMA_INVALID"
    SPECIALIST_BATCH_SOURCE_MISMATCH = "SPECIALIST_BATCH_SOURCE_MISMATCH"
    SPECIALIST_SERVICE_NOT_VISIBLE = "SPECIALIST_SERVICE_NOT_VISIBLE"
    SPECIALIST_EVIDENCE_REF_NOT_VISIBLE = "SPECIALIST_EVIDENCE_REF_NOT_VISIBLE"
    SPECIALIST_DUPLICATE_EVIDENCE_REF = "SPECIALIST_DUPLICATE_EVIDENCE_REF"
    SPECIALIST_OVERLAPPING_EVIDENCE_REF = "SPECIALIST_OVERLAPPING_EVIDENCE_REF"
    SPECIALIST_HYPOTHESIS_COUNT_INVALID = "SPECIALIST_HYPOTHESIS_COUNT_INVALID"
    SPECIALIST_SCORE_INVALID = "SPECIALIST_SCORE_INVALID"
    SPECIALIST_CAUSAL_ROLE_INVALID = "SPECIALIST_CAUSAL_ROLE_INVALID"


class PairwiseFailureCode(str, Enum):
    PAIRWISE_JSON_OR_SCHEMA_INVALID = "PAIRWISE_JSON_OR_SCHEMA_INVALID"
    PAIRWISE_PREFERENCE_INVALID = "PAIRWISE_PREFERENCE_INVALID"
    PAIRWISE_ROLE_INVALID = "PAIRWISE_ROLE_INVALID"
    PAIRWISE_CONFIDENCE_INVALID = "PAIRWISE_CONFIDENCE_INVALID"
    PAIRWISE_EVIDENCE_REF_NOT_VISIBLE = "PAIRWISE_EVIDENCE_REF_NOT_VISIBLE"
    PAIRWISE_DUPLICATE_EVIDENCE_REF = "PAIRWISE_DUPLICATE_EVIDENCE_REF"
    PAIRWISE_OVERLAPPING_EVIDENCE_REF = "PAIRWISE_OVERLAPPING_EVIDENCE_REF"


class FusionFailureCode(str, Enum):
    FUSION_JSON_OR_SCHEMA_INVALID = "FUSION_JSON_OR_SCHEMA_INVALID"
    FUSION_SERVICE_NOT_SUPPORTED = "FUSION_SERVICE_NOT_SUPPORTED"
    FUSION_EVIDENCE_REF_NOT_VISIBLE = "FUSION_EVIDENCE_REF_NOT_VISIBLE"
    FUSION_ACTION_SERVICE_INCONSISTENT = "FUSION_ACTION_SERVICE_INCONSISTENT"
    FUSION_OVERRIDE_LACKS_CONTRADICTION = "FUSION_OVERRIDE_LACKS_CONTRADICTION"
    FUSION_DUPLICATE_EVIDENCE_REF = "FUSION_DUPLICATE_EVIDENCE_REF"
    FUSION_OVERLAPPING_EVIDENCE_REF = "FUSION_OVERLAPPING_EVIDENCE_REF"
    FUSION_REASON_CODE_INVALID = "FUSION_REASON_CODE_INVALID"


FusionGuardrailReason = Literal[
    "OVERLAPPING_EVIDENCE_REJECTED_KEEP_INITIAL"
]


class InitialDiagnosisInput(V2Model):
    schema_version: Literal[
        "rcaeval-single-first-adaptive.initial-input.v1"
    ] = "rcaeval-single-first-adaptive.initial-input.v1"
    incident: IncidentManifest
    bounded_evidence: tuple[BoundedEvidenceSnapshotV2, ...] = Field(
        min_length=1, max_length=64
    )
    indicator_candidates: tuple[IndicatorCandidateSnapshotV2, ...] = Field(
        min_length=1, max_length=6
    )
    visible_services: tuple[ServiceName, ...] = Field(min_length=1, max_length=64)
    visible_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=70)

    @model_validator(mode="after")
    def require_single_external_authority(self) -> InitialDiagnosisInput:
        if any(item.source not in {"metrics", "logs"} for item in self.bounded_evidence):
            raise ValueError("initial input may contain only Metrics and Logs evidence")
        expected_services = tuple(
            sorted(
                {
                    *(item.service for item in self.bounded_evidence),
                    *(item.service for item in self.indicator_candidates),
                }
            )
        )
        expected_refs = tuple(
            sorted(
                {
                    *(item.evidence_ref for item in self.bounded_evidence),
                    *(item.evidence_ref for item in self.indicator_candidates),
                }
            )
        )
        if self.visible_services != expected_services:
            raise ValueError("initial visible services differ from sent evidence")
        if self.visible_evidence_refs != expected_refs:
            raise ValueError("initial visible refs differ from sent evidence")
        return self


class InitialDiagnosis(V2Model):
    root_cause_service: ServiceName
    model_proposed_indicator: CanonicalIndicator | None = None
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=2_000)
    uncertainty_flags: tuple[UncertaintyFlag, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def require_unique_bounded_references(self) -> InitialDiagnosis:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("initial diagnosis evidence references must be unique")
        if len(self.uncertainty_flags) != len(set(self.uncertainty_flags)):
            raise ValueError("initial diagnosis uncertainty flags must be unique")
        return self


class SpecialistInitialDiagnosisContext(V2Model):
    """Provider-visible Initial context without non-authoritative evidence refs."""

    root_cause_service: ServiceName
    model_proposed_indicator: CanonicalIndicator | None
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1, max_length=2_000)
    uncertainty_flags: tuple[UncertaintyFlag, ...]

    @model_validator(mode="after")
    def require_unique_uncertainty_flags(
        self,
    ) -> SpecialistInitialDiagnosisContext:
        if len(self.uncertainty_flags) != len(set(self.uncertainty_flags)):
            raise ValueError("specialist Initial uncertainty flags must be unique")
        return self


class SpecialistInput(V2Model):
    schema_version: Literal[
        "rcaeval-single-first-adaptive.specialist-input.v1"
    ] = "rcaeval-single-first-adaptive.specialist-input.v1"
    source: Literal["logs", "traces"]
    incident: IncidentManifest
    initial_diagnosis: SpecialistInitialDiagnosisContext
    source_evidence: tuple[BoundedEvidenceSnapshotV2, ...] = Field(
        min_length=1, max_length=64
    )
    visible_services: tuple[ServiceName, ...] = Field(min_length=1, max_length=64)
    visible_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_single_source_authority(self) -> SpecialistInput:
        if any(item.source != self.source for item in self.source_evidence):
            raise ValueError("specialist evidence differs from requested source")
        evidence_refs = tuple(item.evidence_ref for item in self.source_evidence)
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("specialist source evidence references must be unique")
        expected_services = tuple(
            sorted(
                {
                    self.initial_diagnosis.root_cause_service,
                    *(item.service for item in self.source_evidence),
                }
            )
        )
        expected_refs = tuple(sorted(evidence_refs))
        if self.visible_services != expected_services:
            raise ValueError("specialist visible services differ from sent evidence")
        if self.visible_evidence_refs != expected_refs:
            raise ValueError("specialist visible refs differ from sent evidence")
        return self


class LogsPairwiseInput(V2Model):
    """Provider-visible Initial-vs-Alternative comparison with Logs authority only."""

    schema_version: Literal[
        "rcaeval-single-first-adaptive.logs-pairwise-input.v1"
    ] = "rcaeval-single-first-adaptive.logs-pairwise-input.v1"
    incident: IncidentManifest
    initial_service: ServiceName
    metrics_alternative_service: ServiceName
    initial_indicator: CanonicalIndicator | None
    logs_evidence: tuple[BoundedEvidenceSnapshotV2, ...] = Field(
        min_length=1, max_length=64
    )
    visible_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)

    @property
    def source(self) -> Literal["logs"]:
        """Expose runtime dispatch authority without serializing an extra field."""

        return "logs"

    @model_validator(mode="after")
    def require_exact_logs_authority(self) -> LogsPairwiseInput:
        if self.initial_service == self.metrics_alternative_service:
            raise ValueError("pairwise candidates must differ")
        if any(item.source != "logs" for item in self.logs_evidence):
            raise ValueError("pairwise input may contain only Logs evidence")
        evidence_refs = tuple(item.evidence_ref for item in self.logs_evidence)
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("pairwise Logs evidence references must be unique")
        if self.visible_evidence_refs != tuple(sorted(evidence_refs)):
            raise ValueError("pairwise visible refs differ from sent Logs evidence")
        return self


class LogsPairwiseVerification(V2Model):
    preference: LogsPairwisePreference
    initial_role: CausalRole
    alternative_role: CausalRole
    supporting_evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    contradicting_evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_disjoint_unique_references(self) -> LogsPairwiseVerification:
        supporting = set(self.supporting_evidence_refs)
        contradicting = set(self.contradicting_evidence_refs)
        if len(supporting) != len(self.supporting_evidence_refs):
            raise ValueError("pairwise supporting evidence references must be unique")
        if len(contradicting) != len(self.contradicting_evidence_refs):
            raise ValueError("pairwise contradicting evidence references must be unique")
        if supporting & contradicting:
            raise ValueError("pairwise evidence roles must be disjoint")
        return self


class GateFeatureSnapshot(V2Model):
    initial_output_valid: Literal[True] = True
    initial_confidence: StrictFloat = Field(ge=0.0, le=1.0)
    metrics_service_rank: StrictInt | None = Field(default=None, ge=1)
    metrics_top1_service: ServiceName | None
    metrics_top2_service: ServiceName | None
    metrics_top1_top2_margin: StrictFloat = Field(ge=0.0)
    initial_equals_metrics_top1: StrictBool
    initial_evidence_supports_predicted_service: StrictBool
    cross_source_service_disagreement: StrictBool
    strong_conflict_count: StrictInt = Field(ge=0, le=3)
    indicator_candidate_available: StrictBool
    trace_available: StrictBool
    network_or_trace_ambiguity: StrictBool
    uncertainty_flags: tuple[UncertaintyFlag, ...]


class EscalationDecision(V2Model):
    route: EscalationRoute
    reason_codes: tuple[GateReasonCode, ...] = Field(min_length=1)
    gate_feature_snapshot: GateFeatureSnapshot

    @model_validator(mode="after")
    def forbid_trace_route_without_trace(self) -> EscalationDecision:
        if not self.gate_feature_snapshot.trace_available and self.route in {
            EscalationRoute.ESCALATE_TRACES,
            EscalationRoute.ESCALATE_BOTH,
        }:
            raise ValueError("trace route requires available traces")
        return self


class RankedHypothesis(V2Model):
    service: ServiceName
    indicator_or_none: CanonicalIndicator | None
    score: StrictFloat = Field(ge=0.0)
    causal_role: CausalRole
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=64)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=64)
    summary: str = Field(min_length=1, max_length=2_000)
    source: SourceName

    @model_validator(mode="after")
    def require_disjoint_unique_references(self) -> RankedHypothesis:
        supporting = set(self.supporting_evidence_refs)
        contradicting = set(self.contradicting_evidence_refs)
        if len(supporting) != len(self.supporting_evidence_refs):
            raise ValueError("supporting evidence references must be unique")
        if len(contradicting) != len(self.contradicting_evidence_refs):
            raise ValueError("contradicting evidence references must be unique")
        if supporting & contradicting:
            raise ValueError("supporting and contradicting evidence must be disjoint")
        return self


class ProviderRankedHypothesis(V2Model):
    """Source-free hypothesis shape exposed to the external Provider."""

    service: ServiceName
    indicator_or_none: CanonicalIndicator | None = None
    score: StrictFloat = Field(ge=0.0)
    causal_role: CausalRole
    supporting_evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    contradicting_evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    summary: str = Field(min_length=1, max_length=2_000)


class ProviderRankedHypothesisBatch(V2Model):
    hypotheses: tuple[ProviderRankedHypothesis, ...] = Field(
        min_length=1, max_length=3
    )


class RankedHypothesisBatch(V2Model):
    source: Literal["logs", "traces"]
    hypotheses: tuple[RankedHypothesis, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def require_source_consistency(self) -> RankedHypothesisBatch:
        if any(item.source != self.source for item in self.hypotheses):
            raise ValueError("hypothesis source differs from batch source")
        return self


class ProviderFusionProposal(V2Model):
    """Provider-facing proposal before internal cross-list safety checks."""

    action: FusionAction
    final_root_service: ServiceName
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    contradicting_evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=16)


class FusionDecision(V2Model):
    action: FusionAction
    final_root_service: ServiceName
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    contradicting_evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def require_unique_fusion_references(self) -> FusionDecision:
        all_refs = self.supporting_evidence_refs + self.contradicting_evidence_refs
        if len(all_refs) != len(set(all_refs)):
            raise ValueError("fusion evidence references must be unique and disjoint")
        return self


class HybridIndicatorResolution(V2Model):
    selected_service: ServiceName
    model_indicator: CanonicalIndicator | None
    deterministic_top1: CanonicalIndicator | None
    final_indicator: CanonicalIndicator | None
    action: IndicatorResolutionAction
    model_candidate_rank: StrictInt | None = Field(default=None, ge=1, le=2)
    deterministic_margin: StrictFloat | None = Field(default=None, ge=0.0)
    evidence_ref: str | None


class AdaptiveDiagnosis(V2Model):
    initial_diagnosis: InitialDiagnosis
    escalation_decision: EscalationDecision
    specialist_hypotheses: tuple[RankedHypothesis, ...] = Field(max_length=6)
    fusion_decision_or_none: FusionDecision | None
    final_root_service: ServiceName
    final_indicator: CanonicalIndicator | None
    indicator_resolution: HybridIndicatorResolution
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def require_route_and_final_consistency(self) -> AdaptiveDiagnosis:
        direct = self.escalation_decision.route is EscalationRoute.DIRECT_RETURN
        if direct != (self.fusion_decision_or_none is None):
            raise ValueError("only direct return may omit fusion")
        expected_root = (
            self.initial_diagnosis.root_cause_service
            if direct
            else self.fusion_decision_or_none.final_root_service  # type: ignore[union-attr]
        )
        if self.final_root_service != expected_root:
            raise ValueError("adaptive final root differs from routed decision")
        if self.indicator_resolution.selected_service != self.final_root_service:
            raise ValueError("indicator resolution service differs from final root")
        if self.indicator_resolution.final_indicator != self.final_indicator:
            raise ValueError("adaptive final indicator differs from resolution")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("adaptive diagnosis evidence references must be unique")
        return self


class AdaptiveOperationTrace(V2Model):
    semantic_operation_index: StrictInt = Field(ge=1, le=4)
    role: AdaptiveOperationRole
    source: SourceName | None
    provider_call_index: StrictInt = Field(ge=1)
    usage: ProviderUsageDelta
    fusion_guardrail_applied: StrictBool = False
    fusion_guardrail_reason: FusionGuardrailReason | None = None
    overlap_count: StrictInt = Field(default=0, ge=0, le=64)

    @model_validator(mode="after")
    def require_role_source_consistency(self) -> AdaptiveOperationTrace:
        expected = {
            AdaptiveOperationRole.INITIAL_DIAGNOSIS: None,
            AdaptiveOperationRole.LOGS_VERIFIER: "logs",
            AdaptiveOperationRole.TRACE_CAUSAL_SPECIALIST: "traces",
            AdaptiveOperationRole.FUSION_JUDGE: None,
        }
        if self.source != expected[self.role]:
            raise ValueError("adaptive operation role differs from source")
        if self.usage.model_calls_delta != 1:
            raise ValueError("completed adaptive operation requires one model call")
        if self.fusion_guardrail_applied:
            if (
                self.role is not AdaptiveOperationRole.FUSION_JUDGE
                or self.fusion_guardrail_reason is None
                or self.overlap_count < 1
            ):
                raise ValueError("adaptive Fusion guardrail trace is incomplete")
        elif (
            self.fusion_guardrail_reason is not None
            or self.overlap_count != 0
        ):
            raise ValueError("inactive Adaptive Fusion guardrail has trace details")
        return self


class AdaptiveCaseResult(V2Model):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: Literal["RE2-OB", "RE2-SS"]
    diagnosis: AdaptiveDiagnosis
    operation_trace: tuple[AdaptiveOperationTrace, ...] = Field(min_length=1, max_length=4)
    tool_calls: StrictInt = Field(ge=2, le=3)
    semantic_operations: StrictInt = Field(ge=1, le=4)
    usage: ProviderUsageDelta

    @model_validator(mode="after")
    def require_exact_route_cost(self) -> AdaptiveCaseResult:
        expected_operations = {
            EscalationRoute.DIRECT_RETURN: 1,
            EscalationRoute.ESCALATE_LOGS: 3,
            EscalationRoute.ESCALATE_TRACES: 3,
            EscalationRoute.ESCALATE_BOTH: 4,
        }[self.diagnosis.escalation_decision.route]
        expected_tools = (
            3
            if self.diagnosis.escalation_decision.route
            in {EscalationRoute.ESCALATE_TRACES, EscalationRoute.ESCALATE_BOTH}
            else 2
        )
        if self.semantic_operations != expected_operations:
            raise ValueError("adaptive semantic operation count differs from route")
        if self.tool_calls != expected_tools:
            raise ValueError("adaptive tool count differs from route")
        if self.semantic_operations != len(self.operation_trace):
            raise ValueError("adaptive operation trace count differs from total")
        if tuple(item.semantic_operation_index for item in self.operation_trace) != tuple(
            range(1, self.semantic_operations + 1)
        ):
            raise ValueError("adaptive operation trace is not contiguous")
        if self.usage.model_calls_delta != self.semantic_operations:
            raise ValueError("adaptive usage differs from semantic operations")
        return self


class AdaptiveTerminalRecord(V2Model):
    schema_version: Literal["rcaeval-single-first-adaptive.terminal.v1"]
    evaluation_version: Literal["single-first-adaptive-v1"]
    candidate_id: str = Field(pattern=r"^candidate-[1-3]$")
    split: Literal["DESIGN", "DEV_VALIDATION"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: Literal["RE2-OB", "RE2-SS"]
    status: AdaptiveTerminalStatus
    result: AdaptiveCaseResult | None
    failure_class: str | None = Field(default=None, max_length=128)
    failure_code: str | None = Field(default=None, max_length=128)
    failure_stage: str | None = Field(default=None, max_length=64)
    safe_validation_error: SafeValidationError | None = None
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime
    latency_ms: StrictFloat = Field(ge=0.0)
    attempt_accounting: AttemptAccountingSummary
    policy_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_terminal_consistency(self) -> AdaptiveTerminalRecord:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("adaptive terminal ended before it started")
        if self.status is AdaptiveTerminalStatus.COMPLETED:
            if self.result is None or any(
                item is not None
                for item in (self.failure_class, self.failure_code, self.failure_stage)
            ):
                raise ValueError("completed adaptive terminal has failure fields")
        elif self.result is not None or self.failure_code is None:
            raise ValueError("failed adaptive terminal requires a safe failure code")
        if self.result is not None and (
            self.result.run_id != self.run_id
            or self.result.case_id != self.case_id
            or self.result.system != self.system
        ):
            raise ValueError("adaptive terminal result identity differs")
        if (
            self.safe_validation_error is not None
            and self.status is not AdaptiveTerminalStatus.INVALID_SCHEMA
        ):
            raise ValueError("safe validation diagnostic requires schema failure")
        return self
