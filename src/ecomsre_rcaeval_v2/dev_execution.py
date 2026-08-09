"""Frozen private schedules and fresh-provider development execution."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval.contracts import Architecture, ScheduledRun, TerminalRecord
from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval.protocol import verify_prompt_lock as verify_v1_prompt_lock
from ecomsre_rcaeval.provider import OpenAICompatibleRCAEvalProvider
from ecomsre_rcaeval.runner import execute_scheduled_once
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.contracts import TerminalRecordV2, V2Model
from ecomsre_rcaeval_v2.evaluation_root import verify_evaluation_root
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.locks import (
    LEGACY_V2_CONFIG,
    PROJECT_ROOT,
    V2_CONFIG,
    verify_model_prompt_lock,
)
from ecomsre_rcaeval_v2.provider import OpenAICompatibleRCAEvalV2Provider
from ecomsre_rcaeval_v2.runner import execute_v2_scheduled_once
from ecomsre_rcaeval_v2.schedule import (
    SCHEDULE_SEED,
    CaseIdentity,
    ScheduleRecord,
    SplitAssignment,
    SplitAssignmentManifest,
    SplitName,
    Variant,
    build_schedule,
    build_smoke_schedule,
    case_identity_bytes,
)


_SCHEDULE_ADAPTER: TypeAdapter[tuple[ScheduleRecord, ...]] = TypeAdapter(
    tuple[ScheduleRecord, ...]
)
_ENV_NAMES = (
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_MODEL",
)
_V1_ARCHITECTURES = {
    Variant.SINGLE_V1_REFERENCE: Architecture.SINGLE,
    Variant.FIXED_V1_REFERENCE: Architecture.FIXED,
    Variant.DYNAMIC_V1_REFERENCE: Architecture.DYNAMIC,
}
_V2_VARIANTS = {
    Variant.SINGLE_V2,
    Variant.FIXED_V2,
    Variant.DYNAMIC_V2,
}
_EXECUTION_PHASES: dict[str, tuple[int, int]] = {
    "smoke": (72, 240),
    "design": (360, 1200),
}
_MAX_PROVIDER_OPERATIONS = {
    Variant.SINGLE_V1_REFERENCE: 1,
    Variant.FIXED_V1_REFERENCE: 4,
    Variant.DYNAMIC_V1_REFERENCE: 5,
    Variant.SINGLE_V2: 1,
    Variant.FIXED_V2: 4,
    Variant.DYNAMIC_V2: 5,
}
_FORBIDDEN_PATH_MARKERS = (
    "re2-tt",
    "tt-case-",
    "holdout-sanitized",
    "evaluator-only",
    "ground-truth.json",
    "scored_cases",
    "/attribution/",
)
_ROOT_SERVICES = {
    "RE2-OB": (
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "productcatalogservice",
        "recommendationservice",
    ),
    "RE2-SS": ("carts", "catalogue", "orders", "payment", "user"),
}
_FAULTS = ("cpu", "mem", "disk", "delay", "loss", "socket")


class PrivateScheduleManifest(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev1.private-schedule.v1"]
    split: SplitName
    seed: Literal[20260808]
    records: tuple[ScheduleRecord, ...]


class PrivateScheduleSet(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev1.private-schedule-set.v1"]
    design_schedule_sha256: str
    dev_validation_schedule_sha256: str
    smoke_schedule_sha256: str
    design_run_count: Literal[360]
    dev_validation_run_count: Literal[480]
    smoke_run_count: Literal[72]


def _canonical_bytes(value: object) -> bytes:
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


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_private_create_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError("existing private schedule artifact differs")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def load_split_assignments(path: Path) -> tuple[SplitAssignment, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("private split assignment manifest is invalid")
    manifest = SplitAssignmentManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if len(manifest.assignments) != 180:
        raise ValueError("private split assignment manifest is incomplete")
    return manifest.assignments


def freeze_private_schedules(
    assignments: tuple[SplitAssignment, ...], private_schedule_root: Path
) -> PrivateScheduleSet:
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    validation = build_schedule(
        assignments, SplitName.DEV_VALIDATION, seed=SCHEDULE_SEED
    )
    smoke = build_smoke_schedule(assignments, design, seed=SCHEDULE_SEED)
    manifests = {
        "design-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev1.private-schedule.v1",
            split=SplitName.DESIGN,
            seed=20260808,
            records=design,
        ),
        "dev-validation-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev1.private-schedule.v1",
            split=SplitName.DEV_VALIDATION,
            seed=20260808,
            records=validation,
        ),
        "smoke-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev1.private-schedule.v1",
            split=SplitName.DESIGN,
            seed=20260808,
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
        schema_version="rcaeval-re2-v2-dev1.private-schedule-set.v1",
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
    if path.is_symlink() or not path.is_file():
        raise ValueError("private schedule manifest is invalid")
    manifest = PrivateScheduleManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if allowed_split is not None and manifest.split is not allowed_split:
        raise ValueError("private schedule split is not authorized for this command")
    return _SCHEDULE_ADAPTER.validate_python(manifest.records)


def discover_case_index(
    ob_root: Path,
    ss_root: Path,
    identities: Collection[CaseIdentity],
) -> dict[CaseIdentity, DevCase]:
    """Open only schedule-selected DESIGN cases, never reserved validation rows."""

    for path in (ob_root, ss_root):
        normalized = str(path).casefold()
        if any(marker in normalized for marker in _FORBIDDEN_PATH_MARKERS):
            raise ValueError("development execution path contains a forbidden marker")
    if ob_root.name != "RE2-OB" or ss_root.name != "RE2-SS":
        raise ValueError("development dataset root name mismatch")
    if any(path.is_symlink() or not path.is_dir() for path in (ob_root, ss_root)):
        raise ValueError("development dataset root is invalid")
    roots = {"RE2-OB": ob_root, "RE2-SS": ss_root}
    selected = set(identities)
    if not selected:
        raise ValueError("development execution requires selected DESIGN identities")
    indexed: dict[CaseIdentity, DevCase] = {}
    for identity in sorted(
        selected,
        key=lambda item: (
            item.system,
            item.root_cause_service,
            item.fault,
            item.instance,
        ),
    ):
        if identity.root_cause_service not in _ROOT_SERVICES[identity.system]:
            raise ValueError("scheduled development service is not locked")
        if identity.fault not in _FAULTS or identity.instance not in {"1", "2", "3"}:
            raise ValueError("scheduled development stratum is not locked")
        case_root = (
            roots[identity.system]
            / f"{identity.root_cause_service}_{identity.fault}"
            / identity.instance
        )
        if case_root.is_symlink() or not case_root.is_dir():
            raise ValueError("scheduled development case root is invalid")
        metrics_candidates = tuple(
            path
            for path in (
                case_root / "simple_metrics.csv",
                case_root / "data.csv",
            )
            if path.exists()
        )
        if len(metrics_candidates) != 1:
            raise ValueError("scheduled development case has invalid metrics")
        metrics_path = metrics_candidates[0]
        logs_path = case_root / "logs.csv"
        traces_candidate = case_root / "traces.csv"
        required = (metrics_path, logs_path, case_root / "inject_time.txt")
        if any(path.is_symlink() or not path.is_file() for path in required):
            raise ValueError("scheduled development case artifact is invalid")
        if identity.system == "RE2-OB":
            if traces_candidate.is_symlink() or not traces_candidate.is_file():
                raise ValueError("scheduled RE2-OB case requires traces")
            traces_path: Path | None = traces_candidate
        elif traces_candidate.exists():
            raise ValueError("scheduled RE2-SS case unexpectedly contains traces")
        else:
            traces_path = None
        try:
            inject_time = int(
                (case_root / "inject_time.txt")
                .read_text(encoding="utf-8")
                .strip()
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError("scheduled development inject time is invalid") from error
        groups = sorted(
            f"{service}_{fault}"
            for service in _ROOT_SERVICES[identity.system]
            for fault in _FAULTS
        )
        group_index = groups.index(
            f"{identity.root_cause_service}_{identity.fault}"
        )
        case_index = group_index * 3 + int(identity.instance)
        indexed[identity] = DevCase(
            case_id=f"{identity.system.lower()}-case-{case_index:04d}",
            system=identity.system,
            root=case_root,
            metrics_path=metrics_path,
            logs_path=logs_path,
            traces_path=traces_path,
            inject_time=inject_time,
            root_cause_service=identity.root_cause_service,
            fault=identity.fault,
            instance=identity.instance,
        )
    return indexed


def _parse_env_file(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("provider environment file is invalid")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, raw_value = line.partition("=")
        if separator != "=" or key.strip() not in _ENV_NAMES:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    if set(values) != set(_ENV_NAMES) or any(not item for item in values.values()):
        raise ValueError("provider environment file is incomplete")
    return values


def provider_config_from_env_file(path: Path) -> OpenAICompatibleConfig:
    config = OpenAICompatibleConfig.from_environment(_parse_env_file(path))
    if config is None:
        raise ValueError("provider environment is not configured")
    return config


def _provider_limits() -> tuple[str, float, int]:
    lock = verify_model_prompt_lock()
    model = lock.get("model")
    max_completion = lock.get("max_completion_tokens")
    budget = json.loads((V2_CONFIG / "budget-lock.json").read_text(encoding="utf-8"))
    timeout = budget.get("model_call_timeout_seconds")
    if (
        not isinstance(model, str)
        or type(max_completion) is not int
        or not isinstance(timeout, (int, float))
    ):
        raise ValueError("v2 provider lock is invalid")
    return model, float(timeout), max_completion


def new_v2_provider(
    config: OpenAICompatibleConfig,
) -> OpenAICompatibleRCAEvalV2Provider:
    model, timeout, max_completion = _provider_limits()
    return OpenAICompatibleRCAEvalV2Provider(
        config=config,
        expected_model=model,
        timeout_seconds=timeout,
        max_completion_tokens=max_completion,
    )


def new_v1_reference_provider(
    config: OpenAICompatibleConfig,
) -> OpenAICompatibleRCAEvalProvider:
    lock = verify_v1_prompt_lock()
    model, timeout, max_completion = _provider_limits()
    if (
        lock.get("model") != model
        or lock.get("max_completion_tokens") != max_completion
    ):
        raise ValueError("v1 and v2 model/provider locks differ")
    return OpenAICompatibleRCAEvalProvider(
        config=config,
        expected_model=model,
        timeout_seconds=timeout,
        max_completion_tokens=max_completion,
    )


def _v1_scheduled(record: ScheduleRecord, case: DevCase) -> ScheduledRun:
    architecture = _V1_ARCHITECTURES.get(record.variant)
    if architecture is None:
        raise ValueError("v1 reference runner received a v2 variant")
    return ScheduledRun(
        run_id=record.run_id,
        case_id=case.case_id,
        architecture=architecture,
        call_position=record.arm_position,
        schedule_seed=SCHEDULE_SEED,
    )


def execute_development_schedule(
    schedule: tuple[ScheduleRecord, ...],
    *,
    cases: Mapping[CaseIdentity, DevCase],
    provider_config: OpenAICompatibleConfig,
    control_root: Path,
    private_run_root: Path,
    execution_phase: Literal["smoke", "design"],
    progress: Callable[
        [int, int, ScheduleRecord, TerminalRecord | TerminalRecordV2], None
    ]
    | None = None,
) -> tuple[TerminalRecord | TerminalRecordV2, ...]:
    """Execute in frozen order with a fresh provider object for every run."""

    verify_evaluation_root(control_root, private_run_root, project_root=PROJECT_ROOT)
    expected_runs, operation_cap = _EXECUTION_PHASES[execution_phase]
    if len(schedule) != expected_runs:
        raise ValueError("development execution schedule count differs from phase")
    operation_ceiling = sum(
        _MAX_PROVIDER_OPERATIONS[item.variant] for item in schedule
    )
    if operation_ceiling > operation_cap:
        raise ValueError("development schedule exceeds Provider operation cap")
    formula_path = LEGACY_V2_CONFIG / "indicator-candidate-formulas.json"
    indicator_lock = json.loads(
        (V2_CONFIG / "indicator-lock.json").read_text(encoding="utf-8")
    )
    expected_formula_sha256 = indicator_lock.get("inherited_formula_config_sha256")
    if (
        indicator_lock.get("selected_formula") != FormulaId.F0.value
        or indicator_lock.get("formula_reselection_performed") is not False
        or not isinstance(expected_formula_sha256, str)
    ):
        raise ValueError("v2-dev.1 inherited indicator lock is invalid")
    formula = load_indicator_config(
        formula_path, expected_sha256=expected_formula_sha256
    )
    records: list[TerminalRecord | TerminalRecordV2] = []
    provider_operations = 0
    for index, scheduled in enumerate(schedule, 1):
        verify_evaluation_root(
            control_root, private_run_root, project_root=PROJECT_ROOT
        )
        case = cases.get(scheduled.identity)
        if case is None:
            raise ValueError("development schedule identity is absent from dataset")
        record: TerminalRecord | TerminalRecordV2
        if scheduled.variant in _V1_ARCHITECTURES:
            record = execute_scheduled_once(
                _v1_scheduled(scheduled, case),
                dev_case_to_telemetry_case(case),
                new_v1_reference_provider(provider_config),
                private_run_root / "v1-terminal-records",
            )
        elif scheduled.variant in _V2_VARIANTS:
            record = execute_v2_scheduled_once(
                scheduled,
                dev_case_to_telemetry_case(case),
                case_identity_sha256=_sha(case_identity_bytes(scheduled.identity)),
                provider=new_v2_provider(provider_config),
                indicator_formula=FormulaId.F0,
                indicator_config=formula,
                run_root=private_run_root / "v2-runs" / scheduled.run_id,
            )
        else:
            raise ValueError("development schedule contains an unknown variant")
        records.append(record)
        provider_operations += (
            record.model_calls
            if isinstance(record, TerminalRecord)
            else record.usage.model_calls_delta
        )
        if provider_operations > operation_cap:
            raise ValueError("development execution exceeded Provider operation cap")
        if progress is not None:
            progress(index, len(schedule), scheduled, record)
    return tuple(records)
