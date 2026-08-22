from __future__ import annotations

import pytest

from ecomsre.dta_v2.v22.ambiguity_coverage_ledger_v225 import (
    AmbiguityCoverageLedgerV225,
    rebuild_ambiguity_set_coverage_v225,
    record_ambiguity_coverage_event_v225,
)
from ecomsre.dta_v2.v22.ambiguity_set_v225 import (
    build_evidence_ambiguity_set_v225,
)
from ecomsre.dta_v2.v22.evaluation_strata_v225 import EvaluatorStrataV225
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.no_incident_set_closure_v225 import (
    ClosureDispositionV225,
    NoIncidentClosureScopeV225,
    evaluate_no_incident_set_closure_v225,
    initial_no_incident_set_closure_state_v225,
)
from ecomsre.dta_v2.v22.opaque_identity_v225 import (
    generate_opaque_identity_plan_v225,
)
from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    ProviderIdentityLintErrorV225,
    lint_provider_payload_v225,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22


def _ambiguity_set():
    return build_evidence_ambiguity_set_v225(
        predicate_kinds=("RESOURCE_CPU_STRONG", "RESOURCE_MEMORY_GROWTH_STRONG"),
        hypothesis_ids=("h:cpu:svc-1d761ddab4", "h:memory:svc-f802c53c0c"),
        target_services=("svc-1d761ddab4", "svc-f802c53c0c"),
        individual_action_ids=(
            "a:resources:svc-1d761ddab4",
            "a:resources:svc-f802c53c0c",
        ),
        bundle_action_id="a:resources:all-candidates:84b2e9999979",
        covered_target_services=(),
    )


def test_v225_opaque_ids_are_seeded_before_mechanism_assignment() -> None:
    left = generate_opaque_identity_plan_v225(
        seed="dta-v225-opaque-evaluation-v1",
        service_count=4,
        operation_count=4,
        change_count=2,
        pair_count=2,
    )
    right = generate_opaque_identity_plan_v225(
        seed="dta-v225-opaque-evaluation-v1",
        service_count=4,
        operation_count=4,
        change_count=2,
        pair_count=2,
    )

    assert left == right
    assert all(item.startswith("svc-") and len(item) == 14 for item in left.services)
    assert all(item.startswith("op-") and len(item) == 13 for item in left.operations)
    assert all(item.startswith("chg-") and len(item) == 14 for item in left.changes)
    assert all(item.startswith("pair-") and len(item) == 15 for item in left.pairs)


def test_v225_rendered_provider_payload_lint_rejects_truth_bearing_identity() -> None:
    with pytest.raises(ProviderIdentityLintErrorV225, match="candidate_services"):
        lint_provider_payload_v225(
            {
                "visible_state": {
                    "candidate_services": ["eval-cpu-primary", "svc-f802c53c0c"],
                    "CPU p95": 96.0,
                    "memory slope": 0.0,
                }
            },
            payload_class="bootstrap",
        )


def test_v225_rendered_provider_payload_lint_allows_telemetry_semantics() -> None:
    report = lint_provider_payload_v225(
        {
            "visible_state": {
                "candidate_services": ["svc-1d761ddab4", "svc-f802c53c0c"],
                "record": {
                    "service": "svc-1d761ddab4",
                    "CPU p95": 96.0,
                    "memory slope": 0.0,
                },
                "hypothesis": "CPU_SATURATION",
            }
        },
        payload_class="post-individual-read",
    )

    assert report.forbidden_identity_values == ()
    assert report.case_ids == ()
    assert report.evaluator_metadata_fields == ()


def test_v225_provider_payload_lint_rejects_evaluator_metadata() -> None:
    with pytest.raises(ProviderIdentityLintErrorV225, match="case_id"):
        lint_provider_payload_v225(
            {"visible_state": {"case_id": "e01", "candidate_services": []}},
            payload_class="terminal-only",
        )


def test_v225_preclosure_read_is_replayed_into_new_ambiguity_set() -> None:
    ledger = record_ambiguity_coverage_event_v225(
        ledger=AmbiguityCoverageLedgerV225.empty(),
        action_id="a:resources:svc-1d761ddab4",
        source=EvidenceSourceV22.RESOURCES,
        target_services=("svc-1d761ddab4",),
        ambiguity_sets=(),
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
        new_predicate_kinds=(),
        read_ordinal=1,
    )

    rebuilt = rebuild_ambiguity_set_coverage_v225(
        ambiguity_set=_ambiguity_set(),
        ledger=ledger,
    )

    assert rebuilt.covered_target_services == ("svc-1d761ddab4",)
    assert rebuilt.remaining_target_services == ("svc-f802c53c0c",)


def test_v225_preclosure_bundle_read_completes_new_ambiguity_set() -> None:
    ambiguity_set = _ambiguity_set()
    ledger = record_ambiguity_coverage_event_v225(
        ledger=AmbiguityCoverageLedgerV225.empty(),
        action_id="a:resources:all-candidates:84b2e9999979",
        source=EvidenceSourceV22.RESOURCES,
        target_services=ambiguity_set.target_services,
        ambiguity_sets=(),
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
        new_predicate_kinds=(),
        read_ordinal=1,
    )

    rebuilt = rebuild_ambiguity_set_coverage_v225(
        ambiguity_set=ambiguity_set,
        ledger=ledger,
    )

    assert rebuilt.complete is True


def test_v225_incomplete_set_below_minimum_cost_fails_closed_to_abstain() -> None:
    state = evaluate_no_incident_set_closure_v225(
        state=initial_no_incident_set_closure_state_v225(
            NoIncidentClosureScopeV225.AMBIGUITY_SET_COMPLETE
        ),
        legacy_no_incident_exposed=True,
        ambiguity_set=_ambiguity_set(),
        target_complete=True,
        remaining_evidence_budget=1.49,
        minimum_completion_cost=1.5,
    )

    assert state.no_incident_withheld is True
    assert state.closure_disposition is ClosureDispositionV225.BUDGET_INSUFFICIENT
    assert state.abstain_reason == "INSUFFICIENT_BUDGET_FOR_AMBIGUITY_CLOSURE"


def test_v225_incomplete_set_at_minimum_cost_remains_readable() -> None:
    state = evaluate_no_incident_set_closure_v225(
        state=initial_no_incident_set_closure_state_v225(
            NoIncidentClosureScopeV225.AMBIGUITY_SET_COMPLETE
        ),
        legacy_no_incident_exposed=True,
        ambiguity_set=_ambiguity_set(),
        target_complete=True,
        remaining_evidence_budget=1.5,
        minimum_completion_cost=1.5,
    )

    assert state.closure_required is True
    assert state.no_incident_withheld is True
    assert state.closure_disposition is ClosureDispositionV225.READABLE_INCOMPLETE
    assert state.abstain_reason is None


def test_v225_fixed_strata_are_treatment_independent() -> None:
    strata = EvaluatorStrataV225.build(
        resource_ambiguity_incidents=tuple(f"e{index:02d}" for index in range(1, 9)),
        resource_normal_controls=("e09", "e10"),
        abstention_controls=("e11", "e12"),
        configuration_incidents=("e13", "e14"),
        service_unavailable_incidents=("e15",),
        dependency_incidents=("e16",),
        cpu_incidents=("e01", "e02", "e03", "e04"),
        memory_incidents=("e05", "e06", "e07", "e08"),
    )

    assert strata.resource_ambiguity_denominator == 8
    assert strata.resource_case_denominator == 10
    assert strata.resource_ambiguity_incidents == tuple(
        f"e{index:02d}" for index in range(1, 9)
    )
