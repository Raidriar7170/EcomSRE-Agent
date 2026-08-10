"""Frozen, zero-Provider RCA100 x OB/SS attribution and replay lifecycle."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rca_unified.adapters import AdaptedCase, load_obss_cases, load_rca100_cases
from ecomsre_rca_unified.analysis import (
    UnifiedMetricCandidate,
    UnifiedRCACase,
    classify_fault_phrase_relation,
    classify_m3_failure,
    classify_strong_single_failure,
    evidence_sufficiency,
    rate,
)
from ecomsre_rca_unified.contracts import (
    ArchitectureOption,
    CanonicalEntityLayer,
    EntityHierarchyPath,
    EvidenceVisibilitySummary,
    FaultOntologyClass,
    FrontierOutcome,
    PropagationDisposition,
)
from ecomsre_rca_unified.frontier import (
    aggregate_outcomes,
    apply_option,
    causal_selection,
    grouped_robustness,
    load_frontier,
    select_architecture,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BASE_COMMIT = "05091a710e26cdeedf9e08cfcf510f1fe6c326cc"
EXPECTED_PR22_HEAD = "7a0c22fa82a967730e238ac666f565cd935014ee"
EXPECTED_PR23_HEAD = EXPECTED_BASE_COMMIT
EXPECTED_CANDIDATE_SHA256 = {
    "candidate-3": "d3b43650b0165045d48917743e02b7dcc3771e96b90960360f9c98dc3a4360e5",
    "candidate-4": "a1f9e4037e762ba0968d64cc0ae9b0cac9bc9b24717f5b31d99cd9bfd0351ca5",
    "candidate-5": "4345a7fe7a7b89881a31c1b3260b078df1a1cf4da3bf2b8531208919fb51b904",
}
PROVIDER_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_MODEL",
)
CLASSIFICATION = (
    "CONSUMED_CROSS_BENCHMARK_DEVELOPMENT",
    "POST_HOC_ARCHITECTURE_ATTRIBUTION",
    "NOT_EXTERNAL_VALIDATION",
    "NOT_PRIMARY_INFERENCE",
)
_FORBIDDEN_PUBLIC_KEYS = {
    "case_id",
    "source_task_id",
    "opaque_case_id",
    "run_id",
    "entity",
    "entity_name",
    "entity_ref",
    "root_entity",
    "root_cause_entity",
    "evidence_ref",
    "evidence_refs",
    "private_case_key",
    "private_path",
    "raw_provider_output",
    "credentials",
    "api_key",
}
_FORBIDDEN_PUBLIC_TEXT = (
    "/users/",
    "/home/",
    "/private/",
    ".ecomsre-private",
    "rca100-case-",
    "metric:000",
    "log:000",
    "trace:000",
    "bearer ",
)
_SOURCE_TASK = re.compile(r"\bt[0-9]{3}\b", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path.name}")
    return value


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("hash input must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path) -> tuple[str, int, int]:
    """Hash content plus absolute normalized paths in stable order."""

    resolved = root.expanduser().resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise ValueError("tree input must be a real directory")
    outer = hashlib.sha256()
    count = 0
    byte_count = 0
    for path in sorted(item for item in resolved.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("tree input may not contain symlink files")
        digest = sha256_file(path)
        outer.update(f"{digest}  {path}\n".encode("utf-8"))
        count += 1
        byte_count += path.stat().st_size
    return outer.hexdigest(), count, byte_count


def write_json_create_once(path: Path, value: object) -> None:
    payload = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"existing create-once JSON differs: {path.name}")
        if path.stat().st_mode & 0o777 != 0o600:
            raise ValueError(f"existing private JSON mode differs: {path.name}")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def write_jsonl_create_once(path: Path, values: Sequence[Mapping[str, object]]) -> None:
    payload = b"".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
        for value in values
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"existing create-once JSONL differs: {path.name}")
        if path.stat().st_mode & 0o777 != 0o600:
            raise ValueError(f"existing private JSONL mode differs: {path.name}")
        return
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def assert_no_provider_environment() -> None:
    present = tuple(name for name in PROVIDER_ENV_NAMES if os.environ.get(name))
    if present:
        raise ValueError("Provider environment must be removed for offline attribution")


def assert_public_payload(payload: object) -> None:
    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).casefold() in _FORBIDDEN_PUBLIC_KEYS:
                    raise ValueError(f"public payload contains forbidden key: {key}")
                walk(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                walk(nested)
        elif isinstance(value, str):
            lowered = value.casefold()
            if any(marker in lowered for marker in _FORBIDDEN_PUBLIC_TEXT):
                raise ValueError("public payload contains forbidden private material")
            if _SOURCE_TASK.search(value):
                raise ValueError("public payload contains forbidden source task identity")

    walk(payload)


def assert_public_text(text: str) -> None:
    lowered = text.casefold()
    if any(marker in lowered for marker in _FORBIDDEN_PUBLIC_TEXT):
        raise ValueError("public text contains forbidden private material")
    if _SOURCE_TASK.search(text):
        raise ValueError("public text contains forbidden source task identity")


def write_public_json(path: Path, value: object) -> None:
    assert_public_payload(value)
    payload = canonical_json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def write_public_text(path: Path, value: str) -> None:
    payload = value.rstrip() + "\n"
    assert_public_text(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def validate_private_root(path: Path, *, create: bool) -> Path:
    if not path.is_absolute():
        raise ValueError("private root must be absolute")
    if create and not path.exists():
        path.mkdir(parents=True, mode=0o700)
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise ValueError("private root must be a real directory")
    try:
        resolved.relative_to(PROJECT_ROOT.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ValueError("private root must remain outside Git")
    resolved.chmod(0o700)
    if resolved.stat().st_mode & 0o777 != 0o700:
        raise ValueError("private root mode must be 0700")
    return resolved


def _git_head() -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_count(root: Path, pattern: str, expected: int) -> None:
    actual = len(tuple(root.glob(pattern)))
    if actual != expected:
        raise ValueError(
            f"frozen artifact count differs under {root.name}: {actual} != {expected}"
        )


def _safe_path(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    lowered = str(resolved).casefold()
    if "re2-tt" in lowered or "tt-case" in lowered:
        raise ValueError("RE2-TT access is forbidden")
    return resolved


def _inventory_tree(path: Path) -> dict[str, int | str]:
    digest, count, byte_count = tree_digest(path)
    return {"sha256": digest, "file_count": count, "byte_count": byte_count}


def freeze_inputs(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    if _git_head() != EXPECTED_BASE_COMMIT:
        raise ValueError("input/frontier freeze must begin at the exact PR #23 head")
    private_root = validate_private_root(args.private_root, create=True)
    frontier = _safe_path(args.frontier)
    raw_frontier = read_object(frontier)
    if tuple(raw_frontier.get("options", {})) != (
        "A0",
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    ):
        raise ValueError("frontier must be frozen before aggregate evaluation")

    candidate_roots = {
        "candidate-3": _safe_path(args.candidate_3_root),
        "candidate-4": _safe_path(args.candidate_4_root),
        "candidate-5": _safe_path(args.candidate_5_root),
    }
    candidate_inventory: dict[str, dict[str, int | str]] = {}
    for name, root in candidate_roots.items():
        _require_count(root / "terminal-records", "*.json", 60)
        inventory = _inventory_tree(root)
        if inventory["sha256"] != EXPECTED_CANDIDATE_SHA256[name]:
            raise ValueError(f"{name} frozen tree differs")
        candidate_inventory[name] = inventory

    tune_root = _safe_path(args.tune_root)
    regression_root = _safe_path(args.regression_root)
    _require_count(tune_root / "terminal-records", "*.json", 60)
    _require_count(regression_root / "terminal-records", "*.json", 120)

    ob_root = _safe_path(args.ob_root)
    ss_root = _safe_path(args.ss_root)
    ob_cases = discover_dev_cases(ob_root, DevSystem.RE2_OB)
    ss_cases = discover_dev_cases(ss_root, DevSystem.RE2_SS)
    if len(ob_cases) != 90 or len(ss_cases) != 90:
        raise ValueError("OB/SS consumed roots must contain exactly 90+90 cases")

    rca_input_root = _safe_path(args.rca_input_root)
    _require_count(rca_input_root, "t*/task.json", 103)
    if len(tuple(path for path in rca_input_root.glob("t*/*") if path.is_file())) != 721:
        raise ValueError("RCA100 input tree must contain 721 case files")
    rca_terminal_root = _safe_path(args.rca_terminal_root)
    _require_count(rca_terminal_root, "*.json", 103)
    rca_answer_root = _safe_path(args.rca_answer_root)
    _require_count(rca_answer_root, "*.gt.json", 103)
    case_scores = read_object(_safe_path(args.rca_case_scores))
    if not isinstance(case_scores.get("records"), list) or len(case_scores["records"]) != 103:
        raise ValueError("RCA100 case-score vector must contain 103 records")

    lock_paths = {
        "rca_input_source_lock": _safe_path(args.rca_input_source_lock),
        "rca_terminal_lock": _safe_path(args.rca_terminal_lock),
        "rca_answer_lock": _safe_path(args.rca_answer_lock),
        "rca_scoring_lock": _safe_path(args.rca_scoring_lock),
        "rca_schedule": _safe_path(args.rca_schedule),
        "rca_case_scores": _safe_path(args.rca_case_scores),
    }
    lock = {
        "schema_version": "rca-crossbenchmark.input-frontier-lock.v1",
        "created_at_utc": utc_now(),
        "classification": list(CLASSIFICATION),
        "base_commit": EXPECTED_BASE_COMMIT,
        "preserved_pr_heads": {
            "22": EXPECTED_PR22_HEAD,
            "23": EXPECTED_PR23_HEAD,
        },
        "frontier": {
            "sha256": sha256_file(frontier),
            "option_ids": ["A0", "A1", "A2", "A3", "A4", "A5"],
        },
        "inputs": {
            "candidate_fixtures": candidate_inventory,
            "pr21_tune": _inventory_tree(tune_root),
            "pr21_regression": _inventory_tree(regression_root),
            "ob_raw": _inventory_tree(ob_root),
            "ss_raw": _inventory_tree(ss_root),
            "rca_input_tree": _inventory_tree(rca_input_root),
            "rca_terminal_tree": _inventory_tree(rca_terminal_root),
            "rca_answer_tree": _inventory_tree(rca_answer_root),
            "rca_locked_files": {
                name: sha256_file(path) for name, path in lock_paths.items()
            },
        },
        "counts": {
            "candidate_3": 60,
            "candidate_4": 60,
            "candidate_5": 60,
            "pr21_tune": 60,
            "pr21_regression": 120,
            "ob_cases": 90,
            "ss_cases": 90,
            "rca100": 103,
        },
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "semantic_operations": 0,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
    }
    write_json_create_once(private_root / "locks/input-and-frontier-lock.json", lock)
    state = {
        "schema_version": "rca-crossbenchmark.state.v1",
        "state": "INPUTS_AND_FRONTIER_FROZEN",
        "created_at_utc": lock["created_at_utc"],
        "lock_sha256": sha256_file(private_root / "locks/input-and-frontier-lock.json"),
    }
    write_json_create_once(
        private_root / "state/INPUTS_AND_FRONTIER_FROZEN.json", state
    )
    return 0


def _validate_methodology(value: Mapping[str, object]) -> None:
    if set(value) != {
        "causal_ranking",
        "compatible_layer_groups",
        "fault_ontology",
        "first_anomaly",
        "methodology_schema_version",
        "propagation",
        "root_eligible_layers",
    }:
        raise ValueError("methodology top-level schema differs")
    if value.get("methodology_schema_version") != "rca-crossbenchmark.methodology.v1":
        raise ValueError("methodology version differs")
    causal = value.get("causal_ranking")
    if not isinstance(causal, Mapping) or causal.get("candidate_pool") != "METRICS_TOP6_ONLY":
        raise ValueError("causal candidate pool differs")
    if causal.get("order") != [
        "FIRST_ANOMALY_ASC",
        "SOURCE_SUPPORT_DESC",
        "METRICS_RANK_ASC",
        "ENTITY_REF_ASC",
    ]:
        raise ValueError("causal ranking order differs")
    if type(causal.get("minimum_source_support")) is not int:
        raise ValueError("causal source threshold must be an integer")
    anomaly = value.get("first_anomaly")
    if not isinstance(anomaly, Mapping) or set(anomaly) != {
        "alerts",
        "events",
        "logs",
        "metrics",
        "traces",
    }:
        raise ValueError("first-anomaly contract differs")
    propagation = value.get("propagation")
    if not isinstance(propagation, Mapping) or propagation.get(
        "missing_source_disposition"
    ) != "UNAVAILABLE":
        raise ValueError("missing propagation evidence must remain unavailable")
    layer_groups = value.get("compatible_layer_groups")
    eligible_layers = value.get("root_eligible_layers")
    if not isinstance(layer_groups, list) or not isinstance(eligible_layers, list):
        raise ValueError("methodology entity layers must be lists")
    if not eligible_layers or any(not isinstance(item, str) for item in eligible_layers):
        raise ValueError("root-eligible layers are invalid")


def freeze_methodology(args: argparse.Namespace) -> int:
    """Create-once lock the attribution methodology before aggregate evaluation."""

    assert_no_provider_environment()
    if _git_head() != EXPECTED_BASE_COMMIT:
        raise ValueError("methodology freeze must begin at the exact PR #23 head")
    private_root = validate_private_root(args.private_root, create=False)
    input_lock_path = private_root / "locks/input-and-frontier-lock.json"
    input_state_path = private_root / "state/INPUTS_AND_FRONTIER_FROZEN.json"
    input_lock = read_object(input_lock_path)
    input_state = read_object(input_state_path)
    input_lock_sha256 = sha256_file(input_lock_path)
    if (
        input_state.get("state") != "INPUTS_AND_FRONTIER_FROZEN"
        or input_state.get("lock_sha256") != input_lock_sha256
    ):
        raise ValueError("input/frontier state binding differs")
    if any(
        input_lock.get(key) != 0
        for key in ("provider_calls", "semantic_operations")
    ):
        raise ValueError("input/frontier lock contains external operations")
    frontier = _safe_path(args.frontier)
    frontier_sha256 = sha256_file(frontier)
    frozen_frontier = input_lock.get("frontier")
    if not isinstance(frozen_frontier, Mapping) or frozen_frontier.get(
        "sha256"
    ) != frontier_sha256:
        raise ValueError("frontier content differs from the input lock")
    methodology = _safe_path(args.methodology)
    raw_methodology = read_object(methodology)
    _validate_methodology(raw_methodology)
    created_at = utc_now()
    lock = {
        "schema_version": "rca-crossbenchmark.attribution-methods-lock.v1",
        "created_at_utc": created_at,
        "classification": list(CLASSIFICATION),
        "base_commit": EXPECTED_BASE_COMMIT,
        "input_frontier_lock_sha256": input_lock_sha256,
        "frontier_sha256": frontier_sha256,
        "methodology_sha256": sha256_file(methodology),
        "causal_candidate_pool": "METRICS_TOP6_ONLY",
        "causal_minimum_source_support": raw_methodology["causal_ranking"][  # type: ignore[index]
            "minimum_source_support"
        ],
        "missing_source_disposition": "UNAVAILABLE",
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "semantic_operations": 0,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
    }
    lock_path = private_root / "locks/attribution-methods-lock.json"
    write_json_create_once(lock_path, lock)
    write_json_create_once(
        private_root / "state/ATTRIBUTION_METHODS_FROZEN.json",
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": "ATTRIBUTION_METHODS_FROZEN",
            "created_at_utc": created_at,
            "previous_state": "INPUTS_AND_FRONTIER_FROZEN",
            "previous_state_record_sha256": sha256_file(input_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    return 0


def _verify_state_binding(
    private_root: Path,
    *,
    state_name: str,
    lock_name: str,
) -> tuple[dict[str, Any], Path, Path]:
    lock_path = private_root / "locks" / lock_name
    state_path = private_root / "state" / f"{state_name}.json"
    lock = read_object(lock_path)
    state = read_object(state_path)
    if state.get("state") != state_name or state.get("lock_sha256") != sha256_file(
        lock_path
    ):
        raise ValueError(f"{state_name} state/lock binding differs")
    return lock, lock_path, state_path


def _verify_frozen_inputs(args: argparse.Namespace, lock: Mapping[str, object]) -> None:
    raw_inputs = lock.get("inputs")
    if not isinstance(raw_inputs, Mapping):
        raise ValueError("input lock inventory is missing")
    fixture_inventory = raw_inputs.get("candidate_fixtures")
    if not isinstance(fixture_inventory, Mapping):
        raise ValueError("candidate fixture inventory is missing")
    root_inputs = {
        "candidate-3": args.candidate_3_root,
        "candidate-4": args.candidate_4_root,
        "candidate-5": args.candidate_5_root,
    }
    for name, path in root_inputs.items():
        if _inventory_tree(_safe_path(path)) != fixture_inventory.get(name):
            raise ValueError(f"frozen {name} tree drifted after input lock")
    tree_inputs = {
        "pr21_tune": args.tune_root,
        "pr21_regression": args.regression_root,
        "ob_raw": args.ob_root,
        "ss_raw": args.ss_root,
        "rca_input_tree": args.rca_input_root,
        "rca_terminal_tree": args.rca_terminal_root,
        "rca_answer_tree": args.rca_answer_root,
    }
    for name, path in tree_inputs.items():
        if _inventory_tree(_safe_path(path)) != raw_inputs.get(name):
            raise ValueError(f"frozen {name} tree drifted after input lock")
    locked_files = raw_inputs.get("rca_locked_files")
    if not isinstance(locked_files, Mapping):
        raise ValueError("RCA locked-file inventory is missing")
    files = {
        "rca_input_source_lock": args.rca_input_source_lock,
        "rca_terminal_lock": args.rca_terminal_lock,
        "rca_answer_lock": args.rca_answer_lock,
        "rca_scoring_lock": args.rca_scoring_lock,
        "rca_schedule": args.rca_schedule,
        "rca_case_scores": args.rca_case_scores,
    }
    for name, path in files.items():
        if sha256_file(_safe_path(path)) != locked_files.get(name):
            raise ValueError(f"frozen {name} drifted after input lock")


def _progress(label: str, completed: int, total: int) -> None:
    if completed == 1 or completed == total or completed % 10 == 0:
        print(f"[{label}] {completed}/{total}", flush=True)


def _rates_by_label(values: Sequence[str]) -> dict[str, dict[str, int | float]]:
    counts = Counter(values)
    denominator = len(values)
    return {
        key: rate(value, denominator) for key, value in sorted(counts.items())
    }


def _rates_by_required_label(
    values: Sequence[str], required: Sequence[str]
) -> dict[str, dict[str, int | float]]:
    counts = Counter(values)
    denominator = len(values)
    labels = sorted(set(counts) | set(required))
    return {label: rate(counts[label], denominator) for label in labels}


def _boolean_rate(values: Sequence[bool]) -> dict[str, int | float]:
    return rate(sum(values), len(values))


def _accuracy_summary(cases: Sequence[AdaptedCase]) -> dict[str, object]:
    return {
        "exact_initial": _boolean_rate(
            [item.unified.initial_correct_exact for item in cases]
        ),
        "exact_final": _boolean_rate(
            [item.unified.m3_correct_exact for item in cases]
        ),
        "service_initial": _boolean_rate(
            [item.unified.initial_correct_service for item in cases]
        ),
        "service_final": _boolean_rate(
            [item.unified.m3_correct_service for item in cases]
        ),
    }


_OPTION_TOOL_COMPONENTS = {
    ArchitectureOption.A0: (
        "CANONICAL_ENTITY_HIERARCHY",
        "FAULT_ONTOLOGY_CLASSIFIER",
    ),
    ArchitectureOption.A1: (
        "CANONICAL_ENTITY_HIERARCHY",
        "FAULT_ONTOLOGY_CLASSIFIER",
        "HISTORICAL_METRICS_M3",
    ),
    ArchitectureOption.A2: (
        "CANONICAL_ENTITY_HIERARCHY",
        "FAULT_ONTOLOGY_CLASSIFIER",
        "HIERARCHY_GUARD",
        "METRICS_ARBITRATION",
    ),
    ArchitectureOption.A3: (
        "CANONICAL_ENTITY_HIERARCHY",
        "FAULT_ONTOLOGY_CLASSIFIER",
        "LOCAL_RESOURCE_GATE",
        "HIERARCHY_GUARD",
        "METRICS_ARBITRATION",
    ),
    ArchitectureOption.A4: (
        "CANONICAL_ENTITY_HIERARCHY",
        "FAULT_ONTOLOGY_CLASSIFIER",
        "LOCAL_RESOURCE_GATE",
        "HIERARCHY_GUARD",
        "METRICS_ARBITRATION",
        "DETERMINISTIC_CAUSAL_RANKING",
    ),
}


def _outcome_final_layer(item: AdaptedCase, outcome: FrontierOutcome) -> str:
    case = item.unified
    if outcome.final_entity == case.initial_entity:
        return case.initial_layer.value
    if outcome.final_entity == case.m3_final_entity:
        return case.m3_final_layer.value
    return next(
        (
            candidate.layer.value
            for candidate in case.metrics_candidates
            if candidate.entity == outcome.final_entity
        ),
        CanonicalEntityLayer.UNKNOWN.value,
    )


def _outcome_propagation_role(
    item: AdaptedCase, outcome: FrontierOutcome
) -> str:
    case = item.unified
    if outcome.final_entity == case.initial_entity:
        return str(item.propagation_record["initial_role"])
    if outcome.final_entity == case.m3_final_entity:
        return str(item.propagation_record["m3_role"])
    raw_roles = item.propagation_record.get("metrics_candidate_roles")
    if not isinstance(raw_roles, list):
        return "NO_GRAPH_PATH"
    return next(
        (
            str(record["role"])
            for record in raw_roles
            if isinstance(record, Mapping)
            and record.get("entity") == outcome.final_entity
        ),
        "NO_GRAPH_PATH",
    )


def _public_outcome_summary(
    outcomes: Sequence[FrontierOutcome],
    cases: Sequence[AdaptedCase],
    option: ArchitectureOption,
) -> dict[str, object]:
    if len(outcomes) != len(cases):
        raise ValueError("public option summary inputs differ in length")
    aggregate = aggregate_outcomes(outcomes)
    denominator = int(aggregate["denominator"])
    initial_correct = int(aggregate["initial_exact_correct"])
    initial_wrong = denominator - initial_correct
    final_wrong = denominator - int(aggregate["final_exact_correct"])
    layer_errors = sum(
        not outcome.final_exact_correct
        and _outcome_final_layer(item, outcome)
        != item.unified.ground_truth_layer.value
        for item, outcome in zip(cases, outcomes, strict=True)
    )
    downstream = sum(
        _outcome_propagation_role(item, outcome) == "DOWNSTREAM_SYMPTOM"
        for item, outcome in zip(cases, outcomes, strict=True)
    )
    components = _OPTION_TOOL_COMPONENTS[option]
    return {
        "denominator": denominator,
        "exact_initial": rate(initial_correct, denominator),
        "exact_final": rate(int(aggregate["final_exact_correct"]), denominator),
        "service_initial": rate(
            int(aggregate["initial_service_correct"]), denominator
        ),
        "service_final": rate(int(aggregate["final_service_correct"]), denominator),
        "root_rescue": rate(int(aggregate["root_rescue"]), initial_wrong),
        "root_damage": rate(int(aggregate["root_damage"]), initial_correct),
        "root_net_rescue": int(aggregate["root_net_rescue"]),
        "pair_rescue": rate(int(aggregate["pair_rescue"]), denominator),
        "pair_damage": rate(int(aggregate["pair_damage"]), denominator),
        "pair_net_rescue": int(aggregate["pair_net_rescue"]),
        "override": rate(int(aggregate["override_count"]), denominator),
        "correct_override": rate(int(aggregate["correct_override"]), denominator),
        "wrong_override": rate(int(aggregate["wrong_override"]), denominator),
        "entity_layer_error": rate(layer_errors, denominator),
        "entity_layer_error_among_final_wrong": rate(layer_errors, final_wrong),
        "downstream_symptom_selection": rate(downstream, denominator),
        "expected_tool_use": {
            "case_denominator": denominator,
            "external_tool_calls": {
                "total": 0,
                "case_denominator": denominator,
                "mean_per_case": 0.0,
            },
            "deterministic_components": list(components),
            "deterministic_component_evaluations": {
                "total": denominator * len(components),
                "case_denominator": denominator,
                "mean_per_case": float(len(components)),
            },
        },
    }


def _markdown_json_report(title: str, payload: Mapping[str, object]) -> str:
    classification = ", ".join(CLASSIFICATION)
    return (
        f"# {title}\n\n"
        f"Classification: `{classification}`.\n\n"
        "This is aggregate-only, consumed-development, post-hoc evidence. It does "
        "not revise the frozen benchmark results and is not external validation.\n\n"
        "## Canonical aggregate\n\n"
        "```json\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n```\n"
    )


def _relation_subset(
    cases: Sequence[AdaptedCase], predicate: Callable[[AdaptedCase], bool], key: str
) -> dict[str, dict[str, int | float]]:
    selected = [str(item.hierarchy_record[key]) for item in cases if predicate(item)]
    return _rates_by_label(selected)


def _visibility_aggregate(cases: Sequence[AdaptedCase]) -> dict[str, object]:
    def visible(item: AdaptedCase, section: str, source: str) -> bool:
        raw = item.visibility_record.get(section)
        if not isinstance(raw, Mapping):
            raise ValueError("private visibility record section is invalid")
        if source in raw:
            return bool(raw[source])
        if source == "topology":
            return bool(raw.get("catalog", False))
        return False

    sources = (
        "catalog",
        "metrics",
        "logs",
        "traces",
        "events",
        "alerts",
        "topology",
        "any_model_visible",
        "causal",
    )
    exact: dict[str, object] = {}
    service: dict[str, object] = {}
    for source in sources:
        exact[source] = _boolean_rate(
            [visible(item, "ground_truth_visible", source) for item in cases]
        )
        service[source] = _boolean_rate(
            [visible(item, "ground_truth_service_visible", source) for item in cases]
        )
    top_hits = {
        f"top_{k}": _boolean_rate(
            [
                any(
                    candidate.entity
                    in item.unified.ground_truth_equivalent_entities
                    for candidate in item.unified.metrics_candidates[:k]
                )
                for item in cases
            ]
        )
        for k in (1, 2, 6)
    }
    goal_sources = ("metrics", "logs", "traces", "events", "alerts", "topology")
    goal_dimensions = (
        ("ground_truth_exact_visible", "ground_truth_visible"),
        ("ground_truth_service_visible", "ground_truth_service_visible"),
        ("initial_visible", "initial_visible"),
        ("metrics_top1_visible", "metrics_top1_visible"),
        (
            "initial_and_ground_truth_co_visible",
            "initial_and_ground_truth_co_visible",
        ),
        (
            "metrics_top1_and_ground_truth_co_visible",
            "metrics_top1_and_ground_truth_co_visible",
        ),
    )
    per_source = {
        source: {
            public_name: _boolean_rate(
                [visible(item, section, source) for item in cases]
            )
            for public_name, section in goal_dimensions
        }
        for source in goal_sources
    }
    return {
        "exact": exact,
        "service": service,
        "metrics": top_hits,
        "per_source": per_source,
    }


def _entity_report(rca: Sequence[AdaptedCase]) -> dict[str, object]:
    initial_wrong = [item for item in rca if not item.unified.initial_correct_exact]
    overrides = [item for item in rca if item.unified.m3_action == "OVERRIDE_METRICS_TOP1"]
    damage = [
        item
        for item in overrides
        if item.unified.initial_correct_exact and not item.unified.m3_correct_exact
    ]
    wrong_to_wrong = [
        item
        for item in overrides
        if not item.unified.initial_correct_exact and not item.unified.m3_correct_exact
    ]
    top1_exact = [
        bool(
            item.unified.metrics_top1
            and item.unified.metrics_top1.entity
            in item.unified.ground_truth_equivalent_entities
        )
        for item in rca
    ]
    top1_service = [
        bool(
            item.unified.metrics_top1
            and item.unified.ground_truth_service is not None
            and item.unified.metrics_top1.service_ancestor
            == item.unified.ground_truth_service
        )
        for item in rca
    ]
    level_metrics: dict[str, object] = {
        "exact": {
            "initial": _boolean_rate(
                [item.unified.initial_correct_exact for item in rca]
            ),
            "historical_m3": _boolean_rate(
                [item.unified.m3_correct_exact for item in rca]
            ),
            "metrics_top1": _boolean_rate(top1_exact),
        },
        "service": {
            "initial": _boolean_rate(
                [item.unified.initial_correct_service for item in rca]
            ),
            "historical_m3": _boolean_rate(
                [item.unified.m3_correct_service for item in rca]
            ),
            "metrics_top1": _boolean_rate(top1_service),
        },
    }
    for public_name, record_name in (
        ("same_workload", "same_workload"),
        ("same_node", "same_node"),
        ("same_topology_component", "same_component"),
    ):
        level_metrics[public_name] = {
            stage: _boolean_rate(
                [
                    bool(item.hierarchy_record[f"{prefix}_{record_name}"])
                    for item in rca
                ]
            )
            for stage, prefix in (
                ("initial", "initial"),
                ("historical_m3", "m3"),
                ("metrics_top1", "metrics_top1"),
            )
        }
    mismatch_relations = {
        "PREDICTED_ANCESTOR",
        "PREDICTED_DESCENDANT",
        "SIBLING_SAME_PARENT",
        "SAME_SERVICE_DIFFERENT_INSTANCE",
        "SAME_NODE",
    }
    mismatch = sum(item.unified.initial_relation in mismatch_relations for item in initial_wrong)
    depths = [len(item.hierarchy_record["ground_truth_path"]) for item in rca]  # type: ignore[arg-type]
    return {
        "schema_version": "rca100-entity-hierarchy-attribution.v1",
        "classification": list(CLASSIFICATION),
        "denominator": len(rca),
        "multi_level_accuracy": level_metrics,
        "error_relations": {
            "initial_wrong": _relation_subset(
                rca, lambda item: not item.unified.initial_correct_exact, "initial_relation"
            ),
            "historical_m3_overrides": _relation_subset(
                rca,
                lambda item: item.unified.m3_action == "OVERRIDE_METRICS_TOP1",
                "m3_relation",
            ),
            "historical_m3_damage": _rates_by_label(
                [item.unified.m3_relation for item in damage]
            ),
            "historical_m3_wrong_to_wrong": _rates_by_label(
                [item.unified.m3_relation for item in wrong_to_wrong]
            ),
        },
        "focus_denominators": {
            "initial_wrong": len(initial_wrong),
            "historical_m3_overrides": len(overrides),
            "historical_m3_damage": len(damage),
            "historical_m3_wrong_to_wrong": len(wrong_to_wrong),
        },
        "granularity_mismatch_contribution": rate(mismatch, len(initial_wrong)),
        "target_layer_distribution": _rates_by_label(
            [item.unified.ground_truth_layer.value for item in rca]
        ),
        "metrics_top1_layer_distribution": _rates_by_label(
            [
                "UNKNOWN"
                if item.unified.metrics_top1 is None
                else item.unified.metrics_top1.layer.value
                for item in rca
            ]
        ),
        "topology_depth": {
            "denominator": len(depths),
            "minimum": min(depths),
            "maximum": max(depths),
            "mean": sum(depths) / len(depths),
        },
    }


_STRONG_SINGLE_FAILURE_CLASSES = (
    "ROOT_NOT_IN_ENTITY_CATALOG",
    "ROOT_NOT_IN_MODEL_VISIBLE_CONTEXT",
    "ROOT_VISIBLE_BUT_NOT_METRICS_TOPK",
    "ENTITY_LAYER_MISMATCH",
    "DOWNSTREAM_SYMPTOM_SELECTED",
    "UPSTREAM_ENTITY_SELECTED",
    "ALERT_TARGET_BIAS",
    "FAULT_REGIME_MISMATCH",
    "PROMPT_ENTITY_TASK_MISMATCH",
    "MODEL_REASONING_FAILURE_WITH_SUFFICIENT_EVIDENCE",
    "TERMINAL_FAILURE",
    "UNRESOLVED",
)
_M3_OVERRIDE_CLASSES = (
    "CORRECT_LOCAL_ANOMALY_ROOT",
    "WRONG_LAYER_OVERRIDE",
    "DOWNSTREAM_SYMPTOM_OVERRIDE",
    "ALERT_TARGET_OVERRIDE",
    "RANKING_PROJECTION_ERROR",
    "ROOT_NOT_IN_METRICS_CANDIDATES",
    "HIGH_MARGIN_NON_CAUSAL_ANOMALY",
    "UNRESOLVED",
)
_FAULT_RELATION_CLASSES = (
    "EXACT_NORMALIZED",
    "CASING_OR_SEPARATOR",
    "TOKEN_OVERLAP",
    "SYNONYM_OR_HIERARCHY_MISMATCH",
    "COMPLETELY_DIFFERENT",
)


def _propagation_length_summary(
    cases: Sequence[AdaptedCase], key: str
) -> dict[str, object]:
    raw = [item.propagation_record.get(key) for item in cases]
    if any(value is not None and not isinstance(value, int) for value in raw):
        raise ValueError("private propagation length is not an integer or null")
    available = [int(value) for value in raw if isinstance(value, int)]
    labels = ["UNAVAILABLE" if value is None else str(value) for value in raw]
    return {
        "denominator": len(cases),
        "available": rate(len(available), len(cases)),
        "hop_distribution": _rates_by_label(labels),
        "mean_available_hops": (
            None if not available else sum(available) / len(available)
        ),
        "max_available_hops": None if not available else max(available),
    }


def _propagation_visibility_report(
    rca: Sequence[AdaptedCase], obss: Sequence[AdaptedCase]
) -> dict[str, object]:
    initial_wrong = [item for item in rca if not item.unified.initial_correct_exact]
    damage = [
        item
        for item in rca
        if item.unified.initial_correct_exact and not item.unified.m3_correct_exact
    ]
    wrong_to_wrong = [
        item
        for item in rca
        if item.unified.m3_action == "OVERRIDE_METRICS_TOP1"
        and not item.unified.initial_correct_exact
        and not item.unified.m3_correct_exact
    ]
    sufficiency = [evidence_sufficiency(item.unified) for item in rca]
    strong_failures = [
        classify_strong_single_failure(item.unified) for item in initial_wrong
    ]
    m3_failures = [
        classify_m3_failure(item.unified)
        for item in rca
        if item.unified.m3_action == "OVERRIDE_METRICS_TOP1"
    ]
    exact_visibility = _visibility_aggregate(rca)
    funnel = {
        "ground_truth_in_catalog": exact_visibility["exact"]["catalog"],  # type: ignore[index]
        "ground_truth_in_any_model_visible_evidence": exact_visibility["exact"][  # type: ignore[index]
            "any_model_visible"
        ],
        "ground_truth_in_metrics_top6": exact_visibility["metrics"]["top_6"],  # type: ignore[index]
        "ground_truth_in_causal_evidence": exact_visibility["exact"]["causal"],  # type: ignore[index]
        "initial_selected_ground_truth": _boolean_rate(
            [item.unified.initial_correct_exact for item in rca]
        ),
        "historical_m3_selected_ground_truth": _boolean_rate(
            [item.unified.m3_correct_exact for item in rca]
        ),
    }
    fault_relations = [classify_fault_phrase_relation(item.unified) for item in rca]
    root_correct_fault_wrong = sum(
        item.unified.initial_correct_exact and not item.unified.initial_pair_correct
        for item in rca
    )
    obss_rescues = [
        item
        for item in obss
        if not item.unified.initial_correct_exact and item.unified.m3_correct_exact
    ]
    damage_margin_bins = [
        (
            "UNAVAILABLE"
            if item.unified.metrics_margin is None
            else "AT_LEAST_0_50"
            if item.unified.metrics_margin >= 0.5
            else "0_25_TO_0_50"
            if item.unified.metrics_margin >= 0.25
            else "BELOW_0_25"
        )
        for item in damage
    ]
    wrong_gt_top6 = [
        any(
            candidate.entity in item.unified.ground_truth_equivalent_entities
            for candidate in item.unified.metrics_candidates[:6]
        )
        for item in wrong_to_wrong
    ]
    wrong_earlier_truth_candidate = [
        any(
            candidate.entity in item.unified.ground_truth_equivalent_entities
            and candidate.first_anomaly_time is not None
            and item.unified.metrics_top1 is not None
            and item.unified.metrics_top1.first_anomaly_time is not None
            and candidate.first_anomaly_time
            < item.unified.metrics_top1.first_anomaly_time
            for candidate in item.unified.metrics_candidates
        )
        for item in wrong_to_wrong
    ]
    obss_initial_wrong = [
        item for item in obss if not item.unified.initial_correct_exact
    ]
    return {
        "schema_version": "rca100-propagation-visibility-attribution.v1",
        "classification": list(CLASSIFICATION),
        "denominators": {
            "rca100": len(rca),
            "rca100_initial_wrong": len(initial_wrong),
            "rca100_historical_m3_damage": len(damage),
            "rca100_historical_m3_wrong_to_wrong": len(wrong_to_wrong),
            "obss_consumed_records": len(obss),
        },
        "propagation_roles": {
            stage: _rates_by_label(
                [str(item.propagation_record[f"{prefix}_role"]) for item in rca]
            )
            for stage, prefix in (
                ("initial", "initial"),
                ("historical_m3", "m3"),
                ("metrics_top1", "metrics_top1"),
            )
        },
        "damage_roles": _rates_by_label(
            [str(item.propagation_record["metrics_top1_role"]) for item in damage]
        ),
        "historical_m3_damage_audit": {
            "metrics_top1_later_than_ground_truth": _boolean_rate(
                [
                    bool(
                        item.propagation_record[
                            "metrics_top1_later_than_ground_truth"
                        ]
                    )
                    for item in damage
                ]
            ),
            "metrics_top1_downstream": _boolean_rate(
                [
                    item.propagation_record["metrics_top1_role"]
                    == "DOWNSTREAM_SYMPTOM"
                    for item in damage
                ]
            ),
            "metrics_top1_alert_target": _boolean_rate(
                [
                    item.propagation_record["metrics_top1_role"]
                    == "ALERT_TARGET_ONLY"
                    for item in damage
                ]
            ),
            "metrics_top1_high_fan_in": _boolean_rate(
                [
                    bool(item.propagation_record["metrics_top1_high_fan_in"])
                    for item in damage
                ]
            ),
            "traffic_volume_available": _boolean_rate(
                [
                    bool(item.propagation_record["traffic_volume_available"])
                    for item in damage
                ]
            ),
            "initial_correct_evidence_sufficiency": _rates_by_label(
                [evidence_sufficiency(item.unified) for item in damage]
            ),
            "confident_wrong_margin_bins": _rates_by_label(damage_margin_bins),
            "initial_metrics_rank": _rates_by_label(
                [
                    "ABSENT"
                    if item.unified.metrics_initial_rank is None
                    else str(item.unified.metrics_initial_rank)
                    for item in damage
                ]
            ),
        },
        "wrong_to_wrong_roles": _rates_by_label(
            [
                str(item.propagation_record["metrics_top1_role"])
                for item in wrong_to_wrong
            ]
        ),
        "wrong_to_wrong_audit": {
            "ground_truth_in_metrics_top6": _boolean_rate(wrong_gt_top6),
            "ground_truth_candidate_earlier_than_top1": _boolean_rate(
                wrong_earlier_truth_candidate
            ),
            "top1_upstream_or_root": _boolean_rate(
                [
                    item.propagation_record["metrics_top1_role"]
                    in {"UPSTREAM_OF_ROOT", "ROOT_EARLIEST_ANOMALY"}
                    for item in wrong_to_wrong
                ]
            ),
            "topology_or_trace_can_exclude_top1": _boolean_rate(
                [
                    bool(item.unified.metrics_top1_is_downstream)
                    or item.unified.propagation_disposition.value == "PRESENT"
                    for item in wrong_to_wrong
                ]
            ),
        },
        "first_anomaly_alignment": _boolean_rate(
            [
                item.propagation_record["metrics_top1_role"]
                == "ROOT_EARLIEST_ANOMALY"
                for item in rca
            ]
        ),
        "downstream_symptom": _boolean_rate(
            [
                item.propagation_record["metrics_top1_role"]
                == "DOWNSTREAM_SYMPTOM"
                for item in rca
            ]
        ),
        "no_graph_path": _boolean_rate(
            [
                item.propagation_record["metrics_top1_role"] == "NO_GRAPH_PATH"
                for item in rca
            ]
        ),
        "visibility": exact_visibility,
        "root_visibility_funnel": funnel,
        "evidence_sufficiency": _rates_by_label(sufficiency),
        "strong_single_failure_decomposition": _rates_by_required_label(
            strong_failures, _STRONG_SINGLE_FAILURE_CLASSES
        ),
        "historical_m3_override_decomposition": _rates_by_required_label(
            m3_failures, _M3_OVERRIDE_CLASSES
        ),
        "fault_phrase_relation": _rates_by_required_label(
            fault_relations, _FAULT_RELATION_CLASSES
        ),
        "root_correct_fault_exact_wrong": rate(root_correct_fault_wrong, len(rca)),
        "focus_visibility": {
            "rca100_initial_wrong": _visibility_aggregate(initial_wrong),
            "rca100_historical_m3_damage": _visibility_aggregate(damage),
            "rca100_historical_m3_wrong_to_wrong": _visibility_aggregate(
                wrong_to_wrong
            ),
            "obss_initial_wrong": _visibility_aggregate(
                obss_initial_wrong
            ),
        },
        "obss_historical_m3_rescue_mechanisms": _rates_by_label(
            [_obss_rescue_mechanism(item) for item in obss_rescues]
        ),
        "cross_benchmark_contrast": {
            "target_layer": {
                "rca100": _rates_by_label(
                    [item.unified.ground_truth_layer.value for item in rca]
                ),
                "obss": _rates_by_label(
                    [item.unified.ground_truth_layer.value for item in obss]
                ),
            },
            "runtime_fault_regime": {
                "rca100": _rates_by_label(
                    [item.unified.fault_regime.value for item in rca]
                ),
                "obss": _rates_by_label(
                    [item.unified.fault_regime.value for item in obss]
                ),
            },
            "fault_family": {
                "rca100": _rates_by_label(
                    [item.unified.fault_family for item in rca]
                ),
                "obss": _rates_by_label(
                    [item.unified.fault_family for item in obss]
                ),
            },
            "propagation_length": {
                "ground_truth_to_initial": {
                    "rca100": _propagation_length_summary(
                        rca, "ground_truth_to_initial_hops"
                    ),
                    "obss": _propagation_length_summary(
                        obss, "ground_truth_to_initial_hops"
                    ),
                },
                "ground_truth_to_metrics_top1": {
                    "rca100": _propagation_length_summary(
                        rca, "ground_truth_to_metrics_top1_hops"
                    ),
                    "obss": _propagation_length_summary(
                        obss, "ground_truth_to_metrics_top1_hops"
                    ),
                },
            },
            "metrics_top1_causal_alignment": {
                "rca100": _boolean_rate(
                    [
                        item.propagation_record["metrics_top1_role"]
                        in {"ROOT_EARLIEST_ANOMALY", "UPSTREAM_OF_ROOT"}
                        for item in rca
                    ]
                ),
                "obss": _boolean_rate(
                    [
                        item.propagation_record["metrics_top1_role"]
                        in {"ROOT_EARLIEST_ANOMALY", "UPSTREAM_OF_ROOT"}
                        for item in obss
                    ]
                ),
            },
            "metrics_top6_ground_truth_coverage": {
                "rca100": exact_visibility["metrics"]["top_6"],  # type: ignore[index]
                "obss": _visibility_aggregate(obss)["metrics"]["top_6"],  # type: ignore[index]
            },
            "margin_at_least_0_25": {
                "rca100": _boolean_rate(
                    [
                        item.unified.metrics_margin is not None
                        and item.unified.metrics_margin >= 0.25
                        for item in rca
                    ]
                ),
                "obss": _boolean_rate(
                    [
                        item.unified.metrics_margin is not None
                        and item.unified.metrics_margin >= 0.25
                        for item in obss
                    ]
                ),
            },
            "topology_depth": {
                "rca100_mean": sum(
                    len(path)
                    for item in rca
                    if isinstance(
                        (path := item.hierarchy_record["ground_truth_path"]), list
                    )
                )
                / len(rca),
                "obss_mean": sum(
                    len(path)
                    for item in obss
                    if isinstance(
                        (path := item.hierarchy_record["ground_truth_path"]), list
                    )
                )
                / len(obss),
            },
        },
    }


def _obss_rescue_mechanism(item: AdaptedCase) -> str:
    case = item.unified
    if case.fault_regime.value == "LOCAL_RESOURCE":
        return "LOCAL_RESOURCE_ROOT"
    if case.fault_regime.value == "NETWORK":
        return "NETWORK_LOCAL_ROOT"
    truth_rank = next(
        (
            candidate.rank
            for candidate in case.metrics_candidates
            if candidate.entity in case.ground_truth_equivalent_entities
        ),
        None,
    )
    if truth_rank is not None and truth_rank > 1:
        return "RANK_CORRECTION"
    if case.ground_truth_layer.value == "SERVICE":
        return "SERVICE_LEVEL_TARGET"
    return "OTHER"


def _causal_agent_audit(
    cases: Sequence[AdaptedCase],
    outcomes: Mapping[ArchitectureOption, Sequence[FrontierOutcome]],
    frontier: Any,
) -> tuple[dict[str, int | float | bool], dict[str, object]]:
    frontier_cases = [item.unified.to_frontier_case() for item in cases]
    wrong_cases = [
        item
        for item in cases
        if not item.unified.initial_correct_exact and not item.unified.terminal_failure
    ]
    initial_wrong = sum(
        not item.initial_exact_correct for item in outcomes[ArchitectureOption.A0]
    )
    layer_relations = {
        "PREDICTED_ANCESTOR",
        "PREDICTED_DESCENDANT",
        "SIBLING_SAME_PARENT",
        "SAME_SERVICE_DIFFERENT_INSTANCE",
        "SAME_NODE",
    }
    role_coverage = {
        "TRACE_TOPOLOGY_CAUSAL_VERIFIER": sum(
            item.unified.ground_truth_entity in item.unified.causal_visible_entities
            for item in wrong_cases
        ),
        "EVENTS_CHANGE_VERIFIER": sum(
            item.unified.ground_truth_entity
            in item.unified.visibility.events_entities
            for item in wrong_cases
        ),
        "HIERARCHY_RESOLVER": sum(
            item.unified.initial_relation in layer_relations for item in wrong_cases
        ),
        "FAULT_TYPE_ONTOLOGY_VERIFIER": 0,
    }
    role_properties = {
        "TRACE_TOPOLOGY_CAUSAL_VERIFIER": (True, True, True, True),
        "EVENTS_CHANGE_VERIFIER": (True, True, True, True),
        "HIERARCHY_RESOLVER": (True, False, True, False),
        "FAULT_TYPE_ONTOLOGY_VERIFIER": (True, False, True, False),
    }
    role_public: dict[str, object] = {}
    qualified_roles: set[str] = set()
    for role, coverage_count in role_coverage.items():
        information_not_fully_consumed, distinguishes, evaluable, nonredundant = (
            role_properties[role]
        )
        coverage_value = (
            0.0 if initial_wrong == 0 else coverage_count / initial_wrong
        )
        qualified = bool(
            information_not_fully_consumed
            and coverage_value >= 0.2
            and distinguishes
            and evaluable
            and nonredundant
        )
        if qualified:
            qualified_roles.add(role)
        role_public[role] = {
            "correct_root_or_path_coverage": rate(coverage_count, initial_wrong),
            "information_not_fully_consumed": information_not_fully_consumed,
            "distinguishes_root_from_symptom": distinguishes,
            "deterministic_proxy_evaluable": evaluable,
            "additional_call_nonredundant": nonredundant,
            "qualified": qualified,
        }
    eligible: dict[str, str] = {}
    for case, adapted in zip(frontier_cases, cases, strict=True):
        if adapted.unified.initial_correct_exact or adapted.unified.terminal_failure:
            continue
        if adapted.unified.fault_regime.value == "LOCAL_RESOURCE":
            continue
        selected = causal_selection(case, frontier)
        if selected is None or selected == case.initial_entity:
            continue
        candidate = next(item for item in case.causal_candidates if item.entity == selected)
        trace_support = bool(
            "TRACE_TOPOLOGY_CAUSAL_VERIFIER" in qualified_roles
            and candidate.source_support >= 2
            and candidate.relation_to_symptom in {"ROOT", "UPSTREAM"}
        )
        event_support = bool(
            "EVENTS_CHANGE_VERIFIER" in qualified_roles
            and selected in adapted.unified.visibility.events_entities
        )
        if trace_support or event_support:
            eligible[case.private_case_key] = selected
    coverage = 0.0 if initial_wrong == 0 else len(eligible) / initial_wrong
    oracle_rescue = 0
    oracle_damage = 0
    oracle_by_fixture: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for adapted, base in zip(cases, outcomes[ArchitectureOption.A5], strict=True):
        selected = eligible.get(adapted.unified.private_case_key)
        oracle_correct = base.final_exact_correct or bool(
            selected is not None
            and selected in adapted.unified.ground_truth_equivalent_entities
        )
        oracle_rescue += not base.initial_exact_correct and oracle_correct
        oracle_damage += base.initial_exact_correct and not oracle_correct
        oracle_by_fixture[adapted.unified.fixture].append(
            (base.initial_exact_correct, oracle_correct)
        )
    rca_pairs = oracle_by_fixture["RCA100"]
    rca_rescue = sum(not initial and final for initial, final in rca_pairs)
    rca_damage = sum(initial and not final for initial, final in rca_pairs)
    obss_ok = all(
        sum((not initial and final) - (initial and not final) for initial, final in pairs)
        >= 0
        for fixture, pairs in oracle_by_fixture.items()
        if fixture != "RCA100"
    )
    message_nonredundant = bool(qualified_roles and coverage >= 0.2)
    selection_input: dict[str, int | float | bool] = {
        "eligible_case_count": len(eligible),
        "eligible_initial_wrong_coverage": coverage,
        "source_evidence_distinguishes_root_symptom": bool(qualified_roles),
        "oracle_rca100_net_rescue": rca_rescue - rca_damage,
        "oracle_damage": oracle_damage,
        "obss_expected_non_degradation": obss_ok,
        "message_contract_nonredundant": message_nonredundant,
        "mean_model_calls": 1.0 + len(eligible) / len(cases),
    }
    public = {
        "candidate_role_audit": role_public,
        "eligible_roles": sorted(qualified_roles),
        "eligible_cases": rate(len(eligible), len(cases)),
        "eligible_initial_wrong_coverage": rate(len(eligible), initial_wrong),
        "source_evidence_distinguishes_root_symptom": bool(qualified_roles),
        "oracle_root_rescue_all_consumed": oracle_rescue,
        "oracle_root_damage_all_consumed": oracle_damage,
        "oracle_rca100_rescue": rca_rescue,
        "oracle_rca100_damage": rca_damage,
        "oracle_rca100_net_rescue": rca_rescue - rca_damage,
        "mean_model_calls": selection_input["mean_model_calls"],
        "message_contract_nonredundant": message_nonredundant,
        "obss_expected_non_degradation": obss_ok,
        "output_space": ["KEEP_INITIAL", "SELECT_CANDIDATE", "INCONCLUSIVE"],
        "free_form_root_generation": False,
    }
    return selection_input, public


def _frontier_report(
    cases: Sequence[AdaptedCase],
    outcomes: Mapping[ArchitectureOption, Sequence[FrontierOutcome]],
    fixture_aggregates: Mapping[str, Mapping[str, Mapping[str, int | float]]],
    robustness: Sequence[Any],
    causal_public: Mapping[str, object],
    selected: ArchitectureOption,
    frontier: Any,
) -> dict[str, object]:
    fixture_names = (
        "candidate-3",
        "candidate-4",
        "candidate-5",
        "pr21-tune",
        "pr21-regression",
    )
    by_fixture = {
        option.value: {
            fixture: _public_outcome_summary(
                [
                    outcome
                    for adapted, outcome in zip(cases, values, strict=True)
                    if adapted.unified.fixture == fixture
                ],
                [item for item in cases if item.unified.fixture == fixture],
                option,
            )
            for fixture in fixture_names
        }
        for option, values in outcomes.items()
        if option is not ArchitectureOption.A5
    }
    option_public: dict[str, object] = {}
    for option, values in outcomes.items():
        definition = frontier.options[option.value]
        if option is ArchitectureOption.A5:
            option_public[option.value] = {
                "name": definition.name,
                "selectable": definition.selectable,
                "reporting_boundary": "ORACLE_ONLY_NO_VERIFIER_OUTPUT",
                "oracle_upper_bound": dict(causal_public),
                "eligible_case_count": causal_public.get("eligible_cases"),
                "expected_model_calls": causal_public.get("mean_model_calls"),
            }
            continue
        rca_values = [
            outcome
            for adapted, outcome in zip(cases, values, strict=True)
            if adapted.unified.fixture == "RCA100"
        ]
        rca_cases = [item for item in cases if item.unified.fixture == "RCA100"]
        option_public[option.value] = {
            "name": definition.name,
            "selectable": definition.selectable,
            "all_consumed": _public_outcome_summary(values, cases, option),
            "rca100": _public_outcome_summary(rca_values, rca_cases, option),
            "obss_fixtures": by_fixture[option.value],
            "expected_model_calls": definition.config.get("model_calls"),
        }
    fold_public = [
        {
            "option": item.option.value,
            "axis": item.axis,
            "held_out_group": item.held_out_group,
            "denominator": item.denominator,
            "root_rescue": item.rescue,
            "root_damage": item.damage,
            "root_net_rescue": item.net_rescue,
        }
        for item in robustness
        if item.option is not ArchitectureOption.A5
    ]
    return {
        "schema_version": "rca-crossbenchmark-architecture-frontier-report.v1",
        "classification": list(CLASSIFICATION),
        "consumed_denominators": {
            "rca100": 103,
            "candidate_3": 60,
            "candidate_4": 60,
            "candidate_5": 60,
            "pr21_tune": 60,
            "pr21_regression": 120,
        },
        "options": option_public,
        "selective_causal_agent_oracle_only": dict(causal_public),
        "grouped_robustness": fold_public,
        "selected_option": selected.value,
        "selected_name": frontier.options[selected.value].name,
        "selection_reason": (
            "HIGHEST_PASSING_FROZEN_PRIORITY"
            if selected is not ArchitectureOption.A0
            else "A2_TO_A5_GATES_NOT_ALL_SATISFIED_FALLBACK_A0"
        ),
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "semantic_operations": 0,
        "new_external_data_accessed": False,
        "re2_tt_accessed": False,
    }


_DECISION_NAMES = {
    ArchitectureOption.A0: "STRONG_SINGLE_HIERARCHICAL",
    ArchitectureOption.A2: "HIERARCHY_GUARDED_METRICS_ARBITRATION",
    ArchitectureOption.A3: "LOCAL_FAULT_METRICS_ARBITRATION",
    ArchitectureOption.A4: "HYBRID_LOCAL_METRICS_CAUSAL_RANKING",
    ArchitectureOption.A5: "HYBRID_LOCAL_METRICS_SELECTIVE_CAUSAL_AGENT",
}

_CORRECTED_PROTECTED_PATHS = (
    "config/rca-crossbenchmark-architecture-convergence-v1/frontier.json",
    "config/rca-crossbenchmark-architecture-convergence-v1/methodology.json",
    "scripts/analysis/rca_crossbenchmark_attribution.py",
    "src/ecomsre_rca_unified/__init__.py",
    "src/ecomsre_rca_unified/adapters.py",
    "src/ecomsre_rca_unified/analysis.py",
    "src/ecomsre_rca_unified/contracts.py",
    "src/ecomsre_rca_unified/frontier.py",
    "src/ecomsre_rca_unified/hierarchy.py",
    "src/ecomsre_rca_unified/propagation.py",
    "src/ecomsre_rca_unified/runtime.py",
    "tests/analysis/test_rca_crossbenchmark_analysis.py",
    "tests/analysis/test_rca_crossbenchmark_attribution.py",
    "tests/analysis/test_rca_crossbenchmark_frontier.py",
)
_CORRECTED_GENERATED_PATHS = (
    "docs/analysis/rca-crossbenchmark-architecture-frontier.json",
    "docs/analysis/rca-crossbenchmark-architecture-frontier.md",
    "docs/analysis/rca100-entity-hierarchy-attribution.json",
    "docs/analysis/rca100-entity-hierarchy-attribution.md",
    "docs/analysis/rca100-propagation-visibility-attribution.json",
    "docs/analysis/rca100-propagation-visibility-attribution.md",
    "docs/design/rca-crossbenchmark-architecture-decision.md",
    "docs/design/unified-hierarchical-rca-v1-spec.md",
    "docs/results/unified-hierarchical-rca-v1-human-brief.md",
    "docs/results/unified-hierarchical-rca-v1-offline-replay.json",
    "docs/results/unified-hierarchical-rca-v1-offline-replay.md",
)
_CORRECTED_LIFECYCLES: dict[int, dict[str, str]] = {
    2: {
        "results": "results-v2",
        "implementation_state": "CORRECTED_METHOD_IMPLEMENTATION_FROZEN",
        "implementation_lock": "corrected-method-implementation-lock.json",
        "analysis_state": "CORRECTED_ATTRIBUTION_COMPLETE",
        "analysis_lock": "corrected-attribution-complete-lock.json",
        "decision_state": "CORRECTED_ARCHITECTURE_DECISION_FROZEN",
        "decision_lock": "corrected-architecture-decision-lock.json",
        "replay_state": "CORRECTED_IMPLEMENTATION_REPLAY_FROZEN",
        "replay_lock": "corrected-implementation-replay-lock.json",
        "public_state": "CORRECTED_PUBLIC_OUTPUTS_VERIFIED",
        "public_lock": "corrected-public-verification-lock.json",
    },
    3: {
        "results": "results-v3",
        "implementation_state": "CORRECTED_V3_METHOD_IMPLEMENTATION_FROZEN",
        "implementation_lock": "corrected-v3-method-implementation-lock.json",
        "analysis_state": "CORRECTED_V3_ATTRIBUTION_COMPLETE",
        "analysis_lock": "corrected-v3-attribution-complete-lock.json",
        "decision_state": "CORRECTED_V3_ARCHITECTURE_DECISION_FROZEN",
        "decision_lock": "corrected-v3-architecture-decision-lock.json",
        "replay_state": "CORRECTED_V3_IMPLEMENTATION_REPLAY_FROZEN",
        "replay_lock": "corrected-v3-implementation-replay-lock.json",
        "public_state": "CORRECTED_V3_PUBLIC_OUTPUTS_VERIFIED",
        "public_lock": "corrected-v3-public-verification-lock.json",
    },
}
_CORRECTED_V3_ALLOWED_CHANGED_PROTECTED_PATHS = frozenset(
    {
        "scripts/analysis/rca_crossbenchmark_attribution.py",
        "src/ecomsre_rca_unified/adapters.py",
        "src/ecomsre_rca_unified/analysis.py",
        "src/ecomsre_rca_unified/propagation.py",
        "tests/analysis/test_rca_crossbenchmark_analysis.py",
        "tests/analysis/test_rca_crossbenchmark_attribution.py",
        "tests/analysis/test_rca_crossbenchmark_frontier.py",
    }
)


def _corrected_version(args: argparse.Namespace) -> int:
    raw = getattr(args, "corrected_version", None)
    if raw is None:
        return 2 if bool(getattr(args, "corrected", False)) else 0
    version = int(raw)
    if version not in _CORRECTED_LIFECYCLES:
        raise ValueError("unknown corrected lifecycle version")
    return version


def _corrected_lifecycle(version: int) -> Mapping[str, str]:
    try:
        return _CORRECTED_LIFECYCLES[version]
    except KeyError as exc:
        raise ValueError("corrected lifecycle version is required") from exc


def _correction_disclosure(version: int) -> dict[str, object]:
    if version == 2:
        return {
            "status": "CORRECTED_APPEND_ONLY_SUCCESSOR",
            "supersedes": "INVALID_GT_DERIVED_AND_UNFROZEN_ANALYSIS_ATTEMPT",
            "thresholds_changed": False,
        }
    if version == 3:
        return {
            "status": "CORRECTED_V3_APPEND_ONLY_SUCCESSOR",
            "supersedes": "CORRECTED_V2_GOAL_COVERAGE_INCOMPLETE",
            "original_invalid_attempt_preserved": True,
            "thresholds_changed": False,
        }
    raise ValueError("corrected disclosure version is invalid")


def _protected_hashes() -> dict[str, str]:
    return {
        relative: sha256_file(PROJECT_ROOT / relative)
        for relative in _CORRECTED_PROTECTED_PATHS
    }


def _verify_corrected_implementation(lock: Mapping[str, object]) -> None:
    expected = lock.get("protected_files")
    if not isinstance(expected, Mapping) or expected != _protected_hashes():
        raise ValueError("corrected attribution implementation drifted after freeze")


def freeze_corrected_implementation(args: argparse.Namespace) -> int:
    """Append-only freeze the bug-repaired implementation before corrected rerun."""

    assert_no_provider_environment()
    private_root = validate_private_root(args.private_root, create=False)
    _, format_lock_path, format_state_path = _verify_state_binding(
        private_root,
        state_name="PUBLIC_OUTPUTS_FORMAT_REPAIRED",
        lock_name="public-output-format-repair-lock.json",
    )
    completed = subprocess.run(
        ("git", "ls-files", "--others", "--exclude-standard"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    live_paths = set(completed.stdout.splitlines())
    expected_paths = set(_CORRECTED_PROTECTED_PATHS) | set(
        _CORRECTED_GENERATED_PATHS
    )
    if live_paths != expected_paths:
        raise ValueError("corrected implementation surface is not the exact 25 paths")
    created_at = utc_now()
    lock_path = private_root / "locks/corrected-method-implementation-lock.json"
    write_json_create_once(
        lock_path,
        {
            "schema_version": "rca-crossbenchmark.corrected-method-lock.v1",
            "created_at_utc": created_at,
            "classification": list(CLASSIFICATION),
            "previous_lock_sha256": sha256_file(format_lock_path),
            "protected_files": _protected_hashes(),
            "generated_public_allowlist": list(_CORRECTED_GENERATED_PATHS),
            "repair_scope": [
                "LABEL_FREE_RUNTIME_FAULT_ONTOLOGY",
                "TEMPORAL_FIRST_ANOMALY_ROLE",
                "COMPLETE_ATTRIBUTION_AND_FOUR_ROLE_AUDIT",
                "A5_ORACLE_ONLY_PUBLIC_BOUNDARY",
                "LABEL_FREE_RUNTIME_INPUT_AND_EVIDENCE_PRESERVATION",
                "INDEPENDENT_CANONICAL_PUBLIC_RECOMPUTATION",
            ],
            "frontier_thresholds_changed": False,
            "post_unblinding_bug_repair": True,
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "semantic_operations": 0,
        },
    )
    write_json_create_once(
        private_root / "state/CORRECTED_METHOD_IMPLEMENTATION_FROZEN.json",
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": "CORRECTED_METHOD_IMPLEMENTATION_FROZEN",
            "created_at_utc": created_at,
            "previous_state": "PUBLIC_OUTPUTS_FORMAT_REPAIRED",
            "previous_state_record_sha256": sha256_file(format_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print("[freeze] corrected method implementation bound", flush=True)
    return 0


def freeze_corrected_v3_implementation(args: argparse.Namespace) -> int:
    """Freeze the append-only Goal-coverage successor after corrected-v2."""

    assert_no_provider_environment()
    private_root = validate_private_root(args.private_root, create=False)
    _, previous_lock_path, previous_state_path = _verify_state_binding(
        private_root,
        state_name="CORRECTED_PUBLIC_OUTPUTS_VERIFIED",
        lock_name="corrected-public-verification-lock.json",
    )
    old_implementation = read_object(
        private_root / "locks/corrected-method-implementation-lock.json"
    )
    old_hashes = _mapping(
        old_implementation.get("protected_files"),
        "corrected-v2 protected files",
    )
    completed = subprocess.run(
        ("git", "ls-files", "--others", "--exclude-standard"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    live_paths = set(completed.stdout.splitlines())
    expected_paths = set(_CORRECTED_PROTECTED_PATHS) | set(
        _CORRECTED_GENERATED_PATHS
    )
    if live_paths != expected_paths:
        raise ValueError("corrected-v3 implementation surface is not exact")
    new_hashes = _protected_hashes()
    changed_paths = {
        path for path, digest in new_hashes.items() if old_hashes.get(path) != digest
    }
    required_changed = {
        "scripts/analysis/rca_crossbenchmark_attribution.py",
        "src/ecomsre_rca_unified/adapters.py",
        "src/ecomsre_rca_unified/analysis.py",
        "src/ecomsre_rca_unified/propagation.py",
    }
    if (
        not required_changed.issubset(changed_paths)
        or not changed_paths.issubset(
            _CORRECTED_V3_ALLOWED_CHANGED_PROTECTED_PATHS
        )
    ):
        raise ValueError("corrected-v3 protected change scope differs")
    for frozen_path in (
        "config/rca-crossbenchmark-architecture-convergence-v1/frontier.json",
        "config/rca-crossbenchmark-architecture-convergence-v1/methodology.json",
        "src/ecomsre_rca_unified/contracts.py",
        "src/ecomsre_rca_unified/frontier.py",
        "src/ecomsre_rca_unified/hierarchy.py",
        "src/ecomsre_rca_unified/runtime.py",
    ):
        if old_hashes.get(frozen_path) != new_hashes[frozen_path]:
            raise ValueError(f"corrected-v3 changed frozen behavior: {frozen_path}")
    created_at = utc_now()
    lock_path = private_root / "locks/corrected-v3-method-implementation-lock.json"
    write_json_create_once(
        lock_path,
        {
            "schema_version": "rca-crossbenchmark.corrected-v3-method-lock.v1",
            "created_at_utc": created_at,
            "classification": list(CLASSIFICATION),
            "previous_lock_sha256": sha256_file(previous_lock_path),
            "corrected_v2_implementation_lock_sha256": sha256_file(
                private_root / "locks/corrected-method-implementation-lock.json"
            ),
            "protected_files": new_hashes,
            "changed_protected_files": sorted(changed_paths),
            "generated_public_allowlist": list(_CORRECTED_GENERATED_PATHS),
            "repair_scope": [
                "PER_SOURCE_VISIBILITY_AND_COVISIBILITY",
                "FAULT_FAMILY_AND_PROPAGATION_LENGTH_CONTRAST",
                "PER_OPTION_LAYER_DOWNSTREAM_AND_TOOL_USE",
                "COMPLETE_REQUIRED_TAXONOMY_WITH_ZERO_COUNTS",
                "INDEPENDENT_CANONICAL_PUBLIC_RECOMPUTATION",
            ],
            "frontier_thresholds_changed": False,
            "runtime_changed": False,
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "semantic_operations": 0,
            "re2_tt_accessed": False,
            "new_external_data_accessed": False,
        },
    )
    write_json_create_once(
        private_root / "state/CORRECTED_V3_METHOD_IMPLEMENTATION_FROZEN.json",
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": "CORRECTED_V3_METHOD_IMPLEMENTATION_FROZEN",
            "created_at_utc": created_at,
            "previous_state": "CORRECTED_PUBLIC_OUTPUTS_VERIFIED",
            "previous_state_record_sha256": sha256_file(previous_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print("[freeze] corrected-v3 Goal coverage implementation bound", flush=True)
    return 0


def _decision_markdown(
    selected: ArchitectureOption,
    entity: Mapping[str, object],
    propagation: Mapping[str, object],
    frontier_report: Mapping[str, object],
) -> str:
    name = _DECISION_NAMES[selected]
    rejected = [item for item in ("A0", "A2", "A3", "A4", "A5") if item != selected.value]
    raw_folds = frontier_report.get("grouped_robustness")
    fold_count = len(raw_folds) if isinstance(raw_folds, list) else 0
    correction_note = (
        "\nThis record is the append-only corrected successor to an invalid "
        "GT-derived/unfrozen analysis attempt. Frozen thresholds were not changed.\n"
        if "correction_disclosure" in frontier_report
        else ""
    )
    return f"""# RCA Cross-Benchmark Architecture Decision

