from __future__ import annotations

import json

import pytest

from ecomsre.dta_v2.v22.controller_contracts import ControllerDecisionV22
from ecomsre.dta_v2.v22.provider_boundary_v4 import (
    AliasResolutionErrorCodeV4,
    AliasResolutionErrorV4,
    ProviderDecisionAliasV4,
    ProviderExecutionModeV4,
    ProviderBoundaryRequestV4,
    build_provider_boundary_request_v4,
    materialize_protocol_requests_v4,
    resolve_provider_alias_decision_v4,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    SyntheticTransitionCategoryV22,
    _setup_transition_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v22.controller_modes import (
    ProviderProbeStatusV22,
    probe_provider_output_mode_v22,
)


def _frozen_probe():
    return probe_provider_output_mode_v22(
        probe=lambda *_args: ProviderProbeStatusV22.SUPPORTED
    )


def _read_request():
    setup = _setup_transition_v22(
        ordinal=1,
        category=SyntheticTransitionCategoryV22.VALID_READ,
        probe=_frozen_probe(),
        arm_override=ControllerArmV22.FLAT_CANONICAL,
    )
    request = build_provider_boundary_request_v4(
        request_kind="TRANSITION",
        execution_mode=ProviderExecutionModeV4.PROTOCOL_CONFORMANCE_ONLY,
        replicate_id="A",
        transition_ordinal=1,
        protocol_intent="READ",
        identity=setup.request.identity,
        controller_input=setup.request.controller_input,
        plan_correction=setup.request.plan_correction,
    )
    return setup, request


def test_alias_binding_is_canonical_and_resolves_exact_internal_decision() -> None:
    _setup, request = _read_request()
    binding = request.alias_binding
    assert tuple(item.alias for item in binding.hypotheses) == tuple(
        f"H{index:02d}" for index in range(len(binding.hypotheses))
    )
    assert tuple(item.alias for item in binding.actions) == tuple(
        f"A{index:02d}" for index in range(len(binding.actions))
    )
    assert tuple(item.alias for item in binding.evidence) == tuple(
        f"E{index:02d}" for index in range(len(binding.evidence))
    )

    decision = ProviderDecisionAliasV4(
        decision="READ",
        hypothesis_alias=next(
            item.alias
            for item in binding.hypotheses
            if item.canonical_id == "h:payment:configuration-error"
        ),
        action_alias=binding.actions[0].alias,
        support_aliases=(),
        contradict_aliases=(),
    )
    resolved = resolve_provider_alias_decision_v4(
        alias_decision=decision,
        binding=binding,
    )
    assert isinstance(resolved, ControllerDecisionV22)
    assert resolved.working_hypothesis_id == "h:payment:configuration-error"
    assert resolved.action_id == binding.actions[0].canonical_id
    assert "H00" not in resolved.model_dump_json()


def test_projection_and_dynamic_schema_expose_only_bounded_alias_state() -> None:
    _setup, request = _read_request()
    visible = request.visible_state()
    encoded = json.dumps(visible, sort_keys=True, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) <= 12_000
    assert visible["execution_mode"] == "PROTOCOL_CONFORMANCE_ONLY"
    assert visible["protocol_intent"] == "READ"
    assert request.dynamic_schema["properties"]["decision"]["enum"] == ["READ"]
    assert request.dynamic_schema["properties"]["action_alias"]["enum"] == [
        item.alias for item in request.alias_binding.actions if item.available
    ]
    for forbidden in (
        "run_id",
        "turn_ordinal",
        "identity_sha256",
        "prompt_sha256",
        "catalog_sha256",
        "policy_sha256",
        "memory_sha256",
        "request_sha256",
        "outcome_sha256",
        "revision_digest",
        "private",
        "http://",
        "https://",
    ):
        assert forbidden not in encoded
    for entry in request.alias_binding.hypotheses:
        assert entry.canonical_id not in encoded
    for entry in request.alias_binding.actions:
        assert entry.canonical_id not in encoded
    for entry in request.alias_binding.evidence:
        assert entry.canonical_id not in encoded


def test_protocol_intent_is_forbidden_outside_conformance_mode() -> None:
    setup, _request = _read_request()
    with pytest.raises(ValueError, match="protocol intent"):
        build_provider_boundary_request_v4(
            request_kind="TRANSITION",
            execution_mode=ProviderExecutionModeV4.SEMANTIC_EVALUATION,
            replicate_id="A",
            transition_ordinal=1,
            protocol_intent="READ",
            identity=setup.request.identity,
            controller_input=setup.request.controller_input,
            plan_correction=setup.request.plan_correction,
        )


def test_semantic_projection_structurally_omits_protocol_intent() -> None:
    setup, _request = _read_request()
    request = build_provider_boundary_request_v4(
        request_kind="TRANSITION",
        execution_mode=ProviderExecutionModeV4.SEMANTIC_EVALUATION,
        replicate_id="A",
        transition_ordinal=1,
        protocol_intent=None,
        identity=setup.request.identity,
        controller_input=setup.request.controller_input,
        plan_correction=setup.request.plan_correction,
    )
    assert "protocol_intent" not in request.visible_state()
    assert request.visible_state()["allowed_decisions"] == [
        "READ",
        "COMMIT",
        "NO_INCIDENT",
        "ABSTAIN",
    ]


def test_rehashed_projection_forgery_is_rejected_against_canonical_input() -> None:
    _setup, request = _read_request()
    projection = {**request.projection, "candidate_services": ["forged"]}
    payload = {
        key: value
        for key, value in request.__dict__.items()
        if key != "request_sha256"
    }
    payload["projection"] = projection
    payload["projection_sha256"] = semantic_sha256_v22(projection)
    draft = ProviderBoundaryRequestV4.model_construct(
        **payload,
        request_sha256="0" * 64,
    )
    payload["request_sha256"] = semantic_sha256_v22(
        draft.model_dump(mode="json", exclude={"request_sha256"})
    )
    with pytest.raises(ValueError, match="boundary differs from canonical input"):
        ProviderBoundaryRequestV4.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("hypothesis_alias", "H99", AliasResolutionErrorCodeV4.UNKNOWN_ALIAS),
        ("hypothesis_alias", "A00", AliasResolutionErrorCodeV4.WRONG_KIND_ALIAS),
        ("action_alias", "A99", AliasResolutionErrorCodeV4.STALE_ALIAS),
    ),
)
def test_unknown_stale_and_wrong_kind_aliases_fail_before_runtime(
    field: str,
    value: str,
    code: AliasResolutionErrorCodeV4,
) -> None:
    _setup, request = _read_request()
    values = {
        "decision": "READ",
        "hypothesis_alias": request.alias_binding.hypotheses[0].alias,
        "action_alias": request.alias_binding.actions[0].alias,
        "support_aliases": (),
        "contradict_aliases": (),
    }
    values[field] = value
    decision = ProviderDecisionAliasV4.model_validate(values)
    with pytest.raises(AliasResolutionErrorV4) as error:
        resolve_provider_alias_decision_v4(
            alias_decision=decision,
            binding=request.alias_binding,
        )
    assert error.value.code is code


