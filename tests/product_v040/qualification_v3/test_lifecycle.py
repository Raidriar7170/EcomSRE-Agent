from copy import deepcopy

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040_v3.lifecycle import ProbeLifecycle, identity
from scripts.product.qualification_v040_v3.oom import parse_oom


def probe():
    return {
        "Id": "probe-id",
        "Name": "/probe",
        "Created": "created",
        "Image": "pinned",
        "Config": {"Labels": {"owned": "true"}, "User": "appuser"},
        "HostConfig": {
            "OomKillDisable": False,
            "OomScoreAdj": 0,
            "Memory": 0,
            "MemorySwap": 0,
            "NetworkMode": "none",
            "Privileged": False,
        },
        "Mounts": [],
        "NetworkSettings": {
            "Networks": {
                "none": {
                    "NetworkID": "",
                    "EndpointID": "",
                    "IPAddress": "",
                    "GlobalIPv6Address": "",
                    "MacAddress": "",
                }
            }
        },
        "RestartCount": 0,
        "State": {"Running": False, "Pid": 0, "StartedAt": "zero", "OOMKilled": False},
    }


def evidence(row, stage):
    observation = {
        "cgroup": "0::/",
        "cgroup_fs": "cgroup2",
        "memory_max": "max",
        "memory_oom_group": "0",
        "oom_score_adj": "0",
        "legacy_oom_control_present": False,
        "pid": 1,
        "start_time": 123,
        "uids": [1000] * 4,
        "gids": [1000] * 4,
        "groups": [1000],
        "cap_eff": 0,
        "cap_prm": 0,
        "no_new_privs": 1,
    }
    return {
        "stage": stage,
        "daemon": {"id": "daemon"},
        "identity_before": identity(row),
        "identity_after": identity(row),
        "first": observation,
        "second": deepcopy(observation),
    }


def started():
    lifecycle = ProbeLifecycle({"id": "daemon"})
    row = probe()
    lifecycle.admit(row, "AFTER_COPYUP_CREATE", None)
    row["State"].update(Running=True, Pid=44, StartedAt="start")
    row["HostConfig"]["OomKillDisable"] = None
    row["NetworkSettings"]["Networks"]["none"].update(
        NetworkID="none-id", EndpointID="endpoint"
    )
    return lifecycle, row


def test_only_predeclared_start_transition_with_fresh_policy_proof():
    lifecycle, row = started()
    before = deepcopy(row)
    fixed = lifecycle.admit(
        row, "AFTER_PROBE_START", evidence(row, "AFTER_PROBE_START")
    )
    assert row == before  # Raw evidence remains untouched.
    assert fixed["HostConfig"]["OomKillDisable"] is False
    assert lifecycle.events[0]["field"] == "HostConfig.OomKillDisable"
    lifecycle.admit(row, "BEFORE_SENTINEL", evidence(row, "BEFORE_SENTINEL"))
    p = evidence(row, "BEFORE_PROBE_STOP")
    lifecycle.admit(row, "BEFORE_PROBE_STOP", p)
    lifecycle.authorize_stop(row, p)
    row["State"].update(Running=False, Pid=0)
    row["NetworkSettings"]["Networks"]["none"]["EndpointID"] = ""
    lifecycle.admit(row, "AFTER_PROBE_STOP", None)
    assert lifecycle.last_proof == p and lifecycle.stopped


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "wrong_cid",
        "stale_stage",
        "wrong_daemon",
        "raced",
        "group_kill",
        "score",
        "limit",
        "legacy",
        "root_group",
        "recreated",
        "privileged",
        "additional_host_field",
        "restart",
        "true",
        "missing_field",
        "wrong_stage",
    ],
)
def test_transition_denials(case):
    lifecycle, row = started()
    stage = "AFTER_PROBE_START"
    p = evidence(row, stage)
    if case == "missing":
        p = None
    elif case == "wrong_cid":
        p["identity_after"]["container_id"] = "other"
    elif case == "stale_stage":
        p["stage"] = "yesterday"
    elif case == "wrong_daemon":
        p["daemon"] = {"id": "other"}
    elif case == "raced":
        p["second"]["start_time"] = 124
    elif case in {"group_kill", "score", "limit", "legacy", "root_group"}:
        key, value = {
            "group_kill": ("memory_oom_group", "1"),
            "score": ("oom_score_adj", "-1000"),
            "limit": ("memory_max", "500"),
            "legacy": ("legacy_oom_control_present", True),
            "root_group": ("groups", [0]),
        }[case]
        p["first"][key] = p["second"][key] = value
    elif case == "recreated":
        row["Created"] = "replacement"
    elif case == "privileged":
        row["HostConfig"]["Privileged"] = True
    elif case == "additional_host_field":
        row["HostConfig"]["UnknownPolicy"] = True
    elif case == "restart":
        row["RestartCount"] = 1
    elif case == "true":
        row["HostConfig"]["OomKillDisable"] = True
    elif case == "missing_field":
        del row["HostConfig"]["OomKillDisable"]
    else:
        stage = "BEFORE_SENTINEL"
        p["stage"] = stage
    with pytest.raises(QualificationBlocked):
        lifecycle.admit(row, stage, p)


