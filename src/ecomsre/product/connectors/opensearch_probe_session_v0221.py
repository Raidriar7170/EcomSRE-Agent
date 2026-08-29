"""Live-shape and offline-parser contracts for Product v0.2.2.1."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
    normalize_opensearch_search_v022,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchNormalizationProfileV0221,
)
from ecomsre.product.connectors.opensearch_probe_v022 import (
    OpenSearchProbeTrafficProfileV022,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchBatchStatusV022,
    OpenSearchMessageModeV022,
    OpenSearchSeverityModeV022,
    OpenSearchTimestampParserV022,
)
from ecomsre.product.contracts import ProductModelV1


SCHEMA_DISCOVERY_PASS_V0221 = "ECOMSRE_PRODUCT_V0221_SCHEMA_DISCOVERY_PASS"
OFFLINE_PARSER_PASS_V0221 = "ECOMSRE_PRODUCT_V0221_OFFLINE_PARSER_PASS"
OFFLINE_PARSER_BLOCKED_V0221 = "BLOCKED_ECOMSRE_PRODUCT_V0221_OFFLINE_PARSER"


class OpenSearchSchemaSessionProfileV0221(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-schema-session-profile.v0221"
    ] = "ecomsre.product.opensearch-schema-session-profile.v0221"
    session_id: Literal["product-v0221-schema-discovery-1"]
    index_pattern: Literal["otel-logs-*"]
    checkout_aliases: tuple[str, ...] = Field(min_length=1, max_length=20)
    maximum_changed_plan_count: Literal[3]
    maximum_request_count: Literal[16]
    maximum_transport_retries: Literal[2]
    maximum_sample_documents: Literal[5]
    maximum_response_bytes: Literal[2_000_000]
    recent_window_seconds: int = Field(ge=60, le=3600)
    stabilization_seconds: int = Field(ge=0, le=120)
    healthy_traffic_profile: OpenSearchProbeTrafficProfileV022
    private_root: Literal[
        ".local/product-v0221/opensearch-schema-session/private"
    ]
    schema_session_json: Literal[
        "docs/analysis/product-v0221-schema-session.json"
    ]
    schema_session_markdown: Literal[
        "docs/analysis/product-v0221-schema-session.md"
    ]
    normalization_profile_path: Literal[
        "config/product-v0221/opensearch-probe/normalization-profile.json"
    ]
    sanitized_fixture_path: Literal[
        "tests/fixtures/product_v0221/opensearch_live_shape.json"
    ]
    offline_parser_report_path: Literal[
        "docs/analysis/product-v0221-offline-parser.json"
    ]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_profile(self) -> "OpenSearchSchemaSessionProfileV0221":
        if self.checkout_aliases != tuple(sorted(set(self.checkout_aliases))):
            raise ValueError("OpenSearch checkout aliases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("OpenSearch schema-session profile digest differs")
        return self


def load_schema_session_profile_v0221(
    path: Path,
) -> OpenSearchSchemaSessionProfileV0221:
    return OpenSearchSchemaSessionProfileV0221.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


class OpenSearchSanitizedFixtureV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-live-shape.v0221"] = (
        "ecomsre.product.opensearch-live-shape.v0221"
    )
    private_sample_shape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    ended_at: datetime
    requested_services: tuple[Literal["checkout"], ...]
    service_aliases: dict[str, Literal["checkout"]]
    response: dict[str, Any]
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_fixture(self) -> "OpenSearchSanitizedFixtureV0221":
        if (
            self.started_at.tzinfo is None
            or self.started_at.utcoffset() is None
            or self.ended_at.tzinfo is None
            or self.ended_at.utcoffset() is None
            or self.started_at.astimezone(UTC) >= self.ended_at.astimezone(UTC)
        ):
            raise ValueError("OpenSearch sanitized fixture window is invalid")
        if self.requested_services != ("checkout",):
            raise ValueError("OpenSearch sanitized fixture services differ")
        if tuple(self.service_aliases) != tuple(sorted(self.service_aliases)):
            raise ValueError("OpenSearch sanitized fixture aliases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"fixture_sha256"})
        )
        if self.fixture_sha256 != expected:
            raise ValueError("OpenSearch sanitized fixture digest differs")
        return self

    def rebind(self) -> "OpenSearchSanitizedFixtureV0221":
        payload = self.model_dump(mode="json", exclude={"fixture_sha256"})
        return OpenSearchSanitizedFixtureV0221.model_validate(
            {**payload, "fixture_sha256": semantic_sha256_v22(payload)}
        )


class OpenSearchOfflineParserReportV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.offline-parser-report.v0221"] = (
        "ecomsre.product.offline-parser-report.v0221"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0221_OFFLINE_PARSER_PASS",
        "BLOCKED_ECOMSRE_PRODUCT_V0221_OFFLINE_PARSER",
    ]
    changed_iteration_count: int = Field(ge=1, le=3)
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sampled_record_count: int = Field(ge=0, le=5)
    accepted_record_count: int = Field(ge=0, le=5)
    rejected_record_count: int = Field(ge=0, le=5)
    rejection_codes_by_count: dict[str, int]
    outer_schema_failure_code: str | None
    timestamp_parse_failures: int = Field(ge=0, le=5)
    service_alias_failures: int = Field(ge=0, le=5)
    message_extraction_failures: int = Field(ge=0, le=5)
    observer_projection_failures: int = Field(ge=0, le=5)
    covered_services: tuple[str, ...]
    baseline_unchanged: Literal[True]
    cleanup: Literal["CLEAN"]
    fault_attempt_count: Literal[0]
    baseline_readiness_attempt_count: Literal[0]
    knowledge_loop_campaign_count: Literal[0]
    action_authority: Literal["NONE"]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_report(self) -> "OpenSearchOfflineParserReportV0221":
        if self.sampled_record_count != (
            self.accepted_record_count + self.rejected_record_count
        ):
            raise ValueError("OpenSearch offline parser record counts differ")
        passing = (
            self.sampled_record_count > 0
            and self.accepted_record_count == self.sampled_record_count
            and self.rejected_record_count == 0
            and not self.rejection_codes_by_count
            and self.outer_schema_failure_code is None
            and self.timestamp_parse_failures == 0
            and self.service_alias_failures == 0
            and self.message_extraction_failures == 0
            and self.observer_projection_failures == 0
            and self.covered_services == ("checkout",)
        )
        if (self.terminal == OFFLINE_PARSER_PASS_V0221) != passing:
            raise ValueError("OpenSearch offline parser terminal differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("OpenSearch offline parser report digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchOfflineParserReportV0221":
        payload = {
            "schema_version": "ecomsre.product.offline-parser-report.v0221",
            **values,
        }
        draft = cls.model_construct(**payload, report_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"report_sha256"})
        return cls.model_validate(
            {**serialized, "report_sha256": semantic_sha256_v22(serialized)}
        )


def _neutralize_shape_v0221(value: object) -> object:
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
            str(key): _neutralize_shape_v0221(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_neutralize_shape_v0221(item) for item in value]
    raise ValueError("OpenSearch live sample contains an unsupported value type")


def _path_value_v0221(source: Mapping[str, object], path: str) -> object | None:
    if path in source:
        return source[path]
    current: object = source
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _set_path_v0221(source: dict[str, Any], path: str, value: object) -> None:
    if path in source:
        source[path] = value
        return
    segments = path.split(".")
    current: dict[str, Any] = source
    for segment in segments[:-1]:
        nested = current.get(segment)
        if not isinstance(nested, dict):
            raise ValueError("OpenSearch sanitized fixture path is unavailable")
        current = nested
    if segments[-1] not in current:
        raise ValueError("OpenSearch sanitized fixture field is unavailable")
    current[segments[-1]] = value


def _neutral_timestamp_v0221(
    parser: OpenSearchTimestampParserV022,
    midpoint: datetime,
) -> str | int:
    seconds = int(midpoint.timestamp())
    if parser is OpenSearchTimestampParserV022.ISO_8601:
        return midpoint.isoformat().replace("+00:00", "Z")
    multiplier = {
        OpenSearchTimestampParserV022.EPOCH_SECONDS: 1,
        OpenSearchTimestampParserV022.EPOCH_MILLIS: 1_000,
        OpenSearchTimestampParserV022.EPOCH_MICROS: 1_000_000,
        OpenSearchTimestampParserV022.EPOCH_NANOS: 1_000_000_000,
    }[parser]
    return seconds * multiplier


def _sanitized_source_v0221(
    source: Mapping[str, object],
    *,
    profile: OpenSearchNormalizationProfileV0221,
    midpoint: datetime,
    service_alias: str,
) -> dict[str, Any]:
    sanitized = _neutralize_shape_v0221(source)
    if not isinstance(sanitized, dict):
        raise ValueError("OpenSearch sanitized source is not an object")
    timestamp_path = profile.timestamp_extraction.extraction.paths[0]
    _set_path_v0221(
        sanitized,
        timestamp_path,
        _neutral_timestamp_v0221(
            profile.timestamp_extraction.parsers[0],
            midpoint,
        ),
    )
    _set_path_v0221(sanitized, profile.service_source_field, service_alias)
    message_path = profile.message_extraction.extraction.paths[0]
    message_value: object = "Checkout request completed."
    if (
        profile.message_extraction.mode
        is OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER
    ):
        message_value = {"stringValue": "Checkout request completed."}
    _set_path_v0221(sanitized, message_path, message_value)
    severity_path = profile.severity_extraction.extraction.paths[0]
    if _path_value_v0221(source, severity_path) is not None:
        severity_value: object = "INFO"
        if (
            profile.severity_extraction.mode
            is OpenSearchSeverityModeV022.INTEGER_OTEL_SEVERITY
        ):
            severity_value = 9
        _set_path_v0221(sanitized, severity_path, severity_value)
    if profile.trace_id_extraction is not None:
        trace_path = profile.trace_id_extraction.paths[0]
        if _path_value_v0221(source, trace_path) is not None:
            _set_path_v0221(sanitized, trace_path, "0" * 32)
    return sanitized


def build_sanitized_live_fixture_v0221(
    *,
    live_response: object,
    profile: OpenSearchNormalizationProfileV0221,
    private_sample_shape_sha256: str,
    started_at: datetime,
    ended_at: datetime,
    service_aliases: Mapping[str, str],
) -> OpenSearchSanitizedFixtureV0221:
    if not isinstance(live_response, Mapping):
        raise ValueError("OpenSearch live response is not an object")
    live_hits_container = live_response.get("hits")
    live_hits = (
        live_hits_container.get("hits")
        if isinstance(live_hits_container, Mapping)
        else None
    )
    if not isinstance(live_hits, list) or not 1 <= len(live_hits) <= 5:
        raise ValueError("OpenSearch live fixture requires one to five hits")
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
    sanitized_hits: list[dict[str, object]] = []
    for ordinal, raw_hit in enumerate(live_hits, start=1):
        if not isinstance(raw_hit, Mapping) or not isinstance(
            raw_hit.get("_source"), Mapping
        ):
            raise ValueError("OpenSearch live hit source is invalid")
        source = raw_hit["_source"]
        assert isinstance(source, Mapping)
        sanitized_hits.append(
            {
                "_index": "otel-logs-sanitized",
                "_id": f"sanitized-hit-{ordinal}",
                "_source": _sanitized_source_v0221(
                    source,
                    profile=profile,
                    midpoint=midpoint,
                    service_alias=service_alias,
                ),
            }
        )
    response = {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {
            "total": {"value": len(sanitized_hits), "relation": "eq"},
            "hits": sanitized_hits,
        },
    }
    payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.opensearch-live-shape.v0221",
        "private_sample_shape_sha256": private_sample_shape_sha256,
        "normalization_profile_sha256": profile.profile_sha256,
        "started_at": started_at.astimezone(UTC),
        "ended_at": ended_at.astimezone(UTC),
        "requested_services": ("checkout",),
        "service_aliases": canonical_aliases,
        "response": response,
    }
    draft = OpenSearchSanitizedFixtureV0221.model_construct(
        **payload,
        fixture_sha256="0" * 64,
    )
    serialized = draft.model_dump(mode="json", exclude={"fixture_sha256"})
    return OpenSearchSanitizedFixtureV0221.model_validate(
        {**serialized, "fixture_sha256": semantic_sha256_v22(serialized)}
    )


def _hit_count_v0221(response: Mapping[str, object]) -> int:
    hits_container = response.get("hits")
    hits = hits_container.get("hits") if isinstance(hits_container, Mapping) else []
    return len(hits) if isinstance(hits, list) else 0


def evaluate_offline_parser_v0221(
    *,
    fixture: OpenSearchSanitizedFixtureV0221,
    profile: OpenSearchNormalizationProfileV0221,
    changed_iteration_count: int = 1,
) -> OpenSearchOfflineParserReportV0221:
    outer_code: str | None = None
    sampled = _hit_count_v0221(fixture.response)
    accepted = rejected = 0
    rejection_codes: dict[str, int] = {}
    covered_services: tuple[str, ...] = ()
    try:
        if fixture.normalization_profile_sha256 != profile.profile_sha256:
            raise ValueError("OpenSearch fixture profile binding differs")
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
        rejection_codes = batch.rejection_codes_by_count
        covered_services = batch.covered_services
        passing = (
            batch.status is OpenSearchBatchStatusV022.SUCCESS_NONEMPTY
            and sampled > 0
            and accepted == sampled
            and rejected == 0
            and covered_services == ("checkout",)
        )
    except OpenSearchSchemaExceptionV022 as error:
        outer_code = error.failure.code.value
        passing = False
    except ValueError:
        outer_code = "OPENSEARCH_PROFILE_BINDING_INVALID"
        passing = False
    timestamp_failures = sum(
        count
        for code, count in rejection_codes.items()
        if "TIMESTAMP" in code
    )
    service_failures = sum(
        count
        for code, count in rejection_codes.items()
        if "SERVICE" in code
    )
    message_failures = sum(
        count
        for code, count in rejection_codes.items()
        if "MESSAGE" in code
    )
    observer_failures = rejection_codes.get(
        "OPENSEARCH_OBSERVER_PROJECTION_REJECTED",
        0,
    )
    return OpenSearchOfflineParserReportV0221.build(
        terminal=(
            OFFLINE_PARSER_PASS_V0221
            if passing
            else OFFLINE_PARSER_BLOCKED_V0221
        ),
        changed_iteration_count=changed_iteration_count,
        fixture_sha256=fixture.fixture_sha256,
        normalization_profile_sha256=profile.profile_sha256,
        sampled_record_count=sampled,
        accepted_record_count=accepted,
        rejected_record_count=rejected,
        rejection_codes_by_count=dict(sorted(rejection_codes.items())),
        outer_schema_failure_code=outer_code,
        timestamp_parse_failures=timestamp_failures,
        service_alias_failures=service_failures,
        message_extraction_failures=message_failures,
        observer_projection_failures=observer_failures,
        covered_services=covered_services,
        baseline_unchanged=True,
        cleanup="CLEAN",
        fault_attempt_count=0,
        baseline_readiness_attempt_count=0,
        knowledge_loop_campaign_count=0,
        action_authority="NONE",
        agent_writes=0,
        runbook_executions=0,
    )


__all__ = (
    "OFFLINE_PARSER_BLOCKED_V0221",
    "OFFLINE_PARSER_PASS_V0221",
    "SCHEMA_DISCOVERY_PASS_V0221",
    "OpenSearchOfflineParserReportV0221",
    "OpenSearchSanitizedFixtureV0221",
    "OpenSearchSchemaSessionProfileV0221",
    "build_sanitized_live_fixture_v0221",
    "evaluate_offline_parser_v0221",
    "load_schema_session_profile_v0221",
)
