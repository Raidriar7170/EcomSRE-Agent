"""Read-only Product acceptance flow; no remediation API or Provider operation."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.contracts import EnvironmentCreateV1
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.incidents.contracts import DiagnosisResultV1, IncidentRecordV1
from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    build_product_v030_environment_payload,
    CANDIDATES_V030,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.remediation.migrations import TABLES, V2_TABLES, V3_TABLES
from .common import REPO, GOAL_SHA, digest, sha, require, seal
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisEvidenceIndexV0232,
    DiagnosisDecisionTraceV0232,
)
from .http import LocalHTTP


def readonly_db(data: Path) -> sqlite3.Connection:
    database = data / "product.sqlite3"
    require(database.is_file() and not database.is_symlink(), "PRODUCT_DB_UNBOUND")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def formal_zero(data: Path) -> dict[str, int]:
    # Registry initialization is static configuration, not an action. Every other
    # remediation row, including candidate projection and idempotency, must be absent.
    names = sorted(
        (set(TABLES) | set(V2_TABLES) | set(V3_TABLES))
        - {"remediation_registry_versions"}
    )
    with readonly_db(data) as db:
        counts = {
            name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
            for name in names
        }
    require(not any(counts.values()), "FORMAL_ACTION_COUNTER_NONZERO")
    return counts


def validate_capabilities(value: dict[str, Any]) -> None:
    EnvironmentCapabilityMatrixV1.model_validate(value)
    sources = {item["source"]: item for item in value["sources"]}
    for source in ("METRICS", "RESOURCES", "TRACES", "LOGS", "RUNTIME"):
        require(source in sources, "CAPABILITY_MISSING:" + source)
        item = sources[source]
        require(
            item["status"] == "AVAILABLE"
            and item["target_complete_coverage"] is True
            and set(CANDIDATES_V030) <= set(item["covered_services"])
            and bool(item["observable_predicates"]),
            "CAPABILITY_INCOMPLETE:" + source,
        )
    require(value["no_incident_eligible"] is True, "NO_INCIDENT_NOT_ELIGIBLE")


def validate_diagnosis(value: dict[str, Any]) -> None:
    DiagnosisResultV1.model_validate(value)
    require(
        value["terminal"] == value["core_or_extension_or_open_world"] == "NO_INCIDENT"
        and value["root_service_ids"] == []
        and value["mechanism"] is None
        and value["capability_limitations"] == []
        and value["action_authority"] == "NONE"
        and value["agent_writes"]
        == value["runbook_executions"]
        == value["provider_calls"]
        == 0,
        "NOFAULT_DIAGNOSIS_NOT_PASS",
    )


def resolve_objects(
    data: Path, evidence: dict[str, Any], diagnosis: dict[str, Any]
) -> dict[str, Any]:
    objects = evidence["objects"]
    refs = {}
    with readonly_db(data) as db:
        for item in objects:
            checksum = item["object_sha256"]
            require(
                len(checksum) == 64 and all(c in "0123456789abcdef" for c in checksum),
                "CAS_DIGEST_INVALID",
            )
            path = data / "objects/sha256" / checksum[:2] / (checksum + ".json")
            require(path.is_file() and not path.is_symlink(), "CAS_OBJECT_MISSING")
            raw = path.read_bytes()
            require(
                sha(raw) == checksum and json.loads(raw) == item["payload"],
                "CAS_CONTENT_DRIFT",
            )
            metadata = db.execute(
                "SELECT byte_size FROM evidence_objects WHERE object_sha256=?",
                (checksum,),
            ).fetchone()
            require(
                metadata is not None and metadata[0] == len(raw), "CAS_METADATA_DRIFT"
            )
            require(item["evidence_ref"] not in refs, "CAS_DUPLICATE_REFERENCE")
            refs[item["evidence_ref"]] = checksum
    require(
        set(diagnosis["supporting_evidence_refs"]) <= set(refs)
        and set(diagnosis["contradicting_evidence_refs"]) <= set(refs),
        "CAS_UNRESOLVED_REFERENCE",
    )
    return {
        "reference_to_sha256": refs,
        "all_supporting_refs_resolved": True,
        "count": len(refs),
    }


def resolve_trace(
    data: Path, index: dict[str, Any], diagnosis: dict[str, Any], refs: dict[str, str]
) -> dict[str, Any]:
    DiagnosisEvidenceIndexV0232.model_validate(index)
    require(
        index["incident_id"] == diagnosis["incident_id"]
        and index["diagnosis_id"] == diagnosis["diagnosis_id"]
        and index["all_object_sha256_by_ref"] == refs
        and not index["failed_source_refs"]
        and not index["capability_limitation_bindings"],
        "EVIDENCE_INDEX_DRIFT",
    )
    with readonly_db(data) as db:
        rows = db.execute(
            "SELECT object_sha256, byte_size FROM evidence_objects LIMIT 8193"
        ).fetchall()
    require(len(rows) <= 8192, "CAS_INDEX_BOUNDS")
    found = []
    total = 0
    for checksum, size in rows:
        require(size <= 10_000_000, "CAS_OBJECT_BOUNDS")
        path = data / "objects/sha256" / checksum[:2] / (checksum + ".json")
        require(not path.is_symlink(), "CAS_SYMLINK")
        raw = path.read_bytes()
        total += len(raw)
        require(
            len(raw) == size and sha(raw) == checksum and total <= 128_000_000,
            "CAS_CONTENT_BOUNDS_OR_DRIFT",
        )
        payload = json.loads(raw)
        if (
            isinstance(payload, dict)
            and payload.get("schema_version")
            == "ecomsre.product.diagnosis-decision-trace.v0232"
            and payload.get("diagnosis_id") == diagnosis["diagnosis_id"]
        ):
            trace = DiagnosisDecisionTraceV0232.model_validate(payload)
            require(
                trace.trace_sha256 == index["decision_trace_sha256"]
                and trace.no_incident_admissible
                and trace.required_coverage_satisfied
                and not trace.failed_sources,
                "DECISION_TRACE_NOT_HEALTHY",
            )
            found.append({"object_sha256": checksum, "payload": payload})
    require(len(found) == 1, "DECISION_TRACE_CARDINALITY")
    return found[0]


class ProductFlow:
    def __init__(
        self,
        http: LocalHTTP,
        data: Path,
        attempt: str,
        binding: dict[str, Any],
        plan: dict[str, Any],
        observe: Callable[[], dict[str, Any]],
    ) -> None:
        self.http, self.data, self.attempt = http, data, attempt
        self.binding, self.plan, self.observe = binding, plan, observe
        self.environment_id: str | None = None
        self.snapshot_number = 0
        self.authority: PilotRuntimeAuthorityV02 | None = None

    def authority_for(self, environment_id: str) -> PilotRuntimeAuthorityV02:
        return PilotRuntimeAuthorityV02.build(
            environment_id=environment_id,
            allowed_logical_services=CANDIDATES_V030,
            profile_sha256=GOAL_SHA,
            daemon_identity_sha256=digest(self.binding["daemon"]),
            docker_context_sha256=digest(self.binding["context"]),
            config_bundle_sha256=digest(self.plan),
            resolved_sandbox_sha256=digest(self.plan["roles"]),
            resolved_endpoints_sha256=digest(
                {
                    "prometheus": "http://prometheus:9090",
                    "jaeger": "http://jaeger:16686/jaeger/ui",
                    "opensearch": "http://opensearch:9200",
                }
            ),
            ownership_scope_sha256=digest({"attempt": self.attempt, "plan": self.plan}),
        )

    def snapshot(self) -> None:
        require(
            self.environment_id is not None and self.authority is not None,
            "RUNTIME_AUTHORITY_UNBOUND",
        )
        assert self.environment_id is not None and self.authority is not None
        observation = self.observe()
        services = {}
        for name in CANDIDATES_V030:
            row = observation[name]
            require(
                row["ready"] is True
                and row["running"] is True
                and row["restart_count"] == 0,
                "RUNTIME_ROLE_UNHEALTHY:" + name,
            )
            services[name] = {
                "state": "RUNNING",
                "healthy": True,
                "restart_count": row["restart_count"],
            }
        snapshot = PilotRuntimeSnapshotV02.build(
            environment_id=self.environment_id,
            authority_sha256=self.authority.connector_binding_sha256,
            observed_at=datetime.now(UTC),
            services=services,
        )
        key = f"runtime-{self.snapshot_number:04d}"
        seal(self.http.root, key + "-observation.json", observation)
        payload = snapshot.model_dump(mode="json")
        seal(self.http.root, key + "-snapshot.json", payload)
        pilot = self.data / "pilot"
        pilot.mkdir(mode=0o700, exist_ok=True)
        seal(pilot, key + ".json", payload)
        # Atomic current pointer replacement; every version remains append-only.
        seal(pilot, key + "-current.json", payload)
        os.replace(pilot / (key + "-current.json"), pilot / "runtime-readiness.json")
        self.snapshot_number += 1

    def job(self, name: str, path: str, payload: Any = None) -> dict[str, Any]:
        self.snapshot()
        created = self.http.json(name + "-create", "POST", path, payload)
        identifier = created["job_id"]
        started = time.monotonic()
        for ordinal in range(120):
            require(time.monotonic() - started < 600, "PRODUCT_JOB_DEADLINE:" + name)
            record = self.http.json(
                f"{name}-poll-{ordinal:03d}", "GET", "/v1/jobs/" + identifier
            )
            if record["status"] == "SUCCEEDED":
                return record
            require(
                record["status"] not in ("FAILED", "CANCELLED"),
                "PRODUCT_JOB_FAILED:" + name,
            )
            if ordinal % 6 == 0:
                self.snapshot()
            time.sleep(5)
        raise RuntimeError("PRODUCT_JOB_DEADLINE:" + name)

    def run(self) -> dict[str, Any]:
        provisional = self.authority_for("env-" + "0" * 24)
        payload = build_product_v030_environment_payload(
            repository_root=REPO,
            runtime_authority_sha256=provisional.connector_binding_sha256,
        )
        payload["name"] = "product-v040-preflight-v4-" + self.attempt
        payload["description"] = (
            "Owned-local engineering preflight; no fault, remediation or Provider authority."
        )
        endpoints = {
            "PROMETHEUS": "http://prometheus:9090",
            "OPENSEARCH": "http://opensearch:9200",
            "JAEGER": "http://jaeger:16686/jaeger/ui",
        }
        for connector in payload["connector_configs"]:
            if connector["kind"] in endpoints:
                connector["endpoint"] = endpoints[connector["kind"]]
        EnvironmentCreateV1.model_validate(payload)
        environment = self.http.json(
            "environment-create", "POST", "/v1/environments", payload
        )
        self.environment_id = environment["environment_id"]
        self.authority = self.authority_for(self.environment_id)
        require(
            self.authority.connector_binding_sha256
            == provisional.connector_binding_sha256,
            "RUNTIME_CONNECTOR_BINDING_DRIFT",
        )
        write_pilot_runtime_authority_v02(
            self.data / "runtime-authority.json", self.authority
        )
        prefix = "/v1/environments/" + self.environment_id
        verification = self.job("connector-verify", prefix + "/verify-jobs")
        capabilities = self.http.json("capabilities", "GET", prefix + "/capabilities")
        validate_capabilities(capabilities)
        policy = {
            "mode": "DEMO_ONLY",
            "lookback_seconds": 180,
            "window_count": 5,
            "minimum_successful_windows": 5,
            "warmup_seconds": 180,
        }
        baseline_job = self.job(
            "baseline-build",
            prefix + "/baseline-jobs",
            {
                "build_policy": policy,
                "candidate_services": list(CANDIDATES_V030),
                "activate": True,
            },
        )
        versions = self.http.json("baseline-versions", "GET", prefix + "/baselines")[
            "items"
        ]
        require(
            len(versions) == 1 and versions[0]["active"] is True,
            "BASELINE_ACTIVE_CARDINALITY",
        )
        baseline = versions[0]
        EnvironmentBaselineV1.model_validate(baseline)
        require(
            baseline["successful_windows"] == baseline["window_count"] == 5
            and baseline["build_policy"]["mode"] == "DEMO_ONLY",
            "BASELINE_NOT_READY",
        )
        audit = self.http.json(
            "baseline-window-audit",
            "GET",
            "/v1/baselines/" + baseline["baseline_id"] + "/window-audit-v023",
        )
        with readonly_db(self.data) as db:
            service_ids = [
                row[0]
                for row in db.execute(
                    "SELECT service_id FROM services WHERE environment_id=? ORDER BY service_id",
                    (self.environment_id,),
                )
            ]
        require(len(service_ids) == 4, "PRODUCT_SERVICE_IDENTITY_CARDINALITY")
        self.snapshot()
        ended = datetime.now(UTC)
        incident = self.http.json(
            "incident-create",
            "POST",
            "/v1/incidents",
            {
                "environment_id": self.environment_id,
                "external_incident_key": "preflight-v4-" + self.attempt,
                "alert_name": "No-fault engineering control",
                "summary": "Healthy checkout control after five-window DEMO_ONLY baseline.",
                "started_at": (ended - timedelta(seconds=60)).isoformat(),
                "ended_at": ended.isoformat(),
                "candidate_service_ids": service_ids,
                "labels": {"fault": "none", "preflight": "v4"},
            },
        )
        IncidentRecordV1.model_validate(incident)
        require(
            incident["baseline_id"] == baseline["baseline_id"]
            and incident["baseline_sha256"] == baseline["baseline_sha256"],
            "INCIDENT_BASELINE_BINDING",
        )
        route = "/v1/incidents/" + incident["incident_id"]
        diagnosis_job = self.job("diagnosis", route + "/diagnosis-jobs")
        diagnosis = self.http.json("diagnosis-result", "GET", route + "/diagnosis")
        validate_diagnosis(diagnosis)
        evidence = self.http.json("evidence-bundle", "GET", route + "/evidence")
        index = self.http.json("evidence-index", "GET", route + "/evidence-index")
        resolved = resolve_objects(self.data, evidence, diagnosis)
        trace = resolve_trace(
            self.data, index, diagnosis, resolved["reference_to_sha256"]
        )
        counts = formal_zero(self.data)
        result = {
            "environment": environment,
            "verification_job": verification,
            "capabilities": capabilities,
            "baseline_job": baseline_job,
            "baseline": baseline,
            "baseline_window_audit": audit,
            "incident": incident,
            "diagnosis_job": diagnosis_job,
            "diagnosis": diagnosis,
            "evidence_index": index,
            "cas_resolution": resolved,
            "decision_trace": trace,
            "formal_counts": counts,
            "provider_calls": 0,
        }
        seal(self.http.root, "product-result.json", result)
        return result
