"""Read pinned OCI layers without extraction or container creation."""

from __future__ import annotations

import gzip
import io
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Any

from scripts.product.qualification_v040.guard import require, QualificationBlocked
from scripts.product.qualification_v040.volumes import KAFKA_PATHS

IDENTITY_FILES = ("/etc/passwd", "/etc/group")
SCRIPT_FILES = (
    "/__cacert_entrypoint.sh",
    "/etc/kafka/docker/run",
    "/etc/kafka/docker/configure",
    "/etc/kafka/docker/launch",
    "/etc/kafka/docker/bash-config",
    "/etc/kafka/docker/configureDefaults",
    "/opt/kafka/bin/kafka-run-class.sh",
    "/opt/kafka/bin/kafka-server-start.sh",
)
TARGETS = (*KAFKA_PATHS, *IDENTITY_FILES, *SCRIPT_FILES)


class HashReader(io.RawIOBase):
    def __init__(self, source: Any) -> None:
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, count: int = -1) -> bytes:
        value = self.source.read(count)
        self.digest.update(value)
        return value


def _inspect_oci(archive_path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    platform = binding["Id"]
    expected_diff_ids = binding["RootFS"]["Layers"]
    records: dict[str, Any] = {}
    contents: dict[str, str] = {}
    history: list[dict[str, Any]] = []
    filesystem_paths: set[str] = set()
    with tarfile.open(archive_path, "r:") as outer:
        members = outer.getmembers()
        require(len({m.name for m in members}) == len(members), "OCI_DUPLICATE_BLOB")

        def blob(digest: str, size: int | None = None) -> bytes:
            require(
                digest.startswith("sha256:") and len(digest) == 71, "OCI_DIGEST_INVALID"
            )
            name = "blobs/sha256/" + digest[7:]
            member = outer.getmember(name)
            require(
                member.isfile() and member.size <= 512 * 1024 * 1024, "OCI_BLOB_INVALID"
            )
            stream = outer.extractfile(member)
            assert stream is not None
            value = stream.read()
            require(
                "sha256:" + hashlib.sha256(value).hexdigest() == digest,
                "OCI_BLOB_DIGEST_MISMATCH",
            )
            require(size is None or len(value) == size, "OCI_BLOB_SIZE_MISMATCH")
            return value

        manifest_bytes = blob(platform)
        manifest = json.loads(manifest_bytes)
        config = json.loads(
            blob(manifest["config"]["digest"], manifest["config"]["size"])
        )
        require(
            config["architecture"] == "arm64" and config["os"] == "linux",
            "OCI_PLATFORM_MISMATCH",
        )
        require(config["config"] == binding["Config"], "OCI_CONFIG_MISMATCH")
        require(
            config["rootfs"]["diff_ids"] == expected_diff_ids
            and len(manifest["layers"]) == len(expected_diff_ids),
            "OCI_ROOTFS_MISMATCH",
        )
        for number, (layer, diff_id) in enumerate(
            zip(manifest["layers"], expected_diff_ids, strict=True)
        ):
            # Verify the compressed OCI blob, then independently hash the complete
            # uncompressed tar stream against config.rootfs.diff_ids.
            blob(layer["digest"], layer["size"])
            source = outer.extractfile("blobs/sha256/" + layer["digest"][7:])
            assert source is not None
            decoded = (
                gzip.GzipFile(fileobj=source)
                if layer["mediaType"].endswith("+gzip")
                else source
            )
            hashed = HashReader(decoded)
            with tarfile.open(fileobj=hashed, mode="r|") as tar:
                for member in tar:
                    raw = PurePosixPath(member.name)
                    require(
                        not raw.is_absolute() and ".." not in raw.parts,
                        "OCI_UNSAFE_PATH",
                    )
                    path = "/" + str(raw).removeprefix("./")
                    if "/.wh." in path:
                        parent, leaf = path.rsplit("/", 1)
                        deleted = parent + "/" + leaf.removeprefix(".wh.")
                        if leaf == ".wh..wh..opq":
                            removed = [p for p in records if p.startswith(parent + "/")]
                        else:
                            removed = [
                                p
                                for p in records
                                if p == deleted or p.startswith(deleted + "/")
                            ]
                        filesystem_paths = {
                            p
                            for p in filesystem_paths
                            if not (
                                p.startswith(parent + "/")
                                if leaf == ".wh..wh..opq"
                                else p == deleted or p.startswith(deleted + "/")
                            )
                        }
                        for p in removed:
                            records.pop(p, None)
                            contents.pop(p, None)
                        continue
                    filesystem_paths.add(path)
                    relevant = path in TARGETS or any(
                        path.startswith(p + "/")
                        for p in (*KAFKA_PATHS, "/opt/kafka/libs")
                    )
                    ancestor = any(p.startswith(path + "/") for p in TARGETS)
                    if not relevant and not ancestor:
                        continue
                    require(
                        member.isdir() or member.isfile(),
                        "OCI_RELEVANT_LINK_OR_SPECIAL",
                        path,
                    )
                    require(
                        not member.pax_headers, "OCI_UNBOUND_EXTENDED_METADATA", path
                    )
                    record = {
                        "path": path,
                        "kind": "directory" if member.isdir() else "file",
                        "uid": member.uid,
                        "gid": member.gid,
                        "mode": member.mode & 0o7777,
                        "size": member.size,
                        "layer_index": number,
                        "layer_digest": layer["digest"],
                        "rootfs_diff_id": diff_id,
                    }
                    if member.isfile():
                        require(
                            member.size <= 128 * 1024 * 1024,
                            "OCI_RELEVANT_FILE_BOUNDS",
                            path,
                        )
                        stream = tar.extractfile(member)
                        assert stream is not None
                        value = stream.read()
                        record["sha256"] = hashlib.sha256(value).hexdigest()
                        if path in (*IDENTITY_FILES, *SCRIPT_FILES):
                            contents[path] = value.decode()
                    records[path] = record
                    history.append(record)
            while hashed.read(1024 * 1024):
                pass
            require(
                "sha256:" + hashed.digest.hexdigest() == diff_id, "OCI_DIFF_ID_MISMATCH"
            )
        require(set(TARGETS).issubset(records), "OCI_PROVENANCE_INCOMPLETE")
        require(
            set(config["config"].get("Volumes") or {}) == set(KAFKA_PATHS),
            "OCI_VOLUME_DECLARATION_MISMATCH",
        )
        return {
            "filesystem_paths": sorted(filesystem_paths),
            "schema_version": "ecomsre.v040.copyup.image-source.v2",
            "image_repo_digests": binding["RepoDigests"],
            "platform_digest": platform,
            "config_digest": manifest["config"]["digest"],
            "platform": "linux/arm64",
            "config_user": config["config"]["User"],
            "config_env": config["config"].get("Env", []),
            "config_volumes": config["config"]["Volumes"],
            "rootfs_diff_ids": expected_diff_ids,
            "manifest_layers": manifest["layers"],
            "paths": {p: records[p] for p in KAFKA_PATHS},
            "all_relevant_entries": records,
            "layer_history": history,
            "identity_and_scripts": contents,
            "evidence_kind": "DIRECT_HASH_VERIFIED_OCI_LAYER_CONTENT",
            "image_index_binding_kind": "DAEMON_REPODIGEST_TO_PLATFORM_BINDING",
            "original_index_bytes_in_export": False,
            "runtime_started": False,
        }


def inspect_oci(archive_path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    try:
        return _inspect_oci(archive_path, binding)
    except QualificationBlocked:
        raise
    except (KeyError, ValueError, tarfile.TarError, OSError, TypeError) as error:
        raise QualificationBlocked(
            "OCI_PROVENANCE_INCOMPLETE", type(error).__name__
        ) from error
