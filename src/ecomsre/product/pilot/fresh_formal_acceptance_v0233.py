"""Fresh formal campaign and acceptance contracts for Product v0.2.3.3."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.healthy_traffic_v0232 import HealthyTrafficProfileV0232
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NOFAULT_CAPABILITY_LIMITED_V0232,
    NOFAULT_FULLY_SUPPORTED_V0232,
    NOFAULT_NOT_SUPPORTED_V0232,
)
from ecomsre.product.pilot.serialization_v0233 import semantic_json_sha256_v0233


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GOAL_VERSION = "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
_STARTING_MAIN = "6e07964e5595b4138decf0276189c76c3e278d87"
_HANDOFF_SHA256 = "72d272951412d696d50fb6ee44c96bbc4a1a6e5ace63d574b0636297b848847f"
_TRAFFIC_CONTRACT_SHA256 = (
    "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b"
)
_ACTIVE_PROFILE_SHA256 = (
    "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
)
_ACTIVE_BASELINE_ID = "base-b25440a36089a8f0e6b9f1dc"
_ACTIVE_BASELINE_SHA256 = (
    "6d3d2d7a4854d1cfc2477746e7d0c940ed8a08644ebc69b7b91066eabe45ae64"
)
_RUNTIME_CONTINUITY_DESCRIPTOR_SHA256 = (
    "b103990c21d1804177a5d15900252259481e520dc2c5380547db0754c76c2e65"
)
_FLAGD_BIND_DESCRIPTOR_SHA256 = (
    "ecd2bffefe79fb7cb356e356a10278bd276d849ad5dd1e220cd0d3e77c0729e9"
)
_STAGE_JOURNAL_CONTRACT_SHA256 = (
    "270b28c0b26544ddc551bd476ddad8551f21c06bfad3e3c490b04e2ff010a0f3"
)
_PRIVATE_FAILURE_CONTRACT_SHA256 = (
    "4aebb50acef0e21a964cdb812cd4d2b6aa8983d3ff106e3d05a8a4df7aa61812"
)
_NOFAULT_SCORER_SOURCE_SHA256 = (
    "059dc820d106059a0fa897014d67c17dd119029104040d2607edfc3c183c5034"
)

FORMAL_CONTRACT_PREFLIGHT_PASS_V0233 = (
    "ECOMSRE_PRODUCT_V0233_FORMAL_CONTRACT_PREFLIGHT_PASS"
)
NOFAULT_FULLY_SUPPORTED_V0233 = (
    "ECOMSRE_PRODUCT_V0233_NOFAULT_FULLY_SUPPORTED"
)
NOFAULT_CAPABILITY_LIMITED_V0233 = (
    "ECOMSRE_PRODUCT_V0233_NOFAULT_CAPABILITY_LIMITED"
)
NOFAULT_NOT_SUPPORTED_V0233 = "ECOMSRE_PRODUCT_V0233_NOFAULT_NOT_SUPPORTED"

_V0233_TERMINAL_BY_V0232 = {
    NOFAULT_FULLY_SUPPORTED_V0232: NOFAULT_FULLY_SUPPORTED_V0233,
    NOFAULT_CAPABILITY_LIMITED_V0232: NOFAULT_CAPABILITY_LIMITED_V0233,
    NOFAULT_NOT_SUPPORTED_V0232: NOFAULT_NOT_SUPPORTED_V0233,
}


class FreshFormalCampaignV0233(ProductModelV1):
    """One-shot campaign envelope; it grants no action authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.fresh-formal-campaign.v0233"] = (
        "ecomsre.product.fresh-formal-campaign.v0233"
    )
    goal_version: Literal[
        "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
    ] = "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
    campaign_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    starting_main: str = Field(pattern=r"^[0-9a-f]{40}$")
    handoff_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    preflight_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_continuity_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    flagd_bind_descriptor_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage_journal_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    private_failure_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    nofault_scorer_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_execution_limit: Literal[1] = 1
    incident_limit: Literal[1] = 1
    diagnosis_limit: Literal[1] = 1
    fault_attempt_limit: Literal[0] = 0
    knowledge_loop_limit: Literal[0] = 0
    action_authority: Literal["NONE"] = "NONE"
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_campaign_binding(self) -> FreshFormalCampaignV0233:
        if (
            self.starting_main != _STARTING_MAIN
            or self.handoff_sha256 != _HANDOFF_SHA256
            or self.traffic_contract_sha256 != _TRAFFIC_CONTRACT_SHA256
            or self.active_profile_sha256 != _ACTIVE_PROFILE_SHA256
            or self.active_baseline_id != _ACTIVE_BASELINE_ID
            or self.active_baseline_sha256 != _ACTIVE_BASELINE_SHA256
            or self.runtime_continuity_descriptor_sha256
            != _RUNTIME_CONTINUITY_DESCRIPTOR_SHA256
            or self.flagd_bind_descriptor_sha256
            != _FLAGD_BIND_DESCRIPTOR_SHA256
            or self.stage_journal_contract_sha256
            != _STAGE_JOURNAL_CONTRACT_SHA256
            or self.private_failure_contract_sha256
            != _PRIVATE_FAILURE_CONTRACT_SHA256
            or self.nofault_scorer_source_sha256
            != _NOFAULT_SCORER_SOURCE_SHA256
        ):
            raise ValueError("Product v0.2.3.3 campaign binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"campaign_sha256"})
        )
        if self.campaign_sha256 != expected:
            raise ValueError("Product v0.2.3.3 campaign digest differs")
        return self


