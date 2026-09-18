"""New Goal-owned subset; reuse pinned services and typed evaluator controls only."""

from scripts.product_v050.preflight import inspect as inspect_ledger

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import uuid


from scripts.product.minimal_payment_acceptance_v040.owned import (
    Owned as BaseOwned,
    command,
    digest,
    save,
    inventory,
)
from ecomsre_live_sandbox.product_v030 import build_product_v030_runtime_bundle
from ecomsre_live_sandbox.knowledge_v030 import build_goal_flag_documents_v030

REPO = Path(__file__).resolve().parents[2]
UPSTREAM = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
PORTS = {
    "frontend": (18080, 8080),
    "prometheus": (19090, 9090),
    "jaeger": (16686, 16686),
    "opensearch": (19200, 9200),
    "flagd": (18016, 8016),
    "flagd-ui": (18081, 4000),
}


def campaign_root(root):
    root = Path(root)
    allowed = REPO / ".local/product-v050/live-02"
    permitted = {allowed, allowed / "diagnostic-01", allowed / "postgres-user-01"}
    if root not in permitted or root.resolve() != root or root.is_symlink():
        raise ValueError("CAMPAIGN_PATH_NOT_AUTHORIZED")
    return root


def load(name, root):
    return json.loads((root / name).read_text())


