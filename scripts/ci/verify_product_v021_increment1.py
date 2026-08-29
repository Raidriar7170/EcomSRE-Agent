#!/usr/bin/env python3
"""Verify the Product v0.2.1 offline baseline-audit increment."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Sequence

from ecomsre.product.pilot.baseline_readiness_v021 import (
    PilotBaselineReadinessProfileV021,
    ReadinessAttemptDispositionV021,
    ReadinessChangeParameterV021,
    load_pilot_baseline_binding_v021,
    render_public_readiness_markdown_v021,
)
from ecomsre.product.pilot.calibration_v021 import (
    QueueProfileV021,
    render_public_calibration_markdown_v021,
)
from ecomsre.product.pilot.readiness_attempts_v021 import (
    PublicReadinessAttemptV021,
)
from scripts.ci.verify_product_v021_history import verify_product_v021_history


TERMINAL = "ECOMSRE_PRODUCT_V021_BASELINE_AUDIT_READY"
CALIBRATION_PASS_V021 = "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS"
CALIBRATION_BLOCKED_V021 = "BLOCKED_ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE"


def _load(path: Path) -> dict[str, Any]:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"v0.2.1 verifier input is not a regular file: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"v0.2.1 Increment 1 artifact is not an object: {path}")
    return payload


def _semantic_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _read_exact_public_text_v021(root: Path, relative_path: str) -> str:
    current = root
    root_metadata = os.lstat(root)
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise ValueError("v0.2.1 public calibration root differs")
    for index, part in enumerate(Path(relative_path).parts):
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError as error:
            raise ValueError("v0.2.1 public calibration artifact is absent") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("v0.2.1 public calibration artifact uses a symlink")
        final = index == len(Path(relative_path).parts) - 1
        if not final and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("v0.2.1 public calibration path differs")
        if final and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("v0.2.1 public calibration artifact is not regular")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(current, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        return handle.read()


def _verify_public_calibration_truth_isolation_v021(*documents: str) -> None:
    combined = "\n".join(documents).casefold()
    forbidden = (
        "kafkaqueueproblems",
        "featureflag",
        "checkout_kafka_queue_overload",
        "injected_value",
        '"selected_value"',
        "ecomsre-v02-",
        "overload simulation",
    )
    if any(item in combined for item in forbidden) or re.search(
        r"#\s*\d+\s+messages",
        combined,
    ):
        raise ValueError("v0.2.1 public calibration leaks private control truth")


def _verify_calibration_terminal_artifacts_v021(
    root: Path,
    *,
    progress: dict[str, Any] | dict[str, object],
    profile: QueueProfileV021,
) -> None:
    json_text = _read_exact_public_text_v021(
        root,
        "docs/analysis/product-v021-profile-calibration.json",
    )
    markdown_text = _read_exact_public_text_v021(
        root,
        "docs/analysis/product-v021-profile-calibration.md",
    )
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise ValueError("v0.2.1 public calibration JSON differs") from error
    if not isinstance(payload, dict):
        raise ValueError("v0.2.1 public calibration JSON is not an object")
    expected_keys = {
        "schema_version",
        "terminal",
        "observed_at",
        "calibration_execution_count",
        "calibration_iteration_count",
        "changed_calibration_iteration_count",
        "selected_root_service",
        "selected_profile_sha256",
        "private_report_sha256",
        "baseline_binding_sha256",
        "attempts",
        "active_baseline_unchanged",
        "outer_baseline_restored",
        "baseline_restoration",
        "owned_demo_cleanup",
        "fault_attempt_count",
        "action_authority",
        "action_authority_violations",
        "agent_writes",
        "runbook_executions",
        "report_sha256",
    }
    if set(payload) != expected_keys:
        raise ValueError("v0.2.1 public calibration JSON shape differs")
    if payload.get("schema_version") != "ecomsre.product.profile-calibration.v021":
        raise ValueError("v0.2.1 public calibration schema differs")
    raw_observed_at = payload.get("observed_at")
    try:
        observed_at = datetime.fromisoformat(
            raw_observed_at.replace("Z", "+00:00")
            if isinstance(raw_observed_at, str)
            else ""
        )
    except ValueError as error:
        raise ValueError("v0.2.1 public calibration timestamp differs") from error
    if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
        raise ValueError("v0.2.1 public calibration timestamp is not UTC")
    supplied_report_sha256 = payload.get("report_sha256")
    report_body = dict(payload)
    report_body.pop("report_sha256", None)
    if (
        not isinstance(supplied_report_sha256, str)
        or supplied_report_sha256 != _semantic_sha256(report_body)
    ):
        raise ValueError("v0.2.1 public calibration digest differs")
    terminal = payload.get("terminal")
    iteration_count = payload.get("calibration_iteration_count")
    changed_count = payload.get("changed_calibration_iteration_count")
    attempts = payload.get("attempts")
    if (
        terminal not in {CALIBRATION_PASS_V021, CALIBRATION_BLOCKED_V021}
        or terminal != progress.get("terminal")
        or payload.get("calibration_execution_count") != 1
        or progress.get("calibration_execution_count") != 1
        or type(iteration_count) is not int
        or not 0 <= iteration_count <= 3
        or type(changed_count) is not int
        or changed_count != max(0, iteration_count - 1)
        or iteration_count != progress.get("profile_calibration_iteration_count")
        or changed_count
        != progress.get("profile_calibration_changed_iteration_count")
        or not isinstance(attempts, list)
        or len(attempts) != iteration_count
    ):
        raise ValueError("v0.2.1 public calibration progress binding differs")
    expected_attempt_keys = {
        "episode_terminal",
        "diagnosis_terminal",
        "support_sources",
        "queue_log_observed",
        "corroborating_source_available",
        "runtime_root_coverage",
        "evidence_refs_resolve",
        "provisional_report_valid",
        "truth_isolation_pass",
        "baseline_recovery",
    }
    if any(
        not isinstance(item, dict) or set(item) != expected_attempt_keys
        for item in attempts
    ):
        raise ValueError("v0.2.1 public calibration attempt projection differs")
    binding = load_pilot_baseline_binding_v021(
        root / "config/product-v021/live-pilot/baseline-binding.json"
    )
    if payload.get("baseline_binding_sha256") != binding.binding_sha256:
        raise ValueError("v0.2.1 public calibration baseline binding differs")
    private_report_sha256 = payload.get("private_report_sha256")
    if not isinstance(private_report_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}",
        private_report_sha256,
    ):
        raise ValueError("v0.2.1 public calibration private report binding differs")
    if (
        payload.get("fault_attempt_count") != 0
        or payload.get("action_authority") != "NONE"
        or payload.get("action_authority_violations") != 0
        or payload.get("agent_writes") != 0
        or payload.get("runbook_executions") != 0
    ):
        raise ValueError("v0.2.1 public calibration authority boundary differs")
    if terminal == CALIBRATION_PASS_V021:
        final_attempt = attempts[-1] if attempts else {}
        final_sources = final_attempt.get("support_sources")
        source_set = set(final_sources) if isinstance(final_sources, list) else set()
        continuation_terminals = {
            "NO_INCIDENT_FALSELY_ADMITTED",
            "OPEN_WORLD_NOT_REACHED",
            "PROFILE_NOT_OBSERVABLE",
        }
        if (
            not 1 <= iteration_count <= 3
            or profile.profile_sha256 is None
            or profile.calibrated_at != observed_at
            or profile.selected_value
            != profile.candidate_values[iteration_count - 1]
            or payload.get("selected_profile_sha256") != profile.profile_sha256
            or payload.get("private_report_sha256")
            != profile.calibration_report_sha256
            or profile.calibration_runtime_binding_sha256
            != binding.runtime_authority_sha256
            or payload.get("selected_root_service") != "checkout"
            or payload.get("active_baseline_unchanged") is not True
            or payload.get("outer_baseline_restored") is not True
            or payload.get("baseline_restoration") is not True
            or payload.get("owned_demo_cleanup") != "CLEAN"
            or final_attempt.get("episode_terminal") != "PASS"
            or final_attempt.get("diagnosis_terminal") != "OPEN_WORLD"
            or final_attempt.get("queue_log_observed") is not True
            or final_attempt.get("corroborating_source_available") is not True
            or final_attempt.get("runtime_root_coverage") is not True
            or final_attempt.get("evidence_refs_resolve") is not True
            or final_attempt.get("provisional_report_valid") is not True
            or "LOGS" not in source_set
            or not source_set.intersection(
                {"METRICS", "TRACES", "RUNTIME", "RESOURCES"}
            )
            or any(
                item.get("episode_terminal") not in continuation_terminals
                for item in attempts[:-1]
            )
            or any(
                item.get("episode_terminal") in {"CORE_ABSORBED", "EXTENSION_ABSORBED"}
                or item.get("baseline_recovery") != "PASS"
                or item.get("truth_isolation_pass") is not True
                for item in attempts
            )
        ):
            raise ValueError("v0.2.1 public calibration PASS binding differs")
    elif profile.selected_value is not None or payload.get(
        "selected_profile_sha256"
    ) is not None:
        raise ValueError("v0.2.1 blocked public calibration freezes a profile")
    expected_markdown = render_public_calibration_markdown_v021(payload)
    if markdown_text != expected_markdown:
        raise ValueError("v0.2.1 public calibration Markdown differs")
    _verify_public_calibration_truth_isolation_v021(json_text, markdown_text)


def _read_public_readiness_text_v021(root: Path, relative_path: str) -> str:
    try:
        return _read_exact_public_text_v021(root, relative_path)
    except (OSError, ValueError) as error:
        raise ValueError("v0.2.1 public readiness artifact differs") from error


def _verify_readiness_attempt_sequence_v021(
    attempts: Sequence[PublicReadinessAttemptV021],
) -> None:
    if not attempts:
        raise ValueError("v0.2.1 public readiness attempt sequence is empty")
    first = attempts[0]
    if (
        first.run_number != 1
        or first.changed_attempt_number != 1
        or first.changed_parameter is not ReadinessChangeParameterV021.INITIAL
        or first.infrastructure_replacement
    ):
        raise ValueError("v0.2.1 public readiness initial sequence differs")
    replacement_count = 0
    for expected_run_number, (previous, current) in enumerate(
        zip(attempts, attempts[1:], strict=False),
        start=2,
    ):
        if current.run_number != expected_run_number:
            raise ValueError("v0.2.1 public readiness run sequence differs")
        if current.infrastructure_replacement:
            replacement_count += 1
            if (
                previous.disposition
                is not ReadinessAttemptDispositionV021.INFRASTRUCTURE_REPLACEMENT_ELIGIBLE
                or current.changed_attempt_number
                != previous.changed_attempt_number
                or current.attempt_signature_sha256
                != previous.attempt_signature_sha256
                or current.changed_parameter is not previous.changed_parameter
            ):
                raise ValueError(
                    "v0.2.1 public readiness replacement sequence differs"
                )
        elif (
            previous.disposition
            is not ReadinessAttemptDispositionV021.TARGETED_REPAIR_ELIGIBLE
            or current.changed_attempt_number
            != previous.changed_attempt_number + 1
            or current.changed_parameter is ReadinessChangeParameterV021.INITIAL
            or current.attempt_signature_sha256
            == previous.attempt_signature_sha256
        ):
            raise ValueError("v0.2.1 public readiness repair sequence differs")
    if replacement_count > 1:
        raise ValueError("v0.2.1 public readiness replacement sequence differs")


def _verify_readiness_terminal_artifacts_v021(
    root: Path,
    *,
    progress: dict[str, Any] | dict[str, object],
    profile: PilotBaselineReadinessProfileV021,
) -> None:
    json_text = _read_public_readiness_text_v021(
        root,
        "docs/analysis/product-v021-baseline-readiness.json",
    )
    markdown_text = _read_public_readiness_text_v021(
        root,
        "docs/analysis/product-v021-baseline-readiness.md",
    )
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise ValueError("v0.2.1 public readiness JSON differs") from error
    if not isinstance(payload, dict):
        raise ValueError("v0.2.1 public readiness JSON is not an object")
    expected_keys = {
        "schema_version",
        "terminal",
        "observed_at",
        "readiness_run_count",
        "readiness_attempt_count",
        "infrastructure_replacement_count",
        "attempts",
        "latest_attempt",
        "fault_attempt_count",
        "profile_calibration_iteration_count",
        "action_authority",
        "action_authority_violations",
        "agent_writes",
        "runbook_executions",
        "result_sha256",
    }
    if set(payload) != expected_keys:
        raise ValueError("v0.2.1 public readiness JSON shape differs")
    supplied_result_sha256 = payload.get("result_sha256")
    result_body = dict(payload)
    result_body.pop("result_sha256", None)
    if (
        not isinstance(supplied_result_sha256, str)
        or supplied_result_sha256 != _semantic_sha256(result_body)
        or payload.get("schema_version")
        != "ecomsre.product.baseline-readiness-result.v021"
    ):
        raise ValueError("v0.2.1 public readiness digest or schema differs")
    raw_observed_at = payload.get("observed_at")
    try:
        observed_at = datetime.fromisoformat(
            raw_observed_at.replace("Z", "+00:00")
            if isinstance(raw_observed_at, str)
            else ""
        )
    except ValueError as error:
        raise ValueError("v0.2.1 public readiness timestamp differs") from error
    if observed_at.tzinfo is None or observed_at.utcoffset() != timedelta(0):
        raise ValueError("v0.2.1 public readiness timestamp is not UTC")
    terminal = payload.get("terminal")
    current_terminal = progress.get("terminal")
    expected_readiness_terminal = (
        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS"
        if current_terminal in {CALIBRATION_PASS_V021, CALIBRATION_BLOCKED_V021}
        else current_terminal
    )
    run_count = payload.get("readiness_run_count")
    changed_count = payload.get("readiness_attempt_count")
    replacement_count = payload.get("infrastructure_replacement_count")
    attempts_raw = payload.get("attempts")
    if (
        terminal != expected_readiness_terminal
        or type(run_count) is not int
        or not 1 <= run_count <= 3
        or type(changed_count) is not int
        or not 1 <= changed_count <= 2
        or type(replacement_count) is not int
        or not 0 <= replacement_count <= 1
        or run_count != changed_count + replacement_count
        or run_count != progress.get("baseline_readiness_run_count")
        or changed_count != progress.get("baseline_readiness_attempt_count")
        or replacement_count != progress.get("infrastructure_replacement_count")
        or not isinstance(attempts_raw, list)
        or len(attempts_raw) != run_count
    ):
        raise ValueError("v0.2.1 public readiness progress binding differs")
    try:
        attempts = tuple(
            PublicReadinessAttemptV021.model_validate(item)
            for item in attempts_raw
        )
        latest = PublicReadinessAttemptV021.model_validate(
            payload.get("latest_attempt")
        )
    except (TypeError, ValueError) as error:
        raise ValueError("v0.2.1 public readiness attempt differs") from error
    _verify_readiness_attempt_sequence_v021(attempts)
    if (
        tuple(item.run_number for item in attempts) != tuple(range(1, run_count + 1))
        or max(item.changed_attempt_number for item in attempts) != changed_count
        or sum(item.infrastructure_replacement for item in attempts)
        != replacement_count
        or latest != attempts[-1]
        or latest.terminal != terminal
        or any(
            item.terminal
            != "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED"
            for item in attempts[:-1]
        )
    ):
        raise ValueError("v0.2.1 public readiness attempt sequence differs")
    attempt_paths = tuple(
        sorted(
            (root / "docs/analysis").glob(
                "product-v021-baseline-readiness-attempt-*.json"
            )
        )
    )
    expected_paths = tuple(
        root
        / "docs/analysis"
        / f"product-v021-baseline-readiness-attempt-{number}.json"
        for number in range(1, run_count + 1)
    )
    if attempt_paths != expected_paths:
        raise ValueError("v0.2.1 public readiness attempt file set differs")
    for expected, attempt in zip(expected_paths, attempts, strict=True):
        try:
            persisted = PublicReadinessAttemptV021.model_validate_json(
                _read_public_readiness_text_v021(
                    root,
                    expected.relative_to(root).as_posix(),
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("v0.2.1 public readiness attempt file differs") from error
        if persisted != attempt:
            raise ValueError("v0.2.1 public readiness aggregate attempt differs")
    if (
        payload.get("fault_attempt_count") != 0
        or payload.get("profile_calibration_iteration_count") != 0
        or payload.get("action_authority") != "NONE"
        or payload.get("action_authority_violations") != 0
        or payload.get("agent_writes") != 0
        or payload.get("runbook_executions") != 0
    ):
        raise ValueError("v0.2.1 public readiness authority boundary differs")
    if terminal == "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS":
        binding = load_pilot_baseline_binding_v021(
            root / "config/product-v021/live-pilot/baseline-binding.json"
        )
        audit = latest.audit
        accepted_ordinals = (
            tuple(item.window_ordinal for item in audit.windows if item.accepted)
            if audit is not None
            else ()
        )
        traffic = latest.traffic_result
        no_truncated_queries = audit is not None and all(
            not result.truncated
            for window in audit.windows
            for result in window.source_results
        )
        if (
            latest.disposition.value != "PASS"
            or latest.failure_domain.value != "NONE"
            or latest.environment_id != binding.environment_id
            or latest.baseline_id != binding.baseline_id
            or latest.baseline_sha256 != binding.baseline_sha256
            or latest.baseline_active is not True
            or audit is None
            or audit.audit_sha256 != binding.audit_sha256
            or audit.parity_sha256 != binding.parity_sha256
            or audit.capability_sha256 != binding.capability_matrix_sha256
            or audit.build_policy != binding.build_policy.model_dump(mode="json")
            or accepted_ordinals != binding.accepted_window_ordinals
            or audit.coverage_matrix != binding.source_coverage_matrix
            or audit.scheduled_window_count != 5
            or audit.accepted_window_count < 4
            or audit.final_builder_would_pass is not True
            or not no_truncated_queries
            or traffic is None
            or traffic.request_seed != profile.healthy_traffic_profile.request_seed
            or traffic.attempted
            != profile.healthy_traffic_profile.maximum_request_count
            or traffic.stopped_on_error_budget is not False
            or binding.healthy_traffic_profile_sha256
            != _semantic_sha256(
                profile.healthy_traffic_profile.model_dump(mode="json")
            )
            or latest.queue_default_unchanged is not True
            or latest.healthy_traffic_stopped is not True
            or latest.api_restart_verified is not True
            or latest.worker_restart_verified is not True
            or latest.outer_baseline_restored is not True
            or latest.owned_demo_cleanup != "CLEAN"
            or latest.baseline_job_safe_error_code is not None
            or latest.safe_error_type is not None
            or latest.failure_before_cleanup_sha256 is not None
        ):
            raise ValueError("v0.2.1 public readiness PASS binding differs")
    expected_markdown = render_public_readiness_markdown_v021(payload)
    if markdown_text != expected_markdown:
        raise ValueError("v0.2.1 public readiness Markdown differs")
    try:
        _verify_public_calibration_truth_isolation_v021(json_text, markdown_text)
    except ValueError as error:
        raise ValueError("v0.2.1 public readiness leaks private truth") from error


def _verify_queue_profile_state_v021(
    queue_profile: dict[str, Any],
    *,
    increment: int,
    terminal: object,
) -> None:
    profile = QueueProfileV021.model_validate(queue_profile)
    common_valid = (
        profile.profile_name == "CHECKOUT_KAFKA_QUEUE_OVERLOAD"
        and profile.candidate_values == (5, 10, 20)
        and profile.expected_default_value == 0
        and profile.baseline_binding_required is True
        and profile.maximum_calibration_changes == 2
    )
    if not common_valid:
        raise ValueError("v0.2.1 queue profile boundary differs")
    frozen = profile.selected_value is not None
    if frozen:
        if (
            increment != 2
            or terminal != "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS"
            or profile.selected_root_service != "checkout"
            or profile.profile_sha256 is None
        ):
            raise ValueError("v0.2.1 queue profile freeze boundary differs")
        return
    if terminal == "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS":
        raise ValueError("v0.2.1 queue profile PASS is not frozen")
    if any(
        getattr(profile, field) is not None
        for field in (
            "selected_root_service",
            "calibration_report_sha256",
            "calibration_contract_sha256",
            "calibration_runtime_binding_sha256",
            "calibrated_at",
            "profile_sha256",
        )
    ):
        raise ValueError("v0.2.1 queue profile fresh state differs")


def verify_product_v021_increment1(project_root: Path) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    history = verify_product_v021_history(root)
    progress = _load(root / "docs/analysis/product-v021-progress.json")
    increment = progress.get("increment")
    if not isinstance(increment, int) or not 1 <= increment <= 2:
        raise ValueError("v0.2.1 Increment 1 progress differs")
    profile = _load(root / "config/product-v021/baseline-readiness/profile.json")
    typed_readiness_profile = PilotBaselineReadinessProfileV021.model_validate(
        profile
    )
    supplied_profile_sha = profile.pop("profile_sha256", None)
    if supplied_profile_sha != _semantic_sha256(profile):
        raise ValueError("v0.2.1 readiness profile digest differs")
    policy = profile.get("build_policy")
    traffic = profile.get("healthy_traffic_profile")
    if not isinstance(policy, dict) or not isinstance(traffic, dict):
        raise ValueError("v0.2.1 readiness profile shape differs")
    if (
        profile.get("candidate_services") != ["checkout"]
        or policy.get("mode") != "DEMO_ONLY"
        or policy.get("lookback_seconds") != 180
        or policy.get("window_count") != 5
        or policy.get("warmup_seconds") != 180
        or profile.get("maximum_changed_attempts") != 2
        or traffic.get("request_seed") != 501
        or traffic.get("error_budget") != 12
        or profile.get("public_root")
        != ".local/product-v021/baseline-readiness"
        or profile.get("private_root")
        != ".local/product-v021/private-baseline-readiness"
    ):
        raise ValueError("v0.2.1 readiness profile boundary differs")
    if increment == 1 and (
        policy.get("minimum_successful_windows") != 4
        or profile.get("stabilization_seconds") != 60
        or profile.get("baseline_accumulation_seconds") != 360
        or traffic.get("maximum_request_count") != 180
        or traffic.get("requests_per_second") != 0.5
    ):
        raise ValueError("v0.2.1 initial readiness profile differs")
    if increment > 1:
        minimum_windows = policy.get("minimum_successful_windows")
        stabilization = profile.get("stabilization_seconds")
        accumulation = profile.get("baseline_accumulation_seconds")
        request_count = traffic.get("maximum_request_count")
        request_rate = traffic.get("requests_per_second")
        if (
            not isinstance(minimum_windows, int)
            or not 4 <= minimum_windows <= 5
            or not isinstance(stabilization, int)
            or not 0 <= stabilization <= 600
            or not isinstance(accumulation, int)
            or not 180 <= accumulation <= 900
            or not isinstance(request_count, int)
            or not 1 <= request_count <= 180
            or not isinstance(request_rate, (int, float))
            or not 0 < float(request_rate) <= 2
        ):
            raise ValueError("v0.2.1 changed readiness profile exceeds Goal bounds")

    campaign = _load(root / "config/product-v021/live-pilot/campaign.json")
    negatives = _load(root / "config/product-v021/live-pilot/negative-controls.json")
    queue_profile = _load(root / "config/product-v021/live-pilot/profile.json")
    expected_episode_roles = {
        "N0": "LIVE_NO_FAULT_NEGATIVE",
        "P1": "FIT_POSITIVE",
        "P2": "FIT_POSITIVE",
        "P3": "SHADOW_POSITIVE",
        "H1": "FINAL_HELDOUT_RECURRENCE",
    }
    traffic_profiles = campaign.get("traffic_profiles")
    if (
        campaign.get("accepted_schedule") != ["N0", "P1", "P2", "P3"]
        or campaign.get("heldout_schedule") != ["H1"]
        or campaign.get("episode_roles") != expected_episode_roles
        or campaign.get("maximum_infrastructure_replacements_per_episode") != 1
        or campaign.get("maximum_changed_calibration_iterations") != 2
        or campaign.get("positive_episode_count") != 3
        or campaign.get("live_no_fault_negative_count") != 1
        or campaign.get("heldout_recurrence_maximum") != 1
        or campaign.get("human_checkpoint_a") != "UNFULFILLED"
        or campaign.get("human_checkpoint_b") != "UNFULFILLED"
        or campaign.get("private_root")
        != ".local/product-v021/private-live-control"
        or campaign.get("product_data_root") != ".local/product-v021/live-pilot"
        or campaign.get("action_authority") != "NONE"
        or campaign.get("runbook_authority") != "NONE"
        or not isinstance(traffic_profiles, dict)
        or set(traffic_profiles) != {"CALIBRATION", "N0", "P1", "P2", "P3", "H1"}
    ):
        raise ValueError("v0.2.1 live campaign boundary differs")
    _verify_queue_profile_state_v021(
        queue_profile,
        increment=increment,
        terminal=progress.get("terminal"),
    )
    if (
        negatives.get("live_no_fault") != "N0"
        or negatives.get("known_core_negative", {}).get("fallback")
        != "VALIDATED_REAL_CAPTURE_NEGATIVE"
        or negatives.get("fit_strata")
        != [
            "LIVE_NO_FAULT",
            "KNOWN_CORE_NEGATIVE",
            "SAME_DOMAIN_REPLAY_CONTROL",
        ]
        or negatives.get("shadow_strata")
        != [
            "ADDITIONAL_NO_INCIDENT",
            "CONFUSABLE_CORE_KNOWN",
            "SOURCE_FAILURE",
            "MOVED_TARGET_COUNTERFACTUAL",
        ]
    ):
        raise ValueError("v0.2.1 negative-control boundary differs")

    predecessor_audit = _load(
        root / "docs/analysis/product-v021-predecessor-baseline-audit.json"
    )
    inferences = predecessor_audit.get("tracked_code_path_inferences")
    if (
        not isinstance(inferences, list)
        or len(inferences) != 1
        or inferences[0].get("classification") != "TRACKED_CODE_PATH_INFERENCE"
        or inferences[0].get("measured_predecessor_cause") is not False
        or predecessor_audit.get("tracked_artifact_facts", {}).get("fault_attempt_count")
        != 0
    ):
        raise ValueError("v0.2.1 predecessor audit claim boundary differs")

    if (
        progress.get("action_authority") != "NONE"
        or (
            increment == 2
            and progress.get("action_authority_violations") != 0
        )
        or progress.get("agent_writes") != 0
        or progress.get("runbook_executions") != 0
    ):
        raise ValueError("v0.2.1 Increment 1 progress differs")
    if increment == 1 and (
        progress.get("terminal") != TERMINAL
        or progress.get("baseline_readiness_attempt_count") != 0
        or progress.get("profile_calibration_iteration_count") != 0
        or progress.get("fault_attempt_count") != 0
        or progress.get("accepted_positive_episode_count") != 0
        or progress.get("heldout_recurrence_count") != 0
        or progress.get("current_human_gate") != "NOT_REACHED"
    ):
        raise ValueError("v0.2.1 Increment 1 progress differs")
    if increment == 2:
        readiness_attempts = progress.get("baseline_readiness_attempt_count")
        readiness_runs = progress.get("baseline_readiness_run_count")
        infrastructure_replacements = progress.get(
            "infrastructure_replacement_count"
        )
        calibration_iterations = progress.get("profile_calibration_iteration_count")
        calibration_changes = progress.get(
            "profile_calibration_changed_iteration_count", 0
        )
        calibration_executions = progress.get("calibration_execution_count", 0)
        terminal = progress.get("terminal")
        allowed_readiness_terminals = {
            "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_REPAIR_REQUIRED",
            "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS",
            "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS",
        }
        allowed_calibration_terminals = {
            "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS",
            "BLOCKED_ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE",
        }
        if (
            not isinstance(readiness_attempts, int)
            or not 1 <= readiness_attempts <= 2
            or not isinstance(readiness_runs, int)
            or not 1 <= readiness_runs <= 3
            or not isinstance(infrastructure_replacements, int)
            or not 0 <= infrastructure_replacements <= 1
            or readiness_runs
            != readiness_attempts + infrastructure_replacements
            or not isinstance(calibration_iterations, int)
            or not 0 <= calibration_iterations <= 3
            or not isinstance(calibration_changes, int)
            or not 0 <= calibration_changes <= 2
            or calibration_changes != max(0, calibration_iterations - 1)
            or terminal not in allowed_readiness_terminals | allowed_calibration_terminals
            or (
                terminal in allowed_readiness_terminals
                and (calibration_iterations != 0 or calibration_executions != 0)
            )
            or (
                terminal == "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS"
                and not 1 <= calibration_iterations <= 3
            )
            or (
                terminal in allowed_calibration_terminals
                and calibration_executions != 1
            )
            or progress.get("fault_attempt_count") != 0
            or progress.get("accepted_positive_episode_count") != 0
            or progress.get("heldout_recurrence_count") != 0
            or progress.get("current_human_gate") != "NOT_REACHED"
        ):
            raise ValueError("v0.2.1 Increment 2 progress state differs")
        supplied_progress_sha = progress.get("progress_sha256")
        if not isinstance(supplied_progress_sha, str):
            raise ValueError("v0.2.1 progress digest is absent")
        progress_body = dict(progress)
        progress_body.pop("progress_sha256")
        if supplied_progress_sha != _semantic_sha256(progress_body):
            raise ValueError("v0.2.1 progress digest differs")
        if terminal in allowed_calibration_terminals:
            _verify_calibration_terminal_artifacts_v021(
                root,
                progress=progress,
                profile=QueueProfileV021.model_validate(queue_profile),
            )
        _verify_readiness_terminal_artifacts_v021(
            root,
            progress=progress,
            profile=typed_readiness_profile,
        )
    return {
        "status": TERMINAL,
        "history_status": history["status"],
        "baseline_readiness_attempt_count": (
            progress.get("baseline_readiness_attempt_count", 0)
        ),
        "baseline_readiness_run_count": progress.get(
            "baseline_readiness_run_count", 0
        ),
        "infrastructure_replacement_count": progress.get(
            "infrastructure_replacement_count", 0
        ),
        "fault_attempt_count": 0,
        "human_checkpoint_a": "UNFULFILLED",
        "human_checkpoint_b": "UNFULFILLED",
        "agent_writes": 0,
        "runbook_executions": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v021_increment1(arguments.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("TERMINAL", "verify_product_v021_increment1")
