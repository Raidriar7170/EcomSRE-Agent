"""Offline Phase 2 comparison generation and verification CLI."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from ecomsre.backends.replay import load_replay_case
from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
)
from ecomsre.phase1.contracts import RCADecision
from ecomsre.phase1.runtime_config import load_agent_settings
from ecomsre.phase2.contracts import Phase2Variant
from ecomsre.phase2.provider import (
    PHASE2_PROVIDER_IDENTITY,
    OpenAICompatiblePhase2Backend,
)
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT, load_token_authority
from ecomsre.phase2.workflows import WorkflowRunResult, run_replay_workflow


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = PROJECT_ROOT / "artifacts/phase2/comparison/comparison-report.json"
DEFAULT_PROVIDER_REPORT = (
    PROJECT_ROOT / "artifacts/phase2/provider-smoke/provider-smoke-report.json"
)
DEFAULT_PROVIDER_CASE_ROOT = (
    PROJECT_ROOT / "artifacts/phase2/provider-smoke/cases"
)
_PROVIDER_ENVIRONMENT_NAMES = (
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_MODEL",
)
_PROVIDER_CASES = (
    (
        "fixed_positive",
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        "ad-partial-failure-complete",
        RCADecision.RCA_CONFIRMED,
    ),
    (
        "dynamic_positive",
        Phase2Variant.DYNAMIC_MULTI_AGENT,
        "ad-partial-failure-complete",
        RCADecision.RCA_CONFIRMED,
    ),
    (
        "fixed_negative",
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        "no-real-incident",
        RCADecision.ABSTAIN,
    ),
    (
        "dynamic_negative",
        Phase2Variant.DYNAMIC_MULTI_AGENT,
        "no-real-incident",
        RCADecision.ABSTAIN,
    ),
)


def _load_evaluator() -> ModuleType:
    module_name = "_ecomsre_phase2_cli_evaluator"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    source = PROJECT_ROOT / "eval/phase2/compare.py"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError("Phase 2 evaluator spec cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _report_bytes(report: object) -> bytes:
    return (
        json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("comparison report target must be a regular file")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _compare(output: Path) -> int:
    report = _load_evaluator().run_comparison(PROJECT_ROOT)
    content = _report_bytes(report)
    _write_atomic(output, content)
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "report": str(output),
                "bytes": len(content),
                "semantic_sha256": report["deterministic_semantic_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _verify(report_path: Path) -> int:
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("comparison report must be an existing regular file")
    observed = report_path.read_bytes()
    expected_report = _load_evaluator().run_comparison(PROJECT_ROOT)
    expected = _report_bytes(expected_report)
    status = "VERIFIED" if observed == expected else "MISMATCH"
    print(
        json.dumps(
            {
                "status": status,
                "report": str(report_path),
                "semantic_sha256": expected_report[
                    "deterministic_semantic_sha256"
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if status == "VERIFIED" else 1


def _provider_case_result(
    *,
    variant: Phase2Variant,
    case_id: str,
    expected_decision: RCADecision,
    result: WorkflowRunResult,
) -> tuple[dict[str, object], bool]:
    trace = result.trace
    final = trace.final_rca
    available_refs = {
        reference
        for record in trace.tool_call_records
        for reference in record.evidence_refs
    }
    final_refs = (
        ()
        if final is None
        else (*final.supporting_evidence, *final.contradicting_evidence)
    )
    provider_identity_valid = bool(trace.model_call_audits) and all(
        record.status == "CHARGED"
        and record.observed_provider_identity == PHASE2_PROVIDER_IDENTITY
        for record in trace.model_call_audits
    )
    evidence_references_valid = set(final_refs).issubset(available_refs)
    passed = (
        trace.status == "COMPLETED"
        and final is not None
        and final.decision is expected_decision
        and evidence_references_valid
        and provider_identity_valid
    )
    return (
        {
            "variant": variant.value,
            "case_id": case_id,
            "run_id": trace.run_id,
            "status": trace.status,
            "expected_decision": expected_decision.value,
            "decision": None if final is None else final.decision.value,
            "root_service": None if final is None else final.root_service,
            "fault_mechanism": (
                None
                if final is None or final.fault_mechanism is None
                else final.fault_mechanism.value
            ),
            "evidence_references_valid": evidence_references_valid,
            "provider_identity_valid": provider_identity_valid,
            "model_calls": len(trace.model_call_audits),
            "model_call_results": [
                {
                    "operation": record.operation.value,
                    "status": record.status,
                    "failure_code": (
                        None
                        if record.failure_code is None
                        else record.failure_code.value
                    ),
                }
                for record in trace.model_call_audits
            ],
            "tool_calls": trace.final_budget_snapshot.charged_tool_calls,
            "terminal_failure_code": (
                None
                if trace.terminal_failure_code is None
                else trace.terminal_failure_code.value
            ),
            "terminal_reason": trace.terminal_reason,
        },
        passed,
    )


def _provider_case_definition(
    requirement: str,
) -> tuple[str, Phase2Variant, str, RCADecision]:
    for definition in _PROVIDER_CASES:
        if definition[0] == requirement:
            return definition
    raise ValueError("unknown provider-smoke requirement")


def run_provider_smoke_case(
    *,
    project_root: Path,
    environment: Mapping[str, str],
    requirement: str,
    transport: OpenAICompatibleTransport | None = None,
) -> dict[str, object]:
    """Acquire one independently preservable real-provider requirement."""

    requirement, variant, case_id, expected_decision = (
        _provider_case_definition(requirement)
    )
    if any(
        not isinstance(environment.get(name), str)
        or not environment.get(name, "").strip()
        for name in _PROVIDER_ENVIRONMENT_NAMES
    ):
        return {
            "schema_version": "phase2.provider-smoke-case-report.v1",
            "status": "SKIPPED_NOT_CONFIGURED",
            "requirement": requirement,
            "provider": PHASE2_PROVIDER_IDENTITY,
            "model": None,
            "token_policy_core_sha256": None,
            "scripted_fallback": False,
            "provider_call_count": 0,
            "provider_prompt_tokens": [],
            "case_result": None,
        }
    config = OpenAICompatibleConfig.from_environment(environment)
    if config is None:
        raise RuntimeError("complete provider configuration was not loaded")
    root = Path(project_root)
    settings = load_agent_settings(root)
    backend = OpenAICompatiblePhase2Backend(
        config=config,
        transport=transport,
        timeout_seconds=float(settings.model_timeout_seconds),
    )
    replay_case = load_replay_case(
        root / "config/phase1/replay-cases/agent-visible",
        case_id,
    )
    result = run_replay_workflow(
        project_root=root,
        replay_case=replay_case,
        variant=variant,
        model_backend=backend,
        expected_provider_identity=PHASE2_PROVIDER_IDENTITY,
    )
    case_result, passed = _provider_case_result(
        variant=variant,
        case_id=case_id,
        expected_decision=expected_decision,
        result=result,
    )
    return {
        "schema_version": "phase2.provider-smoke-case-report.v1",
        "status": "PASSED" if passed else "FAILED",
        "requirement": requirement,
        "provider": PHASE2_PROVIDER_IDENTITY,
        "model": config.model,
        "token_policy_core_sha256": load_token_authority(root).core_sha256,
        "scripted_fallback": False,
        "provider_call_count": backend.calls,
        "provider_prompt_tokens": list(backend.provider_prompt_tokens),
        "case_result": case_result,
    }


def _combine_provider_case_reports(
    reports: tuple[Mapping[str, object], ...],
    *,
    expected_core_sha256: str,
) -> dict[str, object]:
    if len(reports) != len(_PROVIDER_CASES):
        raise ValueError("provider-smoke aggregate requires all four case reports")
    expected_requirements = tuple(item[0] for item in _PROVIDER_CASES)
    if tuple(report.get("requirement") for report in reports) != expected_requirements:
        raise ValueError("provider-smoke case reports are not in frozen order")
    providers = {report.get("provider") for report in reports}
    models = {report.get("model") for report in reports}
    core_digests = {
        report.get("token_policy_core_sha256") for report in reports
    }
    if (
        providers != {PHASE2_PROVIDER_IDENTITY}
        or len(models) != 1
        or len(core_digests) != 1
        or any(report.get("scripted_fallback") is not False for report in reports)
    ):
        raise ValueError("provider-smoke case reports do not share one authority")
    if models != {MODEL_SNAPSHOT} or core_digests != {expected_core_sha256}:
        raise ValueError(
            "provider-smoke case reports do not match the current frozen authority"
        )
    requirements: dict[str, bool] = {}
    case_results: list[object] = []
    provider_prompt_tokens: list[object] = []
    provider_call_count = 0
    for definition, report in zip(_PROVIDER_CASES, reports, strict=True):
        requirement, variant, case_id, expected_decision = definition
        case_result = report.get("case_result")
        calls = report.get("provider_call_count")
        prompt_tokens = report.get("provider_prompt_tokens")
        if report.get("schema_version") != "phase2.provider-smoke-case-report.v1":
            raise ValueError("provider-smoke case report schema is invalid")
        if (
            not isinstance(case_result, Mapping)
            or not isinstance(prompt_tokens, list)
            or type(calls) is not int
            or calls < 0
            or any(type(token) is not int or token <= 0 for token in prompt_tokens)
        ):
            raise ValueError("provider-smoke case report body is invalid")
        requirements[requirement] = (
            report.get("status") == "PASSED"
            and calls > 0
            and len(prompt_tokens) == calls
            and case_result.get("variant") == variant.value
            and case_result.get("case_id") == case_id
            and case_result.get("status") == "COMPLETED"
            and case_result.get("expected_decision") == expected_decision.value
            and case_result.get("decision") == expected_decision.value
            and case_result.get("evidence_references_valid") is True
            and case_result.get("provider_identity_valid") is True
            and case_result.get("terminal_failure_code") is None
        )
        case_results.append(dict(case_result))
        provider_prompt_tokens.extend(prompt_tokens)
        provider_call_count += calls
    requirements["no_scripted_fallback"] = True
    passed = all(requirements.values()) and provider_call_count > 0
    return {
        "schema_version": "phase2.provider-smoke-report.v1",
        "status": "PASSED" if passed else "FAILED",
        "provider": PHASE2_PROVIDER_IDENTITY,
        "model": next(iter(models)),
        "token_policy_core_sha256": next(iter(core_digests)),
        "scripted_fallback": False,
        "acquisition": "independent-preserved-case-reports",
        "provider_call_count": provider_call_count,
        "provider_prompt_tokens": provider_prompt_tokens,
        "case_results": case_results,
        "requirements": requirements,
    }


def run_provider_smoke(
    *,
    project_root: Path,
    environment: Mapping[str, str],
    transport: OpenAICompatibleTransport | None = None,
) -> dict[str, object]:
    """Run the explicit Fixed/Dynamic positive/negative real-provider gate."""

    requirements = {
        requirement: False
        for requirement, _variant, _case_id, _decision in _PROVIDER_CASES
    }
    requirements["no_scripted_fallback"] = True
    if any(
        not isinstance(environment.get(name), str)
        or not environment.get(name, "").strip()
        for name in _PROVIDER_ENVIRONMENT_NAMES
    ):
        return {
            "schema_version": "phase2.provider-smoke-report.v1",
            "status": "SKIPPED_NOT_CONFIGURED",
            "provider": PHASE2_PROVIDER_IDENTITY,
            "model": None,
            "scripted_fallback": False,
            "case_results": [],
            "requirements": requirements,
        }
    reports = tuple(
        run_provider_smoke_case(
            project_root=project_root,
            environment=environment,
            requirement=requirement,
            transport=transport,
        )
        for requirement, _variant, _case_id, _decision in _PROVIDER_CASES
    )
    return _combine_provider_case_reports(
        reports,
        expected_core_sha256=load_token_authority(Path(project_root)).core_sha256,
    )


def aggregate_provider_smoke(
    *,
    case_root: Path,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, object]:
    """Combine four independently preserved PASSED case reports offline."""

    reports: list[Mapping[str, object]] = []
    for requirement, _variant, _case_id, _decision in _PROVIDER_CASES:
        path = Path(case_root) / f"{requirement}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("provider-smoke case report is missing or unsafe")
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("provider-smoke case report must be an object")
        reports.append(parsed)
    return _combine_provider_case_reports(
        tuple(reports),
        expected_core_sha256=load_token_authority(Path(project_root)).core_sha256,
    )


def _archive_and_write(output: Path, report: Mapping[str, object]) -> None:
    if output.is_file() and not output.is_symlink():
        previous = output.read_bytes()
        digest = hashlib.sha256(previous).hexdigest()
        archived = output.parent / "attempts" / f"{digest}.json"
        if not archived.exists():
            _write_atomic(archived, previous)
        elif archived.read_bytes() != previous:
            raise ValueError("provider-smoke archive digest collision")
    _write_atomic(output, _report_bytes(report))


def _provider_smoke(
    output: Path,
    *,
    environment: Mapping[str, str],
    transport: OpenAICompatibleTransport | None = None,
) -> int:
    report = run_provider_smoke(
        project_root=PROJECT_ROOT,
        environment=environment,
        transport=transport,
    )
    _archive_and_write(output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "model": report["model"],
                "scripted_fallback": report["scripted_fallback"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if report["status"] == "PASSED":
        return 0
    return 3 if report["status"] == "SKIPPED_NOT_CONFIGURED" else 1


def _provider_smoke_case(
    requirement: str,
    output: Path,
    *,
    environment: Mapping[str, str],
    transport: OpenAICompatibleTransport | None = None,
) -> int:
    report = run_provider_smoke_case(
        project_root=PROJECT_ROOT,
        environment=environment,
        requirement=requirement,
        transport=transport,
    )
    _archive_and_write(output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "requirement": requirement,
                "report": str(output),
                "scripted_fallback": report["scripted_fallback"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if report["status"] == "PASSED":
        return 0
    return 3 if report["status"] == "SKIPPED_NOT_CONFIGURED" else 1


def _provider_smoke_aggregate(case_root: Path, output: Path) -> int:
    report = aggregate_provider_smoke(case_root=case_root)
    _archive_and_write(output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(output),
                "scripted_fallback": report["scripted_fallback"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if report["status"] == "PASSED" else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    provider_smoke = commands.add_parser("provider-smoke")
    provider_smoke.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_PROVIDER_REPORT,
    )
    provider_smoke_case = commands.add_parser("provider-smoke-case")
    provider_smoke_case.add_argument(
        "--requirement",
        choices=tuple(item[0] for item in _PROVIDER_CASES),
        required=True,
    )
    provider_smoke_case.add_argument("--output", type=Path)
    provider_smoke_aggregate = commands.add_parser("provider-smoke-aggregate")
    provider_smoke_aggregate.add_argument(
        "--case-root",
        type=Path,
        default=DEFAULT_PROVIDER_CASE_ROOT,
    )
    provider_smoke_aggregate.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_PROVIDER_REPORT,
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    transport: OpenAICompatibleTransport | None = None,
) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "compare":
            return _compare(args.output)
        if args.command == "verify":
            return _verify(args.report)
        if args.command == "provider-smoke-case":
            output = args.output or (
                DEFAULT_PROVIDER_CASE_ROOT / f"{args.requirement}.json"
            )
            return _provider_smoke_case(
                args.requirement,
                output,
                environment=os.environ if environment is None else environment,
                transport=transport,
            )
        if args.command == "provider-smoke-aggregate":
            return _provider_smoke_aggregate(args.case_root, args.output)
        return _provider_smoke(
            args.output,
            environment=os.environ if environment is None else environment,
            transport=transport,
        )
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"status": "FAILED", "error": type(error).__name__},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
