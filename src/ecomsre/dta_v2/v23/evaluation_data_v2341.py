"""Fresh fixed evaluation data and admission for DTA v2.3.4.1."""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
import tempfile
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.evaluation_data_v233 import load_evaluation_cases_v233
from ecomsre.dta_v2.v23.evaluation_v234 import (
    ProviderCoreViewBindingV234,
    RegistrationTaskClassV234,
    RegistrationTaskV234,
    RegistrationTruthV234,
    _case_context,
    _hashed,
    _prepare_authorized_task_v234,
)
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    build_registration_alias_provider_request_v2341,
    build_registration_alias_source_request_v2341,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    CatalogFeasibilityStatusV2341,
    build_registration_option_catalog_v2341,
    evaluate_catalog_feasibility_v2341,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    RegistrationImplementationModeV234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    ProviderCoreOntologyViewV234,
    build_provider_core_ontology_view_v234,
)


class EvaluationRoleV2341(str, Enum):
    HIDDEN_KNOWN_RECONSTRUCTION = "HIDDEN_KNOWN_RECONSTRUCTION"
    GENUINELY_UNREGISTERED = "GENUINELY_UNREGISTERED"
    DUPLICATE_CONTROL = "DUPLICATE_CONTROL"
    INSUFFICIENT_CONTROL = "INSUFFICIENT_CONTROL"


class EvaluationTaskSetV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-evaluation-task-set.v1"]
    freeze_id: Literal["dta-v2341-registration-assistance-freeze-20260827-a"]
    tasks: tuple[RegistrationTaskV234, ...] = Field(min_length=16, max_length=16)
    predecessor_task_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    smoke_task_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationTaskSetV2341":
        if tuple(item.task_id for item in self.tasks) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(101, 117)
        ):
            raise ValueError("v2.3.4.1 final task IDs differ")
        counts = {
            kind: sum(item.task_class is kind for item in self.tasks)
            for kind in RegistrationTaskClassV234
        }
        if counts != {
            RegistrationTaskClassV234.HIDDEN_KNOWN: 10,
            RegistrationTaskClassV234.UNREGISTERED: 4,
            RegistrationTaskClassV234.DUPLICATE_CONTROL: 1,
            RegistrationTaskClassV234.INSUFFICIENT_CONTROL: 1,
        }:
            raise ValueError("v2.3.4.1 final task composition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"task_set_sha256"})
        )
        if self.task_set_sha256 != expected:
            raise ValueError("v2.3.4.1 final task-set digest differs")
        return self

    def require(self, task_id: str) -> RegistrationTaskV234:
        task = next((item for item in self.tasks if item.task_id == task_id), None)
        if task is None:
            raise ValueError("v2.3.4.1 final task is absent")
        return task


class EvaluationTruthSetV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-evaluation-truth-set.v1"]
    truths: tuple[RegistrationTruthV234, ...] = Field(min_length=16, max_length=16)
    truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationTruthSetV2341":
        if tuple(item.task_id for item in self.truths) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(101, 117)
        ):
            raise ValueError("v2.3.4.1 final truth IDs differ")
        if sum(item.target_mechanism is not None for item in self.truths) != 10:
            raise ValueError("v2.3.4.1 hidden-known truth composition differs")
        if sum(item.declarative_compilation_expected for item in self.truths) != 3:
            raise ValueError("v2.3.4.1 final declarative composition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"truth_sha256"})
        )
        if self.truth_sha256 != expected:
            raise ValueError("v2.3.4.1 final truth digest differs")
        return self

    def require(self, task_id: str) -> RegistrationTruthV234:
        truth = next((item for item in self.truths if item.task_id == task_id), None)
        if truth is None:
            raise ValueError("v2.3.4.1 final truth is absent")
        return truth


