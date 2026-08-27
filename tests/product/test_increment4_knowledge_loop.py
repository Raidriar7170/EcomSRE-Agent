from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.product.app import create_app
from ecomsre.product.environment.capabilities import (
    EnvironmentCapabilityMatrixV1,
    SourceCapabilityStatusV1,
    SourceCapabilityV1,
)
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
    EvidenceObjectV1,
    IncidentRecordV1,
)
from ecomsre.product.jobs.worker import run_one_job

from ecomsre.product.knowledge.contracts import (
    CandidateClauseV1,
    FaultFamilyStatusV1,
    FingerprintObservationV1,
    PredicateCellStateV1,
    PredicateMatrixCellV1,
    PredicateMatrixRowKindV1,
    PredicateMatrixRowV1,
    ReviewDecisionV1,
    ShadowCaseOriginV1,
    ShadowCaseOutcomeV1,
    ShadowEvaluationStratumV1,
    EnvironmentExtensionRegistryEntryV1,
)
from ecomsre.product.knowledge.runtime import (
    build_incident_fingerprint_v1,
    build_predicate_matrix_v1,
    cluster_similarity_v1,
    evaluate_shadow_gate_v1,
    mine_candidate_clauses_v1,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.knowledge.repository import (
    KnowledgeRepositoryV1,
    _complete_source_coverage_v1,
)


NOW = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)


def _observation(
    incident_id: str,
    *,
    environment_id: str = "env-a",
    anomaly_kinds: tuple[str, ...] = (
        "LOG_UNKNOWN_ERROR_PATTERN",
    ),
    sources: tuple[str, ...] = ("LOGS", "RUNTIME"),
    tokens: tuple[str, ...] = ("mutex", "opaque", "convoy"),
) -> FingerprintObservationV1:
    return FingerprintObservationV1(
        environment_id=environment_id,
        incident_id=incident_id,
        root_service_ids=("svc-payment",),
        broad_domain="CONCURRENCY",
        generic_anomaly_kinds=anomaly_kinds,
        evidence_sources=sources,
        topology_edges=(),
        runtime_state_signature=("payment:RUNNING:True",),
        resource_state_signature=("payment:CPU_NORMAL",),
        normalized_log_tokens=tokens,
        trace_first_error_roles=(),
        source_coverage=sources,
    )


def test_fingerprint_and_similarity_are_deterministic_and_environment_scoped() -> None:
    first = build_incident_fingerprint_v1(_observation("inc-a"))
    repeated = build_incident_fingerprint_v1(_observation("inc-a"))
    related = build_incident_fingerprint_v1(_observation("inc-b"))
    foreign = build_incident_fingerprint_v1(
        _observation("inc-c", environment_id="env-b")
    )

    assert first == repeated
    assert first.fingerprint_sha256 == repeated.fingerprint_sha256
    assert cluster_similarity_v1(first, related) == 1.0
    assert cluster_similarity_v1(first, foreign) is None


def test_similarity_uses_the_goal_weights_and_threshold_boundary() -> None:
    reference = build_incident_fingerprint_v1(_observation("inc-a"))
    related = build_incident_fingerprint_v1(
        _observation(
            "inc-b",
            tokens=("mutex", "opaque", "different"),
        )
    )

    # All fields except the log-token Jaccard remain equal.  Token Jaccard is
    # 2 / 4, so the exact weighted score is 0.95.
    assert cluster_similarity_v1(reference, related) == 0.95
    at_threshold = build_incident_fingerprint_v1(
        _observation(
            "inc-threshold",
            anomaly_kinds=reference.generic_anomaly_kinds,
            sources=reference.evidence_sources,
            tokens=("distinct",),
        ).model_copy(
            update={
                "root_service_ids": ("svc-other",),
                "topology_edges": (("other", "svc-other"),),
                "runtime_state_signature": ("other:EXITED:False",),
                "resource_state_signature": ("other:CPU_HIGH",),
            }
        )
    )
    assert cluster_similarity_v1(reference, at_threshold) == 0.65


def _row(
    row_id: str,
    kind: PredicateMatrixRowKindV1,
    *,
    log: PredicateCellStateV1,
    runtime: PredicateCellStateV1,
) -> PredicateMatrixRowV1:
    return PredicateMatrixRowV1(
        row_id=row_id,
        incident_id=row_id,
        row_kind=kind,
        cells=(
            PredicateMatrixCellV1(
                predicate_id="core:RUNTIME_HEALTHY",
                source="RUNTIME",
                state=runtime,
            ),
            PredicateMatrixCellV1(
                predicate_id="ga:LOG_UNKNOWN_ERROR_PATTERN",
                source="LOGS",
                state=log,
            ),
        ),
    )


