"""Evaluator-only scoring for frozen B0/H1 terminal records."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import cast

from ecomsre_rca100.entity import EntityCatalog, load_entity_catalog, normalize_entity_name
from ecomsre_rca100.evaluator import (
    RCA100GroundTruth,
    fault_correct,
    load_answer_key,
    prediction_correct,
)
from ecomsre_rcaeval.dataset import DevCase, DevSystem, discover_dev_cases
from ecomsre_rca_unified.adapters import read_rca_topology
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.hierarchy import EntityHierarchy
from ecomsre_rca_unified.live_comparison import Arm
from ecomsre_rca_unified.live_evaluation import (
    CaseScore,
    aggregate_paired_scores,
    paired_development_inference,
    regression_gate,
    tune_gate,
)
from ecomsre_rca_unified.live_runtime import (
    LiveTerminalRecord,
    LiveTerminalStatus,
    terminal_status_counts,
)


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evaluator input JSON must be an object")
    return value


def _records(schedule_path: Path, expected: int) -> tuple[dict[str, object], ...]:
    records = _load_object(schedule_path).get("records")
    if not isinstance(records, list) or len(records) != expected:
        raise ValueError("evaluator schedule denominator differs")
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("evaluator schedule record is invalid")
    return cast(tuple[dict[str, object], ...], tuple(records))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _terminals(
    root: Path,
    records: tuple[dict[str, object], ...],
    *,
    schedule_sha256: str,
    implementation_lock_sha256: str,
) -> dict[str, LiveTerminalRecord]:
    terminals = {
        terminal.run_id: terminal
        for path in sorted((root / "terminals").glob("*.json"))
        for terminal in (
            LiveTerminalRecord.model_validate_json(path.read_text(encoding="utf-8")),
        )
    }
    if len(terminals) != len(records):
        raise ValueError("evaluator terminal denominator differs")
    for record in records:
        run_id = record.get("run_id")
        terminal = terminals.get(str(run_id))
        expected = (
            record.get("run_id"),
            record.get("opaque_case_id"),
            record.get("split"),
            record.get("pair_position"),
            record.get("arm_position"),
            record.get("arm"),
            schedule_sha256,
            implementation_lock_sha256,
        )
        if terminal is None or (
            terminal.run_id,
            terminal.opaque_case_id,
            terminal.split,
            terminal.pair_position,
            terminal.arm_position,
            terminal.arm.value,
            terminal.schedule_sha256,
            terminal.implementation_lock_sha256,
        ) != expected:
            raise ValueError("evaluator terminal schedule/implementation binding differs")
    return terminals


def _truth_refs(
    truth: RCA100GroundTruth, catalog: EntityCatalog
) -> frozenset[str]:
    if truth.target_entity_ids:
        truth_ids = set(truth.target_entity_ids)
        return frozenset(
            entity.entity_ref
            for entity in catalog.by_ref.values()
            if ({entity.entity_id} | {ref.rsplit("|", 1)[-1] for ref in entity.same_as_refs})
            & truth_ids
        )
    truth_names = {
        normalize_entity_name(item) for item in truth.target_entity_names
    }
    return frozenset(
        entity.entity_ref
        for entity in catalog.by_ref.values()
        if (
            {entity.normalized_name}
            | {
                catalog.by_ref[ref].normalized_name
                for ref in entity.same_as_refs
                if ref in catalog.by_ref
            }
        )
        & truth_names
    )


def _service_ancestor(entity_ref: str | None, hierarchy: EntityHierarchy) -> str | None:
    if entity_ref is None or entity_ref not in hierarchy.nodes:
        return None
    if hierarchy.nodes[entity_ref].layer is CanonicalEntityLayer.SERVICE:
        return entity_ref
    return hierarchy.service_ancestor(entity_ref)


def _relation(
    predicted: str | None,
    truth_ref: str | None,
    hierarchy: EntityHierarchy,
    *,
    exact: bool,
) -> str:
    if exact:
        return "EXACT_MATCH"
    if predicted is None or truth_ref is None:
        return "UNRESOLVED"
    return hierarchy.relation(predicted, truth_ref)


def _rca_score(
    *,
    opaque_case_id: str,
    source_key: str,
    b0: LiveTerminalRecord,
    h1: LiveTerminalRecord,
    truth: RCA100GroundTruth,
    cases_root: Path,
) -> CaseScore:
    catalog = load_entity_catalog(cases_root / source_key / "topology.json")
    topology = read_rca_topology(cases_root / source_key / "topology.json")
    hierarchy = EntityHierarchy(
        nodes=topology.nodes,
        parent_edges=topology.parent_edges,
        same_as_edges=topology.same_as_edges,
        directed_edges=topology.directed_edges,
        undirected_edges=topology.undirected_edges,
    )
    truth_refs = _truth_refs(truth, catalog)
    truth_services = {
        service
        for truth_ref in truth_refs
        if (service := _service_ancestor(truth_ref, hierarchy)) is not None
    }
    truth_layers = {
        hierarchy.nodes[truth_ref].layer
        for truth_ref in truth_refs
        if truth_ref in hierarchy.nodes
    }

    def dimensions(terminal: LiveTerminalRecord) -> tuple[bool, ...]:
        diagnosis = (
            terminal.diagnosis
            if terminal.status is LiveTerminalStatus.COMPLETED
            else None
        )
        predicted = None if diagnosis is None else diagnosis.root_cause_entity_ref
        exact = prediction_correct(predicted, truth, catalog)
        predicted_service = _service_ancestor(predicted, hierarchy)
        service = exact or (
            predicted_service is not None and predicted_service in truth_services
        )
        fault = fault_correct(
            None if diagnosis is None else diagnosis.fault_type,
            truth,
        )
        predicted_layer = (
            None
            if predicted is None or predicted not in hierarchy.nodes
            else hierarchy.nodes[predicted].layer
        )
        layer = predicted_layer is not None and predicted_layer in truth_layers
        relations = {
            _relation(predicted, truth_ref, hierarchy, exact=exact)
            for truth_ref in truth_refs
        }
        ancestor = "PREDICTED_ANCESTOR" in relations
        descendant = "PREDICTED_DESCENDANT" in relations
        downstream = bool(
            relations.intersection(
                {"PREDICTED_DESCENDANT", "CONNECTED_DOWNSTREAM"}
            )
        )
        return exact, service, fault, layer, ancestor, descendant, downstream

    b0_dimensions = dimensions(b0)
    h1_dimensions = dimensions(h1)
    return CaseScore(
        opaque_case_id=opaque_case_id,
        dataset="RCA100",
        b0_root=b0_dimensions[0],
        h1_root=h1_dimensions[0],
        b0_service=b0_dimensions[1],
        h1_service=h1_dimensions[1],
        b0_fault=b0_dimensions[2],
        h1_fault=h1_dimensions[2],
        b0_pair=b0_dimensions[0] and b0_dimensions[2],
        h1_pair=h1_dimensions[0] and h1_dimensions[2],
        b0_layer=b0_dimensions[3],
        h1_layer=h1_dimensions[3],
        b0_ancestor=b0_dimensions[4],
        h1_ancestor=h1_dimensions[4],
        b0_descendant=b0_dimensions[5],
        h1_descendant=h1_dimensions[5],
        b0_downstream=b0_dimensions[6],
        h1_downstream=h1_dimensions[6],
    )


def _predicted_service(terminal: LiveTerminalRecord) -> str | None:
    diagnosis = (
        terminal.diagnosis
        if terminal.status is LiveTerminalStatus.COMPLETED
        else None
    )
    if diagnosis is None:
        return None
    prefix = "apm|apm.service|"
    if not diagnosis.root_cause_entity_ref.startswith(prefix):
        return None
    return diagnosis.root_cause_entity_ref[len(prefix) :]


def _obss_score(
    *,
    opaque_case_id: str,
    case: DevCase,
    b0: LiveTerminalRecord,
    h1: LiveTerminalRecord,
) -> CaseScore:
    truth_service = normalize_entity_name(case.root_cause_service)
    truth_fault = normalize_entity_name(case.fault)

    def dimensions(terminal: LiveTerminalRecord) -> tuple[bool, bool]:
        diagnosis = (
            terminal.diagnosis
            if terminal.status is LiveTerminalStatus.COMPLETED
            else None
        )
        predicted = _predicted_service(terminal)
        root = (
            predicted is not None
            and normalize_entity_name(predicted) == truth_service
        )
        fault = (
            diagnosis is not None
            and normalize_entity_name(diagnosis.fault_type) == truth_fault
        )
        return root, fault

    b0_root, b0_fault = dimensions(b0)
    h1_root, h1_fault = dimensions(h1)
    return CaseScore(
        opaque_case_id=opaque_case_id,
        dataset="OBSS",
        b0_root=b0_root,
        h1_root=h1_root,
        b0_service=b0_root,
        h1_service=h1_root,
        b0_fault=b0_fault,
        h1_fault=h1_fault,
        b0_pair=b0_root and b0_fault,
        h1_pair=h1_root and h1_fault,
        b0_layer=b0.status is LiveTerminalStatus.COMPLETED,
        h1_layer=h1.status is LiveTerminalStatus.COMPLETED,
        b0_ancestor=False,
        h1_ancestor=False,
        b0_descendant=False,
        h1_descendant=False,
        b0_downstream=False,
        h1_downstream=False,
    )


def _cost(terminals: Sequence[LiveTerminalRecord]) -> dict[str, object]:
    output: dict[str, object] = {}
    arm_values: dict[Arm, tuple[LiveTerminalRecord, ...]] = {}
    for arm in Arm:
        completed = tuple(
            item
            for item in terminals
            if item.arm is arm and item.status is LiveTerminalStatus.COMPLETED
        )
        arm_values[arm] = completed
        known = tuple(
            item
            for item in completed
            if item.input_tokens_if_known is not None
            and item.output_tokens_if_known is not None
        )
        output[arm.value.casefold()] = {
            "completed": len(completed),
            "known_usage": len(known),
            "mean_input_tokens": (
                None
                if len(known) != len(completed) or not completed
                else sum(cast(int, item.input_tokens_if_known) for item in known)
                / len(known)
            ),
            "mean_output_tokens": (
                None
                if len(known) != len(completed) or not completed
                else sum(cast(int, item.output_tokens_if_known) for item in known)
                / len(known)
            ),
            "mean_latency_seconds": (
                None
                if not completed
                else sum(item.latency_seconds for item in completed) / len(completed)
            ),
        }
    b0 = cast(dict[str, object], output["b0"])
    h1 = cast(dict[str, object], output["h1"])
    b0_input = b0["mean_input_tokens"]
    h1_input = h1["mean_input_tokens"]
    b0_latency = b0["mean_latency_seconds"]
    h1_latency = h1["mean_latency_seconds"]
    output["h1_to_b0_input_token_ratio"] = (
        None
        if not isinstance(b0_input, int | float)
        or not isinstance(h1_input, int | float)
        or b0_input <= 0
        else h1_input / b0_input
    )
    output["h1_to_b0_latency_ratio"] = (
        None
        if not isinstance(b0_latency, int | float)
        or not isinstance(h1_latency, int | float)
        or b0_latency <= 0
        else h1_latency / b0_latency
    )
    return output


def case_scores_payload(scores: Sequence[CaseScore]) -> dict[str, object]:
    return {
        "records": [asdict(score) for score in scores],
        "schema_version": "strong-single-hierarchical-live.private-case-scores.v1",
    }


def evaluate_tune(
    *,
    schedule_path: Path,
    terminals_root: Path,
    rca_cases_root: Path,
    ob_root: Path,
    ss_root: Path,
    answer_root: Path,
    implementation_lock_sha256: str,
) -> tuple[dict[str, object], tuple[CaseScore, ...]]:
    records = _records(schedule_path, 326)
    terminals_by_run = _terminals(
        terminals_root,
        records,
        schedule_sha256=_sha(schedule_path),
        implementation_lock_sha256=implementation_lock_sha256,
    )
    truths = load_answer_key(answer_root)
    obss_cases = discover_dev_cases(ob_root, DevSystem.RE2_OB) + discover_dev_cases(
        ss_root, DevSystem.RE2_SS
    )
    obss_index = {item.case_id: item for item in obss_cases}
    if len(obss_index) != 180:
        raise ValueError("evaluator OB/SS source denominator differs")
    scores: list[CaseScore] = []
    for index in range(0, len(records), 2):
        left, right = records[index : index + 2]
        if (
            left.get("opaque_case_id") != right.get("opaque_case_id")
            or {left.get("arm"), right.get("arm")} != {"B0", "H1"}
            or left.get("source") != right.get("source")
            or left.get("source_key") != right.get("source_key")
        ):
            raise ValueError("evaluator pair schedule integrity failed")
        by_arm = {
            str(record["arm"]): terminals_by_run[str(record["run_id"])]
            for record in (left, right)
        }
        opaque = str(left["opaque_case_id"])
        source = str(left["source"])
        source_key = str(left["source_key"])
        if source == "RCA100":
            scores.append(
                _rca_score(
                    opaque_case_id=opaque,
                    source_key=source_key,
                    b0=by_arm["B0"],
                    h1=by_arm["H1"],
                    truth=truths[source_key],
                    cases_root=rca_cases_root,
                )
            )
        elif source == "OBSS" and source_key in obss_index:
            scores.append(
                _obss_score(
                    opaque_case_id=opaque,
                    case=obss_index[source_key],
                    b0=by_arm["B0"],
                    h1=by_arm["H1"],
                )
            )
        else:
            raise ValueError("evaluator schedule source is invalid")
    rows = tuple(scores)
    rca_rows = tuple(item for item in rows if item.dataset == "RCA100")
    obss_rows = tuple(item for item in rows if item.dataset == "OBSS")
    if len(rca_rows) != 103 or len(obss_rows) != 60:
        raise ValueError("evaluator TUNE dataset denominators differ")
    all_terminals = tuple(terminals_by_run.values())
    execution = {
        "terminal_count": len(all_terminals),
        "rca100_completed_b0": sum(
            terminals_by_run[str(record["run_id"])].status
            is LiveTerminalStatus.COMPLETED
            for record in records
            if record["source"] == "RCA100" and record["arm"] == "B0"
        ),
        "rca100_completed_h1": sum(
            terminals_by_run[str(record["run_id"])].status
            is LiveTerminalStatus.COMPLETED
            for record in records
            if record["source"] == "RCA100" and record["arm"] == "H1"
        ),
        "obss_completed_b0": sum(
            terminals_by_run[str(record["run_id"])].status
            is LiveTerminalStatus.COMPLETED
            for record in records
            if record["source"] == "OBSS" and record["arm"] == "B0"
        ),
        "obss_completed_h1": sum(
            terminals_by_run[str(record["run_id"])].status
            is LiveTerminalStatus.COMPLETED
            for record in records
            if record["source"] == "OBSS" and record["arm"] == "H1"
        ),
        "http_429": sum(item.failure_code == "HTTP_429" for item in all_terminals),
        "schema_privacy_schedule_failure": sum(
            item.status
            in {
                LiveTerminalStatus.INVALID_SCHEMA,
                LiveTerminalStatus.PROTOCOL_VIOLATION,
                LiveTerminalStatus.INPUT_PROJECTION_FAILURE,
                LiveTerminalStatus.PRIVACY_FAILURE,
            }
            for item in all_terminals
        ),
        "semantic_model_operations": sum(
            item.semantic_model_operations for item in all_terminals
        ),
        "specialist_calls": sum(item.specialist_calls for item in all_terminals),
        "fusion_calls": sum(item.fusion_model_calls for item in all_terminals),
        "provider_attempts": sum(item.provider_attempts for item in all_terminals),
        "transport_retries": sum(item.transport_retries for item in all_terminals),
        "status_counts": terminal_status_counts(all_terminals),
    }
    rca_aggregate = aggregate_paired_scores(rca_rows)
    obss_aggregate = aggregate_paired_scores(obss_rows)
    combined = aggregate_paired_scores(rows)
    cost = _cost(all_terminals)
    gate = tune_gate(
        rca100=rca_aggregate,
        obss=obss_aggregate,
        combined=combined,
        execution=cast(dict[str, int], execution),
        h1_input_token_ratio=cast(float | None, cost["h1_to_b0_input_token_ratio"]),
        h1_latency_ratio=cast(float | None, cost["h1_to_b0_latency_ratio"]),
    )
    aggregate: dict[str, object] = {
        "schema_version": "strong-single-live.tune-evaluation.v1",
        "classification": [
            "CONSUMED_DEVELOPMENT_EVALUATION",
            "NOT_EXTERNAL_VALIDATION",
            "DESCRIPTIVE_DEVELOPMENT_INFERENCE",
        ],
        "fixed_case_pair_denominator": 163,
        "rca100": rca_aggregate,
        "obss": obss_aggregate,
        "combined": combined,
        "root_inference": paired_development_inference(rows, seed=20260812),
        "execution": execution,
        "cost": cost,
        "gate": gate,
    }
    return aggregate, rows


def evaluate_regression(
    *,
    schedule_path: Path,
    terminals_root: Path,
    ob_root: Path,
    ss_root: Path,
    implementation_lock_sha256: str,
) -> tuple[dict[str, object], tuple[CaseScore, ...]]:
    records = _records(schedule_path, 240)
    terminals_by_run = _terminals(
        terminals_root,
        records,
        schedule_sha256=_sha(schedule_path),
        implementation_lock_sha256=implementation_lock_sha256,
    )
    cases = discover_dev_cases(ob_root, DevSystem.RE2_OB) + discover_dev_cases(
        ss_root, DevSystem.RE2_SS
    )
    case_index = {item.case_id: item for item in cases}
    if len(case_index) != 180:
        raise ValueError("regression evaluator OB/SS source denominator differs")
    scores: list[CaseScore] = []
    for index in range(0, len(records), 2):
        left, right = records[index : index + 2]
        if (
            left.get("opaque_case_id") != right.get("opaque_case_id")
            or {left.get("arm"), right.get("arm")} != {"B0", "H1"}
            or left.get("source") != "OBSS"
            or right.get("source") != "OBSS"
            or left.get("source_key") != right.get("source_key")
        ):
            raise ValueError("regression evaluator pair schedule integrity failed")
        by_arm = {
            str(record["arm"]): terminals_by_run[str(record["run_id"])]
            for record in (left, right)
        }
        source_key = str(left["source_key"])
        if source_key not in case_index:
            raise ValueError("regression evaluator source case is absent")
        scores.append(
            _obss_score(
                opaque_case_id=str(left["opaque_case_id"]),
                case=case_index[source_key],
                b0=by_arm["B0"],
                h1=by_arm["H1"],
            )
        )
    rows = tuple(scores)
    if len(rows) != 120:
        raise ValueError("regression evaluator denominator differs")
    terminals = tuple(terminals_by_run.values())
    execution = {
        "terminal_count": len(terminals),
        "admitted_arms": sum(
            item.status is not LiveTerminalStatus.NOT_ADMITTED
            for item in terminals
        ),
        "completed_b0": sum(
            item.status is LiveTerminalStatus.COMPLETED and item.arm is Arm.B0
            for item in terminals
        ),
        "completed_h1": sum(
            item.status is LiveTerminalStatus.COMPLETED and item.arm is Arm.H1
            for item in terminals
        ),
        "http_429": sum(item.failure_code == "HTTP_429" for item in terminals),
        "schema_privacy_schedule_failure": sum(
            item.status
            in {
                LiveTerminalStatus.INVALID_SCHEMA,
                LiveTerminalStatus.PROTOCOL_VIOLATION,
                LiveTerminalStatus.INPUT_PROJECTION_FAILURE,
                LiveTerminalStatus.PRIVACY_FAILURE,
            }
            for item in terminals
        ),
        "semantic_model_operations": sum(
            item.semantic_model_operations for item in terminals
        ),
        "specialist_calls": sum(item.specialist_calls for item in terminals),
        "fusion_calls": sum(item.fusion_model_calls for item in terminals),
        "provider_attempts": sum(item.provider_attempts for item in terminals),
        "transport_retries": sum(item.transport_retries for item in terminals),
        "status_counts": terminal_status_counts(terminals),
    }
    paired = aggregate_paired_scores(rows)
    cost = _cost(terminals)
    gate = regression_gate(
        aggregate=paired,
        execution=cast(dict[str, int], execution),
        h1_input_token_ratio=cast(float | None, cost["h1_to_b0_input_token_ratio"]),
        h1_latency_ratio=cast(float | None, cost["h1_to_b0_latency_ratio"]),
    )
    aggregate: dict[str, object] = {
        "schema_version": "strong-single-live.regression-evaluation.v1",
        "classification": [
            "CONSUMED_DEVELOPMENT_EVALUATION",
            "NOT_EXTERNAL_VALIDATION",
            "DESCRIPTIVE_DEVELOPMENT_INFERENCE",
        ],
        "fixed_case_pair_denominator": 120,
        "obss": paired,
        "root_inference": paired_development_inference(rows, seed=20260813),
        "execution": execution,
        "cost": cost,
        "gate": gate,
    }
    return aggregate, rows


__all__ = ["case_scores_payload", "evaluate_regression", "evaluate_tune"]