Status: **FROZEN — {name}**

Classification: `{', '.join(CLASSIFICATION)}`.
{correction_note}

## Decision

Select `{selected.value}` / `{name}` as the only `unified-hierarchical-rca-v1` architecture. This decision uses frozen consumed-development evidence only, preserves the official results, and is not external validation or primary inference.

Rejected options: {', '.join(rejected)}. A1 is historical comparison only and is not selectable.

## Evidence

- Entity hierarchy: RCA100 denominator `{entity['denominator']}`; multi-level exact, service, workload, node, and topology-component diagnostics are frozen in the aggregate report.
- Propagation and visibility: all source funnels, causal roles, Strong Single failures, M3 failures, and fault-phrase relations are frozen in the aggregate report with explicit denominators.
- Communication: the Trace/Topology Causal Verifier was assessed only as an oracle upper bound; no verifier output or Provider call was fabricated.
- Cross-benchmark counterfactual: all A0–A5 outcomes cover the fixed 103 + 60 + 60 + 60 + 60 + 120 records.
- Robustness: `{fold_count}` grouped leave-one-out folds are frozen.
- Cost: the selected architecture's expected call count is the value frozen for `{selected.value}`; no new tool or Provider operation is introduced in replay.

## Remaining uncertainty

All evidence is post-hoc and consumed-development. Generalization and live verifier behavior remain unmeasured. A future live development evaluation requires separate authorization and must not be represented by this replay.
"""


def _spec_markdown(selected: ArchitectureOption, frontier: Any) -> str:
    definition = frontier.options[selected.value]
    return f"""# Unified Hierarchical RCA v1 Specification

