"""Closed constructor registry for Product connector implementations."""

from __future__ import annotations

from typing import Callable, Mapping

import httpx

from ecomsre.product.connectors.base import ProductConnectorV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.http_health import HttpHealthConnectorV1
from ecomsre.product.connectors.jaeger import JaegerConnectorV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.contracts import ConnectorConfigV1, ConnectorKindV1
from ecomsre.product.errors import ProductError


class ConnectorRegistryV1:
    def __init__(
        self,
        *,
        credential_resolver: CredentialResolverV1,
        timeout_seconds: float,
        before_request: Callable[[], None] | None = None,
        transports: Mapping[str, httpx.BaseTransport] | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver
        self._timeout_seconds = timeout_seconds
        self._before_request = before_request
        self._transports = dict(transports or {})

    def create(self, config: ConnectorConfigV1) -> ProductConnectorV1:
        transport = self._transports.get(config.name)
        if config.kind is ConnectorKindV1.PROMETHEUS:
            return PrometheusConnectorV1(
                config,
                credential_resolver=self._credential_resolver,
                timeout_seconds=self._timeout_seconds,
                before_request=self._before_request,
                transport=transport,
            )
        if config.kind is ConnectorKindV1.OPENSEARCH:
            return OpenSearchConnectorV1(
                config,
                credential_resolver=self._credential_resolver,
                timeout_seconds=self._timeout_seconds,
                before_request=self._before_request,
                transport=transport,
            )
        if config.kind is ConnectorKindV1.JAEGER:
            return JaegerConnectorV1(
                config,
                credential_resolver=self._credential_resolver,
                timeout_seconds=self._timeout_seconds,
                before_request=self._before_request,
                transport=transport,
            )
        if config.kind is ConnectorKindV1.HTTP_HEALTH:
            return HttpHealthConnectorV1(
                config,
                credential_resolver=self._credential_resolver,
                timeout_seconds=self._timeout_seconds,
                before_request=self._before_request,
                transport=transport,
            )
        raise ProductError(
            "CONNECTOR_UNAVAILABLE",
            "The configured connector kind is unavailable for real verification.",
        )


__all__ = ("ConnectorRegistryV1",)
