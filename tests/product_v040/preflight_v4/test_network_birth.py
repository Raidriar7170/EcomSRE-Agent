"""Only the observed ordinary bridge defaults can supplement an option-free plan."""

from copy import deepcopy
import pytest
from scripts.product.preflight_v040_v4.birth import validate_storage
from scripts.product.preflight_v040_v4.common import Failure


def network():
    plan = {"name": "owned", "driver": "bridge", "labels": {"owner": "attempt"}}
    row = {
        "Id": "a" * 64,
        "Name": plan["name"],
        "Driver": plan["driver"],
        "Labels": plan["labels"],
        "Scope": "local",
        "EnableIPv4": True,
        "EnableIPv6": False,
        "ConfigOnly": False,
        "Internal": False,
        "Ingress": False,
        "Attachable": False,
        "Containers": {},
        "Options": {
            "com.docker.network.enable_ipv4": "true",
            "com.docker.network.enable_ipv6": "false",
        },
    }
    return row, plan


def test_explicit_bridge_defaults_preserve_raw_record():
    row, plan = network()
    original = deepcopy(row)
    validate_storage("network", row, plan)
    assert row == original
    row["Options"] = {}
    validate_storage("network", row, plan)


@pytest.mark.parametrize(
    "delta",
    [
        {"Options": {"com.docker.network.enable_ipv4": "true"}},
        {"Options": {"com.docker.network.enable_ipv4": "true", "extra": "value"}},
        {
            "Options": {
                "com.docker.network.enable_ipv4": "false",
                "com.docker.network.enable_ipv6": "false",
            }
        },
        {"Driver": "overlay"},
        {"Scope": "swarm"},
        {"EnableIPv4": False},
        {"EnableIPv6": True},
        {"ConfigOnly": True},
        {"Internal": True},
        {"Ingress": True},
        {"Attachable": True},
        {"Containers": {"unknown": {}}},
    ],
)
def test_bridge_default_exception_rejects_authority_drift(delta):
    row, plan = network()
    row.update(delta)
    with pytest.raises(Failure, match="BIRTH_DRIFT"):
        validate_storage("network", row, plan)


def test_network_options_never_apply_to_a_volume():
    row, plan = network()
    with pytest.raises(Failure, match="STORAGE_BIRTH_DRIFT"):
        validate_storage("volume", row, plan)
