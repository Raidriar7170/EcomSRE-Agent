from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.controller_modes import PRIMARY_MODEL_V22
from scripts.dta_v22.run_pr_d_provider_protocol import _parse_provider_env


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