class FreshTrafficProfileV0233(ProductModelV1):
    """A v0.2.3.3 profile with its PASS retry rule inside the seal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.healthy-traffic-profile.v0233"] = (
        "ecomsre.product.healthy-traffic-profile.v0233"
    )
    role: Literal["PREFLIGHT", "FORMAL"]
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    transactions: int = Field(ge=1, le=30)
    requests_per_second: float = Field(gt=0, le=1, allow_inf_nan=False)
    request_seed: int = Field(ge=0)
    maximum_failures: Literal[0] = 0
    transport_retries_allowed_for_pass: Literal[0] = 0
    stabilization_seconds: int = Field(ge=0, le=60)
    minimum_full_episode_duration_seconds: int = Field(ge=0, le=900)
    queue_fault_flag: Literal[0] = 0
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_profile_and_seal(self) -> FreshTrafficProfileV0233:
        expected_values = (
            ("product-v0233-preflight", 10, 1.0, 23083301, 30, 0)
            if self.role == "PREFLIGHT"
            else ("product-v0233-formal", 30, 1.0, 23083302, 0, 300)
        )
        observed_values = (
            self.profile_id,
            self.transactions,
            self.requests_per_second,
            self.request_seed,
            self.stabilization_seconds,
            self.minimum_full_episode_duration_seconds,
        )
        if observed_values != expected_values:
            raise ValueError("Product v0.2.3.3 traffic profile differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("Product v0.2.3.3 traffic profile digest differs")
        return self

    def engine_profile_v0232(self) -> HealthyTrafficProfileV0232:
        """Project the sealed profile into the existing frozen traffic engine."""

        return HealthyTrafficProfileV0232.build(
            profile_id=self.profile_id,
            transactions=self.transactions,
            requests_per_second=self.requests_per_second,
            request_seed=self.request_seed,
            maximum_failures=self.maximum_failures,
            stabilization_seconds=self.stabilization_seconds,
            minimum_full_episode_duration_seconds=(
                self.minimum_full_episode_duration_seconds
            ),
            queue_fault_flag=self.queue_fault_flag,
        )


class SafetyCountersV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    provider_calls: Literal[0] = 0
    fault_attempts: Literal[0] = 0
    knowledge_loop_executions: Literal[0] = 0


class FormalIncidentDiagnosisCardinalityV0233(ProductModelV1):
    """Delta contract for the one authorized Incident and Diagnosis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-cardinality.v0233"] = (
        "ecomsre.product.formal-cardinality.v0233"
    )
    phase: Literal[
        "PRE_INCIDENT",
        "POST_INCIDENT_PRE_DIAGNOSIS",
        "POST_DIAGNOSIS_SUCCEEDED",
        "POST_DIAGNOSIS_FAILED",
    ]
    source_incident_count: int = Field(ge=0)
    source_diagnosis_job_count: int = Field(ge=0)
    source_diagnosis_result_count: int = Field(ge=0)
    source_evidence_index_count: int = Field(ge=0)
    source_fault_family_count: int = Field(ge=0)
    source_knowledge_artifact_count: int = Field(ge=0)
    source_baseline_job_count: int = Field(ge=0)
    current_incident_count: int = Field(ge=0)
    current_diagnosis_job_count: int = Field(ge=0)
    current_diagnosis_result_count: int = Field(ge=0)
    current_evidence_index_count: int = Field(ge=0)
    current_fault_family_count: int = Field(ge=0)
    current_knowledge_artifact_count: int = Field(ge=0)
    current_baseline_job_count: int = Field(ge=0)
    cardinality_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_deltas(self) -> FormalIncidentDiagnosisCardinalityV0233:
        source = (
            self.source_incident_count,
            self.source_diagnosis_job_count,
            self.source_diagnosis_result_count,
            self.source_evidence_index_count,
            self.source_fault_family_count,
            self.source_knowledge_artifact_count,
            self.source_baseline_job_count,
        )
        current = (
            self.current_incident_count,
            self.current_diagnosis_job_count,
            self.current_diagnosis_result_count,
            self.current_evidence_index_count,
            self.current_fault_family_count,
            self.current_knowledge_artifact_count,
            self.current_baseline_job_count,
        )
        deltas = tuple(after - before for before, after in zip(source, current))
        expected = {
            "PRE_INCIDENT": (0, 0, 0, 0, 0, 0, 0),
            "POST_INCIDENT_PRE_DIAGNOSIS": (1, 0, 0, 0, 0, 0, 0),
            "POST_DIAGNOSIS_SUCCEEDED": (1, 1, 1, 1, 0, 0, 0),
            "POST_DIAGNOSIS_FAILED": (1, 1, 0, 0, 0, 0, 0),
        }[self.phase]
        if deltas != expected:
            raise ValueError("Product v0.2.3.3 formal cardinality differs")
        body = self.model_dump(mode="json", exclude={"cardinality_sha256"})
        if self.cardinality_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.3.3 formal cardinality digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalIncidentDiagnosisCardinalityV0233:
        body = {
            "schema_version": "ecomsre.product.formal-cardinality.v0233",
            **payload,
        }
        return cls.model_validate(
            {**body, "cardinality_sha256": semantic_json_sha256_v0233(body)}
        )


