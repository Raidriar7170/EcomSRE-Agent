from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from pydantic import ValidationError
import pytest

from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre.product.pilot import runtime_continuity_v0231 as continuity_v0231
from ecomsre.product.pilot.runtime_authority_v02 import PilotRuntimeAuthorityV02
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    FlagdBindDescriptorV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
    admit_flagd_bind_descriptor_v0231,
    build_runtime_authority_continuity_descriptor_v0231,
)
from ecomsre_live_sandbox.contracts import (
    CleanupResult,
    ConfigBundle,
    LocalEndpoints,
    ResolvedSandbox,
    canonical_json_bytes,
    load_bundle,
)
from ecomsre_live_sandbox.control import build_flag_documents
from scripts.product_v0231.run_continuity_preflight import (
    _contains_absolute_locator,
)


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
HEAD = "b" * 40
RUN_ROOT = Path(".local/product-v023/baseline-readiness/runs/test-run")
FLAG_DIRECTORY = RUN_ROOT / "private/demo/runtime/flagd"
FLAG_FILE = FLAG_DIRECTORY / "demo.flagd.json"


def _context(
    runtime_authority_sha256: str = "c" * 64,
) -> ProductBaselineContinuationContextV0231:
    return ProductBaselineContinuationContextV0231.build(
        predecessor_head=HEAD,
        source_attempt_sha256="1" * 64,
        source_private_report_sha256="2" * 64,
        product_data_root_locator=str(RUN_ROOT / "product"),
        product_data_root_locator_sha256="3" * 64,
        environment_id="env-" + "4" * 24,
        active_baseline_id="base-" + "5" * 24,
        active_baseline_sha256="6" * 64,
        readiness_audit_sha256="7" * 64,
        parity_sha256="8" * 64,
        active_profile_sha256="9" * 64,
        service_identity_sha256="a" * 64,
        capability_sha256="b" * 64,
        runtime_authority_path=str(RUN_ROOT / "product/pilot/runtime-authority.json"),
        runtime_authority_sha256=runtime_authority_sha256,
    )


def _binding(flag_sha256: str) -> ProductV023PrivateStateBindingV0231:
    return ProductV023PrivateStateBindingV0231(
        baseline_private_report_locator=str(
            RUN_ROOT / "private/attempt-completion.json"
        ),
        baseline_private_report_sha256="2" * 64,
        product_data_root_locator=str(RUN_ROOT / "product"),
        product_database_sha256=SHA,
        product_database_wal_sha256=SHA,
        product_database_shm_sha256=SHA,
        nofault_blocker_locator=str(RUN_ROOT / "private/nofault-blocker.json"),
        nofault_blocker_sha256=SHA,
        runtime_authority_locator=str(
            RUN_ROOT / "product/pilot/runtime-authority.json"
        ),
        runtime_authority_file_sha256=SHA,
        resolved_compose_locator=str(
            RUN_ROOT / "private/demo/control/resolved-compose.json"
        ),
        resolved_compose_file_sha256=SHA,
        flagd_file_locator=str(FLAG_FILE),
        flagd_file_sha256=flag_sha256,
    )


def _compose(flag_directory: Path) -> dict[str, Any]:
    source = str(flag_directory.resolve(strict=True))
    return {
        "services": {
            "flagd": {
                "volumes": [
                    {
                        "type": "bind",
                        "source": source,
                        "target": "/etc/flagd",
                        "read_only": True,
                    }
                ]
            },
            "flagd-ui": {
                "volumes": [
                    {
                        "type": "bind",
                        "source": source,
                        "target": "/app/data",
                    }
                ]
            },
        }
    }


def _flag_fixture(tmp_path: Path) -> tuple[bytes, ConfigBundle]:
    bundle = load_bundle(ROOT / "config/live-telemetry-controlled-remediation-v1")
    upstream_source = (
        ROOT / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    )
    upstream_bytes = upstream_source.read_bytes()
    upstream = json.loads(upstream_bytes)
    upstream_target = (
        tmp_path / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    )
    upstream_target.parent.mkdir(parents=True)
    upstream_target.write_bytes(upstream_bytes)
    baseline, _fault = build_flag_documents(upstream, bundle)
    payload = canonical_json_bytes(baseline)
    directory = tmp_path / FLAG_DIRECTORY
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    flag_file = tmp_path / FLAG_FILE
    flag_file.write_bytes(payload)
    flag_file.chmod(0o600)
    return payload, bundle


