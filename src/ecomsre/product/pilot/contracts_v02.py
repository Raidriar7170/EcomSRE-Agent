from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, model_validator


Sha256V02 = str


def semantic_sha256_v02(payload: object) -> str:
    def json_default(value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        raise TypeError(f"unsupported semantic digest value: {type(value).__name__}")

    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=json_default,
        ).encode("utf-8")
    ).hexdigest()


class PilotModelV02(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PilotEpisodeRoleV02(str, Enum):
    CALIBRATION = "CALIBRATION"
    LIVE_NO_FAULT_NEGATIVE = "LIVE_NO_FAULT_NEGATIVE"
    LIVE_KNOWN_NEGATIVE = "LIVE_KNOWN_NEGATIVE"
    POSITIVE_FIT = "POSITIVE_FIT"
    POSITIVE_SHADOW = "POSITIVE_SHADOW"
    HELDOUT_RECURRENCE = "HELDOUT_RECURRENCE"


class PilotEpisodeTerminalV02(str, Enum):
    PASS = "PASS"
    PROFILE_NOT_OBSERVABLE = "PROFILE_NOT_OBSERVABLE"
    CORE_ABSORBED = "CORE_ABSORBED"
    EXTENSION_ABSORBED = "EXTENSION_ABSORBED"
    NO_INCIDENT_FALSELY_ADMITTED = "NO_INCIDENT_FALSELY_ADMITTED"
    OPEN_WORLD_NOT_REACHED = "OPEN_WORLD_NOT_REACHED"
    BASELINE_NOT_RESTORED = "BASELINE_NOT_RESTORED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    CONNECTOR_FAILED = "CONNECTOR_FAILED"
    DIAGNOSIS_FAILED = "DIAGNOSIS_FAILED"


class PilotAttemptStageV02(str, Enum):
    PLANNED = "PLANNED"
    BASELINE_VERIFIED = "BASELINE_VERIFIED"
    CONTROL_APPLIED = "CONTROL_APPLIED"
    TRAFFIC_STOPPED = "TRAFFIC_STOPPED"
    OBSERVATION_CAPTURED = "OBSERVATION_CAPTURED"
    DIAGNOSIS_PERSISTED = "DIAGNOSIS_PERSISTED"
    FLAG_RESTORED = "FLAG_RESTORED"
    FLAG_RESTORE_BLOCKED = "FLAG_RESTORE_BLOCKED"
    RECOVERY_VERIFIED = "RECOVERY_VERIFIED"
    CLEANUP_CLEAN = "CLEANUP_CLEAN"
    CLEANUP_BLOCKED = "CLEANUP_BLOCKED"
    FINALIZED = "FINALIZED"


class PilotAttemptFailureDomainV02(str, Enum):
    NONE = "NONE"
    DOCKER = "DOCKER"
    CONNECTOR = "CONNECTOR"
    TRAFFIC = "TRAFFIC"
    PRODUCT = "PRODUCT"
    SEMANTIC = "SEMANTIC"
    CLEANUP = "CLEANUP"


PILOT_ATTEMPT_TRANSITIONS_V02: dict[
    PilotAttemptStageV02, frozenset[PilotAttemptStageV02]
] = {
    PilotAttemptStageV02.PLANNED: frozenset(
        {
            PilotAttemptStageV02.BASELINE_VERIFIED,
            PilotAttemptStageV02.FLAG_RESTORED,
            PilotAttemptStageV02.FLAG_RESTORE_BLOCKED,
        }
    ),
    PilotAttemptStageV02.BASELINE_VERIFIED: frozenset(
        {
            PilotAttemptStageV02.CONTROL_APPLIED,
            PilotAttemptStageV02.TRAFFIC_STOPPED,
            PilotAttemptStageV02.FLAG_RESTORED,
            PilotAttemptStageV02.FLAG_RESTORE_BLOCKED,
        }
    ),
    PilotAttemptStageV02.CONTROL_APPLIED: frozenset(
        {
            PilotAttemptStageV02.TRAFFIC_STOPPED,
            PilotAttemptStageV02.FLAG_RESTORED,
            PilotAttemptStageV02.FLAG_RESTORE_BLOCKED,
        }
    ),
    PilotAttemptStageV02.TRAFFIC_STOPPED: frozenset(
        {
            PilotAttemptStageV02.OBSERVATION_CAPTURED,
            PilotAttemptStageV02.FLAG_RESTORED,
            PilotAttemptStageV02.FLAG_RESTORE_BLOCKED,
        }
    ),
    PilotAttemptStageV02.OBSERVATION_CAPTURED: frozenset(
        {
            PilotAttemptStageV02.DIAGNOSIS_PERSISTED,
            PilotAttemptStageV02.FLAG_RESTORED,
            PilotAttemptStageV02.FLAG_RESTORE_BLOCKED,
        }
    ),
    PilotAttemptStageV02.DIAGNOSIS_PERSISTED: frozenset(
        {
            PilotAttemptStageV02.FLAG_RESTORED,
            PilotAttemptStageV02.FLAG_RESTORE_BLOCKED,
        }
    ),
    PilotAttemptStageV02.FLAG_RESTORE_BLOCKED: frozenset(
        {PilotAttemptStageV02.CLEANUP_BLOCKED}
    ),
    PilotAttemptStageV02.FLAG_RESTORED: frozenset(
        {
            PilotAttemptStageV02.RECOVERY_VERIFIED,
            PilotAttemptStageV02.CLEANUP_CLEAN,
            PilotAttemptStageV02.CLEANUP_BLOCKED,
        }
    ),
    PilotAttemptStageV02.RECOVERY_VERIFIED: frozenset(
        {PilotAttemptStageV02.CLEANUP_CLEAN, PilotAttemptStageV02.CLEANUP_BLOCKED}
    ),
    PilotAttemptStageV02.CLEANUP_CLEAN: frozenset(
        {PilotAttemptStageV02.FINALIZED}
    ),
    PilotAttemptStageV02.CLEANUP_BLOCKED: frozenset(
        {PilotAttemptStageV02.FINALIZED}
    ),
    PilotAttemptStageV02.FINALIZED: frozenset(),
}


class PilotAttemptEventV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.pilot-attempt-event.v02"] = (
        "ecomsre.product.pilot-attempt-event.v02"
    )
    event_id: str = Field(min_length=1, max_length=160)
    attempt_id: str = Field(min_length=1, max_length=160)
    slot_id: str = Field(min_length=1, max_length=80)
    role: PilotEpisodeRoleV02
    attempt_number: StrictInt = Field(ge=1, le=4)
    sequence: StrictInt = Field(ge=1, le=20)
    previous_stage: PilotAttemptStageV02 | None
    stage: PilotAttemptStageV02
    attempt_signature_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    failure_domain: PilotAttemptFailureDomainV02 | None = None
    usable_fault_observation: bool | None = None
    diagnosis_result_exists: bool | None = None
    flag_restored: bool | None = None
    cleanup_status: Literal["CLEAN", "BLOCKED", "NOT_ATTEMPTED"] | None = None
    episode_terminal: PilotEpisodeTerminalV02 | None = None
    observed_at: datetime
    event_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_event(self) -> "PilotAttemptEventV02":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("attempt event timestamp must be UTC")
        if self.sequence == 1:
            if self.previous_stage is not None or self.stage is not PilotAttemptStageV02.PLANNED:
                raise ValueError("first attempt event must be PLANNED")
        elif self.previous_stage is None:
            raise ValueError("non-initial attempt event requires previous stage")
        terminal_fields = (
            self.failure_domain,
            self.usable_fault_observation,
            self.diagnosis_result_exists,
            self.flag_restored,
            self.cleanup_status,
            self.episode_terminal,
        )
        if self.stage is PilotAttemptStageV02.FINALIZED:
            if any(value is None for value in terminal_fields):
                raise ValueError("FINALIZED attempt event requires terminal outcome fields")
        elif any(value is not None for value in terminal_fields):
            raise ValueError("terminal outcome fields are reserved for FINALIZED events")
        expected = semantic_sha256_v02(
            self.model_dump(mode="json", exclude={"event_sha256"})
        )
        if self.event_sha256 != expected:
            raise ValueError("attempt event digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "PilotAttemptEventV02":
        body = {
            "schema_version": "ecomsre.product.pilot-attempt-event.v02",
            **payload,
        }
        draft = cls.model_construct(**body, event_sha256="0" * 64)
        body["event_sha256"] = semantic_sha256_v02(
            draft.model_dump(mode="json", exclude={"event_sha256"})
        )
        return cls.model_validate(body)

    def replacement_eligible(self) -> bool:
        return (
            self.stage is PilotAttemptStageV02.FINALIZED
            and self.failure_domain
            in {
                PilotAttemptFailureDomainV02.DOCKER,
                PilotAttemptFailureDomainV02.CONNECTOR,
                PilotAttemptFailureDomainV02.TRAFFIC,
            }
            and self.usable_fault_observation is False
            and self.diagnosis_result_exists is False
            and self.flag_restored is True
            and self.cleanup_status == "CLEAN"
        )


class QueueProfileV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.pilot.queue-profile.v02"] = (
        "ecomsre.product.pilot.queue-profile.v02"
    )
    profile_id: str = Field(min_length=1, max_length=120)
    profile_name: Literal["CHECKOUT_KAFKA_QUEUE_OVERLOAD"] = (
        "CHECKOUT_KAFKA_QUEUE_OVERLOAD"
    )
    candidate_values: tuple[StrictInt, ...] = Field(min_length=1, max_length=3)
    maximum_calibration_changes: Literal[2]
    expected_default_value: Literal[0]
    selected_value: StrictInt | None = Field(default=None, ge=1, le=20)
    selected_root_service: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$"
    )
    calibration_report_sha256: Sha256V02 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    calibration_contract_sha256: Sha256V02 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    calibration_runtime_binding_sha256: Sha256V02 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    calibrated_at: datetime | None = None
    profile_sha256: Sha256V02 | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def require_profile(self) -> "QueueProfileV02":
        values = tuple(self.candidate_values)
        if values != tuple(sorted(set(values))) or any(
            value < 1 or value > 20 for value in values
        ):
            raise ValueError("queue profile candidates must be unique values from 1 to 20")
        selection = (
            self.selected_value,
            self.selected_root_service,
            self.calibration_report_sha256,
            self.calibration_contract_sha256,
            self.calibration_runtime_binding_sha256,
            self.calibrated_at,
        )
        if any(item is not None for item in selection) and any(
            item is None for item in selection
        ):
            raise ValueError("frozen queue profile selection is incomplete")
        if self.selected_value is not None:
            if self.selected_value not in values:
                raise ValueError("selected queue value is outside candidate set")
            if (
                self.calibrated_at is None
                or self.calibrated_at.tzinfo is None
                or self.calibrated_at.utcoffset() != timedelta(0)
            ):
                raise ValueError("queue profile calibration timestamp must be UTC")
        expected = semantic_sha256_v02(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 is not None and self.profile_sha256 != expected:
            raise ValueError("queue profile digest differs")
        return self

    def frozen(self) -> "QueueProfileV02":
        payload = self.model_dump(mode="python", exclude={"profile_sha256"})
        return QueueProfileV02.model_validate(
            {
                **payload,
                "profile_sha256": semantic_sha256_v02(
                    self.model_dump(mode="json", exclude={"profile_sha256"})
                ),
            }
        )


class TrafficProfileV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.pilot.traffic-profile.v02"] = (
        "ecomsre.product.pilot.traffic-profile.v02"
    )
    profile_id: str = Field(min_length=1, max_length=120)
    request_seed: StrictInt = Field(ge=0, le=2**31 - 1)
    maximum_request_count: StrictInt = Field(ge=1, le=30)
    requests_per_second: StrictFloat = Field(gt=0, le=2.0)
    error_budget: StrictInt = Field(ge=1, le=3)


