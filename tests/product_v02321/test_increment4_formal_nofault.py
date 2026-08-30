from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from ecomsre.dta_v2.v22.read_contracts import RuntimeStateV22, semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.incidents.contracts import (
    EvidenceBundleV1,
    EvidenceObjectV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    ConnectorEvidenceBindingV0232,
    DiagnosisEvidenceIndexV0232,
    OpenSearchProfileEvidenceBindingV0232,
    RuntimeSnapshotEvidenceBindingV0232,
)
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.pilot.formal_contract_v02321 import (
    FormalContractFreezeV02321,
)
from ecomsre.product.pilot.formal_nofault_v02321 import (
    BASELINE_RESTART_PASS_V02321,
    FORMAL_HEALTHY_TRAFFIC_PASS_V02321,
    NOFAULT_ACCEPTANCE_COMPLETE_V02321,
    RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321,
    BaselineRestartProofV02321,
    FormalCloneReservationV02321,
    FormalExecutionAdmissionV02321,
    FormalExecutionBlockerV02321,
    FormalTrafficBlockerV02321,
    FormalTrafficConsumptionV02321,
    FormalTrafficDispatchCheckpointV02321,
    FormalTrafficObservationCheckpointV02321,
    FormalTrafficResultV02321,
    FreshRuntimeSnapshotProofV02321,
    NoFaultAcceptanceResultV02321,
    RuntimeAuthorityProofV02321,
    FormalBlockerClosureV02321,
    measured_terminal_v02321,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    UPSTREAM_COMMIT_V0232,
    CheckoutTransactionObservationV0232,
    HealthyTrafficExecutionV0232,
    HealthyTrafficRunnerV0232,
    IncidentTrafficBindingV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NoFaultEvidenceAssessmentV0232,
    NoFaultMeasuredTerminalV0232,
    score_nofault_evidence_v0232,
)
from ecomsre.product.pilot.product_state_clone_v02321 import (
    FormalProductPoststateV02321,
    FormalStateCloneReportV02321,
    PreflightStateCloneReportV02321,
)
from ecomsre.product.pilot.product_state_clone_v0232 import ProductStateSourceV0232
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    load_traffic_profile_v0232,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre_live_sandbox.contracts import canonical_json_bytes, write_private_json
from scripts.product_v02321 import run_state_clone
from scripts.product_v02321 import run_formal_nofault
from scripts.product_v02321.run_formal_nofault import run_formal_nofault_v02321
from scripts.product_v0232.run_evidence_binding_preflight import _fixture


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
HEAD = "b" * 40
RUNTIME_DESCRIPTOR_PATH = (
    ROOT / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
)
RUNTIME_DESCRIPTOR_SHA256 = json.loads(RUNTIME_DESCRIPTOR_PATH.read_bytes())[
    "descriptor_sha256"
]


def _prepare_temp_formal_repository(root: Path) -> str:
    freeze = json.loads(
        (ROOT / "docs/analysis/product-v02321-formal-contract-freeze.json").read_bytes()
    )
    paths = {str(item["path"]) for item in freeze["frozen_files"]} | {
        ".gitignore",
        "docs/analysis/product-v02321-formal-contract-freeze.json",
        "docs/analysis/product-v0231-runtime-authority-descriptor.json",
        "docs/analysis/product-v0232-predecessor-audit.json",
        "docs/external-reviews/product-v02321-pre-execution-review.md",
        "scripts/product_v02321/run_state_clone.py",
        "scripts/product_v02321/run_formal_nofault.py",
        "src/ecomsre/product/pilot/formal_contract_v02321.py",
        "src/ecomsre/product/pilot/formal_nofault_v02321.py",
        "src/ecomsre/product/pilot/product_state_clone_v02321.py",
    }
    upstream_files = {
        "src/frontend/gateways/Api.gateway.ts",
        "src/frontend/pages/api/cart.ts",
        "src/frontend/pages/api/checkout.ts",
        "src/frontend/protos/demo.ts",
        "src/frontend/types/Cart.ts",
        "src/load-generator/people.json",
        "src/load-generator/script.js",
    }
    paths.update(
        f"third_party/opentelemetry-demo/{relative}" for relative in upstream_files
    )
    for relative in sorted(paths):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    review_path = root / "docs/external-reviews/product-v02321-pre-execution-review.md"
    review_text = review_path.read_text(encoding="utf-8")
    review_start = "<!-- ECOMSRE_PRODUCT_V02321_REVIEW_JSON_START -->\n```json\n"
    review_end = "\n```\n<!-- ECOMSRE_PRODUCT_V02321_REVIEW_JSON_END -->"
    review_payload = json.loads(
        review_text.split(review_start, 1)[1].split(review_end, 1)[0]
    )
    reviewed_files = {
        "formal_contract_verifier_file_sha256": (
            "src/ecomsre/product/pilot/formal_contract_v02321.py"
        ),
        "formal_nofault_contract_file_sha256": (
            "src/ecomsre/product/pilot/formal_nofault_v02321.py"
        ),
        "formal_nofault_runner_file_sha256": (
            "scripts/product_v02321/run_formal_nofault.py"
        ),
        "formal_state_clone_contract_file_sha256": (
            "src/ecomsre/product/pilot/product_state_clone_v02321.py"
        ),
        "formal_state_clone_runner_file_sha256": (
            "scripts/product_v02321/run_state_clone.py"
        ),
    }
    review_payload.update(
        {
            field: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for field, relative in reviewed_files.items()
        }
    )
    review_payload.pop("review_sha256", None)
    review_payload["review_sha256"] = semantic_sha256_v22(review_payload)
    rendered_review = json.dumps(review_payload, indent=2, ensure_ascii=False)
    review_path.write_text(
        review_text.split(review_start, 1)[0]
        + review_start
        + rendered_review
        + review_end
        + review_text.split(review_end, 1)[1],
        encoding="utf-8",
    )
    subprocess.run(("git", "init", "-q"), cwd=root, check=True)
    subprocess.run(("git", "add", "."), cwd=root, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Product Fixture",
            "-c",
            "user.email=product-fixture@example.invalid",
            "commit",
            "-qm",
            "formal recovery fixture",
        ),
        cwd=root,
        check=True,
    )
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    upstream_git = root / "third_party/opentelemetry-demo/.git"
    upstream_git.mkdir()
    (upstream_git / "HEAD").write_text(f"{UPSTREAM_COMMIT_V0232}\n", encoding="utf-8")
    return head


def _rebind_state_locator(
    state: ProductStateSourceV0232,
    locator: str,
) -> ProductStateSourceV0232:
    body = state.model_dump(mode="json", exclude={"source_sha256"})
    body["source_locator"] = locator
    return ProductStateSourceV0232.model_validate(
        {**body, "source_sha256": semantic_sha256_v22(body)}
    )


def _prepare_temp_recoverable_formal_clone(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FormalExecutionAdmissionV02321, bytes]:
    _prepare_temp_formal_repository(root)
    admission, _freeze, _review = run_formal_nofault._strict_admission(root)
    preflight = PreflightStateCloneReportV02321.model_validate_json(
        (
            ROOT / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_bytes()
    )
    destination_root = root / admission.formal_clone_destination_locator
    destination_root.mkdir(parents=True)
    destination = _rebind_state_locator(
        preflight.destination_state,
        admission.formal_clone_destination_locator,
    )

    def admit_fixture(
        state_root: Path,
        *,
        locator: str,
    ) -> ProductStateSourceV0232:
        if state_root != destination_root or locator != destination.source_locator:
            raise AssertionError("unexpected formal clone fixture admission")
        return destination

    monkeypatch.setattr(run_formal_nofault, "_admit_state", admit_fixture)
    clone = run_state_clone._bind_existing_clone(
        source=preflight.source_state,
        destination=destination,
        destination_locator=admission.formal_clone_destination_locator,
    )
    report = FormalStateCloneReportV02321.build(
        formal_admission_sha256=admission.admission_sha256,
        formal_clone_plan_sha256=admission.formal_clone_plan_sha256,
        source_repository_binding=preflight.source_repository_binding,
        predecessor_private_acceptance=preflight.predecessor_private_acceptance,
        source_state=preflight.source_state.model_dump(mode="json"),
        clone=clone.model_dump(mode="json"),
        destination_state=destination.model_dump(mode="json"),
        destination_locator=admission.formal_clone_destination_locator,
    )
    report_bytes = canonical_json_bytes(report.model_dump(mode="json"))
    report_path = root / "docs/analysis/product-v02321-product-state-clone-formal.json"
    report_path.write_bytes(report_bytes)
    _write_reservation(root, admission)
    return admission, report_bytes


def _prepare_consumed_recovery_fixture(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    FormalExecutionAdmissionV02321,
    Path,
    FormalTrafficConsumptionV02321,
]:
    admission, _report_bytes = _prepare_temp_recoverable_formal_clone(
        root,
        monkeypatch,
    )
    private_root = root / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    write_private_json(
        private_root / "admission.json",
        admission.model_dump(mode="json"),
        create_once=True,
    )
    freeze = FormalContractFreezeV02321.model_validate_json(
        (root / "docs/analysis/product-v02321-formal-contract-freeze.json").read_bytes()
    )
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=admission.admission_sha256,
        execution_head=admission.execution_head,
        traffic_contract_sha256=freeze.traffic_contract_sha256,
        formal_profile_sha256=freeze.formal_profile_sha256,
        episode_started_at=datetime.now(UTC),
    )
    write_private_json(
        private_root / "traffic-consumption.json",
        consumption.model_dump(mode="json"),
        create_once=True,
    )
    assert (
        run_formal_nofault._reserved_admission_for_private_recovery_v02321(
            root=root,
            private_root=private_root,
        )
        == admission
    )
    return admission, private_root, consumption


def _admission(
    *,
    source_state_sha256: str = "f" * 64,
    formal_clone_plan_sha256: str = "1" * 64,
    destination_locator: str = (
        ".local/product-v02321/product-state/formal-ffffffffffffffffffffffff/product"
    ),
) -> FormalExecutionAdmissionV02321:
    return FormalExecutionAdmissionV02321.build(
        execution_head=HEAD,
        formal_contract_freeze_sha256=SHA,
        formal_contract_freeze_file_sha256="c" * 64,
        pre_execution_review_sha256="d" * 64,
        pre_execution_review_file_sha256="e" * 64,
        source_state_sha256=source_state_sha256,
        formal_clone_plan_sha256=formal_clone_plan_sha256,
        formal_clone_destination_locator=destination_locator,
        formal_runner_file_sha256="2" * 64,
        formal_contract_file_sha256="3" * 64,
        runtime_continuity_descriptor_sha256=RUNTIME_DESCRIPTOR_SHA256,
        runtime_continuity_descriptor_file_sha256=(
            run_formal_nofault._sha256(RUNTIME_DESCRIPTOR_PATH)
        ),
    )


def _copy_formal_freeze(root: Path) -> FormalContractFreezeV02321:
    target = root / "docs/analysis/product-v02321-formal-contract-freeze.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "docs/analysis/product-v02321-formal-contract-freeze.json",
        target,
    )
    return FormalContractFreezeV02321.model_validate_json(target.read_bytes())


def _unproven_closure(admission: FormalExecutionAdmissionV02321) -> dict[str, object]:
    return run_formal_nofault._unproven_blocker_closure_v02321(admission).model_dump(
        mode="json"
    )


def _observed_state(
    count: int,
    *,
    fault_family_count: int = 0,
    knowledge_artifact_count: int = 0,
    provider_calls: int = 0,
    agent_writes: int = 0,
    runbook_executions: int = 0,
    action_authority: str = "NONE",
) -> dict[str, int | str]:
    return {
        "incident_count": count,
        "diagnosis_count": count,
        "diagnosis_job_count": count,
        "fault_family_count": fault_family_count,
        "knowledge_artifact_count": knowledge_artifact_count,
        "provider_calls": provider_calls,
        "agent_writes": agent_writes,
        "runbook_executions": runbook_executions,
        "action_authority": action_authority,
    }


def _write_full_traffic_journal(
    *,
    private_root: Path,
    consumption: FormalTrafficConsumptionV02321,
    execution: HealthyTrafficExecutionV0232,
) -> None:
    for observation in execution.observations:
        ordinal = observation.ordinal
        dispatch = FormalTrafficDispatchCheckpointV02321.build(
            consumption_sha256=consumption.consumption_sha256,
            ordinal=ordinal,
            cart_payload_sha256=hashlib.sha256(f"cart-{ordinal}".encode()).hexdigest(),
            checkout_payload_sha256=hashlib.sha256(
                f"checkout-{ordinal}".encode()
            ).hexdigest(),
        )
        checkpoint = FormalTrafficObservationCheckpointV02321.build(
            consumption_sha256=consumption.consumption_sha256,
            dispatch_checkpoint_sha256=dispatch.checkpoint_sha256,
            observation=observation.model_dump(mode="json"),
        )
        write_private_json(
            private_root / f"traffic-journal/traffic-dispatch-{ordinal:03d}.json",
            dispatch.model_dump(mode="json"),
            create_once=True,
        )
        write_private_json(
            private_root / f"traffic-journal/traffic-observation-{ordinal:03d}.json",
            checkpoint.model_dump(mode="json"),
            create_once=True,
        )


