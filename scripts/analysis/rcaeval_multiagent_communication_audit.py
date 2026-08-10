"""Offline RCAEval Multi-Agent communication and evidence arbitration audit.

This module intentionally has no Provider import, Provider construction, network
path, or semantic-generation path.  It reconstructs deterministic runtime inputs
from preserved development artifacts and raw bounded OB/SS evidence, then emits
case-level private diagnostics and aggregate-only public reports.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import fmean
from typing import Any, Literal

from ecomsre_rcaeval.adapter import ArchitectureContextBuilder
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.dataset import DevCase, DevSystem, discover_dev_cases
from ecomsre_rcaeval.scoring import normalize_indicator
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.indicator_evaluation import build_runtime_metric_candidates


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_LOCK_PATH = PROJECT_ROOT / "config/rcaeval-adaptive-v2/model-lock.json"
AGENT_CONFIG_PATH = PROJECT_ROOT / "config/rcaeval-adaptive-v2/agent.json"
CLASSIFICATION = (
    "CONSUMED_OBSS_DEVELOPMENT",
    "POST_HOC_DIAGNOSTIC",
    "NOT_EXTERNAL_VALIDATION",
    "NOT_PRIMARY_INFERENCE",
    "NO_PROVIDER_CALLS",
)
OBSERVABILITY = Literal[
    "DIRECTLY_OBSERVABLE",
    "DETERMINISTICALLY_RECONSTRUCTABLE",
    "UNOBSERVABLE_FROM_PRESERVED_ARTIFACTS",
]
SUFFICIENCY_CLASSES = (
    "METRICS_ONLY_SUFFICIENT",
    "LOGS_CAN_COMPARE",
    "TRACE_CAN_COMPARE",
    "LOGS_AND_TRACE_CAN_COMPARE",
    "NO_SPECIALIST_SOURCE_CAN_COMPARE",
)
FAILURE_MECHANISMS = (
    "CANDIDATE_NOT_RETRIEVED",
    "GATE_MISSED_ERROR",
    "WRONG_SOURCE_SELECTED",
    "SOURCE_SIGNAL_ABSENT",
    "ALTERNATIVE_NOT_VISIBLE_TO_SPECIALIST",
    "ROUTING_RATIONALE_NOT_TRANSMITTED",
    "METRICS_PROVENANCE_NOT_TRANSMITTED",
    "INITIAL_RATIONALE_NOT_TRANSMITTED",
    "SPECIALIST_TASK_SOURCE_MISMATCH",
    "SPECIALIST_REASONING_ERROR_WITH_SUFFICIENT_INPUT",
    "CAUSAL_ROLE_MISCLASSIFICATION",
    "EVIDENCE_ROLE_MISCLASSIFICATION",
    "OUTPUT_COMPRESSION_LOSS",
    "FUSION_OVERCONSTRAINED",
    "FUSION_CORRECTLY_REJECTED_WEAK_OUTPUT",
    "INDICATOR_ONLY_FAILURE",
    "NO_ACTIONABLE_ALTERNATIVE",
)
ARCHITECTURE_DECISIONS = (
    "METRICS_ARBITRATION",
    "METRICS_PLUS_TRACE_VERIFICATION",
    "COMMUNICATION_REPAIRED_CROSS_SOURCE_VERIFIER",
    "STRONG_SINGLE_RECOMMENDED",
)
_FAULTS = ("cpu", "mem", "disk", "delay", "loss", "socket")
_PROVIDER_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_MODEL",
)
_FORBIDDEN_PUBLIC_KEYS = {
    "case_id",
    "run_id",
    "service",
    "service_name",
    "initial_service",
    "alternative_service",
    "truth_service",
    "root_cause_service",
    "evidence_ref",
    "evidence_refs",
    "private_case_key",
    "private_path",
    "raw_provider_output",
    "api_key",
    "credentials",
}
_FORBIDDEN_PUBLIC_TEXT = (
    "/users/",
    "/home/",
    "/private/",
    ".ecomsre-private",
    "bearer ",
    "tt-case-",
)
_CONCRETE_REF = re.compile(r"(?:metric|log|trace|indicator):[0-9]{4}", re.IGNORECASE)
_KNOWN_PRIVATE_SERVICE = re.compile(
    r"^(?:checkout|frontend|catalog|[a-z]+service|carts|catalogue|orders|payment|user)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CaseMaterial:
    private_case_key: str
    system: str
    root_cause_service: str
    fault: str
    instance: str
    case: DevCase


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("offline audit JSON input must be an object")
    return value


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def _continuous(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "minimum": None if not values else min(values),
        "mean": None if not values else fmean(values),
        "maximum": None if not values else max(values),
    }


def _canonical_indicator(value: object) -> str:
    text = str(value)
    if text in {"cpu", "mem", "diskio", "latency", "socket"}:
        return text
    return normalize_indicator(text)  # type: ignore[arg-type]


def _write_json(path: Path, value: object, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(encoded, encoding="utf-8")
    path.chmod(0o600 if private else 0o644)


def _write_jsonl(path: Path, rows: Iterable[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    path.chmod(0o600)


def _write_text(path: Path, value: str, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")
    path.chmod(0o600 if private else 0o644)


def assert_public_payload(payload: object) -> None:
    """Fail closed on identifiers, concrete services/refs, paths, and credentials."""

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).casefold() in _FORBIDDEN_PUBLIC_KEYS:
                    raise ValueError(f"public payload contains forbidden key: {key}")
                walk(nested)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                walk(nested)
            return
        if isinstance(value, str):
            lowered = value.casefold()
            if any(marker in lowered for marker in _FORBIDDEN_PUBLIC_TEXT):
                raise ValueError("public payload contains forbidden local material")
            if _CONCRETE_REF.search(value):
                raise ValueError("public payload contains forbidden concrete reference")
            if _KNOWN_PRIVATE_SERVICE.fullmatch(value):
                raise ValueError("public payload contains forbidden concrete service")

    walk(payload)


def assert_rate_contract(payload: object) -> None:
    """Require every emitted ratio object to carry raw counts and a value."""

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            keys = set(value)
            if keys.intersection({"numerator", "denominator", "value"}):
                if not {"numerator", "denominator", "value"}.issubset(keys):
                    raise ValueError("public rate lacks numerator/denominator/value")
                numerator = value["numerator"]
                denominator = value["denominator"]
                ratio_value = value["value"]
                if type(numerator) is not int or type(denominator) is not int:
                    raise ValueError("public rate counts must be integers")
                expected = None if denominator == 0 else numerator / denominator
                if expected is None:
                    if ratio_value is not None:
                        raise ValueError("zero-denominator public rate must be null")
                elif not isinstance(ratio_value, (int, float)) or not math.isclose(
                    float(ratio_value), expected, rel_tol=0.0, abs_tol=1e-12
                ):
                    raise ValueError("public rate value differs from raw counts")
            for nested in value.values():
                walk(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                walk(nested)

    walk(payload)


def build_artifact_sufficiency() -> dict[str, dict[str, str]]:
    """Classify semantic observability from terminal, sidecar, and raw evidence."""

    directly = "DIRECTLY_OBSERVABLE"
    reconstruct = "DETERMINISTICALLY_RECONSTRUCTABLE"
    unobservable = "UNOBSERVABLE_FROM_PRESERVED_ARTIFACTS"
    return {
        "initial_diagnosis": {
            "classification": directly,
            "basis": "Completed Adaptive v2 terminal diagnosis.initial_diagnosis.",
        },
        "initial_evidence_refs": {
            "classification": directly,
            "basis": "Completed terminal Initial Diagnosis refs.",
        },
        "initial_confidence": {
            "classification": directly,
            "basis": "Completed terminal Initial Diagnosis confidence.",
        },
        "metrics_ranking": {
            "classification": reconstruct,
            "basis": "Frozen raw Metrics plus tracked F0 indicator configuration.",
        },
        "metrics_rank_score_margin": {
            "classification": reconstruct,
            "basis": "Rank and margin are partly stored; full ranking is reconstructed.",
        },
        "gate_feature_snapshot": {
            "classification": reconstruct,
            "basis": "Flattened v2 Gate decision is stored; omitted raw inputs are reconstructed.",
        },
        "gate_reason_codes": {
            "classification": directly,
            "basis": "Completed terminal Gate decision reason codes.",
        },
        "gate_route": {
            "classification": directly,
            "basis": "Completed terminal Gate route.",
        },
        "metrics_alternative": {
            "classification": reconstruct,
            "basis": "Stored for Candidate-5; deterministically reconstructed for Candidate-3/4.",
        },
        "specialist_input_envelope": {
            "classification": reconstruct,
            "basis": "Typed tracked contract plus Initial and bounded source evidence; sidecar stores only request hash.",
        },
        "specialist_visible_services": {
            "classification": reconstruct,
            "basis": "Exact typed projection from single-source bounded evidence.",
        },
        "specialist_visible_refs": {
            "classification": reconstruct,
            "basis": "Exact typed projection from single-source bounded evidence.",
        },
        "specialist_output": {
            "classification": directly,
            "basis": "Completed terminal free hypotheses or pairwise verification.",
        },
        "pairwise_output": {
            "classification": directly,
            "basis": "Candidate-5 completed terminal pairwise verification.",
        },
        "trace_hypotheses": {
            "classification": directly,
            "basis": "Trace outputs share specialist_hypotheses and are separated by source when called.",
        },
        "fusion_input": {
            "classification": reconstruct,
            "basis": "Initial, Gate, Metrics, Specialist output, and visible Logs refs are preserved or reconstructed.",
        },
        "fusion_reason": {
            "classification": directly,
            "basis": "Completed terminal deterministic Fusion reason codes.",
        },
        "final_diagnosis": {
            "classification": directly,
            "basis": "Completed terminal final Root and Indicator.",
        },
        "indicator_action": {
            "classification": directly,
            "basis": "Completed terminal Indicator resolution action.",
        },
        "provider_request_body": {
            "classification": unobservable,
            "basis": "Provider sidecars retain request SHA only, not request bodies.",
        },
        "failed_terminal_semantic_outputs": {
            "classification": unobservable,
            "basis": "Failed terminal result is null and sidecars contain no semantic body.",
        },
    }


def build_communication_graph() -> dict[str, list[dict[str, object]]]:
    stages: list[dict[str, object]] = [
        {"stage": "RAW_BOUNDED_EVIDENCE", "authority": "SOURCE_BOUND"},
        {"stage": "STRONG_SINGLE_INITIAL", "authority": "MODEL_PROPOSAL"},
        {"stage": "GATE_FEATURES", "authority": "DETERMINISTIC"},
        {"stage": "GATE_ROUTE", "authority": "DETERMINISTIC"},
        {"stage": "METRICS_ALTERNATIVE", "authority": "DETERMINISTIC"},
        {"stage": "SPECIALIST_INPUT", "authority": "SOURCE_BOUND"},
        {"stage": "SPECIALIST_OUTPUT", "authority": "MODEL_PROPOSAL"},
        {"stage": "DETERMINISTIC_FUSION", "authority": "DETERMINISTIC"},
        {"stage": "INDICATOR_RESOLUTION", "authority": "DETERMINISTIC"},
        {"stage": "FINAL_DIAGNOSIS", "authority": "TERMINAL"},
    ]
    edges: list[dict[str, object]] = [
        {
            "edge": "EVIDENCE_TO_INITIAL",
            "fields_transmitted": ["incident", "full_architecture_context"],
            "fields_omitted": ["ground_truth"],
            "evidence_authority": "METRICS_LOGS_TRACES_CONTEXT",
            "service_authority": "CONTEXT_VISIBLE",
            "candidate_authority": "NONE",
            "source_restrictions": "Strong Single receives the frozen context projection.",
            "normalization": "RCAEval bounded adapter",
            "compression": "top-six bounded source summaries",
        },
        {
            "edge": "INITIAL_TO_GATE",
            "fields_transmitted": [
                "initial_diagnosis",
                "metrics_ranking",
                "evidence_support_flag",
                "source_conflict_flags",
                "availability_flags",
            ],
            "fields_omitted": ["ground_truth"],
            "evidence_authority": "DETERMINISTIC_FEATURES",
            "service_authority": "INITIAL_AND_METRICS",
            "candidate_authority": "RANKED_METRICS",
            "source_restrictions": "No Provider call.",
            "normalization": "typed Gate inputs",
            "compression": "raw evidence reduced to booleans/ranks/margin",
        },
        {
            "edge": "GATE_TO_SPECIALIST_FREE",
            "fields_transmitted": [
                "incident",
                "initial_context_without_initial_refs",
                "single_source_evidence",
                "visible_services",
                "visible_refs",
            ],
            "fields_omitted": [
                "gate_reason_codes",
                "metrics_ranking",
                "metrics_alternative",
                "initial_evidence_refs",
            ],
            "evidence_authority": "SINGLE_SOURCE",
            "service_authority": "INITIAL_PLUS_SOURCE_VISIBLE",
            "candidate_authority": "FREE_GENERATION",
            "source_restrictions": "Logs or Traces only.",
            "normalization": "typed SpecialistInput",
            "compression": "source-only projection",
        },
        {
            "edge": "METRICS_TO_LOGS_PAIRWISE",
            "fields_transmitted": [
                "incident",
                "initial_service",
                "alternative_service",
                "initial_indicator",
                "logs_evidence",
                "visible_refs",
            ],
            "fields_omitted": [
                "metrics_alternative_rank",
                "metrics_alternative_score",
                "metrics_margin",
                "initial_metrics_rank",
                "gate_reason_codes",
                "initial_confidence",
                "initial_explanation",
                "initial_evidence_refs",
                "alternative_selection_reason",
            ],
            "evidence_authority": "LOGS_ONLY",
            "service_authority": "CROSS_SOURCE_IDENTITIES_ALLOWED",
            "candidate_authority": "DETERMINISTIC_METRICS_IDENTITY_ONLY",
            "source_restrictions": "Citations must be Logs-visible.",
            "normalization": "typed LogsPairwiseInput",
            "compression": "Metrics provenance removed",
        },
        {
            "edge": "TRACE_SPECIALIST_INPUT",
            "fields_transmitted": [
                "incident",
                "initial_context_without_initial_refs",
                "trace_evidence",
                "visible_services",
                "visible_refs",
            ],
            "fields_omitted": [
                "metrics_alternative",
                "metrics_provenance",
                "gate_reason_codes",
            ],
            "evidence_authority": "TRACES_ONLY",
            "service_authority": "INITIAL_PLUS_TRACE_VISIBLE",
            "candidate_authority": "FREE_GENERATION",
            "source_restrictions": "Trace citations only.",
            "normalization": "typed SpecialistInput",
            "compression": "per-service anomaly summaries",
        },
        {
            "edge": "SPECIALIST_TO_FUSION",
            "fields_transmitted": [
                "initial_diagnosis",
                "gate_decision",
                "metrics_ranking",
                "metrics_alternative",
                "specialist_output",
                "visible_logs_refs",
            ],
            "fields_omitted": ["explicit_source_visibility_limitation"],
            "evidence_authority": "MIXED_TYPED_INPUT",
            "service_authority": "DETERMINISTIC_ALLOWLIST",
            "candidate_authority": "METRICS_BOUND",
            "source_restrictions": "No Provider Fusion call.",
            "normalization": "typed deterministic Fusion",
            "compression": "reason/action only in terminal",
        },
        {
            "edge": "FUSION_TO_INDICATOR_FINAL",
            "fields_transmitted": [
                "selected_root",
                "initial_indicator",
                "metric_indicator_candidates",
            ],
            "fields_omitted": ["specialist_free_text"],
            "evidence_authority": "METRICS_BOUND",
            "service_authority": "FUSION_SELECTED",
            "candidate_authority": "DETERMINISTIC",
            "source_restrictions": "Indicator remains deterministic.",
            "normalization": "canonical indicator",
            "compression": "final action/root/indicator",
        },
    ]
    return {"stages": stages, "edges": edges}


def build_communication_knowledge_matrix() -> dict[str, dict[str, bool]]:
    fields = (
        "initial_service",
        "alternative_service",
        "metrics_rank_score",
        "gate_reasons",
        "initial_confidence",
        "initial_evidence_refs",
        "logs_evidence",
        "trace_evidence",
        "ground_truth",
    )

    def row(**values: bool) -> dict[str, bool]:
        return {field: bool(values.get(field, False)) for field in fields}

    return {
        "INITIAL": row(logs_evidence=True, trace_evidence=True),
        "GATE": row(
            initial_service=True,
            metrics_rank_score=True,
            initial_confidence=True,
            initial_evidence_refs=True,
        ),
        "LOGS_FREE_SPECIALIST": row(
            initial_service=True,
            initial_confidence=True,
            logs_evidence=True,
        ),
        "LOGS_PAIRWISE": row(
            initial_service=True,
            alternative_service=True,
            logs_evidence=True,
        ),
        "TRACE_SPECIALIST": row(
            initial_service=True,
            initial_confidence=True,
            trace_evidence=True,
        ),
        "FUSION": row(
            initial_service=True,
            alternative_service=True,
            metrics_rank_score=True,
            gate_reasons=True,
            initial_confidence=True,
            initial_evidence_refs=True,
        ),
    }


def _rank_of(service: str, ranking: Sequence[Sequence[object]]) -> int | None:
    for index, item in enumerate(ranking, start=1):
        if len(item) >= 1 and str(item[0]) == service:
            return index
    return None


def _alternative_service(row: Mapping[str, Any]) -> str | None:
    alternative = row.get("metrics_alternative")
    if not isinstance(alternative, Mapping):
        return None
    value = alternative.get("alternative_service")
    return value if isinstance(value, str) else None


def _source_services(row: Mapping[str, Any], key: str) -> set[str]:
    values = row.get(key)
    if not isinstance(values, (list, tuple)):
        return set()
    return {
        str(item.get("service"))
        for item in values
        if isinstance(item, Mapping) and isinstance(item.get("service"), str)
    }


def _source_refs(row: Mapping[str, Any], key: str) -> set[str]:
    values = row.get(key)
    if not isinstance(values, (list, tuple)):
        return set()
    return {
        str(item.get("evidence_ref"))
        for item in values
        if isinstance(item, Mapping) and isinstance(item.get("evidence_ref"), str)
    }


def classify_evidence_sufficiency(row: Mapping[str, Any]) -> dict[str, Any]:
    initial = str(row.get("initial_service"))
    truth = str(row.get("truth_service"))
    alternative = _alternative_service(row)
    ranking = row.get("metrics_ranking", ())
    metrics_rank = _rank_of(truth, ranking if isinstance(ranking, Sequence) else ())
    if metrics_rank is None:
        raise ValueError(
            "source sufficiency requires True Root in the Metrics candidate set"
        )
    logs = _source_services(row, "logs_evidence")
    traces = _source_services(row, "trace_evidence")
    correct_alternative = alternative == truth
    truth_retrieved = metrics_rank is not None
    logs_compare = truth_retrieved and {initial, truth}.issubset(logs)
    traces_compare = truth_retrieved and {initial, truth}.issubset(traces)
    if logs_compare and traces_compare:
        classification = "LOGS_AND_TRACE_CAN_COMPARE"
    elif logs_compare:
        classification = "LOGS_CAN_COMPARE"
    elif traces_compare:
        classification = "TRACE_CAN_COMPARE"
    elif correct_alternative:
        classification = "NO_SPECIALIST_SOURCE_CAN_COMPARE"
    else:
        classification = "METRICS_ONLY_SUFFICIENT"
    if classification not in SUFFICIENCY_CLASSES:
        raise AssertionError("invalid evidence sufficiency classification")
    return {
        "class": classification,
        "metrics_top1_truth_visible": metrics_rank == 1,
        "metrics_top2_truth_visible": metrics_rank is not None and metrics_rank <= 2,
        "metrics_top3_truth_visible": metrics_rank is not None and metrics_rank <= 3,
        "metrics_top6_truth_visible": metrics_rank is not None and metrics_rank <= 6,
        "metrics_alternative_is_truth": correct_alternative,
        "truth_logs_visible": truth in logs,
        "truth_trace_visible": truth in traces,
        "initial_logs_visible": initial in logs,
        "initial_trace_visible": initial in traces,
        "alternative_logs_visible": alternative in logs if alternative else False,
        "alternative_trace_visible": alternative in traces if alternative else False,
        "initial_alternative_logs_co_visible": (
            alternative is not None and {initial, alternative}.issubset(logs)
        ),
        "initial_alternative_trace_co_visible": (
            alternative is not None and {initial, alternative}.issubset(traces)
        ),
    }


def _pairwise_has_correct_preference(row: Mapping[str, Any]) -> bool:
    verification = row.get("logs_pairwise_verification")
    return bool(
        isinstance(verification, Mapping)
        and verification.get("preference") == "ALTERNATIVE"
        and _alternative_service(row) == row.get("truth_service")
    )


def _pairwise_currently_authorizes(row: Mapping[str, Any]) -> bool:
    verification = row.get("logs_pairwise_verification")
    if not isinstance(verification, Mapping):
        return False
    cited = set(
        tuple(verification.get("supporting_evidence_refs", ()))
        + tuple(verification.get("contradicting_evidence_refs", ()))
    )
    return bool(
        verification.get("preference") == "ALTERNATIVE"
        and verification.get("alternative_role") == "ROOT_CANDIDATE"
        and verification.get("supporting_evidence_refs")
        and (
            verification.get("initial_role") == "PROPAGATED_SYMPTOM"
            or verification.get("contradicting_evidence_refs")
        )
        and cited.issubset(_source_refs(row, "logs_evidence"))
    )


def _free_has_correct_alternative(row: Mapping[str, Any], *, fusion_ready: bool) -> bool:
    hypotheses = row.get("specialist_hypotheses", ())
    if not isinstance(hypotheses, (list, tuple)):
        return False
    truth = row.get("truth_service")
    for item in hypotheses:
        if not isinstance(item, Mapping) or item.get("service") != truth:
            continue
        if not fusion_ready:
            return True
        if (
            item.get("causal_role") == "ROOT_CANDIDATE"
            and item.get("supporting_evidence_refs")
        ):
            return True
    return False


def classify_failure_mechanism(row: Mapping[str, Any]) -> dict[str, Any]:
    """Assign one primary category using retrieval→Gate→source→message→model→Fusion."""

    initial = row.get("initial_service")
    truth = row.get("truth_service")
    if initial == truth:
        primary = (
            "INDICATOR_ONLY_FAILURE"
            if row.get("initial_indicator") != row.get("truth_indicator")
            else "NO_ACTIONABLE_ALTERNATIVE"
        )
        return {"primary": primary, "secondary": ()}
    ranking = row.get("metrics_ranking", ())
    if _rank_of(str(truth), ranking if isinstance(ranking, Sequence) else ()) is None:
        return {"primary": "CANDIDATE_NOT_RETRIEVED", "secondary": ()}
    if row.get("gate_route") == "DIRECT_RETURN":
        return {"primary": "GATE_MISSED_ERROR", "secondary": ()}

    sufficiency = classify_evidence_sufficiency(row)
    logs_compare = sufficiency["class"] in {
        "LOGS_CAN_COMPARE",
        "LOGS_AND_TRACE_CAN_COMPARE",
    }
    trace_compare = sufficiency["class"] in {
        "TRACE_CAN_COMPARE",
        "LOGS_AND_TRACE_CAN_COMPARE",
    }
    route = row.get("gate_route")
    if route == "VERIFY_LOGS" and not logs_compare:
        primary = "WRONG_SOURCE_SELECTED" if trace_compare else "SOURCE_SIGNAL_ABSENT"
        return {"primary": primary, "secondary": ("SPECIALIST_TASK_SOURCE_MISMATCH",)}
    if route == "VERIFY_TRACES" and not trace_compare:
        primary = "WRONG_SOURCE_SELECTED" if logs_compare else "SOURCE_SIGNAL_ABSENT"
        return {"primary": primary, "secondary": ("SPECIALIST_TASK_SOURCE_MISMATCH",)}
    if route == "VERIFY_BOTH" and not (logs_compare or trace_compare):
        return {
            "primary": "SOURCE_SIGNAL_ABSENT",
            "secondary": ("SPECIALIST_TASK_SOURCE_MISMATCH",),
        }

    secondary = (
        "ROUTING_RATIONALE_NOT_TRANSMITTED",
        "METRICS_PROVENANCE_NOT_TRANSMITTED",
        "INITIAL_RATIONALE_NOT_TRANSMITTED",
    )
    if row.get("candidate") == "candidate-4":
        source_key = "trace_evidence" if route == "VERIFY_TRACES" else "logs_evidence"
        visible = _source_services(row, source_key) | {str(initial)}
        if _alternative_service(row) not in visible:
            return {"primary": "ALTERNATIVE_NOT_VISIBLE_TO_SPECIALIST", "secondary": secondary}
        if not _free_has_correct_alternative(row, fusion_ready=False):
            return {
                "primary": "SPECIALIST_REASONING_ERROR_WITH_SUFFICIENT_INPUT",
                "secondary": secondary,
            }
        if not _free_has_correct_alternative(row, fusion_ready=True):
            return {"primary": "CAUSAL_ROLE_MISCLASSIFICATION", "secondary": secondary}
    else:
        if not _pairwise_has_correct_preference(row):
            return {
                "primary": "SPECIALIST_REASONING_ERROR_WITH_SUFFICIENT_INPUT",
                "secondary": secondary,
            }
        verification = row.get("logs_pairwise_verification")
        if isinstance(verification, Mapping) and verification.get("alternative_role") != "ROOT_CANDIDATE":
            return {"primary": "CAUSAL_ROLE_MISCLASSIFICATION", "secondary": secondary}
        if isinstance(verification, Mapping) and not verification.get("supporting_evidence_refs"):
            return {"primary": "EVIDENCE_ROLE_MISCLASSIFICATION", "secondary": secondary}

    if row.get("final_service") == truth:
        return {"primary": "NO_ACTIONABLE_ALTERNATIVE", "secondary": secondary}
    if _f1_override(row) and _alternative_service(row) == truth:
        return {"primary": "FUSION_OVERCONSTRAINED", "secondary": secondary}
    return {"primary": "FUSION_CORRECTLY_REJECTED_WEAK_OUTPUT", "secondary": secondary}


def audit_free_specialist(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    calls = [
        row
        for row in rows
        if row.get("completed")
        and row.get("gate_route") in {"VERIFY_LOGS", "VERIFY_BOTH"}
    ]
    hypotheses: list[tuple[Mapping[str, Any], Mapping[str, Any], int]] = []
    for row in calls:
        values = row.get("specialist_hypotheses", ())
        if not isinstance(values, (list, tuple)):
            continue
        logs_values = [
            item
            for item in values
            if isinstance(item, Mapping) and item.get("source") == "logs"
        ]
        for rank, item in enumerate(logs_values, start=1):
            hypotheses.append((row, item, rank))
    truth_hypotheses = [
        item
        for item in hypotheses
        if item[1].get("service") == item[0].get("truth_service")
    ]
    correct = [
        item
        for item in truth_hypotheses
        if item[0].get("initial_service") != item[0].get("truth_service")
    ]
    root_role = [item for item in hypotheses if item[1].get("causal_role") == "ROOT_CANDIDATE"]
    symptom_role = [
        item for item in hypotheses if item[1].get("causal_role") == "PROPAGATED_SYMPTOM"
    ]
    root_role_correct = sum(
        item[1].get("service") == item[0].get("truth_service")
        for item in root_role
    )
    symptom_role_correct = sum(
        item[1].get("service") == item[0].get("initial_service")
        and item[0].get("initial_service") != item[0].get("truth_service")
        for item in symptom_role
    )
    services = {
        str(item[1].get("service")) for item in hypotheses if item[1].get("service")
    }
    return {
        "specialist_calls": len(calls),
        "hypothesis_count": len(hypotheses),
        "unique_service_count": len(services),
        "correct_alternative_rank1": sum(item[2] == 1 for item in correct),
        "correct_alternative_any_rank": len({str(item[0].get("private_case_key")) for item in correct}),
        "truth_hypothesis_rank1_all_calls": sum(
            item[2] == 1 for item in truth_hypotheses
        ),
        "truth_hypothesis_any_rank_all_calls": len(
            {
                str(item[0].get("private_case_key"))
                for item in truth_hypotheses
            }
        ),
        "initial_hypothesis_count": sum(
            item[1].get("service") == item[0].get("initial_service") for item in hypotheses
        ),
        "truth_hypothesis_count": len(truth_hypotheses),
        "root_candidate_role_truth_alignment": _ratio(root_role_correct, len(root_role)),
        "propagated_symptom_role_heuristic_alignment": _ratio(
            symptom_role_correct, len(symptom_role)
        ),
        "causal_role_oracle": "ROOT_IDENTITY_PLUS_NON_ROOT_HEURISTIC_ONLY",
        "supporting_ref_count": sum(len(tuple(item[1].get("supporting_evidence_refs", ()))) for item in hypotheses),
        "contradicting_ref_count": sum(len(tuple(item[1].get("contradicting_evidence_refs", ()))) for item in hypotheses),
        "score_correctness": {
            "correct": _continuous(
                [
                    float(item[1].get("score", 0.0))
                    for item in truth_hypotheses
                ]
            ),
            "incorrect": _continuous(
                [
                    float(item[1].get("score", 0.0))
                    for item in hypotheses
                    if item not in truth_hypotheses
                ]
            ),
        },
    }


def _expected_pairwise_preference(row: Mapping[str, Any]) -> str:
    initial_correct = row.get("initial_service") == row.get("truth_service")
    alternative_correct = _alternative_service(row) == row.get("truth_service")
    if alternative_correct and not initial_correct:
        return "ALTERNATIVE"
    if initial_correct and not alternative_correct:
        return "INITIAL"
    return "INCONCLUSIVE"


def audit_pairwise_specialist(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    calls = [
        row
        for row in rows
        if row.get("completed")
        and isinstance(row.get("logs_pairwise_verification"), Mapping)
    ]
    preferences = Counter(
        str(row["logs_pairwise_verification"].get("preference")) for row in calls
    )
    sufficient = {
        str(row.get("private_case_key")): (
            _alternative_service(row) is not None
            and {
                str(row.get("initial_service")),
                str(_alternative_service(row)),
            }.issubset(_source_services(row, "logs_evidence"))
        )
        for row in calls
    }
    correct_preferences = sum(
        row["logs_pairwise_verification"].get("preference")
        == _expected_pairwise_preference(row)
        for row in calls
    )
    initial_role_correct = sum(
        (
            row["logs_pairwise_verification"].get("initial_role") == "ROOT_CANDIDATE"
            if row.get("initial_service") == row.get("truth_service")
            else row["logs_pairwise_verification"].get("initial_role")
            == "PROPAGATED_SYMPTOM"
        )
        for row in calls
    )
    alternative_role_correct = sum(
        (
            row["logs_pairwise_verification"].get("alternative_role") == "ROOT_CANDIDATE"
            if _alternative_service(row) == row.get("truth_service")
            else row["logs_pairwise_verification"].get("alternative_role")
            != "ROOT_CANDIDATE"
        )
        for row in calls
    )
    inconclusive = [
        row
        for row in calls
        if row["logs_pairwise_verification"].get("preference") == "INCONCLUSIVE"
    ]
    bins = {"[0.00,0.50)": [0, 0], "[0.50,0.75)": [0, 0], "[0.75,1.01)": [0, 0]}
    for row in calls:
        confidence = float(row["logs_pairwise_verification"].get("confidence", 0.0))
        key = "[0.00,0.50)" if confidence < 0.5 else "[0.50,0.75)" if confidence < 0.75 else "[0.75,1.01)"
        bins[key][1] += 1
        bins[key][0] += row["logs_pairwise_verification"].get("preference") == _expected_pairwise_preference(row)
    feasibility_fields = (
        "candidate_identity_present",
        "candidate_provenance_present",
        "candidate_strength_present",
        "gate_reason_present",
        "initial_rationale_present",
        "source_evidence_for_initial_present",
        "source_evidence_for_alternative_present",
        "both_candidates_comparable",
    )
    feasibility_counts = Counter[str]()
    for row in calls:
        logs = _source_services(row, "logs_evidence")
        initial = str(row.get("initial_service"))
        alternative = _alternative_service(row)
        flags = {
            "candidate_identity_present": alternative is not None,
            "candidate_provenance_present": False,
            "candidate_strength_present": False,
            "gate_reason_present": False,
            "initial_rationale_present": False,
            "source_evidence_for_initial_present": initial in logs,
            "source_evidence_for_alternative_present": alternative in logs if alternative else False,
            "both_candidates_comparable": alternative is not None and {initial, alternative}.issubset(logs),
        }
        feasibility_counts.update(key for key, value in flags.items() if value)
    alternative_rows = [
        row
        for row in calls
        if row["logs_pairwise_verification"].get("preference") == "ALTERNATIVE"
    ]
    alternative_conditions = {
        "alternative_role_root_candidate": sum(
            row["logs_pairwise_verification"].get("alternative_role") == "ROOT_CANDIDATE"
            for row in alternative_rows
        ),
        "initial_role_propagated_symptom": sum(
            row["logs_pairwise_verification"].get("initial_role") == "PROPAGATED_SYMPTOM"
            for row in alternative_rows
        ),
        "support_refs_nonempty": sum(
            bool(row["logs_pairwise_verification"].get("supporting_evidence_refs"))
            for row in alternative_rows
        ),
        "contradict_refs_nonempty": sum(
            bool(row["logs_pairwise_verification"].get("contradicting_evidence_refs"))
            for row in alternative_rows
        ),
        "refs_visible": sum(
            set(
                tuple(row["logs_pairwise_verification"].get("supporting_evidence_refs", ()))
                + tuple(row["logs_pairwise_verification"].get("contradicting_evidence_refs", ()))
            ).issubset(_source_refs(row, "logs_evidence"))
            for row in alternative_rows
        ),
        "metrics_alternative_correct": sum(
            _alternative_service(row) == row.get("truth_service") for row in alternative_rows
        ),
    }
    return {
        "pairwise_calls": len(calls),
        "preference_distribution": {
            value: preferences[value] for value in ("INITIAL", "ALTERNATIVE", "INCONCLUSIVE")
        },
        "preference_accuracy": _ratio(correct_preferences, len(calls)),
        "initial_role_heuristic_alignment": _ratio(initial_role_correct, len(calls)),
        "alternative_role_truth_alignment": _ratio(alternative_role_correct, len(calls)),
        "causal_role_oracle": "ROOT_IDENTITY_PLUS_NON_ROOT_HEURISTIC_ONLY",
        "supporting_ref_count": sum(
            len(tuple(row["logs_pairwise_verification"].get("supporting_evidence_refs", ())))
            for row in calls
        ),
        "contradicting_ref_count": sum(
            len(tuple(row["logs_pairwise_verification"].get("contradicting_evidence_refs", ())))
            for row in calls
        ),
        "confidence_bins": {
            key: _ratio(value[0], value[1]) for key, value in bins.items()
        },
        "inconclusive_insufficient_source": sum(
            not sufficient[str(row.get("private_case_key"))] for row in inconclusive
        ),
        "inconclusive_despite_sufficient_source": sum(
            sufficient[str(row.get("private_case_key"))] for row in inconclusive
        ),
        "communication_feasibility": {
            "call_count": len(calls),
            "field_rates": {
                field: _ratio(feasibility_counts[field], len(calls))
                for field in feasibility_fields
            },
        },
        "alternative_case_count": len(alternative_rows),
        "alternative_override_conditions": alternative_conditions,
    }


def _normalized_fusion(
    action: str, final_root_service: object, reason_codes: Sequence[object]
) -> dict[str, Any]:
    return {
        "action": action,
        "final_root_service": str(final_root_service),
        "reason_codes": tuple(str(value) for value in reason_codes),
    }


def _f1_override(row: Mapping[str, Any]) -> bool:
    verification = row.get("logs_pairwise_verification")
    return bool(
        isinstance(verification, Mapping)
        and verification.get("preference") == "ALTERNATIVE"
        and verification.get("alternative_role") == "ROOT_CANDIDATE"
        and verification.get("supporting_evidence_refs")
    )


def _f2_override(row: Mapping[str, Any]) -> bool:
    alternative = row.get("metrics_alternative")
    verification = row.get("logs_pairwise_verification")
    return bool(
        isinstance(verification, Mapping)
        and verification.get("preference") == "ALTERNATIVE"
        and verification.get("supporting_evidence_refs")
        and isinstance(alternative, Mapping)
        and alternative.get("alternative_rank") == 1
        and float(alternative.get("metrics_margin", 0.0)) >= 0.5
    )


def _f3_override(row: Mapping[str, Any]) -> bool:
    alternative = _alternative_service(row)
    hypotheses = row.get("specialist_hypotheses", ())
    if not alternative or not isinstance(hypotheses, (list, tuple)):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("source") == "traces"
        and item.get("service") == alternative
        and item.get("causal_role") == "ROOT_CANDIDATE"
        and item.get("supporting_evidence_refs")
        for item in hypotheses
    )


def replay_current_fusion(row: Mapping[str, Any]) -> dict[str, Any]:
    """Replay the checked-in Candidate-5 deterministic root Fusion contract."""

    initial = row.get("initial_service")
    alternative = _alternative_service(row)
    if row.get("gate_route") == "DIRECT_RETURN":
        reason = (
            "KEEP_DIRECT"
            if row.get("candidate") == "candidate-5"
            else "INITIAL_NOT_UNSTABLE"
        )
        return _normalized_fusion("KEEP_INITIAL", initial, (reason,))
    if not row.get("gate_initial_unstable", True):
        return _normalized_fusion(
            "KEEP_INITIAL", initial, ("INITIAL_NOT_UNSTABLE",)
        )
    if alternative is None:
        return _normalized_fusion(
            "KEEP_INITIAL", initial, ("NO_METRICS_ALTERNATIVE",)
        )
    route = row.get("gate_route")
    hypotheses = row.get("specialist_hypotheses", ())

    def replay_free() -> dict[str, Any]:
        if route == "DIRECT_RETURN" or not row.get("gate_initial_unstable", True):
            return _normalized_fusion(
                "KEEP_INITIAL", initial, ("INITIAL_NOT_UNSTABLE",)
            )
        ranking = row.get("metrics_ranking", ())
        top_two = {
            str(item[0])
            for item in ranking[:2]
            if isinstance(item, (list, tuple)) and item
        }
        values = hypotheses if isinstance(hypotheses, (list, tuple)) else ()
        roots = [
            item
            for item in values
            if isinstance(item, Mapping)
            and item.get("causal_role") == "ROOT_CANDIDATE"
            and item.get("service") != initial
            and item.get("service") in top_two
            and float(item.get("score", 0.0)) >= 0.95
            and item.get("supporting_evidence_refs")
        ]
        root_services = {str(item.get("service")) for item in roots}
        if len(root_services) != 1:
            reason = (
                "NO_SINGLE_STRONG_ALTERNATIVE"
                if not root_services
                else "SPECIALIST_ALTERNATIVES_CONFLICT"
            )
            return _normalized_fusion("KEEP_INITIAL", initial, (reason,))
        contradictions = [
            item
            for item in values
            if isinstance(item, Mapping)
            and item.get("service") == initial
            and (
                item.get("contradicting_evidence_refs")
                or (
                    item.get("causal_role") == "PROPAGATED_SYMPTOM"
                    and item.get("supporting_evidence_refs")
                )
            )
        ]
        if not contradictions:
            return _normalized_fusion(
                "KEEP_INITIAL", initial, ("INITIAL_NOT_EXPLICITLY_CONTRADICTED",)
            )
        return _normalized_fusion(
            "OVERRIDE_INITIAL",
            next(iter(root_services)),
            ("STRONG_AUTHORIZED_ALTERNATIVE",),
        )

    if route == "VERIFY_TRACES":
        trace_decision = replay_free()
        if trace_decision["action"] == "OVERRIDE_INITIAL":
            trace_decision["reason_codes"] = ("TRACE_ALTERNATIVE_OVERRIDE",)
        return trace_decision

    verification = row.get("logs_pairwise_verification")
    if route == "VERIFY_BOTH":
        if not isinstance(verification, Mapping):
            return _normalized_fusion(
                "KEEP_INITIAL", initial, ("LOGS_PAIRWISE_INCONCLUSIVE",)
            )
        trace_agrees = bool(
            isinstance(hypotheses, (list, tuple))
            and any(
                isinstance(item, Mapping)
                and item.get("source") == "traces"
                and item.get("service") == alternative
                and item.get("causal_role") == "ROOT_CANDIDATE"
                and float(item.get("score", 0.0)) >= 0.95
                and item.get("supporting_evidence_refs")
                for item in hypotheses
            )
        )
        if _pairwise_currently_authorizes(row) and trace_agrees:
            return _normalized_fusion(
                "OVERRIDE_INITIAL", alternative, ("BOTH_SOURCES_AGREE_OVERRIDE",)
            )
        return _normalized_fusion(
            "KEEP_INITIAL", initial, ("BOTH_SOURCES_DO_NOT_AGREE",)
        )

    if isinstance(verification, Mapping):
        if _pairwise_currently_authorizes(row):
            return _normalized_fusion(
                "OVERRIDE_INITIAL",
                alternative,
                ("LOGS_PAIRWISE_ALTERNATIVE_OVERRIDE",),
            )
        if verification.get("preference") == "INITIAL":
            reason = "LOGS_PAIRWISE_INITIAL"
        elif verification.get("preference") == "INCONCLUSIVE":
            reason = "LOGS_PAIRWISE_INCONCLUSIVE"
        elif verification.get("alternative_role") != "ROOT_CANDIDATE":
            reason = "LOGS_PAIRWISE_ALT_LACKS_ROOT_ROLE"
        elif not verification.get("supporting_evidence_refs"):
            reason = "LOGS_PAIRWISE_ALT_LACKS_SUPPORT"
        elif not (
            verification.get("initial_role") == "PROPAGATED_SYMPTOM"
            or verification.get("contradicting_evidence_refs")
        ):
            reason = "LOGS_PAIRWISE_INITIAL_NOT_CONTRADICTED"
        elif not set(
            tuple(verification.get("supporting_evidence_refs", ()))
            + tuple(verification.get("contradicting_evidence_refs", ()))
        ).issubset(_source_refs(row, "logs_evidence")):
            reason = "LOGS_PAIRWISE_REF_NOT_VISIBLE"
        else:
            raise AssertionError("authorizing pairwise output was not handled")
        return _normalized_fusion("KEEP_INITIAL", initial, (reason,))

    return replay_free()


def _frontier_counts(
    rows: Sequence[Mapping[str, Any]], decisions: Mapping[str, bool]
) -> dict[str, Any]:
    initial_correct = sum(
        row.get("initial_service") == row.get("truth_service") for row in rows
    )
    final_correct = 0
    override_count = 0
    correct_override = 0
    wrong_override = 0
    root_rescue = 0
    root_damage = 0
    for row in rows:
        key = str(row.get("private_case_key"))
        override = decisions.get(key, False) and _alternative_service(row) is not None
        initial = row.get("initial_service")
        final = _alternative_service(row) if override else initial
        truth = row.get("truth_service")
        before = initial == truth
        after = final == truth
        final_correct += after
        override_count += override
        correct_override += override and after
        wrong_override += override and not after
        root_rescue += (not before) and after
        root_damage += before and not after
    return {
        "case_count": len(rows),
        "initial_root": initial_correct,
        "final_root": final_correct,
        "override_count": override_count,
        "correct_override": correct_override,
        "wrong_override": wrong_override,
        "root_rescue": root_rescue,
        "root_damage": root_damage,
        "net_rescue": root_rescue - root_damage,
        "root_damage_rate": _ratio(root_damage, len(rows)),
    }


def evaluate_fusion_frontier(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("completed", True)]
    rules = {
        "F0": {
            str(row.get("private_case_key")): replay_current_fusion(row)["action"]
            == "OVERRIDE_INITIAL"
            for row in completed
        },
        "F1": {
            str(row.get("private_case_key")): _f1_override(row) for row in completed
        },
        "F2": {
            str(row.get("private_case_key")): _f2_override(row) for row in completed
        },
        "F3": {
            str(row.get("private_case_key")): _f3_override(row) for row in completed
        },
    }
    report: dict[str, Any] = {
        name: _frontier_counts(completed, rule) for name, rule in rules.items()
    }
    report["positive_net_rescue"] = any(
        report[name]["net_rescue"] > 0 for name in ("F1", "F2", "F3")
    )
    keep_reasons = Counter(
        reason
        for row in completed
        for reason in replay_current_fusion(row)["reason_codes"]
        if replay_current_fusion(row)["action"] == "KEEP_INITIAL"
    )
    report["keep_reason_distribution"] = dict(sorted(keep_reasons.items()))
    report["replay_value_identical"] = _ratio(
        sum(replay_current_fusion(row) == row.get("stored_fusion") for row in completed),
        len(completed),
    )
    if report["positive_net_rescue"]:
        bottleneck = "FUSION_IS_BOTTLENECK"
    elif any(
        row.get("initial_service") != row.get("truth_service")
        and _alternative_service(row) == row.get("truth_service")
        and classify_evidence_sufficiency(row)["class"]
        in {"LOGS_CAN_COMPARE", "LOGS_AND_TRACE_CAN_COMPARE"}
        for row in completed
    ):
        bottleneck = "SPECIALIST_OUTPUT_IS_BOTTLENECK"
    else:
        bottleneck = "SOURCE_EVIDENCE_IS_BOTTLENECK"
    report["bottleneck_verdict"] = bottleneck
    return report


def _metrics_rule(row: Mapping[str, Any], rule: str) -> bool:
    alternative = row.get("metrics_alternative")
    if not isinstance(alternative, Mapping):
        return False
    rank = alternative.get("alternative_rank")
    margin = float(alternative.get("metrics_margin", 0.0))
    initial_rank = alternative.get("initial_rank_or_none")
    if rule == "M0":
        return False
    if rule == "M1":
        return rank == 1 and margin >= 0.25
    if rule == "M2":
        return rank == 1 and margin >= 0.5
    if rule == "M3":
        return rank == 1 and margin >= 0.25 and (
            initial_rank is None or int(initial_rank) > 2
        )
    raise ValueError(f"unknown metrics rule: {rule}")


def evaluate_metrics_frontier(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row.get("completed", True)]
    result: dict[str, Any] = {}
    for name in ("M0", "M1", "M2", "M3"):
        decisions = {
            str(row.get("private_case_key")): _metrics_rule(row, name)
            for row in completed
        }
        counts = _frontier_counts(completed, decisions)
        initial_pair = sum(
            row.get("initial_service") == row.get("truth_service")
            and _canonical_indicator(row.get("initial_indicator"))
            == _canonical_indicator(row.get("truth_indicator"))
            for row in completed
        )
        # The bounded counterfactual explicitly freezes the Initial indicator.
        final_pair = sum(
            (
                _alternative_service(row)
                if decisions[str(row.get("private_case_key"))]
                else row.get("initial_service")
            )
            == row.get("truth_service")
            and _canonical_indicator(row.get("initial_indicator"))
            == _canonical_indicator(row.get("truth_indicator"))
            for row in completed
        )
        pair_rescue = 0
        pair_damage = 0
        for row in completed:
            key = str(row.get("private_case_key"))
            before_pair = bool(
                row.get("initial_service") == row.get("truth_service")
                and _canonical_indicator(row.get("initial_indicator"))
                == _canonical_indicator(row.get("truth_indicator"))
            )
            final_service = (
                _alternative_service(row)
                if decisions[key]
                else row.get("initial_service")
            )
            after_pair = bool(
                final_service == row.get("truth_service")
                and _canonical_indicator(row.get("initial_indicator"))
                == _canonical_indicator(row.get("truth_indicator"))
            )
            pair_rescue += (not before_pair) and after_pair
            pair_damage += before_pair and not after_pair
        counts.update(
            {
                "initial_pair": initial_pair,
                "final_pair": final_pair,
                "pair_rescue": pair_rescue,
                "pair_damage": pair_damage,
                "pair_net_rescue": pair_rescue - pair_damage,
                "indicator_policy": "KEEP_INITIAL_INDICATOR",
            }
        )
        result[name] = counts
    return result


def evaluate_trace_opportunity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wrong = [
        row
        for row in rows
        if row.get("completed", True)
        and row.get("initial_service") != row.get("truth_service")
    ]
    eligible = [
        row for row in wrong if _alternative_service(row) == row.get("truth_service")
    ]
    alternative_visible = 0
    co_visible = 0
    truth_visible = 0
    for row in eligible:
        traces = _source_services(row, "trace_evidence")
        alternative = _alternative_service(row)
        initial = str(row.get("initial_service"))
        truth = str(row.get("truth_service"))
        alternative_visible += alternative is not None and alternative in traces
        co_visible += alternative is not None and {initial, alternative}.issubset(traces)
        truth_visible += truth in traces
    alternative_rate = _ratio(alternative_visible, len(eligible))
    support_gate = bool(
        len(eligible) >= 4
        and co_visible >= 4
        and alternative_rate["value"] is not None
        and float(alternative_rate["value"]) >= 0.5
    )
    causal = {
        "caller_callee_available": False,
        "error_propagation_available": False,
        "latency_anomaly_available": True,
        "root_symptom_roles_available": False,
        "basis": "Current bounded trace projection is per-service latency/error summary without dependency edges or propagation roles.",
    }
    return {
        "wrong_initial_count": len(wrong),
        "truth_matching_alternative_count": len(eligible),
        "alternative_trace_visible": alternative_rate,
        "truth_trace_visible": _ratio(truth_visible, len(eligible)),
        "initial_alternative_trace_co_visible": co_visible,
        "support_gate": support_gate,
        "causal_sufficiency": causal,
        "genuine_causal_information": support_gate
        and causal["caller_callee_available"]
        and causal["error_propagation_available"],
    }


_MESSAGE_CONTRACT_FIELDS: dict[str, tuple[str, ...]] = {
    "C0": (
        "initial_service",
        "alternative_service",
        "initial_indicator",
        "logs_evidence",
        "visible_refs",
    ),
    "C1": (
        "initial_service",
        "alternative_service",
        "initial_indicator",
        "logs_evidence",
        "visible_refs",
        "gate_reason_codes",
        "initial_confidence",
        "metrics_rank_risk",
        "metrics_margin_risk",
    ),
    "C2": (
        "initial_service",
        "alternative_service",
        "initial_indicator",
        "logs_evidence",
        "visible_refs",
        "alternative_rank",
        "alternative_score",
        "metrics_margin",
        "initial_metrics_rank",
    ),
    "C3": (
        "initial_service",
        "alternative_service",
        "initial_indicator",
        "logs_evidence",
        "visible_refs",
        "initial_explanation",
        "initial_cited_evidence_summary",
    ),
    "C4": (
        "initial_service",
        "alternative_service",
        "initial_indicator",
        "logs_evidence",
        "visible_refs",
        "gate_reason_codes",
        "initial_confidence",
        "metrics_rank_risk",
        "metrics_margin_risk",
        "alternative_rank",
        "alternative_score",
        "metrics_margin",
        "initial_metrics_rank",
        "bounded_metrics_evidence",
    ),
    "C5": (
        "initial_service",
        "alternative_service",
        "initial_indicator",
        "gate_reason_codes",
        "initial_confidence",
        "metrics_rank_risk",
        "metrics_margin_risk",
        "alternative_rank",
        "alternative_score",
        "metrics_margin",
        "initial_metrics_rank",
        "trace_evidence",
        "visible_refs",
    ),
}


def _message_values(row: Mapping[str, Any], contract: str) -> dict[str, Any]:
    alternative = row.get("metrics_alternative")
    if not isinstance(alternative, Mapping):
        alternative = {}
    all_evidence = tuple(row.get("metrics_evidence", ())) + tuple(
        row.get("logs_evidence", ())
    ) + tuple(row.get("trace_evidence", ()))
    by_ref = {
        str(item.get("evidence_ref")): item
        for item in all_evidence
        if isinstance(item, Mapping) and item.get("evidence_ref")
    }
    cited_summary = tuple(
        by_ref[ref]
        for ref in row.get("initial_evidence_refs", ())
        if ref in by_ref
    )
    source_key = "trace_evidence" if contract == "C5" else "logs_evidence"
    values = {
        "initial_service": row.get("initial_service"),
        "alternative_service": alternative.get("alternative_service"),
        "initial_indicator": row.get("initial_indicator"),
        "logs_evidence": row.get("logs_evidence", ()),
        "trace_evidence": row.get("trace_evidence", ()),
        "visible_refs": tuple(sorted(_source_refs(row, source_key))),
        "gate_reason_codes": row.get("gate_reasons", ()),
        "initial_confidence": row.get("initial_confidence"),
        "metrics_rank_risk": row.get("metrics_rank_risk"),
        "metrics_margin_risk": row.get("metrics_margin_risk"),
        "alternative_rank": alternative.get("alternative_rank"),
        "alternative_score": alternative.get("alternative_score"),
        "metrics_margin": alternative.get("metrics_margin"),
        "initial_metrics_rank": alternative.get("initial_rank_or_none"),
        "initial_explanation": row.get("initial_explanation"),
        "initial_cited_evidence_summary": cited_summary,
        "bounded_metrics_evidence": row.get("metrics_evidence", ()),
    }
    return {field: values[field] for field in _MESSAGE_CONTRACT_FIELDS[contract]}


def _reference_occurrences(value: object) -> list[str]:
    output: list[str] = []
    if isinstance(value, Mapping):
        for nested in value.values():
            output.extend(_reference_occurrences(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            output.extend(_reference_occurrences(nested))
    elif isinstance(value, str) and _CONCRETE_REF.fullmatch(value):
        output.append(value)
    return output


def evaluate_message_contracts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    calls = [
        row
        for row in rows
        if row.get("completed", True)
        and isinstance(row.get("metrics_alternative"), Mapping)
        and isinstance(row.get("logs_pairwise_verification"), Mapping)
    ]
    result: dict[str, Any] = {}
    full_field_count = len(
        {field for fields in _MESSAGE_CONTRACT_FIELDS.values() for field in fields}
    )
    for contract, fields in _MESSAGE_CONTRACT_FIELDS.items():
        total_slots = len(calls) * len(fields)
        full_slots = len(calls) * full_field_count
        duplicate_refs = 0
        reference_occurrences = 0
        payload_sizes: list[float] = []
        for row in calls:
            envelope = _message_values(row, contract)
            refs = _reference_occurrences(envelope)
            reference_occurrences += len(refs)
            duplicate_refs += len(refs) - len(set(refs))
            payload_sizes.append(
                float(len(json.dumps(envelope, ensure_ascii=False, sort_keys=True)))
            )
        target = "traces" if contract == "C5" else "logs"
        has_cross_source_metrics = contract == "C4"
        sufficient = 0
        comparable = 0
        for row in calls:
            source_key = "trace_evidence" if target == "traces" else "logs_evidence"
            source_services = _source_services(row, source_key)
            initial = str(row.get("initial_service"))
            alternative = _alternative_service(row)
            if has_cross_source_metrics:
                metrics_services = {
                    str(item[0])
                    for item in row.get("metrics_ranking", ())
                    if isinstance(item, (list, tuple)) and item
                }
                visible_services = source_services | metrics_services
                sufficient += alternative == row.get("truth_service")
            else:
                visible_services = source_services
                sufficient += bool(
                    alternative == row.get("truth_service")
                    and alternative is not None
                    and {initial, alternative}.issubset(source_services)
                )
            comparable += bool(
                alternative is not None
                and {initial, alternative}.issubset(visible_services)
            )
        result[contract] = {
            "fields": fields,
            "field_count": len(fields),
            "field_completeness": _ratio(total_slots, full_slots),
            "candidate_provenance_completeness": _ratio(
                len(calls) if "alternative_rank" in fields else 0, len(calls)
            ),
            "source_sufficiency": _ratio(sufficient, len(calls)),
            "both_candidate_visibility": _ratio(comparable, len(calls)),
            "evidence_duplication": _ratio(
                duplicate_refs, reference_occurrences
            ),
            "payload_field_units": len(calls) * len(fields),
            "estimated_payload_bytes": _continuous(payload_sizes),
        }
    eligible = [
        row
        for row in calls
        if row.get("initial_service") != row.get("truth_service")
        and _alternative_service(row) == row.get("truth_service")
        and classify_evidence_sufficiency(row)["class"]
        in {"LOGS_CAN_COMPARE", "LOGS_AND_TRACE_CAN_COMPARE"}
        and not _pairwise_currently_authorizes(row)
    ]
    overlap_values: list[float] = []
    for row in calls:
        overlap_initial_refs = set(row.get("initial_context_evidence_refs", ()))
        source_refs = _source_refs(row, "metrics_evidence") | _source_refs(
            row, "logs_evidence"
        )
        overlap_values.append(
            1.0
            if not source_refs
            else len(overlap_initial_refs.intersection(source_refs)) / len(source_refs)
        )
    mean_overlap = 0.0 if not overlap_values else fmean(overlap_values)
    result.update(
        {
            "communication_repair_eligible_count": len(eligible),
            "communication_repair_eligible": len(eligible) >= 4,
            "initial_context_source_overlap": {
                "minimum": None if not overlap_values else min(overlap_values),
                "mean": None if not overlap_values else mean_overlap,
                "maximum": None if not overlap_values else max(overlap_values),
            },
            "cross_source_verifier_redundant": bool(calls and mean_overlap >= 0.75),
            "redundancy_basis": "Initial already consumes the full bounded ArchitectureContext; overlap measures whether a new verifier would mainly repeat evidence.",
        }
    )
    return result


def select_architecture(
    metrics: Mapping[str, Any],
    trace: Mapping[str, Any],
    communication: Mapping[str, Any],
    fusion: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose exactly one architecture by the frozen decision priority."""

    if metrics.get("selected_rule") is not None and metrics.get("supported_rules"):
        decision = "METRICS_ARBITRATION"
        reason = "A deterministic metrics rule shows robust positive net rescue."
    elif trace.get("support_gate") and trace.get("genuine_causal_information"):
        decision = "METRICS_PLUS_TRACE_VERIFICATION"
        reason = "Metrics needs causal trace evidence and the bounded trace contract supplies it."
    elif (
        communication.get("communication_repair_eligible")
        and not communication.get("cross_source_verifier_redundant")
        and fusion.get("positive_net_rescue")
    ):
        decision = "COMMUNICATION_REPAIRED_CROSS_SOURCE_VERIFIER"
        reason = "A repaired envelope plus bounded Fusion relaxation has actionable non-redundant rescue."
    else:
        decision = "STRONG_SINGLE_RECOMMENDED"
        reason = "No multi-agent path clears deterministic value, causal trace, and non-redundancy gates."
    if decision not in ARCHITECTURE_DECISIONS:
        raise AssertionError("architecture decision must be one of the frozen options")
    return {"decision": decision, "reason": reason}


