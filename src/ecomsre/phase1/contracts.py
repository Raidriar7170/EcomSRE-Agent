"""Immutable contracts for Phase 1 single-agent RCA and replay."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

EVIDENCE_REF_PATTERN = (
    r"^evidence://[0-9a-f]{32}/(?:metrics|logs|traces|changes)/[0-9]{4}$"
)
_EVIDENCE_REF_RE = re.compile(EVIDENCE_REF_PATTERN)
EvidenceRef = Annotated[str, StringConstraints(pattern=EVIDENCE_REF_PATTERN)]

# Conservative serialization bounds for replay-controlled Phase 1 records.
# These are contract limits, not estimates of model or backend capabilities.
MAX_ID_LENGTH = 128
MAX_SERVICE_LENGTH = 256
MAX_SLI_LENGTH = 256
MAX_INCIDENT_SERVICE_LENGTH = 128
MAX_INCIDENT_SLI_LENGTH = 128
MAX_INCIDENT_SUMMARY_LENGTH = 1000
MAX_EVIDENCE_SUMMARY_LENGTH = 4000
MAX_EVIDENCE_OBSERVATION_TYPE_LENGTH = 128
MAX_EVIDENCE_ATTRIBUTE_NAME_LENGTH = 128
MAX_EVIDENCE_ATTRIBUTE_VALUE_LENGTH = 4000
MAX_EVIDENCE_ATTRIBUTES = 64
MAX_RAW_ARTIFACT_REF_LENGTH = 256
MAX_EVIDENCE_LIMITATIONS = 32
MAX_HYPOTHESIS_LENGTH = 2000
MAX_TEXT_ENTRY_LENGTH = 1000
MAX_CAUSAL_CHAIN_ITEMS = 32
MAX_EVIDENCE_REFS = 64
MAX_MISSING_EVIDENCE_ITEMS = 32
MAX_MODEL_MESSAGES = 64
MAX_MODEL_MESSAGE_LENGTH = 16_000
MAX_MODEL_CONTENT_LENGTH = 65_536
MAX_TERMINAL_STATUS_LENGTH = 64
MAX_TERMINAL_REASON_LENGTH = 512
# Accommodates sub-microsecond clock-read/serialization skew without allowing
# provider-authored timing to escape the Agent-measured call duration.
MODEL_TIMING_TOLERANCE_SECONDS = 0.000001

# This is an intentionally bounded lexical contract. It rejects named tool
# tokens and recognizable executable syntax; it is not arbitrary-language
# security classification.
_SHELL_METACHAR_RE = re.compile(r"[;&|`$<>]|\r|\n")
_SHELL_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:"
    r"bash|sh|zsh|fish|sudo|rm|mv|cp|curl|wget|python(?:3)?|node|"
    r"docker|kubectl|helm|terraform|ansible|chmod|chown|kill|pkill|"
    r"git|make|npm|pnpm|yarn|pip(?:3)?|cat|echo|sed|awk|grep|find"
    r")(?:\s|$)"
)
_TYPED_TOOL_RE = re.compile(
    r"(?i)\b(?:metrics|logs|traces|changes|final)action\b|"
    r"\b(?:metrics|logs|traces|changes)_action\b|"
    r"\b(?:query_metrics|search_logs|search_traces|list_changes)\b|"
    r"\b(?:query|get|search|list)_(?:metrics|logs|traces|changes)\s*\("
)
_EMBEDDED_COMMAND_RE = re.compile(
    r"(?i)\b(?:run|execute|invoke|call|issue)\s+"
    r"(?:sudo\s+)?(?:"
    r"bash|sh|zsh|fish|rm|mv|cp|curl|wget|python(?:3)?|node|"
    r"docker|kubectl|helm|terraform|ansible|chmod|chown|kill|pkill|"
    r"git|make|npm|pnpm|yarn|pip(?:3)?"
    r")\b|"
    r"\b(?:kubectl|docker|helm|terraform)\s+(?:"
    r"apply|delete|deploy|destroy|exec|kill|patch|restart|rollback|"
    r"rollout|scale|start|stop"
    r")\b"
)
_SHELL_COMMAND_TOKEN_RE = re.compile(
    r"(?i)\brm\s+(?:-[a-z]*[rf][a-z]*\s+)?\S+|"
    r"\bcurl\s+(?:-[a-z]\s+)*https?://\S+|"
    r"\bwget\s+(?:-[a-z]\s+)*https?://\S+|"
    r"\b(?:bash|sh)\s+(?:-[a-z]\s+)*\S+\.sh\b|"
    r"\bpython(?:3)?\s+(?:-[a-z]\s+)*\S+\.py\b|"
    r"\bnode\s+(?:-[a-z]\s+)*\S+\.js\b|"
    r"\bgit\s+(?:"
    r"add|branch|checkout|clean|clone|commit|config|diff|fetch|init|"
    r"log|merge|pull|push|rebase|remote|reset|restore|rev-parse|show|"
    r"status|switch|tag|worktree"
    r")\b|"
    r"\bmake\s+(?:-[a-z]\s+|all\b|build\b|clean\b|deploy\b|"
    r"install\b|release\b|test\b)|"
    r"\bdocker\s+compose\s+(?:down|exec|kill|pull|push|restart|rm|"
    r"run|start|stop|up)\b|"
    r"\bkubectl\s+(?:apply|delete|describe|exec|get|logs|patch|"
    r"rollout|scale)\b|"
    r"\bhelm\s+(?:install|rollback|test|uninstall|upgrade)\b|"
    r"\bterraform\s+(?:apply|destroy|import|init|plan|refresh|taint)\b|"
    r"\bsed\s+-[a-z]+\b"
)
_EXPLICIT_RATIONALE_REMEDIATION_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:"
    r"restart|rollback|roll\s+back|scale|deploy|delete|remove|kill|stop|"
    r"start|modify|write|patch|remediate|apply|reconfigure|terminate|"
    r"drain|cordon|uncordon"
    r")\b.{0,200}\b(?:now|immediately|at\s+once)\b|"
    r"\b(?:run|execute|invoke|issue|perform)\s+(?:a\s+|an\s+|the\s+)?(?:"
    r"restart|rollback|roll\s+back|scale|deploy|delete|remove|kill|stop|"
    r"start|modify|write|patch|remediate|apply|reconfigure|terminate|"
    r"drain|cordon|uncordon"
    r")\b"
)
_ABSTAIN_EXPLANATION_RE = re.compile(
    r"(?i)(?:"
    r"\bno\s+confirmed\s+incident\b|"
    r"\bincident\b.{0,60}\b(?:is\s+)?not\s+confirmed\b|"
    r"\b(?:cannot|can't|unable\s+to)\s+confirm\b.{0,60}\bincident\b|"
    r"\b(?:does|do|did)\s+not\s+establish\s+(?:an?\s+)?incident\b"
    r")"
)
_MORE_EVIDENCE_RE = re.compile(
    r"(?i)\b(?:"
    r"more|additional|missing|insufficient|incomplete"
    r")\s+evidence\b|\bevidence\s+(?:is|remains)\s+(?:missing|insufficient)\b"
)
_INCOMPATIBLE_CONFIRMED_RE = re.compile(
    r"(?i)\b(?:"
    r"no\s+confirmed\s+incident|incident\s+(?:is\s+)?not\s+confirmed|"
    r"need(?:s|ed)?\s+(?:more|additional)\s+evidence|"
    r"cannot\s+confirm|can't\s+confirm|unable\s+to\s+confirm|abstain"
    r")\b|"
    r"\b(?:does|do|did)\s+not\s+(?:establish|identify|support)\s+"
    r"(?:a\s+|the\s+)?(?:root\s+)?cause\b"
)
_EVALUATOR_TOKEN_SEQUENCES = (
    ("expected", "root", "service"),
    ("expected", "fault", "mechanism"),
    ("expected", "mechanism"),
    ("ground", "truth"),
    ("scenario", "truth"),
    ("scenario", "label"),
    ("answer", "key"),
    ("evaluator", "only"),
    ("evaluator", "root", "service"),
    ("evaluator", "path"),
)
_EXPECTED_DECISION_MEASUREMENT_NOUNS = frozenset(
    {"latency", "duration", "timeout", "rate"}
)
_DECISION_ENUM_TOKEN_SEQUENCES = (
    ("rca", "confirmed"),
    ("need", "more", "evidence"),
    ("abstain",),
)
MAX_MARKER_SCAN_DEPTH = 64
MAX_MARKER_SCAN_NODES = 10_000
MAX_MARKER_TOKENS = 4_096


def _trimmed(
    value: object,
    *,
    field_name: str,
    maximum: int | None = None,
) -> str:
    if not isinstance(value, str):
        # Pydantic v2 deliberately lets TypeError escape validators instead of
        # wrapping it as ValidationError, so contract type failures use ValueError.
        raise ValueError(f"{field_name} must be a string")  # noqa: TRY004
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must not be empty")
    if maximum is not None and len(trimmed) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return trimmed


def _validate_evidence_ref(value: str) -> str:
    if _EVIDENCE_REF_RE.fullmatch(value) is None:
        raise ValueError("invalid evidence reference")
    return value


def _validate_text_tuple(
    values: tuple[str, ...],
    *,
    field_name: str,
    maximum: int,
) -> tuple[str, ...]:
    return tuple(
        _trimmed(value, field_name=field_name, maximum=maximum)
        for value in values
    )


def _reject_executable_text(
    value: str,
    *,
    field_name: str,
) -> str:
    if "evidence://" in value.lower():
        raise ValueError(f"{field_name} must not contain an Evidence reference")
    if _TYPED_TOOL_RE.search(value):
        raise ValueError(f"{field_name} must not invoke a typed tool")
    if (
        _SHELL_METACHAR_RE.search(value)
        or _SHELL_PREFIX_RE.search(value)
        or _EMBEDDED_COMMAND_RE.search(value)
        or _SHELL_COMMAND_TOKEN_RE.search(value)
    ):
        raise ValueError(f"{field_name} must not contain shell syntax")
    if _EXPLICIT_RATIONALE_REMEDIATION_RE.search(value):
        raise ValueError(
            f"{field_name} must remain advisory and read-only"
        )
    return value


def _reject_evaluator_markers(value: object) -> None:
    """Reject exact evaluator-only markers in any Agent-visible field."""

    active: set[int] = set()
    nodes = 0

    def canonicalize(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text).casefold()
        return "".join(
            character
            for character in normalized
            if unicodedata.category(character) not in {"Cf", "Mn", "Me"}
        )

    def contains_forbidden_sequence(
        text: str,
        *,
        mapping_key: bool,
    ) -> bool:
        canonical = canonicalize(text)
        tokens = tuple(
            re.findall(r"[^\W_]+", canonical, flags=re.UNICODE)
        )
        if len(tokens) > MAX_MARKER_TOKENS:
            raise ValueError("evaluator marker scan exceeds token limit")
        for sequence in _EVALUATOR_TOKEN_SEQUENCES:
            width = len(sequence)
            if any(
                tokens[index : index + width] == sequence
                for index in range(len(tokens) - width + 1)
            ):
                return True
        expected_decision_indices = tuple(
            index
            for index in range(len(tokens) - 1)
            if tokens[index : index + 2] == ("expected", "decision")
        )
        if mapping_key and expected_decision_indices:
            return True
        for index in expected_decision_indices:
            following_index = index + 2
            if (
                following_index >= len(tokens)
                or tokens[following_index]
                not in _EXPECTED_DECISION_MEASUREMENT_NOUNS
            ):
                return True
            tail = tokens[following_index + 1 :]
            for decision_sequence in _DECISION_ENUM_TOKEN_SEQUENCES:
                width = len(decision_sequence)
                if any(
                    tail[tail_index : tail_index + width]
                    == decision_sequence
                    for tail_index in range(len(tail) - width + 1)
                ):
                    return True
        return False

    def visit(item: object, depth: int, *, mapping_key: bool = False) -> None:
        nonlocal nodes
        nodes += 1
        if depth > MAX_MARKER_SCAN_DEPTH or nodes > MAX_MARKER_SCAN_NODES:
            raise ValueError("evaluator marker scan exceeds safety limits")
        if isinstance(item, (bytes, bytearray, memoryview)):
            raise ValueError(  # noqa: TRY004 - Pydantic wraps ValueError
                "Agent-visible data must not contain binary values"
            )
        if isinstance(item, str):
            if contains_forbidden_sequence(item, mapping_key=mapping_key):
                raise ValueError("Agent-visible data contains evaluator marker")
            return
        if isinstance(item, BaseModel):
            object_id = id(item)
            if object_id in active:
                raise ValueError("evaluator marker scan found a cycle")
            active.add(object_id)
            for field_name in type(item).model_fields:
                visit(getattr(item, field_name), depth + 1)
            active.remove(object_id)
            return
        if isinstance(item, Mapping):
            object_id = id(item)
            if object_id in active:
                raise ValueError("evaluator marker scan found a cycle")
            active.add(object_id)
            for key, nested in item.items():
                visit(key, depth + 1, mapping_key=True)
                visit(nested, depth + 1)
            active.remove(object_id)
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            object_id = id(item)
            if object_id in active:
                raise ValueError("evaluator marker scan found a cycle")
            active.add(object_id)
            for nested in item:
                visit(nested, depth + 1)
            active.remove(object_id)

    visit(value, 0)


class Phase1Model(BaseModel):
    """Base for immutable, closed-world Phase 1 records."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_evaluator_only_markers(cls, value: object) -> object:
        _reject_evaluator_markers(value)
        return value

    @model_validator(mode="after")
    def require_utc_and_ordered_intervals(self) -> Phase1Model:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if isinstance(value, datetime):
                utc_offset = value.utcoffset()
                if utc_offset is None or utc_offset.total_seconds() != 0:
                    raise ValueError(
                        f"{field_name} must be a time-aware UTC timestamp"
                    )

        started_at = getattr(self, "started_at", None)
        ended_at = getattr(self, "ended_at", None)
        if (
            isinstance(started_at, datetime)
            and isinstance(ended_at, datetime)
            and ended_at < started_at
        ):
            raise ValueError("ended_at precedes started_at")
        return self


