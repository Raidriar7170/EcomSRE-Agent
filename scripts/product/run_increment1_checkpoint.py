#!/usr/bin/env python3
"""Run the durable Product shell checkpoint without external connectors."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import Any, Sequence

import httpx


_TOKEN_ENV = "ECOMSRE_PRODUCT_INCREMENT1_CHECKPOINT_TOKEN"
_ROOT = Path(__file__).resolve().parents[2]


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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
            response = client.get("/readyz")
            if response.status_code == 200 and response.json() == {"status": "ready"}:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Product API process did not become ready")


def run_checkpoint(data_root: Path) -> dict[str, Any]:
    data_root = data_root.resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    port = _available_loopback_port()
    process_environment = os.environ.copy()
    python_path = f"{_ROOT}:{_ROOT / 'src'}"
    if existing_python_path := process_environment.get("PYTHONPATH"):
        python_path = f"{python_path}{os.pathsep}{existing_python_path}"
    process_environment.update(
        {
            "PYTHONPATH": python_path,
            "ECOMSRE_PRODUCT_DATA_ROOT": str(data_root),
            "ECOMSRE_PRODUCT_API_HOST": "127.0.0.1",
            "ECOMSRE_PRODUCT_API_PORT": str(port),
            "ECOMSRE_PRODUCT_ADMIN_TOKEN_ENV": _TOKEN_ENV,
            _TOKEN_ENV: token,
        }
    )
    worker_environment = dict(process_environment)
    worker_environment.pop(_TOKEN_ENV, None)
    api_process: subprocess.Popen[str] | None = None
    worker_process: subprocess.Popen[str] | None = None
    try:
        headers = {"Authorization": f"Bearer {token}"}
        api_process = _start_process("ecomsre.product.app", process_environment)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            _wait_for_ready(client, api_process)
            created_response = client.post(
                "/v1/environments",
                headers=headers,
                json={
                    "name": "increment-1-checkpoint",
                    "description": "Deterministic fixture Product checkpoint",
                    "timezone": "UTC",
                    "service_identity_policy": {
                        "canonical_field": "service.name"
                    },
                    "connector_configs": [
                        {
                            "name": "fixture",
                            "kind": "FIXTURE",
                            "endpoint": None,
                            "settings": {"dataset": "increment-1"},
                            "credential_refs": {},
                        }
                    ],
                    "explicit_service_catalog": ["frontend", "payment"],
                },
            )
            created_response.raise_for_status()
            environment_record = created_response.json()
            environment_id = environment_record["environment_id"]
            job_response = client.post(
                f"/v1/environments/{environment_id}/verify-jobs",
                headers=headers,
            )
            job_response.raise_for_status()
            job_id = job_response.json()["job_id"]
            worker_process = _start_process(
                "ecomsre.product.jobs.worker",
                worker_environment,
            )
            deadline = time.monotonic() + 10
            completed_job: httpx.Response | None = None
            while time.monotonic() < deadline:
                if worker_process.poll() is not None:
                    raise RuntimeError("Product worker process exited before completion")
                candidate = client.get(f"/v1/jobs/{job_id}")
                candidate.raise_for_status()
                if candidate.json()["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    completed_job = candidate
                    break
                time.sleep(0.1)
            if completed_job is None:
                raise RuntimeError("fixture job did not reach a terminal state")

        _stop_process(worker_process)
        worker_process = None
        _stop_process(api_process)
        api_process = None

        api_process = _start_process("ecomsre.product.app", process_environment)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            _wait_for_ready(client, api_process)
            persisted_environment = client.get(f"/v1/environments/{environment_id}")
            persisted_environment.raise_for_status()
            persisted_job = client.get(f"/v1/jobs/{job_id}")
            persisted_job.raise_for_status()

        environment_persisted = persisted_environment.json() == environment_record
        fixture_job_status = persisted_job.json()["status"]
        if not environment_persisted:
            raise RuntimeError("environment did not survive Product API restart")
        if fixture_job_status != "SUCCEEDED":
            raise RuntimeError("fixture job did not complete successfully")

        return {
            "schema_version": "ecomsre.product.increment1-checkpoint.v1",
            "terminal": "ECOMSRE_PRODUCT_MVP_V01_API_PASS",
            "process_mode": "SEPARATE_API_AND_WORKER_PROCESSES",
            "api_process_starts": 2,
            "worker_process_starts": 1,
            "environment_id": environment_id,
            "environment_persisted_after_restart": environment_persisted,
            "fixture_job_id": job_id,
            "fixture_job_status": fixture_job_status,
        }
    finally:
        _stop_process(worker_process)
        _stop_process(api_process)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.data_root is not None:
        result = run_checkpoint(args.data_root)
    else:
        with TemporaryDirectory(prefix="ecomsre-product-increment1-") as directory:
            result = run_checkpoint(Path(directory))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main", "run_checkpoint")
