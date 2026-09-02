from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.product.connectors.base import (
    ConnectorCapabilityV1,
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_BINDING_SHA256_V023,
    ACTIVE_PROFILE_SHA256_V023,
    CANDIDATE_SET_SHA256_V023,
    OPERATOR_DECISION_SHA256_V023,
    PROFILE_BINDING_PASS_V023,
    OpenSearchConnectorDiagnosticsV023,
    OpenSearchConnectorProfileBindingV023,
    build_profile_bound_opensearch_config_v023,
    load_product_v023_profile_binding,
)
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeConnectorV02,
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.contracts import ConnectorConfigV1, ConnectorKindV1
from ecomsre.product.environment.capabilities import SourceCapabilityStatusV1
from ecomsre.product.incidents.read_backend import (
    ProductReadBackendV1,
    _project_capability_scope_v0232,
)
from ecomsre.product.pilot.runtime_authority_v02 import PilotRuntimeAuthorityV02
from ecomsre.product.incidents.evidence_binding_v0232 import (
    CapabilityEvidenceObservationV0232,
    CapabilityLimitationBindingV0232,
    CapabilityLimitationCandidateV0232,
    ConnectorEvidenceBindingV0232,
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
    OpenSearchProfileEvidenceBindingV0232,
    RuntimeSnapshotEvidenceBindingV0232,
    build_connector_evidence_binding_v0232,
    build_opensearch_profile_evidence_binding_v0232,
    build_runtime_snapshot_evidence_binding_v0232,
)


INCIDENT_ID = f"inc-{'1' * 24}"
DIAGNOSIS_ID = f"diag-{'2' * 24}"
ENVIRONMENT_ID = f"env-{'3' * 24}"
SHA = {
    name: character * 64
    for name, character in {
        "config": "1",
        "context": "2",
        "component": "3",
        "combined": "4",
        "payload": "5",
        "diagnostics": "6",
        "snapshot": "7",
        "connector": "8",
        "pilot": "9",
        "read": "a",
        "matrix": "b",
        "bundle": "c",
        "trace": "d",
        "object_logs": "e",
        "object_runtime": "f",
        "object_capability": "0",
    }.items()
}
WINDOW = ConnectorWindowV1(
    started_at=datetime(2026, 8, 29, 15, 0, tzinfo=UTC),
    ended_at=datetime(2026, 8, 29, 15, 1, tzinfo=UTC),
)
ROOT = Path(__file__).resolve().parents[2]


def test_capability_scope_projects_environment_partial_status_to_candidates() -> None:
    status, available = _project_capability_scope_v0232(
        status=SourceCapabilityStatusV1.PARTIAL,
        covered_services=("checkout", "payment"),
        required_services=("checkout",),
    )

    assert status is SourceCapabilityStatusV1.AVAILABLE
    assert available == ("checkout",)

    status, available = _project_capability_scope_v0232(
        status=SourceCapabilityStatusV1.PARTIAL,
        covered_services=("checkout",),
        required_services=("checkout", "payment"),
    )

    assert status is SourceCapabilityStatusV1.PARTIAL
    assert available == ("checkout",)

    status, available = _project_capability_scope_v0232(
        status=SourceCapabilityStatusV1.PARTIAL,
        covered_services=("payment",),
        required_services=("checkout",),
    )

    assert status is SourceCapabilityStatusV1.UNAVAILABLE
    assert available == ()


def test_read_acquisition_accepts_environment_partial_source_complete_for_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def execute(self, *, action, incident, environment, identity_by_logical, window):
        del self, incident, environment, identity_by_logical
        return (
            ConnectorQueryResultV1.build(
                source=action.source,
                status=ReadSourceStatusV22.SUCCESS_EMPTY,
                requested_services=action.target_services,
                covered_services=action.target_services,
                window=window,
                records=(),
                truncated=False,
                safe_error_code=None,
                latency_ms=1.0,
            ),
            False,
            (),
        )

    monkeypatch.setattr(ProductReadBackendV1, "_execute", execute)
    backend = ProductReadBackendV1(
        connectors=cast(Any, object()),
        changes=cast(Any, object()),
        metrics=cast(Any, SimpleNamespace(increment=lambda *args: None)),
    )

    acquisition = backend.acquire(
        incident=cast(
            Any,
            SimpleNamespace(
                incident_id=INCIDENT_ID,
                incident_sha256=SHA["component"],
                candidate_logical_services=("checkout",),
                diagnosis_observed_at=WINDOW.ended_at,
            ),
        ),
        environment=cast(Any, SimpleNamespace(connector_configs=())),
        identity_map=cast(Any, SimpleNamespace(services=())),
        capability_matrix=cast(
            Any,
            SimpleNamespace(
                capability_sha256=SHA["matrix"],
                sources=(
                    SimpleNamespace(
                        source=EvidenceSourceV22.LOGS,
                        status=SourceCapabilityStatusV1.PARTIAL,
                        covered_services=("checkout", "payment"),
                    ),
                    SimpleNamespace(
                        source=EvidenceSourceV22.TRACES,
                        status=SourceCapabilityStatusV1.PARTIAL,
                        covered_services=("payment",),
                    ),
                ),
            ),
        ),
        topology_edges=(),
    )

    assert len(acquisition.raw_outcomes) == 1
    assert acquisition.capability_limitations == ("SOURCE_TRACES_UNAVAILABLE",)
    assert len(acquisition.capability_observations_v0232) == 1
    assert acquisition.capability_observations_v0232[0].source is EvidenceSourceV22.TRACES
    assert acquisition.capability_observations_v0232[0].capability_status is (
        SourceCapabilityStatusV1.UNAVAILABLE
    )
    assert acquisition.capability_observations_v0232[0].available_services == ()
    assert len(acquisition.capability_limitation_candidates_v0232) == 1
    assert acquisition.capability_limitation_candidates_v0232[0].limitation_code == (
        "SOURCE_TRACES_UNAVAILABLE"
    )


