from __future__ import annotations

from datetime import datetime, timezone
import inspect
import math

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.action_catalog import (
    ActionMaskReasonV22,
    ActionCoverageV22,
    StaticTopologyV22,
    ToolCapabilityV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
    build_tool_capability_registry_v22,
    resolve_canonical_request_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    EvidenceSourceV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    SpanStatusV22,
    TraceSpanV22,
    build_canonical_read_request_v22,
    RecentChangeRecordV22,
    RolloutStateV22,
    semantic_sha256_v22,
)


TOPOLOGY = StaticTopologyV22.build(
    services=("ad", "checkout", "payment", "shipping"),
    edges=(
        ("checkout", "payment"),
        ("checkout", "shipping"),
    ),
)
REGISTRY = build_default_tool_capability_registry_v22()


def _catalog(
    *,
    executed_action_ids: tuple[str, ...] = (),
    remaining_budget: float = 20.0,
):
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=TOPOLOGY,
        capability_registry=REGISTRY,
        executed_action_ids=executed_action_ids,
        remaining_budget=remaining_budget,
    )


def _action_id(source: EvidenceSourceV22, targets: tuple[str, ...]) -> str:
    action = next(
        item
        for item in _catalog().actions
        if item.source is source and item.target_services == targets
    )
    return action.action_id


def test_catalog_and_resolver_accept_no_fixture_or_query_parameters() -> None:
    builder_parameters = inspect.signature(build_action_catalog_v22).parameters
    resolver_parameters = inspect.signature(resolve_canonical_request_v22).parameters

    forbidden = {
        "truth",
        "fixture",
        "expected_mechanism",
        "expected_source",
        "fault_controller",
        "metric_kinds",
        "max_results",
        "max_records",
        "max_spans",
        "sampling_window_seconds",
        "sample_count",
    }
    assert forbidden.isdisjoint(builder_parameters)
    assert tuple(resolver_parameters) == ("catalog", "action_id")

    action_id = _action_id(EvidenceSourceV22.RESOURCES, ("payment",))
    with pytest.raises(TypeError):
        resolve_canonical_request_v22(  # type: ignore[call-arg]
            catalog=_catalog(),
            action_id=action_id,
            sample_count=99,
        )


def test_same_action_id_always_binds_the_same_canonical_request() -> None:
    first = _catalog()
    second = _catalog()

    assert first.catalog_sha256 == second.catalog_sha256
    assert {
        item.action_id: item.request.model_dump(mode="json")
        for item in first.actions
    } == {
        item.action_id: item.request.model_dump(mode="json")
        for item in second.actions
    }
    for action in first.actions:
        request = resolve_canonical_request_v22(
            catalog=first,
            action_id=action.action_id,
        )
        assert request.request_sha256 == action.request_sha256