class TrafficRunResultV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.pilot.traffic-result.v02"] = (
        "ecomsre.product.pilot.traffic-result.v02"
    )
    profile_id: str
    attempted: StrictInt = Field(ge=0, le=30)
    succeeded: StrictInt = Field(ge=0, le=30)
    failed: StrictInt = Field(ge=0, le=30)
    stopped_on_error_budget: bool
    duration_seconds: StrictFloat = Field(ge=0)
    result_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_result(self) -> "TrafficRunResultV02":
        if self.succeeded + self.failed != self.attempted:
            raise ValueError("traffic counts differ")
        expected = semantic_sha256_v02(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("traffic result digest differs")
        return self


class QueueFlagTransitionV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.pilot.queue-transition.v02"] = (
        "ecomsre.product.pilot.queue-transition.v02"
    )
    before_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    applied_value: StrictInt = Field(ge=1, le=20)


class LivePilotEpisodeV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.live-pilot-episode.v02"]
    episode_id: str
    role: PilotEpisodeRoleV02
    environment_id: str
    incident_id: str | None
    product_job_id: str | None
    private_control_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    public_evidence_bundle_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    flag_profile_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    traffic_profile_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")
    diagnosis_terminal: str
    root_services: tuple[str, ...]
    broad_domain: str
    mechanism: str
    evidence_refs: tuple[str, ...]
    fingerprint_id: str | None
    family_id: str | None
    open_world_invocations: StrictInt = Field(ge=0)
    action_authority_violations: StrictInt = Field(ge=0)
    agent_writes: StrictInt = Field(ge=0)
    runbook_executions: StrictInt = Field(ge=0)
    baseline_restored: bool
    cleanup_status: Literal["CLEAN", "BLOCKED", "NOT_ATTEMPTED"]
    episode_terminal: PilotEpisodeTerminalV02
    observed_at: datetime
    episode_sha256: Sha256V02 = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_episode(self) -> "LivePilotEpisodeV02":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("episode timestamp must be UTC")
        if self.root_services != tuple(sorted(set(self.root_services))):
            raise ValueError("episode roots must be canonical")
        if self.evidence_refs != tuple(sorted(set(self.evidence_refs))):
            raise ValueError("episode evidence refs must be canonical")
        if self.episode_terminal is PilotEpisodeTerminalV02.PASS and (
            not self.baseline_restored
            or self.cleanup_status != "CLEAN"
            or self.action_authority_violations != 0
            or self.agent_writes != 0
            or self.runbook_executions != 0
        ):
            raise ValueError("PASS episode requires restoration, clean cleanup, and zero authority violations")
        expected = semantic_sha256_v02(
            self.model_dump(mode="json", exclude={"episode_sha256"})
        )
        if self.episode_sha256 != expected:
            raise ValueError("episode digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "LivePilotEpisodeV02":
        body = {"schema_version": "ecomsre.product.live-pilot-episode.v02", **payload}
        draft = cls.model_construct(**body, episode_sha256="0" * 64)
        body["episode_sha256"] = semantic_sha256_v02(
            draft.model_dump(mode="json", exclude={"episode_sha256"})
        )
        return cls.model_validate(body)

    def public_projection(self) -> dict[str, object]:
        return {
            "schema_version": "ecomsre.product.live-pilot-public-episode.v02",
            "episode_id": self.episode_id,
            "environment_id": self.environment_id,
            "incident_id": self.incident_id,
            "product_job_id": self.product_job_id,
            "public_evidence_bundle_sha256": self.public_evidence_bundle_sha256,
            "baseline_sha256": self.baseline_sha256,
            "diagnosis_terminal": self.diagnosis_terminal,
            "root_services": self.root_services,
            "broad_domain": self.broad_domain,
            "evidence_refs": self.evidence_refs,
            "fingerprint_id": self.fingerprint_id,
            "family_id": self.family_id,
            "open_world_invocations": self.open_world_invocations,
            "action_authority_violations": self.action_authority_violations,
            "agent_writes": self.agent_writes,
            "runbook_executions": self.runbook_executions,
            "baseline_restored": self.baseline_restored,
            "cleanup_status": self.cleanup_status,
            "episode_terminal": self.episode_terminal.value,
            "observed_at": self.observed_at.isoformat(),
            "episode_sha256": self.episode_sha256,
        }


__all__ = [
    "LivePilotEpisodeV02",
    "PILOT_ATTEMPT_TRANSITIONS_V02",
    "PilotAttemptEventV02",
    "PilotAttemptFailureDomainV02",
    "PilotAttemptStageV02",
    "PilotEpisodeRoleV02",
    "PilotEpisodeTerminalV02",
    "QueueFlagTransitionV02",
    "QueueProfileV02",
    "TrafficProfileV02",
    "TrafficRunResultV02",
    "semantic_sha256_v02",
]