def test_generic_connector_binding_seals_component_and_combined_results() -> None:
    binding = ConnectorEvidenceBindingV0232.build(
        binding_id="connector-binding-logs-1",
        incident_id=INCIDENT_ID,
        action_id="a:logs:checkout",
        source=EvidenceSourceV22.LOGS,
        connector_name="otel-opensearch",
        connector_kind=ConnectorKindV1.OPENSEARCH,
        environment_id=ENVIRONMENT_ID,
        connector_config_sha256=SHA["config"],
        query_context_sha256=SHA["context"],
        component_result_sha256=SHA["component"],
        combined_result_sha256=SHA["combined"],
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=WINDOW,
        binding_kind="OPENSEARCH_PROFILE",
        binding_payload_sha256=SHA["payload"],
    )

    assert binding.component_result_sha256 != binding.combined_result_sha256
    assert binding.binding_sha256 != SHA["component"]

    with pytest.raises(ValidationError, match="binding digest differs"):
        ConnectorEvidenceBindingV0232.model_validate(
            {
                **binding.model_dump(mode="json"),
                "combined_result_sha256": SHA["payload"],
            }
        )
    with pytest.raises(ValidationError, match="covered services exceed requested"):
        ConnectorEvidenceBindingV0232.build(
            **{
                **binding.model_dump(mode="python", exclude={"binding_sha256"}),
                "covered_services": ("checkout", "payment"),
            }
        )


def test_generic_connector_binding_builder_uses_exact_query_inputs() -> None:
    config = ConnectorConfigV1(
        name="otel-opensearch",
        kind=ConnectorKindV1.OPENSEARCH,
        endpoint="http://127.0.0.1:9200",
        settings={
            "index_pattern": "otel-v1-apm-span-*",
            "timestamp_field": "@timestamp",
            "service_field": "service.name",
            "severity_field": "severity_text",
            "message_field": "body",
        },
    )
    context = ConnectorQueryContextV1(
        environment_id=ENVIRONMENT_ID,
        requested_services=("checkout",),
        window=WINDOW,
        maximum_records=20,
        requested_source=EvidenceSourceV22.LOGS,
    )
    component = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.LOGS,
        status=ReadSourceStatusV22.SUCCESS_EMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=WINDOW,
        records=(),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )
    combined = component.model_copy(
        update={"result_sha256": component.result_sha256}
    )

    binding = build_connector_evidence_binding_v0232(
        incident_id=INCIDENT_ID,
        action_id="a:logs:checkout",
        config=config,
        context=context,
        component_result=component,
        combined_result=combined,
        binding_kind="GENERIC",
        binding_payload_sha256=component.result_sha256,
    )

    assert binding.connector_config_sha256 == semantic_sha256_v22(
        config.model_dump(mode="json")
    )
    assert binding.query_context_sha256 == semantic_sha256_v22(
        context.model_dump(mode="json")
    )
    assert binding.component_result_sha256 == component.result_sha256
    assert binding.combined_result_sha256 == combined.result_sha256