def test_unknown_is_never_treated_as_a_negative_and_three_negatives_are_required() -> None:
    positives = tuple(
        _row(
            f"positive-{index}",
            PredicateMatrixRowKindV1.POSITIVE_FAMILY,
            log=PredicateCellStateV1.PRESENT,
            runtime=PredicateCellStateV1.PRESENT,
        )
        for index in range(3)
    )
    only_two_negatives = tuple(
        _row(
            f"negative-{index}",
            PredicateMatrixRowKindV1.NO_INCIDENT_CONTROL,
            log=(
                PredicateCellStateV1.UNKNOWN
                if index == 0
                else PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE
            ),
            runtime=PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
        )
        for index in range(2)
    )
    insufficient = build_predicate_matrix_v1(
        environment_id="env-a",
        family_id="family-a",
        rows=positives + only_two_negatives,
    )
    assert mine_candidate_clauses_v1(insufficient).status == "NEEDS_MORE_NEGATIVES"

    matrix = build_predicate_matrix_v1(
        environment_id="env-a",
        family_id="family-a",
        rows=positives
        + only_two_negatives
        + (
            _row(
                "negative-2",
                PredicateMatrixRowKindV1.NO_INCIDENT_CONTROL,
                log=PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
                runtime=PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
            ),
            _row(
                "negative-3",
                PredicateMatrixRowKindV1.CORE_KNOWN_CONTROL,
                log=PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
                runtime=PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
            ),
        ),
    )
    mined = mine_candidate_clauses_v1(matrix)
    assert mined.status == "CANDIDATES_READY"
    assert mined.candidates
    selected = mined.candidates[0]
    assert selected.predicate_count <= 3
    assert selected.positive_recall >= 0.60
    assert selected.false_positive_rate <= 0.20
    assert all(
        candidate.predicate_ids != ("ga:LOG_UNKNOWN_ERROR_PATTERN",)
        for candidate in mined.candidates
    )
    duplicate_filtered = mine_candidate_clauses_v1(
        matrix,
        existing_clause_predicates=(mined.candidates[0].predicate_ids,),
    )
    assert all(
        candidate.predicate_ids != mined.candidates[0].predicate_ids
        for candidate in duplicate_filtered.candidates
    )


def test_core_and_no_incident_false_positives_are_rejected() -> None:
    positives = tuple(
        _row(
            f"positive-{index}",
            PredicateMatrixRowKindV1.POSITIVE_FAMILY,
            log=PredicateCellStateV1.PRESENT,
            runtime=PredicateCellStateV1.PRESENT,
        )
        for index in range(3)
    )
    negatives = (
        _row(
            "core-overlap",
            PredicateMatrixRowKindV1.CORE_KNOWN_CONTROL,
            log=PredicateCellStateV1.PRESENT,
            runtime=PredicateCellStateV1.PRESENT,
        ),
        _row(
            "no-incident-overlap",
            PredicateMatrixRowKindV1.NO_INCIDENT_CONTROL,
            log=PredicateCellStateV1.PRESENT,
            runtime=PredicateCellStateV1.PRESENT,
        ),
        _row(
            "negative-clean",
            PredicateMatrixRowKindV1.OTHER_ACCEPTED_FAMILY,
            log=PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
            runtime=PredicateCellStateV1.ABSENT_WITH_COMPLETE_COVERAGE,
        ),
    )
    matrix = build_predicate_matrix_v1(
        environment_id="env-a",
        family_id="family-a",
        rows=positives + negatives,
    )
    assert mine_candidate_clauses_v1(matrix).status == "NO_ACCEPTABLE_CANDIDATE"


