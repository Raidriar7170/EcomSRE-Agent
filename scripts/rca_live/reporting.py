"""Canonical aggregate-only public projection for the live comparison."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from ecomsre_rca_unified.live_evaluation import scan_public_payloads


TUNE_JSON = Path("docs/results/strong-single-hierarchical-live-v1-tune.json")
TUNE_MARKDOWN = Path("docs/results/strong-single-hierarchical-live-v1-tune.md")
HUMAN_BRIEF = Path("docs/results/strong-single-hierarchical-live-v1-human-brief.md")
REGRESSION_JSON = Path(
    "docs/results/strong-single-hierarchical-live-v1-regression.json"
)
REGRESSION_MARKDOWN = Path(
    "docs/results/strong-single-hierarchical-live-v1-regression.md"
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("public projection input must be a JSON object")
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_exact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing public result differs from canonical projection")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def verify_scoring_artifact_hashes(
    private_root: Path, phase: str
) -> dict[str, object]:
    if phase not in {"tune", "regression"}:
        raise ValueError("public scoring phase is invalid")
    lock = _load(private_root / "locks" / f"{phase}-scoring-lock.json")
    aggregate_path = private_root / "evaluation" / f"{phase}-aggregate.json"
    scores_path = private_root / "evaluation" / f"{phase}-case-scores.json"
    if _sha(aggregate_path) != lock.get("aggregate_sha256"):
        raise ValueError(f"{phase} aggregate hash differs from scoring lock")
    if _sha(scores_path) != lock.get("case_scores_sha256"):
        raise ValueError(f"{phase} case-score hash differs from scoring lock")
    return lock


def _public_context_audit(context: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "b0_valid_contexts",
        "duplicate_entity_count",
        "h1_entity_count",
        "h1_propagation_relation_count",
        "h1_valid_contexts",
        "input_token_estimate",
        "invalid_ref_count",
        "revision",
        "source_counts",
        "truncation_count",
    )
    if any(key not in context for key in keys):
        raise ValueError("context audit public projection is incomplete")
    return {key: context[key] for key in keys}


def _tune_public(private_root: Path) -> dict[str, object]:
    aggregate = _load(private_root / "evaluation" / "tune-aggregate.json")
    context = _load(private_root / "audit" / "context-audit.json")
    preflight = _load(private_root / "locks" / "provider-preflight-lock.json")
    schedule = _load(private_root / "locks" / "schedule-lock.json")
    scoring = _load(private_root / "locks" / "tune-scoring-lock.json")
    return {
        "schema_version": "strong-single-hierarchical-live.public-tune.v1",
        "version": "strong-single-hierarchical-live-dev-v1",
        "classification": [
            "CONSUMED_DEVELOPMENT_EVALUATION",
            "NOT_EXTERNAL_VALIDATION",
            "B0_VS_H1_INDEPENDENT_PAIRED_CALLS",
        ],
        "historical_boundary": {
            "pr22": "BLOCKED_PROTOCOL_DRIFT",
            "pr23": (
                "POST_LOCK_EVALUATOR_REPAIR_DISCLOSED / "
                "RCA100_EXTERNAL_M3_NOT_SUPPORTED"
            ),
            "pr24": "NORMAL_MERGE_COMMIT_PRESERVED_LINEAGE",
            "pr25": "A2_APPLICABILITY_GATE_NOT_SUPPORTED_KEEP_A0",
        },
        "arm_contract": {
            "b0": "BASELINE_STRONG_SINGLE",
            "h1": "STRONG_SINGLE_HIERARCHICAL",
            "same_model": True,
            "same_output_schema": True,
            "same_raw_bounded_evidence": True,
            "model_calls_per_arm": 1,
            "specialist_calls": 0,
            "fusion_calls": 0,
            "post_model_override": False,
            "entity_cap": 64,
            "propagation_cap": 12,
        },
        "privacy": {
            "benchmark_identity_model_visible": False,
            "ground_truth_runtime_visible": False,
            "provider_payload_source_ids": False,
            "case_level_publication": False,
        },
        "context_audit": _public_context_audit(context),
        "provider_preflight": {
            "arms": preflight["arms"],
            "passed": preflight["passed"],
            "semantic_operations": preflight["semantic_operations"],
        },
        "schedule": {
            "tune": schedule["tune"],
            "alternating_order": True,
        },
        "results": aggregate,
        "integrity": {
            "tune_scoring_lock_sha256": _sha(
                private_root / "locks" / "tune-scoring-lock.json"
            ),
            "aggregate_sha256": scoring["aggregate_sha256"],
            "case_scores_committed": False,
            "raw_evidence_committed": False,
            "answer_key_committed": False,
        },
    }


def _regression_public(private_root: Path) -> dict[str, object] | None:
    aggregate_path = private_root / "evaluation" / "regression-aggregate.json"
    if not aggregate_path.exists():
        return None
    aggregate = _load(aggregate_path)
    scoring = _load(private_root / "locks" / "regression-scoring-lock.json")
    schedule = _load(private_root / "locks" / "schedule-lock.json")
    return {
        "schema_version": "strong-single-hierarchical-live.public-regression.v1",
        "version": "strong-single-hierarchical-live-dev-v1",
        "classification": [
            "CONSUMED_DEVELOPMENT_EVALUATION",
            "NOT_EXTERNAL_VALIDATION",
            "ONE_SHOT_PAIRED_REGRESSION",
        ],
        "arm_contract": {
            "b0": "BASELINE_STRONG_SINGLE",
            "h1": "STRONG_SINGLE_HIERARCHICAL",
            "same_model": True,
            "same_output_schema": True,
            "same_raw_bounded_evidence": True,
            "independent_model_calls_per_arm": 1,
            "specialist_calls": 0,
            "fusion_calls": 0,
            "post_model_override": False,
        },
        "privacy": {
            "benchmark_identity_model_visible": False,
            "ground_truth_runtime_visible": False,
            "provider_payload_source_ids": False,
            "case_level_publication": False,
        },
        "schedule": {
            "regression": schedule["regression"],
            "alternating_order": True,
        },
        "results": aggregate,
        "integrity": {
            "regression_scoring_lock_sha256": _sha(
                private_root / "locks" / "regression-scoring-lock.json"
            ),
            "aggregate_sha256": scoring["aggregate_sha256"],
            "reruns": 0,
            "post_run_tuning": False,
            "case_scores_committed": False,
        },
    }


def _metric_line(label: str, value: object) -> str:
    return f"- {label}: `{json.dumps(value, ensure_ascii=False, sort_keys=True)}`"


def _tune_markdown(report: Mapping[str, object]) -> str:
    results = report["results"]
    if not isinstance(results, Mapping):
        raise ValueError("public TUNE results are invalid")
    gate = results["gate"]
    if not isinstance(gate, Mapping):
        raise ValueError("public TUNE Gate is invalid")
    lines = [
        "# Strong Single vs Strong Single Hierarchical — TUNE",
        "",
        "This is a consumed development evaluation, not external validation.",
        "Each arm used one independent model call with alternating pair order, the ",
        "same model/output schema/raw bounded evidence, and zero override, Specialist, ",
        "or Fusion calls.",
        "",
        "## Frozen result",
        "",
        _metric_line("Verdict", gate["verdict"]),
        _metric_line("RCA100 aggregate", results["rca100"]),
        _metric_line("OB/SS aggregate", results["obss"]),
        _metric_line("Combined aggregate", results["combined"]),
        _metric_line("Cost", results["cost"]),
        _metric_line("Execution", results["execution"]),
        _metric_line("Descriptive paired inference", results["root_inference"]),
        "",
        "## Claim boundary",
        "",
        "No case-level identity, prediction, answer, entity, raw evidence, private ",
        "path, Provider endpoint, or credential is published. RE2-TT and new external ",
        "data were not accessed. This result does not establish external superiority.",
        "",
    ]
    return "\n".join(lines)


def _regression_markdown(report: Mapping[str, object]) -> str:
    results = report["results"]
    if not isinstance(results, Mapping):
        raise ValueError("public Regression results are invalid")
    gate = results["gate"]
    if not isinstance(gate, Mapping):
        raise ValueError("public Regression Gate is invalid")
    lines = [
        "# Strong Single vs Strong Single Hierarchical — Regression",
        "",
        "This is a one-shot consumed OB/SS development regression, not external ",
        "validation. The candidate was frozen after TUNE and was not tuned or rerun.",
        "B0 and H1 used paired independent one-call executions with alternating order, ",
        "the same model/output schema/raw bounded evidence, and zero post-model ",
        "override, Specialist, or Fusion calls.",
        "",
        "## Frozen result",
        "",
        _metric_line("Verdict", gate["verdict"]),
        _metric_line("OB/SS aggregate", results["obss"]),
        _metric_line("Cost", results["cost"]),
        _metric_line("Execution", results["execution"]),
        _metric_line("Descriptive paired inference", results["root_inference"]),
        "",
    ]
    return "\n".join(lines)


def _human_brief(tune: Mapping[str, object], regression: Mapping[str, object] | None) -> str:
    tune_results = tune["results"]
    if not isinstance(tune_results, Mapping) or not isinstance(
        tune_results.get("gate"), Mapping
    ):
        raise ValueError("Human Brief TUNE projection is invalid")
    tune_gate = tune_results["gate"]
    assert isinstance(tune_gate, Mapping)
    final_gate: Mapping[str, object] = tune_gate
    regression_text = "未执行（TUNE Gate 未通过或尚未进入 Regression）。"
    if regression is not None:
        regression_results = regression["results"]
        if not isinstance(regression_results, Mapping) or not isinstance(
            regression_results.get("gate"), Mapping
        ):
            raise ValueError("Human Brief Regression projection is invalid")
        final_gate = regression_results["gate"]  # type: ignore[assignment]
        regression_text = "已执行一次冻结候选的 120-pair OB/SS Regression。"
    return "\n".join(
        (
            "# Strong Single Hierarchical Live Development — Human Brief",
            "",
            "结论标记：",
            "",
            f"`{final_gate['verdict']}`",
            "",
            "本阶段比较 B0 Baseline Strong Single 与 H1 Strong Single Hierarchical。",
            "两臂逐 case 独立调用一次同一模型，交替 arm 顺序，共享相同输出 schema",
            "和原始 bounded evidence；没有后处理 override、Specialist 或 LLM Fusion。",
            "",
            f"TUNE：`{tune_gate['verdict']}`。{regression_text}",
            "",
            "这是已消费 development data 上的工程评估，不是 fresh external validation，",
            "也不支持把描述性 bootstrap / McNemar 结果表述为 external superiority。",
            "公开材料仅包含 aggregate；case-level 预测、答案、实体、原始证据和私有路径",
            "均未提交。",
            "",
        )
    )


def expected_public_outputs(private_root: Path) -> dict[Path, bytes]:
    tune = _tune_public(private_root)
    regression = _regression_public(private_root)
    outputs = {
        TUNE_JSON: _json_bytes(tune),
        TUNE_MARKDOWN: _tune_markdown(tune).encode("utf-8"),
        HUMAN_BRIEF: _human_brief(tune, regression).encode("utf-8"),
    }
    if regression is not None:
        outputs[REGRESSION_JSON] = _json_bytes(regression)
        outputs[REGRESSION_MARKDOWN] = _regression_markdown(regression).encode(
            "utf-8"
        )
    return outputs


def _reject_unexpected_optional_outputs(
    project_root: Path, outputs: Mapping[Path, bytes]
) -> None:
    all_outputs = {
        TUNE_JSON,
        TUNE_MARKDOWN,
        HUMAN_BRIEF,
        REGRESSION_JSON,
        REGRESSION_MARKDOWN,
    }
    unexpected = all_outputs - set(outputs)
    if any((project_root / relative).exists() for relative in unexpected):
        raise ValueError("unexpected stale public result exists")


def publish(project_root: Path, private_root: Path) -> dict[str, str]:
    outputs = expected_public_outputs(private_root)
    _reject_unexpected_optional_outputs(project_root, outputs)
    scan_public_payloads(outputs)
    hashes: dict[str, str] = {}
    for relative, payload in outputs.items():
        _write_exact(project_root / relative, payload)
        hashes[relative.as_posix()] = hashlib.sha256(payload).hexdigest()
    return hashes


def verify(project_root: Path, private_root: Path) -> dict[str, str]:
    outputs = expected_public_outputs(private_root)
    _reject_unexpected_optional_outputs(project_root, outputs)
    scan_public_payloads(outputs)
    hashes: dict[str, str] = {}
    for relative, expected in outputs.items():
        path = project_root / relative
        if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
            raise ValueError(f"canonical public result differs: {relative}")
        hashes[relative.as_posix()] = hashlib.sha256(expected).hexdigest()
    return hashes


__all__ = [
    "expected_public_outputs",
    "publish",
    "verify",
    "verify_scoring_artifact_hashes",
]
