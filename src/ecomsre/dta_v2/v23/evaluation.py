"""Frozen case construction, execution, and scoring for DTA v2.3."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from enum import Enum
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import (
    ActionCatalogV22,
    EvidenceActionV22,
    StaticTopologyV22,
)
from ecomsre.dta_v2.v22.diagnosis import AdmittedDiagnosisV22
from ecomsre.dta_v2.v22.gap_router_v222 import SOURCE_PREDICATE_CAPABILITIES_V222
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.memory import (
    EvidencePredicateV22,
    MemoryReadOutcomeV22,
    SalientEvidenceMemoryV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_replay import NormalizedPracticalCaseV22
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    PredicateRequirementV22,
    RequirementServiceBindingV22,
    SupportClauseV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    RecentChangeRecordV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22, ReplayCaptureV22
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    build_replay_target_coverage_v225,
    complete_resource_records_v225,
)
from ecomsre.dta_v2.v23.discovery_runtime import _build_read_outcome_v23
from ecomsre.dta_v2.v22.simple_provider import (
    ProviderTransportErrorV22,
    StdlibProviderTransportV22,
)
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.dta_v2.v23.contracts import ProvisionalIncidentReportV23
from ecomsre.dta_v2.v23.discovery_provider import (
    DISCOVERY_SYSTEM_PROMPT_V23,
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
    build_discovery_provider_request_v23,
    call_discovery_provider_v23,
)
from ecomsre.dta_v2.v23.discovery_router import (
    DiscoveryActionOptionV23,
    DiscoveryReadOutcomeClassV23,
    NegativeCoverageLedgerV23,
    build_discovery_plan_v23,
    record_discovery_outcome_v23,
    resolve_discovery_action_v23,
)
from ecomsre.dta_v2.v23.discovery_runtime import (
    _classify_discovery_outcome,
    _deterministic_development_report_v23,
)
from ecomsre.dta_v2.v23.generic_anomalies import extract_generic_anomalies_v23
from ecomsre.dta_v2.v23.novelty_gate import (
    NoveltyDispositionV23,
    NoveltyGateDecisionV23,
    derive_unresolved_interpretation_conflict_v23,
    evaluate_novelty_gate_v23,
)
from ecomsre.dta_v2.v23.known_admission import (
    KnownAdmissionStateV23,
    build_known_admission_state_v23,
)
from ecomsre.dta_v2.v23.ontology_view import (
    ActiveOntologyViewV23,
    build_active_ontology_view_v23,
)
from ecomsre.dta_v2.v23.residual_graph import (
    ResidualEvidenceGraphV23,
    build_known_terminal_candidates_v23,
    build_residual_evidence_graph_v23,
)


class EvaluationSourceSplitV23(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    EVALUATION = "EVALUATION"
    SYNTHETIC_DERIVED = "SYNTHETIC_DERIVED"


class EvaluationCategoryV23(str, Enum):
    NOVEL_HIDDEN = "NOVEL_HIDDEN"
    NOVEL_UNREGISTERED = "NOVEL_UNREGISTERED"
    REGISTERED_KNOWN = "REGISTERED_KNOWN"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_CONFLICT = "INSUFFICIENT_CONFLICT"


class MeasuredResultTerminalV23(str, Enum):
    EFFECT_OBSERVED = "DTA_V23_OPEN_WORLD_DISCOVERY_EFFECT_OBSERVED"
    MIXED_RESULT = "DTA_V23_OPEN_WORLD_DISCOVERY_MIXED_RESULT"
    NOT_OBSERVED = "DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED"


class EvaluationCaseSpecV23(DtaModelV22):
    case_id: str = Field(pattern=r"^ow-[0-9]{3}$")
    source_split: EvaluationSourceSplitV23
    source_case_id: str
    derivation_id: str = Field(pattern=r"^drv-[0-9]{3}$")


class EvaluationCaseSetV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-case-set.v1"]
    cases: tuple[EvaluationCaseSpecV23, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationCaseSetV23":
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("evaluation cases are not canonical")
        derivations = tuple(item.derivation_id for item in self.cases)
        if len(derivations) != len(set(derivations)):
            raise ValueError("evaluation derivation IDs are not unique")
        return self


class EvaluationTruthV23(DtaModelV22):
    case_id: str = Field(pattern=r"^ow-[0-9]{3}$")
    category: EvaluationCategoryV23
    expected_disposition: str
    expected_root_service: str | None
    expected_broad_domain: str | None
    expected_mechanism: MechanismV22 | None
    semantic_concepts: tuple[str, ...]
    counterfactual_pair_id: str | None
    requires_discovery_read: StrictBool
    empty_or_misleading_action: StrictBool

    @model_validator(mode="after")
    def require_truth(self) -> "EvaluationTruthV23":
        novelty = self.category in {
            EvaluationCategoryV23.NOVEL_HIDDEN,
            EvaluationCategoryV23.NOVEL_UNREGISTERED,
        }
        if novelty != (self.expected_root_service is not None):
            if self.category is not EvaluationCategoryV23.REGISTERED_KNOWN:
                raise ValueError("evaluation root differs from category")
        if self.category is EvaluationCategoryV23.NOVEL_HIDDEN:
            if self.expected_mechanism is None:
                raise ValueError("hidden novelty truth lacks a mechanism")
        elif self.category is EvaluationCategoryV23.REGISTERED_KNOWN:
            if self.expected_mechanism is None or self.expected_root_service is None:
                raise ValueError("known truth lacks mechanism or root")
        elif self.expected_mechanism is not None:
            raise ValueError("non-registered truth carries a known mechanism")
        return self


class EvaluationTruthSetV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-truth-set.v1"]
    truths: tuple[EvaluationTruthV23, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationTruthSetV23":
        ids = tuple(item.case_id for item in self.truths)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("evaluation truths are not canonical")
        counts = {
            category: sum(item.category is category for item in self.truths)
            for category in EvaluationCategoryV23
        }
        expected = {
            EvaluationCategoryV23.NOVEL_HIDDEN: 10,
            EvaluationCategoryV23.NOVEL_UNREGISTERED: 4,
            EvaluationCategoryV23.REGISTERED_KNOWN: 4,
            EvaluationCategoryV23.NO_INCIDENT: 3,
            EvaluationCategoryV23.INSUFFICIENT_CONFLICT: 3,
        }
        if counts != expected:
            raise ValueError("evaluation truth composition differs")
        if sum(item.requires_discovery_read for item in self.truths) < 8:
            raise ValueError("evaluation lacks eight discovery-read novelty cases")
        if sum(item.empty_or_misleading_action for item in self.truths) < 4:
            raise ValueError("evaluation lacks four empty or misleading actions")
        pairs = {
            item.counterfactual_pair_id
            for item in self.truths
            if item.counterfactual_pair_id is not None
        }
        if len(pairs) < 4 or any(
            sum(item.counterfactual_pair_id == pair for item in self.truths) != 2
            for pair in pairs
        ):
            raise ValueError("evaluation counterfactual pairs differ")
        return self

    def require(self, case_id: str) -> EvaluationTruthV23:
        item = next((value for value in self.truths if value.case_id == case_id), None)
        if item is None:
            raise ValueError("evaluation truth case is absent")
        return item


class EvaluationOntologyViewSpecV23(DtaModelV22):
    case_id: str = Field(pattern=r"^ow-[0-9]{3}$")
    hidden_mechanism: MechanismV22 | None


class EvaluationOntologyViewSetV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-ontology-view-set.v1"]
    views: tuple[EvaluationOntologyViewSpecV23, ...] = Field(
        min_length=24, max_length=24
    )

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationOntologyViewSetV23":
        ids = tuple(item.case_id for item in self.views)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("evaluation ontology views are not canonical")
        registered = (
            MechanismV22.CONFIGURATION_ERROR,
            MechanismV22.SERVICE_UNAVAILABLE,
            MechanismV22.CPU_SATURATION,
            MechanismV22.MEMORY_LEAK,
            MechanismV22.DEPENDENCY_LATENCY,
        )
        if any(
            sum(item.hidden_mechanism is mechanism for item in self.views) != 2
            for mechanism in registered
        ):
            raise ValueError("evaluation leave-one-out denominator differs")
        return self

    def require(self, case_id: str) -> EvaluationOntologyViewSpecV23:
        item = next((value for value in self.views if value.case_id == case_id), None)
        if item is None:
            raise ValueError("evaluation ontology view is absent")
        return item


class EvaluationPreflightV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-preflight.v1"]
    case_count: Literal[24]
    planned_runs: Literal[48]
    execution_count_before: Literal[0]
    provider_model: str
    cases_sha256: str
    truth_sha256: str
    ontology_views_sha256: str
    manifest_sha256: str | None
    output_path: str
    status: Literal["DTA_V23_FIXED_EVALUATION_PREFLIGHT_PASS"]


class ManifestFileBindingV23(DtaModelV22):
    path: str
    sha256: str


class EvaluationManifestV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-manifest.v2"]
    base_commit: Literal["f17688f4c313b1483bfb7c56675c429605faf489"]
    branch: Literal["codex/dta-v23-open-world-discovery"]
    provider_model: str
    planned_case_count: Literal[24]
    planned_run_count: Literal[48]
    planned_execution_count: Literal[1]
    cases: ManifestFileBindingV23
    truth: ManifestFileBindingV23
    ontology_views: ManifestFileBindingV23
    source_case_sets: tuple[ManifestFileBindingV23, ...] = Field(
        min_length=2,
        max_length=2,
    )
    runtime_sources: tuple[ManifestFileBindingV23, ...] = Field(
        min_length=10,
        max_length=16,
    )
    discovery_system_prompt_sha256: str
    output_json: str
    output_markdown: str
    fixed_at_utc: datetime

    @model_validator(mode="after")
    def require_manifest(self) -> "EvaluationManifestV23":
        _require_utc_manifest(self.fixed_at_utc)
        paths = tuple(item.path for item in self.source_case_sets)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("manifest source case sets are not canonical")
        runtime_paths = tuple(item.path for item in self.runtime_sources)
        if runtime_paths != tuple(sorted(set(runtime_paths))):
            raise ValueError("manifest runtime sources are not canonical")
        return self


def _require_utc_manifest(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("manifest timestamp must be timezone-aware UTC")


def load_evaluation_manifest_v23(path: Path) -> EvaluationManifestV23:
    return EvaluationManifestV23.model_validate_json(path.read_bytes())


def load_evaluation_case_set_v23(path: Path) -> EvaluationCaseSetV23:
    return EvaluationCaseSetV23.model_validate_json(path.read_bytes())


def load_evaluation_truth_set_v23(path: Path) -> EvaluationTruthSetV23:
    return EvaluationTruthSetV23.model_validate_json(path.read_bytes())


def load_evaluation_ontology_views_v23(path: Path) -> EvaluationOntologyViewSetV23:
    return EvaluationOntologyViewSetV23.model_validate_json(path.read_bytes())


def _opaque_service(case_id: str, service: str) -> str:
    return f"svc-{semantic_sha256_v22({'case_id': case_id, 'service': service})[:10]}"


_COUNTERFACTUAL_TARGET_LOW_V23 = frozenset({"ow-001", "ow-003", "ow-011", "ow-013"})
_COUNTERFACTUAL_TARGET_HIGH_V23 = frozenset({"ow-002", "ow-004", "ow-012", "ow-014"})


def _counterfactual_control_alias_v23(*, case_id: str, target: str) -> str:
    target_low = case_id in _COUNTERFACTUAL_TARGET_LOW_V23
    if not target_low and case_id not in _COUNTERFACTUAL_TARGET_HIGH_V23:
        raise ValueError("case is not a fixed counterfactual target")
    for ordinal in range(256):
        candidate = _opaque_service(case_id, f"counterfactual-control-{ordinal}")
        if (target < candidate) == target_low:
            return candidate
    raise ValueError("cannot construct a bounded opaque counterfactual control")


def _replace_service_text(value: str, mapping: dict[str, str]) -> str:
    result = value
    for source in sorted(mapping, key=len, reverse=True):
        result = result.replace(source, mapping[source])
    return result


def _anonymize_case(
    *,
    source: NormalizedPracticalCaseV22,
    spec: EvaluationCaseSpecV23,
) -> NormalizedPracticalCaseV22:
    mapping = {
        service: _opaque_service(spec.case_id, service)
        for service in source.candidate_services
    }
    candidates = tuple(sorted(mapping.values()))
    control_service: str | None = None
    if spec.case_id in (
        _COUNTERFACTUAL_TARGET_LOW_V23 | _COUNTERFACTUAL_TARGET_HIGH_V23
    ) and spec.case_id in {"ow-001", "ow-002", "ow-003", "ow-004"}:
        if len(candidates) != 1:
            raise ValueError("fixed replay counterfactual source is not single-target")
        control_service = _counterfactual_control_alias_v23(
            case_id=spec.case_id,
            target=candidates[0],
        )
        candidates = tuple(sorted((*candidates, control_service)))
    capture = source.capture
    resources = tuple(
        ResourceUsageRecordV22(
            **{
                **item.model_dump(mode="python"),
                "service": mapping[item.service],
            }
        )
        for item in capture.resources
        if item.service in mapping
    )
    resources = complete_resource_records_v225(
        candidate_services=candidates,
        records=resources,
    )
    projected = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=capture.captured_at,
        metrics=(
            *tuple(
                MetricFactV22(
                    **{
                        **item.model_dump(mode="python"),
                        "service": mapping[item.service],
                    }
                )
                for item in capture.metrics
                if item.service in mapping
            ),
            *(
                tuple(
                    MetricFactV22(
                        **{
                            **item.model_dump(mode="python"),
                            "service": control_service,
                            "value": (
                                None
                                if item.value is None
                                else 0.01
                                if item.metric_kind is MetricKindV22.ERROR_RATE
                                else 10.0
                                if item.metric_kind is MetricKindV22.LATENCY_P95_MS
                                else 100.0
                                if item.metric_kind is MetricKindV22.REQUEST_SUPPORT
                                else item.value
                            ),
                        }
                    )
                    for item in capture.metrics
                    if item.service in mapping
                )
                if control_service is not None
                else ()
            ),
        ),
        logs=tuple(
            LogRecordV22(
                **{
                    **item.model_dump(mode="python"),
                    "service": mapping[item.service],
                    "message": _replace_service_text(item.message, mapping),
                }
            )
            for item in capture.logs
            if item.service in mapping
        ),
        traces=tuple(
            TraceSpanV22(
                **{
                    **item.model_dump(mode="python"),
                    "service_path": tuple(
                        mapping[value] for value in item.service_path
                    ),
                    "service": mapping[item.service],
                    "parent_service": (
                        mapping[item.parent_service]
                        if item.parent_service is not None
                        else None
                    ),
                    "operation": _replace_service_text(item.operation, mapping),
                }
            )
            for item in capture.traces
            if item.service in mapping
            and all(value in mapping for value in item.service_path)
            and (item.parent_service is None or item.parent_service in mapping)
        ),
        runtime=(
            *tuple(
                RuntimeRecordV22(
                    **{
                        **item.model_dump(mode="python"),
                        "service": mapping[item.service],
                    }
                )
                for item in capture.runtime
                if item.service in mapping
            ),
            *(
                (
                    RuntimeRecordV22(
                        schema_version="dta-v22.runtime-record.v1",
                        service=control_service,
                        state=RuntimeStateV22.RUNNING,
                        healthy=True,
                        restart_count=0,
                    ),
                )
                if control_service is not None
                else ()
            ),
        ),
        resources=resources,
        changes=tuple(
            RecentChangeRecordV22(
                **{
                    **item.model_dump(mode="python"),
                    "service": mapping[item.service],
                }
            )
            for item in capture.changes
            if item.service in mapping
        ),
        source_failures=capture.source_failures,
    )
    edges: tuple[tuple[str, str], ...] = tuple(
        sorted(
            (
                (mapping[left], mapping[right])
                if mapping[left] < mapping[right]
                else (mapping[right], mapping[left])
            )
            for left, right in source.topology_edges
            if left in mapping and right in mapping
        )
    )
    if control_service is not None:
        target = next(iter(mapping.values()))
        edges = (
            (target, control_service)
            if target < control_service
            else (control_service, target),
        )
    return NormalizedPracticalCaseV22(
        schema_version="dta-v22.practical-normalized-case.v1",
        case_id=spec.case_id,
        source_bytes_sha256=semantic_sha256_v22(
            {
                "source_sha256": source.source_bytes_sha256,
                "case_id": spec.case_id,
                "derivation_id": spec.derivation_id,
                "capture": projected.model_dump(mode="json"),
            }
        ),
        candidate_services=candidates,
        topology_edges=edges,
        capture=projected,
        normalization_notes=(
            "DTA v2.3 opaque replay projection.",
            "Missing resource controls are completed with frozen normal records.",
        ),
    )


def _synthetic_case(spec: EvaluationCaseSpecV23) -> NormalizedPracticalCaseV22:
    if spec.source_case_id not in {"syn-01", "syn-02", "syn-03", "syn-04"}:
        raise ValueError("unknown v2.3 synthetic source")
    originals = ("node-a", "node-b")
    mapping = {value: _opaque_service(spec.case_id, value) for value in originals}
    candidates = tuple(sorted(mapping.values()))
    if spec.case_id in _COUNTERFACTUAL_TARGET_LOW_V23:
        root = candidates[0]
    elif spec.case_id in _COUNTERFACTUAL_TARGET_HIGH_V23:
        root = candidates[1]
    else:
        raise ValueError("synthetic evaluation case lacks counterfactual target role")
    other = next(item for item in candidates if item != root)
    ordinal = int(spec.case_id.split("-")[1])
    captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=ordinal)
    window_start = captured_at - timedelta(seconds=300)
    metrics = tuple(
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service=service,
            metric_kind=kind,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=5,
            value=(
                0.30
                if service == root and kind is MetricKindV22.ERROR_RATE
                else 500.0
                if service == root and kind is MetricKindV22.LATENCY_P95_MS
                else 0.01
                if kind is MetricKindV22.ERROR_RATE
                else 10.0
                if kind is MetricKindV22.LATENCY_P95_MS
                else 100.0
            ),
            unit=METRIC_UNIT_BY_KIND_V22[kind],
            window_started_at=window_start,
            window_ended_at=captured_at,
        )
        for service in candidates
        for kind in (
            MetricKindV22.ERROR_RATE,
            MetricKindV22.LATENCY_P95_MS,
            MetricKindV22.REQUEST_SUPPORT,
        )
    )
    message = (
        "worker pool capacity wait exceeded while leases remain occupied"
        if spec.source_case_id in {"syn-01", "syn-02"}
        else "queue backlog throttle gate is delaying new work admission"
    )
    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=captured_at,
        metrics=metrics,
        logs=(
            LogRecordV22(
                schema_version="dta-v22.log-record.v1",
                observed_at=captured_at,
                service=root,
                severity="ERROR",
                message=message,
            ),
        ),
        traces=(
            TraceSpanV22(
                schema_version="dta-v22.trace-span.v1",
                observed_at=captured_at,
                service_path=(other, root),
                service=root,
                parent_service=other,
                operation="bounded work admission",
                status=SpanStatusV22.ERROR,
                duration_ms=10.0,
                first_error_location=True,
            ),
        ),
        runtime=tuple(
            RuntimeRecordV22(
                schema_version="dta-v22.runtime-record.v1",
                service=service,
                state=RuntimeStateV22.RUNNING,
                healthy=True,
                restart_count=0,
            )
            for service in candidates
        ),
        resources=complete_resource_records_v225(
            candidate_services=candidates,
            records=(),
        ),
        changes=(),
        source_failures=(),
    )
    return NormalizedPracticalCaseV22(
        schema_version="dta-v22.practical-normalized-case.v1",
        case_id=spec.case_id,
        source_bytes_sha256=semantic_sha256_v22(
            {
                "case_id": spec.case_id,
                "derivation_id": spec.derivation_id,
                "capture": capture.model_dump(mode="json"),
            }
        ),
        candidate_services=candidates,
        topology_edges=((root, other) if root < other else (other, root),),
        capture=capture,
        normalization_notes=(
            "Synthetic derived open-world case with no new PredicateKind.",
        ),
    )


def materialize_evaluation_case_v23(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV23,
) -> NormalizedPracticalCaseV22:
    if spec.source_split is EvaluationSourceSplitV23.SYNTHETIC_DERIVED:
        return _synthetic_case(spec)
    split = (
        "development"
        if spec.source_split is EvaluationSourceSplitV23.DEVELOPMENT
        else "evaluation"
    )
    case_set = load_practical_case_set_v22(
        repository_root / f"config/dta-v22-sprint/{split}/cases.json"
    )
    source_spec = next(
        (item for item in case_set.cases if item.case_id == spec.source_case_id),
        None,
    )
    if source_spec is None:
        raise ValueError("v2.3 source case is absent")
    source = materialize_practical_case_v22(
        spec=source_spec,
        repository_root=repository_root,
    )
    return _anonymize_case(source=source, spec=spec)


def _all_discriminating_outcomes(
    case: NormalizedPracticalCaseV22,
) -> tuple[object, ...]:
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _snapshot, _full, catalog = _bootstrap(
        case=case,
        topology=topology,
        run_id=semantic_sha256_v22({"case": case.case_id, "mode": "audit"})[:32],
    )
    backend = QuerySpecificReplayBackendV22(case.capture)
    additions: list[object] = []
    for source in (
        EvidenceSourceV22.LOGS,
        EvidenceSourceV22.TRACES,
        EvidenceSourceV22.CHANGES,
    ):
        for service in case.candidate_services:
            action = next(
                item
                for item in catalog.registry_actions
                if item.source is source and item.target_services == (service,)
            )
            additions.append(backend.execute(action))
    coverage = build_replay_target_coverage_v225(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=case.candidate_services,
        covered_target_services=case.candidate_services,
    )
    resource_action = contrastive_resource_action_if_eligible_v225(
        coverage=coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=len(case.candidate_services),
        remaining_budget=3.0,
        bundle_mode=True,
    )
    if resource_action is None:
        raise ValueError("evaluation case lacks target-complete resources")
    additions.append(
        _build_read_outcome_v23(action=resource_action, capture=case.capture)
    )
    return (*outcomes, *additions)


def verify_unregistered_case_has_no_known_terminal_v23(
    *,
    case: NormalizedPracticalCaseV22,
) -> bool:
    memory, _ = build_memory_views_v22(
        outcomes=_all_discriminating_outcomes(case),  # type: ignore[arg-type]
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    view = build_active_ontology_view_v23(candidate_services=case.candidate_services)
    admission = build_known_admission_state_v23(
        view=view,
        memory=memory,
        topology_edges=case.topology_edges,
    )
    known = build_known_terminal_candidates_v23(
        admitted_diagnoses=admission.admitted_diagnoses,
    )
    return not known


class LazyTruthStoreV23:
    """Read evaluator truth only after both arms for the current case complete."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.load_count = 0

    def load_case_after_both_arms(
        self,
        case_id: str,
        *,
        arms_completed: int,
    ) -> EvaluationTruthV23:
        if arms_completed != 2:
            raise ValueError("truth requires both arms to complete first")
        self.load_count += 1
        return load_evaluation_truth_set_v23(self.path).require(case_id)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_evaluation_preflight_v23(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_path: Path,
    ontology_views_path: Path,
    output_path: Path,
    expected_provider_model: str,
    manifest_path: Path | None = None,
) -> EvaluationPreflightV23:
    if output_path.exists():
        raise FileExistsError(f"write-once evaluation output exists: {output_path}")
    cases = load_evaluation_case_set_v23(cases_path)
    truths = load_evaluation_truth_set_v23(truth_path)
    views = load_evaluation_ontology_views_v23(ontology_views_path)
    ids = tuple(item.case_id for item in cases.cases)
    if ids != tuple(item.case_id for item in truths.truths) or ids != tuple(
        item.case_id for item in views.views
    ):
        raise ValueError("evaluation case, truth, and view IDs differ")
    progress_path = repository_root / "docs/analysis/dta-v23-open-world-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    execution_count = progress.get(
        "valid_fixed_evaluation_execution_count",
        progress.get("fixed_evaluation_execution_count"),
    )
    if execution_count != 0:
        raise ValueError("fixed evaluation execution count is not zero")
    if not expected_provider_model.strip():
        raise ValueError("fixed evaluation provider model is empty")
    manifest_sha256 = None
    if manifest_path is not None:
        manifest = load_evaluation_manifest_v23(manifest_path)
        bindings = (
            (manifest.cases, cases_path),
            (manifest.truth, truth_path),
            (manifest.ontology_views, ontology_views_path),
        )
        for binding, actual in bindings:
            if repository_root / binding.path != actual.resolve():
                raise ValueError("manifest evaluation path binding differs")
            if binding.sha256 != _file_sha256(actual):
                raise ValueError("manifest evaluation file digest differs")
        for binding in manifest.source_case_sets:
            source_path = repository_root / binding.path
            if binding.sha256 != _file_sha256(source_path):
                raise ValueError("manifest source case-set digest differs")
        for binding in manifest.runtime_sources:
            source_path = repository_root / binding.path
            if binding.sha256 != _file_sha256(source_path):
                raise ValueError("manifest runtime source digest differs")
        prompt_sha256 = hashlib.sha256(
            DISCOVERY_SYSTEM_PROMPT_V23.encode("utf-8")
        ).hexdigest()
        if manifest.discovery_system_prompt_sha256 != prompt_sha256:
            raise ValueError("manifest discovery Prompt digest differs")
        if manifest.provider_model != expected_provider_model:
            raise ValueError("manifest provider model differs")
        expected_output = repository_root / manifest.output_json
        if output_path.resolve() != expected_output.resolve():
            raise ValueError("manifest output path differs")
        manifest_sha256 = _file_sha256(manifest_path)
    return EvaluationPreflightV23(
        schema_version="dta-v23.evaluation-preflight.v1",
        case_count=24,
        planned_runs=48,
        execution_count_before=0,
        provider_model=expected_provider_model,
        cases_sha256=_file_sha256(cases_path),
        truth_sha256=_file_sha256(truth_path),
        ontology_views_sha256=_file_sha256(ontology_views_path),
        manifest_sha256=manifest_sha256,
        output_path=str(output_path.relative_to(repository_root))
        if output_path.is_relative_to(repository_root)
        else str(output_path),
        status="DTA_V23_FIXED_EVALUATION_PREFLIGHT_PASS",
    )


