"""Post-unblinding canonical evaluation and aggregate-only report builder."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Mapping

from ecomsre.evidence.hashes import canonical_json_bytes
from ecomsre_rca100.evaluation_integrity import load_frozen_evaluation_inputs
from ecomsre_rca100.evaluator import evaluate_terminals
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    advance_state,
    create_once_json,
    current_state,
    load_strict_json,
)
from ecomsre_rca100.prompt import output_schema_sha256, prompt_sha256
from ecomsre_rca100.public_projection import scan_public_artifacts
from ecomsre_rca100.runner import RCA100TerminalRecord


SOURCE_REPOSITORY = (
    "https://www.aiops.cn/gitlab/aiops-live-benchmark/agenticopseval.git"
)
SOURCE_COMMIT = "fd92cae17e6e14fa3ed0f3963c31838151fbdaa7"
INPUT_TREE_SHA256 = "8ab512ce9ad041ed1ffd89226c2df77d3bb741fed08990854f481794c98585bb"
FRESH_INPUT_TREE_SHA256 = "aca130e350330000e0d9bc575606e3a5378178b6d7e0c2afb5cf13910596fea9"
SCHEDULE_SHA256 = "00604fa3157edde3597a7ef6758637be06a099051181d921cc35a7f305c4459e"
MODEL = "gpt-5.4-mini-2026-03-17"
PROTOCOL_ID = "rca100-metrics-arbitration-v1"
_PROVIDER_CREDENTIALS = (
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def _execution_summary(
    terminals: Mapping[str, RCA100TerminalRecord],
) -> dict[str, object]:
    values = tuple(terminals.values())
    statuses = Counter(item.status.value for item in values)
    failures = Counter(
        item.failure_code for item in values if item.failure_code is not None
    )
    latencies = tuple(item.latency_seconds for item in values)
    return {
        "planned": 103,
        "terminalized": len(values),
        "completed": statuses["COMPLETED"],
        "failure_taxonomy": dict(sorted(failures.items())),
        "semantic_model_operations": sum(
            item.semantic_model_operations for item in values
        ),
        "specialist_calls": 0,
        "fusion_model_calls": 0,
        "provider_attempts": sum(item.provider_attempts for item in values),
        "transport_retries": sum(item.transport_retries for item in values),
        "known_token_lower_bound": sum(
            item.known_token_lower_bound for item in values
        ),
        "conservative_token_upper_bound": sum(
            item.conservative_token_upper_bound for item in values
        ),
        "mean_latency_seconds": statistics.fmean(latencies),
        "median_latency_seconds": statistics.median(latencies),
        "http_429_abort_triggered": any(
            item.failure_code == "HTTP_429" for item in values
        ),
        "metrics_ranking_available": sum(
            item.metrics_projection_status == "AVAILABLE" for item in values
        ),
        "metrics_ranking_unavailable": sum(
            item.metrics_projection_status == "METRICS_PROJECTION_UNAVAILABLE"
            for item in values
        ),
    }


def _public_report(
    aggregate: Mapping[str, object],
    execution: Mapping[str, object],
    audit: Mapping[str, object],
    terminal_lock: Mapping[str, object],
    answer_lock: Mapping[str, object],
    protocol_lock: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "rca100.metrics-arbitration-final.v1",
        "status": "RCA100_EXTERNAL_REPORT_FROZEN_READY_FOR_PUBLICATION_REVIEW",
        "classification": aggregate["root"]["classification"],  # type: ignore[index]
        "source": {
            "repository": SOURCE_REPOSITORY,
            "commit": SOURCE_COMMIT,
            "license": "CC BY-NC-SA 4.0",
            "fixed_denominator": 103,
            "agent_facing_files": 721,
            "input_tree_sha256": INPUT_TREE_SHA256,
            "fresh_content_tree_sha256": FRESH_INPUT_TREE_SHA256,
        },
        "protocol": {
            "implementation_commit": protocol_lock["implementation_commit"],
            "schedule_sha256": SCHEDULE_SHA256,
            "model": MODEL,
            "prompt_sha256": protocol_lock["prompt_sha256"],
            "output_schema_sha256": protocol_lock["output_schema_sha256"],
            "m3_rank_condition": "NONE_OR_GREATER_THAN_2",
            "m3_margin": 0.25,
            "fault_type_preservation": "MODEL_INITIAL",
            "bootstrap_seed": 20260810,
            "bootstrap_replicates": 10000,
            "agent_visible_modalities": ["task", "metrics", "logs", "traces"],
            "excluded_modalities": ["events", "full_alerts"],
            "topology_use": "DETERMINISTIC_CANONICALIZATION_ONLY",
            "entity_normalization": "UNICODE_NFC_TRIM_CASEFOLD_COLLAPSE_WHITESPACE_EXACT",
            "metrics_projection": {
                "formula": "ABS_POST_MINUS_PRE_OVER_MAX_ABS_PRE_EPSILON",
                "minimum_pre_samples": 3,
                "minimum_post_samples": 3,
                "entity_aggregation": "MAX_VALID_SERIES_F0",
                "top_k": 6,
            },
            "budget": {
                "timeout_seconds": 30,
                "max_completion_tokens": 2048,
                "max_provider_attempts": 206,
                "max_transport_retries": 103,
                "conservative_token_upper_bound": 6592000,
            },
        },
        "adapter_audit": {
            "tasks_parsed": audit["tasks_parsed"],
            "metrics_parsed": audit["metrics_parsed"],
            "logs_parsed": audit["logs_parsed"],
            "traces_parsed": audit["traces_parsed"],
            "topology_parsed": audit["topology_parsed"],
            "metrics_ranking_available": audit["metrics_ranking_available"],
            "metrics_ranking_unavailable": audit["metrics_ranking_unavailable"],
            "anchor_source_distribution": audit["anchor_source_distribution"],
            "unmapped_rows": {
                "metrics": audit["metrics_unmapped_rows"],
                "logs": audit["logs_unmapped_rows"],
                "traces": audit["traces_unmapped_rows"],
            },
        },
        "isolation": {
            "labels_absent_before_terminal_lock": True,
            "runtime_evaluator_roots_disjoint": True,
            "external_identifier_web_lookup": False,
            "provider_credentials_during_unblinding": False,
            "source_drift": False,
            "answer_tree_sha256": answer_lock["answer_key_tree_sha256"],
            "terminal_tree_sha256": terminal_lock["terminal_tree_sha256"],
        },
        "execution": dict(execution),
        "primary": aggregate["root"],
        "primary_inference_eligible": aggregate["primary_inference_eligible"],
        "secondary_pair": aggregate["pair"],
        "m3": aggregate["m3"],
        "descriptive_subgroups": aggregate["descriptive_subgroups"],
        "official_style": aggregate["official_style"],
        "claim_boundary": {
            "superiority_rule": "POINT_GT_0_AND_CI_LOWER_GT_0",
            "one_shot": True,
            "rerun": False,
            "release_or_deployment_claim": False,
            "contamination_caveat": (
                "The model snapshot predates formal publication, but indirect "
                "pretraining exposure to public benchmark material cannot be proven absent."
            ),
        },
    }


def _markdown(report: Mapping[str, object]) -> str:
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

Classification: `{report['classification']}`

This frozen one-shot evaluation used all 103 official RCA100 incidents. Each
case received one Strong Single model call followed by the unchanged,
deterministic root-only M3 rule. Events and full Alerts were excluded; topology
was used only for deterministic entity identity.

## Primary paired result

- Initial Root Entity correct: {primary['initial_correct']} / 103
- Final Root Entity correct: {primary['final_correct']} / 103
- Primary-inference eligible: {report['primary_inference_eligible']} / 103
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
- Semantic model operations: {execution['semantic_model_operations']}
- Specialist / Fusion model calls: 0 / 0
- Provider attempts / transport retries: {execution['provider_attempts']} / {execution['transport_retries']}
- Known token lower bound: {execution['known_token_lower_bound']}
- Conservative token upper bound: {execution['conservative_token_upper_bound']}
- Mean / median latency: {float(execution['mean_latency_seconds']):.3f}s / {float(execution['median_latency_seconds']):.3f}s
- Official composite: `OFFICIAL_COMPOSITE_NOT_AVAILABLE`

## Descriptive subgroups

The canonical JSON includes frozen aggregate-only subgroup records for fault
category, fault type, root entity domain/type, alert entity type, M3 action and
applicability, Initial rank, normalized-margin bins, and Metrics projection
availability. Every subgroup record carries its denominator. These descriptive
views do not alter the fixed 103-case primary endpoint.

## Claim boundary

The primary claim is limited to the same-run Initial-to-Final Root Entity
change under this exact frozen protocol. This is not a release, deployment,
equivalence, non-inferiority, or general autonomous-agent claim. The model
snapshot predates formal publication, but indirect pretraining exposure to
public benchmark material cannot be proven absent. No result-driven rerun,
case removal, M3 change, or post-result tuning was performed.
"""


