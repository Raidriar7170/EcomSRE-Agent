import pytest
from pydantic import ValidationError

from ecomsre.phase0.models import Outcome, TerminalResult


EXPECTED_EXIT_CODES = {
    Outcome.SUCCESS: 0,
    Outcome.BLOCKED_ENVIRONMENT: 20,
    Outcome.BLOCKED_UPSTREAM: 21,
    Outcome.FAILED_ACCEPTANCE: 30,
    Outcome.UNSAFE: 40,
    Outcome.MANUAL_INTERVENTION_REQUIRED: 41,
    Outcome.INVALID_INVOCATION: 64,
}


def test_every_terminal_outcome_has_the_exact_frozen_exit_code() -> None:
    assert {outcome: outcome.exit_code for outcome in Outcome} == EXPECTED_EXIT_CODES


@pytest.mark.parametrize("outcome", list(Outcome))
def test_terminal_result_derives_exit_code_without_ambiguity(outcome: Outcome) -> None:
    result = TerminalResult(outcome=outcome, reason_code="TEST_REASON")

    assert result.exit_code == EXPECTED_EXIT_CODES[outcome]


def test_terminal_result_rejects_a_conflicting_explicit_exit_code() -> None:
    with pytest.raises(ValidationError, match="exit code"):
        TerminalResult(
            outcome=Outcome.UNSAFE,
            exit_code=0,
            reason_code="RESOURCE_OWNERSHIP_UNKNOWN",
        )


def test_domain_models_are_immutable() -> None:
    result = TerminalResult(outcome=Outcome.SUCCESS, reason_code="ALL_GATES_PASSED")

    with pytest.raises(ValidationError):
        result.outcome = Outcome.FAILED_ACCEPTANCE
