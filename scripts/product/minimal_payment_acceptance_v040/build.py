"""Build a source-bound Product image from an exact, private tracked context."""

import json
from pathlib import Path
import subprocess
from .owned import command, digest, save, GOAL
from .plan import REPO


def main() -> None:
    if command("git", "-C", str(REPO), "status", "--porcelain"):
        raise ValueError("BUILD_SOURCE_DIRTY")
    head = command("git", "-C", str(REPO), "rev-parse", "HEAD")
    root = Path.home() / ".local/share/ecomsre-minimal-payment/builds" / head
    root.mkdir(parents=True, mode=0o700)
    context = root / "context"
    context.mkdir(mode=0o700)
    manifest = {}
    paths = command(
        "git",
        "-C",
        str(REPO),
        "ls-files",
        "src",
        "pyproject.toml",
        "uv.lock",
        "config/product-v040/remediation-registry.v1.json",
        "config/product-v040/minimal-payment/Dockerfile",
    ).splitlines()
    import hashlib

    for name in paths:
        raw = subprocess.check_output(["git", "show", head + ":" + name], cwd=REPO)
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        manifest[name] = hashlib.sha256(raw).hexdigest()
    tag = "ecomsre-product-minimal:" + head[:12]
    args = [
        "docker",
        "buildx",
        "build",
        "--builder",
        "desktop-linux",
        "--load",
        "--platform",
        "linux/arm64",
        "--provenance=false",
        "--label",
        "io.ecomsre.minimal.source=" + head,
        "--label",
        "io.ecomsre.minimal.goal=" + GOAL,
        "--tag",
        tag,
        "--metadata-file",
        str(root / "metadata.json"),
        "-f",
        str(context / "config/product-v040/minimal-payment/Dockerfile"),
        str(context),
    ]
    save(
        root / "intent.json",
        {
            "source_head": head,
            "manifest": manifest,
            "context_sha256": digest(manifest),
            "argv": args,
        },
    )
    result = subprocess.run(args, capture_output=True, text=True, timeout=1200)
    save(
        root / "response.json",
        {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )
    if result.returncode:
        raise ValueError("PRODUCT_BUILD_FAILED; private build response retained")
    row = json.loads(
        command("docker", "image", "inspect", "--platform", "linux/arm64", tag)
    )[0]
    if (
        row["Architecture"] != "arm64"
        or row["Config"]["Labels"]["io.ecomsre.minimal.source"] != head
    ):
        raise ValueError("PRODUCT_IMAGE_BINDING_MISMATCH")
    save(root / "image.json", row)
    print(json.dumps({"tag": tag, "image_id": row["Id"], "source_head": head}))


if __name__ == "__main__":
    main()
