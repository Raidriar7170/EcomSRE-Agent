"""Process-independent counters rendered in Prometheus text format."""

from __future__ import annotations

from datetime import UTC, datetime
import json
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
        canonical = dict(sorted((labels or {}).items()))
        if any(
            not _LABEL_PATTERN.fullmatch(name)
            or not value
            or len(value) > 120
            or any(character in value for character in "\r\n\x00\"\\")
            for name, value in canonical.items()
        ):
            raise ValueError("Product metric labels are invalid")
        serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO product_metric_counters(metric_name, labels_json, value, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(metric_name, labels_json) DO UPDATE SET "
                "value = product_metric_counters.value + excluded.value, "
                "updated_at = excluded.updated_at",
                (metric_name, serialized, amount, datetime.now(UTC).isoformat()),
            )

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
                labels = json.loads(labels_json)
                rendered = ",".join(
                    f'{key}="{label}"' for key, label in labels.items()
                )
                lines.append(f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}")
        return "\n".join(lines) + "\n"


__all__ = ("METRIC_NAMES_V1", "ProductMetricsV1")
