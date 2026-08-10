"""Build the aggregate-only RCA100 report after evaluator-only repair scoring."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from ecomsre.evidence.hashes import sha256_file
from ecomsre_rca100.lifecycle import create_once_json, load_strict_json
from ecomsre_rca100.public_projection import scan_public_artifacts
from scripts.rca100.build_report import _execution_summary
from scripts.rca100.repair.lifecycle import (
    DECISION_RECORD_SHA256,
    ORIGINAL_ATTEMPT_TREE_SHA256,
    ORIGINAL_NO_LABEL_AUDIT_SHA256,
    ORIGINAL_PR22_HEAD,
    ORIGINAL_PROTOCOL_FREEZE_SHA256,
    ORIGINAL_PROVIDER_TREE_SHA256,
    ORIGINAL_TERMINAL_LOCK_SHA256,
    ORIGINAL_TERMINAL_TREE_SHA256,
    REPAIR_PROTOCOL_ID,
    RepairEnvironment,
    current_repair_state,
    load_repair_evaluation_inputs,
)


FINAL_STATUS = (
    "RCA100_EVALUATOR_REPAIR_FINAL_REPORT_FROZEN_READY_FOR_PUBLICATION_REVIEW"
)
METHOD_STATUS = "POST_LOCK_EVALUATOR_REPAIR_DISCLOSED"
DISCLOSURE = (
    "Predictions were generated and locked in a one-shot, answer-blind RCA100 "
    "execution. After terminal lock, the frozen evaluator was found to misread "
    "the official mapping.json envelope. A separately authorized evaluator-only "
    "repair unwrapped the frozen task_to_case_id field. No Provider call, "
    "prediction rerun, M3 change, or case replacement was performed. Apart from "
    "the envelope extraction, the scorer, entity matching, statistics, and fixed "
    "denominator were unchanged."
)


def _mapping(path: Path, *, label: str) -> Mapping[str, object]:
    value = load_strict_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    return value


def _write_once_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def public_report(
    *,
    aggregate: Mapping[str, object],
    execution: Mapping[str, object],
    audit: Mapping[str, object],
    terminal_lock: Mapping[str, object],
    answer_lock: Mapping[str, object],
    implementation_lock: Mapping[str, object],
    scoring_lock: Mapping[str, object],
) -> dict[str, object]:
    root = aggregate["root"]
    assert isinstance(root, Mapping)
    return {
        "schema_version": "rca100.metrics-arbitration-evaluator-repair-final.v1",
        "status": FINAL_STATUS,
        "evaluation_method_status": METHOD_STATUS,
        "classification": root["classification"],
        "source": {
            "repository": (
                "https://www.aiops.cn/gitlab/aiops-live-benchmark/"
                "agenticopseval.git"
            ),
            "commit": "fd92cae17e6e14fa3ed0f3963c31838151fbdaa7",
            "license": "CC BY-NC-SA 4.0",
            "fixed_denominator": 103,
            "agent_facing_files": 721,
        },
        "original_protocol": {
            "pr": 22,
            "disposition": "BLOCKED_PROTOCOL_DRIFT",
            "implementation_commit": ORIGINAL_PR22_HEAD,
            "protocol_freeze_sha256": ORIGINAL_PROTOCOL_FREEZE_SHA256,
            "terminal_tree_sha256": ORIGINAL_TERMINAL_TREE_SHA256,
            "attempt_tree_sha256": ORIGINAL_ATTEMPT_TREE_SHA256,
            "provider_sidecar_tree_sha256": ORIGINAL_PROVIDER_TREE_SHA256,
            "no_label_schema_audit_sha256": ORIGINAL_NO_LABEL_AUDIT_SHA256,
        },
        "repair_protocol": {
            "protocol_id": REPAIR_PROTOCOL_ID,
            "classification": "POST_LOCK_EVALUATOR_ONLY_REPAIR",
            "decision_code": "POST_LOCK_MAPPING_ENVELOPE_REPAIR",
            "decision_record_sha256": DECISION_RECORD_SHA256,
            "implementation_commit": implementation_lock[
                "repair_implementation_commit"
            ],
            "answer_key_tree_sha256": answer_lock[
                "answer_key_source_tree_sha256"
            ],
            "case_score_vector_sha256": scoring_lock[
                "case_score_vector_sha256"
            ],
            "provider_calls": 0,
        },
        "post_lock_evaluator_repair_disclosure": {
            "heading": "Post-lock Evaluator Repair Disclosure",
            "statement": DISCLOSURE,
            "predictions_locked_before_answer_acquisition": True,
            "original_protocol_blocker_retained": True,
            "frozen_envelope_field_extracted": "task_to_case_id",
            "provider_calls_after_original_terminal_lock": 0,
            "prediction_reruns": 0,
            "m3_changes": 0,
            "case_replacements": 0,
            "entity_alias_changes": 0,
            "scoring_rule_changes": 0,
        },
        "adapter_audit": {
            "tasks_parsed": audit["tasks_parsed"],
            "metrics_parsed": audit["metrics_parsed"],
            "logs_parsed": audit["logs_parsed"],
            "traces_parsed": audit["traces_parsed"],
            "topology_parsed": audit["topology_parsed"],
            "metrics_ranking_available": audit["metrics_ranking_available"],
            "metrics_ranking_unavailable": audit[
                "metrics_ranking_unavailable"
            ],
        },
        "execution": dict(execution),
        "primary": aggregate["root"],
        "primary_inference_eligible": aggregate["primary_inference_eligible"],
        "secondary_pair": aggregate["pair"],
        "m3": aggregate["m3"],
        "completion": aggregate["completion"],
        "descriptive_subgroups": aggregate["descriptive_subgroups"],
        "official_style": aggregate["official_style"],
        "claim_boundary": {
            "allowed": (
                "External RCA100 predictions were generated answer-blind and "
                "scored after a separately authorized evaluator-envelope repair."
            ),
            "forbidden": "The preregistered evaluator executed unchanged.",
            "superiority_rule": "POINT_GT_0_AND_CI_LOWER_GT_0",
            "bootstrap_replicates": 10000,
            "bootstrap_seed": 20260810,
            "fixed_denominator": 103,
            "rerun": False,
            "release_or_deployment_claim": False,
        },
        "terminal_lock_binding": {
            "terminal_records": terminal_lock["terminal_records"],
            "run_attempts": terminal_lock["run_attempts"],
            "duplicate_run_ids": terminal_lock["duplicate_run_ids"],
        },
    }


def markdown(report: Mapping[str, object]) -> str:
    primary = report["primary"]
    pair = report["secondary_pair"]
    m3 = report["m3"]
    execution = report["execution"]
    assert isinstance(primary, Mapping)
    assert isinstance(pair, Mapping)
    assert isinstance(m3, Mapping)
    assert isinstance(execution, Mapping)
    return f"""# RCA100 Metrics Arbitration v1 Final Result

