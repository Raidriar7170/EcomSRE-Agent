"""Use actual Product APIs and repositories; never insert Diagnosis/fixture rows."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import time
import subprocess
from typing import Any
import httpx
from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
from ecomsre.product.remediation.attempt_contracts import AttemptRequestV1
from ecomsre.product.remediation.repository import RemediationRepositoryV1
from ecomsre.product.remediation.state import StateObservationV1, TrustedStateBindingV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from .owned import save, GOAL


def environment_payload(name: str) -> dict[str, Any]:
    labels = '{service_name="{service}"}'
    templates = {
        "request_support": "sum(increase(payment_probe_requests_total"
        + labels
        + "[30s]))",
        "error_rate": "sum(rate(payment_probe_errors_total"
        + labels
        + "[30s])) / sum(rate(payment_probe_requests_total"
        + labels
        + "[30s]))",
        "latency": "sum(rate(payment_probe_duration_milliseconds_total"
        + labels
        + "[30s])) / sum(rate(payment_probe_requests_total"
        + labels
        + "[30s]))",
        "cpu": "payment_owned_cpu_percent" + labels,
        "memory": "payment_owned_memory_bytes" + labels,
    }
    return {
        "name": name,
        "description": "Owned local direct Payment acceptance; measured probes and resource observations.",
        "service_identity_policy": {
            "discovery_mode": "DECLARED_ONLY",
            "services": [
                {"logical_service": "payment", "aliases": {"prometheus": ["payment"]}}
            ],
        },
        "connector_configs": [
            {
                "name": "minimal-prometheus",
                "kind": "PROMETHEUS",
                "endpoint": "http://prometheus:9090",
                "settings": {"query_templates": templates, "step_seconds": 1},
            }
        ],
    }


class Product:
    def __init__(self, root: Path, port: int, admin: str, check: Any, api_id: str):
        self.root, self.check, self.api_id = root, check, api_id
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + admin},
            timeout=30,
            trust_env=False,
        )
        self.ordinal = 0
        self.environment: dict[str, Any] = {}
        self.baseline: dict[str, Any] = {}
        self.service: str = ""

    def call(self, method: str, route: str, body: Any = None) -> Any:
        self.check()
        self.ordinal += 1
        raw = subprocess.check_output(
            ["docker", "exec", "-i", self.api_id, "python", "/api_transport.py"],
            input=json.dumps(
                {
                    "method": method,
                    "route": route,
                    "body": body,
                    "key": f"minimal-{self.ordinal}",
                }
            ),
            text=True,
            timeout=45,
        )
        response = json.loads(raw)
        save(
            self.root / "product" / f"{self.ordinal:04d}.json",
            {
                "method": method,
                "route": route,
                "request": body,
                "status": response["status"],
                "response": response["body"],
            },
        )
        if not 200 <= response["status"] < 300:
            raise ValueError("PRODUCT_API_ERROR:" + json.dumps(response))
        return response["body"]

    def job(self, route: str, body: Any = None) -> Any:
        value = self.call("POST", route, body)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            result = self.call("GET", "/v1/jobs/" + value["job_id"])
            if result["status"] == "SUCCEEDED":
                return result
            if result["status"] in ("FAILED", "CANCELLED"):
                raise ValueError("PRODUCT_JOB_FAILED:" + json.dumps(result))
            time.sleep(1)
        raise ValueError("PRODUCT_JOB_TIMEOUT")

    def prepare(self, name: str) -> None:
        self.environment = self.call(
            "POST", "/v1/environments", environment_payload(name)
        )
        prefix = "/v1/environments/" + self.environment["environment_id"]
        self.job(prefix + "/verify-jobs")
        self.capabilities = self.call("GET", prefix + "/capabilities")
        self.job(
            prefix + "/baseline-jobs",
            {
                "build_policy": {
                    "mode": "DEMO_ONLY",
                    "lookback_seconds": 180,
                    "window_count": 5,
                    "minimum_successful_windows": 5,
                    "warmup_seconds": 180,
                },
                "candidate_services": ["payment"],
                "activate": True,
            },
        )
        versions = self.call("GET", prefix + "/baselines")["items"]
        if len(versions) != 1 or not versions[0]["active"]:
            raise ValueError("ACTIVE_BASELINE_MISSING")
        self.baseline = versions[0]
        store = SqliteStoreV1(self.root / "data/product.sqlite3")
        with store.connect() as db:
            rows = db.execute(
                "SELECT service_id FROM services WHERE environment_id=? AND logical_service=?",
                (self.environment["environment_id"], "payment"),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("PAYMENT_SERVICE_IDENTITY_MISSING")
        self.service = rows[0][0]

    def diagnose(self, name: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        incident = self.call(
            "POST",
            "/v1/incidents",
            {
                "environment_id": self.environment["environment_id"],
                "external_incident_key": name,
                "alert_name": "Payment business observation",
                "summary": "Direct Payment request monitoring",
                "started_at": (now - timedelta(seconds=60)).isoformat(),
                "ended_at": now.isoformat(),
                "candidate_service_ids": [self.service],
                "labels": {},
            },
        )
        route = "/v1/incidents/" + incident["incident_id"]
        self.job(route + "/diagnosis-jobs")
        diagnosis = self.call("GET", route + "/diagnosis")
        evidence = self.call("GET", route + "/evidence")
        index = self.call("GET", route + "/evidence-index")
        refs = {item["evidence_ref"]: item for item in evidence["objects"]}
        for ref in diagnosis["supporting_evidence_refs"]:
            item = refs[ref]
            checksum = item["object_sha256"]
            path = (
                self.root / "data/objects/sha256" / checksum[:2] / (checksum + ".json")
            )
            import hashlib

            if (
                hashlib.sha256(path.read_bytes()).hexdigest() != checksum
                or json.loads(path.read_bytes()) != item["payload"]
            ):
                raise ValueError("EVIDENCE_CAS_MISMATCH")
        return {
            "incident": incident,
            "diagnosis": diagnosis,
            "evidence": evidence,
            "index": index,
            "all_supporting_refs_resolved": True,
        }

    def change(self, revision: str) -> Any:
        return self.call(
            "POST",
            "/v1/environments/" + self.environment["environment_id"] + "/changes",
            {
                "service_id": self.service,
                "category": "CONFIGURATION",
                "occurred_at": datetime.now(UTC).isoformat(),
                "revision": revision,
                "summary": "Observed local configuration rollout",
                "external_change_id": "minimal-config-rollout",
            },
        )

    def approve(self, diagnosis: dict[str, Any]) -> tuple[Any, Any]:
        value = diagnosis["diagnosis"]
        if (
            value["terminal"] != "CORE_KNOWN"
            or value["root_service_ids"] != [self.service]
            or value["broad_domain"] != "CONFIGURATION"
            or value["mechanism"] != "CONFIGURATION_ERROR"
            or value["provider_calls"] != 0
        ):
            raise ValueError("CONFIGURATION_DIAGNOSIS_NOT_OBSERVED")
        projection = self.call(
            "POST",
            "/v1/incidents/"
            + diagnosis["incident"]["incident_id"]
            + "/remediation-candidates",
        )
        if len(projection["candidates"]) != 1:
            raise ValueError("UNIQUE_CANDIDATE_MISSING:" + json.dumps(projection))
        candidate = projection["candidates"][0]
        approval = self.call(
            "POST",
            "/v1/remediation-candidates/" + candidate["candidate_id"] + "/approvals",
            {
                "approver": "LOCAL_OPERATOR",
                "authorization_source": "USER_EXPLICIT_PRODUCT_V040_GOAL_AUTHORIZATION",
                "decision": "APPROVE",
                "scope": {
                    "runbook_id": "ROLLBACK_CONFIGURATION",
                    "target_logical_service": "payment",
                    "maximum_forward_steps": 1,
                },
                "ttl_seconds": 600,
            },
        )
        save(
            self.root / "approval-authority.json",
            {
                "goal_sha256": GOAL,
                "approval_sha256": approval["approval_sha256"],
                "candidate_sha256": candidate["candidate_sha256"],
                "codex_autonomous_self_approval": False,
                "authority": "User activation of the saved minimal Payment Goal, sections 0 and 9",
            },
        )
        return candidate, approval

    def attempt(
        self,
        candidate: Any,
        approval: Any,
        binding: TrustedStateBindingV1,
        port: int,
        token: str,
    ) -> tuple[RemediationAttemptRepositoryV1, Any]:
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + token},
            timeout=30,
            trust_env=False,
        )

        class State:
            def read_current(self) -> StateObservationV1:
                response = client.get("/state")
                response.raise_for_status()
                return StateObservationV1.model_validate(response.json())

        store = SqliteStoreV1(self.root / "data/product.sqlite3")
        objects = ContentAddressedObjectStoreV1(
            self.root / "data/objects", metadata_store=store
        )
        repo = RemediationAttemptRepositoryV1(
            RemediationRepositoryV1(store, objects), provider=State(), binding=binding
        )
        attempt = repo.create(
            candidate["candidate_id"],
            AttemptRequestV1(approval_id=approval["approval_id"]),
            "minimal-single-attempt",
        )
        save(self.root / "authorized-attempt.json", attempt.model_dump(mode="json"))
        return repo, attempt
