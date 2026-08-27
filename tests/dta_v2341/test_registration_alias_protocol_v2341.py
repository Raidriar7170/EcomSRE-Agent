from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import runpy
from typing import Callable, cast

from ecomsre.dta_v2.v23.cli import main
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    CatalogFeasibilityStatusV2341,
    RegistrationOptionCatalogV2341,
    build_registration_option_catalog_v2341,
    evaluate_catalog_feasibility_v2341,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    RegistrationAliasProviderV2341,
    RegistrationAliasSelectionV2341,
    build_registration_alias_provider_request_v2341,
)
from ecomsre.dta_v2.v23.registration_assembler_v2341 import (
    RegistrationValidationContextV2341,
    assemble_formal_registration_draft_v2341,
    validate_registration_draft_in_context_v2341,
)
from ecomsre.dta_v2.v23.registration_development_v234 import (
    build_connection_pool_review_item_v234,
)
from ecomsre.dta_v2.v23.registration_development_gate_v2341 import (
    run_predecessor_development_gate_v2341,
)
from ecomsre.dta_v2.v23.registration_store_v2341 import (
    LocalRegistrationAliasStoreV2341,
)
from ecomsre.dta_v2.v23.evaluation_v234 import (
    _prepare_authorized_task_v234,
    load_core_schema_views_v234,
    load_registration_tasks_v234,
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


def _connection_pool_context(tmp_path: Path):
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
    return request, authorization, accepted.shadow_entry, item


def test_runtime_builds_canonical_catalog_without_provider_or_truth(
    tmp_path: Path,
) -> None:
    request, _authorization, _shadow, _item = _connection_pool_context(tmp_path)

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


def test_alias_selection_has_exactly_six_fields_and_order_is_runtime_owned(
    tmp_path: Path,
) -> None:
    request, authorization, shadow, item = _connection_pool_context(tmp_path)
    catalog = build_registration_option_catalog_v2341(request=request)
    provider_request = build_registration_alias_provider_request_v2341(
        source_request=request,
        catalog=catalog,
    )
    raw = json.dumps(
        {
            "disposition_alias": "D00",
            "mechanism_concept": "connection pool exhaustion",
            "clause_aliases": ["C02", "C00", "C02"],
            "confusable_aliases": ["M04", "M00", "M04"],
            "engineering_gap_aliases": [],
            "semantic_rationale": (
                "Pool wait evidence and error behavior support a distinct bounded mechanism."
            ),
        }
    )
    provider = RegistrationAliasProviderV2341(transport=lambda _body: raw)

    result = provider.select(request=provider_request, catalog=catalog)
    assembly = assemble_formal_registration_draft_v2341(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        catalog=catalog,
        provider_result=result,
        validation_context=RegistrationValidationContextV2341.PRODUCTION_REGISTRATION,
    )
    contextual = validate_registration_draft_in_context_v2341(
        draft=assembly.formal_draft,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        context=RegistrationValidationContextV2341.PRODUCTION_REGISTRATION,
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert set(RegistrationAliasSelectionV2341.model_fields) == {
        "disposition_alias",
        "mechanism_concept",
        "clause_aliases",
        "confusable_aliases",
        "engineering_gap_aliases",
        "semantic_rationale",
    }
    assert result.selection.clause_aliases == ("C00", "C02")
    assert result.selection.confusable_aliases == ("M00", "M04")
    assert result.trace.provider_calls == 1
    assert result.trace.protocol_repairs == 0
    provider_payload = json.dumps(provider_request.provider_payload(), sort_keys=True)
    assert "sha256" not in provider_payload
    assert "extraction_rule" not in provider_payload
    assert "service_binding" not in provider_payload
    assert "evidence_ref" not in provider_payload
    assert assembly.formal_draft.action_authority == "NONE"
    assert assembly.formal_draft.repository_write_authority == "NONE"
    assert tuple(
        item.predicate_name for item in assembly.formal_draft.predicates
    ) == tuple(
        sorted(item.predicate_name for item in assembly.formal_draft.predicates)
    )
    assert contextual.production_validation.status.value == "VALID"
    assert contextual.promotion_eligible is True


def test_alias_provider_rejects_unknown_alias_after_at_most_two_repairs(
    tmp_path: Path,
) -> None:
    request, _authorization, _shadow, _item = _connection_pool_context(tmp_path)
    catalog = build_registration_option_catalog_v2341(request=request)
    provider_request = build_registration_alias_provider_request_v2341(
        source_request=request,
        catalog=catalog,
    )
    responses = iter(
        json.dumps(
            {
                "disposition_alias": "D00",
                "mechanism_concept": "connection pool exhaustion",
                "clause_aliases": ["C99"],
                "confusable_aliases": [],
                "engineering_gap_aliases": [],
                "semantic_rationale": "Accepted evidence supports one bounded mechanism.",
            }
        )
        for _ in range(3)
    )
    provider = RegistrationAliasProviderV2341(transport=lambda _body: next(responses))

    try:
        provider.select(request=provider_request, catalog=catalog)
    except RuntimeError as exc:
        assert "exhausted two protocol repairs" in str(exc)
    else:
        raise AssertionError("unknown clause alias unexpectedly passed")


def test_hidden_known_collision_is_reconstruction_evidence_not_promotion(
    tmp_path: Path,
) -> None:
    task = load_registration_tasks_v234(
        ROOT / "config/dta-v234/evaluation/tasks.json"
    ).require("rt-001")
    item, shadow, authorization = _prepare_authorized_task_v234(
        repository_root=ROOT,
        task=task,
        local_root=tmp_path / ".local" / "hidden-known",
    )
    ontology_view = load_core_schema_views_v234(
        ROOT / "config/dta-v234/evaluation/core-schema-snapshot.json"
    ).require("rt-001")
    source_request = build_registration_draft_provider_request_v234(
        authorization_context=authorization,
        shadow_fault=shadow,
        accepted_reports=(project_development_report_v234(item),),
        ontology_view=ontology_view,
    )
    catalog = build_registration_option_catalog_v2341(request=source_request)
    provider_request = build_registration_alias_provider_request_v2341(
        source_request=source_request,
        catalog=catalog,
    )
    raw = json.dumps(
        {
            "disposition_alias": "D00",
            "mechanism_concept": "configuration error",
            "clause_aliases": ["C00"],
            "confusable_aliases": [],
            "engineering_gap_aliases": [],
            "semantic_rationale": (
                "Recent change and error evidence reconstruct a bounded mechanism."
            ),
        }
    )
    result = RegistrationAliasProviderV2341(
        transport=lambda _body: raw
    ).select(request=provider_request, catalog=catalog)
    assembly = assemble_formal_registration_draft_v2341(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        catalog=catalog,
        provider_result=result,
        validation_context=(
            RegistrationValidationContextV2341.HIDDEN_KNOWN_RECONSTRUCTION
        ),
    )

    contextual = validate_registration_draft_in_context_v2341(
        draft=assembly.formal_draft,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        context=RegistrationValidationContextV2341.HIDDEN_KNOWN_RECONSTRUCTION,
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert contextual.production_validation.status.value == "INVALID"
    assert "CORE_MECHANISM_COLLISION" in contextual.collision_evidence_codes
    assert contextual.reconstruction_valid is True
    assert contextual.context_pass is True
    assert contextual.promotion_eligible is False


def test_alias_protocol_is_default_cli_and_selection_can_be_reassembled(
    tmp_path: Path,
    capsys,
) -> None:
    _request, authorization, _shadow, _item = _connection_pool_context(tmp_path)
    local_root = tmp_path / ".local" / "dta-v2341"
    authorization_id = authorization.authorization.authorization_id

    assert main(
        (
            "ontology",
            "catalog",
            authorization_id,
            "--local-root",
            str(local_root),
        )
    ) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert catalog["provider_calls"] == 0

    assert main(
        (
            "ontology",
            "generate-draft",
            authorization_id,
            "--development-fixture",
            "--local-root",
            str(local_root),
        )
    ) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["provider_trace"]["schema_version"] == (
        "dta-v2341.registration-alias-provider-trace.v1"
    )
    assert draft["provider_trace"]["provider_calls"] == 0

    assert main(
        (
            "ontology",
            "show-selection",
            draft["draft_id"],
            "--local-root",
            str(local_root),
        )
    ) == 0
    result = json.loads(capsys.readouterr().out)
    selection_id = (
        "selection-v2341-"
        + result["trace"]["canonical_selection_sha256"][:16]
    )
    assert set(result["selection"]) == {
        "disposition_alias",
        "mechanism_concept",
        "clause_aliases",
        "confusable_aliases",
        "engineering_gap_aliases",
        "semantic_rationale",
    }

    assert main(
        (
            "ontology",
            "assemble-draft",
            selection_id,
            "--local-root",
            str(local_root),
        )
    ) == 0
    assert json.loads(capsys.readouterr().out)["draft_id"] == draft["draft_id"]
    assert LocalRegistrationAliasStoreV2341(local_root).load_assembly(
        draft["draft_id"]
    ).canonical_order_failures == 0


def test_predecessor_failed_roles_pass_bounded_alias_development_gate() -> None:
    gate = run_predecessor_development_gate_v2341(repository_root=ROOT)

    assert gate.terminal == "DTA_V2341_PREDECESSOR_DEVELOPMENT_GATE_PASS"
    assert gate.protocol_valid_role_count == 5
    assert gate.hidden_known_reconstruction_pass_count == 2
    assert gate.declarative_ready_valid_count == 2
    assert gate.engineering_required_gap_count == 1
    assert gate.duplicate_control_provider_calls == 0
    assert gate.insufficient_control_provider_calls == 0
    assert gate.rt_011_canonical_order_failure_eliminated is True
    assert gate.action_authority_violations == 0
