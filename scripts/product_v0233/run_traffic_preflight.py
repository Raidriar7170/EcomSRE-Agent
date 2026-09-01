#!/usr/bin/env python3
"""Run one evidence-bound Product v0.2.3.3 live traffic preflight attempt."""

from __future__ import annotations

import argparse
import ast
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.tool_contracts import HealthState, RuntimeRecord, RuntimeState
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.baseline_readiness_v021 import verify_queue_default_v021
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    FORMAL_CONTRACT_PREFLIGHT_PASS_V0233,
    FormalContractPreflightV0233,
    load_fresh_formal_campaign_v0233,
    load_fresh_traffic_profile_v0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalSourceSelectionV0233,
    configured_source_candidates_v0233,
    select_fresh_formal_source_v0233,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    HealthyTrafficRunnerV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
    load_preserved_runtime_inputs_v0231,
)
from ecomsre.product.pilot.traffic_preflight_v0233 import (
    ALLOWED_REPAIR_SURFACES_V0233,
    TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233,
    TRAFFIC_PREFLIGHT_PASS_V0233,
    DemoCleanupV0233,
    DiagnosisSemanticSourceManifestV0233,
    FormalClonePlanV0233,
    FormalContractFreezeV0233,
    ProductCleanupV0233,
    TrafficPreflightAttemptV0233,
    TrafficPreflightBlockedAttemptV0233,
    TrafficPreflightLedgerV0233,
    TrafficPreflightPassV0233,
    TrafficRepairSurfaceSnapshotV0233,
)
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    build_traffic_harness_typed_request_plan_v02321,
    materialize_planned_request_v02321,
)
from ecomsre_live_sandbox.contracts import (
    canonical_json_bytes,
    load_bundle,
    write_private_json,
)
from scripts.product_v0233.freeze_traffic_surface import freeze_surface


