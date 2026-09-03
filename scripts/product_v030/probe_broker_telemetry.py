"""Three healthy transactions; verify full acquisition before a new Baseline."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import time

from fastapi.testclient import TestClient
import httpx

from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
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
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre.product.pilot.live_calibration_v02 import (
    _authority_inputs,
    _request_json,
    _run_product_job,
)
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    CANDIDATES_V030,
    build_product_v030_environment_payload,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre_live_sandbox.contracts import write_private_json
from ecomsre_live_sandbox.knowledge_v030 import (
    ProductV030Lifecycle,
    observe_queue_lag_v030,
    owned_runtime_observation_v030,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--resume-failed-traffic", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    private = args.private_root.resolve()
    if not private.is_relative_to(root / ".local/product-v030"):
        raise ValueError("Goal root differs")
    data = private / "product"
    previous = None
    output_path = private / "broker-probe.json"
    if args.resume_failed_traffic:
        previous = json.loads(output_path.read_text())
        if (
            previous.get("status") != "BROKER_TELEMETRY_PROBE_FAILED"
            or "verification" in previous
            or previous.get("traffic", {}).get("failed") != 1
        ):
            raise ValueError(
                "only the preserved pre-verification traffic failure can resume"
            )
        output_path = private / "broker-probe-resumed.json"
    if output_path.exists():
        raise FileExistsError("probe output already exists")
    if data.exists() and previous is None:
        raise FileExistsError("probe Product directory already exists")
    lifecycle = ProductV030Lifecycle(
        repository_root=root,
        private_root=private,
        image_identities=root / ".local/product-v030/acquired-images.json",
    )
    lifecycle.admit()
    backend = lifecycle.authorize_reads()
    assert isinstance(backend, LocalSandboxReadBackend)
    assert lifecycle.goal_controller is not None
    before = lifecycle.goal_controller.read("BASELINE")
    profile = {
        "detector": "n>=3 and value>=max(20,baseline_mean+5*max(baseline_stddev,1))",
        "queue_query": 'sum(kafka_consumer_group_lag_ratio{group="{service}",topic="orders"})',
        "broker_latency_population": "Produce TotalTimeMs JMX 95thPercentile",
        "broker_trace_semantics": "Real KafkaApis.handleProduceRequest execution; not async ACK or end-to-end success",
        "jmx_sha256": hashlib.sha256(
            (root / "config/product-v030/kafka-jmx.yml").read_bytes()
        ).hexdigest(),
    }
    write_private_json(
        private / "control/queue-telemetry-config.json", profile, create_once=True
    )
    profile_sha = hashlib.sha256(
        (private / "control/queue-telemetry-config.json").read_bytes()
    ).hexdigest()
    inputs = _authority_inputs(backend)
    prebound = PilotRuntimeAuthorityV02.build(
        environment_id="env-" + "0" * 24,
        allowed_logical_services=CANDIDATES_V030,
        profile_sha256=profile_sha,
        **inputs,
    )
    authority_path = data / "pilot/runtime-authority.json"
    settings = ProductSettingsV1(
        data_root=data,
        pilot_runtime_authority_path=authority_path,
        connector_timeout_seconds=15,
        fault_family_review_min_occurrences=3,
    )
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "before_flags": before,
        "fault_enable_count": 0,
        "incident_count_created": 0,
        "baseline_count_created": 0,
    }
    try:
        app = create_app(settings)
        with TestClient(app) as client:
            environment = (
                previous["environment"]
                if previous
                else _request_json(
                    client,
                    "POST",
                    "/v1/environments",
                    payload=build_product_v030_environment_payload(
                        repository_root=root,
                        runtime_authority_sha256=prebound.connector_binding_sha256,
                    ),
                )
            )
            result["environment"] = environment
            env_id = environment["environment_id"]
            authority = PilotRuntimeAuthorityV02.build(
                environment_id=env_id,
                allowed_logical_services=CANDIDATES_V030,
                profile_sha256=profile_sha,
                **inputs,
            )
            if previous is None:
                write_pilot_runtime_authority_v02(authority_path, authority)
            elif (
                PilotRuntimeAuthorityV02.model_validate_json(
                    authority_path.read_bytes()
                )
                != authority
            ):
                raise ValueError("resumed probe authority differs")
            started_at = datetime.now(UTC)
            observations = []
            with httpx.Client(
                event_hooks={
                    "response": [
                        lambda response: observations.append(
                            {
                                "path": response.request.url.path,
                                "status": response.status_code,
                            }
                        )
                    ]
                }
            ) as traffic_client:
                traffic = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                    endpoint="http://127.0.0.1:18080/api/checkout",
                    profile=HealthyTrafficProfileV021(
                        request_seed=28902 if previous else 28901,
                        maximum_request_count=3,
                        requests_per_second=1,
                        error_budget=1,
                    ),
                )
            result["traffic"] = {
                **traffic.model_dump(mode="json"),
                "http_observations": observations,
            }
            if traffic.attempted != 3 or traffic.failed:
                raise RuntimeError("three healthy probe transactions did not complete")
            print("stage=BROKER_PROBE_TRAFFIC_3_OF_3_COMPLETE", flush=True)
            while (datetime.now(UTC) - started_at).total_seconds() < 100:
                time.sleep(5)
            services, result["runtime"] = owned_runtime_observation_v030(
                lifecycle.environment, candidates=CANDIDATES_V030
            )
            snapshot = PilotRuntimeSnapshotV02.build(
                environment_id=env_id,
                authority_sha256=authority.connector_binding_sha256,
                observed_at=datetime.now(UTC),
                services=services,
            )
            write_pilot_runtime_snapshot_v02(
                data / "pilot/runtime-readiness.json", snapshot
            )
            ended_at = datetime.now(UTC)
            _, result["verification"] = _run_product_job(
                client,
                settings,
                path=f"/v1/environments/{env_id}/verify-jobs",
                worker_id="product-v030-broker-probe",
            )
            identity = app.state.services.get_map(env_id)
            capabilities = app.state.capabilities.get(env_id)
            payload = {
                "schema_version": "ecomsre.product.incident.v1",
                "incident_id": "inc-" + "0" * 24,
                "environment_id": env_id,
                "baseline_id": "base-" + "0" * 24,
                "baseline_sha256": "0" * 64,
                "service_identity_sha256": identity.identity_sha256,
                "source_capability_sha256": capabilities.capability_sha256,
                "external_incident_key": "ephemeral-read-probe-not-persisted",
                "alert_name": "bounded-telemetry-read",
                "summary": "Non-Incident read-only acquisition probe without Diagnosis or Baseline.",
                "started_at": started_at,
                "ended_at": ended_at,
                "diagnosis_observed_at": ended_at,
                "created_at": ended_at,
                "candidate_service_ids": tuple(
                    sorted(
                        item.service_id
                        for item in identity.services
                        if item.logical_service in CANDIDATES_V030[:3]
                    )
                ),
                "candidate_logical_services": CANDIDATES_V030[:3],
                "labels": {"fault": "none"},
            }
            constructed = IncidentRecordV1.model_construct(
                **payload, incident_sha256="0" * 64
            )
            ephemeral = IncidentRecordV1.model_validate(
                {
                    **payload,
                    "incident_sha256": semantic_sha256_v22(
                        constructed.model_dump(mode="json", exclude={"incident_sha256"})
                    ),
                }
            )
            acquisition = ProductReadBackendV1(
                connectors=ConnectorRegistryV1(
                    credential_resolver=CredentialResolverV1(),
                    timeout_seconds=15,
                    data_root=data,
                ),
                changes=app.state.changes,
                metrics=app.state.metrics,
                pilot_runtime_authority=authority,
            ).acquire(
                incident=ephemeral,
                environment=app.state.environments.get(env_id),
                identity_map=identity,
                capability_matrix=capabilities,
                topology_edges=(),
            )
            evidence = {
                "snapshots": list(acquisition.snapshots),
                "raw_outcomes": [
                    item.model_dump(mode="json") for item in acquisition.raw_outcomes
                ],
                "memory_outcomes": [
                    item.model_dump(mode="json") for item in acquisition.memory_outcomes
                ],
            }
            result["leaked_tokens"] = sorted(
                set(
                    re.findall(
                        r"kafkaQueueProblems|paymentFailure|defaultVariant|feature\s*flag|\.flagd\.json|\.local/product-v030|overload simulation",
                        json.dumps(evidence),
                        re.I,
                    )
                )
            )
            result["capability_limitations"] = list(acquisition.capability_limitations)
            write_private_json(
                private / "broker-probe-acquisition.json", evidence, create_once=True
            )
            with httpx.Client(timeout=10) as lag_client:
                result["lag_after"] = observe_queue_lag_v030(lag_client)
                native = lag_client.get(
                    "http://127.0.0.1:19090/api/v1/query",
                    params={
                        "query": 'kafka_produce_request_time_95p_milliseconds{service_name="kafka"}'
                    },
                )
                native.raise_for_status()
                result["native_broker_p95"] = native.json()
            broker = [
                item
                for item in evidence["raw_outcomes"]
                if item["action_id"] in {"a:traces:kafka", "a:metrics:kafka:core"}
            ]
            result["status"] = (
                "BROKER_TELEMETRY_PROBE_PASS"
                if (
                    not result["leaked_tokens"]
                    and not result["capability_limitations"]
                    and len(broker) == 2
                    and all(item["status"] == "SUCCESS_NONEMPTY" for item in broker)
                    and len(result["native_broker_p95"]["data"]["result"]) == 1
                    and result["lag_after"]["lag"] < 20
                )
                else "BROKER_TELEMETRY_PROBE_FAILED"
            )
            result["after_flags"] = lifecycle.goal_controller.read("BASELINE")
    except Exception as error:
        result["status"] = "BROKER_TELEMETRY_PROBE_FAILED"
        result["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        result["ended_at"] = datetime.now(UTC).isoformat()
        write_private_json(output_path, result, create_once=True)
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in (
                    "status",
                    "capability_limitations",
                    "leaked_tokens",
                    "lag_after",
                )
            }
        ),
        flush=True,
    )
    if result["status"] != "BROKER_TELEMETRY_PROBE_PASS":
        raise RuntimeError("broker telemetry probe did not pass")


if __name__ == "__main__":
    main()
