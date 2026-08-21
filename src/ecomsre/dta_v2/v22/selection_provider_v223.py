"""v2.2.3 selection Provider with a case-scoped protocol-repair budget."""

from __future__ import annotations

from typing import Protocol

from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionProviderOutcomeV222,
    SelectionProviderProtocolFailureV222,
    SelectionProviderSemanticErrorV222,
    SelectionProviderV222,
    SelectionTurnRequestV222,
    _usage,
    parse_selection_response_v222,
)
from ecomsre.dta_v2.v22.simple_provider import ProviderTransportErrorV22


class SelectionProviderProtocolV223(Protocol):
    def complete_turn(
        self,
        *,
        request: SelectionTurnRequestV222,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> SelectionProviderOutcomeV222: ...


class SelectionProviderV223(SelectionProviderV222):
    def complete_turn(
        self,
        *,
        request: SelectionTurnRequestV222,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> SelectionProviderOutcomeV222:
        del run_id
        if not 0 <= max_protocol_repairs <= 2:
            raise ValueError("case-scoped protocol repair budget is out of bounds")
        provider_calls = retries = repairs = 0
        latency = 0.0
        usages: list[tuple[int, int, int]] = []
        repair_code: str | None = None
        while provider_calls < max_protocol_repairs + 1:
            payload = self._payload(request=request, repair_code=repair_code)
            provider_calls += 1
            try:
                response, request_retries, request_latency = self._post(payload)
            except ProviderTransportErrorV22 as error:
                retries += error.retry_count
                latency += error.latency_ms
                raise SelectionProviderProtocolFailureV222(
                    "TRANSPORT_FAILED",
                    provider_calls=provider_calls,
                    protocol_repairs=repairs,
                    transport_retry_count=retries,
                    input_tokens=sum(item[0] for item in usages),
                    output_tokens=sum(item[1] for item in usages),
                    total_tokens=sum(item[2] for item in usages),
                    latency_ms=latency,
                ) from error
            retries += request_retries
            latency += request_latency
            usages.append(_usage(response))
            try:
                decision = parse_selection_response_v222(
                    response,
                    aliases=request.aliases,
                )
            except SelectionProviderSemanticErrorV222 as error:
                repair_code = error.safe_code
                if repairs >= max_protocol_repairs:
                    break
                repairs += 1
                continue
            input_tokens = sum(item[0] for item in usages)
            output_tokens = sum(item[1] for item in usages)
            reported_total = sum(item[2] for item in usages)
            return SelectionProviderOutcomeV222(
                decision=decision,
                first_pass_protocol_success=repairs == 0,
                post_repair_protocol_success=True,
                protocol_repairs=repairs,
                provider_calls=provider_calls,
                transport_retry_count=retries,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=reported_total or input_tokens + output_tokens,
                latency_ms=latency,
            )
        input_tokens = sum(item[0] for item in usages)
        output_tokens = sum(item[1] for item in usages)
        reported_total = sum(item[2] for item in usages)
        raise SelectionProviderProtocolFailureV222(
            repair_code or "PROTOCOL_FAILED",
            provider_calls=provider_calls,
            protocol_repairs=repairs,
            transport_retry_count=retries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=reported_total or input_tokens + output_tokens,
            latency_ms=latency,
        )


__all__ = ("SelectionProviderProtocolV223", "SelectionProviderV223")
