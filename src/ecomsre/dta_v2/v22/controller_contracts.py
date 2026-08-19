"""Runtime-owned controller contracts for DTA v2.2 Planner-Lite."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, ValidationInfo, model_validator

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22
from ecomsre.dta_v2.v22.diagnosis import FaultDomainV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    LogicalServiceV22,
    Sha256V22,
    semantic_sha256_v22,
)


NO_ACTION_ID_V22 = "NONE"
NO_INCIDENT_HYPOTHESIS_ID_V22 = "h:none:no-incident"
ABSTAIN_HYPOTHESIS_ID_V22 = "h:none:unresolved"


class ControllerDecisionKindV22(str, Enum):
    READ = "READ"
    COMMIT = "COMMIT"
    NO_INCIDENT = "NO_INCIDENT"
    ABSTAIN = "ABSTAIN"


class BeliefStatusV22(str, Enum):
    UNTESTED = "UNTESTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"


class HypothesisCatalogEntryV22(DtaModelV22):
    hypothesis_id: str = Field(pattern=r"^h:[a-z0-9-]+:[a-z0-9-]+$")
    target_service: LogicalServiceV22 | None
    fault_domain: FaultDomainV22
    mechanism: MechanismV22


_INCIDENT_ONTOLOGY_V22: tuple[
    tuple[str, FaultDomainV22, MechanismV22], ...
] = (
    (
        "configuration-error",
        FaultDomainV22.CONFIGURATION,
        MechanismV22.CONFIGURATION_ERROR,
    ),
    (
        "service-unavailable",
        FaultDomainV22.RUNTIME,
        MechanismV22.SERVICE_UNAVAILABLE,
    ),
    ("memory-leak", FaultDomainV22.RESOURCE, MechanismV22.MEMORY_LEAK),
    ("cpu-saturation", FaultDomainV22.RESOURCE, MechanismV22.CPU_SATURATION),
    (
        "dependency-latency",
        FaultDomainV22.DEPENDENCY,
        MechanismV22.DEPENDENCY_LATENCY,
    ),
)


def _canonical_hypotheses_v22(
    candidate_services: tuple[str, ...],
) -> tuple[HypothesisCatalogEntryV22, ...]:
    entries = tuple(
        HypothesisCatalogEntryV22(
            hypothesis_id=f"h:{service}:{suffix}",
            target_service=service,
            fault_domain=domain,
            mechanism=mechanism,
        )
        for service in candidate_services
        for suffix, domain, mechanism in _INCIDENT_ONTOLOGY_V22
    )
    return (
        *entries,
        HypothesisCatalogEntryV22(
            hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
            target_service=None,
            fault_domain=FaultDomainV22.NO_INCIDENT,
            mechanism=MechanismV22.NO_INCIDENT,
        ),
        HypothesisCatalogEntryV22(
            hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
            target_service=None,
            fault_domain=FaultDomainV22.UNKNOWN,
            mechanism=MechanismV22.UNKNOWN,
        ),
    )


class HypothesisCatalogV22(DtaModelV22):
    schema_version: Literal["dta-v22.hypothesis-catalog.v1"]
    candidate_services: tuple[LogicalServiceV22, ...] = Field(min_length=1, max_length=4)
    ontology_version: Literal["dta-v22.closed-fault-ontology.v1"]
    hypotheses: tuple[HypothesisCatalogEntryV22, ...] = Field(
        min_length=7,
        max_length=22,
    )
    catalog_sha256: Sha256V22

    @model_validator(mode="after")
    def require_catalog(self) -> HypothesisCatalogV22:
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("hypothesis candidates are not canonical")
        if self.hypotheses != _canonical_hypotheses_v22(self.candidate_services):
            raise ValueError("hypothesis catalog differs from closed ontology")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"catalog_sha256"})
        )
        if self.catalog_sha256 != expected:
            raise ValueError("hypothesis catalog digest differs")
        return self

    def require(self, hypothesis_id: str) -> HypothesisCatalogEntryV22:
        selected = next(
            (item for item in self.hypotheses if item.hypothesis_id == hypothesis_id),
            None,
        )
        if selected is None:
            raise ValueError("working hypothesis is outside the closed catalog")
        return selected


def build_hypothesis_catalog_v22(
    *, candidate_services: tuple[str, ...]
) -> HypothesisCatalogV22:
    candidates = tuple(sorted(item.strip() for item in candidate_services))
    if not 1 <= len(candidates) <= 4 or candidates != tuple(sorted(set(candidates))):
        raise ValueError("hypothesis candidates require one to four unique services")
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.hypothesis-catalog.v1",
        "candidate_services": candidates,
        "ontology_version": "dta-v22.closed-fault-ontology.v1",
        "hypotheses": _canonical_hypotheses_v22(candidates),
    }
    draft = HypothesisCatalogV22.model_construct(
        **payload,
        catalog_sha256="0" * 64,
    )
    return HypothesisCatalogV22.model_validate(
        {
            **payload,
            "catalog_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"catalog_sha256"})
            ),
        }
    )


class ControllerDecisionV22(DtaModelV22):
    """The complete model-owned output; runtime metadata is deliberately absent."""

    decision: ControllerDecisionKindV22
    working_hypothesis_id: str = Field(pattern=r"^h:[a-z0-9-]+:[a-z0-9-]+$")
    action_id: str = Field(pattern=r"^(?:NONE|a:[a-z0-9][a-z0-9:+-]*)$")
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=40)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=40)

    @model_validator(mode="after")
    def require_shape(self) -> ControllerDecisionV22:
        for values in (
            self.supporting_evidence_refs,
            self.contradicting_evidence_refs,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("controller evidence refs are not canonical")
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("controller support and contradiction refs overlap")
        if self.decision is ControllerDecisionKindV22.READ:
            if self.action_id == NO_ACTION_ID_V22:
                raise ValueError("READ requires an action")
        elif self.action_id != NO_ACTION_ID_V22:
            raise ValueError("non-READ decision requires the NONE action sentinel")
        if (
            self.decision is ControllerDecisionKindV22.NO_INCIDENT
            and self.working_hypothesis_id != NO_INCIDENT_HYPOTHESIS_ID_V22
        ):
            raise ValueError("No-Incident sentinel hypothesis differs")
        if (
            self.decision is ControllerDecisionKindV22.ABSTAIN
            and self.working_hypothesis_id != ABSTAIN_HYPOTHESIS_ID_V22
        ):
            raise ValueError("Abstain sentinel hypothesis differs")
        if self.decision is ControllerDecisionKindV22.COMMIT:
            if self.working_hypothesis_id in {
                NO_INCIDENT_HYPOTHESIS_ID_V22,
                ABSTAIN_HYPOTHESIS_ID_V22,
            }:
                raise ValueError("COMMIT requires an incident hypothesis")
            if not self.supporting_evidence_refs:
                raise ValueError("COMMIT requires supporting evidence refs")
        return self


class BeliefTurnRecordV22(DtaModelV22):
    schema_version: Literal["dta-v22.belief-turn-record.v1"]
    turn_ordinal: StrictInt = Field(ge=1, le=6)
    decision: ControllerDecisionKindV22
    working_hypothesis_id: str
    action_id: str
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]
    executed_coverage_keys: tuple[str, ...]
    record_sha256: Sha256V22

    @model_validator(mode="after")
    def require_record(self) -> BeliefTurnRecordV22:
        for values in (
            self.supporting_evidence_refs,
            self.contradicting_evidence_refs,
            self.executed_coverage_keys,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("belief turn values are not canonical")
        if (self.decision is ControllerDecisionKindV22.READ) != bool(
            self.executed_coverage_keys
        ):
            raise ValueError("belief READ coverage differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"record_sha256"})
        )
        if self.record_sha256 != expected:
            raise ValueError("belief turn digest differs")
        return self


def _turn_record_v22(
    *,
    turn_ordinal: int,
    decision: ControllerDecisionV22,
    coverage_keys: tuple[str, ...],
) -> BeliefTurnRecordV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.belief-turn-record.v1",
        "turn_ordinal": turn_ordinal,
        "decision": decision.decision,
        "working_hypothesis_id": decision.working_hypothesis_id,
        "action_id": decision.action_id,
        "supporting_evidence_refs": decision.supporting_evidence_refs,
        "contradicting_evidence_refs": decision.contradicting_evidence_refs,
        "executed_coverage_keys": coverage_keys,
    }
    return BeliefTurnRecordV22.model_validate(
        {**payload, "record_sha256": semantic_sha256_v22(payload)}
    )


class BeliefLedgerV22(DtaModelV22):
    schema_version: Literal["dta-v22.belief-ledger.v1"]
    hypothesis_catalog_sha256: Sha256V22
    current_working_hypothesis_id: str | None
    selected_hypothesis_ids: tuple[str, ...]
    executed_action_ids: tuple[str, ...]
    covered_capability_keys: tuple[str, ...]
    correction_used: StrictBool
    correction_error_code: str | None
    turn_records: tuple[BeliefTurnRecordV22, ...] = Field(max_length=6)
    ledger_sha256: Sha256V22

    @model_validator(mode="after")
    def require_ledger(self) -> BeliefLedgerV22:
        if tuple(item.turn_ordinal for item in self.turn_records) != tuple(
            range(1, len(self.turn_records) + 1)
        ):
            raise ValueError("belief turn ordinals are not contiguous")
        selected = tuple(
            sorted({item.working_hypothesis_id for item in self.turn_records})
        )
        executed = tuple(
            sorted(
                item.action_id
                for item in self.turn_records
                if item.decision is ControllerDecisionKindV22.READ
            )
        )
        covered = tuple(
            sorted(
                {
                    key
                    for item in self.turn_records
                    for key in item.executed_coverage_keys
                }
            )
        )
        current = (
            self.turn_records[-1].working_hypothesis_id
            if self.turn_records
            else None
        )
        if (
            self.current_working_hypothesis_id != current
            or self.selected_hypothesis_ids != selected
            or self.executed_action_ids != executed
            or self.covered_capability_keys != covered
            or self.correction_used != (self.correction_error_code is not None)
        ):
            raise ValueError("belief ledger differs from derived turn state")
        if len(self.executed_action_ids) != len(set(self.executed_action_ids)):
            raise ValueError("belief ledger repeats an executed action")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("belief ledger digest differs")
        return self


def _build_ledger_v22(
    *,
    hypothesis_catalog_sha256: str,
    turn_records: tuple[BeliefTurnRecordV22, ...],
    correction_error_code: str | None,
) -> BeliefLedgerV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.belief-ledger.v1",
        "hypothesis_catalog_sha256": hypothesis_catalog_sha256,
        "current_working_hypothesis_id": (
            turn_records[-1].working_hypothesis_id if turn_records else None
        ),
        "selected_hypothesis_ids": tuple(
            sorted({item.working_hypothesis_id for item in turn_records})
        ),
        "executed_action_ids": tuple(
            sorted(
                item.action_id
                for item in turn_records
                if item.decision is ControllerDecisionKindV22.READ
            )
        ),
        "covered_capability_keys": tuple(
            sorted(
                {
                    key
                    for item in turn_records
                    for key in item.executed_coverage_keys
                }
            )
        ),
        "correction_used": correction_error_code is not None,
        "correction_error_code": correction_error_code,
        "turn_records": turn_records,
    }
    draft = BeliefLedgerV22.model_construct(
        **payload,
        ledger_sha256="0" * 64,
    )
    return BeliefLedgerV22.model_validate(
        {
            **payload,
            "ledger_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"ledger_sha256"})
            ),
        }
    )


def initialize_belief_ledger_v22(*, catalog: HypothesisCatalogV22) -> BeliefLedgerV22:
    catalog = HypothesisCatalogV22.model_validate(catalog.model_dump(mode="python"))
    return _build_ledger_v22(
        hypothesis_catalog_sha256=catalog.catalog_sha256,
        turn_records=(),
        correction_error_code=None,
    )


def record_belief_turn_v22(
    *,
    ledger: BeliefLedgerV22,
    hypothesis_catalog: HypothesisCatalogV22,
    action_catalog: ActionCatalogV22,
    decision: ControllerDecisionV22,
    known_evidence_refs: tuple[str, ...],
) -> BeliefLedgerV22:
    hypothesis_catalog = HypothesisCatalogV22.model_validate(
        hypothesis_catalog.model_dump(mode="python")
    )
    ledger = BeliefLedgerV22.model_validate(ledger.model_dump(mode="python"))
    action_catalog = ActionCatalogV22.model_validate(
        action_catalog.model_dump(mode="python")
    )
    decision = ControllerDecisionV22.model_validate(decision.model_dump(mode="python"))
    if ledger.hypothesis_catalog_sha256 != hypothesis_catalog.catalog_sha256:
        raise ValueError("belief ledger hypothesis catalog binding differs")
    hypothesis_catalog.require(decision.working_hypothesis_id)
    known = set(known_evidence_refs)
    observed = set(decision.supporting_evidence_refs) | set(
        decision.contradicting_evidence_refs
    )
    if not observed.issubset(known):
        raise ValueError("controller evidence ref is outside current memory")
    coverage: tuple[str, ...] = ()
    if decision.decision is ControllerDecisionKindV22.READ:
        if decision.action_id in set(ledger.executed_action_ids):
            raise ValueError("controller action was already executed")
        action = next(
            (
                item
                for item in action_catalog.actions
                if item.action_id == decision.action_id
            ),
            None,
        )
        if action is None:
            raise ValueError("controller action is not available in the current catalog")
        coverage = action.coverage_keys
    record = _turn_record_v22(
        turn_ordinal=len(ledger.turn_records) + 1,
        decision=decision,
        coverage_keys=coverage,
    )
    return _build_ledger_v22(
        hypothesis_catalog_sha256=hypothesis_catalog.catalog_sha256,
        turn_records=(*ledger.turn_records, record),
        correction_error_code=ledger.correction_error_code,
    )


class HypothesisBeliefV22(DtaModelV22):
    hypothesis_id: str
    status: BeliefStatusV22
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]


class BeliefLedgerViewV22(DtaModelV22):
    schema_version: Literal["dta-v22.belief-ledger-view.v1"]
    hypothesis_catalog_sha256: Sha256V22
    source_ledger_sha256: Sha256V22
    current_working_hypothesis_id: str | None
    hypotheses: tuple[HypothesisBeliefV22, ...] = Field(min_length=7, max_length=22)
    executed_action_ids: tuple[str, ...]
    covered_capability_keys: tuple[str, ...]
    correction_used: StrictBool
    view_sha256: Sha256V22

    @model_validator(mode="after")
    def require_view(self, info: ValidationInfo) -> BeliefLedgerViewV22:
        context = info.context if isinstance(info.context, dict) else None
        if (
            context is None
            or not isinstance(context.get("ledger"), BeliefLedgerV22)
            or not isinstance(context.get("catalog"), HypothesisCatalogV22)
        ):
            raise ValueError("belief view requires authoritative runtime provenance")
        ledger = BeliefLedgerV22.model_validate(
            context["ledger"].model_dump(mode="python")
        )
        catalog = HypothesisCatalogV22.model_validate(
            context["catalog"].model_dump(mode="python")
        )
        expected_payload = _belief_view_payload_v22(ledger=ledger, catalog=catalog)
        expected_draft = BeliefLedgerViewV22.model_construct(
            **expected_payload,
            view_sha256="0" * 64,
        )
        if self.model_dump(mode="json", exclude={"view_sha256"}) != (
            expected_draft.model_dump(mode="json", exclude={"view_sha256"})
        ):
            raise ValueError("belief view differs from authoritative runtime ledger")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"view_sha256"})
        )
        if self.view_sha256 != expected:
            raise ValueError("belief view digest differs")
        return self


def _belief_view_payload_v22(
    *,
    ledger: BeliefLedgerV22,
    catalog: HypothesisCatalogV22,
) -> dict[str, Any]:
    if ledger.hypothesis_catalog_sha256 != catalog.catalog_sha256:
        raise ValueError("belief view catalog differs from ledger")
    beliefs: list[HypothesisBeliefV22] = []
    for hypothesis in catalog.hypotheses:
        records = tuple(
            item
            for item in ledger.turn_records
            if item.working_hypothesis_id == hypothesis.hypothesis_id
        )
        support = tuple(
            sorted({ref for item in records for ref in item.supporting_evidence_refs})
        )
        contradict = tuple(
            sorted({ref for item in records for ref in item.contradicting_evidence_refs})
        )
        if contradict:
            status = BeliefStatusV22.CONTRADICTED
        elif any(
            item.decision
            in {ControllerDecisionKindV22.COMMIT, ControllerDecisionKindV22.NO_INCIDENT}
            for item in records
        ):
            status = BeliefStatusV22.SUPPORTED
        elif support:
            status = BeliefStatusV22.PARTIALLY_SUPPORTED
        else:
            status = BeliefStatusV22.UNTESTED
        beliefs.append(
            HypothesisBeliefV22(
                hypothesis_id=hypothesis.hypothesis_id,
                status=status,
                supporting_evidence_refs=support,
                contradicting_evidence_refs=contradict,
            )
        )
    return {
        "schema_version": "dta-v22.belief-ledger-view.v1",
        "hypothesis_catalog_sha256": catalog.catalog_sha256,
        "source_ledger_sha256": ledger.ledger_sha256,
        "current_working_hypothesis_id": ledger.current_working_hypothesis_id,
        "hypotheses": tuple(beliefs),
        "executed_action_ids": ledger.executed_action_ids,
        "covered_capability_keys": ledger.covered_capability_keys,
        "correction_used": ledger.correction_used,
    }


def build_belief_ledger_view_v22(
    *,
    ledger: BeliefLedgerV22,
    hypothesis_catalog: HypothesisCatalogV22,
) -> BeliefLedgerViewV22:
    payload = _belief_view_payload_v22(
        ledger=ledger,
        catalog=hypothesis_catalog,
    )
    draft = BeliefLedgerViewV22.model_construct(
        **payload,
        view_sha256="0" * 64,
    )
    return BeliefLedgerViewV22.model_validate(
        {
            **payload,
            "view_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"view_sha256"})
            ),
        },
        context={"ledger": ledger, "catalog": hypothesis_catalog},
    )


__all__ = (
    "ABSTAIN_HYPOTHESIS_ID_V22",
    "NO_ACTION_ID_V22",
    "NO_INCIDENT_HYPOTHESIS_ID_V22",
    "BeliefLedgerV22",
    "BeliefLedgerViewV22",
    "BeliefStatusV22",
    "BeliefTurnRecordV22",
    "ControllerDecisionKindV22",
    "ControllerDecisionV22",
    "HypothesisBeliefV22",
    "HypothesisCatalogEntryV22",
    "HypothesisCatalogV22",
    "build_belief_ledger_view_v22",
    "build_hypothesis_catalog_v22",
    "initialize_belief_ledger_v22",
    "record_belief_turn_v22",
)
