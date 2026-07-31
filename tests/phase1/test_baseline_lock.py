from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ecomsre.model.gateway as gateway_module
from ecomsre.phase1.contracts import RCAResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_LOCK_PATH = PROJECT_ROOT / "config/phase1/baseline-lock.json"
CURRENT_DISPOSITION_PATH = (
    PROJECT_ROOT
    / "docs/review-evidence/phase1-single-agent-replay-20260801"
    / "current-disposition.json"
)
HUMAN_BRIEF_PATH = (
    PROJECT_ROOT
    / "docs/human-briefs/2026-07-31-phase1-single-agent-rca-replay.html"
)
GROUND_TRUTH_PATHS = tuple(
    sorted(
        (PROJECT_ROOT / "eval/phase1/ground-truth").glob("*.json"),
        key=lambda path: path.as_posix(),
    )
)
MANIFEST_PATHS = tuple(
    sorted(
        (
            PROJECT_ROOT
            / "config/phase1/replay-cases/agent-visible"
        ).glob("*/manifest.json"),
        key=lambda path: path.as_posix(),
    )
)


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _composite_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(PROJECT_ROOT).as_posix()):
        relative_path = path.relative_to(PROJECT_ROOT).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def test_phase1_baseline_lock_is_strict_and_recomputable() -> None:
    assert BASELINE_LOCK_PATH.is_file(), "baseline lock must be tracked"
    lock = json.loads(BASELINE_LOCK_PATH.read_text(encoding="utf-8"))

    assert set(lock) == {
        "schema_version",
        "baseline_version",
        "hash_contract",
        "model",
        "budgets",
        "artifacts",
    }
    assert lock["schema_version"] == "phase1.baseline-lock.v1"
    assert lock["baseline_version"] == "phase1.single-agent-replay.v1"
    assert lock["hash_contract"] == {
        "algorithm": "sha256",
        "canonical_json": (
            "UTF-8 json.dumps with allow_nan=false, ensure_ascii=false, "
            "separators=(',', ':'), sort_keys=true"
        ),
        "composite_raw_files": (
            "For each POSIX relative path sorted lexicographically: "
            "uint64be(path_utf8_length) + path_utf8 + "
            "uint64be(content_length) + raw_content"
        ),
    }
    assert lock["model"] == {
        "snapshot": "gpt-5.4-mini-2026-03-17",
        "temperature": 0,
    }
    assert lock["budgets"] == {
        "max_model_calls": 8,
        "max_tool_calls": 8,
        "max_total_tokens": 12000,
    }
    agent_config = json.loads(
        (PROJECT_ROOT / "config/phase1/agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert lock["model"]["temperature"] == agent_config["temperature"]
    assert lock["budgets"] == {
        key: agent_config[key]
        for key in (
            "max_model_calls",
            "max_tool_calls",
            "max_total_tokens",
        )
    }

    artifacts = lock["artifacts"]
    assert set(artifacts) == {
        "prompt",
        "tool_schema",
        "rca_schema",
        "evaluator",
        "replay_suite",
        "validator",
    }
    assert artifacts["prompt"] == {
        "sha256": _sha256(
            gateway_module.PHASE1_SYSTEM_INSTRUCTION.encode("utf-8")
        ),
        "source_paths": [
            "src/ecomsre/model/gateway.py#PHASE1_SYSTEM_INSTRUCTION"
        ],
        "serialization": "exact_utf8_text",
    }
    assert artifacts["tool_schema"] == {
        "sha256": _sha256(
            _canonical_json(gateway_module._tool_definitions())
        ),
        "source_paths": ["src/ecomsre/model/gateway.py#_tool_definitions"],
        "serialization": "canonical_json",
    }
    assert artifacts["rca_schema"] == {
        "sha256": _sha256(_canonical_json(RCAResult.model_json_schema())),
        "source_paths": ["src/ecomsre/phase1/contracts.py#RCAResult"],
        "serialization": "canonical_json",
    }
    evaluator_paths = (
        PROJECT_ROOT / "eval/phase1/run.py",
        *GROUND_TRUTH_PATHS,
    )
    assert artifacts["evaluator"] == {
        "sha256": _composite_sha256(evaluator_paths),
        "source_paths": [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in evaluator_paths
        ],
        "serialization": "composite_raw_files",
    }
    replay_suite_paths = (*MANIFEST_PATHS, *GROUND_TRUTH_PATHS)
    replay_suite_paths = tuple(
        sorted(
            replay_suite_paths,
            key=lambda path: path.relative_to(PROJECT_ROOT).as_posix(),
        )
    )
    assert artifacts["replay_suite"] == {
        "version": "phase1.replay-suite.v1",
        "sha256": _composite_sha256(replay_suite_paths),
        "source_paths": [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in replay_suite_paths
        ],
        "serialization": "composite_raw_files",
    }
    validator_path = PROJECT_ROOT / "src/ecomsre/phase1/validator.py"
    assert artifacts["validator"] == {
        "sha256": _sha256(validator_path.read_bytes()),
        "source_paths": ["src/ecomsre/phase1/validator.py"],
        "serialization": "raw_file_bytes",
    }


def test_phase1_disposition_separates_scripted_suite_from_provider_gate() -> None:
    assert CURRENT_DISPOSITION_PATH.is_file(), (
        "machine-readable disposition must be tracked"
    )
    disposition = json.loads(
        CURRENT_DISPOSITION_PATH.read_text(encoding="utf-8")
    )

    assert set(disposition) == {
        "schema_version",
        "phase",
        "truth_marker",
        "offline_scripted_evaluation",
        "real_provider_gate",
        "baseline_lock_path",
    }
    assert disposition["schema_version"] == (
        "phase1.current-disposition.v1"
    )
    assert disposition["phase"] == "phase1-single-agent-rca-replay"
    assert disposition["truth_marker"] == (
        "PHASE1_SINGLE_AGENT_REPLAY_MVP_READY"
    )
    assert disposition["baseline_lock_path"] == (
        "config/phase1/baseline-lock.json"
    )
    assert disposition["offline_scripted_evaluation"] == {
        "status": "PASSED",
        "adapter": "scripted-replay-v1",
        "case_count": 7,
        "decision_accuracy": {"numerator": 7, "denominator": 7},
        "report_path": "artifacts/phase1/evaluation/evaluation-report.json",
    }
    assert disposition["real_provider_gate"] == {
        "status": "PASSED",
        "provider": "openai-compatible",
        "model_snapshot": "gpt-5.4-mini-2026-03-17",
        "case_count": 2,
        "cases": [
            {
                "case_id": "ad-partial-failure-complete",
                "decision": "RCA_CONFIRMED",
            },
            {
                "case_id": "no-real-incident",
                "decision": "ABSTAIN",
            },
        ],
        "report_path": (
            "artifacts/phase1/provider-smoke/provider-smoke-report.json"
        ),
    }


def test_phase1_human_brief_preserves_provider_claim_boundary() -> None:
    brief = HUMAN_BRIEF_PATH.read_text(encoding="utf-8")

    assert "PHASE1_SINGLE_AGENT_REPLAY_MVP_READY" in brief
    assert "scripted adapter 冻结七案例：Decision Accuracy 7 / 7" in brief
    assert "真实 provider gate 仅运行两个指定案例，不是七案例真实模型准确率" in brief
    assert "ad-partial-failure-complete" in brief
    assert "no-real-incident" in brief
    assert "gpt-5.4-mini-2026-03-17" in brief
    assert "baseline-lock.json" in brief
    assert "current-disposition.json" in brief
    assert "未配置时仍返回 <code>SKIPPED_NOT_CONFIGURED</code>" in brief
    assert "本次没有配置或调用真实" not in brief
    assert "真实 gate 未运行" not in brief
    assert "不能声称真实模型质量、真实 provider 可用性" not in brief