def _write_reservation(
    root: Path,
    admission: FormalExecutionAdmissionV02321,
) -> FormalCloneReservationV02321:
    reservation = FormalCloneReservationV02321.build(admission=admission)
    write_private_json(
        root / ".local/product-v02321/formal-reservation.json",
        reservation.model_dump(mode="json"),
        create_once=True,
    )
    return reservation


def _healthy_checkout_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if request.url.path == "/api/cart":
        return httpx.Response(
            200,
            json={
                "userId": payload["userId"],
                "items": [{"productId": "0PUK6V6EV0", "quantity": 1}],
            },
        )
    return httpx.Response(
        200,
        json={
            "orderId": "order-fixture",
            "shippingTrackingId": "tracking-fixture",
            "shippingCost": {
                "currencyCode": "USD",
                "units": 1,
                "nanos": 0,
            },
            "shippingAddress": {
                "streetAddress": "1 Contract Way",
                "city": "Local",
                "state": "CA",
                "country": "United States",
                "zipCode": "94016",
            },
            "items": [
                {
                    "item": {
                        "productId": "0PUK6V6EV0",
                        "quantity": 1,
                        "product": {"id": "0PUK6V6EV0"},
                    },
                    "cost": {
                        "currencyCode": "USD",
                        "units": 1,
                        "nanos": 0,
                    },
                }
            ],
        },
    )


def _successful_formal_execution() -> HealthyTrafficExecutionV0232:

    profile = load_traffic_profile_v0232(ROOT, role="FORMAL")
    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(_healthy_checkout_handler),
        sleep=lambda _seconds: None,
    ) as runner:
        return runner.run(
            endpoint="http://127.0.0.1:18080/api/checkout",
            profile=profile,
            contract=load_checkout_traffic_contract_v0232(ROOT),
            role="FORMAL",
        )


def _incident(
    incident_id: str = "inc-0123456789abcdef01234567",
    *,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    environment_id: str = "env-" + "1" * 24,
    baseline_id: str = "base-" + "2" * 24,
    baseline_sha256: str = "3" * 64,
    diagnosis_observed_at: datetime | None = None,
    external_incident_key: str = "product-v02321-nofault-fixture",
    service_identity_sha256: str = "4" * 64,
    source_capability_sha256: str = "5" * 64,
    created_at: datetime | None = None,
) -> IncidentRecordV1:
    now = datetime.now(UTC)
    draft = IncidentRecordV1.model_construct(
        schema_version="ecomsre.product.incident.v1",
        incident_id=incident_id,
        environment_id=environment_id,
        external_incident_key=external_incident_key,
        alert_name="No-Fault fixture",
        summary="No-Fault fixture",
        started_at=started_at or now,
        ended_at=ended_at or now,
        candidate_service_ids=("svc-checkout",),
        labels={"fault": "none"},
        baseline_id=baseline_id,
        baseline_sha256=baseline_sha256,
        service_identity_sha256=service_identity_sha256,
        source_capability_sha256=source_capability_sha256,
        candidate_logical_services=("checkout",),
        diagnosis_observed_at=diagnosis_observed_at or ended_at or now,
        created_at=created_at or now,
        incident_sha256="0" * 64,
    )
    body = draft.model_dump(mode="json", exclude={"incident_sha256"})
    return IncidentRecordV1.model_validate(
        {**body, "incident_sha256": semantic_sha256_v22(body)}
    )


def _bound_fixture_evidence(
    *,
    environment_id: str,
    window: ConnectorWindowV1,
    snapshot: PilotRuntimeSnapshotV02,
    pilot_runtime_authority_sha256: str,
    read_authority_sha256: str,
) -> tuple[object, EvidenceBundleV1, DiagnosisEvidenceIndexV0232, object]:
    diagnosis, fixture_bundle, fixture_index, trace = _fixture()
    rebound_objects: list[EvidenceObjectV1] = []
    for item in fixture_bundle.objects:
        result = ConnectorQueryResultV1.model_validate_json(
            json.dumps(item.payload["connector_result"])
        )
        rebound_result = ConnectorQueryResultV1.build(
            source=result.source,
            status=result.status,
            requested_services=result.requested_services,
            covered_services=result.covered_services,
            window=window,
            records=result.records,
            truncated=result.truncated,
            safe_error_code=result.safe_error_code,
            latency_ms=result.latency_ms,
        )
        entry = item.payload["connector_bindings_v0232"][0]
        generic = ConnectorEvidenceBindingV0232.model_validate_json(
            json.dumps(entry["connector_binding"])
        )
        specialized_payload = entry["binding_payload"]
        rebound_specialized: dict[str, object] | None = None
        binding_payload_sha256 = rebound_result.result_sha256
        if generic.binding_kind.value == "OPENSEARCH_PROFILE":
            profile = OpenSearchProfileEvidenceBindingV0232.model_validate_json(
                json.dumps(specialized_payload)
            )
            rebound_profile = OpenSearchProfileEvidenceBindingV0232.build(
                **profile.model_dump(
                    mode="python",
                    exclude={
                        "schema_version",
                        "binding_sha256",
                        "connector_result_sha256",
                        "query_window",
                    },
                ),
                connector_result_sha256=rebound_result.result_sha256,
                query_window=window,
            )
            rebound_specialized = rebound_profile.model_dump(mode="json")
            binding_payload_sha256 = rebound_profile.binding_sha256
        elif generic.binding_kind.value == "RUNTIME_SNAPSHOT":
            rebound_runtime = RuntimeSnapshotEvidenceBindingV0232.build(
                runtime_snapshot_sha256=snapshot.snapshot_sha256,
                runtime_snapshot_observed_at=snapshot.observed_at,
                runtime_snapshot_environment_id=snapshot.environment_id,
                runtime_snapshot_authority_sha256=snapshot.authority_sha256,
                pilot_runtime_authority_sha256=pilot_runtime_authority_sha256,
                read_authority_sha256=read_authority_sha256,
                connector_binding_sha256=snapshot.authority_sha256,
                maximum_age_seconds=600,
                age_at_query_seconds=(
                    window.ended_at - snapshot.observed_at
                ).total_seconds(),
                requested_services=("checkout",),
                covered_services=("checkout",),
                connector_result_sha256=rebound_result.result_sha256,
                query_window=window,
            )
            rebound_specialized = rebound_runtime.model_dump(mode="json")
            binding_payload_sha256 = rebound_runtime.binding_sha256
        rebound_generic = ConnectorEvidenceBindingV0232.build(
            **generic.model_dump(
                mode="python",
                exclude={
                    "schema_version",
                    "binding_sha256",
                    "environment_id",
                    "query_context_sha256",
                    "component_result_sha256",
                    "combined_result_sha256",
                    "window",
                    "binding_payload_sha256",
                },
            ),
            environment_id=environment_id,
            query_context_sha256=semantic_sha256_v22(
                {
                    "environment_id": environment_id,
                    "source": result.source.value,
                    "window": window.model_dump(mode="json"),
                }
            ),
            component_result_sha256=rebound_result.result_sha256,
            combined_result_sha256=rebound_result.result_sha256,
            window=window,
            binding_payload_sha256=binding_payload_sha256,
        )
        payload = {
            "schema_version": "ecomsre.product.read-snapshot.v1",
            "connector_components": [rebound_result.model_dump(mode="json")],
            "connector_result": rebound_result.model_dump(mode="json"),
            "connector_bindings_v0232": [
                {
                    "connector_binding": rebound_generic.model_dump(mode="json"),
                    "binding_payload": rebound_specialized,
                }
            ],
        }
        object_sha256 = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        rebound_objects.append(
            EvidenceObjectV1(
                evidence_ref=item.evidence_ref,
                source=item.source,
                action_id=item.action_id,
                object_sha256=object_sha256,
                payload=payload,
            )
        )
    bundle = EvidenceBundleV1(
        incident_id=fixture_bundle.incident_id,
        diagnosis_id=fixture_bundle.diagnosis_id,
        objects=tuple(rebound_objects),
        supporting_evidence_refs=fixture_bundle.supporting_evidence_refs,
        contradicting_evidence_refs=fixture_bundle.contradicting_evidence_refs,
    )
    index = DiagnosisEvidenceIndexV0232.build(
        **fixture_index.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "index_sha256",
                "evidence_bundle_sha256",
                "all_object_sha256_by_ref",
            },
        ),
        evidence_bundle_sha256=semantic_sha256_v22(bundle.model_dump(mode="json")),
        all_object_sha256_by_ref={
            item.evidence_ref: item.object_sha256 for item in rebound_objects
        },
    )
    return diagnosis, bundle, index, trace


def _diagnosis_job() -> ProductJobRecordV1:
    incident_id = "inc-0123456789abcdef01234567"
    return ProductJobRecordV1(
        job_id="job-0123456789abcdef01234567",
        job_type=ProductJobTypeV1.DIAGNOSIS,
        status=ProductJobStatusV1.PENDING,
        payload={"incident_id": incident_id},
        idempotency_key=f"diagnosis:{incident_id}",
        attempt_count=0,
        created_at=1.0,
        updated_at=1.0,
    )


def test_formal_admission_is_self_sealed_and_zero_state() -> None:
    admission = _admission()
    assert admission.formal_healthy_traffic_execution_count == 0
    assert admission.accepted_successor_incident_count == 0
    assert admission.successor_diagnosis_count == 0
    assert admission.action_authority == "NONE"
    assert len(admission.admission_sha256) == 64


def test_formal_clone_rechecks_review_before_resolving_or_writing_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GateBlocked(RuntimeError):
        pass

    def reject_before_mutation(_root: Path) -> object:
        raise GateBlocked("strict review gate")

    monkeypatch.setattr(
        run_state_clone,
        "verify_formal_pre_execution_review_v02321",
        reject_before_mutation,
    )
    with pytest.raises(GateBlocked, match="strict review gate"):
        run_state_clone.create_formal_state_clone_v02321(
            project_root=tmp_path,
            source_root=tmp_path / "missing-source",
            predecessor_private_acceptance=tmp_path / "missing-acceptance.json",
            admission=_admission(),
        )
    assert not (tmp_path / ".local/product-v02321").exists()
    assert not (
        tmp_path / "docs/analysis/product-v02321-product-state-clone-formal.json"
    ).exists()


def test_formal_runner_refuses_an_existing_private_root_before_discovery(
    tmp_path: Path,
) -> None:
    (tmp_path / ".local/product-v02321/formal").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="formal execution already began"):
        run_formal_nofault_v02321(project_root=tmp_path)


def test_reservation_is_durable_before_clone_and_drives_reentry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopBeforeClone(RuntimeError):
        pass

    predecessor = tmp_path / "predecessor"
    source = predecessor / "source-product"
    source.mkdir(parents=True)
    private_acceptance = predecessor / "acceptance.json"
    private_acceptance.write_text("{}\n", encoding="utf-8")
    admission = _admission()
    freeze = SimpleNamespace(source_state_sha256=admission.source_state_sha256)
    source_state = SimpleNamespace(source_sha256=admission.source_state_sha256)
    clone_calls = 0

    monkeypatch.setattr(
        run_formal_nofault,
        "_require_preserved_runtime_root_v02321",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_strict_admission",
        lambda _root: (admission, freeze, object()),
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_admit_state",
        lambda *_args, **_kwargs: source_state,
    )

    def stop_clone(**kwargs: object) -> object:
        nonlocal clone_calls
        clone_calls += 1
        reservation_path = tmp_path / ".local/product-v02321/formal-reservation.json"
        reservation = FormalCloneReservationV02321.model_validate_json(
            reservation_path.read_bytes()
        )
        assert reservation.admission == admission
        assert kwargs["strict_gate_already_verified"] is True
        raise StopBeforeClone("reservation observed before clone")

    monkeypatch.setattr(
        run_formal_nofault,
        "create_formal_state_clone_v02321",
        stop_clone,
    )
    with pytest.raises(StopBeforeClone, match="reservation observed"):
        run_formal_nofault_v02321(
            project_root=tmp_path,
            predecessor_root=predecessor,
            source_product_root=source,
            predecessor_private_acceptance=private_acceptance,
        )

    monkeypatch.setattr(
        run_formal_nofault,
        "_strict_admission",
        lambda _root: pytest.fail("strict absence gate must not rerun"),
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_verify_admission_after_reservation",
        lambda _root, observed, **_kwargs: (
            freeze if observed == admission else pytest.fail()
        ),
    )
    with pytest.raises(StopBeforeClone, match="reservation observed"):
        run_formal_nofault_v02321(
            project_root=tmp_path,
            predecessor_root=predecessor,
            source_product_root=source,
            predecessor_private_acceptance=private_acceptance,
        )
    assert clone_calls == 2