_ENDPOINT = "http://127.0.0.1:18080/api/checkout"
_ATTEMPT_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,39}$")
_GOAL_VERSION = "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.3 JSON object is invalid: {path.name}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_owner_count(database: Path) -> int:
    try:
        result = subprocess.run(
            ("lsof", "-F", "p", "--", str(database)),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("lsof is required for Product-state admission") from error
    if result.returncode not in {0, 1}:
        raise RuntimeError("Product-state owner observation failed")
    return len(
        {
            line.removeprefix("p")
            for line in result.stdout.splitlines()
            if line.startswith("p") and line.removeprefix("p").isdigit()
        }
    )


def _write_public_create_once(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Product v0.2.3.3 public artifact exists: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_public(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checkout_runtime(backend: Any, request: Any) -> tuple[str, bool, int]:
    result = backend.execute(request)
    records = tuple(item for item in result.records if type(item) is RuntimeRecord)
    if len(records) != 1 or records[0].logical_service != "checkout":
        raise RuntimeError("Product v0.2.3.3 checkout Runtime coverage differs")
    record = records[0]
    if (
        record.state is not RuntimeState.RUNNING
        or record.health is not HealthState.HEALTHY
        or record.restart_count != 0
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_CHECKOUT_RUNTIME")
    return record.state.value, True, record.restart_count


def _demo_cleanup(cleanup: Any) -> DemoCleanupV0233:
    result = DemoCleanupV0233(
        verdict=getattr(cleanup, "verdict", "BLOCKED"),
        owned_containers=getattr(cleanup, "owned_containers", -1),
        owned_networks=getattr(cleanup, "owned_networks", -1),
        owned_volumes=getattr(cleanup, "owned_volumes", -1),
        non_owned_resources_changed=getattr(
            cleanup, "non_owned_resources_changed", True
        ),
    )
    if not result.clean:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_DEMO_CLEANUP")
    return result


def _product_cleanup(
    observation: Mapping[str, object], *, before: int, after: int
) -> ProductCleanupV0233:
    result = ProductCleanupV0233.model_validate(
        {
            "verdict": observation.get("verdict", "BLOCKED"),
            "owned_host_processes": observation.get("owned_host_processes", -1),
            "database_owner_count_before": before,
            "database_owner_count_after": after,
            "product_api_port_available": observation.get(
                "product_api_port_available", False
            ),
            "non_owned_resources_changed": observation.get(
                "non_owned_resources_changed", True
            ),
        }
    )
    if not result.clean or observation.get("launches") != ():
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_PRODUCT_CLEANUP")
    return result


def _attempt_chain(
    root: Path,
    *,
    prior_attempt: Path | None,
    changed_surface: Path | None,
) -> tuple[
    tuple[TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233, ...],
    int,
    str | None,
    str | None,
    str | None,
]:
    ledger_path = root / "docs/analysis/product-v0233-traffic-preflight-ledger.json"
    if ledger_path.exists():
        ledger = TrafficPreflightLedgerV0233.model_validate_json(
            ledger_path.read_bytes()
        )
        attempts = ledger.attempts
    else:
        attempts = ()
    ordinal = len(attempts) + 1
    if ordinal == 1:
        if prior_attempt is not None or changed_surface is not None:
            raise ValueError("first traffic attempt cannot bind a changed surface")
        return attempts, ordinal, None, None, None
    if attempts[-1].terminal == TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233:
        raise ValueError("passing traffic preflight cannot be retried")
    previous_attempt = attempts[-1]
    if (
        previous_attempt.demo_cleanup is None
        or not previous_attempt.demo_cleanup.clean
        or previous_attempt.product_cleanup is None
        or not previous_attempt.product_cleanup.clean
        or previous_attempt.source_state_before_sha256 is None
        or previous_attempt.source_state_after_sha256 is None
        or previous_attempt.source_state_before_sha256
        != previous_attempt.source_state_after_sha256
    ):
        raise ValueError("later traffic attempt requires prior CLEAN closure")
    if len(attempts) >= 2:
        previous_classes = tuple(_attempt_failure_class(item) for item in attempts[-2:])
        if previous_classes[0] is not None and previous_classes[0] == previous_classes[1]:
            raise ValueError("recurring traffic failure class closes retry authority")
    expected_prior = (
        root
        / "docs/analysis"
        / f"product-v0233-traffic-preflight-attempt-{ordinal - 1}.json"
    ).resolve(strict=True)
    if prior_attempt is None or prior_attempt.resolve(strict=True) != expected_prior:
        raise ValueError("later traffic attempt prior Attempt differs")
    prior_payload = _load_object(expected_prior)
    prior_sha256 = prior_payload.get("attempt_sha256")
    if prior_sha256 != attempts[-1].attempt_sha256:
        raise ValueError("later traffic attempt prior seal differs")
    snapshot_path = (
        root
        / "docs/analysis"
        / f"product-v0233-traffic-repair-surface-attempt-{ordinal - 1}.json"
    )
    snapshot = TrafficRepairSurfaceSnapshotV0233.model_validate_json(
        snapshot_path.read_bytes()
    )
    if (
        snapshot.attempt_ordinal != ordinal - 1
        or snapshot.attempt_sha256 != prior_sha256
    ):
        raise ValueError("later traffic attempt repair surface binding differs")
    if changed_surface is None:
        raise ValueError("later traffic attempt requires changed infrastructure surface")
    surface = changed_surface.resolve(strict=True)
    try:
        locator = surface.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("changed surface must be inside the campaign repository") from error
    if not surface.is_file() or locator not in ALLOWED_REPAIR_SURFACES_V0233:
        raise ValueError("changed surface is not an infrastructure/harness file")
    surface_sha256 = _sha256_file(surface)
    current_surface_sha256_by_path = {
        path: _sha256_file(root / path) for path in ALLOWED_REPAIR_SURFACES_V0233
    }
    if current_surface_sha256_by_path == snapshot.source_sha256_by_path:
        raise ValueError("identical traffic preflight rerun is prohibited")
    if snapshot.source_sha256_by_path[locator] == surface_sha256:
        raise ValueError("declared traffic repair surface did not change")
    return attempts, ordinal, str(prior_sha256), locator, surface_sha256


def _attempt_failure_class(
    attempt: TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233,
) -> tuple[str, str] | None:
    if isinstance(attempt, TrafficPreflightBlockedAttemptV0233):
        return attempt.failure_stage, attempt.safe_error_type
    if attempt.terminal != TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233:
        return "COMPLETED_TRAFFIC_RESULT", "HealthyTrafficRunNotPass"
    return None


def _persist_attempt(
    root: Path,
    *,
    prior_attempts: tuple[
        TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233, ...
    ],
    attempt: TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233,
) -> TrafficPreflightLedgerV0233:
    attempt_path = (
        root
        / "docs/analysis"
        / f"product-v0233-traffic-preflight-attempt-{attempt.attempt_ordinal}.json"
    )
    _write_public_create_once(attempt_path, attempt.model_dump(mode="json"))
    ledger = TrafficPreflightLedgerV0233.build(attempts=(*prior_attempts, attempt))
    _replace_public(
        root / "docs/analysis/product-v0233-traffic-preflight-ledger.json",
        ledger.model_dump(mode="json"),
    )
    return ledger


def _progress(
    root: Path,
    *,
    terminal: str,
    attempt_count: int,
    attempt_sha256: str,
    traffic_preflight_sha256: str | None = None,
    formal_contract_freeze_sha256: str | None = None,
) -> dict[str, Any]:
    prior = _load_object(root / "docs/analysis/product-v0233-progress.json")
    body = {
        **{key: value for key, value in prior.items() if key != "progress_sha256"},
        "phase": (
            "INCREMENT_3_TRAFFIC_PREFLIGHT_PASS"
            if terminal == TRAFFIC_PREFLIGHT_PASS_V0233
            else "INCREMENT_3_TRAFFIC_PREFLIGHT_BLOCKED"
        ),
        "current_terminal": terminal,
        "live_traffic_preflight_count": attempt_count,
        "traffic_preflight_attempt_sha256": attempt_sha256,
        "traffic_preflight_sha256": traffic_preflight_sha256,
        "formal_contract_freeze_sha256": formal_contract_freeze_sha256,
        "next_gate": (
            "PRODUCT_V0233_PRE_EXECUTION_REVIEW"
            if terminal == TRAFFIC_PREFLIGHT_PASS_V0233
            else "TARGETED_INFRASTRUCTURE_OR_HARNESS_REPAIR"
        ),
    }
    return {**body, "progress_sha256": semantic_sha256_v22(body)}


def _module_source_path(root: Path, module: str) -> Path | None:
    if not module.startswith("ecomsre"):
        return None
    base = root / "src" / Path(*module.split("."))
    source = base.with_suffix(".py")
    if source.is_file():
        return source
    package = base / "__init__.py"
    return package if package.is_file() else None


def _diagnosis_source_manifest(root: Path) -> DiagnosisSemanticSourceManifestV0233:
    entry_points = (
        "src/ecomsre/product/api.py",
        "src/ecomsre/product/incidents/diagnosis_pipeline_v02322.py",
        "src/ecomsre/product/jobs/handlers.py",
        "src/ecomsre/product/jobs/worker.py",
        "src/ecomsre/product/pilot/nofault_acceptance_v0232.py",
    )
    pending = [root / path for path in entry_points]
    discovered: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in discovered:
            continue
        if not source.is_file() or not source.is_relative_to(root / "src"):
            raise ValueError("Product v0.2.3.3 Diagnosis source closure differs")
        discovered.add(source)
        relative = source.relative_to(root / "src").with_suffix("")
        module_parts = list(relative.parts)
        if module_parts[-1] == "__init__":
            module_parts.pop()
            package_parts = module_parts
        else:
            package_parts = module_parts[:-1]
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    keep = len(package_parts) - node.level + 1
                    base_parts = package_parts[: max(keep, 0)]
                    if node.module:
                        base_parts.extend(node.module.split("."))
                    base = ".".join(base_parts)
                else:
                    base = node.module or ""
                if base:
                    modules.add(base)
                    modules.update(
                        f"{base}.{alias.name}"
                        for alias in node.names
                        if alias.name != "*"
                    )
        for module in modules:
            candidate = _module_source_path(root, module)
            if candidate is not None and candidate not in discovered:
                pending.append(candidate)
            parts = module.split(".")
            for length in range(1, len(parts)):
                package = _module_source_path(root, ".".join(parts[:length]))
                if package is not None and package.name == "__init__.py":
                    pending.append(package)
    source_sha256_by_path = {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in sorted(discovered)
    }
    return DiagnosisSemanticSourceManifestV0233.build(
        entry_point_paths=entry_points,
        source_sha256_by_path=source_sha256_by_path,
    )


def _formal_freeze(
    root: Path,
    *,
    preflight: TrafficPreflightPassV0233,
    prepared_manifest: ProductV0233RepositoryStateManifest,
    campaign: Any,
    attempt: TrafficPreflightAttemptV0233,
) -> tuple[
    FormalClonePlanV0233,
    DiagnosisSemanticSourceManifestV0233,
    FormalContractFreezeV0233,
]:
    clone_plan = FormalClonePlanV0233.build(
        source_selection_sha256=attempt.source_selection_sha256
    )
    source_manifest = _diagnosis_source_manifest(root)
    scorer_path = "src/ecomsre/product/pilot/nofault_acceptance_v0232.py"
    scorer_sha256 = _sha256_file(root / scorer_path)
    if scorer_sha256 != campaign.nofault_scorer_source_sha256:
        raise ValueError("Product v0.2.3.3 No-Fault scorer source drifted")
    freeze = FormalContractFreezeV0233.build(
        campaign_sha256=campaign.campaign_sha256,
        traffic_preflight_sha256=preflight.preflight_sha256,
        source_selection_sha256=attempt.source_selection_sha256,
        formal_clone_plan_sha256=clone_plan.plan_sha256,
        traffic_contract_sha256=attempt.traffic_contract_sha256,
        preflight_profile_sha256=attempt.profile_sha256,
        preflight_profile_file_sha256=_sha256_file(
            root / "config/product-v0233/traffic/preflight-profile.json"
        ),
        formal_profile_sha256=campaign.formal_profile_sha256,
        formal_profile_file_sha256=_sha256_file(
            root / "config/product-v0233/traffic/formal-profile.json"
        ),
        runtime_continuity_descriptor_sha256=(
            attempt.runtime_continuity_descriptor_sha256
        ),
        flagd_bind_descriptor_sha256=attempt.flagd_bind_descriptor_sha256,
        resolved_compose_sha256=attempt.resolved_compose_sha256,
        read_authority_sha256=attempt.read_authority_sha256,
        pilot_runtime_authority_sha256=attempt.pilot_runtime_authority_sha256,
        active_profile_sha256=campaign.active_profile_sha256,
        active_baseline_sha256=campaign.active_baseline_sha256,
        stage_journal_contract_sha256=campaign.stage_journal_contract_sha256,
        private_failure_contract_sha256=campaign.private_failure_contract_sha256,
        diagnosis_semantic_source_manifest_sha256=source_manifest.manifest_sha256,
        nofault_scorer_source_sha256=scorer_sha256,
        prepared_repository_manifest_sha256=prepared_manifest.manifest_sha256,
    )
    return clone_plan, source_manifest, freeze


def run_traffic_preflight_v0233(
    *,
    project_root: Path,
    preferred_root: Path,
    fallback_root: Path,
    runtime_predecessor_root: Path,
    attempt_id: str,
    prior_attempt: Path | None = None,
    changed_surface: Path | None = None,
) -> TrafficPreflightAttemptV0233 | TrafficPreflightBlockedAttemptV0233:
    if _ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise ValueError("Product v0.2.3.3 traffic attempt ID differs")
    root = project_root.resolve(strict=True)
    predecessor = runtime_predecessor_root.resolve(strict=True)
    if (root / "docs/analysis/product-v0233-traffic-preflight.json").exists():
        raise FileExistsError("Product v0.2.3.3 traffic preflight already passed")
    prior_attempts, ordinal, prior_sha256, surface, surface_sha256 = _attempt_chain(
        root,
        prior_attempt=prior_attempt,
        changed_surface=changed_surface,
    )
    attempt_public = (
        root
        / "docs/analysis"
        / f"product-v0233-traffic-preflight-attempt-{ordinal}.json"
    )
    private_root = root / f".local/product-v0233/traffic-preflight/{attempt_id}"
    if private_root.exists() or private_root.is_symlink() or attempt_public.exists():
        raise FileExistsError("Product v0.2.3.3 traffic attempt exists")

    campaign = load_fresh_formal_campaign_v0233(root)
    profile = load_fresh_traffic_profile_v0233(root, role="PREFLIGHT")
    formal_profile = load_fresh_traffic_profile_v0233(root, role="FORMAL")
    engine_profile = profile.engine_profile_v0232()
    contract = load_checkout_traffic_contract_v0232(root)
    if contract.contract_sha256 != campaign.traffic_contract_sha256:
        raise ValueError("Product v0.2.3.3 traffic contract drifted")
    prepared_manifest = ProductV0233RepositoryStateManifest.model_validate_json(
        (root / "config/product-v0233/repository-state-manifest.json").read_bytes()
    )
    contract_preflight = FormalContractPreflightV0233.model_validate_json(
        (root / "docs/analysis/product-v0233-formal-contract-preflight.json").read_bytes()
    )
    if (
        prepared_manifest.phase is not RepositoryPhaseV0233.PREPARED
        or prepared_manifest.formal_clone_count != 0
        or prepared_manifest.formal_execution_count != 0
        or contract_preflight.terminal != FORMAL_CONTRACT_PREFLIGHT_PASS_V0233
        or contract_preflight.preflight_sha256
        != prepared_manifest.contract_preflight_sha256
    ):
        raise ValueError("Product v0.2.3.3 prepared boundary differs")

    preferred, fallback = configured_source_candidates_v0233(
        preferred_root=preferred_root,
        fallback_root=fallback_root,
    )
    source_before = select_fresh_formal_source_v0233(
        preferred=preferred,
        fallback=fallback,
        owner_counter=_database_owner_count,
    )
    tracked_source = FreshFormalSourceSelectionV0233.model_validate_json(
        (root / "config/product-v0233/source-selection.json").read_bytes()
    )
    if source_before != tracked_source:
        raise ValueError("Product v0.2.3.3 selected source drifted")
    selected_root = (
        preferred.source_root
        if source_before.source_kind == preferred.source_kind
        else fallback.source_root
    )
    database_owner_count_before = _database_owner_count(
        selected_root / "product.sqlite3"
    )
    product_processes = _ProductHostProcessesV023(
        root=root,
        data_root=selected_root,
        private_root=private_root / "product-processes",
    )
    product_before = product_processes.cleanup_observation()
    if (
        database_owner_count_before != 0
        or product_before.get("verdict") != "CLEAN"
        or product_before.get("launches") != ()
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_PRODUCT_PREEXISTING")

    private_binding = ProductV023PrivateStateBindingV0231.model_validate(
        _load_object(root / "config/product-v0231/historical-results.v1.json").get(
            "private_state"
        )
    )
    context = ProductBaselineContinuationContextV0231.model_validate_json(
        (root / "docs/analysis/product-v0231-baseline-continuation-context.json").read_bytes()
    )
    tracked_runtime = RuntimeAuthorityContinuityDescriptorV0231.model_validate_json(
        (root / "docs/analysis/product-v0231-runtime-authority-descriptor.json").read_bytes()
    )
    bundle = load_bundle(predecessor / "config/live-telemetry-controlled-remediation-v1")
    authority, resolved_compose = load_preserved_runtime_inputs_v0231(
        predecessor_root=predecessor,
        binding=private_binding,
    )
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=private_root / "demo",
        binding=private_binding,
        context=context,
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=resolved_compose,
    )
    lifecycle.admit_prestart()
    if (
        lifecycle.runtime_descriptor != tracked_runtime
        or tracked_runtime.descriptor_sha256
        != campaign.runtime_continuity_descriptor_sha256
        or tracked_runtime.flagd_bind_descriptor_sha256
        != campaign.flagd_bind_descriptor_sha256
    ):
        raise ValueError("Product v0.2.3.3 Runtime authority drifted")

    # Preflight has no Product-state clone; source selection is the stable state
    # binding consumed by the inherited typed-request planner.
    typed_plan = build_traffic_harness_typed_request_plan_v02321(
        campaign_sha256=campaign.campaign_sha256,
        role="PREFLIGHT",
        state_clone_sha256=source_before.selection_sha256,
        attempt_ordinal=ordinal,
    )
    runtime_request = materialize_planned_request_v02321(
        typed_plan, tool_name="inspect_service_runtime"
    )

    attempt_consumed = False
    execution: HealthyTrafficExecutionV0232 | None = None
    checkout_runtime: tuple[str, bool, int] | None = None
    queue_before_sha256: str | None = None
    queue_after_sha256: str | None = None
    baseline_before_sha256: str | None = None
    baseline_after_sha256: str | None = None
    source_after: FreshFormalSourceSelectionV0233 | None = None
    demo_cleanup: DemoCleanupV0233 | None = None
    product_cleanup: ProductCleanupV0233 | None = None
    live_error: BaseException | None = None
    failure_stage = "BEFORE_RUNTIME_START"

    def consume_attempt() -> None:
        nonlocal attempt_consumed
        write_private_json(
            private_root / "attempt-start.json",
            {
                "schema_version": (
                    "ecomsre.product.private-traffic-preflight-start.v0233"
                ),
                "attempt_id": attempt_id,
                "attempt_ordinal": ordinal,
                "started_at": datetime.now(UTC).isoformat(),
                "campaign_sha256": campaign.campaign_sha256,
                "source_selection_sha256": source_before.selection_sha256,
                "profile_sha256": profile.profile_sha256,
                "engine_profile_sha256": engine_profile.profile_sha256,
                "traffic_contract_sha256": contract.contract_sha256,
                "typed_request_plan_sha256": typed_plan.plan_sha256,
                "prior_attempt_sha256": prior_sha256,
                "changed_surface": surface,
                "changed_surface_sha256": surface_sha256,
                "formal_clone_count": 0,
                "formal_execution_count": 0,
                "new_incident_count": 0,
                "new_diagnosis_count": 0,
                "action_authority": "NONE",
            },
            create_once=True,
        )
        attempt_consumed = True

    try:
        lifecycle.start(on_boundary_verified=consume_attempt)
        failure_stage = "RUNTIME_READINESS"
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if lifecycle.rebound_authority != authority:
            raise ValueError("Product v0.2.3.3 fresh Runtime authority differs")
        checkout_runtime = _checkout_runtime(backend, runtime_request)
        failure_stage = "PRE_TRAFFIC_CLOSURE"
        queue_before = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=profile.queue_fault_flag,
        )
        queue_before_sha256 = queue_before.before_sha256
        baseline_before_sha256 = lifecycle.read_baseline_sha256()
        time.sleep(profile.stabilization_seconds)
        failure_stage = "TRAFFIC_EXECUTION"
        with HealthyTrafficRunnerV0232() as runner:
            execution = runner.run(
                endpoint=_ENDPOINT,
                profile=engine_profile,
                contract=contract,
                role="PREFLIGHT",
            )
        write_private_json(
            private_root / "traffic-execution.json",
            execution.model_dump(mode="json"),
            create_once=True,
        )
        failure_stage = "POST_TRAFFIC_CLOSURE"
    except BaseException as error:
        live_error = error
    finally:
        cleanup_errors: list[BaseException] = []
        if lifecycle.environment is not None:
            try:
                if queue_before_sha256 is not None:
                    queue_after = verify_queue_default_v021(
                        lifecycle.flag_file,
                        expected_default_value=profile.queue_fault_flag,
                        expected_sha256=queue_before_sha256,
                    )
                    queue_after_sha256 = queue_after.after_sha256
                if baseline_before_sha256 is not None:
                    baseline_after_sha256 = lifecycle.read_baseline_sha256()
                demo_cleanup = _demo_cleanup(
                    lifecycle.cleanup_owned(
                        baseline_unchanged=(
                            baseline_before_sha256 is None
                            or baseline_before_sha256 == baseline_after_sha256
                        )
                    )
                )
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        try:
            product_observation = product_processes.cleanup_observation()
            database_owner_count_after = _database_owner_count(
                selected_root / "product.sqlite3"
            )
            product_cleanup = _product_cleanup(
                product_observation,
                before=database_owner_count_before,
                after=database_owner_count_after,
            )
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
        try:
            source_after = select_fresh_formal_source_v0233(
                preferred=preferred,
                fallback=fallback,
                owner_counter=_database_owner_count,
            )
            if source_after != source_before:
                raise RuntimeError("Product v0.2.3.3 source changed during preflight")
        except BaseException as source_error:
            cleanup_errors.append(source_error)
        if cleanup_errors:
            if live_error is None:
                live_error = cleanup_errors[0]
                failure_stage = "CLEANUP_OR_SOURCE_CLOSURE"
            write_private_json(
                private_root / "cleanup-failure.json",
                {
                    "schema_version": (
                        "ecomsre.product.private-cleanup-failure.v0233"
                    ),
                    "safe_error_types": [
                        type(error).__name__ for error in cleanup_errors
                    ],
                },
                create_once=True,
            )

    if live_error is not None:
        blocked = TrafficPreflightBlockedAttemptV0233.build(
            attempt_id=attempt_id,
            attempt_ordinal=ordinal,
            prior_attempt_sha256=prior_sha256,
            changed_surface=surface,
            changed_surface_sha256=surface_sha256,
            attempt_consumed=attempt_consumed,
            failure_stage=failure_stage,
            safe_error_type=type(live_error).__name__,
            campaign_sha256=campaign.campaign_sha256,
            source_selection_sha256=source_before.selection_sha256,
            profile_sha256=profile.profile_sha256,
            traffic_contract_sha256=contract.contract_sha256,
            source_state_before_sha256=source_before.selection_sha256,
            source_state_after_sha256=(
                source_after.selection_sha256 if source_after is not None else None
            ),
            demo_cleanup=demo_cleanup,
            product_cleanup=product_cleanup,
        )
        write_private_json(
            private_root / "attempt-failure.json",
            blocked.model_dump(mode="json"),
            create_once=True,
        )
        ledger = _persist_attempt(
            root, prior_attempts=prior_attempts, attempt=blocked
        )
        freeze_surface(
            project_root=root,
            attempt_ordinal=blocked.attempt_ordinal,
            preserve_round_one_freeze=False,
        )
        recurring = (
            bool(prior_attempts)
            and _attempt_failure_class(prior_attempts[-1])
            == _attempt_failure_class(blocked)
        )
        _replace_public(
            root / "docs/analysis/product-v0233-progress.json",
            _progress(
                root,
                terminal=(
                    "BLOCKED_ECOMSRE_PRODUCT_V0233_RECURRING_TRAFFIC_PREFLIGHT_FAILURE"
                    if recurring
                    else "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT"
                ),
                attempt_count=ledger.attempt_count,
                attempt_sha256=blocked.attempt_sha256,
            ),
        )
        return blocked

    if (
        not attempt_consumed
        or execution is None
        or checkout_runtime is None
        or queue_before_sha256 is None
        or queue_after_sha256 is None
        or baseline_before_sha256 is None
        or baseline_after_sha256 is None
        or source_after is None
        or demo_cleanup is None
        or product_cleanup is None
    ):
        raise RuntimeError("Product v0.2.3.3 traffic preflight closure is incomplete")
    attempt = TrafficPreflightAttemptV0233.build(
        attempt_id=attempt_id,
        attempt_ordinal=ordinal,
        prior_attempt_sha256=prior_sha256,
        changed_surface=surface,
        changed_surface_sha256=surface_sha256,
        campaign_sha256=campaign.campaign_sha256,
        source_selection_sha256=source_before.selection_sha256,
        profile_sha256=profile.profile_sha256,
        engine_profile_sha256=engine_profile.profile_sha256,
        traffic_contract_sha256=contract.contract_sha256,
        typed_request_plan_sha256=typed_plan.plan_sha256,
        flagd_bind_descriptor_sha256=tracked_runtime.flagd_bind_descriptor_sha256,
        runtime_continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
        resolved_compose_sha256=tracked_runtime.resolved_compose_sha256,
        read_authority_sha256=authority.read_authority.authority_sha256,
        pilot_runtime_authority_sha256=authority.pilot_authority_sha256,
        checkout_state=checkout_runtime[0],
        checkout_healthy=checkout_runtime[1],
        checkout_restart_count=checkout_runtime[2],
        execution=execution,
        queue_before_sha256=queue_before_sha256,
        queue_after_sha256=queue_after_sha256,
        outer_baseline_before_sha256=baseline_before_sha256,
        outer_baseline_after_sha256=baseline_after_sha256,
        source_state_before_sha256=source_before.selection_sha256,
        source_state_after_sha256=source_after.selection_sha256,
        source_incident_count=source_before.source_counts.incident_count,
        source_diagnosis_count=source_before.source_counts.diagnosis_count,
        demo_cleanup=demo_cleanup,
        product_cleanup=product_cleanup,
    )
    if attempt.terminal != TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233:
        write_private_json(
            private_root / "attempt-failure.json",
            attempt.model_dump(mode="json"),
            create_once=True,
        )
        ledger = _persist_attempt(
            root, prior_attempts=prior_attempts, attempt=attempt
        )
        freeze_surface(
            project_root=root,
            attempt_ordinal=attempt.attempt_ordinal,
            preserve_round_one_freeze=False,
        )
        recurring = (
            bool(prior_attempts)
            and _attempt_failure_class(prior_attempts[-1])
            == _attempt_failure_class(attempt)
        )
        _replace_public(
            root / "docs/analysis/product-v0233-progress.json",
            _progress(
                root,
                terminal=(
                    "BLOCKED_ECOMSRE_PRODUCT_V0233_RECURRING_TRAFFIC_PREFLIGHT_FAILURE"
                    if recurring
                    else "BLOCKED_ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT"
                ),
                attempt_count=ledger.attempt_count,
                attempt_sha256=attempt.attempt_sha256,
            ),
        )
        return attempt
    ledger = _persist_attempt(root, prior_attempts=prior_attempts, attempt=attempt)
    preflight = TrafficPreflightPassV0233.build(
        attempt=attempt,
        ledger_sha256=ledger.ledger_sha256,
        formal_profile_sha256=formal_profile.profile_sha256,
    )
    clone_plan, source_manifest, freeze = _formal_freeze(
        root,
        preflight=preflight,
        prepared_manifest=prepared_manifest,
        campaign=campaign,
        attempt=attempt,
    )
    _write_public_create_once(
        root / "docs/analysis/product-v0233-formal-clone-plan.json",
        clone_plan.model_dump(mode="json"),
    )
    _write_public_create_once(
        root / "docs/analysis/product-v0233-diagnosis-source-manifest.json",
        source_manifest.model_dump(mode="json"),
    )
    _write_public_create_once(
        root / "docs/analysis/product-v0233-traffic-preflight.json",
        preflight.model_dump(mode="json"),
    )
    _write_public_create_once(
        root / "docs/analysis/product-v0233-formal-contract-freeze.json",
        freeze.model_dump(mode="json"),
    )
    _replace_public(
        root / "docs/analysis/product-v0233-progress.json",
        _progress(
            root,
            terminal=preflight.terminal,
            attempt_count=ledger.attempt_count,
            attempt_sha256=attempt.attempt_sha256,
            traffic_preflight_sha256=preflight.preflight_sha256,
            formal_contract_freeze_sha256=freeze.freeze_sha256,
        ),
    )
    return attempt


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--preferred-root", type=Path, required=True)
    parser.add_argument("--fallback-root", type=Path, required=True)
    parser.add_argument("--runtime-predecessor-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--prior-attempt", type=Path)
    parser.add_argument("--changed-surface", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    result = run_traffic_preflight_v0233(
        project_root=arguments.project_root,
        preferred_root=arguments.preferred_root,
        fallback_root=arguments.fallback_root,
        runtime_predecessor_root=arguments.runtime_predecessor_root,
        attempt_id=arguments.attempt_id,
        prior_attempt=arguments.prior_attempt,
        changed_surface=arguments.changed_surface,
    )
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0 if result.terminal == TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0233 else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("run_traffic_preflight_v0233",)