def score_measured_terminal_v23(
    *,
    novelty_recall: float,
    root_localization: float,
    broad_domain_accuracy: float,
    evidence_ref_validity: float,
    false_novel_rate: float,
    known_accuracy_drop_cases: int | float,
    no_incident_accuracy_drop_cases: int | float,
    action_authority_violations: int | float,
) -> MeasuredResultTerminalV23:
    if (
        novelty_recall >= 0.70
        and root_localization >= 0.70
        and broad_domain_accuracy >= 0.65
        and evidence_ref_validity >= 0.90
        and false_novel_rate <= 0.20
        and known_accuracy_drop_cases <= 1
        and no_incident_accuracy_drop_cases <= 1
        and action_authority_violations == 0
    ):
        return MeasuredResultTerminalV23.EFFECT_OBSERVED
    if (
        novelty_recall >= 0.50
        and evidence_ref_validity >= 0.80
        and action_authority_violations == 0
    ):
        return MeasuredResultTerminalV23.MIXED_RESULT
    return MeasuredResultTerminalV23.NOT_OBSERVED


class EvaluationArmV23(str, Enum):
    CLOSED_WORLD_ONLY = "CLOSED_WORLD_ONLY"
    OPEN_WORLD_DISCOVERY = "OPEN_WORLD_DISCOVERY"