def test_strict_review_precedes_clean_head_audit_artifact_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReviewBlocked(RuntimeError):
        pass

    clean_head_called = False

    def reject_review(_root: Path) -> object:
        raise ReviewBlocked("review first")

    def audited_clean_head(_root: Path) -> str:
        nonlocal clean_head_called
        clean_head_called = True
        return HEAD

    monkeypatch.setattr(
        run_formal_nofault,
        "verify_formal_pre_execution_review_v02321",
        reject_review,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_require_clean_head",
        audited_clean_head,
    )
    with pytest.raises(ReviewBlocked, match="review first"):
        run_formal_nofault._strict_admission(tmp_path)
    assert clean_head_called is False


def test_reserved_readmission_regular_path_gate_rejects_symlink_substitution(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real.py"
    real.write_text("print('same bytes')\n", encoding="utf-8")
    linked = tmp_path / "runner.py"
    linked.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        run_formal_nofault._require_regular_path(
            root=tmp_path,
            path=linked,
            directory=False,
            label="reserved runner",
        )


def test_reserved_readmission_accepts_only_the_exact_validated_clone_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, report_bytes = _prepare_temp_recoverable_formal_clone(
        tmp_path,
        monkeypatch,
    )
    assert not (tmp_path / ".local/product-v02321/formal").exists()
    assert (
        run_formal_nofault._validated_formal_clone_report_bytes_v02321(
            root=tmp_path,
            admission=admission,
        )
        == report_bytes
    )
    freeze = run_formal_nofault._verify_admission_after_reservation(
        tmp_path,
        admission,
        allowed_public_files={
            "docs/analysis/product-v02321-product-state-clone-formal.json": (
                report_bytes
            )
        },
    )
    assert freeze.formal_clone_plan.plan_sha256 == admission.formal_clone_plan_sha256


def test_reserved_readmission_rejects_noncanonical_clone_report_before_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, report_bytes = _prepare_temp_recoverable_formal_clone(
        tmp_path,
        monkeypatch,
    )
    report_path = (
        tmp_path / "docs/analysis/product-v02321-product-state-clone-formal.json"
    )
    report_path.write_text(
        json.dumps(json.loads(report_bytes), indent=2) + "\n",
        encoding="utf-8",
    )
    assert not (tmp_path / ".local/product-v02321/formal").exists()
    with pytest.raises(ValueError, match="not canonical"):
        run_formal_nofault._validated_formal_clone_report_bytes_v02321(
            root=tmp_path,
            admission=admission,
        )


def test_ambiguous_incident_and_diagnosis_ack_recover_exact_persisted_records() -> None:
    incident = _incident()
    job = _diagnosis_job()

    def timeout() -> dict[str, object]:
        raise TimeoutError("ambiguous acknowledgement")

    recovered_incident = run_formal_nofault._request_or_recover_incident_v02321(
        request=timeout,
        recover=lambda: incident,
    )
    recovered_job = run_formal_nofault._request_or_recover_diagnosis_job_v02321(
        request=timeout,
        recover=lambda: job,
    )
    assert recovered_incident == incident
    assert recovered_job == job

    progressed_job = job.model_copy(
        update={
            "status": ProductJobStatusV1.RUNNING,
            "claimed_by": "fixture-worker",
            "lease_expires_at": 10.0,
            "attempt_count": 1,
            "updated_at": 2.0,
        }
    )
    assert (
        run_formal_nofault._request_or_recover_diagnosis_job_v02321(
            request=lambda: job.model_dump(mode="json"),
            recover=lambda: progressed_job,
        )
        == job
    )

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE",
    ):
        run_formal_nofault._request_or_recover_incident_v02321(
            request=lambda: _incident("inc-fedcba987654321001234567").model_dump(
                mode="json"
            ),
            recover=lambda: incident,
        )


def test_formal_traffic_consumption_occurs_before_first_cart_and_is_one_shot() -> None:
    checkpoint = FormalTrafficConsumptionV02321.build(
        admission_sha256=SHA,
        execution_head=HEAD,
        traffic_contract_sha256="c" * 64,
        formal_profile_sha256="d" * 64,
        episode_started_at=datetime.now(UTC),
    )
    assert checkpoint.stage == "CONSUMED_BEFORE_FIRST_CART"
    assert checkpoint.formal_healthy_traffic_execution_count_before == 0
    assert checkpoint.formal_healthy_traffic_execution_count_after == 1

    with pytest.raises(ValueError, match="already consumed"):
        FormalTrafficConsumptionV02321.require_unconsumed(checkpoint)


def test_journal_persists_each_dispatch_before_the_request() -> None:
    events: list[str] = []
    observations: list[CheckoutTransactionObservationV0232] = []
    dispatches: list[FormalTrafficDispatchCheckpointV02321] = []
    observation_checkpoints: list[FormalTrafficObservationCheckpointV02321] = []
    state: dict[str, object] = {}
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=SHA,
        execution_head=HEAD,
        traffic_contract_sha256="c" * 64,
        formal_profile_sha256="d" * 64,
        episode_started_at=datetime.now(UTC),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(f"request:{request.url.path}")
        return _healthy_checkout_handler(request)

    def persist(name: str, _payload: object) -> None:
        events.append(f"persist:{name}")

    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    ) as runner:
        execution = run_formal_nofault.run_formal_traffic_journaled_v02321(
            runner=runner,
            endpoint="http://127.0.0.1:18080/api/checkout",
            profile=load_traffic_profile_v0232(ROOT, role="FORMAL"),
            contract=load_checkout_traffic_contract_v0232(ROOT),
            consumption=consumption,
            dispatch_checkpoints=dispatches,
            observation_checkpoints=observation_checkpoints,
            observations=observations,
            state=state,
            persist=persist,
        )

    assert events[0] == "persist:traffic-dispatch-001.json"
    assert events[1] == "request:/api/cart"
    assert len(observations) == 30
    assert execution.run.successful_transactions == 30
    assert state == {
        "stage": "EXECUTION_RETURNED",
        "pending_dispatch_ordinal": None,
        "remote_delivery": "OBSERVED",
    }


def test_journal_persistence_failure_freezes_unknown_delivery_without_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations: list[CheckoutTransactionObservationV0232] = []
    dispatches: list[FormalTrafficDispatchCheckpointV02321] = []
    observation_checkpoints: list[FormalTrafficObservationCheckpointV02321] = []
    state: dict[str, object] = {}
    requests: list[str] = []
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    freeze = _copy_formal_freeze(tmp_path)
    admission = _admission()
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=admission.admission_sha256,
        execution_head=admission.execution_head,
        traffic_contract_sha256=freeze.traffic_contract_sha256,
        formal_profile_sha256=freeze.formal_profile_sha256,
        episode_started_at=datetime.now(UTC),
    )
    write_private_json(
        private_root / "traffic-consumption.json",
        consumption.model_dump(mode="json"),
        create_once=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _healthy_checkout_handler(request)

    def persist(name: str, payload: object) -> None:
        if name == "traffic-observation-002.json":
            raise OSError("fixture persistence failure")
        write_private_json(
            private_root / "traffic-journal" / name,
            payload,
            create_once=True,
        )

    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    ) as runner:
        with pytest.raises(OSError, match="persistence failure") as caught:
            run_formal_nofault.run_formal_traffic_journaled_v02321(
                runner=runner,
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=load_traffic_profile_v0232(ROOT, role="FORMAL"),
                contract=load_checkout_traffic_contract_v0232(ROOT),
                consumption=consumption,
                dispatch_checkpoints=dispatches,
                observation_checkpoints=observation_checkpoints,
                observations=observations,
                state=state,
                persist=persist,
            )

    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(1),
    )
    source_state = SimpleNamespace(source_sha256=admission.source_state_sha256)
    terminal = run_formal_nofault._seal_formal_failure_v02321(
        root=tmp_path,
        private_root=private_root,
        admission=admission,
        live_error=caught.value,
        stage="FORMAL_TRAFFIC_CONSUMED",
        product_data_root=tmp_path,
        environment_id="env-" + "1" * 24,
        consumption=consumption,
        traffic_result=None,
        execution=None,
        dispatch_checkpoints=dispatches,
        observation_checkpoints=observation_checkpoints,
        traffic_journal_state=state,
        product_cleanup={"verdict": "CLEAN"},
        demo_cleanup=None,
        queue_before_sha256="4" * 64,
        queue_after_sha256="4" * 64,
        outer_baseline_before_sha256="5" * 64,
        outer_baseline_after_sha256="5" * 64,
        source_before=source_state,  # type: ignore[arg-type]
        source_after=None,
    )
    blocker = FormalTrafficBlockerV02321.model_validate_json(
        (private_root / "blocker.json").read_bytes()
    )
    assert requests == [
        "/api/cart",
        "/api/checkout",
        "/api/cart",
        "/api/checkout",
    ]
    assert blocker.completed_transactions == 1
    assert blocker.pending_dispatch_ordinal == 2
    assert blocker.remote_delivery == "UNKNOWN"
    assert blocker.accepted_successor_incident_count == 0
    assert blocker.successor_diagnosis_count == 0
    assert terminal == "BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC"
    assert not (private_root / "incident.json").exists()
    assert not (private_root / "diagnosis-job.json").exists()


def test_reentry_seals_consumed_traffic_without_rerunning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    admission = _admission()
    _write_reservation(tmp_path, admission)
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=admission.admission_sha256,
        execution_head=admission.execution_head,
        traffic_contract_sha256="c" * 64,
        formal_profile_sha256="d" * 64,
        episode_started_at=datetime.now(UTC),
    )
    write_private_json(
        private_root / "admission.json",
        admission.model_dump(mode="json"),
        create_once=True,
    )
    write_private_json(
        private_root / "traffic-consumption.json",
        consumption.model_dump(mode="json"),
        create_once=True,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(1),
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_verify_admission_after_reservation",
        lambda _root, observed, **_kwargs: (
            SimpleNamespace() if observed == admission else pytest.fail()
        ),
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_load_bound_consumption_v02321",
        lambda **_kwargs: consumption,
    )

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC",
    ):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )

    blocker = json.loads((private_root / "blocker.json").read_bytes())
    assert blocker["stage"] == "CONSUMED_BEFORE_FIRST_CART"
    assert blocker["formal_healthy_traffic_execution_count"] == 1
    assert blocker["accepted_successor_incident_count"] == 0
    assert blocker["successor_diagnosis_count"] == 0
    assert blocker["closure"]["verdict"] == "BLOCKED"
    assert blocker["closure"]["source_state_status"] == "UNPROVEN"
    assert blocker["closure"]["product_cleanup"]["observation_complete"] is False
    assert blocker["closure"]["demo_cleanup"]["observation_complete"] is False
    assert not (private_root / "formal-traffic-blocker.json").exists()


def test_reentry_ignores_orphan_live_closure_and_seals_one_unproven_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, private_root, _consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    orphan = FormalBlockerClosureV02321.build(
        product_cleanup={
            "observation_complete": False,
            "verdict": "BLOCKED",
            "safe_error_code": "ORPHAN_PRODUCT_CLEANUP",
        },
        demo_cleanup={
            "observation_complete": False,
            "verdict": "BLOCKED",
            "safe_error_code": "ORPHAN_DEMO_CLEANUP",
        },
        evidence_origin="LIVE_OBSERVATION",
        queue_state_status="OBSERVED",
        queue_before_sha256="4" * 64,
        queue_after_sha256="4" * 64,
        outer_baseline_state_status="OBSERVED",
        outer_baseline_before_sha256="5" * 64,
        outer_baseline_after_sha256="5" * 64,
        source_state_status="UNPROVEN",
        source_state_before_sha256=admission.source_state_sha256,
        source_state_after_sha256=None,
    )
    write_private_json(
        private_root / "blocker-closure-evidence.json",
        orphan.model_dump(mode="json"),
        create_once=True,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(1),
    )

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC",
    ):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )

    blocker = FormalTrafficBlockerV02321.model_validate_json(
        (private_root / "blocker.json").read_bytes()
    )
    assert blocker.closure.evidence_origin == "RECOVERY_UNPROVEN"
    assert blocker.closure.verdict == "BLOCKED"
    assert not (private_root / "formal-traffic-blocker.json").exists()


@pytest.mark.parametrize("checkpoint", ("reservation", "admission"))
def test_reserved_recovery_rejects_noncanonical_admission_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: str,
) -> None:
    _admission_value, private_root, _consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    path = (
        tmp_path / ".local/product-v02321/formal-reservation.json"
        if checkpoint == "reservation"
        else private_root / "admission.json"
    )
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(ValueError, match="reservation|admission"):
        run_formal_nofault._reserved_admission_for_private_recovery_v02321(
            root=tmp_path,
            private_root=private_root,
        )


def test_reentry_does_not_count_a_symlinked_consumption_as_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    admission = _admission()
    _write_reservation(tmp_path, admission)
    write_private_json(
        private_root / "admission.json",
        admission.model_dump(mode="json"),
        create_once=True,
    )
    outside = tmp_path / "outside-consumption.json"
    outside.write_text("{}\n", encoding="utf-8")
    (private_root / "traffic-consumption.json").symlink_to(outside)
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(1),
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_verify_admission_after_reservation",
        lambda _root, observed, **_kwargs: (
            SimpleNamespace() if observed == admission else pytest.fail()
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE",
    ):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )

    blocker = FormalExecutionBlockerV02321.model_validate_json(
        (private_root / "blocker.json").read_bytes()
    )
    assert blocker.formal_healthy_traffic_execution_count == 0
    assert blocker.safe_error_code == "INVALID_DURABLE_TRAFFIC_CONSUMPTION"

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE",
    ):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )


def test_nonpublication_reentry_requires_the_exact_durable_reservation(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    admission = _admission()
    write_private_json(
        private_root / "admission.json",
        admission.model_dump(mode="json"),
        create_once=True,
    )

    with pytest.raises(ValueError, match="formal reservation"):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )

    _write_reservation(
        tmp_path,
        _admission(source_state_sha256="e" * 64),
    )
    with pytest.raises(ValueError, match="recovery admission differs"):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )


def test_real_git_reentry_seals_and_replays_with_the_exact_clone_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, _report_bytes = _prepare_temp_recoverable_formal_clone(
        tmp_path,
        monkeypatch,
    )
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    write_private_json(
        private_root / "admission.json",
        admission.model_dump(mode="json"),
        create_once=True,
    )
    freeze = FormalContractFreezeV02321.model_validate_json(
        (
            tmp_path / "docs/analysis/product-v02321-formal-contract-freeze.json"
        ).read_bytes()
    )
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=admission.admission_sha256,
        execution_head=admission.execution_head,
        traffic_contract_sha256=freeze.traffic_contract_sha256,
        formal_profile_sha256=freeze.formal_profile_sha256,
        episode_started_at=datetime.now(UTC),
    )
    write_private_json(
        private_root / "traffic-consumption.json",
        consumption.model_dump(mode="json"),
        create_once=True,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(1),
    )

    class CrashBeforePublicBlocker(RuntimeError):
        pass

    original_write_public_once = run_formal_nofault._write_public_once

    def crash_before_public_blocker(
        path: Path,
        payload: dict[str, object],
    ) -> None:
        if path.name == "product-v02321-formal-blocker.json":
            raise CrashBeforePublicBlocker("private blocker persisted")
        original_write_public_once(path, payload)

    monkeypatch.setattr(
        run_formal_nofault,
        "_write_public_once",
        crash_before_public_blocker,
    )
    with pytest.raises(CrashBeforePublicBlocker, match="private blocker persisted"):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )
    assert (private_root / "blocker.json").is_file()
    assert not (tmp_path / "docs/analysis/product-v02321-formal-blocker.json").exists()

    monkeypatch.setattr(
        run_formal_nofault,
        "_write_public_once",
        original_write_public_once,
    )
    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC",
    ):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )
    assert (tmp_path / "docs/analysis/product-v02321-formal-blocker.json").is_file()

    with pytest.raises(
        RuntimeError,
        match="BLOCKED_ECOMSRE_PRODUCT_V02321_FORMAL_HEALTHY_TRAFFIC",
    ):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )


@pytest.mark.parametrize(
    "forged_field",
    ("execution_head", "admission_sha256", "consumption_sha256"),
)
def test_blocker_replay_rejects_forged_admission_and_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forged_field: str,
) -> None:
    admission, private_root, consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    closure = _unproven_closure(admission)
    values = {
        "execution_head": admission.execution_head,
        "admission_sha256": admission.admission_sha256,
        "consumption_sha256": consumption.consumption_sha256,
    }
    values[forged_field] = "0" * (40 if forged_field == "execution_head" else 64)
    blocker = FormalTrafficBlockerV02321.build(
        **values,
        stage="CONSUMED_BEFORE_FIRST_CART",
        traffic_execution=None,
        dispatch_checkpoints=(),
        observation_checkpoints=(),
        pending_dispatch_ordinal=None,
        remote_delivery="NOT_STARTED",
        safe_error_code="FORGED_FIXTURE",
        closure=closure,
    )
    blocker_bytes = canonical_json_bytes(blocker.model_dump(mode="json"))
    with pytest.raises(ValueError, match="blocker binding"):
        run_formal_nofault._validate_existing_blocker_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
            blocker_bytes=blocker_bytes,
        )


def test_blocker_replay_rejects_count_and_journal_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, private_root, consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    closure = _unproven_closure(admission)
    count_drift = FormalExecutionBlockerV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        stage="PROCESS_INTERRUPTED_AFTER_FORMAL_START",
        safe_error_code="FORGED_COUNT",
        formal_healthy_traffic_execution_count=0,
        observed_state_status="UNAVAILABLE",
        observed_incident_count=None,
        observed_diagnosis_count=None,
        observed_diagnosis_job_count=None,
        accepted_successor_incident_count=None,
        successor_diagnosis_count=None,
        closure=closure,
    )
    with pytest.raises(ValueError, match="blocker binding"):
        run_formal_nofault._validate_existing_blocker_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
            blocker_bytes=canonical_json_bytes(count_drift.model_dump(mode="json")),
        )

    traffic_blocker = FormalTrafficBlockerV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        consumption_sha256=consumption.consumption_sha256,
        stage="CONSUMED_BEFORE_FIRST_CART",
        traffic_execution=None,
        dispatch_checkpoints=(),
        observation_checkpoints=(),
        pending_dispatch_ordinal=None,
        remote_delivery="NOT_STARTED",
        safe_error_code="JOURNAL_DRIFT_FIXTURE",
        closure=closure,
    )
    dispatch = FormalTrafficDispatchCheckpointV02321.build(
        consumption_sha256=consumption.consumption_sha256,
        ordinal=1,
        cart_payload_sha256="1" * 64,
        checkout_payload_sha256="2" * 64,
    )
    write_private_json(
        private_root / "traffic-journal/traffic-dispatch-001.json",
        dispatch.model_dump(mode="json"),
        create_once=True,
    )
    with pytest.raises(ValueError, match="blocker binding"):
        run_formal_nofault._validate_existing_blocker_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
            blocker_bytes=canonical_json_bytes(traffic_blocker.model_dump(mode="json")),
        )


def test_replay_rejects_traffic_terminal_after_successor_cardinality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, private_root, consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(2),
    )
    blocker = FormalTrafficBlockerV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        consumption_sha256=consumption.consumption_sha256,
        stage="CONSUMED_BEFORE_FIRST_CART",
        traffic_execution=None,
        dispatch_checkpoints=(),
        observation_checkpoints=(),
        pending_dispatch_ordinal=None,
        remote_delivery="NOT_STARTED",
        safe_error_code="FORGED_TRAFFIC_TERMINAL",
        closure=_unproven_closure(admission),
    )

    with pytest.raises(ValueError, match="blocker binding"):
        run_formal_nofault._validate_existing_blocker_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
            blocker_bytes=canonical_json_bytes(blocker.model_dump(mode="json")),
        )


@pytest.mark.parametrize(
    "stage",
    (
        "PROCESS_INTERRUPTED_AFTER_FORMAL_START",
        "PROCESS_INTERRUPTED_AFTER_FORMAL_TRAFFIC_PASS",
    ),
)
def test_replay_rejects_infrastructure_terminal_that_replaces_traffic_or_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    admission, private_root, _consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(1),
    )
    blocker = FormalExecutionBlockerV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        stage=stage,
        safe_error_code="FORGED_INFRASTRUCTURE_TERMINAL",
        formal_healthy_traffic_execution_count=1,
        observed_state_status="OBSERVED",
        observed_incident_count=1,
        observed_diagnosis_count=1,
        observed_diagnosis_job_count=1,
        observed_fault_family_count=0,
        observed_knowledge_artifact_count=0,
        observed_provider_calls=0,
        observed_agent_writes=0,
        observed_runbook_executions=0,
        accepted_successor_incident_count=0,
        successor_diagnosis_count=0,
        action_authority="NONE",
        closure=_unproven_closure(admission),
    )

    with pytest.raises(ValueError, match="blocker binding"):
        run_formal_nofault._validate_existing_blocker_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
            blocker_bytes=canonical_json_bytes(blocker.model_dump(mode="json")),
        )


def test_replay_rejects_unavailable_observation_when_state_is_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, private_root, _consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(2),
    )
    blocker = FormalExecutionBlockerV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        stage="PROCESS_INTERRUPTED_AFTER_FORMAL_START",
        safe_error_code="FORGED_UNAVAILABLE",
        formal_healthy_traffic_execution_count=1,
        observed_state_status="UNAVAILABLE",
        observed_incident_count=None,
        observed_diagnosis_count=None,
        observed_diagnosis_job_count=None,
        accepted_successor_incident_count=None,
        successor_diagnosis_count=None,
        closure=_unproven_closure(admission),
    )

    with pytest.raises(ValueError, match="blocker binding"):
        run_formal_nofault._validate_existing_blocker_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
            blocker_bytes=canonical_json_bytes(blocker.model_dump(mode="json")),
        )


def test_recovery_rejects_misbound_formal_traffic_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, private_root, consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    execution = _successful_formal_execution()
    _write_full_traffic_journal(
        private_root=private_root,
        consumption=consumption,
        execution=execution,
    )
    write_private_json(
        private_root / "traffic-execution.json",
        execution.model_dump(mode="json"),
        create_once=True,
    )
    started = execution.run.started_at - timedelta(seconds=1)
    result = FormalTrafficResultV02321.build(
        admission_sha256="0" * 64,
        consumption_sha256=consumption.consumption_sha256,
        execution=execution,
        episode_started_at=started,
        episode_ended_at=started + timedelta(seconds=300),
        monotonic_duration_ms=300_000,
    )
    write_private_json(
        private_root / "formal-traffic.json",
        result.model_dump(mode="json"),
        create_once=True,
    )

    with pytest.raises(ValueError, match="formal traffic result binding"):
        run_formal_nofault._observe_authoritative_recovery_state_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
        )


def test_replay_rejects_forged_clean_cleanup_with_exact_source_poststate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission, private_root, _consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(2),
    )
    source = PreflightStateCloneReportV02321.model_validate_json(
        (
            tmp_path / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_bytes()
    ).source_state
    assert source.source_sha256 == admission.source_state_sha256
    write_private_json(
        private_root / "source-poststate.json",
        source.model_dump(mode="json"),
        create_once=True,
    )
    observed_closure = FormalBlockerClosureV02321.build(
        product_cleanup={
            "observation_complete": False,
            "verdict": "BLOCKED",
            "safe_error_code": "PRODUCT_CLEANUP_UNPROVEN",
        },
        demo_cleanup={
            "observation_complete": False,
            "verdict": "BLOCKED",
            "safe_error_code": "DEMO_CLEANUP_UNPROVEN",
        },
        evidence_origin="LIVE_OBSERVATION",
        queue_state_status="OBSERVED",
        queue_before_sha256="4" * 64,
        queue_after_sha256="4" * 64,
        outer_baseline_state_status="OBSERVED",
        outer_baseline_before_sha256="5" * 64,
        outer_baseline_after_sha256="5" * 64,
        source_state_status="UNCHANGED",
        source_state_before_sha256=admission.source_state_sha256,
        source_state_after_sha256=admission.source_state_sha256,
    )
    write_private_json(
        private_root / "blocker-closure-evidence.json",
        observed_closure.model_dump(mode="json"),
        create_once=True,
    )
    forged_closure = FormalBlockerClosureV02321.build(
        product_cleanup={
            "observation_complete": True,
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "database_owner_count_before": 0,
            "database_owner_count_after": 0,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
            "safe_error_code": None,
        },
        demo_cleanup={
            "observation_complete": True,
            "verdict": "CLEAN",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "safe_error_code": None,
        },
        evidence_origin="LIVE_OBSERVATION",
        queue_state_status="OBSERVED",
        queue_before_sha256="4" * 64,
        queue_after_sha256="4" * 64,
        outer_baseline_state_status="OBSERVED",
        outer_baseline_before_sha256="5" * 64,
        outer_baseline_after_sha256="5" * 64,
        source_state_status="UNCHANGED",
        source_state_before_sha256=admission.source_state_sha256,
        source_state_after_sha256=admission.source_state_sha256,
    )
    blocker = FormalExecutionBlockerV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        stage="PROCESS_INTERRUPTED_AFTER_FORMAL_START",
        safe_error_code="FORGED_CLEAN_CLOSURE",
        formal_healthy_traffic_execution_count=1,
        observed_state_status="OBSERVED",
        observed_incident_count=2,
        observed_diagnosis_count=2,
        observed_diagnosis_job_count=2,
        observed_fault_family_count=0,
        observed_knowledge_artifact_count=0,
        observed_provider_calls=0,
        observed_agent_writes=0,
        observed_runbook_executions=0,
        accepted_successor_incident_count=1,
        successor_diagnosis_count=1,
        action_authority="NONE",
        closure=forged_closure.model_dump(mode="json"),
    )

    with pytest.raises(ValueError, match="blocker closure"):
        run_formal_nofault._validate_existing_blocker_v02321(
            root=tmp_path,
            private_root=private_root,
            admission=admission,
            blocker_bytes=canonical_json_bytes(blocker.model_dump(mode="json")),
        )


