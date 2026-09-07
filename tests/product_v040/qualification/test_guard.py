from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.product.qualification_v040.guard import (
    QUAL_LABEL,
    ZERO_COUNTS,
    QualificationBlocked,
    QualificationJournal,
    sha,
)
from scripts.product.qualification_v040.driver import Driver
from scripts.product.qualification_v040.runtime import QualificationRuntime

QUAL = "test-qualification"
IID = "sha256:" + "1" * 64
CID = "2" * 64
NID = "3" * 64
BID = "4" * 64


def envelope(owned: bool = False, running: bool = True) -> dict[str, Any]:
    network = {
        "Id": NID,
        "Name": "owned-net",
        "Labels": {QUAL_LABEL: QUAL},
        "Driver": "bridge",
        "Internal": True,
        "Containers": {CID: {}} if owned and running else {},
    }
    volume = {
        "Name": "owned-volume",
        "Labels": {QUAL_LABEL: QUAL},
        "Driver": "local",
        "Options": None,
        "Mountpoint": "/docker/volumes/owned-volume/_data",
    }
    mount = {
        "Type": "volume",
        "Name": "owned-volume",
        "Driver": "local",
        "Source": volume["Mountpoint"],
        "Destination": "/seed",
        "RW": True,
        "Mode": "z",
    }
    container = {
        "Id": CID,
        "Name": "/owned-container",
        "Image": IID,
        "Config": {
            "Image": IID,
            "Labels": {QUAL_LABEL: QUAL},
            "User": "1000:1000",
            "Entrypoint": None,
            "Cmd": ["app"],
            "Env": [],
        },
        "HostConfig": {
            "NetworkMode": "owned-net",
            "ReadonlyRootfs": True,
            "PortBindings": {},
            "Tmpfs": {},
            "Mounts": [{"Target": "/seed", "VolumeOptions": {"NoCopy": False}}],
        },
        "Mounts": [mount],
        "NetworkSettings": {"Networks": {"owned-net": {"NetworkID": NID}}},
        "State": {"Running": running},
        "RestartCount": 0,
    }
    rows: dict[str, list[dict[str, Any]]] = {
        "containers": [container] if owned else [],
        "networks": [
            {
                "Id": BID,
                "Name": "bridge",
                "Labels": {},
                "Driver": "bridge",
                "Internal": False,
                "Containers": {},
            }
        ]
        + ([network] if owned else []),
        "volumes": [volume] if owned else [],
        "images": [{"Id": IID, "Config": {}}],
    }
    ids = {
        k: sorted(r["Name"] if k == "volumes" else r["Id"] for r in values)
        for k, values in rows.items()
    }
    return {
        "daemon_before": {"id": "daemon"},
        "daemon_after": {"id": "daemon"},
        "ids_before": deepcopy(ids),
        "ids_after": deepcopy(ids),
        "inspect": rows,
        "inspect_after": deepcopy(rows),
        "seed_properties": {"status": "NOT_BOUND"},
        "mount_contents": {CID + ":/seed": {"sha256": "seed"}} if owned else {},
        "mount_contents_after": {CID + ":/seed": {"sha256": "seed"}} if owned else {},
        "platform_images_after": {
            "image": {
                "Id": IID,
                "Architecture": "arm64",
                "Config": {"Volumes": {"/seed": {}}},
            }
        },
        "platform_images": {
            "image": {
                "Id": IID,
                "Architecture": "arm64",
                "Config": {"Volumes": {"/seed": {}}},
            }
        },
    }


def reindex(value: dict[str, Any]) -> None:
    ids = {
        k: sorted(r["Name"] if k == "volumes" else r["Id"] for r in values)
        for k, values in value["inspect"].items()
    }
    value["mount_contents_after"] = deepcopy(value["mount_contents"])
    value["ids_before"] = deepcopy(ids)
    value["ids_after"] = deepcopy(ids)


