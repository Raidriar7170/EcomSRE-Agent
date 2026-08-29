"""Derive safe public structure and candidates from one frozen private capture."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchProfileCandidateSetV0222,
    OpenSearchProfileComponentCandidateV0222,
    OpenSearchProfileComponentKindV0222,
    build_component_candidate_v0222,
    build_profile_candidate_set_v0222,
)
from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureRequestKindV0222,
    OpenSearchCaptureStatusV0222,
    OpenSearchPublicStructuralSummaryV0222,
    OpenSearchSchemaCaptureBundleV0222,
    build_public_structural_summary_v0222,
)
from ecomsre.product.connectors.opensearch_probe_execution_v0221 import (
    _select_query_fields_v0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    build_empirical_query_verification_v0221,
)
from ecomsre.product.connectors.opensearch_probe_v022 import (
    OpenSearchCandidateRankingV022,
    OpenSearchFieldCapsSnapshotV022,
    parse_field_caps_v022,
    parse_mapping_v022,
    resolve_normalization_profile_v022,
    summarize_sample_shapes_v022,
)


@dataclass(frozen=True)
class OpenSearchCaptureAnalysisV0222:
    public_summary: OpenSearchPublicStructuralSummaryV0222
    candidate_set: OpenSearchProfileCandidateSetV0222
    component_candidates: tuple[OpenSearchProfileComponentCandidateV0222, ...]


def _payload_for_kind(
    *,
    private_root: Path,
    bundle: OpenSearchSchemaCaptureBundleV0222,
    kind: OpenSearchCaptureRequestKindV0222,
) -> tuple[object, str]:
    responses = tuple(
        response
        for response in bundle.responses
        if response.request_kind is kind
        and response.status is OpenSearchCaptureStatusV0222.PARSED
    )
    if not responses:
        raise ValueError(f"OpenSearch capture {kind.value} response count differs")
    response = responses[-1]
    raw = (private_root / response.response_object_ref).read_bytes()
    if len(raw) != response.response_byte_size:
        raise ValueError("OpenSearch capture response byte size differs")
    return json.loads(raw), response.response_object_ref


def _sample_sources(payload: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(payload, Mapping):
        raise ValueError("OpenSearch structural sample response is invalid")
    hits = payload.get("hits")
    rows = hits.get("hits") if isinstance(hits, Mapping) else None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise ValueError("OpenSearch structural sample rows are invalid")
    output: list[Mapping[str, object]] = []
    for row in rows:
        source = row.get("_source") if isinstance(row, Mapping) else None
        if not isinstance(source, Mapping):
            raise ValueError("OpenSearch structural sample source is invalid")
        output.append(source)
    return tuple(output)


def _types_for(
    field_caps: OpenSearchFieldCapsSnapshotV022,
    path: str,
) -> tuple[str, ...]:
    capability = field_caps.fields.get(path)
    return () if capability is None else capability.mapping_types


def _component_from_ranking(
    *,
    ranking: OpenSearchCandidateRankingV022,
    kind: OpenSearchProfileComponentKindV0222,
    alias: str,
    field_caps: OpenSearchFieldCapsSnapshotV022,
    supporting_refs: tuple[str, ...],
    query_status: str = "NOT_APPLICABLE",
) -> OpenSearchProfileComponentCandidateV0222:
    return build_component_candidate_v0222(
        component_alias=alias,
        kind=kind,
        accessor=ranking.field_path,
        encoding_or_mode="STRING",
        mapping_types=ranking.mapping_types,
        field_caps_types=_types_for(field_caps, ranking.field_path),
        sample_presence_count=ranking.sample_coverage,
        sample_parse_success_count=ranking.compatible_value_count,
        checkout_match_count=ranking.checkout_alias_match_count,
        query_verification_status=query_status,
        supporting_capture_refs=supporting_refs,
        contradicting_capture_refs=(),
    )


def analyze_capture_bundle_v0222(
    *,
    private_root: Path,
    bundle: OpenSearchSchemaCaptureBundleV0222,
    index_pattern: str,
    checkout_aliases: tuple[str, ...],
) -> OpenSearchCaptureAnalysisV0222:
    if not bundle.capture_completeness:
        raise ValueError("OpenSearch capture bundle is incomplete")
    resolved_payload, resolved_ref = _payload_for_kind(
        private_root=private_root,
        bundle=bundle,
        kind=OpenSearchCaptureRequestKindV0222.INDEX_RESOLUTION,
    )
    if not isinstance(resolved_payload, Mapping) or not resolved_payload.get("indices"):
        raise ValueError("OpenSearch resolved index response is empty")
    mapping_payload, mapping_ref = _payload_for_kind(
        private_root=private_root,
        bundle=bundle,
        kind=OpenSearchCaptureRequestKindV0222.MAPPING,
    )
    field_caps_payload, field_caps_ref = _payload_for_kind(
        private_root=private_root,
        bundle=bundle,
        kind=OpenSearchCaptureRequestKindV0222.FIELD_CAPS,
    )
    sample_payload, sample_ref = _payload_for_kind(
        private_root=private_root,
        bundle=bundle,
        kind=OpenSearchCaptureRequestKindV0222.STRUCTURAL_SAMPLE,
    )
    aggregation_payload, aggregation_ref = _payload_for_kind(
        private_root=private_root,
        bundle=bundle,
        kind=OpenSearchCaptureRequestKindV0222.SERVICE_AGGREGATION,
    )
    range_payload, range_ref = _payload_for_kind(
        private_root=private_root,
        bundle=bundle,
        kind=OpenSearchCaptureRequestKindV0222.TIMESTAMP_RANGE,
    )
    verification_payload, verification_ref = _payload_for_kind(
        private_root=private_root,
        bundle=bundle,
        kind=OpenSearchCaptureRequestKindV0222.PROFILE_VERIFICATION,
    )
    mapping = parse_mapping_v022(mapping_payload)
    field_caps = parse_field_caps_v022(field_caps_payload)
    samples = _sample_sources(sample_payload)
    sample_shapes = summarize_sample_shapes_v022(samples)
    base = resolve_normalization_profile_v022(
        index_pattern=index_pattern,
        mapping=mapping,
        field_caps=field_caps,
        samples=samples,
        sample_shapes=sample_shapes,
        checkout_aliases=checkout_aliases,
    )
    timestamp_query, service_source, service_query = _select_query_fields_v0221(
        mapping=mapping,
        field_caps=field_caps,
    )
    empirical = build_empirical_query_verification_v0221(
        service_query_field=service_query,
        timestamp_query_field=timestamp_query,
        checkout_aliases=checkout_aliases,
        service_aggregation_response=aggregation_payload,
        timestamp_range_response=range_payload,
        profile_verification_response=verification_payload,
    )
    if not empirical.checkout_service_observed:
        raise ValueError("OpenSearch capture did not verify checkout")

    common_refs = tuple(sorted({mapping_ref, field_caps_ref, sample_ref}))
    query_refs = tuple(sorted({aggregation_ref, range_ref, verification_ref}))
    components: list[OpenSearchProfileComponentCandidateV0222] = []
    timestamp_rankings = tuple(
        ranking
        for ranking in base.candidate_rankings["timestamp"]
        if ranking.field_path == timestamp_query
    )
    for ordinal, ranking in enumerate(timestamp_rankings):
        components.append(
            _component_from_ranking(
                ranking=ranking,
                kind=OpenSearchProfileComponentKindV0222.TIMESTAMP,
                alias=f"timestamp-{ordinal:02d}",
                field_caps=field_caps,
                supporting_refs=tuple(sorted({*common_refs, range_ref})),
            )
        )
    service_rankings = tuple(
        ranking
        for ranking in base.candidate_rankings["service"]
        if ranking.field_path.removesuffix(".keyword") == service_source
    )
    for ordinal, ranking in enumerate(service_rankings):
        components.append(
            _component_from_ranking(
                ranking=ranking,
                kind=OpenSearchProfileComponentKindV0222.SERVICE_SOURCE,
                alias=f"service-source-{ordinal:02d}",
                field_caps=field_caps,
                supporting_refs=tuple(sorted({*common_refs, aggregation_ref})),
            )
        )
    query_capability = field_caps.fields[service_query]
    components.append(
        build_component_candidate_v0222(
            component_alias="service-query-00",
            kind=OpenSearchProfileComponentKindV0222.SERVICE_QUERY,
            accessor=service_query,
            encoding_or_mode="KEYWORD_QUERY",
            mapping_types=query_capability.mapping_types,
            field_caps_types=query_capability.mapping_types,
            sample_presence_count=0,
            sample_parse_success_count=0,
            checkout_match_count=0,
            query_verification_status="PASS",
            supporting_capture_refs=query_refs,
            contradicting_capture_refs=(),
        )
    )
    for category, kind in (
        ("message", OpenSearchProfileComponentKindV0222.MESSAGE),
        ("severity", OpenSearchProfileComponentKindV0222.SEVERITY),
        ("trace_id", OpenSearchProfileComponentKindV0222.TRACE_ID),
    ):
        rankings = base.candidate_rankings[category]
        for ordinal, ranking in enumerate(rankings[:8]):
            components.append(
                _component_from_ranking(
                    ranking=ranking,
                    kind=kind,
                    alias=f"{category.replace('_', '-')}-{ordinal:02d}",
                    field_caps=field_caps,
                    supporting_refs=common_refs,
                )
            )
        if not rankings and kind in {
            OpenSearchProfileComponentKindV0222.SEVERITY,
            OpenSearchProfileComponentKindV0222.TRACE_ID,
        }:
            components.append(
                build_component_candidate_v0222(
                    component_alias=f"{category.replace('_', '-')}-optional",
                    kind=kind,
                    accessor="__OPTIONAL__",
                    encoding_or_mode="OPTIONAL",
                    mapping_types=(),
                    field_caps_types=(),
                    sample_presence_count=0,
                    sample_parse_success_count=0,
                    checkout_match_count=0,
                    query_verification_status="NOT_APPLICABLE",
                    supporting_capture_refs=(sample_ref,),
                    contradicting_capture_refs=(),
                )
            )
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=bundle.bundle_sha256,
        components=tuple(components),
    )
    mapping_types = {
        path: (field.mapping_type,) for path, field in sorted(mapping.fields.items())
    }
    field_caps_types = {
        path: capability.mapping_types
        for path, capability in sorted(field_caps.fields.items())
    }
    public_summary = build_public_structural_summary_v0222(
        bundle=bundle,
        json_path_inventory={
            path: ",".join(types)
            for path, types in sorted(sample_shapes.field_types.items())
        },
        mapping_types=mapping_types,
        field_caps_types=field_caps_types,
        presence_rates={
            path: (count, sample_shapes.sample_count)
            for path, count in sorted(sample_shapes.field_presence.items())
        },
        timestamp_parseability_counts={
            ranking.field_path: (
                ranking.compatible_value_count,
                ranking.sample_coverage,
            )
            for ranking in base.candidate_rankings["timestamp"]
        },
        service_alias_counts={
            ranking.field_path: ranking.checkout_alias_match_count
            for ranking in base.candidate_rankings["service"]
        },
        message_type_classes=tuple(
            sorted(
                {
                    item
                    for ranking in base.candidate_rankings["message"]
                    for item in ranking.mapping_types
                }
            )
        ),
        severity_type_classes=tuple(
            sorted(
                {
                    item
                    for ranking in base.candidate_rankings["severity"]
                    for item in ranking.mapping_types
                }
            )
        ),
        trace_id_type_classes=tuple(
            sorted(
                {
                    item
                    for ranking in base.candidate_rankings["trace_id"]
                    for item in ranking.mapping_types
                }
            )
        ),
        private_structural_shape_sha256=sample_shapes.sample_shape_sha256,
    )
    del resolved_ref
    return OpenSearchCaptureAnalysisV0222(
        public_summary=public_summary,
        candidate_set=candidate_set,
        component_candidates=tuple(components),
    )


__all__ = ("OpenSearchCaptureAnalysisV0222", "analyze_capture_bundle_v0222")
