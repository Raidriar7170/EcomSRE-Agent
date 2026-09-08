"""Boundary regressions for v4 semantic admission, independent of Docker."""

from copy import deepcopy
import itertools
import pytest
from scripts.product.preflight_v040_v4.common import Failure, LABEL, ROLE_LABEL
from scripts.product.preflight_v040_v4.identity import (
    effective_process,
    lifecycle,
    service_health,
)


@pytest.mark.parametrize(
    "entry,command,image_entry,image_command",
    itertools.product([None, [], ["override"]], repeat=4),
)
def test_process_matrix(entry, command, image_entry, image_command):
    actual = effective_process(
        {"entrypoint": entry, "command": command},
        {"Entrypoint": image_entry, "Cmd": image_command},
    )
    assert actual["resolved_effective_entrypoint"] == (
        image_entry if entry is None else entry
    )
    assert actual["resolved_effective_command"] == (
        command if command is not None else image_command if entry is None else []
    )


def test_string_process():
    actual = effective_process(
        {"command": 'echo "two words"'}, {"Entrypoint": ["/entry"]}
    )
    assert actual["resolved_effective_command"] == ["echo", "two words"]


def row(role="payment", identifier="a"):
    return {
        "Id": identifier,
        "Created": "now",
        "Image": "sha256:image",
        "Name": role,
        "Config": {
            "Labels": {
                "com.docker.compose.project": "sandbox",
                "com.docker.compose.service": role,
                LABEL: "attempt",
                ROLE_LABEL: role,
            }
        },
        "HostConfig": {"OomKillDisable": False, "Privileged": False},
        "Mounts": [],
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "RestartCount": 0,
    }


def health(records, births=None, readiness=None):
    return service_health(
        records,
        project="sandbox",
        attempt="attempt",
        births=births or {"payment": "a"},
        readiness=readiness or {},
    )


def test_exact_28_roles():
    roles = {f"role{i}": str(i) for i in range(28)}
    assert all(
        x["ready"]
        for x in health([row(k, v) for k, v in roles.items()], roles).values()
    )


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "MISSING"),
        ("extra", "UNEXPECTED"),
        ("duplicate", "DUPLICATE"),
        ("malformed", "MALFORMED"),
        ("replacement", "ID_DRIFT"),
    ],
)
def test_bad_roles(case, code):
    records = [row()]
    if case == "missing":
        records = []
    if case == "extra":
        records.append(row("probe"))
    if case == "duplicate":
        records.append(row())
    if case == "malformed":
        records[0]["State"] = None
    if case == "replacement":
        records[0]["Id"] = "b"
    with pytest.raises(Failure, match=code):
        health(records)


def test_unhealthy_and_no_declared_check():
    record = row()
    record["State"]["Health"]["Status"] = "unhealthy"
    assert not health([record])["payment"]["ready"]
    record["State"].pop("Health")
    assert not health([record])["payment"]["ready"]
    assert health([record], readiness={"payment": True})["payment"]["ready"]


def test_separate_probe_and_unrelated_container():
    probe = row("probe")
    probe["Config"]["Labels"].pop("com.docker.compose.project")
    assert health([row(), probe])["payment"]["ready"]


def test_oom_exception_is_narrow():
    before = row("kafka-volume-probe")
    after = deepcopy(before)
    after["HostConfig"]["OomKillDisable"] = None
    lifecycle(
        before, after, role="kafka-volume-probe", stage="running", oom_evidence=True
    )
    for role, evidence in [("payment", True), ("kafka-volume-probe", False)]:
        with pytest.raises(Failure):
            lifecycle(before, after, role=role, stage="running", oom_evidence=evidence)
    after["HostConfig"]["Privileged"] = True
    with pytest.raises(Failure):
        lifecycle(
            before, after, role="kafka-volume-probe", stage="running", oom_evidence=True
        )


@pytest.mark.parametrize("field", ["Id", "Image", "Created"])
def test_immutable_drift(field):
    before = row()
    after = deepcopy(before)
    after[field] = "different"
    with pytest.raises(Failure, match="IMMUTABLE"):
        lifecycle(before, after, role="payment", stage="running")
