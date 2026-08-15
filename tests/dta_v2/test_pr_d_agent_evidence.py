from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.agent import run_tool_using_agent
from ecomsre.dta_v2.agent_contracts import ProviderUsage, build_alert_context
from ecomsre.dta_v2.agent_evidence import (
    load_agent_run,
    persist_agent_run,
)
from ecomsre.dta_v2.agent_provider import ProviderTurn, build_provider_identity
from ecomsre.dta_v2.contracts import DtaDiagnosis, Terminal, semantic_sha256
from ecomsre.dta_v2.read_tools import FakeReadBackend
from ecomsre.dta_v2.registry import (
    load_runbook_registry,
    load_scenario_registry,
)


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "7" * 32


class TerminalProvider:
    def __init__(
        self,
        *,
        raw_response: dict[str, object] | None = None,
        raw_argument_overrides: dict[str, object] | None = None,
    ) -> None:
        self.identity = build_provider_identity("gpt-5.4-mini-2026-03-17")
        self.attempted_calls = 0
        self.raw_response = raw_response or {"id": "private-turn-1"}
        self.raw_argument_overrides = raw_argument_overrides or {}

    def investigation_turn(self, *, context, transcript, read_tools_enabled):
        del transcript, read_tools_enabled
        self.attempted_calls += 1
        diagnosis = DtaDiagnosis(
            schema_version="dta-v2.diagnosis.v1",
            run_id=context.run_id,
            terminal=Terminal.NEED_MORE_EVIDENCE,
            root_service=None,
            root_entity_ref=None,
            fault_domain=None,
            mechanism=None,
            confidence=0.2,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
            evidence_source_types=(),
            uncertainties=("One additional independent source is required.",),
            summary="The bounded evidence is insufficient.",
        )
        return ProviderTurn(
            function_name="submit_dta_diagnosis",
            tool_call_id="call-1",
            raw_response=self.raw_response,
            raw_response_sha256=semantic_sha256(self.raw_response),
            raw_arguments={
                **diagnosis.model_dump(mode="json"),
                **self.raw_argument_overrides,
            },
            usage=ProviderUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            monotonic_latency_ms=2,
            diagnosis=diagnosis,
        )

    def action_selection_turn(self, *, diagnosis, candidate_view):
        del diagnosis, candidate_view
        raise AssertionError("noncompleted diagnosis reached Action Selection")


def _result(provider: TerminalProvider | None = None):
    scenario = load_scenario_registry(
        ROOT / "config/dta-v2/scenarios/agent-visible"
    ).scenarios[0]
    context = build_alert_context(
        scenario=scenario,
        run_id=RUN_ID,
        started_at=datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc)
        + timedelta(minutes=5),
    )
    return run_tool_using_agent(
        context=context,
        backend=FakeReadBackend.healthy(),
        registry=load_runbook_registry(ROOT / "config/dta-v2/runbooks"),
        provider=provider or TerminalProvider(),
    )


def test_private_agent_evidence_is_create_once_complete_and_exact_mode(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private" / "pr-d" / "attempt-1"
    result = _result()
    manifest = persist_agent_run(private, result, forbidden_secrets=("never-store",))

    assert manifest.result_sha256 == result.result_sha256
    assert manifest.missing_artifacts == (
        "final/action-proposal.json",
        "final/candidate-set.json",
        "final/candidate-view.json",
        "final/resolved-evidence.json",
    )
    expected = {
        "identity.json",
        "turns/0001/raw-response.json",
        "turns/0001/tool-call-arguments.json",
        "turns/0001/parsed-diagnosis.json",
        "turns/0001/usage-latency.json",
        "final/diagnosis.json",
        "final/evidence-store.json",
        "agent-run-result.json",
        "manifest.json",
    }
    assert expected.issubset(
        {path.relative_to(private).as_posix() for path in private.rglob("*.json")}
    )
    for path in (private, *private.rglob("*")):
        if path.is_dir():
            assert path.stat().st_mode & 0o777 == 0o700
        else:
            assert path.stat().st_mode & 0o777 == 0o600
    assert load_agent_run(private) == result
    assert persist_agent_run(private, result).manifest_sha256 == manifest.manifest_sha256


def test_private_agent_evidence_rejects_symlink_overwrite_secret_and_cot(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        persist_agent_run(link, _result())

    private = tmp_path / "private"
    result = _result()
    persist_agent_run(private, result)
    changed = _result(TerminalProvider(raw_response={"id": "changed"}))
    with pytest.raises(FileExistsError, match="create-once"):
        persist_agent_run(private, changed)

    with pytest.raises(ValueError, match="forbidden secret"):
        persist_agent_run(
            tmp_path / "secret",
            _result(TerminalProvider(raw_response={"id": "never-store"})),
            forbidden_secrets=("never-store",),
        )
    with pytest.raises(ValueError, match="forbidden secret"):
        persist_agent_run(
            tmp_path / "fragmented-secret",
            _result(
                TerminalProvider(
                    raw_response={"id": "turn", "echo": ["never-", "store"]}
                )
            ),
            forbidden_secrets=("never-store",),
        )
    with pytest.raises(ValueError, match="forbidden secret"):
        persist_agent_run(
            tmp_path / "key-value-fragmented-secret",
            _result(
                TerminalProvider(
                    raw_response={"private-provider-": "test-secret"}
                )
            ),
            forbidden_secrets=("private-provider-test-secret",),
        )
    with pytest.raises(ValueError, match="private reasoning"):
        persist_agent_run(
            tmp_path / "cot",
            _result(
                TerminalProvider(
                    raw_response={"id": "turn", "analysis": "hidden detail"}
                )
            ),
        )
    with pytest.raises(ValueError, match="private reasoning"):
        persist_agent_run(
            tmp_path / "decoded-arguments-cot",
            _result(
                TerminalProvider(
                    raw_argument_overrides={
                        "reasoning_content": "hidden detail"
                    }
                )
            ),
        )
    with pytest.raises(ValueError, match="private configuration"):
        persist_agent_run(
            tmp_path / "decoded-arguments-config",
            _result(
                TerminalProvider(
                    raw_argument_overrides={
                        "headers": {"Authorization": "redacted"}
                    }
                )
            ),
        )
    with pytest.raises(ValueError, match="forbidden secret"):
        persist_agent_run(
            tmp_path / "cross-field-fragmented-secret",
            _result(
                TerminalProvider(
                    raw_argument_overrides={
                        "uncertainties": ["private-provider-"],
                        "summary": "test-secret",
                    }
                )
            ),
            forbidden_secrets=("private-provider-test-secret",),
        )


def test_private_agent_evidence_rejects_preexisting_weak_directory(
    tmp_path: Path,
) -> None:
    weak = tmp_path / "weak"
    weak.mkdir(mode=0o755)
    weak.chmod(0o755)
    with pytest.raises(PermissionError, match="0700"):
        persist_agent_run(weak, _result())
