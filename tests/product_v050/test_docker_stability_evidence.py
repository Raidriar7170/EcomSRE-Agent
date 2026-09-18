import json

import pytest

from scripts.ci.verify_product_v050_docker_stability import RESULT, verify_claims


def bundle():
    return [
        json.loads((RESULT / name).read_text())
        for name in ("result.json", "calls.json", "live-traces.json")
    ]


def test_live_terminal_is_derived_with_old_failures_retained():
    assert verify_claims(*bundle())["complete_read_followups"] == 13


@pytest.mark.parametrize(
    "tamper", ["cost", "candidate", "restore", "read", "drift", "support", "mechanism"]
)
def test_live_claim_tampering_is_rejected(tamper):
    report, calls, sessions = bundle()
    if tamper == "cost":
        report["accounting"]["committed_upper_microusd"] = 0
    elif tamper == "candidate":
        report["accepted_candidates"] = 1
    elif tamper == "restore":
        sessions[0]["healthy_restored"] = False
    elif tamper == "read":
        sessions[0]["supplemental_observations"][0]["object_sha256"] = "0" * 64
    elif tamper == "drift":
        report["attempts"][-1]["cleanup"]["network_extra_unchanged"] = False
    elif tamper == "support":
        sessions[1]["supported_hypothesis_ids"] = []
    else:
        sessions[1]["hypotheses"][0]["mechanism"] = "FABRICATED_MECHANISM"
    with pytest.raises(ValueError):
        verify_claims(report, calls, sessions)
