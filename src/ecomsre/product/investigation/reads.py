"""Finite runtime-owned query templates over existing Product connectors."""

import json
from datetime import timedelta
from typing import Any

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorWindowV1, ConnectorQueryResultV1
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030
from ecomsre.product.incidents.contracts import IncidentRecordV1, EvidenceBundleV1
from ecomsre.product.contracts import EnvironmentRecordV1, ServiceIdentityMapV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1


# Omit free-form messages, operations, labels, paths and controller metadata.
# Typed numeric/state data suffice for the first vertical investigation slice.
_FIELDS = frozenset(
    {
        "service",
        "metric_kind",
        "support_status",
        "sample_count",
        "value",
        "unit",
        "window_started_at",
        "window_ended_at",
        "observed_at",
        "severity",
        "status",
        "duration_ms",
        "first_error_location",
        "parent_service",
        "service_path",
        "state",
        "healthy",
        "restart_count",
        "sampling_window_seconds",
        "samples",
        "memory_slope_bytes_per_second",
        "offset_ms",
        "cpu_percent",
        "memory_bytes",
    }
)


def project_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ([project_record(x) for x in value] if key == "samples" else value)
        for key, value in record.items()
        if key in _FIELDS
    }


class InvestigationReads:
    def __init__(
        self,
        *,
        incident: IncidentRecordV1,
        environment: EnvironmentRecordV1,
        identities: ServiceIdentityMapV1,
        capabilities: EnvironmentCapabilityMatrixV1,
        backend: ProductReadBackendV1,
        objects: ContentAddressedObjectStoreV1,
        initial_evidence: EvidenceBundleV1 | None = None,
    ) -> None:
        self.incident, self.environment = incident, environment
        self.identities, self.backend, self.objects = identities, backend, objects
        self.entries: dict[str, tuple[Any, ConnectorWindowV1]] = {}
        self.initial_observations: list[dict[str, Any]] = []
        already_read = set()
        if initial_evidence is not None:
            seen = set()
            for item in initial_evidence.objects:
                raw = item.payload.get("connector_result")
                action_payload = item.payload.get("action")
                if raw is None or action_payload is None:
                    continue
                result = ConnectorQueryResultV1.model_validate_json(json.dumps(raw))
                # Deduplicate snapshots carried by multiple record-level references.
                if result.result_sha256 in seen:
                    continue
                seen.add(result.result_sha256)
                already_read.add(
                    (action_payload["request_sha256"], result.window.model_dump_json())
                )
                self.initial_observations.append(
                    {
                        "evidence_ref": item.evidence_ref,
                        "object_sha256": item.object_sha256,
                        "source": result.source.value,
                        "targets": list(result.requested_services),
                        "window": result.window.model_dump(mode="json"),
                        "status": result.status.value,
                        "covered_services": list(result.covered_services),
                        "truncated": result.truncated or len(result.records) > 40,
                        "records": [
                            project_record(r.model_dump(mode="json"))
                            for r in result.records[:40]
                        ],
                        "cached": True,
                    }
                )
        catalog = build_action_catalog_v22(
            candidate_services=incident.candidate_logical_services,
            topology=StaticTopologyV22.build(
                services=incident.candidate_logical_services, edges=()
            ),
            capability_registry=build_tool_capability_registry_v22(),
            executed_action_ids=(),
            remaining_budget=100.0,
        )
        actions = list(catalog.registry_actions)
        if "fraud-detection" in incident.candidate_logical_services and any(
            c.kind.value == "PROMETHEUS"
            and "queue_lag" in c.settings.get("query_templates", {})
            for c in environment.connector_configs
        ):
            actions.append(build_queue_lag_action_v030())
        # Historical, bounded windows. No runtime refresh or arbitrary future window.
        end = incident.diagnosis_observed_at
        for action in actions:
            if len(action.target_services) != 1 or action.source not in {
                EvidenceSourceV22.METRICS,
                EvidenceSourceV22.LOGS,
                EvidenceSourceV22.TRACES,
                EvidenceSourceV22.RESOURCES,
            }:
                continue
            available = any(
                c.source == action.source
                and c.status.value != "UNAVAILABLE"
                and set(action.target_services).issubset(c.covered_services)
                for c in capabilities.sources
            )
            if not available:
                continue
            for offset in (0, 30):
                window = ConnectorWindowV1(
                    started_at=end - timedelta(seconds=offset + 30),
                    ended_at=end - timedelta(seconds=offset),
                )
                if (action.request_sha256, window.model_dump_json()) in already_read:
                    continue
                identity = semantic_sha256_v22(
                    {
                        "request": action.request_sha256,
                        "window": window.model_dump(mode="json"),
                        "capability": capabilities.capability_sha256,
                    }
                )
                self.entries["read-" + identity[:24]] = (action, window)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": key,
                "source": action.source.value,
                "targets": list(action.target_services),
                "template": action.request.model_dump(
                    mode="json", exclude={"request_sha256"}
                ),
                "window": window.model_dump(mode="json"),
            }
            for key, (action, window) in sorted(self.entries.items())
        ]

    def read(self, action_id: str) -> dict[str, Any]:
        action, window = self.entries[action_id]
        result, _pilot, _parts = self.backend._execute(
            action=action,
            incident=self.incident,
            environment=self.environment,
            identity_by_logical={
                s.logical_service: s for s in self.identities.services
            },
            window=window,
        )
        # Persist the typed observation; only its allowlisted projection goes to model.
        stored = self.objects.put_json(result.model_dump(mode="json"))
        return {
            "evidence_ref": "investigation:" + stored.object_sha256,
            "object_sha256": stored.object_sha256,
            "source": result.source.value,
            "targets": list(action.target_services),
            "window": window.model_dump(mode="json"),
            "status": result.status.value,
            "truncated": result.truncated,
            "covered_services": list(result.covered_services),
            "records": [
                project_record(r.model_dump(mode="json")) for r in result.records
            ],
            "cached": False,
        }
