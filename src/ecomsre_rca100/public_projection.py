"""Leakage checks for runtime payloads and aggregate-only public artifacts."""

from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Iterable


_PRIVATE_VALUE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])t[0-9]{3}(?![A-Za-z0-9])"),
    re.compile(r"rca100-case-[0-9]{4}"),
    re.compile(r"/Users/[^\s\"']+"),
)
_PRIVATE_FIELD_NAMES = (
    '"source_task_id"',
    '"opaque_case_id"',
    '"run_id"',
    '"initial_root_entity_ref"',
    '"final_root_entity_ref"',
    '"initial_evidence_refs"',
    '"final_evidence_refs"',
)
_CREDENTIAL_MARKERS = (
    "ECOMSRE_LLM_API_KEY",
    "OPENAI_API_KEY",
    "Authorization: Bearer",
)
_PREEXEC_MARKERS = (
    "answer_key",
    ".gt.json",
    "root_cause_entities",
    "root_cause_types",
    "raw_ground_truth",
    "target_entity_ids",
    "target_entities",
    "expected_fault_id",
)


def scan_public_artifacts(paths: Iterable[Path]) -> tuple[str, ...]:
    findings: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for marker in _PRIVATE_FIELD_NAMES + _CREDENTIAL_MARKERS:
            if marker in text:
                findings.append(f"{path.name}:private-marker:{marker}")
        for pattern in _PRIVATE_VALUE_PATTERNS:
            if pattern.search(text):
                findings.append(f"{path.name}:private-value:{pattern.pattern}")
    return tuple(findings)


def scan_preexecution_runtime(repository_root: Path) -> tuple[str, ...]:
    allowed = {
        "src/ecomsre_rca100/evaluation_integrity.py",
        "src/ecomsre_rca100/evaluator.py",
        "src/ecomsre_rca100/public_projection.py",
        "tests/benchmarks/rca100/test_evaluator.py",
    }
    findings: list[str] = []
    candidates = (
        *(repository_root / "src" / "ecomsre_rca100").glob("*.py"),
        *(repository_root / "scripts" / "rca100").glob("*.py"),
        *(repository_root / "tests" / "benchmarks" / "rca100").glob("*.py"),
    )
    for path in candidates:
        relative = path.relative_to(repository_root).as_posix()
        if relative.startswith("tests/benchmarks/rca100/") or relative in allowed or path.name in {
            "acquire_answer_key.py",
            "build_report.py",
            "verify_report.py",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in _PREEXEC_MARKERS:
            if marker in text:
                findings.append(f"{relative}:{marker}")
    return tuple(findings)


def verify_runtime_evaluator_import_separation(repository_root: Path) -> None:
    package = repository_root / "src" / "ecomsre_rca100"
    for path in package.glob("*.py"):
        if path.name in {
            "evaluation_integrity.py",
            "evaluator.py",
            "public_projection.py",
        }:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "ecomsre_rca100.evaluation_integrity",
                "ecomsre_rca100.evaluator",
            }:
                raise ValueError(f"runtime module imports evaluator: {path.name}")
            if isinstance(node, ast.Import) and any(
                alias.name
                in {
                    "ecomsre_rca100.evaluation_integrity",
                    "ecomsre_rca100.evaluator",
                }
                for alias in node.names
            ):
                raise ValueError(f"runtime module imports evaluator: {path.name}")


__all__ = [
    "scan_preexecution_runtime",
    "scan_public_artifacts",
    "verify_runtime_evaluator_import_separation",
]
