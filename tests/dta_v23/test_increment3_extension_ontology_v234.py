from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import shutil
from typing import get_args

import pytest

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v23.extension_registry_v234 import (
    ExtensionOntologyRegistryV234,
    LocalExtensionOntologyStoreV234,
    OntologyDraftReviewDecisionV234,
    OntologyPromotionDecisionV234,
    build_ontology_draft_review_v234,
)
from ecomsre.dta_v2.v23.extension_runtime_v234 import (
    ExtensionAdmittedDiagnosisV234,
    ExtensionDiagnosisRouteV234,
    EXTENSION_RUNTIME_RULE_TYPES_V234,
    assert_extension_diagnosis_non_actionable_v234,
    diagnose_extension_enabled_v234,
)
from ecomsre.dta_v2.v23.cli import main
import ecomsre.dta_v2.v23.cli as cli_v23
from ecomsre.dta_v2.v23.ontology_expansion_v234 import OntologyExpansionStateV234
from ecomsre.dta_v2.v23.discovery_runtime import (
    assert_v23_artifact_is_non_actionable,
)
from ecomsre.dta_v2.v23.registration_development_v234 import (
    build_increment3_development_shadow_v234,
    run_increment2_development_demo_v234,
    run_increment3_development_demo_v234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    ExtensionPredicateRuleV234,
)
from ecomsre.dta_v2.v23.registration_evaluator_v234 import (
    ExtensionShadowEvaluationStatusV234,
    build_increment3_development_shadow_cases_v234,
    build_shadow_evaluation_case_v234,
    evaluate_extension_shadow_v234,
    evaluate_increment3_development_shadow_v234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
)
from ecomsre.dta_v2.v23.review_registry import LocalReviewStoreV23, TEST_REVIEWER_V23


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _increment2(tmp_path: Path):
    return run_increment2_development_demo_v234(
        repository_root=ROOT,
        local_root=tmp_path / ".local" / "dta-v234",
        run_at=NOW,
    )


def _approved(tmp_path: Path):
    demo = _increment2(tmp_path)
    review = build_ontology_draft_review_v234(
        draft=demo.formal_draft,
        validation=demo.validation,
        decision=OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION,
        reviewer=TEST_REVIEWER_V23,
        review_note=(
            "SIMULATED HUMAN REVIEW: approve isolated shadow evaluation only."
        ),
        requested_changes=(),
        reviewed_at=NOW,
    )
    return demo, review


def _approved_with_two_reports(tmp_path: Path):
    demo, shadow = build_increment3_development_shadow_v234(
        repository_root=ROOT,
        local_root=tmp_path / ".local" / "dta-v234",
        run_at=NOW,
    )
    review = build_ontology_draft_review_v234(
        draft=demo.formal_draft,
        validation=demo.validation,
        decision=OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION,
        reviewer=TEST_REVIEWER_V23,
        review_note=(
            "SIMULATED HUMAN REVIEW: approve isolated shadow evaluation only."
        ),
        requested_changes=(),
        reviewed_at=NOW,
    )
    return demo, review, shadow


def _accepted_reports(tmp_path: Path, shadow):
    store = LocalReviewStoreV23(tmp_path / ".local" / "dta-v234")
    return tuple(store.load_item(report_id) for report_id in shadow.positive_report_ids)


def test_draft_review_is_explicit_bound_and_simulated(tmp_path: Path) -> None:
    demo, review = _approved(tmp_path)

    assert demo.validation.status is DraftValidationStatusV234.VALID
    assert review.decision is OntologyDraftReviewDecisionV234.APPROVE_SHADOW_EVALUATION
    assert review.simulation is True
    assert review.draft_sha256 == demo.formal_draft.draft_sha256
    assert review.validation_sha256 == demo.validation.validation_sha256

    with pytest.raises(ValueError, match="requested changes"):
        build_ontology_draft_review_v234(
            draft=demo.formal_draft,
            validation=demo.validation,
            decision=OntologyDraftReviewDecisionV234.REQUEST_DRAFT_REVISION,
            reviewer=TEST_REVIEWER_V23,
            review_note="SIMULATED HUMAN REVIEW: request a bounded revision.",
            requested_changes=(),
            reviewed_at=NOW,
        )


