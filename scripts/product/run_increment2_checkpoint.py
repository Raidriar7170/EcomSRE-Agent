#!/usr/bin/env python3
"""Run the real-connector-type Product baseline checkpoint on loopback fixtures."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from typing import Any, Sequence
from urllib.parse import parse_qs, urlsplit

import httpx

from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


_TOKEN_ENV = "ECOMSRE_PRODUCT_INCREMENT2_CHECKPOINT_TOKEN"
_ROOT = Path(__file__).resolve().parents[2]


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class _ConnectorFixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def _write(self, payload: object, status: int = 200) -> None:
        content = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/prometheus/api/v1/label/service_name/values":
            self._write({"status": "success", "data": ["payment-api"]})
            return
        if parsed.path == "/prometheus/api/v1/query_range":
            expression = query.get("query", [""])[0]
            if "payment-api" not in expression:
                self._write({"error": "unexpected service alias"}, 400)
                return
            ended = int(float(query["end"][0]))
            if expression.startswith("errors"):
                values = [[ended - 30, "0.01"], [ended, "0.02"]]
            elif expression.startswith("cpu"):
                values = [[ended - 30, "10"], [ended, "20"]]
            else:
                values = [[ended - 30, "100"], [ended, "130"]]
            self._write(
                {
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [{"metric": {}, "values": values}],
                    },
                }
            )
            return
        if parsed.path == "/jaeger/api/services":
            self._write({"data": ["PaymentService"]})
            return
        if parsed.path == "/jaeger/api/traces":
            if query.get("service") != ["PaymentService"]:
                self._write({"error": "unexpected service alias"}, 400)
                return
            started = int(query["start"][0])
            self._write(
                {
                    "data": [
                        {
                            "processes": {"p1": {"serviceName": "PaymentService"}},
                            "spans": [
                                {
                                    "spanID": "root",
                                    "operationName": "charge",
                                    "startTime": started + 1_000_000,
                                    "duration": 20_000,
                                    "processID": "p1",
                                    "references": [],
                                    "tags": [],
                                }
                            ],
                        }
                    ]
                }
            )
            return
        if parsed.path == "/health/payment":
            self._write({"healthy": True})
            return
        self._write({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if parsed.path != "/opensearch/otel-*/_search":
            self._write({"error": "not found"}, 404)
            return
        if body["size"] == 0:
            self._write(
                {
                    "hits": {"hits": []},
                    "aggregations": {
                        "services": {"buckets": [{"key": "payment_logs"}]}
                    },
                }
            )
            return
        aliases = body["query"]["bool"]["filter"][0]["terms"]["service"]
        if aliases != ["payment_logs"]:
            self._write({"error": "unexpected service alias"}, 400)
            return
        observed_at = body["query"]["bool"]["filter"][1]["range"]["@timestamp"]["gte"]
        self._write(
            {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": observed_at,
                                "service": "payment_logs",
                                "severity": "DIAGNOSTIC",
                                "body": "charge completed in 20 ms",
                            }
                        }
                    ],
                    "total": {"value": 1},
                }
            }
        )


def _start_process(module: str, environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        (sys.executable, "-m", module),
        cwd=_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait_for_ready(client: httpx.Client, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Product API process exited before readiness")
        try:
            if client.get("/readyz").json() == {"status": "ready"}:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Product API process did not become ready")


def _wait_for_job(client: httpx.Client, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return payload
        time.sleep(0.1)
    raise RuntimeError("Product job did not reach a terminal state")


def run_checkpoint(data_root: Path) -> dict[str, Any]:
    data_root = data_root.resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    api_port = _available_loopback_port()
    fixture_port = _available_loopback_port()
    fixture_server = ThreadingHTTPServer(
        ("127.0.0.1", fixture_port),
        _ConnectorFixtureHandler,
    )
    fixture_thread = threading.Thread(target=fixture_server.serve_forever, daemon=True)
    fixture_thread.start()
    token = secrets.token_urlsafe(32)
    process_environment = os.environ.copy()
    python_path = f"{_ROOT}:{_ROOT / 'src'}"
    if existing_python_path := process_environment.get("PYTHONPATH"):
        python_path = f"{python_path}{os.pathsep}{existing_python_path}"
    process_environment.update(
        {
            "PYTHONPATH": python_path,
            "ECOMSRE_PRODUCT_DATA_ROOT": str(data_root),
            "ECOMSRE_PRODUCT_API_HOST": "127.0.0.1",
            "ECOMSRE_PRODUCT_API_PORT": str(api_port),
            "ECOMSRE_PRODUCT_ADMIN_TOKEN_ENV": _TOKEN_ENV,
            _TOKEN_ENV: token,
        }
    )
    worker_environment = dict(process_environment)
    worker_environment.pop(_TOKEN_ENV, None)
    api_process: subprocess.Popen[str] | None = None
    worker_process: subprocess.Popen[str] | None = None
    try:
        api_process = _start_process("ecomsre.product.app", process_environment)
        worker_process = _start_process("ecomsre.product.jobs.worker", worker_environment)
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(
            base_url=f"http://127.0.0.1:{api_port}", timeout=3
        ) as client:
            _wait_for_ready(client, api_process)
            base = f"http://127.0.0.1:{fixture_port}"
            environment_response = client.post(
                "/v1/environments",
                headers=headers,
                json={
                    "name": "increment-2-connector-checkpoint",
                    "service_identity_policy": {
                        "services": [
                            {
                                "logical_service": "payment",
                                "aliases": {
                                    "prometheus": ["payment-api"],
                                    "opensearch": ["payment_logs"],
                                    "jaeger": ["PaymentService"],
                                    "http_health": ["payment-health"],
                                },
                            }
                        ]
                    },
                    "connector_configs": [
                        {
                            "name": "prometheus",
                            "kind": "PROMETHEUS",
                            "endpoint": f"{base}/prometheus",
                            "settings": {
                                "query_templates": {
                                    "error_rate": "errors{service=\"{service}\"}",
                                    "request_support": "requests{service=\"{service}\"}",
                                    "latency": "latency{service=\"{service}\"}",
                                    "cpu": "cpu{service=\"{service}\"}",
                                    "memory": "memory{service=\"{service}\"}",
                                }
                            },
                        },
                        {
                            "name": "logs",
                            "kind": "OPENSEARCH",
                            "endpoint": f"{base}/opensearch",
                            "settings": {
                                "index_pattern": "otel-*",
                                "timestamp_field": "@timestamp",
                                "service_field": "service",
                                "severity_field": "severity",
                                "message_field": "body",
                            },
                        },
                        {
                            "name": "traces",
                            "kind": "JAEGER",
                            "endpoint": f"{base}/jaeger",
                            "settings": {},
                        },
                        {
                            "name": "runtime",
                            "kind": "HTTP_HEALTH",
                            "settings": {
                                "services": [
                                    {
                                        "service_id": "payment-health",
                                        "health_url": f"{base}/health/payment",
                                        "success_statuses": [200],
                                        "healthy_json_field": "healthy",
                                    }
                                ]
                            },
                        },
                    ],
                    "explicit_service_catalog": ["payment"],
                },
            )
            environment_response.raise_for_status()
            environment_id = environment_response.json()["environment_id"]
            verify_job = client.post(
                f"/v1/environments/{environment_id}/verify-jobs",
                headers=headers,
            )
            verify_job.raise_for_status()
            verify_terminal = _wait_for_job(client, verify_job.json()["job_id"])
            if verify_terminal["status"] != "SUCCEEDED":
                raise RuntimeError(
                    f"connector verification failed: {verify_terminal['safe_error_code']}"
                )
            baseline_job = client.post(
                f"/v1/environments/{environment_id}/baseline-jobs",
                headers=headers,
                json={"activate": False},
            )
            baseline_job.raise_for_status()
            baseline_terminal = _wait_for_job(client, baseline_job.json()["job_id"])
            if baseline_terminal["status"] != "SUCCEEDED":
                raise RuntimeError(
                    f"baseline build failed: {baseline_terminal['safe_error_code']}"
                )
            matrix = client.get(
                f"/v1/environments/{environment_id}/capabilities"
            ).json()
            baselines = client.get(
                f"/v1/environments/{environment_id}/baselines"
            ).json()["items"]

            service = ServiceCatalogRepositoryV1(
                SqliteStoreV1(data_root / "product.sqlite3")
            ).get_map(environment_id).services[0]
            change = client.post(
                f"/v1/environments/{environment_id}/changes",
                headers=headers,
                json={
                    "service_id": service.service_id,
                    "category": "DEPLOYMENT",
                    "occurred_at": "2026-08-27T00:00:00Z",
                    "revision": "checkpoint-r1",
                    "summary": "checkpoint deployment observation",
                    "external_change_id": "increment2-checkpoint-change",
                },
            )
            change.raise_for_status()
        _stop_process(worker_process)
        worker_process = None
        _stop_process(api_process)
        api_process = None

        api_process = _start_process("ecomsre.product.app", process_environment)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{api_port}", timeout=3
        ) as client:
            _wait_for_ready(client, api_process)
            persisted_matrix = client.get(
                f"/v1/environments/{environment_id}/capabilities"
            ).json()
            persisted_baselines = client.get(
                f"/v1/environments/{environment_id}/baselines"
            ).json()["items"]
        sources = {item["source"]: item["status"] for item in matrix["sources"]}
        if sources != {
            "CHANGES": "AVAILABLE",
            "LOGS": "AVAILABLE",
            "METRICS": "AVAILABLE",
            "RESOURCES": "AVAILABLE",
            "RUNTIME": "AVAILABLE",
            "TRACES": "AVAILABLE",
        }:
            raise RuntimeError("source capability matrix is incomplete")
        if len(baselines) != 1 or baselines[0]["successful_windows"] != 6:
            raise RuntimeError("historical baseline was not built from six windows")
        if baselines[0]["active"] is not False:
            raise RuntimeError("baseline was activated without explicit promotion")
        if persisted_matrix != matrix or persisted_baselines != baselines:
            raise RuntimeError("Increment 2 state did not survive API restart")
        return {
            "schema_version": "ecomsre.product.increment2-checkpoint.v1",
            "terminal": "ECOMSRE_PRODUCT_MVP_V01_CONNECTOR_PASS",
            "connector_transport": "BOUNDED_LOOPBACK_HTTP",
            "configured_connector_kinds": [
                "PROMETHEUS",
                "OPENSEARCH",
                "JAEGER",
                "HTTP_HEALTH",
            ],
            "environment_id": environment_id,
            "canonical_services": [service.logical_service],
            "source_statuses": sources,
            "baseline_id": baselines[0]["baseline_id"],
            "baseline_window_count": baselines[0]["window_count"],
            "baseline_successful_windows": baselines[0]["successful_windows"],
            "baseline_active": baselines[0]["active"],
            "change_event_id": change.json()["change_event_id"],
            "state_persisted_after_restart": True,
            "agent_writes": 0,
            "runbook_executions": 0,
        }
    finally:
        _stop_process(worker_process)
        _stop_process(api_process)
        fixture_server.shutdown()
        fixture_server.server_close()
        fixture_thread.join(timeout=5)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.data_root is not None:
        result = run_checkpoint(args.data_root)
    else:
        with TemporaryDirectory(prefix="ecomsre-product-increment2-") as directory:
            result = run_checkpoint(Path(directory))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main", "run_checkpoint")
