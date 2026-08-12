from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre_live_sandbox.contracts import (
    LocalEndpoints,
    ResolvedSandbox,
    canonical_sha256,
    file_sha256,
    load_bundle,
)
from ecomsre_live_sandbox.environment import CommandResult, SandboxDriftError, SandboxEnvironment
from ecomsre_live_sandbox.image_authority import (
    CachedImage,
    CachedImageInspection,
    ComposeIdentityMismatch,
    ImageAuthorityMismatch,
    compose_identities,
    ensure_image_authority,
    write_run_image_verification,
)


def _inspection(*, image_id: str = "sha256:" + "1" * 64) -> CachedImageInspection:
    image = CachedImage(
        source_reference="example.invalid/demo:3.0.0",
        image_id=image_id,
        image_index_digest="sha256:" + "2" * 64,
        resolved_platform_digest=image_id,
        raw_inspect_sha256="3" * 64,
    )
    return CachedImageInspection(
        historical_image_lock_sha256="4" * 64,
        upstream_commit="1755859a9de82c2e5e225be68abc401a5ebf2b4f",
        upstream_tag="3.0.0",
        platform="linux/arm64",
        images=(image,),
    )


def _compose(flagd: Path) -> dict[str, object]:
    return {
        "name": "ecomsre-live-sandbox-v1",
        "services": {
            "flagd": {
                "image": "example.invalid/demo:3.0.0",
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(flagd),
                        "target": "/etc/flagd",
                        "read_only": True,
                    }
                ],
            },
            "flagd-ui": {
                "image": "example.invalid/demo:3.0.0",
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(flagd),
                        "target": "/app/data",
                        "read_only": False,
                    }
                ],
            },
        },
        "networks": {"default": {"name": "ecomsre-live-sandbox-v1-default"}},
    }


def test_shared_authority_is_created_once_then_read_without_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "control" / "image-authority.json"
    inspection = _inspection()

    first = ensure_image_authority(path, inspection)
    before = path.stat().st_mtime_ns
    second = ensure_image_authority(path, inspection)

    assert first == second
    assert path.stat().st_mtime_ns == before
    payload = json.loads(path.read_text())
    assert payload["authority_sha256"] == canonical_sha256(
        {key: value for key, value in payload.items() if key != "authority_sha256"}
    )
    assert "run_id" not in payload
    assert "compose" not in json.dumps(payload).casefold()


@pytest.mark.parametrize(
    "field",
    ("historical", "source", "image_id", "platform_digest"),
)
def test_shared_authority_mismatch_fails_closed(tmp_path: Path, field: str) -> None:
    path = tmp_path / "control" / "image-authority.json"
    original = _inspection()
    ensure_image_authority(path, original)
    image = original.images[0]
    if field == "historical":
        changed = original.model_copy(update={"historical_image_lock_sha256": "6" * 64})
    elif field == "source":
        changed = original.model_copy(
            update={
                "images": (
                    image.model_copy(update={"source_reference": "example.invalid/other:3.0.0"}),
                )
            }
        )
    elif field == "image_id":
        changed_id = "sha256:" + "7" * 64
        changed = original.model_copy(
            update={
                "images": (
                    image.model_copy(
                        update={
                            "image_id": changed_id,
                            "resolved_platform_digest": changed_id,
                        }
                    ),
                )
            }
        )
    else:
        changed = original.model_copy(
            update={
                "images": (
                    image.model_copy(
                        update={"resolved_platform_digest": "sha256:" + "8" * 64}
                    ),
                )
            }
        )
    with pytest.raises(ImageAuthorityMismatch):
        ensure_image_authority(path, changed)


def test_compose_instance_varies_but_structure_is_stable(tmp_path: Path) -> None:
    private = tmp_path / "private"
    diagnostic_flagd = private / "runtime" / "probe-01" / "flagd"
    canonical_flagd = private / "runtime" / "invocation-a" / "flagd"

    diagnostic = compose_identities(
        _compose(diagnostic_flagd),
        private_root=private,
        flagd_directory=diagnostic_flagd,
    )
    canonical = compose_identities(
        _compose(canonical_flagd),
        private_root=private,
        flagd_directory=canonical_flagd,
    )

    assert diagnostic.instance_sha256 != canonical.instance_sha256
    assert diagnostic.structure_sha256 == canonical.structure_sha256
    assert diagnostic.normalized_bind_count == 2


@pytest.mark.parametrize("mutation", ("image", "network", "read_only", "target"))
def test_structural_compose_change_changes_identity(tmp_path: Path, mutation: str) -> None:
    private = tmp_path / "private"
    flagd = private / "runtime" / "probe-01" / "flagd"
    original = _compose(flagd)
    changed = json.loads(json.dumps(original))
    if mutation == "image":
        changed["services"]["flagd"]["image"] = "example.invalid/demo:changed"
    elif mutation == "network":
        changed["networks"]["default"]["name"] = "changed"
    elif mutation == "read_only":
        changed["services"]["flagd"]["volumes"][0]["read_only"] = False
    else:
        changed["services"]["flagd"]["volumes"][0]["target"] = "/changed"

    before = compose_identities(original, private_root=private, flagd_directory=flagd)
    after = compose_identities(changed, private_root=private, flagd_directory=flagd)
    assert before.structure_sha256 != after.structure_sha256