def plan() -> dict[str, Any]:
    initial = envelope()
    roles = {
        "c": {
            "kind": "containers",
            "name": "owned-container",
            "labels": {QUAL_LABEL: QUAL},
            "image_id": IID,
            "platform_digest": IID,
            "image_reference": IID,
            "user": "1000:1000",
            "entrypoint": None,
            "command": ["app"],
            "environment": [],
            "read_only": True,
            "ports": {},
            "tmpfs": {},
            "networks": ["owned-net"],
            "mounts": {
                "/seed": {
                    "type": "volume",
                    "source": "owned-volume",
                    "rw": True,
                    "options": {"nocopy": False},
                }
            },
            "birth_stage": "AFTER_CREATE",
        },
        "n": {
            "kind": "networks",
            "name": "owned-net",
            "labels": {QUAL_LABEL: QUAL},
            "internal": True,
            "birth_stage": "AFTER_CREATE",
        },
        "v": {
            "kind": "volumes",
            "name": "owned-volume",
            "labels": {QUAL_LABEL: QUAL},
            "birth_stage": "AFTER_CREATE",
        },
    }
    return {
        "qualification_id": QUAL,
        "formal_authority": False,
        "counters": deepcopy(ZERO_COUNTS),
        "daemon": initial["daemon_before"],
        "platform_images": initial["platform_images"],
        "nonowned": {
            k: {r["Name"] if k == "volumes" else r["Id"]: r for r in rows}
            for k, rows in initial["inspect"].items()
        },
        "roles": roles,
        "bind_commitments": {},
        "stages": [
            {"name": n, "present_roles": r}
            for n, r in [
                ("INITIAL", []),
                ("BEFORE_CREATE", []),
                ("AFTER_CREATE", ["c", "n", "v"]),
                ("BEFORE_RUN", ["c", "n", "v"]),
                ("AFTER_RUN", ["c", "n", "v"]),
                ("BEFORE_OWNED_STOP", ["c", "n", "v"]),
                ("AFTER_OWNED_STOP", ["c", "n", "v"]),
                ("BEFORE_REMOVE", ["c", "n", "v"]),
                ("AFTER_REMOVE", []),
                ("POST_CLEANUP_READBACK", []),
            ]
        ],
    }


def started(tmp_path: Path) -> QualificationJournal:
    journal = QualificationJournal(tmp_path / "journal", plan())
    journal.observe("INITIAL", envelope())
    journal.observe("BEFORE_CREATE", envelope())
    journal.observe("AFTER_CREATE", envelope(True))
    return journal


def test_complete_declared_lifecycle(tmp_path: Path) -> None:
    j = started(tmp_path)
    for stage in ("BEFORE_RUN", "AFTER_RUN", "BEFORE_OWNED_STOP"):
        j.observe(stage, envelope(True))
    for stage in ("AFTER_OWNED_STOP", "BEFORE_REMOVE"):
        j.observe(stage, envelope(True, False))
    j.observe("AFTER_REMOVE", envelope())
    j.observe("POST_CLEANUP_READBACK", envelope())
    assert j.next_stage == len(j.plan["stages"])
    assert not (j.root / "first-divergence.json").exists()


