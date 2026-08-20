from __future__ import annotations

import json

import pytest

from ecomsre.dta_v2.v22.controller_contracts import ControllerDecisionV22
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.controller_modes import (
    ProviderProbeStatusV22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    SyntheticTransitionCategoryV22,
    _setup_transition_v22,
)
from ecomsre.dta_v2.v22.provider_compatibility_v5 import (
    AliasResolutionErrorCodeV5,
    AliasResolutionErrorV5,
    ProviderDecisionAliasV5,
    ProviderExecutionModeV5,
    STATIC_PROVIDER_ALIAS_SCHEMA_V5,
    build_provider_compatibility_request_v5,
    materialize_protocol_requests_v5,
    resolve_provider_alias_decision_v5,
    static_schema_sha256_v5,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


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
    request = build_provider_compatibility_request_v5(
        request_kind="TRANSITION",
        execution_mode=ProviderExecutionModeV5.PROTOCOL_CONFORMANCE_ONLY,
        replicate_id="A",
        transition_ordinal=1,
        protocol_intent="READ",
        identity=setup.request.identity,
        controller_input=setup.request.controller_input,
        plan_correction=setup.request.plan_correction,
    )
    return setup, request


def _schema_keywords(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(map(_schema_keywords, value.values())),
        )
    if isinstance(value, list):
        return set().union(*(map(_schema_keywords, value)))
    return set()


def test_static_schema_uses_only_the_frozen_conservative_subset() -> None:
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum"}
    assert _schema_keywords(STATIC_PROVIDER_ALIAS_SCHEMA_V5) <= allowed | {
        "decision",
        "hypothesis_alias",
        "action_alias",
        "support_aliases",
        "contradict_aliases",
    }
    assert STATIC_PROVIDER_ALIAS_SCHEMA_V5["properties"]["decision"]["enum"] == [
        "READ",
        "COMMIT",
        "NO_INCIDENT",
        "ABSTAIN",
    ]
    encoded = json.dumps(STATIC_PROVIDER_ALIAS_SCHEMA_V5, sort_keys=True)
    for forbidden in (
        "uniqueItems",
        "minItems",
        "maxItems",
        "pattern",
        "oneOf",
        "allOf",
        "if",
        "then",
        "H00",
        "A00",
        "E00",
    ):
        assert forbidden not in encoded
    assert static_schema_sha256_v5() == semantic_sha256_v22(
        STATIC_PROVIDER_ALIAS_SCHEMA_V5
    )


def test_all_48_requests_bind_one_static_schema_and_distinct_request_hashes() -> None:
    requests_a = materialize_protocol_requests_v5(replicate_id="A")
    requests_b = materialize_protocol_requests_v5(replicate_id="B")
    assert len(requests_a) == len(requests_b) == 24
    assert {
        item.request.static_schema_sha256 for item in (*requests_a, *requests_b)
    } == {static_schema_sha256_v5()}
    hashes_a = {item.request.request_sha256 for item in requests_a}
    hashes_b = {item.request.request_sha256 for item in requests_b}
    assert len(hashes_a) == len(hashes_b) == 24
    assert hashes_a.isdisjoint(hashes_b)
    for values in (requests_a, requests_b):
        for start in range(0, 21):
            block = values[start : start + 4]
            assert {item.arm for item in block} == set(ControllerArmV22)
            assert len({item.protocol_intent for item in block}) >= 2
            assert sum(item.transition_kind == "CORRECTION_ENVELOPE" for item in block) <= 1


def test_valid_aliases_resolve_to_the_unchanged_internal_decision() -> None:
    _setup, request = _read_request()
    hypothesis = next(
        item.alias
        for item in request.alias_binding.hypotheses
        if item.canonical_id == "h:payment:configuration-error"
    )
    action = next(item.alias for item in request.alias_binding.actions if item.available)
    resolved = resolve_provider_alias_decision_v5(
        alias_decision=ProviderDecisionAliasV5(
            decision="READ",
            hypothesis_alias=hypothesis,
            action_alias=action,
            support_aliases=(),
            contradict_aliases=(),
        ),
        binding=request.alias_binding,
    )
    assert isinstance(resolved, ControllerDecisionV22)
    assert resolved.working_hypothesis_id == "h:payment:configuration-error"
    assert resolved.action_id != "NONE"


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        ({"hypothesis_alias": "H99"}, AliasResolutionErrorCodeV5.UNKNOWN_ALIAS),
        ({"action_alias": "A99"}, AliasResolutionErrorCodeV5.STALE_ALIAS),
        ({"action_alias": "H00"}, AliasResolutionErrorCodeV5.WRONG_KIND_ALIAS),
        (
            {"support_aliases": ("E00",), "contradict_aliases": ("E00",)},
            AliasResolutionErrorCodeV5.DUPLICATE_ALIAS,
        ),
        (
            {"decision": "COMMIT", "action_alias": "A00"},
            AliasResolutionErrorCodeV5.DECISION_ACTION_MISMATCH,
        ),
        (
            {"decision": "COMMIT", "action_alias": "NONE", "support_aliases": ()},
            AliasResolutionErrorCodeV5.COMMIT_SUPPORT_REQUIRED,
        ),
    ),
)
def test_request_bound_local_validation_rejects_before_runtime(
    payload: dict[str, object],
    code: AliasResolutionErrorCodeV5,
) -> None:
    _setup, request = _read_request()
    values: dict[str, object] = {
        "decision": "READ",
        "hypothesis_alias": request.alias_binding.hypotheses[0].alias,
        "action_alias": next(
            item.alias for item in request.alias_binding.actions if item.available
        ),
        "support_aliases": (),
        "contradict_aliases": (),
    }
    values.update(payload)
    with pytest.raises(AliasResolutionErrorV5) as raised:
        resolve_provider_alias_decision_v5(
            alias_decision=ProviderDecisionAliasV5.model_validate(values),
            binding=request.alias_binding,
        )
    assert raised.value.code is code


def test_projection_remains_minimal_and_protocol_intent_is_mode_scoped() -> None:
    setup, request = _read_request()
    encoded = json.dumps(request.visible_state(), sort_keys=True, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) <= 12_000
    for forbidden in (
        "controller_input",
        "request_sha256",
        "http://",
        "https://",
        "/Users/",
        "runbook",
        "command",
    ):
        assert forbidden not in encoded.lower() if forbidden.islower() else forbidden not in encoded
    semantic = build_provider_compatibility_request_v5(
        request_kind="TRANSITION",
        execution_mode=ProviderExecutionModeV5.SEMANTIC_EVALUATION,
        replicate_id="A",
        transition_ordinal=1,
        protocol_intent=None,
        identity=setup.request.identity,
        controller_input=setup.request.controller_input,
        plan_correction=setup.request.plan_correction,
    )
    assert "protocol_intent" not in semantic.visible_state()
