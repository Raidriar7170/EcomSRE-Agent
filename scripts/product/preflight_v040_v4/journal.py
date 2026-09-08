"""Create-once intents/receipts; interrupted mutations are reconciled without retry."""

from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
from .common import digest, load, now, require, seal


class Journal:
    def __init__(self, root: Path, binding: dict[str, Any]) -> None:
        self.root = root
        self.binding = binding
        root.mkdir(parents=True, mode=0o700, exist_ok=True)

    def once(
        self,
        key: str,
        command: list[str],
        authority: dict[str, Any],
        execute: Callable[[], dict[str, Any]],
        observe: Callable[[], dict[str, Any]],
        postcondition: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any]:
        """An existing unmatched intent never causes a second execute call."""
        require(key.replace("-", "").isalnum(), "JOURNAL_KEY")
        intent_name, receipt_name = f"intents/{key}.json", f"receipts/{key}.json"
        intent_path, receipt_path = self.root / intent_name, self.root / receipt_name
        expected = {
            "key": key,
            "command": command,
            "authority": authority,
            "binding": self.binding,
        }
        if receipt_path.exists():
            require(intent_path.exists(), "RECEIPT_WITHOUT_INTENT")
            prior = load(intent_path)
            require(
                all(prior[k] == v for k, v in expected.items()),
                "JOURNAL_AUTHORITY_DRIFT",
            )
            receipt = load(receipt_path)
            require(receipt["intent_digest"] == digest(prior), "RECEIPT_INTEGRITY")
            require(receipt["postcondition"] is True, "PRIOR_MUTATION_BLOCKED")
            return receipt
        if intent_path.exists():
            prior = load(intent_path)
            require(
                all(prior[k] == v for k, v in expected.items()),
                "JOURNAL_AUTHORITY_DRIFT",
            )
            observation = observe()
            seal(
                self.root,
                f"reconciliations/{key}.json",
                {"intent_digest": digest(prior), "observation": observation, **now()},
            )
            outcome = {"execution": "RECONCILED_NO_RETRY", "client_exit_status": None}
        else:
            prior = {**expected, **now()}
            seal(self.root, intent_name, prior)
            try:
                outcome = execute()
            except Exception as error:
                outcome = {
                    "execution": "CLIENT_ERROR",
                    "error_type": type(error).__name__,
                }
            observation = observe()
        okay = postcondition(observation)
        receipt = {
            "intent_digest": digest(prior),
            "outcome": outcome,
            "observation": observation,
            "postcondition": okay,
            **now(),
        }
        seal(self.root, receipt_name, receipt)
        require(okay, "MUTATION_POSTCONDITION_FAILED")
        return receipt
