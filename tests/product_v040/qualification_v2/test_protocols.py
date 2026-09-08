import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040_v2.processes import (
    CENSUS_PROTOCOL,
    parse_census,
    approved_process_argv,
)
from scripts.product.qualification_v040_v2.data import parse_data_metadata
from scripts.product.qualification_v040_v2.freeze import consume_once
from scripts.product.qualification_v040_v2.sentinel import validate_sentinel_v2
from scripts.product.qualification_v040.volumes import KAFKA_PATHS
from test_policy import policy, process


def encoded_census():
    tokens = ["CENSUS", "2"]
    for pid, args in [
        (1, ["/bin/sleep", "2147483647"]),
        (2, ["/bin/bash", "-c", CENSUS_PROTOCOL]),
    ]:
        tokens += [
            "PROCESS",
            str(pid),
            "0",
            "123",
            "1000 1000 1000 1000",
            "1000 1000 1000 1000",
            "1000",
            "0000000000000000",
            "0000000000000000",
            "1",
            str(len(args)),
            *args,
        ]
    return "\0".join([*tokens, "COMPLETE", ""]) * 2


def test_complete_process_identity_and_self_are_preserved():
    c = parse_census(encoded_census())
    assert c["complete"] and c["self_pid"] == 2
    assert len(c["processes"]) == 2
    assert c["processes"][0]["groups"] == [1000]
    assert len(approved_process_argv(c, "probe")) == 2


@pytest.mark.parametrize(
    "case", ["truncated", "missing_groups", "duplicate_tail", "wrong_self"]
)
def test_incomplete_process_capture_fails_closed(case):
    raw = encoded_census()
    if case == "truncated":
        raw = raw[:-12]
    elif case == "missing_groups":
        raw = raw.replace("\x001000\x00", "\x00MISSING\x00", 1)
    elif case == "duplicate_tail":
        raw += "extra\0"
    else:
        raw = raw.replace(CENSUS_PROTOCOL, "unbound")
    with pytest.raises(QualificationBlocked):
        parse_census(raw)


def test_unknown_pid1_java_is_not_an_approved_kafka_writer():
    row = process()
    row["argv"] = ["/opt/java/openjdk/bin/java", "OtherClass"]
    with pytest.raises(QualificationBlocked, match="PROCESS_CENSUS_SELF_UNBOUND"):
        approved_process_argv({"processes": [row]}, "kafka")


def test_mutable_data_large_content_is_not_read_or_declared_immutable():
    raw = "\0".join(
        [
            "ENTRY",
            ".",
            "directory",
            "1000",
            "0",
            "775",
            "0",
            "5",
            "ENTRY",
            "log",
            "file",
            "1000",
            "1000",
            "644",
            "1073741824",
            "6",
            "COMPLETE",
            "",
        ]
    )
    result = parse_data_metadata(raw)
    assert result["entries"]["log"]["size"] == 1073741824
    assert "sha256" not in result["entries"]["log"]
    assert result["inodes"] == {".": 5, "log": 6}


@pytest.mark.parametrize("mode", ["partial", "owner", "groups"])
def test_sentinel_failure_is_not_pass(mode):
    tokens = [
        "IDENTITY",
        "3",
        "0",
        "1000 1000 1000 1000",
        "1000 1000 1000 1000",
        "1000",
        "0",
        "0",
        "1",
        "123",
    ]
    for path in KAFKA_PATHS:
        tokens += ["PASS", path, "1000:1000:600", "ABSENT"]
    raw = "\0".join([*tokens, "COMPLETE", ""])
    assert validate_sentinel_v2(raw, policy(), "a" * 32)["status"] == "PASS"
    if mode == "partial":
        raw = raw[:-12]
    elif mode == "owner":
        raw = raw.replace("1000:1000:600", "1000:0:600", 1)
    else:
        raw = raw.replace("\x001000\x00", "\x001000 0\x00", 1)
    with pytest.raises(QualificationBlocked):
        validate_sentinel_v2(raw, policy(), "a" * 32)


def test_one_shot_fuse_is_shared_across_worktrees(tmp_path, monkeypatch):
    from scripts.product.qualification_v040_v2 import freeze

    common = tmp_path / "git"
    common.mkdir()
    monkeypatch.setattr(
        freeze,
        "git",
        lambda repo, *args: str(common) if args[-1] == "--git-common-dir" else "head",
    )
    first = consume_once(tmp_path / "worktree-a", "b" * 64, "a" * 32)
    assert (
        first["no_fault_qualification_count"] == 1
        and first["formal_campaign_count"] == 0
    )
    with pytest.raises(
        QualificationBlocked, match="NOFAULT_ALLOWANCE_ALREADY_CONSUMED"
    ):
        consume_once(tmp_path / "worktree-b", "b" * 64, "c" * 32)


@pytest.mark.parametrize(
    "field,value",
    [("start_time", 124), ("uids", [0] * 4), ("groups", [0]), ("argv", ["evil"])],
)
def test_two_complete_passes_reject_identity_race(field, value):
    raw = encoded_census()
    marker = raw.index("COMPLETE\0") + len("COMPLETE\0")
    second = raw[marker:]
    replacements = {
        "start_time": ("\x00123\x00", "\x00124\x00"),
        "uids": ("1000 1000 1000 1000", "0 0 0 0"),
        "groups": ("\x001000\x00", "\x000\x00"),
        "argv": ("2147483647", "evil"),
    }
    old, new = replacements[field]
    with pytest.raises(QualificationBlocked, match="PROCESS_CENSUS_IDENTITY_RACE"):
        parse_census(raw[:marker] + second.replace(old, new, 1))


@pytest.mark.parametrize("index", [0, 1])
def test_duplicate_writer_role_rejected(index):
    from copy import deepcopy

    census = parse_census(encoded_census())
    extra = deepcopy(census["processes"][index])
    extra["pid"] = 3
    census["processes"].append(extra)
    with pytest.raises(QualificationBlocked, match="UNKNOWN_WRITER_PROCESS"):
        approved_process_argv(census, "probe")


@pytest.mark.parametrize("change", ["agent", "classpath", "duplicate_java"])
def test_kafka_argv_is_prebound_and_singleton(change):
    from copy import deepcopy

    census = parse_census(encoded_census())
    expected = policy()["kafka_startup_contract"]["argv"]
    census["processes"][0]["argv"] = deepcopy(expected)
    assert len(approved_process_argv(census, "kafka", expected)) == 2
    if change == "agent":
        census["processes"][0]["argv"].insert(1, "-javaagent:/tmp/evil.jar")
    elif change == "classpath":
        census["processes"][0]["argv"][expected.index("-cp") + 1] += ":/tmp/evil.jar"
    else:
        extra = deepcopy(census["processes"][0])
        extra["pid"] = 3
        census["processes"].append(extra)
    with pytest.raises(QualificationBlocked, match="UNKNOWN_WRITER_PROCESS"):
        approved_process_argv(census, "kafka", expected)
