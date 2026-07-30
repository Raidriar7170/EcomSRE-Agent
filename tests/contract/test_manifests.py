import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ecomsre.environment.manifests import (
    EXPECTED_PLATFORM,
    UPSTREAM_COMMIT,
    UPSTREAM_TAG,
    ImageLockStatus,
    InspectedImage,
    LockMatchChecks,
    LockVerification,
    ResolvedComposeConfig,
    acceptance_compose_arguments,
    generate_candidate_image_lock,
    verify_acceptance_image_lock,
    write_candidate_image_lock,
)
from ecomsre.phase0.models import Outcome
from ecomsre.evidence.hashes import sha256_bytes


COMPOSE_CONTENT = json.dumps(
    {"services": {"adservice": {"image": "otel/demo:3.0.0-adservice"}}},
    sort_keys=True,
)
COMPOSE_HASH = sha256_bytes(COMPOSE_CONTENT.encode())
INDEX_DIGEST = "sha256:" + "d" * 64
PLATFORM_DIGEST = "sha256:" + "e" * 64
SOURCE_REFERENCES = ("otel/demo:3.0.0-adservice",)


def _resolved_compose() -> ResolvedComposeConfig:
    return ResolvedComposeConfig.from_stdout(COMPOSE_CONTENT)


def _image(**overrides) -> InspectedImage:
    values = {
        "logical_name": "adservice",
        "source_reference": "otel/demo:3.0.0-adservice",
        "image_index_digest": INDEX_DIGEST,
        "resolved_platform_digest": PLATFORM_DIGEST,
        "architecture": "arm64",
        "platform": "linux/arm64",
        "image_id": "sha256:" + "f" * 64,
    }
    values.update(overrides)
    return InspectedImage(**values)


def test_frozen_upstream_and_platform_constants_match_decisions() -> None:
    assert UPSTREAM_TAG == "3.0.0"
    assert UPSTREAM_COMMIT == "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    assert EXPECTED_PLATFORM == "linux/arm64"


def test_bootstrap_generates_candidate_lock_only_from_inspected_metadata() -> None:
    acquired_at = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)

    lock = generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=_resolved_compose(),
        acquired_at=acquired_at,
    )

    assert lock.status is ImageLockStatus.LOCKED
    assert lock.images[0].image_index_digest == INDEX_DIGEST
    assert lock.images[0].resolved_platform_digest == PLATFORM_DIGEST
    assert lock.images[0].acquired_at == acquired_at
    assert lock.images[0].upstream_commit == UPSTREAM_COMMIT
    assert lock.images[0].compose_config_sha256 == COMPOSE_HASH
    assert lock.allowed_source_references == SOURCE_REFERENCES


def test_locked_manifest_requires_utc_and_one_acquisition_timestamp() -> None:
    acquired_at = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)
    lock = generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=_resolved_compose(),
        acquired_at=acquired_at,
    )

    with pytest.raises(ValidationError, match="UTC"):
        generate_candidate_image_lock(
            images=(_image(),),
            resolved_compose=_resolved_compose(),
            acquired_at=datetime(2026, 7, 30, 2, 0),
        )
    with pytest.raises(ValidationError, match="acquisition"):
        type(lock).model_validate(
            {
                **lock.model_dump(mode="python"),
                "images": (
                    {
                        **lock.images[0].model_dump(mode="python"),
                        "acquired_at": acquired_at.replace(hour=3),
                    },
                ),
            }
        )


def test_candidate_lock_publish_is_exclusive_atomic_and_leaves_no_temp(
    tmp_path,
) -> None:
    lock = generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=_resolved_compose(),
        acquired_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
    )
    target = tmp_path / "image-lock.json"

    write_candidate_image_lock(target, lock)

    assert type(lock).model_validate_json(target.read_text()) == lock
    assert not list(tmp_path.glob(".*.tmp"))
    with pytest.raises(FileExistsError):
        write_candidate_image_lock(target, lock)


def test_acceptance_verifies_candidate_immutably_against_cached_image() -> None:
    lock = generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=_resolved_compose(),
        acquired_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
    )
    before = lock.model_dump_json()

    result = verify_acceptance_image_lock(
        lock,
        cached_images=(_image(),),
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_compose_config_sha256=COMPOSE_HASH,
    )

    assert result.passed is True
    assert result.outcome is Outcome.SUCCESS
    assert result.checks.all_matched is True
    assert lock.model_dump_json() == before