def _authority() -> PilotRuntimeAuthorityV02:
    return PilotRuntimeAuthorityV02.build(
        environment_id=_context().environment_id,
        allowed_logical_services=("checkout",),
        profile_sha256="d" * 64,
        daemon_identity_sha256="e" * 64,
        docker_context_sha256="f" * 64,
        config_bundle_sha256="1" * 64,
        resolved_sandbox_sha256="2" * 64,
        resolved_endpoints_sha256="3" * 64,
        ownership_scope_sha256="4" * 64,
    )


def _resolved(raw_compose: dict[str, object]) -> ResolvedSandbox:
    return ResolvedSandbox(
        compose_sha256=semantic_sha256(raw_compose),
        services=("flagd", "flagd-ui"),
        image_references=("example.invalid/frozen:1",),
        endpoints=LocalEndpoints(
            frontend="http://127.0.0.1:18080",
            flag_control="http://127.0.0.1:18080/feature/api",
            flag_evaluation="http://127.0.0.1:18016",
            prometheus="http://127.0.0.1:19090",
            opensearch="http://127.0.0.1:19200",
            jaeger="http://127.0.0.1:11686",
        ),
    )


def _authority_for_runtime(
    *,
    bundle: ConfigBundle,
    resolved: ResolvedSandbox,
    daemon_id: str = "daemon-1",
    updates: dict[str, str] | None = None,
) -> PilotRuntimeAuthorityV02:
    authority_inputs = {
        "daemon_identity_sha256": semantic_sha256({"daemon_identity": daemon_id}),
        "docker_context_sha256": semantic_sha256({"docker_context": "desktop-linux"}),
        "config_bundle_sha256": semantic_sha256(bundle.model_dump(mode="json")),
        "resolved_sandbox_sha256": semantic_sha256(
            resolved.model_dump(mode="json")
        ),
        "resolved_endpoints_sha256": semantic_sha256(
            {
                "prometheus": resolved.endpoints.prometheus,
                "opensearch": resolved.endpoints.opensearch,
                "jaeger": resolved.endpoints.jaeger,
                "docker": "unix:///private/docker.sock",
            }
        ),
        "ownership_scope_sha256": semantic_sha256(
            {
                "compose_project": bundle.environment.compose_project,
                "sandbox_label_key": bundle.environment.sandbox_label_key,
                "sandbox_label_value": bundle.environment.sandbox_id,
            }
        ),
    }
    authority_inputs.update(updates or {})
    return PilotRuntimeAuthorityV02.build(
        environment_id=_context().environment_id,
        allowed_logical_services=("checkout",),
        profile_sha256="d" * 64,
        **authority_inputs,
    )


class _FakeEnvironment:
    def __init__(
        self,
        *,
        repository_root: Path,
        bundle: ConfigBundle,
        flagd_directory: Path,
        raw_compose: dict[str, Any],
        resolved: ResolvedSandbox,
        daemon_id: str,
    ) -> None:
        self.repository_root = repository_root
        self.bundle = bundle
        self.flagd_directory = flagd_directory
        self.raw_compose = raw_compose
        self.resolved = resolved
        self.daemon_id = daemon_id
        self.docker_context = "desktop-linux"
        self.docker_endpoint = "unix:///private/docker.sock"
        self.owned_resources = {"container": 0, "network": 0, "volume": 0}
        self.start_count = 0
        self.cleanup_count = 0
        self._baseline_snapshot: object | None = None

    def verify_local_docker(self) -> dict[str, str]:
        return {
            "context": self.docker_context,
            "endpoint": self.docker_endpoint,
            "daemon_id": self.daemon_id,
        }

    def verify_upstream(self) -> None:
        return None

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        assert require_complete is False
        return dict(self.owned_resources)

    def resolve(self) -> tuple[ResolvedSandbox, dict[str, Any]]:
        return self.resolved, self.raw_compose

    def start(self) -> None:
        self.start_count += 1
        self._baseline_snapshot = object()

    def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
        self.cleanup_count += 1
        return CleanupResult(
            baseline_restored=baseline_restored,
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            non_owned_resources_changed=False,
            verdict="CLEAN" if baseline_restored else "BLOCKED",
        )


