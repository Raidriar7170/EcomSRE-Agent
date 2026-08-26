"""Declarative, non-actionable Extension Ontology runtime for DTA v2.3.4."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SkipValidation, StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    ChangeSalientPayloadV22,
    LogSalientPayloadV22,
    MetricSalientPayloadV22,
    ResourceSalientPayloadV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    TraceSalientPayloadV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.predicates import MechanismV22, RequirementServiceBindingV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v23.evaluation_data_v233 import load_evaluation_cases_v233
from ecomsre.dta_v2.v23.evaluation_v231 import materialize_evaluation_case_v231
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
    ExtensionPredicateDefinitionV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    GenericAnomalyKindRuleV234,
    LogCategoryRuleV234,
    LogTemplateContainsAnyRuleV234,
    MetricBaselineRatioRuleV234,
    MetricThresholdRuleV234,
    RecentChangeStateRuleV234,
    ResourceCpuThresholdRuleV234,
    ResourceMemorySlopeRuleV234,
    RuntimeStateRuleV234,
    ThresholdComparisonV234,
    TraceDurationThresholdRuleV234,
    TraceFirstErrorAtServiceRuleV234,
    TracePathContainsRuleV234,
    hashed_model_v234,
)


EXTENSION_RUNTIME_RULE_TYPES_V234 = (
    CorePredicateReferenceRuleV234,
    GenericAnomalyKindRuleV234,
    LogCategoryRuleV234,
    LogTemplateContainsAnyRuleV234,
    TraceFirstErrorAtServiceRuleV234,
    TracePathContainsRuleV234,
    TraceDurationThresholdRuleV234,
    MetricThresholdRuleV234,
    MetricBaselineRatioRuleV234,
    ResourceCpuThresholdRuleV234,
    ResourceMemorySlopeRuleV234,
    RuntimeStateRuleV234,
    RecentChangeStateRuleV234,
)


class ExtensionSourceCoverageV234(DtaModelV22):
    source: EvidenceSourceV22
    statuses: tuple[ReadSourceStatusV22, ...] = Field(min_length=1)
    reachable: StrictBool

    @model_validator(mode="after")
    def require_coverage(self) -> "ExtensionSourceCoverageV234":
        if self.statuses != tuple(sorted(set(self.statuses), key=lambda item: item.value)):
            raise ValueError("extension source statuses are not canonical")
        expected = any(
            status in {
                ReadSourceStatusV22.SUCCESS_EMPTY,
                ReadSourceStatusV22.SUCCESS_NONEMPTY,
            }
            for status in self.statuses
        )
        if self.reachable is not expected:
            raise ValueError("extension source reachability differs from statuses")
        return self


class ExtensionRuntimeInputV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-runtime-input.v1"]
    case_id: str
    candidate_services: tuple[str, ...] = Field(min_length=1)
    adjacent_services: tuple[tuple[str, str], ...]
    baseline: BaselineProfileV22
    # SalientEvidenceMemoryV22 is already provenance-validated by
    # build_memory_views_v22; revalidation requires the original outcomes.
    memory: SkipValidation[SalientEvidenceMemoryV22]
    generic_anomalies: tuple[GenericAnomalyV23, ...]
    source_coverage: tuple[ExtensionSourceCoverageV234, ...]
    runtime_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_input(self) -> "ExtensionRuntimeInputV234":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("extension runtime candidates are not canonical")
        if self.adjacent_services != tuple(sorted(set(self.adjacent_services))):
            raise ValueError("extension runtime adjacency is not canonical")
        if any(set(edge) - set(self.candidate_services) for edge in self.adjacent_services):
            raise ValueError("extension runtime adjacency escapes candidates")
        sources = tuple(item.source for item in self.source_coverage)
        if sources != tuple(sorted(set(sources), key=lambda item: item.value)):
            raise ValueError("extension source coverage is not canonical")
        if self.baseline.baseline_sha256 != self.memory.baseline_sha256:
            raise ValueError("extension runtime baseline differs from memory")
        if any(
            not set(item.evidence_refs).issubset(
                {ref.evidence_ref for ref in self.memory.evidence_refs}
            )
            for item in self.generic_anomalies
        ):
            raise ValueError("extension anomaly references escape memory")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"runtime_input_sha256"})
        )
        if self.runtime_input_sha256 != expected:
            raise ValueError("extension runtime input digest differs")
        return self

    def source_is_reachable(self, source: EvidenceSourceV22) -> bool:
        return any(item.source is source and item.reachable for item in self.source_coverage)

    def neighbors(self, service: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    right if left == service else left
                    for left, right in self.adjacent_services
                    if service in {left, right}
                }
            )
        )


class ExtensionEvidencePredicateV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-evidence-predicate.v1"]
    predicate_id: str = Field(pattern=r"^ep-v234-[0-9a-f]{16}$")
    registration_id: str
    predicate_name: str
    predicate_slug: str
    source: EvidenceSourceV22
    service: str
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    predicate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_predicate(self) -> "ExtensionEvidencePredicateV234":
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("extension predicate evidence refs are not canonical")
        expected_id = "ep-v234-" + semantic_sha256_v22(
            {
                "registration_id": self.registration_id,
                "predicate_name": self.predicate_name,
                "service": self.service,
                "evidence_refs": self.evidence_refs,
            }
        )[:16]
        if self.predicate_id != expected_id:
            raise ValueError("extension evidence predicate identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"predicate_sha256"})
        )
        if self.predicate_sha256 != expected:
            raise ValueError("extension evidence predicate digest differs")
        return self


class ExtensionSupportDecisionV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-support-decision.v1"]
    registration_id: str
    target_service: str
    admitted: StrictBool
    matched_clause_id: str | None
    supporting_predicates: tuple[ExtensionEvidencePredicateV234, ...]
    supporting_evidence_refs: tuple[str, ...]
    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_decision(self) -> "ExtensionSupportDecisionV234":
        predicate_ids = tuple(item.predicate_id for item in self.supporting_predicates)
        if predicate_ids != tuple(sorted(set(predicate_ids))):
            raise ValueError("extension support predicates are not canonical")
        expected_refs = tuple(
            sorted({ref for item in self.supporting_predicates for ref in item.evidence_refs})
        )
        if self.supporting_evidence_refs != expected_refs:
            raise ValueError("extension support evidence refs differ")
        if self.admitted != (self.matched_clause_id is not None):
            raise ValueError("extension admission differs from matched clause")
        if self.admitted and not self.supporting_predicates:
            raise ValueError("extension admission lacks predicates")
        if not self.admitted and (self.supporting_predicates or self.supporting_evidence_refs):
            raise ValueError("failed extension admission carries support")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("extension support decision digest differs")
        return self


class ExtensionAdmittedDiagnosisV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-admitted-diagnosis.v1"]
    terminal: Literal["REGISTERED_EXTENSION_DIAGNOSIS"]
    mechanism_slug: str
    root_service: str
    broad_fault_domain: str
    matched_clause_id: str
    supporting_predicate_ids: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    registration_id: str
    action_authority: Literal["NONE"]
    diagnosis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_diagnosis(self) -> "ExtensionAdmittedDiagnosisV234":
        for values in (self.supporting_predicate_ids, self.supporting_evidence_refs):
            if values != tuple(sorted(set(values))) or not values:
                raise ValueError("extension diagnosis support is not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"diagnosis_sha256"})
        )
        if self.diagnosis_sha256 != expected:
            raise ValueError("extension diagnosis digest differs")
        return self


class ExtensionDiagnosisRouteV234(str, Enum):
    CORE_KNOWN = "CORE_KNOWN"
    EXTENSION = "EXTENSION"
    NO_INCIDENT = "NO_INCIDENT"
    OPEN_WORLD = "OPEN_WORLD"


class ExtensionDiagnosisResultV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-diagnosis-result.v1"]
    case_id: str
    route: ExtensionDiagnosisRouteV234
    core_known_diagnosis: MechanismV22 | None
    extension_diagnosis: ExtensionAdmittedDiagnosisV234 | None
    no_incident_admitted: StrictBool
    open_world_required: StrictBool
    open_world_provider_calls: Literal[0]
    action_authority: Literal["NONE"]
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_route(self) -> "ExtensionDiagnosisResultV234":
        expected = {
            ExtensionDiagnosisRouteV234.CORE_KNOWN: (
                self.core_known_diagnosis is not None
                and self.extension_diagnosis is None
                and not self.no_incident_admitted
                and not self.open_world_required
            ),
            ExtensionDiagnosisRouteV234.EXTENSION: (
                self.core_known_diagnosis is None
                and self.extension_diagnosis is not None
                and not self.no_incident_admitted
                and not self.open_world_required
            ),
            ExtensionDiagnosisRouteV234.NO_INCIDENT: (
                self.core_known_diagnosis is None
                and self.extension_diagnosis is None
                and self.no_incident_admitted
                and not self.open_world_required
            ),
            ExtensionDiagnosisRouteV234.OPEN_WORLD: (
                self.core_known_diagnosis is None
                and self.extension_diagnosis is None
                and not self.no_incident_admitted
                and self.open_world_required
            ),
        }[self.route]
        if not expected:
            raise ValueError("extension diagnosis route fields differ")
        digest = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != digest:
            raise ValueError("extension diagnosis result digest differs")
        return self


def _compare_v234(left: float, comparison: ThresholdComparisonV234 | str, right: float) -> bool:
    value = comparison.value if isinstance(comparison, ThresholdComparisonV234) else comparison
    if value == "GREATER_THAN":
        return left > right
    if value == "GREATER_THAN_OR_EQUAL":
        return left >= right
    if value == "LESS_THAN":
        return left < right
    if value == "LESS_THAN_OR_EQUAL":
        return left <= right
    raise AssertionError("unsupported bounded comparison")


def _predicate_match_v234(
    *,
    definition: ExtensionPredicateDefinitionV234,
    runtime_input: ExtensionRuntimeInputV234,
    target_service: str,
) -> tuple[str, tuple[str, ...]] | None:
    if not runtime_input.source_is_reachable(definition.evidence_source):
        return None
    eligible = {target_service}
    if definition.service_binding is RequirementServiceBindingV22.TARGET_OR_PARENT:
        eligible.update(runtime_input.neighbors(target_service))
    if definition.require_exact_parent:
        eligible.discard(target_service)
    rule = definition.extraction_rule
    if not isinstance(rule, EXTENSION_RUNTIME_RULE_TYPES_V234):
        raise AssertionError("unmapped extension predicate rule")
    if isinstance(rule, CorePredicateReferenceRuleV234):
        for item in runtime_input.memory.predicates:
            if item.service in eligible and item.predicate_kind is rule.predicate_kind:
                return item.service, item.evidence_refs
        return None
    if isinstance(rule, GenericAnomalyKindRuleV234):
        for anomaly in runtime_input.generic_anomalies:
            if anomaly.service in eligible and anomaly.kind is rule.anomaly_kind:
                return anomaly.service, anomaly.evidence_refs
        return None
    for fact in runtime_input.memory.salient_facts:
        if fact.service not in eligible or fact.source is not definition.evidence_source:
            continue
        payload = fact.payload
        matched = False
        if isinstance(rule, LogCategoryRuleV234) and isinstance(payload, LogSalientPayloadV22):
            matched = payload.category is rule.category
        elif isinstance(rule, LogTemplateContainsAnyRuleV234) and isinstance(payload, LogSalientPayloadV22):
            template = payload.normalized_template if rule.case_sensitive else payload.normalized_template.casefold()
            literals = rule.literals if rule.case_sensitive else tuple(item.casefold() for item in rule.literals)
            matched = any(literal in template for literal in literals)
        elif isinstance(rule, TraceFirstErrorAtServiceRuleV234) and isinstance(payload, TraceSalientPayloadV22):
            matched = payload.first_error_location
        elif isinstance(rule, TracePathContainsRuleV234) and isinstance(payload, TraceSalientPayloadV22):
            if rule.required_service_role == "TARGET":
                matched = target_service in payload.service_path
            elif rule.required_service_role == "PARENT":
                matched = any(item in payload.service_path for item in runtime_input.neighbors(target_service))
            else:
                matched = target_service in payload.service_path or any(
                    item in payload.service_path for item in runtime_input.neighbors(target_service)
                )
        elif isinstance(rule, TraceDurationThresholdRuleV234) and isinstance(payload, TraceSalientPayloadV22):
            matched = _compare_v234(payload.duration_ms, rule.comparison, rule.milliseconds)
        elif isinstance(rule, MetricThresholdRuleV234) and isinstance(payload, MetricSalientPayloadV22):
            matched = (
                payload.metric_kind is rule.metric_kind
                and payload.value is not None
                and _compare_v234(payload.value, rule.comparison, rule.threshold)
            )
        elif isinstance(rule, MetricBaselineRatioRuleV234) and isinstance(payload, MetricSalientPayloadV22):
            matched = (
                payload.metric_kind is rule.metric_kind
                and payload.sample_count >= rule.minimum_samples
                and payload.baseline_ratio is not None
                and _compare_v234(payload.baseline_ratio, rule.comparison, rule.ratio)
            )
        elif isinstance(rule, ResourceCpuThresholdRuleV234) and isinstance(payload, ResourceSalientPayloadV22):
            matched = _compare_v234(payload.cpu_p95_percent, rule.comparison, rule.percent)
        elif isinstance(rule, ResourceMemorySlopeRuleV234) and isinstance(payload, ResourceSalientPayloadV22):
            matched = (
                payload.sample_count >= rule.minimum_points
                and _compare_v234(
                    payload.memory_slope_bytes_per_second,
                    rule.comparison,
                    rule.bytes_per_second,
                )
            )
        elif isinstance(rule, RuntimeStateRuleV234) and isinstance(payload, RuntimeSalientPayloadV22):
            matched = payload.state in rule.states
        elif isinstance(rule, RecentChangeStateRuleV234) and isinstance(payload, ChangeSalientPayloadV22):
            matched = payload.category in rule.categories and payload.relative_seconds <= rule.window_seconds
        if matched:
            return fact.service, fact.evidence_refs
    return None


class ExtensionPredicateRuntimeV234:
    """Total interpreter for the bounded declarative predicate union."""

    def evaluate(
        self,
        *,
        registration: CompiledFaultRegistrationV234,
        runtime_input: ExtensionRuntimeInputV234,
        target_service: str,
    ) -> tuple[ExtensionEvidencePredicateV234, ...]:
        matched = []
        for definition in registration.predicates:
            result = _predicate_match_v234(
                definition=definition,
                runtime_input=runtime_input,
                target_service=target_service,
            )
            if result is None:
                continue
            service, refs = result
            identity = {
                "registration_id": registration.registration_id,
                "predicate_name": definition.predicate_name,
                "service": service,
                "evidence_refs": tuple(sorted(set(refs))),
            }
            matched.append(
                hashed_model_v234(
                    ExtensionEvidencePredicateV234,
                    {
                        "schema_version": "dta-v234.extension-evidence-predicate.v1",
                        "predicate_id": f"ep-v234-{semantic_sha256_v22(identity)[:16]}",
                        "registration_id": registration.registration_id,
                        "predicate_name": definition.predicate_name,
                        "predicate_slug": definition.predicate_slug,
                        "source": definition.evidence_source,
                        "service": service,
                        "evidence_refs": tuple(sorted(set(refs))),
                    },
                    "predicate_sha256",
                )
            )
        return tuple(sorted(matched, key=lambda item: item.predicate_id))


class ExtensionSupportPolicyV234:
    """Evaluate the compiled DNF without entering the frozen v2.2 policy."""

    def evaluate(
        self,
        *,
        registration: CompiledFaultRegistrationV234,
        runtime_input: ExtensionRuntimeInputV234,
        target_services: tuple[str, ...] | None = None,
    ) -> tuple[ExtensionSupportDecisionV234, ...]:
        services = runtime_input.candidate_services if target_services is None else target_services
        if not set(services).issubset(runtime_input.candidate_services):
            raise ValueError("extension evaluation target escapes candidates")
        decisions = []
        runtime = ExtensionPredicateRuntimeV234()
        for target in tuple(sorted(set(services))):
            predicates = runtime.evaluate(
                registration=registration,
                runtime_input=runtime_input,
                target_service=target,
            )
            by_name = {item.predicate_name: item for item in predicates}
            chosen_clause = None
            chosen_predicates: tuple[ExtensionEvidencePredicateV234, ...] = ()
            for clause in registration.support_clauses:
                selected = tuple(
                    by_name[requirement.predicate_name]
                    for requirement in clause.requirements
                    if requirement.predicate_name in by_name
                )
                if len(selected) == len(clause.requirements):
                    chosen_clause = clause.clause_id
                    chosen_predicates = tuple(
                        sorted(selected, key=lambda item: item.predicate_id)
                    )
                    break
            payload: dict[str, Any] = {
                "schema_version": "dta-v234.extension-support-decision.v1",
                "registration_id": registration.registration_id,
                "target_service": target,
                "admitted": chosen_clause is not None,
                "matched_clause_id": chosen_clause,
                "supporting_predicates": chosen_predicates,
                "supporting_evidence_refs": tuple(
                    sorted({ref for item in chosen_predicates for ref in item.evidence_refs})
                ),
            }
            decisions.append(
                hashed_model_v234(
                    ExtensionSupportDecisionV234,
                    payload,
                    "decision_sha256",
                )
            )
        return tuple(decisions)


@lru_cache(maxsize=64)
def build_extension_replay_input_v234(
    *, repository_root: Path, case_id: str
) -> ExtensionRuntimeInputV234:
    cases = load_evaluation_cases_v233(
        repository_root / "config/dta-v233/evaluation/cases.json"
    )
    spec = cases.require(case_id)
    case = materialize_evaluation_case_v231(repository_root=repository_root, spec=spec)
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _snapshot, _full, catalog = _bootstrap(
        case=case,
        topology=topology,
        run_id=semantic_sha256_v22(
            {"case_id": case_id, "lane": "dta-v234-extension-runtime"}
        )[:32],
    )
    executed = {item.action_id for item in outcomes}
    backend = QuerySpecificReplayBackendV22(case.capture)
    additional = tuple(
        backend.execute(action)
        for action in catalog.registry_actions
        if action.action_id not in executed
        and action.source is not EvidenceSourceV22.RUNTIME
    )
    baseline = _baseline(case)
    memory, _ = build_memory_views_v22(
        outcomes=(*outcomes, *additional),
        baseline=baseline,
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=case.candidate_services,
    )
    coverage = tuple(
        ExtensionSourceCoverageV234(
            source=source,
            statuses=tuple(
                sorted(
                    {
                        item.status
                        for item in memory.observation_summaries
                        if item.source is source
                    },
                    key=lambda item: item.value,
                )
            ),
            reachable=any(
                item.source is source
                and item.status
                in {
                    ReadSourceStatusV22.SUCCESS_EMPTY,
                    ReadSourceStatusV22.SUCCESS_NONEMPTY,
                }
                for item in memory.observation_summaries
            ),
        )
        for source in sorted(
            {item.source for item in memory.observation_summaries},
            key=lambda item: item.value,
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.extension-runtime-input.v1",
        "case_id": case_id,
        "candidate_services": case.candidate_services,
        "adjacent_services": case.topology_edges,
        "baseline": baseline,
        "memory": memory,
        "generic_anomalies": anomalies,
        "source_coverage": coverage,
    }
    return hashed_model_v234(
        ExtensionRuntimeInputV234,
        payload,
        "runtime_input_sha256",
    )


def _route_result_v234(**payload: Any) -> ExtensionDiagnosisResultV234:
    return hashed_model_v234(
        ExtensionDiagnosisResultV234,
        {"schema_version": "dta-v234.extension-diagnosis-result.v1", **payload},
        "result_sha256",
    )


def diagnose_extension_enabled_v234(
    *,
    repository_root: Path,
    case_id: str,
    registry: Any,
    core_known_diagnosis: MechanismV22 | None,
    no_incident_admitted: bool,
) -> ExtensionDiagnosisResultV234:
    if core_known_diagnosis is not None:
        if core_known_diagnosis in {MechanismV22.NO_INCIDENT, MechanismV22.UNKNOWN}:
            raise ValueError("core priority requires a registered incident mechanism")
        return _route_result_v234(
            case_id=case_id,
            route=ExtensionDiagnosisRouteV234.CORE_KNOWN,
            core_known_diagnosis=core_known_diagnosis,
            extension_diagnosis=None,
            no_incident_admitted=False,
            open_world_required=False,
            open_world_provider_calls=0,
            action_authority="NONE",
        )
    runtime_input = build_extension_replay_input_v234(
        repository_root=repository_root,
        case_id=case_id,
    )
    for entry in registry.entries:
        if entry.status != "ACTIVE":
            continue
        decisions = ExtensionSupportPolicyV234().evaluate(
            registration=entry.compiled_registration,
            runtime_input=runtime_input,
        )
        admitted = next((item for item in decisions if item.admitted), None)
        if admitted is None:
            continue
        diagnosis = hashed_model_v234(
            ExtensionAdmittedDiagnosisV234,
            {
                "schema_version": "dta-v234.extension-admitted-diagnosis.v1",
                "terminal": "REGISTERED_EXTENSION_DIAGNOSIS",
                "mechanism_slug": entry.mechanism_slug,
                "root_service": admitted.target_service,
                "broad_fault_domain": entry.broad_fault_domain,
                "matched_clause_id": admitted.matched_clause_id,
                "supporting_predicate_ids": tuple(
                    item.predicate_id for item in admitted.supporting_predicates
                ),
                "supporting_evidence_refs": admitted.supporting_evidence_refs,
                "registration_id": entry.registration_id,
                "action_authority": "NONE",
            },
            "diagnosis_sha256",
        )
        return _route_result_v234(
            case_id=case_id,
            route=ExtensionDiagnosisRouteV234.EXTENSION,
            core_known_diagnosis=None,
            extension_diagnosis=diagnosis,
            no_incident_admitted=False,
            open_world_required=False,
            open_world_provider_calls=0,
            action_authority="NONE",
        )
    if no_incident_admitted:
        return _route_result_v234(
            case_id=case_id,
            route=ExtensionDiagnosisRouteV234.NO_INCIDENT,
            core_known_diagnosis=None,
            extension_diagnosis=None,
            no_incident_admitted=True,
            open_world_required=False,
            open_world_provider_calls=0,
            action_authority="NONE",
        )
    return _route_result_v234(
        case_id=case_id,
        route=ExtensionDiagnosisRouteV234.OPEN_WORLD,
        core_known_diagnosis=None,
        extension_diagnosis=None,
        no_incident_admitted=False,
        open_world_required=True,
        open_world_provider_calls=0,
        action_authority="NONE",
    )


def assert_extension_diagnosis_non_actionable_v234(value: object) -> None:
    if isinstance(value, ExtensionAdmittedDiagnosisV234):
        raise TypeError("ExtensionAdmittedDiagnosisV234 is non-actionable")


__all__ = (
    "ExtensionAdmittedDiagnosisV234",
    "EXTENSION_RUNTIME_RULE_TYPES_V234",
    "ExtensionDiagnosisResultV234",
    "ExtensionDiagnosisRouteV234",
    "ExtensionEvidencePredicateV234",
    "ExtensionPredicateRuntimeV234",
    "ExtensionRuntimeInputV234",
    "ExtensionSourceCoverageV234",
    "ExtensionSupportDecisionV234",
    "ExtensionSupportPolicyV234",
    "assert_extension_diagnosis_non_actionable_v234",
    "build_extension_replay_input_v234",
    "diagnose_extension_enabled_v234",
)