def test_digest_or_platform_mismatch_blocks_acceptance() -> None:
    lock = generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=_resolved_compose(),
        acquired_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
    )

    result = verify_acceptance_image_lock(
        lock,
        cached_images=(
            _image(
                resolved_platform_digest="sha256:" + "0" * 64,
                platform="linux/amd64",
                architecture="amd64",
            ),
        ),
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_compose_config_sha256=COMPOSE_HASH,
    )

    assert result.passed is False
    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert "ARM64_DIGEST_MISMATCH" in result.reason_codes
    assert "PLATFORM_MISMATCH" in result.reason_codes


def test_acceptance_compose_arguments_always_use_pull_never() -> None:
    arguments = acceptance_compose_arguments("unix:///var/run/docker.sock")

    assert arguments[-5:] == (
        "up",
        "--detach",
        "--pull",
        "never",
        "--no-build",
    )
    assert _option_values(arguments, "--project-name") == ["ecomsre-phase0"]
    assert _option_values(arguments, "--file") == [
        "third_party/opentelemetry-demo/compose.yaml",
        "third_party/opentelemetry-demo/compose.observability.yaml",
        "config/phase0/compose.phase0.yaml",
    ]
    assert _option_values(arguments, "--env-file") == [
        "third_party/opentelemetry-demo/.env"
    ]
    assert "latest" not in arguments


def _option_values(arguments: tuple[str, ...], option: str) -> list[str]:
    return [
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == option
    ]


@pytest.mark.parametrize(
    "source_reference",
    [
        "otel/demo",
        "otel/demo:latest",
        "otel/demo:main",
        "otel/demo:nightly",
        "otel/demo:",
        "otel/demo@sha256:" + "z" * 64,
    ],
)
def test_image_metadata_rejects_floating_source_references(
    source_reference: str,
) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        _image(source_reference=source_reference)


def test_acceptance_rejects_source_reference_mismatch() -> None:
    lock = generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=_resolved_compose(),
        acquired_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
    )

    result = verify_acceptance_image_lock(
        lock,
        cached_images=(_image(source_reference="other/demo:3.0.0-adservice"),),
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_compose_config_sha256=COMPOSE_HASH,
    )

    assert result.passed is False
    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert result.reason_codes == ("SOURCE_REFERENCE_MISMATCH",)


def test_candidate_lock_rejects_duplicate_logical_images() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        generate_candidate_image_lock(
            images=(_image(), _image()),
            resolved_compose=_resolved_compose(),
            acquired_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
        )


def test_acceptance_rejects_duplicate_cached_logical_images() -> None:
    lock = generate_candidate_image_lock(
        images=(_image(),),
        resolved_compose=_resolved_compose(),
        acquired_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
    )

    result = verify_acceptance_image_lock(
        lock,
        cached_images=(_image(), _image()),
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_compose_config_sha256=COMPOSE_HASH,
    )

    assert result.passed is False
    assert result.reason_codes == ("DUPLICATE_CACHED_IMAGE",)


def test_candidate_rejects_numeric_tag_not_present_in_resolved_compose() -> None:
    with pytest.raises(ValueError, match="resolved Compose"):
        generate_candidate_image_lock(
            images=(_image(source_reference="other/demo:3.0.0-adservice"),),
            resolved_compose=_resolved_compose(),
            acquired_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
        )


def test_lock_verification_cross_fields_reject_inconsistent_success() -> None:
    with pytest.raises(ValidationError, match="successful"):
        LockVerification(
            passed=True,
            outcome=Outcome.SUCCESS,
            reason_codes=(),
            checks=LockMatchChecks(
                source_references=False,
                digests=True,
                platforms=True,
                image_ids=True,
                upstream_binding=True,
                compose_binding=True,
                complete_inventory=True,
            ),
        )


def test_lock_verification_cross_fields_reject_inconsistent_failure() -> None:
    with pytest.raises(ValidationError, match="failed"):
        LockVerification(
            passed=False,
            outcome=Outcome.SUCCESS,
            reason_codes=(),
            checks=LockMatchChecks.all_passed(),
        )
