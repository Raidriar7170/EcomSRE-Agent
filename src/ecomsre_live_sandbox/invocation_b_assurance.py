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
_SUCCESS_FORBIDDEN_REASONS = {
    "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE",
    "NO_DIAGNOSTIC_METRICS",
    "INSUFFICIENT_RESOLVABLE_EVIDENCE",
    "CONTROL_TRUTH_LEAK",
    "VISIBLE_SERVICE_COUNT_BELOW_MINIMUM",
}


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _safe_list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _require_cleanup(cleanup: object, *, success: bool) -> Mapping[str, object]:
    if not isinstance(cleanup, Mapping):
        raise ValueError("sealed private terminal cleanup aggregate is missing")
    verdict = cleanup.get("verdict")
    if verdict not in {"CLEAN", "NOT_REQUIRED", "BLOCKED"}:
        raise ValueError("sealed private terminal cleanup verdict is invalid")
    if success or verdict in {"CLEAN", "NOT_REQUIRED"}:
        if any(
            (
                cleanup.get("baseline_restored") is not True,
                cleanup.get("owned_containers") != 0,
                cleanup.get("owned_networks") != 0,
                cleanup.get("owned_volumes") != 0,
                cleanup.get("non_owned_resources_changed") is not False,
                success and verdict != "CLEAN",
            )
        ):
            raise ValueError("sealed private terminal cleanup truth is contradictory")
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

    availability = terminal.get("source_availability")
    counts = terminal.get("source_counts")
    broad = terminal.get("projection_broad_counts")
    diagnostic = terminal.get("projection_diagnostic_counts")
    if not all(isinstance(value, Mapping) for value in (availability, counts, broad, diagnostic)):
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
    if terminal.get("invalid_refs") != 0 or terminal.get("all_refs_resolve") is not True:
        raise ValueError("success Evidence refs are not fully resolvable")
    if set(broad_map) != _PROJECTION_SOURCES or any(
        not _nonnegative_int(broad_map.get(source)) for source in _PROJECTION_SOURCES
    ) or not _positive_int(broad_map.get("metrics")):
        raise ValueError("success broad projection counts are invalid")
    if set(diagnostic_map) != _PROJECTION_SOURCES or any(
        not _nonnegative_int(diagnostic_map.get(source)) for source in _PROJECTION_SOURCES
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
    if not isinstance(empty_streams, (list, tuple)) or sorted(empty_streams) != expected_empty:
        raise ValueError("success empty model streams contradict projection counts")
    if not isinstance(reasons, (list, tuple)):
        raise ValueError("success projection reason codes are missing")
    reason_set = set(reasons)
    for source in ("LOGS", "TRACES"):
        code = f"NO_DIAGNOSTIC_{source}"
        if (code in reason_set) != (source in expected_empty):
            raise ValueError("success empty-stream reason code is contradictory")
    if reason_set & _SUCCESS_FORBIDDEN_REASONS:
        raise ValueError("success projection contains a blocking reason code")

    visible = terminal.get("visible_service_count")
    if type(visible) is not int or not 3 <= visible <= 8:
        raise ValueError("success visible service count is outside the frozen range")
    context_sha = terminal.get("fault_time_a0_context_sha256")
    provider_sha = terminal.get("provider_live_context_sha256")
    if any(
        (
            terminal.get("a0_context_builder_calls") != 1,
            terminal.get("fault_time_a0_context_artifact_exists") is not True,
            not isinstance(context_sha, str),
            isinstance(context_sha, str) and _SHA256.fullmatch(context_sha) is None,
            not isinstance(provider_sha, str),
            isinstance(provider_sha, str) and _SHA256.fullmatch(provider_sha) is None,
            context_sha != provider_sha,
        )
    ):
        raise ValueError("success fault-time context binding is invalid")

    required = {
        "fault_injections": 1,
        "provider_calls": 2,
        "model_calls": 1,
        "forward_mutations": 1,
        "rollback_mutations": 0,
        "provider_preflight_passed": True,
        "fault_impact_passed": True,
        "diagnosis_gate": True,
        "diagnosis_correct": True,
        "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "approval_valid": True,
        "policy_verdict": "ALLOW",
        "recovery_verification_passed": True,
    }
    if any(terminal.get(key) != expected for key, expected in required.items()):
        raise ValueError("success execution or authorization Gates are contradictory")
    _require_cleanup(terminal.get("cleanup"), success=True)
    claim_boundary = terminal.get("claim_boundary")
    if not isinstance(claim_boundary, (list, tuple)) or tuple(claim_boundary) != config.reporting.claim_boundary:
        raise ValueError("success claim boundary differs from frozen v6 authority")


def _require_non_success_invariants(
    terminal: Mapping[str, object], *, verdict: str
) -> None:
    cleanup = _require_cleanup(terminal.get("cleanup"), success=False)
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

    if verdict == "BLOCKED_PROVIDER_PREFLIGHT":
        require_exact(
            model_calls=0,
            fault_injections=0,
            forward_mutations=0,
            rollback_mutations=0,
        )
        if counts["provider_calls"] not in {0, 1} or terminal.get(
            "provider_preflight_passed"
        ) is True:
            raise ValueError("Provider-preflight terminal has impossible stage counts")
    elif verdict.startswith("BLOCKED_E2E_V6_"):
        require_exact(
            provider_calls=1,
            model_calls=0,
            fault_injections=0,
            forward_mutations=0,
            rollback_mutations=0,
        )
        if terminal.get("provider_preflight_passed") is not True:
            raise ValueError("post-preflight terminal has impossible stage counts")
    elif verdict in {
        "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
        "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
    }:
        require_exact(
            provider_calls=1,
            model_calls=0,
            fault_injections=1,
            forward_mutations=0,
            rollback_mutations=0,
        )
    elif verdict in {
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
        "BLOCKED_POLICY_REJECTED",
    }:
        require_exact(
            provider_calls=2,
            model_calls=1,
            fault_injections=1,
            forward_mutations=0,
            rollback_mutations=0,
        )
    elif verdict in {
        "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
        "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
    }:
        require_exact(
            provider_calls=2,
            model_calls=1,
            fault_injections=1,
            forward_mutations=1,
        )
        if counts["rollback_mutations"] not in {0, 1}:
            raise ValueError("remediation terminal has impossible rollback count")
    elif verdict == "BLOCKED_PUBLIC_RESULT_VERIFICATION":
        require_exact(
            provider_calls=2,
            model_calls=1,
            fault_injections=1,
            forward_mutations=1,
            rollback_mutations=0,
        )
    elif verdict == "BLOCKED_CLEANUP_INCOMPLETE":
        if any(
            (
                counts["provider_calls"] > 2,
                counts["model_calls"] > 1,
                counts["fault_injections"] > 1,
                counts["forward_mutations"] > 1,
                counts["rollback_mutations"] > 1,
                counts["model_calls"] == 1
                and counts["provider_calls"] != 2,
                counts["provider_calls"] == 2
                and counts["model_calls"] != 1,
                counts["model_calls"] == 1
                and counts["fault_injections"] != 1,
                counts["fault_injections"] == 1
                and counts["provider_calls"] < 1,
                counts["forward_mutations"] == 1
                and (
                    counts["model_calls"] != 1
                    or counts["fault_injections"] != 1
                ),
                counts["rollback_mutations"] == 1
                and counts["forward_mutations"] != 1,
            )
        ):
            raise ValueError("cleanup terminal has impossible stage counts")
    else:
        raise ValueError("non-success terminal lacks a stage-count policy")
    if verdict != "BLOCKED_PROVIDER_PREFLIGHT" and (
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
    return {
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
        "fault_time_a0_context_sha256": terminal.get(
            "fault_time_a0_context_sha256"
        ),
        "provider_live_context_sha256": terminal.get(
            "provider_live_context_sha256"
        ),
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
        "rollback_exact_hash_verified": terminal.get(
            "rollback_exact_hash_verified"
        ),
        "cleanup": terminal.get("cleanup"),
        "claim_boundary": list(config.reporting.claim_boundary),
    }


def build_expected_public_result(
    config: E2EV6Config,
    sealed_private_terminal: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(sealed_private_terminal, Mapping):
        raise ValueError("sealed private terminal is required")
    verdict = sealed_private_terminal.get("verdict")
    policy = get_invocation_b_verdict_policy("v6")
    if not isinstance(verdict, str) or verdict not in policy.legal_terminals:
        raise ValueError("sealed private terminal is not legal for v6")
    if sealed_private_terminal.get("version") != config.authority.version:
        raise ValueError("sealed private terminal version differs")
    if verdict == policy.success:
        _require_success_invariants(config, sealed_private_terminal)
    else:
        _require_non_success_invariants(sealed_private_terminal, verdict=verdict)
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
    if not isinstance(resolver_sha256, str) or _SHA256.fullmatch(resolver_sha256) is None:
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
    if metadata_core["builder_call_count"] != 1:
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
