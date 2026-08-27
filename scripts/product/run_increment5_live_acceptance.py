"""Run the Product MVP against one evaluator-owned, no-fault OTel Demo."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from typing import Any

import httpx

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.product.live_acceptance import LiveReadOnlyAcceptanceV1


PRODUCT_PROJECT = "ecomsre-product-mvp-v01"
PRODUCT_LABEL_KEY = "io.ecomsre.product"
PRODUCT_LABEL_VALUE = "ecomsre-product-mvp-v01"
PRODUCT_LABEL = f"{PRODUCT_LABEL_KEY}={PRODUCT_LABEL_VALUE}"
PRODUCT_IMAGE = "ecomsre-product-mvp-v01:local"
PRODUCT_PORT = 18081
_SUCCESS = {"SUCCESS_EMPTY", "SUCCESS_NONEMPTY"}
_PRODUCT_RESOURCE_KINDS = ("container", "network", "volume")
_PRODUCT_RESOURCE_NAMES = {
    "container": frozenset(
        {
            "ecomsre-product-mvp-v01-api-1",
            "ecomsre-product-mvp-v01-worker-1",
        }
    ),
    "network": frozenset({"ecomsre-product-mvp-v01-default"}),
    "volume": frozenset(
        {"ecomsre-product-mvp-v01_ecomsre-product-data"}
    ),
}
ProductInventory = tuple[frozenset[str], frozenset[str], frozenset[str]]


def _write_sha_bound_json(
    private_root: Path,
    filename: str,
    payload: Mapping[str, object],
) -> Path:
    bound = dict(payload)
    bound["report_sha256"] = hashlib.sha256(
        json.dumps(
            bound,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    output = private_root / "report" / filename
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.write_text(
        json.dumps(bound, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output.chmod(0o600)
    return output


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: float = 600,
) -> str:
    completed = subprocess.run(
        list(arguments),
        cwd=cwd,
        env=None if environment is None else dict(environment),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"allowlisted command failed: {arguments[0]} "
            f"{completed.stderr.strip()[:500]}"
        )
    return completed.stdout


def _ids(root: Path, arguments: Sequence[str]) -> frozenset[str]:
    return frozenset(item for item in _run(arguments, cwd=root).splitlines() if item)


def _resource_snapshot(root: Path) -> ProductInventory:
    return (
        _ids(root, ("docker", "ps", "-aq")),
        _ids(root, ("docker", "network", "ls", "-q")),
        _ids(root, ("docker", "volume", "ls", "-q")),
    )


def _product_ids(root: Path, kind: str) -> frozenset[str]:
    common = (
        "--filter",
        f"label=com.docker.compose.project={PRODUCT_PROJECT}",
        "--filter",
        f"label={PRODUCT_LABEL}",
    )
    if kind == "container":
        return _ids(root, ("docker", "ps", "-aq", *common))
    if kind == "network":
        return _ids(root, ("docker", "network", "ls", "-q", *common))
    if kind == "volume":
        return _ids(root, ("docker", "volume", "ls", "-q", *common))
    raise ValueError("unsupported Product Docker resource kind")


def _namespace_ids(root: Path, kind: str, *, label: str) -> frozenset[str]:
    common = ("--filter", f"label={label}")
    if kind == "container":
        return _ids(root, ("docker", "ps", "-aq", *common))
    if kind == "network":
        return _ids(root, ("docker", "network", "ls", "-q", *common))
    if kind == "volume":
        return _ids(root, ("docker", "volume", "ls", "-q", *common))
    raise ValueError("unsupported Product Docker resource kind")


def _project_ids(root: Path, kind: str) -> frozenset[str]:
    return _namespace_ids(
        root,
        kind,
        label=f"com.docker.compose.project={PRODUCT_PROJECT}",
    )


def _custom_label_ids(root: Path, kind: str) -> frozenset[str]:
    return _namespace_ids(root, kind, label=PRODUCT_LABEL)


def _product_inventory(root: Path) -> ProductInventory:
    return tuple(_product_ids(root, kind) for kind in _PRODUCT_RESOURCE_KINDS)  # type: ignore[return-value]


def _product_resource_names(root: Path, kind: str) -> frozenset[str]:
    filters = (
        "--filter",
        f"label=com.docker.compose.project={PRODUCT_PROJECT}",
        "--filter",
        f"label={PRODUCT_LABEL}",
    )
    if kind == "container":
        command = ("docker", "ps", "-a", *filters, "--format", "{{.Names}}")
    elif kind == "network":
        command = ("docker", "network", "ls", *filters, "--format", "{{.Name}}")
    elif kind == "volume":
        command = ("docker", "volume", "ls", *filters, "--format", "{{.Name}}")
    else:
        raise ValueError("unsupported Product Docker resource kind")
    return _ids(root, command)


def _discover_exact_product_inventory(root: Path) -> ProductInventory | None:
    inventory = _product_inventory(root)
    expected_counts = (2, 1, 1)
    if tuple(len(items) for items in inventory) != expected_counts:
        return None
    if any(
        _project_ids(root, kind) != inventory[index]
        or _custom_label_ids(root, kind) != inventory[index]
        or _product_resource_names(root, kind) != _PRODUCT_RESOURCE_NAMES[kind]
        for index, kind in enumerate(_PRODUCT_RESOURCE_KINDS)
    ):
        return None
    return inventory


def _product_namespace_is_empty(root: Path) -> bool:
    return not _any_product_namespace_resource(root) and not _has_product_name_collision(
        root
    )


def _exact_resource_names(root: Path, kind: str) -> frozenset[str]:
    if kind == "container":
        command = ("docker", "ps", "-a", "--format", "{{.Names}}")
    elif kind == "network":
        command = ("docker", "network", "ls", "--format", "{{.Name}}")
    elif kind == "volume":
        command = ("docker", "volume", "ls", "--format", "{{.Name}}")
    else:
        raise ValueError("unsupported Product Docker resource kind")
    expected = _PRODUCT_RESOURCE_NAMES[kind]
    return frozenset(_ids(root, command).intersection(expected))


def _has_product_name_collision(root: Path) -> bool:
    return any(
        _exact_resource_names(root, kind)
        for kind in _PRODUCT_RESOURCE_KINDS
    )


def _product_image_owner(root: Path) -> str | None:
    completed = subprocess.run(
        (
            "docker",
            "image",
            "inspect",
            "--format",
            f'{{{{ index .Config.Labels "{PRODUCT_LABEL_KEY}" }}}}',
            PRODUCT_IMAGE,
        ),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        if "no such image" in completed.stderr.lower():
            return None
        raise RuntimeError("Product image namespace inspection failed")
    return completed.stdout.strip()


def _require_product_image_namespace(root: Path, *, allow_absent: bool) -> None:
    owner = _product_image_owner(root)
    if owner is None and allow_absent:
        return
    if owner != PRODUCT_LABEL_VALUE:
        raise RuntimeError("Product image tag is not owned by this Product namespace")


def _any_product_namespace_resource(root: Path) -> bool:
    filters = (
        "--filter",
        f"label=com.docker.compose.project={PRODUCT_PROJECT}",
    )
    product_filters = ("--filter", f"label={PRODUCT_LABEL}")
    commands = (
        ("docker", "ps", "-aq"),
        ("docker", "network", "ls", "-q"),
        ("docker", "volume", "ls", "-q"),
    )
    return any(
        _ids(root, (*command, *filters)) or _ids(root, (*command, *product_filters))
        for command in commands
    )


def _require_port_available(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
    except OSError as error:
        raise RuntimeError(f"Product loopback port is occupied: {port}") from error
    finally:
        probe.close()


def _compose_environment(token: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "ECOMSRE_ADMIN_TOKEN": token,
            "ECOMSRE_PRODUCT_API_PORT": str(PRODUCT_PORT),
        }
    )
    return environment


def _start_product(root: Path, token: str) -> ProductInventory:
    if _any_product_namespace_resource(root) or _has_product_name_collision(root):
        raise RuntimeError(
            "Product Compose namespace or exact resource name is not empty before startup"
        )
    _require_product_image_namespace(root, allow_absent=True)
    _require_port_available(PRODUCT_PORT)
    environment = _compose_environment(token)
    compose = ("docker", "compose", "-f", str(root / "docker-compose.product.yml"))
    _run(
        (*compose, "build", "--pull=false"),
        cwd=root,
        environment=environment,
        timeout_seconds=900,
    )
    _require_product_image_namespace(root, allow_absent=False)
    _run(
        (*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180"),
        cwd=root,
        environment=environment,
        timeout_seconds=240,
    )
    inventory = _discover_exact_product_inventory(root)
    if inventory is None:
        raise RuntimeError("Product Compose namespace contains an unknown resource")
    counts = dict(
        zip(_PRODUCT_RESOURCE_KINDS, (len(items) for items in inventory), strict=True)
    )
    if counts != {"container": 2, "network": 1, "volume": 1}:
        raise RuntimeError("Product Compose ownership inventory is incomplete")
    return inventory


def _cleanup_product(
    root: Path,
    token: str,
    baseline: ProductInventory,
    expected: ProductInventory,
) -> str:
    if any(
        _project_ids(root, kind) != expected[index]
        or _custom_label_ids(root, kind) != expected[index]
        or _product_ids(root, kind) != expected[index]
        for index, kind in enumerate(_PRODUCT_RESOURCE_KINDS)
    ):
        return "BLOCKED"
    environment = _compose_environment(token)
    compose = ("docker", "compose", "-f", str(root / "docker-compose.product.yml"))
    _run(
        (*compose, "down", "--volumes", "--timeout", "30"),
        cwd=root,
        environment=environment,
        timeout_seconds=180,
    )
    counts = tuple(len(_product_ids(root, kind)) for kind in _PRODUCT_RESOURCE_KINDS)
    return "CLEAN" if counts == (0, 0, 0) and _resource_snapshot(root) == baseline else "BLOCKED"


def _cleanup_product_after_attempt(
    root: Path,
    token: str,
    baseline: ProductInventory,
    frozen_inventory: ProductInventory | None,
) -> str:
    inventory = frozen_inventory or _discover_exact_product_inventory(root)
    if inventory is not None:
        return _cleanup_product(root, token, baseline, inventory)
    if _product_namespace_is_empty(root) and _resource_snapshot(root) == baseline:
        return "CLEAN"
    return "BLOCKED"


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    payload: object | None = None,
) -> dict[str, Any]:
    response = client.request(method, path, json=payload)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(
            f"Product API request failed: {method} {path} "
            f"status={response.status_code} body={response.text[:500]}"
        )
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("Product API response is not an object")
    return value


def _wait_job(
    client: httpx.Client,
    job_id: str,
    *,
    timeout_seconds: float = 300,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = _request_json(client, "GET", f"/v1/jobs/{job_id}")
        if job.get("status") == "SUCCEEDED":
            result = job.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("succeeded Product job lacks a result")
            return result
        if job.get("status") in {"FAILED", "CANCELLED"}:
            raise RuntimeError(
                f"Product job failed safely: {job.get('safe_error_code', 'UNKNOWN')}"
            )
        time.sleep(1)
    raise TimeoutError(f"Product job did not complete: {job_id}")


def _environment_payload(root: Path) -> dict[str, Any]:
    raw = json.loads(
        (root / "examples/product/environment.otel-demo.json").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(raw, dict):
        raise RuntimeError("Product OTel environment example is not an object")
    return raw


def _exercise_product(root: Path, token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(
        base_url=f"http://127.0.0.1:{PRODUCT_PORT}",
        headers=headers,
        timeout=30,
    ) as client:
        ready = _request_json(client, "GET", "/readyz")
        if ready.get("status") != "ready":
            raise RuntimeError("Product API is not ready")
        environment = _request_json(
            client,
            "POST",
            "/v1/environments",
            payload=_environment_payload(root),
        )
        environment_id = str(environment["environment_id"])
        verify_job = _request_json(
            client,
            "POST",
            f"/v1/environments/{environment_id}/verify-jobs",
        )
        verification = _wait_job(client, str(verify_job["job_id"]))
        health = verification.get("connector_health")
        identity_map = verification.get("service_identity_map")
        if not isinstance(health, list) or not isinstance(identity_map, dict):
            raise RuntimeError("Product verification result is incomplete")
        sources = {
            str(item["kind"]): str(item["status"])
            for item in health
            if isinstance(item, dict)
        }
        services_raw = identity_map.get("services")
        if not isinstance(services_raw, list):
            raise RuntimeError("Product service identity map is unavailable")
        logical_services = tuple(
            sorted(
                str(item["logical_service"])
                for item in services_raw
                if isinstance(item, dict)
            )
        )
        payment = next(
            (
                item
                for item in services_raw
                if isinstance(item, dict) and item.get("logical_service") == "payment"
            ),
            None,
        )
        if not isinstance(payment, dict):
            raise RuntimeError("verified Product catalog lacks payment")

        baseline_job = _request_json(
            client,
            "POST",
            f"/v1/environments/{environment_id}/baseline-jobs",
            payload={
                "build_policy": {
                    "mode": "DEMO_ONLY",
                    "lookback_seconds": 180,
                    "window_count": 5,
                    "minimum_successful_windows": 1,
                    "warmup_seconds": 180,
                },
                "activate": True,
            },
        )
        baseline = _wait_job(
            client,
            str(baseline_job["job_id"]),
            timeout_seconds=600,
        )
        observed_at = datetime.now(UTC)
        incident = _request_json(
            client,
            "POST",
            "/v1/incidents",
            payload={
                "environment_id": environment_id,
                "external_incident_key": "live-read-only-no-fault-v1",
                "alert_name": "live-read-only-observation",
                "summary": "Evaluator-controlled no-fault Product acceptance.",
                "started_at": observed_at.isoformat(),
                "ended_at": observed_at.isoformat(),
                "candidate_service_ids": [str(payment["service_id"])],
                "labels": {"mode": "live-read-only", "fault": "none"},
            },
        )
        incident_id = str(incident["incident_id"])
        diagnosis_job = _request_json(
            client,
            "POST",
            f"/v1/incidents/{incident_id}/diagnosis-jobs",
        )
        diagnosis = _wait_job(client, str(diagnosis_job["job_id"]))
        evidence = _request_json(client, "GET", f"/v1/incidents/{incident_id}/evidence")
        objects = evidence.get("objects")
        if not isinstance(objects, list) or not objects:
            raise RuntimeError("Product evidence bundle is empty")
        object_refs = {
            str(item["evidence_ref"])
            for item in objects
            if isinstance(item, dict) and item.get("evidence_ref")
        }
        linked_refs = {
            str(item)
            for key in ("supporting_evidence_refs", "contradicting_evidence_refs")
            for item in evidence.get(key, [])
        }
        explicit_failures: set[str] = set()
        for item in objects:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            action = payload.get("action")
            action_id = str(action.get("action_id", "unknown")) if isinstance(action, dict) else "unknown"
            components = payload.get("connector_components")
            if not isinstance(components, list):
                continue
            for component in components:
                if not isinstance(component, dict) or component.get("status") in _SUCCESS:
                    continue
                explicit_failures.add(
                    ":".join(
                        (
                            str(component.get("source", "UNKNOWN")),
                            str(component.get("safe_error_code", "UNKNOWN")),
                            action_id,
                        )
                    )
                )
        metrics = client.get("/metrics")
        metrics.raise_for_status()
        if "ecomsre_diagnosis_terminals_total" not in metrics.text:
            raise RuntimeError("Product operational metrics are unavailable")
        return {
            "sources": sources,
            "normalized_services": logical_services,
            "environment_id": environment_id,
            "baseline_id": str(baseline["baseline_id"]),
            "baseline_mode": str(baseline["build_policy"]["mode"]),
            "successful_baseline_windows": int(baseline["successful_windows"]),
            "incident_id": incident_id,
            "diagnosis_terminal": str(diagnosis["terminal"]),
            "evidence_object_count": len(objects),
            "evidence_refs_resolved": linked_refs.issubset(object_refs),
            "connector_raw_failures": len(explicit_failures),
            "explicit_source_failures": tuple(sorted(explicit_failures)),
            "agent_writes": int(diagnosis["agent_writes"]),
            "runbook_executions": int(diagnosis["runbook_executions"]),
        }


def _write_live_failure_report(
    *,
    private_root: Path,
    error: Exception,
    docker_context: str | None,
    daemon_id: str | None,
    baseline_unchanged: bool,
    product_cleanup: str,
    demo_cleanup: CleanupObservation | None,
    demo_cleanup_error: Exception | None,
) -> Path:
    return _write_sha_bound_json(
        private_root,
        "product-live-read-only-failure.json",
        {
            "schema_version": "ecomsre.product.live-read-only-failure.v1",
            "terminal": "BLOCKED_ECOMSRE_PRODUCT_LIVE_ACCEPTANCE",
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "docker_context": docker_context,
            "docker_daemon_id_sha256": (
                None
                if daemon_id is None
                else hashlib.sha256(daemon_id.encode("utf-8")).hexdigest()
            ),
            "error_type": type(error).__name__,
            "safe_error": str(error)[:500],
            "baseline_unchanged": baseline_unchanged,
            "product_cleanup": product_cleanup,
            "demo_cleanup": None if demo_cleanup is None else demo_cleanup.verdict,
            "demo_cleanup_error": (
                None
                if demo_cleanup_error is None
                else type(demo_cleanup_error).__name__
            ),
            "owned_containers_after_cleanup": (
                None if demo_cleanup is None else demo_cleanup.owned_containers
            ),
            "owned_networks_after_cleanup": (
                None if demo_cleanup is None else demo_cleanup.owned_networks
            ),
            "owned_volumes_after_cleanup": (
                None if demo_cleanup is None else demo_cleanup.owned_volumes
            ),
            "non_owned_resources_changed": (
                None if demo_cleanup is None else demo_cleanup.non_owned_resources_changed
            ),
            "fault_injections": 0,
            "forward_mutations": 0,
        },
    )


def _finalize_live_acceptance(
    *,
    private_root: Path,
    failure: Exception | None,
    docker_context: str | None,
    daemon_id: str | None,
    product_result: dict[str, Any] | None,
    baseline_unchanged: bool,
    product_cleanup: str,
    demo_cleanup: CleanupObservation | None,
    demo_cleanup_error: Exception | None,
) -> LiveReadOnlyAcceptanceV1:
    try:
        if failure is not None:
            raise failure
        if docker_context is None or daemon_id is None:
            raise RuntimeError("live Docker identity is unavailable")
        if demo_cleanup is None:
            raise RuntimeError("live Demo cleanup result is unavailable")
        if product_result is None or demo_cleanup.verdict != "CLEAN":
            raise RuntimeError("live Product acceptance did not close cleanly")
        report = LiveReadOnlyAcceptanceV1.build(
            observed_at=datetime.now(UTC),
            docker_context=docker_context,
            docker_daemon_id_sha256=hashlib.sha256(
                daemon_id.encode("utf-8")
            ).hexdigest(),
            **product_result,
            fault_injections=0,
            forward_mutations=0,
            product_cleanup=product_cleanup,
            demo_cleanup=demo_cleanup.verdict,
            owned_containers_after_cleanup=demo_cleanup.owned_containers,
            owned_networks_after_cleanup=demo_cleanup.owned_networks,
            owned_volumes_after_cleanup=demo_cleanup.owned_volumes,
            non_owned_resources_changed=demo_cleanup.non_owned_resources_changed,
        )
    except Exception as error:
        _write_live_failure_report(
            private_root=private_root,
            error=error,
            docker_context=docker_context,
            daemon_id=daemon_id,
            baseline_unchanged=baseline_unchanged,
            product_cleanup=product_cleanup,
            demo_cleanup=demo_cleanup,
            demo_cleanup_error=demo_cleanup_error,
        )
        raise
    output = private_root / "report" / "product-live-read-only-acceptance.json"
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    output.chmod(0o600)
    return report


def run_acceptance(
    *,
    repository_root: Path,
    private_root: Path,
    stabilization_seconds: int = 30,
    baseline_accumulation_seconds: int = 360,
) -> LiveReadOnlyAcceptanceV1:
    root = repository_root.resolve()
    private = private_root.resolve()
    if root == Path("/") or private == Path("/") or private.is_relative_to(root):
        raise ValueError("live acceptance roots are unsafe")
    if baseline_accumulation_seconds < 360 or baseline_accumulation_seconds > 900:
        raise ValueError("live baseline accumulation must be between 360 and 900 seconds")
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private,
        stabilization_seconds=stabilization_seconds,
    )
    token = secrets.token_urlsafe(32)
    product_baseline: ProductInventory | None = None
    product_inventory: ProductInventory | None = None
    product_cleanup = "BLOCKED"
    demo_cleanup: CleanupObservation | None = None
    demo_cleanup_error: Exception | None = None
    baseline_unchanged = False
    product_result: dict[str, Any] | None = None
    before_flag: str | None = None
    failure: Exception | None = None
    lifecycle.admit()
    docker_context: str | None = None
    daemon_id: str | None = None
    try:
        docker_context = _run(("docker", "context", "show"), cwd=root).strip()
        daemon_id = _run(
            ("docker", "info", "--format", "{{.ID}}"),
            cwd=root,
        ).strip()
        lifecycle.start()
        lifecycle.wait_ready()
        lifecycle.authorize_reads()
        before_flag = lifecycle.read_baseline_sha256()
        if baseline_accumulation_seconds:
            time.sleep(baseline_accumulation_seconds)
        product_baseline = _resource_snapshot(root)
        product_inventory = _start_product(root, token)
        product_result = _exercise_product(root, token)
        after_flag = lifecycle.read_baseline_sha256()
        baseline_unchanged = before_flag == after_flag
    except Exception as error:
        failure = error
        if before_flag is not None:
            try:
                baseline_unchanged = before_flag == lifecycle.read_baseline_sha256()
            except Exception:
                baseline_unchanged = False
    finally:
        if product_baseline is not None:
            try:
                product_cleanup = _cleanup_product_after_attempt(
                    root,
                    token,
                    product_baseline,
                    product_inventory,
                )
            except Exception:
                product_cleanup = "BLOCKED"
        elif product_inventory is not None:
            product_cleanup = "BLOCKED"
        try:
            demo_cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=baseline_unchanged
            )
        except Exception as cleanup_error:
            demo_cleanup_error = cleanup_error
            if failure is None:
                failure = cleanup_error
    return _finalize_live_acceptance(
        private_root=private,
        failure=failure,
        docker_context=docker_context,
        daemon_id=daemon_id,
        product_result=product_result,
        baseline_unchanged=baseline_unchanged,
        product_cleanup=product_cleanup,
        demo_cleanup=demo_cleanup,
        demo_cleanup_error=demo_cleanup_error,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--stabilization-seconds", type=int, default=30)
    parser.add_argument("--baseline-accumulation-seconds", type=int, default=360)
    arguments = parser.parse_args(argv)
    try:
        report = run_acceptance(
            repository_root=arguments.repository_root,
            private_root=arguments.private_root,
            stabilization_seconds=arguments.stabilization_seconds,
            baseline_accumulation_seconds=arguments.baseline_accumulation_seconds,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "terminal": "BLOCKED_ECOMSRE_PRODUCT_LIVE_ACCEPTANCE",
                    "error_type": type(error).__name__,
                    "safe_error": str(error)[:500],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(report.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("run_acceptance",)
