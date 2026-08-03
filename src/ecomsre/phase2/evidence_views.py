"""Store-resolved, least-privilege Evidence views for Phase 2 requests."""

from __future__ import annotations

import re
from enum import Enum
from threading import RLock

from pydantic import TypeAdapter, ValidationError

from ecomsre.phase1.contracts import (
    MAX_EVIDENCE_REFS,
    Incident,
)
from ecomsre.phase1.evidence import (
    EvidenceStore,
    EvidenceStoreError,
    EvidenceStoreErrorCode,
)
from ecomsre.phase1.validator import revalidate_phase1_model
from ecomsre.phase2.contracts import (
    MAX_PLAN_NODES,
    MAX_REFINEMENT_NODES,
    AdmittedInvestigationGraph,
    BudgetSnapshot,
    Identifier,
    JudgeRequest,
    ModelAllowedActions,
    ResolvedEvidenceView,
    SPECIALIST_TOOL_BINDINGS,
    SpecialistFinding,
    SpecialistModelRequest,
    SpecialistTask,
    SpecialistToolDispatchResult,
)


class EvidenceResolutionErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    CROSS_RUN_REF = "CROSS_RUN_REF"
    UNKNOWN_REF = "UNKNOWN_REF"
    MALFORMED_REF = "MALFORMED_REF"
    STORE_MISMATCH = "STORE_MISMATCH"


class EvidenceResolutionError(ValueError):
    def __init__(self, code: EvidenceResolutionErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


_STORE_ERROR_MAP = {
    EvidenceStoreErrorCode.CROSS_RUN_REF: EvidenceResolutionErrorCode.CROSS_RUN_REF,
    EvidenceStoreErrorCode.UNKNOWN_REF: EvidenceResolutionErrorCode.UNKNOWN_REF,
    EvidenceStoreErrorCode.MALFORMED_REF: EvidenceResolutionErrorCode.MALFORMED_REF,
}
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class FindingStore:
    """Resolve immutable Specialist findings for exactly one run."""

    def __init__(self, run_id: str) -> None:
        if type(run_id) is not str or _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("run_id must be exactly 32 lowercase hex characters")
        self._run_id = run_id
        self._by_id: dict[str, SpecialistFinding] = {}
        self._lock = RLock()

    @property
    def run_id(self) -> str:
        return self._run_id

    def add(self, finding: SpecialistFinding) -> SpecialistFinding:
        try:
            validated = SpecialistFinding.model_validate(finding)
        except (TypeError, ValidationError, ValueError) as error:
            raise EvidenceResolutionError(
                EvidenceResolutionErrorCode.INVALID_INPUT,
                str(error),
            ) from error
        if validated.run_id != self._run_id:
            raise EvidenceResolutionError(
                EvidenceResolutionErrorCode.STORE_MISMATCH,
                "finding belongs to another run",
            )
        with self._lock:
            if validated.finding_id in self._by_id:
                raise EvidenceResolutionError(
                    EvidenceResolutionErrorCode.STORE_MISMATCH,
                    "finding ID is already allocated in this run",
                )
            self._by_id = {**self._by_id, validated.finding_id: validated}
        return validated

    def resolve(self, finding_id: str) -> SpecialistFinding:
        try:
            validated_id = TypeAdapter(Identifier).validate_python(finding_id)
        except (TypeError, ValidationError, ValueError) as error:
            raise EvidenceResolutionError(
                EvidenceResolutionErrorCode.INVALID_INPUT,
                "finding ID is malformed",
            ) from error
        with self._lock:
            try:
                return self._by_id[validated_id]
            except KeyError as error:
                raise EvidenceResolutionError(
                    EvidenceResolutionErrorCode.STORE_MISMATCH,
                    "finding ID is not present in this run store",
                ) from error


def resolve_evidence_view(
    *,
    evidence_store: EvidenceStore,
    run_id: str,
    evidence_refs: tuple[str, ...],
) -> ResolvedEvidenceView:
    """Resolve exactly the named refs without exposing store-wide enumeration."""

    if not isinstance(evidence_store, EvidenceStore):
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.INVALID_INPUT,
            "evidence_store must be an EvidenceStore",
        )
    if type(run_id) is not str or run_id != evidence_store.run_id:
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.STORE_MISMATCH,
            "resolver run does not match the Evidence Store",
        )
    if (
        type(evidence_refs) is not tuple
        or len(evidence_refs) > MAX_EVIDENCE_REFS
        or any(type(reference) is not str for reference in evidence_refs)
        or len(evidence_refs) != len(set(evidence_refs))
    ):
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.INVALID_INPUT,
            "evidence_refs must be an exact tuple of unique refs",
        )
    try:
        evidence = tuple(evidence_store.resolve(reference) for reference in evidence_refs)
        return ResolvedEvidenceView(
            schema_version="phase2.resolved-evidence-view.v1",
            run_id=run_id,
            evidence=evidence,
        )
    except EvidenceStoreError as error:
        raise EvidenceResolutionError(
            _STORE_ERROR_MAP.get(
                error.code,
                EvidenceResolutionErrorCode.INVALID_INPUT,
            ),
            str(error),
        ) from error
    except (TypeError, ValidationError, ValueError) as error:
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.INVALID_INPUT,
            str(error),
        ) from error


