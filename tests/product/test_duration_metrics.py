from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.telemetry.metrics import ProductMetricsV1


NAME = "ecomsre_job_execution_seconds"
LABELS = {"job_type": "DIAGNOSIS", "status": "SUCCEEDED"}


def test_histogram_preserves_fractional_durations_and_survives_restart(tmp_path):
    path = tmp_path / "metrics.sqlite3"
    metrics = ProductMetricsV1(SqliteStoreV1(path))
    for seconds in (0.125, 0.375, 400.0):
        metrics.observe_duration(NAME, seconds, LABELS)
    rendered = metrics.render()
    assert f"# TYPE {NAME} histogram" in rendered
    assert (
        f'{NAME}_bucket{{job_type="DIAGNOSIS",le="0.1",status="SUCCEEDED"}} 0'
        in rendered
    )
    assert (
        f'{NAME}_bucket{{job_type="DIAGNOSIS",le="0.25",status="SUCCEEDED"}} 1'
        in rendered
    )
    assert (
        f'{NAME}_bucket{{job_type="DIAGNOSIS",le="0.5",status="SUCCEEDED"}} 2'
        in rendered
    )
    assert (
        f'{NAME}_bucket{{job_type="DIAGNOSIS",le="+Inf",status="SUCCEEDED"}} 3'
        in rendered
    )
    assert f'{NAME}_sum{{job_type="DIAGNOSIS",status="SUCCEEDED"}} 400.5' in rendered
    assert f'{NAME}_count{{job_type="DIAGNOSIS",status="SUCCEEDED"}} 3' in rendered
    assert "_sum_microseconds" not in rendered
    assert ProductMetricsV1(SqliteStoreV1(path)).render() == rendered


def test_histogram_concurrent_writers_do_not_lose_samples(tmp_path):
    metrics = ProductMetricsV1(SqliteStoreV1(tmp_path / "metrics.sqlite3"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda _: metrics.observe_duration(NAME, 0.125, LABELS), range(12)
            )
        )
    rendered = metrics.render()
    assert f'{NAME}_count{{job_type="DIAGNOSIS",status="SUCCEEDED"}} 12' in rendered
    assert f'{NAME}_sum{{job_type="DIAGNOSIS",status="SUCCEEDED"}} 1.5' in rendered
    assert (
        f'{NAME}_bucket{{job_type="DIAGNOSIS",le="+Inf",status="SUCCEEDED"}} 12'
        in rendered
    )


def test_histogram_sample_is_atomic_on_storage_failure(tmp_path):
    store = SqliteStoreV1(tmp_path / "metrics.sqlite3")
    metrics = ProductMetricsV1(store)
    with store.connect() as connection:
        connection.execute("""CREATE TRIGGER reject_histogram_sum BEFORE INSERT
            ON product_metric_counters WHEN NEW.metric_name LIKE '%_sum_microseconds'
            BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic storage failure"):
        metrics.observe_duration(NAME, 0.1, LABELS)
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM product_metric_counters"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("seconds", [-0.1, float("nan"), float("inf"), -float("inf")])
def test_histogram_rejects_invalid_durations_before_writing(tmp_path, seconds):
    store = SqliteStoreV1(tmp_path / "metrics.sqlite3")
    metrics = ProductMetricsV1(store)
    with pytest.raises(ValueError):
        metrics.observe_duration(NAME, seconds, LABELS)
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM product_metric_counters"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "labels", [{"le": "1"}, {"status": 'bad"label'}, {"Status": "BAD"}]
)
def test_histogram_rejects_reserved_or_invalid_labels(tmp_path, labels):
    metrics = ProductMetricsV1(SqliteStoreV1(tmp_path / "metrics.sqlite3"))
    with pytest.raises(ValueError):
        metrics.observe_duration(NAME, 0.1, labels)


def test_legacy_duration_counter_keeps_its_type_and_value(tmp_path):
    metrics = ProductMetricsV1(SqliteStoreV1(tmp_path / "metrics.sqlite3"))
    metrics.increment("ecomsre_job_duration_seconds", LABELS, amount=2)
    metrics.observe_duration(NAME, 0.125, LABELS)
    rendered = metrics.render()
    assert "# TYPE ecomsre_job_duration_seconds counter" in rendered
    assert (
        'ecomsre_job_duration_seconds{job_type="DIAGNOSIS",status="SUCCEEDED"} 2'
        in rendered
    )
