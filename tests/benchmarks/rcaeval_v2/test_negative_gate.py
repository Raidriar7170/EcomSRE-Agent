from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval_v2.public_projection import assert_public_payload


ROOT = Path(__file__).parents[3]
EVIDENCE = ROOT / "docs" / "review-evidence" / "rcaeval-re2-v2-dev"
RESULTS = ROOT / "docs" / "results"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_failed_provider_smoke_is_public_case_free_and_fail_closed() -> None:
    gate_path = EVIDENCE / "provider-smoke-gate.json"
    gate = _load(gate_path)
    disposition = _load(EVIDENCE / "current-disposition.json")
    aggregate = _load(RESULTS / "rcaeval-re2-v2-dev-aggregate.json")

    assert gate["state"] == "V2_PROVIDER_DEV_GATE_NOT_PASSED"
    assert gate["gate_checks"]["exact_failure_stage_coverage"]["passed"] is False
    assert gate["gate_checks"]["v2_run_completion"]["passed"] is False
    assert gate["run_accounting"]["terminalized_runs"] == 10
    assert disposition["state"] == "V2_PROVIDER_DEV_GATE_NOT_PASSED"
    assert disposition["dev_validation_accessed"] is False
    assert disposition["provider_smoke_gate_sha256"] == hashlib.sha256(
        gate_path.read_bytes()
    ).hexdigest()
    assert aggregate["paired_development_comparisons"] == {}
    assert aggregate["architecture_summaries"] == {}
    for payload in (gate, disposition, aggregate):
        assert_public_payload(payload)


def test_public_negative_result_contains_no_case_or_run_level_fields() -> None:
    for path in (
        EVIDENCE / "provider-smoke-gate.json",
        EVIDENCE / "current-disposition.json",
        RESULTS / "rcaeval-re2-v2-dev-aggregate.json",
    ):
        encoded = path.read_text(encoding="utf-8").casefold()
        assert '"case_id"' not in encoded
        assert '"run_id"' not in encoded
        assert '"instance"' not in encoded
        assert "/users/" not in encoded
        assert "/home/" not in encoded
        assert "/private/" not in encoded
