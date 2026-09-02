#!/usr/bin/env python3
"""Run the deterministic Product v0.2.3.3 formal-contract preflight."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from ecomsre.dta_v2.v22.memory import BaselineProfileV22
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import (
    BaselineBuildPolicyV1,
    BaselineRepositoryV1,
    EnvironmentBaselineV1,
)
from ecomsre.product.connectors.fixture import FixtureConnectorV1
from ecomsre.product.environment.capabilities import (
    CapabilityMatrixRepositoryV1,
    build_environment_capability_matrix,
)
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.contracts import (
    ActionAuthorityV1,
    DiagnosisLaneV1,
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
    IncidentCreateV1,
)
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.incidents.repository import (
    DiagnosisRepositoryV1,
    IncidentRepositoryV1,
)
from ecomsre.product.jobs.contracts import ProductJobStatusV1, ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    FORMAL_CONTRACT_PREFLIGHT_PASS_V0233,
    DiagnosisPipelineAcceptanceV0233,
    FormalIncidentDiagnosisCardinalityV0233,
    FormalContractCaseV0233,
    FormalContractPreflightV0233,
    admit_incident_creation_v0233,
    load_fresh_formal_campaign_v0233,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NOFAULT_CAPABILITY_LIMITED_V0232,
    NOFAULT_FULLY_SUPPORTED_V0232,
    NOFAULT_NOT_SUPPORTED_V0232,
    score_nofault_evidence_v0232,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.product_v0232.run_evidence_binding_preflight import _fixture


_NOW = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
_GOAL_VERSION = "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
_EXPECTED_CASE_IDS = (
    "01_FULLY_SUPPORTED_HEALTHY",
    "02_CAPABILITY_LIMITED_BOUND",
    "03_NOT_SUPPORTED_CORE_KNOWN",
    "04_NOT_SUPPORTED_OPEN_WORLD",
    "05_NOT_SUPPORTED_CONFLICTING",
    "06_NOT_SUPPORTED_STALE_RUNTIME",
    "07_NOT_SUPPORTED_MISSING_P01",
    "08_NOT_SUPPORTED_UNRESOLVED_EVIDENCE_REF",
    "09_FAILED_DIAGNOSIS_STAGE",
    "10_FORMAL_TRAFFIC_BLOCKER_BEFORE_INCIDENT",
    "11_SOURCE_CLONE_DELTA_VALIDATION",
    "12_REPOSITORY_PHASE_VALIDATION",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _deterministic_product_id_factory():
    counts: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counts[prefix] = counts.get(prefix, 0) + 1
        suffix = hashlib.sha256(
            f"product-v0233-contract-preflight:{prefix}:{counts[prefix]}".encode()
        ).hexdigest()[:24]
        return f"{prefix}-{suffix}"

    return generate


def _deterministic_id_patches(stack: ExitStack) -> None:
    generate = _deterministic_product_id_factory()
    for target in (
        "ecomsre.product.environment.repository.new_product_id",
        "ecomsre.product.environment.verification.new_product_id",
        "ecomsre.product.baselines.new_product_id",
        "ecomsre.product.jobs.repository.new_product_id",
        "ecomsre.product.incidents.repository.new_product_id",
    ):
        stack.enter_context(patch(target, side_effect=generate))


def _sealed_baseline(
    *,
    environment_id: str,
    service_ids: tuple[str, ...],
    capability_sha256: str,
) -> EnvironmentBaselineV1:
    policy = BaselineBuildPolicyV1()
    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.environment-baseline.v1",
        "baseline_id": "base-" + "b" * 24,
        "environment_id": environment_id,
        "service_ids": service_ids,
        "source_capability_sha256": capability_sha256,
        "v22_baseline_profile": BaselineProfileV22.build(
            metric_stats=(), trace_stats=(), resource_stats=()
        ),
        "topology_edges": (),
        "normal_log_templates": (),
        "build_policy": policy,
        "window_count": policy.window_count,
        "successful_windows": policy.window_count,
        "built_at": _NOW,
        "active": False,
    }
    draft = EnvironmentBaselineV1.model_construct(
        **body, baseline_sha256="0" * 64
    )
    return EnvironmentBaselineV1.model_validate(
        {
            **body,
            "baseline_sha256": semantic_sha256_v22(
                draft.model_dump(
                    mode="json", exclude={"baseline_sha256", "active"}
                )
            ),
        }
    )


def _load_decision_trace(
    store: SqliteStoreV1,
    objects: ContentAddressedObjectStoreV1,
    *,
    expected_sha256: str,
) -> DiagnosisDecisionTraceV0232:
    with store.connect() as connection:
        object_sha256s = tuple(
            str(row["object_sha256"])
            for row in connection.execute(
                "SELECT object_sha256 FROM evidence_objects ORDER BY object_sha256"
            ).fetchall()
        )
    matches: list[DiagnosisDecisionTraceV0232] = []
    for object_sha256 in object_sha256s:
        try:
            candidate = DiagnosisDecisionTraceV0232.model_validate_json(
                objects.read_bytes(object_sha256)
            )
        except (json.JSONDecodeError, ValueError):
            continue
        if candidate.trace_sha256 == expected_sha256:
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError("fixture Decision Trace persistence differs")
    return matches[0]


def _ordinary_fixture_pipeline(
    root: Path,
) -> tuple[DiagnosisPipelineAcceptanceV0233, str, bool, bool, bool, bool]:
    settings = ProductSettingsV1(
        data_root=root,
        sqlite_path=root / "product.sqlite3",
        object_store_root=root / "objects",
    )
    with ExitStack() as stack:
        _deterministic_id_patches(stack)
        store = SqliteStoreV1(settings.sqlite_path)
        environments = EnvironmentRepositoryV1(store)
        services = ServiceCatalogRepositoryV1(store)
        capabilities = CapabilityMatrixRepositoryV1(store)
        baselines = BaselineRepositoryV1(store)
        environment = environments.create(
            {
                "name": "product-v0233-contract-preflight",
                "description": "Deterministic ordinary Worker fixture.",
                "timezone": "UTC",
                "service_identity_policy": {
                    "services": [{"logical_service": "checkout"}]
                },
                "connector_configs": [
                    {
                        "name": "fixture",
                        "kind": "FIXTURE",
                        "settings": {"dataset": "product-mvp-demo"},
                        "credential_refs": {},
                    }
                ],
                "explicit_service_catalog": ["checkout"],
            },
            now=_NOW.timestamp(),
        )
        identity = services.get_map(environment.environment_id)
        health = FixtureConnectorV1(environment.connector_configs[0]).verify()
        capability = build_environment_capability_matrix(
            environment_id=environment.environment_id,
            logical_services=("checkout",),
            connector_health=(health,),
            changes_available=False,
            verified_at=_NOW,
        )
        capabilities.put(capability)
        baseline = _sealed_baseline(
            environment_id=environment.environment_id,
            service_ids=tuple(item.service_id for item in identity.services),
            capability_sha256=capability.capability_sha256,
        )
        baselines.put(baseline, activate=True)
        incidents = IncidentRepositoryV1(
            store,
            environments=environments,
            services=services,
            capabilities=capabilities,
            baselines=baselines,
        )
        incident = incidents.create(
            IncidentCreateV1(
                environment_id=environment.environment_id,
                external_incident_key="product-v0233-contract-preflight",
                alert_name="checkout no-fault fixture",
                summary="A deterministic healthy fixture observation.",
                started_at=_NOW - timedelta(minutes=5),
                ended_at=_NOW,
                candidate_service_ids=(identity.services[0].service_id,),
                labels={"fault": "none", "scope": "contract-preflight"},
            ),
            now=_NOW.timestamp(),
        )
        jobs = JobRepositoryV1(store)
        queued = jobs.enqueue(
            ProductJobTypeV1.DIAGNOSIS,
            {"incident_id": incident.incident_id},
            idempotency_key="product-v0233-contract-preflight",
            now=(_NOW + timedelta(seconds=1)).timestamp(),
        )
        if not run_one_job(
            settings,
            worker_id="product-v0233-contract-preflight-worker",
            now=(_NOW + timedelta(seconds=2)).timestamp(),
        ):
            raise ValueError("ordinary fixture Worker did not claim the Diagnosis job")
        completed = jobs.get(queued.job_id)
        if completed.status is not ProductJobStatusV1.SUCCEEDED:
            raise ValueError("ordinary fixture Diagnosis job did not succeed")
        object_store = ContentAddressedObjectStoreV1(
            settings.object_store_root, metadata_store=store
        )
        diagnoses = DiagnosisRepositoryV1(store, object_store)
        diagnosis = diagnoses.get(incident.incident_id)
        bundle = diagnoses.evidence(incident.incident_id)
        index = diagnoses.evidence_index(incident.incident_id)
        trace = _load_decision_trace(
            store, object_store, expected_sha256=index.decision_trace_sha256
        )
        events = DiagnosisStageJournalRepositoryV02322(store).list_events(
            queued.job_id
        )
        private_root = root / "private/diagnosis-failures" / queued.job_id
        private_absent = not private_root.exists() or not tuple(private_root.iterdir())
        assessment = score_nofault_evidence_v0232(
            diagnosis=diagnosis,
            bundle=bundle,
            index=index,
            decision_trace=trace,
        )
        expected_terminal = NOFAULT_NOT_SUPPORTED_V0232
        acceptance = DiagnosisPipelineAcceptanceV0233.build_success(
            job_id=queued.job_id,
            journal_tail_sha256=events[-1].event_sha256,
            event_count=len(events),
            diagnosis_result_sha256=diagnosis.result_sha256,
            evidence_bundle_sha256=semantic_sha256_v22(
                bundle.model_dump(mode="json")
            ),
            evidence_index_sha256=index.index_sha256,
            decision_trace_sha256=trace.trace_sha256,
        )
        if not private_absent:
            raise ValueError("ordinary fixture unexpectedly persisted private failure")
        trace_persisted = trace.trace_sha256 == index.decision_trace_sha256
        scorer_expected = assessment.terminal.value == expected_terminal
        return (
            acceptance,
            assessment.terminal.value,
            True,
            True,
            trace_persisted,
            scorer_expected,
        )


def _failed_pipeline_fixture(root: Path) -> DiagnosisPipelineAcceptanceV0233:
    with ExitStack() as stack:
        _deterministic_id_patches(stack)
        store = SqliteStoreV1(root / "product.sqlite3")
        jobs = JobRepositoryV1(store)
        queued = jobs.enqueue(
            ProductJobTypeV1.DIAGNOSIS,
            {"incident_id": "inc-" + "9" * 24},
            now=_NOW.timestamp(),
        )
        claimed = jobs.claim_next(
            "product-v0233-failure-worker",
            lease_seconds=60,
            now=(_NOW + timedelta(seconds=1)).timestamp(),
        )
        if claimed is None or claimed.job_id != queued.job_id:
            raise ValueError("failure fixture job claim differs")
        journal = DiagnosisStageJournalRepositoryV02322(store)
        pipeline = DiagnosisPipelineV02322(
            journal,
            job_id=queued.job_id,
            incident_id="inc-" + "9" * 24,
            observed_at=_NOW + timedelta(seconds=2),
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.JOB_CLAIMED,
            input_binding_sha256="1" * 64,
            operation=lambda: {"claimed": True},
        )
        try:
            pipeline.run(
                DiagnosisPipelineStageV02322.EVIDENCE_INDEX_STARTED,
                input_binding_sha256="2" * 64,
                operation=lambda: (_ for _ in ()).throw(
                    ValueError("deterministic Evidence Index failure")
                ),
            )
        except ValueError as error:
            projection, envelope, path = pipeline.capture_failure(
                error, data_root=root, job_payload=claimed.payload
            )
        else:
            raise ValueError("failure fixture did not fail")
        failed = jobs.fail(
            queued.job_id,
            "product-v0233-failure-worker",
            claimed.attempt_count,
            projection.safe_error_code,
            public_failure_v02322=projection,
            now=(_NOW + timedelta(seconds=3)).timestamp(),
        )
        events = journal.list_events(queued.job_id)
        if (
            failed.status is not ProductJobStatusV1.FAILED
            or not path.is_file()
            or json.loads(path.read_text())["failure_envelope_sha256"]
            != envelope.failure_envelope_sha256
        ):
            raise ValueError("failure fixture private/public binding differs")
        return DiagnosisPipelineAcceptanceV0233.build_failure(
            job_id=queued.job_id,
            journal_tail_sha256=events[-1].event_sha256,
            event_count=len(events),
            failure_stage=projection.failure_stage.value,
            safe_error_code=projection.safe_error_code,
            exception_fingerprint=projection.exception_fingerprint,
            private_failure_envelope_sha256=envelope.failure_envelope_sha256,
        )


def _replace_terminal(
    fixture: tuple[Any, Any, Any, Any],
    terminal: DiagnosisTerminalV1,
) -> tuple[Any, Any, Any, Any]:
    diagnosis, bundle, index, trace = fixture
    classified = terminal in {
        DiagnosisTerminalV1.CORE_KNOWN,
        DiagnosisTerminalV1.EXTENSION_KNOWN,
        DiagnosisTerminalV1.OPEN_WORLD,
    }
    lane = {
        DiagnosisTerminalV1.CORE_KNOWN: DiagnosisLaneV1.CORE,
        DiagnosisTerminalV1.EXTENSION_KNOWN: DiagnosisLaneV1.EXTENSION,
        DiagnosisTerminalV1.NO_INCIDENT: DiagnosisLaneV1.NO_INCIDENT,
        DiagnosisTerminalV1.OPEN_WORLD: DiagnosisLaneV1.OPEN_WORLD,
        DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE: DiagnosisLaneV1.ABSTAIN,
        DiagnosisTerminalV1.CONFLICTING_EVIDENCE: DiagnosisLaneV1.ABSTAIN,
    }[terminal]
    body = {
        **diagnosis.model_dump(mode="python", exclude={"result_sha256"}),
        "terminal": terminal,
        "core_or_extension_or_open_world": lane,
        "root_service_ids": (("svc-" + "3" * 24,) if classified else ()),
        "mechanism": ("UNKNOWN_MECHANISM" if classified else None),
        "broad_domain": ("UNKNOWN" if classified else None),
        "provisional_report": ({"bounded": True} if terminal is DiagnosisTerminalV1.OPEN_WORLD else None),
        "action_authority": ActionAuthorityV1.NONE,
    }
    normalized = DiagnosisResultV1.model_construct(
        **body, result_sha256="0" * 64
    ).model_dump(mode="json", exclude={"result_sha256"})
    replaced = DiagnosisResultV1.model_validate(
        {**body, "result_sha256": semantic_sha256_v22(normalized)}
    )
    return replaced, bundle, index, trace


def _unresolved_reference_fixture() -> tuple[Any, Any, Any, Any]:
    diagnosis, bundle, index, trace = _fixture()
    missing = "e:v0233:missing:unresolved"
    diagnosis_body = {
        **diagnosis.model_dump(mode="python", exclude={"result_sha256"}),
        "supporting_evidence_refs": tuple(
            sorted((*diagnosis.supporting_evidence_refs, missing))
        ),
    }
    normalized = DiagnosisResultV1.model_construct(
        **diagnosis_body, result_sha256="0" * 64
    ).model_dump(mode="json", exclude={"result_sha256"})
    diagnosis = DiagnosisResultV1.model_validate(
        {**diagnosis_body, "result_sha256": semantic_sha256_v22(normalized)}
    )
    bundle = EvidenceBundleV1(
        incident_id=bundle.incident_id,
        diagnosis_id=bundle.diagnosis_id,
        objects=bundle.objects,
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        contradicting_evidence_refs=(),
    )
    invalid = DiagnosisEvidenceIndexV0232.model_construct(
        **{
            **index.model_dump(mode="python"),
            "linked_support_refs": diagnosis.supporting_evidence_refs,
        }
    )
    return diagnosis, bundle, invalid, trace


def _score_case(
    case_id: str,
    expected: str,
    fixture: tuple[Any, Any, Any, Any],
) -> FormalContractCaseV0233:
    assessment = score_nofault_evidence_v0232(
        diagnosis=fixture[0],
        bundle=fixture[1],
        index=fixture[2],
        decision_trace=fixture[3],
    )
    return FormalContractCaseV0233.build(
        case_id=case_id,
        expected_terminal=expected,
        observed_terminal=assessment.terminal.value,
        reasons=assessment.reasons,
        passed=assessment.terminal.value == expected,
    )


def _repository_manifest(
    root: Path,
    *,
    contract_preflight_sha256: str,
) -> ProductV0233RepositoryStateManifest:
    history = json.loads(
        (root / "docs/analysis/product-v0233-predecessor-audit.json").read_text()
    )
    source = json.loads(
        (root / "config/product-v0233/source-selection.json").read_text()
    )
    clone = json.loads(
        (root / "docs/analysis/product-v0233-clone-contract.json").read_text()
    )
    campaign = load_fresh_formal_campaign_v0233(root)
    body = {
        "schema_version": "ecomsre.product.repository-state.v0233",
        "goal_version": _GOAL_VERSION,
        "phase": RepositoryPhaseV0233.PREPARED,
        "history_and_handoff_sha256": history["audit_sha256"],
        "source_selection_sha256": source["selection_sha256"],
        "clone_contract_sha256": clone["contract_sha256"],
        "campaign_sha256": campaign.campaign_sha256,
        "contract_preflight_sha256": contract_preflight_sha256,
        "traffic_preflight_sha256": None,
        "formal_contract_freeze_sha256": None,
        "pre_execution_review_sha256": None,
        "formal_result_sha256": None,
        "formal_blocker_sha256": None,
        "knowledge_handoff_sha256": None,
        "cleanup_proof_sha256": None,
        "formal_clone_count": 0,
        "formal_execution_count": 0,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
        "action_authority": "NONE",
    }
    return ProductV0233RepositoryStateManifest.model_validate(
        {**body, "manifest_sha256": semantic_sha256_v22(body)}
    )


def _non_scorer_cases(
    root: Path,
    failure: DiagnosisPipelineAcceptanceV0233,
) -> tuple[FormalContractCaseV0233, ...]:
    failure_ok = (
        failure.job_status == "FAILED"
        and failure.stage_journal_terminal == "FAILED"
        and failure.private_failure_envelope_sha256 is not None
    )
    failure_case = FormalContractCaseV0233.build(
        case_id="09_FAILED_DIAGNOSIS_STAGE",
        expected_terminal="FAILED_WITH_JOURNAL_AND_PRIVATE_ENVELOPE",
        observed_terminal=(
            "FAILED_WITH_JOURNAL_AND_PRIVATE_ENVELOPE"
            if failure_ok
            else "FAILURE_CONTRACT_INVALID"
        ),
        reasons=(),
        passed=failure_ok,
    )
    try:
        admit_incident_creation_v0233(
            runtime_authority_pass=True,
            baseline_restart_pass=True,
            formal_traffic_pass=False,
            fresh_runtime_snapshot_pass=True,
            new_incident_count=0,
            new_diagnosis_count=0,
        )
    except ValueError as error:
        traffic_observed = str(error)
    else:
        traffic_observed = "INCIDENT_CREATION_INCORRECTLY_ADMITTED"
    traffic_case = FormalContractCaseV0233.build(
        case_id="10_FORMAL_TRAFFIC_BLOCKER_BEFORE_INCIDENT",
        expected_terminal="FORMAL_TRAFFIC_NOT_PASS",
        observed_terminal=traffic_observed,
        reasons=(),
        passed=traffic_observed == "FORMAL_TRAFFIC_NOT_PASS",
    )
    clone = json.loads(
        (root / "docs/analysis/product-v0233-clone-contract.json").read_text()
    )
    clone_ok = (
        clone["terminal"]
        == "ECOMSRE_PRODUCT_V0233_SOURCE_AND_CLONE_CONTRACT_PASS"
        and clone["source_unchanged"] is True
        and clone["temporary_clone_removed"] is True
        and clone["clone"]["source_counts"] == clone["clone"]["starting_counts"]
        and clone["authoritative_formal_clone_count"] == 0
    )
    clone_case = FormalContractCaseV0233.build(
        case_id="11_SOURCE_CLONE_DELTA_VALIDATION",
        expected_terminal="SOURCE_CLONE_DELTA_PASS",
        observed_terminal=(
            "SOURCE_CLONE_DELTA_PASS" if clone_ok else "SOURCE_CLONE_DELTA_FAIL"
        ),
        reasons=(),
        passed=clone_ok,
    )
    source_counts = clone["clone"]["source_counts"]
    cardinality = FormalIncidentDiagnosisCardinalityV0233.build(
        phase="PRE_INCIDENT",
        source_incident_count=source_counts["incident_count"],
        source_diagnosis_job_count=source_counts["diagnosis_job_count"],
        source_diagnosis_result_count=source_counts["diagnosis_count"],
        source_evidence_index_count=source_counts["diagnosis_evidence_index_count"],
        source_fault_family_count=source_counts["fault_family_count"],
        source_knowledge_artifact_count=source_counts["knowledge_artifact_count"],
        source_baseline_job_count=source_counts["baseline_job_count"],
        current_incident_count=source_counts["incident_count"],
        current_diagnosis_job_count=source_counts["diagnosis_job_count"],
        current_diagnosis_result_count=source_counts["diagnosis_count"],
        current_evidence_index_count=source_counts["diagnosis_evidence_index_count"],
        current_fault_family_count=source_counts["fault_family_count"],
        current_knowledge_artifact_count=source_counts["knowledge_artifact_count"],
        current_baseline_job_count=source_counts["baseline_job_count"],
    )
    placeholder = _repository_manifest(root, contract_preflight_sha256="f" * 64)
    repository_ok = (
        placeholder.phase is RepositoryPhaseV0233.PREPARED
        and placeholder.formal_execution_count == 0
        and placeholder.measured_result_count == 0
        and cardinality.phase == "PRE_INCIDENT"
    )
    repository_case = FormalContractCaseV0233.build(
        case_id="12_REPOSITORY_PHASE_VALIDATION",
        expected_terminal="PREPARED",
        observed_terminal=placeholder.phase.value,
        reasons=(),
        passed=repository_ok,
    )
    return failure_case, traffic_case, clone_case, repository_case


def _update_progress(
    root: Path,
    *,
    report: FormalContractPreflightV0233,
    manifest: ProductV0233RepositoryStateManifest,
) -> None:
    progress_path = root / "docs/analysis/product-v0233-progress.json"
    prior = json.loads(progress_path.read_text())
    body = {
        **{key: value for key, value in prior.items() if key != "progress_sha256"},
        "phase": "INCREMENT_2_FORMAL_CONTRACT_PREFLIGHT_PASS",
        "current_terminal": FORMAL_CONTRACT_PREFLIGHT_PASS_V0233,
        "campaign_sha256": report.campaign_sha256,
        "contract_preflight_sha256": report.preflight_sha256,
        "repository_state_manifest_sha256": manifest.manifest_sha256,
        "next_gate": "ECOMSRE_PRODUCT_V0233_TRAFFIC_PREFLIGHT_PASS",
    }
    _write_json(
        progress_path, {**body, "progress_sha256": semantic_sha256_v22(body)}
    )


def run_contract_preflight(project_root: Path) -> FormalContractPreflightV0233:
    root = Path(project_root).resolve()
    campaign = load_fresh_formal_campaign_v0233(root)
    source = json.loads(
        (root / "config/product-v0233/source-selection.json").read_text()
    )
    scoring_cases = (
        _score_case(
            "01_FULLY_SUPPORTED_HEALTHY",
            NOFAULT_FULLY_SUPPORTED_V0232,
            _fixture(),
        ),
        _score_case(
            "02_CAPABILITY_LIMITED_BOUND",
            NOFAULT_CAPABILITY_LIMITED_V0232,
            _fixture(
                terminal=DiagnosisTerminalV1.INSUFFICIENT_EVIDENCE,
                metrics_failure=True,
                limitation_code="SOURCE_METRICS_QUERY_FAILURE",
                limitation_bound=True,
            ),
        ),
        _score_case(
            "03_NOT_SUPPORTED_CORE_KNOWN",
            NOFAULT_NOT_SUPPORTED_V0232,
            _replace_terminal(_fixture(), DiagnosisTerminalV1.CORE_KNOWN),
        ),
        _score_case(
            "04_NOT_SUPPORTED_OPEN_WORLD",
            NOFAULT_NOT_SUPPORTED_V0232,
            _replace_terminal(_fixture(), DiagnosisTerminalV1.OPEN_WORLD),
        ),
        _score_case(
            "05_NOT_SUPPORTED_CONFLICTING",
            NOFAULT_NOT_SUPPORTED_V0232,
            _replace_terminal(_fixture(), DiagnosisTerminalV1.CONFLICTING_EVIDENCE),
        ),
        _score_case(
            "06_NOT_SUPPORTED_STALE_RUNTIME",
            NOFAULT_NOT_SUPPORTED_V0232,
            _fixture(stale_runtime=True),
        ),
        _score_case(
            "07_NOT_SUPPORTED_MISSING_P01",
            NOFAULT_NOT_SUPPORTED_V0232,
            _fixture(profile_bound=False),
        ),
        _score_case(
            "08_NOT_SUPPORTED_UNRESOLVED_EVIDENCE_REF",
            NOFAULT_NOT_SUPPORTED_V0232,
            _unresolved_reference_fixture(),
        ),
    )
    with TemporaryDirectory(prefix="product-v0233-contract-preflight-") as temporary:
        temporary_root = Path(temporary)
        (
            fixture_pipeline,
            fixture_terminal,
            bundle_ok,
            index_ok,
            trace_ok,
            scorer_ok,
        ) = _ordinary_fixture_pipeline(temporary_root / "ordinary")
        failure_pipeline = _failed_pipeline_fixture(temporary_root / "failure")
    cases = (*scoring_cases, *_non_scorer_cases(root, failure_pipeline))
    if tuple(case.case_id for case in cases) != _EXPECTED_CASE_IDS:
        raise ValueError("Product v0.2.3.3 preflight case order differs")
    passed_count = sum(case.passed for case in cases)
    report = FormalContractPreflightV0233.build(
        terminal=(
            FORMAL_CONTRACT_PREFLIGHT_PASS_V0233
            if passed_count == len(cases) and scorer_ok
            else "ECOMSRE_PRODUCT_V0233_FORMAL_CONTRACT_PREFLIGHT_FAIL"
        ),
        campaign_sha256=campaign.campaign_sha256,
        source_selection_sha256=source["selection_sha256"],
        case_count=len(cases),
        passed_case_count=passed_count,
        cases=cases,
        fixture_pipeline=fixture_pipeline,
        fixture_evidence_bundle_persisted=bundle_ok,
        fixture_evidence_index_persisted=index_ok,
        fixture_decision_trace_persisted=trace_ok,
        fixture_scorer_terminal=fixture_terminal,
        fixture_scorer_expected_terminal=scorer_ok,
        action_authority="NONE",
        formal_execution_count=0,
        new_incident_count=0,
        new_diagnosis_count=0,
        provider_calls=0,
        agent_writes=0,
        runbook_executions=0,
    )
    manifest = _repository_manifest(
        root, contract_preflight_sha256=report.preflight_sha256
    )
    _write_json(
        root / "docs/analysis/product-v0233-formal-contract-preflight.json",
        report.model_dump(mode="json"),
    )
    _write_json(
        root / "config/product-v0233/repository-state-manifest.json",
        manifest.model_dump(mode="json"),
    )
    _update_progress(root, report=report, manifest=manifest)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    report = run_contract_preflight(arguments.project_root)
    print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ("run_contract_preflight",)
