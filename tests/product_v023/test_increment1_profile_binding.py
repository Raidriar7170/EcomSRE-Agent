from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    BASELINE_HANDOFF_SHA256_V023,
    PROFILE_BINDING_PASS_V023,
    OpenSearchConnectorProfileBindingV023,
    build_profile_bound_opensearch_config_v023,
    build_product_v023_environment_payload,
    load_product_v023_profile_binding,
)
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    OpenSearchConnectorSettingsModeV1,
    OpenSearchConnectorSettingsV1,
)
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_PROFILE_SHA256 = (
    "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
)


def _profile_config() -> ConnectorConfigV1:
    return build_profile_bound_opensearch_config_v023(
        active_profile_path=(
            ROOT / "config/product-v0222/opensearch/normalization-profile.json"
        ),
        handoff_path=ROOT / "docs/analysis/product-v0222-baseline-handoff.json",
        endpoint="https://opensearch.test",
    )


def test_legacy_explicit_fields_remain_backward_compatible() -> None:
    config = ConnectorConfigV1(
        name="logs",
        kind="OPENSEARCH",
        endpoint="https://opensearch.test",
        settings={
            "index_pattern": "legacy-logs-*",
            "timestamp_field": "observedTimestamp",
            "service_field": "resource.service.name",
            "service_query_field": "resource.service.name.keyword",
            "severity_field": "severity.text",
            "message_field": "body",
            "trace_id_field": "trace_id",
        },
    )

    settings = OpenSearchConnectorSettingsV1.model_validate(config.settings)

    assert settings.mode is OpenSearchConnectorSettingsModeV1.LEGACY_EXPLICIT_FIELDS
    assert settings.timestamp_field == "observedTimestamp"
    assert settings.trace_id_field == "trace_id"
    assert settings.profile_binding is None


def test_active_p01_profile_builds_one_profile_bound_connector_config() -> None:
    binding = load_product_v023_profile_binding(
        active_profile_path=(
            ROOT / "config/product-v0222/opensearch/normalization-profile.json"
        ),
        handoff_path=ROOT / "docs/analysis/product-v0222-baseline-handoff.json",
    )
    config = _profile_config()
    settings = OpenSearchConnectorSettingsV1.model_validate(config.settings)

    assert binding.profile_sha256 == ACTIVE_PROFILE_SHA256
    assert binding.profile_status == "ACTIVE"
    assert binding.selected_candidate_alias == "P01"
    assert binding.baseline_handoff_sha256 == BASELINE_HANDOFF_SHA256_V023
    assert binding.index_pattern == "otel-logs-*"
    assert binding.timestamp_extraction.extraction.paths == ("@timestamp",)
    assert binding.service_source_field == "resource.service.name"
    assert binding.service_query_field == "resource.service.name.keyword"
    assert binding.severity_extraction.extraction.paths == ("severity.text",)
    assert binding.message_extraction.extraction.paths == ("body",)
    assert binding.trace_id_extraction is not None
    assert binding.trace_id_extraction.paths == ("traceId",)
    assert settings.mode is OpenSearchConnectorSettingsModeV1.PROFILE_BOUND
    assert settings.profile_binding == binding
    assert settings.timestamp_field is None
    assert settings.trace_id_field is None


def test_profile_binding_rejects_a_self_consistent_wrong_handoff_digest() -> None:
    binding = load_product_v023_profile_binding(
        active_profile_path=(
            ROOT / "config/product-v0222/opensearch/normalization-profile.json"
        ),
        handoff_path=ROOT / "docs/analysis/product-v0222-baseline-handoff.json",
    )
    tampered = binding.model_dump(mode="json")
    tampered["baseline_handoff_sha256"] = "0" * 64
    tampered["binding_sha256"] = semantic_sha256_v22(
        {key: value for key, value in tampered.items() if key != "binding_sha256"}
    )

    with pytest.raises(ValidationError, match="frozen OpenSearch binding"):
        OpenSearchConnectorProfileBindingV023.model_validate(tampered)


@pytest.mark.parametrize(
    ("stale_field", "stale_value"),
    (
        ("timestamp_field", "observedTimestamp"),
        ("trace_id_field", "trace_id"),
    ),
)
def test_profile_bound_mode_rejects_independent_stale_field_overrides(
    stale_field: str,
    stale_value: str,
) -> None:
    config = _profile_config()
    settings = {**config.settings, stale_field: stale_value}

    with pytest.raises(ValidationError, match="profile-bound"):
        ConnectorConfigV1(
            name="logs",
            kind="OPENSEARCH",
            endpoint="https://opensearch.test",
            settings=settings,
        )