class EvaluationCoreViewSetV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.provider-core-view-set.v1"]
    authoritative_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    views: tuple[ProviderCoreViewBindingV234, ...] = Field(
        min_length=16, max_length=16
    )
    view_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> "EvaluationCoreViewSetV2341":
        if tuple(item.task_id for item in self.views) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(101, 117)
        ):
            raise ValueError("v2.3.4.1 final Provider-view IDs differ")
        if any(
            item.materialize().authoritative_snapshot_sha256
            != self.authoritative_snapshot_sha256
            for item in self.views
        ):
            raise ValueError("v2.3.4.1 final Provider views differ from snapshot")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"view_set_sha256"})
        )
        if self.view_set_sha256 != expected:
            raise ValueError("v2.3.4.1 final Provider-view digest differs")
        return self

    def require(self, task_id: str) -> ProviderCoreOntologyViewV234:
        binding = next((item for item in self.views if item.task_id == task_id), None)
        if binding is None:
            raise ValueError("v2.3.4.1 final Provider view is absent")
        return binding.materialize()


class EvaluationAdmissionTaskV2341(DtaModelV22):
    task_id: str
    role: EvaluationRoleV2341
    provider_call_expected: StrictBool
    target_hidden: StrictBool
    catalog_feasibility_pass: StrictBool
    hidden_identifier_leaks: Literal[0]
    provider_calls: Literal[0]
    status: Literal["PASS"]


