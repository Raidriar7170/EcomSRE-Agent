"""Environment-scoped idempotent change-event ingestion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    RecentChangeRecordV22,
    RolloutStateV22,
    semantic_sha256_v22,
)
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.errors import ProductError, not_found
from ecomsre.product.ids import new_product_id
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


class ChangeEventCreateV1(ProductModelV1):
    service_id: str = Field(pattern=r"^svc-[0-9a-f]{24}$")
    category: ChangeCategoryV22
    occurred_at: datetime
    revision: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1, max_length=1000)
    external_change_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )

    @model_validator(mode="after")
    def require_utc_time(self) -> "ChangeEventCreateV1":
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() != timedelta(0):
            raise ValueError("change event time must be UTC")
        return self


class ChangeEventRecordV1(ChangeEventCreateV1):
    schema_version: Literal["ecomsre.product.change-event.v1"] = (
        "ecomsre.product.change-event.v1"
    )
    change_event_id: str = Field(pattern=r"^chg-[0-9a-f]{24}$")
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    v22_record: RecentChangeRecordV22
    created_at: datetime
    event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_event(self) -> "ChangeEventRecordV1":
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(0):
            raise ValueError("change event creation time must be UTC")
        if self.v22_record.observed_at != self.occurred_at:
            raise ValueError("change event v2.2 timestamp differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"event_sha256"})
        )
        if self.event_sha256 != expected:
            raise ValueError("change event digest differs")
        return self


class ChangeEventRepositoryV1:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def create(
        self,
        environment_id: str,
        value: ChangeEventCreateV1 | Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> ChangeEventRecordV1:
        request = (
            value
            if isinstance(value, ChangeEventCreateV1)
            else ChangeEventCreateV1.model_validate(value)
        )
        created_at = (
            datetime.now(UTC) if now is None else datetime.fromtimestamp(now, UTC)
        )
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                environment = connection.execute(
                    "SELECT 1 FROM environments WHERE environment_id = ?",
                    (environment_id,),
                ).fetchone()
                if environment is None:
                    raise not_found(
                        "ENVIRONMENT_NOT_FOUND",
                        "The requested environment does not exist.",
                    )
                service = connection.execute(
                    """SELECT logical_service FROM services
                       WHERE environment_id = ? AND service_id = ?""",
                    (environment_id, request.service_id),
                ).fetchone()
                if service is None:
                    raise ProductError(
                        "SERVICE_NOT_FOUND",
                        "The requested canonical service does not exist.",
                        status_code=404,
                    )
                existing = connection.execute(
                    """SELECT payload_json FROM change_events
                       WHERE environment_id = ? AND external_change_id = ?""",
                    (environment_id, request.external_change_id),
                ).fetchone()
                if existing is not None:
                    record = ChangeEventRecordV1.model_validate_json(
                        existing["payload_json"]
                    )
                    existing_request = ChangeEventCreateV1.model_validate(
                        record.model_dump(
                            mode="python",
                            include=set(ChangeEventCreateV1.model_fields),
                        )
                    )
                    if existing_request != request:
                        raise ProductError(
                            "CHANGE_IDEMPOTENCY_CONFLICT",
                            "The external change ID is already bound to a different payload.",
                            status_code=409,
                        )
                    connection.execute("COMMIT")
                    return record
                revision_digest = hashlib.sha256(request.revision.encode("utf-8")).hexdigest()
                opaque_seed = f"{environment_id}\x00{request.external_change_id}".encode()
                v22_record = RecentChangeRecordV22(
                    schema_version="dta-v22.recent-change-record.v1",
                    opaque_change_id=f"chg_{hashlib.sha256(opaque_seed).hexdigest()[:24]}",
                    service=service["logical_service"],
                    observed_at=request.occurred_at,
                    category=request.category,
                    rollout_state=RolloutStateV22.COMPLETED,
                    revision_digest=revision_digest,
                )
                draft = ChangeEventRecordV1.model_construct(
                    **request.model_dump(mode="python"),
                    change_event_id=new_product_id("chg"),
                    environment_id=environment_id,
                    v22_record=v22_record,
                    created_at=created_at,
                    event_sha256="0" * 64,
                )
                payload = draft.model_dump(mode="json", exclude={"event_sha256"})
                record = ChangeEventRecordV1.model_validate(
                    {
                        **request.model_dump(mode="python"),
                        "change_event_id": draft.change_event_id,
                        "environment_id": environment_id,
                        "v22_record": v22_record,
                        "created_at": created_at,
                        "event_sha256": semantic_sha256_v22(payload),
                    }
                )
                serialized = json.dumps(
                    record.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    """INSERT INTO change_events(
                        change_event_id, environment_id, external_change_id,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        record.change_event_id,
                        environment_id,
                        request.external_change_id,
                        serialized,
                        created_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
                return record
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_v22(
        self,
        *,
        environment_id: str,
        logical_services: tuple[str, ...],
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[tuple[RecentChangeRecordV22, ...], bool]:
        if not 1 <= limit <= 20:
            raise ValueError("change query limit is outside the canonical bound")
        if logical_services != tuple(sorted(set(logical_services))):
            raise ValueError("change query services are not canonical")
        if started_at.tzinfo is None or ended_at.tzinfo is None or ended_at <= started_at:
            raise ValueError("change query window is invalid")
        placeholders = ",".join("?" for _item in logical_services)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM change_events WHERE environment_id = ? "
                f"AND json_extract(payload_json, '$.v22_record.service') IN ({placeholders}) "
                "AND julianday(json_extract(payload_json, '$.v22_record.observed_at')) "
                "BETWEEN julianday(?) AND julianday(?) "
                "ORDER BY json_extract(payload_json, '$.v22_record.observed_at'), "
                "change_event_id LIMIT ?",
                (
                    environment_id,
                    *logical_services,
                    started_at.isoformat(),
                    ended_at.isoformat(),
                    limit + 1,
                ),
            ).fetchall()
        selected = tuple(
            record.v22_record
            for row in rows
            for record in (ChangeEventRecordV1.model_validate_json(row["payload_json"]),)
            if record.v22_record.service in set(logical_services)
            and started_at <= record.v22_record.observed_at <= ended_at
        )
        return selected[:limit], len(selected) > limit


__all__ = (
    "ChangeEventCreateV1",
    "ChangeEventRecordV1",
    "ChangeEventRepositoryV1",
)
