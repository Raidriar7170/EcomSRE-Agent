"""Goal-bound reuse of the frozen owned sandbox without historical writes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping
from typing import Any

from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre_live_sandbox.environment import ExactCommandRunner, CommandResult
from ecomsre_live_sandbox.contracts import ensure_private_directory, write_private_json
from ecomsre_live_sandbox.knowledge_v030 import (
    GoalFlagControllerV030,
    ProductV030Lifecycle,
    build_goal_flag_documents_v030,
    initialize_goal_flag_file_v030,
)
from ecomsre_live_sandbox.product_v030 import (
    ProductV030SandboxEnvironment,
    build_product_v030_runtime_bundle,
    full_mode_image_from_registry_v030,
)

GOAL_SHA256 = "d8ec6455a6108f40d67eb8441f18e952670b087255c0fb15fc14ccb87e32695a"
IMAGE_PROOF_SHA256 = "3cc9e3d2710d85348376bf8aa26ef7bac0bbe4291055110f1b3e2b9cab6e731c"


class PinnedDockerRunnerV040(ExactCommandRunner):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300,
    ) -> CommandResult:
        selected = dict(os.environ if env is None else env)
        for key in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
        ):
            selected.pop(key, None)
        if arguments and arguments[0] == "docker":
            arguments = ("docker", "--context", "desktop-linux", *arguments[1:])
        return super().run(
            arguments, cwd=cwd, env=selected, timeout_seconds=timeout_seconds
        )


class ProductV040Lifecycle(ProductV030Lifecycle):
    """Imports frozen mechanics; emits only new v0.4 private authority evidence."""

    def admit(self) -> None:
        root = self.repository_root
        control_root = self.private_root / "control"
        flag_directory = self.private_root / "runtime/flagd"
        for directory in (control_root, flag_directory):
            ensure_private_directory(directory)
        upstream = json.loads(
            (
                root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
            ).read_bytes()
        )
        self.bundle, self.goal_documents = build_goal_flag_documents_v030(
            upstream, build_product_v030_runtime_bundle(root)
        )
        self.baseline_document = self.goal_documents["BASELINE"]
        self.fault_document = self.goal_documents["PAYMENT"]
        self.flag_file = flag_directory / "demo.flagd.json"
        # Resume is read-only: a formal fault document must never be overwritten
        # or rejected before fresh owned cleanup authority can be reconstructed.
        if not self.flag_file.exists():
            if not (control_root / "admission.json").exists():
                initialize_goal_flag_file_v030(self.flag_file, self.baseline_document)
        elif self.flag_file.is_symlink() or not self.flag_file.is_file():
            raise ValueError("private runtime flag file is not regular")
        proof_bytes = self.image_identities.read_bytes()
        if hashlib.sha256(proof_bytes).hexdigest() != IMAGE_PROOF_SHA256:
            raise ValueError("frozen full-mode image proofs differ")
        identities = json.loads(proof_bytes)
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
            raise ValueError("image proof identity binding differs")
        self.environment = ProductV030SandboxEnvironment(
            repository_root=root,
            bundle=self.bundle,
            flagd_directory=flag_directory,
            full_mode_images=images,
            runner=PinnedDockerRunnerV040(),
        )
        docker = self.environment.verify_local_docker()
        self.environment.verify_upstream()
        resolved, raw_compose = self.environment.resolve()
        self.admitted_resolved_sha256 = semantic_sha256(
            resolved.model_dump(mode="json")
        )
        inspection = self.environment.inspect_cached_images(resolved)
        admitted: dict[str, Any] = {
            "schema_version": "ecomsre.product.v040.private-runtime-admission.v1",
            "authorization": "USER_EXPLICIT_PRODUCT_V040_GOAL_AUTHORIZATION",
            "goal_sha256": GOAL_SHA256,
            "docker": docker,
            "resolved": resolved.model_dump(mode="json"),
            "resolved_compose": raw_compose,
            "historical_lock_sha256": inspection.historical_image_lock_sha256,
            "upstream_commit": inspection.upstream_commit,
            "platform": "linux/arm64",
            "image_proof_sha256": IMAGE_PROOF_SHA256,
            "images": [image.model_dump(mode="json") for image in inspection.images],
        }
        admission_path = control_root / "admission.json"
        if admission_path.exists():
            if json.loads(admission_path.read_bytes()) != admitted:
                raise ValueError("live runtime admission drifted")
        else:
            write_private_json(admission_path, admitted, create_once=True)
        self.environment.verify_owned_resources(require_complete=False)
        self.goal_controller = GoalFlagControllerV030(
            endpoints=resolved.endpoints,
            flag_file=self.flag_file,
            documents=self.goal_documents,
        )