class Severity(str, Enum):
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"


class EvidenceSource(str, Enum):
    METRICS = "METRICS"
    LOGS = "LOGS"
    TRACES = "TRACES"
    CHANGES = "CHANGES"


class RCADecision(str, Enum):
    RCA_CONFIRMED = "RCA_CONFIRMED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    ABSTAIN = "ABSTAIN"


class FaultMechanism(str, Enum):
    RUNTIME_CONFIGURATION_FAILURE = "runtime_configuration_failure"
    REQUEST_PROCESSING_FAILURE = "request_processing_failure"
    CACHE_BACKEND_TIMEOUT = "cache_backend_timeout"


class RecommendedNextAction(str, Enum):
    REVIEW_BOUNDED_REPLAY_EVIDENCE = "Review the bounded replay evidence."
    REVIEW_AVAILABLE_READ_ONLY_OBSERVATIONS = (
        "Review the available read-only observations."
    )
    INSPECT_READ_ONLY_EVIDENCE_INDEX = (
        "Inspect the read-only evidence index."
    )
    COLLECT_ADDITIONAL_READ_ONLY_TELEMETRY_EVIDENCE = (
        "Collect additional read-only telemetry evidence."
    )
    COMPARE_AVAILABLE_READ_ONLY_OBSERVATIONS = (
        "Compare the available read-only observations."
    )
    PRESERVE_CURRENT_REPLAY_EVIDENCE = (
        "Preserve the current replay evidence."
    )
    RETAIN_READ_ONLY_OBSERVATIONS = "Retain the read-only observations."
    REQUEST_ADDITIONAL_READ_ONLY_EVIDENCE_FROM_SERVICE_OWNER = (
        "Request additional read-only evidence from the service owner."
    )
    EXAMINE_EVIDENCE_GAPS = "Examine the evidence gaps."
    VALIDATE_ADDITIONAL_OBSERVATIONS_AGAINST_INCIDENT_WINDOW = (
        "Validate additional observations against the incident window."
    )
    CORRELATE_AVAILABLE_READ_ONLY_OBSERVATIONS = (
        "Correlate the available read-only observations."
    )
    CONTINUE_MONITORING_AFFECTED_SLI = (
        "Continue monitoring the affected SLI."
    )
    MONITOR_AFFECTED_SLI = "Monitor the affected SLI."
    DOCUMENT_EVIDENCE_GAP = "Document the evidence gap."
    AWAIT_ADDITIONAL_OBSERVATIONS = "Await additional observations."
    ASK_SERVICE_OWNER_TO_REVIEW_EVIDENCE = (
        "Ask the service owner to review the evidence."
    )