@pytest.mark.parametrize("drift", ("queue", "baseline"))
def test_typed_clean_closure_rejects_queue_or_baseline_drift(drift: str) -> None:
    closure = FormalBlockerClosureV02321.build(
        product_cleanup={
            "observation_complete": True,
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "database_owner_count_before": 0,
            "database_owner_count_after": 0,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
            "safe_error_code": None,
        },
        demo_cleanup={
            "observation_complete": True,
            "verdict": "CLEAN",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "safe_error_code": None,
        },
        evidence_origin="LIVE_OBSERVATION",
        queue_state_status="OBSERVED",
        queue_before_sha256="4" * 64,
        queue_after_sha256=("6" * 64 if drift == "queue" else "4" * 64),
        outer_baseline_state_status="OBSERVED",
        outer_baseline_before_sha256="5" * 64,
        outer_baseline_after_sha256=("7" * 64 if drift == "baseline" else "5" * 64),
        source_state_status="UNCHANGED",
        source_state_before_sha256="8" * 64,
        source_state_after_sha256="8" * 64,
    )

    assert closure.verdict == "BLOCKED"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("fault_family_count", 1),
        ("knowledge_artifact_count", 1),
        ("provider_calls", 1),
        ("agent_writes", 1),
        ("runbook_executions", 1),
        ("action_authority", "NON_NONE"),
    ),
)
def test_recovery_authority_drift_forces_exact_infrastructure_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: int | str,
) -> None:
    admission, private_root, _consumption = _prepare_consumed_recovery_fixture(
        tmp_path,
        monkeypatch,
    )
    observed_state = _observed_state(1)
    observed_state[field] = value
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: observed_state,
    )

    state = run_formal_nofault._observe_authoritative_recovery_state_v02321(
        root=tmp_path,
        private_root=private_root,
        admission=admission,
    )
    blocker = run_formal_nofault._blocker_from_recovery_state_v02321(
        state=state,
        admission=admission,
        closure=run_formal_nofault._unproven_blocker_closure_v02321(admission),
        safe_error_code="AUTHORITY_DRIFT",
    )

    assert isinstance(blocker, FormalExecutionBlockerV02321)
    assert state.terminal_kind == "INFRASTRUCTURE"
    observed_field = (
        "action_authority" if field == "action_authority" else f"observed_{field}"
    )
    assert getattr(blocker, observed_field) == value