class NoFaultAcceptanceResultV0233(ProductModelV1):
    """Campaign wrapper around the frozen v0.2.3.2 scorer result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.nofault-acceptance.v0233"] = (
        "ecomsre.product.nofault-acceptance.v0233"
    )
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_authority_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_restart_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_traffic_execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    fresh_runtime_snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    incident_traffic_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    incident_sha256: str = Field(pattern=_SHA256_PATTERN)
    diagnosis_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_index_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision_trace_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage_journal_tail_sha256: str = Field(pattern=_SHA256_PATTERN)
    v0232_assessment_sha256: str = Field(pattern=_SHA256_PATTERN)
    measured_terminal: Literal[
        "ECOMSRE_PRODUCT_V0233_NOFAULT_FULLY_SUPPORTED",
        "ECOMSRE_PRODUCT_V0233_NOFAULT_CAPABILITY_LIMITED",
        "ECOMSRE_PRODUCT_V0233_NOFAULT_NOT_SUPPORTED",
    ]
    reasons: tuple[str, ...]
    safety_counters: SafetyCountersV0233
    cleanup_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_canonical_sealed_result(self) -> NoFaultAcceptanceResultV0233:
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("Product v0.2.3.3 No-Fault reasons are not canonical")
        if (
            self.measured_terminal == NOFAULT_FULLY_SUPPORTED_V0233
            and self.reasons
        ):
            raise ValueError("fully supported No-Fault result contains reasons")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("Product v0.2.3.3 No-Fault result digest differs")
        return self

    @classmethod
    def build_from_v0232(
        cls,
        *,
        v0232_measured_terminal: str,
        **payload: Any,
    ) -> NoFaultAcceptanceResultV0233:
        try:
            mapped = _V0233_TERMINAL_BY_V0232[v0232_measured_terminal]
        except KeyError as error:
            raise ValueError("unsupported v0.2.3.2 No-Fault terminal") from error
        body = {
            "schema_version": "ecomsre.product.nofault-acceptance.v0233",
            **payload,
            "measured_terminal": mapped,
        }
        return cls.model_validate(
            {**body, "result_sha256": semantic_json_sha256_v0233(body)}
        )


class DiagnosisPipelineAcceptanceV0233(ProductModelV1):
    """Disjoint successful and failed Stage Journal acceptance shapes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.diagnosis-pipeline-acceptance.v0233"] = (
        "ecomsre.product.diagnosis-pipeline-acceptance.v0233"
    )
    job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    job_status: Literal["SUCCEEDED", "FAILED"]
    stage_journal_terminal: Literal["JOB_SUCCEEDED", "FAILED"]
    journal_tail_sha256: str = Field(pattern=_SHA256_PATTERN)
    event_count: int = Field(ge=1, le=100)
    failure_stage: str | None = None
    safe_error_code: str | None = None
    exception_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    diagnosis_result_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    evidence_bundle_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    evidence_index_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    decision_trace_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    private_failure_envelope_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    acceptance_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_one_pipeline_disposition(self) -> DiagnosisPipelineAcceptanceV0233:
        persisted = (
            self.diagnosis_result_sha256,
            self.evidence_bundle_sha256,
            self.evidence_index_sha256,
            self.decision_trace_sha256,
        )
        success = (
            self.job_status == "SUCCEEDED"
            and self.stage_journal_terminal == "JOB_SUCCEEDED"
            and self.failure_stage is None
            and self.safe_error_code is None
            and self.exception_fingerprint is None
            and all(value is not None for value in persisted)
            and self.private_failure_envelope_sha256 is None
        )
        failure = (
            self.job_status == "FAILED"
            and self.stage_journal_terminal == "FAILED"
            and self.failure_stage is not None
            and self.safe_error_code is not None
            and self.exception_fingerprint is not None
            and all(value is None for value in persisted)
            and self.private_failure_envelope_sha256 is not None
        )
        if success == failure:
            raise ValueError("Product v0.2.3.3 pipeline acceptance is not exact")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"acceptance_sha256"})
        )
        if self.acceptance_sha256 != expected:
            raise ValueError("Product v0.2.3.3 pipeline acceptance digest differs")
        return self

    @classmethod
    def _build(cls, body: dict[str, Any]) -> DiagnosisPipelineAcceptanceV0233:
        return cls.model_validate(
            {**body, "acceptance_sha256": semantic_json_sha256_v0233(body)}
        )

    @classmethod
    def build_success(cls, **payload: Any) -> DiagnosisPipelineAcceptanceV0233:
        return cls._build(
            {
                "schema_version": (
                    "ecomsre.product.diagnosis-pipeline-acceptance.v0233"
                ),
                **payload,
                "job_status": "SUCCEEDED",
                "stage_journal_terminal": "JOB_SUCCEEDED",
                "failure_stage": None,
                "safe_error_code": None,
                "exception_fingerprint": None,
                "private_failure_envelope_sha256": None,
            }
        )

    @classmethod
    def build_failure(cls, **payload: Any) -> DiagnosisPipelineAcceptanceV0233:
        return cls._build(
            {
                "schema_version": (
                    "ecomsre.product.diagnosis-pipeline-acceptance.v0233"
                ),
                **payload,
                "job_status": "FAILED",
                "stage_journal_terminal": "FAILED",
                "diagnosis_result_sha256": None,
                "evidence_bundle_sha256": None,
                "evidence_index_sha256": None,
                "decision_trace_sha256": None,
            }
        )


