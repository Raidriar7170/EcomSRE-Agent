"""Evidence-driven OpenSearch profile resolution for Product v0.2.2.1."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
)
from ecomsre.product.connectors.opensearch_probe_v022 import (
    OpenSearchCandidateRankingV022,
    OpenSearchFieldCapabilityV022,
    OpenSearchFieldCapsSnapshotV022,
    OpenSearchMappingFieldV022,
    OpenSearchMappingSnapshotV022,
    OpenSearchProfileResolutionV022,
    OpenSearchSampleShapeSummaryV022,
    resolve_normalization_profile_v022,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchExtractionRuleV022,
    OpenSearchMessageExtractionV022,
    OpenSearchNormalizationProfileV022,
    OpenSearchSeverityExtractionV022,
    OpenSearchTimestampExtractionV022,
)
from ecomsre.product.contracts import ProductModelV1


PROFILE_VERIFIED_V0221 = "ECOMSRE_PRODUCT_V0221_PROFILE_VERIFIED"
SCHEMA_AMBIGUOUS_V0221 = "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"


class OpenSearchFieldCapsStatusV0221(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE_OPTIONAL = "UNAVAILABLE_OPTIONAL"


class OpenSearchProfileResolutionModeV0221(str, Enum):
    MAPPING_FIELD_CAPS_SAMPLE = "MAPPING_FIELD_CAPS_SAMPLE"
    MAPPING_SAMPLE_EMPIRICAL = "MAPPING_SAMPLE_EMPIRICAL"


class OpenSearchProbeProtocolBlockerV0221(RuntimeError):
    def __init__(self, terminal: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.terminal = terminal


class OpenSearchEmpiricalQueryVerificationV0221(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-empirical-query-verification.v0221"
    ] = "ecomsre.product.opensearch-empirical-query-verification.v0221"
    service_query_field: str = Field(min_length=1, max_length=255)
    timestamp_query_field: str = Field(min_length=1, max_length=255)
    checkout_service_observed: bool
    terms_aggregation_succeeded: bool
    timestamp_range_query_succeeded: bool
    profile_verification_status: Literal["SUCCESS_EMPTY", "SUCCESS_NONEMPTY"]
    verification_hit_count: int = Field(ge=0, le=5)
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_verified_queries(self) -> "OpenSearchEmpiricalQueryVerificationV0221":
        if not (
            self.checkout_service_observed
            and self.terms_aggregation_succeeded
            and self.timestamp_range_query_succeeded
        ):
            raise ValueError("OpenSearch empirical query verification is incomplete")
        if self.profile_verification_status == "SUCCESS_EMPTY":
            if self.verification_hit_count != 0:
                raise ValueError("OpenSearch empty verification hit count differs")
        elif self.verification_hit_count == 0:
            raise ValueError("OpenSearch nonempty verification has zero hits")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"verification_sha256"})
        )
        if self.verification_sha256 != expected:
            raise ValueError("OpenSearch empirical verification digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchEmpiricalQueryVerificationV0221":
        payload = {
            "schema_version": (
                "ecomsre.product.opensearch-empirical-query-verification.v0221"
            ),
            **values,
        }
        draft = cls.model_construct(**payload, verification_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"verification_sha256"})
        return cls.model_validate(
            {**serialized, "verification_sha256": semantic_sha256_v22(serialized)}
        )


def parse_field_mapping_v0221(payload: object) -> OpenSearchMappingSnapshotV022:
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("OpenSearch focused field mapping response is invalid")
    index_names: list[str] = []
    fields: dict[str, OpenSearchMappingFieldV022] = {}
    for index_name in sorted(payload):
        index = payload[index_name]
        if not isinstance(index_name, str) or not isinstance(index, Mapping):
            raise ValueError("OpenSearch focused field mapping index is invalid")
        mappings = index.get("mappings")
        if not isinstance(mappings, Mapping):
            raise ValueError("OpenSearch focused field mapping container is invalid")
        index_names.append(index_name)
        for response_name in sorted(mappings):
            entry = mappings[response_name]
            if not isinstance(entry, Mapping):
                raise ValueError("OpenSearch focused field mapping entry is invalid")
            full_name = entry.get("full_name")
            raw_mapping = entry.get("mapping")
            if (
                not isinstance(full_name, str)
                or not isinstance(raw_mapping, Mapping)
                or not isinstance(raw_mapping.get(full_name), Mapping)
            ):
                raise ValueError("OpenSearch focused field mapping shape is invalid")
            definition = raw_mapping[full_name]
            assert isinstance(definition, Mapping)
            mapping_type = definition.get("type")
            if not isinstance(mapping_type, str):
                raise ValueError("OpenSearch focused field mapping type is invalid")
            existing = fields.get(full_name)
            candidate = OpenSearchMappingFieldV022(mapping_type=mapping_type)
            if existing is not None and existing != candidate:
                raise ValueError("OpenSearch focused field mapping conflicts")
            fields[full_name] = candidate
    return OpenSearchMappingSnapshotV022(
        index_names=tuple(index_names),
        fields=dict(sorted(fields.items())),
        mapping_sha256=semantic_sha256_v22(payload),
    )


def build_service_aggregation_body_v0221(service_query_field: str) -> dict[str, object]:
    if not service_query_field or len(service_query_field) > 255:
        raise ValueError("OpenSearch service query field is invalid")
    return {
        "size": 0,
        "aggs": {"services": {"terms": {"field": service_query_field, "size": 100}}},
    }


def build_timestamp_range_body_v0221(
    timestamp_query_field: str,
    *,
    started_at: str,
    ended_at: str,
) -> dict[str, object]:
    if not timestamp_query_field or started_at >= ended_at:
        raise ValueError("OpenSearch timestamp range query is invalid")
    return {
        "size": 0,
        "query": {
            "range": {
                timestamp_query_field: {"gte": started_at, "lte": ended_at}
            }
        },
    }


def build_profile_verification_body_v0221(
    *,
    service_query_field: str,
    timestamp_query_field: str,
    checkout_aliases: tuple[str, ...],
    started_at: str,
    ended_at: str,
) -> dict[str, object]:
    if not checkout_aliases or checkout_aliases != tuple(sorted(set(checkout_aliases))):
        raise ValueError("OpenSearch checkout aliases are not canonical")
    return {
        "size": 5,
        "_source": True,
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            timestamp_query_field: {
                                "gte": started_at,
                                "lte": ended_at,
                            }
                        }
                    },
                    {"terms": {service_query_field: list(checkout_aliases)}},
                ]
            }
        },
    }


def _valid_search_hits_v0221(payload: object) -> list[object]:
    if not isinstance(payload, Mapping) or payload.get("timed_out") is True:
        raise ValueError("OpenSearch empirical search response is invalid")
    shards = payload.get("_shards")
    if isinstance(shards, Mapping) and shards.get("failed") not in {None, 0}:
        raise ValueError("OpenSearch empirical search has failed shards")
    hits = payload.get("hits")
    if not isinstance(hits, Mapping) or not isinstance(hits.get("hits"), list):
        raise ValueError("OpenSearch empirical search hits are invalid")
    return hits["hits"]


def build_empirical_query_verification_v0221(
    *,
    service_query_field: str,
    timestamp_query_field: str,
    checkout_aliases: tuple[str, ...],
    service_aggregation_response: object,
    timestamp_range_response: object,
    profile_verification_response: object,
) -> OpenSearchEmpiricalQueryVerificationV0221:
    if not isinstance(service_aggregation_response, Mapping):
        raise ValueError("OpenSearch service aggregation response is invalid")
    aggregations = service_aggregation_response.get("aggregations")
    services = aggregations.get("services") if isinstance(aggregations, Mapping) else None
    buckets = services.get("buckets") if isinstance(services, Mapping) else None
    if not isinstance(buckets, list):
        raise ValueError("OpenSearch service aggregation buckets are invalid")
    observed = any(
        isinstance(bucket, Mapping)
        and bucket.get("key") in checkout_aliases
        and isinstance(bucket.get("doc_count"), int)
        and int(bucket["doc_count"]) > 0
        for bucket in buckets
    )
    _valid_search_hits_v0221(timestamp_range_response)
    verification_hits = _valid_search_hits_v0221(profile_verification_response)
    return OpenSearchEmpiricalQueryVerificationV0221.build(
        service_query_field=service_query_field,
        timestamp_query_field=timestamp_query_field,
        checkout_service_observed=observed,
        terms_aggregation_succeeded=True,
        timestamp_range_query_succeeded=True,
        profile_verification_status=(
            "SUCCESS_NONEMPTY" if verification_hits else "SUCCESS_EMPTY"
        ),
        verification_hit_count=len(verification_hits),
    )


class OpenSearchNormalizationProfileV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-normalization-profile.v0221"] = (
        "ecomsre.product.opensearch-normalization-profile.v0221"
    )
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{3,79}$")
    index_pattern: str = Field(min_length=1, max_length=255)
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    field_caps_status: OpenSearchFieldCapsStatusV0221
    field_caps_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sample_shape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    empirical_query_verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_resolution_mode: OpenSearchProfileResolutionModeV0221
    timestamp_extraction: OpenSearchTimestampExtractionV022
    service_extraction: OpenSearchExtractionRuleV022
    service_source_field: str = Field(min_length=1, max_length=255)
    service_query_field: str = Field(min_length=1, max_length=255)
    severity_extraction: OpenSearchSeverityExtractionV022
    message_extraction: OpenSearchMessageExtractionV022
    trace_id_extraction: OpenSearchExtractionRuleV022 | None = None
    message_projection_policy: Literal["AS_OBSERVED", "OBSERVER_SYMPTOM_V1"]
    maximum_record_rejection_fraction: float = Field(
        ge=0,
        le=0.25,
        allow_inf_nan=False,
    )
    profile_source: Literal["LIVE_SCHEMA_PROBE_V0221"]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_profile(self) -> "OpenSearchNormalizationProfileV0221":
        if self.field_caps_status is OpenSearchFieldCapsStatusV0221.AVAILABLE:
            if (
                self.field_caps_sha256 is None
                or self.profile_resolution_mode
                is not OpenSearchProfileResolutionModeV0221.MAPPING_FIELD_CAPS_SAMPLE
            ):
                raise ValueError("OpenSearch available Field Caps binding differs")
        elif (
            self.field_caps_sha256 is not None
            or self.profile_resolution_mode
            is not OpenSearchProfileResolutionModeV0221.MAPPING_SAMPLE_EMPIRICAL
        ):
            raise ValueError("OpenSearch optional Field Caps binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("OpenSearch v0.2.2.1 profile digest differs")
        return self

    @classmethod
    def from_v022(
        cls,
        profile: OpenSearchNormalizationProfileV022,
        *,
        field_caps_status: OpenSearchFieldCapsStatusV0221,
        field_caps_sha256: str | None,
        empirical_query_verification_sha256: str,
        profile_resolution_mode: OpenSearchProfileResolutionModeV0221,
    ) -> "OpenSearchNormalizationProfileV0221":
        payload: dict[str, Any] = {
            "schema_version": "ecomsre.product.opensearch-normalization-profile.v0221",
            **profile.model_dump(
                mode="json",
                exclude={
                    "schema_version",
                    "field_caps_sha256",
                    "profile_source",
                    "profile_sha256",
                },
            ),
            "field_caps_status": field_caps_status,
            "field_caps_sha256": field_caps_sha256,
            "empirical_query_verification_sha256": (
                empirical_query_verification_sha256
            ),
            "profile_resolution_mode": profile_resolution_mode,
            "profile_source": "LIVE_SCHEMA_PROBE_V0221",
        }
        return cls.model_validate(
            {**payload, "profile_sha256": semantic_sha256_v22(payload)}
        )

    def as_v022(self) -> OpenSearchNormalizationProfileV022:
        """Adapt the verified successor profile to the mature typed parser."""

        return OpenSearchNormalizationProfileV022.build(
            **self.model_dump(
                mode="python",
                exclude={
                    "schema_version",
                    "field_caps_status",
                    "field_caps_sha256",
                    "empirical_query_verification_sha256",
                    "profile_resolution_mode",
                    "profile_source",
                    "profile_sha256",
                },
            ),
            field_caps_sha256=(
                self.field_caps_sha256
                or self.empirical_query_verification_sha256
            ),
            profile_source="LIVE_SCHEMA_PROBE_V022",
        )


class OpenSearchProfileResolutionV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-profile-resolution.v0221"] = (
        "ecomsre.product.opensearch-profile-resolution.v0221"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0221_PROFILE_VERIFIED"]
    profile: OpenSearchNormalizationProfileV0221
    candidate_rankings: dict[str, tuple[OpenSearchCandidateRankingV022, ...]]
    tie_breaks: tuple[str, ...]
    empirical_verification: OpenSearchEmpiricalQueryVerificationV0221
    resolution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_resolution(self) -> "OpenSearchProfileResolutionV0221":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"resolution_sha256"})
        )
        if self.resolution_sha256 != expected:
            raise ValueError("OpenSearch v0.2.2.1 resolution digest differs")
        return self


def _mapping_capabilities_v0221(
    mapping: OpenSearchMappingSnapshotV022,
) -> OpenSearchFieldCapsSnapshotV022:
    aggregatable_types = {
        "boolean",
        "byte",
        "date",
        "date_nanos",
        "double",
        "float",
        "half_float",
        "integer",
        "ip",
        "keyword",
        "long",
        "scaled_float",
        "short",
        "unsigned_long",
    }
    fields = {
        path: OpenSearchFieldCapabilityV022(
            mapping_types=(field.mapping_type,),
            searchable=field.mapping_type not in {"object", "nested"},
            aggregatable=field.mapping_type in aggregatable_types,
        )
        for path, field in sorted(mapping.fields.items())
    }
    return OpenSearchFieldCapsSnapshotV022(
        fields=fields,
        field_caps_sha256=semantic_sha256_v22(
            {
                "source": "MAPPING_DERIVED_CAPABILITY_CANDIDATES_V0221",
                "mapping_sha256": mapping.mapping_sha256,
                "fields": {
                    path: capability.model_dump(mode="json")
                    for path, capability in fields.items()
                },
            }
        ),
    )


def _block_ambiguous_v0221(message: str) -> OpenSearchProbeProtocolBlockerV0221:
    return OpenSearchProbeProtocolBlockerV0221(SCHEMA_AMBIGUOUS_V0221, message)


def _resolve_v022(
    *,
    index_pattern: str,
    mapping: OpenSearchMappingSnapshotV022,
    field_caps: OpenSearchFieldCapsSnapshotV022,
    samples: Sequence[Mapping[str, object]],
    sample_shapes: OpenSearchSampleShapeSummaryV022,
    checkout_aliases: tuple[str, ...],
) -> OpenSearchProfileResolutionV022:
    try:
        return resolve_normalization_profile_v022(
            index_pattern=index_pattern,
            mapping=mapping,
            field_caps=field_caps,
            samples=samples,
            sample_shapes=sample_shapes,
            checkout_aliases=checkout_aliases,
        )
    except (OpenSearchSchemaExceptionV022, ValueError) as error:
        raise _block_ambiguous_v0221(
            "OpenSearch Mapping and samples do not prove a unique profile"
        ) from error


def resolve_normalization_profile_v0221(
    *,
    index_pattern: str,
    mapping: OpenSearchMappingSnapshotV022,
    field_caps: OpenSearchFieldCapsSnapshotV022 | None,
    samples: Sequence[Mapping[str, object]],
    sample_shapes: OpenSearchSampleShapeSummaryV022,
    checkout_aliases: tuple[str, ...],
    empirical_verification: OpenSearchEmpiricalQueryVerificationV0221,
) -> OpenSearchProfileResolutionV0221:
    if not samples or sample_shapes.sample_count != len(samples):
        raise _block_ambiguous_v0221(
            "OpenSearch bounded samples are unavailable or inconsistent"
        )
    effective_caps = field_caps or _mapping_capabilities_v0221(mapping)
    base = _resolve_v022(
        index_pattern=index_pattern,
        mapping=mapping,
        field_caps=effective_caps,
        samples=samples,
        sample_shapes=sample_shapes,
        checkout_aliases=checkout_aliases,
    )
    timestamp_field = base.profile.timestamp_extraction.extraction.paths[0]
    if (
        empirical_verification.service_query_field
        != base.profile.service_query_field
        or empirical_verification.timestamp_query_field != timestamp_field
    ):
        raise _block_ambiguous_v0221(
            "OpenSearch empirical query fields do not bind the selected profile"
        )
    unresolved_required_ties: list[str] = []
    resolved_ties: list[str] = []
    for tie in base.tie_breaks:
        category = tie.split(":", 1)[0]
        if category == "timestamp":
            resolved_ties.append("timestamp:EMPIRICAL_RANGE_QUERY")
        elif category == "service":
            selected_source = base.profile.service_source_field
            empirical_source = empirical_verification.service_query_field.removesuffix(
                ".keyword"
            )
            if empirical_source == selected_source:
                resolved_ties.append("service:EMPIRICAL_TERMS_QUERY")
            else:
                unresolved_required_ties.append(tie)
        elif category == "message":
            unresolved_required_ties.append(tie)
        else:
            resolved_ties.append(tie)
    if unresolved_required_ties:
        raise _block_ambiguous_v0221(
            "OpenSearch required profile candidates remain tied"
        )
    status = (
        OpenSearchFieldCapsStatusV0221.AVAILABLE
        if field_caps is not None
        else OpenSearchFieldCapsStatusV0221.UNAVAILABLE_OPTIONAL
    )
    mode = (
        OpenSearchProfileResolutionModeV0221.MAPPING_FIELD_CAPS_SAMPLE
        if field_caps is not None
        else OpenSearchProfileResolutionModeV0221.MAPPING_SAMPLE_EMPIRICAL
    )
    profile = OpenSearchNormalizationProfileV0221.from_v022(
        base.profile,
        field_caps_status=status,
        field_caps_sha256=(
            field_caps.field_caps_sha256 if field_caps is not None else None
        ),
        empirical_query_verification_sha256=(
            empirical_verification.verification_sha256
        ),
        profile_resolution_mode=mode,
    )
    payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.opensearch-profile-resolution.v0221",
        "terminal": PROFILE_VERIFIED_V0221,
        "profile": profile,
        "candidate_rankings": base.candidate_rankings,
        "tie_breaks": tuple(resolved_ties),
        "empirical_verification": empirical_verification,
    }
    draft = OpenSearchProfileResolutionV0221.model_construct(
        **payload,
        resolution_sha256="0" * 64,
    )
    serialized = draft.model_dump(mode="json", exclude={"resolution_sha256"})
    return OpenSearchProfileResolutionV0221.model_validate(
        {**serialized, "resolution_sha256": semantic_sha256_v22(serialized)}
    )


__all__ = (
    "PROFILE_VERIFIED_V0221",
    "SCHEMA_AMBIGUOUS_V0221",
    "OpenSearchEmpiricalQueryVerificationV0221",
    "OpenSearchFieldCapsStatusV0221",
    "OpenSearchNormalizationProfileV0221",
    "OpenSearchProbeProtocolBlockerV0221",
    "OpenSearchProfileResolutionModeV0221",
    "OpenSearchProfileResolutionV0221",
    "build_empirical_query_verification_v0221",
    "build_profile_verification_body_v0221",
    "build_service_aggregation_body_v0221",
    "build_timestamp_range_body_v0221",
    "parse_field_mapping_v0221",
    "resolve_normalization_profile_v0221",
)
