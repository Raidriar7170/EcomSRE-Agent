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
        "NetworkSettings": {"Networks": {}},
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
