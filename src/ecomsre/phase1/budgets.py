"""Atomic run-local budget accounting."""

from __future__ import annotations

from ecomsre.phase1.contracts import (
    BudgetLimits,
    BudgetSnapshot,
    StableErrorCode,
)


class BudgetExceeded(RuntimeError):
    """Raised before a budget counter would exceed its immutable limit."""

    def __init__(self, detail: str) -> None:
        self.code = StableErrorCode.BUDGET_EXHAUSTED
        super().__init__(f"{self.code.value}: {detail}")


class RunBudget:
    """Mutable counters governed by immutable run-local limits."""

    def __init__(self, limits: BudgetLimits) -> None:
        self._limits = limits
        self._model_calls = 0
        self._tool_calls = 0
        self._total_tokens = 0

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    @property
    def remaining_model_calls(self) -> int:
        return self._limits.max_model_calls - self._model_calls

    @property
    def remaining_tool_calls(self) -> int:
        return self._limits.max_tool_calls - self._tool_calls

    @property
    def remaining_tokens(self) -> int:
        return self._limits.max_total_tokens - self._total_tokens

    def consume_model_call(self) -> None:
        if self._model_calls >= self._limits.max_model_calls:
            raise BudgetExceeded("model call limit reached")
        self._model_calls += 1

    def consume_tool_call(self) -> None:
        if self._tool_calls >= self._limits.max_tool_calls:
            raise BudgetExceeded("tool call limit reached")
        self._tool_calls += 1

    def consume_tokens(self, amount: int) -> None:
        if type(amount) is not int:
            raise TypeError("token amount must be an integer")
        if amount < 0:
            raise ValueError("token amount must be nonnegative")
        if self._total_tokens + amount > self._limits.max_total_tokens:
            raise BudgetExceeded("token limit reached")
        self._total_tokens += amount

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            model_calls=self._model_calls,
            tool_calls=self._tool_calls,
            total_tokens=self._total_tokens,
            limits=self._limits,
        )
