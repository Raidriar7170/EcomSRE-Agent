"""Build only the frozen tracked Product context on the bound local Docker builder."""

from __future__ import annotations
import argparse
import json
import os
import subprocess
from typing import Any
from .common import (
    REPO,
    ROOT,
    GOAL_SHA,
    git,
    require,
    seal,
    seal_bytes,
    digest,
    now,
)
from .docker import Docker


def build() -> dict[str, Any]:
    require(not git("status", "--porcelain"), "BUILD_SOURCE_DIRTY")
    head = git("rev-parse", "HEAD")
    root = ROOT / "builds" / head
    root.mkdir(parents=True, mode=0o700)
    context = root / "context"
    context.mkdir(mode=0o700)
    paths = git(
        "ls-files",
        "src",
        "pyproject.toml",
        "uv.lock",
        "Dockerfile.product",
        "config/product-v040/remediation-registry.v1.json",
    ).splitlines()
    require(
        "Dockerfile.product" in paths
        and "config/product-v040/remediation-registry.v1.json" in paths,
        "BUILD_CONTEXT_INCOMPLETE",
    )
    manifest = {}
    for relative in paths:
        raw = subprocess.check_output(["git", "show", head + ":" + relative], cwd=REPO)
        manifest[relative] = seal_bytes(context, relative, raw)
    docker = Docker()
    binding = docker.binding()
    docker.bound = binding
    builder = docker.read("buildx", "inspect", "desktop-linux")
    require(
        "Driver:        docker" in builder
        or "Driver:       docker" in builder
        or any(line.split() == ["Driver:", "docker"] for line in builder.splitlines()),
        "BUILD_DRIVER_NOT_LOCAL",
    )
    require(
        any(
            line.split() == ["Endpoint:", "desktop-linux"]
            for line in builder.splitlines()
        ),
        "BUILD_ENDPOINT_NOT_LOCAL",
    )
    seal(root, "builder.json", {"raw": builder, "binding": binding})
    tag = "ecomsre-product-v040-preflight-v4:" + head[:12]
    before = docker.capture()
    require(
        not any(
            row["Repository"] == "ecomsre-product-v040-preflight-v4"
            and row["Tag"] == head[:12]
            for row in before["images"]
        ),
        "BUILD_TAG_PREEXISTS",
    )
    args = [
        "buildx",
        "build",
        "--builder",
        "desktop-linux",
        "--load",
        "--platform",
        "linux/arm64",
        "--provenance=false",
        "--tag",
        tag,
        "--label",
        "io.ecomsre.preflight.v4.source=" + head,
        "--label",
        "io.ecomsre.preflight.v4.goal=" + GOAL_SHA,
        "--iidfile",
        str(root / "iid.txt"),
        "--metadata-file",
        str(root / "metadata.json"),
        "-f",
        str(context / "Dockerfile.product"),
        str(context),
    ]
    seal(
        root,
        "build-intent.json",
        {
            "argv": args,
            "source_head": head,
            "context_files": manifest,
            "context_digest": digest(manifest),
            "binding": binding,
            **now(),
        },
    )
    result = docker.command(args, timeout=1200)
    seal(
        root,
        "build-response.json",
        {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            **now(),
        },
    )
    require(result.returncode == 0, "PRODUCT_BUILD_FAILED")
    identifier = (root / "iid.txt").read_text().strip()
    metadata = json.loads((root / "metadata.json").read_bytes())
    require(
        metadata["containerimage.config.digest"] == identifier, "BUILD_CONFIG_ID_DRIFT"
    )
    platform = metadata["containerimage.digest"]
    require(
        metadata["containerimage.descriptor"]["digest"] == platform
        and metadata["containerimage.descriptor"]["platform"]
        == {"architecture": "arm64", "os": "linux"},
        "BUILD_MANIFEST_PLATFORM_DRIFT",
    )
    image = json.loads(
        docker.read("image", "inspect", "--platform", "linux/arm64", platform)
    )[0]
    require(image["Id"] in (platform, identifier), "BUILD_LOCAL_IMAGE_ID_DRIFT")
    require(
        image["Os"] == "linux"
        and image["Architecture"] == "arm64"
        and image["Config"]["Labels"]["io.ecomsre.preflight.v4.source"] == head,
        "PRODUCT_BUILD_IDENTITY",
    )
    value = {
        "source_head": head,
        "context_files": manifest,
        "context_digest": digest(manifest),
        "dockerfile_sha256": manifest["Dockerfile.product"],
        "base_image_digest": "sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf",
        "uv_image_digest": "sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3",
        "image_id": image["Id"],
        "platform_digest": platform,
        "config_digest": identifier,
        "tag": tag,
        "config": image["Config"],
        "raw_inspect": image,
        "metadata": metadata,
        "binding": binding,
        "runtime_resources_created": 0,
        **now(),
    }
    for path in (root / "iid.txt", root / "metadata.json"):
        os.chmod(path, 0o600)
    seal(root, "product-image.json", value)
    return value


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = build()
    print(
        json.dumps(
            {
                "image_id": result["image_id"],
                "source_head": result["source_head"],
                "manifest": str(
                    ROOT / "builds" / result["source_head"] / "product-image.json"
                ),
            }
        )
    )
