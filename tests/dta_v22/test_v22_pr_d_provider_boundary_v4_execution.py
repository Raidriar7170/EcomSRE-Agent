from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.controller_modes import ProviderOutputModeV22
from ecomsre.dta_v2.v22.controller_provider import ProviderHttpErrorV22
from ecomsre.dta_v2.v22.protocol_suite_v4 import run_protocol_replicate_v4
from scripts.dta_v22 import run_pr_d_provider_boundary_v4 as runner
from scripts.dta_v22.run_pr_d_provider_boundary_v4 import (
    _campaign,
    _write_once,
)


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


def test_campaign_requires_both_independent_passes_and_exact_selected_mode_calls() -> (
    None
):
    values = (_binding("A", "PASS"), _binding("B", "PASS"))
    passing = _campaign(
        commit="a" * 40,
        tree="b" * 40,
        manifest_sha256="c" * 64,
        probe_binding={
            "supported": True,
            "provider_calls": 1,
            "selected_mode": "STRICT_STRUCTURED_OUTPUT",
        },
        replicate_bindings=values,
        observed_provider_calls=49,
        selected_mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
    )
    assert passing["terminal"] == "DTA_V22_PR_D_CONTROLLER_READY"
    assert passing["merge_ready"] is True

    wrong_calls = _campaign(
        commit="a" * 40,
        tree="b" * 40,
        manifest_sha256="c" * 64,
        probe_binding={
            "supported": True,
            "provider_calls": 1,
            "selected_mode": "STRICT_STRUCTURED_OUTPUT",
        },
        replicate_bindings=values,
        observed_provider_calls=50,
        selected_mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
    )
    assert wrong_calls["terminal"] == "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"

    local = _campaign(
        commit="a" * 40,
        tree="b" * 40,
        manifest_sha256="c" * 64,
        probe_binding={
            "supported": True,
            "provider_calls": 2,
            "selected_mode": "LOCAL_FAIL_CLOSED_JSON",
        },
        replicate_bindings=values,
        observed_provider_calls=50,
        selected_mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
    )
    assert local["terminal"] == "DTA_V22_PR_D_CONTROLLER_READY"
    assert local["expected_provider_calls_for_complete_campaign"] == 50


def test_semantic_failure_in_a_does_not_remove_b_from_campaign() -> None:
    campaign = _campaign(
        commit="a" * 40,
        tree="b" * 40,
        manifest_sha256="c" * 64,
        probe_binding={
            "supported": True,
            "provider_calls": 1,
            "selected_mode": "STRICT_STRUCTURED_OUTPUT",
        },
        replicate_bindings=(
            _binding("A", "BLOCKED"),
            _binding("B", "PASS"),
        ),
        observed_provider_calls=49,
        selected_mode=ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
    )
    assert campaign["completed_replicate_count"] == 2
    assert campaign["terminal"] == "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    assert campaign["replacement_replicate_count"] == 0
    assert campaign["third_v3_replicate_count"] == 0


