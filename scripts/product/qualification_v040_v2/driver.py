"""One-shot v2 successor using frozen v1 lifecycle components by inheritance."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import socket
import tarfile
from types import SimpleNamespace
from typing import Any

from scripts.product.v040_ownership import tree_commitment
from scripts.product.v040_preparation import diagnosis
from scripts.product.v040_runtime import read_json, seal_private
from scripts.product.qualification_v040.capture import (
    platform_images,
    pinned_cached_reference,
)
from scripts.product.qualification_v040.guard import (
    labels,
    name,
    require,
    sha,
    validate_role,
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
    parse_copyup,
    runtime_user,
)

from scripts.product.qualification_v040.driver import Driver
from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040_v2.journal import V2Journal
from scripts.product.qualification_v040_v2.data import (
    DATA_METADATA_PROTOCOL,
    parse_data_metadata,
)
from scripts.product.qualification_v040_v2.policy import (
    validate_policy,
    provenance_gate,
    access_gate,
)
from scripts.product.qualification_v040_v2.processes import (
    CENSUS_PROTOCOL,
    parse_census,
    approved_process_argv,
)
from scripts.product.qualification_v040_v2.sentinel import (
    SENTINEL_V2,
    validate_sentinel_v2,
)


class V2Driver(Driver):
    def __init__(self, runtime: QualificationRuntime, policy: dict[str, Any]) -> None:
        super().__init__(runtime)
        validate_policy(policy)
        self.policy = deepcopy(policy)
        self.census_count = 0
        self.result.update(
            {
                "policy_id": policy["policy_id"],
                "policy_sha256": sha(policy),
                "prior_v1": policy["prior_v1"],
                "access_census": [],
                "runtime_process_identity": None,
                "sentinel": "NOT_EXECUTED",
            }
        )

    def setup(self, historical: Path) -> None:
        runtime = self.runtime
        runtime.stabilize()
        self.environment, resolved, sandbox, baseline = prepare_sandbox(
            runtime, historical
        )
        product = prepare_product(runtime, historical)
        startup = self.policy["kafka_startup_contract"]
        require(
            sandbox["services"]["kafka"]["environment"]
            == startup["compose_environment_before_locale_override"],
            "KAFKA_STARTUP_ENV_DRIFT",
        )
        sandbox["services"]["kafka"]["environment"].update(startup["locale_override"])
        sandbox["services"]["kafka"]["cap_drop"] = ["ALL"]
        sandbox["services"]["kafka"]["security_opt"] = ["no-new-privileges:true"]
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
            kafka["image"] == self.policy["pinned_reference"]
            and image["Id"] == self.policy["image_source"]["platform_digest"]
            and image["RootFS"]["Layers"]
            == self.policy["image_source"]["rootfs_diff_ids"]
            and image["Config"]["User"] == self.policy["image_source"]["config_user"],
            "IMAGE_SOURCE_MISMATCH",
        )
        require(
            set(image["Config"].get("Volumes") or {}) == set(KAFKA_PATHS),
            "KAFKA_IMAGE_VOLUME_DRIFT",
        )
        probe_service = {
            "container_name": "ecomsre-v040-" + runtime.qualification + "-volume-probe",
            "image": kafka["image"],
            "entrypoint": ["/bin/sleep"],
            "command": ["2147483647"],
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
        plan["copyup_access_policy"] = self.policy
        plan["copyup_access_policy_sha256"] = sha(self.policy)
        for after, action in [
            ("AFTER_COPYUP_MEASUREMENT", "PROBE_START"),
            ("AFTER_PROBE_START", "ACCESS_CENSUS"),
            ("AFTER_SENTINEL", "PROBE_STOP"),
        ]:
            position = next(
                i for i, s in enumerate(plan["stages"]) if s["name"] == after
            )
            present = plan["stages"][position]["present_roles"]
            plan["stages"][position + 1 : position + 1] = [
                {"name": prefix + action, "present_roles": list(present)}
                for prefix in ("BEFORE_", "AFTER_")
            ]
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
        self.journal = V2Journal(runtime.private / "host/stage-journal", plan)
        self.journal.observe("INITIAL", initial)

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
            "/bin/sleep",
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
            "2147483647",
        )

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
                    SENTINEL_V2.encode()
                ).hexdigest(),
            },
        )
        self.result["copyup"] = {
            "uid": self.uid,
            "gid": self.gid,
            "measurements": self.measurements,
            "writeability": "NOT_EXECUTED",
        }
        provenance_gate(
            self.policy,
            self.measurements,
            {
                "reference": self.policy["pinned_reference"],
                "platform_digest": self.policy["image_source"]["platform_digest"],
                "config_digest": self.policy["image_source"]["config_digest"],
            },
        )

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
        self.step(
            "PROBE_START",
            lambda: self.runtime.docker(
                "container",
                "start",
                self.bound_id("probe/container/kafka-volume-probe"),
            ),
        )
        self.step("ACCESS_CENSUS", lambda: None)
        self.step("SENTINEL", self.sentinel)
        self.step(
            "PROBE_STOP",
            lambda: self.runtime.docker(
                "container",
                "stop",
                "--time",
                "5",
                self.bound_id("probe/container/kafka-volume-probe"),
            ),
        )
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

    def capture_seeds(self, inspected: dict[str, Any]) -> dict[str, Any]:
        if self.journal is not None and not self.cleanup_mode:
            self.access_census(inspected)
        kafka_rows = [
            r
            for r in inspected["containers"]
            if self.journal is not None
            and r["Name"].lstrip("/")
            == self.journal.plan["roles"]["sandbox/container/kafka"]["name"]
            and r["State"]["Running"]
        ]
        if self.cleanup_mode or not self.measurements or not kafka_rows:
            return super().capture_seeds(inspected)
        assert self.journal is not None
        row = kafka_rows[0]
        validate_role(
            self.journal.plan["roles"]["sandbox/container/kafka"],
            row,
            self.runtime.qualification,
        )
        container = row["Id"]
        # Census already authenticated the complete attachment set and writer
        # identities before these fixed read-only measurements.
        self.capture_sequence += 1
        passes = []
        for sweep in range(2):
            measured = [
                parse_copyup(
                    self.archive(
                        self.bound_id("sandbox/container/kafka")
                        if "sandbox/container/kafka" in self.journal.bound
                        else self.bound_id("probe/container/kafka-volume-probe"),
                        path,
                        f"v2-seed-{self.capture_sequence:03d}-{sweep}-{i}.tar",
                    ),
                    path,
                )
                for i, path in enumerate(KAFKA_PATHS[:2])
            ]
            raw = self.runtime.docker(
                "exec", container, "/bin/bash", "-c", DATA_METADATA_PROTOCOL, timeout=30
            )
            measured.append(parse_data_metadata(raw))
            passes.append(measured)
        require(passes[0][2]["inodes"] == passes[1][2]["inodes"], "RACED_DATA_METADATA")
        return {
            "status": "MEASURED",
            "initial": self.measurements,
            "uid": self.uid,
            "gid": self.gid,
            "before": passes[0],
            "after": passes[1],
        }

    def access_census(self, inspected: dict[str, Any]) -> None:
        assert self.journal is not None
        plan = self.journal.plan
        stages = [s["name"] for s in plan["stages"]]
        stage = self.active_stage
        if stage not in stages or stages.index(stage) < stages.index(
            "AFTER_PROBE_START"
        ):
            return
        volume_roles = {
            key: role
            for key, role in plan["roles"].items()
            if role["kind"] == "volumes" and "qualification-kafka-" in key
        }
        expected_volumes = {r["name"] for r in volume_roles.values()}
        fresh = {
            r["name"]
            for k, r in volume_roles.items()
            if k in self.journal.bound and k in self.attempted_births
        }
        volume_rows = {r["Name"]: r for r in inspected["volumes"]}
        for key, role in volume_roles.items():
            require(
                resource_fixed("volumes", volume_rows[role["name"]])
                == self.journal.bound[key],
                "REUSED_OR_UNBOUND_VOLUME",
            )
        attachments, expected, readers = [], [], []
        present = set(plan["stages"][stages.index(stage)]["present_roles"])
        for row in inspected["containers"]:
            selected = [
                m
                for m in row["Mounts"]
                if m.get("Name") in expected_volumes
                or m.get("Source")
                in {volume_rows[n]["Mountpoint"] for n in expected_volumes}
            ]
            if not selected:
                continue
            keys = [
                k
                for k, role in plan["roles"].items()
                if role["kind"] == "containers"
                and role["name"] == name("containers", row)
            ]
            require(
                len(keys) == 1
                and keys[0] in present
                and keys[0]
                in {"probe/container/kafka-volume-probe", "sandbox/container/kafka"},
                "EXTRA_OR_UNBOUND_ATTACHMENT",
            )
            key = keys[0]
            role = plan["roles"][key]
            validate_role(role, row, self.runtime.qualification)
            if key in self.journal.bound:
                require(
                    resource_fixed("containers", row) == self.journal.bound[key],
                    "WRITER_CONTAINER_DRIFT",
                )
            else:
                require(
                    role["birth_stage"] == stage and key in self.attempted_births,
                    "UNBOUND_WRITER_CONTAINER",
                )
            for mount in selected:
                attachments.append(
                    {
                        "container": row["Id"],
                        "volume": mount.get("Name"),
                        "target": mount["Destination"],
                        "source": mount["Source"],
                        "rw": mount["RW"],
                        "type": mount["Type"],
                    }
                )
            for target, mount in role["mounts"].items():
                if mount["source"] in expected_volumes:
                    expected.append(
                        {
                            "container": row["Id"],
                            "volume": mount["source"],
                            "target": target,
                            "source": volume_rows[mount["source"]]["Mountpoint"],
                            "rw": mount["rw"],
                            "type": "volume",
                        }
                    )
            if row["State"]["Running"]:
                readers.append((key, row["Id"]))
        require(
            sorted(attachments, key=sha) == sorted(expected, key=sha),
            "EXTRA_OR_UNBOUND_ATTACHMENT",
        )
        expected_readers = set()
        if (
            stages.index("AFTER_PROBE_START")
            <= stages.index(stage)
            < stages.index("AFTER_PROBE_STOP")
        ):
            expected_readers.add("probe/container/kafka-volume-probe")
        if stages.index(stage) >= stages.index("AFTER_SANDBOX_START"):
            expected_readers.add("sandbox/container/kafka")
        require(
            {key for key, _ in readers} == expected_readers,
            "RUNNING_WRITER_CENSUS_INCOMPLETE",
        )
        record: dict[str, Any] = {
            "stage": stage,
            "attachments": attachments,
            "expected_attachments": expected,
            "fresh_volumes": sorted(fresh),
            "process_census": [],
            "formal_authority": False,
        }
        for key, container in readers:
            raw = self.runtime.docker(
                "exec", container, "/bin/bash", "-c", CENSUS_PROTOCOL, timeout=20
            )
            census = parse_census(raw)
            record["process_census"].append(
                {
                    "role": key,
                    "container": container,
                    "census": census,
                    "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                }
            )
        self.census_count += 1
        seal_private(
            self.runtime.private
            / "host"
            / f"access-census-{self.census_count:03d}.json",
            record,
        )
        self.result["access_census"].append(
            {"stage": stage, "evidence_sha256": sha(record)}
        )
        for reader in record["process_census"]:
            role = "probe" if reader["role"].startswith("probe/") else "kafka"
            approved = approved_process_argv(
                reader["census"], role, self.policy["kafka_startup_contract"]["argv"]
            )
            access_gate(
                self.policy,
                reader["census"]["processes"],
                approved,
                attachments,
                expected,
                fresh,
                expected_volumes,
            )
            primary = next(p for p in reader["census"]["processes"] if p["pid"] == 1)
            self.result["runtime_process_identity"] = {
                "role": role,
                "identity": primary,
            }

    def sentinel(self) -> None:
        self.result["sentinel"] = {"status": "ATTEMPTED_OUTCOME_UNKNOWN"}
        self.result["copyup"]["writeability"] = "ATTEMPTED_OUTCOME_UNKNOWN"
        seal_private(
            self.runtime.private / "host/sentinel-v2-intent.json",
            {
                "qualification_id": self.runtime.qualification,
                "protocol_sha256": hashlib.sha256(SENTINEL_V2.encode()).hexdigest(),
                "activity": "QUALIFICATION_ONLY",
                "formal_authority": False,
            },
        )
        try:
            raw = self.runtime.docker(
                "exec",
                self.bound_id("probe/container/kafka-volume-probe"),
                "/bin/bash",
                "-c",
                SENTINEL_V2,
                "qualification-sentinel",
                self.runtime.qualification,
                timeout=30,
            )
        except Exception as error:
            raise QualificationBlocked(
                "SENTINEL_EXECUTION_FAILED", type(error).__name__
            ) from error
        # Persist raw output before validation; a failure never becomes PASS.
        seal_private(
            self.runtime.private / "host/sentinel-v2-observation.json",
            {"stdout": raw, "formal_authority": False},
        )
        result = validate_sentinel_v2(raw, self.policy, self.runtime.qualification)
        self.result["sentinel"] = result
        self.result["copyup"]["writeability"] = result
        seal_private(self.runtime.private / "host/sentinel-v2-result.json", result)
