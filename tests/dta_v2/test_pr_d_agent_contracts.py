from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.agent_contracts import (
    ActionSelectionDecision,
    AgentIdentityManifest,
    build_agent_identity_manifest,
    build_candidate_action_view,
)
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionParameter,
    CandidateRunbook,
    EvidenceSource,
    RiskLevel,
    RunbookId,
    RunbookParameterSpec,
    RunbookParameterType,
    build_candidate_set,
)


def _candidate_set():
    candidate = CandidateRunbook(
        schema_version="dta-v2.candidate-runbook.v1",
        runbook_id=RunbookId.RESTART_SERVICE,
        runbook_sha256="1" * 64,
        target_service="recommendation",
        risk_level=RiskLevel.LOW,
        parameters=(
            RunbookParameterSpec(
                name="wait_for_health_seconds",
                parameter_type=RunbookParameterType.INTEGER,
                required=True,
                minimum=5,
                maximum=120,
            ),
        ),
        required_evidence_sources=(
            EvidenceSource.METRICS,
            EvidenceSource.RUNTIME,
        ),
    )
    return build_candidate_set(
        run_id="a" * 32,
        diagnosis_sha256="2" * 64,
        resolved_evidence_sha256="3" * 64,
        registry_sha256="4" * 64,
        write_candidates=(candidate,),
    )


def test_candidate_action_view_is_exact_stage_11_safe_surface() -> None:
    view = build_candidate_action_view(_candidate_set())
    payload = view.model_dump(mode="json")

    assert set(payload) == {
        "schema_version",
        "write_candidates",
        "allowed_nonwrite_dispositions",
    }
    assert set(payload["write_candidates"][0]) == {
        "runbook_id",
        "target_service",
        "risk_level",
        "parameters",
        "required_evidence_sources",
    }
    assert payload["write_candidates"][0]["parameters"][0] == {
        "name": "wait_for_health_seconds",
        "parameter_type": "INTEGER",
        "required": True,
        "minimum": 5,
        "maximum": 120,
        "allowed_values": [],
    }
    serialized = json.dumps(payload, sort_keys=True).casefold()
    for forbidden in (
        "sha256",
        "registry",
        "precondition",
        "forward_step",
        "executor",
        "verifier",
        "command",
        "path",
        "container",
        "gold",
    ):
        assert forbidden not in serialized


def test_action_selection_decision_cannot_set_risk_or_write_authority() -> None:
    decision = ActionSelectionDecision(
        schema_version="dta-v2.action-selection-decision.v1",
        disposition=ActionDisposition.EXECUTE_RUNBOOK,
        runbook_id=RunbookId.RESTART_SERVICE,
        target_service="recommendation",
        parameters=(
            ActionParameter(name="wait_for_health_seconds", value=30),
        ),
        supporting_evidence_refs=(
            f"evidence://{'a' * 32}/metrics/0001",
            f"evidence://{'a' * 32}/runtime/0002",
        ),
        rationale="The bounded candidate matches the diagnosed runtime failure.",
    )
    assert "risk_level" not in decision.model_dump()
    with pytest.raises(ValidationError, match="extra"):
        ActionSelectionDecision.model_validate(
            {**decision.model_dump(mode="json"), "executor_id": "arbitrary"}
        )


def test_agent_identity_manifest_is_exact_and_digest_bound() -> None:
    manifest = build_agent_identity_manifest(
        model_id="gpt-5.4-mini-2026-03-17",
        prompt_sha256="1" * 64,
        tool_schema_sha256="2" * 64,
        diagnosis_schema_sha256="3" * 64,
        action_selection_schema_sha256="4" * 64,
        action_proposal_schema_sha256="5" * 64,
    )
    assert manifest.temperature == 0.0
    assert manifest.provider_adapter_version == (
        "dta-v2.openai-compatible-agent.v1"
    )
    assert manifest.identity_sha256 != "0" * 64

    forged = manifest.model_dump(mode="python")
    forged["model_id"] = "different-model"
    with pytest.raises(ValidationError, match="identity digest"):
        AgentIdentityManifest.model_validate(forged)
