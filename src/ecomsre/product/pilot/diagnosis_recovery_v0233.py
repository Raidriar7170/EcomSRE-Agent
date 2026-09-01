"""Frozen Diagnosis acquisition and recovery helpers for Product v0.2.3.3."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.memory import RuntimeReadOutcomeV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityEvidenceObservationV0232,
    CapabilityLimitationCandidateV0232,
)
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.pilot.formal_recovery_v0233 import (
    DiagnosisAcquisitionCheckpointV0233,
    formal_diagnosis_idempotency_key_v0233,
)
from ecomsre.product.pilot.serialization_v0233 import semantic_json_sha256_v0233


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ATTEMPT_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"


def diagnosis_checkpoint_locator_v0233(attempt_id: str) -> str:
    if not attempt_id or not all(
        character.islower() or character.isdigit() or character == "-"
        for character in attempt_id
    ):
        raise ValueError("Product v0.2.3.3 attempt ID differs")
    return f"private/formal-v0233/{attempt_id}/diagnosis-acquisition-checkpoint.json"


class FormalDiagnosisJobContextV0233(ProductModelV1):
    """Private job payload that binds fresh or recovery Diagnosis execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-diagnosis-job-context.v0233"] = (
        "ecomsre.product.formal-diagnosis-job-context.v0233"
    )
    campaign_id: str = Field(pattern=_ATTEMPT_PATTERN)
    semantic_generation: int = Field(ge=1)
    attempt_id: str = Field(pattern=_ATTEMPT_PATTERN)
    diagnosis_generation: int = Field(ge=1)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_surface_sha256: str = Field(pattern=_SHA256_PATTERN)
    acquisition_checkpoint_locator: str
    acquisition_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    context_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_context(self) -> FormalDiagnosisJobContextV0233:
        if (
            self.acquisition_checkpoint_locator
            != diagnosis_checkpoint_locator_v0233(self.attempt_id)
            or self.context_sha256
            != semantic_json_sha256_v0233(
                self.model_dump(mode="json", exclude={"context_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 formal Diagnosis context differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalDiagnosisJobContextV0233:
        body = {
            "schema_version": "ecomsre.product.formal-diagnosis-job-context.v0233",
            **payload,
            "acquisition_checkpoint_locator": diagnosis_checkpoint_locator_v0233(
                payload["attempt_id"]
            ),
        }
        return cls.model_validate(
            {**body, "context_sha256": semantic_json_sha256_v0233(body)}
        )


class FormalDiagnosisRecoverySubmissionV0233(ProductModelV1):
    """Idempotent recovery job bound to one failed lineage and frozen reads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.formal-diagnosis-recovery-submission.v0233"
    ] = "ecomsre.product.formal-diagnosis-recovery-submission.v0233"
    incident_id: str = Field(pattern=r"^inc-[a-zA-Z0-9-]{1,120}$")
    incident_sha256: str = Field(pattern=_SHA256_PATTERN)
    context: FormalDiagnosisJobContextV0233
    idempotency_key: str
    preserved_failed_job_ids: tuple[str, ...]
    submission_sha256: str = Field(pattern=_SHA256_PATTERN)

    @property
    def job_payload(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "formal_recovery_v0233": self.context.model_dump(mode="json"),
        }

    @model_validator(mode="after")
    def require_recovery_binding(self) -> FormalDiagnosisRecoverySubmissionV0233:
        failed = tuple(sorted(set(self.preserved_failed_job_ids)))
        expected_key = (
            None
            if self.context.acquisition_sha256 is None
            else final_diagnosis_idempotency_key_v0233(
                context=self.context,
                incident_sha256=self.incident_sha256,
                acquisition_sha256=self.context.acquisition_sha256,
            )
        )
        if (
            self.context.diagnosis_generation < 2
            or self.context.acquisition_sha256 is None
            or not failed
            or failed != self.preserved_failed_job_ids
            or any(
                not job_id.startswith("job-") or len(job_id) != 28
                for job_id in failed
            )
            or self.idempotency_key != expected_key
            or self.submission_sha256
            != semantic_json_sha256_v0233(
                self.model_dump(mode="json", exclude={"submission_sha256"})
            )
        ):
            raise ValueError("Product v0.2.3.3 Diagnosis recovery differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        checkpoint: DiagnosisAcquisitionCheckpointV0233,
        diagnosis_generation: int,
        preserved_failed_job_ids: tuple[str, ...],
    ) -> FormalDiagnosisRecoverySubmissionV0233:
        context = FormalDiagnosisJobContextV0233.build(
            campaign_id=checkpoint.campaign_id,
            semantic_generation=checkpoint.semantic_generation,
            attempt_id=checkpoint.attempt_id,
            diagnosis_generation=diagnosis_generation,
            active_profile_sha256=checkpoint.active_profile_sha256,
            semantic_surface_sha256=checkpoint.semantic_surface_sha256,
            acquisition_sha256=checkpoint.acquisition_sha256,
        )
        body = {
            "schema_version": (
                "ecomsre.product.formal-diagnosis-recovery-submission.v0233"
            ),
            "incident_id": checkpoint.incident_id,
            "incident_sha256": checkpoint.incident_sha256,
            "context": context.model_dump(mode="json"),
            "idempotency_key": final_diagnosis_idempotency_key_v0233(
                context=context,
                incident_sha256=checkpoint.incident_sha256,
                acquisition_sha256=checkpoint.acquisition_sha256,
            ),
            "preserved_failed_job_ids": sorted(set(preserved_failed_job_ids)),
        }
        return cls.model_validate(
            {**body, "submission_sha256": semantic_json_sha256_v0233(body)}
        )


def build_diagnosis_acquisition_checkpoint_v0233(
    *,
    context: FormalDiagnosisJobContextV0233,
    acquisition: ProductReadAcquisitionV1,
    incident_id: str,
    incident_sha256: str,
    incident_observation_started_at: Any,
    incident_observation_ended_at: Any,
    baseline_sha256: str,
    service_identity_sha256: str,
    capability_sha256: str,
) -> DiagnosisAcquisitionCheckpointV0233:
    snapshots = tuple(acquisition.snapshots)
    connector_results = tuple(snapshot["connector_result"] for snapshot in snapshots)
    provenance = tuple(
        item
        for snapshot in snapshots
        for item in snapshot.get("connector_bindings_v0232", ())
    )
    runtime_bindings = tuple(
        item
        for item in provenance
        if item.get("connector_binding", {}).get("binding_kind")
        == "RUNTIME_SNAPSHOT"
        and isinstance(item.get("binding_payload"), dict)
    )
    profile_bindings = tuple(
        item
        for item in provenance
        if item.get("connector_binding", {}).get("binding_kind")
        == "OPENSEARCH_PROFILE"
        and isinstance(item.get("binding_payload"), dict)
    )
    if len(runtime_bindings) != 1:
        raise ValueError("Product v0.2.3.3 Runtime acquisition binding differs")
    if not profile_bindings or any(
        item["binding_payload"].get("active_profile_sha256")
        != context.active_profile_sha256
        or item["binding_payload"].get("selected_candidate_alias") != "P01"
        for item in profile_bindings
    ):
        raise ValueError("Product v0.2.3.3 P01 acquisition binding differs")
    runtime_binding_sha256 = runtime_bindings[0]["binding_payload"].get(
        "binding_sha256"
    )
    if not isinstance(runtime_binding_sha256, str):
        raise ValueError("Product v0.2.3.3 Runtime acquisition binding differs")
    read_snapshot_sha256s = {
        f"read-snapshot-{ordinal:03d}.json": semantic_json_sha256_v0233(snapshot)
        for ordinal, snapshot in enumerate(snapshots)
    }
    return DiagnosisAcquisitionCheckpointV0233.build(
        campaign_id=context.campaign_id,
        semantic_generation=context.semantic_generation,
        attempt_id=context.attempt_id,
        incident_id=incident_id,
        incident_sha256=incident_sha256,
        incident_observation_started_at=incident_observation_started_at,
        incident_observation_ended_at=incident_observation_ended_at,
        baseline_sha256=baseline_sha256,
        active_profile_sha256=context.active_profile_sha256,
        service_identity_sha256=service_identity_sha256,
        capability_sha256=capability_sha256,
        connector_query_results=connector_results,
        connector_provenance_bindings=provenance,
        runtime_snapshot_binding_sha256=runtime_binding_sha256,
        source_coverage={
            source.value: services
            for source, services in acquisition.covered_services_by_source.items()
        },
        capability_limitations=acquisition.capability_limitations,
        capability_observations=tuple(
            item.model_dump(mode="json")
            for item in acquisition.capability_observations_v0232
        ),
        limitation_candidates=tuple(
            item.model_dump(mode="json")
            for item in acquisition.capability_limitation_candidates_v0232
        ),
        read_snapshots=snapshots,
        read_snapshot_sha256s=read_snapshot_sha256s,
        semantic_surface_sha256=context.semantic_surface_sha256,
    )


def restore_diagnosis_acquisition_v0233(
    checkpoint: DiagnosisAcquisitionCheckpointV0233,
    *,
    context: FormalDiagnosisJobContextV0233,
    incident_id: str,
    incident_sha256: str,
) -> ProductReadAcquisitionV1:
    if (
        context.acquisition_sha256 != checkpoint.acquisition_sha256
        or context.semantic_surface_sha256 != checkpoint.semantic_surface_sha256
        or context.campaign_id != checkpoint.campaign_id
        or context.semantic_generation != checkpoint.semantic_generation
        or context.attempt_id != checkpoint.attempt_id
        or incident_id != checkpoint.incident_id
        or incident_sha256 != checkpoint.incident_sha256
    ):
        raise ValueError("Product v0.2.3.3 acquisition recovery binding differs")

    raw_outcomes: list[ReadOutcomeV22] = []
    memory_outcomes: list[ReadOutcomeV22 | RuntimeReadOutcomeV22] = []
    for snapshot in checkpoint.read_snapshots:
        raw_outcomes.append(
            ReadOutcomeV22.model_validate_json(
                json.dumps(snapshot["read_outcome"], sort_keys=True)
            )
        )
        memory = snapshot.get("memory_outcome")
        if memory is None:
            continue
        memory_type = (
            RuntimeReadOutcomeV22
            if memory.get("schema_version") == "dta-v22.runtime-read-outcome.v1"
            else ReadOutcomeV22
        )
        memory_outcomes.append(
            memory_type.model_validate_json(json.dumps(memory, sort_keys=True))
        )

    restored = ProductReadAcquisitionV1(
        raw_outcomes=tuple(raw_outcomes),
        memory_outcomes=tuple(memory_outcomes),
        snapshots=checkpoint.read_snapshots,
        covered_services_by_source={
            EvidenceSourceV22(source): services
            for source, services in checkpoint.source_coverage.items()
        },
        capability_limitations=checkpoint.capability_limitations,
        capability_observations_v0232=tuple(
            CapabilityEvidenceObservationV0232.model_validate_json(
                json.dumps(item, sort_keys=True)
            )
            for item in checkpoint.capability_observations
        ),
        capability_limitation_candidates_v0232=tuple(
            CapabilityLimitationCandidateV0232.model_validate_json(
                json.dumps(item, sort_keys=True)
            )
            for item in checkpoint.limitation_candidates
        ),
    )
    rebound = build_diagnosis_acquisition_checkpoint_v0233(
        context=context.model_copy(update={"acquisition_sha256": None}),
        acquisition=restored,
        incident_id=checkpoint.incident_id,
        incident_sha256=checkpoint.incident_sha256,
        incident_observation_started_at=checkpoint.incident_observation_started_at,
        incident_observation_ended_at=checkpoint.incident_observation_ended_at,
        baseline_sha256=checkpoint.baseline_sha256,
        service_identity_sha256=checkpoint.service_identity_sha256,
        capability_sha256=checkpoint.capability_sha256,
    )
    if rebound != checkpoint:
        raise ValueError("Product v0.2.3.3 acquisition recovery content differs")
    return restored


def final_diagnosis_idempotency_key_v0233(
    *,
    context: FormalDiagnosisJobContextV0233,
    incident_sha256: str,
    acquisition_sha256: str,
) -> str:
    return formal_diagnosis_idempotency_key_v0233(
        incident_sha256=incident_sha256,
        acquisition_sha256=acquisition_sha256,
        semantic_surface_sha256=context.semantic_surface_sha256,
        diagnosis_generation=context.diagnosis_generation,
    )


__all__ = (
    "FormalDiagnosisJobContextV0233",
    "FormalDiagnosisRecoverySubmissionV0233",
    "build_diagnosis_acquisition_checkpoint_v0233",
    "diagnosis_checkpoint_locator_v0233",
    "final_diagnosis_idempotency_key_v0233",
    "restore_diagnosis_acquisition_v0233",
)