def test_source_coverage_requires_untruncated_target_complete_connector_reads() -> None:
    incident = IncidentRecordV1.model_construct(
        environment_id="env-a",
        source_capability_sha256="a" * 64,
        candidate_logical_services=("payment",),
    )
    capability = EnvironmentCapabilityMatrixV1.model_construct(
        environment_id="env-a",
        capability_sha256="a" * 64,
        sources=(
            SourceCapabilityV1.model_construct(
                source=EvidenceSourceV22.LOGS,
                status=SourceCapabilityStatusV1.AVAILABLE,
                covered_services=("payment",),
                target_complete_coverage=True,
            ),
        ),
    )

    def evidence(*, truncated: bool, covered: tuple[str, ...]) -> EvidenceBundleV1:
        item = EvidenceObjectV1.model_construct(
            source=EvidenceSourceV22.LOGS,
            payload={
                "action": {"action_id": "action-a", "target_services": ["payment"]},
                "connector_result": {
                    "requested_services": ["payment"],
                    "covered_services": list(covered),
                    "truncated": truncated,
                },
                "read_outcome": {"status": "SUCCESS_NONEMPTY"},
            },
        )
        return EvidenceBundleV1.model_construct(objects=(item,))

    assert _complete_source_coverage_v1(
        incident=incident,
        evidence=evidence(truncated=False, covered=("payment",)),
        capability_matrix=capability,
    ) == ("LOGS",)
    assert _complete_source_coverage_v1(
        incident=incident,
        evidence=evidence(truncated=True, covered=("payment",)),
        capability_matrix=capability,
    ) == ()
    assert _complete_source_coverage_v1(
        incident=incident,
        evidence=evidence(truncated=False, covered=()),
        capability_matrix=capability,
    ) == ()
    incomplete_capability = capability.model_copy(
        update={
            "sources": (
                capability.sources[0].model_copy(
                    update={"target_complete_coverage": False}
                ),
            )
        }
    )
    assert _complete_source_coverage_v1(
        incident=incident,
        evidence=evidence(truncated=False, covered=("payment",)),
        capability_matrix=incomplete_capability,
    ) == ()