class ProviderCostV23(DtaModelV22):
    provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0.0)


class EvaluationArmRunV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-arm-run.v2"]
    case_id: str
    arm: EvaluationArmV23
    case_bytes_sha256: str
    active_view_sha256: str
    bootstrap_memory_sha256: str
    common_memory_sha256: str
    common_read_count: StrictInt = Field(ge=0, le=2)
    discovery_read_count: StrictInt = Field(ge=0, le=3)
    final_disposition: str
    known_admission_sha256: str
    admitted_diagnosis: AdmittedDiagnosisV22 | None
    known_mechanism: MechanismV22 | None
    known_root_service: str | None
    no_incident_admissible: StrictBool
    residual_graph: ResidualEvidenceGraphV23 | None
    novelty_decision: NoveltyGateDecisionV23 | None
    memory_evidence_refs: tuple[str, ...]
    negative_coverage: NegativeCoverageLedgerV23 | None
    provisional_report: ProvisionalIncidentReportV23 | None
    provider_error_code: str | None
    provider_cost: ProviderCostV23
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    action_authority_violations: Literal[0]
    run_sha256: str

    @model_validator(mode="after")
    def require_run(self) -> "EvaluationArmRunV23":
        if self.arm is EvaluationArmV23.CLOSED_WORLD_ONLY:
            if self.discovery_read_count or self.provisional_report is not None:
                raise ValueError("closed-world arm received open-world capability")
            if any(
                value is not None
                for value in (
                    self.residual_graph,
                    self.novelty_decision,
                    self.negative_coverage,
                )
            ):
                raise ValueError("closed-world arm received discovery state")
        elif any(
            value is None
            for value in (
                self.residual_graph,
                self.novelty_decision,
                self.negative_coverage,
            )
        ):
            raise ValueError("open-world arm lacks discovery state")
        if (self.admitted_diagnosis is None) != (self.known_mechanism is None):
            raise ValueError("known terminal projection differs from v2.2 admission")
        if self.admitted_diagnosis is not None and (
            self.known_mechanism is not self.admitted_diagnosis.mechanism
            or self.known_root_service != self.admitted_diagnosis.root_service
        ):
            raise ValueError("known terminal fields differ from v2.2 admission")
        if self.provisional_report is not None:
            if self.provisional_report.action_authority != "NONE":
                raise ValueError("evaluation report has action authority")
            if self.final_disposition not in {
                NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED.value,
                NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY.value,
            }:
                raise ValueError("evaluation report differs from disposition")
            cited = set(
                (
                    *self.provisional_report.supporting_evidence_refs,
                    *self.provisional_report.contradicting_evidence_refs,
                )
            )
            if not cited.issubset(self.memory_evidence_refs):
                raise ValueError("evaluation report cites evidence outside memory")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )
        if self.run_sha256 != expected:
            raise ValueError("evaluation arm run digest differs")
        return self


