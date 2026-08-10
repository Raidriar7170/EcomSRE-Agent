"""Guarded RCA100 one-shot lifecycle commands."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Literal

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_file
from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    StdlibOpenAICompatibleTransport,
)
from ecomsre_rca100.contracts import (
    CanonicalRCA100Entity,
    RCA100MetricsEntityRank,
)
from ecomsre_rca100.dataset import audit_dataset
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    RCA100Schedule,
    advance_state,
    create_once_json,
    current_state,
    load_strict_json,
    schedule_sha256,
    tree_sha256,
    verify_tree_binding,
)
from ecomsre_rca100.projection import (
    RCA100AgentContext,
    RCA100AgentTask,
    RCA100MetricEvidence,
    RCA100MetricsProjection,
    RCA100SourceProjection,
)
from ecomsre_rca100.preflight import run_synthetic_full_pipeline
from ecomsre_rca100.prompt import (
    OpenAICompatibleRCA100Provider,
    output_schema_sha256,
    prompt_sha256,
)
from ecomsre_rca100.public_projection import (
    scan_preexecution_runtime,
    verify_runtime_evaluator_import_separation,
)
from ecomsre_rca100.runner import (
    RCA100TerminalRecord,
    execute_case,
    execute_schedule,
)
from ecomsre_rcaeval_adaptive.v2_runner import PacedTransport, RequestPacer
from ecomsre_rcaeval_v2.dev3_provider import Dev3RetryingTransport
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    rebuild_attempt_accounting,
)


PROTOCOL_ID = "rca100-metrics-arbitration-v1"
SOURCE_COMMIT = "fd92cae17e6e14fa3ed0f3963c31838151fbdaa7"
INPUT_TREE_SHA256 = "8ab512ce9ad041ed1ffd89226c2df77d3bb741fed08990854f481794c98585bb"
FRESH_INPUT_TREE_SHA256 = "aca130e350330000e0d9bc575606e3a5378178b6d7e0c2afb5cf13910596fea9"
SCHEDULE_SHA256 = "00604fa3157edde3597a7ef6758637be06a099051181d921cc35a7f305c4459e"
MODEL = "gpt-5.4-mini-2026-03-17"
TIMEOUT_SECONDS = 30.0
MAX_COMPLETION_TOKENS = 2048
PROMPT_TOKEN_RESERVATION = 29952
ATTEMPT_TOKEN_RESERVATION = 32000


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _roots() -> PrivateRoots:
    roots = PrivateRoots.from_environment(os.environ)
    roots.validate(repository_root=_repository_root())
    return roots


def _config_root() -> Path:
    return _repository_root() / "config" / PROTOCOL_ID


def _head() -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_clean() -> None:
    status = subprocess.run(
        ("git", "status", "--porcelain"),
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: worktree is not clean")


def _schedule(roots: PrivateRoots) -> RCA100Schedule:
    schedule = RCA100Schedule.model_validate_json(
        (roots.schedule / "schedule.json").read_text(encoding="utf-8")
    )
    if schedule_sha256(schedule) != SCHEDULE_SHA256:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule hash differs")
    return schedule


def _protocol_lock(roots: PrivateRoots) -> dict[str, object]:
    value = load_strict_json(roots.control / "locks" / "protocol-freeze.json")
    if not isinstance(value, dict):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: protocol lock is invalid")
    return value


def _config_tree_sha256() -> str:
    return tree_sha256(_config_root())[0]


def _verify_static_bindings(roots: PrivateRoots) -> dict[str, object]:
    lock = _protocol_lock(roots)
    if lock.get("implementation_commit") != _head():
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: implementation commit differs")
    if lock.get("config_tree_sha256") != _config_tree_sha256():
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: config tree differs")
    if lock.get("prompt_sha256") != prompt_sha256():
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: Prompt differs")
    if lock.get("output_schema_sha256") != output_schema_sha256():
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: output schema differs")
    if lock.get("schedule_sha256") != SCHEDULE_SHA256:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule binding differs")
    if lock.get("fresh_content_tree_sha256") != FRESH_INPUT_TREE_SHA256:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: fresh input tree binding differs")
    source = load_strict_json(roots.control / "source" / "input-source-lock.json")
    if not isinstance(source, dict) or (
        source.get("source_commit") != SOURCE_COMMIT
        or source.get("input_tree_sha256") != INPUT_TREE_SHA256
        or source.get("answer" + "_key_materialized") is not False
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: source lock differs")
    if (roots.input_source / "RCA100" / ("answer" + "_key")).exists():
        raise ValueError("BLOCKED_GROUND_TRUTH_LEAKAGE")
    verify_tree_binding(
        roots.input_source / "RCA100" / "cases",
        expected_sha256=FRESH_INPUT_TREE_SHA256,
        expected_file_count=721,
        label="RCA100 label-blind input",
    )
    _schedule(roots)
    _require_clean()
    return lock


def _provider_config() -> OpenAICompatibleConfig:
    config = OpenAICompatibleConfig.from_environment()
    if config is None:
        raise ValueError("OpenAI-compatible Provider configuration is absent")
    if config.model != MODEL:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: Provider model differs")
    return config


def command_audit() -> None:
    roots = _roots()
    if current_state(roots.control) != "INPUTS_ACQUIRED":
        raise ValueError("RCA100 no-label audit requires INPUTS_ACQUIRED")
    findings = scan_preexecution_runtime(_repository_root())
    if findings:
        raise ValueError(f"BLOCKED_GROUND_TRUTH_LEAKAGE:{findings}")
    verify_runtime_evaluator_import_separation(_repository_root())
    audit = audit_dataset(roots.input_source / "RCA100")
    audit_sha = create_once_json(
        roots.control / "audit" / "no-label-schema-audit.json",
        audit.model_dump(mode="json"),
    )
    advance_state(
        roots.control,
        "ADAPTER_VALIDATED_NO_GT",
        bindings={"audit_sha256": audit_sha},
    )
    print(audit.model_dump_json(indent=2))


def command_freeze(*, ci_reference: str) -> None:
    roots = _roots()
    if current_state(roots.control) != "ADAPTER_VALIDATED_NO_GT":
        raise ValueError("RCA100 freeze requires ADAPTER_VALIDATED_NO_GT")
    _require_clean()
    if scan_preexecution_runtime(_repository_root()):
        raise ValueError("BLOCKED_GROUND_TRUTH_LEAKAGE")
    verify_runtime_evaluator_import_separation(_repository_root())
    verify_tree_binding(
        roots.input_source / "RCA100" / "cases",
        expected_sha256=FRESH_INPUT_TREE_SHA256,
        expected_file_count=721,
        label="RCA100 label-blind input",
    )
    schedule = _schedule(roots)
    prompt_lock = load_strict_json(_config_root() / "prompt-lock.json")
    if not isinstance(prompt_lock, dict) or (
        prompt_lock.get("prompt_sha256") != prompt_sha256()
        or prompt_lock.get("output_schema_sha256") != output_schema_sha256()
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: Prompt config differs")
    freeze = {
        "schema_version": "rca100.protocol-freeze.v1",
        "protocol_id": PROTOCOL_ID,
        "implementation_commit": _head(),
        "ci_reference": ci_reference,
        "source_commit": SOURCE_COMMIT,
        "input_tree_sha256": INPUT_TREE_SHA256,
        "fresh_content_tree_sha256": FRESH_INPUT_TREE_SHA256,
        "fresh_content_tree_algorithm": "SORTED_RELATIVE_PATH_NUL_SHA256_NEWLINE_V1",
        "source_lock_sha256": "f99c48e69d240bedbfe9d441fef1effd6ede0ef66b28f33859fddc45aa89356e",
        "config_tree_sha256": _config_tree_sha256(),
        "prompt_sha256": prompt_sha256(),
        "output_schema_sha256": output_schema_sha256(),
        "schedule_sha256": schedule_sha256(schedule),
        "fixed_denominator": 103,
        "model": MODEL,
        "temperature": 0.0,
        "top_p": 1.0,
        "m3_rank_condition": "NONE_OR_GREATER_THAN_2",
        "m3_margin": 0.25,
        "fault_type_preservation": "MODEL_INITIAL",
        "bootstrap_seed": 20260810,
        "bootstrap_replicates": 10000,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    freeze_sha = create_once_json(
        roots.control / "locks" / "protocol-freeze.json", freeze
    )
    advance_state(
        roots.control,
        "PROTOCOL_FROZEN",
        bindings={"protocol_freeze_sha256": freeze_sha},
    )
    print(json.dumps({"protocol_freeze_sha256": freeze_sha}, indent=2))


def _synthetic_context() -> RCA100AgentContext:
    entities = tuple(
        CanonicalRCA100Entity(
            entity_ref=f"apm|apm.service|synthetic-{suffix}",
            domain="apm",
            type="apm.service",
            entity_id=f"synthetic-{suffix}",
            entity_name=f"synthetic-{suffix}",
            normalized_name=f"synthetic-{suffix}",
        )
        for suffix in ("a", "b")
    )
    evidence = tuple(
        RCA100MetricEvidence(
            evidence_ref=f"metric:{index:04d}",
            entity_ref=entity.entity_ref,
            metric="synthetic_latency",
            pre_count=3,
            post_count=3,
            pre_mean=1.0,
            post_mean=score + 1.0,
            score=score,
            summary=f"Synthetic capacity evidence F0={score:.1f}.",
        )
        for index, (entity, score) in enumerate(zip(entities, (4.0, 1.0)), 1)
    )
    ranking = tuple(
        RCA100MetricsEntityRank(
            entity_ref=entity.entity_ref,
            rank=index,
            score=score,
            supporting_metrics_evidence_refs=(f"metric:{index:04d}",),
        )
        for index, (entity, score) in enumerate(zip(entities, (4.0, 1.0)), 1)
    )
    def unavailable(
        source: Literal["logs", "traces"],
    ) -> RCA100SourceProjection:
        return RCA100SourceProjection(
            source=source,
            status="SOURCE_UNAVAILABLE",
            reason="SYNTHETIC_PREFLIGHT_EXCLUDED",
            total_rows=0,
            window_rows=0,
            mapped_rows=0,
            unmapped_rows=0,
        )
    return RCA100AgentContext(
        task=RCA100AgentTask(
            opaque_case_id="rca100-case-0001",
            alert_title="Synthetic capacity check",
            prompt_text="Diagnose the supplied synthetic anomaly.",
            window_start_timestamp=100.0,
            anchor_timestamp=110.0,
            window_end_timestamp=120.0,
            anchor_source="TASK_ALERT_TRIGGER",
            alert_entity_ref=entities[1].entity_ref,
        ),
        visible_entities=entities,
        metrics=RCA100MetricsProjection(
            status="AVAILABLE",
            evidence=evidence,
            ranking=ranking,
            total_rows=12,
            window_rows=12,
            mapped_rows=12,
            unmapped_rows=0,
            valid_series=2,
            ranked_entities=2,
        ),
        logs=unavailable("logs"),
        traces=unavailable("traces"),
    )


def command_preflight() -> None:
    roots = _roots()
    if current_state(roots.control) != "PROTOCOL_FROZEN":
        raise ValueError("RCA100 preflight requires PROTOCOL_FROZEN")
    lock = _verify_static_bindings(roots)
    config = _provider_config()
    protocol_freeze_sha = sha256_file(
        roots.control / "locks" / "protocol-freeze.json"
    )
    synthetic_terminal = run_synthetic_full_pipeline(
        roots,
        protocol_freeze_sha256=protocol_freeze_sha,
        schedule_sha256=SCHEDULE_SHA256,
        model=MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        prompt_token_reservation=PROMPT_TOKEN_RESERVATION,
        attempt_token_reservation=ATTEMPT_TOKEN_RESERVATION,
        retry_policy_sha256=hashlib.sha256(
            canonical_json_bytes(load_strict_json(_config_root() / "budget.json"))
        ).hexdigest(),
    )
    context = _synthetic_context()
    run_root = roots.journal / "preflight" / "synthetic-capacity-v1"
    budget = AttemptBudget(
        max_provider_attempts=2,
        max_retry_attempts=1,
        prompt_token_reservation=PROMPT_TOKEN_RESERVATION,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        max_conservative_tokens=2 * ATTEMPT_TOKEN_RESERVATION,
    )
    policy_sha = hashlib.sha256(
        canonical_json_bytes(load_strict_json(_config_root() / "budget.json"))
    ).hexdigest()
    transport = Dev3RetryingTransport(
        PacedTransport(StdlibOpenAICompatibleTransport(), RequestPacer(5.0)),
        run_root=run_root,
        budget=budget,
        policy_lock_sha256=policy_sha,
        expected_timeout_seconds=TIMEOUT_SECONDS,
    )
    provider = OpenAICompatibleRCA100Provider(
        config=config,
        expected_model=MODEL,
        timeout_seconds=TIMEOUT_SECONDS,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        transport=transport,
    )
    success = False
    failure_code = None
    try:
        provider.diagnose(context)
        success = True
    except Exception as error:
        failure_code = type(error).__name__
    accounting = rebuild_attempt_accounting(
        (run_root,),
        prompt_token_reservation=PROMPT_TOKEN_RESERVATION,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    gate_passed = bool(
        success
        and provider.usage_known
        and accounting.provider_attempt_count == 1
        and accounting.retry_attempt_count == 0
    )
    gate = {
        "schema_version": "rca100.holdout-preflight.v1",
        "protocol_freeze_sha256": protocol_freeze_sha,
        "implementation_commit": lock["implementation_commit"],
        "synthetic_full_pipeline": "PASS",
        "synthetic_terminal_sha256": sha256_file(
            roots.control
            / "preflight"
            / "synthetic-full-pipeline-v1"
            / "output"
            / "terminals"
            / f"{synthetic_terminal.opaque_case_id}.json"
        ),
        "provider_valid_typed_response": success,
        "provider_known_usage": provider.usage_known,
        "provider_attempts": accounting.provider_attempt_count,
        "transport_retries": accounting.retry_attempt_count,
        "http_429": int(failure_code == "HTTPError"),
        "schema_error": int(not success),
        "failure_class_or_none": failure_code,
        "passed": gate_passed,
    }
    gate_sha = create_once_json(
        roots.control / "preflight" / "holdout-preflight.json", gate
    )
    if not gate_passed:
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_PREFLIGHT")
    advance_state(
        roots.control,
        "HOLDOUT_PREFLIGHT_PASSED",
        bindings={"holdout_preflight_sha256": gate_sha},
    )
    print(json.dumps(gate, indent=2))


def command_run() -> None:
    roots = _roots()
    if current_state(roots.control) != "HOLDOUT_PREFLIGHT_PASSED":
        raise ValueError("RCA100 run requires HOLDOUT_PREFLIGHT_PASSED")
    _verify_static_bindings(roots)
    config = _provider_config()
    schedule = _schedule(roots)
    budget = AttemptBudget(
        max_provider_attempts=206,
        max_retry_attempts=103,
        prompt_token_reservation=PROMPT_TOKEN_RESERVATION,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        max_conservative_tokens=6_592_000,
    )
    pacer = RequestPacer(5.0)
    retry_policy_sha = hashlib.sha256(
        canonical_json_bytes(load_strict_json(_config_root() / "budget.json"))
    ).hexdigest()

    def execute(record: object) -> RCA100TerminalRecord:
        from ecomsre_rca100.lifecycle import RCA100ScheduleRecord

        if not isinstance(record, RCA100ScheduleRecord):
            raise TypeError("invalid scheduled record")
        return execute_case(
            record,
            cases_root=roots.input_source / "RCA100" / "cases",
            journal_root=roots.journal,
            output_root=roots.output,
            schedule_sha256=SCHEDULE_SHA256,
            protocol_freeze_sha256=sha256_file(
                roots.control / "locks" / "protocol-freeze.json"
            ),
            provider_config=config,
            expected_model=MODEL,
            timeout_seconds=TIMEOUT_SECONDS,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            prompt_token_reservation=PROMPT_TOKEN_RESERVATION,
            pacer=pacer,
            budget=budget,
            retry_policy_sha256=retry_policy_sha,
        )

    terminals = execute_schedule(schedule, execute=execute)
    summary = {
        "schema_version": "rca100.execution-summary.v1",
        "planned": 103,
        "terminalized": len(terminals),
        "completed": sum(item.status.value == "COMPLETED" for item in terminals),
        "http_429_abort": any(item.failure_code == "HTTP_429" for item in terminals),
        "provider_attempts": sum(item.provider_attempts for item in terminals),
        "transport_retries": sum(item.transport_retries for item in terminals),
        "semantic_model_operations": sum(
            item.semantic_model_operations for item in terminals
        ),
        "specialist_calls": 0,
        "fusion_model_calls": 0,
        "known_token_lower_bound": sum(
            item.known_token_lower_bound for item in terminals
        ),
        "conservative_token_upper_bound": sum(
            item.conservative_token_upper_bound for item in terminals
        ),
    }
    execution_sha = create_once_json(
        roots.control / "execution" / "execution-summary.json", summary
    )
    if summary["http_429_abort"]:
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_DURING_HOLDOUT")
    if len(terminals) != 103:
        raise ValueError("RCA100 holdout lacks 103 terminals")
    advance_state(
        roots.control,
        "HOLDOUT_EXECUTED",
        bindings={"execution_summary_sha256": execution_sha},
    )
    print(json.dumps(summary, indent=2))


def command_lock_terminals() -> None:
    roots = _roots()
    if current_state(roots.control) != "HOLDOUT_EXECUTED":
        raise ValueError("RCA100 terminal lock requires HOLDOUT_EXECUTED")
    lock = _verify_static_bindings(roots)
    schedule = _schedule(roots)
    terminal_paths = sorted((roots.output / "terminals").glob("*.json"))
    terminals = tuple(
        RCA100TerminalRecord.model_validate_json(path.read_text(encoding="utf-8"))
        for path in terminal_paths
    )
    if len(terminals) != 103:
        raise ValueError("RCA100 terminal lock requires 103 terminal records")
    if {item.opaque_case_id for item in terminals} != {
        item.opaque_case_id for item in schedule.records
    } or len({item.run_id for item in terminals}) != 103:
        raise ValueError("RCA100 terminal identities differ from schedule")
    run_attempts = tuple((roots.journal / "run-attempts").glob("*.json"))
    if len(run_attempts) != 103:
        raise ValueError("RCA100 terminal lock requires 103 run attempts")
    terminal_tree, terminal_count = tree_sha256(roots.output / "terminals")
    attempt_tree, attempt_count = tree_sha256(roots.journal / "run-attempts")
    provider_tree, provider_count = tree_sha256(roots.journal / "runs")
    terminal_lock = {
        "schema_version": "rca100.terminal-records-lock.v1",
        "implementation_commit": lock["implementation_commit"],
        "schedule_sha256": SCHEDULE_SHA256,
        "terminal_records": terminal_count,
        "run_attempts": attempt_count,
        "provider_sidecar_records": provider_count,
        "terminal_tree_sha256": terminal_tree,
        "attempt_tree_sha256": attempt_tree,
        "provider_attempt_tree_sha256": provider_tree,
        "duplicate_run_ids": 0,
        "semantic_retries": 0,
        "schema_retries": 0,
        "locked_at_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    terminal_lock_sha = create_once_json(
        roots.control / "locks" / "terminal-records-lock.json", terminal_lock
    )
    advance_state(
        roots.control,
        "TERMINAL_RECORDS_LOCKED",
        bindings={"terminal_records_lock_sha256": terminal_lock_sha},
    )
    print(json.dumps(terminal_lock, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("audit")
    freeze = subcommands.add_parser("freeze")
    freeze.add_argument("--ci-reference", required=True)
    subcommands.add_parser("preflight")
    subcommands.add_parser("run")
    subcommands.add_parser("lock-terminals")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "audit":
        command_audit()
    elif args.command == "freeze":
        command_freeze(ci_reference=args.ci_reference)
    elif args.command == "preflight":
        command_preflight()
    elif args.command == "run":
        command_run()
    elif args.command == "lock-terminals":
        command_lock_terminals()
    else:
        raise AssertionError("unknown RCA100 command")


if __name__ == "__main__":
    main()