def _passing_lifecycle(
    tmp_path: Path,
) -> tuple[
    AuthorityContinuousSandboxLifecycleV0231,
    _FakeEnvironment,
    ConfigBundle,
    dict[str, Any],
    ResolvedSandbox,
]:
    payload, bundle = _flag_fixture(tmp_path)
    raw_compose = _compose(tmp_path / FLAG_DIRECTORY)
    resolved = _resolved(raw_compose)
    authority = _authority_for_runtime(bundle=bundle, resolved=resolved)
    instances: list[_FakeEnvironment] = []

    def factory(**kwargs: object) -> _FakeEnvironment:
        environment = _FakeEnvironment(
            repository_root=Path(str(kwargs["repository_root"])),
            bundle=kwargs["bundle"],  # type: ignore[arg-type]
            flagd_directory=Path(str(kwargs["flagd_directory"])),
            raw_compose=raw_compose,
            resolved=resolved,
            daemon_id="daemon-1",
        )
        instances.append(environment)
        return environment

    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=tmp_path,
        private_root=tmp_path / "preflight-private",
        binding=_binding(hashlib.sha256(payload).hexdigest()),
        context=_context(authority.pilot_authority_sha256),
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=raw_compose,
        environment_factory=factory,
    )
    lifecycle.admit_prestart()
    return lifecycle, instances[0], bundle, raw_compose, resolved


def test_public_locator_guard_allows_only_the_fixed_container_destination() -> None:
    assert _contains_absolute_locator({"container_destination": "/etc/flagd"}) is False
    assert _contains_absolute_locator({"flag_file_locator": "/private/flagd"}) is True
    assert _contains_absolute_locator({"flag_file_locator": "/etc/flagd"}) is True


def test_flagd_descriptor_admits_only_exact_bound_path(tmp_path: Path) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    binding = _binding(hashlib.sha256(payload).hexdigest())
    descriptor = admit_flagd_bind_descriptor_v0231(
        predecessor_root=tmp_path,
        binding=binding,
        context=_context(),
        bundle=bundle,
        resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
    )

    assert descriptor.flagd_directory_locator == str(FLAG_DIRECTORY)
    assert descriptor.flag_file_locator == str(FLAG_FILE)
    assert descriptor.flag_file_mode == 0o600
    assert descriptor.directory_mode == 0o700
    assert descriptor.container_destination == "/etc/flagd"
    assert descriptor.mount_mode == "READ_ONLY"
    assert descriptor.flag_file_bytes_sha256 == hashlib.sha256(payload).hexdigest()

    relocated = tmp_path / "relocated"
    relocated.mkdir(mode=0o700)
    (relocated / "demo.flagd.json").write_bytes(payload)
    (relocated / "demo.flagd.json").chmod(0o600)
    with pytest.raises(ValueError, match="exact flagd mounts differ"):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=binding,
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(relocated),
        )


def test_flagd_descriptor_rejects_mode_or_symlink_drift(tmp_path: Path) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    binding = _binding(hashlib.sha256(payload).hexdigest())
    flag_file = tmp_path / FLAG_FILE
    flag_file.chmod(0o644)
    with pytest.raises(PermissionError, match="flag file mode"):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=binding,
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
        )

    flag_file.chmod(0o600)
    real_directory = tmp_path / "flagd-real"
    (tmp_path / FLAG_DIRECTORY).rename(real_directory)
    (tmp_path / FLAG_DIRECTORY).symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(OSError):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=binding,
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(real_directory),
        )


