from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.memory import LogCategoryV22, PredicateKindV22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    RequirementServiceBindingV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    EvidenceSourceV22,
    MetricKindV22,
    MetricUnitV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.cli import main
from ecomsre.dta_v2.v23.discovery_provider import DiscoveryProviderTransportErrorV23
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    LocalOntologyExpansionStoreV234,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    RegistrationPatchBundleV234,
    compile_registration_v234,
    render_registration_patch_bundle_v234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    FormalPredicateDraftV234,
    FormalFaultRegistrationDraftV234,
    GenericAnomalyKindRuleV234,
    LogCategoryRuleV234,
    LogTemplateContainsAnyRuleV234,
    MetricBaselineRatioRuleV234,
    MetricThresholdRuleV234,
    PredicateImplementationModeV234,
    PredicateRequirementDraftV234,
    RecentChangeStateRuleV234,
    RegistrationImplementationModeV234,
    SupportClauseDraftV234,
    ResourceCpuThresholdRuleV234,
    ResourceMemorySlopeRuleV234,
    RuntimeStateRuleV234,
    TraceDurationThresholdRuleV234,
    TraceFirstErrorAtServiceRuleV234,
    TracePathContainsRuleV234,
    ThresholdComparisonV234,
    mechanism_distinguishing_summary_v234,
    mechanism_display_name_v234,
    mechanism_human_definition_v234,
    predicate_negative_example_v234,
    predicate_positive_example_v234,
    predicate_semantic_definition_v234,
    rebuild_formal_registration_draft_v234,
    support_clause_rationale_v234,
)
from ecomsre.dta_v2.v23.registration_development_v234 import (
    VX311_DEVELOPMENT_EVIDENCE_REFS_V234,
    build_connection_pool_review_item_v234,
    run_increment2_development_demo_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    RegistrationDraftProviderV234,
    build_provider_core_ontology_view_v234,
)
from ecomsre.dta_v2.v23.registration_store_v234 import LocalRegistrationDraftStoreV234
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    FROZEN_DUPLICATE_ABSORPTION_POLICY_V234,
    validate_registration_draft_v234,
)
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    LocalReviewStoreV23,
    TEST_REVIEWER_V23,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)


def _local_root(tmp_path: Path) -> Path:
    return tmp_path / ".local" / "dta-v234"


def _authorized_connection_pool(local_root: Path):
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
        review_note="SIMULATED HUMAN REVIEW: accept connection-pool incident as new.",
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
        authorization_note="SIMULATED HUMAN REVIEW: generate formal draft only.",
        authorized_at=NOW,
    )
    return accepted.shadow_entry, item, authorization


