"""Fresh runtime content commitment independent of publication-only commits."""

from __future__ import annotations
from typing import Any
from .common import REPO, UPSTREAM, git, sha, digest, require


def runtime_surface() -> dict[str, Any]:
    paths = git(
        "ls-files",
        "src",
        "config",
        "scripts/product/preflight_v040_v4",
        "Dockerfile.product",
        "docker-compose.product.yml",
        "docker-compose.product.preflight-v4.yml",
    ).splitlines()
    files = {}
    total = 0
    for relative in paths:
        path = REPO / relative
        require(path.is_file() and not path.is_symlink(), "RUNTIME_SOURCE_NOT_REGULAR")
        raw = path.read_bytes()
        files[relative] = sha(raw)
        total += len(raw)
    return {
        "sha256": digest({"files": files, "upstream": UPSTREAM}),
        "files": files,
        "bytes_read": total,
        "cache_hits": 0,
    }