def test_extension_runtime_is_total_over_the_bounded_dsl_union() -> None:
    annotated_union = get_args(ExtensionPredicateRuleV234)[0]
    assert set(get_args(annotated_union)) == set(EXTENSION_RUNTIME_RULE_TYPES_V234)


def test_increment3_shadow_passes_and_isolated_registry_stays_empty(
    tmp_path: Path,
) -> None:
    demo, review, shadow = _approved_with_two_reports(tmp_path)
    result = evaluate_increment3_development_shadow_v234(
        repository_root=ROOT,
        compiled=demo.compiled_registration,
        draft_review=review,
        shadow=shadow,
        accepted_reports=_accepted_reports(tmp_path, shadow),
        evaluated_at=NOW,
    )

    assert result.status is ExtensionShadowEvaluationStatusV234.PROMOTION_READY
    assert result.accepted_positive_report_count == 2
    assert result.positive_replay_case_count >= 1
    assert result.positive_recall >= 0.75
    assert result.false_positive_rate <= 0.10
    assert result.core_known_overlap == 0
    assert result.no_incident_regression == 0
    assert result.other_extension_destructive_overlap == 0
    assert result.evidence_ref_validity == 1.0
    assert result.source_reachability == 1.0
    assert result.counterfactual_consistency >= 0.80
    assert result.action_authority_violations == 0
    assert ExtensionOntologyRegistryV234.empty().entries == ()

    one_report_path = tmp_path / "one-report"
    one_report_demo, one_report_review = _approved(one_report_path)
    retained = evaluate_increment3_development_shadow_v234(
        repository_root=ROOT,
        compiled=one_report_demo.compiled_registration,
        draft_review=one_report_review,
        shadow=one_report_demo.shadow_fault,
        accepted_reports=_accepted_reports(
            one_report_path, one_report_demo.shadow_fault
        ),
        evaluated_at=NOW,
    )
    assert retained.status is ExtensionShadowEvaluationStatusV234.RETAINED_FAILED


def test_shadow_evidence_is_bound_and_positive_replays_must_be_disjoint(
    tmp_path: Path,
) -> None:
    demo, review = _approved(tmp_path)
    cases = build_increment3_development_shadow_cases_v234(
        repository_root=ROOT,
        compiled=demo.compiled_registration,
    )
    positive = next(item for item in cases if item.stratum.value == "POSITIVE_INCIDENT")
    duplicates = tuple(
        build_shadow_evaluation_case_v234(
            evaluation_case_id=f"duplicate-positive-{ordinal}",
            stratum=positive.stratum,
            runtime_input=positive.runtime_input,
            target_services=positive.target_services,
            expected_root_service=positive.expected_root_service,
        )
        for ordinal in (1, 2)
    )
    with pytest.raises(ValueError, match="not disjoint"):
        evaluate_extension_shadow_v234(
            compiled=demo.compiled_registration,
            draft_review=review,
            shadow=demo.shadow_fault,
            accepted_reports=_accepted_reports(tmp_path, demo.shadow_fault),
            cases=tuple(sorted((*cases, *duplicates), key=lambda item: item.evaluation_case_id)),
            evaluated_at=NOW,
        )
    two_report_path = tmp_path / "two-report-binding"
    bound_demo, bound_review, bound_shadow = _approved_with_two_reports(
        two_report_path
    )
    bound_cases = build_increment3_development_shadow_cases_v234(
        repository_root=ROOT,
        compiled=bound_demo.compiled_registration,
    )
    with pytest.raises(ValueError, match="accepted report artifacts differ"):
        evaluate_extension_shadow_v234(
            compiled=bound_demo.compiled_registration,
            draft_review=bound_review,
            shadow=bound_shadow,
            accepted_reports=_accepted_reports(two_report_path, bound_shadow)[:1],
            cases=bound_cases,
            evaluated_at=NOW,
        )