def _human_brief(report: Mapping[str, object]) -> str:
    primary = report["primary"]
    m3 = report["m3"]
    assert isinstance(primary, Mapping)
    assert isinstance(m3, Mapping)
    return f"""# RCA100 外部盲测 Human Brief

状态：`{report['status']}`

结果分类：`{report['classification']}`

本次一次性外部 Holdout 固定纳入 103 个案例。每个案例只有一次 Strong
Single 调用，随后执行未修改的确定性 M3；没有 Specialist、Fusion、语义重试、
结果驱动重试或补跑。

- Initial Root 正确：{primary['initial_correct']} / 103
- Final Root 正确：{primary['final_correct']} / 103
- Primary inference eligible：{report['primary_inference_eligible']} / 103
- Root Damage / Rescue / Net：{primary['damage']} / {primary['rescue']} / {primary['net_rescue']}
- Root Damage Rate：{float(primary['damage_rate']):.6f}（{primary['damage']} / {primary['damage_rate_denominator']}）
- 95% 配对区间：[{float(primary['ci_lower']):.6f}, {float(primary['ci_upper']):.6f}]
- McNemar exact p：{float(primary['mcnemar_exact_p_value']):.6g}
- KEEP / OVERRIDE：{m3['keep']} / {m3['override']}

标签只在 103 个 terminal 锁定后，于隔离 evaluator 中获取；unblinding 时
Provider credentials 已移除。公开材料仅含 aggregate，不含任何逐案例身份、
预测、答案、证据或推理。

人审重点：核对结论措辞是否严格匹配冻结统计分类；不得基于结果修改 M3、
重跑 RCA100、扩大为发布或部署声明。模型快照早于正式发布，但无法证明完全
不存在间接预训练暴露，这一 caveat 必须保留。
"""


