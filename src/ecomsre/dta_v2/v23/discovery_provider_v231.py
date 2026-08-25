"""Provider projection for evidence-bound DTA v2.3.1 competing reports."""

from __future__ import annotations

import json
from typing import Any, Callable, Literal, Mapping

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v231 import ConflictAssessmentV231
from ecomsre.dta_v2.v23.contracts_v231 import (
    CompetingHypothesisSetV231,
    ProvisionalIncidentReportV231,
    build_provider_report_v231,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
    MAX_EXACT_TRANSPORT_RETRIES_V23,
    MAX_PROTOCOL_REPAIRS_V23,
)
from ecomsre.dta_v2.v23.ontology_view import ActiveOntologyViewV23, provider_ontology_payload_v23
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


DISCOVERY_SYSTEM_PROMPT_V231 = """You are a read-only open-world incident discovery assistant.
Use only the supplied candidates, residual evidence, conflict assessment, and
evidence-bound competing hypotheses. Do not force a single mechanism when the
evidence supports multiple plausible interpretations. Return the leading
interpretation, alternatives, contradictions, and the next observation that would
separate them. Preserve every supplied hypothesis ID, anomaly ID, and evidence ref.
The output is provisional and cannot authorize remediation. action_authority must be
NONE. Return one JSON object and no commands, URLs, shell, Docker, Runbook, or writes."""


class DiscoveryProviderRequestV231(DtaModelV22):
    schema_version: Literal["dta-v231.discovery-provider-request.v1"]
    candidate_services: tuple[str, ...]
    active_ontology: dict[str, Any]
    generic_anomalies: tuple[dict[str, Any], ...]
    residual_graph: dict[str, Any]
    conflict_assessment: dict[str, Any]
    competing_hypotheses: tuple[dict[str, Any], ...] = Field(min_length=2, max_length=4)
    top_shadow_matches: tuple[dict[str, Any], ...] = Field(max_length=3)
    validation_graph: ResidualEvidenceGraphV23 = Field(exclude=True, repr=False)
    validation_assessment: ConflictAssessmentV231 = Field(exclude=True, repr=False)
    validation_hypothesis_set: CompetingHypothesisSetV231 = Field(
        exclude=True,
        repr=False,
    )
    request_sha256: str

    @model_validator(mode="after")
    def require_request(self) -> "DiscoveryProviderRequestV231":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("v2.3.1 Provider candidates are not canonical")
        if self.validation_graph.candidate_services != self.candidate_services:
            raise ValueError("v2.3.1 Provider graph candidates differ")
        if self.residual_graph.get("graph_sha256") != self.validation_graph.graph_sha256:
            raise ValueError("v2.3.1 Provider graph binding differs")
        if (
            self.conflict_assessment.get("assessment_sha256")
            != self.validation_assessment.assessment_sha256
        ):
            raise ValueError("v2.3.1 Provider assessment binding differs")
        if tuple(self.competing_hypotheses) != tuple(
            item.model_dump(mode="json")
            for item in self.validation_hypothesis_set.hypotheses
        ):
            raise ValueError("v2.3.1 Provider hypothesis binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("v2.3.1 Provider request digest differs")
        return self


class DiscoveryProviderOutcomeV231(DtaModelV22):
    schema_version: Literal["dta-v231.discovery-provider-outcome.v1"]
    report: ProvisionalIncidentReportV231
    protocol_repairs: StrictInt = Field(ge=0, le=MAX_PROTOCOL_REPAIRS_V23)
    transport_retries: StrictInt = Field(
        ge=0,
        le=MAX_EXACT_TRANSPORT_RETRIES_V23 * (MAX_PROTOCOL_REPAIRS_V23 + 1),
    )
    provider_calls: StrictInt = Field(ge=1)


def _plain_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        if isinstance(result, dict):
            return result
    raise ValueError("v2.3.1 Provider projection is not a mapping")


def build_discovery_provider_request_v231(
    *,
    active_ontology: ActiveOntologyViewV23,
    graph: ResidualEvidenceGraphV23,
    assessment: ConflictAssessmentV231,
    hypothesis_set: CompetingHypothesisSetV231,
    top_shadow_matches: tuple[object, ...],
) -> DiscoveryProviderRequestV231:
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.discovery-provider-request.v1",
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
            if item.anomaly_id in set(graph.residual_anomaly_ids)
        ),
        "residual_graph": {
            "graph_sha256": graph.graph_sha256,
            "residual_anomaly_ids": graph.residual_anomaly_ids,
            "source_coverage": tuple(
                item.model_dump(mode="json") for item in graph.source_coverage
            ),
            "healthy_runtime_services": graph.healthy_runtime_services,
            "contrastive_target_present": graph.contrastive_target_present,
        },
        "conflict_assessment": assessment.model_dump(mode="json"),
        "competing_hypotheses": tuple(
            item.model_dump(mode="json") for item in hypothesis_set.hypotheses
        ),
        "top_shadow_matches": tuple(_plain_mapping(item) for item in top_shadow_matches),
        "validation_graph": graph,
        "validation_assessment": assessment,
        "validation_hypothesis_set": hypothesis_set,
    }
    draft = DiscoveryProviderRequestV231.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return DiscoveryProviderRequestV231.model_validate(
        {
            **payload,
            "request_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"request_sha256"})
            ),
        }
    )


