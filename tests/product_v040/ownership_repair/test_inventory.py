"""Deterministic saved-observation tests. No Docker, HTTP, or runtime startup."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.product.v040_ownership import (
    COUNTERS,
    KINDS,
    OWNERS,
    STAGES,
    OwnershipBlocked,
    StageJournal,
    inventory_from_saved_inspects,
    sha,
    tree_commitment,
    validate_mount_provenance,
)


@pytest.fixture
def raw():
    labels = {
        "com.docker.compose.project": "ecomsre-live-sandbox-v1",
        **OWNERS["ecomsre-live-sandbox-v1"],
    }
    return {
        "daemon_before": {
            "context": "desktop-linux",
            "endpoint": "unix:///fixture.sock",
            "daemon_id": "fixture-daemon",
        },
        "daemon_after": {
            "context": "desktop-linux",
            "endpoint": "unix:///fixture.sock",
            "daemon_id": "fixture-daemon",
        },
        "ids_before": {
            "containers": ["c1"],
            "networks": ["n1", "bridge1"],
            "volumes": ["v1"],
            "images": ["sha256:" + "a" * 64],
        },
        "ids_after": {
            "containers": ["c1"],
            "networks": ["n1", "bridge1"],
            "volumes": ["v1"],
            "images": ["sha256:" + "a" * 64],
        },
        "inspect": {
            "containers": [
                {
                    "Id": "c1",
                    "Config": {"Labels": labels, "Env": ["TEST_SECRET=never-publish"]},
                    "Image": "sha256:" + "a" * 64,
                    "Mounts": [
                        {
                            "Type": "volume",
                            "Name": "v1",
                            "Source": "/fixture/v1",
                            "Destination": "/var/lib/kafka/data",
                            "RW": True,
                        }
                    ],
                    "NetworkSettings": {"Networks": {"default": {"NetworkID": "n1"}}},
                }
            ],
            "networks": [
                {"Id": "n1", "Labels": labels},
                {"Id": "bridge1", "Labels": {}, "Name": "bridge"},
            ],
            "volumes": [
                {
                    "Name": "v1",
                    "Labels": labels,
                    "Mountpoint": "/fixture/v1",
                    "Driver": "local",
                }
            ],
            "images": [
                {
                    "Id": "sha256:" + "a" * 64,
                    "Config": {"Labels": {}, "Volumes": {"/var/lib/kafka/data": {}}},
                    "RepoDigests": ["fixture@sha256:" + "a" * 64],
                    "RootFS": {"Layers": ["sha256:" + "b" * 64]},
                }
            ],
        },
        "mount_contents": {
            "c1:/var/lib/kafka/data": {
                "digest_kind": "TEST_FIXTURE",
                "sha256": "c" * 64,
            }
        },
    }


@pytest.fixture
def snapshot(raw):
    return inventory_from_saved_inspects(raw)


def plan(snapshot):
    return {stage: deepcopy(snapshot) for stage in STAGES}


def test_projection_keeps_identity_content_and_hidden_config_binding(raw):
    snapshot = inventory_from_saved_inspects(raw)
    assert "never-publish" not in json.dumps(snapshot)
    assert snapshot["containers"]["c1"]["inspect_sha256"] == sha(
        raw["inspect"]["containers"][0]
    )
    raw["inspect"]["containers"][0]["Config"]["Env"].append("CHANGED=yes")
    assert snapshot != inventory_from_saved_inspects(raw)


@pytest.mark.parametrize("kind", KINDS)
def test_partial_or_raced_enumeration_denied(raw, kind):
    raw["ids_after"][kind].append("unexpected")
    with pytest.raises(OwnershipBlocked):
        inventory_from_saved_inspects(raw)


@pytest.mark.parametrize("key", ["ids_before", "ids_after", "inspect"])
def test_unknown_resource_category_denied(raw, key):
    raw[key]["unknown"] = []
    with pytest.raises(OwnershipBlocked):
        inventory_from_saved_inspects(raw)


@pytest.mark.parametrize("kind", KINDS)
def test_missing_inspect_result_denied(raw, kind):
    raw["inspect"][kind].pop()
    with pytest.raises(OwnershipBlocked):
        inventory_from_saved_inspects(raw)


def test_unknown_network_reference_denied_even_in_expected_plan(raw):
    raw["inspect"]["containers"][0]["NetworkSettings"]["Networks"]["default"][
        "NetworkID"
    ] = "foreign"
    with pytest.raises(OwnershipBlocked, match="unknown attached network"):
        inventory_from_saved_inspects(raw)


def test_project_label_alone_does_not_grant_ownership(raw):
    del raw["inspect"]["networks"][0]["Labels"]["io.ecomsre.sandbox.id"]
    with pytest.raises(OwnershipBlocked, match="ownership unknown"):
        inventory_from_saved_inspects(raw)


def test_clean_full_stage_replay_has_no_authority(tmp_path, snapshot):
    journal = StageJournal(tmp_path / "journal", plan(snapshot))
    for stage in STAGES:
        journal.observe(stage, snapshot)
    assert len(list(journal.root.glob("stage-*.json"))) == len(STAGES)
    assert json.loads((journal.root / "plan.json").read_bytes())["counts"] == COUNTERS
    StageJournal(journal.root, plan(snapshot))


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    "drift", ["bridge", "unknown", "mount", "image", "labels", "content", "malformed"]
)
def test_every_stage_latches_first_divergence_and_never_reaches_protected_action(
    tmp_path, snapshot, stage, drift
):
    expected = plan(snapshot)
    journal = StageJournal(tmp_path / "journal", expected)
    for earlier in STAGES[: STAGES.index(stage)]:
        journal.observe(earlier, snapshot)
    actual = deepcopy(snapshot)
    if drift == "bridge":
        row = actual["networks"].pop("bridge1")
        row["identity"] = "bridge2"
        actual["networks"]["bridge2"] = row
    elif drift == "unknown":
        actual["volumes"]["anonymous"] = {
            "identity": "anonymous",
            "labels": {},
            "inspect_sha256": "a" * 64,
        }
    elif drift == "mount":
        actual["containers"]["c1"]["mounts"][0]["Source"] = "/wrong/source"
    elif drift == "image":
        actual["containers"]["c1"]["image_digest"] = "unknown-image"
    elif drift == "labels":
        actual["networks"]["n1"]["labels"] = {}
    elif drift == "content":
        actual["mount_contents"]["c1:/var/lib/kafka/data"]["sha256"] = "d" * 64
    else:
        actual["containers"]["c1"] = None
    calls = []
    with pytest.raises(OwnershipBlocked):
        journal.protected_boundary(stage, actual)
        calls.append("fault or authorization")
    assert not calls
    path = journal.root / "first-divergence.json"
    first = path.read_bytes()
    record = json.loads(first)
    assert record["stage"] == stage
    assert record["actual"] == actual and record["expected"] == snapshot
    assert record["counts"] == COUNTERS and record["authority"] == "NONE"
    with pytest.raises(OwnershipBlocked):
        journal.observe(stage, snapshot)
    with pytest.raises(OwnershipBlocked):
        StageJournal(journal.root, expected)
    assert path.read_bytes() == first


def test_plan_cannot_adopt_later_foreign_network(tmp_path, snapshot):
    expected = plan(snapshot)
    expected["AFTER_SANDBOX_START"]["networks"]["foreign"] = {
        "identity": "foreign",
        "labels": {},
        "inspect_sha256": "a" * 64,
    }
    with pytest.raises(OwnershipBlocked, match="non-owned"):
        StageJournal(tmp_path / "journal", expected)


def test_stage_skip_is_durable_failure(tmp_path, snapshot):
    journal = StageJournal(tmp_path / "journal", plan(snapshot))
    with pytest.raises(OwnershipBlocked):
        journal.observe("BEFORE_FAULT", snapshot)
    assert (journal.root / "first-divergence.json").exists()


@pytest.mark.parametrize(
    "error", ["missing", "partial", "malformed", "daemon", "volume_source"]
)
def test_collection_errors_also_leave_first_divergence(tmp_path, snapshot, raw, error):
    journal = StageJournal(tmp_path / "journal", plan(snapshot))
    if error == "missing":
        del raw["inspect"]
    elif error == "partial":
        raw["inspect"]["containers"] = []
    elif error == "malformed":
        raw["inspect"]["networks"] = [None]
    elif error == "daemon":
        raw["daemon_after"]["daemon_id"] = "replacement"
    else:
        raw["inspect"]["containers"][0]["Mounts"][0]["Source"] = "/other"
    with pytest.raises(OwnershipBlocked):
        journal.observe_saved("PREFLIGHT", raw)
    record = json.loads((journal.root / "first-divergence.json").read_bytes())
    assert record["actual"]["saved_input_sha256"] == sha(raw)
    assert "never-publish" not in json.dumps(record)


def test_journal_corruption_blocks_restart(tmp_path, snapshot):
    journal = StageJournal(tmp_path / "journal", plan(snapshot))
    journal.observe("PREFLIGHT", snapshot)
    path = journal.root / "stage-00.json"
    value = json.loads(path.read_bytes())
    value["snapshot"]["networks"] = {}
    path.write_text(json.dumps(value))
    with pytest.raises(OwnershipBlocked):
        StageJournal(journal.root, plan(snapshot))


def test_successful_inventory_never_grants_formal_authority(tmp_path, snapshot):
    journal = StageJournal(tmp_path / "journal", plan(snapshot))
    with pytest.raises(OwnershipBlocked, match="OFFLINE_REPAIR_ONLY"):
        journal.protected_boundary("PREFLIGHT", snapshot)


def test_mount_digest_covers_content_names_modes_and_symlinks(tmp_path):
    source = tmp_path / "config"
    source.mkdir()
    file = source / "settings"
    file.write_text("a")
    first = tree_commitment(source)
    file.write_text("b")
    assert tree_commitment(source) != first
    second = tree_commitment(source)
    file.chmod(0o400)
    assert tree_commitment(source) != second
    (source / "escape").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OwnershipBlocked):
        tree_commitment(source)


def test_legacy_campaign_entrypoints_stop_before_reading_or_constructing_runtime(
    monkeypatch,
):
    from scripts.product import run_payment_v040 as runner

    def forbidden(*args, **kwargs):
        pytest.fail("offline increment touched runtime")

    monkeypatch.setattr(runner, "ProductRuntimeV040", forbidden)
    for callback in (
        runner.main,
        lambda: runner.prepare(None, None),
        lambda: runner.campaign(None, None),
    ):
        with pytest.raises(OwnershipBlocked, match="OFFLINE_REPAIR_ONLY"):
            callback()


def test_review_manifest_complete_and_three_overlays_bound():
    from scripts.product.v040_ownership_provenance import kafka_overlay

    root = Path(__file__).resolve().parents[3]
    value = json.loads(
        (
            root / "config/product-v040/ownership-repair/environment-provenance.v1.json"
        ).read_bytes()
    )
    claimed = value.pop("manifest_sha256")
    assert sha(value) == claimed
    validate_mount_provenance(value)
    assert len(value["services"]) == 33
    assert value["kafka_overlay_sha256"] == sha(kafka_overlay())
    assert value["counts"] == COUNTERS
    for drift in ("source", "owner_labels", "image_digest", "content"):
        changed = deepcopy(value)
        mount = next(
            m
            for m in changed["mounts"]
            if m["service"] == "sandbox/kafka" and m["type"] == "volume"
        )
        mount[drift] = (
            {"sha256": "bad"}
            if drift == "content"
            else ({} if drift == "owner_labels" else "unbound")
        )
        with pytest.raises(OwnershipBlocked):
            validate_mount_provenance(changed)


@pytest.mark.parametrize("truncate", [False, True])
def test_offline_replay_entrypoint_and_incomplete_trace(
    tmp_path, raw, snapshot, truncate
):
    from scripts.product.replay_v040_ownership import replay

    expected = tmp_path / "expected.json"
    observed = tmp_path / "actual.json"
    expected.write_text(json.dumps(plan(snapshot), sort_keys=True))
    stages = STAGES[:-1] if truncate else STAGES
    observed.write_text(json.dumps([{"stage": s, "observation": raw} for s in stages]))
    if truncate:
        with pytest.raises(OwnershipBlocked):
            replay(expected, observed, tmp_path / "out")
        assert (tmp_path / "out/first-divergence.json").exists()
    else:
        result = replay(expected, observed, tmp_path / "out")
        assert result["authority"] == "NONE" and result["counts"] == COUNTERS


@pytest.mark.parametrize(
    "bad",
    [
        "not-json",
        "{}",
        "[{}]",
        "[null]",
        '[{"stage":"PREFLIGHT"}]',
        '[{"observation":{}}]',
        '[{"stage":"PREFLIGHT","observation":{},"unknown":true}]',
    ],
)
def test_malformed_trace_envelopes_latch_before_any_action(tmp_path, snapshot, bad):
    from scripts.product.replay_v040_ownership import replay

    expected, observed = tmp_path / "expected.json", tmp_path / "actual.json"
    expected.write_text(json.dumps(plan(snapshot)))
    observed.write_text(bad)
    with pytest.raises(OwnershipBlocked):
        replay(expected, observed, tmp_path / "out")
    record = json.loads((tmp_path / "out/first-divergence.json").read_bytes())
    assert record["stage"] in {"MALFORMED_TRACE", "MALFORMED_ENVELOPE"}
    assert record["counts"] == COUNTERS and record["authority"] == "NONE"


class FakeReadRuntime:
    def __init__(self, root, raw):
        self.private = root / "private"
        self.repository = root
        self.raw = raw
        self.calls = []

    def boundary(self):
        self.calls.append("boundary")
        return self.raw["daemon_before"]

    def docker(self, kind, action, *args, **kwargs):
        self.calls.append((kind, action, args))
        assert action in {"ls", "inspect"}
        plural = {
            "container": "containers",
            "network": "networks",
            "volume": "volumes",
            "image": "images",
        }[kind]
        if action == "ls":
            return "\n".join(self.raw["ids_before"][plural])
        return json.dumps(self.raw["inspect"][plural])


def test_read_adapter_requires_plan_before_any_daemon_query(tmp_path, raw):
    from scripts.product.v040_inventory_capture import checkpoint

    runtime = FakeReadRuntime(tmp_path, raw)
    with pytest.raises(OwnershipBlocked, match="no independently bound"):
        checkpoint(runtime, "BEFORE_FAULT")
    assert runtime.calls == []


@pytest.mark.parametrize(
    "stage",
    ["BEFORE_FAULT", "BEFORE_APPROVAL", "BEFORE_AUTHORIZATION", "BEFORE_REMEDIATION"],
)
def test_capture_adapter_blocks_replacement_before_protected_dispatch(
    tmp_path, raw, stage
):
    from scripts.product.v040_inventory_capture import checkpoint, collect

    runtime = FakeReadRuntime(tmp_path, raw)
    expected_snapshot = inventory_from_saved_inspects(collect(runtime))
    host = runtime.private / "host"
    host.mkdir(parents=True)
    (host / "ownership-stage-plan.json").write_text(json.dumps(plan(expected_snapshot)))
    for earlier in STAGES[: STAGES.index(stage)]:
        checkpoint(runtime, earlier)
    raw["ids_before"]["networks"][1] = "bridge2"
    raw["inspect"]["networks"][1]["Id"] = "bridge2"
    dispatched = []
    with pytest.raises(OwnershipBlocked):
        checkpoint(runtime, stage)
        dispatched.append("protected action")
    assert dispatched == []
    first = json.loads(
        (host / "ownership-stage-journal/first-divergence.json").read_bytes()
    )
    assert first["stage"] == stage and first["counts"] == COUNTERS
