"""Evidence-backed admission gates for the one local Payment campaign."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import stat
from typing import Any

from ecomsre.product.baselines import BaselineRepositoryV1
from scripts.ci.verify_product_v040_history import verify as verify_history
from ecomsre.product.remediation.live_evidence import LiveCountsV040, LiveManifestV040
from ecomsre.product.remediation.execution_contracts import RecoveryPolicyV1
from ecomsre.product.remediation.repository import REGISTRY_SHA256
from scripts.product.v040_observer import LiveObserverV040
from scripts.product.v040_runtime import (
    ProductRuntimeV040,
    read_json,
    seal_private,
    digest,
)


NETWORK_PROBE = r"""
import json, os, socket
from pathlib import Path
import httpx
import sys
targets = json.loads(sys.argv[1])
result = {"denied": [], "credential_isolation": True, "write_mount_absent": True}
for host, port in targets:
    try:
        stream = socket.create_connection((host, port), timeout=2)
        stream.close()
        result["denied"].append(False)
    except OSError:
        result["denied"].append(True)
result["credential_isolation"] = not any(os.environ.get(key) for key in ("ECOMSRE_REMEDIATION_WRITE_TOKEN", "ECOMSRE_REMEDIATION_WINDOW_TOKEN", "ECOMSRE_REMEDIATION_OBSERVER_TOKEN"))
result["write_mount_absent"] = not any(Path(path).exists() for path in ("/run/remediation-write/control.sock", "/run/remediation-private/profile.json", "/var/lib/remediation-control/dispatch.sqlite3", "/var/run/docker.sock"))
with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
    result["proxy_denies_control"] = client.post("http://remediation-observer:8081/observability/flag/write", json={}).status_code in (404, 405)
    result["proxy_read_works"] = client.get("http://remediation-observer:8081/observability/jaeger/api/services").status_code == 200
if os.environ.get("ECOMSRE_REMEDIATION_READ_TOKEN"):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/run/remediation-read/control.sock", retries=0), base_url="http://control", timeout=5) as client:
        response = client.post("/recovery-window", headers={"Authorization": "Bearer " + os.environ["ECOMSRE_REMEDIATION_READ_TOKEN"]}, json={"started_after": "2026-09-05T00:00:00Z", "policy_sha256": "0"*64})
        result["read_token_cannot_request_window"] = response.status_code == 403
