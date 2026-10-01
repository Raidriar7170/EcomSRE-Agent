"""Temporary-ledger checks only; no live round activation or Provider dispatch."""

import pytest

from ecomsre.product.errors import ProductError
from ecomsre.product.investigation import closure_budget as budget
from test_investigation import repository


def activate(repo):
    with repo.store.connect() as c:
        digest = budget.sha(budget.ledger(c))
    return budget.start(repo.store, expected_ledger_sha256=digest)


def settled(repo, key, amount=1, state="COMPLETED"):
    repo.reserve(key, {"key": key}, amount)
    repo.settle(key, {"proposal": {}}, amount if state == "COMPLETED" else None, state)


def test_activation_is_immutable_and_cache_does_not_dispatch(tmp_path):
    repo = repository(tmp_path)
    settled(repo, "old", 100)
    first = activate(repo)
    key = budget.PROPOSAL_PREFIX + "0"
    settled(repo, key, 5)
    restarted = repository(tmp_path)
    assert (
        budget.start(restarted.store, expected_ledger_sha256=first["baseline_sha256"])
        == first
    )
    assert restarted.reserve(key, {"key": key}, 5) == {"proposal": {}}
    assert restarted.accounting()["request_count"] == 2
    with pytest.raises(ValueError, match="another baseline"):
        activate(restarted)
    with pytest.raises(ProductError) as error:
        restarted.reserve("knowledge-draft-v050.2:proposal:0", {}, 1)
    assert error.value.code == "CLOSURE_PROPOSAL_NAMESPACE"


def test_all_uses_and_unknown_usage_consume_request_budget(tmp_path):
    repo = repository(tmp_path)
    activate(repo)
    for i in range(40):
        settled(repo, f"investigation:{i}", state="UNKNOWN")
    with pytest.raises(ProductError, match="cap reached"):
        repo.reserve("unrelated-scope-selection", {}, 1)
    assert repo.accounting()["unknown_usage_requests"] == 40


def test_cost_cap_retains_unknown_reservation(tmp_path):
    repo = repository(tmp_path)
    activate(repo)
    settled(repo, "transport-failure", 7_999_999, "UNKNOWN")
    with pytest.raises(ProductError, match="cap reached"):
        repo.reserve("next", {}, 2)
    assert repo.accounting()["committed_upper_microusd"] == 7_999_999


def test_six_semantic_slots_include_failed_and_repeated_output(tmp_path):
    repo = repository(tmp_path)
    with pytest.raises(ProductError) as error:
        repo.reserve(budget.PROPOSAL_PREFIX + "0", {}, 1)
    assert error.value.code == "CLOSURE_ROUND_NOT_STARTED"
    activate(repo)
    with pytest.raises(ProductError) as error:
        repo.reserve(budget.PROPOSAL_PREFIX + "1", {}, 1)
    assert error.value.code == "CLOSURE_SEMANTIC_ORDER"
    for i in range(6):
        settled(repo, budget.PROPOSAL_PREFIX + str(i), state="UNKNOWN")
    with pytest.raises(ProductError, match="slots exhausted"):
        repo.reserve(budget.PROPOSAL_PREFIX + "6", {}, 1)
    assert repo.accounting()["request_count"] == 6


def test_effective_cap_uses_original_balance_and_refuses_active_dispatch(tmp_path):
    repo = repository(tmp_path)
    repo.reserve("old", {}, 19_999_995)
    with pytest.raises(ValueError, match="active dispatch"):
        activate(repo)
    repo.settle("old", {}, None, "UNKNOWN")
    assert activate(repo)["committed_cap_microusd"] == 5
    settled(repo, "next", 5)
    with pytest.raises(ProductError, match="cap reached"):
        repo.reserve("beyond", {}, 1)


def test_original_ledger_cannot_be_rewritten_to_buy_budget(tmp_path):
    repo = repository(tmp_path)
    settled(repo, "old", 100)
    activate(repo)
    with repo.store.connect() as c:
        c.execute("UPDATE investigation_provider_calls_v050 SET charged_microusd=0")
    with pytest.raises(ProductError) as error:
        repo.reserve("new", {}, 1)
    assert error.value.code == "CLOSURE_LEDGER_DRIFT"


@pytest.mark.parametrize("style", ["input", "messages"])
def test_proposal_task_cannot_hide_under_investigation_key(tmp_path, style):
    import json

    repo = repository(tmp_path)
    activate(repo)
    binding = {
        "payload": {
            style: [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"task": "propose_detection_knowledge", "view": {}}
                    ),
                }
            ]
        }
    }
    with pytest.raises(ProductError) as error:
        repo.reserve("innocent-investigation-key", binding, 1)
    assert error.value.code == "CLOSURE_PROPOSAL_NAMESPACE"
    assert repo.accounting()["request_count"] == 0


def test_original_request_balance_is_smaller_than_round_cap(tmp_path):
    repo = repository(tmp_path)
    for i in range(198):
        settled(repo, f"old-{i}")
    assert activate(repo)["request_cap"] == 2
    settled(repo, "new-1")
    settled(repo, "new-2")
    with pytest.raises(ProductError, match="cap reached"):
        repo.reserve("new-3", {}, 1)
    assert repo.accounting()["request_count"] == 200