def test_hermetic_clone_observation_binds_zero_authority_counters(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "product"
    data_root.mkdir()
    store = SqliteStoreV1(data_root / "product.sqlite3")
    diagnosis, _bundle, _index, _trace = _fixture()
    environment_id = "env-" + "1" * 24
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO environments(environment_id, name, description, timezone, "
            "service_identity_policy_json, explicit_service_catalog_json, created_at, "
            "updated_at) VALUES (?, 'fixture', '', 'UTC', '{}', '[]', ?, ?)",
            (
                environment_id,
                "2026-08-30T00:00:00+00:00",
                "2026-08-30T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO incidents(incident_id, environment_id, external_incident_key, "
            "payload_json, created_at) VALUES (?, ?, 'fixture', '{}', ?)",
            (
                diagnosis.incident_id,
                environment_id,
                "2026-08-30T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO diagnosis_results(diagnosis_id, incident_id, payload_json, "
            "created_at) VALUES (?, ?, ?, ?)",
            (
                diagnosis.diagnosis_id,
                diagnosis.incident_id,
                canonical_json_bytes(diagnosis.model_dump(mode="json")).decode(),
                "2026-08-30T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO diagnosis_jobs(job_id, job_type, status, payload_json, "
            "result_json, safe_error_code, idempotency_key, claimed_by, "
            "lease_expires_at, attempt_count, created_at, updated_at) "
            "VALUES ('job-fixture', 'DIAGNOSIS', 'SUCCEEDED', '{}', '{}', NULL, "
            "'diagnosis:fixture', NULL, NULL, 1, 1.0, 1.0)"
        )
        connection.execute("COMMIT")

    observed = run_formal_nofault._observe_formal_cardinality(
        data_root,
        environment_id=environment_id,
    )

    assert observed is not None
    assert observed["fault_family_count"] == 0
    assert observed["knowledge_artifact_count"] == 0
    assert observed["provider_calls"] == 0
    assert observed["agent_writes"] == 0
    assert observed["runbook_executions"] == 0
    assert observed["action_authority"] == "NONE"


@pytest.mark.parametrize(
    ("checkpoint", "kind"),
    (
        ("publication-intent.json", "symlink"),
        ("publication-intent.json", "directory"),
        ("publication-bundle.json", "symlink"),
        ("publication-bundle.json", "directory"),
    ),
)
def test_reentry_rejects_nonregular_publication_checkpoints(
    tmp_path: Path,
    checkpoint: str,
    kind: str,
) -> None:
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    target = private_root / checkpoint
    if kind == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        target.symlink_to(outside)
    else:
        target.mkdir()

    with pytest.raises(ValueError, match="publication (intent|bundle)"):
        run_formal_nofault._recover_interrupted_private_run_v02321(
            root=tmp_path,
            private_root=private_root,
        )


def test_runtime_failure_after_durable_traffic_pass_is_infrastructure_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admission = _admission()
    freeze = _copy_formal_freeze(tmp_path)
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=admission.admission_sha256,
        execution_head=admission.execution_head,
        traffic_contract_sha256=freeze.traffic_contract_sha256,
        formal_profile_sha256=freeze.formal_profile_sha256,
        episode_started_at=datetime.now(UTC),
    )
    execution = _successful_formal_execution()
    started = execution.run.started_at - timedelta(seconds=1)
    traffic = FormalTrafficResultV02321.build(
        admission_sha256=admission.admission_sha256,
        consumption_sha256=consumption.consumption_sha256,
        execution=execution,
        episode_started_at=started,
        episode_ended_at=started + timedelta(seconds=300),
        monotonic_duration_ms=300_000,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_observe_formal_cardinality",
        lambda *_args, **_kwargs: _observed_state(1),
    )
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    write_private_json(
        private_root / "traffic-consumption.json",
        consumption.model_dump(mode="json"),
        create_once=True,
    )
    _write_full_traffic_journal(
        private_root=private_root,
        consumption=consumption,
        execution=execution,
    )
    write_private_json(
        private_root / "traffic-execution.json",
        execution.model_dump(mode="json"),
        create_once=True,
    )
    write_private_json(
        private_root / "formal-traffic.json",
        traffic.model_dump(mode="json"),
        create_once=True,
    )
    source_state = SimpleNamespace(source_sha256=admission.source_state_sha256)
    terminal = run_formal_nofault._seal_formal_failure_v02321(
        root=tmp_path,
        private_root=private_root,
        admission=admission,
        live_error=RuntimeError("fresh Runtime snapshot failed"),
        stage="FORMAL_TRAFFIC_PASS",
        product_data_root=tmp_path,
        environment_id="env-" + "1" * 24,
        consumption=consumption,
        traffic_result=traffic,
        execution=execution,
        dispatch_checkpoints=(),
        observation_checkpoints=(),
        traffic_journal_state={
            "stage": "EXECUTION_RETURNED",
            "pending_dispatch_ordinal": None,
            "remote_delivery": "OBSERVED",
        },
        product_cleanup={"verdict": "CLEAN"},
        demo_cleanup=None,
        queue_before_sha256="4" * 64,
        queue_after_sha256="4" * 64,
        outer_baseline_before_sha256="5" * 64,
        outer_baseline_after_sha256="5" * 64,
        source_before=source_state,  # type: ignore[arg-type]
        source_after=None,
    )
    blocker = FormalExecutionBlockerV02321.model_validate_json(
        (private_root / "blocker.json").read_bytes()
    )
    assert terminal == "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
    assert blocker.formal_healthy_traffic_execution_count == 1
    assert blocker.stage == "PROCESS_INTERRUPTED_AFTER_FORMAL_TRAFFIC_PASS"
    assert blocker.accepted_successor_incident_count == 0
    assert not (private_root / "formal-traffic-blocker.json").exists()


def test_reentry_rejects_a_misbound_or_non_regular_traffic_journal(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / ".local/product-v02321/formal"
    journal = private_root / "traffic-journal"
    journal.mkdir(parents=True)
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=SHA,
        execution_head=HEAD,
        traffic_contract_sha256="c" * 64,
        formal_profile_sha256="d" * 64,
        episode_started_at=datetime.now(UTC),
    )
    dispatch = FormalTrafficDispatchCheckpointV02321.build(
        consumption_sha256=consumption.consumption_sha256,
        ordinal=1,
        cart_payload_sha256="1" * 64,
        checkout_payload_sha256="2" * 64,
    )
    different_dispatch = FormalTrafficDispatchCheckpointV02321.build(
        consumption_sha256=consumption.consumption_sha256,
        ordinal=1,
        cart_payload_sha256="3" * 64,
        checkout_payload_sha256="2" * 64,
    )
    observation = _successful_formal_execution().observations[0]
    observation_checkpoint = FormalTrafficObservationCheckpointV02321.build(
        consumption_sha256=consumption.consumption_sha256,
        dispatch_checkpoint_sha256=different_dispatch.checkpoint_sha256,
        observation=observation,
    )
    write_private_json(
        journal / "traffic-dispatch-001.json",
        dispatch.model_dump(mode="json"),
        create_once=True,
    )
    write_private_json(
        journal / "traffic-observation-001.json",
        observation_checkpoint.model_dump(mode="json"),
        create_once=True,
    )

    with pytest.raises(ValueError, match="journal chain"):
        run_formal_nofault._load_traffic_journal_v02321(
            private_root=private_root,
            consumption=consumption,
        )

    (journal / "unexpected.json").symlink_to(journal / "traffic-dispatch-001.json")
    with pytest.raises(ValueError, match="symlink"):
        run_formal_nofault._load_traffic_journal_v02321(
            private_root=private_root,
            consumption=consumption,
        )


def test_publication_intent_preflights_cross_binding_and_recovers_cas_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_root = tmp_path / ".local/product-v02321/formal"
    private_root.mkdir(parents=True)
    private_files = {
        path: canonical_json_bytes({"private_path": path})
        for path in run_formal_nofault._PRIVATE_PUBLICATION_FILES
    }
    for path, payload in private_files.items():
        if path != "acceptance.json":
            target = private_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    public_files = {
        path: canonical_json_bytes({"public_path": path})
        for path in run_formal_nofault._PUBLICATION_OUTPUTS
    }
    progress_path = tmp_path / "docs/analysis/product-v02321-progress.json"
    progress_path.parent.mkdir(parents=True)
    (tmp_path / "docs/results").mkdir(parents=True)
    descriptor_path = (
        tmp_path / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
    )
    descriptor_path.write_text("{}\n", encoding="utf-8")
    old_progress = canonical_json_bytes({"progress": "before"})
    progress_path.write_bytes(old_progress)
    run_formal_nofault._freeze_publication_bundle(
        project_root=tmp_path,
        private_root=private_root,
        execution_head=HEAD,
        private_files=private_files,
        public_files=public_files,
    )

    sentinel = SimpleNamespace(result_sha256="sentinel")
    admission_sentinel = SimpleNamespace(execution_head=HEAD)
    (private_root.parent / "formal-reservation.json").write_text(
        "{}\n", encoding="utf-8"
    )
    cross_binding_calls = 0
    reject_cross_binding = True

    def validate_payload(_path: str, payload: bytes) -> dict[str, object]:
        parsed = json.loads(payload)
        assert isinstance(parsed, dict)
        return parsed

    def cross_bind(**_kwargs: object) -> object:
        nonlocal cross_binding_calls
        cross_binding_calls += 1
        if reject_cross_binding:
            raise ValueError("fixture cross-binding differs")
        return sentinel

    monkeypatch.setattr(
        run_formal_nofault,
        "_validate_publication_payload",
        validate_payload,
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_require_publication_cross_bindings_v02321",
        cross_bind,
    )
    monkeypatch.setattr(
        run_formal_nofault.NoFaultAcceptanceResultV02321,
        "model_validate_json",
        classmethod(lambda _cls, _payload: sentinel),
    )
    monkeypatch.setattr(
        run_formal_nofault.FormalCloneReservationV02321,
        "model_validate",
        classmethod(
            lambda _cls, _payload: SimpleNamespace(admission=admission_sentinel)
        ),
    )
    monkeypatch.setattr(
        run_formal_nofault.FormalExecutionAdmissionV02321,
        "model_validate",
        classmethod(lambda _cls, _payload: admission_sentinel),
    )
    monkeypatch.setattr(
        run_formal_nofault,
        "_verify_admission_after_reservation",
        lambda _root, _admission, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        run_formal_nofault.RuntimeAuthorityContinuityDescriptorV0231,
        "model_validate",
        classmethod(lambda _cls, _payload: SimpleNamespace(descriptor_sha256="f" * 64)),
    )
    monkeypatch.setattr(
        run_formal_nofault.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=f"{HEAD}\n"),
    )

    progress_path.write_bytes(canonical_json_bytes({"progress": "tampered"}))
    with pytest.raises(ValueError, match="compare-and-swap"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    assert not (private_root / "acceptance.json").exists()
    assert not (
        tmp_path / "docs/analysis/product-v02321-baseline-restart.json"
    ).exists()

    progress_path.write_bytes(old_progress)
    with pytest.raises(ValueError, match="cross-binding"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    assert not (private_root / "acceptance.json").exists()
    assert not (
        tmp_path / "docs/analysis/product-v02321-baseline-restart.json"
    ).exists()
    reject_cross_binding = False
    original_replace = run_formal_nofault._replace_public
    crashed = False

    def replace_then_crash(path: Path, payload: object) -> None:
        nonlocal crashed
        original_replace(path, payload)  # type: ignore[arg-type]
        if not crashed:
            crashed = True
            raise OSError("publication crash after progress CAS")

    monkeypatch.setattr(
        run_formal_nofault,
        "_replace_public",
        replace_then_crash,
    )
    with pytest.raises(OSError, match="publication crash"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    assert (
        progress_path.read_bytes()
        == public_files["docs/analysis/product-v02321-progress.json"]
    )

    monkeypatch.setattr(run_formal_nofault, "_replace_public", original_replace)
    recovered = run_formal_nofault.recover_formal_publication_v02321(
        project_root=tmp_path
    )
    assert recovered is sentinel
    assert cross_binding_calls == 3
    assert (
        tmp_path / "docs/analysis/product-v02321-fresh-runtime-snapshot.json"
    ).read_bytes() == public_files[
        "docs/analysis/product-v02321-fresh-runtime-snapshot.json"
    ]
    assert (private_root / "publication-completion.json").is_file()


def test_publication_cross_binding_accepts_one_complete_exact_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_temp_formal_repository(tmp_path)
    preflight = json.loads(
        (
            ROOT / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_bytes()
    )
    source = ProductStateSourceV0232.model_validate(preflight["source_state"])
    destination_locator = (
        ".local/product-v02321/product-state/"
        f"formal-{source.source_sha256[:24]}/product"
    )
    destination_body = {
        **source.model_dump(mode="json", exclude={"source_sha256"}),
        "source_locator": destination_locator,
    }
    destination = ProductStateSourceV0232.model_validate(
        {
            **destination_body,
            "source_sha256": semantic_sha256_v22(destination_body),
        }
    )
    clone = run_state_clone._bind_existing_clone(
        source=source,
        destination=destination,
        destination_locator=destination_locator,
    )
    admission, _freeze, _review = run_formal_nofault._strict_admission(tmp_path)
    assert admission.source_state_sha256 == source.source_sha256
    assert admission.formal_clone_destination_locator == destination_locator
    clone_report = FormalStateCloneReportV02321.build(
        formal_admission_sha256=admission.admission_sha256,
        formal_clone_plan_sha256=admission.formal_clone_plan_sha256,
        source_repository_binding={"binding": "fixture"},
        predecessor_private_acceptance={"acceptance": "fixture"},
        source_state=source.model_dump(mode="json"),
        clone=clone.model_dump(mode="json"),
        destination_state=destination.model_dump(mode="json"),
        destination_locator=destination_locator,
    )
    formal_poststate = FormalProductPoststateV02321.build(
        state_locator=destination_locator,
        database_file_sha256="1" * 64,
        database_logical_sha256="2" * 64,
        object_inventory_sha256="3" * 64,
        runtime_file_inventory_sha256="4" * 64,
        counts={
            **source.source_counts.model_dump(mode="json"),
            "diagnosis_job_count": 2,
            "incident_count": 2,
            "diagnosis_count": 2,
            "evidence_object_count": source.source_counts.evidence_object_count + 3,
        },
        environment_id=source.source_environment_id,
        active_baseline_id=source.source_active_baseline_id,
        active_baseline_sha256=source.source_active_baseline_sha256,
        profile_sha256=source.source_profile_sha256,
    )

    execution = _successful_formal_execution()
    consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=admission.admission_sha256,
        execution_head=admission.execution_head,
        traffic_contract_sha256=execution.run.contract_sha256,
        formal_profile_sha256=execution.run.profile_sha256,
        episode_started_at=execution.run.started_at - timedelta(seconds=1),
    )
    episode_started_at = execution.run.started_at - timedelta(seconds=1)
    episode_ended_at = episode_started_at + timedelta(seconds=300)
    traffic = FormalTrafficResultV02321.build(
        admission_sha256=admission.admission_sha256,
        consumption_sha256=consumption.consumption_sha256,
        execution=execution,
        episode_started_at=episode_started_at,
        episode_ended_at=episode_ended_at,
        monotonic_duration_ms=300_000,
    )

    diagnosis, evidence, index, trace = _fixture()
    assessment = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=evidence,
        index=index,
        decision_trace=trace,
    )
    incident = _incident(
        diagnosis.incident_id,
        started_at=episode_started_at,
        ended_at=episode_ended_at,
        environment_id=source.source_environment_id,
        baseline_id=source.source_active_baseline_id,
        baseline_sha256=source.source_active_baseline_sha256,
    )
    incident_binding = IncidentTrafficBindingV0232.build(
        incident_id=incident.incident_id,
        execution=execution,
        episode_started_at=episode_started_at,
        episode_ended_at=episode_ended_at,
    )
    queued_job = ProductJobRecordV1(
        job_id="job-0123456789abcdef01234567",
        job_type=ProductJobTypeV1.DIAGNOSIS,
        status=ProductJobStatusV1.PENDING,
        payload={"incident_id": incident.incident_id},
        idempotency_key=f"diagnosis:{incident.incident_id}",
        attempt_count=0,
        created_at=1.0,
        updated_at=1.0,
    )
    completed_job = queued_job.model_copy(
        update={
            "status": ProductJobStatusV1.SUCCEEDED,
            "result": diagnosis.model_dump(mode="json"),
            "claimed_by": None,
            "attempt_count": 1,
            "updated_at": 2.0,
        }
    )
    predecessor_session = json.loads(
        (ROOT / "docs/analysis/product-v0231-continuation-session-1.json").read_bytes()
    )
    predecessor_restart = json.loads(
        (ROOT / "docs/analysis/product-v0231-baseline-restart.json").read_bytes()
    )
    runtime_authority = RuntimeAuthorityProofV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        continuity_descriptor_sha256=predecessor_session["runtime_authority_proof"][
            "continuity_descriptor_sha256"
        ],
        inner_proof=predecessor_session["runtime_authority_proof"],
        checkout_state="RUNNING",
        checkout_healthy=True,
        checkout_restart_count=0,
    )
    baseline_restart = BaselineRestartProofV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        active_baseline_id=source.source_active_baseline_id,
        active_baseline_sha256=source.source_active_baseline_sha256,
        active_profile_sha256=source.source_profile_sha256,
        inner_proof=predecessor_restart["proof"],
        new_baseline_count=0,
    )
    connector_binding_sha256 = str(
        runtime_authority.inner_proof.components["connector_binding_sha256"]["observed"]
    )
    snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=source.source_environment_id,
        authority_sha256=connector_binding_sha256,
        observed_at=episode_ended_at + timedelta(seconds=1),
        services={
            "checkout": {
                "state": RuntimeStateV22.RUNNING,
                "healthy": True,
                "restart_count": 0,
            }
        },
    )
    fresh_runtime = FreshRuntimeSnapshotProofV02321.build(
        execution_head=admission.execution_head,
        traffic_result_sha256=traffic.result_sha256,
        snapshot=snapshot,
        runtime_authority_sha256=str(
            runtime_authority.inner_proof.components["pilot_runtime_authority_sha256"][
                "observed"
            ]
        ),
        runtime_continuity_descriptor_sha256=(
            runtime_authority.continuity_descriptor_sha256
        ),
        connector_binding_sha256=snapshot.authority_sha256,
    )
    evidence_window = ConnectorWindowV1(
        started_at=traffic.episode_started_at,
        ended_at=snapshot.observed_at,
    )
    diagnosis, evidence, index, trace = _bound_fixture_evidence(
        environment_id=source.source_environment_id,
        window=evidence_window,
        snapshot=snapshot,
        pilot_runtime_authority_sha256=fresh_runtime.runtime_authority_sha256,
        read_authority_sha256=str(
            runtime_authority.inner_proof.components["read_authority_sha256"][
                "observed"
            ]
        ),
    )
    assessment = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=evidence,
        index=index,
        decision_trace=trace,
    )
    restart_snapshot = baseline_restart.inner_proof.inner_proof.after
    incident = _incident(
        diagnosis.incident_id,
        started_at=episode_started_at,
        ended_at=snapshot.observed_at,
        diagnosis_observed_at=snapshot.observed_at,
        environment_id=source.source_environment_id,
        baseline_id=source.source_active_baseline_id,
        baseline_sha256=source.source_active_baseline_sha256,
        external_incident_key=(
            f"product-v02321-nofault-{admission.admission_sha256[:16]}"
        ),
        service_identity_sha256=restart_snapshot.service_identity_sha256,
        source_capability_sha256=restart_snapshot.capability_sha256,
        created_at=snapshot.observed_at + timedelta(milliseconds=1),
    )
    incident_binding = IncidentTrafficBindingV0232.build(
        incident_id=incident.incident_id,
        execution=execution,
        episode_started_at=episode_started_at,
        episode_ended_at=episode_ended_at,
    )

    acceptance = NoFaultAcceptanceResultV02321.build(
        execution_head=admission.execution_head,
        admission_sha256=admission.admission_sha256,
        formal_clone_report_sha256=clone_report.report_sha256,
        formal_poststate_sha256=formal_poststate.poststate_sha256,
        source_poststate_sha256=source.source_sha256,
        runtime_authority_proof_sha256=runtime_authority.proof_sha256,
        baseline_restart_proof_sha256=baseline_restart.proof_sha256,
        formal_traffic_result_sha256=traffic.result_sha256,
        fresh_runtime_snapshot_proof_sha256=fresh_runtime.proof_sha256,
        incident_traffic_binding_sha256=incident_binding.binding_sha256,
        incident_id=incident.incident_id,
        diagnosis_id=diagnosis.diagnosis_id,
        diagnosis_incident_id=diagnosis.incident_id,
        evidence_incident_id=evidence.incident_id,
        evidence_diagnosis_id=evidence.diagnosis_id,
        index_incident_id=index.incident_id,
        index_diagnosis_id=index.diagnosis_id,
        trace_incident_id=trace.incident_id,
        trace_diagnosis_id=trace.diagnosis_id,
        assessment_incident_id=assessment.incident_id,
        assessment_diagnosis_id=assessment.diagnosis_id,
        diagnosis_result_sha256=diagnosis.result_sha256,
        evidence_bundle_sha256=semantic_sha256_v22(evidence.model_dump(mode="json")),
        evidence_index_sha256=index.index_sha256,
        decision_trace_sha256=trace.trace_sha256,
        assessment_sha256=assessment.result_sha256,
        source_assessment_terminal=assessment.terminal,
        measured_terminal=measured_terminal_v02321(assessment.terminal),
        source_incident_count_after=1,
        source_diagnosis_count_after=1,
        starting_incident_count=1,
        starting_diagnosis_count=1,
        ending_incident_count=2,
        ending_diagnosis_count=2,
        fault_family_count=0,
        knowledge_artifact_count=0,
        fault_attempt_count=0,
        knowledge_loop_campaign_count=0,
        agent_writes=0,
        runbook_executions=0,
        provider_calls=0,
        action_authority="NONE",
        product_cleanup="CLEAN",
        demo_cleanup="CLEAN",
        source_product_state_unchanged=True,
    )
    progress = run_formal_nofault._updated_progress(
        ROOT,
        result=acceptance,
        clone_report=clone_report,
        authority_proof=runtime_authority,
        restart_proof=baseline_restart,
        traffic=traffic,
    )
    private_payloads = {
        "acceptance.json": acceptance.model_dump(mode="json"),
        "admission.json": admission.model_dump(mode="json"),
        "assessment.json": assessment.model_dump(mode="json"),
        "baseline-restart.json": baseline_restart.model_dump(mode="json"),
        "decision-trace.json": trace.model_dump(mode="json"),
        "diagnosis-job-completion.json": completed_job.model_dump(mode="json"),
        "diagnosis-job.json": queued_job.model_dump(mode="json"),
        "diagnosis.json": diagnosis.model_dump(mode="json"),
        "evidence-bundle.json": evidence.model_dump(mode="json"),
        "evidence-index.json": index.model_dump(mode="json"),
        "formal-poststate.json": formal_poststate.model_dump(mode="json"),
        "formal-traffic.json": traffic.model_dump(mode="json"),
        "fresh-runtime-snapshot.json": fresh_runtime.model_dump(mode="json"),
        "incident-traffic-binding.json": incident_binding.model_dump(mode="json"),
        "incident.json": incident.model_dump(mode="json"),
        "runtime-authority.json": runtime_authority.model_dump(mode="json"),
        "source-poststate.json": source.model_dump(mode="json"),
        "traffic-consumption.json": consumption.model_dump(mode="json"),
        "traffic-execution.json": execution.model_dump(mode="json"),
    }
    public_payloads = {
        "docs/analysis/product-v02321-baseline-restart.json": (
            baseline_restart.model_dump(mode="json")
        ),
        "docs/analysis/product-v02321-formal-traffic.json": (
            traffic.model_dump(mode="json")
        ),
        "docs/analysis/product-v02321-fresh-runtime-snapshot.json": (
            fresh_runtime.model_dump(mode="json")
        ),
        "docs/analysis/product-v02321-product-state-clone-formal.json": (
            clone_report.model_dump(mode="json")
        ),
        "docs/analysis/product-v02321-progress.json": progress,
        "docs/analysis/product-v02321-runtime-authority.json": (
            runtime_authority.model_dump(mode="json")
        ),
        "docs/results/product-v02321-nofault-acceptance.json": (
            acceptance.model_dump(mode="json")
        ),
    }

    assert (
        run_formal_nofault._require_publication_cross_bindings_v02321(
            private_payloads=private_payloads,
            public_payloads=public_payloads,
        )
        == acceptance
    )
    base_private_payloads = dict(private_payloads)
    base_public_payloads = dict(public_payloads)

    recovery_private_root = tmp_path / ".local/product-v02321/formal"
    recovery_private_root.mkdir(parents=True)
    reservation = FormalCloneReservationV02321.build(admission=admission)
    write_private_json(
        tmp_path / ".local/product-v02321/formal-reservation.json",
        reservation.model_dump(mode="json"),
        create_once=True,
    )
    private_bytes = {
        path: canonical_json_bytes(payload)
        for path, payload in base_private_payloads.items()
    }
    public_bytes = {
        path: canonical_json_bytes(payload)
        for path, payload in base_public_payloads.items()
    }
    for path, payload in private_bytes.items():
        if path == "acceptance.json":
            continue
        target = recovery_private_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    recovery_progress = tmp_path / "docs/analysis/product-v02321-progress.json"
    recovery_progress.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/results").mkdir(parents=True, exist_ok=True)
    recovery_progress.write_bytes(
        (ROOT / "docs/analysis/product-v02321-progress.json").read_bytes()
    )
    run_formal_nofault._freeze_publication_bundle(
        project_root=tmp_path,
        private_root=recovery_private_root,
        execution_head=admission.execution_head,
        private_files=private_bytes,
        public_files=public_bytes,
    )
    reservation_path = tmp_path / ".local/product-v02321/formal-reservation.json"
    intent_path = recovery_private_root / "publication-intent.json"
    bundle_path = recovery_private_root / "publication-bundle.json"
    for path, label in (
        (reservation_path, "reservation"),
        (bundle_path, "publication bundle"),
    ):
        canonical = path.read_bytes()
        path.write_bytes(canonical + b" ")
        with pytest.raises(ValueError, match=f"{label}.*canonical"):
            run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
        path.write_bytes(canonical)
    canonical_bundle = bundle_path.read_bytes()
    canonical_intent = intent_path.read_bytes()

    intent_path.write_bytes(canonical_intent + b" ")
    with pytest.raises(ValueError, match="publication intent.*canonical"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    intent_path.write_bytes(canonical_intent)

    intent_path.unlink()
    with pytest.raises(ValueError, match="publication intent.*regular"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    intent_path.write_bytes(canonical_intent)

    misbound_intent = json.loads(canonical_intent)
    misbound_intent.pop("intent_sha256")
    misbound_intent["execution_head"] = "0" * 40
    misbound_intent["intent_sha256"] = semantic_sha256_v22(misbound_intent)
    intent_path.write_bytes(canonical_json_bytes(misbound_intent))
    with pytest.raises(ValueError, match="publication intent/bundle differs"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    intent_path.write_bytes(canonical_intent)

    bundle_path.unlink()
    intent_path.write_bytes(canonical_intent + b" ")
    with pytest.raises(ValueError, match="publication intent.*canonical"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    intent_path.write_bytes(canonical_intent)
    run_formal_nofault._freeze_publication_bundle_from_intent(
        private_root=recovery_private_root
    )
    assert bundle_path.read_bytes() == canonical_bundle
    wrong_admission = FormalExecutionAdmissionV02321.build(
        **{
            **admission.model_dump(
                mode="python",
                exclude={
                    "schema_version",
                    "admission_sha256",
                    "formal_runner_file_sha256",
                },
            ),
            "formal_runner_file_sha256": "9" * 64,
        }
    )
    wrong_reservation = FormalCloneReservationV02321.build(admission=wrong_admission)
    reservation_path.write_bytes(
        canonical_json_bytes(wrong_reservation.model_dump(mode="json"))
    )
    with pytest.raises(ValueError, match="reserved admission"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    reservation_path.write_bytes(
        canonical_json_bytes(reservation.model_dump(mode="json"))
    )

    class CrashAfterProgressCas(RuntimeError):
        pass

    original_replace = run_formal_nofault._replace_public

    def crash_after_progress_cas(target: Path, payload: dict[str, object]) -> None:
        original_replace(target, payload)
        if target == recovery_progress:
            raise CrashAfterProgressCas("crash after progress CAS")

    monkeypatch.setattr(
        run_formal_nofault,
        "_replace_public",
        crash_after_progress_cas,
    )
    with pytest.raises(CrashAfterProgressCas, match="after progress CAS"):
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
    assert (
        recovery_progress.read_bytes()
        == public_bytes["docs/analysis/product-v02321-progress.json"]
    )
    assert (recovery_private_root / "acceptance.json").read_bytes() == private_bytes[
        "acceptance.json"
    ]
    assert not (recovery_private_root / "publication-completion.json").exists()

    monkeypatch.setattr(run_formal_nofault, "_replace_public", original_replace)
    assert (
        run_formal_nofault.recover_formal_publication_v02321(project_root=tmp_path)
        == acceptance
    )
    assert (recovery_private_root / "publication-completion.json").is_file()

    def reject_cross_binding(
        *,
        private_updates: dict[str, dict[str, object]] | None = None,
        public_updates: dict[str, dict[str, object]] | None = None,
    ) -> None:
        with pytest.raises(ValueError):
            run_formal_nofault._require_publication_cross_bindings_v02321(
                private_payloads={
                    **base_private_payloads,
                    **(private_updates or {}),
                },
                public_payloads={
                    **base_public_payloads,
                    **(public_updates or {}),
                },
            )

    def rebuild_acceptance(**updates: object) -> NoFaultAcceptanceResultV02321:
        return NoFaultAcceptanceResultV02321.build(
            **{
                **acceptance.model_dump(mode="python", exclude={"result_sha256"}),
                **updates,
            }
        )

    def progress_for(
        candidate: NoFaultAcceptanceResultV02321,
        candidate_traffic: FormalTrafficResultV02321 = traffic,
    ) -> dict[str, object]:
        return run_formal_nofault._updated_progress(
            ROOT,
            result=candidate,
            clone_report=clone_report,
            authority_proof=runtime_authority,
            restart_proof=baseline_restart,
            traffic=candidate_traffic,
        )

    tampered_progress = {
        **progress,
        "source_poststate_sha256": "0" * 64,
    }
    tampered_progress.pop("progress_sha256")
    resealed_tampered_progress = {
        **tampered_progress,
        "progress_sha256": semantic_sha256_v22(tampered_progress),
    }
    reject_cross_binding(
        public_updates={
            "docs/analysis/product-v02321-progress.json": (resealed_tampered_progress)
        }
    )

    terminal_tampered = rebuild_acceptance(
        source_assessment_terminal=NoFaultMeasuredTerminalV0232.NOT_SUPPORTED,
        measured_terminal="ECOMSRE_PRODUCT_V02321_NOFAULT_NOT_SUPPORTED",
    )
    reject_cross_binding(
        private_updates={"acceptance.json": terminal_tampered.model_dump(mode="json")},
        public_updates={
            "docs/results/product-v02321-nofault-acceptance.json": (
                terminal_tampered.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-progress.json": progress_for(
                terminal_tampered
            ),
        },
    )

    coherent_assessment_body = {
        **assessment.model_dump(mode="json", exclude={"result_sha256"}),
        "terminal": NoFaultMeasuredTerminalV0232.NOT_SUPPORTED,
        "reasons": ("COHERENT_TAMPER",),
    }
    coherent_assessment = NoFaultEvidenceAssessmentV0232.model_validate(
        {
            **coherent_assessment_body,
            "result_sha256": semantic_sha256_v22(coherent_assessment_body),
        }
    )
    coherent_acceptance = rebuild_acceptance(
        assessment_sha256=coherent_assessment.result_sha256,
        source_assessment_terminal=coherent_assessment.terminal,
        measured_terminal="ECOMSRE_PRODUCT_V02321_NOFAULT_NOT_SUPPORTED",
    )
    reject_cross_binding(
        private_updates={
            "assessment.json": coherent_assessment.model_dump(mode="json"),
            "acceptance.json": coherent_acceptance.model_dump(mode="json"),
        },
        public_updates={
            "docs/results/product-v02321-nofault-acceptance.json": (
                coherent_acceptance.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-progress.json": progress_for(
                coherent_acceptance
            ),
        },
    )

    alternate_snapshot = PilotRuntimeSnapshotV02.build(
        environment_id="env-" + "9" * 24,
        authority_sha256=snapshot.authority_sha256,
        observed_at=snapshot.observed_at,
        services={
            "checkout": {
                "state": RuntimeStateV22.RUNNING,
                "healthy": True,
                "restart_count": 0,
            }
        },
    )
    _, alternate_evidence, alternate_index, _ = _bound_fixture_evidence(
        environment_id=alternate_snapshot.environment_id,
        window=evidence_window,
        snapshot=alternate_snapshot,
        pilot_runtime_authority_sha256=fresh_runtime.runtime_authority_sha256,
        read_authority_sha256=str(
            runtime_authority.inner_proof.components["read_authority_sha256"][
                "observed"
            ]
        ),
    )
    alternate_assessment = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=alternate_evidence,
        index=alternate_index,
        decision_trace=trace,
    )
    assert alternate_assessment.terminal is NoFaultMeasuredTerminalV0232.FULLY_SUPPORTED
    alternate_acceptance = rebuild_acceptance(
        evidence_bundle_sha256=semantic_sha256_v22(
            alternate_evidence.model_dump(mode="json")
        ),
        evidence_index_sha256=alternate_index.index_sha256,
        assessment_sha256=alternate_assessment.result_sha256,
    )
    reject_cross_binding(
        private_updates={
            "assessment.json": alternate_assessment.model_dump(mode="json"),
            "evidence-bundle.json": alternate_evidence.model_dump(mode="json"),
            "evidence-index.json": alternate_index.model_dump(mode="json"),
            "acceptance.json": alternate_acceptance.model_dump(mode="json"),
        },
        public_updates={
            "docs/results/product-v02321-nofault-acceptance.json": (
                alternate_acceptance.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-progress.json": progress_for(
                alternate_acceptance
            ),
        },
    )

    stale_snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=snapshot.environment_id,
        authority_sha256=snapshot.authority_sha256,
        observed_at=traffic.episode_ended_at - timedelta(seconds=1),
        services={
            item.logical_service: {
                "state": item.state,
                "healthy": item.healthy,
                "restart_count": item.restart_count,
            }
            for item in snapshot.services
        },
    )
    stale_runtime = FreshRuntimeSnapshotProofV02321.build(
        **{
            **fresh_runtime.model_dump(
                mode="python", exclude={"proof_sha256", "snapshot"}
            ),
            "snapshot": stale_snapshot,
        }
    )
    stale_acceptance = rebuild_acceptance(
        fresh_runtime_snapshot_proof_sha256=stale_runtime.proof_sha256
    )
    reject_cross_binding(
        private_updates={
            "acceptance.json": stale_acceptance.model_dump(mode="json"),
            "fresh-runtime-snapshot.json": stale_runtime.model_dump(mode="json"),
        },
        public_updates={
            "docs/results/product-v02321-nofault-acceptance.json": (
                stale_acceptance.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-fresh-runtime-snapshot.json": (
                stale_runtime.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-progress.json": progress_for(
                stale_acceptance
            ),
        },
    )

    authority_misbound = FreshRuntimeSnapshotProofV02321.build(
        **{
            **fresh_runtime.model_dump(mode="python", exclude={"proof_sha256"}),
            "runtime_authority_sha256": "0" * 64,
        }
    )
    authority_misbound_acceptance = rebuild_acceptance(
        fresh_runtime_snapshot_proof_sha256=authority_misbound.proof_sha256
    )
    reject_cross_binding(
        private_updates={
            "acceptance.json": authority_misbound_acceptance.model_dump(mode="json"),
            "fresh-runtime-snapshot.json": authority_misbound.model_dump(mode="json"),
        },
        public_updates={
            "docs/results/product-v02321-nofault-acceptance.json": (
                authority_misbound_acceptance.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-fresh-runtime-snapshot.json": (
                authority_misbound.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-progress.json": progress_for(
                authority_misbound_acceptance
            ),
        },
    )

    incident_misbound = _incident(
        incident.incident_id,
        started_at=traffic.episode_started_at,
        ended_at=traffic.episode_ended_at,
        diagnosis_observed_at=traffic.episode_ended_at,
        environment_id=source.source_environment_id,
        baseline_id=source.source_active_baseline_id,
        baseline_sha256=source.source_active_baseline_sha256,
        external_incident_key=(
            f"product-v02321-nofault-{admission.admission_sha256[:16]}"
        ),
        service_identity_sha256=restart_snapshot.service_identity_sha256,
        source_capability_sha256=restart_snapshot.capability_sha256,
        created_at=snapshot.observed_at + timedelta(milliseconds=1),
    )
    reject_cross_binding(
        private_updates={"incident.json": incident_misbound.model_dump(mode="json")}
    )

    incident_created_too_early = incident.model_copy(
        update={"created_at": snapshot.observed_at - timedelta(milliseconds=1)}
    )
    reject_cross_binding(
        private_updates={
            "incident.json": incident_created_too_early.model_dump(mode="json")
        }
    )

    queued_with_result = queued_job.model_copy(
        update={"result": {"unexpected": "queued result"}}
    )
    reject_cross_binding(
        private_updates={
            "diagnosis-job.json": queued_with_result.model_dump(mode="json")
        }
    )
    queued_with_claim = queued_job.model_copy(
        update={
            "claimed_by": "fixture-worker",
            "lease_expires_at": 10.0,
            "attempt_count": 1,
        }
    )
    reject_cross_binding(
        private_updates={
            "diagnosis-job.json": queued_with_claim.model_dump(mode="json")
        }
    )

    wrong_job_type = completed_job.model_copy(
        update={"job_type": ProductJobTypeV1.BASELINE_BUILD}
    )
    reject_cross_binding(
        private_updates={
            "diagnosis-job-completion.json": wrong_job_type.model_dump(mode="json")
        }
    )
    wrong_job_payload = completed_job.model_copy(
        update={"payload": {"incident_id": "inc-" + "9" * 24}}
    )
    reject_cross_binding(
        private_updates={
            "diagnosis-job-completion.json": wrong_job_payload.model_dump(mode="json")
        }
    )
    completion_created_at_drift = completed_job.model_copy(
        update={"created_at": completed_job.created_at + 1.0}
    )
    reject_cross_binding(
        private_updates={
            "diagnosis-job-completion.json": (
                completion_created_at_drift.model_dump(mode="json")
            )
        }
    )
    completion_with_lease = completed_job.model_copy(
        update={"claimed_by": "fixture-worker", "lease_expires_at": 10.0}
    )
    reject_cross_binding(
        private_updates={
            "diagnosis-job-completion.json": completion_with_lease.model_dump(
                mode="json"
            )
        }
    )

    contract_misbound_consumption = FormalTrafficConsumptionV02321.build(
        admission_sha256=admission.admission_sha256,
        execution_head=admission.execution_head,
        traffic_contract_sha256="0" * 64,
        formal_profile_sha256=execution.run.profile_sha256,
        episode_started_at=episode_started_at,
    )
    contract_misbound_traffic = FormalTrafficResultV02321.build(
        admission_sha256=admission.admission_sha256,
        consumption_sha256=contract_misbound_consumption.consumption_sha256,
        execution=execution,
        episode_started_at=episode_started_at,
        episode_ended_at=episode_ended_at,
        monotonic_duration_ms=300_000,
    )
    contract_misbound_runtime = FreshRuntimeSnapshotProofV02321.build(
        **{
            **fresh_runtime.model_dump(mode="python", exclude={"proof_sha256"}),
            "traffic_result_sha256": contract_misbound_traffic.result_sha256,
        }
    )
    contract_misbound_acceptance = rebuild_acceptance(
        formal_traffic_result_sha256=contract_misbound_traffic.result_sha256,
        fresh_runtime_snapshot_proof_sha256=(contract_misbound_runtime.proof_sha256),
    )
    reject_cross_binding(
        private_updates={
            "acceptance.json": contract_misbound_acceptance.model_dump(mode="json"),
            "traffic-consumption.json": contract_misbound_consumption.model_dump(
                mode="json"
            ),
            "formal-traffic.json": contract_misbound_traffic.model_dump(mode="json"),
            "fresh-runtime-snapshot.json": contract_misbound_runtime.model_dump(
                mode="json"
            ),
        },
        public_updates={
            "docs/results/product-v02321-nofault-acceptance.json": (
                contract_misbound_acceptance.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-formal-traffic.json": (
                contract_misbound_traffic.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-fresh-runtime-snapshot.json": (
                contract_misbound_runtime.model_dump(mode="json")
            ),
            "docs/analysis/product-v02321-progress.json": progress_for(
                contract_misbound_acceptance,
                contract_misbound_traffic,
            ),
        },
    )

    invalid_counter_progress = dict(progress)
    invalid_counter_progress.pop("progress_sha256")
    invalid_counter_progress["accepted_successor_incident_count"] = 0
    invalid_counter_progress["progress_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in invalid_counter_progress.items()
            if key != "progress_sha256"
        }
    )
    with pytest.raises(ValueError, match="accepted_successor_incident_count"):
        run_formal_nofault._validate_publication_payload(
            "docs/analysis/product-v02321-progress.json",
            canonical_json_bytes(invalid_counter_progress),
        )

    extra_claim_progress = dict(progress)
    extra_claim_progress.pop("progress_sha256")
    extra_claim_progress["unreviewed_public_claim"] = "PASS"
    extra_claim_progress["progress_sha256"] = semantic_sha256_v22(extra_claim_progress)
    with pytest.raises(ValueError, match="unreviewed_public_claim"):
        run_formal_nofault._validate_publication_payload(
            "docs/analysis/product-v02321-progress.json",
            canonical_json_bytes(extra_claim_progress),
        )

    for path in ("runtime-authority.json", "baseline-restart.json"):
        minimal = {"execution_head": admission.execution_head}
        minimal["proof_sha256"] = semantic_sha256_v22(minimal)
        with pytest.raises(ValueError):
            run_formal_nofault._validate_publication_payload(
                path,
                canonical_json_bytes(minimal),
            )


def test_formal_traffic_requires_exact_30_of_30_and_300_second_episode() -> None:
    execution = _successful_formal_execution()
    started = execution.run.started_at - timedelta(seconds=1)
    ended = started + timedelta(seconds=300)
    result = FormalTrafficResultV02321.build(
        admission_sha256=SHA,
        consumption_sha256="c" * 64,
        execution=execution,
        episode_started_at=started,
        episode_ended_at=ended,
        monotonic_duration_ms=300_000,
    )
    assert result.terminal == FORMAL_HEALTHY_TRAFFIC_PASS_V02321
    assert result.execution.run.planned_transactions == 30
    assert result.execution.run.successful_transactions == 30
    assert result.hidden_retry_count == 0

    with pytest.raises(ValueError, match="300 seconds"):
        FormalTrafficResultV02321.build(
            admission_sha256=SHA,
            consumption_sha256="c" * 64,
            execution=execution,
            episode_started_at=started,
            episode_ended_at=started + timedelta(seconds=299),
            monotonic_duration_ms=300_000,
        )

    with pytest.raises(ValueError, match="greater than or equal to 300000"):
        FormalTrafficResultV02321.build(
            admission_sha256=SHA,
            consumption_sha256="c" * 64,
            execution=execution,
            episode_started_at=started,
            episode_ended_at=ended,
            monotonic_duration_ms=299_999,
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            NoFaultMeasuredTerminalV0232.FULLY_SUPPORTED,
            "ECOMSRE_PRODUCT_V02321_NOFAULT_FULLY_SUPPORTED",
        ),
        (
            NoFaultMeasuredTerminalV0232.CAPABILITY_LIMITED,
            "ECOMSRE_PRODUCT_V02321_NOFAULT_CAPABILITY_LIMITED",
        ),
        (
            NoFaultMeasuredTerminalV0232.NOT_SUPPORTED,
            "ECOMSRE_PRODUCT_V02321_NOFAULT_NOT_SUPPORTED",
        ),
    ],
)
def test_measured_terminal_mapping_is_frozen(
    source: NoFaultMeasuredTerminalV0232,
    expected: str,
) -> None:
    assert measured_terminal_v02321(source) == expected