def build_specialist_model_request(
    *,
    task: SpecialistTask,
    dispatch_result: SpecialistToolDispatchResult,
    budget_snapshot: BudgetSnapshot,
    evidence_store: EvidenceStore,
) -> SpecialistModelRequest:
    """Build the unchanged model request from one exact dispatch result."""

    try:
        validated_task = SpecialistTask.model_validate(task)
        validated_result = SpecialistToolDispatchResult.model_validate(
            dispatch_result
        )
        validated_snapshot = BudgetSnapshot.model_validate(budget_snapshot)
    except (TypeError, ValidationError, ValueError) as error:
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.INVALID_INPUT,
            str(error),
        ) from error

    authorization = validated_result.specialist_authorization
    record = validated_result.tool_call_record
    expected_owner, expected_source, expected_tool = SPECIALIST_TOOL_BINDINGS[
        validated_task.specialist_role
    ]
    charge_sequence = authorization.tool_charged_snapshot_sequence
    if (
        charge_sequence is None
        or validated_task.run_id != authorization.run_id
        or validated_task.tool_authorization_id != authorization.authorization_id
        or validated_task.model_capacity_slot_id != authorization.capacity_slot_id
        or validated_task.node_id != authorization.owner_node_id
        or authorization.owner_role is not expected_owner
        or validated_task.source is not expected_source
        or authorization.source is not expected_source
        or validated_task.tool_name is not expected_tool
        or authorization.tool_name is not expected_tool
        or record.run_id != validated_task.run_id
        or record.incident_id != validated_task.incident_id
        or record.task_id != validated_task.node_id
        or record.action != validated_task.query
        or validated_snapshot.sequence < charge_sequence
        or validated_snapshot.run_id != authorization.run_id
        or validated_snapshot.case_id != authorization.case_id
        or validated_snapshot.variant is not authorization.variant
        or authorization.authorization_id
        not in validated_snapshot.active_specialist_authorization_ids
        or authorization.capacity_slot_id
        in validated_snapshot.active_capacity_slot_ids
        or authorization.model_lease_id is not None
    ):
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.STORE_MISMATCH,
            "dispatch result, task, and current budget lineage do not match",
        )

    dependency_view = resolve_evidence_view(
        evidence_store=evidence_store,
        run_id=validated_task.run_id,
        evidence_refs=validated_task.dependency_evidence_refs,
    )
    new_evidence_view = resolve_evidence_view(
        evidence_store=evidence_store,
        run_id=validated_task.run_id,
        evidence_refs=record.evidence_refs,
    )
    if new_evidence_view.evidence != record.evidence:
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.STORE_MISMATCH,
            "tool record Evidence differs from canonical store records",
        )
    try:
        return SpecialistModelRequest(
            schema_version="phase2.specialist-model-request.v1",
            task=validated_task,
            tool_call_record=record,
            new_evidence=new_evidence_view.evidence,
            dependency_finding_ids=validated_task.dependency_finding_ids,
            resolved_dependency_evidence_view=dependency_view,
            budget_snapshot=validated_snapshot,
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.INVALID_INPUT,
            str(error),
        ) from error