def test_duplicate_evidence_alias_is_a_typed_failure() -> None:
    _setup, request = _read_request()
    evidence = request.alias_binding.evidence[0].alias
    decision = ProviderDecisionAliasV4(
        decision="COMMIT",
        hypothesis_alias=request.alias_binding.hypotheses[0].alias,
        action_alias="NONE",
        support_aliases=(evidence, evidence),
        contradict_aliases=(),
    )
    with pytest.raises(AliasResolutionErrorV4) as error:
        resolve_provider_alias_decision_v4(
            alias_decision=decision,
            binding=request.alias_binding,
        )
    assert error.value.code is AliasResolutionErrorCodeV4.DUPLICATE_ALIAS


def test_protocol_matrices_are_stratified_unique_and_disjoint() -> None:
    requests_a = materialize_protocol_requests_v4(replicate_id="A")
    requests_b = materialize_protocol_requests_v4(replicate_id="B")
    assert len(requests_a) == len(requests_b) == 24
    assert len({item.request.request_sha256 for item in requests_a}) == 24
    assert len({item.request.request_sha256 for item in requests_b}) == 24
    assert {
        item.request.request_sha256 for item in requests_a
    }.isdisjoint({item.request.request_sha256 for item in requests_b})
    for values in (requests_a, requests_b):
        for start in range(0, 24, 4):
            block = values[start : start + 4]
            assert {item.arm for item in block} == set(ControllerArmV22)
            assert len({item.protocol_intent for item in block}) >= 2
            assert (
                sum(
                    item.transition_kind == "CORRECTION_ENVELOPE"
                    for item in block
                )
                <= 1
            )
