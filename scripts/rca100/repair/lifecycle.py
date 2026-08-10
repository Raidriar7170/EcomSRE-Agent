"""Post-lock evaluator-only RCA100 repair lifecycle and frozen scoring."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Literal, Mapping

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_file
from ecomsre_rca100.entity import EntityCatalog, load_entity_catalog
from ecomsre_rca100.evaluator import (
    RCA100CaseScore,
    RCA100GroundTruth,
    evaluate_terminals,
    load_answer_key,
)
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    RCA100Schedule,
    create_once_json,
    current_state,
    load_strict_json,
    schedule_sha256,
    tree_sha256,
    verify_tree_binding,
)
from ecomsre_rca100.runner import RCA100TerminalRecord


REPAIR_PROTOCOL_ID = "rca100-metrics-arbitration-v1-evaluator-repair.1"
ORIGINAL_PROTOCOL_ID = "rca100-metrics-arbitration-v1"
ORIGINAL_PR22_HEAD = "7a0c22fa82a967730e238ac666f565cd935014ee"
ORIGINAL_PR22_BRANCH = "external/rca100-metrics-arbitration-v1"
ORIGINAL_PROTOCOL_FREEZE_SHA256 = (
    "7d4684825216f4791d8dae4061bca95995381928beba6f504865854468ca5011"
)
ORIGINAL_TERMINAL_LOCK_SHA256 = (
    "9cec58225baa45064123826d7f247c4ac52e57215e75ca3b026b08a0ed8b6afe"
)
ORIGINAL_TERMINAL_TREE_SHA256 = (
    "a404226b0f79ac34887997bec230e8a1736cd16595299e68a8f166439eb8762c"
)
ORIGINAL_ATTEMPT_TREE_SHA256 = (
    "4f05d296ec3b66848d4d50b5222db7e67e6aea2ef9c52d46a5de19adfbeb9e7b"
)
ORIGINAL_PROVIDER_TREE_SHA256 = (
    "a3988a7719ccaeee80ba708592f4674f3a2bcd8382f0557ad5579bb93b5b67fe"
)
ORIGINAL_SCHEDULE_SHA256 = (
    "00604fa3157edde3597a7ef6758637be06a099051181d921cc35a7f305c4459e"
)
ORIGINAL_INPUT_TREE_SHA256 = (
    "aca130e350330000e0d9bc575606e3a5378178b6d7e0c2afb5cf13910596fea9"
)
ORIGINAL_NO_LABEL_AUDIT_SHA256 = (
    "5635babf2fcdd7dcb24711ba9b4f5da10739719f69d16f355c7827b7a0d9dc1b"
)
SOURCE_REPOSITORY = (
    "https://www.aiops.cn/gitlab/aiops-live-benchmark/agenticopseval.git"
)
SOURCE_COMMIT = "fd92cae17e6e14fa3ed0f3963c31838151fbdaa7"
SOURCE_SNAPSHOT_TREE_SHA256 = (
    "0d491f594c583ba1cb0e235222b59983b930c192af00b80c6a4caae87dda1551"
)
SOURCE_SNAPSHOT_MAPPING_SHA256 = (
    "e331cab32d8c6c0c1e7d5fbf98d6f97d05946a956cd1b22e00ec9b3562dc4fdb"
)
SOURCE_SNAPSHOT_TASK_KEY_SHA256 = (
    "74d39edf1fd1137209f7200a2be124ca25327831ec7ed890ced132933522eefc"
)
SOURCE_SNAPSHOT_LOCK_SHA256 = (
    "9d8295133ae7eab319258b5122d4f7601ac7159f010a3f2a91cd705947170e5b"
)
DECISION_RECORD_SHA256 = (
    "55198522fd18a3165f696ce845f956249b900d07635e9ee5a70d41198310e24c"
)
DECISION_LOCK_SHA256 = (
    "00fafe130073c89a88efa87fcf90dedcdb407835f2dc2328151e78f86885d4b4"
)
FROZEN_MAPPING_TOP_LEVEL_TYPES = {
    "case_id_to_task": "object",
    "seed": "integer",
    "task_to_case_id": "object",
    "version": "string",
}
_PROVIDER_CREDENTIALS = (
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)
_IMPLEMENTATION_PATHS = frozenset(
    {
        "config/rca100-metrics-arbitration-v1-evaluator-repair.1/protocol.json",
        "docs/decisions/rca100-metrics-arbitration-v1-evaluator-repair-decision.md",
        "docs/external-benchmarks/rca100-metrics-arbitration-v1-evaluator-repair-protocol.md",
        "scripts/rca100/acquire_answer_key.py",
        "scripts/rca100/build_report.py",
        "scripts/rca100/repair/__init__.py",
        "scripts/rca100/repair/build_report.py",
        "scripts/rca100/repair/lifecycle.py",
        "scripts/rca100/repair/verify_report.py",
        "scripts/rca100/verify_report.py",
        "src/ecomsre_rca100/evaluator.py",
        "tests/benchmarks/rca100/test_evaluator.py",
        "tests/benchmarks/rca100/test_evaluator_repair.py",
    }
)
PUBLIC_RESULT_PATHS = frozenset(
    {
        "docs/results/rca100-metrics-arbitration-v1-final.json",
        "docs/results/rca100-metrics-arbitration-v1-final.md",
        "docs/results/rca100-metrics-arbitration-v1-human-brief.md",
        "docs/review-evidence/rca100-metrics-arbitration-v1/evaluator-repair-disposition.json",
        "docs/review-evidence/rca100-metrics-arbitration-v1/execution-integrity.json",
        "docs/review-evidence/rca100-metrics-arbitration-v1/final-report-verification.json",
        "docs/review-evidence/rca100-metrics-arbitration-v1/human-review-checklist.md",
    }
)


REPAIR_STATE_CHAIN = (
    "REPAIR_SOURCE_SNAPSHOT_LOCKED",
    "REPAIR_DECISION_FROZEN",
    "REPAIR_IMPLEMENTATION_FROZEN",
    "REPAIR_ANSWER_KEY_LOCKED",
    "REPAIR_SCORED",
    "REPAIR_FINAL_REPORT_FROZEN",
)
RepairStateName = Literal[
    "REPAIR_SOURCE_SNAPSHOT_LOCKED",
    "REPAIR_DECISION_FROZEN",
    "REPAIR_IMPLEMENTATION_FROZEN",
    "REPAIR_ANSWER_KEY_LOCKED",
    "REPAIR_SCORED",
    "REPAIR_FINAL_REPORT_FROZEN",
]


@dataclass(frozen=True, slots=True)
class RepairEnvironment:
    roots: PrivateRoots
    repair_control: Path
    repository_root: Path

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
        *,
        repository_root: Path,
    ) -> RepairEnvironment:
        roots = PrivateRoots.from_environment(environment)
        roots.validate(repository_root=repository_root, create=False)
        value = environment.get("RCA100_EVALUATOR_REPAIR_CONTROL_ROOT")
        if not value:
            raise ValueError("missing repair control root")
        repair_control = Path(value)
        if not repair_control.is_absolute():
            raise ValueError("repair control root must be absolute")
        if repair_control.is_symlink() or not repair_control.is_dir():
            raise ValueError("repair control root must be a non-symlink directory")
        repo = repository_root.resolve()
        candidate = repair_control.resolve()
        if (
            candidate == repo
            or repo in candidate.parents
            or candidate in repo.parents
        ):
            raise ValueError("repair control root overlaps repository")
        original_roots = (
            roots.input_source,
            roots.control,
            roots.schedule,
            roots.journal,
            roots.output,
            roots.evaluator_source,
            roots.evaluator,
        )
        for original in original_roots:
            resolved = original.resolve()
            if (
                candidate == resolved
                or candidate in resolved.parents
                or resolved in candidate.parents
            ):
                raise ValueError("repair control root overlaps original private roots")
        return cls(
            roots=roots,
            repair_control=repair_control,
            repository_root=repository_root,
        )


@dataclass(frozen=True, slots=True)
class OriginalExecution:
    protocol_lock: Mapping[str, object]
    terminal_lock: Mapping[str, object]
    schedule: RCA100Schedule
    terminals: dict[str, RCA100TerminalRecord]


@dataclass(frozen=True, slots=True)
class RepairEvaluationInputs:
    original: OriginalExecution
    implementation_lock: Mapping[str, object]
    answer_lock: Mapping[str, object]
    truths: dict[str, RCA100GroundTruth]
    catalogs: dict[str, EntityCatalog]
    alert_entity_types: dict[str, str]


def _mapping(path: Path, *, label: str) -> Mapping[str, object]:
    value = load_strict_json(path)
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    return value


def _git_output(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_no_provider_credentials(environment: Mapping[str, str]) -> None:
    if any(name in environment for name in _PROVIDER_CREDENTIALS):
        raise ValueError("Provider credentials remained during evaluator repair")


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    raise ValueError("unsupported JSON type")


def _mapping_structure(answer_root: Path) -> dict[str, object]:
    mapping_path = answer_root / "mapping.json"
    envelope = load_strict_json(mapping_path)
    if not isinstance(envelope, Mapping):
        raise ValueError("BLOCKED_UNEXPECTED_OFFICIAL_MAPPING_ENVELOPE")
    top_level_keys = sorted(envelope)
    top_level_types = {
        key: _json_type(envelope[key]) for key in top_level_keys
    }
    if top_level_types != FROZEN_MAPPING_TOP_LEVEL_TYPES:
        raise ValueError("BLOCKED_UNEXPECTED_OFFICIAL_MAPPING_ENVELOPE")
    task_mapping = envelope.get("task_to_case_id")
    if not isinstance(task_mapping, Mapping):
        raise ValueError("BLOCKED_UNEXPECTED_OFFICIAL_MAPPING_ENVELOPE")
    expected_tasks = [f"t{index:03d}" for index in range(1, 104)]
    if sorted(task_mapping) != expected_tasks:
        raise ValueError("BLOCKED_UNEXPECTED_OFFICIAL_MAPPING_ENVELOPE")
    if not all(
        isinstance(value, str) and bool(value.strip())
        for value in task_mapping.values()
    ):
        raise ValueError("BLOCKED_UNEXPECTED_OFFICIAL_MAPPING_ENVELOPE")
    return {
        "top_level_keys": top_level_keys,
        "top_level_json_types": top_level_types,
        "task_to_case_id_present": True,
        "task_to_case_id_json_type": "object",
        "task_to_case_id_entry_count": len(task_mapping),
        "task_key_set_algorithm": "CANONICAL_JSON_SORTED_TASK_KEYS_V1",
        "task_key_set_sha256": hashlib.sha256(
            canonical_json_bytes(expected_tasks)
        ).hexdigest(),
        "task_value_json_type": "string",
        "task_values_non_empty": True,
    }


def _require_repair_state_at_least(
    control_root: Path,
    expected: RepairStateName,
) -> None:
    observed = current_repair_state(control_root)
    if observed is None or REPAIR_STATE_CHAIN.index(observed) < REPAIR_STATE_CHAIN.index(
        expected
    ):
        raise ValueError(f"evaluator repair requires {expected}")


def _require_state_lock_binding(
    control_root: Path,
    *,
    state: RepairStateName,
    field: str,
    lock_path: Path,
) -> None:
    record = _mapping(control_root / "state" / f"{state}.json", label=state)
    if record.get(field) != sha256_file(lock_path):
        raise ValueError(f"{state} does not bind its create-once lock")


def verify_source_snapshot(repair: RepairEnvironment) -> Mapping[str, object]:
    _require_repair_state_at_least(
        repair.repair_control, "REPAIR_SOURCE_SNAPSHOT_LOCKED"
    )
    lock_path = (
        repair.repair_control
        / "locks"
        / "answer-key-source-snapshot-lock.json"
    )
    if sha256_file(lock_path) != SOURCE_SNAPSHOT_LOCK_SHA256:
        raise ValueError("BLOCKED_ANSWER_KEY_SOURCE_DRIFT")
    _require_state_lock_binding(
        repair.repair_control,
        state="REPAIR_SOURCE_SNAPSHOT_LOCKED",
        field="source_snapshot_lock_sha256",
        lock_path=lock_path,
    )
    lock = _mapping(lock_path, label="answer-key source snapshot lock")
    source = repair.roots.evaluator_source
    if (
        _git_output(source, "rev-parse", "HEAD") != SOURCE_COMMIT
        or _git_output(source, "remote", "get-url", "origin")
        != SOURCE_REPOSITORY
    ):
        raise ValueError("BLOCKED_ANSWER_KEY_SOURCE_DRIFT")
    answer_root = source / "RCA100" / "answer_key"
    observed_tree, observed_files = tree_sha256(answer_root)
    gt_names = sorted(
        path.name
        for path in answer_root.glob("t[0-9][0-9][0-9].gt.json")
        if path.is_file() and not path.is_symlink()
    )
    expected_gt = [f"t{index:03d}.gt.json" for index in range(1, 104)]
    structure = _mapping_structure(answer_root)
    if (
        observed_tree != SOURCE_SNAPSHOT_TREE_SHA256
        or observed_files != 105
        or gt_names != expected_gt
        or sha256_file(answer_root / "mapping.json")
        != SOURCE_SNAPSHOT_MAPPING_SHA256
        or structure.get("task_key_set_sha256")
        != SOURCE_SNAPSHOT_TASK_KEY_SHA256
        or lock.get("answer_key_tree_sha256") != observed_tree
        or lock.get("answer_key_file_count") != observed_files
        or lock.get("mapping_json_sha256") != SOURCE_SNAPSHOT_MAPPING_SHA256
        or lock.get("ground_truth_file_count") != 103
        or lock.get("mapping_envelope_schema") != structure
        or lock.get("provider_calls") != 0
    ):
        raise ValueError("BLOCKED_ANSWER_KEY_SOURCE_DRIFT")
    return lock


def verify_original_execution(repair: RepairEnvironment) -> OriginalExecution:
    roots = repair.roots
    if current_state(roots.control) != "TERMINAL_RECORDS_LOCKED":
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    protocol_path = roots.control / "locks" / "protocol-freeze.json"
    terminal_path = roots.control / "locks" / "terminal-records-lock.json"
    if (
        sha256_file(protocol_path) != ORIGINAL_PROTOCOL_FREEZE_SHA256
        or sha256_file(terminal_path) != ORIGINAL_TERMINAL_LOCK_SHA256
    ):
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    protocol_lock = _mapping(protocol_path, label="original protocol freeze")
    terminal_lock = _mapping(terminal_path, label="original terminal lock")
    if (
        protocol_lock.get("implementation_commit") != ORIGINAL_PR22_HEAD
        or protocol_lock.get("schedule_sha256") != ORIGINAL_SCHEDULE_SHA256
        or terminal_lock.get("terminal_tree_sha256")
        != ORIGINAL_TERMINAL_TREE_SHA256
        or terminal_lock.get("attempt_tree_sha256")
        != ORIGINAL_ATTEMPT_TREE_SHA256
        or terminal_lock.get("provider_attempt_tree_sha256")
        != ORIGINAL_PROVIDER_TREE_SHA256
        or terminal_lock.get("terminal_records") != 103
        or terminal_lock.get("run_attempts") != 103
        or terminal_lock.get("duplicate_run_ids") != 0
    ):
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    terminal_state = _mapping(
        roots.control / "state" / "TERMINAL_RECORDS_LOCKED.json",
        label="original terminal state",
    )
    if terminal_state.get("terminal_records_lock_sha256") != sha256_file(
        terminal_path
    ):
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    verify_tree_binding(
        roots.output / "terminals",
        expected_sha256=ORIGINAL_TERMINAL_TREE_SHA256,
        expected_file_count=103,
        label="original terminal record",
    )
    verify_tree_binding(
        roots.journal / "run-attempts",
        expected_sha256=ORIGINAL_ATTEMPT_TREE_SHA256,
        expected_file_count=103,
        label="original run attempt",
    )
    verify_tree_binding(
        roots.journal / "runs",
        expected_sha256=ORIGINAL_PROVIDER_TREE_SHA256,
        expected_file_count=215,
        label="original Provider sidecar",
    )
    if (
        _git_output(roots.input_source, "rev-parse", "HEAD") != SOURCE_COMMIT
        or _git_output(roots.input_source, "remote", "get-url", "origin")
        != SOURCE_REPOSITORY
        or _git_output(roots.evaluator_source, "rev-parse", "HEAD")
        != SOURCE_COMMIT
        or (roots.input_source / "RCA100" / "answer_key").exists()
    ):
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    verify_tree_binding(
        roots.input_source / "RCA100" / "cases",
        expected_sha256=ORIGINAL_INPUT_TREE_SHA256,
        expected_file_count=721,
        label="original label-blind input",
    )
    if (
        sha256_file(roots.control / "audit" / "no-label-schema-audit.json")
        != ORIGINAL_NO_LABEL_AUDIT_SHA256
    ):
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    config_tree, _ = tree_sha256(
        repair.repository_root / "config" / ORIGINAL_PROTOCOL_ID
    )
    if protocol_lock.get("config_tree_sha256") != config_tree:
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    schedule = RCA100Schedule.model_validate_json(
        (roots.schedule / "schedule.json").read_text(encoding="utf-8")
    )
    if (
        len(schedule.records) != 103
        or schedule_sha256(schedule) != ORIGINAL_SCHEDULE_SHA256
        or len({item.run_id for item in schedule.records}) != 103
    ):
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    terminals = {
        terminal.opaque_case_id: terminal
        for path in sorted((roots.output / "terminals").glob("*.json"))
        if (
            terminal := RCA100TerminalRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        )
    }
    if (
        len(terminals) != 103
        or len({item.run_id for item in terminals.values()}) != 103
    ):
        raise ValueError("BLOCKED_ORIGINAL_EXECUTION_EVIDENCE_DRIFT")
    return OriginalExecution(
        protocol_lock=protocol_lock,
        terminal_lock=terminal_lock,
        schedule=schedule,
        terminals=terminals,
    )


_FROZEN_FUNCTIONS: Mapping[str, tuple[str, ...]] = {
    "scripts/rca100/acquire_answer_key.py": ("_repository_root", "main"),
    "scripts/rca100/build_report.py": (
        "_repository_root",
        "_write_once_text",
        "_execution_summary",
        "_public_report",
        "_markdown",
        "_human_brief",
        "_current_disposition",
        "_source_lock_public",
        "_execution_integrity_public",
        "main",
    ),
    "scripts/rca100/verify_report.py": ("_mapping", "_written_text", "main"),
    "src/ecomsre_rca100/evaluator.py": (
        "parse_ground_truth",
        "prediction_correct",
        "fault_correct",
        "evaluate_terminals",
    ),
    "src/ecomsre_rca100/statistics.py": (
        "exact_mcnemar_p_value",
        "paired_inference",
    ),
    "src/ecomsre_rcaeval_adaptive/metrics_arbitration.py": (
        "decide_metrics_arbitration",
        "arbitrate_diagnosis",
    ),
    "src/ecomsre_rca100/runner.py": (
        "execute_case",
        "execute_schedule",
    ),
    "src/ecomsre_rca100/prompt.py": ("build_request_payload",),
}


def _function_source_hashes(source: str, names: Sequence[str]) -> dict[str, str]:
    lines = source.splitlines(keepends=True)
    nodes = {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    output: dict[str, str] = {}
    for name in names:
        node = nodes.get(name)
        if node is None or node.end_lineno is None:
            raise ValueError(f"frozen function is missing: {name}")
        content = "".join(lines[node.lineno - 1 : node.end_lineno]).encode()
        output[name] = hashlib.sha256(content).hexdigest()
    return output


def _git_function_hashes(
    repository: Path,
    revision: str,
) -> dict[str, str]:
    output: dict[str, str] = {}
    for path, names in _FROZEN_FUNCTIONS.items():
        source = _git_output(repository, "show", f"{revision}:{path}") + "\n"
        for name, digest in _function_source_hashes(source, names).items():
            output[f"{path}:{name}"] = digest
    return output


def _git_changed_paths(repository: Path, revision: str) -> set[str]:
    output = _git_output(repository, "diff", "--name-only", f"{revision}..HEAD")
    return {line for line in output.splitlines() if line}


def _git_status_paths(repository: Path) -> set[str]:
    status = _git_output(repository, "status", "--porcelain=v1", "-uall")
    return {
        line[3:].split(" -> ")[-1]
        for line in status.splitlines()
        if line
    }


def _protected_file_hashes(
    repository: Path,
    paths: Sequence[str],
) -> dict[str, str]:
    return {path: sha256_file(repository / path) for path in sorted(paths)}


def freeze_implementation(
    repair: RepairEnvironment,
    *,
    reviewer_verdict: str,
    ci_reference: str,
) -> Mapping[str, object]:
    _require_no_provider_credentials(os.environ)
    if current_repair_state(repair.repair_control) != "REPAIR_DECISION_FROZEN":
        raise ValueError("implementation freeze requires REPAIR_DECISION_FROZEN")
    if reviewer_verdict != "PASS_EVALUATOR_ENVELOPE_ONLY_REPAIR":
        raise ValueError("evaluator repair reviewer verdict did not pass")
    if not ci_reference.strip():
        raise ValueError("evaluator repair implementation freeze requires CI evidence")
    if _git_status_paths(repair.repository_root):
        raise ValueError("evaluator repair implementation worktree is dirty")
    if (
        _git_output(
            repair.repository_root, "rev-parse", ORIGINAL_PR22_BRANCH
        )
        != ORIGINAL_PR22_HEAD
    ):
        raise ValueError("BLOCKED_PR22_HEAD_DRIFT")
    verify_source_snapshot(repair)
    verify_original_execution(repair)
    changed_paths = _git_changed_paths(repair.repository_root, ORIGINAL_PR22_HEAD)
    if not changed_paths or changed_paths - _IMPLEMENTATION_PATHS:
        raise ValueError("BLOCKED_EVALUATOR_REPAIR_SCOPE_EXCEEDED")
    required_paths = {
        "src/ecomsre_rca100/evaluator.py",
        "tests/benchmarks/rca100/test_evaluator.py",
        "tests/benchmarks/rca100/test_evaluator_repair.py",
        "docs/decisions/rca100-metrics-arbitration-v1-evaluator-repair-decision.md",
        "scripts/rca100/acquire_answer_key.py",
        "scripts/rca100/build_report.py",
        "scripts/rca100/repair/__init__.py",
        "scripts/rca100/repair/build_report.py",
        "scripts/rca100/repair/lifecycle.py",
        "scripts/rca100/repair/verify_report.py",
        "scripts/rca100/verify_report.py",
        "config/rca100-metrics-arbitration-v1-evaluator-repair.1/protocol.json",
        "docs/external-benchmarks/rca100-metrics-arbitration-v1-evaluator-repair-protocol.md",
    }
    if not required_paths.issubset(changed_paths):
        raise ValueError("evaluator repair implementation surface is incomplete")
    decision_path = (
        repair.repository_root
        / "docs"
        / "decisions"
        / "rca100-metrics-arbitration-v1-evaluator-repair-decision.md"
    )
    decision_lock_path = (
        repair.repair_control / "locks" / "evaluator-repair-decision-lock.json"
    )
    if (
        sha256_file(decision_path) != DECISION_RECORD_SHA256
        or sha256_file(decision_lock_path) != DECISION_LOCK_SHA256
    ):
        raise ValueError("evaluator repair Decision Record drifted")
    _require_state_lock_binding(
        repair.repair_control,
        state="REPAIR_DECISION_FROZEN",
        field="decision_lock_sha256",
        lock_path=decision_lock_path,
    )
    base_hashes = _git_function_hashes(
        repair.repository_root, ORIGINAL_PR22_HEAD
    )
    implementation_commit = _git_output(repair.repository_root, "rev-parse", "HEAD")
    current_hashes = _git_function_hashes(
        repair.repository_root, implementation_commit
    )
    if base_hashes != current_hashes:
        raise ValueError("BLOCKED_EVALUATOR_REPAIR_SCOPE_EXCEEDED")
    protected_hashes = _protected_file_hashes(
        repair.repository_root, tuple(changed_paths)
    )
    evaluator_sha = sha256_file(
        repair.repository_root / "src" / "ecomsre_rca100" / "evaluator.py"
    )
    test_sha = sha256_file(
        repair.repository_root
        / "tests"
        / "benchmarks"
        / "rca100"
        / "test_evaluator.py"
    )
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lock = {
        "schema_version": "rca100.evaluator-repair-implementation-lock.v1",
        "repair_protocol_id": REPAIR_PROTOCOL_ID,
        "repair_branch": _git_output(
            repair.repository_root, "branch", "--show-current"
        ),
        "repair_implementation_commit": implementation_commit,
        "original_pr22_head": ORIGINAL_PR22_HEAD,
        "original_protocol_freeze_sha256": ORIGINAL_PROTOCOL_FREEZE_SHA256,
        "original_terminal_tree_sha256": ORIGINAL_TERMINAL_TREE_SHA256,
        "original_attempt_tree_sha256": ORIGINAL_ATTEMPT_TREE_SHA256,
        "original_provider_sidecar_tree_sha256": ORIGINAL_PROVIDER_TREE_SHA256,
        "original_no_label_audit_sha256": ORIGINAL_NO_LABEL_AUDIT_SHA256,
        "answer_key_source_snapshot_lock_sha256": SOURCE_SNAPSHOT_LOCK_SHA256,
        "mapping_structural_schema": {
            "top_level_json_types": FROZEN_MAPPING_TOP_LEVEL_TYPES,
            "task_to_case_id_entries": 103,
            "task_key_set_sha256": SOURCE_SNAPSHOT_TASK_KEY_SHA256,
        },
        "decision_record_sha256": DECISION_RECORD_SHA256,
        "decision_lock_sha256": DECISION_LOCK_SHA256,
        "evaluator_py_sha256": evaluator_sha,
        "evaluator_test_fixture_sha256": test_sha,
        "unchanged_scorer_function_sha256": current_hashes,
        "protected_file_sha256": protected_hashes,
        "changed_paths": sorted(changed_paths),
        "reviewer_verdict": reviewer_verdict,
        "ci_reference": ci_reference,
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "created_at_utc": now,
    }
    lock_path = (
        repair.repair_control
        / "locks"
        / "evaluator-repair-implementation-lock.json"
    )
    lock_sha = create_once_json(lock_path, lock)
    advance_repair_state(
        repair.repair_control,
        "REPAIR_IMPLEMENTATION_FROZEN",
        bindings={
            "implementation_lock_sha256": lock_sha,
            "repair_implementation_commit": implementation_commit,
            "provider_calls": 0,
        },
    )
    return {**lock, "implementation_lock_sha256": lock_sha}


def verify_repair_implementation(
    repair: RepairEnvironment,
) -> Mapping[str, object]:
    _require_repair_state_at_least(
        repair.repair_control, "REPAIR_IMPLEMENTATION_FROZEN"
    )
    lock_path = (
        repair.repair_control
        / "locks"
        / "evaluator-repair-implementation-lock.json"
    )
    _require_state_lock_binding(
        repair.repair_control,
        state="REPAIR_IMPLEMENTATION_FROZEN",
        field="implementation_lock_sha256",
        lock_path=lock_path,
    )
    lock = _mapping(lock_path, label="evaluator repair implementation lock")
    implementation_commit = lock.get("repair_implementation_commit")
    if not isinstance(implementation_commit, str):
        raise ValueError("evaluator repair implementation commit is missing")
    if _git_output(repair.repository_root, "rev-parse", "HEAD") != implementation_commit:
        raise ValueError("evaluator repair implementation HEAD drifted")
    unexpected = _git_status_paths(repair.repository_root) - PUBLIC_RESULT_PATHS
    if unexpected:
        raise ValueError(f"evaluator repair protected paths changed: {sorted(unexpected)}")
    protected = lock.get("protected_file_sha256")
    if not isinstance(protected, Mapping):
        raise ValueError("evaluator repair protected file binding is invalid")
    for path, expected in protected.items():
        if (
            not isinstance(path, str)
            or not isinstance(expected, str)
            or sha256_file(repair.repository_root / path) != expected
        ):
            raise ValueError("evaluator repair protected file drifted")
    function_hashes = lock.get("unchanged_scorer_function_sha256")
    if not isinstance(function_hashes, Mapping):
        raise ValueError("evaluator repair function binding is invalid")
    if dict(function_hashes) != _git_function_hashes(
        repair.repository_root, implementation_commit
    ):
        raise ValueError("evaluator repair scorer function drifted")
    return lock


def lock_answer_key(repair: RepairEnvironment) -> Mapping[str, object]:
    _require_no_provider_credentials(os.environ)
    if current_repair_state(repair.repair_control) != "REPAIR_IMPLEMENTATION_FROZEN":
        raise ValueError("answer lock requires REPAIR_IMPLEMENTATION_FROZEN")
    snapshot = verify_source_snapshot(repair)
    original = verify_original_execution(repair)
    implementation = verify_repair_implementation(repair)
    answer_root = repair.roots.evaluator_source / "RCA100" / "answer_key"
    truths = load_answer_key(answer_root)
    expected_tasks = {f"t{index:03d}" for index in range(1, 104)}
    if (
        set(truths) != expected_tasks
        or any(
            not truth.canonical_case_id
            or (not truth.target_entity_ids and not truth.target_entity_names)
            or not truth.fault_types
            for truth in truths.values()
        )
    ):
        raise ValueError("BLOCKED_EVALUATOR_REPAIR_SCOPE_EXCEEDED")
    tree_hash, file_count = tree_sha256(answer_root)
    structure = _mapping_structure(answer_root)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lock = {
        "schema_version": "rca100.evaluator-repair-answer-key-lock.v1",
        "repair_protocol_id": REPAIR_PROTOCOL_ID,
        "answer_key_source_tree_sha256": tree_hash,
        "answer_key_file_count": file_count,
        "mapping_json_sha256": sha256_file(answer_root / "mapping.json"),
        "task_key_set_sha256": structure["task_key_set_sha256"],
        "ground_truth_file_count": 103,
        "loaded_truth_count": len(truths),
        "source_commit": SOURCE_COMMIT,
        "repair_implementation_commit": implementation[
            "repair_implementation_commit"
        ],
        "source_snapshot_lock_sha256": sha256_file(
            repair.repair_control
            / "locks"
            / "answer-key-source-snapshot-lock.json"
        ),
        "original_terminal_lock_sha256": ORIGINAL_TERMINAL_LOCK_SHA256,
        "original_terminal_tree_sha256": original.terminal_lock[
            "terminal_tree_sha256"
        ],
        "mapping_envelope_contract": "EXACT_FROZEN_TASK_TO_CASE_ID_V1",
        "provider_credentials_present": False,
        "provider_calls": 0,
        "created_at_utc": now,
    }
    if (
        lock["answer_key_source_tree_sha256"]
        != snapshot["answer_key_tree_sha256"]
    ):
        raise ValueError("BLOCKED_ANSWER_KEY_SOURCE_DRIFT")
    lock_path = repair.repair_control / "locks" / "answer-key-lock.json"
    lock_sha = create_once_json(lock_path, lock)
    advance_repair_state(
        repair.repair_control,
        "REPAIR_ANSWER_KEY_LOCKED",
        bindings={
            "answer_key_lock_sha256": lock_sha,
            "loaded_truth_count": 103,
            "provider_calls": 0,
        },
    )
    return {**lock, "answer_key_lock_sha256": lock_sha}


def _alert_entity_type(task_path: Path) -> str:
    task = _mapping(task_path, label="RCA100 agent task")
    alert = task.get("alert_entity")
    if not isinstance(alert, Mapping):
        return "UNAVAILABLE"
    value = alert.get("entity_type")
    return value.strip() if isinstance(value, str) and value.strip() else "UNAVAILABLE"


def load_repair_evaluation_inputs(
    repair: RepairEnvironment,
) -> RepairEvaluationInputs:
    _require_no_provider_credentials(os.environ)
    _require_repair_state_at_least(
        repair.repair_control, "REPAIR_ANSWER_KEY_LOCKED"
    )
    verify_source_snapshot(repair)
    original = verify_original_execution(repair)
    implementation = verify_repair_implementation(repair)
    answer_path = repair.repair_control / "locks" / "answer-key-lock.json"
    _require_state_lock_binding(
        repair.repair_control,
        state="REPAIR_ANSWER_KEY_LOCKED",
        field="answer_key_lock_sha256",
        lock_path=answer_path,
    )
    answer_lock = _mapping(answer_path, label="repair answer-key lock")
    answer_root = repair.roots.evaluator_source / "RCA100" / "answer_key"
    verify_tree_binding(
        answer_root,
        expected_sha256=SOURCE_SNAPSHOT_TREE_SHA256,
        expected_file_count=105,
        label="repair answer key",
    )
    if (
        answer_lock.get("repair_implementation_commit")
        != implementation.get("repair_implementation_commit")
        or answer_lock.get("original_terminal_lock_sha256")
        != ORIGINAL_TERMINAL_LOCK_SHA256
        or answer_lock.get("provider_calls") != 0
    ):
        raise ValueError("repair answer-key lock binding differs")
    truths = load_answer_key(answer_root)
    catalogs: dict[str, EntityCatalog] = {}
    alert_entity_types: dict[str, str] = {}
    for record in original.schedule.records:
        case_root = (
            repair.roots.input_source
            / "RCA100"
            / "cases"
            / record.source_task_id
        )
        catalogs[record.source_task_id] = load_entity_catalog(
            case_root / "topology.json"
        )
        alert_entity_types[record.source_task_id] = _alert_entity_type(
            case_root / "task.json"
        )
    return RepairEvaluationInputs(
        original=original,
        implementation_lock=implementation,
        answer_lock=answer_lock,
        truths=truths,
        catalogs=catalogs,
        alert_entity_types=alert_entity_types,
    )


def case_score_vector_sha256(scores: Sequence[RCA100CaseScore]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            [
                {
                    "completed": score.completed,
                    "initial_root_correct": score.initial_root_correct,
                    "final_root_correct": score.final_root_correct,
                    "initial_pair_correct": score.initial_pair_correct,
                    "final_pair_correct": score.final_pair_correct,
                    "m3_action": score.m3_action,
                    "metrics_projection_status": score.metrics_projection_status,
                }
                for score in scores
            ]
        )
    ).hexdigest()


def score_frozen_terminals(repair: RepairEnvironment) -> Mapping[str, object]:
    if current_repair_state(repair.repair_control) != "REPAIR_ANSWER_KEY_LOCKED":
        raise ValueError("scoring requires REPAIR_ANSWER_KEY_LOCKED")
    inputs = load_repair_evaluation_inputs(repair)
    aggregate, scores = evaluate_terminals(
        schedule=inputs.original.schedule,
        terminals=inputs.original.terminals,
        truths=inputs.truths,
        catalogs=inputs.catalogs,
        alert_entity_types=inputs.alert_entity_types,
    )
    if len(scores) != 103 or aggregate.get("fixed_denominator") != 103:
        raise ValueError("evaluator repair scoring denominator differs")
    result_root = repair.repair_control / "results"
    aggregate_sha = create_once_json(result_root / "aggregate.json", aggregate)
    case_scores = {
        "schema_version": "rca100.evaluator-repair-private-case-scores.v1",
        "records": [score.model_dump(mode="json") for score in scores],
    }
    case_scores_sha = create_once_json(result_root / "case-scores.json", case_scores)
    vector_sha = case_score_vector_sha256(scores)
    root_result = aggregate.get("root")
    if not isinstance(root_result, Mapping):
        raise ValueError("RCA100 repaired root aggregate is invalid")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lock = {
        "schema_version": "rca100.evaluator-repair-scoring-result-lock.v1",
        "repair_protocol_id": REPAIR_PROTOCOL_ID,
        "repair_implementation_commit": inputs.implementation_lock[
            "repair_implementation_commit"
        ],
        "answer_key_lock_sha256": sha256_file(
            repair.repair_control / "locks" / "answer-key-lock.json"
        ),
        "original_terminal_lock_sha256": ORIGINAL_TERMINAL_LOCK_SHA256,
        "fixed_denominator": 103,
        "terminals_scored": len(scores),
        "case_score_vector_sha256": vector_sha,
        "aggregate_file_sha256": aggregate_sha,
        "case_scores_file_sha256": case_scores_sha,
        "classification": root_result["classification"],
        "bootstrap_replicates": root_result["bootstrap_replicates"],
        "bootstrap_seed": root_result["bootstrap_seed"],
        "mcnemar_exact_p_value": root_result["mcnemar_exact_p_value"],
        "provider_calls": 0,
        "prediction_reruns": 0,
        "case_replacements": 0,
        "created_at_utc": now,
    }
    lock_path = repair.repair_control / "locks" / "scoring-result-lock.json"
    lock_sha = create_once_json(lock_path, lock)
    advance_repair_state(
        repair.repair_control,
        "REPAIR_SCORED",
        bindings={
            "scoring_result_lock_sha256": lock_sha,
            "case_score_vector_sha256": vector_sha,
            "classification": root_result["classification"],
            "provider_calls": 0,
        },
    )
    return {**lock, "scoring_result_lock_sha256": lock_sha, "aggregate": aggregate}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _repair_environment() -> RepairEnvironment:
    return RepairEnvironment.from_environment(
        os.environ, repository_root=_repository_root()
    )


def repair_main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze-implementation")
    freeze.add_argument("--reviewer-verdict", required=True)
    freeze.add_argument("--ci-reference", required=True)
    subparsers.add_parser("lock-answer-key")
    subparsers.add_parser("score")
    arguments = parser.parse_args()
    repair = _repair_environment()
    if arguments.command == "freeze-implementation":
        result = freeze_implementation(
            repair,
            reviewer_verdict=arguments.reviewer_verdict,
            ci_reference=arguments.ci_reference,
        )
    elif arguments.command == "lock-answer-key":
        result = lock_answer_key(repair)
    else:
        result = score_frozen_terminals(repair)
    print(json.dumps(result, indent=2))


def current_repair_state(control_root: Path) -> RepairStateName | None:
    found = [
        state
        for state in REPAIR_STATE_CHAIN
        if (control_root / "state" / f"{state}.json").is_file()
    ]
    if not found:
        return None
    expected = list(REPAIR_STATE_CHAIN[: len(found)])
    if found != expected:
        raise ValueError("RCA100 evaluator repair state chain is non-contiguous")
    return found[-1]  # type: ignore[return-value]


def advance_repair_state(
    control_root: Path,
    state: RepairStateName,
    *,
    bindings: Mapping[str, object],
) -> str:
    index = REPAIR_STATE_CHAIN.index(state)
    expected_previous = None if index == 0 else REPAIR_STATE_CHAIN[index - 1]
    if current_repair_state(control_root) != expected_previous:
        raise ValueError("RCA100 evaluator repair transition is out of order")
    previous_sha = (
        None
        if expected_previous is None
        else sha256_file(control_root / "state" / f"{expected_previous}.json")
    )
    return create_once_json(
        control_root / "state" / f"{state}.json",
        {
            "schema_version": "rca100.evaluator-repair-state.v1",
            "state": state,
            "previous_state": expected_previous,
            "previous_state_record_sha256": previous_sha,
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            **dict(bindings),
        },
    )


__all__ = [
    "ORIGINAL_PR22_HEAD",
    "ORIGINAL_PROTOCOL_FREEZE_SHA256",
    "ORIGINAL_TERMINAL_LOCK_SHA256",
    "PUBLIC_RESULT_PATHS",
    "REPAIR_PROTOCOL_ID",
    "REPAIR_STATE_CHAIN",
    "RepairEnvironment",
    "RepairEvaluationInputs",
    "RepairStateName",
    "advance_repair_state",
    "case_score_vector_sha256",
    "current_repair_state",
    "load_repair_evaluation_inputs",
    "verify_original_execution",
    "verify_repair_implementation",
    "verify_source_snapshot",
]
