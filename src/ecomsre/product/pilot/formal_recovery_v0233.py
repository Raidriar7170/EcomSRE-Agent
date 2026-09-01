"""Attempt-scoped recovery contracts for Product v0.2.3.3 formal execution."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.serialization_v0233 import (
    canonical_jsonable_v0233,
    semantic_json_sha256_v0233,
)
from ecomsre_live_sandbox.contracts import write_private_json


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ZERO_SHA256 = "0" * 64
_ATTEMPT_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"


class FormalExecutionStateV0233(str, Enum):
    PREPARED = "PREPARED"
    TRAFFIC_PREFLIGHT_PASS = "TRAFFIC_PREFLIGHT_PASS"
    FORMAL_ENVIRONMENT_READY = "FORMAL_ENVIRONMENT_READY"
    FORMAL_TRAFFIC_RUNNING = "FORMAL_TRAFFIC_RUNNING"
    FORMAL_TRAFFIC_PASS = "FORMAL_TRAFFIC_PASS"
    LIVE_CAPTURE_SEALED = "LIVE_CAPTURE_SEALED"
    INCIDENT_CREATED = "INCIDENT_CREATED"
    ACQUISITION_SEALED = "ACQUISITION_SEALED"
    DIAGNOSIS_RUNNING = "DIAGNOSIS_RUNNING"
    DIAGNOSIS_PERSISTED = "DIAGNOSIS_PERSISTED"
    SCORED = "SCORED"
    CLOSED = "CLOSED"
    RECOVERABLE_FAILURE = "RECOVERABLE_FAILURE"
    NONRECOVERABLE_FAILURE = "NONRECOVERABLE_FAILURE"


_FORWARD_TRANSITION: dict[FormalExecutionStateV0233, FormalExecutionStateV0233] = {
    FormalExecutionStateV0233.PREPARED: (
        FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY
    ),
    FormalExecutionStateV0233.TRAFFIC_PREFLIGHT_PASS: (
        FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY
    ),
    FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY: (
        FormalExecutionStateV0233.FORMAL_TRAFFIC_RUNNING
    ),
    FormalExecutionStateV0233.FORMAL_TRAFFIC_RUNNING: (
        FormalExecutionStateV0233.FORMAL_TRAFFIC_PASS
    ),
    FormalExecutionStateV0233.FORMAL_TRAFFIC_PASS: (
        FormalExecutionStateV0233.LIVE_CAPTURE_SEALED
    ),
    FormalExecutionStateV0233.LIVE_CAPTURE_SEALED: (
        FormalExecutionStateV0233.INCIDENT_CREATED
    ),
    FormalExecutionStateV0233.INCIDENT_CREATED: (
        FormalExecutionStateV0233.ACQUISITION_SEALED
    ),
    FormalExecutionStateV0233.ACQUISITION_SEALED: (
        FormalExecutionStateV0233.DIAGNOSIS_RUNNING
    ),
    FormalExecutionStateV0233.DIAGNOSIS_RUNNING: (
        FormalExecutionStateV0233.DIAGNOSIS_PERSISTED
    ),
    FormalExecutionStateV0233.DIAGNOSIS_PERSISTED: FormalExecutionStateV0233.SCORED,
    FormalExecutionStateV0233.SCORED: FormalExecutionStateV0233.CLOSED,
}

_RECOVERY_RESUME_STATES = frozenset(_FORWARD_TRANSITION)


def _sorted_sha256_mapping(value: Mapping[str, str]) -> dict[str, str]:
    normalized = dict(sorted(value.items()))
    if any(
        not path
        or path.startswith("/")
        or ".." in Path(path).parts
        or len(digest) != 64
        for path, digest in normalized.items()
    ):
        raise ValueError("Product v0.2.3.3 artifact SHA mapping differs")
    return normalized


def _sorted_coverage_mapping(
    value: Mapping[str, tuple[str, ...] | list[str]],
) -> dict[str, tuple[str, ...]]:
    return {
        source: tuple(sorted(set(services)))
        for source, services in sorted(value.items())
    }


class FormalSemanticSurfaceV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-semantic-surface.v0233"] = (
        "ecomsre.product.formal-semantic-surface.v0233"
    )
    semantic_generation: int = Field(ge=1)
    checkout_traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_traffic_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_authority_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    service_identity_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    capability_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_source_sha256_by_path: dict[str, str]
    nofault_scorer_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage_journal_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_surface_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_canonical_semantic_surface(self) -> FormalSemanticSurfaceV0233:
        if (
            not self.diagnosis_source_sha256_by_path
            or self.diagnosis_source_sha256_by_path
            != _sorted_sha256_mapping(self.diagnosis_source_sha256_by_path)
            or self.semantic_surface_sha256
            != semantic_sha256_v22(
                self.model_dump(mode="json", exclude={"semantic_surface_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 semantic surface differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalSemanticSurfaceV0233:
        body = {
            "schema_version": "ecomsre.product.formal-semantic-surface.v0233",
            **payload,
            "diagnosis_source_sha256_by_path": _sorted_sha256_mapping(
                payload["diagnosis_source_sha256_by_path"]
            ),
        }
        return cls.model_validate(
            {
                **body,
                "semantic_surface_sha256": semantic_json_sha256_v0233(body),
            }
        )


class FormalOperationalSurfaceV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-operational-surface.v0233"] = (
        "ecomsre.product.formal-operational-surface.v0233"
    )
    operational_file_sha256_by_path: dict[str, str]
    operational_surface_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_canonical_operational_surface(self) -> FormalOperationalSurfaceV0233:
        if (
            not self.operational_file_sha256_by_path
            or self.operational_file_sha256_by_path
            != _sorted_sha256_mapping(self.operational_file_sha256_by_path)
            or self.operational_surface_sha256
            != semantic_sha256_v22(
                self.model_dump(mode="json", exclude={"operational_surface_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 operational surface differs")
        return self

    @classmethod
    def build(
        cls, *, operational_file_sha256_by_path: Mapping[str, str]
    ) -> FormalOperationalSurfaceV0233:
        body = {
            "schema_version": "ecomsre.product.formal-operational-surface.v0233",
            "operational_file_sha256_by_path": _sorted_sha256_mapping(
                operational_file_sha256_by_path
            ),
        }
        return cls.model_validate(
            {
                **body,
                "operational_surface_sha256": semantic_json_sha256_v0233(body),
            }
        )


class LiveCaptureBundleV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.live-capture-bundle.v0233"] = (
        "ecomsre.product.live-capture-bundle.v0233"
    )
    campaign_id: str = Field(pattern=_ATTEMPT_PATTERN)
    semantic_generation: int = Field(ge=1)
    attempt_id: str = Field(pattern=_ATTEMPT_PATTERN)
    formal_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_authority_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_restart_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_traffic_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    episode_started_at: datetime
    episode_ended_at: datetime
    fresh_runtime_snapshot_raw: dict[str, Any]
    fresh_runtime_snapshot_raw_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_connector_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    queue_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_before_sha256: str = Field(pattern=_SHA256_PATTERN)
    outer_baseline_after_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    service_identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    capability_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_surface_sha256: str = Field(pattern=_SHA256_PATTERN)
    live_capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_closed_live_capture(self) -> LiveCaptureBundleV0233:
        if (
            self.episode_started_at.tzinfo is None
            or self.episode_ended_at.tzinfo is None
            or self.episode_ended_at < self.episode_started_at
            or self.queue_before_sha256 != self.queue_after_sha256
            or self.outer_baseline_before_sha256 != self.outer_baseline_after_sha256
            or self.fresh_runtime_snapshot_raw_sha256
            != semantic_json_sha256_v0233(self.fresh_runtime_snapshot_raw)
            or self.live_capture_bundle_sha256
            != semantic_sha256_v22(
                self.model_dump(
                    mode="json", exclude={"live_capture_bundle_sha256"}
                )
            )
        ):
            raise ValueError("Product v0.2.3.3 live capture bundle differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> LiveCaptureBundleV0233:
        body = canonical_jsonable_v0233(
            {
                "schema_version": "ecomsre.product.live-capture-bundle.v0233",
                **payload,
                "fresh_runtime_snapshot_raw_sha256": semantic_json_sha256_v0233(
                    payload["fresh_runtime_snapshot_raw"]
                ),
            }
        )
        return cls.model_validate(
            {
                **body,
                "live_capture_bundle_sha256": semantic_json_sha256_v0233(body),
            }
        )


class DiagnosisAcquisitionCheckpointV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.diagnosis-acquisition-checkpoint.v0233"
    ] = "ecomsre.product.diagnosis-acquisition-checkpoint.v0233"
    campaign_id: str = Field(pattern=_ATTEMPT_PATTERN)
    semantic_generation: int = Field(ge=1)
    attempt_id: str = Field(pattern=_ATTEMPT_PATTERN)
    incident_id: str = Field(pattern=r"^inc-[a-zA-Z0-9-]{1,120}$")
    incident_sha256: str = Field(pattern=_SHA256_PATTERN)
    incident_observation_started_at: datetime
    incident_observation_ended_at: datetime
    baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    service_identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    capability_sha256: str = Field(pattern=_SHA256_PATTERN)
    connector_query_results: tuple[dict[str, Any], ...]
    connector_provenance_bindings: tuple[dict[str, Any], ...]
    runtime_snapshot_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_coverage: dict[str, tuple[str, ...]]
    capability_limitations: tuple[str, ...]
    capability_observations: tuple[dict[str, Any], ...]
    limitation_candidates: tuple[dict[str, Any], ...]
    read_snapshots: tuple[dict[str, Any], ...]
    read_snapshot_sha256s: dict[str, str]
    semantic_surface_sha256: str = Field(pattern=_SHA256_PATTERN)
    acquisition_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_acquisition(self) -> DiagnosisAcquisitionCheckpointV0233:
        if (
            self.incident_observation_started_at.tzinfo is None
            or self.incident_observation_ended_at.tzinfo is None
            or self.incident_observation_ended_at
            < self.incident_observation_started_at
            or not self.connector_query_results
            or not self.connector_provenance_bindings
            or self.source_coverage
            != _sorted_coverage_mapping(self.source_coverage)
            or self.capability_limitations
            != tuple(sorted(set(self.capability_limitations)))
            or not self.read_snapshots
            or self.read_snapshot_sha256s
            != _sorted_sha256_mapping(self.read_snapshot_sha256s)
            or self.read_snapshot_sha256s
            != {
                f"read-snapshot-{ordinal:03d}.json": semantic_json_sha256_v0233(
                    snapshot
                )
                for ordinal, snapshot in enumerate(self.read_snapshots)
            }
            or self.acquisition_sha256
            != semantic_sha256_v22(
                self.model_dump(mode="json", exclude={"acquisition_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 Diagnosis acquisition differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> DiagnosisAcquisitionCheckpointV0233:
        body = canonical_jsonable_v0233(
            {
                "schema_version": (
                    "ecomsre.product.diagnosis-acquisition-checkpoint.v0233"
                ),
                **payload,
                "source_coverage": _sorted_coverage_mapping(
                    payload["source_coverage"]
                ),
                "capability_limitations": sorted(
                    set(payload["capability_limitations"])
                ),
                "read_snapshot_sha256s": _sorted_sha256_mapping(
                    payload["read_snapshot_sha256s"]
                ),
            }
        )
        return cls.model_validate(
            {**body, "acquisition_sha256": semantic_json_sha256_v0233(body)}
        )


class FormalExecutionCheckpointV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-execution-checkpoint.v0233"] = (
        "ecomsre.product.formal-execution-checkpoint.v0233"
    )
    campaign_id: str = Field(pattern=_ATTEMPT_PATTERN)
    semantic_generation: int = Field(ge=1)
    attempt_id: str = Field(pattern=_ATTEMPT_PATTERN)
    sequence: int = Field(ge=1)
    state: FormalExecutionStateV0233
    semantic_surface_sha256: str = Field(pattern=_SHA256_PATTERN)
    operational_surface_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    input_artifact_sha256s: dict[str, str]
    output_artifact_sha256s: dict[str, str]
    created_at: datetime
    previous_checkpoint_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkpoint_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_canonical_checkpoint(self) -> FormalExecutionCheckpointV0233:
        first = (
            self.sequence == 1
            and self.state is FormalExecutionStateV0233.PREPARED
            and self.previous_checkpoint_sha256 == _ZERO_SHA256
        )
        later = self.sequence > 1 and self.previous_checkpoint_sha256 != _ZERO_SHA256
        if (
            self.created_at.tzinfo is None
            or not (first or later)
            or self.input_artifact_sha256s
            != _sorted_sha256_mapping(self.input_artifact_sha256s)
            or self.output_artifact_sha256s
            != _sorted_sha256_mapping(self.output_artifact_sha256s)
            or self.checkpoint_sha256
            != semantic_sha256_v22(
                self.model_dump(mode="json", exclude={"checkpoint_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 checkpoint differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        previous: FormalExecutionCheckpointV0233 | None,
        state: FormalExecutionStateV0233,
        created_at: datetime,
        campaign_id: str | None = None,
        semantic_generation: int | None = None,
        attempt_id: str | None = None,
        semantic_surface_sha256: str | None = None,
        operational_surface_sha256: str | None = None,
        source_selection_sha256: str | None = None,
        formal_clone_sha256: str | None = None,
        input_artifact_sha256s: Mapping[str, str] | None = None,
        output_artifact_sha256s: Mapping[str, str] | None = None,
    ) -> FormalExecutionCheckpointV0233:
        if previous is None:
            required = (
                campaign_id,
                semantic_generation,
                attempt_id,
                semantic_surface_sha256,
                operational_surface_sha256,
                source_selection_sha256,
            )
            if state is not FormalExecutionStateV0233.PREPARED or any(
                value is None for value in required
            ):
                raise ValueError("Product v0.2.3.3 first checkpoint differs")
            sequence = 1
            previous_sha256 = _ZERO_SHA256
        else:
            allowed = {
                _FORWARD_TRANSITION.get(previous.state),
                FormalExecutionStateV0233.RECOVERABLE_FAILURE,
                FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
            }
            if previous.state is FormalExecutionStateV0233.RECOVERABLE_FAILURE:
                allowed = set(_RECOVERY_RESUME_STATES)
                allowed.update(
                    {
                        FormalExecutionStateV0233.RECOVERABLE_FAILURE,
                        FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
                    }
                )
            allowed.discard(None)
            if state not in allowed:
                raise ValueError("Product v0.2.3.3 checkpoint transition differs")
            identity = (
                (campaign_id, previous.campaign_id, "campaign"),
                (semantic_generation, previous.semantic_generation, "generation"),
                (attempt_id, previous.attempt_id, "attempt"),
                (
                    semantic_surface_sha256,
                    previous.semantic_surface_sha256,
                    "semantic surface",
                ),
                (
                    source_selection_sha256,
                    previous.source_selection_sha256,
                    "source selection",
                ),
            )
            for supplied, expected, name in identity:
                if supplied is not None and supplied != expected:
                    raise ValueError(f"Product v0.2.3.3 checkpoint {name} differs")
            campaign_id = previous.campaign_id
            semantic_generation = previous.semantic_generation
            attempt_id = previous.attempt_id
            semantic_surface_sha256 = previous.semantic_surface_sha256
            source_selection_sha256 = previous.source_selection_sha256
            operational_surface_sha256 = (
                operational_surface_sha256 or previous.operational_surface_sha256
            )
            if formal_clone_sha256 is None:
                formal_clone_sha256 = previous.formal_clone_sha256
            sequence = previous.sequence + 1
            previous_sha256 = previous.checkpoint_sha256
            if created_at < previous.created_at:
                raise ValueError("Product v0.2.3.3 checkpoint time differs")
        assert campaign_id is not None
        assert semantic_generation is not None
        assert attempt_id is not None
        assert semantic_surface_sha256 is not None
        assert operational_surface_sha256 is not None
        assert source_selection_sha256 is not None
        body = {
            "schema_version": "ecomsre.product.formal-execution-checkpoint.v0233",
            "campaign_id": campaign_id,
            "semantic_generation": semantic_generation,
            "attempt_id": attempt_id,
            "sequence": sequence,
            "state": state.value,
            "semantic_surface_sha256": semantic_surface_sha256,
            "operational_surface_sha256": operational_surface_sha256,
            "source_selection_sha256": source_selection_sha256,
            "formal_clone_sha256": formal_clone_sha256,
            "input_artifact_sha256s": _sorted_sha256_mapping(
                input_artifact_sha256s or {}
            ),
            "output_artifact_sha256s": _sorted_sha256_mapping(
                output_artifact_sha256s or {}
            ),
            "created_at": created_at,
            "previous_checkpoint_sha256": previous_sha256,
        }
        return cls.model_validate(
            {**body, "checkpoint_sha256": semantic_json_sha256_v0233(body)}
        )


class FormalCheckpointRepositoryV0233:
    """Create-once private checkpoint chain for one attempt."""

    def __init__(self, attempt_root: Path) -> None:
        self.attempt_root = Path(attempt_root)
        self.root = self.attempt_root / "checkpoints"

    def checkpoint_path(self, checkpoint: FormalExecutionCheckpointV0233) -> Path:
        state = checkpoint.state.value.lower().replace("_", "-")
        return self.root / f"{checkpoint.sequence:04d}-{state}.json"

    def append(self, checkpoint: FormalExecutionCheckpointV0233) -> Path:
        path = self.checkpoint_path(checkpoint)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Product v0.2.3.3 checkpoint exists: {path.name}")
        existing = self.load_chain()
        if existing:
            previous = existing[-1]
            if (
                checkpoint.sequence != previous.sequence + 1
                or checkpoint.previous_checkpoint_sha256
                != previous.checkpoint_sha256
                or checkpoint.attempt_id != previous.attempt_id
            ):
                raise ValueError("Product v0.2.3.3 checkpoint chain differs")
        elif checkpoint.sequence != 1:
            raise ValueError("Product v0.2.3.3 checkpoint chain differs")
        if checkpoint.attempt_id != self.attempt_root.name:
            raise ValueError("Product v0.2.3.3 checkpoint attempt path differs")
        write_private_json(path, checkpoint, create_once=True)
        return path

    def load_chain(self) -> tuple[FormalExecutionCheckpointV0233, ...]:
        if not self.root.exists():
            return ()
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("Product v0.2.3.3 checkpoint root differs")
        paths = sorted(self.root.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise ValueError("Product v0.2.3.3 checkpoint path differs")
        chain: list[FormalExecutionCheckpointV0233] = []
        for path in paths:
            try:
                checkpoint = FormalExecutionCheckpointV0233.model_validate_json(
                    path.read_bytes()
                )
            except (OSError, ValueError) as error:
                raise ValueError("Product v0.2.3.3 checkpoint invalid") from error
            if path != self.checkpoint_path(checkpoint):
                raise ValueError("Product v0.2.3.3 checkpoint filename differs")
            if checkpoint.attempt_id != self.attempt_root.name:
                raise ValueError("Product v0.2.3.3 checkpoint attempt path differs")
            if chain:
                previous = chain[-1]
                if (
                    checkpoint.sequence != previous.sequence + 1
                    or checkpoint.previous_checkpoint_sha256
                    != previous.checkpoint_sha256
                ):
                    raise ValueError("Product v0.2.3.3 checkpoint chain differs")
            elif checkpoint.sequence != 1:
                raise ValueError("Product v0.2.3.3 checkpoint chain differs")
            chain.append(checkpoint)
        return tuple(chain)


FormalAttemptDispositionV0233 = Literal[
    "LEGACY_BLOCKED",
    "ACTIVE",
    "RECOVERABLE_FAILURE",
    "NONRECOVERABLE_FAILURE",
    "MEASURED",
]

MeasuredTerminalV0233 = Literal[
    "ECOMSRE_PRODUCT_V0233_NOFAULT_FULLY_SUPPORTED",
    "ECOMSRE_PRODUCT_V0233_NOFAULT_CAPABILITY_LIMITED",
    "ECOMSRE_PRODUCT_V0233_NOFAULT_NOT_SUPPORTED",
]


class FormalAttemptRecordV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-attempt-record.v0233"] = (
        "ecomsre.product.formal-attempt-record.v0233"
    )
    attempt_id: str = Field(pattern=_ATTEMPT_PATTERN)
    ordinal: int = Field(ge=1)
    semantic_generation: int = Field(ge=1)
    disposition: FormalAttemptDispositionV0233
    latest_state: FormalExecutionStateV0233
    latest_checkpoint_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    blocker_terminal: str | None = None
    measured_terminal: MeasuredTerminalV0233 | None = None
    evidence_sha256_by_path: dict[str, str]
    record_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_attempt_disposition(self) -> FormalAttemptRecordV0233:
        legacy = (
            self.disposition == "LEGACY_BLOCKED"
            and self.latest_checkpoint_sha256 is None
            and self.blocker_terminal is not None
            and self.measured_terminal is None
        )
        active = (
            self.disposition == "ACTIVE"
            and self.latest_checkpoint_sha256 is not None
            and self.blocker_terminal is None
            and self.measured_terminal is None
            and self.latest_state
            not in {
                FormalExecutionStateV0233.CLOSED,
                FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
            }
        )
        recoverable = (
            self.disposition == "RECOVERABLE_FAILURE"
            and self.latest_state is FormalExecutionStateV0233.RECOVERABLE_FAILURE
            and self.latest_checkpoint_sha256 is not None
            and self.blocker_terminal is not None
            and self.measured_terminal is None
        )
        nonrecoverable = (
            self.disposition == "NONRECOVERABLE_FAILURE"
            and self.latest_state is FormalExecutionStateV0233.NONRECOVERABLE_FAILURE
            and self.latest_checkpoint_sha256 is not None
            and self.blocker_terminal is not None
            and self.measured_terminal is None
        )
        measured = (
            self.disposition == "MEASURED"
            and self.latest_state is FormalExecutionStateV0233.CLOSED
            and self.latest_checkpoint_sha256 is not None
            and self.blocker_terminal is None
            and self.measured_terminal is not None
        )
        if (
            sum((legacy, active, recoverable, nonrecoverable, measured)) != 1
            or self.evidence_sha256_by_path
            != _sorted_sha256_mapping(self.evidence_sha256_by_path)
            or self.record_sha256
            != semantic_sha256_v22(
                self.model_dump(mode="json", exclude={"record_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 attempt record differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalAttemptRecordV0233:
        body = {
            "schema_version": "ecomsre.product.formal-attempt-record.v0233",
            **payload,
            "evidence_sha256_by_path": _sorted_sha256_mapping(
                payload["evidence_sha256_by_path"]
            ),
        }
        return cls.model_validate(
            {**body, "record_sha256": semantic_json_sha256_v0233(body)}
        )


class FormalAttemptLedgerV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-attempt-ledger.v0233"] = (
        "ecomsre.product.formal-attempt-ledger.v0233"
    )
    campaign_id: str = Field(pattern=_ATTEMPT_PATTERN)
    attempts: tuple[FormalAttemptRecordV0233, ...] = Field(min_length=1)
    latest_attempt_id: str = Field(pattern=_ATTEMPT_PATTERN)
    measured_result_count: int = Field(ge=0, le=1)
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_append_only_attempt_order(self) -> FormalAttemptLedgerV0233:
        ordinals = tuple(item.ordinal for item in self.attempts)
        identifiers = tuple(item.attempt_id for item in self.attempts)
        measured = sum(item.disposition == "MEASURED" for item in self.attempts)
        if (
            ordinals != tuple(range(1, len(self.attempts) + 1))
            or len(set(identifiers)) != len(identifiers)
            or self.latest_attempt_id != self.attempts[-1].attempt_id
            or self.measured_result_count != measured
            or (measured and self.attempts[-1].disposition != "MEASURED")
            or self.ledger_sha256
            != semantic_sha256_v22(
                self.model_dump(mode="json", exclude={"ledger_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 attempt ledger differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        campaign_id: str,
        attempts: tuple[FormalAttemptRecordV0233, ...],
    ) -> FormalAttemptLedgerV0233:
        body = {
            "schema_version": "ecomsre.product.formal-attempt-ledger.v0233",
            "campaign_id": campaign_id,
            "attempts": [item.model_dump(mode="json") for item in attempts],
            "latest_attempt_id": attempts[-1].attempt_id,
            "measured_result_count": sum(
                item.disposition == "MEASURED" for item in attempts
            ),
        }
        return cls.model_validate(
            {**body, "ledger_sha256": semantic_json_sha256_v0233(body)}
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_legacy_attempt1_record_v0233(project_root: Path) -> FormalAttemptRecordV0233:
    """Bind Attempt 1 public bytes without moving or rewriting historical evidence."""

    root = Path(project_root).resolve(strict=True)
    manifest_path = (
        root / "docs/analysis/product-v0233-formal-blocker-evidence-manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3.3 Attempt 1 manifest differs")
    sealed = dict(payload)
    supplied = sealed.pop("manifest_sha256", None)
    if (
        supplied != semantic_sha256_v22(sealed)
        or payload.get("terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
        or payload.get("failure_stage") != "FORMAL_TRAFFIC_PASS"
        or payload.get("completed_transactions") != 30
        or payload.get("new_incident_count") != 0
        or payload.get("new_diagnosis_count") != 0
        or payload.get("measured_result_count") != 0
    ):
        raise ValueError("Product v0.2.3.3 Attempt 1 manifest differs")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Product v0.2.3.3 Attempt 1 artifacts differ")
    evidence = {
        manifest_path.relative_to(root).as_posix(): _sha256_file(manifest_path)
    }
    for artifact in artifacts.values():
        if not isinstance(artifact, dict) or "public_path" not in artifact:
            continue
        relative = str(artifact["public_path"])
        path = root / relative
        expected = artifact.get("file_sha256") or artifact.get("public_file_sha256")
        observed = _sha256_file(path)
        if observed != expected:
            raise ValueError("Product v0.2.3.3 Attempt 1 artifact differs")
        evidence[relative] = observed
    return FormalAttemptRecordV0233.build(
        attempt_id="attempt-1",
        ordinal=1,
        semantic_generation=1,
        disposition="LEGACY_BLOCKED",
        latest_state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        latest_checkpoint_sha256=None,
        blocker_terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        measured_terminal=None,
        evidence_sha256_by_path=evidence,
    )


def determine_earliest_safe_resume_state_v0233(
    checkpoint: FormalExecutionCheckpointV0233,
) -> FormalExecutionStateV0233:
    if checkpoint.state is FormalExecutionStateV0233.CLOSED:
        raise ValueError("Product v0.2.3.3 measured attempt cannot resume")
    if checkpoint.state is FormalExecutionStateV0233.NONRECOVERABLE_FAILURE:
        raise ValueError("Product v0.2.3.3 nonrecoverable attempt cannot resume")
    if checkpoint.state is not FormalExecutionStateV0233.RECOVERABLE_FAILURE:
        return checkpoint.state
    outputs = checkpoint.output_artifact_sha256s
    if any(path.endswith("diagnosis-acquisition-checkpoint.json") for path in outputs):
        return FormalExecutionStateV0233.ACQUISITION_SEALED
    if any(path.endswith("live-capture-bundle.json") for path in outputs):
        return FormalExecutionStateV0233.LIVE_CAPTURE_SEALED
    return FormalExecutionStateV0233.PREPARED


def formal_incident_external_key_v0233(bundle: LiveCaptureBundleV0233) -> str:
    return (
        f"product-v0233-g{bundle.semantic_generation}-"
        f"{bundle.live_capture_bundle_sha256[:24]}"
    )


def formal_diagnosis_idempotency_key_v0233(
    *,
    incident_sha256: str,
    acquisition_sha256: str,
    semantic_surface_sha256: str,
    diagnosis_generation: int,
) -> str:
    body = {
        "incident_sha256": incident_sha256,
        "acquisition_sha256": acquisition_sha256,
        "semantic_surface_sha256": semantic_surface_sha256,
        "diagnosis_generation": diagnosis_generation,
    }
    return f"formal-v0233-diagnosis-{semantic_json_sha256_v0233(body)[:32]}"


def acquisition_recovery_is_compatible_v0233(
    checkpoint: DiagnosisAcquisitionCheckpointV0233,
    *,
    semantic_surface_sha256: str,
) -> bool:
    return checkpoint.semantic_surface_sha256 == semantic_surface_sha256


def verify_checkpoint_artifacts_v0233(
    project_root: Path,
    checkpoint: FormalExecutionCheckpointV0233,
) -> None:
    root = Path(project_root).resolve(strict=True)
    artifacts = {
        **checkpoint.input_artifact_sha256s,
        **checkpoint.output_artifact_sha256s,
    }
    for relative, expected in artifacts.items():
        path = root / relative
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ValueError(
                "Product v0.2.3.3 checkpoint artifact missing"
            ) from error
        if (
            not resolved.is_relative_to(root)
            or path.is_symlink()
            or not path.is_file()
            or _sha256_file(path) != expected
        ):
            raise ValueError("Product v0.2.3.3 checkpoint artifact differs")


__all__ = (
    "DiagnosisAcquisitionCheckpointV0233",
    "FormalAttemptLedgerV0233",
    "FormalAttemptRecordV0233",
    "FormalCheckpointRepositoryV0233",
    "FormalExecutionCheckpointV0233",
    "FormalExecutionStateV0233",
    "FormalOperationalSurfaceV0233",
    "FormalSemanticSurfaceV0233",
    "LiveCaptureBundleV0233",
    "acquisition_recovery_is_compatible_v0233",
    "build_legacy_attempt1_record_v0233",
    "determine_earliest_safe_resume_state_v0233",
    "formal_diagnosis_idempotency_key_v0233",
    "formal_incident_external_key_v0233",
    "verify_checkpoint_artifacts_v0233",
)
