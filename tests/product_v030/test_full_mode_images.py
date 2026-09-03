from copy import deepcopy
import hashlib
import json

import pytest

from ecomsre_live_sandbox.environment import SandboxDriftError
from ecomsre_live_sandbox.product_v030 import (
    FULL_IMAGES_V030,
    full_mode_image_from_registry_v030,
)


def _inputs():
    config_digest = "sha256:" + "c" * 64
    raw = json.dumps({"schemaVersion": 2, "config": {"digest": config_digest}})
    platform_digest = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
    index_digest = "sha256:" + "a" * 64
    return {
        "reference": FULL_IMAGES_V030[0],
        "descriptor": {
            "digest": index_digest,
            "manifests": [
                {
                    "digest": platform_digest,
                    "platform": {
                        "os": "linux",
                        "architecture": "arm64",
                    },
                }
            ],
        },
        "platform_manifest_raw": raw,
        "cached": {
            "Id": config_digest,
            "Os": "linux",
            "Architecture": "arm64",
            "RepoDigests": [f"ghcr.io/open-telemetry/demo@{index_digest}"],
        },
    }


@pytest.mark.parametrize("cli_newline", [False, True])
def test_registry_platform_bytes_bind_local_image_id(cli_newline):
    inputs = _inputs()
    if cli_newline:
        inputs["platform_manifest_raw"] += "\n"
    image = full_mode_image_from_registry_v030(**inputs)
    assert image.image_id == inputs["cached"]["Id"]
    assert image.image_index_digest == inputs["descriptor"]["digest"]
    assert (
        image.resolved_platform_digest == inputs["descriptor"]["manifests"][0]["digest"]
    )


def test_containerd_image_id_is_the_verified_platform_manifest_not_config():
    inputs = _inputs()
    platform = inputs["descriptor"]["manifests"][0]
    inputs["cached"]["Id"] = platform["digest"]
    inputs["cached"]["Descriptor"] = deepcopy(platform)
    image = full_mode_image_from_registry_v030(**inputs)
    assert image.image_id == image.resolved_platform_digest


def test_containerd_descriptor_must_match_the_verified_manifest():
    inputs = _inputs()
    inputs["cached"]["Id"] = inputs["descriptor"]["manifests"][0]["digest"]
    inputs["cached"]["Descriptor"] = {"digest": "sha256:" + "e" * 64}
    with pytest.raises(SandboxDriftError):
        full_mode_image_from_registry_v030(**inputs)


@pytest.mark.parametrize(
    "drift",
    [
        "foreign_reference",
        "manifest_bytes",
        "wrong_config",
        "wrong_os",
        "wrong_architecture",
        "wrong_index",
        "malformed_repo_digests",
        "duplicate_platform",
        "missing_platform",
        "malformed_platform",
        "malformed_manifest",
        "missing_index_digest",
    ],
)
def test_acquired_images_fail_closed_on_identity_drift(drift):
    inputs = deepcopy(_inputs())
    descriptor = inputs["descriptor"]
    cached = inputs["cached"]
    if drift == "foreign_reference":
        inputs["reference"] = "ghcr.io/open-telemetry/demo:latest"
    elif drift == "manifest_bytes":
        inputs["platform_manifest_raw"] += " "
    elif drift == "wrong_config":
        cached["Id"] = "sha256:" + "d" * 64
    elif drift == "wrong_os":
        cached["Os"] = "windows"
    elif drift == "wrong_architecture":
        cached["Architecture"] = "amd64"
    elif drift == "wrong_index":
        cached["RepoDigests"] = ["ghcr.io/open-telemetry/demo@sha256:" + "e" * 64]
    elif drift == "malformed_repo_digests":
        cached["RepoDigests"] = None
    elif drift == "duplicate_platform":
        descriptor["manifests"] *= 2
    elif drift == "missing_platform":
        descriptor["manifests"] = []
    elif drift == "malformed_platform":
        descriptor["manifests"][0]["platform"] = []
    elif drift == "malformed_manifest":
        inputs["platform_manifest_raw"] = "not-json"
    elif drift == "missing_index_digest":
        descriptor.pop("digest")
    with pytest.raises(SandboxDriftError):
        full_mode_image_from_registry_v030(**inputs)
