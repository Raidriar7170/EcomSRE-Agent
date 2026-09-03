import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre_live_sandbox.knowledge_v030 import (
    consumer_membership_healthy_v030,
    owned_runtime_observation_v030,
)
from ecomsre_live_sandbox.product_v030 import (
    ProductV030SandboxEnvironment,
    build_product_v030_runtime_bundle,
)


STATE = "GROUP COORDINATOR (ID) ASSIGNMENT-STRATEGY STATE #MEMBERS\nfraud-detection kafka:9092 (1) range Stable 1\n"
MEMBERS = "GROUP CONSUMER-ID HOST CLIENT-ID #PARTITIONS CURRENT-EPOCH CURRENT-ASSIGNMENT TARGET-EPOCH TARGET-ASSIGNMENT\nfraud-detection consumer-id /172.18.0.22 consumer-fraud-detection-1 1 - orders:0 - -\n"


@pytest.mark.parametrize(
    "native_health", [{"Status": "unhealthy"}, {"Status": "starting"}, {}, None]
)
def test_membership_never_overrides_existing_nonhealthy_docker_health(
    tmp_path, monkeypatch, native_health
):
    root = Path(__file__).resolve().parents[2]
    environment = ProductV030SandboxEnvironment(
        repository_root=root,
        bundle=build_product_v030_runtime_bundle(root),
        flagd_directory=tmp_path,
    )
    monkeypatch.setattr(environment, "verify_owned_resources", lambda **_: {})
    monkeypatch.setattr(
        environment, "_owned_ids", lambda _: frozenset({"fraud", "broker"})
    )
    items = [
        {
            "Id": "fraud",
            "Config": {"Labels": {"com.docker.compose.service": "fraud-detection"}},
            "State": {"Running": True, "Health": native_health},
            "RestartCount": 0,
            "NetworkSettings": {
                "Networks": {
                    "ecomsre-live-sandbox-v1-default": {"IPAddress": "172.18.0.22"}
                }
            },
        },
        {"Id": "broker", "Config": {"Labels": {"com.docker.compose.service": "kafka"}}},
    ]

    def run(arguments, **_):
        if arguments[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps(items))
        return SimpleNamespace(stdout=MEMBERS if "--members" in arguments else STATE)

    monkeypatch.setattr(environment.runner, "run", run)
    services, _ = owned_runtime_observation_v030(
        environment, candidates=("fraud-detection",)
    )
    assert services["fraud-detection"]["healthy"] is False


def test_consumer_probe_binds_active_membership_to_exact_container_ip():
    assert consumer_membership_healthy_v030(
        state_output=STATE,
        members_output=MEMBERS,
        container_ip="172.18.0.22",
    )


@pytest.mark.parametrize(
    "state,members,ip",
    [
        (STATE.replace("Stable", "Empty"), MEMBERS, "172.18.0.22"),
        (STATE.replace("Stable 1", "Stable 0"), MEMBERS, "172.18.0.22"),
        (STATE, MEMBERS, "172.18.0.99"),
        (STATE, MEMBERS.replace("orders:0", "-"), "172.18.0.22"),
        (STATE, MEMBERS.replace("fraud-detection", "another-consumer"), "172.18.0.22"),
        ("", MEMBERS, "172.18.0.22"),
        (STATE, "", "172.18.0.22"),
        (STATE, MEMBERS + MEMBERS.splitlines()[1] + "\n", "172.18.0.22"),
    ],
)
def test_consumer_probe_never_infers_health_from_unknown_or_unbound_membership(
    state, members, ip
):
    assert not consumer_membership_healthy_v030(
        state_output=state, members_output=members, container_ip=ip
    )
