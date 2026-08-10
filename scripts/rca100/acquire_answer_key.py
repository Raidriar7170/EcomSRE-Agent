"""Post-terminal-lock acquisition validation for the isolated evaluator."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from ecomsre_rca100.evaluator import load_answer_key
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    advance_state,
    create_once_json,
    current_state,
    tree_sha256,
)


SOURCE_COMMIT = "fd92cae17e6e14fa3ed0f3963c31838151fbdaa7"
_PROVIDER_CREDENTIALS = (
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    roots = PrivateRoots.from_environment(os.environ)
    roots.validate(repository_root=_repository_root(), create=False)
    if current_state(roots.control) != "TERMINAL_RECORDS_LOCKED":
        raise ValueError("answer acquisition requires TERMINAL_RECORDS_LOCKED")
    if any(name in os.environ for name in _PROVIDER_CREDENTIALS):
        raise ValueError("Provider credentials remained during answer acquisition")
    commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=roots.evaluator_source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if commit != SOURCE_COMMIT:
        raise ValueError("evaluator source commit differs from the source lock")
    answer_root = roots.evaluator_source / "RCA100" / "answer_key"
    truths = load_answer_key(answer_root)
    answer_tree, file_count = tree_sha256(answer_root)
    if len(truths) != 103 or file_count != 105:
        raise ValueError("answer acquisition coverage differs from 103 cases")
    lock = {
        "schema_version": "rca100.answer-key-lock.v1",
        "source_commit": SOURCE_COMMIT,
        "mapping_coverage": 103,
        "ground_truth_files": 103,
        "answer_key_files": file_count,
        "answer_key_tree_sha256": answer_tree,
        "provider_credentials_present": False,
        "acquired_at_utc": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }
    lock_sha = create_once_json(
        roots.evaluator / "locks" / "answer-key-lock.json", lock
    )
    advance_state(
        roots.control,
        "ANSWER_KEY_ACQUIRED",
        bindings={"answer_key_lock_sha256": lock_sha},
    )
    print(json.dumps(lock, indent=2))


if __name__ == "__main__":
    main()
