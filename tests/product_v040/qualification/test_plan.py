from copy import deepcopy
from typing import Any

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040.plan import container_role, stage_roles


def test_every_mutation_has_prebound_before_and_after_stages() -> None:
    sandbox: dict[str, Any] = {
        "services": {"kafka": {}},
        "volumes": {"data": {}, "secrets": {}, "config": {}},
        "networks": {"default": {}},
    }
    product: dict[str, Any] = {
        "services": {"api": {}, "worker": {}, "remediation-observer": {}},
        "networks": {"default": {}, "internal": {}},
    }
    stages, births = stage_roles(sandbox, product)
    names = [s["name"] for s in stages]
    assert len(names) == len(set(names))
    for i, stage in enumerate(stages):
        if stage["name"].startswith("AFTER_"):
            assert stages[i - 1]["name"] == stage["name"].replace(
                "AFTER_", "BEFORE_", 1
            )
    assert names.index(births["sandbox/volume/data"]) < names.index(
        "AFTER_COPYUP_CREATE"
    )
    assert names.index("AFTER_SENTINEL") < names.index("BEFORE_SANDBOX_START")
    assert names.index("AFTER_PROBE_REMOVE") < names.index("BEFORE_HEALTHY_CONTROL")
    assert stages[-1]["present_roles"] == []
    assert not any("executor" in key or "gateway" in key for key in births)


def role_fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    service = {
        "container_name": "test",
        "image": "image",
        "volumes": [{"type": "volume", "source": "data", "target": "/data"}],
    }
    compose = {"volumes": {"data": {"name": "owned-data"}}, "networks": {}}
    image = {
        "Id": "sha256:" + "1" * 64,
        "Config": {"Volumes": {"/data": {}}, "Env": [], "Labels": {}},
    }
    return service, compose, image


def test_every_declared_image_volume_is_explicitly_bound() -> None:
    service, compose, image = role_fixture()
    result = container_role(
        service, compose, "project", image, "AFTER_START", "identity"
    )
    assert result["image_config_volumes"] == ["/data"]
    assert result["mounts"]["/data"]["source"] == "owned-data"
    changed = deepcopy(image)
    changed["Config"]["Volumes"]["/unbound"] = {}
    with pytest.raises(QualificationBlocked, match="UNBOUND_IMAGE_VOLUME"):
        container_role(service, compose, "project", changed, "AFTER_START", "identity")