def test_promotion_requires_bound_review_and_shadow_result(tmp_path: Path) -> None:
    demo, review, accepted_shadow = _approved_with_two_reports(tmp_path)
    shadow = evaluate_increment3_development_shadow_v234(
        repository_root=ROOT,
        compiled=demo.compiled_registration,
        draft_review=review,
        shadow=accepted_shadow,
        accepted_reports=_accepted_reports(tmp_path, accepted_shadow),
        evaluated_at=NOW,
    )
    store = LocalExtensionOntologyStoreV234(tmp_path / ".local" / "dta-v234")
    store.save_draft_review(review)
    store.save_shadow_result(shadow)
    wrong_shadow = accepted_shadow.model_copy(
        update={"shadow_fault_id": "shadow-v23-0000000000000000"}
    )
    with pytest.raises(ValueError, match="bound draft approval"):
        store.promote(
            compiled=demo.compiled_registration,
            validation=demo.validation,
            draft_review=review,
            shadow_result=shadow,
            shadow=wrong_shadow,
            decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
            reviewer=TEST_REVIEWER_V23,
            review_note="SIMULATED HUMAN REVIEW: reject a mismatched Shadow Fault.",
            reviewed_at=NOW,
        )
    entry, promotion = store.promote(
        compiled=demo.compiled_registration,
        validation=demo.validation,
        draft_review=review,
        shadow_result=shadow,
        shadow=accepted_shadow,
        decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
        reviewer=TEST_REVIEWER_V23,
        review_note=(
            "SIMULATED HUMAN REVIEW: promote the passing development registration."
        ),
        reviewed_at=NOW,
    )

    assert promotion.simulation is True
    assert entry.status == "ACTIVE"
    assert entry.remediation_authority == "NONE"
    assert store.load_registry().entries == (entry,)

    mismatched = shadow.model_copy(update={"source_compiled_sha256": "0" * 64})
    with pytest.raises(ValueError, match="unchanged passing shadow result"):
        LocalExtensionOntologyStoreV234(
            tmp_path / "separate" / ".local" / "dta-v234"
        ).promote(
            compiled=demo.compiled_registration,
            validation=demo.validation,
            draft_review=review,
            shadow_result=mismatched,
            shadow=accepted_shadow,
            decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
            reviewer=TEST_REVIEWER_V23,
            review_note="SIMULATED HUMAN REVIEW: reject changed shadow binding.",
            reviewed_at=NOW,
        )

    with pytest.raises(ValueError, match="already exists"):
        store.promote(
            compiled=demo.compiled_registration,
            validation=demo.validation,
            draft_review=review,
            shadow_result=shadow,
            shadow=accepted_shadow,
            decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
            reviewer=TEST_REVIEWER_V23,
            review_note="SIMULATED HUMAN REVIEW: duplicate promotion must fail.",
            reviewed_at=NOW,
        )


def test_revocation_preserves_history_and_disables_admission(tmp_path: Path) -> None:
    demo, review, accepted_shadow = _approved_with_two_reports(tmp_path)
    shadow = evaluate_increment3_development_shadow_v234(
        repository_root=ROOT,
        compiled=demo.compiled_registration,
        draft_review=review,
        shadow=accepted_shadow,
        accepted_reports=_accepted_reports(tmp_path, accepted_shadow),
        evaluated_at=NOW,
    )
    store = LocalExtensionOntologyStoreV234(tmp_path / ".local" / "dta-v234")
    store.save_draft_review(review)
    store.save_shadow_result(shadow)
    entry, _ = store.promote(
        compiled=demo.compiled_registration,
        validation=demo.validation,
        draft_review=review,
        shadow_result=shadow,
        shadow=accepted_shadow,
        decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW: promote before revocation coverage.",
        reviewed_at=NOW,
    )

    revoked, revocation = store.revoke(
        registration_id=entry.registration_id,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW: revoke the development registration.",
        reviewed_at=NOW,
    )

    assert revocation.decision is (
        OntologyPromotionDecisionV234.REVOKE_EXTENSION_REGISTRATION
    )
    assert revoked.status == "REVOKED"
    assert revoked.promotion_record_sha256 == entry.promotion_record_sha256
    assert revoked.revocation_record_sha256 == revocation.review_sha256
    assert len(store.load_registry().entries) == 1

    routed = diagnose_extension_enabled_v234(
        repository_root=ROOT,
        case_id="vx-312",
        registry=store.load_registry(),
        core_known_diagnosis=None,
        no_incident_admitted=False,
    )
    assert routed.route is ExtensionDiagnosisRouteV234.OPEN_WORLD


