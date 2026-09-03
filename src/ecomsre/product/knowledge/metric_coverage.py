"""Incident-local observability of the optional queue-aware Metrics action set.

This does not claim endpoint-wide target completeness. The three currently
registered generic metric symptoms are observable only from the exact selected
core bundles plus the optional queue action, with sufficient bound samples.
"""

from datetime import timedelta
import json
import math

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22, build_action_catalog_v22, build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22, MetricFactV22, ReadSourceStatusV22, semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.connectors.base import ConnectorQueryResultV1, ConnectorWindowV1
from ecomsre.product.contracts import ConnectorKindV1, EnvironmentRecordV1
from ecomsre.product.incidents.contracts import EvidenceBundleV1, IncidentRecordV1
from ecomsre.product.incidents.queue_action import build_queue_lag_action_v030


def complete_queue_aware_metrics_v1(
    *, incident: IncidentRecordV1, evidence: EvidenceBundleV1,
    environment: EnvironmentRecordV1 | None, baseline: EnvironmentBaselineV1 | None,
) -> tuple[ReadOutcomeV22, ...]:
    if environment is None or baseline is None or (
        environment.environment_id != incident.environment_id
        or baseline.environment_id != incident.environment_id
        or baseline.baseline_id != incident.baseline_id
        or baseline.baseline_sha256 != incident.baseline_sha256
        or "fraud-detection" not in incident.candidate_logical_services
        or not any(
            config.kind is ConnectorKindV1.PROMETHEUS
            and "queue_lag" in config.settings.get("query_templates", {})
            for config in environment.connector_configs
        )
    ):
        return ()
    candidates = incident.candidate_logical_services
    catalog = build_action_catalog_v22(
        candidate_services=candidates,
        topology=StaticTopologyV22.build(services=candidates, edges=()),
        capability_registry=build_tool_capability_registry_v22(),
        executed_action_ids=(), remaining_budget=100.0,
    )
    expected = {
        action.action_id: action for action in (
            *(a for a in catalog.registry_actions
              if a.source is EvidenceSourceV22.METRICS and len(a.target_services) == 1),
            build_queue_lag_action_v030(),
        )
    }
    stats = {(s.service, s.metric_kind): s for s in baseline.v22_baseline_profile.metric_stats}
    seen: dict[str, ReadOutcomeV22] = {}
    seen_digests: dict[str, str] = {}
    try:
        for obj in evidence.objects:
            if obj.source is not EvidenceSourceV22.METRICS:
                continue
            payload = obj.payload
            action = expected.get(obj.action_id or "")
            if (action is None
                or payload.get("incident_id") != incident.incident_id
                or semantic_sha256_v22(payload) != obj.object_sha256
                or payload.get("action") != action.model_dump(mode="json")
                or payload.get("memory_outcome") != payload.get("read_outcome")):
                return ()
            if action.action_id in seen_digests:
                if seen_digests[action.action_id] != obj.object_sha256:
                    return ()
                # A persisted action snapshot is linked once per Evidence ref.
                continue
            result = ConnectorQueryResultV1.model_validate_json(json.dumps(payload["connector_result"]))
            outcome = ReadOutcomeV22.model_validate_json(json.dumps(payload["read_outcome"]))
            window = ConnectorWindowV1(
                started_at=incident.diagnosis_observed_at - timedelta(seconds=action.request.lookback_seconds or 60),
                ended_at=incident.diagnosis_observed_at,
            )
            if (result.source is not EvidenceSourceV22.METRICS
                or result.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
                or result.truncated or result.window != window
                or result.requested_services != action.target_services
                or set(result.covered_services) != set(action.target_services)
                or outcome.action_id != action.action_id
                or outcome.request_sha256 != action.request_sha256
                or outcome.source != result.source or outcome.status != result.status
                or outcome.truncated or outcome.records != result.records):
                return ()
            keys = {(s, k) for s in action.target_services for k in action.request.metric_kinds}
            if len(outcome.records) != len(keys):
                return ()
            for record in outcome.records:
                if not isinstance(record, MetricFactV22):
                    return ()
                key = (record.service, record.metric_kind)
                stat = stats.get(key)
                if (key not in keys or stat is None or record.sample_count < 3
                    or record.value is None or not math.isfinite(record.value)
                    or not math.isfinite(stat.mean) or not math.isfinite(stat.standard_deviation)
                    or record.window_started_at != window.started_at
                    or record.window_ended_at != window.ended_at):
                    return ()
                keys.remove(key)
            seen[action.action_id] = outcome
            seen_digests[action.action_id] = obj.object_sha256
    except (KeyError, TypeError, ValueError):
        return ()
    return tuple(seen[key] for key in sorted(seen)) if seen.keys() == expected.keys() else ()