Version: `unified-hierarchical-rca-v1`
Selected option: `{selected.value}` / `{definition.name}`

## Typed input

The runtime accepts a benchmark-independent typed case projection: canonical entity layer, explicit hierarchy/service ancestry, typed fault ontology, Metrics Top-6 ranks and margin, propagation disposition, first-anomaly timing, causal source support, and evidence visibility. Benchmark or system identity is never a routing feature.

## Decision rule

Frozen strategy: `{definition.strategy}`. Root provenance is emitted for every decision. Fault ontology is preserved from the Strong Single initial diagnosis; root arbitration never rewrites the frozen fault phrase.

## Safety and cost

- No arbitrary root generation; outputs are the Initial root or a frozen candidate.
- No Provider construction or call is permitted in offline replay.
- Missing or insufficient evidence fails closed to the Initial root.
- Expected model calls: `{definition.config.get('model_calls')}`.
- The exact runtime outcome must equal the frozen Phase G counterfactual for every consumed record.

## Evidence boundary

This specification is derived from consumed-development, post-hoc attribution. It is not external validation, primary inference, a release claim, or authorization for live evaluation.
"""


def analyze(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    private_root = validate_private_root(args.private_root, create=False)
    corrected_version = _corrected_version(args)
    corrected = corrected_version > 0
    lifecycle = (
        _corrected_lifecycle(corrected_version) if corrected else None
    )
    input_lock, input_lock_path, _ = _verify_state_binding(
        private_root,
        state_name="INPUTS_AND_FRONTIER_FROZEN",
        lock_name="input-and-frontier-lock.json",
    )
    methods_lock, methods_lock_path, methods_state_path = _verify_state_binding(
        private_root,
        state_name="ATTRIBUTION_METHODS_FROZEN",
        lock_name="attribution-methods-lock.json",
    )
    implementation_state_path = methods_state_path
    implementation_lock_path: Path | None = None
    if corrected:
        assert lifecycle is not None
        implementation_lock, implementation_lock_path, implementation_state_path = (
            _verify_state_binding(
                private_root,
                state_name=lifecycle["implementation_state"],
                lock_name=lifecycle["implementation_lock"],
            )
        )
        _verify_corrected_implementation(implementation_lock)
    if methods_lock.get("input_frontier_lock_sha256") != sha256_file(input_lock_path):
        raise ValueError("methodology lock is not bound to frozen inputs")
    if any(
        methods_lock.get(key) != 0
        for key in ("provider_calls", "semantic_operations")
    ):
        raise ValueError("methodology lock contains external operations")
    frontier_path = _safe_path(args.frontier)
    methodology_path = _safe_path(args.methodology)
    if methods_lock.get("frontier_sha256") != sha256_file(frontier_path):
        raise ValueError("frontier drifted after methodology lock")
    if methods_lock.get("methodology_sha256") != sha256_file(methodology_path):
        raise ValueError("methodology drifted after methodology lock")
    print("[integrity] fresh frozen-input hashing", flush=True)
    _verify_frozen_inputs(args, input_lock)
    methodology = read_object(methodology_path)
    _validate_methodology(methodology)
    frontier = load_frontier(frontier_path)
    print("[adapter] RCA100", flush=True)
    rca = load_rca100_cases(
        cases_root=_safe_path(args.rca_input_root),
        terminals_root=_safe_path(args.rca_terminal_root),
        schedule_path=_safe_path(args.rca_schedule),
        answer_root=_safe_path(args.rca_answer_root),
        methodology=methodology,
        progress=_progress,
    )
    print("[adapter] OB/SS", flush=True)
    obss = load_obss_cases(
        candidate_3_root=_safe_path(args.candidate_3_root),
        candidate_4_root=_safe_path(args.candidate_4_root),
        candidate_5_root=_safe_path(args.candidate_5_root),
        tune_root=_safe_path(args.tune_root),
        regression_root=_safe_path(args.regression_root),
        ob_root=_safe_path(args.ob_root),
        ss_root=_safe_path(args.ss_root),
        indicator_config_path=_safe_path(args.indicator_config),
        indicator_config_sha256=args.indicator_config_sha256,
        methodology=methodology,
        progress=_progress,
    )
    cases = rca + obss
    if len(rca) != 103 or len(obss) != 360 or len(cases) != 463:
        raise ValueError("unified consumed denominator differs")
    rca_initial = sum(item.unified.initial_correct_exact for item in rca)
    rca_final = sum(item.unified.m3_correct_exact for item in rca)
    rca_override = sum(
        item.unified.m3_action == "OVERRIDE_METRICS_TOP1" for item in rca
    )
    rca_damage = sum(
        item.unified.initial_correct_exact and not item.unified.m3_correct_exact
        for item in rca
    )
    if (rca_initial, rca_final, rca_override, rca_damage) != (16, 10, 36, 6):
        raise ValueError("RCA100 frozen result reconstruction differs")
    expected_obss = {
        "candidate-3": (49, 57),
        "candidate-4": (51, 57),
        "candidate-5": (45, 57),
        "pr21-tune": (51, 57),
        "pr21-regression": (95, 112),
    }
    for fixture, expected in expected_obss.items():
        subset = [item for item in obss if item.unified.fixture == fixture]
        actual = (
            sum(item.unified.initial_correct_exact for item in subset),
            sum(item.unified.m3_correct_exact for item in subset),
        )
        if actual != expected:
            raise ValueError(f"{fixture} frozen result reconstruction differs")
    frontier_cases = [item.unified.to_frontier_case() for item in cases]
    outcomes = {
        option: tuple(apply_option(case, option, frontier) for case in frontier_cases)
        for option in ArchitectureOption
    }
    if sum(item.initial_exact_correct for item in outcomes[ArchitectureOption.A0][:103]) != 16:
        raise ValueError("A0 does not preserve the frozen RCA100 Initial vector")
    robustness = grouped_robustness(
        frontier_cases,
        {option: outcomes[option] for option in ArchitectureOption},
    )
    option_aggregates: dict[str, dict[str, int | float]] = {}
    fixture_aggregates: dict[str, dict[str, dict[str, int | float]]] = {}
    for option, values in outcomes.items():
        combined = dict(aggregate_outcomes(values))
        rca_values = values[:103]
        rca_aggregate = aggregate_outcomes(rca_values)
        combined.update(
            {
                "rca100_initial": int(rca_aggregate["initial_exact_correct"]),
                "rca100_final": int(rca_aggregate["final_exact_correct"]),
                "rca100_rescue": int(rca_aggregate["root_rescue"]),
                "rca100_damage": int(rca_aggregate["root_damage"]),
                "rca100_net_rescue": int(rca_aggregate["root_net_rescue"]),
            }
        )
        option_aggregates[option.value] = combined
        fixture_aggregates[option.value] = {}
        for fixture in expected_obss:
            fixture_aggregates[option.value][fixture] = aggregate_outcomes(
                [
                    outcome
                    for adapted, outcome in zip(cases, values, strict=True)
                    if adapted.unified.fixture == fixture
                ]
            )
    causal_selection_input, causal_public = _causal_agent_audit(
        cases, outcomes, frontier
    )
    selected = select_architecture(
        option_aggregates=option_aggregates,
        fixture_aggregates=fixture_aggregates,
        robustness=robustness,
        causal_agent=causal_selection_input,
        frontier=frontier,
    )
    if selected is ArchitectureOption.A1:
        raise ValueError("historical A1 is not selectable")

    results_root = private_root / (
        lifecycle["results"] if lifecycle is not None else "results"
    )
    write_jsonl_create_once(
        results_root / "unified-case-records.jsonl",
        [item.unified.private_record() for item in cases],
    )
    write_jsonl_create_once(
        results_root / "entity-hierarchy-by-case.jsonl",
        [dict(item.hierarchy_record) for item in cases],
    )
    write_jsonl_create_once(
        results_root / "propagation-role-by-case.jsonl",
        [dict(item.propagation_record) for item in cases],
    )
    write_jsonl_create_once(
        results_root / "evidence-visibility-by-case.jsonl",
        [dict(item.visibility_record) for item in cases],
    )
    write_jsonl_create_once(
        results_root / "strong-single-failure-by-case.jsonl",
        [
            {
                "schema_version": "rca-crossbenchmark.strong-single-failure.v1",
                "private_case_key": item.unified.private_case_key,
                "classification": classify_strong_single_failure(item.unified),
                "evidence_sufficiency": evidence_sufficiency(item.unified),
            }
            for item in cases
        ],
    )
    write_jsonl_create_once(
        results_root / "m3-failure-by-case.jsonl",
        [
            {
                "schema_version": "rca-crossbenchmark.m3-failure.v1",
                "private_case_key": item.unified.private_case_key,
                "classification": classify_m3_failure(item.unified),
                "initial_correct": item.unified.initial_correct_exact,
                "historical_m3_correct": item.unified.m3_correct_exact,
            }
            for item in cases
        ],
    )
    frontier_records: list[Mapping[str, object]] = []
    for index, item in enumerate(cases):
        frontier_records.append(
            {
                "schema_version": "rca-crossbenchmark.architecture-frontier-case.v1",
                "private_case_key": item.unified.private_case_key,
                "fixture": item.unified.fixture,
                "outcomes": {
                    option.value: {
                        "final_entity": outcomes[option][index].final_entity,
                        "root_provenance": outcomes[option][index].root_provenance.value,
                        "decision_reason": outcomes[option][index].decision_reason,
                        "override": outcomes[option][index].override,
                        "initial_exact_correct": outcomes[option][index].initial_exact_correct,
                        "final_exact_correct": outcomes[option][index].final_exact_correct,
                        "initial_service_correct": outcomes[option][index].initial_service_correct,
                        "final_service_correct": outcomes[option][index].final_service_correct,
                        "initial_pair_correct": outcomes[option][index].initial_pair_correct,
                        "final_pair_correct": outcomes[option][index].final_pair_correct,
                    }
                    for option in ArchitectureOption
                },
            }
        )
    write_jsonl_create_once(
        results_root / "architecture-frontier-by-case.jsonl", frontier_records
    )
    fold_records = [
        {
            "option": item.option.value,
            "axis": item.axis,
            "held_out_group": item.held_out_group,
            "denominator": item.denominator,
            "rescue": item.rescue,
            "damage": item.damage,
            "net_rescue": item.net_rescue,
        }
        for item in robustness
    ]
    write_json_create_once(
        results_root / "robustness-folds.json",
        {
            "schema_version": "rca-crossbenchmark.robustness-folds.v1",
            "records": fold_records,
        },
    )

    entity_report = _entity_report(rca)
    propagation_report = _propagation_visibility_report(rca, obss)
    frontier_report = _frontier_report(
        cases,
        outcomes,
        fixture_aggregates,
        robustness,
        causal_public,
        selected,
        frontier,
    )
    if corrected:
        correction = _correction_disclosure(corrected_version)
        entity_report["correction_disclosure"] = correction
        propagation_report["correction_disclosure"] = correction
        frontier_report["correction_disclosure"] = correction
    decision_input = {
        "schema_version": "rca-crossbenchmark.decision-input.v1",
        "classification": list(CLASSIFICATION),
        "input_frontier_lock_sha256": sha256_file(input_lock_path),
        "attribution_methods_lock_sha256": sha256_file(methods_lock_path),
        "option_aggregates": option_aggregates,
        "fixture_aggregates": fixture_aggregates,
        "causal_agent": causal_selection_input,
        "selected_option": selected.value,
        "selected_name": _DECISION_NAMES[selected],
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "semantic_operations": 0,
    }
    write_json_create_once(results_root / "decision-input.json", decision_input)

    analysis_root = PROJECT_ROOT / "docs/analysis"
    entity_json = analysis_root / "rca100-entity-hierarchy-attribution.json"
    entity_md = analysis_root / "rca100-entity-hierarchy-attribution.md"
    propagation_json = (
        analysis_root / "rca100-propagation-visibility-attribution.json"
    )
    propagation_md = (
        analysis_root / "rca100-propagation-visibility-attribution.md"
    )
    frontier_json = analysis_root / "rca-crossbenchmark-architecture-frontier.json"
    frontier_md = analysis_root / "rca-crossbenchmark-architecture-frontier.md"
    write_public_json(entity_json, entity_report)
    write_public_text(
        entity_md, _markdown_json_report("RCA100 Entity Hierarchy Attribution", entity_report)
    )
    write_public_json(propagation_json, propagation_report)
    write_public_text(
        propagation_md,
        _markdown_json_report(
            "RCA100 Propagation and Visibility Attribution", propagation_report
        ),
    )
    write_public_json(frontier_json, frontier_report)
    write_public_text(
        frontier_md,
        _markdown_json_report(
            "RCA Cross-Benchmark Architecture Frontier", frontier_report
        ),
    )
    analysis_public_paths = (
        entity_json,
        entity_md,
        propagation_json,
        propagation_md,
        frontier_json,
        frontier_md,
    )
    analysis_private_paths = tuple(
        results_root / name
        for name in (
            "unified-case-records.jsonl",
            "entity-hierarchy-by-case.jsonl",
            "propagation-role-by-case.jsonl",
            "evidence-visibility-by-case.jsonl",
            "strong-single-failure-by-case.jsonl",
            "m3-failure-by-case.jsonl",
            "architecture-frontier-by-case.jsonl",
            "robustness-folds.json",
            "decision-input.json",
        )
    )
    created_at = utc_now()
    analysis_lock_path = private_root / "locks" / (
        lifecycle["analysis_lock"]
        if lifecycle is not None
        else "attribution-complete-lock.json"
    )
    if corrected and implementation_lock_path is None:
        raise ValueError("corrected implementation lock path is unavailable")
    analysis_predecessor_path = (
        implementation_lock_path if corrected else methods_lock_path
    )
    assert analysis_predecessor_path is not None
    write_json_create_once(
        analysis_lock_path,
        {
            "schema_version": "rca-crossbenchmark.attribution-complete-lock.v1",
            "created_at_utc": created_at,
            "classification": list(CLASSIFICATION),
            "previous_lock_sha256": sha256_file(analysis_predecessor_path),
            "public_outputs": {
                path.name: sha256_file(path) for path in analysis_public_paths
            },
            "private_outputs": {
                path.name: sha256_file(path) for path in analysis_private_paths
            },
            "unified_denominator": len(cases),
            "selected_option": selected.value,
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "semantic_operations": 0,
            "new_external_data_accessed": False,
            "re2_tt_accessed": False,
        },
    )
    analysis_state_name = (
        lifecycle["analysis_state"]
        if lifecycle is not None
        else "ATTRIBUTION_COMPLETE"
    )
    analysis_state_path = private_root / "state" / f"{analysis_state_name}.json"
    write_json_create_once(
        analysis_state_path,
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": analysis_state_name,
            "created_at_utc": created_at,
            "previous_state": (
                lifecycle["implementation_state"]
                if lifecycle is not None
                else "ATTRIBUTION_METHODS_FROZEN"
            ),
            "previous_state_record_sha256": sha256_file(
                implementation_state_path if corrected else methods_state_path
            ),
            "lock_sha256": sha256_file(analysis_lock_path),
        },
    )

    design_root = PROJECT_ROOT / "docs/design"
    decision_path = design_root / "rca-crossbenchmark-architecture-decision.md"
    spec_path = design_root / "unified-hierarchical-rca-v1-spec.md"
    write_public_text(
        decision_path,
        _decision_markdown(selected, entity_report, propagation_report, frontier_report),
    )
    write_public_text(spec_path, _spec_markdown(selected, frontier))
    decision_lock_path = private_root / "locks" / (
        lifecycle["decision_lock"]
        if lifecycle is not None
        else "architecture-decision-lock.json"
    )
    decision_created_at = utc_now()
    write_json_create_once(
        decision_lock_path,
        {
            "schema_version": "rca-crossbenchmark.architecture-decision-lock.v1",
            "created_at_utc": decision_created_at,
            "classification": list(CLASSIFICATION),
            "previous_lock_sha256": sha256_file(analysis_lock_path),
            "selected_option": selected.value,
            "decision_name": _DECISION_NAMES[selected],
            "decision_input_sha256": sha256_file(results_root / "decision-input.json"),
            "decision_record_sha256": sha256_file(decision_path),
            "implementation_spec_sha256": sha256_file(spec_path),
            "frontier_report_sha256": sha256_file(frontier_json),
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "semantic_operations": 0,
        },
    )
    write_json_create_once(
        private_root
        / "state"
        / (
            f"{lifecycle['decision_state']}.json"
            if lifecycle is not None
            else "ARCHITECTURE_DECISION_FROZEN.json"
        ),
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": (
                lifecycle["decision_state"]
                if lifecycle is not None
                else "ARCHITECTURE_DECISION_FROZEN"
            ),
            "created_at_utc": decision_created_at,
            "previous_state": analysis_state_name,
            "previous_state_record_sha256": sha256_file(analysis_state_path),
            "lock_sha256": sha256_file(decision_lock_path),
        },
    )
    print(f"[decision] {selected.value} {_DECISION_NAMES[selected]}", flush=True)
    return 0


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("JSONL input must be a regular file")
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("JSONL record must be an object")
            records.append(value)
    return tuple(records)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"private {name} must be an object")
    return value


def _unified_from_private(record: Mapping[str, Any]) -> UnifiedRCACase:
    truth = _mapping(record.get("ground_truth"), "ground truth")
    initial = _mapping(record.get("initial"), "initial")
    historical = _mapping(record.get("historical_m3"), "historical M3")
    hierarchy_path = _mapping(initial.get("hierarchy_path"), "initial hierarchy")
    raw_visibility = _mapping(record.get("visibility"), "visibility")
    raw_candidates = record.get("metrics_candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("private Metrics candidates must be a list")
    candidates = tuple(
        UnifiedMetricCandidate(
            entity=str(item["entity"]),
            service_ancestor=(
                None if item.get("service") is None else str(item["service"])
            ),
            layer=CanonicalEntityLayer(str(item["layer"])),
            rank=int(item["rank"]),
            score=float(item["score"]),
            metric_family=str(item["metric_family"]),
            first_anomaly_time=(
                None
                if item.get("first_anomaly_time") is None
                else float(item["first_anomaly_time"])
            ),
            source_support=int(item["source_support"]),
            relation_to_symptom=str(item["relation_to_symptom"]),
        )
        for raw_item in raw_candidates
        for item in (_mapping(raw_item, "Metrics candidate"),)
    )
    equivalent = truth.get("equivalent_entities")
    parents = hierarchy_path.get("explicit_parents")
    supporting_refs = initial.get("supporting_evidence_refs")
    if not isinstance(equivalent, list) or not isinstance(parents, list):
        raise ValueError("private hierarchy/equivalence list differs")
    if not isinstance(supporting_refs, list):
        raise ValueError("private Initial evidence refs must be a list")
    return UnifiedRCACase(
        private_case_key=str(record["private_case_key"]),
        fixture=str(record["fixture"]),
        benchmark=str(record["benchmark"]),
        system=str(record["system"]),
        fault_family=str(record["fault_family"]),
        fault_type_truth=str(record["fault_type_truth"]),
        fault_type_raw=str(record["fault_type_raw"]),
        fault_regime=FaultOntologyClass(str(record["fault_regime"])),
        ground_truth_fault_regime=FaultOntologyClass(
            str(truth["fault_regime"])
        ),
        metric_family=str(record["metric_family"]),
        ground_truth_entity=str(truth["entity"]),
        ground_truth_equivalent_entities=frozenset(str(item) for item in equivalent),
        ground_truth_layer=CanonicalEntityLayer(str(truth["layer"])),
        ground_truth_service=(
            None if truth.get("service") is None else str(truth["service"])
        ),
        ground_truth_workload=(
            None if truth.get("workload") is None else str(truth["workload"])
        ),
        ground_truth_node=(
            None if truth.get("node") is None else str(truth["node"])
        ),
        initial_entity=str(initial["entity"]),
        initial_layer=CanonicalEntityLayer(str(initial["layer"])),
        initial_hierarchy_path=EntityHierarchyPath(
            entity=str(hierarchy_path["entity"]),
            explicit_parents=tuple(str(item) for item in parents),
            service_ancestor_or_none=(
                None
                if hierarchy_path.get("service_ancestor") is None
                else str(hierarchy_path["service_ancestor"])
            ),
            infrastructure_ancestor_or_none=(
                None
                if hierarchy_path.get("infrastructure_ancestor") is None
                else str(hierarchy_path["infrastructure_ancestor"])
            ),
        ),
        initial_supporting_evidence_refs=tuple(
            str(item) for item in supporting_refs
        ),
        initial_service=(
            None if initial.get("service") is None else str(initial["service"])
        ),
        initial_correct_exact=bool(initial["exact_correct"]),
        initial_correct_service=bool(initial["service_correct"]),
        initial_pair_correct=bool(initial["pair_correct"]),
        initial_relation=str(initial["relation"]),
        m3_action=(
            None if historical.get("action") is None else str(historical["action"])
        ),
        m3_final_entity=str(historical["entity"]),
        m3_final_layer=CanonicalEntityLayer(str(historical["layer"])),
        m3_final_service=(
            None
            if historical.get("service") is None
            else str(historical["service"])
        ),
        m3_correct_exact=bool(historical["exact_correct"]),
        m3_correct_service=bool(historical["service_correct"]),
        m3_pair_correct=bool(historical["pair_correct"]),
        m3_relation=str(historical["relation"]),
        metrics_candidates=candidates,
        metrics_initial_rank=(
            None
            if record.get("metrics_initial_rank") is None
            else int(record["metrics_initial_rank"])
        ),
        metrics_margin=(
            None
            if record.get("metrics_margin") is None
            else float(record["metrics_margin"])
        ),
        metrics_top1_is_downstream=bool(record["metrics_top1_is_downstream"]),
        propagation_disposition=PropagationDisposition(
            str(record["propagation_disposition"])
        ),
        visibility=EvidenceVisibilitySummary(
            catalog_entities=frozenset(str(item) for item in raw_visibility["catalog"]),
            metrics_entities=frozenset(str(item) for item in raw_visibility["metrics"]),
            logs_entities=frozenset(str(item) for item in raw_visibility["logs"]),
            traces_entities=frozenset(str(item) for item in raw_visibility["traces"]),
            events_entities=frozenset(str(item) for item in raw_visibility["events"]),
            alerts_entities=frozenset(str(item) for item in raw_visibility["alerts"]),
            topology_entities=frozenset(
                str(item) for item in raw_visibility["topology"]
            ),
        ),
        causal_visible_entities=frozenset(
            str(item) for item in raw_visibility["causal"]
        ),
        alert_entity=(
            None
            if record.get("alert_entity") is None
            else str(record["alert_entity"])
        ),
        terminal_failure=bool(record["terminal_failure"]),
    )


def _adapted_from_private(results_root: Path) -> tuple[AdaptedCase, ...]:
    unified = _read_jsonl(results_root / "unified-case-records.jsonl")
    hierarchy = _read_jsonl(results_root / "entity-hierarchy-by-case.jsonl")
    propagation = _read_jsonl(results_root / "propagation-role-by-case.jsonl")
    visibility = _read_jsonl(results_root / "evidence-visibility-by-case.jsonl")
    if not (len(unified) == len(hierarchy) == len(propagation) == len(visibility) == 463):
        raise ValueError("corrected private vectors must align at 463 records")
    output: list[AdaptedCase] = []
    for raw_case, raw_hierarchy, raw_propagation, raw_visibility in zip(
        unified, hierarchy, propagation, visibility, strict=True
    ):
        keys = {
            raw_case.get("private_case_key"),
            raw_hierarchy.get("private_case_key"),
            raw_propagation.get("private_case_key"),
            raw_visibility.get("private_case_key"),
        }
        if len(keys) != 1:
            raise ValueError("corrected private vector identities differ")
        output.append(
            AdaptedCase(
                unified=_unified_from_private(raw_case),
                hierarchy_record=raw_hierarchy,
                propagation_record=raw_propagation,
                visibility_record=raw_visibility,
            )
        )
    return tuple(output)


def _replay_dataset_summary(
    pairs: Sequence[tuple[bool, bool]],
) -> dict[str, object]:
    denominator = len(pairs)
    initial = sum(before for before, _ in pairs)
    final = sum(after for _, after in pairs)
    rescue = sum(not before and after for before, after in pairs)
    damage = sum(before and not after for before, after in pairs)
    return {
        "denominator": denominator,
        "exact_initial": rate(initial, denominator),
        "exact_final": rate(final, denominator),
        "root_rescue": rate(rescue, denominator - initial),
        "root_damage": rate(damage, initial),
        "root_net_rescue": rescue - damage,
    }


def _replay_markdown(payload: Mapping[str, object]) -> str:
    return _markdown_json_report(
        "Unified Hierarchical RCA v1 Offline Replay", payload
    )


def _human_brief(payload: Mapping[str, object]) -> str:
    datasets = payload.get("datasets")
    if not isinstance(datasets, Mapping):
        raise ValueError("replay report datasets are missing")
    rca = datasets.get("RCA100")
    if not isinstance(rca, Mapping):
        raise ValueError("replay report RCA100 aggregate is missing")
    initial = rca.get("exact_initial")
    final = rca.get("exact_final")
    if not isinstance(initial, Mapping) or not isinstance(final, Mapping):
        raise ValueError("replay report RCA100 rates are missing")
    correction_note = (
        "本文件是对先前无效 GT-derived / 未冻结分析尝试的 append-only 修正版；"
        "A0–A5 阈值未改变。\n\n"
        if "correction_disclosure" in payload
        else ""
    )
    return f"""# Unified Hierarchical RCA v1 Human Brief

