from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040.volumes import validate_access
from scripts.product.qualification_v040_v2.policy import (
    access_gate,
    provenance_gate,
    validate_policy,
)


def policy():
    return json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "config/product-v040/copyup-access-v2/policy.json"
        ).read_text()
    )


def measurements(p):
    return [
        {
            "path": path,
            "entries": {".": {k: m[k] for k in ("kind", "uid", "gid", "mode", "size")}},
        }
        for path, m in p["image_source"]["paths"].items()
    ]


def image(p):
    return {
        "reference": p["pinned_reference"],
        "platform_digest": p["image_source"]["platform_digest"],
        "config_digest": p["image_source"]["config_digest"],
    }


def process():
    return {
        "pid": 1,
        "ppid": 0,
        "start_time": 12,
        "uids": [1000] * 4,
        "gids": [1000] * 4,
        "groups": [1000],
        "argv": ["/bin/sleep", "2147483647"],
        "cap_eff": 0,
        "cap_prm": 0,
        "no_new_privs": 1,
    }


def test_v2_is_independent_image_provenance_not_rewritten_v1():
    p = policy()
    observed = measurements(p)
    validate_policy(p)
    provenance_gate(p, observed, image(p))
    with pytest.raises(QualificationBlocked, match="COPYUP_IDENTITY_MISMATCH"):
        validate_access(observed, 1000, 1000)


@pytest.mark.parametrize("field,value", [("uid", 1001), ("gid", 1000), ("mode", 0o755)])
def test_path_specific_metadata_drift_is_not_effective_access_permission(field, value):
    p = policy()
    observed = measurements(p)
    observed[0]["entries"]["."][field] = value
    with pytest.raises(QualificationBlocked, match="COPYUP_SOURCE_METADATA_DRIFT"):
        provenance_gate(p, observed, image(p))


@pytest.mark.parametrize("field", ["reference", "platform_digest", "config_digest"])
def test_image_source_mismatch(field):
    p = policy()
    source = image(p)
    source[field] = "different"
    with pytest.raises(QualificationBlocked, match="IMAGE_SOURCE_MISMATCH"):
        provenance_gate(p, measurements(p), source)


def test_world_write_cannot_be_approved_by_image_matching():
    p = policy()
    p["image_source"]["paths"]["/etc/kafka/secrets"]["mode"] = 0o777
    p["image_source"]["all_relevant_entries"]["/etc/kafka/secrets"]["mode"] = 0o777
    with pytest.raises(QualificationBlocked, match="UNSAFE_IMAGE_METADATA"):
        validate_policy(p)


@pytest.mark.parametrize(
    "field", ["config_digest", "platform_digest", "rootfs_diff_ids"]
)
def test_incomplete_oci_provenance(field):
    p = policy()
    p["image_source"][field] = "" if "digest" in field else []
    with pytest.raises(QualificationBlocked, match="OCI_PROVENANCE_INCOMPLETE"):
        validate_policy(p)


@pytest.mark.parametrize(
    "case",
    [
        "supplementary_root",
        "primary_root",
        "unknown_writer",
        "extra_attachment",
        "reused_volume",
        "unbound_mount",
        "missing_groups",
        "incomplete_uid",
        "capability",
        "privilege",
        "unknown_group",
    ],
)
def test_access_gate_rejects_unbound_capabilities(case):
    p = policy()
    row = process()
    approved = [deepcopy(row)]
    expected = [
        {"volume": v, "target": p, "writer": "probe"}
        for v, p in zip(
            ("v1", "v2", "v3"),
            ("/etc/kafka/secrets", "/mnt/shared/config", "/var/lib/kafka/data"),
            strict=True,
        )
    ]
    attached = deepcopy(expected)
    fresh = {"v1", "v2", "v3"}
    if case == "supplementary_root":
        row["groups"] = [1000, 0]
    elif case == "primary_root":
        row["gids"] = [0] * 4
    elif case == "unknown_writer":
        row["argv"] = ["/bin/bash", "-c", "unbound"]
    elif case == "extra_attachment":
        attached.append({"volume": "v1", "writer": "product"})
    elif case == "reused_volume":
        fresh.remove("v2")
    elif case == "unbound_mount":
        attached[0]["target"] = "/other"
    elif case == "missing_groups":
        del row["groups"]
    elif case == "incomplete_uid":
        row["uids"] = [1000]
    elif case == "capability":
        row["cap_eff"] = 1
    elif case == "privilege":
        row["no_new_privs"] = 0
    elif case == "unknown_group":
        row["groups"] = [1000, 5]
    with pytest.raises(QualificationBlocked):
        access_gate(p, [row], approved, attached, expected, fresh, {"v1", "v2", "v3"})


def test_exact_owner_uid_access_does_not_require_supplementary_root():
    p = policy()
    row = process()
    attachments = [{"volume": v} for v in ("a", "b", "c")]
    access_gate(
        p,
        [row],
        [deepcopy(row)],
        attachments,
        attachments,
        {"a", "b", "c"},
        {"a", "b", "c"},
    )