def test_formal_product_poststate_requires_exact_successor_cardinality() -> None:
    payload = {
        "state_locator": (
            ".local/product-v02321/product-state/"
            "formal-ffffffffffffffffffffffff/product"
        ),
        "database_file_sha256": "1" * 64,
        "database_logical_sha256": "2" * 64,
        "object_inventory_sha256": "3" * 64,
        "runtime_file_inventory_sha256": "4" * 64,
        "counts": {
            "baseline_count": 1,
            "active_baseline_count": 1,
            "baseline_job_count": 1,
            "verify_job_count": 1,
            "diagnosis_job_count": 2,
            "incident_count": 2,
            "diagnosis_count": 2,
            "evidence_object_count": 2,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "pending_job_count": 0,
            "running_job_count": 0,
            "failed_job_count": 0,
        },
        "environment_id": "env-" + "1" * 24,
        "active_baseline_id": "base-" + "2" * 24,
        "active_baseline_sha256": "5" * 64,
        "profile_sha256": "6" * 64,
    }
    poststate = FormalProductPoststateV02321.build(**payload)
    assert poststate.counts.incident_count == 2
    assert poststate.counts.diagnosis_count == 2

    for count in (1, 3):
        with pytest.raises(ValueError, match="formal poststate counts differ"):
            FormalProductPoststateV02321.build(
                **{
                    **payload,
                    "counts": {
                        **payload["counts"],  # type: ignore[dict-item]
                        "incident_count": count,
                        "diagnosis_count": count,
                    },
                }
            )


