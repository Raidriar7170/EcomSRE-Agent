"""Explicit per-source replay target-completeness metadata for DTA v2.2.5."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22


class ReplayTargetCoverageModeV225(str, Enum):
    TARGET_COMPLETE = "TARGET_COMPLETE"
    TARGET_PARTIAL = "TARGET_PARTIAL"


class ReplayTargetCoverageV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.replay-target-coverage.v1"]
    source: EvidenceSourceV22
    candidate_services: tuple[str, ...]
    covered_target_services: tuple[str, ...]
    coverage_mode: ReplayTargetCoverageModeV225
    coverage_sha256: str

    @model_validator(mode="after")
    def require_coverage(self) -> "ReplayTargetCoverageV225":
        for values in (self.candidate_services, self.covered_target_services):
            if values != tuple(sorted(set(values))):
                raise ValueError("replay target coverage services are not canonical")
        if not 1 <= len(self.candidate_services) <= 4:
            raise ValueError("replay target coverage requires one to four candidates")
        if not set(self.covered_target_services).issubset(self.candidate_services):
            raise ValueError("covered replay target is not a candidate")
        expected_mode = (
            ReplayTargetCoverageModeV225.TARGET_COMPLETE
            if self.covered_target_services == self.candidate_services
            else ReplayTargetCoverageModeV225.TARGET_PARTIAL
        )
        if self.coverage_mode is not expected_mode:
            raise ValueError("replay target coverage mode differs from covered targets")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"coverage_sha256"})
        )
        if self.coverage_sha256 != expected:
            raise ValueError("replay target coverage digest differs")
        return self


class ReplayCaseTargetCoverageV225(DtaModelV22):
    case_id: str
    sources: tuple[ReplayTargetCoverageV225, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def require_case(self) -> "ReplayCaseTargetCoverageV225":
        if tuple(item.source for item in self.sources) != tuple(EvidenceSourceV22):
            raise ValueError("case target coverage sources are incomplete or noncanonical")
        candidates = {item.candidate_services for item in self.sources}
        if len(candidates) != 1:
            raise ValueError("case target coverage candidate sets differ by source")
        return self

    def require(self, source: EvidenceSourceV22) -> ReplayTargetCoverageV225:
        return next(item for item in self.sources if item.source is source)


class ReplayTargetCoverageSetV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.replay-target-coverage-set.v1"]
    cases: tuple[ReplayCaseTargetCoverageV225, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_set(self) -> "ReplayTargetCoverageSetV225":
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("target coverage case set is not canonical")
        return self

    def require(self, case_id: str) -> ReplayCaseTargetCoverageV225:
        item = next((item for item in self.cases if item.case_id == case_id), None)
        if item is None:
            raise ValueError("target coverage case is absent")
        return item


def load_replay_target_coverage_set_v225(path: Path) -> ReplayTargetCoverageSetV225:
    return ReplayTargetCoverageSetV225.model_validate_json(path.read_bytes())


def build_replay_target_coverage_v225(
    *,
    source: EvidenceSourceV22,
    candidate_services: tuple[str, ...],
    covered_target_services: tuple[str, ...],
) -> ReplayTargetCoverageV225:
    for values in (candidate_services, covered_target_services):
        if values != tuple(sorted(set(values))):
            raise ValueError("replay target coverage services must be canonical")
    mode = (
        ReplayTargetCoverageModeV225.TARGET_COMPLETE
        if covered_target_services == candidate_services
        else ReplayTargetCoverageModeV225.TARGET_PARTIAL
    )
    payload = {
        "schema_version": "dta-v22.5.replay-target-coverage.v1",
        "source": source,
        "candidate_services": candidate_services,
        "covered_target_services": covered_target_services,
        "coverage_mode": mode,
    }
    return ReplayTargetCoverageV225(
        **payload,
        coverage_sha256=semantic_sha256_v22(payload),
    )


def normal_resource_record_v225(*, service: str) -> ResourceUsageRecordV22:
    """Return an explicit normal five-sample record below frozen strong thresholds."""

    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=20.0,
                memory_bytes=100_000_000,
            )
            for offset in (0, 2_500, 5_000, 7_500, 10_000)
        ),
        memory_slope_bytes_per_second=0.0,
    )


def complete_resource_records_v225(
    *,
    candidate_services: tuple[str, ...],
    records: tuple[ResourceUsageRecordV22, ...],
) -> tuple[ResourceUsageRecordV22, ...]:
    if candidate_services != tuple(sorted(set(candidate_services))):
        raise ValueError("candidate services must be canonical")
    by_service = {item.service: item for item in records}
    if len(by_service) != len(records):
        raise ValueError("resource records contain duplicate targets")
    if not set(by_service).issubset(candidate_services):
        raise ValueError("resource record target is not a candidate")
    return tuple(
        by_service.get(service) or normal_resource_record_v225(service=service)
        for service in candidate_services
    )


def require_capture_matches_target_coverage_v225(
    *, coverage: ReplayTargetCoverageV225, capture: ReplayCaptureV22
) -> None:
    coverage = ReplayTargetCoverageV225.model_validate(
        coverage.model_dump(mode="python")
    )
    capture = ReplayCaptureV22.model_validate(capture.model_dump(mode="python"))
    if coverage.source is not EvidenceSourceV22.RESOURCES:
        return
    captured = tuple(
        sorted(
            item.service
            for item in capture.resources
            if item.service in set(coverage.candidate_services)
        )
    )
    if captured != coverage.covered_target_services:
        label = (
            "TARGET_COMPLETE Resources coverage"
            if coverage.coverage_mode is ReplayTargetCoverageModeV225.TARGET_COMPLETE
            else "TARGET_PARTIAL Resources coverage"
        )
        raise ValueError(f"{label} contradicts replay resource records")


__all__ = (
    "ReplayCaseTargetCoverageV225",
    "ReplayTargetCoverageModeV225",
    "ReplayTargetCoverageSetV225",
    "ReplayTargetCoverageV225",
    "build_replay_target_coverage_v225",
    "complete_resource_records_v225",
    "load_replay_target_coverage_set_v225",
    "normal_resource_record_v225",
    "require_capture_matches_target_coverage_v225",
)
