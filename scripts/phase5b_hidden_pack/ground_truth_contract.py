"""Out-of-band evaluator-only truth contract for a Phase 5B hidden pack."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
    field_validator,
    model_validator,
)

from ecomsre.phase1.contracts import (
    MAX_CAUSAL_CHAIN_ITEMS,
    MAX_MISSING_EVIDENCE_ITEMS,
    MAX_SERVICE_LENGTH,
    MAX_SLI_LENGTH,
    MAX_TEXT_ENTRY_LENGTH,
    EvidenceSource,
    _reject_executable_text,
    _trimmed,
)
from ecomsre.phase5a.contracts import DiagnosisDecisionV2, UnifiedMechanismV2


_SOURCE_ORDER = {
    EvidenceSource.METRICS: 0,
    EvidenceSource.LOGS: 1,
    EvidenceSource.TRACES: 2,
    EvidenceSource.CHANGES: 3,
}

SubsetIdentifier = Annotated[
    str,
    Strict(),
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9_]*$",
    ),
]


class HiddenGroundTruthV1(BaseModel):
    """One private truth record; never an Agent-visible input contract."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        strict=True,
    )

    schema_version: Literal["phase5b.hidden-ground-truth.v1"]
    evaluation_version: Literal["phase5b.v1"]
    template_id: str = Field(pattern=r"^hidden-0[1-6]$")
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    decision: DiagnosisDecisionV2
    incident_confirmed: bool
    root_service: str | None = Field(default=None, max_length=MAX_SERVICE_LENGTH)
    fault_mechanism: UnifiedMechanismV2 | None = None
    causal_chain: tuple[str, ...] = Field(max_length=MAX_CAUSAL_CHAIN_ITEMS)
    affected_sli: str | None = Field(default=None, max_length=MAX_SLI_LENGTH)
    required_support_sources: tuple[EvidenceSource, ...] = Field(max_length=4)
    required_contradiction_handling: tuple[str, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    required_missing_evidence: tuple[str, ...] = Field(
        max_length=MAX_MISSING_EVIDENCE_ITEMS
    )
    write_disposition: Literal[
        "NO_ACTION",
        "SAFE_REPLAY_REMEDIATION_CANDIDATE",
    ]
    difficult_subsets: tuple[SubsetIdentifier, ...] = Field(min_length=1)

    @field_validator(
        "causal_chain",
        "required_support_sources",
        "required_contradiction_handling",
        "required_missing_evidence",
        "difficult_subsets",
        mode="before",
    )
    @classmethod
    def parse_json_arrays(cls, value: object) -> tuple[object, ...]:
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        raise ValueError("hidden ground-truth collection fields must be JSON arrays")

    @field_validator("decision", mode="before")
    @classmethod
    def parse_decision(cls, value: object) -> DiagnosisDecisionV2:
        if isinstance(value, DiagnosisDecisionV2):
            return value
        if type(value) is str:
            return DiagnosisDecisionV2(value)
        raise ValueError("decision must be an exact diagnosis decision")

    @field_validator("fault_mechanism", mode="before")
    @classmethod
    def parse_fault_mechanism(
        cls,
        value: object | None,
    ) -> UnifiedMechanismV2 | None:
        if value is None or isinstance(value, UnifiedMechanismV2):
            return value
        if type(value) is str:
            return UnifiedMechanismV2(value)
        raise ValueError("fault_mechanism must use the frozen mechanism allowlist")

    @field_validator("required_support_sources", mode="before")
    @classmethod
    def parse_support_sources(cls, value: object) -> tuple[EvidenceSource, ...]:
        values = cls.parse_json_arrays(value)
        parsed: list[EvidenceSource] = []
        for item in values:
            if isinstance(item, EvidenceSource):
                parsed.append(item)
            elif type(item) is str:
                parsed.append(EvidenceSource(item))
            else:
                raise ValueError("support sources must use the evidence-source allowlist")
        return tuple(parsed)

    @field_validator("root_service", "affected_sli", mode="before")
    @classmethod
    def require_safe_optional_claim(cls, value: object | None) -> str | None:
        if value is None:
            return None
        bounded = _trimmed(value, field_name="hidden truth claim")
        return _reject_executable_text(bounded, field_name="hidden truth claim")

    @field_validator(
        "causal_chain",
        "required_contradiction_handling",
        "required_missing_evidence",
    )
    @classmethod
    def require_safe_explanations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        safe: list[str] = []
        for value in values:
            bounded = _trimmed(
                value,
                field_name="hidden truth explanation",
                maximum=MAX_TEXT_ENTRY_LENGTH,
            )
            safe.append(
                _reject_executable_text(
                    bounded,
                    field_name="hidden truth explanation",
                )
            )
        return tuple(safe)

    @field_validator("required_support_sources")
    @classmethod
    def require_canonical_support_sources(
        cls,
        values: tuple[EvidenceSource, ...],
    ) -> tuple[EvidenceSource, ...]:
        if len(values) != len(set(values)):
            raise ValueError("support sources contain duplicates")
        if values != tuple(sorted(values, key=_SOURCE_ORDER.__getitem__)):
            raise ValueError("support sources must use canonical source order")
        return values

    @field_validator("difficult_subsets")
    @classmethod
    def require_unique_sorted_subsets(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("difficult subsets contain duplicates")
        if values != tuple(sorted(values)):
            raise ValueError("difficult subsets must use canonical order")
        return values

    @model_validator(mode="after")
    def require_decision_semantics(self) -> HiddenGroundTruthV1:
        if self.decision is DiagnosisDecisionV2.RCA_CONFIRMED:
            if not self.incident_confirmed:
                raise ValueError("confirmed truth requires a confirmed incident")
            if self.root_service is None or self.fault_mechanism is None:
                raise ValueError("confirmed truth requires one root and mechanism")
            if not self.causal_chain:
                raise ValueError("confirmed truth requires a causal chain")
            if self.affected_sli is None:
                raise ValueError("confirmed truth requires an affected SLI")
            if len(self.required_support_sources) < 2:
                raise ValueError("confirmed truth requires two support sources")
            if self.required_missing_evidence:
                raise ValueError("confirmed truth cannot retain evidence gaps")
        elif self.decision is DiagnosisDecisionV2.NEED_MORE_EVIDENCE:
            if not self.incident_confirmed:
                raise ValueError("need-more truth requires a confirmed incident anomaly")
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("need-more truth cannot claim a root or mechanism")
            if self.causal_chain:
                raise ValueError("need-more truth cannot claim a causal chain")
            if self.affected_sli is None:
                raise ValueError("need-more truth requires an affected SLI")
            if not self.required_support_sources:
                raise ValueError("need-more truth requires incident support")
            if not self.required_contradiction_handling:
                raise ValueError("need-more truth requires contradiction handling")
            if not self.required_missing_evidence:
                raise ValueError("need-more truth requires concrete missing evidence")
        else:
            if self.incident_confirmed:
                raise ValueError("abstain truth requires no confirmed incident")
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("abstain truth cannot claim a root or mechanism")
            if self.causal_chain or self.required_support_sources:
                raise ValueError("abstain truth cannot claim causal support")
            if self.required_missing_evidence:
                raise ValueError("abstain truth cannot retain evidence gaps")
            if not self.required_contradiction_handling:
                raise ValueError("abstain truth requires freshness or incident handling")
        if (
            self.write_disposition == "SAFE_REPLAY_REMEDIATION_CANDIDATE"
            and self.decision is not DiagnosisDecisionV2.RCA_CONFIRMED
        ):
            raise ValueError("safe replay remediation requires confirmed truth")
        return self