def test_extension_diagnosis_is_non_actionable_and_core_has_priority(
    tmp_path: Path,
) -> None:
    demo, review, accepted_shadow = _approved_with_two_reports(tmp_path)
    shadow = evaluate_increment3_development_shadow_v234(
        repository_root=ROOT,
        compiled=demo.compiled_registration,
        draft_review=review,
        shadow=accepted_shadow,
        accepted_reports=_accepted_reports(tmp_path, accepted_shadow),
        evaluated_at=NOW,
    )
    store = LocalExtensionOntologyStoreV234(tmp_path / ".local" / "dta-v234")
    store.save_draft_review(review)
    store.save_shadow_result(shadow)
    entry, _ = store.promote(
        compiled=demo.compiled_registration,
        validation=demo.validation,
        draft_review=review,
        shadow_result=shadow,
        shadow=accepted_shadow,
        decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW: promote for runtime coverage.",
        reviewed_at=NOW,
    )

    routed = diagnose_extension_enabled_v234(
        repository_root=ROOT,
        case_id="vx-312",
        registry=store.load_registry(),
        core_known_diagnosis=None,
        no_incident_admitted=False,
    )
    assert routed.route is ExtensionDiagnosisRouteV234.EXTENSION
    assert isinstance(routed.extension_diagnosis, ExtensionAdmittedDiagnosisV234)
    assert routed.extension_diagnosis.terminal == "REGISTERED_EXTENSION_DIAGNOSIS"
    assert routed.extension_diagnosis.mechanism_slug == entry.mechanism_slug
    assert routed.extension_diagnosis.action_authority == "NONE"
    assert routed.open_world_provider_calls == 0
    with pytest.raises(TypeError, match="non-actionable"):
        assert_extension_diagnosis_non_actionable_v234(
            routed.extension_diagnosis
        )
    with pytest.raises(TypeError, match="non-actionable"):
        assert_v23_artifact_is_non_actionable(routed.extension_diagnosis)

    core = diagnose_extension_enabled_v234(
        repository_root=ROOT,
        case_id="vx-312",
        registry=store.load_registry(),
        core_known_diagnosis=MechanismV22.DEPENDENCY_LATENCY,
        no_incident_admitted=False,
    )
    assert core.route is ExtensionDiagnosisRouteV234.CORE_KNOWN
    assert core.core_known_diagnosis is MechanismV22.DEPENDENCY_LATENCY
    assert core.extension_diagnosis is None

    no_incident = diagnose_extension_enabled_v234(
        repository_root=ROOT,
        case_id="vx-321",
        registry=ExtensionOntologyRegistryV234.empty(),
        core_known_diagnosis=None,
        no_incident_admitted=True,
    )
    assert no_incident.route is ExtensionDiagnosisRouteV234.NO_INCIDENT

    fallback = diagnose_extension_enabled_v234(
        repository_root=ROOT,
        case_id="vx-313",
        registry=ExtensionOntologyRegistryV234.empty(),
        core_known_diagnosis=None,
        no_incident_admitted=False,
    )
    assert fallback.route is ExtensionDiagnosisRouteV234.OPEN_WORLD
    assert fallback.open_world_required is True


