"""Public Product API contracts for the durable shell."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    maximum_sample_count: int = Field(default=10000, ge=1)
    maximum_series_count: int = Field(default=1000, ge=1)
    maximum_response_bytes: int = Field(default=10_000_000, ge=1)

    @field_validator("query_templates")
    @classmethod
    def query_templates_are_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"request_support", "error_rate", "latency", "cpu", "memory"}
        if not set(value).issubset(allowed):
            raise ValueError("Prometheus query template name is not supported")
        if any(not template or len(template) > 4000 for template in value.values()):
            raise ValueError("Prometheus query template is invalid")
        return dict(sorted(value.items()))


class OpenSearchConnectorSettingsV1(ProductModelV1):
    index_pattern: str | None = Field(default=None, min_length=1, max_length=255)
    timestamp_field: str | None = Field(default=None, min_length=1, max_length=255)
    service_field: str | None = Field(default=None, min_length=1, max_length=255)
    severity_field: str | None = Field(default=None, min_length=1, max_length=255)
    message_field: str | None = Field(default=None, min_length=1, max_length=255)
    trace_id_field: str | None = Field(default=None, min_length=1, max_length=255)
    maximum_result_count: int = Field(default=200, ge=1)
    maximum_response_bytes: int = Field(default=10_000_000, ge=1)


class JaegerConnectorSettingsV1(ProductModelV1):
    service_field_behavior: str = Field(default="serviceName", max_length=120)
    lookback_seconds: int = Field(default=3600, ge=1)
    limit: int = Field(default=100, ge=1)
    minimum_duration_ms: int = Field(default=0, ge=0)
    tag_names: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    maximum_response_bytes: int = Field(default=10_000_000, ge=1)

    @field_validator("tag_names")
    @classmethod
    def tag_names_are_not_secret_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(_SECRET_FIELD_PATTERN.search(item) for item in value):
            raise ValueError("Jaeger tag name is secret-bearing")
        if any(not item or len(item) > 255 for item in value):
            raise ValueError("Jaeger tag name is invalid")
        return tuple(sorted(set(value)))


class HttpHealthTargetSettingsV1(ProductModelV1):
    service_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    health_url: str
    success_statuses: tuple[int, ...] = (200,)
    timeout_seconds: float | None = Field(default=None, gt=0)
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
        max_length=1000,
    )

    @model_validator(mode="after")
    def service_ids_are_unique(self) -> "HttpHealthConnectorSettingsV1":
        service_ids = [service.service_id for service in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("health service IDs must be unique")
        return self


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
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def reject_inline_credentials(self) -> "ConnectorConfigV1":
        if self.endpoint is not None:
            self.endpoint = _validate_connector_url(self.endpoint)
        self.settings = _validate_connector_settings(self.kind, self.settings)
        return self


class EnvironmentCreateV1(ProductModelV1):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    service_identity_policy: dict[str, Any] = Field(default_factory=dict)
    connector_configs: tuple[ConnectorConfigV1, ...] = Field(default_factory=tuple)
    explicit_service_catalog: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=1000,
    )

    @model_validator(mode="after")
    def require_canonical_collections(self) -> "EnvironmentCreateV1":
        connector_names = [connector.name for connector in self.connector_configs]
        if len(connector_names) != len(set(connector_names)):
            raise ValueError("connector names must be unique within an environment")
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
)