Status: `{report['status']}`

Evaluation method: `{report['evaluation_method_status']}`

Classification: `{report['classification']}`

## Post-lock Evaluator Repair Disclosure

{DISCLOSURE}

PR #22 remains permanently `BLOCKED_PROTOCOL_DRIFT`. This repaired result does
not claim that the preregistered evaluator executed unchanged.

## Primary paired result

- Initial Root Entity correct: {primary['initial_correct']} / 103
- Final Root Entity correct: {primary['final_correct']} / 103
- Point difference: {float(primary['point_difference']):.6f}
- 95% paired bootstrap CI: [{float(primary['ci_lower']):.6f}, {float(primary['ci_upper']):.6f}]
- Exact McNemar p-value: {float(primary['mcnemar_exact_p_value']):.6g}
- Root Damage / Rescue / Net: {primary['damage']} / {primary['rescue']} / {primary['net_rescue']}
- Root Damage Rate: {float(primary['damage_rate']):.6f} ({primary['damage']} / {primary['damage_rate_denominator']})
- KEEP / OVERRIDE: {m3['keep']} / {m3['override']}
- Correct / Wrong Override: {m3['correct_override']} / {m3['wrong_override']}

## Secondary and execution

- Initial Pair correct: {pair['initial_correct']} / 103
- Final Pair correct: {pair['final_correct']} / 103
- Pair Damage / Rescue / Net: {pair['damage']} / {pair['rescue']} / {pair['net_rescue']}
- Completed terminals: {execution['completed']} / 103
- Provider attempts / original transport retries: {execution['provider_attempts']} / {execution['transport_retries']}
- Provider calls added by repair: 0
- Prediction reruns / case replacements: 0 / 0
- Official composite: `OFFICIAL_COMPOSITE_NOT_AVAILABLE`