def test_flagd_descriptor_reconstructs_only_at_exact_existing_directory(
    tmp_path: Path,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    binding = _binding(hashlib.sha256(payload).hexdigest())
    flag_file = tmp_path / FLAG_FILE
    flag_file.unlink()
    proof_root = tmp_path / "private-proof"
    proof_root.mkdir(mode=0o700)
    proof = proof_root / "flagd-reconstruction.json"

    descriptor = admit_flagd_bind_descriptor_v0231(
        predecessor_root=tmp_path,
        binding=binding,
        context=_context(),
        bundle=bundle,
        resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
        reconstruction_proof_path=proof,
    )

    assert flag_file.read_bytes() == payload
    assert stat.S_IMODE(flag_file.stat().st_mode) == 0o600
    assert descriptor.flag_file_bytes_sha256 == hashlib.sha256(payload).hexdigest()
    proof_payload = json.loads(proof.read_text(encoding="utf-8"))
    assert proof_payload["flag_file_locator"] == str(FLAG_FILE)
    assert proof_payload["flag_file_bytes_sha256"] == hashlib.sha256(payload).hexdigest()
    assert "absolute" not in json.dumps(proof_payload)

    flag_file.unlink()
    with pytest.raises(FileExistsError):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=binding,
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
            reconstruction_proof_path=proof,
        )


def test_flagd_reconstruction_rejects_mismatched_bytes_before_any_write(
    tmp_path: Path,
) -> None:
    _payload, bundle = _flag_fixture(tmp_path)
    flag_file = tmp_path / FLAG_FILE
    flag_file.unlink()
    proof_root = tmp_path / "private-proof"
    proof_root.mkdir(mode=0o700)
    proof = proof_root / "flagd-reconstruction.json"

    with pytest.raises(ValueError, match="differ before write"):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=_binding("0" * 64),
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
            reconstruction_proof_path=proof,
        )

    assert flag_file.exists() is False
    assert proof.exists() is False


def test_flagd_reconstruction_rolls_back_flag_and_partial_proof_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    flag_file = tmp_path / FLAG_FILE
    flag_file.unlink()
    proof_root = tmp_path / "private-proof"
    proof_root.mkdir(mode=0o700)
    proof = proof_root / "flagd-reconstruction.json"
    original_write = os.write
    write_count = 0

    def fail_during_proof(descriptor: int, body: bytes) -> int:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            partial = max(1, len(body) // 2)
            original_write(descriptor, body[:partial])
            raise OSError("simulated proof write failure")
        return original_write(descriptor, body)

    monkeypatch.setattr(
        "ecomsre.product.pilot.runtime_continuity_v0231.os.write",
        fail_during_proof,
    )
    with pytest.raises(OSError, match="simulated proof write failure"):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=_binding(hashlib.sha256(payload).hexdigest()),
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
            reconstruction_proof_path=proof,
        )

    assert flag_file.exists() is False
    assert proof.exists() is False


@pytest.mark.parametrize("failure_point", ("write", "fsync"))
def test_flagd_reconstruction_rolls_back_partial_flag_before_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    flag_file = tmp_path / FLAG_FILE
    flag_file.unlink()
    proof_root = tmp_path / "private-proof"
    proof_root.mkdir(mode=0o700)
    proof = proof_root / "flagd-reconstruction.json"

    if failure_point == "write":
        monkeypatch.setattr(
            "ecomsre.product.pilot.runtime_continuity_v0231.os.write",
            lambda _descriptor, _body: (_ for _ in ()).throw(
                OSError("simulated flag write failure")
            ),
        )
    else:
        monkeypatch.setattr(
            "ecomsre.product.pilot.runtime_continuity_v0231.os.fsync",
            lambda _descriptor: (_ for _ in ()).throw(
                OSError("simulated flag fsync failure")
            ),
        )

    with pytest.raises(OSError, match=f"simulated flag {failure_point} failure"):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=_binding(hashlib.sha256(payload).hexdigest()),
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
            reconstruction_proof_path=proof,
        )

    assert flag_file.exists() is False
    assert proof.exists() is False


def test_flagd_reconstruction_rejects_symlinked_proof_parent_before_write(
    tmp_path: Path,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    flag_file = tmp_path / FLAG_FILE
    flag_file.unlink()
    real_proof_root = tmp_path / "private-proof-real"
    real_proof_root.mkdir(mode=0o700)
    proof_root = tmp_path / "private-proof-link"
    proof_root.symlink_to(real_proof_root, target_is_directory=True)
    proof = proof_root / "flagd-reconstruction.json"

    with pytest.raises(ValueError, match="contains a symlink"):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=_binding(hashlib.sha256(payload).hexdigest()),
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
            reconstruction_proof_path=proof,
        )

    assert flag_file.exists() is False
    assert (real_proof_root / proof.name).exists() is False


