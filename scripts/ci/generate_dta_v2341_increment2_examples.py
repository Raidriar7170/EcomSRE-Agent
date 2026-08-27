#!/usr/bin/env python3
"""Generate deterministic v2.3.4.1 alias and assembly examples."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from ecomsre.dta_v2.v23.evaluation_v234 import (
    _prepare_authorized_task_v234,
    load_core_schema_views_v234,
    load_registration_tasks_v234,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    RegistrationAliasProviderV2341,
    build_registration_alias_provider_request_v2341,
    build_registration_alias_source_request_v2341,
)
from ecomsre.dta_v2.v23.registration_assembler_v2341 import (
    RegistrationValidationContextV2341,
    assemble_formal_registration_draft_v2341,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    build_registration_option_catalog_v2341,
)
from ecomsre.dta_v2.v23.registration_development_gate_v2341 import (
    run_predecessor_development_gate_v2341,
)
from ecomsre.dta_v2.v23.registration_development_v234 import (
    build_connection_pool_review_item_v234,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    TEST_REVIEWER_V23,
)


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "config/dta-v2341/examples"
GATE_OUTPUT = ROOT / "docs/analysis/dta-v2341-predecessor-development-gate.json"
NOW = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dta-v2341-increment2-") as raw:
        local_root = Path(raw)
        item = build_connection_pool_review_item_v234(
            repository_root=ROOT,
            queued_at=NOW,
        )
        review_store = LocalReviewStoreV23(local_root / "declarative")
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
            local_root / "declarative"
        ).authorize_draft_generation(
            shadow_fault_id=accepted.shadow_entry.shadow_fault_id,
            reviewer=TEST_REVIEWER_V23,
            authorization_note="SIMULATED HUMAN REVIEW: formal draft only.",
            authorized_at=NOW,
        )
        source_request = build_registration_alias_source_request_v2341(
            authorization_context=authorization,
            shadow=accepted.shadow_entry,
            accepted_reports=(item,),
        )
        catalog = build_registration_option_catalog_v2341(request=source_request)
        provider_request = build_registration_alias_provider_request_v2341(
            source_request=source_request,
            catalog=catalog,
        )
        clause_aliases = tuple(
            item.clause_alias for item in reversed(catalog.clause_options[:3])
        )
        confusable_aliases = tuple(
            item.confusable_alias for item in reversed(catalog.confusable_options[:2])
        )
        raw_selection = json.dumps(
            {
                "disposition_alias": "D00",
                "mechanism_concept": "connection pool exhaustion",
                "clause_aliases": [*clause_aliases, *clause_aliases[:1]],
                "confusable_aliases": [
                    *confusable_aliases,
                    *confusable_aliases[:1],
                ],
                "engineering_gap_aliases": [],
                "semantic_rationale": (
                    "Pool wait evidence and error behavior support a distinct bounded mechanism."
                ),
            }
        )
        provider_result = RegistrationAliasProviderV2341(
            transport=lambda _body: raw_selection
        ).select(request=provider_request, catalog=catalog)
        assembly = assemble_formal_registration_draft_v2341(
            authorization_context=authorization,
            shadow=accepted.shadow_entry,
            accepted_reports=(item,),
            catalog=catalog,
            provider_result=provider_result,
            validation_context=(
                RegistrationValidationContextV2341.PRODUCTION_REGISTRATION
            ),
        )

        tasks = load_registration_tasks_v234(
            ROOT / "config/dta-v234/evaluation/tasks.json"
        )
        views = load_core_schema_views_v234(
            ROOT / "config/dta-v234/evaluation/core-schema-snapshot.json"
        )
        engineering_task = tasks.require("rt-014")
        eng_item, eng_shadow, eng_authorization = _prepare_authorized_task_v234(
            repository_root=ROOT,
            task=engineering_task,
            local_root=local_root / "engineering",
        )
        eng_source = build_registration_alias_source_request_v2341(
            authorization_context=eng_authorization,
            shadow=eng_shadow,
            accepted_reports=(eng_item,),
            ontology_view=views.require("rt-014"),
        )
        eng_catalog = build_registration_option_catalog_v2341(request=eng_source)
        eng_request = build_registration_alias_provider_request_v2341(
            source_request=eng_source,
            catalog=eng_catalog,
        )
        eng_raw = json.dumps(
            {
                "disposition_alias": "D01",
                "mechanism_concept": "network transport degradation",
                "clause_aliases": [],
                "confusable_aliases": [],
                "engineering_gap_aliases": [
                    item.engineering_gap_alias
                    for item in eng_catalog.engineering_gap_options
                ],
                "semantic_rationale": (
                    "Accepted evidence requires one bounded extraction capability."
                ),
            }
        )
        eng_result = RegistrationAliasProviderV2341(
            transport=lambda _body: eng_raw
        ).select(request=eng_request, catalog=eng_catalog)
        eng_assembly = assemble_formal_registration_draft_v2341(
            authorization_context=eng_authorization,
            shadow=eng_shadow,
            accepted_reports=(eng_item,),
            catalog=eng_catalog,
            provider_result=eng_result,
            validation_context=(
                RegistrationValidationContextV2341.PRODUCTION_REGISTRATION
            ),
        )

        gate = run_predecessor_development_gate_v2341(repository_root=ROOT)
        _write(OUTPUT / "alias-selection.json", provider_result.selection.model_dump(mode="json"))
        _write(
            OUTPUT / "assembled-formal-draft.json",
            assembly.formal_draft.model_dump(mode="json"),
        )
        _write(
            OUTPUT / "engineering-required-draft.json",
            eng_assembly.formal_draft.model_dump(mode="json"),
        )
        _write(GATE_OUTPUT, gate.model_dump(mode="json"))
        print(gate.terminal)


if __name__ == "__main__":
    main()