def test_increment3_demo_and_committed_examples_are_bound_and_simulated(
    tmp_path: Path,
) -> None:
    demo = run_increment3_development_demo_v234(
        repository_root=ROOT,
        local_root=tmp_path / ".local" / "dta-v234",
        run_at=NOW,
    )

    assert demo.human_review_label == "SIMULATED HUMAN REVIEW"
    assert demo.accepted_positive_report_count == 2
    assert demo.shadow_evaluation.status is (
        ExtensionShadowEvaluationStatusV234.PROMOTION_READY
    )
    assert demo.diagnosis.terminal == "REGISTERED_EXTENSION_DIAGNOSIS"
    assert demo.open_world_provider_calls == 0
    assert demo.registration_provider_calls == 0

    namespace = runpy.run_path(
        str(ROOT / "scripts/ci/generate_dta_v234_increment3_examples.py")
    )
    expected = namespace["render_increment3_examples_v234"](ROOT)
    assert all((ROOT / path).read_bytes() == content for path, content in expected.items())
    shadow_text = (ROOT / "config/dta-v234/examples/shadow-evaluation.json").read_text(
        encoding="utf-8"
    )
    promotion_text = (ROOT / "config/dta-v234/examples/promotion-record.json").read_text(
        encoding="utf-8"
    )
    registry_text = (ROOT / "config/dta-v234/examples/extension-registry.json").read_text(
        encoding="utf-8"
    )
    assert "SIMULATED HUMAN REVIEW" in shadow_text
    assert "SIMULATED HUMAN REVIEW" in promotion_text
    assert "SIMULATED HUMAN REVIEW" in registry_text


def test_increment3_promotion_diagnosis_and_revocation_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    demo, review, accepted_shadow = _approved_with_two_reports(tmp_path)
    shadow = evaluate_increment3_development_shadow_v234(
        repository_root=ROOT,
        compiled=demo.compiled_registration,
        draft_review=review,
        shadow=accepted_shadow,
        accepted_reports=_accepted_reports(tmp_path, accepted_shadow),
        evaluated_at=NOW,
    )
    local_root = tmp_path / ".local" / "dta-v234"
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    (repository_root / ".git").write_text("gitdir: test\n", encoding="utf-8")
    source_evaluation = ROOT / "config/dta-v233/evaluation"
    target_evaluation = repository_root / "config/dta-v233/evaluation"
    target_evaluation.parent.mkdir(parents=True)
    shutil.copytree(source_evaluation, target_evaluation)
    extension_store = LocalExtensionOntologyStoreV234(local_root)
    extension_store.save_draft_review(review)
    extension_store.save_shadow_result(shadow)

    assert main(
        (
            "ontology",
            "promote",
            demo.formal_draft.draft_id,
            "--reviewer",
            TEST_REVIEWER_V23,
            "--note",
            "SIMULATED HUMAN REVIEW: promote through the CLI contract.",
            "--local-root",
            str(local_root),
            "--repository-root",
            str(repository_root),
        )
    ) == 0
    promoted = json.loads(capsys.readouterr().out)
    registration_id = promoted["entry"]["registration_id"]
    assert promoted["entry"]["status"] == "ACTIVE"

    assert main(
        (
            "diagnose",
            "--case",
            "vx-312",
            "--policy",
            "extension-enabled",
            "--repository-root",
            str(repository_root),
            "--local-root",
            str(local_root),
        )
    ) == 0
    diagnosed = json.loads(capsys.readouterr().out)
    assert diagnosed["route"] == "EXTENSION"
    assert diagnosed["open_world_provider_calls"] == 0

    assert main(
        (
            "ontology",
            "revoke",
            registration_id,
            "--reviewer",
            TEST_REVIEWER_V23,
            "--note",
            "SIMULATED HUMAN REVIEW: revoke through the CLI contract.",
            "--local-root",
            str(local_root),
            "--repository-root",
            str(repository_root),
        )
    ) == 0
    revoked = json.loads(capsys.readouterr().out)
    assert revoked["entry"]["status"] == "REVOKED"
    local_store = LocalExtensionOntologyStoreV234(local_root)
    transitions = local_store.list_transitions(
        draft_id=demo.formal_draft.draft_id
    )
    assert {item.to_state for item in transitions} == {
        OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED,
        OntologyExpansionStateV234.PROMOTION_READY,
    }
    by_state = {item.to_state: item for item in transitions}
    assert by_state[
        OntologyExpansionStateV234.PROMOTION_READY
    ].previous_transition_sha256 == by_state[
        OntologyExpansionStateV234.SHADOW_EVALUATION_APPROVED
    ].transition_sha256
    tracked_registry = LocalExtensionOntologyStoreV234(
        local_root,
        repository_root=repository_root,
    ).load_registry()
    commits_by_state = {
        item.to_state: item for item in tracked_registry.transition_commits
    }
    assert set(commits_by_state) == {
        OntologyExpansionStateV234.PROMOTED_EXTENSION,
        OntologyExpansionStateV234.REVOKED,
    }
    assert commits_by_state[
        OntologyExpansionStateV234.PROMOTED_EXTENSION
    ].previous_transition_sha256 == by_state[
        OntologyExpansionStateV234.PROMOTION_READY
    ].transition_sha256
    assert commits_by_state[
        OntologyExpansionStateV234.REVOKED
    ].previous_transition_sha256 == commits_by_state[
        OntologyExpansionStateV234.PROMOTED_EXTENSION
    ].transition_sha256


