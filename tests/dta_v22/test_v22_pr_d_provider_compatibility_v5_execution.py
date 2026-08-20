from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.controller_modes import ProviderOutputModeV22
from ecomsre.dta_v2.v22.protocol_suite_v5 import run_protocol_replicate_v5
from scripts.dta_v22 import run_pr_d_provider_compatibility_v5 as runner


def _binding(replicate_id: str, terminal: str) -> dict[str, str]:
    return {
        "replicate_id": replicate_id,
        "report_sha256": "1" * 64,
        "terminal": terminal,
        "private_raw_sha256": "2" * 64,
        "private_semantic_sha256": "3" * 64,
        "public_raw_sha256": "4" * 64,
        "public_semantic_sha256": "5" * 64,
    }


def _probe_binding() -> dict[str, object]:
    return {
        "provider_calls": 1,
        "supported": True,
        "selected_mode": "LOCAL_FAIL_CLOSED_JSON",
    }


def test_campaign_requires_exactly_one_probe_and_two_independent_passes() -> None:
    campaign = runner._campaign(
        commit="a" * 40,
        tree="b" * 40,
        manifest_sha256="c" * 64,
        probe_binding=_probe_binding(),
        replicate_bindings=(_binding("A", "PASS"), _binding("B", "PASS")),
        observed_provider_calls=49,
        selected_mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
    )
    assert campaign["terminal"] == "DTA_V22_PR_D_CONTROLLER_READY"
    assert campaign["expected_provider_calls_for_complete_campaign"] == 49

    wrong_calls = runner._campaign(
        commit="a" * 40,
        tree="b" * 40,
        manifest_sha256="c" * 64,
        probe_binding=_probe_binding(),
        replicate_bindings=(_binding("A", "PASS"), _binding("B", "PASS")),
        observed_provider_calls=50,
        selected_mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
    )
    assert wrong_calls["terminal"] == "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"


def test_semantic_block_in_a_does_not_remove_b() -> None:
    campaign = runner._campaign(
        commit="a" * 40,
        tree="b" * 40,
        manifest_sha256="c" * 64,
        probe_binding=_probe_binding(),
        replicate_bindings=(_binding("A", "BLOCKED"), _binding("B", "PASS")),
        observed_provider_calls=49,
        selected_mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
    )
    assert campaign["completed_replicate_count"] == 2
    assert campaign["terminal"] == "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    assert campaign["replacement_replicate_count"] == 0


def test_public_probe_is_a_bounded_projection_without_raw_decisions() -> None:
    private = {
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
        "manifest_sha256": "c" * 64,
        "supported": True,
        "provider_calls": 1,
        "selected_mode": "LOCAL_FAIL_CLOSED_JSON",
        "failure_class": None,
        "safe_failure": None,
        "probe_report": {
            "provider_request_sha256": "d" * 64,
            "static_schema_sha256": "e" * 64,
            "prompt_sha256": "f" * 64,
            "report_sha256": "0" * 64,
            "attempts": [{"failure": None}],
            "turn": {
                "alias_decision": {"decision": "ABSTAIN"},
                "canonical_decision": {"decision": "ABSTAIN"},
            },
        },
    }
    public = runner._public_probe(
        private_probe=private,
        private_raw_sha256="1" * 64,
        private_semantic_sha256="2" * 64,
        manifest_binding_raw_sha256="3" * 64,
        manifest_binding_semantic_sha256="4" * 64,
        executed_at="2026-08-20T00:00:00+00:00",
    )
    text = json.dumps(public, sort_keys=True)
    assert "turn" not in public
    assert "probe_report" not in public
    assert "alias_decision" not in text
    assert "canonical_decision" not in text


