"""Frozen private schedules and fresh-provider development execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval.contracts import Architecture, ScheduledRun, TerminalRecord
from ecomsre_rcaeval.dataset import DevCase, DevSystem, discover_dev_cases
from ecomsre_rcaeval.protocol import verify_prompt_lock as verify_v1_prompt_lock
from ecomsre_rcaeval.provider import OpenAICompatibleRCAEvalProvider
from ecomsre_rcaeval.runner import execute_scheduled_once
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.contracts import TerminalRecordV2, V2Model
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.locks import V2_CONFIG, verify_model_prompt_lock
from ecomsre_rcaeval_v2.provider import OpenAICompatibleRCAEvalV2Provider
from ecomsre_rcaeval_v2.runner import execute_v2_scheduled_once
from ecomsre_rcaeval_v2.schedule import (
    SPLIT_SEED,
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
_FORBIDDEN_PATH_MARKERS = (
    "re2-tt",
    "tt-case-",
    "holdout-sanitized",
    "evaluator-only",
    "ground-truth.json",
    "scored_cases",
    "/attribution/",
)


class PrivateScheduleManifest(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.private-schedule.v1"]
    split: SplitName
    seed: Literal[20260807]
    records: tuple[ScheduleRecord, ...]


class PrivateScheduleSet(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev.private-schedule-set.v1"]
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
    design = build_schedule(assignments, SplitName.DESIGN, seed=SPLIT_SEED)
    validation = build_schedule(
        assignments, SplitName.DEV_VALIDATION, seed=SPLIT_SEED
    )
    smoke = build_smoke_schedule(assignments, design, seed=SPLIT_SEED)
    manifests = {
        "design-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev.private-schedule.v1",
            split=SplitName.DESIGN,
            seed=20260807,
            records=design,
        ),
        "dev-validation-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev.private-schedule.v1",
            split=SplitName.DEV_VALIDATION,
            seed=20260807,
            records=validation,
        ),
        "smoke-schedule.json": PrivateScheduleManifest(
            schema_version="rcaeval-re2-v2-dev.private-schedule.v1",
            split=SplitName.DESIGN,
            seed=20260807,
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
        schema_version="rcaeval-re2-v2-dev.private-schedule-set.v1",
        design_schedule_sha256=_sha(payloads["design-schedule.json"]),
        dev_validation_schedule_sha256=_sha(
            payloads["dev-validation-schedule.json"]
        ),
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


def load_private_schedule(path: Path) -> tuple[ScheduleRecord, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("private schedule manifest is invalid")
    manifest = PrivateScheduleManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    return _SCHEDULE_ADAPTER.validate_python(manifest.records)


def discover_case_index(
    ob_root: Path, ss_root: Path
) -> dict[CaseIdentity, DevCase]:
    for path in (ob_root, ss_root):
        normalized = str(path).casefold()
        if any(marker in normalized for marker in _FORBIDDEN_PATH_MARKERS):
            raise ValueError("development execution path contains a forbidden marker")
    cases = discover_dev_cases(ob_root, DevSystem.RE2_OB) + discover_dev_cases(
        ss_root, DevSystem.RE2_SS
    )
    indexed = {
        CaseIdentity(
            system=case.system,  # type: ignore[arg-type]
            root_cause_service=case.root_cause_service,
            fault=case.fault,
            instance=case.instance,
        ): case
        for case in cases
    }
    if len(indexed) != 180:
        raise ValueError("development execution requires 180 unique OB/SS cases")
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


def new_v2_provider(config: OpenAICompatibleConfig) -> OpenAICompatibleRCAEvalV2Provider:
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
    if lock.get("model") != model or lock.get("max_completion_tokens") != max_completion:
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
        schedule_seed=SPLIT_SEED,
    )


def execute_development_schedule(
    schedule: tuple[ScheduleRecord, ...],
    *,
    cases: Mapping[CaseIdentity, DevCase],
    provider_config: OpenAICompatibleConfig,
    private_run_root: Path,
    progress: Callable[
        [int, int, ScheduleRecord, TerminalRecord | TerminalRecordV2], None
    ]
    | None = None,
) -> tuple[TerminalRecord | TerminalRecordV2, ...]:
    """Execute in frozen order with a fresh provider object for every run."""

    formula_path = V2_CONFIG / "indicator-candidate-formulas.json"
    formula = load_indicator_config(
        formula_path, expected_sha256=_sha(formula_path.read_bytes())
    )
    records: list[TerminalRecord | TerminalRecordV2] = []
    for index, scheduled in enumerate(schedule, 1):
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
        if progress is not None:
            progress(index, len(schedule), scheduled, record)
    return tuple(records)
