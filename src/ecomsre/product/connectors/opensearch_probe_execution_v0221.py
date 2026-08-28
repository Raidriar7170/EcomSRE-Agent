"""Bounded variant-aware OpenSearch probe execution for Product v0.2.2.1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from urllib.parse import quote

from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchHttpErrorCodeV0221,
    OpenSearchHttpErrorEnvelopeV0221,
    OpenSearchHttpErrorV0221,
    OpenSearchProbeClientV0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeChangeReasonV0221,
    OpenSearchProbeEndpointKindV0221,
    OpenSearchProbePlanVariantV0221,
    OpenSearchProbeRequestAttemptV0221,
    OpenSearchProbeRequestPlanV0221,
    OpenSearchProbeRequestV0221,
    OpenSearchProbeSessionLedgerV0221,
    build_probe_request_plan_v0221,
    select_next_request_plan_variant_v0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchProfileResolutionV0221,
    build_empirical_query_verification_v0221,
    build_profile_verification_body_v0221,
    build_service_aggregation_body_v0221,
    build_timestamp_range_body_v0221,
    resolve_normalization_profile_v0221,
)
from ecomsre.product.connectors.opensearch_probe_v022 import (
    OpenSearchFieldCapsSnapshotV022,
    OpenSearchMappingSnapshotV022,
    OpenSearchSampleShapeSummaryV022,
    parse_field_caps_v022,
    parse_mapping_v022,
    summarize_sample_shapes_v022,
)


@dataclass(frozen=True)
class OpenSearchProbeExecutionV0221:
    plans: tuple[OpenSearchProbeRequestPlanV0221, ...]
    attempts: tuple[OpenSearchProbeRequestAttemptV0221, ...]
    safe_error_envelopes: tuple[OpenSearchHttpErrorEnvelopeV0221, ...]
    raw_error_bodies: tuple[bytes, ...]
    raw_response_bodies: tuple[tuple[str, bytes], ...]
    mapping: OpenSearchMappingSnapshotV022
    field_caps: OpenSearchFieldCapsSnapshotV022 | None
    samples: tuple[Mapping[str, object], ...]
    sample_shapes: OpenSearchSampleShapeSummaryV022
    sample_response: object
    resolution: OpenSearchProfileResolutionV0221
    request_count: int
    changed_plan_count: int
    transport_retry_count: int


def _field_caps_allowlist_v0221(
    mapping: OpenSearchMappingSnapshotV022,
) -> tuple[str, ...]:
    tokens = (
        "body",
        "message",
        "observedtimestamp",
        "service",
        "severity",
        "timestamp",
        "timeunixnano",
        "trace",
    )
    selected = tuple(
        path
        for path in sorted(mapping.fields)
        if any(token in path.lower().replace("_", "") for token in tokens)
    )
    if not selected or len(selected) > 64:
        raise ValueError("OpenSearch Field Caps candidate allowlist is invalid")
    return selected


def _select_query_fields_v0221(
    *,
    mapping: OpenSearchMappingSnapshotV022,
    field_caps: OpenSearchFieldCapsSnapshotV022 | None,
) -> tuple[str, str, str]:
    timestamp_candidates = tuple(
        path
        for path, item in mapping.fields.items()
        if item.mapping_type in {"date", "date_nanos"}
        and (
            "timestamp" in path.lower().replace("_", "")
            or "timeunixnano" in path.lower().replace("_", "")
        )
    )
    service_sources = tuple(
        path
        for path, item in mapping.fields.items()
        if "service" in path.lower().replace("_", "")
        and "name" in path.lower().replace("_", "")
        and item.multi_field_of is None
        and item.mapping_type in {"keyword", "text"}
    )
    if not timestamp_candidates or not service_sources:
        raise ValueError("OpenSearch Mapping lacks required query candidates")
    timestamp = sorted(
        timestamp_candidates,
        key=lambda path: (
            path not in {"observedTimestamp", "observed.timestamp", "@timestamp"},
            path,
        ),
    )[0]
    service_source = sorted(
        service_sources,
        key=lambda path: (path != "resource.service.name", path),
    )[0]
    if field_caps is not None:
        service_queries = tuple(
            path
            for path, capability in field_caps.fields.items()
            if capability.searchable
            and capability.aggregatable
            and "service" in path.lower().replace("_", "")
            and "name" in path.lower().replace("_", "")
        )
    else:
        service_queries = tuple(
            path
            for path, item in mapping.fields.items()
            if item.mapping_type == "keyword"
            and (
                path == service_source
                or item.multi_field_of == service_source
            )
        )
    preferred = f"{service_source}.keyword"
    ordered_queries = sorted(
        service_queries,
        key=lambda path: (path != preferred, path != service_source, path),
    )
    if not ordered_queries:
        raise ValueError("OpenSearch Mapping lacks a keyword-compatible service field")
    return timestamp, service_source, ordered_queries[0]


def _request_by_id_v0221(
    plan: OpenSearchProbeRequestPlanV0221,
    request_id: str,
) -> OpenSearchProbeRequestV0221:
    requests = (
        plan.mapping_request,
        *(() if plan.field_caps_request is None else (plan.field_caps_request,)),
        *plan.field_mapping_requests,
        *(() if plan.aggregation_request is None else (plan.aggregation_request,)),
        *plan.sample_requests,
    )
    matches = tuple(request for request in requests if request.request_id == request_id)
    if len(matches) != 1:
        raise ValueError("OpenSearch request-plan lookup differs")
    return matches[0]


def _run_request_v0221(
    *,
    client: OpenSearchProbeClientV0221,
    plan: OpenSearchProbeRequestPlanV0221,
    request: OpenSearchProbeRequestV0221,
    path: str,
    json_body: object,
    maximum_transport_retries: int,
) -> tuple[object, bytes]:
    payload, raw, _ = client.request_json_with_transport_retries(
        maximum_transport_retries=maximum_transport_retries,
        plan_id=plan.plan_id,
        request_id=request.request_id,
        method=request.method,
        endpoint_kind=request.endpoint_kind,
        path=path,
        path_template=request.path_template,
        query_parameters=request.query_parameters,
        json_body=json_body,
    )
    return payload, raw


def _change_reason_v0221(
    *,
    envelope: OpenSearchHttpErrorEnvelopeV0221,
    next_variant: OpenSearchProbePlanVariantV0221,
) -> OpenSearchProbeChangeReasonV0221:
    if next_variant is OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY:
        if (
            envelope.safe_error_code
            is OpenSearchHttpErrorCodeV0221.OPENSEARCH_METHOD_NOT_ALLOWED
        ):
            return OpenSearchProbeChangeReasonV0221.FIELD_CAPS_METHOD_NOT_ALLOWED
        return OpenSearchProbeChangeReasonV0221.FIELD_CAPS_GET_HTTP_400
    return {
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_PERMISSION_DENIED: (
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_PERMISSION_DENIED
        ),
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_ENDPOINT_NOT_FOUND: (
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_ENDPOINT_NOT_FOUND
        ),
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_FIELD_CAPS_UNSUPPORTED: (
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_UNSUPPORTED
        ),
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_PARAMETER_INVALID: (
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_UNSUPPORTED
        ),
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID: (
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_UNSUPPORTED
        ),
    }.get(
        envelope.safe_error_code,
        OpenSearchProbeChangeReasonV0221.FIELD_CAPS_UNSUPPORTED,
    )


def _sample_sources_v0221(payload: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(payload, Mapping):
        raise ValueError("OpenSearch sample response is invalid")
    hits = payload.get("hits")
    rows = hits.get("hits") if isinstance(hits, Mapping) else None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise ValueError("OpenSearch bounded sample rows are invalid")
    output: list[Mapping[str, object]] = []
    for row in rows:
        source = row.get("_source") if isinstance(row, Mapping) else None
        if not isinstance(source, Mapping):
            raise ValueError("OpenSearch bounded sample source is invalid")
        output.append(source)
    return tuple(output)


def _checkout_aliases_v0221(
    payload: object,
    configured: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        raise ValueError("OpenSearch service aggregation response is invalid")
    aggregations = payload.get("aggregations")
    services = (
        aggregations.get("services")
        if isinstance(aggregations, Mapping)
        else None
    )
    buckets = services.get("buckets") if isinstance(services, Mapping) else None
    if not isinstance(buckets, list):
        raise ValueError("OpenSearch service aggregation buckets are invalid")
    observed = tuple(
        str(bucket["key"])
        for bucket in buckets
        if isinstance(bucket, Mapping)
        and isinstance(bucket.get("key"), str)
        and "checkout"
        in str(bucket["key"]).lower().replace("-", "").replace("_", "")
    )
    aliases = tuple(sorted(set(configured) | set(observed)))
    if not aliases:
        raise ValueError("OpenSearch checkout service was not observed")
    return aliases


def _targeted_sample_body_v0221(
    *,
    timestamp_field: str,
    service_query_field: str,
    checkout_aliases: tuple[str, ...],
    started_at: str,
    ended_at: str,
    maximum_sample_documents: int,
) -> dict[str, object]:
    body = build_profile_verification_body_v0221(
        service_query_field=service_query_field,
        timestamp_query_field=timestamp_field,
        checkout_aliases=checkout_aliases,
        started_at=started_at,
        ended_at=ended_at,
    )
    body["size"] = maximum_sample_documents
    body["sort"] = [{timestamp_field: {"order": "desc"}}]
    return body


def execute_probe_protocol_v0221(
    *,
    client: OpenSearchProbeClientV0221,
    index_pattern: str,
    checkout_aliases: tuple[str, ...],
    maximum_sample_documents: int,
    maximum_transport_retries: int,
    started_at: datetime,
    ended_at: datetime,
) -> OpenSearchProbeExecutionV0221:
    if (
        checkout_aliases != tuple(sorted(set(checkout_aliases)))
        or maximum_sample_documents != 5
        or maximum_transport_retries != 2
        or started_at >= ended_at
    ):
        raise ValueError("OpenSearch probe execution boundary differs")
    index = quote(index_pattern, safe="*,-_")
    mapping_path = f"/{index}/_mapping"
    field_caps_path = f"/{index}/_field_caps"
    search_path = f"/{index}/_search"
    ledger = OpenSearchProbeSessionLedgerV0221()
    raw_responses: list[tuple[str, bytes]] = []
    safe_envelopes: list[OpenSearchHttpErrorEnvelopeV0221] = []
    raw_errors: list[bytes] = []

    mapping_payload, mapping_raw, _ = client.request_json_with_transport_retries(
        maximum_transport_retries=maximum_transport_retries,
        plan_id="plan-a-field-caps-get-query",
        request_id="mapping",
        method="GET",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.MAPPING,
        path=mapping_path,
        path_template="/{index}/_mapping",
        query_parameters={},
        json_body=None,
    )
    raw_responses.append(("mapping.json", mapping_raw))
    mapping = parse_mapping_v022(mapping_payload)
    fields = _field_caps_allowlist_v0221(mapping)
    variant = OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY
    parent_plan_id: str | None = None
    change_reason = OpenSearchProbeChangeReasonV0221.INITIAL_OFFICIAL_PROTOCOL
    field_caps_payload: object | None = None
    field_caps_raw: bytes | None = None
    current_plan: OpenSearchProbeRequestPlanV0221
    while True:
        current_plan = build_probe_request_plan_v0221(
            variant=variant,
            fields=fields,
            parent_plan_id=parent_plan_id,
            change_reason_code=change_reason,
        )
        ledger.register_plan(current_plan)
        if variant is OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL:
            break
        field_caps_request = current_plan.field_caps_request
        if field_caps_request is None:
            raise ValueError("OpenSearch Field Caps request is absent")
        try:
            field_caps_payload, field_caps_raw = _run_request_v0221(
                client=client,
                plan=current_plan,
                request=field_caps_request,
                path=field_caps_path,
                json_body=None,
                maximum_transport_retries=maximum_transport_retries,
            )
            break
        except OpenSearchHttpErrorV0221 as error:
            safe_envelopes.append(error.envelope)
            raw_errors.append(error.response_body)
            next_variant = select_next_request_plan_variant_v0221(
                error.envelope,
                current_variant=variant,
            )
            if next_variant is None:
                raise
            parent_plan_id = current_plan.plan_id
            change_reason = _change_reason_v0221(
                envelope=error.envelope,
                next_variant=next_variant,
            )
            variant = next_variant
    field_caps = (
        parse_field_caps_v022(field_caps_payload)
        if field_caps_payload is not None
        else None
    )
    if field_caps_raw is not None:
        raw_responses.append(("field-caps.json", field_caps_raw))

    if variant is OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL:
        broad_request = _request_by_id_v0221(current_plan, "broad-sample")
        _, broad_raw = _run_request_v0221(
            client=client,
            plan=current_plan,
            request=broad_request,
            path=search_path,
            json_body={"size": maximum_sample_documents, "_source": True},
            maximum_transport_retries=maximum_transport_retries,
        )
        raw_responses.append(("broad-sample.json", broad_raw))

    timestamp_field, _, service_query_field = _select_query_fields_v0221(
        mapping=mapping,
        field_caps=field_caps,
    )
    aggregation_request = _request_by_id_v0221(
        current_plan,
        "service-aggregation",
    )
    aggregation_payload, aggregation_raw = _run_request_v0221(
        client=client,
        plan=current_plan,
        request=aggregation_request,
        path=search_path,
        json_body=build_service_aggregation_body_v0221(service_query_field),
        maximum_transport_retries=maximum_transport_retries,
    )
    raw_responses.append(("service-aggregation.json", aggregation_raw))
    effective_aliases = _checkout_aliases_v0221(
        aggregation_payload,
        checkout_aliases,
    )
    started_text = started_at.isoformat()
    ended_text = ended_at.isoformat()
    range_request = _request_by_id_v0221(
        current_plan,
        "timestamp-range-verification",
    )
    range_payload, range_raw = _run_request_v0221(
        client=client,
        plan=current_plan,
        request=range_request,
        path=search_path,
        json_body=build_timestamp_range_body_v0221(
            timestamp_field,
            started_at=started_text,
            ended_at=ended_text,
        ),
        maximum_transport_retries=maximum_transport_retries,
    )
    raw_responses.append(("timestamp-range.json", range_raw))
    sample_request = _request_by_id_v0221(current_plan, "checkout-sample")
    sample_payload, sample_raw = _run_request_v0221(
        client=client,
        plan=current_plan,
        request=sample_request,
        path=search_path,
        json_body=_targeted_sample_body_v0221(
            timestamp_field=timestamp_field,
            service_query_field=service_query_field,
            checkout_aliases=effective_aliases,
            started_at=started_text,
            ended_at=ended_text,
            maximum_sample_documents=maximum_sample_documents,
        ),
        maximum_transport_retries=maximum_transport_retries,
    )
    raw_responses.append(("checkout-sample.json", sample_raw))
    profile_request = _request_by_id_v0221(current_plan, "profile-verification")
    profile_payload, profile_raw = _run_request_v0221(
        client=client,
        plan=current_plan,
        request=profile_request,
        path=search_path,
        json_body=build_profile_verification_body_v0221(
            service_query_field=service_query_field,
            timestamp_query_field=timestamp_field,
            checkout_aliases=effective_aliases,
            started_at=started_text,
            ended_at=ended_text,
        ),
        maximum_transport_retries=maximum_transport_retries,
    )
    raw_responses.append(("profile-verification.json", profile_raw))
    samples = _sample_sources_v0221(sample_payload)
    sample_shapes = summarize_sample_shapes_v022(samples)
    empirical = build_empirical_query_verification_v0221(
        service_query_field=service_query_field,
        timestamp_query_field=timestamp_field,
        checkout_aliases=effective_aliases,
        service_aggregation_response=aggregation_payload,
        timestamp_range_response=range_payload,
        profile_verification_response=profile_payload,
    )
    resolution = resolve_normalization_profile_v0221(
        index_pattern=index_pattern,
        mapping=mapping,
        field_caps=field_caps,
        samples=samples,
        sample_shapes=sample_shapes,
        checkout_aliases=effective_aliases,
        empirical_verification=empirical,
    )
    for attempt in client.attempts:
        ledger.record_attempt(attempt)
    return OpenSearchProbeExecutionV0221(
        plans=tuple(ledger.plans),
        attempts=tuple(ledger.attempts),
        safe_error_envelopes=tuple(safe_envelopes),
        raw_error_bodies=tuple(raw_errors),
        raw_response_bodies=tuple(raw_responses),
        mapping=mapping,
        field_caps=field_caps,
        samples=samples,
        sample_shapes=sample_shapes,
        sample_response=sample_payload,
        resolution=resolution,
        request_count=client.request_count,
        changed_plan_count=len(ledger.plans),
        transport_retry_count=max(
            (attempt.transport_retry_count for attempt in ledger.attempts),
            default=0,
        ),
    )


__all__ = (
    "OpenSearchProbeExecutionV0221",
    "execute_probe_protocol_v0221",
)
