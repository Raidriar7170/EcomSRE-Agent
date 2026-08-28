"""Public Product API contracts for the durable shell."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


_SECRET_FIELD_PATTERN = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|credential|password|secret|token)(?:$|[_-])",
    re.I,
)


class ProductModelV1(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectorKindV1(str, Enum):
    PROMETHEUS = "PROMETHEUS"
    OPENSEARCH = "OPENSEARCH"
    JAEGER = "JAEGER"
    HTTP_HEALTH = "HTTP_HEALTH"
    PILOT_RUNTIME = "PILOT_RUNTIME"
    FIXTURE = "FIXTURE"


def _validate_connector_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("connector URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("connector URL must not contain userinfo")
    if parsed.query:
        raise ValueError("connector URL must not contain a secret query or any query")
    if parsed.fragment:
        raise ValueError("connector URL must not contain a fragment")
    return value


class FixtureConnectorSettingsV1(ProductModelV1):
    dataset: str = Field(min_length=1, max_length=120)


class PrometheusConnectorSettingsV1(ProductModelV1):
    query_templates: dict[str, str] = Field(default_factory=dict)
    service_label: str = Field(default="service_name", pattern=r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
    step_seconds: int = Field(default=15, ge=1, le=3600)
    maximum_sample_count: int = Field(default=10000, ge=1, le=10_000)
    maximum_series_count: int = Field(default=1000, ge=1, le=1_000)
    maximum_response_bytes: int = Field(default=10_000_000, ge=1, le=10_000_000)

    @field_validator("query_templates")
    @classmethod
    def query_templates_are_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"request_support", "error_rate", "latency", "cpu", "memory"}
        allowed_variables = {"service", "start", "end", "step"}
        if set(value) != allowed:
            raise ValueError("Prometheus query templates are incomplete")
        if any(not template or len(template) > 4000 for template in value.values()):
            raise ValueError("Prometheus query template is invalid")
        for template in value.values():
            variables = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template))
            if "service" not in variables or not variables.issubset(allowed_variables):
                raise ValueError("Prometheus query template variable is not supported")
        return dict(sorted(value.items()))


class OpenSearchConnectorSettingsV1(ProductModelV1):
    index_pattern: str = Field(min_length=1, max_length=255)
    timestamp_field: str = Field(min_length=1, max_length=255)
    service_field: str = Field(min_length=1, max_length=255)
    service_query_field: str | None = Field(default=None, min_length=1, max_length=255)
    severity_field: str = Field(min_length=1, max_length=255)
    message_field: str = Field(min_length=1, max_length=255)
    message_projection_policy: Literal[
        "AS_OBSERVED",
        "OBSERVER_SYMPTOM_V1",
    ] = "AS_OBSERVED"
    trace_id_field: str | None = Field(default=None, min_length=1, max_length=255)
    severity_filter: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    maximum_result_count: int = Field(default=200, ge=1, le=200)
    maximum_response_bytes: int = Field(default=10_000_000, ge=1, le=10_000_000)

    @field_validator("index_pattern")
    @classmethod
    def index_pattern_is_bounded(cls, value: str) -> str:
        if (
            ".." in value
            or not re.fullmatch(r"[A-Za-z0-9_.*,-]+", value)
            or value in {"*", "_all"}
        ):
            raise ValueError("OpenSearch index pattern is invalid")
        return value

    @field_validator("severity_filter")
    @classmethod
    def severity_filter_is_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if normalized != value or any(
            item not in {"WARN", "ERROR", "FATAL", "DIAGNOSTIC"}
            for item in normalized
        ):
            raise ValueError("OpenSearch severity filter is invalid")
        return normalized


class JaegerConnectorSettingsV1(ProductModelV1):
    service_field_behavior: str = Field(
        default="serviceName",
        pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,119}$",
    )
    lookback_seconds: int = Field(default=3600, ge=1, le=3600)
    limit: int = Field(default=100, ge=1, le=100)
    minimum_duration_ms: int = Field(default=0, ge=0, le=3_600_000)
    tags: dict[str, str] = Field(default_factory=dict, max_length=20)
    maximum_response_bytes: int = Field(default=10_000_000, ge=1, le=10_000_000)

    @field_validator("tags")
    @classmethod
    def tags_are_bounded_and_non_secret(cls, value: dict[str, str]) -> dict[str, str]:
        if any(_SECRET_FIELD_PATTERN.search(item) for item in value):
            raise ValueError("Jaeger tag name is secret-bearing")
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,254}", key)
            or not tag_value
            or len(tag_value) > 255
            or any(character in tag_value for character in "\r\n\x00")
            for key, tag_value in value.items()
        ):
            raise ValueError("Jaeger tag filter is invalid")
        return dict(sorted(value.items()))


class HttpHealthTargetSettingsV1(ProductModelV1):
    service_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    health_url: str
    success_statuses: tuple[int, ...] = (200,)
    timeout_seconds: float | None = Field(default=None, gt=0, le=60)
    healthy_json_field: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("health_url")
    @classmethod
    def health_url_is_non_secret(cls, value: str) -> str:
        return _validate_connector_url(value)

    @field_validator("success_statuses")
    @classmethod
    def statuses_are_bounded(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        normalized = tuple(sorted(set(value)))
        if not normalized or any(status < 100 or status > 599 for status in normalized):
            raise ValueError("health success status is invalid")
        return normalized


class HttpHealthConnectorSettingsV1(ProductModelV1):
    services: tuple[HttpHealthTargetSettingsV1, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )

    @model_validator(mode="after")
    def service_ids_are_unique(self) -> "HttpHealthConnectorSettingsV1":
        service_ids = [service.service_id for service in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("health service IDs must be unique")
        return self


class PilotRuntimeConnectorSettingsV02(ProductModelV1):
    snapshot_ref: str = Field(
        pattern=r"^pilot/[a-zA-Z0-9_.-]{1,120}\.json$",
    )
    authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    maximum_age_seconds: int = Field(default=300, ge=1, le=600)


def _validate_connector_settings(
    kind: ConnectorKindV1,
    settings: dict[str, Any],
) -> dict[str, Any]:
    model: ProductModelV1
    if kind is ConnectorKindV1.FIXTURE:
        model = FixtureConnectorSettingsV1.model_validate(settings)
    elif kind is ConnectorKindV1.PROMETHEUS:
        model = PrometheusConnectorSettingsV1.model_validate(settings)
    elif kind is ConnectorKindV1.OPENSEARCH:
        model = OpenSearchConnectorSettingsV1.model_validate(settings)
    elif kind is ConnectorKindV1.JAEGER:
        model = JaegerConnectorSettingsV1.model_validate(settings)
    elif kind is ConnectorKindV1.HTTP_HEALTH:
        model = HttpHealthConnectorSettingsV1.model_validate(settings)
    elif kind is ConnectorKindV1.PILOT_RUNTIME:
        model = PilotRuntimeConnectorSettingsV02.model_validate(settings)
    else:
        raise ValueError("connector kind is not supported")
    return model.model_dump(mode="json", exclude_none=True)


class ConnectorConfigV1(ProductModelV1):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    kind: ConnectorKindV1
    endpoint: str | None = Field(default=None, max_length=2048)
    settings: dict[str, Any] = Field(default_factory=dict)
    credential_refs: dict[str, str] = Field(default_factory=dict)

    @field_validator("credential_refs")
    @classmethod
    def credential_refs_are_indirect(cls, value: dict[str, str]) -> dict[str, str]:
        for name, reference in value.items():
            if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", name):
                raise ValueError("credential reference name is invalid")
            if reference.startswith("env:"):
                variable = reference.removeprefix("env:")
                if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", variable):
                    raise ValueError("environment credential reference is invalid")
            elif reference.startswith("file:"):
                secret_path = reference.removeprefix("file:")
                if not secret_path.startswith("/") or "\x00" in secret_path:
                    raise ValueError("file credential reference must be absolute")
            else:
                raise ValueError("credentials must use env: or file: references")
            if len(reference) > 512:
                raise ValueError("credential reference is invalid")
        names = set(value)
        allowed = {"bearer", "basic_username", "basic_password"}
        header_names = tuple(name for name in names if name.startswith("header."))
        unknown = {
            name for name in names if name not in allowed and not name.startswith("header.")
        }
        basic = names.intersection({"basic_username", "basic_password"})
        blocked_headers = {
            "authorization",
            "connection",
            "content-length",
            "cookie",
            "host",
            "proxy-authorization",
            "set-cookie",
            "transfer-encoding",
        }
        if (
            unknown
            or len(header_names) > 16
            or ("bearer" in names and basic)
            or (basic and basic != {"basic_username", "basic_password"})
        ):
            raise ValueError("credential reference configuration is invalid")
        for reference_name in header_names:
            header_name = reference_name.removeprefix("header.")
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header_name)
                or header_name.casefold() in blocked_headers
            ):
                raise ValueError("static credential header is invalid")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def reject_inline_credentials(self) -> "ConnectorConfigV1":
        endpoint_required = self.kind in {
            ConnectorKindV1.PROMETHEUS,
            ConnectorKindV1.OPENSEARCH,
            ConnectorKindV1.JAEGER,
        }
        if endpoint_required and self.endpoint is None:
            raise ValueError("connector endpoint is required for this kind")
        if self.endpoint is not None:
            self.endpoint = _validate_connector_url(self.endpoint)
        if not endpoint_required and self.endpoint is not None:
            raise ValueError("connector endpoint is not supported for this kind")
        self.settings = _validate_connector_settings(self.kind, self.settings)
        return self


class ServiceSourceAliasesV1(ProductModelV1):
    prometheus: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    opensearch: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    jaeger: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    http_health: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @field_validator("prometheus", "opensearch", "jaeger", "http_health")
    @classmethod
    def aliases_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if len(normalized) != len(value):
            raise ValueError("service aliases contain duplicates")
        if any(
            not alias
            or len(alias) > 120
            or alias != alias.strip()
            or any(character in alias for character in "\r\n\x00")
            for alias in normalized
        ):
            raise ValueError("service alias is invalid")
        return normalized

    @model_validator(mode="after")
    def require_source_specific_alias_safety(self) -> "ServiceSourceAliasesV1":
        if any(
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", alias)
            for alias in self.prometheus
        ):
            raise ValueError("Prometheus service alias is invalid")
        return self


class ServiceIdentityRuleV1(ProductModelV1):
    logical_service: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    aliases: ServiceSourceAliasesV1 = Field(default_factory=ServiceSourceAliasesV1)
    approved_many_to_one: bool = False

    @model_validator(mode="after")
    def require_approved_many_to_one(self) -> "ServiceIdentityRuleV1":
        alias_groups = self.aliases.model_dump(mode="python").values()
        if not self.approved_many_to_one and any(len(group) > 1 for group in alias_groups):
            raise ValueError("service alias many-to-one mapping is not approved")
        return self


class ServiceIdentityPolicyV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.service-identity-policy.v1"] = (
        "ecomsre.product.service-identity-policy.v1"
    )
    canonical_field: str | None = Field(default=None, min_length=1, max_length=255)
    prometheus_label: str | None = Field(default=None, min_length=1, max_length=255)
    opensearch_field: str | None = Field(default=None, min_length=1, max_length=255)
    jaeger_service_field: str | None = Field(default=None, min_length=1, max_length=255)
    health_service_field: str | None = Field(default=None, min_length=1, max_length=255)
    services: tuple[ServiceIdentityRuleV1, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )

    @model_validator(mode="after")
    def require_unambiguous_rules(self) -> "ServiceIdentityPolicyV1":
        logical = tuple(item.logical_service for item in self.services)
        if logical != tuple(sorted(set(logical))):
            raise ValueError("service identity logical services are not canonical")
        owners: dict[tuple[str, str], str] = {}
        for rule in self.services:
            for source, aliases in rule.aliases.model_dump(mode="python").items():
                for alias in aliases:
                    key = (source, alias)
                    previous = owners.setdefault(key, rule.logical_service)
                    if previous != rule.logical_service:
                        raise ValueError("service identity alias collision")
        for source in ServiceSourceAliasesV1.model_fields:
            if sum(len(getattr(rule.aliases, source)) for rule in self.services) > 20:
                raise ValueError("service identity source alias fanout exceeds the bound")
        return self


class ServiceIdentityV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.service-identity.v1"] = (
        "ecomsre.product.service-identity.v1"
    )
    service_id: str = Field(pattern=r"^svc-[0-9a-f]{24}$")
    logical_service: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    aliases: ServiceSourceAliasesV1 = Field(default_factory=ServiceSourceAliasesV1)


class ServiceIdentityMapV1(ProductModelV1):
    schema_version: Literal["ecomsre.product.service-identity-map.v1"] = (
        "ecomsre.product.service-identity-map.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    services: tuple[ServiceIdentityV1, ...] = Field(max_length=20)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        environment_id: str,
        services: tuple[ServiceIdentityV1, ...],
    ) -> "ServiceIdentityMapV1":
        canonical = tuple(sorted(services, key=lambda item: item.logical_service))
        payload = {
            "schema_version": "ecomsre.product.service-identity-map.v1",
            "environment_id": environment_id,
            "services": tuple(item.model_dump(mode="json") for item in canonical),
        }
        return cls.model_validate(
            {**payload, "identity_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_bound_canonical_map(self) -> "ServiceIdentityMapV1":
        logical = tuple(item.logical_service for item in self.services)
        service_ids = tuple(item.service_id for item in self.services)
        if logical != tuple(sorted(set(logical))) or len(service_ids) != len(
            set(service_ids)
        ):
            raise ValueError("service identity map is not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"identity_sha256"})
        )
        if self.identity_sha256 != expected:
            raise ValueError("service identity map digest differs")
        return self


class EnvironmentCreateV1(ProductModelV1):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    service_identity_policy: ServiceIdentityPolicyV1 = Field(
        default_factory=ServiceIdentityPolicyV1
    )
    connector_configs: tuple[ConnectorConfigV1, ...] = Field(
        default_factory=tuple,
        max_length=5,
    )
    explicit_service_catalog: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=20,
    )

    @model_validator(mode="after")
    def require_canonical_collections(self) -> "EnvironmentCreateV1":
        connector_names = [connector.name for connector in self.connector_configs]
        if len(connector_names) != len(set(connector_names)):
            raise ValueError("connector names must be unique within an environment")
        connector_kinds_sequence = [
            connector.kind for connector in self.connector_configs
        ]
        if len(connector_kinds_sequence) != len(set(connector_kinds_sequence)):
            raise ValueError("connector kinds must be unique within an environment")
        connector_kinds = {connector.kind for connector in self.connector_configs}
        allowed_fixture_mix = {
            ConnectorKindV1.FIXTURE,
            ConnectorKindV1.PILOT_RUNTIME,
        }
        if (
            ConnectorKindV1.FIXTURE in connector_kinds
            and not connector_kinds.issubset(allowed_fixture_mix)
        ):
            raise ValueError("fixture connectors cannot be mixed with real connectors")
        catalog = tuple(sorted(set(self.explicit_service_catalog)))
        for service_id in catalog:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,120}", service_id):
                raise ValueError("explicit service ID is invalid")
        self.explicit_service_catalog = catalog
        return self


class EnvironmentRecordV1(EnvironmentCreateV1):
    schema_version: str = "ecomsre.product.environment.v1"
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    created_at: str
    updated_at: str


class EnvironmentListV1(ProductModelV1):
    items: tuple[EnvironmentRecordV1, ...]


class HealthResultV1(ProductModelV1):
    status: str


__all__ = (
    "ConnectorConfigV1",
    "ConnectorKindV1",
    "EnvironmentCreateV1",
    "EnvironmentListV1",
    "EnvironmentRecordV1",
    "HealthResultV1",
    "ProductModelV1",
    "ServiceIdentityMapV1",
    "ServiceIdentityPolicyV1",
    "ServiceIdentityRuleV1",
    "ServiceIdentityV1",
    "ServiceSourceAliasesV1",
)
