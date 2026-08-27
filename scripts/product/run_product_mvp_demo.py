#!/usr/bin/env python3
"""Run the deterministic Product MVP knowledge-loop checkpoint."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from typing import Any, ContextManager

from fastapi.testclient import TestClient

from ecomsre.product.app import create_app
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.knowledge.repository import KnowledgeRepositoryV1
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


OBSERVATION_EPOCH = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)
TERMINAL = "ECOMSRE_PRODUCT_MVP_V01_KNOWLEDGE_LOOP_PASS"


def _require(response: Any, status_code: int) -> dict[str, Any]:
    if response.status_code != status_code:
        raise RuntimeError(
            f"Product API returned {response.status_code}: {response.text}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Product API returned a non-object payload")
    return payload


def _run_job(
    client: TestClient,
    settings: ProductSettingsV1,
    job: dict[str, Any],
    worker_id: str,
) -> dict[str, Any]:
    if not run_one_job(settings, worker_id=worker_id):
        raise RuntimeError("Product worker did not claim the expected job")
    record = _require(client.get(f"/v1/jobs/{job['job_id']}"), 200)
    if record["status"] != "SUCCEEDED":
        raise RuntimeError(
            f"Product job failed closed: {record.get('safe_error_code')}"
        )
    return record


def _diagnose_slot(
    client: TestClient,
    settings: ProductSettingsV1,
    *,
    environment_id: str,
    service_id: str,
    slot: int,
) -> dict[str, Any]:
    observed_at = OBSERVATION_EPOCH + timedelta(minutes=slot)
    incident = _require(
        client.post(
            "/v1/incidents",
            json={
                "environment_id": environment_id,
                "external_incident_key": f"product-mvp-slot-{slot}",
                "alert_name": "payment-observation",
                "summary": "A bounded opaque fixture observation requires diagnosis.",
                "started_at": (observed_at - timedelta(minutes=1)).isoformat(),
                "ended_at": observed_at.isoformat(),
                "candidate_service_ids": [service_id],
                "labels": {"source": "product-mvp-demo"},
            },
        ),
        201,
    )
    job = _require(
        client.post(f"/v1/incidents/{incident['incident_id']}/diagnosis-jobs"),
        202,
    )
    _run_job(client, settings, job, f"demo-diagnosis-{slot}")
    return _require(
        client.get(f"/v1/incidents/{incident['incident_id']}/diagnosis"),
        200,
    )


def run_demo(data_root: Path) -> None:
    settings = ProductSettingsV1(
        data_root=data_root,
        sqlite_path=data_root / "product.sqlite3",
        object_store_root=data_root / "objects",
    )
    with TestClient(create_app(settings)) as client:
        environment = _require(
            client.post(
                "/v1/environments",
                json={
                    "name": "product-mvp-knowledge-loop",
                    "description": "One deterministic time-indexed fixture environment.",
                    "timezone": "UTC",
                    "service_identity_policy": {
                        "services": [{"logical_service": "payment"}]
                    },
                    "connector_configs": [
                        {
                            "name": "fixture",
                            "kind": "FIXTURE",
                            "settings": {"dataset": "product-knowledge-loop"},
                            "credential_refs": {},
                        }
                    ],
                    "explicit_service_catalog": ["payment"],
                },
            ),
            201,
        )
        environment_id = str(environment["environment_id"])
        verify = _require(
            client.post(f"/v1/environments/{environment_id}/verify-jobs"), 202
        )
        verified = _run_job(client, settings, verify, "demo-verify")
        service_id = str(
            verified["result"]["service_identity_map"]["services"][0][
                "service_id"
            ]
        )
        baseline = _require(
            client.post(
                f"/v1/environments/{environment_id}/baseline-jobs",
                json={"activate": True},
            ),
            202,
        )
        _run_job(client, settings, baseline, "demo-baseline")

        core = _diagnose_slot(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            slot=3,
        )
        if core["terminal"] != "CORE_KNOWN":
            raise RuntimeError("deterministic Core Known control did not terminate")
        positives = tuple(
            _diagnose_slot(
                client,
                settings,
                environment_id=environment_id,
                service_id=service_id,
                slot=slot,
            )
            for slot in (0, 1, 2)
        )
        if {item["terminal"] for item in positives} != {"OPEN_WORLD"}:
            raise RuntimeError("three OpenWorld positives were not produced")
        controls = tuple(
            _diagnose_slot(
                client,
                settings,
                environment_id=environment_id,
                service_id=service_id,
                slot=slot,
            )
            for slot in (4, 5)
        )
        if {item["terminal"] for item in controls} != {"NO_INCIDENT"}:
            raise RuntimeError("same-environment NoIncident controls were not produced")

        families = _require(
            client.get(f"/v1/environments/{environment_id}/fault-families"), 200
        )["items"]
        if len(families) != 1 or families[0]["status"] != "REVIEW_READY":
            raise RuntimeError("the three fingerprints did not form one review-ready family")
        family_id = str(families[0]["family_id"])
        review = _require(
            client.post(
                f"/v1/fault-families/{family_id}/reviews",
                json={
                    "decision": "ACCEPT_AS_NEW",
                    "reviewer": "TEST_REVIEWER",
                    "note": "SIMULATED HUMAN REVIEW: accept the bounded recurring family.",
                    "reviewed_at": (
                        OBSERVATION_EPOCH + timedelta(minutes=10)
                    ).isoformat(),
                },
            ),
            201,
        )
        draft = _require(
            client.post(
                f"/v1/fault-families/{family_id}/registration-drafts",
                json={
                    "human_review_id": review["review_id"],
                    "human_canonical_label": "Opaque Mutex Convoy",
                    "llm_explanation": (
                        "SIMULATED LLM ADVISORY: the Runtime-mined clause requires "
                        "the opaque log anomaly and healthy target runtime."
                    ),
                    "unresolved_gaps": [],
                },
            ),
            201,
        )
        if draft["implementation_mode"] != "DECLARATIVE_READY":
            raise RuntimeError(f"registration drafting ended {draft['implementation_mode']}")
        registration_id = str(draft["registration_id"])
        shadow = _require(
            client.post(
                f"/v1/registrations/{registration_id}/shadow-evaluation-jobs",
                json={},
            ),
            201,
        )
        if shadow["gate_passed"] is not True:
            raise RuntimeError(f"shadow gate failed: {shadow['reason_codes']}")
        required_strata = {
            "POSITIVE_INCIDENT",
            "CONFUSABLE_CORE_KNOWN",
            "OTHER_EXTENSION",
            "NO_INCIDENT",
            "INSUFFICIENT_OR_CONFLICT",
            "TARGET_COUNTERFACTUAL",
            "SOURCE_FAILURE",
        }
        if {item["stratum"] for item in shadow["outcomes"]} != required_strata:
            raise RuntimeError("shadow evaluation did not bind all required strata")
        if any(
            item["matched"] is not False
            for item in shadow["outcomes"]
            if item["stratum"] == "SOURCE_FAILURE"
        ):
            raise RuntimeError("shadow source-failure behavior did not fail closed")
        if any(
            item["evaluated_target_services"] != ["counterfactual-target"]
            for item in shadow["outcomes"]
            if item["stratum"] == "TARGET_COUNTERFACTUAL"
        ):
            raise RuntimeError("shadow target counterfactual did not move the target")
        _require(
            client.post(
                f"/v1/registrations/{registration_id}/promotions",
                json={
                    "shadow_evaluation_id": shadow["evaluation_id"],
                    "reviewer": "TEST_REVIEWER",
                    "note": "SIMULATED HUMAN REVIEW: promote the passing shadow registration.",
                    "promoted_at": (
                        OBSERVATION_EPOCH + timedelta(minutes=11)
                    ).isoformat(),
                },
            ),
            201,
        )
        recurrence = _diagnose_slot(
            client,
            settings,
            environment_id=environment_id,
            service_id=service_id,
            slot=6,
        )
        if recurrence["terminal"] != "EXTENSION_KNOWN":
            raise RuntimeError("the disjoint recurrence did not use the active extension")
        if any(
            recurrence[key] != 0
            for key in ("provider_calls", "agent_writes", "runbook_executions")
        ):
            raise RuntimeError("the recurrence crossed a protected action boundary")

    with TestClient(create_app(settings)) as restarted:
        persisted = _require(
            restarted.get(f"/v1/environments/{environment_id}/fault-families"), 200
        )["items"]
        if len(persisted) != 1 or persisted[0]["status"] != "PROMOTED":
            raise RuntimeError("fault-family state did not survive Product restart")
        _require(restarted.get(f"/v1/registrations/{registration_id}"), 200)
    store = SqliteStoreV1(settings.sqlite_path)
    knowledge = KnowledgeRepositoryV1(
        store,
        ContentAddressedObjectStoreV1(
            settings.object_store_root,
            metadata_store=store,
        ),
    )
    if len(knowledge.active_extensions(environment_id)) != 1:
        raise RuntimeError("ACTIVE environment registry did not survive Product restart")


def _root_context(path: Path | None) -> ContextManager[str | Path]:
    if path is None:
        return tempfile.TemporaryDirectory(prefix="ecomsre-product-mvp-")
    path.mkdir(parents=True, exist_ok=True)
    return nullcontext(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path)
    arguments = parser.parse_args()
    with _root_context(arguments.data_root) as root:
        run_demo(Path(root))
    print(TERMINAL)


if __name__ == "__main__":
    main()
