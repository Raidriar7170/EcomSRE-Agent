"""Verify immutable Goal, starting tree, submodule and historical result bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.remediation.contracts import RemediationRegistryV1


START = "3f851bdfd17f686fe84a20a390122deb9c7276b7"
TREE = "61341661145010a785e5d256583107dc75bf8046"
GOAL_SHA = "d8ec6455a6108f40d67eb8441f18e952670b087255c0fb15fc14ccb87e32695a"
UPSTREAM = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
FROZEN_PREFIXES = (
    "src/ecomsre/dta_v2/",
    "config/dta-v2/",
    "docs/results/dta-",
    "docs/results/product-",
    "docs/analysis/product-v030-",
)


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ("git", "-C", str(root), *args), check=True, capture_output=True
    ).stdout


def sealed_file(path: Path, field: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    body = {key: item for key, item in value.items() if key != field}
    if value.get(field) != semantic_sha256_v22(body):
        raise ValueError("Product v0.4 manifest seal differs")
    return value


def verify(root: Path) -> dict[str, Any]:
    goal = sealed_file(
        root / "config/product-v040/goal-contract.v1.json", "contract_sha256"
    )
    if (
        goal["goal_sha256"] != GOAL_SHA
        or hashlib.sha256(
            json.loads(
                (root / "config/product-v040/goal-contract.source.json").read_bytes()
            )["text"].encode("utf-8")
        ).hexdigest()
        != GOAL_SHA
    ):
        raise ValueError("frozen Goal bytes differ")
    if (goal["starting_main"], goal["starting_tree"], goal["upstream_commit"]) != (
        START,
        TREE,
        UPSTREAM,
    ):
        raise ValueError("Goal starting binding differs")
    if git(root, "rev-parse", START + "^{tree}").decode().strip() != TREE:
        raise ValueError("starting tree differs")
    git(root, "merge-base", "--is-ancestor", START, "HEAD")
    if (
        git(root, "ls-tree", "HEAD", "third_party/opentelemetry-demo")
        .decode()
        .split()[2]
        != UPSTREAM
    ):
        raise ValueError("upstream pointer differs")
    # Verify the index pointer too, so a staged drift cannot pass pre-commit.
    if (
        git(root, "ls-files", "--stage", "third_party/opentelemetry-demo")
        .decode()
        .split()[1]
        != UPSTREAM
    ):
        raise ValueError("staged upstream pointer differs")
    manifest = sealed_file(
        root / "config/product-v040/historical-bindings.v1.json", "manifest_sha256"
    )
    if (
        manifest["starting_main"],
        manifest["starting_tree"],
        manifest["upstream_commit"],
    ) != (START, TREE, UPSTREAM):
        raise ValueError("history starting binding differs")
    expected_paths = [
        path
        for path in git(root, "ls-tree", "-r", "--name-only", START)
        .decode()
        .splitlines()
        if path.startswith(FROZEN_PREFIXES)
    ]
    if [item["path"] for item in manifest["frozen_artifacts"]] != expected_paths:
        raise ValueError("historical inventory differs")
    # Read historical objects in one Git process rather than one process per file.
    requests = "".join(f"{START}:{path}\n" for path in expected_paths).encode()
    batch = subprocess.run(
        ("git", "-C", str(root), "cat-file", "--batch"),
        input=requests,
        capture_output=True,
        check=True,
    ).stdout
    offset = 0
    for item in manifest["frozen_artifacts"]:
        end = batch.index(b"\n", offset)
        header = batch[offset:end].split()
        size = int(header[2])
        original = batch[end + 1 : end + 1 + size]
        offset = end + 2 + size
        path = root / item["path"]
        current = path.read_bytes()
        if (
            path.is_symlink()
            or current != original
            or size != item["size_bytes"]
            or hashlib.sha256(original).hexdigest() != item["sha256"]
        ):
            raise ValueError("historical artifact bytes differ: " + item["path"])
    for relative, digest in manifest["pr90_correctness_sources"].items():
        if (
            hashlib.sha256(git(root, "show", f"{START}:{relative}")).hexdigest()
            != digest
        ):
            raise ValueError("PR90 historical source binding differs")
    registry = RemediationRegistryV1.model_validate_json(
        (root / "config/product-v040/remediation-registry.v1.json").read_bytes()
    )
    return {
        "terminal": "ECOMSRE_PRODUCT_V040_HISTORY_BINDING_PASS",
        "goal_sha256": GOAL_SHA,
        "starting_main": START,
        "frozen_files": len(expected_paths),
        "registry_sha256": registry.registry_sha256,
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path.cwd()), sort_keys=True))
