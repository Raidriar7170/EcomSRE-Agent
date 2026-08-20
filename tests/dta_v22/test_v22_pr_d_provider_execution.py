from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.controller_modes import PRIMARY_MODEL_V22
from scripts.dta_v22.run_pr_d_provider_protocol import (
    _FORMAL_HTTP_AUTO_RETRY_COUNT,
    _FORMAL_INTER_REPLICATE_COOLDOWN_SECONDS,
    _FORMAL_MIN_REQUEST_INTERVAL_SECONDS,
    _FORMAL_REPLICATE_IDS,
    _PRIVATE_EVIDENCE_ROOT,
    _PUBLIC_CAMPAIGN_SUMMARY_RELATIVE,
    _PUBLIC_REPLICATE_SUMMARY_RELATIVES,
    _execute_fixed_schedule,
    _parse_provider_env,
    _persist_replicate_artifacts,
    _probe_failure_receipt,
    _validate_campaign_paths,
    _verify_frozen_execution_identity,
    main as provider_campaign_main,
)


def _write_env(path: Path, *, model: str = PRIMARY_MODEL_V22) -> None:
    path.write_text(
        "\n".join(
            (
                "ECOMSRE_LLM_BASE_URL=https://provider.example/v1",
                "ECOMSRE_LLM_API_KEY=private-test-value",
                f"ECOMSRE_LLM_MODEL={model}",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_provider_execution_env_is_exact_private_and_model_continuous(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider.env"
    _write_env(path)
    values = _parse_provider_env(path)
    assert set(values) == {
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    }
    assert values["ECOMSRE_LLM_MODEL"] == PRIMARY_MODEL_V22

    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        _parse_provider_env(path)


def test_provider_execution_rejects_model_swap_and_shell_expansion(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider.env"
    _write_env(path, model="gpt-silent-successor")
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_MODEL_CONTINUITY"):
        _parse_provider_env(path)

    path.write_text(
        "\n".join(
            (
                "ECOMSRE_LLM_BASE_URL=https://provider.example/v1",
                "ECOMSRE_LLM_API_KEY=$(unsupported-command)",
                f"ECOMSRE_LLM_MODEL={PRIMARY_MODEL_V22}",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    with pytest.raises(ValueError, match="shell expansion"):
        _parse_provider_env(path)


def test_provider_outputs_require_exact_private_root_and_public_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    public_parent = root / "docs" / "analysis"
    public_parent.mkdir(parents=True)
    private_root = (
        tmp_path
        / ".ecomsre"
        / "private"
        / "dta-v22-p0-master-v1"
        / "pr-d"
        / "provider-protocol-v3"
    )
    private_paths = {
        name: private_root / name
        for name in (
            "provider-mode-probe.json",
            "replicate-a.json",
            "replicate-b.json",
            "campaign.json",
        )
    }
    public_paths = {
        "A": root / _PUBLIC_REPLICATE_SUMMARY_RELATIVES["A"],
        "B": root / _PUBLIC_REPLICATE_SUMMARY_RELATIVES["B"],
        "campaign": root / _PUBLIC_CAMPAIGN_SUMMARY_RELATIVE,
    }
    assert _validate_campaign_paths(
        repository_root=root,
        private_root=private_root,
    ) == (private_paths, public_paths)

    with pytest.raises(ValueError, match="exact Goal root"):
        _validate_campaign_paths(
            repository_root=root,
            private_root=tmp_path / "old-private-root",
        )


def test_formal_provider_execution_uses_conservative_fixed_pacing() -> None:
    assert _FORMAL_MIN_REQUEST_INTERVAL_SECONDS == 4.0
    assert _FORMAL_INTER_REPLICATE_COOLDOWN_SECONDS == 60.0
    assert _FORMAL_HTTP_AUTO_RETRY_COUNT == 0
    assert _FORMAL_REPLICATE_IDS == ("A", "B")
    source = inspect.getsource(provider_campaign_main)
    assert source.index("_verify_frozen_execution_identity(") < source.index(
        "probe_provider_output_mode_v22("
    )


@pytest.mark.parametrize("first_passes", [False, True])
def test_fixed_schedule_persists_each_result_before_terminal_and_never_runs_third(
    first_passes: bool,
) -> None:
    events: list[str] = []

    def execute(replicate_id: str) -> dict[str, object]:
        events.append(f"execute-{replicate_id}")
        return {
            "replicate_id": replicate_id,
            "provider_gate_eligible": first_passes if replicate_id == "A" else True,
        }

    def persist(value: dict[str, object]) -> dict[str, object]:
        replicate_id = str(value["replicate_id"])
        events.append(f"persist-{replicate_id}")
        return {"replicate_id": replicate_id, "verified": True}

    def cooldown(seconds: float) -> None:
        events.append(f"cooldown-{seconds:.1f}")

    def build(
        outcomes: tuple[dict[str, object], dict[str, object]],
        bindings: tuple[dict[str, object], dict[str, object]],
    ) -> dict[str, object]:
        events.append("build-campaign")
        return {
            "terminal": (
                "DTA_V22_PR_D_CONTROLLER_READY"
                if all(bool(item["provider_gate_eligible"]) for item in outcomes)
                else "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
            ),
            "bindings": bindings,
        }

    def persist_campaign(value: dict[str, object]) -> dict[str, object]:
        events.append("persist-campaign")
        return value

    campaign = _execute_fixed_schedule(
        execute_replicate=execute,
        persist_replicate=persist,
        cooldown=cooldown,
        build_campaign=build,
        persist_campaign=persist_campaign,
    )

    assert events == [
        "execute-A",
        "persist-A",
        "cooldown-60.0",
        "execute-B",
        "persist-B",
        "build-campaign",
        "persist-campaign",
    ]
    assert campaign["terminal"] == (
        "DTA_V22_PR_D_CONTROLLER_READY"
        if first_passes
        else "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    )


def test_private_root_is_exact_goal_root() -> None:
    assert _PRIVATE_EVIDENCE_ROOT.parts[-5:] == (
        ".ecomsre",
        "private",
        "dta-v22-p0-master-v1",
        "pr-d",
        "provider-protocol-v3",
    )


def test_negative_replicate_is_private_verified_then_publicly_bound(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "replicate-a.json"
    public_path = tmp_path / "replicate-a-summary.json"
    receipt = _probe_failure_receipt(
        replicate_id="A",
        implementation_commit="a" * 40,
        implementation_tree="b" * 40,
        preregistration_sha256="c" * 64,
    )

    binding = _persist_replicate_artifacts(
        outcome=receipt,
        private_path=private_path,
        public_path=public_path,
        probe_evidence_sha256="d" * 64,
    )

    assert binding["verified"] is True
    private = json.loads(private_path.read_text(encoding="utf-8"))
    public_text = public_path.read_text(encoding="utf-8")
    public = json.loads(public_text)
    assert public["private_evidence_raw_sha256"] == binding["private_raw_sha256"]
    assert public["private_evidence_semantic_sha256"] == private["evidence_sha256"]
    assert public["provider_gate_eligible"] is False
    assert public["terminal"] == "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    assert "unpublished Provider transport detail" not in public_text
    assert str(tmp_path) not in public_text


def test_replicates_fail_closed_if_frozen_bytes_change_between_arms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = tmp_path / "frozen.py"
    attempt = tmp_path / "attempt.json"
    frozen.write_text("frozen-v1\n", encoding="utf-8")
    attempt.write_text("attempt-v1\n", encoding="utf-8")
    preregistration = {
        "preregistration_sha256": "c" * 64,
        "frozen_raw_sha256_by_path": {
            "frozen.py": hashlib.sha256(frozen.read_bytes()).hexdigest()
        },
        "historical_attempt_raw_sha256_by_path": {
            "attempt.json": hashlib.sha256(attempt.read_bytes()).hexdigest()
        },
    }
    monkeypatch.setattr(
        "scripts.dta_v22.run_pr_d_provider_protocol._git_text",
        lambda _root, *args: "a" * 40 if args[-1] == "HEAD" else "b" * 40,
    )
    monkeypatch.setattr(
        "scripts.dta_v22.run_pr_d_provider_protocol._load_preregistration",
        lambda _root: preregistration,
    )
    monkeypatch.setattr(
        "scripts.dta_v22.run_pr_d_provider_protocol._worktree_dirty_paths",
        lambda _root: set(),
    )

    _verify_frozen_execution_identity(
        root=tmp_path,
        implementation_commit="a" * 40,
        implementation_tree="b" * 40,
        preregistration_sha256="c" * 64,
    )
    frozen.write_text("changed-between-replicates\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"):
        _verify_frozen_execution_identity(
            root=tmp_path,
            implementation_commit="a" * 40,
            implementation_tree="b" * 40,
            preregistration_sha256="c" * 64,
        )

    frozen.write_text("frozen-v1\n", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.dta_v22.run_pr_d_provider_protocol._worktree_dirty_paths",
        lambda _root: {"undeclared-change.py"},
    )
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"):
        _verify_frozen_execution_identity(
            root=tmp_path,
            implementation_commit="a" * 40,
            implementation_tree="b" * 40,
            preregistration_sha256="c" * 64,
        )
