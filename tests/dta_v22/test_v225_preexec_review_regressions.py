from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    AmbiguityBundleCaseRunV225,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_cli_v225 import (
    AmbiguityBundleStudyArtifactV225,
)
from ecomsre.dta_v2.v22.ambiguity_coverage_ledger_v225 import (
    AmbiguityCoverageLedgerV225,
    forgotten_coverage_event_count_v225,
    rebuild_ambiguity_set_coverage_v225,
    record_ambiguity_coverage_event_v225,
)
from ecomsre.dta_v2.v22.ambiguity_set_v225 import (
    build_evidence_ambiguity_set_v225,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    ProviderIdentityLintErrorV225,
    lint_provider_payload_v225,
)
from ecomsre.dta_v2.v22.provider_payload_lint_report_v225 import (
    build_provider_payload_lint_report_v225,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("hypothesis_ids", ["h:svc-1234567890:cpu-saturation"]),
        ("attempted_action_ids", ["a:resources:svc-1234567890"]),
        ("set_id", "eas:resources:0123456789abcdef"),
    ),
)
def test_v225_provider_lint_rejects_semantic_runtime_identity_classes(
    key: str,
    value: object,
) -> None:
    with pytest.raises(ProviderIdentityLintErrorV225):
        lint_provider_payload_v225(
            {"visible_state": {key: value}},
            payload_class="regression",
        )


def test_v225_lint_renders_every_frozen_evaluation_run() -> None:
    report = build_provider_payload_lint_report_v225(repository_root=ROOT)

    assert report.evaluation_runs_rendered == 64
    assert report.runtime_payloads_rendered == 64
    assert report.synthetic_protocol_payloads_rendered == 2
    assert len(report.rendered_reports) == 66
    assert report.forbidden_identity_value_count == 0
    assert report.provider_case_id_count == 0
    assert report.provider_evaluator_metadata_field_count == 0


def test_v225_forgotten_coverage_count_is_derived_from_ledger_and_set() -> None:
    ambiguity = build_evidence_ambiguity_set_v225(
        predicate_kinds=("RESOURCE_CPU_STRONG", "RESOURCE_MEMORY_GROWTH_STRONG"),
        hypothesis_ids=("h:cpu:svc-a", "h:memory:svc-b"),
        target_services=("svc-1234567890", "svc-abcdef1234"),
        individual_action_ids=(
            "a:resources:svc-1234567890",
            "a:resources:svc-abcdef1234",
        ),
        bundle_action_id="a:resources:all-candidates:0123456789ab",
        covered_target_services=(),
    )
    ledger = record_ambiguity_coverage_event_v225(
        ledger=AmbiguityCoverageLedgerV225.empty(),
        action_id="a:resources:svc-1234567890",
        source=EvidenceSourceV22.RESOURCES,
        target_services=("svc-1234567890",),
        ambiguity_sets=(),
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
        new_predicate_kinds=(),
        read_ordinal=1,
    )

    assert forgotten_coverage_event_count_v225(
        ledger=ledger,
        ambiguity_set=ambiguity,
    ) == 1
    rebuilt = rebuild_ambiguity_set_coverage_v225(
        ambiguity_set=ambiguity,
        ledger=ledger,
    )
    assert forgotten_coverage_event_count_v225(
        ledger=ledger,
        ambiguity_set=rebuilt,
    ) == 0


def test_v225_run_rejects_tampered_forgotten_coverage_count() -> None:
    artifact = AmbiguityBundleStudyArtifactV225.model_validate_json(
        (
            ROOT
            / "docs/results/dta-v22-5-opaque-ambiguity-development.json"
        ).read_bytes()
    )
    run = next(
        item for item in artifact.campaign.runs if item.closure_state.ambiguity_set
    )
    payload = run.model_dump(mode="python")
    payload["forgotten_preclosure_read_count"] = 1

    with pytest.raises(ValueError, match="forgotten preclosure coverage count differs"):
        AmbiguityBundleCaseRunV225.model_validate(payload)
