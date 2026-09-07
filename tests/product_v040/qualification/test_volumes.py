import io
import tarfile

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040.volumes import (
    KAFKA_PATHS,
    MAX_ARCHIVE_BYTES,
    parse_copyup,
    runtime_user,
    validate_access,
    validate_sentinel,
    verify_process_identity,
)


def archive(
    path: str,
    *,
    uid: int = 1000,
    gid: int = 1000,
    mode: int = 0o755,
    extra: str = "file",
) -> bytes:
    stream = io.BytesIO()
    root = path.rsplit("/", 1)[-1]
    with tarfile.open(fileobj=stream, mode="w") as result:
        entry = tarfile.TarInfo(root)
        entry.type = tarfile.DIRTYPE
        entry.uid, entry.gid, entry.mode = uid, gid, mode
        result.addfile(entry)
        if extra:
            child = tarfile.TarInfo(root + "/seed")
            child.uid, child.gid, child.mode = uid, gid, 0o644
            if extra == "link":
                child.type = tarfile.SYMTYPE
                child.linkname = "/outside"
                result.addfile(child)
            elif extra == "escape":
                child.name = root + "/../escape"
                result.addfile(child)
            else:
                child.size = 4
                result.addfile(child, io.BytesIO(b"seed"))
    return stream.getvalue()


def test_all_copyup_content_owners_modes_and_actual_user_are_measured() -> None:
    values = [parse_copyup(archive(path), path) for path in KAFKA_PATHS]
    validate_access(values, 1000, 1000)
    for row in values:
        assert row["entries"]["."]["uid"] == 1000
        assert row["entries"]["."]["gid"] == 1000
        assert row["entries"]["."]["mode"] == 0o755
        assert len(row["entries"]["seed"]["sha256"]) == 64
    uid, gid = runtime_user(b"appuser:x:1000:1000::/home/appuser:/bin/sh\n", "appuser")
    result = validate_sentinel(
        "1000\n1000\n" + "".join("PASS " + p + "\n" for p in KAFKA_PATHS), uid, gid
    )
    assert result["sentinel_absent"] is True
    verify_process_identity(
        "Uid:\t1000\t1000\t1000\t1000\nGid:\t1000\t1000\t1000\t1000\n", uid, gid
    )


@pytest.mark.parametrize(
    "field,value", [("uid", 0), ("gid", 0), ("mode", 0o555), ("mode", 0o777)]
)
def test_copyup_access_mismatch_blocks_without_repair(field: str, value: int) -> None:
    values = [parse_copyup(archive(path), path) for path in KAFKA_PATHS]
    values[0]["entries"]["."][field] = value
    with pytest.raises(QualificationBlocked):
        validate_access(values, 1000, 1000)
    assert values[0]["entries"]["."][field] == value


@pytest.mark.parametrize("extra", ["link", "escape"])
def test_copyup_unsafe_entries_fail_closed(extra: str) -> None:
    with pytest.raises(QualificationBlocked, match="COPYUP_UNSAFE_ENTRY"):
        parse_copyup(archive(KAFKA_PATHS[0], extra=extra), KAFKA_PATHS[0])


def test_copyup_archive_is_bounded_before_parsing() -> None:
    with pytest.raises(QualificationBlocked, match="COPYUP_MEASUREMENT_BOUNDS"):
        parse_copyup(b"x" * (MAX_ARCHIVE_BYTES + 1), KAFKA_PATHS[0])


@pytest.mark.parametrize(
    "output",
    ["", "1000\n1000\n", "0\n0\n" + "".join("PASS " + p + "\n" for p in KAFKA_PATHS)],
)
def test_sentinel_partial_or_wrong_user_never_passes(output: str) -> None:
    with pytest.raises(QualificationBlocked, match="KAFKA_WRITEABILITY_MISMATCH"):
        validate_sentinel(output, 1000, 1000)


@pytest.mark.parametrize(
    "status",
    [
        "Uid: 0 0 0 0\nGid: 1000 1000 1000 1000\n",
        "Uid: 1000 1000 1000 1000\nGid: 0 0 0 0\n",
        "",
    ],
)
def test_probe_user_must_match_actual_kafka_process(status: str) -> None:
    with pytest.raises(QualificationBlocked, match="KAFKA_PROCESS_IDENTITY_MISMATCH"):
        verify_process_identity(status, 1000, 1000)


@pytest.mark.parametrize(
    "passwd",
    [
        b"",
        b"appuser:x:0:0::/:/bin/sh\n",
        b"appuser:x:1000:1000::/:/bin/sh\nappuser:x:1001:1001::/:/bin/sh\n",
    ],
)
def test_numeric_user_resolution_must_be_unambiguous(passwd: bytes) -> None:
    with pytest.raises(QualificationBlocked):
        runtime_user(passwd, "appuser")


def lifecycle_measurements():
    return [
        {
            "path": p,
            "entries": {
                ".": {
                    "kind": "directory",
                    "uid": 1000,
                    "gid": 1000,
                    "mode": 0o755,
                    "size": 0,
                }
            },
        }
        for p in KAFKA_PATHS
    ]


@pytest.mark.parametrize(
    "stage",
    [
        "BEFORE_WARMUP",
        "AFTER_HEALTHY_CONTROL",
        "BEFORE_NO_INCIDENT",
        "AFTER_NETWORK_DENIAL",
    ],
)
@pytest.mark.parametrize("drift", ["mode", "uid", "secret", "config"])
def test_later_checkpoint_seed_drift_latches(tmp_path, monkeypatch, stage, drift):
    from copy import deepcopy
    from types import SimpleNamespace
    from scripts.product.qualification_v040.driver import Driver
    from scripts.product.qualification_v040.guard import (
        QualificationJournal,
        ZERO_COUNTS,
    )

    initial = lifecycle_measurements()
    measurements = deepcopy(initial)
    if drift in {"mode", "uid"}:
        measurements[2]["entries"]["."][drift] = 0
    else:
        measurements[0 if drift == "secret" else 1]["entries"]["unexpected"] = {
            "kind": "file",
            "uid": 1000,
            "gid": 1000,
            "mode": 0o644,
            "size": 1,
            "sha256": "x",
        }
    names = [
        "INITIAL",
        "AFTER_COPYUP_MEASUREMENT",
        "AFTER_SANDBOX_START",
        "AFTER_KAFKA_IDENTITY",
        stage,
        "BEFORE_CLEANUP",
        "AFTER_OWNED_STOP",
    ]
    plan = {
        "formal_authority": False,
        "counters": ZERO_COUNTS,
        "roles": {},
        "stages": [{"name": n, "present_roles": []} for n in names],
        "seed_binding_policy": "CREATE_ONCE",
    }
    journal = QualificationJournal(tmp_path / "journal", plan)
    journal.seed_binding = {"initial": initial, "uid": 1000, "gid": 1000}
    from scripts.product.v040_runtime import seal_private

    seal_private(journal.root / "seed-binding.json", journal.seed_binding)
    journal.generated_config = initial[1]["entries"]
    driver = Driver(SimpleNamespace(private=tmp_path, qualification="q"))
    driver.journal = journal
    saved = {
        "status": "MEASURED",
        **journal.seed_binding,
        "before": measurements,
        "after": measurements,
    }

    # Exercise Driver's actual fail-closed collection-to-journal path.
    def snapshot():
        journal.validate_seeds(stage, saved)
        return {}

    monkeypatch.setattr(driver, "snapshot", snapshot)
    with pytest.raises(QualificationBlocked):
        driver.checkpoint(stage)
    assert (journal.root / "first-divergence.json").exists()