def test_runtime_continuity_descriptor_binds_every_authority_component() -> None:
    authority = _authority()
    context = _context(authority.pilot_authority_sha256)
    flagd = FlagdBindDescriptorV0231.build(
        source_attempt_sha256=context.source_attempt_sha256,
        flagd_directory_locator=str(FLAG_DIRECTORY),
        flagd_directory_locator_sha256="5" * 64,
        flag_file_locator=str(FLAG_FILE),
        flag_file_locator_sha256="6" * 64,
        flag_file_bytes_sha256="7" * 64,
        flag_file_mode=0o600,
        directory_mode=0o700,
        container_destination="/etc/flagd",
        mount_mode="READ_ONLY",
        baseline_document_sha256="7" * 64,
        fault_document_sha256="8" * 64,
        config_bundle_sha256="1" * 64,
        resolved_compose_sha256="9" * 64,
    )
    descriptor = build_runtime_authority_continuity_descriptor_v0231(
        authority=authority,
        context=context,
        flagd_descriptor=flagd,
        resolved_compose_sha256=flagd.resolved_compose_sha256,
    )

    assert descriptor.read_authority_sha256 == authority.read_authority.authority_sha256
    assert descriptor.pilot_runtime_authority_sha256 == (
        authority.pilot_authority_sha256
    )
    assert descriptor.connector_binding_sha256 == authority.connector_binding_sha256

    payload = descriptor.model_dump(mode="json")
    payload["daemon_identity_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="descriptor digest differs"):
        RuntimeAuthorityContinuityDescriptorV0231.model_validate(payload)


def test_exact_path_lifecycle_preflight_proves_authority_without_starting(
    tmp_path: Path,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    raw_compose = _compose(tmp_path / FLAG_DIRECTORY)
    resolved = _resolved(raw_compose)
    authority = _authority_for_runtime(bundle=bundle, resolved=resolved)
    context = _context(authority.pilot_authority_sha256)
    instances: list[_FakeEnvironment] = []

    def factory(**kwargs: object) -> _FakeEnvironment:
        environment = _FakeEnvironment(
            repository_root=Path(str(kwargs["repository_root"])),
            bundle=kwargs["bundle"],  # type: ignore[arg-type]
            flagd_directory=Path(str(kwargs["flagd_directory"])),
            raw_compose=raw_compose,
            resolved=resolved,
            daemon_id="daemon-1",
        )
        instances.append(environment)
        return environment

    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=tmp_path,
        private_root=tmp_path / "preflight-private",
        binding=_binding(hashlib.sha256(payload).hexdigest()),
        context=context,
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=raw_compose,
        environment_factory=factory,
    )

    report = lifecycle.admit_prestart()

    assert report.terminal == "ECOMSRE_PRODUCT_V0231_CONTINUITY_PREFLIGHT_PASS"
    assert report.descriptor_terminal == (
        "ECOMSRE_PRODUCT_V0231_CONTINUITY_DESCRIPTOR_PASS"
    )
    assert instances[0].start_count == 0
    assert lifecycle.started is False
    assert report.docker_start_count == 0
    assert report.live_session_count == 0
    assert report.fault_attempt_count == 0
    assert report.action_authority == "NONE"
    assert (tmp_path / "preflight-private/pre-start-resolve.json").is_file()


@pytest.mark.parametrize("drift", ["compose", "daemon"])
def test_exact_path_lifecycle_rejects_prestart_continuity_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    preserved_raw = _compose(tmp_path / FLAG_DIRECTORY)
    preserved_resolved = _resolved(preserved_raw)
    authority = _authority_for_runtime(bundle=bundle, resolved=preserved_resolved)
    current_raw = json.loads(json.dumps(preserved_raw))
    if drift == "compose":
        current_raw["services"]["flagd"]["volumes"][0]["read_only"] = False
    current_resolved = _resolved(current_raw)
    instances: list[_FakeEnvironment] = []

    def factory(**kwargs: object) -> _FakeEnvironment:
        environment = _FakeEnvironment(
            repository_root=Path(str(kwargs["repository_root"])),
            bundle=kwargs["bundle"],  # type: ignore[arg-type]
            flagd_directory=Path(str(kwargs["flagd_directory"])),
            raw_compose=current_raw,
            resolved=current_resolved,
            daemon_id="daemon-2" if drift == "daemon" else "daemon-1",
        )
        instances.append(environment)
        return environment

    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=tmp_path,
        private_root=tmp_path / "preflight-private",
        binding=_binding(hashlib.sha256(payload).hexdigest()),
        context=_context(authority.pilot_authority_sha256),
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=preserved_raw,
        environment_factory=factory,
    )

    terminal = (
        "BLOCKED_ECOMSRE_PRODUCT_V0231_COMPOSE_CONTINUITY"
        if drift == "compose"
        else "BLOCKED_ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY"
    )
    with pytest.raises(ValueError, match=terminal):
        lifecycle.admit_prestart()
    assert instances[0].start_count == 0


