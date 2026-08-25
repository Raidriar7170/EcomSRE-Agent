"""One shared A/T selection surface and Provider protocol for DTA v2.2.6."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22
from ecomsre.dta_v2.v22.real_fault_terminalizer_v226 import (
    RealFaultAdmittedTerminalV226,
)


REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226 = (
    "Investigate whether one opaque candidate has a current operational fault and "
    "gather only the evidence needed for a supported terminal. Select exactly one "
    "current A or T alias and one current H focus alias or NONE. A selects one bounded "
    "read-only runtime-owned evidence action. T selects one runtime-admitted terminal. "
    "Use no identifiers outside the supplied aliases. Never construct query parameters, "
    "evidence refs, commands, actions, Runbooks, remediation, or writes. Return only "
    'the JSON object {"selection":"A00 or T00","focus":"H00 or NONE"}.'
)


class RealFaultVisibleActionV226(DtaModelV22):
    alias: str = Field(pattern=r"^A[0-9]{2}$")
    source: EvidenceSourceV22
    target_aliases: tuple[str, ...] = Field(min_length=1, max_length=4)
    weighted_cost: StrictFloat = Field(gt=0, le=10)


class RealFaultVisibleTerminalV226(DtaModelV22):
    alias: str = Field(pattern=r"^T[0-9]{2}$")
    terminal_kind: Literal["CPU_SATURATION", "NO_INCIDENT", "ABSTAIN"]
    root_service_alias: str | None = Field(
        default=None, pattern=r"^svc-[0-9a-f]{10}$"
    )
    mechanism: Literal["CPU_SATURATION"] | None


class RealFaultVisibleFocusV226(DtaModelV22):
    alias: str = Field(pattern=r"^H[0-9]{2}$")
    target_alias: str = Field(pattern=r"^svc-[0-9a-f]{10}$")
    mechanism: str = Field(min_length=1, max_length=64)


class RealFaultSelectionRequestV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.selection-request.v1"]
    output_shape: Literal['{"selection":"A00 or T00","focus":"H00 or NONE"}']
    actions: tuple[RealFaultVisibleActionV226, ...] = Field(max_length=32)
    terminals: tuple[RealFaultVisibleTerminalV226, ...] = Field(max_length=4)
    focuses: tuple[RealFaultVisibleFocusV226, ...] = Field(max_length=32)
    remaining_semantic_actions: StrictInt = Field(ge=0, le=4)
    remaining_target_equivalent_reads: StrictInt = Field(ge=0, le=4)

    @model_validator(mode="after")
    def require_surface(self) -> RealFaultSelectionRequestV226:
        for aliases in (
            tuple(item.alias for item in self.actions),
            tuple(item.alias for item in self.terminals),
            tuple(item.alias for item in self.focuses),
        ):
            if len(aliases) != len(set(aliases)):
                raise ValueError("selection surface aliases are not unique")
        if not self.actions and not self.terminals:
            raise ValueError("selection surface is empty")
        return self


class RealFaultSelectionDecisionV226(DtaModelV22):
    selection: str = Field(pattern=r"^(?:A|T)[0-9]{2}$")
    focus: str = Field(pattern=r"^(?:H[0-9]{2}|NONE)$")


class RealFaultSelectionOutcomeV226(DtaModelV22):
    decision: RealFaultSelectionDecisionV226
    first_pass_protocol_success: StrictBool
    post_repair_protocol_success: StrictBool
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    provider_calls: StrictInt = Field(ge=1, le=3)
    transport_retry_count: StrictInt = Field(ge=0, le=3)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)

    @model_validator(mode="after")
    def require_outcome(self) -> RealFaultSelectionOutcomeV226:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("selection token accounting differs")
        if self.provider_calls != 1 + self.protocol_repairs:
            raise ValueError("selection calls differ from protocol repairs")
        if self.first_pass_protocol_success != (self.protocol_repairs == 0):
            raise ValueError("first-pass selection accounting differs")
        if not self.post_repair_protocol_success:
            raise ValueError("accepted selection did not pass protocol")
        return self


class RealFaultSelectionProviderV226(Protocol):
    def complete_selection(
        self,
        *,
        request: RealFaultSelectionRequestV226,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> RealFaultSelectionOutcomeV226: ...


@dataclass(frozen=True)
class RealFaultSelectionSurfaceV226:
    request: RealFaultSelectionRequestV226
    action_by_alias: dict[str, EvidenceActionV22 | ContrastiveResourceActionV225]
    terminal_by_alias: dict[str, RealFaultAdmittedTerminalV226]
    focus_by_alias: dict[str, str]


def build_real_fault_selection_surface_v226(
    *,
    actions: tuple[EvidenceActionV22 | ContrastiveResourceActionV225, ...],
    terminals: tuple[RealFaultAdmittedTerminalV226, ...],
    focuses: tuple[tuple[str, str, str], ...],
    remaining_semantic_actions: int,
    remaining_target_equivalent_reads: int,
) -> RealFaultSelectionSurfaceV226:
    ordered_actions = tuple(sorted(actions, key=lambda item: item.action_id))
    ordered_terminals = tuple(sorted(terminals, key=lambda item: item.terminal_id))
    ordered_focuses = tuple(sorted(focuses, key=lambda item: item[0]))
    action_by_alias = {
        f"A{index:02d}": action for index, action in enumerate(ordered_actions)
    }
    terminal_by_alias = {
        f"T{index:02d}": terminal for index, terminal in enumerate(ordered_terminals)
    }
    focus_by_alias = {
        f"H{index:02d}": hypothesis_id
        for index, (hypothesis_id, _target, _mechanism) in enumerate(ordered_focuses)
    }
    request = RealFaultSelectionRequestV226(
        schema_version="dta-v226-real-fault.selection-request.v1",
        output_shape='{"selection":"A00 or T00","focus":"H00 or NONE"}',
        actions=tuple(
            RealFaultVisibleActionV226(
                alias=alias,
                source=action.source,
                target_aliases=action.target_services,
                weighted_cost=action.weighted_cost,
            )
            for alias, action in action_by_alias.items()
        ),
        terminals=tuple(
            RealFaultVisibleTerminalV226(
                alias=alias,
                terminal_kind=terminal.terminal_kind.value,
                root_service_alias=terminal.root_service_alias,
                mechanism=terminal.mechanism,
            )
            for alias, terminal in terminal_by_alias.items()
        ),
        focuses=tuple(
            RealFaultVisibleFocusV226(
                alias=alias,
                target_alias=target,
                mechanism=mechanism,
            )
            for alias, (_hypothesis_id, target, mechanism) in zip(
                focus_by_alias,
                ordered_focuses,
                strict=True,
            )
        ),
        remaining_semantic_actions=remaining_semantic_actions,
        remaining_target_equivalent_reads=remaining_target_equivalent_reads,
    )
    return RealFaultSelectionSurfaceV226(
        request=request,
        action_by_alias=action_by_alias,
        terminal_by_alias=terminal_by_alias,
        focus_by_alias=focus_by_alias,
    )


__all__ = (
    "REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226",
    "RealFaultSelectionDecisionV226",
    "RealFaultSelectionOutcomeV226",
    "RealFaultSelectionProviderV226",
    "RealFaultSelectionRequestV226",
    "RealFaultSelectionSurfaceV226",
    "build_real_fault_selection_surface_v226",
)
