"""Minimal dependency closure derived from pinned Payment's direct gRPC path."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .owned import command, save

REPO = Path(__file__).resolve().parents[3]
UPSTREAM = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
REFERENCES = {
    "payment": "ghcr.io/open-telemetry/demo:3.0.0-payment",
    "flagd": "ghcr.io/open-feature/flagd:v0.16.0",
    "otel-collector": "ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib:0.157.0",
    "prometheus": "quay.io/prometheus/prometheus:v3.13.1",
}
REASONS = {
    "payment": "Pinned real gRPC Charge implementation",
    "flagd": "Pinned real feature-flag evaluation",
    "otel-collector": "Receive real Payment OTLP traces, metrics and logs",
    "payment-probe": "Fixed synthetic direct Charge caller and measured business metrics",
    "prometheus": "Historical real business and owned Payment resource metrics",
    "payment-control": "Fixed two-document file transport required by current gateway",
    "api": "Current Product API and persistent repositories",
    "worker": "Current Product Connector, Baseline and Diagnosis jobs",
    "remediation-control-gateway": "Current guarded single-restore control with signed state evidence",
    "remediation-executor": "Current independent typed Executor, StepReceipt and Recovery Verifier",
}


def bind(
    source: Path | str, target: str, readonly: bool = True, kind: str = "bind"
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": kind,
        "source": str(source),
        "target": target,
        "read_only": readonly,
    }
    if kind == "volume":
        value["volume"] = {"nocopy": True}
    return value


def pinned_images(root: Path, product: str) -> dict[str, Any]:
    result = {}
    for name, ref in {**REFERENCES, "product": product}.items():
        row = json.loads(
            command("docker", "image", "inspect", "--platform", "linux/arm64", ref)
        )[0]
        if row["Architecture"] != "arm64" or row["Os"] != "linux":
            raise ValueError("IMAGE_PLATFORM_MISMATCH")
        if name != "product" and not row["RepoDigests"]:
            raise ValueError("IMAGE_DIGEST_UNAVAILABLE")
        index = json.loads(command("docker", "image", "inspect", ref))[0]
        row["index_id"] = index["Id"]
        row["runtime_reference"] = row["RepoDigests"][0] if name != "product" else ref
        result[name] = row
    save(root / "images.json", result)
    return result


def build_plan(
    root: Path,
    nonce: str,
    images: dict[str, Any],
    ports: dict[str, int],
    labels: dict[str, str],
    secrets: dict[str, str],
) -> dict[str, Any]:
    networks = {n: nonce + "-" + n for n in ("business", "control", "observation")}
    volumes = {n: nonce + "-" + n for n in ("read-socket", "write-socket")}
    image_ref = {
        n: row.get("runtime_reference", row["Id"]) for n, row in images.items()
    }

    def service(
        image: str,
        cmd: list[str] | None,
        nets: list[str],
        mounts: list[dict[str, Any]],
        port: tuple[str, int] | None = None,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "image": image_ref[image],
            "pull_policy": "never",
            "platform": "linux/arm64",
            "container_name": nonce + "-" + (port[0] if port else image),
            "labels": labels,
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "restart": "no",
            "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=64m"],
            "volumes": mounts,
            "networks": nets,
            "mem_limit": "512m",
        }
        if cmd is not None:
            value["command"] = cmd
        if port:
            value["ports"] = [
                {
                    "target": port[1],
                    "published": str(ports[port[0]]),
                    "host_ip": "127.0.0.1",
                    "protocol": "tcp",
                }
            ]
        return value

    flags = root / "control/flags"
    services = {
        "payment": service("payment", None, ["business"], []),
        "flagd": service(
            "flagd",
            ["start", "--uri", "file:/etc/flagd/demo.flagd.json"],
            ["business", "control"],
            [bind(flags, "/etc/flagd")],
            ("flagd", 8016),
        ),
        "otel-collector": service(
            "otel-collector",
            ["--config=/etc/minimal.json"],
            ["business"],
            [
                bind(
                    REPO / "config/product-v040/minimal-payment/collector.json",
                    "/etc/minimal.json",
                )
            ],
        ),
        "payment-probe": service(
            "payment",
            ["/probe.cjs"],
            ["business"],
            [
                bind(
                    REPO / "scripts/product/minimal_payment_acceptance_v040/probe.cjs",
                    "/probe.cjs",
                )
            ],
            ("probe", 8080),
        ),
        "prometheus": service(
            "prometheus",
            [
                "--config.file=/etc/prometheus/minimal.json",
                "--storage.tsdb.path=/prometheus",
                "--storage.tsdb.retention.time=2h",
            ],
            ["business", "observation"],
            [bind(root / "prometheus.json", "/etc/prometheus/minimal.json")],
            ("prometheus", 9090),
        ),
        "payment-control": service(
            "product",
            ["python", "/harness/flag_transport.py"],
            ["control"],
            [
                bind(root / "control", "/control", False),
                bind(
                    REPO / "scripts/product/minimal_payment_acceptance_v040", "/harness"
                ),
            ],
            ("control", 8080),
        ),
        "api": service(
            "product",
            ["python", "-m", "ecomsre.product.app"],
            ["observation"],
            [bind(root / "data", "/var/lib/ecomsre", False)],
            ("api", 8080),
        ),
        "worker": service(
            "product",
            ["python", "-m", "ecomsre.product.jobs.worker"],
            ["observation"],
            [bind(root / "data", "/var/lib/ecomsre", False)],
        ),
    }
    services["payment"]["environment"] = {
        "PAYMENT_PORT": "50051",
        "FLAGD_HOST": "flagd",
        "FLAGD_PORT": "8013",
        "NODE_OPTIONS": "--require @opentelemetry/auto-instrumentations-node/register",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
        "OTEL_SERVICE_NAME": "payment",
        "OTEL_METRIC_EXPORT_INTERVAL": "1000",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=payment",
    }
    services["prometheus"]["tmpfs"].append("/prometheus:rw,nosuid,nodev,size=128m")
    services["payment-control"]["user"] = "0:0"
    services["api"]["environment"] = {
        "ECOMSRE_ADMIN_TOKEN": secrets["admin"],
        "ECOMSRE_PRODUCT_API_HOST": "0.0.0.0",
    }
    for role in ("api", "worker"):
        services[role].setdefault("environment", {})["ECOMSRE_PRODUCT_DATA_ROOT"] = (
            "/var/lib/ecomsre"
        )
    common = {
        "ECOMSRE_PRODUCT_DATA_ROOT": "/var/lib/ecomsre",
        "ECOMSRE_REMEDIATION_ENABLED": "1",
        "ECOMSRE_REMEDIATION_ISOLATED_PROFILE": "product-v040-network-v1",
        "ECOMSRE_REMEDIATION_BINDING_PATH": "/run/remediation-config/binding.json",
        "ECOMSRE_REMEDIATION_POLICY_PATH": "/run/remediation-config/recovery-policy.json",
        "ECOMSRE_REMEDIATION_READ_SOCKET": "/run/remediation-read/control.sock",
        "ECOMSRE_REMEDIATION_WRITE_SOCKET": "/run/remediation-write/control.sock",
        "ECOMSRE_REMEDIATION_READ_TOKEN": secrets["read"],
        "ECOMSRE_REMEDIATION_WRITE_TOKEN": secrets["write"],
    }
    shared = [
        bind(root / "data", "/var/lib/ecomsre", False),
        bind(root / "config", "/run/remediation-config"),
    ]
    for role, action in [
        ("remediation-control-gateway", "control-gateway"),
        ("remediation-executor", "executor"),
    ]:
        gateway = action == "control-gateway"
        mounts = shared + [
            bind(
                volumes["read-socket"], "/run/remediation-read", not gateway, "volume"
            ),
            bind(
                volumes["write-socket"], "/run/remediation-write", not gateway, "volume"
            ),
        ]
        services[role] = service(
            "product",
            ["python", "-m", "ecomsre.product.remediation.runtime", action],
            ["control", "observation"] if gateway else [],
            mounts,
            ("gateway", 8081) if gateway else None,
        )
        services[role]["environment"] = dict(common)
        # Fresh empty named socket volumes require the gateway to own their roots.
        # No capabilities, Docker socket or host namespace are granted.
        if gateway:
            services[role]["user"] = "0:0"
            services[role]["volumes"] += [
                bind(root / "private", "/run/remediation-private"),
                bind(root / "observer", "/run/remediation-observer"),
                bind(flags, "/runtime/payment-flags"),
                bind(root / "ledger", "/var/lib/remediation-control", False),
            ]
            services[role]["environment"].update(
                {
                    "ECOMSRE_REMEDIATION_PRIVATE_PROFILE": "/run/remediation-private/profile.json",
                    "ECOMSRE_REMEDIATION_CONTROL_LEDGER": "/var/lib/remediation-control/dispatch.sqlite3",
                    "ECOMSRE_REMEDIATION_RECOVERY_WITNESS": "/run/remediation-observer/recovery.json",
                    "ECOMSRE_REMEDIATION_OBSERVER_TOKEN": secrets["observer"],
                    "ECOMSRE_REMEDIATION_READ_PROXY_PORT": "8081",
                }
            )
        else:
            del services[role]["networks"]
            services[role]["network_mode"] = "none"
    for role, spec in services.items():
        spec["container_name"] = nonce + "-" + role
    # Prometheus reads the fixed observer endpoint, never the control document.
    save(
        root / "prometheus.json",
        {
            "global": {"scrape_interval": "1s"},
            "scrape_configs": [
                {
                    "job_name": "payment-business",
                    "static_configs": [{"targets": ["payment-probe:8080"]}],
                },
                {
                    "job_name": "payment-resource",
                    "static_configs": [
                        {"targets": [f"host.docker.internal:{ports['observer']}"]}
                    ],
                },
            ],
        },
    )
    return {
        "name": nonce,
        "services": services,
        "networks": {k: {"external": True, "name": v} for k, v in networks.items()},
        "volumes": {v: {"external": True, "name": v} for v in volumes.values()},
    }