def prepare(root):
    root = campaign_root(root)
    if any(
        (root / name).exists()
        for name in ("manifest.json", "documents.json", "before.json")
    ):
        raise ValueError("CAMPAIGN_ALREADY_PREPARED")
    if (
        command(
            "git",
            "-C",
            str(REPO / "third_party/opentelemetry-demo"),
            "rev-parse",
            "HEAD",
        )
        != UPSTREAM
    ):
        raise ValueError("UPSTREAM_CHANGED")
    source = load("upstream-pinned-resolved.json", root)
    images = load("cached-images.json", root)
    if root.name in {"diagnostic-01", "postgres-user-01"}:
        parent = root.parent
        if (
            not load("cleanup.json", parent)["result"]["clean"]
            or not (parent / "first-failure.json").is_file()
        ):
            raise ValueError("DIAGNOSTIC_PARENT_NOT_CLEAN_FAILED")
        if load("admitted-baseline.json", root) != load(
            "admitted-baseline.json", parent
        ):
            raise ValueError("DIAGNOSTIC_BASELINE_REPLACEMENT")
        if root.name == "postgres-user-01":
            diagnostic = parent / "diagnostic-01"
            if not load("cleanup.json", diagnostic)["result"]["clean"]:
                raise ValueError("DIAGNOSTIC_NOT_CLEAN")
            failure = load("start-command-failure.json", diagnostic)
            if not any(
                row.get("Status") == "exited" and row.get("ExitCode") == 1
                for row in failure["states"].values()
            ):
                raise ValueError("MISSING_STARTUP_FAILURE_EVIDENCE")
        nonce = load("manifest.json", parent)["campaign"]
    else:
        nonce = "ecomsre-v050-" + uuid.uuid4().hex[:10]
    labels = {
        "io.ecomsre.minimal.goal": hashlib.sha256(
            (REPO / "docs/goals/EcomSRE_v0.5_Live_Resume_Amendment.md").read_bytes()
        ).hexdigest(),
        "io.ecomsre.minimal.attempt": nonce,
        "io.ecomsre.sandbox.id": nonce,
    }
    bundle = build_product_v030_runtime_bundle(REPO)
    _, documents = build_goal_flag_documents_v030(
        json.loads(
            (
                REPO / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
            ).read_text()
        ),
        bundle,
    )
    save(root / "documents.json", documents)
    flags = root / "control"
    flags.mkdir(mode=0o700)
    save(flags / "demo.flagd.json", documents["BASELINE"])
    plan = {
        "name": nonce,
        "services": {},
        "networks": {"default": {"name": nonce + "-default", "external": True}},
        "volumes": {},
    }
    for name, image in images.items():
        s = deepcopy(source["services"][name])
        for key in [
            "build",
            "develop",
            "deploy",
            "container_name",
            "networks",
            "ports",
            "volumes",
        ]:
            s.pop(key, None)
        s.update(
            image=image["runtime_reference"],
            pull_policy="never",
            platform="linux/arm64",
            restart="no",
            container_name=nonce + "-" + name,
            labels=labels,
            networks=["default"],
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )
        if name == "astronomy-db" and root.name == "postgres-user-01":
            # Verified fixed image has postgres uid/gid 999 and owned data/run dirs.
            # Avoid root chown/gosu with ALL capabilities dropped; grant no caps.
            if (
                image["runtime_reference"]
                != "postgres@sha256:3a82e1f56c8f0f5616a11103ac3d47e632c3938698946a7ad26da0df1334744a"
            ):
                raise ValueError("POSTGRES_USER_IMAGE_NOT_VERIFIED")
            s["user"] = "999:999"
        # Keep each frozen image's filesystem semantics; no writable host root.
        s["mem_limit"] = (
            "2g"
            if name == "opensearch"
            else "1g"
            if name in {"kafka", "astronomy-db"}
            else "768m"
        )
        s["depends_on"] = {
            n: v for n, v in s.get("depends_on", {}).items() if n in images
        }
        if name == "otel-collector":
            s["depends_on"] = {
                n: {"condition": "service_started"}
                for n in ["prometheus", "jaeger", "opensearch"]
            }
        mounts = []
        for m in source["services"][name].get("volumes", []):
            if name == "otel-collector":
                continue
            m = deepcopy(m)
            m["read_only"] = True
            m.pop("bind", None)
            if name in {"flagd", "flagd-ui"}:
                m["source"] = str(flags)
                m["read_only"] = name == "flagd"
            if not Path(m["source"]).exists():
                raise ValueError("MISSING_PINNED_BIND:" + name)
            mounts.append(m)
        for i, target in enumerate(image["Config"].get("Volumes") or {}):
            if target == "/tmp":
                s.setdefault("tmpfs", []).append("/tmp:rw,nosuid,nodev,size=128m")
                continue
            vol = nonce + "-" + name + "-" + str(i)
            plan["volumes"][vol] = {"name": vol, "external": True}
            mounts.append(
                {"type": "volume", "source": vol, "target": target, "read_only": False}
            )
        s["volumes"] = mounts
        if name in PORTS:
            port, target = PORTS[name]
            s["ports"] = [
                {
                    "target": target,
                    "published": str(port),
                    "host_ip": "127.0.0.1",
                    "protocol": "tcp",
                }
            ]
        plan["services"][name] = s
    # Finite subset of the frozen collector receivers/exporters, no host-metrics root mount.
    collector = {
        "receivers": {
            "otlp": {
                "protocols": {
                    "grpc": {"endpoint": "0.0.0.0:4317"},
                    "http": {"endpoint": "0.0.0.0:4318"},
                }
            },
            "docker_stats": {
                "endpoint": "unix:///var/run/docker.sock",
                "api_version": "1.44",
                "collection_interval": "2s",
                "container_labels_to_metric_labels": {
                    "com.docker.compose.project": "compose_project",
                    "com.docker.compose.service": "compose_service",
                    "io.ecomsre.sandbox.id": "sandbox_id",
                },
            },
            "kafkametrics": {
                "scrapers": ["brokers", "topics", "consumers"],
                "brokers": ["kafka:9092"],
                "collection_interval": "10s",
            },
        },
        "connectors": {"span_metrics": {"metrics_flush_interval": "5s"}},
        "processors": {
            "memory_limiter": {
                "check_interval": "5s",
                "limit_mib": 500,
                "spike_limit_mib": 100,
            }
        },
        "exporters": {
            "otlp_grpc/jaeger": {"endpoint": "jaeger:4317", "tls": {"insecure": True}},
            "otlp_http/prometheus": {"endpoint": "http://prometheus:9090/api/v1/otlp"},
            "opensearch": {
                "logs_index": "otel-logs",
                "logs_index_time_format": "yyyy-MM-dd",
                "http": {"endpoint": "http://opensearch:9200"},
            },
        },
        "service": {
            "pipelines": {
                "traces": {
                    "receivers": ["otlp"],
                    "processors": ["memory_limiter"],
                    "exporters": ["otlp_grpc/jaeger", "span_metrics"],
                },
                "metrics": {
                    "receivers": [
                        "otlp",
                        "docker_stats",
                        "kafkametrics",
                        "span_metrics",
                    ],
                    "processors": ["memory_limiter"],
                    "exporters": ["otlp_http/prometheus"],
                },
                "logs": {
                    "receivers": ["otlp"],
                    "processors": ["memory_limiter"],
                    "exporters": ["opensearch"],
                },
            }
        },
    }
    save(root / "collector.json", collector)
    s = plan["services"]["otel-collector"]
    s["command"] = ["--config=/etc/ecomsre/collector.json"]
    s["volumes"] = [
        {
            "type": "bind",
            "source": str(root / "collector.json"),
            "target": "/etc/ecomsre/collector.json",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": "/var/run/docker.sock",
            "target": "/var/run/docker.sock",
            "read_only": True,
        },
    ]
    s["environment"]["OTEL_COLLECTOR_HOST"] = "0.0.0.0"
    # Existing Goal JMX semantics; source remains read-only.
    plan["services"]["kafka"]["environment"]["OTEL_JMX_CONFIG"] = (
        "/etc/ecomsre/kafka-jmx.yml"
    )
    plan["services"]["kafka"]["environment"]["OTEL_INSTRUMENTATION_METHODS_INCLUDE"] = (
        "kafka.server.KafkaApis[handleProduceRequest]"
    )
    plan["services"]["kafka"]["volumes"].append(
        {
            "type": "bind",
            "source": str(REPO / "config/product-v030/kafka-jmx.yml"),
            "target": "/etc/ecomsre/kafka-jmx.yml",
            "read_only": True,
        }
    )
    # Upstream Prometheus config contains only pinned local scrape endpoints.
    save(root / "compose.json", plan)
    save(
        root / "manifest.json",
        {
            "campaign": nonce,
            "admitted_baseline_sha256": digest(load("admitted-baseline.json", root)),
            "stability_amendment_sha256": hashlib.sha256(
                (
                    REPO / "docs/goals/EcomSRE_v0.5_Docker_Stability_Resume.md"
                ).read_bytes()
            ).hexdigest(),
            "goal": labels["io.ecomsre.minimal.goal"],
            "upstream": UPSTREAM,
            "services": sorted(images),
            "topology_sha256": digest(plan),
            "ports": PORTS,
            "labels": labels,
            "provider_ledger": str(REPO / ".local/product-v050/product.sqlite3"),
            "provider_start_count": inspect_ledger(REPO / ".local/product-v050")[
                "provider_request_count"
            ],
            "episode_cap": 12,
            "episode_order": {
                "e01": "DISCOVERY",
                "e02": "DISCOVERY",
                "e03": "DISCOVERY",
                "e04": "DEVELOPMENT",
                "e05": "DEVELOPMENT",
                "e06": "HOLDOUT",
                "e07": "REUSE",
            },
            "control": "existing BASELINE/QUEUE/PAYMENT documents and bounded checkout traffic",
            "new_product_writes": 0,
            "provider_container_mounts": [],
            "prepared_at": datetime.now(UTC).isoformat(),
        },
    )
    print(
        json.dumps(
            {
                "prepared": True,
                "services": len(images),
                "docker_mutations": 0,
                "topology_sha256": digest(plan),
            }
        )
    )


