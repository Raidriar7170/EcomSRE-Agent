"""Offline integration gates for the separately authorized single live campaign."""

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from ecomsre.product.errors import ProductError
from ecomsre.product.remediation.execution_contracts import RecoveryObservationV1
from ecomsre.product.remediation.window_requests import (
    RequestedRecoveryWindowProviderV1,
)
from tests.product_v040.test_executor import (
    executable as executable,
    authorized_material as authorized_material,
    api as api,
    material as material,
    FakeRecovery,
)


def test_observer_requests_follow_reserved_windows_once(executable, tmp_path):
    values, attempt, _, executor, recovery, policy = executable
    executor.run_one(attempt["attempt_id"])
    requests = tmp_path / "requests"
    requests.mkdir(mode=0o700)
    provider = RequestedRecoveryWindowProviderV1(
        recovery, requests, tmp_path / "responses", SecretStr("fixture-key")
    )
    with pytest.raises(ProductError) as denied:
        provider.reserve(started_after=values[4].clock(), policy=policy)
    assert denied.value.code == "REMEDIATION_RECOVERY_REQUEST_NO_ACTIVE_VERIFIER"

    class Observe(FakeRecovery):
        def acquire(self, **kwargs):
            request = provider.reserve(**kwargs)
            assert request.ordinal == self.calls + 1
            assert (
                request.receipt_sha256
                == recovery.receipt(attempt["attempt_id"]).receipt_sha256
            )
            with pytest.raises(FileExistsError):
                provider.reserve(**kwargs)
            return super().acquire(**kwargs)

    assert (
        recovery.verify(attempt["attempt_id"], Observe(values)).state.value
        == "RECOVERED"
    )
    assert len(list(requests.glob("*.json"))) == 2
    with pytest.raises(ProductError) as denied:
        provider.reserve(started_after=values[4].clock(), policy=policy)
    assert denied.value.code == "REMEDIATION_RECOVERY_REQUEST_NO_ACTIVE_VERIFIER"


def test_observer_failure_consumes_slot_without_replacement(executable, tmp_path):
    values, attempt, adapter, executor, recovery, _ = executable
    executor.run_one(attempt["attempt_id"])
    requests = tmp_path / "requests"
    requests.mkdir(mode=0o700)
    provider = RequestedRecoveryWindowProviderV1(
        recovery, requests, tmp_path / "responses", SecretStr("fixture-key")
    )

    class Broken(FakeRecovery):
        def acquire(self, **kwargs):
            provider.reserve(**kwargs)
            self.calls += 1
            raise OSError("observer unavailable")

    observer = Broken(values)
    result = recovery.verify(attempt["attempt_id"], observer)
    assert result.state.value == "VERIFICATION_FAILED"
    assert adapter.calls == 1 and observer.calls == 2
    assert len(list(requests.glob("*.json"))) == 2
    assert not recovery.windows(attempt["attempt_id"])
    assert recovery.verify(attempt["attempt_id"], observer) == result
    assert observer.calls == 2


def test_finalization_uses_actual_time_after_closed_window(executable):
    values, attempt, _, executor, recovery, _ = executable
    executor.run_one(attempt["attempt_id"])

    class Finalized(FakeRecovery):
        def acquire(self, **kwargs):
            value = super().acquire(**kwargs)
            values[0][3][0] += timedelta(seconds=2)
            return RecoveryObservationV1.build(
                **value.model_dump(exclude={"observation_sha256", "created_at"}),
                created_at=values[4].clock(),
            )

    assert (
        recovery.verify(attempt["attempt_id"], Finalized(values)).state.value
        == "RECOVERED"
    )
    first, second = recovery.windows(attempt["attempt_id"])
    assert first.created_at > first.ended_at
    assert second.started_at > first.created_at


@pytest.mark.parametrize("offset", [-1, 31])
def test_observer_cannot_backdate_or_delay_finalization(executable, offset):
    values, _, _, _, _, policy = executable
    value = FakeRecovery(values).acquire(started_after=values[4].clock(), policy=policy)
    with pytest.raises(ValueError, match="time anchors"):
        RecoveryObservationV1.build(
            **value.model_dump(exclude={"observation_sha256", "created_at"}),
            created_at=value.ended_at + timedelta(seconds=offset),
        )


def test_bootstrap_app_has_no_control_or_database_dependency(monkeypatch):
    from ecomsre.product.remediation.runtime import observation_proxy_app

    original = Path.read_bytes

    def profile(path):
        if str(path) == "/run/remediation-private/observation-proxy.json":
            return b'{"prometheus_base_url":"http://127.0.0.1:1","jaeger_base_url":"http://127.0.0.1:2","opensearch_base_url":"http://127.0.0.1:3"}'
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", profile)
    monkeypatch.delenv("ECOMSRE_REMEDIATION_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("ECOMSRE_REMEDIATION_BINDING_PATH", raising=False)
    with TestClient(observation_proxy_app()) as client:
        for path in (
            "/restore-baseline",
            "/recovery-window",
            "/state",
            "/write",
            "/read",
        ):
            assert client.post(path, json={}).status_code == 404
        assert (
            client.post(
                "/observability/prometheus/api/v1/admin/tsdb/delete_series"
            ).status_code
            == 405
        )


def test_future_finalization_is_rejected_before_persisting_window(executable):
    values, attempt, _, executor, recovery, _ = executable
    executor.run_one(attempt["attempt_id"])

    class Future(FakeRecovery):
        def acquire(self, **kwargs):
            observation = super().acquire(**kwargs)
            return RecoveryObservationV1.build(
                **observation.model_dump(exclude={"observation_sha256", "created_at"}),
                created_at=observation.ended_at + timedelta(seconds=2),
            )

    assert (
        recovery.verify(attempt["attempt_id"], Future(values)).state.value
        == "VERIFICATION_FAILED"
    )
    assert recovery.windows(attempt["attempt_id"]) == ()
    assert "WINDOW_COUNT" in recovery.evaluation(attempt["attempt_id"]).reason_codes


def test_jaeger_proxy_preserves_frozen_upstream_base_path():
    import httpx
    from fastapi import FastAPI
    from ecomsre.product.remediation.observation_proxy import (
        ObservationProxyProfileV1,
        mount_observation_proxy,
    )

    paths = []

    def upstream(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": []})

    app = FastAPI()
    mount_observation_proxy(
        app,
        ObservationProxyProfileV1(
            prometheus_base_url="http://127.0.0.1:19090",
            jaeger_base_url="http://127.0.0.1:11686",
            opensearch_base_url="http://127.0.0.1:19200",
        ),
        client=httpx.Client(transport=httpx.MockTransport(upstream)),
    )
    with TestClient(app) as client:
        assert client.get("/observability/jaeger/api/services").status_code == 200
        assert client.get("/observability/jaeger/api/traces").status_code == 200
    assert paths == ["/jaeger/ui/api/services", "/jaeger/ui/api/traces"]
