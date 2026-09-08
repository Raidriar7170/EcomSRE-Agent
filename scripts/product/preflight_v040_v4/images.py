"""Fresh immutable image inspection; index, platform and local identity stay distinct."""

from typing import Any
import json
from .common import REPO, require, load, seal
from .docker import Docker


IMAGE_COMPAT_DEFAULTS = {
    "AttachStderr": False,
    "AttachStdin": False,
    "AttachStdout": False,
    "Domainname": "",
    "Hostname": "",
    "Image": "",
    "OpenStdin": False,
    "StdinOnce": False,
    "Tty": False,
    "OnBuild": None,
}


def image_config(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config)
    for key, default in IMAGE_COMPAT_DEFAULTS.items():
        if key in result:
            require(result[key] == default, "IMAGE_COMPAT_CONFIG_DRIFT:" + key)
            del result[key]
    for key in (
        "Cmd",
        "Entrypoint",
        "Volumes",
        "OnBuild",
        "ExposedPorts",
        "Labels",
        "Healthcheck",
        "Shell",
    ):
        if key in result and result[key] is None:
            del result[key]
    for key in ("User", "WorkingDir"):
        if result.get(key) == "":
            del result[key]
    return result


def inspect_images(docker: Docker, evidence_root: Any) -> dict[str, Any]:
    locked = load(REPO / "config/product-v040/preflight-v4/images.json")["images"]
    result = {}
    for index, (reference, expected) in enumerate(sorted(locked.items())):
        index_digest = expected["index_reference"].split("@")[1]
        raw = json.loads(
            docker.read("image", "inspect", "--platform", "linux/arm64", index_digest)
        )
        require(len(raw) == 1, "IMAGE_INSPECT_CARDINALITY")
        item = raw[0]
        seal(evidence_root, f"images/{index:02d}.json", item)
        require(
            item["Os"] == "linux" and item["Architecture"] == "arm64",
            "IMAGE_PLATFORM_DRIFT",
        )
        require(expected["index_reference"] in item["RepoDigests"], "IMAGE_INDEX_DRIFT")
        require(
            image_config(item["Config"]) == image_config(expected["config"])
            and item["RootFS"] == expected["rootfs"],
            "IMAGE_CONTENT_DRIFT:" + reference,
        )
        descriptor = item.get("Descriptor")
        if descriptor:
            require(
                descriptor["digest"] == expected["platform_digest"],
                "IMAGE_DESCRIPTOR_DRIFT",
            )
        # Docker Desktop may expose the local index as Id instead of the selected
        # platform Id. Content equality, fixed platform and RepoDigest are all gates.
        require(
            item["Id"] in (index_digest, expected["platform_digest"]),
            "IMAGE_LOCAL_ID_DRIFT",
        )
        result[reference] = {
            **expected,
            "image_id": item["Id"],
            "runtime_reference": index_digest,
            "inspect": item,
        }
    return result
