"""No Docker calls: fixed runtime targeting, build inputs and business oracle."""

from types import SimpleNamespace
import json

import httpx
import pytest

from scripts.live_sandbox.product_v040 import PinnedDockerRunnerV040
from scripts.product.v040_observer import (
    HostLoopbackTransportV040,
    checkout_business_passed,
)
from scripts.product.v040_runtime import ProductRuntimeV040


def test_runtime_build_context_includes_registry_excludes_private(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repository"
    repo.mkdir()
    runtime = ProductRuntimeV040(repo)
    (runtime.private / "host").mkdir(mode=0o700, parents=True)
    names = [
        "Dockerfile.product",
        "pyproject.toml",
        "uv.lock",
        "src/nested/module.py",
        "config/product-v040/remediation-registry.v1.json",
    ]
    for name in names:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    (repo / ".dockerignore").write_text("*\n!src/**\n")
    (runtime.private / "host/secret.json").write_text("PRIVATE")
    monkeypatch.setattr(
        runtime,
        "command",
        lambda argv: "\0".join(f"100644 {'a' * 40} 0\t{name}" for name in names),
    )
    result = runtime.build_context()
    assert set(result) == set(names)
    context = runtime.private / "host/build-context"
    assert {
        str(p.relative_to(context)) for p in context.rglob("*") if p.is_file()
    } == set(names)
    assert not (context / ".dockerignore").exists()
    with pytest.raises(FileExistsError):
        runtime.build_context()


def test_sandbox_docker_runner_uses_same_fixed_context(monkeypatch, tmp_path):
    captured = []

    def execute(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("ecomsre_live_sandbox.environment.subprocess.run", execute)
    runner = PinnedDockerRunnerV040()
    runner.run(
        ("docker", "info"),
        cwd=tmp_path,
        env={
            "PATH": "/bin",
            "DOCKER_HOST": "tcp://wrong:2375",
            "DOCKER_CONTEXT": "other",
            "SANDBOX_FLAGD_DIR": "/owned",
        },
    )
    argv, options = captured[0]
    assert argv == ["docker", "--context", "desktop-linux", "info"]
    assert options["env"] == {"PATH": "/bin", "SANDBOX_FLAGD_DIR": "/owned"}


def test_product_does_not_accept_daemon_drift(tmp_path, monkeypatch):
    runtime = ProductRuntimeV040(tmp_path)
    (runtime.private / "host").mkdir(mode=0o700, parents=True)
    expected = {
        "context": "desktop-linux",
        "endpoint": "unix:///local.sock",
        "daemon_id": "old",
    }
    (runtime.private / "host/daemon.json").write_text(json.dumps(expected))
    monkeypatch.setattr(
        runtime,
        "docker",
        lambda *args: (
            json.dumps("unix:///local.sock")
            if args[0] == "context"
            else json.dumps({"OSType": "linux", "Architecture": "aarch64", "ID": "new"})
        ),
    )
    with pytest.raises(ValueError, match="drift"):
        runtime.boundary()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"orderId": "id", "items": []},
        {"orderId": "", "items": [{}]},
        {
            "orderId": "id",
            "items": [{"item": {"productId": "different", "quantity": 1}}],
        },
    ],
)
def test_business_oracle_rejects_nominal_http_success_without_order(payload):
    assert not checkout_business_passed(payload)


def test_business_oracle_requires_the_frozen_cart_item():
    assert checkout_business_passed(
        {
            "orderId": "fictional",
            "items": [{"item": {"productId": "0PUK6V6EV0", "quantity": 1}}],
        }
    )


def test_host_mapping_preserves_profile_identity_and_rejects_other_origins():
    transport = HostLoopbackTransportV040()
    with pytest.raises(ValueError, match="outside"):
        transport.handle_request(
            httpx.Request("GET", "http://example.invalid:18080/read")
        )
    with pytest.raises(ValueError, match="outside"):
        transport.handle_request(
            httpx.Request("GET", "http://host.docker.internal:2375/read")
        )
    transport.close()


