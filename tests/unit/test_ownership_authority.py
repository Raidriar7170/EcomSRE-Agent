import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecomsre.environment import ownership_authority as authority
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipManifest,
)
from ecomsre.environment.ownership_authority import (
    AuthenticatedOwnershipContext,
    OwnershipAuthorityError,
    create_ownership_authority_artifacts,
    load_authenticated_ownership_context,
)
from ecomsre.evidence.hashes import canonical_json_sha256
from ecomsre.phase0.models import Outcome


RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
CREATED_AT = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


def _manifest(run_id: str = RUN_ID) -> OwnershipManifest:
    return OwnershipManifest(
        run_id=run_id,
        resources=(
            OwnedResource(
                kind="port",
                name="tcp:8080",
                resource_id="tcp:8080",
                labels={
                    PROJECT_LABEL: PROJECT_NAMESPACE,
                    RUN_LABEL: run_id,
                },
            ),
        ),
    )


def _paths(root: Path, run_id: str = RUN_ID) -> tuple[Path, Path, Path]:
    return (
        root / "observer-visible" / run_id / "resource-ownership.json",
        root / "evaluator-only" / run_id / "ownership-anchor.json",
        root / "evaluator-only" / run_id / ".ownership-anchor.key",
    )


def test_authenticated_context_factory_roundtrip_uses_fixed_paths(
    tmp_path: Path,
) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )

    context = load_authenticated_ownership_context(tmp_path, RUN_ID)

    manifest_path, anchor_path, key_path = _paths(tmp_path)
    assert context.run_id == RUN_ID
    assert context.project_name == PROJECT_NAMESPACE
    assert context.manifest == _manifest()
    assert manifest_path.is_file()
    assert anchor_path.is_file()
    assert key_path.is_file()
    assert key_path.stat().st_mode & 0o077 == 0


def test_authority_writer_fails_closed_if_parent_directory_is_swapped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_write = authority._write_immutable_bytes
    swapped = False

    def swap_parent_then_write(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            run_root = tmp_path / "evaluator-only" / RUN_ID
            displaced = tmp_path / "evaluator-only" / f"{RUN_ID}-displaced"
            run_root.rename(displaced)
            run_root.mkdir(mode=0o700)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(
        authority,
        "_write_immutable_bytes",
        swap_parent_then_write,
    )

    with pytest.raises(ValueError, match="capability root was replaced"):
        create_ownership_authority_artifacts(
            tmp_path,
            _manifest(),
            created_at=CREATED_AT,
        )

    replacement = tmp_path / "evaluator-only" / RUN_ID
    assert list(replacement.iterdir()) == []
    assert not (
        tmp_path / "observer-visible" / RUN_ID / "resource-ownership.json"
    ).exists()


def test_authenticated_empty_inventory_intent_roundtrip_and_tamper_rejection(
    tmp_path: Path,
) -> None:
    assert hasattr(authority, "OwnershipIntent")
    assert hasattr(authority, "AuthenticatedOwnershipIntent")
    assert hasattr(authority, "create_ownership_intent_artifacts")
    assert hasattr(authority, "load_authenticated_ownership_intent")
    intent = authority.OwnershipIntent(
        schema_version="phase0.ownership-intent.v1",
        run_id=RUN_ID,
        project_name=PROJECT_NAMESPACE,
        canonical_labels={
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: RUN_ID,
        },
        expected_compose_files=(
            "third_party/opentelemetry-demo/compose.yaml",
            "third_party/opentelemetry-demo/compose.observability.yaml",
            "config/phase0/compose.phase0.yaml",
        ),
        expected_compose_sha256="1" * 64,
        expected_image_sources=("example.test/demo:3.0.0-ad",),
        pull_policy="never",
        build_policy="no-build",
        resources=(),
        created_at=CREATED_AT,
    )

    paths = authority.create_ownership_intent_artifacts(tmp_path, intent)
    authenticated = authority.load_authenticated_ownership_intent(
        tmp_path,
        RUN_ID,
    )

    assert isinstance(authenticated, authority.AuthenticatedOwnershipIntent)
    assert authenticated.is_authentic()
    assert authenticated.intent == intent
    assert paths.intent_path.is_file()
    assert paths.anchor_path.is_file()
    assert paths.key_path.stat().st_mode & 0o077 == 0

    raw = json.loads(paths.intent_path.read_text(encoding="utf-8"))
    raw["expected_compose_sha256"] = "2" * 64
    paths.intent_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(OwnershipAuthorityError, match="intent"):
        authority.load_authenticated_ownership_intent(tmp_path, RUN_ID)


def test_ownership_intent_rejects_nonempty_inventory() -> None:
    assert hasattr(authority, "OwnershipIntent")
    with pytest.raises(ValueError, match="empty inventory"):
        authority.OwnershipIntent(
            schema_version="phase0.ownership-intent.v1",
            run_id=RUN_ID,
            project_name=PROJECT_NAMESPACE,
            canonical_labels={
                PROJECT_LABEL: PROJECT_NAMESPACE,
                RUN_LABEL: RUN_ID,
            },
            expected_compose_files=("compose.yaml",),
            expected_compose_sha256="1" * 64,
            expected_image_sources=("example.test/demo:3.0.0-ad",),
            pull_policy="never",
            build_policy="no-build",
            resources=_manifest().resources,
            created_at=CREATED_AT,
        )


def test_authenticated_context_cannot_be_directly_constructed() -> None:
    with pytest.raises(TypeError, match="loader"):
        AuthenticatedOwnershipContext(
            run_id=RUN_ID,
            project_name=PROJECT_NAMESPACE,
            canonical_labels={},
            manifest=_manifest(),
            manifest_sha256="0" * 64,
            created_at=CREATED_AT,
        )


def test_coordinated_manifest_and_anchor_replacement_fails_hmac(
    tmp_path: Path,
) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    manifest_path, anchor_path, _key_path = _paths(tmp_path)
    replacement = OwnershipManifest(run_id=RUN_ID, resources=())
    manifest_path.write_text(
        json.dumps(replacement.model_dump(mode="json")),
        encoding="utf-8",
    )
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor["manifest_sha256"] = canonical_json_sha256(
        replacement.model_dump(mode="json")
    )
    anchor_path.write_text(json.dumps(anchor), encoding="utf-8")

    with pytest.raises(OwnershipAuthorityError, match="authentication"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)


def test_forged_anchor_hmac_is_rejected(tmp_path: Path) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    _manifest_path, anchor_path, _key_path = _paths(tmp_path)
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor["hmac_sha256"] = "0" * 64
    anchor_path.write_text(json.dumps(anchor), encoding="utf-8")

    with pytest.raises(OwnershipAuthorityError, match="authentication"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)


def test_cross_run_anchor_is_rejected(tmp_path: Path) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    source_paths = _paths(tmp_path)
    target_paths = _paths(tmp_path, OTHER_RUN_ID)
    target_paths[0].parent.mkdir(parents=True)
    target_paths[1].parent.mkdir(parents=True)
    os.chmod(target_paths[0].parent, 0o700)
    os.chmod(target_paths[1].parent, 0o700)
    for source, target in zip(source_paths, target_paths, strict=True):
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)

    with pytest.raises(OwnershipAuthorityError, match="run"):
        load_authenticated_ownership_context(tmp_path, OTHER_RUN_ID)


