"""Final assurance-closed lifecycle for live E2E v6."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, cast

from ecomsre_live_sandbox.contracts import (
    HumanApprovalRecord,
    canonical_json_bytes,
)
import ecomsre_live_sandbox.e2e_v3 as e2e_v3
import ecomsre_live_sandbox.e2e_v4 as e2e_v4
from ecomsre_live_sandbox.e2e_v5 import _collect_v5_no_fault_evidence
from ecomsre_live_sandbox.e2e_v6_contracts import E2EV6Config, E2EV6PrivateRoots
from ecomsre_live_sandbox.environment import SandboxEnvironment
from ecomsre_live_sandbox.invocation_b_assurance import (
    build_expected_public_result,
    verify_public_result,
)


def run_development_probe(
    config: E2EV6Config,
    roots: E2EV6PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    kwargs.setdefault("evidence_collector", _collect_v5_no_fault_evidence)
    return e2e_v4.run_development_probe(cast(Any, config), cast(Any, roots), **kwargs)


def run_canonical_invocation_a(
    config: E2EV6Config,
    roots: E2EV6PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    kwargs.setdefault("evidence_collector", _collect_v5_no_fault_evidence)
    return e2e_v4.run_canonical_invocation_a(
        cast(Any, config), cast(Any, roots), **kwargs
    )


def record_human_approval_for_invocation_b(
    config: E2EV6Config,
    roots: E2EV6PrivateRoots,
    *,
    approver: str,
    phrase: str,
) -> HumanApprovalRecord:
    return e2e_v4.record_human_approval_for_invocation_b(
        cast(Any, config),
        cast(Any, roots),
        approver=approver,
        phrase=phrase,
    )


def _write_new_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_public_outputs_v6(
    config: E2EV6Config,
    terminal: Mapping[str, object],
    *,
    sealed_terminal_path: Path,
) -> tuple[str, str, str]:
    if sealed_terminal_path.is_symlink() or not sealed_terminal_path.is_file():
        raise ValueError("v6 public projection requires the sealed private terminal")
    sealed = json.loads(sealed_terminal_path.read_text(encoding="utf-8"))
    if not isinstance(sealed, Mapping) or dict(sealed) != dict(terminal):
        raise ValueError("v6 supplied terminal differs from the sealed private terminal")
    public = build_expected_public_result(config, sealed)
    verify_public_result(config, public, sealed)
    paths = (
        config.repository_root / config.reporting.public_result_json,
        config.repository_root / config.reporting.public_result_markdown,
        config.repository_root / config.reporting.public_human_brief,
    )
    _write_new_public(paths[0], canonical_json_bytes(public))
    _write_new_public(
        paths[1],
        (
            "# Live Fault to A0 Controlled Remediation E2E v6\n\n"
            f"**Verdict:** `{public['verdict']}`\n\n"
            "Deterministically projected from the sealed private terminal for one "
            "preregistered local Sandbox scenario using a human-preauthorized "
            "frozen runbook; not production or autonomous remediation.\n"
        ).encode("utf-8"),
    )
    _write_new_public(
        paths[2],
        (
            "# Live Fault → A0 → Controlled Remediation v6 — Human Brief\n\n"
            "本结果由 sealed private terminal 确定性投影，仅代表一个本地 Sandbox、"
            "一个预注册场景和人工预授权的冻结修复 runbook；不构成生产自治或 "
            "Multi-Agent 优越性声明。\n"
        ).encode("utf-8"),
    )
    return cast(
        tuple[str, str, str],
        tuple(path.relative_to(config.repository_root).as_posix() for path in paths),
    )


def run_invocation_b(
    config: E2EV6Config,
    roots: E2EV6PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    kwargs.setdefault("environment_factory", SandboxEnvironment)
    kwargs.setdefault(
        "public_writer",
        lambda current_config, terminal: _write_public_outputs_v6(
            cast(E2EV6Config, current_config),
            terminal,
            sealed_terminal_path=roots.invocation_b / "terminal.json",
        ),
    )
    return e2e_v3.run_invocation_b(cast(Any, config), cast(Any, roots), **kwargs)


__all__ = [
    "build_expected_public_result",
    "record_human_approval_for_invocation_b",
    "run_canonical_invocation_a",
    "run_development_probe",
    "run_invocation_b",
    "verify_public_result",
]
