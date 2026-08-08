"""Frozen schedule persistence and Provider execution for v2-dev.2."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval.contracts import TerminalRecord
from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval.protocol import verify_prompt_lock as verify_v1_prompt_lock
from ecomsre_rcaeval.provider import OpenAICompatibleRCAEvalProvider
from ecomsre_rcaeval.runner import execute_scheduled_once
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.contracts import TerminalRecordV2, V2Model
from ecomsre_rcaeval_v2.dev2_admission import ScheduleAdmissionLock, v1_scheduled_run
from ecomsre_rcaeval_v2.dev2_evaluation_root import (
    EvaluationRootLock,
    verify_evaluation_root,
    verify_provider_ready,
)
from ecomsre_rcaeval_v2.dev2_evidence import verify_passing_smoke_gate
from ecomsre_rcaeval_v2.dev2_schedule import (
    PROTOCOL_ID,
    SCHEDULE_SEED,
    ArchitectureFamily,
    ScheduleRecord,
    Variant,
    as_dev1_runtime_record,
    build_schedule,
    build_smoke_schedule,
)
from ecomsre_rcaeval_v2.dev_execution import (
    discover_case_index,
    provider_config_from_env_file as _provider_config_from_env_file,
)
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.locks import LEGACY_V2_CONFIG, PROJECT_ROOT
from ecomsre_rcaeval_v2.observability import verify_terminal_run_journal
from ecomsre_rcaeval_v2.dev2_paths import (
    journal_root_for,
    reject_dev2_forbidden_paths,
)
from ecomsre_rcaeval_v2.provider import OpenAICompatibleRCAEvalV2Provider
from ecomsre_rcaeval_v2.runner import execute_v2_scheduled_once
from ecomsre_rcaeval_v2.schedule import CaseIdentity, SplitAssignment, SplitName


DEV2_CONFIG = PROJECT_ROOT / "config" / "rcaeval-re2-v2-dev2"
_SCHEDULE_ADAPTER: TypeAdapter[tuple[ScheduleRecord, ...]] = TypeAdapter(
    tuple[ScheduleRecord, ...]
)
_V2_VARIANTS = {Variant.SINGLE_V2, Variant.FIXED_V2, Variant.DYNAMIC_V2}
_V2_ARCHITECTURES = {
    Variant.SINGLE_V2: "single_v2",
    Variant.FIXED_V2: "fixed_v2",
    Variant.DYNAMIC_V2: "dynamic_v2",
}
_MAX_PROVIDER_OPERATIONS = {
    Variant.SINGLE_V1_REFERENCE: 1,
    Variant.FIXED_V1_REFERENCE: 4,
    Variant.DYNAMIC_V1_REFERENCE: 5,
    Variant.SINGLE_V2: 1,
    Variant.FIXED_V2: 4,
    Variant.DYNAMIC_V2: 5,
}
_EXECUTION_PHASES: dict[str, tuple[int, int]] = {
    "smoke": (72, 240),
    "design": (360, 1200),
}


class PrivateScheduleManifest(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev2.private-schedule.v1"]
    split: SplitName
    seed: Literal[20260809]
    records: tuple[ScheduleRecord, ...]


class PrivateScheduleSet(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev2.private-schedule-set.v1"]
    design_schedule_sha256: str
    dev_validation_schedule_sha256: str
    smoke_schedule_sha256: str
    design_run_count: Literal[360]
    dev_validation_run_count: Literal[480]
    smoke_run_count: Literal[72]


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_private_create_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing dev2 private schedule artifact differs")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def freeze_private_schedules(
    assignments: tuple[SplitAssignment, ...], private_schedule_root: Path
) -> PrivateScheduleSet:
    reject_dev2_forbidden_paths(private_schedule_root)
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    validation = build_schedule(assignments, SplitName.DEV_VALIDATION, seed=SCHEDULE_SEED)
    smoke = build_smoke_schedule(assignments, design, seed=SCHEDULE_SEED)
    manifests = {
        "design-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev2.private-schedule.v1",
            split=SplitName.DESIGN,
            seed=20260809,
            records=design,
        ),
        "dev-validation-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev2.private-schedule.v1",
            split=SplitName.DEV_VALIDATION,
            seed=20260809,
            records=validation,
        ),
        "smoke-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev2.private-schedule.v1",
            split=SplitName.DESIGN,
            seed=20260809,
            records=smoke,
        ),
    }
    payloads = {
        name: _canonical_bytes(manifest.model_dump(mode="json"))
        for name, manifest in manifests.items()
    }
    for name, payload in payloads.items():
        _write_private_create_once(private_schedule_root / name, payload)
    result = PrivateScheduleSet(
        schema_version="rcaeval-re2-v2-dev2.private-schedule-set.v1",
        design_schedule_sha256=_sha(payloads["design-schedule.json"]),
        dev_validation_schedule_sha256=_sha(payloads["dev-validation-schedule.json"]),
        smoke_schedule_sha256=_sha(payloads["smoke-schedule.json"]),
        design_run_count=360,
        dev_validation_run_count=480,
        smoke_run_count=72,
    )
    _write_private_create_once(
        private_schedule_root / "schedule-set-lock.json",
        _canonical_bytes(result.model_dump(mode="json")),
    )
    return result


def load_private_schedule(
    path: Path, *, allowed_split: SplitName | None = None
) -> tuple[ScheduleRecord, ...]:
    reject_dev2_forbidden_paths(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("dev2 private schedule manifest is invalid")
    manifest = PrivateScheduleManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if allowed_split is not None and manifest.split is not allowed_split:
        raise ValueError("dev2 private schedule split is not authorized")
    return _SCHEDULE_ADAPTER.validate_python(manifest.records)


def _load_locked_phase_schedule(
    control_root: Path,
    phase: Literal["smoke", "design"],
    *,
    evaluation: EvaluationRootLock,
    admission: ScheduleAdmissionLock,
) -> tuple[ScheduleRecord, ...]:
    name = "smoke-schedule.json" if phase == "smoke" else "design-schedule.json"
    path = control_root / "schedules" / name
    observed_sha = _sha(path.read_bytes())
    expected_sha = (
        evaluation.smoke_schedule_sha256
        if phase == "smoke"
        else evaluation.design_schedule_sha256
    )
    admission_sha = (
        admission.smoke_schedule_sha256
        if phase == "smoke"
        else admission.design_schedule_sha256
    )
    if observed_sha != expected_sha or observed_sha != admission_sha:
        raise ValueError("dev2 locked execution schedule hash drift")
    schedule = load_private_schedule(path, allowed_split=SplitName.DESIGN)
    expected_count = 72 if phase == "smoke" else 360
    if len(schedule) != expected_count:
        raise ValueError("dev2 locked execution schedule count drift")
    return schedule


def load_locked_phase_schedule(
    control_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    phase: Literal["smoke", "design"],
    *,
    project_root: Path = PROJECT_ROOT,
    preserved_roots: Mapping[str, Path],
) -> tuple[ScheduleRecord, ...]:
    evaluation, admission = verify_provider_ready(
        control_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=project_root,
        preserved_roots=preserved_roots,
    )
    return _load_locked_phase_schedule(
        control_root, phase, evaluation=evaluation, admission=admission
    )


def extract_run_ids(path: Path) -> set[str]:
    """Read run IDs from a preserved schedule without accepting path-like values."""

    reject_dev2_forbidden_paths(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("preserved schedule is missing or invalid")
    text = path.read_text(encoding="utf-8")
    forbidden_content = ("re2-tt", "tt-case-", "holdout-sanitized", "evaluator-only", "ground-truth")
    if any(marker in text.casefold() for marker in forbidden_content):
        raise ValueError("preserved schedule contains a forbidden TT/private marker")
    value = json.loads(text)
    found: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            run_id = item.get("run_id")
            if isinstance(run_id, str) and len(run_id) == 32 and all(c in "0123456789abcdef" for c in run_id):
                found.add(run_id)
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    if not found:
        raise ValueError("preserved schedule contains no run IDs")
    return found


def _expected_model_prompt_lock() -> dict[str, object]:
    dev1 = json.loads(
        (PROJECT_ROOT / "config/rcaeval-re2-v2-dev1/model-prompt-lock.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        **dev1,
        "schema_version": "rcaeval-re2-v2-dev2.model-prompt-lock.v1",
        "protocol_id": PROTOCOL_ID,
    }


def verify_model_prompt_lock() -> dict[str, object]:
    observed = json.loads((DEV2_CONFIG / "model-prompt-lock.json").read_text(encoding="utf-8"))
    expected = _expected_model_prompt_lock()
    if observed != expected:
        raise ValueError("dev2 model/prompt lock is not byte-equivalent to dev1 contracts")
    return observed


def provider_config_from_env_file(path: Path) -> OpenAICompatibleConfig:
    reject_dev2_forbidden_paths(path)
    return _provider_config_from_env_file(path)


def _provider_limits() -> tuple[str, float, int]:
    lock = verify_model_prompt_lock()
    budget = json.loads((DEV2_CONFIG / "budget-lock.json").read_text(encoding="utf-8"))
    model = lock.get("model")
    max_completion = lock.get("max_completion_tokens")
    timeout = budget.get("model_call_timeout_seconds")
    if not isinstance(model, str) or type(max_completion) is not int or not isinstance(timeout, (int, float)):
        raise ValueError("dev2 Provider lock is invalid")
    return model, float(timeout), max_completion


def new_v2_provider(config: OpenAICompatibleConfig) -> OpenAICompatibleRCAEvalV2Provider:
    model, timeout, max_completion = _provider_limits()
    return OpenAICompatibleRCAEvalV2Provider(
        config=config,
        expected_model=model,
        timeout_seconds=timeout,
        max_completion_tokens=max_completion,
    )


def new_v1_reference_provider(config: OpenAICompatibleConfig) -> OpenAICompatibleRCAEvalProvider:
    v1_lock = verify_v1_prompt_lock()
    model, timeout, max_completion = _provider_limits()
    if v1_lock.get("model") != model or v1_lock.get("max_completion_tokens") != max_completion:
        raise ValueError("v1 and dev2 model/provider locks differ")
    return OpenAICompatibleRCAEvalProvider(
        config=config,
        expected_model=model,
        timeout_seconds=timeout,
        max_completion_tokens=max_completion,
    )


def _reuse_terminal_if_present(
    record: ScheduleRecord,
    case: DevCase,
    private_run_root: Path,
) -> TerminalRecord | TerminalRecordV2 | None:
    if record.architecture_family is ArchitectureFamily.V1_REFERENCE:
        scheduled = v1_scheduled_run(record, case)
        terminal_path = private_run_root / "v1-terminal-records" / f"{record.run_id}.json"
        if not terminal_path.exists():
            return None
        attempt_path = (
            private_run_root
            / "v1-terminal-records.attempts"
            / f"{record.run_id}.json"
        )
        if any(path.is_symlink() or not path.is_file() for path in (terminal_path, attempt_path)):
            raise ValueError("dev2 reused v1 terminal or attempt is invalid")
        v1_terminal = TerminalRecord.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        expected_attempt = {
            "schema_version": "rcaeval-re2.semantic-attempt.v1",
            "run_id": record.run_id,
            "case_id": case.case_id,
            "architecture": scheduled.architecture.value,
            "max_semantic_attempts": 1,
        }
        if v1_terminal.run_id != record.run_id or v1_terminal.case_id != case.case_id or v1_terminal.architecture is not scheduled.architecture or attempt != expected_attempt:
            raise ValueError("dev2 reused v1 terminal differs from schedule")
        return v1_terminal
    run_root = private_run_root / "v2-runs" / record.run_id
    if not (run_root / "terminal-record.json").exists():
        return None
    v2_terminal, _operations = verify_terminal_run_journal(run_root)
    if v2_terminal.run_id != record.run_id or v2_terminal.case_id != case.case_id or v2_terminal.system != record.identity.system or v2_terminal.architecture != _V2_ARCHITECTURES[record.variant]:
        raise ValueError("dev2 reused v2 terminal differs from schedule")
    return v2_terminal


def execute_development_schedule(
    schedule: tuple[ScheduleRecord, ...],
    *,
    cases: Mapping[CaseIdentity, DevCase],
    provider_config: OpenAICompatibleConfig,
    control_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    execution_phase: Literal["smoke", "design"],
    preserved_roots: Mapping[str, Path],
    progress: Callable[[int, int, ScheduleRecord, TerminalRecord | TerminalRecordV2], None] | None = None,
) -> tuple[TerminalRecord | TerminalRecordV2, ...]:
    """Execute frozen order, after both locks, with one semantic attempt per run."""

    evaluation, admission = verify_provider_ready(
        control_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        project_root=PROJECT_ROOT,
        preserved_roots=preserved_roots,
    )
    locked_schedule = _load_locked_phase_schedule(
        control_root,
        execution_phase,
        evaluation=evaluation,
        admission=admission,
    )
    if schedule != locked_schedule:
        raise ValueError("dev2 execution rows differ from the admitted locked schedule")
    if execution_phase == "design":
        verify_passing_smoke_gate(
            control_root / "evidence" / "provider-smoke-gate.json",
            control_root=control_root,
            output_root=output_root,
            smoke_journal_root=smoke_journal_root,
            design_journal_root=design_journal_root,
            project_root=PROJECT_ROOT,
            smoke_schedule=_load_locked_phase_schedule(
                control_root,
                "smoke",
                evaluation=evaluation,
                admission=admission,
            ),
        )
    expected_runs, operation_cap = _EXECUTION_PHASES[execution_phase]
    if len(schedule) != expected_runs:
        raise ValueError("dev2 execution schedule count differs from phase")
    if sum(_MAX_PROVIDER_OPERATIONS[item.variant] for item in schedule) > operation_cap:
        raise ValueError("dev2 schedule exceeds Provider operation cap")
    indicator_lock = json.loads((DEV2_CONFIG / "indicator-lock.json").read_text(encoding="utf-8"))
    expected_formula_sha = indicator_lock.get("inherited_formula_config_sha256")
    if indicator_lock.get("selected_formula") != "F0" or indicator_lock.get("formula_reselection_performed") is not False or not isinstance(expected_formula_sha, str):
        raise ValueError("dev2 inherited F0 lock is invalid")
    formula = load_indicator_config(
        LEGACY_V2_CONFIG / "indicator-candidate-formulas.json",
        expected_sha256=expected_formula_sha,
    )
    output: list[TerminalRecord | TerminalRecordV2] = []
    provider_operations = 0
    smoke_ids = {
        record.run_id
        for record in _load_locked_phase_schedule(
            control_root,
            "smoke",
            evaluation=evaluation,
            admission=admission,
        )
    }
    for index, record in enumerate(schedule, 1):
        verify_evaluation_root(
            control_root,
            output_root,
            smoke_journal_root,
            design_journal_root,
            project_root=PROJECT_ROOT,
        )
        case = cases.get(record.identity)
        if case is None:
            raise ValueError("dev2 schedule identity is absent from DESIGN cases")
        journal_root = journal_root_for(
            record,
            phase=execution_phase,
            smoke_run_ids=smoke_ids,
            smoke_journal_root=smoke_journal_root,
            design_journal_root=design_journal_root,
        )
        reused = _reuse_terminal_if_present(record, case, journal_root)
        terminal: TerminalRecord | TerminalRecordV2
        if reused is not None:
            terminal = reused
        elif record.architecture_family is ArchitectureFamily.V1_REFERENCE:
            terminal = execute_scheduled_once(
                v1_scheduled_run(record, case),
                dev_case_to_telemetry_case(case),
                new_v1_reference_provider(provider_config),
                journal_root / "v1-terminal-records",
            )
        elif record.variant in _V2_VARIANTS:
            terminal = execute_v2_scheduled_once(
                as_dev1_runtime_record(record),
                dev_case_to_telemetry_case(case),
                case_identity_sha256=_sha(
                    b"\0".join(
                        value.encode()
                        for value in (
                            record.identity.system,
                            record.identity.root_cause_service,
                            record.identity.fault,
                            record.identity.instance,
                        )
                    )
                ),
                provider=new_v2_provider(provider_config),
                indicator_formula=FormulaId.F0,
                indicator_config=formula,
                run_root=journal_root / "v2-runs" / record.run_id,
            )
        else:
            raise ValueError("dev2 schedule contains an unknown variant")
        output.append(terminal)
        provider_operations += terminal.model_calls if isinstance(terminal, TerminalRecord) else terminal.usage.model_calls_delta
        if provider_operations > operation_cap:
            raise ValueError("dev2 execution exceeded Provider operation cap")
        if progress is not None:
            progress(index, len(schedule), record, terminal)
    return tuple(output)


__all__ = [
    "PrivateScheduleSet",
    "as_dev1_runtime_record",
    "discover_case_index",
    "execute_development_schedule",
    "extract_run_ids",
    "freeze_private_schedules",
    "load_private_schedule",
    "load_locked_phase_schedule",
    "provider_config_from_env_file",
]