def test_symlink_or_missing_key_is_rejected(tmp_path: Path) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    manifest_path, _anchor_path, key_path = _paths(tmp_path)
    real_manifest = tmp_path / "replacement.json"
    shutil.copyfile(manifest_path, real_manifest)
    manifest_path.unlink()
    manifest_path.symlink_to(real_manifest)

    with pytest.raises(OwnershipAuthorityError, match="symlink"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)

    manifest_path.unlink()
    shutil.copyfile(real_manifest, manifest_path)
    os.chmod(manifest_path, 0o600)
    key_path.unlink()
    with pytest.raises(OwnershipAuthorityError, match="missing") as error:
        load_authenticated_ownership_context(tmp_path, RUN_ID)
    assert error.value.outcome is Outcome.UNSAFE
    assert error.value.exit_code == 40
    assert error.value.reason_code == "RESOURCE_OWNERSHIP_UNKNOWN"


def test_insecure_key_permissions_are_rejected(tmp_path: Path) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    _manifest_path, _anchor_path, key_path = _paths(tmp_path)
    os.chmod(key_path, 0o644)

    with pytest.raises(OwnershipAuthorityError, match="permissions"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)

    os.chmod(key_path, 0o700)
    with pytest.raises(OwnershipAuthorityError, match="permissions"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)

    os.chmod(key_path, 0o600)
    os.chmod(key_path.parent, 0o775)
    with pytest.raises(OwnershipAuthorityError, match="permissions"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)


def test_world_writable_trust_root_is_rejected(tmp_path: Path) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    os.chmod(tmp_path, 0o777)

    with pytest.raises(OwnershipAuthorityError, match="permissions"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)


def test_world_writable_intermediate_ancestor_is_rejected(
    tmp_path: Path,
) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    manifest_path, _anchor_path, _key_path = _paths(tmp_path)
    os.chmod(manifest_path.parent.parent, 0o777)

    with pytest.raises(OwnershipAuthorityError, match="permissions"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)


def test_wrong_owner_anywhere_in_trust_chain_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    create_ownership_authority_artifacts(
        tmp_path,
        _manifest(),
        created_at=CREATED_AT,
    )
    actual_uid = os.geteuid()
    monkeypatch.setattr(
        "ecomsre.environment.ownership_authority.os.geteuid",
        lambda: actual_uid + 1,
    )

    with pytest.raises(OwnershipAuthorityError, match="owner"):
        load_authenticated_ownership_context(tmp_path, RUN_ID)


def test_port_manifest_rejects_arbitrary_name_or_ambiguous_identity() -> None:
    labels = {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: RUN_ID,
    }
    with pytest.raises(ValueError, match="inconsistent"):
        OwnershipManifest(
            run_id=RUN_ID,
            resources=(
                OwnedResource(
                    kind="port",
                    name="arbitrary",
                    resource_id="tcp:8080",
                    labels=labels,
                ),
            ),
        )
    with pytest.raises(ValueError, match="inconsistent"):
        OwnershipManifest(
            run_id=RUN_ID,
            resources=(
                OwnedResource(
                    kind="port",
                    name="tcp:8080",
                    resource_id="tcp:8080",
                    labels=labels,
                ),
                OwnedResource(
                    kind="port",
                    name="tcp:8080-alias",
                    resource_id="tcp:8080",
                    labels=labels,
                ),
            ),
        )