print(json.dumps(result))
"""


def private_storage_modes(runtime: ProductRuntimeV040) -> dict[str, Any]:
    """Measure Product-owned raw storage permissions, without chmod repairs."""
    files = directories = wal = shm = databases = 0
    for root in (runtime.private / "product", runtime.private / "ledger"):
        for path in (root, *root.rglob("*")):
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path.name.endswith(("-wal", "-shm")):
                    continue  # A closed SQLite connection may remove its sidecars.
                raise
            if stat.S_ISDIR(info.st_mode):
                directories += 1
                expected = 0o700
            elif stat.S_ISREG(info.st_mode):
                files += 1
                databases += path.name.endswith(".sqlite3")
                wal += path.name.endswith("-wal")
                shm += path.name.endswith("-shm")
                expected = 0o600
            else:
                raise ValueError("private raw storage contains an unexpected object")
            if stat.S_IMODE(info.st_mode) != expected:
                raise ValueError("private raw storage permissions differ")
    if databases < 1:
        raise ValueError("private Product database is not observed")
    return {"status": "PASS", "regular_files": files, "directories": directories,
            "databases": databases, "wal_files": wal, "shm_files": shm}


def network_denial(
    runtime: ProductRuntimeV040, observer: LiveObserverV040
) -> dict[str, Any]:
    observer.witness()
    observer.host_state.read_current()
    env = observer.lifecycle.environment
    rows = json.loads(
        runtime.docker("container", "inspect", *sorted(env._owned_ids("container")))
    )
    by_service = {
        row["Config"]["Labels"]["com.docker.compose.service"]: row for row in rows
    }
    targets = [("host.docker.internal", 18080), ("host.docker.internal", 18016)]
    for service, port in (("frontend-proxy", 8080), ("flagd", 8016)):
        networks = by_service[service]["NetworkSettings"]["Networks"]
        ip = networks["ecomsre-live-sandbox-v1-default"]["IPAddress"]
        targets.append((ip, port))
    result: dict[str, Any] = {}
    for service in ("api", "worker"):
        value = json.loads(
            runtime.compose(
                "exec",
                "-T",
                service,
                "python",
                "-c",
                NETWORK_PROBE,
                json.dumps(targets),
                timeout=40,
            )
        )
        if not all(value["denied"]) or not all(
            v for key, v in value.items() if key != "denied"
        ):
            seal_private(
                runtime.private / "host/network-denial-failed.json",
                {"service": service, "result": value},
            )
            raise ValueError("actual API/Worker isolation failed")
        result[service] = value
    owned = runtime.owned()
    product_rows = json.loads(
        runtime.docker("container", "inspect", *owned["container"])
    )
    executors = [
        row
        for row in product_rows
        if row["Config"]["Labels"]["com.docker.compose.service"]
        == "remediation-executor"
    ]
    if (
        len(executors) != 1
        or executors[0]["HostConfig"]["NetworkMode"] != "none"
        or executors[0]["HostConfig"]["Privileged"]
        or not executors[0]["HostConfig"]["ReadonlyRootfs"]
    ):
        raise ValueError("actual executor isolation differs")
    result["executor"] = {
        "network_none": True,
        "rootfs_readonly": True,
        "privileged": False,
    }
    result["private_storage_permissions"] = private_storage_modes(runtime)
    result["status"] = "PASS"
    seal_private(runtime.private / "host/network-denial.json", result)
    return result


def counts(observer: LiveObserverV040) -> LiveCountsV040:
    repo = observer.recovery.attempts
    with repo.store.connect() as connection:
        tables = {
            name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in (
                "remediation_authorizations",
                "remediation_write_intents",
                "remediation_executor_dispatches",
                "remediation_step_receipts",
                "remediation_recovery_windows",
            )
        }
        attempts = connection.execute(
            "SELECT payload_json FROM remediation_attempts"
        ).fetchall()
    forward: int | None = 0
    for row in attempts:
        value = json.loads(row[0])["forward_write_count"]
        if value is None:
            forward = None
            break
        assert forward is not None
        forward += value
    return LiveCountsV040(
        fault_campaigns=int((observer.private / "host/fault-intent.json").exists()),
        fault_confirmed=(observer.private / "host/fault-confirmed.json").exists(),
        accepted_attempts=tables["remediation_authorizations"],
        write_intents=tables["remediation_write_intents"],
        dispatches=tables["remediation_executor_dispatches"],
        forward_mutations=forward,
        receipts=tables["remediation_step_receipts"],
        recovery_windows=tables["remediation_recovery_windows"],
    )


def freeze(runtime: ProductRuntimeV040, observer: LiveObserverV040) -> LiveManifestV040:
    observer.witness()
    if observer.host_state.read_current().fault_still_present:
        raise ValueError("baseline is not healthy at freeze")
    if runtime.command(
        ("git", "status", "--porcelain", "--untracked-files=normal")
    ).strip():
        raise ValueError("live freeze requires clean exact source")
    head = runtime.command(("git", "rev-parse", "HEAD")).strip()
    review = read_json(runtime.private / "host/pre-execution-review.json")
    validation = read_json(runtime.private / "host/local-validation.json")
    for gate in (review, validation):
        if gate["head"] != head or gate["verdict"] != "PASS":
            raise ValueError("pre-execution gate does not bind exact head")
    if review["must_fix"] != 0 or review["claim_accuracy"] != "PASS":
        raise ValueError("independent review is not passing")
    for check in ("full_pytest", "ruff", "mainline_mypy", "new_verifiers"):
        if validation[check] != "PASS":
            raise ValueError("full local validation is incomplete")
    ci = read_json(runtime.private / "host/exact-head-ci.json")
    # Re-read GitHub rather than trusting a caller-provided success string.
    current_ci = json.loads(
        runtime.command(
            (
                "gh",
                "run",
                "view",
                str(ci["run_id"]),
                "--json",
                "headSha,conclusion,status,workflowName",
            )
        )
    )
    if current_ci != {
        "headSha": head,
        "conclusion": "success",
        "status": "completed",
        "workflowName": "Agent mainline",
    }:
        raise ValueError("exact-head Agent mainline is not successful")
    verify_history(runtime.repository)
    private_storage_modes(runtime)
    source_inputs = read_json(runtime.private / "host/build-inputs.json")
    current_entries = runtime.command(
        (
            "git",
            "ls-files",
            "--stage",
            "-z",
            "--",
            "Dockerfile.product",
            "pyproject.toml",
            "uv.lock",
            "src",
            "config/product-v040/remediation-registry.v1.json",
        )
    ).split("\0")
    current_inputs = {}
    for entry in filter(None, current_entries):
        metadata, name = entry.split("\t", 1)
        mode, _, stage = metadata.split()
        if stage != "0" or mode not in {"100644", "100755"}:
            raise ValueError("build input mode or merge stage differs")
        current_inputs[name] = {
            "git_mode": mode,
            "sha256": hashlib.sha256(
                (runtime.repository / name).read_bytes()
            ).hexdigest(),
        }
    if current_inputs != source_inputs:
        raise ValueError("Product build input set, mode or content differs")
    build = read_json(runtime.private / "host/product-build.json")
    image = json.loads(
        runtime.docker(
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            runtime.env["ECOMSRE_V040_IMAGE"],
        )
    )[0]
    if (
        image["Id"] != build["image_id"]
        or digest(source_inputs) != build["source_inputs_sha256"]
    ):
        raise ValueError("Product build binding differs")
    frozen_inventory = read_json(runtime.private / "host/product-ownership.json")
    if runtime.owned() != frozen_inventory:
        raise ValueError("Product inventory changed before freeze")
    rows = json.loads(
        runtime.docker("container", "inspect", *frozen_inventory["container"])
    )
    if any(
        row["Image"] != build["image_id"] or not row["State"]["Running"] for row in rows
    ):
        raise ValueError("running Product image or process differs")
    baseline = read_json(runtime.private / "host/healthy-baseline.json")
    active = BaselineRepositoryV1(observer.recovery.attempts.store).get_active(
        baseline["environment"]["environment_id"]
    )
    if (
        active.baseline_sha256 != baseline["baseline"]["baseline_sha256"]
        or active.baseline_id != baseline["baseline"]["baseline_id"]
    ):
        raise ValueError("Active Baseline changed before freeze")
    control = read_json(runtime.private / "host/control-diagnosis.json")
    if control["diagnosis"]["terminal"] != "NO_INCIDENT":
        raise ValueError("no-fault control failed")
    no_candidates = read_json(runtime.private / "host/control-candidates.json")
    if no_candidates["candidates"]:
        raise ValueError("no-fault control produced a candidate")
    network = read_json(runtime.private / "host/network-denial.json")
    if network["status"] != "PASS":
        raise ValueError("runtime isolation is not measured")
    admission = read_json(runtime.private / "sandbox/control/admission.json")
    if observer.lifecycle.environment.verify_local_docker() != runtime.boundary():
        raise ValueError("runtime authority differs")
    observer.lifecycle.environment.verify_upstream()
    resolved, _ = observer.lifecycle.environment.resolve()
    if resolved.model_dump(mode="json") != admission["resolved"]:
        raise ValueError("frozen Sandbox Compose changed")
    actual_images = observer.lifecycle.environment.inspect_cached_images(resolved)
    if [value.model_dump(mode="json") for value in actual_images.images] != admission[
        "images"
    ]:
        raise ValueError("frozen Sandbox image identity changed")
    policy = RecoveryPolicyV1.model_validate_json(
        (runtime.private / "config/recovery-policy.json").read_bytes()
    )
    compose = json.loads(
        runtime.compose("--profile", "remediation", "config", "--format", "json")
    )
    manifest = LiveManifestV040.build(
        code_head=head,
        code_tree=runtime.command(("git", "rev-parse", "HEAD^{tree}")).strip(),
        source_inputs_sha256=digest(source_inputs),
        product_image_sha256=build["image_id"].removeprefix("sha256:"),
        historical_bindings_sha256=hashlib.sha256(
            (
                runtime.repository / "config/product-v040/historical-bindings.v1.json"
            ).read_bytes()
        ).hexdigest(),
        historical_image_lock_sha256=admission["historical_lock_sha256"],
        owned_image_lock_sha256=digest(admission["images"]),
        registry_sha256=REGISTRY_SHA256,
        runtime_profile_sha256=hashlib.sha256(
            (
                runtime.repository / "config/product-v040/live-profile.v1.json"
            ).read_bytes()
        ).hexdigest(),
        product_compose_sha256=digest(compose),
        sandbox_compose_sha256=resolved.compose_sha256,
        environment_id=baseline["environment"]["environment_id"],
        baseline_id=baseline["baseline"]["baseline_id"],
        baseline_sha256=baseline["baseline"]["baseline_sha256"],
        ownership_inventory_sha256=digest(
            {
                "product": frozen_inventory,
                "sandbox": read_json(runtime.private / "host/ownership.json"),
            }
        ),
        policy=policy,
        nofault_control_sha256=digest(control),
        network_denial_sha256=digest(network),
        pre_execution_review_sha256=digest(review),
        local_validation_sha256=digest(validation),
        ci_run_id=ci["run_id"],
        initial_counts=counts(observer),
        frozen_at=datetime.now(UTC),
    )
    seal_private(
        runtime.private / "host/frozen-manifest.json", manifest.model_dump(mode="json")
    )
    return manifest