def build_architecture_matrix(
    metrics_frontiers: Mapping[str, Mapping[str, Mapping[str, Any]]],
    metrics_selection: Mapping[str, Any],
    trace: Mapping[str, Any],
    communication: Mapping[str, Any],
    fusion: Mapping[str, Any],
) -> dict[str, Any]:
    selected_rule = metrics_selection.get("selected_rule")
    if isinstance(selected_rule, str):
        metrics_net = sum(
            frontier[selected_rule]["net_rescue"]
            for frontier in metrics_frontiers.values()
        )
        metrics_damage = sum(
            frontier[selected_rule]["root_damage"]
            for frontier in metrics_frontiers.values()
        )
    else:
        metrics_net = 0
        metrics_damage = 0
    fusion_net = max(
        int(fusion[name]["net_rescue"]) for name in ("F0", "F1", "F2", "F3")
    )
    common: dict[str, Any] = {
        "dimensions": (
            "offline_root_net_rescue",
            "root_damage",
            "cross_fixture_robustness",
            "evidence_sufficiency",
            "communication_completeness",
            "expected_model_calls",
            "expected_latency",
            "provider_failure_exposure",
            "interpretability",
            "multi_agent_authenticity",
            "implementation_complexity",
        )
    }
    common["options"] = {
        "STRONG_SINGLE": {
            "offline_root_net_rescue": 0,
            "root_damage": 0,
            "cross_fixture_robustness": "PRESERVED_BASELINE_ONLY",
            "evidence_sufficiency": "FULL_BOUNDED_CONTEXT",
            "communication_completeness": "NOT_APPLICABLE",
            "expected_model_calls": 1,
            "expected_latency": "LOWEST",
            "provider_failure_exposure": "ONE_CALL",
            "interpretability": "MODEL_RATIONALE",
            "multi_agent_authenticity": "NONE",
            "implementation_complexity": "LOW",
        },
        "METRICS_ARBITRATION": {
            "offline_root_net_rescue": metrics_net,
            "root_damage": metrics_damage,
            "cross_fixture_robustness": "SUPPORTED"
            if selected_rule
            else "NOT_SUPPORTED",
            "evidence_sufficiency": "DETERMINISTIC_METRICS",
            "communication_completeness": "NO_NEW_MESSAGE",
            "expected_model_calls": 1,
            "expected_latency": "LOW",
            "provider_failure_exposure": "ONE_CALL",
            "interpretability": "HIGH",
            "multi_agent_authenticity": "DETERMINISTIC_ARBITRATION_NOT_MULTI_AGENT",
            "implementation_complexity": "LOW",
        },
        "METRICS_PLUS_TRACE_VERIFICATION": {
            "offline_root_net_rescue": None,
            "root_damage": None,
            "cross_fixture_robustness": "UNRESOLVED",
            "evidence_sufficiency": "SUPPORTED"
            if trace.get("support_gate")
            else "NOT_SUPPORTED",
            "communication_completeness": "STATIC_CONTRACT_ONLY",
            "expected_model_calls": 2,
            "expected_latency": "HIGHER",
            "provider_failure_exposure": "TWO_CALLS",
            "interpretability": "MEDIUM",
            "multi_agent_authenticity": "SOURCE_SPECIALIST",
            "implementation_complexity": "MEDIUM",
        },
        "COMMUNICATION_REPAIRED_CROSS_SOURCE_VERIFIER": {
            "offline_root_net_rescue": fusion_net,
            "root_damage": min(
                int(fusion[name]["root_damage"])
                for name in ("F0", "F1", "F2", "F3")
                if int(fusion[name]["net_rescue"]) == fusion_net
            ),
            "cross_fixture_robustness": "UNRESOLVED",
            "evidence_sufficiency": "SUPPORTED"
            if communication.get("communication_repair_eligible")
            else "NOT_SUPPORTED",
            "communication_completeness": "C4_STATIC_ONLY",
            "expected_model_calls": 2,
            "expected_latency": "HIGHER",
            "provider_failure_exposure": "TWO_CALLS",
            "interpretability": "MEDIUM",
            "multi_agent_authenticity": "CROSS_SOURCE_VERIFIER",
            "implementation_complexity": "HIGH",
        },
    }
    return common