def test_action_id_cannot_be_rebound_to_different_query_parameters() -> None:
    action = next(
        item for item in _catalog().registry_actions if item.action_id == "a:logs:payment"
    )
    changed_request = build_canonical_read_request_v22(
        source=EvidenceSourceV22.LOGS,
        target_services=("payment",),
        lookback_seconds=300,
        max_records=20,
    )
    payload = action.model_dump(mode="python", exclude={"action_sha256"})
    payload.update(
        request=changed_request,
        request_sha256=changed_request.request_sha256,
    )
    json_payload = {
        **action.model_dump(mode="json", exclude={"action_sha256"}),
        "request": changed_request.model_dump(mode="json"),
        "request_sha256": changed_request.request_sha256,
    }

    with pytest.raises(ValidationError, match="versioned canonical parameters"):
        type(action).model_validate(
            {
                **payload,
                "action_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_non_runtime_sources_reject_multiple_targets() -> None:
    with pytest.raises(ValidationError, match="exactly one target"):
        build_canonical_read_request_v22(
            source=EvidenceSourceV22.LOGS,
            target_services=("checkout", "payment"),
            lookback_seconds=300,
            max_records=12,
        )


def test_deserialized_catalog_cannot_make_an_executed_action_available() -> None:
    catalog = _catalog()
    action_id = "a:logs:payment"
    payload = catalog.model_dump(mode="python", exclude={"catalog_sha256"})
    payload["action_coverage"] = ActionCoverageV22.build(
        executed_action_ids=(action_id,),
        covered_capability_keys=(),
    )
    json_payload = catalog.model_dump(mode="json", exclude={"catalog_sha256"})
    json_payload["action_coverage"] = payload["action_coverage"].model_dump(
        mode="json"
    )

    with pytest.raises(ValidationError, match="executed action remains available"):
        type(catalog).model_validate(
            {
                **payload,
                "catalog_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_deserialized_catalog_cannot_omit_the_canonical_registry_surface() -> None:
    catalog = _catalog()
    payload = catalog.model_dump(mode="python", exclude={"catalog_sha256"})
    payload.update(registry_actions=(), actions=(), masked_actions=())
    json_payload = catalog.model_dump(mode="json", exclude={"catalog_sha256"})
    json_payload.update(registry_actions=[], actions=[], masked_actions=[])

    with pytest.raises(ValidationError, match="canonical registry surface"):
        type(catalog).model_validate(
            {
                **payload,
                "catalog_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_catalog_binds_enabled_sources_to_capability_registry_digest() -> None:
    registry = build_tool_capability_registry_v22(
        disabled_sources=(EvidenceSourceV22.LOGS,)
    )
    catalog = build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=TOPOLOGY,
        capability_registry=registry,
        executed_action_ids=(),
        remaining_budget=20.0,
    )
    payload = catalog.model_dump(mode="python", exclude={"catalog_sha256"})
    payload["capability_registry_sha256"] = REGISTRY.registry_sha256
    json_payload = catalog.model_dump(mode="json", exclude={"catalog_sha256"})
    json_payload["capability_registry_sha256"] = REGISTRY.registry_sha256

    with pytest.raises(ValidationError, match="capability registry binding"):
        type(catalog).model_validate(
            {
                **payload,
                "catalog_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_deserialized_catalog_requires_candidate_cardinality() -> None:
    catalog = _catalog()
    payload = catalog.model_dump(mode="python", exclude={"catalog_sha256"})
    payload["candidate_services"] = ()
    json_payload = catalog.model_dump(mode="json", exclude={"catalog_sha256"})
    json_payload["candidate_services"] = []

    with pytest.raises(ValidationError, match="one to four candidate services"):
        type(catalog).model_validate(
            {
                **payload,
                "catalog_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_action_coverage_has_a_closed_digest_bound_contract() -> None:
    coverage = ActionCoverageV22.build(
        executed_action_ids=("a:logs:payment",),
        covered_capability_keys=("logs:payment:read",),
    )
    payload = coverage.model_dump(mode="python")
    payload["covered_capability_keys"] = ("logs:checkout:read",)

    with pytest.raises(ValidationError, match="coverage digest"):
        ActionCoverageV22.model_validate(payload)


def test_deserialized_catalog_must_record_executed_coverage() -> None:
    action_id = "a:logs:payment"
    catalog = _catalog(executed_action_ids=(action_id,))
    payload = catalog.model_dump(mode="python", exclude={"catalog_sha256"})
    payload["action_coverage"] = ActionCoverageV22.build(
        executed_action_ids=(action_id,),
        covered_capability_keys=(),
    )
    json_payload = catalog.model_dump(mode="json", exclude={"catalog_sha256"})
    json_payload["action_coverage"] = payload["action_coverage"].model_dump(
        mode="json"
    )

    with pytest.raises(ValidationError, match="executed coverage"):
        type(catalog).model_validate(
            {
                **payload,
                "catalog_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_action_cost_cannot_drift_from_the_versioned_registry() -> None:
    action = next(
        item for item in _catalog().registry_actions if item.action_id == "a:logs:payment"
    )
    payload = action.model_dump(mode="python", exclude={"action_sha256"})
    payload["weighted_cost"] = 0.25
    json_payload = action.model_dump(mode="json", exclude={"action_sha256"})
    json_payload["weighted_cost"] = 0.25

    with pytest.raises(ValidationError, match="versioned weighted cost"):
        type(action).model_validate(
            {
                **payload,
                "action_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_action_coverage_cannot_drift_after_semantic_rehash() -> None:
    action = next(
        item for item in _catalog().registry_actions if item.action_id == "a:logs:payment"
    )
    payload = action.model_dump(mode="python", exclude={"action_sha256"})
    payload["coverage_keys"] = ("logs:checkout:read",)
    json_payload = action.model_dump(mode="json", exclude={"action_sha256"})
    json_payload["coverage_keys"] = ["logs:checkout:read"]

    with pytest.raises(ValidationError, match="canonical coverage"):
        type(action).model_validate(
            {
                **payload,
                "action_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_action_dominance_cannot_drift_after_semantic_rehash() -> None:
    action = next(
        item
        for item in _catalog().registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
        and item.target_services == ("checkout", "payment")
    )
    payload = action.model_dump(mode="python", exclude={"action_sha256"})
    payload["dominates_action_ids"] = ("a:logs:payment",)
    json_payload = action.model_dump(mode="json", exclude={"action_sha256"})
    json_payload["dominates_action_ids"] = ["a:logs:payment"]

    with pytest.raises(ValidationError, match="canonical dominance"):
        type(action).model_validate(
            {
                **payload,
                "action_sha256": semantic_sha256_v22(json_payload),
            }
        )


def test_catalog_is_truth_and_fixture_independent() -> None:
    serialized = _catalog().model_dump_json().casefold()

    for forbidden in (
        "ground_truth",
        "gold_label",
        "expected_mechanism",
        "expected_source",
        "expected_action",
        "fixture",
        "fault_controller",
        "injected_variant",
    ):
        assert forbidden not in serialized


def test_dynamic_mask_removes_executed_and_covered_actions() -> None:
    initial = _catalog()
    logs_id = _action_id(EvidenceSourceV22.LOGS, ("payment",))
    after_logs = _catalog(executed_action_ids=(logs_id,))

    assert logs_id not in {item.action_id for item in after_logs.actions}
    assert next(
        item for item in after_logs.masked_actions if item.action_id == logs_id
    ).reason is ActionMaskReasonV22.EXECUTED
    with pytest.raises(ValueError, match="not available"):
        resolve_canonical_request_v22(catalog=after_logs, action_id=logs_id)

    runtime_all = next(
        item
        for item in initial.registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
        and item.target_services == ("checkout", "payment")
    )
    after_runtime = _catalog(executed_action_ids=(runtime_all.action_id,))
    runtime_individuals = tuple(
        item
        for item in after_runtime.registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
        and len(item.target_services) == 1
    )

    assert runtime_individuals
    assert all(
        next(
            masked
            for masked in after_runtime.masked_actions
            if masked.action_id == item.action_id
        ).reason
        is ActionMaskReasonV22.COVERED
        for item in runtime_individuals
    )


def test_runtime_dominance_is_explicit_and_budget_sensitive() -> None:
    full = _catalog()
    runtime_individuals = {
        item.action_id
        for item in full.registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
        and len(item.target_services) == 1
    }
    dominated = {
        item.action_id
        for item in full.masked_actions
        if item.reason is ActionMaskReasonV22.DOMINATED
    }

    assert runtime_individuals <= dominated
    low_budget = _catalog(remaining_budget=0.5)
    assert any(
        item.source is EvidenceSourceV22.RUNTIME
        and len(item.target_services) == 1
        for item in low_budget.actions
    )
    assert any(
        item.reason is ActionMaskReasonV22.OVER_BUDGET
        for item in low_budget.masked_actions
    )


def test_explicit_coverage_and_disabled_sources_have_exact_mask_reasons() -> None:
    initial = _catalog()
    logs = next(item for item in initial.registry_actions if item.action_id == "a:logs:payment")
    covered = build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=TOPOLOGY,
        capability_registry=REGISTRY,
        executed_action_ids=(),
        remaining_budget=20.0,
        covered_capability_keys=logs.coverage_keys,
    )
    assert next(
        item for item in covered.masked_actions if item.action_id == logs.action_id
    ).reason is ActionMaskReasonV22.COVERED

    disabled_registry = build_tool_capability_registry_v22(
        disabled_sources=(EvidenceSourceV22.LOGS,)
    )
    unavailable = build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=TOPOLOGY,
        capability_registry=disabled_registry,
        executed_action_ids=(),
        remaining_budget=20.0,
    )
    assert all(
        next(
            masked
            for masked in unavailable.masked_actions
            if masked.action_id == item.action_id
        ).reason
        is ActionMaskReasonV22.SOURCE_UNAVAILABLE
        for item in unavailable.registry_actions
        if item.source is EvidenceSourceV22.LOGS
    )


@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_nonfinite_numbers_are_rejected_at_every_contract_boundary(value: float) -> None:
    with pytest.raises(ValidationError):
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service="payment",
            metric_kind=MetricKindV22.ERROR_RATE,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=3,
            value=value,
            unit=MetricUnitV22.RATIO,
            window_started_at=datetime(2026, 8, 19, 11, 55, tzinfo=timezone.utc),
            window_ended_at=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
        )
    with pytest.raises(ValidationError):
        TraceSpanV22(
            schema_version="dta-v22.trace-span.v1",
            observed_at=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
            service_path=("payment",),
            service="payment",
            parent_service=None,
            operation="Charge",
            status=SpanStatusV22.OK,
            duration_ms=value,
            first_error_location=False,
        )
    with pytest.raises(ValidationError):
        ResourceSampleV22(offset_ms=0, cpu_percent=value, memory_bytes=1)
    with pytest.raises(ValidationError):
        ResourceUsageRecordV22(
            schema_version="dta-v22.resource-usage-record.v1",
            service="payment",
            sampling_window_seconds=10,
            samples=(
                ResourceSampleV22(offset_ms=0, cpu_percent=1.0, memory_bytes=1),
                ResourceSampleV22(offset_ms=10000, cpu_percent=1.0, memory_bytes=1),
            ),
            memory_slope_bytes_per_second=value,
        )
    with pytest.raises(ValidationError):
        ToolCapabilityV22(
            source=EvidenceSourceV22.LOGS,
            enabled=True,
            weighted_cost=value,
        )
    with pytest.raises((ValidationError, ValueError)):
        build_action_catalog_v22(
            candidate_services=("checkout", "payment"),
            topology=TOPOLOGY,
            capability_registry=REGISTRY,
            executed_action_ids=(),
            remaining_budget=value,
        )
    with pytest.raises(ValueError):
        semantic_sha256_v22({"value": value})


def test_recent_changes_contract_exposes_only_sanitized_opaque_fields() -> None:
    record = RecentChangeRecordV22(
        schema_version="dta-v22.recent-change-record.v1",
        opaque_change_id="chg_0123456789abcdef",
        service="payment",
        observed_at=datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc),
        category=ChangeCategoryV22.CONFIGURATION,
        rollout_state=RolloutStateV22.COMPLETED,
        revision_digest="1" * 64,
    )
    payload = record.model_dump(mode="json")

    assert set(payload) == {
        "schema_version",
        "opaque_change_id",
        "service",
        "observed_at",
        "category",
        "rollout_state",
        "revision_digest",
    }
    serialized = record.model_dump_json().casefold()
    for forbidden in (
        "fault_flag",
        "injected_variant",
        "expected_mechanism",
        "expected_runbook",
        "commit_sha",
        "branch_name",
        "pull_request",
    ):
        assert forbidden not in serialized

    with pytest.raises(ValidationError):
        RecentChangeRecordV22.model_validate(
            {
                **payload,
                "commit_sha": "2" * 40,
            }
        )