def test_false_null_change_during_ordinary_running_stage_is_not_normalized():
    lifecycle, row = started()
    lifecycle.admit(row, "AFTER_PROBE_START", evidence(row, "AFTER_PROBE_START"))
    row["HostConfig"]["OomKillDisable"] = False
    with pytest.raises(QualificationBlocked, match="OOM_UNAUTHORIZED_TRANSITION"):
        lifecycle.admit(row, "BEFORE_SENTINEL", evidence(row, "BEFORE_SENTINEL"))


def test_stop_without_intent_and_restart_are_denied():
    lifecycle, row = started()
    lifecycle.admit(row, "AFTER_PROBE_START", evidence(row, "AFTER_PROBE_START"))
    row["State"].update(Running=False, Pid=0)
    row["NetworkSettings"]["Networks"]["none"]["EndpointID"] = ""
    with pytest.raises(QualificationBlocked, match="PROBE_STOP_INTENT_MISSING"):
        lifecycle.admit(row, "AFTER_PROBE_STOP", None)


def encoded_oom():
    fields = ["OOM", "0::/", "cgroup2", "max", "0", "0", "ABSENT"]
    for pid in (1, 2):
        fields += [
            "IDENTITY",
            str(pid),
            "123",
            "1000 1000 1000 1000",
            "1000 1000 1000 1000",
            "1000",
            "0",
            "0",
            "1",
        ]
    return "\0".join([*fields, "COMPLETE", ""]) * 2


def test_oom_protocol_two_complete_identity_bound_reads():
    result = parse_oom(encoded_oom())
    assert result["first"] == result["second"]
    assert result["first"]["observer"]["pid"] == 2


@pytest.mark.parametrize("case", ["truncated", "extra", "root", "race"])
def test_oom_protocol_incomplete_or_changed_reads_denied(case):
    raw = encoded_oom()
    if case == "truncated":
        raw = raw[:-4]
    elif case == "extra":
        raw += "extra"
    elif case == "root":
        raw = raw.replace("\x001000\x00", "\x000\x00", 1)
    else:
        raw = raw.replace("\x00123\x00", "\x00124\x00", 1)
    with pytest.raises(QualificationBlocked):
        parse_oom(raw)


def test_endpoint_replacement_preserving_all_other_fields_is_denied():
    lifecycle, row = started()
    lifecycle.admit(row, "AFTER_PROBE_START", evidence(row, "AFTER_PROBE_START"))
    row["NetworkSettings"]["Networks"]["none"]["EndpointID"] = "replacement"
    with pytest.raises(QualificationBlocked, match="PROBE_ENDPOINT_IDENTITY_DRIFT"):
        lifecycle.admit(row, "BEFORE_SENTINEL", evidence(row, "BEFORE_SENTINEL"))


def test_driver_comparison_and_capture_preserve_raw_inspect(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from scripts.product.qualification_v040_v3 import driver as module
    from scripts.product.qualification_v040_v3.lifecycle import raw_fingerprint

    lifecycle, row = started()
    original = deepcopy(row)
    driver = object.__new__(module.V3Driver)
    driver.probe_lifecycle = lifecycle
    driver.probe_capture_count = 0
    driver.active_stage = "AFTER_PROBE_START"
    driver.journal = SimpleNamespace(
        plan={
            "roles": {
                module.PROBE_ROLE: {
                    "name": "probe",
                    "birth_stage": "AFTER_COPYUP_CREATE",
                }
            },
            "daemon": {"id": "daemon"},
        },
        bound={module.PROBE_ROLE: lifecycle.birth},
    )
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        if args[0] == "info":
            return "2"
        if args[0] == "exec":
            return encoded_oom()
        assert args == ("container", "inspect", "probe-id")
        return json.dumps([original])

    driver.runtime = SimpleNamespace(
        private=tmp_path,
        qualification="fixture",
        docker=docker,
        boundary=lambda: {"id": "daemon"},
    )
    # This regression isolates capture/comparison; role validation has separate fail-closed tests.
    monkeypatch.setattr(module, "validate_role", lambda *args: None)
    (tmp_path / "host").mkdir(mode=0o700)
    driver.capture_probe_policy({"containers": [row]})
    assert row == original
    assert lifecycle.last_raw["HostConfig"]["OomKillDisable"] is None
    assert driver.same(lifecycle.birth, "containers", row)
    assert row == original
    for suffix in ("raw-before", "raw-after"):
        assert (
            json.loads((tmp_path / f"host/probe-policy-001.{suffix}.json").read_text())
            == original
        )
    detached = raw_fingerprint(row)
    detached["Config"]["Labels"]["owned"] = "changed"
    detached["HostConfig"]["OomKillDisable"] = False
    assert row == original
    assert len(commands) == 3