class EvaluationAdmissionV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-evaluation-admission.v1"]
    task_count: Literal[16]
    hidden_known_task_count: Literal[10]
    genuinely_unregistered_task_count: Literal[4]
    control_task_count: Literal[2]
    provider_called_task_count: Literal[14]
    catalog_feasibility_pass_count: Literal[14]
    hidden_view_pass_count: Literal[10]
    hidden_identifier_leaks: Literal[0]
    unregistered_core_clause_match_count: Literal[0]
    duplicate_control_core_match_count: Literal[1]
    insufficient_control_evidence_source_count: StrictInt = Field(ge=0, le=1)
    task_digest_overlap_count: Literal[0]
    truth_evaluator_only: Literal[True]
    premature_truth_reads: Literal[0]
    provider_calls: Literal[0]
    action_authority_violations: Literal[0]
    tasks: tuple[EvaluationAdmissionTaskV2341, ...] = Field(
        min_length=16, max_length=16
    )
    terminal: Literal["DTA_V2341_EVALUATION_DATA_PASS"]
    admission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_admission(self) -> "EvaluationAdmissionV2341":
        if tuple(item.task_id for item in self.tasks) != tuple(
            f"rt-{ordinal:03d}" for ordinal in range(101, 117)
        ):
            raise ValueError("v2.3.4.1 final admission task IDs differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("v2.3.4.1 final admission digest differs")
        return self


_TASK_SPECS_V2341: tuple[dict[str, Any], ...] = (
    {"source": "vx-301", "class": "HIDDEN_KNOWN", "domain": "CONFIGURATION", "root": "svc-50b4ddc9a9", "label": "opaque-state-cluster-alpha", "description": "Recent opaque state movement coincides with strong target error evidence.", "symptoms": ("changes show recent opaque state movement", "metrics show strong target error evidence"), "kinds": ("METRIC_ERROR_OUTLIER", "RECENT_CHANGE_CORRELATION")},
    {"source": "vx-302", "class": "HIDDEN_KNOWN", "domain": "CONFIGURATION", "root": "svc-77d4c9fe68", "label": "opaque-state-cluster-beta", "description": "A second opaque state movement coincides with strong target error evidence.", "symptoms": ("changes show a second opaque state movement", "metrics show strong target error evidence"), "kinds": ("METRIC_ERROR_OUTLIER", "RECENT_CHANGE_CORRELATION")},
    {"source": "vx-305", "class": "HIDDEN_KNOWN", "domain": "RUNTIME", "root": "svc-8452f9b829", "label": "opaque-runtime-cluster-alpha", "description": "The target runtime is absent while strong target error evidence remains.", "symptoms": ("metrics show strong target error evidence", "runtime shows the target is absent"), "kinds": ("METRIC_ERROR_OUTLIER", "RUNTIME_NOT_RUNNING")},
    {"source": "vx-306", "class": "HIDDEN_KNOWN", "domain": "RUNTIME", "root": "svc-75a0bd2dea", "label": "opaque-runtime-cluster-beta", "description": "A second target runtime is absent while strong target error evidence remains.", "symptoms": ("metrics show strong target error evidence", "runtime shows a second target is absent"), "kinds": ("METRIC_ERROR_OUTLIER", "RUNTIME_NOT_RUNNING")},
    {"source": "vx-307", "class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-c64512f0e1", "label": "opaque-resource-cluster-alpha", "description": "Sustained target compute utilization coincides with strong error evidence.", "symptoms": ("metrics show strong target error evidence", "resources show sustained target compute utilization"), "kinds": ("METRIC_ERROR_OUTLIER", "RESOURCE_CPU_OUTLIER")},
    {"source": "vx-308", "class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-aee314eeda", "label": "opaque-resource-cluster-beta", "description": "A second sustained target utilization signal coincides with strong error evidence.", "symptoms": ("metrics show strong target error evidence", "resources show a second sustained utilization signal"), "kinds": ("METRIC_ERROR_OUTLIER", "RESOURCE_CPU_OUTLIER")},
    {"source": "vx-309", "class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-0d7b707a36", "label": "opaque-growth-cluster-alpha", "description": "Monotonic target allocation growth coincides with an error log cluster.", "symptoms": ("logs show a target allocation pressure cluster", "resources show monotonic target allocation growth"), "kinds": ("LOG_ERROR_CLUSTER", "RESOURCE_MEMORY_TREND")},
    {"source": "vx-310", "class": "HIDDEN_KNOWN", "domain": "RESOURCE", "root": "svc-e51b9e1bcc", "label": "opaque-growth-cluster-beta", "description": "A second monotonic allocation signal coincides with an error log cluster.", "symptoms": ("logs show a second allocation pressure cluster", "resources show monotonic target allocation growth"), "kinds": ("LOG_ERROR_CLUSTER", "RESOURCE_MEMORY_TREND")},
    {"source": "vx-303", "class": "HIDDEN_KNOWN", "domain": "DEPENDENCY", "root": "svc-64b3d3907c", "label": "opaque-path-cluster-alpha", "description": "A slow downstream path coincides with strong target latency evidence.", "symptoms": ("metrics show strong target latency evidence", "traces show a slow downstream path"), "kinds": ("METRIC_LATENCY_OUTLIER", "TRACE_LATENCY_OUTLIER")},
    {"source": "vx-304", "class": "HIDDEN_KNOWN", "domain": "DEPENDENCY", "root": "svc-c7902c06b9", "label": "opaque-path-cluster-beta", "description": "A second slow downstream path coincides with strong target latency evidence.", "symptoms": ("metrics show strong target latency evidence", "traces show a second slow downstream path"), "kinds": ("METRIC_LATENCY_OUTLIER", "TRACE_LATENCY_OUTLIER")},
    {"source": "vx-311", "class": "UNREGISTERED", "domain": "CONCURRENCY", "root": "svc-28037ae9fb", "label": "bounded-connection-capacity-pressure", "description": "Concurrent work waits for bounded connection capacity while target errors rise.", "symptoms": ("logs show bounded connection wait under load", "metrics show strong target error evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source": "vx-313", "class": "UNREGISTERED", "domain": "CONCURRENCY", "root": "svc-1dc3d6375c", "label": "bounded-backlog-capacity-pressure", "description": "Backlog pressure delays bounded workers while target errors rise.", "symptoms": ("logs show backlog worker wait", "metrics show strong target error evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source": "vx-316", "class": "UNREGISTERED", "domain": "EXTERNAL", "root": "svc-9470751930", "label": "bounded-external-throttle-pressure", "description": "External throttle responses coincide with strong target error evidence.", "symptoms": ("logs show external throttle responses", "metrics show strong target error evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source": "vx-315", "class": "UNREGISTERED", "domain": "NETWORK", "root": "svc-4c39ea767b", "label": "bounded-transport-ordering-pressure", "description": "Ordering-sensitive transport resets require correlation beyond bounded declarative rules.", "symptoms": ("logs show transport reset ordering", "metrics show strong target error evidence"), "kinds": ("LOG_UNKNOWN_ERROR_PATTERN", "METRIC_ERROR_OUTLIER")},
    {"source": "vx-317", "class": "DUPLICATE_CONTROL", "domain": "CONFIGURATION", "root": "svc-9e87901b82", "label": "visible-active-pattern", "description": "Visible evidence already satisfies an active Runtime registration.", "symptoms": ("changes and metrics match a visible active clause",), "kinds": ()},
    {"source": "vx-328", "class": "INSUFFICIENT_CONTROL", "domain": "UNKNOWN", "root": "svc-01a294e042", "label": "weak-observation-pattern", "description": "The available observation does not establish a formal incident mechanism.", "symptoms": ("runtime remains healthy without corroborating evidence",), "kinds": ()},
)


_TRUTH_SPECS_V2341: tuple[dict[str, Any], ...] = (
    {"target": "CONFIGURATION_ERROR", "slug": "configuration-error", "domain": "CONFIGURATION", "mode": "DECLARATIVE_READY", "clauses": ("configuration:change-and-error-metric", "configuration:change-and-log", "configuration:error-metric-and-first-error-trace"), "compile": False},
    {"target": "CONFIGURATION_ERROR", "slug": "configuration-error", "domain": "CONFIGURATION", "mode": "DECLARATIVE_READY", "clauses": ("configuration:change-and-error-metric", "configuration:change-and-log", "configuration:error-metric-and-first-error-trace"), "compile": False},
    {"target": "SERVICE_UNAVAILABLE", "slug": "service-unavailable", "domain": "RUNTIME", "mode": "DECLARATIVE_READY", "clauses": ("service-unavailable:not-running", "service-unavailable:unhealthy-error-metric", "service-unavailable:unhealthy-first-error"), "compile": False},
    {"target": "SERVICE_UNAVAILABLE", "slug": "service-unavailable", "domain": "RUNTIME", "mode": "DECLARATIVE_READY", "clauses": ("service-unavailable:not-running", "service-unavailable:unhealthy-error-metric", "service-unavailable:unhealthy-first-error"), "compile": False},
    {"target": "CPU_SATURATION", "slug": "cpu-saturation", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("cpu-saturation:resource-and-healthy",), "compile": False},
    {"target": "CPU_SATURATION", "slug": "cpu-saturation", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("cpu-saturation:resource-and-healthy",), "compile": False},
    {"target": "MEMORY_LEAK", "slug": "memory-leak", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("memory-leak:growth-and-healthy", "memory-leak:growth-and-log", "memory-leak:growth-and-restarts"), "compile": False},
    {"target": "MEMORY_LEAK", "slug": "memory-leak", "domain": "RESOURCE", "mode": "DECLARATIVE_READY", "clauses": ("memory-leak:growth-and-healthy", "memory-leak:growth-and-log", "memory-leak:growth-and-restarts"), "compile": False},
    {"target": "DEPENDENCY_LATENCY", "slug": "dependency-latency", "domain": "DEPENDENCY", "mode": "DECLARATIVE_READY", "clauses": ("dependency-latency:trace-and-metric",), "compile": False},
    {"target": "DEPENDENCY_LATENCY", "slug": "dependency-latency", "domain": "DEPENDENCY", "mode": "DECLARATIVE_READY", "clauses": ("dependency-latency:trace-and-metric",), "compile": False},
    {"target": None, "slug": "connection-pool-exhaustion", "domain": "CONCURRENCY", "mode": "DECLARATIVE_READY", "clauses": (), "compile": True},
    {"target": None, "slug": "queue-backlog-saturation", "domain": "CONCURRENCY", "mode": "DECLARATIVE_READY", "clauses": (), "compile": True},
    {"target": None, "slug": "external-quota-throttling", "domain": "EXTERNAL", "mode": "DECLARATIVE_READY", "clauses": (), "compile": True},
    {"target": None, "slug": "network-transport-degradation", "domain": "NETWORK", "mode": "ENGINEERING_REQUIRED", "clauses": (), "compile": False},
    {"target": None, "slug": "configuration-error", "domain": "CONFIGURATION", "mode": "DUPLICATE_EXISTING", "clauses": (), "compile": False},
    {"target": None, "slug": None, "domain": None, "mode": "INSUFFICIENT_EVIDENCE", "clauses": (), "compile": False},
)


def build_evaluation_data_v2341(
    *, repository_root: Path
) -> tuple[
    EvaluationTaskSetV2341,
    EvaluationTruthSetV2341,
    EvaluationCoreViewSetV2341,
]:
    cases = load_evaluation_cases_v233(
        repository_root / "config/dta-v233/evaluation/cases.json"
    )
    snapshot = build_core_ontology_schema_snapshot_v234()
    predecessor = json.loads(
        (repository_root / "config/dta-v234/evaluation/tasks.json").read_text(
            encoding="utf-8"
        )
    )
    smoke = json.loads(
        (repository_root / "config/dta-v2341/smoke/tasks.json").read_text(
            encoding="utf-8"
        )
    )
    tasks: list[RegistrationTaskV234] = []
    truths: list[RegistrationTruthV234] = []
    views: list[ProviderCoreViewBindingV234] = []
    for ordinal, (task_spec, truth_spec) in enumerate(
        zip(_TASK_SPECS_V2341, _TRUTH_SPECS_V2341, strict=True), start=101
    ):
        task_id = f"rt-{ordinal:03d}"
        target = (
            MechanismV22(truth_spec["target"])
            if truth_spec["target"] is not None
            else None
        )
        view = build_provider_core_ontology_view_v234(
            snapshot=snapshot,
            hidden_mechanism=target,
        )
        views.append(
            _hashed(
                ProviderCoreViewBindingV234,
                {"task_id": task_id, "provider_view": view.model_dump(mode="json")},
                "binding_sha256",
            )
        )
        source = cases.require(cast(str, task_spec["source"]))
        task_class = RegistrationTaskClassV234(task_spec["class"])
        payload: dict[str, Any] = {
            "task_id": task_id,
            "source_case_id": source.case_id,
            "source_case_sha256": source.source_bytes_sha256,
            "task_class": task_class,
            "provider_view_sha256": view.view_sha256,
            "selected_root_service": task_spec["root"],
            "broad_fault_domain": ProvisionalFaultDomainV23(task_spec["domain"]),
            "provisional_mechanism_label": task_spec["label"],
            "mechanism_description": task_spec["description"],
            "observed_symptoms": tuple(sorted(task_spec["symptoms"])),
            "selected_anomaly_kinds": tuple(
                sorted(
                    (GenericAnomalyKindV23(item) for item in task_spec["kinds"]),
                    key=lambda item: item.value,
                )
            ),
            "provider_call_expected": task_class
            in {
                RegistrationTaskClassV234.HIDDEN_KNOWN,
                RegistrationTaskClassV234.UNREGISTERED,
            },
        }
        tasks.append(_hashed(RegistrationTaskV234, payload, "task_sha256"))
        truths.append(
            RegistrationTruthV234(
                task_id=task_id,
                target_mechanism=target,
                target_mechanism_slug=truth_spec["slug"],
                expected_broad_fault_domain=(
                    ProvisionalFaultDomainV23(truth_spec["domain"])
                    if truth_spec["domain"] is not None
                    else None
                ),
                expected_implementation_mode=RegistrationImplementationModeV234(
                    truth_spec["mode"]
                ),
                expected_core_clause_ids=tuple(sorted(truth_spec["clauses"])),
                declarative_compilation_expected=truth_spec["compile"],
            )
        )
    task_set = cast(
        EvaluationTaskSetV2341,
        _hashed(
            EvaluationTaskSetV2341,
            {
                "schema_version": "dta-v2341.registration-evaluation-task-set.v1",
                "freeze_id": "dta-v2341-registration-assistance-freeze-20260827-a",
                "tasks": tuple(tasks),
                "predecessor_task_set_sha256": predecessor["task_set_sha256"],
                "smoke_task_set_sha256": smoke["task_set_sha256"],
            },
            "task_set_sha256",
        ),
    )
    truth_set = cast(
        EvaluationTruthSetV2341,
        _hashed(
            EvaluationTruthSetV2341,
            {
                "schema_version": "dta-v2341.registration-evaluation-truth-set.v1",
                "truths": tuple(truths),
            },
            "truth_sha256",
        ),
    )
    view_set = cast(
        EvaluationCoreViewSetV2341,
        _hashed(
            EvaluationCoreViewSetV2341,
            {
                "schema_version": "dta-v2341.provider-core-view-set.v1",
                "authoritative_snapshot_sha256": snapshot.snapshot_sha256,
                "views": tuple(views),
            },
            "view_set_sha256",
        ),
    )
    return task_set, truth_set, view_set


def load_evaluation_tasks_v2341(path: Path) -> EvaluationTaskSetV2341:
    return EvaluationTaskSetV2341.model_validate_json(path.read_bytes())


def load_evaluation_truth_v2341(path: Path) -> EvaluationTruthSetV2341:
    return EvaluationTruthSetV2341.model_validate_json(path.read_bytes())


def load_evaluation_views_v2341(path: Path) -> EvaluationCoreViewSetV2341:
    return EvaluationCoreViewSetV2341.model_validate_json(path.read_bytes())


def _role_v2341(task: RegistrationTaskV234) -> EvaluationRoleV2341:
    return {
        RegistrationTaskClassV234.HIDDEN_KNOWN: (
            EvaluationRoleV2341.HIDDEN_KNOWN_RECONSTRUCTION
        ),
        RegistrationTaskClassV234.UNREGISTERED: (
            EvaluationRoleV2341.GENUINELY_UNREGISTERED
        ),
        RegistrationTaskClassV234.DUPLICATE_CONTROL: (
            EvaluationRoleV2341.DUPLICATE_CONTROL
        ),
        RegistrationTaskClassV234.INSUFFICIENT_CONTROL: (
            EvaluationRoleV2341.INSUFFICIENT_CONTROL
        ),
    }[task.task_class]


def _truth_labels_v2341(truth: RegistrationTruthV234) -> set[str]:
    if truth.target_mechanism is None:
        return set()
    value = truth.target_mechanism.value.casefold()
    return {value, value.replace("_", "-"), value.replace("_", " ")}


def run_evaluation_data_admission_v2341(
    *, repository_root: Path, evaluation_root: Path
) -> EvaluationAdmissionV2341:
    tasks = load_evaluation_tasks_v2341(evaluation_root / "tasks.json")
    truths = load_evaluation_truth_v2341(evaluation_root / "truth.json")
    views = load_evaluation_views_v2341(
        evaluation_root / "core-schema-snapshot.json"
    )
    predecessor = json.loads(
        (repository_root / "config/dta-v234/evaluation/tasks.json").read_text(
            encoding="utf-8"
        )
    )
    smoke = json.loads(
        (repository_root / "config/dta-v2341/smoke/tasks.json").read_text(
            encoding="utf-8"
        )
    )
    prior_digests = {
        item["task_sha256"]
        for item in (*predecessor["tasks"], *smoke["tasks"])
    }
    overlap_count = sum(item.task_sha256 in prior_digests for item in tasks.tasks)
    hidden_pass = 0
    hidden_leaks = 0
    catalog_pass = 0
    unregistered_matches = 0
    duplicate_matches = 0
    insufficient_sources = 0
    results: list[EvaluationAdmissionTaskV2341] = []
    with tempfile.TemporaryDirectory(prefix="dta-v2341-evaluation-admission-") as raw:
        local_root = Path(raw)
        for task in tasks.tasks:
            truth = truths.require(task.task_id)
            view = views.require(task.task_id)
            if task.provider_view_sha256 != view.view_sha256:
                raise ValueError("v2.3.4.1 final task view binding differs")
            labels = _truth_labels_v2341(truth)
            target_hidden = False
            if task.task_class is RegistrationTaskClassV234.HIDDEN_KNOWN:
                assert truth.target_mechanism is not None
                visible = json.dumps(
                    {
                        "task": task.model_dump(mode="json"),
                        "view": view.model_dump(mode="json"),
                    },
                    sort_keys=True,
                ).casefold()
                target_hidden = (
                    truth.target_mechanism not in view.runtime_known_mechanisms
                    and not any(label in visible for label in labels)
                )
                hidden_pass += int(target_hidden)
                hidden_leaks += int(not target_hidden)
            context = _case_context(
                repository_root=repository_root,
                task=task,
                hide=None,
            )
            if task.task_class is RegistrationTaskClassV234.UNREGISTERED:
                unregistered_matches += int(
                    context.admission.admitted_diagnosis is not None
                )
            elif task.task_class is RegistrationTaskClassV234.DUPLICATE_CONTROL:
                duplicate_matches += int(
                    context.admission.admitted_diagnosis is not None
                )
            elif task.task_class is RegistrationTaskClassV234.INSUFFICIENT_CONTROL:
                insufficient_sources = len(
                    {
                        item.source
                        for item in extract_generic_anomalies_v23(
                            memory=context.memory,
                            candidate_services=context.case.candidate_services,
                        )
                    }
                )
                if context.admission.admitted_diagnosis is not None:
                    raise ValueError("insufficient final control matched a core clause")
            feasible = not task.provider_call_expected
            if task.provider_call_expected:
                item, shadow, authorization = _prepare_authorized_task_v234(
                    repository_root=repository_root,
                    task=task,
                    local_root=local_root / task.task_id,
                )
                source_request = build_registration_alias_source_request_v2341(
                    authorization_context=authorization,
                    shadow=shadow,
                    accepted_reports=(item,),
                    ontology_view=view,
                )
                catalog = build_registration_option_catalog_v2341(
                    request=source_request
                )
                feasibility = evaluate_catalog_feasibility_v2341(
                    catalog=catalog,
                    expected_disposition=truth.expected_implementation_mode,
                )
                feasible = (
                    feasibility.status is CatalogFeasibilityStatusV2341.PASS
                )
                if not feasible:
                    raise ValueError("BLOCKED_DTA_V2341_EVALUATION_DATA")
                provider_request = build_registration_alias_provider_request_v2341(
                    source_request=source_request,
                    catalog=catalog,
                )
                provider_visible = json.dumps(
                    provider_request.provider_payload(), sort_keys=True
                ).casefold()
                if any(label in provider_visible for label in labels):
                    raise ValueError("hidden final truth leaked into Provider content")
                catalog_pass += 1
            results.append(
                EvaluationAdmissionTaskV2341(
                    task_id=task.task_id,
                    role=_role_v2341(task),
                    provider_call_expected=task.provider_call_expected,
                    target_hidden=target_hidden,
                    catalog_feasibility_pass=feasible,
                    hidden_identifier_leaks=0,
                    provider_calls=0,
                    status="PASS",
                )
            )
    if (
        hidden_pass != 10
        or hidden_leaks != 0
        or catalog_pass != 14
        or unregistered_matches != 0
        or duplicate_matches != 1
        or insufficient_sources > 1
        or overlap_count != 0
    ):
        raise ValueError("BLOCKED_DTA_V2341_EVALUATION_DATA")
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.registration-evaluation-admission.v1",
        "task_count": 16,
        "hidden_known_task_count": 10,
        "genuinely_unregistered_task_count": 4,
        "control_task_count": 2,
        "provider_called_task_count": 14,
        "catalog_feasibility_pass_count": 14,
        "hidden_view_pass_count": 10,
        "hidden_identifier_leaks": 0,
        "unregistered_core_clause_match_count": 0,
        "duplicate_control_core_match_count": 1,
        "insufficient_control_evidence_source_count": insufficient_sources,
        "task_digest_overlap_count": 0,
        "truth_evaluator_only": True,
        "premature_truth_reads": 0,
        "provider_calls": 0,
        "action_authority_violations": 0,
        "tasks": tuple(results),
        "terminal": "DTA_V2341_EVALUATION_DATA_PASS",
    }
    return cast(
        EvaluationAdmissionV2341,
        _hashed(EvaluationAdmissionV2341, payload, "admission_sha256"),
    )


__all__ = (
    "EvaluationAdmissionV2341",
    "EvaluationCoreViewSetV2341",
    "EvaluationRoleV2341",
    "EvaluationTaskSetV2341",
    "EvaluationTruthSetV2341",
    "build_evaluation_data_v2341",
    "load_evaluation_tasks_v2341",
    "load_evaluation_truth_v2341",
    "load_evaluation_views_v2341",
    "run_evaluation_data_admission_v2341",
)
