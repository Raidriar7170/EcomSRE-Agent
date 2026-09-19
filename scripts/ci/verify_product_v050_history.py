"""Preserve v0.4.1's exact presentation snapshot alongside v0.5 successor docs.

The historical verifier and manifest are unmodified. Check every original byte
binding in the current checkout, allowing only explicitly bound presentation/CI
successors, then run that verifier against a temporary projection of its original
presentation bytes. This verifies historical evidence, not current live behavior.
"""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE = "550a564d29954e6f3c2395790294e23800231ac4"
ALLOWED = frozenset(
    {
        ".github/workflows/agent-mainline.yml",
        "README.md",
        "docs/interview/PROJECT_PITCH.md",
        "docs/product/STATUS.md",
        "docs/product/ARCHITECTURE.md",
        "docs/product/LIMITATIONS.md",
        "docs/product/QUICKSTART.md",
    }
)


def verify(root: Path = ROOT) -> dict:
    bindings = json.loads(
        (root / "config/product-v050/historical-successor-bindings.json").read_text()
    )
    if bindings["base_commit"] != BASE or set(bindings["files"]) != ALLOWED:
        raise ValueError("successor scope differs")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASE, "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    manifest_path = "docs/results/product-v041-live-safety/evidence-manifest.json"
    frozen_manifest = subprocess.check_output(
        ["git", "show", BASE + ":" + manifest_path], cwd=root
    )
    if (root / manifest_path).read_bytes() != frozen_manifest:
        raise ValueError("historical manifest differs from fixed base")
    manifest = json.loads(frozen_manifest)
    originals = {}
    for name, expected in manifest["files"].items():
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("historical path type differs: " + name)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if name in ALLOWED:
            entry = bindings["files"][name]
            original = subprocess.check_output(
                ["git", "show", BASE + ":" + name], cwd=root
            )
            if (
                hashlib.sha256(original).hexdigest() != expected
                or entry["historical_sha256"] != expected
                or entry["successor_sha256"] != actual
            ):
                raise ValueError("exact successor binding differs: " + name)
            originals[name] = original
        elif actual != expected:
            raise ValueError("historical evidence changed: " + name)
    if set(originals) != ALLOWED:
        raise ValueError("successor path missing from historical manifest")
    from scripts.ci import verify_product_v041_closeout as historical

    with tempfile.TemporaryDirectory(prefix="ecomsre-v050-history-") as temp:
        projected = Path(temp)
        paths = (
            subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
            .decode()
            .split("\0")
        )
        for name in paths:
            if not name:
                continue
            source = root / name
            if not source.is_file():
                continue
            target = projected / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name in originals:
                target.write_bytes(originals[name])
            else:
                target.symlink_to(source)
        prior_root, prior_result = historical.ROOT, historical.RESULT
        try:
            historical.ROOT = projected
            historical.RESULT = projected / "docs/results/product-v041-live-safety"
            historical.main()
        finally:
            historical.ROOT, historical.RESULT = prior_root, prior_result
    return {
        "status": "PASS",
        "scope": "HISTORICAL_V041_EVIDENCE_AND_EXACT_SUCCESSOR_DOCS",
        "base_commit": BASE,
        "successor_paths": sorted(ALLOWED),
        "historical_verifier_modified": False,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
