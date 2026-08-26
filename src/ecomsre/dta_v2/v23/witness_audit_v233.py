"""Development-only witness and guard audit over the frozen v2.3.2 set."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22, build_memory_views_v22
from ecomsre.dta_v2.v22.practical_runner import _baseline
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v23.contradiction_witness_v233 import (
    ContradictionWitnessV233,
    WitnessStrengthV233,
    build_contradiction_witnesses_v233,
)
from ecomsre.dta_v2.v23.evaluation import _build_common_context_v23
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    AdmissionStratumV232,
    load_evaluation_cases_v232,
    load_evaluation_truth_index_v232,
    load_evaluation_truth_shard_v232,
    load_evaluation_views_v232,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCaseSpecV231,
    EvaluationOntologyViewSpecV231,
    _residual_graph_v231,
    materialize_evaluation_case_v231,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDecisionV233,
    IrreconcilableGuardDispositionV233,
    evaluate_irreconcilable_guard_v233,
)


class WitnessAuditReadReasonV233(str, Enum):
    LOCALIZE_EXCLUSIVE_ROOTS = "LOCALIZE_EXCLUSIVE_ROOTS"
    TEST_WITNESS_CLOSURE = "TEST_WITNESS_CLOSURE"


class WitnessAuditReadV233(DtaModelV22):
    source: EvidenceSourceV22
    target_services: tuple[str, ...]
    reason: WitnessAuditReadReasonV233
    guard_directed: StrictBool


class WitnessAuditEntryV233(DtaModelV22):
    case_id: str
    stratum: AdmissionStratumV232
    decision: IrreconcilableGuardDecisionV233
    witnesses: tuple[ContradictionWitnessV233, ...]
    reads: tuple[WitnessAuditReadV233, ...]
    guard_read_used: StrictBool
    report_generated: Literal[False]
    registered_known_unchanged: StrictBool
    no_incident_unchanged: StrictBool


class WitnessAuditV233(DtaModelV22):
    schema_version: Literal["dta-v233.witness-audit.v1"]
    development_set: Literal["dta-v232-fixed-24-case"]
    case_count: Literal[24]
    irreconcilable_controls_blocked: StrictInt = Field(ge=0, le=3)
    novelty_cases_blocked: StrictInt = Field(ge=0, le=14)
    registered_known_unchanged: StrictInt = Field(ge=0, le=4)
    no_incident_unchanged: StrictInt = Field(ge=0, le=3)
    maximum_discovery_reads: StrictInt = Field(ge=0, le=3)
    guard_directed_reads: StrictInt = Field(ge=0)
    provider_calls: Literal[0]
    witness_iteration_count: Literal[1]
    entries: tuple[WitnessAuditEntryV233, ...] = Field(min_length=24, max_length=24)
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_audit(self) -> "WitnessAuditV233":
        ids = tuple(item.case_id for item in self.entries)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.3 witness audit cases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"audit_sha256"})
        )
        if self.audit_sha256 != expected:
            raise ValueError("v2.3.3 witness audit digest differs")
        return self


def _action(
    *,
    context: Any,
    source: EvidenceSourceV22,
    targets: tuple[str, ...],
    outcomes: tuple[object, ...],
) -> EvidenceActionV22 | None:
    executed = {str(getattr(item, "action_id")) for item in outcomes}
    actions = {
        item.target_services[0]: item
        for item in context.catalog.registry_actions
        if item.source is source
        and len(item.target_services) == 1
        and item.action_id not in executed
    }
    return next((actions[target] for target in targets if target in actions), None)


def _execute(
    *,
    action: EvidenceActionV22,
    backend: QuerySpecificReplayBackendV22,
    outcomes: tuple[object, ...],
    context: Any,
) -> tuple[tuple[object, ...], SalientEvidenceMemoryV22]:
    updated = (*outcomes, backend.execute(action))
    memory, _ = build_memory_views_v22(
        outcomes=updated,  # type: ignore[arg-type]
        baseline=_baseline(context.case),
        observed_at=context.case.capture.captured_at,
        top_k=64,
    )
    return updated, memory


def _legal_sources(context: Any, outcomes: tuple[object, ...]) -> tuple[EvidenceSourceV22, ...]:
    executed = {str(getattr(item, "action_id")) for item in outcomes}
    return tuple(
        sorted(
            {
                item.source
                for item in context.catalog.registry_actions
                if item.action_id not in executed and item.weighted_cost <= 3.0
            },
            key=lambda item: item.value,
        )
    )


def audit_case_witness_v233(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV231,
    view_spec: EvaluationOntologyViewSpecV231,
    stratum: AdmissionStratumV232,
) -> WitnessAuditEntryV233:
    case = materialize_evaluation_case_v231(
        repository_root=repository_root,
        spec=spec,
    )
    context = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    outcomes: tuple[object, ...] = tuple(context.outcomes)
    memory = context.memory
    backend = QuerySpecificReplayBackendV22(case.capture)
    reads: list[WitnessAuditReadV233] = []
    graph = _residual_graph_v231(context=context, memory=memory)
    witnesses = build_contradiction_witnesses_v233(
        graph=graph,
        memory=memory,
        observation_scope=spec.case_id,
    )

    # Multiple services are not themselves contradictory. When two distinct
    # strong error surfaces remain root-ambiguous, one normal discovery read
    # may localize typed trace claims before the guard is evaluated.
    error_services = tuple(
        sorted(
            {
                item.service
                for item in graph.generic_anomalies
                if item.kind is GenericAnomalyKindV23.METRIC_ERROR_OUTLIER
            }
        )
    )
    if not any(item.strength is WitnessStrengthV233.STRONG for item in witnesses) and len(
        error_services
    ) >= 2:
        trace_action = _action(
            context=context,
            source=EvidenceSourceV22.TRACES,
            targets=error_services,
            outcomes=outcomes,
        )
        if trace_action is not None:
            outcomes, memory = _execute(
                action=trace_action,
                backend=backend,
                outcomes=outcomes,
                context=context,
            )
            reads.append(
                WitnessAuditReadV233(
                    source=EvidenceSourceV22.TRACES,
                    target_services=trace_action.target_services,
                    reason=WitnessAuditReadReasonV233.LOCALIZE_EXCLUSIVE_ROOTS,
                    guard_directed=False,
                )
            )
            graph = _residual_graph_v231(context=context, memory=memory)
            witnesses = build_contradiction_witnesses_v233(
                graph=graph,
                memory=memory,
                observation_scope=spec.case_id,
            )

    decision = evaluate_irreconcilable_guard_v233(
        witnesses=witnesses,
        legal_sources=_legal_sources(context, outcomes),
        remaining_reads=3 - len(reads),
        guard_read_used=False,
    )
    guard_read_used = False
    if decision.disposition is IrreconcilableGuardDispositionV233.RESOLVABLE:
        source = decision.required_additional_reads[0]
        targets = tuple(
            dict.fromkeys(
                service
                for witness in decision.witnesses
                for service in witness.services
                if service in set(case.candidate_services)
            )
        )
        guard_action = _action(
            context=context,
            source=source,
            targets=targets,
            outcomes=outcomes,
        )
        if guard_action is not None and len(reads) < 3:
            outcomes, memory = _execute(
                action=guard_action,
                backend=backend,
                outcomes=outcomes,
                context=context,
            )
            reads.append(
                WitnessAuditReadV233(
                    source=source,
                    target_services=guard_action.target_services,
                    reason=WitnessAuditReadReasonV233.TEST_WITNESS_CLOSURE,
                    guard_directed=True,
                )
            )
            guard_read_used = True
            graph = _residual_graph_v231(context=context, memory=memory)
            witnesses = build_contradiction_witnesses_v233(
                graph=graph,
                memory=memory,
                observation_scope=spec.case_id,
            )
            decision = evaluate_irreconcilable_guard_v233(
                witnesses=witnesses,
                legal_sources=_legal_sources(context, outcomes),
                remaining_reads=3 - len(reads),
                guard_read_used=True,
            )

    return WitnessAuditEntryV233(
        case_id=spec.case_id,
        stratum=stratum,
        decision=decision,
        witnesses=witnesses,
        reads=tuple(reads),
        guard_read_used=guard_read_used,
        report_generated=False,
        registered_known_unchanged=(
            stratum is not AdmissionStratumV232.REGISTERED_KNOWN
            or len(context.admission.admitted_diagnoses) == 1
        ),
        no_incident_unchanged=(
            stratum is not AdmissionStratumV232.NO_INCIDENT
            or context.admission.no_incident_admissible
        ),
    )


def build_v232_witness_audit_v233(*, repository_root: Path) -> WitnessAuditV233:
    evaluation_root = repository_root / "config/dta-v232/evaluation"
    cases = load_evaluation_cases_v232(evaluation_root / "cases.json")
    views = load_evaluation_views_v232(evaluation_root / "ontology-views.json")
    truth_index = load_evaluation_truth_index_v232(evaluation_root / "truth.json")
    entries = tuple(
        audit_case_witness_v233(
            repository_root=repository_root,
            spec=spec,
            view_spec=views.require(spec.case_id),
            stratum=load_evaluation_truth_shard_v232(
                index_path=evaluation_root / "truth.json",
                binding=truth_index.require(spec.case_id),
            ).record.admission_stratum,
        )
        for spec in cases.cases
    )
    irreconcilable = AdmissionStratumV232.INSUFFICIENT_IRRECONCILABLE
    novelty = {AdmissionStratumV232.NOVEL_HIDDEN, AdmissionStratumV232.NOVEL_UNREGISTERED}
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.witness-audit.v1",
        "development_set": "dta-v232-fixed-24-case",
        "case_count": 24,
        "irreconcilable_controls_blocked": sum(
            item.stratum is irreconcilable
            and item.decision.disposition
            is IrreconcilableGuardDispositionV233.IRRECONCILABLE
            for item in entries
        ),
        "novelty_cases_blocked": sum(
            item.stratum in novelty
            and item.decision.disposition
            is IrreconcilableGuardDispositionV233.IRRECONCILABLE
            for item in entries
        ),
        "registered_known_unchanged": sum(
            item.stratum is AdmissionStratumV232.REGISTERED_KNOWN
            and item.registered_known_unchanged
            for item in entries
        ),
        "no_incident_unchanged": sum(
            item.stratum is AdmissionStratumV232.NO_INCIDENT
            and item.no_incident_unchanged
            for item in entries
        ),
        "maximum_discovery_reads": max(len(item.reads) for item in entries),
        "guard_directed_reads": sum(
            read.guard_directed for item in entries for read in item.reads
        ),
        "provider_calls": 0,
        "witness_iteration_count": 1,
        "entries": entries,
    }
    draft = WitnessAuditV233.model_construct(**payload, audit_sha256="0" * 64)
    return WitnessAuditV233.model_validate(
        {
            **payload,
            "audit_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"audit_sha256"})
            ),
        }
    )


def render_witness_audit_markdown_v233(audit: WitnessAuditV233) -> str:
    lines = [
        "# DTA v2.3.3 Witness Guard Development Audit",
        "",
        "Development-only audit over the frozen v2.3.2 set. No Provider was called.",
        "",
        f"- Old irreconcilable controls blocked: `{audit.irreconcilable_controls_blocked} / 3`",
        f"- Old novelty cases blocked: `{audit.novelty_cases_blocked} / 14`",
        f"- Registered-known unchanged: `{audit.registered_known_unchanged} / 4`",
        f"- No-Incident unchanged: `{audit.no_incident_unchanged} / 3`",
        f"- Maximum discovery reads: `{audit.maximum_discovery_reads}`",
        f"- Guard-directed reads: `{audit.guard_directed_reads}`",
        f"- Provider calls: `{audit.provider_calls}`",
        "",
        "| Case | Stratum | Guard | Strong witnesses | Reads |",
        "|---|---|---|---:|---:|",
    ]
    for item in audit.entries:
        strong = sum(
            witness.strength is WitnessStrengthV233.STRONG
            for witness in item.witnesses
        )
        lines.append(
            f"| {item.case_id} | {item.stratum.value} | "
            f"{item.decision.disposition.value} | {strong} | {len(item.reads)} |"
        )
    return "\n".join(lines) + "\n"


__all__ = (
    "WitnessAuditEntryV233",
    "WitnessAuditReadReasonV233",
    "WitnessAuditReadV233",
    "WitnessAuditV233",
    "audit_case_witness_v233",
    "build_v232_witness_audit_v233",
    "render_witness_audit_markdown_v233",
)
