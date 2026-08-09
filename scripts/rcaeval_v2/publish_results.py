"""Publish case-free v2-dev.1 review artifacts after Provider execution stops."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from ecomsre_rcaeval_v2.public_projection import (
    assert_public_payload,
    write_public_json_create_once,
    write_public_text_create_once,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("public source artifact is missing or invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("public source artifact must be an object")
    assert_public_payload(value)
    return value


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"public result {label} is invalid")
    return value


def _number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"public result {label} is invalid")
    return float(value)


def _rate_text(value: object) -> str:
    row = _mapping(value, "rate")
    return (
        f"{row['numerator']}/{row['denominator']} "
        f"({_number(row['value'], 'rate value'):.4f})"
    )


def _summary(
    state: str,
    f0: Mapping[str, object],
    smoke: Mapping[str, object],
    aggregate: Mapping[str, object],
    design_gate: Mapping[str, object],
) -> str:
    lines = [
        "# RCAEval RE2 v2-dev.1 DESIGN Summary",
        "",
        f"State: `{state}`",
        "",
        "Classification: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`",
        "",
        "PR #14 remains preserved as the v2-dev-v1 negative-gate record.",
        "",
        "## Inherited F0",
        "",
        f"- Overall Coverage@6: {_rate_text(f0['overall_coverage_at_6'])}",
        f"- Memory Coverage@6: {_rate_text(f0['memory_coverage_at_6'])}",
        f"- Socket Coverage@6: {_rate_text(f0['socket_coverage_at_6'])}",
        "- Formula re-selection: No",
        "- Provider calls: 0",
        "",
        "## Provider Smoke",
        "",
    ]
    smoke_runs = _mapping(smoke.get("run_accounting"), "Smoke accounting")
    smoke_provider = _mapping(
        smoke.get("provider_accounting"), "Smoke Provider accounting"
    )
    lines.extend(
        [
            f"- Terminalized: {smoke_runs.get('terminalized')}/{smoke_runs.get('planned')}",
            f"- Provider operations: {smoke_provider.get('provider_operations')}",
            f"- Known tokens: {smoke_provider.get('known_tokens')}",
            f"- Gate: `{smoke.get('state')}`",
            "",
        ]
    )
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
        for name, raw_row in architecture.items():
            row = _mapping(raw_row, "architecture")
            lines.append(
                f"| {name} | {_rate_text(row['completed_runs'])} | "
                f"{_rate_text(row['root_service_ac_at_1'])} | "
                f"{_rate_text(row['root_cause_pair_ac_at_1'])} |"
            )
        signals = _mapping(design_gate.get("design_signals"), "design signals")
        indicator = _mapping(signals.get("indicator"), "indicator signal")
        architecture_signal = _mapping(
            signals.get("architecture"), "architecture signal"
        )
        lines.extend(
            [
                "",
                "## Review signals",
                "",
                f"- Single v2 pair delta: {_number(indicator['single_v2_pair_improvement'], 'pair delta'):+.4f}",
                f"- Root Service preservation: {_number(indicator['root_service_preservation'], 'service delta'):+.4f}",
                f"- Memory correct pairs: {indicator['memory_pair_correct']}",
                f"- Socket correct pairs: {indicator['socket_pair_correct']}",
                f"- Architecture: `{architecture_signal['classification']}`",
                f"- DESIGN Gate: `{design_gate.get('state')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Boundary",
            "",
            "DEV_VALIDATION values were not accessed and no validation run was executed.",
            "RE2-TT was not accessed. No external superiority claim is made.",
            "The next action is human candidate-freeze review only.",
            "",
        ]
    )
    return "\n".join(lines)


def _data_card(f0: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "# RCAEval RE2 v2-dev.1 Development Data Card",
            "",
            "Status: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`",
            "",
            "The version reuses the frozen RE2-OB and RE2-SS development dataset binding and the unchanged 60-case DESIGN split. The 120 reserved DEV_VALIDATION rows were not opened by this task.",
            "",
            "## Locked sources",
            "",
            "- RE2-OB: 90 development cases; metrics, logs, and traces.",
            "- RE2-SS: 90 development cases; metrics and logs; traces forbidden.",
            "- DESIGN: 60 cases, 30 per system.",
            "- DEV_VALIDATION: 120 cases reserved and not accessed.",
            "- RE2-TT: forbidden and not accessed.",
            "",
            "## Inherited F0 reverification",
            "",
            f"- Overall Coverage@6: {_rate_text(f0['overall_coverage_at_6'])}",
            f"- Memory Coverage@6: {_rate_text(f0['memory_coverage_at_6'])}",
            f"- Socket Coverage@6: {_rate_text(f0['socket_coverage_at_6'])}",
            "- Formula re-selection: No.",
            "- Provider calls: 0.",
            "",
            "Only schedule-selected DESIGN telemetry was opened for F0 and runtime evaluation. Public outputs are aggregate-only and contain no case-level records.",
            "",
        ]
    )


def _human_brief(state: str, smoke: Mapping[str, object], design: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "# RCAEval RE2 v2-dev.1 人工审阅简报",
            "",
            f"当前状态：`{state}`",
            "",
            "本阶段新建了独立协议、schedule、外部锁与私有输出根；PR #14 的旧失败证据没有改写。四项修复覆盖路径脱敏、operation stage、Provider 前置外部锁以及 Final Judge 严格本地校验与安全诊断。",
            "",
            f"Provider Smoke：`{smoke.get('state')}`。DESIGN：`{design.get('state')}`。所有公开材料仅含聚合结果。",
            "",
            "数据边界：未访问 DEV_VALIDATION values，未执行 validation，未访问 RE2-TT，未形成外部优越性结论。",
            "",
            "建议：人工检查 DESIGN 指标信号与 architecture 分类；若接受，再通过独立任务冻结 candidate 并授权一次性 DEV_VALIDATION。",
            "",
        ]
    )


def publish(
    *,
    f0_path: Path,
    smoke_path: Path,
    aggregate_path: Path | None,
    design_gate_path: Path | None,
) -> str:
    f0 = _load(f0_path)
    smoke = _load(smoke_path)
    smoke_passed = smoke.get("state") == "V2_DEV1_PROVIDER_SMOKE_GATE_PASSED"
    if smoke_passed:
        if aggregate_path is None or design_gate_path is None:
            raise ValueError("passing Smoke requires DESIGN public evidence")
        aggregate = _load(aggregate_path)
        design_gate = _load(design_gate_path)
        design_passed = design_gate.get("state") == "V2_DEV1_DESIGN_GATE_PASSED"
        state = (
            "RCAEval_RE2_V2_DEV1_DESIGN_COMPLETE_READY_FOR_CANDIDATE_FREEZE_REVIEW"
            if design_passed
            else "V2_DEV1_DESIGN_GATE_NOT_PASSED"
        )
    else:
        state = "V2_DEV1_PROVIDER_SMOKE_GATE_NOT_PASSED"
        aggregate = {
            "schema_version": "rcaeval-re2-v2-dev1.aggregate-split.v1",
            "protocol_id": "rcaeval-re2-v2-dev.1",
            "classification": [
                "DEVELOPMENT_VISIBLE",
                "DESIGN_SET",
                "NOT_EXTERNAL_HOLDOUT",
                "NOT_PRIMARY_INFERENCE",
            ],
            "architecture_summaries": {},
            "state": state,
        }
        design_gate = {
            "schema_version": "rcaeval-re2-v2-dev1.design-gate.v1",
            "protocol_id": "rcaeval-re2-v2-dev.1",
            "classification": aggregate["classification"],
            "state": "NOT_RUN_DUE_TO_SMOKE_GATE",
        }
    disposition = {
        "schema_version": "rcaeval-re2-v2-dev1.current-disposition.v1",
        "protocol_id": "rcaeval-re2-v2-dev.1",
        "classification": aggregate["classification"],
        "state": state,
        "smoke_state": smoke.get("state"),
        "design_state": design_gate.get("state"),
        "formula": "F0",
        "formula_reselection_performed": False,
        "dev_validation_values_accessed": False,
        "dev_validation_executed": False,
        "re2_tt_accessed": False,
        "external_claim_made": False,
        "v2_dev_v1_evidence_changed": False,
        "old_identifiers_reused": False,
        "private_case_records_committed": False,
        "raw_provider_outputs_committed": False,
    }
    results = PROJECT_ROOT / "docs" / "results"
    review = PROJECT_ROOT / "docs" / "review-evidence" / "rcaeval-re2-v2-dev1"
    external = PROJECT_ROOT / "docs" / "external-benchmarks"
    write_public_json_create_once(
        results / "rcaeval-re2-v2-dev1-design-aggregate.json", aggregate
    )
    write_public_text_create_once(
        results / "rcaeval-re2-v2-dev1-design-summary.md",
        _summary(state, f0, smoke, aggregate, design_gate),
    )
    write_public_json_create_once(review / "provider-smoke-gate.json", smoke)
    write_public_json_create_once(review / "design-gate.json", design_gate)
    write_public_json_create_once(review / "current-disposition.json", disposition)
    write_public_text_create_once(
        review / "human-brief.md", _human_brief(state, smoke, design_gate)
    )
    write_public_text_create_once(
        external / "rcaeval-re2-v2-dev1-data-card.md", _data_card(f0)
    )
    return state


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--f0", required=True, type=Path)
    parser.add_argument("--smoke", required=True, type=Path)
    parser.add_argument("--aggregate", type=Path)
    parser.add_argument("--design-gate", type=Path)
    args = parser.parse_args(argv)
    print(
        publish(
            f0_path=args.f0,
            smoke_path=args.smoke,
            aggregate_path=args.aggregate,
            design_gate_path=args.design_gate,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
