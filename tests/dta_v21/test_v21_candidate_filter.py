from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ecomsre.dta_v2.v21.candidate_filter import (
    CandidateFilterError,
    filter_runbook_candidates,
)
from ecomsre.dta_v2.v21.contracts import (
    FaultDomainV21,
    FaultMechanismV21,
    ResolvedEvidenceV21,
    RunbookIdV21,
    TerminalV21,
    build_resolved_diagnosis_evidence_view_v21,
)
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry
from ecomsre.dta_v2.v21.replay import build_replay_diagnosis


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("root", "domain", "mechanism", "sources", "expected"),
    [
        (
            "ad",
            FaultDomainV21.LOCAL_RESOURCE,
            FaultMechanismV21.CPU_SATURATION,
            ("METRICS", "RESOURCES", "RUNTIME"),
            RunbookIdV21.MITIGATE_CPU_SATURATION,
        ),
        (
            "email",
            FaultDomainV21.SERVICE_RUNTIME,
            FaultMechanismV21.SERVICE_UNAVAILABLE,
            ("RUNTIME", "METRICS"),
            RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
        ),
        (
            "product-catalog",
            FaultDomainV21.SERVICE_RUNTIME,
            FaultMechanismV21.SERVICE_UNAVAILABLE,
            ("RUNTIME", "TRACES"),
            RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
        ),
        (
            "shipping",
            FaultDomainV21.DEPENDENCY,
            FaultMechanismV21.DEPENDENCY_LATENCY,
            ("TRACES", "METRICS"),
            RunbookIdV21.RESTORE_DEPENDENCY_LATENCY,
        ),
        (
            "email",
            FaultDomainV21.LOCAL_RESOURCE,
            FaultMechanismV21.MEMORY_LEAK,
            ("METRICS", "RUNTIME", "RESOURCES"),
            RunbookIdV21.MITIGATE_MEMORY_LEAK,
        ),
    ],
)
def test_candidate_filter_uses_typed_diagnosis_evidence_registry_and_target(
    root: str,
    domain: FaultDomainV21,
    mechanism: FaultMechanismV21,
    sources: tuple[str, ...],
    expected: RunbookIdV21,
) -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id="2" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service=root,
        fault_domain=domain,
        mechanism=mechanism,
        evidence_sources=sources,
    )
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=load_default_runbook_registry(REPO_ROOT),
        exact_target=root,
    )

    assert [item.runbook_id for item in candidates.write_candidates] == [expected]


def test_candidate_filter_rejects_target_drift_and_has_no_truth_inputs() -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id="3" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="email",
        fault_domain=FaultDomainV21.SERVICE_RUNTIME,
        mechanism=FaultMechanismV21.SERVICE_UNAVAILABLE,
        evidence_sources=("RUNTIME", "METRICS"),
    )
    with pytest.raises(CandidateFilterError, match="exact target"):
        filter_runbook_candidates(
            diagnosis=diagnosis,
            diagnosis_evidence=evidence,
            registry=load_default_runbook_registry(REPO_ROOT),
            exact_target="product-catalog",
        )

    parameters = inspect.signature(filter_runbook_candidates).parameters
    assert set(parameters) == {
        "diagnosis",
        "diagnosis_evidence",
        "registry",
        "exact_target",
    }
    source = inspect.getsource(filter_runbook_candidates).casefold()
    for forbidden in (
        "evaluator_truth",
        "scenario_control",
        "fault_operation",
        "held_out_split",
    ):
        assert forbidden not in source


def test_model_confidence_cannot_change_candidate_authority() -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id="7" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="ad",
        fault_domain=FaultDomainV21.LOCAL_RESOURCE,
        mechanism=FaultMechanismV21.CPU_SATURATION,
        evidence_sources=("METRICS", "RUNTIME", "RESOURCES"),
    )
    registry = load_default_runbook_registry(REPO_ROOT)
    high = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target="ad",
    )
    low = filter_runbook_candidates(
        diagnosis=diagnosis.model_copy(update={"confidence": 0.01}),
        diagnosis_evidence=evidence,
        registry=registry,
        exact_target="ad",
    )

    assert low.write_candidates == high.write_candidates


def test_availability_candidates_require_target_specific_evidence() -> None:
    diagnosis, evidence = build_replay_diagnosis(
        run_id="d" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="email",
        fault_domain=FaultDomainV21.SERVICE_RUNTIME,
        mechanism=FaultMechanismV21.SERVICE_UNAVAILABLE,
        evidence_sources=("RUNTIME",),
    )
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=load_default_runbook_registry(REPO_ROOT),
        exact_target="email",
    )

    assert candidates.write_candidates == ()

    product_diagnosis, product_evidence = build_replay_diagnosis(
        run_id="e" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="product-catalog",
        fault_domain=FaultDomainV21.SERVICE_RUNTIME,
        mechanism=FaultMechanismV21.SERVICE_UNAVAILABLE,
        evidence_sources=("METRICS", "RUNTIME"),
    )
    product_candidates = filter_runbook_candidates(
        diagnosis=product_diagnosis,
        diagnosis_evidence=product_evidence,
        registry=load_default_runbook_registry(REPO_ROOT),
        exact_target="product-catalog",
    )
    assert product_candidates.write_candidates == ()


def test_candidate_filter_allows_affected_service_evidence_with_root_binding() -> None:
    diagnosis, original = build_replay_diagnosis(
        run_id="f" * 32,
        terminal=TerminalV21.COMPLETED,
        root_service="shipping",
        fault_domain=FaultDomainV21.DEPENDENCY,
        mechanism=FaultMechanismV21.DEPENDENCY_LATENCY,
        evidence_sources=("METRICS", "TRACES"),
    )
    evidence = build_resolved_diagnosis_evidence_view_v21(
        run_id=diagnosis.run_id,
        evidence=tuple(
            ResolvedEvidenceV21(
                evidence_ref=item.evidence_ref,
                source=item.source,
                service_scope=(
                    ("checkout",)
                    if item.source.value == "METRICS"
                    else ("checkout", "shipping")
                ),
                artifact_sha256=item.artifact_sha256,
            )
            for item in original.evidence
        ),
    )

    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=evidence,
        registry=load_default_runbook_registry(REPO_ROOT),
        exact_target="shipping",
    )

    assert [item.runbook_id for item in candidates.write_candidates] == [
        RunbookIdV21.RESTORE_DEPENDENCY_LATENCY
    ]

    affected_only = build_resolved_diagnosis_evidence_view_v21(
        run_id=diagnosis.run_id,
        evidence=tuple(
            ResolvedEvidenceV21(
                evidence_ref=item.evidence_ref,
                source=item.source,
                service_scope=("checkout",),
                artifact_sha256=item.artifact_sha256,
            )
            for item in original.evidence
        ),
    )
    with pytest.raises(CandidateFilterError, match="lacks the exact target"):
        filter_runbook_candidates(
            diagnosis=diagnosis,
            diagnosis_evidence=affected_only,
            registry=load_default_runbook_registry(REPO_ROOT),
            exact_target="shipping",
        )
