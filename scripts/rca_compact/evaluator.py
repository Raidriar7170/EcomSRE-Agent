"""Evaluator-only scoring for the frozen B0/C1 compact retrieval run."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import cast

from ecomsre_rca100.entity import (
    EntityCatalog,
    load_entity_catalog,
    normalize_entity_name,
)
from ecomsre_rca100.evaluator import (
    RCA100GroundTruth,
    fault_correct,
    load_answer_key,
    prediction_correct,
)
from ecomsre_rcaeval.dataset import DevCase, DevSystem, discover_dev_cases
from ecomsre_rca_unified.adapters import read_rca_topology
from ecomsre_rca_unified.compact_evaluation import (
    CaseScore,
    aggregate_paired_scores,
    live_tune_gate,
    paired_development_inference,
)
from ecomsre_rca_unified.compact_runtime import (
    Arm,
    CompactTerminalRecord,
    CompactTerminalStatus,
    terminal_status_counts,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.hierarchy import EntityHierarchy


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("evaluator input must be a regular JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evaluator input JSON must be an object")
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _records(schedule_path: Path) -> tuple[dict[str, object], ...]:
    raw = _load_object(schedule_path)
    records = raw.get("records")
    if (
        raw.get("seed") != 20260814
        or not isinstance(records, list)
        or len(records) != 326
    ):
        raise ValueError("evaluator schedule denominator or seed differs")
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("evaluator schedule record is invalid")
    return cast(tuple[dict[str, object], ...], tuple(records))


def _terminals(
    root: Path,
    records: tuple[dict[str, object], ...],
    *,
    schedule_sha256: str,
    implementation_lock_sha256: str,
) -> dict[str, CompactTerminalRecord]:
    paths = tuple(sorted((root / "terminals").glob("*.json")))
    if len(paths) != 326 or any(path.is_symlink() for path in paths):
        raise ValueError("evaluator terminal denominator differs")
    terminals = {
        terminal.run_id: terminal
        for path in paths
        for terminal in (
            CompactTerminalRecord.model_validate_json(path.read_text(encoding="utf-8")),
        )
    }
    if len(terminals) != 326:
        raise ValueError("evaluator terminal identities are not unique")
    for record in records:
        terminal = terminals.get(str(record.get("run_id")))
        expected = (
            record.get("run_id"),
            record.get("opaque_case_id"),
            record.get("pair_position"),
            record.get("arm_position"),
            record.get("arm"),
            schedule_sha256,
            implementation_lock_sha256,
        )
        if (
            terminal is None
            or (
                terminal.run_id,
                terminal.opaque_case_id,
                terminal.pair_position,
                terminal.arm_position,
                terminal.arm.value,
                terminal.schedule_sha256,
                terminal.implementation_lock_sha256,
            )
            != expected
        ):
            raise ValueError(
                "evaluator terminal schedule or implementation binding differs"
            )
    return terminals


def _truth_refs(truth: RCA100GroundTruth, catalog: EntityCatalog) -> frozenset[str]:
    if truth.target_entity_ids:
        truth_ids = set(truth.target_entity_ids)
        return frozenset(
            entity.entity_ref
            for entity in catalog.by_ref.values()
            if (
                {entity.entity_id}
                | {ref.rsplit("|", 1)[-1] for ref in entity.same_as_refs}
            )
            & truth_ids
        )
    truth_names = {normalize_entity_name(item) for item in truth.target_entity_names}
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
    b0: CompactTerminalRecord,
    c1: CompactTerminalRecord,
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

    def dimensions(terminal: CompactTerminalRecord) -> tuple[bool, ...]:
        diagnosis = (
            terminal.diagnosis
            if terminal.status is CompactTerminalStatus.COMPLETED
            else None
        )
        predicted = None if diagnosis is None else diagnosis.root_cause_entity_ref
        exact = prediction_correct(predicted, truth, catalog)
        predicted_service = _service_ancestor(predicted, hierarchy)
        service = exact or (
            predicted_service is not None and predicted_service in truth_services
        )
        fault = fault_correct(
            None if diagnosis is None else diagnosis.fault_type, truth
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
            relations.intersection({"PREDICTED_DESCENDANT", "CONNECTED_DOWNSTREAM"})
        )
        return exact, service, fault, layer, ancestor, descendant, downstream

    b0_dimensions = dimensions(b0)
    c1_dimensions = dimensions(c1)
    return CaseScore(
        opaque_case_id=opaque_case_id,
        dataset="RCA100",
        b0_root=b0_dimensions[0],
        c1_root=c1_dimensions[0],
        b0_service=b0_dimensions[1],
        c1_service=c1_dimensions[1],
        b0_fault=b0_dimensions[2],
        c1_fault=c1_dimensions[2],
        b0_pair=b0_dimensions[0] and b0_dimensions[2],
        c1_pair=c1_dimensions[0] and c1_dimensions[2],
        b0_layer=b0_dimensions[3],
        c1_layer=c1_dimensions[3],
        b0_ancestor=b0_dimensions[4],
        c1_ancestor=c1_dimensions[4],
        b0_descendant=b0_dimensions[5],
        c1_descendant=c1_dimensions[5],
        b0_downstream=b0_dimensions[6],
        c1_downstream=c1_dimensions[6],
    )


def _predicted_service(terminal: CompactTerminalRecord) -> str | None:
    diagnosis = (
        terminal.diagnosis
        if terminal.status is CompactTerminalStatus.COMPLETED
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
    b0: CompactTerminalRecord,
    c1: CompactTerminalRecord,
) -> CaseScore:
    truth_service = normalize_entity_name(case.root_cause_service)
    truth_fault = normalize_entity_name(case.fault)

    def dimensions(terminal: CompactTerminalRecord) -> tuple[bool, bool]:
        diagnosis = (
            terminal.diagnosis
            if terminal.status is CompactTerminalStatus.COMPLETED
            else None
        )
        predicted = _predicted_service(terminal)
        root = (
            predicted is not None and normalize_entity_name(predicted) == truth_service
        )
        fault = (
            diagnosis is not None
            and normalize_entity_name(diagnosis.fault_type) == truth_fault
        )
        return root, fault

    b0_root, b0_fault = dimensions(b0)
    c1_root, c1_fault = dimensions(c1)
    return CaseScore(
        opaque_case_id=opaque_case_id,
        dataset="OBSS",
        b0_root=b0_root,
        c1_root=c1_root,
        b0_service=b0_root,
        c1_service=c1_root,
        b0_fault=b0_fault,
        c1_fault=c1_fault,
        b0_pair=b0_root and b0_fault,
        c1_pair=c1_root and c1_fault,
        b0_layer=b0.status is CompactTerminalStatus.COMPLETED,
        c1_layer=c1.status is CompactTerminalStatus.COMPLETED,
        b0_ancestor=False,
        c1_ancestor=False,
        b0_descendant=False,
        c1_descendant=False,
        b0_downstream=False,
        c1_downstream=False,
    )


def _cost(terminals: Sequence[CompactTerminalRecord]) -> dict[str, object]:
    output: dict[str, object] = {}
    for arm in Arm:
        completed = tuple(
            item
            for item in terminals
            if item.arm is arm and item.status is CompactTerminalStatus.COMPLETED
        )
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
    c1 = cast(dict[str, object], output["c1"])
    b0_input = b0["mean_input_tokens"]
    c1_input = c1["mean_input_tokens"]
    b0_latency = b0["mean_latency_seconds"]
    c1_latency = c1["mean_latency_seconds"]
    output["c1_to_b0_input_token_ratio"] = (
        None
        if not isinstance(b0_input, int | float)
        or not isinstance(c1_input, int | float)
        or b0_input <= 0
        else c1_input / b0_input
    )
    output["c1_to_b0_latency_ratio"] = (
        None
        if not isinstance(b0_latency, int | float)
        or not isinstance(c1_latency, int | float)
        or b0_latency <= 0
        else c1_latency / b0_latency
    )
    return output


def _mechanism(
    terminals: Sequence[CompactTerminalRecord], rows: Sequence[CaseScore]
) -> dict[str, object]:
    scores = {row.opaque_case_id: row for row in rows}
    c1 = tuple(
        item
        for item in terminals
        if item.arm is Arm.C1
        and item.status is CompactTerminalStatus.COMPLETED
        and item.diagnosis is not None
    )
    ranks_by_dataset: dict[str, list[int]] = {"RCA100": [], "OBSS": []}
    selected_buckets: Counter[str] = Counter()
    correct_buckets: Counter[str] = Counter()
    wrong_buckets: Counter[str] = Counter()
    cited_sources: Counter[str] = Counter()
    for terminal in c1:
        diagnosis = terminal.diagnosis
        assert diagnosis is not None
        score = scores[terminal.opaque_case_id]
        rank = diagnosis.selected_candidate_rank
        bucket = diagnosis.selected_allocation_bucket
        if rank is None or bucket is None or diagnosis.root_candidate_id is None:
            raise ValueError("completed C1 terminal lacks strict candidate metadata")
        ranks_by_dataset[score.dataset].append(rank)
        selected_buckets[bucket] += 1
        (correct_buckets if score.c1_root else wrong_buckets)[bucket] += 1
        for evidence_ref in diagnosis.evidence_refs:
            cited_sources[evidence_ref.partition(":")[0].upper()] += 1
    return {
        "candidate_id_valid_completed": len(c1),
        "invalid_candidate_ids": 0,
        "selected_candidate_rank": {
            dataset.casefold(): {
                "count": len(values),
                "median": None if not values else median(values),
            }
            for dataset, values in ranks_by_dataset.items()
        },
        "selected_bucket_distribution": dict(sorted(selected_buckets.items())),
        "correct_root_bucket_distribution": dict(sorted(correct_buckets.items())),
        "wrong_root_bucket_distribution": dict(sorted(wrong_buckets.items())),
        "cited_evidence_source_counts": dict(sorted(cited_sources.items())),
    }


def case_scores_payload(scores: Sequence[CaseScore]) -> dict[str, object]:
    return {
        "schema_version": "compact-retrieval.private-case-scores.v1",
        "records": [asdict(score) for score in scores],
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
    candidate_recall: Mapping[str, object],
) -> tuple[dict[str, object], tuple[CaseScore, ...]]:
    records = _records(schedule_path)
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
            or {left.get("arm"), right.get("arm")} != {"B0", "C1"}
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
                    c1=by_arm["C1"],
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
                    c1=by_arm["C1"],
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
    c1_terminals = tuple(item for item in all_terminals if item.arm is Arm.C1)
    mechanism = _mechanism(all_terminals, rows)
    execution = {
        "b0_terminalized": sum(item.arm is Arm.B0 for item in all_terminals),
        "c1_terminalized": len(c1_terminals),
        "b0_completed": sum(
            item.arm is Arm.B0 and item.status is CompactTerminalStatus.COMPLETED
            for item in all_terminals
        ),
        "c1_completed": sum(
            item.status is CompactTerminalStatus.COMPLETED for item in c1_terminals
        ),
        "c1_invalid_schema": sum(
            item.status is CompactTerminalStatus.INVALID_SCHEMA for item in c1_terminals
        ),
        "http_429": sum(item.failure_code == "HTTP_429" for item in all_terminals),
        "privacy_schedule_failure": sum(
            item.status
            in {
                CompactTerminalStatus.INPUT_PROJECTION_FAILURE,
                CompactTerminalStatus.PRIVACY_FAILURE,
            }
            for item in all_terminals
        ),
        "invalid_candidate_ids": cast(int, mechanism["invalid_candidate_ids"]),
        "semantic_model_operations": sum(
            item.semantic_model_operations for item in all_terminals
        ),
        "specialist_calls": sum(item.specialist_calls for item in all_terminals),
        "fusion_calls": sum(item.fusion_model_calls for item in all_terminals),
        "provider_attempts": sum(item.provider_attempts for item in all_terminals),
        "transport_retries": sum(item.transport_retries for item in all_terminals),
        "status_counts": {
            "b0": terminal_status_counts(
                tuple(item for item in all_terminals if item.arm is Arm.B0)
            ),
            "c1": terminal_status_counts(c1_terminals),
        },
    }
    rca_aggregate = aggregate_paired_scores(rca_rows)
    obss_aggregate = aggregate_paired_scores(obss_rows)
    combined = aggregate_paired_scores(rows)
    cost = _cost(all_terminals)
    gate = live_tune_gate(
        rca100=rca_aggregate,
        obss=obss_aggregate,
        combined=combined,
        execution=cast(Mapping[str, int], execution),
        input_token_ratio=cast(float | None, cost["c1_to_b0_input_token_ratio"]),
        latency_ratio=cast(float | None, cost["c1_to_b0_latency_ratio"]),
    )
    aggregate: dict[str, object] = {
        "schema_version": "compact-retrieval.live-tune-evaluation.v1",
        "classification": [
            "CONSUMED_DEVELOPMENT_EVALUATION",
            "ONE_ARCHITECTURE_CANDIDATE",
            "ONE_PAIRED_LIVE_RUN",
            "NO_RERUN",
            "NOT_EXTERNAL_VALIDATION",
        ],
        "fixed_case_pair_denominator": 163,
        "rca100": rca_aggregate,
        "obss": obss_aggregate,
        "combined": combined,
        "root_inference": paired_development_inference(rows, seed=20260814),
        "candidate_recall": dict(candidate_recall),
        "mechanism": mechanism,
        "execution": execution,
        "cost": cost,
        "gate": gate,
    }
    return aggregate, rows


__all__ = ["case_scores_payload", "evaluate_tune"]