class StableErrorCode(str, Enum):
    INVALID_QUERY = "INVALID_QUERY"
    OUTSIDE_INCIDENT_WINDOW = "OUTSIDE_INCIDENT_WINDOW"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    MALFORMED_REPLAY_ARTIFACT = "MALFORMED_REPLAY_ARTIFACT"
    INTERNAL_CONTRACT_VIOLATION = "INTERNAL_CONTRACT_VIOLATION"
    MODEL_PROTOCOL_VIOLATION = "MODEL_PROTOCOL_VIOLATION"
    MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"


class AgentTerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    TERMINATED = "TERMINATED"


class AgentTerminalReason(str, Enum):
    FINAL_RCA_ACCEPTED = "FINAL_RCA_ACCEPTED"
    MODEL_CALL_BUDGET_EXHAUSTED = "MODEL_CALL_BUDGET_EXHAUSTED"
    TOKEN_BUDGET_EXHAUSTED = "TOKEN_BUDGET_EXHAUSTED"
    TOOL_CALL_BUDGET_EXHAUSTED = "TOOL_CALL_BUDGET_EXHAUSTED"
    MODEL_CALL_TIMED_OUT = "MODEL_CALL_TIMED_OUT"
    MODEL_GATEWAY_FAILED = "MODEL_GATEWAY_FAILED"
    MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
    FINAL_RCA_INVALID = "FINAL_RCA_INVALID"
    TOOL_EVIDENCE_ALLOCATION_INVALID = (
        "TOOL_EVIDENCE_ALLOCATION_INVALID"
    )
    FAILED_TOOL_PERSISTED_EVIDENCE = "FAILED_TOOL_PERSISTED_EVIDENCE"


class Incident(Phase1Model):
    schema_version: Literal["phase1.incident.v1"]
    incident_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    alert_source_service: str | None = Field(
        default=None,
        max_length=MAX_INCIDENT_SERVICE_LENGTH,
    )
    summary: str = Field(
        min_length=1,
        max_length=MAX_INCIDENT_SUMMARY_LENGTH,
    )
    started_at: datetime
    ended_at: datetime
    affected_sli: str = Field(
        min_length=1,
        max_length=MAX_INCIDENT_SLI_LENGTH,
    )
    severity: Severity

    @field_validator(
        "incident_id",
        "summary",
        "affected_sli",
        mode="before",
    )
    @classmethod
    def trim_required_text(cls, value: object, info: ValidationInfo) -> str:
        field_name = info.field_name or "text"
        return _trimmed(value, field_name=field_name)

    @field_validator("alert_source_service", mode="before")
    @classmethod
    def trim_optional_hint(cls, value: object | None) -> str | None:
        if value is None:
            return None
        return _trimmed(value, field_name="alert_source_service")


class BudgetLimits(Phase1Model):
    max_model_calls: StrictInt = Field(ge=0)
    max_tool_calls: StrictInt = Field(ge=0)
    max_total_tokens: StrictInt = Field(ge=0)


class InvestigationRequest(Phase1Model):
    schema_version: Literal["phase1.investigation-request.v1"]
    request_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    task_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    incident: Incident
    budgets: BudgetLimits

    @field_validator("request_id", "agent_id", "task_id", mode="before")
    @classmethod
    def trim_request_identity(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        return _trimmed(value, field_name=info.field_name or "identity")


EvidenceScalar = str | int | float | bool | None


class EvidenceAttribute(Phase1Model):
    """One immutable, JSON-scalar evidence attribute."""

    name: str = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_ATTRIBUTE_NAME_LENGTH,
    )
    value: EvidenceScalar

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> str:
        return _trimmed(value, field_name="attribute name")

    @field_validator("value", mode="before")
    @classmethod
    def require_json_scalar(cls, value: object) -> EvidenceScalar:
        if type(value) not in {str, int, float, bool, type(None)}:
            raise ValueError("attribute value must be a JSON scalar")
        if isinstance(value, str) and len(value) > MAX_EVIDENCE_ATTRIBUTE_VALUE_LENGTH:
            raise ValueError(
                "attribute value exceeds "
                f"{MAX_EVIDENCE_ATTRIBUTE_VALUE_LENGTH} characters"
            )
        return cast(EvidenceScalar, value)