def _current_disposition(report: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": "rca100.public-disposition.v1",
        "status": report["status"],
        "classification": report["classification"],
        "draft_pr": "REVIEW_REQUIRED",
        "merge": "FORBIDDEN_BY_GOAL",
        "release_tag": "FORBIDDEN_BY_GOAL",
        "rerun": "FORBIDDEN",
    }


def _source_lock_public() -> dict[str, object]:
    return {
        "schema_version": "rca100.source-lock-public.v1",
        "repository": SOURCE_REPOSITORY,
        "commit": SOURCE_COMMIT,
        "license": "CC BY-NC-SA 4.0",
        "fixed_denominator": 103,
        "agent_facing_files": 721,
        "input_tree_sha256": INPUT_TREE_SHA256,
        "fresh_content_tree_sha256": FRESH_INPUT_TREE_SHA256,
    }


def _execution_integrity_public(
    *,
    protocol_lock: Mapping[str, object],
    terminal_lock: Mapping[str, object],
    answer_lock: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "rca100.execution-integrity-public.v1",
        "implementation_commit": protocol_lock["implementation_commit"],
        "schedule_sha256": SCHEDULE_SHA256,
        "terminal_tree_sha256": terminal_lock["terminal_tree_sha256"],
        "attempt_tree_sha256": terminal_lock["attempt_tree_sha256"],
        "provider_attempt_tree_sha256": terminal_lock[
            "provider_attempt_tree_sha256"
        ],
        "answer_tree_sha256": answer_lock["answer_key_tree_sha256"],
        "terminal_records": 103,
        "run_attempts": 103,
        "semantic_retries": 0,
        "case_replacements": 0,
    }


HUMAN_REVIEW_CHECKLIST = """# RCA100 Publication Review Checklist

- Confirm the classification exactly matches the frozen paired interval.
- Confirm all denominators remain 103 and every failure remains included.
- Confirm public artifacts contain aggregate values only.
- Preserve the contamination caveat and non-release claim boundary.
- Do not rerun the benchmark or change M3 based on this result.
- Merge, release, and tag remain outside this Goal.
"""


