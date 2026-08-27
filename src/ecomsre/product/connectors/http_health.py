"""Read-only HTTP health Product connector."""

from __future__ import annotations

from typing import Callable, Mapping

import httpx

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    RuntimeRecordV22,
    RuntimeStateV22,
)
from ecomsre.product.connectors._http import (
    BoundedHttpTransportV1,
    ConnectorRequestError,
)
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorCapabilityV1,
    ConnectorHealthResultV1,
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    HttpHealthConnectorSettingsV1,
    HttpHealthTargetSettingsV1,
)


def _field(payload: object, path: str) -> object:
    current = payload
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise ValueError("health response field is unavailable")
        current = current[segment]
    return current


class HttpHealthConnectorV1:
    def __init__(
        self,
        config: ConnectorConfigV1,
        *,
        credential_resolver: CredentialResolverV1,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        if config.kind is not ConnectorKindV1.HTTP_HEALTH:
            raise ValueError("HTTP health connector configuration is invalid")
        self.config = config
        self._settings = HttpHealthConnectorSettingsV1.model_validate(config.settings)
        self._targets = {item.service_id: item for item in self._settings.services}
        self._http = BoundedHttpTransportV1(
            credential_resolver=credential_resolver,
            credential_refs=config.credential_refs,
            timeout_seconds=timeout_seconds,
            maximum_response_bytes=1_000_000,
            transport=transport,
            before_request=before_request,
        )

    def capabilities(self) -> tuple[ConnectorCapabilityV1, ...]:
        return (
            ConnectorCapabilityV1(
                source=EvidenceSourceV22.RUNTIME,
                supports_historical_range=False,
                supports_multi_target=True,
                supports_service_discovery=True,
                supports_baseline=True,
                supports_target_complete_coverage=False,
                maximum_window_seconds=0,
            ),
        )

    def verify(self) -> ConnectorHealthResultV1:
        successes = 0
        latency_ms = 0.0
        last_error: ConnectorRequestError | None = None
        for target in self._settings.services:
            try:
                _, request_latency = self._probe(target)
                successes += 1
                latency_ms += request_latency
            except ConnectorRequestError as error:
                last_error = error
                latency_ms += error.latency_ms
            except ValueError:
                last_error = ConnectorRequestError(
                    ReadSourceStatusV22.FAILURE_SCHEMA,
                    "CONNECTOR_SCHEMA_INVALID",
                    0,
                )
        if successes == len(self._settings.services) and successes > 0:
            status = ConnectorAvailabilityV1.AVAILABLE
            safe_error_code = None
        elif successes:
            status = ConnectorAvailabilityV1.PARTIAL
            safe_error_code = "CONNECTOR_PARTIAL"
        else:
            status = ConnectorAvailabilityV1.UNAVAILABLE
            safe_error_code = (
                last_error.safe_error_code
                if last_error is not None
                else "CONNECTOR_NO_TARGETS"
            )
        return ConnectorHealthResultV1(
            connector_name=self.config.name,
            kind=self.config.kind,
            status=status,
            capabilities=self.capabilities(),
            discovered_services=tuple(sorted(self._targets)),
            safe_error_code=safe_error_code,
            latency_ms=latency_ms,
        )

    def query(
        self,
        context: ConnectorQueryContextV1,
    ) -> tuple[ConnectorQueryResultV1, ...]:
        context = ConnectorQueryContextV1.model_validate(context.model_dump())
        results: list[ConnectorQueryResultV1] = []
        for service in context.requested_services:
            target = next(
                (
                    self._targets.get(alias)
                    for alias in context.aliases_for(service)
                    if self._targets.get(alias) is not None
                ),
                None,
            )
            if target is None:
                results.append(
                    self._failure(
                        context,
                        service,
                        ConnectorRequestError(
                            ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                            "CONNECTOR_TARGET_UNAVAILABLE",
                            0,
                        ),
                    )
                )
                continue
            try:
                healthy, latency_ms = self._probe(target)
            except ConnectorRequestError as error:
                results.append(self._failure(context, service, error))
                continue
            except ValueError:
                results.append(
                    self._failure(
                        context,
                        service,
                        ConnectorRequestError(
                            ReadSourceStatusV22.FAILURE_SCHEMA,
                            "CONNECTOR_SCHEMA_INVALID",
                            0,
                        ),
                    )
                )
                continue
            results.append(
                ConnectorQueryResultV1.build(
                    source=EvidenceSourceV22.RUNTIME,
                    status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                    requested_services=(service,),
                    covered_services=(service,),
                    window=context.window,
                    records=(
                        RuntimeRecordV22(
                            schema_version="dta-v22.runtime-record.v1",
                            service=service,
                            state=RuntimeStateV22.RUNNING,
                            healthy=healthy,
                            restart_count=0,
                        ),
                    ),
                    truncated=False,
                    safe_error_code=None,
                    latency_ms=latency_ms,
                )
            )
        return tuple(results)

    def _probe(
        self,
        target: HttpHealthTargetSettingsV1,
    ) -> tuple[bool, float]:
        if target.healthy_json_field is None:
            _content, status_code, latency_ms = self._http.request_bytes(
                "GET",
                target.health_url,
                allow_http_error_status=True,
                timeout_seconds=target.timeout_seconds,
            )
            payload: object = None
        else:
            payload, status_code, latency_ms = self._http.request_json(
                "GET",
                target.health_url,
                allow_http_error_status=True,
                timeout_seconds=target.timeout_seconds,
            )
        healthy = status_code in target.success_statuses
        if target.healthy_json_field is not None:
            observed = _field(payload, target.healthy_json_field)
            if not isinstance(observed, bool):
                raise ValueError("health response field is not boolean")
            healthy = healthy and observed
        return healthy, latency_ms

    @staticmethod
    def _failure(
        context: ConnectorQueryContextV1,
        service: str,
        error: ConnectorRequestError,
    ) -> ConnectorQueryResultV1:
        return ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.RUNTIME,
            status=error.status,
            requested_services=(service,),
            covered_services=(),
            window=context.window,
            records=(),
            truncated=False,
            safe_error_code=error.safe_error_code,
            latency_ms=error.latency_ms,
        )

    def close(self) -> None:
        self._http.close()


__all__ = ("HttpHealthConnectorV1",)