def test_acceptance_requires_exact_successor_delta_and_zero_authority() -> None:
    result = NoFaultAcceptanceResultV02321.build(
        execution_head=HEAD,
        admission_sha256=SHA,
        formal_clone_report_sha256="c" * 64,
        formal_poststate_sha256="8" * 64,
        source_poststate_sha256="9" * 64,
        runtime_authority_proof_sha256="d" * 64,
        baseline_restart_proof_sha256="e" * 64,
        formal_traffic_result_sha256="f" * 64,
        fresh_runtime_snapshot_proof_sha256="1" * 64,
        incident_traffic_binding_sha256="7" * 64,
        incident_id="inc-0123456789abcdef01234567",
        diagnosis_id="diag-0123456789abcdef01234567",
        diagnosis_incident_id="inc-0123456789abcdef01234567",
        evidence_incident_id="inc-0123456789abcdef01234567",
        evidence_diagnosis_id="diag-0123456789abcdef01234567",
        index_incident_id="inc-0123456789abcdef01234567",
        index_diagnosis_id="diag-0123456789abcdef01234567",
        trace_incident_id="inc-0123456789abcdef01234567",
        trace_diagnosis_id="diag-0123456789abcdef01234567",
        assessment_incident_id="inc-0123456789abcdef01234567",
        assessment_diagnosis_id="diag-0123456789abcdef01234567",
        diagnosis_result_sha256="2" * 64,
        evidence_bundle_sha256="3" * 64,
        evidence_index_sha256="4" * 64,
        decision_trace_sha256="5" * 64,
        assessment_sha256="6" * 64,
        source_assessment_terminal=(NoFaultMeasuredTerminalV0232.NOT_SUPPORTED),
        measured_terminal="ECOMSRE_PRODUCT_V02321_NOFAULT_NOT_SUPPORTED",
        source_incident_count_after=1,
        source_diagnosis_count_after=1,
        starting_incident_count=1,
        starting_diagnosis_count=1,
        ending_incident_count=2,
        ending_diagnosis_count=2,
        fault_family_count=0,
        knowledge_artifact_count=0,
        fault_attempt_count=0,
        knowledge_loop_campaign_count=0,
        agent_writes=0,
        runbook_executions=0,
        provider_calls=0,
        action_authority="NONE",
        product_cleanup="CLEAN",
        demo_cleanup="CLEAN",
        source_product_state_unchanged=True,
    )
    assert result.terminal == NOFAULT_ACCEPTANCE_COMPLETE_V02321
    assert result.runtime_authority_terminal == RUNTIME_AUTHORITY_CONTINUITY_PASS_V02321
    assert result.baseline_restart_terminal == BASELINE_RESTART_PASS_V02321

    with pytest.raises(ValueError, match="ending_diagnosis_count|delta"):
        NoFaultAcceptanceResultV02321.build(
            **{
                **result.model_dump(mode="python", exclude={"result_sha256"}),
                "ending_diagnosis_count": 3,
            }
        )

    with pytest.raises(ValueError, match="evidence identity"):
        NoFaultAcceptanceResultV02321.build(
            **{
                **result.model_dump(mode="python", exclude={"result_sha256"}),
                "assessment_incident_id": "inc-fedcba987654321001234567",
            }
        )
