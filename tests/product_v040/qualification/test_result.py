from copy import deepcopy

import pytest

from scripts.ci.verify_product_v040_qualification import verify_result


def blocked():
    return {
        "formal_authority": False,
        "one_shot_allowance_consumed": False,
        "counters": dict.fromkeys(
            (
                "formal_faults",
                "remediation_writes",
                "provider_calls",
                "formal_campaign_executions",
            ),
            0,
        ),
        "status": "BLOCKED_PRE_EXECUTION",
        "first_divergence": "LATCHED",
    }


def test_preserved_negative_is_not_runtime_success():
    result = blocked()
    verify_result(result)
    for key, value in (
        ("formal_authority", True),
        ("one_shot_allowance_consumed", True),
        ("first_divergence", "NONE"),
    ):
        changed = deepcopy(result)
        changed[key] = value
        with pytest.raises(ValueError):
            verify_result(changed)


@pytest.mark.parametrize("value", [1, None, False, "0"])
def test_unknown_or_nonzero_is_not_zero(value):
    result = blocked()
    result["counters"]["formal_campaign_executions"] = value
    with pytest.raises(ValueError):
        verify_result(result)
