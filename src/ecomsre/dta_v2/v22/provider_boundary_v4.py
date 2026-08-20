"""Minimal, request-bound Provider projection for DTA v2.2 PR-D v4."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Literal

from pydantic import Field, InstanceOf, model_validator

from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerTurnInputV22,
)
from ecomsre.dta_v2.v22.controller_modes import ControllerIdentityManifestV22
from ecomsre.dta_v2.v22.controller_runtime import PlanCorrectionV22
from ecomsre.dta_v2.v22.controller_provider import build_provider_turn_request_v22
from ecomsre.dta_v2.v22.memory import SalientFactV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    Sha256V22,
    semantic_sha256_v22,
)


class ProviderExecutionModeV4(str, Enum):
    PROTOCOL_CONFORMANCE_ONLY = "PROTOCOL_CONFORMANCE_ONLY"
    SEMANTIC_EVALUATION = "SEMANTIC_EVALUATION"
    DEVELOPMENT = "DEVELOPMENT"
    HELD_OUT = "HELD_OUT"
    LIVE = "LIVE"


ProtocolIntentV4 = Literal["READ", "COMMIT", "NO_INCIDENT", "ABSTAIN"]


class AliasResolutionErrorCodeV4(str, Enum):
    UNKNOWN_ALIAS = "UNKNOWN_ALIAS"
    STALE_ALIAS = "STALE_ALIAS"
    WRONG_KIND_ALIAS = "WRONG_KIND_ALIAS"
    DUPLICATE_ALIAS = "DUPLICATE_ALIAS"


@dataclass(frozen=True, slots=True)
class AliasResolutionErrorV4(ValueError):
    code: AliasResolutionErrorCodeV4

    def __str__(self) -> str:
        return self.code.value


class ProviderDecisionAliasV4(DtaModelV22):
    decision: Literal["READ", "COMMIT", "NO_INCIDENT", "ABSTAIN"]
    hypothesis_alias: str = Field(pattern=r"^[HAE][0-9]{2}$")
    action_alias: str = Field(pattern=r"^(?:NONE|[HAE][0-9]{2})$")
    support_aliases: tuple[str, ...] = Field(max_length=40)
    contradict_aliases: tuple[str, ...] = Field(max_length=40)


class ProviderHypothesisAliasBindingV4(DtaModelV22):
    alias: str = Field(pattern=r"^H[0-9]{2}$")
    canonical_id: str


class ProviderActionAliasBindingV4(DtaModelV22):
    alias: str = Field(pattern=r"^A[0-9]{2}$")
    canonical_id: str
    available: bool


class ProviderEvidenceAliasBindingV4(DtaModelV22):
    alias: str = Field(pattern=r"^E[0-9]{2}$")
    canonical_id: str


class ProviderAliasBindingV4(DtaModelV22):
    schema_version: Literal["dta-v22.provider-alias-binding.v4"]
    hypotheses: tuple[ProviderHypothesisAliasBindingV4, ...]
    actions: tuple[ProviderActionAliasBindingV4, ...]
    evidence: tuple[ProviderEvidenceAliasBindingV4, ...]
    controller_input_sha256: Sha256V22
    binding_sha256: Sha256V22

    @model_validator(mode="after")
    def require_binding(self) -> ProviderAliasBindingV4:
        for prefix, values in (
            ("H", self.hypotheses),
            ("A", self.actions),
            ("E", self.evidence),
        ):
            if tuple(item.alias for item in values) != tuple(
                f"{prefix}{index:02d}" for index in range(len(values))
            ):
                raise ValueError("Provider aliases are not canonical")
            ids = tuple(item.canonical_id for item in values)
            if len(ids) != len(set(ids)):
                raise ValueError("Provider alias binding contains duplicate IDs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("Provider alias binding digest differs")
        return self


class ProviderBoundaryRequestV4(DtaModelV22):
    schema_version: Literal["dta-v22.provider-boundary-request.v4"]
    request_kind: Literal["PROBE", "TRANSITION"]
    execution_mode: ProviderExecutionModeV4
    replicate_id: Literal["PROBE", "A", "B"]
    transition_ordinal: int = Field(ge=0, le=24)
    protocol_intent: ProtocolIntentV4 | None
    identity: InstanceOf[ControllerIdentityManifestV22]
    controller_input: InstanceOf[ControllerTurnInputV22]
    plan_correction: InstanceOf[PlanCorrectionV22] | None
    alias_binding: ProviderAliasBindingV4
    projection: dict[str, Any]
    dynamic_schema: dict[str, Any]
    projection_sha256: Sha256V22
    schema_sha256: Sha256V22
    request_sha256: Sha256V22

    @model_validator(mode="after")
    def require_request(self) -> ProviderBoundaryRequestV4:
        if (
            self.execution_mode
            is not ProviderExecutionModeV4.PROTOCOL_CONFORMANCE_ONLY
            and self.protocol_intent is not None
        ):
            raise ValueError("protocol intent is forbidden outside conformance mode")
        if (
            self.execution_mode
            is ProviderExecutionModeV4.PROTOCOL_CONFORMANCE_ONLY
            and self.protocol_intent is None
        ):
            raise ValueError("protocol transition requires a protocol intent")
        if self.alias_binding.controller_input_sha256 != self.controller_input.input_sha256:
            raise ValueError("alias binding differs from controller input")
        build_provider_turn_request_v22(
            execution_mode="PROTOCOL_ONLY",
            identity=self.identity,
            controller_input=self.controller_input,
            plan_correction=self.plan_correction,
        )
        expected_binding = _build_alias_binding_v4(self.controller_input)
        expected_projection = json.loads(
            json.dumps(
                _projection_v4(
                    execution_mode=self.execution_mode,
                    protocol_intent=self.protocol_intent,
                    controller_input=self.controller_input,
                    correction=self.plan_correction,
                    binding=expected_binding,
                ),
                allow_nan=False,
                ensure_ascii=False,
            )
        )
        expected_schema = _dynamic_schema_v4(
            intent=self.protocol_intent,
            binding=expected_binding,
        )
        if (
            self.alias_binding != expected_binding
            or self.projection != expected_projection
            or self.dynamic_schema != expected_schema
        ):
            raise ValueError("v4 Provider boundary differs from canonical input")
        if self.projection_sha256 != semantic_sha256_v22(self.projection):
            raise ValueError("Provider projection digest differs")
        if self.schema_sha256 != semantic_sha256_v22(self.dynamic_schema):
            raise ValueError("dynamic Provider schema digest differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("Provider boundary request digest differs")
        return self

    def visible_state(self) -> dict[str, Any]:
        return self.projection


def _build_alias_binding_v4(
    controller_input: ControllerTurnInputV22,
) -> ProviderAliasBindingV4:
    available = {item.action_id for item in controller_input.action_catalog.actions}
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-alias-binding.v4",
        "hypotheses": tuple(
            ProviderHypothesisAliasBindingV4(
                alias=f"H{index:02d}",
                canonical_id=item.hypothesis_id,
            )
            for index, item in enumerate(controller_input.hypothesis_catalog.hypotheses)
        ),
        "actions": tuple(
            ProviderActionAliasBindingV4(
                alias=f"A{index:02d}",
                canonical_id=item.action_id,
                available=item.action_id in available,
            )
            for index, item in enumerate(controller_input.action_catalog.registry_actions)
        ),
        "evidence": tuple(
            ProviderEvidenceAliasBindingV4(
                alias=f"E{index:02d}",
                canonical_id=item.evidence_ref,
            )
            for index, item in enumerate(controller_input.salient_memory.evidence_refs)
        ),
        "controller_input_sha256": controller_input.input_sha256,
    }
    draft = ProviderAliasBindingV4.model_construct(
        **payload,
        binding_sha256="0" * 64,
    )
    return ProviderAliasBindingV4.model_validate(
        {
            **payload,
            "binding_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"binding_sha256"})
            ),
        }
    )


def _public_payload_v4(fact: SalientFactV22) -> dict[str, Any]:
    payload = fact.payload.model_dump(mode="json")
    return {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "schema_version",
            "endpoint",
            "normalized_template",
            "revision_digest",
        }
    }


def _projection_v4(
    *,
    execution_mode: ProviderExecutionModeV4,
    protocol_intent: str | None,
    controller_input: ControllerTurnInputV22,
    correction: PlanCorrectionV22 | None,
    binding: ProviderAliasBindingV4,
) -> dict[str, Any]:
    hypothesis_alias = {
        item.canonical_id: item.alias for item in binding.hypotheses
    }
    action_alias = {item.canonical_id: item.alias for item in binding.actions}
    evidence_alias = {item.canonical_id: item.alias for item in binding.evidence}
    facts = []
    for fact in controller_input.salient_memory.salient_facts:
        aliases = tuple(
            evidence_alias[ref]
            for ref in fact.evidence_refs
            if ref in evidence_alias
        )
        if aliases:
            facts.append(
                {
                    "source": fact.source.value,
                    "service": fact.service,
                    "evidence_aliases": aliases,
                    "signal_strength": fact.signal_strength.value,
                    "signals": _public_payload_v4(fact),
                }
            )
    planner_state: dict[str, Any] | None = None
    if controller_input.belief_ledger_view is not None:
        view = controller_input.belief_ledger_view
        planner_state = {
            "working_hypothesis_alias": (
                None
                if view.current_working_hypothesis_id is None
                else hypothesis_alias[view.current_working_hypothesis_id]
            ),
            "beliefs": tuple(
                {
                    "hypothesis_alias": hypothesis_alias[item.hypothesis_id],
                    "status": item.status.value,
                    "support_aliases": tuple(
                        evidence_alias[ref]
                        for ref in item.supporting_evidence_refs
                        if ref in evidence_alias
                    ),
                    "contradict_aliases": tuple(
                        evidence_alias[ref]
                        for ref in item.contradicting_evidence_refs
                        if ref in evidence_alias
                    ),
                }
                for item in view.hypotheses
            ),
        }
    projection = {
        "schema_version": "dta-v22.provider-visible-controller-state.v4",
        "execution_mode": execution_mode.value,
        "allowed_decisions": (
            (protocol_intent,)
            if protocol_intent is not None
            else ("READ", "COMMIT", "NO_INCIDENT", "ABSTAIN")
        ),
        "arm": controller_input.arm.value,
        "candidate_services": controller_input.bootstrap.candidate_services,
        "hypotheses": tuple(
            {
                "alias": item.alias,
                "target_service": hypothesis.target_service,
                "fault_domain": hypothesis.fault_domain.value,
                "mechanism": hypothesis.mechanism.value,
            }
            for item, hypothesis in zip(
                binding.hypotheses,
                controller_input.hypothesis_catalog.hypotheses,
                strict=True,
            )
        ),
        "actions": tuple(
            {
                "alias": action_alias[action.action_id],
                "source": action.source.value,
                "target_services": action.target_services,
                "weighted_cost": action.weighted_cost,
            }
            for action in controller_input.action_catalog.actions
        ),
        "evidence": tuple(facts),
        "budget": {
            "remaining_evidence": controller_input.runtime_context.remaining_evidence_budget,
            "remaining_provider_turns": controller_input.runtime_context.remaining_provider_turns,
        },
        "planner_state": planner_state,
        "correction": (
            None
            if correction is None
            else {
                "safe_error_code": correction.safe_error_code.value,
                "current_valid_action_aliases": tuple(
                    action_alias[action_id]
                    for action_id in correction.current_valid_action_ids
                ),
                "remaining_evidence_budget": correction.remaining_evidence_budget,
            }
        ),
    }
    if execution_mode is ProviderExecutionModeV4.PROTOCOL_CONFORMANCE_ONLY:
        projection["protocol_intent"] = protocol_intent
    return projection


def _dynamic_schema_v4(
    *,
    intent: ProtocolIntentV4 | None,
    binding: ProviderAliasBindingV4,
) -> dict[str, Any]:
    all_hypotheses = [item.alias for item in binding.hypotheses]
    incident_hypotheses = [
        item.alias
        for item in binding.hypotheses
        if item.canonical_id
        not in {NO_INCIDENT_HYPOTHESIS_ID_V22, ABSTAIN_HYPOTHESIS_ID_V22}
    ]
    if intent in {"READ", "COMMIT"}:
        hypothesis_aliases = incident_hypotheses
    elif intent == "NO_INCIDENT":
        hypothesis_aliases = [
            item.alias
            for item in binding.hypotheses
            if item.canonical_id == NO_INCIDENT_HYPOTHESIS_ID_V22
        ]
    elif intent == "ABSTAIN":
        hypothesis_aliases = [
            item.alias
            for item in binding.hypotheses
            if item.canonical_id == ABSTAIN_HYPOTHESIS_ID_V22
        ]
    else:
        hypothesis_aliases = all_hypotheses
    action_aliases = [item.alias for item in binding.actions if item.available]
    evidence_aliases = [item.alias for item in binding.evidence]
    action_enum = (
        action_aliases
        if intent == "READ"
        else [NO_ACTION_ID_V22]
        if intent is not None
        else [NO_ACTION_ID_V22, *action_aliases]
    )
    decision_enum = (
        [intent]
        if intent is not None
        else ["READ", "COMMIT", "NO_INCIDENT", "ABSTAIN"]
    )
    support_schema: dict[str, Any] = {
        "type": "array",
        "items": {"type": "string", "enum": evidence_aliases},
        "uniqueItems": True,
        "maxItems": 40,
    }
    if intent == "COMMIT":
        support_schema["minItems"] = 1
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "decision",
            "hypothesis_alias",
            "action_alias",
            "support_aliases",
            "contradict_aliases",
        ],
        "properties": {
            "decision": {"type": "string", "enum": decision_enum},
            "hypothesis_alias": {"type": "string", "enum": hypothesis_aliases},
            "action_alias": {"type": "string", "enum": action_enum},
            "support_aliases": support_schema,
            "contradict_aliases": {
                "type": "array",
                "items": {"type": "string", "enum": evidence_aliases},
                "uniqueItems": True,
                "maxItems": 40,
            },
        },
    }


def build_provider_boundary_request_v4(
    *,
    request_kind: Literal["PROBE", "TRANSITION"],
    execution_mode: ProviderExecutionModeV4,
    replicate_id: Literal["PROBE", "A", "B"],
    transition_ordinal: int,
    protocol_intent: ProtocolIntentV4 | None,
    identity: ControllerIdentityManifestV22,
    controller_input: ControllerTurnInputV22,
    plan_correction: PlanCorrectionV22 | None,
) -> ProviderBoundaryRequestV4:
    if (
        execution_mode is not ProviderExecutionModeV4.PROTOCOL_CONFORMANCE_ONLY
        and protocol_intent is not None
    ):
        raise ValueError("protocol intent is forbidden outside conformance mode")
    binding = _build_alias_binding_v4(controller_input)
    projection = json.loads(
        json.dumps(
            _projection_v4(
                execution_mode=execution_mode,
                protocol_intent=protocol_intent,
                controller_input=controller_input,
                correction=plan_correction,
                binding=binding,
            ),
            allow_nan=False,
            ensure_ascii=False,
        )
    )
    schema = _dynamic_schema_v4(intent=protocol_intent, binding=binding)
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-boundary-request.v4",
        "request_kind": request_kind,
        "execution_mode": execution_mode,
        "replicate_id": replicate_id,
        "transition_ordinal": transition_ordinal,
        "protocol_intent": protocol_intent,
        "identity": identity,
        "controller_input": controller_input,
        "plan_correction": plan_correction,
        "alias_binding": binding,
        "projection": projection,
        "dynamic_schema": schema,
        "projection_sha256": semantic_sha256_v22(projection),
        "schema_sha256": semantic_sha256_v22(schema),
    }
    draft = ProviderBoundaryRequestV4.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return ProviderBoundaryRequestV4.model_validate(
        {
            **payload,
            "request_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"request_sha256"})
            ),
        }
    )


def _alias_kind(alias: str) -> str | None:
    return alias[0] if len(alias) == 3 and alias[1:].isdigit() else None


def _resolve(
    *,
    alias: str,
    expected_kind: str,
    values: tuple[Any, ...],
    stale_action: bool = False,
) -> str:
    kind = _alias_kind(alias)
    if kind != expected_kind:
        raise AliasResolutionErrorV4(AliasResolutionErrorCodeV4.WRONG_KIND_ALIAS)
    selected = next((item for item in values if item.alias == alias), None)
    if selected is None:
        raise AliasResolutionErrorV4(
            AliasResolutionErrorCodeV4.STALE_ALIAS
            if stale_action
            else AliasResolutionErrorCodeV4.UNKNOWN_ALIAS
        )
    if stale_action and not selected.available:
        raise AliasResolutionErrorV4(AliasResolutionErrorCodeV4.STALE_ALIAS)
    return selected.canonical_id


def resolve_provider_alias_decision_v4(
    *,
    alias_decision: ProviderDecisionAliasV4,
    binding: ProviderAliasBindingV4,
) -> ControllerDecisionV22:
    combined = (*alias_decision.support_aliases, *alias_decision.contradict_aliases)
    if len(combined) != len(set(combined)):
        raise AliasResolutionErrorV4(AliasResolutionErrorCodeV4.DUPLICATE_ALIAS)
    hypothesis_id = _resolve(
        alias=alias_decision.hypothesis_alias,
        expected_kind="H",
        values=binding.hypotheses,
    )
    if alias_decision.action_alias == NO_ACTION_ID_V22:
        action_id = NO_ACTION_ID_V22
    else:
        action_id = _resolve(
            alias=alias_decision.action_alias,
            expected_kind="A",
            values=binding.actions,
            stale_action=True,
        )
    support = tuple(
        sorted(
            _resolve(
                alias=alias,
                expected_kind="E",
                values=binding.evidence,
            )
            for alias in alias_decision.support_aliases
        )
    )
    contradict = tuple(
        sorted(
            _resolve(
                alias=alias,
                expected_kind="E",
                values=binding.evidence,
            )
            for alias in alias_decision.contradict_aliases
        )
    )
    sentinel = {
        "NO_INCIDENT": NO_INCIDENT_HYPOTHESIS_ID_V22,
        "ABSTAIN": ABSTAIN_HYPOTHESIS_ID_V22,
    }.get(alias_decision.decision)
    if sentinel is not None and hypothesis_id != sentinel:
        raise AliasResolutionErrorV4(AliasResolutionErrorCodeV4.WRONG_KIND_ALIAS)
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22(alias_decision.decision),
        working_hypothesis_id=hypothesis_id,
        action_id=action_id,
        supporting_evidence_refs=support,
        contradicting_evidence_refs=contradict,
    )


@dataclass(frozen=True, slots=True)
class MaterializedProtocolRequestV4:
    transition_id: str
    arm: ControllerArmV22
    protocol_intent: str
    protocol_category: str
    transition_kind: str
    correction_class: str | None
    session: Any
    request: ProviderBoundaryRequestV4


_MATRIX_V4: dict[str, tuple[tuple[str, ProtocolIntentV4, str], ...]] = {
    "A": (
        ("F", "READ", "STALE"), ("F", "ABSTAIN", "ORDINARY"), ("P", "COMMIT", "ORDINARY"), ("P", "NO_INCIDENT", "ORDINARY"),
        ("P", "READ", "STALE"), ("P", "ABSTAIN", "ORDINARY"), ("F", "COMMIT", "ORDINARY"), ("F", "NO_INCIDENT", "ORDINARY"),
        ("F", "READ", "INVALID_REF"), ("F", "ABSTAIN", "ORDINARY"), ("P", "COMMIT", "ORDINARY"), ("P", "NO_INCIDENT", "ORDINARY"),
        ("P", "READ", "INVALID_REF"), ("P", "ABSTAIN", "ORDINARY"), ("F", "COMMIT", "ORDINARY"), ("F", "NO_INCIDENT", "ORDINARY"),
        ("F", "READ", "ORDINARY"), ("F", "ABSTAIN", "BUDGET"), ("P", "READ", "ORDINARY"), ("P", "ABSTAIN", "UNAVAILABLE"),
        ("F", "READ", "ORDINARY"), ("F", "ABSTAIN", "UNAVAILABLE"), ("P", "READ", "ORDINARY"), ("P", "ABSTAIN", "BUDGET"),
    ),
    "B": (
        ("F", "READ", "STALE"), ("F", "NO_INCIDENT", "ORDINARY"), ("P", "ABSTAIN", "ORDINARY"), ("P", "COMMIT", "ORDINARY"),
        ("P", "READ", "INVALID_REF"), ("P", "NO_INCIDENT", "ORDINARY"), ("F", "ABSTAIN", "ORDINARY"), ("F", "COMMIT", "ORDINARY"),
        ("F", "READ", "INVALID_REF"), ("F", "NO_INCIDENT", "ORDINARY"), ("P", "ABSTAIN", "ORDINARY"), ("P", "COMMIT", "ORDINARY"),
        ("P", "READ", "STALE"), ("P", "NO_INCIDENT", "ORDINARY"), ("F", "ABSTAIN", "ORDINARY"), ("F", "COMMIT", "ORDINARY"),
        ("F", "READ", "ORDINARY"), ("F", "ABSTAIN", "UNAVAILABLE"), ("P", "READ", "ORDINARY"), ("P", "ABSTAIN", "BUDGET"),
        ("F", "READ", "ORDINARY"), ("F", "ABSTAIN", "BUDGET"), ("P", "READ", "ORDINARY"), ("P", "ABSTAIN", "UNAVAILABLE"),
    ),
}


def materialize_protocol_requests_v4(
    *, replicate_id: Literal["A", "B"]
) -> tuple[MaterializedProtocolRequestV4, ...]:
    from ecomsre.dta_v2.v22.controller_modes import (
        ProviderProbeStatusV22,
        probe_provider_output_mode_v22,
    )
    from ecomsre.dta_v2.v22.protocol_suite import (
        SyntheticTransitionCategoryV22,
        _setup_transition_v22,
    )

    probe = probe_provider_output_mode_v22(
        probe=lambda *_args: ProviderProbeStatusV22.SUPPORTED
    )
    category_by_spec = {
        ("READ", "ORDINARY"): SyntheticTransitionCategoryV22.VALID_READ,
        ("COMMIT", "ORDINARY"): SyntheticTransitionCategoryV22.VALID_COMMIT,
        ("NO_INCIDENT", "ORDINARY"): SyntheticTransitionCategoryV22.VALID_NO_INCIDENT,
        ("ABSTAIN", "ORDINARY"): SyntheticTransitionCategoryV22.VALID_ABSTAIN,
        ("ABSTAIN", "BUDGET"): SyntheticTransitionCategoryV22.BUDGET_EXHAUSTION,
        ("ABSTAIN", "UNAVAILABLE"): SyntheticTransitionCategoryV22.UNAVAILABLE_SOURCE,
        ("READ", "STALE"): SyntheticTransitionCategoryV22.STALE_ACTION_CORRECTION,
        ("READ", "INVALID_REF"): SyntheticTransitionCategoryV22.INVALID_REF_CORRECTION,
    }
    materialized = []
    for ordinal, (arm_code, intent, case) in enumerate(
        _MATRIX_V4[replicate_id], start=1
    ):
        arm = (
            ControllerArmV22.FLAT_CANONICAL
            if arm_code == "F"
            else ControllerArmV22.PLANNER_LITE
        )
        setup = _setup_transition_v22(
            ordinal=ordinal,
            category=category_by_spec[(intent, case)],
            probe=probe,
            arm_override=arm,
        )
        request = build_provider_boundary_request_v4(
            request_kind="TRANSITION",
            execution_mode=ProviderExecutionModeV4.PROTOCOL_CONFORMANCE_ONLY,
            replicate_id=replicate_id,
            transition_ordinal=ordinal,
            protocol_intent=intent,
            identity=setup.request.identity,
            controller_input=setup.request.controller_input,
            plan_correction=setup.request.plan_correction,
        )
        correction = case if case in {"STALE", "INVALID_REF"} else None
        category_name = {
            ("READ", "ORDINARY"): "READ",
            ("COMMIT", "ORDINARY"): "COMMIT",
            ("NO_INCIDENT", "ORDINARY"): "NO_INCIDENT",
            ("ABSTAIN", "ORDINARY"): "ABSTAIN",
            ("ABSTAIN", "BUDGET"): "BUDGET_EXHAUSTED",
            ("ABSTAIN", "UNAVAILABLE"): "SOURCE_UNAVAILABLE",
            ("READ", "STALE"): "STALE_ACTION_CORRECTION",
            ("READ", "INVALID_REF"): "INVALID_REF_CORRECTION",
        }[(intent, case)]
        materialized.append(
            MaterializedProtocolRequestV4(
                transition_id=f"dta-v22-v4-{replicate_id.lower()}-{ordinal:02d}",
                arm=arm,
                protocol_intent=intent,
                protocol_category=category_name,
                transition_kind=(
                    "CORRECTION_ENVELOPE" if correction else "ORDINARY"
                ),
                correction_class=correction,
                session=setup.session,
                request=request,
            )
        )
    return tuple(materialized)


def build_provider_probe_request_v4() -> ProviderBoundaryRequestV4:
    """Build the one preregistered probe against the actual v4 alias schema."""

    from ecomsre.dta_v2.v22.controller_modes import (
        ProviderProbeStatusV22,
        probe_provider_output_mode_v22,
    )
    from ecomsre.dta_v2.v22.protocol_suite import (
        SyntheticTransitionCategoryV22,
        _setup_transition_v22,
    )

    probe = probe_provider_output_mode_v22(
        probe=lambda *_args: ProviderProbeStatusV22.SUPPORTED
    )
    setup = _setup_transition_v22(
        ordinal=1,
        category=SyntheticTransitionCategoryV22.VALID_ABSTAIN,
        probe=probe,
        arm_override=ControllerArmV22.FLAT_CANONICAL,
    )
    return build_provider_boundary_request_v4(
        request_kind="PROBE",
        execution_mode=ProviderExecutionModeV4.PROTOCOL_CONFORMANCE_ONLY,
        replicate_id="PROBE",
        transition_ordinal=0,
        protocol_intent="ABSTAIN",
        identity=setup.request.identity,
        controller_input=setup.request.controller_input,
        plan_correction=setup.request.plan_correction,
    )


__all__ = (
    "AliasResolutionErrorCodeV4",
    "AliasResolutionErrorV4",
    "MaterializedProtocolRequestV4",
    "ProviderAliasBindingV4",
    "ProviderBoundaryRequestV4",
    "ProviderDecisionAliasV4",
    "ProviderExecutionModeV4",
    "build_provider_boundary_request_v4",
    "build_provider_probe_request_v4",
    "materialize_protocol_requests_v4",
    "resolve_provider_alias_decision_v4",
)