The canonical JSON contains all frozen aggregate-only descriptive subgroups,
each with its denominator. No case-level prediction, answer, mapping, evidence,
reasoning, private path, credential, or Provider endpoint is public.
"""


def human_brief(report: Mapping[str, object]) -> str:
    primary = report["primary"]
    m3 = report["m3"]
    assert isinstance(primary, Mapping)
    assert isinstance(m3, Mapping)
    return f"""# RCA100 外部评估 Human Brief

状态：`{report['status']}`

方法状态：`{report['evaluation_method_status']}`

结果分类：`{report['classification']}`

## Post-lock Evaluator Repair Disclosure

103 个预测在 answer material 获取前已经一次性生成并锁定。原 frozen
evaluator 错误理解官方 mapping envelope，原协议永久保留为
`BLOCKED_PROTOCOL_DRIFT`。本次单独授权的 evaluator-only repair 只提取冻结的
`task_to_case_id`；没有 Provider 调用、预测重跑、M3 修改或 case replacement。
除 envelope extraction 外，scorer、entity matching、statistics 和固定 denominator
均未修改。

- Initial / Final Root 正确：{primary['initial_correct']} / {primary['final_correct']}（固定分母 103）
- Root Damage / Rescue / Net：{primary['damage']} / {primary['rescue']} / {primary['net_rescue']}
- Root Damage Rate：{float(primary['damage_rate']):.6f}
- 95% 配对区间：[{float(primary['ci_lower']):.6f}, {float(primary['ci_upper']):.6f}]
- McNemar exact p：{float(primary['mcnemar_exact_p_value']):.6g}
- KEEP / OVERRIDE：{m3['keep']} / {m3['override']}

允许的声明边界：External RCA100 predictions were generated answer-blind and
scored after a separately authorized evaluator-envelope repair. 不得声称原
preregistered evaluator 未经修改地端到端执行。
"""


def disposition(report: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": "rca100.evaluator-repair-public-disposition.v1",
        "status": report["status"],
        "evaluation_method_status": report["evaluation_method_status"],
        "classification": report["classification"],
        "original_pr22_disposition": "BLOCKED_PROTOCOL_DRIFT",
        "draft_pr": "REVIEW_REQUIRED",
        "merge": "FORBIDDEN_BY_GOAL",
        "release_tag": "FORBIDDEN_BY_GOAL",
        "rerun": "FORBIDDEN",
    }


def execution_integrity(
    *,
    terminal_lock: Mapping[str, object],
    answer_lock: Mapping[str, object],
    implementation_lock: Mapping[str, object],
    scoring_lock: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "rca100.evaluator-repair-execution-integrity.v1",
        "original_pr22_head": ORIGINAL_PR22_HEAD,
        "original_protocol_freeze_sha256": ORIGINAL_PROTOCOL_FREEZE_SHA256,
        "original_terminal_tree_sha256": terminal_lock["terminal_tree_sha256"],
        "original_attempt_tree_sha256": terminal_lock["attempt_tree_sha256"],
        "original_provider_sidecar_tree_sha256": terminal_lock[
            "provider_attempt_tree_sha256"
        ],
        "original_no_label_audit_sha256": ORIGINAL_NO_LABEL_AUDIT_SHA256,
        "repair_protocol_id": REPAIR_PROTOCOL_ID,
        "repair_implementation_commit": implementation_lock[
            "repair_implementation_commit"
        ],
        "answer_key_tree_sha256": answer_lock["answer_key_source_tree_sha256"],
        "case_score_vector_sha256": scoring_lock["case_score_vector_sha256"],
        "terminal_records": 103,
        "run_attempts": 103,
        "fixed_denominator": 103,
        "provider_calls_added": 0,
        "prediction_reruns": 0,
        "case_replacements": 0,
        "m3_changes": 0,
        "scorer_changes_excluding_envelope_loader": 0,
    }


HUMAN_REVIEW_CHECKLIST = """# RCA100 Evaluator Repair Publication Review Checklist

