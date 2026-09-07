"""Fresh qualification setup; no old campaign directory or execution authority."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any

from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre_live_sandbox.knowledge_v030 import (
    build_goal_flag_documents_v030,
    initialize_goal_flag_file_v030,
)
from ecomsre_live_sandbox.product_v030 import (
    ProductV030SandboxEnvironment,
    build_product_v030_runtime_bundle,
    full_mode_image_from_registry_v030,
)
from scripts.live_sandbox.product_v040 import PinnedDockerRunnerV040, IMAGE_PROOF_SHA256
from scripts.product.v040_runtime import ProductRuntimeV040, read_json, seal_private
from scripts.product.qualification_v040.capture import platform_images
from scripts.product.qualification_v040.guard import (
    QUAL_LABEL,
    QualificationBlocked,
    require,
)
from scripts.product.qualification_v040.plan import (
    PRODUCT_PROJECT,
    SANDBOX_PROJECT,
    transform_sandbox,
)


class QualificationRuntime(ProductRuntimeV040):
    def __init__(self, repository: Path, private: Path, qualification: str) -> None:
        super().__init__(repository)
        require(
            private.parent == self.repository / ".local/product-v040-qualifications",
            "INVALID_QUALIFICATION_ROOT",
        )
        require(
            not private.exists() and not private.is_symlink(),
            "QUALIFICATION_ROOT_EXISTS",
        )
        private.mkdir(parents=True, mode=0o700)
        self.private, self.qualification = private, qualification
        for directory in (
            "host",
            "errors",
            "product",
            "product/pilot",
            "proxy",
            "sandbox",
            "sandbox/control",
            "sandbox/runtime",
            "sandbox/runtime/flagd",
            "ledger",
        ):
            (private / directory).mkdir(mode=0o700, exist_ok=True)
        self.env = {
            "ECOMSRE_V040_ROOT": str(private),
            "ECOMSRE_V040_USER": f"{os.getuid()}:{os.getgid()}",
            "ECOMSRE_V040_IMAGE": "ecomsre-product-v040:a0e8aab077bd",
            "ECOMSRE_PRODUCT_API_PORT": "18001",
            "ECOMSRE_ADMIN_TOKEN": secrets.token_urlsafe(32),
            "ECOMSRE_REMEDIATION_API_BINDING_PATH": "",
        }
        seal_private(
            private / "host/qualification-identity.json",
            {
                "qualification_id": qualification,
                "formal_authority": False,
                "source_head": self.command(("git", "rev-parse", "HEAD")).strip(),
                "source_tree": self.command(
                    ("git", "rev-parse", "HEAD^{tree}")
                ).strip(),
            },
        )
        seal_private(
            private / "proxy/observation-proxy.json",
            {
                "prometheus_base_url": "http://host.docker.internal:19090",
                "jaeger_base_url": "http://host.docker.internal:11686",
                "opensearch_base_url": "http://host.docker.internal:19200",
            },
        )

    def boundary(self) -> dict[str, str]:
        endpoint = json.loads(
            self.docker(
                "context",
                "inspect",
                "desktop-linux",
                "--format",
                "{{json .Endpoints.docker.Host}}",
            )
        )
        info = json.loads(self.docker("info", "--format", "{{json .}}"))
        require(
            isinstance(endpoint, str)
            and endpoint.startswith("unix:///")
            and info.get("OSType") == "linux"
            and info.get("Architecture") in {"arm64", "aarch64"},
            "DAEMON_PLATFORM_DRIFT",
        )
        value = {
            "context": "desktop-linux",
            "endpoint": endpoint,
            "daemon_id": info["ID"],
            "os": info["OSType"],
            "architecture": info["Architecture"],
            "server_version": info["ServerVersion"],
            "kernel_version": info["KernelVersion"],
            "daemon_name": info["Name"],
            "docker_root_dir": info["DockerRootDir"],
        }
        path = self.private / "host/daemon.json"
        if path.exists():
            require(read_json(path) == value, "DAEMON_IDENTITY_DRIFT")
        return value

    def stabilize(self) -> dict[str, str]:
        samples = []
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                value = self.boundary()
                samples.append({"monotonic_ns": time.monotonic_ns(), "binding": value})
                if len(samples) >= 3 and all(
                    s.get("binding") == value for s in samples[-3:]
                ):
                    seal_private(
                        self.private / "host/daemon-stabilization.json", samples
                    )
                    seal_private(self.private / "host/daemon.json", value)
                    return value
            except Exception as error:
                samples.append(
                    {
                        "monotonic_ns": time.monotonic_ns(),
                        "error_type": type(error).__name__,
                    }
                )
            time.sleep(2)
        seal_private(self.private / "host/daemon-stabilization.json", samples)
        raise QualificationBlocked("DAEMON_NOT_STABLE")

    def initialize(self) -> None:
        raise QualificationBlocked("FORMAL_AUTHORITY_FORBIDDEN")

    def enable(self) -> None:
        raise QualificationBlocked("FORMAL_AUTHORITY_FORBIDDEN")

    def build(self) -> dict[str, Any]:
        raise QualificationBlocked("BUILD_NOT_AUTHORIZED")

    def start_bootstrap(self) -> None:
        raise QualificationBlocked("QUALIFICATION_STAGE_AUTHORITY_REQUIRED")

    def cleanup(self) -> dict[str, list[str]]:
        raise QualificationBlocked("QUALIFICATION_CLEANUP_AUTHORITY_REQUIRED")

    def compose_plan(
        self, project: str, path: Path, *arguments: str, timeout: int = 60
    ) -> str:
        require(project in {PRODUCT_PROJECT, SANDBOX_PROJECT}, "UNKNOWN_PROJECT")
        return self.command(
            (
                "docker",
                "--context",
                "desktop-linux",
                "compose",
                "--project-name",
                project,
                "-f",
                str(path),
                *arguments,
            ),
            timeout=timeout,
            compose=True,
        )


class BaselineReader:
    def __init__(
        self, endpoints: Any, flag_file: Path, document: dict[str, Any]
    ) -> None:
        self.endpoints, self.flag_file, self.document = endpoints, flag_file, document

    def read(self, state: str) -> dict[str, Any]:
        from datetime import UTC, datetime
        from ecomsre_live_sandbox.knowledge_v030 import _local_json
        from ecomsre_live_sandbox.contracts import canonical_sha256

        require(state == "BASELINE", "FORMAL_AUTHORITY_FORBIDDEN")
        require(read_json(self.flag_file) == self.document, "HEALTHY_FLAG_DRIFT")
        ui = _local_json(self.endpoints.flag_control + "/read")
        require(
            isinstance(ui, dict) and ui.get("flags") == self.document["flags"],
            "HEALTHY_FLAG_DRIFT",
        )
        evaluations = {}
        for key in ("kafkaQueueProblems", "paymentFailure", "loadGeneratorTraffic"):
            row = _local_json(
                self.endpoints.flag_evaluation + "/ofrep/v1/evaluate/flags/" + key,
                method="POST",
                payload={},
            )
            require(
                isinstance(row, dict)
                and row.get("variant") == "off"
                and row.get("value") == 0,
                "HEALTHY_FLAG_DRIFT",
            )
            assert isinstance(row, dict)
            evaluations[key] = {"variant": row["variant"], "value": row["value"]}
        return {
            "state": "BASELINE",
            "document_sha256": canonical_sha256(self.document),
            "observed_at": datetime.now(UTC).isoformat(),
            "evaluations": evaluations,
        }


class QualifiedEnvironment(ProductV030SandboxEnvironment):
    def bind_qualification(
        self,
        runtime: QualificationRuntime,
        resolved: Any,
        raw: dict[str, Any],
        path: Path,
    ) -> None:
        self.qual_runtime, self.qual_resolved, self.qual_raw, self.qual_path = (
            runtime,
            resolved,
            raw,
            path,
        )

    def resolve(self) -> tuple[Any, dict[str, Any]]:
        if not hasattr(self, "qual_path"):
            return super().resolve()
        require(read_json(self.qual_path) == self.qual_raw, "COMPOSE_INPUT_DRIFT")
        self.qual_runtime.boundary()
        return self.qual_resolved, deepcopy(self.qual_raw)

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        counts = super().verify_owned_resources(require_complete=False)
        if hasattr(self, "qual_runtime"):
            for kind in ("container", "network", "volume"):
                ids = self._owned_ids(kind)
                if ids:
                    rows = json.loads(
                        self.qual_runtime.docker(kind, "inspect", *sorted(ids))
                    )
                    for row in rows:
                        labels = (
                            row["Config"].get("Labels")
                            if kind == "container"
                            else row.get("Labels")
                        ) or {}
                        require(
                            labels.get(QUAL_LABEL) == self.qual_runtime.qualification,
                            "OWNERSHIP_LABEL_DRIFT",
                        )
            if require_complete:
                require(
                    counts == {"container": 28, "network": 1, "volume": 6},
                    "INCOMPLETE_OWNED_INVENTORY",
                )
        return counts

    def start(self) -> None:
        raise QualificationBlocked("QUALIFICATION_STAGE_AUTHORITY_REQUIRED")

    def cleanup(self, **kwargs: Any) -> Any:
        raise QualificationBlocked("QUALIFICATION_CLEANUP_AUTHORITY_REQUIRED")


def prepare_sandbox(
    runtime: QualificationRuntime, historical: Path
) -> tuple[QualifiedEnvironment, Any, dict[str, Any], dict[str, Any]]:
    root = runtime.repository
    bundle, documents = build_goal_flag_documents_v030(
        read_json(root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"),
        build_product_v030_runtime_bundle(root),
    )
    flag_directory = runtime.private / "sandbox/runtime/flagd"
    initialize_goal_flag_file_v030(
        flag_directory / "demo.flagd.json", documents["BASELINE"]
    )
    proof = (historical / "host/image-proofs-original.json").read_bytes()
    require(
        hashlib.sha256(proof).hexdigest() == IMAGE_PROOF_SHA256,
        "HISTORICAL_IMAGE_PROOF_DRIFT",
    )
    identities = json.loads(proof)
    images = tuple(
        full_mode_image_from_registry_v030(
            reference=p["reference"],
            descriptor=p["descriptor"],
            platform_manifest_raw=p["platform_manifest_raw"],
            cached=p["cached"],
        )
        for p in identities["registry_proofs"]
    )
    require(
        [i.model_dump(mode="json") for i in images] == identities["images"],
        "HISTORICAL_IMAGE_PROOF_DRIFT",
    )
    environment = QualifiedEnvironment(
        repository_root=root,
        bundle=bundle,
        flagd_directory=flag_directory,
        full_mode_images=images,
        runner=PinnedDockerRunnerV040(),
    )
    environment.verify_local_docker()
    environment.verify_upstream()
    resolved, raw = environment.resolve()
    inspection = environment.inspect_cached_images(resolved)
    require(
        not any(environment.verify_owned_resources(require_complete=False).values()),
        "PREEXISTING_SANDBOX_RESOURCES",
    )
    environment.verify_ports_available()
    seal_private(
        runtime.private / "host/sandbox-preflight.json",
        {
            "base_resolved": raw,
            "inspection": inspection.model_dump(mode="json"),
            "baseline_document_sha256": hashlib.sha256(
                (flag_directory / "demo.flagd.json").read_bytes()
            ).hexdigest(),
        },
    )
    return (
        environment,
        resolved,
        transform_sandbox(raw, runtime.qualification),
        documents["BASELINE"],
    )


def prepare_product(runtime: QualificationRuntime, historical: Path) -> dict[str, Any]:
    require(not any(runtime.owned().values()), "PREEXISTING_PRODUCT_RESOURCES")
    expected = read_json(historical / "host/build-inputs.json")
    current = {}
    entries = runtime.command(
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
    for entry in filter(None, entries):
        metadata, path = entry.split("\t", 1)
        mode, _, stage = metadata.split()
        require(
            stage == "0" and mode in {"100644", "100755"}, "IMAGE_BUILD_INPUT_DRIFT"
        )
        current[path] = {
            "git_mode": mode,
            "sha256": hashlib.sha256(
                (runtime.repository / path).read_bytes()
            ).hexdigest(),
        }
    require(current == expected, "IMAGE_BUILD_INPUT_DRIFT")
    build = read_json(historical / "host/product-build.json")
    runtime.env["ECOMSRE_V040_IMAGE"] = build["image_tag"]
    images = platform_images(runtime, [build["image_tag"]])
    require(
        images[build["image_tag"]]["Id"] == build["image_id"], "PRODUCT_IMAGE_DRIFT"
    )
    seal_private(
        runtime.private / "host/reused-application-image.json",
        {
            "historical_build": build,
            "fresh_image": images,
            "fresh_build_inputs_sha256": semantic_sha256(current),
            "all_build_inputs_byte_identical": True,
            "image_builds": 0,
        },
    )
    raw = json.loads(
        runtime.compose("--profile", "remediation", "config", "--format", "json")
    )
    raw["services"] = {
        name: raw["services"][name]
        for name in ("api", "worker", "remediation-observer")
    }
    raw.pop("volumes", None)
    for name, service in raw["services"].items():
        service.pop("build", None)
        service.pop("profiles", None)
        service["container_name"] = "ecomsre-v040-" + runtime.qualification + "-" + name
        service.setdefault("labels", {})[QUAL_LABEL] = runtime.qualification
        if name in {"api", "worker"}:
            service["environment"]["ECOMSRE_REMEDIATION_ENABLED"] = "0"
            service["environment"]["ECOMSRE_REMEDIATION_BINDING_PATH"] = ""
            for key in list(service["environment"]):
                if key.startswith("ECOMSRE_REMEDIATION_") and key.endswith("_TOKEN"):
                    service["environment"][key] = ""
        service["volumes"] = [
            m
            for m in service.get("volumes", [])
            if m["target"] not in {"/run/remediation-read", "/run/remediation-config"}
        ]
    for network in raw["networks"].values():
        network.setdefault("labels", {})[QUAL_LABEL] = runtime.qualification
    seal_private(
        runtime.private / "host/runtime.json",
        {"environment": runtime.env, "formal_authority": False},
    )
    return raw
