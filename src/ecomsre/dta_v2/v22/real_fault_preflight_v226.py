"""Fail-closed static admission for the single DTA v2.2.6 live campaign."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v21.live_contracts import load_live_demo_config_v21
from ecomsre.dta_v2.v21.live_protocol import (
    load_ad_cpu_resource_recovery_protocol_v1,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    require_provider_payload_opaque_v225,
)
from ecomsre.dta_v2.v22.real_fault_manifest_v226 import (
    RealFaultAliasMapSetV226,
    RealFaultPreLiveFreezeV226,
)
from ecomsre.dta_v2.v22.real_fault_preflight_v225 import (
    GitCommandRunnerV225,
    run_read_only_git_v225,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226,
)


PROVIDER_GATE_ITERATION_SHA256_V226 = (
    "d0ca56b5b6d03faf8135fc7d5dca1568ef911935c6d2655902b1716749e9dbec"
)

_OUTPUTS = (
    "config/dta-v226-real-fault/alias-maps.json",
    "config/dta-v226-real-fault/cases.json",
    "config/dta-v226-real-fault/truth.json",
    "config/dta-v226-real-fault/manifest.json",
    "config/dta-v226-real-fault/captures/baseline-map-a.json",
    "config/dta-v226-real-fault/captures/baseline-map-b.json",
    "config/dta-v226-real-fault/captures/fault-map-a.json",
    "config/dta-v226-real-fault/captures/fault-map-b.json",
    "docs/results/dta-v226-real-fault-comparison.json",
    "docs/results/dta-v226-real-fault-comparison.md",
    "docs/results/dta-v226-real-fault-error-analysis.md",
    "docs/results/dta-v226-real-fault-interview-brief.md",
)


def sha256_file_v226(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RealFaultStaticPreflightV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.static-preflight.v1"]
    status: Literal["DTA_V226_REAL_FAULT_STATIC_PREFLIGHT_PASS"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch: Literal["codex/dta-v226-real-fault-transfer-repair"]
    worktree_clean: Literal[True]
    starting_main_is_ancestor: Literal[True]
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    live_config_loaded: Literal[True]
    ad_protocol_loaded: Literal[True]
    provider_old_capture_gate_passed: Literal[True]
    pre_live_review_passed: Literal[True]
    prompt_lint_passed: Literal[True]
    new_aliases_verified: Literal[True]
    outputs_absent: Literal[True]
    checked_at: datetime
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_preflight(self) -> RealFaultStaticPreflightV226:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("v2.2.6 preflight timestamp lacks timezone")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("v2.2.6 preflight digest differs")
        return self


def _verify_provider_gate_v226(
    *, repository_root: Path, private_iteration_path: Path, manifest: RealFaultPreLiveFreezeV226
) -> None:
    summary_path = (
        repository_root / "docs/analysis/dta-v226-provider-development.json"
    )
    if sha256_file_v226(summary_path) != manifest.provider_development_summary_sha256:
        raise ValueError("v2.2.6 Provider development summary differs")
    summary = json.loads(summary_path.read_bytes())
    if summary.get("status") != "DTA_V226_REAL_PROVIDER_OLD_CAPTURE_GATE_PASS":
        raise ValueError("v2.2.6 old-capture Provider gate did not pass")
    iterations = summary.get("iterations")
    if not isinstance(iterations, list) or not iterations:
        raise ValueError("v2.2.6 Provider iteration binding is absent")
    if iterations[-1].get("report_sha256") != PROVIDER_GATE_ITERATION_SHA256_V226:
        raise ValueError("v2.2.6 public Provider iteration binding differs")
    if private_iteration_path.is_symlink() or not private_iteration_path.is_file():
        raise ValueError("v2.2.6 private Provider gate evidence is absent")
    if sha256_file_v226(private_iteration_path) != PROVIDER_GATE_ITERATION_SHA256_V226:
        raise ValueError("v2.2.6 private Provider gate bytes differ")
    private = json.loads(private_iteration_path.read_bytes())
    private_summary = private.get("summary")
    if (
        private.get("status") != "DTA_V226_REAL_PROVIDER_OLD_CAPTURE_GATE_PASS"
        or not isinstance(private_summary, dict)
        or private_summary.get("valid_terminals") != 8
        or private_summary.get("protocol_failures") != 0
        or private_summary.get("runner_failures") != 0
        or private_summary.get("transport_failures") != 0
    ):
        raise ValueError("v2.2.6 private Provider gate is not protocol-valid")


def run_static_preflight_v226(
    *,
    repository_root: Path,
    provider_env_path: Path,
    private_provider_gate_path: Path,
    manifest: RealFaultPreLiveFreezeV226,
    alias_maps: RealFaultAliasMapSetV226,
    git_runner: GitCommandRunnerV225,
) -> RealFaultStaticPreflightV226:
    root = repository_root.resolve(strict=True)
    head = run_read_only_git_v225(git_runner, "rev-parse", "HEAD")
    if head != manifest.code_head:
        raise ValueError("v2.2.6 manifest code HEAD differs")
    if (
        run_read_only_git_v225(git_runner, "branch", "--show-current")
        != "codex/dta-v226-real-fault-transfer-repair"
    ):
        raise ValueError("v2.2.6 execution branch differs")
    if run_read_only_git_v225(
        git_runner, "status", "--porcelain=v1", "--untracked-files=all"
    ):
        raise ValueError("v2.2.6 live campaign requires an exactly clean worktree")
    run_read_only_git_v225(
        git_runner,
        "merge-base",
        "--is-ancestor",
        "1c6520d706481f37b63a5b14c1fe8554b52d530b",
        "HEAD",
    )
    provider = load_private_provider_env(provider_env_path)
    if provider["ECOMSRE_LLM_MODEL"] != manifest.provider_model:
        raise ValueError("v2.2.6 Provider model differs from the freeze")
    config = load_live_demo_config_v21(
        root / "config/dta-v21/live/live-demo.v1.json"
    )
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    if (
        config.provider_model != manifest.provider_model
        or config.protocol_sha256 != protocol.protocol_sha256
    ):
        raise ValueError("v2.2.6 reused v2.1 lifecycle bindings differ")
    if alias_maps.set_sha256 != manifest.alias_map_set_sha256:
        raise ValueError("v2.2.6 alias maps differ from the freeze")
    prompt_sha = hashlib.sha256(
        REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226.encode("utf-8")
    ).hexdigest()
    if prompt_sha != manifest.selection_prompt_sha256:
        raise ValueError("v2.2.6 selection Prompt differs from the freeze")
    for relative, expected in (
        (
            "src/ecomsre/dta_v2/v22/real_fault_terminalizer_v226.py",
            manifest.terminalizer_sha256,
        ),
        (
            "src/ecomsre/dta_v2/v22/real_fault_scorer_v226.py",
            manifest.scorer_sha256,
        ),
    ):
        if sha256_file_v226(root / relative) != expected:
            raise ValueError(f"v2.2.6 frozen implementation differs: {relative}")
    review_path = root / "docs/external-reviews/dta-v226-real-fault-pre-live-review.md"
    if sha256_file_v226(review_path) != manifest.pre_live_review_sha256:
        raise ValueError("v2.2.6 pre-live review bytes differ")
    review = review_path.read_text(encoding="utf-8")
    if "Must Fix:\n0" not in review or "Claim Accuracy:\nPASS" not in review:
        raise ValueError("v2.2.6 pre-live review did not pass")
    _verify_provider_gate_v226(
        repository_root=root,
        private_iteration_path=private_provider_gate_path,
        manifest=manifest,
    )
    require_provider_payload_opaque_v225(
        {
            "selection_prompt": REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226,
            "candidate_aliases": alias_maps.aliases,
        }
    )
    if any((root / relative).exists() for relative in _OUTPUTS):
        raise FileExistsError("v2.2.6 write-once output already exists")
    payload = {
        "schema_version": "dta-v226-real-fault.static-preflight.v1",
        "status": "DTA_V226_REAL_FAULT_STATIC_PREFLIGHT_PASS",
        "code_head": head,
        "branch": "codex/dta-v226-real-fault-transfer-repair",
        "worktree_clean": True,
        "starting_main_is_ancestor": True,
        "provider_model": provider["ECOMSRE_LLM_MODEL"],
        "live_config_loaded": True,
        "ad_protocol_loaded": True,
        "provider_old_capture_gate_passed": True,
        "pre_live_review_passed": True,
        "prompt_lint_passed": True,
        "new_aliases_verified": True,
        "outputs_absent": True,
        "checked_at": datetime.now(timezone.utc),
    }
    draft = cast(Any, RealFaultStaticPreflightV226).model_construct(
        **payload, preflight_sha256="0" * 64
    )
    return RealFaultStaticPreflightV226.model_validate(
        {
            **payload,
            "preflight_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"preflight_sha256"})
            ),
        }
    )


__all__ = (
    "PROVIDER_GATE_ITERATION_SHA256_V226",
    "RealFaultStaticPreflightV226",
    "run_static_preflight_v226",
    "sha256_file_v226",
)
