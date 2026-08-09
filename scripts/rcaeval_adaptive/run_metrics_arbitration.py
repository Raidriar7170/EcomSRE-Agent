#!/usr/bin/env python3
"""Fixture replay and live execution CLI for Metrics Arbitration v1."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Literal, cast

from ecomsre.model.gateway import StdlibOpenAICompatibleTransport
from ecomsre_rcaeval.adapter import (
    ArchitectureContext,
    IncidentManifest,
    SourceObservation,
)
from ecomsre_rcaeval.contracts import Architecture, Diagnosis
from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval.scoring import normalize_indicator
from ecomsre_rcaeval.tools import SourceStatus, ToolEvidence
from ecomsre_rcaeval_v2 import dev3_execution as _dev3_execution
from ecomsre_rcaeval_v2.dev3_execution import (
    load_private_schedule,
    provider_config_from_env_file,
)
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev3ProviderProxy,
    Dev3RetryingTransport,
)
from ecomsre_rcaeval_v2.dev3_schedule import Variant
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    rebuild_attempt_accounting,
)
from ecomsre_rcaeval_v2.dev_execution import discover_case_index
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.schedule import CaseIdentity, SplitName, case_identity_bytes
from ecomsre_rcaeval_adaptive.metrics_arbitration import (
    MetricsArbitrationAction,
    MetricsArbitrationPolicy,
    MetricsServiceRank,
    arbitrate_diagnosis,
)
from ecomsre_rcaeval_adaptive.contracts import AdaptiveTerminalStatus
from ecomsre_rcaeval_adaptive.metrics_arbitration_runner import (
    MetricsArbitrationTerminalRecord,
    PacedTransport,
    RequestPacer,
    aggregate_metrics_arbitration,
    evaluate_metrics_arbitration_gate,
    execute_metrics_arbitration_batch,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
new_v1_reference_provider = _dev3_execution.new_v1_reference_provider
CONFIG_ROOT = PROJECT_ROOT / "config/rcaeval-metrics-arbitration-v1"
PUBLIC_REPLAY_PATH = (
    PROJECT_ROOT / "docs/analysis/rcaeval-metrics-arbitration-m3-replay.json"
)
RUNTIME_SCOPES = (
    "config/rcaeval-metrics-arbitration-v1",
    "config/rcaeval-re2-v1",
    "config/rcaeval-re2-v2-dev",
    "config/rcaeval-re2-v2-dev3",
    "docs/analysis/rcaeval-metrics-arbitration-m3-replay.json",
    "pyproject.toml",
    "scripts/analysis/rcaeval_multiagent_communication_audit.py",
    "scripts/rcaeval_adaptive/run_metrics_arbitration.py",
    "src/ecomsre",
    "src/ecomsre_rcaeval",
    "src/ecomsre_rcaeval_v2",
    "src/ecomsre_rcaeval_adaptive",
    "uv.lock",
)


def _load_audit_module() -> Any:
    path = PROJECT_ROOT / "scripts/analysis/rcaeval_multiagent_communication_audit.py"
    spec = importlib.util.spec_from_file_location(
        "rcaeval_multiagent_communication_audit_for_m3", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen fixture audit helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_AUDIT = _load_audit_module()
_load_candidate_rows = _AUDIT._load_candidate_rows
_load_case_index = _AUDIT._load_case_index
_tree_digest = _AUDIT._tree_digest
assert_public_payload = _AUDIT.assert_public_payload


FORMULA_PATH = (
    PROJECT_ROOT / "config/rcaeval-re2-v2-dev/indicator-candidate-formulas.json"
)
FORMULA_SHA256 = "51a8373e72e924151d9e8749ffc6b2959eadee59cc0b11510f9d8f6d6ed2455a"
PROVIDER_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_MODEL",
)
FROZEN_FIXTURE_SHA256 = {
    "candidate-3": "d3b43650b0165045d48917743e02b7dcc3771e96b90960360f9c98dc3a4360e5",
    "candidate-4": "a1f9e4037e762ba0968d64cc0ae9b0cac9bc9b24717f5b31d99cd9bfd0351ca5",
    "candidate-5": "4345a7fe7a7b89881a31c1b3260b078df1a1cf4da3bf2b8531208919fb51b904",
}
EXPECTED_REPLAY = {
    "candidate-3": {
        "completed": 60,
        "initial_root_correct": 49,
        "final_root_correct": 57,
        "override": 8,
        "root_rescue": 8,
        "root_damage": 0,
        "root_net_rescue": 8,
    },
    "candidate-4": {
        "completed": 59,
        "initial_root_correct": 51,
        "final_root_correct": 57,
        "override": 6,
        "root_rescue": 6,
        "root_damage": 0,
        "root_net_rescue": 6,
    },
    "candidate-5": {
        "completed": 60,
        "initial_root_correct": 45,
        "final_root_correct": 57,
        "override": 12,
        "root_rescue": 12,
        "root_damage": 0,
        "root_net_rescue": 12,
    },
}


def _canonical_indicator(value: object) -> str:
    text = str(value)
    if text not in {"cpu", "mem", "diskio", "latency", "socket"}:
        raise ValueError("fixture Initial indicator is not canonical")
    return text


def _ranking_for_row(row: Mapping[str, Any]) -> tuple[MetricsServiceRank, ...]:
    raw_ranking = row.get("metrics_ranking")
    raw_evidence = row.get("metrics_evidence", ())
    if not isinstance(raw_ranking, (list, tuple)) or not raw_ranking:
        raise ValueError("fixture replay lacks Metrics ranking")
    evidence_by_service: dict[str, list[str]] = {}
    if isinstance(raw_evidence, (list, tuple)):
        for item in raw_evidence:
            if not isinstance(item, Mapping):
                continue
            service = item.get("service")
            reference = item.get("evidence_ref")
            if isinstance(service, str) and isinstance(reference, str):
                evidence_by_service.setdefault(service, []).append(reference)
    output: list[MetricsServiceRank] = []
    for rank, item in enumerate(raw_ranking, start=1):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("fixture Metrics ranking row is invalid")
        service = str(item[0])
        output.append(
            MetricsServiceRank(
                service=service,
                rank=rank,
                score=float(item[1]),
                supporting_metrics_evidence_refs=tuple(
                    dict.fromkeys(evidence_by_service.get(service, ()))
                ),
            )
        )
    return tuple(output)


def evaluate_fixture_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, int], tuple[dict[str, Any], ...]]:
    """Apply the production M3 function to preserved Initial outputs, with no Provider."""

    private_rows: list[dict[str, Any]] = []
    completed = 0
    initial_correct = 0
    final_correct = 0
    overrides = 0
    root_rescue = 0
    root_damage = 0
    for row in rows:
        if row.get("completed") is not True:
            continue
        completed += 1
        initial = Diagnosis(
            root_cause_service=str(row["initial_service"]),
            root_cause_indicator=_canonical_indicator(
                row["initial_indicator"]
            ),  # type: ignore[arg-type]
            confidence=(
                None
                if row.get("initial_confidence") is None
                else float(row["initial_confidence"])
            ),
            evidence_refs=tuple(str(value) for value in row["initial_evidence_refs"]),
            explanation=str(row["initial_explanation"]),
        )
        result = arbitrate_diagnosis(
            initial, _ranking_for_row(row), MetricsArbitrationPolicy()
        )
        truth_service = str(row["truth_service"])
        before = initial.root_cause_service == truth_service
        after = result.final_root_service == truth_service
        override = (
            result.arbitration_decision.action
            is MetricsArbitrationAction.OVERRIDE_METRICS_TOP1
        )
        initial_correct += before
        final_correct += after
        overrides += override
        root_rescue += (not before) and after
        root_damage += before and not after
        private_rows.append(
            {
                "private_case_key": row.get("private_case_key"),
                "candidate": row.get("candidate"),
                "truth_service": truth_service,
                "truth_indicator": row.get("truth_indicator"),
                "initial_diagnosis": initial.model_dump(mode="json"),
                "metrics_service_ranking": [
                    item.model_dump(mode="json") for item in _ranking_for_row(row)
                ],
                "arbitration": result.model_dump(mode="json"),
                "initial_root_correct": before,
                "final_root_correct": after,
                "root_rescue": (not before) and after,
                "root_damage": before and not after,
                "indicator_preserved": (
                    result.final_indicator == initial.root_cause_indicator
                ),
            }
        )
    aggregate = {
        "completed": completed,
        "initial_root_correct": initial_correct,
        "final_root_correct": final_correct,
        "override": overrides,
        "root_rescue": root_rescue,
        "root_damage": root_damage,
        "root_net_rescue": root_rescue - root_damage,
    }
    return aggregate, tuple(private_rows)


def _write_json(path: Path, value: object, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600 if private else 0o644)


def _write_jsonl_create_once(
    path: Path, rows: Sequence[Mapping[str, Any]]
) -> None:
    payload = b"".join(
        (
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        for row in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing M3 private fixture replay differs")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _replay_markdown(public: Mapping[str, Any]) -> str:
    lines = [
        "# RCAEval Metrics Arbitration M3 Fixture Replay",
        "",
        "Status: `M3_FIXTURE_REPLAY_PASSED`",
        "",
        "This is a zero-Provider deterministic replay over consumed OB/SS development fixtures. It is not external validation.",
        "",
        "| Fixture | Completed | Initial Root | M3 Final Root | Override | Rescue | Damage | Net |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    aggregates = public["aggregates"]
    assert isinstance(aggregates, Mapping)
    for candidate in ("candidate-3", "candidate-4", "candidate-5"):
        value = aggregates[candidate]
        assert isinstance(value, Mapping)
        lines.append(
            f"| {candidate.title()} | {value['completed']} | "
            f"{value['initial_root_correct']} | {value['final_root_correct']} | "
            f"{value['override']} | {value['root_rescue']} | "
            f"{value['root_damage']} | {value['root_net_rescue']:+d} |"
        )
    lines.extend(
        (
            "",
            "All final indicators are the exact Initial indicators. Case-level material remains Git-external.",
        )
    )
    return "\n".join(lines) + "\n"


def run_fixture_replay(args: argparse.Namespace) -> int:
    if any(os.environ.get(name) for name in PROVIDER_ENV_NAMES):
        raise ValueError("fixture replay requires Provider environment removal")
    roots = {
        "candidate-3": args.candidate_3_root,
        "candidate-4": args.candidate_4_root,
        "candidate-5": args.candidate_5_root,
    }
    inventory: dict[str, dict[str, object]] = {}
    for candidate, root in roots.items():
        digest, file_count, byte_count = _tree_digest(root)
        if digest != FROZEN_FIXTURE_SHA256[candidate]:
            raise ValueError("M3_FIXTURE_REPLAY_MISMATCH")
        inventory[candidate] = {
            "sha256": digest,
            "file_count": file_count,
            "byte_count": byte_count,
        }
    cases = _load_case_index(args.ob_root, args.ss_root)
    config = load_indicator_config(FORMULA_PATH, expected_sha256=FORMULA_SHA256)
    projections: dict[str, dict[str, Any]] = {}
    aggregates: dict[str, dict[str, int]] = {}
    private_rows: list[dict[str, Any]] = []
    for candidate in ("candidate-3", "candidate-4", "candidate-5"):
        rows = _load_candidate_rows(
            candidate=candidate,
            root=roots[candidate],
            cases=cases,
            projections=projections,
            indicator_config=config,
        )
        aggregate, candidate_private = evaluate_fixture_rows(rows)
        if aggregate != EXPECTED_REPLAY[candidate]:
            raise ValueError("M3_FIXTURE_REPLAY_MISMATCH")
        if not all(row["indicator_preserved"] for row in candidate_private):
            raise ValueError("M3_FIXTURE_REPLAY_MISMATCH")
        aggregates[candidate] = aggregate
        private_rows.extend(candidate_private)
    for candidate, root in roots.items():
        after, _, _ = _tree_digest(root)
        if after != inventory[candidate]["sha256"]:
            raise ValueError("M3 fixture changed during replay")
    public = {
        "schema_version": "rcaeval-metrics-arbitration.fixture-replay.v1",
        "evaluation_version": "metrics-arbitration-v1",
        "status": "M3_FIXTURE_REPLAY_PASSED",
        "classification": [
            "CONSUMED_OBSS_DEVELOPMENT",
            "POST_HOC_DETERMINISTIC_REPLAY",
            "NOT_EXTERNAL_VALIDATION",
            "NO_PROVIDER_CALLS",
            "NO_TT_ACCESS",
        ],
        "rule": "M3",
        "aggregates": aggregates,
        "indicator_policy": "PRESERVE_EXACT_INITIAL_INDICATOR",
        "provider_calls": 0,
    }
    assert_public_payload(public)
    private_output = _validate_private_output(
        args.private_output,
        public_outputs=(args.public_json, args.public_markdown),
    )
    _write_jsonl_create_once(private_output, private_rows)
    _write_json(args.public_json, public, private=False)
    args.public_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.public_markdown.write_text(_replay_markdown(public), encoding="utf-8")
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Metrics arbitration JSON must be an object")
    return value


def _validate_fixture_replay(path: Path, *, require_tracked_path: bool) -> str:
    resolved = path.expanduser().resolve(strict=True)
    if require_tracked_path and resolved != PUBLIC_REPLAY_PATH.resolve(strict=True):
        raise ValueError("Provider lifecycle requires the tracked M3 replay artifact")
    if require_tracked_path:
        tracked = subprocess.run(
            ("git", "ls-files", "--error-unmatch", "--", str(PUBLIC_REPLAY_PATH)),
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        status = subprocess.run(
            ("git", "status", "--porcelain=v1", "--", str(PUBLIC_REPLAY_PATH)),
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        if tracked.returncode != 0 or status.stdout:
            raise ValueError("Provider lifecycle requires a tracked clean M3 replay")
    value = _load_json(resolved)
    if (
        value.get("schema_version")
        != "rcaeval-metrics-arbitration.fixture-replay.v1"
        or value.get("evaluation_version") != "metrics-arbitration-v1"
        or value.get("status") != "M3_FIXTURE_REPLAY_PASSED"
        or value.get("classification")
        != [
            "CONSUMED_OBSS_DEVELOPMENT",
            "POST_HOC_DETERMINISTIC_REPLAY",
            "NOT_EXTERNAL_VALIDATION",
            "NO_PROVIDER_CALLS",
            "NO_TT_ACCESS",
        ]
        or value.get("rule") != "M3"
        or value.get("provider_calls") != 0
        or value.get("indicator_policy")
        != "PRESERVE_EXACT_INITIAL_INDICATOR"
        or value.get("aggregates") != EXPECTED_REPLAY
    ):
        raise ValueError("M3_FIXTURE_REPLAY_MISMATCH")
    assert_public_payload(value)
    return _sha256(resolved)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_config_values(
    agent: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    model: Mapping[str, Any],
) -> None:
    expected_agent = {
        "architecture": "STRONG_SINGLE_INITIAL_THEN_DETERMINISTIC_ROOT_ONLY_METRICS_M3",
        "evaluation_version": "metrics-arbitration-v1",
        "fusion_model_enabled": False,
        "initial_rank_override_min_exclusive": 2,
        "initial_sources": ["metrics", "logs", "traces"],
        "normalized_margin_min": 0.25,
        "pacing": {
            "concurrency": 1,
            "minimum_interval_seconds": 5.0,
            "respect_retry_after": True,
        },
        "preserve_initial_indicator": True,
        "retry": {
            "max_allowlisted_transport_retries_per_operation": 1,
            "schema_retry": "FORBIDDEN",
            "semantic_retry": "FORBIDDEN",
        },
        "rule": "M3",
        "schema_version": "rcaeval-metrics-arbitration.agent.v1",
        "semantic_model_calls": 1,
        "specialists_enabled": False,
    }
    expected_model = {
        "fallback": "NO_FALLBACK",
        "inherited_indicator_config_path": (
            "config/rcaeval-re2-v2-dev/indicator-candidate-formulas.json"
        ),
        "inherited_indicator_config_sha256": FORMULA_SHA256,
        "inherited_transport_retry_policy_path": (
            "config/rcaeval-re2-v2-dev3/transport-retry-policy.json"
        ),
        "max_completion_tokens": 2048,
        "model": "gpt-5.4-mini-2026-03-17",
        "provider": "openai-compatible",
        "schema_retry": "FORBIDDEN",
        "schema_version": "rcaeval-metrics-arbitration.model-lock.v1",
        "selected_indicator_formula": "F0",
        "semantic_retry": "FORBIDDEN",
        "strong_single_initial": (
            "EXACT_V1_REFERENCE_PROVIDER_PROMPT_CONTEXT_AND_DIAGNOSIS"
        ),
        "temperature": 0.0,
        "timeout_seconds": 30.0,
        "top_p": 1.0,
        "transport_retry": "ONE_ALLOWLISTED_BYTE_IDENTICAL_REQUEST_RETRY",
        "transport_retry_policy_sha256": (
            "7fd010103f83a1cb99b0c478ddafdf6e9fd0dc349a4297e7bb55c9b4157c202b"
        ),
    }
    if dict(agent) != expected_agent:
        raise ValueError("Metrics arbitration agent lock differs from M3")
    if dict(model) != expected_model:
        raise ValueError("Metrics arbitration model lock differs")
    expected_schedules = {
        "smoke": "9ee6f72f0800750ab731d618faee3893d85b0f70475ac08676c233c70ee8206a",
        "tune": "f5bd027a40464d44051b686c32c3a07653e3516c9681b5a10becd4b13b82cd8d",
        "regression": "e5adae294869eceb0d8fdb323afbde2eb6778d771cc798dd36f4f6e8842bbe69",
    }
    if evaluation.get("schedule_sha256") != expected_schedules:
        raise ValueError("Metrics arbitration schedule lock differs")
    expected_gates: dict[str, dict[str, object]] = {
        "smoke_gate": {
            "scheduled_exact": 12,
            "terminalized_exact": 12,
            "completion_min": 11,
            "http_429_terminal_failure_max": 1,
            "privacy_schema_schedule_failure_max": 0,
            "semantic_operations_per_completed_exact": 1,
            "specialist_calls_exact": 0,
            "fusion_model_calls_exact": 0,
            "m3_action_valid_required": True,
        },
        "tune_gate": {
            "completion_min": 58,
            "http_429_terminal_failure_max": 3,
            "privacy_schema_schedule_failure_max": 0,
            "final_root_correct_min": 51,
            "pair_correct_min": 27,
            "same_run_root_rescue_strictly_greater_than_damage": True,
            "same_run_root_net_rescue_min": 1,
            "same_run_root_damage_max": 2,
            "same_run_pair_rescue_not_less_than_damage": True,
            "same_run_pair_net_rescue_min": 0,
            "mean_semantic_operations_exact": 1.0,
            "specialist_calls_exact": 0,
            "fusion_model_calls_exact": 0,
        },
        "regression_gate": {
            "completion_min": 114,
            "http_429_terminal_failure_max": 6,
            "privacy_schema_schedule_failure_max": 0,
            "final_root_correct_min": 97,
            "pair_correct_min": 50,
            "same_run_root_rescue_not_less_than_damage": True,
            "same_run_root_net_rescue_min": 0,
            "same_run_root_damage_rate_max": 0.05,
            "same_run_pair_rescue_not_less_than_damage": True,
            "same_run_pair_net_rescue_min": 0,
            "mean_semantic_operations_exact": 1.0,
            "specialist_calls_exact": 0,
            "fusion_model_calls_exact": 0,
        },
    }
    for section, expected in expected_gates.items():
        observed = evaluation.get(section)
        if not isinstance(observed, Mapping) or any(
            observed.get(key) != value for key, value in expected.items()
        ):
            raise ValueError("Metrics arbitration evaluation Gate differs")
    expected_evaluation = {
        "claim_boundary": [
            "CONSUMED_OBSS_DEVELOPMENT",
            "NOT_EXTERNAL_VALIDATION",
            "NOT_PRODUCTION_GENERALIZATION",
            "NO_TT_ACCESS",
        ],
        "classification": [
            "OBSS_DEVELOPMENT_POOL",
            "DEVELOPMENT_VISIBLE",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "evaluation_version": "metrics-arbitration-v1",
        "historical_baselines": {
            "lineage": "CROSS_RUN_CONTEXTUAL_BASELINE",
            "regression_strong_single_pair_correct": 55,
            "regression_strong_single_root_correct": 99,
            "tune_strong_single_pair_correct": 29,
            "tune_strong_single_root_correct": 51,
        },
        "regression_gate": expected_gates["regression_gate"],
        "same_run_endpoint_authority": {
            "indicator": "INITIAL_INDICATOR_PRESERVED",
            "pair": "INITIAL_AND_FINAL_FROM_SAME_METRICS_ARBITRATION_RUN",
            "root": "INITIAL_AND_FINAL_FROM_SAME_METRICS_ARBITRATION_RUN",
        },
        "schedule_sha256": expected_schedules,
        "schema_version": "rcaeval-metrics-arbitration.evaluation.v1",
        "smoke_gate": expected_gates["smoke_gate"],
        "tune_gate": expected_gates["tune_gate"],
    }
    if dict(evaluation) != expected_evaluation:
        raise ValueError("Metrics arbitration evaluation lock differs")


def _config_snapshot() -> dict[str, Any]:
    agent_path = CONFIG_ROOT / "agent.json"
    evaluation_path = CONFIG_ROOT / "evaluation.json"
    model_path = CONFIG_ROOT / "model-lock.json"
    agent = _load_json(agent_path)
    evaluation = _load_json(evaluation_path)
    model = _load_json(model_path)
    _validate_config_values(agent, evaluation, model)
    formula_path = PROJECT_ROOT / str(model["inherited_indicator_config_path"])
    retry_path = PROJECT_ROOT / str(model["inherited_transport_retry_policy_path"])
    if _sha256(formula_path) != model["inherited_indicator_config_sha256"]:
        raise ValueError("Metrics arbitration indicator config hash drift")
    if _sha256(retry_path) != model["transport_retry_policy_sha256"]:
        raise ValueError("Metrics arbitration retry policy hash drift")
    return {
        "agent": agent,
        "evaluation": evaluation,
        "model": model,
        "agent_config_sha256": _sha256(agent_path),
        "evaluation_config_sha256": _sha256(evaluation_path),
        "model_lock_sha256": _sha256(model_path),
        "formula_path": formula_path,
    }


def _clean_implementation_sha() -> str:
    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "--", *RUNTIME_SCOPES),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout:
        raise ValueError(
            "Metrics arbitration runtime must be committed before Provider execution"
        )
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _validate_private_root(path: Path) -> Path:
    requested = path.expanduser()
    if not requested.is_absolute() or requested.is_symlink():
        raise ValueError("Metrics arbitration private root must be absolute and real")
    resolved = requested.resolve(strict=False)
    project = PROJECT_ROOT.resolve()
    if resolved == project or resolved.is_relative_to(project):
        raise ValueError("Metrics arbitration private root must remain outside Git")
    existing = resolved
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    inside_git = subprocess.run(
        ("git", "-C", str(existing), "rev-parse", "--is-inside-work-tree"),
        check=False,
        capture_output=True,
        text=True,
    )
    if inside_git.returncode == 0 and inside_git.stdout.strip() == "true":
        raise ValueError("Metrics arbitration private root is inside a Git worktree")
    return resolved


def _validate_private_output(
    path: Path, *, public_outputs: Sequence[Path]
) -> Path:
    requested = path.expanduser()
    if not requested.is_absolute() or requested.is_symlink():
        raise ValueError("M3 private replay output must be absolute and non-symlink")
    resolved = requested.resolve(strict=False)
    _validate_private_root(resolved.parent)
    public = {item.expanduser().resolve(strict=False) for item in public_outputs}
    if resolved in public or any(
        resolved == item.parent or resolved.is_relative_to(item.parent)
        for item in public
        if item.parent == PROJECT_ROOT.resolve()
    ):
        raise ValueError("M3 private replay output overlaps a public output")
    if resolved.exists() and not resolved.is_file():
        raise ValueError("M3 private replay output must be a regular file")
    return resolved


def _write_create_once(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing Metrics arbitration private artifact differs")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _synthetic_preflight_context() -> tuple[IncidentManifest, ArchitectureContext]:
    case_id = "synthetic-provider-preflight"
    incident = IncidentManifest(
        case_id=case_id,
        system="RE2-OB",
        anomaly_timestamp=1_000,
        modalities=("metrics", "logs", "traces"),
    )
    evidence = (
        ToolEvidence(
            evidence_id="metric:0001",
            service="synthetic-alpha",
            name="synthetic_cpu_shift",
            started_at=900.0,
            ended_at=1_000.0,
            summary="Synthetic alpha service metric increased around T0.",
            points=((900.0, 1.0), (1_000.0, 9.0)),
        ),
        ToolEvidence(
            evidence_id="log:0001",
            service="synthetic-alpha",
            name="synthetic_error_pattern",
            started_at=1_000.0,
            ended_at=1_000.0,
            summary="Synthetic alpha service emitted an error at T0.",
        ),
        ToolEvidence(
            evidence_id="trace:0001",
            service="synthetic-beta",
            name="synthetic_trace_latency",
            started_at=1_000.0,
            ended_at=1_001.0,
            summary="Synthetic beta trace observed downstream latency.",
        ),
    )
    sources: tuple[Literal["metrics", "logs", "traces"], ...] = (
        "metrics",
        "logs",
        "traces",
    )
    context = ArchitectureContext(
        context_id="e" * 32,
        run_id="f" * 32,
        case_id=case_id,
        architecture=Architecture.SINGLE,
        evidence=evidence,
        canonical_evidence=(),
        specialist_assessments=(),
        source_observations=tuple(
            SourceObservation(source=source, status=SourceStatus.AVAILABLE)
            for source in sources
        ),
        investigated_sources=sources,
        commander_stages=(),
        tool_call_count=0,
        targeted_refinement_used=False,
    )
    return incident, context


def run_preflight(args: argparse.Namespace) -> int:
    run_root = _validate_private_root(args.run_root)
    implementation_sha = _clean_implementation_sha()
    lock = _config_snapshot()
    fixture_replay_sha256 = _validate_fixture_replay(
        args.fixture_replay, require_tracked_path=True
    )
    result_path = run_root / "preflight-result.json"
    if result_path.exists():
        result = _load_json(result_path)
        _validate_preflight_payload(
            result,
            implementation_sha=implementation_sha,
            lock=lock,
            fixture_replay_sha256=fixture_replay_sha256,
        )
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    model = lock["model"]
    sidecar = run_root / "provider-sidecar"
    max_completion_tokens = int(model["max_completion_tokens"])
    budget = AttemptBudget.restore(
        (sidecar,),
        max_provider_attempts=2,
        max_retry_attempts=1,
        prompt_token_reservation=29_952,
        max_completion_tokens=max_completion_tokens,
        max_conservative_tokens=2 * (29_952 + max_completion_tokens),
    )
    transport = Dev3RetryingTransport(
        PacedTransport(
            StdlibOpenAICompatibleTransport(),
            RequestPacer(float(lock["agent"]["pacing"]["minimum_interval_seconds"])),
        ),
        run_root=sidecar,
        budget=budget,
        policy_lock_sha256=str(model["transport_retry_policy_sha256"]),
        expected_timeout_seconds=float(model["timeout_seconds"]),
    )
    provider = Dev3ProviderProxy(
        new_v1_reference_provider(
            provider_config_from_env_file(args.env_file), transport=transport
        ),
        run_root=sidecar,
        policy_lock_sha256=str(model["transport_retry_policy_sha256"]),
    )
    incident, context = _synthetic_preflight_context()
    try:
        diagnosis = provider.diagnose(incident, context, Architecture.SINGLE)
    except Exception as error:
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_PREFLIGHT") from error
    accounting = rebuild_attempt_accounting(
        (sidecar,),
        prompt_token_reservation=29_952,
        max_completion_tokens=max_completion_tokens,
    )
    usage = provider.last_usage_tokens
    attempt_payloads = tuple(
        _load_json(path)
        for path in sorted((sidecar / "provider-attempts").glob("*.json"))
    )
    observed_http_429 = sum(
        item.get("failure_code") == "HTTP_429" for item in attempt_payloads
    )
    if (
        not isinstance(diagnosis, Diagnosis)
        or provider.calls != 1
        or usage is None
        or accounting.unknown_attempt_count != 0
        or accounting.provider_attempt_count not in {1, 2}
        or observed_http_429 != 0
    ):
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_PREFLIGHT")
    result = {
        "schema_version": "rcaeval-metrics-arbitration.preflight.v1",
        "status": "PROVIDER_CAPACITY_PREFLIGHT_PASSED",
        "classification": "SYNTHETIC_NON_CASE_PROVIDER_HEALTH_CALL",
        "implementation_git_sha": implementation_sha,
        "agent_config_sha256": lock["agent_config_sha256"],
        "evaluation_config_sha256": lock["evaluation_config_sha256"],
        "model_lock_sha256": lock["model_lock_sha256"],
        "fixture_replay_sha256": fixture_replay_sha256,
        "response_valid": True,
        "usage_known": True,
        "usage_tokens": usage,
        "http_429": 0,
        "schema_error": False,
        "provider_calls": 1,
        "provider_attempts": accounting.provider_attempt_count,
        "transport_retries": accounting.retry_attempt_count,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _write_create_once(result_path, result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


def _validate_preflight_payload(
    result: Mapping[str, Any],
    *,
    implementation_sha: str,
    lock: Mapping[str, Any],
    fixture_replay_sha256: str,
) -> None:
    provider_attempts = result.get("provider_attempts")
    transport_retries = result.get("transport_retries")
    if (
        result.get("schema_version")
        != "rcaeval-metrics-arbitration.preflight.v1"
        or result.get("status") != "PROVIDER_CAPACITY_PREFLIGHT_PASSED"
        or result.get("classification")
        != "SYNTHETIC_NON_CASE_PROVIDER_HEALTH_CALL"
        or result.get("implementation_git_sha") != implementation_sha
        or result.get("agent_config_sha256") != lock["agent_config_sha256"]
        or result.get("evaluation_config_sha256")
        != lock["evaluation_config_sha256"]
        or result.get("model_lock_sha256") != lock["model_lock_sha256"]
        or result.get("fixture_replay_sha256") != fixture_replay_sha256
        or result.get("response_valid") is not True
        or result.get("usage_known") is not True
        or type(result.get("usage_tokens")) is not int
        or int(result["usage_tokens"]) < 0
        or result.get("http_429") != 0
        or result.get("schema_error") is not False
        or result.get("provider_calls") != 1
        or type(provider_attempts) is not int
        or provider_attempts not in {1, 2}
        or type(transport_retries) is not int
        or transport_retries != provider_attempts - 1
    ):
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_PREFLIGHT")


def _load_bound_preflight(
    path: Path, implementation_sha: str, lock: Mapping[str, Any]
) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=True)
    _validate_private_root(resolved.parent)
    result = _load_json(resolved)
    fixture_replay_sha256 = _validate_fixture_replay(
        PUBLIC_REPLAY_PATH, require_tracked_path=True
    )
    _validate_preflight_payload(
        result,
        implementation_sha=implementation_sha,
        lock=lock,
        fixture_replay_sha256=fixture_replay_sha256,
    )
    return result


def _git_runtime_unchanged(older_sha: str, newer_sha: str) -> bool:
    ancestor = subprocess.run(
        ("git", "merge-base", "--is-ancestor", older_sha, newer_sha),
        cwd=PROJECT_ROOT,
        check=False,
    )
    unchanged = subprocess.run(
        ("git", "diff", "--quiet", older_sha, newer_sha, "--", *RUNTIME_SCOPES),
        cwd=PROJECT_ROOT,
        check=False,
    )
    return ancestor.returncode == 0 and unchanged.returncode == 0


def _expected_gate_disposition(phase: str, passed: bool) -> str:
    if passed:
        return "PASSED"
    return {
        "smoke": "METRICS_ARBITRATION_SMOKE_NOT_PASSED",
        "tune": "METRICS_ARBITRATION_TUNE_NOT_PASSED_READY_FOR_REVIEW",
        "regression": "METRICS_ARBITRATION_REGRESSION_NOT_PASSED",
    }[phase]


def _validate_development_result(
    path: Path,
    *,
    expected_phase: Literal["smoke", "tune", "regression"],
    implementation_sha: str,
    lock: Mapping[str, Any],
    preflight_result_sha256: str,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=True)
    _validate_private_root(resolved.parent)
    result = _load_json(resolved)
    aggregate = result.get("aggregate")
    outcomes = result.get("outcomes")
    older_sha = result.get("implementation_git_sha")
    expected_schedule_sha256 = str(
        lock["evaluation"]["schedule_sha256"][expected_phase]
    )
    fixture_replay_sha256 = _validate_fixture_replay(
        PUBLIC_REPLAY_PATH, require_tracked_path=True
    )
    expected_count = {"smoke": 12, "tune": 60, "regression": 120}[
        expected_phase
    ]
    if (
        result.get("schema_version")
        != "rcaeval-metrics-arbitration.development-result.v1"
        or result.get("evaluation_version") != "metrics-arbitration-v1"
        or result.get("phase") != expected_phase
        or result.get("classification")
        != [
            "CONSUMED_OBSS_DEVELOPMENT_RESULT",
            "NOT_EXTERNAL_VALIDATION",
            "NO_TT_ACCESS",
        ]
        or not isinstance(aggregate, dict)
        or not isinstance(outcomes, list)
        or len(outcomes) != expected_count
        or any(not isinstance(row, Mapping) for row in outcomes)
        or not isinstance(older_sha, str)
        or not _git_runtime_unchanged(older_sha, implementation_sha)
        or result.get("agent_config_sha256") != lock["agent_config_sha256"]
        or result.get("evaluation_config_sha256")
        != lock["evaluation_config_sha256"]
        or result.get("model_lock_sha256") != lock["model_lock_sha256"]
        or result.get("fixture_replay_sha256") != fixture_replay_sha256
        or result.get("preflight_result_sha256") != preflight_result_sha256
        or result.get("schedule_sha256") != expected_schedule_sha256
    ):
        raise ValueError("Metrics arbitration phase result lineage differs")
    recomputed = _aggregate_outcome_rows(outcomes, scheduled=expected_count)
    stored_without_gate = {
        key: value
        for key, value in aggregate.items()
        if key not in {"gate_passed", "gate_disposition"}
    }
    if recomputed != stored_without_gate:
        raise ValueError("Metrics arbitration phase aggregate differs from outcomes")
    gate_passed = evaluate_metrics_arbitration_gate(expected_phase, recomputed)
    if (
        aggregate.get("gate_passed") is not gate_passed
        or aggregate.get("gate_disposition")
        != _expected_gate_disposition(expected_phase, gate_passed)
    ):
        raise ValueError("Metrics arbitration phase Gate differs")
    return result


def _load_phase_authorization(
    path: Path,
    *,
    expected_phase: Literal["smoke", "tune", "regression"],
    implementation_sha: str,
    lock: Mapping[str, Any],
    preflight_result_sha256: str,
) -> dict[str, Any]:
    result = _validate_development_result(
        path,
        expected_phase=expected_phase,
        implementation_sha=implementation_sha,
        lock=lock,
        preflight_result_sha256=preflight_result_sha256,
    )
    aggregate = result["aggregate"]
    if aggregate.get("gate_passed") is not True:
        raise ValueError("Metrics arbitration prior phase authorization differs")
    return result


def _scheduled_identities(
    path: Path, phase: str, *, expected_sha256: str
) -> tuple[CaseIdentity, ...]:
    if _sha256(path) != expected_sha256:
        raise ValueError("Metrics arbitration schedule hash differs")
    split = SplitName.DEV_VALIDATION if phase == "regression" else SplitName.DESIGN
    records = load_private_schedule(path, allowed_split=split)
    identities = tuple(
        item.identity for item in records if item.variant is Variant.SINGLE_V1_REFERENCE
    )
    expected = {"smoke": 12, "tune": 60, "regression": 120}[phase]
    if len(identities) != expected or len(set(identities)) != expected:
        raise ValueError("Metrics arbitration schedule count differs")
    return identities


def _evaluate_terminals(
    identities: tuple[CaseIdentity, ...],
    terminals: tuple[MetricsArbitrationTerminalRecord, ...],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(identities) != len(terminals):
        raise ValueError("Metrics arbitration terminal count differs")
    rows: list[dict[str, Any]] = []
    for identity, terminal in zip(identities, terminals, strict=True):
        completed = terminal.status is AdaptiveTerminalStatus.COMPLETED
        result = terminal.result
        initial_root_correct: bool | None = None
        initial_pair_correct: bool | None = None
        final_root_correct: bool | None = None
        final_pair_correct: bool | None = None
        action: str | None = None
        rank_passed: bool | None = None
        margin_passed: bool | None = None
        margin: float | None = None
        initial_rank: int | None = None
        correct_override = False
        wrong_override = False
        if result is not None:
            diagnosis = result.diagnosis
            decision = diagnosis.arbitration_decision
            truth_indicator = normalize_indicator(identity.fault)
            initial_root_correct = (
                diagnosis.initial_diagnosis.root_cause_service
                == identity.root_cause_service
            )
            initial_pair_correct = bool(
                initial_root_correct
                and diagnosis.initial_diagnosis.root_cause_indicator == truth_indicator
            )
            final_root_correct = diagnosis.final_root_service == identity.root_cause_service
            final_pair_correct = bool(
                final_root_correct and diagnosis.final_indicator == truth_indicator
            )
            action = decision.action.value
            rank_passed = decision.rank_condition_passed
            margin_passed = decision.margin_condition_passed
            margin = decision.normalized_margin
            initial_rank = decision.initial_metrics_rank_or_none
            override = action == "OVERRIDE_METRICS_TOP1"
            correct_override = bool(
                override and not initial_root_correct and final_root_correct
            )
            wrong_override = bool(override and not final_root_correct)
        rows.append(
            {
                "private_case_key": hashlib.sha256(
                    case_identity_bytes(identity)
                ).hexdigest(),
                "completed": completed,
                "terminal_status": terminal.status.value,
                "failure_code": terminal.failure_code,
                "disqualifying_failure": terminal.status.value
                in {
                    "INVALID_SCHEMA",
                    "PROTOCOL_VIOLATION",
                    "RUNTIME_CONTRACT_VIOLATION",
                    "INTERRUPTED",
                },
                "initial_root_correct": initial_root_correct,
                "initial_pair_correct": initial_pair_correct,
                "final_root_correct": final_root_correct,
                "final_pair_correct": final_pair_correct,
                "action": action,
                "reason_codes": (
                    () if result is None else result.diagnosis.arbitration_decision.reason_codes
                ),
                "rank_condition_passed": rank_passed,
                "margin_condition_passed": margin_passed,
                "both_conditions_passed": bool(rank_passed and margin_passed),
                "initial_metrics_rank_or_none": initial_rank,
                "normalized_margin": margin,
                "semantic_operations": terminal.semantic_operations_attempted,
                "provider_attempts": terminal.attempt_accounting.provider_attempt_count,
                "transport_retries": terminal.attempt_accounting.retry_attempt_count,
                "known_token_lower_bound": (
                    terminal.attempt_accounting.known_token_lower_bound
                ),
                "conservative_token_upper_bound": (
                    terminal.attempt_accounting.conservative_token_upper_bound
                ),
                "latency_ms": terminal.latency_ms,
                "correct_override": correct_override,
                "wrong_override": wrong_override,
            }
        )
    return _aggregate_outcome_rows(rows, scheduled=len(identities)), rows


def _aggregate_outcome_rows(
    rows: Sequence[Mapping[str, Any]], *, scheduled: int
) -> dict[str, Any]:
    action_counts: Counter[str] = Counter(
        str(row["action"]) for row in rows if isinstance(row.get("action"), str)
    )
    reason_counts: Counter[str] = Counter(
        str(reason)
        for row in rows
        for reason in row.get("reason_codes", ())
        if isinstance(reason, str)
    )
    initial_ranks: Counter[str] = Counter(
        "NONE" if row.get("initial_metrics_rank_or_none") is None else str(row["initial_metrics_rank_or_none"])
        for row in rows
        if row.get("completed") is True
    )
    margins = [
        float(value)
        for row in rows
        if isinstance((value := row.get("normalized_margin")), (int, float))
        and not isinstance(value, bool)
    ]
    aggregate = aggregate_metrics_arbitration(rows, scheduled=scheduled)
    aggregate.update(
        {
            "keep_count": action_counts["KEEP_INITIAL"],
            "override_count": action_counts["OVERRIDE_METRICS_TOP1"],
            "correct_override_count": sum(
                row.get("correct_override") is True for row in rows
            ),
            "wrong_override_count": sum(
                row.get("wrong_override") is True for row in rows
            ),
            "rank_condition_count": sum(
                row["rank_condition_passed"] is True for row in rows
            ),
            "margin_condition_count": sum(
                row["margin_condition_passed"] is True for row in rows
            ),
            "both_condition_count": sum(
                row["both_conditions_passed"] is True for row in rows
            ),
            "initial_rank_distribution": dict(sorted(initial_ranks.items())),
            "margin_distribution": {
                "minimum": None if not margins else min(margins),
                "mean": None if not margins else sum(margins) / len(margins),
                "maximum": None if not margins else max(margins),
            },
            "reason_code_distribution": dict(sorted(reason_counts.items())),
            "provider_attempts": sum(
                int(row["provider_attempts"])
                for row in rows
                if type(row.get("provider_attempts")) is int
            ),
            "transport_retries": sum(
                int(row["transport_retries"])
                for row in rows
                if type(row.get("transport_retries")) is int
            ),
            "known_token_lower_bound": sum(
                int(row["known_token_lower_bound"])
                for row in rows
                if type(row.get("known_token_lower_bound")) is int
            ),
            "conservative_token_upper_bound": sum(
                int(row["conservative_token_upper_bound"])
                for row in rows
                if type(row.get("conservative_token_upper_bound")) is int
            ),
            "mean_latency_ms": sum(float(row["latency_ms"]) for row in rows)
            / len(rows),
            "mean_latency_ms_completed_only": (
                None
                if not any(row["completed"] for row in rows)
                else sum(float(row["latency_ms"]) for row in rows if row["completed"])
                / sum(row["completed"] for row in rows)
            ),
        }
    )
    return aggregate


def run_development(args: argparse.Namespace) -> int:
    phase = cast(Literal["smoke", "tune", "regression"], str(args.phase))
    if (
        (phase == "smoke" and (args.smoke_result is not None or args.tune_result is not None))
        or (phase == "tune" and (args.smoke_result is None or args.tune_result is not None))
        or (phase == "regression" and (args.tune_result is None or args.smoke_result is not None))
    ):
        raise ValueError("Metrics arbitration phase authorization arguments differ")
    run_root = _validate_private_root(args.run_root)
    result_path = run_root / "results" / f"{phase}.json"
    implementation_sha = _clean_implementation_sha()
    lock = _config_snapshot()
    preflight = _load_bound_preflight(
        args.preflight_result, implementation_sha, lock
    )
    preflight_result_sha256 = _sha256(
        args.preflight_result.expanduser().resolve(strict=True)
    )
    if phase == "tune":
        if args.smoke_result.expanduser().resolve(strict=True) != (
            run_root / "results/smoke.json"
        ):
            raise ValueError("TUNE must reuse the Smoke run root")
        _load_phase_authorization(
            args.smoke_result,
            expected_phase="smoke",
            implementation_sha=implementation_sha,
            lock=lock,
            preflight_result_sha256=preflight_result_sha256,
        )
    elif phase == "regression":
        _load_phase_authorization(
            args.tune_result,
            expected_phase="tune",
            implementation_sha=implementation_sha,
            lock=lock,
            preflight_result_sha256=preflight_result_sha256,
        )
    identities = _scheduled_identities(
        args.schedule,
        phase,
        expected_sha256=str(lock["evaluation"]["schedule_sha256"][phase]),
    )
    if result_path.exists():
        existing = _validate_development_result(
            result_path,
            expected_phase=phase,
            implementation_sha=implementation_sha,
            lock=lock,
            preflight_result_sha256=preflight_result_sha256,
        )
        existing_aggregate = existing["aggregate"]
        print(
            json.dumps(
                {
                    "phase": phase,
                    "reused": True,
                    "gate_passed": existing_aggregate["gate_passed"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0 if existing_aggregate["gate_passed"] else 2
    cases: Mapping[CaseIdentity, DevCase] = discover_case_index(
        args.ob_root, args.ss_root, set(identities)
    )
    model = lock["model"]
    schedule_sha256 = _sha256(args.schedule)
    run_lock = {
        "schema_version": "rcaeval-metrics-arbitration.run-lock.v1",
        "evaluation_version": "metrics-arbitration-v1",
        "phase": phase,
        "implementation_git_sha": implementation_sha,
        "agent_config_sha256": lock["agent_config_sha256"],
        "evaluation_config_sha256": lock["evaluation_config_sha256"],
        "model_lock_sha256": lock["model_lock_sha256"],
        "fixture_replay_sha256": preflight["fixture_replay_sha256"],
        "preflight_result_sha256": preflight_result_sha256,
        "schedule_sha256": schedule_sha256,
        "rule": "M3",
        "semantic_model_calls": 1,
        "specialist_calls": 0,
        "fusion_model_calls": 0,
    }
    _write_create_once(run_root / "run-locks" / f"{phase}.json", run_lock)
    split: Literal["TUNE_SET", "REGRESSION_SET"] = (
        "REGRESSION_SET" if phase == "regression" else "TUNE_SET"
    )
    terminals = execute_metrics_arbitration_batch(
        identities,
        cases=cases,
        split=split,
        provider_config=provider_config_from_env_file(args.env_file),
        timeout_seconds=float(model["timeout_seconds"]),
        max_completion_tokens=int(model["max_completion_tokens"]),
        indicator_formula=FormulaId.F0,
        indicator_config=load_indicator_config(
            lock["formula_path"],
            expected_sha256=str(model["inherited_indicator_config_sha256"]),
        ),
        policy=MetricsArbitrationPolicy.model_validate(
            {
                "initial_rank_override_min_exclusive": lock["agent"][
                    "initial_rank_override_min_exclusive"
                ],
                "normalized_margin_min": lock["agent"]["normalized_margin_min"],
                "preserve_initial_indicator": lock["agent"][
                    "preserve_initial_indicator"
                ],
                "semantic_model_calls": lock["agent"]["semantic_model_calls"],
                "specialists_enabled": lock["agent"]["specialists_enabled"],
                "fusion_model_enabled": lock["agent"]["fusion_model_enabled"],
            }
        ),
        run_root=run_root,
        policy_lock_sha256=str(model["transport_retry_policy_sha256"]),
        minimum_interval_seconds=float(
            lock["agent"]["pacing"]["minimum_interval_seconds"]
        ),
        progress=lambda index, total, terminal: print(
            f"{phase} {index}/{total} {terminal.status.value}", flush=True
        ),
    )
    aggregate, rows = _evaluate_terminals(identities, terminals)
    aggregate["gate_passed"] = evaluate_metrics_arbitration_gate(phase, aggregate)
    aggregate["gate_disposition"] = _expected_gate_disposition(
        phase, bool(aggregate["gate_passed"])
    )
    private = {
        "schema_version": "rcaeval-metrics-arbitration.development-result.v1",
        "evaluation_version": "metrics-arbitration-v1",
        "classification": [
            "CONSUMED_OBSS_DEVELOPMENT_RESULT",
            "NOT_EXTERNAL_VALIDATION",
            "NO_TT_ACCESS",
        ],
        "phase": phase,
        "implementation_git_sha": implementation_sha,
        "agent_config_sha256": lock["agent_config_sha256"],
        "evaluation_config_sha256": lock["evaluation_config_sha256"],
        "model_lock_sha256": lock["model_lock_sha256"],
        "fixture_replay_sha256": preflight["fixture_replay_sha256"],
        "preflight_result_sha256": preflight_result_sha256,
        "schedule_sha256": schedule_sha256,
        "aggregate": aggregate,
        "outcomes": rows,
    }
    _write_create_once(result_path, private)
    print(
        json.dumps(
            {
                "phase": phase,
                "gate_passed": aggregate["gate_passed"],
                "completed": aggregate["completed"],
                "root": aggregate["final_root_correct"],
                "pair": aggregate["final_pair_correct"],
                "net_root_rescue": aggregate["same_run_root_net_rescue"],
                "http_429": aggregate["http_429_terminal_failures"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if aggregate["gate_passed"] else 2


def _public_phase(
    path: Path,
    expected_phase: Literal["smoke", "tune", "regression"],
    *,
    implementation_sha: str,
    lock: Mapping[str, Any],
    preflight_result_sha256: str,
) -> dict[str, Any]:
    private = _validate_development_result(
        path,
        expected_phase=expected_phase,
        implementation_sha=implementation_sha,
        lock=lock,
        preflight_result_sha256=preflight_result_sha256,
    )
    return dict(private["aggregate"])


def _development_status(phases: Mapping[str, Any]) -> str:
    regression = phases.get("regression")
    tune = phases.get("tune")
    smoke = phases.get("smoke")
    if isinstance(regression, Mapping):
        return (
            "METRICS_ARBITRATION_REGRESSION_PASSED_READY_FOR_FRESH_HOLDOUT_PLAN"
            if regression.get("gate_passed") is True
            else "METRICS_ARBITRATION_REGRESSION_NOT_PASSED"
        )
    if isinstance(tune, Mapping):
        return (
            "METRICS_ARBITRATION_TUNE_PASSED_REGRESSION_PENDING"
            if tune.get("gate_passed") is True
            else "METRICS_ARBITRATION_TUNE_NOT_PASSED_READY_FOR_REVIEW"
        )
    if isinstance(smoke, Mapping):
        return (
            "METRICS_ARBITRATION_SMOKE_PASSED_TUNE_PENDING"
            if smoke.get("gate_passed") is True
            else "METRICS_ARBITRATION_SMOKE_NOT_PASSED"
        )
    if phases.get("preflight") is not None:
        return "PROVIDER_CAPACITY_PREFLIGHT_PASSED_SMOKE_PENDING"
    return "IMPLEMENTED_AWAITING_PROVIDER_CAPACITY_PREFLIGHT"


def _results_markdown(public: Mapping[str, Any]) -> str:
    lines = [
        "# RCAEval Metrics Arbitration v1 — Development Results",
        "",
        f"Status: `{public['status']}`",
        "",
        "M3 changes only the Root service when the Initial service is outside Metrics Top-2 and the normalized Top-1/Top-2 margin is at least 0.25. The exact Initial indicator is always retained.",
        "",
        "The primary evidence is the same-run Initial → Final comparison. Historical Strong Single values are cross-run context, not paired causal evidence.",
        "",
        "## Frozen fixture replay",
        "",
        "| Fixture | Completed | Initial Root | M3 Final Root | Override | Rescue | Damage | Net |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    fixture = public["fixture_replay"]
    assert isinstance(fixture, Mapping)
    aggregates = fixture["aggregates"]
    assert isinstance(aggregates, Mapping)
    for candidate in ("candidate-3", "candidate-4", "candidate-5"):
        value = aggregates[candidate]
        assert isinstance(value, Mapping)
        lines.append(
            f"| {candidate.title()} | {value['completed']} | "
            f"{value['initial_root_correct']} | {value['final_root_correct']} | "
            f"{value['override']} | {value['root_rescue']} | "
            f"{value['root_damage']} | {value['root_net_rescue']:+d} |"
        )
    lines.extend(("", "## Live phases", ""))
    phases = public["phases"]
    assert isinstance(phases, Mapping)
    for name in ("smoke", "tune", "regression"):
        value = phases.get(name)
        if not isinstance(value, Mapping):
            lines.append(f"- {name.title()}: not executed.")
            continue
        lines.append(
            f"- {name.title()}: gate `{value['gate_passed']}`; completed "
            f"{value['completed']}/{value['scheduled']}; Final Root "
            f"{value['final_root_correct']}; Final Pair {value['final_pair_correct']}; "
            f"Root net rescue {value['same_run_root_net_rescue']:+d}."
        )
    lines.extend(
        (
            "",
            "## Historical context",
            "",
            "- TUNE Strong Single: Root 51/60, Pair 29/60.",
            "- Regression Strong Single: Root 99/120, Pair 55/120.",
            "- Classification: `CROSS_RUN_CONTEXTUAL_BASELINE`.",
            "",
            "Claim boundary: consumed OB/SS development evidence only; no TT access; no external validation or production-generalization claim.",
        )
    )
    return "\n".join(lines) + "\n"


def _human_brief(public: Mapping[str, Any]) -> str:
    phases = public["phases"]
    assert isinstance(phases, Mapping)
    tune = phases.get("tune")
    regression = phases.get("regression")
    lines = [
        "# Human Brief：Metrics Arbitration v1",
        "",
        f"当前状态：`{public['status']}`。",
        "",
        "本阶段实现了一个独立、Root-only 的 M3 仲裁器：每个 case 仅调用一次 Strong Single；当 Initial Root 不在 Metrics Top-2 且归一化分差不低于 0.25 时，确定性切换到 Metrics Top-1。Indicator 始终保留 Initial 值，Specialist 与 Fusion LLM 调用均为 0。",
        "",
        "三套冻结 fixture 的零 Provider 回放均精确复现：Final Root 都为 57，Root rescue 分别为 8、6、12，damage 都为 0。",
        "",
    ]
    if isinstance(tune, Mapping):
        lines.append(
            f"一次性 TUNE：完成 {tune['completed']}/60，Final Root {tune['final_root_correct']}/60，Final Pair {tune['final_pair_correct']}/60，Root net rescue {tune['same_run_root_net_rescue']:+d}，Gate={'通过' if tune['gate_passed'] else '未通过'}。"
        )
        lines.append("")
    else:
        lines.extend(("TUNE 尚未执行。", ""))
    if isinstance(regression, Mapping):
        lines.append(
            f"一次性 Regression：完成 {regression['completed']}/120，Final Root {regression['final_root_correct']}/120，Final Pair {regression['final_pair_correct']}/120，Root net rescue {regression['same_run_root_net_rescue']:+d}，Gate={'通过' if regression['gate_passed'] else '未通过'}。"
        )
        lines.append("")
    else:
        lines.extend(("Regression 尚未执行；只有 TUNE Gate 通过后才允许运行。", ""))
    lines.append(
        "结论边界：主要算法结论只使用同一 run 的 Initial→Final；历史 Strong Single 仅标记为 `CROSS_RUN_CONTEXTUAL_BASELINE`。这是已消费 OB/SS development 证据，不是外部验证；未访问 TT，也不主张生产泛化。"
    )
    return "\n".join(lines) + "\n"


def project_public_results(args: argparse.Namespace) -> int:
    _validate_fixture_replay(args.fixture_replay, require_tracked_path=True)
    fixture = _load_json(PUBLIC_REPLAY_PATH)
    phases: dict[str, Any] = {"preflight": None, "smoke": None, "tune": None, "regression": None}
    live_paths = (
        args.preflight_result,
        args.smoke_result,
        args.tune_result,
        args.regression_result,
    )
    implementation_sha = _clean_implementation_sha() if any(live_paths) else None
    lock = _config_snapshot() if any(live_paths) else None
    preflight_result_sha256: str | None = None
    if args.preflight_result is not None:
        assert implementation_sha is not None and lock is not None
        preflight = _load_bound_preflight(
            args.preflight_result, implementation_sha, lock
        )
        preflight_result_sha256 = _sha256(
            args.preflight_result.expanduser().resolve(strict=True)
        )
        phases["preflight"] = {
            "status": preflight["status"],
            "response_valid": preflight["response_valid"],
            "usage_known": preflight["usage_known"],
            "http_429": preflight["http_429"],
            "schema_error": preflight["schema_error"],
        }
    for phase, path in (
        ("smoke", args.smoke_result),
        ("tune", args.tune_result),
        ("regression", args.regression_result),
    ):
        if path is not None:
            assert implementation_sha is not None and lock is not None
            if preflight_result_sha256 is None:
                raise ValueError("public phase projection lacks bound preflight")
            phase_name = cast(
                Literal["smoke", "tune", "regression"], phase
            )
            phases[phase] = _public_phase(
                path,
                phase_name,
                implementation_sha=implementation_sha,
                lock=lock,
                preflight_result_sha256=preflight_result_sha256,
            )
    if phases["preflight"] is None and phases["smoke"] is not None:
        raise ValueError("public Smoke projection lacks preflight")
    if (
        phases["tune"] is not None
        and (
            not isinstance(phases["smoke"], Mapping)
            or phases["smoke"].get("gate_passed") is not True
        )
    ):
        raise ValueError("public TUNE projection lacks passing Smoke")
    if (
        phases["regression"] is not None
        and (
            not isinstance(phases["tune"], Mapping)
            or phases["tune"].get("gate_passed") is not True
        )
    ):
        raise ValueError("public Regression projection lacks passing TUNE")
    if args.smoke_result is not None and args.tune_result is not None:
        smoke_root = args.smoke_result.expanduser().resolve(strict=True).parent.parent
        tune_root = args.tune_result.expanduser().resolve(strict=True).parent.parent
        if smoke_root != tune_root:
            raise ValueError("public Smoke and TUNE roots differ")
    public = {
        "schema_version": "rcaeval-metrics-arbitration.public-development.v1",
        "evaluation_version": "metrics-arbitration-v1",
        "status": _development_status(phases),
        "decision_lineage": {
            "source": "PR_20_METRICS_ARBITRATION_DECISION",
            "frozen_decision_commit": "59ace4d",
        },
        "m3_rule": {
            "initial_rank_override_min_exclusive": 2,
            "normalized_margin_min": 0.25,
            "preserve_initial_indicator": True,
            "semantic_model_calls": 1,
            "specialist_calls": 0,
            "fusion_model_calls": 0,
        },
        "fixture_replay": fixture,
        "phases": phases,
        "historical_strong_single_context": {
            "classification": "CROSS_RUN_CONTEXTUAL_BASELINE",
            "tune": {"root_correct": 51, "pair_correct": 29, "scheduled": 60},
            "regression": {
                "root_correct": 99,
                "pair_correct": 55,
                "scheduled": 120,
            },
        },
        "primary_endpoint_authority": "SAME_RUN_INITIAL_TO_FINAL",
        "claim_boundary": [
            "CONSUMED_OBSS_DEVELOPMENT",
            "NOT_EXTERNAL_VALIDATION",
            "NOT_PRODUCTION_GENERALIZATION",
            "NO_TT_ACCESS",
        ],
    }
    assert_public_payload(public)
    _write_json(args.public_json, public, private=False)
    args.public_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.public_markdown.write_text(_results_markdown(public), encoding="utf-8")
    args.human_brief.parent.mkdir(parents=True, exist_ok=True)
    args.human_brief.write_text(_human_brief(public), encoding="utf-8")
    print(json.dumps({"status": public["status"]}, sort_keys=True), flush=True)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay = subparsers.add_parser("fixture-replay")
    replay.add_argument("--candidate-3-root", type=Path, required=True)
    replay.add_argument("--candidate-4-root", type=Path, required=True)
    replay.add_argument("--candidate-5-root", type=Path, required=True)
    replay.add_argument("--ob-root", type=Path, required=True)
    replay.add_argument("--ss-root", type=Path, required=True)
    replay.add_argument("--private-output", type=Path, required=True)
    replay.add_argument(
        "--public-json",
        type=Path,
        default=PROJECT_ROOT
        / "docs/analysis/rcaeval-metrics-arbitration-m3-replay.json",
    )
    replay.add_argument(
        "--public-markdown",
        type=Path,
        default=PROJECT_ROOT
        / "docs/analysis/rcaeval-metrics-arbitration-m3-replay.md",
    )
    replay.set_defaults(handler=run_fixture_replay)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument(
        "--fixture-replay", type=Path, default=PUBLIC_REPLAY_PATH
    )
    preflight.add_argument("--env-file", type=Path, required=True)
    preflight.add_argument("--run-root", type=Path, required=True)
    preflight.set_defaults(handler=run_preflight)

    development = subparsers.add_parser("development")
    development.add_argument(
        "--phase", choices=("smoke", "tune", "regression"), required=True
    )
    development.add_argument("--ob-root", type=Path, required=True)
    development.add_argument("--ss-root", type=Path, required=True)
    development.add_argument("--schedule", type=Path, required=True)
    development.add_argument("--env-file", type=Path, required=True)
    development.add_argument("--run-root", type=Path, required=True)
    development.add_argument("--preflight-result", type=Path, required=True)
    development.add_argument("--smoke-result", type=Path)
    development.add_argument("--tune-result", type=Path)
    development.set_defaults(handler=run_development)

    project = subparsers.add_parser("project-results")
    project.add_argument("--fixture-replay", type=Path, required=True)
    project.add_argument("--preflight-result", type=Path)
    project.add_argument("--smoke-result", type=Path)
    project.add_argument("--tune-result", type=Path)
    project.add_argument("--regression-result", type=Path)
    project.add_argument(
        "--public-json",
        type=Path,
        default=PROJECT_ROOT
        / "docs/results/rcaeval-metrics-arbitration-v1-development.json",
    )
    project.add_argument(
        "--public-markdown",
        type=Path,
        default=PROJECT_ROOT
        / "docs/results/rcaeval-metrics-arbitration-v1-development.md",
    )
    project.add_argument(
        "--human-brief",
        type=Path,
        default=PROJECT_ROOT
        / "docs/results/rcaeval-metrics-arbitration-v1-human-brief.md",
    )
    project.set_defaults(handler=project_public_results)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
