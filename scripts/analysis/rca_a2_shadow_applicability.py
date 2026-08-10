"""Freeze, evaluate, replay, and publish the bounded A2 Gate frontier."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

from ecomsre_rca_unified.a2_shadow import (
    A2ShadowInput,
    ApplicabilityGateId,
)
from ecomsre_rca_unified.analysis import UnifiedMetricCandidate, UnifiedRCACase
from ecomsre_rca_unified.applicability import (
    ApplicabilityCase,
    GateEvaluation,
    GateFrontierResult,
    evaluate_applicability_frontier,
    evaluate_production_case,
    evaluate_reference_case,
)
from ecomsre_rca_unified.contracts import (
    CanonicalEntityLayer,
    EntityHierarchyPath,
    EvidenceVisibilitySummary,
    FaultOntologyClass,
    FrontierOutcome,
    PropagationDisposition,
)
from ecomsre_rca_unified.frontier import load_frontier
from ecomsre_rca_unified.runtime import (
    StrongSingleHierarchicalInput,
    execute_unified_hierarchical_rca,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STARTING_COMMIT = "5b972b3a3dc00830e0d69df27c38935f3c4846ac"
PR24_BRANCH = "feature/rca-crossbenchmark-architecture-convergence-v1"
EVALUATION_VERSION = "hierarchical-a2-shadow-v1"
NO_GATE_VERDICT = "A2_APPLICABILITY_GATE_NOT_SUPPORTED_KEEP_A0"
CLASSIFICATION = (
    "CONSUMED_CROSS_BENCHMARK_DEVELOPMENT",
    "LIVE_DEVELOPMENT_EVALUATION",
    "NOT_EXTERNAL_VALIDATION",
    "NOT_PRIMARY_INFERENCE",
)
EXPECTED_DESIGN_PREFIX = (
    ("RCA100", 103),
    ("candidate-3", 60),
    ("candidate-4", 60),
    ("candidate-5", 60),
)
EXPECTED_ALL_COUNTS = {
    "RCA100": 103,
    "candidate-3": 60,
    "candidate-4": 60,
    "candidate-5": 60,
    "pr21-tune": 60,
    "pr21-regression": 120,
}
IMPLEMENTATION_PATHS = frozenset(
    {
        "config/rca-a2-shadow-applicability-v1/applicability.json",
        "scripts/analysis/rca_a2_shadow_applicability.py",
        "src/ecomsre_rca_unified/a2_shadow.py",
        "src/ecomsre_rca_unified/applicability.py",
        "tests/analysis/test_rca_a2_shadow_applicability.py",
    }
)
PUBLIC_PATHS = (
    PROJECT_ROOT / "docs/analysis/rca-a2-applicability-frontier.json",
    PROJECT_ROOT / "docs/analysis/rca-a2-applicability-frontier.md",
    PROJECT_ROOT / "docs/design/rca-a2-applicability-decision.md",
    PROJECT_ROOT / "docs/design/strong-single-hierarchical-conditional-a2-v1-spec.md",
    PROJECT_ROOT / "docs/results/rca-a2-live-shadow-development.json",
    PROJECT_ROOT / "docs/results/rca-a2-live-shadow-development.md",
    PROJECT_ROOT / "docs/results/rca-a2-live-shadow-human-brief.md",
)

_PROVIDER_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT",
)
_PUBLIC_FORBIDDEN = re.compile(
    r"(?:/Users/|\.ecomsre-private|private_case|case_id|source_task_id|"
    r"run_id|ground_truth|terminal-record|provider_endpoint|"
    r"sk-[A-Za-z0-9]{20,}|(?:service|pod|container|node)\|)",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSON is missing or unsafe: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"required JSON must be an object: {path.name}")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def load_applicability_policy(path: Path) -> dict[str, Any]:
    policy = read_object(path.resolve(strict=True))
    required = {
        "schema_version",
        "evaluation_version",
        "starting_commit",
        "classification",
        "authoritative_runtime",
        "a2_reference",
        "design_prefix",
        "gates",
        "root_eligible_layers",
        "acceptance",
        "selection_priority",
        "no_gate_verdict",
        "provider_boundary",
    }
    if set(policy) != required:
        raise ValueError("A2 applicability policy schema differs")
    gates = policy.get("gates")
    if not isinstance(gates, list) or tuple(
        str(_mapping(item, "Gate").get("id")) for item in gates
    ) != tuple(item.value for item in ApplicabilityGateId):
        raise ValueError("A2 applicability policy must contain exactly ordered G0-G4")
    prefix = policy.get("design_prefix")
    if (
        not isinstance(prefix, list)
        or tuple(
            (
                str(_mapping(item, "design prefix").get("fixture")),
                int(_mapping(item, "design prefix").get("count", -1)),
            )
            for item in prefix
        )
        != EXPECTED_DESIGN_PREFIX
    ):
        raise ValueError("A2 design prefix differs from RCA100 and Candidate-3/4/5")
    if (
        policy.get("evaluation_version") != EVALUATION_VERSION
        or policy.get("starting_commit") != STARTING_COMMIT
        or tuple(policy.get("classification", ())) != CLASSIFICATION
        or policy.get("no_gate_verdict") != NO_GATE_VERDICT
    ):
        raise ValueError("A2 applicability policy identity differs")
    reference = _mapping(policy.get("a2_reference"), "A2 reference")
    acceptance = _mapping(policy.get("acceptance"), "acceptance")
    rca = _mapping(acceptance.get("rca100"), "RCA100 acceptance")
    obss = _mapping(acceptance.get("obss_aggregate"), "OBSS acceptance")
    if (
        reference.get("minimum_normalized_margin") != 0.25
        or rca.get("maximum_root_damage_rate") != 0.1
        or obss.get("minimum_g0_net_retained_fraction") != 0.5
    ):
        raise ValueError("A2 applicability thresholds differ from the Goal")
    return policy


def read_design_prefix_objects(
    path: Path,
    ordered_counts: Sequence[tuple[str, int]],
) -> tuple[dict[str, Any], ...]:
    """Read exactly the design prefix; never inspect a later outcome row."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("A2 design vector is missing or unsafe")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for fixture, count in ordered_counts:
            if not fixture or count <= 0:
                raise ValueError("A2 design prefix specification is invalid")
            for _ in range(count):
                line = handle.readline()
                if not line:
                    raise ValueError("A2 design vector ended before the frozen prefix")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("A2 design row must be an object")
                if value.get("fixture") != fixture:
                    raise ValueError("A2 design fixture order drifted")
                rows.append(value)
    return tuple(rows)


