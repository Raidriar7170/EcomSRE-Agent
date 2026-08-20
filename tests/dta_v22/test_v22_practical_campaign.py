from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre.dta_v2.v22.controller_contracts import (
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
)
from ecomsre.dta_v2.v22.practical_campaign import (
    PracticalTruthSetV22,
    load_practical_truth_set_v22,
    run_practical_campaign_v22,
)
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaptureKindV22,
    PracticalCaseSetV22,
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import PracticalRunStatusV22
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.simple_provider import ProviderTurnOutcomeV22
from ecomsre.dta_v2.v22.simple_provider import SHARED_SYSTEM_PROMPT_V22


ROOT = Path(__file__).resolve().parents[2]


class _NoIncidentProvider:
    calls = 0

    def complete_turn(
        self,
        *,
        turn_input: object,
        run_id: str,
        system_prompt: str,
        allow_semantic_repair: bool,
    ) -> ProviderTurnOutcomeV22:
        del turn_input, run_id, system_prompt, allow_semantic_repair
        self.calls += 1
        return ProviderTurnOutcomeV22(
            decision=ControllerDecisionV22(
                decision=ControllerDecisionKindV22.NO_INCIDENT,
                working_hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
                action_id=NO_ACTION_ID_V22,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            ),
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            semantic_repair_used=False,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            latency_ms=1.0,
        )


def test_frozen_sets_have_required_distribution_and_honest_synthetic_counts() -> None:
    development = load_practical_case_set_v22(
        ROOT / "config/dta-v22-sprint/development/cases.json"
    )
    evaluation = load_practical_case_set_v22(
        ROOT / "config/dta-v22-sprint/evaluation/cases.json"
    )

    assert len(development.cases) == 8
    assert len(evaluation.cases) == 12
    assert sum(
        item.capture_kind is PracticalCaptureKindV22.REAL_PUBLIC_REPLAY
        for item in evaluation.cases
    ) == 9
    assert sum(item.bootstrap_insufficient_expected for item in evaluation.cases) >= 6
    assert {pair for item in evaluation.cases for pair in item.counterfactual_pair_ids} == {
        "cf1",
        "cf2",
        "cf3",
        "cf4",
    }
    payment_dependency = materialize_practical_case_v22(
        spec=next(item for item in evaluation.cases if item.case_id == "e08"),
        repository_root=ROOT,
    )
    assert payment_dependency.candidate_services == ("checkout", "payment")
    assert payment_dependency.topology_edges == (("checkout", "payment"),)
    assert (
        ROOT.joinpath("config/dta-v22-sprint/prompt.txt")
        .read_text(encoding="utf-8")
        .strip()
        == SHARED_SYSTEM_PROMPT_V22
    )


def test_fixed_evaluation_manifest_binds_frozen_inputs() -> None:
    manifest = json.loads(
        (ROOT / "config/dta-v22-sprint/evaluation/manifest.json").read_bytes()
    )

    assert manifest["case_count"] == 12
    assert manifest["arms"] == ["FLAT_CANONICAL", "PLANNER_LITE"]
    assert manifest["same_case_bytes_both_arms_required"] is True
    assert manifest["private_seal"] is False
    for item in manifest["frozen_files"]:
        payload = (ROOT / item["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]


def test_campaign_loads_truth_only_after_both_arms(tmp_path: Path) -> None:
    development = load_practical_case_set_v22(
        ROOT / "config/dta-v22-sprint/development/cases.json"
    )
    healthy = next(item for item in development.cases if item.case_id == "d07")
    case_path = tmp_path / "cases.json"
    truth_path = tmp_path / "truth.json"
    case_path.write_text(
        PracticalCaseSetV22(
            schema_version="dta-v22.practical-case-set.v1",
            cases=(healthy,),
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    truth_path.write_text(
        PracticalTruthSetV22(
            schema_version="dta-v22.practical-truth-set.v1",
            truths=(
                PracticalTruthV22(
                    case_id="d07",
                    expected_terminal="NO_INCIDENT",
                    expected_root_service=None,
                    expected_mechanism=None,
                    evidence_applicable=False,
                ),
            ),
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    provider = _NoIncidentProvider()

    def load_after_runs(path: Path) -> PracticalTruthSetV22:
        assert provider.calls == 2
        return load_practical_truth_set_v22(path)

    result = run_practical_campaign_v22(
        case_set_path=case_path,
        truth_path=truth_path,
        repository_root=ROOT,
        provider=provider,  # type: ignore[arg-type]
        truth_loader=load_after_runs,
    )

    assert len(result.case_runs) == 2
    assert all(
        item.status is PracticalRunStatusV22.VALID_TERMINAL
        for item in result.case_runs
    )
    assert len({item.case_bytes_sha256 for item in result.case_runs}) == 1
    assert result.truth_loaded_after_both_arms is True
    assert result.agent_writes == 0