def drift(value: dict[str, Any], case: str) -> None:
    rows = value["inspect"]
    if case == "builtin_network_replaced":
        rows["networks"][0]["Id"] = "9" * 64
    elif case == "owned_network_replaced":
        rows["networks"][1]["Id"] = "9" * 64
        rows["containers"][0]["NetworkSettings"]["Networks"]["owned-net"][
            "NetworkID"
        ] = "9" * 64
    elif case == "container_replaced":
        rows["containers"][0]["Id"] = "9" * 64
        rows["networks"][1]["Containers"] = {"9" * 64: {}}
        value["mount_contents"] = {"9" * 64 + ":/seed": {"sha256": "seed"}}
    elif case == "volume_source":
        rows["volumes"][0]["Mountpoint"] += "-replacement"
    elif case == "mount_source":
        rows["containers"][0]["Mounts"][0]["Name"] = "other"
    elif case == "mount_access":
        rows["containers"][0]["Mounts"][0]["RW"] = False
    elif case == "mount_copyup":
        rows["containers"][0]["HostConfig"]["Mounts"][0]["VolumeOptions"]["NoCopy"] = (
            True
        )
    elif case == "labels":
        rows["containers"][0]["Config"]["Labels"][QUAL_LABEL] = "other"
    elif case == "extra_owner_label":
        rows["containers"][0]["Config"]["Labels"]["unknown.owner"] = "foreign"
    elif case == "runtime_user":
        rows["containers"][0]["Config"]["User"] = "root"
    elif case == "image":
        rows["containers"][0]["Image"] = "sha256:" + "9" * 64
    elif case == "platform":
        value["platform_images"]["image"]["Architecture"] = "amd64"
    elif case == "daemon":
        value["daemon_before"] = value["daemon_after"] = {"id": "other"}
    elif case == "unknown_volume":
        rows["volumes"].append({"Name": "foreign", "Labels": {}})
    elif case == "unknown_network":
        rows["networks"].append({"Id": "8" * 64, "Name": "foreign", "Labels": {}})
    elif case == "unknown_container":
        row = deepcopy(rows["containers"][0])
        row["Id"] = "8" * 64
        row["Name"] = "/foreign"
        row["Config"]["Labels"] = {}
        row["Mounts"] = []
        rows["containers"].append(row)
    elif case == "unknown_qualified_volume":
        rows["volumes"].append({"Name": "unbound-owned", "Labels": {QUAL_LABEL: QUAL}})
    elif case == "restart":
        rows["containers"][0]["RestartCount"] = 1
    elif case == "endpoint":
        rows["networks"][1]["Containers"]["8" * 64] = {}
    elif case == "environment":
        rows["containers"][0]["Config"]["Env"].append("ECOMSRE_REMEDIATION_ENABLED=1")
    else:
        raise AssertionError(case)
    reindex(value)
    value["inspect_after"] = deepcopy(value["inspect"])
    value["platform_images_after"] = deepcopy(value["platform_images"])


@pytest.mark.parametrize(
    "case",
    [
        "builtin_network_replaced",
        "owned_network_replaced",
        "container_replaced",
        "volume_source",
        "mount_source",
        "mount_access",
        "mount_copyup",
        "labels",
        "extra_owner_label",
        "runtime_user",
        "image",
        "platform",
        "daemon",
        "unknown_volume",
        "unknown_network",
        "unknown_container",
        "unknown_qualified_volume",
        "restart",
        "endpoint",
        "environment",
    ],
)
def test_drift_latches_before_any_downstream_authority(
    tmp_path: Path, case: str
) -> None:
    j = started(tmp_path)
    value = envelope(True)
    drift(value, case)
    calls = []
    with pytest.raises(QualificationBlocked):
        j.observe("BEFORE_RUN", value)
        calls.extend(["fault", "authorization", "remediation"])
    assert calls == []
    first = (j.root / "first-divergence.json").read_bytes()
    assert json.loads(first)["prohibited_action_budget"] == ZERO_COUNTS
    with pytest.raises(QualificationBlocked, match="FIRST_DIVERGENCE_LATCHED"):
        j.observe("BEFORE_RUN", envelope(True))
    assert (j.root / "first-divergence.json").read_bytes() == first
    with pytest.raises(FileExistsError):
        QualificationJournal(j.root, plan())
    assert (j.root / "first-divergence.json").read_bytes() == first


@pytest.mark.parametrize(
    "case",
    [
        "missing_kind",
        "missing_row",
        "duplicate_id",
        "missing_mount",
        "raced_ids",
        "raced_daemon",
    ],
)
def test_incomplete_and_raced_capture_is_durable(tmp_path: Path, case: str) -> None:
    j = started(tmp_path)
    value = envelope(True)
    if case == "missing_kind":
        del value["inspect"]["volumes"]
    elif case == "missing_row":
        value["inspect"]["volumes"] = []
    elif case == "duplicate_id":
        value["ids_before"]["containers"].append(CID)
    elif case == "missing_mount":
        value["mount_contents"] = {}
    elif case == "raced_ids":
        value["ids_after"]["containers"] = []
    elif case == "raced_daemon":
        value["daemon_after"] = {"id": "other"}
    with pytest.raises(QualificationBlocked):
        j.observe("BEFORE_RUN", value)
    assert (j.root / "first-divergence.json").is_file()