def test_read_backend_captures_generic_binding_for_legacy_connector() -> None:
    config = ConnectorConfigV1(
        name="legacy-logs",
        kind=ConnectorKindV1.OPENSEARCH,
        endpoint="http://127.0.0.1:9200",
        settings={
            "index_pattern": "otel-v1-apm-span-*",
            "timestamp_field": "@timestamp",
            "service_field": "service.name",
            "severity_field": "severity_text",
            "message_field": "body",
        },
    )

    class LegacyConnector:
        def capabilities(self):
            return (
                ConnectorCapabilityV1(
                    source=EvidenceSourceV22.LOGS,
                    supports_historical_range=True,
                    supports_multi_target=False,
                    supports_service_discovery=True,
                    supports_baseline=True,
                    supports_target_complete_coverage=True,
                    maximum_window_seconds=3600,
                ),
            )

        def query(self, context):
            return (
                ConnectorQueryResultV1.build(
                    source=EvidenceSourceV22.LOGS,
                    status=ReadSourceStatusV22.SUCCESS_EMPTY,
                    requested_services=context.requested_services,
                    covered_services=context.requested_services,
                    window=context.window,
                    records=(),
                    truncated=False,
                    safe_error_code=None,
                    latency_ms=1.0,
                ),
            )

        def close(self):
            return None

    class Registry:
        def create(self, observed_config):
            assert observed_config == config
            return LegacyConnector()

    action = next(
        item
        for item in build_action_catalog_v22(
            candidate_services=("checkout",),
            topology=StaticTopologyV22.build(services=("checkout",), edges=()),
            capability_registry=build_tool_capability_registry_v22(),
            executed_action_ids=(),
            remaining_budget=100.0,
        ).registry_actions
        if item.source is EvidenceSourceV22.LOGS
    )
    backend = ProductReadBackendV1(
        connectors=Registry(),  # type: ignore[arg-type]
        changes=object(),  # type: ignore[arg-type]
        metrics=object(),  # type: ignore[arg-type]
    )
    result, _fixture_backed, components = backend._execute(
        action=action,
        incident=cast(
            Any,
            SimpleNamespace(
                incident_id=INCIDENT_ID,
                environment_id=ENVIRONMENT_ID,
            ),
        ),
        environment=cast(
            Any,
            SimpleNamespace(connector_configs=(config,)),
        ),
        identity_by_logical={
            "checkout": SimpleNamespace(
                aliases=SimpleNamespace(opensearch=("checkout",))
            )
        },
        window=WINDOW,
    )

    assert result.status is ReadSourceStatusV22.SUCCESS_EMPTY
    assert len(components) == 1
    captured = backend._last_connector_bindings_v0232
    assert len(captured) == 1
    assert captured[0]["connector_binding"]["component_result_sha256"] == (
        components[0].result_sha256
    )
    assert captured[0]["connector_binding"]["combined_result_sha256"] == (
        result.result_sha256
    )
    assert captured[0]["binding_payload"] is None

    mismatched_window = ConnectorWindowV1(
        started_at=datetime(2026, 8, 29, 14, 59, tzinfo=UTC),
        ended_at=datetime(2026, 8, 29, 15, 0, tzinfo=UTC),
    )

    class MismatchedLegacyConnector(LegacyConnector):
        def query(self, context):
            return (
                ConnectorQueryResultV1.build(
                    source=EvidenceSourceV22.LOGS,
                    status=ReadSourceStatusV22.SUCCESS_EMPTY,
                    requested_services=("payment",),
                    covered_services=("payment",),
                    window=mismatched_window,
                    records=(),
                    truncated=False,
                    safe_error_code=None,
                    latency_ms=1.0,
                ),
            )

    class MismatchedRegistry:
        def create(self, observed_config):
            assert observed_config == config
            return MismatchedLegacyConnector()

    mismatched_backend = ProductReadBackendV1(
        connectors=MismatchedRegistry(),  # type: ignore[arg-type]
        changes=object(),  # type: ignore[arg-type]
        metrics=object(),  # type: ignore[arg-type]
    )
    failed, _fixture_backed, mismatched_components = mismatched_backend._execute(
        action=action,
        incident=cast(
            Any,
            SimpleNamespace(
                incident_id=INCIDENT_ID,
                environment_id=ENVIRONMENT_ID,
            ),
        ),
        environment=cast(
            Any,
            SimpleNamespace(connector_configs=(config,)),
        ),
        identity_by_logical={
            "checkout": SimpleNamespace(
                aliases=SimpleNamespace(opensearch=("checkout",))
            )
        },
        window=WINDOW,
    )

    mismatch_binding = mismatched_backend._last_connector_bindings_v0232[0]
    assert failed.status is ReadSourceStatusV22.FAILURE_SCHEMA
    assert failed.safe_error_code == "CONNECTOR_ACTION_CONTRACT_INVALID"
    assert mismatch_binding["connector_binding"]["binding_kind"] == "GENERIC"
    assert mismatch_binding["connector_binding"]["component_result_sha256"] == (
        mismatched_components[0].result_sha256
    )
    assert mismatch_binding["connector_binding"]["combined_result_sha256"] == (
        failed.result_sha256
    )
    assert mismatch_binding["binding_payload"] is None


def test_opensearch_p01_binding_supports_empty_and_nonempty_success_counts() -> None:
    common = {
        "active_profile_id": "product-v0222-operator-selected-profile",
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "profile_binding_sha256": ACTIVE_PROFILE_BINDING_SHA256_V023,
        "selected_candidate_alias": "P01",
        "candidate_set_sha256": CANDIDATE_SET_SHA256_V023,
        "operator_decision_sha256": OPERATOR_DECISION_SHA256_V023,
        "query_diagnostics_sha256": SHA["diagnostics"],
        "connector_result_sha256": SHA["component"],
        "query_window": WINDOW,
    }
    empty = OpenSearchProfileEvidenceBindingV0232.build(
        **common,
        accepted_record_count=0,
        rejected_record_count=0,
        rejection_reason_codes=(),
    )
    nonempty = OpenSearchProfileEvidenceBindingV0232.build(
        **common,
        accepted_record_count=4,
        rejected_record_count=1,
        rejection_reason_codes=("OPENSEARCH_TRACE_ID_VALUE_INVALID",),
    )

    assert empty.accepted_record_count == 0
    assert nonempty.accepted_record_count == 4
    with pytest.raises(ValidationError, match="frozen P01 profile differs"):
        OpenSearchProfileEvidenceBindingV0232.build(
            **{**common, "active_profile_sha256": SHA["payload"]},
            accepted_record_count=0,
            rejected_record_count=0,
            rejection_reason_codes=(),
        )
    with pytest.raises(ValidationError, match="rejection reasons differ"):
        OpenSearchProfileEvidenceBindingV0232.build(
            **common,
            accepted_record_count=0,
            rejected_record_count=0,
            rejection_reason_codes=("UNEXPECTED_REJECTION",),
        )