def provider_response_payload_v231(
    report: ProvisionalIncidentReportV231,
) -> dict[str, Any]:
    return report.model_dump(
        mode="json",
        exclude={"schema_version", "report_id", "report_sha256"},
    )


_RESPONSE_FIELDS_V231 = frozenset(
    set(ProvisionalIncidentReportV231.model_fields)
    - {"schema_version", "report_id", "report_sha256"}
)


def _request_body(
    request: DiscoveryProviderRequestV231,
    *,
    repair_ordinal: int,
) -> str:
    body: dict[str, Any] = {
        "system": DISCOVERY_SYSTEM_PROMPT_V231,
        "request": request.model_dump(mode="json"),
        "response_contract": {
            "uncertainty_mode": "COMPETING_HYPOTHESES",
            "action_authority": "NONE",
            "format": "one JSON object only",
        },
    }
    if repair_ordinal:
        body["protocol_repair"] = {
            "ordinal": repair_ordinal,
            "instruction": (
                "The prior response failed the v2.3.1 report contract. Return one "
                "corrected object without changing supplied IDs or evidence refs."
            ),
        }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_report(
    *,
    raw: str,
    request: DiscoveryProviderRequestV231,
) -> ProvisionalIncidentReportV231:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("v2.3.1 Provider response is not one JSON object")
    if set(value) != _RESPONSE_FIELDS_V231:
        raise ValueError("v2.3.1 Provider response fields differ")
    return build_provider_report_v231(
        response_payload=value,
        graph=request.validation_graph,
        hypothesis_set=request.validation_hypothesis_set,
    )


def call_discovery_provider_v231(
    *,
    request: DiscoveryProviderRequestV231,
    transport: Callable[[str], str],
) -> DiscoveryProviderOutcomeV231:
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
            except DiscoveryProviderTransportErrorV23 as exc:
                if not exc.retryable or retry == MAX_EXACT_TRANSPORT_RETRIES_V23:
                    raise
                total_transport_retries += 1
        assert raw is not None
        try:
            report = _parse_report(raw=raw, request=request)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            if repair_ordinal == MAX_PROTOCOL_REPAIRS_V23:
                raise DiscoveryProviderProtocolFailureV23(
                    "provider exhausted two v2.3.1 protocol repairs"
                ) from exc
            continue
        return DiscoveryProviderOutcomeV231(
            schema_version="dta-v231.discovery-provider-outcome.v1",
            report=report,
            protocol_repairs=repair_ordinal,
            transport_retries=total_transport_retries,
            provider_calls=provider_calls,
        )
    raise AssertionError("unreachable v2.3.1 Provider protocol state")


__all__ = (
    "DISCOVERY_SYSTEM_PROMPT_V231",
    "DiscoveryProviderOutcomeV231",
    "DiscoveryProviderRequestV231",
    "build_discovery_provider_request_v231",
    "call_discovery_provider_v231",
    "provider_response_payload_v231",
)