def test_profile_snapshot_survives_environment_repository_restart(tmp_path: Path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    created = EnvironmentRepositoryV1(store).create(
        {
            "name": "product-v023-p01",
            "connector_configs": [_profile_config().model_dump(mode="json")],
            "explicit_service_catalog": ["checkout"],
        }
    )

    restarted = EnvironmentRepositoryV1(SqliteStoreV1(store.path)).get(
        created.environment_id
    )
    settings = OpenSearchConnectorSettingsV1.model_validate(
        restarted.connector_configs[0].settings
    )

    assert settings.profile_binding is not None
    assert settings.profile_binding.profile_sha256 == ACTIVE_PROFILE_SHA256
    assert settings.profile_binding.baseline_handoff_sha256 == BASELINE_HANDOFF_SHA256_V023


def test_p01_adapter_builds_an_ordinary_product_environment_payload() -> None:
    payload = build_product_v023_environment_payload(
        repository_root=ROOT,
        runtime_authority_sha256="1" * 64,
    )

    assert payload["name"] == "product-v023-fresh-baseline-nofault"
    assert payload["explicit_service_catalog"] == ["checkout"]
    identity = payload["service_identity_policy"]["services"][0]
    assert identity["logical_service"] == "checkout"
    assert identity["approved_many_to_one"] is True
    assert identity["aliases"]["opensearch"] == ["checkout", "checkoutservice"]
    by_kind = {item["kind"]: item for item in payload["connector_configs"]}
    assert set(by_kind) == {
        "PROMETHEUS",
        "OPENSEARCH",
        "JAEGER",
        "HTTP_HEALTH",
        "PILOT_RUNTIME",
    }
    opensearch = OpenSearchConnectorSettingsV1.model_validate(
        by_kind["OPENSEARCH"]["settings"]
    )
    assert opensearch.mode is OpenSearchConnectorSettingsModeV1.PROFILE_BOUND
    assert opensearch.profile_binding.profile_sha256 == ACTIVE_PROFILE_SHA256
    assert by_kind["PILOT_RUNTIME"]["settings"] == {
        "snapshot_ref": "pilot/runtime-readiness.json",
        "authority_sha256": "1" * 64,
        "maximum_age_seconds": 600,
    }


def test_profile_bound_connector_uses_p01_query_and_typed_normalizer() -> None:
    failure_mode: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal failure_mode
        body = json.loads(request.content)
        if body["size"] == 0:
            assert body["aggs"]["services"]["terms"]["field"] == (
                "resource.service.name.keyword"
            )
            return httpx.Response(
                200,
                json={
                    "hits": {"hits": []},
                    "aggregations": {
                        "services": {
                            "buckets": [
                                {"key": "checkout"},
                                {"key": "checkoutservice"},
                            ]
                        }
                    },
                },
            )
        if failure_mode == "schema":
            return httpx.Response(200, json={"not_hits": {}})
        if failure_mode == "transport":
            raise httpx.ConnectError("test-only transport failure", request=request)
        filters = body["query"]["bool"]["filter"]
        assert filters[0] == {
            "terms": {
                "resource.service.name.keyword": ["checkout", "checkoutservice"]
            }
        }
        assert set(body["_source"]) == {
            "@timestamp",
            "resource.service.name",
            "severity.text",
            "body",
            "traceId",
        }
        assert "observedTimestamp" not in json.dumps(body)
        assert "trace_id" not in json.dumps(body)
        hits = []
        for ordinal in range(5):
            source = {
                "@timestamp": f"2026-08-29T08:00:0{ordinal}Z",
                "resource": {"service": {"name": "checkoutservice"}},
                "severity": {"text": "INFO"},
                "body": f"healthy checkout {ordinal}",
            }
            if ordinal == 4:
                source["traceId"] = "not-hex"
            else:
                source["traceId"] = f"{ordinal + 1:032x}"
            hits.append({"_source": source})
        return httpx.Response(
            200,
            json={"hits": {"hits": hits, "total": {"value": 5}}},
        )

    connector = OpenSearchConnectorV1(
        _profile_config(),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    context = ConnectorQueryContextV1(
        environment_id=f"env-{'a' * 24}",
        requested_services=("checkout",),
        service_aliases={
            "checkout": "checkout",
            "checkoutservice": "checkout",
        },
        window=ConnectorWindowV1(
            started_at=datetime(2026, 8, 29, 8, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 29, 8, 1, 0, tzinfo=UTC),
        ),
        maximum_records=5,
    )

    health = connector.verify()
    result = connector.query(context)[0]
    diagnostics = connector.profile_diagnostics()

    assert health.discovered_services == ("checkout", "checkoutservice")
    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert len(result.records) == 4
    assert all(record.service == "checkout" for record in result.records)
    assert diagnostics is not None
    assert diagnostics.terminal == PROFILE_BINDING_PASS_V023
    assert diagnostics.profile_sha256 == ACTIVE_PROFILE_SHA256
    assert diagnostics.timestamp_query_field == "@timestamp"
    assert diagnostics.trace_id_field == "traceId"
    assert diagnostics.maximum_record_rejection_fraction == 0.2
    assert diagnostics.last_query_status == "SUCCESS_NONEMPTY"
    assert diagnostics.last_normalization_status == "SUCCESS_NONEMPTY"
    assert diagnostics.last_safe_error_code is None
    assert diagnostics.last_sampled_record_count == 5
    assert diagnostics.last_accepted_record_count == 4
    assert diagnostics.last_rejected_record_count == 1
    assert diagnostics.last_rejection_fraction == 0.2
    assert diagnostics.last_rejection_codes_by_count == {
        "OPENSEARCH_TRACE_ID_VALUE_INVALID": 1
    }

    failure_mode = "schema"
    failed = connector.query(context)[0]
    failed_diagnostics = connector.profile_diagnostics()

    assert failed.status is ReadSourceStatusV22.FAILURE_SCHEMA
    assert failed.safe_error_code == "OPENSEARCH_HITS_CONTAINER_INVALID"
    assert failed_diagnostics is not None
    assert failed_diagnostics.last_query_status == "FAILURE_SCHEMA"
    assert failed_diagnostics.last_query_batch_sha256 is None
    assert (
        failed_diagnostics.last_safe_error_code
        == "OPENSEARCH_HITS_CONTAINER_INVALID"
    )
    assert failed_diagnostics.last_sampled_record_count == 0

    failure_mode = "transport"
    unavailable = connector.query(context)[0]
    unavailable_diagnostics = connector.profile_diagnostics()

    assert unavailable.status is ReadSourceStatusV22.FAILURE_UNAVAILABLE
    assert unavailable.safe_error_code == "CONNECTOR_UNAVAILABLE"
    assert unavailable_diagnostics is not None
    assert unavailable_diagnostics.last_query_status == "FAILURE_UNAVAILABLE"
    assert unavailable_diagnostics.last_normalization_status is None
    assert unavailable_diagnostics.last_query_batch_sha256 is None
    assert unavailable_diagnostics.last_safe_error_code == "CONNECTOR_UNAVAILABLE"


def test_profile_bound_connector_fails_closed_above_rejection_threshold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        hits = []
        for ordinal in range(5):
            source = {
                "@timestamp": f"2026-08-29T08:00:0{ordinal}Z",
                "resource.service.name": "checkout",
                "severity.text": "INFO",
                "body": f"healthy checkout {ordinal}",
                "traceId": (
                    "not-hex" if ordinal >= 3 else f"{ordinal + 1:032x}"
                ),
            }
            hits.append({"_source": source})
        return httpx.Response(
            200,
            json={"hits": {"hits": hits, "total": {"value": 5}}},
        )

    connector = OpenSearchConnectorV1(
        _profile_config(),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    context = ConnectorQueryContextV1(
        environment_id=f"env-{'b' * 24}",
        requested_services=("checkout",),
        service_aliases={"checkout": "checkout"},
        window=ConnectorWindowV1(
            started_at=datetime(2026, 8, 29, 8, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 29, 8, 1, 0, tzinfo=UTC),
        ),
        maximum_records=5,
    )

    result = connector.query(context)[0]
    diagnostics = connector.profile_diagnostics()

    assert result.status is ReadSourceStatusV22.FAILURE_SCHEMA
    assert result.safe_error_code == "OPENSEARCH_PROFILE_RECORDS_REJECTED"
    assert diagnostics is not None
    assert diagnostics.last_query_status == "FAILURE_SCHEMA"
    assert diagnostics.last_normalization_status == "PARTIAL_SCHEMA"
    assert diagnostics.last_safe_error_code == "OPENSEARCH_PROFILE_RECORDS_REJECTED"
    assert diagnostics.last_accepted_record_count == 3
    assert diagnostics.last_rejected_record_count == 2
    assert diagnostics.last_rejection_fraction == 0.4
