"""Image-derived provenance and independently checked effective access."""

from __future__ import annotations

from typing import Any

from scripts.product.qualification_v040.guard import require, sha
from scripts.product.qualification_v040.volumes import KAFKA_PATHS

POLICY_ID = "COPYUP_EFFECTIVE_ACCESS_POLICY_V2"


def validate_policy(policy: dict[str, Any]) -> None:
    require(
        policy["policy_id"] == POLICY_ID and policy["formal_authority"] is False,
        "POLICY_INVALID",
    )
    source = policy["image_source"]
    require(
        source["evidence_kind"] == "DIRECT_HASH_VERIFIED_OCI_LAYER_CONTENT"
        and source["platform"] == "linux/arm64"
        and source["config_user"] == "appuser"
        and set(source["config_volumes"]) == set(KAFKA_PATHS)
        and set(source["paths"]) == set(KAFKA_PATHS)
        and len(source["rootfs_diff_ids"]) == len(source["manifest_layers"]) > 0,
        "OCI_PROVENANCE_INCOMPLETE",
    )
    for field in ("platform_digest", "config_digest"):
        require(
            isinstance(source.get(field), str)
            and source[field].startswith("sha256:")
            and len(source[field]) == 71,
            "OCI_PROVENANCE_INCOMPLETE",
        )
    require(
        source["image_repo_digests"] == [policy["pinned_reference"]],
        "IMAGE_SOURCE_MISMATCH",
    )
    require(
        policy["process"]["euid"] == 1000
        and policy["process"]["egid"] == 1000
        and policy["process"]["forbidden_groups"] == [0]
        and policy["runtime_qualification_limit"] == 1,
        "POLICY_INVALID",
    )
    relevant = source["all_relevant_entries"]
    require(
        {
            p
            for p in relevant
            if any(p == root or p.startswith(root + "/") for root in KAFKA_PATHS)
        }
        == set(KAFKA_PATHS),
        "OCI_UNBOUND_SEED_CONTENT",
    )
    require(
        all(source["paths"][p] == relevant[p] for p in KAFKA_PATHS),
        "OCI_PROVENANCE_INCOMPLETE",
    )
    for path, metadata in source["paths"].items():
        require(
            metadata["path"] == path
            and metadata["kind"] == "directory"
            and metadata["uid"] == 1000
            and metadata["mode"] & 0o700 == 0o700
            and not metadata["mode"] & 0o002,
            "UNSAFE_IMAGE_METADATA",
        )
        index = metadata["layer_index"]
        require(
            0 <= index < len(source["rootfs_diff_ids"])
            and source["rootfs_diff_ids"][index] == metadata["rootfs_diff_id"]
            and source["manifest_layers"][index]["digest"] == metadata["layer_digest"],
            "OCI_PROVENANCE_INCOMPLETE",
        )
        if metadata["mode"] & 0o020:
            require(
                metadata["gid"] == 0
                and policy["group_write_decision"][path]
                == "EXACT_IMAGE_GID_0_NOT_IN_PROCESS_GROUPS_NO_OTHER_ATTACHMENTS",
                "GROUP_WRITE_UNAPPROVED",
            )


def provenance_gate(
    policy: dict[str, Any], measurements: list[dict[str, Any]], image: dict[str, Any]
) -> None:
    validate_policy(policy)
    source = policy["image_source"]
    require(
        image["platform_digest"] == source["platform_digest"]
        and image["reference"] == policy["pinned_reference"]
        and image["config_digest"] == source["config_digest"],
        "IMAGE_SOURCE_MISMATCH",
    )
    require(
        len(measurements) == 3
        and {m["path"] for m in measurements} == set(KAFKA_PATHS),
        "COPYUP_PROVENANCE_INCOMPLETE",
    )
    for row in measurements:
        expected = source["paths"][row["path"]]
        # OCI adjudication freezes these exact empty image directories. No extra
        # seed entry can silently become an accepted initial baseline.
        require(set(row["entries"]) == {"."}, "UNKNOWN_COPYUP_ENTRY")
        entry = row["entries"]["."]
        require(
            all(entry[k] == expected[k] for k in ("kind", "uid", "gid", "mode")),
            "COPYUP_SOURCE_METADATA_DRIFT",
            row["path"],
        )


def validate_process(policy: dict[str, Any], process: dict[str, Any]) -> None:
    require(
        set(
            (
                "pid",
                "ppid",
                "start_time",
                "uids",
                "gids",
                "groups",
                "argv",
                "cap_eff",
                "cap_prm",
                "no_new_privs",
            )
        ).issubset(process),
        "PROCESS_IDENTITY_INCOMPLETE",
    )
    require(
        type(process["pid"]) is int
        and process["pid"] > 0
        and type(process["ppid"]) is int
        and process["ppid"] >= 0
        and type(process["start_time"]) is int
        and process["start_time"] > 0,
        "PROCESS_IDENTITY_INCOMPLETE",
    )
    expected = policy["process"]
    require(
        process["uids"] == [expected["euid"]] * 4
        and process["gids"] == [expected["egid"]] * 4,
        "PROCESS_IDENTITY_DRIFT",
    )
    require(
        isinstance(process["groups"], list)
        and all(type(g) is int and g >= 0 for g in process["groups"]),
        "PROCESS_GROUPS_INCOMPLETE",
    )
    require(
        not (set(process["groups"]) | set(process["gids"]))
        & set(expected["forbidden_groups"]),
        "UNEXPECTED_SUPPLEMENTARY_GID_0",
    )
    require(
        set(process["groups"]).issubset({expected["egid"]}), "UNEXPECTED_PROCESS_GROUP"
    )
    require(
        process["cap_eff"] == 0
        and process["cap_prm"] == 0
        and process["no_new_privs"] == 1,
        "PROCESS_CAPABILITY_DRIFT",
    )


def access_gate(
    policy: dict[str, Any],
    processes: list[dict[str, Any]],
    approved_argv: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    expected_attachments: list[dict[str, Any]],
    fresh_volumes: set[str],
    expected_volumes: set[str],
) -> None:
    require(
        fresh_volumes == expected_volumes and len(fresh_volumes) == 3,
        "REUSED_OR_UNBOUND_VOLUME",
    )
    require(
        {a["volume"] for a in expected_attachments} == expected_volumes, "UNBOUND_MOUNT"
    )
    require(
        sorted(attachments, key=sha) == sorted(expected_attachments, key=sha),
        "EXTRA_OR_UNBOUND_ATTACHMENT",
    )
    require(
        bool(processes) and len({p["pid"] for p in processes}) == len(processes),
        "PROCESS_CENSUS_INCOMPLETE",
    )
    require(any(p["pid"] == 1 for p in processes), "PROCESS_CENSUS_INCOMPLETE")
    for process in processes:
        validate_process(policy, process)
        require(process in approved_argv, "UNKNOWN_WRITER_PROCESS")
        for metadata in policy["image_source"]["paths"].values():
            require(
                process["uids"][1] == metadata["uid"]
                and metadata["mode"] & 0o300 == 0o300
                and not metadata["mode"] & 0o002,
                "EFFECTIVE_ACCESS_DENIED",
            )
            if metadata["mode"] & 0o020:
                require(
                    metadata["gid"]
                    not in set(process["groups"]) | set(process["gids"]),
                    "GROUP_WRITE_PROCESS_MEMBER",
                )
