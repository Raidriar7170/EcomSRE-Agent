"""Authenticated gateway against a fake pinned-upstream HTTP shape."""

from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest

from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
from ecomsre.product.remediation.executor import (
    ProductPaymentConfigurationRollbackExecutor,
)
from ecomsre.product.remediation.observation_proxy import (
    ObservationProxyProfileV1,
    mount_observation_proxy,
)
from ecomsre.product.remediation.payment_control import (
    GuardedPaymentControlV1,
    LocalPaymentStateProviderV1,
    OwnershipWitnessV1,
    PrivatePaymentControlProfileV1,
    control_apps,
    digest,
)
from ecomsre.product.remediation.state import TrustedStateBindingV1
from tests.product_v040.test_approval_api import api as api, candidate, approve
from tests.product_v040.test_authorization import create
from tests.product_v040.test_candidates import material as material


@pytest.fixture
def gateway(api, tmp_path):
    item = candidate(api)
    approval = approve(api, item).json()
    baseline = {
        "$schema": "synthetic-schema",
        "flags": {
            "paymentFailure": {
                "defaultVariant": "off",
                "variants": {"off": 0, "100%": 1},
            }
        },
    }
    fault = json.loads(json.dumps(baseline))
    fault["flags"]["paymentFailure"]["defaultVariant"] = "100%"
    control_url, eval_url = "http://127.0.0.1:19901/api", "http://127.0.0.1:19902"
    configuration = {
        "flag_control_url": control_url,
        "flag_evaluation_url": eval_url,
        "baseline_configuration_digest": digest(baseline),
        "fault_configuration_digest": digest(fault),
    }
    binding = TrustedStateBindingV1.build(
        environment_id=item["environment_id"],
        environment_ownership_digest="a" * 64,
        target_identity_digest="b" * 64,
        identity_map_sha256=item["identity_map_sha256"],
        control_identity_sha256=digest(configuration),
        baseline_id=item["baseline_id"],
        baseline_sha256=item["baseline_sha256"],
        baseline_configuration_digest=digest(baseline),
        fault_configuration_digest=digest(fault),
        registry_sha256=item["registry_sha256"],
        created_at=api[3][0],
    )
    flag = tmp_path / "flags.json"
    flag.write_text(json.dumps(fault))
    witness_path = tmp_path / "ownership.json"
    key = SecretStr("o" * 32)

    def witness():
        value = OwnershipWitnessV1(
            environment_id=binding.environment_id,
            environment_ownership_digest=binding.environment_ownership_digest,
            target_identity_digest=binding.target_identity_digest,
            control_identity_sha256=binding.control_identity_sha256,
            non_owned_resources_unchanged=True,
            observed_at=api[3][0],
            signature="0" * 64,
        )
        body = value.model_dump(mode="json", exclude={"signature"})
        signature = hmac.new(
            key.get_secret_value().encode(),
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        witness_path.write_text(json.dumps({**body, "signature": signature}))

    witness()
    calls = []

    def upstream(request):
        current = json.loads(flag.read_bytes())
        if request.url.path == "/api/read":
            return httpx.Response(200, json={"flags": current["flags"]})
        if request.url.path == "/ofrep/v1/evaluate/flags/paymentFailure":
            variant = current["flags"]["paymentFailure"]["defaultVariant"]
            return httpx.Response(
                200, json={"variant": variant, "value": int(variant == "100%")}
            )
        if request.url.path == "/api/write":
            calls.append(request)
            temporary = flag.with_suffix(".new")
            temporary.write_text(json.dumps(json.loads(request.content)["data"]))
            temporary.replace(flag)
            return httpx.Response(200, json={})
        return httpx.Response(404)

    profile = PrivatePaymentControlProfileV1(
        binding=binding,
        flag_control_url=control_url,
        flag_evaluation_url=eval_url,
        flag_file=flag,
        ownership_witness_file=witness_path,
        baseline_document=baseline,
        fault_document=fault,
    )
    state = LocalPaymentStateProviderV1(
        profile,
        key,
        clock=lambda: api[3][0],
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
    )
    repo = RemediationAttemptRepositoryV1(
        api[1].state.remediation, provider=state, binding=binding
    )
    api[1].state.remediation_attempts = repo
    from ecomsre.product.remediation.recovery import RecoveryRepositoryV1
    from ecomsre.product.remediation.execution_contracts import RecoveryPolicyV1

    RecoveryRepositoryV1(repo).bind_policy(
        RecoveryPolicyV1.build(
            **{
                name: getattr(binding, name)
                for name in (
                    "environment_id",
                    "baseline_sha256",
                    "baseline_configuration_digest",
                    "fault_configuration_digest",
                    "target_identity_digest",
                    "control_identity_sha256",
                    "environment_ownership_digest",
                )
            },
            business_error_ratio_max=0.01,
            minimum_business_requests=10,
            window_seconds=10,
            created_at=binding.created_at,
        )
    )
    values = api, item, approval, state, repo
    api[3][0] += timedelta(seconds=1)
    attempt = create(values).json()
    api[3][0] += timedelta(seconds=1)
    control = GuardedPaymentControlV1(repo, state, tmp_path / "control.sqlite3")
    executor = ProductPaymentConfigurationRollbackExecutor(repo, control)
    lease = repo.claim(attempt["attempt_id"])
    committed = repo.commit_write_intent(
        attempt["attempt_id"],
        lease_owner=lease.active_lease_owner,
        lease_generation=lease.lease_generation,
    )
    dispatch, _ = executor._reserve(committed)
    return api, control, dispatch, calls, witness


def test_gateway_only_exact_write_credential_restores_once(gateway):
    _, control, dispatch, calls, _ = gateway
    read_app, write_app = control_apps(
        control, read_token=SecretStr("r" * 32), write_token=SecretStr("w" * 32)
    )
    with TestClient(read_app) as read, TestClient(write_app) as write:
        assert (
            read.get(
                "/state", headers={"Authorization": "Bearer " + "r" * 32}
            ).status_code
            == 200
        )
        assert (
            read.post(
                "/restore-payment-baseline", json=dispatch.model_dump(mode="json")
            ).status_code
            == 404
        )
        assert (
            write.post(
                "/restore-payment-baseline", json=dispatch.model_dump(mode="json")
            ).status_code
            == 403
        )
        assert (
            write.post(
                "/restore-payment-baseline",
                json=dispatch.model_dump(mode="json"),
                headers={"Authorization": "Bearer " + "r" * 32},
            ).status_code
            == 403
        )
        response = write.post(
            "/restore-payment-baseline",
            json=dispatch.model_dump(mode="json"),
            headers={"Authorization": "Bearer " + "w" * 32},
        )
        assert response.status_code == 200, response.text
        assert not response.json()["fault_still_present"] and len(calls) == 1
        assert (
            write.post(
                "/restore-payment-baseline",
                json=dispatch.model_dump(mode="json"),
                headers={"Authorization": "Bearer " + "w" * 32},
            ).status_code
            == 409
        )
        assert len(calls) == 1


def test_gateway_lease_expiry_and_invalid_requests_do_not_write(gateway):
    api, control, dispatch, calls, witness = gateway
    _, app = control_apps(
        control, read_token=SecretStr("r" * 32), write_token=SecretStr("w" * 32)
    )
    with TestClient(app) as client:
        invalid = {
            **dispatch.model_dump(mode="json"),
            "command": "private-secret-command",
        }
        response = client.post(
            "/restore-payment-baseline",
            json=invalid,
            headers={"Authorization": "Bearer " + "w" * 32},
        )
        assert response.status_code == 422 and "private-secret" not in response.text
        api[3][0] += timedelta(seconds=31)
        witness()
        assert (
            client.post(
                "/restore-payment-baseline",
                json=dispatch.model_dump(mode="json"),
                headers={"Authorization": "Bearer " + "w" * 32},
            ).status_code
            == 409
        )
    assert calls == []


def test_ownership_signature_and_state_drift_fail_closed(gateway):
    _, control, _, calls, _ = gateway
    path = control.state.profile.ownership_witness_file
    raw = json.loads(path.read_bytes())
    raw["signature"] = "0" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        control.state.read_current()
    assert calls == []


def test_proxy_has_no_arbitrary_origin_path_or_method():
    seen = []

    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    app = FastAPI()
    mount_observation_proxy(
        app,
        ObservationProxyProfileV1(
            prometheus_base_url="http://127.0.0.1:19090",
            jaeger_base_url="http://127.0.0.1:16686",
            opensearch_base_url="http://127.0.0.1:19200",
        ),
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
    )
    with TestClient(app) as client:
        assert (
            client.get(
                "/observability/prometheus/api/v1/query", params={"query": "up"}
            ).status_code
            == 200
        )
        assert (
            client.post("/observability/prometheus/api/v1/query", json={}).status_code
            == 405
        )
        assert (
            client.get("/observability/http://127.0.0.1:19901/api/write").status_code
            == 404
        )
        assert (
            client.get(
                "/observability/prometheus/api/v1/admin/tsdb/delete_series"
            ).status_code
            == 404
        )
    assert len(seen) == 1 and seen[0].method == "GET"


def test_executor_default_disabled_and_compose_isolates_write_capability():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "ecomsre.product.remediation.runtime", "executor"],
        cwd=root,
        # pytest's pythonpath setting does not propagate to a child process.
        # Supply the source tree explicitly and inherit no control credentials.
        env={"PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0 and "REMEDIATION_DISABLED" in result.stderr
    compose = (root / "docker-compose.product.yml").read_text()
    prefix, executor = compose.split("  remediation-executor:", 1)
    executor, gateway = executor.split("  remediation-control-gateway:", 1)
    assert "WRITE_TOKEN" not in prefix and "remediation-write:" not in prefix
    assert "network_mode: none" in executor and 'profiles: ["remediation"]' in executor
    assert "ports:" not in executor and "docker.sock" not in compose
    assert "read_only: true" in executor and "cap_drop: [ALL]" in executor
    assert "/runtime/payment-flags:ro" in gateway
    overlay = (root / "config/product-v040/remediation-network.v1.yml").read_text()
    assert overlay.count("networks: !override [remediation-observation]") == 2
    assert "internal: true" in overlay
    dockerfile = (root / "Dockerfile.product").read_text()
    assert (
        "chown -R ecomsre:ecomsre /var/lib/ecomsre /var/lib/remediation-control /run/remediation-read /run/remediation-write"
        in dockerfile
    )


def test_real_unix_socket_transport_with_fake_upstream(gateway, tmp_path):
    from datetime import UTC, datetime
    import threading
    import time
    import uvicorn
    from ecomsre.product.remediation.payment_control import UnixPaymentRestoreClientV1

    _, control, dispatch, calls, _ = gateway
    _, app = control_apps(
        control, read_token=SecretStr("r" * 32), write_token=SecretStr("w" * 32)
    )
    # Unix paths are length-bounded on macOS; use a short temporary test root.
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="v04-") as short:
        socket = Path(short) / "w.sock"
        server = uvicorn.Server(
            uvicorn.Config(app, uds=str(socket), access_log=False, log_level="critical")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            client = UnixPaymentRestoreClientV1(socket, SecretStr("w" * 32))
            after = client.restore_baseline(
                dispatch, expires_at=datetime.now(UTC) + timedelta(seconds=30)
            )
            assert not after.fault_still_present and len(calls) == 1
            with pytest.raises(httpx.HTTPStatusError):
                client.restore_baseline(
                    dispatch, expires_at=datetime.now(UTC) + timedelta(seconds=30)
                )
            assert len(calls) == 1
            client.client.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        assert not thread.is_alive()


def test_real_product_connectors_work_through_fixed_observation_proxy():
    from ecomsre.product.connectors.credentials import CredentialResolverV1
    from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
    from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
    from ecomsre.product.contracts import ConnectorConfigV1
    from tests.product.test_increment2_http_connectors import CONTEXT

    seen = []

    def upstream(request):
        seen.append(request)
        if request.url.path == "/api/v1/label/service_name/values":
            return httpx.Response(200, json={"status": "success", "data": ["payment"]})
        if request.url.path == "/api/v1/series":
            return httpx.Response(
                200, json={"status": "success", "data": [{"service_name": "payment"}]}
            )
        if request.url.path == "/otel-logs-*/_search":
            body = json.loads(request.content)
            if body["size"] == 0:
                return httpx.Response(
                    200,
                    json={
                        "aggregations": {"services": {"buckets": [{"key": "payment"}]}}
                    },
                )
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "total": {"value": 1, "relation": "eq"},
                        "hits": [
                            {
                                "_source": {
                                    "@timestamp": CONTEXT.window.ended_at.isoformat(),
                                    "resource": {"service": {"name": "payment"}},
                                    "severity": {"text": "ERROR"},
                                    "body": "payment request failed",
                                    "traceId": "fixture-trace",
                                }
                            }
                        ],
                    }
                },
            )
        return httpx.Response(404)

    app = FastAPI()
    profile = ObservationProxyProfileV1(
        prometheus_base_url="http://127.0.0.1:19090",
        jaeger_base_url="http://127.0.0.1:16686",
        opensearch_base_url="http://127.0.0.1:19200",
    )
    mount_observation_proxy(
        app, profile, client=httpx.Client(transport=httpx.MockTransport(upstream))
    )
    with TestClient(app) as proxy:

        def transport(request):
            response = proxy.request(
                request.method,
                request.url.path,
                params=request.url.params,
                content=request.content,
            )
            return httpx.Response(response.status_code, content=response.content)

        resolver = CredentialResolverV1(environment={})
        prometheus = PrometheusConnectorV1(
            ConnectorConfigV1(
                name="metrics",
                kind="PROMETHEUS",
                endpoint="http://proxy/observability/prometheus",
                settings={
                    "query_templates": {
                        name: name + '{service_name="{service}"}'
                        for name in (
                            "request_support",
                            "error_rate",
                            "latency",
                            "cpu",
                            "memory",
                        )
                    },
                    "service_label": "service_name",
                },
            ),
            credential_resolver=resolver,
            timeout_seconds=2,
            transport=httpx.MockTransport(transport),
        )
        assert prometheus.verify().status.value == "AVAILABLE"
        assert prometheus.query_series(
            matcher='{service_name="payment"}', window=CONTEXT.window
        ) == ({"service_name": "payment"},)
        logs = OpenSearchConnectorV1(
            ConnectorConfigV1(
                name="logs",
                kind="OPENSEARCH",
                endpoint="http://proxy/observability/opensearch",
                settings={
                    "mode": "LEGACY_EXPLICIT_FIELDS",
                    "index_pattern": "otel-logs-*",
                    "timestamp_field": "@timestamp",
                    "service_field": "resource.service.name",
                    "service_query_field": "resource.service.name.keyword",
                    "severity_field": "severity.text",
                    "message_field": "body",
                    "trace_id_field": "traceId",
                    "message_projection_policy": "OBSERVER_SYMPTOM_V1",
                    "maximum_result_count": 200,
                },
            ),
            credential_resolver=resolver,
            timeout_seconds=2,
            transport=httpx.MockTransport(transport),
        )
        assert logs.verify().status.value == "AVAILABLE"
        result = logs.query(CONTEXT)[0]
        assert (
            result.status.value == "SUCCESS_NONEMPTY"
            and result.records[0].message == "payment request failed"
        )
        count = len(seen)
        for path, body in (
            (
                "/observability/opensearch/otel-logs-*/_delete_by_query",
                {"query": {"match_all": {}}},
            ),
            (
                "/observability/opensearch/otel-logs-*/_search",
                {"size": 1, "script": "forbidden"},
            ),
            (
                "/observability/opensearch/otel-logs-*/_search",
                {
                    "size": 0,
                    "aggs": {
                        "services": {
                            "terms": {
                                "field": "resource.service.name.keyword",
                                "size": 201,
                            }
                        }
                    },
                },
            ),
        ):
            assert proxy.post(path, json=body).status_code in {404, 405, 422}
        assert len(seen) == count
