"""Fail-closed contract for the manual Product live read-only acceptance."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


LIVE_READ_ONLY_TERMINAL = "ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS"
_REQUIRED_SOURCES = frozenset(
    {"PROMETHEUS", "OPENSEARCH", "JAEGER", "HTTP_HEALTH"}
)


class LiveReadOnlyAcceptanceV1(ProductModelV1):
    """Public, secret-free evidence for one evaluator-owned local acceptance."""

    schema_version: Literal["ecomsre.product.live-read-only-acceptance.v1"] = (
        "ecomsre.product.live-read-only-acceptance.v1"
    )
    terminal: Literal["ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS"]
    observed_at: datetime
    docker_context: str = Field(min_length=1, max_length=120)
    docker_daemon_id_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: dict[
        str,
        Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"],
    ]
    normalized_services: tuple[str, ...] = Field(min_length=10, max_length=20)
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_mode: Literal["DEMO_ONLY"]
    successful_baseline_windows: int = Field(ge=1, le=5)
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_terminal: Literal[
        "NO_INCIDENT",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
    ]
    evidence_object_count: int = Field(ge=1)
    evidence_refs_resolved: Literal[True]
    connector_raw_failures: int = Field(ge=0)
    explicit_source_failures: tuple[str, ...] = Field(max_length=20)
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    fault_injections: Literal[0]
    forward_mutations: Literal[0]
    product_cleanup: Literal["CLEAN"]
    demo_cleanup: Literal["CLEAN"]
    owned_containers_after_cleanup: Literal[0]
    owned_networks_after_cleanup: Literal[0]
    owned_volumes_after_cleanup: Literal[0]
    non_owned_resources_changed: Literal[False]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_complete_acceptance(self) -> "LiveReadOnlyAcceptanceV1":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("live acceptance timestamp must be canonical UTC")
        if set(self.sources) != _REQUIRED_SOURCES or any(
            value != "AVAILABLE" for value in self.sources.values()
        ):
            raise ValueError("live acceptance source availability is incomplete")
        if self.normalized_services != tuple(sorted(set(self.normalized_services))):
            raise ValueError("live acceptance services are not canonical")
        failures = tuple(sorted(set(self.explicit_source_failures)))
        if failures != self.explicit_source_failures:
            raise ValueError("explicit source failures are not canonical")
        if self.connector_raw_failures != len(failures):
            raise ValueError("connector failure count is not explicitly represented")
        if self.diagnosis_terminal == "NO_INCIDENT" and failures:
            raise ValueError("No-Incident cannot hide a connector source failure")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("live acceptance digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "LiveReadOnlyAcceptanceV1":
        payload = {
            "schema_version": "ecomsre.product.live-read-only-acceptance.v1",
            "terminal": LIVE_READ_ONLY_TERMINAL,
            **values,
        }
        draft = cls.model_construct(**payload, report_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"report_sha256"})
        return cls.model_validate(
            {**serialized, "report_sha256": semantic_sha256_v22(serialized)}
        )


__all__ = ("LIVE_READ_ONLY_TERMINAL", "LiveReadOnlyAcceptanceV1")
