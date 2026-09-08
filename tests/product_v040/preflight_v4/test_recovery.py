"""A committed recovery can close one failed prior attempt without rewriting it."""

import json
from copy import deepcopy
import pytest
from scripts.product.preflight_v040_v4 import recovery
from scripts.product.preflight_v040_v4.common import Failure, GOAL_SHA, digest, sha
from scripts.product.preflight_v040_v4.identity import created_identity


def test_mount_order_is_not_mount_authority():
    before = {
        "Id": "id",
        "Created": "then",
        "Image": "image",
        "Name": "probe",
        "Config": {},
        "HostConfig": {"OomKillDisable": False},
        "Mounts": [
            {"Destination": "/a", "Source": "v1"},
            {"Destination": "/b", "Source": "v2"},
        ],
        "NetworkSettings": {"Networks": {"none": {"NetworkID": "", "EndpointID": ""}}},
        "State": {"Status": "created", "Running": False},
        "RestartCount": 0,
    }
    after = deepcopy(before)
    after["Mounts"].reverse()
    created_identity(before, after, {"none": "n" * 64})
    for key, value in (
        ("Aliases", ["unexpected"]),
        ("IPAMConfig", {"IPv4Address": "1.2.3.4"}),
        ("NetworkID", "changed"),
    ):
        changed = deepcopy(after)
        changed["NetworkSettings"]["Networks"]["none"][key] = value
        with pytest.raises(Failure, match="IMMUTABLE"):
            created_identity(before, changed, {"none": "n" * 64})
    after["Mounts"][0]["Source"] = "unknown"
    with pytest.raises(Failure, match="IMMUTABLE"):
        created_identity(before, after, {"none": "n" * 64})


@pytest.fixture
def recovered(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "REPO", tmp_path / "repo")
    root = tmp_path / "runs"
    identifier = "a" * 32
    prior = root / identifier

    def write(relative, value):
        p = prior / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value))
        return p

    intent = {"command": ["volume", "rm", "owned-volume"]}
    final = {"resources": {"volume": []}}
    values = {
        "attempt-result.json": {"status": "FAILED", "cleanup_status": "BLOCKED_SAFETY"},
        "cleanup-failure.json": {"reason": "NONOWNED_DRIFT"},
        "owned-cleanup-nonowned-drift-proof.json": {
            "owned_remaining": {"containers": 0, "networks": 0, "volumes": 0},
            "final_views_stable": True,
            "images_unchanged": True,
            "nonowned_unchanged": False,
        },
        "daemon-stabilization-event.json": {
            "observation": "VM_START_TRIGGERED_BY_FIRST_VOLUME_CREATE",
            "records": [{"raw": "idle -> busy /volumes/create"}],
        },
        "closure-final-inventory-1.json": deepcopy(final),
        "closure-final-inventory-2.json": deepcopy(final),
        "births/000-volume.json": {
            "kind": "volume",
            "record": {"Name": "owned-volume"},
        },
        "journal/intents/remove-volume.json": intent,
        "journal/receipts/remove-volume.json": {
            "intent_digest": digest(intent),
            "postcondition": True,
            "outcome": {"client_exit_status": 0},
        },
    }
    for name, value in values.items():
        write(name, value)
    proof = {
        "schema_version": "ecomsre.preflight.v4.stabilization-recovery.v2",
        "goal_sha256": GOAL_SHA,
        "attempt_id": identifier,
        "disposition": "OWNED_CLEANUP_VERIFIED_NEW_BASELINE_REQUIRED",
        "old_result_preserved": True,
        "nonowned_unchanged": False,
        "files": {name: sha((prior / name).read_bytes()) for name in values},
    }
    path = write("stabilization-recovery-v2.json", proof)
    policy = (
        recovery.REPO / "config/product-v040/preflight-v4/recovery-authorizations.json"
    )
    policy.parent.mkdir(parents=True)
    policy.write_text(
        json.dumps(
            {
                "attempts": {
                    identifier: {"file": path.name, "sha256": sha(path.read_bytes())}
                }
            }
        )
    )
    return root, identifier, prior


def test_recovery_keeps_failed_result(recovered):
    root, identifier, prior = recovered
    original = (prior / "attempt-result.json").read_bytes()
    assert recovery.cleanup_complete(root, identifier)
    assert (prior / "attempt-result.json").read_bytes() == original
    assert not recovery.cleanup_complete(root, "b" * 32)


@pytest.mark.parametrize(
    "path",
    [
        "attempt-result.json",
        "cleanup-failure.json",
        "daemon-stabilization-event.json",
        "closure-final-inventory-2.json",
        "births/000-volume.json",
        "journal/receipts/remove-volume.json",
    ],
)
def test_recovery_fails_on_any_frozen_evidence_drift(recovered, path):
    root, identifier, prior = recovered
    (prior / path).write_text("{}")
    with pytest.raises(Failure, match="EVIDENCE_DRIFT"):
        recovery.cleanup_complete(root, identifier)


def test_recovery_cannot_be_self_authorized(recovered):
    root, identifier, prior = recovered
    (prior / "stabilization-recovery-v2.json").write_text("{}")
    with pytest.raises(Failure, match="COMMITMENT_DRIFT"):
        recovery.cleanup_complete(root, identifier)


def test_index_identity_requires_exact_platform_descriptor():
    from scripts.product.preflight_v040_v4.birth import validate_image_identity

    index, platform = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    plan = {
        "image_id": platform,
        "platform_digest": platform,
        "service": {
            "image": index,
            "labels": {"io.ecomsre.preflight.v4.role": "probe"},
        },
    }
    row = {
        "Image": index,
        "Config": {"Image": index},
        "ImageManifestDescriptor": {
            "digest": platform,
            "platform": {"os": "linux", "architecture": "arm64"},
        },
    }
    validate_image_identity(row, plan)
    for changed in (
        {**row, "Image": "sha256:" + "c" * 64},
        {**row, "ImageManifestDescriptor": None},
        {
            **row,
            "ImageManifestDescriptor": {
                "digest": platform,
                "platform": {"os": "linux", "architecture": "amd64"},
            },
        },
    ):
        with pytest.raises(Failure, match="BIRTH_IMAGE"):
            validate_image_identity(changed, plan)
