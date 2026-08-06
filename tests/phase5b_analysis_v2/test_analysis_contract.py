from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from scripts.phase5b_analysis_v2.analysis import (
    preregistered_subsets_for_template,
    project_hidden_truth_v2,
    score_hidden_record_v2,
)
from scripts.phase5b_execution.contracts import (
    ObservedDiagnosisRecord,
    ProviderUsageRecord,
    TerminalStatus,
    canonical_json_bytes,
    seal_raw_record,
)


RUN_ID = "a" * 32


def _hidden_truth(*, difficult_subsets: list[str]) -> dict[str, object]:
    return {
        "schema_version": "phase5b.hidden-ground-truth.v1",
        "evaluation_version": "phase5b.v1",
        "template_id": "hidden-01",
        "seed_id": "seed-00",
        "decision": "RCA_CONFIRMED",
        "incident_confirmed": True,
        "root_service": "checkout",
        "fault_mechanism": "timeout",
        "causal_chain": ["checkout stalls", "latency degrades"],
        "affected_sli": "checkout latency",
        "required_support_sources": ["METRICS", "LOGS"],
        "required_contradiction_handling": [],
        "required_missing_evidence": [],
        "write_disposition": "NO_ACTION",
        "difficult_subsets": difficult_subsets,
    }


def _raw_record():
    diagnosis = ObservedDiagnosisRecord(
        run_id=RUN_ID,
        decision="RCA_CONFIRMED",
        root_service="checkout",
        fault_mechanism="timeout",
        causal_chain=("checkout stalls", "latency degrades"),
        affected_sli="checkout latency",
        supporting_evidence=(
            f"evidence://{RUN_ID}/metrics/0001",
            f"evidence://{RUN_ID}/logs/0002",
        ),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale="The two independent sources identify checkout timeout.",
        recommended_next_action="Continue replay-only verification.",
    )
    usage = ProviderUsageRecord(
        model_calls=0,
        tool_calls=2,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        workflow_tokens=0,
        combined_tokens=0,
        provider_network_calls=0,
        provider_usage_known=True,
    )
    return seal_raw_record(
        run_id=RUN_ID,
        template_id="hidden-01",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
        terminal_status=TerminalStatus.COMPLETED,
        observed_diagnosis=diagnosis,
        usage=usage,
        evidence_class="MOCK_EXECUTION_REHEARSAL",
        provider_attempted=False,
        latency_ms=0,
        failure_code=None,
        failure_stage=None,
        investigated_sources=("METRICS", "LOGS"),
    )


def test_primary_truth_is_independent_of_private_subset_metadata() -> None:
    first = project_hidden_truth_v2(
        payload=_hidden_truth(difficult_subsets=["private-label-a"]),
        template_id="hidden-01",
        seed_id="seed-00",
        write_disposition="NO_ACTION",
    )
    second = project_hidden_truth_v2(
        payload=_hidden_truth(difficult_subsets=["private-label-b"]),
        template_id="hidden-01",
        seed_id="seed-00",
        write_disposition="NO_ACTION",
    )

    assert first.expected_decision == second.expected_decision == "RCA_CONFIRMED"
    assert first.expected_root_service == second.expected_root_service == "checkout"
    assert (
        first.expected_fault_mechanism == second.expected_fault_mechanism == "timeout"
    )


def test_unknown_private_subset_labels_cannot_change_primary_scoring() -> None:
    raw = _raw_record()
    first_payload = _hidden_truth(difficult_subsets=["private-label-a"])
    second_payload = _hidden_truth(difficult_subsets=["private-label-b"])

    first = score_hidden_record_v2(
        raw=raw,
        payload=first_payload,
        truth_sha256=hashlib.sha256(canonical_json_bytes(first_payload)).hexdigest(),
        write_disposition="NO_ACTION",
    )
    second = score_hidden_record_v2(
        raw=raw,
        payload=second_payload,
        truth_sha256=hashlib.sha256(canonical_json_bytes(second_payload)).hexdigest(),
        write_disposition="NO_ACTION",
    )

    assert first.decision_correct is True
    assert second.decision_correct is True
    assert first.root_service_correct == second.root_service_correct is True
    assert first.mechanism_correct == second.mechanism_correct is True


def test_scoring_preserves_raw_record_and_truth_payload_hashes() -> None:
    raw = _raw_record()
    payload = _hidden_truth(difficult_subsets=["private-label-a"])
    raw_sha256 = raw.record_sha256
    payload_before = deepcopy(payload)
    truth_sha256 = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    scored = score_hidden_record_v2(
        raw=raw,
        payload=payload,
        truth_sha256=truth_sha256,
        write_disposition="NO_ACTION",
    )

    assert raw.record_sha256 == raw_sha256
    assert raw.expected_record_sha256() == raw_sha256
    assert payload == payload_before
    assert hashlib.sha256(canonical_json_bytes(payload)).hexdigest() == truth_sha256
    assert scored.raw_record_sha256 == raw_sha256
    assert scored.truth_content_sha256 == truth_sha256


def test_public_preregistered_mapping_controls_secondary_subset_grouping() -> None:
    projection = project_hidden_truth_v2(
        payload={
            **_hidden_truth(difficult_subsets=["private-label-ignored"]),
            "template_id": "hidden-03",
        },
        template_id="hidden-03",
        seed_id="seed-00",
        write_disposition="NO_ACTION",
    )

    assert preregistered_subsets_for_template("hidden-03") == (
        "delayed_stale_telemetry",
        "required_abstention",
    )
    assert projection.difficult_subsets == preregistered_subsets_for_template(
        "hidden-03"
    )


def test_unregistered_template_cannot_enter_v2_subset_grouping() -> None:
    with pytest.raises(ValueError, match="preregistered subset mapping"):
        preregistered_subsets_for_template("hidden-07")