def scan_public_payload(payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    match = _PUBLIC_FORBIDDEN.search(text)
    if match is not None:
        raise ValueError(f"public leakage marker found: {match.group(0)}")


def scan_public_text(text: str) -> None:
    match = _PUBLIC_FORBIDDEN.search(text)
    if match is not None:
        raise ValueError(f"public leakage marker found: {match.group(0)}")


def assert_no_provider_environment() -> None:
    present = [name for name in _PROVIDER_ENV if os.environ.get(name)]
    if present:
        raise ValueError("Provider environment must remain absent for offline A2 work")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _validate_git_lineage(*, require_clean: bool) -> str:
    head = _git("rev-parse", "HEAD")
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", STARTING_COMMIT, head),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    origin_head = _git("rev-parse", f"origin/{PR24_BRANCH}")
    if origin_head != STARTING_COMMIT:
        raise ValueError("PR #24 local remote-tracking head drifted")
    if require_clean and _git("status", "--porcelain=v1", "-uall"):
        raise ValueError("A2 implementation freeze requires a clean worktree")
    return head


def _validate_private_root(path: Path, *, create: bool) -> Path:
    root = path.expanduser()
    if not root.is_absolute():
        raise ValueError("A2 private root must be absolute")
    if create and not root.exists():
        root.mkdir(mode=0o700)
    resolved = root.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise ValueError("A2 private root must be a real directory")
    if stat.S_IMODE(resolved.stat().st_mode) != 0o700:
        raise ValueError("A2 private root mode must be 0700")
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        pass
    else:
        raise ValueError("A2 private root must remain outside Git")
    for name in ("locks", "state", "results"):
        child = resolved / name
        if create and not child.exists():
            child.mkdir(mode=0o700)
        if child.is_symlink() or not child.is_dir():
            raise ValueError("A2 private subdirectory is missing or unsafe")
        if stat.S_IMODE(child.stat().st_mode) != 0o700:
            raise ValueError("A2 private subdirectory mode must be 0700")
    return resolved


def _write_private_bytes_create_once(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"existing create-once private file differs: {path.name}")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def write_private_json_create_once(path: Path, payload: object) -> None:
    _write_private_bytes_create_once(path, canonical_json_bytes(payload))


def write_private_jsonl_create_once(
    path: Path,
    records: Sequence[Mapping[str, object]],
) -> None:
    payload = b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for record in records
    )
    _write_private_bytes_create_once(path, payload)


def _write_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _state_binding(
    root: Path,
    *,
    state_name: str,
    lock_name: str,
) -> tuple[dict[str, Any], Path, Path]:
    lock_path = root / "locks" / lock_name
    state_path = root / "state" / f"{state_name}.json"
    lock = read_object(lock_path)
    state = read_object(state_path)
    if state.get("state") != state_name or state.get("lock_sha256") != sha256_file(
        lock_path
    ):
        raise ValueError(f"A2 state/lock binding differs: {state_name}")
    return lock, lock_path, state_path


