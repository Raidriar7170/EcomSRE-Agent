"""Process-independent counters and duration histograms in Prometheus format."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
import re

from ecomsre.product.storage.sqlite_store import SqliteStoreV1


METRIC_NAMES_V1 = (
    "ecomsre_http_requests_total",
    "ecomsre_jobs_total",
    "ecomsre_job_duration_seconds",
    "ecomsre_connector_requests_total",
    "ecomsre_connector_failures_total",
    "ecomsre_diagnosis_terminals_total",
    "ecomsre_open_world_reports_total",
    "ecomsre_fault_families_total",
    "ecomsre_registration_promotions_total",
)
_LABEL_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
DURATION_HISTOGRAM_NAMES_V1 = (
    "ecomsre_job_queue_wait_seconds",
    "ecomsre_job_execution_seconds",
)
_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
    120,
    300,
)
_UPSERT = (
    "INSERT INTO product_metric_counters(metric_name, labels_json, value, updated_at) "
    "VALUES (?, ?, ?, ?) ON CONFLICT(metric_name, labels_json) DO UPDATE SET "
    "value = product_metric_counters.value + excluded.value, "
    "updated_at = excluded.updated_at"
)


def _labels_json(labels: dict[str, str] | None) -> str:
    canonical = dict(sorted((labels or {}).items()))
    if any(
        not _LABEL_PATTERN.fullmatch(name)
        or not value
        or len(value) > 120
        or any(character in value for character in '\r\n\x00"\\')
        for name, value in canonical.items()
    ):
        raise ValueError("Product metric labels are invalid")
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def _sample(name: str, labels_json: str, value: int | float) -> str:
    rendered = ",".join(
        f'{key}="{label}"' for key, label in json.loads(labels_json).items()
    )
    return f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}"


class ProductMetricsV1:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def increment(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        *,
        amount: int = 1,
    ) -> None:
        if metric_name not in METRIC_NAMES_V1 or amount < 0:
            raise ValueError("Product metric update is invalid")
        serialized = _labels_json(labels)
        with self.store.connect() as connection:
            connection.execute(
                _UPSERT,
                (metric_name, serialized, amount, datetime.now(UTC).isoformat()),
            )

    def observe_duration(
        self,
        metric_name: str,
        seconds: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Atomically persist cumulative buckets/count and an integer microsecond sum.

        The sum uses the existing integer table without a schema migration;
        only its exported value is converted back to seconds.
        """
        if (
            metric_name not in DURATION_HISTOGRAM_NAMES_V1
            or not math.isfinite(seconds)
            or seconds < 0
            or seconds >= (2**63 - 1) / 1_000_000
            or "le" in (labels or {})
        ):
            raise ValueError("Product duration observation is invalid")
        serialized = _labels_json(labels)
        updated_at = datetime.now(UTC).isoformat()
        samples = [
            (metric_name + "_count", serialized, 1, updated_at),
            (
                metric_name + "_sum_microseconds",
                serialized,
                round(seconds * 1_000_000),
                updated_at,
            ),
        ]
        for boundary in (*_DURATION_BUCKETS, math.inf):
            bucket_labels = {
                **(labels or {}),
                "le": format(boundary, "g") if math.isfinite(boundary) else "+Inf",
            }
            samples.append(
                (
                    metric_name + "_bucket",
                    _labels_json(bucket_labels),
                    int(seconds <= boundary),
                    updated_at,
                )
            )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.executemany(_UPSERT, samples)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def render(self) -> str:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT metric_name, labels_json, value FROM product_metric_counters "
                "ORDER BY metric_name, labels_json"
            ).fetchall()
        values = {
            (row["metric_name"], row["labels_json"]): row["value"] for row in rows
        }
        lines: list[str] = []
        for name in METRIC_NAMES_V1:
            lines.append(f"# TYPE {name} counter")
            matching = tuple(
                (labels_json, value)
                for (metric_name, labels_json), value in values.items()
                if metric_name == name
            )
            if not matching:
                lines.append(f"{name} 0")
                continue
            for labels_json, value in matching:
                lines.append(_sample(name, labels_json, value))
        for name in DURATION_HISTOGRAM_NAMES_V1:
            lines.append(f"# TYPE {name} histogram")
            series = sorted(
                labels_json
                for metric_name, labels_json in values
                if metric_name == name + "_count"
            )
            for labels_json in series:
                labels = json.loads(labels_json)
                for boundary in (*_DURATION_BUCKETS, math.inf):
                    bucket_labels = _labels_json(
                        {
                            **labels,
                            "le": format(boundary, "g")
                            if math.isfinite(boundary)
                            else "+Inf",
                        }
                    )
                    lines.append(
                        _sample(
                            name + "_bucket",
                            bucket_labels,
                            values[(name + "_bucket", bucket_labels)],
                        )
                    )
                lines.append(
                    _sample(
                        name + "_sum",
                        labels_json,
                        values[(name + "_sum_microseconds", labels_json)] / 1_000_000,
                    )
                )
                lines.append(
                    _sample(
                        name + "_count",
                        labels_json,
                        values[(name + "_count", labels_json)],
                    )
                )
        return "\n".join(lines) + "\n"


__all__ = ("DURATION_HISTOGRAM_NAMES_V1", "METRIC_NAMES_V1", "ProductMetricsV1")
