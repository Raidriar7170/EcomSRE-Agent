"""Read the existing fault window with corrected Product configuration; never inject."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, LogRecordV22
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.contracts import ConnectorConfigV1
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    build_product_v030_environment_payload,
)
from ecomsre_live_sandbox.contracts import write_private_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    private = args.private_root.resolve()
    source = private / "phase-a-resumed.json"
    original = json.loads(source.read_text())
    window = ConnectorWindowV1.model_validate(original["product_metrics"]["window"])
    strong_stamps = set()
    for sample in original["lag_observations"]:
        observed_at = datetime.fromisoformat(sample["observed_at"]).timestamp()
        stamp = sample["source_timestamp"]
        if (
            not math.isfinite(sample["lag"])
            or not math.isfinite(stamp)
            or not 0 <= observed_at - stamp <= 20
        ):
            raise RuntimeError("queue observation is nonfinite or stale")
        if (
            sample["lag"] >= 20
            and window.started_at.timestamp() <= stamp <= window.ended_at.timestamp()
        ):
            strong_stamps.add(stamp)
    if original["fault_enable_count"] != 1 or len(strong_stamps) < 3:
        raise RuntimeError(
            "one preflight with three fresh elevated lag samples is required"
        )
    restored_at = datetime.fromisoformat(
        original["transitions"][-1]["observed_at"]
    ).timestamp()
    if (
        not original["baseline_restored_and_drained"]
        or original["after"]["lag"] >= 20
        or original["after"]["source_timestamp"] < restored_at
    ):
        raise RuntimeError("fresh post-restore low lag is not proven")
    for key in ("runtime_before", "runtime_during"):
        for state in original[key]["services"].values():
            if (
                state["state"] != "RUNNING"
                or not state["healthy"]
                or state["restart_count"]
            ):
                raise RuntimeError("preflight Runtime state did not pass")
    environment = build_product_v030_environment_payload(
        repository_root=root, runtime_authority_sha256="0" * 64
    )
    registry = ConnectorRegistryV1(
        credential_resolver=CredentialResolverV1(environment={}), timeout_seconds=15
    )
    results: list[ConnectorQueryResultV1] = []
    for raw in environment["connector_configs"]:
        if raw["kind"] == "PILOT_RUNTIME":
            continue
        connector = registry.create(ConnectorConfigV1.model_validate(raw))
        try:
            results.extend(
                connector.query(
                    ConnectorQueryContextV1(
                        environment_id="env-" + "0" * 24,
                        requested_services=("checkout", "fraud-detection", "kafka"),
                        window=window,
                        maximum_records=200,
                    )
                )
            )
        finally:
            connector.close()
    visible = {
        "connector_results": [item.model_dump(mode="json") for item in results],
        "runtime_records": original["runtime_during"]["services"],
    }
    serialized = json.dumps(visible)
    forbidden = re.compile(
        r"kafkaQueueProblems|paymentFailure|defaultVariant|feature\s*flag|\.flagd\.json|\.local/product-v030|overload simulation",
        re.I,
    )
    leaked = sorted(set(forbidden.findall(serialized)))
    logs = next(item for item in results if item.source is EvidenceSourceV22.LOGS)
    symptoms = [
        record.message for record in logs.records if isinstance(record, LogRecordV22)
    ]
    passed = not leaked and any("sleeping 1 second" in message for message in symptoms)
    passed = passed and all(
        not item.status.value.startswith("FAILURE") for item in results
    )
    output = {
        "status": "ECOMSRE_PRODUCT_V030_QUEUE_TELEMETRY_READY"
        if passed
        else "QUEUE_TELEMETRY_VERIFICATION_FAILED",
        "observed_at": datetime.now(UTC).isoformat(),
        "source_preflight_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "fault_enable_count_total": 1,
        "new_fault_enable_count": 0,
        "incident_count": 0,
        "strong_source_timestamps": sorted(strong_stamps),
        "fresh_post_restore_low_lag": True,
        "leaked_tokens": leaked,
        "coverage": [item.source.value for item in results] + ["RUNTIME"],
        "evidence": visible,
        "environment_payload": environment,
        "complete_incident_acquisition_leakage_gate_before_p1": "PENDING",
    }
    write_private_json(private / "phase-a-verification.json", output, create_once=True)
    print(
        json.dumps(
            {
                key: output[key]
                for key in (
                    "status",
                    "new_fault_enable_count",
                    "leaked_tokens",
                    "coverage",
                )
            }
        ),
        flush=True,
    )
    if not passed:
        raise RuntimeError(
            "existing queue preflight window did not pass telemetry verification"
        )


if __name__ == "__main__":
    main()