def test_extension_cli_uses_full_core_ontology_before_extension(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(
        (
            "diagnose",
            "--case",
            "vx-301",
            "--policy",
            "extension-enabled",
            "--repository-root",
            str(ROOT),
            "--local-root",
            str(tmp_path / ".local" / "dta-v234"),
        )
    ) == 0
    diagnosed = json.loads(capsys.readouterr().out)
    assert diagnosed["route"] == "CORE_KNOWN"
    assert diagnosed["core_known_diagnosis"] == "CONFIGURATION_ERROR"


def test_cli_validation_loads_active_registry_slugs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo, review, accepted_shadow = _approved_with_two_reports(tmp_path)
    shadow = evaluate_increment3_development_shadow_v234(
        repository_root=ROOT,
        compiled=demo.compiled_registration,
        draft_review=review,
        shadow=accepted_shadow,
        accepted_reports=_accepted_reports(tmp_path, accepted_shadow),
        evaluated_at=NOW,
    )
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    (repository_root / ".git").write_text("gitdir: test\n", encoding="utf-8")
    store = LocalExtensionOntologyStoreV234(
        tmp_path / ".local" / "dta-v234",
        repository_root=repository_root,
    )
    store.save_draft_review(review)
    store.save_shadow_result(shadow)
    store.promote(
        compiled=demo.compiled_registration,
        validation=demo.validation,
        draft_review=review,
        shadow_result=shadow,
        shadow=accepted_shadow,
        decision=OntologyPromotionDecisionV234.PROMOTE_TO_EXTENSION_ONTOLOGY,
        reviewer=TEST_REVIEWER_V23,
        review_note="SIMULATED HUMAN REVIEW: promote for collision coverage.",
        reviewed_at=NOW,
    )
    captured: dict[str, tuple[str, ...]] = {}

    def _capture_validation(**kwargs):
        captured["promoted"] = kwargs["promoted_mechanism_slugs"]
        return demo.validation

    monkeypatch.setattr(cli_v23, "validate_registration_draft_v234", _capture_validation)
    monkeypatch.setattr(
        cli_v23.LocalRegistrationDraftStoreV234,
        "record_validation",
        lambda *args, **kwargs: None,
    )
    assert main(
        (
            "ontology",
            "validate-draft",
            demo.formal_draft.draft_id,
            "--local-root",
            str(tmp_path / ".local" / "dta-v234"),
            "--repository-root",
            str(repository_root),
        )
    ) == 0
    capsys.readouterr()
    assert captured["promoted"] == ("connection-pool-exhaustion",)


def test_extension_store_rejects_unbounded_write_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must end with"):
        LocalExtensionOntologyStoreV234(tmp_path / "arbitrary")
    parser = cli_v23.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            (
                "ontology",
                "promote",
                "draft-v234-0000000000000000",
                "--reviewer",
                TEST_REVIEWER_V23,
                "--note",
                "SIMULATED HUMAN REVIEW: invalid arbitrary write path.",
                "--registry-path",
                str(tmp_path / "arbitrary.json"),
            )
        )