def _unified_from_private(record: Mapping[str, Any]) -> UnifiedRCACase:
    truth = _mapping(record.get("ground_truth"), "private ground truth")
    initial = _mapping(record.get("initial"), "private Initial")
    historical = _mapping(record.get("historical_m3"), "private historical M3")
    hierarchy = _mapping(initial.get("hierarchy_path"), "private hierarchy")
    visibility = _mapping(record.get("visibility"), "private visibility")
    raw_candidates = record.get("metrics_candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("private Metrics candidates must be a list")
    equivalent = truth.get("equivalent_entities")
    parents = hierarchy.get("explicit_parents")
    refs = initial.get("supporting_evidence_refs")
    if (
        not isinstance(equivalent, list)
        or not isinstance(parents, list)
        or not isinstance(refs, list)
    ):
        raise ValueError("private RCA lists differ")
    candidates = tuple(
        UnifiedMetricCandidate(
            entity=str(item["entity"]),
            service_ancestor=None
            if item.get("service") is None
            else str(item["service"]),
            layer=CanonicalEntityLayer(str(item["layer"])),
            rank=int(item["rank"]),
            score=float(item["score"]),
            metric_family=str(item["metric_family"]),
            first_anomaly_time=None
            if item.get("first_anomaly_time") is None
            else float(item["first_anomaly_time"]),
            source_support=int(item["source_support"]),
            relation_to_symptom=str(item["relation_to_symptom"]),
        )
        for raw_item in raw_candidates
        for item in (_mapping(raw_item, "private Metrics candidate"),)
    )
    return UnifiedRCACase(
        private_case_key=str(record["private_case_key"]),
        fixture=str(record["fixture"]),
        benchmark=str(record["benchmark"]),
        system=str(record["system"]),
        fault_family=str(record["fault_family"]),
        fault_type_truth=str(record["fault_type_truth"]),
        fault_type_raw=str(record["fault_type_raw"]),
        fault_regime=FaultOntologyClass(str(record["fault_regime"])),
        ground_truth_fault_regime=FaultOntologyClass(str(truth["fault_regime"])),
        metric_family=str(record["metric_family"]),
        ground_truth_entity=str(truth["entity"]),
        ground_truth_equivalent_entities=frozenset(str(item) for item in equivalent),
        ground_truth_layer=CanonicalEntityLayer(str(truth["layer"])),
        ground_truth_service=None
        if truth.get("service") is None
        else str(truth["service"]),
        ground_truth_workload=None
        if truth.get("workload") is None
        else str(truth["workload"]),
        ground_truth_node=None if truth.get("node") is None else str(truth["node"]),
        initial_entity=str(initial["entity"]),
        initial_layer=CanonicalEntityLayer(str(initial["layer"])),
        initial_hierarchy_path=EntityHierarchyPath(
            entity=str(hierarchy["entity"]),
            explicit_parents=tuple(str(item) for item in parents),
            service_ancestor_or_none=None
            if hierarchy.get("service_ancestor") is None
            else str(hierarchy["service_ancestor"]),
            infrastructure_ancestor_or_none=None
            if hierarchy.get("infrastructure_ancestor") is None
            else str(hierarchy["infrastructure_ancestor"]),
        ),
        initial_supporting_evidence_refs=tuple(str(item) for item in refs),
        initial_service=None
        if initial.get("service") is None
        else str(initial["service"]),
        initial_correct_exact=bool(initial["exact_correct"]),
        initial_correct_service=bool(initial["service_correct"]),
        initial_pair_correct=bool(initial["pair_correct"]),
        initial_relation=str(initial["relation"]),
        m3_action=None
        if historical.get("action") is None
        else str(historical["action"]),
        m3_final_entity=str(historical["entity"]),
        m3_final_layer=CanonicalEntityLayer(str(historical["layer"])),
        m3_final_service=None
        if historical.get("service") is None
        else str(historical["service"]),
        m3_correct_exact=bool(historical["exact_correct"]),
        m3_correct_service=bool(historical["service_correct"]),
        m3_pair_correct=bool(historical["pair_correct"]),
        m3_relation=str(historical["relation"]),
        metrics_candidates=candidates,
        metrics_initial_rank=None
        if record.get("metrics_initial_rank") is None
        else int(record["metrics_initial_rank"]),
        metrics_margin=None
        if record.get("metrics_margin") is None
        else float(record["metrics_margin"]),
        metrics_top1_is_downstream=bool(record["metrics_top1_is_downstream"]),
        propagation_disposition=PropagationDisposition(
            str(record["propagation_disposition"])
        ),
        visibility=EvidenceVisibilitySummary(
            catalog_entities=frozenset(str(item) for item in visibility["catalog"]),
            metrics_entities=frozenset(str(item) for item in visibility["metrics"]),
            logs_entities=frozenset(str(item) for item in visibility["logs"]),
            traces_entities=frozenset(str(item) for item in visibility["traces"]),
            events_entities=frozenset(str(item) for item in visibility["events"]),
            alerts_entities=frozenset(str(item) for item in visibility["alerts"]),
            topology_entities=frozenset(str(item) for item in visibility["topology"]),
        ),
        causal_visible_entities=frozenset(str(item) for item in visibility["causal"]),
        alert_entity=None
        if record.get("alert_entity") is None
        else str(record["alert_entity"]),
        terminal_failure=bool(record["terminal_failure"]),
    )


def _runtime_projection(case: UnifiedRCACase) -> A2ShadowInput:
    top1 = case.metrics_top1
    return A2ShadowInput(
        initial_entity=case.initial_entity,
        initial_layer=case.initial_layer,
        initial_hierarchy_path=case.initial_hierarchy_path,
        initial_metrics_rank_or_none=case.metrics_initial_rank,
        metrics_top1_entity=None if top1 is None else top1.entity,
        metrics_top1_layer=CanonicalEntityLayer.UNKNOWN if top1 is None else top1.layer,
        metrics_top1_service_ancestor=None if top1 is None else top1.service_ancestor,
        metrics_margin=case.metrics_margin,
        metrics_top1_is_downstream=case.metrics_top1_is_downstream,
        propagation_disposition=case.propagation_disposition,
        evidence_visibility=case.visibility,
        fault_type_raw=case.fault_type_raw,
        fault_ontology_class=case.fault_regime,
        supporting_evidence_refs=case.initial_supporting_evidence_refs,
    )


def _applicability_case(case: UnifiedRCACase) -> ApplicabilityCase:
    return ApplicabilityCase(
        fixture=case.fixture,
        frontier_case=case.to_frontier_case(),
        runtime_input=_runtime_projection(case),
    )


def _load_design_cases(path: Path) -> tuple[ApplicabilityCase, ...]:
    rows = read_design_prefix_objects(path, EXPECTED_DESIGN_PREFIX)
    return tuple(_applicability_case(_unified_from_private(item)) for item in rows)


def _read_all_cases(path: Path) -> tuple[UnifiedRCACase, ...]:
    rows: list[UnifiedRCACase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(_unified_from_private(json.loads(line)))
    counts: dict[str, int] = {}
    for item in rows:
        counts[item.fixture] = counts.get(item.fixture, 0) + 1
    if counts != EXPECTED_ALL_COUNTS:
        raise ValueError("full frozen case vector count differs")
    return tuple(rows)


def _outcome_record(
    fixture: str,
    gate: ApplicabilityGateId,
    outcome: FrontierOutcome,
) -> dict[str, object]:
    return {
        "schema_version": "hierarchical-a2-shadow-v1.reference-case.v1",
        "private_case_key": outcome.private_case_key,
        "fixture": fixture,
        "gate": gate.value,
        "option": outcome.option.value,
        "initial_entity": outcome.initial_entity,
        "final_entity": outcome.final_entity,
        "fault_type": outcome.fault_type,
        "initial_exact_correct": outcome.initial_exact_correct,
        "final_exact_correct": outcome.final_exact_correct,
        "initial_service_correct": outcome.initial_service_correct,
        "final_service_correct": outcome.final_service_correct,
        "initial_pair_correct": outcome.initial_pair_correct,
        "final_pair_correct": outcome.final_pair_correct,
        "override": outcome.override,
        "root_rescue": outcome.root_rescue,
        "root_damage": outcome.root_damage,
    }


def _fold_public(item: Any) -> dict[str, object]:
    return {
        "axis": item.axis,
        "held_out_group": item.held_out_group,
        "denominator": item.denominator,
        "root_rescue": item.rescue,
        "root_damage": item.damage,
        "root_net_rescue": item.net_rescue,
    }


def _gate_public(item: GateEvaluation) -> dict[str, object]:
    return {
        "accepted": item.accepted,
        "rejection_reasons": list(item.rejection_reasons),
        "model_calls": item.model_calls,
        "rca100": dict(item.rca100),
        "obss_fixtures": {name: dict(value) for name, value in item.fixtures.items()},
        "obss_aggregate": dict(item.obss_aggregate),
        "g0_obss_net_retained_fraction": item.obss_net_retained_fraction,
        "fault_family_fold_pass_fraction": item.fault_family_fold_pass_fraction,
        "entity_layer_fold_pass_fraction": item.entity_layer_fold_pass_fraction,
        "grouped_robustness": [_fold_public(fold) for fold in item.folds],
    }


def _frontier_public(result: GateFrontierResult) -> dict[str, object]:
    selected = None if result.selected_gate is None else result.selected_gate.value
    verdict = NO_GATE_VERDICT if selected is None else "A2_APPLICABILITY_GATE_SUPPORTED"
    payload: dict[str, object] = {
        "schema_version": "hierarchical-a2-shadow-v1.applicability-frontier.v1",
        "evaluation_version": EVALUATION_VERSION,
        "classification": list(CLASSIFICATION),
        "authoritative_runtime": "A0_STRONG_SINGLE_HIERARCHICAL",
        "a2_reference": "G0_A2_REFERENCE",
        "design_denominators": {
            fixture: count for fixture, count in EXPECTED_DESIGN_PREFIX
        },
        "design_read_boundary": "EXACT_283_RECORD_PREFIX_NO_TUNE_OR_REGRESSION_OUTCOME_PARSE",
        "gates": {
            gate.value: _gate_public(result.evaluations[gate])
            for gate in ApplicabilityGateId
        },
        "selected_gate": selected,
        "gate_supported": selected is not None,
        "verdict": verdict,
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "live_shadow_executed": False,
        "promotion_executed": False,
        "regression_executed": False,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
    }
    scan_public_payload(payload)
    return payload


def _json_markdown(title: str, payload: Mapping[str, object]) -> str:
    return f"# {title}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n```\n"


def _decision_markdown(frontier_payload: Mapping[str, object]) -> str:
    gates = _mapping(frontier_payload.get("gates"), "public gates")
    lines = [
        "# RCA A2 Applicability Decision",
        "",
        f"Status: `{frontier_payload['verdict']}`",
        "",
        "A0 remains the authoritative fallback and the only active runtime.",
        "A2 remains a typed Shadow recommendation only; no Gate was promoted.",
        "",
        "## Frozen Gate Results",
        "",
        "| Gate | Accepted | RCA100 Initial→Final | Rescue/Damage/Net | OB/SS Net | G0 Net Retained |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for gate in ApplicabilityGateId:
        value = _mapping(gates[gate.value], gate.value)
        rca = _mapping(value["rca100"], "RCA100")
        obss = _mapping(value["obss_aggregate"], "OBSS")
        lines.append(
            f"| {gate.value} | {str(value['accepted']).lower()} | "
            f"{rca['initial_exact_correct']}→{rca['final_exact_correct']} | "
            f"{rca['root_rescue']}/{rca['root_damage']}/{rca['root_net_rescue']} | "
            f"{obss['root_net_rescue']} | {float(value['g0_obss_net_retained_fraction']):.6f} |"
        )
    lines.extend(
        (
            "",
            "G0/G1/G2 fail the frozen RCA100 safety boundary. G3/G4 avoid RCA100 damage but retain less than half of G0 OB/SS net rescue. The finite frontier is consumed; no sixth Gate or threshold search is authorized.",
            "",
            "Runtime Gate inputs contain only entity layers, hierarchy, Metrics rank/margin, topology relation, propagation disposition, evidence support, metric family, and typed fault ontology. Benchmark identity is not an input.",
            "",
            "No Provider was constructed. Live Shadow, promotion, and Regression were not executed.",
        )
    )
    text = "\n".join(lines).rstrip() + "\n"
    scan_public_text(text)
    return text


def _shadow_public(replay_summary: Mapping[str, object]) -> dict[str, object]:
    payload = {
        "schema_version": "hierarchical-a2-shadow-v1.development-result.v1",
        "evaluation_version": EVALUATION_VERSION,
        "classification": list(CLASSIFICATION),
        "verdict": NO_GATE_VERDICT,
        "a0_authoritative": True,
        "a2_mode": "SHADOW_ONLY_INACTIVE_REFERENCE",
        "selected_gate": None,
        "runtime_version": None,
        "offline_implementation_replay": dict(replay_summary),
        "provider_preflight": {"executed": False, "attempts": 0},
        "live_shadow": {"executed": False, "scheduled": 0, "terminalized": 0},
        "promotion": {"executed": False, "active": False},
        "regression": {"executed": False, "scheduled": 0, "reruns": 0},
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "specialist_calls": 0,
        "fusion_calls": 0,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
    }
    scan_public_payload(payload)
    return payload


def _inactive_spec_markdown() -> str:
    text = """# Strong Single Hierarchical Conditional A2 v1 — Inactive Shadow Reference

Status: `A2_APPLICABILITY_GATE_NOT_SUPPORTED_KEEP_A0`

No conditional A2 runtime version was frozen or activated. A0 Strong Single Hierarchical remains the only active runtime and always keeps the Initial Root.

The implemented Shadow contract can report G0–G4 `WOULD_KEEP` or `WOULD_OVERRIDE` recommendations from one Strong Single observation. In Shadow mode the authoritative root, downstream remediation decision, indicator, and fault type remain the Initial values. The contract contains no benchmark identity and creates no Specialist or Fusion call.

Because no frozen applicability Gate passed, Provider preflight, live Shadow, promotion, and Regression are prohibited for this evaluation version. The finite Gate frontier is consumed and must not be extended with another Gate or threshold search.
"""
    scan_public_text(text)
    return text


def _human_brief() -> str:
    text = """# A2 条件式 Shadow Human Brief

结论：`A2_APPLICABILITY_GATE_NOT_SUPPORTED_KEEP_A0`。

G0/G1/G2 保留了 OB/SS 的离线收益，但在 RCA100 上仍造成 2 次 Root Damage；G3/G4 消除了该 Damage，却只保留 G0 OB/SS 净收益的约 7.69%，未达到冻结的 50% 门槛。因此没有 Gate 可以同时满足跨系统安全与收益要求。

A2 typed Shadow 合同与逐 case production replay 已实现，但 A0 仍是唯一 active runtime。未构造 Provider，未执行 live Shadow、promotion 或 Regression，也未访问新外部数据或 RE2-TT。本阶段是 consumed development evidence，不是外部验证。
"""
    scan_public_text(text)
    return text


def _verify_source_integrity(input_lock: Mapping[str, Any]) -> tuple[Path, Path]:
    source_root = Path(str(input_lock["source_private_root"]))
    source_vector = source_root / "results-v3/unified-case-records.jsonl"
    source_public_lock = (
        source_root / "locks/corrected-v3-public-verification-lock.json"
    )
    if sha256_file(source_vector) != input_lock.get("source_vector_sha256"):
        raise ValueError("frozen source vector drifted")
    if sha256_file(source_public_lock) != input_lock.get("source_public_lock_sha256"):
        raise ValueError("frozen source public lock drifted")
    return source_root, source_vector


def freeze_inputs(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    _validate_git_lineage(require_clean=True)
    root = _validate_private_root(args.private_root, create=True)
    source = _validate_private_root(args.source_private_root, create=False)
    policy_path = args.policy.resolve(strict=True)
    frontier_path = args.frontier.resolve(strict=True)
    load_applicability_policy(policy_path)
    load_frontier(frontier_path)
    source_public_lock_path = (
        source / "locks/corrected-v3-public-verification-lock.json"
    )
    source_public_state_path = (
        source / "state/CORRECTED_V3_PUBLIC_OUTPUTS_VERIFIED.json"
    )
    source_input_lock_path = source / "locks/input-and-frontier-lock.json"
    source_vector = source / "results-v3/unified-case-records.jsonl"
    source_public_lock = read_object(source_public_lock_path)
    source_public_state = read_object(source_public_state_path)
    if source_public_state.get("lock_sha256") != sha256_file(source_public_lock_path):
        raise ValueError("corrected-v3 source state/lock binding differs")
    private_outputs = _mapping(
        source_public_lock.get("private_outputs"), "source private outputs"
    )
    if private_outputs.get(source_vector.name) != sha256_file(source_vector):
        raise ValueError("corrected-v3 source vector differs from its public lock")
    line_count = sum(1 for _ in source_vector.open("rb"))
    if line_count != sum(EXPECTED_ALL_COUNTS.values()):
        raise ValueError("corrected-v3 source vector denominator differs")
    created = utc_now()
    lock_path = root / "locks/a2-input-lock.json"
    payload = {
        "schema_version": "hierarchical-a2-shadow-v1.input-lock.v1",
        "created_at_utc": created,
        "classification": list(CLASSIFICATION),
        "starting_commit": STARTING_COMMIT,
        "pr24_remote_tracking_head": _git("rev-parse", f"origin/{PR24_BRANCH}"),
        "source_private_root": str(source),
        "source_public_lock_sha256": sha256_file(source_public_lock_path),
        "source_public_state_sha256": sha256_file(source_public_state_path),
        "source_input_lock_sha256": sha256_file(source_input_lock_path),
        "source_vector_sha256": sha256_file(source_vector),
        "source_vector_bytes": source_vector.stat().st_size,
        "source_vector_records": line_count,
        "design_prefix": [
            {"fixture": fixture, "count": count}
            for fixture, count in EXPECTED_DESIGN_PREFIX
        ],
        "design_records": sum(count for _, count in EXPECTED_DESIGN_PREFIX),
        "later_outcomes_semantically_parsed_for_selection": False,
        "policy_sha256": sha256_file(policy_path),
        "frontier_sha256": sha256_file(frontier_path),
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
    }
    write_private_json_create_once(lock_path, payload)
    write_private_json_create_once(
        root / "state/A2_INPUTS_AND_APPLICABILITY_FROZEN.json",
        {
            "schema_version": "hierarchical-a2-shadow-v1.state.v1",
            "state": "A2_INPUTS_AND_APPLICABILITY_FROZEN",
            "created_at_utc": created,
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print("[freeze-inputs] A2 design prefix and G0-G4 FROZEN", flush=True)
    return 0


def freeze_implementation(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    head = _validate_git_lineage(require_clean=True)
    if head == STARTING_COMMIT:
        raise ValueError("A2 implementation must be committed before freeze")
    changed = set(_git("diff", "--name-only", f"{STARTING_COMMIT}..HEAD").splitlines())
    if changed != set(IMPLEMENTATION_PATHS):
        raise ValueError(f"A2 implementation path surface differs: {sorted(changed)}")
    root = _validate_private_root(args.private_root, create=False)
    input_lock, input_lock_path, input_state_path = _state_binding(
        root,
        state_name="A2_INPUTS_AND_APPLICABILITY_FROZEN",
        lock_name="a2-input-lock.json",
    )
    _verify_source_integrity(input_lock)
    source = Path(str(input_lock["source_private_root"]))
    previous_method = read_object(
        source / "locks/corrected-v3-method-implementation-lock.json"
    )
    protected = _mapping(previous_method.get("protected_files"), "PR24 protected files")
    for relative, expected in protected.items():
        path = PROJECT_ROOT / str(relative)
        if sha256_file(path) != expected:
            raise ValueError(f"PR #24 protected file changed: {relative}")
    created = utc_now()
    lock_path = root / "locks/a2-implementation-lock.json"
    payload = {
        "schema_version": "hierarchical-a2-shadow-v1.implementation-lock.v1",
        "created_at_utc": created,
        "classification": list(CLASSIFICATION),
        "previous_lock_sha256": sha256_file(input_lock_path),
        "implementation_commit": head,
        "implementation_files": {
            relative: sha256_file(PROJECT_ROOT / relative)
            for relative in sorted(IMPLEMENTATION_PATHS)
        },
        "pr24_protected_files": dict(protected),
        "a2_reference_functions": {
            "historical_m3": hashlib.sha256(
                inspect.getsource(
                    __import__(
                        "ecomsre_rca_unified.frontier", fromlist=["_historical_m3"]
                    )._historical_m3
                ).encode()
            ).hexdigest(),
            "compatible_layers": hashlib.sha256(
                inspect.getsource(
                    __import__(
                        "ecomsre_rca_unified.frontier", fromlist=["_compatible_layers"]
                    )._compatible_layers
                ).encode()
            ).hexdigest(),
        },
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "new_agents": 0,
    }
    write_private_json_create_once(lock_path, payload)
    write_private_json_create_once(
        root / "state/A2_SHADOW_IMPLEMENTATION_FROZEN.json",
        {
            "schema_version": "hierarchical-a2-shadow-v1.state.v1",
            "state": "A2_SHADOW_IMPLEMENTATION_FROZEN",
            "created_at_utc": created,
            "previous_state": "A2_INPUTS_AND_APPLICABILITY_FROZEN",
            "previous_state_record_sha256": sha256_file(input_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print(f"[freeze-implementation] {head} FROZEN", flush=True)
    return 0


def _verify_implementation(root: Path) -> tuple[dict[str, Any], Path, Path]:
    lock, lock_path, state_path = _state_binding(
        root,
        state_name="A2_SHADOW_IMPLEMENTATION_FROZEN",
        lock_name="a2-implementation-lock.json",
    )
    if _git("rev-parse", "HEAD") != lock.get("implementation_commit"):
        raise ValueError("A2 analysis HEAD differs from implementation freeze")
    dirty = set(
        line[3:].split(" -> ")[-1]
        for line in _git("status", "--porcelain=v1", "-uall").splitlines()
        if line
    )
    allowed = {str(path.relative_to(PROJECT_ROOT)) for path in PUBLIC_PATHS}
    if dirty - allowed:
        raise ValueError(f"A2 protected worktree drifted: {sorted(dirty - allowed)}")
    files = _mapping(lock.get("implementation_files"), "implementation files")
    for relative, expected in files.items():
        if sha256_file(PROJECT_ROOT / str(relative)) != expected:
            raise ValueError(f"A2 implementation file drifted: {relative}")
    return lock, lock_path, state_path


def analyze_offline(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    root = _validate_private_root(args.private_root, create=False)
    implementation_lock, implementation_lock_path, implementation_state_path = (
        _verify_implementation(root)
    )
    input_lock, _, _ = _state_binding(
        root,
        state_name="A2_INPUTS_AND_APPLICABILITY_FROZEN",
        lock_name="a2-input-lock.json",
    )
    _, source_vector = _verify_source_integrity(input_lock)
    frontier = load_frontier(args.frontier.resolve(strict=True))
    cases = _load_design_cases(source_vector)
    result = evaluate_applicability_frontier(cases, frontier)
    reference_records = tuple(
        _outcome_record(
            case.fixture,
            gate,
            evaluate_reference_case(case, gate, frontier),
        )
        for gate in ApplicabilityGateId
        for case in cases
    )
    if len(reference_records) != len(cases) * len(ApplicabilityGateId):
        raise ValueError("A2 reference record denominator differs")
    private_reference = root / "results/offline-reference-by-case.jsonl"
    private_frontier = root / "results/offline-frontier.json"
    public_frontier = _frontier_public(result)
    write_private_jsonl_create_once(private_reference, reference_records)
    write_private_json_create_once(private_frontier, public_frontier)
    _write_public(PUBLIC_PATHS[0], canonical_json_bytes(public_frontier))
    _write_public(
        PUBLIC_PATHS[1],
        _json_markdown("RCA A2 Applicability Frontier", public_frontier).encode(
            "utf-8"
        ),
    )
    _write_public(PUBLIC_PATHS[2], _decision_markdown(public_frontier).encode("utf-8"))
    created = utc_now()
    lock_path = root / "locks/a2-offline-applicability-lock.json"
    payload = {
        "schema_version": "hierarchical-a2-shadow-v1.offline-lock.v1",
        "created_at_utc": created,
        "classification": list(CLASSIFICATION),
        "previous_lock_sha256": sha256_file(implementation_lock_path),
        "implementation_commit": implementation_lock["implementation_commit"],
        "design_records": len(cases),
        "gate_outcomes": len(reference_records),
        "selected_gate": None
        if result.selected_gate is None
        else result.selected_gate.value,
        "verdict": public_frontier["verdict"],
        "private_outputs": {
            private_reference.name: sha256_file(private_reference),
            private_frontier.name: sha256_file(private_frontier),
        },
        "public_outputs": {path.name: sha256_file(path) for path in PUBLIC_PATHS[:3]},
        "later_outcomes_semantically_parsed_for_selection": False,
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "live_shadow_executed": False,
        "regression_executed": False,
    }
    write_private_json_create_once(lock_path, payload)
    write_private_json_create_once(
        root / "state/A2_OFFLINE_APPLICABILITY_COMPLETE.json",
        {
            "schema_version": "hierarchical-a2-shadow-v1.state.v1",
            "state": "A2_OFFLINE_APPLICABILITY_COMPLETE",
            "created_at_utc": created,
            "previous_state": "A2_SHADOW_IMPLEMENTATION_FROZEN",
            "previous_state_record_sha256": sha256_file(implementation_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print(
        f"[analyze-offline] selected={public_frontier['selected_gate']} verdict={public_frontier['verdict']}",
        flush=True,
    )
    return 0


def _load_reference_records(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("private A2 reference record must be an object")
            key = (str(value["gate"]), str(value["private_case_key"]))
            if key in result:
                raise ValueError("private A2 reference record is duplicated")
            result[key] = value
    return result


def replay_offline(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    root = _validate_private_root(args.private_root, create=False)
    _verify_implementation(root)
    analysis_lock, analysis_lock_path, analysis_state_path = _state_binding(
        root,
        state_name="A2_OFFLINE_APPLICABILITY_COMPLETE",
        lock_name="a2-offline-applicability-lock.json",
    )
    if (
        analysis_lock.get("selected_gate") is not None
        or analysis_lock.get("verdict") != NO_GATE_VERDICT
    ):
        raise ValueError("this terminal replay path requires no supported Gate")
    input_lock, _, _ = _state_binding(
        root,
        state_name="A2_INPUTS_AND_APPLICABILITY_FROZEN",
        lock_name="a2-input-lock.json",
    )
    _, source_vector = _verify_source_integrity(input_lock)
    frontier = load_frontier(args.frontier.resolve(strict=True))
    cases = _load_design_cases(source_vector)
    references = _load_reference_records(
        root / "results/offline-reference-by-case.jsonl"
    )
    production_records: list[dict[str, object]] = []
    exact = 0
    for gate in ApplicabilityGateId:
        for case in cases:
            outcome = evaluate_production_case(case, gate, frontier)
            record = _outcome_record(case.fixture, gate, outcome)
            expected = references[(gate.value, outcome.private_case_key)]
            comparable = dict(record)
            if comparable == expected:
                exact += 1
            else:
                raise ValueError("A2 production replay differs from Phase 9 reference")
            production_records.append(record)
    all_cases = _read_all_cases(source_vector)
    a0_exact = 0
    for unified_case in all_cases:
        projection = _runtime_projection(unified_case)
        a0 = execute_unified_hierarchical_rca(
            StrongSingleHierarchicalInput(
                initial_root=projection.initial_entity,
                initial_layer=projection.initial_layer,
                initial_hierarchy_path=projection.initial_hierarchy_path,
                fault_type_raw=projection.fault_type_raw,
                fault_ontology_class=projection.fault_ontology_class,
                evidence_visibility=projection.evidence_visibility,
                supporting_evidence_refs=projection.supporting_evidence_refs,
            )
        )
        a0_exact += int(
            a0.final_root == unified_case.initial_entity
            and a0.fault_type_raw == unified_case.fault_type_raw
        )
    replay_summary = {
        "design_cases": len(cases),
        "gates": len(ApplicabilityGateId),
        "reference_to_production_exact": exact,
        "reference_to_production_denominator": len(cases) * len(ApplicabilityGateId),
        "a0_exact": a0_exact,
        "a0_denominator": len(all_cases),
        "match": exact == len(cases) * len(ApplicabilityGateId)
        and a0_exact == len(all_cases),
    }
    private_production = root / "results/offline-production-replay-by-case.jsonl"
    private_summary = root / "results/offline-production-replay.json"
    write_private_jsonl_create_once(private_production, tuple(production_records))
    write_private_json_create_once(private_summary, replay_summary)
    shadow_public = _shadow_public(replay_summary)
    _write_public(PUBLIC_PATHS[3], _inactive_spec_markdown().encode("utf-8"))
    _write_public(PUBLIC_PATHS[4], canonical_json_bytes(shadow_public))
    _write_public(
        PUBLIC_PATHS[5],
        _json_markdown("RCA A2 Live Shadow Development", shadow_public).encode("utf-8"),
    )
    _write_public(PUBLIC_PATHS[6], _human_brief().encode("utf-8"))
    created = utc_now()
    lock_path = root / "locks/a2-implementation-replay-lock.json"
    payload = {
        "schema_version": "hierarchical-a2-shadow-v1.replay-lock.v1",
        "created_at_utc": created,
        "classification": list(CLASSIFICATION),
        "previous_lock_sha256": sha256_file(analysis_lock_path),
        "replay_summary": replay_summary,
        "private_outputs": {
            private_production.name: sha256_file(private_production),
            private_summary.name: sha256_file(private_summary),
        },
        "public_outputs": {path.name: sha256_file(path) for path in PUBLIC_PATHS[3:]},
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "live_shadow_executed": False,
        "promotion_executed": False,
        "regression_executed": False,
    }
    write_private_json_create_once(lock_path, payload)
    write_private_json_create_once(
        root / "state/A2_IMPLEMENTATION_REPLAY_FROZEN.json",
        {
            "schema_version": "hierarchical-a2-shadow-v1.state.v1",
            "state": "A2_IMPLEMENTATION_REPLAY_FROZEN",
            "created_at_utc": created,
            "previous_state": "A2_OFFLINE_APPLICABILITY_COMPLETE",
            "previous_state_record_sha256": sha256_file(analysis_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print(
        f"[replay-offline] G0-G4 {exact}/{len(cases) * len(ApplicabilityGateId)} A0 {a0_exact}/{len(all_cases)} PASS",
        flush=True,
    )
    return 0


def verify_public(args: argparse.Namespace) -> int:
    assert_no_provider_environment()
    root = _validate_private_root(args.private_root, create=False)
    _verify_implementation(root)
    analysis_lock, _, _ = _state_binding(
        root,
        state_name="A2_OFFLINE_APPLICABILITY_COMPLETE",
        lock_name="a2-offline-applicability-lock.json",
    )
    replay_lock, replay_lock_path, replay_state_path = _state_binding(
        root,
        state_name="A2_IMPLEMENTATION_REPLAY_FROZEN",
        lock_name="a2-implementation-replay-lock.json",
    )
    input_lock, _, _ = _state_binding(
        root,
        state_name="A2_INPUTS_AND_APPLICABILITY_FROZEN",
        lock_name="a2-input-lock.json",
    )
    _, source_vector = _verify_source_integrity(input_lock)
    frontier = load_frontier(args.frontier.resolve(strict=True))
    cases = _load_design_cases(source_vector)
    result = evaluate_applicability_frontier(cases, frontier)
    expected_frontier = _frontier_public(result)
    replay_summary = read_object(root / "results/offline-production-replay.json")
    expected_shadow = _shadow_public(replay_summary)
    expected_bytes = {
        PUBLIC_PATHS[0]: canonical_json_bytes(expected_frontier),
        PUBLIC_PATHS[1]: _json_markdown(
            "RCA A2 Applicability Frontier", expected_frontier
        ).encode("utf-8"),
        PUBLIC_PATHS[2]: _decision_markdown(expected_frontier).encode("utf-8"),
        PUBLIC_PATHS[3]: _inactive_spec_markdown().encode("utf-8"),
        PUBLIC_PATHS[4]: canonical_json_bytes(expected_shadow),
        PUBLIC_PATHS[5]: _json_markdown(
            "RCA A2 Live Shadow Development", expected_shadow
        ).encode("utf-8"),
        PUBLIC_PATHS[6]: _human_brief().encode("utf-8"),
    }
    if (
        analysis_lock.get("selected_gate") is not None
        or expected_frontier["verdict"] != NO_GATE_VERDICT
    ):
        raise ValueError(
            "canonical public verification expected the unsupported-Gate path"
        )
    for path, expected in expected_bytes.items():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
            raise ValueError(f"A2 public output is noncanonical: {path.name}")
        scan_public_text(expected.decode("utf-8"))
    if (PROJECT_ROOT / "docs/results/rca-conditional-a2-regression.json").exists() or (
        PROJECT_ROOT / "docs/results/rca-conditional-a2-regression.md"
    ).exists():
        raise ValueError("Regression output exists despite unsupported Gate")
    public_hashes = {path.name: sha256_file(path) for path in PUBLIC_PATHS}
    locked_hashes = {
        **_mapping(analysis_lock.get("public_outputs"), "analysis public outputs"),
        **_mapping(replay_lock.get("public_outputs"), "replay public outputs"),
    }
    if public_hashes != locked_hashes:
        raise ValueError("A2 public output hashes differ from the append-only locks")
    created = utc_now()
    lock_path = root / "locks/a2-public-verification-lock.json"
    payload = {
        "schema_version": "hierarchical-a2-shadow-v1.public-lock.v1",
        "created_at_utc": created,
        "classification": list(CLASSIFICATION),
        "previous_lock_sha256": sha256_file(replay_lock_path),
        "public_outputs": public_hashes,
        "canonical_outputs_exact": len(PUBLIC_PATHS),
        "reference_recomputed_from_design_prefix": True,
        "later_outcomes_semantically_parsed_for_selection": False,
        "leakage_scan_passed": True,
        "provider_objects_constructed": 0,
        "provider_calls": 0,
        "live_shadow_executed": False,
        "promotion_executed": False,
        "regression_executed": False,
        "re2_tt_accessed": False,
        "new_external_data_accessed": False,
        "verdict": NO_GATE_VERDICT,
    }
    write_private_json_create_once(lock_path, payload)
    write_private_json_create_once(
        root / "state/A2_PUBLIC_OUTPUTS_VERIFIED.json",
        {
            "schema_version": "hierarchical-a2-shadow-v1.state.v1",
            "state": "A2_PUBLIC_OUTPUTS_VERIFIED",
            "created_at_utc": created,
            "previous_state": "A2_IMPLEMENTATION_REPLAY_FROZEN",
            "previous_state_record_sha256": sha256_file(replay_state_path),
            "lock_sha256": sha256_file(lock_path),
        },
    )
    print(
        f"[verify-public] canonical {len(PUBLIC_PATHS)}/{len(PUBLIC_PATHS)} PASS",
        flush=True,
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze-inputs")
    freeze.add_argument("--private-root", type=Path, required=True)
    freeze.add_argument("--source-private-root", type=Path, required=True)
    freeze.add_argument("--policy", type=Path, required=True)
    freeze.add_argument("--frontier", type=Path, required=True)
    freeze.set_defaults(handler=freeze_inputs)
    implementation = commands.add_parser("freeze-implementation")
    implementation.add_argument("--private-root", type=Path, required=True)
    implementation.set_defaults(handler=freeze_implementation)
    analyze = commands.add_parser("analyze-offline")
    analyze.add_argument("--private-root", type=Path, required=True)
    analyze.add_argument("--frontier", type=Path, required=True)
    analyze.set_defaults(handler=analyze_offline)
    replay = commands.add_parser("replay-offline")
    replay.add_argument("--private-root", type=Path, required=True)
    replay.add_argument("--frontier", type=Path, required=True)
    replay.set_defaults(handler=replay_offline)
    verify = commands.add_parser("verify-public")
    verify.add_argument("--private-root", type=Path, required=True)
    verify.add_argument("--frontier", type=Path, required=True)
    verify.set_defaults(handler=verify_public)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
