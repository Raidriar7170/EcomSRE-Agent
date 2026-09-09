import json
import pytest
from ecomsre.product.contracts import EnvironmentCreateV1
from scripts.product.minimal_payment_acceptance_v040.plan import build_plan, REASONS
from scripts.product.minimal_payment_acceptance_v040.product import environment_payload
from scripts.product.minimal_payment_acceptance_v040.owned import (
    Owned,
    validate_container,
)
from scripts.product.minimal_payment_acceptance_v040.observer import memory_bytes


def test_minimal_dependency_closure_isolated_controls(tmp_path):
    images = {
        name: {"Id": "sha256:" + name}
        for name in ("product", "payment", "flagd", "otel-collector", "prometheus")
    }
    ports = {
        name: 23000 + n
        for n, name in enumerate(
            ("api", "probe", "flagd", "control", "prometheus", "observer", "gateway")
        )
    }
    plan = build_plan(
        tmp_path,
        "test",
        images,
        ports,
        {"goal": "test"},
        {k: k * 32 for k in ("read", "write", "observer", "admin")},
    )
    assert set(plan["services"]) == set(REASONS)
    assert plan["services"]["remediation-executor"]["network_mode"] == "none"
    assert plan["services"]["api"]["networks"] == ["observation"]
    assert plan["services"]["worker"]["networks"] == ["observation"]
    assert plan["services"]["payment-control"]["networks"] == ["control"]
    assert not any("/var/run/docker.sock" in str(s) for s in plan["services"].values())
    assert all(
        p["host_ip"] == "127.0.0.1"
        for s in plan["services"].values()
        for p in s.get("ports", [])
    )
    assert all(
        m["volume"]["nocopy"]
        for s in plan["services"].values()
        for m in s["volumes"]
        if m["type"] == "volume"
    )


def container_fixture():
    spec = {
        "image": "image",
        "volumes": [
            {
                "target": "/config",
                "source": "/private/config",
                "type": "bind",
                "read_only": True,
            }
        ],
    }
    image = {
        "Id": "image-id",
        "Config": {"Entrypoint": ["/node"], "Cmd": ["server.js"]},
    }
    row = {
        "Config": {"Labels": {"goal": "bound"}},
        "HostConfig": {
            "Privileged": False,
            "NetworkMode": "net",
            "PidMode": "",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "PortBindings": {},
        },
        "Image": "image-id",
        "Path": "/node",
        "Args": ["server.js"],
        "Mounts": [
            {
                "Type": "bind",
                "Destination": "/config",
                "Source": "/private/config",
                "RW": False,
            }
        ],
        "NetworkSettings": {"Networks": {"net": {"NetworkID": "net-id"}}},
    }
    return row, spec, {"image": image}


def test_effective_image_default_command_and_semantic_mount():
    row, spec, images = container_fixture()
    validate_container(row, spec, {"goal": "bound"}, images, {"net-id"})
    row["HostConfig"]["UnrelatedDockerDefault"] = None
    row["Mounts"].reverse()
    validate_container(row, spec, {"goal": "bound"}, images, {"net-id"})


@pytest.mark.parametrize(
    "field,value", [("Image", "other"), ("Path", "/bin/sh"), ("Args", ["other"])]
)
def test_image_and_command_drift_denied(field, value):
    row, spec, images = container_fixture()
    row[field] = value
    with pytest.raises(ValueError):
        validate_container(row, spec, {"goal": "bound"}, images, {"net-id"})


def test_unknown_mount_and_port_denied():
    row, spec, images = container_fixture()
    row["Mounts"].append(
        {
            "Destination": "/docker",
            "Type": "bind",
            "Source": "/var/run/docker.sock",
            "RW": True,
        }
    )
    with pytest.raises(ValueError, match="MOUNT_SET"):
        validate_container(row, spec, {"goal": "bound"}, images, {"net-id"})
    row, spec, images = container_fixture()
    row["HostConfig"]["PortBindings"] = {
        "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}]
    }
    with pytest.raises(ValueError, match="PORT_SET"):
        validate_container(row, spec, {"goal": "bound"}, images, {"net-id"})


def test_cleanup_rejects_recreated_or_unknown_resource_before_mutation(
    monkeypatch, tmp_path
):
    owner = object.__new__(Owned)
    owner.labels = {"goal": "bound"}
    owner.births = {"container": {"id": {"Id": "id", "Created": "first"}}}
    owner.fresh = lambda: None
    calls = []

    def command(*args):
        calls.append(args)
        return json.dumps(
            [{"Id": "id", "Created": "second", "Config": {"Labels": {"goal": "bound"}}}]
        )

    monkeypatch.setattr(
        "scripts.product.minimal_payment_acceptance_v040.owned.command", command
    )
    with pytest.raises(ValueError, match="BIRTH_IDENTITY_DRIFT"):
        owner.cleanup()
    assert all("inspect" in call for call in calls)
    with pytest.raises(KeyError):
        owner.require_birth("container", "unknown")


