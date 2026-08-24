"""Private physical and public opaque capture contracts for the real-fault study."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import json
import re
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.opaque_identity_v225 import OpaqueServiceIdV225
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    LogRecordV22,
    MetricFactV22,
    RecentChangeRecordV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22


_KNOWN_PHYSICAL_SERVICES = (
    "ad",
    "astronomy-db",
    "cart",
    "checkout",
    "currency",
    "email",
    "flagd",
    "flagd-ui",
    "frontend",
    "frontend-proxy",
    "grafana",
    "image-provider",
    "jaeger",
    "load-generator",
    "opamp-server",
    "opensearch",
    "otel-collector",
    "payment",
    "product-catalog",
    "prometheus",
    "quote",
    "recommendation",
    "shipping",
    "telemetry-docs",
    "valkey-cart",
)


class RealFaultCaseKind(str, Enum):
    BASELINE = "BASELINE"
    AD_CPU_FAULT = "AD_CPU_FAULT"


class RealFaultSourceWindowV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.source-window.v1"]
    captured_at: datetime
    metrics_lookback_seconds: Literal[60]
    logs_lookback_seconds: Literal[300]
    traces_lookback_seconds: Literal[300]
    resources_sampling_window_seconds: Literal[10]
    resources_sample_count: Literal[5]
    source_window_sha256: str

    @model_validator(mode="after")
    def require_window(self) -> RealFaultSourceWindowV1:
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() != timedelta(0):
            raise ValueError("real-fault capture timestamp must be UTC")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"source_window_sha256"})
        )
        if self.source_window_sha256 != expected:
            raise ValueError("real-fault source window digest differs")
        return self


class PhysicalServiceBindingV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.physical-service-binding.v1"]
    fault_service: Literal["ad"]
    comparator_service: str = Field(pattern=r"^(?:email|product-catalog|recommendation)$")
    binding_sha256: str

    @model_validator(mode="after")
    def require_binding(self) -> PhysicalServiceBindingV1:
        if self.fault_service == self.comparator_service:
            raise ValueError("real-fault physical services must differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("real-fault physical binding digest differs")
        return self

    @property
    def services(self) -> tuple[str, str]:
        return tuple(sorted((self.fault_service, self.comparator_service)))  # type: ignore[return-value]


class RealFaultAliasBindingV1(DtaModelV22):
    alias: OpaqueServiceIdV225
    physical_service: str = Field(pattern=r"^(?:ad|email|product-catalog|recommendation)$")


class RealFaultAliasMapV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.alias-map.v1"]
    map_name: Literal["MAP_A", "MAP_B"]
    bindings: tuple[RealFaultAliasBindingV1, RealFaultAliasBindingV1]
    map_sha256: str

    @model_validator(mode="after")
    def require_map(self) -> RealFaultAliasMapV1:
        aliases = tuple(item.alias for item in self.bindings)
        physical = tuple(item.physical_service for item in self.bindings)
        if aliases != tuple(sorted(set(aliases))) or len(set(physical)) != 2:
            raise ValueError("real-fault alias map is not canonical and bijective")
        if "ad" not in physical:
            raise ValueError("real-fault alias map lacks the Ad/comparator pair")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"map_sha256"})
        )
        if self.map_sha256 != expected:
            raise ValueError("real-fault alias map digest differs")
        return self

    def physical_for(self, alias: str) -> str:
        matches = tuple(item.physical_service for item in self.bindings if item.alias == alias)
        if len(matches) != 1:
            raise ValueError("opaque alias is outside the frozen map")
        return matches[0]

    def alias_for(self, physical_service: str) -> str:
        matches = tuple(item.alias for item in self.bindings if item.physical_service == physical_service)
        if len(matches) != 1:
            raise ValueError("physical service is outside the frozen map")
        return matches[0]


class RealFaultPhysicalCaptureV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.physical-capture.v1"]
    campaign_id: str = Field(min_length=1, max_length=128)
    kind: RealFaultCaseKind
    binding: PhysicalServiceBindingV1
    source_window: RealFaultSourceWindowV1
    capture: ReplayCaptureV22
    owned_local_capture: Literal[True]
    physical_capture_sha256: str

    @model_validator(mode="after")
    def require_physical_capture(self) -> RealFaultPhysicalCaptureV1:
        if self.capture.captured_at != self.source_window.captured_at:
            raise ValueError("physical capture timestamp differs from its source window")
        for values, label in (
            (tuple(item.service for item in self.capture.runtime), "Runtime"),
            (tuple(item.service for item in self.capture.resources), "Resources"),
        ):
            if tuple(sorted(values)) != self.binding.services:
                raise ValueError(f"physical {label} capture is not target-complete")
        visible_services = {
            *(item.service for item in self.capture.metrics),
            *(item.service for item in self.capture.logs),
            *(item.service for item in self.capture.traces),
            *(item.service for item in self.capture.runtime),
            *(item.service for item in self.capture.resources),
            *(item.service for item in self.capture.changes),
        }
        if not visible_services.issubset(set(self.binding.services)):
            raise ValueError("physical capture includes a service outside the frozen pair")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"physical_capture_sha256"})
        )
        if self.physical_capture_sha256 != expected:
            raise ValueError("physical capture digest differs")
        return self


class RealFaultOpaqueCaptureV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.opaque-capture.v1"]
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    alias_map_name: Literal["MAP_A", "MAP_B"]
    candidate_aliases: tuple[OpaqueServiceIdV225, OpaqueServiceIdV225]
    source_window: RealFaultSourceWindowV1
    capture: ReplayCaptureV22
    provenance: Literal["OWNED_LOCAL_CAPTURE"]
    physical_capture_sha256: str
    opaque_capture_sha256: str

    @model_validator(mode="after")
    def require_opaque_capture(self) -> RealFaultOpaqueCaptureV1:
        if self.candidate_aliases != tuple(sorted(set(self.candidate_aliases))):
            raise ValueError("opaque candidate aliases are not canonical and unique")
        services = {
            *(item.service for item in self.capture.metrics),
            *(item.service for item in self.capture.logs),
            *(item.service for item in self.capture.traces),
            *(item.service for item in self.capture.runtime),
            *(item.service for item in self.capture.resources),
            *(item.service for item in self.capture.changes),
        }
        if not services.issubset(set(self.candidate_aliases)):
            raise ValueError("opaque capture includes a service outside candidate aliases")
        if tuple(sorted(item.service for item in self.capture.runtime)) != self.candidate_aliases:
            raise ValueError("opaque Runtime capture is not target-complete")
        if tuple(sorted(item.service for item in self.capture.resources)) != self.candidate_aliases:
            raise ValueError("opaque Resources capture is not target-complete")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"opaque_capture_sha256"})
        )
        if self.opaque_capture_sha256 != expected:
            raise ValueError("opaque capture digest differs")
        return self


class RealFaultCapturePairV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.capture-pair.v1"]
    baseline_physical_capture_sha256: str
    fault_physical_capture_sha256: str
    cases: tuple[
        RealFaultOpaqueCaptureV1,
        RealFaultOpaqueCaptureV1,
        RealFaultOpaqueCaptureV1,
        RealFaultOpaqueCaptureV1,
    ]
    pair_sha256: str

    @model_validator(mode="after")
    def require_pair(self) -> RealFaultCapturePairV1:
        if tuple(item.case_id for item in self.cases) != (
            "fault-map-a",
            "fault-map-b",
            "baseline-map-a",
            "baseline-map-b",
        ):
            raise ValueError("real-fault opaque case order differs")
        if {item.physical_capture_sha256 for item in self.cases[:2]} != {
            self.fault_physical_capture_sha256
        } or {item.physical_capture_sha256 for item in self.cases[2:]} != {
            self.baseline_physical_capture_sha256
        }:
            raise ValueError("opaque cases do not bind the two physical captures")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"pair_sha256"})
        )
        if self.pair_sha256 != expected:
            raise ValueError("real-fault capture pair digest differs")
        return self


class RealFaultBootstrapV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.bootstrap.v1"]
    candidate_aliases: tuple[OpaqueServiceIdV225, OpaqueServiceIdV225]
    runtime: tuple[RuntimeRecordV22, RuntimeRecordV22]
    metrics: tuple[MetricFactV22, ...]
    bootstrap_sha256: str

    @model_validator(mode="after")
    def require_bootstrap(self) -> RealFaultBootstrapV1:
        if tuple(sorted(item.service for item in self.runtime)) != self.candidate_aliases:
            raise ValueError("common bootstrap Runtime is not target-complete")
        if not {item.service for item in self.metrics}.issubset(set(self.candidate_aliases)):
            raise ValueError("common bootstrap Metrics escape candidate aliases")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"bootstrap_sha256"})
        )
        if self.bootstrap_sha256 != expected:
            raise ValueError("common bootstrap digest differs")
        return self


def build_source_window_v225(*, captured_at: datetime) -> RealFaultSourceWindowV1:
    payload = {
        "schema_version": "dta-v225-real-fault.source-window.v1",
        "captured_at": captured_at,
        "metrics_lookback_seconds": 60,
        "logs_lookback_seconds": 300,
        "traces_lookback_seconds": 300,
        "resources_sampling_window_seconds": 10,
        "resources_sample_count": 5,
    }
    draft = cast(Any, RealFaultSourceWindowV1).model_construct(
        **payload, source_window_sha256="0" * 64
    )
    return RealFaultSourceWindowV1.model_validate(
        {
            **payload,
            "source_window_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"source_window_sha256"})
            ),
        }
    )


def _physical_binding(*, fault_service: str, comparator_service: str) -> PhysicalServiceBindingV1:
    payload = {
        "schema_version": "dta-v225-real-fault.physical-service-binding.v1",
        "fault_service": fault_service,
        "comparator_service": comparator_service,
    }
    draft = cast(Any, PhysicalServiceBindingV1).model_construct(
        **payload, binding_sha256="0" * 64
    )
    return PhysicalServiceBindingV1.model_validate(
        {
            **payload,
            "binding_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"binding_sha256"})
            ),
        }
    )


def _alias_map(name: Literal["MAP_A", "MAP_B"], bindings: tuple[RealFaultAliasBindingV1, RealFaultAliasBindingV1]) -> RealFaultAliasMapV1:
    payload = {
        "schema_version": "dta-v225-real-fault.alias-map.v1",
        "map_name": name,
        "bindings": tuple(sorted(bindings, key=lambda item: item.alias)),
    }
    draft = cast(Any, RealFaultAliasMapV1).model_construct(
        **payload, map_sha256="0" * 64
    )
    return RealFaultAliasMapV1.model_validate(
        {
            **payload,
            "map_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"map_sha256"})
            ),
        }
    )


def build_alias_maps_v225(
    *, fault_service: str, comparator_service: str, aliases: tuple[str, str]
) -> tuple[RealFaultAliasMapV1, RealFaultAliasMapV1]:
    if fault_service != "ad" or comparator_service not in {
        "email",
        "product-catalog",
        "recommendation",
    }:
        raise ValueError("real-fault physical pair is outside the frozen policy")
    aliases = tuple(sorted(aliases))  # type: ignore[assignment]
    if len(set(aliases)) != 2:
        raise ValueError("real-fault aliases must be unique")
    left, right = aliases
    return (
        _alias_map(
            "MAP_A",
            (
                RealFaultAliasBindingV1(alias=left, physical_service=fault_service),
                RealFaultAliasBindingV1(alias=right, physical_service=comparator_service),
            ),
        ),
        _alias_map(
            "MAP_B",
            (
                RealFaultAliasBindingV1(alias=left, physical_service=comparator_service),
                RealFaultAliasBindingV1(alias=right, physical_service=fault_service),
            ),
        ),
    )


def build_physical_capture_v225(
    *,
    campaign_id: str,
    kind: RealFaultCaseKind,
    fault_service: str,
    comparator_service: str,
    source_window: RealFaultSourceWindowV1,
    capture: ReplayCaptureV22,
) -> RealFaultPhysicalCaptureV1:
    payload = {
        "schema_version": "dta-v225-real-fault.physical-capture.v1",
        "campaign_id": campaign_id,
        "kind": kind,
        "binding": _physical_binding(
            fault_service=fault_service, comparator_service=comparator_service
        ),
        "source_window": source_window,
        "capture": capture,
        "owned_local_capture": True,
    }
    draft = cast(Any, RealFaultPhysicalCaptureV1).model_construct(
        **payload, physical_capture_sha256="0" * 64
    )
    return RealFaultPhysicalCaptureV1.model_validate(
        {
            **payload,
            "physical_capture_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"physical_capture_sha256"})
            ),
        }
    )


def _replace_text(value: str, mapping: dict[str, str]) -> str:
    for physical in sorted(mapping, key=len, reverse=True):
        value = re.sub(
            rf"(?<![a-z0-9]){re.escape(physical)}(?![a-z0-9])",
            mapping[physical],
            value,
            flags=re.IGNORECASE,
        )
    return value


def _render_capture(capture: ReplayCaptureV22, alias_map: RealFaultAliasMapV1) -> ReplayCaptureV22:
    mapping = {item.physical_service: item.alias for item in alias_map.bindings}
    return ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=capture.captured_at,
        metrics=tuple(sorted((
            MetricFactV22.model_validate(
                {**item.model_dump(mode="python"), "service": mapping[item.service]}
            )
            for item in capture.metrics
        ), key=lambda item: (item.service, item.metric_kind.value))),
        logs=tuple(sorted((
            LogRecordV22.model_validate(
                {
                    **item.model_dump(mode="python"),
                    "service": mapping[item.service],
                    "message": _replace_text(item.message, mapping),
                }
            )
            for item in capture.logs
        ), key=lambda item: (item.service, item.observed_at, item.severity, item.message))),
        traces=tuple(sorted((
            TraceSpanV22.model_validate(
                {
                    **item.model_dump(mode="python"),
                    "service_path": tuple(mapping[value] for value in item.service_path),
                    "service": mapping[item.service],
                    "parent_service": None if item.parent_service is None else mapping[item.parent_service],
                    "operation": _replace_text(item.operation, mapping),
                }
            )
            for item in capture.traces
        ), key=lambda item: (item.service, item.observed_at, item.operation))),
        runtime=tuple(sorted((
            RuntimeRecordV22.model_validate(
                {**item.model_dump(mode="python"), "service": mapping[item.service]}
            )
            for item in capture.runtime
        ), key=lambda item: item.service)),
        resources=tuple(sorted((
            ResourceUsageRecordV22.model_validate(
                {**item.model_dump(mode="python"), "service": mapping[item.service]}
            )
            for item in capture.resources
        ), key=lambda item: item.service)),
        changes=tuple(sorted((
            RecentChangeRecordV22.model_validate(
                {**item.model_dump(mode="python"), "service": mapping[item.service]}
            )
            for item in capture.changes
        ), key=lambda item: (item.service, item.observed_at, item.opaque_change_id))),
        source_failures=capture.source_failures,
    )


def build_opaque_capture_v225(
    *, case_id: str, physical_capture: RealFaultPhysicalCaptureV1, alias_map: RealFaultAliasMapV1
) -> RealFaultOpaqueCaptureV1:
    expected_prefix = "baseline" if physical_capture.kind is RealFaultCaseKind.BASELINE else "fault"
    expected_suffix = "a" if alias_map.map_name == "MAP_A" else "b"
    if case_id != f"{expected_prefix}-map-{expected_suffix}":
        raise ValueError("opaque case ID differs from physical state and alias map")
    aliases = tuple(sorted(item.alias for item in alias_map.bindings))
    payload = {
        "schema_version": "dta-v225-real-fault.opaque-capture.v1",
        "case_id": case_id,
        "alias_map_name": alias_map.map_name,
        "candidate_aliases": aliases,
        "source_window": physical_capture.source_window,
        "capture": _render_capture(physical_capture.capture, alias_map),
        "provenance": "OWNED_LOCAL_CAPTURE",
        "physical_capture_sha256": physical_capture.physical_capture_sha256,
    }
    draft = cast(Any, RealFaultOpaqueCaptureV1).model_construct(
        **payload, opaque_capture_sha256="0" * 64
    )
    result = RealFaultOpaqueCaptureV1.model_validate(
        {
            **payload,
            "opaque_capture_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"opaque_capture_sha256"})
            ),
        }
    )
    require_public_capture_opaque_v225(result)
    return result


def build_capture_pair_v225(
    *,
    baseline: RealFaultPhysicalCaptureV1,
    fault: RealFaultPhysicalCaptureV1,
    cases: tuple[
        RealFaultOpaqueCaptureV1,
        RealFaultOpaqueCaptureV1,
        RealFaultOpaqueCaptureV1,
        RealFaultOpaqueCaptureV1,
    ],
) -> RealFaultCapturePairV1:
    payload = {
        "schema_version": "dta-v225-real-fault.capture-pair.v1",
        "baseline_physical_capture_sha256": baseline.physical_capture_sha256,
        "fault_physical_capture_sha256": fault.physical_capture_sha256,
        "cases": cases,
    }
    draft = cast(Any, RealFaultCapturePairV1).model_construct(
        **payload, pair_sha256="0" * 64
    )
    return RealFaultCapturePairV1.model_validate(
        {
            **payload,
            "pair_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"pair_sha256"})
            ),
        }
    )


def truth_root_alias_v225(*, alias_map: RealFaultAliasMapV1, kind: RealFaultCaseKind) -> str | None:
    return None if kind is RealFaultCaseKind.BASELINE else alias_map.alias_for("ad")


def build_common_bootstrap_v225(capture: RealFaultOpaqueCaptureV1) -> RealFaultBootstrapV1:
    payload = {
        "schema_version": "dta-v225-real-fault.bootstrap.v1",
        "candidate_aliases": capture.candidate_aliases,
        "runtime": tuple(sorted(capture.capture.runtime, key=lambda item: item.service)),
        "metrics": tuple(
            sorted(capture.capture.metrics, key=lambda item: (item.service, item.metric_kind.value))
        ),
    }
    draft = cast(Any, RealFaultBootstrapV1).model_construct(
        **payload, bootstrap_sha256="0" * 64
    )
    return RealFaultBootstrapV1.model_validate(
        {
            **payload,
            "bootstrap_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"bootstrap_sha256"})
            ),
        }
    )


def require_public_capture_opaque_v225(capture: RealFaultOpaqueCaptureV1) -> None:
    raw = capture.model_dump_json().casefold()
    for service in _KNOWN_PHYSICAL_SERVICES:
        if re.search(rf"(?<![a-z0-9]){re.escape(service)}(?![a-z0-9])", raw):
            raise ValueError("public capture contains a physical service identity")
    for value in (
        *(item.message for item in capture.capture.logs),
        *(item.operation for item in capture.capture.traces),
    ):
        lowered = value.casefold()
        if (
            re.search(r"(?:^|[\s=:'\"])(?:/users/|/home/|/var/run/|~/)", lowered)
            or re.search(r"\b[0-9a-f]{12,64}\b", lowered)
            or any(
                marker in lowered
                for marker in (
                    "container_id",
                    "container id",
                    "docker://",
                    "private://",
                    ".ecomsre/",
                )
            )
        ):
            raise ValueError("public capture text contains a private runtime identity")


def require_provider_payload_opaque_v225(value: object) -> None:
    if hasattr(value, "model_dump"):
        value = cast(Any, value).model_dump(mode="json")
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).casefold()
    for service in _KNOWN_PHYSICAL_SERVICES:
        if re.search(rf"(?<![a-z0-9]){re.escape(service)}(?![a-z0-9])", raw):
            raise ValueError("Provider payload contains a physical service identity")
    for marker in (
        "expected root",
        "fault target",
        "case truth",
        "physical service",
        "container_id",
        "private://",
        "fault-map",
        "baseline-map",
    ):
        if marker in raw:
            raise ValueError("Provider payload contains evaluator or private material")


__all__ = (
    "PhysicalServiceBindingV1",
    "RealFaultAliasMapV1",
    "RealFaultBootstrapV1",
    "RealFaultCapturePairV1",
    "RealFaultCaseKind",
    "RealFaultOpaqueCaptureV1",
    "RealFaultPhysicalCaptureV1",
    "RealFaultSourceWindowV1",
    "build_alias_maps_v225",
    "build_capture_pair_v225",
    "build_common_bootstrap_v225",
    "build_opaque_capture_v225",
    "build_physical_capture_v225",
    "build_source_window_v225",
    "require_public_capture_opaque_v225",
    "require_provider_payload_opaque_v225",
    "truth_root_alias_v225",
)
