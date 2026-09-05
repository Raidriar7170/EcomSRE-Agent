"""Offline Product v0.4 development verifier; never mints measured evidence."""

import json
from pathlib import Path

from scripts.ci.verify_product_v040_history import verify as verify_history
from scripts.product.demo_remediation_v040 import run_demo


def verify(root: Path) -> dict[str, object]:
    history = verify_history(root)
    fixture = run_demo()
    if (
        fixture["terminal"] != "RECOVERED"
        or fixture["forward_fake_mutations"] != 1
        or fixture["recovery_windows"] != 2
        or fixture["live_campaigns"] != 0
        or fixture["live_mutations"] != 0
    ):
        raise ValueError("fixture development evidence differs")
    compose = (root / "docker-compose.product.yml").read_text()
    prefix, executor = compose.split("  remediation-executor:", 1)
    executor = executor.split("  remediation-control-gateway:", 1)[0]
    if (
        'profiles: ["remediation"]' not in executor
        or "network_mode: none" not in executor
        or "ports:" in executor
        or "WRITE_TOKEN" in prefix
        or "docker.sock" in compose
    ):
        raise ValueError("executor static isolation contract differs")
    overlay = (root / "config/product-v040/remediation-network.v1.yml").read_text()
    if (
        overlay.count("networks: !override [remediation-observation]") != 2
        or "internal: true" not in overlay
    ):
        raise ValueError("remediation observation isolation contract differs")
    return {
        "terminal": "ECOMSRE_PRODUCT_V040_D_EXECUTOR_AND_VERIFIER_PASS",
        "evidence_mode": "OFFLINE_FIXTURE_AND_STATIC_ONLY",
        "history": history,
        "fixture": fixture,
        "runtime_network_denial": "NOT_MEASURED_PR_E_GATE",
    }


if __name__ == "__main__":
    print(
        json.dumps(
            verify(Path(__file__).resolve().parents[2]), sort_keys=True, indent=2
        )
    )