class EvaluationCasePairV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-case-pair.v2"]
    case_id: str
    closed_world: EvaluationArmRunV23
    open_world: EvaluationArmRunV23
    evaluator_truth: EvaluationTruthV23

    @model_validator(mode="after")
    def require_pair(self) -> "EvaluationCasePairV23":
        if {
            self.case_id,
            self.closed_world.case_id,
            self.open_world.case_id,
            self.evaluator_truth.case_id,
        } != {self.case_id}:
            raise ValueError("evaluation pair case IDs differ")
        if self.closed_world.case_bytes_sha256 != self.open_world.case_bytes_sha256:
            raise ValueError("evaluation arms use different case bytes")
        if self.closed_world.active_view_sha256 != self.open_world.active_view_sha256:
            raise ValueError("evaluation arms use different ontology views")
        if (
            self.closed_world.bootstrap_memory_sha256
            != self.open_world.bootstrap_memory_sha256
            or self.closed_world.common_memory_sha256
            != self.open_world.common_memory_sha256
        ):
            raise ValueError("evaluation arms use different common evidence")
        if (
            self.closed_world.known_admission_sha256
            != self.open_world.known_admission_sha256
        ):
            raise ValueError("evaluation arms use different v2.2 Diagnosis admission")
        return self


@dataclass(frozen=True, slots=True)
class _CommonContextV23:
    case: NormalizedPracticalCaseV22
    view: ActiveOntologyViewV23
    outcomes: tuple[MemoryReadOutcomeV22, ...]
    memory: SalientEvidenceMemoryV22
    admission: KnownAdmissionStateV23
    catalog: ActionCatalogV22
    common_action_ids: tuple[str, ...]
    bootstrap_memory_sha256: str
    common_read_count: int


def _case_state(
    *,
    case: NormalizedPracticalCaseV22,
    admission: KnownAdmissionStateV23,
    memory: SalientEvidenceMemoryV22,
    negative_coverage: NegativeCoverageLedgerV23,
) -> tuple[ResidualEvidenceGraphV23, NoveltyGateDecisionV23]:
    known = build_known_terminal_candidates_v23(
        admitted_diagnoses=admission.admitted_diagnoses,
    )
    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=case.candidate_services,
    )
    graph = build_residual_evidence_graph_v23(
        candidate_services=case.candidate_services,
        generic_anomalies=anomalies,
        known_terminal_candidates=known,
        memory=memory,
    )
    failures = tuple(
        sorted(
            {
                item.source
                for item in negative_coverage.entries
                if item.outcome_class is DiscoveryReadOutcomeClassV23.SOURCE_FAILURE
            },
            key=lambda item: item.value,
        )
    )
    decision = evaluate_novelty_gate_v23(
        graph=graph,
        no_incident_admissible=admission.no_incident_admissible,
        remaining_budget_before_discovery=3.0,
        required_source_failures=failures,
        conflicting_evidence=(
            admission.conflicting_evidence
            or derive_unresolved_interpretation_conflict_v23(
                graph=graph,
                bounded_reads_completed=len(negative_coverage.entries),
            )
        ),
    )
    return graph, decision


def _option_from_action(action: object) -> DiscoveryActionOptionV23:
    targets = tuple(getattr(action, "target_services"))
    return DiscoveryActionOptionV23(
        action_id=str(getattr(action, "action_id")),
        source=getattr(action, "source"),
        target_services=targets,
        request_sha256=str(getattr(action, "request_sha256")),
        coverage_keys=tuple(getattr(action, "coverage_keys")),
        weighted_cost=float(getattr(action, "weighted_cost")),
        multi_target=len(targets) > 1,
    )


