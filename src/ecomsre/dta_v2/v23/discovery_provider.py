"""Provider projection and bounded protocol for provisional DTA v2.3 reports."""

from __future__ import annotations

import json
from typing import Any, Callable, Literal, Mapping

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    ProvisionalIncidentReportV23,
    build_provisional_report_v23,
)
from ecomsre.dta_v2.v23.discovery_router import NegativeCoverageLedgerV23
from ecomsre.dta_v2.v23.ontology_view import (
    ActiveOntologyViewV23,
    provider_ontology_payload_v23,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


MAX_PROTOCOL_REPAIRS_V23 = 2
MAX_EXACT_TRANSPORT_RETRIES_V23 = 3


DISCOVERY_SYSTEM_PROMPT_V23 = """You are a read-only open-world incident discovery assistant.
Use only the supplied candidate services, mechanism-independent anomalies, residual
evidence, active ontology projection, coverage, and negative coverage. Return one JSON
object matching the requested provisional report fields. This report is provisional:
it has no command, write, remediation, Docker, shell, URL, Runbook, or action authority.
Never invent evidence references or services. action_authority must be NONE."""


class DiscoveryProviderTransportErrorV23(RuntimeError):
    """A retryable failure before a response body was obtained."""


class DiscoveryProviderProtocolFailureV23(RuntimeError):
    """The bounded semantic repair allowance was exhausted."""


class DiscoveryProviderRequestV23(DtaModelV22):
    schema_version: Literal["dta-v23.discovery-provider-request.v1"]
    candidate_services: tuple[str, ...]
    active_ontology: dict[str, Any]
    generic_anomalies: tuple[dict[str, Any], ...]
    residual_graph: dict[str, Any]
    known_hypotheses: tuple[dict[str, Any], ...]
    source_coverage: tuple[dict[str, Any], ...]
    negative_coverage: tuple[dict[str, Any], ...]
    last_post_read_delta: dict[str, Any] | None
    top_shadow_matches: tuple[dict[str, Any], ...] = Field(max_length=3)
    validation_graph: ResidualEvidenceGraphV23 = Field(exclude=True, repr=False)
    request_sha256: str

    @model_validator(mode="after")
    def require_request(self) -> "DiscoveryProviderRequestV23":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("provider candidates are not canonical")
        if self.validation_graph.candidate_services != self.candidate_services:
            raise ValueError("provider validation graph candidates differ")
        if self.residual_graph.get("graph_sha256") != self.validation_graph.graph_sha256:
            raise ValueError("provider residual projection graph binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("provider request digest differs")
        return self


class DiscoveryProviderOutcomeV23(DtaModelV22):
    schema_version: Literal["dta-v23.discovery-provider-outcome.v1"]
    report: ProvisionalIncidentReportV23
    protocol_repairs: StrictInt = Field(ge=0, le=MAX_PROTOCOL_REPAIRS_V23)
    transport_retries: StrictInt = Field(
        ge=0,
        le=MAX_EXACT_TRANSPORT_RETRIES_V23 * (MAX_PROTOCOL_REPAIRS_V23 + 1),
    )
    provider_calls: StrictInt = Field(ge=1)


def _plain_mapping(value: object | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        if isinstance(result, dict):
            return result
    raise ValueError("provider projection value is not a mapping")


def build_discovery_provider_request_v23(
    *,
    active_ontology: ActiveOntologyViewV23,
    graph: ResidualEvidenceGraphV23,
    negative_coverage: NegativeCoverageLedgerV23,
    last_post_read_delta: object | None,
    top_shadow_matches: tuple[object, ...],
) -> DiscoveryProviderRequestV23:
    admitted = {item.hypothesis_id: item for item in graph.known_terminal_candidates}
    known = tuple(
        {
            "hypothesis_id": item.hypothesis_id,
            "target_service": item.target_service,
            "fault_domain": item.fault_domain.value,
            "accepted": item.hypothesis_id in admitted,
            "short_reason": (
                admitted[item.hypothesis_id].matched_clause_id
                if item.hypothesis_id in admitted
                else "active support not admitted"
            ),
        }
        for item in active_ontology.active_hypotheses
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.discovery-provider-request.v1",
        "candidate_services": graph.candidate_services,
        "active_ontology": provider_ontology_payload_v23(active_ontology),
        "generic_anomalies": tuple(
            {
                "anomaly_id": item.anomaly_id,
                "kind": item.kind.value,
                "source": item.source.value,
                "service": item.service,
                "related_services": item.related_services,
                "strength": item.strength.value,
                "summary": item.summary,
                "evidence_refs": item.evidence_refs,
            }
            for item in graph.generic_anomalies
        ),
        "residual_graph": {
            "graph_sha256": graph.graph_sha256,
            "explained_anomaly_ids": graph.explained_anomaly_ids,
            "residual_anomaly_ids": graph.residual_anomaly_ids,
            "contradicted_anomaly_ids": graph.contradicted_anomaly_ids,
            "explanation_coverage": graph.explanation_coverage,
            "healthy_runtime_services": graph.healthy_runtime_services,
            "contrastive_target_present": graph.contrastive_target_present,
        },
        "known_hypotheses": known,
        "source_coverage": tuple(
            {
                "source": item.source.value,
                "queried": item.queried,
                "covered_services": item.covered_services,
                "successful_observations": item.successful_observations,
                "failed_observations": item.failed_observations,
            }
            for item in graph.source_coverage
        ),
        "negative_coverage": tuple(
            {
                "source": item.source.value,
                "target_services": item.target_services,
                "outcome_class": item.outcome_class.value,
                "new_anomaly_ids": item.new_anomaly_ids,
            }
            for item in negative_coverage.entries
        ),
        "last_post_read_delta": _plain_mapping(last_post_read_delta),
        "top_shadow_matches": tuple(
            item
            for value in top_shadow_matches
            if (item := _plain_mapping(value)) is not None
        ),
        "validation_graph": graph,
    }
    draft = DiscoveryProviderRequestV23.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return DiscoveryProviderRequestV23.model_validate(
        {
            **payload,
            "request_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"request_sha256"})
            ),
        }
    )


def _request_body(
    request: DiscoveryProviderRequestV23,
    *,
    repair_ordinal: int,
) -> str:
    body: dict[str, Any] = {
        "system": DISCOVERY_SYSTEM_PROMPT_V23,
        "request": request.model_dump(mode="json"),
        "response_contract": {
            "terminal": (
                "UNREGISTERED_INCIDENT_SUSPECTED or "
                "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY"
            ),
            "action_authority": "NONE",
            "format": "one JSON object only",
        },
    }
    if repair_ordinal:
        body["protocol_repair"] = {
            "ordinal": repair_ordinal,
            "instruction": (
                "The prior response failed the provisional-report contract. "
                "Return one corrected JSON object using only supplied values."
            ),
        }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_report(
    *,
    raw: str,
    request: DiscoveryProviderRequestV23,
    memory: SalientEvidenceMemoryV22,
    graph: ResidualEvidenceGraphV23,
) -> ProvisionalIncidentReportV23:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("provider response is not one JSON object")
    allowed = {
        "terminal",
        "suspected_root_services",
        "affected_services",
        "broad_fault_domain",
        "provisional_mechanism_label",
        "mechanism_description",
        "observed_symptoms",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
        "unexplained_anomaly_ids",
        "alternative_hypotheses",
        "recommended_next_observations",
        "confidence",
        "action_authority",
    }
    if set(value) != allowed:
        raise ValueError("provider response fields differ from the short contract")
    residual_refs = {
        item.anomaly_id: item.evidence_refs
        for item in graph.generic_anomalies
        if item.anomaly_id in set(graph.residual_anomaly_ids)
    }
    return build_provisional_report_v23(
        terminal=str(value["terminal"]),  # type: ignore[arg-type]
        candidate_services=request.candidate_services,
        suspected_root_services=tuple(value["suspected_root_services"]),
        affected_services=tuple(value["affected_services"]),
        broad_fault_domain=ProvisionalFaultDomainV23(str(value["broad_fault_domain"])),
        provisional_mechanism_label=str(value["provisional_mechanism_label"]),
        mechanism_description=str(value["mechanism_description"]),
        observed_symptoms=tuple(value["observed_symptoms"]),
        supporting_evidence_refs=tuple(value["supporting_evidence_refs"]),
        contradicting_evidence_refs=tuple(value["contradicting_evidence_refs"]),
        unexplained_anomaly_ids=tuple(value["unexplained_anomaly_ids"]),
        alternative_hypotheses=tuple(value["alternative_hypotheses"]),
        recommended_next_observations=tuple(value["recommended_next_observations"]),
        confidence=float(value["confidence"]),
        memory=memory,
        residual_anomaly_refs=residual_refs,
    )


def call_discovery_provider_v23(
    *,
    request: DiscoveryProviderRequestV23,
    memory: SalientEvidenceMemoryV22,
    transport: Callable[[str], str],
) -> DiscoveryProviderOutcomeV23:
    """Call a local Provider transport with two repairs and three exact retries."""

    graph = _graph_from_request(request=request, memory=memory)
    total_transport_retries = 0
    provider_calls = 0
    for repair_ordinal in range(MAX_PROTOCOL_REPAIRS_V23 + 1):
        body = _request_body(request, repair_ordinal=repair_ordinal)
        raw: str | None = None
        for retry in range(MAX_EXACT_TRANSPORT_RETRIES_V23 + 1):
            try:
                provider_calls += 1
                raw = transport(body)
                break
            except DiscoveryProviderTransportErrorV23:
                if retry == MAX_EXACT_TRANSPORT_RETRIES_V23:
                    raise
                total_transport_retries += 1
        assert raw is not None
        try:
            report = _parse_report(
                raw=raw,
                request=request,
                memory=memory,
                graph=graph,
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            if repair_ordinal == MAX_PROTOCOL_REPAIRS_V23:
                raise DiscoveryProviderProtocolFailureV23(
                    "provider exhausted two protocol repairs"
                ) from exc
            continue
        return DiscoveryProviderOutcomeV23(
            schema_version="dta-v23.discovery-provider-outcome.v1",
            report=report,
            protocol_repairs=repair_ordinal,
            transport_retries=total_transport_retries,
            provider_calls=provider_calls,
        )
    raise AssertionError("unreachable provider protocol state")


def _graph_from_request(
    *,
    request: DiscoveryProviderRequestV23,
    memory: SalientEvidenceMemoryV22,
) -> ResidualEvidenceGraphV23:
    """Rebind request digest to its in-memory graph without exposing memory to Provider.

    The current caller stores the graph in a process-local cache attribute for the
    duration of one call. This keeps the transport payload small while validation
    still uses the authoritative typed graph.
    """

    graph = request.validation_graph
    if graph.candidate_services != request.candidate_services:
        raise ValueError("provider request candidates differ from graph")
    if not memory.memory_sha256:
        raise ValueError("provider validation memory lacks a digest")
    return graph


__all__ = (
    "DISCOVERY_SYSTEM_PROMPT_V23",
    "DiscoveryProviderOutcomeV23",
    "DiscoveryProviderProtocolFailureV23",
    "DiscoveryProviderRequestV23",
    "DiscoveryProviderTransportErrorV23",
    "MAX_EXACT_TRANSPORT_RETRIES_V23",
    "MAX_PROTOCOL_REPAIRS_V23",
    "build_discovery_provider_request_v23",
    "call_discovery_provider_v23",
)
