"""One admitted engineering attempt; every failure is preserved before exact cleanup."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
from typing import Any
from .budget import reserve
from .common import (
    REPO,
    ROOT,
    GOAL_SHA,
    KAFKA_PATHS,
    Failure,
    digest,
    git,
    load,
    now,
    require,
    seal,
    seal_bytes,
)
from .copyup import parse_copyup, validate_copyup
from .docker import Docker, static_inventory, image_inventory
from .host import listeners, require_ports_free, require_no_preexisting_owned
from .http import LocalHTTP
from .identity import lifecycle
from .oom import OOM_PROTOCOL, bind_oom
from .prepare import prepare
from .processes import CENSUS_PROTOCOL, validate_probe_census
from .product import ProductFlow, formal_zero
from .readiness import dependencies_ready, health_map
from .resources import Resources
from .sentinel import SENTINEL_V4, validate_sentinel
from .traffic import traffic
from .source import runtime_surface
from .images import image_config
from .transport import bounded


def admission(path: Path) -> dict[str, Any]:
    value = load(path)
    head = git("rev-parse", "HEAD")
    require(
        value["goal_sha256"] == GOAL_SHA
        and value["source_head"] == value["ci_head"] == head
        and value["reviewed_tree"] == git("rev-parse", "HEAD^{tree}")
        and value["live_admission"] == "ALLOW"
        and value["must_fix"] == 0
        and value["claim_accuracy"] == "PASS"
        and value["ci_conclusion"] == "SUCCESS",
        "LIVE_ADMISSION_WITHHELD",
    )
    require(not git("status", "--porcelain"), "LIVE_SOURCE_NOT_CLEAN")
    require(
        value["runtime_surface"] == runtime_surface()["sha256"], "RUNTIME_CONTENT_DRIFT"
    )
    return value


def probe_args(plan: dict[str, Any]) -> list[str]:
    service = plan["containers"]["kafka-volume-probe"]["service"]
    args = [
        "container",
        "create",
        "--name",
        service["container_name"],
        "--hostname",
        service["hostname"],
        "--platform",
        "linux/arm64",
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "1000:1000",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--pids-limit",
        "64",
        "--restart",
        "no",
        "--entrypoint",
        "/bin/sleep",
    ]
    for key, value in sorted(service["labels"].items()):
        args += ["--label", key + "=" + value]
    for mount in service["volumes"]:
        args += [
            "--mount",
            "type=volume,source="
            + plan["volumes"][mount["source"]]["name"]
            + ",target="
            + mount["target"],
        ]
    return args + [service["image"], "2147483647"]


def probe_exec(
    resources: Resources, key: str, protocol: str, extra: list[str] | None = None
) -> str:
    birth = resources.find_birth("container", "kafka-volume-probe")
    identifier = birth["record"]["Id"]
    view = resources.capture("before-" + key)
    resources.assert_stable(view)
    attachments: dict[str, list[str]] = {
        name: []
        for name in (
            resources.plan["volumes"]["kafka-" + p.rsplit("/", 1)[1]]["name"]
            for p in KAFKA_PATHS
        )
    }
    for row in view["resources"]["container"]:
        for mount in row["Mounts"]:
            if mount.get("Name") in attachments:
                attachments[mount["Name"]].append(row["Id"])
    require(
        all(ids == [identifier] for ids in attachments.values()),
        "PROBE_VOLUME_WRITER_PRESENT",
    )
    args = [
        "exec",
        "--user",
        "1000:1000",
        identifier,
        "/bin/bash",
        "-c",
        protocol,
        *(extra or []),
    ]
    seal(
        resources.root,
        key + "-intent.json",
        {
            "argv": args,
            "birth_digest": digest(birth),
            "before_digest": digest(view),
            **now(),
        },
    )
    result = resources.docker.command(args, timeout=45)
    seal(
        resources.root,
        key + "-response.json",
        {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            **now(),
        },
    )
    require(result.returncode == 0, "PROBE_EXEC_FAILED:" + key)
    return result.stdout


def stage(root: Path, name: str, details: Any) -> None:
    seal(root, "stages/" + name + ".json", {"stage": name, "details": details, **now()})
    print(json.dumps({"stage": name, **now()}), flush=True)


def attempt(
    gate_path: Path, product_image_path: Path, attempt_id: str
) -> dict[str, Any]:
    gate = admission(gate_path)
    require(
        len(attempt_id) == 32 and all(c in "0123456789abcdef" for c in attempt_id),
        "ATTEMPT_ID_INVALID",
    )
    root = ROOT / attempt_id
    root.mkdir(mode=0o700)
    seal(root, "admission.json", gate)
    image = load(product_image_path)
    require(image["source_head"] == gate["source_head"], "PRODUCT_BUILD_HEAD_DRIFT")
    docker = Docker()
    initial = docker.capture()
    docker.bound = initial["binding"]
    second = docker.capture()
    require(
        static_inventory(initial) == static_inventory(second), "INITIAL_INVENTORY_RACE"
    )
    require(
        not initial["resources"]["container"],
        "PREEXISTING_CONTAINER_REQUIRES_BOUNDARY_REVIEW",
    )
    require_no_preexisting_owned(initial)
    require_no_preexisting_owned(second)
    seal(root, "initial-inventory-1.json", initial)
    seal(root, "initial-inventory-2.json", second)
    listening = listeners()
    require_ports_free(listening)
    seal(root, "initial-listeners.json", listening)
    plan = prepare(root, attempt_id, docker, image, initial, gate["runtime_surface"])
    surface = digest(
        {
            "source": gate["runtime_surface"],
            "policy": load(REPO / "config/product-v040/preflight-v4/policy.json"),
            "compose": plan["semantic_compose_digest"],
            "images": plan["image_commitments"],
            "product_image": image["image_id"],
        }
    )
    seal(
        root,
        "attempt-identity.json",
        {
            "attempt_id": attempt_id,
            "runtime_surface": surface,
            "source_head": gate["source_head"],
            **now(),
        },
    )
    stage(
        root,
        "00-05-PLAN_FROZEN",
        {"plan_digest": digest(plan), "runtime_surface": surface},
    )
    resources = Resources(root, plan, docker, gate["source_head"])
    reserve(ROOT, attempt_id, surface)
    status = "FAILED"
    failure = None
    product = None
    traffic_result = None
    try:
        for ordinal, path in enumerate(KAFKA_PATHS):
            resources.storage("volume", "kafka-" + path.rsplit("/", 1)[1])
        stage(root, "06-KAFKA_VOLUMES_CREATE", {"count": 3})
        args = probe_args(plan)
        resources.create_group(
            "create-probe",
            args,
            [
                (
                    "container",
                    "kafka-volume-probe",
                    plan["containers"]["kafka-volume-probe"]["service"][
                        "container_name"
                    ],
                )
            ],
        )
        birth = resources.find_birth("container", "kafka-volume-probe")
        identifier = birth["record"]["Id"]
        stage(root, "07-PROBE_CREATE", {"birth_digest": digest(birth)})
        measurements = []
        for ordinal, path in enumerate(KAFKA_PATHS):
            before_copy = resources.capture("before-copyup-" + str(ordinal))
            current_probe = next(
                r
                for r in before_copy["resources"]["container"]
                if r["Id"] == identifier
            )
            require(current_probe == birth["record"], "COPYUP_PROBE_DRIFT")
            copy_args = ["docker", "container", "cp", identifier + ":" + path, "-"]
            seal(
                root,
                f"probe/copyup-{ordinal}-intent.json",
                {
                    "argv": copy_args,
                    "birth_digest": digest(birth),
                    "before_digest": digest(before_copy),
                    **now(),
                },
            )
            raw = bounded(
                copy_args,
                cwd=REPO,
                env=docker.env,
                timeout=45,
                maximum=16 * 1024 * 1024,
            )
            seal_bytes(root, f"probe/copyup-{ordinal}.tar", raw.stdout)
            seal(
                root,
                f"probe/copyup-{ordinal}-response.json",
                {
                    "returncode": raw.returncode,
                    "stderr": raw.stderr.decode("utf-8", errors="replace"),
                    "stdout_bytes": len(raw.stdout),
                    **now(),
                },
            )
            require(raw.returncode == 0, "COPYUP_READ_FAILED")
            measurements.append(parse_copyup(raw.stdout, path))
        images = load(REPO / "config/product-v040/preflight-v4/images.json")["images"]
        validate_copyup(
            measurements,
            load(REPO / "config/product-v040/preflight-v4/kafka-image-provenance.json"),
            images["ghcr.io/open-telemetry/demo:3.0.0-kafka"],
        )
        stage(root, "08-COPYUP_MEASURE", measurements)
        started = resources.start("kafka-volume-probe")
        stage(root, "09-PROBE_START", {"id": identifier})
        oom_raw = probe_exec(resources, "probe-oom", OOM_PROTOCOL)
        after = next(
            r
            for r in resources.capture("after-oom")["resources"]["container"]
            if r["Id"] == identifier
        )
        proof = bind_oom(oom_raw, started, after, resources.binding)
        seal(root, "oom-proof.json", proof)
        lifecycle(
            birth["record"],
            after,
            role="kafka-volume-probe",
            stage="running",
            oom_evidence=proof,
            binding=resources.binding,
        )
        census = validate_probe_census(
            probe_exec(resources, "probe-census", CENSUS_PROTOCOL)
        )
        stage(root, "10-PROBE_PROCESS_CENSUS", census)
        sentinel = validate_sentinel(
            probe_exec(
                resources,
                "probe-sentinel",
                SENTINEL_V4,
                ["qualification-sentinel", attempt_id],
            ),
            attempt_id,
        )
        stage(root, "11-PROBE_SENTINEL", sentinel)
        resources.remove(birth)
        absent = resources.capture("probe-absence")
        require(
            not any(
                r["Id"] == identifier or r["Name"] == birth["record"]["Name"]
                for r in absent["resources"]["container"]
            ),
            "PROBE_RETAINED",
        )
        none = next(
            r
            for r in absent["resources"]["network"]
            if r["Id"] == plan["builtin_none_id"]
        )
        require(
            identifier not in (none.get("Containers") or {}),
            "PROBE_NONE_ENDPOINT_RETAINED",
        )
        stage(
            root,
            "12-14-PROBE_REMOVED",
            {"id_absent": True, "none_endpoint_absent": True},
        )
        for key in plan["networks"]:
            if not key.startswith("product-"):
                resources.storage("network", key)
        for key in plan["volumes"]:
            if not key.startswith("kafka-"):
                resources.storage("volume", key)
        stage(root, "15-16-SANDBOX_STORAGE", {"created": True})
        resources.compose_create(root / "sandbox.json", plan["project"], "sandbox")
        for role in plan["sandbox_start_order"]:
            for poll in range(90):
                view = resources.capture("dependency-" + role + "-" + str(poll))
                by_id = {r["Id"]: r for r in view["resources"]["container"]}
                records = {
                    b["role"]: by_id[b["record"]["Id"]]
                    for b in resources.births
                    if b["kind"] == "container" and b["role"] in plan["roles"]
                }
                if dependencies_ready(role, plan["roles"], records):
                    break
                time.sleep(2)
            else:
                raise Failure("DEPENDENCY_NOT_READY:" + role)
            resources.start(role)
        stage(root, "17-18-SANDBOX_STARTED", {"roles": list(plan["roles"])})
        clients = {
            port: LocalHTTP(root / "http" / str(port), port=port)
            for port in (18080, 18016, 19090, 19200, 11686)
        }
        health_ordinal = 0

        def observe() -> dict[str, Any]:
            nonlocal health_ordinal
            view = resources.capture("service-health-" + str(health_ordinal))
            resources.assert_stable(view)
            result = health_map(
                view, plan, resources.births, clients, "health-" + str(health_ordinal)
            )
            after_view = resources.capture(
                "after-service-health-" + str(health_ordinal)
            )
            resources.assert_stable(after_view)
            health_ordinal += 1
            return result

        for _ in range(60):
            health = observe()
            if all(r["ready"] for r in health.values()):
                break
            time.sleep(5)
        else:
            raise Failure("SANDBOX_UNHEALTHY")
        stage(root, "19-SANDBOX_PER_SERVICE_HEALTH", health)
        warmup = traffic(clients[18080], attempt_id, "warmup")
        stage(root, "20-WARMUP", warmup)
        started_wait = time.monotonic()
        while time.monotonic() - started_wait < 330:
            time.sleep(min(15, 330 - (time.monotonic() - started_wait)))
            resources.assert_stable(
                resources.capture(
                    "settlement-" + str(int(time.monotonic() - started_wait))
                )
            )
        stage(root, "21-SETTLEMENT", {"seconds": time.monotonic() - started_wait})
        traffic_result = traffic(clients[18080], attempt_id, "healthy")
        stage(root, "22-23-HEALTHY_TRAFFIC", traffic_result)
        current_image = json.loads(docker.read("image", "inspect", image["image_id"]))[
            0
        ]
        require(
            current_image["Id"] == image["image_id"]
            and current_image["Os"] == "linux"
            and current_image["Architecture"] == "arm64"
            and current_image["RootFS"] == image["raw_inspect"]["RootFS"]
            and image_config(current_image["Config"]) == image_config(image["config"]),
            "PRODUCT_IMAGE_DRIFT",
        )
        stage(root, "24-PRODUCT_IMAGE_VERIFY", {"image_id": image["image_id"]})
        for key in plan["networks"]:
            if key.startswith("product-"):
                resources.storage("network", key)
        stage(root, "25-PRODUCT_STORAGE", {"created": True})
        resources.compose_create(
            root / "product.json", plan["product_project"], "product"
        )
        resources.start("api")
        resources.start("worker")
        stage(root, "26-PRODUCT_START", {"roles": ["api", "worker"]})
        variables = load(root / "product-substitutions.json")
        api = LocalHTTP(
            root / "product-http", port=18001, token=variables["V4_ADMIN_TOKEN"]
        )
        for i in range(30):
            result = api.request("ready-" + str(i), "GET", "/readyz")
            if result["status"] == 200:
                break
            time.sleep(2)
        else:
            raise Failure("PRODUCT_NOT_READY")
        stage(root, "27-PRODUCT_READY", result)
        product = ProductFlow(
            api, root / "product-data", attempt_id, resources.binding, plan, observe
        ).run()
        stage(root, "28-37-PRODUCT_NOFAULT", product)
        metrics = api.request("product-metrics", "GET", "/metrics")
        stage(root, "38-PRODUCT_METRICS", metrics)
        stage(
            root,
            "39-PRE_CLEANUP",
            {"capture_digest": digest(resources.capture("pre-cleanup"))},
        )
        status = "PASS"
    except Exception as error:
        failure = {"type": type(error).__name__, "reason": str(error), **now()}
        diagnostics_errors = []
        try:
            seal(root, "attempt-failure.json", failure)
        except OSError as evidence_error:
            diagnostics_errors.append(
                {"operation": "failure-seal", "error": str(evidence_error)}
            )
        try:
            view = resources.capture("failure")
        except Exception as diagnostic_error:
            view = {"resources": {"container": []}}
            diagnostics_errors.append(
                {"operation": "capture", "error": str(diagnostic_error)}
            )
        for row in view["resources"]["container"]:
            if (row["Config"].get("Labels") or {}).get(
                "io.ecomsre.preflight.v4.attempt"
            ) == attempt_id:
                try:
                    log_result = docker.command(
                        ["logs", "--tail", "100", "--timestamps", row["Id"]], timeout=20
                    )
                    seal(
                        root,
                        "failure-logs/" + row["Id"] + ".json",
                        {
                            "stdout": log_result.stdout,
                            "stderr": log_result.stderr,
                            "returncode": log_result.returncode,
                        },
                    )
                except Exception as diagnostic_error:
                    diagnostics_errors.append(
                        {
                            "operation": "logs",
                            "id": row["Id"],
                            "error": str(diagnostic_error),
                        }
                    )
        try:
            seal(root, "diagnostic-errors.json", diagnostics_errors)
        except OSError:
            pass  # Evidence storage failure must never skip provably safe cleanup.
    cleanup: dict[str, Any]
    try:
        cleanup = resources.cleanup(initial)
    except Exception as cleanup_error:
        status = "FAILED"
        cleanup = {
            "status": "BLOCKED_SAFETY",
            "reason": str(cleanup_error),
            "known_births": [
                {
                    "kind": b["kind"],
                    "role": b["role"],
                    "identifier": b["record"][
                        "Name" if b["kind"] == "volume" else "Id"
                    ],
                }
                for b in resources.births
            ],
            **now(),
        }
        try:
            cleanup["retained_observation"] = docker.capture()
        except Exception as capture_error:
            cleanup["retained_observation_error"] = str(capture_error)
        seal(root, "cleanup-failure.json", cleanup)
    closure_error = None
    try:
        final_listeners = listeners()
        seal(root, "final-listeners.json", final_listeners)
        require_ports_free(final_listeners)
        final_listeners_second = listeners()
        seal(root, "final-listeners-2.json", final_listeners_second)
        require_ports_free(final_listeners_second)
        final_view = docker.capture()
        seal(root, "final-image-inventory.json", final_view)
        require(
            image_inventory(final_view["images"]) == image_inventory(initial["images"]),
            "NONOWNED_IMAGES_DRIFT",
        )
        if (root / "product-data/product.sqlite3").exists():
            formal_zero(root / "product-data")
        require(
            digest(load(root / "flags/demo.flagd.json"))
            == plan["baseline_flag_sha256"],
            "BASELINE_FLAG_DRIFT",
        )
    except Exception as error:
        status = "FAILED"
        closure_error = {"type": type(error).__name__, "reason": str(error), **now()}
        seal(root, "closure-failure.json", closure_error)
    result = {
        "attempt_id": attempt_id,
        "status": status,
        "failure": failure,
        "cleanup_status": cleanup["status"],
        "closure_error": closure_error,
        "runtime_surface": surface,
        "source_head": gate["source_head"],
        "product_image_id": image["image_id"],
        "healthy_traffic": traffic_result,
        "product_result_digest": digest(product) if product else None,
        "formal_authority": "NONE",
        "provider_calls": 0,
        **now(),
    }
    seal(root, "attempt-result.json", result)
    stage(root, "40-46-ATTEMPT_RESULT", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--admission", required=True, type=Path)
    parser.add_argument("--product-image", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            attempt(args.admission, args.product_image, args.attempt_id), sort_keys=True
        )
    )