def main() -> None:
    roots = PrivateRoots.from_environment(os.environ)
    roots.validate(repository_root=_repository_root(), create=False)
    if current_state(roots.control) != "ANSWER_KEY_ACQUIRED":
        raise ValueError("report build requires ANSWER_KEY_ACQUIRED")
    if any(name in os.environ for name in _PROVIDER_CREDENTIALS):
        raise ValueError("Provider credentials remained during unblinding")
    inputs = load_frozen_evaluation_inputs(
        roots=roots,
        repository_root=_repository_root(),
        protocol_id=PROTOCOL_ID,
        expected_source_commit=SOURCE_COMMIT,
        expected_input_tree_sha256=INPUT_TREE_SHA256,
        expected_fresh_input_tree_sha256=FRESH_INPUT_TREE_SHA256,
        expected_input_file_count=721,
        expected_schedule_sha256=SCHEDULE_SHA256,
        expected_model=MODEL,
        expected_prompt_sha256=prompt_sha256(),
        expected_output_schema_sha256=output_schema_sha256(),
    )
    schedule = inputs.schedule
    terminals = inputs.terminals
    truths = inputs.truths
    catalogs = inputs.catalogs
    aggregate, scores = evaluate_terminals(
        schedule=schedule,
        terminals=terminals,
        truths=truths,
        catalogs=catalogs,
        alert_entity_types=inputs.alert_entity_types,
    )
    aggregate_sha = create_once_json(
        roots.evaluator / "results" / "aggregate.json", aggregate
    )
    create_once_json(
        roots.evaluator / "results" / "case-scores.json",
        {
            "schema_version": "rca100.private-case-scores.v1",
            "records": [item.model_dump(mode="json") for item in scores],
        },
    )
    advance_state(
        roots.control,
        "UNBLINDED",
        bindings={"evaluation_aggregate_sha256": aggregate_sha},
    )
    audit = load_strict_json(
        roots.control / "audit" / "no-label-schema-audit.json"
    )
    terminal_lock = inputs.terminal_lock
    answer_lock = inputs.answer_lock
    protocol_lock = inputs.protocol_lock
    if not all(
        isinstance(item, Mapping)
        for item in (audit, terminal_lock, answer_lock, protocol_lock)
    ):
        raise ValueError("RCA100 report inputs are invalid")
    report = _public_report(
        aggregate,
        _execution_summary(terminals),
        audit,  # type: ignore[arg-type]
        terminal_lock,  # type: ignore[arg-type]
        answer_lock,  # type: ignore[arg-type]
        protocol_lock,  # type: ignore[arg-type]
    )
    repo = _repository_root()
    final_json = repo / "docs" / "results" / "rca100-metrics-arbitration-v1-final.json"
    final_md = repo / "docs" / "results" / "rca100-metrics-arbitration-v1-final.md"
    brief = repo / "docs" / "results" / "rca100-metrics-arbitration-v1-human-brief.md"
    review = repo / "docs" / "review-evidence" / "rca100-metrics-arbitration-v1"
    create_once_json(final_json, report)
    _write_once_text(final_md, _markdown(report))
    _write_once_text(brief, _human_brief(report))
    create_once_json(
        review / "current-disposition.json",
        _current_disposition(report),
    )
    create_once_json(
        review / "source-lock-public.json",
        _source_lock_public(),
    )
    create_once_json(
        review / "execution-integrity.json",
        _execution_integrity_public(
            protocol_lock=protocol_lock,  # type: ignore[arg-type]
            terminal_lock=terminal_lock,  # type: ignore[arg-type]
            answer_lock=answer_lock,  # type: ignore[arg-type]
        ),
    )
    _write_once_text(
        review / "human-review-checklist.md",
        HUMAN_REVIEW_CHECKLIST,
    )
    public_paths = (
        final_json,
        final_md,
        brief,
        review / "current-disposition.json",
        review / "source-lock-public.json",
        review / "execution-integrity.json",
        review / "human-review-checklist.md",
    )
    findings = scan_public_artifacts(public_paths)
    if findings:
        raise ValueError(f"public report leakage detected: {findings}")
    recomputed, _ = evaluate_terminals(
        schedule=schedule,
        terminals=terminals,
        truths=truths,
        catalogs=catalogs,
        alert_entity_types=inputs.alert_entity_types,
    )
    if canonical_json_bytes(recomputed) != canonical_json_bytes(aggregate):
        raise ValueError("canonical report recomputation differs")
    print(json.dumps({"report_built_state": "UNBLINDED", **report}, indent=2))


def _dispatch_main() -> None:
    if sys.argv[1:] == ["evaluator-repair"]:
        from scripts.rca100.repair.build_report import repair_main

        repair_main()
        return
    main()


if __name__ == "__main__":
    _dispatch_main()
