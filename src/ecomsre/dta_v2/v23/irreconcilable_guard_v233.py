"""Witness-closed irreconcilable guard for DTA v2.3.3."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.contradiction_witness_v233 import (
    ContradictionWitnessV233,
    WitnessStrengthV233,
)


class IrreconcilableGuardDispositionV233(str, Enum):
    OPEN = "OPEN"
    RESOLVABLE = "RESOLVABLE"
    IRRECONCILABLE = "IRRECONCILABLE"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"


class IrreconcilableGuardDecisionV233(DtaModelV22):
    schema_version: Literal["dta-v233.irreconcilable-guard-decision.v1"]
    disposition: IrreconcilableGuardDispositionV233
    witnesses: tuple[ContradictionWitnessV233, ...]
    blocking_witness_ids: tuple[str, ...]
    resolvable_sources: tuple[EvidenceSourceV22, ...]
    required_additional_reads: tuple[EvidenceSourceV22, ...]
    reason_codes: tuple[str, ...] = Field(min_length=1)
    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_decision(self) -> "IrreconcilableGuardDecisionV233":
        ids = tuple(item.witness_id for item in self.witnesses)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.3 guard witnesses are not canonical")
        if self.blocking_witness_ids != tuple(
            sorted(set(self.blocking_witness_ids))
        ):
            raise ValueError("v2.3.3 guard blocking witnesses are not canonical")
        if not set(self.blocking_witness_ids).issubset(ids):
            raise ValueError("v2.3.3 guard blocks on an unknown witness")
        for values, label in (
            (self.resolvable_sources, "resolvable sources"),
            (self.required_additional_reads, "required reads"),
        ):
            if values != tuple(sorted(set(values), key=lambda item: item.value)):
                raise ValueError(f"v2.3.3 guard {label} are not canonical")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("v2.3.3 guard reasons are not canonical")
        if self.disposition is IrreconcilableGuardDispositionV233.OPEN and self.witnesses:
            raise ValueError("open v2.3.3 guard carries witnesses")
        if self.disposition is IrreconcilableGuardDispositionV233.RESOLVABLE:
            if not self.blocking_witness_ids or len(self.required_additional_reads) != 1:
                raise ValueError("resolvable v2.3.3 guard lacks one deciding read")
        if self.disposition is IrreconcilableGuardDispositionV233.IRRECONCILABLE:
            if not self.blocking_witness_ids or self.required_additional_reads:
                raise ValueError("irreconcilable v2.3.3 guard is not closed")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("v2.3.3 guard decision digest differs")
        return self


def evaluate_irreconcilable_guard_v233(
    *,
    witnesses: tuple[ContradictionWitnessV233, ...],
    legal_sources: tuple[EvidenceSourceV22, ...],
    remaining_reads: int,
    guard_read_used: bool,
) -> IrreconcilableGuardDecisionV233:
    """Close only on typed strong witnesses; charge at most one resolving read."""

    if not 0 <= remaining_reads <= 3:
        raise ValueError("v2.3.3 guard remaining reads are outside the shared budget")
    if legal_sources != tuple(
        sorted(set(legal_sources), key=lambda item: item.value)
    ):
        raise ValueError("v2.3.3 guard legal sources are not canonical")
    canonical = tuple(sorted(witnesses, key=lambda item: item.witness_id))
    strong = tuple(
        item
        for item in canonical
        if item.strength is WitnessStrengthV233.STRONG
        and item.coverage_satisfied
    )
    blocking = tuple(item.witness_id for item in strong)
    resolvable = tuple(
        sorted(
            {
                source
                for item in strong
                for source in item.resolvable_sources
                if source in set(legal_sources)
            },
            key=lambda item: item.value,
        )
    )
    required: tuple[EvidenceSourceV22, ...] = ()
    reasons: tuple[str, ...]
    if not canonical:
        disposition = IrreconcilableGuardDispositionV233.OPEN
        reasons = ("NO_CONTRADICTION_WITNESS",)
    elif not strong:
        disposition = IrreconcilableGuardDispositionV233.INSUFFICIENT_COVERAGE
        reasons = ("APPARENT_CONTRADICTION_LACKS_CLOSED_COVERAGE",)
    elif resolvable and remaining_reads > 0 and not guard_read_used:
        disposition = IrreconcilableGuardDispositionV233.RESOLVABLE
        required = (resolvable[0],)
        reasons = (
            "ONE_LEGAL_GUARD_READ_CAN_TEST_THE_WITNESS",
            "SHARED_DISCOVERY_BUDGET_CHARGED",
        )
    else:
        disposition = IrreconcilableGuardDispositionV233.IRRECONCILABLE
        reasons = (
            "STRONG_WITNESS_REMAINS_CLOSED",
            *(
                ("ALLOWED_GUARD_READ_DID_NOT_RESOLVE",)
                if guard_read_used
                else ("NO_LEGAL_BOUNDED_READ_CAN_RESOLVE",)
            ),
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.irreconcilable-guard-decision.v1",
        "disposition": disposition,
        "witnesses": canonical,
        "blocking_witness_ids": blocking,
        "resolvable_sources": resolvable,
        "required_additional_reads": required,
        "reason_codes": tuple(sorted(set(reasons))),
    }
    draft = IrreconcilableGuardDecisionV233.model_construct(
        **payload,
        decision_sha256="0" * 64,
    )
    return IrreconcilableGuardDecisionV233.model_validate(
        {
            **payload,
            "decision_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"decision_sha256"})
            ),
        }
    )


__all__ = (
    "IrreconcilableGuardDecisionV233",
    "IrreconcilableGuardDispositionV233",
    "evaluate_irreconcilable_guard_v233",
)
