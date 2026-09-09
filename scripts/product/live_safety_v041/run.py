"""One create-once minimal Payment engineering attempt with unconditional cleanup."""

from __future__ import annotations
import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import traceback
from typing import Any
import httpx
from ecomsre.product.remediation.execution_contracts import RecoveryPolicyV1
from ecomsre.product.remediation.state import TrustedStateBindingV1
from scripts.product.minimal_payment_acceptance_v040.owned import (
    command,
    digest,
    save,
    validate_container,
)
from .support import Owned, GOAL, stamp, compatible_image, build_plan
from scripts.product.minimal_payment_acceptance_v040.plan import (
    REPO,
    UPSTREAM,
    REASONS,
    pinned_images,
)
from .observer import Observer
from .product import Product


def free_ports() -> dict[str, int]:
    sockets = []
    result = {}
    try:
        for name in (
            "api",
            "probe",
            "flagd",
            "control",
            "prometheus",
            "observer",
            "gateway",
        ):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
            result[name] = sock.getsockname()[1]
    finally:
        for sock in sockets:
            sock.close()
    return result


def wait_http(client: httpx.Client, url: str, *, deadline_seconds: int = 90) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            if client.get(url).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise ValueError("ENDPOINT_NOT_READY:" + url)


def public_result(root: Path, result: dict[str, Any]) -> None:
    # Raw API responses and control profiles stay private. This intermediate
    # record exposes only counts, semantic outcomes and cryptographic commitments.
    save(root / "result.json", result)
    print(json.dumps(result, sort_keys=True), flush=True)