def test_operation_lock_excludes_second_cleanup_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    repository = Path(__file__).resolve().parents[2]
    runtime = ProductRuntimeV040(tmp_path)
    with runtime.operation_lock():
        result = subprocess.run(
            [sys.executable, "-c", "from pathlib import Path; from scripts.product.v040_runtime import ProductRuntimeV040; "
             "runtime=ProductRuntimeV040(Path(__import__('sys').argv[1])); "
             "runtime.operation_lock().__enter__()", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=repository,
            env={"PYTHONPATH": os.pathsep.join((str(repository / "src"), str(repository)))},
            timeout=10,
        )
        assert result.returncode != 0 and "another campaign operation is active" in result.stderr
    with runtime.operation_lock():
        pass


def test_cancelled_observer_cannot_acquire_or_sample(tmp_path):
    import threading
    from scripts.product.v040_observer import LiveObserverV040
    observer = object.__new__(LiveObserverV040)
    observer.stop_event = threading.Event()
    observer.stop_event.set()
    observer.private = tmp_path
    request = SimpleNamespace(attempt_id="attempt-" + "a" * 24, ordinal=1)
    with pytest.raises(InterruptedError, match="consumed window"):
        observer.window(request, None)
    assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("mounts", [
    ["/tmp:rw", "noexec", "nosuid", "nodev", "size=16m"],
    ["/tmp:rw,noexec,nosuid,nodev,size=16m", "/extra"],
    ["/tmp:rw,noexec,nosuid,nodev,size=64m"],
])
def test_split_or_broadened_observer_tmpfs_is_rejected_before_start(mounts):
    from scripts.product.v040_runtime import SERVICES, validate_resolved_tmpfs
    plan = {"services": {name: {"tmpfs": [f"/tmp:rw,noexec,nosuid,nodev,size={64 if name in {'api','worker'} else 16}m"]} for name in SERVICES}}
    validate_resolved_tmpfs(plan)
    plan["services"]["remediation-observer"]["tmpfs"] = mounts
    with pytest.raises(ValueError, match="tmpfs mount differs"):
        validate_resolved_tmpfs(plan)


def test_demo_failure_capture_denies_unproven_ownership(tmp_path):
    from scripts.live_sandbox.product_v040 import ProductV040Lifecycle

    lifecycle = object.__new__(ProductV040Lifecycle)
    lifecycle.private_root = tmp_path
    lifecycle.repository_root = tmp_path
    calls = []

    def reject(kind, identities):
        raise ValueError("unknown ownership")

    lifecycle.environment = SimpleNamespace(
        verify_local_docker=lambda: {}, _owned_ids=lambda kind: ["unknown"],
        _inspect_labels=reject,
        runner=SimpleNamespace(run=lambda *args, **kwargs: calls.append(args)),
    )
    lifecycle.capture_failure()
    assert not calls
    assert not (tmp_path / "control/failure-diagnostics.json").exists()


def test_demo_failure_capture_keeps_partial_logs(tmp_path):
    from scripts.live_sandbox.product_v040 import ProductV040Lifecycle

    lifecycle = object.__new__(ProductV040Lifecycle)
    lifecycle.private_root = tmp_path
    lifecycle.repository_root = tmp_path
    (tmp_path / "control").mkdir(mode=0o700)
    verified = []

    def run(args, **kwargs):
        assert verified
        if args[-1] == "stopped":
            raise RuntimeError("container disappeared")
        return SimpleNamespace(stdout="retained", stderr="diagnostic")

    lifecycle.environment = SimpleNamespace(
        verify_local_docker=lambda: {},
        _owned_ids=lambda kind: ["stopped", "present"],
        _inspect_labels=lambda *args: verified.append(True),
        runner=SimpleNamespace(run=run),
    )
    lifecycle.capture_failure()
    rows = json.loads((tmp_path / "control/failure-diagnostics.json").read_text())
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[1]["stderr"] == "diagnostic"
