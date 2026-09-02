from pathlib import Path

from ecomsre_live_sandbox.product_v030 import (
    ProductV030SandboxEnvironment,
    build_product_v030_runtime_bundle,
)


ROOT = Path(__file__).resolve().parents[2]


def test_full_mode_extends_the_owned_runtime_without_changing_upstream(tmp_path):
    bundle = build_product_v030_runtime_bundle(ROOT)
    files = bundle.environment.compose_files
    assert files.index("third_party/opentelemetry-demo/compose.full.yaml") == 1
    assert files[-1] == "config/product-v030/compose.sandbox.yaml"
    environment = ProductV030SandboxEnvironment(
        repository_root=ROOT,
        bundle=bundle,
        flagd_directory=tmp_path,
    )
    assert {"accounting", "fraud-detection", "kafka", "checkout"}.issubset(
        environment.expected_services
    )
    assert len(environment.expected_services) == 28
    assert bundle.environment.platform == "linux/arm64"
    assert (
        bundle.environment.upstream_commit == "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    )
