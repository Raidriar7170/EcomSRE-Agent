"""Operator-selected OpenSearch profile contracts for Product v0.2.2.2."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchOperatorDecisionLedgerV0222,
    OpenSearchOperatorDecisionV0222,
    OpenSearchProfileCandidateSetV0222,
)
from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
    normalize_opensearch_search_v022,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchBatchStatusV022,
    OpenSearchExtractionModeV022,
    OpenSearchExtractionRuleV022,
    OpenSearchMessageExtractionV022,
    OpenSearchMessageModeV022,
    OpenSearchNormalizationProfileV022,
    OpenSearchSeverityExtractionV022,
    OpenSearchSeverityModeV022,
    OpenSearchTimestampExtractionV022,
    OpenSearchTimestampParserV022,
)
from ecomsre.product.contracts import ProductModelV1


OFFLINE_PROFILE_PASS_V0222 = "ECOMSRE_PRODUCT_V0222_OFFLINE_PROFILE_PASS"
OFFLINE_PROFILE_BLOCKED_V0222 = "BLOCKED_ECOMSRE_PRODUCT_V0222_OFFLINE_PROFILE"
HOLDOUT_VERIFICATION_PASS_V0222 = (
    "ECOMSRE_PRODUCT_V0222_HOLDOUT_VERIFICATION_PASS"
)
PROFILE_VERIFICATION_BLOCKED_V0222 = (
    "BLOCKED_ECOMSRE_PRODUCT_V0222_PROFILE_VERIFICATION"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MISSING_V0222 = object()


class OpenSearchProfileStatusV0222(str, Enum):
    OPERATOR_SELECTED = "OPERATOR_SELECTED"
    ACTIVE = "ACTIVE"


class OpenSearchNormalizationProfileV0222(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-normalization-profile.v0222"
    ] = "ecomsre.product.opensearch-normalization-profile.v0222"
    profile_id: Literal["product-v0222-operator-selected-profile"]
    profile_status: OpenSearchProfileStatusV0222
    index_pattern: str = Field(min_length=1, max_length=255)
    capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    operator_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    selected_candidate_alias: str = Field(pattern=r"^P[0-9]{2}$")
    selected_candidate_sha256: str = Field(pattern=_SHA256_PATTERN)
    mapping_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    field_caps_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    structural_sample_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    timestamp_extraction: OpenSearchTimestampExtractionV022
    service_extraction: OpenSearchExtractionRuleV022
    service_source_field: str = Field(min_length=1, max_length=255)
    service_query_field: str = Field(min_length=1, max_length=255)
    severity_extraction: OpenSearchSeverityExtractionV022
    message_extraction: OpenSearchMessageExtractionV022
    trace_id_extraction: OpenSearchExtractionRuleV022 | None
    message_projection_policy: Literal["AS_OBSERVED"]
    maximum_record_rejection_fraction: float = Field(
        ge=0.2,
        le=0.2,
        allow_inf_nan=False,
    )
    profile_source: Literal["CAPTURE_FIRST_OPERATOR_SELECTION_V0222"]
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_profile(self) -> "OpenSearchNormalizationProfileV0222":
        if self.service_source_field not in self.service_extraction.paths:
            raise ValueError("OpenSearch selected service source differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("OpenSearch selected profile digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchNormalizationProfileV0222":
        normalized = dict(values)
        normalized["timestamp_extraction"] = (
            OpenSearchTimestampExtractionV022.model_validate(
                normalized["timestamp_extraction"]
            )
        )
        normalized["service_extraction"] = OpenSearchExtractionRuleV022.model_validate(
            normalized["service_extraction"]
        )
        normalized["severity_extraction"] = (
            OpenSearchSeverityExtractionV022.model_validate(
                normalized["severity_extraction"]
            )
        )
        normalized["message_extraction"] = (
            OpenSearchMessageExtractionV022.model_validate(
                normalized["message_extraction"]
            )
        )
        if normalized.get("trace_id_extraction") is not None:
            normalized["trace_id_extraction"] = (
                OpenSearchExtractionRuleV022.model_validate(
                    normalized["trace_id_extraction"]
                )
            )
        payload = {
            "schema_version": (
                "ecomsre.product.opensearch-normalization-profile.v0222"
            ),
            **normalized,
        }
        draft = cls.model_construct(**payload, profile_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"profile_sha256"})
        return cls.model_validate(
            {**body, "profile_sha256": semantic_sha256_v22(body)}
        )

    def as_v022(self) -> OpenSearchNormalizationProfileV022:
        """Adapt the selected profile to the mature typed parser."""

        return OpenSearchNormalizationProfileV022.build(
            profile_id=self.profile_id,
            index_pattern=self.index_pattern,
            mapping_sha256=self.mapping_response_sha256,
            field_caps_sha256=self.field_caps_response_sha256,
            sample_shape_sha256=self.structural_sample_response_sha256,
            timestamp_extraction=self.timestamp_extraction,
            service_extraction=self.service_extraction,
            service_source_field=self.service_source_field,
            service_query_field=self.service_query_field,
            severity_extraction=self.severity_extraction,
            message_extraction=self.message_extraction,
            trace_id_extraction=self.trace_id_extraction,
            message_projection_policy=self.message_projection_policy,
            maximum_record_rejection_fraction=(
                self.maximum_record_rejection_fraction
            ),
            profile_source="LIVE_SCHEMA_PROBE_V022",
        )

    def activate(self) -> "OpenSearchNormalizationProfileV0222":
        if self.profile_status is not OpenSearchProfileStatusV0222.OPERATOR_SELECTED:
            raise ValueError("OpenSearch profile is not awaiting activation")
        return OpenSearchNormalizationProfileV0222.build(
            **self.model_dump(
                mode="python",
                exclude={"schema_version", "profile_status", "profile_sha256"},
            ),
            profile_status=OpenSearchProfileStatusV0222.ACTIVE,
        )


def _path_value_v0222(source: Mapping[str, object], path: str) -> object:
    if path in source:
        return source[path]
    current: object = source
    segments = path.split(".")
    for index, segment in enumerate(segments):
        if not isinstance(current, Mapping):
            return _MISSING_V0222
        remaining = ".".join(segments[index:])
        if remaining in current:
            return current[remaining]
        if segment not in current:
            return _MISSING_V0222
        current = current[segment]
    return current


def _sources_v0222(response: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(response, Mapping):
        raise ValueError("OpenSearch selected sample is not an object")
    hits_container = response.get("hits")
    hits = hits_container.get("hits") if isinstance(hits_container, Mapping) else None
    if not isinstance(hits, list) or not 1 <= len(hits) <= 5:
        raise ValueError("OpenSearch selected sample requires one to five hits")
    sources: list[Mapping[str, object]] = []
    for hit in hits:
        if not isinstance(hit, Mapping) or not isinstance(hit.get("_source"), Mapping):
            raise ValueError("OpenSearch selected sample source is invalid")
        source = hit["_source"]
        assert isinstance(source, Mapping)
        sources.append(source)
    return tuple(sources)


def assemble_selected_profile_v0222(
    *,
    candidate_set: OpenSearchProfileCandidateSetV0222,
    decision_ledger: OpenSearchOperatorDecisionLedgerV0222,
    index_pattern: str,
    mapping_response_sha256: str,
    field_caps_response_sha256: str,
    structural_sample_response_sha256: str,
    structural_sample_response: object,
) -> OpenSearchNormalizationProfileV0222:
    if decision_ledger.candidate_set_sha256 != candidate_set.candidate_set_sha256:
        raise ValueError("OpenSearch decision Candidate Set binding differs")
    decision = decision_ledger.decisions[-1]
    if (
        decision.decision is not OpenSearchOperatorDecisionV0222.SELECT_PROFILE
        or decision.selected_candidate_alias is None
    ):
        raise ValueError("OpenSearch operator did not select a profile")
    candidate = next(
        (
            item
            for item in candidate_set.candidates
            if item.candidate_alias == decision.selected_candidate_alias
        ),
        None,
    )
    if candidate is None:
        raise ValueError("OpenSearch selected candidate is unavailable")
    fields = candidate.profile_fields
    sources = _sources_v0222(structural_sample_response)
    observed: dict[str, tuple[object, ...]] = {
        semantic: tuple(_path_value_v0222(source, path) for source in sources)
        for semantic, path in fields.items()
        if semantic != "service_query"
    }
    required_semantics = ("timestamp", "service_source", "message", "severity")
    if any(
        value is _MISSING_V0222
        for semantic in required_semantics
        for value in observed[semantic]
    ):
        raise ValueError("OpenSearch selected field is absent from retained sample")
    if not all(isinstance(value, str) for value in observed["timestamp"]):
        raise ValueError("OpenSearch selected timestamp type is unsupported")
    for value in observed["timestamp"]:
        assert isinstance(value, str)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("OpenSearch selected timestamp is not aware")
    if not all(isinstance(value, str) and value for value in observed["service_source"]):
        raise ValueError("OpenSearch selected service type is unsupported")
    if not all(isinstance(value, str) for value in observed["message"]):
        raise ValueError("OpenSearch selected message type is unsupported")
    severity_values = observed["severity"]
    if all(isinstance(value, str) for value in severity_values):
        severity_mode = OpenSearchSeverityModeV022.STRING_VALUE
    elif all(isinstance(value, int) and not isinstance(value, bool) for value in severity_values):
        severity_mode = OpenSearchSeverityModeV022.INTEGER_OTEL_SEVERITY
    else:
        raise ValueError("OpenSearch selected severity type is unsupported")
    observed_trace_ids = tuple(
        value for value in observed["trace_id"] if value is not _MISSING_V0222
    )
    if not observed_trace_ids or not all(
        isinstance(value, str) for value in observed_trace_ids
    ):
        raise ValueError("OpenSearch selected trace-ID type is unsupported")
    extraction_mode = OpenSearchExtractionModeV022.DOTTED_OR_NESTED_PATH
    return OpenSearchNormalizationProfileV0222.build(
        profile_id="product-v0222-operator-selected-profile",
        profile_status=OpenSearchProfileStatusV0222.OPERATOR_SELECTED,
        index_pattern=index_pattern,
        capture_bundle_sha256=candidate_set.capture_bundle_sha256,
        candidate_set_sha256=candidate_set.candidate_set_sha256,
        operator_decision_sha256=decision.decision_sha256,
        selected_candidate_alias=candidate.candidate_alias,
        selected_candidate_sha256=candidate.candidate_sha256,
        mapping_response_sha256=mapping_response_sha256,
        field_caps_response_sha256=field_caps_response_sha256,
        structural_sample_response_sha256=structural_sample_response_sha256,
        timestamp_extraction=OpenSearchTimestampExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=extraction_mode,
                paths=(fields["timestamp"],),
            ),
            parsers=(OpenSearchTimestampParserV022.ISO_8601,),
        ),
        service_extraction=OpenSearchExtractionRuleV022(
            mode=extraction_mode,
            paths=(fields["service_source"],),
        ),
        service_source_field=fields["service_source"],
        service_query_field=fields["service_query"],
        severity_extraction=OpenSearchSeverityExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=extraction_mode,
                paths=(fields["severity"],),
            ),
            mode=severity_mode,
        ),
        message_extraction=OpenSearchMessageExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=extraction_mode,
                paths=(fields["message"],),
            ),
            mode=OpenSearchMessageModeV022.STRING_VALUE,
        ),
        trace_id_extraction=OpenSearchExtractionRuleV022(
            mode=extraction_mode,
            paths=(fields["trace_id"],),
        ),
        message_projection_policy="AS_OBSERVED",
        maximum_record_rejection_fraction=0.2,
        profile_source="CAPTURE_FIRST_OPERATOR_SELECTION_V0222",
    )


class OpenSearchSelectedProfileFixtureV0222(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-selected-profile-shape.v0222"
    ] = "ecomsre.product.opensearch-selected-profile-shape.v0222"
    capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_sample_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalization_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    started_at: datetime
    ended_at: datetime
    requested_services: tuple[Literal["checkout"], ...]
    service_aliases: dict[str, Literal["checkout"]]
    response: dict[str, Any]
    fixture_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_fixture(self) -> "OpenSearchSelectedProfileFixtureV0222":
        if (
            self.started_at.tzinfo is None
            or self.started_at.utcoffset() is None
            or self.ended_at.tzinfo is None
            or self.ended_at.utcoffset() is None
            or self.started_at.astimezone(UTC) >= self.ended_at.astimezone(UTC)
        ):
            raise ValueError("OpenSearch selected fixture window is invalid")
        if self.requested_services != ("checkout",):
            raise ValueError("OpenSearch selected fixture services differ")
        if tuple(self.service_aliases) != tuple(sorted(self.service_aliases)):
            raise ValueError("OpenSearch selected fixture aliases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"fixture_sha256"})
        )
        if self.fixture_sha256 != expected:
            raise ValueError("OpenSearch selected fixture digest differs")
        return self

    def rebind(self) -> "OpenSearchSelectedProfileFixtureV0222":
        body = self.model_dump(mode="json", exclude={"fixture_sha256"})
        return OpenSearchSelectedProfileFixtureV0222.model_validate(
            {**body, "fixture_sha256": semantic_sha256_v22(body)}
        )


def _neutralize_shape_v0222(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return "value"
    if isinstance(value, int):
        return 1
    if isinstance(value, float):
        return 1.0
    if isinstance(value, Mapping):
        return {
            str(key): _neutralize_shape_v0222(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_neutralize_shape_v0222(item) for item in value]
    raise ValueError("OpenSearch selected sample has an unsupported value type")


def _set_path_v0222(source: dict[str, Any], path: str, value: object) -> None:
    if path in source:
        source[path] = value
        return
    current: dict[str, Any] = source
    segments = path.split(".")
    for index, segment in enumerate(segments):
        remaining = ".".join(segments[index:])
        if remaining in current:
            current[remaining] = value
            return
        if index == len(segments) - 1:
            break
        nested = current.get(segment)
        if not isinstance(nested, dict):
            break
        current = nested
    raise ValueError("OpenSearch selected fixture path is unavailable")


def build_sanitized_selected_profile_fixture_v0222(
    *,
    live_response: object,
    profile: OpenSearchNormalizationProfileV0222,
    capture_bundle_sha256: str,
    private_sample_response_sha256: str,
    started_at: datetime,
    ended_at: datetime,
    service_aliases: Mapping[str, str],
) -> OpenSearchSelectedProfileFixtureV0222:
    if profile.profile_status is not OpenSearchProfileStatusV0222.OPERATOR_SELECTED:
        raise ValueError("OpenSearch fixture requires an operator-selected profile")
    if capture_bundle_sha256 != profile.capture_bundle_sha256:
        raise ValueError("OpenSearch selected fixture capture binding differs")
    _sources_v0222(live_response)
    sanitized = _neutralize_shape_v0222(live_response)
    if not isinstance(sanitized, dict):
        raise ValueError("OpenSearch selected fixture is not an object")
    hits_container = sanitized.get("hits")
    if not isinstance(hits_container, dict) or not isinstance(
        hits_container.get("hits"), list
    ):
        raise ValueError("OpenSearch selected fixture hits are invalid")
    hits = hits_container["hits"]
    canonical_aliases = dict(sorted(service_aliases.items()))
    checkout_aliases = tuple(
        alias
        for alias, logical in canonical_aliases.items()
        if logical == "checkout" and alias != "checkout"
    )
    service_alias = checkout_aliases[0] if checkout_aliases else "checkout"
    midpoint = started_at.astimezone(UTC) + (
        ended_at.astimezone(UTC) - started_at.astimezone(UTC)
    ) / 2
    timestamp_value = midpoint.isoformat().replace("+00:00", "Z")
    for ordinal, hit in enumerate(hits, start=1):
        if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
            raise ValueError("OpenSearch selected fixture hit is invalid")
        source = hit["_source"]
        hit["_index"] = "otel-logs-sanitized"
        hit["_id"] = f"sanitized-hit-{ordinal}"
        _set_path_v0222(
            source,
            profile.timestamp_extraction.extraction.paths[0],
            timestamp_value,
        )
        _set_path_v0222(source, profile.service_source_field, service_alias)
        _set_path_v0222(
            source,
            profile.message_extraction.extraction.paths[0],
            "Checkout request completed.",
        )
        _set_path_v0222(
            source,
            profile.severity_extraction.extraction.paths[0],
            (
                9
                if profile.severity_extraction.mode
                is OpenSearchSeverityModeV022.INTEGER_OTEL_SEVERITY
                else "INFO"
            ),
        )
        if profile.trace_id_extraction is not None:
            trace_path = profile.trace_id_extraction.paths[0]
            if _path_value_v0222(source, trace_path) is not _MISSING_V0222:
                _set_path_v0222(source, trace_path, "0" * 32)
    hits_container["total"] = {"value": len(hits), "relation": "eq"}
    if "timed_out" in sanitized:
        sanitized["timed_out"] = False
    shards = sanitized.get("_shards")
    if isinstance(shards, dict):
        if "failed" in shards:
            shards["failed"] = 0
        if "successful" in shards:
            shards["successful"] = 1
        if "total" in shards:
            shards["total"] = 1
        if "skipped" in shards:
            shards["skipped"] = 0
    body: dict[str, Any] = {
        "schema_version": (
            "ecomsre.product.opensearch-selected-profile-shape.v0222"
        ),
        "capture_bundle_sha256": capture_bundle_sha256,
        "private_sample_response_sha256": private_sample_response_sha256,
        "normalization_profile_sha256": profile.profile_sha256,
        "started_at": started_at.astimezone(UTC),
        "ended_at": ended_at.astimezone(UTC),
        "requested_services": ("checkout",),
        "service_aliases": canonical_aliases,
        "response": sanitized,
    }
    draft = OpenSearchSelectedProfileFixtureV0222.model_construct(
        **body,
        fixture_sha256="0" * 64,
    )
    serialized = draft.model_dump(mode="json", exclude={"fixture_sha256"})
    return OpenSearchSelectedProfileFixtureV0222.model_validate(
        {**serialized, "fixture_sha256": semantic_sha256_v22(serialized)}
    )


class OpenSearchRecordDispositionV0222(ProductModelV1):
    hit_ordinal: int = Field(ge=0, le=5)
    disposition: Literal["ACCEPTED", "REJECTED"]
    accepted_service: str | None = None
    rejection_code: str | None = None

    @model_validator(mode="after")
    def require_explicit_disposition(self) -> "OpenSearchRecordDispositionV0222":
        if self.disposition == "ACCEPTED":
            if self.accepted_service != "checkout" or self.rejection_code is not None:
                raise ValueError("OpenSearch accepted disposition differs")
        elif self.accepted_service is not None or not self.rejection_code:
            raise ValueError("OpenSearch rejected disposition differs")
        return self


class OpenSearchOfflineProfileReportV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.offline-profile-report.v0222"] = (
        "ecomsre.product.offline-profile-report.v0222"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0222_OFFLINE_PROFILE_PASS",
        "BLOCKED_ECOMSRE_PRODUCT_V0222_OFFLINE_PROFILE",
    ]
    offline_changed_iteration_count: int = Field(ge=1, le=3)
    capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    operator_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalization_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    fixture_sha256: str = Field(pattern=_SHA256_PATTERN)
    sampled_record_count: int = Field(ge=0, le=5)
    accepted_record_count: int = Field(ge=0, le=5)
    accepted_checkout_record_count: int = Field(ge=0, le=5)
    rejected_record_count: int = Field(ge=0, le=5)
    rejection_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    record_dispositions: tuple[OpenSearchRecordDispositionV0222, ...]
    rejection_codes_by_count: dict[str, int]
    outer_schema_failure_code: str | None
    timestamp_parse_failures: int = Field(ge=0, le=5)
    service_alias_failures: int = Field(ge=0, le=5)
    message_extraction_failures: int = Field(ge=0, le=5)
    observer_projection_failures: int = Field(ge=0, le=5)
    baseline_unchanged: Literal[True]
    cleanup: Literal["CLEAN"]
    fault_attempt_count: Literal[0]
    baseline_readiness_attempt_count: Literal[0]
    product_diagnosis_attempt_count: Literal[0]
    knowledge_loop_campaign_count: Literal[0]
    action_authority: Literal["NONE"]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_report(self) -> "OpenSearchOfflineProfileReportV0222":
        if self.sampled_record_count != (
            self.accepted_record_count + self.rejected_record_count
        ):
            raise ValueError("OpenSearch offline selected counts differ")
        if len(self.record_dispositions) != self.sampled_record_count or tuple(
            item.hit_ordinal for item in self.record_dispositions
        ) != tuple(range(self.sampled_record_count)):
            raise ValueError("OpenSearch offline dispositions differ")
        passing = (
            self.sampled_record_count > 0
            and self.accepted_checkout_record_count > 0
            and self.rejection_fraction <= 0.2
            and self.outer_schema_failure_code is None
            and self.timestamp_parse_failures == 0
            and self.service_alias_failures == 0
            and self.message_extraction_failures == 0
            and self.observer_projection_failures == 0
        )
        if (self.terminal == OFFLINE_PROFILE_PASS_V0222) != passing:
            raise ValueError("OpenSearch offline selected terminal differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("OpenSearch offline selected report digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchOfflineProfileReportV0222":
        body = {
            "schema_version": "ecomsre.product.offline-profile-report.v0222",
            **values,
        }
        draft = cls.model_construct(**body, report_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"report_sha256"})
        return cls.model_validate(
            {**serialized, "report_sha256": semantic_sha256_v22(serialized)}
        )


def _hit_count_v0222(response: Mapping[str, object]) -> int:
    hits_container = response.get("hits")
    hits = hits_container.get("hits") if isinstance(hits_container, Mapping) else []
    return len(hits) if isinstance(hits, list) else 0


def evaluate_offline_selected_profile_v0222(
    *,
    fixture: OpenSearchSelectedProfileFixtureV0222,
    profile: OpenSearchNormalizationProfileV0222,
    offline_changed_iteration_count: int,
) -> OpenSearchOfflineProfileReportV0222:
    outer_code: str | None = None
    sampled = _hit_count_v0222(fixture.response)
    accepted = rejected = accepted_checkout = 0
    rejection_fraction = 0.0
    rejection_codes: dict[str, int] = {}
    dispositions: tuple[OpenSearchRecordDispositionV0222, ...] = ()
    try:
        if (
            fixture.normalization_profile_sha256 != profile.profile_sha256
            or fixture.capture_bundle_sha256 != profile.capture_bundle_sha256
        ):
            raise ValueError("OpenSearch selected fixture profile binding differs")
        context = ConnectorQueryContextV1(
            environment_id="env-" + "0" * 24,
            requested_services=("checkout",),
            service_aliases=dict(fixture.service_aliases),
            window=ConnectorWindowV1(
                started_at=fixture.started_at.astimezone(UTC),
                ended_at=fixture.ended_at.astimezone(UTC),
            ),
            maximum_records=5,
            requested_source=EvidenceSourceV22.LOGS,
        )
        batch = normalize_opensearch_search_v022(
            deepcopy(fixture.response),
            profile=profile.as_v022(),
            context=context,
            latency_ms=0.0,
        )
        sampled = batch.sampled_hit_count
        accepted = batch.accepted_record_count
        rejected = batch.rejected_record_count
        rejection_fraction = batch.rejection_fraction
        rejection_codes = batch.rejection_codes_by_count
        accepted_by_ordinal = {
            item.hit_ordinal: item.record.service for item in batch.normalizations
        }
        rejected_by_ordinal = {
            item.hit_ordinal: item.failure.code.value for item in batch.rejections
        }
        dispositions = tuple(
            OpenSearchRecordDispositionV0222(
                hit_ordinal=ordinal,
                disposition=(
                    "ACCEPTED" if ordinal in accepted_by_ordinal else "REJECTED"
                ),
                accepted_service=accepted_by_ordinal.get(ordinal),
                rejection_code=rejected_by_ordinal.get(ordinal),
            )
            for ordinal in range(sampled)
        )
        accepted_checkout = sum(
            service == "checkout" for service in accepted_by_ordinal.values()
        )
        passing = (
            batch.status is OpenSearchBatchStatusV022.SUCCESS_NONEMPTY
            and accepted_checkout > 0
            and rejection_fraction <= 0.2
        )
    except OpenSearchSchemaExceptionV022 as error:
        outer_code = error.failure.code.value
        passing = False
    except ValueError:
        outer_code = "OPENSEARCH_PROFILE_BINDING_INVALID"
        passing = False
    timestamp_failures = sum(
        count for code, count in rejection_codes.items() if "TIMESTAMP" in code
    )
    service_failures = sum(
        count for code, count in rejection_codes.items() if "SERVICE" in code
    )
    message_failures = sum(
        count for code, count in rejection_codes.items() if "MESSAGE" in code
    )
    observer_failures = rejection_codes.get(
        "OPENSEARCH_OBSERVER_PROJECTION_REJECTED",
        0,
    )
    passing = (
        passing
        and outer_code is None
        and timestamp_failures == 0
        and service_failures == 0
        and message_failures == 0
        and observer_failures == 0
    )
    return OpenSearchOfflineProfileReportV0222.build(
        terminal=(
            OFFLINE_PROFILE_PASS_V0222 if passing else OFFLINE_PROFILE_BLOCKED_V0222
        ),
        offline_changed_iteration_count=offline_changed_iteration_count,
        capture_bundle_sha256=profile.capture_bundle_sha256,
        candidate_set_sha256=profile.candidate_set_sha256,
        operator_decision_sha256=profile.operator_decision_sha256,
        normalization_profile_sha256=profile.profile_sha256,
        fixture_sha256=fixture.fixture_sha256,
        sampled_record_count=sampled,
        accepted_record_count=accepted,
        accepted_checkout_record_count=accepted_checkout,
        rejected_record_count=rejected,
        rejection_fraction=rejection_fraction,
        record_dispositions=dispositions,
        rejection_codes_by_count=dict(sorted(rejection_codes.items())),
        outer_schema_failure_code=outer_code,
        timestamp_parse_failures=timestamp_failures,
        service_alias_failures=service_failures,
        message_extraction_failures=message_failures,
        observer_projection_failures=observer_failures,
        baseline_unchanged=True,
        cleanup="CLEAN",
        fault_attempt_count=0,
        baseline_readiness_attempt_count=0,
        product_diagnosis_attempt_count=0,
        knowledge_loop_campaign_count=0,
        action_authority="NONE",
        agent_writes=0,
        runbook_executions=0,
    )


class OpenSearchHoldoutVerificationReportV0222(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-holdout-verification.v0222"
    ] = "ecomsre.product.opensearch-holdout-verification.v0222"
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0222_HOLDOUT_VERIFICATION_PASS",
        "BLOCKED_ECOMSRE_PRODUCT_V0222_PROFILE_VERIFICATION",
    ]
    session_id: str = Field(pattern=r"^product-v0222-holdout-[12]$")
    holdout_verification_session_count: int = Field(ge=1, le=2)
    capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    operator_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    selected_candidate_alias: str = Field(pattern=r"^P[0-9]{2}$")
    selected_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    selected_profile_file_sha256_before: str = Field(pattern=_SHA256_PATTERN)
    selected_profile_file_sha256_after: str = Field(pattern=_SHA256_PATTERN)
    profile_bytes_unchanged: bool
    service_aggregation_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    timestamp_range_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    targeted_response_sha256: str = Field(pattern=_SHA256_PATTERN)
    started_at: datetime
    ended_at: datetime
    read_only_request_count: int = Field(ge=0, le=12)
    transport_retry_count: int = Field(ge=0, le=3)
    service_aggregation_observed_aliases: tuple[str, ...]
    timestamp_range_query_status: Literal["PASS", "FAIL"]
    targeted_checkout_query_status: Literal["PASS", "FAIL"]
    sampled_record_count: int = Field(ge=0, le=5)
    accepted_record_count: int = Field(ge=0, le=5)
    accepted_checkout_record_count: int = Field(ge=0, le=5)
    rejected_record_count: int = Field(ge=0, le=5)
    rejection_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    record_dispositions: tuple[OpenSearchRecordDispositionV0222, ...]
    rejection_codes_by_count: dict[str, int]
    outer_schema_failure_count: int = Field(ge=0, le=3)
    timestamp_parse_failures: int = Field(ge=0, le=5)
    service_alias_ambiguity_count: int = Field(ge=0, le=5)
    service_alias_failures: int = Field(ge=0, le=5)
    message_extraction_failures: int = Field(ge=0, le=5)
    observer_projection_failures: int = Field(ge=0, le=5)
    baseline_unchanged: bool
    cleanup: Literal["CLEAN", "BLOCKED"]
    fault_attempt_count: Literal[0]
    baseline_readiness_attempt_count: Literal[0]
    product_diagnosis_attempt_count: Literal[0]
    knowledge_loop_campaign_count: Literal[0]
    action_authority: Literal["NONE"]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    verification_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_verification(
        self,
    ) -> "OpenSearchHoldoutVerificationReportV0222":
        if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
            raise ValueError("OpenSearch holdout window is not aware")
        if self.started_at.astimezone(UTC) >= self.ended_at.astimezone(UTC):
            raise ValueError("OpenSearch holdout window differs")
        if self.sampled_record_count != (
            self.accepted_record_count + self.rejected_record_count
        ):
            raise ValueError("OpenSearch holdout counts differ")
        if len(self.record_dispositions) != self.sampled_record_count:
            raise ValueError("OpenSearch holdout dispositions differ")
        if self.service_aggregation_observed_aliases != tuple(
            sorted(set(self.service_aggregation_observed_aliases))
        ):
            raise ValueError("OpenSearch holdout aliases are not canonical")
        passing = (
            self.read_only_request_count >= 3
            and bool(self.service_aggregation_observed_aliases)
            and self.timestamp_range_query_status == "PASS"
            and self.targeted_checkout_query_status == "PASS"
            and self.accepted_checkout_record_count > 0
            and self.rejection_fraction <= 0.2
            and self.outer_schema_failure_count == 0
            and self.timestamp_parse_failures == 0
            and self.service_alias_ambiguity_count == 0
            and self.service_alias_failures == 0
            and self.message_extraction_failures == 0
            and self.observer_projection_failures == 0
            and self.profile_bytes_unchanged
            and self.baseline_unchanged
            and self.cleanup == "CLEAN"
        )
        if (self.terminal == HOLDOUT_VERIFICATION_PASS_V0222) != passing:
            raise ValueError("OpenSearch holdout terminal differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"verification_sha256"})
        )
        if self.verification_sha256 != expected:
            raise ValueError("OpenSearch holdout digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchHoldoutVerificationReportV0222":
        body = {
            "schema_version": (
                "ecomsre.product.opensearch-holdout-verification.v0222"
            ),
            **values,
        }
        draft = cls.model_construct(**body, verification_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"verification_sha256"})
        return cls.model_validate(
            {**serialized, "verification_sha256": semantic_sha256_v22(serialized)}
        )


def _response_sha256_v0222(response: object) -> str:
    return semantic_sha256_v22(response)


def _validate_search_outer_v0222(response: object) -> None:
    if not isinstance(response, Mapping):
        raise ValueError("OpenSearch holdout response is not an object")
    hits = response.get("hits")
    if not isinstance(hits, Mapping) or not isinstance(hits.get("hits"), list):
        raise ValueError("OpenSearch holdout hits are invalid")


def _aggregation_aliases_v0222(
    response: object,
    service_aliases: Mapping[str, str],
) -> tuple[str, ...]:
    _validate_search_outer_v0222(response)
    assert isinstance(response, Mapping)
    aggregations = response.get("aggregations")
    services = (
        aggregations.get("services") if isinstance(aggregations, Mapping) else None
    )
    buckets = services.get("buckets") if isinstance(services, Mapping) else None
    if not isinstance(buckets, list):
        raise ValueError("OpenSearch holdout aggregation is invalid")
    observed = tuple(
        sorted(
            {
                key
                for bucket in buckets
                if isinstance(bucket, Mapping)
                and isinstance((key := bucket.get("key")), str)
                and service_aliases.get(key) == "checkout"
            }
        )
    )
    if not observed:
        raise ValueError("OpenSearch holdout did not observe checkout")
    return observed


def evaluate_holdout_verification_v0222(
    *,
    profile: OpenSearchNormalizationProfileV0222,
    service_aggregation_response: object,
    timestamp_range_response: object,
    targeted_response: object,
    started_at: datetime,
    ended_at: datetime,
    service_aliases: Mapping[str, str],
    read_only_request_count: int,
    transport_retry_count: int,
    selected_profile_file_sha256_before: str,
    selected_profile_file_sha256_after: str,
    cleanup: str,
    baseline_unchanged: bool = True,
    session_ordinal: int = 1,
) -> OpenSearchHoldoutVerificationReportV0222:
    if profile.profile_status is not OpenSearchProfileStatusV0222.OPERATOR_SELECTED:
        raise ValueError("OpenSearch holdout requires operator-selected profile")
    if not 1 <= session_ordinal <= 2:
        raise ValueError("OpenSearch holdout session budget differs")
    canonical_aliases = dict(sorted(service_aliases.items()))
    outer_failures = 0
    aggregation_aliases: tuple[str, ...] = ()
    timestamp_status: Literal["PASS", "FAIL"] = "FAIL"
    targeted_status: Literal["PASS", "FAIL"] = "FAIL"
    try:
        aggregation_aliases = _aggregation_aliases_v0222(
            service_aggregation_response,
            canonical_aliases,
        )
    except ValueError:
        outer_failures += 1
    try:
        _validate_search_outer_v0222(timestamp_range_response)
        timestamp_status = "PASS"
    except ValueError:
        outer_failures += 1
    sampled = accepted = accepted_checkout = rejected = 0
    rejection_fraction = 0.0
    rejection_codes: dict[str, int] = {}
    dispositions: tuple[OpenSearchRecordDispositionV0222, ...] = ()
    try:
        context = ConnectorQueryContextV1(
            environment_id="env-" + "0" * 24,
            requested_services=("checkout",),
            service_aliases=canonical_aliases,
            window=ConnectorWindowV1(
                started_at=started_at.astimezone(UTC),
                ended_at=ended_at.astimezone(UTC),
            ),
            maximum_records=5,
            requested_source=EvidenceSourceV22.LOGS,
        )
        batch = normalize_opensearch_search_v022(
            targeted_response,
            profile=profile.as_v022(),
            context=context,
            latency_ms=0.0,
        )
        targeted_status = "PASS"
        sampled = batch.sampled_hit_count
        accepted = batch.accepted_record_count
        rejected = batch.rejected_record_count
        rejection_fraction = batch.rejection_fraction
        rejection_codes = batch.rejection_codes_by_count
        accepted_by_ordinal = {
            item.hit_ordinal: item.record.service for item in batch.normalizations
        }
        rejected_by_ordinal = {
            item.hit_ordinal: item.failure.code.value for item in batch.rejections
        }
        accepted_checkout = sum(
            service == "checkout" for service in accepted_by_ordinal.values()
        )
        dispositions = tuple(
            OpenSearchRecordDispositionV0222(
                hit_ordinal=ordinal,
                disposition=(
                    "ACCEPTED" if ordinal in accepted_by_ordinal else "REJECTED"
                ),
                accepted_service=accepted_by_ordinal.get(ordinal),
                rejection_code=rejected_by_ordinal.get(ordinal),
            )
            for ordinal in range(sampled)
        )
    except OpenSearchSchemaExceptionV022:
        outer_failures += 1
    timestamp_failures = sum(
        count for code, count in rejection_codes.items() if "TIMESTAMP" in code
    )
    service_failures = sum(
        count for code, count in rejection_codes.items() if "SERVICE" in code
    )
    message_failures = sum(
        count for code, count in rejection_codes.items() if "MESSAGE" in code
    )
    observer_failures = rejection_codes.get(
        "OPENSEARCH_OBSERVER_PROJECTION_REJECTED",
        0,
    )
    bytes_unchanged = (
        selected_profile_file_sha256_before
        == selected_profile_file_sha256_after
    )
    passing = (
        read_only_request_count >= 3
        and bool(aggregation_aliases)
        and timestamp_status == "PASS"
        and targeted_status == "PASS"
        and accepted_checkout > 0
        and rejection_fraction <= 0.2
        and outer_failures == 0
        and timestamp_failures == 0
        and service_failures == 0
        and message_failures == 0
        and observer_failures == 0
        and bytes_unchanged
        and cleanup == "CLEAN"
    )
    return OpenSearchHoldoutVerificationReportV0222.build(
        terminal=(
            HOLDOUT_VERIFICATION_PASS_V0222
            if passing
            else PROFILE_VERIFICATION_BLOCKED_V0222
        ),
        session_id=f"product-v0222-holdout-{session_ordinal}",
        holdout_verification_session_count=session_ordinal,
        capture_bundle_sha256=profile.capture_bundle_sha256,
        candidate_set_sha256=profile.candidate_set_sha256,
        operator_decision_sha256=profile.operator_decision_sha256,
        selected_candidate_alias=profile.selected_candidate_alias,
        selected_profile_sha256=profile.profile_sha256,
        selected_profile_file_sha256_before=selected_profile_file_sha256_before,
        selected_profile_file_sha256_after=selected_profile_file_sha256_after,
        profile_bytes_unchanged=bytes_unchanged,
        service_aggregation_response_sha256=_response_sha256_v0222(
            service_aggregation_response
        ),
        timestamp_range_response_sha256=_response_sha256_v0222(
            timestamp_range_response
        ),
        targeted_response_sha256=_response_sha256_v0222(targeted_response),
        started_at=started_at.astimezone(UTC),
        ended_at=ended_at.astimezone(UTC),
        read_only_request_count=read_only_request_count,
        transport_retry_count=transport_retry_count,
        service_aggregation_observed_aliases=aggregation_aliases,
        timestamp_range_query_status=timestamp_status,
        targeted_checkout_query_status=targeted_status,
        sampled_record_count=sampled,
        accepted_record_count=accepted,
        accepted_checkout_record_count=accepted_checkout,
        rejected_record_count=rejected,
        rejection_fraction=rejection_fraction,
        record_dispositions=dispositions,
        rejection_codes_by_count=dict(sorted(rejection_codes.items())),
        outer_schema_failure_count=outer_failures,
        timestamp_parse_failures=timestamp_failures,
        service_alias_ambiguity_count=0,
        service_alias_failures=service_failures,
        message_extraction_failures=message_failures,
        observer_projection_failures=observer_failures,
        baseline_unchanged=baseline_unchanged,
        cleanup=cleanup,
        fault_attempt_count=0,
        baseline_readiness_attempt_count=0,
        product_diagnosis_attempt_count=0,
        knowledge_loop_campaign_count=0,
        action_authority="NONE",
        agent_writes=0,
        runbook_executions=0,
    )


__all__ = (
    "HOLDOUT_VERIFICATION_PASS_V0222",
    "OFFLINE_PROFILE_BLOCKED_V0222",
    "OFFLINE_PROFILE_PASS_V0222",
    "PROFILE_VERIFICATION_BLOCKED_V0222",
    "OpenSearchHoldoutVerificationReportV0222",
    "OpenSearchNormalizationProfileV0222",
    "OpenSearchOfflineProfileReportV0222",
    "OpenSearchProfileStatusV0222",
    "OpenSearchRecordDispositionV0222",
    "OpenSearchSelectedProfileFixtureV0222",
    "assemble_selected_profile_v0222",
    "build_sanitized_selected_profile_fixture_v0222",
    "evaluate_offline_selected_profile_v0222",
    "evaluate_holdout_verification_v0222",
)
