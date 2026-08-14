"""One-session LOCAL_DEMO runner built on the frozen v6 E2E runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Collection, Mapping, cast

from pydantic import BaseModel

from ecomsre_live_sandbox.contracts import (
    DiagnosisGate,
    DiagnosisResult,
    LocalDemoDiagnosisAdmission,
    LocalDemoStandingAuthorization,
    SLIWindow,
    canonical_json_bytes,
    canonical_sha256,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.control import evaluate_local_demo_diagnosis_gate
from ecomsre_live_sandbox.e2e_telemetry import scan_model_projection
import ecomsre_live_sandbox.e2e_v4 as e2e_v4
import ecomsre_live_sandbox.e2e_v6 as e2e_v6
from ecomsre_live_sandbox.e2e_v6_contracts import E2EV6PrivateRoots
from ecomsre_live_sandbox.local_demo_contracts import (
    LocalDemoConfig,
    LocalDemoPrivateRoot,
    local_runtime_config_sha256,
)
from ecomsre_live_sandbox.workflow import make_provider
from ecomsre_rca_unified.adapters import classify_fault_ontology


def _git(config: LocalDemoConfig, *arguments: str) -> str:
    return e2e_v4._git(config.repository_root, *arguments)


def verify_local_demo_worktree(
    config: LocalDemoConfig, clean_required: bool = True
) -> str:
    return e2e_v4._verify_worktree(cast(Any, config), clean_required)


def _read_mapping(path: Path, *, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"LOCAL_DEMO {label} is absent")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"LOCAL_DEMO {label} is malformed")
    return value


def _load_invocation_authority(
    config: LocalDemoConfig,
    roots: E2EV6PrivateRoots,
    top: LocalDemoPrivateRoot,
    authorization: LocalDemoStandingAuthorization,
) -> tuple[
    Mapping[str, object], None, None, LocalDemoStandingAuthorization
]:
    top.validate_standing_authorization(authorization, config.sandbox)
    lock = _read_mapping(
        roots.control / "latest-development-pass-lock.json",
        label="development pass lock",
    )
    return lock, None, None, authorization


def _verify_scenario_lock(
    config: LocalDemoConfig,
    roots: E2EV6PrivateRoots,
    locked: Mapping[str, object],
    implementation_commit: str,
) -> None:
    del roots
    if any(
        (
            locked.get("implementation_commit") != implementation_commit,
            not isinstance(locked.get("image_authority_sha256"), str),
            not isinstance(locked.get("compose_structure_sha256"), str),
            config.sandbox.scenario.target_service != "payment",
            config.sandbox.scenario.target_configuration_key
            != "paymentFailure.defaultVariant",
        )
    ):
        raise RuntimeError("LOCAL_DEMO scenario lock differs")


def _local_admission(
    diagnosis: DiagnosisResult,
    bundle: object,
    context: object,
    resolvable_refs: Collection[str],
    provider: object,
) -> LocalDemoDiagnosisAdmission:
    if not hasattr(context, "model_dump"):
        raise TypeError("LOCAL_DEMO context is not typed")
    dumped = context.model_dump(mode="json")
    context_path_hash = canonical_sha256(dumped)
    provider_hash = getattr(provider, "last_context_sha256", None)
    findings = scan_model_projection(dumped)
    return evaluate_local_demo_diagnosis_gate(
        diagnosis,
        cast(Any, bundle),
        resolvable_refs=resolvable_refs,
        context_sha256=context_path_hash,
        provider_live_input_sha256=provider_hash,
        control_truth_findings=findings,
    )


def _write_diagnosis_lineage(
    roots: E2EV6PrivateRoots,
    provider: object,
    context: object,
    diagnosis: DiagnosisResult,
    strict_gate: DiagnosisGate,
    local_gate: LocalDemoDiagnosisAdmission | None,
) -> None:
    initial = getattr(provider, "last_initial_diagnosis", None)
    if initial is None or local_gate is None:
        raise RuntimeError("LOCAL_DEMO diagnosis lineage is incomplete")
    raw = getattr(provider, "last_raw_response", None)
    arguments = getattr(provider, "last_tool_arguments", None)
    if not isinstance(raw, Mapping) or not isinstance(arguments, Mapping):
        raise RuntimeError("LOCAL_DEMO Provider lineage is incomplete")
    lineage_root = roots.provider / "diagnosis-lineage"
    typed_initial = (
        initial.model_dump(mode="json")
        if isinstance(initial, BaseModel)
        else initial
    )
    artifacts: tuple[tuple[str, object], ...] = (
        ("provider-strict-raw-response.json", raw),
        ("tool-call-arguments.json", arguments),
        ("parsed-a0.json", typed_initial),
        ("unified-runtime-output.json", diagnosis),
        (
            "ontology-adapter-input.json",
            {"fault_type_raw": getattr(initial, "fault_type", None)},
        ),
        (
            "ontology-adapter-output.json",
            {
                "fault_class": classify_fault_ontology(
                    str(getattr(initial, "fault_type", ""))
                ).value
            },
        ),
        ("final-diagnosis-result.json", diagnosis),
        ("strict-audit-gate.json", strict_gate),
        ("local-demo-gate.json", local_gate),
    )
    hashes: dict[str, str] = {}
    for name, value in artifacts:
        hashes[name] = write_private_json(
            lineage_root / name, value, create_once=True
        )
    write_private_json(
        lineage_root / "manifest.json",
        {
            "schema_version": "live-e2e.local-demo-diagnosis-lineage.v1",
            "artifacts": hashes,
            "provider_live_input_sha256": getattr(
                provider, "last_context_sha256", None
            ),
        },
        create_once=True,
    )


def _seal_accepted_run(
    config: LocalDemoConfig,
    roots: E2EV6PrivateRoots,
    top: LocalDemoPrivateRoot,
    terminal: dict[str, object],
    baseline_windows: Collection[SLIWindow],
) -> str:
    top.require_pre_live_admission(cast(str, terminal["implementation_commit"]))
    value = {
        "schema_version": "live-e2e.local-demo-accepted-run.v1",
        "mode": "LOCAL_DEMO",
        "classification": "POST_FAILURE_REGRESSION_DEMO",
        "implementation_commit": terminal["implementation_commit"],
        "standing_authorization_sha256": file_sha256(
            top.root / "authorization.json"
        ),
        "pre_live_admission_sha256": file_sha256(
            top.root / "pre-live-admission.json"
        ),
        "baseline_window_sha256": [
            canonical_sha256(item.model_dump(mode="json"))
            for item in baseline_windows
        ],
        "pre_fault_counters": {
            "fault_injections": 0,
            "model_calls": 0,
            "forward_mutations": 0,
            "rollback_mutations": 0,
        },
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
    path = roots.control / "accepted-local-demo-run.json"
    digest = write_private_json(path, value, create_once=True)
    terminal["accepted_live_run_sealed"] = True
    terminal["accepted_live_run_sha256"] = digest
    return digest


def verify_success_terminal(
    config: LocalDemoConfig, terminal: Mapping[str, object]
) -> None:
    cleanup = terminal.get("cleanup")
    if terminal.get("verdict") != config.authority.invocation_b_success:
        return
    if any(
        (
            terminal.get("fault_injections") != 1,
            terminal.get("provider_calls") != 2,
            terminal.get("model_calls") != 1,
            terminal.get("a0_context_builder_calls") != 1,
            terminal.get("semantic_model_calls") != 1,
            terminal.get("specialist_calls") != 0,
            terminal.get("fusion_calls") != 0,
            terminal.get("provider_attempts") != 1,
            terminal.get("baseline_windows") != 2,
            terminal.get("recovery_window_count") != 2,
            terminal.get("local_demo_gate") is not True,
            terminal.get("local_demo_root_match") is not True,
            terminal.get("local_demo_evidence_valid") is not True,
            terminal.get("local_demo_source_coverage_valid") is not True,
            terminal.get("local_demo_single_call_valid") is not True,
            terminal.get("local_demo_context_binding_valid") is not True,
            terminal.get("standing_authorization_valid") is not True,
            terminal.get("plan_action")
            != "RESTORE_FROZEN_SERVICE_CONFIGURATION",
            terminal.get("policy_verdict") != "ALLOW",
            terminal.get("forward_mutations") != 1,
            terminal.get("rollback_mutations") != 0,
            terminal.get("recovery_verification_passed") is not True,
            not isinstance(cleanup, Mapping),
            cleanup.get("verdict") != "CLEAN"
            if isinstance(cleanup, Mapping)
            else True,
            cleanup.get("baseline_restored") is not True
            if isinstance(cleanup, Mapping)
            else True,
            terminal.get("accepted_live_run_sealed") is not True,
        )
    ):
        raise ValueError("LOCAL_DEMO success terminal does not recompute")


def build_public_result(
    config: LocalDemoConfig, terminal: Mapping[str, object]
) -> dict[str, object]:
    verify_success_terminal(config, terminal)
    if terminal.get("verdict") != config.authority.invocation_b_success:
        raise ValueError("LOCAL_DEMO public result requires a successful terminal")
    cleanup = cast(Mapping[str, object], terminal["cleanup"])
    result: dict[str, object] = {
        "schema_version": "live-e2e.local-demo-public-result.v1",
        "mode": "LOCAL_DEMO",
        "classification": "POST_FAILURE_REGRESSION_DEMO",
        "verdict": terminal["verdict"],
        "implementation_commit": terminal["implementation_commit"],
        "result_head": terminal["result_head"],
        "model": terminal.get("model"),
        "source_availability": terminal.get("source_availability", {}),
        "source_counts": terminal.get("source_counts", {}),
        "invalid_refs": terminal.get("invalid_refs"),
        "strict_expected_root_service": terminal.get(
            "strict_expected_root_service"
        ),
        "strict_expected_fault_class": terminal.get(
            "strict_expected_fault_class"
        ),
        "predicted_root_service": terminal.get("predicted_root_service"),
        "predicted_fault_class": terminal.get("predicted_fault_class"),
        "strict_audit_pass": terminal.get("strict_audit_pass"),
        "strict_reason_codes": terminal.get("strict_reason_codes", []),
        "local_demo_gate": terminal.get("local_demo_gate"),
        "local_demo_warnings": terminal.get("local_demo_warning_codes", []),
        "context_binding_valid": terminal.get(
            "local_demo_context_binding_valid"
        ),
        "fault_injections": terminal.get("fault_injections"),
        "model_calls": terminal.get("model_calls"),
        "semantic_model_calls": terminal.get("semantic_model_calls"),
        "specialist_calls": terminal.get("specialist_calls"),
        "fusion_calls": terminal.get("fusion_calls"),
        "forward_mutations": terminal.get("forward_mutations"),
        "rollback_mutations": terminal.get("rollback_mutations"),
        "baseline_windows": terminal.get("baseline_windows"),
        "recovery_windows": terminal.get("recovery_window_count"),
        "independent_verification": terminal.get(
            "recovery_verification_passed"
        ),
        "standing_authorization_valid": terminal.get(
            "standing_authorization_valid"
        ),
        "codex_autonomous_self_approval": terminal.get(
            "codex_autonomous_self_approval"
        ),
        "cleanup": dict(cleanup),
        "claim_boundary": list(config.reporting.claim_boundary),
    }
    result["semantic_sha256"] = canonical_sha256(result)
    return result


def _write_public_outputs(
    config: LocalDemoConfig,
    roots: E2EV6PrivateRoots,
    terminal: Mapping[str, object],
) -> tuple[str, ...]:
    if terminal.get("verdict") != config.authority.invocation_b_success:
        return ()
    sealed = _read_mapping(
        roots.invocation_b / "terminal.json", label="sealed terminal"
    )
    if sealed != dict(terminal):
        raise ValueError("LOCAL_DEMO supplied terminal differs from sealed terminal")
    public = build_public_result(config, sealed)
    paths = (
        config.repository_root / config.reporting.public_result_json,
        config.repository_root / config.reporting.public_result_markdown,
        config.repository_root / config.reporting.public_human_brief,
    )
    payloads = (
        canonical_json_bytes(public),
        (
            "# LOCAL_DEMO Fault-to-Recovery E2E\n\n"
            f"**Verdict:** `{public['verdict']}`\n\n"
            "Post-failure regression demo on one known local payment scenario. "
            "The strict fault-class audit remains visible, while remediation "
            "admission is independently governed by the LOCAL_DEMO root, "
            "evidence, call-shape, and context-binding Gate. This is not a "
            "held-out benchmark or production autonomy claim.\n"
        ).encode("utf-8"),
        (
            "# LOCAL_DEMO 本地故障到恢复 E2E — Human Brief\n\n"
            "本结果是已知 payment 场景的 `POST_FAILURE_REGRESSION_DEMO`："
            "保留 Strict Diagnosis Gate 的 fault-class 审计结论，同时仅由"
            "独立 LOCAL_DEMO Gate 准入冻结白名单恢复动作。它不代表 held-out "
            "RCA 指标、生产自治或 Multi-Agent 优越性。\n"
        ).encode("utf-8"),
    )
    for path, payload in zip(paths, payloads, strict=True):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise FileExistsError(f"LOCAL_DEMO public projection differs: {path}")
    for path, payload in zip(paths, payloads, strict=True):
        if not path.exists():
            e2e_v6._write_new_public(path, payload)
    return tuple(
        path.relative_to(config.repository_root).as_posix() for path in paths
    )


def run_local_demo(
    config: LocalDemoConfig,
    top: LocalDemoPrivateRoot,
    *,
    provider_environment: Mapping[str, str],
) -> dict[str, object]:
    """Run no-fault readiness and one full local-demo attempt on one clean head."""

    implementation_commit = verify_local_demo_worktree(config, True)
    pre_live = top.require_pre_live_admission(implementation_commit)
    authorization = top.ensure_standing_authorization(config)
    if provider_environment.get("ECOMSRE_LLM_MODEL") != config.authority.a0_model:
        raise RuntimeError("LOCAL_DEMO Provider model differs from the configured lock")
    runtime_sha = canonical_sha256(
        {
            "local_config": local_runtime_config_sha256(config),
            "implementation_commit": implementation_commit,
            "pre_live_admission": canonical_sha256(pre_live),
        }
    )
    attempt = top.allocate_attempt(
        implementation_commit=implementation_commit,
        runtime_config_sha256=runtime_sha,
    )
    roots = E2EV6PrivateRoots(attempt / "evidence")
    development = e2e_v6.run_development_probe(
        cast(Any, config), cast(Any, roots)
    )
    if development.get("verdict") != config.authority.development_success_terminal:
        top.complete_attempt(attempt, development)
        return development

    completed = False

    def complete(
        current_config: object,
        current_roots: object,
        terminal: Mapping[str, object],
    ) -> None:
        nonlocal completed
        del current_config, current_roots
        top.complete_attempt(attempt, terminal)
        completed = True
        if terminal.get("verdict") == config.authority.invocation_b_success:
            write_private_json(
                top.root / "final" / "accepted-attempt.json",
                {
                    "schema_version": "live-e2e.local-demo-final-pointer.v1",
                    "attempt_relative_path": attempt.relative_to(top.root).as_posix(),
                    "terminal_sha256": file_sha256(
                        roots.invocation_b / "terminal.json"
                    ),
                    "verdict": terminal.get("verdict"),
                },
                create_once=True,
            )

    try:
        terminal = e2e_v6.run_invocation_b(
            cast(Any, config),
            cast(Any, roots),
            provider_factory=lambda current: make_provider(
                cast(Any, current).sandbox, provider_environment
            ),
            invocation_authority_loader=lambda current, current_roots, now: (
                _load_invocation_authority(
                    cast(LocalDemoConfig, current),
                    cast(E2EV6PrivateRoots, current_roots),
                    top,
                    authorization,
                )
            ),
            exact_head_admission_verifier=lambda current_roots, head: (
                top.require_pre_live_admission(head)
            ),
            scenario_lock_verifier=lambda current, current_roots, locked, head: (
                _verify_scenario_lock(
                    cast(LocalDemoConfig, current),
                    cast(E2EV6PrivateRoots, current_roots),
                    locked,
                    head,
                )
            ),
            diagnosis_admission_evaluator=_local_admission,
            diagnosis_lineage_writer=lambda provider, context, diagnosis, strict, local: (
                _write_diagnosis_lineage(
                    roots, provider, context, diagnosis, strict, local
                )
            ),
            accepted_run_sealer=lambda current, current_roots, terminal, windows: (
                _seal_accepted_run(
                    cast(LocalDemoConfig, current),
                    cast(E2EV6PrivateRoots, current_roots),
                    top,
                    terminal,
                    windows,
                )
            ),
            pre_seal_terminal_verifier=lambda current, terminal: (
                verify_success_terminal(cast(LocalDemoConfig, current), terminal)
            ),
            live_attempt_completer=complete,
            public_writer=lambda current, terminal: _write_public_outputs(
                cast(LocalDemoConfig, current), roots, terminal
            ),
        )
    except Exception:
        if not completed:
            terminal_path = roots.invocation_b / "terminal.json"
            if terminal_path.is_file() and not terminal_path.is_symlink():
                top.complete_attempt(
                    attempt,
                    _read_mapping(terminal_path, label="failed terminal"),
                )
        raise
    return terminal


__all__ = [
    "build_public_result",
    "run_local_demo",
    "verify_local_demo_worktree",
    "verify_success_terminal",
]
