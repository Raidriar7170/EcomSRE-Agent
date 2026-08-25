from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.memory import RuntimeReadOutcomeV22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
)
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    RealFaultActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_bootstrap_v226 import (
    build_real_fault_canonical_bootstrap_v226,
    real_fault_run_id_v226,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1


ROOT = Path(__file__).resolve().parents[2]
CASE_IDS = (
    "fault-map-a",
    "fault-map-b",
    "baseline-map-a",
    "baseline-map-b",
)


def _capture(case_id: str) -> RealFaultOpaqueCaptureV1:
    path = ROOT / f"config/dta-v225-real-fault/captures/{case_id}.json"
    return RealFaultOpaqueCaptureV1.model_validate_json(path.read_text())


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_old_real_capture_uses_one_canonical_bootstrap(case_id: str) -> None:
    capture = _capture(case_id)
    baseline_case = (
        f"baseline-{case_id.split('-', 1)[1]}"
        if case_id.startswith("fault-")
        else case_id
    )
    baseline = _capture(baseline_case)
    run_id = real_fault_run_id_v226(capture)
    backend = RealFaultActionReadBackendV225.snapshot(
        capture=capture,
        run_id=run_id,
    )

    bootstrap, outcomes = build_real_fault_canonical_bootstrap_v226(
        capture=capture,
        baseline_capture=baseline,
        backend=backend,
    )

    assert tuple(outcome.source for outcome in outcomes) == (
        EvidenceSourceV22.RUNTIME,
        EvidenceSourceV22.METRICS,
        EvidenceSourceV22.METRICS,
    )
    assert isinstance(outcomes[0], RuntimeReadOutcomeV22)
    assert all(outcome.source is not EvidenceSourceV22.RESOURCES for outcome in outcomes)
    assert bootstrap.resources_in_bootstrap is False
    assert bootstrap.candidate_services == capture.candidate_aliases
    assert len(bootstrap.read_bindings) == 3
    assert {item.action_id for item in bootstrap.read_bindings} == {
        outcome.action_id for outcome in outcomes
    }
    assert all(item.evidence_refs for item in bootstrap.read_bindings)
    assert all(
        evidence_ref.startswith("e:a:")
        for item in bootstrap.read_bindings
        for evidence_ref in item.evidence_refs
    )
    assert "evidence://" not in bootstrap.model_dump_json()
    assert bootstrap.bootstrap_sha256 == bootstrap.recompute_sha256()


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_unsupported_real_metrics_remain_typed(case_id: str) -> None:
    capture = _capture(case_id)
    baseline_case = (
        f"baseline-{case_id.split('-', 1)[1]}"
        if case_id.startswith("fault-")
        else case_id
    )
    baseline = _capture(baseline_case)
    run_id = real_fault_run_id_v226(capture)
    backend = RealFaultActionReadBackendV225.snapshot(
        capture=capture,
        run_id=run_id,
    )

    bootstrap, outcomes = build_real_fault_canonical_bootstrap_v226(
        capture=capture,
        baseline_capture=baseline,
        backend=backend,
    )
    metrics = tuple(
        record
        for outcome in outcomes
        if outcome.source is EvidenceSourceV22.METRICS
        for record in outcome.records
        if isinstance(record, MetricFactV22)
    )
    unsupported_error_rate = tuple(
        item
        for item in metrics
        if item.metric_kind is MetricKindV22.ERROR_RATE
        and item.support_status is MetricSupportStatusV22.UNSUPPORTED
    )

    assert len(unsupported_error_rate) == 2
    assert all(item.sample_count == 0 and item.value is None for item in unsupported_error_rate)
    assert bootstrap.unsupported_metric_count == 2
