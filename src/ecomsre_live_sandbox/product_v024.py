"""Exact owned-Sandbox Docker stats extension for Product v0.2.4."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Mapping

from ecomsre_live_sandbox.contracts import ConfigBundle, load_bundle
from ecomsre_live_sandbox.environment import (
    EXPECTED_SERVICES,
    ExactCommandRunner,
    SandboxDriftError,
    SandboxEnvironment,
)


_RUNTIME_RELATIVE_V024 = Path("config/product-v024/runtime")
_OVERLAY_RELATIVE_V024 = _RUNTIME_RELATIVE_V024 / "compose.sandbox.yaml"
_DOCKER_SOCKET = Path("/var/run/docker.sock")
_COLLECTOR_CONFIG_TARGET = "/etc/otelcol-config-sandbox.yml"


def build_product_v024_runtime_bundle(repository_root: Path) -> ConfigBundle:
    root = Path(repository_root).resolve(strict=True)
    original = load_bundle(root / "config/live-telemetry-controlled-remediation-v1")
    payload = original.model_dump(mode="json")
    payload["environment"]["compose_files"] = [
        *original.environment.compose_files,
        _OVERLAY_RELATIVE_V024.as_posix(),
    ]
    return ConfigBundle.model_validate(payload)


def validate_collector_service_v024(
    service: Mapping[str, object],
    *,
    product_config: Path,
    historical_config: Path,
) -> dict[str, object]:
    """Admit only one read-only Docker socket and the v0.2.4 final config."""

    normalized = deepcopy(dict(service))
    volumes = normalized.get("volumes")
    if not isinstance(volumes, list) or len(volumes) != 6:
        raise SandboxDriftError("Product v0.2.4 Collector mount set differs")

    product_matches = [
        item
        for item in volumes
        if isinstance(item, dict) and item.get("target") == _COLLECTOR_CONFIG_TARGET
    ]
    socket_matches = [
        item
        for item in volumes
        if isinstance(item, dict)
        and item.get("source") == str(_DOCKER_SOCKET)
    ]
    if (
        len(product_matches) != 1
        or product_matches[0].get("type") != "bind"
        or Path(str(product_matches[0].get("source", ""))).resolve(strict=False)
        != product_config.resolve(strict=False)
        or product_matches[0].get("read_only") is not True
    ):
        raise SandboxDriftError("Product v0.2.4 Collector config bind differs")
    if (
        len(socket_matches) != 1
        or socket_matches[0].get("type") != "bind"
        or socket_matches[0].get("target") != str(_DOCKER_SOCKET)
        or socket_matches[0].get("read_only") is not True
    ):
        raise SandboxDriftError("Product v0.2.4 Docker socket bind differs")

    product_matches[0]["source"] = str(historical_config.resolve(strict=False))
    normalized["volumes"] = [
        item for item in volumes if item is not socket_matches[0]
    ]
    return normalized


class ProductV024SandboxEnvironment(SandboxEnvironment):
    """Preserve the frozen Sandbox and admit only the authorized fallback delta."""

    def __init__(
        self,
        *,
        repository_root: Path,
        bundle: ConfigBundle,
        flagd_directory: Path,
        runner: ExactCommandRunner | None = None,
    ) -> None:
        super().__init__(
            repository_root=repository_root,
            bundle=bundle,
            flagd_directory=flagd_directory,
            runner=runner,
        )
        self.product_v024_runtime_root = (
            self.repository_root / _RUNTIME_RELATIVE_V024
        ).resolve()

    def _compose_env(self) -> dict[str, str]:
        value = super()._compose_env()
        value["SANDBOX_COLLECTOR_CONFIG"] = str(
            self.product_v024_runtime_root / "otelcol-sandbox.yml"
        )
        return value

    def _verify_resolved_contract(self, value: Mapping[str, object]) -> None:
        normalized = deepcopy(dict(value))
        services = normalized.get("services")
        if (
            not isinstance(services, dict)
            or tuple(sorted(services)) != EXPECTED_SERVICES
        ):
            raise SandboxDriftError("Product v0.2.4 service inventory differs")
        collector = services.get("otel-collector")
        if not isinstance(collector, Mapping):
            raise SandboxDriftError("Product v0.2.4 Collector service is absent")
        services["otel-collector"] = validate_collector_service_v024(
            collector,
            product_config=self.product_v024_runtime_root / "otelcol-sandbox.yml",
            historical_config=self.config_root / "otelcol-sandbox.yml",
        )
        super()._verify_resolved_contract(normalized)

    def manual_cleanup_command(self) -> str:
        return " ".join(
            (
                "DEMO_VERSION=3.0.0",
                "IMAGE_VERSION=3.0.0",
                f"ECOMSRE_SANDBOX_ID={self.bundle.environment.sandbox_id}",
                f"SANDBOX_FLAGD_DIR={self.flagd_directory}",
                "SANDBOX_COLLECTOR_CONFIG="
                f"{self.product_v024_runtime_root / 'otelcol-sandbox.yml'}",
                *self._compose_prefix(),
                "down --volumes --remove-orphans --timeout 30",
            )
        )


__all__ = (
    "ProductV024SandboxEnvironment",
    "build_product_v024_runtime_bundle",
    "validate_collector_service_v024",
)
