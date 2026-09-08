"""Crash windows cannot duplicate resource mutations."""

import pytest
from scripts.product.preflight_v040_v4.common import Failure, load, seal
from scripts.product.preflight_v040_v4.journal import Journal


@pytest.mark.parametrize("already_intent", [False, True])
def test_intent_precedes_execution_and_resume_never_reexecutes(
    tmp_path, already_intent
):
    journal = Journal(tmp_path, {"daemon": "d"})
    command = ["container", "rm", "a"]
    authority = {"birth": "hash"}
    calls = []
    if already_intent:
        seal(
            tmp_path,
            "intents/remove-a.json",
            {
                "key": "remove-a",
                "command": command,
                "authority": authority,
                "binding": {"daemon": "d"},
            },
        )

    def execute():
        assert load(tmp_path / "intents/remove-a.json")["command"] == command
        assert not (tmp_path / "receipts/remove-a.json").exists()
        calls.append(1)
        return {"returncode": 0}

    receipt = journal.once(
        "remove-a",
        command,
        authority,
        execute,
        lambda: {"absent": True},
        lambda x: x["absent"],
    )
    assert receipt["postcondition"]
    assert len(calls) == (0 if already_intent else 1)
    assert (
        journal.once(
            "remove-a", command, authority, execute, lambda: {}, lambda x: False
        )
        == receipt
    )
    assert len(calls) == (0 if already_intent else 1)


def test_unknown_outcome_does_not_retry(tmp_path):
    journal = Journal(tmp_path, {})
    seal(
        tmp_path,
        "intents/x.json",
        {"key": "x", "command": ["rm", "a"], "authority": {}, "binding": {}},
    )

    def forbidden():
        raise AssertionError("must not execute")

    with pytest.raises(Failure, match="POSTCONDITION"):
        journal.once("x", ["rm", "a"], {}, forbidden, lambda: {}, lambda x: False)


def test_failure_has_receipt_and_stops(tmp_path):
    journal = Journal(tmp_path, {})

    def fail():
        raise OSError("transport failure")

    with pytest.raises(Failure, match="POSTCONDITION"):
        journal.once("x", ["rm", "a"], {}, fail, lambda: {}, lambda x: False)
    assert load(tmp_path / "receipts/x.json")["outcome"]["error_type"] == "OSError"
    with pytest.raises(Failure, match="PRIOR_MUTATION"):
        journal.once("x", ["rm", "a"], {}, fail, lambda: {}, lambda x: False)
