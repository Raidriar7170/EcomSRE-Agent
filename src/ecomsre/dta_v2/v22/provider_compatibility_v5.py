"""Minimal, request-bound Provider projection for DTA v2.2 PR-D v5."""

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


class ProviderExecutionModeV5(str, Enum):
    PROTOCOL_CONFORMANCE_ONLY = "PROTOCOL_CONFORMANCE_ONLY"
    SEMANTIC_EVALUATION = "SEMANTIC_EVALUATION"
    DEVELOPMENT = "DEVELOPMENT"
    HELD_OUT = "HELD_OUT"
    LIVE = "LIVE"


ProtocolIntentV5 = Literal["READ", "COMMIT", "NO_INCIDENT", "ABSTAIN"]


class AliasResolutionErrorCodeV5(str, Enum):
    UNKNOWN_ALIAS = "UNKNOWN_ALIAS"
    STALE_ALIAS = "STALE_ALIAS"
    WRONG_KIND_ALIAS = "WRONG_KIND_ALIAS"
    DUPLICATE_ALIAS = "DUPLICATE_ALIAS"
    DECISION_ACTION_MISMATCH = "DECISION_ACTION_MISMATCH"
    HYPOTHESIS_DECISION_MISMATCH = "HYPOTHESIS_DECISION_MISMATCH"
    COMMIT_SUPPORT_REQUIRED = "COMMIT_SUPPORT_REQUIRED"


@dataclass(frozen=True, slots=True)
class AliasResolutionErrorV5(ValueError):
    code: AliasResolutionErrorCodeV5

    def __str__(self) -> str:
        return self.code.value


class ProviderDecisionAliasV5(DtaModelV22):
    decision: Literal["READ", "COMMIT", "NO_INCIDENT", "ABSTAIN"]
    hypothesis_alias: str = Field(pattern=r"^[HAE][0-9]{2}$")
    action_alias: str = Field(pattern=r"^(?:NONE|[HAE][0-9]{2})$")
    support_aliases: tuple[str, ...] = Field(max_length=40)
    contradict_aliases: tuple[str, ...] = Field(max_length=40)


class ProviderHypothesisAliasBindingV5(DtaModelV22):
    alias: str = Field(pattern=r"^H[0-9]{2}$")
    canonical_id: str


class ProviderActionAliasBindingV5(DtaModelV22):
    alias: str = Field(pattern=r"^A[0-9]{2}$")
    canonical_id: str
    available: bool


class ProviderEvidenceAliasBindingV5(DtaModelV22):
    alias: str = Field(pattern=r"^E[0-9]{2}$")
    canonical_id: str


class ProviderAliasBindingV5(DtaModelV22):
    schema_version: Literal["dta-v22.provider-alias-binding.v5"]
    hypotheses: tuple[ProviderHypothesisAliasBindingV5, ...]
    actions: tuple[ProviderActionAliasBindingV5, ...]
    evidence: tuple[ProviderEvidenceAliasBindingV5, ...]
    controller_input_sha256: Sha256V22
    binding_sha256: Sha256V22

    @model_validator(mode="after")
    def require_binding(self) -> ProviderAliasBindingV5:
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


