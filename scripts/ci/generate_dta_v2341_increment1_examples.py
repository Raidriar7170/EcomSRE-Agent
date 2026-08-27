#!/usr/bin/env python3
"""Generate the deterministic v2.3.4.1 increment-1 catalog example."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile

from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    build_registration_option_catalog_v2341,
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
OUTPUT = ROOT / "config/dta-v2341/examples/registration-option-catalog.json"
NOW = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dta-v2341-catalog-") as directory:
        local_root = Path(directory)
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
        if accepted.shadow_entry is None:
            raise ValueError("development acceptance lacks a Shadow Fault")
        authorization = LocalOntologyExpansionStoreV234(
            local_root
        ).authorize_draft_generation(
            shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
            reviewer=TEST_REVIEWER_V23,
            authorization_note="SIMULATED HUMAN REVIEW: formal draft only.",
            authorized_at=NOW,
        )
        request = build_registration_draft_provider_request_v234(
            authorization_context=authorization,
            shadow_fault=accepted.shadow_entry,
            accepted_reports=(project_development_report_v234(item),),
            ontology_view=build_provider_core_ontology_view_v234(
                snapshot=authorization.core_ontology_snapshot,
            ),
        )
        catalog = build_registration_option_catalog_v2341(request=request)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(
            catalog.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        print(catalog.catalog_sha256)


if __name__ == "__main__":
    main()
