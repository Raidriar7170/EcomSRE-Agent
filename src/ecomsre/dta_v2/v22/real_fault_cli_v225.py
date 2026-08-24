"""One-campaign, write-once CLI for the v2.2.5 real-fault shadow study."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Literal, Protocol, cast

from pydantic import model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.read_tools import ReadBackend
from ecomsre.dta_v2.v21.live_contracts import (
    LiveFaultImpactEvidenceV21,
    LiveScenarioV21,
    load_live_demo_config_v21,
)
from ecomsre.dta_v2.v21.live_owned import OwnedLiveAttemptV21
from ecomsre.dta_v2.v21.live_protocol import (
    load_ad_cpu_resource_recovery_protocol_v1,
)
from ecomsre.dta_v2.v21.live_runner import LiveExecutionLeaseV21
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V225,
)
from ecomsre.dta_v2.v22.opaque_identity_v225 import (
    generate_opaque_identity_plan_v225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.real_fault_bundle_arm_v225 import (
    run_current_runtime_bundle_live_v225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultCapturePairV1,
    RealFaultCaseKind,
    RealFaultOpaqueCaptureV1,
    RealFaultPhysicalCaptureV1,
    build_alias_maps_v225,
    build_capture_pair_v225,
    build_opaque_capture_v225,
    truth_root_alias_v225,
)
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v225 import (
    RealFaultArmRun,
    RealFaultCaseTruthV1,
    RealFaultLiveShadowRun,
    RealFaultStudyExecutionV1,
)
from ecomsre.dta_v2.v22.real_fault_flat_arm_v225 import (
    REAL_FAULT_FLAT_SYSTEM_PROMPT_V225,
    RealFaultFlatProviderV225,
)
from ecomsre.dta_v2.v22.real_fault_live_v225 import RealFaultShadowLifecycleV1
from ecomsre.dta_v2.v22.real_fault_preflight_v225 import (
    RealFaultStaticPreflightV1,
    run_static_preflight_v225,
    select_healthy_comparator_v225,
)
from ecomsre.dta_v2.v22.real_fault_shadow_scorer_v225 import (
    RealFaultStudyScoreV1,
    score_real_fault_study_v225,
)
from ecomsre.dta_v2.v22.real_fault_study_v225 import (
    RealFaultAliasMapSetV1,
    RealFaultManifestV1,
    RealFaultPreLiveFreezeV1,
    build_alias_map_set_v225,
    build_case_set_v225,
    build_manifest_v225,
    build_pre_live_freeze_v225,
    build_public_alias_map_set_v225,
    build_truth_set_v225,
    execute_real_fault_study_v225,
)
from ecomsre.dta_v2.v22.selection_provider_v225 import SelectionProviderV225
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_live_sandbox.contracts import (
    CleanupResult,
    ensure_private_directory,
    write_private_json,
)


class LiveLifecycleV225(Protocol):
    @property
    def run_id(self) -> str: ...

    def admit_start_and_wait(self) -> None: ...

    def live_backend(self) -> ReadBackend: ...

    def capture_and_prove_baseline(
        self, *, code_head: str, preflight_sha256: str
    ) -> object: ...

    def capture_state(
        self,
        *,
        campaign_id: str,
        kind: RealFaultCaseKind,
        comparator_service: str,
    ) -> RealFaultPhysicalCaptureV1: ...

    def revalidate_before_fault(self) -> None: ...

    def inject_and_verify_fault(self) -> LiveFaultImpactEvidenceV21: ...

    def restore_and_cleanup(self) -> tuple[bool, dict[str, object]]: ...


@dataclass(frozen=True)
class PreparedLiveStudyV225:
    comparator_service: Literal["email", "product-catalog", "recommendation"]
    alias_maps: RealFaultAliasMapSetV1
    pre_live_freeze: RealFaultPreLiveFreezeV1
    preflight: RealFaultStaticPreflightV1


@dataclass(frozen=True)
class LiveCaptureOutcomeV225:
    prepared: PreparedLiveStudyV225
    baseline_physical: RealFaultPhysicalCaptureV1
    fault_physical: RealFaultPhysicalCaptureV1
    capture_pair: RealFaultCapturePairV1
    fault_impact: LiveFaultImpactEvidenceV21
    live_baseline: RealFaultLiveShadowRun
    live_fault: RealFaultLiveShadowRun
    baseline_restored: bool
    cleanup: CleanupResult


class RealFaultLiveSequenceError(RuntimeError):
    """Safe live terminal carrying restoration evidence but no raw backend text."""

    def __init__(
        self,
        *,
        stage: str,
        baseline_capture_exists: bool,
        fault_capture_exists: bool,
        provider_shadow_exists: bool,
        replacement_cause: Literal["LOCAL_ENVIRONMENT", "TELEMETRY", "NONE"],
        baseline_restored: bool,
        cleanup: CleanupResult | None,
    ) -> None:
        super().__init__("BLOCKED_DTA_V225_REAL_FAULT_ENVIRONMENT")
        self.stage = stage
        self.baseline_capture_exists = baseline_capture_exists
        self.fault_capture_exists = fault_capture_exists
        self.provider_shadow_exists = provider_shadow_exists
        self.replacement_cause = replacement_cause
        self.baseline_restored = baseline_restored
        self.cleanup = cleanup


PrepareCallbackV225 = Callable[
    [Literal["email", "product-catalog", "recommendation"]], PreparedLiveStudyV225
]
PhysicalObserverV225 = Callable[[RealFaultPhysicalCaptureV1], None]
CurrentProviderFactoryV225 = Callable[[], SelectionProviderV225]


def _replacement_cause_v225(
    *, stage: str, error: BaseException
) -> Literal["LOCAL_ENVIRONMENT", "TELEMETRY", "NONE"]:
    if not isinstance(error, Exception):
        return "NONE"
    if isinstance(
        error,
        (AssertionError, FileExistsError, PermissionError, TypeError, ValueError),
    ):
        return "NONE"
    if stage == "ADMISSION":
        return "LOCAL_ENVIRONMENT"
    if stage in {"COMPARATOR_SELECTION", "BASELINE_PROOF"}:
        return "TELEMETRY"
    return "NONE"


def _render_cases(
    *,
    baseline: RealFaultPhysicalCaptureV1,
    fault: RealFaultPhysicalCaptureV1,
    maps: RealFaultAliasMapSetV1,
) -> tuple[
    RealFaultOpaqueCaptureV1,
    RealFaultOpaqueCaptureV1,
    RealFaultOpaqueCaptureV1,
    RealFaultOpaqueCaptureV1,
]:
    map_a, map_b = maps.maps
    return (
        build_opaque_capture_v225(
            case_id="fault-map-a", physical_capture=fault, alias_map=map_a
        ),
        build_opaque_capture_v225(
            case_id="fault-map-b", physical_capture=fault, alias_map=map_b
        ),
        build_opaque_capture_v225(
            case_id="baseline-map-a", physical_capture=baseline, alias_map=map_a
        ),
        build_opaque_capture_v225(
            case_id="baseline-map-b", physical_capture=baseline, alias_map=map_b
        ),
    )


def run_live_capture_sequence_v225(
    *,
    lifecycle: LiveLifecycleV225,
    campaign_id: str,
    code_head: str,
    model_id: str,
    prepare: PrepareCallbackV225,
    current_provider_factory: CurrentProviderFactoryV225,
    physical_observer: PhysicalObserverV225,
) -> LiveCaptureOutcomeV225:
    """Acquire two states and two live shadows, then always restore and clean."""

    stage = "ADMISSION"
    prepared: PreparedLiveStudyV225 | None = None
    baseline_physical: RealFaultPhysicalCaptureV1 | None = None
    fault_physical: RealFaultPhysicalCaptureV1 | None = None
    fault_impact: LiveFaultImpactEvidenceV21 | None = None
    live_baseline: RealFaultLiveShadowRun | None = None
    live_fault: RealFaultLiveShadowRun | None = None
    primary_error: BaseException | None = None
    restored = False
    cleanup: CleanupResult | None = None
    try:
        lifecycle.admit_start_and_wait()
        stage = "COMPARATOR_SELECTION"
        comparator = cast(
            Literal["email", "product-catalog", "recommendation"],
            select_healthy_comparator_v225(
                backend=lifecycle.live_backend(), run_id=lifecycle.run_id
            ),
        )
        stage = "STATIC_PREFLIGHT"
        prepared = prepare(comparator)
        stage = "BASELINE_PROOF"
        lifecycle.capture_and_prove_baseline(
            code_head=code_head,
            preflight_sha256=prepared.preflight.preflight_sha256,
        )
        stage = "BASELINE_CAPTURE"
        baseline_physical = lifecycle.capture_state(
            campaign_id=campaign_id,
            kind=RealFaultCaseKind.BASELINE,
            comparator_service=comparator,
        )
        physical_observer(baseline_physical)
        baseline_a = build_opaque_capture_v225(
            case_id="baseline-map-a",
            physical_capture=baseline_physical,
            alias_map=prepared.alias_maps.maps[0],
        )
        stage = "LIVE_BASELINE_SHADOW"
        live_baseline = run_current_runtime_bundle_live_v225(
            capture=baseline_a,
            baseline_capture=baseline_a,
            alias_map=prepared.alias_maps.maps[0],
            live_backend=lifecycle.live_backend(),
            model_id=model_id,
            provider=current_provider_factory(),
        )
        lifecycle.revalidate_before_fault()
        stage = "FAULT_INJECTION"
        fault_impact = lifecycle.inject_and_verify_fault()
        stage = "FAULT_CAPTURE"
        fault_physical = lifecycle.capture_state(
            campaign_id=campaign_id,
            kind=RealFaultCaseKind.AD_CPU_FAULT,
            comparator_service=comparator,
        )
        physical_observer(fault_physical)
        fault_a = build_opaque_capture_v225(
            case_id="fault-map-a",
            physical_capture=fault_physical,
            alias_map=prepared.alias_maps.maps[0],
        )
        stage = "LIVE_FAULT_SHADOW"
        live_fault = run_current_runtime_bundle_live_v225(
            capture=fault_a,
            baseline_capture=baseline_a,
            alias_map=prepared.alias_maps.maps[0],
            live_backend=lifecycle.live_backend(),
            model_id=model_id,
            provider=current_provider_factory(),
        )
        stage = "RESTORE_AND_CLEANUP"
    except BaseException as error:
        primary_error = error
    finally:
        try:
            restored_value, cleanup_value = lifecycle.restore_and_cleanup()
            restored = restored_value
            cleanup = CleanupResult.model_validate(
                {
                    key: cleanup_value[key]
                    for key in (
                        "baseline_restored",
                        "owned_containers",
                        "owned_networks",
                        "owned_volumes",
                        "non_owned_resources_changed",
                        "verdict",
                    )
                }
            )
        except BaseException as cleanup_error:
            if primary_error is None:
                primary_error = cleanup_error
    if primary_error is not None or cleanup is None:
        replacement_cause = (
            "NONE"
            if primary_error is None
            else _replacement_cause_v225(stage=stage, error=primary_error)
        )
        raise RealFaultLiveSequenceError(
            stage=stage,
            baseline_capture_exists=baseline_physical is not None,
            fault_capture_exists=fault_physical is not None,
            provider_shadow_exists=live_baseline is not None or live_fault is not None,
            replacement_cause=replacement_cause,
            baseline_restored=restored,
            cleanup=cleanup,
        ) from primary_error
    if not restored or cleanup.verdict != "CLEAN" or cleanup.non_owned_resources_changed:
        raise RealFaultLiveSequenceError(
            stage="RESTORE_AND_CLEANUP",
            baseline_capture_exists=baseline_physical is not None,
            fault_capture_exists=fault_physical is not None,
            provider_shadow_exists=live_baseline is not None or live_fault is not None,
            replacement_cause="NONE",
            baseline_restored=restored,
            cleanup=cleanup,
        )
    if any(
        item is None
        for item in (
            prepared,
            baseline_physical,
            fault_physical,
            fault_impact,
            live_baseline,
            live_fault,
        )
    ):
        raise RuntimeError("real-fault successful lifecycle lacks required evidence")
    prepared = cast(PreparedLiveStudyV225, prepared)
    baseline_physical = cast(RealFaultPhysicalCaptureV1, baseline_physical)
    fault_physical = cast(RealFaultPhysicalCaptureV1, fault_physical)
    cases = _render_cases(
        baseline=baseline_physical,
        fault=fault_physical,
        maps=prepared.alias_maps,
    )
    return LiveCaptureOutcomeV225(
        prepared=prepared,
        baseline_physical=baseline_physical,
        fault_physical=fault_physical,
        capture_pair=build_capture_pair_v225(
            baseline=baseline_physical, fault=fault_physical, cases=cases
        ),
        fault_impact=cast(LiveFaultImpactEvidenceV21, fault_impact),
        live_baseline=cast(RealFaultLiveShadowRun, live_baseline),
        live_fault=cast(RealFaultLiveShadowRun, live_fault),
        baseline_restored=restored,
        cleanup=cleanup,
    )


class RealFaultStudyArtifactV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.study-artifact.v1"]
    recorded_at: datetime
    accepted_live_campaigns: Literal[1]
    shared_physical_captures: Literal[2]
    shared_capture_semantic_actions: Literal[16]
    shared_capture_target_equivalent_reads: Literal[20]
    manifest: RealFaultManifestV1
    preflight: RealFaultStaticPreflightV1
    capture_pair_sha256: str
    fault_impact_sha256: str
    live_baseline: RealFaultLiveShadowRun
    live_fault: RealFaultLiveShadowRun
    baseline_restored: Literal[True]
    cleanup: Literal["CLEAN"]
    non_owned_changes: Literal[0]
    execution: RealFaultStudyExecutionV1
    score: RealFaultStudyScoreV1
    execution_count: Literal[1]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]
    uncaught_exceptions: Literal[0]
    artifact_sha256: str

    @model_validator(mode="after")
    def require_artifact(self) -> RealFaultStudyArtifactV1:
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("real-fault artifact timestamp lacks timezone")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        expected = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        if self.artifact_sha256 != expected:
            raise ValueError("real-fault artifact digest differs")
        return self


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_once(path: Path, value: DtaModelV22 | dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        value.model_dump_json(indent=2)
        if isinstance(value, DtaModelV22)
        else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    )
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text + "\n")


def _truths(
    *, maps: RealFaultAliasMapSetV1
) -> tuple[
    RealFaultCaseTruthV1,
    RealFaultCaseTruthV1,
    RealFaultCaseTruthV1,
    RealFaultCaseTruthV1,
]:
    rows = []
    for case_id, alias_map in (
        ("fault-map-a", maps.maps[0]),
        ("fault-map-b", maps.maps[1]),
        ("baseline-map-a", maps.maps[0]),
        ("baseline-map-b", maps.maps[1]),
    ):
        fault = case_id.startswith("fault-")
        rows.append(
            RealFaultCaseTruthV1(
                schema_version="dta-v225-real-fault.case-truth.v1",
                case_id=case_id,
                case_kind="AD_CPU_FAULT" if fault else "BASELINE",
                expected_root_alias=(
                    truth_root_alias_v225(
                        alias_map=alias_map, kind=RealFaultCaseKind.AD_CPU_FAULT
                    )
                    if fault
                    else None
                ),
                expected_fault_domain="LOCAL_RESOURCE" if fault else None,
                expected_mechanism="CPU_SATURATION" if fault else None,
            )
        )
    return cast(tuple[RealFaultCaseTruthV1, RealFaultCaseTruthV1, RealFaultCaseTruthV1, RealFaultCaseTruthV1], tuple(rows))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DTA v2.2.5 real-fault study")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--lease-root", type=Path, required=True)
    parser.add_argument("--replacement", action="store_true")
    parser.add_argument("--minimum-request-interval", type=float, default=4.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def _claim_campaign_v225(*, private_root: Path, replacement: bool) -> str:
    primary = private_root / "campaign-0001"
    execution_claim = private_root / "final-execution-claim.json"
    if not replacement:
        if (private_root / "primary-campaign-claim.json").exists() or primary.exists():
            raise FileExistsError("real-fault primary campaign was already claimed")
        write_private_json(
            private_root / "primary-campaign-claim.json",
            {
                "schema_version": "dta-v225-real-fault.primary-campaign-claim.v1",
                "campaign_id": "campaign-0001",
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            },
            create_once=True,
        )
        return "campaign-0001"
    blocked_path = primary / "blocked-terminal.json"
    if not blocked_path.is_file() or execution_claim.exists():
        raise PermissionError("real-fault replacement lacks an eligible primary terminal")
    blocked = json.loads(blocked_path.read_text(encoding="utf-8"))
    cleanup = blocked.get("cleanup")
    eligible = (
        blocked.get("baseline_capture_exists") is False
        and blocked.get("fault_capture_exists") is False
        and blocked.get("provider_shadow_exists") is False
        and blocked.get("replacement_cause") in {"LOCAL_ENVIRONMENT", "TELEMETRY"}
        and blocked.get("stage")
        in {"ADMISSION", "COMPARATOR_SELECTION", "BASELINE_PROOF"}
        and blocked.get("baseline_restored") is True
        and isinstance(cleanup, dict)
        and cleanup.get("verdict") == "CLEAN"
        and cleanup.get("non_owned_resources_changed") is False
        and not (primary / "paired-runs.jsonl").exists()
    )
    if not eligible:
        raise PermissionError("real-fault primary terminal forbids replacement")
    replacement_root = private_root / "campaign-0002"
    if (
        (private_root / "replacement-campaign-claim.json").exists()
        or replacement_root.exists()
    ):
        raise FileExistsError("real-fault replacement campaign was already claimed")
    write_private_json(
        private_root / "replacement-campaign-claim.json",
        {
            "schema_version": "dta-v225-real-fault.replacement-campaign-claim.v1",
            "campaign_id": "campaign-0002",
            "primary_terminal_sha256": hashlib.sha256(blocked_path.read_bytes()).hexdigest(),
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )
    return "campaign-0002"


def _claim_final_execution_v225(
    *, private_root: Path, campaign_id: str, manifest: RealFaultManifestV1
) -> None:
    if campaign_id not in {"campaign-0001", "campaign-0002"}:
        raise ValueError("real-fault final execution campaign ID differs")
    write_private_json(
        private_root / "final-execution-claim.json",
        {
            "schema_version": "dta-v225-real-fault.final-execution-claim.v1",
            "campaign_id": campaign_id,
            "manifest_sha256": manifest.manifest_sha256,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "maximum_execution_count": 1,
        },
        create_once=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.repository_root.resolve(strict=True)
    private_root = args.private_root.resolve()
    ensure_private_directory(private_root)
    campaign_id = _claim_campaign_v225(
        private_root=private_root, replacement=args.replacement
    )
    campaign_root = private_root / campaign_id
    if campaign_root.exists() or campaign_root.is_symlink():
        raise FileExistsError("real-fault campaign is write-once")
    ensure_private_directory(campaign_root)
    head = _git(root, "rev-parse", "HEAD")
    provider_values = load_private_provider_env(args.provider_env)
    provider_config = OpenAICompatibleConfig(
        base_url=provider_values["ECOMSRE_LLM_BASE_URL"],
        api_key=provider_values["ECOMSRE_LLM_API_KEY"],
        model=provider_values["ECOMSRE_LLM_MODEL"],
    )
    config = load_live_demo_config_v21(root / "config/dta-v21/live/live-demo.v1.json")
    protocol = load_ad_cpu_resource_recovery_protocol_v1(
        root / "config/dta-v21/live/ad-cpu-resource-recovery.v1.json"
    )
    registry = load_default_runbook_registry(root)
    identity_plan = generate_opaque_identity_plan_v225(
        service_count=2, operation_count=0, change_count=0, pair_count=0
    )
    scorer_path = root / "src/ecomsre/dta_v2/v22/real_fault_shadow_scorer_v225.py"

    with LiveExecutionLeaseV21(args.lease_root) as lease:
        attempt = OwnedLiveAttemptV21(
            repository_root=root,
            private_root=campaign_root / "owned-sandbox",
            accepted_private_prf_root=private_root,
            attempt_id=f"dta-v225-real-fault-{campaign_id}",
            config=config,
            scenario=config.require_scenario(LiveScenarioV21.AD_CPU_SATURATION),
            registry=registry,
            protocol=protocol,
            provider_env_path=args.provider_env,
            concurrency_guard=lease.assert_exclusive,
        )
        lifecycle = RealFaultShadowLifecycleV1(attempt)

        def current_provider() -> SelectionProviderV225:
            return SelectionProviderV225(
                config=provider_config,
                minimum_request_interval_seconds=args.minimum_request_interval,
                timeout_seconds=args.timeout,
                max_completion_tokens=120,
                debug_root=campaign_root / "provider-debug",
            )

        def prepare(
            comparator: Literal["email", "product-catalog", "recommendation"],
        ) -> PreparedLiveStudyV225:
            map_a, map_b = build_alias_maps_v225(
                fault_service="ad",
                comparator_service=comparator,
                aliases=cast(tuple[str, str], identity_plan.services),
            )
            maps = build_alias_map_set_v225(map_a=map_a, map_b=map_b)
            pre_live_freeze = build_pre_live_freeze_v225(
                code_head=head,
                comparator_service=comparator,
                alias_map_set_sha256=maps.set_sha256,
                flat_prompt_sha256=hashlib.sha256(
                    REAL_FAULT_FLAT_SYSTEM_PROMPT_V225.encode()
                ).hexdigest(),
                current_prompt_sha256=hashlib.sha256(
                    SHARED_SELECTION_SYSTEM_PROMPT_V225.encode()
                ).hexdigest(),
                scorer_sha256=hashlib.sha256(scorer_path.read_bytes()).hexdigest(),
            )
            preflight = run_static_preflight_v225(
                repository_root=root,
                provider_env_path=args.provider_env,
                manifest=pre_live_freeze,
                alias_maps=maps,
            )
            write_private_json(
                campaign_root / "private-alias-maps.json",
                maps,
                create_once=True,
            )
            return PreparedLiveStudyV225(
                comparator_service=comparator,
                alias_maps=maps,
                pre_live_freeze=pre_live_freeze,
                preflight=preflight,
            )

        def observe_physical(capture: RealFaultPhysicalCaptureV1) -> None:
            label = "baseline" if capture.kind is RealFaultCaseKind.BASELINE else "fault"
            write_private_json(
                campaign_root / f"physical-{label}-capture.json",
                capture,
                create_once=True,
            )

        try:
            live = run_live_capture_sequence_v225(
                lifecycle=lifecycle,
                campaign_id=campaign_id,
                code_head=head,
                model_id=provider_config.model,
                prepare=prepare,
                current_provider_factory=current_provider,
                physical_observer=observe_physical,
            )
        except RealFaultLiveSequenceError as error:
            write_private_json(
                campaign_root / "blocked-terminal.json",
                {
                    "schema_version": "dta-v225-real-fault.blocked-terminal.v1",
                    "terminal": str(error),
                    "stage": error.stage,
                    "baseline_capture_exists": error.baseline_capture_exists,
                    "fault_capture_exists": error.fault_capture_exists,
                    "provider_shadow_exists": error.provider_shadow_exists,
                    "replacement_cause": error.replacement_cause,
                    "baseline_restored": error.baseline_restored,
                    "cleanup": (
                        None
                        if error.cleanup is None
                        else error.cleanup.model_dump(mode="json")
                    ),
                },
                create_once=True,
            )
            raise

    cases = live.capture_pair.cases
    captures = {item.case_id: item for item in cases}
    truths = _truths(maps=live.prepared.alias_maps)
    truth_by_case = {item.case_id: item for item in truths}
    case_set = build_case_set_v225(captures=cases)
    truth_set = build_truth_set_v225(truths=truths)
    manifest = build_manifest_v225(
        pre_live_freeze=live.prepared.pre_live_freeze,
        capture_pair_sha256=live.capture_pair.pair_sha256,
        case_set_sha256=case_set.case_set_sha256,
        truth_set_sha256=truth_set.truth_set_sha256,
    )
    write_private_json(
        campaign_root / "final-study-manifest.json", manifest, create_once=True
    )
    _claim_final_execution_v225(
        private_root=private_root, campaign_id=campaign_id, manifest=manifest
    )
    journal_path = campaign_root / "paired-runs.jsonl"
    journal_handle = journal_path.open("x", encoding="utf-8")

    def observe_run(ordinal: int, run: RealFaultArmRun) -> None:
        journal_handle.write(
            json.dumps(
                {"ordinal": ordinal, "run": run.model_dump(mode="json")},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        journal_handle.flush()
        print(
            json.dumps(
                {
                    "ordinal": ordinal,
                    "case_id": run.case_id,
                    "arm": run.arm.value,
                    "status": run.status.value,
                    "terminal": run.prediction.terminal,
                    "provider_calls": run.provider_calls,
                    "transport_retries": run.transport_retries,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    try:
        execution, observed_truths = execute_real_fault_study_v225(
            captures=captures,
            model_id=provider_config.model,
            flat_provider_factory=lambda: RealFaultFlatProviderV225(
                config=provider_config,
                timeout_seconds=args.timeout,
                max_completion_tokens=1600,
            ),
            current_provider_factory=current_provider,
            truth_loader=lambda case_id: truth_by_case[case_id],
            run_observer=observe_run,
        )
    finally:
        journal_handle.close()
    if observed_truths != truths:
        raise ValueError("real-fault truth load order differs")
    score = score_real_fault_study_v225(
        execution=execution,
        truths=truths,
        live_fault=live.live_fault,
        live_baseline=live.live_baseline,
        live_baseline_omission_reason=None,
        baseline_restored=live.baseline_restored,
        cleanup="CLEAN",
        non_owned_changes=0,
    )
    payload = {
        "schema_version": "dta-v225-real-fault.study-artifact.v1",
        "recorded_at": datetime.now(timezone.utc),
        "accepted_live_campaigns": 1,
        "shared_physical_captures": 2,
        "shared_capture_semantic_actions": 16,
        "shared_capture_target_equivalent_reads": 20,
        "manifest": manifest,
        "preflight": live.prepared.preflight,
        "capture_pair_sha256": live.capture_pair.pair_sha256,
        "fault_impact_sha256": live.fault_impact.evidence_sha256,
        "live_baseline": live.live_baseline,
        "live_fault": live.live_fault,
        "baseline_restored": True,
        "cleanup": "CLEAN",
        "non_owned_changes": 0,
        "execution": execution,
        "score": score,
        "execution_count": 1,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
        "uncaught_exceptions": 0,
    }
    draft = cast(Any, RealFaultStudyArtifactV1).model_construct(
        **payload, artifact_sha256="0" * 64
    )
    digest_payload = draft.model_dump(mode="json", exclude={"artifact_sha256"})
    artifact = RealFaultStudyArtifactV1.model_validate(
        {
            **payload,
            "artifact_sha256": hashlib.sha256(
                json.dumps(
                    digest_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest(),
        }
    )
    output_root = root / "config/dta-v225-real-fault"
    _write_once(
        output_root / "alias-maps.json",
        build_public_alias_map_set_v225(private_maps=live.prepared.alias_maps),
    )
    _write_once(output_root / "cases.json", case_set)
    _write_once(output_root / "truth.json", truth_set)
    _write_once(output_root / "manifest.json", manifest)
    for capture in cases:
        _write_once(output_root / "captures" / f"{capture.case_id}.json", capture)
    _write_once(
        root / "docs/results/dta-v225-real-fault-shadow-comparison.json",
        artifact,
    )
    write_private_json(
        campaign_root / "campaign-success.json",
        {
            "schema_version": "dta-v225-real-fault.campaign-success.v1",
            "transfer_terminal": score.transfer_terminal.value,
            "comparison_disposition": score.comparison_disposition.value,
            "execution_count": execution.execution_count,
            "baseline_restored": True,
            "cleanup": "CLEAN",
            "non_owned_changes": 0,
        },
        create_once=True,
    )
    print(
        json.dumps(
            {
                "transfer_terminal": score.transfer_terminal.value,
                "comparison_disposition": score.comparison_disposition.value,
                "execution_count": execution.execution_count,
                "cleanup": "CLEAN",
                "non_owned_changes": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "LiveCaptureOutcomeV225",
    "PreparedLiveStudyV225",
    "RealFaultLiveSequenceError",
    "RealFaultStudyArtifactV1",
    "main",
    "run_live_capture_sequence_v225",
)
