"""Fail-closed validation for final RCA results and agent reports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import TypeVar, cast

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from ecomsre.phase1.contracts import (
    AgentRunReport,
    Evidence,
    EvidenceSource,
    Incident,
    RCADecision,
    RCAResult,
)
from ecomsre.phase1.evidence import (
    EvidenceStore,
    EvidenceStoreError,
    EvidenceStoreErrorCode,
)
from ecomsre.phase1.semantics import evidence_supports_mechanism


class EvidenceValidationReason(str, Enum):
    SCHEMA_REVALIDATION_FAILED = "SCHEMA_REVALIDATION_FAILED"
    MALFORMED_EVIDENCE_REF = "MALFORMED_EVIDENCE_REF"
    CROSS_RUN_EVIDENCE_REF = "CROSS_RUN_EVIDENCE_REF"
    UNKNOWN_EVIDENCE_REF = "UNKNOWN_EVIDENCE_REF"
    EVIDENCE_ROLE_OVERLAP = "EVIDENCE_ROLE_OVERLAP"
    SLI_MISMATCH = "SLI_MISMATCH"
    INSUFFICIENT_INDEPENDENT_SOURCES = "INSUFFICIENT_INDEPENDENT_SOURCES"
    INSUFFICIENT_MATCHING_EVIDENCE = "INSUFFICIENT_MATCHING_EVIDENCE"
    REQUIRED_CHANGES_EVIDENCE_MISSING = (
        "REQUIRED_CHANGES_EVIDENCE_MISSING"
    )
    REPORT_RUN_MISMATCH = "REPORT_RUN_MISMATCH"
    REPORT_INCIDENT_MISMATCH = "REPORT_INCIDENT_MISMATCH"
    REPORT_EVIDENCE_INDEX_MISMATCH = "REPORT_EVIDENCE_INDEX_MISMATCH"
    REPORT_BUDGET_MISMATCH = "REPORT_BUDGET_MISMATCH"
    REPORT_TOOL_RECORD_MISMATCH = "REPORT_TOOL_RECORD_MISMATCH"
    REPORT_VALIDITY_FLAG_FALSE = "REPORT_VALIDITY_FLAG_FALSE"
    MODEL_CONFIGURATION_MISMATCH = "MODEL_CONFIGURATION_MISMATCH"
    UNSUPPORTED_TOOL_ACTION = "UNSUPPORTED_TOOL_ACTION"


class EvidenceValidationError(ValueError):
    """Typed validation error with a stable machine-readable reason."""

    def __init__(
        self,
        code: EvidenceValidationReason,
        detail: str,
    ) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


_STORE_REASON = {
    EvidenceStoreErrorCode.MALFORMED_REF: (
        EvidenceValidationReason.MALFORMED_EVIDENCE_REF
    ),
    EvidenceStoreErrorCode.CROSS_RUN_REF: (
        EvidenceValidationReason.CROSS_RUN_EVIDENCE_REF
    ),
    EvidenceStoreErrorCode.UNKNOWN_REF: (
        EvidenceValidationReason.UNKNOWN_EVIDENCE_REF
    ),
}

_Phase1Record = TypeVar("_Phase1Record", bound=BaseModel)


def _format_storage_keys(keys: Iterable[object]) -> str:
    rendered = (
        f"{type(key).__name__}:{key!r}"
        for key in keys
    )
    return ", ".join(sorted(rendered))


def _reject_undeclared_model_storage(
    value: object,
    visited_ids: set[int],
) -> None:
    """Fail closed on undeclared Pydantic storage anywhere in a record graph."""

    if isinstance(value, BaseModel):
        object_id = id(value)
        if object_id in visited_ids:
            return
        visited_ids.add(object_id)

        declared_fields = set(type(value).model_fields)
        stored_fields = set(value.__dict__)
        unexpected_fields = stored_fields - declared_fields
        fields_set = value.__pydantic_fields_set__
        if type(fields_set) not in {set, frozenset}:
            raise EvidenceValidationError(
                EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
                "__pydantic_fields_set__ must be a plain set or frozenset",
            )
        unexpected_fields_set = {
            field_name
            for field_name in fields_set
            if not isinstance(field_name, str)
            or field_name not in declared_fields
        }
        if unexpected_fields_set:
            names = _format_storage_keys(unexpected_fields_set)
            raise EvidenceValidationError(
                EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
                f"unexpected __pydantic_fields_set__ entries: {names}",
            )

        pydantic_private = value.__pydantic_private__
        if pydantic_private is not None:
            if not isinstance(pydantic_private, Mapping):
                raise EvidenceValidationError(
                    EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
                    "__pydantic_private__ is not a mapping",
                )
            if pydantic_private:
                names = _format_storage_keys(pydantic_private)
                raise EvidenceValidationError(
                    EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
                    f"nonempty __pydantic_private__ storage: {names}",
                )

        pydantic_extra = value.__pydantic_extra__
        if pydantic_extra is not None:
            if not isinstance(pydantic_extra, Mapping):
                raise EvidenceValidationError(
                    EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
                    "__pydantic_extra__ is not a mapping",
                )
            unexpected_fields.update(pydantic_extra)
        if unexpected_fields:
            names = _format_storage_keys(unexpected_fields)
            raise EvidenceValidationError(
                EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
                f"undeclared model storage keys: {names}",
            )

        for field_name in declared_fields & stored_fields:
            _reject_undeclared_model_storage(
                value.__dict__[field_name],
                visited_ids,
            )
        return

    if isinstance(value, (tuple, list)):
        object_id = id(value)
        if object_id in visited_ids:
            return
        visited_ids.add(object_id)
        for item in value:
            _reject_undeclared_model_storage(item, visited_ids)
        return

    if isinstance(value, Mapping):
        object_id = id(value)
        if object_id in visited_ids:
            return
        visited_ids.add(object_id)
        for key, item in value.items():
            _reject_undeclared_model_storage(key, visited_ids)
            _reject_undeclared_model_storage(item, visited_ids)


def revalidate_phase1_model(
    record: object,
    record_type: type[_Phase1Record],
) -> _Phase1Record:
    """Reject hidden model storage and return an exact plain reconstruction."""

    if type(record) is not record_type:
        raise EvidenceValidationError(
            EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
            f"record must be an exact {record_type.__name__}",
        )
    model_record = cast(BaseModel, record)
    try:
        _reject_undeclared_model_storage(model_record, set())
    except EvidenceValidationError:
        raise
    except Exception as error:
        raise EvidenceValidationError(
            EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
            f"model storage scan failed: {type(error).__name__}: {error}",
        ) from error
    try:
        plain = model_record.model_dump(
            mode="python",
            round_trip=True,
            warnings="error",
        )
        reconstructed = record_type.model_validate(plain)
    except (
        PydanticSerializationError,
        RecursionError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        raise EvidenceValidationError(
            EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
            f"{type(error).__name__}: {error}",
        ) from error
    if reconstructed != model_record:
        raise EvidenceValidationError(
            EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED,
            "plain-data reconstruction differs from the supplied object",
        )
    return reconstructed


def _resolve(store: EvidenceStore, reference: str) -> Evidence:
    try:
        return store.resolve(reference)
    except EvidenceStoreError as error:
        reason = _STORE_REASON.get(error.code)
        if reason is None:
            raise
        raise EvidenceValidationError(reason, str(error)) from error


def validate_rca_result(
    result: RCAResult,
    store: EvidenceStore,
    incident: Incident,
) -> RCAResult:
    """Validate final Evidence capability refs without repairing the result."""

    revalidate_phase1_model(result, RCAResult)

    supporting = tuple(
        _resolve(store, reference)
        for reference in result.supporting_evidence
    )
    tuple(
        _resolve(store, reference)
        for reference in result.contradicting_evidence
    )

    overlap = set(result.supporting_evidence) & set(
        result.contradicting_evidence
    )
    if overlap:
        raise EvidenceValidationError(
            EvidenceValidationReason.EVIDENCE_ROLE_OVERLAP,
            "supporting and contradicting evidence overlap",
        )

    if (
        result.affected_sli is not None
        and result.affected_sli != incident.affected_sli
    ):
        raise EvidenceValidationError(
            EvidenceValidationReason.SLI_MISMATCH,
            "RCA affected_sli conflicts with the incident",
        )

    if result.decision is not RCADecision.RCA_CONFIRMED:
        return result

    independent_sources = {item.source for item in supporting}
    if len(independent_sources) < 2:
        raise EvidenceValidationError(
            EvidenceValidationReason.INSUFFICIENT_INDEPENDENT_SOURCES,
            "confirmed RCA needs at least two independent Evidence sources",
        )

    matching = tuple(
        item
        for item in supporting
        if item.service == result.root_service
        and result.fault_mechanism is not None
        and evidence_supports_mechanism(item, result.fault_mechanism)
    )
    matching_sources = {item.source for item in matching}
    if len(matching) < 2 or len(matching_sources) < 2:
        raise EvidenceValidationError(
            EvidenceValidationReason.INSUFFICIENT_MATCHING_EVIDENCE,
            "fewer than two independent Evidence objects match the "
            "claimed root service and fault mechanism",
        )

    if (
        result.fault_mechanism == "runtime_configuration_failure"
        and not any(item.source is EvidenceSource.CHANGES for item in matching)
    ):
        raise EvidenceValidationError(
            EvidenceValidationReason.REQUIRED_CHANGES_EVIDENCE_MISSING,
            "runtime_configuration_failure requires matching CHANGES Evidence",
        )

    return result


def validate_agent_report(
    report: AgentRunReport,
    store: EvidenceStore,
    incident: Incident,
) -> AgentRunReport:
    """Independently validate a complete report against its run-local store."""

    revalidate_phase1_model(report, AgentRunReport)

    if report.run_id != store.run_id:
        raise EvidenceValidationError(
            EvidenceValidationReason.REPORT_RUN_MISMATCH,
            "report run_id conflicts with EvidenceStore run_id",
        )
    if report.request.incident != incident:
        raise EvidenceValidationError(
            EvidenceValidationReason.REPORT_INCIDENT_MISMATCH,
            "report incident conflicts with validator incident",
        )

    configuration = report.model_configuration
    for model_record in report.model_call_records:
        request = model_record.request
        if (
            request.model_name != configuration.model_name
            or request.temperature != configuration.temperature
            or request.timeout_seconds
            != configuration.model_timeout_seconds
        ):
            raise EvidenceValidationError(
                EvidenceValidationReason.MODEL_CONFIGURATION_MISMATCH,
                "model request conflicts with report model configuration",
            )

    if report.final_rca is not None:
        validate_rca_result(report.final_rca, store, incident)

    snapshot = store.snapshot()
    if report.evidence_index != snapshot:
        raise EvidenceValidationError(
            EvidenceValidationReason.REPORT_EVIDENCE_INDEX_MISMATCH,
            "report evidence index is not the exact store snapshot",
        )

    if any(
        record.status == "ERROR"
        and record.evidence
        and not record.evidence_quarantined
        for record in report.tool_call_records
    ):
        raise EvidenceValidationError(
            EvidenceValidationReason.REPORT_TOOL_RECORD_MISMATCH,
            "non-quarantined failed tool records cannot contain Evidence",
        )

    snapshot_refs = tuple(item.evidence_ref for item in snapshot)
    tool_evidence = tuple(
        item
        for record in report.tool_call_records
        for item in record.evidence
    )
    tool_refs = tuple(item.evidence_ref for item in tool_evidence)
    if tool_refs != snapshot_refs or tool_evidence != snapshot:
        raise EvidenceValidationError(
            EvidenceValidationReason.REPORT_TOOL_RECORD_MISMATCH,
            "tool records do not exactly account for stored Evidence",
        )

    expected_sources = {
        "metrics": EvidenceSource.METRICS,
        "logs": EvidenceSource.LOGS,
        "traces": EvidenceSource.TRACES,
        "changes": EvidenceSource.CHANGES,
    }
    for tool_record in report.tool_call_records:
        action_type = tool_record.action.action_type
        expected_source = expected_sources.get(action_type)
        if expected_source is None:
            raise EvidenceValidationError(
                EvidenceValidationReason.UNSUPPORTED_TOOL_ACTION,
                f"unsupported tool action: {action_type}",
            )
        if any(
            item.source is not expected_source
            for item in tool_record.evidence
        ):
            raise EvidenceValidationError(
                EvidenceValidationReason.REPORT_TOOL_RECORD_MISMATCH,
                "tool action and Evidence source conflict",
            )
        for item in tool_record.evidence:
            if _resolve(store, item.evidence_ref) != item:
                raise EvidenceValidationError(
                    EvidenceValidationReason.REPORT_TOOL_RECORD_MISMATCH,
                    "tool Evidence conflicts with stored Evidence",
                )

    snapshot_budget = report.budget_snapshot
    limits = report.budget_limits
    recorded_tokens = sum(
        record.charged_tokens for record in report.model_call_records
    )
    if (
        snapshot_budget.limits != limits
        or snapshot_budget.model_calls
        != sum(
            record.model_call_consumed
            for record in report.model_call_records
        )
        or snapshot_budget.tool_calls
        != sum(
            record.budget_consumed
            for record in report.tool_call_records
        )
        or snapshot_budget.total_tokens != recorded_tokens
        or snapshot_budget.model_calls > limits.max_model_calls
        or snapshot_budget.tool_calls > limits.max_tool_calls
        or snapshot_budget.total_tokens > limits.max_total_tokens
    ):
        raise EvidenceValidationError(
            EvidenceValidationReason.REPORT_BUDGET_MISMATCH,
            "report call or token accounting conflicts with budget limits",
        )

    if not report.schema_valid or not report.evidence_references_valid:
        raise EvidenceValidationError(
            EvidenceValidationReason.REPORT_VALIDITY_FLAG_FALSE,
            "report validity flags must be true after independent validation",
        )
    return report
