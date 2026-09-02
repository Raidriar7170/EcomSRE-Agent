"""Goal-scoped full-mode delta over the owned Product v0.2.4 sandbox."""

from copy import deepcopy
from pathlib import Path
from typing import Mapping

from ecomsre_live_sandbox.contracts import ConfigBundle
from ecomsre_live_sandbox.environment import EXPECTED_SERVICES, SandboxDriftError
from ecomsre_live_sandbox.product_v024 import (
    ProductV024SandboxEnvironment,
    build_product_v024_runtime_bundle,
)


FULL_SERVICES_V030 = ("accounting", "fraud-detection", "kafka")


def build_product_v030_runtime_bundle(repository_root: Path) -> ConfigBundle:
    original = build_product_v024_runtime_bundle(repository_root)
    payload = original.model_dump(mode="json")
    files = payload["environment"]["compose_files"]
    files.insert(1, "third_party/opentelemetry-demo/compose.full.yaml")
    files.append("config/product-v030/compose.sandbox.yaml")
    payload["environment"]["sandbox_id"] = "e477da43-27e7-4c55-8491-1d45cda03000"
    return ConfigBundle.model_validate(payload)


class ProductV030SandboxEnvironment(ProductV024SandboxEnvironment):
    @property
    def expected_services(self) -> tuple[str, ...]:
        return tuple(sorted((*EXPECTED_SERVICES, *FULL_SERVICES_V030)))

    def _compose_env(self) -> dict[str, str]:
        value = super()._compose_env()
        value["SANDBOX_COLLECTOR_CONFIG"] = str(
            self.repository_root / "config/product-v030/otelcol-sandbox.yml"
        )
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
            if (
                not isinstance(service, dict)
                or service.get("platform") != "linux/arm64"
                or service.get("pull_policy") != "never"
                or service.get("container_name") != f"ecomsre-live-sandbox-v1-{name}"
                or service.get("labels", {}).get(
                    self.bundle.environment.sandbox_label_key
                )
                != self.bundle.environment.sandbox_id
                or service.get("ports")
                or service.get("volumes")
                or service.get("privileged") is True
                or service.get("network_mode") == "host"
                or not str(service.get("image", "")).endswith(f":3.0.0-{name}")
            ):
                raise SandboxDriftError(f"Product v0.3 {name} isolation differs")
        # Parent verifies the exact existing read-only socket/config mount delta.
        self.product_v024_runtime_root = self.repository_root / "config/product-v030"
        super()._verify_resolved_contract(normalized)
