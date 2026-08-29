"""Bounded OpenSearch schema-discovery contracts for Product v0.2.2."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchExtractionModeV022,
    OpenSearchExtractionRuleV022,
    OpenSearchMessageExtractionV022,
    OpenSearchMessageModeV022,
    OpenSearchNormalizationProfileV022,
    OpenSearchSchemaErrorCodeV022,
    OpenSearchSchemaFailureV022,
    OpenSearchSeverityExtractionV022,
    OpenSearchSeverityModeV022,
    OpenSearchTimestampExtractionV022,
    OpenSearchTimestampParserV022,
)
from ecomsre.product.contracts import ProductModelV1


class OpenSearchMappingFieldV022(ProductModelV1):
    mapping_type: str = Field(min_length=1, max_length=80)
    multi_field_of: str | None = Field(default=None, min_length=1, max_length=255)


class OpenSearchMappingSnapshotV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-mapping-snapshot.v022"] = (
        "ecomsre.product.opensearch-mapping-snapshot.v022"
    )
    index_names: tuple[str, ...]
    fields: dict[str, OpenSearchMappingFieldV022]
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OpenSearchFieldCapabilityV022(ProductModelV1):
    mapping_types: tuple[str, ...]
    searchable: bool
    aggregatable: bool


class OpenSearchFieldCapsSnapshotV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-field-caps.v022"] = (
        "ecomsre.product.opensearch-field-caps.v022"
    )
    fields: dict[str, OpenSearchFieldCapabilityV022]
    field_caps_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OpenSearchSampleShapeSummaryV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-sample-shapes.v022"] = (
        "ecomsre.product.opensearch-sample-shapes.v022"
    )
    sample_count: int = Field(ge=0, le=5)
    field_presence: dict[str, int]
    field_types: dict[str, tuple[str, ...]]
    sample_shape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_summary(self) -> "OpenSearchSampleShapeSummaryV022":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"sample_shape_sha256"})
        )
        if self.sample_shape_sha256 != expected:
            raise ValueError("OpenSearch sample-shape digest differs")
        return self


class OpenSearchCandidateRankingV022(ProductModelV1):
    category: Literal["timestamp", "service", "severity", "message", "trace_id"]
    field_path: str = Field(min_length=1, max_length=255)
    mapping_types: tuple[str, ...]
    searchable: bool
    aggregatable: bool
    sample_coverage: int = Field(ge=0, le=5)
    compatible_value_count: int = Field(ge=0, le=5)
    checkout_alias_match_count: int = Field(ge=0, le=5)
    coercion_complexity: int = Field(ge=0, le=10)
    score: int
    tied_at_score: bool


class OpenSearchProfileResolutionV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-profile-resolution.v022"] = (
        "ecomsre.product.opensearch-profile-resolution.v022"
    )
    profile: OpenSearchNormalizationProfileV022
    candidate_rankings: dict[str, tuple[OpenSearchCandidateRankingV022, ...]]
    tie_breaks: tuple[str, ...]
    resolution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_resolution(self) -> "OpenSearchProfileResolutionV022":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"resolution_sha256"})
        )
        if self.resolution_sha256 != expected:
            raise ValueError("OpenSearch profile resolution digest differs")
        return self


class OpenSearchPublicSchemaFingerprintV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-schema-fingerprint.v022"] = (
        "ecomsre.product.opensearch-schema-fingerprint.v022"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V022_SCHEMA_DISCOVERY_PASS"]
    index_names: tuple[str, ...]
    field_paths: tuple[str, ...]
    field_types: dict[str, tuple[str, ...]]
    candidate_rankings: dict[str, tuple[OpenSearchCandidateRankingV022, ...]]
    sample_count: int = Field(ge=0, le=5)
    request_count: int = Field(ge=1, le=12)
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    field_caps_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_shape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    private_capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_profile: OpenSearchNormalizationProfileV022
    fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_fingerprint(self) -> "OpenSearchPublicSchemaFingerprintV022":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"fingerprint_sha256"})
        )
        if self.fingerprint_sha256 != expected:
            raise ValueError("OpenSearch public fingerprint digest differs")
        return self


class OpenSearchProbeTrafficProfileV022(ProductModelV1):
    request_seed: int = Field(ge=0)
    maximum_request_count: int = Field(ge=1, le=60)
    requests_per_second: float = Field(gt=0, le=2, allow_inf_nan=False)
    error_budget: int = Field(ge=1, le=10)


class OpenSearchSchemaProbeProfileV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-schema-probe-profile.v022"] = (
        "ecomsre.product.opensearch-schema-probe-profile.v022"
    )
    campaign_id: str = Field(pattern=r"^product-v022-schema-[a-z0-9-]{4,60}$")
    index_pattern: str = Field(min_length=1, max_length=255)
    checkout_aliases: tuple[str, ...] = Field(min_length=1, max_length=20)
    maximum_request_count: Literal[12]
    maximum_sample_documents: Literal[5]
    maximum_response_bytes: Literal[2_000_000]
    recent_window_seconds: int = Field(ge=60, le=3600)
    stabilization_seconds: int = Field(ge=0, le=120)
    healthy_traffic_profile: OpenSearchProbeTrafficProfileV022
    private_root: Literal[".local/product-v022/opensearch-schema-probe/private"]
    public_fingerprint_json: Literal[
        "docs/analysis/product-v022-opensearch-schema-fingerprint.json"
    ]
    public_fingerprint_markdown: Literal[
        "docs/analysis/product-v022-opensearch-schema-fingerprint.md"
    ]
    normalization_profile_path: Literal[
        "config/product-v022/opensearch-probe/normalization-profile.json"
    ]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_probe_profile(self) -> "OpenSearchSchemaProbeProfileV022":
        if self.checkout_aliases != tuple(sorted(set(self.checkout_aliases))):
            raise ValueError("OpenSearch checkout aliases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("OpenSearch schema-probe profile digest differs")
        return self


def _probe_error_v022(
    code: OpenSearchSchemaErrorCodeV022,
    *,
    field_path: str | None = None,
    mapping_type: str | None = None,
) -> OpenSearchSchemaExceptionV022:
    return OpenSearchSchemaExceptionV022(
        OpenSearchSchemaFailureV022.build(
            code=code,
            field_path=field_path,
            mapping_type=mapping_type,
        )
    )


def load_schema_probe_profile_v022(path: Path) -> OpenSearchSchemaProbeProfileV022:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return OpenSearchSchemaProbeProfileV022.model_validate(payload)


def _flatten_mapping_v022(
    properties: Mapping[str, object],
    *,
    prefix: str,
    output: dict[str, OpenSearchMappingFieldV022],
) -> None:
    for name in sorted(properties):
        raw = properties[name]
        if not isinstance(raw, Mapping):
            raise _probe_error_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID,
                field_path=f"{prefix}{name}",
            )
        path = f"{prefix}{name}"
        mapping_type = raw.get("type")
        nested = raw.get("properties")
        if mapping_type is None and isinstance(nested, Mapping):
            mapping_type = "object"
        if isinstance(mapping_type, str):
            output[path] = OpenSearchMappingFieldV022(mapping_type=mapping_type)
        if nested is not None:
            if not isinstance(nested, Mapping):
                raise _probe_error_v022(
                    OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID,
                    field_path=path,
                )
            _flatten_mapping_v022(nested, prefix=f"{path}.", output=output)
        fields = raw.get("fields")
        if fields is not None:
            if not isinstance(fields, Mapping):
                raise _probe_error_v022(
                    OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID,
                    field_path=path,
                )
            for suffix in sorted(fields):
                subfield = fields[suffix]
                if not isinstance(subfield, Mapping) or not isinstance(
                    subfield.get("type"), str
                ):
                    raise _probe_error_v022(
                        OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID,
                        field_path=f"{path}.{suffix}",
                    )
                output[f"{path}.{suffix}"] = OpenSearchMappingFieldV022(
                    mapping_type=str(subfield["type"]),
                    multi_field_of=path,
                )


def parse_mapping_v022(payload: object) -> OpenSearchMappingSnapshotV022:
    if not isinstance(payload, Mapping) or not payload:
        raise _probe_error_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID
        )
    fields: dict[str, OpenSearchMappingFieldV022] = {}
    index_names: list[str] = []
    for index_name in sorted(payload):
        index = payload[index_name]
        if not isinstance(index_name, str) or not isinstance(index, Mapping):
            raise _probe_error_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID
            )
        mappings = index.get("mappings")
        if not isinstance(mappings, Mapping):
            raise _probe_error_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID,
                field_path=f"{index_name}.mappings",
            )
        properties = mappings.get("properties")
        if not isinstance(properties, Mapping):
            raise _probe_error_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID,
                field_path=f"{index_name}.mappings.properties",
            )
        index_names.append(index_name)
        _flatten_mapping_v022(properties, prefix="", output=fields)
    normalized = dict(sorted(fields.items()))
    return OpenSearchMappingSnapshotV022(
        index_names=tuple(index_names),
        fields=normalized,
        mapping_sha256=semantic_sha256_v22(payload),
    )


def parse_field_caps_v022(payload: object) -> OpenSearchFieldCapsSnapshotV022:
    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("fields"), Mapping
    ):
        raise _probe_error_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID
        )
    output: dict[str, OpenSearchFieldCapabilityV022] = {}
    raw_fields = payload["fields"]
    assert isinstance(raw_fields, Mapping)
    for path in sorted(raw_fields):
        raw_types = raw_fields[path]
        if (
            not isinstance(path, str)
            or not isinstance(raw_types, Mapping)
            or not raw_types
        ):
            raise _probe_error_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID,
                field_path=str(path),
            )
        mapping_types: list[str] = []
        searchable = True
        aggregatable = True
        for type_name in sorted(raw_types):
            cap = raw_types[type_name]
            if not isinstance(cap, Mapping):
                raise _probe_error_v022(
                    OpenSearchSchemaErrorCodeV022.OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID,
                    field_path=path,
                )
            effective_type = cap.get("type", type_name)
            if not isinstance(effective_type, str):
                raise _probe_error_v022(
                    OpenSearchSchemaErrorCodeV022.OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID,
                    field_path=path,
                )
            mapping_types.append(effective_type)
            searchable = searchable and cap.get("searchable") is True
            aggregatable = aggregatable and cap.get("aggregatable") is True
        output[path] = OpenSearchFieldCapabilityV022(
            mapping_types=tuple(sorted(set(mapping_types))),
            searchable=searchable,
            aggregatable=aggregatable,
        )
    return OpenSearchFieldCapsSnapshotV022(
        fields=dict(sorted(output.items())),
        field_caps_sha256=semantic_sha256_v22(payload),
    )


def _value_type_v022(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "unsupported"


def _flatten_sample_v022(
    value: object,
    *,
    prefix: str,
    output: dict[str, str],
) -> None:
    if prefix:
        output[prefix] = _value_type_v022(value)
    if isinstance(value, Mapping):
        for key in sorted(value):
            if not isinstance(key, str):
                continue
            child = f"{prefix}.{key}" if prefix else key
            _flatten_sample_v022(value[key], prefix=child, output=output)


def summarize_sample_shapes_v022(
    samples: Sequence[Mapping[str, object]],
) -> OpenSearchSampleShapeSummaryV022:
    if len(samples) > 5:
        raise ValueError("OpenSearch sample count exceeds five")
    presence: Counter[str] = Counter()
    field_types: defaultdict[str, set[str]] = defaultdict(set)
    for sample in samples:
        flattened: dict[str, str] = {}
        _flatten_sample_v022(sample, prefix="", output=flattened)
        for path, value_type in flattened.items():
            presence[path] += 1
            field_types[path].add(value_type)
    payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.opensearch-sample-shapes.v022",
        "sample_count": len(samples),
        "field_presence": dict(sorted(presence.items())),
        "field_types": {
            path: tuple(sorted(types)) for path, types in sorted(field_types.items())
        },
    }
    draft = OpenSearchSampleShapeSummaryV022.model_construct(
        **payload,
        sample_shape_sha256="0" * 64,
    )
    return OpenSearchSampleShapeSummaryV022.model_validate(
        {
            **payload,
            "sample_shape_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"sample_shape_sha256"})
            ),
        }
    )


def _sample_value_v022(sample: Mapping[str, object], path: str) -> object:
    if path in sample:
        return sample[path]
    current: object = sample
    segments = path.split(".")
    for index, segment in enumerate(segments):
        if not isinstance(current, Mapping):
            return None
        remaining = ".".join(segments[index:])
        if remaining in current:
            return current[remaining]
        if segment not in current:
            return None
        current = current[segment]
    return current


def _category_v022(path: str) -> str | None:
    lowered = path.lower().replace("_", "")
    if "timestamp" in lowered or "timeunixnano" in lowered or path == "@timestamp":
        return "timestamp"
    if "service" in lowered and ("name" in lowered or lowered.endswith("service")):
        return "service"
    if "severity" in lowered:
        return "severity"
    if lowered in {"body", "message", "log"} or lowered.endswith(
        (".body", ".message", ".stringvalue")
    ):
        return "message"
    if "trace" in lowered and "id" in lowered:
        return "trace_id"
    return None


def _compatible_v022(category: str, value: object) -> bool:
    if value is None:
        return False
    if category in {"service", "severity", "trace_id"}:
        return isinstance(value, (str, int)) and not isinstance(value, bool)
    if category == "timestamp":
        return isinstance(value, (str, int, float)) and not isinstance(value, bool)
    if category == "message":
        return isinstance(value, (str, int, float, bool))
    return False


def _rank_candidates_v022(
    *,
    category: str,
    paths: Sequence[str],
    mapping: OpenSearchMappingSnapshotV022,
    field_caps: OpenSearchFieldCapsSnapshotV022,
    samples: Sequence[Mapping[str, object]],
    checkout_aliases: tuple[str, ...],
) -> tuple[OpenSearchCandidateRankingV022, ...]:
    drafts: list[dict[str, Any]] = []
    for path in sorted(set(paths)):
        values = [_sample_value_v022(sample, path) for sample in samples]
        coverage = sum(value is not None for value in values)
        compatible = sum(_compatible_v022(category, value) for value in values)
        aliases = sum(
            isinstance(value, str) and value in checkout_aliases for value in values
        )
        mapping_field = mapping.fields.get(path)
        cap = field_caps.fields.get(path)
        mapping_types = (
            cap.mapping_types
            if cap is not None
            else ((mapping_field.mapping_type,) if mapping_field is not None else ())
        )
        complexity = path.count(".")
        score = (
            coverage * 100
            + compatible * 40
            + aliases * 80
            + (20 if cap is not None and cap.searchable else 0)
            + (10 if cap is not None and cap.aggregatable else 0)
            + (15 if mapping_field is not None else 0)
            - complexity
        )
        drafts.append(
            {
                "category": category,
                "field_path": path,
                "mapping_types": tuple(sorted(set(mapping_types))),
                "searchable": bool(cap and cap.searchable),
                "aggregatable": bool(cap and cap.aggregatable),
                "sample_coverage": coverage,
                "compatible_value_count": compatible,
                "checkout_alias_match_count": aliases,
                "coercion_complexity": complexity,
                "score": score,
            }
        )
    ordered = sorted(
        drafts, key=lambda item: (-int(item["score"]), str(item["field_path"]))
    )
    score_counts = Counter(int(item["score"]) for item in ordered)
    return tuple(
        OpenSearchCandidateRankingV022(
            **item,
            tied_at_score=score_counts[int(item["score"])] > 1,
        )
        for item in ordered
    )


def _timestamp_parser_v022(
    values: Sequence[object],
) -> OpenSearchTimestampParserV022:
    present = [value for value in values if value is not None]
    if present and all(isinstance(value, str) for value in present):
        try:
            for value in present:
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise _probe_error_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FORMAT_UNSUPPORTED
            ) from error
        return OpenSearchTimestampParserV022.ISO_8601
    if present and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in present
    ):
        numeric_values = [
            float(value) for value in present if isinstance(value, (int, float))
        ]
        magnitude = max(abs(value) for value in numeric_values)
        if magnitude >= 1e17:
            return OpenSearchTimestampParserV022.EPOCH_NANOS
        if magnitude >= 1e14:
            return OpenSearchTimestampParserV022.EPOCH_MICROS
        if magnitude >= 1e11:
            return OpenSearchTimestampParserV022.EPOCH_MILLIS
        return OpenSearchTimestampParserV022.EPOCH_SECONDS
    raise _probe_error_v022(
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_TYPE_INVALID
    )


def _source_rule_v022(
    path: str,
    samples: Sequence[Mapping[str, object]],
    *,
    mode: OpenSearchExtractionModeV022 | None = None,
) -> OpenSearchExtractionRuleV022:
    direct_count = sum(path in sample for sample in samples)
    selected_mode = mode or (
        OpenSearchExtractionModeV022.DIRECT_KEY
        if direct_count == len(samples)
        else OpenSearchExtractionModeV022.DOTTED_OR_NESTED_PATH
    )
    return OpenSearchExtractionRuleV022(mode=selected_mode, paths=(path,))


def resolve_normalization_profile_v022(
    *,
    index_pattern: str,
    mapping: OpenSearchMappingSnapshotV022,
    field_caps: OpenSearchFieldCapsSnapshotV022,
    samples: Sequence[Mapping[str, object]],
    sample_shapes: OpenSearchSampleShapeSummaryV022,
    checkout_aliases: tuple[str, ...],
) -> OpenSearchProfileResolutionV022:
    all_paths = (
        set(mapping.fields) | set(field_caps.fields) | set(sample_shapes.field_presence)
    )
    by_category: defaultdict[str, list[str]] = defaultdict(list)
    for path in all_paths:
        category = _category_v022(path)
        if category is not None:
            by_category[category].append(path)
    rankings: dict[str, tuple[OpenSearchCandidateRankingV022, ...]] = {}
    required = ("timestamp", "service", "message")
    for category in ("timestamp", "service", "severity", "message", "trace_id"):
        rankings[category] = _rank_candidates_v022(
            category=category,
            paths=by_category[category],
            mapping=mapping,
            field_caps=field_caps,
            samples=samples,
            checkout_aliases=checkout_aliases,
        )
        if category in required and not rankings[category]:
            raise _probe_error_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_REQUIRED_FIELD_NOT_DISCOVERED,
                field_path=category,
            )
    selected = {
        category: values[0].field_path
        for category, values in rankings.items()
        if values
    }
    if any(rankings[name][0].sample_coverage == 0 for name in required):
        raise _probe_error_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_REQUIRED_FIELD_NOT_DISCOVERED
        )
    service_source = selected["service"]
    if service_source.endswith(".keyword"):
        service_source = service_source.removesuffix(".keyword")
    query_options = (f"{service_source}.keyword", service_source)
    service_query = next(
        (
            path
            for path in query_options
            if path in field_caps.fields
            and field_caps.fields[path].searchable
            and field_caps.fields[path].aggregatable
        ),
        None,
    )
    if service_query is None:
        raise _probe_error_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_QUERY_FIELD_NOT_AGGREGATABLE,
            field_path=service_source,
        )
    timestamp_path = selected["timestamp"]
    timestamp_parser = _timestamp_parser_v022(
        [_sample_value_v022(sample, timestamp_path) for sample in samples]
    )
    message_path = selected["message"]
    wrapper_path: str | None = None
    if message_path.endswith(".stringValue"):
        candidate = message_path.removesuffix(".stringValue")
        if all(
            isinstance(_sample_value_v022(sample, candidate), Mapping)
            for sample in samples
        ):
            wrapper_path = candidate
    severity_path = selected.get("severity")
    trace_path = selected.get("trace_id")
    profile = OpenSearchNormalizationProfileV022.build(
        profile_id=f"otel-checkout-{sample_shapes.sample_shape_sha256[:12]}",
        index_pattern=index_pattern,
        mapping_sha256=mapping.mapping_sha256,
        field_caps_sha256=field_caps.field_caps_sha256,
        sample_shape_sha256=sample_shapes.sample_shape_sha256,
        timestamp_extraction=OpenSearchTimestampExtractionV022(
            extraction=_source_rule_v022(timestamp_path, samples),
            parsers=(timestamp_parser,),
        ),
        service_extraction=_source_rule_v022(service_source, samples),
        service_source_field=service_source,
        service_query_field=service_query,
        severity_extraction=OpenSearchSeverityExtractionV022(
            extraction=(
                _source_rule_v022(severity_path, samples)
                if severity_path is not None
                else OpenSearchExtractionRuleV022(
                    mode=OpenSearchExtractionModeV022.OPTIONAL,
                    paths=("severity",),
                )
            ),
            mode=(
                OpenSearchSeverityModeV022.STRING_VALUE
                if severity_path is not None
                else OpenSearchSeverityModeV022.OPTIONAL_TO_DIAGNOSTIC
            ),
        ),
        message_extraction=OpenSearchMessageExtractionV022(
            extraction=(
                _source_rule_v022(
                    wrapper_path,
                    samples,
                    mode=OpenSearchExtractionModeV022.OTLP_VALUE_WRAPPER,
                )
                if wrapper_path is not None
                else _source_rule_v022(message_path, samples)
            ),
            mode=(
                OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER
                if wrapper_path is not None
                else OpenSearchMessageModeV022.STRING_VALUE
            ),
        ),
        trace_id_extraction=(
            _source_rule_v022(
                trace_path,
                samples,
                mode=OpenSearchExtractionModeV022.OPTIONAL,
            )
            if trace_path is not None
            else None
        ),
        message_projection_policy="OBSERVER_SYMPTOM_V1",
        maximum_record_rejection_fraction=0.25,
        profile_source="LIVE_SCHEMA_PROBE_V022",
    )
    ties = tuple(
        f"{category}:{values[0].score}"
        for category, values in sorted(rankings.items())
        if values and values[0].tied_at_score
    )
    payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.opensearch-profile-resolution.v022",
        "profile": profile,
        "candidate_rankings": dict(sorted(rankings.items())),
        "tie_breaks": ties,
    }
    draft = OpenSearchProfileResolutionV022.model_construct(
        **payload,
        resolution_sha256="0" * 64,
    )
    return OpenSearchProfileResolutionV022.model_validate(
        {
            **payload,
            "resolution_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"resolution_sha256"})
            ),
        }
    )


def build_public_schema_fingerprint_v022(
    *,
    mapping: OpenSearchMappingSnapshotV022,
    field_caps: OpenSearchFieldCapsSnapshotV022,
    sample_shapes: OpenSearchSampleShapeSummaryV022,
    resolution: OpenSearchProfileResolutionV022,
    private_capture_sha256: str,
    request_count: int,
) -> OpenSearchPublicSchemaFingerprintV022:
    payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.opensearch-schema-fingerprint.v022",
        "terminal": "ECOMSRE_PRODUCT_V022_SCHEMA_DISCOVERY_PASS",
        "index_names": mapping.index_names,
        "field_paths": tuple(sorted(set(mapping.fields) | set(field_caps.fields))),
        "field_types": {
            path: capability.mapping_types
            for path, capability in sorted(field_caps.fields.items())
        },
        "candidate_rankings": resolution.candidate_rankings,
        "sample_count": sample_shapes.sample_count,
        "request_count": request_count,
        "mapping_sha256": mapping.mapping_sha256,
        "field_caps_sha256": field_caps.field_caps_sha256,
        "sample_shape_sha256": sample_shapes.sample_shape_sha256,
        "private_capture_sha256": private_capture_sha256,
        "normalization_profile": resolution.profile,
    }
    draft = OpenSearchPublicSchemaFingerprintV022.model_construct(
        **payload,
        fingerprint_sha256="0" * 64,
    )
    return OpenSearchPublicSchemaFingerprintV022.model_validate(
        {
            **payload,
            "fingerprint_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"fingerprint_sha256"})
            ),
        }
    )


__all__ = (
    "OpenSearchCandidateRankingV022",
    "OpenSearchFieldCapabilityV022",
    "OpenSearchFieldCapsSnapshotV022",
    "OpenSearchMappingFieldV022",
    "OpenSearchMappingSnapshotV022",
    "OpenSearchProfileResolutionV022",
    "OpenSearchPublicSchemaFingerprintV022",
    "OpenSearchProbeTrafficProfileV022",
    "OpenSearchSampleShapeSummaryV022",
    "OpenSearchSchemaProbeProfileV022",
    "build_public_schema_fingerprint_v022",
    "parse_field_caps_v022",
    "parse_mapping_v022",
    "load_schema_probe_profile_v022",
    "resolve_normalization_profile_v022",
    "summarize_sample_shapes_v022",
)