class ProviderCompatibilityRequestV5(DtaModelV22):
    schema_version: Literal["dta-v22.provider-boundary-request.v5"]
    request_kind: Literal["PROBE", "TRANSITION"]
    execution_mode: ProviderExecutionModeV5
    replicate_id: Literal["PROBE", "A", "B"]
    transition_ordinal: int = Field(ge=0, le=24)
    protocol_intent: ProtocolIntentV5 | None
    identity: InstanceOf[ControllerIdentityManifestV22]
    controller_input: InstanceOf[ControllerTurnInputV22]
    plan_correction: InstanceOf[PlanCorrectionV22] | None
    alias_binding: ProviderAliasBindingV5
    projection: dict[str, Any]
    static_schema: dict[str, Any]
    projection_sha256: Sha256V22
    static_schema_sha256: Sha256V22
    request_sha256: Sha256V22

    @model_validator(mode="after")
    def require_request(self) -> ProviderCompatibilityRequestV5:
        if (
            self.execution_mode
            is not ProviderExecutionModeV5.PROTOCOL_CONFORMANCE_ONLY
            and self.protocol_intent is not None
        ):
            raise ValueError("protocol intent is forbidden outside conformance mode")
        if (
            self.execution_mode
            is ProviderExecutionModeV5.PROTOCOL_CONFORMANCE_ONLY
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
        expected_binding = _build_alias_binding_v5(self.controller_input)
        expected_projection = json.loads(
            json.dumps(
                _projection_v5(
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
        expected_schema = STATIC_PROVIDER_ALIAS_SCHEMA_V5
        if (
            self.alias_binding != expected_binding
            or self.projection != expected_projection
            or self.static_schema != expected_schema
        ):
            raise ValueError("v5 Provider boundary differs from canonical input")
        if self.projection_sha256 != semantic_sha256_v22(self.projection):
            raise ValueError("Provider projection digest differs")
        if self.static_schema_sha256 != static_schema_sha256_v5():
            raise ValueError("static Provider schema digest differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("Provider boundary request digest differs")
        return self

    def visible_state(self) -> dict[str, Any]:
        return self.projection


def _build_alias_binding_v5(
    controller_input: ControllerTurnInputV22,
) -> ProviderAliasBindingV5:
    available = {item.action_id for item in controller_input.action_catalog.actions}
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-alias-binding.v5",
        "hypotheses": tuple(
            ProviderHypothesisAliasBindingV5(
                alias=f"H{index:02d}",
                canonical_id=item.hypothesis_id,
            )
            for index, item in enumerate(controller_input.hypothesis_catalog.hypotheses)
        ),
        "actions": tuple(
            ProviderActionAliasBindingV5(
                alias=f"A{index:02d}",
                canonical_id=item.action_id,
                available=item.action_id in available,
            )
            for index, item in enumerate(controller_input.action_catalog.registry_actions)
        ),
        "evidence": tuple(
            ProviderEvidenceAliasBindingV5(
                alias=f"E{index:02d}",
                canonical_id=item.evidence_ref,
            )
            for index, item in enumerate(controller_input.salient_memory.evidence_refs)
        ),
        "controller_input_sha256": controller_input.input_sha256,
    }
    draft = ProviderAliasBindingV5.model_construct(
        **payload,
        binding_sha256="0" * 64,
    )
    return ProviderAliasBindingV5.model_validate(
        {
            **payload,
            "binding_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"binding_sha256"})
            ),
        }
    )


def _public_payload_v5(fact: SalientFactV22) -> dict[str, Any]:
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


def _projection_v5(
    *,
    execution_mode: ProviderExecutionModeV5,
    protocol_intent: str | None,
    controller_input: ControllerTurnInputV22,
    correction: PlanCorrectionV22 | None,
    binding: ProviderAliasBindingV5,
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
                    "signals": _public_payload_v5(fact),
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
        "schema_version": "dta-v22.provider-visible-controller-state.v5",
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
    if execution_mode is ProviderExecutionModeV5.PROTOCOL_CONFORMANCE_ONLY:
        projection["protocol_intent"] = protocol_intent
    return projection


STATIC_PROVIDER_ALIAS_SCHEMA_V5: dict[str, Any] = {
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
        "decision": {
            "type": "string",
            "enum": ["READ", "COMMIT", "NO_INCIDENT", "ABSTAIN"],
        },
        "hypothesis_alias": {"type": "string"},
        "action_alias": {"type": "string"},
        "support_aliases": {"type": "array", "items": {"type": "string"}},
        "contradict_aliases": {"type": "array", "items": {"type": "string"}},
    },
}


def static_schema_sha256_v5() -> str:
    return semantic_sha256_v22(STATIC_PROVIDER_ALIAS_SCHEMA_V5)


def build_provider_compatibility_request_v5(
    *,
    request_kind: Literal["PROBE", "TRANSITION"],
    execution_mode: ProviderExecutionModeV5,
    replicate_id: Literal["PROBE", "A", "B"],
    transition_ordinal: int,
    protocol_intent: ProtocolIntentV5 | None,
    identity: ControllerIdentityManifestV22,
    controller_input: ControllerTurnInputV22,
    plan_correction: PlanCorrectionV22 | None,
) -> ProviderCompatibilityRequestV5:
    if (
        execution_mode is not ProviderExecutionModeV5.PROTOCOL_CONFORMANCE_ONLY
        and protocol_intent is not None
    ):
        raise ValueError("protocol intent is forbidden outside conformance mode")
    binding = _build_alias_binding_v5(controller_input)
    projection = json.loads(
        json.dumps(
            _projection_v5(
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
    schema = json.loads(json.dumps(STATIC_PROVIDER_ALIAS_SCHEMA_V5))
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.provider-boundary-request.v5",
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
        "static_schema": schema,
        "projection_sha256": semantic_sha256_v22(projection),
        "static_schema_sha256": static_schema_sha256_v5(),
    }
    draft = ProviderCompatibilityRequestV5.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    return ProviderCompatibilityRequestV5.model_validate(
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
        raise AliasResolutionErrorV5(AliasResolutionErrorCodeV5.WRONG_KIND_ALIAS)
    selected = next((item for item in values if item.alias == alias), None)
    if selected is None:
        raise AliasResolutionErrorV5(
            AliasResolutionErrorCodeV5.STALE_ALIAS
            if stale_action
            else AliasResolutionErrorCodeV5.UNKNOWN_ALIAS
        )
    if stale_action and not selected.available:
        raise AliasResolutionErrorV5(AliasResolutionErrorCodeV5.STALE_ALIAS)
    return selected.canonical_id


def resolve_provider_alias_decision_v5(
    *,
    alias_decision: ProviderDecisionAliasV5,
    binding: ProviderAliasBindingV5,
) -> ControllerDecisionV22:
    combined = (*alias_decision.support_aliases, *alias_decision.contradict_aliases)
    if len(combined) != len(set(combined)):
        raise AliasResolutionErrorV5(AliasResolutionErrorCodeV5.DUPLICATE_ALIAS)
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
        raise AliasResolutionErrorV5(
            AliasResolutionErrorCodeV5.HYPOTHESIS_DECISION_MISMATCH
        )
    if alias_decision.decision in {"READ", "COMMIT"} and hypothesis_id in {
        NO_INCIDENT_HYPOTHESIS_ID_V22,
        ABSTAIN_HYPOTHESIS_ID_V22,
    }:
        raise AliasResolutionErrorV5(
            AliasResolutionErrorCodeV5.HYPOTHESIS_DECISION_MISMATCH
        )
    if (alias_decision.decision == "READ") != (action_id != NO_ACTION_ID_V22):
        raise AliasResolutionErrorV5(
            AliasResolutionErrorCodeV5.DECISION_ACTION_MISMATCH
        )
    if alias_decision.decision == "COMMIT" and not support:
        raise AliasResolutionErrorV5(
            AliasResolutionErrorCodeV5.COMMIT_SUPPORT_REQUIRED
        )
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22(alias_decision.decision),
        working_hypothesis_id=hypothesis_id,
        action_id=action_id,
        supporting_evidence_refs=support,
        contradicting_evidence_refs=contradict,
    )


@dataclass(frozen=True, slots=True)
class MaterializedProtocolRequestV5:
    transition_id: str
    arm: ControllerArmV22
    protocol_intent: str
    protocol_category: str
    transition_kind: str
    correction_class: str | None
    session: Any
    request: ProviderCompatibilityRequestV5


_MATRIX_V5: dict[str, tuple[tuple[str, ProtocolIntentV5, str], ...]] = {
    "A": (
        ("F", "READ", "STALE"),
        ("P", "COMMIT", "ORDINARY"),
        ("F", "ABSTAIN", "ORDINARY"),
        ("P", "NO_INCIDENT", "ORDINARY"),
        ("F", "COMMIT", "ORDINARY"),
        ("P", "READ", "STALE"),
        ("F", "ABSTAIN", "BUDGET"),
        ("P", "ABSTAIN", "ORDINARY"),
        ("F", "NO_INCIDENT", "ORDINARY"),
        ("P", "READ", "ORDINARY"),
        ("F", "READ", "INVALID_REF"),
        ("P", "ABSTAIN", "BUDGET"),
        ("F", "READ", "ORDINARY"),
        ("P", "COMMIT", "ORDINARY"),
        ("F", "ABSTAIN", "UNAVAILABLE"),
        ("P", "READ", "INVALID_REF"),
        ("F", "COMMIT", "ORDINARY"),
        ("P", "NO_INCIDENT", "ORDINARY"),
        ("F", "NO_INCIDENT", "ORDINARY"),
        ("P", "ABSTAIN", "UNAVAILABLE"),
        ("F", "ABSTAIN", "ORDINARY"),
        ("P", "READ", "ORDINARY"),
        ("F", "READ", "ORDINARY"),
        ("P", "ABSTAIN", "ORDINARY"),
    ),
    "B": (
        ("P", "NO_INCIDENT", "ORDINARY"),
        ("F", "READ", "STALE"),
        ("P", "COMMIT", "ORDINARY"),
        ("F", "NO_INCIDENT", "ORDINARY"),
        ("P", "READ", "ORDINARY"),
        ("F", "ABSTAIN", "ORDINARY"),
        ("P", "READ", "INVALID_REF"),
        ("F", "COMMIT", "ORDINARY"),
        ("P", "ABSTAIN", "ORDINARY"),
        ("F", "ABSTAIN", "BUDGET"),
        ("P", "ABSTAIN", "BUDGET"),
        ("F", "READ", "INVALID_REF"),
        ("P", "COMMIT", "ORDINARY"),
        ("F", "READ", "ORDINARY"),
        ("P", "NO_INCIDENT", "ORDINARY"),
        ("F", "NO_INCIDENT", "ORDINARY"),
        ("P", "READ", "STALE"),
        ("F", "ABSTAIN", "UNAVAILABLE"),
        ("P", "ABSTAIN", "UNAVAILABLE"),
        ("F", "COMMIT", "ORDINARY"),
        ("P", "READ", "ORDINARY"),
        ("F", "READ", "ORDINARY"),
        ("P", "ABSTAIN", "ORDINARY"),
        ("F", "ABSTAIN", "ORDINARY"),
    ),
}


def _require_matrix_v5() -> None:
    expected_per_arm = {
        ("READ", "ORDINARY"): 2,
        ("COMMIT", "ORDINARY"): 2,
        ("NO_INCIDENT", "ORDINARY"): 2,
        ("ABSTAIN", "ORDINARY"): 2,
        ("ABSTAIN", "BUDGET"): 1,
        ("ABSTAIN", "UNAVAILABLE"): 1,
        ("READ", "STALE"): 1,
        ("READ", "INVALID_REF"): 1,
    }
    if set(_MATRIX_V5) != {"A", "B"} or _MATRIX_V5["A"] == _MATRIX_V5["B"]:
        raise ValueError("v5 replicate matrix identity differs")
    for rows in _MATRIX_V5.values():
        if len(rows) != 24:
            raise ValueError("v5 replicate matrix length differs")
        for arm in ("F", "P"):
            arm_rows = tuple((intent, case) for code, intent, case in rows if code == arm)
            observed = {
                key: sum(item == key for item in arm_rows)
                for key in expected_per_arm
            }
            if len(arm_rows) != 12 or observed != expected_per_arm:
                raise ValueError("v5 per-arm matrix composition differs")
        for start in range(21):
            block = rows[start : start + 4]
            if (
                {item[0] for item in block} != {"F", "P"}
                or len({item[1] for item in block}) < 2
                or sum(item[2] in {"STALE", "INVALID_REF"} for item in block) > 1
            ):
                raise ValueError("v5 sliding four-transition matrix rule differs")


def materialize_protocol_requests_v5(
    *, replicate_id: Literal["A", "B"]
) -> tuple[MaterializedProtocolRequestV5, ...]:
    from ecomsre.dta_v2.v22.controller_modes import (
        ProviderProbeStatusV22,
        probe_provider_output_mode_v22,
    )
    from ecomsre.dta_v2.v22.protocol_suite import (
        SyntheticTransitionCategoryV22,
        _setup_transition_v22,
    )

    _require_matrix_v5()
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
        _MATRIX_V5[replicate_id], start=1
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
        request = build_provider_compatibility_request_v5(
            request_kind="TRANSITION",
            execution_mode=ProviderExecutionModeV5.PROTOCOL_CONFORMANCE_ONLY,
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
            MaterializedProtocolRequestV5(
                transition_id=f"dta-v22-v5-{replicate_id.lower()}-{ordinal:02d}",
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


def build_provider_probe_request_v5() -> ProviderCompatibilityRequestV5:
    """Build the one preregistered probe against the actual v5 alias schema."""

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
    return build_provider_compatibility_request_v5(
        request_kind="PROBE",
        execution_mode=ProviderExecutionModeV5.PROTOCOL_CONFORMANCE_ONLY,
        replicate_id="PROBE",
        transition_ordinal=0,
        protocol_intent="ABSTAIN",
        identity=setup.request.identity,
        controller_input=setup.request.controller_input,
        plan_correction=setup.request.plan_correction,
    )


__all__ = (
    "AliasResolutionErrorCodeV5",
    "AliasResolutionErrorV5",
    "MaterializedProtocolRequestV5",
    "ProviderAliasBindingV5",
    "ProviderCompatibilityRequestV5",
    "ProviderDecisionAliasV5",
    "ProviderExecutionModeV5",
    "STATIC_PROVIDER_ALIAS_SCHEMA_V5",
    "build_provider_compatibility_request_v5",
    "build_provider_probe_request_v5",
    "materialize_protocol_requests_v5",
    "resolve_provider_alias_decision_v5",
    "static_schema_sha256_v5",
)
