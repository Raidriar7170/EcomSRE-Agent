"""Fake Docker regression tests; no local daemon, prior worktree, or credentials."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.product.cleanup_v040_v3_retained.core import (
    Authority,
    Blocked,
    Engine,
    KINDS,
    LABEL,
    PROBE,
    QUAL,
    command_values,
    digest,
    nonowned,
    seal,
    validate_pair,
)


@pytest.fixture
def fixture() -> tuple[Authority, dict[str, Any]]:
    networks: list[dict[str, Any]] = []
    for i, name in enumerate(
        (
            "ecomsre-product-v040_remediation-observation",
            "ecomsre-product-v040-default",
            "ecomsre-live-sandbox-v1-default",
        )
    ):
        networks.append(
            {
                "Id": f"{100 + i:064x}",
                "Name": name,
                "Labels": {LABEL: QUAL},
                "Driver": "bridge",
                "Internal": False,
                "Ingress": False,
                "Containers": {},
            }
        )
    names = [
        "ecomsre-v040-" + QUAL + "-" + x
        for x in (
            "astronomy-db-data",
            "jaeger-data",
            "prometheus-data",
            "qualification-kafka-config",
            "qualification-kafka-data",
            "qualification-kafka-secrets",
        )
    ]
    volumes = [
        {
            "Name": n,
            "Labels": {LABEL: QUAL},
            "Driver": "local",
            "Options": None,
            "Mountpoint": "/var/lib/docker/volumes/" + n + "/_data",
        }
        for n in names
    ]
    containers: list[dict[str, Any]] = []
    services: dict[str, Any] = {}
    for i in range(29):
        rid = PROBE if i == 28 else f"{i + 1:064x}"
        role = "kafka-volume-probe" if i == 28 else f"service-{i:02}"
        ep, cmd = (
            (["/bin/sleep"], ["2147483647"]) if i == 28 else (["entry"], ["default"])
        )
        c: dict[str, Any] = {
            "Id": rid,
            "Name": "/" + role,
            "Image": "image",
            "Config": {
                "Labels": {LABEL: QUAL, "com.docker.compose.service": role},
                "Image": "pinned",
                "Entrypoint": ep,
                "Cmd": cmd,
            },
            "HostConfig": {"OomKillDisable": None if i == 28 else False},
            "Mounts": [],
            "NetworkSettings": {"Networks": {}},
            "State": {"Running": i != 28, "Status": "running" if i != 28 else "exited"},
        }
        if i != 28:
            c["NetworkSettings"]["Networks"] = {
                networks[2]["Name"]: {"NetworkID": networks[2]["Id"]}
            }
            networks[2]["Containers"][rid] = {"EndpointID": "ep-" + rid}
            services[role] = {
                "entrypoint": None,
                "command": None,
                "depends_on": {f"service-{i - 1:02}": {}} if i else {},
            }
        containers.append(c)
    containers[0]["Mounts"] = [
        {
            "Name": names[0],
            "Type": "volume",
            "Source": volumes[0]["Mountpoint"],
            "Destination": "/data",
            "RW": True,
        }
    ]
    rows: dict[str, Any] = {"containers": containers, "networks": networks, "volumes": volumes}
    retained = {
        "qualification_id": QUAL,
        "resources": {
            k: [dict(r, raw_inspect_sha256=digest(r)) for r in rows[k]] for k in KINDS
        },
    }
    raw = {
        "inspect": deepcopy(rows),
        "daemon_before": {},
        "daemon_after": {},
        "platform_images": {
            "pinned": {"Config": {"Entrypoint": ["entry"], "Cmd": ["default"]}}
        },
    }
    plan = {
        "roles": {
            "probe/container/kafka-volume-probe": {
                "entrypoint": ["/bin/sleep"],
                "command": ["2147483647"],
            }
        }
    }
    authority = Authority(retained, raw, {"services": services}, plan)
    view = {
        "inspect": rows,
        "binding": {"daemon": "one"},
        "ids": {
            k: [r["Name" if k == "volumes" else "Id"] for r in rows[k]] for k in KINDS
        },
    }
    return authority, view


class Fake:
    def __init__(self, view: dict[str, Any], fail: bool = False):
        self.view = deepcopy(view)
        self.calls: list[list[str]] = []
        self.fail = fail

    def capture(self) -> dict[str, Any]:
        return deepcopy(self.view)

    def mutate(self, argv: list[str]) -> dict[str, Any]:
        self.calls.append(argv)
        if self.fail:
            return {"exit_status": 1}
        kind, op, rid = argv[1] + "s", argv[2], argv[-1]
        rows = self.view["inspect"][kind]
        row = next(r for r in rows if r["Name" if kind == "volumes" else "Id"] == rid)
        if op == "stop":
            row["State"]["Running"] = False
            row["State"]["Status"] = "exited"
            for net in self.view["inspect"]["networks"]:
                net["Containers"].pop(rid, None)
        else:
            rows.remove(row)
            self.view["ids"][kind].remove(rid)
            if kind == "containers":
                for net in self.view["inspect"]["networks"]:
                    net["Containers"].pop(rid, None)
        return {"exit_status": 0}


def test_exact_sequence_and_cardinality(fixture: Any, tmp_path: Path) -> None:
    a, v = fixture
    fake = Fake(v)
    e = Engine(a, fake, tmp_path)
    result = e.run()
    assert result["counts"] == {
        "stop": 28,
        "container_rm": 29,
        "network_rm": 3,
        "volume_rm": 6,
    }
    assert fake.calls[0] == ["docker", "container", "rm", PROBE]
    assert [x[-1] for x in fake.calls[1:29]] == a.order
    assert all(x[2] == "stop" for x in fake.calls[1:29])
    assert all(x[2] == "rm" for x in fake.calls[29:])
    assert len(list((tmp_path / "mutation-intents").glob("*.json"))) == 66
    assert len(list((tmp_path / "mutation-receipts").glob("*.json"))) == 66
    assert all(not rows for rows in fake.view["inspect"].values())
    assert all(len(c) == (6 if c[2] == "stop" else 4) for c in fake.calls)


@pytest.mark.parametrize("kind", KINDS)
def test_already_absent(fixture: Any, kind: str) -> None:
    a, v = fixture
    row = v["inspect"][kind].pop(0)
    rid = row["Name" if kind == "volumes" else "Id"]
    assert a.validate(v)[kind][rid] == "ALREADY_ABSENT"


@pytest.mark.parametrize("kind", ("containers", "networks"))
def test_replacement(fixture: Any, kind: str) -> None:
    a, v = fixture
    v["inspect"][kind][0]["Id"] = "e" * 64
    v["inspect"][kind][0].get("Config", v["inspect"][kind][0])["Labels"] = {}
    with pytest.raises(Blocked, match="RESOURCE_REPLACED"):
        a.validate(v)


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_qualification(fixture: Any, kind: str) -> None:
    a, v = fixture
    extra = deepcopy(v["inspect"][kind][0])
    extra["Name"] = "unknown"
    extra["Id"] = "e" * 64
    v["inspect"][kind].append(extra)
    with pytest.raises(Blocked, match="UNKNOWN_QUALIFICATION_RESOURCE"):
        a.validate(v)


@pytest.mark.parametrize(
    "field,value",
    [("Image", "drift"), ("Name", "/drift"), ("HostConfig", {}), ("Config", {})],
)
def test_container_identity_drift(fixture: Any, field: str, value: Any) -> None:
    a, v = fixture
    if field == "Config":
        v["inspect"]["containers"][0]["Config"]["Labels"]["other"] = "drift"
    else:
        v["inspect"]["containers"][0][field] = value
    with pytest.raises(Blocked):
        a.validate(v)


@pytest.mark.parametrize(
    "field,value",
    [
        ("Source", "/unknown"),
        ("Destination", "/wrong"),
        ("Type", "bind"),
        ("RW", False),
    ],
)
def test_mount_drift(fixture: Any, field: str, value: Any) -> None:
    a, v = fixture
    v["inspect"]["containers"][0]["Mounts"][0][field] = value
    with pytest.raises(Blocked, match="RESOURCE_IDENTITY_DRIFT"):
        a.validate(v)


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("networks", "Driver", "overlay"),
        ("networks", "Internal", True),
        ("volumes", "Labels", {}),
        ("volumes", "Driver", "unknown"),
        ("volumes", "Options", {"x": "y"}),
    ],
)
def test_network_volume_drift(fixture: Any, kind: str, field: str, value: Any) -> None:
    a, v = fixture
    v["inspect"][kind][0][field] = value
    with pytest.raises(Blocked, match="RESOURCE_IDENTITY_DRIFT"):
        a.validate(v)


def test_binding_drift(fixture: Any) -> None:
    a, v = fixture
    c = v["inspect"]["containers"][0]
    next(iter(c["NetworkSettings"]["Networks"].values()))["NetworkID"] = "f" * 64
    with pytest.raises(Blocked, match="RESOURCE_IDENTITY_DRIFT"):
        a.validate(v)


@pytest.mark.parametrize("attachment", ("volume", "network"))
def test_unknown_attachment(fixture: Any, attachment: str) -> None:
    a, v = fixture
    c = deepcopy(v["inspect"]["containers"][0])
    c["Id"] = "e" * 64
    c["Name"] = "/unknown"
    c["Config"]["Labels"] = {}
    if attachment == "volume":
        c["NetworkSettings"]["Networks"] = {}
    else:
        c["Mounts"] = []
    v["inspect"]["containers"].append(c)
    with pytest.raises(Blocked, match="UNEXPECTED_"):
        a.validate(v)


def test_extra_endpoint(fixture: Any) -> None:
    a, v = fixture
    v["inspect"]["networks"][0]["Containers"]["e" * 64] = {}
    with pytest.raises(Blocked, match="UNEXPECTED_NETWORK_ENDPOINT"):
        a.validate(v)


@pytest.mark.parametrize(
    "drift", ("missing_inspect", "duplicate", "daemon", "id", "identity", "endpoint")
)
def test_capture_races(fixture: Any, drift: str) -> None:
    _, v = fixture
    b = deepcopy(v)
    if drift == "missing_inspect":
        b["inspect"]["containers"].pop()
    if drift == "duplicate":
        b["inspect"]["containers"].append(b["inspect"]["containers"][0])
    if drift == "daemon":
        b["binding"] = {"daemon": "two"}
    if drift == "id":
        b["ids"]["containers"].pop()
    if drift == "identity":
        b["inspect"]["containers"][0]["Image"] = "changed"
    if drift == "endpoint":
        b["inspect"]["networks"][0]["Containers"]["unknown"] = {}
    with pytest.raises(Blocked):
        validate_pair(v, b)


def test_lifecycle_variation_recorded(fixture: Any) -> None:
    a, v = fixture
    b = deepcopy(v)
    b["inspect"]["containers"][0]["State"] = {
        "Running": False,
        "Pid": 0,
        "FinishedAt": "later",
    }
    validate_pair(v, b)
    a.validate(b)


def test_command_derivation_no_observed_authority(fixture: Any) -> None:
    a, v = fixture
    row = command_values(
        {"command": None, "entrypoint": None},
        {"Config": {"Entrypoint": ["ep"], "Cmd": ["cmd"]}},
    )
    assert row["resolved_effective_command"] == ["cmd"]
    assert row["resolved_effective_entrypoint"] == ["ep"]
    v["inspect"]["containers"][0]["Config"]["Cmd"] = ["other"]
    with pytest.raises(Blocked, match="CLEANUP_EFFECTIVE_COMMAND_MISMATCH"):
        a.validate(v)
    assert a.commands[v["inspect"]["containers"][0]["Id"]][
        "resolved_effective_command"
    ] == ["default"]


def test_probe_oom_normalization_is_not_general(fixture: Any) -> None:
    a, v = fixture
    a.validate(v)
    v["inspect"]["containers"][-1]["HostConfig"]["OomKillDisable"] = False
    with pytest.raises(Blocked, match="RESOURCE_IDENTITY_DRIFT"):
        a.validate(v)


def test_failure_stops_later_mutations(fixture: Any, tmp_path: Path) -> None:
    a, v = fixture
    fake = Fake(v, True)
    e = Engine(a, fake, tmp_path)
    with pytest.raises(Blocked):
        e.run()
    assert len(fake.calls) == 1
    assert len(list((tmp_path / "mutation-intents").glob("*.json"))) == 1
    assert len(list((tmp_path / "mutation-receipts").glob("*.json"))) == 1
    with pytest.raises(FileExistsError):
        seal(tmp_path, "mutation-intents/000.json", {})


@pytest.mark.parametrize(
    "op", ["kill", "start", "restart", "prune", "down", "rm -f", "*"]
)
def test_forbidden_commands(fixture: Any, op: str) -> None:
    a, _ = fixture
    with pytest.raises(Blocked):
        a.argv("containers", PROBE, op)


@pytest.mark.parametrize(
    "target", ["*", PROBE[:12], PROBE + " " + PROBE, "--force", "unknown"]
)
def test_no_arbitrary_targets(fixture: Any, target: str) -> None:
    a, _ = fixture
    with pytest.raises(Blocked):
        a.argv("containers", target, "rm")


def test_stopped_no_stop_and_no_start(fixture: Any, tmp_path: Path) -> None:
    a, v = fixture
    for c in v["inspect"]["containers"]:
        c["State"]["Running"] = False
    for n in v["inspect"]["networks"]:
        n["Containers"] = {}
    fake = Fake(v)
    Engine(a, fake, tmp_path).run()
    assert all(c[2] == "rm" for c in fake.calls)


def test_no_remove_running(fixture: Any, tmp_path: Path) -> None:
    a, v = fixture
    e = Engine(a, Fake(v), tmp_path)
    e.baseline = v
    e.binding = v["binding"]
    with pytest.raises(Blocked, match="REMOVE_REQUIRES_STOPPED"):
        e.action("containers", a.order[0], "rm")
    assert not e.intents


def test_network_zero_endpoints_required(fixture: Any, tmp_path: Path) -> None:
    a, v = fixture
    e = Engine(a, Fake(v), tmp_path)
    e.baseline = v
    e.binding = v["binding"]
    with pytest.raises(Blocked, match="UNEXPECTED_NETWORK_ENDPOINT"):
        e.action("networks", a.network_order[2], "rm")


def test_volume_zero_attachment_required(fixture: Any, tmp_path: Path) -> None:
    a, v = fixture
    e = Engine(a, Fake(v), tmp_path)
    e.baseline = v
    e.binding = v["binding"]
    with pytest.raises(Blocked, match="UNEXPECTED_ATTACHMENT"):
        e.action("volumes", a.volume_order[0], "rm")


def test_none_only_exact_probe_endpoint(fixture: Any) -> None:
    a, v = fixture
    builtin = {
        "Id": "f" * 64,
        "Name": "none",
        "Labels": {},
        "Driver": "null",
        "Containers": {
            PROBE: {"EndpointID": "exact"},
            "unknown": {"EndpointID": "keep"},
        },
    }
    v["inspect"]["networks"].append(builtin)
    post = deepcopy(v)
    post["inspect"]["networks"][-1]["Containers"].pop(PROBE)
    assert nonowned(post, a, False) == nonowned(v, a, True)
    post["inspect"]["networks"][-1]["Containers"].pop("unknown")
    assert nonowned(post, a, False) != nonowned(v, a, True)


def test_receipt_chain_and_permissions(fixture: Any, tmp_path: Path) -> None:
    a, v = fixture
    Engine(a, Fake(v), tmp_path).run()
    previous = None
    from scripts.product.cleanup_v040_v3_retained.core import sha

    for p in sorted((tmp_path / "mutation-receipts").glob("*.json")):
        receipt = json.loads(p.read_bytes())
        assert receipt["previous_receipt_sha256"] == previous
        claim = receipt.pop("receipt_sha256")
        assert claim == digest(receipt)
        previous = sha(p.read_bytes())
        assert p.stat().st_mode & 0o777 == 0o600
