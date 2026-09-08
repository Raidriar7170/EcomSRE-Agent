"""The single no-fault qualification path and separate exact-owned cleanup."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import select
import socket
import sqlite3
import subprocess
import tarfile
import time
from types import SimpleNamespace
from typing import Any

from ecomsre.dta_v2.contracts import semantic_sha256
from scripts.product.v040_ownership import tree_commitment
from scripts.product.v040_preparation import diagnosis
from scripts.product.v040_gates import NETWORK_PROBE, private_storage_modes
from scripts.product.v040_runtime import read_json, seal_private
from scripts.product.qualification_v040.capture import (
    capture,
    platform_images,
    pinned_cached_reference,
)
from scripts.product.qualification_v040_v3.guard import (
    KINDS,
    QUAL_LABEL,
    ZERO_COUNTS,
    QualificationJournal,
    QualificationBlocked,
    identity,
    labels,
    name,
    require,
    sha,
    validate_role,
    validate_envelope,
    resource_fixed,
)
from scripts.product.qualification_v040.healthy import healthy_baseline
from scripts.product.qualification_v040.plan import (
    PRODUCT_PROJECT,
    SANDBOX_PROJECT,
    build_plan,
    container_role,
    compose_defaults,
)
from scripts.product.qualification_v040.runtime import (
    BaselineReader,
    QualificationRuntime,
    prepare_product,
    prepare_sandbox,
)
from scripts.product.qualification_v040.volumes import (
    KAFKA_PATHS,
    MAX_ARCHIVE_BYTES,
    SENTINEL_PROTOCOL,
    parse_copyup,
    runtime_user,
    validate_access,
    validate_sentinel,
    verify_process_identity,
)


class Driver:
    def __init__(self, runtime: QualificationRuntime) -> None:
        self.runtime = runtime
        self.journal: QualificationJournal | None = None
        self.references: list[str] = []
        self.mutable_binds = {str(runtime.private / "product")}
        self.attempted_births: set[str] = set()
        self.measurements: list[dict[str, Any]] = []
        self.capture_sequence = 0
        self.cleanup_mode = False
        self.active_stage = "INITIAL"
        self.result: dict[str, Any] = {
            "status": "PRE_EXECUTION_ONLY",
            "healthy_traffic": None,
            "active_baseline": None,
            "no_incident": None,
            "isolation": None,
            "copyup": None,
            "cleanup": None,
            "counters": deepcopy(ZERO_COUNTS),
            "counter_evidence": {"status": "NOT_YET_OBSERVED"},
            "one_shot_allowance_consumed": False,
            "formal_authority": False,
        }

    def same(self, bound: dict[str, Any], kind: str, row: dict[str, Any]) -> bool:
        return resource_fixed(kind, row) == bound

    def prepare_cleanup_stop(self) -> None:
        pass

    def snapshot(self) -> dict[str, Any]:
        return capture(
            self.runtime,
            self.runtime.qualification,
            self.references,
            self.mutable_binds,
            self.capture_seeds,
        )

    def capture_seeds(self, inspected: dict[str, Any]) -> dict[str, Any]:
        if not self.measurements:
            return {"status": "NOT_BOUND"}
        if self.cleanup_mode:
            return {"status": "NOT_REQUIRED_DURING_CLEANUP"}
        assert self.journal is not None
        # The stopped probe retains the same explicitly bound mounts through
        # Kafka birth; after removal only the previously bound Kafka is used.
        keys = ("sandbox/container/kafka", "probe/container/kafka-volume-probe")
        selected = None
        for key in keys:
            if key not in self.journal.bound:
                continue
            rows = [r for r in inspected["containers"] if r["Id"] == self.bound_id(key)]
            if rows:
                role = self.journal.plan["roles"][key]
                validate_role(role, rows[0], self.runtime.qualification)
                require(
                    self.same(self.journal.bound[key], "containers", rows[0]),
                    "SEED_READER_IDENTITY_DRIFT",
                )
                selected = rows[0]["Id"]
                break
        require(selected is not None, "SEED_READER_UNAVAILABLE")
        assert isinstance(selected, str)
        self.capture_sequence += 1
        passes = []
        for sweep in range(2):
            passes.append(
                [
                    parse_copyup(
                        self.archive(
                            selected,
                            path,
                            f"seed-{self.capture_sequence:03d}-{sweep}-{index}.tar",
                        ),
                        path,
                    )
                    for index, path in enumerate(KAFKA_PATHS)
                ]
            )
        return {
            "status": "MEASURED",
            "initial": self.measurements,
            "uid": self.uid,
            "gid": self.gid,
            "before": passes[0],
            "after": passes[1],
        }

    def checkpoint(self, stage: str) -> None:
        assert self.journal is not None
        self.active_stage = stage
        try:
            saved = self.snapshot()
        except Exception as error:
            self.journal.block(
                getattr(error, "code", "INCOMPLETE_CAPTURE"),
                stage,
                type(error).__name__,
                actual=getattr(self, "last_raw_capture", None),
            )
        self.journal.observe(stage, saved)

    def step(self, action: str, operation: Any) -> Any:
        assert self.journal is not None
        self.checkpoint("BEFORE_" + action)
        self.attempted_births.update(
            key
            for key, role in self.journal.plan["roles"].items()
            if role["birth_stage"] == "AFTER_" + action
        )
        value = operation()
        self.checkpoint("AFTER_" + action)
        return value

    def setup(self, historical: Path) -> None:
        runtime = self.runtime
        runtime.stabilize()
        self.environment, resolved, sandbox, baseline = prepare_sandbox(
            runtime, historical
        )
        product = prepare_product(runtime, historical)
        with socket.socket() as port_probe:
            port_probe.bind(("127.0.0.1", 18001))
        refs = sorted(
            {
                service["image"]
                for c in (sandbox, product)
                for service in c["services"].values()
            }
        )
        originals = platform_images(runtime, refs)
        seal_private(runtime.private / "host/fresh-image-inspection.json", originals)
        # Eliminate mutable tag selection during subsequent Compose startup.
        for compose in (sandbox, product):
            for service in compose["services"].values():
                source = service["image"]
                observed = originals[source]
                service["image"] = pinned_cached_reference(source, observed)
        self.references = sorted(
            {
                service["image"]
                for c in (sandbox, product)
                for service in c["services"].values()
            }
        )
        pinned = platform_images(runtime, self.references)
        require(
            all(
                pinned[pinned_cached_reference(ref, original)] == original
                for ref, original in originals.items()
            ),
            "PINNED_PLATFORM_BINDING_DRIFT",
        )
        initial = self.snapshot()
        self.preflight_initial = initial
        seal_private(runtime.private / "host/initial-inventory.json", initial)
        for kind in ("containers", "networks", "volumes"):
            require(
                not any(
                    labels(kind, row).get("com.docker.compose.project")
                    in {SANDBOX_PROJECT, PRODUCT_PROJECT}
                    for row in initial["inspect"][kind]
                ),
                "PREEXISTING_PROJECT_RESOURCES",
            )
        kafka = sandbox["services"]["kafka"]
        image = initial["platform_images"][kafka["image"]]
        require(
            set(image["Config"].get("Volumes") or {}) == set(KAFKA_PATHS),
            "KAFKA_IMAGE_VOLUME_DRIFT",
        )
        probe_service = {
            "container_name": "ecomsre-v040-" + runtime.qualification + "-volume-probe",
            "image": kafka["image"],
            "entrypoint": ["/bin/sh"],
            "command": ["-c", SENTINEL_PROTOCOL],
            "read_only": True,
            "labels": {
                **kafka["labels"],
                "com.docker.compose.service": "kafka-volume-probe",
            },
            "volumes": [m for m in kafka["volumes"] if m["target"] in KAFKA_PATHS],
        }
        probe = container_role(
            probe_service,
            sandbox,
            SANDBOX_PROJECT,
            image,
            "AFTER_COPYUP_CREATE",
            runtime.qualification,
        )
        probe["network_none"] = True
        commitments = {}
        for compose in (sandbox, product):
            for service in compose["services"].values():
                for mount in service.get("volumes") or []:
                    if mount["type"] != "bind":
                        continue
                    source = Path(mount["source"])
                    commitment: dict[str, Any]
                    if str(source) == "/var/run/docker.sock":
                        require(
                            mount.get("read_only") is True,
                            "DOCKER_SOCKET_WRITE_FORBIDDEN",
                        )
                        commitment = {
                            "digest_kind": "DAEMON_SOCKET_BINDING_V1",
                            "sha256": sha(initial["daemon_before"]),
                        }
                    elif str(source) in self.mutable_binds:
                        info = source.stat()
                        commitment = {
                            "digest_kind": "OWNED_MUTABLE_DIRECTORY_IDENTITY_V1",
                            "source": str(source),
                            "uid": info.st_uid,
                            "gid": info.st_gid,
                            "mode": info.st_mode & 0o7777,
                            "inode": info.st_ino,
                        }
                    else:
                        require(
                            not source.is_symlink()
                            and source.resolve().is_relative_to(runtime.repository),
                            "UNBOUND_HOST_SOURCE",
                        )
                        commitment = tree_commitment(source)
                    commitments[str(source)] = commitment
        plan = build_plan(
            runtime.qualification, initial, sandbox, product, probe, commitments
        )
        plan["source"] = read_json(runtime.private / "host/qualification-identity.json")
        plan["seed_binding_policy"] = (
            "CREATE_ONCE_POST_COPYUP_FROM_FIXED_IMAGE_BEFORE_KAFKA_START"
        )
        self.compose_paths = {}
        for group, compose in (("sandbox", sandbox), ("product", product)):
            startup = deepcopy(compose)
            startup["volumes"] = {
                key: {"name": value["name"], "external": True}
                for key, value in startup.get("volumes", {}).items()
            }
            startup["networks"] = {
                key: {"name": value["name"], "external": True}
                for key, value in startup["networks"].items()
            }
            path = runtime.private / "host" / (group + "-compose.json")
            seal_private(path, startup)
            project = SANDBOX_PROJECT if group == "sandbox" else PRODUCT_PROJECT
            expanded = json.loads(
                runtime.compose_plan(project, path, "config", "--format", "json")
            )
            seal_private(
                runtime.private / "host" / (group + "-compose-expanded.json"), expanded
            )
            require(
                compose_defaults(expanded) == compose_defaults(startup),
                "QUALIFICATION_COMPOSE_REEXPANSION_DRIFT",
                group,
            )
            self.compose_paths[group] = path
            plan[group + "_compose_sha256"] = sha(expanded)
            if group == "sandbox":
                qualified_resolved = resolved.model_copy(
                    update={
                        "compose_sha256": hashlib.sha256(
                            json.dumps(expanded, sort_keys=True).encode()
                        ).hexdigest(),
                        "image_references": tuple(
                            sorted({s["image"] for s in expanded["services"].values()})
                        ),
                    }
                )
                self.environment.bind_qualification(
                    runtime, qualified_resolved, read_json(path), path
                )
        self.lifecycle: Any = SimpleNamespace(
            environment=self.environment,
            goal_controller=BaselineReader(
                resolved.endpoints,
                runtime.private / "sandbox/runtime/flagd/demo.flagd.json",
                baseline,
            ),
        )
        seal_private(
            runtime.private / "host/image-config-volumes-coverage.json",
            {
                key: {
                    "image": role["image_reference"],
                    "declared": role["image_config_volumes"],
                    "bound_targets": sorted(set(role["mounts"]) | set(role["tmpfs"])),
                    "unbound": [],
                }
                for key, role in plan["roles"].items()
                if role["kind"] == "containers"
            },
        )
        self.journal = QualificationJournal(
            runtime.private / "host/stage-journal", plan
        )
        self.journal.observe("INITIAL", initial)

    def create_volume(self, role: dict[str, Any]) -> None:
        self.runtime.docker(
            "volume",
            "create",
            "--driver",
            "local",
            *(
                a
                for k, v in sorted(role["labels"].items())
                for a in ("--label", k + "=" + v)
            ),
            role["name"],
        )

    def create_network(self, role: dict[str, Any]) -> None:
        self.runtime.docker(
            "network",
            "create",
            "--driver",
            "bridge",
            *(("--internal",) if role["internal"] else ()),
            *(
                a
                for k, v in sorted(role["labels"].items())
                for a in ("--label", k + "=" + v)
            ),
            role["name"],
        )

    def create_probe(self) -> None:
        assert self.journal is not None
        role = self.journal.plan["roles"]["probe/container/kafka-volume-probe"]
        self.runtime.docker(
            "container",
            "create",
            "--name",
            role["name"],
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "/bin/sh",
            *(
                a
                for k, v in sorted(role["labels"].items())
                for a in ("--label", k + "=" + v)
            ),
            *(
                a
                for target, m in role["mounts"].items()
                for a in (
                    "--mount",
                    "type=volume,source=" + m["source"] + ",target=" + target,
                )
            ),
            role["image_reference"],
            "-c",
            SENTINEL_PROTOCOL,
        )

    def bound_id(self, key: str) -> str:
        assert self.journal is not None
        row = self.journal.bound[key]
        return identity(self.journal.plan["roles"][key]["kind"], row)

    def archive(self, container: str, path: str, artifact: str) -> bytes:
        require(path in {*KAFKA_PATHS, "/etc/passwd"}, "COPY_PATH_FORBIDDEN")
        require(
            container
            in {self.bound_id("probe/container/kafka-volume-probe")}
            | (
                {self.bound_id("sandbox/container/kafka")}
                if self.journal and "sandbox/container/kafka" in self.journal.bound
                else set()
            ),
            "COPY_OWNER_UNKNOWN",
        )
        argv = [
            "docker",
            "--context",
            "desktop-linux",
            "cp",
            container + ":" + path,
            "-",
        ]
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                k: v
                for k, v in os.environ.items()
                if k not in {"DOCKER_HOST", "DOCKER_CONTEXT"}
            },
        )
        payload = bytearray()
        deadline = time.monotonic() + 45
        try:
            assert process.stdout is not None
            while True:
                require(time.monotonic() < deadline, "COPYUP_MEASUREMENT_TIMEOUT")
                ready, _, _ = select.select([process.stdout], [], [], 0.2)
                if not ready:
                    continue
                data = os.read(
                    process.stdout.fileno(),
                    min(65536, MAX_ARCHIVE_BYTES + 1 - len(payload)),
                )
                if not data:
                    break
                payload.extend(data)
                require(len(payload) <= MAX_ARCHIVE_BYTES, "COPYUP_MEASUREMENT_BOUNDS")
            require(process.wait(timeout=5) == 0, "COPYUP_MEASUREMENT_FAILED")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        from ecomsre.product.remediation.window_requests import create_private_file

        create_private_file(self.runtime.private / "host" / artifact, bytes(payload))
        return bytes(payload)

    def measure_copyup(self) -> None:
        probe = self.bound_id("probe/container/kafka-volume-probe")
        self.measurements = [
            parse_copyup(self.archive(probe, path, f"copyup-{index}.tar"), path)
            for index, path in enumerate(KAFKA_PATHS)
        ]
        passwd_archive = self.archive(probe, "/etc/passwd", "kafka-passwd.tar")
        with tarfile.open(fileobj=io.BytesIO(passwd_archive)) as archive:
            rows = archive.getmembers()
            require(
                len(rows) == 1
                and rows[0].isfile()
                and rows[0].name == "passwd"
                and rows[0].size < 65536,
                "RUNTIME_USER_UNKNOWN",
            )
            stream = archive.extractfile(rows[0])
            assert stream is not None
            passwd = stream.read(65536)
        self.uid, self.gid = runtime_user(passwd, "appuser")
        seal_private(
            self.runtime.private / "host/copyup-measurements.json",
            {
                "uid": self.uid,
                "gid": self.gid,
                "measurements": self.measurements,
                "mutable_paths": ["/var/lib/kafka/data", "/mnt/shared/config"],
                "sentinel_protocol_sha256": hashlib.sha256(
                    SENTINEL_PROTOCOL.encode()
                ).hexdigest(),
            },
        )
        self.result["copyup"] = {
            "uid": self.uid,
            "gid": self.gid,
            "measurements": self.measurements,
            "writeability": "NOT_EXECUTED",
        }
        validate_access(self.measurements, self.uid, self.gid)

    def sentinel(self) -> None:
        result = validate_sentinel(
            self.runtime.docker(
                "container",
                "start",
                "--attach",
                self.bound_id("probe/container/kafka-volume-probe"),
            ),
            self.uid,
            self.gid,
        )
        self.result["copyup"]["writeability"] = result
        seal_private(self.runtime.private / "host/sentinel-result.json", result)
        # Independent post-sentinel copy verifies absence and unchanged seed bytes.
        probe = self.bound_id("probe/container/kafka-volume-probe")
        for index, previous in enumerate(self.measurements):
            now = parse_copyup(
                self.archive(probe, previous["path"], f"post-sentinel-{index}.tar"),
                previous["path"],
            )
            require(now["entries"] == previous["entries"], "COPYUP_CONTENT_DRIFT")

    def verify_kafka(self) -> None:
        container = self.bound_id("sandbox/container/kafka")
        status = self.runtime.docker("exec", container, "cat", "/proc/1/status")
        verify_process_identity(status, self.uid, self.gid)
        observations = []
        for measured in self.measurements:
            for relative, expected in measured["entries"].items():
                path = (
                    measured["path"]
                    if relative == "."
                    else measured["path"] + "/" + relative
                )
                stat_args = (
                    "exec",
                    container,
                    "stat",
                    "-c",
                    "%u %g %a %F %i %s %y %z",
                    "--",
                    path,
                )
                before = self.runtime.docker(*stat_args).strip()
                parts = before.split()
                require(
                    parts[:3]
                    == [
                        str(expected["uid"]),
                        str(expected["gid"]),
                        format(expected["mode"], "o"),
                    ],
                    "KAFKA_SEED_MODE_DRIFT",
                    path,
                )
                kind = "directory" if expected["kind"] == "directory" else "regular"
                require(parts[3] == kind, "KAFKA_SEED_KIND_DRIFT", path)
                if expected["kind"] == "file":
                    value = self.runtime.docker(
                        "exec", container, "sha256sum", "--", path
                    ).split()[0]
                    require(value == expected["sha256"], "COPYUP_CONTENT_DRIFT", path)
                require(
                    self.runtime.docker(*stat_args).strip() == before,
                    "RACED_SEED_CAPTURE",
                )
                observations.append({"path": path, "stat": before})
        # A stopped helper already measured all three sentinel paths absent.
        # Check absence again in the live Kafka mount namespace before business traffic.
        absent = self.runtime.docker(
            "exec",
            container,
            "/bin/sh",
            "-c",
            'set -eu; for p in /etc/kafka/secrets /mnt/shared/config /var/lib/kafka/data; do test ! -e "$p/.ecomsre-v040-qualification-sentinel"; test ! -L "$p/.ecomsre-v040-qualification-sentinel"; done; printf ABSENT',
        )
        require(absent == "ABSENT", "SENTINEL_NOT_REMOVED")
        seal_private(
            self.runtime.private / "host/live-kafka-identity-and-seeds.json",
            {
                "uid": self.uid,
                "gid": self.gid,
                "process_status": status,
                "seed_entries": observations,
                "sentinel_absent": True,
            },
        )

    def compose_up(self, group: str, services: tuple[str, ...]) -> None:
        project = SANDBOX_PROJECT if group == "sandbox" else PRODUCT_PROJECT
        self.runtime.compose_plan(
            project,
            self.compose_paths[group],
            "up",
            "-d",
            "--pull",
            "never",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "300" if group == "sandbox" else "60",
            *services,
            timeout=360 if group == "sandbox" else 90,
        )
        if group == "sandbox":
            require(
                all(self.environment.service_health().values()), "SANDBOX_UNHEALTHY"
            )
        elif "remediation-observer" in services:
            self.runtime.wait_host_ready()

    def read_authority_inputs(self) -> dict[str, str]:
        assert self.journal is not None
        from ecomsre.dta_v2.telemetry_adapters import _build_owned_read_authority

        runtime = self.runtime
        daemon = runtime.boundary()
        resolved, _ = self.environment.resolve()
        authority = _build_owned_read_authority(
            daemon_identity=daemon["daemon_id"],
            docker_context=daemon["context"],
            config_bundle_sha256=semantic_sha256(
                {
                    "bundle": self.environment.bundle.model_dump(mode="json"),
                    "qualification_plan_sha256": self.journal.plan_sha,
                }
            ),
            resolved_sandbox_sha256=semantic_sha256(resolved.model_dump(mode="json")),
            prometheus_base_url=resolved.endpoints.prometheus,
            opensearch_base_url=resolved.endpoints.opensearch,
            jaeger_base_url=resolved.endpoints.jaeger,
            docker_endpoint=daemon["endpoint"],
            compose_project=SANDBOX_PROJECT,
            sandbox_label_key=QUAL_LABEL,
            sandbox_label_value=runtime.qualification,
        )
        return {
            key: getattr(authority, key)
            for key in (
                "daemon_identity_sha256",
                "docker_context_sha256",
                "config_bundle_sha256",
                "resolved_sandbox_sha256",
                "resolved_endpoints_sha256",
                "ownership_scope_sha256",
            )
        }

    def isolation(self) -> dict[str, Any]:
        sandbox = json.loads(
            self.runtime.docker(
                "container",
                "inspect",
                self.bound_id("sandbox/container/frontend-proxy"),
                self.bound_id("sandbox/container/flagd"),
            )
        )
        targets = [("host.docker.internal", 18080), ("host.docker.internal", 18016)]
        for row, port in zip(sandbox, (8080, 8016), strict=True):
            targets.append(
                (
                    row["NetworkSettings"]["Networks"][
                        "ecomsre-live-sandbox-v1-default"
                    ]["IPAddress"],
                    port,
                )
            )
        result = {}
        for service in ("api", "worker"):
            value = json.loads(
                self.runtime.docker(
                    "exec",
                    self.bound_id("product/container/" + service),
                    "python",
                    "-c",
                    NETWORK_PROBE,
                    json.dumps(targets),
                    timeout=40,
                )
            )
            require(
                all(value["denied"])
                and all(v for k, v in value.items() if k != "denied"),
                "NETWORK_DENIAL_FAILED",
                service,
            )
            result[service] = value
        current = self.snapshot()
        owned = [
            row
            for row in current["inspect"]["containers"]
            if labels("containers", row).get(QUAL_LABEL) == self.runtime.qualification
        ]
        product = [
            row
            for row in owned
            if labels("containers", row).get("com.docker.compose.project")
            == PRODUCT_PROJECT
        ]
        require(
            {labels("containers", row)["com.docker.compose.service"] for row in product}
            == {"api", "worker", "remediation-observer"},
            "EXECUTOR_PRESENT",
        )
        for row in product:
            if labels("containers", row)["com.docker.compose.service"] in {
                "api",
                "worker",
            }:
                env = dict(v.split("=", 1) for v in row["Config"]["Env"])
                require(
                    env.get("ECOMSRE_REMEDIATION_ENABLED") == "0"
                    and not env.get("ECOMSRE_REMEDIATION_BINDING_PATH")
                    and not any(
                        v
                        for k, v in env.items()
                        if k.startswith("ECOMSRE_REMEDIATION_") and k.endswith("_TOKEN")
                    ),
                    "REMEDIATION_NOT_DISABLED",
                )
        result["executor_unavailable"] = True
        result["gateway_unavailable"] = True
        result["api_worker_remediation_disabled"] = True
        result["private_storage"] = private_storage_modes(self.runtime)
        seal_private(self.runtime.private / "host/runtime-isolation.json", result)
        return result

    def run(self) -> None:
        assert self.journal is not None
        roles = self.journal.plan["roles"]
        stage_index = {s["name"]: i for i, s in enumerate(self.journal.plan["stages"])}
        ordered_roles = sorted(
            roles.items(), key=lambda item: stage_index[item[1]["birth_stage"]]
        )
        for key, role in ordered_roles:
            if role["kind"] == "volumes":
                self.step(
                    role["birth_stage"].removeprefix("AFTER_"),
                    lambda r=role: self.create_volume(r),
                )
        self.step("COPYUP_CREATE", self.create_probe)
        self.step("COPYUP_MEASUREMENT", self.measure_copyup)
        self.step("SENTINEL", self.sentinel)
        for _, role in ordered_roles:
            if role["kind"] == "networks":
                self.step(
                    role["birth_stage"].removeprefix("AFTER_"),
                    lambda r=role: self.create_network(r),
                )
        self.step("SANDBOX_START", lambda: self.compose_up("sandbox", ()))
        self.step("KAFKA_IDENTITY", self.verify_kafka)
        self.step(
            "PROBE_REMOVE",
            lambda: self.runtime.docker(
                "container", "rm", self.bound_id("probe/container/kafka-volume-probe")
            ),
        )
        self.step("PRODUCT_API_START", lambda: self.compose_up("product", ("api",)))
        self.step(
            "PRODUCT_READERS_START",
            lambda: self.compose_up("product", ("worker", "remediation-observer")),
        )
        baseline = healthy_baseline(
            self.runtime, self.lifecycle, self.read_authority_inputs(), self.checkpoint
        )
        self.result["healthy_traffic"] = baseline["traffic"]["traffic"]
        self.result["active_baseline"] = {
            k: baseline["baseline"][k] for k in ("baseline_id", "baseline_sha256")
        }
        control = self.step(
            "NO_INCIDENT",
            lambda: diagnosis(
                self.runtime,
                self.lifecycle,
                name="qualification-control",
                started_at=baseline["traffic"]["started_at"],
            ),
        )
        require(
            control["diagnosis"]["terminal"] == "NO_INCIDENT",
            "NO_INCIDENT_NOT_OBSERVED",
        )
        self.result["no_incident"] = {
            "terminal": "NO_INCIDENT",
            "evidence_sha256": sha(control),
        }
        self.result["isolation"] = self.step("NETWORK_DENIAL", self.isolation)
        self.result["status"] = "PASS_NO_FAULT_ONLY"

    def zero_database_counts(self) -> dict[str, Any]:
        path = self.runtime.private / "product/product.sqlite3"
        names = {
            "remediation_candidates": "formal_candidates",
            "remediation_approvals": "approvals",
            "remediation_authorizations": "attempt_authorizations",
            "remediation_attempts": "formal_campaign_executions",
            "remediation_write_intents": "write_intents",
            "remediation_executor_dispatches": "executor_invocations",
            "remediation_step_receipts": "step_receipts",
            "remediation_recovery_windows": "formal_recovery_windows",
        }
        result: dict[str, Any] = {"status": "UNKNOWN", "tables": {}}
        error: Exception | None = None
        try:
            if not path.exists():
                require(
                    "product/container/api" not in self.attempted_births,
                    "FORMAL_DATABASE_MISSING",
                )
                result = {"status": "NOT_CREATED_BEFORE_PRODUCT_START", "tables": {}}
            else:
                with sqlite3.connect(
                    path.as_uri() + "?mode=ro", uri=True
                ) as connection:
                    tables = {
                        r[0]
                        for r in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    for name in names:
                        result["tables"][name] = (
                            int(
                                connection.execute(
                                    "SELECT COUNT(*) FROM " + name
                                ).fetchone()[0]
                            )
                            if name in tables
                            else None
                        )
                result["status"] = (
                    "NONZERO"
                    if any(v for v in result["tables"].values())
                    else "UNKNOWN"
                    if None in result["tables"].values()
                    else "ZERO"
                )
        except Exception as caught:
            error = caught
            result["error_type"] = type(caught).__name__
        # Retain observations BEFORE asserting; never print fabricated zeros.
        for name, counter in names.items():
            if result["status"] not in {"NOT_CREATED_BEFORE_PRODUCT_START"}:
                self.result["counters"][counter] = result["tables"].get(name)
        if result["status"] in {"NONZERO", "UNKNOWN"}:
            self.result["counters"]["remediation_writes"] = None
        self.result["counter_evidence"] = result
        seal_private(self.runtime.private / "host/formal-database-counts.json", result)
        require(
            error is None and result["status"] != "UNKNOWN", "FORMAL_COUNTER_UNKNOWN"
        )
        require(result["status"] != "NONZERO", "FORMAL_COUNTER_NONZERO")
        return result

    def latch_cleanup_failure(self, error: Exception) -> None:
        code = getattr(error, "code", "CLEANUP_PROTOCOL_ERROR")
        if self.journal is not None:
            try:
                self.journal.block(
                    code,
                    "CLEANUP",
                    type(error).__name__,
                    observed_counters=self.result["counters"],
                    counter_evidence=self.result["counter_evidence"],
                )
            except QualificationBlocked:
                pass
        else:
            path = self.runtime.private / "host/preflight-first-divergence.json"
            if not path.exists():
                seal_private(
                    path,
                    {
                        "code": code,
                        "stage": "CLEANUP",
                        "formal_authority": False,
                        "counters": self.result["counters"],
                    },
                )

    def cleanup(self) -> dict[str, Any]:
        if self.journal is None:
            # Cleanup inventory must not depend on a failed platform-reference lookup.
            final = capture(
                self.runtime, self.runtime.qualification, [], self.mutable_binds
            )
            validate_envelope(final)
            self.zero_database_counts()
            seal_private(
                self.runtime.private / "host/preflight-cleanup-inventory.json", final
            )
            counts = {
                kind: sum(
                    labels(kind, row).get(QUAL_LABEL) == self.runtime.qualification
                    for row in final["inspect"][kind]
                )
                for kind in ("containers", "networks", "volumes")
            }
            before = getattr(self, "preflight_initial", None)
            unchanged = final["inspect"] == before["inspect"] if before else None
            return {
                "status": "NO_OWNED_CREATION_ATTEMPTED",
                "owned_remaining": counts,
                "nonowned_unchanged": unchanged,
                "nonowned_comparison": "MEASURED"
                if before
                else "INITIAL_BOUNDARY_NOT_ESTABLISHED",
            }
        runtime = self.runtime
        plan = self.journal.plan
        failed = (self.journal.root / "first-divergence.json").exists()
        # Cleanup is separately authorized, never clears or advances a failed journal.
        if not failed:
            try:
                self.checkpoint("BEFORE_CLEANUP")
            except QualificationBlocked:
                failed = True
                self.result["status"] = "BLOCKED_PRE_EXECUTION"
        self.cleanup_mode = True
        cleanup_root = runtime.private / "host/cleanup"
        cleanup_root.mkdir(mode=0o700)
        sequence = 0

        def observe() -> dict[str, Any]:
            nonlocal sequence
            saved = self.snapshot()
            validate_envelope(saved)
            seal_private(cleanup_root / f"{sequence:03d}-inventory.json", saved)
            sequence += 1
            require(
                saved["daemon_before"] == plan["daemon"] == saved["daemon_after"],
                "CLEANUP_DAEMON_DRIFT",
            )
            return saved

        def targets(kind: str) -> list[str]:
            assert self.journal is not None
            saved = observe()
            output = []
            for row in saved["inspect"][kind]:
                if labels(kind, row).get(QUAL_LABEL) != runtime.qualification:
                    continue
                candidates = [
                    key
                    for key, role in plan["roles"].items()
                    if role["kind"] == kind and role["name"] == name(kind, row)
                ]
                require(len(candidates) == 1, "CLEANUP_UNKNOWN_RESOURCE")
                key = candidates[0]
                require(key in self.attempted_births, "CLEANUP_UNAUTHORIZED_BIRTH")
                role = plan["roles"][key]
                validate_role(role, row, runtime.qualification)
                if key in self.journal.bound:
                    require(
                        self.same(self.journal.bound[key], kind, row),
                        "CLEANUP_RESOURCE_REPLACED",
                    )
                else:
                    require(False, "CLEANUP_UNBOUND_RESOURCE", key)
                output.append(identity(kind, row))
            return output

        def cleanup_action(action: str, operation: Any) -> None:
            if not failed:
                self.step(action, operation)
            else:
                self.active_stage = "BEFORE_" + action
                operation()
                self.active_stage = "AFTER_" + action
                observe()

        def mutate(kind: str, verb: str) -> None:
            assert self.journal is not None
            ids = targets(kind)
            if kind == "containers" and verb == "stop":
                self.prepare_cleanup_stop()
            seal_private(
                cleanup_root / f"{sequence:03d}-{verb}-{kind}-intent.json",
                {
                    "ids": ids,
                    "operation": verb,
                    "plan_sha256": self.journal.plan_sha,
                    "formal_authority": False,
                },
            )
            if ids:
                runtime.docker(
                    {
                        "containers": "container",
                        "networks": "network",
                        "volumes": "volume",
                    }[kind],
                    verb,
                    *(("--time", "30") if verb == "stop" else ()),
                    *ids,
                    timeout=60,
                )

        cleanup_action("OWNED_STOP", lambda: mutate("containers", "stop"))
        counter_error = None
        try:
            database = self.zero_database_counts()
        except QualificationBlocked as error:
            self.latch_cleanup_failure(error)
            self.result["status"] = "BLOCKED_PRE_EXECUTION"
            failed = True
            counter_error = error.code
            database = self.result["counter_evidence"]
        cleanup_action("CONTAINERS_REMOVE", lambda: mutate("containers", "rm"))
        cleanup_action("NETWORKS_REMOVE", lambda: mutate("networks", "rm"))
        cleanup_action("VOLUMES_REMOVE", lambda: mutate("volumes", "rm"))
        final = observe()
        counts = {
            kind: sum(
                labels(kind, row).get(QUAL_LABEL) == runtime.qualification
                for row in final["inspect"][kind]
            )
            for kind in ("containers", "networks", "volumes")
        }
        nonowned = {
            kind: {identity(kind, row): row for row in final["inspect"][kind]}
            for kind in KINDS
        }
        unchanged = nonowned == plan["nonowned"]
        require(not any(counts.values()), "CLEANUP_INCOMPLETE")
        if not failed:
            self.checkpoint("POST_CLEANUP_READBACK")
        return {
            "status": "CLEAN" if unchanged else "NONOWNED_DRIFT_RETAINED",
            "owned_remaining": counts,
            "nonowned_before_sha256": sha(plan["nonowned"]),
            "nonowned_after_sha256": sha(nonowned),
            "nonowned_unchanged": unchanged,
            "formal_database_counts": database,
            "counter_error": counter_error,
        }