class FormalContractCaseV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[0-9]{2}_[A-Z0-9_]+$")
    expected_terminal: str
    observed_terminal: str
    reasons: tuple[str, ...] = ()
    passed: bool
    case_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_case_seal(self) -> FormalContractCaseV0233:
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"case_sha256"})
        )
        if self.case_sha256 != expected:
            raise ValueError("Product v0.2.3.3 preflight case digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalContractCaseV0233:
        return cls.model_validate(
            {**payload, "case_sha256": semantic_json_sha256_v0233(payload)}
        )


class FormalContractPreflightV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-contract-preflight.v0233"] = (
        "ecomsre.product.formal-contract-preflight.v0233"
    )
    goal_version: Literal[
        "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
    ] = "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0233_FORMAL_CONTRACT_PREFLIGHT_PASS",
        "ECOMSRE_PRODUCT_V0233_FORMAL_CONTRACT_PREFLIGHT_FAIL",
    ]
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    case_count: int = Field(ge=1)
    passed_case_count: int = Field(ge=0)
    cases: tuple[FormalContractCaseV0233, ...]
    fixture_pipeline: DiagnosisPipelineAcceptanceV0233
    fixture_evidence_bundle_persisted: bool
    fixture_evidence_index_persisted: bool
    fixture_decision_trace_persisted: bool
    fixture_scorer_terminal: str
    fixture_scorer_expected_terminal: bool
    action_authority: Literal["NONE"] = "NONE"
    formal_execution_count: Literal[0] = 0
    new_incident_count: Literal[0] = 0
    new_diagnosis_count: Literal[0] = 0
    provider_calls: Literal[0] = 0
    agent_writes: Literal[0] = 0
    runbook_executions: Literal[0] = 0
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_preflight(self) -> FormalContractPreflightV0233:
        passed = (
            self.case_count == len(self.cases)
            and self.passed_case_count == self.case_count
            and all(case.passed for case in self.cases)
            and len({case.case_id for case in self.cases}) == self.case_count
            and self.fixture_pipeline.job_status == "SUCCEEDED"
            and self.fixture_evidence_bundle_persisted
            and self.fixture_evidence_index_persisted
            and self.fixture_decision_trace_persisted
            and self.fixture_scorer_expected_terminal
        )
        if (self.terminal == FORMAL_CONTRACT_PREFLIGHT_PASS_V0233) != passed:
            raise ValueError("Product v0.2.3.3 formal preflight disposition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("Product v0.2.3.3 formal preflight digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> FormalContractPreflightV0233:
        body = {
            "schema_version": "ecomsre.product.formal-contract-preflight.v0233",
            "goal_version": _GOAL_VERSION,
            **payload,
        }
        normalized = cls.model_construct(
            **body, preflight_sha256="0" * 64
        ).model_dump(mode="json", exclude={"preflight_sha256"})
        return cls.model_validate(
            {**body, "preflight_sha256": semantic_json_sha256_v0233(normalized)}
        )


def load_fresh_traffic_profile_v0233(
    project_root: Any,
    *,
    role: Literal["PREFLIGHT", "FORMAL"],
) -> FreshTrafficProfileV0233:
    from pathlib import Path

    name = "preflight-profile.json" if role == "PREFLIGHT" else "formal-profile.json"
    profile = FreshTrafficProfileV0233.model_validate_json(
        (Path(project_root) / "config/product-v0233/traffic" / name).read_bytes()
    )
    if profile.role != role:
        raise ValueError(f"Product v0.2.3.3 {role.lower()} traffic profile differs")
    return profile


def load_fresh_formal_campaign_v0233(project_root: Any) -> FreshFormalCampaignV0233:
    from pathlib import Path

    root = Path(project_root)
    campaign = FreshFormalCampaignV0233.model_validate_json(
        (root / "config/product-v0233/campaign.json").read_bytes()
    )
    preflight = load_fresh_traffic_profile_v0233(root, role="PREFLIGHT")
    formal = load_fresh_traffic_profile_v0233(root, role="FORMAL")
    source_selection = __import__("json").loads(
        (root / "config/product-v0233/source-selection.json").read_text()
    )
    if (
        campaign.preflight_profile_sha256 != preflight.profile_sha256
        or campaign.formal_profile_sha256 != formal.profile_sha256
        or campaign.source_selection_sha256 != source_selection.get("selection_sha256")
    ):
        raise ValueError("Product v0.2.3.3 campaign profile/source binding differs")
    return campaign


def admit_incident_creation_v0233(
    *,
    runtime_authority_pass: bool,
    baseline_restart_pass: bool,
    formal_traffic_pass: bool,
    fresh_runtime_snapshot_pass: bool,
    new_incident_count: int,
    new_diagnosis_count: int,
) -> Literal["ECOMSRE_PRODUCT_V0233_INCIDENT_CREATION_ADMITTED"]:
    """Fail closed until every pre-Incident formal gate has passed."""

    if not runtime_authority_pass:
        raise ValueError("RUNTIME_AUTHORITY_NOT_PASS")
    if not baseline_restart_pass:
        raise ValueError("BASELINE_RESTART_NOT_PASS")
    if not formal_traffic_pass:
        raise ValueError("FORMAL_TRAFFIC_NOT_PASS")
    if not fresh_runtime_snapshot_pass:
        raise ValueError("FRESH_RUNTIME_SNAPSHOT_NOT_PASS")
    if new_incident_count != 0 or new_diagnosis_count != 0:
        raise ValueError("FORMAL_CARDINALITY_ALREADY_CONSUMED")
    return "ECOMSRE_PRODUCT_V0233_INCIDENT_CREATION_ADMITTED"


__all__ = (
    "FORMAL_CONTRACT_PREFLIGHT_PASS_V0233",
    "NOFAULT_CAPABILITY_LIMITED_V0233",
    "NOFAULT_FULLY_SUPPORTED_V0233",
    "NOFAULT_NOT_SUPPORTED_V0233",
    "DiagnosisPipelineAcceptanceV0233",
    "FormalIncidentDiagnosisCardinalityV0233",
    "FormalContractCaseV0233",
    "FormalContractPreflightV0233",
    "FreshFormalCampaignV0233",
    "FreshTrafficProfileV0233",
    "NoFaultAcceptanceResultV0233",
    "SafetyCountersV0233",
    "admit_incident_creation_v0233",
    "load_fresh_formal_campaign_v0233",
    "load_fresh_traffic_profile_v0233",
)
