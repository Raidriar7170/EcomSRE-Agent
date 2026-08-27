from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import runpy
from typing import Callable, cast

from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    CatalogFeasibilityStatusV2341,
    RegistrationOptionCatalogV2341,
    build_registration_option_catalog_v2341,
    evaluate_catalog_feasibility_v2341,
)
from ecomsre.dta_v2.v23.registration_development_v234 import (
    build_connection_pool_review_item_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    build_provider_core_ontology_view_v234,
    build_registration_draft_provider_request_v234,
    project_development_report_v234,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    TEST_REVIEWER_V23,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)


def _connection_pool_request(tmp_path: Path):
    local_root = tmp_path / ".local" / "dta-v2341"
    item = build_connection_pool_review_item_v234(
        repository_root=ROOT,
        queued_at=NOW,
    )
    review_store = LocalReviewStoreV23(local_root)
    review_store.enqueue(item)
    accepted = review_store.decide(
        report_id=item.report.report_id,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW: accept bounded pool evidence.",
        canonical_label="connection-pool-exhaustion",
        merge_target=None,
        requested_observations=(),
        reviewed_at=NOW,
    )
    assert accepted.shadow_entry is not None
    authorization = LocalOntologyExpansionStoreV234(
        local_root
    ).authorize_draft_generation(
        shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
        reviewer=TEST_REVIEWER_V23,
        authorization_note="SIMULATED HUMAN REVIEW: formal draft only.",
        authorized_at=NOW,
    )
    ontology_view = build_provider_core_ontology_view_v234(
        snapshot=authorization.core_ontology_snapshot,
    )
    request = build_registration_draft_provider_request_v234(
        authorization_context=authorization,
        shadow_fault=accepted.shadow_entry,
        accepted_reports=(project_development_report_v234(item),),
        ontology_view=ontology_view,
    )
    return request


def test_runtime_builds_canonical_catalog_without_provider_or_truth(
    tmp_path: Path,
) -> None:
    request = _connection_pool_request(tmp_path)

    first = build_registration_option_catalog_v2341(request=request)
    second = build_registration_option_catalog_v2341(request=request)
    feasibility = evaluate_catalog_feasibility_v2341(catalog=first)

    assert first == second
    example = RegistrationOptionCatalogV2341.model_validate_json(
        (
            ROOT
            / "config/dta-v2341/examples/registration-option-catalog.json"
        ).read_bytes()
    )
    assert example == first
    assert tuple(item.disposition_alias for item in first.disposition_options) == (
        "D00",
        "D01",
        "D02",
        "D03",
    )
    assert tuple(item.predicate_alias for item in first.predicate_options) == tuple(
        f"P{ordinal:02d}" for ordinal in range(len(first.predicate_options))
    )
    assert tuple(item.clause_alias for item in first.clause_options) == tuple(
        f"C{ordinal:02d}" for ordinal in range(len(first.clause_options))
    )
    assert len(first.predicate_options) <= 12
    assert len(first.clause_options) <= 24
    assert all(item.draft.extraction_rule is not None for item in first.predicate_options)
    assert all(item.requirements for item in first.clause_options)
    assert all(
        item.requirements
        == tuple(
            sorted(
                item.requirements,
                key=lambda requirement: (
                    requirement.predicate_name,
                    requirement.service_binding.value,
                    requirement.require_exact_parent,
                ),
            )
        )
        for item in first.clause_options
    )
    provider_projection = json.dumps(
        first.provider_projection(),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "extraction_rule" not in provider_projection
    assert "threshold_rule" not in provider_projection
    assert "clause_id" not in provider_projection
    assert "evaluator" not in provider_projection.casefold()
    assert first.provider_calls == 0
    assert feasibility.status is CatalogFeasibilityStatusV2341.PASS
    assert feasibility.terminal == "DTA_V2341_CATALOG_FEASIBILITY_PASS"


def test_predecessor_history_is_frozen_and_successor_descends_from_it() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/ci/verify_dta_v2341_history.py"))
    verify = cast(Callable[[], None], namespace["verify"])

    verify()

    blocker = json.loads(
        (ROOT / "docs/analysis/dta-v234-provider-blocker.json").read_text(
            encoding="utf-8"
        )
    )
    assert blocker["status"] == "BLOCKED_DTA_V234_PROVIDER"
    assert blocker["provider_smoke"]["execution_count"] == 1
    assert blocker["provider_smoke"]["real_fix_count"] == 2
    assert blocker["fixed_evaluation_execution_count"] == 0
    assert blocker["measured_result_terminal"] is None
