"""One non-Incident off/on/off telemetry preflight on the already-owned runtime."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time

import httpx

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
)
from ecomsre.product.connectors.base import ConnectorQueryContextV1, ConnectorWindowV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    build_product_v023_environment_payload,
)
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.contracts import ConnectorConfigV1
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre_live_sandbox.contracts import write_private_json
from ecomsre_live_sandbox.knowledge_v030 import (
    ProductV030Lifecycle,
    owned_runtime_observation_v030,
)


LAG_SELECTOR = 'kafka_consumer_group_lag_ratio{group="fraud-detection",topic="orders"}'
QUEUE_TEMPLATE = 'sum(kafka_consumer_group_lag_ratio{group="{service}",topic="orders"})'
CANDIDATES = ("checkout", "fraud-detection", "kafka")


def prom(client: httpx.Client, expression: str) -> list[dict]:
    response = client.get(
        "http://127.0.0.1:19090/api/v1/query", params={"query": expression}
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success" or payload["data"]["resultType"] != "vector":
        raise RuntimeError("Prometheus query did not return a vector")
    return payload["data"]["result"]


def lag(client: httpx.Client) -> dict:
    values = prom(client, LAG_SELECTOR)
    stamps = prom(client, f"timestamp({LAG_SELECTOR})")
    if (
        len(values) != 1
        or len(stamps) != 1
        or values[0]["metric"].get("partition") != "0"
    ):
        raise RuntimeError("discovered consumer-lag series inventory changed")
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "lag": float(values[0]["value"][1]),
        "source_timestamp": float(stamps[0]["value"][1]),
        "series": values[0],
    }


def traffic(client: httpx.Client, seed: int) -> dict:
    requests = []

    def observed(response: httpx.Response) -> None:
        requests.append(
            {"path": response.request.url.path, "status": response.status_code}
        )

    client.event_hooks["response"].append(observed)
    result = BoundedHealthyCheckoutTrafficV021(client=client).run(
        endpoint="http://127.0.0.1:18080/api/checkout",
        profile=HealthyTrafficProfileV021(
            request_seed=seed,
            maximum_request_count=3,
            requests_per_second=1,
            error_budget=1,
        ),
    )
    client.event_hooks["response"].remove(observed)
    return {**result.model_dump(mode="json"), "http_observations": requests}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--resume-before-fault", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    private = args.private_root.resolve()
    if not private.is_relative_to(root / ".local/product-v030"):
        raise ValueError("private Goal directory differs")
    result_path = private / "phase-a.json"
    if args.resume_before_fault:
        previous = json.loads(result_path.read_text())
        if (
            previous.get("fault_enable_count") != 0
            or previous.get("baseline_restored_and_drained") is not True
        ):
            raise ValueError(
                "cannot resume a preflight that already enabled a fault or lacks restore proof"
            )
        result_path = private / "phase-a-resumed.json"
    if result_path.exists():
        raise FileExistsError("non-Incident preflight result already exists")
    lifecycle = ProductV030Lifecycle(
        repository_root=root,
        private_root=private,
        image_identities=root / ".local/product-v030/acquired-images.json",
    )
    lifecycle.admit()
    lifecycle.authorize_reads()
    controller = lifecycle.goal_controller
    assert controller is not None
    result: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "incident_count": 0,
        "fault_enable_count": 0,
        "transitions": [],
        "lag_observations": [],
        "product_action_authority": "NONE",
        "provider_calls": 0,
    }
    write_private_json(
        private / "control/queue-telemetry-config.json",
        {
            "query_template": QUEUE_TEMPLATE,
            "step_seconds": 10,
            "logical_service": "fraud-detection",
            "metric_kind": "QUEUE_LAG",
            "unit": "COUNT",
            "detector": "sample_count >= 3 and value >= max(20, baseline_mean + 5 * max(baseline_stddev, 1))",
            "health_basis": "COORDINATOR_MEMBERSHIP_NOT_LOW_LAG",
        },
        create_once=True,
    )

    def persist() -> None:
        write_private_json(private / "phase-a-progress.json", result, create_once=False)

    with httpx.Client(timeout=15) as client:
        try:
            result["transitions"].append(controller.read("BASELINE"))
            result["healthy_traffic"] = traffic(client, 29001)
            persist()
            if (
                result["healthy_traffic"]["attempted"] != 3
                or result["healthy_traffic"]["failed"]
            ):
                raise RuntimeError(
                    "three healthy checkout transactions did not complete"
                )
            time.sleep(15)
            result["before"] = lag(client)
            if result["before"]["lag"] >= 20:
                raise RuntimeError("queue pre-state is not low lag")
            services, proof = owned_runtime_observation_v030(
                lifecycle.environment, candidates=CANDIDATES
            )
            result["runtime_before"] = proof
            if any(
                item["state"] != "RUNNING"
                or not item["healthy"]
                or item["restart_count"]
                for item in services.values()
            ):
                raise RuntimeError("Runtime pre-state is not healthy")
            persist()
            result["fault_started_at"] = datetime.now(UTC).isoformat()
            result["transitions"].append(controller.apply("QUEUE"))
            result["fault_enable_count"] = 1
            persist()
            result["fault_traffic"] = traffic(client, 29002)
            persist()
            if (
                result["fault_traffic"]["attempted"] != 3
                or result["fault_traffic"]["failed"]
            ):
                raise RuntimeError("three fault checkout transactions did not complete")
            print("stage=QUEUE_ENABLED_THREE_TRANSACTIONS", flush=True)
            deadline = time.monotonic() + 120
            strong_stamps: set[float] = set()
            while len(strong_stamps) < 3:
                observed = lag(client)
                result["lag_observations"].append(observed)
                if observed["lag"] >= 20:
                    strong_stamps.add(observed["source_timestamp"])
                persist()
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "three distinct elevated lag observations were not obtained"
                    )
                time.sleep(5)
            result["strong_source_timestamp_count"] = len(strong_stamps)
            services, proof = owned_runtime_observation_v030(
                lifecycle.environment, candidates=CANDIDATES
            )
            result["runtime_during"] = proof
            if not all(
                item["healthy"] and item["state"] == "RUNNING"
                for item in services.values()
            ):
                raise RuntimeError("Runtime became unhealthy during queue preflight")
            end = datetime.now(UTC)
            start = datetime.fromisoformat(result["fault_started_at"])
            window = ConnectorWindowV1(started_at=start, ended_at=end)
            env = build_product_v023_environment_payload(
                repository_root=root, runtime_authority_sha256="0" * 64
            )
            prometheus = next(
                item
                for item in env["connector_configs"]
                if item["kind"] == "PROMETHEUS"
            )
            prometheus["settings"]["query_templates"]["queue_lag"] = QUEUE_TEMPLATE
            prometheus["settings"]["step_seconds"] = 10
            connector = PrometheusConnectorV1(
                ConnectorConfigV1.model_validate(prometheus),
                credential_resolver=CredentialResolverV1(environment={}),
                timeout_seconds=15,
            )
            metric_results = connector.query(
                ConnectorQueryContextV1(
                    environment_id="env-" + "0" * 24,
                    requested_services=("fraud-detection",),
                    window=window,
                    maximum_records=1,
                    requested_source=EvidenceSourceV22.METRICS,
                    metric_kinds=(MetricKindV22.QUEUE_LAG,),
                )
            )
            metric = next(
                item
                for item in metric_results
                if item.source is EvidenceSourceV22.METRICS
            )
            result["product_metrics"] = metric.model_dump(mode="json")
            if (
                len(metric.records) != 1
                or not isinstance(metric.records[0], MetricFactV22)
                or metric.records[0].sample_count < 3
                or metric.records[0].value is None
                or metric.records[0].value < 20
            ):
                raise RuntimeError("Product queue MetricFact contract did not pass")
            opensearch = next(
                item
                for item in env["connector_configs"]
                if item["kind"] == "OPENSEARCH"
            )
            logs_connector = OpenSearchConnectorV1(
                ConnectorConfigV1.model_validate(opensearch),
                credential_resolver=CredentialResolverV1(environment={}),
                timeout_seconds=15,
            )
            log_results = logs_connector.query(
                ConnectorQueryContextV1(
                    environment_id="env-" + "0" * 24,
                    requested_services=CANDIDATES,
                    window=window,
                    maximum_records=1000,
                    requested_source=EvidenceSourceV22.LOGS,
                )
            )
            result["product_logs"] = [
                item.model_dump(mode="json") for item in log_results
            ]
            visible = json.dumps([result["product_metrics"], result["product_logs"]])
            forbidden = (
                "kafkaQueueProblems",
                "paymentFailure",
                "defaultVariant",
                "flagd",
                ".local/product-v030",
                "overload simulation",
            )
            if any(token.casefold() in visible.casefold() for token in forbidden):
                raise RuntimeError("Product telemetry retained private control truth")
            if any(item.status.value.startswith("FAILURE") for item in log_results):
                raise RuntimeError("Product log query failed")
            messages = [
                record.message
                for item in log_results
                for record in item.records
                if isinstance(record, LogRecordV22)
            ]
            if not any("sleeping 1 second" in message for message in messages):
                raise RuntimeError("live normalized consumer symptom was not observed")
            result["leakage_check"] = "PASS"
            result["status"] = "ECOMSRE_PRODUCT_V030_QUEUE_TELEMETRY_READY"
        except Exception as error:
            result["status"] = "QUEUE_TELEMETRY_PREFLIGHT_FAILED"
            result["failure"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            try:
                result["transitions"].append(controller.apply("BASELINE"))
                deadline = time.monotonic() + 120
                while True:
                    result["after"] = lag(client)
                    if result["after"]["lag"] < 20:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("queue did not drain after baseline restore")
                    time.sleep(5)
                result["baseline_restored_and_drained"] = True
            except Exception as error:
                result["baseline_restored_and_drained"] = False
                result["restore_failure"] = type(error).__name__
                result["status"] = "QUEUE_TELEMETRY_PREFLIGHT_FAILED"
                raise
            finally:
                result["ended_at"] = datetime.now(UTC).isoformat()
                persist()
                write_private_json(result_path, result, create_once=True)
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "fault_enable_count",
                    "baseline_restored_and_drained",
                )
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