## 结论

{correction_note}
冻结决策为 `A0 / STRONG_SINGLE_HIERARCHICAL`。实现对全部 463 条 consumed-development 记录完成离线重放，与 Phase G counterfactual 逐条一致；RCA100 为 `{initial.get('numerator')}/{initial.get('denominator')}` → `{final.get('numerator')}/{final.get('denominator')}`，无 override、无 Root Damage、无 Root Rescue。

## 为什么选择 A0

A2 在 RCA100 产生负净收益且 Damage Rate 超限；A3/A4 没有在 RCA100 产生正净收益；A5 的非冗余 eligible coverage 与 RCA100 oracle 净收益不足。冻结规则因此要求回退 A0，而不是继续追加实验。

## 实现边界

Runtime 输出 canonical entity layer、typed fault ontology、root provenance 与 decision reason；Final Root 永远保持 Strong Single Initial Root。未保留 Metrics 或 Agent override，也未进行 Provider 构造、调用、新数据访问或 RE2-TT 访问。

## 证据等级

这是 consumed cross-benchmark development 的 post-hoc attribution 与 offline replay，不是 external validation，也不是 primary inference。下一步若获单独授权，应是一次有界 live development evaluation，而不是新的 attribution candidate。
"""


def replay(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    private_root = validate_private_root(args.private_root, create=False)
    corrected_version = _corrected_version(args)
    corrected = corrected_version > 0
    lifecycle = (
        _corrected_lifecycle(corrected_version) if corrected else None
    )
    input_lock, input_lock_path, _ = _verify_state_binding(
        private_root,
        state_name="INPUTS_AND_FRONTIER_FROZEN",
        lock_name="input-and-frontier-lock.json",
    )
    methods_lock, methods_lock_path, _ = _verify_state_binding(
        private_root,
        state_name="ATTRIBUTION_METHODS_FROZEN",
        lock_name="attribution-methods-lock.json",
    )
    analysis_lock, analysis_lock_path, _ = _verify_state_binding(
        private_root,
        state_name=(
            lifecycle["analysis_state"]
            if lifecycle is not None
            else "ATTRIBUTION_COMPLETE"
        ),
        lock_name=(
            lifecycle["analysis_lock"]
            if lifecycle is not None
            else "attribution-complete-lock.json"
        ),
    )
    decision_lock, decision_lock_path, decision_state_path = _verify_state_binding(
        private_root,
        state_name=(
            lifecycle["decision_state"]
            if lifecycle is not None
            else "ARCHITECTURE_DECISION_FROZEN"
        ),
        lock_name=(
            lifecycle["decision_lock"]
            if lifecycle is not None
            else "architecture-decision-lock.json"
        ),
    )
    if methods_lock.get("input_frontier_lock_sha256") != sha256_file(input_lock_path):
        raise ValueError("methodology lock is not bound to frozen inputs")
    if corrected:
        assert lifecycle is not None
        implementation_lock, implementation_lock_path, _ = _verify_state_binding(
            private_root,
            state_name=lifecycle["implementation_state"],
            lock_name=lifecycle["implementation_lock"],
        )
        _verify_corrected_implementation(implementation_lock)
        expected_analysis_predecessor = sha256_file(implementation_lock_path)
    else:
        expected_analysis_predecessor = sha256_file(methods_lock_path)
    if analysis_lock.get("previous_lock_sha256") != expected_analysis_predecessor:
        raise ValueError("analysis lock predecessor differs")
    if decision_lock.get("previous_lock_sha256") != sha256_file(analysis_lock_path):
        raise ValueError("decision lock is not bound to frozen attribution")
    if (
        decision_lock.get("selected_option") != "A0"
        or decision_lock.get("decision_name") != "STRONG_SINGLE_HIERARCHICAL"
    ):
        raise ValueError("runtime implementation differs from frozen decision")
    frontier_path = _safe_path(args.frontier)
    methodology_path = _safe_path(args.methodology)
    if methods_lock.get("frontier_sha256") != sha256_file(frontier_path):
        raise ValueError("frontier drifted before implementation replay")
    if methods_lock.get("methodology_sha256") != sha256_file(methodology_path):
        raise ValueError("methodology drifted before implementation replay")
    print("[integrity] fresh replay input hashing", flush=True)
    _verify_frozen_inputs(args, input_lock)
    methodology = read_object(methodology_path)
    _validate_methodology(methodology)
    print("[replay adapter] RCA100", flush=True)
    rca = load_rca100_cases(
        cases_root=_safe_path(args.rca_input_root),
        terminals_root=_safe_path(args.rca_terminal_root),
        schedule_path=_safe_path(args.rca_schedule),
        answer_root=_safe_path(args.rca_answer_root),
        methodology=methodology,
        progress=_progress,
    )
    print("[replay adapter] OB/SS", flush=True)
    obss = load_obss_cases(
        candidate_3_root=_safe_path(args.candidate_3_root),
        candidate_4_root=_safe_path(args.candidate_4_root),
        candidate_5_root=_safe_path(args.candidate_5_root),
        tune_root=_safe_path(args.tune_root),
        regression_root=_safe_path(args.regression_root),
        ob_root=_safe_path(args.ob_root),
        ss_root=_safe_path(args.ss_root),
        indicator_config_path=_safe_path(args.indicator_config),
        indicator_config_sha256=args.indicator_config_sha256,
        methodology=methodology,
        progress=_progress,
    )
    cases = rca + obss
    if len(cases) != 463:
        raise ValueError("implementation replay denominator differs")
    frontier_records_path = (
        private_root
        / (lifecycle["results"] if lifecycle is not None else "results")
        / "architecture-frontier-by-case.jsonl"
    )
    frontier_records = _read_jsonl(frontier_records_path)
    if len(frontier_records) != len(cases):
        raise ValueError("frozen frontier vector denominator differs")
    from ecomsre_rca_unified.runtime import (  # noqa: PLC0415
        EVALUATION_VERSION,
        StrongSingleHierarchicalInput,
        execute_unified_hierarchical_rca,
    )

    by_fixture: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    exact_matches = 0
    for adapted, expected in zip(cases, frontier_records, strict=True):
        case = adapted.unified
        if expected.get("private_case_key") != case.private_case_key:
            raise ValueError("implementation replay case order/identity differs")
        raw_outcomes = expected.get("outcomes")
        if not isinstance(raw_outcomes, Mapping):
            raise ValueError("frozen frontier case outcomes are missing")
        expected_a0 = raw_outcomes.get("A0")
        if not isinstance(expected_a0, Mapping):
            raise ValueError("frozen A0 case outcome is missing")
        runtime_input = StrongSingleHierarchicalInput(
            initial_root=case.initial_entity,
            initial_layer=case.initial_layer,
            initial_hierarchy_path=case.initial_hierarchy_path,
            fault_type_raw=case.fault_type_raw,
            fault_ontology_class=case.fault_regime,
            evidence_visibility=case.visibility,
            supporting_evidence_refs=case.initial_supporting_evidence_refs,
        )
        actual = execute_unified_hierarchical_rca(runtime_input)
        final_correct = actual.final_root in case.ground_truth_equivalent_entities
        matches = bool(
            actual.evaluation_version == EVALUATION_VERSION
            and actual.initial_root == case.initial_entity
            and actual.final_root == expected_a0.get("final_entity")
            and actual.root_provenance.value
            == expected_a0.get("root_provenance")
            and final_correct == expected_a0.get("final_exact_correct")
            and actual.initial_layer == case.initial_layer
            and actual.final_layer == case.initial_layer
            and actual.fault_type_raw == case.fault_type_raw
            and actual.fault_ontology_class == case.fault_regime
            and actual.supporting_evidence_refs
            == case.initial_supporting_evidence_refs
        )
        if not matches:
            raise ValueError("BLOCKED_ARCHITECTURE_IMPLEMENTATION_REPLAY_MISMATCH")
        exact_matches += 1
        by_fixture[case.fixture].append((case.initial_correct_exact, final_correct))
    if exact_matches != 463:
        raise ValueError("BLOCKED_ARCHITECTURE_IMPLEMENTATION_REPLAY_MISMATCH")
    ordered_fixtures = (
        "RCA100",
        "candidate-3",
        "candidate-4",
        "candidate-5",
        "pr21-tune",
        "pr21-regression",
    )
    report = {
        "schema_version": "unified-hierarchical-rca-v1.offline-replay.v1",
        "evaluation_version": EVALUATION_VERSION,
        "classification": list(CLASSIFICATION),
        "selected_option": "A0",
        "decision_name": "STRONG_SINGLE_HIERARCHICAL",
        "datasets": {
            fixture: _replay_dataset_summary(by_fixture[fixture])
            for fixture in ordered_fixtures
        },
        "implementation_counterfactual_exact_match": True,
        "exact_match": rate(exact_matches, len(cases)),
        "root_provenance": "MODEL_INITIAL",
        "fault_ontology": "TYPED_DETERMINISTIC",
        "arbitration": "NO_OVERRIDE",
        "communication_envelope": "NOT_APPLICABLE_A0",
        "fusion": "KEEP_INITIAL",
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "semantic_operations": 0,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
    }
    if corrected:
        report["correction_disclosure"] = _correction_disclosure(
            corrected_version
        )
    results_root = PROJECT_ROOT / "docs/results"
    json_path = results_root / "unified-hierarchical-rca-v1-offline-replay.json"
    markdown_path = results_root / "unified-hierarchical-rca-v1-offline-replay.md"
    brief_path = results_root / "unified-hierarchical-rca-v1-human-brief.md"
    write_public_json(json_path, report)
    write_public_text(markdown_path, _replay_markdown(report))
    write_public_text(brief_path, _human_brief(report))
    runtime_path = PROJECT_ROOT / "src/ecomsre_rca_unified/runtime.py"
    created_at = utc_now()
    replay_lock_path = private_root / "locks" / (
        lifecycle["replay_lock"]
        if lifecycle is not None
        else "implementation-replay-lock.json"
    )
    write_json_create_once(
        replay_lock_path,
        {
            "schema_version": "rca-crossbenchmark.implementation-replay-lock.v1",
            "created_at_utc": created_at,
            "classification": list(CLASSIFICATION),
            "previous_lock_sha256": sha256_file(decision_lock_path),
            "runtime_sha256": sha256_file(runtime_path),
            "frontier_case_vector_sha256": sha256_file(frontier_records_path),
            "public_outputs": {
                path.name: sha256_file(path)
                for path in (json_path, markdown_path, brief_path)
            },
            "denominator": len(cases),
            "exact_match_count": exact_matches,
            "selected_option": "A0",
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "semantic_operations": 0,
            "re2_tt_accessed": False,
            "new_external_data_accessed": False,
        },
    )
    write_json_create_once(
        private_root
        / "state"
        / (
            f"{lifecycle['replay_state']}.json"
            if lifecycle is not None
            else "IMPLEMENTATION_REPLAY_FROZEN.json"
        ),
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": (
                lifecycle["replay_state"]
                if lifecycle is not None
                else "IMPLEMENTATION_REPLAY_FROZEN"
            ),
            "created_at_utc": created_at,
            "previous_state": (
                lifecycle["decision_state"]
                if lifecycle is not None
                else "ARCHITECTURE_DECISION_FROZEN"
            ),
            "previous_state_record_sha256": sha256_file(decision_state_path),
            "lock_sha256": sha256_file(replay_lock_path),
        },
    )
    print("[replay] A0 exact match 463/463", flush=True)
    return 0


def _canonical_replay_from_private(
    private_root: Path,
    *,
    corrected: bool = False,
    corrected_version: int = 0,
) -> dict[str, object]:
    if corrected and corrected_version == 0:
        corrected_version = 2
    lifecycle = (
        _corrected_lifecycle(corrected_version)
        if corrected_version > 0
        else None
    )
    results_name = lifecycle["results"] if lifecycle is not None else "results"
    unified_records = _read_jsonl(
        private_root / results_name / "unified-case-records.jsonl"
    )
    frontier_records = _read_jsonl(
        private_root / results_name / "architecture-frontier-by-case.jsonl"
    )
    if len(unified_records) != 463 or len(frontier_records) != 463:
        raise ValueError("private replay vectors must each contain 463 records")
    by_fixture: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    exact_matches = 0
    for unified, frontier_record in zip(
        unified_records, frontier_records, strict=True
    ):
        if unified.get("private_case_key") != frontier_record.get("private_case_key"):
            raise ValueError("private replay vectors are misaligned")
        raw_initial = unified.get("initial")
        raw_outcomes = frontier_record.get("outcomes")
        if not isinstance(raw_initial, Mapping) or not isinstance(
            raw_outcomes, Mapping
        ):
            raise ValueError("private replay vector schema differs")
        a0 = raw_outcomes.get("A0")
        fixture = unified.get("fixture")
        if not isinstance(a0, Mapping) or not isinstance(fixture, str):
            raise ValueError("private A0 replay record schema differs")
        initial_correct = raw_initial.get("exact_correct")
        final_correct = a0.get("final_exact_correct")
        initial_root = raw_initial.get("entity")
        if type(initial_correct) is not bool or type(final_correct) is not bool:
            raise ValueError("private replay correctness field differs")
        matches = bool(
            a0.get("final_entity") == initial_root
            and a0.get("root_provenance") == "MODEL_INITIAL"
            and a0.get("override") is False
            and final_correct == initial_correct
        )
        if not matches:
            raise ValueError("BLOCKED_ARCHITECTURE_IMPLEMENTATION_REPLAY_MISMATCH")
        exact_matches += 1
        by_fixture[fixture].append((initial_correct, final_correct))
    ordered_fixtures = (
        "RCA100",
        "candidate-3",
        "candidate-4",
        "candidate-5",
        "pr21-tune",
        "pr21-regression",
    )
    report: dict[str, object] = {
        "schema_version": "unified-hierarchical-rca-v1.offline-replay.v1",
        "evaluation_version": "unified-hierarchical-rca-v1",
        "classification": list(CLASSIFICATION),
        "selected_option": "A0",
        "decision_name": "STRONG_SINGLE_HIERARCHICAL",
        "datasets": {
            fixture: _replay_dataset_summary(by_fixture[fixture])
            for fixture in ordered_fixtures
        },
        "implementation_counterfactual_exact_match": True,
        "exact_match": rate(exact_matches, len(unified_records)),
        "root_provenance": "MODEL_INITIAL",
        "fault_ontology": "TYPED_DETERMINISTIC",
        "arbitration": "NO_OVERRIDE",
        "communication_envelope": "NOT_APPLICABLE_A0",
        "fusion": "KEEP_INITIAL",
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "semantic_operations": 0,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
    }
    if corrected_version > 0:
        report["correction_disclosure"] = _correction_disclosure(
            corrected_version
        )
    return report


def _canonical_corrected_analysis(
    private_root: Path, frontier_path: Path, *, corrected_version: int = 2
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    lifecycle = _corrected_lifecycle(corrected_version)
    cases = _adapted_from_private(private_root / lifecycle["results"])
    rca = cases[:103]
    obss = cases[103:]
    if any(item.unified.fixture != "RCA100" for item in rca):
        raise ValueError("corrected private RCA100 partition differs")
    frontier = load_frontier(frontier_path)
    frontier_cases = [item.unified.to_frontier_case() for item in cases]
    outcomes = {
        option: tuple(apply_option(case, option, frontier) for case in frontier_cases)
        for option in ArchitectureOption
    }
    robustness = grouped_robustness(frontier_cases, outcomes)
    fixture_names = (
        "candidate-3",
        "candidate-4",
        "candidate-5",
        "pr21-tune",
        "pr21-regression",
    )
    option_aggregates: dict[str, dict[str, int | float]] = {}
    fixture_aggregates: dict[str, dict[str, dict[str, int | float]]] = {}
    for option, values in outcomes.items():
        aggregate = dict(aggregate_outcomes(values))
        rca_aggregate = aggregate_outcomes(values[:103])
        aggregate.update(
            {
                "rca100_initial": int(rca_aggregate["initial_exact_correct"]),
                "rca100_final": int(rca_aggregate["final_exact_correct"]),
                "rca100_rescue": int(rca_aggregate["root_rescue"]),
                "rca100_damage": int(rca_aggregate["root_damage"]),
                "rca100_net_rescue": int(rca_aggregate["root_net_rescue"]),
            }
        )
        option_aggregates[option.value] = aggregate
        fixture_aggregates[option.value] = {
            fixture: aggregate_outcomes(
                [
                    outcome
                    for adapted, outcome in zip(cases, values, strict=True)
                    if adapted.unified.fixture == fixture
                ]
            )
            for fixture in fixture_names
        }
    causal_input, causal_public = _causal_agent_audit(cases, outcomes, frontier)
    selected = select_architecture(
        option_aggregates=option_aggregates,
        fixture_aggregates=fixture_aggregates,
        robustness=robustness,
        causal_agent=causal_input,
        frontier=frontier,
    )
    if selected is not ArchitectureOption.A0:
        raise ValueError("corrected canonical selection differs from frozen A0")
    entity = _entity_report(rca)
    propagation = _propagation_visibility_report(rca, obss)
    frontier_report = _frontier_report(
        cases,
        outcomes,
        fixture_aggregates,
        robustness,
        causal_public,
        selected,
        frontier,
    )
    correction = _correction_disclosure(corrected_version)
    entity["correction_disclosure"] = correction
    propagation["correction_disclosure"] = correction
    frontier_report["correction_disclosure"] = correction
    return entity, propagation, frontier_report


def verify_public(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    private_root = validate_private_root(args.private_root, create=False)
    analysis_lock, _, _ = _verify_state_binding(
        private_root,
        state_name="ATTRIBUTION_COMPLETE",
        lock_name="attribution-complete-lock.json",
    )
    decision_lock, decision_lock_path, _ = _verify_state_binding(
        private_root,
        state_name="ARCHITECTURE_DECISION_FROZEN",
        lock_name="architecture-decision-lock.json",
    )
    replay_lock, replay_lock_path, replay_state_path = _verify_state_binding(
        private_root,
        state_name="IMPLEMENTATION_REPLAY_FROZEN",
        lock_name="implementation-replay-lock.json",
    )
    verification_lock_path = private_root / "locks/public-outputs-verification-lock.json"
    verification_state_path = private_root / "state/PUBLIC_OUTPUTS_VERIFIED.json"
    verification_lock: dict[str, Any] | None = None
    if verification_lock_path.exists() or verification_state_path.exists():
        verification_lock, _, _ = _verify_state_binding(
            private_root,
            state_name="PUBLIC_OUTPUTS_VERIFIED",
            lock_name="public-outputs-verification-lock.json",
        )
    format_lock_path = private_root / "locks/public-output-format-repair-lock.json"
    format_state_path = private_root / "state/PUBLIC_OUTPUTS_FORMAT_REPAIRED.json"
    format_lock: dict[str, Any] | None = None
    if format_lock_path.exists() or format_state_path.exists():
        format_lock, _, _ = _verify_state_binding(
            private_root,
            state_name="PUBLIC_OUTPUTS_FORMAT_REPAIRED",
            lock_name="public-output-format-repair-lock.json",
        )
        if verification_lock is None or format_lock.get(
            "previous_lock_sha256"
        ) != sha256_file(verification_lock_path):
            raise ValueError("format repair is not bound to public verification")
    if replay_lock.get("previous_lock_sha256") != sha256_file(decision_lock_path):
        raise ValueError("replay lock is not bound to frozen decision")
    analysis_root = PROJECT_ROOT / "docs/analysis"
    design_root = PROJECT_ROOT / "docs/design"
    results_root = PROJECT_ROOT / "docs/results"
    entity_json = analysis_root / "rca100-entity-hierarchy-attribution.json"
    entity_md = analysis_root / "rca100-entity-hierarchy-attribution.md"
    propagation_json = (
        analysis_root / "rca100-propagation-visibility-attribution.json"
    )
    propagation_md = (
        analysis_root / "rca100-propagation-visibility-attribution.md"
    )
    frontier_json = analysis_root / "rca-crossbenchmark-architecture-frontier.json"
    frontier_md = analysis_root / "rca-crossbenchmark-architecture-frontier.md"
    decision_path = design_root / "rca-crossbenchmark-architecture-decision.md"
    spec_path = design_root / "unified-hierarchical-rca-v1-spec.md"
    replay_json = results_root / "unified-hierarchical-rca-v1-offline-replay.json"
    replay_md = results_root / "unified-hierarchical-rca-v1-offline-replay.md"
    brief_path = results_root / "unified-hierarchical-rca-v1-human-brief.md"
    public_paths = (
        entity_json,
        entity_md,
        propagation_json,
        propagation_md,
        frontier_json,
        frontier_md,
        decision_path,
        spec_path,
        replay_json,
        replay_md,
        brief_path,
    )
    if any(path.is_symlink() or not path.is_file() for path in public_paths):
        raise ValueError("required public output is missing or is a symlink")
    raw_analysis_outputs = analysis_lock.get("public_outputs")
    if not isinstance(raw_analysis_outputs, Mapping):
        raise ValueError("analysis public-output binding is missing")
    for path in public_paths[:6]:
        if raw_analysis_outputs.get(path.name) != sha256_file(path):
            raise ValueError(f"analysis public output drifted: {path.name}")
    if (
        decision_lock.get("decision_record_sha256") != sha256_file(decision_path)
        or decision_lock.get("frontier_report_sha256") != sha256_file(frontier_json)
    ):
        raise ValueError("decision public output drifted")
    if format_lock is None:
        if decision_lock.get("implementation_spec_sha256") != sha256_file(spec_path):
            raise ValueError("implementation spec drifted from decision lock")
    elif (
        format_lock.get("old_sha256")
        != decision_lock.get("implementation_spec_sha256")
        or format_lock.get("new_sha256") != sha256_file(spec_path)
        or format_lock.get("semantic_change") is not False
    ):
        raise ValueError("implementation spec format-repair binding differs")
    raw_replay_outputs = replay_lock.get("public_outputs")
    if not isinstance(raw_replay_outputs, Mapping):
        raise ValueError("replay public-output binding is missing")
    for path in public_paths[8:]:
        if raw_replay_outputs.get(path.name) != sha256_file(path):
            raise ValueError(f"replay public output drifted: {path.name}")
    runtime_path = PROJECT_ROOT / "src/ecomsre_rca_unified/runtime.py"
    if replay_lock.get("runtime_sha256") != sha256_file(runtime_path):
        raise ValueError("selected runtime drifted after replay lock")

    entity_payload = read_object(entity_json)
    propagation_payload = read_object(propagation_json)
    frontier_payload = read_object(frontier_json)
    replay_payload = read_object(replay_json)
    for payload in (
        entity_payload,
        propagation_payload,
        frontier_payload,
        replay_payload,
    ):
        assert_public_payload(payload)
    expected_replay = _canonical_replay_from_private(private_root)
    if replay_payload != expected_replay:
        raise ValueError("public replay JSON differs from canonical private recomputation")
    frontier = load_frontier(_safe_path(args.frontier))
    expected_text = {
        entity_md: _markdown_json_report(
            "RCA100 Entity Hierarchy Attribution", entity_payload
        ),
        propagation_md: _markdown_json_report(
            "RCA100 Propagation and Visibility Attribution", propagation_payload
        ),
        frontier_md: _markdown_json_report(
            "RCA Cross-Benchmark Architecture Frontier", frontier_payload
        ),
        decision_path: _decision_markdown(
            ArchitectureOption.A0,
            entity_payload,
            propagation_payload,
            frontier_payload,
        ),
        spec_path: _spec_markdown(ArchitectureOption.A0, frontier),
        replay_md: _replay_markdown(expected_replay),
        brief_path: _human_brief(expected_replay),
    }
    for path, expected in expected_text.items():
        actual = path.read_text(encoding="utf-8")
        if actual != expected.rstrip() + "\n":
            raise ValueError(f"public text differs from canonical rendering: {path.name}")
        assert_public_text(actual)
    private_result_names = (
        "unified-case-records.jsonl",
        "entity-hierarchy-by-case.jsonl",
        "propagation-role-by-case.jsonl",
        "evidence-visibility-by-case.jsonl",
        "strong-single-failure-by-case.jsonl",
        "m3-failure-by-case.jsonl",
        "architecture-frontier-by-case.jsonl",
        "robustness-folds.json",
        "decision-input.json",
    )
    private_results = tuple(
        private_root / "results" / name for name in private_result_names
    )
    if any(
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_mode & 0o777 != 0o600
        for path in private_results
    ):
        raise ValueError("private result file boundary differs")
    for lock in (analysis_lock, decision_lock, replay_lock):
        if any(lock.get(key) != 0 for key in ("provider_calls", "semantic_operations")):
            raise ValueError("frozen lifecycle contains external operations")
    if verification_lock is None:
        created_at = utc_now()
        write_json_create_once(
            verification_lock_path,
            {
                "schema_version": "rca-crossbenchmark.public-verification-lock.v1",
                "created_at_utc": created_at,
                "classification": list(CLASSIFICATION),
                "previous_lock_sha256": sha256_file(replay_lock_path),
                "public_outputs": {
                    path.name: sha256_file(path) for path in public_paths
                },
                "private_outputs": {
                    path.name: sha256_file(path) for path in private_results
                },
                "canonical_replay_recomputed": True,
                "canonical_text_exact_match": True,
                "leakage_scan_passed": True,
                "provider_objects_constructed": 0,
                "provider_calls": 0,
                "semantic_operations": 0,
                "re2_tt_accessed": False,
                "new_external_data_accessed": False,
            },
        )
        write_json_create_once(
            verification_state_path,
            {
                "schema_version": "rca-crossbenchmark.state.v1",
                "state": "PUBLIC_OUTPUTS_VERIFIED",
                "created_at_utc": created_at,
                "previous_state": "IMPLEMENTATION_REPLAY_FROZEN",
                "previous_state_record_sha256": sha256_file(replay_state_path),
                "lock_sha256": sha256_file(verification_lock_path),
            },
        )
    print("[verify] 11 public outputs canonical/leakage PASS", flush=True)
    return 0


def repair_public_format(args: argparse.Namespace) -> int:
    """Apply the sole post-freeze whitespace repair without rewriting old locks."""

    assert_no_provider_environment()
    private_root = validate_private_root(args.private_root, create=False)
    decision_lock, _, _ = _verify_state_binding(
        private_root,
        state_name="ARCHITECTURE_DECISION_FROZEN",
        lock_name="architecture-decision-lock.json",
    )
    verification_lock, verification_lock_path, verification_state_path = (
        _verify_state_binding(
            private_root,
            state_name="PUBLIC_OUTPUTS_VERIFIED",
            lock_name="public-outputs-verification-lock.json",
        )
    )
    spec_path = PROJECT_ROOT / "docs/design/unified-hierarchical-rca-v1-spec.md"
    old_payload = spec_path.read_text(encoding="utf-8")
    old_sha256 = sha256_file(spec_path)
    if (
        decision_lock.get("implementation_spec_sha256") != old_sha256
        or not isinstance(verification_lock.get("public_outputs"), Mapping)
        or verification_lock["public_outputs"].get(spec_path.name) != old_sha256  # type: ignore[union-attr]
    ):
        raise ValueError("pre-repair spec is not bound to frozen public outputs")
    frontier = load_frontier(_safe_path(args.frontier))
    new_payload = _spec_markdown(ArchitectureOption.A0, frontier).rstrip() + "\n"
    if old_payload.replace("  \n", "\n") != new_payload:
        raise ValueError("format repair would change more than trailing Markdown spaces")
    write_public_text(spec_path, new_payload)
    new_sha256 = sha256_file(spec_path)
    if new_sha256 == old_sha256:
        raise ValueError("format repair did not change the bound output")
    created_at = utc_now()
    repair_lock_path = private_root / "locks/public-output-format-repair-lock.json"
    write_json_create_once(
        repair_lock_path,
        {
            "schema_version": "rca-crossbenchmark.public-format-repair-lock.v1",
            "created_at_utc": created_at,
            "classification": list(CLASSIFICATION),
            "previous_lock_sha256": sha256_file(verification_lock_path),
            "output_name": spec_path.name,
            "old_sha256": old_sha256,
            "new_sha256": new_sha256,
            "repair": "REMOVE_TRAILING_MARKDOWN_HARD_BREAK_SPACES",
            "semantic_change": False,
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "semantic_operations": 0,
        },
    )
    write_json_create_once(
        private_root / "state/PUBLIC_OUTPUTS_FORMAT_REPAIRED.json",
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": "PUBLIC_OUTPUTS_FORMAT_REPAIRED",
            "created_at_utc": created_at,
            "previous_state": "PUBLIC_OUTPUTS_VERIFIED",
            "previous_state_record_sha256": sha256_file(verification_state_path),
            "lock_sha256": sha256_file(repair_lock_path),
        },
    )
    print("[repair] public spec trailing-whitespace only PASS", flush=True)
    return 0


def _verify_corrected_v3_goal_coverage(
    propagation: Mapping[str, object], frontier: Mapping[str, object]
) -> None:
    visibility = _mapping(propagation.get("visibility"), "visibility report")
    per_source = _mapping(
        visibility.get("per_source"), "per-source visibility report"
    )
    required_sources = {
        "metrics",
        "logs",
        "traces",
        "events",
        "alerts",
        "topology",
    }
    required_dimensions = {
        "ground_truth_exact_visible",
        "ground_truth_service_visible",
        "initial_visible",
        "metrics_top1_visible",
        "initial_and_ground_truth_co_visible",
        "metrics_top1_and_ground_truth_co_visible",
    }
    if set(per_source) != required_sources:
        raise ValueError("corrected-v3 per-source visibility coverage differs")
    for source in required_sources:
        dimensions = _mapping(per_source[source], f"{source} visibility dimensions")
        if set(dimensions) != required_dimensions:
            raise ValueError(
                f"corrected-v3 {source} visibility dimensions differ"
            )
        for dimension in dimensions.values():
            rate_value = _mapping(dimension, "visibility dimension rate")
            if rate_value.get("denominator") != 103:
                raise ValueError("corrected-v3 visibility denominator differs")

    contrast = _mapping(
        propagation.get("cross_benchmark_contrast"),
        "cross-benchmark contrast",
    )
    for contrast_field in ("fault_family", "propagation_length"):
        if contrast_field not in contrast:
            raise ValueError(f"corrected-v3 contrast omits {contrast_field}")

    taxonomy_requirements = {
        "strong_single_failure_decomposition": set(
            _STRONG_SINGLE_FAILURE_CLASSES
        ),
        "historical_m3_override_decomposition": set(_M3_OVERRIDE_CLASSES),
        "fault_phrase_relation": set(_FAULT_RELATION_CLASSES),
    }
    for field, required_labels in taxonomy_requirements.items():
        observed = _mapping(propagation.get(field), field)
        if not required_labels.issubset(observed):
            raise ValueError(f"corrected-v3 taxonomy omits required {field}")

    options = _mapping(frontier.get("options"), "frontier options")
    for option_name in ("A0", "A1", "A2", "A3", "A4"):
        option = _mapping(options.get(option_name), f"{option_name} option")
        summaries: list[Mapping[str, Any]] = [
            _mapping(option.get("all_consumed"), f"{option_name} all consumed"),
            _mapping(option.get("rca100"), f"{option_name} RCA100"),
        ]
        fixtures = _mapping(option.get("obss_fixtures"), f"{option_name} fixtures")
        summaries.extend(
            _mapping(value, f"{option_name} fixture summary")
            for value in fixtures.values()
        )
        for summary in summaries:
            denominator = summary.get("denominator")
            if not isinstance(denominator, int) or denominator <= 0:
                raise ValueError("corrected-v3 option denominator differs")
            for metric in (
                "entity_layer_error",
                "downstream_symptom_selection",
            ):
                metric_rate = _mapping(summary.get(metric), metric)
                if metric_rate.get("denominator") != denominator:
                    raise ValueError(
                        f"corrected-v3 {option_name} {metric} denominator differs"
                    )
            tool_use = _mapping(summary.get("expected_tool_use"), "tool use")
            if tool_use.get("case_denominator") != denominator:
                raise ValueError(
                    f"corrected-v3 {option_name} tool-use denominator differs"
                )


def verify_corrected_public(args: argparse.Namespace) -> int:
    """Independently rebuild every corrected public projection from private vectors."""

    assert_no_provider_environment()
    private_root = validate_private_root(args.private_root, create=False)
    corrected_version = int(getattr(args, "corrected_version", 2))
    lifecycle = _corrected_lifecycle(corrected_version)
    implementation_lock, implementation_lock_path, _ = _verify_state_binding(
        private_root,
        state_name=lifecycle["implementation_state"],
        lock_name=lifecycle["implementation_lock"],
    )
    _verify_corrected_implementation(implementation_lock)
    analysis_lock, analysis_lock_path, _ = _verify_state_binding(
        private_root,
        state_name=lifecycle["analysis_state"],
        lock_name=lifecycle["analysis_lock"],
    )
    decision_lock, decision_lock_path, _ = _verify_state_binding(
        private_root,
        state_name=lifecycle["decision_state"],
        lock_name=lifecycle["decision_lock"],
    )
    replay_lock, replay_lock_path, replay_state_path = _verify_state_binding(
        private_root,
        state_name=lifecycle["replay_state"],
        lock_name=lifecycle["replay_lock"],
    )
    if analysis_lock.get("previous_lock_sha256") != sha256_file(
        implementation_lock_path
    ):
        raise ValueError("corrected attribution is not bound to implementation")
    if decision_lock.get("previous_lock_sha256") != sha256_file(analysis_lock_path):
        raise ValueError("corrected decision is not bound to attribution")
    if replay_lock.get("previous_lock_sha256") != sha256_file(decision_lock_path):
        raise ValueError("corrected replay is not bound to decision")

    analysis_root = PROJECT_ROOT / "docs/analysis"
    design_root = PROJECT_ROOT / "docs/design"
    results_root = PROJECT_ROOT / "docs/results"
    entity_json = analysis_root / "rca100-entity-hierarchy-attribution.json"
    entity_md = analysis_root / "rca100-entity-hierarchy-attribution.md"
    propagation_json = (
        analysis_root / "rca100-propagation-visibility-attribution.json"
    )
    propagation_md = (
        analysis_root / "rca100-propagation-visibility-attribution.md"
    )
    frontier_json = analysis_root / "rca-crossbenchmark-architecture-frontier.json"
    frontier_md = analysis_root / "rca-crossbenchmark-architecture-frontier.md"
    decision_path = design_root / "rca-crossbenchmark-architecture-decision.md"
    spec_path = design_root / "unified-hierarchical-rca-v1-spec.md"
    replay_json = results_root / "unified-hierarchical-rca-v1-offline-replay.json"
    replay_md = results_root / "unified-hierarchical-rca-v1-offline-replay.md"
    brief_path = results_root / "unified-hierarchical-rca-v1-human-brief.md"
    public_paths = (
        entity_json,
        entity_md,
        propagation_json,
        propagation_md,
        frontier_json,
        frontier_md,
        decision_path,
        spec_path,
        replay_json,
        replay_md,
        brief_path,
    )
    if any(path.is_symlink() or not path.is_file() for path in public_paths):
        raise ValueError("corrected public output is missing or is a symlink")
    analysis_outputs = _mapping(
        analysis_lock.get("public_outputs"), "corrected analysis output lock"
    )
    for path in public_paths[:6]:
        if analysis_outputs.get(path.name) != sha256_file(path):
            raise ValueError(f"corrected analysis output drifted: {path.name}")
    if (
        decision_lock.get("decision_record_sha256") != sha256_file(decision_path)
        or decision_lock.get("implementation_spec_sha256") != sha256_file(spec_path)
        or decision_lock.get("frontier_report_sha256") != sha256_file(frontier_json)
    ):
        raise ValueError("corrected decision output drifted")
    replay_outputs = _mapping(
        replay_lock.get("public_outputs"), "corrected replay output lock"
    )
    for path in public_paths[8:]:
        if replay_outputs.get(path.name) != sha256_file(path):
            raise ValueError(f"corrected replay output drifted: {path.name}")
    if replay_lock.get("runtime_sha256") != sha256_file(
        PROJECT_ROOT / "src/ecomsre_rca_unified/runtime.py"
    ):
        raise ValueError("corrected runtime drifted after replay")

    expected_entity, expected_propagation, expected_frontier = (
        _canonical_corrected_analysis(
            private_root,
            _safe_path(args.frontier),
            corrected_version=corrected_version,
        )
    )
    expected_replay = _canonical_replay_from_private(
        private_root, corrected_version=corrected_version
    )
    actual_json = {
        entity_json: read_object(entity_json),
        propagation_json: read_object(propagation_json),
        frontier_json: read_object(frontier_json),
        replay_json: read_object(replay_json),
    }
    expected_json = {
        entity_json: expected_entity,
        propagation_json: expected_propagation,
        frontier_json: expected_frontier,
        replay_json: expected_replay,
    }
    for path, payload in actual_json.items():
        assert_public_payload(payload)
        if payload != expected_json[path]:
            raise ValueError(
                f"corrected public JSON differs from private recomputation: {path.name}"
            )
    if corrected_version >= 3:
        _verify_corrected_v3_goal_coverage(
            expected_propagation, expected_frontier
        )
    a5 = _mapping(expected_frontier.get("options"), "frontier options").get("A5")
    if not isinstance(a5, Mapping) or set(a5) != {
        "name",
        "selectable",
        "reporting_boundary",
        "oracle_upper_bound",
        "eligible_case_count",
        "expected_model_calls",
    }:
        raise ValueError("A5 public surface exceeds oracle-only boundary")
    folds = expected_frontier.get("grouped_robustness")
    if not isinstance(folds, list) or any(
        isinstance(item, Mapping) and item.get("option") == "A5" for item in folds
    ):
        raise ValueError("A5 folds must not enter the public report")
    frontier = load_frontier(_safe_path(args.frontier))
    expected_text = {
        entity_md: _markdown_json_report(
            "RCA100 Entity Hierarchy Attribution", expected_entity
        ),
        propagation_md: _markdown_json_report(
            "RCA100 Propagation and Visibility Attribution", expected_propagation
        ),
        frontier_md: _markdown_json_report(
            "RCA Cross-Benchmark Architecture Frontier", expected_frontier
        ),
        decision_path: _decision_markdown(
            ArchitectureOption.A0,
            expected_entity,
            expected_propagation,
            expected_frontier,
        ),
        spec_path: _spec_markdown(ArchitectureOption.A0, frontier),
        replay_md: _replay_markdown(expected_replay),
        brief_path: _human_brief(expected_replay),
    }
    for path, expected in expected_text.items():
        actual = path.read_text(encoding="utf-8")
        if actual != expected.rstrip() + "\n":
            raise ValueError(f"corrected public text is noncanonical: {path.name}")
        assert_public_text(actual)
    private_results = tuple(
        private_root / lifecycle["results"] / name
        for name in (
            "unified-case-records.jsonl",
            "entity-hierarchy-by-case.jsonl",
            "propagation-role-by-case.jsonl",
            "evidence-visibility-by-case.jsonl",
            "strong-single-failure-by-case.jsonl",
            "m3-failure-by-case.jsonl",
            "architecture-frontier-by-case.jsonl",
            "robustness-folds.json",
            "decision-input.json",
        )
    )
    if any(
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_mode & 0o777 != 0o600
        for path in private_results
    ):
        raise ValueError("corrected private output boundary differs")
    created_at = utc_now()
    lock_path = private_root / "locks" / lifecycle["public_lock"]
    write_json_create_once(
        lock_path,
        {
            "schema_version": "rca-crossbenchmark.corrected-public-lock.v1",
            "created_at_utc": created_at,
            "classification": list(CLASSIFICATION),
            "previous_lock_sha256": sha256_file(replay_lock_path),
            "public_outputs": {
                path.name: sha256_file(path) for path in public_paths
            },
            "private_outputs": {
                path.name: sha256_file(path) for path in private_results
            },
            "analysis_json_independently_recomputed": True,
            "replay_json_independently_recomputed": True,
            "canonical_text_exact_match": True,
            "a5_oracle_only_boundary": True,
            "leakage_scan_passed": True,
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "semantic_operations": 0,
            "re2_tt_accessed": False,
            "new_external_data_accessed": False,
        },
    )
    write_json_create_once(
        private_root / "state" / f"{lifecycle['public_state']}.json",
        {
            "schema_version": "rca-crossbenchmark.state.v1",
            "state": lifecycle["public_state"],
            "created_at_utc": created_at,
            "previous_state": lifecycle["replay_state"],
            "previous_state_record_sha256": sha256_file(replay_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print(
        f"[verify corrected-v{corrected_version}] independent 11/11 PASS",
        flush=True,
    )
    return 0


def _add_consumed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--frontier", type=Path, required=True)
    parser.add_argument("--methodology", type=Path, required=True)
    parser.add_argument("--candidate-3-root", type=Path, required=True)
    parser.add_argument("--candidate-4-root", type=Path, required=True)
    parser.add_argument("--candidate-5-root", type=Path, required=True)
    parser.add_argument("--tune-root", type=Path, required=True)
    parser.add_argument("--regression-root", type=Path, required=True)
    parser.add_argument("--ob-root", type=Path, required=True)
    parser.add_argument("--ss-root", type=Path, required=True)
    parser.add_argument("--indicator-config", type=Path, required=True)
    parser.add_argument("--indicator-config-sha256", required=True)
    parser.add_argument("--rca-input-root", type=Path, required=True)
    parser.add_argument("--rca-terminal-root", type=Path, required=True)
    parser.add_argument("--rca-answer-root", type=Path, required=True)
    parser.add_argument("--rca-input-source-lock", type=Path, required=True)
    parser.add_argument("--rca-terminal-lock", type=Path, required=True)
    parser.add_argument("--rca-answer-lock", type=Path, required=True)
    parser.add_argument("--rca-scoring-lock", type=Path, required=True)
    parser.add_argument("--rca-schedule", type=Path, required=True)
    parser.add_argument("--rca-case-scores", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze-inputs")
    freeze.add_argument("--private-root", type=Path, required=True)
    freeze.add_argument("--frontier", type=Path, required=True)
    freeze.add_argument("--candidate-3-root", type=Path, required=True)
    freeze.add_argument("--candidate-4-root", type=Path, required=True)
    freeze.add_argument("--candidate-5-root", type=Path, required=True)
    freeze.add_argument("--tune-root", type=Path, required=True)
    freeze.add_argument("--regression-root", type=Path, required=True)
    freeze.add_argument("--ob-root", type=Path, required=True)
    freeze.add_argument("--ss-root", type=Path, required=True)
    freeze.add_argument("--rca-input-root", type=Path, required=True)
    freeze.add_argument("--rca-terminal-root", type=Path, required=True)
    freeze.add_argument("--rca-answer-root", type=Path, required=True)
    freeze.add_argument("--rca-input-source-lock", type=Path, required=True)
    freeze.add_argument("--rca-terminal-lock", type=Path, required=True)
    freeze.add_argument("--rca-answer-lock", type=Path, required=True)
    freeze.add_argument("--rca-scoring-lock", type=Path, required=True)
    freeze.add_argument("--rca-schedule", type=Path, required=True)
    freeze.add_argument("--rca-case-scores", type=Path, required=True)
    freeze.set_defaults(handler=freeze_inputs)
    methods = subparsers.add_parser("freeze-methodology")
    methods.add_argument("--private-root", type=Path, required=True)
    methods.add_argument("--frontier", type=Path, required=True)
    methods.add_argument("--methodology", type=Path, required=True)
    methods.set_defaults(handler=freeze_methodology)
    analysis = subparsers.add_parser("analyze")
    analysis.add_argument("--private-root", type=Path, required=True)
    analysis.add_argument("--frontier", type=Path, required=True)
    analysis.add_argument("--methodology", type=Path, required=True)
    analysis.add_argument("--candidate-3-root", type=Path, required=True)
    analysis.add_argument("--candidate-4-root", type=Path, required=True)
    analysis.add_argument("--candidate-5-root", type=Path, required=True)
    analysis.add_argument("--tune-root", type=Path, required=True)
    analysis.add_argument("--regression-root", type=Path, required=True)
    analysis.add_argument("--ob-root", type=Path, required=True)
    analysis.add_argument("--ss-root", type=Path, required=True)
    analysis.add_argument("--indicator-config", type=Path, required=True)
    analysis.add_argument("--indicator-config-sha256", required=True)
    analysis.add_argument("--rca-input-root", type=Path, required=True)
    analysis.add_argument("--rca-terminal-root", type=Path, required=True)
    analysis.add_argument("--rca-answer-root", type=Path, required=True)
    analysis.add_argument("--rca-input-source-lock", type=Path, required=True)
    analysis.add_argument("--rca-terminal-lock", type=Path, required=True)
    analysis.add_argument("--rca-answer-lock", type=Path, required=True)
    analysis.add_argument("--rca-scoring-lock", type=Path, required=True)
    analysis.add_argument("--rca-schedule", type=Path, required=True)
    analysis.add_argument("--rca-case-scores", type=Path, required=True)
    analysis.set_defaults(handler=analyze)
    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--private-root", type=Path, required=True)
    replay_parser.add_argument("--frontier", type=Path, required=True)
    replay_parser.add_argument("--methodology", type=Path, required=True)
    replay_parser.add_argument("--candidate-3-root", type=Path, required=True)
    replay_parser.add_argument("--candidate-4-root", type=Path, required=True)
    replay_parser.add_argument("--candidate-5-root", type=Path, required=True)
    replay_parser.add_argument("--tune-root", type=Path, required=True)
    replay_parser.add_argument("--regression-root", type=Path, required=True)
    replay_parser.add_argument("--ob-root", type=Path, required=True)
    replay_parser.add_argument("--ss-root", type=Path, required=True)
    replay_parser.add_argument("--indicator-config", type=Path, required=True)
    replay_parser.add_argument("--indicator-config-sha256", required=True)
    replay_parser.add_argument("--rca-input-root", type=Path, required=True)
    replay_parser.add_argument("--rca-terminal-root", type=Path, required=True)
    replay_parser.add_argument("--rca-answer-root", type=Path, required=True)
    replay_parser.add_argument("--rca-input-source-lock", type=Path, required=True)
    replay_parser.add_argument("--rca-terminal-lock", type=Path, required=True)
    replay_parser.add_argument("--rca-answer-lock", type=Path, required=True)
    replay_parser.add_argument("--rca-scoring-lock", type=Path, required=True)
    replay_parser.add_argument("--rca-schedule", type=Path, required=True)
    replay_parser.add_argument("--rca-case-scores", type=Path, required=True)
    replay_parser.set_defaults(handler=replay)
    verify = subparsers.add_parser("verify-public")
    verify.add_argument("--private-root", type=Path, required=True)
    verify.add_argument("--frontier", type=Path, required=True)
    verify.set_defaults(handler=verify_public)
    format_repair = subparsers.add_parser("repair-public-format")
    format_repair.add_argument("--private-root", type=Path, required=True)
    format_repair.add_argument("--frontier", type=Path, required=True)
    format_repair.set_defaults(handler=repair_public_format)
    corrected_freeze = subparsers.add_parser("freeze-corrected-implementation")
    corrected_freeze.add_argument("--private-root", type=Path, required=True)
    corrected_freeze.set_defaults(handler=freeze_corrected_implementation)
    corrected_analysis = subparsers.add_parser("analyze-corrected")
    _add_consumed_arguments(corrected_analysis)
    corrected_analysis.set_defaults(handler=analyze, corrected_version=2)
    corrected_replay = subparsers.add_parser("replay-corrected")
    _add_consumed_arguments(corrected_replay)
    corrected_replay.set_defaults(handler=replay, corrected_version=2)
    corrected_verify = subparsers.add_parser("verify-corrected-public")
    corrected_verify.add_argument("--private-root", type=Path, required=True)
    corrected_verify.add_argument("--frontier", type=Path, required=True)
    corrected_verify.set_defaults(
        handler=verify_corrected_public, corrected_version=2
    )
    corrected_v3_freeze = subparsers.add_parser(
        "freeze-corrected-v3-implementation"
    )
    corrected_v3_freeze.add_argument("--private-root", type=Path, required=True)
    corrected_v3_freeze.set_defaults(handler=freeze_corrected_v3_implementation)
    corrected_v3_analysis = subparsers.add_parser("analyze-corrected-v3")
    _add_consumed_arguments(corrected_v3_analysis)
    corrected_v3_analysis.set_defaults(handler=analyze, corrected_version=3)
    corrected_v3_replay = subparsers.add_parser("replay-corrected-v3")
    _add_consumed_arguments(corrected_v3_replay)
    corrected_v3_replay.set_defaults(handler=replay, corrected_version=3)
    corrected_v3_verify = subparsers.add_parser("verify-corrected-v3-public")
    corrected_v3_verify.add_argument("--private-root", type=Path, required=True)
    corrected_v3_verify.add_argument("--frontier", type=Path, required=True)
    corrected_v3_verify.set_defaults(
        handler=verify_corrected_public, corrected_version=3
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
