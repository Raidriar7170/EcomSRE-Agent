"""Authorized, bounded Provider protocol for DTA v2.3.4 registration drafts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
import json
from pathlib import Path
import time
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    RequirementServiceBindingV22,
    SupportClauseV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.simple_provider import (
    ProviderTransportErrorV22,
    StdlibProviderTransportV22,
)
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    CoreMechanismExampleV234,
    CoreOntologySchemaSnapshotV234,
    PredicateSourceBindingV234,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
    MAX_EXACT_TRANSPORT_RETRIES_V23,
    MAX_PROTOCOL_REPAIRS_V23,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    FormalFaultRegistrationDraftV234,
    FormalPredicateDraftV234,
    LogTemplateContainsAnyRuleV234,
    MechanismProposalV234,
    PredicateImplementationModeV234,
    PredicateRequirementDraftV234,
    RegistrationDraftContentV234,
    RegistrationImplementationModeV234,
    RegistrationProviderModeV234,
    RegistrationTestPlanV234,
    SupportClauseDraftV234,
    assert_provider_authored_content_safe_v234,
    build_formal_registration_draft_v234,
    build_provider_trace_v234,
    hashed_model_v234,
    mechanism_distinguishing_summary_v234,
    mechanism_human_definition_v234,
    predicate_negative_example_v234,
    predicate_positive_example_v234,
    predicate_semantic_definition_v234,
    provider_authored_content_sha256_v234,
    support_clause_rationale_v234,
)
from ecomsre.dta_v2.v23.review_registry import (
    ReviewQueueItemV23,
    ShadowFaultEntryV23,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


REGISTRATION_DRAFT_SYSTEM_PROMPT_V234 = """You are a registration-draft assistant.
The human has authorized FORMAL_DRAFT_ONLY. Use only the supplied accepted-report
projections, Shadow Fault, Provider-visible ontology view, and bounded predicate DSL.
Return exactly the Provider-authored registration fields. Prefer existing core
predicates, use DNF support clauses, and exclude remediation. Do not emit source
code, shell commands, diffs, file contents, paths, URLs, network calls, Runbooks,
credentials, actions, or repository writes. Use Title Case display names, declarative
sentences beginning with a schema-approved domain lead, diagnostic log literals
beginning with a schema-approved domain noun, and identifier-only binding fields.
Follow the supplied canonical text templates exactly; do not paraphrase them."""


class AcceptedReportProjectionSourceV234(str, Enum):
    DEVELOPMENT_FIXTURE = "DEVELOPMENT_FIXTURE"
    RUNTIME_BOUND_V233 = "RUNTIME_BOUND_V233"


class RootOwnershipV234(str, Enum):
    DEVELOPMENT_FIXTURE = "DEVELOPMENT_FIXTURE"
    RUNTIME_OWNED = "RUNTIME_OWNED"


class AcceptedEvidenceSummaryV234(DtaModelV22):
    evidence_ref: str
    source: EvidenceSourceV22
    service: str
    anomaly_kind: GenericAnomalyKindV23
    summary: str = Field(min_length=1, max_length=500)


class AcceptedReportProjectionV234(DtaModelV22):
    schema_version: Literal["dta-v234.accepted-report-projection.v1"]
    accepted_seed_report_id: str
    source_report_id: str
    source_case_id: str
    report_sha256: str
    queue_item_sha256: str
    projection_source: AcceptedReportProjectionSourceV234
    selected_root_service: str
    root_ownership: RootOwnershipV234
    broad_fault_domain: str
    evidence_summaries: tuple[AcceptedEvidenceSummaryV234, ...] = Field(min_length=1)
    projection_sha256: str

    @model_validator(mode="after")
    def require_projection(self) -> "AcceptedReportProjectionV234":
        if (
            self.projection_source
            is AcceptedReportProjectionSourceV234.RUNTIME_BOUND_V233
        ) != (self.root_ownership is RootOwnershipV234.RUNTIME_OWNED):
            raise ValueError("accepted report root ownership differs from projection source")
        refs = tuple(item.evidence_ref for item in self.evidence_summaries)
        if refs != tuple(sorted(set(refs))):
            raise ValueError("accepted report evidence summaries are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"projection_sha256"})
        )
        if self.projection_sha256 != expected:
            raise ValueError("accepted report projection digest differs")
        return self


class ProviderCoreOntologyViewV234(DtaModelV22):
    schema_version: Literal["dta-v234.provider-core-ontology-view.v1"]
    authoritative_snapshot_sha256: str
    hidden_mechanism: MechanismV22 | None = Field(exclude=True, repr=False)
    runtime_known_mechanisms: tuple[MechanismV22, ...]
    runtime_known_support_clauses: tuple[SupportClauseV22, ...]
    format_reference_support_clauses: tuple[SupportClauseV22, ...]
    visible_predicate_kinds: tuple[PredicateKindV22, ...]
    predicate_source_bindings: tuple[PredicateSourceBindingV234, ...]
    authoritative_single_predicate_allowlist: tuple[PredicateKindV22, ...]
    representative_examples: tuple[CoreMechanismExampleV234, ...]
    view_sha256: str

    @model_validator(mode="after")
    def require_view(self) -> "ProviderCoreOntologyViewV234":
        mechanisms = tuple(
            sorted(
                {item.mechanism for item in self.runtime_known_support_clauses},
                key=lambda item: item.value,
            )
        )
        if mechanisms != self.runtime_known_mechanisms:
            raise ValueError("Provider ontology mechanisms differ from runtime clauses")
        predicates = tuple(item.predicate_kind for item in self.predicate_source_bindings)
        if predicates != self.visible_predicate_kinds:
            raise ValueError("Provider ontology predicate source bindings differ")
        if self.hidden_mechanism is not None:
            rendered = json.dumps(
                self.model_dump(
                    mode="json",
                    exclude={"hidden_mechanism", "view_sha256"},
                ),
                sort_keys=True,
            ).casefold()
            leaks = {
                self.hidden_mechanism.value.casefold(),
                self.hidden_mechanism.value.casefold().replace("_", "-"),
                self.hidden_mechanism.value.casefold().replace("_", " "),
            }
            if any(value in rendered for value in leaks):
                raise ValueError("hidden mechanism leaks into Provider ontology view")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"view_sha256"})
        )
        if self.view_sha256 != expected:
            raise ValueError("Provider ontology view digest differs")
        return self


class RegistrationDraftProviderRequestV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-draft-provider-request.v1"]
    authorization_id: str
    authorization_note: str
    authorization_simulation: StrictBool
    shadow_fault: ShadowFaultEntryV23
    registration_seed_sha256: str
    legacy_registration_seed: dict[str, Any]
    accepted_reports: tuple[AcceptedReportProjectionV234, ...] = Field(min_length=1)
    core_ontology_view: ProviderCoreOntologyViewV234
    confusable_shadow_faults: tuple[dict[str, Any], ...]
    bounded_dsl_rule_kinds: tuple[str, ...]
    request_sha256: str

    @model_validator(mode="after")
    def require_request(self) -> "RegistrationDraftProviderRequestV234":
        if self.authorization_id == "" or self.registration_seed_sha256 == "":
            raise ValueError("registration Provider request lacks authorization bindings")
        report_ids = tuple(item.accepted_seed_report_id for item in self.accepted_reports)
        if report_ids != tuple(sorted(set(report_ids))):
            raise ValueError("registration Provider report projections are not canonical")
        if self.bounded_dsl_rule_kinds != tuple(
            sorted(set(self.bounded_dsl_rule_kinds))
        ):
            raise ValueError("registration Provider DSL rule kinds are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("registration Provider request digest differs")
        return self


class ProviderAuthoredRegistrationDraftV234(DtaModelV22):
    implementation_mode: RegistrationImplementationModeV234
    mechanism: MechanismProposalV234
    predicates: tuple[FormalPredicateDraftV234, ...]
    support_clauses: tuple[SupportClauseDraftV234, ...]
    test_plan: RegistrationTestPlanV234
    unresolved_engineering_questions: tuple[str, ...]


_DSL_RULE_KINDS_V234 = (
    "CORE_PREDICATE_REFERENCE",
    "GENERIC_ANOMALY_KIND",
    "LOG_CATEGORY",
    "LOG_TEMPLATE_CONTAINS_ANY",
    "METRIC_BASELINE_RATIO",
    "METRIC_THRESHOLD",
    "RECENT_CHANGE_STATE",
    "RESOURCE_CPU_THRESHOLD",
    "RESOURCE_MEMORY_SLOPE",
    "RUNTIME_STATE",
    "TRACE_DURATION_THRESHOLD",
    "TRACE_FIRST_ERROR_AT_SERVICE",
    "TRACE_PATH_CONTAINS",
)


def _source_from_evidence_ref_v234(ref: str) -> EvidenceSourceV22:
    lowered = ref.casefold()
    by_token = {
        ":metrics:": EvidenceSourceV22.METRICS,
        ":logs:": EvidenceSourceV22.LOGS,
        ":traces:": EvidenceSourceV22.TRACES,
        ":runtime:": EvidenceSourceV22.RUNTIME,
        ":resources:": EvidenceSourceV22.RESOURCES,
        ":changes:": EvidenceSourceV22.CHANGES,
    }
    matches = tuple(source for token, source in by_token.items() if token in lowered)
    if len(matches) != 1:
        raise ValueError("development report evidence ref lacks one source binding")
    return matches[0]


def project_development_report_v234(
    item: ReviewQueueItemV23,
) -> AcceptedReportProjectionV234:
    """Project an explicitly simulated legacy report without claiming runtime ownership."""

    if not item.automated_fixture or not item.source_case_id.startswith("dta-v234-"):
        raise ValueError("legacy accepted report is not a v2.3.4 development fixture")
    anomalies_by_source = {
        source: tuple(
            sorted(
                (
                    value
                    for value in item.residual_anomalies
                    if value.source is source
                ),
                key=lambda value: value.anomaly_id,
            )
        )
        for source in EvidenceSourceV22
    }
    source_ordinals = {source: 0 for source in EvidenceSourceV22}
    summaries = []
    for ref in item.report.supporting_evidence_refs:
        source = _source_from_evidence_ref_v234(ref)
        candidates = anomalies_by_source[source]
        ordinal = source_ordinals[source]
        if ordinal >= len(candidates):
            raise ValueError("development report evidence lacks an anomaly projection")
        anomaly = candidates[ordinal]
        source_ordinals[source] += 1
        summaries.append(
            AcceptedEvidenceSummaryV234(
                evidence_ref=ref,
                source=source,
                service=anomaly.service,
                anomaly_kind=anomaly.kind,
                summary=next(
                    (
                        symptom
                        for symptom in item.report.observed_symptoms
                        if source.value.casefold().removesuffix("s") in symptom.casefold()
                    ),
                    item.report.mechanism_description,
                ),
            )
        )
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.accepted-report-projection.v1",
        "accepted_seed_report_id": item.report.report_id,
        "source_report_id": item.report.report_id,
        "source_case_id": item.source_case_id,
        "report_sha256": item.report.report_sha256,
        "queue_item_sha256": item.queue_item_sha256,
        "projection_source": AcceptedReportProjectionSourceV234.DEVELOPMENT_FIXTURE,
        "selected_root_service": item.report.suspected_root_services[0],
        "root_ownership": RootOwnershipV234.DEVELOPMENT_FIXTURE,
        "broad_fault_domain": item.report.broad_fault_domain.value,
        "evidence_summaries": tuple(sorted(summaries, key=lambda value: value.evidence_ref)),
    }
    return hashed_model_v234(
        AcceptedReportProjectionV234,
        payload,
        "projection_sha256",
    )


def build_provider_core_ontology_view_v234(
    *,
    snapshot: CoreOntologySchemaSnapshotV234,
    hidden_mechanism: MechanismV22 | None = None,
) -> ProviderCoreOntologyViewV234:
    runtime_clauses = tuple(
        clause
        for clause in snapshot.frozen_core_support_clauses
        if clause.mechanism is not hidden_mechanism
    )
    format_clauses = tuple(
        clause
        for clause in snapshot.core_support_clauses
        if clause.mechanism is not hidden_mechanism
    )
    visible_predicates = tuple(
        sorted(
            {
                requirement.predicate_kind
                for clause in format_clauses
                for requirement in clause.requirements
            },
            key=lambda item: item.value,
        )
    )
    source_by_kind = {
        item.predicate_kind: item for item in snapshot.predicate_source_bindings
    }
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.provider-core-ontology-view.v1",
        "authoritative_snapshot_sha256": snapshot.snapshot_sha256,
        "hidden_mechanism": hidden_mechanism,
        "runtime_known_mechanisms": tuple(
            sorted(
                {clause.mechanism for clause in runtime_clauses},
                key=lambda item: item.value,
            )
        ),
        "runtime_known_support_clauses": runtime_clauses,
        "format_reference_support_clauses": format_clauses,
        "visible_predicate_kinds": visible_predicates,
        "predicate_source_bindings": tuple(
            source_by_kind[kind] for kind in visible_predicates
        ),
        "authoritative_single_predicate_allowlist": tuple(
            kind
            for kind in snapshot.authoritative_single_predicate_allowlist
            if kind in visible_predicates
        ),
        "representative_examples": tuple(
            item
            for item in snapshot.representative_examples
            if item.mechanism is not hidden_mechanism
        ),
    }
    return hashed_model_v234(ProviderCoreOntologyViewV234, payload, "view_sha256")


def build_registration_draft_provider_request_v234(
    *,
    authorization_context: DraftGenerationAuthorizationResultV234,
    shadow_fault: ShadowFaultEntryV23,
    accepted_reports: tuple[AcceptedReportProjectionV234, ...],
    ontology_view: ProviderCoreOntologyViewV234,
    confusable_shadow_faults: tuple[ShadowFaultEntryV23, ...] = (),
) -> RegistrationDraftProviderRequestV234:
    authorization = authorization_context.authorization
    seed = authorization_context.registration_seed
    transition = authorization_context.transition
    if authorization.shadow_fault_id != shadow_fault.shadow_fault_id:
        raise ValueError("registration Provider shadow differs from authorization")
    if seed.shadow_fault_id != shadow_fault.shadow_fault_id:
        raise ValueError("registration Provider seed differs from shadow fault")
    if transition.shadow_entry_sha256 != shadow_fault.entry_sha256:
        raise ValueError("registration Provider shadow bytes differ from authorization")
    if ontology_view.authoritative_snapshot_sha256 != (
        authorization_context.core_ontology_snapshot.snapshot_sha256
    ):
        raise ValueError("registration Provider ontology view differs from authorization")
    projection_ids = tuple(item.accepted_seed_report_id for item in accepted_reports)
    if projection_ids != seed.positive_report_ids:
        raise ValueError("registration Provider accepted reports differ from seed")
    bindings = {item.report_id: item for item in seed.accepted_report_bindings}
    if any(
        item.accepted_seed_report_id not in bindings
        or item.source_report_id != item.accepted_seed_report_id
        or item.source_case_id
        != bindings[item.accepted_seed_report_id].source_case_id
        or item.report_sha256
        != bindings[item.accepted_seed_report_id].report_sha256
        or item.queue_item_sha256
        != bindings[item.accepted_seed_report_id].queue_item_sha256
        for item in accepted_reports
    ):
        raise ValueError("registration Provider accepted report bytes differ from seed")
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-draft-provider-request.v1",
        "authorization_id": authorization.authorization_id,
        "authorization_note": authorization.authorization_note,
        "authorization_simulation": authorization.simulation,
        "shadow_fault": shadow_fault,
        "registration_seed_sha256": seed.seed_sha256,
        "legacy_registration_seed": seed.legacy_registration_draft.model_dump(mode="json"),
        "accepted_reports": accepted_reports,
        "core_ontology_view": ontology_view,
        "confusable_shadow_faults": tuple(
            item.model_dump(mode="json")
            for item in sorted(confusable_shadow_faults, key=lambda value: value.shadow_fault_id)
        ),
        "bounded_dsl_rule_kinds": _DSL_RULE_KINDS_V234,
    }
    return hashed_model_v234(
        RegistrationDraftProviderRequestV234,
        payload,
        "request_sha256",
    )


def _deterministic_provider_content_v234(
    request: RegistrationDraftProviderRequestV234,
) -> ProviderAuthoredRegistrationDraftV234:
    """A schema-identical development Provider with no network call."""

    report = request.accepted_reports[0]
    evidence_by_source = {
        source: tuple(
            sorted(
                item.evidence_ref
                for item in report.evidence_summaries
                if item.source is source
            )
        )
        for source in EvidenceSourceV22
    }
    if EvidenceSourceV22.LOGS not in evidence_by_source or (
        len(evidence_by_source[EvidenceSourceV22.LOGS]) != 1
        or len(evidence_by_source[EvidenceSourceV22.METRICS]) < 2
    ):
        raise ValueError("connection-pool development draft requires logs and metrics")
    metric_refs = evidence_by_source[EvidenceSourceV22.METRICS]
    mechanism = MechanismProposalV234(
        mechanism_enum_name="CONNECTION_POOL_EXHAUSTION",
        mechanism_slug="connection-pool-exhaustion",
        display_name="Connection Pool Exhaustion",
        broad_fault_domain=request.shadow_fault.broad_fault_domain,
        human_definition=mechanism_human_definition_v234(
            "connection-pool-exhaustion"
        ),
        distinguishing_summary=mechanism_distinguishing_summary_v234(
            "connection-pool-exhaustion"
        ),
        confusable_core_mechanisms=(
            MechanismV22.DEPENDENCY_LATENCY,
            MechanismV22.SERVICE_UNAVAILABLE,
        ),
        confusable_extension_mechanisms=(),
    )
    predicates = (
        FormalPredicateDraftV234(
            predicate_name="CONNECTION_POOL_WAIT_LOG",
            predicate_slug="connection-pool-wait-log",
            implementation_mode=(
                PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE
            ),
            evidence_source=EvidenceSourceV22.LOGS,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "connection-pool-wait-log"
            ),
            extraction_rule=LogTemplateContainsAnyRuleV234(
                kind="LOG_TEMPLATE_CONTAINS_ANY",
                literals=(
                    "connection pool exhausted",
                    "pool capacity wait",
                    "worker pool semaphore wait",
                ),
                case_sensitive=False,
            ),
            threshold_rule=None,
            supporting_report_evidence_refs=evidence_by_source[EvidenceSourceV22.LOGS],
            positive_examples=(
                predicate_positive_example_v234("connection-pool-wait-log"),
            ),
            negative_examples=(
                predicate_negative_example_v234("connection-pool-wait-log"),
            ),
        ),
        FormalPredicateDraftV234(
            predicate_name="METRIC_ERROR_RATE_STRONG",
            predicate_slug="metric-error-rate-strong",
            implementation_mode=PredicateImplementationModeV234.REUSE_CORE_PREDICATE,
            evidence_source=EvidenceSourceV22.METRICS,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "metric-error-rate-strong"
            ),
            extraction_rule=CorePredicateReferenceRuleV234(
                kind="CORE_PREDICATE_REFERENCE",
                predicate_kind=PredicateKindV22.METRIC_ERROR_RATE_STRONG,
            ),
            threshold_rule=None,
            supporting_report_evidence_refs=(metric_refs[0],),
            positive_examples=(
                predicate_positive_example_v234("metric-error-rate-strong"),
            ),
            negative_examples=(
                predicate_negative_example_v234("metric-error-rate-strong"),
            ),
        ),
        FormalPredicateDraftV234(
            predicate_name="METRIC_LATENCY_STRONG",
            predicate_slug="metric-latency-strong",
            implementation_mode=PredicateImplementationModeV234.REUSE_CORE_PREDICATE,
            evidence_source=EvidenceSourceV22.METRICS,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "metric-latency-strong"
            ),
            extraction_rule=CorePredicateReferenceRuleV234(
                kind="CORE_PREDICATE_REFERENCE",
                predicate_kind=PredicateKindV22.METRIC_LATENCY_STRONG,
            ),
            threshold_rule=None,
            supporting_report_evidence_refs=(metric_refs[1],),
            positive_examples=(
                predicate_positive_example_v234("metric-latency-strong"),
            ),
            negative_examples=(
                predicate_negative_example_v234("metric-latency-strong"),
            ),
        ),
    )
    clauses = tuple(
        SupportClauseDraftV234(
            clause_id=f"connection-pool-exhaustion:wait-log-and-{suffix}",
            mechanism_slug="connection-pool-exhaustion",
            requirements=(
                PredicateRequirementDraftV234(
                    predicate_name="CONNECTION_POOL_WAIT_LOG",
                    service_binding=RequirementServiceBindingV22.TARGET,
                    require_exact_parent=False,
                ),
                PredicateRequirementDraftV234(
                    predicate_name=predicate_name,
                    service_binding=RequirementServiceBindingV22.TARGET,
                    require_exact_parent=False,
                ),
            ),
            rationale=support_clause_rationale_v234(),
        )
        for suffix, predicate_name in (
            ("error-rate", "METRIC_ERROR_RATE_STRONG"),
            ("latency", "METRIC_LATENCY_STRONG"),
        )
    )
    return ProviderAuthoredRegistrationDraftV234(
        implementation_mode=RegistrationImplementationModeV234.DECLARATIVE_READY,
        mechanism=mechanism,
        predicates=predicates,
        support_clauses=clauses,
        test_plan=RegistrationTestPlanV234(
            positive_report_ids=tuple(
                sorted(item.accepted_seed_report_id for item in request.accepted_reports)
            ),
            positive_case_ids=tuple(
                sorted(item.source_case_id for item in request.accepted_reports)
            ),
            confusable_core_mechanisms=mechanism.confusable_core_mechanisms,
            required_known_controls=(
                "known-dependency-latency-control",
                "known-service-unavailable-control",
            ),
            required_no_incident_controls=("healthy-connection-pool-control",),
            required_counterfactuals=(
                "move-pool-wait-log-to-downstream-service",
            ),
            required_source_failure_tests=(
                "logs-unavailable-fails-closed",
                "metrics-unavailable-fails-closed",
            ),
            required_clause_binding_tests=(
                "requirements-bind-same-target-service",
            ),
        ),
        unresolved_engineering_questions=(),
    )


_PROVIDER_FIELDS_V234 = frozenset(ProviderAuthoredRegistrationDraftV234.model_fields)


def _request_body_v234(
    request: RegistrationDraftProviderRequestV234,
    *,
    repair_ordinal: int,
    repair_issue_codes: tuple[str, ...] = (),
) -> str:
    body: dict[str, Any] = {
        "system": REGISTRATION_DRAFT_SYSTEM_PROMPT_V234,
        "request": request.model_dump(mode="json"),
        "response_contract": {
            "fields": tuple(sorted(_PROVIDER_FIELDS_V234)),
            "format": "one JSON object only",
            "locally_owned_fields_forbidden": (
                "authorization_id",
                "draft_id",
                "provider_trace",
                "remediation_registration",
                "repository_write_authority",
            ),
            "canonical_text_templates": {
                "mechanism_human_definition": (
                    "The {mechanism_slug_with_hyphens_replaced_by_spaces} mechanism "
                    "is defined by bounded evidence predicates and canonical support clauses."
                ),
                "mechanism_distinguishing_summary": (
                    "The {mechanism_slug_with_hyphens_replaced_by_spaces} mechanism "
                    "is distinguished by its canonical clauses and evidence bindings."
                ),
                "predicate_semantic_definition": (
                    "The {predicate_slug_with_hyphens_replaced_by_spaces} predicate "
                    "is defined by its typed extraction rule and accepted evidence binding."
                ),
                "predicate_positive_example": (
                    "The {predicate_slug_with_hyphens_replaced_by_spaces} predicate "
                    "is present in accepted evidence."
                ),
                "predicate_negative_example": (
                    "The {predicate_slug_with_hyphens_replaced_by_spaces} predicate "
                    "is absent from accepted evidence."
                ),
                "support_clause_rationale": (
                    "The clause requires its canonical predicates at their declared "
                    "service bindings."
                ),
                "engineering_question": (
                    "Define the bounded engineering gap for {predicate_slug}."
                ),
            },
            "semantic_rules": (
                "Copy positive report IDs, positive case IDs, and evidence refs exactly from the request.",
                "Use lowercase hyphenated opaque identifiers for every control reference.",
                "Use GENERIC_ANOMALY_KIND or a typed threshold for unfamiliar log semantics.",
                "Use ENGINEERING_REQUIRED with a null extraction rule when ordering or correlation is outside the bounded DSL.",
                "DUPLICATE_EXISTING and INSUFFICIENT_EVIDENCE must contain no support clauses.",
            ),
        },
    }
    if repair_ordinal:
        body["protocol_repair"] = {
            "ordinal": repair_ordinal,
            "safe_issue_codes": repair_issue_codes
            or ("PRIOR_RESPONSE_PROTOCOL_INVALID",),
            "instruction": (
                "Regenerate the complete object. Use the exact canonical text templates. "
                "Display name must be the title-cased enum tokens. Sort every tuple field "
                "lexicographically. Positive upper-tail thresholds must be greater than zero. "
                "DECLARATIVE_READY requires predicates and clauses and forbids engineering "
                "questions. ENGINEERING_REQUIRED requires at least one "
                "REQUIRES_CODE_IMPLEMENTATION predicate with null extraction rule plus one "
                "canonical engineering question."
            ),
        }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_provider_content_v234(raw: str) -> ProviderAuthoredRegistrationDraftV234:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("registration Provider response is not one JSON object")
    if set(value) != _PROVIDER_FIELDS_V234:
        raise ValueError("registration Provider response fields differ")
    content = ProviderAuthoredRegistrationDraftV234.model_validate_json(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    assert_provider_authored_content_safe_v234(content.model_dump(mode="json"))
    return content


class OpenAICompatibleRegistrationDraftTransportV234:
    """Dedicated forced-tool transport with no raw-content fallback."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        minimum_request_interval_seconds: float = 6.0,
        timeout_seconds: float = 120.0,
        raw_artifact_dir: Path | None = None,
    ) -> None:
        if minimum_request_interval_seconds < 0:
            raise ValueError("registration Provider request interval cannot be negative")
        self.config = config
        self.minimum_request_interval_seconds = minimum_request_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.raw_artifact_dir = raw_artifact_dir
        self.transport = StdlibProviderTransportV22()
        self._last_started: float | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.latency_ms = 0.0

    @staticmethod
    def _tool() -> dict[str, object]:
        schema = ProviderAuthoredRegistrationDraftV234.model_json_schema()
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": "submit_formal_fault_registration_draft",
                "description": "Submit only bounded registration semantics.",
                "strict": False,
                "parameters": schema,
            },
        }

    @staticmethod
    def _extract(response: Mapping[str, object]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_PROVIDER_ENVELOPE",
                retryable=False,
            )
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_PROVIDER_ENVELOPE",
                retryable=False,
            )
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_PROVIDER_ENVELOPE",
                retryable=False,
            )
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_REGISTRATION_TOOL_CALL",
                retryable=False,
            )
        call = calls[0]
        function = call.get("function") if isinstance(call, Mapping) else None
        if (
            not isinstance(function, Mapping)
            or function.get("name") != "submit_formal_fault_registration_draft"
            or not isinstance(function.get("arguments"), str)
        ):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_REGISTRATION_TOOL_CALL",
                retryable=False,
            )
        return cast(str, function["arguments"])

    def __call__(self, body: str) -> str:
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_LOCAL_REQUEST",
                retryable=False,
            )
        now = time.monotonic()
        if self._last_started is not None:
            delay = self.minimum_request_interval_seconds - (now - self._last_started)
            if delay > 0:
                time.sleep(delay)
        self._last_started = time.monotonic()
        started = time.monotonic()
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": str(parsed["system"])},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": parsed["request"],
                            "response_contract": parsed["response_contract"],
                            "protocol_repair": parsed.get("protocol_repair"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "tools": [self._tool()],
            "tool_choice": {
                "type": "function",
                "function": {
                    "name": "submit_formal_fault_registration_draft"
                },
            },
            "temperature": 0,
        }
        try:
            response = self.transport.post_json(
                url=f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
        except ProviderTransportErrorV22 as exc:
            raise DiscoveryProviderTransportErrorV23(
                exc.safe_code,
                retryable=exc.retryable,
            ) from exc
        if self.raw_artifact_dir is not None:
            self.raw_artifact_dir.mkdir(parents=True, exist_ok=True)
            ordinal = len(tuple(self.raw_artifact_dir.glob("request-*.json"))) + 1
            (self.raw_artifact_dir / f"request-{ordinal:03d}.json").write_text(
                json.dumps(payload, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            (self.raw_artifact_dir / f"response-{ordinal:03d}.json").write_text(
                json.dumps(response, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        self.latency_ms += (time.monotonic() - started) * 1000.0
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            self.input_tokens += int(usage.get("prompt_tokens", 0))
            self.output_tokens += int(usage.get("completion_tokens", 0))
            self.total_tokens += int(usage.get("total_tokens", 0))
        return self._extract(response)


class RegistrationDraftProviderV234:
    """Generate one formal draft; deterministic validation is deliberately separate."""

    def __init__(self, transport: Callable[[str], str] | None = None) -> None:
        self.transport = transport

    def generate(
        self,
        *,
        authorization_context: DraftGenerationAuthorizationResultV234,
        shadow: ShadowFaultEntryV23,
        accepted_reports: tuple[ReviewQueueItemV23, ...],
        hidden_mechanism: MechanismV22 | None = None,
        ontology_view: ProviderCoreOntologyViewV234 | None = None,
        confusable_shadow_faults: tuple[ShadowFaultEntryV23, ...] = (),
    ) -> FormalFaultRegistrationDraftV234:
        projections = tuple(
            project_development_report_v234(item)
            for item in sorted(accepted_reports, key=lambda value: value.report.report_id)
        )
        if ontology_view is not None and hidden_mechanism is not None:
            raise ValueError(
                "registration Provider accepts either a frozen ontology view or a hidden mechanism"
            )
        if ontology_view is None:
            ontology_view = build_provider_core_ontology_view_v234(
                snapshot=authorization_context.core_ontology_snapshot,
                hidden_mechanism=hidden_mechanism,
            )
        request = build_registration_draft_provider_request_v234(
            authorization_context=authorization_context,
            shadow_fault=shadow,
            accepted_reports=projections,
            ontology_view=ontology_view,
            confusable_shadow_faults=confusable_shadow_faults,
        )
        if self.transport is None:
            authored = _deterministic_provider_content_v234(request)
            provider_mode = RegistrationProviderModeV234.DETERMINISTIC_DEVELOPMENT
            provider_calls = 0
            protocol_repairs = 0
            transport_retries = 0
        else:
            authored, provider_calls, protocol_repairs, transport_retries = (
                self._call_provider(request)
            )
            provider_mode = RegistrationProviderModeV234.OPENAI_COMPATIBLE
        content = RegistrationDraftContentV234(
            **authored.model_dump(mode="python"),
            remediation_registration="NOT_INCLUDED",
        )
        response_sha256 = provider_authored_content_sha256_v234(authored)
        trace = build_provider_trace_v234(
            provider_mode=provider_mode,
            request_sha256=request.request_sha256,
            response_sha256=response_sha256,
            provider_calls=provider_calls,
            protocol_repairs=protocol_repairs,
            transport_retries=transport_retries,
        )
        return build_formal_registration_draft_v234(
            authorization_id=authorization_context.authorization.authorization_id,
            shadow_fault_id=shadow.shadow_fault_id,
            registration_seed_sha256=authorization_context.registration_seed.seed_sha256,
            core_ontology_snapshot_sha256=(
                authorization_context.core_ontology_snapshot.snapshot_sha256
            ),
            content=content,
            provider_trace=trace,
        )

    def _call_provider(
        self,
        request: RegistrationDraftProviderRequestV234,
    ) -> tuple[ProviderAuthoredRegistrationDraftV234, int, int, int]:
        assert self.transport is not None
        total_transport_retries = 0
        provider_calls = 0
        repair_issue_codes: tuple[str, ...] = ()
        for repair_ordinal in range(MAX_PROTOCOL_REPAIRS_V23 + 1):
            body = _request_body_v234(
                request,
                repair_ordinal=repair_ordinal,
                repair_issue_codes=repair_issue_codes,
            )
            raw: str | None = None
            for retry in range(MAX_EXACT_TRANSPORT_RETRIES_V23 + 1):
                try:
                    provider_calls += 1
                    raw = self.transport(body)
                    break
                except DiscoveryProviderTransportErrorV23 as exc:
                    if not exc.retryable or retry == MAX_EXACT_TRANSPORT_RETRIES_V23:
                        raise
                    total_transport_retries += 1
            assert raw is not None
            try:
                authored = _parse_provider_content_v234(raw)
                # Build the public contract here as a protocol-safety check. The
                # deterministic semantic validator intentionally remains outside.
                RegistrationDraftContentV234(
                    **authored.model_dump(mode="python"),
                    remediation_registration="NOT_INCLUDED",
                )
                if authored.implementation_mode is RegistrationImplementationModeV234.DECLARATIVE_READY:
                    if (
                        not authored.predicates
                        or not authored.support_clauses
                        or authored.unresolved_engineering_questions
                        or any(
                            item.implementation_mode
                            is PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION
                            for item in authored.predicates
                        )
                    ):
                        raise ValueError("DECLARATIVE_MODE_BINDING_INVALID")
                elif authored.implementation_mode is RegistrationImplementationModeV234.ENGINEERING_REQUIRED:
                    if (
                        not authored.unresolved_engineering_questions
                        or not any(
                            item.implementation_mode
                            is PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION
                            and item.extraction_rule is None
                            for item in authored.predicates
                        )
                    ):
                        raise ValueError("ENGINEERING_MODE_BINDING_INVALID")
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                if repair_ordinal == MAX_PROTOCOL_REPAIRS_V23:
                    raise DiscoveryProviderProtocolFailureV23(
                        "registration Provider exhausted two protocol repairs"
                    ) from exc
                repair_issue_codes = _safe_protocol_issue_codes_v234(exc)
                continue
            return (
                authored,
                provider_calls,
                repair_ordinal,
                total_transport_retries,
            )
        raise AssertionError("unreachable registration Provider protocol state")


def _safe_protocol_issue_codes_v234(exc: Exception) -> tuple[str, ...]:
    rendered = str(exc).casefold()
    rules = (
        ("display name", "DISPLAY_NAME_CANONICALIZATION_REQUIRED"),
        ("not canonical", "CANONICAL_ORDER_REQUIRED"),
        ("vacuous metric threshold", "POSITIVE_THRESHOLD_REQUIRED"),
        ("implementation_mode", "IMPLEMENTATION_MODE_ENUM_REQUIRED"),
        ("declarative-ready draft", "DECLARATIVE_MODE_BINDING_INVALID"),
        ("declarative_mode_binding_invalid", "DECLARATIVE_MODE_BINDING_INVALID"),
        ("engineering-required draft", "ENGINEERING_MODE_BINDING_INVALID"),
        ("engineering_mode_binding_invalid", "ENGINEERING_MODE_BINDING_INVALID"),
    )
    codes = tuple(sorted({code for token, code in rules if token in rendered}))
    return codes or ("PRIOR_RESPONSE_PROTOCOL_INVALID",)


__all__ = (
    "AcceptedEvidenceSummaryV234",
    "AcceptedReportProjectionSourceV234",
    "AcceptedReportProjectionV234",
    "ProviderAuthoredRegistrationDraftV234",
    "ProviderCoreOntologyViewV234",
    "OpenAICompatibleRegistrationDraftTransportV234",
    "REGISTRATION_DRAFT_SYSTEM_PROMPT_V234",
    "RegistrationDraftProviderRequestV234",
    "RegistrationDraftProviderV234",
    "RootOwnershipV234",
    "build_provider_core_ontology_view_v234",
    "build_registration_draft_provider_request_v234",
    "project_development_report_v234",
)
