import pytest
from scripts.product.preflight_v040_v4.cleanup_oom import parse_cleanup_oom
from scripts.product.preflight_v040_v4.oom import parse_oom
from scripts.product.preflight_v040_v4.common import Failure


def capture(observer_uid="1000", observer_cap="0", target_uid="999", target_nnp="0"):
    fields = [
        "OOM",
        "0::/",
        "cgroup2",
        "536870912",
        "0",
        "0",
        "ABSENT",
        "IDENTITY",
        "1",
        "234",
        " ".join([target_uid] * 4),
        "999 " * 4,
        "999",
        "0",
        "0",
        target_nnp,
        "IDENTITY",
        "42",
        "567",
        " ".join([observer_uid] * 4),
        "1000 " * 4,
        "1000",
        observer_cap,
        "0",
        "0",
        "COMPLETE",
        "",
    ]
    part = "\0".join(fields)
    return part + part


def test_cleanup_observes_actual_target_credentials_without_weakening_probe():
    raw = capture()
    parsed = parse_cleanup_oom(raw)
    assert parsed["first"]["uids"] == [999] * 4
    assert parsed["first"]["no_new_privs"] == 0
    with pytest.raises(Failure):
        parse_oom(raw)


@pytest.mark.parametrize(
    "raw",
    [
        capture(observer_uid="0"),
        capture(observer_cap="1"),
        capture(target_uid="-1"),
        capture(target_nnp="2"),
        capture()[:-1],
        capture().replace("567", "568", 1),
    ],
)
def test_cleanup_rejects_privileged_observer_partial_and_raced_evidence(raw):
    with pytest.raises(Failure):
        parse_cleanup_oom(raw)
