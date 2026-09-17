"""Model proposals are untrusted; runtime owns identity and authority."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.product.connectors.base import ConnectorWindowV1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class InvestigationConfig(StrictModel):
    enabled: bool = False
    max_hypotheses: int = Field(default=4, ge=1, le=4)
    max_evidence_reads: int = Field(default=8, ge=1, le=8)
    max_provider_calls: int = Field(default=10, ge=1, le=10)
    max_no_progress_turns: int = Field(default=2, ge=1, le=2)
    schema_repair_budget: int = Field(default=1, ge=0, le=1)
    reasoning_effort: Literal["medium", "high"] = "medium"


class PriceSchedule(StrictModel):
    """Operator-supplied dated upper rates, including gateway surcharges."""

    provider_profile: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    accepted_response_models: tuple[str, ...] = Field(default=(), max_length=8)
    as_of: date
    source: str = Field(min_length=1, max_length=500)
    input_usd_per_million: float = Field(gt=0, le=1000)
    output_usd_per_million: float = Field(gt=0, le=1000)


class PredictionTest(StrictModel):
    """A model-selected numeric test; its outcome is computed only by Runtime."""

    field: Literal["cpu_percent", "memory_bytes"]
    operator: Literal["mean", "max", "delta", "rate"]
    comparator: Literal["gt", "ge", "lt", "le"]
    threshold: float = Field(allow_inf_nan=False)
    window_seconds: int = Field(ge=1, le=30)
    minimum_samples: int = Field(ge=2, le=10)


class HypothesisProposal(StrictModel):
    # null creates a hypothesis; updates must reference a runtime-assigned ID.
    hypothesis_id: str | None
    mechanism: str = Field(min_length=1, max_length=400)
    target: str = Field(min_length=1, max_length=100)
    support: list[str] = Field(max_length=12)
    against: list[str] = Field(max_length=12)
    missing_observations: list[str] = Field(max_length=4)
    falsifiable_prediction: str = Field(min_length=1, max_length=400)
    claim_window: ConnectorWindowV1
    prediction_test: PredictionTest | None = None


class InvestigationDecision(StrictModel):
    kind: Literal["READ", "UPDATE_HYPOTHESES", "CONCLUDE", "ABSTAIN"]
    action_id: str | None
    hypotheses: list[HypothesisProposal] = Field(max_length=4)
    rationale: str = Field(min_length=1, max_length=600)
    result: Literal["PROVISIONAL_SUPPORTED", "UNRESOLVED", "OBSERVABILITY_GAP"] | None
    unresolved_alternatives: list[str] = Field(max_length=4)

    @model_validator(mode="after")
    def shape(self):
        if (self.kind == "READ") != (self.action_id is not None):
            raise ValueError("READ requires exactly one catalog action")
        terminal = self.kind in {"CONCLUDE", "ABSTAIN"}
        if terminal != (self.result is not None):
            raise ValueError("terminal result shape differs")
        if self.kind == "ABSTAIN" and self.result == "PROVISIONAL_SUPPORTED":
            raise ValueError("abstention cannot claim support")
        return self