def _common_candidate_action(
    *,
    view: ActiveOntologyViewV23,
    memory: SalientEvidenceMemoryV22,
    topology_edges: tuple[tuple[str, str], ...],
    catalog: ActionCatalogV22,
    executed_action_ids: set[str],
) -> EvidenceActionV22 | None:
    """Route shared v2.2 reads from support-clause gaps, never v2.3 state."""

    def parent_for(target: str, mechanism: MechanismV22) -> str | None:
        if mechanism is not MechanismV22.DEPENDENCY_LATENCY:
            return None
        return next(
            (
                right if left == target else left
                for left, right in topology_edges
                if target in {left, right}
            ),
            None,
        )

    def matches(
        *,
        predicate: EvidencePredicateV22,
        requirement: PredicateRequirementV22,
        target: str,
        parent: str | None,
    ) -> bool:
        if getattr(predicate, "predicate_kind") is not getattr(
            requirement, "predicate_kind"
        ):
            return False
        allowed = {target}
        if (
            getattr(requirement, "service_binding")
            is RequirementServiceBindingV22.TARGET_OR_PARENT
            and parent is not None
        ):
            allowed.add(parent)
        if getattr(predicate, "service") not in allowed:
            return False
        return (
            not getattr(requirement, "require_exact_parent")
            or getattr(predicate, "parent_service") == parent
        )

    gaps: list[tuple[str, str, PredicateRequirementV22, str, str | None, int]] = []
    for hypothesis in view.active_hypotheses:
        target = hypothesis.target_service
        if target is None or hypothesis.mechanism in {
            MechanismV22.NO_INCIDENT,
            MechanismV22.UNKNOWN,
        }:
            continue
        parent = parent_for(target, hypothesis.mechanism)
        if hypothesis.mechanism is MechanismV22.DEPENDENCY_LATENCY and parent is None:
            continue
        clauses = tuple(
            item
            for item in view.active_support_clauses
            if item.mechanism is hypothesis.mechanism
        )
        clause_gaps: list[
            tuple[SupportClauseV22, tuple[PredicateRequirementV22, ...], int]
        ] = []
        for clause in clauses:
            matched = tuple(
                requirement
                for requirement in clause.requirements
                if any(
                    matches(
                        predicate=predicate,
                        requirement=requirement,
                        target=target,
                        parent=parent,
                    )
                    for predicate in memory.predicates
                )
            )
            missing = tuple(
                requirement
                for requirement in clause.requirements
                if requirement not in matched
            )
            evidence_score = sum(
                requirement.predicate_kind.value != "RUNTIME_HEALTHY"
                for requirement in matched
            )
            clause_gaps.append((clause, missing, evidence_score))
        if not clause_gaps:
            continue
        minimum = min(len(missing) for _clause, missing, _score in clause_gaps)
        for clause, missing, evidence_score in clause_gaps:
            if len(missing) != minimum or not missing:
                continue
            for requirement in missing:
                gaps.append(
                    (
                        hypothesis.hypothesis_id,
                        str(getattr(clause, "clause_id")),
                        requirement,
                        target,
                        parent,
                        evidence_score,
                    )
                )

    ranked: list[tuple[tuple[object, ...], EvidenceActionV22]] = []
    for action in catalog.registry_actions:
        if (
            action.action_id in executed_action_ids
            or len(action.target_services) != 1
            or action.source is EvidenceSourceV22.RUNTIME
        ):
            continue
        hits = tuple(
            gap
            for gap in gaps
            if getattr(gap[2], "predicate_kind")
            in SOURCE_PREDICATE_CAPABILITIES_V222[action.source]
            and action.target_services[0]
            in (
                {gap[3], gap[4]}
                if getattr(gap[2], "service_binding")
                is RequirementServiceBindingV22.TARGET_OR_PARENT
                else {gap[3]}
            )
        )
        if not hits:
            continue
        completed_clauses = len({(item[0], item[1]) for item in hits})
        reduced_hypotheses = len({item[0] for item in hits})
        maximum_evidence_score = max(item[5] for item in hits)
        total_evidence_score = sum(item[5] for item in hits)
        ranked.append(
            (
                (
                    -maximum_evidence_score,
                    -total_evidence_score,
                    -completed_clauses,
                    -len(hits),
                    -reduced_hypotheses,
                    action.weighted_cost,
                    action.action_id,
                ),
                action,
            )
        )
    return min(ranked, key=lambda item: item[0])[1] if ranked else None


def _build_common_context_v23(
    *,
    case: NormalizedPracticalCaseV22,
    hidden_mechanism: MechanismV22 | None,
) -> _CommonContextV23:
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _snapshot, _full, catalog = _bootstrap(
        case=case,
        topology=topology,
        run_id=semantic_sha256_v22(
            {"case": case.case_id, "lane": "dta-v23-fixed-common"}
        )[:32],
    )
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    bootstrap_memory_sha256 = memory.memory_sha256
    view = build_active_ontology_view_v23(
        candidate_services=case.candidate_services,
        hidden_mechanisms=(hidden_mechanism,) if hidden_mechanism is not None else (),
    )
    backend = QuerySpecificReplayBackendV22(case.capture)
    executed: set[str] = set()
    common_reads = 0
    admission = build_known_admission_state_v23(
        view=view,
        memory=memory,
        topology_edges=case.topology_edges,
    )
    while (
        common_reads < 2
        and admission.admitted_diagnosis is None
        and not admission.no_incident_admissible
        and not admission.conflicting_evidence
    ):
        action = _common_candidate_action(
            view=view,
            memory=memory,
            topology_edges=case.topology_edges,
            catalog=catalog,
            executed_action_ids=executed,
        )
        if action is None:
            break
        outcome = backend.execute(action)
        outcomes = (*outcomes, outcome)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=_baseline(case),
            observed_at=case.capture.captured_at,
            top_k=64,
        )
        executed.add(action.action_id)
        common_reads += 1
        admission = build_known_admission_state_v23(
            view=view,
            memory=memory,
            topology_edges=case.topology_edges,
        )
    return _CommonContextV23(
        case=case,
        view=view,
        outcomes=outcomes,
        memory=memory,
        admission=admission,
        catalog=catalog,
        common_action_ids=tuple(sorted(executed)),
        bootstrap_memory_sha256=bootstrap_memory_sha256,
        common_read_count=common_reads,
    )


def _known_projection(
    admission: KnownAdmissionStateV23,
) -> tuple[MechanismV22 | None, str | None]:
    if admission.admitted_diagnosis is None:
        return None, None
    value = admission.admitted_diagnosis
    return value.mechanism, value.root_service


def _build_arm_run(
    *,
    context: _CommonContextV23,
    arm: EvaluationArmV23,
    graph: ResidualEvidenceGraphV23 | None,
    decision: NoveltyGateDecisionV23 | None,
    negative: NegativeCoverageLedgerV23 | None,
    discovery_reads: int,
    report: ProvisionalIncidentReportV23 | None,
    provider_error_code: str | None,
    provider_cost: ProviderCostV23,
    memory: SalientEvidenceMemoryV22,
) -> EvaluationArmRunV23:
    mechanism, root = _known_projection(context.admission)
    if provider_error_code is not None:
        final = "PROVIDER_FAILED"
    elif arm is EvaluationArmV23.CLOSED_WORLD_ONLY:
        if context.admission.conflicting_evidence:
            final = NoveltyDispositionV23.CONFLICTING_EVIDENCE.value
        elif context.admission.admitted_diagnosis is not None:
            final = NoveltyDispositionV23.KNOWN_INCIDENT.value
        elif context.admission.no_incident_admissible:
            final = NoveltyDispositionV23.NO_INCIDENT.value
        else:
            final = NoveltyDispositionV23.INSUFFICIENT_EVIDENCE.value
    else:
        if decision is None:
            raise ValueError("open-world arm lacks a Novelty Gate decision")
        final = decision.disposition.value
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.evaluation-arm-run.v2",
        "case_id": context.case.case_id,
        "arm": arm,
        "case_bytes_sha256": context.case.source_bytes_sha256,
        "active_view_sha256": context.view.view_sha256,
        "bootstrap_memory_sha256": context.bootstrap_memory_sha256,
        "common_memory_sha256": context.memory.memory_sha256,
        "common_read_count": context.common_read_count,
        "discovery_read_count": discovery_reads,
        "final_disposition": final,
        "known_admission_sha256": context.admission.state_sha256,
        "admitted_diagnosis": context.admission.admitted_diagnosis,
        "known_mechanism": mechanism,
        "known_root_service": root,
        "no_incident_admissible": context.admission.no_incident_admissible,
        "residual_graph": graph,
        "novelty_decision": decision,
        "memory_evidence_refs": tuple(
            sorted(item.evidence_ref for item in memory.evidence_refs)
        ),
        "negative_coverage": negative,
        "provisional_report": report,
        "provider_error_code": provider_error_code,
        "provider_cost": provider_cost,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority_violations": 0,
    }
    draft = EvaluationArmRunV23.model_construct(**payload, run_sha256="0" * 64)
    return EvaluationArmRunV23.model_validate(
        {
            **payload,
            "run_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"run_sha256"})
            ),
        }
    )


def run_closed_world_arm_v23(context: _CommonContextV23) -> EvaluationArmRunV23:
    return _build_arm_run(
        context=context,
        arm=EvaluationArmV23.CLOSED_WORLD_ONLY,
        graph=None,
        decision=None,
        negative=None,
        discovery_reads=0,
        report=None,
        provider_error_code=None,
        provider_cost=ProviderCostV23(
            provider_calls=0,
            protocol_repairs=0,
            transport_retries=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0.0,
        ),
        memory=context.memory,
    )


class _ProviderStatsProtocol:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float


