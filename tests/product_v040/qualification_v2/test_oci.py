import gzip
import hashlib
import io
import json
import tarfile

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040.volumes import KAFKA_PATHS
from scripts.product.qualification_v040_v2.oci import TARGETS, inspect_oci


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def member(tar, name, value):
    info = tarfile.TarInfo(name)
    info.size = len(value)
    tar.addfile(info, io.BytesIO(value))


def fixture(tmp_path, mutation=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:") as layer:
        for path in TARGETS:
            info = tarfile.TarInfo(path.lstrip("/"))
            if path in KAFKA_PATHS:
                info.type = tarfile.DIRTYPE
                info.uid = 1000
                info.gid = 1000 if path == KAFKA_PATHS[1] else 0
                info.mode = 0o755 if path == KAFKA_PATHS[1] else 0o775
                if mutation == "symlink" and path == KAFKA_PATHS[0]:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/outside"
                layer.addfile(info)
            else:
                data = b"fixture"
                info.size = len(data)
                layer.addfile(info, io.BytesIO(data))
    raw = stream.getvalue()
    compressed = gzip.compress(raw, mtime=0)
    config = {
        "architecture": "arm64",
        "os": "linux",
        "config": {"User": "appuser", "Volumes": dict.fromkeys(KAFKA_PATHS, {})},
        "rootfs": {"diff_ids": [digest(raw)]},
    }
    config_bytes = json.dumps(config).encode()
    manifest = {
        "config": {"digest": digest(config_bytes), "size": len(config_bytes)},
        "layers": [
            {
                "digest": digest(compressed),
                "size": len(compressed),
                "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
            }
        ],
    }
    manifest_bytes = json.dumps(manifest).encode()
    binding = {
        "Id": digest(manifest_bytes),
        "RootFS": {"Layers": [digest(raw)]},
        "Config": config["config"],
        "RepoDigests": ["test@sha256:" + "1" * 64],
    }
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w:") as outer:
        member(outer, "blobs/sha256/" + digest(manifest_bytes)[7:], manifest_bytes)
        member(outer, "blobs/sha256/" + digest(config_bytes)[7:], config_bytes)
        if mutation != "missing_layer":
            member(
                outer,
                "blobs/sha256/" + digest(compressed)[7:],
                b"corrupt" if mutation == "tamper" else compressed,
            )
    return path, binding


def test_direct_oci_path_metadata_is_independent_of_runtime(tmp_path):
    path, binding = fixture(tmp_path)
    result = inspect_oci(path, binding)
    assert [result["paths"][p]["gid"] for p in KAFKA_PATHS] == [0, 1000, 0]
    assert result["runtime_started"] is False
    assert result["original_index_bytes_in_export"] is False


@pytest.mark.parametrize("mutation", ["tamper", "missing_layer", "symlink"])
def test_incomplete_or_untrusted_oci_provenance_blocks(tmp_path, mutation):
    path, binding = fixture(tmp_path, mutation)
    with pytest.raises(QualificationBlocked):
        inspect_oci(path, binding)
