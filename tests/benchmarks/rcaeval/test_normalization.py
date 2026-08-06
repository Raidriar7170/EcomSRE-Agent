from __future__ import annotations

import pytest

from ecomsre_rcaeval.normalization import (
    ServiceNormalizer,
    UnresolvedServiceAlias,
)


def test_service_normalization_is_explicit_and_canonical() -> None:
    normalizer = ServiceNormalizer(
        canonical_services=("checkoutservice", "front-end"),
        aliases={"frontend": "front-end"},
    )

    assert normalizer.normalize(" CHECKOUTSERVICE ") == "checkoutservice"
    assert normalizer.normalize("FrontEnd") == "front-end"

    with pytest.raises(UnresolvedServiceAlias, match="unresolved service alias"):
        normalizer.normalize("unknown-service")


def test_service_normalization_rejects_unlocked_alias_targets() -> None:
    with pytest.raises(ValueError, match="canonical target"):
        ServiceNormalizer(
            canonical_services=("checkoutservice",),
            aliases={"frontend": "front-end"},
        )
