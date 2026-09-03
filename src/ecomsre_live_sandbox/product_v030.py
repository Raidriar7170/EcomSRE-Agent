"""Goal-scoped full-mode delta over the owned Product v0.2.4 sandbox."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Mapping

from ecomsre_live_sandbox.contracts import (
    ConfigBundle,
    ResolvedSandbox,
    write_private_json,
)
from ecomsre_live_sandbox.environment import (
    EXPECTED_SERVICES,
    ExactCommandRunner,
    ResourceOwnershipError,
    SandboxDriftError,
)
from ecomsre_live_sandbox.image_authority import CachedImage, CachedImageInspection
from ecomsre_live_sandbox.product_v024 import (
    ProductV024SandboxEnvironment,
    build_product_v024_runtime_bundle,
)


FULL_SERVICES_V030 = ("accounting", "fraud-detection", "kafka")
FULL_IMAGES_V030 = tuple(
    f"ghcr.io/open-telemetry/demo:3.0.0-{name}" for name in FULL_SERVICES_V030
)


def full_mode_image_from_registry_v030(
    *,
    reference: str,
    descriptor: Mapping[str, object],
    platform_manifest_raw: str,
    cached: Mapping[str, object],
) -> CachedImage:
    """Bind one exact registry ARM64 manifest to the acquired local config ID."""
    manifests = descriptor.get("manifests")
    matches = (
        [
            item
            for item in manifests
            if isinstance(item, dict)
            and isinstance(item.get("platform"), dict)
            and item["platform"].get("os") == "linux"
            and item["platform"].get("architecture") == "arm64"
        ]
        if isinstance(manifests, list)
        else []
    )
    if reference not in FULL_IMAGES_V030 or len(matches) != 1:
        raise SandboxDriftError(
            "Product v0.3 requires one exact linux/arm64 registry manifest"
        )
    raw = platform_manifest_raw.encode()
    expected_digest = matches[0].get("digest")
    # buildx can append one presentation newline; no other normalization is allowed.
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_digest and raw.endswith(
        b"\n"
    ):
        raw = raw[:-1]
    if "sha256:" + hashlib.sha256(raw).hexdigest() != expected_digest:
        raise SandboxDriftError("Product v0.3 registry platform manifest bytes differ")
    try:
        platform_manifest = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise SandboxDriftError(
            "Product v0.3 registry platform manifest is malformed"
        ) from error
    config = (
        platform_manifest.get("config") if isinstance(platform_manifest, dict) else None
    )
    repo_digests = cached.get("RepoDigests")
    local_descriptor = cached.get("Descriptor")
    containerd_identity = (
        cached.get("Id") == expected_digest
        and isinstance(local_descriptor, dict)
        and local_descriptor.get("digest") == expected_digest
        and local_descriptor.get("platform") == matches[0].get("platform")
    )
    if (
        not isinstance(config, dict)
        or (config.get("digest") != cached.get("Id") and not containerd_identity)
        or cached.get("Os") != "linux"
        or cached.get("Architecture") != "arm64"
        or not isinstance(repo_digests, list)
        or f"{reference.rsplit(':', 1)[0]}@{descriptor.get('digest')}"
        not in repo_digests
    ):
        raise SandboxDriftError("Product v0.3 acquired image identity differs")
    return CachedImage(
        source_reference=reference,
        image_id=str(cached["Id"]),
        image_index_digest=str(descriptor["digest"]),
        resolved_platform_digest=str(matches[0]["digest"]),
        raw_inspect_sha256=hashlib.sha256(
            json.dumps(cached, sort_keys=True).encode()
        ).hexdigest(),
    )


def build_product_v030_runtime_bundle(repository_root: Path) -> ConfigBundle:
    original = build_product_v024_runtime_bundle(repository_root)
    payload = original.model_dump(mode="json")
    files = payload["environment"]["compose_files"]
    files.insert(1, "third_party/opentelemetry-demo/compose.full.yaml")
    files.append("config/product-v030/compose.sandbox.yaml")
    payload["environment"]["sandbox_id"] = "e477da43-27e7-4c55-8491-1d45cda03000"
    return ConfigBundle.model_validate(payload)


class ProductV030SandboxEnvironment(ProductV024SandboxEnvironment):
    def __init__(
        self,
        *,
        repository_root: Path,
        bundle: ConfigBundle,
        flagd_directory: Path,
        runner: ExactCommandRunner | None = None,
        full_mode_images: tuple[CachedImage, ...] = (),
    ) -> None:
        super().__init__(
            repository_root=repository_root,
            bundle=bundle,
            flagd_directory=flagd_directory,
            runner=runner,
        )
        self.full_mode_images = full_mode_images
        self.product_v024_runtime_root = self.repository_root / "config/product-v030"

    @property
    def expected_services(self) -> tuple[str, ...]:
        return tuple(sorted((*EXPECTED_SERVICES, *FULL_SERVICES_V030)))

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        # Compose down --remove-orphans spans the project, not the sandbox label.
        # Reject any foreign project member before either start or cleanup.
        label = f"label=com.docker.compose.project={self.bundle.environment.compose_project}"
        for kind, prefix in (
            ("container", ("docker", "ps", "-aq")),
            ("network", ("docker", "network", "ls", "-q")),
            ("volume", ("docker", "volume", "ls", "-q")),
        ):
            if self._ids((*prefix, "--filter", label)) != self._owned_ids(kind):
                raise ResourceOwnershipError(
                    "Product v0.3 found foreign same-project resources"
                )
        return super().verify_owned_resources(require_complete=require_complete)

    def _compose_env(self) -> dict[str, str]:
        value = super()._compose_env()
        value["SANDBOX_COLLECTOR_CONFIG"] = str(
            self.repository_root / "config/product-v030/otelcol-sandbox.yml"
        )
        value["SANDBOX_KAFKA_JMX_CONFIG"] = str(
            self.repository_root / "config/product-v030/kafka-jmx.yml"
        )
        jmx = Path(value["SANDBOX_KAFKA_JMX_CONFIG"])
        if jmx.is_symlink() or not jmx.is_file():
            raise SandboxDriftError(
                "Product v0.3 JMX config is not a regular owned file"
            )
        value["SANDBOX_KAFKA_JMX_SHA256"] = hashlib.sha256(jmx.read_bytes()).hexdigest()
        return value

    def _verify_resolved_contract(self, value: Mapping[str, object]) -> None:
        normalized = deepcopy(dict(value))
        services = normalized.get("services")
        if (
            not isinstance(services, dict)
            or tuple(sorted(services)) != self.expected_services
        ):
            raise SandboxDriftError("Product v0.3 full-mode service inventory differs")
        for name in FULL_SERVICES_V030:
            service = services.pop(name)
            expected_mounts = (
                [
                    {
                        "type": "bind",
                        "source": str(
                            self.repository_root / "config/product-v030/kafka-jmx.yml"
                        ),
                        "target": "/etc/ecomsre/kafka-jmx.yml",
                        "read_only": True,
                        "bind": {"create_host_path": False},
                    }
                ]
                if name == "kafka"
                else []
            )
            if (
                not isinstance(service, dict)
                or service.get("platform") != "linux/arm64"
                or service.get("pull_policy") != "never"
                or service.get("container_name") != f"ecomsre-live-sandbox-v1-{name}"
                or not isinstance(service.get("labels"), dict)
                or service.get("labels", {}).get(
                    self.bundle.environment.sandbox_label_key
                )
                != self.bundle.environment.sandbox_id
                or service.get("ports")
                or (service.get("volumes") or []) != expected_mounts
                or service.get("privileged") is True
                or service.get("network_mode") == "host"
                or service.get("image") != f"ghcr.io/open-telemetry/demo:3.0.0-{name}"
            ):
                raise SandboxDriftError(f"Product v0.3 {name} isolation differs")
            if name == "kafka":
                expected_env = {
                    "OTEL_JMX_CONFIG": "/etc/ecomsre/kafka-jmx.yml",
                    "OTEL_INSTRUMENTATION_METHODS_INCLUDE": "kafka.server.KafkaApis[handleProduceRequest]",
                }
                observed_env = service.get("environment")
                if not isinstance(observed_env, dict) or any(
                    observed_env.get(key) != value
                    for key, value in expected_env.items()
                ):
                    raise SandboxDriftError(
                        "Product v0.3 kafka telemetry configuration differs"
                    )
                if (
                    service["labels"].get("io.ecomsre.telemetry.jmx.sha256")
                    != self._compose_env()["SANDBOX_KAFKA_JMX_SHA256"]
                ):
                    raise SandboxDriftError(
                        "Product v0.3 kafka telemetry content binding differs"
                    )
        # Parent verifies the exact existing read-only socket/config mount delta.
        self.product_v024_runtime_root = self.repository_root / "config/product-v030"
        super()._verify_resolved_contract(normalized)

    def inspect_cached_images(self, resolved: ResolvedSandbox) -> CachedImageInspection:
        expected = {image.source_reference: image for image in self.full_mode_images}
        if (
            len(expected) != len(self.full_mode_images)
            or set(expected) != set(FULL_IMAGES_V030)
            or not set(expected).issubset(resolved.image_references)
        ):
            raise SandboxDriftError(
                "Product v0.3 full-mode image authority is incomplete"
            )
        historical = super().inspect_cached_images(
            resolved.model_copy(
                update={
                    "image_references": tuple(
                        ref for ref in resolved.image_references if ref not in expected
                    )
                }
            )
        )
        additions = []
        for reference in FULL_IMAGES_V030:
            result = self.runner.run(
                ("docker", "image", "inspect", "--platform", "linux/arm64", reference),
                cwd=self.repository_root,
            )
            raw = json.loads(result.stdout)
            image = raw[0] if isinstance(raw, list) and len(raw) == 1 else None
            identity = expected[reference]
            if (
                not isinstance(image, dict)
                or image.get("Os") != "linux"
                or image.get("Architecture") != "arm64"
                or image.get("Id") != identity.image_id
                or not isinstance(image.get("RepoDigests"), list)
                or f"{reference.rsplit(':', 1)[0]}@{identity.image_index_digest}"
                not in image["RepoDigests"]
            ):
                raise SandboxDriftError(
                    f"Product v0.3 cached image identity differs for {reference}"
                )
            additions.append(
                identity.model_copy(
                    update={
                        "raw_inspect_sha256": hashlib.sha256(
                            result.stdout.encode()
                        ).hexdigest()
                    }
                )
            )
        return historical.model_copy(
            update={
                "images": tuple(
                    sorted(
                        (*historical.images, *additions),
                        key=lambda item: item.source_reference,
                    )
                )
            }
        )

    def verify_cached_images(
        self, resolved: ResolvedSandbox, control_root: Path
    ) -> str:
        inspection = self.inspect_cached_images(resolved)
        return write_private_json(
            control_root / "image-lock.json",
            {
                "schema_version": "product-v030.private-full-mode-image-lock.v1",
                "authorization": "USER_GOAL_STANDING_AUTHORIZATION_DEC_061",
                "historical_lock_sha256": inspection.historical_image_lock_sha256,
                "historical_lock_unchanged": True,
                "upstream_commit": inspection.upstream_commit,
                "compose_sha256": resolved.compose_sha256,
                "platform": "linux/arm64",
                "added_source_references": FULL_IMAGES_V030,
                "images": [
                    image.model_dump(mode="json") for image in inspection.images
                ],
            },
            create_once=True,
        )
