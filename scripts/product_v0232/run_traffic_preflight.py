#!/usr/bin/env python3
"""Run one bounded Product v0.2.3.2 healthy-traffic preflight Attempt."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.tool_contracts import (
    HealthState,
    RuntimeRecord,
    RuntimeState,
    build_inspect_service_runtime_request,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.baseline_readiness_v021 import (
    verify_queue_default_v021,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    HealthyTrafficRunnerV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateSourceV0232,
    admit_product_state_source_v0232,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
    load_preserved_runtime_inputs_v0231,
)
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232,
    TrafficPreflightAttemptV0232,
    TrafficPreflightEvidenceV0232,
    load_traffic_campaign_v0232,
    load_traffic_profile_v0232,
)
from ecomsre_live_sandbox.contracts import (
    canonical_json_bytes,
    load_bundle,
    write_private_json,
)
from scripts.ci.verify_product_v0232_traffic import verify_product_v0232_traffic
from scripts.product_v0232.run_evidence_binding_preflight import run_preflight
from scripts.product_v0232.run_state_clone import (
    BASELINE_ID_V0232,
    BASELINE_SHA256_V0232,
    ENVIRONMENT_ID_V0232,
    PILOT_RUNTIME_AUTHORITY_SHA256_V0232,
    PROFILE_SHA256_V0232,
    RUNTIME_CONNECTOR_BINDING_SHA256_V0232,
    verify_existing_state_clone,
)
from scripts.ci.verify_product_v0232_history import SOURCE_LOCATOR_V0232


_ENDPOINT_V0232 = "http://127.0.0.1:18080/api/checkout"
_EXPECTED_INCREMENT3_TERMINAL = (
    "ECOMSRE_PRODUCT_V0232_EVIDENCE_BINDING_CONTRACT_PASS"
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.2 JSON object is invalid: {path.name}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_public_create_once(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Product v0.2.3.2 public report exists: {path.name}")
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


def _admit_source_state(source_product_root: Path) -> ProductStateSourceV0232:
    return admit_product_state_source_v0232(
        source_product_root,
        source_locator=SOURCE_LOCATOR_V0232,
        expected_environment_id=ENVIRONMENT_ID_V0232,
        expected_baseline_id=BASELINE_ID_V0232,
        expected_baseline_sha256=BASELINE_SHA256_V0232,
        expected_profile_sha256=PROFILE_SHA256_V0232,
        expected_pilot_runtime_authority_sha256=(
            PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        ),
        expected_runtime_connector_binding_sha256=(
            RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        ),
    )


def _admit_clone_state(root: Path) -> tuple[ProductStateSourceV0232, str, Path]:
    clone = _load_object(root / "docs/analysis/product-v0232-product-state-clone.json")
    locator = clone.get("destination_locator")
    clone_sha256 = clone.get("clone_sha256")
    if not isinstance(locator, str) or not isinstance(clone_sha256, str):
        raise ValueError("Product v0.2.3.2 clone report differs")
    data_root = root / locator
    state = admit_product_state_source_v0232(
        data_root,
        source_locator=locator,
        expected_environment_id=ENVIRONMENT_ID_V0232,
        expected_baseline_id=BASELINE_ID_V0232,
        expected_baseline_sha256=BASELINE_SHA256_V0232,
        expected_profile_sha256=PROFILE_SHA256_V0232,
        expected_pilot_runtime_authority_sha256=(
            PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        ),
        expected_runtime_connector_binding_sha256=(
            RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        ),
    )
    return state, clone_sha256, data_root


def _database_owner_count(database: Path) -> int:
    result = subprocess.run(
        ("lsof", "-F", "p", str(database)),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Product-state owner observation failed")
    return len(
        {
            line.removeprefix("p")
            for line in result.stdout.splitlines()
            if line.startswith("p") and line.removeprefix("p").isdigit()
        }
    )


def _checkout_runtime(backend: Any, *, run_id: str) -> tuple[str, bool, int]:
    result = backend.execute(
        build_inspect_service_runtime_request(
            run_id=run_id,
            services=("checkout",),
            max_results=1,
        )
    )
    records = tuple(item for item in result.records if type(item) is RuntimeRecord)
    if len(records) != 1 or records[0].logical_service != "checkout":
        raise RuntimeError("Product v0.2.3.2 checkout Runtime coverage differs")
    record = records[0]
    if (
        record.state is not RuntimeState.RUNNING
        or record.health is not HealthState.HEALTHY
        or record.restart_count != 0
    ):
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V0232_CHECKOUT_RUNTIME_NOT_HEALTHY"
        )
    return record.state.value, True, record.restart_count


def _cleanup_payload(cleanup: Any) -> dict[str, object]:
    payload = {
        "verdict": getattr(cleanup, "verdict", "BLOCKED"),
        "owned_containers": getattr(cleanup, "owned_containers", -1),
        "owned_networks": getattr(cleanup, "owned_networks", -1),
        "owned_volumes": getattr(cleanup, "owned_volumes", -1),
        "non_owned_resources_changed": getattr(
            cleanup, "non_owned_resources_changed", True
        ),
    }
    if (
        payload["verdict"] != "CLEAN"
        or any(payload[name] != 0 for name in ("owned_containers", "owned_networks", "owned_volumes"))
        or payload["non_owned_resources_changed"] is not False
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0232_DEMO_CLEANUP")
    return payload


def _updated_progress(
    root: Path,
    *,
    attempt: TrafficPreflightAttemptV0232,
    preflight: TrafficPreflightEvidenceV0232,
    campaign_sha256: str,
    base_progress: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    progress = dict(base_progress) if base_progress is not None else _increment3_progress(root)
    body = {
        **progress,
        "terminal": preflight.terminal,
        "increment": 4,
        "live_traffic_preflight_attempt_count": attempt.attempt_ordinal,
        "traffic_preflight_attempt_sha256": attempt.attempt_sha256,
        "traffic_preflight_sha256": preflight.preflight_sha256,
        "formal_profile_sha256": preflight.formal_profile_sha256,
        "campaign_sha256": campaign_sha256,
    }
    return {**body, "progress_sha256": semantic_sha256_v22(body)}


def _increment3_progress(root: Path) -> dict[str, Any]:
    progress = _load_object(root / "docs/analysis/product-v0232-progress.json")
    supplied = progress.pop("progress_sha256", None)
    if (
        supplied != semantic_sha256_v22(progress)
        or progress.get("terminal") != _EXPECTED_INCREMENT3_TERMINAL
        or progress.get("increment") != 3
        or progress.get("offline_changed_iteration_count") != 3
        or progress.get("live_traffic_preflight_attempt_count") != 0
        or progress.get("formal_healthy_traffic_execution_count") != 0
    ):
        raise ValueError("Product v0.2.3.2 preflight progress boundary differs")
    return progress


def _blocked_progress(
    root: Path,
    *,
    attempt_ordinal: int,
    attempt_sha256: str,
    closure_failure_sha256: str | None = None,
    base_progress: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    progress = dict(base_progress) if base_progress is not None else _increment3_progress(root)
    body = {
        **progress,
        "terminal": "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT",
        "increment": 4,
        "live_traffic_preflight_attempt_count": attempt_ordinal,
        "traffic_preflight_attempt_sha256": attempt_sha256,
    }
    if closure_failure_sha256 is not None:
        body["traffic_preflight_closure_failure_sha256"] = closure_failure_sha256
    return {**body, "progress_sha256": semantic_sha256_v22(body)}


def run_traffic_preflight_v0232(
    *,
    project_root: Path,
    predecessor_root: Path,
    source_product_root: Path,
    predecessor_private_acceptance: Path,
    attempt_ordinal: int,
    changed_parameter: str | None = None,
    changed_parameter_evidence_sha256: str | None = None,
) -> TrafficPreflightAttemptV0232:
    root = Path(project_root).resolve(strict=True)
    predecessor = Path(predecessor_root).resolve(strict=True)
    source_product = Path(source_product_root).resolve(strict=True)
    if attempt_ordinal != 1:
        raise ValueError(
            "Attempt 2 requires a separately reviewed one-parameter profile change"
        )
    if changed_parameter is not None or changed_parameter_evidence_sha256 is not None:
        raise ValueError("Attempt 1 cannot declare a changed parameter")
    attempt_public_path = (
        root
        / f"docs/analysis/product-v0232-traffic-preflight-attempt-{attempt_ordinal}.json"
    )
    preflight_public_path = root / "docs/analysis/product-v0232-traffic-preflight.json"
    closure_failure_public_path = (
        root
        / "docs/analysis"
        / (
            "product-v0232-traffic-preflight-closure-failure-"
            f"attempt-{attempt_ordinal}.json"
        )
    )
    private_root = (
        root / f".local/product-v0232/traffic-preflight/attempt-{attempt_ordinal}"
    )
    if (
        private_root.exists()
        or private_root.is_symlink()
        or attempt_public_path.exists()
        or attempt_public_path.is_symlink()
        or preflight_public_path.exists()
        or preflight_public_path.is_symlink()
        or closure_failure_public_path.exists()
        or closure_failure_public_path.is_symlink()
    ):
        raise FileExistsError("Product v0.2.3.2 traffic preflight Attempt exists")

    verify_existing_state_clone(
        project_root=root,
        source_root=source_product,
        predecessor_private_acceptance=predecessor_private_acceptance,
    )
    verify_product_v0232_traffic(root)
    evidence_preflight = run_preflight(root)
    if evidence_preflight.get("terminal") != _EXPECTED_INCREMENT3_TERMINAL:
        raise ValueError("Product v0.2.3.2 Evidence-binding preflight differs")

    contract = load_checkout_traffic_contract_v0232(root)
    profile = load_traffic_profile_v0232(root, role="PREFLIGHT")
    formal_profile = load_traffic_profile_v0232(root, role="FORMAL")
    campaign = load_traffic_campaign_v0232(root)
    starting_progress = _increment3_progress(root)
    formal_profile_file_sha256_before = _sha256_file(
        root / "config/product-v0232/traffic/formal-profile.json"
    )
    manifest = _load_object(root / "config/product-v0231/historical-results.v1.json")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest.get("private_state")
    )
    context = ProductBaselineContinuationContextV0231.model_validate(
        _load_object(
            root / "docs/analysis/product-v0231-baseline-continuation-context.json"
        )
    )
    tracked_runtime = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _load_object(root / "docs/analysis/product-v0231-runtime-authority-descriptor.json")
    )
    bundle = load_bundle(predecessor / "config/live-telemetry-controlled-remediation-v1")
    authority, resolved_compose = load_preserved_runtime_inputs_v0231(
        predecessor_root=predecessor,
        binding=binding,
    )
    source_before = _admit_source_state(source_product)
    product_before, product_clone_sha256, product_data_root = _admit_clone_state(root)
    database_owner_count_before = _database_owner_count(
        product_data_root / "product.sqlite3"
    )
    product_processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_data_root,
        private_root=private_root / "product-processes",
    )
    product_preflight = product_processes.cleanup_observation()
    if (
        database_owner_count_before != 0
        or product_preflight.get("verdict") != "CLEAN"
        or product_preflight.get("launches") != ()
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0232_PRODUCT_PREEXISTING")
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=private_root / "demo",
        binding=binding,
        context=context,
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=resolved_compose,
    )
    lifecycle.admit_prestart()
    if lifecycle.runtime_descriptor != tracked_runtime:
        raise ValueError("Product v0.2.3.2 tracked Runtime descriptor differs")

    attempt_consumed = False
    execution: HealthyTrafficExecutionV0232 | None = None
    queue_before_sha256: str | None = None
    queue_after_sha256: str | None = None
    baseline_before_sha256: str | None = None
    baseline_after_sha256: str | None = None
    checkout_runtime: tuple[str, bool, int] | None = None
    cleanup_payload: dict[str, object] | None = None
    product_cleanup_payload: dict[str, object] | None = None
    source_after: ProductStateSourceV0232 | None = None
    product_after: ProductStateSourceV0232 | None = None
    live_error: BaseException | None = None

    def seal_consumed_failure(error: BaseException, *, stage: str) -> None:
        if not attempt_consumed:
            return
        public_attempt_exists = attempt_public_path.exists()
        failure_body: dict[str, Any] = {
            "schema_version": (
                "ecomsre.product.traffic-preflight-closure-failure.v0232"
                if public_attempt_exists
                else "ecomsre.product.traffic-preflight-blocked-attempt.v0232"
            ),
            "terminal": "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT",
            "attempt_ordinal": attempt_ordinal,
            "attempt_consumed": True,
            "safe_error_type": type(error).__name__,
            "stage": stage,
            "profile_sha256": profile.profile_sha256,
            "contract_sha256": contract.contract_sha256,
            "source_file_bindings": [
                item.model_dump(mode="json") for item in contract.source_file_bindings
            ],
            "flagd_bind_descriptor_sha256": (
                tracked_runtime.flagd_bind_descriptor_sha256
            ),
            "runtime_continuity_descriptor_sha256": (
                tracked_runtime.descriptor_sha256
            ),
            "resolved_compose_sha256": tracked_runtime.resolved_compose_sha256,
            "source_state_before_sha256": source_before.source_sha256,
            "source_state_after_sha256": (
                source_after.source_sha256 if source_after is not None else None
            ),
            "product_state_clone_sha256": product_clone_sha256,
            "product_state_before_sha256": product_before.source_sha256,
            "product_state_after_sha256": (
                product_after.source_sha256 if product_after is not None else None
            ),
            "demo_cleanup": cleanup_payload or {"verdict": "BLOCKED"},
            "product_cleanup": product_cleanup_payload or {"verdict": "BLOCKED"},
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
            "action_authority": "NONE",
        }
        if execution is not None:
            failure_body.update(
                execution_sha256=execution.execution_sha256,
                planned_transactions=execution.run.planned_transactions,
                completed_transactions=execution.run.completed_transactions,
                successful_transactions=execution.run.successful_transactions,
                failed_transactions=execution.run.failed_transactions,
            )
        failure_report = {
            **failure_body,
            "attempt_sha256": semantic_sha256_v22(failure_body),
        }
        write_private_json(
            private_root / "attempt-failure.json",
            failure_report,
            create_once=True,
        )
        public_failure_path = attempt_public_path
        progress_attempt_sha256 = failure_report["attempt_sha256"]
        closure_failure_sha256: str | None = None
        if public_attempt_exists:
            public_failure_path = closure_failure_public_path
            existing_attempt = _load_object(attempt_public_path)
            existing_attempt_sha256 = existing_attempt.get("attempt_sha256")
            if not isinstance(existing_attempt_sha256, str):
                raise ValueError(
                    "Product v0.2.3.2 existing public Attempt is not sealed"
                )
            progress_attempt_sha256 = existing_attempt_sha256
            closure_failure_sha256 = failure_report["attempt_sha256"]
        _write_public_create_once(public_failure_path, failure_report)
        _replace_public(
            root / "docs/analysis/product-v0232-progress.json",
            _blocked_progress(
                root,
                attempt_ordinal=attempt_ordinal,
                attempt_sha256=progress_attempt_sha256,
                closure_failure_sha256=closure_failure_sha256,
                base_progress=starting_progress,
            ),
        )

    def consume_attempt() -> None:
        nonlocal attempt_consumed
        write_private_json(
            private_root / "attempt-start.json",
            {
                "schema_version": "ecomsre.product.private-traffic-preflight-start.v0232",
                "attempt_ordinal": attempt_ordinal,
                "started_at": datetime.now(UTC).isoformat(),
                "profile_sha256": profile.profile_sha256,
                "contract_sha256": contract.contract_sha256,
                "runtime_continuity_descriptor_sha256": tracked_runtime.descriptor_sha256,
                "incident_count_before": product_before.source_counts.incident_count,
                "diagnosis_count_before": product_before.source_counts.diagnosis_count,
                "accepted_successor_incident_count": 0,
                "successor_diagnosis_count": 0,
                "action_authority": "NONE",
            },
            create_once=True,
        )
        attempt_consumed = True

    try:
        lifecycle.start(on_boundary_verified=consume_attempt)
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if lifecycle.rebound_authority != authority:
            raise ValueError("Product v0.2.3.2 fresh Runtime authority differs")
        checkout_runtime = _checkout_runtime(
            backend,
            run_id=f"product-v0232-traffic-preflight-{attempt_ordinal}",
        )
        queue_before = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=profile.queue_fault_flag,
        )
        queue_before_sha256 = queue_before.before_sha256
        baseline_before_sha256 = lifecycle.read_baseline_sha256()
        time.sleep(profile.stabilization_seconds)
        with HealthyTrafficRunnerV0232() as runner:
            execution = runner.run(
                endpoint=_ENDPOINT_V0232,
                profile=profile,
                contract=contract,
                role="PREFLIGHT",
            )
        write_private_json(
            private_root / "traffic-execution.json",
            execution.model_dump(mode="json"),
            create_once=True,
        )
    except BaseException as error:
        live_error = error
    finally:
        if attempt_consumed:
            cleanup_errors: list[tuple[str, BaseException]] = []
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
                cleanup = lifecycle.cleanup_owned(
                    baseline_unchanged=(
                        baseline_before_sha256 is not None
                        and baseline_before_sha256 == baseline_after_sha256
                    )
                )
                cleanup_payload = _cleanup_payload(cleanup)
            except BaseException as cleanup_error:
                cleanup_errors.append(("DEMO", cleanup_error))
            try:
                observed_product_cleanup = product_processes.cleanup_observation()
                database_owner_count_after = _database_owner_count(
                    product_data_root / "product.sqlite3"
                )
                product_cleanup_payload = {
                    **observed_product_cleanup,
                    "database_owner_count_before": database_owner_count_before,
                    "database_owner_count_after": database_owner_count_after,
                }
                if (
                    observed_product_cleanup.get("verdict") != "CLEAN"
                    or observed_product_cleanup.get("launches") != ()
                    or database_owner_count_after != 0
                ):
                    raise RuntimeError(
                        "BLOCKED_ECOMSRE_PRODUCT_V0232_PRODUCT_CLEANUP"
                    )
            except BaseException as cleanup_error:
                cleanup_errors.append(("PRODUCT", cleanup_error))
            if cleanup_errors:
                if live_error is None:
                    live_error = cleanup_errors[0][1]
                write_private_json(
                    private_root / "cleanup-failure.json",
                    {
                        "schema_version": (
                            "ecomsre.product.private-cleanup-failure.v0232"
                        ),
                        "failures": [
                            {
                                "scope": scope,
                                "safe_error_type": type(error).__name__,
                            }
                            for scope, error in cleanup_errors
                        ],
                    },
                    create_once=True,
                )

    try:
        source_after = _admit_source_state(source_product)
        product_after, observed_clone_sha256, observed_product_root = (
            _admit_clone_state(root)
        )
        if (
            source_after != source_before
            or product_after != product_before
            or observed_clone_sha256 != product_clone_sha256
            or observed_product_root != product_data_root
        ):
            raise RuntimeError("Product v0.2.3.2 Product state changed")
    except BaseException as state_error:
        if live_error is None:
            live_error = state_error
    if live_error is not None:
        seal_consumed_failure(
            live_error,
            stage="AFTER_TRAFFIC" if execution is not None else "BEFORE_TRAFFIC",
        )
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"
        ) from live_error
    try:
        if (
            not attempt_consumed
            or execution is None
            or checkout_runtime is None
            or queue_before_sha256 is None
            or queue_after_sha256 is None
            or baseline_before_sha256 is None
            or baseline_after_sha256 is None
            or cleanup_payload is None
            or product_cleanup_payload is None
            or source_after is None
            or product_after is None
        ):
            raise RuntimeError(
                "Product v0.2.3.2 traffic preflight closure is incomplete"
            )
        if (
            _sha256_file(root / "config/product-v0232/traffic/formal-profile.json")
            != formal_profile_file_sha256_before
        ):
            raise ValueError(
                "Product v0.2.3.2 formal profile changed during preflight"
            )
        attempt = TrafficPreflightAttemptV0232.build(
            attempt_ordinal=attempt_ordinal,
            changed_parameter=changed_parameter,
            changed_parameter_evidence_sha256=changed_parameter_evidence_sha256,
            execution=execution,
            source_file_bindings=contract.source_file_bindings,
            flagd_bind_descriptor_sha256=(
                tracked_runtime.flagd_bind_descriptor_sha256
            ),
            runtime_continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
            resolved_compose_sha256=tracked_runtime.resolved_compose_sha256,
            read_authority_sha256=authority.read_authority.authority_sha256,
            pilot_runtime_authority_sha256=authority.pilot_authority_sha256,
            checkout_state=checkout_runtime[0],
            checkout_healthy=checkout_runtime[1],
            checkout_restart_count=checkout_runtime[2],
            queue_before_sha256=queue_before_sha256,
            queue_after_sha256=queue_after_sha256,
            outer_baseline_before_sha256=baseline_before_sha256,
            outer_baseline_after_sha256=baseline_after_sha256,
            source_state_before_sha256=source_before.source_sha256,
            source_state_after_sha256=source_after.source_sha256,
            product_state_clone_sha256=product_clone_sha256,
            product_state_before_sha256=product_before.source_sha256,
            product_state_after_sha256=product_after.source_sha256,
            incident_count_before=product_before.source_counts.incident_count,
            incident_count_after=product_after.source_counts.incident_count,
            diagnosis_count_before=product_before.source_counts.diagnosis_count,
            diagnosis_count_after=product_after.source_counts.diagnosis_count,
            demo_cleanup=cleanup_payload,
            product_cleanup=product_cleanup_payload,
        )
        preflight: TrafficPreflightEvidenceV0232 | None = None
        if attempt.terminal == TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232:
            preflight = TrafficPreflightEvidenceV0232.build(
                attempt=attempt,
                formal_profile=formal_profile,
                campaign=campaign,
            )
            progress_payload = _updated_progress(
                root,
                attempt=attempt,
                preflight=preflight,
                campaign_sha256=campaign.campaign_sha256,
                base_progress=starting_progress,
            )
        else:
            progress_payload = _blocked_progress(
                root,
                attempt_ordinal=attempt_ordinal,
                attempt_sha256=attempt.attempt_sha256,
                base_progress=starting_progress,
            )
    except BaseException as closure_error:
        seal_consumed_failure(closure_error, stage="POST_CLEANUP_CLOSURE")
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"
        ) from closure_error

    try:
        _write_public_create_once(
            attempt_public_path,
            attempt.model_dump(mode="json"),
        )
        if preflight is not None:
            _write_public_create_once(
                preflight_public_path,
                preflight.model_dump(mode="json"),
            )
        _replace_public(
            root / "docs/analysis/product-v0232-progress.json",
            progress_payload,
        )
    except BaseException as persistence_error:
        seal_consumed_failure(persistence_error, stage="PUBLIC_PERSISTENCE")
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"
        ) from persistence_error
    return attempt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--predecessor-root", type=Path, required=True)
    parser.add_argument("--source-product-root", type=Path, required=True)
    parser.add_argument("--predecessor-private-acceptance", type=Path, required=True)
    parser.add_argument("--attempt", type=int, choices=(1, 2), required=True)
    parser.add_argument("--changed-parameter")
    parser.add_argument("--changed-parameter-evidence-sha256")
    arguments = parser.parse_args(argv)
    result = run_traffic_preflight_v0232(
        project_root=arguments.project_root,
        predecessor_root=arguments.predecessor_root,
        source_product_root=arguments.source_product_root,
        predecessor_private_acceptance=arguments.predecessor_private_acceptance,
        attempt_ordinal=arguments.attempt,
        changed_parameter=arguments.changed_parameter,
        changed_parameter_evidence_sha256=(
            arguments.changed_parameter_evidence_sha256
        ),
    )
    print(result.model_dump_json())
    return 0 if result.terminal == TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V0232 else 2


if __name__ == "__main__":
    raise SystemExit(main())