@pytest.mark.parametrize(
    "authority_field",
    (
        "daemon_identity_sha256",
        "docker_context_sha256",
        "config_bundle_sha256",
        "resolved_sandbox_sha256",
        "resolved_endpoints_sha256",
        "ownership_scope_sha256",
    ),
)
def test_exact_path_lifecycle_rejects_each_authority_component_drift(
    tmp_path: Path,
    authority_field: str,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    raw_compose = _compose(tmp_path / FLAG_DIRECTORY)
    resolved = _resolved(raw_compose)
    authority = _authority_for_runtime(
        bundle=bundle,
        resolved=resolved,
        updates={authority_field: "0" * 64},
    )
    instances: list[_FakeEnvironment] = []

    def factory(**kwargs: object) -> _FakeEnvironment:
        environment = _FakeEnvironment(
            repository_root=Path(str(kwargs["repository_root"])),
            bundle=kwargs["bundle"],  # type: ignore[arg-type]
            flagd_directory=Path(str(kwargs["flagd_directory"])),
            raw_compose=raw_compose,
            resolved=resolved,
            daemon_id="daemon-1",
        )
        instances.append(environment)
        return environment

    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=tmp_path,
        private_root=tmp_path / "preflight-private",
        binding=_binding(hashlib.sha256(payload).hexdigest()),
        context=_context(authority.pilot_authority_sha256),
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=raw_compose,
        environment_factory=factory,
    )

    with pytest.raises(ValueError, match="BLOCKED_ECOMSRE_PRODUCT_V0231"):
        lifecycle.admit_prestart()
    assert all(instance.start_count == 0 for instance in instances)


def test_exact_path_lifecycle_revalidates_flag_bytes_immediately_before_start(
    tmp_path: Path,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    raw_compose = _compose(tmp_path / FLAG_DIRECTORY)
    resolved = _resolved(raw_compose)
    authority = _authority_for_runtime(bundle=bundle, resolved=resolved)
    instances: list[_FakeEnvironment] = []

    def factory(**kwargs: object) -> _FakeEnvironment:
        environment = _FakeEnvironment(
            repository_root=Path(str(kwargs["repository_root"])),
            bundle=kwargs["bundle"],  # type: ignore[arg-type]
            flagd_directory=Path(str(kwargs["flagd_directory"])),
            raw_compose=raw_compose,
            resolved=resolved,
            daemon_id="daemon-1",
        )
        instances.append(environment)
        return environment

    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=tmp_path,
        private_root=tmp_path / "preflight-private",
        binding=_binding(hashlib.sha256(payload).hexdigest()),
        context=_context(authority.pilot_authority_sha256),
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=raw_compose,
        environment_factory=factory,
    )
    lifecycle.admit_prestart()
    (tmp_path / FLAG_FILE).write_bytes(b"{}")

    with pytest.raises(ValueError, match="exact flagd file bytes differ"):
        lifecycle.start()
    assert instances[0].start_count == 0


def test_start_consumes_session_only_after_fresh_boundary_verification(
    tmp_path: Path,
) -> None:
    lifecycle, environment, _bundle, _raw_compose, _resolved_value = (
        _passing_lifecycle(tmp_path)
    )
    consumed: list[str] = []

    lifecycle.start(on_boundary_verified=lambda: consumed.append("SESSION_1"))

    assert consumed == ["SESSION_1"]
    assert environment.start_count == 1


