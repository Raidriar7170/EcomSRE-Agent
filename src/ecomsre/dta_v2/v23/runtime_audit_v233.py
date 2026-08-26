"""Development-only Provider boundary audit over the frozen v2.3.2 set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts_v231 import ReviewRecommendationV231
from ecomsre.dta_v2.v23.discovery_runtime_v233 import run_discovery_case_v233
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    AdmissionStratumV232,
    load_evaluation_cases_v232,
    load_evaluation_truth_index_v232,
    load_evaluation_truth_shard_v232,
    load_evaluation_views_v232,
)


class ProviderAuditEntryV233(DtaModelV22):
    case_id: str
    stratum: AdmissionStratumV232
    terminal: str
    report_generated: StrictBool
    initial_protocol_valid: StrictBool
    runtime_binding_drift: StrictBool
    action_authority_violation: StrictBool
    discovery_reads: int = Field(ge=0, le=3)
    provider_calls: int = Field(ge=0)
    protocol_repairs: int = Field(ge=0, le=2)


class ProviderAuditV233(DtaModelV22):
    schema_version: Literal["dta-v233.provider-audit.v1"]
    development_set: Literal["dta-v232-fixed-24-case"]
    case_count: Literal[24]
    report_eligible_cases: int
    initial_protocol_valid: int
    post_repair_success: int
    root_domain_evidence_drift: int
    action_authority_violations: int
    registered_known_unchanged: int
    no_incident_unchanged: int
    maximum_discovery_reads: int
    provider_calls: int
    provider_synthesis_iteration_count: Literal[1]
    entries: tuple[ProviderAuditEntryV233, ...] = Field(
        min_length=24,
        max_length=24,
    )
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_audit(self) -> "ProviderAuditV233":
        ids = tuple(item.case_id for item in self.entries)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("v2.3.3 Provider audit cases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"audit_sha256"})
        )
        if self.audit_sha256 != expected:
            raise ValueError("v2.3.3 Provider audit digest differs")
        return self


def _contract_fixture_transport(body: str) -> str:
    """Return only narrative fields from the serialized observer request."""

    request = json.loads(body)["request"]
    hypotheses = request["competing_hypotheses"]
    preferred = sorted(
        hypotheses,
        key=lambda item: (-item["relative_support_score"], item["hypothesis_id"]),
    )[0]
    alternatives = sorted(
        item["hypothesis_id"]
        for item in hypotheses
        if item["hypothesis_id"] != preferred["hypothesis_id"]
    )
    unresolved = sorted(set(request["unresolved_dimensions"]))
    return json.dumps(
        {
            "preferred_hypothesis_id": preferred["hypothesis_id"],
            "provisional_mechanism_label": preferred["provisional_label"],
            "mechanism_description": (
                "Observer-visible evidence supports this provisional narrative; "
                "runtime-owned fields remain unchanged."
            ),
            "alternative_hypothesis_ids": alternatives,
            "unresolved_questions": sorted(preferred["unresolved_questions"]),
            "recommended_next_observations": [
                f"Collect bounded evidence for {item.casefold()}"
                for item in unresolved
            ],
            "review_recommendation": (
                ReviewRecommendationV231.REQUEST_MORE_EVIDENCE.value
            ),
        },
        sort_keys=True,
    )


def build_v232_provider_audit_v233(*, repository_root: Path) -> ProviderAuditV233:
    evaluation_root = repository_root / "config/dta-v232/evaluation"
    cases = load_evaluation_cases_v232(evaluation_root / "cases.json")
    views = load_evaluation_views_v232(evaluation_root / "ontology-views.json")
    truth_index = load_evaluation_truth_index_v232(evaluation_root / "truth.json")
    novelty = {
        AdmissionStratumV232.NOVEL_HIDDEN,
        AdmissionStratumV232.NOVEL_UNREGISTERED,
    }
    entries: list[ProviderAuditEntryV233] = []
    for spec in cases.cases:
        stratum = load_evaluation_truth_shard_v232(
            index_path=evaluation_root / "truth.json",
            binding=truth_index.require(spec.case_id),
        ).record.admission_stratum
        state = run_discovery_case_v233(
            repository_root=repository_root,
            spec=spec,
            view_spec=views.require(spec.case_id),
            provider_transport=_contract_fixture_transport,
        )
        report = state.provisional_report
        projection = state.domain_projection
        drift = bool(
            report is not None
            and projection is not None
            and (
                report.runtime_selected_root_service
                != projection.selected_root_service
                or report.broad_fault_domain is not projection.selected_domain
                or report.supporting_evidence_refs
                != projection.supporting_evidence_refs
                or report.contradicting_evidence_refs
                != projection.contradicting_evidence_refs
            )
        )
        eligible = stratum in novelty
        entries.append(
            ProviderAuditEntryV233(
                case_id=spec.case_id,
                stratum=stratum,
                terminal=state.terminal,
                report_generated=report is not None,
                initial_protocol_valid=(
                    eligible and report is not None and state.protocol_repairs == 0
                ),
                runtime_binding_drift=drift,
                action_authority_violation=(
                    report is not None and report.action_authority != "NONE"
                ),
                discovery_reads=len(state.discovery_reads),
                provider_calls=state.provider_calls,
                protocol_repairs=state.protocol_repairs,
            )
        )
    canonical = tuple(sorted(entries, key=lambda item: item.case_id))
    payload: dict[str, Any] = {
        "schema_version": "dta-v233.provider-audit.v1",
        "development_set": "dta-v232-fixed-24-case",
        "case_count": 24,
        "report_eligible_cases": sum(item.stratum in novelty for item in canonical),
        "initial_protocol_valid": sum(
            item.initial_protocol_valid for item in canonical
        ),
        "post_repair_success": sum(
            item.stratum in novelty and item.report_generated for item in canonical
        ),
        "root_domain_evidence_drift": sum(
            item.runtime_binding_drift for item in canonical
        ),
        "action_authority_violations": sum(
            item.action_authority_violation for item in canonical
        ),
        "registered_known_unchanged": sum(
            item.stratum is AdmissionStratumV232.REGISTERED_KNOWN
            and item.terminal == "REGISTERED_KNOWN"
            for item in canonical
        ),
        "no_incident_unchanged": sum(
            item.stratum is AdmissionStratumV232.NO_INCIDENT
            and item.terminal == "NO_INCIDENT"
            for item in canonical
        ),
        "maximum_discovery_reads": max(
            item.discovery_reads for item in canonical
        ),
        "provider_calls": sum(item.provider_calls for item in canonical),
        "provider_synthesis_iteration_count": 1,
        "entries": canonical,
    }
    draft = ProviderAuditV233.model_construct(
        **payload,
        audit_sha256="0" * 64,
    )
    return ProviderAuditV233.model_validate(
        {
            **payload,
            "audit_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"audit_sha256"})
            ),
        }
    )


__all__ = (
    "ProviderAuditEntryV233",
    "ProviderAuditV233",
    "build_v232_provider_audit_v233",
)