def test_real_query_configuration_no_constant_success():
    payload = EnvironmentCreateV1.model_validate(environment_payload("minimal"))
    templates = payload.connector_configs[0].settings["query_templates"]
    assert "rate(payment_probe_errors_total" in templates["error_rate"]
    assert "rate(payment_probe_requests_total" in templates["error_rate"]
    assert templates["cpu"].startswith("payment_owned_cpu_percent")
    assert templates["memory"].startswith("payment_owned_memory_bytes")
    assert all("vector(0)" not in value for value in templates.values())


def test_resource_units_and_unknown_unit_fail_closed():
    assert memory_bytes("12MiB") == 12 * 1024 * 1024
    assert memory_bytes("1.5kB") == 1500
    with pytest.raises(ValueError):
        memory_bytes("unknown")


def test_only_birth_bound_none_network_membership_is_normalized(monkeypatch):
    owner = object.__new__(Owned)
    owner.births = {"container": {"owned": {}}, "network": {}, "volume": {}}
    owner.before = {
        "container": {},
        "network": {"none-id": {"Name": "none", "Containers": {}, "Driver": "null"}},
        "volume": {},
    }
    current = {
        "container": {"owned": {}},
        "network": {
            "none-id": {
                "Name": "none",
                "Containers": {"owned": {"EndpointID": "owned-endpoint"}},
                "Driver": "null",
            }
        },
        "volume": {},
    }
    monkeypatch.setattr(
        "scripts.product.minimal_payment_acceptance_v040.owned.inventory",
        lambda: json.loads(json.dumps(current)),
    )
    assert owner.unchanged()
    current["network"]["none-id"]["Containers"]["unknown"] = {"EndpointID": "external"}
    assert not owner.unchanged()
    current["network"]["none-id"]["Containers"].pop("unknown")
    current["network"]["none-id"]["Driver"] = "bridge"
    assert not owner.unchanged()


def test_harness_windows_bind_exact_end_and_exclude_late_responses():
    from datetime import UTC, datetime, timedelta
    from ecomsre.product.remediation.execution_contracts import RecoveryPolicyV1
    from scripts.product.minimal_payment_acceptance_v040.observer import (
        window_observation,
    )

    start = datetime(2026, 9, 9, tzinfo=UTC)
    end = start + timedelta(seconds=10)
    policy = RecoveryPolicyV1.build(
        environment_id="env-" + "1" * 24,
        baseline_sha256="a" * 64,
        baseline_configuration_digest="b" * 64,
        fault_configuration_digest="c" * 64,
        target_identity_digest="d" * 64,
        control_identity_sha256="e" * 64,
        environment_ownership_digest="f" * 64,
        business_error_ratio_max=0.01,
        minimum_business_requests=10,
        window_seconds=10,
        created_at=start,
    )
    probes = [
        {
            "at": (start + timedelta(seconds=n / 10)).isoformat(),
            "value": {"ok": True, "grpc_code": 0},
        }
        for n in range(1, 101)
    ]
    probes.append(
        {
            "at": (end + timedelta(seconds=1)).isoformat(),
            "value": {"ok": False, "grpc_code": 2},
        }
    )
    value = window_observation(
        policy=policy,
        start=start,
        end=end,
        elapsed=10001,
        requests=probes,
        before=("b" * 64, True),
        after=("b" * 64, True),
    )
    assert value.created_at == value.ended_at == end
    assert value.business_requests == 100 and value.business_errors == 0
    assert value.business_observation_kind == "DIRECT_PAYMENT_TRAFFIC"
    assert value.flag_evaluation_restored
    value = window_observation(
        policy=policy,
        start=start,
        end=end,
        elapsed=10001,
        requests=probes,
        before=("c" * 64, False),
        after=("b" * 64, True),
    )
    assert not value.flag_evaluation_restored
    with pytest.raises(ValueError, match="OBSERVATION_WINDOW_DURATION"):
        window_observation(
            policy=policy,
            start=start,
            end=end + timedelta(seconds=1),
            elapsed=11000,
            requests=probes,
            before=("b" * 64, True),
            after=("b" * 64, True),
        )
