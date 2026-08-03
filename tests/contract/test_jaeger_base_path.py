import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JAEGER_BASE_PATH = "/jaeger/ui"


def test_all_production_jaeger_requests_use_configured_base_path() -> None:
    for relative in (
        "src/ecomsre/environment/readiness.py",
        "src/ecomsre/telemetry/jaeger.py",
        "src/ecomsre/telemetry/probe.py",
        "src/ecomsre/telemetry/prometheus.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert '"/api/traces' not in source
        assert "f\"/api/traces" not in source

    registry = json.loads(
        (ROOT / "config/phase0/telemetry-queries-v3.0.0.json").read_bytes()
    )
    assert registry["jaeger"]["request_template"].startswith(
        f"{JAEGER_BASE_PATH}/api/traces?"
    )
