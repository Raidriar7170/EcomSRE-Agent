"""Schema-aware parsing for the frozen OpenSearch service identity field."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class OpenSearchServiceIdentityReason(str, Enum):
    PARSED = "OPENSEARCH_SERVICE_IDENTITY_PARSED"
    MISSING = "OPENSEARCH_SERVICE_IDENTITY_MISSING"
    TYPE_INVALID = "OPENSEARCH_SERVICE_IDENTITY_TYPE_INVALID"
    SHAPE_INVALID = "OPENSEARCH_SERVICE_IDENTITY_SHAPE_INVALID"
    CONFLICT = "OPENSEARCH_SERVICE_IDENTITY_CONFLICT"
    FIELD_UNSUPPORTED = "OPENSEARCH_SERVICE_IDENTITY_FIELD_UNSUPPORTED"


@dataclass(frozen=True)
class OpenSearchServiceIdentity:
    value: str | None
    reason: OpenSearchServiceIdentityReason

    @property
    def parsed(self) -> bool:
        return self.reason is OpenSearchServiceIdentityReason.PARSED


def parse_opensearch_service_identity(
    source: object,
    *,
    field: str,
) -> OpenSearchServiceIdentity:
    """Parse only the approved representations of resource.service.name."""
    if field != "resource.service.name":
        return OpenSearchServiceIdentity(
            value=None,
            reason=OpenSearchServiceIdentityReason.FIELD_UNSUPPORTED,
        )
    if not isinstance(source, dict):
        return OpenSearchServiceIdentity(
            value=None,
            reason=OpenSearchServiceIdentityReason.SHAPE_INVALID,
        )
    if "resource" not in source:
        return OpenSearchServiceIdentity(
            value=None,
            reason=OpenSearchServiceIdentityReason.MISSING,
        )
    resource: Any = source["resource"]
    if not isinstance(resource, dict):
        return OpenSearchServiceIdentity(
            value=None,
            reason=OpenSearchServiceIdentityReason.SHAPE_INVALID,
        )

    flattened_present = "service.name" in resource
    flattened = resource.get("service.name")
    if flattened_present and not isinstance(flattened, str):
        return OpenSearchServiceIdentity(
            value=None,
            reason=OpenSearchServiceIdentityReason.TYPE_INVALID,
        )

    nested_present = False
    nested: Any = None
    if "service" in resource:
        service = resource["service"]
        if not isinstance(service, dict):
            return OpenSearchServiceIdentity(
                value=None,
                reason=OpenSearchServiceIdentityReason.SHAPE_INVALID,
            )
        nested_present = "name" in service
        nested = service.get("name")
        if nested_present and not isinstance(nested, str):
            return OpenSearchServiceIdentity(
                value=None,
                reason=OpenSearchServiceIdentityReason.TYPE_INVALID,
            )

    if flattened_present and nested_present and flattened != nested:
        return OpenSearchServiceIdentity(
            value=None,
            reason=OpenSearchServiceIdentityReason.CONFLICT,
        )
    if flattened_present:
        return OpenSearchServiceIdentity(
            value=flattened,
            reason=OpenSearchServiceIdentityReason.PARSED,
        )
    if nested_present:
        return OpenSearchServiceIdentity(
            value=nested,
            reason=OpenSearchServiceIdentityReason.PARSED,
        )
    return OpenSearchServiceIdentity(
        value=None,
        reason=OpenSearchServiceIdentityReason.MISSING,
    )
