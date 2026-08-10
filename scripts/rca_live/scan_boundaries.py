"""Static fail-closed label/evaluator separation scan for live B0/H1 code."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Literal


RUNTIME_PATHS = (
    "src/ecomsre_rca_unified/hierarchical_context.py",
    "src/ecomsre_rca_unified/live_rca100_scan.py",
    "src/ecomsre_rca_unified/live_context_adapters.py",
    "src/ecomsre_rca_unified/live_comparison.py",
    "src/ecomsre_rca_unified/live_evaluation.py",
    "src/ecomsre_rca_unified/live_runtime.py",
)
CLI_PATH = "scripts/rca_live/cli.py"
FORBIDDEN_RUNTIME_IMPORTS = (
    "ecomsre_rca100.evaluator",
    "ecomsre_rca_unified.adapters",
    "ecomsre_rcaeval.scoring",
    "scripts.rca_live.evaluator",
)
FORBIDDEN_RUNTIME_NAMES = {
    "DevCase",
    "discover_dev_cases",
    "load_answer_key",
    "root_cause_service",
}
ALLOWED_EVALUATOR_IMPORT_FUNCTIONS = {
    "score_tune",
    "score_regression",
    "_verify_private_evaluation",
}


class BoundaryVisitor(ast.NodeVisitor):
    def __init__(self, *, role: Literal["RUNTIME", "CLI"]) -> None:
        self.role = role
        self.function_stack: list[str] = []
        self.violations: list[str] = []

    def _record(self, node: ast.AST, message: str) -> None:
        self.violations.append(f"line {getattr(node, 'lineno', 0)}: {message}")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def _check_import(self, node: ast.AST, module: str) -> None:
        if self.role == "RUNTIME" and module.startswith(FORBIDDEN_RUNTIME_IMPORTS):
            self._record(node, f"runtime imports evaluator/truth module {module}")
        if module == "scripts.rca_live.evaluator" and (
            not self.function_stack
            or self.function_stack[-1] not in ALLOWED_EVALUATOR_IMPORT_FUNCTIONS
        ):
            self._record(node, "evaluator import is outside a post-lock command")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_import(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_import(node, node.module or "")
        if self.role == "RUNTIME":
            for alias in node.names:
                if alias.name in FORBIDDEN_RUNTIME_NAMES:
                    self._record(node, f"runtime imports forbidden name {alias.name}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.role == "RUNTIME" and node.id in FORBIDDEN_RUNTIME_NAMES:
            self._record(node, f"runtime references forbidden label name {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in {"root_cause_service", "fault"}:
            self._record(node, f"pre-execution code reads label attribute {node.attr}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value in {"root_cause_service", "fault"}:
            current_function = self.function_stack[-1] if self.function_stack else None
            allowed = (
                self.role == "RUNTIME"
                and current_function
                in {"assert_model_context_private", "_private_payload_markers"}
            ) or (
                self.role == "CLI"
                and current_function in ALLOWED_EVALUATOR_IMPORT_FUNCTIONS
            )
            if not allowed:
                self._record(node, f"pre-execution code reads label key {node.value}")
        self.generic_visit(node)


def scan_file(path: Path, *, role: Literal["RUNTIME", "CLI"]) -> tuple[str, ...]:
    if path.is_symlink() or not path.is_file():
        return ("required scan target is not a regular file",)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        return (f"cannot parse scan target: {error}",)
    visitor = BoundaryVisitor(role=role)
    visitor.visit(tree)
    return tuple(visitor.violations)


def scan_project(project_root: Path) -> None:
    violations: list[str] = []
    for relative in RUNTIME_PATHS:
        path = project_root / relative
        violations.extend(
            f"{relative}:{message}"
            for message in scan_file(path, role="RUNTIME")
        )
    violations.extend(
        f"{CLI_PATH}:{message}"
        for message in scan_file(project_root / CLI_PATH, role="CLI")
    )
    if violations:
        raise ValueError(
            "live label/evaluator separation scan failed:\n" + "\n".join(violations)
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    scan_project(args.project_root.resolve(strict=True))
    print("PASS_LIVE_LABEL_EVALUATOR_SEPARATION")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