def test_opensearch_binding_builder_cross_checks_diagnostics_and_result() -> None:
    diagnostics_body = {
        "schema_version": "ecomsre.product.opensearch-connector-diagnostics.v023",
        "terminal": PROFILE_BINDING_PASS_V023,
        "settings_mode": "PROFILE_BOUND",
        "profile_binding_sha256": ACTIVE_PROFILE_BINDING_SHA256_V023,
        "profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "index_pattern": "otel-logs-*",
        "timestamp_query_field": "@timestamp",
        "service_source_field": "resource.service.name",
        "service_query_field": "resource.service.name.keyword",
        "severity_field": "severity.text",
        "message_field": "body",
        "trace_id_field": "traceId",
        "maximum_record_rejection_fraction": 0.2,
        "last_query_status": "SUCCESS_EMPTY",
        "last_normalization_status": "SUCCESS_EMPTY",
        "last_query_batch_sha256": SHA["payload"],
        "last_safe_error_code": None,
        "last_sampled_record_count": 0,
        "last_accepted_record_count": 0,
        "last_rejected_record_count": 0,
        "last_rejection_fraction": 0.0,
        "last_rejection_codes_by_count": {},
    }
    diagnostics = OpenSearchConnectorDiagnosticsV023.model_validate(
        {
            **diagnostics_body,
            "diagnostics_sha256": semantic_sha256_v22(diagnostics_body),
        }
    )
    result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.LOGS,
        status=ReadSourceStatusV22.SUCCESS_EMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=WINDOW,
        records=(),
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )

    binding = build_opensearch_profile_evidence_binding_v0232(
        profile_binding=load_product_v023_profile_binding(
            active_profile_path=(
                ROOT / "config/product-v0222/opensearch/normalization-profile.json"
            ),
            handoff_path=ROOT / "docs/analysis/product-v0222-baseline-handoff.json",
        ),
        diagnostics=diagnostics,
        connector_result=result,
    )

    assert binding.query_diagnostics_sha256 == diagnostics.diagnostics_sha256
    assert binding.accepted_record_count == 0
    assert binding.connector_result_sha256 == result.result_sha256

    with pytest.raises(ValueError, match="diagnostics/result semantics differ"):
        build_opensearch_profile_evidence_binding_v0232(
            profile_binding=load_product_v023_profile_binding(
                active_profile_path=(
                    ROOT
                    / "config/product-v0222/opensearch/normalization-profile.json"
                ),
                handoff_path=(
                    ROOT / "docs/analysis/product-v0222-baseline-handoff.json"
                ),
            ),
            diagnostics=diagnostics,
            connector_result=result.model_copy(
                update={"status": ReadSourceStatusV22.SUCCESS_NONEMPTY}
            ),
        )


