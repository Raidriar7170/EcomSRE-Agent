from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.simple_provider import (
    AliasTableV22,
    ProviderSemanticErrorV22,
    parse_provider_response_v22,
)


def _aliases() -> AliasTableV22:
    return AliasTableV22.build(
        hypothesis_ids=(
            "h:payment:configuration-error",
            NO_INCIDENT_HYPOTHESIS_ID_V22,
            ABSTAIN_HYPOTHESIS_ID_V22,
        ),
        action_ids=("a:logs:payment",),
        evidence_refs=("ev:logs:payment:1",),
    )


def _tool_response(arguments: object) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "submit_controller_decision",
                                "arguments": (
                                    arguments
                                    if isinstance(arguments, str)
                                    else json.dumps(arguments)
                                ),
                            },
                        }
                    ]
                }
            }
        ]
    }


def test_closed_hypothesis_catalog_has_explicit_safe_terminals() -> None:
    catalog = build_hypothesis_catalog_v22(candidate_services=("payment", "checkout"))

    assert len(catalog.hypotheses) == 12
    assert catalog.hypotheses[-2].hypothesis_id == NO_INCIDENT_HYPOTHESIS_ID_V22
    assert catalog.hypotheses[-1].hypothesis_id == ABSTAIN_HYPOTHESIS_ID_V22


def test_internal_controller_shape_keeps_zero_write_decisions_typed() -> None:
    decision = ControllerDecisionV22(
        decision=ControllerDecisionKindV22.COMMIT,
        working_hypothesis_id="h:payment:configuration-error",
        action_id=NO_ACTION_ID_V22,
        supporting_evidence_refs=("ev:logs:payment:1",),
        contradicting_evidence_refs=(),
    )
    assert decision.decision is ControllerDecisionKindV22.COMMIT

    with pytest.raises(ValidationError, match="COMMIT requires supporting evidence"):
        ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id="h:payment:configuration-error",
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        )


def test_static_tool_call_and_normal_json_fallback_map_local_aliases() -> None:
    payload = {
        "decision": "COMMIT",
        "hypothesis": "H00",
        "action": "NONE",
        "support": ["E00"],
        "contradict": [],
    }
    expected = ControllerDecisionV22(
        decision=ControllerDecisionKindV22.COMMIT,
        working_hypothesis_id="h:payment:configuration-error",
        action_id=NO_ACTION_ID_V22,
        supporting_evidence_refs=("ev:logs:payment:1",),
        contradicting_evidence_refs=(),
    )

    assert parse_provider_response_v22(_tool_response(payload), aliases=_aliases()) == expected
    content_response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    assert parse_provider_response_v22(content_response, aliases=_aliases()) == expected


def test_alias_validation_fails_closed_with_a_safe_code() -> None:
    response = _tool_response(
        {
            "decision": "READ",
            "hypothesis": "H99",
            "action": "A00",
            "support": [],
            "contradict": [],
        }
    )

    with pytest.raises(ProviderSemanticErrorV22) as captured:
        parse_provider_response_v22(response, aliases=_aliases())
    assert captured.value.safe_code == "UNKNOWN_H_ALIAS"


@pytest.mark.parametrize(
    ("decision", "hypothesis"),
    (("NO_INCIDENT", "H01"), ("ABSTAIN", "H02")),
)
def test_safe_terminal_aliases_are_exact(decision: str, hypothesis: str) -> None:
    parsed = parse_provider_response_v22(
        _tool_response(
            {
                "decision": decision,
                "hypothesis": hypothesis,
                "action": "NONE",
                "support": [],
                "contradict": [],
            }
        ),
        aliases=_aliases(),
    )
    assert parsed.action_id == NO_ACTION_ID_V22
