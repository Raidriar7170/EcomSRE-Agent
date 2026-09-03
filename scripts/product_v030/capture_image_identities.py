"""Read the three explicitly authorized registry/local identities; never pull."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre_live_sandbox.contracts import write_private_json
from ecomsre_live_sandbox.environment import ExactCommandRunner, SandboxDriftError
from ecomsre_live_sandbox.product_v030 import (
    FULL_IMAGES_V030,
    full_mode_image_from_registry_v030,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    runner = ExactCommandRunner()
    images = []
    proofs = []
    for reference in FULL_IMAGES_V030:
        descriptor = json.loads(
            runner.run(
                (
                    "docker",
                    "buildx",
                    "imagetools",
                    "inspect",
                    reference,
                    "--format",
                    "{{json .Manifest}}",
                ),
                cwd=root,
            ).stdout
        )
        matches = [
            item
            for item in descriptor["manifests"]
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture") == "arm64"
        ]
        if len(matches) != 1:
            raise SandboxDriftError(
                "registry must supply exactly one linux/arm64 manifest"
            )
        raw_manifest = runner.run(
            (
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                f"{reference.rsplit(':', 1)[0]}@{matches[0]['digest']}",
                "--raw",
            ),
            cwd=root,
        ).stdout
        cached = json.loads(
            runner.run(
                (
                    "docker",
                    "image",
                    "inspect",
                    "--platform",
                    "linux/arm64",
                    reference,
                ),
                cwd=root,
            ).stdout
        )
        if not isinstance(cached, list) or len(cached) != 1:
            raise SandboxDriftError("local image identity is not unique")
        image = full_mode_image_from_registry_v030(
            reference=reference,
            descriptor=descriptor,
            platform_manifest_raw=raw_manifest,
            cached=cached[0],
        )
        images.append(image.model_dump(mode="json"))
        proofs.append(
            {
                "reference": reference,
                "descriptor": descriptor,
                "platform_manifest_raw": raw_manifest,
                "cached": cached[0],
            }
        )
        print(
            json.dumps({"source_reference": reference, "identity_verified": True}),
            flush=True,
        )
    digest = write_private_json(
        args.output,
        {
            "schema_version": "product-v030.acquired-image-identities.v1",
            "authorization": "USER_GOAL_STANDING_AUTHORIZATION_DEC_061",
            "images": images,
            "registry_proofs": proofs,
        },
        create_once=True,
    )
    print(
        json.dumps({"acquired_image_count": len(images), "artifact_sha256": digest}),
        flush=True,
    )


if __name__ == "__main__":
    main()
