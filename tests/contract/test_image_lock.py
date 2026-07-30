import json
from pathlib import Path

from ecomsre.environment.manifests import (
    ImageLockManifest,
    ImageLockStatus,
    load_image_lock,
    verify_acceptance_image_lock,
)
from ecomsre.phase0.models import Outcome


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "config" / "phase0" / "image-lock.json"


def test_repository_image_lock_is_explicitly_uninitialized_without_fake_digests() -> (
    None
):
    raw = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    lock = ImageLockManifest.model_validate(raw)

    assert lock.status is ImageLockStatus.UNINITIALIZED
    assert lock.images == ()
    assert lock.compose_config_sha256 is None
    assert "digest" not in LOCK_PATH.read_text(encoding="utf-8").lower()


def test_uninitialized_lock_blocks_formal_acceptance() -> None:
    lock = load_image_lock(LOCK_PATH)

    result = verify_acceptance_image_lock(
        lock,
        cached_images=(),
        observed_upstream_commit=lock.upstream_commit,
        observed_compose_config_sha256="a" * 64,
    )

    assert result.passed is False
    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert result.reason_codes == ("INPUT_NOT_FROZEN",)