def run_open_world_arm_v23(
    context: _CommonContextV23,
    *,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationArmRunV23:
    outcomes = context.outcomes
    memory = context.memory
    negative = NegativeCoverageLedgerV23.empty()
    graph, decision = _case_state(
        case=context.case,
        admission=context.admission,
        memory=memory,
        negative_coverage=negative,
    )
    backend = QuerySpecificReplayBackendV22(context.case.capture)
    discovery_reads = 0
    remaining_budget = 3.0
    while decision.disposition is NoveltyDispositionV23.INSUFFICIENT_EVIDENCE or (
        discovery_reads == 0
        and decision.disposition
        in {
            NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED,
            NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY,
        }
    ):
        plan = build_discovery_plan_v23(
            catalog=context.catalog,
            graph=graph,
            negative_coverage=negative,
            reads_used=discovery_reads,
            remaining_weighted_budget=remaining_budget,
            target_complete_resource_coverage=True,
            excluded_action_ids=context.common_action_ids,
        )
        if plan is None:
            break
        action = resolve_discovery_action_v23(
            option=plan.selected_action,
            catalog=context.catalog,
            target_complete_resource_coverage=True,
        )
        before_ids = {item.anomaly_id for item in graph.generic_anomalies}
        if isinstance(action, ContrastiveResourceActionV225):
            outcome = _build_read_outcome_v23(
                action=action,
                capture=context.case.capture,
            )
        elif isinstance(action, EvidenceActionV22):
            outcome = backend.execute(action)
        else:
            raise TypeError("evaluation discovery action type is unsupported")
        outcomes = (*outcomes, outcome)
        memory, _ = build_memory_views_v22(
            outcomes=outcomes,
            baseline=_baseline(context.case),
            observed_at=context.case.capture.captured_at,
            top_k=64,
        )
        after = extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=context.case.candidate_services,
        )
        outcome_class, new_ids = _classify_discovery_outcome(
            outcome=outcome,
            before_anomaly_ids=before_ids,
            after_anomaly_ids={item.anomaly_id for item in after},
        )
        negative = record_discovery_outcome_v23(
            ledger=negative,
            action=plan.selected_action,
            outcome_class=outcome_class,
            new_anomaly_ids=new_ids,
        )
        discovery_reads += 1
        remaining_budget = max(
            0.0, remaining_budget - plan.selected_action.weighted_cost
        )
        graph, decision = _case_state(
            case=context.case,
            admission=context.admission,
            memory=memory,
            negative_coverage=negative,
        )
    report = None
    provider_error = None
    cost = ProviderCostV23(
        provider_calls=0,
        protocol_repairs=0,
        transport_retries=0,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        latency_ms=0.0,
    )
    if decision.disposition in {
        NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED,
        NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY,
    }:
        if provider_transport is None:
            report = _deterministic_development_report_v23(
                disposition=decision.disposition,
                graph=graph,
                memory=memory,
            )
        else:
            request = build_discovery_provider_request_v23(
                active_ontology=context.view,
                graph=graph,
                negative_coverage=negative,
                last_post_read_delta=None,
                top_shadow_matches=(),
            )
            before_input = int(getattr(provider_transport, "input_tokens", 0))
            before_output = int(getattr(provider_transport, "output_tokens", 0))
            before_total = int(getattr(provider_transport, "total_tokens", 0))
            before_latency = float(getattr(provider_transport, "latency_ms", 0.0))
            try:
                provider_outcome = call_discovery_provider_v23(
                    request=request,
                    memory=memory,
                    transport=provider_transport,
                )
            except DiscoveryProviderProtocolFailureV23:
                provider_error = "PROTOCOL_FAILED"
            except DiscoveryProviderTransportErrorV23 as exc:
                provider_error = f"TRANSPORT_FAILED:{exc.safe_code}"
            else:
                report = provider_outcome.report
                cost = ProviderCostV23(
                    provider_calls=provider_outcome.provider_calls,
                    protocol_repairs=provider_outcome.protocol_repairs,
                    transport_retries=provider_outcome.transport_retries,
                    input_tokens=int(getattr(provider_transport, "input_tokens", 0))
                    - before_input,
                    output_tokens=int(getattr(provider_transport, "output_tokens", 0))
                    - before_output,
                    total_tokens=int(getattr(provider_transport, "total_tokens", 0))
                    - before_total,
                    latency_ms=float(getattr(provider_transport, "latency_ms", 0.0))
                    - before_latency,
                )
    return _build_arm_run(
        context=context,
        arm=EvaluationArmV23.OPEN_WORLD_DISCOVERY,
        graph=graph,
        decision=decision,
        negative=negative,
        discovery_reads=discovery_reads,
        report=report,
        provider_error_code=provider_error,
        provider_cost=cost,
        memory=memory,
    )