def test_unexpected_private_path_fails_closed(tmp_path: Path) -> None:
    private = tmp_path / "private"
    flagd = private / "runtime" / "probe-01" / "flagd"
    compose = _compose(flagd)
    compose["services"]["flagd"]["environment"] = {
        "UNEXPECTED": str(private / "other")
    }

    with pytest.raises(ComposeIdentityMismatch, match="unexpected private-root path"):
        compose_identities(compose, private_root=private, flagd_directory=flagd)


def test_non_bind_flagd_source_fails_closed(tmp_path: Path) -> None:
    private = tmp_path / "private"
    flagd = private / "runtime" / "probe-01" / "flagd"
    compose = _compose(flagd)
    compose["services"]["flagd"]["volumes"][0]["type"] = "volume"  # type: ignore[index]

    with pytest.raises(ComposeIdentityMismatch, match="unexpected private-root path"):
        compose_identities(compose, private_root=private, flagd_directory=flagd)


def test_three_run_verifications_are_independent_and_authority_is_stable(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    authority_path = private / "control" / "image-authority.json"
    inspection = _inspection()
    authority = ensure_image_authority(authority_path, inspection)
    before = authority_path.read_bytes()
    structure_hashes = set()
    paths = (
        private / "diagnostics" / "probe-01" / "image-verification.json",
        private / "canonical" / "invocation-a" / "image-verification.json",
        private / "live-run" / "invocation-b" / "image-verification.json",
    )
    for index, path in enumerate(paths, start=1):
        flagd = private / "runtime" / f"run-{index}" / "flagd"
        result = write_run_image_verification(
            path,
            run_id=f"run-{index}",
            run_kind=("DIAGNOSTIC_PROBE", "CANONICAL_INVOCATION_A", "INVOCATION_B")[index - 1],
            authority=authority,
            inspection=inspection,
            resolved_compose=_compose(flagd),
            private_root=private,
            flagd_directory=flagd,
        )
        structure_hashes.add(result.compose_structure_sha256)
    assert len(structure_hashes) == 1
    assert authority_path.read_bytes() == before
    assert all(path.is_file() for path in paths)


class _InspectRunner:
    def __init__(self, image: dict[str, object]) -> None:
        self.image = image
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments: tuple[str, ...], **_: object) -> CommandResult:
        self.calls.append(arguments)
        return CommandResult(arguments=arguments, stdout=json.dumps([self.image]), stderr="")


def _real_inspection_environment(
    tmp_path: Path,
    *,
    source: str = "example.invalid/demo:3.0.0",
    image_id: str = "sha256:" + "1" * 64,
    index_digest: str = "sha256:" + "2" * 64,
) -> tuple[SandboxEnvironment, ResolvedSandbox, Path]:
    lock_path = tmp_path / "config/phase0/image-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "allowed_source_references": [source],
                "images": [
                    {
                        "source_reference": source,
                        "image_id": image_id,
                        "image_index_digest": index_digest,
                        "resolved_platform_digest": image_id,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repository = source.rsplit(":", 1)[0]
    runner = _InspectRunner(
        {
            "Os": "linux",
            "Architecture": "arm64",
            "Id": image_id,
            "RepoDigests": [f"{repository}@{index_digest}"],
        }
    )
    environment = SandboxEnvironment(
        repository_root=tmp_path,
        bundle=load_bundle(Path("config/live-telemetry-controlled-remediation-v1")),
        flagd_directory=tmp_path / "private/flagd",
        runner=runner,  # type: ignore[arg-type]
    )
    resolved = ResolvedSandbox(
        compose_sha256="a" * 64,
        services=("flagd",),
        image_references=(source,),
        endpoints=LocalEndpoints(
            frontend="http://127.0.0.1:18080",
            flag_control="http://127.0.0.1:18080/feature/api",
            flag_evaluation="http://127.0.0.1:18016",
            prometheus="http://127.0.0.1:19090",
            opensearch="http://127.0.0.1:19200",
            jaeger="http://127.0.0.1:11686",
        ),
    )
    return environment, resolved, lock_path


def test_cached_image_inspection_is_side_effect_free_and_validates_all_identities(
    tmp_path: Path,
) -> None:
    environment, resolved, lock_path = _real_inspection_environment(tmp_path)

    inspection = environment.inspect_cached_images(resolved)

    assert inspection.historical_image_lock_sha256 == file_sha256(lock_path)
    assert inspection.images[0].image_index_digest == "sha256:" + "2" * 64
    assert not (tmp_path / "control").exists()


@pytest.mark.parametrize("mutation", ("source", "image_id", "index", "platform"))
def test_cached_image_inspection_mismatch_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    environment, resolved, lock_path = _real_inspection_environment(tmp_path)
    runner = environment.runner
    if mutation == "source":
        resolved = resolved.model_copy(update={"image_references": ("example.invalid/other:3.0.0",)})
    elif mutation == "image_id":
        runner.image["Id"] = "sha256:" + "5" * 64  # type: ignore[attr-defined]
    elif mutation == "index":
        runner.image["RepoDigests"] = ["example.invalid/demo@sha256:" + "6" * 64]  # type: ignore[attr-defined]
    else:
        payload = json.loads(lock_path.read_text())
        payload["images"][0]["resolved_platform_digest"] = "sha256:" + "7" * 64
        lock_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SandboxDriftError):
        environment.inspect_cached_images(resolved)
