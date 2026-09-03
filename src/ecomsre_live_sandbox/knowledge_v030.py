"""Private evaluator lifecycle for the user's Product v0.3 Goal, never an Agent tool."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping

import httpx

from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre.dta_v2.read_only_smoke import _SandboxOwnedSmokeLifecycle
from ecomsre_live_sandbox.contracts import (
    ConfigBundle,
    LocalEndpoints,
    canonical_sha256,
    ensure_private_directory,
    write_private_json,
)
from ecomsre_live_sandbox.control import (
    _local_json,
    _restore_private_flag_mode,
    build_flag_documents,
)
from ecomsre_live_sandbox.product_v030 import (
    ProductV030SandboxEnvironment,
    build_product_v030_runtime_bundle,
    full_mode_image_from_registry_v030,
)


def observe_queue_lag_v030(client: httpx.Client) -> dict[str, Any]:
    """Bind value and source timestamp to one evaluation instant; reject staleness."""
    selector = 'kafka_consumer_group_lag_ratio{group="fraud-detection",topic="orders"}'
    evaluated_at = datetime.now(UTC)
    vectors = []
    for expression in (selector, f"timestamp({selector})"):
        response = client.get(
            "http://127.0.0.1:19090/api/v1/query",
            params={"query": expression, "time": evaluated_at.timestamp()},
        )
        response.raise_for_status()
        payload = response.json()
        if (
            payload.get("status") != "success"
            or payload["data"]["resultType"] != "vector"
        ):
            raise RuntimeError("queue query is not a successful vector")
        vectors.append(payload["data"]["result"])
    values, stamps = vectors
    if (
        len(values) != 1
        or len(stamps) != 1
        or values[0]["metric"].get("partition") != "0"
        or {k: v for k, v in values[0]["metric"].items() if k != "__name__"}
        != {k: v for k, v in stamps[0]["metric"].items() if k != "__name__"}
    ):
        raise RuntimeError("queue series identity changed")
    value = float(values[0]["value"][1])
    source_time = float(stamps[0]["value"][1])
    if (
        not math.isfinite(value)
        or value < 0
        or not math.isfinite(source_time)
        or not 0 <= evaluated_at.timestamp() - source_time <= 30
    ):
        raise RuntimeError("queue sample is invalid or stale")
    return {
        "observed_at": evaluated_at.isoformat(),
        "lag": value,
        "source_timestamp": source_time,
        "series": values[0],
    }


def consumer_membership_healthy_v030(
    *,
    state_output: str,
    members_output: str,
    container_ip: str,
) -> bool:
    """Coordinator membership is liveness, not proof of zero lag or progress."""
    states = [
        line.split()
        for line in state_output.splitlines()
        if line.startswith("fraud-detection ")
    ]
    members = [
        line.split()
        for line in members_output.splitlines()
        if line.startswith("fraud-detection ")
    ]
    return (
        len(states) == len(members) == 1
        and len(states[0]) == 6
        and states[0][-2:] == ["Stable", "1"]
        and len(members[0]) == 9
        and members[0][2] == f"/{container_ip}"
        and members[0][3].startswith("consumer-fraud-detection-")
        and members[0][4] == "1"
        and members[0][6] == "orders:0"
    )


def owned_runtime_observation_v030(
    environment: ProductV030SandboxEnvironment,
    *,
    candidates: tuple[str, ...],
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    """Fresh Docker state plus an independent, read-only consumer liveness probe."""
    if not candidates or not set(candidates).issubset(
        {"checkout", "fraud-detection", "kafka", "payment"}
    ):
        raise ValueError("Goal Runtime candidate scope differs")
    environment.verify_owned_resources(require_complete=True)
    identifiers = environment._owned_ids("container")
    inspected = json.loads(
        environment.runner.run(
            ("docker", "inspect", *sorted(identifiers)),
            cwd=environment.repository_root,
        ).stdout
    )
    by_service = {
        item["Config"]["Labels"]["com.docker.compose.service"]: item
        for item in inspected
    }
    states: dict[str, dict[str, object]] = {}
    proof: dict[str, object] = {
        "observed_at": datetime.now(UTC).isoformat(),
        "health_basis": {},
        "containers": {},
    }
    bases: dict[str, str] = {}
    for name in candidates:
        item = by_service[name]
        state = item["State"]
        running = state.get("Running") is True and not any(
            state.get(key) for key in ("Paused", "Restarting", "Dead", "OOMKilled")
        )
        native_health = state.get("Health")
        healthy = (
            running
            and isinstance(native_health, dict)
            and native_health.get("Status") == "healthy"
        )
        bases[name] = "DOCKER_CONFIGURED_HEALTHCHECK"
        if name == "fraud-detection" and "Health" not in state:
            ip = item["NetworkSettings"]["Networks"]["ecomsre-live-sandbox-v1-default"][
                "IPAddress"
            ]
            outputs = {}
            for option in ("state", "members"):
                result = environment.runner.run(
                    (
                        "docker",
                        "exec",
                        "--env",
                        "KAFKA_HEAP_OPTS=-Xms32m -Xmx128m",
                        "--env",
                        "KAFKA_OPTS=",
                        "--env",
                        "JAVA_TOOL_OPTIONS=",
                        "--env",
                        "_JAVA_OPTIONS=",
                        by_service["kafka"]["Id"],
                        "/opt/kafka/bin/kafka-consumer-groups.sh",
                        "--bootstrap-server",
                        "kafka:9092",
                        "--timeout",
                        "5000",
                        "--describe",
                        "--group",
                        "fraud-detection",
                        f"--{option}",
                        *(("--verbose",) if option == "members" else ()),
                    ),
                    cwd=environment.repository_root,
                    timeout_seconds=15,
                )
                outputs[option] = result.stdout
            healthy = running and consumer_membership_healthy_v030(
                state_output=outputs["state"],
                members_output=outputs["members"],
                container_ip=ip,
            )
            bases[name] = "COORDINATOR_MEMBERSHIP"
            proof["consumer_probe"] = {
                "container_id": item["Id"],
                "container_ip": ip,
                "raw_docker_health": state.get("Health"),
                "outputs": outputs,
                "healthy": healthy,
                "interpretation": "ACTIVE_MEMBER_LIVENESS_NOT_PROCESSING_PROGRESS_OR_LOW_LAG",
            }
        states[name] = {
            "state": "RUNNING" if running else "OTHER",
            "healthy": healthy,
            "restart_count": item["RestartCount"],
        }
    proof["health_basis"] = bases
    proof["containers"] = {name: by_service[name] for name in candidates}
    proof["services"] = states
    return states, proof


def build_goal_flag_documents_v030(
    upstream: Mapping[str, object],
    bundle: ConfigBundle,
) -> tuple[ConfigBundle, dict[str, dict[str, Any]]]:
    # First verify the immutable upstream against the historical construction.
    original_baseline, original_payment = build_flag_documents(upstream, bundle)
    baseline: dict[str, Any] = deepcopy(original_baseline)
    payment: dict[str, Any] = deepcopy(original_payment)
    for document in (baseline, payment):
        document["flags"]["loadGeneratorTraffic"]["defaultVariant"] = "off"
    queue = deepcopy(baseline)
    queue["flags"]["kafkaQueueProblems"]["defaultVariant"] = "on"
    payload = bundle.model_dump(mode="json")
    payload["scenario"]["baseline_document_sha256"] = canonical_sha256(baseline)
    payload["scenario"]["fault_document_sha256"] = canonical_sha256(payment)
    return ConfigBundle.model_validate(payload), {
        "BASELINE": baseline,
        "QUEUE": queue,
        "PAYMENT": payment,
    }


class GoalFlagControllerV030:
    """Three fixed evaluator documents with file/UI/OFREP agreement."""

    def __init__(
        self,
        *,
        endpoints: LocalEndpoints,
        flag_file: Path,
        documents: Mapping[str, Mapping[str, object]],
    ) -> None:
        if set(documents) != {"BASELINE", "QUEUE", "PAYMENT"}:
            raise ValueError("Goal flag states differ")
        self.endpoints = endpoints
        self.flag_file = flag_file
        self.documents = deepcopy(dict(documents))
        self.hashes = {key: canonical_sha256(value) for key, value in documents.items()}

    def read(self, state: str) -> dict[str, object]:
        expected = self.documents[state]
        if self.flag_file.is_symlink() or not self.flag_file.is_file():
            raise RuntimeError("private evaluator flag file is unavailable")
        document = json.loads(self.flag_file.read_text())
        if canonical_sha256(document) != self.hashes[state]:
            raise RuntimeError("private evaluator flag document differs")
        readback = _local_json(f"{self.endpoints.flag_control}/read")
        if not isinstance(readback, dict) or readback.get("flags") != expected["flags"]:
            raise RuntimeError("flag UI readback differs")
        evaluations = {}
        for key, variant, value in (
            (
                "kafkaQueueProblems",
                "on" if state == "QUEUE" else "off",
                100 if state == "QUEUE" else 0,
            ),
            (
                "paymentFailure",
                "100%" if state == "PAYMENT" else "off",
                1 if state == "PAYMENT" else 0,
            ),
            ("loadGeneratorTraffic", "off", 0),
        ):
            observed = _local_json(
                f"{self.endpoints.flag_evaluation}/ofrep/v1/evaluate/flags/{key}",
                method="POST",
                payload={},
            )
            if (
                not isinstance(observed, dict)
                or observed.get("variant") != variant
                or observed.get("value") != value
            ):
                raise RuntimeError("flag evaluation differs from the frozen Goal state")
            evaluations[key] = {"variant": variant, "value": value}
        return {
            "state": state,
            "document_sha256": self.hashes[state],
            "observed_at": datetime.now(UTC).isoformat(),
            "evaluations": evaluations,
        }

    def apply(self, state: str) -> dict[str, object]:
        document = self.documents[state]
        if canonical_sha256(document) != self.hashes[state]:
            raise RuntimeError("Goal controller document changed")
        _local_json(
            f"{self.endpoints.flag_control}/write",
            method="POST",
            payload={"data": document},
        )
        _restore_private_flag_mode(self.flag_file)
        deadline = time.monotonic() + 15
        while True:
            try:
                return self.read(state)
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)


def initialize_goal_flag_file_v030(path: Path, baseline: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Goal baseline flag file is not regular")
        if canonical_sha256(json.loads(path.read_text())) != canonical_sha256(baseline):
            raise ValueError("existing Goal flag document is not the baseline")
        # The flag UI uses its own JSON formatting; preserve the observed bytes.
        _restore_private_flag_mode(path)
    else:
        write_private_json(path, baseline, create_once=True)


class ProductV030Lifecycle(_SandboxOwnedSmokeLifecycle):
    def __init__(
        self, *, repository_root: Path, private_root: Path, image_identities: Path
    ) -> None:
        super().__init__(
            repository_root=repository_root,
            private_root=private_root,
            stabilization_seconds=0,
        )
        self.image_identities = image_identities
        self.goal_documents: dict[str, dict[str, Any]] = {}
        self.goal_controller: GoalFlagControllerV030 | None = None

    def admit(self) -> None:
        root = self.repository_root
        control_root = self.private_root / "control"
        flag_directory = self.private_root / "runtime/flagd"
        for directory in (control_root, flag_directory):
            ensure_private_directory(directory)
        upstream = json.loads(
            (
                root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
            ).read_text()
        )
        self.bundle, self.goal_documents = build_goal_flag_documents_v030(
            upstream, build_product_v030_runtime_bundle(root)
        )
        self.baseline_document = self.goal_documents["BASELINE"]
        self.fault_document = self.goal_documents["PAYMENT"]
        self.flag_file = flag_directory / "demo.flagd.json"
        initialize_goal_flag_file_v030(self.flag_file, self.baseline_document)
        identities = json.loads(self.image_identities.read_text())
        images = tuple(
            full_mode_image_from_registry_v030(
                reference=proof["reference"],
                descriptor=proof["descriptor"],
                platform_manifest_raw=proof["platform_manifest_raw"],
                cached=proof["cached"],
            )
            for proof in identities["registry_proofs"]
        )
        if [image.model_dump(mode="json") for image in images] != identities["images"]:
            raise RuntimeError("acquired image proofs differ from captured identities")
        self.environment = ProductV030SandboxEnvironment(
            repository_root=root,
            bundle=self.bundle,
            flagd_directory=flag_directory,
            full_mode_images=images,
        )
        self.environment.verify_local_docker()
        self.environment.verify_upstream()
        resolved, raw_compose = self.environment.resolve()
        self.admitted_resolved_sha256 = semantic_sha256(
            resolved.model_dump(mode="json")
        )
        write_private_json(
            control_root / "resolved-compose.json", raw_compose, create_once=True
        )
        self.environment.verify_cached_images(resolved, control_root)
        self.environment.verify_owned_resources(require_complete=False)
        self.goal_controller = GoalFlagControllerV030(
            endpoints=resolved.endpoints,
            flag_file=self.flag_file,
            documents=self.goal_documents,
        )