- Preserve PR #22 as `BLOCKED_PROTOCOL_DRIFT`.
- Confirm the post-lock evaluator repair disclosure remains prominent.
- Confirm all predictions and terminals remain immutable and the denominator is 103.
- Confirm the classification exactly matches the frozen paired interval.
- Confirm public artifacts contain aggregate values only.
- Do not claim that the preregistered evaluator executed unchanged.
- Do not rerun RCA100, modify M3/scoring, merge, release, or tag under this Goal.
"""


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def repair_main() -> None:
    repository = _repository_root()
    repair = RepairEnvironment.from_environment(
        os.environ, repository_root=repository
    )
    if current_repair_state(repair.repair_control) != "REPAIR_SCORED":
        raise ValueError("repair report build requires REPAIR_SCORED")
    inputs = load_repair_evaluation_inputs(repair)
    aggregate = _mapping(
        repair.repair_control / "results" / "aggregate.json",
        label="repair evaluator aggregate",
    )
    aggregate_path = repair.repair_control / "results" / "aggregate.json"
    case_scores_path = repair.repair_control / "results" / "case-scores.json"
    scoring_path = repair.repair_control / "locks" / "scoring-result-lock.json"
    scoring_lock = _mapping(
        scoring_path,
        label="repair scoring result lock",
    )
    scored_state = _mapping(
        repair.repair_control / "state" / "REPAIR_SCORED.json",
        label="repair scored state",
    )
    if (
        scored_state.get("scoring_result_lock_sha256")
        != sha256_file(scoring_path)
        or scoring_lock.get("aggregate_file_sha256")
        != sha256_file(aggregate_path)
        or scoring_lock.get("case_scores_file_sha256")
        != sha256_file(case_scores_path)
        or scoring_lock.get("answer_key_lock_sha256")
        != sha256_file(repair.repair_control / "locks" / "answer-key-lock.json")
        or scoring_lock.get("repair_implementation_commit")
        != inputs.implementation_lock.get("repair_implementation_commit")
        or scoring_lock.get("original_terminal_lock_sha256")
        != ORIGINAL_TERMINAL_LOCK_SHA256
        or scoring_lock.get("fixed_denominator") != 103
        or scoring_lock.get("terminals_scored") != 103
        or aggregate.get("fixed_denominator") != 103
        or scoring_lock.get("provider_calls") != 0
        or scoring_lock.get("prediction_reruns") != 0
        or scoring_lock.get("case_replacements") != 0
    ):
        raise ValueError("repair scoring result lock binding differs")
    audit = _mapping(
        repair.roots.control / "audit" / "no-label-schema-audit.json",
        label="original label-blind audit",
    )
    report = public_report(
        aggregate=aggregate,
        execution=_execution_summary(inputs.original.terminals),
        audit=audit,
        terminal_lock=inputs.original.terminal_lock,
        answer_lock=inputs.answer_lock,
        implementation_lock=inputs.implementation_lock,
        scoring_lock=scoring_lock,
    )
    final_json = repository / "docs" / "results" / "rca100-metrics-arbitration-v1-final.json"
    final_md = final_json.with_suffix(".md")
    brief = final_json.with_name("rca100-metrics-arbitration-v1-human-brief.md")
    review = repository / "docs" / "review-evidence" / "rca100-metrics-arbitration-v1"
    create_once_json(final_json, report)
    _write_once_text(final_md, markdown(report))
    _write_once_text(brief, human_brief(report))
    create_once_json(review / "evaluator-repair-disposition.json", disposition(report))
    create_once_json(
        review / "execution-integrity.json",
        execution_integrity(
            terminal_lock=inputs.original.terminal_lock,
            answer_lock=inputs.answer_lock,
            implementation_lock=inputs.implementation_lock,
            scoring_lock=scoring_lock,
        ),
    )
    _write_once_text(review / "human-review-checklist.md", HUMAN_REVIEW_CHECKLIST)
    public_paths = (
        final_json,
        final_md,
        brief,
        review / "evaluator-repair-disposition.json",
        review / "execution-integrity.json",
        review / "human-review-checklist.md",
    )
    findings = scan_public_artifacts(public_paths)
    if findings:
        raise ValueError(f"public repair report leakage detected: {findings}")
    print(json.dumps({"report_built": True, **report}, indent=2))
