"""Create-once lifecycle for the bounded B0/H1 consumed-development comparison."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Literal, cast

from ecomsre.evidence.hashes import canonical_json_bytes
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rca100.lifecycle import tree_sha256
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rcaeval_v2.dev3_execution import provider_config_from_env_file
from ecomsre_rcaeval_v2.dev3_evidence import verify_provider_sidecar
from ecomsre_rcaeval_v2.dev3_token_accounting import (
    AttemptBudget,
    rebuild_attempt_accounting,
)
from ecomsre_rca_unified.hierarchical_context import build_hierarchical_context
from ecomsre_rca_unified.hierarchical_context import (
    EvidenceItem,
    HierarchySource,
    LiveBaseContext,
    LiveEntity,
    RelationSource,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.live_comparison import (
    Arm,
    CaseRef,
    EVALUATION_VERSION,
    ScheduledArm,
    build_request_payload,
    paired_schedule,
    prompt_hashes,
)
from ecomsre_rca_unified.live_context_adapters import (
    assert_model_context_private,
    build_obss_live_inputs,
    build_rca100_live_inputs,
    discover_label_blind_dev_cases,
)
from ecomsre_rca_unified.live_runtime import (
    CrossLifecycleRequestPacer,
    LiveRunAttempt,
    LiveTerminalRecord,
    LiveTerminalStatus,
    execute_live_arm,
    seal_interrupted_live_arm,
    terminalize_local_failure,
    terminalize_not_admitted,
    validate_terminal_binding,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "config" / "rca-strong-single-hierarchical-live-v1"
CONTROL_GENERATION = "full-payload-identity-and-lifecycle-bound-v1"
STARTING_MAIN_COMMIT = "867d0336766070bf00f09e8355e5e02b9adafbda"
PR24_HEAD_COMMIT = "5b972b3a3dc00830e0d69df27c38935f3c4846ac"
PR24_FIRST_PARENT = "7c9c51ede0b8589cb7212c9bb440d9811731becd"
CORE_PATHS = (
    "src/ecomsre_rca_unified/hierarchical_context.py",
    "src/ecomsre_rca_unified/live_context_adapters.py",
    "src/ecomsre_rca_unified/live_comparison.py",
    "src/ecomsre_rca_unified/live_evaluation.py",
    "src/ecomsre_rca_unified/live_runtime.py",
    "scripts/rca_live/__init__.py",
    "scripts/rca_live/cli.py",
    "scripts/rca_live/evaluator.py",
    "scripts/rca_live/reporting.py",
    "scripts/rca_live/scan_boundaries.py",
    "src/ecomsre_rca_unified/live_rca100_scan.py",
    "tests/analysis/test_rca_strong_single_hierarchical_live.py",
    "docs/design/strong-single-hierarchical-live-v1-protocol.md",
    "docs/design/strong-single-hierarchical-live-v1-spec.md",
    "docs/DECISIONS.md",
    ".github/workflows/rcaeval-v2-dev.yml",
)
CONTEXT_AUDIT_IMPLEMENTATION_PATHS = (
    "scripts/rca_live/cli.py",
    "src/ecomsre/phase1/contracts.py",
    "src/ecomsre/phase1/evidence.py",
    "src/ecomsre_rca100/contracts.py",
    "src/ecomsre_rca100/entity.py",
    "src/ecomsre_rca100/projection.py",
    "src/ecomsre_rcaeval/adapter.py",
    "src/ecomsre_rcaeval/artifacts.py",
    "src/ecomsre_rcaeval/contracts.py",
    "src/ecomsre_rcaeval/dataset.py",
    "src/ecomsre_rcaeval/tools.py",
    "src/ecomsre_rca_unified/contracts.py",
    "src/ecomsre_rca_unified/hierarchical_context.py",
    "src/ecomsre_rca_unified/hierarchy.py",
    "src/ecomsre_rca_unified/live_context_adapters.py",
    "src/ecomsre_rca_unified/live_comparison.py",
    "src/ecomsre_rca_unified/live_rca100_scan.py",
    "src/ecomsre_rca_unified/propagation.py",
)
FROZEN_RUNTIME_DEPENDENCY_PATHS = (
    *CONTEXT_AUDIT_IMPLEMENTATION_PATHS,
    "src/ecomsre/model/gateway.py",
    "src/ecomsre_rcaeval_adaptive/v2_runner.py",
    "src/ecomsre_rcaeval_v2/contracts.py",
    "src/ecomsre_rcaeval_v2/dev3_evidence.py",
    "src/ecomsre_rcaeval_v2/dev3_execution.py",
    "src/ecomsre_rcaeval_v2/dev3_provider.py",
    "src/ecomsre_rcaeval_v2/dev3_token_accounting.py",
)
EXPECTED_INPUT_TREES = {
    "rca100": {
        "files": 721,
        "bytes": 3_662_456_456,
        "sha256": "55c578716529dd159f59ebb1825258f04b29ae048697a370b0f28b9341f72e20",
    },
    "obss_ob": {
        "files": 1_092,
        "bytes": 8_662_327_689,
        "sha256": "63ed757d1a475ca99afc3f3ba0310e73d1c657d1b3473efc8a3f44c16a2b8c44",
    },
    "obss_ss": {
        "files": 810,
        "bytes": 2_323_203_527,
        "sha256": "63186deada78cb0b719bec01f69fcbe159e1bc0998fe7224c3a39bbdc4745f35",
    },
}
EXPECTED_ANSWER_SOURCE_LOCK_SHA256 = (
    "f0426118842f3148caf4e57cf0de12f1cb7ab245bbcd1519794d4b8d91a6a77b"
)
EXPECTED_OBSS_DATASET_AUDIT_SHA256 = (
    "df437ad58e5b71b771ab2879dbcfd106fb142665dd41db8e16768f5b82d177fa"
)
EXPECTED_RCA100_INPUT_SOURCE_LOCK_SHA256 = (
    "3401e955d0bcc780d1d3fc80532d91ec1a0d7e960b18da92f065c6b07c976f62"
)
STATE_PREDECESSORS: dict[str, str | None] = {
    "SCHEDULE_FROZEN": None,
    "CONTEXT_AUDITED": "SCHEDULE_FROZEN",
    "IMPLEMENTATION_FROZEN": "CONTEXT_AUDITED",
    "CI_ADMITTED": "IMPLEMENTATION_FROZEN",
    "INPUTS_REVERIFIED": "CI_ADMITTED",
    "PROVIDER_PREFLIGHT_PASSED": "INPUTS_REVERIFIED",
    "TUNE_EXECUTED": "PROVIDER_PREFLIGHT_PASSED",
    "TUNE_ABORTED_HTTP429": "PROVIDER_PREFLIGHT_PASSED",
    "TUNE_TERMINALS_LOCKED": "TUNE_EXECUTED",
    "GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK": "TUNE_TERMINALS_LOCKED",
    "TUNE_SCORED": "GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK",
    "CANDIDATE_FROZEN": "TUNE_SCORED",
    "REGRESSION_EXECUTED": "CANDIDATE_FROZEN",
    "REGRESSION_TERMINALS_LOCKED": "REGRESSION_EXECUTED",
    "REGRESSION_SCORED": "REGRESSION_TERMINALS_LOCKED",
}
STATE_LOCK_PATHS = {
    "SCHEDULE_FROZEN": "locks/schedule-lock.json",
    "CONTEXT_AUDITED": "locks/context-audit-lock.json",
    "IMPLEMENTATION_FROZEN": "locks/implementation-lock.json",
    "CI_ADMITTED": "locks/ci-admission-lock.json",
    "INPUTS_REVERIFIED": "locks/input-reverification-lock.json",
    "PROVIDER_PREFLIGHT_PASSED": "locks/provider-preflight-lock.json",
    "TUNE_EXECUTED": "runtime/tune/execution-summary.json",
    "TUNE_ABORTED_HTTP429": "locks/tune-http429-abort-lock.json",
    "TUNE_TERMINALS_LOCKED": "locks/tune-terminal-lock.json",
    "GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK": "locks/ground-truth-lock.json",
    "TUNE_SCORED": "locks/tune-scoring-lock.json",
    "CANDIDATE_FROZEN": "locks/candidate-lock.json",
    "REGRESSION_EXECUTED": "runtime/regression/execution-summary.json",
    "REGRESSION_TERMINALS_LOCKED": "locks/regression-terminal-lock.json",
    "REGRESSION_SCORED": "locks/regression-scoring-lock.json",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSON is not a regular file: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"required JSON is not an object: {path.name}")
    return value


def _require_exact_object(
    path: Path, expected: Mapping[str, object], label: str
) -> dict[str, object]:
    observed = _load_object(path)
    if observed != dict(expected):
        raise ValueError(f"{label} differs")
    return observed


def _require_int(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _require_float(value: object, label: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    return float(value)


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


def _write_create_once(path: Path, value: object) -> str:
    payload = _canonical_bytes(value)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"create-once artifact differs: {path.name}")
        existing = path.read_bytes()
        if existing == payload:
            return _sha_bytes(existing)
        if isinstance(value, Mapping) and "created_at_utc" in value:
            observed = _load_object(path)
            preserved_timestamp = _require_utc_timestamp(
                observed.get("created_at_utc"), path.name
            )
            expected = dict(value)
            expected["created_at_utc"] = preserved_timestamp
            if existing == _canonical_bytes(expected):
                return _sha_bytes(existing)
        raise ValueError(f"create-once artifact differs: {path.name}")
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    return _sha_bytes(payload)


def _require_state(private_root: Path, name: str) -> dict[str, object]:
    _verify_state_chain(private_root, name)
    return _load_object(private_root / "state" / f"{name}.json")


def _advance_state(
    private_root: Path,
    name: str,
    *,
    predecessor: str | None,
    lock_name: str,
    lock_sha256: str,
) -> None:
    predecessor_sha = None
    if predecessor is not None:
        predecessor_path = private_root / "state" / f"{predecessor}.json"
        _load_object(predecessor_path)
        predecessor_sha = _sha_file(predecessor_path)
    _write_create_once(
        private_root / "state" / f"{name}.json",
        {
            "created_at_utc": _utc_now(),
            "evaluation_version": EVALUATION_VERSION,
            "lock_name": lock_name,
            "lock_sha256": lock_sha256,
            "predecessor": predecessor,
            "predecessor_sha256": predecessor_sha,
            "schema_version": "strong-single-hierarchical-live.state.v1",
            "state": name,
        },
    )


def _validate_public_frozen_state(
    private_root: Path,
    *,
    predecessor: str,
    verification_lock_sha256: str,
) -> dict[str, object]:
    path = private_root / "state" / "PUBLIC_RESULT_FROZEN.json"
    state = _load_object(path)
    created_at = state.get("created_at_utc")
    try:
        parsed_created_at = datetime.fromisoformat(
            str(created_at).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("public frozen state timestamp differs") from exc
    if (
        not isinstance(created_at, str)
        or parsed_created_at.tzinfo is None
        or parsed_created_at.utcoffset() != timezone.utc.utcoffset(parsed_created_at)
    ):
        raise ValueError("public frozen state timestamp differs")
    expected = {
        "created_at_utc": created_at,
        "evaluation_version": EVALUATION_VERSION,
        "lock_name": "public-verification-lock.json",
        "lock_sha256": verification_lock_sha256,
        "predecessor": predecessor,
        "predecessor_sha256": _sha_file(
            private_root / "state" / f"{predecessor}.json"
        ),
        "schema_version": "strong-single-hierarchical-live.state.v1",
        "state": "PUBLIC_RESULT_FROZEN",
    }
    if state != expected:
        raise ValueError("public frozen state binding differs")
    return state


def _config_hashes() -> dict[str, str]:
    expected = {
        "budget.json",
        "context-policy.json",
        "model-lock.json",
        "prompt-lock.json",
        "protocol.json",
        "regression-gates.json",
        "tune-gates.json",
        "tune-schedule.json",
    }
    paths = tuple(sorted(CONFIG_ROOT.glob("*.json")))
    if {item.name for item in paths} != expected:
        raise ValueError("live comparison config surface differs")
    return {path.name: _sha_file(path) for path in paths}


def _verify_prompt_lock() -> None:
    lock = _load_object(CONFIG_ROOT / "prompt-lock.json")
    hashes = prompt_hashes()
    if any(lock.get(key) != value for key, value in hashes.items()):
        raise ValueError("live comparison prompt/output schema hash drift")


def _obss_case_refs(
    terminal_root: Path,
    *,
    expected_tree_sha256: str,
    expected_count: int,
    expected_split: Literal["TUNE_SET", "REGRESSION_SET"],
    ob_root: Path,
    ss_root: Path,
) -> tuple[CaseRef, ...]:
    observed_tree, observed_files = tree_sha256(terminal_root)
    if observed_tree != expected_tree_sha256 or observed_files != expected_count:
        raise ValueError("consumed OB/SS terminal tree differs")
    cases = discover_label_blind_dev_cases(
        ob_root, system="RE2-OB"
    ) + discover_label_blind_dev_cases(
        ss_root, system="RE2-SS"
    )
    index = {case.case_id: case for case in cases}
    if len(index) != 180:
        raise ValueError("label-blind OB/SS case index denominator differs")
    selected: set[str] = set()
    for path in sorted(terminal_root.glob("*.json")):
        terminal = _load_object(path)
        case_id = terminal.get("case_id")
        system = terminal.get("system")
        if (
            not isinstance(case_id, str)
            or case_id not in index
            or system not in {"RE2-OB", "RE2-SS"}
            or index[case_id].system != system
            or terminal.get("split") != expected_split
        ):
            raise ValueError("consumed OB/SS terminal case projection is invalid")
        selected.add(case_id)
    output = [
        CaseRef(source="OBSS", source_key=case_id)
        for case_id in sorted(selected)
    ]
    if len(output) != expected_count or len(set(output)) != expected_count:
        raise ValueError("selected OB/SS schedule denominator differs")
    return tuple(output)


def _private_schedule_payload(
    rows: Sequence[ScheduledArm], *, seed: int
) -> dict[str, object]:
    return {
        "evaluation_version": EVALUATION_VERSION,
        "records": [
            {
                "arm": item.arm.value,
                "arm_position": item.arm_position,
                "opaque_case_id": item.opaque_case_id,
                "pair_position": item.pair_position,
                "run_id": item.run_id,
                "source": item.source,
                "source_key": item.source_key,
                "split": item.split,
            }
            for item in rows
        ],
        "schema_version": "strong-single-hierarchical-live.private-schedule.v1",
        "seed": seed,
    }


def _verify_schedule_source_locks(
    *,
    obss_dataset_audit_path: Path,
    rca100_input_source_lock_path: Path,
) -> dict[str, object]:
    output: dict[str, object] = {}
    for name, path, expected in (
        (
            "obss_dataset_audit",
            obss_dataset_audit_path,
            EXPECTED_OBSS_DATASET_AUDIT_SHA256,
        ),
        (
            "rca100_input_source_lock",
            rca100_input_source_lock_path,
            EXPECTED_RCA100_INPUT_SOURCE_LOCK_SHA256,
        ),
    ):
        if path.is_symlink() or not path.is_file() or _sha_file(path) != expected:
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule source lock differs")
        output[name] = {
            "absolute_path": str(path.resolve(strict=True)),
            "sha256": expected,
        }
    return output


def _require_utc_timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} timestamp differs")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} timestamp differs") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} timestamp differs")
    return value


def _expected_schedule_lock(
    private_root: Path,
    *,
    created_at_utc: str,
    source_locks: Mapping[str, object],
) -> dict[str, object]:
    return {
        "config_hashes": _config_hashes(),
        "control_generation": CONTROL_GENERATION,
        "created_at_utc": created_at_utc,
        "evaluation_version": EVALUATION_VERSION,
        "regression": {
            "case_pairs": 120,
            "records": 240,
            "schedule_sha256": _sha_file(
                private_root / "schedules" / "regression.json"
            ),
            "seed": 20260813,
        },
        "schema_version": "strong-single-hierarchical-live.schedule-lock.v1",
        "source_locks": {
            **source_locks,
            "tune_consumed_terminal_tree_sha256": (
                "d3e8aba8514b8f688107f1d5728dd4c5e476b28dc3b6a86fc9a2a8b4f43e9363"
            ),
            "regression_consumed_terminal_tree_sha256": (
                "7e1d563c248cbe8d082fa84b07d48fd283034815a8a4f4940f68f037d18ac0d7"
            ),
        },
        "tune": {
            "case_pairs": 163,
            "records": 326,
            "schedule_sha256": _sha_file(private_root / "schedules" / "tune.json"),
            "seed": 20260812,
        },
    }


def _verify_schedule_payload(
    private_root: Path,
    *,
    split: Literal["TUNE", "REGRESSION"],
    expected_records: int,
    seed: int,
) -> None:
    payload = _load_object(
        private_root / "schedules" / f"{split.casefold()}.json"
    )
    raw_records = payload.get("records")
    if (
        set(payload) != {"evaluation_version", "records", "schema_version", "seed"}
        or payload.get("evaluation_version") != EVALUATION_VERSION
        or payload.get("schema_version")
        != "strong-single-hierarchical-live.private-schedule.v1"
        or payload.get("seed") != seed
        or not isinstance(raw_records, list)
        or len(raw_records) != expected_records
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: private schedule payload differs")
    record_keys = {
        "arm",
        "arm_position",
        "opaque_case_id",
        "pair_position",
        "run_id",
        "source",
        "source_key",
        "split",
    }
    records: list[dict[str, object]] = []
    for raw in raw_records:
        if (
            not isinstance(raw, dict)
            or set(raw) != record_keys
            or raw.get("split") != split
            or raw.get("arm") not in {"B0", "H1"}
            or type(raw.get("arm_position")) is not int
            or type(raw.get("pair_position")) is not int
            or raw.get("source") not in {"RCA100", "OBSS"}
            or not isinstance(raw.get("source_key"), str)
            or not raw["source_key"]
            or not isinstance(raw.get("opaque_case_id"), str)
            or not isinstance(raw.get("run_id"), str)
        ):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule record schema differs")
        records.append(raw)
    pair_identities: set[tuple[object, object]] = set()
    opaque_ids: set[object] = set()
    run_ids: set[object] = set()
    source_pairs = {"RCA100": 0, "OBSS": 0}
    for offset in range(0, expected_records, 2):
        pair_position = offset // 2 + 1
        first, second = records[offset : offset + 2]
        expected_arms = ("B0", "H1") if pair_position % 2 else ("H1", "B0")
        if (
            first["pair_position"] != pair_position
            or second["pair_position"] != pair_position
            or (first["arm_position"], second["arm_position"]) != (1, 2)
            or (first["arm"], second["arm"]) != expected_arms
            or first["source"] != second["source"]
            or first["source_key"] != second["source_key"]
            or first["opaque_case_id"] != second["opaque_case_id"]
        ):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule pair ordering differs")
        identity = (first["source"], first["source_key"])
        if identity in pair_identities:
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule case identity repeats")
        pair_identities.add(identity)
        source_pairs[cast(str, first["source"])] += 1
        expected_opaque = "case-" + hashlib.sha256(
            b"\0".join(
                (
                    EVALUATION_VERSION.encode(),
                    split.encode(),
                    str(seed).encode(),
                    cast(str, first["source"]).encode(),
                    cast(str, first["source_key"]).encode(),
                )
            )
        ).hexdigest()[:20]
        if first["opaque_case_id"] != expected_opaque:
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule opaque identity differs")
        opaque_ids.add(expected_opaque)
        for record in (first, second):
            expected_run = hashlib.sha256(
                b"\0".join(
                    (
                        EVALUATION_VERSION.encode(),
                        split.encode(),
                        expected_opaque.encode(),
                        cast(str, record["arm"]).encode(),
                    )
                )
            ).hexdigest()[:32]
            if record["run_id"] != expected_run or expected_run in run_ids:
                raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule run identity differs")
            run_ids.add(expected_run)
    expected_sources = (
        {"RCA100": 103, "OBSS": 60}
        if split == "TUNE"
        else {"RCA100": 0, "OBSS": 120}
    )
    if (
        source_pairs != expected_sources
        or len(pair_identities) != expected_records // 2
        or len(opaque_ids) != expected_records // 2
        or len(run_ids) != expected_records
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule denominators differ")


def _require_active_control(private_root: Path) -> dict[str, object]:
    if (
        (private_root / "locks" / "superseded-lock.json").exists()
        or (private_root / "state" / "SUPERSEDED.json").exists()
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: private control is superseded")
    schedule_lock = _load_object(private_root / "locks" / "schedule-lock.json")
    if schedule_lock.get("control_generation") != CONTROL_GENERATION:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: private control generation differs")
    created_at = _require_utc_timestamp(
        schedule_lock.get("created_at_utc"), "schedule lock"
    )
    source_locks = schedule_lock.get("source_locks")
    if not isinstance(source_locks, Mapping) or set(source_locks) != {
        "obss_dataset_audit",
        "rca100_input_source_lock",
        "tune_consumed_terminal_tree_sha256",
        "regression_consumed_terminal_tree_sha256",
    }:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule source binding differs")
    verified_files: dict[str, object] = {}
    for name, expected_sha in (
        ("obss_dataset_audit", EXPECTED_OBSS_DATASET_AUDIT_SHA256),
        ("rca100_input_source_lock", EXPECTED_RCA100_INPUT_SOURCE_LOCK_SHA256),
    ):
        value = source_locks.get(name)
        if not isinstance(value, Mapping):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule source binding differs")
        absolute_path = value.get("absolute_path")
        if (
            not isinstance(absolute_path, str)
            or value.get("sha256") != expected_sha
            or Path(absolute_path).is_symlink()
            or not Path(absolute_path).is_file()
            or _sha_file(Path(absolute_path)) != expected_sha
        ):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule source binding differs")
        verified_files[name] = dict(value)
    verified_source_locks = {
        **verified_files,
        "tune_consumed_terminal_tree_sha256": (
            "d3e8aba8514b8f688107f1d5728dd4c5e476b28dc3b6a86fc9a2a8b4f43e9363"
        ),
        "regression_consumed_terminal_tree_sha256": (
            "7e1d563c248cbe8d082fa84b07d48fd283034815a8a4f4940f68f037d18ac0d7"
        ),
    }
    if dict(source_locks) != verified_source_locks:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: schedule source binding differs")
    _require_exact_object(
        private_root / "locks" / "schedule-lock.json",
        _expected_schedule_lock(
            private_root,
            created_at_utc=created_at,
            source_locks=verified_files,
        ),
        "BLOCKED_PROTOCOL_DRIFT: schedule control",
    )
    _verify_schedule_payload(
        private_root, split="TUNE", expected_records=326, seed=20260812
    )
    _verify_schedule_payload(
        private_root, split="REGRESSION", expected_records=240, seed=20260813
    )
    return schedule_lock


def mark_superseded(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    if (
        private_root.resolve() == PROJECT_ROOT.resolve()
        or PROJECT_ROOT.resolve() in private_root.resolve().parents
    ):
        raise ValueError("superseded private control must be outside the repository")
    reason = f"SUPERSEDED_{args.reason}"
    lock_path = private_root / "locks" / "superseded-lock.json"
    state_path = private_root / "state" / "SUPERSEDED.json"
    if lock_path.exists() or state_path.exists():
        lock = _load_object(lock_path)
        state = _load_object(state_path)
        if (
            lock.get("reason") != reason
            or lock.get("execution_eligible") is not False
            or state.get("reason") != reason
            or state.get("lock_sha256") != _sha_file(lock_path)
        ):
            raise ValueError("existing superseded control marker differs")
        print(json.dumps({"reason": reason, "state": "SUPERSEDED"}, sort_keys=True))
        return
    schedule_lock = _load_object(private_root / "locks" / "schedule-lock.json")
    if schedule_lock.get("control_generation") == CONTROL_GENERATION:
        raise ValueError("active control generation cannot be superseded")
    forbidden = (
        private_root / "locks" / "implementation-lock.json",
        private_root / "locks" / "ground-truth-lock.json",
        private_root / "runtime",
        private_root / "evaluation",
    )
    if any(path.exists() for path in forbidden):
        raise ValueError("only zero-Provider pre-implementation control may be superseded")
    preserved: dict[str, str] = {}
    for directory_name in ("audit", "locks", "schedules", "state"):
        directory = private_root / directory_name
        if not directory.exists():
            continue
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            if path.is_symlink():
                raise ValueError("superseded control contains a symlink artifact")
            preserved[path.relative_to(private_root).as_posix()] = _sha_file(path)
    if "locks/schedule-lock.json" not in preserved:
        raise ValueError("superseded control lacks a schedule lock")
    lock_sha = _write_create_once(
        lock_path,
        {
            "created_at_utc": _utc_now(),
            "evaluation_version": EVALUATION_VERSION,
            "execution_eligible": False,
            "ground_truth_acquired": False,
            "preserved_artifact_sha256": preserved,
            "provider_calls": 0,
            "reason": reason,
            "schema_version": "strong-single-hierarchical-live.superseded-lock.v1",
        },
    )
    _write_create_once(
        state_path,
        {
            "created_at_utc": _utc_now(),
            "evaluation_version": EVALUATION_VERSION,
            "lock_name": lock_path.name,
            "lock_sha256": lock_sha,
            "reason": reason,
            "schema_version": "strong-single-hierarchical-live.superseded-state.v1",
            "state": "SUPERSEDED",
        },
    )
    print(json.dumps({"reason": reason, "state": "SUPERSEDED"}, sort_keys=True))


def build_schedules(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    if private_root.resolve() == PROJECT_ROOT.resolve() or PROJECT_ROOT.resolve() in private_root.resolve().parents:
        raise ValueError("private evaluation root must be outside the repository")
    _verify_prompt_lock()
    verified_source_locks = _verify_schedule_source_locks(
        obss_dataset_audit_path=Path(args.obss_dataset_audit_path),
        rca100_input_source_lock_path=Path(args.rca_input_source_lock_path),
    )
    rca_cases_root = Path(args.rca_cases_root)
    rca_roots = tuple(sorted(path for path in rca_cases_root.iterdir() if path.is_dir()))
    if len(rca_roots) != 103 or any(path.is_symlink() for path in rca_roots):
        raise ValueError("RCA100 source case denominator differs")
    rca_refs = tuple(CaseRef(source="RCA100", source_key=path.name) for path in rca_roots)
    tune_obss = _obss_case_refs(
        Path(args.tune_consumed_terminals_root),
        expected_tree_sha256=(
            "d3e8aba8514b8f688107f1d5728dd4c5e476b28dc3b6a86fc9a2a8b4f43e9363"
        ),
        expected_count=60,
        expected_split="TUNE_SET",
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
    )
    regression_obss = _obss_case_refs(
        Path(args.regression_consumed_terminals_root),
        expected_tree_sha256=(
            "7e1d563c248cbe8d082fa84b07d48fd283034815a8a4f4940f68f037d18ac0d7"
        ),
        expected_count=120,
        expected_split="REGRESSION_SET",
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
    )
    tune = paired_schedule((*rca_refs, *tune_obss), seed=20260812, split="TUNE")
    regression = paired_schedule(
        regression_obss, seed=20260813, split="REGRESSION"
    )
    tune_sha = _write_create_once(
        private_root / "schedules" / "tune.json",
        _private_schedule_payload(tune, seed=20260812),
    )
    regression_sha = _write_create_once(
        private_root / "schedules" / "regression.json",
        _private_schedule_payload(regression, seed=20260813),
    )
    lock = {
        "config_hashes": _config_hashes(),
        "control_generation": CONTROL_GENERATION,
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "regression": {
            "case_pairs": 120,
            "records": 240,
            "schedule_sha256": regression_sha,
            "seed": 20260813,
        },
        "schema_version": "strong-single-hierarchical-live.schedule-lock.v1",
        "source_locks": {
            **verified_source_locks,
            "tune_consumed_terminal_tree_sha256": (
                "d3e8aba8514b8f688107f1d5728dd4c5e476b28dc3b6a86fc9a2a8b4f43e9363"
            ),
            "regression_consumed_terminal_tree_sha256": (
                "7e1d563c248cbe8d082fa84b07d48fd283034815a8a4f4940f68f037d18ac0d7"
            ),
        },
        "tune": {
            "case_pairs": 163,
            "records": 326,
            "schedule_sha256": tune_sha,
            "seed": 20260812,
        },
    }
    lock_sha = _write_create_once(private_root / "locks" / "schedule-lock.json", lock)
    _advance_state(
        private_root,
        "SCHEDULE_FROZEN",
        predecessor=None,
        lock_name="schedule-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps({"state": "SCHEDULE_FROZEN", "tune_records": 326, "regression_records": 240}, sort_keys=True))


def _load_private_schedule(
    private_root: Path, split: Literal["TUNE", "REGRESSION"]
) -> tuple[dict[str, object], ...]:
    schedule_lock = _load_object(private_root / "locks" / "schedule-lock.json")
    section = schedule_lock.get(split.casefold())
    if not isinstance(section, Mapping):
        raise ValueError("private schedule lock section is missing")
    path = private_root / "schedules" / f"{split.casefold()}.json"
    if _sha_file(path) != section.get("schedule_sha256"):
        raise ValueError("private schedule hash drift")
    value = _load_object(path)
    records = value.get("records")
    expected = 326 if split == "TUNE" else 240
    if not isinstance(records, list) or len(records) != expected:
        raise ValueError("private schedule record denominator differs")
    return cast(tuple[dict[str, object], ...], tuple(records))


def _obss_index(ob_root: Path, ss_root: Path) -> dict[str, TelemetryCase]:
    cases = discover_label_blind_dev_cases(
        ob_root, system="RE2-OB"
    ) + discover_label_blind_dev_cases(
        ss_root, system="RE2-SS"
    )
    output = {case.case_id: case for case in cases}
    if len(output) != 180:
        raise ValueError("OB/SS runtime case index denominator differs")
    return output


def _percentile(values: Sequence[int], proportion: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * proportion) - 1)]


def _count_distribution(values: Sequence[int]) -> dict[str, int | float]:
    if not values:
        raise ValueError("count distribution requires at least one value")
    return {
        "max": max(values),
        "mean": sum(values) / len(values),
        "median": _percentile(values, 0.5),
        "min": min(values),
        "p95": _percentile(values, 0.95),
    }


def _context_audit_implementation_hashes() -> dict[str, str]:
    return {
        path: _sha_file(PROJECT_ROOT / path)
        for path in CONTEXT_AUDIT_IMPLEMENTATION_PATHS
    }


def _verify_context_audit_binding(
    private_root: Path, *, rehash_input_trees: bool = False
) -> dict[str, object]:
    lock_path = private_root / "locks" / "context-audit-lock.json"
    lock = _load_object(lock_path)
    created_at = _require_utc_timestamp(
        lock.get("created_at_utc"), "context audit lock"
    )
    locked_input_trees = lock.get("input_trees")
    if not isinstance(locked_input_trees, Mapping) or set(locked_input_trees) != set(
        EXPECTED_INPUT_TREES
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: context audit input binding differs")
    for name, frozen in EXPECTED_INPUT_TREES.items():
        item = locked_input_trees.get(name)
        if not isinstance(item, Mapping) or any(
            item.get(key) != value
            for key, value in (
                ("byte_count", frozen["bytes"]),
                ("file_count", frozen["files"]),
                ("sha256", frozen["sha256"]),
            )
        ):
            raise ValueError(
                "BLOCKED_PROTOCOL_DRIFT: context audit input binding differs"
            )
        absolute_root = item.get("absolute_root")
        if not isinstance(absolute_root, str) or not Path(absolute_root).is_absolute():
            raise ValueError(
                "BLOCKED_PROTOCOL_DRIFT: context audit input binding differs"
            )
        if rehash_input_trees:
            digest, file_count, byte_count = _absolute_tree_digest(Path(absolute_root))
            if (digest, file_count, byte_count) != (
                frozen["sha256"],
                frozen["files"],
                frozen["bytes"],
            ):
                raise ValueError(
                    "BLOCKED_PROTOCOL_DRIFT: context audit input tree differs"
                )
    expected = {
        "audited_implementation_sha256": _context_audit_implementation_hashes(),
        "config_hashes": _config_hashes(),
        "context_audit_sha256": _sha_file(
            private_root / "audit" / "context-audit.json"
        ),
        "created_at_utc": created_at,
        "evaluation_version": EVALUATION_VERSION,
        "input_trees": locked_input_trees,
        "methodology_sha256": _load_object(
            CONFIG_ROOT / "context-policy.json"
        ).get("methodology_sha256"),
        "revision": "v3_dictionary_bitmask_final",
        "schedule_lock_sha256": _sha_file(
            private_root / "locks" / "schedule-lock.json"
        ),
        "schema_version": "strong-single-hierarchical-live.context-audit-lock.v1",
    }
    if lock != expected:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: context audit binding differs")
    return lock


def audit_contexts(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_active_control(private_root)
    _require_state(private_root, "SCHEDULE_FROZEN")
    input_trees = _fresh_expected_input_trees(args)
    audit_name = "context-audit.json"
    lock_name = "context-audit-lock.json"
    records = _load_private_schedule(private_root, "TUNE")
    pairs = tuple(records[index] for index in range(0, len(records), 2))
    if len(pairs) != 163:
        raise ValueError("TUNE pair denominator differs")
    obss = _obss_index(Path(args.ob_root), Path(args.ss_root))
    methodology = _load_object(Path(args.methodology))
    if _sha_file(Path(args.methodology)) != _load_object(CONFIG_ROOT / "context-policy.json").get("methodology_sha256"):
        raise ValueError("context methodology hash drift")
    model = str(_load_object(CONFIG_ROOT / "model-lock.json")["model"])
    entities: list[int] = []
    relations: list[int] = []
    b0_estimates: list[int] = []
    h1_estimates: list[int] = []
    truncations = duplicates = invalid_refs = 0
    source_counts = {"RCA100": 0, "OBSS": 0}
    for index, record in enumerate(pairs, 1):
        source = record.get("source")
        source_key = record.get("source_key")
        if not isinstance(source, str) or source not in source_counts or not isinstance(source_key, str):
            raise ValueError("private TUNE schedule source identity is invalid")
        if source == "RCA100":
            base, hierarchy_source = build_rca100_live_inputs(
                Path(args.rca_cases_root) / source_key,
                projection_case_number=index,
                methodology=methodology,
            )
        else:
            case = obss.get(source_key)
            if case is None:
                raise ValueError("private TUNE OB/SS source case is absent")
            base, hierarchy_source = build_obss_live_inputs(case)
        hierarchy = build_hierarchical_context(base, hierarchy_source)
        assert_model_context_private(base, source_key, hierarchy)
        entity_refs = [item.entity_ref for item in hierarchy.entity_cards]
        duplicates += len(entity_refs) - len(set(entity_refs))
        visible = set(entity_refs)
        invalid_refs += sum(
            relation.source_entity_ref not in visible
            or relation.target_entity_ref not in visible
            for relation in hierarchy.propagation_relations
        )
        invalid_refs += sum(
            reference is not None and reference not in visible
            for card in hierarchy.entity_cards
            for reference in (
                card.parent_ref_or_none,
                card.service_ancestor_or_none,
            )
        )
        truncations += int(hierarchy.dropped_included_candidate_count > 0)
        entities.append(len(hierarchy.entity_cards))
        relations.append(len(hierarchy.propagation_relations))
        b0_payload = build_request_payload(
            model=model,
            base=base,
            arm=Arm.B0,
            hierarchy=None,
            max_completion_tokens=2048,
        )
        h1_payload = build_request_payload(
            model=model,
            base=base,
            arm=Arm.H1,
            hierarchy=hierarchy,
            max_completion_tokens=2048,
        )
        b0_estimates.append(math.ceil(len(canonical_json_bytes(b0_payload)) / 3))
        h1_estimates.append(math.ceil(len(canonical_json_bytes(h1_payload)) / 3))
        source_counts[source] += 1
        if index % 10 == 0 or index == len(pairs):
            print(f"[context-audit] {index}/{len(pairs)}", flush=True)
    if duplicates or invalid_refs or source_counts != {"RCA100": 103, "OBSS": 60}:
        raise ValueError("context audit integrity check failed")
    if max(h1_estimates) > 29_952:
        raise ValueError("H1 context estimate exceeds prompt token reservation")
    audit = {
        "b0_valid_contexts": 163,
        "created_at_utc": _utc_now(),
        "duplicate_entity_count": duplicates,
        "evaluation_version": EVALUATION_VERSION,
        "h1_entity_count": _count_distribution(entities),
        "h1_propagation_relation_count": _count_distribution(relations),
        "h1_valid_contexts": 163,
        "input_token_estimate": {
            "basis": "CEIL_CANONICAL_REQUEST_UTF8_BYTES_DIV_3",
            "b0_max": max(b0_estimates),
            "b0_mean": sum(b0_estimates) / len(b0_estimates),
            "h1_max": max(h1_estimates),
            "h1_mean": sum(h1_estimates) / len(h1_estimates),
            "h1_to_b0_mean_ratio": (sum(h1_estimates) / sum(b0_estimates)),
        },
        "invalid_ref_count": invalid_refs,
        "revision": "v3_dictionary_bitmask_final",
        "schema_version": "strong-single-hierarchical-live.context-audit.v1",
        "source_counts": source_counts,
        "truncation_count": truncations,
    }
    audit_sha = _write_create_once(private_root / "audit" / audit_name, audit)
    lock = {
        "audited_implementation_sha256": _context_audit_implementation_hashes(),
        "config_hashes": _config_hashes(),
        "context_audit_sha256": audit_sha,
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "input_trees": input_trees,
        "methodology_sha256": _sha_file(Path(args.methodology)),
        "revision": "v3_dictionary_bitmask_final",
        "schedule_lock_sha256": _sha_file(private_root / "locks" / "schedule-lock.json"),
        "schema_version": "strong-single-hierarchical-live.context-audit-lock.v1",
    }
    lock_sha = _write_create_once(private_root / "locks" / lock_name, lock)
    _advance_state(
        private_root,
        "CONTEXT_AUDITED",
        predecessor="SCHEDULE_FROZEN",
        lock_name=lock_name,
        lock_sha256=lock_sha,
    )
    print(json.dumps(audit, sort_keys=True))


def _git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _historical_lineage_binding(
    implementation_commit: str, *, allowed_paths: set[str]
) -> dict[str, object]:
    parents = _git("rev-list", "--parents", "-n", "1", STARTING_MAIN_COMMIT).split()
    if parents != [STARTING_MAIN_COMMIT, PR24_FIRST_PARENT, PR24_HEAD_COMMIT]:
        raise ValueError("BLOCKED_PR24_MERGE_LINEAGE_MISSING")
    for ancestor in (STARTING_MAIN_COMMIT, PR24_HEAD_COMMIT):
        subprocess.run(
            ("git", "merge-base", "--is-ancestor", ancestor, implementation_commit),
            cwd=PROJECT_ROOT,
            check=True,
        )
    changed_paths = tuple(
        sorted(
            filter(
                None,
                _git(
                    "diff",
                    "--name-only",
                    f"{STARTING_MAIN_COMMIT}..{implementation_commit}",
                ).splitlines(),
            )
        )
    )
    if not changed_paths or not set(changed_paths).issubset(allowed_paths):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: implementation path surface differs")
    return {
        "implementation_changed_paths": list(changed_paths),
        "pr24_head_commit": PR24_HEAD_COMMIT,
        "pr24_merge_commit": STARTING_MAIN_COMMIT,
        "pr24_merge_parents": [PR24_FIRST_PARENT, PR24_HEAD_COMMIT],
        "starting_main_commit": STARTING_MAIN_COMMIT,
    }


def _implementation_changed_path_allowlist() -> set[str]:
    return set(CORE_PATHS) | {
        f"config/rca-strong-single-hierarchical-live-v1/{name}"
        for name in _config_hashes()
    }


def freeze_implementation(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_active_control(private_root)
    _require_state(private_root, "CONTEXT_AUDITED")
    _verify_context_audit_binding(private_root, rehash_input_trees=True)
    if _git("status", "--porcelain"):
        raise ValueError("implementation freeze requires a clean worktree")
    head = _git("rev-parse", "HEAD")
    tracked = set(_git("ls-files").splitlines())
    config_paths = tuple(
        f"config/rca-strong-single-hierarchical-live-v1/{name}"
        for name in _config_hashes()
    )
    protected_paths = tuple(
        dict.fromkeys((*CORE_PATHS, *config_paths, *FROZEN_RUNTIME_DEPENDENCY_PATHS))
    )
    if not set(protected_paths).issubset(tracked):
        raise ValueError("implementation freeze protected surface is untracked")
    protected = {
        path: _sha_file(PROJECT_ROOT / path) for path in sorted(protected_paths)
    }
    historical_lineage = _historical_lineage_binding(
        head, allowed_paths=_implementation_changed_path_allowlist()
    )
    lock = {
        **historical_lineage,
        "config_hashes": _config_hashes(),
        "control_generation": CONTROL_GENERATION,
        "context_audit_lock_sha256": _sha_file(
            private_root / "locks" / "context-audit-lock.json"
        ),
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": head,
        "protected_files": protected,
        "schedule_lock_sha256": _sha_file(private_root / "locks" / "schedule-lock.json"),
        "schema_version": "strong-single-hierarchical-live.implementation-lock.v1",
    }
    lock_sha = _write_create_once(private_root / "locks" / "implementation-lock.json", lock)
    _advance_state(
        private_root,
        "IMPLEMENTATION_FROZEN",
        predecessor="CONTEXT_AUDITED",
        lock_name="implementation-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps({"state": "IMPLEMENTATION_FROZEN", "implementation_commit": head}, sort_keys=True))


def record_ci_admission(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "IMPLEMENTATION_FROZEN")
    implementation = _verify_implementation(private_root)
    forbidden_pre_ci = (
        private_root / "runtime",
        private_root / "evaluation",
        private_root / "locks" / "ground-truth-lock.json",
        private_root / "locks" / "candidate-lock.json",
    )
    if any(path.exists() for path in forbidden_pre_ci):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: pre-CI runtime artifact exists")
    if args.head != implementation.get("implementation_commit"):
        raise ValueError("CI admission head differs from implementation freeze")
    if args.ci_state != "SUCCESS":
        raise ValueError("Provider admission requires successful applicable CI")
    lock = {
        "ci_state": args.ci_state,
        "created_at_utc": _utc_now(),
        "draft_pr_number": args.pr_number,
        "evaluation_version": EVALUATION_VERSION,
        "head": args.head,
        "implementation_lock_sha256": _sha_file(private_root / "locks" / "implementation-lock.json"),
        "schema_version": "strong-single-hierarchical-live.ci-admission-lock.v1",
    }
    lock_sha = _write_create_once(private_root / "locks" / "ci-admission-lock.json", lock)
    _advance_state(
        private_root,
        "CI_ADMITTED",
        predecessor="IMPLEMENTATION_FROZEN",
        lock_name="ci-admission-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps({"state": "CI_ADMITTED", "draft_pr_number": args.pr_number}, sort_keys=True))


def _verify_implementation(private_root: Path) -> dict[str, object]:
    _require_active_control(private_root)
    _verify_context_audit_binding(private_root, rehash_input_trees=True)
    implementation = _load_object(private_root / "locks" / "implementation-lock.json")
    if implementation.get("control_generation") != CONTROL_GENERATION:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: implementation control generation differs")
    if implementation.get("implementation_commit") != _git("rev-parse", "HEAD"):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: implementation commit differs")
    if _git("status", "--porcelain"):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: implementation worktree is dirty")
    protected = implementation.get("protected_files")
    if not isinstance(protected, Mapping):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: protected file lock is invalid")
    for raw_path, expected in protected.items():
        if not isinstance(raw_path, str) or not isinstance(expected, str):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: protected file binding is invalid")
        if _sha_file(PROJECT_ROOT / raw_path) != expected:
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: protected file differs")
    expected_lineage = _historical_lineage_binding(
        str(implementation.get("implementation_commit")),
        allowed_paths=_implementation_changed_path_allowlist(),
    )
    if any(implementation.get(key) != value for key, value in expected_lineage.items()):
        raise ValueError("BLOCKED_PR24_MERGE_LINEAGE_MISSING")
    if implementation.get("config_hashes") != _config_hashes():
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: configuration differs")
    if (
        implementation.get("schedule_lock_sha256")
        != _sha_file(private_root / "locks" / "schedule-lock.json")
        or implementation.get("context_audit_lock_sha256")
        != _sha_file(private_root / "locks" / "context-audit-lock.json")
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: implementation lineage differs")
    _verify_prompt_lock()
    return implementation


def _absolute_tree_digest(root: Path) -> tuple[str, int, int]:
    if root.is_symlink():
        raise ValueError("input tree root may not be a symlink")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("input tree root must be a real directory")
    digest = hashlib.sha256()
    count = 0
    byte_count = 0
    for path in sorted(item for item in resolved.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("input tree may not contain symlink files")
        content_sha = _sha_file(path)
        digest.update(f"{content_sha}  {path}\n".encode("utf-8"))
        count += 1
        byte_count += path.stat().st_size
    return digest.hexdigest(), count, byte_count


def _fresh_expected_input_trees(args: argparse.Namespace) -> dict[str, object]:
    roots = {
        "rca100": Path(args.rca_cases_root),
        "obss_ob": Path(args.ob_root),
        "obss_ss": Path(args.ss_root),
    }
    observed: dict[str, object] = {}
    for name, root in roots.items():
        digest, count, byte_count = _absolute_tree_digest(root)
        expected = EXPECTED_INPUT_TREES[name]
        if (
            digest != expected["sha256"]
            or count != expected["files"]
            or byte_count != expected["bytes"]
        ):
            raise ValueError(f"BLOCKED_PROTOCOL_DRIFT: {name} input tree differs")
        observed[name] = {
            "absolute_root": str(root.resolve(strict=True)),
            "byte_count": byte_count,
            "file_count": count,
            "sha256": digest,
        }
    if (Path(args.rca_cases_root).parent / "answer_key").exists():
        raise ValueError("BLOCKED_GROUND_TRUTH_LEAKAGE: runtime input contains answer key")
    return observed


def verify_inputs(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "CI_ADMITTED")
    _verify_implementation(private_root)
    observed = _fresh_expected_input_trees(args)
    lock = {
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "implementation_lock_sha256": _sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
        "input_trees": observed,
        "rca100_answer_key_present_in_runtime_input": False,
        "schema_version": "strong-single-hierarchical-live.input-reverification.v1",
        "tree_algorithm": "SHA256_CONTENT_PLUS_ABSOLUTE_PATH_V1",
    }
    lock_sha = _write_create_once(
        private_root / "locks" / "input-reverification-lock.json", lock
    )
    _advance_state(
        private_root,
        "INPUTS_REVERIFIED",
        predecessor="CI_ADMITTED",
        lock_name="input-reverification-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps({"state": "INPUTS_REVERIFIED", "inputs": observed}, sort_keys=True))


def _provider_config(env_file: Path) -> OpenAICompatibleConfig:
    if env_file.is_symlink() or not env_file.is_file():
        raise ValueError("trusted Provider environment file is invalid")
    if env_file.stat().st_mode & 0o077:
        raise ValueError("trusted Provider environment file permissions are too broad")
    config = provider_config_from_env_file(env_file)
    model = _load_object(CONFIG_ROOT / "model-lock.json").get("model")
    if config.model != model:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: Provider model differs")
    return config


def _synthetic_inputs() -> tuple[LiveBaseContext, HierarchySource]:
    service_a = LiveEntity(
        entity_ref="apm|apm.service|synthetic-a",
        entity_name="synthetic-a",
        layer=CanonicalEntityLayer.SERVICE,
        service_ancestor_or_none="apm|apm.service|synthetic-a",
        parent_ref_or_none=None,
    )
    service_b = LiveEntity(
        entity_ref="apm|apm.service|synthetic-b",
        entity_name="synthetic-b",
        layer=CanonicalEntityLayer.SERVICE,
        service_ancestor_or_none="apm|apm.service|synthetic-b",
        parent_ref_or_none=None,
    )
    base = LiveBaseContext(
        alert_title="Synthetic latency anomaly",
        prompt_text="Diagnose the supplied synthetic bounded evidence.",
        alert_entity_ref=service_b.entity_ref,
        entities=(service_a, service_b),
        evidence=(
            EvidenceItem(
                evidence_ref="metric:0001",
                source="METRICS",
                entity_ref=service_a.entity_ref,
                name="latency",
                started_at=1.0,
                ended_at=2.0,
                score=4.0,
                summary="Synthetic service latency increased after the anchor.",
            ),
            EvidenceItem(
                evidence_ref="trace:0001",
                source="TRACES",
                entity_ref=service_b.entity_ref,
                name="request",
                started_at=2.0,
                ended_at=3.0,
                score=1.0,
                summary="Synthetic downstream request became slow later.",
            ),
        ),
        source_status={
            "METRICS": "AVAILABLE",
            "LOGS": "SOURCE_UNAVAILABLE",
            "TRACES": "AVAILABLE",
        },
    )
    source = HierarchySource(
        entities=(service_a, service_b),
        parent_edges=(),
        topology_edges=(),
        propagation_edges=(
            RelationSource(
                source_entity_ref=service_a.entity_ref,
                target_entity_ref=service_b.entity_ref,
                relation_type="TRACE_PARENT_CHILD",
            ),
        ),
        source_visibility={
            service_a.entity_ref: frozenset({"METRICS"}),
            service_b.entity_ref: frozenset({"TRACES", "ALERTS"}),
        },
        first_anomaly_source={
            service_a.entity_ref: "METRICS",
            service_b.entity_ref: "TRACES",
        },
    )
    return base, source


def _preflight_record(arm: Arm, position: int) -> ScheduledArm:
    opaque = "case-" + hashlib.sha256(b"synthetic-preflight").hexdigest()[:20]
    run_id = hashlib.sha256(
        b"\0".join((EVALUATION_VERSION.encode(), b"PREFLIGHT", arm.value.encode()))
    ).hexdigest()[:32]
    return ScheduledArm(
        split="PREFLIGHT",
        pair_position=1,
        arm_position=position,
        opaque_case_id=opaque,
        source="SYNTHETIC",
        source_key="synthetic-non-case",
        arm=arm,
        run_id=run_id,
    )


def provider_preflight(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "INPUTS_REVERIFIED")
    implementation = _verify_implementation(private_root)
    if _has_any_state(
        private_root,
        (
            "TUNE_EXECUTED",
            "TUNE_ABORTED_HTTP429",
            "TUNE_TERMINALS_LOCKED",
            "GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK",
            "TUNE_SCORED",
            "CANDIDATE_FROZEN",
            "REGRESSION_EXECUTED",
            "REGRESSION_TERMINALS_LOCKED",
            "REGRESSION_SCORED",
            "PUBLIC_RESULT_FROZEN",
        ),
    ) and not _has_state(private_root, "PROVIDER_PREFLIGHT_PASSED"):
        raise ValueError("Provider preflight state is missing below a descendant")
    preflight_lock_path = private_root / "locks" / "provider-preflight-lock.json"
    if preflight_lock_path.exists() or _has_state(
        private_root, "PROVIDER_PREFLIGHT_PASSED"
    ):
        terminals = _verify_preflight_lock_again(private_root)
        lock = _load_object(preflight_lock_path)
        if not _has_state(private_root, "PROVIDER_PREFLIGHT_PASSED"):
            _advance_state(
                private_root,
                "PROVIDER_PREFLIGHT_PASSED",
                predecessor="INPUTS_REVERIFIED",
                lock_name=preflight_lock_path.name,
                lock_sha256=_sha_file(preflight_lock_path),
            )
        _require_state(private_root, "PROVIDER_PREFLIGHT_PASSED")
        if len(terminals) != 2 or lock.get("passed") is not True:
            raise ValueError("Provider preflight locked evidence did not pass")
        print(json.dumps(lock, sort_keys=True))
        return
    _verify_input_trees_again(args)
    if any(
        path.exists()
        for path in (
            private_root / "runtime" / "tune",
            private_root / "runtime" / "regression",
            private_root / "evaluation",
            private_root / "locks" / "ground-truth-lock.json",
            private_root / "locks" / "candidate-lock.json",
        )
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: preflight boundary is contaminated")
    config = _provider_config(Path(args.env_file))
    model_lock = _load_object(CONFIG_ROOT / "model-lock.json")
    budget_lock = _load_object(CONFIG_ROOT / "budget.json")
    preflight_budget = cast(Mapping[str, object], budget_lock["preflight"])
    prompt_reservation = _require_int(
        budget_lock["prompt_token_reservation_per_attempt"], "prompt reservation"
    )
    max_completion = _require_int(
        budget_lock["max_completion_tokens_per_attempt"], "completion budget"
    )
    budget = AttemptBudget(
        max_provider_attempts=_require_int(
            preflight_budget["max_provider_attempts"], "preflight attempt budget"
        ),
        max_retry_attempts=_require_int(
            preflight_budget["max_retry_attempts"], "preflight retry budget"
        ),
        prompt_token_reservation=prompt_reservation,
        max_completion_tokens=max_completion,
        max_conservative_tokens=(
            _require_int(
                preflight_budget["max_provider_attempts"],
                "preflight attempt budget",
            )
            * (prompt_reservation + max_completion)
        ),
    )
    base, source = _synthetic_inputs()
    hierarchy = build_hierarchical_context(base, source)
    assert_model_context_private(base, "synthetic-non-case", hierarchy)
    pacer = CrossLifecycleRequestPacer(
        _require_float(
            model_lock["minimum_request_spacing_seconds"], "Provider pacing"
        )
    )
    journal_root = private_root / "runtime" / "preflight" / "journal"
    output_root = private_root / "runtime" / "preflight" / "output"
    schedule_sha = _sha_file(private_root / "locks" / "schedule-lock.json")
    implementation_sha = _sha_file(private_root / "locks" / "implementation-lock.json")
    terminals = tuple(
        execute_live_arm(
            _preflight_record(arm, position),
            base=base,
            hierarchy=None if arm is Arm.B0 else hierarchy,
            journal_root=journal_root,
            output_root=output_root,
            schedule_sha256=schedule_sha,
            implementation_lock_sha256=implementation_sha,
            provider_config=config,
            expected_model=str(model_lock["model"]),
            timeout_seconds=_require_float(
                model_lock["timeout_seconds"], "Provider timeout"
            ),
            max_completion_tokens=max_completion,
            prompt_token_reservation=prompt_reservation,
            pacer=pacer,
            budget=budget,
            retry_policy_sha256=str(model_lock["transport_retry_policy_sha256"]),
        )
        for position, arm in enumerate((Arm.B0, Arm.H1), 1)
    )
    _verify_provider_run_sidecars(
        private_root, phase="preflight", terminals=terminals
    )
    passed = all(
        terminal.status is LiveTerminalStatus.COMPLETED
        and terminal.input_tokens_if_known is not None
        and terminal.output_tokens_if_known is not None
        and terminal.known_token_lower_bound > 0
        and terminal.failure_code is None
        for terminal in terminals
    )
    terminal_tree, terminal_files = tree_sha256(output_root / "terminals")
    run_attempt_tree, run_attempt_files = tree_sha256(journal_root / "run-attempts")
    provider_tree, provider_files = tree_sha256(journal_root / "runs")
    lock = {
        "arms": {
            terminal.arm.value: {
                "known_usage": terminal.input_tokens_if_known is not None,
                "provider_attempts": terminal.provider_attempts,
                "status": terminal.status.value,
                "transport_retries": terminal.transport_retries,
            }
            for terminal in terminals
        },
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": implementation["implementation_commit"],
        "input_reverification_lock_sha256": _sha_file(
            private_root / "locks" / "input-reverification-lock.json"
        ),
        "passed": passed,
        "provider_attempt_files": provider_files,
        "provider_attempt_tree_sha256": provider_tree,
        "run_attempt_files": run_attempt_files,
        "run_attempt_tree_sha256": run_attempt_tree,
        "schedule_lock_sha256": schedule_sha,
        "schema_version": "strong-single-hierarchical-live.provider-preflight.v1",
        "semantic_operations": sum(
            terminal.semantic_model_operations for terminal in terminals
        ),
        "terminal_files": terminal_files,
        "terminal_tree_sha256": terminal_tree,
    }
    lock_sha = _write_create_once(
        private_root / "locks" / "provider-preflight-lock.json", lock
    )
    if not passed:
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_OR_SCHEMA_PREFLIGHT")
    _advance_state(
        private_root,
        "PROVIDER_PREFLIGHT_PASSED",
        predecessor="INPUTS_REVERIFIED",
        lock_name="provider-preflight-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps(lock, sort_keys=True))


def _verify_preflight_lock_again(private_root: Path) -> tuple[LiveTerminalRecord, ...]:
    lock_path = private_root / "locks" / "provider-preflight-lock.json"
    lock = _load_object(lock_path)
    created_at = _require_utc_timestamp(
        lock.get("created_at_utc"), "Provider preflight lock"
    )
    output_root = private_root / "runtime" / "preflight" / "output"
    journal_root = private_root / "runtime" / "preflight" / "journal"
    schedule_sha = _sha_file(private_root / "locks" / "schedule-lock.json")
    implementation_lock_sha = _sha_file(
        private_root / "locks" / "implementation-lock.json"
    )
    records = tuple(
        _preflight_record(arm, position)
        for position, arm in enumerate((Arm.B0, Arm.H1), 1)
    )
    expected_ids = {item.run_id for item in records}
    terminal_paths = tuple(sorted((output_root / "terminals").glob("*.json")))
    attempt_paths = tuple(sorted((journal_root / "run-attempts").glob("*.json")))
    if (
        {path.stem for path in terminal_paths} != expected_ids
        or {path.stem for path in attempt_paths} != expected_ids
    ):
        raise ValueError("Provider preflight raw file set differs")
    terminals: list[LiveTerminalRecord] = []
    for record in records:
        terminal = LiveTerminalRecord.model_validate_json(
            (output_root / "terminals" / f"{record.run_id}.json").read_text(
                encoding="utf-8"
            )
        )
        attempt = LiveRunAttempt.model_validate_json(
            (journal_root / "run-attempts" / f"{record.run_id}.json").read_text(
                encoding="utf-8"
            )
        )
        validate_terminal_binding(
            record,
            terminal,
            attempt,
            schedule_sha256=schedule_sha,
            implementation_lock_sha256=implementation_lock_sha,
        )
        terminals.append(terminal)
    _verify_provider_run_sidecars(
        private_root, phase="preflight", terminals=terminals
    )
    terminal_tree, terminal_files = tree_sha256(output_root / "terminals")
    run_attempt_tree, run_attempt_files = tree_sha256(journal_root / "run-attempts")
    provider_tree, provider_files = tree_sha256(journal_root / "runs")
    implementation = _load_object(
        private_root / "locks" / "implementation-lock.json"
    )
    expected = {
        "arms": {
            terminal.arm.value: {
                "known_usage": terminal.input_tokens_if_known is not None,
                "provider_attempts": terminal.provider_attempts,
                "status": terminal.status.value,
                "transport_retries": terminal.transport_retries,
            }
            for terminal in terminals
        },
        "created_at_utc": created_at,
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": implementation["implementation_commit"],
        "input_reverification_lock_sha256": _sha_file(
            private_root / "locks" / "input-reverification-lock.json"
        ),
        "passed": True,
        "provider_attempt_files": provider_files,
        "provider_attempt_tree_sha256": provider_tree,
        "run_attempt_files": run_attempt_files,
        "run_attempt_tree_sha256": run_attempt_tree,
        "schedule_lock_sha256": schedule_sha,
        "schema_version": "strong-single-hierarchical-live.provider-preflight.v1",
        "semantic_operations": sum(
            terminal.semantic_model_operations for terminal in terminals
        ),
        "terminal_files": terminal_files,
        "terminal_tree_sha256": terminal_tree,
    }
    if lock != expected or any(
        terminal.status is not LiveTerminalStatus.COMPLETED for terminal in terminals
    ):
        raise ValueError("Provider preflight lock/raw evidence differs")
    return tuple(terminals)


def _scheduled_arm(record: Mapping[str, object]) -> ScheduledArm:
    try:
        return ScheduledArm(
            split=cast(Literal["TUNE", "REGRESSION"], record["split"]),
            pair_position=_require_int(record["pair_position"], "pair position"),
            arm_position=_require_int(record["arm_position"], "arm position"),
            opaque_case_id=str(record["opaque_case_id"]),
            source=str(record["source"]),
            source_key=str(record["source_key"]),
            arm=Arm(str(record["arm"])),
            run_id=str(record["run_id"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("private paired schedule record is invalid") from error


def _phase_budget(
    private_root: Path,
    *,
    phase: Literal["tune", "regression"],
    prompt_reservation: int,
    max_completion: int,
) -> AttemptBudget:
    budget_lock = _load_object(CONFIG_ROOT / "budget.json")
    values = cast(Mapping[str, object], budget_lock[phase])
    run_root = private_root / "runtime" / phase / "journal" / "runs"
    existing = tuple(sorted(path for path in run_root.glob("*") if path.is_dir()))
    keyword_args = {
        "max_provider_attempts": _require_int(
            values["max_provider_attempts"], "phase attempt budget"
        ),
        "max_retry_attempts": _require_int(
            values["max_retry_attempts"], "phase retry budget"
        ),
        "prompt_token_reservation": prompt_reservation,
        "max_completion_tokens": max_completion,
        "max_conservative_tokens": _require_int(
            values["max_conservative_tokens"], "phase token budget"
        ),
    }
    if existing:
        return AttemptBudget.restore(existing, **keyword_args)
    return AttemptBudget(**keyword_args)


def _verify_provider_run_sidecars(
    private_root: Path,
    *,
    phase: Literal["preflight", "tune", "regression"],
    terminals: Sequence[LiveTerminalRecord],
    allowed_orphan_run_ids: frozenset[str] = frozenset(),
) -> None:
    model_lock = _load_object(CONFIG_ROOT / "model-lock.json")
    budget_lock = _load_object(CONFIG_ROOT / "budget.json")
    prompt_reservation = _require_int(
        budget_lock["prompt_token_reservation_per_attempt"],
        "prompt reservation",
    )
    max_completion = _require_int(
        budget_lock["max_completion_tokens_per_attempt"],
        "completion budget",
    )
    run_parent = private_root / "runtime" / phase / "journal" / "runs"
    expected_run_ids = {
        terminal.run_id
        for terminal in terminals
        if terminal.semantic_model_operations > 0
    }
    observed_run_ids = {
        path.name for path in run_parent.glob("*") if path.is_dir()
    }
    if (
        not expected_run_ids.issubset(observed_run_ids)
        or not (observed_run_ids - expected_run_ids).issubset(
            allowed_orphan_run_ids
        )
    ):
        raise ValueError(f"{phase} Provider sidecar run set differs")
    for terminal in terminals:
        if terminal.semantic_model_operations == 0:
            if (
                terminal.provider_attempts != 0
                or terminal.transport_retries != 0
            ):
                raise ValueError(f"{phase} local terminal Provider accounting differs")
            continue
        semantics, attempts = verify_provider_sidecar(
            run_parent / terminal.run_id,
            expected_semantic_operations=terminal.semantic_model_operations,
            expected_policy_lock_sha256=str(
                model_lock["transport_retry_policy_sha256"]
            ),
            expected_timeout_seconds=_require_float(
                model_lock["timeout_seconds"], "Provider timeout"
            ),
            prompt_token_reservation=prompt_reservation,
            max_completion_tokens=max_completion,
        )
        accounting = rebuild_attempt_accounting(
            (run_parent / terminal.run_id,),
            prompt_token_reservation=prompt_reservation,
            max_completion_tokens=max_completion,
        )
        request_hashes = {
            request_hash
            for semantic in semantics
            for request_hash in semantic.request_sha256s
        }
        request_binding_valid = (
            not request_hashes and terminal.request_sha256 is None
            if terminal.provider_attempts == 0
            else terminal.request_sha256 in request_hashes
            and len(request_hashes) == 1
        )
        if (
            len(attempts) != terminal.provider_attempts
            or accounting.provider_attempt_count != terminal.provider_attempts
            or accounting.retry_attempt_count != terminal.transport_retries
            or accounting.known_token_lower_bound != terminal.known_token_lower_bound
            or accounting.conservative_token_upper_bound
            != terminal.conservative_token_upper_bound
            or not request_binding_valid
            or any(
                semantic.operation_type != "FINAL_JUDGE" for semantic in semantics
            )
        ):
            raise ValueError(f"{phase} Provider sidecar/terminal binding differs")
        if terminal.input_tokens_if_known is not None and (
            terminal.output_tokens_if_known is None
            or terminal.input_tokens_if_known + terminal.output_tokens_if_known
            != terminal.known_token_lower_bound
        ):
            raise ValueError(f"{phase} Provider usage/terminal binding differs")


def _validate_http429_boundary(
    split: Literal["TUNE", "REGRESSION"],
    terminals: Sequence[LiveTerminalRecord],
) -> None:
    http_429 = tuple(item for item in terminals if item.failure_code == "HTTP_429")
    if not http_429:
        raise ValueError(f"{split} HTTP429 boundary lacks a terminal HTTP429")
    abort_pair = min(item.pair_position for item in http_429)
    if split == "TUNE":
        if (
            terminals[-1].pair_position != abort_pair
            or any(item.pair_position > abort_pair for item in terminals)
        ):
            raise ValueError("TUNE HTTP429 abort exceeds its schedule boundary")
        return
    for terminal in terminals:
        if terminal.pair_position <= abort_pair:
            continue
        if (
            terminal.status is not LiveTerminalStatus.NOT_ADMITTED
            or terminal.failure_code != "NOT_ADMITTED_AFTER_HTTP429"
            or terminal.semantic_model_operations != 0
            or terminal.provider_attempts != 0
            or terminal.transport_retries != 0
        ):
            raise ValueError("REGRESSION admitted a terminal after HTTP429")


def _validate_partial_phase_records(
    private_root: Path,
    *,
    split: Literal["TUNE", "REGRESSION"],
    allow_orphan_attempt: bool,
    require_http_429_boundary: bool = False,
) -> tuple[tuple[LiveTerminalRecord, ...], dict[str, int | str]]:
    """Validate an immutable schedule prefix before recovery or abort sealing."""

    records = tuple(
        _scheduled_arm(item) for item in _load_private_schedule(private_root, split)
    )
    phase = split.casefold()
    output_root = private_root / "runtime" / phase / "output" / "terminals"
    journal_root = private_root / "runtime" / phase / "journal"
    attempt_root = journal_root / "run-attempts"
    schedule_sha = _sha_file(private_root / "schedules" / f"{phase}.json")
    implementation_sha = _sha_file(
        private_root / "locks" / "implementation-lock.json"
    )
    terminal_paths = tuple(sorted(output_root.glob("*.json")))
    attempt_paths = tuple(sorted(attempt_root.glob("*.json")))
    if any(
        path.is_symlink() or not path.is_file()
        for path in (*terminal_paths, *attempt_paths)
    ):
        raise ValueError(f"{split} partial execution contains an invalid file")
    schedule_ids = tuple(item.run_id for item in records)
    terminal_ids = {path.stem for path in terminal_paths}
    if terminal_ids != set(schedule_ids[: len(terminal_paths)]):
        raise ValueError(f"{split} partial terminal set is not a schedule prefix")
    attempt_ids = {path.stem for path in attempt_paths}
    if not attempt_ids.issubset(set(schedule_ids)):
        raise ValueError(f"{split} partial run-attempt set differs from schedule")

    terminals: list[LiveTerminalRecord] = []
    required_attempt_ids: set[str] = set()
    for record in records[: len(terminal_paths)]:
        terminal = LiveTerminalRecord.model_validate_json(
            (output_root / f"{record.run_id}.json").read_text(encoding="utf-8")
        )
        attempt_path = attempt_root / f"{record.run_id}.json"
        attempt = (
            None
            if not attempt_path.exists()
            else LiveRunAttempt.model_validate_json(
                attempt_path.read_text(encoding="utf-8")
            )
        )
        validate_terminal_binding(
            record,
            terminal,
            attempt,
            schedule_sha256=schedule_sha,
            implementation_lock_sha256=implementation_sha,
        )
        if terminal.status is not LiveTerminalStatus.NOT_ADMITTED:
            required_attempt_ids.add(record.run_id)
        terminals.append(terminal)
    if not required_attempt_ids.issubset(attempt_ids):
        raise ValueError(f"{split} partial run-attempt binding is incomplete")
    unexpected_attempt_ids = attempt_ids - required_attempt_ids
    orphan_run_ids: frozenset[str] = frozenset()
    if unexpected_attempt_ids:
        next_index = len(terminals)
        allowed_orphan = (
            set()
            if next_index >= len(records)
            else {records[next_index].run_id}
        )
        if (
            not allow_orphan_attempt
            or unexpected_attempt_ids != allowed_orphan
        ):
            raise ValueError(f"{split} partial run-attempt set is not resumable")
        orphan = records[next_index]
        attempt = LiveRunAttempt.model_validate_json(
            (attempt_root / f"{orphan.run_id}.json").read_text(encoding="utf-8")
        )
        observed = (
            attempt.run_id,
            attempt.opaque_case_id,
            attempt.split,
            attempt.pair_position,
            attempt.arm_position,
            attempt.arm,
            attempt.schedule_sha256,
            attempt.implementation_lock_sha256,
        )
        expected = (
            orphan.run_id,
            orphan.opaque_case_id,
            orphan.split,
            orphan.pair_position,
            orphan.arm_position,
            orphan.arm,
            schedule_sha,
            implementation_sha,
        )
        if observed != expected:
            raise ValueError(f"{split} orphan run-attempt binding differs")
        orphan_run_ids = frozenset((orphan.run_id,))

    if require_http_429_boundary:
        if orphan_run_ids:
            raise ValueError(f"{split} HTTP429 abort boundary has an orphan attempt")
        _validate_http429_boundary(split, terminals)

    terminal_tuple = tuple(terminals)
    _verify_provider_run_sidecars(
        private_root,
        phase=cast(Literal["tune", "regression"], phase),
        terminals=terminal_tuple,
        allowed_orphan_run_ids=orphan_run_ids,
    )
    terminal_tree, terminal_files = tree_sha256(output_root)
    attempt_tree, attempt_files = tree_sha256(attempt_root)
    provider_tree, provider_files = tree_sha256(journal_root / "runs")
    return terminal_tuple, {
        "provider_attempt_files": provider_files,
        "provider_attempt_tree_sha256": provider_tree,
        "run_attempt_files": attempt_files,
        "run_attempt_tree_sha256": attempt_tree,
        "terminal_files": terminal_files,
        "terminal_tree_sha256": terminal_tree,
    }


def _seal_http429_orphan(
    private_root: Path,
    terminals: Sequence[LiveTerminalRecord],
    *,
    split: Literal["TUNE", "REGRESSION"],
    timeout_seconds: float,
    max_completion_tokens: int,
    prompt_token_reservation: int,
    retry_policy_sha256: str,
) -> None:
    if not terminals or not any(
        item.failure_code == "HTTP_429" for item in terminals
    ):
        return
    records = tuple(
        _scheduled_arm(item)
        for item in _load_private_schedule(private_root, split)
    )
    next_index = len(terminals)
    if next_index >= len(records):
        return
    orphan = records[next_index]
    attempt_path = (
        private_root
        / "runtime"
        / split.casefold()
        / "journal"
        / "run-attempts"
        / f"{orphan.run_id}.json"
    )
    if not attempt_path.exists():
        return
    abort_pair = min(
        item.pair_position
        for item in terminals
        if item.failure_code == "HTTP_429"
    )
    if orphan.pair_position != abort_pair:
        raise ValueError(f"{split} HTTP429 orphan exceeds its abort pair")
    seal_interrupted_live_arm(
        orphan,
        journal_root=private_root / "runtime" / split.casefold() / "journal",
        output_root=private_root / "runtime" / split.casefold() / "output",
        schedule_sha256=_sha_file(
            private_root / "schedules" / f"{split.casefold()}.json"
        ),
        implementation_lock_sha256=_sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
        timeout_seconds=timeout_seconds,
        max_completion_tokens=max_completion_tokens,
        prompt_token_reservation=prompt_token_reservation,
        retry_policy_sha256=retry_policy_sha256,
    )


def _execution_summary_payload(
    phase: Literal["tune", "regression"],
    terminals: Sequence[LiveTerminalRecord],
    *,
    created_at_utc: str,
) -> dict[str, int | str]:
    planned = 326 if phase == "tune" else 240
    payload: dict[str, int | str] = {
        "completed": sum(
            item.status is LiveTerminalStatus.COMPLETED for item in terminals
        ),
        "created_at_utc": created_at_utc,
        "evaluation_version": EVALUATION_VERSION,
        "fusion_calls": sum(item.fusion_model_calls for item in terminals),
        "http_429": sum(item.failure_code == "HTTP_429" for item in terminals),
        "planned": planned,
        "provider_attempts": sum(item.provider_attempts for item in terminals),
        "schema_version": (
            f"strong-single-hierarchical-live.{phase}-execution.v1"
        ),
        "semantic_model_operations": sum(
            item.semantic_model_operations for item in terminals
        ),
        "specialist_calls": sum(item.specialist_calls for item in terminals),
        "terminalized": len(terminals),
        "transport_retries": sum(item.transport_retries for item in terminals),
    }
    if phase == "regression":
        payload["not_admitted"] = sum(
            item.status is LiveTerminalStatus.NOT_ADMITTED for item in terminals
        )
    return payload


def _verify_execution_summary_again(
    private_root: Path, *, phase: Literal["tune", "regression"]
) -> tuple[LiveTerminalRecord, ...]:
    split = cast(Literal["TUNE", "REGRESSION"], phase.upper())
    terminals, _evidence = _validate_partial_phase_records(
        private_root,
        split=split,
        allow_orphan_attempt=False,
    )
    expected_count = 326 if phase == "tune" else 240
    if len(terminals) != expected_count:
        raise ValueError(f"{split} execution summary lacks its full terminal set")
    summary_path = private_root / "runtime" / phase / "execution-summary.json"
    summary = _load_object(summary_path)
    created_at = _require_utc_timestamp(
        summary.get("created_at_utc"), f"{split} execution summary"
    )
    expected = _execution_summary_payload(
        phase, terminals, created_at_utc=created_at
    )
    if summary != expected:
        raise ValueError(f"{split} execution summary/raw evidence differs")
    return terminals


def _verify_tune_http429_abort_again(private_root: Path) -> None:
    terminals, evidence = _validate_partial_phase_records(
        private_root,
        split="TUNE",
        allow_orphan_attempt=False,
        require_http_429_boundary=True,
    )
    summary_path = private_root / "runtime" / "tune" / "execution-summary.json"
    summary = _load_object(summary_path)
    summary_created_at = _require_utc_timestamp(
        summary.get("created_at_utc"), "TUNE HTTP429 execution summary"
    )
    if summary != _execution_summary_payload(
        "tune", terminals, created_at_utc=summary_created_at
    ):
        raise ValueError("TUNE HTTP429 execution summary/raw evidence differs")
    lock_path = private_root / "locks" / "tune-http429-abort-lock.json"
    lock = _load_object(lock_path)
    created_at = _require_utc_timestamp(
        lock.get("created_at_utc"), "TUNE HTTP429 abort lock"
    )
    expected: dict[str, object] = {
        "created_at_utc": created_at,
        "evaluation_version": EVALUATION_VERSION,
        "execution_summary_sha256": _sha_file(summary_path),
        "implementation_lock_sha256": _sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
        "input_reverification_lock_sha256": _sha_file(
            private_root / "locks" / "input-reverification-lock.json"
        ),
        "preflight_lock_sha256": _sha_file(
            private_root / "locks" / "provider-preflight-lock.json"
        ),
        "provider_attempts": sum(item.provider_attempts for item in terminals),
        **evidence,
        "schedule_sha256": _sha_file(
            private_root / "schedules" / "tune.json"
        ),
        "schema_version": "strong-single-hierarchical-live.tune-http429-abort.v1",
        "terminalized": len(terminals),
        "transport_retries": sum(
            item.transport_retries for item in terminals
        ),
    }
    if lock != expected:
        raise ValueError("TUNE HTTP429 abort lock/raw evidence differs")


def _has_state(private_root: Path, name: str) -> bool:
    return (private_root / "state" / f"{name}.json").exists()


def _has_any_state(private_root: Path, names: Sequence[str]) -> bool:
    return any(_has_state(private_root, name) for name in names)


def run_tune(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "PROVIDER_PREFLIGHT_PASSED")
    _verify_implementation(private_root)
    if _has_any_state(
        private_root,
        (
            "TUNE_TERMINALS_LOCKED",
            "GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK",
            "TUNE_SCORED",
            "CANDIDATE_FROZEN",
            "REGRESSION_EXECUTED",
            "REGRESSION_TERMINALS_LOCKED",
            "REGRESSION_SCORED",
            "PUBLIC_RESULT_FROZEN",
        ),
    ) and not _has_state(private_root, "TUNE_EXECUTED"):
        raise ValueError("TUNE execution state is missing below a descendant")
    abort_lock_path = private_root / "locks" / "tune-http429-abort-lock.json"
    if abort_lock_path.exists() or _has_state(private_root, "TUNE_ABORTED_HTTP429"):
        _verify_tune_http429_abort_again(private_root)
        if not _has_state(private_root, "TUNE_ABORTED_HTTP429"):
            _advance_state(
                private_root,
                "TUNE_ABORTED_HTTP429",
                predecessor="PROVIDER_PREFLIGHT_PASSED",
                lock_name=abort_lock_path.name,
                lock_sha256=_sha_file(abort_lock_path),
            )
        _require_state(private_root, "TUNE_ABORTED_HTTP429")
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_DURING_TUNE")
    tune_summary_path = private_root / "runtime" / "tune" / "execution-summary.json"
    if _has_state(private_root, "TUNE_EXECUTED") and not tune_summary_path.exists():
        raise ValueError("TUNE descendant state lacks its execution summary")
    if tune_summary_path.exists():
        existing_summary = _load_object(tune_summary_path)
        if existing_summary.get("http_429") == 0:
            _verify_execution_summary_again(private_root, phase="tune")
            if not _has_state(private_root, "TUNE_EXECUTED"):
                _advance_state(
                    private_root,
                    "TUNE_EXECUTED",
                    predecessor="PROVIDER_PREFLIGHT_PASSED",
                    lock_name=tune_summary_path.name,
                    lock_sha256=_sha_file(tune_summary_path),
                )
            _require_state(private_root, "TUNE_EXECUTED")
            if (private_root / "locks" / "tune-terminal-lock.json").exists():
                _verify_terminal_lock_again(private_root, split="TUNE")
            print(json.dumps(existing_summary, sort_keys=True))
            return
    _verify_input_trees_again(args)
    _verify_preflight_lock_again(private_root)
    records = _load_private_schedule(private_root, "TUNE")
    model_lock = _load_object(CONFIG_ROOT / "model-lock.json")
    budget_lock = _load_object(CONFIG_ROOT / "budget.json")
    prompt_reservation = _require_int(
        budget_lock["prompt_token_reservation_per_attempt"], "prompt reservation"
    )
    max_completion = _require_int(
        budget_lock["max_completion_tokens_per_attempt"], "completion budget"
    )
    budget = _phase_budget(
        private_root,
        phase="tune",
        prompt_reservation=prompt_reservation,
        max_completion=max_completion,
    )
    pacer = CrossLifecycleRequestPacer(
        _require_float(
            model_lock["minimum_request_spacing_seconds"], "Provider pacing"
        )
    )
    journal_root = private_root / "runtime" / "tune" / "journal"
    output_root = private_root / "runtime" / "tune" / "output"
    existing_terminals, _existing_evidence = _validate_partial_phase_records(
        private_root,
        split="TUNE",
        allow_orphan_attempt=True,
    )
    http_429 = any(item.failure_code == "HTTP_429" for item in existing_terminals)
    if http_429:
        _seal_http429_orphan(
            private_root,
            existing_terminals,
            split="TUNE",
            timeout_seconds=_require_float(
                model_lock["timeout_seconds"], "Provider timeout"
            ),
            max_completion_tokens=max_completion,
            prompt_token_reservation=prompt_reservation,
            retry_policy_sha256=str(
                model_lock["transport_retry_policy_sha256"]
            ),
        )
    config = None if http_429 else _provider_config(Path(args.env_file))
    implementation_sha = _sha_file(private_root / "locks" / "implementation-lock.json")
    schedule_sha = _sha_file(private_root / "schedules" / "tune.json")
    methodology_path = Path(args.methodology)
    if (
        _sha_file(methodology_path)
        != _load_object(CONFIG_ROOT / "context-policy.json").get("methodology_sha256")
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: methodology differs")
    methodology = _load_object(methodology_path)
    obss = _obss_index(Path(args.ob_root), Path(args.ss_root))
    for pair_index in range(0, len(records), 2):
        if http_429:
            break
        raw_pair = records[pair_index : pair_index + 2]
        scheduled = tuple(_scheduled_arm(item) for item in raw_pair)
        if (
            len(scheduled) != 2
            or scheduled[0].opaque_case_id != scheduled[1].opaque_case_id
            or {item.arm for item in scheduled} != {Arm.B0, Arm.H1}
            or scheduled[0].source != scheduled[1].source
            or scheduled[0].source_key != scheduled[1].source_key
        ):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: TUNE pair integrity differs")
        source = scheduled[0].source
        source_key = scheduled[0].source_key
        local_failure: Literal["INPUT_PROJECTION_FAILURE", "PRIVACY_FAILURE"] | None = None
        base: LiveBaseContext | None = None
        hierarchy = None
        try:
            if source == "RCA100":
                base, hierarchy_source = build_rca100_live_inputs(
                    Path(args.rca_cases_root) / source_key,
                    projection_case_number=scheduled[0].pair_position,
                    methodology=methodology,
                )
            elif source == "OBSS" and source_key in obss:
                base, hierarchy_source = build_obss_live_inputs(obss[source_key])
            else:
                raise ValueError("TUNE schedule source identity is invalid")
            hierarchy = build_hierarchical_context(base, hierarchy_source)
        except Exception:
            local_failure = "INPUT_PROJECTION_FAILURE"
        if base is not None and local_failure is None:
            try:
                assert_model_context_private(base, source_key, hierarchy)
            except Exception:
                local_failure = "PRIVACY_FAILURE"
        pair_terminals: list[LiveTerminalRecord] = []
        for item in scheduled:
            if local_failure is not None or base is None or hierarchy is None:
                terminal = terminalize_local_failure(
                    item,
                    status=local_failure or "INPUT_PROJECTION_FAILURE",
                    journal_root=journal_root,
                    output_root=output_root,
                    schedule_sha256=schedule_sha,
                    implementation_lock_sha256=implementation_sha,
                )
            else:
                if config is None:
                    raise AssertionError("TUNE Provider config is missing")
                terminal = execute_live_arm(
                    item,
                    base=base,
                    hierarchy=None if item.arm is Arm.B0 else hierarchy,
                    journal_root=journal_root,
                    output_root=output_root,
                    schedule_sha256=schedule_sha,
                    implementation_lock_sha256=implementation_sha,
                    provider_config=config,
                    expected_model=str(model_lock["model"]),
                    timeout_seconds=_require_float(
                        model_lock["timeout_seconds"], "Provider timeout"
                    ),
                    max_completion_tokens=max_completion,
                    prompt_token_reservation=prompt_reservation,
                    pacer=pacer,
                    budget=budget,
                    retry_policy_sha256=str(
                        model_lock["transport_retry_policy_sha256"]
                    ),
                )
            pair_terminals.append(terminal)
            print(
                f"[tune] {pair_index // 2 + 1}/163 {item.arm.value} "
                f"{terminal.status.value}",
                flush=True,
            )
        if any(item.failure_code == "HTTP_429" for item in pair_terminals):
            http_429 = True
            break
    terminals, partial_evidence = _validate_partial_phase_records(
        private_root,
        split="TUNE",
        allow_orphan_attempt=False,
        require_http_429_boundary=http_429,
    )
    summary = _execution_summary_payload(
        "tune", terminals, created_at_utc=_utc_now()
    )
    summary_sha = _write_create_once(
        private_root / "runtime" / "tune" / "execution-summary.json", summary
    )
    if http_429 or summary["http_429"]:
        if not http_429:
            raise ValueError("TUNE HTTP429 summary/boundary differs")
        lock_sha = _write_create_once(
            private_root / "locks" / "tune-http429-abort-lock.json",
            {
                "created_at_utc": _utc_now(),
                "evaluation_version": EVALUATION_VERSION,
                "execution_summary_sha256": summary_sha,
                "implementation_lock_sha256": implementation_sha,
                "input_reverification_lock_sha256": _sha_file(
                    private_root / "locks" / "input-reverification-lock.json"
                ),
                "preflight_lock_sha256": _sha_file(
                    private_root / "locks" / "provider-preflight-lock.json"
                ),
                "provider_attempts": sum(
                    item.provider_attempts for item in terminals
                ),
                **partial_evidence,
                "schedule_sha256": schedule_sha,
                "schema_version": "strong-single-hierarchical-live.tune-http429-abort.v1",
                "terminalized": len(terminals),
                "transport_retries": sum(
                    item.transport_retries for item in terminals
                ),
            },
        )
        _advance_state(
            private_root,
            "TUNE_ABORTED_HTTP429",
            predecessor="PROVIDER_PREFLIGHT_PASSED",
            lock_name="tune-http429-abort-lock.json",
            lock_sha256=lock_sha,
        )
        raise ValueError("BLOCKED_PROVIDER_CAPACITY_DURING_TUNE")
    if len(terminals) != 326:
        raise ValueError("TUNE execution is incomplete but resumable without reissue")
    _advance_state(
        private_root,
        "TUNE_EXECUTED",
        predecessor="PROVIDER_PREFLIGHT_PASSED",
        lock_name="execution-summary.json",
        lock_sha256=summary_sha,
    )
    print(json.dumps(summary, sort_keys=True))


def _validated_phase_terminals(
    private_root: Path, *, split: Literal["TUNE", "REGRESSION"]
) -> tuple[tuple[ScheduledArm, ...], tuple[LiveTerminalRecord, ...]]:
    records = tuple(
        _scheduled_arm(item) for item in _load_private_schedule(private_root, split)
    )
    phase = split.casefold()
    output_root = private_root / "runtime" / phase / "output" / "terminals"
    attempt_root = private_root / "runtime" / phase / "journal" / "run-attempts"
    schedule_sha = _sha_file(private_root / "schedules" / f"{phase}.json")
    implementation_sha = _sha_file(
        private_root / "locks" / "implementation-lock.json"
    )
    expected_ids = {item.run_id for item in records}
    terminal_paths = tuple(sorted(output_root.glob("*.json")))
    if {path.stem for path in terminal_paths} != expected_ids:
        raise ValueError(f"{split} terminal file set differs from schedule")
    attempt_paths = tuple(sorted(attempt_root.glob("*.json")))
    if not {path.stem for path in attempt_paths}.issubset(expected_ids):
        raise ValueError(f"{split} run-attempt file set differs from schedule")
    terminals: list[LiveTerminalRecord] = []
    for record in records:
        terminal = LiveTerminalRecord.model_validate_json(
            (output_root / f"{record.run_id}.json").read_text(encoding="utf-8")
        )
        attempt_path = attempt_root / f"{record.run_id}.json"
        attempt = (
            None
            if not attempt_path.exists()
            else LiveRunAttempt.model_validate_json(
                attempt_path.read_text(encoding="utf-8")
            )
        )
        validate_terminal_binding(
            record,
            terminal,
            attempt,
            schedule_sha256=schedule_sha,
            implementation_lock_sha256=implementation_sha,
        )
        if (
            terminal.status is LiveTerminalStatus.NOT_ADMITTED
            and attempt is not None
        ):
            raise ValueError(f"{split} not-admitted terminal has a run-attempt")
        terminals.append(terminal)
    if any(item.failure_code == "HTTP_429" for item in terminals):
        _validate_http429_boundary(split, terminals)
    return records, tuple(terminals)


def lock_tune_terminals(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "TUNE_EXECUTED")
    implementation = _verify_implementation(private_root)
    if (private_root / "locks" / "ground-truth-lock.json").exists():
        raise ValueError("BLOCKED_GROUND_TRUTH_LEAKAGE: GT lock predates terminal lock")
    records, terminals = _validated_phase_terminals(private_root, split="TUNE")
    _verify_provider_run_sidecars(private_root, phase="tune", terminals=terminals)
    output_root = private_root / "runtime" / "tune" / "output"
    journal_root = private_root / "runtime" / "tune" / "journal"
    if len({item.opaque_case_id for item in terminals}) != 163:
        raise ValueError("TUNE terminal pair accounting differs")
    run_attempts = tuple(sorted((journal_root / "run-attempts").glob("*.json")))
    if len(run_attempts) != 326:
        raise ValueError("TUNE run-attempt accounting differs")
    run_roots = tuple(sorted(path for path in (journal_root / "runs").glob("*") if path.is_dir()))
    budget_lock = _load_object(CONFIG_ROOT / "budget.json")
    accounting = rebuild_attempt_accounting(
        run_roots,
        prompt_token_reservation=_require_int(
            budget_lock["prompt_token_reservation_per_attempt"],
            "prompt reservation",
        ),
        max_completion_tokens=_require_int(
            budget_lock["max_completion_tokens_per_attempt"],
            "completion budget",
        ),
    )
    if (
        accounting.provider_attempt_count
        != sum(item.provider_attempts for item in terminals)
        or accounting.retry_attempt_count
        != sum(item.transport_retries for item in terminals)
    ):
        raise ValueError("TUNE Provider attempt accounting differs")
    terminal_tree, terminal_files = tree_sha256(output_root / "terminals")
    run_attempt_tree, run_attempt_files = tree_sha256(journal_root / "run-attempts")
    provider_tree, provider_files = tree_sha256(journal_root / "runs")
    lock = {
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "ground_truth_loaded_before_lock": False,
        "implementation_commit": implementation["implementation_commit"],
        "provider_attempt_files": provider_files,
        "provider_attempt_tree_sha256": provider_tree,
        "provider_attempts": accounting.provider_attempt_count,
        "run_attempt_files": run_attempt_files,
        "run_attempt_tree_sha256": run_attempt_tree,
        "schedule_sha256": _sha_file(private_root / "schedules" / "tune.json"),
        "schema_version": "strong-single-hierarchical-live.tune-terminal-lock.v1",
        "terminal_files": terminal_files,
        "terminal_tree_sha256": terminal_tree,
        "transport_retries": accounting.retry_attempt_count,
    }
    lock_sha = _write_create_once(
        private_root / "locks" / "tune-terminal-lock.json", lock
    )
    _advance_state(
        private_root,
        "TUNE_TERMINALS_LOCKED",
        predecessor="TUNE_EXECUTED",
        lock_name="tune-terminal-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps(lock, sort_keys=True))


def score_tune(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "TUNE_TERMINALS_LOCKED")
    _verify_implementation(private_root)
    _verify_input_trees_again(args)
    _verify_terminal_lock_again(private_root, split="TUNE")
    if any(name in os.environ for name in ("ECOMSRE_LLM_BASE_URL", "ECOMSRE_LLM_API_KEY", "ECOMSRE_LLM_MODEL")):
        raise ValueError("Provider credentials must be absent during evaluator-only scoring")
    answer_root = Path(args.answer_root)
    answer_tree, answer_files = tree_sha256(answer_root)
    if answer_files != 105:
        raise ValueError("RCA100 evaluator answer tree denominator differs")
    answer_source_lock = Path(args.answer_source_lock)
    if args.answer_source_lock_sha256 != EXPECTED_ANSWER_SOURCE_LOCK_SHA256:
        raise ValueError("RCA100 evaluator answer source lock expectation differs")
    if _sha_file(answer_source_lock) != args.answer_source_lock_sha256:
        raise ValueError("RCA100 evaluator answer source lock differs")
    ground_truth_lock = {
        "answer_source_lock_sha256": _sha_file(answer_source_lock),
        "answer_tree_files": answer_files,
        "answer_tree_sha256": answer_tree,
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "provider_credentials_present": False,
        "schema_version": "strong-single-hierarchical-live.ground-truth-lock.v1",
        "tune_terminal_lock_sha256": _sha_file(
            private_root / "locks" / "tune-terminal-lock.json"
        ),
    }
    ground_truth_sha = _write_create_once(
        private_root / "locks" / "ground-truth-lock.json", ground_truth_lock
    )
    _advance_state(
        private_root,
        "GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK",
        predecessor="TUNE_TERMINALS_LOCKED",
        lock_name="ground-truth-lock.json",
        lock_sha256=ground_truth_sha,
    )
    from scripts.rca_live.evaluator import case_scores_payload, evaluate_tune

    aggregate, scores = evaluate_tune(
        schedule_path=private_root / "schedules" / "tune.json",
        terminals_root=private_root / "runtime" / "tune" / "output",
        rca_cases_root=Path(args.rca_cases_root),
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
        answer_root=answer_root,
        implementation_lock_sha256=_sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
    )
    aggregate_sha = _write_create_once(
        private_root / "evaluation" / "tune-aggregate.json", aggregate
    )
    scores_sha = _write_create_once(
        private_root / "evaluation" / "tune-case-scores.json",
        case_scores_payload(scores),
    )
    scoring_lock = {
        "aggregate_sha256": aggregate_sha,
        "case_scores_sha256": scores_sha,
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "ground_truth_lock_sha256": ground_truth_sha,
        "implementation_lock_sha256": _sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
        "schema_version": "strong-single-hierarchical-live.tune-scoring-lock.v1",
        "tune_terminal_lock_sha256": _sha_file(
            private_root / "locks" / "tune-terminal-lock.json"
        ),
        "verdict": cast(Mapping[str, object], aggregate["gate"])["verdict"],
    }
    scoring_sha = _write_create_once(
        private_root / "locks" / "tune-scoring-lock.json", scoring_lock
    )
    _advance_state(
        private_root,
        "TUNE_SCORED",
        predecessor="GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK",
        lock_name="tune-scoring-lock.json",
        lock_sha256=scoring_sha,
    )
    print(json.dumps(aggregate, sort_keys=True))


def _verified_tune_aggregate(private_root: Path) -> dict[str, object]:
    _verify_terminal_lock_again(private_root, split="TUNE")
    from scripts.rca_live.reporting import verify_scoring_artifact_hashes

    scoring = verify_scoring_artifact_hashes(private_root, "tune")
    if (
        scoring.get("ground_truth_lock_sha256")
        != _sha_file(private_root / "locks" / "ground-truth-lock.json")
        or scoring.get("implementation_lock_sha256")
        != _sha_file(private_root / "locks" / "implementation-lock.json")
        or scoring.get("tune_terminal_lock_sha256")
        != _sha_file(private_root / "locks" / "tune-terminal-lock.json")
    ):
        raise ValueError("TUNE scoring lock lineage differs")
    return _load_object(private_root / "evaluation" / "tune-aggregate.json")


def _verify_candidate_lock(private_root: Path) -> dict[str, object]:
    candidate = _load_object(private_root / "locks" / "candidate-lock.json")
    implementation = _load_object(
        private_root / "locks" / "implementation-lock.json"
    )
    if (
        candidate.get("candidate_id") != "strong-single-hierarchical-live-v1"
        or candidate.get("config_hashes") != _config_hashes()
        or candidate.get("implementation_commit")
        != implementation.get("implementation_commit")
        or candidate.get("prompt_hashes") != prompt_hashes()
        or candidate.get("runtime_changed_after_tune") is not False
        or candidate.get("tune_aggregate_sha256")
        != _sha_file(private_root / "evaluation" / "tune-aggregate.json")
        or candidate.get("tune_scoring_lock_sha256")
        != _sha_file(private_root / "locks" / "tune-scoring-lock.json")
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: candidate lock binding differs")
    return candidate


def freeze_candidate(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "TUNE_SCORED")
    implementation = _verify_implementation(private_root)
    aggregate = _verified_tune_aggregate(private_root)
    gate = aggregate.get("gate")
    if not isinstance(gate, Mapping) or gate.get("passed") is not True:
        raise ValueError("TUNE Gate did not pass; Regression is forbidden")
    candidate_id = "strong-single-hierarchical-live-v1"
    lock = {
        "candidate_id": candidate_id,
        "config_hashes": _config_hashes(),
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": implementation["implementation_commit"],
        "prompt_hashes": prompt_hashes(),
        "runtime_changed_after_tune": False,
        "schema_version": "strong-single-hierarchical-live.candidate-lock.v1",
        "tune_aggregate_sha256": _sha_file(
            private_root / "evaluation" / "tune-aggregate.json"
        ),
        "tune_scoring_lock_sha256": _sha_file(
            private_root / "locks" / "tune-scoring-lock.json"
        ),
    }
    lock_sha = _write_create_once(private_root / "locks" / "candidate-lock.json", lock)
    _advance_state(
        private_root,
        "CANDIDATE_FROZEN",
        predecessor="TUNE_SCORED",
        lock_name="candidate-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps(lock, sort_keys=True))


def run_regression(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "CANDIDATE_FROZEN")
    _verify_implementation(private_root)
    if _has_any_state(
        private_root,
        (
            "REGRESSION_TERMINALS_LOCKED",
            "REGRESSION_SCORED",
            "PUBLIC_RESULT_FROZEN",
        ),
    ) and not _has_state(private_root, "REGRESSION_EXECUTED"):
        raise ValueError("Regression execution state is missing below a descendant")
    regression_summary_path = (
        private_root / "runtime" / "regression" / "execution-summary.json"
    )
    if _has_state(
        private_root, "REGRESSION_EXECUTED"
    ) and not regression_summary_path.exists():
        raise ValueError("Regression descendant state lacks its execution summary")
    if regression_summary_path.exists():
        _verify_execution_summary_again(private_root, phase="regression")
        if not _has_state(private_root, "REGRESSION_EXECUTED"):
            _advance_state(
                private_root,
                "REGRESSION_EXECUTED",
                predecessor="CANDIDATE_FROZEN",
                lock_name=regression_summary_path.name,
                lock_sha256=_sha_file(regression_summary_path),
            )
        _require_state(private_root, "REGRESSION_EXECUTED")
        if (private_root / "locks" / "regression-terminal-lock.json").exists():
            _verify_terminal_lock_again(private_root, split="REGRESSION")
        print(
            json.dumps(_load_object(regression_summary_path), sort_keys=True)
        )
        return
    _verify_input_trees_again(args)
    _verify_preflight_lock_again(private_root)
    aggregate = _verified_tune_aggregate(private_root)
    gate = aggregate.get("gate")
    if not isinstance(gate, Mapping) or gate.get("passed") is not True:
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: TUNE Gate no longer passes")
    _verify_candidate_lock(private_root)
    records = _load_private_schedule(private_root, "REGRESSION")
    model_lock = _load_object(CONFIG_ROOT / "model-lock.json")
    budget_lock = _load_object(CONFIG_ROOT / "budget.json")
    prompt_reservation = _require_int(
        budget_lock["prompt_token_reservation_per_attempt"], "prompt reservation"
    )
    max_completion = _require_int(
        budget_lock["max_completion_tokens_per_attempt"], "completion budget"
    )
    budget = _phase_budget(
        private_root,
        phase="regression",
        prompt_reservation=prompt_reservation,
        max_completion=max_completion,
    )
    pacer = CrossLifecycleRequestPacer(
        _require_float(
            model_lock["minimum_request_spacing_seconds"], "Provider pacing"
        )
    )
    journal_root = private_root / "runtime" / "regression" / "journal"
    output_root = private_root / "runtime" / "regression" / "output"
    existing_terminals, _existing_evidence = _validate_partial_phase_records(
        private_root,
        split="REGRESSION",
        allow_orphan_attempt=True,
    )
    stop_after_pair = min(
        (
            item.pair_position
            for item in existing_terminals
            if item.failure_code == "HTTP_429"
        ),
        default=None,
    )
    if stop_after_pair is not None:
        _seal_http429_orphan(
            private_root,
            existing_terminals,
            split="REGRESSION",
            timeout_seconds=_require_float(
                model_lock["timeout_seconds"], "Provider timeout"
            ),
            max_completion_tokens=max_completion,
            prompt_token_reservation=prompt_reservation,
            retry_policy_sha256=str(
                model_lock["transport_retry_policy_sha256"]
            ),
        )
        existing_terminals, _existing_evidence = _validate_partial_phase_records(
            private_root,
            split="REGRESSION",
            allow_orphan_attempt=False,
            require_http_429_boundary=True,
        )
    config = (
        None
        if stop_after_pair is not None
        else _provider_config(Path(args.env_file))
    )
    implementation_sha = _sha_file(private_root / "locks" / "implementation-lock.json")
    schedule_sha = _sha_file(private_root / "schedules" / "regression.json")
    obss = _obss_index(Path(args.ob_root), Path(args.ss_root))
    for pair_index in range(0, len(records), 2):
        scheduled = tuple(
            _scheduled_arm(item) for item in records[pair_index : pair_index + 2]
        )
        if (
            len(scheduled) != 2
            or scheduled[0].opaque_case_id != scheduled[1].opaque_case_id
            or {item.arm for item in scheduled} != {Arm.B0, Arm.H1}
            or scheduled[0].source != "OBSS"
            or scheduled[1].source != "OBSS"
            or scheduled[0].source_key != scheduled[1].source_key
        ):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: Regression pair integrity differs")
        pair_terminals: list[LiveTerminalRecord] = []
        if (
            stop_after_pair is not None
            and scheduled[0].pair_position >= stop_after_pair
        ):
            for item in scheduled:
                terminal = terminalize_not_admitted(
                    item,
                    output_root=output_root,
                    schedule_sha256=schedule_sha,
                    implementation_lock_sha256=implementation_sha,
                )
                pair_terminals.append(terminal)
                print(
                    f"[regression] {pair_index // 2 + 1}/120 {item.arm.value} "
                    f"{terminal.status.value}",
                    flush=True,
                )
            continue
        source_key = scheduled[0].source_key
        base: LiveBaseContext | None = None
        hierarchy = None
        local_failure: Literal["INPUT_PROJECTION_FAILURE", "PRIVACY_FAILURE"] | None = None
        try:
            case = obss[source_key]
            base, hierarchy_source = build_obss_live_inputs(case)
            hierarchy = build_hierarchical_context(base, hierarchy_source)
        except Exception:
            local_failure = "INPUT_PROJECTION_FAILURE"
        if base is not None and local_failure is None:
            try:
                assert_model_context_private(base, source_key, hierarchy)
            except Exception:
                local_failure = "PRIVACY_FAILURE"
        for item in scheduled:
            if local_failure is not None or base is None or hierarchy is None:
                terminal = terminalize_local_failure(
                    item,
                    status=local_failure or "INPUT_PROJECTION_FAILURE",
                    journal_root=journal_root,
                    output_root=output_root,
                    schedule_sha256=schedule_sha,
                    implementation_lock_sha256=implementation_sha,
                )
            else:
                if config is None:
                    raise AssertionError("Regression Provider config is missing")
                terminal = execute_live_arm(
                    item,
                    base=base,
                    hierarchy=None if item.arm is Arm.B0 else hierarchy,
                    journal_root=journal_root,
                    output_root=output_root,
                    schedule_sha256=schedule_sha,
                    implementation_lock_sha256=implementation_sha,
                    provider_config=config,
                    expected_model=str(model_lock["model"]),
                    timeout_seconds=_require_float(
                        model_lock["timeout_seconds"], "Provider timeout"
                    ),
                    max_completion_tokens=max_completion,
                    prompt_token_reservation=prompt_reservation,
                    pacer=pacer,
                    budget=budget,
                    retry_policy_sha256=str(
                        model_lock["transport_retry_policy_sha256"]
                    ),
                )
            pair_terminals.append(terminal)
            print(
                f"[regression] {pair_index // 2 + 1}/120 {item.arm.value} "
                f"{terminal.status.value}",
                flush=True,
            )
        if any(item.failure_code == "HTTP_429" for item in pair_terminals):
            stop_after_pair = scheduled[0].pair_position
    terminals, _final_evidence = _validate_partial_phase_records(
        private_root,
        split="REGRESSION",
        allow_orphan_attempt=False,
        require_http_429_boundary=stop_after_pair is not None,
    )
    summary = _execution_summary_payload(
        "regression", terminals, created_at_utc=_utc_now()
    )
    summary_sha = _write_create_once(
        private_root / "runtime" / "regression" / "execution-summary.json",
        summary,
    )
    if len(terminals) != 240:
        raise ValueError("Regression execution is incomplete but resumable without reissue")
    _advance_state(
        private_root,
        "REGRESSION_EXECUTED",
        predecessor="CANDIDATE_FROZEN",
        lock_name="execution-summary.json",
        lock_sha256=summary_sha,
    )
    print(json.dumps(summary, sort_keys=True))


def lock_regression_terminals(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "REGRESSION_EXECUTED")
    implementation = _verify_implementation(private_root)
    records, terminals = _validated_phase_terminals(
        private_root, split="REGRESSION"
    )
    _verify_provider_run_sidecars(
        private_root, phase="regression", terminals=terminals
    )
    output_root = private_root / "runtime" / "regression" / "output"
    journal_root = private_root / "runtime" / "regression" / "journal"
    if len({item.opaque_case_id for item in terminals}) != 120:
        raise ValueError("Regression terminal pair accounting differs")
    run_attempts = tuple(sorted((journal_root / "run-attempts").glob("*.json")))
    admitted = sum(
        item.status is not LiveTerminalStatus.NOT_ADMITTED for item in terminals
    )
    if len(run_attempts) != admitted:
        raise ValueError("Regression run-attempt accounting differs")
    run_roots = tuple(
        sorted(path for path in (journal_root / "runs").glob("*") if path.is_dir())
    )
    budget_lock = _load_object(CONFIG_ROOT / "budget.json")
    accounting = rebuild_attempt_accounting(
        run_roots,
        prompt_token_reservation=_require_int(
            budget_lock["prompt_token_reservation_per_attempt"],
            "prompt reservation",
        ),
        max_completion_tokens=_require_int(
            budget_lock["max_completion_tokens_per_attempt"],
            "completion budget",
        ),
    )
    if (
        accounting.provider_attempt_count
        != sum(item.provider_attempts for item in terminals)
        or accounting.retry_attempt_count
        != sum(item.transport_retries for item in terminals)
    ):
        raise ValueError("Regression Provider attempt accounting differs")
    terminal_tree, terminal_files = tree_sha256(output_root / "terminals")
    run_attempt_tree, run_attempt_files = tree_sha256(journal_root / "run-attempts")
    provider_tree, provider_files = tree_sha256(journal_root / "runs")
    lock = {
        "candidate_lock_sha256": _sha_file(
            private_root / "locks" / "candidate-lock.json"
        ),
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": implementation["implementation_commit"],
        "provider_attempt_files": provider_files,
        "provider_attempt_tree_sha256": provider_tree,
        "provider_attempts": accounting.provider_attempt_count,
        "run_attempt_files": run_attempt_files,
        "run_attempt_tree_sha256": run_attempt_tree,
        "schedule_sha256": _sha_file(
            private_root / "schedules" / "regression.json"
        ),
        "schema_version": (
            "strong-single-hierarchical-live.regression-terminal-lock.v1"
        ),
        "terminal_files": terminal_files,
        "terminal_tree_sha256": terminal_tree,
        "transport_retries": accounting.retry_attempt_count,
    }
    lock_sha = _write_create_once(
        private_root / "locks" / "regression-terminal-lock.json", lock
    )
    _advance_state(
        private_root,
        "REGRESSION_TERMINALS_LOCKED",
        predecessor="REGRESSION_EXECUTED",
        lock_name="regression-terminal-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps(lock, sort_keys=True))


def score_regression(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    _require_state(private_root, "REGRESSION_TERMINALS_LOCKED")
    _verify_implementation(private_root)
    _verify_input_trees_again(args)
    _verify_terminal_lock_again(private_root, split="REGRESSION")
    _verify_candidate_lock(private_root)
    if any(name in os.environ for name in ("ECOMSRE_LLM_BASE_URL", "ECOMSRE_LLM_API_KEY", "ECOMSRE_LLM_MODEL")):
        raise ValueError("Provider credentials must be absent during evaluator-only scoring")
    from scripts.rca_live.evaluator import case_scores_payload, evaluate_regression

    aggregate, scores = evaluate_regression(
        schedule_path=private_root / "schedules" / "regression.json",
        terminals_root=private_root / "runtime" / "regression" / "output",
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
        implementation_lock_sha256=_sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
    )
    aggregate_sha = _write_create_once(
        private_root / "evaluation" / "regression-aggregate.json", aggregate
    )
    scores_sha = _write_create_once(
        private_root / "evaluation" / "regression-case-scores.json",
        case_scores_payload(scores),
    )
    lock = {
        "aggregate_sha256": aggregate_sha,
        "case_scores_sha256": scores_sha,
        "candidate_lock_sha256": _sha_file(
            private_root / "locks" / "candidate-lock.json"
        ),
        "created_at_utc": _utc_now(),
        "evaluation_version": EVALUATION_VERSION,
        "implementation_lock_sha256": _sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
        "regression_terminal_lock_sha256": _sha_file(
            private_root / "locks" / "regression-terminal-lock.json"
        ),
        "schema_version": "strong-single-hierarchical-live.regression-scoring-lock.v1",
        "verdict": cast(Mapping[str, object], aggregate["gate"])["verdict"],
    }
    lock_sha = _write_create_once(
        private_root / "locks" / "regression-scoring-lock.json", lock
    )
    _advance_state(
        private_root,
        "REGRESSION_SCORED",
        predecessor="REGRESSION_TERMINALS_LOCKED",
        lock_name="regression-scoring-lock.json",
        lock_sha256=lock_sha,
    )
    print(json.dumps(aggregate, sort_keys=True))


def _verify_report_protected_surface(private_root: Path) -> dict[str, object]:
    _require_active_control(private_root)
    implementation = _load_object(private_root / "locks" / "implementation-lock.json")
    protected = implementation.get("protected_files")
    if not isinstance(protected, Mapping):
        raise ValueError("report verifier protected surface is invalid")
    for raw_path, expected in protected.items():
        if (
            not isinstance(raw_path, str)
            or not isinstance(expected, str)
            or _sha_file(PROJECT_ROOT / raw_path) != expected
        ):
            raise ValueError("BLOCKED_PROTOCOL_DRIFT: report protected file differs")
    if implementation.get("config_hashes") != _config_hashes():
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: report configuration differs")
    commit = implementation.get("implementation_commit")
    if not isinstance(commit, str):
        raise ValueError("report implementation commit binding is invalid")
    expected_lineage = _historical_lineage_binding(
        commit, allowed_paths=_implementation_changed_path_allowlist()
    )
    if any(implementation.get(key) != value for key, value in expected_lineage.items()):
        raise ValueError("BLOCKED_PR24_MERGE_LINEAGE_MISSING")
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", commit, "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
    )
    allowed_public = {
        "docs/results/strong-single-hierarchical-live-v1-tune.json",
        "docs/results/strong-single-hierarchical-live-v1-tune.md",
        "docs/results/strong-single-hierarchical-live-v1-human-brief.md",
    }
    if (private_root / "evaluation" / "regression-aggregate.json").exists():
        allowed_public.update(
            {
                "docs/results/strong-single-hierarchical-live-v1-regression.json",
                "docs/results/strong-single-hierarchical-live-v1-regression.md",
            }
        )
    committed_changes = set(
        filter(None, _git("diff", "--name-only", f"{commit}..HEAD").splitlines())
    )
    dirty_changes = {
        line[3:]
        for line in _git("status", "--porcelain").splitlines()
        if len(line) >= 4
    }
    if not committed_changes.issubset(allowed_public) or not dirty_changes.issubset(
        allowed_public
    ):
        raise ValueError("BLOCKED_PROTOCOL_DRIFT: non-result path changed after freeze")
    return implementation


def _verify_state_chain(private_root: Path, final_state: str) -> None:
    current: str | None = final_state
    seen: set[str] = set()
    while current is not None:
        if current in seen or current not in STATE_PREDECESSORS:
            raise ValueError("private evaluation state chain is invalid")
        seen.add(current)
        state_path = private_root / "state" / f"{current}.json"
        state = _load_object(state_path)
        created_at = _require_utc_timestamp(
            state.get("created_at_utc"), f"{current} state"
        )
        expected_predecessor = STATE_PREDECESSORS[current]
        lock_relative = STATE_LOCK_PATHS[current]
        lock_path = private_root / lock_relative
        expected_predecessor_sha = (
            None
            if expected_predecessor is None
            else _sha_file(
                private_root / "state" / f"{expected_predecessor}.json"
            )
        )
        expected_state = {
            "created_at_utc": created_at,
            "evaluation_version": EVALUATION_VERSION,
            "lock_name": lock_path.name,
            "lock_sha256": _sha_file(lock_path),
            "predecessor": expected_predecessor,
            "predecessor_sha256": expected_predecessor_sha,
            "schema_version": "strong-single-hierarchical-live.state.v1",
            "state": current,
        }
        if state != expected_state:
            raise ValueError(f"private evaluation state binding differs: {current}")
        current = expected_predecessor


def _verify_input_trees_again(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    lock = _load_object(private_root / "locks" / "input-reverification-lock.json")
    locked_trees = lock.get("input_trees")
    if not isinstance(locked_trees, Mapping):
        raise ValueError("input reverification lock is invalid")
    roots = {
        "rca100": Path(args.rca_cases_root),
        "obss_ob": Path(args.ob_root),
        "obss_ss": Path(args.ss_root),
    }
    for name, root in roots.items():
        digest, file_count, byte_count = _absolute_tree_digest(root)
        expected = locked_trees.get(name)
        observed = {
            "absolute_root": str(root.resolve(strict=True)),
            "byte_count": byte_count,
            "file_count": file_count,
            "sha256": digest,
        }
        if expected != observed:
            raise ValueError(f"BLOCKED_PROTOCOL_DRIFT: {name} scoring input differs")


def _verify_terminal_lock_again(
    private_root: Path, *, split: Literal["TUNE", "REGRESSION"]
) -> tuple[LiveTerminalRecord, ...]:
    _records, terminals = _validated_phase_terminals(private_root, split=split)
    phase = split.casefold()
    _verify_provider_run_sidecars(
        private_root,
        phase=cast(Literal["tune", "regression"], phase),
        terminals=terminals,
    )
    output_root = private_root / "runtime" / phase / "output"
    journal_root = private_root / "runtime" / phase / "journal"
    lock = _load_object(private_root / "locks" / f"{phase}-terminal-lock.json")
    terminal_tree, terminal_files = tree_sha256(output_root / "terminals")
    attempt_tree, attempt_files = tree_sha256(journal_root / "run-attempts")
    provider_tree, provider_files = tree_sha256(journal_root / "runs")
    implementation = _load_object(
        private_root / "locks" / "implementation-lock.json"
    )
    checks = {
        "implementation_commit": implementation.get("implementation_commit"),
        "provider_attempt_files": provider_files,
        "provider_attempt_tree_sha256": provider_tree,
        "provider_attempts": sum(item.provider_attempts for item in terminals),
        "run_attempt_files": attempt_files,
        "run_attempt_tree_sha256": attempt_tree,
        "schedule_sha256": _sha_file(
            private_root / "schedules" / f"{phase}.json"
        ),
        "terminal_files": terminal_files,
        "terminal_tree_sha256": terminal_tree,
        "transport_retries": sum(item.transport_retries for item in terminals),
    }
    if any(lock.get(key) != value for key, value in checks.items()):
        raise ValueError(f"{split} terminal lock no longer binds raw execution")
    if split == "REGRESSION" and lock.get("candidate_lock_sha256") != _sha_file(
        private_root / "locks" / "candidate-lock.json"
    ):
        raise ValueError("Regression terminal lock candidate binding differs")
    return terminals


def _verify_ground_truth_again(args: argparse.Namespace) -> None:
    if any(
        name in os.environ
        for name in (
            "ECOMSRE_LLM_BASE_URL",
            "ECOMSRE_LLM_API_KEY",
            "ECOMSRE_LLM_MODEL",
        )
    ):
        raise ValueError("Provider credentials must be absent during verification")
    private_root = Path(args.private_root)
    ground_truth = _load_object(private_root / "locks" / "ground-truth-lock.json")
    answer_root = Path(args.answer_root)
    answer_tree, answer_files = tree_sha256(answer_root)
    answer_source_lock = Path(args.answer_source_lock)
    if (
        _sha_file(answer_source_lock) != EXPECTED_ANSWER_SOURCE_LOCK_SHA256
        or ground_truth.get("answer_source_lock_sha256")
        != EXPECTED_ANSWER_SOURCE_LOCK_SHA256
        or ground_truth.get("answer_tree_sha256") != answer_tree
        or ground_truth.get("answer_tree_files") != answer_files
        or ground_truth.get("provider_credentials_present") is not False
        or ground_truth.get("tune_terminal_lock_sha256")
        != _sha_file(private_root / "locks" / "tune-terminal-lock.json")
    ):
        raise ValueError("ground-truth lock/source binding differs")


def _verify_private_evaluation(args: argparse.Namespace) -> str:
    private_root = Path(args.private_root)
    _require_active_control(private_root)
    _verify_context_audit_binding(private_root)
    _verify_input_trees_again(args)
    _verify_preflight_lock_again(private_root)
    _verify_ground_truth_again(args)
    _verify_state_chain(private_root, "TUNE_SCORED")
    _verify_terminal_lock_again(private_root, split="TUNE")
    from scripts.rca_live.evaluator import case_scores_payload, evaluate_regression, evaluate_tune
    from scripts.rca_live.reporting import verify_scoring_artifact_hashes

    tune_scoring = verify_scoring_artifact_hashes(private_root, "tune")
    if (
        tune_scoring.get("ground_truth_lock_sha256")
        != _sha_file(private_root / "locks" / "ground-truth-lock.json")
        or tune_scoring.get("implementation_lock_sha256")
        != _sha_file(private_root / "locks" / "implementation-lock.json")
        or tune_scoring.get("tune_terminal_lock_sha256")
        != _sha_file(private_root / "locks" / "tune-terminal-lock.json")
    ):
        raise ValueError("TUNE scoring lock lineage differs")
    tune_aggregate, tune_scores = evaluate_tune(
        schedule_path=private_root / "schedules" / "tune.json",
        terminals_root=private_root / "runtime" / "tune" / "output",
        rca_cases_root=Path(args.rca_cases_root),
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
        answer_root=Path(args.answer_root),
        implementation_lock_sha256=_sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
    )
    if (
        (private_root / "evaluation" / "tune-aggregate.json").read_bytes()
        != _canonical_bytes(tune_aggregate)
        or (private_root / "evaluation" / "tune-case-scores.json").read_bytes()
        != _canonical_bytes(case_scores_payload(tune_scores))
    ):
        raise ValueError("canonical TUNE recomputation differs from locked scoring")
    tune_gate_result = tune_aggregate.get("gate")
    if not isinstance(tune_gate_result, Mapping):
        raise ValueError("canonical TUNE Gate is invalid")
    if tune_gate_result.get("passed") is not True:
        if any(
            (private_root / "evaluation" / name).exists()
            for name in (
                "regression-aggregate.json",
                "regression-case-scores.json",
            )
        ):
            raise ValueError("Regression artifacts exist after a failed TUNE Gate")
        return "TUNE_SCORED"

    _verify_state_chain(private_root, "REGRESSION_SCORED")
    _verify_terminal_lock_again(private_root, split="REGRESSION")
    regression_scoring = verify_scoring_artifact_hashes(
        private_root, "regression"
    )
    if (
        regression_scoring.get("candidate_lock_sha256")
        != _sha_file(private_root / "locks" / "candidate-lock.json")
        or regression_scoring.get("implementation_lock_sha256")
        != _sha_file(private_root / "locks" / "implementation-lock.json")
        or regression_scoring.get("regression_terminal_lock_sha256")
        != _sha_file(private_root / "locks" / "regression-terminal-lock.json")
    ):
        raise ValueError("Regression scoring lock lineage differs")
    regression_aggregate, regression_scores = evaluate_regression(
        schedule_path=private_root / "schedules" / "regression.json",
        terminals_root=private_root / "runtime" / "regression" / "output",
        ob_root=Path(args.ob_root),
        ss_root=Path(args.ss_root),
        implementation_lock_sha256=_sha_file(
            private_root / "locks" / "implementation-lock.json"
        ),
    )
    if (
        (private_root / "evaluation" / "regression-aggregate.json").read_bytes()
        != _canonical_bytes(regression_aggregate)
        or (
            private_root / "evaluation" / "regression-case-scores.json"
        ).read_bytes()
        != _canonical_bytes(case_scores_payload(regression_scores))
    ):
        raise ValueError(
            "canonical Regression recomputation differs from locked scoring"
        )
    return "REGRESSION_SCORED"


def publish_results(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    predecessor = _verify_private_evaluation(args)
    implementation = _verify_report_protected_surface(private_root)
    from scripts.rca_live.reporting import publish

    hashes = publish(PROJECT_ROOT, private_root)
    lock_path = private_root / "locks" / "public-projection-lock.json"
    expected_lock = {
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": implementation["implementation_commit"],
        "predecessor": predecessor,
        "predecessor_state_sha256": _sha_file(
            private_root / "state" / f"{predecessor}.json"
        ),
        "public_files": hashes,
        "tune_scoring_lock_sha256": _sha_file(
            private_root / "locks" / "tune-scoring-lock.json"
        ),
        "regression_scoring_lock_sha256": (
            None
            if predecessor == "TUNE_SCORED"
            else _sha_file(
                private_root / "locks" / "regression-scoring-lock.json"
            )
        ),
        "schema_version": (
            "strong-single-hierarchical-live.public-projection-lock.v1"
        ),
    }
    if lock_path.exists():
        _require_exact_object(
            lock_path, expected_lock, "existing public projection lock"
        )
        lock_sha = _sha_file(lock_path)
    else:
        lock_sha = _write_create_once(lock_path, expected_lock)
    print(
        json.dumps(
            {"public_files": hashes, "public_projection_lock_sha256": lock_sha},
            sort_keys=True,
        )
    )


def verify_results(args: argparse.Namespace) -> None:
    private_root = Path(args.private_root)
    predecessor = _verify_private_evaluation(args)
    implementation = _verify_report_protected_surface(private_root)
    from scripts.rca_live.reporting import verify

    hashes = verify(PROJECT_ROOT, private_root)
    projection_lock_path = private_root / "locks" / "public-projection-lock.json"
    expected_projection_lock = {
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": implementation["implementation_commit"],
        "predecessor": predecessor,
        "predecessor_state_sha256": _sha_file(
            private_root / "state" / f"{predecessor}.json"
        ),
        "public_files": hashes,
        "tune_scoring_lock_sha256": _sha_file(
            private_root / "locks" / "tune-scoring-lock.json"
        ),
        "regression_scoring_lock_sha256": (
            None
            if predecessor == "TUNE_SCORED"
            else _sha_file(
                private_root / "locks" / "regression-scoring-lock.json"
            )
        ),
        "schema_version": (
            "strong-single-hierarchical-live.public-projection-lock.v1"
        ),
    }
    _require_exact_object(
        projection_lock_path,
        expected_projection_lock,
        "public projection lock",
    )
    verification_lock_path = private_root / "locks" / "public-verification-lock.json"
    expected_verification_lock = {
        "canonical_exact_comparison": "PASS",
        "evaluation_version": EVALUATION_VERSION,
        "implementation_commit": implementation["implementation_commit"],
        "predecessor": predecessor,
        "predecessor_state_sha256": _sha_file(
            private_root / "state" / f"{predecessor}.json"
        ),
        "public_files": hashes,
        "public_leakage_scan": "PASS",
        "public_projection_lock_sha256": _sha_file(projection_lock_path),
        "schema_version": (
            "strong-single-hierarchical-live.public-verification-lock.v1"
        ),
    }
    if verification_lock_path.exists():
        _require_exact_object(
            verification_lock_path,
            expected_verification_lock,
            "existing public verification lock",
        )
        lock_sha = _sha_file(verification_lock_path)
    else:
        lock_sha = _write_create_once(
            verification_lock_path, expected_verification_lock
        )
    state_path = private_root / "state" / "PUBLIC_RESULT_FROZEN.json"
    if not state_path.exists():
        _advance_state(
            private_root,
            "PUBLIC_RESULT_FROZEN",
            predecessor=predecessor,
            lock_name="public-verification-lock.json",
            lock_sha256=lock_sha,
        )
    _validate_public_frozen_state(
        private_root,
        predecessor=predecessor,
        verification_lock_sha256=lock_sha,
    )
    print(
        json.dumps(
            {
                "canonical_verification": "PASS",
                "public_files": hashes,
                "public_leakage_scan": "PASS",
                "state": "PUBLIC_RESULT_FROZEN",
            },
            sort_keys=True,
        )
    )


def _add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--rca-cases-root", type=Path, required=True)
    parser.add_argument("--ob-root", type=Path, required=True)
    parser.add_argument("--ss-root", type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    schedule = subparsers.add_parser("build-schedules")
    _add_common_source_args(schedule)
    schedule.add_argument(
        "--tune-consumed-terminals-root", type=Path, required=True
    )
    schedule.add_argument(
        "--regression-consumed-terminals-root", type=Path, required=True
    )
    schedule.add_argument("--obss-dataset-audit-path", type=Path, required=True)
    schedule.add_argument("--rca-input-source-lock-path", type=Path, required=True)
    schedule.set_defaults(action=build_schedules)
    superseded = subparsers.add_parser("mark-superseded")
    superseded.add_argument("--private-root", type=Path, required=True)
    superseded.add_argument(
        "--reason",
        choices=(
            "PRE_PROVIDER_LABEL_BOUNDARY_REPAIR",
            "PRE_IMPLEMENTATION_CONTEXT_AUDIT_SCHEMA_REPAIR",
            "PRE_IMPLEMENTATION_PROVIDER_PAYLOAD_IDENTITY_REPAIR",
            "PRE_IMPLEMENTATION_CONTROL_GENERATION_REPAIR",
        ),
        required=True,
    )
    superseded.set_defaults(action=mark_superseded)
    audit = subparsers.add_parser("audit-contexts")
    _add_common_source_args(audit)
    audit.add_argument("--methodology", type=Path, required=True)
    audit.set_defaults(action=audit_contexts)
    freeze = subparsers.add_parser("freeze-implementation")
    freeze.add_argument("--private-root", type=Path, required=True)
    freeze.set_defaults(action=freeze_implementation)
    admission = subparsers.add_parser("record-ci-admission")
    admission.add_argument("--private-root", type=Path, required=True)
    admission.add_argument("--pr-number", type=int, required=True)
    admission.add_argument("--head", required=True)
    admission.add_argument("--ci-state", choices=("SUCCESS", "FAILURE"), required=True)
    admission.set_defaults(action=record_ci_admission)
    inputs = subparsers.add_parser("verify-inputs")
    _add_common_source_args(inputs)
    inputs.set_defaults(action=verify_inputs)
    preflight = subparsers.add_parser("provider-preflight")
    _add_common_source_args(preflight)
    preflight.add_argument("--env-file", type=Path, required=True)
    preflight.set_defaults(action=provider_preflight)
    tune = subparsers.add_parser("run-tune")
    _add_common_source_args(tune)
    tune.add_argument("--methodology", type=Path, required=True)
    tune.add_argument("--env-file", type=Path, required=True)
    tune.set_defaults(action=run_tune)
    tune_lock = subparsers.add_parser("lock-tune-terminals")
    tune_lock.add_argument("--private-root", type=Path, required=True)
    tune_lock.set_defaults(action=lock_tune_terminals)
    tune_score = subparsers.add_parser("score-tune")
    _add_common_source_args(tune_score)
    tune_score.add_argument("--answer-root", type=Path, required=True)
    tune_score.add_argument("--answer-source-lock", type=Path, required=True)
    tune_score.add_argument("--answer-source-lock-sha256", required=True)
    tune_score.set_defaults(action=score_tune)
    candidate = subparsers.add_parser("freeze-candidate")
    candidate.add_argument("--private-root", type=Path, required=True)
    candidate.set_defaults(action=freeze_candidate)
    regression = subparsers.add_parser("run-regression")
    _add_common_source_args(regression)
    regression.add_argument("--env-file", type=Path, required=True)
    regression.set_defaults(action=run_regression)
    regression_lock = subparsers.add_parser("lock-regression-terminals")
    regression_lock.add_argument("--private-root", type=Path, required=True)
    regression_lock.set_defaults(action=lock_regression_terminals)
    regression_score = subparsers.add_parser("score-regression")
    _add_common_source_args(regression_score)
    regression_score.set_defaults(action=score_regression)
    publish_parser = subparsers.add_parser("publish-results")
    _add_common_source_args(publish_parser)
    publish_parser.add_argument("--answer-root", type=Path, required=True)
    publish_parser.add_argument("--answer-source-lock", type=Path, required=True)
    publish_parser.set_defaults(action=publish_results)
    verify_parser = subparsers.add_parser("verify-results")
    _add_common_source_args(verify_parser)
    verify_parser.add_argument("--answer-root", type=Path, required=True)
    verify_parser.add_argument("--answer-source-lock", type=Path, required=True)
    verify_parser.set_defaults(action=verify_results)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    action = cast(Any, args.action)
    action(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
