"""Fail-closed static and live-baseline preflight for the real-fault study."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.read_tools import ReadBackend, ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    HealthState,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
)
from ecomsre.dta_v2.v21.live_contracts import load_live_demo_config_v21
from ecomsre.dta_v2.v21.live_protocol import (
    load_ad_cpu_resource_recovery_protocol_v1,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    require_provider_payload_opaque_v225,
)
from ecomsre.dta_v2.v22.real_fault_flat_arm_v225 import (
    REAL_FAULT_FLAT_SYSTEM_PROMPT_V225,
)
from ecomsre.dta_v2.v22.real_fault_study_v225 import (
    RealFaultAliasMapSetV1,
    RealFaultPreLiveFreezeV1,
)


_OUTPUTS = (
    "config/dta-v225-real-fault/alias-maps.json",
    "config/dta-v225-real-fault/cases.json",
    "config/dta-v225-real-fault/truth.json",
    "config/dta-v225-real-fault/manifest.json",
    "config/dta-v225-real-fault/captures/baseline-map-a.json",
    "config/dta-v225-real-fault/captures/baseline-map-b.json",
    "config/dta-v225-real-fault/captures/fault-map-a.json",
    "config/dta-v225-real-fault/captures/fault-map-b.json",
    "docs/results/dta-v225-real-fault-shadow-comparison.json",
    "docs/results/dta-v225-real-fault-shadow-comparison.md",
    "docs/results/dta-v225-real-fault-shadow-error-analysis.md",
    "docs/results/dta-v225-real-fault-shadow-interview-brief.md",
)


class NoHealthyComparatorV225(RuntimeError):
    pass


class GitCommandResultV225(Protocol):
    exit_code: int
    stdout: str


class GitCommandRunnerV225(Protocol):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> GitCommandResultV225: ...


def run_read_only_git_v225(
    runner: GitCommandRunnerV225, *args: str
) -> str:
    result = runner.run(("git", *args), timeout_seconds=30)
    if result.exit_code != 0:
        raise RuntimeError("audited read-only git command failed")
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RealFaultStaticPreflightV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.static-preflight.v1"]
    status: Literal["DTA_V225_REAL_FAULT_STATIC_PREFLIGHT_PASS"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch: Literal["codex/dta-v225-real-fault-shadow"]
    worktree_clean: Literal[True]
    origin_main_is_ancestor: Literal[True]
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    live_config_loaded: Literal[True]
    ad_protocol_loaded: Literal[True]
    prompt_lint_passed: Literal[True]
    outputs_absent: Literal[True]
    checked_at: datetime
    preflight_sha256: str

    @model_validator(mode="after")
    def require_preflight(self) -> RealFaultStaticPreflightV1:
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("real-fault preflight timestamp lacks UTC")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("real-fault preflight digest differs")
        return self


def run_static_preflight_v225(
    *,
    repository_root: Path,
    provider_env_path: Path,
    manifest: RealFaultPreLiveFreezeV1,
    alias_maps: RealFaultAliasMapSetV1,
    git_runner: GitCommandRunnerV225,
) -> RealFaultStaticPreflightV1:
    root = repository_root.resolve(strict=True)
    head = run_read_only_git_v225(git_runner, "rev-parse", "HEAD")
    if head != manifest.code_head:
        raise ValueError("real-fault manifest code head differs from current HEAD")
    if (
        run_read_only_git_v225(git_runner, "branch", "--show-current")
        != "codex/dta-v225-real-fault-shadow"
    ):
        raise ValueError("real-fault preflight branch differs")
    if run_read_only_git_v225(
        git_runner, "status", "--porcelain=v1", "--untracked-files=all"
    ):
        raise ValueError("real-fault preflight requires an exactly clean worktree")
    run_read_only_git_v225(
        git_runner, "merge-base", "--is-ancestor", "origin/main", "HEAD"
    )
    provider = load_private_provider_env(provider_env_path)
    if provider["ECOMSRE_LLM_MODEL"] != manifest.provider_model:
        raise ValueError("real-fault Provider model differs from manifest")
    config = load_live_demo_config_v21(
        root / "config/dta-v21/live/live-demo.v1.json"
    )
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    if config.provider_model != manifest.provider_model or config.protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("real-fault v2.1 live bindings differ")
    if alias_maps.set_sha256 != manifest.alias_map_set_sha256:
        raise ValueError("real-fault alias maps differ from manifest")
    if hashlib.sha256(REAL_FAULT_FLAT_SYSTEM_PROMPT_V225.encode()).hexdigest() != manifest.flat_prompt_sha256:
        raise ValueError("real-fault Flat prompt differs from manifest")
    if hashlib.sha256(SHARED_SELECTION_SYSTEM_PROMPT_V225.encode()).hexdigest() != manifest.current_prompt_sha256:
        raise ValueError("real-fault current prompt differs from manifest")
    scorer = root / "src/ecomsre/dta_v2/v22/real_fault_shadow_scorer_v225.py"
    if _sha256(scorer) != manifest.scorer_sha256:
        raise ValueError("real-fault scorer differs from manifest")
    require_provider_payload_opaque_v225(
        {
            "flat_prompt": REAL_FAULT_FLAT_SYSTEM_PROMPT_V225,
            "current_prompt": SHARED_SELECTION_SYSTEM_PROMPT_V225,
            "candidate_aliases": tuple(
                item.alias for item in alias_maps.maps[0].bindings
            ),
        }
    )
    if any((root / item).exists() for item in _OUTPUTS):
        raise FileExistsError("real-fault write-once output already exists")
    payload = {
        "schema_version": "dta-v225-real-fault.static-preflight.v1",
        "status": "DTA_V225_REAL_FAULT_STATIC_PREFLIGHT_PASS",
        "code_head": head,
        "branch": "codex/dta-v225-real-fault-shadow",
        "worktree_clean": True,
        "origin_main_is_ancestor": True,
        "provider_model": provider["ECOMSRE_LLM_MODEL"],
        "live_config_loaded": True,
        "ad_protocol_loaded": True,
        "prompt_lint_passed": True,
        "outputs_absent": True,
        "checked_at": datetime.now(timezone.utc),
    }
    draft = cast(Any, RealFaultStaticPreflightV1).model_construct(
        **payload, preflight_sha256="0" * 64
    )
    return RealFaultStaticPreflightV1.model_validate(
        {
            **payload,
            "preflight_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"preflight_sha256"})
            ),
        }
    )


def select_healthy_comparator_v225(*, backend: ReadBackend, run_id: str) -> str:
    """Select Recommendation first, then the first healthy lexicographic fallback."""

    for comparator in ("recommendation", "email", "product-catalog"):
        services = tuple(sorted(("ad", comparator)))
        try:
            runtime_result = backend.execute(
                build_inspect_service_runtime_request(
                    run_id=run_id, services=services, max_results=2
                )
            )
            resource_result = backend.execute(
                build_inspect_resource_usage_request(
                    run_id=run_id,
                    services=services,
                    sampling_window_seconds=10,
                    sample_count=5,
                )
            )
        except ReadBackendFailure:
            continue
        runtimes = tuple(
            item for item in runtime_result.records if isinstance(item, RuntimeRecord)
        )
        resources = tuple(
            item
            for item in resource_result.records
            if isinstance(item, ResourceUsageRecord)
        )
        if (
            {item.logical_service for item in runtimes} != set(services)
            or {item.logical_service for item in resources} != set(services)
            or any(
                item.state is not RuntimeState.RUNNING
                or item.health not in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED}
                for item in runtimes
            )
            or any(
                len(item.samples) != 5
                or max(sample.cpu_percent for sample in item.samples) >= 80.0
                or not all(
                    math.isfinite(sample.cpu_percent)
                    and math.isfinite(float(sample.memory_bytes))
                    for sample in item.samples
                )
                or not math.isfinite(item.memory_slope_bytes_per_second)
                for item in resources
            )
        ):
            continue
        return comparator
    raise NoHealthyComparatorV225(
        "no comparator satisfies the target-complete baseline preflight"
    )


__all__ = (
    "RealFaultStaticPreflightV1",
    "NoHealthyComparatorV225",
    "run_static_preflight_v225",
    "select_healthy_comparator_v225",
)
