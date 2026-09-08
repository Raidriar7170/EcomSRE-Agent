from copy import deepcopy

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040_v3.retained_cleanup import (
    CONTAINER,
    LABEL,
    PREFIX,
    QUAL,
    VOLUMES,
    validate,
)


def inventories():
    volumes = [
        {
            "Name": name,
            "Labels": {LABEL: QUAL},
            "Mountpoint": "/vol/" + name,
            "CreatedAt": "frozen",
            "Driver": "local",
        }
        for name in VOLUMES
    ]
    mounts = [
        {
            "Name": name,
            "Source": "/vol/" + name,
            "Destination": "/data/" + str(i),
            "RW": True,
        }
        for i, name in enumerate(VOLUMES[-3:])
    ]
    container = {
        "Id": CONTAINER,
        "Name": "/" + PREFIX + "volume-probe",
        "Created": "frozen",
        "Config": {"Labels": {LABEL: QUAL}, "Image": "pinned", "User": "1000:1000"},
        "HostConfig": {
            "NetworkMode": "none",
            "OomKillDisable": None,
            "Privileged": False,
        },
        "Image": "sha256:pinned",
        "Mounts": mounts,
    }
    current = {
        "daemon": {"ID": "daemon"},
        "context": [{"Endpoints": {"docker": {"Host": "unix:///owned"}}}],
        "inspect": {"containers": [container], "volumes": volumes, "networks": []},
    }
    retained = {
        "daemon_before": {"daemon_id": "daemon", "endpoint": "unix:///owned"},
        "inspect": deepcopy(current["inspect"]),
    }
    return current, retained


def test_exact_retained_fingerprint_is_accepted_without_normalization():
    current, retained = inventories()
    validate(current, retained, set(VOLUMES), True)


@pytest.mark.parametrize(
    "mutation",
    [
        "id",
        "name",
        "image",
        "labels",
        "mount",
        "attachment",
        "volume",
        "oom",
        "privilege",
        "daemon",
        "endpoint",
    ],
)
def test_cleanup_identity_mismatch_prevents_admission(mutation):
    current, retained = inventories()
    row = current["inspect"]["containers"][0]
    if mutation == "id":
        row["Id"] = "replacement"
    elif mutation == "name":
        row["Name"] = "/different"
    elif mutation == "image":
        row["Image"] = "sha256:other"
    elif mutation == "labels":
        row["Config"]["Labels"][LABEL] = "other"
    elif mutation == "mount":
        row["Mounts"][0]["RW"] = False
    elif mutation == "attachment":
        unknown = deepcopy(row)
        unknown["Id"] = "unknown"
        unknown["Config"]["Labels"] = {}
        current["inspect"]["containers"].append(unknown)
    elif mutation == "volume":
        current["inspect"]["volumes"][0]["CreatedAt"] = "recreated"
    elif mutation == "oom":
        row["HostConfig"]["OomKillDisable"] = False
    elif mutation == "privilege":
        row["HostConfig"]["Privileged"] = True
    elif mutation == "daemon":
        current["daemon"]["ID"] = "other"
    elif mutation == "endpoint":
        current["context"][0]["Endpoints"]["docker"]["Host"] = "tcp://remote"
    with pytest.raises(QualificationBlocked):
        validate(current, retained, set(VOLUMES), True)


def test_volume_removal_requires_no_attachment_after_container_removal():
    current, retained = inventories()
    current["inspect"]["containers"][0]["Config"]["Labels"] = {}
    with pytest.raises(QualificationBlocked, match="CLEANUP_ATTACHMENT_DRIFT"):
        validate(current, retained, set(VOLUMES), False)


def test_same_name_replacement_between_complete_passes_rejects():
    from scripts.product.qualification_v040_v3.retained_cleanup import validate_capture

    first, _ = inventories()
    first["ids"] = {
        kind: [r["Name" if kind == "volumes" else "Id"] for r in rows]
        for kind, rows in first["inspect"].items()
    }
    first["images"] = []
    second = deepcopy(first)
    validate_capture(first, second)
    second["inspect"]["volumes"][0]["CreatedAt"] = "replaced"
    with pytest.raises(QualificationBlocked, match="CLEANUP_RACED_INVENTORY"):
        validate_capture(first, second)
    second = deepcopy(first)
    second["inspect"]["volumes"].pop()
    with pytest.raises(QualificationBlocked, match="CLEANUP_INCOMPLETE_INVENTORY"):
        validate_capture(first, second)


@pytest.mark.parametrize(
    "source", ["/vol", "/vol/" + VOLUMES[0] + "/child", "/vol/./" + VOLUMES[0]]
)
def test_overlapping_bind_is_unexpected_attachment(source):
    current, retained = inventories()
    current["inspect"]["containers"].append(
        {
            "Id": "unknown",
            "Config": {"Labels": {}},
            "Mounts": [{"Type": "bind", "Source": source, "Destination": "/exposed"}],
        }
    )
    with pytest.raises(QualificationBlocked, match="CLEANUP_ATTACHMENT_DRIFT"):
        validate(current, retained, set(VOLUMES), True)


def test_image_order_does_not_change_identity_but_content_does():
    from scripts.product.qualification_v040_v3.retained_cleanup import image_set

    rows = [{"ID": "one", "Tag": "a"}, {"ID": "two", "Tag": "b"}]
    assert image_set(rows) == image_set(list(reversed(rows)))
    changed = deepcopy(rows)
    changed[0]["ID"] = "replacement"
    assert image_set(rows) != image_set(changed)