def test_negative_pool_excludes_unrelated_non_no_incident_controls(tmp_path: Path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    repository = KnowledgeRepositoryV1(
        store,
        ContentAddressedObjectStoreV1(tmp_path / "objects", metadata_store=store),
    )
    positive = build_incident_fingerprint_v1(_observation("positive"))
    unrelated = build_incident_fingerprint_v1(
        _observation(
            "unrelated",
            anomaly_kinds=("METRIC_LATENCY_OUTLIER",),
            sources=("METRICS",),
            tokens=("different",),
        ).model_copy(
            update={
                "broad_domain": "DEPENDENCY",
                "root_service_ids": ("svc-inventory",),
            }
        )
    )
    positive_incident = IncidentRecordV1.model_construct(
        candidate_logical_services=("payment",),
    )
    unrelated_incident = IncidentRecordV1.model_construct(
        candidate_logical_services=("inventory",),
    )
    core_result = DiagnosisResultV1.model_construct(
        terminal=DiagnosisTerminalV1.CORE_KNOWN,
    )
    no_incident_result = DiagnosisResultV1.model_construct(
        terminal=DiagnosisTerminalV1.NO_INCIDENT,
    )
    confusable_result = DiagnosisResultV1.model_construct(
        terminal=DiagnosisTerminalV1.CORE_KNOWN,
        mechanism=MechanismV22.CPU_SATURATION,
    )

    assert repository._eligible_negative_control(
        positive_fingerprints=(positive,),
        positive_incidents=(positive_incident,),
        control_fingerprint=unrelated,
        control_incident=unrelated_incident,
        result=core_result,
    ) is False
    assert repository._eligible_negative_control(
        positive_fingerprints=(positive,),
        positive_incidents=(positive_incident,),
        control_fingerprint=unrelated,
        control_incident=unrelated_incident,
        result=no_incident_result,
    ) is True
    assert repository._eligible_negative_control(
        positive_fingerprints=(positive.model_copy(update={"broad_domain": "RESOURCE"}),),
        positive_incidents=(positive_incident,),
        control_fingerprint=unrelated,
        control_incident=unrelated_incident,
        result=confusable_result,
    ) is True


def _shadow_case(
    case_id: str,
    stratum: ShadowEvaluationStratumV1,
    *,
    matched: bool,
) -> ShadowCaseOutcomeV1:
    runtime_sha256 = semantic_sha256_v22({"case_id": case_id})
    payload = {
        "schema_version": "ecomsre.product.shadow-case-outcome.v1",
        "case_id": case_id,
        "incident_id": f"incident-{case_id}",
        "stratum": stratum,
        "origin": ShadowCaseOriginV1.PERSISTED_INCIDENT,
        "runtime_input_sha256": runtime_sha256,
        "expected_match": stratum is ShadowEvaluationStratumV1.POSITIVE_INCIDENT,
        "matched": matched,
        "evaluated_target_services": ("payment",),
        "supporting_evidence_refs": (("evidence-a",) if matched else ()),
        "available_evidence_refs": ("evidence-a",),
        "required_sources": ("LOGS", "RUNTIME"),
        "source_reachable": True,
        "action_authority_violations": 0,
        "reason_code": None,
    }
    return ShadowCaseOutcomeV1.model_validate(
        {**payload, "outcome_sha256": semantic_sha256_v22(payload)}
    )


def _shadow_outcomes(*, failed_counterfactual: bool) -> tuple[ShadowCaseOutcomeV1, ...]:
    values = [
        *(
            _shadow_case(
                f"positive-{index}",
                ShadowEvaluationStratumV1.POSITIVE_INCIDENT,
                matched=index < 3,
            )
            for index in range(4)
        ),
        _shadow_case(
            "core-control",
            ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN,
            matched=False,
        ),
        _shadow_case(
            "no-incident-control",
            ShadowEvaluationStratumV1.NO_INCIDENT,
            matched=False,
        ),
        *(
            _shadow_case(
                f"insufficient-{index}",
                ShadowEvaluationStratumV1.INSUFFICIENT_OR_CONFLICT,
                matched=(index == 0 and not failed_counterfactual),
            )
            for index in range(12)
        ),
        *(
            _shadow_case(
                f"counterfactual-{index}",
                ShadowEvaluationStratumV1.TARGET_COUNTERFACTUAL,
                matched=index < (2 if failed_counterfactual else 1),
            )
            for index in range(5)
        ),
        _shadow_case(
            "source-failure",
            ShadowEvaluationStratumV1.SOURCE_FAILURE,
            matched=False,
        ),
    ]
    unavailable_payload = {
        "schema_version": "ecomsre.product.shadow-case-outcome.v1",
        "case_id": "other-extension-not-available",
        "incident_id": None,
        "stratum": ShadowEvaluationStratumV1.OTHER_EXTENSION,
        "origin": ShadowCaseOriginV1.NOT_AVAILABLE,
        "runtime_input_sha256": None,
        "expected_match": None,
        "matched": None,
        "evaluated_target_services": (),
        "supporting_evidence_refs": (),
        "available_evidence_refs": (),
        "required_sources": ("LOGS", "RUNTIME"),
        "source_reachable": None,
        "action_authority_violations": 0,
        "reason_code": "NO_ACTIVE_OTHER_EXTENSION_CONTROL_AVAILABLE",
    }
    values.append(
        ShadowCaseOutcomeV1.model_validate(
            {
                **unavailable_payload,
                "outcome_sha256": semantic_sha256_v22(unavailable_payload),
            }
        )
    )
    return tuple(values)


def test_shadow_promotion_gate_is_exact_and_fail_closed() -> None:
    passed = evaluate_shadow_gate_v1(
        registration_id="registration-a",
        outcomes=_shadow_outcomes(failed_counterfactual=False),
    )
    failed = evaluate_shadow_gate_v1(
        registration_id="registration-a",
        outcomes=_shadow_outcomes(failed_counterfactual=True),
    )

    assert passed.gate_passed is True
    assert passed.reason_codes == ()
    assert passed.positive_recall == 0.75
    assert passed.false_positive_rate == 0.10
    assert passed.counterfactual_consistency == 0.80
    assert failed.gate_passed is False
    assert failed.reason_codes == ("COUNTERFACTUAL_CONSISTENCY_BELOW_GATE",)


def test_contract_enums_preserve_goal_truth_markers() -> None:
    assert FaultFamilyStatusV1.REVIEW_READY.value == "REVIEW_READY"
    assert ReviewDecisionV1.ACCEPT_AS_NEW.value == "ACCEPT_AS_NEW"
    clause = CandidateClauseV1(
        candidate_id="candidate-a",
        predicate_ids=(
            "ga:LOG_UNKNOWN_ERROR_PATTERN",
            "core:RUNTIME_HEALTHY",
        ),
        evidence_sources=("LOGS", "RUNTIME"),
        positive_recall=1.0,
        false_positive_rate=0.0,
        core_known_overlap_rate=0.0,
        no_incident_false_positive_rate=0.0,
        score=0.8,
    )
    assert clause.action_authority == "NONE"


def test_increment4_one_environment_knowledge_loop_recurrence(tmp_path: Path) -> None:
    settings = ProductSettingsV1(
        data_root=tmp_path,
        sqlite_path=tmp_path / "product.sqlite3",
        object_store_root=tmp_path / "objects",
    )

    def diagnose_at(
        client: TestClient,
        *,
        environment_id: str,
        service_id: str,
        slot: int,
    ) -> dict[str, object]:
        observed_at = NOW + timedelta(minutes=slot)
        incident = client.post(
            "/v1/incidents",
            json={
                "environment_id": environment_id,
                "external_incident_key": f"knowledge-slot-{slot}",
                "alert_name": "payment-observation",
                "summary": "A bounded opaque fixture observation requires diagnosis.",
                "started_at": (observed_at - timedelta(minutes=1)).isoformat(),
                "ended_at": observed_at.isoformat(),
                "candidate_service_ids": [service_id],
                "labels": {"source": "increment4-checkpoint"},
            },
        )
        assert incident.status_code == 201, incident.text
        incident_id = incident.json()["incident_id"]
        job = client.post(f"/v1/incidents/{incident_id}/diagnosis-jobs")
        assert job.status_code == 202, job.text
        assert run_one_job(settings, worker_id=f"knowledge-worker-{slot}") is True
        terminal = client.get(f"/v1/jobs/{job.json()['job_id']}").json()
        assert terminal["status"] == "SUCCEEDED", terminal
        return client.get(f"/v1/incidents/{incident_id}/diagnosis").json()

    with TestClient(create_app(settings)) as client:
        environment = client.post(
            "/v1/environments",
            json={
                "name": "knowledge-loop",
                "description": "One deterministic time-indexed fixture environment.",
                "timezone": "UTC",
                "service_identity_policy": {
                    "services": [{"logical_service": "payment"}]
                },
                "connector_configs": [
                    {
                        "name": "fixture",
                        "kind": "FIXTURE",
                        "settings": {"dataset": "product-knowledge-loop"},
                        "credential_refs": {},
                    }
                ],
                "explicit_service_catalog": ["payment"],
            },
        )
        assert environment.status_code == 201, environment.text
        environment_id = environment.json()["environment_id"]
        verify = client.post(f"/v1/environments/{environment_id}/verify-jobs")
        assert run_one_job(settings, worker_id="knowledge-verify") is True
        verified = client.get(f"/v1/jobs/{verify.json()['job_id']}").json()
        assert verified["status"] == "SUCCEEDED", verified
        service_id = verified["result"]["service_identity_map"]["services"][0][
            "service_id"
        ]
        baseline = client.post(
            f"/v1/environments/{environment_id}/baseline-jobs",
            json={"activate": True},
        )
        assert run_one_job(settings, worker_id="knowledge-baseline") is True
        assert client.get(f"/v1/jobs/{baseline.json()['job_id']}").json()["status"] == "SUCCEEDED"

        positives = tuple(
            diagnose_at(
                client,
                environment_id=environment_id,
                service_id=service_id,
                slot=slot,
            )
            for slot in (0, 1, 2)
        )
        controls = tuple(
            diagnose_at(
                client,
                environment_id=environment_id,
                service_id=service_id,
                slot=slot,
            )
            for slot in (3, 4, 5)
        )
        assert {item["terminal"] for item in positives} == {"OPEN_WORLD"}
        assert tuple(item["terminal"] for item in controls) == (
            "CORE_KNOWN",
            "NO_INCIDENT",
            "NO_INCIDENT",
        )

        families = client.get(
            f"/v1/environments/{environment_id}/fault-families"
        ).json()["items"]
        assert len(families) == 1
        family = families[0]
        assert family["status"] == "REVIEW_READY"
        assert len(family["member_incident_ids"]) == 3
        review = client.post(
            f"/v1/fault-families/{family['family_id']}/reviews",
            json={
                "decision": "ACCEPT_AS_NEW",
                "reviewer": "TEST_REVIEWER",
                "note": "SIMULATED HUMAN REVIEW: accept the bounded recurring family.",
                "reviewed_at": (NOW + timedelta(minutes=10)).isoformat(),
            },
        )
        assert review.status_code == 201, review.text
        draft = client.post(
            f"/v1/fault-families/{family['family_id']}/registration-drafts",
            json={
                "human_review_id": review.json()["review_id"],
                "human_canonical_label": "Opaque Mutex Convoy",
                "llm_explanation": "A deterministic advisory summary of the Runtime-mined clause.",
                "unresolved_gaps": [],
            },
        )
        assert draft.status_code == 201, draft.text
        draft_payload = draft.json()
        assert draft_payload["implementation_mode"] == "DECLARATIVE_READY"
        selected = next(
            item
            for item in draft_payload["candidate_clauses"]
            if item["candidate_id"] == draft_payload["selected_candidate_id"]
        )
        assert selected["predicate_ids"] == [
            "core:RUNTIME_HEALTHY",
            "ga:LOG_UNKNOWN_ERROR_PATTERN",
        ]
        shadow = client.post(
            f"/v1/registrations/{draft_payload['registration_id']}/shadow-evaluation-jobs",
            json={},
        )
        assert shadow.status_code == 201, shadow.text
        assert shadow.json()["gate_passed"] is True
        assert {item["stratum"] for item in shadow.json()["outcomes"]} == {
            item.value for item in ShadowEvaluationStratumV1
        }
        assert all(
            item["matched"] is False
            for item in shadow.json()["outcomes"]
            if item["stratum"] == "SOURCE_FAILURE"
        )
        assert all(
            item["origin"] == "DERIVED_COUNTERFACTUAL"
            and item["evaluated_target_services"] == ["counterfactual-target"]
            for item in shadow.json()["outcomes"]
            if item["stratum"] == "TARGET_COUNTERFACTUAL"
        )
        assert shadow.json()["runtime_evaluation_sha256"]
        promotion = client.post(
            f"/v1/registrations/{draft_payload['registration_id']}/promotions",
            json={
                "shadow_evaluation_id": shadow.json()["evaluation_id"],
                "reviewer": "TEST_REVIEWER",
                "note": "SIMULATED HUMAN REVIEW: promote the passing shadow registration.",
                "promoted_at": (NOW + timedelta(minutes=11)).isoformat(),
            },
        )
        assert promotion.status_code == 201, promotion.text
        assert promotion.json()["status"] == "ACTIVE"

        recurrence = diagnose_at(
            client,
            environment_id=environment_id,
            service_id=service_id,
            slot=6,
        )
        assert recurrence["terminal"] == "EXTENSION_KNOWN"
        assert recurrence["provider_calls"] == 0
        assert recurrence["agent_writes"] == 0
        assert recurrence["runbook_executions"] == 0

        source_failure = diagnose_at(
            client,
            environment_id=environment_id,
            service_id=service_id,
            slot=8,
        )
        assert source_failure["terminal"] == "INSUFFICIENT_EVIDENCE"
        assert source_failure["terminal"] != "EXTENSION_KNOWN"
        assert source_failure["provider_calls"] == 0

        revoked = client.post(
            f"/v1/registrations/{draft_payload['registration_id']}/revocations",
            json={
                "reviewer": "TEST_REVIEWER",
                "note": "SIMULATED HUMAN REVIEW: revoke without deleting history.",
                "revoked_at": (NOW + timedelta(minutes=12)).isoformat(),
            },
        )
        assert revoked.status_code == 201, revoked.text

    store = SqliteStoreV1(settings.sqlite_path)
    knowledge = KnowledgeRepositoryV1(
        store,
        ContentAddressedObjectStoreV1(
            settings.object_store_root,
            metadata_store=store,
        ),
    )
    assert knowledge.active_extensions(environment_id) == ()
    with store.connect() as connection:
        history = connection.execute(
            "SELECT status, payload_json FROM environment_extension_registry_versions "
            "WHERE environment_id = ? ORDER BY registry_version",
            (environment_id,),
        ).fetchall()
    assert [row["status"] for row in history] == ["ACTIVE", "REVOKED"]
    entries = tuple(
        EnvironmentExtensionRegistryEntryV1.model_validate_json(row["payload_json"])
        for row in history
    )
    active, revoked_entry = entries
    assert active.family_id == family["family_id"]
    assert active.human_canonical_label == "Opaque Mutex Convoy"
    assert active.shadow_evaluation_sha256 == shadow.json()["evaluation_sha256"]
    assert active.action_authority == "NONE"
    assert active.remediation_authority == "NONE"
    assert revoked_entry.status == "REVOKED"
    assert revoked_entry.revocation_review is not None