def test_profile_bound_connector_exposes_exact_v0232_evidence_input() -> None:
    config = build_profile_bound_opensearch_config_v023(
        active_profile_path=(
            ROOT / "config/product-v0222/opensearch/normalization-profile.json"
        ),
        handoff_path=ROOT / "docs/analysis/product-v0222-baseline-handoff.json",
        endpoint="https://opensearch.test",
    )

    failure_mode = False
    nonempty_mode = False

    def handler(_request: httpx.Request) -> httpx.Response:
        if failure_mode:
            return httpx.Response(503, json={"error": "unavailable"})
        if nonempty_mode:
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "hits": [
                            {
                                "_source": {
                                    "@timestamp": "2026-08-29T15:00:30Z",
                                    "resource.service.name": "checkout",
                                    "severity.text": "INFO",
                                    "body": "healthy checkout",
                                    "traceId": "1" * 32,
                                }
                            }
                        ],
                        "total": {"value": 1},
                    }
                },
            )
        return httpx.Response(
            200,
            json={"hits": {"hits": [], "total": {"value": 0}}},
        )

    connector = OpenSearchConnectorV1(
        config,
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    result = connector.query(
        ConnectorQueryContextV1(
            environment_id=ENVIRONMENT_ID,
            requested_services=("checkout",),
            service_aliases={"checkout": "checkout"},
            window=WINDOW,
            maximum_records=5,
            requested_source=EvidenceSourceV22.LOGS,
        )
    )[0]

    exposed = connector.evidence_binding_v0232()

    assert exposed is not None
    profile_binding, diagnostics = exposed
    assert isinstance(profile_binding, OpenSearchConnectorProfileBindingV023)
    assert isinstance(diagnostics, OpenSearchConnectorDiagnosticsV023)
    assert diagnostics.last_query_status == result.status.value

    class Registry:
        def create(self, observed_config):
            assert observed_config == config
            return connector

    action = next(
        item
        for item in build_action_catalog_v22(
            candidate_services=("checkout",),
            topology=StaticTopologyV22.build(services=("checkout",), edges=()),
            capability_registry=build_tool_capability_registry_v22(),
            executed_action_ids=(),
            remaining_budget=100.0,
        ).registry_actions
        if item.source is EvidenceSourceV22.LOGS
    )
    backend = ProductReadBackendV1(
        connectors=Registry(),  # type: ignore[arg-type]
        changes=object(),  # type: ignore[arg-type]
        metrics=object(),  # type: ignore[arg-type]
    )
    combined, _fixture_backed, _components = backend._execute(
        action=action,
        incident=cast(
            Any,
            SimpleNamespace(
                incident_id=INCIDENT_ID,
                environment_id=ENVIRONMENT_ID,
            ),
        ),
        environment=cast(
            Any,
            SimpleNamespace(connector_configs=(config,)),
        ),
        identity_by_logical={
            "checkout": SimpleNamespace(
                aliases=SimpleNamespace(opensearch=("checkout",))
            )
        },
        window=WINDOW,
    )

    captured = backend._last_connector_bindings_v0232
    assert combined.status is ReadSourceStatusV22.SUCCESS_EMPTY
    assert captured[0]["connector_binding"]["binding_kind"] == (
        "OPENSEARCH_PROFILE"
    )
    assert captured[0]["binding_payload"]["active_profile_sha256"] == (
        ACTIVE_PROFILE_SHA256_V023
    )

    nonempty_mode = True
    nonempty_connector = OpenSearchConnectorV1(
        config,
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )

    class NonemptyRegistry:
        def create(self, observed_config):
            assert observed_config == config
            return nonempty_connector

    nonempty_backend = ProductReadBackendV1(
        connectors=NonemptyRegistry(),  # type: ignore[arg-type]
        changes=object(),  # type: ignore[arg-type]
        metrics=object(),  # type: ignore[arg-type]
    )
    nonempty, _fixture_backed, _components = nonempty_backend._execute(
        action=action,
        incident=cast(
            Any,
            SimpleNamespace(
                incident_id=INCIDENT_ID,
                environment_id=ENVIRONMENT_ID,
            ),
        ),
        environment=cast(
            Any,
            SimpleNamespace(connector_configs=(config,)),
        ),
        identity_by_logical={
            "checkout": SimpleNamespace(
                aliases=SimpleNamespace(opensearch=("checkout",))
            )
        },
        window=WINDOW,
    )
    nonempty_capture = nonempty_backend._last_connector_bindings_v0232
    assert nonempty.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert nonempty_capture[0]["connector_binding"]["binding_kind"] == (
        "OPENSEARCH_PROFILE"
    )
    assert nonempty_capture[0]["binding_payload"]["accepted_record_count"] == 1

    nonempty_mode = False
    failure_mode = True
    failing_connector = OpenSearchConnectorV1(
        config,
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )

    class FailingRegistry:
        def create(self, observed_config):
            assert observed_config == config
            return failing_connector

    failing_backend = ProductReadBackendV1(
        connectors=FailingRegistry(),  # type: ignore[arg-type]
        changes=object(),  # type: ignore[arg-type]
        metrics=object(),  # type: ignore[arg-type]
    )
    failed, _fixture_backed, _components = failing_backend._execute(
        action=action,
        incident=cast(
            Any,
            SimpleNamespace(
                incident_id=INCIDENT_ID,
                environment_id=ENVIRONMENT_ID,
            ),
        ),
        environment=cast(
            Any,
            SimpleNamespace(connector_configs=(config,)),
        ),
        identity_by_logical={
            "checkout": SimpleNamespace(
                aliases=SimpleNamespace(opensearch=("checkout",))
            )
        },
        window=WINDOW,
    )

    failed_capture = failing_backend._last_connector_bindings_v0232
    assert failed.status is ReadSourceStatusV22.FAILURE_UNAVAILABLE
    assert failed_capture[0]["connector_binding"]["binding_kind"] == "GENERIC"
    assert failed_capture[0]["binding_payload"] is None


def test_runtime_binding_requires_exact_checkout_authority_and_age() -> None:
    observed_at = datetime(2026, 8, 29, 15, 0, 50, tzinfo=UTC)
    payload = {
        "runtime_snapshot_sha256": SHA["snapshot"],
        "runtime_snapshot_observed_at": observed_at,
        "runtime_snapshot_environment_id": ENVIRONMENT_ID,
        "runtime_snapshot_authority_sha256": SHA["connector"],
        "pilot_runtime_authority_sha256": SHA["pilot"],
        "read_authority_sha256": SHA["read"],
        "connector_binding_sha256": SHA["connector"],
        "maximum_age_seconds": 600,
        "age_at_query_seconds": 10.0,
        "requested_services": ("checkout",),
        "covered_services": ("checkout",),
        "connector_result_sha256": SHA["component"],
        "query_window": WINDOW,
    }
    binding = RuntimeSnapshotEvidenceBindingV0232.build(**payload)

    assert binding.runtime_snapshot_observed_at == observed_at
    with pytest.raises(ValidationError, match="snapshot authority differs"):
        RuntimeSnapshotEvidenceBindingV0232.build(
            **{**payload, "runtime_snapshot_authority_sha256": SHA["payload"]}
        )
    with pytest.raises(ValidationError, match="age at query differs"):
        RuntimeSnapshotEvidenceBindingV0232.build(
            **{**payload, "age_at_query_seconds": 11.0}
        )
    with pytest.raises(
        ValidationError,
        match="at least 1 item|checkout coverage differs",
    ):
        RuntimeSnapshotEvidenceBindingV0232.build(**{**payload, "covered_services": ()})


def test_runtime_connector_exposes_and_builds_exact_fresh_snapshot_binding(
    tmp_path: Path,
) -> None:
    authority = PilotRuntimeAuthorityV02.build(
        environment_id=ENVIRONMENT_ID,
        allowed_logical_services=("checkout",),
        profile_sha256=SHA["payload"],
        daemon_identity_sha256="1" * 64,
        docker_context_sha256="2" * 64,
        config_bundle_sha256="3" * 64,
        resolved_sandbox_sha256="4" * 64,
        resolved_endpoints_sha256="5" * 64,
        ownership_scope_sha256="6" * 64,
    )
    snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=ENVIRONMENT_ID,
        authority_sha256=authority.connector_binding_sha256,
        observed_at=datetime(2026, 8, 29, 15, 0, 50, tzinfo=UTC),
        services={
            "checkout": {
                "state": RuntimeStateV22.RUNNING,
                "healthy": True,
                "restart_count": 0,
            }
        },
    )
    write_pilot_runtime_snapshot_v02(tmp_path / "pilot/runtime.json", snapshot)
    config = ConnectorConfigV1(
        name="pilot-runtime",
        kind=ConnectorKindV1.PILOT_RUNTIME,
        settings={
            "snapshot_ref": "pilot/runtime.json",
            "authority_sha256": authority.connector_binding_sha256,
            "maximum_age_seconds": 600,
        },
    )
    connector = PilotRuntimeConnectorV02(config, data_root=tmp_path)
    result = connector.query(
        ConnectorQueryContextV1(
            environment_id=ENVIRONMENT_ID,
            requested_services=("checkout",),
            window=WINDOW,
            maximum_records=1,
            requested_source=EvidenceSourceV22.RUNTIME,
        )
    )[0]

    exposed = connector.evidence_binding_v0232()
    binding = build_runtime_snapshot_evidence_binding_v0232(
        snapshot=exposed,
        config=config,
        runtime_authority=authority,
        connector_result=result,
        formal_traffic_started_at=WINDOW.started_at,
        diagnosis_observed_at=WINDOW.ended_at,
    )

    assert exposed == snapshot
    assert binding.runtime_snapshot_sha256 == snapshot.snapshot_sha256
    assert binding.connector_binding_sha256 == authority.connector_binding_sha256
    assert binding.pilot_runtime_authority_sha256 == (
        authority.pilot_authority_sha256
    )
    assert binding.read_authority_sha256 == authority.read_authority.authority_sha256

    class Registry:
        def create(self, observed_config):
            assert observed_config == config
            return connector

    action = next(
        item
        for item in build_action_catalog_v22(
            candidate_services=("checkout",),
            topology=StaticTopologyV22.build(services=("checkout",), edges=()),
            capability_registry=build_tool_capability_registry_v22(),
            executed_action_ids=(),
            remaining_budget=100.0,
        ).registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
    )
    backend = ProductReadBackendV1(
        connectors=Registry(),  # type: ignore[arg-type]
        changes=object(),  # type: ignore[arg-type]
        metrics=object(),  # type: ignore[arg-type]
        pilot_runtime_authority=authority,
    )
    combined, _fixture_backed, _components = backend._execute(
        action=action,
        incident=cast(
            Any,
            SimpleNamespace(
                incident_id=INCIDENT_ID,
                environment_id=ENVIRONMENT_ID,
                started_at=WINDOW.started_at,
                diagnosis_observed_at=WINDOW.ended_at,
            ),
        ),
        environment=cast(
            Any,
            SimpleNamespace(
                environment_id=ENVIRONMENT_ID,
                connector_configs=(config,),
            ),
        ),
        identity_by_logical={},
        window=WINDOW,
    )

    captured = backend._last_connector_bindings_v0232
    assert combined.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert captured[0]["connector_binding"]["binding_kind"] == "RUNTIME_SNAPSHOT"
    assert captured[0]["binding_payload"]["runtime_snapshot_sha256"] == (
        snapshot.snapshot_sha256
    )


