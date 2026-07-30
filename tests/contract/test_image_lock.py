import json
import re
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


def test_repository_image_lock_is_complete_locked_arm64_inventory() -> None:
    raw = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    lock = ImageLockManifest.model_validate(raw)

    assert lock.status is ImageLockStatus.LOCKED
    assert lock.upstream_tag == "3.0.0"
    assert (
        lock.upstream_commit
        == "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    )
    assert len(lock.images) == 25
    assert lock.compose_config_sha256 is not None
    assert re.fullmatch(r"[0-9a-f]{64}", lock.compose_config_sha256)
    assert len(lock.allowed_source_references) == 25
    assert {image.source_reference for image in lock.images} == set(
        lock.allowed_source_references
    )
    assert all(image.architecture == "arm64" for image in lock.images)
    assert all(image.platform == "linux/arm64" for image in lock.images)
    assert all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image.image_index_digest)
        for image in lock.images
    )
    assert all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image.resolved_platform_digest)
        for image in lock.images
    )
    assert all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image.image_id)
        for image in lock.images
    )
    assert all(image.upstream_commit == lock.upstream_commit for image in lock.images)
    assert all(
        image.compose_config_sha256 == lock.compose_config_sha256
        for image in lock.images
    )


def test_uninitialized_lock_fixture_blocks_formal_acceptance(tmp_path: Path) -> None:
    path = tmp_path / "image-lock.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.image-lock.v1",
                "status": "UNINITIALIZED",
                "upstream_tag": "3.0.0",
                "upstream_commit": (
                    "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
                ),
                "compose_config_sha256": None,
                "created_at": None,
                "allowed_source_references": [],
                "images": [],
            }
        ),
        encoding="utf-8",
    )
    lock = load_image_lock(path)

    result = verify_acceptance_image_lock(
        lock,
        cached_images=(),
        observed_upstream_commit=lock.upstream_commit,
        observed_compose_config_sha256="a" * 64,
    )

    assert result.passed is False
    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert result.reason_codes == ("INPUT_NOT_FROZEN",)


def test_locked_fixture_is_independent_of_repository_path(tmp_path: Path) -> None:
    path = tmp_path / "locked-image-lock.json"
    compose_hash = "c" * 64
    created_at = "2026-07-30T10:00:00Z"
    sources = [
        "registry.example/fixture/service-a:1.0.0",
        "registry.example/fixture/service-b:1.0.0",
    ]
    path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.image-lock.v1",
                "status": "LOCKED",
                "upstream_tag": "3.0.0",
                "upstream_commit": (
                    "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
                ),
                "compose_config_sha256": compose_hash,
                "created_at": created_at,
                "allowed_source_references": sources,
                "images": [
                    {
                        "logical_name": f"fixture-{index}",
                        "source_reference": source,
                        "image_index_digest": f"sha256:{index:064x}",
                        "resolved_platform_digest": f"sha256:{index + 2:064x}",
                        "architecture": "arm64",
                        "platform": "linux/arm64",
                        "image_id": f"sha256:{index + 4:064x}",
                        "acquired_at": created_at,
                        "upstream_commit": (
                            "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
                        ),
                        "compose_config_sha256": compose_hash,
                    }
                    for index, source in enumerate(sources, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )

    lock = load_image_lock(path)

    assert lock.status is ImageLockStatus.LOCKED
    assert len(lock.images) == 2
    assert set(lock.allowed_source_references) == set(sources)
