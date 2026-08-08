"""Publish the final case-free v2-dev.3 result and Agent redesign handoff."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Mapping

from ecomsre_rcaeval_v2.dev3_audit import Dev2FailureAuditLock
from ecomsre_rcaeval_v2.dev3_completion import (
    COMPLETION_AMENDMENT_LOCK_NAME,
    COMPLETION_GATE_NAME,
    load_completion_phase_schedules,
    verify_design_completion_amendment_ready,
)
from ecomsre_rcaeval_v2.dev3_evidence import (
    assess_design,
    evidence_source_bindings,
    materialize_combined_design_journal,
    public_admission_gate,
    verify_smoke_gate,
)
from ecomsre_rcaeval_v2.dev3_execution import discover_case_index
from ecomsre_rcaeval_v2.dev3_postrun import POSTRUN_LOCK_NAME
from ecomsre_rcaeval_v2.public_projection import (
    assert_public_payload,
    write_public_json_create_once,
    write_public_text_create_once,
)
from scripts.rcaeval_v2.dev3_cli import (
    add_preserved_root_arguments,
    preserved_roots_from_args,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_ID = "rcaeval-re2-v2-dev.3"
DESIGN_COMPLETE = "RCAEval_RE2_V2_DEV3_DESIGN_COMPLETE_READY_FOR_AGENT_REDESIGN"
FINAL_PROVIDER_LIMIT = (
    "RCAEval_RE2_V2_DEV3_FINAL_PROVIDER_LIMIT_RECORDED_READY_FOR_AGENT_REDESIGN"
)
DESIGN_NOT_RUN = "NOT_RUN_DUE_TO_FINAL_SMOKE_GATE"
CLASSIFICATION = [
    "DEVELOPMENT_VISIBLE",
    "DESIGN_SET",
    "NOT_EXTERNAL_HOLDOUT",
    "NOT_PRIMARY_INFERENCE",
]


def _load(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("dev3 public source artifact is missing or invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("dev3 public source artifact must be an object")
    assert_public_payload(value)
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("dev3 public result mapping is invalid")
    return value


def _integer(value: object, *, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"dev3 {name} is invalid")
    return value


def _rate(value: object) -> str:
    row = _mapping(value)
    raw_value = row.get("value")
    if not isinstance(raw_value, (int, float)):
        raise ValueError("dev3 public rate value is invalid")
    return (
        f"{row.get('numerator')}/{row.get('denominator')} "
        f"({float(raw_value):.4f})"
    )


def _public_failure_audit(lock: Dev2FailureAuditLock, *, lock_sha256: str) -> dict[str, object]:
    operation_types: Counter[str] = Counter()
    valid_responses = 0
    for group in lock.audit.groups:
        operation_types[group.operation_type] += group.count
        valid_responses += int(group.valid_response_received) * group.count
    payload: dict[str, object] = {
        "schema_version": "rcaeval-re2-v2-dev3.provider-failure-audit.v1",
        "protocol_id": PROTOCOL_ID,
        "classification": CLASSIFICATION,
        "failure_count": lock.audit.failure_count,
        "failure_classes": {
            key.value: value for key, value in lock.audit.failure_class_counts.items()
        },
        "retry_eligible": lock.audit.retry_eligible_count,
        "retry_ineligible": lock.audit.retry_ineligible_count,
        "valid_response_received": valid_responses,
        "failed_attempt_usage_disposition": {
            "KNOWN_POSITIVE": lock.audit.usage_known_count,
            "UNKNOWN_NO_VALID_RESPONSE": lock.audit.usage_unknown_count,
        },
        "operation_types": dict(sorted(operation_types.items())),
        "audit_lock_sha256": lock_sha256,
        "state": "DEV2_PROVIDER_FAILURE_AUDIT_LOCKED",
    }
    assert_public_payload(payload)
    return payload


def _summary(
    state: str,
    f0: Mapping[str, object],
    admission: Mapping[str, object],
    audit: Mapping[str, object],
    smoke: Mapping[str, object],
    aggregate: Mapping[str, object],
    design_gate: Mapping[str, object],
) -> str:
    run_accounting = _mapping(smoke.get("run_accounting"))
    provider = _mapping(smoke.get("provider_accounting"))
    lines = [
        "# RCAEval RE2 v2-dev.3 Final Infrastructure Result",
        "",
        f"State: `{state}`",
        "",
        "Classification: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`",
        "",
        "PR #14, PR #15, and PR #16 remain immutable failed-gate evidence.",
        "",
        "## Dev.2 Provider Failure Audit",
        "",
        f"- Failed runs: {audit.get('failure_count')}",
        f"- Failure classes: `{json.dumps(audit.get('failure_classes'), sort_keys=True)}`",
        f"- Retry eligible / ineligible: {audit.get('retry_eligible')} / {audit.get('retry_ineligible')}",
        "",
        "## Zero-Provider Admission and inherited F0",
        "",
        f"- Admission: `{admission.get('state')}`",
        f"- Smoke / DESIGN / validation metadata: {_mapping(admission.get('smoke')).get('admitted')} / {_mapping(admission.get('design')).get('admitted')} / {_mapping(admission.get('dev_validation_metadata')).get('admitted')}",
        "- Provider objects, calls, run attempts, operation attempts, and Provider attempts: 0",
        f"- F0 Overall / Memory / Socket: {_rate(f0['overall_coverage_at_6'])} / {_rate(f0['memory_coverage_at_6'])} / {_rate(f0['socket_coverage_at_6'])}",
        "",
        "## Provider Smoke",
        "",
        f"- Gate: `{smoke.get('state')}`",
        f"- Terminalized: {run_accounting.get('terminalized')}/72",
        f"- Semantic operations / Provider attempts: {provider.get('semantic_operations')} / {provider.get('provider_attempts')}",
        f"- Transport retries / recoveries / failures: {provider.get('transport_retries')} / {provider.get('retry_recoveries')} / {provider.get('retry_failures')}",
        f"- Known token lower bound / conservative upper bound: {provider.get('known_token_lower_bound')} / {provider.get('conservative_token_upper_bound')}",
        "",
    ]
    architecture = aggregate.get("architecture_summaries")
    if isinstance(architecture, Mapping) and architecture:
        lines.extend(
            [
                "## DESIGN architecture metrics",
                "",
                "| Variant | Completed | Root Service AC@1 | Root Cause Pair AC@1 |",
                "|---|---:|---:|---:|",
            ]
        )
        for name, raw in architecture.items():
            row = _mapping(raw)
            lines.append(
                f"| {name} | {_rate(row['completed_runs'])} | "
                f"{_rate(row['root_service_ac_at_1'])} | "
                f"{_rate(row['root_cause_pair_ac_at_1'])} |"
            )
        lines.extend(["", f"DESIGN Gate: `{design_gate.get('state')}`", ""])
    else:
        lines.extend(
            [
                "## DESIGN",
                "",
                f"DESIGN: `{DESIGN_NOT_RUN}`. The 72-run Smoke result is final for the infrastructure line.",
                "",
            ]
        )
    lines.extend(
        [
            "## Boundary and transition",
            "",
            "DEV_VALIDATION values and directories were not accessed or executed. RE2-TT was not accessed. No external claim is made.",
            "",
            "There is no dev.4. The next task is to implement the Single-first Adaptive RCA Agent described in the Agent Redesign Handoff.",
            "",
        ]
    )
    return "\n".join(lines)


def _agent_redesign_handoff(state: str) -> str:
    return "\n".join(
        [
            "# RCAEval Agent Redesign Handoff",
            "",
            f"Infrastructure terminal state: `{state}`",
            "",
            "This document is the only recommended entry point for the next phase. The infrastructure line ends at v2-dev.3; do not create another Harness-only development version.",
            "",
            "## Required architecture",
            "",
            "Strong Single baseline → deterministic uncertainty/conflict gate → zero escalation for easy cases → selective Specialist escalation for hard cases → contradiction-aware fusion → deterministic Indicator Resolver.",
            "",
            "The design is Single-first and permits zero follow-up calls. It must not invoke every evidence source by default. Specialists return ranked hypotheses with supporting and contradicting evidence and label each hypothesis as root, symptom, or uncertain causal role. The final Judge remains architecture-blind. The Indicator Resolver and v2-dev.3 transport policy remain intact.",
            "",
            "## Required acceptance metrics",
            "",
            "- Damage Rate",
            "- Rescue Rate",
            "- Escalation Precision",
            "- Escalation Recall",
            "- Zero-escalation Rate",
            "- Root Service AC@1",
            "- Root Cause Pair AC@1",
            "- Terminal Failure Rate",
            "- Tool Calls",
            "- Semantic Operations",
            "- Provider Attempts",
            "- Tokens",
            "- Latency",
            "",
            "## Scope of the next task",
            "",
            "Implement Single-first Adaptive RCA Agent on OB/SS DESIGN data.",
            "",
            "This handoff does not implement that Agent. DEV_VALIDATION remains unauthorized and RE2-TT remains forbidden.",
            "",
        ]
    )


def _final_infrastructure_limit(
    state: str, smoke: Mapping[str, object]
) -> str:
    provider = _mapping(smoke.get("provider_accounting"))
    return "\n".join(
        [
            "# RCAEval RE2 v2-dev.3 Final Infrastructure Limit Report",
            "",
            f"State: `{state}`",
            "",
            f"The final 72-run Provider Smoke terminalized but did not pass: `{smoke.get('state')}`. DESIGN was not run.",
            "",
            f"Provider semantic operations: {provider.get('semantic_operations')}; attempts: {provider.get('provider_attempts')}; transport retries: {provider.get('transport_retries')}; recoveries: {provider.get('retry_recoveries')}; retry failures: {provider.get('retry_failures')}.",
            "",
            f"Known token lower bound: {provider.get('known_token_lower_bound')}; unknown attempts: {provider.get('unknown_attempt_count')}; conservative upper bound: {provider.get('conservative_token_upper_bound')}.",
            "",
            "All evidence is retained outside Git, and the public result is aggregate-only. No dev.4 will be created. The next task is the real Single-first Adaptive RCA Agent.",
            "",
        ]
    )


def _require_integrity_safe_smoke_failure(
    smoke: Mapping[str, object],
) -> None:
    checks = _mapping(smoke.get("gate_checks"))
    provider_failure_checks = {
        "v2_run_completion",
        "completed_attempt_usage_coverage",
        "final_judge_schema",
        "final_judge_schema_dev3",
        "token_accounting_v2",
    }
    failed_integrity = [
        name
        for name, value in checks.items()
        if name not in provider_failure_checks
        and not bool(_mapping(value).get("passed"))
    ]
    token = _mapping(checks.get("token_accounting_v2"))
    if (
        _integer(token.get("orphan_attempts"), name="orphan attempt count") != 0
        or _integer(token.get("conservative_token_upper_bound"), name="token upper bound")
        > _integer(token.get("maximum"), name="token budget")
    ):
        failed_integrity.append("token_accounting_v2")
    if failed_integrity:
        raise ValueError(
            "dev3 Smoke failure includes integrity-gate failures: "
            + ",".join(sorted(set(failed_integrity)))
        )


def _require_design_not_started(
    *,
    control_root: Path,
    output_root: Path,
    design_journal_root: Path,
) -> None:
    allowed = {".evaluation-root-authority.json"}
    if design_journal_root.is_symlink() or not design_journal_root.is_dir():
        raise ValueError("dev3 DESIGN journal root is missing or invalid")
    if {path.name for path in design_journal_root.iterdir()} - allowed:
        raise ValueError("dev3 failed-Smoke publication found DESIGN journal evidence")
    forbidden = (
        control_root / "evidence/design-aggregate.json",
        control_root / "evidence/design-gate.json",
        output_root / "evidence/design-outcomes.json",
        output_root / "evidence/combined-design-journal",
    )
    if any(path.exists() for path in forbidden):
        raise ValueError("dev3 failed-Smoke publication found DESIGN output evidence")


def publish(
    *,
    ob_root: Path,
    ss_root: Path,
    control_root: Path,
    private_schedule_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    preserved_roots: Mapping[str, Path],
) -> str:
    _amendment, _postrun, parent, admission_lock = (
        verify_design_completion_amendment_ready(
            control_root,
            private_schedule_root,
            output_root,
            smoke_journal_root,
            design_journal_root,
            project_root=PROJECT_ROOT,
            preserved_roots=preserved_roots,
        )
    )
    f0 = _load(control_root / "evidence/f0-public.json")
    if (
        f0.get("protocol_id") != PROTOCOL_ID
        or f0.get("state") != "INHERITED_F0_REVERIFIED"
        or _mapping(f0.get("overall_coverage_at_6")).get("numerator") != 57
        or _mapping(f0.get("memory_coverage_at_6")).get("numerator") != 10
        or _mapping(f0.get("socket_coverage_at_6")).get("numerator") != 9
    ):
        raise ValueError("dev3 canonical F0 evidence is invalid")

    admission_path = control_root / "evidence/schedule-admission-gate.json"
    admission = _load(admission_path)
    admission_lock_path = control_root / "locks/schedule-admission-lock.json"
    expected_admission = public_admission_gate(
        admission_lock,
        lock_sha256=hashlib.sha256(admission_lock_path.read_bytes()).hexdigest(),
    )
    if admission != expected_admission:
        raise ValueError("dev3 canonical Admission Gate differs from its lock")

    audit_lock_path = control_root / "locks/dev2-provider-failure-audit.json"
    audit_lock = Dev2FailureAuditLock.model_validate_json(
        audit_lock_path.read_text(encoding="utf-8")
    )
    audit = _public_failure_audit(
        audit_lock,
        lock_sha256=hashlib.sha256(audit_lock_path.read_bytes()).hexdigest(),
    )

    smoke_schedule, design_schedule = load_completion_phase_schedules(
        private_schedule_root,
        parent=parent,
        admission=admission_lock,
    )
    smoke = verify_smoke_gate(
        control_root / "evidence/provider-smoke-gate.json",
        control_root=control_root,
        private_schedule_root=private_schedule_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        project_root=PROJECT_ROOT,
        smoke_schedule=smoke_schedule,
        require_passing=False,
    )
    run_accounting = _mapping(smoke.get("run_accounting"))
    if (
        _integer(run_accounting.get("planned"), name="Smoke planned count") != 72
        or _integer(run_accounting.get("terminalized"), name="Smoke terminal count")
        != 72
    ):
        raise ValueError("dev3 final publication requires 72/72 Smoke terminals")

    smoke_passed = smoke.get("state") == "V2_DEV3_PROVIDER_SMOKE_GATE_PASSED"
    if smoke_passed:
        combined_root = output_root / "evidence/combined-design-journal"
        combined_sha = materialize_combined_design_journal(
            smoke_journal_root=smoke_journal_root,
            design_journal_root=design_journal_root,
            combined_root=combined_root,
            smoke_schedule=smoke_schedule,
            design_schedule=design_schedule,
        )
        bindings = evidence_source_bindings(
            project_root=PROJECT_ROOT,
            control_root=control_root,
            private_schedule_root=private_schedule_root,
            output_root=output_root,
            smoke_journal_root=smoke_journal_root,
            design_journal_root=design_journal_root,
        )
        bindings["combined_design_journal_sha256"] = combined_sha
        bindings["postrun_evaluation_lock_sha256"] = hashlib.sha256(
            (control_root / "locks" / POSTRUN_LOCK_NAME).read_bytes()
        ).hexdigest()
        cases = discover_case_index(
            ob_root, ss_root, {record.identity for record in design_schedule}
        )
        _outcomes, expected_aggregate, expected_design_gate, design_passed = (
            assess_design(
                design_schedule,
                combined_root,
                cases=cases,
                source_bindings=bindings,
            )
        )
        expected_bindings = expected_design_gate.get("source_bindings")
        if not isinstance(expected_bindings, dict):
            raise ValueError("dev3 DESIGN completion gate binding is invalid")
        expected_bindings["design_completion_amendment_lock_sha256"] = (
            hashlib.sha256(
                (
                    control_root
                    / "locks"
                    / COMPLETION_AMENDMENT_LOCK_NAME
                ).read_bytes()
            ).hexdigest()
        )
        aggregate = _load(control_root / "evidence/design-aggregate.json")
        design_gate = _load(control_root / "evidence" / COMPLETION_GATE_NAME)
        if aggregate != expected_aggregate or design_gate != expected_design_gate:
            raise ValueError("dev3 canonical DESIGN evidence failed recomputation")
        if not design_passed:
            raise ValueError("dev3 DESIGN integrity gate did not pass")
        state = DESIGN_COMPLETE
    else:
        _require_integrity_safe_smoke_failure(smoke)
        _require_design_not_started(
            control_root=control_root,
            output_root=output_root,
            design_journal_root=design_journal_root,
        )
        state = FINAL_PROVIDER_LIMIT
        aggregate = {
            "schema_version": "rcaeval-re2-v2-dev3.aggregate-split.v1",
            "protocol_id": PROTOCOL_ID,
            "classification": CLASSIFICATION,
            "architecture_summaries": {},
            "state": DESIGN_NOT_RUN,
        }
        design_gate = {
            "schema_version": "rcaeval-re2-v2-dev3.design-gate.v1",
            "protocol_id": PROTOCOL_ID,
            "classification": CLASSIFICATION,
            "state": DESIGN_NOT_RUN,
        }

    disposition = {
        "schema_version": "rcaeval-re2-v2-dev3.current-disposition.v1",
        "protocol_id": PROTOCOL_ID,
        "classification": CLASSIFICATION,
        "state": state,
        "admission_state": admission.get("state"),
        "smoke_state": smoke.get("state"),
        "design_state": design_gate.get("state"),
        "formula": "F0",
        "formula_reselection_performed": False,
        "dev_validation_directories_opened": False,
        "dev_validation_values_accessed": False,
        "dev_validation_executed": False,
        "re2_tt_accessed": False,
        "external_claim_made": False,
        "pr14_evidence_changed": False,
        "pr15_evidence_changed": False,
        "pr16_evidence_changed": False,
        "v1_frozen_scope_changed": False,
        "old_identifiers_reused": False,
        "private_records_committed": False,
        "provider_payloads_committed": False,
        "dev4_created": False,
    }
    human = "\n".join(
        [
            "# RCAEval RE2 v2-dev.3 人工审阅简报",
            "",
            f"最终状态：`{state}`",
            "",
            "dev.3 是最后一个基础设施测试版本。它冻结了 dev.2 失败归因、严格 transport-only retry 和 Token Accounting v2，并完成 72/360/480 零 Provider Admission。",
            "",
            f"Provider Smoke：`{smoke.get('state')}`；DESIGN：`{design_gate.get('state')}`。所有公开材料仅含安全聚合。",
            "",
            "数据边界：未打开 DEV_VALIDATION 目录，未读取其值，未执行 validation，未访问 RE2-TT，也没有外部结论。PR #14、#15、#16 的负向证据保持不变。",
            "",
            "建议：停止 Harness-only 迭代，按 Agent Redesign Handoff 实现 Single-first Adaptive RCA Agent。",
            "",
        ]
    )

    results = PROJECT_ROOT / "docs/results"
    review = PROJECT_ROOT / "docs/review-evidence/rcaeval-re2-v2-dev3"
    write_public_json_create_once(
        results / "rcaeval-re2-v2-dev3-smoke-aggregate.json", smoke
    )
    write_public_json_create_once(
        results / "rcaeval-re2-v2-dev3-design-aggregate.json", aggregate
    )
    write_public_text_create_once(
        results / "rcaeval-re2-v2-dev3-summary.md",
        _summary(state, f0, admission, audit, smoke, aggregate, design_gate),
    )
    if not smoke_passed:
        write_public_text_create_once(
            results / "rcaeval-re2-v2-dev3-final-infrastructure-limit.md",
            _final_infrastructure_limit(state, smoke),
        )
    write_public_json_create_once(review / "current-disposition.json", disposition)
    write_public_json_create_once(review / "provider-failure-audit.json", audit)
    write_public_json_create_once(review / "schedule-admission-gate.json", admission)
    write_public_json_create_once(review / "provider-smoke-gate.json", smoke)
    write_public_json_create_once(review / "design-gate.json", design_gate)
    write_public_text_create_once(review / "human-brief.md", human)
    write_public_text_create_once(
        PROJECT_ROOT / "docs/design/rcaeval-agent-redesign-handoff.md",
        _agent_redesign_handoff(state),
    )
    return state


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    add_preserved_root_arguments(parser)
    args = parser.parse_args(argv)
    print(
        publish(
            ob_root=args.ob_root,
            ss_root=args.ss_root,
            control_root=args.control_root,
            private_schedule_root=args.private_schedule_root,
            output_root=args.output_root,
            smoke_journal_root=args.smoke_journal_root,
            design_journal_root=args.design_journal_root,
            preserved_roots=preserved_roots_from_args(args),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
