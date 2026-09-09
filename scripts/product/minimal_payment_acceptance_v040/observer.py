"""Independent read-only resource and business observer; no flag write capability."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
import httpx
from ecomsre.product.remediation.execution_contracts import (
    RecoveryObservationV1,
    RecoveryPolicyV1,
)
from ecomsre.product.remediation.state import TrustedStateBindingV1
from .owned import command, digest, save


def atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(value, sort_keys=True))
    temporary.chmod(0o600)
    os.replace(temporary, path)


def signature(value: Any, key: str) -> str:
    return hmac.new(
        key.encode(),
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def memory_bytes(value: str) -> int:
    units = {
        "B": 1,
        "kB": 1000,
        "KB": 1000,
        "KiB": 1024,
        "MB": 1000**2,
        "MiB": 1024**2,
        "GB": 1000**3,
        "GiB": 1024**3,
    }
    for suffix in sorted(units, key=len, reverse=True):
        if value.endswith(suffix):
            return round(float(value[: -len(suffix)]) * units[suffix])
    raise ValueError("RESOURCE_UNIT_UNKNOWN")


class Observer:
    def __init__(
        self,
        root: Path,
        port: int,
        probe_port: int,
        flag_port: int,
        payment_id: str,
        validate: Any,
        key: str,
    ):
        self.root, self.payment_id, self.validate, self.key = (
            root,
            payment_id,
            validate,
            key,
        )
        self.probe = f"http://127.0.0.1:{probe_port}"
        self.flag = f"http://127.0.0.1:{flag_port}"
        self.client = httpx.Client(timeout=8, trust_env=False)
        self.binding: TrustedStateBindingV1 | None = None
        self.policy: RecoveryPolicyV1 | None = None
        self.stop = threading.Event()
        self.error: Exception | None = None
        self.metrics: str | None = None
        self.sample = 0
        self.window = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                if (
                    self.path != "/metrics"
                    or owner.metrics is None
                    or owner.error is not None
                ):
                    self.send_error(503)
                    return
                self.send_response(200)
                self.send_header(
                    "Content-Type", "text/plain; version=0.0.4; charset=utf-8"
                )
                self.end_headers()
                self.wfile.write(owner.metrics.encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.threads = [
            threading.Thread(target=self.server.serve_forever, daemon=True),
            threading.Thread(target=self.run, daemon=True),
            threading.Thread(target=self.windows, daemon=True),
        ]

    def start(self) -> None:
        for thread in self.threads:
            thread.start()

    def close(self) -> None:
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        for thread in self.threads:
            thread.join(timeout=15)

    def check(self) -> None:
        if self.error is not None:
            raise RuntimeError("READ_ONLY_OBSERVER_FAILED") from self.error

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                self.validate()
                row = json.loads(
                    command(
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{json .}}",
                        self.payment_id,
                    )
                )
                cpu = float(row["CPUPerc"].rstrip("%"))
                memory = memory_bytes(row["MemUsage"].split(" / ")[0])
                self.metrics = f'payment_owned_cpu_percent{{service_name="payment"}} {cpu}\npayment_owned_memory_bytes{{service_name="payment"}} {memory}\n'
                save(
                    self.root / "observer/raw" / f"resource-{self.sample:05d}.json",
                    {"observed_at": datetime.now(UTC).isoformat(), "stats": row},
                )
                self.sample += 1
                if self.binding is not None:
                    b = self.binding
                    body = {
                        name: getattr(b, name)
                        for name in (
                            "environment_id",
                            "environment_ownership_digest",
                            "target_identity_digest",
                            "control_identity_sha256",
                        )
                    }
                    body.update(
                        non_owned_resources_unchanged=True,
                        observed_at=datetime.now(UTC).isoformat(),
                    )
                    atomic(
                        self.root / "observer/ownership.json",
                        {**body, "signature": signature(body, self.key)},
                    )
                self.stop.wait(1)
        except Exception as error:
            self.error = error

    def current(self) -> tuple[str, bool]:
        value = json.loads((self.root / "control/flags/demo.flagd.json").read_bytes())
        response = self.client.post(
            self.flag + "/ofrep/v1/evaluate/flags/paymentFailure", json={}
        )
        response.raise_for_status()
        evaluation = response.json()
        return digest(value), evaluation.get("variant") == "off" and type(
            evaluation.get("value")
        ) is int and evaluation["value"] == 0

    def windows(self) -> None:
        try:
            while not self.stop.wait(0.2):
                policy = self.policy
                if policy is None:
                    continue
                start = datetime.now(UTC)
                end = start + timedelta(seconds=policy.window_seconds)
                mono = time.monotonic()
                before = self.current()
                requests = []
                while datetime.now(UTC) < end and not self.stop.is_set():
                    response = self.client.get(self.probe + "/probe")
                    response.raise_for_status()
                    requests.append(
                        {"at": datetime.now(UTC).isoformat(), "value": response.json()}
                    )
                    self.stop.wait(0.25)
                if self.stop.is_set():
                    return
                after = self.current()
                self.validate()
                elapsed = (time.monotonic() - mono) * 1000
                observation = RecoveryObservationV1.build(
                    environment_id=policy.environment_id,
                    policy_sha256=policy.policy_sha256,
                    started_at=start,
                    ended_at=end,
                    elapsed_ms=elapsed,
                    infrastructure_passed=True,
                    endpoint_passed=all(
                        item["value"]["grpc_code"] == 0 for item in requests
                    ),
                    business_observation_kind="DIRECT_PAYMENT_TRAFFIC",
                    business_requests=len(requests),
                    business_errors=sum(not item["value"]["ok"] for item in requests),
                    configuration_digest=after[0],
                    flag_evaluation_restored=before
                    == after
                    == (policy.baseline_configuration_digest, True),
                    non_owned_resources_unchanged=True,
                    environment_ownership_digest=policy.environment_ownership_digest,
                    created_at=datetime.now(UTC),
                )
                body = observation.model_dump(mode="json")
                envelope = {"observation": body, "signature": signature(body, self.key)}
                save(
                    self.root / "observer/raw" / f"window-{self.window:04d}.json",
                    {
                        "envelope": envelope,
                        "requests": requests,
                        "before": before,
                        "after": after,
                    },
                )
                self.window += 1
                atomic(self.root / "observer/recovery.json", envelope)
        except Exception as error:
            self.error = error