def run(product_image: str, case_id: str) -> dict[str, Any]:
    if command("git", "-C", str(REPO), "status", "--porcelain"):
        raise ValueError("LIVE_SOURCE_MUST_BE_COMMITTED")
    head = command("git", "-C", str(REPO), "rev-parse", "HEAD")
    upstream = REPO / "third_party/opentelemetry-demo"
    if command("git", "-C", str(upstream), "rev-parse", "HEAD") != UPSTREAM or command(
        "git", "-C", str(upstream), "status", "--porcelain"
    ):
        raise ValueError("PINNED_UPSTREAM_DRIFT")
    nonce = "ecomsre-v041-" + case_id.lower() + "-" + secrets.token_hex(6)
    root = Path.home() / ".local/share/ecomsre-live-safety-v041" / nonce
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    for name in ("data", "control/flags", "config", "private", "observer", "ledger"):
        (root / name).mkdir(parents=True, mode=0o755)
    # The enclosing 0700 attempt root protects local access; writable Docker bind
    # roots support the image's uid 10001 on Docker Desktop's shared filesystem.
    for name in ("data", "ledger"):
        (root / name).chmod(0o777)
    keys = {
        name: secrets.token_hex(32) for name in ("admin", "read", "write", "observer")
    }
    ports = free_ports()
    save(
        root / "private/session.json",
        {"ports": ports, "keys": keys, "source_head": head},
    )
    images = pinned_images(root, product_image)
    image_source_head = compatible_image(images["product"], head)
    owned = Owned(root, nonce)
    observer: Observer | None = None
    plan: dict[str, Any] = {}
    ids: dict[str, str] = {}
    running: set[str] = set()
    result: dict[str, Any] = {
        "schema_version": "ecomsre.product.live-safety-case.v1",
        "case_id": case_id,
        "image_source_head": image_source_head,
        "events": {"case_started": stamp()},
        "source_head": head,
        "goal_sha256": GOAL,
        "upstream_commit": UPSTREAM,
        "provider_calls": 0,
        "fault_injections": 0,
        "executor_attempts": 0,
        "terminal": "IMPLEMENTATION_FAILED",
        "minimal_services": REASONS,
        "started_at": datetime.now(UTC).isoformat(),
    }
    client = httpx.Client(timeout=10, trust_env=False, follow_redirects=False)
    try:
        baseline = json.loads((upstream / "src/flagd/demo.flagd.json").read_bytes())
        if baseline["flags"]["paymentFailure"]["defaultVariant"] != "off":
            raise ValueError("UPSTREAM_BASELINE_NOT_HEALTHY")
        fault = json.loads(json.dumps(baseline))
        fault["flags"]["paymentFailure"]["defaultVariant"] = "100%"
        for name, value in [
            ("baseline.json", baseline),
            ("fault.json", fault),
            ("flags/demo.flagd.json", baseline),
        ]:
            save(root / "control" / name, value)
            (root / "control" / name).chmod(0o644)
        plan = build_plan(root, nonce, images, ports, owned.labels, keys)
        save(root / "compose.json", plan)
        result["compose_sha256"] = digest(plan)
        netids = {}
        for name, spec in plan["networks"].items():
            netids[name] = owned.create_aux(
                "network", spec["name"], internal=name == "observation"
            )
        for name in plan["volumes"]:
            owned.create_aux("volume", name)
        image_by_id = {row["runtime_reference"]: row for row in images.values()}
        for role, spec in plan["services"].items():
            if command(
                "docker",
                "ps",
                "-aq",
                "--filter",
                "name=^/" + spec["container_name"] + "$",
            ):
                raise ValueError("PLANNED_CONTAINER_PREEXISTS")
            # Capture any created exact object even when Compose reports failure.
            created_process = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(root / "compose.json"),
                    "create",
                    "--no-build",
                    "--pull",
                    "never",
                    role,
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            save(
                root / "create" / (role + ".json"),
                {
                    "returncode": created_process.returncode,
                    "stdout": created_process.stdout,
                    "stderr": created_process.stderr,
                },
            )
            found = command(
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                "name=^/" + spec["container_name"] + "$",
            ).split()
            if len(found) == 1:
                owned.capture_birth("container", found[0])
                ids[role] = found[0]
            if created_process.returncode or len(found) != 1:
                raise ValueError("CONTAINER_CREATE_FAILED:" + role)

        def validate() -> None:
            owned.fresh()
            if not owned.unchanged():
                from scripts.product.minimal_payment_acceptance_v040.owned import (
                    inventory,
                )

                snapshot_path = root / "non-owned-drift.json"
                if not snapshot_path.exists():
                    save(snapshot_path, inventory())
                raise ValueError("NON_OWNED_DRIFT")
            for role in sorted(running):
                row = owned.require_birth("container", ids[role])
                spec = plan["services"][role]
                expected_nets = {netids[n] for n in spec.get("networks", [])}
                if spec.get("network_mode") == "none":
                    # Docker's built-in none network has no external interfaces.
                    expected_nets = {
                        n["NetworkID"]
                        for n in row["NetworkSettings"]["Networks"].values()
                        if n.get("NetworkID") in owned.before["network"]
                        and owned.before["network"][n["NetworkID"]]["Name"] == "none"
                    }
                validate_container(row, spec, owned.labels, image_by_id, expected_nets)
                if (
                    not row["State"]["Running"]
                    or row["State"].get("OOMKilled")
                    or row["RestartCount"] != 0
                ):
                    raise ValueError("OWNED_SERVICE_NOT_RUNNING:" + role)

        def start(role: str) -> None:
            owned.require_birth("container", ids[role])
            command("docker", "start", ids[role])
            running.add(role)
            validate()

        for role in (
            "payment-control",
            "flagd",
            "otel-collector",
            "payment",
            "payment-probe",
            "prometheus",
            "api",
            "worker",
        ):
            start(role)
        observer = Observer(
            root,
            ports["observer"],
            ports["probe"],
            ports["flagd"],
            ids["payment"],
            validate,
            keys["observer"],
            insufficient=case_id == "S4",
        )
        observer.start()
        wait_http(client, f"http://127.0.0.1:{ports['probe']}/ready")
        product = Product(root, ports["api"], keys["admin"], observer.check, ids["api"])
        product.call("GET", "/readyz")
        wait_http(client, f"http://127.0.0.1:{ports['prometheus']}/-/ready")
        # Healthy business and retention sufficient for the unchanged Baseline API.
        healthy_probes = []
        deadline = time.monotonic() + 390
        while time.monotonic() < deadline:
            observer.check()
            response = client.get(f"http://127.0.0.1:{ports['probe']}/probe")
            response.raise_for_status()
            healthy_probes.append(response.json())
            if len(healthy_probes) > 5 and not response.json()["ok"]:
                raise ValueError("HEALTHY_BUSINESS_FAILED")
            if len(healthy_probes) % 20 == 0:
                print("healthy-baseline warmup", len(healthy_probes), flush=True)
            time.sleep(2)
        save(root / "healthy-probes.json", healthy_probes)
        product.prepare(nonce)
        healthy_diagnosis = product.diagnose("healthy-control")
        save(root / "healthy-diagnosis.json", healthy_diagnosis)
        result["healthy"] = {
            "baseline_sha256": product.baseline["baseline_sha256"],
            "business_requests": len(healthy_probes),
            "business_errors_after_startup": sum(
                not p["ok"] for p in healthy_probes[5:]
            ),
            "product_diagnosis": healthy_diagnosis["diagnosis"]["terminal"],
            "proof": "Active Product Baseline plus real successful direct Payment requests; unavailable telemetry remains explicit.",
        }
        validate()
        projection = product.call(
            "POST",
            "/v1/incidents/"
            + healthy_diagnosis["incident"]["incident_id"]
            + "/remediation-candidates",
        )
        result["healthy_candidate_count"] = len(projection["candidates"])
        if projection["candidates"]:
            raise ValueError("HEALTHY_CANDIDATE_UNEXPECTED")
        if case_id == "S0":
            result["events"]["safe_denial"] = stamp()
            result["terminal"] = "NO_CANDIDATE"
            return result
        # Trusted local injection, outside the Product Diagnosis/Executor process.
        result["events"]["fault_write_started"] = stamp()
        injection_at = datetime.now(UTC).isoformat()
        response = client.post(
            f"http://127.0.0.1:{ports['control']}/write",
            json={"data": fault},
            headers={"X-EcomSRE-Controller": "FAULT_INJECTION"},
        )
        response.raise_for_status()
        result["events"]["fault_write_acknowledged"] = stamp()
        result["fault_injections"] = 1
        fault_probes = []
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            observer.check()
            response = client.get(f"http://127.0.0.1:{ports['probe']}/probe")
            response.raise_for_status()
            fault_probes.append(response.json())
            if not response.json()["ok"]:
                result["events"].setdefault("first_failed_business_request", stamp())
            time.sleep(1)
        save(root / "fault-probes.json", fault_probes)
        if not all(
            not p["ok"] and p["expected_payment_failure"] for p in fault_probes[-30:]
        ) or digest(
            json.loads((root / "control/flags/demo.flagd.json").read_bytes())
        ) != digest(fault):
            raise ValueError("REAL_PAYMENT_FAULT_NOT_CONFIRMED")
        result["fault"] = {
            "injected_at": injection_at,
            "configuration_digest": digest(fault),
            "confirmed_requests": 30,
            "expected_payment_failures": 30,
        }
        product.change(digest(fault))
        result["events"]["diagnosis_request_started"] = stamp()
        fault_diagnosis = product.diagnose("business-incident")
        result["events"]["diagnosis_response_observed"] = stamp()
        save(root / "fault-diagnosis.json", fault_diagnosis)
        # Build binding from current Product identity/baseline and fresh owned IDs.
        projection = product.call(
            "GET",
            "/v1/incidents/"
            + fault_diagnosis["incident"]["incident_id"]
            + "/remediation-candidates",
        )
        if len(projection["candidates"]) != 1:
            raise ValueError("UNIQUE_CANDIDATE_MISSING:" + json.dumps(projection))
        candidate = projection["candidates"][0]
        control_url = f"http://host.docker.internal:{ports['control']}"
        flag_url = f"http://host.docker.internal:{ports['flagd']}"
        binding = TrustedStateBindingV1.build(
            environment_id=candidate["environment_id"],
            environment_ownership_digest=digest(
                {
                    "daemon": owned.daemon,
                    "resources": ids,
                    "networks": netids,
                    "labels": owned.labels,
                }
            ),
            target_identity_digest=digest(owned.births["container"][ids["payment"]]),
            identity_map_sha256=candidate["identity_map_sha256"],
            control_identity_sha256=digest(
                {
                    "flag_control_url": control_url,
                    "flag_evaluation_url": flag_url,
                    "baseline_configuration_digest": digest(baseline),
                    "fault_configuration_digest": digest(fault),
                }
            ),
            baseline_id=candidate["baseline_id"],
            baseline_sha256=candidate["baseline_sha256"],
            baseline_configuration_digest=digest(baseline),
            fault_configuration_digest=digest(fault),
            registry_sha256=candidate["registry_sha256"],
            created_at=datetime.now(UTC),
        )
        policy = RecoveryPolicyV1.build(
            environment_id=binding.environment_id,
            baseline_sha256=binding.baseline_sha256,
            baseline_configuration_digest=binding.baseline_configuration_digest,
            fault_configuration_digest=binding.fault_configuration_digest,
            target_identity_digest=binding.target_identity_digest,
            control_identity_sha256=binding.control_identity_sha256,
            environment_ownership_digest=binding.environment_ownership_digest,
            business_error_ratio_max=0.01,
            minimum_business_requests=10,
            window_seconds=10,
            created_at=datetime.now(UTC),
        )
        profile = {
            "binding": binding.model_dump(mode="json"),
            "flag_control_url": control_url,
            "flag_evaluation_url": flag_url,
            "flag_file": "/runtime/payment-flags/demo.flagd.json",
            "ownership_witness_file": "/run/remediation-observer/ownership.json",
            "baseline_document": baseline,
            "fault_document": fault,
        }
        for relative, value in [
            ("config/binding.json", binding.model_dump(mode="json")),
            ("config/recovery-policy.json", policy.model_dump(mode="json")),
            ("private/profile.json", profile),
            (
                "private/observation-proxy.json",
                {
                    "prometheus_base_url": f"http://host.docker.internal:{ports['prometheus']}",
                    "jaeger_base_url": f"http://host.docker.internal:{ports['prometheus']}",
                    "opensearch_base_url": f"http://host.docker.internal:{ports['prometheus']}",
                },
            ),
        ]:
            save(root / relative, value)
            if relative.startswith("config/"):
                (root / relative).chmod(0o644)
        observer.binding = binding
        time.sleep(5)
        observer.check()
        start("remediation-control-gateway")
        state_headers = {"Authorization": "Bearer " + keys["read"]}
        deadline = time.monotonic() + 60
        while True:
            try:
                state_response = client.get(
                    f"http://127.0.0.1:{ports['gateway']}/state", headers=state_headers
                )
                if state_response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise ValueError("GATEWAY_STATE_NOT_READY")
            observer.check()
            time.sleep(1)
        start("bound-api")
        product.api_id = ids["bound-api"]
        # Wait for the real configured API; readiness is through exact loopback transport.
        deadline = time.monotonic() + 45
        while True:
            try:
                product.call("GET", "/readyz")
                break
            except (ValueError, subprocess.SubprocessError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        save(
            root / "running-gateway.json",
            owned.require_birth("container", ids["remediation-control-gateway"]),
        )
        command(
            "docker",
            "exec",
            ids["remediation-control-gateway"],
            "python",
            "/peer_probe.py",
        )
        candidate, approval = product.approve(fault_diagnosis)
        result["events"]["approval_response_observed"] = stamp()
        candidate_route = "/v1/remediation-candidates/" + candidate["candidate_id"]
        if case_id == "S1":
            product.call(
                "POST",
                candidate_route + "/revocations",
                {"approval_id": approval["approval_id"], "reason": "OPERATOR_REVOKED"},
            )
            result["approval_status"] = product.call(
                "GET", "/v1/remediation-approvals/" + approval["approval_id"]
            )
        if case_id == "S2":
            validate()
            response = client.post(
                f"http://127.0.0.1:{ports['control']}/write",
                json={"data": baseline},
                headers={"X-EcomSRE-Controller": "BASELINE_RESTORE"},
            )
            response.raise_for_status()
            deadline = time.monotonic() + 20
            while observer.current() != (digest(baseline), True):
                if time.monotonic() >= deadline:
                    raise ValueError("DRIFT_BASELINE_NOT_CONFIRMED")
                time.sleep(0.25)
            result["controller_baseline_restores"] = 1
            response = client.get(
                f"http://127.0.0.1:{ports['gateway']}/state", headers=state_headers
            )
            response.raise_for_status()
            state = response.json()
            if (
                state["current_configuration_digest"] != digest(baseline)
                or state["fault_still_present"]
            ):
                raise ValueError("GATEWAY_DRIFT_NOT_CONFIRMED")
            save(root / "drift-state.json", state)
        if case_id == "S3":
            replay_projection = product.call(
                "POST",
                "/v1/incidents/"
                + fault_diagnosis["incident"]["incident_id"]
                + "/remediation-candidates",
                key="minimal-90001",
            )
            if replay_projection["candidates"] != [candidate]:
                raise ValueError("CANDIDATE_REPLAY_MISMATCH")
            # Reuse the exact original request bytes recorded by the harness.
            requests = [
                json.loads(p.read_bytes())
                for p in sorted((root / "product").glob("*.json"))
            ]
            approval_request = next(
                v["request"]
                for v in requests
                if v["method"] == "POST"
                and v["route"] == candidate_route + "/approvals"
            )
            if (
                product.call(
                    "POST",
                    candidate_route + "/approvals",
                    approval_request,
                    key="minimal-90002",
                )
                != approval
            ):
                raise ValueError("APPROVAL_REPLAY_MISMATCH")
        result["events"]["attempt_request_started"] = stamp()
        attempt_body = product.call(
            "POST",
            candidate_route + "/attempts",
            {"approval_id": approval["approval_id"]},
            key="minimal-90003",
        )
        result["events"]["attempt_response_observed"] = stamp()
        save(root / "api-attempt.json", attempt_body)
        from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
        from ecomsre.product.remediation.repository import RemediationRepositoryV1
        from ecomsre.product.storage.sqlite_store import SqliteStoreV1
        from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1

        store = SqliteStoreV1(root / "data/product.sqlite3")
        repo = RemediationAttemptRepositoryV1(
            RemediationRepositoryV1(
                store,
                ContentAddressedObjectStoreV1(
                    root / "data/objects", metadata_store=store
                ),
            )
        )
        attempt = repo.get(attempt_body["attempt_id"])
        if case_id in ("S1", "S2"):
            expected_reason = (
                "APPROVAL_REVOKED"
                if case_id == "S1"
                else "CONFIGURATION_DRIFT_NOT_VISIBLE"
            )
            if (
                attempt.safe_error_code != expected_reason
                or attempt.final_disposition != "NO_WRITE"
            ):
                raise ValueError("EXPECTED_SAFE_DENIAL_NOT_OBSERVED")
            result["events"]["safe_denial"] = stamp()
            result["terminal"] = attempt.state.value
            result["safe_error_code"] = attempt.safe_error_code
            result["final_disposition"] = attempt.final_disposition
            save(root / "final-attempt.json", attempt.model_dump(mode="json"))
            return result
        if case_id == "S3":
            replay = product.call(
                "POST",
                candidate_route + "/attempts",
                {"approval_id": approval["approval_id"]},
                key="minimal-90003",
            )
            if replay != attempt_body:
                raise ValueError("ATTEMPT_REPLAY_MISMATCH")
        result["executor_attempts"] = 1
        observer.policy = policy
        start("remediation-executor")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            observer.check()
            attempt = repo.get(attempt.attempt_id)
            if attempt.terminal is not None:
                break
            time.sleep(1)
        if attempt.terminal is None:
            raise ValueError("TERMINAL_NOT_REACHED")
        result["events"]["terminal_observed"] = stamp()
        save(root / "final-attempt.json", attempt.model_dump(mode="json"))
        owned.require_birth("container", ids["remediation-executor"])
        replay = command(
            "docker", "exec", ids["remediation-executor"], "python", "/replay.py"
        )
        save(root / "executor-replay.json", json.loads(replay))
        with repo.store.connect() as db:
            rows = {
                r[0]: [dict(v) for v in db.execute("SELECT * FROM " + r[0])]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'remediation_%'"
                ).fetchall()
            }
        save(root / "remediation-rows.json", rows)
        result.update(
            terminal=attempt.state.value,
            final_disposition=attempt.final_disposition,
            candidate_sha256=candidate["candidate_sha256"],
            approval_sha256=approval["approval_sha256"],
            final_attempt_sha256=digest(attempt.model_dump(mode="json")),
            diagnosis={
                "terminal": fault_diagnosis["diagnosis"]["terminal"],
                "mechanism": fault_diagnosis["diagnosis"]["mechanism"],
                "domain": fault_diagnosis["diagnosis"]["broad_domain"],
                "root": "payment",
                "all_supporting_refs_resolved": True,
            },
        )
        expected = "VERIFICATION_FAILED" if case_id == "S4" else "RECOVERED"
        if attempt.state.value != expected:
            raise ValueError("PRODUCT_RECOVERY_NOT_CONFIRMED")
    except Exception as error:
        save(
            root / "failure.json",
            {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        result["failure_type"] = type(error).__name__
        result["failure_message"] = str(error).split(":")[0]
    finally:
        result["events"]["cleanup_started"] = stamp()
        result["collection_errors"] = []
        try:
            if observer is not None:
                observer.close()
        except Exception as error:
            result["collection_errors"].append(type(error).__name__)
        # Log retention must never bypass the separately authorized exact cleanup.
        for role, identifier in ids.items():
            try:
                logs_process = subprocess.run(
                    ["docker", "logs", "--tail", "1000", identifier],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                save(
                    root / "logs" / (role + ".json"),
                    {"stdout": logs_process.stdout, "stderr": logs_process.stderr},
                )
            except Exception as error:
                result["collection_errors"].append(type(error).__name__)
        if "payment-control" in running:
            try:
                # Separate safety restoration; never an Executor success receipt.
                current = json.loads(
                    (root / "control/flags/demo.flagd.json").read_bytes()
                )
                if current != baseline:
                    owned.require_birth("container", ids["payment-control"])
                    response = client.post(
                        f"http://127.0.0.1:{ports['control']}/write",
                        json={"data": baseline},
                        headers={"X-EcomSRE-Controller": "BASELINE_RESTORE"},
                    )
                    response.raise_for_status()
                    result["safety_baseline_restores"] = 1
                result["baseline_file_restored"] = (
                    json.loads((root / "control/flags/demo.flagd.json").read_bytes())
                    == baseline
                )
            except Exception:
                result["baseline_file_restored"] = False
        try:
            result["cleanup"] = owned.cleanup()
        except Exception as error:
            result["terminal"] = "safety_blocked_checkpoint"
            result["cleanup_error"] = type(error).__name__
            public_result(root, result)
            raise
        result["events"]["cleanup_completed"] = stamp()
        from .evidence import collect

        try:
            collect(root, result)
        except Exception as error:
            result["evidence_status"] = "FAILED"
            result["evidence_error"] = type(error).__name__
            result["terminal"] = "IMPLEMENTATION_FAILED"
            save(
                root / "collection-failure.json",
                {"message": str(error), "traceback": traceback.format_exc()},
            )
        result["finished_at"] = datetime.now(UTC).isoformat()
        public_result(root, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-image", required=True)
    parser.add_argument("--case", choices=["S0", "S1", "S2", "S3", "S4"], required=True)
    args = parser.parse_args()
    run(args.product_image, args.case)
