import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest

from ecomsre.environment.manifests import (
    COMPOSE_CANONICALIZATION_SCHEMA_VERSION,
    UPSTREAM_COMMIT,
    UPSTREAM_TAG,
    ImageLockManifest,
    InspectedImage,
    ResolvedComposeConfig,
    canonicalize_compose_contract,
    compose_canonicalization_schema,
    generate_candidate_image_lock,
    rotate_candidate_image_lock,
    verify_acceptance_image_lock,
)
from ecomsre.phase0.models import Outcome


RUN_A = "1" * 32
RUN_B = "2" * 32


def _compose_payload(run_id: str) -> dict[str, object]:
    labels = {
        "io.ecomsre.project": "ecomsre-phase0",
        "io.ecomsre.run": run_id,
    }
    return {
        "name": "ecomsre-phase0",
        "networks": {
            "default": {
                "name": "ecomsre-phase0",
                "labels": copy.deepcopy(labels),
            }
        },
        "services": {
            "frontend-proxy": {
                "image": "ghcr.io/open-telemetry/demo:3.0.0-frontend-proxy",
                "platform": "linux/arm64",
                "pull_policy": "never",
                "labels": copy.deepcopy(labels),
                "ports": [
                    {
                        "host_ip": "127.0.0.1",
                        "published": "8080",
                        "target": 8080,
                        "protocol": "tcp",
                    }
                ],
            },
            "flagd": {
                "image": "ghcr.io/open-feature/flagd:v0.16.0",
                "platform": "linux/arm64",
                "pull_policy": "never",
                "labels": copy.deepcopy(labels),
                "command": ["start", "--uri", "file:./etc/flagd/demo.flagd.json"],
                "environment": {"OTEL_SERVICE_NAME": "flagd"},
                "volumes": [
                    {
                        "type": "bind",
                        "source": (
                            "/repo/artifacts/phase0/evaluator-only/"
                            f"{run_id}/control"
                        ),
                        "target": "/etc/flagd",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": "/repo/config/phase0/static.yml",
                        "target": "/etc/static.yml",
                        "read_only": True,
                    },
                    {
                        "type": "volume",
                        "source": "prometheus-data",
                        "target": "/prometheus",
                    },
                ],
            },
        },
        "volumes": {
            "prometheus-data": {
                "name": f"ecomsre-phase0-{run_id}-prometheus-data",
                "labels": copy.deepcopy(labels),
            }
        },
        "x-phase0-labels": copy.deepcopy(labels),
        "x-phase0-service": {
            "labels": copy.deepcopy(labels),
            "platform": "linux/arm64",
            "pull_policy": "never",
        },
    }


def _resolved(payload: dict[str, object]) -> ResolvedComposeConfig:
    return ResolvedComposeConfig.from_stdout(
        json.dumps(payload, indent=2, sort_keys=True)
    )


def test_run_scoped_identity_changes_only_runtime_instance_hash() -> None:
    first = _resolved(_compose_payload(RUN_A))
    second = _resolved(_compose_payload(RUN_B))

    assert first.canonicalization_schema_version == (
        COMPOSE_CANONICALIZATION_SCHEMA_VERSION
    )
    assert first.canonical_compose_contract_sha256 == (
        second.canonical_compose_contract_sha256
    )
    assert first.runtime_compose_instance_sha256 != (
        second.runtime_compose_instance_sha256
    )


def test_schema_records_the_only_runtime_identity_and_exact_selectors() -> None:
    schema = compose_canonicalization_schema()

    assert schema["schema_version"] == COMPOSE_CANONICALIZATION_SCHEMA_VERSION
    assert schema["runtime_identity"] == {
        "name": "ECOMSRE_RUN_ID",
        "format": "lowercase-hex-32",
        "canonical_token": "<ECOMSRE_RUN_ID>",
    }
    assert schema["selectors"] == [
        "services.*.labels.io.ecomsre.run",
        "networks.*.labels.io.ecomsre.run",
        "volumes.*.labels.io.ecomsre.run",
        "x-phase0-labels.io.ecomsre.run",
        "x-phase0-service.labels.io.ecomsre.run",
        "volumes.<logical-name>.name",
        "services.*.volumes[type=bind].source:"
        "artifacts/phase0/evaluator-only/<run-id>/...",
    ]


