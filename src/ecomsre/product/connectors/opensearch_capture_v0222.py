"""Capture-first private OpenSearch evidence storage for Product v0.2.2.2."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SECRET_NAME = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|credential|password|secret|token)(?:$|[_-])",
    re.I,
)
_RESPONSE_HEADER_ALLOWLIST = frozenset({"content-length", "content-type"})
_REQUIRED_CAPTURE_KINDS = frozenset(
    {
        "INDEX_RESOLUTION",
        "MAPPING",
        "FIELD_CAPS",
        "STRUCTURAL_SAMPLE",
        "SERVICE_AGGREGATION",
        "TIMESTAMP_RANGE",
        "PROFILE_VERIFICATION",
    }
)


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


class OpenSearchCaptureStatusV0222(str, Enum):
    INTENT_RECORDED = "INTENT_RECORDED"
    RESPONSE_CAPTURED = "RESPONSE_CAPTURED"
    PARSED = "PARSED"
    REJECTED = "REJECTED"


class OpenSearchCaptureRequestKindV0222(str, Enum):
    INDEX_RESOLUTION = "INDEX_RESOLUTION"
    MAPPING = "MAPPING"
    FIELD_CAPS = "FIELD_CAPS"
    STRUCTURAL_SAMPLE = "STRUCTURAL_SAMPLE"
    SERVICE_AGGREGATION = "SERVICE_AGGREGATION"
    TIMESTAMP_RANGE = "TIMESTAMP_RANGE"
    PROFILE_VERIFICATION = "PROFILE_VERIFICATION"


class OpenSearchCaptureRequestV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-capture-request.v0222"] = (
        "ecomsre.product.opensearch-capture-request.v0222"
    )
    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    session_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    request_plan_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    request_kind: OpenSearchCaptureRequestKindV0222
    method: Literal["GET", "POST"]
    endpoint_class: str = Field(min_length=2, max_length=255)
    index_binding: str = Field(min_length=1, max_length=255)
    query_parameter_names: tuple[str, ...] = Field(max_length=10)
    request_body_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_ordinal: int = Field(ge=1, le=20)
    created_at: datetime
    status: Literal[OpenSearchCaptureStatusV0222.INTENT_RECORDED] = (
        OpenSearchCaptureStatusV0222.INTENT_RECORDED
    )

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        return _utc(value, "OpenSearch capture intent created_at")

    @model_validator(mode="after")
    def require_safe_canonical_intent(self) -> "OpenSearchCaptureRequestV0222":
        if self.query_parameter_names != tuple(sorted(set(self.query_parameter_names))):
            raise ValueError("OpenSearch capture query names are not canonical")
        if any(_SECRET_NAME.search(name) for name in self.query_parameter_names):
            raise ValueError("OpenSearch capture query name is secret-bearing")
        if any(character in self.index_binding for character in "\r\n\x00"):
            raise ValueError("OpenSearch capture index binding is invalid")
        return self


class OpenSearchCapturedResponseV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-captured-response.v0222"] = (
        "ecomsre.product.opensearch-captured-response.v0222"
    )
    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    request_kind: OpenSearchCaptureRequestKindV0222
    http_status: int = Field(ge=100, le=599)
    response_headers: dict[str, str] = Field(max_length=2)
    response_object_ref: str = Field(
        pattern=r"^objects/sha256/[0-9a-f]{2}/[0-9a-f]{64}$"
    )
    response_byte_size: int = Field(ge=0, le=2_000_000)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    transport_latency_ms: float = Field(ge=0, le=300_000)
    received_at: datetime
    safe_parse_stage: str | None = Field(default=None, max_length=80)
    safe_error_code: str | None = Field(default=None, max_length=120)
    structural_summary_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    status: OpenSearchCaptureStatusV0222

    @field_validator("received_at")
    @classmethod
    def received_at_is_utc(cls, value: datetime) -> datetime:
        return _utc(value, "OpenSearch capture response received_at")

    @model_validator(mode="after")
    def require_safe_response(self) -> "OpenSearchCapturedResponseV0222":
        if set(self.response_headers) - _RESPONSE_HEADER_ALLOWLIST:
            raise ValueError("OpenSearch capture response header is not allowlisted")
        if any(_SECRET_NAME.search(name) for name in self.response_headers):
            raise ValueError("OpenSearch capture response header is secret-bearing")
        if self.status is OpenSearchCaptureStatusV0222.RESPONSE_CAPTURED:
            if any(
                value is not None
                for value in (
                    self.safe_parse_stage,
                    self.safe_error_code,
                    self.structural_summary_sha256,
                )
            ):
                raise ValueError("OpenSearch unparsed response has parse metadata")
        elif self.status in {
            OpenSearchCaptureStatusV0222.PARSED,
            OpenSearchCaptureStatusV0222.REJECTED,
        }:
            if self.safe_parse_stage is None or self.structural_summary_sha256 is None:
                raise ValueError("OpenSearch parsed response metadata is incomplete")
            if (
                self.status is OpenSearchCaptureStatusV0222.REJECTED
                and self.safe_error_code is None
            ):
                raise ValueError("OpenSearch rejected response lacks a safe error code")
        else:
            raise ValueError("OpenSearch captured response status differs")
        return self


class OpenSearchCaptureLedgerEventV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-capture-event.v0222"] = (
        "ecomsre.product.opensearch-capture-event.v0222"
    )
    event_ordinal: int = Field(ge=1, le=100)
    session_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    status: OpenSearchCaptureStatusV0222
    event_payload: dict[str, Any]
    previous_event_sha256: str = Field(pattern=_SHA256_PATTERN)
    event_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_event(self) -> "OpenSearchCaptureLedgerEventV0222":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"event_sha256"})
        )
        if self.event_sha256 != expected:
            raise ValueError("OpenSearch capture event digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchCaptureLedgerEventV0222":
        draft = cls.model_construct(**values, event_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"event_sha256"})
        return cls.model_validate({**body, "event_sha256": semantic_sha256_v22(body)})


class OpenSearchCaptureLedgerV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-capture-ledger.v0222"] = (
        "ecomsre.product.opensearch-capture-ledger.v0222"
    )
    session_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    events: tuple[OpenSearchCaptureLedgerEventV0222, ...] = Field(max_length=100)
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_append_only_chain(self) -> "OpenSearchCaptureLedgerV0222":
        previous = "0" * 64
        for ordinal, event in enumerate(self.events, start=1):
            if (
                event.event_ordinal != ordinal
                or event.session_id != self.session_id
                or event.previous_event_sha256 != previous
            ):
                raise ValueError("OpenSearch capture event chain differs")
            previous = event.event_sha256
        body = self.model_dump(mode="json", exclude={"ledger_sha256"})
        if self.ledger_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch capture ledger digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        session_id: str,
        events: tuple[OpenSearchCaptureLedgerEventV0222, ...],
    ) -> "OpenSearchCaptureLedgerV0222":
        draft = cls.model_construct(
            session_id=session_id,
            events=events,
            ledger_sha256="0" * 64,
        )
        body = draft.model_dump(mode="json", exclude={"ledger_sha256"})
        return cls.model_validate({**body, "ledger_sha256": semantic_sha256_v22(body)})


class OpenSearchSchemaCaptureBundleV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-capture-bundle.v0222"] = (
        "ecomsre.product.opensearch-capture-bundle.v0222"
    )
    session_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    requests: tuple[OpenSearchCaptureRequestV0222, ...] = Field(max_length=20)
    responses: tuple[OpenSearchCapturedResponseV0222, ...] = Field(max_length=20)
    resolved_index_response_refs: tuple[str, ...]
    mapping_response_refs: tuple[str, ...]
    field_caps_response_refs: tuple[str, ...]
    structural_sample_refs: tuple[str, ...]
    service_aggregation_refs: tuple[str, ...]
    timestamp_range_refs: tuple[str, ...]
    profile_verification_refs: tuple[str, ...]
    capture_completeness: bool
    missing_capture_kinds: tuple[str, ...]
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)
    bundle_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_bundle(self) -> "OpenSearchSchemaCaptureBundleV0222":
        if tuple(request.request_ordinal for request in self.requests) != tuple(
            range(1, len(self.requests) + 1)
        ):
            raise ValueError("OpenSearch capture request ordinals differ")
        if self.missing_capture_kinds != tuple(sorted(set(self.missing_capture_kinds))):
            raise ValueError("OpenSearch missing capture kinds are not canonical")
        if self.capture_completeness == bool(self.missing_capture_kinds):
            raise ValueError("OpenSearch capture completeness differs")
        body = self.model_dump(mode="json", exclude={"bundle_sha256"})
        if self.bundle_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch capture bundle digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchSchemaCaptureBundleV0222":
        draft = cls.model_construct(**values, bundle_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"bundle_sha256"})
        return cls.model_validate({**body, "bundle_sha256": semantic_sha256_v22(body)})


class OpenSearchPublicStructuralSummaryV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-public-structure.v0222"] = (
        "ecomsre.product.opensearch-public-structure.v0222"
    )
    session_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    capture_completeness: bool
    response_sha256s: tuple[str, ...] = Field(max_length=20)
    json_path_inventory: dict[str, str] = Field(max_length=500)
    mapping_types: dict[str, tuple[str, ...]] = Field(max_length=500)
    field_caps_types: dict[str, tuple[str, ...]] = Field(max_length=500)
    presence_rates: dict[str, tuple[int, int]] = Field(max_length=500)
    timestamp_parseability_counts: dict[str, tuple[int, int]] = Field(
        max_length=100
    )
    service_alias_counts: dict[str, int] = Field(max_length=100)
    message_type_classes: tuple[str, ...] = Field(max_length=20)
    severity_type_classes: tuple[str, ...] = Field(max_length=20)
    trace_id_type_classes: tuple[str, ...] = Field(max_length=20)
    private_structural_shape_sha256: str = Field(pattern=_SHA256_PATTERN)
    summary_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bounded_canonical_summary(
        self,
    ) -> "OpenSearchPublicStructuralSummaryV0222":
        tuple_fields = (
            self.response_sha256s,
            self.message_type_classes,
            self.severity_type_classes,
            self.trace_id_type_classes,
        )
        if any(items != tuple(sorted(set(items))) for items in tuple_fields):
            raise ValueError("OpenSearch public structural tuple is not canonical")
        for values in (*self.mapping_types.values(), *self.field_caps_types.values()):
            if values != tuple(sorted(set(values))):
                raise ValueError("OpenSearch public structural types are not canonical")
        for present, total in (
            *self.presence_rates.values(),
            *self.timestamp_parseability_counts.values(),
        ):
            if not 0 <= present <= total <= 1_000_000:
                raise ValueError("OpenSearch public structural count differs")
        if any(not 0 <= count <= 1_000_000 for count in self.service_alias_counts.values()):
            raise ValueError("OpenSearch public service count differs")
        body = self.model_dump(mode="json", exclude={"summary_sha256"})
        if self.summary_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch public structural summary digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchPublicStructuralSummaryV0222":
        draft = cls.model_construct(**values, summary_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"summary_sha256"})
        return cls.model_validate({**body, "summary_sha256": semantic_sha256_v22(body)})


def build_public_structural_summary_v0222(
    *,
    bundle: OpenSearchSchemaCaptureBundleV0222,
    json_path_inventory: Mapping[str, str],
    mapping_types: Mapping[str, tuple[str, ...]],
    field_caps_types: Mapping[str, tuple[str, ...]],
    presence_rates: Mapping[str, tuple[int, int]],
    timestamp_parseability_counts: Mapping[str, tuple[int, int]],
    service_alias_counts: Mapping[str, int],
    message_type_classes: tuple[str, ...],
    severity_type_classes: tuple[str, ...],
    trace_id_type_classes: tuple[str, ...],
    private_structural_shape_sha256: str,
) -> OpenSearchPublicStructuralSummaryV0222:
    """Build a tracked-safe summary that cannot carry raw response bodies."""

    return OpenSearchPublicStructuralSummaryV0222.build(
        session_id=bundle.session_id,
        capture_bundle_sha256=bundle.bundle_sha256,
        capture_completeness=bundle.capture_completeness,
        response_sha256s=tuple(
            sorted({response.response_sha256 for response in bundle.responses})
        ),
        json_path_inventory=dict(sorted(json_path_inventory.items())),
        mapping_types={
            path: tuple(sorted(set(types)))
            for path, types in sorted(mapping_types.items())
        },
        field_caps_types={
            path: tuple(sorted(set(types)))
            for path, types in sorted(field_caps_types.items())
        },
        presence_rates=dict(sorted(presence_rates.items())),
        timestamp_parseability_counts=dict(
            sorted(timestamp_parseability_counts.items())
        ),
        service_alias_counts=dict(sorted(service_alias_counts.items())),
        message_type_classes=tuple(sorted(set(message_type_classes))),
        severity_type_classes=tuple(sorted(set(severity_type_classes))),
        trace_id_type_classes=tuple(sorted(set(trace_id_type_classes))),
        private_structural_shape_sha256=private_structural_shape_sha256,
    )


class OpenSearchCaptureStoreV0222:
    """Persist capture events before later parsing or profile resolution."""

    def __init__(
        self,
        *,
        private_root: Path,
        session_id: str,
        maximum_response_bytes: int,
    ) -> None:
        self.private_root = Path(private_root)
        if not 1 <= maximum_response_bytes <= 2_000_000:
            raise ValueError("OpenSearch capture response-byte bound differs")
        self.maximum_response_bytes = maximum_response_bytes
        self._objects_root = self.private_root / "objects" / "sha256"
        self._ledger_path = self.private_root / "capture-ledger.jsonl"
        self._bundle_path = self.private_root / "capture-bundle.json"
        self._prepare_private_directories()
        self._events = self._load_events()
        if self._events and self._events[0].session_id != session_id:
            raise ValueError("OpenSearch capture session differs from retained ledger")
        self.session_id = session_id
        self._requests: dict[str, OpenSearchCaptureRequestV0222] = {}
        self._responses: dict[str, OpenSearchCapturedResponseV0222] = {}
        self._rebuild_views()

    def _prepare_private_directories(self) -> None:
        self._objects_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in (
            self.private_root,
            self.private_root / "objects",
            self._objects_root,
        ):
            path.chmod(0o700)

    def _load_events(self) -> list[OpenSearchCaptureLedgerEventV0222]:
        if not self._ledger_path.exists():
            return []
        metadata = self._ledger_path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("OpenSearch capture ledger is not a regular file")
        events = [
            OpenSearchCaptureLedgerEventV0222.model_validate(json.loads(line))
            for line in self._ledger_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if events:
            OpenSearchCaptureLedgerV0222.build(
                session_id=events[0].session_id,
                events=tuple(events),
            )
        return events

    def _rebuild_views(self) -> None:
        for event in self._events:
            if event.status is OpenSearchCaptureStatusV0222.INTENT_RECORDED:
                self._requests[event.request_id] = (
                    OpenSearchCaptureRequestV0222.model_validate(event.event_payload)
                )
            elif event.status is OpenSearchCaptureStatusV0222.RESPONSE_CAPTURED:
                self._responses[event.request_id] = (
                    OpenSearchCapturedResponseV0222.model_validate(event.event_payload)
                )
            elif event.status in {
                OpenSearchCaptureStatusV0222.PARSED,
                OpenSearchCaptureStatusV0222.REJECTED,
            }:
                self._responses[event.request_id] = (
                    OpenSearchCapturedResponseV0222.model_validate(event.event_payload)
                )

    def _append_event(
        self,
        *,
        request_id: str,
        status: OpenSearchCaptureStatusV0222,
        payload: Mapping[str, Any],
    ) -> OpenSearchCaptureLedgerEventV0222:
        event = OpenSearchCaptureLedgerEventV0222.build(
            event_ordinal=len(self._events) + 1,
            session_id=self.session_id,
            request_id=request_id,
            status=status,
            event_payload=dict(payload),
            previous_event_sha256=(
                self._events[-1].event_sha256 if self._events else "0" * 64
            ),
        )
        line = json.dumps(
            event.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        )
        descriptor = os.open(
            self._ledger_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, f"{line}\n".encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._events.append(event)
        return event

    def record_request_intent(
        self,
        *,
        request_id: str,
        request_plan_id: str,
        request_kind: str,
        method: Literal["GET", "POST"],
        endpoint_class: str,
        index_binding: str,
        query_parameter_names: tuple[str, ...],
        request_body_schema_sha256: str,
        request_ordinal: int,
        created_at: datetime | None = None,
    ) -> OpenSearchCaptureRequestV0222:
        if request_id in self._requests:
            raise ValueError("OpenSearch capture request intent is duplicated")
        if request_ordinal != len(self._requests) + 1:
            raise ValueError("OpenSearch capture request ordinal differs")
        normalized_request_kind = OpenSearchCaptureRequestKindV0222(request_kind)
        request = OpenSearchCaptureRequestV0222(
            request_id=request_id,
            session_id=self.session_id,
            request_plan_id=request_plan_id,
            request_kind=normalized_request_kind,
            method=method,
            endpoint_class=endpoint_class,
            index_binding=index_binding,
            query_parameter_names=query_parameter_names,
            request_body_schema_sha256=request_body_schema_sha256,
            request_ordinal=request_ordinal,
            created_at=created_at or datetime.now(UTC),
        )
        self._append_event(
            request_id=request_id,
            status=OpenSearchCaptureStatusV0222.INTENT_RECORDED,
            payload=request.model_dump(mode="json"),
        )
        self._requests[request_id] = request
        return request

    def _write_object(self, response_body: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(response_body).hexdigest()
        relative = Path("objects") / "sha256" / digest[:2] / digest
        destination = self.private_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.parent.chmod(0o700)
        try:
            descriptor = os.open(
                destination,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            if destination.read_bytes() != response_body:
                raise ValueError("OpenSearch content-addressed object differs")
        else:
            try:
                os.write(descriptor, response_body)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return relative.as_posix(), digest

    def record_response(
        self,
        *,
        request_id: str,
        http_status: int,
        response_headers: Mapping[str, str],
        response_body: bytes,
        transport_latency_ms: float,
        received_at: datetime | None = None,
    ) -> OpenSearchCapturedResponseV0222:
        request = self._requests.get(request_id)
        if request is None or request_id in self._responses:
            raise ValueError("OpenSearch capture response lacks one pending intent")
        if len(response_body) > self.maximum_response_bytes:
            raise ValueError("OpenSearch capture response exceeds byte bound")
        filtered_headers = {
            str(name).lower(): str(value)[:255]
            for name, value in response_headers.items()
            if str(name).lower() in _RESPONSE_HEADER_ALLOWLIST
        }
        object_ref, digest = self._write_object(response_body)
        response = OpenSearchCapturedResponseV0222(
            request_id=request_id,
            request_kind=request.request_kind,
            http_status=http_status,
            response_headers=dict(sorted(filtered_headers.items())),
            response_object_ref=object_ref,
            response_byte_size=len(response_body),
            response_sha256=digest,
            transport_latency_ms=transport_latency_ms,
            received_at=received_at or datetime.now(UTC),
            status=OpenSearchCaptureStatusV0222.RESPONSE_CAPTURED,
        )
        self._append_event(
            request_id=request_id,
            status=OpenSearchCaptureStatusV0222.RESPONSE_CAPTURED,
            payload=response.model_dump(mode="json"),
        )
        self._responses[request_id] = response
        return response

    def record_parse_result(
        self,
        *,
        request_id: str,
        safe_parse_stage: str,
        safe_error_code: str | None,
        structural_summary_sha256: str,
        accepted: bool,
    ) -> OpenSearchCapturedResponseV0222:
        response = self._responses.get(request_id)
        if (
            response is None
            or response.status is not OpenSearchCaptureStatusV0222.RESPONSE_CAPTURED
        ):
            raise ValueError("OpenSearch capture parse result lacks captured response")
        status = (
            OpenSearchCaptureStatusV0222.PARSED
            if accepted
            else OpenSearchCaptureStatusV0222.REJECTED
        )
        updated = OpenSearchCapturedResponseV0222.model_validate(
            {
                **response.model_dump(mode="json"),
                "safe_parse_stage": safe_parse_stage,
                "safe_error_code": safe_error_code,
                "structural_summary_sha256": structural_summary_sha256,
                "status": status,
            }
        )
        self._append_event(
            request_id=request_id,
            status=status,
            payload=updated.model_dump(mode="json"),
        )
        self._responses[request_id] = updated
        return updated

    def capture_ledger(self) -> OpenSearchCaptureLedgerV0222:
        return OpenSearchCaptureLedgerV0222.build(
            session_id=self.session_id,
            events=tuple(self._events),
        )

    def build_bundle(self) -> OpenSearchSchemaCaptureBundleV0222:
        requests = tuple(
            sorted(self._requests.values(), key=lambda item: item.request_ordinal)
        )
        responses = tuple(
            self._responses[request.request_id]
            for request in requests
            if request.request_id in self._responses
        )
        refs_by_kind: dict[str, tuple[str, ...]] = {}
        for kind in _REQUIRED_CAPTURE_KINDS:
            refs_by_kind[kind] = tuple(
                response.response_object_ref
                for response in responses
                if response.request_kind.value == kind
            )
        missing = tuple(sorted(kind for kind, refs in refs_by_kind.items() if not refs))
        bundle = OpenSearchSchemaCaptureBundleV0222.build(
            session_id=self.session_id,
            requests=requests,
            responses=responses,
            resolved_index_response_refs=refs_by_kind["INDEX_RESOLUTION"],
            mapping_response_refs=refs_by_kind["MAPPING"],
            field_caps_response_refs=refs_by_kind["FIELD_CAPS"],
            structural_sample_refs=refs_by_kind["STRUCTURAL_SAMPLE"],
            service_aggregation_refs=refs_by_kind["SERVICE_AGGREGATION"],
            timestamp_range_refs=refs_by_kind["TIMESTAMP_RANGE"],
            profile_verification_refs=refs_by_kind["PROFILE_VERIFICATION"],
            capture_completeness=not missing,
            missing_capture_kinds=missing,
            ledger_sha256=self.capture_ledger().ledger_sha256,
        )
        temporary = self.private_root / ".capture-bundle.product-v0222.tmp"
        temporary.write_text(
            json.dumps(
                bundle.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self._bundle_path)
        return bundle

    @staticmethod
    def load_bundle(*, private_root: Path) -> OpenSearchSchemaCaptureBundleV0222:
        path = Path(private_root) / "capture-bundle.json"
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("OpenSearch capture bundle is not a regular file")
        return OpenSearchSchemaCaptureBundleV0222.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def verify_content_addressed_objects(self) -> int:
        count = 0
        seen: set[str] = set()
        for response in self._responses.values():
            if response.response_object_ref not in seen:
                path = self.private_root / response.response_object_ref
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("OpenSearch captured object is not a regular file")
                content = path.read_bytes()
                if (
                    len(content) != response.response_byte_size
                    or hashlib.sha256(content).hexdigest() != response.response_sha256
                ):
                    raise ValueError("OpenSearch captured object verification differs")
                seen.add(response.response_object_ref)
            count += 1
        return count


__all__ = (
    "OpenSearchCapturedResponseV0222",
    "OpenSearchCaptureLedgerV0222",
    "OpenSearchCaptureRequestKindV0222",
    "OpenSearchCaptureRequestV0222",
    "OpenSearchCaptureStatusV0222",
    "OpenSearchCaptureStoreV0222",
    "OpenSearchPublicStructuralSummaryV0222",
    "OpenSearchSchemaCaptureBundleV0222",
    "build_public_structural_summary_v0222",
)
