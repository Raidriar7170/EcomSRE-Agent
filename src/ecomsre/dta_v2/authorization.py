"""Semantic Master Authorization and run-bound child records for DTA v2."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionProposal,
    CandidateSet,
    DtaDiagnosis,
    DtaModel,
    Identifier,
    ResolvedDiagnosisEvidenceView,
    RiskLevel,
    RunId,
    RunbookId,
    RunbookSpec,
    Sha256,
    semantic_sha256,
    validate_action_proposal_binding,
)
from ecomsre.dta_v2.operational_contracts import CurrentStateSnapshot
from ecomsre.dta_v2.registry import RunbookRegistry


_MVP_SCENARIO_IDS = ("dta-dev-001", "dta-dev-002", "dta-dev-003")
_MASTER_GOAL_SHA256 = (
    "7ecced0b1698f4a8b3e1f4e8a25f72598cc3ac6d3791bd74827618eb00bb10ea"
)


def _require_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def runbook_parameter_schema_sha256(runbook: RunbookSpec) -> str:
    return semantic_sha256(
        [parameter.model_dump(mode="json") for parameter in runbook.parameters]
    )


def action_parameters_sha256(proposal: ActionProposal) -> str:
    return semantic_sha256(
        [parameter.model_dump(mode="json") for parameter in proposal.parameters]
    )


class AuthorizationRecord(DtaModel):
    """Common immutable human-authorization audit boundary."""

    authorization_id: Identifier
    approver: Literal["Minghong Sun"]
    authorization_source: Literal[
        "USER_EXPLICIT_DTA_V2_MASTER_GOAL_AUTHORIZATION"
    ]
    command_execution: Literal["CODEX_DELEGATED_EXECUTION"]
    codex_autonomous_self_approval: Literal[False]
    additional_human_confirmation_required: Literal[False]
    issued_at: datetime
    authorization_sha256: Sha256

    @model_validator(mode="after")
    def require_authorization_issuance(self) -> AuthorizationRecord:
        _require_utc(self.issued_at, field_name="issued_at")
        return self


class AuthorizedRunbookScope(DtaModel):
    runbook_id: RunbookId
    runbook_sha256: Sha256
    target_service: Identifier
    risk_level: RiskLevel
    parameter_schema_sha256: Sha256
    maximum_forward_steps: StrictInt = Field(ge=1, le=2)

    @model_validator(mode="after")
    def deny_high_risk(self) -> AuthorizedRunbookScope:
        if self.risk_level is RiskLevel.HIGH:
            raise ValueError("HIGH risk authorization is forbidden")
        return self


class MasterAuthorizationRecord(AuthorizationRecord):
    schema_version: Literal["dta-v2.master-authorization-record.v1"]
    authorization_mode: Literal["DTA_V2_MASTER_STANDING_AUTHORIZATION"]
    goal_version: Literal["dta-v2-master-v1"]
    goal_sha256: Literal[
        "7ecced0b1698f4a8b3e1f4e8a25f72598cc3ac6d3791bd74827618eb00bb10ea"
    ]
    environment_scope: Literal["LOCAL_UNIX_DOCKER"]
    sandbox_identity: Literal["ecomsre-live-sandbox-v1"]
    allowed_scenario_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=16)
    registry_sha256: Sha256
    authorized_runbooks: tuple[AuthorizedRunbookScope, ...] = Field(
        min_length=1,
        max_length=3,
    )
    maximum_active_transactions: Literal[1]

    @model_validator(mode="after")
    def require_master_semantics(self) -> MasterAuthorizationRecord:
        if self.goal_sha256 != _MASTER_GOAL_SHA256:
            raise ValueError("Master Authorization differs from the active Goal")
        if self.allowed_scenario_ids != _MVP_SCENARIO_IDS:
            raise ValueError("Master Authorization requires the exact MVP scenario set")
        if self.allowed_scenario_ids != tuple(sorted(self.allowed_scenario_ids)):
            raise ValueError("authorized scenarios are not canonical")
        if len(self.allowed_scenario_ids) != len(set(self.allowed_scenario_ids)):
            raise ValueError("authorized scenarios contain duplicates")
        ids = tuple(scope.runbook_id for scope in self.authorized_runbooks)
        if ids != tuple(sorted(RunbookId, key=lambda item: item.value)):
            raise ValueError("Master Authorization requires the exact MVP Runbooks")
        if ids != tuple(sorted(ids, key=lambda item: item.value)):
            raise ValueError("authorized Runbooks are not canonical")
        if len(ids) != len(set(ids)):
            raise ValueError("authorized Runbooks contain duplicates")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected:
            raise ValueError("authorization digest does not bind Master scope")
        return self


class AttemptAuthorizationRecord(AuthorizationRecord):
    schema_version: Literal["dta-v2.attempt-authorization-record.v1"]
    authorization_mode: Literal["DTA_V2_MASTER_RUN_BOUND_CHILD"]
    master_authorization_sha256: Sha256
    run_id: RunId
    attempt_id: Identifier
    scenario_id: Identifier
    current_state_sha256: Sha256
    proposal_sha256: Sha256
    candidate_set_sha256: Sha256
    diagnosis_sha256: Sha256
    resolved_evidence_sha256: Sha256
    registry_sha256: Sha256
    runbook_id: RunbookId
    runbook_sha256: Sha256
    target_service: Identifier
    parameters_sha256: Sha256
    risk_level: RiskLevel
    maximum_forward_steps: StrictInt = Field(ge=1, le=2)
    expires_at: datetime

    @model_validator(mode="after")
    def require_attempt_semantics(self) -> AttemptAuthorizationRecord:
        _require_utc(self.expires_at, field_name="expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("attempt authorization expiry must follow issuance")
        if self.risk_level is RiskLevel.HIGH:
            raise ValueError("HIGH risk authorization is forbidden")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected:
            raise ValueError("authorization digest does not bind attempt scope")
        return self


def _with_digest(model_type: type[DtaModel], payload: dict[str, Any]):
    draft = model_type.model_construct(
        **payload,
        authorization_sha256="0" * 64,
    )
    return model_type.model_validate(
        {
            **payload,
            "authorization_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"authorization_sha256"})
            ),
        }
    )


def build_master_authorization(
    *,
    registry: RunbookRegistry,
    goal_version: str,
    goal_sha256: str,
    allowed_scenario_ids: tuple[str, ...],
    sandbox_identity: str,
    issued_at: datetime,
) -> MasterAuthorizationRecord:
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    scopes = tuple(
        AuthorizedRunbookScope(
            runbook_id=runbook.runbook_id,
            runbook_sha256=semantic_sha256(runbook.model_dump(mode="json")),
            target_service=runbook.target_services[0],
            risk_level=runbook.risk_level,
            parameter_schema_sha256=runbook_parameter_schema_sha256(runbook),
            maximum_forward_steps=runbook.maximum_forward_steps,
        )
        for runbook in registry.runbooks
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.master-authorization-record.v1",
        "authorization_id": "dta-v2-master-v1",
        "authorization_mode": "DTA_V2_MASTER_STANDING_AUTHORIZATION",
        "approver": "Minghong Sun",
        "authorization_source": (
            "USER_EXPLICIT_DTA_V2_MASTER_GOAL_AUTHORIZATION"
        ),
        "command_execution": "CODEX_DELEGATED_EXECUTION",
        "codex_autonomous_self_approval": False,
        "additional_human_confirmation_required": False,
        "goal_version": goal_version,
        "goal_sha256": goal_sha256,
        "environment_scope": "LOCAL_UNIX_DOCKER",
        "sandbox_identity": sandbox_identity,
        "allowed_scenario_ids": tuple(sorted(allowed_scenario_ids)),
        "registry_sha256": registry.registry_sha256,
        "authorized_runbooks": scopes,
        "maximum_active_transactions": 1,
        "issued_at": issued_at,
    }
    return MasterAuthorizationRecord.model_validate(
        _with_digest(MasterAuthorizationRecord, payload)
    )


def _require_scope(
    master: MasterAuthorizationRecord,
    *,
    runbook: RunbookSpec,
    runbook_sha256: str,
) -> AuthorizedRunbookScope:
    matching = tuple(
        scope
        for scope in master.authorized_runbooks
        if scope.runbook_id is runbook.runbook_id
    )
    if len(matching) != 1:
        raise ValueError("Master Authorization does not allow the Runbook")
    scope = matching[0]
    if (
        scope.runbook_sha256 != runbook_sha256
        or scope.target_service != runbook.target_services[0]
        or scope.risk_level is not runbook.risk_level
        or scope.parameter_schema_sha256
        != runbook_parameter_schema_sha256(runbook)
        or scope.maximum_forward_steps != runbook.maximum_forward_steps
    ):
        raise ValueError("Master Authorization Runbook scope differs")
    return scope


def derive_attempt_authorization(
    *,
    master: MasterAuthorizationRecord,
    scenario_id: str,
    registry: RunbookRegistry,
    candidate_set: CandidateSet,
    diagnosis: DtaDiagnosis,
    diagnosis_evidence: ResolvedDiagnosisEvidenceView,
    proposal: ActionProposal,
    current_state: CurrentStateSnapshot,
    issued_at: datetime,
    expires_at: datetime,
) -> AttemptAuthorizationRecord:
    master = MasterAuthorizationRecord.model_validate(master.model_dump(mode="python"))
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    current_state = CurrentStateSnapshot.model_validate(
        current_state.model_dump(mode="python")
    )
    if issued_at < master.issued_at:
        raise ValueError("attempt authorization predates Master issuance")
    if scenario_id not in master.allowed_scenario_ids:
        raise ValueError("scenario is outside Master Authorization")
    if master.registry_sha256 != registry.registry_sha256:
        raise ValueError("Master Authorization Registry differs")
    validate_action_proposal_binding(
        proposal=proposal,
        candidate_set=candidate_set,
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=diagnosis_evidence,
    )
    if (
        proposal.disposition is not ActionDisposition.EXECUTE_RUNBOOK
        or proposal.runbook_id is None
        or proposal.runbook_sha256 is None
        or proposal.target_service is None
    ):
        raise ValueError("attempt authorization requires an executable proposal")
    runbook = registry.require(proposal.runbook_id)
    runbook_sha256 = semantic_sha256(runbook.model_dump(mode="json"))
    scope = _require_scope(
        master,
        runbook=runbook,
        runbook_sha256=runbook_sha256,
    )
    if (
        current_state.run_id != proposal.run_id
        or current_state.target_logical_service != proposal.target_service
    ):
        raise ValueError("attempt current state differs from the proposal")
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.attempt-authorization-record.v1",
        "authorization_id": f"attempt-auth:{current_state.attempt_id}",
        "authorization_mode": "DTA_V2_MASTER_RUN_BOUND_CHILD",
        "approver": master.approver,
        "authorization_source": master.authorization_source,
        "command_execution": master.command_execution,
        "codex_autonomous_self_approval": False,
        "additional_human_confirmation_required": False,
        "master_authorization_sha256": master.authorization_sha256,
        "run_id": current_state.run_id,
        "attempt_id": current_state.attempt_id,
        "scenario_id": scenario_id,
        "current_state_sha256": current_state.snapshot_sha256,
        "proposal_sha256": proposal.proposal_sha256,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "diagnosis_sha256": candidate_set.diagnosis_sha256,
        "resolved_evidence_sha256": diagnosis_evidence.resolved_evidence_sha256,
        "registry_sha256": registry.registry_sha256,
        "runbook_id": runbook.runbook_id,
        "runbook_sha256": runbook_sha256,
        "target_service": proposal.target_service,
        "parameters_sha256": action_parameters_sha256(proposal),
        "risk_level": scope.risk_level,
        "maximum_forward_steps": scope.maximum_forward_steps,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    return AttemptAuthorizationRecord.model_validate(
        _with_digest(AttemptAuthorizationRecord, payload)
    )


def persist_master_authorization(
    path: Path,
    authorization: MasterAuthorizationRecord,
) -> None:
    authorization = MasterAuthorizationRecord.model_validate(
        authorization.model_dump(mode="python")
    )
    parent = path.parent
    for component in (path, parent, *parent.parents):
        if component.is_symlink():
            raise ValueError("authorization path must not contain a symlink")
    private_root = parent.parent
    missing: list[Path] = []
    cursor = parent
    while not cursor.exists() and cursor != private_root.parent:
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    for directory in (private_root, parent):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("authorization directory must be regular")
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise ValueError("authorization directory mode must be 0700")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        data = json.dumps(
            authorization.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(path, 0o600)


def load_master_authorization(path: Path) -> MasterAuthorizationRecord:
    for component in (path, path.parent, *path.parent.parents):
        if component.is_symlink():
            raise ValueError("authorization path must not contain a symlink")
    if not path.is_file():
        raise ValueError("authorization file must be a regular non-symlink")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
    if mode != 0o600:
        os.close(descriptor)
        raise ValueError("authorization file mode must be 0600")
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        os.close(descriptor)
        raise ValueError("authorization directory mode must be 0700")
    with os.fdopen(descriptor, "r", encoding="utf-8") as source:
        return MasterAuthorizationRecord.model_validate_json(source.read())