def test_deterministic_provider_validator_and_compiler_close_increment2(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)

    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    validation = validate_registration_draft_v234(
        draft=draft,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )
    compiled = compile_registration_v234(
        draft=draft,
        validation=validation,
        snapshot=authorization.core_ontology_snapshot,
    )
    bundle = render_registration_patch_bundle_v234(
        compiled=compiled,
        output_root=local_root / "registration-bundles",
    )

    assert draft.implementation_mode is RegistrationImplementationModeV234.DECLARATIVE_READY
    assert {predicate.evidence_source for predicate in draft.predicates} == {
        EvidenceSourceV22.LOGS,
        EvidenceSourceV22.METRICS,
    }
    assert validation.status is DraftValidationStatusV234.VALID
    assert validation.errors == ()
    assert compiled.action_authority == "NONE"
    assert bundle.remediation_registration == "NOT_INCLUDED"
    assert bundle.bundle_directory.is_dir()
    assert {path.name for path in bundle.bundle_directory.iterdir()} == {
        "registration-manifest.json",
        "mechanism-definition.json",
        "predicate-definitions.json",
        "dnf-support-policy.json",
        "test-specification.json",
        "patch-plan.md",
        "promotion-checklist.md",
    }
    assert "src/ecomsre/dta_v2/v22" not in (
        bundle.bundle_directory / "patch-plan.md"
    ).read_text(encoding="utf-8")
    manifest = json.loads(
        (bundle.bundle_directory / "registration-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["bundle_payload_sha256"] == bundle.bundle_sha256
    assert len(manifest["artifact_inventory"]) == 7
    for file in bundle.files:
        assert file.content_sha256 == hashlib.sha256(
            (bundle.bundle_directory / file.relative_path).read_bytes()
        ).hexdigest()
    assert render_registration_patch_bundle_v234(
        compiled=compiled,
        output_root=local_root / "registration-bundles",
    ) == bundle
    with pytest.raises(ValueError, match="dta-v234 local root"):
        render_registration_patch_bundle_v234(
            compiled=compiled,
            output_root=tmp_path / "outside",
        )
    with pytest.raises(ValueError, match=r"\.local/dta-v234"):
        render_registration_patch_bundle_v234(
            compiled=compiled,
            output_root=(
                tmp_path / "arbitrary" / "dta-v234" / "registration-bundles"
            ),
        )
    real_local = tmp_path / "real-local"
    real_local.mkdir()
    linked_local = tmp_path / "linked" / ".local"
    linked_local.parent.mkdir()
    linked_local.symlink_to(real_local, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        render_registration_patch_bundle_v234(
            compiled=compiled,
            output_root=linked_local / "dta-v234" / "registration-bundles",
        )


def test_vx311_replay_drives_the_simulated_increment2_demo(tmp_path: Path) -> None:
    item = build_connection_pool_review_item_v234(
        repository_root=ROOT,
        queued_at=NOW,
    )
    assert item.report.supporting_evidence_refs == (
        VX311_DEVELOPMENT_EVIDENCE_REFS_V234
    )

    demo = run_increment2_development_demo_v234(
        repository_root=ROOT,
        local_root=_local_root(tmp_path),
        run_at=NOW,
    )

    assert demo.formal_draft.mechanism.mechanism_slug == (
        "connection-pool-exhaustion"
    )
    assert demo.validation.status is DraftValidationStatusV234.VALID
    assert demo.registration_provider_calls == 0
    assert demo.open_world_provider_calls == 0
    assert demo.patch_bundle.bundle_directory.is_dir()


def test_committed_increment2_examples_match_the_replay_generator() -> None:
    namespace = runpy.run_path(
        str(ROOT / "scripts/ci/generate_dta_v234_increment2_examples.py")
    )
    expected = namespace["render_increment2_examples_v234"](ROOT)

    assert all((ROOT / path).read_bytes() == content for path, content in expected.items())
    FormalFaultRegistrationDraftV234.model_validate_json(
        (ROOT / "config/dta-v234/examples/formal-registration-draft.json").read_bytes()
    )
    RegistrationPatchBundleV234.model_validate_json(
        (ROOT / "config/dta-v234/examples/registration-patch-bundle.json").read_bytes()
    )


def test_dsl_has_one_typed_model_for_every_supported_rule_kind() -> None:
    rules = (
        CorePredicateReferenceRuleV234(
            kind="CORE_PREDICATE_REFERENCE",
            predicate_kind=PredicateKindV22.TRACE_FIRST_ERROR,
        ),
        GenericAnomalyKindRuleV234(
            kind="GENERIC_ANOMALY_KIND",
            anomaly_kind=GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN,
        ),
        LogCategoryRuleV234(kind="LOG_CATEGORY", category=LogCategoryV22.OTHER),
        LogTemplateContainsAnyRuleV234(
            kind="LOG_TEMPLATE_CONTAINS_ANY",
            literals=("connection lease wait", "connection pool exhausted"),
            case_sensitive=False,
        ),
        TraceFirstErrorAtServiceRuleV234(kind="TRACE_FIRST_ERROR_AT_SERVICE"),
        TracePathContainsRuleV234(
            kind="TRACE_PATH_CONTAINS", required_service_role="TARGET"
        ),
        TraceDurationThresholdRuleV234(
            kind="TRACE_DURATION_THRESHOLD",
            comparison=ThresholdComparisonV234.GREATER_THAN,
            milliseconds=25.0,
        ),
        MetricThresholdRuleV234(
            kind="METRIC_THRESHOLD",
            metric_kind=MetricKindV22.ERROR_RATE,
            comparison=ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
            threshold=0.2,
            unit=MetricUnitV22.RATIO,
        ),
        MetricBaselineRatioRuleV234(
            kind="METRIC_BASELINE_RATIO",
            metric_kind=MetricKindV22.LATENCY_P95_MS,
            comparison=ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
            ratio=1.5,
            minimum_samples=3,
        ),
        ResourceCpuThresholdRuleV234(
            kind="RESOURCE_CPU_THRESHOLD",
            comparison=ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
            percent=90.0,
        ),
        ResourceMemorySlopeRuleV234(
            kind="RESOURCE_MEMORY_SLOPE",
            comparison="GREATER_THAN",
            bytes_per_second=1024.0,
            minimum_points=3,
        ),
        RuntimeStateRuleV234(
            kind="RUNTIME_STATE", states=(RuntimeStateV22.EXITED,)
        ),
        RecentChangeStateRuleV234(
            kind="RECENT_CHANGE_STATE",
            categories=(ChangeCategoryV22.DEPLOYMENT,),
            window_seconds=600,
        ),
    )

    assert tuple(rule.kind for rule in rules) == (
        "CORE_PREDICATE_REFERENCE",
        "GENERIC_ANOMALY_KIND",
        "LOG_CATEGORY",
        "LOG_TEMPLATE_CONTAINS_ANY",
        "TRACE_FIRST_ERROR_AT_SERVICE",
        "TRACE_PATH_CONTAINS",
        "TRACE_DURATION_THRESHOLD",
        "METRIC_THRESHOLD",
        "METRIC_BASELINE_RATIO",
        "RESOURCE_CPU_THRESHOLD",
        "RESOURCE_MEMORY_SLOPE",
        "RUNTIME_STATE",
        "RECENT_CHANGE_STATE",
    )


def test_dsl_rejects_invalid_regex_threshold_and_source_rules() -> None:
    with pytest.raises(ValidationError, match="regular expression"):
        LogTemplateContainsAnyRuleV234(
            kind="LOG_TEMPLATE_CONTAINS_ANY",
            literals=("([unclosed",),
            case_sensitive=False,
        )
    with pytest.raises(ValidationError):
        ResourceCpuThresholdRuleV234(
            kind="RESOURCE_CPU_THRESHOLD",
            comparison=ThresholdComparisonV234.GREATER_THAN,
            percent=101.0,
        )
    with pytest.raises(ValidationError):
        MetricThresholdRuleV234(
            kind="METRIC_THRESHOLD",
            metric_kind=MetricKindV22.ERROR_RATE,
            comparison="GREATER_THAN",
            threshold=10.0,
            unit=MetricUnitV22.BYTES,
        )


def test_validator_rejects_single_non_authoritative_predicate_clause(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    weakened_clause = draft.support_clauses[0].model_copy(
        update={"requirements": (draft.support_clauses[0].requirements[0],)}
    )
    weakened = rebuild_formal_registration_draft_v234(
        draft,
        support_clauses=(weakened_clause,),
    )

    validation = validate_registration_draft_v234(
        draft=weakened,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert validation.status is DraftValidationStatusV234.INVALID
    assert "CLAUSE_LACKS_INDEPENDENT_CORROBORATION" in validation.error_codes


def test_validator_rejects_one_broad_singleton_beside_a_valid_clause(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    broad_singleton = draft.support_clauses[1].model_copy(
        update={"requirements": (draft.support_clauses[1].requirements[0],)}
    )
    mixed = rebuild_formal_registration_draft_v234(
        draft,
        support_clauses=(draft.support_clauses[0], broad_singleton),
    )

    validation = validate_registration_draft_v234(
        draft=mixed,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert validation.status is DraftValidationStatusV234.INVALID
    assert any(
        code.startswith("NON_AUTHORITATIVE_SINGLETON_CLAUSE:")
        for code in validation.error_codes
    )


def test_validator_binds_each_evidence_ref_to_its_real_source_and_root(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    metric_ref = next(
        ref for ref in item.report.supporting_evidence_refs if ":metrics:" in ref
    )
    forged_log_predicate = draft.predicates[0].model_copy(
        update={"supporting_report_evidence_refs": (metric_ref,)}
    )
    forged = rebuild_formal_registration_draft_v234(
        draft,
        predicates=(forged_log_predicate, *draft.predicates[1:]),
    )

    validation = validate_registration_draft_v234(
        draft=forged,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert validation.status is DraftValidationStatusV234.INVALID
    assert any(
        code.startswith("EVIDENCE_REF_SOURCE_MISMATCH:")
        for code in validation.error_codes
    )


def test_validator_classifies_core_collision_as_duplicate_existing(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    duplicate_mechanism = draft.mechanism.model_copy(
        update={
            "mechanism_enum_name": "CPU_SATURATION",
            "mechanism_slug": "cpu-saturation",
            "display_name": mechanism_display_name_v234("CPU_SATURATION"),
            "human_definition": mechanism_human_definition_v234(
                "cpu-saturation"
            ),
            "distinguishing_summary": mechanism_distinguishing_summary_v234(
                "cpu-saturation"
            ),
        }
    )
    duplicate = rebuild_formal_registration_draft_v234(
        draft,
        implementation_mode=RegistrationImplementationModeV234.DUPLICATE_EXISTING,
        mechanism=duplicate_mechanism,
        support_clauses=(),
    )

    validation = validate_registration_draft_v234(
        draft=duplicate,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert validation.status is DraftValidationStatusV234.NON_REGISTRABLE
    assert validation.classification is RegistrationImplementationModeV234.DUPLICATE_EXISTING


def test_validator_binds_frozen_similarity_threshold_and_rejects_near_duplicate(
    tmp_path: Path,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )

    validation = validate_registration_draft_v234(
        draft=draft,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=("connection-pool-exhaustion-fault",),
    )

    assert validation.status is DraftValidationStatusV234.INVALID
    assert "SHADOW_EXTENSION_SIMILARITY_DUPLICATE" in validation.error_codes
    assert validation.duplicate_absorption_policy_sha256 == (
        FROZEN_DUPLICATE_ABSORPTION_POLICY_V234.policy_sha256
    )


def test_validator_rejects_core_dnf_equivalence_and_control_absorption(
    tmp_path: Path,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    log_ref, metric_ref, _ = item.report.supporting_evidence_refs
    predicates = (
        FormalPredicateDraftV234(
            predicate_name="RESOURCE_CPU_STRONG",
            predicate_slug="resource-cpu-strong",
            implementation_mode=PredicateImplementationModeV234.REUSE_CORE_PREDICATE,
            evidence_source=EvidenceSourceV22.RESOURCES,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "resource-cpu-strong"
            ),
            extraction_rule=CorePredicateReferenceRuleV234(
                kind="CORE_PREDICATE_REFERENCE",
                predicate_kind=PredicateKindV22.RESOURCE_CPU_STRONG,
            ),
            supporting_report_evidence_refs=(metric_ref,),
            positive_examples=(
                predicate_positive_example_v234("resource-cpu-strong"),
            ),
            negative_examples=(
                predicate_negative_example_v234("resource-cpu-strong"),
            ),
        ),
        FormalPredicateDraftV234(
            predicate_name="RUNTIME_HEALTHY",
            predicate_slug="runtime-healthy",
            implementation_mode=PredicateImplementationModeV234.REUSE_CORE_PREDICATE,
            evidence_source=EvidenceSourceV22.RUNTIME,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "runtime-healthy"
            ),
            extraction_rule=CorePredicateReferenceRuleV234(
                kind="CORE_PREDICATE_REFERENCE",
                predicate_kind=PredicateKindV22.RUNTIME_HEALTHY,
            ),
            supporting_report_evidence_refs=(log_ref,),
            positive_examples=(
                predicate_positive_example_v234("runtime-healthy"),
            ),
            negative_examples=(
                predicate_negative_example_v234("runtime-healthy"),
            ),
        ),
    )
    clause = SupportClauseDraftV234(
        clause_id="novel-cpu-condition:core-equivalent",
        mechanism_slug="novel-cpu-condition",
        requirements=tuple(
            PredicateRequirementDraftV234(
                predicate_name=predicate.predicate_name,
                service_binding=predicate.service_binding,
                require_exact_parent=predicate.require_exact_parent,
            )
            for predicate in predicates
        ),
        rationale=support_clause_rationale_v234(),
    )
    core_equivalent = rebuild_formal_registration_draft_v234(
        draft,
        mechanism=draft.mechanism.model_copy(
            update={
                "mechanism_enum_name": "NOVEL_CPU_CONDITION",
                "mechanism_slug": "novel-cpu-condition",
                "display_name": mechanism_display_name_v234(
                    "NOVEL_CPU_CONDITION"
                ),
                "human_definition": mechanism_human_definition_v234(
                    "novel-cpu-condition"
                ),
                "distinguishing_summary": mechanism_distinguishing_summary_v234(
                    "novel-cpu-condition"
                ),
            }
        ),
        predicates=predicates,
        support_clauses=(clause,),
    )

    validation = validate_registration_draft_v234(
        draft=core_equivalent,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert "CORE_SEMANTIC_EQUIVALENCE" in validation.error_codes
    assert "CORE_CONTROL_ABSORPTION" in validation.error_codes


def test_validator_rejects_generic_only_no_incident_absorption_risk(
    tmp_path: Path,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    log_ref, metric_ref, _ = item.report.supporting_evidence_refs
    predicates = (
        FormalPredicateDraftV234(
            predicate_name="GENERIC_LOG_PATTERN",
            predicate_slug="generic-log-pattern",
            implementation_mode=PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE,
            evidence_source=EvidenceSourceV22.LOGS,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "generic-log-pattern"
            ),
            extraction_rule=GenericAnomalyKindRuleV234(
                kind="GENERIC_ANOMALY_KIND",
                anomaly_kind=GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN,
            ),
            supporting_report_evidence_refs=(log_ref,),
            positive_examples=(
                predicate_positive_example_v234("generic-log-pattern"),
            ),
            negative_examples=(
                predicate_negative_example_v234("generic-log-pattern"),
            ),
        ),
        FormalPredicateDraftV234(
            predicate_name="GENERIC_METRIC_OUTLIER",
            predicate_slug="generic-metric-outlier",
            implementation_mode=PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE,
            evidence_source=EvidenceSourceV22.METRICS,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "generic-metric-outlier"
            ),
            extraction_rule=GenericAnomalyKindRuleV234(
                kind="GENERIC_ANOMALY_KIND",
                anomaly_kind=GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
            ),
            supporting_report_evidence_refs=(metric_ref,),
            positive_examples=(
                predicate_positive_example_v234("generic-metric-outlier"),
            ),
            negative_examples=(
                predicate_negative_example_v234("generic-metric-outlier"),
            ),
        ),
    )
    broad = rebuild_formal_registration_draft_v234(
        draft,
        mechanism=draft.mechanism.model_copy(
            update={
                "mechanism_enum_name": "BROAD_GENERIC_ALERT",
                "mechanism_slug": "broad-generic-alert",
                "display_name": mechanism_display_name_v234(
                    "BROAD_GENERIC_ALERT"
                ),
                "human_definition": mechanism_human_definition_v234(
                    "broad-generic-alert"
                ),
                "distinguishing_summary": mechanism_distinguishing_summary_v234(
                    "broad-generic-alert"
                ),
            }
        ),
        predicates=predicates,
        support_clauses=(
            SupportClauseDraftV234(
                clause_id="broad-generic-alert:any-generic-anomalies",
                mechanism_slug="broad-generic-alert",
                requirements=tuple(
                    PredicateRequirementDraftV234(
                        predicate_name=predicate.predicate_name,
                        service_binding=predicate.service_binding,
                        require_exact_parent=False,
                    )
                    for predicate in predicates
                ),
                rationale=support_clause_rationale_v234(),
            ),
        ),
    )

    validation = validate_registration_draft_v234(
        draft=broad,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert any(
        code.startswith("NO_INCIDENT_ABSORPTION_RISK:")
        for code in validation.error_codes
    )


def test_contract_rejects_provider_code_shell_diff_and_runbook_fields(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    payload = draft.model_dump(mode="python")
    payload["unresolved_engineering_questions"] = (
        "Runbook: sudo sh -c 'curl https://example.invalid'\n--- a/file\n+++ b/file",
    )

    with pytest.raises(ValidationError, match="forbidden executable content"):
        FormalFaultRegistrationDraftV234.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_text",
    (
        "from pathlib import Path\nPath('/tmp/x').write_text('x')",
        "import os\nprint(os.environ)",
        "rm -rf build-output",
        "wget example.invalid/payload",
        "socket.create_connection host",
        "x = lambda: 1",
        "raise SystemExit",
        "touch output",
        "chmod 600 output",
        "nc host 443",
        "printf hello world.",
        "ssh host command.",
        "cp source target.",
        "Use echo hello world.",
        "Service runs npm install package.",
    ),
)
def test_contract_fails_closed_on_disguised_code_shell_file_and_network_content(
    tmp_path: Path,
    unsafe_text: str,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    payload = draft.model_dump(mode="python")
    payload["mechanism"]["human_definition"] = unsafe_text

    with pytest.raises(ValidationError, match="forbidden executable content"):
        FormalFaultRegistrationDraftV234.model_validate(payload)


def test_formal_draft_response_sha_binds_provider_authored_semantics(
    tmp_path: Path,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    mutated_mechanism = draft.mechanism.model_copy(
        update={
            "confusable_core_mechanisms": (),
        }
    )
    mutated = draft.model_copy(
        update={
            "mechanism": mutated_mechanism,
            "draft_sha256": "0" * 64,
        }
    )
    payload = mutated.model_dump(mode="python")
    payload["draft_sha256"] = semantic_sha256_v22(
        mutated.model_dump(mode="json", exclude={"draft_sha256"})
    )

    with pytest.raises(ValidationError, match="Provider-authored content digest differs"):
        FormalFaultRegistrationDraftV234.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_text",
    (
        "printf hello world",
        "ssh host command",
        "cp source target",
        "echo hello world",
        "ping example invalid",
        "npm install package",
        "env python app",
        "make install target",
        "rsync source target",
        "openssl version check",
        "timeout 5 ping host",
        "service nginx restart",
        "quota",
        "trace",
    ),
)
def test_contract_rejects_commands_in_non_sentence_provider_fields(
    tmp_path: Path,
    unsafe_text: str,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    mechanism_payload = draft.mechanism.model_dump(mode="python")
    mechanism_payload["display_name"] = unsafe_text
    with pytest.raises(ValidationError, match="forbidden executable content"):
        type(draft.mechanism).model_validate(mechanism_payload)

    with pytest.raises(ValidationError, match="forbidden executable content"):
        LogTemplateContainsAnyRuleV234(
            kind="LOG_TEMPLATE_CONTAINS_ANY",
            literals=(unsafe_text,),
            case_sensitive=False,
        )

    unsafe_binding = (
        unsafe_text if " " in unsafe_text else f"{unsafe_text} command"
    )
    predicate_payload = draft.predicates[0].model_dump(mode="python")
    predicate_payload["supporting_report_evidence_refs"] = (unsafe_binding,)
    with pytest.raises(ValidationError, match="forbidden executable content"):
        FormalPredicateDraftV234.model_validate(predicate_payload)

    plan_payload = draft.test_plan.model_dump(mode="python")
    plan_payload["positive_report_ids"] = (unsafe_binding,)
    with pytest.raises(ValidationError, match="forbidden executable content"):
        type(draft.test_plan).model_validate(plan_payload)


def test_contract_rejects_vacuous_rules_that_can_absorb_no_incident() -> None:
    with pytest.raises(ValidationError, match="substantive"):
        LogTemplateContainsAnyRuleV234(
            kind="LOG_TEMPLATE_CONTAINS_ANY",
            literals=("e",),
            case_sensitive=False,
        )
    with pytest.raises(ValidationError, match="vacuous metric threshold"):
        MetricThresholdRuleV234(
            kind="METRIC_THRESHOLD",
            metric_kind=MetricKindV22.ERROR_RATE,
            comparison=ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
            threshold=0.0,
            unit=MetricUnitV22.RATIO,
        )
    with pytest.raises(ValidationError, match="vacuous metric threshold"):
        MetricThresholdRuleV234(
            kind="METRIC_THRESHOLD",
            metric_kind=MetricKindV22.LATENCY_P95_MS,
            comparison=ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
            threshold=0.0,
            unit=MetricUnitV22.MILLISECONDS,
        )


def test_validator_detects_declarative_absorption_of_frozen_core_dnf(
    tmp_path: Path,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    log_ref, metric_ref, _ = item.report.supporting_evidence_refs
    predicates = (
        FormalPredicateDraftV234(
            predicate_name="DECLARATIVE_CPU_SATURATION",
            predicate_slug="declarative-cpu-saturation",
            implementation_mode=(
                PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE
            ),
            evidence_source=EvidenceSourceV22.RESOURCES,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "declarative-cpu-saturation"
            ),
            extraction_rule=ResourceCpuThresholdRuleV234(
                kind="RESOURCE_CPU_THRESHOLD",
                comparison=ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
                percent=80.0,
            ),
            threshold_rule="RULE_EMBEDS_TYPED_THRESHOLD",
            supporting_report_evidence_refs=(metric_ref,),
            positive_examples=(
                predicate_positive_example_v234("declarative-cpu-saturation"),
            ),
            negative_examples=(
                predicate_negative_example_v234("declarative-cpu-saturation"),
            ),
        ),
        FormalPredicateDraftV234(
            predicate_name="DECLARATIVE_RUNTIME_HEALTHY",
            predicate_slug="declarative-runtime-healthy",
            implementation_mode=(
                PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE
            ),
            evidence_source=EvidenceSourceV22.RUNTIME,
            service_binding=RequirementServiceBindingV22.TARGET,
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                "declarative-runtime-healthy"
            ),
            extraction_rule=RuntimeStateRuleV234(
                kind="RUNTIME_STATE",
                states=(RuntimeStateV22.RUNNING,),
            ),
            supporting_report_evidence_refs=(log_ref,),
            positive_examples=(
                predicate_positive_example_v234("declarative-runtime-healthy"),
            ),
            negative_examples=(
                predicate_negative_example_v234("declarative-runtime-healthy"),
            ),
        ),
    )
    declarative = rebuild_formal_registration_draft_v234(
        draft,
        mechanism=draft.mechanism.model_copy(
            update={
                "mechanism_enum_name": "NOVEL_CPU_CONDITION",
                "mechanism_slug": "novel-cpu-condition",
                "display_name": mechanism_display_name_v234(
                    "NOVEL_CPU_CONDITION"
                ),
                "human_definition": mechanism_human_definition_v234(
                    "novel-cpu-condition"
                ),
                "distinguishing_summary": mechanism_distinguishing_summary_v234(
                    "novel-cpu-condition"
                ),
            }
        ),
        predicates=predicates,
        support_clauses=(
            SupportClauseDraftV234(
                clause_id="novel-cpu-condition:declarative-core-equivalent",
                mechanism_slug="novel-cpu-condition",
                requirements=tuple(
                    PredicateRequirementDraftV234(
                        predicate_name=predicate.predicate_name,
                        service_binding=predicate.service_binding,
                        require_exact_parent=predicate.require_exact_parent,
                    )
                    for predicate in predicates
                ),
                rationale=support_clause_rationale_v234(),
            ),
        ),
    )

    validation = validate_registration_draft_v234(
        draft=declarative,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert "CORE_CONTROL_ABSORPTION" in validation.error_codes


def test_contract_requires_predicate_examples_and_complete_negative_plan(
    tmp_path: Path,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    empty_examples = draft.predicates[0].model_copy(
        update={"positive_examples": (), "negative_examples": ()}
    )
    with pytest.raises(ValidationError):
        rebuild_formal_registration_draft_v234(
            draft,
            predicates=(empty_examples, *draft.predicates[1:]),
        )

    payload = draft.test_plan.model_dump(mode="python")
    for field in (
        "required_known_controls",
        "required_no_incident_controls",
        "required_counterfactuals",
        "required_source_failure_tests",
        "required_clause_binding_tests",
    ):
        payload[field] = ()
    with pytest.raises(ValidationError):
        type(draft.test_plan).model_validate(payload)


def test_engineering_required_cannot_compile_for_activation(tmp_path: Path) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    draft = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    engineering_predicate = draft.predicates[0].model_copy(
        update={
            "implementation_mode": PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION,
            "extraction_rule": None,
        }
    )
    engineering = rebuild_formal_registration_draft_v234(
        draft,
        implementation_mode=RegistrationImplementationModeV234.ENGINEERING_REQUIRED,
        predicates=(engineering_predicate, *draft.predicates[1:]),
        unresolved_engineering_questions=(
            "Define the bounded engineering gap for connection-lease-occupancy.",
        ),
    )
    validation = validate_registration_draft_v234(
        draft=engineering,
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
        promoted_mechanism_slugs=(),
        shadow_mechanism_slugs=(),
    )

    assert validation.status is DraftValidationStatusV234.ENGINEERING_REQUIRED
    with pytest.raises(ValueError, match="DECLARATIVE_READY"):
        compile_registration_v234(
            draft=engineering,
            validation=validation,
            snapshot=authorization.core_ontology_snapshot,
        )


def _provider_authored_payload(draft: FormalFaultRegistrationDraftV234) -> str:
    return json.dumps(
        {
            "implementation_mode": draft.implementation_mode.value,
            "mechanism": draft.mechanism.model_dump(mode="json"),
            "predicates": [item.model_dump(mode="json") for item in draft.predicates],
            "support_clauses": [
                item.model_dump(mode="json") for item in draft.support_clauses
            ],
            "test_plan": draft.test_plan.model_dump(mode="json"),
            "unresolved_engineering_questions": list(
                draft.unresolved_engineering_questions
            ),
        },
        sort_keys=True,
    )


def test_provider_retries_only_the_exact_request_and_records_safe_counts(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    baseline = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    raw = _provider_authored_payload(baseline)

    class RecordingTransport:
        def __init__(self) -> None:
            self.bodies: list[str] = []

        def __call__(self, body: str) -> str:
            self.bodies.append(body)
            if len(self.bodies) <= 3:
                raise DiscoveryProviderTransportErrorV23("TIMEOUT", retryable=True)
            return raw

    transport = RecordingTransport()
    draft = RegistrationDraftProviderV234(transport=transport).generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )

    assert len(transport.bodies) == 4
    assert len(set(transport.bodies)) == 1
    assert draft.provider_trace.provider_calls == 4
    assert draft.provider_trace.transport_retries == 3
    assert draft.provider_trace.protocol_repairs == 0


def test_provider_repairs_prohibited_content_but_not_semantic_weakness(
    tmp_path: Path,
) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    baseline = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    safe = json.loads(_provider_authored_payload(baseline))
    unsafe = dict(safe)
    unsafe["mechanism"] = dict(safe["mechanism"])
    unsafe["mechanism"]["human_definition"] = "Runbook: curl https://example.invalid"
    responses = [json.dumps(unsafe), json.dumps(safe)]
    bodies: list[str] = []

    def repaired_transport(body: str) -> str:
        bodies.append(body)
        return responses.pop(0)

    repaired = RegistrationDraftProviderV234(transport=repaired_transport).generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    assert repaired.provider_trace.protocol_repairs == 1
    assert repaired.provider_trace.provider_calls == 2
    assert bodies[0] != bodies[1]

    weak = dict(safe)
    weak["implementation_mode"] = "DUPLICATE_EXISTING"
    weak["mechanism"] = {
        **safe["mechanism"],
        "mechanism_enum_name": "CPU_SATURATION",
        "mechanism_slug": "cpu-saturation",
        "display_name": mechanism_display_name_v234("CPU_SATURATION"),
        "human_definition": mechanism_human_definition_v234("cpu-saturation"),
        "distinguishing_summary": mechanism_distinguishing_summary_v234(
            "cpu-saturation"
        ),
    }
    weak["support_clauses"] = []
    calls = 0

    def weak_transport(_body: str) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(weak)

    weak_draft = RegistrationDraftProviderV234(transport=weak_transport).generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    assert calls == 1
    assert weak_draft.implementation_mode is (
        RegistrationImplementationModeV234.DUPLICATE_EXISTING
    )


def test_cross_bound_shadow_fails_before_provider_transport(tmp_path: Path) -> None:
    local_root = _local_root(tmp_path)
    shadow, item, authorization = _authorized_connection_pool(local_root)
    calls = 0

    def transport(_body: str) -> str:
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not be called")

    forged_shadow = shadow.model_copy(
        update={"shadow_fault_id": "shadow-v23-0000000000000000"}
    )
    with pytest.raises(ValueError, match="shadow differs from authorization"):
        RegistrationDraftProviderV234(transport=transport).generate(
            authorization_context=authorization,
            shadow=forged_shadow,
            accepted_reports=(item,),
        )
    assert calls == 0


def test_same_id_different_shadow_and_queue_bytes_fail_before_transport(
    tmp_path: Path,
) -> None:
    shadow, item, authorization = _authorized_connection_pool(_local_root(tmp_path))
    baseline = RegistrationDraftProviderV234().generate(
        authorization_context=authorization,
        shadow=shadow,
        accepted_reports=(item,),
    )
    raw = _provider_authored_payload(baseline)
    calls = 0

    def transport(_body: str) -> str:
        nonlocal calls
        calls += 1
        return raw

    shadow_payload = shadow.model_dump(mode="python", exclude={"entry_sha256"})
    shadow_payload["distinguishing_features"] = tuple(
        sorted((*shadow.distinguishing_features, "forged-same-id-feature"))
    )
    forged_shadow = type(shadow).model_validate(
        {
            **shadow_payload,
            "entry_sha256": semantic_sha256_v22(shadow_payload),
        }
    )
    with pytest.raises(ValueError, match="shadow bytes differ from authorization"):
        RegistrationDraftProviderV234(transport=transport).generate(
            authorization_context=authorization,
            shadow=forged_shadow,
            accepted_reports=(item,),
        )
    assert calls == 0

    queue_payload = item.model_dump(mode="python", exclude={"queue_item_sha256"})
    queue_payload["source_case_id"] = "dta-v234-forged-same-report-id"
    queue_digest_payload = item.model_copy(
        update={"source_case_id": queue_payload["source_case_id"]}
    ).model_dump(mode="json", exclude={"queue_item_sha256"})
    forged_item = type(item).model_validate(
        {
            **queue_payload,
            "queue_item_sha256": semantic_sha256_v22(queue_digest_payload),
        }
    )
    with pytest.raises(ValueError, match="accepted report bytes differ from seed"):
        RegistrationDraftProviderV234(transport=transport).generate(
            authorization_context=authorization,
            shadow=shadow,
            accepted_reports=(forged_item,),
        )
    assert calls == 0


def test_hidden_provider_view_omits_target_mechanism_and_private_clauses() -> None:
    view = build_provider_core_ontology_view_v234(
        snapshot=build_core_ontology_schema_snapshot_v234(),
        hidden_mechanism=MechanismV22.CPU_SATURATION,
    )
    rendered = view.model_dump_json().casefold()

    assert "cpu_saturation" not in rendered
    assert "cpu-saturation" not in rendered
    assert "cpu saturation" not in rendered
    assert all(
        clause.mechanism is not MechanismV22.CPU_SATURATION
        for clause in view.runtime_known_support_clauses
    )


def test_increment2_cli_generates_validates_and_renders_local_bundle(
    tmp_path: Path,
    capsys,
) -> None:
    local_root = _local_root(tmp_path)
    _shadow, _item, authorization = _authorized_connection_pool(local_root)

    assert main(
        (
            "ontology",
            "generate-draft",
            authorization.authorization.authorization_id,
            "--development-fixture",
            "--local-root",
            str(local_root),
        )
    ) == 0
    draft_payload = json.loads(capsys.readouterr().out)
    draft_id = draft_payload["draft_id"]
    assert draft_payload["provider_trace"]["provider_calls"] == 0

    assert main(
        (
            "ontology",
            "validate-draft",
            draft_id,
            "--local-root",
            str(local_root),
        )
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VALID"

    assert main(
        (
            "ontology",
            "render-bundle",
            draft_id,
            "--local-root",
            str(local_root),
        )
    ) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["automatic_tracked_write"] is False
    assert (
        LocalRegistrationDraftStoreV234(local_root).bundles_dir / draft_id
    ).is_dir()