class OpenAICompatibleDiscoveryTransportV23:
    """Local OpenAI-compatible forced-function transport with safe accounting."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        minimum_request_interval_seconds: float = 6.0,
        timeout_seconds: float = 120.0,
    ) -> None:
        if minimum_request_interval_seconds < 0:
            raise ValueError("provider request interval cannot be negative")
        self.config = config
        self.minimum_request_interval_seconds = minimum_request_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.transport = StdlibProviderTransportV22()
        self._last_started: float | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.latency_ms = 0.0

    @staticmethod
    def _tool() -> dict[str, object]:
        string_array = {"type": "array", "items": {"type": "string"}}
        properties: dict[str, object] = {
            "terminal": {
                "type": "string",
                "enum": [
                    "UNREGISTERED_INCIDENT_SUSPECTED",
                    "KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY",
                ],
            },
            "suspected_root_services": string_array,
            "affected_services": string_array,
            "broad_fault_domain": {
                "type": "string",
                "enum": [
                    "CONFIGURATION",
                    "RUNTIME",
                    "RESOURCE",
                    "DEPENDENCY",
                    "NETWORK",
                    "CONCURRENCY",
                    "DATA",
                    "EXTERNAL",
                    "UNKNOWN",
                ],
            },
            "provisional_mechanism_label": {"type": "string"},
            "mechanism_description": {"type": "string"},
            "observed_symptoms": string_array,
            "supporting_evidence_refs": string_array,
            "contradicting_evidence_refs": string_array,
            "unexplained_anomaly_ids": string_array,
            "alternative_hypotheses": string_array,
            "recommended_next_observations": string_array,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "action_authority": {"type": "string", "enum": ["NONE"]},
        }
        return {
            "type": "function",
            "function": {
                "name": "submit_provisional_incident_report",
                "description": "Submit one evidence-bound non-actionable report.",
                "strict": False,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": sorted(properties),
                },
            },
        }

    @staticmethod
    def _extract(response: Mapping[str, object]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_PROVIDER_ENVELOPE",
                retryable=False,
            )
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_PROVIDER_ENVELOPE",
                retryable=False,
            )
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_PROVIDER_ENVELOPE",
                retryable=False,
            )
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and len(tool_calls) == 1:
            call = tool_calls[0]
            if isinstance(call, Mapping):
                function = call.get("function")
                if (
                    isinstance(function, Mapping)
                    and function.get("name") == "submit_provisional_incident_report"
                    and isinstance(function.get("arguments"), str)
                ):
                    return cast(str, function["arguments"])
        content = message.get("content")
        if isinstance(content, str):
            return content
        raise DiscoveryProviderTransportErrorV23(
            "INVALID_PROVIDER_RESPONSE",
            retryable=False,
        )

    def __call__(self, body: str) -> str:
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_LOCAL_REQUEST",
                retryable=False,
            )
        now = time.monotonic()
        if self._last_started is not None:
            delay = self.minimum_request_interval_seconds - (now - self._last_started)
            if delay > 0:
                time.sleep(delay)
        self._last_started = time.monotonic()
        started = time.monotonic()
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": str(parsed["system"])},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": parsed["request"],
                            "response_contract": parsed["response_contract"],
                            "protocol_repair": parsed.get("protocol_repair"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "tools": [self._tool()],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_provisional_incident_report"},
            },
            "temperature": 0,
        }
        try:
            response = self.transport.post_json(
                url=f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
        except ProviderTransportErrorV22 as exc:
            raise DiscoveryProviderTransportErrorV23(
                exc.safe_code,
                retryable=exc.retryable,
            ) from exc
        self.latency_ms += (time.monotonic() - started) * 1000.0
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            self.input_tokens += int(usage.get("prompt_tokens", 0))
            self.output_tokens += int(usage.get("completion_tokens", 0))
            self.total_tokens += int(usage.get("total_tokens", 0))
        return self._extract(response)


def run_evaluation_case_pair_v23(
    *,
    repository_root: Path,
    spec: EvaluationCaseSpecV23,
    view_spec: EvaluationOntologyViewSpecV23,
    truth_store: LazyTruthStoreV23,
    provider_transport: Callable[[str], str] | None,
) -> EvaluationCasePairV23:
    if spec.case_id != view_spec.case_id:
        raise ValueError("evaluation case and ontology view IDs differ")
    case = materialize_evaluation_case_v23(repository_root=repository_root, spec=spec)
    common = _build_common_context_v23(
        case=case,
        hidden_mechanism=view_spec.hidden_mechanism,
    )
    closed = run_closed_world_arm_v23(common)
    opened = run_open_world_arm_v23(
        common,
        provider_transport=provider_transport,
    )
    truth = truth_store.load_case_after_both_arms(spec.case_id, arms_completed=2)
    return EvaluationCasePairV23(
        schema_version="dta-v23.evaluation-case-pair.v2",
        case_id=spec.case_id,
        closed_world=closed,
        open_world=opened,
        evaluator_truth=truth,
    )


class EvaluationMetricsV23(DtaModelV22):
    schema_version: Literal["dta-v23.evaluation-metrics.v1"]
    novelty_recall: StrictFloat = Field(ge=0.0, le=1.0)
    false_novel_rate: StrictFloat = Field(ge=0.0, le=1.0)
    insufficient_conflict_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    root_localization: StrictFloat = Field(ge=0.0, le=1.0)
    broad_domain_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_ref_validity: StrictFloat = Field(ge=0.0, le=1.0)
    residual_anomaly_citation_validity: StrictFloat = Field(ge=0.0, le=1.0)
    report_schema_validity: StrictFloat = Field(ge=0.0, le=1.0)
    alternative_hypothesis_completeness: StrictFloat = Field(ge=0.0, le=1.0)
    registered_known_closed_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    registered_known_open_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    no_incident_closed_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    no_incident_open_accuracy: StrictFloat = Field(ge=0.0, le=1.0)
    known_accuracy_drop_cases: StrictInt = Field(ge=0)
    no_incident_accuracy_drop_cases: StrictInt = Field(ge=0)
    action_authority_violations: StrictInt = Field(ge=0)
    mean_discovery_reads: StrictFloat = Field(ge=0.0, le=3.0)
    novelty_cases_with_discovery_reads: StrictInt = Field(ge=0, le=14)
    empty_read_rate: StrictFloat = Field(ge=0.0, le=1.0)
    generic_anomaly_yield_rate: StrictFloat = Field(ge=0.0, le=1.0)
    first_useful_evidence_mean_ordinal: StrictFloat | None
    discovery_source_distribution: dict[str, int]
    negative_coverage_use_count: StrictInt = Field(ge=0)
    semantic_grade_counts: dict[str, int]
    provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    provider_latency_ms: StrictFloat = Field(ge=0.0)


class FixedEvaluationArtifactV23(DtaModelV22):
    schema_version: Literal["dta-v23.fixed-evaluation.v2"]
    execution_count: Literal[1]
    case_count: Literal[24]
    run_count: Literal[48]
    provider_model: str
    preflight: EvaluationPreflightV23
    pairs: tuple[EvaluationCasePairV23, ...] = Field(min_length=24, max_length=24)
    metrics: EvaluationMetricsV23
    measured_result_terminal: MeasuredResultTerminalV23
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    docker_calls: Literal[0]
    new_live_faults: Literal[0]
    artifact_sha256: str

    @model_validator(mode="after")
    def require_artifact(self) -> "FixedEvaluationArtifactV23":
        ids = tuple(item.case_id for item in self.pairs)
        if ids != tuple(sorted(set(ids))) or len(ids) != 24:
            raise ValueError("fixed evaluation pair denominator differs")
        if self.preflight.provider_model != self.provider_model:
            raise ValueError("fixed evaluation provider binding differs")
        expected_terminal = score_measured_terminal_v23(
            novelty_recall=self.metrics.novelty_recall,
            root_localization=self.metrics.root_localization,
            broad_domain_accuracy=self.metrics.broad_domain_accuracy,
            evidence_ref_validity=self.metrics.evidence_ref_validity,
            false_novel_rate=self.metrics.false_novel_rate,
            known_accuracy_drop_cases=self.metrics.known_accuracy_drop_cases,
            no_incident_accuracy_drop_cases=self.metrics.no_incident_accuracy_drop_cases,
            action_authority_violations=self.metrics.action_authority_violations,
        )
        if self.measured_result_terminal is not expected_terminal:
            raise ValueError("measured terminal differs from frozen thresholds")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("fixed evaluation artifact digest differs")
        return self


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _is_novel_report(run: EvaluationArmRunV23) -> bool:
    return run.provisional_report is not None and run.final_disposition in {
        NoveltyDispositionV23.UNREGISTERED_INCIDENT_SUSPECTED.value,
        NoveltyDispositionV23.KNOWN_DIAGNOSIS_WITH_RESIDUAL_NOVELTY.value,
    }


def _known_exact(run: EvaluationArmRunV23, truth: EvaluationTruthV23) -> bool:
    return (
        run.final_disposition == NoveltyDispositionV23.KNOWN_INCIDENT.value
        and run.known_mechanism is truth.expected_mechanism
        and run.known_root_service == truth.expected_root_service
    )


def _semantic_grade(
    report: ProvisionalIncidentReportV23,
    truth: EvaluationTruthV23,
) -> int:
    rendered = (
        f"{report.provisional_mechanism_label} {report.mechanism_description}"
    ).casefold()
    matches = sum(concept.casefold() in rendered for concept in truth.semantic_concepts)
    return 2 if matches >= 2 else 1 if matches == 1 else 0


def score_evaluation_pairs_v23(
    pairs: tuple[EvaluationCasePairV23, ...],
) -> EvaluationMetricsV23:
    if len(pairs) != 24:
        raise ValueError("evaluation scoring requires exactly 24 pairs")
    novelty = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category
        in {
            EvaluationCategoryV23.NOVEL_HIDDEN,
            EvaluationCategoryV23.NOVEL_UNREGISTERED,
        }
    )
    controls = tuple(item for item in pairs if item not in novelty)
    known = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category is EvaluationCategoryV23.REGISTERED_KNOWN
    )
    no_incident = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category is EvaluationCategoryV23.NO_INCIDENT
    )
    insufficient = tuple(
        item
        for item in pairs
        if item.evaluator_truth.category is EvaluationCategoryV23.INSUFFICIENT_CONFLICT
    )
    detected = tuple(item for item in novelty if _is_novel_report(item.open_world))
    reports = tuple(item.open_world.provisional_report for item in detected)
    reports = tuple(item for item in reports if item is not None)
    root_correct = sum(
        item.evaluator_truth.expected_root_service
        in set(item.open_world.provisional_report.suspected_root_services)
        for item in detected
        if item.open_world.provisional_report is not None
    )
    domain_correct = sum(
        item.open_world.provisional_report.broad_fault_domain.value
        == item.evaluator_truth.expected_broad_domain
        for item in detected
        if item.open_world.provisional_report is not None
    )
    cited_count = 0
    valid_cited_count = 0
    residual_valid = 0
    alternative_complete = 0
    grades: Counter[int] = Counter()
    for item in detected:
        report = item.open_world.provisional_report
        assert report is not None
        graph = item.open_world.residual_graph
        assert graph is not None
        cited = (
            *report.supporting_evidence_refs,
            *report.contradicting_evidence_refs,
        )
        cited_count += len(cited)
        valid_cited_count += sum(
            value in set(item.open_world.memory_evidence_refs) for value in cited
        )
        residual_ids = set(graph.residual_anomaly_ids)
        residual_refs = {
            ref
            for anomaly in graph.generic_anomalies
            if anomaly.anomaly_id in residual_ids
            for ref in anomaly.evidence_refs
        }
        residual_valid += int(
            set(report.unexplained_anomaly_ids).issubset(residual_ids)
            and bool(set(report.supporting_evidence_refs).intersection(residual_refs))
        )
        alternative_complete += int(bool(report.alternative_hypotheses))
        grades[_semantic_grade(report, item.evaluator_truth)] += 1
    closed_known_correct = sum(
        _known_exact(item.closed_world, item.evaluator_truth) for item in known
    )
    open_known_correct = sum(
        _known_exact(item.open_world, item.evaluator_truth) for item in known
    )
    closed_no_correct = sum(
        item.closed_world.final_disposition == NoveltyDispositionV23.NO_INCIDENT.value
        for item in no_incident
    )
    open_no_correct = sum(
        item.open_world.final_disposition == NoveltyDispositionV23.NO_INCIDENT.value
        for item in no_incident
    )
    discovery_entries = tuple(
        entry
        for item in pairs
        for ledger in (item.open_world.negative_coverage,)
        if ledger is not None
        for entry in ledger.entries
    )
    useful_ordinals = tuple(
        index
        for item in pairs
        for ledger in (item.open_world.negative_coverage,)
        if ledger is not None
        for index, entry in enumerate(
            ledger.entries,
            start=1,
        )
        if entry.outcome_class is DiscoveryReadOutcomeClassV23.ANOMALY_YIELD
    )
    sources = Counter(entry.source.value for entry in discovery_entries)
    negative_use = sum(
        entry.outcome_class
        in {
            DiscoveryReadOutcomeClassV23.EMPTY_CAPTURED,
            DiscoveryReadOutcomeClassV23.NONEMPTY_NO_NEW_ANOMALY,
            DiscoveryReadOutcomeClassV23.SOURCE_FAILURE,
        }
        for entry in discovery_entries
    )
    costs = tuple(item.open_world.provider_cost for item in pairs)
    authority_violations = sum(
        item.open_world.action_authority_violations
        + item.closed_world.action_authority_violations
        for item in pairs
    )
    return EvaluationMetricsV23(
        schema_version="dta-v23.evaluation-metrics.v1",
        novelty_recall=_ratio(len(detected), len(novelty)),
        false_novel_rate=_ratio(
            sum(_is_novel_report(item.open_world) for item in controls),
            len(controls),
        ),
        insufficient_conflict_accuracy=_ratio(
            sum(
                item.open_world.final_disposition
                in {
                    NoveltyDispositionV23.INSUFFICIENT_EVIDENCE.value,
                    NoveltyDispositionV23.CONFLICTING_EVIDENCE.value,
                }
                for item in insufficient
            ),
            len(insufficient),
        ),
        root_localization=_ratio(root_correct, len(novelty)),
        broad_domain_accuracy=_ratio(domain_correct, len(novelty)),
        evidence_ref_validity=_ratio(valid_cited_count, cited_count),
        residual_anomaly_citation_validity=_ratio(residual_valid, len(reports)),
        report_schema_validity=_ratio(len(reports), len(detected)),
        alternative_hypothesis_completeness=_ratio(
            alternative_complete,
            len(reports),
        ),
        registered_known_closed_accuracy=_ratio(closed_known_correct, len(known)),
        registered_known_open_accuracy=_ratio(open_known_correct, len(known)),
        no_incident_closed_accuracy=_ratio(closed_no_correct, len(no_incident)),
        no_incident_open_accuracy=_ratio(open_no_correct, len(no_incident)),
        known_accuracy_drop_cases=max(0, closed_known_correct - open_known_correct),
        no_incident_accuracy_drop_cases=max(0, closed_no_correct - open_no_correct),
        action_authority_violations=authority_violations,
        mean_discovery_reads=sum(item.open_world.discovery_read_count for item in pairs)
        / len(pairs),
        novelty_cases_with_discovery_reads=sum(
            item.open_world.discovery_read_count > 0 for item in novelty
        ),
        empty_read_rate=_ratio(
            sum(
                entry.outcome_class is DiscoveryReadOutcomeClassV23.EMPTY_CAPTURED
                for entry in discovery_entries
            ),
            len(discovery_entries),
        ),
        generic_anomaly_yield_rate=_ratio(
            sum(
                entry.outcome_class is DiscoveryReadOutcomeClassV23.ANOMALY_YIELD
                for entry in discovery_entries
            ),
            len(discovery_entries),
        ),
        first_useful_evidence_mean_ordinal=(
            None if not useful_ordinals else sum(useful_ordinals) / len(useful_ordinals)
        ),
        discovery_source_distribution=dict(sorted(sources.items())),
        negative_coverage_use_count=negative_use,
        semantic_grade_counts={str(value): grades[value] for value in (0, 1, 2)},
        provider_calls=sum(item.provider_calls for item in costs),
        protocol_repairs=sum(item.protocol_repairs for item in costs),
        transport_retries=sum(item.transport_retries for item in costs),
        input_tokens=sum(item.input_tokens for item in costs),
        output_tokens=sum(item.output_tokens for item in costs),
        total_tokens=sum(item.total_tokens for item in costs),
        provider_latency_ms=sum(item.latency_ms for item in costs),
    )


def _build_fixed_artifact(
    *,
    preflight: EvaluationPreflightV23,
    pairs: tuple[EvaluationCasePairV23, ...],
) -> FixedEvaluationArtifactV23:
    metrics = score_evaluation_pairs_v23(pairs)
    terminal = score_measured_terminal_v23(
        novelty_recall=metrics.novelty_recall,
        root_localization=metrics.root_localization,
        broad_domain_accuracy=metrics.broad_domain_accuracy,
        evidence_ref_validity=metrics.evidence_ref_validity,
        false_novel_rate=metrics.false_novel_rate,
        known_accuracy_drop_cases=metrics.known_accuracy_drop_cases,
        no_incident_accuracy_drop_cases=metrics.no_incident_accuracy_drop_cases,
        action_authority_violations=metrics.action_authority_violations,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.fixed-evaluation.v2",
        "execution_count": 1,
        "case_count": 24,
        "run_count": 48,
        "provider_model": preflight.provider_model,
        "preflight": preflight,
        "pairs": pairs,
        "metrics": metrics,
        "measured_result_terminal": terminal,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "new_live_faults": 0,
    }
    draft = FixedEvaluationArtifactV23.model_construct(
        **payload,
        artifact_sha256="0" * 64,
    )
    return FixedEvaluationArtifactV23.model_validate(
        {
            **payload,
            "artifact_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"artifact_sha256"})
            ),
        }
    )


def run_fixed_evaluation_once_v23(
    *,
    repository_root: Path,
    cases_path: Path,
    truth_path: Path,
    ontology_views_path: Path,
    manifest_path: Path,
    output_path: Path,
    provider_transport: OpenAICompatibleDiscoveryTransportV23,
    observer: Callable[[EvaluationCasePairV23], None] | None = None,
) -> FixedEvaluationArtifactV23:
    preflight = build_evaluation_preflight_v23(
        repository_root=repository_root,
        cases_path=cases_path,
        truth_path=truth_path,
        ontology_views_path=ontology_views_path,
        output_path=output_path,
        expected_provider_model=provider_transport.config.model,
        manifest_path=manifest_path,
    )
    local_root = repository_root / ".local/dta-v23"
    local_root.mkdir(parents=True, exist_ok=True)
    sentinel = local_root / "fixed-evaluation.started.json"
    partial = local_root / "fixed-evaluation.partial.jsonl"
    if sentinel.exists() or partial.exists():
        raise FileExistsError("fixed evaluation write-once sentinel already exists")
    sentinel.write_text(
        json.dumps(
            {
                "status": "STARTED",
                "planned_execution_count": 1,
                "cases_sha256": preflight.cases_sha256,
                "truth_sha256": preflight.truth_sha256,
                "ontology_views_sha256": preflight.ontology_views_sha256,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    cases = load_evaluation_case_set_v23(cases_path)
    views = load_evaluation_ontology_views_v23(ontology_views_path)
    truth_store = LazyTruthStoreV23(truth_path)
    pairs: list[EvaluationCasePairV23] = []
    with partial.open("x", encoding="utf-8") as handle:
        for spec in cases.cases:
            pair = run_evaluation_case_pair_v23(
                repository_root=repository_root,
                spec=spec,
                view_spec=views.require(spec.case_id),
                truth_store=truth_store,
                provider_transport=provider_transport,
            )
            pairs.append(pair)
            handle.write(pair.model_dump_json() + "\n")
            handle.flush()
            if observer is not None:
                observer(pair)
    artifact = _build_fixed_artifact(preflight=preflight, pairs=tuple(pairs))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(artifact.model_dump_json(indent=2) + "\n")
    sentinel.write_text(
        json.dumps(
            {
                "status": "COMPLETE",
                "execution_count": 1,
                "artifact_sha256": artifact.artifact_sha256,
                "measured_result_terminal": artifact.measured_result_terminal.value,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return artifact


def render_evaluation_markdown_v23(artifact: FixedEvaluationArtifactV23) -> str:
    metrics = artifact.metrics
    return "\n".join(
        (
            "# DTA v2.3 Open-World Discovery — Fixed Evaluation",
            "",
            f"Measured terminal: `{artifact.measured_result_terminal.value}`",
            "",
            f"- Execution count: `{artifact.execution_count}`",
            f"- Cases / runs: `{artifact.case_count}` / `{artifact.run_count}`",
            f"- Novelty recall: `{metrics.novelty_recall:.3f}`",
            f"- Root localization: `{metrics.root_localization:.3f}`",
            f"- Broad-domain accuracy: `{metrics.broad_domain_accuracy:.3f}`",
            f"- Evidence-ref validity: `{metrics.evidence_ref_validity:.3f}`",
            f"- False-novel rate: `{metrics.false_novel_rate:.3f}`",
            f"- Registered-known closed/open accuracy: `{metrics.registered_known_closed_accuracy:.3f}` / `{metrics.registered_known_open_accuracy:.3f}`",
            f"- No-Incident closed/open accuracy: `{metrics.no_incident_closed_accuracy:.3f}` / `{metrics.no_incident_open_accuracy:.3f}`",
            f"- Mean discovery reads: `{metrics.mean_discovery_reads:.3f}`",
            f"- Provider calls / repairs / retries: `{metrics.provider_calls}` / `{metrics.protocol_repairs}` / `{metrics.transport_retries}`",
            f"- Action-authority violations: `{metrics.action_authority_violations}`",
            "",
            "The study used committed replay/derived evidence only. It did not call Docker, create a live fault, execute a Runbook, or grant Agent write authority.",
            "",
        )
    )


__all__ = (
    "EvaluationCaseSetV23",
    "EvaluationCasePairV23",
    "EvaluationCaseSpecV23",
    "EvaluationCategoryV23",
    "EvaluationOntologyViewSetV23",
    "EvaluationPreflightV23",
    "EvaluationMetricsV23",
    "EvaluationSourceSplitV23",
    "EvaluationTruthSetV23",
    "EvaluationTruthV23",
    "EvaluationArmRunV23",
    "EvaluationArmV23",
    "LazyTruthStoreV23",
    "MeasuredResultTerminalV23",
    "FixedEvaluationArtifactV23",
    "OpenAICompatibleDiscoveryTransportV23",
    "ProviderCostV23",
    "build_evaluation_preflight_v23",
    "load_evaluation_case_set_v23",
    "load_evaluation_ontology_views_v23",
    "load_evaluation_truth_set_v23",
    "materialize_evaluation_case_v23",
    "render_evaluation_markdown_v23",
    "run_fixed_evaluation_once_v23",
    "run_evaluation_case_pair_v23",
    "score_evaluation_pairs_v23",
    "score_measured_terminal_v23",
    "verify_unregistered_case_has_no_known_terminal_v23",
)
