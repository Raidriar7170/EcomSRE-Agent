"""Shared terminal admission for both DTA v2.2.6 acquisition policies."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictFloat, model_validator

from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222
from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    PredicateKindV22,
    ResourceSalientPayloadV22,
    SalientEvidenceMemoryV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)


class RealFaultTerminalKindV226(str, Enum):
    CPU_SATURATION = "CPU_SATURATION"
    NO_INCIDENT = "NO_INCIDENT"
    ABSTAIN = "ABSTAIN"


class RealFaultTerminalizationStateV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.terminalization-state.v1"]
    candidate_services: tuple[str, ...] = Field(min_length=2, max_length=4)
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    memory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gap_graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_covered_targets: tuple[str, ...]
    remaining_budget: StrictFloat = Field(ge=0)
    required_source_failures: tuple[EvidenceSourceV22, ...]
    budget_prevented_required_coverage: StrictBool
    conflicting_evidence: StrictBool
    truth_consulted: Literal[False]
    state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_state(self) -> RealFaultTerminalizationStateV226:
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("terminalization candidates are not canonical")
        if self.resource_covered_targets != tuple(
            sorted(set(self.resource_covered_targets))
        ):
            raise ValueError("terminalization resource coverage is not canonical")
        if not set(self.resource_covered_targets).issubset(self.candidate_services):
            raise ValueError("terminalization coverage is outside candidates")
        if self.required_source_failures != tuple(
            sorted(set(self.required_source_failures), key=list(EvidenceSourceV22).index)
        ):
            raise ValueError("terminalization source failures are not canonical")
        if self.state_sha256 != self.recompute_sha256():
            raise ValueError("terminalization state digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )


class RealFaultAdmittedTerminalV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.admitted-terminal.v1"]
    terminal_id: str = Field(pattern=r"^t:v226:[a-z-]+:[0-9a-f]{16}$")
    terminal_kind: RealFaultTerminalKindV226
    root_service_alias: str | None = Field(
        default=None, pattern=r"^svc-[0-9a-f]{10}$"
    )
    fault_domain: Literal["LOCAL_RESOURCE"] | None
    mechanism: Literal["CPU_SATURATION"] | None
    supporting_evidence_refs: tuple[str, ...]
    evidence_clause_valid: StrictBool
    admission_reason: Literal[
        "CPU_EFFECTIVE_SUPPORT",
        "EVIDENCE_CLOSED_NORMAL",
        "REQUIRED_SOURCE_FAILED",
        "BUDGET_INCOMPLETE",
        "CONFLICTING_EVIDENCE",
        "EVIDENCE_INCOMPLETE",
    ]
    runtime_admissible: Literal[True]
    terminal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_terminal(self) -> RealFaultAdmittedTerminalV226:
        if self.supporting_evidence_refs != tuple(
            sorted(set(self.supporting_evidence_refs))
        ):
            raise ValueError("terminal evidence refs are not canonical")
        if self.terminal_kind is RealFaultTerminalKindV226.CPU_SATURATION:
            if (
                self.root_service_alias is None
                or self.fault_domain != "LOCAL_RESOURCE"
                or self.mechanism != "CPU_SATURATION"
                or len(self.supporting_evidence_refs) < 2
                or not self.evidence_clause_valid
                or self.admission_reason != "CPU_EFFECTIVE_SUPPORT"
            ):
                raise ValueError("CPU terminal lacks effective support")
        elif self.terminal_kind is RealFaultTerminalKindV226.NO_INCIDENT:
            if (
                self.root_service_alias is not None
                or self.fault_domain is not None
                or self.mechanism is not None
                or not self.supporting_evidence_refs
                or not self.evidence_clause_valid
                or self.admission_reason != "EVIDENCE_CLOSED_NORMAL"
            ):
                raise ValueError("No-Incident terminal is not evidence-closed")
        elif (
            self.root_service_alias is not None
            or self.fault_domain is not None
            or self.mechanism is not None
            or self.supporting_evidence_refs
            or self.evidence_clause_valid
            or self.admission_reason
            not in {
                "REQUIRED_SOURCE_FAILED",
                "BUDGET_INCOMPLETE",
                "CONFLICTING_EVIDENCE",
                "EVIDENCE_INCOMPLETE",
            }
        ):
            raise ValueError("Abstain terminal carries an incident claim")
        identity = {
            "terminal_kind": self.terminal_kind.value,
            "root_service_alias": self.root_service_alias,
            "fault_domain": self.fault_domain,
            "mechanism": self.mechanism,
            "supporting_evidence_refs": self.supporting_evidence_refs,
            "admission_reason": self.admission_reason,
        }
        label = self.terminal_kind.value.casefold().replace("_", "-")
        if self.terminal_id != f"t:v226:{label}:{semantic_sha256_v22(identity)[:16]}":
            raise ValueError("terminal ID differs from admitted claim")
        if self.terminal_sha256 != self.recompute_sha256():
            raise ValueError("admitted terminal digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"terminal_sha256"})
        )


class RealFaultTerminalizationResultV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.terminalization-result.v1"]
    state: RealFaultTerminalizationStateV226
    terminal_candidates: tuple[RealFaultAdmittedTerminalV226, ...] = Field(
        max_length=4
    )
    shared_terminalizer: Literal[True]
    truth_consulted: Literal[False]
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_result(self) -> RealFaultTerminalizationResultV226:
        ids = tuple(item.terminal_id for item in self.terminal_candidates)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("terminal candidates are not canonical")
        if self.result_sha256 != self.recompute_sha256():
            raise ValueError("terminalization result digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )


def _state_v226(**values: object) -> RealFaultTerminalizationStateV226:
    payload = {
        "schema_version": "dta-v226-real-fault.terminalization-state.v1",
        **values,
        "truth_consulted": False,
    }
    draft = cast(Any, RealFaultTerminalizationStateV226).model_construct(
        **payload, state_sha256="0" * 64
    )
    return RealFaultTerminalizationStateV226.model_validate(
        {
            **payload,
            "state_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"state_sha256"})
            ),
        }
    )


def _terminal_v226(
    *,
    terminal_kind: RealFaultTerminalKindV226,
    root_service_alias: str | None,
    supporting_evidence_refs: tuple[str, ...],
    evidence_clause_valid: bool,
    admission_reason: str,
) -> RealFaultAdmittedTerminalV226:
    canonical_refs = tuple(sorted(set(supporting_evidence_refs)))
    identity = {
        "terminal_kind": terminal_kind.value,
        "root_service_alias": root_service_alias,
        "fault_domain": (
            "LOCAL_RESOURCE"
            if terminal_kind is RealFaultTerminalKindV226.CPU_SATURATION
            else None
        ),
        "mechanism": (
            "CPU_SATURATION"
            if terminal_kind is RealFaultTerminalKindV226.CPU_SATURATION
            else None
        ),
        "supporting_evidence_refs": canonical_refs,
        "admission_reason": admission_reason,
    }
    label = terminal_kind.value.casefold().replace("_", "-")
    payload = {
        "schema_version": "dta-v226-real-fault.admitted-terminal.v1",
        "terminal_id": f"t:v226:{label}:{semantic_sha256_v22(identity)[:16]}",
        "terminal_kind": terminal_kind,
        "root_service_alias": root_service_alias,
        "fault_domain": identity["fault_domain"],
        "mechanism": identity["mechanism"],
        "supporting_evidence_refs": canonical_refs,
        "admission_reason": admission_reason,
        "evidence_clause_valid": evidence_clause_valid,
        "runtime_admissible": True,
    }
    draft = cast(Any, RealFaultAdmittedTerminalV226).model_construct(
        **payload, terminal_sha256="0" * 64
    )
    return RealFaultAdmittedTerminalV226.model_validate(
        {
            **payload,
            "terminal_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"terminal_sha256"})
            ),
        }
    )


def _abstain(reason: str) -> RealFaultAdmittedTerminalV226:
    return _terminal_v226(
        terminal_kind=RealFaultTerminalKindV226.ABSTAIN,
        root_service_alias=None,
        supporting_evidence_refs=(),
        evidence_clause_valid=False,
        admission_reason=reason,
    )


def _memory_growth_effectively_strong_v226(
    *, service: str, baseline: BaselineProfileV22, memory: SalientEvidenceMemoryV22
) -> bool:
    fact = next(
        (
            item
            for item in memory.salient_facts
            if item.source is EvidenceSourceV22.RESOURCES
            and item.service == service
            and isinstance(item.payload, ResourceSalientPayloadV22)
        ),
        None,
    )
    baseline_stat = baseline.resource(service)
    if fact is None:
        return True
    payload = cast(ResourceSalientPayloadV22, fact.payload)
    current = payload.memory_slope_bytes_per_second
    if baseline_stat is None:
        return current >= 1.0
    baseline_slope = max(0.0, baseline_stat.memory_slope_bytes_per_second)
    return current >= max(1.0, baseline_slope * 2.0)


def terminalize_real_fault_v226(
    *,
    candidate_services: tuple[str, ...],
    baseline: BaselineProfileV22,
    memory: SalientEvidenceMemoryV22,
    gap_graph: GapGraphV222,
    resource_covered_targets: tuple[str, ...],
    remaining_budget: float,
    required_source_failures: tuple[EvidenceSourceV22, ...],
    budget_prevented_required_coverage: bool,
    conflicting_evidence: bool,
) -> RealFaultTerminalizationResultV226:
    if baseline.baseline_sha256 != memory.baseline_sha256:
        raise ValueError("terminalizer memory uses another baseline")
    if memory.memory_sha256 != gap_graph.memory_sha256:
        raise ValueError("terminalizer Gap Graph uses another memory")
    state = _state_v226(
        candidate_services=candidate_services,
        baseline_sha256=baseline.baseline_sha256,
        memory_sha256=memory.memory_sha256,
        gap_graph_sha256=gap_graph.graph_sha256,
        resource_covered_targets=resource_covered_targets,
        remaining_budget=float(remaining_budget),
        required_source_failures=tuple(
            sorted(set(required_source_failures), key=list(EvidenceSourceV22).index)
        ),
        budget_prevented_required_coverage=budget_prevented_required_coverage,
        conflicting_evidence=conflicting_evidence,
    )

    candidates: tuple[RealFaultAdmittedTerminalV226, ...]
    if required_source_failures:
        candidates = (_abstain("REQUIRED_SOURCE_FAILED"),)
    elif conflicting_evidence:
        candidates = (_abstain("CONFLICTING_EVIDENCE"),)
    else:
        cpu_terminals: list[RealFaultAdmittedTerminalV226] = []
        for service in candidate_services:
            cpu = next(
                (
                    item
                    for item in memory.predicates
                    if item.service == service
                    and item.predicate_kind is PredicateKindV22.RESOURCE_CPU_STRONG
                ),
                None,
            )
            healthy = next(
                (
                    item
                    for item in memory.predicates
                    if item.service == service
                    and item.predicate_kind is PredicateKindV22.RUNTIME_HEALTHY
                ),
                None,
            )
            if cpu is not None and healthy is not None:
                cpu_terminals.append(
                    _terminal_v226(
                        terminal_kind=RealFaultTerminalKindV226.CPU_SATURATION,
                        root_service_alias=service,
                        supporting_evidence_refs=(
                            *cpu.evidence_refs,
                            *healthy.evidence_refs,
                        ),
                        evidence_clause_valid=True,
                        admission_reason="CPU_EFFECTIVE_SUPPORT",
                    )
                )
        if cpu_terminals:
            candidates = tuple(sorted(cpu_terminals, key=lambda item: item.terminal_id))
        elif budget_prevented_required_coverage:
            candidates = (_abstain("BUDGET_INCOMPLETE"),)
        else:
            runtime_healthy = {
                item.service: item
                for item in memory.predicates
                if item.predicate_kind is PredicateKindV22.RUNTIME_HEALTHY
                and item.service in set(candidate_services)
            }
            resource_refs = {
                service: tuple(
                    sorted(
                        {
                            ref
                            for fact in memory.salient_facts
                            if fact.source is EvidenceSourceV22.RESOURCES
                            and fact.service == service
                            for ref in fact.evidence_refs
                        }
                    )
                )
                for service in candidate_services
            }
            incident_predicates = tuple(
                item
                for item in memory.predicates
                if item.service in set(candidate_services)
                and item.predicate_kind is not PredicateKindV22.RUNTIME_HEALTHY
                and (
                    item.predicate_kind
                    is not PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG
                    or _memory_growth_effectively_strong_v226(
                        service=item.service,
                        baseline=baseline,
                        memory=memory,
                    )
                )
            )
            no_incident_ready = (
                set(runtime_healthy) == set(candidate_services)
                and set(resource_covered_targets) == set(candidate_services)
                and all(resource_refs.values())
                and not incident_predicates
            )
            if no_incident_ready:
                supporting_refs = tuple(
                    sorted(
                        {
                            ref
                            for service in candidate_services
                            for ref in (
                                *runtime_healthy[service].evidence_refs,
                                *resource_refs[service],
                            )
                        }
                    )
                )
                candidates = (
                    _terminal_v226(
                        terminal_kind=RealFaultTerminalKindV226.NO_INCIDENT,
                        root_service_alias=None,
                        supporting_evidence_refs=supporting_refs,
                        evidence_clause_valid=True,
                        admission_reason="EVIDENCE_CLOSED_NORMAL",
                    ),
                )
            elif remaining_budget <= 0:
                candidates = (_abstain("EVIDENCE_INCOMPLETE"),)
            else:
                candidates = ()

    ordered = tuple(sorted(candidates, key=lambda item: item.terminal_id))
    payload = {
        "schema_version": "dta-v226-real-fault.terminalization-result.v1",
        "state": state,
        "terminal_candidates": ordered,
        "shared_terminalizer": True,
        "truth_consulted": False,
    }
    draft = cast(Any, RealFaultTerminalizationResultV226).model_construct(
        **payload, result_sha256="0" * 64
    )
    return RealFaultTerminalizationResultV226.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )


__all__ = (
    "RealFaultAdmittedTerminalV226",
    "RealFaultTerminalKindV226",
    "RealFaultTerminalizationResultV226",
    "RealFaultTerminalizationStateV226",
    "terminalize_real_fault_v226",
)
