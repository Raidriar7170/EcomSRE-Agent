from __future__ import annotations

import pytest
from pydantic import ValidationError

from ecomsre.phase1.budgets import BudgetExceeded, RunBudget
from ecomsre.phase1.contracts import BudgetLimits, BudgetSnapshot, StableErrorCode


def test_budget_consumption_returns_immutable_snapshot() -> None:
    limits = BudgetLimits(
        max_model_calls=2,
        max_tool_calls=3,
        max_total_tokens=100,
    )
    budget = RunBudget(limits)

    budget.consume_model_call()
    budget.consume_tool_call()
    budget.consume_tokens(40)

    assert budget.snapshot() == BudgetSnapshot(
        model_calls=1,
        tool_calls=1,
        total_tokens=40,
        limits=limits,
    )
    assert budget.remaining_model_calls == 1
    assert budget.remaining_tool_calls == 2
    assert budget.remaining_tokens == 60


@pytest.mark.parametrize(
    ("method_name", "limits"),
    (
        (
            "consume_model_call",
            BudgetLimits(
                max_model_calls=0,
                max_tool_calls=1,
                max_total_tokens=1,
            ),
        ),
        (
            "consume_tool_call",
            BudgetLimits(
                max_model_calls=1,
                max_tool_calls=0,
                max_total_tokens=1,
            ),
        ),
    ),
)
def test_call_budget_failure_is_check_before_increment_and_atomic(
    method_name: str,
    limits: BudgetLimits,
) -> None:
    budget = RunBudget(limits)
    before = budget.snapshot()

    with pytest.raises(BudgetExceeded) as raised:
        getattr(budget, method_name)()

    assert raised.value.code is StableErrorCode.BUDGET_EXHAUSTED
    assert budget.snapshot() == before


def test_token_budget_failure_is_check_before_increment_and_atomic() -> None:
    limits = BudgetLimits(
        max_model_calls=1,
        max_tool_calls=1,
        max_total_tokens=10,
    )
    budget = RunBudget(limits)
    budget.consume_tokens(7)
    before = budget.snapshot()

    with pytest.raises(BudgetExceeded) as raised:
        budget.consume_tokens(4)

    assert raised.value.code is StableErrorCode.BUDGET_EXHAUSTED
    assert budget.snapshot() == before


@pytest.mark.parametrize("amount", (-1, -100, 1.5, True))
def test_token_amount_must_be_a_nonnegative_integer(amount: object) -> None:
    budget = RunBudget(
        BudgetLimits(
            max_model_calls=1,
            max_tool_calls=1,
            max_total_tokens=10,
        )
    )
    before = budget.snapshot()

    with pytest.raises((TypeError, ValueError)):
        budget.consume_tokens(amount)  # type: ignore[arg-type]

    assert budget.snapshot() == before


def test_budget_limits_cannot_be_silently_increased() -> None:
    limits = BudgetLimits(
        max_model_calls=1,
        max_tool_calls=1,
        max_total_tokens=1,
    )
    budget = RunBudget(limits)

    with pytest.raises(ValidationError) as frozen_error:
        budget.limits.max_model_calls = 2  # type: ignore[misc]
    assert frozen_error.value.errors()[0]["type"] == "frozen_instance"

    assert budget.snapshot().limits.max_model_calls == 1
