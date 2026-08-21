"""Evaluator-only audit of the target ambiguity frozen by DTA v2.2.3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.ambiguity_set_v224 import (
    resource_target_visibility_signature_v224,
)
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import build_effective_support_policy_v222
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import SOURCE_PREDICATE_CAPABILITIES_V222
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)


SIGNATURE_INPUT_FIELDS_V224 = (
    "runtime_predicates",
    "metric_predicates_and_support",
    "topology_role",
    "already_covered_sources",
    "current_gap_requirements",
    "negative_coverage",
)
_WRONG_TARGET_CASE_IDS = ("d05", "d06", "d08")


class TargetAmbiguityCaseAuditV224(DtaModelV22):
    schema_version: Literal["dta-v22.4.target-ambiguity-case-audit.v1"]
    case_id: str
    mechanism: str
    candidate_hypothesis_ids: tuple[str, ...]
    minimum_missing_clauses: tuple[tuple[str, StrictInt], ...]
    gap_relevant_source_families: tuple[str, ...]
    per_target_available_actions: tuple[str, ...]
    target_visibility_signatures: tuple[str, ...]
    visible_state_equivalence_classes: tuple[tuple[str, ...], ...]
    actual_resource_action_id: str
    actual_resource_action_target: str
    actual_outcome_class: str
    actual_predicate_yield: StrictBool
    truth_target_service: str
    truth_target_ordinal: StrictInt = Field(ge=0)
    resource_ambiguity_set_size: StrictInt = Field(ge=1)
    single_target_preference_available: StrictBool
    signature_truth_consulted: Literal[False]
    signature_future_outcome_consulted: Literal[False]
    case_audit_sha256: str

    @model_validator(mode="after")
    def require_digest(self) -> "TargetAmbiguityCaseAuditV224":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"case_audit_sha256"})
        )
        if self.case_audit_sha256 != expected:
            raise ValueError("v2.2.4 target ambiguity case digest differs")
        return self


class TargetAmbiguityAuditReportV224(DtaModelV22):
    schema_version: Literal["dta-v22.4.target-ambiguity-audit.v1"]
    cases: tuple[TargetAmbiguityCaseAuditV224, ...]
    wrong_target_case_ids: tuple[str, ...]
    signature_input_fields: tuple[str, ...]
    counterfactual_truth_reversals: StrictInt = Field(ge=0)
    ambiguity_audit_passed: StrictBool
    evaluator_only: Literal[True]
    report_sha256: str

    @model_validator(mode="after")
    def require_report(self) -> "TargetAmbiguityAuditReportV224":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.2.4 target ambiguity audit digest differs")
        return self


def _equivalence_classes(
    *, services: tuple[str, ...], signatures: tuple[str, ...]
) -> tuple[tuple[str, ...], ...]:
    grouped: dict[str, list[str]] = {}
    for service, signature in zip(services, signatures, strict=True):
        grouped.setdefault(signature, []).append(service)
    return tuple(sorted((tuple(values) for values in grouped.values()), key=lambda item: item))


def _case_audit(
    *,
    repository_root: Path,
    spec: object,
    truth: dict[str, object],
    frozen_run: dict[str, object],
) -> TargetAmbiguityCaseAuditV224:
    practical_spec = cast(Any, spec)
    case = materialize_practical_case_v22(
        spec=practical_spec,
        repository_root=repository_root,
    )
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _, _, catalog = _bootstrap(case=case, topology=topology, run_id="0" * 32)
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    graph = build_gap_graph_v222(
        policy=build_effective_support_policy_v222(),
        hypothesis_catalog=build_hypothesis_catalog_v22(
            candidate_services=case.candidate_services
        ),
        memory=memory,
        topology_edges=case.topology_edges,
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    resource_predicate_kinds = SOURCE_PREDICATE_CAPABILITIES_V222[
        EvidenceSourceV22.RESOURCES
    ]
    resource_hypotheses = tuple(
        item
        for item in graph.hypotheses
        if not item.complete
        and any(
            gap.predicate_kind in resource_predicate_kinds
            for clause in item.clauses
            for gap in clause.missing_requirements
        )
    )
    signatures = tuple(
        resource_target_visibility_signature_v224(
            service=service,
            candidate_services=case.candidate_services,
            topology_edges=case.topology_edges,
            memory=memory,
            gap_graph=graph,
        )
        for service in case.candidate_services
    )
    classes = _equivalence_classes(
        services=case.candidate_services,
        signatures=signatures,
    )
    events = cast(list[dict[str, object]], frozen_run["adaptive_read_events"])
    event = next(item for item in events if item["source"] == EvidenceSourceV22.RESOURCES.value)
    targets = cast(list[str], event["targets"])
    truth_target = cast(str, truth["expected_root_service"])
    gap_sources = tuple(
        source.value
        for source in EvidenceSourceV22
        if any(
            gap.predicate_kind in SOURCE_PREDICATE_CAPABILITIES_V222[source]
            for hypothesis in resource_hypotheses
            for clause in hypothesis.clauses
            for gap in clause.missing_requirements
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v22.4.target-ambiguity-case-audit.v1",
        "case_id": case.case_id,
        "mechanism": cast(str, truth["expected_mechanism"]),
        "candidate_hypothesis_ids": tuple(item.hypothesis_id for item in resource_hypotheses),
        "minimum_missing_clauses": tuple(
            (item.hypothesis_id, item.minimum_missing_count)
            for item in resource_hypotheses
        ),
        "gap_relevant_source_families": gap_sources,
        "per_target_available_actions": tuple(
            item.action_id
            for item in catalog.registry_actions
            if item.source is EvidenceSourceV22.RESOURCES
        ),
        "target_visibility_signatures": signatures,
        "visible_state_equivalence_classes": classes,
        "actual_resource_action_id": cast(str, event["action_id"]),
        "actual_resource_action_target": targets[0],
        "actual_outcome_class": cast(str, event["outcome_class"]),
        "actual_predicate_yield": event["outcome_class"] == "PREDICATE_YIELD",
        "truth_target_service": truth_target,
        "truth_target_ordinal": case.candidate_services.index(truth_target),
        "resource_ambiguity_set_size": max(len(item) for item in classes),
        "single_target_preference_available": len(set(signatures)) != 1,
        "signature_truth_consulted": False,
        "signature_future_outcome_consulted": False,
    }
    return TargetAmbiguityCaseAuditV224.model_validate(
        {**payload, "case_audit_sha256": semantic_sha256_v22(payload)}
    )


def audit_v223_target_ambiguity_v224(
    *, repository_root: Path
) -> TargetAmbiguityAuditReportV224:
    """Use truth only after visible-state signatures have been constructed."""

    root = repository_root.resolve()
    case_set = load_practical_case_set_v22(
        root / "config/dta-v22-3/evaluation/cases.json"
    )
    truth_raw = json.loads(
        (root / "config/dta-v22-3/evaluation/truth.json").read_bytes()
    )
    truths = {item["case_id"]: item for item in truth_raw["truths"]}
    frozen = json.loads(
        (root / "docs/results/dta-v22-3-admission-dispatch-evaluation.json").read_bytes()
    )
    auto_closed = {
        item["case_id"]: item
        for item in frozen["campaign"]["runs"]
        if item["combination"] == "AUTO_CLOSED"
    }
    resource_ids = tuple(
        item["case_id"]
        for item in truth_raw["truths"]
        if item["expected_mechanism"] in {"CPU_SATURATION", "MEMORY_LEAK"}
    )
    specs = {item.case_id: item for item in case_set.cases}
    cases = tuple(
        _case_audit(
            repository_root=root,
            spec=specs[case_id],
            truth=truths[case_id],
            frozen_run=auto_closed[case_id],
        )
        for case_id in resource_ids
    )
    by_id = {item.case_id: item for item in cases}
    reversals = sum(
        by_id[left].truth_target_ordinal != by_id[right].truth_target_ordinal
        and by_id[left].mechanism == by_id[right].mechanism
        for left, right in (("d05", "d06"), ("d07", "d08"))
    )
    passed = (
        reversals == 2
        and all(
            by_id[case_id].resource_ambiguity_set_size >= 2
            and not by_id[case_id].single_target_preference_available
            and by_id[case_id].actual_resource_action_target
            != by_id[case_id].truth_target_service
            and by_id[case_id].actual_outcome_class == "EMPTY_CAPTURED"
            for case_id in _WRONG_TARGET_CASE_IDS
        )
    )
    digest_payload: dict[str, object] = {
        "schema_version": "dta-v22.4.target-ambiguity-audit.v1",
        "cases": tuple(item.model_dump(mode="json") for item in cases),
        "wrong_target_case_ids": _WRONG_TARGET_CASE_IDS,
        "signature_input_fields": SIGNATURE_INPUT_FIELDS_V224,
        "counterfactual_truth_reversals": reversals,
        "ambiguity_audit_passed": passed,
        "evaluator_only": True,
    }
    return TargetAmbiguityAuditReportV224(
        schema_version="dta-v22.4.target-ambiguity-audit.v1",
        cases=cases,
        wrong_target_case_ids=_WRONG_TARGET_CASE_IDS,
        signature_input_fields=SIGNATURE_INPUT_FIELDS_V224,
        counterfactual_truth_reversals=reversals,
        ambiguity_audit_passed=passed,
        evaluator_only=True,
        report_sha256=semantic_sha256_v22(digest_payload),
    )


__all__ = (
    "SIGNATURE_INPUT_FIELDS_V224",
    "TargetAmbiguityAuditReportV224",
    "TargetAmbiguityCaseAuditV224",
    "audit_v223_target_ambiguity_v224",
)