class Evidence(Phase1Model):
    schema_version: Literal["phase1.evidence.v1"]
    evidence_ref: EvidenceRef
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source: EvidenceSource
    observation_type: str = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_OBSERVATION_TYPE_LENGTH,
    )
    attributes: tuple[EvidenceAttribute, ...] = Field(
        max_length=MAX_EVIDENCE_ATTRIBUTES,
    )
    raw_artifact_ref: str = Field(
        min_length=1,
        max_length=MAX_RAW_ARTIFACT_REF_LENGTH,
        pattern=r"^(?:metrics|logs|traces|changes)\.json#[0-9]+$",
    )
    raw_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitations: tuple[str, ...] = Field(
        max_length=MAX_EVIDENCE_LIMITATIONS,
    )
    summary: str = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_SUMMARY_LENGTH,
    )
    started_at: datetime
    ended_at: datetime
    service: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)

    @field_validator("evidence_ref")
    @classmethod
    def require_exact_reference(cls, value: str) -> str:
        return _validate_evidence_ref(value)

    @field_validator("observation_type", "summary", mode="before")
    @classmethod
    def trim_required_text(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        field_name = info.field_name or "text"
        return _trimmed(value, field_name=field_name)

    @field_validator("service", mode="before")
    @classmethod
    def trim_service(cls, value: object) -> str:
        return _trimmed(value, field_name="service")

    @field_validator("limitations")
    @classmethod
    def require_bounded_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validate_text_tuple(
            values,
            field_name="limitations",
            maximum=MAX_TEXT_ENTRY_LENGTH,
        )

    @field_validator("attributes")
    @classmethod
    def require_unique_attribute_names(
        cls,
        values: tuple[EvidenceAttribute, ...],
    ) -> tuple[EvidenceAttribute, ...]:
        names = tuple(attribute.name for attribute in values)
        if len(names) != len(set(names)):
            raise ValueError("attributes contain duplicate names")
        if names != tuple(sorted(names)):
            raise ValueError(
                "attributes must use canonical order sorted by name"
            )
        return values

    @model_validator(mode="after")
    def require_reference_and_artifact_matches(self) -> Evidence:
        ref_parts = self.evidence_ref.split("/")
        ref_run_id = ref_parts[2]
        ref_source = ref_parts[3].upper()
        if ref_run_id != self.run_id:
            raise ValueError("evidence reference run_id conflicts with run_id")
        if ref_source != self.source.value:
            raise ValueError("evidence reference source conflicts with source")
        expected_artifact_prefix = f"{self.source.value.lower()}.json#"
        if not self.raw_artifact_ref.startswith(expected_artifact_prefix):
            raise ValueError(
                "raw_artifact_ref conflicts with evidence source"
            )
        return self


def _evidence_map(
    items: tuple[Evidence, ...],
    *,
    label: str,
) -> dict[str, Evidence]:
    evidence_by_ref: dict[str, Evidence] = {}
    for item in items:
        if item.evidence_ref in evidence_by_ref:
            raise ValueError(f"{label} contains duplicate evidence refs")
        evidence_by_ref[item.evidence_ref] = item
    return evidence_by_ref


class Hypothesis(Phase1Model):
    schema_version: Literal["phase1.hypothesis.v1"]
    hypothesis_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    statement: str = Field(min_length=1, max_length=MAX_HYPOTHESIS_LENGTH)
    supporting_evidence: tuple[EvidenceRef, ...] = Field(
        max_length=MAX_EVIDENCE_REFS
    )
    contradicting_evidence: tuple[EvidenceRef, ...] = Field(
        max_length=MAX_EVIDENCE_REFS
    )
    confidence: StrictFloat = Field(ge=0, le=1)

    @field_validator("hypothesis_id", "statement", mode="before")
    @classmethod
    def trim_hypothesis_text(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        field_name = info.field_name or "text"
        return _trimmed(value, field_name=field_name)

    @field_validator("supporting_evidence", "contradicting_evidence")
    @classmethod
    def require_exact_references(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(_validate_evidence_ref(value) for value in values)


class RCAResult(Phase1Model):
    schema_version: Literal["phase1.rca-result.v1"]
    decision: RCADecision
    root_service: str | None = Field(
        default=None,
        max_length=MAX_SERVICE_LENGTH,
    )
    fault_mechanism: FaultMechanism | None = None
    causal_chain: tuple[str, ...] = Field(
        max_length=MAX_CAUSAL_CHAIN_ITEMS
    )
    affected_sli: str | None = Field(
        default=None,
        max_length=MAX_SLI_LENGTH,
    )
    supporting_evidence: tuple[EvidenceRef, ...] = Field(
        max_length=MAX_EVIDENCE_REFS
    )
    contradicting_evidence: tuple[EvidenceRef, ...] = Field(
        max_length=MAX_EVIDENCE_REFS
    )
    missing_evidence: tuple[str, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    confidence: StrictFloat = Field(ge=0, le=1)
    decision_rationale: str = Field(min_length=1, max_length=1000)
    recommended_next_action: RecommendedNextAction

    @field_validator(
        "root_service",
        "fault_mechanism",
        "affected_sli",
        mode="before",
    )
    @classmethod
    def trim_optional_claim(
        cls,
        value: object | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        field_name = info.field_name or "claim"
        return _trimmed(value, field_name=field_name)

    @field_validator("causal_chain", "missing_evidence")
    @classmethod
    def require_nonempty_tuple_entries(
        cls,
        values: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        field_name = info.field_name or "entries"
        return _validate_text_tuple(
            values,
            field_name=field_name,
            maximum=MAX_TEXT_ENTRY_LENGTH,
        )

    @field_validator("supporting_evidence", "contradicting_evidence")
    @classmethod
    def require_exact_references(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(_validate_evidence_ref(value) for value in values)

    @field_validator("decision_rationale", mode="before")
    @classmethod
    def validate_rationale_text(cls, value: object) -> str:
        value = _trimmed(
            value,
            field_name="decision_rationale",
            maximum=1000,
        )
        return _reject_executable_text(
            value,
            field_name="decision_rationale",
        )

    @model_validator(mode="after")
    def require_decision_semantics(self) -> RCAResult:
        if self.decision is RCADecision.RCA_CONFIRMED:
            if self.root_service is None:
                raise ValueError("RCA_CONFIRMED requires root_service")
            if self.fault_mechanism is None:
                raise ValueError("RCA_CONFIRMED requires fault_mechanism")
            if not self.causal_chain:
                raise ValueError("RCA_CONFIRMED requires causal_chain")
            if self.affected_sli is None:
                raise ValueError("RCA_CONFIRMED requires affected_sli")
            if len(set(self.supporting_evidence)) < 2:
                raise ValueError(
                    "RCA_CONFIRMED requires at least two supporting references"
                )
            sources = {
                reference.split("/")[3] for reference in self.supporting_evidence
            }
            if len(sources) < 2:
                raise ValueError(
                    "RCA_CONFIRMED requires supporting evidence from two sources"
                )
            if self.missing_evidence:
                raise ValueError(
                    "RCA_CONFIRMED requires empty missing_evidence"
                )
            if _INCOMPATIBLE_CONFIRMED_RE.search(self.decision_rationale):
                raise ValueError(
                    "decision_rationale conflicts with RCA_CONFIRMED"
                )

        elif self.decision is RCADecision.NEED_MORE_EVIDENCE:
            if not self.missing_evidence:
                raise ValueError(
                    "NEED_MORE_EVIDENCE requires missing_evidence"
                )
            if _MORE_EVIDENCE_RE.search(self.decision_rationale) is None:
                raise ValueError(
                    "decision_rationale must explain the evidence gap"
                )

        else:
            if self.root_service is not None:
                raise ValueError("ABSTAIN requires root_service to be absent")
            if self.fault_mechanism is not None:
                raise ValueError(
                    "ABSTAIN requires fault_mechanism to be absent"
                )
            if _ABSTAIN_EXPLANATION_RE.search(self.decision_rationale) is None:
                raise ValueError(
                    "ABSTAIN rationale must explain no confirmed incident"
                )

        return self


class _QueryAction(Phase1Model):
    started_at: datetime
    ended_at: datetime
    service: str | None = Field(default=None, max_length=MAX_SERVICE_LENGTH)

    @field_validator("service", mode="before")
    @classmethod
    def trim_service(cls, value: object | None) -> str | None:
        if value is None:
            return None
        return _trimmed(value, field_name="service")


class MetricsAction(_QueryAction):
    action_type: Literal["metrics"]


class LogsAction(_QueryAction):
    action_type: Literal["logs"]


class TracesAction(_QueryAction):
    action_type: Literal["traces"]


class ChangesAction(_QueryAction):
    action_type: Literal["changes"]


class FinalAction(Phase1Model):
    action_type: Literal["final"]
    result: RCAResult


ToolAction = Annotated[
    MetricsAction | LogsAction | TracesAction | ChangesAction,
    Field(discriminator="action_type"),
]
Action = Annotated[
    MetricsAction | LogsAction | TracesAction | ChangesAction | FinalAction,
    Field(discriminator="action_type"),
]


class ReadOnlyToolName(str, Enum):
    QUERY_METRICS = "query_metrics"
    SEARCH_LOGS = "search_logs"
    SEARCH_TRACES = "search_traces"
    LIST_CHANGES = "list_changes"


class ModelFunctionName(str, Enum):
    QUERY_METRICS = "query_metrics"
    SEARCH_LOGS = "search_logs"
    SEARCH_TRACES = "search_traces"
    LIST_CHANGES = "list_changes"
    SUBMIT_RCA = "submit_rca"


class RemainingBudgets(Phase1Model):
    model_calls: StrictInt = Field(ge=0)
    tool_calls: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)


class TranscriptEntry(Phase1Model):
    sequence: StrictInt = Field(ge=1)
    action: ToolAction
    tool_name: ReadOnlyToolName
    status: Literal["OK", "ERROR"]
    error_code: StableErrorCode | None
    evidence_refs: tuple[EvidenceRef, ...] = Field(
        max_length=MAX_EVIDENCE_REFS,
    )

    @model_validator(mode="after")
    def require_consistent_entry(self) -> TranscriptEntry:
        expected_name = {
            "metrics": ReadOnlyToolName.QUERY_METRICS,
            "logs": ReadOnlyToolName.SEARCH_LOGS,
            "traces": ReadOnlyToolName.SEARCH_TRACES,
            "changes": ReadOnlyToolName.LIST_CHANGES,
        }[self.action.action_type]
        if self.tool_name is not expected_name:
            raise ValueError("tool_name conflicts with transcript action")
        if self.status == "OK" and self.error_code is not None:
            raise ValueError("OK transcript entry cannot have an error code")
        if self.status == "ERROR":
            if self.error_code is None:
                raise ValueError(
                    "ERROR transcript entry requires an error code"
                )
            if self.evidence_refs:
                raise ValueError(
                    "ERROR transcript entry cannot contain evidence refs"
                )
        return self


class ModelUsage(Phase1Model):
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_consistent_total(self) -> ModelUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens conflicts with token counts")
        return self


class ModelConfiguration(Phase1Model):
    """Serializable model settings with no credential-bearing fields."""

    model_name: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)
    temperature: StrictFloat
    model_timeout_seconds: StrictFloat = Field(gt=0)

    @field_validator("model_name", mode="before")
    @classmethod
    def trim_model_name(cls, value: object) -> str:
        return _trimmed(value, field_name="model_name")


class ModelRequest(Phase1Model):
    schema_version: Literal["phase1.model-request.v1"]
    request_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    incident_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    task_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    model_name: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)
    incident: Incident
    transcript: tuple[TranscriptEntry, ...] = Field(
        max_length=MAX_MODEL_MESSAGES,
    )
    evidence: tuple[Evidence, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    remaining_budgets: RemainingBudgets
    allowed_actions: tuple[ModelFunctionName, ...] = Field(
        min_length=5,
        max_length=5,
    )
    temperature: StrictFloat
    timeout_seconds: StrictFloat = Field(gt=0)

    @field_validator(
        "request_id",
        "agent_id",
        "incident_id",
        "task_id",
        "model_name",
        mode="before",
    )
    @classmethod
    def trim_identity(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        field_name = info.field_name or "identity"
        return _trimmed(value, field_name=field_name)

    @field_validator("allowed_actions")
    @classmethod
    def require_exact_allowed_actions(
        cls,
        values: tuple[ModelFunctionName, ...],
    ) -> tuple[ModelFunctionName, ...]:
        if values != tuple(ModelFunctionName):
            raise ValueError("allowed_actions must be the exact action catalog")
        return values

    @model_validator(mode="after")
    def require_request_identity_consistency(self) -> ModelRequest:
        if self.incident_id != self.incident.incident_id:
            raise ValueError("incident_id conflicts with incident")
        if any(item.run_id != self.run_id for item in self.evidence):
            raise ValueError("request Evidence belongs to another run")
        sequences = tuple(item.sequence for item in self.transcript)
        if sequences != tuple(range(1, len(self.transcript) + 1)):
            raise ValueError(
                "transcript sequence must be exactly 1 through N"
            )
        return self


class ModelResponse(Phase1Model):
    schema_version: Literal["phase1.model-response.v1"]
    request_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    response_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    incident_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    task_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    provider_name: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)
    model_name: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)
    action: Action
    usage: ModelUsage
    started_at: datetime
    ended_at: datetime
    monotonic_duration_seconds: StrictFloat = Field(ge=0)
    error_code: StableErrorCode | None = None

    @field_validator(
        "request_id",
        "response_id",
        "agent_id",
        "incident_id",
        "task_id",
        "provider_name",
        "model_name",
        mode="before",
    )
    @classmethod
    def trim_identity(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        field_name = info.field_name or "identity"
        return _trimmed(value, field_name=field_name)


class ToolCallRecord(Phase1Model):
    schema_version: Literal["phase1.tool-call-record.v1"]
    call_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    incident_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    task_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    tool_name: ReadOnlyToolName
    action: ToolAction
    evidence: tuple[Evidence, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    evidence_refs: tuple[EvidenceRef, ...] = Field(
        max_length=MAX_EVIDENCE_REFS,
    )
    started_at: datetime
    ended_at: datetime
    monotonic_duration_seconds: StrictFloat = Field(ge=0)
    budget_consumed: StrictBool
    dispatched: StrictBool
    evidence_quarantined: StrictBool
    usable: StrictBool
    status: Literal["OK", "ERROR"]
    error_code: StableErrorCode | None = None

    @field_validator(
        "call_id",
        "agent_id",
        "incident_id",
        "task_id",
        mode="before",
    )
    @classmethod
    def trim_tool_identity(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        return _trimmed(value, field_name=info.field_name or "identity")

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> ToolCallRecord:
        expected_source, expected_name = {
            "metrics": (
                EvidenceSource.METRICS,
                ReadOnlyToolName.QUERY_METRICS,
            ),
            "logs": (EvidenceSource.LOGS, ReadOnlyToolName.SEARCH_LOGS),
            "traces": (
                EvidenceSource.TRACES,
                ReadOnlyToolName.SEARCH_TRACES,
            ),
            "changes": (
                EvidenceSource.CHANGES,
                ReadOnlyToolName.LIST_CHANGES,
            ),
        }[self.action.action_type]
        if self.tool_name is not expected_name:
            raise ValueError("tool_name conflicts with action")
        if any(item.source is not expected_source for item in self.evidence):
            raise ValueError(
                f"{self.action.action_type} action evidence source must be "
                f"{expected_source.value}"
            )
        expected_refs = tuple(item.evidence_ref for item in self.evidence)
        if self.evidence_refs != expected_refs:
            raise ValueError("evidence_refs conflict with Evidence records")
        if self.dispatched and not self.budget_consumed:
            raise ValueError("dispatched tool call requires consumed budget")
        if (
            self.evidence
            and not self.dispatched
            and not self.evidence_quarantined
        ):
            raise ValueError("undispatched tool call cannot contain Evidence")
        if self.status == "OK" and self.error_code is not None:
            raise ValueError("successful tool call cannot have an error_code")
        if self.status == "OK" and (
            not self.budget_consumed or not self.dispatched
        ):
            raise ValueError(
                "successful tool call requires budget and dispatch"
            )
        if self.status == "OK" and (
            self.evidence_quarantined or not self.usable
        ):
            raise ValueError(
                "successful tool call must be usable and not quarantined"
            )
        if self.status == "ERROR":
            if self.usable:
                raise ValueError("failed tool call cannot be usable")
            if self.evidence and not self.evidence_quarantined:
                raise ValueError("failed tool call cannot contain Evidence")
            if self.error_code is None:
                raise ValueError("failed tool call requires an error_code")
        if self.evidence_quarantined:
            if (
                self.status != "ERROR"
                or self.usable
                or self.error_code
                is not StableErrorCode.INTERNAL_CONTRACT_VIOLATION
            ):
                raise ValueError(
                    "quarantined tool evidence requires an unusable internal "
                    "contract error"
                )
        return self


class ModelCallRecord(Phase1Model):
    schema_version: Literal["phase1.model-call-record.v1"]
    call_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    incident_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    task_id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    request: ModelRequest
    response: ModelResponse | None
    started_at: datetime
    ended_at: datetime
    monotonic_duration_seconds: StrictFloat = Field(ge=0)
    model_call_consumed: StrictBool
    charged_tokens: StrictInt = Field(ge=0)
    status: Literal["OK", "ERROR"]
    error_code: StableErrorCode | None = None

    @field_validator(
        "call_id",
        "agent_id",
        "incident_id",
        "task_id",
        mode="before",
    )
    @classmethod
    def trim_model_call_identity(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        return _trimmed(value, field_name=info.field_name or "identity")

    @model_validator(mode="after")
    def require_consistent_response(self) -> ModelCallRecord:
        identity = (
            self.run_id,
            self.agent_id,
            self.incident_id,
            self.task_id,
        )
        request_identity = (
            self.request.run_id,
            self.request.agent_id,
            self.request.incident_id,
            self.request.task_id,
        )
        if identity != request_identity:
            raise ValueError("model call identity conflicts with request")
        if not self.model_call_consumed:
            raise ValueError("model call record requires consumed call budget")
        if self.response is None:
            if self.error_code is None:
                raise ValueError("missing model response requires an error_code")
            if self.status != "ERROR":
                raise ValueError("missing response requires ERROR status")
            if self.charged_tokens != 0:
                raise ValueError("missing response cannot charge tokens")
            return self

        if self.response.request_id != self.request.request_id:
            raise ValueError(
                "model response request_id conflicts with request"
            )
        if self.response.model_name != self.request.model_name:
            raise ValueError(
                "model response model_name conflicts with request"
            )
        response_identity = (
            self.response.run_id,
            self.response.agent_id,
            self.response.incident_id,
            self.response.task_id,
        )
        if response_identity != identity:
            raise ValueError("model response identity conflicts with call")
        if (
            self.response.error_code is not None
            and self.error_code is not self.response.error_code
        ):
            raise ValueError(
                "model call error_code conflicts with provider response"
            )
        if (
            self.response.started_at < self.started_at
            or self.response.ended_at > self.ended_at
        ):
            raise ValueError("model response timing escapes model call")
        if (
            self.response.monotonic_duration_seconds
            > (
                self.monotonic_duration_seconds
                + MODEL_TIMING_TOLERANCE_SECONDS
            )
        ):
            raise ValueError(
                "model response monotonic timing exceeds model call"
            )
        if self.status == "OK" and self.error_code is not None:
            raise ValueError("OK model call cannot have an error code")
        if (
            self.status == "OK"
            and self.charged_tokens != self.response.usage.total_tokens
        ):
            raise ValueError("OK model call must charge observed usage")
        if self.status == "ERROR" and self.error_code is None:
            raise ValueError("ERROR model call requires an error code")
        if self.status == "ERROR" and self.charged_tokens != 0:
            raise ValueError("rejected model response cannot charge tokens")
        return self


class BudgetSnapshot(Phase1Model):
    model_calls: StrictInt = Field(ge=0)
    tool_calls: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    limits: BudgetLimits

    @model_validator(mode="after")
    def require_within_limits(self) -> BudgetSnapshot:
        if self.model_calls > self.limits.max_model_calls:
            raise ValueError("model call budget exceeded")
        if self.tool_calls > self.limits.max_tool_calls:
            raise ValueError("tool call budget exceeded")
        if self.total_tokens > self.limits.max_total_tokens:
            raise ValueError("token budget exceeded")
        return self


class AgentRunReport(Phase1Model):
    schema_version: Literal["phase1.agent-run-report.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    request: InvestigationRequest
    model_configuration: ModelConfiguration
    final_rca: RCAResult | None
    model_call_records: tuple[ModelCallRecord, ...]
    tool_call_records: tuple[ToolCallRecord, ...]
    evidence_index: tuple[Evidence, ...]
    budget_limits: BudgetLimits
    budget_snapshot: BudgetSnapshot
    started_at: datetime
    ended_at: datetime
    monotonic_duration_seconds: StrictFloat = Field(ge=0)
    terminal_status: AgentTerminalStatus
    terminal_reason: AgentTerminalReason
    terminal_error_code: StableErrorCode | None
    schema_valid: StrictBool
    evidence_references_valid: StrictBool

    @model_validator(mode="after")
    def require_report_consistency(self) -> AgentRunReport:
        if self.terminal_status is AgentTerminalStatus.COMPLETED:
            if self.final_rca is None:
                raise ValueError("completed report requires final RCA")
            if (
                self.terminal_reason
                is not AgentTerminalReason.FINAL_RCA_ACCEPTED
                or self.terminal_error_code is not None
            ):
                raise ValueError(
                    "completed report terminal fields are inconsistent"
                )
        else:
            if self.final_rca is not None:
                raise ValueError("terminated report cannot claim final RCA")
            if (
                self.terminal_reason
                is AgentTerminalReason.FINAL_RCA_ACCEPTED
                or self.terminal_error_code is None
            ):
                raise ValueError(
                    "terminated report terminal fields are inconsistent"
                )
            expected_terminal_error = {
                AgentTerminalReason.MODEL_CALL_BUDGET_EXHAUSTED: (
                    StableErrorCode.BUDGET_EXHAUSTED
                ),
                AgentTerminalReason.TOKEN_BUDGET_EXHAUSTED: (
                    StableErrorCode.BUDGET_EXHAUSTED
                ),
                AgentTerminalReason.TOOL_CALL_BUDGET_EXHAUSTED: (
                    StableErrorCode.BUDGET_EXHAUSTED
                ),
                AgentTerminalReason.MODEL_CALL_TIMED_OUT: (
                    StableErrorCode.TIMEOUT
                ),
                AgentTerminalReason.MODEL_GATEWAY_FAILED: (
                    StableErrorCode.MODEL_PROTOCOL_VIOLATION
                ),
                AgentTerminalReason.MODEL_RESPONSE_INVALID: (
                    StableErrorCode.MODEL_PROTOCOL_VIOLATION
                ),
                AgentTerminalReason.FINAL_RCA_INVALID: (
                    StableErrorCode.MODEL_PROTOCOL_VIOLATION
                ),
                AgentTerminalReason.TOOL_EVIDENCE_ALLOCATION_INVALID: (
                    StableErrorCode.INTERNAL_CONTRACT_VIOLATION
                ),
                AgentTerminalReason.FAILED_TOOL_PERSISTED_EVIDENCE: (
                    StableErrorCode.INTERNAL_CONTRACT_VIOLATION
                ),
            }.get(self.terminal_reason)
            if self.terminal_error_code is not expected_terminal_error:
                raise ValueError(
                    "terminated report terminal error is inconsistent"
                )
        if self.request.run_id != self.run_id:
            raise ValueError("request run_id conflicts with report run_id")
        if self.request.budgets != self.budget_limits:
            raise ValueError(
                "request budgets conflict with report budget limits"
            )
        if self.budget_snapshot.limits != self.budget_limits:
            raise ValueError("budget snapshot limits conflict with report limits")
        expected_model_call_ids = tuple(
            f"model-call-{index:04d}"
            for index in range(1, len(self.model_call_records) + 1)
        )
        model_call_ids = tuple(
            record.call_id for record in self.model_call_records
        )
        if model_call_ids != expected_model_call_ids:
            raise ValueError(
                "model call_id values must be unique and sequential"
            )
        expected_request_ids = tuple(
            f"model-request-{index:04d}"
            for index in range(1, len(self.model_call_records) + 1)
        )
        request_ids = tuple(
            record.request.request_id
            for record in self.model_call_records
        )
        if request_ids != expected_request_ids:
            raise ValueError(
                "model request_id values must be unique and sequential"
            )
        response_ids = tuple(
            record.response.response_id
            for record in self.model_call_records
            if record.response is not None
        )
        if len(response_ids) != len(set(response_ids)):
            raise ValueError("model response_id values must be unique")
        expected_tool_call_ids = tuple(
            f"tool-call-{index:04d}"
            for index in range(1, len(self.tool_call_records) + 1)
        )
        tool_call_ids = tuple(
            record.call_id for record in self.tool_call_records
        )
        if tool_call_ids != expected_tool_call_ids:
            raise ValueError(
                "tool call_id values must be unique and sequential"
            )
        if self.terminal_status is AgentTerminalStatus.COMPLETED:
            if not self.model_call_records:
                raise ValueError(
                    "completed report requires a final model call"
                )
            last_model_record = self.model_call_records[-1]
            if (
                last_model_record.status != "OK"
                or last_model_record.response is None
                or type(last_model_record.response.action) is not FinalAction
                or last_model_record.response.action.result != self.final_rca
            ):
                raise ValueError(
                    "completed report final RCA must equal the last OK "
                    "FinalAction"
                )
        elif self.terminal_reason in {
            AgentTerminalReason.MODEL_CALL_TIMED_OUT,
            AgentTerminalReason.MODEL_GATEWAY_FAILED,
            AgentTerminalReason.MODEL_RESPONSE_INVALID,
        }:
            if not self.model_call_records:
                raise ValueError(
                    "model terminal error requires a model call record"
                )
            last_model_record = self.model_call_records[-1]
            if (
                last_model_record.status != "ERROR"
                or last_model_record.error_code
                is not self.terminal_error_code
            ):
                raise ValueError(
                    "terminal error conflicts with last model call outcome"
                )
        elif self.terminal_reason is AgentTerminalReason.FINAL_RCA_INVALID:
            if not self.model_call_records:
                raise ValueError(
                    "invalid final RCA requires a model call record"
                )
            last_model_record = self.model_call_records[-1]
            if (
                last_model_record.status != "OK"
                or last_model_record.response is None
                or type(last_model_record.response.action) is not FinalAction
            ):
                raise ValueError(
                    "invalid final RCA must preserve its OK FinalAction record"
                )
        consumed_model_calls = sum(
            record.model_call_consumed
            for record in self.model_call_records
        )
        if self.budget_snapshot.model_calls != consumed_model_calls:
            raise ValueError("model call count conflicts with budget snapshot")
        consumed_tool_calls = sum(
            record.budget_consumed
            for record in self.tool_call_records
        )
        if self.budget_snapshot.tool_calls != consumed_tool_calls:
            raise ValueError("tool call count conflicts with budget snapshot")

        recorded_tokens = sum(
            record.charged_tokens for record in self.model_call_records
        )
        if self.budget_snapshot.total_tokens != recorded_tokens:
            raise ValueError("token count conflicts with model call records")
        expected_identity = (
            self.run_id,
            self.request.agent_id,
            self.request.incident.incident_id,
            self.request.task_id,
        )
        for model_record in self.model_call_records:
            if (
                model_record.run_id,
                model_record.agent_id,
                model_record.incident_id,
                model_record.task_id,
            ) != expected_identity:
                raise ValueError(
                    "model call identity conflicts with report request"
                )
            if (
                model_record.started_at < self.started_at
                or model_record.ended_at > self.ended_at
                or model_record.monotonic_duration_seconds
                > self.monotonic_duration_seconds
            ):
                raise ValueError("model call timing escapes report")

        previous_prefix_length: int | None = None
        previous_model_record: ModelCallRecord | None = None
        preceding_charged_tokens = 0
        for call_index, model_record in enumerate(
            self.model_call_records,
            start=1,
        ):
            prefix_length = len(model_record.request.transcript)
            if prefix_length > len(self.tool_call_records):
                raise ValueError(
                    "model request transcript exceeds tool record history"
                )
            tool_prefix = self.tool_call_records[:prefix_length]
            expected_transcript = tuple(
                TranscriptEntry(
                    sequence=index,
                    action=record.action,
                    tool_name=record.tool_name,
                    status=record.status,
                    error_code=record.error_code,
                    evidence_refs=record.evidence_refs,
                )
                for index, record in enumerate(tool_prefix, start=1)
            )
            if model_record.request.transcript != expected_transcript:
                raise ValueError(
                    "model request transcript conflicts with tool prefix"
                )
            expected_evidence = tuple(
                item for record in tool_prefix for item in record.evidence
            )
            if model_record.request.evidence != expected_evidence:
                raise ValueError(
                    "model request Evidence conflicts with tool prefix"
                )
            if any(record.evidence_quarantined for record in tool_prefix):
                raise ValueError(
                    "model request cannot include a quarantined tool prefix"
                )
            if model_record.request.incident != self.request.incident:
                raise ValueError(
                    "model request incident conflicts with report incident"
                )
            expected_remaining = RemainingBudgets(
                model_calls=self.budget_limits.max_model_calls - call_index,
                tool_calls=(
                    self.budget_limits.max_tool_calls
                    - sum(record.budget_consumed for record in tool_prefix)
                ),
                total_tokens=(
                    self.budget_limits.max_total_tokens
                    - preceding_charged_tokens
                ),
            )
            if model_record.request.remaining_budgets != expected_remaining:
                raise ValueError(
                    "model request remaining budgets conflict with report "
                    "history"
                )
            if previous_prefix_length is None:
                if prefix_length != 0:
                    raise ValueError(
                        "first model request must have an empty transcript"
                    )
            else:
                if prefix_length != previous_prefix_length + 1:
                    raise ValueError(
                        "consecutive model request transcripts must advance "
                        "by one tool"
                    )
                assert previous_model_record is not None
                if (
                    previous_model_record.status != "OK"
                    or previous_model_record.response is None
                    or type(previous_model_record.response.action)
                    is FinalAction
                    or self.tool_call_records[previous_prefix_length].action
                    != previous_model_record.response.action
                ):
                    raise ValueError(
                        "model transcript continuation conflicts with prior "
                        "model action"
                    )
            previous_prefix_length = prefix_length
            previous_model_record = model_record
            preceding_charged_tokens += model_record.charged_tokens
        if not self.model_call_records:
            if self.tool_call_records:
                raise ValueError("tool records require a prior model action")
        else:
            assert previous_prefix_length is not None
            trailing_tool_count = (
                len(self.tool_call_records) - previous_prefix_length
            )
            if trailing_tool_count not in {0, 1}:
                raise ValueError(
                    "tool history cannot continue beyond the last model action"
                )
            if trailing_tool_count == 1:
                assert previous_model_record is not None
                if (
                    previous_model_record.status != "OK"
                    or previous_model_record.response is None
                    or type(previous_model_record.response.action)
                    is FinalAction
                    or self.tool_call_records[previous_prefix_length].action
                    != previous_model_record.response.action
                ):
                    raise ValueError(
                        "trailing tool record conflicts with the last model "
                        "action"
                    )

        accepted_final_action = any(
            record.status == "OK"
            and record.response is not None
            and type(record.response.action) is FinalAction
            for record in self.model_call_records
        )
        terminal_last_model_record = (
            self.model_call_records[-1]
            if self.model_call_records
            else None
        )
        last_successful_tool_action = (
            terminal_last_model_record is not None
            and terminal_last_model_record.status == "OK"
            and terminal_last_model_record.response is not None
            and type(terminal_last_model_record.response.action)
            is not FinalAction
        )
        has_corresponding_trailing_tool = (
            terminal_last_model_record is not None
            and len(self.tool_call_records)
            == len(terminal_last_model_record.request.transcript) + 1
        )
        if (
            self.terminal_reason
            is AgentTerminalReason.MODEL_CALL_BUDGET_EXHAUSTED
        ):
            if (
                consumed_model_calls
                != self.budget_limits.max_model_calls
                or accepted_final_action
                or (
                    terminal_last_model_record is not None
                    and not (
                        last_successful_tool_action
                        and has_corresponding_trailing_tool
                    )
                )
            ):
                raise ValueError(
                    "model call budget terminal lacks an exhausted limit "
                    "and pending continuation"
                )
        elif (
            self.terminal_reason
            is AgentTerminalReason.TOOL_CALL_BUDGET_EXHAUSTED
        ):
            if (
                consumed_tool_calls != self.budget_limits.max_tool_calls
                or not last_successful_tool_action
                or has_corresponding_trailing_tool
            ):
                raise ValueError(
                    "tool call budget terminal requires an exhausted limit "
                    "and an unexecuted last tool action"
                )
        elif (
            self.terminal_reason
            is AgentTerminalReason.TOKEN_BUDGET_EXHAUSTED
        ):
            exhausted_before_next_call = (
                recorded_tokens == self.budget_limits.max_total_tokens
                and consumed_model_calls
                < self.budget_limits.max_model_calls
                and (
                    terminal_last_model_record is None
                    or (
                        last_successful_tool_action
                        and has_corresponding_trailing_tool
                    )
                )
            )
            oversized_last_response = (
                terminal_last_model_record is not None
                and terminal_last_model_record.status == "ERROR"
                and terminal_last_model_record.error_code
                is StableErrorCode.BUDGET_EXHAUSTED
                and terminal_last_model_record.charged_tokens == 0
                and terminal_last_model_record.response is not None
                and terminal_last_model_record.response.usage.total_tokens
                > terminal_last_model_record.request.remaining_budgets.total_tokens
            )
            if (
                accepted_final_action
                or not (
                    exhausted_before_next_call or oversized_last_response
                )
            ):
                raise ValueError(
                    "token budget terminal lacks an exhausted limit or "
                    "oversized rejected response"
                )
        for tool_record in self.tool_call_records:
            if (
                tool_record.run_id,
                tool_record.agent_id,
                tool_record.incident_id,
                tool_record.task_id,
            ) != expected_identity:
                raise ValueError(
                    "tool call identity conflicts with report request"
                )
            if (
                tool_record.started_at < self.started_at
                or tool_record.ended_at > self.ended_at
                or tool_record.monotonic_duration_seconds
                > self.monotonic_duration_seconds
            ):
                raise ValueError("tool call timing escapes report")

        quarantined_records = tuple(
            record
            for record in self.tool_call_records
            if record.evidence_quarantined
        )
        if quarantined_records:
            if (
                len(quarantined_records) != 1
                or quarantined_records[0] is not self.tool_call_records[-1]
            ):
                raise ValueError(
                    "quarantined tool record must be the final tool record"
                )
            expected_quarantine_reason = {
                AgentTerminalReason.TOOL_EVIDENCE_ALLOCATION_INVALID,
                AgentTerminalReason.FAILED_TOOL_PERSISTED_EVIDENCE,
            }
            if (
                self.terminal_status is not AgentTerminalStatus.TERMINATED
                or self.terminal_reason not in expected_quarantine_reason
            ):
                raise ValueError(
                    "tool quarantine requires an integrity terminal reason"
                )
        elif self.terminal_reason in {
            AgentTerminalReason.TOOL_EVIDENCE_ALLOCATION_INVALID,
            AgentTerminalReason.FAILED_TOOL_PERSISTED_EVIDENCE,
        }:
            raise ValueError(
                "tool integrity termination requires a quarantined record"
            )

        index_by_ref = _evidence_map(
            self.evidence_index,
            label="evidence index",
        )
        tool_evidence = tuple(
            item
            for record in self.tool_call_records
            for item in record.evidence
        )
        tool_by_ref = _evidence_map(
            tool_evidence,
            label="tool records",
        )
        final_refs = (
            ()
            if self.final_rca is None
            else (
                self.final_rca.supporting_evidence
                + self.final_rca.contradicting_evidence
            )
        )
        if len(final_refs) != len(set(final_refs)):
            raise ValueError("final RCA contains duplicate evidence refs")

        quarantined_refs = {
            item.evidence_ref
            for record in quarantined_records
            for item in record.evidence
        }
        if quarantined_refs.intersection(final_refs):
            raise ValueError(
                "final RCA cannot reference quarantined evidence"
            )

        all_refs = set(index_by_ref) | set(tool_by_ref) | set(final_refs)
        if any(reference.split("/")[2] != self.run_id for reference in all_refs):
            raise ValueError("evidence reference run_id conflicts with report run_id")

        orphan_index_refs = set(index_by_ref) - set(tool_by_ref)
        if orphan_index_refs:
            raise ValueError(
                "evidence index contains orphan evidence not produced by a tool"
            )
        unindexed_tool_refs = set(tool_by_ref) - set(index_by_ref)
        if unindexed_tool_refs:
            raise ValueError("tool evidence is absent from evidence index")
        if any(
            index_by_ref[reference] != tool_evidence_item
            for reference, tool_evidence_item in tool_by_ref.items()
        ):
            raise ValueError(
                "tool and index evidence content conflicts for the same ref"
            )

        if self.evidence_references_valid and not set(final_refs).issubset(
            index_by_ref
        ):
            raise ValueError(
                "evidence references marked valid are absent from evidence index"
            )
        return self