def build_multiagent_verdict(
    communication: Mapping[str, Any], fusion: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    c0 = communication.get("C0", {})
    return (
        {
            "rank": 1,
            "mechanism": "SOURCE_SIGNAL_INSUFFICIENT",
            "support": "SUPPORTED",
            "basis": c0.get("both_candidate_visibility"),
        },
        {
            "rank": 2,
            "mechanism": "MULTI_STAGE_REDUNDANCY",
            "support": "SUPPORTED"
            if communication.get("cross_source_verifier_redundant")
            else "NOT_SUPPORTED",
            "basis": communication.get("initial_context_source_overlap"),
        },
        {
            "rank": 3,
            "mechanism": "MESSAGE_CONTRACT_LOSS",
            "support": "PARTIALLY_SUPPORTED",
            "basis": communication.get("C0", {}).get(
                "candidate_provenance_completeness"
            ),
        },
        {
            "rank": 4,
            "mechanism": "SPECIALIST_TASK_DEFINITION",
            "support": "UNRESOLVED",
            "basis": "Causal-role truth is unavailable beyond root identity.",
        },
        {
            "rank": 5,
            "mechanism": "FUSION_OVERSTRICT",
            "support": "SUPPORTED"
            if fusion.get("positive_net_rescue")
            else "NOT_SUPPORTED",
            "basis": fusion.get("bottleneck_verdict"),
        },
    )


def _select_robust_metrics(
    frontiers: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> dict[str, Any]:
    supported: list[str] = []
    details: dict[str, Any] = {}
    required = {"candidate-3", "candidate-4", "candidate-5"}
    if not required.issubset(frontiers):
        return {
            "supported_rules": (),
            "selected_rule": None,
            "rule_support": {},
        }
    for rule in ("M1", "M2", "M3"):
        primary = frontiers["candidate-5"][rule]
        c3 = frontiers["candidate-3"][rule]
        c4 = frontiers["candidate-4"][rule]
        primary_gate = bool(
            primary["root_rescue"] > primary["root_damage"]
            and primary["net_rescue"] >= 2
            and primary["root_damage"] <= 2
        )
        robustness_gate = bool(
            (
                c3["net_rescue"] > 0
                and c3["root_damage"] <= 2
                and c4["net_rescue"] >= 0
            )
            or (
                c4["net_rescue"] > 0
                and c4["root_damage"] <= 2
                and c3["net_rescue"] >= 0
            )
        )
        details[rule] = {
            "candidate_5_primary_gate": primary_gate,
            "candidate_3_4_robustness_gate": robustness_gate,
        }
        if primary_gate and robustness_gate:
            supported.append(rule)
    selected = max(
        supported,
        key=lambda rule: (
            sum(frontier[rule]["net_rescue"] for frontier in frontiers.values()),
            -sum(frontier[rule]["root_damage"] for frontier in frontiers.values()),
            rule,
        ),
        default=None,
    )
    return {
        "supported_rules": tuple(supported),
        "selected_rule": selected,
        "rule_support": details,
    }


def _aggregate_source_visibility(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    completed = [row for row in rows if row.get("completed", True)]

    def count(predicate: Any) -> dict[str, int | float | None]:
        return _ratio(sum(bool(predicate(row)) for row in completed), len(completed))

    return {
        "case_count": len(completed),
        "truth_metrics_top1": count(
            lambda row: _rank_of(
                str(row.get("truth_service")), row.get("metrics_ranking", ())
            )
            == 1
        ),
        "truth_metrics_top2": count(
            lambda row: (
                rank := _rank_of(
                    str(row.get("truth_service")), row.get("metrics_ranking", ())
                )
            )
            is not None
            and rank <= 2
        ),
        "truth_metrics_top3": count(
            lambda row: (
                rank := _rank_of(
                    str(row.get("truth_service")), row.get("metrics_ranking", ())
                )
            )
            is not None
            and rank <= 3
        ),
        "truth_metrics_top6": count(
            lambda row: _rank_of(
                str(row.get("truth_service")), row.get("metrics_ranking", ())
            )
            is not None
        ),
        "truth_logs_visible": count(
            lambda row: str(row.get("truth_service"))
            in _source_services(row, "logs_evidence")
        ),
        "truth_trace_visible": count(
            lambda row: str(row.get("truth_service"))
            in _source_services(row, "trace_evidence")
        ),
        "initial_logs_visible": count(
            lambda row: str(row.get("initial_service"))
            in _source_services(row, "logs_evidence")
        ),
        "initial_trace_visible": count(
            lambda row: str(row.get("initial_service"))
            in _source_services(row, "trace_evidence")
        ),
        "alternative_logs_visible": count(
            lambda row: _alternative_service(row)
            in _source_services(row, "logs_evidence")
        ),
        "alternative_trace_visible": count(
            lambda row: _alternative_service(row)
            in _source_services(row, "trace_evidence")
        ),
        "initial_alternative_logs_co_visible": count(
            lambda row: _alternative_service(row) is not None
            and {
                str(row.get("initial_service")),
                str(_alternative_service(row)),
            }.issubset(_source_services(row, "logs_evidence"))
        ),
        "initial_alternative_trace_co_visible": count(
            lambda row: _alternative_service(row) is not None
            and {
                str(row.get("initial_service")),
                str(_alternative_service(row)),
            }.issubset(_source_services(row, "trace_evidence"))
        ),
    }


def _aggregate_source_sufficiency(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    wrong = [
        row
        for row in rows
        if row.get("completed", True)
        and row.get("initial_service") != row.get("truth_service")
    ]
    retrieved = [
        row
        for row in wrong
        if _rank_of(
            str(row.get("truth_service")), row.get("metrics_ranking", ())
        )
        is not None
    ]
    counter = Counter(classify_evidence_sufficiency(row)["class"] for row in retrieved)
    return {
        "initial_wrong_count": len(wrong),
        "truth_retrieved_count": len(retrieved),
        "candidate_not_retrieved_count": len(wrong) - len(retrieved),
        "class_counts": {key: counter[key] for key in SUFFICIENCY_CLASSES},
    }


def build_public_report(
    candidate_rows: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    ordered = {name: list(candidate_rows[name]) for name in sorted(candidate_rows)}
    metrics_frontiers = {
        name: evaluate_metrics_frontier(rows) for name, rows in ordered.items()
    }
    metrics_selection = _select_robust_metrics(metrics_frontiers)
    c5_rows = ordered.get("candidate-5", ())
    fusion = evaluate_fusion_frontier(c5_rows)
    wrong_union: dict[str, Mapping[str, Any]] = {}
    for name in reversed(sorted(ordered)):
        for row in ordered[name]:
            if row.get("initial_service") != row.get("truth_service"):
                wrong_union.setdefault(str(row.get("private_case_key")), row)
    union_rows = list(wrong_union.values())
    trace = evaluate_trace_opportunity(union_rows)
    communication = evaluate_message_contracts(c5_rows or union_rows)
    decision = select_architecture(metrics_selection, trace, communication, fusion)
    architecture_matrix = build_architecture_matrix(
        metrics_frontiers, metrics_selection, trace, communication, fusion
    )
    source_classes: dict[str, Any] = {}
    source_visibility: dict[str, Any] = {}
    failure_counts: dict[str, Any] = {}
    for name, rows in ordered.items():
        completed = [row for row in rows if row.get("completed", True)]
        source_classes[name] = _aggregate_source_sufficiency(completed)
        source_visibility[name] = _aggregate_source_visibility(completed)
        failure_counter = Counter(
            classify_failure_mechanism(row)["primary"] for row in completed
        )
        failure_counts[name] = {
            key: failure_counter[key] for key in FAILURE_MECHANISMS
        }
    report = {
        "schema_version": "rcaeval-multiagent-communication-audit-v1",
        "classification": CLASSIFICATION,
        "provider_calls": 0,
        "scope": {
            name: {
                "terminal_count": len(rows),
                "completed_count": sum(
                    row.get("completed", True) for row in rows
                ),
                "failed_count": sum(
                    not row.get("completed", True) for row in rows
                ),
            }
            for name, rows in ordered.items()
        },
        "artifact_sufficiency": build_artifact_sufficiency(),
        "communication_stage_count": len(build_communication_graph()["stages"]),
        "source_sufficiency_counts": source_classes,
        "initial_wrong_union_source_sufficiency": _aggregate_source_sufficiency(
            union_rows
        ),
        "source_visibility": source_visibility,
        "failure_mechanism_counts": failure_counts,
        "specialist_audit": {
            "candidate_4_free": audit_free_specialist(ordered.get("candidate-4", ())),
            "candidate_5_pairwise": audit_pairwise_specialist(c5_rows),
        },
        "fusion_frontier": fusion,
        "metrics_frontiers": metrics_frontiers,
        "metrics_selection": metrics_selection,
        "trace_opportunity": trace,
        "message_contract_ablation": communication,
        "multiagent_communication_verdict": build_multiagent_verdict(
            communication, fusion
        ),
        "architecture_matrix": architecture_matrix,
        "architecture_decision": decision,
    }
    assert_public_payload(report)
    assert_rate_contract(report)
    return report


def _tree_digest(root: Path) -> tuple[str, int, int]:
    """Match the frozen shell digest: sha256 of absolute-path shasum lines."""

    resolved = root.expanduser().resolve(strict=True)
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError("audit input root must be a real directory")
    outer = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for path in sorted(item for item in resolved.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ValueError("audit input may not contain symlink files")
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        outer.update(f"{digest}  {path}\n".encode())
        file_count += 1
        byte_count += len(payload)
    return outer.hexdigest(), file_count, byte_count


def _identity_digest(case: DevCase) -> str:
    payload = b"\0".join(
        value.encode("utf-8")
        for value in (
            case.system,
            case.root_cause_service,
            case.fault,
            case.instance,
        )
    )
    return hashlib.sha256(payload).hexdigest()


def _load_case_index(ob_root: Path, ss_root: Path) -> dict[str, DevCase]:
    roots = (ob_root.expanduser().resolve(strict=True), ss_root.expanduser().resolve(strict=True))
    encoded = "\n".join(str(root).casefold() for root in roots)
    if "re2-tt" in encoded or "tt-case" in encoded:
        raise ValueError("audit forbids RE2-TT inputs")
    ob_cases = discover_dev_cases(roots[0], DevSystem.RE2_OB)
    ss_cases = discover_dev_cases(roots[1], DevSystem.RE2_SS)
    if len(ob_cases) != 90 or len(ss_cases) != 90:
        raise ValueError("audit requires the complete 90+90 development-visible cases")
    cases = ob_cases + ss_cases
    result = {case.case_id: case for case in cases}
    if len(result) != 180:
        raise ValueError("development case identifiers are not unique")
    return result


def _service_ranking(candidates: Sequence[Any]) -> tuple[tuple[str, float], ...]:
    output: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in candidates:
        service = str(item.service)
        if service in seen:
            continue
        output.append((service, float(item.score)))
        seen.add(service)
        if len(output) == 6:
            break
    return tuple(output)


def _normalized_margin(ranking: Sequence[Sequence[object]]) -> float:
    if len(ranking) < 2:
        return 1.0
    top1 = float(str(ranking[0][1]))
    top2 = float(str(ranking[1][1]))
    return max(0.0, (top1 - top2) / max(abs(top1), 1e-12))


def _alternative_from_ranking(
    initial_service: str, ranking: tuple[tuple[str, float], ...]
) -> dict[str, Any] | None:
    initial_rank = _rank_of(initial_service, ranking)
    for rank, (service, score) in enumerate(ranking, start=1):
        if service != initial_service:
            return {
                "initial_service": initial_service,
                "alternative_service": service,
                "alternative_rank": rank,
                "alternative_score": score,
                "initial_rank_or_none": initial_rank,
                "metrics_margin": _normalized_margin(ranking),
            }
    return None


def _case_projection(
    case: DevCase, indicator_config: Any
) -> dict[str, Any]:
    telemetry = dev_case_to_telemetry_case(case)
    builder = ArchitectureContextBuilder(
        telemetry, Architecture.SINGLE, run_id="d" * 32
    )
    for source_name in ("metrics", "logs", "traces"):
        builder.query_source(source_name)  # type: ignore[arg-type]
    context = builder.snapshot()
    evidence: list[dict[str, str]] = []
    for item in context.evidence:
        prefix = item.evidence_id.partition(":")[0]
        evidence_source = {"metric": "metrics", "log": "logs", "trace": "traces"}.get(
            prefix
        )
        if evidence_source is None:
            raise ValueError("bounded evidence has an unknown source prefix")
        evidence.append(
            {
                "evidence_ref": item.evidence_id,
                "source": evidence_source,
                "service": item.service,
                "observation": item.summary,
            }
        )
    candidates = build_runtime_metric_candidates(
        telemetry,
        case_identity_sha256=_identity_digest(case),
        formula=FormulaId.F0,
        config=indicator_config,
    )
    return {
        "metrics_ranking": _service_ranking(candidates),
        "evidence": tuple(evidence),
        "context_refs": tuple(item["evidence_ref"] for item in evidence),
    }


def _validate_stored_alternative(
    stored: Mapping[str, Any], reconstructed: Mapping[str, Any] | None
) -> None:
    if reconstructed is None:
        raise ValueError("stored Metrics alternative cannot be reconstructed")
    for key in (
        "initial_service",
        "alternative_service",
        "alternative_rank",
        "initial_rank_or_none",
    ):
        if stored.get(key) != reconstructed.get(key):
            raise ValueError(f"stored Metrics alternative differs at {key}")
    for key in ("alternative_score", "metrics_margin"):
        if not math.isclose(
            float(stored.get(key, math.nan)),
            float(reconstructed.get(key, math.nan)),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"stored Metrics alternative differs at {key}")


def _load_candidate_rows(
    *,
    candidate: str,
    root: Path,
    cases: Mapping[str, DevCase],
    projections: dict[str, dict[str, Any]],
    indicator_config: Any,
) -> list[dict[str, Any]]:
    terminal_root = root / "terminal-records"
    paths = tuple(sorted(terminal_root.glob("*.json")))
    if len(paths) != 60:
        raise ValueError(f"{candidate} requires exactly 60 terminal records")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        terminal = _read_object(path)
        if terminal.get("candidate_id") != candidate:
            raise ValueError(f"{candidate} terminal has a different candidate id")
        case_id = terminal.get("case_id")
        if not isinstance(case_id, str) or case_id in seen or case_id not in cases:
            raise ValueError(f"{candidate} terminal has an invalid case id")
        seen.add(case_id)
        case = cases[case_id]
        if case_id not in projections:
            projections[case_id] = _case_projection(case, indicator_config)
        projection = projections[case_id]
        completed = terminal.get("status") == "COMPLETED"
        base: dict[str, Any] = {
            "private_case_key": case_id,
            "candidate": candidate,
            "completed": completed,
            "terminal_status": terminal.get("status"),
            "failure_stage": terminal.get("failure_stage"),
            "failure_code": terminal.get("failure_code"),
            "truth_service": case.root_cause_service,
            "truth_indicator": normalize_indicator(case.fault),
            "metrics_ranking": projection["metrics_ranking"],
            "metrics_evidence": tuple(
                item
                for item in projection["evidence"]
                if item["source"] == "metrics"
            ),
            "logs_evidence": tuple(
                item for item in projection["evidence"] if item["source"] == "logs"
            ),
            "trace_evidence": tuple(
                item
                for item in projection["evidence"]
                if item["source"] == "traces"
            ),
            "initial_context_evidence_refs": projection["context_refs"],
        }
        if not completed:
            rows.append(base)
            continue
        result = terminal.get("result")
        diagnosis = result.get("diagnosis") if isinstance(result, Mapping) else None
        if not isinstance(diagnosis, Mapping):
            raise ValueError(f"{candidate} completed terminal lacks diagnosis")
        initial = diagnosis.get("initial_diagnosis")
        gate = diagnosis.get("gate_decision")
        fusion = diagnosis.get("fusion_decision")
        indicator = diagnosis.get("indicator_resolution")
        if not all(isinstance(value, Mapping) for value in (initial, gate, fusion, indicator)):
            raise ValueError(f"{candidate} completed diagnosis lacks typed stages")
        assert isinstance(initial, Mapping)
        assert isinstance(gate, Mapping)
        assert isinstance(fusion, Mapping)
        assert isinstance(indicator, Mapping)
        initial_service = str(initial.get("root_cause_service"))
        ranking = projection["metrics_ranking"]
        alternative = _alternative_from_ranking(initial_service, ranking)
        stored_alternative = diagnosis.get("metrics_alternative")
        if isinstance(stored_alternative, Mapping):
            _validate_stored_alternative(stored_alternative, alternative)
            alternative = dict(stored_alternative)
        stored_rank = gate.get("metrics_service_rank")
        if stored_rank != _rank_of(initial_service, ranking):
            raise ValueError(f"reconstructed {candidate} Metrics rank differs")
        if not math.isclose(
            float(gate.get("metrics_top1_top2_margin", math.nan)),
            _normalized_margin(ranking),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"reconstructed {candidate} Metrics margin differs")
        base.update(
            {
                "initial_service": initial_service,
                "initial_indicator": initial.get("root_cause_indicator"),
                "initial_confidence": initial.get("confidence"),
                "initial_explanation": initial.get("explanation"),
                "initial_evidence_refs": tuple(initial.get("evidence_refs", ())),
                "metrics_alternative": alternative,
                "gate_route": gate.get("route"),
                "gate_reasons": tuple(gate.get("reason_codes", ())),
                "gate_initial_unstable": gate.get("initial_unstable"),
                "metrics_rank_risk": gate.get("metrics_rank_risk"),
                "metrics_margin_risk": gate.get("metrics_margin_risk"),
                "specialist_hypotheses": tuple(
                    diagnosis.get("specialist_hypotheses", ())
                ),
                "logs_pairwise_verification": diagnosis.get(
                    "logs_pairwise_verification"
                ),
                "stored_fusion": _normalized_fusion(
                    str(fusion.get("action")),
                    fusion.get("final_root_service"),
                    tuple(fusion.get("reason_codes", ())),
                ),
                "final_service": diagnosis.get("final_root_service"),
                "final_indicator": diagnosis.get("final_indicator"),
                "indicator_action": indicator.get("action"),
            }
        )
        if replay_current_fusion(base) != base["stored_fusion"]:
            raise ValueError(f"{candidate} deterministic Fusion replay differs")
        rows.append(base)
    expected_completed = {"candidate-3": 60, "candidate-4": 59, "candidate-5": 60}
    if sum(row["completed"] for row in rows) != expected_completed[candidate]:
        raise ValueError(f"{candidate} completed count differs from frozen evidence")
    return rows


def _build_private_outputs(
    candidate_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    inventory: Mapping[str, Any],
) -> dict[str, object]:
    completed = [
        row
        for name in sorted(candidate_rows)
        for row in candidate_rows[name]
        if row.get("completed")
    ]
    stages = []
    evidence = []
    failures = []
    specialist = []
    fusion = []
    metrics = []
    traces = []
    for row in completed:
        key = row["private_case_key"]
        candidate = row["candidate"]
        stages.append(
            {
                "private_case_key": key,
                "candidate": candidate,
                "initial": {
                    "service": row["initial_service"],
                    "indicator": row["initial_indicator"],
                    "confidence": row["initial_confidence"],
                    "evidence_refs": row["initial_evidence_refs"],
                },
                "gate": {
                    "route": row["gate_route"],
                    "reason_codes": row["gate_reasons"],
                    "initial_unstable": row["gate_initial_unstable"],
                },
                "metrics_alternative": row["metrics_alternative"],
                "specialist_hypotheses": row["specialist_hypotheses"],
                "logs_pairwise_verification": row["logs_pairwise_verification"],
                "fusion": row["stored_fusion"],
                "final": {
                    "service": row["final_service"],
                    "indicator": row["final_indicator"],
                    "indicator_action": row["indicator_action"],
                },
            }
        )
        evidence.append(
            {
                "private_case_key": key,
                "candidate": candidate,
                "truth_service": row["truth_service"],
                "truth_indicator": row["truth_indicator"],
                "metrics_ranking": row["metrics_ranking"],
                "classification": classify_evidence_sufficiency(row),
            }
        )
        failures.append(
            {
                "private_case_key": key,
                "candidate": candidate,
                "initial_correct": row["initial_service"] == row["truth_service"],
                "final_correct": row["final_service"] == row["truth_service"],
                "mechanism": classify_failure_mechanism(row),
            }
        )
        if row["specialist_hypotheses"] or row["logs_pairwise_verification"]:
            specialist.append(
                {
                    "private_case_key": key,
                    "candidate": candidate,
                    "evidence_sufficiency": classify_evidence_sufficiency(row),
                    "specialist_hypotheses": row["specialist_hypotheses"],
                    "logs_pairwise_verification": row["logs_pairwise_verification"],
                }
            )
        f_rules = {
            "F0": replay_current_fusion(row)["action"] == "OVERRIDE_INITIAL",
            "F1": _f1_override(row),
            "F2": _f2_override(row),
            "F3": _f3_override(row),
        }
        fusion.append(
            {
                "private_case_key": key,
                "candidate": candidate,
                "stored": row["stored_fusion"],
                "counterfactual_overrides": f_rules,
            }
        )
        metrics.append(
            {
                "private_case_key": key,
                "candidate": candidate,
                "initial_service": row["initial_service"],
                "initial_indicator": row["initial_indicator"],
                "truth_service": row["truth_service"],
                "truth_indicator": row["truth_indicator"],
                "metrics_alternative": row["metrics_alternative"],
                "rules": {
                    name: _metrics_rule(row, name)
                    for name in ("M0", "M1", "M2", "M3")
                },
            }
        )
        trace_services = _source_services(row, "trace_evidence")
        traces.append(
            {
                "private_case_key": key,
                "candidate": candidate,
                "initial_trace_visible": row["initial_service"] in trace_services,
                "alternative_trace_visible": _alternative_service(row)
                in trace_services,
                "truth_trace_visible": row["truth_service"] in trace_services,
                "trace_evidence": row["trace_evidence"],
            }
        )
    message_rows = [
        {"contract": name, "aggregate": value}
        for name, value in evaluate_message_contracts(
            candidate_rows.get("candidate-5", ())
        ).items()
    ]
    return {
        "artifact-sufficiency.json": {
            "schema_version": "rcaeval-multiagent-artifact-sufficiency-v1",
            "classification": CLASSIFICATION,
            "provider_calls": 0,
            "inventory": inventory,
            "observability": build_artifact_sufficiency(),
            "communication_graph": build_communication_graph(),
            "communication_knowledge_matrix": build_communication_knowledge_matrix(),
        },
        "communication-stage-by-case.jsonl": stages,
        "evidence-sufficiency-by-case.jsonl": evidence,
        "failure-mechanism-by-case.jsonl": failures,
        "specialist-output-audit.jsonl": specialist,
        "fusion-counterfactual-by-case.jsonl": fusion,
        "metrics-frontier-by-case.jsonl": metrics,
        "trace-coverage-by-case.jsonl": traces,
        "message-contract-ablation.jsonl": message_rows,
    }


def _render_public_markdown(report: Mapping[str, Any]) -> str:
    decision = report["architecture_decision"]
    metrics = report["metrics_selection"]
    trace = report["trace_opportunity"]
    communication = report["message_contract_ablation"]
    fusion = report["fusion_frontier"]
    frontiers = report["metrics_frontiers"]
    scope = report["scope"]
    c_rows = report["message_contract_ablation"]
    verdict = report["multiagent_communication_verdict"]

    def counts(rate: Mapping[str, Any]) -> str:
        return f"{rate['numerator']}/{rate['denominator']}"
    lines = [
        "# RCAEval Multi-Agent Communication Audit",
        "",
        "## Verdict",
        "",
        f"**{decision['decision']}** — {decision['reason']}",
        "",
        "This is consumed OB/SS development evidence and a post-hoc diagnostic, not external validation or primary inference. Provider calls: **0**.",
        "",
        "| Preserved candidate | Terminals | Completed | Failed |",
        "|---|---:|---:|---:|",
        *(
            f"| {name} | {scope[name]['terminal_count']} | {scope[name]['completed_count']} | {scope[name]['failed_count']} |"
            for name in sorted(scope)
        ),
        "",
        "## What the real v2 path communicates",
        "",
        "The Initial call is the Strong Single contract over the full bounded `ArchitectureContext`; it is not the older Adaptive-v1 `InitialDiagnosisInput`. Candidate-3 and Candidate-4 use a free single-source specialist contract. Candidate-5 changes only Logs to an Initial-vs-Metrics-Alternative pairwise contract; Trace remains the free contract. Fusion is deterministic and receives more provenance than the Logs pairwise verifier.",
        "",
        "The pairwise Logs verifier receives both identities, the Initial indicator, Logs evidence, and visible Logs references. It does **not** receive Metrics rank/score/margin, Gate reasons, Initial confidence/explanation/evidence references, or the Metrics selection rationale. Sidecars preserve hashes and accounting, not request/response bodies, so those envelopes are reconstructed from typed contracts and bounded raw inputs.",
        "",
        "## Counterfactual gates",
        "",
        f"- Robust Metrics rules: `{', '.join(metrics['supported_rules']) or 'none'}`; selected: `{metrics['selected_rule'] or 'none'}`.",
        f"- Trace visibility gate: `{trace['support_gate']}`; genuine causal information: `{trace['genuine_causal_information']}`.",
        f"- Relaxed Fusion shows positive net rescue: `{fusion['positive_net_rescue']}`.",
        f"- Communication repair eligible: `{communication['communication_repair_eligible']}`; new cross-source verifier redundant: `{communication['cross_source_verifier_redundant']}`.",
        "",
        "For selected rule M3, root-only results are:",
        "",
        "| Preserved candidate | Completed | Initial root correct | M3 root correct | Overrides | Rescues | Damages | Net rescue |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {name} | {frontiers[name]['M3']['case_count']} | {frontiers[name]['M3']['initial_root']} | {frontiers[name]['M3']['final_root']} | {frontiers[name]['M3']['override_count']} | {frontiers[name]['M3']['root_rescue']} | {frontiers[name]['M3']['root_damage']} | {frontiers[name]['M3']['net_rescue']} |"
            for name in sorted(frontiers)
        ),
        "",
        "M3 overrides the root only when the deterministic Metrics alternative is rank 1, normalized top-1/top-2 margin is at least 0.25, and the Initial service is absent from the top two. The Initial indicator is frozen. Across the three preserved candidates M3 yields zero root damage and positive net rescue; this is development evidence, not a claim of held-out generalization.",
        "",
        "## Specialist, Fusion, and Trace evidence",
        "",
        f"Candidate-4 produced {report['specialist_audit']['candidate_4_free']['hypothesis_count']} free-generation hypotheses. Truth-matching hypotheses appeared in {report['specialist_audit']['candidate_4_free']['truth_hypothesis_any_rank_all_calls']} calls overall, but correct alternatives for Initial-wrong cases appeared at rank 1 / any rank in {report['specialist_audit']['candidate_4_free']['correct_alternative_rank1']} / {report['specialist_audit']['candidate_4_free']['correct_alternative_any_rank']} calls.",
        "",
        f"Candidate-5 had {report['specialist_audit']['candidate_5_pairwise']['pairwise_calls']} pairwise calls. Both candidates were Logs-visible in {counts(report['specialist_audit']['candidate_5_pairwise']['communication_feasibility']['field_rates']['both_candidates_comparable'])}; candidate provenance, strength, Gate reason, and Initial rationale were each present in 0 of those calls. Causal-role comparisons below are explicitly heuristic because evaluator truth provides root identity, not a propagated-symptom oracle.",
        "",
        f"Current Fusion replay was value-identical in {counts(fusion['replay_value_identical'])}. F1/F2/F3 each produced net rescue `{fusion['F1']['net_rescue']}` / `{fusion['F2']['net_rescue']}` / `{fusion['F3']['net_rescue']}`; bottleneck verdict: `{fusion['bottleneck_verdict']}`.",
        "",
        f"Among {trace['truth_matching_alternative_count']} Initial-wrong cases with a truth-matching Metrics alternative, it was Trace-visible in {counts(trace['alternative_trace_visible'])}, with {trace['initial_alternative_trace_co_visible']} co-visible pairs. The support gate failed, and the bounded projection lacks causal edges/propagation roles.",
        "",
        "## Static message-contract ablation",
        "",
        "| Contract | Fields | Provenance | Source sufficient | Both candidates visible | Mean serialized bytes | Evidence duplication |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {name} | {c_rows[name]['field_count']} | {counts(c_rows[name]['candidate_provenance_completeness'])} | {counts(c_rows[name]['source_sufficiency'])} | {counts(c_rows[name]['both_candidate_visibility'])} | {c_rows[name]['estimated_payload_bytes']['mean']:.1f} | {counts(c_rows[name]['evidence_duplication'])} |"
            for name in ("C0", "C1", "C2", "C3", "C4", "C5")
        ),
        "",
        "These are static envelope measurements over preserved inputs, not generated answers. C0 is the current Logs pairwise envelope; C1 adds Gate context; C2 adds Metrics provenance; C3 adds Initial rationale; C4 combines Gate + Metrics + bounded Metrics/Logs; C5 is the analogous Metrics + Trace pairwise envelope.",
        "",
        "## Interpretation",
        "",
        "A communication defect is real only when a source can compare the candidates, the correct alternative is already available, a missing field changes the action boundary, and the resulting override survives root-damage accounting. More context alone is not evidence for a new Agent. The existing Initial already sees all bounded sources, while the bounded Trace projection lacks caller/callee edges, error propagation, and explicit root-versus-symptom roles.",
        "",
        "Ranked communication verdict:",
        "",
        *(
            f"{item['rank']}. `{item['mechanism']}` — `{item['support']}`."
            for item in verdict
        ),
        "",
        "The recommended runtime shape retains one Strong Single model call and adds only deterministic M3 root arbitration; it does not retain a model-based Multi-Agent root arbiter. Expected model calls: **1**. Implementation of that runtime change is outside this audit and remains unauthorized.",
        "",
        "## Boundaries",
        "",
        "No candidate was rerun. No new Provider or Agent was invoked. No RE2-TT or new data was read. Public artifacts contain aggregate counts/rates only; case-level identities, service names, evidence references, rationales, and raw outputs remain in the Git-external private audit root.",
    ]
    return "\n".join(lines)


def _render_v2_spec(report: Mapping[str, Any]) -> str:
    decision = report["architecture_decision"]["decision"]
    return f"""# RCAEval Multi-Agent Communication v2 Contract

## Status and scope

This is a post-hoc design contract over consumed OB/SS development evidence. It records what an evidence-aware communication boundary would have to preserve; it does not authorize a runtime change, Provider call, new Agent, evaluation, or release. The selected next architecture is `{decision}`.

## Current stage graph

`bounded evidence -> Strong Single Initial -> deterministic Gate -> deterministic Metrics alternative -> source-bound specialist -> deterministic Fusion -> deterministic Indicator -> final diagnosis`

The Strong Single Initial consumes the complete bounded `ArchitectureContext` (Metrics, Logs, and available Traces). Gate consumes the Initial diagnosis plus deterministically reconstructed Metrics rank/margin and source-conflict features. Candidate-5 Logs pairwise consumes only: incident, Initial identity, Metrics-alternative identity, Initial indicator, bounded Logs evidence, and exact visible Logs refs. The free Trace verifier remains source-only. Neither verifier receives evaluator truth.

## Unique responsibilities and visibility

### Strong Single Initial

Unique responsibility: produce the only model-authored initial root/indicator proposal and citations. It sees the incident and full bounded `ArchitectureContext`. It does not see evaluator truth, future Gate results, a named Metrics alternative, or arbitration outcomes.

### Deterministic Gate

Unique responsibility: decide direct return versus a bounded source route. It sees the Initial diagnosis, deterministic Metrics ranking/margin, evidence-support and source-conflict flags, and source availability. It has no Provider call and never sees evaluator truth.

### Deterministic Metrics candidate producer / arbiter

Unique responsibility: produce ranked service candidates with `DETERMINISTIC_METRICS` provenance and, under the selected M3 frontier, decide a root-only override. It sees locked Metrics inputs and the Initial identity/rank. It does not change the Initial indicator and does not consume Logs/Trace model prose.

### Source verifier (contract only; not selected)

Unique responsibility would be to compare exactly two provenance-labelled candidates within one declared source. It may cite only refs in its source-visible allowlist. It must not invent cross-source support, reinterpret Metrics provenance as Logs/Trace evidence, or see evaluator truth. Current evidence does not authorize adding this model call.

### Deterministic Fusion and Indicator resolution

Unique responsibility: enforce override preconditions and retain `KEEP_INITIAL` on inconclusive, unsupported, non-visible, conflicting, or source-mismatched output. It has no Provider call. Indicator resolution is separate from root arbitration.

## Proposed communication envelope

If a future verifier is authorized, its minimum typed envelope must carry:

1. both candidate identities and explicit provenance (`MODEL_INITIAL` versus `DETERMINISTIC_METRICS`);
2. Metrics alternative rank, score, normalized margin, and Initial rank;
3. Gate route, reason codes, and risk flags;
4. Initial confidence, explanation, and cited refs;
5. source-specific evidence plus an exact visible-ref allowlist;
6. an explicit source limitation saying which claims the verifier cannot adjudicate.

Evidence refs remain source-authoritative. Metrics provenance may justify *comparison* but cannot be cited as Logs or Trace evidence. Output must label each candidate `ROOT_CANDIDATE`, `PROPAGATED_SYMPTOM`, or `UNCERTAIN`; separate supporting and contradicting refs; and return `INITIAL`, `ALTERNATIVE`, or `INCONCLUSIVE`.

## Fusion rules

`KEEP_INITIAL` is the default. `OVERRIDE_INITIAL` requires: Gate instability, a deterministic Metrics alternative, explicit alternative preference, alternative root role, non-empty source-visible support, and either an Initial propagated-symptom role or source-visible contradiction. `INCONCLUSIVE`, missing provenance, non-visible refs, or source mismatch must keep the Initial. Indicator arbitration remains separate and keeps the Initial indicator in these root-only counterfactuals.

## Call graph and failure semantics

Expected semantic calls remain 1 for direct return, 2 for one-source verification, and 3 for both-source verification. A future implementation must not add a second general cross-source model call unless non-redundant causal information is proved. Terminal failures retain no semantic imputation. Sidecar hashes/accounting cannot be treated as request or response contents.

## Selected architecture

`METRICS_ARBITRATION` keeps one model call and adds a deterministic root-only M3 rule: Metrics Top-1, margin at least 0.25, and Initial rank absent or greater than 2. Candidate provenance is explicit and no new message is sent. The Initial indicator remains fixed. Multi-Agent root arbitration is not retained because source comparison and non-redundancy gates failed.
"""


def _render_decision(report: Mapping[str, Any]) -> str:
    selected = report["architecture_decision"]
    frontiers = report["metrics_frontiers"]
    trace = report["trace_opportunity"]
    communication = report["message_contract_ablation"]
    fusion = report["fusion_frontier"]
    return f"""# RCAEval Next Architecture Decision

## Decision

`{selected['decision']}`

{selected['reason']}

Exactly one option is selected. The rejected options remain useful hypotheses, not approved runtime work.

The selected M3 rule has preserved-candidate net rescue `{frontiers['candidate-3']['M3']['net_rescue']}`, `{frontiers['candidate-4']['M3']['net_rescue']}`, and `{frontiers['candidate-5']['M3']['net_rescue']}`, with root damage `{frontiers['candidate-3']['M3']['root_damage']}`, `{frontiers['candidate-4']['M3']['root_damage']}`, and `{frontiers['candidate-5']['M3']['root_damage']}`. Candidate-5 clears the primary `rescue > damage`, `net >= 2`, `damage <= 2` gate; Candidate-3/4 clear the robustness gate.

## Decision order

1. Choose `METRICS_ARBITRATION` only when one frozen M-rule has positive, low-damage rescue across preserved candidates.
2. Otherwise choose `METRICS_PLUS_TRACE_VERIFICATION` only when Trace visibility clears the gate **and** the bounded projection contains genuine causal direction/propagation information.
3. Otherwise choose `COMMUNICATION_REPAIRED_CROSS_SOURCE_VERIFIER` only when missing message fields create at least four actionable cases, relaxed Fusion has positive net rescue, and the new verifier is not redundant with Initial.
4. Otherwise choose `STRONG_SINGLE_RECOMMENDED`.

## Why communication alone is insufficient

Candidate-5 omitted Metrics provenance, Gate reasons, and Initial rationale from its Logs pairwise input, so the communication diagnosis is real and testable. But the Initial already consumes the full bounded ArchitectureContext. A new cross-source verifier would therefore repeat the same bounded evidence unless it receives new causal structure. Current Trace summaries provide per-service anomaly summaries rather than caller/callee edges or error propagation. The decision consequently follows measured rescue/damage and redundancy, not Agent count.

Static communication repair eligibility is `{communication['communication_repair_eligible']}`; C4 redundancy is `{communication['cross_source_verifier_redundant']}`. Trace support is `{trace['support_gate']}` with genuine causal information `{trace['genuine_causal_information']}`. The best relaxed Fusion net rescue is `{max(fusion[name]['net_rescue'] for name in ('F1', 'F2', 'F3'))}`. These fail the three alternative decision gates.

## Runtime shape and cost

- Multi-Agent root arbitration retained: **No**.
- Recommended roles: one Strong Single model proposal plus one deterministic Metrics root arbiter.
- Expected model calls: **1**.
- Implementation scope if separately authorized: root-only M3 arbitration with the Initial indicator frozen, typed provenance, and explicit damage monitoring.

## Non-authorization

This decision does not authorize Candidate-6, a new runtime Agent, any Provider call, candidate rerun, RE2-TT access, new data, release, merge, or PR #19 modification.
"""


def _write_private_bundle(root: Path, outputs: Mapping[str, object]) -> None:
    resolved = root.expanduser().resolve()
    if resolved.exists():
        raise ValueError("private audit output root already exists")
    temporary = resolved.with_name(f"{resolved.name}.tmp")
    if temporary.exists():
        raise ValueError("private audit temporary root already exists")
    temporary.mkdir(parents=True, mode=0o700)
    temporary.chmod(0o700)
    for name, value in outputs.items():
        path = temporary / name
        if name.endswith(".jsonl"):
            if not isinstance(value, list):
                raise ValueError("private JSONL output must be a list")
            _write_jsonl(path, value)
        else:
            _write_json(path, value, private=True)
    os.replace(temporary, resolved)


def validate_output_boundaries(
    private_root: Path, public_paths: Sequence[Path]
) -> None:
    resolved_private = private_root.expanduser().resolve()
    if any((ancestor / ".git").exists() for ancestor in (resolved_private, *resolved_private.parents)):
        raise ValueError("private audit output must be Git-external")
    for path in public_paths:
        resolved_public = path.expanduser().resolve()
        if (
            resolved_public == resolved_private
            or resolved_private in resolved_public.parents
        ):
            raise ValueError("public output may not overlap the private audit root")


def _require_no_provider_environment() -> None:
    present = [name for name in _PROVIDER_ENV_NAMES if os.environ.get(name)]
    if present:
        raise ValueError("Provider environment must be removed for this offline audit")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-3-root", type=Path, required=True)
    parser.add_argument("--candidate-4-root", type=Path, required=True)
    parser.add_argument("--candidate-5-root", type=Path, required=True)
    parser.add_argument("--ob-root", type=Path, required=True)
    parser.add_argument("--ss-root", type=Path, required=True)
    parser.add_argument("--private-output-root", type=Path, required=True)
    parser.add_argument(
        "--public-json",
        type=Path,
        default=PROJECT_ROOT / "docs/analysis/rcaeval-multiagent-communication-audit.json",
    )
    parser.add_argument(
        "--public-markdown",
        type=Path,
        default=PROJECT_ROOT / "docs/analysis/rcaeval-multiagent-communication-audit.md",
    )
    parser.add_argument(
        "--public-spec",
        type=Path,
        default=PROJECT_ROOT / "docs/design/rcaeval-multiagent-communication-v2-spec.md",
    )
    parser.add_argument(
        "--public-decision",
        type=Path,
        default=PROJECT_ROOT / "docs/design/rcaeval-next-architecture-decision.md",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _require_no_provider_environment()
    validate_output_boundaries(
        args.private_output_root,
        (
            args.public_json,
            args.public_markdown,
            args.public_spec,
            args.public_decision,
        ),
    )
    candidate_roots = {
        "candidate-3": args.candidate_3_root.expanduser().resolve(strict=True),
        "candidate-4": args.candidate_4_root.expanduser().resolve(strict=True),
        "candidate-5": args.candidate_5_root.expanduser().resolve(strict=True),
    }
    expected_digests = {
        "candidate-3": "d3b43650b0165045d48917743e02b7dcc3771e96b90960360f9c98dc3a4360e5",
        "candidate-4": "a1f9e4037e762ba0968d64cc0ae9b0cac9bc9b24717f5b31d99cd9bfd0351ca5",
        "candidate-5": "4345a7fe7a7b89881a31c1b3260b078df1a1cf4da3bf2b8531208919fb51b904",
    }
    before: dict[str, Any] = {}
    for candidate, root in candidate_roots.items():
        digest, file_count, byte_count = _tree_digest(root)
        if digest != expected_digests[candidate]:
            raise ValueError(f"{candidate} frozen artifact digest differs")
        before[candidate] = {
            "tree_sha256": digest,
            "file_count": file_count,
            "byte_count": byte_count,
            "terminal_count": len(tuple((root / "terminal-records").glob("*.json"))),
            "sidecar_count": len(tuple((root / "provider-sidecars").rglob("*.json"))),
        }

    cases = _load_case_index(args.ob_root, args.ss_root)
    model_lock = _read_object(MODEL_LOCK_PATH)
    indicator_path = PROJECT_ROOT / str(model_lock["inherited_indicator_config_path"])
    indicator_sha = str(model_lock["inherited_indicator_config_sha256"])
    if hashlib.sha256(indicator_path.read_bytes()).hexdigest() != indicator_sha:
        raise ValueError("tracked Indicator config differs from model lock")
    indicator_config = load_indicator_config(
        indicator_path, expected_sha256=indicator_sha
    )
    projections: dict[str, dict[str, Any]] = {}
    rows = {
        candidate: _load_candidate_rows(
            candidate=candidate,
            root=root,
            cases=cases,
            projections=projections,
            indicator_config=indicator_config,
        )
        for candidate, root in candidate_roots.items()
    }
    report = build_public_report(rows)
    after = {
        candidate: _tree_digest(root)[0] for candidate, root in candidate_roots.items()
    }
    if any(after[name] != before[name]["tree_sha256"] for name in before):
        raise ValueError("frozen candidate artifacts changed during audit")
    inventory = {
        candidate: value
        | {
            "unchanged_after_analysis": after[candidate] == value["tree_sha256"]
        }
        for candidate, value in before.items()
    }
    private_outputs = _build_private_outputs(rows, inventory)

    assert_public_payload(report)
    assert_rate_contract(report)
    _write_private_bundle(args.private_output_root, private_outputs)
    _write_json(args.public_json, report, private=False)
    _write_text(args.public_markdown, _render_public_markdown(report))
    _write_text(args.public_spec, _render_v2_spec(report))
    _write_text(args.public_decision, _render_decision(report))
    print(
        json.dumps(
            {
                "decision": report["architecture_decision"]["decision"],
                "provider_calls": 0,
                "candidate_artifacts_unchanged": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
