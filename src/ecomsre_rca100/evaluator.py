"""Evaluator-only RCA100 parsing and exact paired scoring."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Literal, Mapping

from pydantic import Field, model_validator

from ecomsre_rca100.contracts import RCA100Model
from ecomsre_rca100.entity import EntityCatalog, normalize_entity_name
from ecomsre_rca100.lifecycle import RCA100Schedule, load_strict_json
from ecomsre_rca100.runner import RCA100TerminalRecord, RCA100TerminalStatus
from ecomsre_rca100.statistics import paired_inference


class RCA100GroundTruth(RCA100Model):
    schema_version: Literal["rca100.evaluator-ground-truth.v1"] = (
        "rca100.evaluator-ground-truth.v1"
    )
    source_task_id: str = Field(pattern=r"^t[0-9]{3}$")
    canonical_case_id: str = Field(min_length=1, max_length=1_000)
    target_entity_ids: tuple[str, ...] = Field(default=(), max_length=64)
    target_entity_names: tuple[str, ...] = Field(default=(), max_length=64)
    fault_types: tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_target(self) -> RCA100GroundTruth:
        if not self.target_entity_ids and not self.target_entity_names:
            raise ValueError("RCA100 Ground Truth lacks a root entity")
        return self


class RCA100CaseScore(RCA100Model):
    schema_version: Literal["rca100.evaluator-case-score.v1"] = (
        "rca100.evaluator-case-score.v1"
    )
    source_task_id: str = Field(pattern=r"^t[0-9]{3}$")
    completed: bool
    initial_root_correct: bool
    final_root_correct: bool
    initial_pair_correct: bool
    final_pair_correct: bool
    m3_action: str | None = None
    metrics_projection_status: str | None = None
    fault_category: str
    fault_type_group: str
    root_entity_domain_type: str
    alert_entity_type: str
    initial_rank_group: str
    margin_bin: str
    m3_applicability: str


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    output: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            output.append(item.strip())
        elif isinstance(item, Mapping):
            for key in ("entity_id", "id", "entity_name", "name"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    output.append(candidate.strip())
                    break
    return tuple(dict.fromkeys(output))


def parse_ground_truth(
    source_task_id: str,
    canonical_case_id: str,
    payload: Mapping[str, object],
) -> RCA100GroundTruth:
    raw = payload.get("raw_ground_truth")
    if isinstance(raw, str):
        decoded = json.loads(raw)
    else:
        decoded = raw
    outcome: Mapping[str, object] = {}
    if isinstance(decoded, Mapping) and isinstance(decoded.get("outcome"), Mapping):
        outcome = decoded["outcome"]  # type: ignore[assignment]
    ids = _strings(outcome.get("target_entity_ids"))
    target_entities = outcome.get("target_entities")
    names: list[str] = []
    if isinstance(target_entities, list):
        for item in target_entities:
            if isinstance(item, Mapping):
                entity_id = item.get("entity_id") or item.get("id")
                name = item.get("entity_name") or item.get("name")
                if isinstance(entity_id, str) and entity_id.strip():
                    ids += (entity_id.strip(),)
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
    if not ids and not names:
        names.extend(_strings(payload.get("root_cause_entities")))
    faults = _strings(payload.get("root_cause_types"))
    return RCA100GroundTruth(
        source_task_id=source_task_id,
        canonical_case_id=canonical_case_id,
        target_entity_ids=tuple(dict.fromkeys(ids)),
        target_entity_names=tuple(dict.fromkeys(names)),
        fault_types=faults,
    )


def prediction_correct(
    entity_ref: str | None,
    truth: RCA100GroundTruth,
    catalog: EntityCatalog,
) -> bool:
    if entity_ref is None or entity_ref not in catalog.by_ref:
        return False
    entity = catalog.by_ref[entity_ref]
    predicted_ids = {entity.entity_id}
    predicted_ids.update(item.rsplit("|", 1)[-1] for item in entity.same_as_refs)
    if truth.target_entity_ids:
        return bool(predicted_ids.intersection(truth.target_entity_ids))
    predicted_names = {entity.normalized_name}
    predicted_names.update(
        catalog.by_ref[item].normalized_name
        for item in entity.same_as_refs
        if item in catalog.by_ref
    )
    return bool(
        predicted_names.intersection(
            normalize_entity_name(item) for item in truth.target_entity_names
        )
    )


def fault_correct(fault_type: str | None, truth: RCA100GroundTruth) -> bool:
    if fault_type is None:
        return False
    value = normalize_entity_name(fault_type)
    return value in {normalize_entity_name(item) for item in truth.fault_types}


def _fault_category(canonical_case_id: str) -> str:
    prefix = canonical_case_id.split("-", 1)[0].strip()
    return prefix or "UNAVAILABLE"


def _fault_type_group(truth: RCA100GroundTruth) -> str:
    values = sorted({normalize_entity_name(item) for item in truth.fault_types})
    return " | ".join(values) if values else "UNAVAILABLE"


def _root_entity_domain_type(
    truth: RCA100GroundTruth,
    catalog: EntityCatalog,
) -> str:
    truth_ids = set(truth.target_entity_ids)
    truth_names = {
        normalize_entity_name(item) for item in truth.target_entity_names
    }
    values = {
        f"{entity.domain}/{entity.type}"
        for entity in catalog.by_ref.values()
        if entity.entity_id in truth_ids or entity.normalized_name in truth_names
    }
    return " | ".join(sorted(values)) if values else "UNRESOLVED"


def _initial_rank_group(terminal: RCA100TerminalRecord) -> str:
    if terminal.initial_metrics_rank_or_none is None:
        return "NONE"
    return str(terminal.initial_metrics_rank_or_none)


def _margin_bin(terminal: RCA100TerminalRecord) -> str:
    margin = terminal.normalized_margin
    if margin is None:
        return "NONE"
    if margin < 0.25:
        return "[0.00,0.25)"
    if margin < 0.5:
        return "[0.25,0.50)"
    return "[0.50,+inf)"


def _subgroup_records(
    scores: tuple[RCA100CaseScore, ...],
    attribute: str,
) -> list[dict[str, object]]:
    groups: dict[str, list[RCA100CaseScore]] = {}
    for score in scores:
        key = str(getattr(score, attribute))
        groups.setdefault(key, []).append(score)
    records: list[dict[str, object]] = []
    for group, values in sorted(groups.items()):
        root_damage = sum(
            item.initial_root_correct and not item.final_root_correct
            for item in values
        )
        root_rescue = sum(
            not item.initial_root_correct and item.final_root_correct
            for item in values
        )
        pair_damage = sum(
            item.initial_pair_correct and not item.final_pair_correct
            for item in values
        )
        pair_rescue = sum(
            not item.initial_pair_correct and item.final_pair_correct
            for item in values
        )
        records.append(
            {
                "group": group,
                "denominator": len(values),
                "initial_root_correct": sum(
                    item.initial_root_correct for item in values
                ),
                "final_root_correct": sum(item.final_root_correct for item in values),
                "root_damage": root_damage,
                "root_rescue": root_rescue,
                "root_net_rescue": root_rescue - root_damage,
                "initial_pair_correct": sum(
                    item.initial_pair_correct for item in values
                ),
                "final_pair_correct": sum(item.final_pair_correct for item in values),
                "pair_damage": pair_damage,
                "pair_rescue": pair_rescue,
                "pair_net_rescue": pair_rescue - pair_damage,
            }
        )
    return records


def evaluate_terminals(
    *,
    schedule: RCA100Schedule,
    terminals: Mapping[str, RCA100TerminalRecord],
    truths: Mapping[str, RCA100GroundTruth],
    catalogs: Mapping[str, EntityCatalog],
    alert_entity_types: Mapping[str, str],
) -> tuple[dict[str, object], tuple[RCA100CaseScore, ...]]:
    if (
        len(schedule.records) != 103
        or len(terminals) != 103
        or len(truths) != 103
        or len(alert_entity_types) != 103
    ):
        raise ValueError("RCA100 evaluator requires the fixed 103-case denominator")
    scores: list[RCA100CaseScore] = []
    for record in schedule.records:
        terminal = terminals[record.opaque_case_id]
        truth = truths[record.source_task_id]
        catalog = catalogs[record.source_task_id]
        initial_root = prediction_correct(
            terminal.initial_root_entity_ref, truth, catalog
        )
        final_root = prediction_correct(terminal.final_root_entity_ref, truth, catalog)
        initial_fault = fault_correct(terminal.initial_fault_type, truth)
        final_fault = fault_correct(terminal.final_fault_type, truth)
        scores.append(
            RCA100CaseScore(
                source_task_id=record.source_task_id,
                completed=terminal.status is RCA100TerminalStatus.COMPLETED,
                initial_root_correct=initial_root,
                final_root_correct=final_root,
                initial_pair_correct=initial_root and initial_fault,
                final_pair_correct=final_root and final_fault,
                m3_action=(
                    None if terminal.m3_action is None else terminal.m3_action.value
                ),
                metrics_projection_status=terminal.metrics_projection_status,
                fault_category=_fault_category(truth.canonical_case_id),
                fault_type_group=_fault_type_group(truth),
                root_entity_domain_type=_root_entity_domain_type(truth, catalog),
                alert_entity_type=alert_entity_types[record.source_task_id],
                initial_rank_group=_initial_rank_group(terminal),
                margin_bin=_margin_bin(terminal),
                m3_applicability=(
                    "APPLICABLE"
                    if terminal.m3_action is not None
                    else "NOT_APPLICABLE_TERMINAL_FAILURE"
                ),
            )
        )
    initial_root_vector = tuple(item.initial_root_correct for item in scores)
    final_root_vector = tuple(item.final_root_correct for item in scores)
    root_inference = paired_inference(initial_root_vector, final_root_vector)
    initial_pair_vector = tuple(item.initial_pair_correct for item in scores)
    final_pair_vector = tuple(item.final_pair_correct for item in scores)
    pair_damage = sum(
        before and not after
        for before, after in zip(initial_pair_vector, final_pair_vector)
    )
    pair_rescue = sum(
        not before and after
        for before, after in zip(initial_pair_vector, final_pair_vector)
    )
    actions = Counter(item.m3_action for item in scores)
    correct_override = sum(
        item.m3_action == "OVERRIDE_METRICS_TOP1" and item.final_root_correct
        for item in scores
    )
    wrong_override = sum(
        item.m3_action == "OVERRIDE_METRICS_TOP1" and not item.final_root_correct
        for item in scores
    )
    score_tuple = tuple(scores)
    aggregate: dict[str, object] = {
        "schema_version": "rca100.evaluation-aggregate.v1",
        "fixed_denominator": 103,
        "primary_inference_eligible": 103,
        "root": root_inference.model_dump(mode="json"),
        "pair": {
            "initial_correct": sum(initial_pair_vector),
            "final_correct": sum(final_pair_vector),
            "damage": pair_damage,
            "rescue": pair_rescue,
            "net_rescue": pair_rescue - pair_damage,
        },
        "m3": {
            "keep": actions["KEEP_INITIAL"],
            "override": actions["OVERRIDE_METRICS_TOP1"],
            "correct_override": correct_override,
            "wrong_override": wrong_override,
        },
        "completion": {
            "completed": sum(item.completed for item in scores),
            "failed": sum(not item.completed for item in scores),
        },
        "official_style": {
            "localization": root_inference.final_correct / 103,
            "identification": sum(
                fault_correct(
                    terminals[record.opaque_case_id].final_fault_type,
                    truths[record.source_task_id],
                )
                for record in schedule.records
            )
            / 103,
            "reason_process": None,
            "composite": None,
            "status": "OFFICIAL_COMPOSITE_NOT_AVAILABLE",
        },
        "descriptive_subgroups": {
            "fault_category": _subgroup_records(score_tuple, "fault_category"),
            "fault_type": _subgroup_records(score_tuple, "fault_type_group"),
            "root_entity_domain_type": _subgroup_records(
                score_tuple, "root_entity_domain_type"
            ),
            "alert_entity_type": _subgroup_records(
                score_tuple, "alert_entity_type"
            ),
            "m3_action": _subgroup_records(score_tuple, "m3_action"),
            "m3_applicability": _subgroup_records(
                score_tuple, "m3_applicability"
            ),
            "initial_rank": _subgroup_records(score_tuple, "initial_rank_group"),
            "margin_bin": _subgroup_records(score_tuple, "margin_bin"),
            "metrics_projection_status": _subgroup_records(
                score_tuple, "metrics_projection_status"
            ),
        },
    }
    return aggregate, score_tuple


def _frozen_task_mapping(envelope: object) -> Mapping[str, object]:
    if not isinstance(envelope, Mapping):
        raise ValueError("RCA100 answer mapping envelope must be an object")
    if set(envelope) != {
        "case_id_to_task",
        "seed",
        "task_to_case_id",
        "version",
    }:
        raise ValueError("RCA100 answer mapping envelope schema differs")
    if (
        not isinstance(envelope["case_id_to_task"], Mapping)
        or not isinstance(envelope["seed"], int)
        or isinstance(envelope["seed"], bool)
        or not isinstance(envelope["task_to_case_id"], Mapping)
        or not isinstance(envelope["version"], str)
    ):
        raise ValueError("RCA100 answer mapping envelope types differ")
    return envelope["task_to_case_id"]  # type: ignore[return-value]


def load_answer_key(answer_root: Path) -> dict[str, RCA100GroundTruth]:
    mapping_value = _frozen_task_mapping(
        load_strict_json(answer_root / "mapping.json")
    )
    mapping: dict[str, str] = {}
    for key, value in mapping_value.items():
        if not isinstance(key, str):
            raise ValueError("RCA100 answer mapping task key is invalid")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("RCA100 answer mapping case ID is invalid")
        mapping[key] = value.strip()
    if set(mapping) != {f"t{index:03d}" for index in range(1, 104)}:
        raise ValueError("RCA100 answer mapping coverage differs from 103 tasks")
    truths: dict[str, RCA100GroundTruth] = {}
    for source_id, canonical_id in sorted(mapping.items()):
        value = load_strict_json(answer_root / f"{source_id}.gt.json")
        if not isinstance(value, dict):
            raise ValueError("RCA100 Ground Truth file must be an object")
        truths[source_id] = parse_ground_truth(source_id, canonical_id, value)
    return truths


__all__ = [
    "RCA100CaseScore",
    "RCA100GroundTruth",
    "evaluate_terminals",
    "fault_correct",
    "load_answer_key",
    "parse_ground_truth",
    "prediction_correct",
]
