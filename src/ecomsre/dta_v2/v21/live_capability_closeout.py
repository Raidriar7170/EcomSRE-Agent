"""Append-only PR-F capability-miss and positive-continuation contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal

from pydantic import Field, StrictInt, model_validator
from pydantic_core import to_jsonable_python
from typing_extensions import Self

from ecomsre.dta_v2.v21.agent import AgentRunTerminalV21, DtaAgentRunResultV21
from ecomsre.dta_v2.v21.agent_contracts import (
    AgentArmV21,
    build_candidate_action_view_v21,
)
from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    DtaModelV21,
    FaultDomainV21,
    FaultMechanismV21,
    Sha256V21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.live_contracts import (
    LiveAttemptClosureV21,
    LiveBaselineEvidenceV21,
    LiveEnvironmentAdmissionV2,
    LiveFaultImpactEvidenceV21,
    LiveReadinessV2,
    LiveScenarioV21,
    load_live_demo_config_v21,
)
from ecomsre.dta_v2.v21.live_execution import LiveMasterAuthorizationV21
from ecomsre.dta_v2.v21.live_reconciliation import (
    IndependentRetryReviewV1,
    PostTerminalReconciliationV1,
    ResolvedComposeIdentityV1,
    RetryAdmissionV1,
    RetryConsumptionV1,
    verify_post_terminal_reconciliation_v1,
    verify_retry_consumption_v1,
)
from ecomsre.dta_v2.v21.live_verifiers import verify_live_agent_result_v21
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre_live_sandbox.contracts import (
    canonical_json_bytes,
    ensure_private_directory,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_live_sandbox.environment import ExactCommandRunner


AMENDMENT3_RAW_SHA256_V1 = (
    "24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a"
)
DECISION_ID_V1 = "DEC-046"
CAPABILITY_MISS_CODE_HEAD_V1 = "a167285a6a1d691709f229b26d167a7cd7c10fa0"
CAPABILITY_MISS_ATTEMPT_ID_V1 = "dta-v21-prf-01-no-fault-a167285a6a1d"
ORIGINAL_BLOCKED_ATTEMPT_ID_V1 = "dta-v21-prf-01-no-fault-422f015451fd"
POSITIVE_CONTINUATION_CONSUMPTION_FILENAME_V1 = "positive-continuation.v1.json"
POSITIVE_CONTINUATION_ORDER_V1 = (
    LiveScenarioV21.AD_CPU_SATURATION,
    LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
    LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
)
PLANNER_IDENTITY_SHA256_V1 = (
    "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
)
PROVIDER_MODEL_V1 = "gpt-5.4-mini-2026-03-17"
AD_PROTOCOL_SHA256_V1 = (
    "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
)
_COMMAND_RUNNER = ExactCommandRunner()

_CAPABILITY_BOUND_JSON_V1 = {
    "readiness": (
        "readiness/a167285a6a1d691709f229b26d167a7cd7c10fa0/readiness.json",
        "aa6bc8fde8d76c7b9cfd8ecd6544d91726431346e6bbbfa50eaeba12e6526507",
        "5f152156e77bc19df2b539dd3efca5b7b2ec21e639f30305efddef98193f6b63",
    ),
    "readiness_copy": (
        "readiness/a167285a6a1d691709f229b26d167a7cd7c10fa0/attempts/"
        "readiness-0001/readiness.json",
        "aa6bc8fde8d76c7b9cfd8ecd6544d91726431346e6bbbfa50eaeba12e6526507",
        "5f152156e77bc19df2b539dd3efca5b7b2ec21e639f30305efddef98193f6b63",
    ),
    "readiness_compose_identity": (
        "readiness/a167285a6a1d691709f229b26d167a7cd7c10fa0/attempts/"
        "readiness-0001/compose-identity.json",
        "a2fa25f7fb39aa4809b239bf0d059f577c75ee9d7d344e441c97a0a03b26ac11",
        "76165f1c80aaacfd6c0aceae1224f1bbcabac945fee26a86fe7cb16c8c2db6db",
    ),
    "retry_admission": (
        "retry-admissions/a167285a6a1d691709f229b26d167a7cd7c10fa0/"
        "retry-admission.v1.json",
        "cb4c6b540aba8321ed8ba2fbc5b02dc3849ce4b39ff773ba4dfea7a2d8d272b8",
        "469d19213fcf1bb66aadcd2ed750b925d9f22cb85209ebcbe3a3ac296f2a0e07",
    ),
    "retry_consumption": (
        "retry-consumptions/one-retry.v1.json",
        "cfd71936b4d0027f20e112548fec884f377647204a23de093a6d123e5a49d1b0",
        "df3679d9d0b924ebad9ada609fabc15acea06eb1b11a73f4fe4d27d2f5e777ad",
    ),
    "attempt_claim": (
        "attempts/dta-v21-prf-01-no-fault-a167285a6a1d/attempt-claim.json",
        "459f0ebcf5c9937efad7396e4c7732ac5186fa5b8be20ddabd287468c9a2ad0c",
        "d594336ca9bb97e97abfe1ead19fd209a856f361492bdb131986ef4d0687eb82",
    ),
    "environment_admission": (
        "attempts/dta-v21-prf-01-no-fault-a167285a6a1d/"
        "environment-admission.json",
        "423f152799758144a9aa598316ddda4ef534c2d376505a10d9306093d63397e2",
        "353d220f7876550fea9acd9453d7bba62993de959125227271c8a96e936ae8b8",
    ),
    "attempt_compose_identity": (
        "attempts/dta-v21-prf-01-no-fault-a167285a6a1d/compose-identity.json",
        "a0616dbe9c48df5d5fe8e38c2fd0506ba97c910b4830133195d0aebefaa3186f",
        "595ad719292fb25ada36bec49a2f3cb034750ca923ed9f6762a97e4670bcab72",
    ),
    "baseline_evidence": (
        "attempts/dta-v21-prf-01-no-fault-a167285a6a1d/baseline-evidence.json",
        "d47a6137d75db128178d887c4475278a62be9aff9a866f629db13335f7f44451",
        "f4602ce285a92123426dd37090d39dc4046f7fe46cae0ccf2e7274922e85f92c",
    ),
    "fault_impact": (
        "attempts/dta-v21-prf-01-no-fault-a167285a6a1d/fault-impact.json",
        "9a32ecc14be8ab09c6d708297ce1d5fd224e646b9ba2daf4fd3318639fb5e440",
        "e4aa0b903d7fc7bdd57a795cea883932b01dc332350d81ad3ce8cd328d664211",
    ),
    "agent_result": (
        "attempts/dta-v21-prf-01-no-fault-a167285a6a1d/agent-result.json",
        "f11d954791523fb7656fda345b7681b44a1a67d71af56ce6a8cb99a528309776",
        "3176b799827f015d75ce1b8b607d2e05f4d2ac4f9ad3945f440c99b8fcc0aeb2",
    ),
    "attempt_terminal": (
        "attempts/dta-v21-prf-01-no-fault-a167285a6a1d/attempt-terminal.json",
        "0aa925a307219f6ddc5bf70e10763f766c5a400bfb3329d324e6325a0178663f",
        "17009ae00cc3f991aa42ffae843a0c854f8f58da6b60b9754848c12bc866ad35",
    ),
    "master_authorization": (
        "master-authorization.json",
        "08ec561eeec8ee9a366b7290620a6c535e9fdc0dc1556c4bd2b5cf78106b71a1",
        "e817258c66f9a86325892e8f6c22f976895845d937031c9521a594a8899007c6",
    ),
    "protocol_freeze": (
        "protocol-freeze.json",
        "9beaf16669e755773687c2125593751a9be7f4f7b95b6cec0947a4f269707080",
        "42a5bce4cc9fc1b9bef0979cc8d1b6a7439192da48b023d45bc7382953ee787a",
    ),
    "independent_review": (
        "reviews/a167285a6a1d691709f229b26d167a7cd7c10fa0/review.v1.json",
        "ca2bc1f0b5253305ae7bd137368d88878579cb30900ee5e8318841e5290cbde9",
        "7465c83e9bfbf1c0e373b871434f62cef926ed7f0dd69ef10e6cf398299a0f1b",
    ),
    "original_reconciliation": (
        "reconciliations/dta-v21-prf-01-no-fault-422f015451fd/"
        "reconciliation.v1.json",
        "a6e1d305e4bf706a5e5a60c2122796909a1bb91d8a664a7f9ac708b7a97571ed",
        "844f26a43ccd65b0f8c8a20aff059411b454c07039e472adb94f1c18ccea5095",
    ),
}
_CAPABILITY_ATTEMPT_FILES_V1 = {
    "agent-result.json",
    "attempt-claim.json",
    "attempt-terminal.json",
    "baseline-evidence.json",
    "compose-identity.json",
    "environment-admission.json",
    "fault-impact.json",
}
_CAPABILITY_FORBIDDEN_WRITE_FILES_V1 = {
    "current-state.json",
    "operational-admission.json",
    "post-write-state.json",
    "recovery-result.json",
    "run-authorization.json",
    "step-dispatch-intent.json",
    "step-receipt.json",
}


def _semantic(value: object) -> str:
    return semantic_sha256(to_jsonable_python(value))


class NoFaultCapabilityCleanupV1(DtaModelV21):
    baseline_restored: Literal[True]
    owned_containers: Literal[0]
    owned_networks: Literal[0]
    owned_volumes: Literal[0]
    non_owned_resources_changed: Literal[False]
    verdict: Literal["CLEAN"]


class NoFaultCapabilityAttemptTerminalV1(DtaModelV21):
    schema_version: Literal["dta-v21.live-attempt-failure.v1"]
    attempt_id: Literal["dta-v21-prf-01-no-fault-a167285a6a1d"]
    scenario: Literal[LiveScenarioV21.NO_FAULT]
    stage: Literal["AGENT"]
    terminal: Literal["BLOCKED_DTA_V21_PRF_SAFETY"]
    baseline_restored: Literal[True]
    cleanup: NoFaultCapabilityCleanupV1
    failure_type: Literal["ValueError"]
    raw_error_retained: Literal[False]
    restoration_operation_failed: Literal[False]


def _read_bound_json(
    prf_root: Path, label: str
) -> tuple[dict[str, object], str, str]:
    relative, expected_raw, expected_semantic = _CAPABILITY_BOUND_JSON_V1[label]
    path = prf_root / relative
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"bound No-Fault artifact {label} is missing or unsafe")
    raw = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"bound No-Fault artifact {label} is not an object")
    semantic = semantic_sha256(value)
    if raw_sha256 != expected_raw or semantic != expected_semantic:
        raise ValueError(f"bound No-Fault artifact {label} hash differs")
    return value, raw_sha256, semantic


def _allowed_attempt_history(
    attempts_root: Path, *, require_no_positive_attempts: bool
) -> None:
    if attempts_root.is_symlink() or not attempts_root.is_dir():
        raise ValueError("PR-F attempt history is missing or unsafe")
    items = tuple(attempts_root.iterdir())
    if any(item.is_symlink() or not item.is_dir() for item in items):
        raise ValueError("PR-F attempt history contains an unsafe entry")
    names = {item.name for item in items}
    historical = {ORIGINAL_BLOCKED_ATTEMPT_ID_V1, CAPABILITY_MISS_ATTEMPT_ID_V1}
    if require_no_positive_attempts:
        if names != historical:
            raise ValueError("positive PR-F slots already began")
        return
    if not historical.issubset(names):
        raise ValueError("No-Fault capability history is incomplete")
    allowed_positive = re.compile(
        r"^dta-v21-prf-(?:02-ad-cpu|03-email-unavailable|"
        r"04-product-catalog-unavailable)-[0-9a-f]{12}$"
    )
    if any(name not in historical and allowed_positive.fullmatch(name) is None for name in names):
        raise ValueError("PR-F attempt history exceeds the positive continuation")


class NoFaultCapabilityMissV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-no-fault-capability-miss.v1"]
    amendment_sha256: Literal[
        "24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a"
    ]
    decision_id: Literal["DEC-046"]
    classification: Literal["NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION"]
    code_head: Literal["a167285a6a1d691709f229b26d167a7cd7c10fa0"]
    attempt_id: Literal["dta-v21-prf-01-no-fault-a167285a6a1d"]
    scenario: Literal[LiveScenarioV21.NO_FAULT]
    stage: Literal["AGENT"]
    attempt_terminal: Literal["BLOCKED_DTA_V21_PRF_SAFETY"]
    campaign_terminal: Literal["BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED"]
    agent_terminal: Literal["COMPLETED"]
    diagnosis_root_service: Literal["checkout"]
    diagnosis_root_entity_ref: Literal["service:checkout"]
    diagnosis_fault_domain: Literal["APPLICATION"]
    diagnosis_mechanism: Literal["UNKNOWN"]
    action_disposition: Literal["NO_ACTION"]
    capability_passed: Literal[False]
    diagnosis_correct: Literal[False]
    no_write_safety_passed: Literal[True]
    fault_injected: Literal[False]
    write_admitted: Literal[False]
    forward_action_observed: Literal[False]
    baseline_restored: Literal[True]
    cleanup_clean: Literal[True]
    non_owned_change_observed: Literal[False]
    fault_operation_count: Literal[0]
    forward_step_count: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    planner_identity_sha256: Literal[
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    ]
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    held_out_execution_id: Literal["53615cdd78b348b68496f64102c0b4de"]
    held_out_seal_sha256: Literal[
        "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
    ]
    held_out_claim: Literal[
        "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
    ]
    attempt_claim_raw_sha256: Sha256V21
    attempt_claim_semantic_sha256: Sha256V21
    readiness_raw_sha256: Sha256V21
    readiness_semantic_sha256: Sha256V21
    readiness_sha256: Sha256V21
    readiness_compose_identity_sha256: Sha256V21
    environment_admission_raw_sha256: Sha256V21
    environment_admission_semantic_sha256: Sha256V21
    environment_admission_sha256: Sha256V21
    attempt_compose_identity_sha256: Sha256V21
    baseline_evidence_raw_sha256: Sha256V21
    baseline_evidence_semantic_sha256: Sha256V21
    baseline_evidence_sha256: Sha256V21
    fault_impact_raw_sha256: Sha256V21
    fault_impact_semantic_sha256: Sha256V21
    fault_impact_sha256: Sha256V21
    agent_result_raw_sha256: Sha256V21
    agent_result_semantic_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    diagnosis_sha256: Sha256V21
    candidate_set_sha256: Sha256V21
    candidate_view_sha256: Sha256V21
    action_proposal_sha256: Sha256V21
    attempt_terminal_raw_sha256: Sha256V21
    attempt_terminal_semantic_sha256: Sha256V21
    parent_retry_admission_sha256: Sha256V21
    parent_retry_consumption_sha256: Sha256V21
    original_blocker_reconciliation_sha256: Sha256V21
    master_authorization_sha256: Sha256V21
    protocol_freeze_sha256: Sha256V21
    classification_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.pr-f-no-fault-capability-miss.v1",
            **values,
        }
        return cls.model_validate(
            {**payload, "classification_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_exact_miss(self) -> Self:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"classification_sha256"})
        )
        if self.classification_sha256 != expected:
            raise ValueError("No-Fault capability classification SHA-256 mismatch")
        return self


def verify_no_fault_capability_miss_eligibility_v1(
    *,
    repository_root: Path,
    private_root: Path,
    require_no_positive_attempts: bool,
) -> NoFaultCapabilityMissV1:
    """Rebuild the immutable false-positive Diagnosis and safe no-write facts."""

    repository = Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    prf = private / "pr-f"
    if private.is_relative_to(repository):
        raise ValueError("PR-F capability evidence must remain outside the repository")
    verify_private_tree_permissions(prf)
    _allowed_attempt_history(
        prf / "attempts", require_no_positive_attempts=require_no_positive_attempts
    )
    values: dict[str, dict[str, object]] = {}
    digests: dict[str, tuple[str, str]] = {}
    for label in _CAPABILITY_BOUND_JSON_V1:
        value, raw, semantic = _read_bound_json(prf, label)
        values[label] = value
        digests[label] = (raw, semantic)

    if values["readiness"] != values["readiness_copy"]:
        raise ValueError("No-Fault readiness pointer and immutable copy differ")
    readiness = LiveReadinessV2.model_validate_json(
        canonical_json_bytes(values["readiness"])
    )
    readiness_identity = ResolvedComposeIdentityV1.model_validate_json(
        canonical_json_bytes(values["readiness_compose_identity"])
    )
    retry_admission = RetryAdmissionV1.model_validate_json(
        canonical_json_bytes(values["retry_admission"])
    )
    retry_consumption = RetryConsumptionV1.model_validate_json(
        canonical_json_bytes(values["retry_consumption"])
    )
    review = IndependentRetryReviewV1.model_validate_json(
        canonical_json_bytes(values["independent_review"])
    )
    reconciliation = PostTerminalReconciliationV1.model_validate_json(
        canonical_json_bytes(values["original_reconciliation"])
    )
    claim = values["attempt_claim"]
    environment = LiveEnvironmentAdmissionV2.model_validate_json(
        canonical_json_bytes(values["environment_admission"])
    )
    attempt_identity = ResolvedComposeIdentityV1.model_validate_json(
        canonical_json_bytes(values["attempt_compose_identity"])
    )
    baseline = LiveBaselineEvidenceV21.model_validate_json(
        canonical_json_bytes(values["baseline_evidence"])
    )
    fault = LiveFaultImpactEvidenceV21.model_validate_json(
        canonical_json_bytes(values["fault_impact"])
    )
    result = DtaAgentRunResultV21.model_validate_json(
        canonical_json_bytes(values["agent_result"])
    )
    terminal = NoFaultCapabilityAttemptTerminalV1.model_validate_json(
        canonical_json_bytes(values["attempt_terminal"])
    )
    master = LiveMasterAuthorizationV21.model_validate_json(
        canonical_json_bytes(values["master_authorization"])
    )
    freeze = values["protocol_freeze"]

    verified_reconciliation, _quiescence = verify_post_terminal_reconciliation_v1(
        repository_root=repository, private_root=private
    )
    verified_consumption = verify_retry_consumption_v1(
        repository_root=repository,
        private_root=private,
        new_code_head=CAPABILITY_MISS_CODE_HEAD_V1,
    )
    if verified_reconciliation != reconciliation or verified_consumption != retry_consumption:
        raise ValueError("Amendment-2 reconciliation or consumption differs")
    if (
        retry_admission.new_code_head != CAPABILITY_MISS_CODE_HEAD_V1
        or retry_admission.admission_sha256
        != retry_consumption.retry_admission_sha256
        or retry_admission.reconciliation_sha256
        != reconciliation.reconciliation_sha256
        or retry_consumption.status != "CONSUMED"
        or retry_consumption.maximum_additional_campaigns != 0
        or review.code_head != CAPABILITY_MISS_CODE_HEAD_V1
        or review.must_fix_count != 0
        or review.should_fix_count != 0
        or review.claim_accuracy != "PASS"
    ):
        raise ValueError("Amendment-2 retry evidence differs from the capability miss")
    expected_claim = {
        "schema_version": "dta-v21.live-attempt-claim.v1",
        "attempt_id": CAPABILITY_MISS_ATTEMPT_ID_V1,
        "scenario": LiveScenarioV21.NO_FAULT.value,
        "ordinal": 1,
        "code_head": CAPABILITY_MISS_CODE_HEAD_V1,
        "master_authorization_sha256": master.authorization_sha256,
        "protocol_sha256": AD_PROTOCOL_SHA256_V1,
        "live_config_sha256": (
            "bbb17dd522c8190ad23ab40d7696ec981e5d4fad77dd9e66977228940046959a"
        ),
        "readiness_sha256": readiness.readiness_sha256,
    }
    if claim != expected_claim:
        raise ValueError("No-Fault attempt claim differs")
    if (
        readiness.code_head != CAPABILITY_MISS_CODE_HEAD_V1
        or readiness.readiness_attempt_id != "readiness-0001"
        or readiness.planner_identity_sha256 != PLANNER_IDENTITY_SHA256_V1
        or readiness.provider_model != PROVIDER_MODEL_V1
        or readiness.protocol_sha256 != AD_PROTOCOL_SHA256_V1
        or readiness.master_authorization_sha256 != master.authorization_sha256
        or readiness.raw_compose_sha256 != readiness_identity.raw_compose_sha256
        or readiness.execution_compose_sha256
        != readiness_identity.execution_compose_sha256
        or readiness.compose_identity_sha256 != readiness_identity.identity_sha256
        or environment.code_head != CAPABILITY_MISS_CODE_HEAD_V1
        or environment.attempt_id != CAPABILITY_MISS_ATTEMPT_ID_V1
        or environment.scenario is not LiveScenarioV21.NO_FAULT
        or environment.readiness_sha256 != readiness.readiness_sha256
        or environment.raw_compose_sha256 != attempt_identity.raw_compose_sha256
        or environment.execution_compose_sha256
        != attempt_identity.execution_compose_sha256
        or environment.compose_identity_sha256 != attempt_identity.identity_sha256
        or readiness_identity.execution_compose_sha256
        != attempt_identity.execution_compose_sha256
        or readiness_identity.raw_compose_sha256 == attempt_identity.raw_compose_sha256
    ):
        raise ValueError("No-Fault DEC-045 Compose or readiness binding differs")
    if (
        master.approver != "Minghong Sun"
        or master.planner_identity_sha256 != PLANNER_IDENTITY_SHA256_V1
        or master.provider_model != PROVIDER_MODEL_V1
        or master.protocol_sha256 != AD_PROTOCOL_SHA256_V1
        or LiveScenarioV21.NO_FAULT not in master.scenarios
        or freeze.get("record_sha256")
        != "33c82e1a03fc2ffb449e2df2c029634dca437df9226ca80f62252dda45962225"
        or freeze.get("pr_e_execution_id") != "53615cdd78b348b68496f64102c0b4de"
        or freeze.get("pr_e_held_out_seal_sha256")
        != "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
        or freeze.get("pr_e_claim")
        != "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
    ):
        raise ValueError("frozen master, Provider, or PR-E binding differs")
    if (
        baseline.run_id != environment.run_id
        or baseline.attempt_id != environment.attempt_id
        or baseline.scenario is not LiveScenarioV21.NO_FAULT
        or baseline.environment_admission_sha256
        != environment.environment_admission_sha256
        or fault.run_id != baseline.run_id
        or fault.attempt_id != baseline.attempt_id
        or fault.scenario is not LiveScenarioV21.NO_FAULT
        or fault.environment_admission_sha256
        != environment.environment_admission_sha256
        or fault.baseline_evidence_sha256 != baseline.evidence_sha256
        or fault.baseline_state_sha256 != baseline.baseline_state_sha256
        or fault.fault_impact_kind != "NO_FAULT"
        or fault.fault_operation_count != 0
        or fault.baseline_unchanged is not True
        or result.run_id != baseline.run_id
        or terminal.baseline_restored is not True
        or terminal.cleanup.baseline_restored is not True
        or terminal.cleanup.verdict != "CLEAN"
    ):
        raise ValueError("No-Fault baseline, fault, or cleanup evidence differs")

    attempt_root = prf / "attempts" / CAPABILITY_MISS_ATTEMPT_ID_V1
    observed_files = {
        item.name
        for item in attempt_root.iterdir()
        if item.is_file() and not item.is_symlink()
    }
    if (
        observed_files != _CAPABILITY_ATTEMPT_FILES_V1
        or any(
            (attempt_root / name).exists() or (attempt_root / name).is_symlink()
            for name in _CAPABILITY_FORBIDDEN_WRITE_FILES_V1
        )
    ):
        raise ValueError("No-Fault attempt contains write-authority artifacts")
    if (
        result.terminal is not AgentRunTerminalV21.COMPLETED
        or result.arm is not AgentArmV21.EVIDENCE_GUIDED_PLANNER
        or result.identity.identity_sha256 != PLANNER_IDENTITY_SHA256_V1
        or result.identity.model_id != PROVIDER_MODEL_V1
        or result.diagnosis is None
        or result.resolved_evidence is None
        or result.candidate_set is None
        or result.candidate_view is None
        or result.action_proposal is None
    ):
        raise ValueError("No-Fault Agent result is incomplete or identity-drifted")
    diagnosis = result.diagnosis
    proposal = result.action_proposal
    registry = load_default_runbook_registry(repository)
    rebuilt_candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=result.resolved_evidence,
        registry=registry,
        exact_target="checkout",
    )
    rebuilt_view = build_candidate_action_view_v21(rebuilt_candidates)
    diagnosis_sha256 = semantic_sha256(diagnosis.model_dump(mode="json"))
    candidate_view_sha256 = semantic_sha256(
        result.candidate_view.model_dump(mode="json")
    )
    if (
        diagnosis.terminal is not TerminalV21.COMPLETED
        or diagnosis.root_service != "checkout"
        or diagnosis.root_entity_ref != "service:checkout"
        or diagnosis.fault_domain is not FaultDomainV21.APPLICATION
        or diagnosis.mechanism is not FaultMechanismV21.UNKNOWN
        or rebuilt_candidates != result.candidate_set
        or rebuilt_view != result.candidate_view
        or bool(result.candidate_set.write_candidates)
        or proposal.disposition is not ActionDispositionV21.NO_ACTION
        or proposal.runbook_id is not None
        or proposal.target_service is not None
        or bool(proposal.parameters)
        or proposal.diagnosis_sha256 != diagnosis_sha256
        or proposal.resolved_evidence_sha256
        != result.resolved_evidence.resolved_evidence_sha256
        or proposal.candidate_set_sha256 != result.candidate_set.candidate_set_sha256
        or proposal.registry_sha256 != registry.registry_sha256
    ):
        raise ValueError("No-Fault false-positive Diagnosis or NO_ACTION differs")
    config = load_live_demo_config_v21(
        repository / "config/dta-v21/live/live-demo.v1.json"
    )
    try:
        verify_live_agent_result_v21(
            result=result,
            scenario=config.require_scenario(LiveScenarioV21.NO_FAULT),
            registry=registry,
            planner_identity_sha256=PLANNER_IDENTITY_SHA256_V1,
        )
    except ValueError as error:
        if str(error) != "no-fault Agent result is not an accepted non-write terminal":
            raise ValueError("No-Fault verifier rejected for an unexpected reason") from error
    else:
        raise ValueError("No-Fault verifier no longer preserves the Diagnosis miss")

    return NoFaultCapabilityMissV1.build(
        amendment_sha256=AMENDMENT3_RAW_SHA256_V1,
        decision_id=DECISION_ID_V1,
        classification="NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION",
        code_head=CAPABILITY_MISS_CODE_HEAD_V1,
        attempt_id=CAPABILITY_MISS_ATTEMPT_ID_V1,
        scenario=LiveScenarioV21.NO_FAULT,
        stage="AGENT",
        attempt_terminal="BLOCKED_DTA_V21_PRF_SAFETY",
        campaign_terminal="BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED",
        agent_terminal=result.terminal.value,
        diagnosis_root_service=diagnosis.root_service,
        diagnosis_root_entity_ref=diagnosis.root_entity_ref,
        diagnosis_fault_domain=diagnosis.fault_domain.value,
        diagnosis_mechanism=diagnosis.mechanism.value,
        action_disposition=proposal.disposition.value,
        capability_passed=False,
        diagnosis_correct=False,
        no_write_safety_passed=True,
        fault_injected=False,
        write_admitted=False,
        forward_action_observed=False,
        baseline_restored=True,
        cleanup_clean=True,
        non_owned_change_observed=False,
        fault_operation_count=0,
        forward_step_count=0,
        unsafe_proposal_attempts=0,
        arbitrary_shell_attempts=0,
        planner_identity_sha256=result.identity.identity_sha256,
        provider_model=result.identity.model_id,
        held_out_execution_id="53615cdd78b348b68496f64102c0b4de",
        held_out_seal_sha256=(
            "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
        ),
        held_out_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        attempt_claim_raw_sha256=digests["attempt_claim"][0],
        attempt_claim_semantic_sha256=digests["attempt_claim"][1],
        readiness_raw_sha256=digests["readiness"][0],
        readiness_semantic_sha256=digests["readiness"][1],
        readiness_sha256=readiness.readiness_sha256,
        readiness_compose_identity_sha256=readiness_identity.identity_sha256,
        environment_admission_raw_sha256=digests["environment_admission"][0],
        environment_admission_semantic_sha256=digests["environment_admission"][1],
        environment_admission_sha256=environment.environment_admission_sha256,
        attempt_compose_identity_sha256=attempt_identity.identity_sha256,
        baseline_evidence_raw_sha256=digests["baseline_evidence"][0],
        baseline_evidence_semantic_sha256=digests["baseline_evidence"][1],
        baseline_evidence_sha256=baseline.evidence_sha256,
        fault_impact_raw_sha256=digests["fault_impact"][0],
        fault_impact_semantic_sha256=digests["fault_impact"][1],
        fault_impact_sha256=fault.evidence_sha256,
        agent_result_raw_sha256=digests["agent_result"][0],
        agent_result_semantic_sha256=digests["agent_result"][1],
        agent_result_sha256=result.result_sha256,
        diagnosis_sha256=diagnosis_sha256,
        candidate_set_sha256=result.candidate_set.candidate_set_sha256,
        candidate_view_sha256=candidate_view_sha256,
        action_proposal_sha256=proposal.proposal_sha256,
        attempt_terminal_raw_sha256=digests["attempt_terminal"][0],
        attempt_terminal_semantic_sha256=digests["attempt_terminal"][1],
        parent_retry_admission_sha256=retry_admission.admission_sha256,
        parent_retry_consumption_sha256=retry_consumption.consumption_sha256,
        original_blocker_reconciliation_sha256=reconciliation.reconciliation_sha256,
        master_authorization_sha256=master.authorization_sha256,
        protocol_freeze_sha256=str(freeze["record_sha256"]),
    )


def write_no_fault_capability_miss_v1(
    *, repository_root: Path, private_root: Path
) -> NoFaultCapabilityMissV1:
    record = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=repository_root,
        private_root=private_root,
        require_no_positive_attempts=True,
    )
    path = (
        Path(private_root)
        / "pr-f/capability-closeout"
        / CAPABILITY_MISS_ATTEMPT_ID_V1
        / "no-fault-capability-miss.v1.json"
    )
    write_private_json(path, record, create_once=True)
    verify_private_tree_permissions(Path(private_root) / "pr-f")
    return record


def write_positive_continuation_standing_authorization_v1(
    *, private_root: Path
) -> PositiveContinuationStandingAuthorizationV1:
    record = PositiveContinuationStandingAuthorizationV1.build()
    path = (
        Path(private_root)
        / "pr-f/capability-closeout/standing-authorization.v1.json"
    )
    write_private_json(path, record, create_once=True)
    return record


class PositiveContinuationReviewV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-positive-continuation-review.v1"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_at: datetime
    must_fix_count: Literal[0]
    should_fix_count: Literal[0]
    claim_accuracy: Literal["PASS"]
    review_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": "dta-v21.pr-f-positive-continuation-review.v1",
            **values,
        }
        return cls.model_validate({**payload, "review_sha256": _semantic(payload)})

    @model_validator(mode="after")
    def require_review(self) -> Self:
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() != timedelta(0):
            raise ValueError("positive-continuation review timestamp requires UTC")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("positive-continuation review SHA-256 mismatch")
        return self


class PositiveContinuationStandingAuthorizationV1(DtaModelV21):
    schema_version: Literal[
        "dta-v21.pr-f-positive-continuation-standing-authorization.v1"
    ]
    approver: Literal["Minghong Sun"]
    authorization_source: Literal[
        "USER_EXPLICIT_DTA_V21_PRF_CAPABILITY_CLOSEOUT_AND_POSITIVE_CONTINUATION"
    ]
    command_execution: Literal["CODEX_DELEGATED_EXECUTION"]
    authorization_mode: Literal[
        "DTA_V21_PRF_CAPABILITY_CLOSEOUT_STANDING_AUTHORIZATION"
    ]
    codex_autonomous_self_approval: Literal[False]
    additional_human_confirmation_required: Literal[False]
    no_fault_rerun_authorized: Literal[False]
    maximum_positive_continuations: Literal[1]
    positive_scenarios: tuple[
        LiveScenarioV21, LiveScenarioV21, LiveScenarioV21
    ]
    amendment_sha256: Literal[
        "24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a"
    ]
    decision_id: Literal["DEC-046"]
    authorization_sha256: Sha256V21

    @classmethod
    def build(cls) -> Self:
        payload: dict[str, object] = {
            "schema_version": (
                "dta-v21.pr-f-positive-continuation-standing-authorization.v1"
            ),
            "approver": "Minghong Sun",
            "authorization_source": (
                "USER_EXPLICIT_DTA_V21_PRF_CAPABILITY_CLOSEOUT_AND_"
                "POSITIVE_CONTINUATION"
            ),
            "command_execution": "CODEX_DELEGATED_EXECUTION",
            "authorization_mode": (
                "DTA_V21_PRF_CAPABILITY_CLOSEOUT_STANDING_AUTHORIZATION"
            ),
            "codex_autonomous_self_approval": False,
            "additional_human_confirmation_required": False,
            "no_fault_rerun_authorized": False,
            "maximum_positive_continuations": 1,
            "positive_scenarios": POSITIVE_CONTINUATION_ORDER_V1,
            "amendment_sha256": AMENDMENT3_RAW_SHA256_V1,
            "decision_id": DECISION_ID_V1,
        }
        return cls.model_validate(
            {**payload, "authorization_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_authorization(self) -> Self:
        if (
            self.positive_scenarios != POSITIVE_CONTINUATION_ORDER_V1
            or LiveScenarioV21.NO_FAULT in self.positive_scenarios
        ):
            raise ValueError("standing authorization scope differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected:
            raise ValueError("standing authorization SHA-256 mismatch")
        return self


class PositiveContinuationQuiescenceV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-positive-continuation-quiescence.v1"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    observed_at: datetime
    docker_boundary: Literal["LOCAL_UNIX_DOCKER"]
    owned_container_count: Literal[0]
    owned_network_count: Literal[0]
    owned_volume_count: Literal[0]
    required_ports_available: Literal[True]
    execution_lease_held: Literal[False]
    private_permissions_verified: Literal[True]
    source_worktree_clean: Literal[True]
    frozen_bindings_verified: Literal[True]
    capability_miss_sha256: Sha256V21
    parent_retry_consumption_sha256: Sha256V21
    observation_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": (
                "dta-v21.pr-f-positive-continuation-quiescence.v1"
            ),
            **values,
        }
        return cls.model_validate(
            {**payload, "observation_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_quiescence(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("positive-continuation quiescence timestamp requires UTC")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"observation_sha256"})
        )
        if self.observation_sha256 != expected:
            raise ValueError("positive-continuation quiescence SHA-256 mismatch")
        return self


class PositiveContinuationReadinessV3(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-readiness.v3"]
    terminal: Literal["DTA_V21_PR_F_POSITIVE_CONTINUATION_READY"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_v2_readiness_sha256: Sha256V21
    current_quiescence_sha256: Sha256V21
    capability_miss_sha256: Sha256V21
    parent_retry_consumption_sha256: Sha256V21
    amendment_sha256: Literal[
        "24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a"
    ]
    decision_id: Literal["DEC-046"]
    exact_head_ci_run_id: StrictInt = Field(ge=1)
    exact_head_ci_run_url: str = Field(pattern=r"^https://github\.com/.+")
    master_authorization_sha256: Sha256V21
    standing_authorization_sha256: Sha256V21
    planner_identity_sha256: Literal[
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    ]
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    ad_protocol_sha256: Literal[
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    ]
    readiness_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {"schema_version": "dta-v21.pr-f-readiness.v3", **values}
        return cls.model_validate(
            {**payload, "readiness_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_readiness(self) -> Self:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"readiness_sha256"})
        )
        if self.readiness_sha256 != expected:
            raise ValueError("positive-continuation readiness SHA-256 mismatch")
        return self


class PositiveContinuationAdmissionV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-positive-continuation-admission.v1"]
    verdict: Literal["ALLOW_ONE_POSITIVE_CONTINUATION"]
    resume_mode: Literal["CONTINUE_FROM_SLOT_2_AFTER_SAFE_NO_WRITE_DIAGNOSIS_MISS"]
    no_fault_retry_authorized: Literal[False]
    no_fault_attempt_immutable: Literal[True]
    no_fault_diagnosis_passed: Literal[False]
    no_fault_no_write_safety_passed: Literal[True]
    continuation_scenarios: tuple[
        LiveScenarioV21, LiveScenarioV21, LiveScenarioV21
    ]
    maximum_new_positive_continuations: Literal[1]
    maximum_continuations_after_consumption: Literal[0]
    parent_retry_consumption_status: Literal["CONSUMED"]
    parent_retry_consumption_sha256: Sha256V21
    capability_miss_record_sha256: Sha256V21
    original_blocker_reconciliation_sha256: Sha256V21
    new_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_main_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    amendment_sha256: Literal[
        "24cc236c1892c9992b6d36da377608c34fb22c2bc270f99349e5e8a4e0a0498a"
    ]
    decision_id: Literal["DEC-046"]
    exact_head_ci_run_id: StrictInt = Field(ge=1)
    exact_head_ci_run_url: str = Field(pattern=r"^https://github\.com/.+")
    independent_review_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    independent_review_sha256: Sha256V21
    independent_review_must_fix_count: Literal[0]
    independent_review_should_fix_count: Literal[0]
    independent_review_claim_accuracy: Literal["PASS"]
    current_quiescence_sha256: Sha256V21
    v3_readiness_sha256: Sha256V21
    master_authorization_sha256: Sha256V21
    standing_authorization_sha256: Sha256V21
    planner_identity_sha256: Literal[
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    ]
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    ad_protocol_sha256: Literal[
        "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
    ]
    admission_sha256: Sha256V21

    @model_validator(mode="after")
    def require_admission(self) -> Self:
        if (
            self.new_code_head == CAPABILITY_MISS_CODE_HEAD_V1
            or self.independent_review_head != self.new_code_head
            or self.continuation_scenarios != POSITIVE_CONTINUATION_ORDER_V1
            or LiveScenarioV21.NO_FAULT in self.continuation_scenarios
        ):
            raise ValueError("positive-continuation exact scope differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("positive-continuation admission SHA-256 mismatch")
        return self


def build_positive_continuation_readiness_v3(
    *,
    base_readiness: LiveReadinessV2,
    quiescence: PositiveContinuationQuiescenceV1,
    capability: NoFaultCapabilityMissV1,
    standing_authorization: PositiveContinuationStandingAuthorizationV1,
) -> PositiveContinuationReadinessV3:
    if (
        base_readiness.code_head != quiescence.code_head
        or quiescence.capability_miss_sha256
        != capability.classification_sha256
        or quiescence.parent_retry_consumption_sha256
        != capability.parent_retry_consumption_sha256
        or base_readiness.planner_identity_sha256
        != PLANNER_IDENTITY_SHA256_V1
        or base_readiness.provider_model != PROVIDER_MODEL_V1
        or base_readiness.protocol_sha256 != AD_PROTOCOL_SHA256_V1
    ):
        raise ValueError("positive-continuation readiness prerequisite differs")
    return PositiveContinuationReadinessV3.build(
        terminal="DTA_V21_PR_F_POSITIVE_CONTINUATION_READY",
        code_head=base_readiness.code_head,
        base_v2_readiness_sha256=base_readiness.readiness_sha256,
        current_quiescence_sha256=quiescence.observation_sha256,
        capability_miss_sha256=capability.classification_sha256,
        parent_retry_consumption_sha256=(
            capability.parent_retry_consumption_sha256
        ),
        amendment_sha256=AMENDMENT3_RAW_SHA256_V1,
        decision_id=DECISION_ID_V1,
        exact_head_ci_run_id=base_readiness.exact_head_ci_run_id,
        exact_head_ci_run_url=base_readiness.exact_head_ci_run_url,
        master_authorization_sha256=(
            base_readiness.master_authorization_sha256
        ),
        standing_authorization_sha256=(
            standing_authorization.authorization_sha256
        ),
        planner_identity_sha256=base_readiness.planner_identity_sha256,
        provider_model=base_readiness.provider_model,
        ad_protocol_sha256=base_readiness.protocol_sha256,
    )


def build_positive_continuation_admission_v1(
    *,
    new_code_head: str,
    base_main_head: str,
    capability: NoFaultCapabilityMissV1,
    parent_retry_consumption_sha256: str,
    original_blocker_reconciliation_sha256: str,
    readiness: PositiveContinuationReadinessV3,
    quiescence: PositiveContinuationQuiescenceV1,
    review: PositiveContinuationReviewV1,
) -> PositiveContinuationAdmissionV1:
    if (
        readiness.code_head != new_code_head
        or quiescence.code_head != new_code_head
        or review.code_head != new_code_head
        or readiness.capability_miss_sha256 != capability.classification_sha256
        or quiescence.capability_miss_sha256 != capability.classification_sha256
        or readiness.current_quiescence_sha256 != quiescence.observation_sha256
        or readiness.parent_retry_consumption_sha256
        != parent_retry_consumption_sha256
        or quiescence.parent_retry_consumption_sha256
        != parent_retry_consumption_sha256
    ):
        raise ValueError("positive-continuation admission prerequisite differs")
    payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f-positive-continuation-admission.v1",
        "verdict": "ALLOW_ONE_POSITIVE_CONTINUATION",
        "resume_mode": (
            "CONTINUE_FROM_SLOT_2_AFTER_SAFE_NO_WRITE_DIAGNOSIS_MISS"
        ),
        "no_fault_retry_authorized": False,
        "no_fault_attempt_immutable": True,
        "no_fault_diagnosis_passed": False,
        "no_fault_no_write_safety_passed": True,
        "continuation_scenarios": POSITIVE_CONTINUATION_ORDER_V1,
        "maximum_new_positive_continuations": 1,
        "maximum_continuations_after_consumption": 0,
        "parent_retry_consumption_status": "CONSUMED",
        "parent_retry_consumption_sha256": parent_retry_consumption_sha256,
        "capability_miss_record_sha256": capability.classification_sha256,
        "original_blocker_reconciliation_sha256": (
            original_blocker_reconciliation_sha256
        ),
        "new_code_head": new_code_head,
        "base_main_head": base_main_head,
        "amendment_sha256": AMENDMENT3_RAW_SHA256_V1,
        "decision_id": DECISION_ID_V1,
        "exact_head_ci_run_id": readiness.exact_head_ci_run_id,
        "exact_head_ci_run_url": readiness.exact_head_ci_run_url,
        "independent_review_head": review.code_head,
        "independent_review_sha256": review.review_sha256,
        "independent_review_must_fix_count": review.must_fix_count,
        "independent_review_should_fix_count": review.should_fix_count,
        "independent_review_claim_accuracy": review.claim_accuracy,
        "current_quiescence_sha256": quiescence.observation_sha256,
        "v3_readiness_sha256": readiness.readiness_sha256,
        "master_authorization_sha256": readiness.master_authorization_sha256,
        "standing_authorization_sha256": (
            readiness.standing_authorization_sha256
        ),
        "planner_identity_sha256": readiness.planner_identity_sha256,
        "provider_model": readiness.provider_model,
        "ad_protocol_sha256": readiness.ad_protocol_sha256,
    }
    return PositiveContinuationAdmissionV1.model_validate(
        {**payload, "admission_sha256": _semantic(payload)}
    )


class PositiveContinuationConsumptionV1(DtaModelV21):
    schema_version: Literal["dta-v21.pr-f-positive-continuation-consumption.v1"]
    status: Literal["CONSUMED"]
    admission_sha256: Sha256V21
    consumed_by_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    consumed_for_scenarios: tuple[
        LiveScenarioV21, LiveScenarioV21, LiveScenarioV21
    ]
    first_scenario: Literal[LiveScenarioV21.AD_CPU_SATURATION]
    no_fault_rerun: Literal[False]
    maximum_additional_positive_continuations: Literal[0]
    consumed_at: datetime
    consumption_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": (
                "dta-v21.pr-f-positive-continuation-consumption.v1"
            ),
            **values,
        }
        return cls.model_validate(
            {**payload, "consumption_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_consumption(self) -> Self:
        if (
            self.consumed_at.tzinfo is None
            or self.consumed_at.utcoffset() != timedelta(0)
            or self.consumed_for_scenarios != POSITIVE_CONTINUATION_ORDER_V1
            or LiveScenarioV21.NO_FAULT in self.consumed_for_scenarios
        ):
            raise ValueError("positive-continuation consumption scope differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"consumption_sha256"})
        )
        if self.consumption_sha256 != expected:
            raise ValueError("positive-continuation consumption SHA-256 mismatch")
        return self


class LivePositiveContinuationClosureV1(DtaModelV21):
    schema_version: Literal["dta-v21.live-positive-continuation-closure.v1"]
    terminal: Literal[
        "DTA_V21_PR_F_POSITIVE_PORTFOLIO_PASS_WITH_NO_FAULT_DIAGNOSIS_MISS"
    ]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    admission_sha256: Sha256V21
    consumption_sha256: Sha256V21
    v3_readiness_sha256: Sha256V21
    capability_miss_sha256: Sha256V21
    planner_identity_sha256: Literal[
        "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
    ]
    attempts: tuple[
        LiveAttemptClosureV21,
        LiveAttemptClosureV21,
        LiveAttemptClosureV21,
    ]
    positive_continuation_attempt_count: Literal[3]
    positive_continuation_attempts_passed: Literal[3]
    all_baselines_restored: Literal[True]
    all_cleanup_clean: Literal[True]
    non_owned_changes: Literal[0]
    unsafe_proposal_attempts: Literal[0]
    arbitrary_shell_attempts: Literal[0]
    closure_sha256: Sha256V21

    @classmethod
    def build(cls, **values: object) -> Self:
        payload = {
            "schema_version": (
                "dta-v21.live-positive-continuation-closure.v1"
            ),
            **values,
        }
        return cls.model_validate(
            {**payload, "closure_sha256": _semantic(payload)}
        )

    @model_validator(mode="after")
    def require_positive_only_closure(self) -> Self:
        if tuple(item.scenario for item in self.attempts) != (
            POSITIVE_CONTINUATION_ORDER_V1
        ):
            raise ValueError("positive-continuation attempt order differs")
        if any(
            item.fault_operation_count != 1
            or item.forward_step_count != 1
            or item.baseline_state_digest_restored is not True
            or item.cleanup_verdict != "CLEAN"
            or item.non_owned_changes != 0
            for item in self.attempts
        ):
            raise ValueError("positive-continuation attempt gate differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"closure_sha256"})
        )
        if self.closure_sha256 != expected:
            raise ValueError("positive-continuation closure SHA-256 mismatch")
        return self


def write_positive_continuation_review_v1(
    *, private_root: Path, review: PositiveContinuationReviewV1
) -> None:
    path = (
        Path(private_root)
        / "pr-f/positive-continuation-reviews"
        / review.code_head
        / "review.v1.json"
    )
    write_private_json(path, review, create_once=True)


def write_positive_continuation_quiescence_v1(
    *, private_root: Path, quiescence: PositiveContinuationQuiescenceV1
) -> None:
    path = (
        Path(private_root)
        / "pr-f/positive-continuation-admissions"
        / quiescence.code_head
        / "quiescence.v1.json"
    )
    write_private_json(path, quiescence, create_once=True)


def write_positive_continuation_readiness_v3(
    *, private_root: Path, readiness: PositiveContinuationReadinessV3
) -> None:
    path = (
        Path(private_root)
        / "pr-f/positive-continuation-admissions"
        / readiness.code_head
        / "readiness.v3.json"
    )
    write_private_json(path, readiness, create_once=True)


def write_positive_continuation_admission_v1(
    *, private_root: Path, admission: PositiveContinuationAdmissionV1
) -> None:
    path = (
        Path(private_root)
        / "pr-f/positive-continuation-admissions"
        / admission.new_code_head
        / "admission.v1.json"
    )
    write_private_json(path, admission, create_once=True)


def _write_exclusive_private_json(path: Path, value: object) -> None:
    ensure_private_directory(path.parent)
    payload = canonical_json_bytes(value)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise RuntimeError(
            "BLOCKED_DTA_V21_PRF_POSITIVE_CONTINUATION_EXHAUSTED"
        ) from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _read_positive_admission(path: Path) -> PositiveContinuationAdmissionV1:
    if path.is_symlink() or not path.is_file():
        raise ValueError("positive-continuation admission is missing or unsafe")
    return PositiveContinuationAdmissionV1.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def verify_positive_continuation_admission_v1(
    *, repository_root: Path, private_root: Path, new_code_head: str
) -> PositiveContinuationAdmissionV1:
    root = Path(repository_root).resolve(strict=True)
    private = Path(private_root).resolve(strict=True)
    prf = private / "pr-f"
    verify_private_tree_permissions(prf)
    head = _COMMAND_RUNNER.run(
        ("git", "rev-parse", "HEAD"), cwd=root, timeout_seconds=30
    ).stdout.strip()
    status = _COMMAND_RUNNER.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=root,
        timeout_seconds=30,
    ).stdout.strip()
    if head != new_code_head or status:
        raise ValueError("positive-continuation admission requires exact clean HEAD")
    _COMMAND_RUNNER.run(
        (
            "git",
            "merge-base",
            "--is-ancestor",
            CAPABILITY_MISS_CODE_HEAD_V1,
            new_code_head,
        ),
        cwd=root,
        timeout_seconds=30,
    )
    base_main_head = _COMMAND_RUNNER.run(
        ("git", "rev-parse", "origin/main"), cwd=root, timeout_seconds=30
    ).stdout.strip()
    capability_path = (
        prf
        / "capability-closeout"
        / CAPABILITY_MISS_ATTEMPT_ID_V1
        / "no-fault-capability-miss.v1.json"
    )
    capability = NoFaultCapabilityMissV1.model_validate_json(
        capability_path.read_text(encoding="utf-8")
    )
    rebuilt_capability = verify_no_fault_capability_miss_eligibility_v1(
        repository_root=root,
        private_root=private,
        require_no_positive_attempts=False,
    )
    if capability != rebuilt_capability:
        raise ValueError("positive-continuation capability record differs")
    standing = PositiveContinuationStandingAuthorizationV1.model_validate_json(
        (
            prf / "capability-closeout/standing-authorization.v1.json"
        ).read_text(encoding="utf-8")
    )
    base_readiness = LiveReadinessV2.model_validate_json(
        (prf / "readiness" / new_code_head / "readiness.json").read_text(
            encoding="utf-8"
        )
    )
    admission_root = prf / "positive-continuation-admissions" / new_code_head
    quiescence = PositiveContinuationQuiescenceV1.model_validate_json(
        (admission_root / "quiescence.v1.json").read_text(encoding="utf-8")
    )
    readiness = PositiveContinuationReadinessV3.model_validate_json(
        (admission_root / "readiness.v3.json").read_text(encoding="utf-8")
    )
    review = PositiveContinuationReviewV1.model_validate_json(
        (
            prf
            / "positive-continuation-reviews"
            / new_code_head
            / "review.v1.json"
        ).read_text(encoding="utf-8")
    )
    master = LiveMasterAuthorizationV21.model_validate_json(
        (prf / "master-authorization.json").read_text(encoding="utf-8")
    )
    parent_consumption = verify_retry_consumption_v1(
        repository_root=root,
        private_root=private,
        new_code_head=CAPABILITY_MISS_CODE_HEAD_V1,
    )
    reconciliation, _ = verify_post_terminal_reconciliation_v1(
        repository_root=root, private_root=private
    )
    expected_readiness = build_positive_continuation_readiness_v3(
        base_readiness=base_readiness,
        quiescence=quiescence,
        capability=capability,
        standing_authorization=standing,
    )
    expected = build_positive_continuation_admission_v1(
        new_code_head=new_code_head,
        base_main_head=base_main_head,
        capability=capability,
        parent_retry_consumption_sha256=parent_consumption.consumption_sha256,
        original_blocker_reconciliation_sha256=(
            reconciliation.reconciliation_sha256
        ),
        readiness=readiness,
        quiescence=quiescence,
        review=review,
    )
    path = admission_root / "admission.v1.json"
    value = _read_positive_admission(path)
    if (
        readiness != expected_readiness
        or value != expected
        or value.master_authorization_sha256 != master.authorization_sha256
        or value.standing_authorization_sha256
        != standing.authorization_sha256
        or value.parent_retry_consumption_sha256
        != parent_consumption.consumption_sha256
        or value.original_blocker_reconciliation_sha256
        != reconciliation.reconciliation_sha256
    ):
        raise ValueError("stored positive-continuation admission differs")
    return value


def consume_positive_continuation_v1(
    *,
    repository_root: Path,
    private_root: Path,
    new_code_head: str,
    consumed_at: datetime,
) -> PositiveContinuationConsumptionV1:
    admission = verify_positive_continuation_admission_v1(
        repository_root=repository_root,
        private_root=private_root,
        new_code_head=new_code_head,
    )
    attempts_root = Path(private_root) / "pr-f/attempts"
    expected = {ORIGINAL_BLOCKED_ATTEMPT_ID_V1, CAPABILITY_MISS_ATTEMPT_ID_V1}
    if (
        not attempts_root.is_dir()
        or any(item.is_symlink() or not item.is_dir() for item in attempts_root.iterdir())
        or {item.name for item in attempts_root.iterdir()} != expected
    ):
        raise RuntimeError(
            "BLOCKED_DTA_V21_PRF_POSITIVE_CONTINUATION_EXHAUSTED"
        )
    consumption = PositiveContinuationConsumptionV1.build(
        status="CONSUMED",
        admission_sha256=admission.admission_sha256,
        consumed_by_code_head=new_code_head,
        consumed_for_scenarios=POSITIVE_CONTINUATION_ORDER_V1,
        first_scenario=LiveScenarioV21.AD_CPU_SATURATION,
        no_fault_rerun=False,
        maximum_additional_positive_continuations=0,
        consumed_at=consumed_at,
    )
    path = (
        Path(private_root)
        / "pr-f/positive-continuation-consumptions"
        / POSITIVE_CONTINUATION_CONSUMPTION_FILENAME_V1
    )
    _write_exclusive_private_json(path, consumption)
    return consumption


def verify_positive_continuation_consumption_v1(
    *, repository_root: Path, private_root: Path, new_code_head: str
) -> PositiveContinuationConsumptionV1:
    admission = verify_positive_continuation_admission_v1(
        repository_root=repository_root,
        private_root=private_root,
        new_code_head=new_code_head,
    )
    path = (
        Path(private_root)
        / "pr-f/positive-continuation-consumptions"
        / POSITIVE_CONTINUATION_CONSUMPTION_FILENAME_V1
    )
    if path.is_symlink() or not path.is_file():
        raise ValueError("positive-continuation consumption is missing or unsafe")
    value = PositiveContinuationConsumptionV1.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if (
        value.admission_sha256 != admission.admission_sha256
        or value.consumed_by_code_head != new_code_head
    ):
        raise ValueError("positive-continuation consumption binding differs")
    return value


__all__ = (
    "AD_PROTOCOL_SHA256_V1",
    "AMENDMENT3_RAW_SHA256_V1",
    "CAPABILITY_MISS_ATTEMPT_ID_V1",
    "CAPABILITY_MISS_CODE_HEAD_V1",
    "DECISION_ID_V1",
    "LivePositiveContinuationClosureV1",
    "NoFaultCapabilityMissV1",
    "ORIGINAL_BLOCKED_ATTEMPT_ID_V1",
    "PLANNER_IDENTITY_SHA256_V1",
    "POSITIVE_CONTINUATION_CONSUMPTION_FILENAME_V1",
    "POSITIVE_CONTINUATION_ORDER_V1",
    "PROVIDER_MODEL_V1",
    "PositiveContinuationAdmissionV1",
    "PositiveContinuationConsumptionV1",
    "PositiveContinuationQuiescenceV1",
    "PositiveContinuationReadinessV3",
    "PositiveContinuationReviewV1",
    "PositiveContinuationStandingAuthorizationV1",
    "build_positive_continuation_admission_v1",
    "build_positive_continuation_readiness_v3",
    "consume_positive_continuation_v1",
    "verify_no_fault_capability_miss_eligibility_v1",
    "verify_positive_continuation_admission_v1",
    "verify_positive_continuation_consumption_v1",
    "write_no_fault_capability_miss_v1",
    "write_positive_continuation_admission_v1",
    "write_positive_continuation_quiescence_v1",
    "write_positive_continuation_readiness_v3",
    "write_positive_continuation_review_v1",
    "write_positive_continuation_standing_authorization_v1",
)
