"""Versioned Provider protocol v4 records and frozen integer gates."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.controller_modes import ProviderOutputModeV22
from ecomsre.dta_v2.v22.controller_provider import ProviderHttpErrorV22
from ecomsre.dta_v2.v22.controller_runtime import (
    ControllerProtocolDispositionV22,
    process_controller_decision_v22,
)
from ecomsre.dta_v2.v22.provider_boundary_v4 import (
    MaterializedProtocolRequestV4,
    ProviderBoundaryRequestV4,
    materialize_protocol_requests_v4,
)
from ecomsre.dta_v2.v22.provider_protocol_v4 import (
    ProviderBoundaryTurnV4,
    ProviderResponseProtocolErrorV4,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    Sha256V22,
    semantic_sha256_v22,
)


ProtocolCategoryV4 = Literal[
    "READ",
    "COMMIT",
    "NO_INCIDENT",
    "ABSTAIN",
    "BUDGET_EXHAUSTED",
    "SOURCE_UNAVAILABLE",
    "STALE_ACTION_CORRECTION",
    "INVALID_REF_CORRECTION",
]


class ProviderTransitionStatusV4(str, Enum):
    COMPLETED_RESPONSE = "COMPLETED_RESPONSE"
    PROVIDER_TRANSPORT_ABORT = "PROVIDER_TRANSPORT_ABORT"
    NOT_ATTEMPTED_AFTER_ABORT = "NOT_ATTEMPTED_AFTER_ABORT"
    NOT_ATTEMPTED_AFTER_PROBE_FAILURE = "NOT_ATTEMPTED_AFTER_PROBE_FAILURE"


class ProviderProtocolFailureClassV4(str, Enum):
    """One mutually exclusive primary disposition for every planned transition."""

    ACCEPTED = "ACCEPTED"
    INVALID_ALIAS_DECISION_SHAPE = "INVALID_ALIAS_DECISION_SHAPE"
    LOCAL_DYNAMIC_SCHEMA_REJECTED = "LOCAL_DYNAMIC_SCHEMA_REJECTED"
    UNKNOWN_ALIAS = "UNKNOWN_ALIAS"
    STALE_ALIAS = "STALE_ALIAS"
    WRONG_KIND_ALIAS = "WRONG_KIND_ALIAS"
    DUPLICATE_ALIAS = "DUPLICATE_ALIAS"
    RUNTIME_REJECTED = "RUNTIME_REJECTED"
    INTENT_MISMATCH = "INTENT_MISMATCH"
    PROVIDER_RESPONSE_PROTOCOL_FAILURE = "PROVIDER_RESPONSE_PROTOCOL_FAILURE"
    PROVIDER_TRANSPORT_ABORT = "PROVIDER_TRANSPORT_ABORT"
    NOT_ATTEMPTED_AFTER_ABORT = "NOT_ATTEMPTED_AFTER_ABORT"
    NOT_ATTEMPTED_AFTER_PROBE_FAILURE = "NOT_ATTEMPTED_AFTER_PROBE_FAILURE"


class ProviderTransportReasonV4(str, Enum):
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    HTTP_RATE_LIMITED = "HTTP_RATE_LIMITED"
    HTTP_CLIENT_ERROR = "HTTP_CLIENT_ERROR"
    HTTP_SERVER_ERROR = "HTTP_SERVER_ERROR"


class ProviderProtocolReplicateTerminalV4(str, Enum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


class ProviderProtocolTransitionV4(DtaModelV22):
    schema_version: Literal["dta-v22.provider-protocol-transition.v4"]
    transition_id: str = Field(pattern=r"^dta-v22-v4-[ab]-[0-9]{2}$")
    ordinal: StrictInt = Field(ge=1, le=24)
    arm: ControllerArmV22
    protocol_intent: Literal["READ", "COMMIT", "NO_INCIDENT", "ABSTAIN"]
    protocol_category: ProtocolCategoryV4
    transition_kind: Literal["ORDINARY", "CORRECTION_ENVELOPE"]
    correction_class: Literal["STALE", "INVALID_REF"] | None
    selected_mode: ProviderOutputModeV22
    status: ProviderTransitionStatusV4
    provider_request_sha256: Sha256V22
    raw_response_sha256: Sha256V22 | None
    parsed_alias: StrictBool | None
    alias_resolved: StrictBool | None
    runtime_admitted: StrictBool | None
    intent_conformant: StrictBool | None
    accepted: StrictBool | None
    failure_class: ProviderProtocolFailureClassV4
    failure_detail_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{0,79}$",
    )
    token_usage_known: StrictBool | None
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    latency_ms: StrictInt | None = Field(default=None, ge=0)
    unknown_alias_dispatches: Literal[0]
    stale_alias_dispatches: Literal[0]
    invalid_evidence_dispatches: Literal[0]
    agent_dispatches: Literal[0]
    write_dispatches: Literal[0]
    runbook_dispatches: Literal[0]
    transition_sha256: Sha256V22

    @model_validator(mode="after")
    def require_transition(self) -> ProviderProtocolTransitionV4:
        completed_fields = (
            self.raw_response_sha256,
            self.parsed_alias,
            self.alias_resolved,
            self.runtime_admitted,
            self.intent_conformant,
            self.accepted,
            self.token_usage_known,
            self.latency_ms,
        )
        if self.status is ProviderTransitionStatusV4.COMPLETED_RESPONSE:
            if any(item is None for item in completed_fields):
                raise ValueError("completed response lacks bounded result fields")
            expected_acceptance = bool(
                self.parsed_alias
                and self.alias_resolved
                and self.runtime_admitted
                and self.intent_conformant
            )
            if self.accepted is not expected_acceptance:
                raise ValueError("completed response acceptance chain differs")
            if (
                (self.alias_resolved is True and self.parsed_alias is not True)
                or (self.runtime_admitted is True and self.alias_resolved is not True)
                or (self.intent_conformant is True and self.alias_resolved is not True)
            ):
                raise ValueError("completed response protocol chain differs")
            if (self.accepted is True) != (
                self.failure_class is ProviderProtocolFailureClassV4.ACCEPTED
            ):
                raise ValueError("completed response primary taxonomy differs")
            if self.failure_class in {
                ProviderProtocolFailureClassV4.PROVIDER_TRANSPORT_ABORT,
                ProviderProtocolFailureClassV4.NOT_ATTEMPTED_AFTER_ABORT,
                ProviderProtocolFailureClassV4.NOT_ATTEMPTED_AFTER_PROBE_FAILURE,
            }:
                raise ValueError("completed response has a non-response taxonomy")
            if (
                self.failure_class
                is ProviderProtocolFailureClassV4.PROVIDER_RESPONSE_PROTOCOL_FAILURE
            ) != (self.failure_detail_code is not None):
                raise ValueError("response protocol detail taxonomy differs")
            if self.token_usage_known:
                if self.input_tokens is None or self.output_tokens is None:
                    raise ValueError("known token usage lacks exact counts")
            elif (
                self.input_tokens is not None
                or self.output_tokens is not None
                or self.failure_class
                is not ProviderProtocolFailureClassV4.PROVIDER_RESPONSE_PROTOCOL_FAILURE
            ):
                raise ValueError("unknown token usage disposition differs")
        elif self.status is ProviderTransitionStatusV4.PROVIDER_TRANSPORT_ABORT:
            if (
                self.failure_class
                is not ProviderProtocolFailureClassV4.PROVIDER_TRANSPORT_ABORT
                or self.failure_detail_code is None
                or any(item is not None for item in completed_fields)
            ):
                raise ValueError("transport abort result shape differs")
        else:
            expected = (
                ProviderProtocolFailureClassV4.NOT_ATTEMPTED_AFTER_ABORT
                if self.status is ProviderTransitionStatusV4.NOT_ATTEMPTED_AFTER_ABORT
                else ProviderProtocolFailureClassV4.NOT_ATTEMPTED_AFTER_PROBE_FAILURE
            )
            if (
                self.failure_class is not expected
                or self.failure_detail_code is not None
                or any(item is not None for item in completed_fields)
            ):
                raise ValueError("unattempted transition contains result evidence")
        if (self.correction_class is not None) != (
            self.transition_kind == "CORRECTION_ENVELOPE"
        ):
            raise ValueError("correction transition classification differs")
        expected_sha = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected_sha:
            raise ValueError("v4 transition digest differs")
        return self


def _transition_payload(
    spec: MaterializedProtocolRequestV4,
    *,
    selected_mode: ProviderOutputModeV22,
) -> dict[str, Any]:
    return {
        "schema_version": "dta-v22.provider-protocol-transition.v4",
        "transition_id": spec.transition_id,
        "ordinal": int(spec.transition_id.rsplit("-", 1)[1]),
        "arm": spec.arm,
        "protocol_intent": spec.protocol_intent,
        "protocol_category": spec.protocol_category,
        "transition_kind": spec.transition_kind,
        "correction_class": spec.correction_class,
        "selected_mode": selected_mode,
        "provider_request_sha256": spec.request.request_sha256,
        "unknown_alias_dispatches": 0,
        "stale_alias_dispatches": 0,
        "invalid_evidence_dispatches": 0,
        "agent_dispatches": 0,
        "write_dispatches": 0,
        "runbook_dispatches": 0,
    }


def _build_transition(payload: dict[str, Any]) -> ProviderProtocolTransitionV4:
    draft = ProviderProtocolTransitionV4.model_construct(
        **payload,
        transition_sha256="0" * 64,
    )
    return ProviderProtocolTransitionV4.model_validate(
        {
            **payload,
            "transition_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"transition_sha256"})
            ),
        }
    )


def completed_transition_v4(
    *,
    spec: MaterializedProtocolRequestV4,
    accepted: bool,
    parsed_alias: bool,
    alias_resolved: bool,
    runtime_admitted: bool,
    intent_conformant: bool,
    input_tokens: int | None,
    output_tokens: int | None,
    latency_ms: int,
    provider_request_sha256: str,
    raw_response_sha256: str,
    failure_class: ProviderProtocolFailureClassV4,
    selected_mode: ProviderOutputModeV22 = ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
    failure_detail_code: str | None = None,
) -> ProviderProtocolTransitionV4:
    if provider_request_sha256 != spec.request.request_sha256:
        raise ValueError("completed transition request differs from frozen request")
    return _build_transition(
        {
            **_transition_payload(spec, selected_mode=selected_mode),
            "status": ProviderTransitionStatusV4.COMPLETED_RESPONSE,
            "raw_response_sha256": raw_response_sha256,
            "parsed_alias": parsed_alias,
            "alias_resolved": alias_resolved,
            "runtime_admitted": runtime_admitted,
            "intent_conformant": intent_conformant,
            "accepted": accepted,
            "failure_class": failure_class,
            "failure_detail_code": failure_detail_code,
            "token_usage_known": input_tokens is not None and output_tokens is not None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
        }
    )


def transport_abort_transition_v4(
    *,
    spec: MaterializedProtocolRequestV4,
    provider_request_sha256: str,
    transport_reason_code: str,
    selected_mode: ProviderOutputModeV22 = ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
) -> ProviderProtocolTransitionV4:
    if provider_request_sha256 != spec.request.request_sha256:
        raise ValueError("abort transition request differs from frozen request")
    reason = ProviderTransportReasonV4(transport_reason_code)
    return _build_transition(
        {
            **_transition_payload(spec, selected_mode=selected_mode),
            "status": ProviderTransitionStatusV4.PROVIDER_TRANSPORT_ABORT,
            "raw_response_sha256": None,
            "parsed_alias": None,
            "alias_resolved": None,
            "runtime_admitted": None,
            "intent_conformant": None,
            "accepted": None,
            "failure_class": ProviderProtocolFailureClassV4.PROVIDER_TRANSPORT_ABORT,
            "failure_detail_code": reason.value,
            "token_usage_known": None,
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": None,
        }
    )


def unattempted_after_abort_transition_v4(
    *,
    spec: MaterializedProtocolRequestV4,
    selected_mode: ProviderOutputModeV22 = ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
) -> ProviderProtocolTransitionV4:
    return _build_transition(
        {
            **_transition_payload(spec, selected_mode=selected_mode),
            "status": ProviderTransitionStatusV4.NOT_ATTEMPTED_AFTER_ABORT,
            "raw_response_sha256": None,
            "parsed_alias": None,
            "alias_resolved": None,
            "runtime_admitted": None,
            "intent_conformant": None,
            "accepted": None,
            "failure_class": ProviderProtocolFailureClassV4.NOT_ATTEMPTED_AFTER_ABORT,
            "failure_detail_code": None,
            "token_usage_known": None,
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": None,
        }
    )


class ProviderProtocolReplicateReportV4(DtaModelV22):
    schema_version: Literal["dta-v22.provider-protocol-replicate-report.v4"]
    replicate_id: Literal["A", "B"]
    implementation_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    implementation_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    manifest_sha256: Sha256V22
    probe_report_sha256: Sha256V22
    selected_mode: ProviderOutputModeV22
    transitions: tuple[ProviderProtocolTransitionV4, ...] = Field(
        min_length=24,
        max_length=24,
    )
    planned_transition_count: Literal[24]
    attempted_transition_count: StrictInt = Field(ge=0, le=24)
    completed_response_count: StrictInt = Field(ge=0, le=24)
    bounded_response_count: StrictInt = Field(ge=0, le=24)
    transport_abort_event_count: StrictInt = Field(ge=0, le=1)
    not_attempted_after_abort_count: StrictInt = Field(ge=0, le=24)
    parsed_output_count: StrictInt = Field(ge=0, le=24)
    alias_resolved_output_count: StrictInt = Field(ge=0, le=24)
    runtime_admitted_output_count: StrictInt = Field(ge=0, le=24)
    protocol_intent_accepted_output_count: StrictInt = Field(ge=0, le=24)
    status_counts: dict[str, StrictInt]
    failure_taxonomy: dict[str, StrictInt]
    transport_reason_counts: dict[str, StrictInt]
    counts_by_arm: dict[str, dict[str, StrictInt]]
    counts_by_category: dict[str, dict[str, StrictInt]]
    ordinary_first_pass_accepted_count: StrictInt = Field(ge=0, le=20)
    ordinary_first_pass_acceptance: float | None
    ordinary_first_pass_by_arm: dict[str, dict[str, StrictInt]]
    correction_accepted_count: StrictInt = Field(ge=0, le=4)
    correction_acceptance: float | None
    correction_by_arm: dict[str, dict[str, StrictInt]]
    final_accepted_count: StrictInt = Field(ge=0, le=24)
    final_acceptance: float | None
    completed_response_with_known_usage_count: StrictInt = Field(ge=0, le=24)
    completed_response_with_unknown_usage_count: StrictInt = Field(ge=0, le=24)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    mean_input_tokens: float | None
    max_input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    provider_calls: StrictInt = Field(ge=0, le=24)
    unknown_alias_dispatches: Literal[0]
    stale_alias_dispatches: Literal[0]
    invalid_evidence_dispatches: Literal[0]
    agent_dispatches: Literal[0]
    write_dispatches: Literal[0]
    runbook_dispatches: Literal[0]
    provider_gate_eligible: StrictBool
    terminal: ProviderProtocolReplicateTerminalV4
    report_sha256: Sha256V22

    @model_validator(mode="after")
    def require_report(self) -> ProviderProtocolReplicateReportV4:
        expected = _report_payload(
            replicate_id=self.replicate_id,
            implementation_commit=self.implementation_commit,
            implementation_tree=self.implementation_tree,
            manifest_sha256=self.manifest_sha256,
            probe_report_sha256=self.probe_report_sha256,
            selected_mode=self.selected_mode,
            transitions=self.transitions,
        )
        observed = self.model_dump(mode="json", exclude={"report_sha256"})
        expected_draft = ProviderProtocolReplicateReportV4.model_construct(
            **expected,
            report_sha256="0" * 64,
        )
        if observed != expected_draft.model_dump(
            mode="json", exclude={"report_sha256"}
        ):
            raise ValueError("v4 replicate metrics differ from frozen gates")
        if self.report_sha256 != semantic_sha256_v22(observed):
            raise ValueError("v4 replicate report digest differs")
        return self


def _summary(values: tuple[ProviderProtocolTransitionV4, ...]) -> dict[str, int]:
    return {
        "planned": len(values),
        "attempted": sum(
            item.status
            in {
                ProviderTransitionStatusV4.COMPLETED_RESPONSE,
                ProviderTransitionStatusV4.PROVIDER_TRANSPORT_ABORT,
            }
            for item in values
        ),
        "completed_response": sum(
            item.status is ProviderTransitionStatusV4.COMPLETED_RESPONSE
            for item in values
        ),
        "accepted": sum(item.accepted is True for item in values),
    }


def _report_payload(
    *,
    replicate_id: str,
    implementation_commit: str,
    implementation_tree: str,
    manifest_sha256: str,
    probe_report_sha256: str,
    selected_mode: ProviderOutputModeV22,
    transitions: tuple[ProviderProtocolTransitionV4, ...],
) -> dict[str, Any]:
    specs = materialize_protocol_requests_v4(replicate_id=replicate_id)  # type: ignore[arg-type]
    if len(transitions) != 24:
        raise ValueError("v4 replicate requires exactly 24 planned transitions")
    for observed, spec in zip(transitions, specs, strict=True):
        if (
            observed.transition_id != spec.transition_id
            or observed.arm is not spec.arm
            or observed.protocol_intent != spec.protocol_intent
            or observed.protocol_category != spec.protocol_category
            or observed.transition_kind != spec.transition_kind
            or observed.correction_class != spec.correction_class
            or observed.selected_mode is not selected_mode
            or observed.provider_request_sha256 != spec.request.request_sha256
        ):
            raise ValueError("v4 transition differs from preregistered matrix")
    status_counts = {
        status.value: sum(item.status is status for item in transitions)
        for status in ProviderTransitionStatusV4
    }
    completed = status_counts[ProviderTransitionStatusV4.COMPLETED_RESPONSE.value]
    aborts = status_counts[ProviderTransitionStatusV4.PROVIDER_TRANSPORT_ABORT.value]
    later = status_counts[ProviderTransitionStatusV4.NOT_ATTEMPTED_AFTER_ABORT.value]
    probe_later = status_counts[
        ProviderTransitionStatusV4.NOT_ATTEMPTED_AFTER_PROBE_FAILURE.value
    ]
    if aborts > 1:
        raise ValueError("v4 replicate permits at most one actual transport abort")
    if aborts:
        abort_index = next(
            index
            for index, item in enumerate(transitions)
            if item.status is ProviderTransitionStatusV4.PROVIDER_TRANSPORT_ABORT
        )
        if any(
            item.status is not ProviderTransitionStatusV4.COMPLETED_RESPONSE
            for item in transitions[:abort_index]
        ) or any(
            item.status is not ProviderTransitionStatusV4.NOT_ATTEMPTED_AFTER_ABORT
            for item in transitions[abort_index + 1 :]
        ):
            raise ValueError("v4 abort and later-unattempted partition differs")
    elif later:
        raise ValueError("later-unattempted transitions require one actual abort")
    complete = completed == 24 and aborts == later == probe_later == 0
    ordinary = tuple(item for item in transitions if item.transition_kind == "ORDINARY")
    correction = tuple(
        item for item in transitions if item.transition_kind == "CORRECTION_ENVELOPE"
    )
    ordinary_accepted = sum(item.accepted is True for item in ordinary)
    correction_accepted = sum(item.accepted is True for item in correction)
    final_accepted = sum(item.accepted is True for item in transitions)
    ordinary_by_arm = {
        arm.value: {
            "accepted": sum(
                item.accepted is True for item in ordinary if item.arm is arm
            ),
            "planned": 10,
        }
        for arm in ControllerArmV22
    }
    correction_by_arm = {
        arm.value: {
            "accepted": sum(
                item.accepted is True for item in correction if item.arm is arm
            ),
            "planned": 2,
        }
        for arm in ControllerArmV22
    }
    counts_by_arm = {
        arm.value: _summary(tuple(item for item in transitions if item.arm is arm))
        for arm in ControllerArmV22
    }
    categories = (
        "READ",
        "COMMIT",
        "NO_INCIDENT",
        "ABSTAIN",
        "BUDGET_EXHAUSTED",
        "SOURCE_UNAVAILABLE",
        "STALE_ACTION_CORRECTION",
        "INVALID_REF_CORRECTION",
    )
    counts_by_category = {
        category: _summary(
            tuple(item for item in transitions if item.protocol_category == category)
        )
        for category in categories
    }
    completed_values = tuple(
        item
        for item in transitions
        if item.status is ProviderTransitionStatusV4.COMPLETED_RESPONSE
    )
    known_usage = tuple(item for item in completed_values if item.token_usage_known)
    unknown_usage_count = completed - len(known_usage)
    input_tokens: int | None
    max_input_tokens: int | None
    mean_input_tokens: float | None
    output_tokens: int | None
    if completed and unknown_usage_count == 0:
        input_token_values: list[int] = []
        output_token_values: list[int] = []
        for item in known_usage:
            if item.input_tokens is None or item.output_tokens is None:
                raise ValueError("known v4 Provider usage lacks exact token counts")
            input_token_values.append(item.input_tokens)
            output_token_values.append(item.output_tokens)
        input_tokens = sum(input_token_values)
        max_input_tokens = max(input_token_values)
        mean_input_tokens = input_tokens / completed
        output_tokens = sum(output_token_values)
    else:
        input_tokens = None
        max_input_tokens = None
        mean_input_tokens = None
        output_tokens = None
    dispatch_fields = (
        "unknown_alias_dispatches",
        "stale_alias_dispatches",
        "invalid_evidence_dispatches",
        "agent_dispatches",
        "write_dispatches",
        "runbook_dispatches",
    )
    dispatches = {
        field: sum(getattr(item, field) for item in transitions)
        for field in dispatch_fields
    }
    failure_taxonomy = {
        failure.value: sum(item.failure_class is failure for item in transitions)
        for failure in ProviderProtocolFailureClassV4
    }
    if sum(failure_taxonomy.values()) != 24:
        raise ValueError("v4 primary failure taxonomy is not a partition")
    transport_reason_counts = {
        reason.value: sum(
            item.failure_detail_code == reason.value for item in transitions
        )
        for reason in ProviderTransportReasonV4
        if any(item.failure_detail_code == reason.value for item in transitions)
    }
    parsed = sum(item.parsed_alias is True for item in transitions)
    resolved = sum(item.alias_resolved is True for item in transitions)
    runtime = sum(item.runtime_admitted is True for item in transitions)
    intent = sum(item.intent_conformant is True for item in transitions)
    pass_gate = (
        complete
        and unknown_usage_count == 0
        and ordinary_accepted >= 19
        and all(value["accepted"] >= 9 for value in ordinary_by_arm.values())
        and correction_accepted == 4
        and all(value["accepted"] == 2 for value in correction_by_arm.values())
        and final_accepted >= 23
        and all(value == 0 for value in dispatches.values())
        and mean_input_tokens is not None
        and mean_input_tokens <= 4_000
        and max_input_tokens is not None
        and max_input_tokens <= 5_500
    )
    return {
        "schema_version": "dta-v22.provider-protocol-replicate-report.v4",
        "replicate_id": replicate_id,
        "implementation_commit": implementation_commit,
        "implementation_tree": implementation_tree,
        "manifest_sha256": manifest_sha256,
        "probe_report_sha256": probe_report_sha256,
        "selected_mode": selected_mode,
        "transitions": transitions,
        "planned_transition_count": 24,
        "attempted_transition_count": completed + aborts,
        "completed_response_count": completed,
        "bounded_response_count": completed,
        "transport_abort_event_count": aborts,
        "not_attempted_after_abort_count": later,
        "parsed_output_count": parsed,
        "alias_resolved_output_count": resolved,
        "runtime_admitted_output_count": runtime,
        "protocol_intent_accepted_output_count": intent,
        "status_counts": status_counts,
        "failure_taxonomy": failure_taxonomy,
        "transport_reason_counts": transport_reason_counts,
        "counts_by_arm": counts_by_arm,
        "counts_by_category": counts_by_category,
        "ordinary_first_pass_accepted_count": ordinary_accepted,
        "ordinary_first_pass_acceptance": ordinary_accepted / 20 if complete else None,
        "ordinary_first_pass_by_arm": ordinary_by_arm,
        "correction_accepted_count": correction_accepted,
        "correction_acceptance": correction_accepted / 4 if complete else None,
        "correction_by_arm": correction_by_arm,
        "final_accepted_count": final_accepted,
        "final_acceptance": final_accepted / 24 if complete else None,
        "completed_response_with_known_usage_count": len(known_usage),
        "completed_response_with_unknown_usage_count": unknown_usage_count,
        "input_tokens": input_tokens,
        "mean_input_tokens": mean_input_tokens,
        "max_input_tokens": max_input_tokens,
        "output_tokens": output_tokens,
        "provider_calls": completed + aborts,
        **dispatches,
        "provider_gate_eligible": complete and unknown_usage_count == 0,
        "terminal": (
            ProviderProtocolReplicateTerminalV4.PASS
            if pass_gate
            else ProviderProtocolReplicateTerminalV4.BLOCKED
        ),
    }


def build_protocol_replicate_report_v4(
    *,
    replicate_id: Literal["A", "B"],
    implementation_commit: str,
    implementation_tree: str,
    manifest_sha256: str,
    probe_report_sha256: str,
    selected_mode: ProviderOutputModeV22,
    transitions: tuple[ProviderProtocolTransitionV4, ...],
) -> ProviderProtocolReplicateReportV4:
    payload = _report_payload(
        replicate_id=replicate_id,
        implementation_commit=implementation_commit,
        implementation_tree=implementation_tree,
        manifest_sha256=manifest_sha256,
        probe_report_sha256=probe_report_sha256,
        selected_mode=selected_mode,
        transitions=transitions,
    )
    draft = ProviderProtocolReplicateReportV4.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return ProviderProtocolReplicateReportV4.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


ProviderCompleteV4 = Callable[
    [ProviderBoundaryRequestV4, ProviderOutputModeV22],
    ProviderBoundaryTurnV4,
]


def _transport_reason(error: BaseException) -> ProviderTransportReasonV4:
    if isinstance(error, ProviderHttpErrorV22):
        if error.status == 429:
            return ProviderTransportReasonV4.HTTP_RATE_LIMITED
        if 400 <= error.status < 500:
            return ProviderTransportReasonV4.HTTP_CLIENT_ERROR
        return ProviderTransportReasonV4.HTTP_SERVER_ERROR
    if isinstance(error, TimeoutError):
        return ProviderTransportReasonV4.TIMEOUT_ERROR
    return ProviderTransportReasonV4.CONNECTION_ERROR


def run_protocol_replicate_v4(
    *,
    replicate_id: Literal["A", "B"],
    implementation_commit: str,
    implementation_tree: str,
    manifest_sha256: str,
    probe_report_sha256: str,
    selected_mode: ProviderOutputModeV22,
    complete: ProviderCompleteV4,
    attempted_calls: Callable[[], int],
) -> ProviderProtocolReplicateReportV4:
    """Run one fixed v4 replicate, returning a report for every Provider outcome."""

    specs = materialize_protocol_requests_v4(replicate_id=replicate_id)
    transitions: list[ProviderProtocolTransitionV4] = []
    start_calls = attempted_calls()
    for index, spec in enumerate(specs):
        before = attempted_calls()
        try:
            turn = complete(spec.request, selected_mode)
        except ProviderResponseProtocolErrorV4 as error:
            if attempted_calls() - before != 1:
                raise RuntimeError(
                    "v4 Provider response call accounting differs"
                ) from error
            transitions.append(
                completed_transition_v4(
                    spec=spec,
                    accepted=False,
                    parsed_alias=False,
                    alias_resolved=False,
                    runtime_admitted=False,
                    intent_conformant=False,
                    input_tokens=error.input_tokens,
                    output_tokens=error.output_tokens,
                    latency_ms=error.monotonic_latency_ms,
                    provider_request_sha256=error.provider_request_sha256,
                    raw_response_sha256=error.raw_response_sha256,
                    failure_class=(
                        ProviderProtocolFailureClassV4.PROVIDER_RESPONSE_PROTOCOL_FAILURE
                    ),
                    failure_detail_code=error.safe_failure_code,
                    selected_mode=selected_mode,
                )
            )
            continue
        except (ConnectionError, TimeoutError, ProviderHttpErrorV22) as error:
            if attempted_calls() - before != 1:
                raise RuntimeError(
                    "v4 Provider abort call accounting differs"
                ) from error
            transitions.append(
                transport_abort_transition_v4(
                    spec=spec,
                    provider_request_sha256=spec.request.request_sha256,
                    transport_reason_code=_transport_reason(error).value,
                    selected_mode=selected_mode,
                )
            )
            transitions.extend(
                unattempted_after_abort_transition_v4(
                    spec=later,
                    selected_mode=selected_mode,
                )
                for later in specs[index + 1 :]
            )
            break
        if attempted_calls() - before != 1:
            raise RuntimeError("v4 Provider transition call accounting differs")
        canonical = turn.canonical_decision
        if canonical is None:
            runtime_admitted = False
            intent_conformant = False
        else:
            result = process_controller_decision_v22(
                session=spec.session,
                raw_decision=canonical,
                turn_input=spec.request.controller_input,
            )
            runtime_admitted = (
                result.disposition is ControllerProtocolDispositionV22.ACCEPTED
            )
            intent_conformant = canonical.decision.value == spec.protocol_intent
        accepted = (
            turn.alias_decision is not None
            and canonical is not None
            and runtime_admitted
            and intent_conformant
        )
        if accepted:
            failure_class = ProviderProtocolFailureClassV4.ACCEPTED
        elif turn.failure_code is not None:
            failure_class = ProviderProtocolFailureClassV4(turn.failure_code.value)
        elif not runtime_admitted:
            failure_class = ProviderProtocolFailureClassV4.RUNTIME_REJECTED
        else:
            failure_class = ProviderProtocolFailureClassV4.INTENT_MISMATCH
        transitions.append(
            completed_transition_v4(
                spec=spec,
                accepted=accepted,
                parsed_alias=turn.alias_decision is not None,
                alias_resolved=canonical is not None,
                runtime_admitted=runtime_admitted,
                intent_conformant=intent_conformant,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                latency_ms=turn.monotonic_latency_ms,
                provider_request_sha256=turn.provider_request_sha256,
                raw_response_sha256=turn.raw_response_sha256,
                failure_class=failure_class,
                selected_mode=selected_mode,
            )
        )
    if len(transitions) != 24:
        raise RuntimeError("v4 replicate did not account for every transition")
    report = build_protocol_replicate_report_v4(
        replicate_id=replicate_id,
        implementation_commit=implementation_commit,
        implementation_tree=implementation_tree,
        manifest_sha256=manifest_sha256,
        probe_report_sha256=probe_report_sha256,
        selected_mode=selected_mode,
        transitions=tuple(transitions),
    )
    if attempted_calls() - start_calls != report.provider_calls:
        raise RuntimeError("v4 replicate Provider call total differs")
    return report


__all__ = (
    "ProviderProtocolFailureClassV4",
    "ProviderProtocolReplicateReportV4",
    "ProviderProtocolReplicateTerminalV4",
    "ProviderProtocolTransitionV4",
    "ProviderTransitionStatusV4",
    "ProviderTransportReasonV4",
    "build_protocol_replicate_report_v4",
    "completed_transition_v4",
    "transport_abort_transition_v4",
    "unattempted_after_abort_transition_v4",
    "run_protocol_replicate_v4",
)
