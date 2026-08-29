"""Bounded Product v0.2.1 live baseline-readiness campaign."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import time
from typing import Any, Mapping, cast

from fastapi.testclient import TestClient
import httpx

from ecomsre.dta_v2.read_only_smoke import _SandboxOwnedSmokeLifecycle
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.app import create_app
from ecomsre.product.baselines import BASELINE_REQUIRED_COMPLETE_SOURCE_POLICY_V021
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    PilotBaselineBindingV021,
    PilotBaselineReadinessProfileV021,
    ReadinessAttemptDispositionV021,
    ReadinessChangeParameterV021,
    ReadinessFailureDomainV021,
    ReadinessSemanticInputsV021,
    build_readiness_attempt_signature_v021,
    render_public_readiness_markdown_v021,
    verify_queue_default_v021,
    write_pilot_baseline_binding_v021,
)
from ecomsre.product.pilot.live_calibration_v02 import (
    _authority_inputs,
    _connector_health,
    _request_json,
    _run_product_job,
    _runtime_services,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.pilot.readiness_attempts_v021 import (
    READINESS_BLOCKED_V021,
    READINESS_PASS_V021,
    READINESS_REPAIR_REQUIRED_V021,
    PublicReadinessAttemptV021,
    ReadinessAttemptFinalV021,
    ReadinessAttemptStartV021,
    load_public_readiness_attempt_v021,
    readiness_attempt_ledger_state_v021,
    reserve_readiness_attempt_v021,
    write_private_bound_json_v021,
    write_public_readiness_attempt_v021,
    write_readiness_attempt_final_v021,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre_live_sandbox.environment import ExactCommandRunner
from ecomsre_live_sandbox.contracts import ensure_private_directory
from scripts.ci.verify_product_v021_history import verify_product_v021_history
from scripts.ci.verify_product_v021_increment1 import verify_product_v021_increment1


PINNED_UPSTREAM_V021 = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
READINESS_CONTRACT_PASS_V021 = (
    "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_CONTRACT_PASS"
)


def _load_object_v021(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _exact_directory_v021(
    repository_root: Path,
    relative_path: str,
    *,
    create: bool,
    private: bool = False,
) -> Path:
    root = Path(repository_root)
    if not root.is_absolute():
        raise ValueError("readiness exact root must be absolute")
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("readiness exact relative path differs")
    root_metadata = os.lstat(root)
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise ValueError("readiness exact root is not a regular directory")
    current = root
    for part in relative.parts:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if not create:
                raise ValueError("readiness exact directory is absent") from None
            current.mkdir(mode=0o700, parents=False, exist_ok=False)
            current.chmod(0o700)
            metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("readiness exact path contains a symlink")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("readiness exact path component is not a directory")
    if private:
        ensure_private_directory(current)
    return current


def _require_absent_target_v021(
    repository_root: Path,
    relative_path: str,
) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or len(relative.parts) < 2:
        raise ValueError("readiness output target differs")
    parent = _exact_directory_v021(
        repository_root,
        relative.parent.as_posix(),
        create=False,
    )
    target = parent / relative.name
    try:
        os.lstat(target)
    except FileNotFoundError:
        return target
    raise ValueError("readiness output target already exists or is a symlink")


def _require_regular_or_absent_target_v021(
    repository_root: Path,
    relative_path: str,
) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or len(relative.parts) < 2:
        raise ValueError("readiness output target differs")
    parent = _exact_directory_v021(
        repository_root,
        relative.parent.as_posix(),
        create=False,
    )
    target = parent / relative.name
    try:
        metadata = os.lstat(target)
    except FileNotFoundError:
        return target
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("readiness output target is not a regular file")
    return target


def _preflight_readiness_roots_v021(
    repository_root: Path,
    profile: PilotBaselineReadinessProfileV021,
) -> tuple[Path, Path]:
    root = Path(repository_root)
    _exact_directory_v021(root, "docs/analysis", create=False)
    _exact_directory_v021(
        root,
        "config/product-v021/live-pilot",
        create=False,
    )
    _require_absent_target_v021(
        root,
        "config/product-v021/live-pilot/baseline-binding.json",
    )
    for name in (
        "product-v021-baseline-readiness.json",
        "product-v021-baseline-readiness.md",
        "product-v021-progress.json",
    ):
        _require_regular_or_absent_target_v021(root, f"docs/analysis/{name}")
        _require_absent_target_v021(
            root,
            f"docs/analysis/.{name}.product-v021.tmp",
        )
    private_root = _exact_directory_v021(
        root,
        profile.private_root,
        create=True,
        private=True,
    )
    product_root = _exact_directory_v021(
        root,
        profile.public_root,
        create=True,
        private=True,
    )
    return private_root, product_root


def verify_baseline_readiness_contract_v021(
    repository_root: Path,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    history = verify_product_v021_history(root)
    increment1 = verify_product_v021_increment1(root)
    profile = PilotBaselineReadinessProfileV021.model_validate(
        _load_object_v021(
            root / "config/product-v021/baseline-readiness/profile.json"
        )
    )
    upstream = ExactCommandRunner().run(
        ("git", "rev-parse", "HEAD"),
        cwd=root / "third_party/opentelemetry-demo",
        timeout_seconds=30,
    ).stdout.strip()
    if upstream != PINNED_UPSTREAM_V021:
        raise ValueError("pinned OTel Demo commit differs")
    return {
        "terminal": READINESS_CONTRACT_PASS_V021,
        "pinned_upstream": upstream,
        "profile_sha256": profile.profile_sha256,
        "candidate_services": profile.candidate_services,
        "maximum_changed_attempts": profile.maximum_changed_attempts,
        "history_status": history["status"],
        "increment1_status": increment1["status"],
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }


def _atomic_write_text_v021(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v021.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _build_environment_payload_v021(
    repository_root: Path,
    *,
    candidate_services: tuple[str, ...],
    runtime_authority_sha256: str,
) -> dict[str, Any]:
    payload = _load_object_v021(
        repository_root / "examples/product/environment.otel-demo.json"
    )
    payload["name"] = "product-v021-baseline-readiness"
    payload["description"] = (
        "Fresh read-only Product environment for bounded v0.2.1 baseline readiness."
    )
    connectors = payload.get("connector_configs")
    if not isinstance(connectors, list):
        raise ValueError("Product environment connector configuration differs")
    normalized: list[dict[str, Any]] = []
    for raw in connectors:
        if not isinstance(raw, dict):
            raise ValueError("Product connector entry differs")
        item = json.loads(json.dumps(raw))
        endpoint = item.get("endpoint")
        if isinstance(endpoint, str):
            item["endpoint"] = endpoint.replace(
                "host.docker.internal", "127.0.0.1"
            )
        settings = item.get("settings")
        if item.get("kind") == "OPENSEARCH" and isinstance(settings, dict):
            settings["severity_filter"] = []
            settings["message_projection_policy"] = "OBSERVER_SYMPTOM_V1"
        if item.get("kind") == "HTTP_HEALTH" and isinstance(settings, dict):
            services = settings.get("services")
            if isinstance(services, list):
                for service in services:
                    if isinstance(service, dict) and isinstance(
                        service.get("health_url"), str
                    ):
                        service["health_url"] = service["health_url"].replace(
                            "host.docker.internal", "127.0.0.1"
                        )
        normalized.append(item)
    normalized.append(
        {
            "name": "pilot-runtime",
            "kind": "PILOT_RUNTIME",
            "endpoint": None,
            "settings": {
                "snapshot_ref": "pilot/runtime-readiness.json",
                "authority_sha256": runtime_authority_sha256,
                "maximum_age_seconds": 600,
            },
            "credential_refs": {},
        }
    )
    payload["connector_configs"] = normalized
    payload["explicit_service_catalog"] = list(candidate_services)
    return payload


def _run_baseline_job_with_audit_v021(
    client: TestClient,
    settings: ProductSettingsV1,
    *,
    environment_id: str,
    profile: PilotBaselineReadinessProfileV021,
    attempt_number: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    queued = _request_json(
        client,
        "POST",
        f"/v1/environments/{environment_id}/baseline-jobs",
        payload={
            "build_policy": profile.build_policy.model_dump(mode="json"),
            "candidate_services": list(profile.candidate_services),
            "activate": True,
        },
    )
    job_id = str(queued["job_id"])
    if not run_one_job(
        settings,
        worker_id=f"product-v021-readiness-baseline-{attempt_number}",
    ):
        raise RuntimeError("Product worker did not claim the readiness baseline job")
    job = _request_json(client, "GET", f"/v1/jobs/{job_id}")
    audit: dict[str, Any] | None
    response = client.get(
        f"/v1/environments/{environment_id}/baseline-readiness"
    )
    audit = (
        cast(dict[str, Any], response.json())
        if response.status_code == 200 and isinstance(response.json(), dict)
        else None
    )
    return job, audit


def _candidate_identity_binding_v021(
    verification: Mapping[str, object],
    candidate_services: tuple[str, ...],
) -> tuple[tuple[str, ...], str]:
    identity = verification.get("service_identity_map")
    services = identity.get("services") if isinstance(identity, dict) else None
    identity_sha256 = (
        identity.get("identity_sha256") if isinstance(identity, dict) else None
    )
    if not isinstance(services, list) or not isinstance(identity_sha256, str):
        raise RuntimeError("Product service identity map is incomplete")
    by_logical = {
        str(item.get("logical_service")): str(item.get("service_id"))
        for item in services
        if isinstance(item, dict)
    }
    if not set(candidate_services).issubset(by_logical):
        raise RuntimeError("Product readiness candidate mapping is incomplete")
    return (
        tuple(sorted(by_logical[name] for name in candidate_services)),
        identity_sha256,
    )


def _build_readiness_semantic_inputs_v021(
    repository_root: Path,
    profile: PilotBaselineReadinessProfileV021,
) -> ReadinessSemanticInputsV021:
    environment = _load_object_v021(
        repository_root / "examples/product/environment.otel-demo.json"
    )
    connectors = environment.get("connector_configs")
    if not isinstance(connectors, list):
        raise ValueError("Product environment connector configuration differs")
    query_templates: list[dict[str, object]] = []
    for raw in connectors:
        if not isinstance(raw, dict):
            raise ValueError("Product connector entry differs")
        settings = raw.get("settings")
        templates = settings.get("query_templates") if isinstance(settings, dict) else None
        query_templates.append(
            {
                "name": raw.get("name"),
                "kind": raw.get("kind"),
                "query_templates": templates if isinstance(templates, dict) else {},
            }
        )
    policy = profile.build_policy
    traffic = profile.healthy_traffic_profile
    return ReadinessSemanticInputsV021.build(
        profile_sha256=profile.profile_sha256,
        candidate_services=profile.candidate_services,
        build_mode=policy.mode.value,
        lookback_seconds=policy.lookback_seconds,
        window_count=policy.window_count,
        warmup_seconds=policy.warmup_seconds,
        stabilization_seconds=profile.stabilization_seconds,
        baseline_accumulation_seconds=profile.baseline_accumulation_seconds,
        minimum_successful_windows=policy.minimum_successful_windows,
        healthy_traffic_maximum_request_count=(
            traffic.maximum_request_count
        ),
        healthy_traffic_request_seed=traffic.request_seed,
        healthy_traffic_error_budget=traffic.error_budget,
        healthy_traffic_requests_per_second=traffic.requests_per_second,
        connector_query_bindings_sha256=semantic_sha256_v22(
            profile.connector_query_bindings
        ),
        connector_query_templates_sha256=semantic_sha256_v22(query_templates),
        service_alias_mapping_sha256=semantic_sha256_v22(
            environment.get("service_identity_policy")
        ),
        required_source_policy_id=(
            BASELINE_REQUIRED_COMPLETE_SOURCE_POLICY_V021
        ),
    )


def _rejection_reason_codes_v021(audit: Mapping[str, object] | None) -> tuple[str, ...]:
    if audit is None:
        return ()
    windows = audit.get("windows")
    if not isinstance(windows, list):
        return ()
    reasons = {
        str(reason)
        for window in windows
        if isinstance(window, dict)
        for reason in window.get("rejection_reason_codes", [])
        if isinstance(reason, str)
    }
    return tuple(sorted(reasons))


def _readiness_pass_preconditions_v021(
    *,
    safe_error: BaseException | None,
    baseline: Mapping[str, object] | None,
    audit: Mapping[str, object] | None,
    verification: Mapping[str, object] | None,
    identity_sha256: str | None,
    connector_configuration_sha256: str | None,
    capability_matrix_sha256: str | None,
    runtime_binding_sha256: str | None,
    api_restart_verified: bool,
    worker_restart_verified: bool,
    queue_default_unchanged: bool,
    healthy_traffic_stopped: bool,
    outer_baseline_restored: bool,
    cleanup_status: str,
) -> bool:
    """Fail closed over every Goal-owned readiness PASS precondition."""

    windows = audit.get("windows") if audit is not None else None
    accepted_windows = (
        audit.get("accepted_window_count") if audit is not None else None
    )
    no_truncated_queries = isinstance(windows, list) and all(
        isinstance(window, dict)
        and isinstance(window.get("source_results"), list)
        and all(
            isinstance(result, dict) and result.get("truncated") is False
            for result in window["source_results"]
        )
        for window in windows
    )
    bound_sha256s = (
        identity_sha256,
        connector_configuration_sha256,
        capability_matrix_sha256,
        runtime_binding_sha256,
    )
    return (
        safe_error is None
        and baseline is not None
        and baseline.get("active") is True
        and audit is not None
        and audit.get("scheduled_window_count") == 5
        and isinstance(windows, list)
        and len(windows) == 5
        and type(accepted_windows) is int
        and accepted_windows >= 4
        and audit.get("final_builder_would_pass") is True
        and no_truncated_queries
        and verification is not None
        and all(
            isinstance(value, str) and len(value) == 64
            for value in bound_sha256s
        )
        and api_restart_verified
        and worker_restart_verified
        and queue_default_unchanged
        and healthy_traffic_stopped
        and outer_baseline_restored
        and cleanup_status == "CLEAN"
    )


def _verify_public_attempt_history_v021(
    repository_root: Path,
    starts: tuple[ReadinessAttemptStartV021, ...],
    finals: tuple[ReadinessAttemptFinalV021, ...],
) -> None:
    if len(starts) != len(finals):
        raise ValueError("a prior readiness run is unfinished")
    analysis_root = repository_root / "docs/analysis"
    actual_paths = tuple(
        sorted(
            analysis_root.glob("product-v021-baseline-readiness-attempt-*.json")
        )
    )
    expected_paths = tuple(
        analysis_root / f"product-v021-baseline-readiness-attempt-{number}.json"
        for number in range(1, len(starts) + 1)
    )
    if actual_paths != expected_paths:
        raise ValueError("public readiness attempt file set differs")
    for start, final in zip(starts, finals, strict=True):
        public = load_public_readiness_attempt_v021(
            repository_root
            / "docs/analysis"
            / f"product-v021-baseline-readiness-attempt-{start.run_number}.json"
        )
        if (
            public.report_sha256 != final.public_attempt_report_sha256
            or public.run_number != start.run_number
            or public.changed_attempt_number != start.changed_attempt_number
            or public.attempt_signature_sha256
            != start.signature.attempt_signature_sha256
            or public.changed_parameter is not start.signature.changed_parameter
            or public.terminal != final.terminal
            or public.disposition is not final.disposition
            or public.audit_sha256 != final.audit_sha256
        ):
            raise ValueError("public readiness attempt history binding differs")


def _write_public_readiness_v021(
    *,
    repository_root: Path,
    terminal: str,
    observed_at: datetime,
    normalized_attempt: PublicReadinessAttemptV021,
) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    analysis_root = root / "docs/analysis"
    if analysis_root.is_symlink() or not analysis_root.is_dir():
        raise ValueError("Product public analysis root is not a regular directory")
    attempts: list[PublicReadinessAttemptV021] = []
    for path in sorted(
        analysis_root.glob("product-v021-baseline-readiness-attempt-*.json")
    ):
        attempts.append(load_public_readiness_attempt_v021(path))
    if tuple(item.run_number for item in attempts) != tuple(
        range(1, len(attempts) + 1)
    ) or not attempts or attempts[-1] != normalized_attempt:
        raise ValueError("public readiness attempt sequence differs")
    changed_attempt_count = max(item.changed_attempt_number for item in attempts)
    payload: dict[str, object] = {
        "schema_version": "ecomsre.product.baseline-readiness-result.v021",
        "terminal": terminal,
        "observed_at": observed_at.isoformat(),
        "readiness_run_count": len(attempts),
        "readiness_attempt_count": changed_attempt_count,
        "infrastructure_replacement_count": sum(
            item.infrastructure_replacement for item in attempts
        ),
        "attempts": [item.model_dump(mode="json") for item in attempts],
        "latest_attempt": normalized_attempt.model_dump(mode="json"),
        "fault_attempt_count": 0,
        "profile_calibration_iteration_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    payload["result_sha256"] = semantic_sha256_v22(payload)
    _atomic_write_text_v021(
        analysis_root / "product-v021-baseline-readiness.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    markdown = render_public_readiness_markdown_v021(payload)
    _atomic_write_text_v021(
        analysis_root / "product-v021-baseline-readiness.md",
        markdown,
    )
    progress: dict[str, object] = {
        "schema_version": "ecomsre.product.v021.progress.v1",
        "goal_version": (
            "ecomsre-product-v021-live-baseline-knowledge-loop-successor-v1"
        ),
        "branch": "codex/product-v021-baseline-readiness-successor",
        "increment": 2,
        "terminal": terminal,
        "baseline_readiness_attempt_count": changed_attempt_count,
        "baseline_readiness_run_count": len(attempts),
        "infrastructure_replacement_count": sum(
            item.infrastructure_replacement for item in attempts
        ),
        "profile_calibration_iteration_count": 0,
        "fault_attempt_count": 0,
        "accepted_positive_episode_count": 0,
        "heldout_recurrence_count": 0,
        "current_human_gate": "NOT_REACHED",
        "next_boundary": (
            "PROFILE_CALIBRATION"
            if terminal == READINESS_PASS_V021
            else (
                "READINESS_REPAIR"
                if terminal == READINESS_REPAIR_REQUIRED_V021
                else "STOPPED_BASELINE_READINESS"
            )
        ),
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    progress["progress_sha256"] = semantic_sha256_v22(progress)
    _atomic_write_text_v021(
        analysis_root / "product-v021-progress.json",
        json.dumps(progress, indent=2, sort_keys=True) + "\n",
    )
    return payload


def run_live_baseline_readiness_v021(
    *,
    repository_root: Path,
    changed_parameter: ReadinessChangeParameterV021 | None = None,
    infrastructure_replacement_for_run_id: str | None = None,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    verify_baseline_readiness_contract_v021(root)
    profile = PilotBaselineReadinessProfileV021.model_validate(
        _load_object_v021(
            root / "config/product-v021/baseline-readiness/profile.json"
        )
    )
    private_campaign_root, product_campaign_root = (
        _preflight_readiness_roots_v021(root, profile)
    )
    starts, finals = readiness_attempt_ledger_state_v021(private_campaign_root)
    _verify_public_attempt_history_v021(root, starts, finals)
    _require_absent_target_v021(
        root,
        (
            "docs/analysis/product-v021-baseline-readiness-attempt-"
            f"{len(starts) + 1}.json"
        ),
    )
    semantics = _build_readiness_semantic_inputs_v021(root, profile)
    if infrastructure_replacement_for_run_id is not None:
        if changed_parameter is not None:
            raise ValueError("infrastructure replacement cannot declare changed inputs")
        if not starts:
            raise ValueError("readiness replacement lacks a prior run")
        signature = starts[-1].signature
        if semantics != signature.semantic_inputs:
            raise ValueError("readiness replacement inputs differ")
    else:
        if not starts:
            selected_change = changed_parameter or ReadinessChangeParameterV021.INITIAL
            prior_audit_sha256 = None
            prior_reasons: tuple[str, ...] = ()
        else:
            if (
                not finals
                or changed_parameter is None
                or changed_parameter is ReadinessChangeParameterV021.INITIAL
            ):
                raise ValueError("changed readiness run requires a declared parameter")
            selected_change = changed_parameter
            prior_audit_sha256 = finals[-1].audit_sha256
            prior_reasons = finals[-1].rejection_reason_codes
        signature = build_readiness_attempt_signature_v021(
            semantic_inputs=semantics,
            changed_parameter=selected_change,
            prior_audit_sha256=prior_audit_sha256,
            prior_rejection_reason_codes=prior_reasons,
        )
    observed_at = datetime.now(UTC)
    run_id = observed_at.strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
    start = reserve_readiness_attempt_v021(
        private_root=private_campaign_root,
        signature=signature,
        run_id=run_id,
        started_at=observed_at.isoformat(),
        infrastructure_replacement_for_run_id=(
            infrastructure_replacement_for_run_id
        ),
    )
    run_number = start.run_number
    changed_attempt_number = start.changed_attempt_number
    private_root = private_campaign_root / "runs" / run_id
    product_data_root = product_campaign_root / run_id
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private_root,
        stabilization_seconds=profile.stabilization_seconds,
    )

    environment_id: str | None = None
    baseline: dict[str, Any] | None = None
    audit: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    baseline_job: dict[str, Any] | None = None
    traffic_result: dict[str, Any] | None = None
    queue_before: dict[str, Any] | None = None
    queue_after: dict[str, Any] | None = None
    runtime_binding_sha256: str | None = None
    identity_sha256: str | None = None
    connector_configuration_sha256: str | None = None
    safe_error: BaseException | None = None
    failure_before_cleanup_sha256: str | None = None
    failure_domain = ReadinessFailureDomainV021.NONE
    interrupted = False
    stage = "RESERVED"
    outer_baseline_restored = False
    cleanup_status = "UNKNOWN_BLOCKED"
    api_restart_verified = False
    worker_restart_verified = False
    try:
        lifecycle.admit()
        stage = "ADMITTED"
        if lifecycle.flag_file is None:
            raise RuntimeError("owned lifecycle did not bind the queue flag file")
        initial = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=0,
        )
        queue_before = initial.model_dump(mode="json")
        stage = "DEFAULT_VERIFIED"
        lifecycle.start()
        stage = "STARTED"
        lifecycle.wait_ready()
        stage = "READY"
        backend = cast(LocalSandboxReadBackend, lifecycle.authorize_reads())
        stage = "READS_AUTHORIZED"
        authority_inputs = _authority_inputs(backend)
        prebound = PilotRuntimeAuthorityV02.build(
            environment_id="env-" + "0" * 24,
            allowed_logical_services=profile.candidate_services,
            profile_sha256=profile.profile_sha256,
            **authority_inputs,
        )
        runtime_binding_sha256 = prebound.connector_binding_sha256
        authority_path = product_data_root / "pilot/runtime-authority.json"
        settings = ProductSettingsV1(
            data_root=product_data_root,
            pilot_runtime_authority_path=authority_path,
            connector_timeout_seconds=15,
            job_lease_seconds=900,
        )
        environment_payload = _build_environment_payload_v021(
            root,
            candidate_services=profile.candidate_services,
            runtime_authority_sha256=prebound.connector_binding_sha256,
        )
        connector_configuration_sha256 = semantic_sha256_v22(
            environment_payload["connector_configs"]
        )
        with TestClient(create_app(settings)) as client:
            ready = _request_json(client, "GET", "/readyz")
            if ready.get("status") != "ready":
                raise RuntimeError("in-process Product API is not ready")
            environment = _request_json(
                client,
                "POST",
                "/v1/environments",
                payload=environment_payload,
            )
            environment_id = str(environment["environment_id"])
            authority = PilotRuntimeAuthorityV02.build(
                environment_id=environment_id,
                allowed_logical_services=profile.candidate_services,
                profile_sha256=profile.profile_sha256,
                **authority_inputs,
            )
            if authority.connector_binding_sha256 != prebound.connector_binding_sha256:
                raise RuntimeError("readiness Runtime binding changed during environment bind")
            write_pilot_runtime_authority_v02(authority_path, authority)
            runtime_services, runtime_drift = _runtime_services(
                backend,
                run_id=secrets.token_hex(16),
            )
            if runtime_drift:
                raise RuntimeError("readiness Runtime baseline contains owned drift")
            snapshot = PilotRuntimeSnapshotV02.build(
                environment_id=environment_id,
                authority_sha256=authority.connector_binding_sha256,
                observed_at=datetime.now(UTC),
                services=runtime_services,
            )
            write_pilot_runtime_snapshot_v02(
                product_data_root / "pilot/runtime-readiness.json",
                snapshot,
            )
            _, verification = _run_product_job(
                client,
                settings,
                path=f"/v1/environments/{environment_id}/verify-jobs",
                worker_id=f"product-v021-readiness-verify-{run_number}",
            )
            _connector_health(verification)
            _candidate_ids, identity_sha256 = _candidate_identity_binding_v021(
                verification,
                profile.candidate_services,
            )
            stage = "CONNECTORS_VERIFIED"
            with httpx.Client() as traffic_client:
                traffic = BoundedHealthyCheckoutTrafficV021(
                    client=traffic_client
                ).run(
                    endpoint="http://127.0.0.1:18080/api/checkout",
                    profile=profile.healthy_traffic_profile,
                )
            traffic_result = traffic.model_dump(mode="json")
            if (
                traffic.attempted != profile.healthy_traffic_profile.maximum_request_count
                or traffic.stopped_on_error_budget
            ):
                raise RuntimeError("healthy readiness traffic did not complete its bound")
            remaining_accumulation = max(
                0.0,
                profile.baseline_accumulation_seconds - traffic.duration_seconds,
            )
            if remaining_accumulation:
                time.sleep(remaining_accumulation)
            stage = "TRAFFIC_STOPPED"
            baseline_job, audit = _run_baseline_job_with_audit_v021(
                client,
                settings,
                environment_id=environment_id,
                profile=profile,
                attempt_number=run_number,
            )
            if audit is not None:
                stage = "AUDIT_CAPTURED"
            if baseline_job.get("status") != "SUCCEEDED" or not isinstance(
                baseline_job.get("result"), dict
            ):
                raise RuntimeError(
                    "Product readiness baseline job failed: "
                    f"{baseline_job.get('safe_error_code')}"
                )
            baseline = cast(dict[str, Any], baseline_job["result"])
            if baseline.get("active") is not True:
                raise RuntimeError("Product readiness baseline is not active")
            if audit is None or audit.get("final_builder_would_pass") is not True:
                raise RuntimeError("Product readiness audit did not pass")
        with TestClient(create_app(settings)) as restarted:
            restart_job = _request_json(
                restarted,
                "POST",
                f"/v1/environments/{environment_id}/verify-jobs",
            )
            restart_job_id = str(restart_job["job_id"])
            if not run_one_job(
                settings,
                worker_id=f"product-v021-readiness-restart-{run_number}",
            ):
                raise RuntimeError("fresh Product worker did not claim restart verification")
            restarted_job = _request_json(
                restarted,
                "GET",
                f"/v1/jobs/{restart_job_id}",
            )
            if restarted_job.get("status") != "SUCCEEDED":
                raise RuntimeError("fresh Product worker restart verification failed")
            worker_restart_verified = True
            listed = _request_json(
                restarted,
                "GET",
                f"/v1/environments/{environment_id}/baselines",
            )
            items = listed.get("items")
            current = (
                next(
                    (
                        item
                        for item in items
                        if isinstance(item, dict)
                        and item.get("baseline_id") == baseline.get("baseline_id")
                    ),
                    None,
                )
                if isinstance(items, list)
                else None
            )
            if not isinstance(current, dict) or current.get("active") is not True:
                raise RuntimeError("active baseline did not survive Product API restart")
            rebound = _request_json(
                restarted,
                "GET",
                f"/v1/baselines/{baseline['baseline_id']}/window-audit",
            )
            if rebound.get("audit_sha256") != audit.get("audit_sha256"):
                raise RuntimeError("readiness audit did not survive Product API restart")
            api_restart_verified = True
        final_queue = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=0,
            expected_sha256=initial.before_sha256,
        )
        queue_after = final_queue.model_dump(mode="json")
        outer_baseline_restored = True
        stage = "RESTORATION_VERIFIED"
    except BaseException as error:
        safe_error = error
        interrupted = not isinstance(error, Exception)
        failure_domain = (
            ReadinessFailureDomainV021.INTERRUPTED
            if interrupted
            else (
                ReadinessFailureDomainV021.INFRASTRUCTURE_STARTUP
                if stage
                in {
                    "RESERVED",
                    "ADMITTED",
                    "DEFAULT_VERIFIED",
                    "STARTED",
                    "READY",
                    "READS_AUTHORIZED",
                }
                else ReadinessFailureDomainV021.CAMPAIGN
            )
        )
        try:
            failure_before_cleanup_sha256 = write_private_bound_json_v021(
                private_root / "report/failure-before-cleanup.json",
                {
                    "schema_version": (
                        "ecomsre.product.baseline-readiness-failure-before-cleanup.v021"
                    ),
                    "run_number": run_number,
                    "changed_attempt_number": changed_attempt_number,
                    "run_id": run_id,
                    "failed_at": datetime.now(UTC).isoformat(),
                    "stage": stage,
                    "failure_domain": failure_domain.value,
                    "safe_error_type": type(error).__name__,
                    "safe_error": str(error)[:1000],
                    "environment_id": environment_id,
                    "baseline_job": baseline_job,
                    "audit": audit,
                    "cleanup_started": False,
                },
            )
        except BaseException as evidence_error:
            safe_error = evidence_error
            failure_domain = ReadinessFailureDomainV021.EVIDENCE
        if lifecycle.flag_file is not None and queue_before is not None:
            try:
                initial_sha = str(queue_before["before_sha256"])
                final_queue = verify_queue_default_v021(
                    lifecycle.flag_file,
                    expected_default_value=0,
                    expected_sha256=initial_sha,
                )
                queue_after = final_queue.model_dump(mode="json")
                outer_baseline_restored = True
            except BaseException:
                outer_baseline_restored = False
    finally:
        try:
            cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=outer_baseline_restored
            )
            cleanup_status = cleanup.verdict
            if cleanup_status != "CLEAN" and safe_error is None:
                safe_error = RuntimeError("owned Demo cleanup did not close CLEAN")
                failure_domain = ReadinessFailureDomainV021.CLEANUP
        except BaseException as error:
            cleanup_status = "BLOCKED"
            if safe_error is None:
                safe_error = error
            failure_domain = ReadinessFailureDomainV021.CLEANUP

    accepted_windows = (
        int(audit.get("accepted_window_count", 0)) if audit is not None else 0
    )
    queue_default_unchanged = (
        queue_before is not None
        and queue_after is not None
        and queue_before.get("default_value") == 0
        and queue_after.get("default_value") == 0
        and queue_before.get("before_sha256") == queue_after.get("after_sha256")
    )
    traffic_stopped = (
        traffic_result is not None
        and traffic_result.get("attempted")
        == profile.healthy_traffic_profile.maximum_request_count
        and traffic_result.get("stopped_on_error_budget") is False
    )
    capability = (
        verification.get("capability_matrix")
        if verification is not None
        else None
    )
    capability_matrix_sha256 = (
        capability.get("capability_sha256")
        if isinstance(capability, dict)
        else None
    )
    usable_audit = (
        audit is not None
        and audit.get("scheduled_window_count") == 5
        and isinstance(audit.get("audit_sha256"), str)
        and len(audit.get("windows", [])) == 5
    )
    rejection_reason_codes = _rejection_reason_codes_v021(audit)
    pass_ready = _readiness_pass_preconditions_v021(
        safe_error=safe_error,
        baseline=baseline,
        audit=audit,
        verification=verification,
        identity_sha256=identity_sha256,
        connector_configuration_sha256=connector_configuration_sha256,
        capability_matrix_sha256=capability_matrix_sha256,
        runtime_binding_sha256=runtime_binding_sha256,
        api_restart_verified=api_restart_verified,
        worker_restart_verified=worker_restart_verified,
        queue_default_unchanged=queue_default_unchanged,
        healthy_traffic_stopped=traffic_stopped,
        outer_baseline_restored=outer_baseline_restored,
        cleanup_status=cleanup_status,
    )
    safe_continuation = (
        failure_before_cleanup_sha256 is not None
        and queue_default_unchanged
        and outer_baseline_restored
        and cleanup_status == "CLEAN"
        and not interrupted
    )
    if pass_ready:
        disposition = ReadinessAttemptDispositionV021.PASS
        failure_domain = ReadinessFailureDomainV021.NONE
    elif (
        usable_audit
        and rejection_reason_codes
        and safe_continuation
        and changed_attempt_number < 2
    ):
        disposition = ReadinessAttemptDispositionV021.TARGETED_REPAIR_ELIGIBLE
    elif (
        not usable_audit
        and safe_continuation
        and failure_domain is ReadinessFailureDomainV021.INFRASTRUCTURE_STARTUP
        and start.infrastructure_replacement_for_run_id is None
    ):
        disposition = (
            ReadinessAttemptDispositionV021.INFRASTRUCTURE_REPLACEMENT_ELIGIBLE
        )
    else:
        disposition = ReadinessAttemptDispositionV021.BLOCKED
    terminal = (
        READINESS_PASS_V021
        if disposition is ReadinessAttemptDispositionV021.PASS
        else (
            READINESS_REPAIR_REQUIRED_V021
            if disposition
            in {
                ReadinessAttemptDispositionV021.TARGETED_REPAIR_ELIGIBLE,
                ReadinessAttemptDispositionV021.INFRASTRUCTURE_REPLACEMENT_ELIGIBLE,
            }
            else READINESS_BLOCKED_V021
        )
    )
    binding: PilotBaselineBindingV021 | None = None
    if pass_ready:
        assert (
            baseline is not None
            and audit is not None
            and verification is not None
            and environment_id is not None
            and identity_sha256 is not None
            and connector_configuration_sha256 is not None
            and capability_matrix_sha256 is not None
            and runtime_binding_sha256 is not None
        )
        binding = PilotBaselineBindingV021.build(
            environment_id=environment_id,
            product_data_root=product_data_root.relative_to(root).as_posix(),
            readiness_private_root=private_root.relative_to(root).as_posix(),
            queue_flag_ref="runtime/flagd/demo.flagd.json",
            runtime_snapshot_ref="pilot/runtime-readiness.json",
            baseline_id=baseline["baseline_id"],
            baseline_sha256=baseline["baseline_sha256"],
            build_policy=profile.build_policy.model_dump(mode="json"),
            accepted_window_ordinals=[
                item.get("window_ordinal")
                for item in audit.get("windows", [])
                if isinstance(item, dict) and item.get("accepted") is True
            ],
            source_coverage_matrix=audit["coverage_matrix"],
            service_identity_map_sha256=identity_sha256,
            connector_configuration_sha256=connector_configuration_sha256,
            capability_matrix_sha256=capability_matrix_sha256,
            runtime_authority_sha256=runtime_binding_sha256,
            healthy_traffic_profile_sha256=semantic_sha256_v22(
                profile.healthy_traffic_profile.model_dump(mode="json")
            ),
            audit_sha256=audit["audit_sha256"],
            parity_sha256=audit["parity_sha256"],
            frozen_at=datetime.now(UTC),
        )
    private_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.private-baseline-readiness-attempt.v021",
        "terminal": terminal,
        "disposition": disposition.value,
        "failure_domain": failure_domain.value,
        "run_number": run_number,
        "changed_attempt_number": changed_attempt_number,
        "attempt_signature_sha256": signature.attempt_signature_sha256,
        "changed_parameter": signature.changed_parameter.value,
        "infrastructure_replacement_for_run_id": (
            start.infrastructure_replacement_for_run_id
        ),
        "run_id": run_id,
        "observed_at": datetime.now(UTC).isoformat(),
        "environment_id": environment_id,
        "verification": verification,
        "baseline_job": baseline_job,
        "baseline": baseline,
        "audit": audit,
        "traffic_result": traffic_result,
        "queue_before": queue_before,
        "queue_after": queue_after,
        "api_restart_verified": api_restart_verified,
        "worker_restart_verified": worker_restart_verified,
        "outer_baseline_restored": outer_baseline_restored,
        "owned_demo_cleanup": cleanup_status,
        "failure_before_cleanup_sha256": failure_before_cleanup_sha256,
        "interrupted": interrupted,
        "safe_error_type": None if safe_error is None else type(safe_error).__name__,
        "safe_error": None if safe_error is None else str(safe_error)[:1000],
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    private_report_sha256 = write_private_bound_json_v021(
        private_root / "report/baseline-readiness-attempt.json",
        private_payload,
    )
    normalized_attempt_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.public-baseline-readiness-attempt.v021",
        "run_number": run_number,
        "changed_attempt_number": changed_attempt_number,
        "attempt_signature_sha256": signature.attempt_signature_sha256,
        "changed_parameter": signature.changed_parameter.value,
        "infrastructure_replacement": (
            start.infrastructure_replacement_for_run_id is not None
        ),
        "terminal": terminal,
        "disposition": disposition.value,
        "failure_domain": failure_domain.value,
        "observed_at": datetime.now(UTC).isoformat(),
        "environment_id": environment_id,
        "baseline_id": None if baseline is None else baseline.get("baseline_id"),
        "baseline_sha256": (
            None if baseline is None else baseline.get("baseline_sha256")
        ),
        "baseline_active": baseline is not None and baseline.get("active") is True,
        "audit": audit,
        "audit_sha256": None if audit is None else audit.get("audit_sha256"),
        "parity_sha256": None if audit is None else audit.get("parity_sha256"),
        "scheduled_window_count": (
            0 if audit is None else audit.get("scheduled_window_count", 0)
        ),
        "accepted_window_count": accepted_windows,
        "traffic_result": traffic_result,
        "queue_default_unchanged": queue_default_unchanged,
        "healthy_traffic_stopped": traffic_stopped,
        "api_restart_verified": api_restart_verified,
        "worker_restart_verified": worker_restart_verified,
        "outer_baseline_restored": outer_baseline_restored,
        "owned_demo_cleanup": cleanup_status,
        "baseline_job_safe_error_code": (
            None if baseline_job is None else baseline_job.get("safe_error_code")
        ),
        "safe_error_type": None if safe_error is None else type(safe_error).__name__,
        "private_report_sha256": private_report_sha256,
        "failure_before_cleanup_sha256": failure_before_cleanup_sha256,
        "fault_attempt_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    public_attempt_path = (
        root
        / "docs/analysis"
        / f"product-v021-baseline-readiness-attempt-{run_number}.json"
    )
    normalized_attempt = write_public_readiness_attempt_v021(
        public_attempt_path,
        normalized_attempt_payload,
    )
    final_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.readiness-attempt-final.v021",
        "run_number": run_number,
        "changed_attempt_number": changed_attempt_number,
        "run_id": run_id,
        "attempt_signature_sha256": signature.attempt_signature_sha256,
        "changed_parameter": signature.changed_parameter.value,
        "infrastructure_replacement_for_run_id": (
            start.infrastructure_replacement_for_run_id
        ),
        "terminal": terminal,
        "disposition": disposition.value,
        "failure_domain": failure_domain.value,
        "audit_sha256": None if audit is None else audit.get("audit_sha256"),
        "rejection_reason_codes": list(rejection_reason_codes),
        "scheduled_window_count": (
            0 if audit is None else audit.get("scheduled_window_count", 0)
        ),
        "usable_audit": usable_audit,
        "queue_default_unchanged": queue_default_unchanged,
        "outer_baseline_restored": outer_baseline_restored,
        "owned_demo_cleanup": cleanup_status,
        "failure_before_cleanup_sha256": failure_before_cleanup_sha256,
        "private_attempt_report_sha256": private_report_sha256,
        "public_attempt_report_sha256": normalized_attempt.report_sha256,
        "interrupted": interrupted,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    write_readiness_attempt_final_v021(
        private_root=private_campaign_root,
        payload=final_payload,
    )

    if binding is not None:
        write_pilot_baseline_binding_v021(
            root / "config/product-v021/live-pilot/baseline-binding.json",
            binding,
        )
    return _write_public_readiness_v021(
        repository_root=root,
        terminal=terminal,
        observed_at=datetime.now(UTC),
        normalized_attempt=normalized_attempt,
    )


__all__ = (
    "PINNED_UPSTREAM_V021",
    "READINESS_BLOCKED_V021",
    "READINESS_CONTRACT_PASS_V021",
    "READINESS_PASS_V021",
    "READINESS_REPAIR_REQUIRED_V021",
    "reserve_readiness_attempt_v021",
    "run_live_baseline_readiness_v021",
    "verify_baseline_readiness_contract_v021",
)