def test_capability_candidate_enforces_category_specific_evidence() -> None:
    unavailable = CapabilityLimitationCandidateV0232.build(
        limitation_code="SOURCE_LOGS_UNAVAILABLE",
        category="SOURCE_UNAVAILABLE",
        source=EvidenceSourceV22.LOGS,
        capability_status=SourceCapabilityStatusV1.UNAVAILABLE,
        connector_action_id=None,
        connector_result_sha256=None,
        safe_error_code=None,
        coverage_required_services=("checkout",),
        coverage_observed_services=(),
    )

    assert unavailable.candidate_sha256
    with pytest.raises(ValidationError, match="query failure evidence differs"):
        CapabilityLimitationCandidateV0232.build(
            limitation_code="SOURCE_LOGS_QUERY_FAILURE",
            category="QUERY_FAILURE",
            source=EvidenceSourceV22.LOGS,
            capability_status=SourceCapabilityStatusV1.AVAILABLE,
            connector_action_id=None,
            connector_result_sha256=None,
            safe_error_code=None,
            coverage_required_services=("checkout",),
            coverage_observed_services=(),
        )
    with pytest.raises(ValidationError, match="coverage gap is absent"):
        CapabilityLimitationCandidateV0232.build(
            limitation_code="SOURCE_LOGS_COVERAGE_GAP",
            category="COVERAGE_GAP",
            source=EvidenceSourceV22.LOGS,
            capability_status=SourceCapabilityStatusV1.PARTIAL,
            connector_action_id="a:logs:checkout",
            connector_result_sha256=SHA["component"],
            safe_error_code=None,
            coverage_required_services=("checkout",),
            coverage_observed_services=("checkout",),
        )

    runtime = CapabilityLimitationCandidateV0232.build(
        limitation_code="RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE",
        category="RUNTIME_AUTHORITY_UNAVAILABLE",
        source=EvidenceSourceV22.RUNTIME,
        capability_status=SourceCapabilityStatusV1.AVAILABLE,
        connector_action_id="a:runtime:checkout",
        connector_result_sha256=SHA["component"],
        safe_error_code="RUNTIME_AUTHORITY_UNAVAILABLE",
        coverage_required_services=("checkout",),
        coverage_observed_services=("checkout",),
    )
    runtime_binding = CapabilityLimitationBindingV0232.build(
        limitation_code=runtime.limitation_code,
        category=runtime.category,
        source=runtime.source,
        evidence_ref="o:a:runtime:checkout:runtime-result",
        connector_result_sha256=runtime.connector_result_sha256,
        capability_observation_sha256=None,
        safe_error_code=runtime.safe_error_code,
        coverage_status="COMPLETE",
    )

    assert runtime_binding.coverage_status.value == "COMPLETE"