def test_canonicalization_projection_is_idempotent() -> None:
    source = _compose_payload(RUN_A)
    original = copy.deepcopy(source)
    projected = canonicalize_compose_contract(source)

    assert source == original
    assert canonicalize_compose_contract(projected) == projected


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["services"]["flagd"].__setitem__(
            "image", "ghcr.io/open-feature/flagd:v0.17.0"
        ),
        lambda payload: payload["services"]["frontend-proxy"]["ports"][0].__setitem__(
            "published", "18080"
        ),
        lambda payload: payload["services"]["flagd"].__setitem__(
            "platform", "linux/amd64"
        ),
        lambda payload: payload["services"]["flagd"]["volumes"][0].__setitem__(
            "type", "tmpfs"
        ),
        lambda payload: payload["services"]["flagd"]["volumes"][0].__setitem__(
            "target", "/etc/flagd-v2"
        ),
        lambda payload: payload["services"]["flagd"]["volumes"][1].__setitem__(
            "source", "/repo/config/phase0/static-v2.yml"
        ),
        lambda payload: payload["services"]["flagd"].__setitem__(
            "command", ["start", "--uri", "file:./etc/flagd/changed.json"]
        ),
        lambda payload: payload["services"]["flagd"].__setitem__(
            "pull_policy", "always"
        ),
        lambda payload: payload["services"]["flagd"].__setitem__(
            "build", {"context": "/repo/changed"}
        ),
        lambda payload: payload["services"]["flagd"]["environment"].__setitem__(
            "OTEL_SERVICE_NAME", "changed"
        ),
        lambda payload: payload["services"].__setitem__(
            "new-service",
            {
                "image": "registry.example/new-service:1.0.0",
                "platform": "linux/arm64",
            },
        ),
    ],
    ids=(
        "image",
        "port",
        "platform",
        "mount-type",
        "mount-target",
        "config-bind-source",
        "command",
        "pull-policy",
        "build-policy",
        "environment",
        "service",
    ),
)
def test_material_compose_changes_change_canonical_contract_hash(mutation) -> None:
    baseline_payload = _compose_payload(RUN_A)
    changed_payload = copy.deepcopy(baseline_payload)
    mutation(changed_payload)

    assert _resolved(baseline_payload).canonical_compose_contract_sha256 != (
        _resolved(changed_payload).canonical_compose_contract_sha256
    )


def test_non_run_scoped_fields_are_never_normalized() -> None:
    baseline_payload = _compose_payload(RUN_A)
    changed_payload = copy.deepcopy(baseline_payload)
    changed_payload["services"]["flagd"]["environment"]["RUNTIME_NOTE"] = RUN_B
    changed_payload["services"]["flagd"]["volumes"][1]["source"] = (
        f"/repo/config/{RUN_B}/static.yml"
    )

    assert _resolved(baseline_payload).canonical_compose_contract_sha256 != (
        _resolved(changed_payload).canonical_compose_contract_sha256
    )


def test_inconsistent_runtime_identity_fails_closed() -> None:
    payload = _compose_payload(RUN_A)
    payload["networks"]["default"]["labels"]["io.ecomsre.run"] = RUN_B

    with pytest.raises(ValueError, match="runtime identity"):
        _resolved(payload)


def _inspected_image() -> InspectedImage:
    return InspectedImage(
        logical_name="frontend-proxy",
        source_reference=(
            "ghcr.io/open-telemetry/demo:3.0.0-frontend-proxy"
        ),
        image_index_digest="sha256:" + "3" * 64,
        resolved_platform_digest="sha256:" + "4" * 64,
        architecture="arm64",
        platform="linux/arm64",
        image_id="sha256:" + "5" * 64,
    )


