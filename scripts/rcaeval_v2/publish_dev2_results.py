"""Publish case-free v2-dev.2 review artifacts after Provider execution stops."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

from ecomsre_rcaeval_v2.public_projection import (
    assert_public_payload,
    write_public_json_create_once,
    write_public_text_create_once,
)
from ecomsre_rcaeval_v2.dev2_evaluation_root import verify_provider_ready
from ecomsre_rcaeval_v2.dev2_evidence import (
    assess_design,
    evidence_source_bindings,
    materialize_combined_design_journal,
    public_admission_gate,
    verify_smoke_gate,
)
from ecomsre_rcaeval_v2.dev2_execution import (
    discover_case_index,
    load_locked_phase_schedule,
)
from ecomsre_rcaeval_v2.dev2_paths import preserved_evidence_roots
from scripts.rcaeval_v2.reverify_dev2_f0 import run_reverification


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLASSIFICATION = [
    "DEVELOPMENT_VISIBLE",
    "DESIGN_SET",
    "NOT_EXTERNAL_HOLDOUT",
    "NOT_PRIMARY_INFERENCE",
]


def _load(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("dev2 public source artifact is missing or invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("dev2 public source artifact must be an object")
    assert_public_payload(value)
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("dev2 public result mapping is invalid")
    return value


def _rate(value: object) -> str:
    row = _mapping(value)
    raw_value = row.get("value")
    if not isinstance(raw_value, (int, float)):
        raise ValueError("dev2 public rate value is invalid")
    return f"{row.get('numerator')}/{row.get('denominator')} ({float(raw_value):.4f})"


def _summary(
    state: str,
    f0: Mapping[str, object],
    admission: Mapping[str, object],
    smoke: Mapping[str, object],
    aggregate: Mapping[str, object],
    design_gate: Mapping[str, object],
) -> str:
    lines = [
        "# RCAEval RE2 v2-dev.2 DESIGN Summary",
        "",
        f"State: `{state}`",
        "",
        "Classification: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`",
        "",
        "PR #14 and PR #15 remain immutable failed-gate evidence.",
        "",
        "## Zero-Provider admission",
        "",
        f"- State: `{admission.get('state')}`",
        f"- Smoke admitted: {_mapping(admission.get('smoke')).get('admitted')}/72",
        f"- DESIGN admitted: {_mapping(admission.get('design')).get('admitted')}/360",
        f"- Reserved DEV_VALIDATION metadata admitted: {_mapping(admission.get('dev_validation_metadata')).get('admitted')}/480",
        "- Provider objects/calls/run attempts/operation attempts: 0/0/0/0",
        "",
        "## Inherited F0",
        "",
        f"- Overall Coverage@6: {_rate(f0['overall_coverage_at_6'])}",
        f"- Memory Coverage@6: {_rate(f0['memory_coverage_at_6'])}",
        f"- Socket Coverage@6: {_rate(f0['socket_coverage_at_6'])}",
        "- Formula re-selection: No",
        "- Provider calls: 0",
        "",
        "## Provider Smoke",
        "",
        f"- State: `{smoke.get('state')}`",
        f"- Terminalized: {_mapping(smoke.get('run_accounting')).get('terminalized')}/72",
        f"- Provider operations: {_mapping(smoke.get('provider_accounting')).get('provider_operations')}",
        f"- Known tokens: {_mapping(smoke.get('provider_accounting')).get('known_tokens')}",
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
                f"| {name} | {_rate(row['completed_runs'])} | {_rate(row['root_service_ac_at_1'])} | {_rate(row['root_cause_pair_ac_at_1'])} |"
            )
        lines.extend(
            [
                "",
                f"DESIGN Gate: `{design_gate.get('state')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Boundary",
            "",
            "DEV_VALIDATION values and case directories were not accessed, and validation was not executed. RE2-TT was not accessed. No external superiority claim is made.",
            "",
            "The next action is human Candidate Freeze Review only.",
            "",
        ]
    )
    return "\n".join(lines)


def publish(
    *,
    ob_root: Path,
    ss_root: Path,
    control_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    preserved_roots: Mapping[str, Path],
) -> str:
    _evaluation, admission_lock = verify_provider_ready(
        control_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=PROJECT_ROOT,
        preserved_roots=preserved_roots,
    )
    if not run_reverification(
        ob_root=ob_root,
        ss_root=ss_root,
        control_root=control_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        private_output=output_root / "evidence/f0-private.json",
        public_output=control_root / "evidence/f0-public.json",
        preserved_roots=preserved_roots,
    ):
        raise ValueError("dev2 canonical inherited F0 reverification drift")
    f0 = _load(control_root / "evidence/f0-public.json")
    if (
        f0.get("protocol_id") != "rcaeval-re2-v2-dev.2"
        or f0.get("state") != "INHERITED_F0_REVERIFIED"
        or _mapping(f0.get("overall_coverage_at_6")).get("numerator") != 57
        or _mapping(f0.get("memory_coverage_at_6")).get("numerator") != 10
        or _mapping(f0.get("socket_coverage_at_6")).get("numerator") != 9
    ):
        raise ValueError("dev2 canonical F0 evidence is invalid")
    admission_path = control_root / "evidence/schedule-admission-gate.json"
    admission = _load(admission_path)
    admission_lock_path = control_root / "locks/schedule-admission-lock.json"
    expected_admission = public_admission_gate(
        admission_lock,
        lock_sha256=hashlib.sha256(admission_lock_path.read_bytes()).hexdigest(),
    )
    if admission != expected_admission:
        raise ValueError("dev2 canonical Admission Gate differs from its lock")
    smoke_schedule = load_locked_phase_schedule(
        control_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        "smoke",
        preserved_roots=preserved_roots,
    )
    smoke = verify_smoke_gate(
        control_root / "evidence/provider-smoke-gate.json",
        control_root=control_root,
        output_root=output_root,
        smoke_journal_root=smoke_journal_root,
        design_journal_root=design_journal_root,
        project_root=PROJECT_ROOT,
        smoke_schedule=smoke_schedule,
        require_passing=False,
    )
    smoke_passed = smoke.get("state") == "V2_DEV2_PROVIDER_SMOKE_GATE_PASSED"
    if smoke_passed:
        design_schedule = load_locked_phase_schedule(
            control_root,
            output_root,
            smoke_journal_root,
            design_journal_root,
            "design",
            preserved_roots=preserved_roots,
        )
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
            output_root=output_root,
            smoke_journal_root=smoke_journal_root,
            design_journal_root=design_journal_root,
        )
        bindings["combined_design_journal_sha256"] = combined_sha
        cases = discover_case_index(
            ob_root, ss_root, {record.identity for record in design_schedule}
        )
        _outcomes, expected_aggregate, expected_design_gate, design_passed = assess_design(
            design_schedule,
            combined_root,
            cases=cases,
            source_bindings=bindings,
        )
        aggregate = _load(control_root / "evidence/design-aggregate.json")
        design_gate = _load(control_root / "evidence/design-gate.json")
        if aggregate != expected_aggregate or design_gate != expected_design_gate:
            raise ValueError("dev2 canonical DESIGN evidence failed recomputation")
        state = (
            "RCAEval_RE2_V2_DEV2_DESIGN_COMPLETE_READY_FOR_CANDIDATE_FREEZE_REVIEW"
            if design_passed
            else "V2_DEV2_DESIGN_GATE_NOT_PASSED"
        )
    else:
        state = "V2_DEV2_PROVIDER_SMOKE_GATE_NOT_PASSED"
        aggregate = {
            "schema_version": "rcaeval-re2-v2-dev2.aggregate-split.v1",
            "protocol_id": "rcaeval-re2-v2-dev.2",
            "classification": CLASSIFICATION,
            "architecture_summaries": {},
            "state": state,
        }
        design_gate = {
            "schema_version": "rcaeval-re2-v2-dev2.design-gate.v1",
            "protocol_id": "rcaeval-re2-v2-dev.2",
            "classification": CLASSIFICATION,
            "state": "NOT_RUN_DUE_TO_SMOKE_GATE",
        }
    disposition = {
        "schema_version": "rcaeval-re2-v2-dev2.current-disposition.v1",
        "protocol_id": "rcaeval-re2-v2-dev.2",
        "classification": CLASSIFICATION,
        "state": state,
        "admission_state": admission.get("state"),
        "smoke_state": smoke.get("state"),
        "design_state": design_gate.get("state"),
        "formula": "F0",
        "formula_reselection_performed": False,
        "dev_validation_case_directories_opened": False,
        "dev_validation_values_accessed": False,
        "dev_validation_executed": False,
        "re2_tt_accessed": False,
        "external_claim_made": False,
        "v2_dev_v1_evidence_changed": False,
        "v2_dev1_evidence_changed": False,
        "old_identifiers_reused": False,
        "private_case_records_committed": False,
        "raw_provider_outputs_committed": False,
    }
    human = "\n".join(
        [
            "# RCAEval RE2 v2-dev.2 人工审阅简报",
            "",
            f"当前状态：`{state}`",
            "",
            "本阶段仅修复六臂全局位置与 family-local 位置的兼容边界，增加 72/360/480 零 Provider Admission Rehearsal，并修复公开扫描 CI 的导入路径。PR #14 与 PR #15 的负向证据保持不变。",
            "",
            f"Admission：`{admission.get('state')}`；Provider Smoke：`{smoke.get('state')}`；DESIGN：`{design_gate.get('state')}`。公开材料只含聚合结果。",
            "",
            "数据边界：未打开 DEV_VALIDATION case directories，未读取 values，未执行 validation，未访问 RE2-TT，也没有形成外部优越性结论。",
            "",
            "建议：人工检查 DESIGN 指标信号与架构分类；如接受，再通过独立任务冻结 candidate 并授权一次性 120-case DEV_VALIDATION。",
            "",
        ]
    )
    results = PROJECT_ROOT / "docs" / "results"
    review = PROJECT_ROOT / "docs" / "review-evidence" / "rcaeval-re2-v2-dev2"
    write_public_json_create_once(results / "rcaeval-re2-v2-dev2-design-aggregate.json", aggregate)
    write_public_text_create_once(
        results / "rcaeval-re2-v2-dev2-design-summary.md",
        _summary(state, f0, admission, smoke, aggregate, design_gate),
    )
    write_public_json_create_once(review / "schedule-admission-gate.json", admission)
    write_public_json_create_once(review / "provider-smoke-gate.json", smoke)
    write_public_json_create_once(review / "design-gate.json", design_gate)
    write_public_json_create_once(review / "current-disposition.json", disposition)
    write_public_text_create_once(review / "human-brief.md", human)
    return state


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    parser.add_argument("--v2-dev-v1-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-control-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    print(
        publish(
            ob_root=args.ob_root,
            ss_root=args.ss_root,
            control_root=args.control_root,
            output_root=args.output_root,
            smoke_journal_root=args.smoke_journal_root,
            design_journal_root=args.design_journal_root,
            preserved_roots=preserved_evidence_roots(
                args.v2_dev_v1_root,
                args.v2_dev1_control_root,
                args.v2_dev1_output_root,
            ),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