def test_cleanup_reauthenticates_full_authority_before_destructive_down(
    tmp_path: Path,
) -> None:
    lifecycle, environment, _bundle, _raw_compose, _resolved_value = (
        _passing_lifecycle(tmp_path)
    )
    lifecycle.start()
    environment.daemon_id = "drifted-daemon"

    with pytest.raises(
        ValueError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0231_CLEANUP_AUTHORITY_CONTINUITY",
    ):
        lifecycle.cleanup_owned(baseline_unchanged=True)
    assert environment.cleanup_count == 0


def test_start_boundary_rejects_fresh_compose_or_owned_resource_drift(
    tmp_path: Path,
) -> None:
    lifecycle, environment, _bundle, _raw_compose, _resolved_value = (
        _passing_lifecycle(tmp_path)
    )
    environment.raw_compose["services"]["flagd"]["volumes"][0][
        "read_only"
    ] = False
    environment.resolved = _resolved(environment.raw_compose)

    consumed: list[str] = []
    with pytest.raises(
        ValueError, match="BLOCKED_ECOMSRE_PRODUCT_V0231_COMPOSE_CONTINUITY"
    ):
        lifecycle.start(on_boundary_verified=lambda: consumed.append("SESSION_1"))
    assert environment.start_count == 0
    assert consumed == []

    environment.raw_compose["services"]["flagd"]["volumes"][0][
        "read_only"
    ] = True
    environment.resolved = _resolved(environment.raw_compose)
    environment.owned_resources["container"] = 1
    with pytest.raises(
        ValueError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0231_PREEXISTING_OWNED_RESOURCES",
    ):
        lifecycle.start(on_boundary_verified=lambda: consumed.append("SESSION_1"))
    assert environment.start_count == 0
    assert consumed == []


@pytest.mark.parametrize(
    "authority_field",
    (
        "daemon_identity_sha256",
        "docker_context_sha256",
        "config_bundle_sha256",
        "resolved_sandbox_sha256",
        "resolved_endpoints_sha256",
        "ownership_scope_sha256",
    ),
)
def test_start_boundary_rejects_each_fresh_authority_component_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authority_field: str,
) -> None:
    lifecycle, environment, bundle, _raw_compose, resolved = _passing_lifecycle(
        tmp_path
    )
    original = continuity_v0231._expected_rebound_authority_v0231

    def drifted_rebound(**kwargs: object) -> PilotRuntimeAuthorityV02:
        original(**kwargs)  # type: ignore[arg-type]
        return _authority_for_runtime(
            bundle=bundle,
            resolved=resolved,
            updates={authority_field: "0" * 64},
        )

    monkeypatch.setattr(
        continuity_v0231,
        "_expected_rebound_authority_v0231",
        drifted_rebound,
    )
    with pytest.raises(
        ValueError,
        match="BLOCKED_ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY",
    ):
        lifecycle.start()
    assert environment.start_count == 0


def test_flagd_descriptor_self_seal_rejects_tampering() -> None:
    descriptor = FlagdBindDescriptorV0231.build(
        source_attempt_sha256="1" * 64,
        flagd_directory_locator=str(FLAG_DIRECTORY),
        flagd_directory_locator_sha256="2" * 64,
        flag_file_locator=str(FLAG_FILE),
        flag_file_locator_sha256="3" * 64,
        flag_file_bytes_sha256="4" * 64,
        flag_file_mode=0o600,
        directory_mode=0o700,
        container_destination="/etc/flagd",
        mount_mode="READ_ONLY",
        baseline_document_sha256="4" * 64,
        fault_document_sha256="5" * 64,
        config_bundle_sha256="6" * 64,
        resolved_compose_sha256="7" * 64,
    )
    tampered = descriptor.model_dump(mode="json")
    tampered["flagd_directory_locator_sha256"] = semantic_sha256(
        {"relocated": True}
    )
    with pytest.raises(ValidationError, match="descriptor digest differs"):
        FlagdBindDescriptorV0231.model_validate(tampered)


def test_exact_flagd_admission_requires_current_user_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, bundle = _flag_fixture(tmp_path)
    binding = _binding(hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)

    with pytest.raises(PermissionError, match="ownership"):
        admit_flagd_bind_descriptor_v0231(
            predecessor_root=tmp_path,
            binding=binding,
            context=_context(),
            bundle=bundle,
            resolved_compose=_compose(tmp_path / FLAG_DIRECTORY),
        )
