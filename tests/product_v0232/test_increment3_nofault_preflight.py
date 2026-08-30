from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.incidents.contracts import EvidenceBundleV1, EvidenceObjectV1
from ecomsre.product.incidents.evidence_binding_v0232 import (
    ConnectorEvidenceBindingV0232,
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import (
    NOFAULT_NOT_SUPPORTED_V0232,
    score_nofault_evidence_v0232,
)
from scripts.product_v0232.run_evidence_binding_preflight import (
    _fixture,
    run_preflight,
)
from scripts.ci.verify_product_v0232_history import (
    verify_product_v0232_written_reports,
)


ROOT = Path(__file__).resolve().parents[2]


def _object_sha256(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _replace_source_payload(
    bundle: EvidenceBundleV1,
    index: DiagnosisEvidenceIndexV0232,
    source: EvidenceSourceV22,
    payload: dict[str, object],
) -> tuple[EvidenceBundleV1, DiagnosisEvidenceIndexV0232]:
    objects = tuple(
        (
            EvidenceObjectV1(
                evidence_ref=item.evidence_ref,
                source=item.source,
                action_id=item.action_id,
                object_sha256=_object_sha256(payload),
                payload=payload,
            )
            if item.source is source
            else item
        )
        for item in bundle.objects
    )
    changed_bundle = bundle.model_copy(update={"objects": objects})
    changed_index = DiagnosisEvidenceIndexV0232.build(
        **{
            **index.model_dump(mode="python", exclude={"index_sha256"}),
            "evidence_bundle_sha256": semantic_sha256_v22(
                changed_bundle.model_dump(mode="json")
            ),
            "all_object_sha256_by_ref": {
                item.evidence_ref: item.object_sha256 for item in objects
            },
        }
    )
    return changed_bundle, changed_index


def test_all_ten_v0232_evidence_binding_preflight_cases_pass() -> None:
    report = run_preflight(ROOT)

    assert report["terminal"] == (
        "ECOMSRE_PRODUCT_V0232_EVIDENCE_BINDING_CONTRACT_PASS"
    )
    assert report["case_count"] == 10
    assert report["passed_case_count"] == 10
    assert [item["case_id"] for item in report["cases"]] == [
        "01_FRESH_RUNTIME_EXPLICIT",
        "02_STALE_RUNTIME",
        "03_ACTIVE_P01_EXPLICIT",
        "04_LOGS_WITHOUT_PROFILE",
        "05_SOURCE_FAILURE_BOUND",
        "06_SOURCE_LIMITATION_UNBOUND",
        "07_ALGORITHMIC_REASON_SEPARATED",
        "08_NO_INCIDENT_COMPLETE",
        "09_INSUFFICIENT_EVIDENCE_BOUND",
        "10_FALSE_OPEN_WORLD_HEALTHY",
    ]
    assert report["predecessor_result_verified"] is True
    assert report["evidence_bundle_v1_compatible"] is True
    assert report["index_deterministic"] is True
    assert report["index_seal_rejects_mutation"] is True
    assert report["index_immutable_persistence"] is True
    assert report["index_deterministic_and_immutable"] is True
    assert report["agent_writes"] == 0
    assert report["runbook_executions"] == 0
    assert report["provider_calls"] == 0
    written = json.loads(
        (ROOT / "docs/analysis/product-v0232-evidence-binding-preflight.json")
        .read_text(encoding="utf-8")
    )
    assert written == report


def test_increment3_progress_and_preflight_are_self_sealed() -> None:
    verified = verify_product_v0232_written_reports(ROOT)

    assert verified["source_clone_count"] == 1


def _write_resealed_preflight_and_progress(
    tmp_path: Path,
    preflight: dict[str, object],
) -> tuple[Path, Path]:
    preflight["preflight_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in preflight.items()
            if key != "preflight_sha256"
        }
    )
    preflight_path = tmp_path / "evidence-preflight.json"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    progress = json.loads(
        (ROOT / "docs/analysis/product-v0232-progress.json").read_text(
            encoding="utf-8"
        )
    )
    progress["evidence_binding_preflight_sha256"] = preflight[
        "preflight_sha256"
    ]
    progress["reference_evidence_index_sha256"] = preflight[
        "reference_evidence_index_sha256"
    ]
    progress["progress_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in progress.items()
            if key != "progress_sha256"
        }
    )
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(progress), encoding="utf-8")
    return preflight_path, progress_path


