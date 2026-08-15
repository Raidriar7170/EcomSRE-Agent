"""Typed no-fault environment/readiness admission without diagnosis projection."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ecomsre_live_sandbox.contracts import FrozenModel, canonical_sha256
from ecomsre_live_sandbox.e2e_source_batch import OrderedSourceBatch


NoFaultReason = Literal[
    "SERVICES_NOT_HEALTHY",
    "BASELINE_NOT_EXACT",
    "SOURCE_NOT_AVAILABLE",
    "SOURCE_TARGET_EMPTY",
    "EVIDENCE_REF_INVALID",
    "BROAD_METRIC_SERVICE_COUNT_BELOW_MINIMUM",
    "LOG_QUERY_CONTRACT_NOT_COMPLETED",
    "TRACE_QUERY_CONTRACT_NOT_COMPLETED",
    "CONTROL_TRUTH_LEAK",
    "PRIVATE_PERMISSION_VIOLATION",
]


class NoFaultReadiness(FrozenModel):
    schema_version: Literal["live-e2e.no-fault-readiness.v5"] = (
        "live-e2e.no-fault-readiness.v5"
    )
    run_id: str = Field(min_length=1, max_length=128)
    services_healthy_count: int = Field(ge=0)
    baseline_exact: bool
    source_statuses: dict[str, str]
    source_counts: dict[str, int]
    invalid_refs: int = Field(ge=0)
    all_refs_resolve: bool
    broad_metric_service_count: int = Field(ge=0)
    logs_query_contract_completed: bool
    traces_query_contract_completed: bool
    control_truth_findings: tuple[str, ...]
    passed: bool
    reason_codes: tuple[NoFaultReason, ...]
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_semantic_hash(self) -> "NoFaultReadiness":
        payload = self.model_dump(mode="json", exclude={"semantic_sha256"})
        if self.semantic_sha256 != canonical_sha256(payload):
            raise ValueError("NoFaultReadiness semantic SHA-256 differs")
        if self.passed != (not self.reason_codes):
            raise ValueError("NoFaultReadiness disposition differs")
        return self


def evaluate_no_fault_readiness(
    *,
    run_id: str,
    source_batch: OrderedSourceBatch,
    services_healthy_count: int,
    baseline_exact: bool,
    broad_metric_service_count: int,
    logs_query_contract_completed: bool,
    traces_query_contract_completed: bool,
    private_permissions_valid: bool,
    control_truth_findings: tuple[str, ...],
) -> NoFaultReadiness:
    statuses = {
        item.source: item.status.value for item in source_batch.source_results
    }
    reasons: list[NoFaultReason] = []
    if services_healthy_count != 25:
        reasons.append("SERVICES_NOT_HEALTHY")
    if not baseline_exact:
        reasons.append("BASELINE_NOT_EXACT")
    if any(status != "AVAILABLE" for status in statuses.values()) or len(statuses) != 3:
        reasons.append("SOURCE_NOT_AVAILABLE")
    if any(count <= 0 for count in source_batch.source_counts.values()):
        reasons.append("SOURCE_TARGET_EMPTY")
    if source_batch.invalid_ref_count != 0 or not source_batch.all_refs_resolve:
        reasons.append("EVIDENCE_REF_INVALID")
    if broad_metric_service_count < 3:
        reasons.append("BROAD_METRIC_SERVICE_COUNT_BELOW_MINIMUM")
    if not logs_query_contract_completed:
        reasons.append("LOG_QUERY_CONTRACT_NOT_COMPLETED")
    if not traces_query_contract_completed:
        reasons.append("TRACE_QUERY_CONTRACT_NOT_COMPLETED")
    if control_truth_findings:
        reasons.append("CONTROL_TRUTH_LEAK")
    if not private_permissions_valid:
        reasons.append("PRIVATE_PERMISSION_VIOLATION")
    payload = {
        "schema_version": "live-e2e.no-fault-readiness.v5",
        "run_id": run_id,
        "services_healthy_count": services_healthy_count,
        "baseline_exact": baseline_exact,
        "source_statuses": statuses,
        "source_counts": source_batch.source_counts,
        "invalid_refs": source_batch.invalid_ref_count,
        "all_refs_resolve": source_batch.all_refs_resolve,
        "broad_metric_service_count": broad_metric_service_count,
        "logs_query_contract_completed": logs_query_contract_completed,
        "traces_query_contract_completed": traces_query_contract_completed,
        "control_truth_findings": control_truth_findings,
        "passed": not reasons,
        "reason_codes": tuple(reasons),
    }
    return NoFaultReadiness.model_validate(
        {**payload, "semantic_sha256": canonical_sha256(payload)}
    )


__all__ = ["NoFaultReadiness", "evaluate_no_fault_readiness"]