def test_v2_image_lock_binds_only_canonical_contract() -> None:
    resolved = _resolved(
        {
            "services": {
                "frontend-proxy": {
                    "image": _inspected_image().source_reference,
                }
            }
        }
    )
    lock = generate_candidate_image_lock(
        images=(_inspected_image(),),
        resolved_compose=resolved,
        acquired_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    assert lock.schema_version == "phase0.image-lock.v2"
    assert lock.canonical_compose_contract_sha256 == (
        resolved.canonical_compose_contract_sha256
    )
    assert lock.compose_canonicalization_schema_version == (
        COMPOSE_CANONICALIZATION_SCHEMA_VERSION
    )
    assert lock.compose_config_sha256 is None
    assert lock.images[0].canonical_compose_contract_sha256 == (
        lock.canonical_compose_contract_sha256
    )
    assert lock.images[0].compose_config_sha256 is None


def test_v1_image_lock_requires_explicit_canonical_migration() -> None:
    legacy_hash = "a" * 64
    acquired_at = datetime(2026, 7, 31, tzinfo=UTC)
    image = _inspected_image()
    lock = ImageLockManifest.model_validate(
        {
            "schema_version": "phase0.image-lock.v1",
            "status": "LOCKED",
            "upstream_tag": UPSTREAM_TAG,
            "upstream_commit": UPSTREAM_COMMIT,
            "compose_config_sha256": legacy_hash,
            "created_at": acquired_at,
            "allowed_source_references": [image.source_reference],
            "images": [
                {
                    **image.model_dump(mode="python"),
                    "acquired_at": acquired_at,
                    "upstream_commit": UPSTREAM_COMMIT,
                    "compose_config_sha256": legacy_hash,
                }
            ],
        }
    )

    verification = verify_acceptance_image_lock(
        lock,
        cached_images=(image,),
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_canonical_compose_contract_sha256="b" * 64,
        observed_canonicalization_schema_version=(
            COMPOSE_CANONICALIZATION_SCHEMA_VERSION
        ),
    )

    assert verification.passed is False
    assert verification.outcome is Outcome.BLOCKED_UPSTREAM
    assert verification.reason_codes == (
        "IMAGE_LOCK_CANONICALIZATION_REQUIRED",
    )


def test_v2_lock_verifies_across_run_ids_but_preserves_instance_identity() -> None:
    image = _inspected_image()

    def runtime_payload(run_id: str) -> dict[str, object]:
        return {
            "services": {
                "frontend-proxy": {
                    "image": image.source_reference,
                    "labels": {
                        "io.ecomsre.project": "ecomsre-phase0",
                        "io.ecomsre.run": run_id,
                    },
                }
            }
        }

    first = _resolved(runtime_payload(RUN_A))
    second = _resolved(runtime_payload(RUN_B))
    lock = generate_candidate_image_lock(
        images=(image,),
        resolved_compose=first,
        acquired_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    verification = verify_acceptance_image_lock(
        lock,
        cached_images=(image,),
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_canonical_compose_contract_sha256=(
            second.canonical_compose_contract_sha256
        ),
        observed_canonicalization_schema_version=(
            second.canonicalization_schema_version
        ),
    )

    assert first.runtime_compose_instance_sha256 != (
        second.runtime_compose_instance_sha256
    )
    assert verification.passed is True


def test_legacy_migration_preserves_exact_v1_lock_history(tmp_path) -> None:
    image = _inspected_image()

    def runtime_payload(run_id: str) -> dict[str, object]:
        return {
            "services": {
                "frontend-proxy": {
                    "image": image.source_reference,
                    "labels": {
                        "io.ecomsre.project": "ecomsre-phase0",
                        "io.ecomsre.run": run_id,
                    },
                }
            }
        }

    old_resolved = _resolved(runtime_payload(RUN_A))
    new_resolved = _resolved(runtime_payload(RUN_B))
    acquired_at = datetime(2026, 7, 31, tzinfo=UTC)
    legacy = ImageLockManifest.model_validate(
        {
            "schema_version": "phase0.image-lock.v1",
            "status": "LOCKED",
            "upstream_tag": UPSTREAM_TAG,
            "upstream_commit": UPSTREAM_COMMIT,
            "compose_config_sha256": (
                old_resolved.runtime_compose_instance_sha256
            ),
            "created_at": acquired_at,
            "allowed_source_references": [image.source_reference],
            "images": [
                {
                    **image.model_dump(mode="python"),
                    "acquired_at": acquired_at,
                    "upstream_commit": UPSTREAM_COMMIT,
                    "compose_config_sha256": (
                        old_resolved.runtime_compose_instance_sha256
                    ),
                }
            ],
        }
    )
    lock_path = tmp_path / "image-lock.json"
    legacy_bytes = (
        json.dumps(
            legacy.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    lock_path.write_bytes(legacy_bytes)
    old_lock_sha256 = hashlib.sha256(legacy_bytes).hexdigest()

    result = rotate_candidate_image_lock(
        path=lock_path,
        resolved_compose=new_resolved,
        cached_images=(image,),
        expected_old_lock_sha256=old_lock_sha256,
        rotation_reason="RUN_INVARIANT_COMPOSE_CONTRACT_MIGRATION",
        rotated_at=datetime(2026, 7, 31, 0, 1, tzinfo=UTC),
    )

    history = (
        tmp_path
        / "image-lock-history"
        / f"{old_lock_sha256}.json"
    )
    assert history.read_bytes() == legacy_bytes
    assert result.lock.schema_version == "phase0.image-lock.v2"
    assert result.lock.canonical_compose_contract_sha256 == (
        new_resolved.canonical_compose_contract_sha256
    )
    assert result.evidence.old_compose_binding_kind == (
        "runtime_compose_instance_sha256"
    )
    assert result.evidence.runtime_compose_instance_sha256 == (
        new_resolved.runtime_compose_instance_sha256
    )