def test_create_once_writer_and_private_root_authority(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    runner._write_once(path, {"status": "negative"}, mode=0o600)
    with pytest.raises(FileExistsError):
        runner._write_once(path, {"status": "replacement"}, mode=0o600)

    private = (
        tmp_path
        / ".ecomsre"
        / "private"
        / "dta-v22-p0-master-v1"
        / "pr-d"
        / "provider-compatibility-v5"
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    runner._prepare_private_root(private, repository)
    assert private.stat().st_mode & 0o777 == 0o700

    escaped = tmp_path / "escaped"
    escaped.mkdir()
    linked_home = tmp_path / "linked-home"
    linked_home.mkdir()
    (linked_home / ".ecomsre").symlink_to(escaped, target_is_directory=True)
    linked_private = (
        linked_home
        / ".ecomsre"
        / "private"
        / "dta-v22-p0-master-v1"
        / "pr-d"
        / "provider-compatibility-v5"
    )
    with pytest.raises(ValueError, match="symlink"):
        runner._prepare_private_root(linked_private, repository)


def test_executable_schedule_has_one_probe_two_replicates_and_zero_retry() -> None:
    main_source = inspect.getsource(runner.main)
    replicate_source = inspect.getsource(run_protocol_replicate_v5)
    assert 'enumerate(("A", "B"))' in main_source
    assert main_source.count("provider.probe(") == 1
    assert "while " not in main_source
    assert "while " not in replicate_source
    assert replicate_source.count("turn = complete(") == 1
    assert "provider.complete(request=request, mode=" not in main_source
    assert main_source.index("_parse_provider_env") < main_source.index(
        "_prepare_private_root"
    )
    assert main_source.index("_persist_probe(") < main_source.index(
        "verify_persisted_probe_stage_v5("
    ) < main_source.index('enumerate(("A", "B"))')
    assert main_source.index("_persist_replicate(") < main_source.index(
        "verify_persisted_replicate_stage_v5("
    )


def test_probe_local_abort_persists_private_and_public_negative_before_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    private = tmp_path / "private" / "provider-compatibility-v5"
    monkeypatch.setattr(
        runner,
        "_parse_args",
        lambda: argparse.Namespace(
            repository_root=root,
            provider_env=root / "provider.env",
            implementation_commit="a" * 40,
            implementation_tree="b" * 40,
        ),
    )
    monkeypatch.setattr(runner, "_PRIVATE_ROOT", private)
    monkeypatch.setattr(
        runner,
        "_PUBLIC",
        {
            "probe": Path("probe.json"),
            "A": Path("replicate-a.json"),
            "B": Path("replicate-b.json"),
            "campaign": Path("campaign.json"),
        },
    )
    monkeypatch.setattr(runner, "_verify_exact_execution_tree", lambda **_kw: None)
    monkeypatch.setattr(
        runner,
        "load_and_verify_manifest_v5",
        lambda _root: {
            "manifest_sha256": "c" * 64,
            "minimum_request_start_interval_seconds": 12.0,
        },
    )
    monkeypatch.setattr(runner, "verify_pre_execution_admission_v5", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "verify_private_execution_v5", lambda **_kw: None)
    monkeypatch.setattr(runner, "verify_persisted_probe_stage_v5", lambda **_kw: None)
    monkeypatch.setattr(
        runner, "verify_persisted_replicate_stage_v5", lambda **_kw: None
    )
    monkeypatch.setattr(
        runner,
        "_parse_provider_env",
        lambda _path: {
            "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
            "ECOMSRE_LLM_API_KEY": "fixture-value",
            "ECOMSRE_LLM_MODEL": "gpt-5.4-mini-2026-03-17",
        },
    )
    monkeypatch.setattr(
        runner,
        "_prepare_private_root",
        lambda path, _root: path.mkdir(parents=True),
    )

    class LocalAbortProvider:
        def __init__(self, **_kwargs: object) -> None:
            self.attempted_calls = 0

        def payload(self, *, request: object) -> dict[str, object]:
            return {"request": type(request).__name__}

        def probe(self, *, request: object) -> None:
            del request
            self.attempted_calls += 1
            raise ValueError("Provider-controlled message must not persist")

    monkeypatch.setattr(runner, "OpenAICompatibleProviderBoundaryV5", LocalAbortProvider)
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"):
        runner.main()
    assert (private / "manifest-binding.json").exists()
    assert (private / "local-mode-probe.json").exists()
    assert (private / "campaign.json").exists()
    assert (root / "probe.json").exists()
    assert (root / "campaign.json").exists()
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (private / "local-mode-probe.json", root / "probe.json")
    )
    assert "Provider-controlled message" not in persisted
