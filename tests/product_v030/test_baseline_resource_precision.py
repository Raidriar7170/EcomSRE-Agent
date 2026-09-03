import pytest
import importlib.util
from pathlib import Path

from ecomsre.product.connectors.prometheus import PrometheusConnectorV1


@pytest.mark.parametrize("start_offset", [0.000036, 0.0009])
def test_baseline_resource_window_tolerates_prometheus_millisecond_quantization(start_offset):
    # Actual live-003 shape: query_range returns millisecond timestamps and the
    # first sample is clipped to the microsecond-resolution requested start.
    base = 1788411357.585
    stamps = [base + start_offset, base + 10, base + 20, base + 30]
    record = PrometheusConnectorV1._resource_record(
        "kafka",
        [(stamp, 5.0) for stamp in stamps],
        [(stamp, float(1000 + i * 100)) for i, stamp in enumerate(stamps)],
        sampling_window_seconds=None,
        sample_count=None,
    )
    assert record is not None
    assert record.sampling_window_seconds == 30
    assert [sample.offset_ms for sample in record.samples] == [0, 10000, 20000, 30000]
    assert record.memory_slope_bytes_per_second == 10


def test_baseline_resource_window_rejects_real_nonintegral_duration():
    samples = [(0.02, 1000.0), (10.0, 1100.0), (20.0, 1200.0), (30.0, 1300.0)]
    assert PrometheusConnectorV1._resource_record(
        "kafka", samples, samples, sampling_window_seconds=None, sample_count=None
    ) is None


def test_goal_baseline_requires_resource_statistics_for_every_candidate():
    path = Path(__file__).resolve().parents[2] / "scripts/product_v030/build_live_baseline.py"
    spec = importlib.util.spec_from_file_location("baseline_resource_gate_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = {"v22_baseline_profile": {"resource_stats": []}}
    with pytest.raises(RuntimeError, match="resource Baseline is incomplete"):
        module.require_baseline_resource_coverage(payload)
    payload["v22_baseline_profile"]["resource_stats"] = [
        {"service": service} for service in module.CANDIDATES_V030
    ]
    module.require_baseline_resource_coverage(payload)
    payload["v22_baseline_profile"]["resource_stats"].pop()
    with pytest.raises(RuntimeError, match="resource Baseline is incomplete"):
        module.require_baseline_resource_coverage(payload)
