"""No-fault readiness and fault-time projection lifecycle for live E2E v5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from ecomsre_live_sandbox.contracts import (
    HumanApprovalRecord,
    canonical_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_contracts import scan_public_e2e_payload
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticFailureCode,
    DiagnosticStage,
)
from ecomsre_live_sandbox.e2e_source_batch import (
    JsonRequester,
    OrderedSourceBatch,
    collect_ordered_source_batch,
)
from ecomsre_live_sandbox.e2e_telemetry import scan_model_projection
from ecomsre_live_sandbox.e2e_v1 import _broad_metric_snapshot
from ecomsre_live_sandbox.e2e_v3 import NoFaultEvidence
import ecomsre_live_sandbox.e2e_v3 as e2e_v3
import ecomsre_live_sandbox.e2e_v4 as e2e_v4
from ecomsre_live_sandbox.e2e_v5_contracts import E2EV5Config, E2EV5PrivateRoots
from ecomsre_live_sandbox.environment import SandboxEnvironment
from ecomsre_live_sandbox.instrumentation_v2 import load_instrumentation_config
from ecomsre_live_sandbox.invocation_b_verdicts import (
    get_invocation_b_verdict_policy,
)
from ecomsre_live_sandbox.no_fault_readiness import (
    NoFaultReadiness,
    evaluate_no_fault_readiness,
)


TELEMETRY_V3_CONFIG_RELATIVE = Path("config/live-telemetry-instrumentation-v3")


@dataclass(frozen=True, slots=True)
class V5NoFaultEvidence(NoFaultEvidence):
    readiness: NoFaultReadiness
    source_batch: OrderedSourceBatch


def _collect_v5_no_fault_evidence(
    config: E2EV5Config,
    roots: E2EV5PrivateRoots,
    run_root: Path,
    tracker: Any,
    endpoints: Any,
    sleep: Callable[[float], None],
    *,
    services_healthy_count: int,
    baseline_exact: bool,
    metrics_request_json: JsonRequester | None = None,
    logs_request_json: JsonRequester | None = None,
    traces_request_json: JsonRequester | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> V5NoFaultEvidence:
    batch = collect_ordered_source_batch(
        instrumentation=load_instrumentation_config(
            config.repository_root / TELEMETRY_V3_CONFIG_RELATIVE
        ),
        endpoints=endpoints,
        telemetry_root=roots.telemetry,
        run_root=run_root,
        run_id=run_root.name,
        projection=config.fault_projection,
        tracker=tracker,
        sleep=sleep,
        metrics_request_json=metrics_request_json,
        logs_request_json=logs_request_json,
        traces_request_json=traces_request_json,
        now=now,
    )
    broad_snapshot = _broad_metric_snapshot(
        endpoints.prometheus,
        at=batch.window_end,
    )
    safe_metadata = {
        "source_statuses": {
            item.source: item.status.value for item in batch.source_results
        },
        "source_counts": batch.source_counts,
        "broad_metric_service_count": sum(
            values[0] > 0 for values in broad_snapshot.values()
        ),
    }
    control_findings = scan_model_projection(safe_metadata)
    private_permissions_valid = True
    try:
        roots.verify()
    except (OSError, ValueError):
        private_permissions_valid = False

    def evaluate() -> NoFaultReadiness:
        readiness = evaluate_no_fault_readiness(
            run_id=run_root.name,
            source_batch=batch,
            services_healthy_count=services_healthy_count,
            baseline_exact=baseline_exact,
            broad_metric_service_count=cast(
                int, safe_metadata["broad_metric_service_count"]
            ),
            logs_query_contract_completed=(
                batch.source_results[1].status.value == "AVAILABLE"
                and batch.source_results[1].attempt_count > 0
            ),
            traces_query_contract_completed=(
                batch.source_results[2].status.value == "AVAILABLE"
                and batch.source_results[2].attempt_count > 0
            ),
            private_permissions_valid=private_permissions_valid,
            control_truth_findings=control_findings,
        )
        write_private_json(
            run_root / "no-fault-readiness.json",
            readiness,
            create_once=True,
        )
        if not readiness.passed:
            raise RuntimeError("NoFaultReadiness did not pass")
        return readiness

    readiness = tracker.execute(
        DiagnosticStage.NO_FAULT_READINESS_EVALUATED,
        evaluate,
        failure_code=DiagnosticFailureCode.NO_FAULT_READINESS_FAILED,
        safe_aggregate=safe_metadata,
    )
    return V5NoFaultEvidence(
        metrics_status=batch.metrics_status,
        logs_status=batch.logs_status,
        traces_status=batch.traces_status,
        source_counts=batch.source_counts,
        invalid_refs=batch.invalid_ref_count,
        visible_service_count=readiness.broad_metric_service_count,
        scenario_truth_leaked=bool(readiness.control_truth_findings),
        projection_sha256=readiness.semantic_sha256,
        readiness=readiness,
        source_batch=batch,
    )


def run_development_probe(
    config: E2EV5Config,
    roots: E2EV5PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    kwargs.setdefault("evidence_collector", _collect_v5_no_fault_evidence)
    return e2e_v4.run_development_probe(cast(Any, config), cast(Any, roots), **kwargs)


def run_canonical_invocation_a(
    config: E2EV5Config,
    roots: E2EV5PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    kwargs.setdefault("evidence_collector", _collect_v5_no_fault_evidence)
    return e2e_v4.run_canonical_invocation_a(
        cast(Any, config), cast(Any, roots), **kwargs
    )


def record_human_approval_for_invocation_b(
    config: E2EV5Config,
    roots: E2EV5PrivateRoots,
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


_LEGAL_INVOCATION_B_TERMINALS = get_invocation_b_verdict_policy(
    "v5"
).legal_terminals


def _public_result_v5(
    config: E2EV5Config, terminal: Mapping[str, object]
) -> dict[str, object]:
    public = {
        "schema_version": "live-e2e.public-result.v5",
        "version": config.authority.version,
        "verdict": terminal.get("verdict"),
        "implementation_commit": terminal.get("implementation_commit"),
        "source_availability": terminal.get("source_availability", {}),
        "source_counts": terminal.get("source_counts", {}),
        "invalid_refs": terminal.get("invalid_refs"),
        "all_refs_resolve": terminal.get("all_refs_resolve"),
        "projection_broad_counts": terminal.get("projection_broad_counts", {}),
        "projection_diagnostic_counts": terminal.get(
            "projection_diagnostic_counts", {}
        ),
        "empty_model_streams": terminal.get("empty_model_streams", []),
        "projection_reason_codes": terminal.get("projection_reason_codes", []),
        "visible_service_count": terminal.get("visible_service_count"),
        "fault_injections": terminal.get("fault_injections", 0),
        "provider_calls": terminal.get("provider_calls", 0),
        "model_calls": terminal.get("model_calls", 0),
        "forward_mutations": terminal.get("forward_mutations", 0),
        "rollback_mutations": terminal.get("rollback_mutations", 0),
        "fault_impact_gate": terminal.get("fault_impact_passed"),
        "diagnosis_gate": terminal.get("diagnosis_gate"),
        "diagnosis_correct": terminal.get("diagnosis_correct"),
        "plan_action": terminal.get("plan_action"),
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "policy_verdict": terminal.get("policy_verdict"),
        "recovery_verification": terminal.get("recovery_verification_passed"),
        "rollback_exact_hash_verified": terminal.get(
            "rollback_exact_hash_verified"
        ),
        "cleanup": terminal.get("cleanup"),
        "claim_boundary": list(config.reporting.claim_boundary),
    }
    public["semantic_sha256"] = canonical_sha256(public)
    return public


def verify_public_result(
    config: E2EV5Config, value: Mapping[str, object]
) -> None:
    if value.get("verdict") not in _LEGAL_INVOCATION_B_TERMINALS:
        raise ValueError("public Invocation B terminal is not legal")
    core = dict(value)
    semantic = core.pop("semantic_sha256", None)
    if semantic != canonical_sha256(core):
        raise ValueError("public Invocation B semantic hash differs")
    if scan_public_e2e_payload(value):
        raise ValueError("public Invocation B result contains private or control data")
    if value.get("verdict") == config.authority.invocation_b_success:
        cleanup = value.get("cleanup")
        source_availability = value.get("source_availability")
        source_counts = value.get("source_counts")
        broad = value.get("projection_broad_counts")
        diagnostic = value.get("projection_diagnostic_counts")
        if any(
            (
                not isinstance(cleanup, Mapping),
                not isinstance(source_availability, Mapping),
                not isinstance(source_counts, Mapping),
                not isinstance(broad, Mapping),
                not isinstance(diagnostic, Mapping),
            )
        ):
            raise ValueError("public Invocation B success aggregates are missing")
        assert isinstance(cleanup, Mapping)
        assert isinstance(source_availability, Mapping)
        assert isinstance(source_counts, Mapping)
        assert isinstance(broad, Mapping)
        assert isinstance(diagnostic, Mapping)
        required_sources = {"METRICS", "LOGS", "TRACES"}
        if any(
            (
                set(source_availability) != required_sources,
                any(source_availability.get(name) != "AVAILABLE" for name in required_sources),
                set(source_counts) != required_sources,
                any(
                    not isinstance(source_counts.get(name), int)
                    or cast(int, source_counts.get(name)) <= 0
                    for name in required_sources
                ),
                value.get("invalid_refs") != 0,
                value.get("all_refs_resolve") is not True,
                not isinstance(broad.get("metrics"), int)
                or cast(int, broad.get("metrics")) <= 0,
                not isinstance(diagnostic.get("metrics"), int)
                or cast(int, diagnostic.get("metrics")) <= 0,
                not (
                    isinstance(diagnostic.get("logs"), int)
                    and cast(int, diagnostic.get("logs")) > 0
                    or isinstance(diagnostic.get("traces"), int)
                    and cast(int, diagnostic.get("traces")) > 0
                ),
                not isinstance(value.get("visible_service_count"), int)
                or not 3 <= cast(int, value.get("visible_service_count")) <= 8,
                value.get("fault_injections") != 1,
                value.get("provider_calls") != 2,
                value.get("model_calls") != 1,
                value.get("forward_mutations") != 1,
                value.get("rollback_mutations") != 0,
                value.get("fault_impact_gate") is not True,
                value.get("diagnosis_gate") is not True,
                value.get("diagnosis_correct") is not True,
                value.get("plan_action") != "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                value.get("policy_verdict") != "ALLOW",
                value.get("recovery_verification") is not True,
                cleanup.get("verdict") != "CLEAN",
                cleanup.get("owned_containers") != 0,
                cleanup.get("owned_networks") != 0,
                cleanup.get("owned_volumes") != 0,
                cleanup.get("non_owned_resources_changed") is not False,
            )
        ):
            raise ValueError("public Invocation B success aggregates do not recompute")


def _write_new_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_public_outputs_v5(
    config: E2EV5Config, terminal: Mapping[str, object]
) -> tuple[str, str, str]:
    public = _public_result_v5(config, terminal)
    verify_public_result(config, public)
    paths = (
        config.repository_root / config.reporting.public_result_json,
        config.repository_root / config.reporting.public_result_markdown,
        config.repository_root / config.reporting.public_human_brief,
    )
    _write_new_public(
        paths[0], json.dumps(public, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    _write_new_public(
        paths[1],
        (
            "# Live Fault to A0 Controlled Remediation E2E v5\n\n"
            f"**Verdict:** `{public['verdict']}`\n\n"
            "One preregistered local Sandbox scenario with a human-preauthorized "
            "frozen runbook; not production or autonomous remediation.\n"
        ).encode("utf-8"),
    )
    _write_new_public(
        paths[2],
        (
            "# Live Fault → A0 → Controlled Remediation v5 — Human Brief\n\n"
            "本结果仅代表一个本地 Sandbox、一个预注册场景和人工预授权的冻结修复 runbook；"
            "不构成生产自治或 Multi-Agent 优越性声明。\n"
        ).encode("utf-8"),
    )
    return cast(
        tuple[str, str, str],
        tuple(path.relative_to(config.repository_root).as_posix() for path in paths),
    )


def run_invocation_b(
    config: E2EV5Config,
    roots: E2EV5PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    kwargs.setdefault("environment_factory", SandboxEnvironment)
    kwargs.setdefault("public_writer", _write_public_outputs_v5)
    return e2e_v3.run_invocation_b(cast(Any, config), cast(Any, roots), **kwargs)


__all__ = [
    "V5NoFaultEvidence",
    "record_human_approval_for_invocation_b",
    "run_canonical_invocation_a",
    "run_development_probe",
    "run_invocation_b",
    "verify_public_result",
]
