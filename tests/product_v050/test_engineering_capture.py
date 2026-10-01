"""Synthetic lifecycle and budget tests; never contact a daemon or endpoint."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest
from scripts.product_v050 import engineering_capture as ec


def put(root, name, value):
    (root / name).write_text(json.dumps(value))


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(ec, "ROOT", tmp_path)
    monkeypatch.setattr(ec, "CLEANING", False)
    put(
        tmp_path,
        "authorization.json",
        {
            "deadline": (datetime.now(UTC) + timedelta(minutes=90)).isoformat(),
            "caps": {"http_reads": 2, "rounds": 3, "deployment_starts": 1},
        },
    )
    return tmp_path


def test_budget_reserves_attempts_even_without_success(root, monkeypatch):
    ec.reserve("http_reads", {"failed": True})
    ec.reserve("http_reads", {"failed": True})
    with pytest.raises(ValueError, match="CAP"):
        ec.reserve("http_reads", {})
    monkeypatch.setattr(ec, "CLEANING", True)
    for _ in range(60):
        ec.reserve("docker_read", {})
    with pytest.raises(ValueError, match="DOCKER_READ_CAP"):
        ec.reserve("docker_read", {})
    ec.reserve("deployment_starts", {})
    with pytest.raises(ValueError, match="CAP"):
        ec.reserve("deployment_starts", {})


def test_projection_never_persists_environment_or_command(root):
    row = {
        "Id": "c",
        "Config": {
            "Env": ["OTEL_SERVICE_NAME=kafka", "SECRET=never-export"],
            "Labels": {},
            "Cmd": ["secret-command"],
        },
        "Path": "secret-command",
        "Args": ["secret-arg"],
    }
    encoded = json.dumps(ec.project({"container": [row]}))
    assert (
        "never-export" not in encoded
        and "secret-command" not in encoded
        and "secret-arg" not in encoded
    )
    assert "OTEL_SERVICE_NAME" in encoded


@pytest.mark.parametrize("mismatch", [None, "label", "name", "birth", "network"])
def test_partial_creation_cleanup_requires_exact_ownership(root, monkeypatch, mismatch):
    labels = {"goal": "g", "attempt": "a"}
    put(root, "manifest.json", {"labels": labels})
    put(
        root,
        "declared-resources.json",
        {"container": ["owned"], "network": ["owned-net"], "volume": []},
    )
    put(
        root,
        "precheck.json",
        {"inventory": {"container": {}, "network": {}, "volume": {}}},
    )
    container = {
        "Id": "c",
        "Name": "/owned",
        "Created": "now",
        "Config": {"Labels": dict(labels)},
    }
    network = {
        "Id": "n",
        "Name": "owned-net",
        "Created": "now",
        "Labels": dict(labels),
        "Containers": {},
    }
    rows = {"container": [container], "network": [network], "volume": []}
    if mismatch == "label":
        container["Config"]["Labels"]["attempt"] = "other"

        # Real snapshot rejects changed/foreign resources before cleanup.
        def blocked(_):
            raise ValueError("NON_OWNED_DRIFT")

        monkeypatch.setattr(ec, "snapshot", blocked)
    else:
        if mismatch == "name":
            container["Name"] = "/other"
        if mismatch == "birth":
            put(
                root,
                "births.json",
                {
                    "container": [{**container, "Created": "different"}],
                    "network": [network],
                    "volume": [],
                },
            )
        if mismatch == "network":
            network["Containers"] = {"foreign": {}}
        monkeypatch.setattr(
            ec,
            "snapshot",
            lambda tag: (
                rows
                if tag.startswith("precleanup-")
                else {"container": [], "network": [], "volume": []}
            ),
        )
    calls = []
    monkeypatch.setattr(ec, "docker", lambda args, **kw: calls.append(args))
    if mismatch:
        with pytest.raises(ValueError):
            ec.cleanup()
        assert calls == []
    else:
        ec.cleanup()
        assert calls == [
            ["stop", "--time", "10", "c"],
            ["container", "rm", "c"],
            ["network", "rm", "n"],
        ]
        assert json.loads((root / "cleanup.json").read_text())["clean"]


def test_http_rejects_external_endpoint_before_request(root):
    with pytest.raises(ValueError, match="ENDPOINT_NOT_ALLOWLISTED"):
        ec.http_get("https://example.com", {}, Path("unused"))


def test_optional_reads_cannot_spend_cleanup_reserve(root):
    for _ in range(47):
        ec.reserve("docker_read", {})
    with pytest.raises(ValueError, match="CLEANUP_RESERVE"):
        ec.reserve("docker_read", {})
    with pytest.raises(ValueError, match="CLEANUP_RESERVE"):
        ec.ensure_capacity(1, 1)


def test_late_optional_work_stops_before_cleanup_deadline(root, monkeypatch):
    auth = ec.load(root / "authorization.json")
    auth["deadline"] = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    put(root, "authorization.json", auth)
    with pytest.raises(ValueError, match="TIME_CAP"):
        ec.reserve("docker_read", {})
    monkeypatch.setattr(ec, "CLEANING", True)
    ec.reserve("docker_read", {})


def test_cleanup_never_claims_clean_for_late_owned_resource(root, monkeypatch):
    labels = {"goal": "g", "attempt": "a"}
    put(root, "manifest.json", {"labels": labels})
    put(
        root,
        "declared-resources.json",
        {"container": ["owned"], "network": [], "volume": []},
    )
    put(
        root,
        "precheck.json",
        {"inventory": {"container": {}, "network": {}, "volume": {}}},
    )
    empty = {"container": [], "network": [], "volume": []}
    late = {**empty, "container": [{"Id": "late", "Config": {"Labels": labels}}]}
    monkeypatch.setattr(
        ec, "snapshot", lambda tag: empty if tag.startswith("precleanup-") else late
    )
    monkeypatch.setattr(
        ec, "docker", lambda *args, **kw: pytest.fail("no declared resource to delete")
    )
    with pytest.raises(ValueError, match="OWNED_REMAINING"):
        ec.cleanup()
    assert not (root / "cleanup.json").exists()


def test_process_projection_handles_options_without_exporting_secrets():
    from scripts.product_v050.engineering_configuration import (
        selected_process_configuration,
    )

    result = selected_process_configuration(
        "/bin/java\0-javaagent:/tmp/opentelemetry-javaagent.jar\0JAVA_TOOL_OPTIONS=-Dsecret=never -Dotel.metric.export.interval=120000\0SECRET=never\0"
    )
    assert (
        result["allowed_system_properties"]["otel.metric.export.interval"] == "120000"
    )
    assert "never" not in json.dumps(result)


def test_jaeger_uses_retained_path_and_preserves_unavailable(root, monkeypatch):
    import httpx

    seen = []
    transport = httpx.MockTransport(
        lambda request: (
            seen.append(str(request.url)) or httpx.Response(404, text="missing")
        )
    )
    monkeypatch.setattr(
        ec.httpx,
        "stream",
        lambda method, url, **kw: httpx.Client(transport=transport).stream(
            method, url, params=kw.get("params")
        ),
    )
    result = ec.http_get(
        "http://127.0.0.1:16686/jaeger/ui/api/traces",
        {"service": "payment"},
        root / "trace.json",
    )
    assert result is None
    assert json.loads((root / "trace.json").read_text())["status"] == 404
    assert "/jaeger/ui/api/traces" in seen[0]
    with pytest.raises(ValueError, match="ENDPOINT"):
        ec.http_get("http://127.0.0.1:16686/api/traces", {}, root / "wrong.json")
