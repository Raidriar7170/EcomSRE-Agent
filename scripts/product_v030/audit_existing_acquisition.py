"""Verify Product v0.3 acquisition leakage without creating an Incident."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.app import create_app
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.incidents.contracts import IncidentRecordV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.pilot.control_gate_v030 import control_record_passes_v030
from ecomsre.product.pilot.runtime_authority_v02 import load_pilot_runtime_authority_v02
from ecomsre.product.settings import ProductSettingsV1
from ecomsre_live_sandbox.contracts import write_private_json


FORBIDDEN = re.compile(
    r"kafkaQueueProblems|paymentFailure|defaultVariant|feature\s*flag|\.flagd\.json|\.local/product-v030|overload simulation",
    re.I,
)
REQUIRED_SOURCES = {"CHANGES", "LOGS", "METRICS", "RESOURCES", "RUNTIME", "TRACES"}
HISTORICAL_ENABLED_FAULT_AUDIT_SHA256 = (
    "95a2680d1fb2af56beeb35164b29822dc1f406e485e95df929ef507f6124370d"
)


def build_current_control_leakage_gate(
    private: Path,
    historical_enabled_fault_audit: Path,
    *,
    expected_historical_sha256: str | None = None,
) -> dict:
    """Bind a new run's complete healthy-control Evidence to its own Baseline."""
    baseline = json.loads((private / "baseline-result.json").read_text())
    if baseline.get("status") != "PRODUCT_V030_FRESH_BASELINE_READY":
        raise ValueError("current fresh Baseline is not ready")
    environment_id = baseline["environment"]["environment_id"]
    baseline_id = baseline["baseline"]["baseline_id"]
    baseline_sha256 = baseline["baseline"]["baseline_sha256"]

    historical_bytes = historical_enabled_fault_audit.read_bytes()
    historical_sha256 = hashlib.sha256(historical_bytes).hexdigest()
    historical = json.loads(historical_bytes)
    if (
        expected_historical_sha256 is not None
        and historical_sha256 != expected_historical_sha256
    ) or historical.get("status") != "PASS" or historical.get("leaked_tokens") != []:
        raise ValueError("historical enabled-fault audit differs or did not pass")

    checks = []
    for case in ("N0-A", "N0-B"):
        case_root = private / "cases" / case
        record = json.loads((case_root / "result.json").read_text())
        evidence_path = case_root / "evidence.json"
        evidence_bytes = evidence_path.read_bytes()
        evidence = json.loads(evidence_bytes)
        if not control_record_passes_v030(record):
            raise ValueError("current control did not pass its original gate")
        incident = record["incident"]
        if (
            incident["environment_id"] != environment_id
            or incident["baseline_id"] != baseline_id
            or incident["baseline_sha256"] != baseline_sha256
        ):
            raise ValueError("current control environment or Baseline differs")
        if evidence.get("incident_id") != incident["incident_id"]:
            raise ValueError("current control Evidence incident differs")
        leaked = sorted(set(FORBIDDEN.findall(evidence_bytes.decode("utf-8"))))
        if leaked:
            raise ValueError("current control Evidence contains evaluator leakage")
        objects = evidence.get("objects", [])
        sources = {item.get("source") for item in objects}
        if sources != REQUIRED_SOURCES:
            raise ValueError("current control Evidence source coverage differs")
        refs = {item.get("evidence_ref") for item in objects}
        diagnosis_refs = set(record["diagnosis"]["supporting_evidence_refs"]) | set(
            record["diagnosis"]["contradicting_evidence_refs"]
        )
        if not diagnosis_refs.issubset(refs):
            raise ValueError("current control Diagnosis references do not resolve")
        checks.append(
            {
                "case": case,
                "incident_id": incident["incident_id"],
                "window": {
                    "started_at": incident["started_at"],
                    "ended_at": incident["ended_at"],
                },
                "object_count": len(objects),
                "sources": sorted(sources),
                "capability_limitations": record["diagnosis"][
                    "capability_limitations"
                ],
                "leaked_tokens": leaked,
                "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            }
        )
    return {
        "status": "PASS",
        "observed_at": datetime.now(UTC).isoformat(),
        "environment_id": environment_id,
        "baseline_id": baseline_id,
        "baseline_sha256": baseline_sha256,
        "current_control_checks": checks,
        "capability_limitations": [],
        "leaked_tokens": [],
        "historical_enabled_fault_audit": {
            "status": historical["status"],
            "sha256": historical_sha256,
            "capability_limitations_preserved": historical.get(
                "capability_limitations", []
            ),
            "scope": "Token leakage only; incomplete historical sources are not declared complete.",
        },
        "evidence_basis": (
            "Exact current N0-A/N0-B complete Product Evidence; no new acquisition, "
            "Incident or fault. Historical enabled-fault token audit is separately "
            "disclosed, not treated as current coverage proof."
        ),
        "incident_count_created": 0,
        "diagnosis_count_created": 0,
        "new_fault_count": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-private-root", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.current_private_root is not None:
        current = args.current_private_root.resolve()
        if not current.is_relative_to(root / ".local/product-v030"):
            raise ValueError("current control root differs")
        historical = root / ".local/product-v030/live-001/pre-p1-acquisition-leakage.json"
        result = build_current_control_leakage_gate(
            current,
            historical,
            expected_historical_sha256=HISTORICAL_ENABLED_FAULT_AUDIT_SHA256,
        )
        write_private_json(
            current / "pre-p1-acquisition-leakage.json", result, create_once=True
        )
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "environment_id",
                        "baseline_id",
                        "leaked_tokens",
                        "capability_limitations",
                    )
                }
            ),
            flush=True,
        )
        return
    private = root / ".local/product-v030/live-001"
    setup = json.loads(
        (private / "baseline-setup-resumed/baseline-result.json").read_text()
    )
    original = json.loads((private / "phase-a-resumed.json").read_text())
    data = private / "product-formal"
    app = create_app(ProductSettingsV1(data_root=data))
    env_id = setup["environment"]["environment_id"]
    environment = app.state.environments.get(env_id)
    identity = app.state.services.get_map(env_id)
    capabilities = app.state.capabilities.get(env_id)
    baseline = app.state.baselines.get_active(env_id)
    authority = load_pilot_runtime_authority_v02(data / "pilot/runtime-authority.json")
    captured = private / "phase-a-full-acquisition"
    runtime = original["runtime_during"]
    snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=env_id,
        authority_sha256=authority.connector_binding_sha256,
        observed_at=datetime.fromisoformat(runtime["observed_at"]),
        services=runtime["services"],
    )
    write_pilot_runtime_snapshot_v02(
        captured / "pilot/runtime-readiness.json", snapshot
    )
    window = original["product_metrics"]["window"]
    candidates = ("checkout", "fraud-detection", "kafka")
    payload = {
        "schema_version": "ecomsre.product.incident.v1",
        "incident_id": "inc-" + "0" * 24,
        "environment_id": env_id,
        "baseline_id": baseline.baseline_id,
        "baseline_sha256": baseline.baseline_sha256,
        "service_identity_sha256": identity.identity_sha256,
        "source_capability_sha256": capabilities.capability_sha256,
        "external_incident_key": "ephemeral-read-preflight-not-persisted",
        "alert_name": "bounded-telemetry-read",
        "summary": "Re-read captured telemetry only; no diagnosis or Incident persistence.",
        "started_at": datetime.fromisoformat(window["started_at"]),
        "ended_at": datetime.fromisoformat(window["ended_at"]),
        "diagnosis_observed_at": datetime.fromisoformat(window["ended_at"]),
        "created_at": datetime.now(UTC),
        "candidate_service_ids": tuple(
            sorted(
                item.service_id
                for item in identity.services
                if item.logical_service in candidates
            )
        ),
        "candidate_logical_services": candidates,
        "labels": {"fault": "synthetic-unknown"},
    }
    draft = IncidentRecordV1.model_validate(
        {
            **payload,
            "incident_sha256": semantic_sha256_v22(
                IncidentRecordV1.model_construct(
                    **payload, incident_sha256="0" * 64
                ).model_dump(mode="json", exclude={"incident_sha256"})
            ),
        }
    )
    backend = ProductReadBackendV1(
        connectors=ConnectorRegistryV1(
            credential_resolver=CredentialResolverV1(),
            timeout_seconds=15,
            data_root=captured,
        ),
        changes=app.state.changes,
        metrics=app.state.metrics,
        pilot_runtime_authority=authority,
    )
    acquisition = backend.acquire(
        incident=draft,
        environment=environment,
        identity_map=identity,
        capability_matrix=capabilities,
        topology_edges=(),
    )
    visible = {
        "raw_outcomes": [
            item.model_dump(mode="json") for item in acquisition.raw_outcomes
        ],
        "memory_outcomes": [
            item.model_dump(mode="json") for item in acquisition.memory_outcomes
        ],
        "snapshots": list(acquisition.snapshots),
        "capability_observations": [
            item.model_dump(mode="json")
            for item in acquisition.capability_observations_v0232
        ],
    }
    leaked = sorted(
        set(
            FORBIDDEN.findall(json.dumps(visible))
        )
    )
    result = {
        "status": "PASS" if not leaked else "FAIL",
        "leaked_tokens": leaked,
        "incident_count_created": 0,
        "diagnosis_count_created": 0,
        "new_fault_count": 0,
        "window": window,
        "runtime_basis": "CAPTURED_ACTUAL_PHASE_A_OBSERVATION",
        "capability_limitations": list(acquisition.capability_limitations),
        "evidence": visible,
    }
    write_private_json(
        private / "pre-p1-acquisition-leakage.json", result, create_once=True
    )
    print(
        json.dumps({key: value for key, value in result.items() if key != "evidence"})
    )


if __name__ == "__main__":
    main()
