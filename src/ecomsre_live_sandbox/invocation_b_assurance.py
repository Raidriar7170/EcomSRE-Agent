"""Deterministic public truth reconstruction from a sealed Invocation B terminal."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
from pathlib import Path
from typing import Any, cast

from ecomsre_live_sandbox.contracts import (
    canonical_sha256,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_contracts import scan_public_e2e_payload
from ecomsre_live_sandbox.e2e_v6_contracts import E2EV6Config
from ecomsre_live_sandbox.invocation_b_verdicts import (
    get_invocation_b_verdict_policy,
)


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCES = {"METRICS", "LOGS", "TRACES"}
_PROJECTION_SOURCES = {"metrics", "logs", "traces"}
_SOURCE_STATUSES = {
    "AVAILABLE",
    "EMPTY",
    "HTTP_FAILED",
    "SCHEMA_MISMATCH",
    "FIELD_MAPPING_UNSUPPORTED",
    "IDENTITY_MISMATCH",
    "INGESTION_TIMEOUT",
    "INVALID_RECORD",
}
_PROJECTION_REASON_CODES = {
    "NO_BROAD_METRICS",
    "NO_DIAGNOSTIC_METRICS",
    "NO_DIAGNOSTIC_LOGS",
    "NO_DIAGNOSTIC_TRACES",
    "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE",
    "INSUFFICIENT_RESOLVABLE_EVIDENCE",
    "CONTROL_TRUTH_LEAK",
    "VISIBLE_SERVICE_COUNT_BELOW_MINIMUM",
}
_CLEANUP_FIELDS = {
    "baseline_restored",
    "owned_containers",
    "owned_networks",
    "owned_volumes",
    "non_owned_resources_changed",
    "verdict",
}
_SUCCESS_FORBIDDEN_REASONS = {
    "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE",
    "NO_DIAGNOSTIC_METRICS",
    "INSUFFICIENT_RESOLVABLE_EVIDENCE",
    "CONTROL_TRUTH_LEAK",
    "VISIBLE_SERVICE_COUNT_BELOW_MINIMUM",
}
_PROJECTION_BLOCKING_REASONS = _SUCCESS_FORBIDDEN_REASONS | {"NO_BROAD_METRICS"}
_UNCLASSIFIED_RUNTIME_IDENTITIES: dict[
    tuple[str, str, str], tuple[str, frozenset[int], str]
] = {
    ("LOCAL_DOCKER_VERIFIED", "UNCLASSIFIED_RUNTIME_FAILURE", "WORKTREE_VERIFIED"): (
        "NOT_REQUIRED",
        frozenset({0}),
        "EARLY",
    ),
    ("LOCAL_DOCKER_VERIFIED", "DOCKER_AUTHORITY_UNAVAILABLE", "WORKTREE_VERIFIED"): (
        "NOT_REQUIRED",
        frozenset({0}),
        "EARLY",
    ),
    ("UPSTREAM_PIN_VERIFIED", "UPSTREAM_PIN_DRIFT", "LOCAL_DOCKER_VERIFIED"): (
        "NOT_REQUIRED",
        frozenset({0}),
        "EARLY",
    ),
    (
        "COMPOSE_RESOLUTION_STARTED",
        "COMPOSE_RESOLUTION_FAILED",
        "UPSTREAM_PIN_VERIFIED",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "IMAGE_AUTHORITY_LOAD_STARTED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "COMPOSE_RESOLVED",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "IMAGE_AUTHORITY_CREATED",
        "IMAGE_AUTHORITY_CREATION_FAILED",
        "IMAGE_AUTHORITY_LOAD_STARTED",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "RUN_IMAGE_VERIFICATION_CREATED",
        "RUN_IMAGE_VERIFICATION_WRITE_FAILED",
        "COMPOSE_STRUCTURE_HASH_VERIFIED",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "IMAGE_LOCK_VERIFICATION_STARTED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "RUN_IMAGE_VERIFICATION_CREATED",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "FAULT_CONTROLLER_PREPARATION_STARTED",
        "FAULT_CONTROLLER_PREPARATION_FAILED",
        "RUN_IMAGE_VERIFICATION_CREATED",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "PORT_PREFLIGHT_STARTED",
        "PORT_CONFLICT",
        "FAULT_CONTROLLER_PREPARED",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "DOCKER_BASELINE_SNAPSHOT_CAPTURED",
        "DOCKER_BASELINE_SNAPSHOT_FAILED",
        "PORTS_AVAILABLE",
    ): ("NOT_REQUIRED", frozenset({0}), "EARLY"),
    (
        "OWNED_RESOURCE_INVENTORY_VERIFIED",
        "OWNED_RESOURCE_INVENTORY_INCOMPLETE",
        "COMPOSE_START_RETURNED",
    ): ("CLEAN", frozenset({0}), "POST_COMPOSE"),
    (
        "STABILIZATION_STARTED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "SERVICES_HEALTHY",
    ): ("CLEAN", frozenset({0}), "POST_COMPOSE"),
    (
        "SOURCE_CAPTURE_WINDOW_STARTED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "BASELINE_CONFIGURATION_VERIFIED",
    ): ("CLEAN", frozenset({0, 1}), "PRE_SOURCE"),
    (
        "SOURCE_CAPTURE_WINDOW_COMPLETED",
        "SOURCE_BATCH_CONTRACT_FAILED",
        "SOURCE_CAPTURE_WINDOW_STARTED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COLLECTION"),
    (
        "METRICS_PROBE_CREATED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "SOURCE_CAPTURE_WINDOW_COMPLETED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COLLECTION"),
    (
        "LOGS_PROBE_CREATED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "METRICS_PROBE_CREATED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COLLECTION"),
    (
        "TRACES_PROBE_CREATED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "LOGS_PROBE_CREATED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COLLECTION"),
    (
        "SOURCE_BATCH_TERMINALIZATION_STARTED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "TRACES_PROBE_CREATED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COLLECTION"),
    (
        "SOURCE_BATCH_TERMINALIZATION_COMPLETED",
        "SOURCE_BATCH_CONTRACT_FAILED",
        "SOURCE_BATCH_TERMINALIZATION_STARTED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COLLECTION"),
    (
        "EVIDENCE_RESOLUTION_COMPLETED",
        "EVIDENCE_RESOLUTION_FAILED",
        "TRACES_PREFLIGHT_COMPLETED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COLLECTION"),
    (
        "SOURCE_AVAILABILITY_GATE_EVALUATED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "EVIDENCE_RESOLUTION_COMPLETED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COMPLETE"),
    (
        "NO_FAULT_READINESS_EVALUATED",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "SOURCE_AVAILABILITY_GATE_EVALUATED",
    ): ("CLEAN", frozenset({1}), "SOURCE_COMPLETE"),
    (
        "POST_PROJECTION_RUNTIME",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "MULTISERVICE_PROJECTION_COMPLETED",
    ): ("CLEAN", frozenset({1}), "POST_PROJECTION"),
    (
        "LIVE_DIAGNOSIS_RUNTIME",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "MULTISERVICE_PROJECTION_COMPLETED",
    ): ("CLEAN", frozenset({1}), "LIVE_DIAGNOSIS"),
    (
        "POLICY_RUNTIME",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "MULTISERVICE_PROJECTION_COMPLETED",
    ): ("CLEAN", frozenset({1}), "POLICY"),
    (
        "REMEDIATION_RUNTIME",
        "UNCLASSIFIED_RUNTIME_FAILURE",
        "MULTISERVICE_PROJECTION_COMPLETED",
    ): ("CLEAN", frozenset({1}), "REMEDIATION"),
}


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _exact_int(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def _exact_bool(value: object, expected: bool) -> bool:
    return type(value) is bool and value is expected


def _optional_exact_false(value: object) -> bool:
    return value is None or _exact_bool(value, False)


def _safe_list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _require_cleanup(cleanup: object, *, success: bool) -> Mapping[str, object]:
    if not isinstance(cleanup, Mapping):
        raise ValueError("sealed private terminal cleanup aggregate is missing")
    if set(cleanup) != _CLEANUP_FIELDS:
        raise ValueError("sealed private terminal cleanup schema is not exact")
    verdict = cleanup.get("verdict")
    if verdict not in {"CLEAN", "NOT_REQUIRED", "BLOCKED"}:
        raise ValueError("sealed private terminal cleanup verdict is invalid")
    if success or verdict in {"CLEAN", "NOT_REQUIRED"}:
        if any(
            (
                cleanup.get("baseline_restored") is not True,
                not _exact_int(cleanup.get("owned_containers"), 0),
                not _exact_int(cleanup.get("owned_networks"), 0),
                not _exact_int(cleanup.get("owned_volumes"), 0),
                cleanup.get("non_owned_resources_changed") is not False,
                success and verdict != "CLEAN",
            )
        ):
            raise ValueError("sealed private terminal cleanup truth is contradictory")
    else:
        baseline_restored = cleanup.get("baseline_restored")
        non_owned_changed = cleanup.get("non_owned_resources_changed")
        if type(baseline_restored) is not bool:
            raise ValueError("blocked cleanup contains invalid boolean truth")
        resources = tuple(
            cleanup.get(field)
            for field in ("owned_containers", "owned_networks", "owned_volumes")
        )
        exception_shape = all(value is None for value in resources) and (
            non_owned_changed is None
        )
        concrete_shape = all(_nonnegative_int(value) for value in resources) and (
            type(non_owned_changed) is bool
        )
        if not exception_shape and not concrete_shape:
            raise ValueError("blocked cleanup mixes unknown and concrete observations")
        if concrete_shape and all(
            (
                baseline_restored is True,
                all(value == 0 for value in resources),
                non_owned_changed is False,
            )
        ):
            raise ValueError("blocked cleanup contradicts fully clean observations")
        if concrete_shape:
            for value in resources:
                if not _nonnegative_int(value):  # pragma: no cover - narrowed above
                    raise ValueError(
                        "blocked cleanup contains an invalid resource count"
                    )
        else:
            for value in resources:
                if value is not None:  # pragma: no cover - narrowed above
                    raise ValueError(
                        "blocked cleanup contains an invalid resource count"
                    )
    return cleanup


def _require_success_invariants(
    config: E2EV6Config, terminal: Mapping[str, object]
) -> None:
    implementation = terminal.get("implementation_commit")
    result_head = terminal.get("result_head")
    if not isinstance(implementation, str) or _COMMIT.fullmatch(implementation) is None:
        raise ValueError("success implementation commit is not exact")
    if not isinstance(result_head, str) or _COMMIT.fullmatch(result_head) is None:
        raise ValueError("success result head is not exact")
    if result_head != implementation:
        raise ValueError("success result head differs from implementation commit")

    availability = terminal.get("source_availability")
    counts = terminal.get("source_counts")
    broad = terminal.get("projection_broad_counts")
    diagnostic = terminal.get("projection_diagnostic_counts")
    if not all(
        isinstance(value, Mapping)
        for value in (availability, counts, broad, diagnostic)
    ):
        raise ValueError("success source or projection aggregates are missing")
    availability_map = cast(Mapping[str, object], availability)
    counts_map = cast(Mapping[str, object], counts)
    broad_map = cast(Mapping[str, object], broad)
    diagnostic_map = cast(Mapping[str, object], diagnostic)
    if set(availability_map) != _SOURCES or any(
        availability_map.get(source) != "AVAILABLE" for source in _SOURCES
    ):
        raise ValueError("success source availability is not exact")
    if set(counts_map) != _SOURCES or any(
        not _positive_int(counts_map.get(source)) for source in _SOURCES
    ):
        raise ValueError("success source counts are not positive integers")
    if (
        not _exact_int(terminal.get("invalid_refs"), 0)
        or terminal.get("all_refs_resolve") is not True
    ):
        raise ValueError("success Evidence refs are not fully resolvable")
    if (
        set(broad_map) != _PROJECTION_SOURCES
        or any(
            not _nonnegative_int(broad_map.get(source))
            for source in _PROJECTION_SOURCES
        )
        or not _positive_int(broad_map.get("metrics"))
    ):
        raise ValueError("success broad projection counts are invalid")
    if set(diagnostic_map) != _PROJECTION_SOURCES or any(
        not _nonnegative_int(diagnostic_map.get(source))
        for source in _PROJECTION_SOURCES
    ):
        raise ValueError("success diagnostic projection counts are invalid")
    if not _positive_int(diagnostic_map.get("metrics")):
        raise ValueError("success diagnostic Metrics are absent")
    if not (
        _positive_int(diagnostic_map.get("logs"))
        or _positive_int(diagnostic_map.get("traces"))
    ):
        raise ValueError("success requires diagnostic Logs or Traces")

    expected_empty = sorted(
        source.upper()
        for source in _PROJECTION_SOURCES
        if diagnostic_map.get(source) == 0
    )
    empty_streams = terminal.get("empty_model_streams")
    reasons = terminal.get("projection_reason_codes")
    if (
        not isinstance(empty_streams, (list, tuple))
        or sorted(empty_streams) != expected_empty
    ):
        raise ValueError("success empty model streams contradict projection counts")
    if not isinstance(reasons, (list, tuple)):
        raise ValueError("success projection reason codes are missing")
    reason_set = set(reasons)
    if len(reason_set) != len(reasons) or not reason_set.issubset(
        _PROJECTION_REASON_CODES
    ):
        raise ValueError("success projection reason codes are not closed")
    for source in ("LOGS", "TRACES"):
        code = f"NO_DIAGNOSTIC_{source}"
        if (code in reason_set) != (source in expected_empty):
            raise ValueError("success empty-stream reason code is contradictory")
    if ("NO_BROAD_METRICS" in reason_set) != (broad_map.get("metrics") == 0):
        raise ValueError("success broad-metrics reason code is contradictory")
    if reason_set & _SUCCESS_FORBIDDEN_REASONS:
        raise ValueError("success projection contains a blocking reason code")

    visible = terminal.get("visible_service_count")
    if type(visible) is not int or not 3 <= visible <= 8:
        raise ValueError("success visible service count is outside the frozen range")
    context_sha = terminal.get("fault_time_a0_context_sha256")
    provider_sha = terminal.get("provider_live_context_sha256")
    if any(
        (
            not _exact_int(terminal.get("a0_context_builder_calls"), 1),
            terminal.get("fault_time_a0_context_artifact_exists") is not True,
            not isinstance(context_sha, str),
            isinstance(context_sha, str) and _SHA256.fullmatch(context_sha) is None,
            not isinstance(provider_sha, str),
            isinstance(provider_sha, str) and _SHA256.fullmatch(provider_sha) is None,
            context_sha != provider_sha,
        )
    ):
        raise ValueError("success fault-time context binding is invalid")

    required_ints = {
        "fault_injections": 1,
        "provider_calls": 2,
        "model_calls": 1,
        "forward_mutations": 1,
        "rollback_mutations": 0,
    }
    required_bools = {
        "provider_preflight_passed": True,
        "fault_impact_passed": True,
        "diagnosis_gate": True,
        "diagnosis_correct": True,
        "approval_valid": True,
        "recovery_verification_passed": True,
    }
    required_strings = {
        "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "policy_verdict": "ALLOW",
    }
    if any(
        (
            any(
                not _exact_int(terminal.get(key), expected)
                for key, expected in required_ints.items()
            ),
            any(
                not _exact_bool(terminal.get(key), expected)
                for key, expected in required_bools.items()
            ),
            any(
                terminal.get(key) != expected
                for key, expected in required_strings.items()
            ),
        )
    ):
        raise ValueError("success execution or authorization Gates are contradictory")
    if "rollback_exact_hash_verified" in terminal:
        raise ValueError("success terminal contains impossible rollback proof")
    cleanup = _require_cleanup(terminal.get("cleanup"), success=True)
    if (
        terminal.get("cleanup_verdict") != cleanup.get("verdict")
        or "cleanup_verdict" not in terminal
        or terminal.get("cleanup_failure_code") is not None
        or "cleanup_failure_code" not in terminal
    ):
        raise ValueError("success cleanup control fields are contradictory")
    if any(
        terminal.get(field) is not None
        for field in ("failed_stage", "last_completed_stage", "failure_code")
    ):
        raise ValueError("success terminal contains a failure identity")
    claim_boundary = terminal.get("claim_boundary")
    if (
        not isinstance(claim_boundary, (list, tuple))
        or tuple(claim_boundary) != config.reporting.claim_boundary
    ):
        raise ValueError("success claim boundary differs from frozen v6 authority")


def _require_non_success_invariants(
    config: E2EV6Config,
    terminal: Mapping[str, object],
    *,
    verdict: str,
) -> None:
    cleanup = _require_cleanup(terminal.get("cleanup"), success=False)
    optional_boolean_fields = (
        "all_refs_resolve",
        "provider_preflight_passed",
        "fault_impact_passed",
        "diagnosis_gate",
        "diagnosis_correct",
        "approval_valid",
        "recovery_verification_passed",
        "rollback_exact_hash_verified",
        "fault_time_a0_context_artifact_exists",
    )
    for field in optional_boolean_fields:
        value = terminal.get(field)
        if value is not None and type(value) is not bool:
            raise ValueError(f"non-success terminal {field} is not an exact boolean")
    for field in ("invalid_refs", "visible_service_count"):
        value = terminal.get(field)
        if value is not None and not _nonnegative_int(value):
            raise ValueError(f"non-success terminal {field} is not an exact integer")
    count_mappings: dict[str, Mapping[object, object]] = {}
    for field in (
        "source_counts",
        "projection_broad_counts",
        "projection_diagnostic_counts",
    ):
        value = terminal.get(field, {})
        allowed_keys = _SOURCES if field == "source_counts" else _PROJECTION_SOURCES
        if (
            not isinstance(value, Mapping)
            or any(
                not isinstance(key, str) or not _nonnegative_int(item)
                for key, item in value.items()
            )
            or not set(value).issubset(allowed_keys)
        ):
            raise ValueError(f"non-success terminal {field} is not an exact count map")
        count_mappings[field] = value
    availability = terminal.get("source_availability", {})
    if (
        not isinstance(availability, Mapping)
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or value not in _SOURCE_STATUSES
            for key, value in availability.items()
        )
        or not set(availability).issubset(_SOURCES)
    ):
        raise ValueError("non-success terminal source availability is malformed")
    for field in ("empty_model_streams", "projection_reason_codes"):
        value = terminal.get(field, [])
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError(f"non-success terminal {field} is malformed")
        allowed = (
            _SOURCES if field == "empty_model_streams" else _PROJECTION_REASON_CODES
        )
        if len(set(value)) != len(value) or not set(value).issubset(allowed):
            raise ValueError(f"non-success terminal {field} is not a closed set")
    invalid_refs = terminal.get("invalid_refs")
    all_refs_resolve = terminal.get("all_refs_resolve")
    if (invalid_refs is None) != (all_refs_resolve is None) or (
        _nonnegative_int(invalid_refs) and all_refs_resolve is not (invalid_refs == 0)
    ):
        raise ValueError("non-success Evidence-ref truth is contradictory")
    if "cleanup_verdict" not in terminal or terminal.get(
        "cleanup_verdict"
    ) != cleanup.get("verdict"):
        raise ValueError("terminal cleanup verdict differs from its cleanup aggregate")
    expected_cleanup_failure = (
        "CLEANUP_FAILED" if cleanup.get("verdict") == "BLOCKED" else None
    )
    if (
        "cleanup_failure_code" not in terminal
        or terminal.get("cleanup_failure_code") != expected_cleanup_failure
    ):
        raise ValueError("terminal cleanup failure code contradicts its aggregate")
    if (
        terminal.get("approval_mode")
        != ("HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK")
        or terminal.get("approval_valid") is not True
    ):
        raise ValueError("non-success terminal approval truth is contradictory")
    claim_boundary = terminal.get("claim_boundary")
    if (
        not isinstance(claim_boundary, (list, tuple))
        or tuple(claim_boundary) != config.reporting.claim_boundary
    ):
        raise ValueError("non-success claim boundary differs from frozen v6 authority")
    for field in ("implementation_commit", "result_head"):
        value = terminal.get(field)
        if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
            raise ValueError("non-success terminal head is not exact")
    if terminal.get("result_head") != terminal.get("implementation_commit"):
        raise ValueError("non-success result head differs from implementation commit")
    count_fields = (
        "provider_calls",
        "model_calls",
        "fault_injections",
        "forward_mutations",
        "rollback_mutations",
    )
    counts: dict[str, int] = {}
    for field in count_fields:
        value = terminal.get(field)
        if type(value) is not int or value < 0:
            raise ValueError("non-success terminal stage count is missing or invalid")
        counts[field] = value

    def require_exact(**expected: int) -> None:
        if any(counts[field] != value for field, value in expected.items()):
            raise ValueError("non-success terminal has impossible stage counts")

    def require_cleanup_verdict(expected: str) -> None:
        if cleanup.get("verdict") != expected:
            raise ValueError("non-success terminal cleanup truth contradicts its stage")

    def require_failure_identity(
        *,
        failed_stage: str | None = None,
        failure_code: str | None = None,
        last_completed_stage: str | None = None,
    ) -> None:
        actual_failed = terminal.get("failed_stage")
        actual_code = terminal.get("failure_code")
        actual_last = terminal.get("last_completed_stage")
        if not isinstance(actual_failed, str) or not actual_failed:
            raise ValueError("non-success terminal failed stage is missing")
        if not isinstance(actual_code, str) or not actual_code:
            raise ValueError("non-success terminal failure code is missing")
        if not isinstance(actual_last, str) or not actual_last:
            raise ValueError("non-success terminal last completed stage is missing")
        if actual_failed == actual_last:
            raise ValueError("non-success terminal stage ordering is contradictory")
        if failed_stage is not None and actual_failed != failed_stage:
            raise ValueError(
                "non-success terminal failed stage contradicts its verdict"
            )
        if failure_code is not None and actual_code != failure_code:
            raise ValueError(
                "non-success terminal failure code contradicts its verdict"
            )
        if last_completed_stage is not None and actual_last != last_completed_stage:
            raise ValueError(
                "non-success terminal last completed stage contradicts its verdict"
            )

    def require_one_of_failure_identities(
        identities: set[tuple[str, str, str]],
    ) -> None:
        actual = (
            terminal.get("failed_stage"),
            terminal.get("failure_code"),
            terminal.get("last_completed_stage"),
        )
        if actual not in identities:
            raise ValueError(
                "non-success terminal root identity contradicts its verdict"
            )

    def require_absent(*fields: str) -> None:
        if any(terminal.get(field) is not None for field in fields):
            raise ValueError("non-success terminal contains future Gate truth")

    def require_no_diagnosis_or_later_truth() -> None:
        require_absent(
            "diagnosis_gate",
            "diagnosis_correct",
            "plan_action",
            "policy_verdict",
            "recovery_verification_passed",
        )

    def require_no_fault_or_later_truth() -> None:
        require_absent("fault_impact_passed")
        require_no_diagnosis_or_later_truth()

    def require_no_collected_source_evidence() -> None:
        if any(
            (
                bool(availability),
                *(bool(value) for value in count_mappings.values()),
                invalid_refs is not None,
                all_refs_resolve is not None,
                terminal.get("visible_service_count") is not None,
                bool(terminal.get("empty_model_streams", [])),
                bool(terminal.get("projection_reason_codes", [])),
            )
        ):
            raise ValueError("early terminal contains future source evidence")

    def require_no_projection_evidence() -> None:
        if any(
            (
                bool(count_mappings["projection_broad_counts"]),
                bool(count_mappings["projection_diagnostic_counts"]),
                terminal.get("visible_service_count") is not None,
                bool(terminal.get("empty_model_streams", [])),
                bool(terminal.get("projection_reason_codes", [])),
            )
        ):
            raise ValueError("pre-projection terminal contains future projection truth")

    def require_complete_available_source_evidence() -> None:
        source_counts = count_mappings["source_counts"]
        if any(
            (
                set(availability) != _SOURCES,
                set(source_counts) != _SOURCES,
                any(availability.get(source) != "AVAILABLE" for source in _SOURCES),
                any(
                    not _positive_int(source_counts.get(source)) for source in _SOURCES
                ),
                not _exact_int(invalid_refs, 0),
                all_refs_resolve is not True,
            )
        ):
            raise ValueError("non-success terminal lacks complete available sources")

    def require_source_gate_evidence(*, gate_failed: bool = True) -> None:
        source_counts = count_mappings["source_counts"]
        if set(availability) != _SOURCES or set(source_counts) != _SOURCES:
            raise ValueError("source failure lacks its exact three-source batch")
        for source in _SOURCES:
            status = availability.get(source)
            count = source_counts.get(source)
            if status == "AVAILABLE" and not _positive_int(count):
                raise ValueError("available source lacks a positive count")
            if status not in {"AVAILABLE", "INVALID_RECORD"} and not _exact_int(
                count, 0
            ):
                raise ValueError("unavailable source contains a nonzero count")
            if (
                status == "INVALID_RECORD"
                and _positive_int(count)
                and (not _positive_int(invalid_refs) or all_refs_resolve is not False)
            ):
                raise ValueError(
                    "invalid-record source count contradicts Evidence-ref truth"
                )
        if _positive_int(invalid_refs) and (
            all_refs_resolve is not False
            or not any(
                availability.get(source) == "INVALID_RECORD"
                and _positive_int(source_counts.get(source))
                for source in _SOURCES
            )
        ):
            raise ValueError(
                "invalid Evidence refs lack a positive invalid-record source"
            )
        all_sources_available = all(
            availability.get(source) == "AVAILABLE" for source in _SOURCES
        )
        if all_sources_available and (
            not _exact_int(invalid_refs, 0) or all_refs_resolve is not True
        ):
            raise ValueError("available sources contradict Evidence-ref truth")
        if gate_failed and (
            all(
                availability.get(source) == "AVAILABLE"
                and _positive_int(source_counts.get(source))
                for source in _SOURCES
            )
            and _exact_int(invalid_refs, 0)
            and all_refs_resolve is True
        ):
            raise ValueError("source failure contradicts an available source batch")

    def require_projection_summary_consistency(*, blocking: bool) -> None:
        broad = count_mappings["projection_broad_counts"]
        diagnostic = count_mappings["projection_diagnostic_counts"]
        expected_empty = {
            source.upper()
            for source in _PROJECTION_SOURCES
            if diagnostic.get(source) == 0
        }
        empty_streams = set(
            cast(
                list[str] | tuple[str, ...],
                terminal.get("empty_model_streams", []),
            )
        )
        reason_codes = set(
            cast(
                list[str] | tuple[str, ...],
                terminal.get("projection_reason_codes", []),
            )
        )
        if empty_streams != expected_empty:
            raise ValueError("non-success empty streams contradict projection counts")
        count_reason_truth = {
            "NO_BROAD_METRICS": broad.get("metrics") == 0,
            "NO_DIAGNOSTIC_METRICS": diagnostic.get("metrics") == 0,
            "NO_DIAGNOSTIC_LOGS": diagnostic.get("logs") == 0,
            "NO_DIAGNOSTIC_TRACES": diagnostic.get("traces") == 0,
            "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE": (
                diagnostic.get("logs") == 0 and diagnostic.get("traces") == 0
            ),
        }
        if any(
            (code in reason_codes) != expected
            for code, expected in count_reason_truth.items()
        ):
            raise ValueError("non-success projection reasons contradict counts")
        has_blocking_reason = bool(reason_codes & _PROJECTION_BLOCKING_REASONS)
        if has_blocking_reason is not blocking:
            raise ValueError(
                "projection blocking reasons contradict its terminal stage"
            )

    def require_complete_projection_evidence() -> None:
        broad = count_mappings["projection_broad_counts"]
        diagnostic = count_mappings["projection_diagnostic_counts"]
        if any(
            (
                set(broad) != _PROJECTION_SOURCES,
                set(diagnostic) != _PROJECTION_SOURCES,
                not _positive_int(broad.get("metrics")),
                not _positive_int(diagnostic.get("metrics")),
                not (
                    _positive_int(diagnostic.get("logs"))
                    or _positive_int(diagnostic.get("traces"))
                ),
            )
        ):
            raise ValueError("non-success terminal lacks complete projection evidence")
        visible = terminal.get("visible_service_count")
        if type(visible) is not int or not 3 <= visible <= 8:
            raise ValueError("non-success terminal visible-service truth is invalid")
        require_projection_summary_consistency(blocking=False)

    def require_projection_failure_evidence() -> None:
        broad = count_mappings["projection_broad_counts"]
        diagnostic = count_mappings["projection_diagnostic_counts"]
        require_complete_available_source_evidence()
        if not broad and not diagnostic:
            require_no_projection_evidence()
            return
        if (
            set(broad) != _PROJECTION_SOURCES
            or set(diagnostic) != _PROJECTION_SOURCES
            or terminal.get("visible_service_count") is not None
        ):
            raise ValueError("projection failure summary is incomplete")
        require_projection_summary_consistency(blocking=True)

    def require_diagnosis_failure_truth() -> None:
        if (
            terminal.get("fault_impact_passed") is not True
            or terminal.get("diagnosis_gate") is not False
        ):
            raise ValueError("Diagnosis-Gate terminal contradicts its Gates")
        if type(terminal.get("diagnosis_correct")) is not bool:
            raise ValueError("Diagnosis-Gate terminal lacks exact diagnosis truth")
        require_absent(
            "plan_action",
            "policy_verdict",
            "recovery_verification_passed",
        )

    def require_policy_failure_truth() -> None:
        if any(
            (
                terminal.get("fault_impact_passed") is not True,
                terminal.get("diagnosis_gate") is not True,
                terminal.get("diagnosis_correct") is not True,
                terminal.get("plan_action") != "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                terminal.get("policy_verdict") != "DENY",
            )
        ):
            raise ValueError("Policy-rejected terminal contradicts its Gates")
        require_absent("recovery_verification_passed")

    def require_context_binding() -> None:
        context_sha = terminal.get("fault_time_a0_context_sha256")
        provider_sha = terminal.get("provider_live_context_sha256")
        if any(
            (
                not _exact_int(terminal.get("a0_context_builder_calls"), 1),
                terminal.get("fault_time_a0_context_artifact_exists") is not True,
                not isinstance(context_sha, str),
                isinstance(context_sha, str) and _SHA256.fullmatch(context_sha) is None,
                not isinstance(provider_sha, str),
                isinstance(provider_sha, str)
                and _SHA256.fullmatch(provider_sha) is None,
                context_sha != provider_sha,
            )
        ):
            raise ValueError("non-success fault-time context binding is invalid")

    def require_projection_context_attempt() -> None:
        builder_calls = terminal.get("a0_context_builder_calls")
        if any(
            (
                not _exact_int(builder_calls, 1),
                "fault_time_a0_context_artifact_exists" in terminal,
                terminal.get("fault_time_a0_context_sha256") is not None,
                "provider_live_context_sha256" in terminal,
            )
        ):
            raise ValueError("projection terminal has impossible context truth")

    def require_context_attempt_or_binding() -> None:
        if terminal.get("fault_time_a0_context_artifact_exists") is True:
            require_context_binding()
        else:
            require_projection_context_attempt()

    def require_no_context() -> None:
        builder_calls = terminal.get("a0_context_builder_calls")
        if any(
            (
                type(builder_calls) is not int,
                type(builder_calls) is int and builder_calls != 0,
                "fault_time_a0_context_artifact_exists" in terminal,
                terminal.get("fault_time_a0_context_sha256") is not None,
                "provider_live_context_sha256" in terminal,
            )
        ):
            raise ValueError("early terminal contains impossible context proof")

    def require_no_rollback_proof() -> None:
        if any(
            (
                counts["rollback_mutations"] != 0,
                "rollback_exact_hash_verified" in terminal,
            )
        ):
            raise ValueError("pre-rollback terminal contains impossible rollback proof")

    def require_pre_mutation_gates() -> None:
        require_context_binding()
        if any(
            (
                terminal.get("fault_impact_passed") is not True,
                terminal.get("diagnosis_gate") is not True,
                terminal.get("diagnosis_correct") is not True,
                terminal.get("plan_action") != "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                terminal.get("policy_verdict") != "ALLOW",
            )
        ):
            raise ValueError("non-success remediation Gate truth is contradictory")

    def require_unclassified_runtime_truth(
        unclassified_policy: tuple[str, frozenset[int], str],
    ) -> None:
        _, allowed_fault_injections, phase = unclassified_policy
        if counts["fault_injections"] not in allowed_fault_injections:
            raise ValueError(
                "versioned unclassified terminal has impossible stage counts"
            )
        if phase in {
            "EARLY",
            "POST_COMPOSE",
            "PRE_SOURCE",
            "SOURCE_COLLECTION",
            "SOURCE_COMPLETE",
            "POST_PROJECTION",
        }:
            require_exact(
                provider_calls=1,
                model_calls=0,
                forward_mutations=0,
                rollback_mutations=0,
            )
        elif phase == "LIVE_DIAGNOSIS":
            require_exact(
                provider_calls=2,
                forward_mutations=0,
                rollback_mutations=0,
            )
            if counts["model_calls"] not in {0, 1}:
                raise ValueError(
                    "versioned unclassified terminal has impossible stage counts"
                )
        elif phase == "POLICY":
            require_exact(
                provider_calls=2,
                model_calls=1,
                forward_mutations=0,
                rollback_mutations=0,
            )
        else:
            require_exact(
                provider_calls=2,
                model_calls=1,
                rollback_mutations=0,
            )
            if counts["forward_mutations"] not in {0, 1}:
                raise ValueError(
                    "versioned unclassified terminal has impossible stage counts"
                )
        require_no_rollback_proof()
        if phase in {"EARLY", "POST_COMPOSE"}:
            require_no_context()
            require_no_fault_or_later_truth()
            require_no_collected_source_evidence()
        elif phase == "PRE_SOURCE":
            require_no_context()
            if counts["fault_injections"] == 0:
                if "fault_impact_passed" in terminal:
                    raise ValueError(
                        "pre-fault runtime failure contains fault-impact truth"
                    )
            elif (
                "fault_impact_passed" in terminal
                and terminal.get("fault_impact_passed") is not True
            ):
                raise ValueError(
                    "pre-source runtime failure contains contradictory fault truth"
                )
            require_no_diagnosis_or_later_truth()
            require_no_collected_source_evidence()
        elif phase == "SOURCE_COLLECTION":
            require_no_context()
            if terminal.get("fault_impact_passed") is not True:
                raise ValueError(
                    "source-collection runtime failure lacks fault-impact truth"
                )
            require_no_diagnosis_or_later_truth()
            require_no_collected_source_evidence()
        elif phase == "SOURCE_COMPLETE":
            require_no_context()
            if terminal.get("fault_impact_passed") is not True:
                raise ValueError("post-source runtime failure lacks fault-impact truth")
            require_no_diagnosis_or_later_truth()
            require_source_gate_evidence(gate_failed=False)
            require_no_projection_evidence()
        else:
            if terminal.get("fault_impact_passed") is not True:
                raise ValueError(
                    "late unclassified runtime failure lacks fault-impact truth"
                )
            require_complete_available_source_evidence()
            require_complete_projection_evidence()
            if phase == "POST_PROJECTION":
                require_context_attempt_or_binding()
                require_no_diagnosis_or_later_truth()
            else:
                require_context_binding()
                if phase == "LIVE_DIAGNOSIS":
                    require_absent(
                        "diagnosis_gate",
                        "diagnosis_correct",
                        "plan_action",
                        "policy_verdict",
                        "recovery_verification_passed",
                    )
                elif phase == "POLICY":
                    if any(
                        (
                            terminal.get("diagnosis_gate") is not True,
                            terminal.get("diagnosis_correct") is not True,
                            terminal.get("plan_action")
                            not in {
                                None,
                                "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                            },
                            terminal.get("policy_verdict") is not None,
                        )
                    ):
                        raise ValueError(
                            "Policy runtime failure contains contradictory Gate truth"
                        )
                    require_absent("recovery_verification_passed")
                else:
                    require_pre_mutation_gates()
                    require_absent("recovery_verification_passed")

    if verdict == "BLOCKED_PROVIDER_PREFLIGHT":
        require_cleanup_verdict("NOT_REQUIRED")
        require_exact(
            model_calls=0,
            fault_injections=0,
            forward_mutations=0,
            rollback_mutations=0,
        )
        if counts["provider_calls"] not in {0, 1} or (
            "provider_preflight_passed" in terminal
        ):
            raise ValueError("Provider-preflight terminal has impossible stage counts")
        require_failure_identity(
            failed_stage="PROVIDER_PREFLIGHT",
            failure_code="PROVIDER_PREFLIGHT_FAILED",
            last_completed_stage="WORKTREE_VERIFIED",
        )
        require_no_context()
        require_no_rollback_proof()
        require_no_fault_or_later_truth()
        require_no_collected_source_evidence()
    elif verdict.startswith("BLOCKED_E2E_V6_"):
        if terminal.get("provider_preflight_passed") is not True:
            raise ValueError("post-preflight terminal has impossible stage counts")
        suffix = verdict.removeprefix("BLOCKED_E2E_V6_")
        exact_identities = {
            "IMAGE_AUTHORITY_MISMATCH": {
                (
                    "IMAGE_AUTHORITY_LOAD_STARTED",
                    "IMAGE_AUTHORITY_MISMATCH",
                    "COMPOSE_RESOLVED",
                ),
                (
                    "IMAGE_AUTHORITY_VERIFIED",
                    "IMAGE_AUTHORITY_MISMATCH",
                    "IMAGE_AUTHORITY_LOAD_STARTED",
                ),
            },
            "COMPOSE_STRUCTURE_IDENTITY_MISMATCH": {
                (
                    "COMPOSE_STRUCTURE_HASH_VERIFIED",
                    "COMPOSE_STRUCTURE_IDENTITY_MISMATCH",
                    "IMAGE_AUTHORITY_VERIFIED",
                ),
            },
            "COMPOSE_UP_FAILED": {
                (
                    "COMPOSE_START_RETURNED",
                    "COMPOSE_UP_FAILED",
                    "COMPOSE_START_REQUESTED",
                ),
            },
            "SERVICE_HEALTH_TIMEOUT": {
                (
                    "SERVICE_HEALTH_WAIT_STARTED",
                    "SERVICE_HEALTH_TIMEOUT",
                    "OWNED_RESOURCE_INVENTORY_VERIFIED",
                ),
                (
                    "SERVICES_HEALTHY",
                    "SERVICE_HEALTH_TIMEOUT",
                    "SERVICE_HEALTH_WAIT_STARTED",
                ),
            },
            "BASELINE_CONFIGURATION_UNAVAILABLE": {
                (
                    "BASELINE_CONFIGURATION_READ_STARTED",
                    "BASELINE_CONFIGURATION_UNAVAILABLE",
                    "STABILIZATION_COMPLETED",
                ),
            },
            "BASELINE_CONFIGURATION_MISMATCH": {
                (
                    "BASELINE_CONFIGURATION_VERIFIED",
                    "BASELINE_CONFIGURATION_MISMATCH",
                    "BASELINE_CONFIGURATION_READ_STARTED",
                ),
            },
        }
        if suffix == "UNCLASSIFIED_RUNTIME_FAILURE":
            require_failure_identity()
            identity = cast(
                tuple[str, str, str],
                (
                    terminal.get("failed_stage"),
                    terminal.get("failure_code"),
                    terminal.get("last_completed_stage"),
                ),
            )
            unclassified_policy = _UNCLASSIFIED_RUNTIME_IDENTITIES.get(identity)
            if unclassified_policy is None:
                raise ValueError(
                    "versioned unclassified terminal lacks an exact runtime identity"
                )
            expected_cleanup = unclassified_policy[0]
            require_cleanup_verdict(expected_cleanup)
            require_unclassified_runtime_truth(unclassified_policy)
        elif suffix in exact_identities:
            require_exact(
                provider_calls=1,
                model_calls=0,
                forward_mutations=0,
                rollback_mutations=0,
            )
            require_exact(fault_injections=0)
            require_one_of_failure_identities(exact_identities[suffix])
            require_cleanup_verdict(
                "NOT_REQUIRED"
                if suffix
                in {
                    "IMAGE_AUTHORITY_MISMATCH",
                    "COMPOSE_STRUCTURE_IDENTITY_MISMATCH",
                }
                else "CLEAN"
            )
        else:
            raise ValueError("versioned non-success terminal lacks an exact identity")
        if suffix != "UNCLASSIFIED_RUNTIME_FAILURE":
            require_no_context()
            require_no_rollback_proof()
            require_no_fault_or_later_truth()
            require_no_collected_source_evidence()
    elif verdict in {
        "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
        "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
    }:
        require_cleanup_verdict("CLEAN")
        require_exact(
            provider_calls=1,
            model_calls=0,
            fault_injections=1,
            forward_mutations=0,
            rollback_mutations=0,
        )
        if verdict == "BLOCKED_FAULT_IMPACT_NOT_OBSERVED":
            if "fault_impact_passed" in terminal:
                raise ValueError("fault-impact terminal contradicts its Gate")
            require_failure_identity(
                failed_stage="FAULT_IMPACT_GATE_EVALUATED",
                failure_code="FAULT_IMPACT_NOT_OBSERVED",
                last_completed_stage="BASELINE_CONFIGURATION_VERIFIED",
            )
            require_no_context()
            require_no_diagnosis_or_later_truth()
            require_no_collected_source_evidence()
        elif verdict == "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE":
            if terminal.get("fault_impact_passed") is not True:
                raise ValueError("source terminal lacks a passed fault-impact Gate")
            require_failure_identity(
                failed_stage="LIVE_TELEMETRY_SOURCE_GATE_EVALUATED",
                failure_code="LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED",
                last_completed_stage="FAULT_IMPACT_GATE_EVALUATED",
            )
            require_no_context()
            require_no_diagnosis_or_later_truth()
            require_source_gate_evidence()
            require_no_projection_evidence()
        else:
            if terminal.get("fault_impact_passed") is not True:
                raise ValueError("projection terminal contradicts its completed Gates")
            require_projection_context_attempt()
            require_no_diagnosis_or_later_truth()
            require_projection_failure_evidence()
            require_failure_identity(
                failed_stage="MULTISERVICE_PROJECTION_COMPLETED",
                failure_code="MULTISERVICE_PROJECTION_FAILED",
                last_completed_stage="MULTISERVICE_PROJECTION_STARTED",
            )
        require_no_rollback_proof()
    elif verdict in {
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
        "BLOCKED_POLICY_REJECTED",
    }:
        require_cleanup_verdict("CLEAN")
        require_exact(
            provider_calls=2,
            model_calls=1,
            fault_injections=1,
            forward_mutations=0,
            rollback_mutations=0,
        )
        require_context_binding()
        require_no_rollback_proof()
        require_complete_available_source_evidence()
        require_complete_projection_evidence()
        if terminal.get("fault_impact_passed") is not True:
            raise ValueError("diagnosis or Policy terminal lacks fault-impact truth")
        if verdict == "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION":
            require_diagnosis_failure_truth()
            require_failure_identity(
                failed_stage="DIAGNOSIS_GATE_EVALUATED",
                failure_code="DIAGNOSIS_GATE_NOT_PASSED",
                last_completed_stage="MULTISERVICE_PROJECTION_COMPLETED",
            )
        else:
            require_policy_failure_truth()
            require_failure_identity(
                failed_stage="POLICY_GATE_EVALUATED",
                failure_code="POLICY_REJECTED",
                last_completed_stage="MULTISERVICE_PROJECTION_COMPLETED",
            )
    elif verdict in {
        "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
        "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
    }:
        require_cleanup_verdict("CLEAN")
        require_exact(
            provider_calls=2,
            model_calls=1,
            fault_injections=1,
            forward_mutations=1,
        )
        require_pre_mutation_gates()
        require_complete_available_source_evidence()
        require_complete_projection_evidence()
        if terminal.get("recovery_verification_passed") is not False:
            raise ValueError("remediation terminal contradicts recovery verification")
        if verdict == "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED":
            if (
                counts["rollback_mutations"] != 1
                or terminal.get("rollback_exact_hash_verified") is not True
            ):
                raise ValueError(
                    "rollback-completed terminal lacks exact rollback truth"
                )
            require_failure_identity(
                failed_stage="REMEDIATION_VERIFICATION_EVALUATED",
                failure_code="REMEDIATION_NOT_VERIFIED",
                last_completed_stage="MULTISERVICE_PROJECTION_COMPLETED",
            )
        else:
            if counts["rollback_mutations"] not in {
                0,
                1,
            } or not _optional_exact_false(
                terminal.get("rollback_exact_hash_verified")
            ):
                raise ValueError("rollback-failed terminal contradicts rollback truth")
            require_failure_identity(
                failed_stage="ROLLBACK_VERIFICATION_EVALUATED",
                failure_code="ROLLBACK_FAILED",
                last_completed_stage="MULTISERVICE_PROJECTION_COMPLETED",
            )
    elif verdict == "BLOCKED_PUBLIC_RESULT_VERIFICATION":
        source_verdict = terminal.get("public_result_source_verdict")
        policy = get_invocation_b_verdict_policy("v6")
        if (
            not isinstance(source_verdict, str)
            or source_verdict == verdict
            or source_verdict not in policy.legal_terminals
        ):
            raise ValueError("public-result failure lacks its source terminal")
        source = dict(terminal)
        source["verdict"] = source_verdict
        for field in ("failed_stage", "last_completed_stage", "failure_code"):
            source[field] = terminal.get(f"public_result_source_{field}")
        if source_verdict == policy.success:
            if any(
                terminal.get(f"public_result_source_{field}") is not None
                for field in ("failed_stage", "last_completed_stage", "failure_code")
            ):
                raise ValueError(
                    "public-result success source contains a failure identity"
                )
            _require_success_invariants(config, source)
        else:
            _require_non_success_invariants(
                config,
                source,
                verdict=source_verdict,
            )
        expected_last_completed = (
            "CLEANUP_COMPLETED"
            if cleanup.get("verdict") == "CLEAN"
            else terminal.get("public_result_source_last_completed_stage")
        )
        if not isinstance(expected_last_completed, str):
            raise ValueError("public-result failure lacks an exact completion root")
        require_failure_identity(
            failed_stage="PUBLIC_RESULT_VERIFICATION",
            failure_code="PUBLIC_RESULT_VERIFICATION_FAILED",
            last_completed_stage=expected_last_completed,
        )
    elif verdict == "BLOCKED_CLEANUP_INCOMPLETE":
        if terminal.get("cleanup_failure_code") != "CLEANUP_FAILED":
            raise ValueError("cleanup terminal lacks its exact cleanup failure code")
        cleanup_identity = cast(
            tuple[str, str, str],
            (
                terminal.get("failed_stage"),
                terminal.get("failure_code"),
                terminal.get("last_completed_stage"),
            ),
        )
        cleanup_unclassified_policy = _UNCLASSIFIED_RUNTIME_IDENTITIES.get(
            cleanup_identity
        )
        if cleanup_unclassified_policy is None and any(
            (
                counts["provider_calls"] > 2,
                counts["model_calls"] > 1,
                counts["fault_injections"] > 1,
                counts["forward_mutations"] > 1,
                counts["rollback_mutations"] > 1,
                counts["model_calls"] == 1 and counts["provider_calls"] != 2,
                counts["provider_calls"] == 2 and counts["model_calls"] != 1,
                counts["model_calls"] == 1 and counts["fault_injections"] != 1,
                counts["fault_injections"] == 1 and counts["provider_calls"] < 1,
                counts["forward_mutations"] == 1
                and (counts["model_calls"] != 1 or counts["fault_injections"] != 1),
                counts["rollback_mutations"] == 1 and counts["forward_mutations"] != 1,
            )
        ):
            raise ValueError("cleanup terminal has impossible stage counts")
        if cleanup_unclassified_policy is not None:
            require_unclassified_runtime_truth(cleanup_unclassified_policy)
        elif counts["model_calls"] == 1:
            require_context_binding()
        elif terminal.get("failed_stage") == "MULTISERVICE_PROJECTION_COMPLETED":
            require_projection_context_attempt()
        else:
            require_no_context()
        if counts["forward_mutations"] == 1:
            require_pre_mutation_gates()
        if (
            terminal.get("recovery_verification_passed") is True
            and counts["forward_mutations"] != 1
        ):
            raise ValueError("cleanup terminal recovery truth is impossible")
        failed_stage = terminal.get("failed_stage")
        if cleanup_unclassified_policy is not None:
            pass
        elif failed_stage in {
            "COMPOSE_START_RETURNED",
            "OWNED_RESOURCE_INVENTORY_VERIFIED",
            "SERVICE_HEALTH_WAIT_STARTED",
            "SERVICES_HEALTHY",
            "STABILIZATION_STARTED",
            "BASELINE_CONFIGURATION_READ_STARTED",
            "BASELINE_CONFIGURATION_VERIFIED",
        }:
            require_exact(
                provider_calls=1,
                model_calls=0,
                fault_injections=0,
                forward_mutations=0,
                rollback_mutations=0,
            )
            require_no_fault_or_later_truth()
            require_no_collected_source_evidence()
        elif failed_stage == "SOURCE_CAPTURE_WINDOW_STARTED":
            require_exact(
                provider_calls=1,
                model_calls=0,
                forward_mutations=0,
                rollback_mutations=0,
            )
            if counts["fault_injections"] not in {0, 1}:
                raise ValueError("cleanup terminal has impossible stage counts")
            if counts["fault_injections"] == 0:
                if "fault_impact_passed" in terminal:
                    raise ValueError(
                        "pre-fault cleanup root contains fault-impact truth"
                    )
            elif (
                "fault_impact_passed" in terminal
                and terminal.get("fault_impact_passed") is not True
            ):
                raise ValueError(
                    "pre-source cleanup root contains contradictory fault truth"
                )
            require_no_diagnosis_or_later_truth()
            require_no_collected_source_evidence()
        elif failed_stage == "FAULT_IMPACT_GATE_EVALUATED":
            require_exact(
                provider_calls=1,
                model_calls=0,
                fault_injections=1,
                forward_mutations=0,
                rollback_mutations=0,
            )
            if "fault_impact_passed" in terminal:
                raise ValueError("cleanup-wrapped fault-impact truth is contradictory")
            require_no_diagnosis_or_later_truth()
            require_no_collected_source_evidence()
        elif failed_stage in {
            "LIVE_TELEMETRY_SOURCE_GATE_EVALUATED",
            "MULTISERVICE_PROJECTION_COMPLETED",
        }:
            require_exact(
                provider_calls=1,
                model_calls=0,
                fault_injections=1,
                forward_mutations=0,
                rollback_mutations=0,
            )
            if terminal.get("fault_impact_passed") is not True:
                raise ValueError("cleanup-wrapped source truth lacks fault impact")
            require_no_diagnosis_or_later_truth()
            if failed_stage == "LIVE_TELEMETRY_SOURCE_GATE_EVALUATED":
                require_source_gate_evidence()
                require_no_projection_evidence()
            else:
                require_projection_failure_evidence()
        elif failed_stage == "DIAGNOSIS_GATE_EVALUATED":
            require_exact(
                provider_calls=2,
                model_calls=1,
                fault_injections=1,
                forward_mutations=0,
                rollback_mutations=0,
            )
            require_diagnosis_failure_truth()
            require_complete_available_source_evidence()
            require_complete_projection_evidence()
        elif failed_stage == "POLICY_GATE_EVALUATED":
            require_exact(
                provider_calls=2,
                model_calls=1,
                fault_injections=1,
                forward_mutations=0,
                rollback_mutations=0,
            )
            require_policy_failure_truth()
            require_complete_available_source_evidence()
            require_complete_projection_evidence()
        elif failed_stage in {
            "REMEDIATION_VERIFICATION_EVALUATED",
            "ROLLBACK_VERIFICATION_EVALUATED",
        }:
            require_exact(
                provider_calls=2,
                model_calls=1,
                fault_injections=1,
                forward_mutations=1,
            )
            if terminal.get("recovery_verification_passed") is not False:
                raise ValueError("cleanup-wrapped recovery truth is contradictory")
            require_complete_available_source_evidence()
            require_complete_projection_evidence()
        elif failed_stage in {"BASELINE_RESTORED", "CLEANUP_COMPLETED"}:
            require_exact(
                provider_calls=2,
                model_calls=1,
                fault_injections=1,
                forward_mutations=1,
                rollback_mutations=0,
            )
            require_pre_mutation_gates()
            if terminal.get("recovery_verification_passed") is not True:
                raise ValueError(
                    "cleanup-root terminal lacks successful recovery truth"
                )
            require_complete_available_source_evidence()
            require_complete_projection_evidence()
        if failed_stage == "REMEDIATION_VERIFICATION_EVALUATED":
            if (
                counts["rollback_mutations"] != 1
                or terminal.get("rollback_exact_hash_verified") is not True
            ):
                raise ValueError(
                    "cleanup-wrapped rollback-completed truth is contradictory"
                )
        elif failed_stage == "ROLLBACK_VERIFICATION_EVALUATED":
            if counts["rollback_mutations"] not in {
                0,
                1,
            } or not _optional_exact_false(
                terminal.get("rollback_exact_hash_verified")
            ):
                raise ValueError(
                    "cleanup-wrapped rollback failure truth is contradictory"
                )
        elif counts["rollback_mutations"] == 0:
            require_no_rollback_proof()
        else:
            raise ValueError("cleanup terminal has impossible rollback stage truth")
        cleanup_root_identities: set[tuple[str, str, str]] = {
            ("COMPOSE_START_RETURNED", "COMPOSE_UP_FAILED", "COMPOSE_START_REQUESTED"),
            (
                "SERVICE_HEALTH_WAIT_STARTED",
                "SERVICE_HEALTH_TIMEOUT",
                "OWNED_RESOURCE_INVENTORY_VERIFIED",
            ),
            (
                "SERVICES_HEALTHY",
                "SERVICE_HEALTH_TIMEOUT",
                "SERVICE_HEALTH_WAIT_STARTED",
            ),
            (
                "BASELINE_CONFIGURATION_READ_STARTED",
                "BASELINE_CONFIGURATION_UNAVAILABLE",
                "STABILIZATION_COMPLETED",
            ),
            (
                "BASELINE_CONFIGURATION_VERIFIED",
                "BASELINE_CONFIGURATION_MISMATCH",
                "BASELINE_CONFIGURATION_READ_STARTED",
            ),
            (
                "FAULT_IMPACT_GATE_EVALUATED",
                "FAULT_IMPACT_NOT_OBSERVED",
                "BASELINE_CONFIGURATION_VERIFIED",
            ),
            (
                "LIVE_TELEMETRY_SOURCE_GATE_EVALUATED",
                "LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED",
                "FAULT_IMPACT_GATE_EVALUATED",
            ),
            (
                "MULTISERVICE_PROJECTION_COMPLETED",
                "MULTISERVICE_PROJECTION_FAILED",
                "MULTISERVICE_PROJECTION_STARTED",
            ),
            (
                "DIAGNOSIS_GATE_EVALUATED",
                "DIAGNOSIS_GATE_NOT_PASSED",
                "MULTISERVICE_PROJECTION_COMPLETED",
            ),
            (
                "POLICY_GATE_EVALUATED",
                "POLICY_REJECTED",
                "MULTISERVICE_PROJECTION_COMPLETED",
            ),
            (
                "REMEDIATION_VERIFICATION_EVALUATED",
                "REMEDIATION_NOT_VERIFIED",
                "MULTISERVICE_PROJECTION_COMPLETED",
            ),
            (
                "ROLLBACK_VERIFICATION_EVALUATED",
                "ROLLBACK_FAILED",
                "MULTISERVICE_PROJECTION_COMPLETED",
            ),
            ("BASELINE_RESTORED", "CLEANUP_FAILED", "CLEANUP_STARTED"),
            ("CLEANUP_COMPLETED", "CLEANUP_FAILED", "BASELINE_RESTORED"),
            ("CLEANUP_COMPLETED", "CLEANUP_FAILED", "COMPOSE_DOWN_RETURNED"),
        }
        require_failure_identity()
        root_identity = cast(
            tuple[str, str, str],
            (
                terminal.get("failed_stage"),
                terminal.get("failure_code"),
                terminal.get("last_completed_stage"),
            ),
        )
        unclassified_policy = _UNCLASSIFIED_RUNTIME_IDENTITIES.get(root_identity)
        if root_identity in cleanup_root_identities:
            pass
        elif unclassified_policy is not None and unclassified_policy[0] == "CLEAN":
            pass
        else:
            raise ValueError(
                "cleanup terminal lacks a recognized preserved root failure"
            )
    else:
        raise ValueError("non-success terminal lacks a stage-count policy")
    if verdict not in {
        "BLOCKED_PROVIDER_PREFLIGHT",
        "BLOCKED_PUBLIC_RESULT_VERIFICATION",
    } and (
        terminal.get("provider_preflight_passed") is not True
        or counts["provider_calls"] < 1
    ):
        raise ValueError("post-preflight terminal contradicts Provider preflight")
    if cleanup.get("verdict") == "BLOCKED" and verdict != "BLOCKED_CLEANUP_INCOMPLETE":
        raise ValueError("cleanup failure did not control the public terminal")
    if verdict == "BLOCKED_CLEANUP_INCOMPLETE" and cleanup.get("verdict") != "BLOCKED":
        raise ValueError("cleanup terminal lacks blocked cleanup truth")


def _safe_public_core(
    config: E2EV6Config, terminal: Mapping[str, object]
) -> dict[str, object]:
    core = {
        "schema_version": "live-e2e.public-result.v6",
        "version": config.authority.version,
        "verdict": terminal.get("verdict"),
        "implementation_commit": terminal.get("implementation_commit"),
        "result_head": terminal.get("result_head"),
        "source_availability": terminal.get("source_availability", {}),
        "source_counts": terminal.get("source_counts", {}),
        "invalid_refs": terminal.get("invalid_refs"),
        "all_refs_resolve": terminal.get("all_refs_resolve"),
        "projection_broad_counts": terminal.get("projection_broad_counts", {}),
        "projection_diagnostic_counts": terminal.get(
            "projection_diagnostic_counts", {}
        ),
        "empty_model_streams": _safe_list(terminal.get("empty_model_streams", [])),
        "projection_reason_codes": _safe_list(
            terminal.get("projection_reason_codes", [])
        ),
        "visible_service_count": terminal.get("visible_service_count"),
        "a0_context_builder_calls": terminal.get("a0_context_builder_calls", 0),
        "fault_time_a0_context_artifact_exists": terminal.get(
            "fault_time_a0_context_artifact_exists", False
        ),
        "fault_time_a0_context_sha256": terminal.get("fault_time_a0_context_sha256"),
        "provider_live_context_sha256": terminal.get("provider_live_context_sha256"),
        "fault_injections": terminal.get("fault_injections", 0),
        "provider_calls": terminal.get("provider_calls", 0),
        "model_calls": terminal.get("model_calls", 0),
        "forward_mutations": terminal.get("forward_mutations", 0),
        "rollback_mutations": terminal.get("rollback_mutations", 0),
        "provider_preflight_passed": terminal.get("provider_preflight_passed"),
        "fault_impact_gate": terminal.get("fault_impact_passed"),
        "diagnosis_gate": terminal.get("diagnosis_gate"),
        "diagnosis_correct": terminal.get("diagnosis_correct"),
        "plan_action": terminal.get("plan_action"),
        "approval_mode": terminal.get("approval_mode"),
        "approval_valid": terminal.get("approval_valid"),
        "policy_verdict": terminal.get("policy_verdict"),
        "recovery_verification": terminal.get("recovery_verification_passed"),
        "rollback_exact_hash_verified": terminal.get("rollback_exact_hash_verified"),
        "cleanup": terminal.get("cleanup"),
        "claim_boundary": list(config.reporting.claim_boundary),
    }
    run_generation = getattr(config.authority, "run_generation", None)
    if run_generation is not None:
        core.update(
            {
                "software_version": terminal.get("software_version"),
                "runtime_policy_version": terminal.get(
                    "runtime_policy_version"
                ),
                "run_generation": terminal.get("run_generation"),
                "predecessor_original_terminal": terminal.get(
                    "predecessor_original_terminal"
                ),
                "predecessor_original_result_head": terminal.get(
                    "predecessor_original_result_head"
                ),
                "original_result_preserved": terminal.get(
                    "original_result_preserved"
                ),
                "accepted_live_run_sha256": terminal.get(
                    "accepted_live_run_sha256"
                ),
            }
        )
        if getattr(config.authority, "predecessor_public_terminal", None) is not None:
            core.update(
                {
                    "predecessor_public_terminal": terminal.get(
                        "predecessor_public_terminal"
                    ),
                    "predecessor_sealed_source_verdict": terminal.get(
                        "predecessor_sealed_source_verdict"
                    ),
                    "predecessor_sealed_terminal_sha256": terminal.get(
                        "predecessor_sealed_terminal_sha256"
                    ),
                    "predecessor_accepted_live_run_sha256": terminal.get(
                        "predecessor_accepted_live_run_sha256"
                    ),
                    "predecessor_final_closure_sha256": terminal.get(
                        "predecessor_final_closure_sha256"
                    ),
                }
            )
    return core


def build_expected_public_result(
    config: E2EV6Config,
    sealed_private_terminal: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(sealed_private_terminal, Mapping):
        raise ValueError("sealed private terminal is required")
    if (
        sealed_private_terminal.get("schema_version")
        != "live-e2e.invocation-b-terminal.v6"
    ):
        raise ValueError("sealed private terminal schema differs from v6")
    verdict = sealed_private_terminal.get("verdict")
    policy = get_invocation_b_verdict_policy("v6")
    if not isinstance(verdict, str) or verdict not in policy.legal_terminals:
        raise ValueError("sealed private terminal is not legal for v6")
    if sealed_private_terminal.get("version") != config.authority.version:
        raise ValueError("sealed private terminal version differs")
    run_generation = getattr(config.authority, "run_generation", None)
    if run_generation is not None:
        accepted = sealed_private_terminal.get("accepted_live_run_sha256")
        predecessor_public_terminal = getattr(
            config.authority, "predecessor_public_terminal", None
        )
        if any(
            (
                sealed_private_terminal.get("software_version")
                != getattr(config.authority, "software_version", None),
                sealed_private_terminal.get("runtime_policy_version")
                != getattr(config.authority, "runtime_policy_version", None),
                sealed_private_terminal.get("run_generation") != run_generation,
                sealed_private_terminal.get("predecessor_original_terminal")
                != config.authority.predecessor_terminal,
                sealed_private_terminal.get("predecessor_original_result_head")
                != getattr(config.authority, "predecessor_result_head", None),
                sealed_private_terminal.get("original_result_preserved") is not True,
                (
                    sealed_private_terminal.get("accepted_live_run_sealed") is True
                    and (
                        not isinstance(accepted, str)
                        or _SHA256.fullmatch(accepted) is None
                    )
                ),
                (
                    predecessor_public_terminal is not None
                    and any(
                        (
                            sealed_private_terminal.get(
                                "predecessor_public_terminal"
                            )
                            != predecessor_public_terminal,
                            sealed_private_terminal.get(
                                "predecessor_sealed_source_verdict"
                            )
                            != getattr(
                                config.authority,
                                "predecessor_sealed_source_verdict",
                                None,
                            ),
                            sealed_private_terminal.get(
                                "predecessor_sealed_terminal_sha256"
                            )
                            != getattr(
                                config.authority,
                                "predecessor_sealed_terminal_sha256",
                                None,
                            ),
                            sealed_private_terminal.get(
                                "predecessor_accepted_live_run_sha256"
                            )
                            != getattr(
                                config.authority,
                                "predecessor_accepted_live_run_sha256",
                                None,
                            ),
                            sealed_private_terminal.get(
                                "predecessor_final_closure_sha256"
                            )
                            != getattr(
                                config.authority,
                                "predecessor_final_closure_sha256",
                                None,
                            ),
                        )
                    )
                ),
            )
        ):
            raise ValueError("sealed private terminal run-generation authority differs")
    if verdict == policy.success:
        _require_success_invariants(config, sealed_private_terminal)
    else:
        _require_non_success_invariants(
            config,
            sealed_private_terminal,
            verdict=verdict,
        )
    core = _safe_public_core(config, sealed_private_terminal)
    core["semantic_sha256"] = canonical_sha256(core)
    if scan_public_e2e_payload(core):
        raise ValueError("expected public result contains private or control data")
    return core


def verify_public_result(
    config: E2EV6Config,
    supplied_public_result: Mapping[str, object],
    sealed_private_terminal: Mapping[str, object] | None,
) -> None:
    if sealed_private_terminal is None:
        raise ValueError("sealed private terminal is required")
    expected = build_expected_public_result(config, sealed_private_terminal)
    if dict(supplied_public_result) != expected:
        raise ValueError("public result differs from the sealed private terminal")
    core = dict(supplied_public_result)
    semantic = core.pop("semantic_sha256", None)
    if semantic != canonical_sha256(core):
        raise ValueError("public result semantic SHA-256 differs")
    if scan_public_e2e_payload(supplied_public_result):
        raise ValueError("public result contains private or control data")


def write_fault_time_context_evidence(
    *,
    private_root: Path,
    invocation_b_root: Path,
    context: Any,
    terminal: dict[str, object],
) -> dict[str, object]:
    context_path = invocation_b_root / "fault-time-a0-context.json"
    metadata_path = invocation_b_root / "fault-time-a0-context-metadata.json"
    if context_path.exists() or context_path.is_symlink():
        raise FileExistsError("fault-time A0 context artifact is create-once")
    if metadata_path.exists() or metadata_path.is_symlink():
        raise FileExistsError("fault-time A0 context metadata is create-once")
    context_sha256 = canonical_sha256(context)
    write_private_json(context_path, context, create_once=True)
    artifact_file_sha256 = file_sha256(context_path)
    if artifact_file_sha256 != context_sha256:
        raise ValueError("fault-time A0 context file differs from canonical payload")
    summary_path = invocation_b_root / "projection-input-summary.json"
    source_results_path = invocation_b_root / "source-results.json"
    if any(
        path.is_symlink() or not path.is_file()
        for path in (summary_path, source_results_path)
    ):
        raise ValueError("fault-time A0 context lacks bound projection inputs")
    visible = getattr(context, "visible_entities", None)
    if not isinstance(visible, tuple):
        raise ValueError("fault-time A0 context visible entities are malformed")
    resolver_sha256 = terminal.get("combined_resolver_sha256")
    if (
        not isinstance(resolver_sha256, str)
        or _SHA256.fullmatch(resolver_sha256) is None
    ):
        raise ValueError("fault-time A0 context lacks the EvidenceResolver SHA-256")
    metadata_core: dict[str, object] = {
        "schema_version": "live-e2e.fault-time-a0-context-metadata.v6",
        "builder_call_count": terminal.get("a0_context_builder_calls"),
        "context_sha256": context_sha256,
        "provider_live_context_sha256": context_sha256,
        "context_artifact_relative_path": context_path.relative_to(
            private_root
        ).as_posix(),
        "context_artifact_file_sha256": artifact_file_sha256,
        "projection_input_summary_sha256": file_sha256(summary_path),
        "source_results_sha256": file_sha256(source_results_path),
        "evidence_resolver_sha256": resolver_sha256,
        "visible_service_count": len(visible),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if not _exact_int(metadata_core["builder_call_count"], 1):
        raise ValueError("fault-time A0 context builder did not run exactly once")
    metadata = {
        **metadata_core,
        "metadata_sha256": canonical_sha256(metadata_core),
    }
    write_private_json(metadata_path, metadata, create_once=True)
    terminal.update(
        {
            "fault_time_a0_context_artifact_exists": True,
            "fault_time_a0_context_sha256": context_sha256,
            "provider_live_context_sha256": context_sha256,
            "fault_time_a0_context_metadata_sha256": file_sha256(metadata_path),
        }
    )
    return metadata


__all__ = [
    "build_expected_public_result",
    "verify_public_result",
    "write_fault_time_context_evidence",
]
