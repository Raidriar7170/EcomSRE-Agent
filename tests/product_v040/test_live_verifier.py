"""Real temporary Git evidence: a resealed label cannot replace measured code."""

from types import SimpleNamespace
import subprocess

import pytest

from ecomsre.product.remediation.payment_control import digest
from scripts.ci.verify_product_v040_live import (
    current_sources,
    historical_sources,
    verify_code_binding,
)


@pytest.fixture
def committed_sources(tmp_path):
    def git(*args):
        return subprocess.run(("git", *args), cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Offline Fixture")
    for name in ("Dockerfile.product", "src/module.py", "config/product-v040/live-profile.v1.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    git("add", ".")
    git("commit", "-qm", "fixture")
    head = git("rev-parse", "HEAD")
    tree, sources = historical_sources(tmp_path, head)
    manifest = SimpleNamespace(code_head=head, code_tree=tree, source_inputs_sha256=digest({
        name: row for name, row in sources.items() if name == "Dockerfile.product" or name.startswith("src/")
    }))
    return tmp_path, manifest, sources


def test_git_commit_and_current_sources_bind_exactly(committed_sources):
    root, manifest, sources = committed_sources
    assert current_sources(root) == sources
    verify_code_binding(root, manifest, sources)


@pytest.mark.parametrize("field", ["code_tree", "source_inputs_sha256"])
def test_resealed_tree_or_build_label_cannot_replace_git_evidence(committed_sources, field):
    root, manifest, sources = committed_sources
    setattr(manifest, field, "0" * (40 if field == "code_tree" else 64))
    with pytest.raises(ValueError, match="differs"):
        verify_code_binding(root, manifest, sources)


def test_current_source_tampering_does_not_rewrite_measurement(committed_sources):
    root, manifest, sources = committed_sources
    (root / "src/module.py").write_text("tampered\n")
    with pytest.raises(ValueError, match="binding differs"):
        verify_code_binding(root, manifest, current_sources(root))


def test_omitted_historical_source_fails_closed(committed_sources):
    root, manifest, sources = committed_sources
    del sources["src/module.py"]
    with pytest.raises(ValueError, match="binding differs"):
        verify_code_binding(root, manifest, sources)
