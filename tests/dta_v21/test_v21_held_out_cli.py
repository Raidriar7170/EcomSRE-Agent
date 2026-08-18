from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import ecomsre.dta_v2.v21.evaluation_contracts as evaluation_contracts_module
from ecomsre.dta_v2.agent_contracts import ProviderUsage
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    MetricRecord,
    MetricUnit,
    ToolName,
)
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21
from ecomsre.dta_v2.v21.agent_provider import ProviderTurnV21
from ecomsre.dta_v2.v21.capture_campaign import build_default_capture_plan_v21
from ecomsre.dta_v2.v21.contracts import (
    DtaDiagnosisV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.evaluation_campaign import (
    build_evaluation_freeze_manifest_v21,
    build_evaluation_preregistration_v21,
    build_evaluation_schedule_v21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationSplitV21,
    PublicCaseBindingV21,
    PublicEvaluationManifestV21,
    ReplayObservationFixtureV21,
)
from ecomsre.dta_v2.v21.evaluation_seal import seal_held_out_pack_v21
from ecomsre.dta_v2.v21.held_out_cli import (
    score_held_out_evaluation_v21,
    execute_held_out_evaluation_v21,
    verify_held_out_execution_seal_v21,
    verify_private_held_out_evaluation_v21,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.owned_capture import build_evaluator_truth_v21
from ecomsre.dta_v2.v21.planner_contracts import (
    DiagnosticHypothesisV21,
    HypothesisStatusV21,
    PlannerNextStepV21,
    build_evidence_plan_decision_v21,
)
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_live_sandbox.contracts import write_private_json
from scripts.ci.verify_dta_v21_held_out import verify_public_held_out_report_v21


ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-5.4-mini-2026-03-17"
START = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)


def _hashed(model_type, payload: dict[str, object], field: str):
    draft = model_type.model_construct(**payload, **{field: "0" * 64})
    return model_type.model_validate(
        {
            **payload,
            field: semantic_sha256(draft.model_dump(mode="json", exclude={field})),
        }
    )


def _visible_case(case_id: str, scenario_id: str) -> AgentVisibleReplayCaseV21:
    tools = tuple(sorted(tuple(ToolName)[:4], key=lambda item: item.value))
    fixtures = tuple(
        _hashed(
            ReplayObservationFixtureV21,
            {
                "schema_version": "dta-v21.replay-observation-fixture.v1",
                "tool": tool,
                "service_scope": ("frontend",),
                "records": (
                    (
                        MetricRecord(
                            service="frontend",
                            metric_kind=MetricKind.ERROR_RATE,
                            value=0.0,
                            unit=MetricUnit.RATIO,
                            sample_count=20,
                        ),
                    )
                    if tool is ToolName.QUERY_METRICS
                    else ()
                ),
                "truncated": False,
                "error_code": None,
            },
            "fixture_sha256",
        )
        for tool in tools
    )
    return _hashed(
        AgentVisibleReplayCaseV21,
        {
            "schema_version": "dta-v21.agent-visible-replay-case.v1",
            "case_id": case_id,
            "scenario_id": scenario_id,
            "captured_started_at": START,
            "captured_ended_at": START + timedelta(seconds=30),
            "observations": fixtures,
            "full_context_tools": tools,
        },
        "case_sha256",
    )


class _GitRunner:
    def __init__(self, head: str, *, status: str = "") -> None:
        self.head = head
        self.status = status

    def run(self, arguments: tuple[str, ...], *, timeout_seconds: float):
        del timeout_seconds
        if arguments == ("git", "rev-parse", "HEAD"):
            return SimpleNamespace(exit_code=0, stdout=f"{self.head}\n")
        if arguments == ("git", "status", "--porcelain"):
            return SimpleNamespace(exit_code=0, stdout=self.status)
        if arguments[:2] == ("git", "show"):
            relative = arguments[2].split(":", 1)[1]
            return SimpleNamespace(
                exit_code=0,
                stdout=(ROOT / relative).read_text(encoding="utf-8"),
            )
        raise AssertionError(arguments)


def _frozen_pack(tmp_path: Path):
    base_head = "a" * 40
    plan = build_default_capture_plan_v21(base_head=base_head)
    development = []
    held_out = []
    pack = tmp_path / "held-out-pack"
    for item in plan.cases:
        visible = _visible_case(item.case_id, item.scenario_id)
        truth = build_evaluator_truth_v21(item)
        binding = PublicCaseBindingV21(
            case_id=item.case_id,
            case_sha256=visible.case_sha256,
            truth_sha256=truth.truth_sha256,
            split_sha256=semantic_sha256(item.split.value),
        )
        if item.split is EvaluationSplitV21.DEVELOPMENT:
            development.append(binding)
        else:
            held_out.append(binding)
            write_private_json(
                pack / "cases" / item.case_id / "agent-visible.json",
                visible,
                create_once=True,
            )
            write_private_json(
                pack / "cases" / item.case_id / "evaluator-truth.json",
                truth,
                create_once=True,
            )
    public = _hashed(
        PublicEvaluationManifestV21,
        {
            "schema_version": "dta-v21.public-evaluation-manifest.v1",
            "case_schema_version": "dta-v21.agent-visible-replay-case.v1",
            "truth_schema_version": "dta-v21.evaluator-case-truth.v1",
            "development_cases": tuple(development),
            "held_out_cases": tuple(held_out),
        },
        "manifest_sha256",
    )
    schedule = build_evaluation_schedule_v21(
        seed_sha256=semantic_sha256("held-out execution test")
    )
    preregistration = build_evaluation_preregistration_v21(
        model_id=MODEL,
        max_completion_tokens=1600,
        schedule_sha256=schedule.schedule_sha256,
    )
    freeze = build_evaluation_freeze_manifest_v21(
        repository_root=ROOT,
        base_code_head=base_head,
        model_id=MODEL,
        max_completion_tokens=1600,
        public_case_manifest=public,
        schedule=schedule,
        preregistration=preregistration,
        git_runner=_GitRunner(base_head),
    )
    seal = seal_held_out_pack_v21(
        held_out_pack_root=pack,
        freeze_manifest=freeze,
        schedule=schedule,
        preregistration=preregistration,
        created_at=START,
    )
    return pack, freeze, schedule, preregistration, seal


class _AlwaysAbstainProvider:
    def __init__(self, arm: AgentArmV21) -> None:
        self.identity = next(
            item
            for item in build_three_arm_identities_v21(
                model_id=MODEL, max_completion_tokens=1600
            )
            if item.arm is arm
        )
        self.attempted_calls = 0

    def investigation_turn(self, *, context, visible_state, read_tools_enabled):
        del visible_state, read_tools_enabled
        self.attempted_calls += 1
        if self.identity.arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER:
            hypothesis = DiagnosticHypothesisV21(
                hypothesis_id="h1",
                root_service=context.candidate_services[0],
                fault_domain=FaultDomainV21.CONFIGURATION,
                fault_mechanism=FaultMechanismV21.CONFIGURATION_ERROR,
                status=HypothesisStatusV21.ACTIVE,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
                unresolved_evidence_sources=(EvidenceSourceV21.METRICS,),
            )
            plan = build_evidence_plan_decision_v21(
                run_id=context.run_id,
                turn_ordinal=1,
                hypotheses=(hypothesis,),
                next_step=PlannerNextStepV21.ABSTAIN,
                evidence_gap_sources=(EvidenceSourceV21.METRICS,),
                read_request=None,
                diagnosis=None,
                bounded_rationale="The visible evidence is insufficient.",
            )
            return ProviderTurnV21(
                function_name="scripted_plan",
                tool_call_id="scripted-1",
                raw_response_sha256=semantic_sha256("scripted-plan"),
                usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                monotonic_latency_ms=1,
                plan_decision=plan,
            )
        diagnosis = DtaDiagnosisV21(
            schema_version="dta-v21.diagnosis.v1",
            run_id=context.run_id,
            terminal=TerminalV21.ABSTAIN,
            root_service=None,
            root_entity_ref=None,
            fault_domain=None,
            mechanism=None,
            confidence=None,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
            evidence_source_types=(),
            uncertainties=("The visible evidence is insufficient.",),
            summary="The bounded evaluation entry abstained.",
        )
        return ProviderTurnV21(
            function_name="scripted_diagnosis",
            tool_call_id="scripted-1",
            raw_response_sha256=semantic_sha256("scripted-diagnosis"),
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            monotonic_latency_ms=1,
            diagnosis=diagnosis,
        )

    def action_selection_turn(self, *, diagnosis, resolved_evidence, candidate_view):
        raise AssertionError("abstention must not reach Action Selection")


class _TransportFailureProvider(_AlwaysAbstainProvider):
    def investigation_turn(self, *, context, visible_state, read_tools_enabled):
        del context, visible_state, read_tools_enabled
        self.attempted_calls += 1
        raise ConnectionError("synthetic external transport failure")


class _ProtocolFailureProvider(_AlwaysAbstainProvider):
    def investigation_turn(self, *, context, visible_state, read_tools_enabled):
        del context, visible_state, read_tools_enabled
        self.attempted_calls += 1
        raise ValueError("synthetic external protocol failure")


def test_execution_seals_exact_24_without_semantic_unblinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack, freeze, schedule, preregistration, seal = _frozen_pack(tmp_path)
    provider_env = tmp_path / "provider.env"
    provider_env.write_text(
        "ECOMSRE_LLM_BASE_URL=https://example.com/v1\n"
        "ECOMSRE_LLM_API_KEY=test-key\n"
        f"ECOMSRE_LLM_MODEL={MODEL}\n",
        encoding="utf-8",
    )
    provider_env.chmod(0o600)
    original = evaluation_contracts_module.EvaluatorCaseTruthV21.model_validate_json
    monkeypatch.setattr(
        evaluation_contracts_module.EvaluatorCaseTruthV21,
        "model_validate_json",
        lambda *_args, **_kwargs: pytest.fail("truth semantics were unblinded"),
    )
    execution_root = tmp_path / "execution"
    authoritative_claim = tmp_path / "claims" / f"{seal.seal_sha256}.json"
    execution = execute_held_out_evaluation_v21(
        repository_root=ROOT,
        provider_env_path=provider_env,
        held_out_pack_root=pack,
        private_execution_root=execution_root,
        execution_id="b" * 32,
        execution_code_head="c" * 40,
        freeze_manifest=freeze,
        schedule=schedule,
        preregistration=preregistration,
        held_out_pack_seal=seal,
        git_runner=_GitRunner("c" * 40),
        provider_factory=lambda arm, config: (
            _AlwaysAbstainProvider(arm)
            if isinstance(config, OpenAICompatibleConfig)
            else pytest.fail("invalid Provider config")
        ),
        created_at=START,
        authoritative_claim_path=authoritative_claim,
    )
    monkeypatch.setattr(
        evaluation_contracts_module.EvaluatorCaseTruthV21,
        "model_validate_json",
        original,
    )

    assert execution.entry_count == 24
    assert len(execution.entries) == 24
    assert (
        verify_held_out_execution_seal_v21(
            private_execution_root=execution_root,
            held_out_pack_seal=seal,
            schedule=schedule,
            authoritative_claim_path=authoritative_claim,
        )
        == execution
    )
    assert not list(execution_root.rglob("score.json"))
    with pytest.raises(FileExistsError, match="already claimed"):
        execute_held_out_evaluation_v21(
            repository_root=ROOT,
            provider_env_path=provider_env,
            held_out_pack_root=pack,
            private_execution_root=execution_root,
            execution_id="b" * 32,
            execution_code_head="c" * 40,
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            held_out_pack_seal=seal,
            git_runner=_GitRunner("c" * 40),
            provider_factory=lambda arm, config: _AlwaysAbstainProvider(arm),
            created_at=START,
            authoritative_claim_path=authoritative_claim,
        )
    with pytest.raises(FileExistsError, match="already claimed"):
        execute_held_out_evaluation_v21(
            repository_root=ROOT,
            provider_env_path=provider_env,
            held_out_pack_root=pack,
            private_execution_root=tmp_path / "alternate-execution",
            execution_id="9" * 32,
            execution_code_head="c" * 40,
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            held_out_pack_seal=seal,
            git_runner=_GitRunner("c" * 40),
            provider_factory=lambda arm, config: _AlwaysAbstainProvider(arm),
            created_at=START + timedelta(minutes=1),
            authoritative_claim_path=authoritative_claim,
        )

    entry_claim = next(execution_root.glob("entries/*/entry-claim.json"))
    entry_claim_bytes = entry_claim.read_bytes()
    entry_claim.unlink()
    with pytest.raises(ValueError, match="entry tree differs"):
        verify_held_out_execution_seal_v21(
            private_execution_root=execution_root,
            held_out_pack_seal=seal,
            schedule=schedule,
            authoritative_claim_path=authoritative_claim,
        )
    entry_claim.write_bytes(entry_claim_bytes)
    entry_claim.chmod(0o600)
    unexpected = entry_claim.parent / "unexpected.json"
    unexpected.write_text("{}\n", encoding="utf-8")
    unexpected.chmod(0o600)
    with pytest.raises(ValueError, match="entry tree differs"):
        verify_held_out_execution_seal_v21(
            private_execution_root=execution_root,
            held_out_pack_seal=seal,
            schedule=schedule,
            authoritative_claim_path=authoritative_claim,
        )


@pytest.mark.parametrize(
    ("provider_type", "failure_code", "private_message"),
    (
        (
            _TransportFailureProvider,
            "PROVIDER_TRANSPORT_FAILURE",
            "synthetic external transport failure",
        ),
        (
            _ProtocolFailureProvider,
            "PROVIDER_PROTOCOL_FAILURE",
            "synthetic external protocol failure",
        ),
    ),
)
def test_provider_failures_are_typed_sealed_and_cannot_be_rerun(
    tmp_path: Path,
    provider_type,
    failure_code: str,
    private_message: str,
) -> None:
    pack, freeze, schedule, preregistration, seal = _frozen_pack(tmp_path)
    provider_env = tmp_path / "provider.env"
    provider_env.write_text(
        "ECOMSRE_LLM_BASE_URL=https://example.com/v1\n"
        "ECOMSRE_LLM_API_KEY=test-key\n"
        f"ECOMSRE_LLM_MODEL={MODEL}\n",
        encoding="utf-8",
    )
    provider_env.chmod(0o600)
    execution_root = tmp_path / "execution"
    authoritative_claim = tmp_path / "claims" / f"{seal.seal_sha256}.json"
    arguments = {
        "repository_root": ROOT,
        "provider_env_path": provider_env,
        "held_out_pack_root": pack,
        "private_execution_root": execution_root,
        "execution_id": "f" * 32,
        "execution_code_head": "1" * 40,
        "freeze_manifest": freeze,
        "schedule": schedule,
        "preregistration": preregistration,
        "held_out_pack_seal": seal,
        "git_runner": _GitRunner("1" * 40),
        "provider_factory": lambda arm, config: provider_type(arm),
        "created_at": START,
        "authoritative_claim_path": authoritative_claim,
    }
    execution = execute_held_out_evaluation_v21(**arguments)

    assert execution.entry_count == 24
    agent_results = [
        path.read_text(encoding="utf-8")
        for path in execution_root.glob("entries/*/agent-result.json")
    ]
    assert len(agent_results) == 24
    assert all('"terminal":"FAILED"' in item for item in agent_results)
    assert all(f'"failure_code":"{failure_code}"' in item for item in agent_results)
    assert all(private_message not in item for item in agent_results)
    with pytest.raises(FileExistsError, match="already claimed"):
        execute_held_out_evaluation_v21(**arguments)


@pytest.mark.parametrize(
    ("runner", "message"),
    (
        (_GitRunner("3" * 40), "code HEAD differs"),
        (_GitRunner("2" * 40, status=" M tracked.py\n"), "worktree is not clean"),
    ),
)
def test_execution_rejects_head_mismatch_and_dirty_worktree_before_claim(
    tmp_path: Path,
    runner: _GitRunner,
    message: str,
) -> None:
    pack, freeze, schedule, preregistration, seal = _frozen_pack(tmp_path)
    provider_env = tmp_path / "provider.env"
    provider_env.write_text(
        "ECOMSRE_LLM_BASE_URL=https://example.com/v1\n"
        "ECOMSRE_LLM_API_KEY=test-key\n"
        f"ECOMSRE_LLM_MODEL={MODEL}\n",
        encoding="utf-8",
    )
    provider_env.chmod(0o600)
    authoritative_claim = tmp_path / "claims" / f"{seal.seal_sha256}.json"
    with pytest.raises(ValueError, match=message):
        execute_held_out_evaluation_v21(
            repository_root=ROOT,
            provider_env_path=provider_env,
            held_out_pack_root=pack,
            private_execution_root=tmp_path / "execution",
            execution_id="8" * 32,
            execution_code_head="2" * 40,
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            held_out_pack_seal=seal,
            git_runner=runner,
            provider_factory=lambda arm, config: _AlwaysAbstainProvider(arm),
            created_at=START,
            authoritative_claim_path=authoritative_claim,
        )
    assert not authoritative_claim.exists()


def test_unblinding_scores_once_and_publishes_bounded_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack, freeze, schedule, preregistration, seal = _frozen_pack(tmp_path)
    provider_env = tmp_path / "provider.env"
    provider_env.write_text(
        "ECOMSRE_LLM_BASE_URL=https://example.com/v1\n"
        "ECOMSRE_LLM_API_KEY=test-key\n"
        f"ECOMSRE_LLM_MODEL={MODEL}\n",
        encoding="utf-8",
    )
    provider_env.chmod(0o600)
    execution_root = tmp_path / "execution"
    authoritative_claim = tmp_path / "claims" / f"{seal.seal_sha256}.json"
    execution = execute_held_out_evaluation_v21(
        repository_root=ROOT,
        provider_env_path=provider_env,
        held_out_pack_root=pack,
        private_execution_root=execution_root,
        execution_id="d" * 32,
        execution_code_head="e" * 40,
        freeze_manifest=freeze,
        schedule=schedule,
        preregistration=preregistration,
        held_out_pack_seal=seal,
        git_runner=_GitRunner("e" * 40),
        provider_factory=lambda arm, config: _AlwaysAbstainProvider(arm),
        created_at=START,
        authoritative_claim_path=authoritative_claim,
    )
    public_root = tmp_path / "public"
    unblinding_root = tmp_path / "unblinding"
    original = evaluation_contracts_module.EvaluatorCaseTruthV21.model_validate_json

    def _after_receipt_only(*args, **kwargs):
        assert (unblinding_root / "unblinding-receipt.json").is_file()
        return original(*args, **kwargs)

    monkeypatch.setattr(
        evaluation_contracts_module.EvaluatorCaseTruthV21,
        "model_validate_json",
        _after_receipt_only,
    )
    report, disposition = score_held_out_evaluation_v21(
        repository_root=ROOT,
        held_out_pack_root=pack,
        private_execution_root=execution_root,
        private_unblinding_root=unblinding_root,
        freeze_manifest=freeze,
        schedule=schedule,
        preregistration=preregistration,
        held_out_pack_seal=seal,
        execution_seal=execution,
        public_evaluation_json=public_root / "dta-v21-evaluation.json",
        public_evaluation_markdown=public_root / "dta-v21-evaluation.md",
        public_disposition_path=public_root / "current-disposition.json",
        unblinded_at=START + timedelta(minutes=1),
        authoritative_claim_path=authoritative_claim,
    )
    monkeypatch.setattr(
        evaluation_contracts_module.EvaluatorCaseTruthV21,
        "model_validate_json",
        original,
    )

    assert report.scored_entry_count == 24
    assert report.held_out_case_count == 8
    assert disposition.terminal == "DTA_V21_PR_E_HELD_OUT_COMPLETED"
    assert disposition.claim == ("DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED")
    assert len(list(unblinding_root.rglob("entry-result.json"))) == 24
    published = (public_root / "dta-v21-evaluation.json").read_text(encoding="utf-8")
    assert "test-key" not in published
    assert str(pack) not in published
    freeze_path = tmp_path / "freeze.json"
    preregistration_path = tmp_path / "preregistration.json"
    progress_path = tmp_path / "progress.json"
    write_private_json(freeze_path, freeze, create_once=True)
    write_private_json(preregistration_path, preregistration, create_once=True)
    progress_path.write_text(
        "{"
        f'"development_report_sha256":"{"0" * 64}",'
        f'"held_out_seal_sha256":"{seal.seal_sha256}",'
        f'"held_out_execution_id":"{report.execution_id}",'
        f'"held_out_claim":"{report.exact_claim}"'
        "}\n",
        encoding="utf-8",
    )
    verification = verify_public_held_out_report_v21(
        public_evaluation_json=public_root / "dta-v21-evaluation.json",
        public_evaluation_markdown=public_root / "dta-v21-evaluation.md",
        public_disposition_path=public_root / "current-disposition.json",
        freeze_manifest_path=freeze_path,
        preregistration_path=preregistration_path,
        master_progress_path=progress_path,
    )
    assert verification["status"] == "DTA_V21_HELD_OUT_REPORT_VERIFIED"
    assert (
        verify_private_held_out_evaluation_v21(
            repository_root=ROOT,
            held_out_pack_root=pack,
            private_execution_root=execution_root,
            private_unblinding_root=unblinding_root,
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            held_out_pack_seal=seal,
            public_report=report,
            authoritative_claim_path=authoritative_claim,
        ).report_sha256
        == report.evaluation.report_sha256
    )
    with pytest.raises(FileExistsError, match="already unblinded"):
        score_held_out_evaluation_v21(
            repository_root=ROOT,
            held_out_pack_root=pack,
            private_execution_root=execution_root,
            private_unblinding_root=unblinding_root,
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            held_out_pack_seal=seal,
            execution_seal=execution,
            public_evaluation_json=public_root / "again.json",
            public_evaluation_markdown=public_root / "again.md",
            public_disposition_path=public_root / "again-disposition.json",
            unblinded_at=START + timedelta(minutes=2),
            authoritative_claim_path=authoritative_claim,
        )
