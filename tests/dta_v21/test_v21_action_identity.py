from __future__ import annotations

from pathlib import Path
import json

import pytest

from ecomsre.dta_v2.v21.agent_contracts import (
    ActionSelectionDecisionV21,
    AgentArmV21,
    AgentIdentityManifestV21,
    build_action_proposal_v21,
    build_candidate_action_view_v21,
)
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre.dta_v2.v21.replay import build_replay_diagnosis


ROOT = Path(__file__).resolve().parents[2]


def test_action_selection_can_choose_only_an_exact_visible_candidate() -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id="2" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="ad",
        fault_domain=FaultDomainV21.LOCAL_RESOURCE,
        mechanism=FaultMechanismV21.CPU_SATURATION,
        evidence_sources=("METRICS", "RUNTIME", "RESOURCES"),
    )
    registry = load_default_runbook_registry(ROOT)
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target="ad",
    )
    visible = build_candidate_action_view_v21(candidates)
    decision = ActionSelectionDecisionV21(
        schema_version="dta-v21.action-selection-decision.v1",
        disposition=ActionDispositionV21.EXECUTE_RUNBOOK,
        runbook_id=RunbookIdV21.MITIGATE_CPU_SATURATION,
        target_service="ad",
        parameters=(),
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        rationale="The visible bounded candidate matches the cited observations.",
    )

    proposal = build_action_proposal_v21(
        diagnosis=diagnosis,
        resolved_evidence=evidence,
        candidate_set=candidates,
        candidate_view=visible,
        registry=registry,
        decision=decision,
    )
    assert proposal.runbook_id is RunbookIdV21.MITIGATE_CPU_SATURATION
    assert proposal.target_service == "ad"

    with pytest.raises(ValueError, match="visible candidate"):
        build_action_proposal_v21(
            diagnosis=diagnosis,
            resolved_evidence=evidence,
            candidate_set=candidates,
            candidate_view=visible,
            registry=registry,
            decision=decision.model_copy(
                update={"runbook_id": RunbookIdV21.RESTART_SERVICE}
            ),
        )


def test_three_arm_identities_share_model_temperature_and_common_schemas() -> None:
    identities = build_three_arm_identities_v21(
        model_id="gpt-5.4-mini-2026-03-17",
        max_completion_tokens=1600,
    )

    assert tuple(item.arm for item in identities) == tuple(AgentArmV21)
    assert {item.model_id for item in identities} == {"gpt-5.4-mini-2026-03-17"}
    assert {item.temperature for item in identities} == {0.0}
    assert len({item.diagnosis_schema_sha256 for item in identities}) == 1
    assert len({item.action_selection_schema_sha256 for item in identities}) == 1
    assert len({item.action_proposal_schema_sha256 for item in identities}) == 1
    assert len({item.identity_sha256 for item in identities}) == 3
    planner = next(
        item for item in identities if item.arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER
    )
    assert planner.planner_schema_sha256 is not None
    assert all(item.context_projection_source_sha256 for item in identities)


def test_provisional_three_arm_identity_files_match_runtime_exactly() -> None:
    expected = {
        item.arm: item
        for item in build_three_arm_identities_v21(
            model_id="gpt-5.4-mini-2026-03-17",
            max_completion_tokens=1600,
        )
    }
    directory = ROOT / "config/dta-v21/agent-identities"
    loaded = tuple(
        AgentIdentityManifestV21.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    )

    assert len(loaded) == 3
    assert {item.arm: item for item in loaded} == expected


def test_pr_c_public_smoke_report_is_sanitized_and_hash_bound() -> None:
    report = json.loads(
        (ROOT / "docs/results/dta-v21-pr-c-development-smoke.json").read_text(
            encoding="utf-8"
        )
    )
    digest = report.pop("report_sha256")

    assert digest == semantic_sha256(report)
    assert report["status"] == "PASS"
    assert report["provider"]["formal_persisted_attempts"] == 6
    assert report["provider"]["failed_attempts_retained"] == 5
    assert report["provider"]["final_status"] == "PASS"
    assert report["live_docker_actions"] == 0
    assert report["runbook_executions"] == 0
    assert report["held_out_executions"] == 0
    serialized = json.dumps(report).casefold()
    for forbidden in (
        "api_key",
        "authorization",
        "raw_provider_response",
        "/users/",
        "chain_of_thought",
    ):
        assert forbidden not in serialized