@pytest.mark.parametrize(
    "action",
    ["fault", "approval", "authorization", "write-intent", "executor", "formal-freeze"],
)
def test_formal_authority_is_always_denied(tmp_path: Path, action: str) -> None:
    j = started(tmp_path)
    with pytest.raises(QualificationBlocked, match="FORMAL_AUTHORITY_FORBIDDEN"):
        j.protect(action)
    assert (
        json.loads((j.root / "first-divergence.json").read_bytes())["formal_authority"]
        is False
    )


def test_changed_plan_does_not_rebind_after_anomaly(tmp_path: Path) -> None:
    j = started(tmp_path)
    tampered = deepcopy(j.plan)
    tampered["daemon"] = {"id": "other"}
    (j.root / "plan.json").write_text(json.dumps(tampered))
    with pytest.raises(QualificationBlocked, match="PLAN_DRIFT"):
        j.observe("BEFORE_RUN", envelope(True))


def test_driver_stops_before_owned_mutation_when_inventory_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    j = QualificationJournal(tmp_path / "journal", plan())
    j.observe("INITIAL", envelope())
    driver = object.__new__(Driver)
    driver.journal = j
    driver.attempted_births = set()
    bad = envelope()
    bad["inspect"]["networks"][0]["Id"] = "9" * 64
    reindex(bad)
    monkeypatch.setattr(driver, "snapshot", lambda: bad)
    called = []
    with pytest.raises(QualificationBlocked):
        driver.step("CREATE", lambda: called.append("mutation"))
    assert called == []


@pytest.mark.parametrize(
    "method", ["enable", "initialize", "build", "start_bootstrap", "cleanup"]
)
def test_old_runtime_mutators_cannot_bypass_qualification(method: str) -> None:
    runtime = object.__new__(QualificationRuntime)
    with pytest.raises(QualificationBlocked):
        getattr(runtime, method)()


def test_stage_hash_chain_covers_raw_capture(tmp_path: Path) -> None:
    j = started(tmp_path)
    records = sorted(j.root.glob("*-accepted.json"))
    previous = j.plan_sha
    for path in records:
        row = json.loads(path.read_bytes())
        assert row["previous_sha256"] == previous
        saved = json.loads(
            path.with_name(path.name.replace("-accepted", "-capture")).read_bytes()
        )
        assert sha(saved) == row["capture_sha256"]
        previous = sha(row)
    assert previous == j.previous


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("volumes", "CreatedAt", "replacement-time"),
        ("networks", "Labels", {QUAL_LABEL: "other"}),
        ("containers", "Mounts", []),
    ],
)
def test_same_id_inspect_race_blocks_driver_before_operation(
    tmp_path, monkeypatch, kind, field, value
):
    from types import SimpleNamespace

    driver = Driver(SimpleNamespace(private=tmp_path, qualification=QUAL))
    driver.journal = started(tmp_path)
    saved = envelope(True)
    saved["inspect_after"][kind][-1][field] = value
    monkeypatch.setattr(driver, "snapshot", lambda: saved)
    operations = []
    with pytest.raises(QualificationBlocked, match="RACED_INSPECTION"):
        driver.step("RUN", lambda: operations.append("mutation"))
    assert operations == []
    assert (
        json.loads((driver.journal.root / "first-divergence.json").read_text())["stage"]
        == "BEFORE_RUN"
    )