def test_capability_observation_has_deterministic_ref_and_one_backing() -> None:
    observation = CapabilityEvidenceObservationV0232.build(
        source=EvidenceSourceV22.TRACES,
        capability_matrix_sha256=SHA["matrix"],
        capability_status=SourceCapabilityStatusV1.UNAVAILABLE,
        required_services=("checkout",),
        available_services=(),
        reason_code="SOURCE_TRACES_UNAVAILABLE",
    )
    binding = CapabilityLimitationBindingV0232.build(
        limitation_code="SOURCE_TRACES_UNAVAILABLE",
        category="SOURCE_UNAVAILABLE",
        source=EvidenceSourceV22.TRACES,
        evidence_ref=observation.evidence_ref,
        connector_result_sha256=None,
        capability_observation_sha256=observation.observation_sha256,
        safe_error_code=None,
        coverage_status="NONE",
    )

    assert observation.evidence_ref.startswith("capability:v0232:traces:")
    assert binding.capability_observation_sha256 == observation.observation_sha256
    with pytest.raises(ValidationError, match="exactly one evidence backing"):
        CapabilityLimitationBindingV0232.build(
            limitation_code="SOURCE_TRACES_UNAVAILABLE",
            category="SOURCE_UNAVAILABLE",
            source=EvidenceSourceV22.TRACES,
            evidence_ref=observation.evidence_ref,
            connector_result_sha256=SHA["component"],
            capability_observation_sha256=observation.observation_sha256,
            safe_error_code=None,
            coverage_status="NONE",
        )


def test_decision_trace_keeps_algorithmic_reasons_in_a_self_sealed_sidecar() -> None:
    trace = DiagnosisDecisionTraceV0232.build(
        incident_id=INCIDENT_ID,
        diagnosis_id=DIAGNOSIS_ID,
        known_admission_status="NONE",
        extension_match_count=0,
        no_incident_admissible=False,
        required_coverage_satisfied=False,
        failed_sources=(EvidenceSourceV22.TRACES,),
        novelty_gate_disposition="INSUFFICIENT_EVIDENCE",
        novelty_gate_reason_codes=("COVERAGE_INSUFFICIENT", "BUDGET_EXHAUSTED"),
        residual_anomaly_ids=("anomaly-2", "anomaly-1"),
    )

    assert trace.novelty_gate_reason_codes == (
        "BUDGET_EXHAUSTED",
        "COVERAGE_INSUFFICIENT",
    )
    assert trace.residual_anomaly_ids == ("anomaly-1", "anomaly-2")
    with pytest.raises(ValidationError, match="trace digest differs"):
        DiagnosisDecisionTraceV0232.model_validate(
            {**trace.model_dump(mode="json"), "extension_match_count": 1}
        )


