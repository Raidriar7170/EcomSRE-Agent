"""Create-once semantic freeze before Product v0.2.3.2.1 formal execution."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.incidents.contracts import EvidenceBundleV1
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NoFaultEvidenceAssessmentV0232,
)
from ecomsre.product.pilot.product_state_clone_v0232 import ProductStateSourceV0232
from ecomsre.product.pilot.product_state_clone_v02321 import (
    PreflightStateCloneReportV02321,
)
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    TrafficPreflightLedgerV02321,
)
from ecomsre.product.pilot.traffic_preflight_live_v02321 import (
    TRAFFIC_PREFLIGHT_PASS_V02321,
    LiveTrafficPreflightAttemptV02321,
    LiveTrafficPreflightPassV02321,
)
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    load_traffic_profile_v0232,
)
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    TrafficHarnessTypedRequestPlanV02321,
)


FORMAL_CONTRACT_FREEZE_PASS_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_FORMAL_CONTRACT_FREEZE_PASS"
] = "ECOMSRE_PRODUCT_V02321_FORMAL_CONTRACT_FREEZE_PASS"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_FROZEN_FILE_ROLES_V02321 = {
    "TRAFFIC_CONTRACT": "config/product-v0232/traffic/contract.json",
    "PREFLIGHT_PROFILE": "config/product-v0232/traffic/preflight-profile.json",
    "FORMAL_PROFILE": "config/product-v0232/traffic/formal-profile.json",
    "PREFLIGHT_PROFILE_BINDING": (
        "config/product-v02321/traffic/preflight-profile-binding.json"
    ),
    "FORMAL_PROFILE_BINDING": (
        "config/product-v02321/traffic/formal-profile-binding.json"
    ),
    "TYPED_REQUEST_PLAN": "config/product-v02321/typed-request-plan.json",
    "PREFLIGHT_STATE_CLONE": (
        "docs/analysis/product-v02321-product-state-clone-preflight.json"
    ),
    "PREFLIGHT_ATTEMPT": (
        "docs/analysis/product-v02321-traffic-preflight-attempt-1.json"
    ),
    "PREFLIGHT_LEDGER": ("docs/analysis/product-v02321-traffic-preflight-ledger.json"),
    "PREFLIGHT_PASS": "docs/analysis/product-v02321-traffic-preflight.json",
    "INCREMENT3_PROGRESS": "docs/analysis/product-v02321-progress.json",
    "TYPED_REQUEST_SCHEMA": ("src/ecomsre/product/pilot/typed_request_plan_v02321.py"),
    "EVIDENCE_BUNDLE_SCHEMA": "src/ecomsre/product/incidents/contracts.py",
    "EVIDENCE_INDEX_SCHEMA": (
        "src/ecomsre/product/incidents/evidence_binding_v0232.py"
    ),
    "NOFAULT_SCORER": "src/ecomsre/product/pilot/nofault_acceptance_v0232.py",
}
_REVIEWED_IMPLEMENTATION_FILES_V02321 = {
    "formal_contract_verifier_file_sha256": (
        "src/ecomsre/product/pilot/formal_contract_v02321.py"
    ),
    "formal_nofault_contract_file_sha256": (
        "src/ecomsre/product/pilot/formal_nofault_v02321.py"
    ),
    "formal_nofault_runner_file_sha256": (
        "scripts/product_v02321/run_formal_nofault.py"
    ),
    "formal_state_clone_contract_file_sha256": (
        "src/ecomsre/product/pilot/product_state_clone_v02321.py"
    ),
    "formal_state_clone_runner_file_sha256": (
        "scripts/product_v02321/run_state_clone.py"
    ),
}
_PREMATURE_FORMAL_PATHS_V02321 = (
    ".local/product-v02321/formal",
    ".local/product-v02321/formal-reservation.json",
    "docs/analysis/product-v02321-product-state-clone-formal.json",
    "docs/analysis/product-v02321-runtime-authority.json",
    "docs/analysis/product-v02321-baseline-restart.json",
    "docs/analysis/product-v02321-formal-traffic.json",
    "docs/analysis/product-v02321-fresh-runtime-snapshot.json",
    "docs/analysis/product-v02321-formal-blocker.json",
    "docs/results/product-v02321-nofault-acceptance.json",
    "docs/results/product-v02321-nofault-acceptance.md",
    "docs/results/product-v02321-limitations.md",
    "docs/results/product-v02321-interview-brief.md",
    "docs/analysis/product-v02321-knowledge-loop-handoff.json",
    "docs/analysis/product-v02321-knowledge-loop-handoff.md",
)
_REVIEW_JSON_START_V02321 = (
    "<!-- ECOMSRE_PRODUCT_V02321_REVIEW_JSON_START -->\n```json\n"
)
_REVIEW_JSON_END_V02321 = "\n```\n<!-- ECOMSRE_PRODUCT_V02321_REVIEW_JSON_END -->"


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path.name} is not a regular frozen file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_sha256(model: type[ProductModelV1]) -> str:
    return semantic_sha256_v22(model.model_json_schema())


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return payload


def _require_missing_path(project: Path, relative: str, *, label: str) -> None:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"{label} locator differs")
    candidate = project / relative_path
    if os.path.lexists(candidate):
        raise FileExistsError(f"Product v0.2.3.2.1 {label} already exists")
    current = project
    for part in relative_path.parts[:-1]:
        current /= part
        if not os.path.lexists(current):
            break
        if current.is_symlink() or not current.is_dir():
            raise ValueError(f"Product v0.2.3.2.1 {label} parent differs")


def _load_profile_binding(
    project: Path,
    *,
    role: Literal["PREFLIGHT", "FORMAL"],
    profile_sha256: str,
    predecessor_head: str,
) -> dict[str, Any]:
    stem = role.lower()
    path = project / f"config/product-v02321/traffic/{stem}-profile-binding.json"
    payload = _load_object(path)
    body = dict(payload)
    supplied = body.pop("binding_sha256", None)
    source_path = f"config/product-v0232/traffic/{stem}-profile.json"
    expected_fields = {
        "schema_version",
        "role",
        "source_path",
        "source_file_sha256",
        "profile_sha256",
        "predecessor_head",
        "binding_sha256",
    }
    if (
        set(payload) != expected_fields
        or supplied != semantic_sha256_v22(body)
        or payload.get("schema_version")
        != "ecomsre.product.traffic-profile-binding.v02321"
        or payload.get("role") != role
        or payload.get("source_path") != source_path
        or payload.get("source_file_sha256") != _sha256_file(project / source_path)
        or payload.get("profile_sha256") != profile_sha256
        or payload.get("predecessor_head") != predecessor_head
    ):
        raise ValueError("Product v0.2.3.2.1 profile binding differs")
    return payload


def _load_progress(project: Path) -> dict[str, Any]:
    progress = _load_object(project / "docs/analysis/product-v02321-progress.json")
    body = dict(progress)
    supplied = body.pop("progress_sha256", None)
    if supplied != semantic_sha256_v22(body):
        raise ValueError("Product v0.2.3.2.1 progress digest differs")
    return progress


class FrozenSemanticFileV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=512)
    file_sha256: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=1)


class FormalClonePlanV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-clone-plan.v02321"] = (
        "ecomsre.product.formal-clone-plan.v02321"
    )
    role: Literal["FORMAL"] = "FORMAL"
    status: Literal["PLANNED_NOT_CREATED"] = "PLANNED_NOT_CREATED"
    source_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_locator: str = Field(min_length=1, max_length=512)
    starting_incident_count: Literal[1] = 1
    starting_diagnosis_count: Literal[1] = 1
    starting_fault_family_count: Literal[0] = 0
    starting_knowledge_artifact_count: Literal[0] = 0
    create_only_after_pre_execution_review: Literal[True] = True
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_plan(self) -> "FormalClonePlanV02321":
        expected_locator = (
            ".local/product-v02321/product-state/"
            f"formal-{self.source_state_sha256[:24]}/product"
        )
        body = self.model_dump(mode="json", exclude={"plan_sha256"})
        if (
            self.destination_locator != expected_locator
            or self.plan_sha256 != semantic_sha256_v22(body)
        ):
            raise ValueError("formal clone plan binding differs")
        return self

    @classmethod
    def build(cls, *, source_state_sha256: str) -> "FormalClonePlanV02321":
        body = {
            "schema_version": "ecomsre.product.formal-clone-plan.v02321",
            "role": "FORMAL",
            "status": "PLANNED_NOT_CREATED",
            "source_state_sha256": source_state_sha256,
            "destination_locator": (
                ".local/product-v02321/product-state/"
                f"formal-{source_state_sha256[:24]}/product"
            ),
            "starting_incident_count": 1,
            "starting_diagnosis_count": 1,
            "starting_fault_family_count": 0,
            "starting_knowledge_artifact_count": 0,
            "create_only_after_pre_execution_review": True,
        }
        return cls.model_validate({**body, "plan_sha256": semantic_sha256_v22(body)})


class FormalContractFreezeV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-contract-freeze.v02321"] = (
        "ecomsre.product.formal-contract-freeze.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_FORMAL_CONTRACT_FREEZE_PASS"] = (
        FORMAL_CONTRACT_FREEZE_PASS_V02321
    )
    traffic_preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_ledger_sha256: str = Field(pattern=_SHA256_PATTERN)
    typed_request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    progress_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    typed_request_plan_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_bundle_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_evidence_index_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    nofault_assessment_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_plan: FormalClonePlanV02321
    frozen_files: tuple[FrozenSemanticFileV02321, ...] = Field(min_length=1)
    formal_healthy_traffic_execution_count: Literal[0] = 0
    accepted_successor_incident_count: Literal[0] = 0
    successor_diagnosis_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    knowledge_loop_campaign_count: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    freeze_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_freeze(self) -> "FormalContractFreezeV02321":
        expected_paths = tuple(sorted(_FROZEN_FILE_ROLES_V02321.items()))
        observed_paths = tuple((item.role, item.path) for item in self.frozen_files)
        expected_schema_hashes = (
            _schema_sha256(TrafficHarnessTypedRequestPlanV02321),
            _schema_sha256(EvidenceBundleV1),
            _schema_sha256(DiagnosisEvidenceIndexV0232),
            _schema_sha256(NoFaultEvidenceAssessmentV0232),
        )
        body = self.model_dump(mode="json", exclude={"freeze_sha256"})
        if (
            self.traffic_contract_sha256
            != "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
            or self.preflight_profile_sha256
            != "20481ac92973ccf5de7510f565f066f13b9e1161e0e36faecec11cd12a40aa4a"
            or self.formal_profile_sha256
            != "0110803ab9b39bf397295f1fd8904aee31fabf9b82b314bf586fae98188f6ce7"
            or (
                self.typed_request_plan_schema_sha256,
                self.evidence_bundle_schema_sha256,
                self.diagnosis_evidence_index_schema_sha256,
                self.nofault_assessment_schema_sha256,
            )
            != expected_schema_hashes
            or self.formal_clone_plan.source_state_sha256 != self.source_state_sha256
            or observed_paths != expected_paths
            or self.freeze_sha256 != semantic_sha256_v22(body)
        ):
            raise ValueError("formal contract freeze binding differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalContractFreezeV02321":
        serializable = dict(payload)
        serializable["formal_clone_plan"] = FormalClonePlanV02321.model_validate(
            payload["formal_clone_plan"]
        ).model_dump(mode="json")
        serializable["frozen_files"] = [
            FrozenSemanticFileV02321.model_validate(item).model_dump(mode="json")
            for item in payload["frozen_files"]
        ]
        body = {
            "schema_version": "ecomsre.product.formal-contract-freeze.v02321",
            "terminal": FORMAL_CONTRACT_FREEZE_PASS_V02321,
            **serializable,
        }
        return cls.model_validate({**body, "freeze_sha256": semantic_sha256_v22(body)})


class FormalPreExecutionReviewV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-pre-execution-review.v02321"] = (
        "ecomsre.product.formal-pre-execution-review.v02321"
    )
    reviewed_at_utc: str
    review_disposition: Literal["PASS"] = "PASS"
    must_fix_count: Literal[0] = 0
    claim_accuracy: Literal["PASS"] = "PASS"
    formal_execution_authorized: Literal[True] = True
    action_authority: Literal["NONE"] = "NONE"
    formal_contract_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_contract_freeze_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    progress_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_attempt_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_ledger_sha256: str = Field(pattern=_SHA256_PATTERN)
    typed_request_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    product_state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_destination_locator: str = Field(min_length=1, max_length=512)
    formal_clone_observed_status: Literal["ABSENT"] = "ABSENT"
    formal_contract_verifier_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_nofault_contract_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_nofault_runner_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_state_clone_contract_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_state_clone_runner_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    infrastructure_session_count: Literal[1] = 1
    traffic_attempt_count: Literal[1] = 1
    formal_healthy_traffic_execution_count: Literal[0] = 0
    accepted_successor_incident_count: Literal[0] = 0
    successor_diagnosis_count: Literal[0] = 0
    fault_attempt_count: Literal[0] = 0
    knowledge_loop_campaign_count: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    review_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("reviewed_at_utc")
    @classmethod
    def require_utc_timestamp(cls, value: str) -> str:
        if not value.endswith("Z"):
            raise ValueError("pre-execution review timestamp is not UTC")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("pre-execution review timestamp differs") from error
        if parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("pre-execution review timestamp is not UTC")
        return value

    @model_validator(mode="after")
    def require_self_seal(self) -> "FormalPreExecutionReviewV02321":
        body = self.model_dump(mode="json", exclude={"review_sha256"})
        if self.review_sha256 != semantic_sha256_v22(body):
            raise ValueError("pre-execution review digest differs")
        return self


def build_formal_contract_freeze_v02321(root: Path) -> FormalContractFreezeV02321:
    project = Path(root).resolve(strict=True)
    preflight = LiveTrafficPreflightPassV02321.model_validate_json(
        (project / "docs/analysis/product-v02321-traffic-preflight.json").read_bytes()
    )
    if preflight.terminal != TRAFFIC_PREFLIGHT_PASS_V02321:
        raise ValueError("Product v0.2.3.2.1 traffic preflight is not PASS")
    source_payload = json.loads(
        (project / "docs/analysis/product-v0232-predecessor-audit.json").read_text(
            encoding="utf-8"
        )
    )
    source = ProductStateSourceV0232.model_validate(source_payload.get("source_state"))
    attempt = LiveTrafficPreflightAttemptV02321.model_validate_json(
        (
            project / "docs/analysis/product-v02321-traffic-preflight-attempt-1.json"
        ).read_bytes()
    )
    ledger = TrafficPreflightLedgerV02321.model_validate_json(
        (
            project / "docs/analysis/product-v02321-traffic-preflight-ledger.json"
        ).read_bytes()
    )
    typed_plan = TrafficHarnessTypedRequestPlanV02321.model_validate_json(
        (project / "config/product-v02321/typed-request-plan.json").read_bytes()
    )
    clone_report = PreflightStateCloneReportV02321.model_validate_json(
        (
            project / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_bytes()
    )
    progress = _load_progress(project)
    contract = load_checkout_traffic_contract_v0232(project)
    preflight_profile = load_traffic_profile_v0232(project, role="PREFLIGHT")
    formal_profile = load_traffic_profile_v0232(project, role="FORMAL")
    _load_profile_binding(
        project,
        role="PREFLIGHT",
        profile_sha256=preflight_profile.profile_sha256,
        predecessor_head=str(progress.get("predecessor_head")),
    )
    _load_profile_binding(
        project,
        role="FORMAL",
        profile_sha256=formal_profile.profile_sha256,
        predecessor_head=str(progress.get("predecessor_head")),
    )
    plan = FormalClonePlanV02321.build(source_state_sha256=source.source_sha256)
    _require_missing_path(
        project,
        plan.destination_locator,
        label="formal clone",
    )
    for relative in _PREMATURE_FORMAL_PATHS_V02321:
        _require_missing_path(project, relative, label="formal output")
    expected_progress = {
        "terminal": TRAFFIC_PREFLIGHT_PASS_V02321,
        "increment": 3,
        "live_request_plan_status": "PASS",
        "live_traffic_preflight_status": "PASS",
        "typed_request_plan_sha256": typed_plan.plan_sha256,
        "product_state_clone_report_sha256": clone_report.report_sha256,
        "product_state_clone_sha256": clone_report.clone.clone_sha256,
        "source_state_sha256": source.source_sha256,
        "traffic_preflight_sha256": preflight.preflight_sha256,
        "traffic_preflight_attempt_sha256": attempt.attempt_sha256,
        "traffic_preflight_ledger_sha256": ledger.ledger_sha256,
        "infrastructure_session_count": 1,
        "traffic_attempt_count": 1,
        "live_traffic_preflight_attempt_count": 1,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "action_authority": "NONE",
    }
    source_counts = source.source_counts
    if (
        preflight.attempt != attempt
        or attempt.ledger != ledger
        or attempt.product_state_clone_report != clone_report
        or typed_plan.role != "PREFLIGHT"
        or typed_plan.attempt_ordinal != 1
        or typed_plan.plan_sha256 != attempt.typed_request_plan_sha256
        or typed_plan.state_clone_sha256 != attempt.product_state_clone_sha256
        or clone_report.source_state != source
        or preflight.frozen_traffic_contract_sha256 != contract.contract_sha256
        or attempt.traffic_contract_sha256 != contract.contract_sha256
        or preflight.frozen_preflight_profile_sha256 != preflight_profile.profile_sha256
        or attempt.traffic_profile_sha256 != preflight_profile.profile_sha256
        or preflight.frozen_formal_profile_sha256 != formal_profile.profile_sha256
        or attempt.formal_profile_sha256 != formal_profile.profile_sha256
        or preflight.typed_request_plan_schema_sha256
        != _schema_sha256(TrafficHarnessTypedRequestPlanV02321)
        or attempt.source_state_before_sha256 != source.source_sha256
        or attempt.source_state_after_sha256 != source.source_sha256
        or source_counts.incident_count != plan.starting_incident_count
        or source_counts.diagnosis_count != plan.starting_diagnosis_count
        or source_counts.fault_family_count != plan.starting_fault_family_count
        or source_counts.knowledge_artifact_count
        != plan.starting_knowledge_artifact_count
    ):
        raise ValueError("Product v0.2.3.2.1 public preflight binding differs")
    if any(progress.get(key) != value for key, value in expected_progress.items()):
        raise ValueError("Product v0.2.3.2.1 progress binding differs")
    bindings = tuple(
        FrozenSemanticFileV02321(
            role=role,
            path=relative,
            file_sha256=_sha256_file(project / relative),
            size_bytes=(project / relative).stat().st_size,
        )
        for role, relative in sorted(_FROZEN_FILE_ROLES_V02321.items())
    )
    return FormalContractFreezeV02321.build(
        traffic_preflight_sha256=preflight.preflight_sha256,
        traffic_preflight_attempt_sha256=preflight.attempt.attempt_sha256,
        traffic_execution_sha256=preflight.attempt.traffic_execution.execution_sha256,
        traffic_preflight_ledger_sha256=preflight.attempt.ledger.ledger_sha256,
        typed_request_plan_sha256=typed_plan.plan_sha256,
        product_state_clone_report_sha256=clone_report.report_sha256,
        product_state_clone_sha256=clone_report.clone.clone_sha256,
        progress_sha256=progress["progress_sha256"],
        traffic_contract_sha256=contract.contract_sha256,
        preflight_profile_sha256=preflight_profile.profile_sha256,
        formal_profile_sha256=formal_profile.profile_sha256,
        typed_request_plan_schema_sha256=_schema_sha256(
            TrafficHarnessTypedRequestPlanV02321
        ),
        evidence_bundle_schema_sha256=_schema_sha256(EvidenceBundleV1),
        diagnosis_evidence_index_schema_sha256=_schema_sha256(
            DiagnosisEvidenceIndexV0232
        ),
        nofault_assessment_schema_sha256=_schema_sha256(NoFaultEvidenceAssessmentV0232),
        source_state_sha256=source.source_sha256,
        formal_clone_plan=plan,
        frozen_files=bindings,
        formal_healthy_traffic_execution_count=0,
        accepted_successor_incident_count=0,
        successor_diagnosis_count=0,
        fault_attempt_count=0,
        knowledge_loop_campaign_count=0,
        agent_writes=0,
        runbook_executions=0,
        provider_calls=0,
        action_authority="NONE",
    )


def verify_formal_contract_freeze_v02321(
    root: Path,
) -> FormalContractFreezeV02321:
    project = Path(root).resolve(strict=True)
    frozen = FormalContractFreezeV02321.model_validate_json(
        (
            project / "docs/analysis/product-v02321-formal-contract-freeze.json"
        ).read_bytes()
    )
    rebuilt = build_formal_contract_freeze_v02321(project)
    if frozen != rebuilt:
        raise ValueError("Product v0.2.3.2.1 formal contract freeze differs")
    return frozen


def require_formal_pre_execution_review_binding_v02321(
    review: FormalPreExecutionReviewV02321,
    freeze: FormalContractFreezeV02321,
    *,
    freeze_file_sha256: str,
) -> FormalPreExecutionReviewV02321:
    expected = {
        "formal_contract_freeze_sha256": freeze.freeze_sha256,
        "formal_contract_freeze_file_sha256": freeze_file_sha256,
        "progress_sha256": freeze.progress_sha256,
        "traffic_preflight_sha256": freeze.traffic_preflight_sha256,
        "traffic_preflight_attempt_sha256": (freeze.traffic_preflight_attempt_sha256),
        "traffic_execution_sha256": freeze.traffic_execution_sha256,
        "traffic_preflight_ledger_sha256": (freeze.traffic_preflight_ledger_sha256),
        "typed_request_plan_sha256": freeze.typed_request_plan_sha256,
        "product_state_clone_report_sha256": (freeze.product_state_clone_report_sha256),
        "product_state_clone_sha256": freeze.product_state_clone_sha256,
        "source_state_sha256": freeze.source_state_sha256,
        "formal_clone_plan_sha256": freeze.formal_clone_plan.plan_sha256,
        "formal_clone_destination_locator": (
            freeze.formal_clone_plan.destination_locator
        ),
        "formal_healthy_traffic_execution_count": (
            freeze.formal_healthy_traffic_execution_count
        ),
        "accepted_successor_incident_count": (freeze.accepted_successor_incident_count),
        "successor_diagnosis_count": freeze.successor_diagnosis_count,
        "fault_attempt_count": freeze.fault_attempt_count,
        "knowledge_loop_campaign_count": freeze.knowledge_loop_campaign_count,
        "agent_writes": freeze.agent_writes,
        "runbook_executions": freeze.runbook_executions,
        "provider_calls": freeze.provider_calls,
        "action_authority": freeze.action_authority,
    }
    if (
        any(getattr(review, key) != value for key, value in expected.items())
        or review.infrastructure_session_count != 1
        or review.traffic_attempt_count != 1
        or review.formal_clone_observed_status != "ABSENT"
    ):
        raise ValueError("Product v0.2.3.2.1 pre-execution review binding differs")
    return review


def verify_formal_pre_execution_review_v02321(
    root: Path,
) -> FormalPreExecutionReviewV02321:
    project = Path(root).resolve(strict=True)
    freeze = verify_formal_contract_freeze_v02321(project)
    freeze_path = project / "docs/analysis/product-v02321-formal-contract-freeze.json"
    review_path = (
        project / "docs/external-reviews/product-v02321-pre-execution-review.md"
    )
    if review_path.is_symlink() or not review_path.is_file():
        raise ValueError("Product v0.2.3.2.1 pre-execution review file differs")
    text = review_path.read_text(encoding="utf-8")
    if (
        text.count(_REVIEW_JSON_START_V02321) != 1
        or text.count(_REVIEW_JSON_END_V02321) != 1
    ):
        raise ValueError("Product v0.2.3.2.1 pre-execution review block differs")
    payload_text = text.split(_REVIEW_JSON_START_V02321, 1)[1].split(
        _REVIEW_JSON_END_V02321, 1
    )[0]
    review = FormalPreExecutionReviewV02321.model_validate_json(payload_text)
    review = require_formal_pre_execution_review_binding_v02321(
        review,
        freeze,
        freeze_file_sha256=_sha256_file(freeze_path),
    )
    implementation_hashes = {
        field: _sha256_file(project / relative)
        for field, relative in _REVIEWED_IMPLEMENTATION_FILES_V02321.items()
    }
    if any(
        getattr(review, field) != observed
        for field, observed in implementation_hashes.items()
    ):
        raise ValueError(
            "Product v0.2.3.2.1 pre-execution implementation review differs"
        )
    return review


__all__ = [
    "FORMAL_CONTRACT_FREEZE_PASS_V02321",
    "FormalClonePlanV02321",
    "FormalContractFreezeV02321",
    "FormalPreExecutionReviewV02321",
    "FrozenSemanticFileV02321",
    "build_formal_contract_freeze_v02321",
    "require_formal_pre_execution_review_binding_v02321",
    "verify_formal_contract_freeze_v02321",
    "verify_formal_pre_execution_review_v02321",
]
