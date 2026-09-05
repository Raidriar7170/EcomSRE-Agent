"""Read the one-entry Product registry; no executable object is constructed."""

from pathlib import Path

from ecomsre.product.remediation.contracts import RemediationRegistryV1


def load_registry(path: Path, *, expected_sha256: str) -> RemediationRegistryV1:
    registry = RemediationRegistryV1.model_validate_json(path.read_bytes())
    if registry.registry_sha256 != expected_sha256:
        raise ValueError("remediation registry binding differs")
    return registry