def test_written_report_verifier_rejects_resealed_case_contract_drift(
    tmp_path: Path,
) -> None:
    preflight = json.loads(
        (
            ROOT / "docs/analysis/product-v0232-evidence-binding-preflight.json"
        ).read_text(encoding="utf-8")
    )
    preflight["cases"][0]["case_id"] = "01_RESEALED_SEMANTIC_DRIFT"
    preflight_path, progress_path = _write_resealed_preflight_and_progress(
        tmp_path,
        preflight,
    )

    with pytest.raises(ValueError, match="Evidence preflight binding differs"):
        verify_product_v0232_written_reports(
            ROOT,
            evidence_preflight_path=preflight_path,
            progress_path=progress_path,
        )


def test_written_report_verifier_scans_preflight_for_local_locator(
    tmp_path: Path,
) -> None:
    preflight = json.loads(
        (
            ROOT / "docs/analysis/product-v0232-evidence-binding-preflight.json"
        ).read_text(encoding="utf-8")
    )
    preflight["debug_locator"] = "/Users/example/private-product-state"
    preflight_path, progress_path = _write_resealed_preflight_and_progress(
        tmp_path,
        preflight,
    )

    with pytest.raises(ValueError, match="public report leaks a local locator"):
        verify_product_v0232_written_reports(
            ROOT,
            evidence_preflight_path=preflight_path,
            progress_path=progress_path,
        )


def test_scorer_rejects_resealed_combined_result_or_malformed_result() -> None:
    diagnosis, bundle, index, trace = _fixture()
    logs = next(
        item for item in bundle.objects if item.source is EvidenceSourceV22.LOGS
    )
    mismatched_payload = deepcopy(logs.payload)
    generic_payload = mismatched_payload["connector_bindings_v0232"][0][
        "connector_binding"
    ]
    generic_payload["combined_result_sha256"] = "f" * 64
    generic_body = {
        key: value for key, value in generic_payload.items() if key != "binding_sha256"
    }
    generic_payload["binding_sha256"] = semantic_sha256_v22(generic_body)
    ConnectorEvidenceBindingV0232.model_validate(generic_payload)
    changed_bundle, changed_index = _replace_source_payload(
        bundle,
        index,
        EvidenceSourceV22.LOGS,
        mismatched_payload,
    )
    mismatched = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=changed_bundle,
        index=changed_index,
        decision_trace=trace,
    )
    assert mismatched.terminal.value == NOFAULT_NOT_SUPPORTED_V0232
    assert "LOGS_PROFILE_BINDING_MISSING" in mismatched.reasons

    malformed_payload = deepcopy(logs.payload)
    malformed_payload["connector_result"].pop("schema_version")
    malformed_bundle, malformed_index = _replace_source_payload(
        bundle,
        index,
        EvidenceSourceV22.LOGS,
        malformed_payload,
    )
    malformed = score_nofault_evidence_v0232(
        diagnosis=diagnosis,
        bundle=malformed_bundle,
        index=malformed_index,
        decision_trace=trace,
    )
    assert malformed.terminal.value == NOFAULT_NOT_SUPPORTED_V0232
    assert "CONNECTOR_RESULT_INVALID" in malformed.reasons


def test_scorer_rejects_resealed_missing_wrong_kind_or_cross_environment_provenance(
) -> None:
    diagnosis, bundle, index, trace = _fixture()
    mutations = (
        (EvidenceSourceV22.METRICS, "REMOVE_BINDINGS", None),
        (EvidenceSourceV22.LOGS, "connector_kind", "PROMETHEUS"),
        (EvidenceSourceV22.RUNTIME, "connector_kind", "OPENSEARCH"),
        (EvidenceSourceV22.METRICS, "connector_kind", "OPENSEARCH"),
        (EvidenceSourceV22.RUNTIME, "environment_id", f"env-{'8' * 24}"),
        (EvidenceSourceV22.METRICS, "environment_id", f"env-{'9' * 24}"),
        (EvidenceSourceV22.LOGS, "environment_id", f"env-{'a' * 24}"),
    )
    for source, field, value in mutations:
        evidence = next(item for item in bundle.objects if item.source is source)
        payload = deepcopy(evidence.payload)
        if field == "REMOVE_BINDINGS":
            payload["connector_bindings_v0232"] = []
        else:
            generic = payload["connector_bindings_v0232"][0][
                "connector_binding"
            ]
            generic[field] = value
            generic["binding_sha256"] = semantic_sha256_v22(
                {
                    key: item
                    for key, item in generic.items()
                    if key != "binding_sha256"
                }
            )
            ConnectorEvidenceBindingV0232.model_validate(generic)
        changed_bundle, changed_index = _replace_source_payload(
            bundle,
            index,
            source,
            payload,
        )
        assessment = score_nofault_evidence_v0232(
            diagnosis=diagnosis,
            bundle=changed_bundle,
            index=changed_index,
            decision_trace=trace,
        )
        assert assessment.terminal.value == NOFAULT_NOT_SUPPORTED_V0232
        assert "CONNECTOR_PROVENANCE_INVALID" in assessment.reasons