def effective_command(spec, image):
    # Compose null inherits the image; an explicit empty list remains an override.
    entry = spec.get("entrypoint")
    cmd = spec.get("command")
    if entry is None:
        entry = image["Config"].get("Entrypoint")
    if cmd is None:
        cmd = image["Config"].get("Cmd")
    return (entry or []) + (cmd or [])


def validate_networks(row, births):
    networks = row["NetworkSettings"]["Networks"]
    if set(networks) != {r["Name"] for r in births.values()}:
        raise ValueError("NETWORK_NAME_DRIFT")
    ids = {n["NetworkID"] for n in networks.values()}
    if row["State"]["Status"] == "created" and ids == {""}:
        return
    if ids != set(births):
        raise ValueError("NETWORK_DRIFT")


def source_matches(actual, expected, context, kind):
    if actual == expected:
        return True
    return (
        context == "desktop-linux"
        and kind == "bind"
        and expected.startswith(str(REPO) + "/")
        and actual == "/host_mnt" + expected
    )


class Owned(BaseOwned):
    def __init__(self, root):
        root = campaign_root(root)
        m = load("manifest.json", root)
        admission = load("admitted-baseline.json", root)
        if digest(admission) != m["admitted_baseline_sha256"]:
            raise ValueError("ADMITTED_BASELINE_BINDING")
        super().__init__(root, m["campaign"])
        if (
            self.before != admission["inventory"]
            or self.context != admission["context"]
            or self.daemon != admission["daemon"]
        ):
            raise ValueError("ADMITTED_BASELINE_DRIFT")
        self.labels = m["labels"]
        self.plan = load("compose.json", self.root)
        self.images = load("cached-images.json", self.root)
        self.expected_context = json.loads(
            command("docker", "context", "inspect", self.context)
        )[0]["Endpoints"]["docker"]["Host"]
        if self.expected_context != admission["endpoint"]:
            raise ValueError("ADMITTED_ENDPOINT_DRIFT")
        self.network_extra = admission["network_extra"]
        self.containers = {}

    def fresh(self):
        super().fresh()
        endpoint = json.loads(command("docker", "context", "inspect", self.context))[0][
            "Endpoints"
        ]["docker"]["Host"]
        if not endpoint.startswith("unix://") or endpoint != self.expected_context:
            raise ValueError("ENDPOINT_DRIFT")

    def validate(self, name, row):
        s = self.plan["services"][name]
        image = self.images[name]
        h = row["HostConfig"]
        c = row["Config"]
        if "user" in s and c.get("User") != s["user"]:
            raise ValueError("RUNTIME_USER_DRIFT")
        if any(c["Labels"].get(k) != v for k, v in self.labels.items()):
            raise ValueError("LABEL_DRIFT")
        if row["Image"] not in {image["Id"], image["index_id"]}:
            raise ValueError("IMAGE_DRIFT")
        if h["Privileged"] or h["NetworkMode"] == "host" or h["PidMode"] == "host":
            raise ValueError("HOST_AUTHORITY")
        if set(h["CapDrop"] or []) != {"ALL"} or h["ReadonlyRootfs"] != s.get(
            "read_only", False
        ):
            raise ValueError("HARDENING_DRIFT")
        expected_cmd = effective_command(s, image)
        if [row["Path"], *row["Args"]] != expected_cmd:
            raise ValueError("COMMAND_DRIFT")
        mounts = {m["Destination"]: m for m in row["Mounts"] if m["Type"] != "tmpfs"}
        if set(mounts) != {m["target"] for m in s["volumes"]}:
            raise ValueError("MOUNT_SET_DRIFT")
        for m in s["volumes"]:
            a = mounts[m["target"]]
            if (
                a["Type"] != m["type"]
                or a["RW"] == m.get("read_only", False)
                or not source_matches(
                    a.get("Name") if a["Type"] == "volume" else a["Source"],
                    m["source"],
                    self.context,
                    m["type"],
                )
            ):
                raise ValueError("MOUNT_DRIFT")
        wanted = {
            str(p["target"]) + "/tcp": [
                {"HostIp": "127.0.0.1", "HostPort": str(p["published"])}
            ]
            for p in s.get("ports", [])
        }
        if (h.get("PortBindings") or {}) != wanted:
            raise ValueError("PORT_DRIFT")
        validate_networks(row, self.births["network"])
        for key, value in s.get("environment", {}).items():
            if value is None:
                if any(v.startswith(key + "=") for v in c["Env"]):
                    raise ValueError("UNDECLARED_INHERITED_ENVIRONMENT")
            elif key + "=" + str(value) not in c["Env"]:
                raise ValueError("ENVIRONMENT_DRIFT")

    def unchanged(self):
        if not super().unchanged():
            return False
        return self.network_extra_unchanged()

    def network_extra_unchanged(self):
        rows = json.loads(command("docker", "network", "inspect", *self.network_extra))
        return {
            r["Id"]: {
                k: r.get(k) for k in ("Scope", "Attachable", "Ingress", "EnableIPv6")
            }
            for r in rows
        } == self.network_extra

    def verify(self):
        self.fresh()
        for name, cid in self.containers.items():
            self.validate(name, self.require_birth("container", cid))
        if not self.unchanged():
            raise ValueError("NON_OWNED_DRIFT")

    def capture_created(self):
        ids = command(
            "docker",
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            "label=io.ecomsre.minimal.attempt=" + self.nonce,
        ).split()
        errors = []
        # Register every provably owned birth before checking any container spec.
        for cid in ids:
            try:
                if cid not in self.births["container"]:
                    self.capture_birth("container", cid)
                name = self.births["container"][cid]["Config"]["Labels"][
                    "com.docker.compose.service"
                ]
                if name not in self.plan["services"]:
                    raise ValueError("UNEXPECTED_OWNED_CONTAINER")
                self.containers[name] = cid
            except Exception as exc:
                errors.append(type(exc).__name__)
        if errors:
            raise ValueError("BIRTH_DISCOVERY_INCOMPLETE:" + ",".join(errors))
        for name, cid in self.containers.items():
            self.validate(name, self.require_birth("container", cid))

    def create_aux(self, kind, name, *, internal=False):
        # A timed-out create may have committed. Recover only this predeclared fresh name.
        try:
            return super().create_aux(kind, name, internal=internal)
        finally:
            rows = subprocess.run(
                ["docker", kind, "inspect", name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if rows.returncode == 0:
                row = json.loads(rows.stdout)[0]
                identifier = row.get("Id", row.get("Name"))
                if identifier not in self.births[kind]:
                    self.capture_birth(kind, identifier)

    def cleanup(self):
        receipts = []
        for kind in ("container", "network", "volume"):
            for identifier in reversed(tuple(self.births[kind])):
                row = self.require_birth(kind, identifier)
                if kind == "container":
                    if row["State"]["Running"]:
                        command("docker", "stop", "--time", "10", identifier)
                    row = self.require_birth(kind, identifier)
                    if row["State"]["Running"]:
                        raise ValueError("STOP_NOT_CONFIRMED")
                    command("docker", "container", "rm", identifier)
                else:
                    if kind == "network" and row.get("Containers"):
                        raise ValueError("NETWORK_NOT_EMPTY")
                    # Docker refuses removal of an attached volume; no force flag.
                    command("docker", kind, "rm", identifier)
                receipts.append(
                    {
                        "kind": kind,
                        "identity_sha256": digest(identifier),
                        "removed": True,
                    }
                )
        after = inventory()
        remaining = {
            kind: len(set(after[kind]) & set(self.births[kind])) for kind in after
        }
        extra_error = None
        try:
            extra_unchanged = self.network_extra_unchanged()
        except Exception as exc:
            extra_unchanged = False
            extra_error = type(exc).__name__
        non_owned_unchanged = after == self.before and extra_unchanged
        clean = non_owned_unchanged and not any(remaining.values())
        result = {
            "receipts": receipts,
            "remaining": remaining,
            "non_owned_unchanged": non_owned_unchanged,
            "network_extra_unchanged": extra_unchanged,
            "network_extra_read_error": extra_error,
            "clean": clean,
        }
        save(self.root / "cleanup.json", {"result": result, "after": after})
        if not clean:
            raise ValueError("CLEANUP_NOT_CLEAN")
        return result

    def record_start_failure(self, exc):
        def text(value):
            return (
                value.decode("utf-8", errors="replace")
                if isinstance(value, bytes)
                else value
            )

        report = {
            "at": datetime.now(UTC).isoformat(),
            "error_type": type(exc).__name__,
            "returncode": getattr(exc, "returncode", None),
            "stdout": text(getattr(exc, "stdout", None)),
            "stderr": text(getattr(exc, "stderr", None)),
            "states": {},
        }
        # This private diagnostic is never a model input. Preserve the command
        # error even if runtime state can no longer be read safely.
        try:
            self.fresh()
            rows = json.loads(
                subprocess.run(
                    ["docker", "container", "inspect", *self.containers.values()],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout
            )
            for row in rows:
                cid = row["Id"]
                birth = self.births["container"].get(cid)
                if (
                    birth is None
                    or row["Created"] != birth["Created"]
                    or any(
                        row["Config"]["Labels"].get(k) != v
                        for k, v in self.labels.items()
                    )
                ):
                    raise ValueError("DIAGNOSTIC_OWNERSHIP_UNKNOWN")
                report["states"][cid] = row["State"]
        except Exception as diagnostic_error:
            report["state_read_error"] = type(diagnostic_error).__name__
        save(self.root / "start-command-failure.json", report)

    def start(self):
        self.verify()
        for port, _ in PORTS.values():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        self.create_aux("network", self.nonce + "-default")
        for name in self.plan["volumes"]:
            self.create_aux("volume", name)
        try:
            command(
                "docker",
                "compose",
                "-f",
                str(self.root / "compose.json"),
                "create",
                "--no-build",
                "--pull",
                "never",
            )
        finally:
            self.capture_created()
        self.verify()
        # Compose start honours the frozen dependencies; each resource is already birth-bound.
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.root / "compose.json"),
                    "start",
                    "--wait",
                    "--wait-timeout",
                    "240",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=270,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            self.record_start_failure(exc)
            raise
        self.verify()
        save(
            self.root / "started.json",
            {"at": datetime.now(UTC).isoformat(), "services": sorted(self.containers)},
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    prepare(parser.parse_args().campaign_root)