def test_diagnosis_index_closes_all_refs_shas_and_typed_bindings() -> None:
    observation = CapabilityEvidenceObservationV0232.build(
        source=EvidenceSourceV22.TRACES,
        capability_matrix_sha256=SHA["matrix"],
        capability_status=SourceCapabilityStatusV1.UNAVAILABLE,
        required_services=("checkout",),
        available_services=(),
        reason_code="SOURCE_TRACES_UNAVAILABLE",
    )
    limitation = CapabilityLimitationBindingV0232.build(
        limitation_code="SOURCE_TRACES_UNAVAILABLE",
        category="SOURCE_UNAVAILABLE",
        source=EvidenceSourceV22.TRACES,
        evidence_ref=observation.evidence_ref,
        connector_result_sha256=None,
        capability_observation_sha256=observation.observation_sha256,
        safe_error_code=None,
        coverage_status="NONE",
    )
    logs_ref = "o:a:logs:checkout:logs-result"
    runtime_ref = "o:a:runtime:checkout:runtime-result"
    all_refs = (logs_ref, observation.evidence_ref, runtime_ref)
    object_shas = {
        logs_ref: SHA["object_logs"],
        runtime_ref: SHA["object_runtime"],
        observation.evidence_ref: SHA["object_capability"],
    }
    index = DiagnosisEvidenceIndexV0232.build(
        incident_id=INCIDENT_ID,
        diagnosis_id=DIAGNOSIS_ID,
        evidence_bundle_sha256=SHA["bundle"],
        all_object_refs=all_refs,
        all_object_sha256_by_ref=object_shas,
        linked_support_refs=(logs_ref,),
        linked_contradiction_refs=(),
        successful_source_refs=(logs_ref, runtime_ref),
        failed_source_refs=(),
        open_search_profile_binding_ref=logs_ref,
        runtime_snapshot_binding_ref=runtime_ref,
        capability_limitation_bindings=(limitation,),
        decision_trace_sha256=SHA["trace"],
    )

    assert index.all_object_refs == tuple(sorted(all_refs))
    assert tuple(index.all_object_sha256_by_ref) == tuple(sorted(all_refs))
    with pytest.raises(ValidationError, match="object ref/SHA map differs"):
        DiagnosisEvidenceIndexV0232.build(
            **{
                **index.model_dump(mode="python", exclude={"index_sha256"}),
                "all_object_sha256_by_ref": {logs_ref: SHA["object_logs"]},
            }
        )
    with pytest.raises(ValidationError, match="successful and failed refs overlap"):
        DiagnosisEvidenceIndexV0232.build(
            **{
                **index.model_dump(mode="python", exclude={"index_sha256"}),
                "failed_source_refs": (logs_ref,),
            }
        )


def test_diagnosis_index_rejects_duplicate_limitation_bindings() -> None:
    observation = CapabilityEvidenceObservationV0232.build(
        source=EvidenceSourceV22.TRACES,
        capability_matrix_sha256=SHA["matrix"],
        capability_status=SourceCapabilityStatusV1.UNAVAILABLE,
        required_services=("checkout",),
        available_services=(),
        reason_code="SOURCE_TRACES_UNAVAILABLE",
    )
    limitation = CapabilityLimitationBindingV0232.build(
        limitation_code="SOURCE_TRACES_UNAVAILABLE",
        category="SOURCE_UNAVAILABLE",
        source=EvidenceSourceV22.TRACES,
        evidence_ref=observation.evidence_ref,
        connector_result_sha256=None,
        capability_observation_sha256=observation.observation_sha256,
        safe_error_code=None,
        coverage_status="NONE",
    )
    logs_ref = "o:a:logs:checkout:logs-result"
    runtime_ref = "o:a:runtime:checkout:runtime-result"

    with pytest.raises(ValidationError, match="limitation bindings are not unique"):
        DiagnosisEvidenceIndexV0232.build(
            incident_id=INCIDENT_ID,
            diagnosis_id=DIAGNOSIS_ID,
            evidence_bundle_sha256=SHA["bundle"],
            all_object_refs=(logs_ref, observation.evidence_ref, runtime_ref),
            all_object_sha256_by_ref={
                logs_ref: SHA["object_logs"],
                runtime_ref: SHA["object_runtime"],
                observation.evidence_ref: SHA["object_capability"],
            },
            linked_support_refs=(logs_ref,),
            linked_contradiction_refs=(),
            successful_source_refs=(logs_ref, runtime_ref),
            failed_source_refs=(),
            open_search_profile_binding_ref=logs_ref,
            runtime_snapshot_binding_ref=runtime_ref,
            capability_limitation_bindings=(limitation, limitation),
            decision_trace_sha256=SHA["trace"],
        )


def test_diagnosis_index_can_represent_missing_specialized_provenance() -> None:
    logs_ref = "o:a:logs:checkout:logs-result"
    runtime_ref = "o:a:runtime:checkout:runtime-result"
    index = DiagnosisEvidenceIndexV0232.build(
        incident_id=INCIDENT_ID,
        diagnosis_id=DIAGNOSIS_ID,
        evidence_bundle_sha256=SHA["bundle"],
        all_object_refs=(logs_ref, runtime_ref),
        all_object_sha256_by_ref={
            logs_ref: SHA["object_logs"],
            runtime_ref: SHA["object_runtime"],
        },
        linked_support_refs=(logs_ref,),
        linked_contradiction_refs=(),
        successful_source_refs=(logs_ref, runtime_ref),
        failed_source_refs=(),
        open_search_profile_binding_ref=None,
        runtime_snapshot_binding_ref=None,
        capability_limitation_bindings=(),
        decision_trace_sha256=SHA["trace"],
    )

    assert index.open_search_profile_binding_ref is None
    assert index.runtime_snapshot_binding_ref is None