def test_every_evidence_write_is_create_once_and_read_back_verified(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.json"
    raw, semantic = _write_once(path, {"status": "negative"}, mode=0o600)
    assert len(raw) == len(semantic) == 64
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        _write_once(path, {"status": "replacement"}, mode=0o600)


def _main_args(root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        repository_root=root,
        provider_env=root / "provider.env",
        implementation_commit="a" * 40,
        implementation_tree="b" * 40,
    )


def _admit_main_without_git(
    monkeypatch: pytest.MonkeyPatch,
    *,
    root: Path,
    private_root: Path,
) -> None:
    monkeypatch.setattr(runner, "_parse_args", lambda: _main_args(root))
    monkeypatch.setattr(runner, "_PRIVATE_ROOT", private_root)
    monkeypatch.setattr(
        runner,
        "_PUBLIC",
        {
            "A": Path("replicate-a.json"),
            "B": Path("replicate-b.json"),
            "campaign": Path("campaign.json"),
        },
    )
    monkeypatch.setattr(runner, "_verify_exact_execution_tree", lambda **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "load_and_verify_manifest_v4",
        lambda _root: {
            "manifest_sha256": "c" * 64,
            "minimum_request_start_interval_seconds": 12.0,
        },
    )
    monkeypatch.setattr(
        runner,
        "verify_pre_execution_admission_v4",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "verify_private_execution_v4",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "_prepare_private_root",
        lambda path, _root: path.mkdir(parents=True),
    )


def test_all_local_env_validation_precedes_create_once_private_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    private_root = tmp_path / "private" / "v4"
    _admit_main_without_git(monkeypatch, root=root, private_root=private_root)
    monkeypatch.setattr(
        runner,
        "_parse_provider_env",
        lambda _path: (_ for _ in ()).throw(ValueError("bad env")),
    )
    with pytest.raises(ValueError, match="bad env"):
        runner.main()
    assert not private_root.exists()
    source = inspect.getsource(runner.main)
    assert source.index("_parse_provider_env") < source.index("_prepare_private_root")


def test_private_root_accepts_only_the_exact_goal_suffix(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    private_root = (
        tmp_path
        / ".ecomsre"
        / "private"
        / "dta-v22-p0-master-v1"
        / "pr-d"
        / "provider-boundary-v4"
    )
    runner._prepare_private_root(private_root, repository)
    assert private_root.is_dir()
    assert private_root.stat().st_mode & 0o777 == 0o700

    near_miss = private_root.parent / "provider-boundary-v4-copy"
    with pytest.raises(ValueError, match="private evidence root"):
        runner._prepare_private_root(near_miss, repository)


def test_probe_http_429_persists_probe_and_campaign_before_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    private_root = tmp_path / "private" / "v4"
    _admit_main_without_git(monkeypatch, root=root, private_root=private_root)
    monkeypatch.setattr(
        runner,
        "_parse_provider_env",
        lambda _path: {
            "ECOMSRE_LLM_BASE_URL": "https://provider.invalid/v1",
            "ECOMSRE_LLM_API_KEY": "fixture-value",
            "ECOMSRE_LLM_MODEL": "gpt-5.4-mini-2026-03-17",
        },
    )

    class RateLimitedProvider:
        def __init__(self, **_kwargs) -> None:
            self.attempted_calls = 0

        def probe(self, *, request):
            self.attempted_calls += 1
            raise ProviderHttpErrorV22(
                status=429,
                code="rate_limit_exceeded",
                error_type="server_error",
                param=None,
            )

    monkeypatch.setattr(
        runner, "OpenAICompatibleProviderBoundaryV4", RateLimitedProvider
    )
    writes: list[str] = []
    original_write = runner._write_once

    def recording_write(path: Path, value: object, *, mode: int):
        writes.append(path.name)
        return original_write(path, value, mode=mode)

    monkeypatch.setattr(runner, "_write_once", recording_write)
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"):
        runner.main()
    assert writes == [
        "manifest-binding.json",
        "provider-mode-probe.json",
        "campaign.json",
        "campaign.json",
    ]
    private_probe = json.loads(
        (private_root / "provider-mode-probe.json").read_text(encoding="utf-8")
    )
    public_campaign = json.loads((root / "campaign.json").read_text(encoding="utf-8"))
    assert private_probe["failure_class"] == "PROVIDER_TRANSPORT_ABORT"
    assert private_probe["safe_failure_code"] == "HTTP_RATE_LIMITED"
    assert public_campaign["terminal"] == "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"


def test_executable_schedule_has_one_probe_two_replicates_and_no_retry_loop() -> None:
    runner_source = inspect.getsource(runner.main)
    replicate_source = inspect.getsource(run_protocol_replicate_v4)
    assert 'enumerate(("A", "B"))' in runner_source
    assert runner_source.count("provider.probe(") == 1
    assert "while " not in runner_source
    assert "while " not in replicate_source
    assert replicate_source.count("turn = complete(") == 1
    assert runner_source.count("verify_private_execution_v4(") == 2
    assert runner_source.rindex("verify_private_execution_v4(") < runner_source.index(
        "print("
    )
    assert runner_source.rindex("_verify_exact_execution_tree(") > runner_source.index(
        "public_payload = _persist_campaign("
    )
    campaign_source = inspect.getsource(runner._campaign)
    for frozen_zero in (
        '"http_auto_retry_count": 0',
        '"semantic_retry_count": 0',
        '"replacement_replicate_count": 0',
        '"third_v3_replicate_count": 0',
    ):
        assert frozen_zero in campaign_source
