"""Minimal Provider synthesis protocol for runtime-owned DTA v2.3.3 reports."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts_v231 import ReviewRecommendationV231
from ecomsre.dta_v2.v23.contracts_v233 import (
    DiscoverySynthesisOutcomeV233,
    DiscoverySynthesisRequestV233,
    DiscoverySynthesisResponseV233,
    ResidualAnomalySummaryV233,
    RuntimeHypothesisV233,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
    MAX_EXACT_TRANSPORT_RETRIES_V23,
    MAX_PROTOCOL_REPAIRS_V23,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    OpenAICompatibleDiscoveryTransportV231,
)
from ecomsre.dta_v2.v23.domain_projection_v233 import DomainProjectionV233
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDecisionV233,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233 = """You are a read-only incident narrative synthesizer.
The runtime has already selected the root, projected the broad domain, bound the
evidence, and evaluated the contradiction guard. Choose only among the supplied
hypothesis IDs and write a bounded provisional explanation. Return exactly the
small response contract. Do not emit or alter root services, domains, evidence
references, anomaly IDs, confidence, action authority, commands, URLs, Docker,
Runbook fields, or remediation instructions."""


def _plain_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        if isinstance(result, dict):
            return result
    raise ValueError("v2.3.3 shadow projection is not a mapping")


def build_discovery_synthesis_request_v233(
    *,
    graph: ResidualEvidenceGraphV23,
    projection: DomainProjectionV233,
    guard: IrreconcilableGuardDecisionV233,
    hypotheses: tuple[RuntimeHypothesisV233, ...],
    unresolved_dimensions: tuple[str, ...],
    top_shadow_matches: tuple[object, ...],
) -> DiscoverySynthesisRequestV233:
    if projection.selected_root_service is None:
        raise ValueError("v2.3.3 Provider request lacks a runtime-selected root")
    by_id = {item.anomaly_id: item for item in graph.generic_anomalies}
    summaries = tuple(
        ResidualAnomalySummaryV233(
            anomaly_id=anomaly_id,
            kind=by_id[anomaly_id].kind.value,
            source=by_id[anomaly_id].source.value,
            service=by_id[anomaly_id].service,
            related_services=by_id[anomaly_id].related_services,
            summary=by_id[anomaly_id].summary,
            evidence_refs=by_id[anomaly_id].evidence_refs,
        )
        for anomaly_id in graph.residual_anomaly_ids
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.discovery-synthesis-request.v1",
        "runtime_selected_root_service": projection.selected_root_service,
        "runtime_domain_projection": projection,
        "competing_hypotheses": tuple(
            sorted(hypotheses, key=lambda item: item.hypothesis_id)
        ),
        "residual_anomaly_summaries": summaries,
        "contradiction_witness_summary": guard.witnesses,
        "guard_decision": guard,
        "unresolved_dimensions": tuple(sorted(set(unresolved_dimensions))),
        "top_shadow_matches": tuple(
            _plain_mapping(item) for item in top_shadow_matches
        ),
        "validation_graph": graph,
    }
    draft = DiscoverySynthesisRequestV233.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return DiscoverySynthesisRequestV233.model_validate(
        {
            **payload,
            "request_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"request_sha256"})
            ),
        }
    )


def deterministic_synthesis_response_v233(
    *,
    request: DiscoverySynthesisRequestV233,
) -> DiscoverySynthesisResponseV233:
    """Exercise the exact Provider boundary without a network call."""

    preferred = sorted(
        request.competing_hypotheses,
        key=lambda item: (-item.relative_support_score, item.hypothesis_id),
    )[0]
    alternatives = tuple(
        sorted(
            item.hypothesis_id
            for item in request.competing_hypotheses
            if item.hypothesis_id != preferred.hypothesis_id
        )
    )
    return DiscoverySynthesisResponseV233(
        preferred_hypothesis_id=preferred.hypothesis_id,
        provisional_mechanism_label=preferred.provisional_label,
        mechanism_description=(
            "Runtime-bound evidence favors the selected hypothesis while the "
            "listed unresolved dimensions remain subject to human review."
        ),
        alternative_hypothesis_ids=alternatives,
        unresolved_questions=preferred.unresolved_questions,
        recommended_next_observations=tuple(
            sorted(
                f"Collect bounded evidence for {item.casefold()}"
                for item in request.unresolved_dimensions
            )
        ),
        review_recommendation=ReviewRecommendationV231.REQUEST_MORE_EVIDENCE,
    )


def provider_response_payload_v233(
    response: DiscoverySynthesisResponseV233,
) -> dict[str, Any]:
    return response.model_dump(mode="json")


_RESPONSE_FIELDS_V233 = frozenset(DiscoverySynthesisResponseV233.model_fields)


class OpenAICompatibleDiscoveryTransportV233(
    OpenAICompatibleDiscoveryTransportV231
):
    """Force the minimal v2.3.3 synthesis schema on its Provider calls."""

    _v233_mode: bool = False

    @staticmethod
    def _tool() -> dict[str, object]:
        if not OpenAICompatibleDiscoveryTransportV233._v233_mode:
            prior_v231_mode = OpenAICompatibleDiscoveryTransportV231._v231_mode
            OpenAICompatibleDiscoveryTransportV231._v231_mode = (
                OpenAICompatibleDiscoveryTransportV233._v231_mode
            )
            try:
                return OpenAICompatibleDiscoveryTransportV231._tool()
            finally:
                OpenAICompatibleDiscoveryTransportV231._v231_mode = (
                    prior_v231_mode
                )
        schema = DiscoverySynthesisResponseV233.model_json_schema()
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": "submit_provisional_incident_report",
                "description": (
                    "Submit only the minimal runtime-bound v2.3.3 narrative synthesis."
                ),
                "strict": False,
                "parameters": schema,
            },
        }

    def __call__(self, body: str) -> str:
        parsed = json.loads(body)
        self._v233_mode = (
            isinstance(parsed, dict)
            and parsed.get("system") == DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233
        )
        type(self)._v233_mode = self._v233_mode
        try:
            return super().__call__(body)
        finally:
            self._v233_mode = False
            type(self)._v233_mode = False


def _request_body(
    request: DiscoverySynthesisRequestV233,
    *,
    repair_ordinal: int,
) -> str:
    body: dict[str, Any] = {
        "system": DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233,
        "request": request.model_dump(mode="json"),
        "response_contract": {
            "fields": tuple(sorted(_RESPONSE_FIELDS_V233)),
            "format": "one JSON object only",
            "runtime_owned_fields_forbidden": (
                "root",
                "domain",
                "evidence_refs",
                "anomaly_ids",
                "confidence",
                "action_authority",
            ),
        },
    }
    if repair_ordinal:
        body["protocol_repair"] = {
            "ordinal": repair_ordinal,
            "instruction": (
                "The prior response failed the minimal v2.3.3 synthesis contract. "
                "Return exactly the required fields and preserve supplied IDs."
            ),
        }
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _parse_response(
    *,
    raw: str,
    request: DiscoverySynthesisRequestV233,
) -> DiscoverySynthesisResponseV233:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("v2.3.3 Provider response is not one JSON object")
    if set(value) != _RESPONSE_FIELDS_V233:
        raise ValueError("v2.3.3 Provider response fields differ")
    for field in (
        "alternative_hypothesis_ids",
        "unresolved_questions",
        "recommended_next_observations",
    ):
        items = value[field]
        if not isinstance(items, list) or not all(
            isinstance(item, str) for item in items
        ):
            raise ValueError(f"v2.3.3 Provider response {field} is not text")
        value[field] = sorted(set(items))
    response = DiscoverySynthesisResponseV233.model_validate_json(
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    )
    ids = {item.hypothesis_id for item in request.competing_hypotheses}
    if response.preferred_hypothesis_id not in ids:
        raise ValueError("v2.3.3 Provider selected an unknown hypothesis")
    if not set(response.alternative_hypothesis_ids).issubset(ids):
        raise ValueError("v2.3.3 Provider emitted an unknown alternative")
    return response


def call_discovery_provider_v233(
    *,
    request: DiscoverySynthesisRequestV233,
    transport: Callable[[str], str],
) -> DiscoverySynthesisOutcomeV233:
    """Call one Provider with at most two repairs and three exact retries."""

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
            synthesis = _parse_response(raw=raw, request=request)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            if repair_ordinal == MAX_PROTOCOL_REPAIRS_V23:
                raise DiscoveryProviderProtocolFailureV23(
                    "provider exhausted two v2.3.3 protocol repairs"
                ) from exc
            continue
        return DiscoverySynthesisOutcomeV233(
            schema_version="dta-v233.discovery-synthesis-outcome.v1",
            synthesis=synthesis,
            protocol_repairs=repair_ordinal,
            transport_retries=total_transport_retries,
            provider_calls=provider_calls,
        )
    raise AssertionError("unreachable v2.3.3 Provider protocol state")


__all__ = (
    "DISCOVERY_SYNTHESIS_SYSTEM_PROMPT_V233",
    "OpenAICompatibleDiscoveryTransportV233",
    "build_discovery_synthesis_request_v233",
    "call_discovery_provider_v233",
    "deterministic_synthesis_response_v233",
    "provider_response_payload_v233",
)
