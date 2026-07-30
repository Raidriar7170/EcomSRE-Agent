import pytest

from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnershipError,
    OwnershipManifest,
    OwnedResource,
    verify_owned_resources,
)


RUN_ID = "a" * 32


def _resource(
    name: str = "ecomsre-phase0-adservice-1",
    *,
    labels: dict[str, str] | None = None,
) -> OwnedResource:
    return OwnedResource(
        kind="container",
        name=name,
        resource_id=f"id-{name}",
        labels=labels
        or {
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: RUN_ID,
        },
    )


def test_fixed_namespace_and_labels_prove_resource_ownership() -> None:
    resource = _resource()
    manifest = OwnershipManifest(run_id=RUN_ID, resources=(resource,))

    verified = verify_owned_resources((resource,), manifest)

    assert manifest.namespace == "ecomsre-phase0"
    assert verified == (resource,)


def test_unknown_discovered_resource_fails_closed() -> None:
    owned = _resource()
    unknown = _resource(name="ecomsre-phase0-unknown-1")
    manifest = OwnershipManifest(run_id=RUN_ID, resources=(owned,))

    with pytest.raises(OwnershipError, match="RESOURCE_OWNERSHIP_UNKNOWN"):
        verify_owned_resources((owned, unknown), manifest)


def test_duplicate_discovered_identity_fails_closed() -> None:
    resource = _resource()
    manifest = OwnershipManifest(run_id=RUN_ID, resources=(resource,))

    with pytest.raises(OwnershipError, match="duplicate discovered"):
        verify_owned_resources((resource, resource), manifest)


def test_missing_or_conflicting_labels_fail_closed() -> None:
    manifest_resource = _resource()
    discovered = _resource(
        labels={
            PROJECT_LABEL: "another-project",
            RUN_LABEL: RUN_ID,
        }
    )
    manifest = OwnershipManifest(run_id=RUN_ID, resources=(manifest_resource,))

    with pytest.raises(OwnershipError, match="RESOURCE_OWNERSHIP_UNKNOWN"):
        verify_owned_resources((discovered,), manifest)


def test_manifest_cannot_claim_a_different_project_namespace() -> None:
    with pytest.raises(ValueError, match="ecomsre-phase0"):
        OwnershipManifest(
            namespace="another-project",
            run_id=RUN_ID,
            resources=(_resource(),),
        )