def test_cleanup_same_named_replacement_never_deleted(tmp_path, monkeypatch):
    from types import SimpleNamespace

    (tmp_path / "host").mkdir(mode=0o700)
    operations = []
    driver = Driver(
        SimpleNamespace(
            private=tmp_path,
            qualification=QUAL,
            docker=lambda *a, **k: operations.append(a),
        )
    )
    driver.journal = started(tmp_path)
    driver.attempted_births = {"c", "n", "v"}
    with pytest.raises(QualificationBlocked):
        driver.journal.block("ORIGINAL_DRIFT", "BEFORE_RUN")
    original = (driver.journal.root / "first-divergence.json").read_bytes()
    saved = envelope(True)
    saved["inspect"]["containers"] = []
    saved["inspect"]["networks"] = saved["inspect"]["networks"][:1]
    saved["mount_contents"] = {}
    saved["mount_contents_after"] = {}
    saved["inspect"]["volumes"][0]["CreatedAt"] = "replacement-time"
    reindex(saved)
    saved["inspect_after"] = deepcopy(saved["inspect"])
    monkeypatch.setattr(driver, "snapshot", lambda: saved)
    with pytest.raises(
        QualificationBlocked, match="CLEANUP_RESOURCE_REPLACED"
    ) as caught:
        driver.cleanup()
    driver.latch_cleanup_failure(caught.value)
    assert not operations
    assert (driver.journal.root / "first-divergence.json").read_bytes() == original


@pytest.mark.parametrize(
    "failure", [QualificationBlocked("CLEANUP_DAEMON_DRIFT"), RuntimeError("io")]
)
def test_cleanup_failure_creates_latch(tmp_path, failure):
    from types import SimpleNamespace

    driver = Driver(SimpleNamespace(private=tmp_path, qualification=QUAL))
    driver.journal = started(tmp_path)
    driver.latch_cleanup_failure(failure)
    assert (
        json.loads((driver.journal.root / "first-divergence.json").read_text())["stage"]
        == "CLEANUP"
    )


@pytest.mark.parametrize("mode", ["nonzero", "incomplete", "corrupt", "missing"])
def test_counter_failure_is_persisted_before_raise(tmp_path, mode):
    import sqlite3
    from types import SimpleNamespace

    (tmp_path / "host").mkdir(mode=0o700)
    (tmp_path / "product").mkdir()
    path = tmp_path / "product/product.sqlite3"
    driver = Driver(SimpleNamespace(private=tmp_path, qualification=QUAL))
    driver.attempted_births.add("product/container/api")
    if mode == "corrupt":
        path.write_bytes(b"not sqlite")
    elif mode != "missing":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE remediation_attempts (id TEXT)")
            if mode == "nonzero":
                connection.execute(
                    "INSERT INTO remediation_attempts VALUES ('unexpected')"
                )
    with pytest.raises(QualificationBlocked, match="FORMAL_COUNTER_(NONZERO|UNKNOWN)"):
        driver.zero_database_counts()
    measured = json.loads((tmp_path / "host/formal-database-counts.json").read_text())
    assert measured == driver.result["counter_evidence"]
    assert measured["status"] == ("NONZERO" if mode == "nonzero" else "UNKNOWN")
    assert driver.result["counters"]["remediation_writes"] is None
    if mode == "nonzero":
        assert driver.result["counters"]["formal_campaign_executions"] == 1


def test_nonzero_cleanup_latch_records_measured_counters(tmp_path):
    from types import SimpleNamespace

    driver = Driver(SimpleNamespace(private=tmp_path, qualification=QUAL))
    driver.journal = started(tmp_path)
    driver.result["counters"]["formal_campaign_executions"] = 1
    driver.result["counter_evidence"] = {"status": "NONZERO"}
    driver.latch_cleanup_failure(QualificationBlocked("FORMAL_COUNTER_NONZERO"))
    evidence = json.loads((driver.journal.root / "first-divergence.json").read_text())
    assert evidence["observed_counters"]["formal_campaign_executions"] == 1
    assert evidence["counter_evidence"]["status"] == "NONZERO"
    assert "counters" not in evidence