def build_judge_request(
    *,
    judge_request_id: str,
    run_id: str,
    incident: Incident,
    admitted_graph: AdmittedInvestigationGraph,
    finding_ids: tuple[str, ...],
    finding_store: FindingStore,
    evidence_store: EvidenceStore,
    budget_snapshot: BudgetSnapshot,
    refinement_round: int,
    allowed_actions: ModelAllowedActions,
    conditional_refinement_bundle_id: str | None,
) -> JudgeRequest:
    """Reconstruct one Judge request from current-run canonical stores."""

    try:
        validated_request_id = TypeAdapter(Identifier).validate_python(
            judge_request_id
        )
        if type(run_id) is not str or _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("run_id is malformed")
        validated_incident = revalidate_phase1_model(incident, Incident)
        validated_graph = AdmittedInvestigationGraph.model_validate(
            admitted_graph
        )
        validated_snapshot = BudgetSnapshot.model_validate(budget_snapshot)
        if (
            type(finding_ids) is not tuple
            or not finding_ids
            or len(finding_ids) > MAX_PLAN_NODES + MAX_REFINEMENT_NODES
            or len(finding_ids) != len(set(finding_ids))
            or any(type(item) is not str for item in finding_ids)
            or not isinstance(finding_store, FindingStore)
            or not isinstance(evidence_store, EvidenceStore)
            or type(refinement_round) is not int
            or refinement_round not in {0, 1}
            or not isinstance(allowed_actions, ModelAllowedActions)
        ):
            raise ValueError("Judge builder input shape is invalid")
        validated_finding_ids = tuple(
            TypeAdapter(Identifier).validate_python(item)
            for item in finding_ids
        )
        validated_bundle_id = (
            None
            if conditional_refinement_bundle_id is None
            else TypeAdapter(Identifier).validate_python(
                conditional_refinement_bundle_id
            )
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.INVALID_INPUT,
            str(error),
        ) from error

    if (
        finding_store.run_id != run_id
        or evidence_store.run_id != run_id
        or validated_graph.run_id != run_id
        or validated_graph.incident_id != validated_incident.incident_id
        or validated_snapshot.run_id != run_id
    ):
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.STORE_MISMATCH,
            "Judge builder inputs do not share one exact run and incident",
        )

    findings = tuple(
        finding_store.resolve(item) for item in validated_finding_ids
    )
    graph_node_order = {
        item.node_id: index
        for index, item in enumerate(validated_graph.all_nodes)
    }
    finding_node_ids = tuple(item.node_id for item in findings)
    if (
        len(finding_node_ids) != len(set(finding_node_ids))
        or any(item not in graph_node_order for item in finding_node_ids)
        or finding_node_ids
        != tuple(sorted(finding_node_ids, key=graph_node_order.__getitem__))
    ):
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.STORE_MISMATCH,
            "finding bodies are not in canonical admitted-graph order",
        )
    projected_refs: list[str] = []
    seen_refs: set[str] = set()
    for finding in findings:
        for reference in finding.evidence_refs:
            if reference not in seen_refs:
                seen_refs.add(reference)
                projected_refs.append(reference)
    available_evidence_refs = tuple(projected_refs)
    resolved_view = resolve_evidence_view(
        evidence_store=evidence_store,
        run_id=run_id,
        evidence_refs=available_evidence_refs,
    )
    try:
        return JudgeRequest(
            schema_version="phase2.judge-request.v1",
            judge_request_id=validated_request_id,
            run_id=run_id,
            incident=validated_incident,
            admitted_graph=validated_graph,
            finding_ids=validated_finding_ids,
            findings=findings,
            available_evidence_refs=available_evidence_refs,
            resolved_evidence_view=resolved_view,
            budget_snapshot=validated_snapshot,
            refinement_round=refinement_round,
            allowed_actions=allowed_actions,
            conditional_refinement_bundle_id=(
                validated_bundle_id
            ),
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise EvidenceResolutionError(
            EvidenceResolutionErrorCode.STORE_MISMATCH,
            str(error),
        ) from error
