import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.environment.preflight import (
    AuthenticatedPreflightEvidence,
    CommandResult,
    DockerSnapshot,
    HostSnapshot,
    OwnershipProof,
    PreflightCollectionError,
    PortObservation,
    PreflightInputs,
    PreflightResult,
    ResourceObservation,
    collect_docker_snapshot,
    collect_host_snapshot,
    compose_plugin_major_version,
    evaluate_preflight,
    issue_authenticated_preflight_evidence,
    preflight_failure_result,
)
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipManifest,
)
from ecomsre.environment.ownership_authority import (
    create_ownership_authority_artifacts,
    load_authenticated_ownership_context,
)
from ecomsre.environment.manifests import LockMatchChecks, LockVerification
from ecomsre.phase0.models import Outcome


GIB = 1024**3
EXPECTED_COMMIT = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
COMPOSE_HASH = "a" * 64
ACTIVE_RUN_ID = "7" * 32


def _ownership_manifest(
    resources: tuple[OwnedResource, ...] = (),
) -> OwnershipManifest:
    return OwnershipManifest(run_id=ACTIVE_RUN_ID, resources=resources)


def _ownership_context(tmp_path, manifest: OwnershipManifest):
    create_ownership_authority_artifacts(
        tmp_path,
        manifest,
        created_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
    )
    return load_authenticated_ownership_context(
        tmp_path,
        manifest.run_id,
    )


class FixtureRunner:
    def __init__(self, results: dict[tuple[str, ...], CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        self.calls.append(arguments)
        return self.results[arguments]


def _host(**overrides) -> HostSnapshot:
    values = {
        "macos_version": "26.5.2",
        "macos_build": "25F84",
        "architecture": "arm64",
        "cpu_model": "Apple M5 Pro",
        "cpu_count": 12,
        "total_memory_bytes": 48 * GIB,
        "available_memory_bytes": 32 * GIB,
        "available_disk_bytes": 679 * GIB,
    }
    values.update(overrides)
    return HostSnapshot(**values)


def _docker(**overrides) -> DockerSnapshot:
    values = {
        "client_available": True,
        "client_version": "29.6.1",
        "daemon_available": True,
        "server_version": "29.6.1",
        "desktop_version": "4.50.0",
        "engine": "Docker Desktop",
        "desktop_identity_verified": True,
        "compose_available": True,
        "compose_version": "v5.3.0",
        "compose_plugin_v2": True,
        "server_os_type": "linux",
        "server_architecture": "arm64",
        "native_platform": "linux/arm64",
        "cpu_count": 12,
        "memory_bytes": 24 * GIB,
        "disk_bytes": 100 * GIB,
        "resource_fields_verified": True,
        "context_name": "desktop-linux",
        "endpoint": "unix:///var/run/docker.sock",
        "daemon_id": "fixture-daemon-id",
    }
    values.update(overrides)
    return DockerSnapshot(**values)


def test_docker_snapshot_requires_observed_daemon_binding_fields() -> None:
    payload = _docker().model_dump()

    for field in ("context_name", "endpoint", "daemon_id"):
        missing = dict(payload)
        missing.pop(field)
        with pytest.raises(ValidationError, match=field):
            DockerSnapshot(**missing)


def _inputs(**overrides) -> PreflightInputs:
    values = {
        "host": _host(),
        "docker": _docker(),
        "ports": (PortObservation(port=8080, occupied=False, ownership="NONE"),),
        "resources": (),
        "ownership_context": None,
        "observed_upstream_commit": EXPECTED_COMMIT,
        "observed_compose_config_sha256": COMPOSE_HASH,
        "expected_compose_config_sha256": COMPOSE_HASH,
        "image_lock_verification": LockVerification(
            passed=True,
            outcome=Outcome.SUCCESS,
            reason_codes=(),
            checks=LockMatchChecks.all_passed(),
        ),
        "pull_policy": "never",
    }
    values.update(overrides)
    return PreflightInputs(**values)


def test_supported_arm64_snapshot_passes_read_only_preflight() -> None:
    result = evaluate_preflight(_inputs())

    assert result.outcome is Outcome.SUCCESS
    assert result.exit_code == 0
    assert result.reason_codes == ()


def test_preflight_evidence_is_authority_issued_and_binds_complete_inputs() -> None:
    inputs = _inputs()
    collected_at = datetime.now(UTC)
    monotonic_finished_ns = time.monotonic_ns()

    evidence = issue_authenticated_preflight_evidence(
        run_id=ACTIVE_RUN_ID,
        inputs=inputs,
        collected_at=collected_at,
        monotonic_started_ns=monotonic_finished_ns - 1_000,
        monotonic_finished_ns=monotonic_finished_ns,
    )

    assert evidence.is_authentic()
    assert evidence.is_current()
    assert evidence.inputs == inputs
    assert evidence.result.outcome is Outcome.SUCCESS
    assert len(evidence.content_sha256) == 64
    with pytest.raises(TypeError, match="authority"):
        AuthenticatedPreflightEvidence(
            run_id=ACTIVE_RUN_ID,
            inputs=inputs,
            result=evidence.result,
            collected_at=collected_at,
            monotonic_started_ns=monotonic_finished_ns - 1_000,
            monotonic_finished_ns=monotonic_finished_ns,
            content_sha256=evidence.content_sha256,
        )


def test_preflight_evidence_tamper_and_staleness_are_rejected() -> None:
    current_monotonic_ns = time.monotonic_ns()
    stale = issue_authenticated_preflight_evidence(
        run_id=ACTIVE_RUN_ID,
        inputs=_inputs(),
        collected_at=datetime.now(UTC) - timedelta(minutes=5),
        monotonic_started_ns=current_monotonic_ns - 300_000_001_000,
        monotonic_finished_ns=current_monotonic_ns - 300_000_000_000,
    )

    assert stale.is_authentic()
    assert not stale.is_current()

    tampered = issue_authenticated_preflight_evidence(
        run_id=ACTIVE_RUN_ID,
        inputs=_inputs(),
        collected_at=datetime.now(UTC),
        monotonic_started_ns=current_monotonic_ns - 1_000,
        monotonic_finished_ns=current_monotonic_ns,
    )
    object.__setattr__(tampered, "_inputs", _inputs(host=_host(cpu_count=8)))

    assert not tampered.is_authentic()
    assert not tampered.is_current()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("context_name", "default"),
        ("endpoint", "unix:///changed/docker.sock"),
        ("daemon_id", "changed-daemon-id"),
    ],
)
def test_preflight_evidence_authentication_binds_daemon_identity(
    field: str,
    replacement: str,
) -> None:
    now = time.monotonic_ns()
    evidence = issue_authenticated_preflight_evidence(
        run_id=ACTIVE_RUN_ID,
        inputs=_inputs(),
        collected_at=datetime.now(UTC),
        monotonic_started_ns=now - 1_000,
        monotonic_finished_ns=now,
    )
    changed_docker = evidence.inputs.docker.model_copy(update={field: replacement})
    object.__setattr__(
        evidence,
        "_inputs",
        evidence.inputs.model_copy(update={"docker": changed_docker}),
    )

    assert not evidence.is_authentic()
    assert not evidence.is_current()


@pytest.mark.parametrize(
    "host",
    [
        _host(architecture="x86_64"),
        _host(total_memory_bytes=15 * GIB),
        _host(available_disk_bytes=24 * GIB),
    ],
)
def test_unsupported_architecture_or_host_resources_block_environment(
    host: HostSnapshot,
) -> None:
    result = evaluate_preflight(_inputs(host=host))

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.exit_code == 20
    assert "ENVIRONMENT_UNSUPPORTED" in result.reason_codes


def test_daemon_unavailable_is_blocked_environment_and_never_auto_started() -> None:
    runner = FixtureRunner(
        {
            (
                "docker",
                "--context",
                "desktop-linux",
                "context",
                "inspect",
                "desktop-linux",
                "--format",
                "{{json .}}",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--context",
                    "desktop-linux",
                    "context",
                    "inspect",
                    "desktop-linux",
                    "--format",
                    "{{json .}}",
                ),
                exit_code=0,
                stdout=json.dumps(
                    {
                        "Name": "desktop-linux",
                        "Endpoints": {
                            "docker": {
                                "Host": "unix:///var/run/docker.sock",
                            }
                        },
                    }
                ),
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "--version",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "--version",
                ),
                exit_code=0,
                stdout="Docker version 29.6.1, build test",
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "compose",
                "version",
                "--short",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "compose",
                    "version",
                    "--short",
                ),
                exit_code=0,
                stdout="5.3.0\n",
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "info",
                "--format",
                "{{json .}}",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "info",
                    "--format",
                    "{{json .}}",
                ),
                exit_code=1,
                stdout="",
                stderr="Cannot connect to the Docker daemon",
            ),
        }
    )

    snapshot = collect_docker_snapshot(runner)
    result = evaluate_preflight(_inputs(docker=snapshot))

    assert snapshot.daemon_available is False
    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.exit_code == 20
    assert "PREFLIGHT_BLOCKED" in result.reason_codes
    assert all("start" not in call for call in runner.calls)


@pytest.mark.parametrize(
    "endpoint",
    ["tcp://127.0.0.1:2375", "ssh://docker@example.test"],
)
def test_remote_context_snapshot_is_blocked_without_daemon_commands(
    endpoint: str,
) -> None:
    context_arguments = (
        "docker",
        "--context",
        "desktop-linux",
        "context",
        "inspect",
        "desktop-linux",
        "--format",
        "{{json .}}",
    )
    runner = FixtureRunner(
        {
            context_arguments: CommandResult(
                arguments=context_arguments,
                exit_code=0,
                stdout=json.dumps(
                    {
                        "Name": "desktop-linux",
                        "Endpoints": {"docker": {"Host": endpoint}},
                    }
                ),
                stderr="",
            )
        }
    )

    snapshot = collect_docker_snapshot(runner)
    result = evaluate_preflight(_inputs(docker=snapshot))

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert snapshot.endpoint == endpoint
    assert snapshot.daemon_id == ""
    assert runner.calls == [context_arguments]


@pytest.mark.parametrize(
    "docker",
    [
        _docker(client_available=False),
        _docker(compose_available=False),
        _docker(memory_bytes=15 * GIB),
        _docker(disk_bytes=24 * GIB),
    ],
)
def test_docker_compose_or_allocation_failure_blocks_environment(
    docker: DockerSnapshot,
) -> None:
    result = evaluate_preflight(_inputs(docker=docker))

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.exit_code == 20


@pytest.mark.parametrize(
    "docker",
    [
        _docker(context_name="default"),
        _docker(context_name=""),
        _docker(endpoint="tcp://127.0.0.1:2375"),
        _docker(endpoint="ssh://docker@example.test"),
        _docker(endpoint=""),
        _docker(daemon_id=""),
        _docker(daemon_id="placeholder"),
    ],
)
def test_unbound_or_remote_docker_daemon_is_blocked_before_mutation(
    docker: DockerSnapshot,
) -> None:
    result = evaluate_preflight(_inputs(docker=docker))

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.exit_code == 20
    assert result.reason_codes == ("ENVIRONMENT_UNSUPPORTED",)


@pytest.mark.parametrize(
    "docker",
    [
        _docker(desktop_identity_verified=False),
        _docker(compose_plugin_v2=False),
        _docker(server_os_type="windows", native_platform="windows/arm64"),
        _docker(server_architecture="amd64", native_platform="linux/amd64"),
        _docker(resource_fields_verified=False),
    ],
)
def test_unverified_docker_identity_or_native_platform_blocks_environment(
    docker: DockerSnapshot,
) -> None:
    result = evaluate_preflight(_inputs(docker=docker))

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.exit_code == 20


def test_unknown_port_owner_stops_unsafe_before_other_failures() -> None:
    inputs = _inputs(
        ports=(PortObservation(port=8080, occupied=True, ownership="UNKNOWN"),),
        observed_upstream_commit="wrong",
    )

    result = evaluate_preflight(inputs)

    assert result.outcome is Outcome.UNSAFE
    assert result.exit_code == 40
    assert result.reason_codes[0] == "RESOURCE_OWNERSHIP_UNKNOWN"


def test_unknown_project_resource_stops_unsafe() -> None:
    inputs = _inputs(
        resources=(
            ResourceObservation(
                kind="container",
                name="ecomsre-phase0-adservice-1",
                ownership="UNKNOWN",
            ),
        ),
    )

    result = evaluate_preflight(inputs)

    assert result.outcome is Outcome.UNSAFE
    assert result.exit_code == 40


def test_present_resource_without_authenticated_context_is_unsafe() -> None:
    result = evaluate_preflight(
        _inputs(
            ports=(
                PortObservation(
                    port=8080,
                    occupied=True,
                    ownership="KNOWN_OTHER",
                ),
            ),
            ownership_context=None,
        )
    )

    assert result.outcome is Outcome.UNSAFE
    assert result.reason_codes == ("RESOURCE_OWNERSHIP_UNKNOWN",)


def test_owned_port_requires_exact_active_manifest_proof(tmp_path) -> None:
    owned_port = OwnedResource(
        kind="port",
        name="tcp:8080",
        resource_id="tcp:8080",
        labels={
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: ACTIVE_RUN_ID,
        },
        identity_evidence=(
            "port:tcp:8080",
            "pid:123",
            "start:Thu Jul 30 10:00:00 2026",
            "executable:/usr/bin/python3",
            "socket:*:8080",
        ),
    )
    manifest = _ownership_manifest((owned_port,))
    context = _ownership_context(tmp_path, manifest)
    port = PortObservation(
        port=8080,
        occupied=True,
        ownership="OWNED",
        ownership_proof=OwnershipProof(
            project_namespace=PROJECT_NAMESPACE,
            manifest_sha256=context.manifest_sha256,
            run_id=ACTIVE_RUN_ID,
            resource_kind="port",
            resource_id="tcp:8080",
            port=8080,
            identifiers=(
                "port:tcp:8080",
                "pid:123",
                "start:Thu Jul 30 10:00:00 2026",
                "executable:/usr/bin/python3",
                "socket:*:8080",
            ),
        ),
    )

    result = evaluate_preflight(
        _inputs(
            ports=(port,),
            ownership_context=context,
        )
    )

    assert result.outcome is Outcome.SUCCESS


@pytest.mark.parametrize("mismatch", ["hash", "run", "inventory"])
def test_owned_proof_mismatch_with_active_manifest_is_unsafe(
    mismatch: str,
    tmp_path,
) -> None:
    owned_port = OwnedResource(
        kind="port",
        name="tcp:8080",
        resource_id="tcp:8080",
        labels={
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: ACTIVE_RUN_ID,
        },
        identity_evidence=(
            "port:tcp:8080",
            "pid:123",
            "start:Thu Jul 30 10:00:00 2026",
            "executable:/usr/bin/python3",
            "socket:*:8080",
        ),
    )
    manifest = _ownership_manifest(() if mismatch == "inventory" else (owned_port,))
    context = _ownership_context(tmp_path, manifest)
    proof = OwnershipProof(
        project_namespace=PROJECT_NAMESPACE,
        manifest_sha256=("8" * 64 if mismatch == "hash" else context.manifest_sha256),
        run_id="9" * 32 if mismatch == "run" else ACTIVE_RUN_ID,
        resource_kind="port",
        resource_id="tcp:8080",
        port=8080,
        identifiers=(
            "port:tcp:8080",
            "pid:123",
            "start:Thu Jul 30 10:00:00 2026",
            "executable:/usr/bin/python3",
            "socket:*:8080",
        ),
    )

    result = evaluate_preflight(
        _inputs(
            ports=(
                PortObservation(
                    port=8080,
                    occupied=True,
                    ownership="OWNED",
                    ownership_proof=proof,
                ),
            ),
            ownership_context=context,
        )
    )

    assert result.outcome is Outcome.UNSAFE
    assert result.reason_codes == ("RESOURCE_OWNERSHIP_UNKNOWN",)


def test_missing_or_tampered_context_cannot_trust_resource_classification(
    tmp_path,
) -> None:
    base = _inputs()
    fake_absent = ResourceObservation.model_construct(
        kind="container",
        name="ecomsre-phase0-adservice-1",
        resource_id="container-id",
        labels={},
        present=True,
        ownership="NONE",
        ownership_proof=None,
    )
    fake_absent_inputs = PreflightInputs.model_construct(
        **{
            **base.__dict__,
            "resources": (fake_absent,),
        }
    )
    classification_result = evaluate_preflight(fake_absent_inputs)

    context = _ownership_context(tmp_path, _ownership_manifest())
    object.__setattr__(context, "_manifest_sha256", "8" * 64)
    tampered_inputs = PreflightInputs.model_construct(
        **{
            **base.__dict__,
            "ownership_context": context,
        }
    )
    tampered_result = evaluate_preflight(tampered_inputs)

    assert classification_result.outcome is Outcome.UNSAFE
    assert tampered_result.outcome is Outcome.UNSAFE


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"observed_upstream_commit": "wrong"}, "INPUT_NOT_FROZEN"),
        (
            {"observed_compose_config_sha256": "b" * 64},
            "COMPOSE_CONFIG_HASH_MISMATCH",
        ),
        ({"pull_policy": "missing"}, "PULL_POLICY_NOT_FROZEN"),
    ],
)
def test_frozen_input_or_pull_policy_mismatch_blocks_upstream(
    overrides: dict,
    reason: str,
) -> None:
    result = evaluate_preflight(_inputs(**overrides))

    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert result.exit_code == 21
    assert reason in result.reason_codes


def test_cached_image_lock_mismatch_blocks_upstream() -> None:
    verification = LockVerification(
        passed=False,
        outcome=Outcome.BLOCKED_UPSTREAM,
        reason_codes=("ARM64_DIGEST_MISMATCH",),
        checks=LockMatchChecks(
            source_references=True,
            digests=False,
            platforms=True,
            image_ids=True,
            upstream_binding=True,
            compose_binding=True,
            complete_inventory=True,
        ),
    )

    result = evaluate_preflight(_inputs(image_lock_verification=verification))

    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert result.exit_code == 21
    assert result.reason_codes == ("ARM64_DIGEST_MISMATCH",)


def test_preflight_rejects_constructed_inconsistent_lock_verification() -> None:
    inconsistent = LockVerification.model_construct(
        passed=True,
        outcome=Outcome.SUCCESS,
        reason_codes=(),
        checks=LockMatchChecks(
            source_references=False,
            digests=True,
            platforms=True,
            image_ids=True,
            upstream_binding=True,
            compose_binding=True,
            complete_inventory=True,
        ),
    )

    valid_inputs = _inputs()
    result = evaluate_preflight(
        PreflightInputs.model_construct(
            **{
                **valid_inputs.__dict__,
                "image_lock_verification": inconsistent,
            }
        )
    )

    assert result.outcome is Outcome.BLOCKED_UPSTREAM
    assert result.reason_codes == ("IMAGE_LOCK_VERIFICATION_INCONSISTENT",)


def test_successful_docker_fixture_is_parsed_without_real_commands() -> None:
    info = {
        "ID": "docker-desktop-daemon",
        "ServerVersion": "29.6.1",
        "OperatingSystem": "Docker Desktop 4.50.0",
        "Name": "docker-desktop",
        "OSType": "linux",
        "Architecture": "aarch64",
        "NCPU": 12,
        "MemTotal": 24 * GIB,
    }
    runner = FixtureRunner(
        {
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "--version",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "--version",
                ),
                exit_code=0,
                stdout="Docker version 29.6.1, build test",
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "compose",
                "version",
                "--short",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "compose",
                    "version",
                    "--short",
                ),
                exit_code=0,
                stdout="5.3.0\n",
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "info",
                "--format",
                "{{json .}}",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "info",
                    "--format",
                    "{{json .}}",
                ),
                exit_code=0,
                stdout=json.dumps(info),
                stderr="",
            ),
            (
                "docker",
                "--context",
                "desktop-linux",
                "context",
                "inspect",
                "desktop-linux",
                "--format",
                "{{json .}}",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--context",
                    "desktop-linux",
                    "context",
                    "inspect",
                    "desktop-linux",
                    "--format",
                    "{{json .}}",
                ),
                exit_code=0,
                stdout=json.dumps(
                    {
                        "Name": "desktop-linux",
                        "Endpoints": {
                            "docker": {
                                "Host": "unix:///var/run/docker.sock",
                            }
                        },
                    }
                ),
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "desktop",
                "settings",
                "export",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "desktop",
                    "settings",
                    "export",
                ),
                exit_code=0,
                stdout=json.dumps({"resources": {"diskSizeMiB": 100 * 1024}}),
                stderr="",
            ),
        }
    )

    snapshot = collect_docker_snapshot(runner)

    assert snapshot.daemon_available is True
    assert snapshot.server_version == "29.6.1"
    assert snapshot.desktop_identity_verified is True
    assert snapshot.compose_plugin_v2 is True
    assert snapshot.native_platform == "linux/arm64"
    assert snapshot.memory_bytes == 24 * GIB
    assert snapshot.disk_bytes == 100 * GIB
    assert snapshot.resource_fields_verified is True
    assert all(
        "DockerRootDirSize" not in result.stdout for result in runner.results.values()
    )


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("1.0.0\n", None),
        ("v2.29.1\n", 2),
        ("5.3.0\n", 5),
        ("Docker Compose version v2.27.0\n", 2),
        ("garbage\n", None),
    ],
)
def test_compose_plugin_major_version_requires_major_two_or_newer(
    output: str,
    expected: int | None,
) -> None:
    assert compose_plugin_major_version(output) == expected


def test_host_snapshot_is_collected_from_injected_read_only_fixtures() -> None:
    commands = {
        ("sw_vers", "-productVersion"): "26.5.2\n",
        ("sw_vers", "-buildVersion"): "25F84\n",
        ("uname", "-m"): "arm64\n",
        ("sysctl", "-n", "machdep.cpu.brand_string"): "Apple M5 Pro\n",
        ("sysctl", "-n", "hw.logicalcpu"): "12\n",
        ("sysctl", "-n", "hw.memsize"): f"{48 * GIB}\n",
        ("vm_stat",): (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free: 100000.\n"
            "Pages inactive: 50000.\n"
            "Pages speculative: 10000.\n"
        ),
        (
            "df",
            "-Pk",
            ".",
        ): (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            f"/dev/test 1000000000 1000 {679 * 1024 * 1024} 1% /\n"
        ),
    }
    runner = FixtureRunner(
        {
            arguments: CommandResult(
                arguments=arguments,
                exit_code=0,
                stdout=stdout,
                stderr="",
            )
            for arguments, stdout in commands.items()
        }
    )

    snapshot = collect_host_snapshot(runner)

    assert snapshot.macos_version == "26.5.2"
    assert snapshot.macos_build == "25F84"
    assert snapshot.architecture == "arm64"
    assert snapshot.total_memory_bytes == 48 * GIB
    assert snapshot.available_memory_bytes == 160000 * 16384
    assert snapshot.available_disk_bytes == 679 * GIB
    assert all(
        call[0] in {"sw_vers", "uname", "sysctl", "vm_stat", "df"}
        for call in runner.calls
    )


def test_host_collection_fails_closed_when_a_probe_fails() -> None:
    runner = FixtureRunner(
        {
            ("sw_vers", "-productVersion"): CommandResult(
                arguments=("sw_vers", "-productVersion"),
                exit_code=1,
                stdout="",
                stderr="probe failed",
            )
        }
    )

    with pytest.raises(RuntimeError, match="PREFLIGHT_BLOCKED"):
        collect_host_snapshot(runner)


def test_malformed_host_and_docker_values_have_typed_fail_closed_result() -> None:
    host_runner = FixtureRunner(
        {
            ("sw_vers", "-productVersion"): CommandResult(
                arguments=("sw_vers", "-productVersion"),
                exit_code=0,
                stdout="26.5.2\n",
                stderr="",
            ),
            ("sw_vers", "-buildVersion"): CommandResult(
                arguments=("sw_vers", "-buildVersion"),
                exit_code=0,
                stdout="25F84\n",
                stderr="",
            ),
            ("uname", "-m"): CommandResult(
                arguments=("uname", "-m"),
                exit_code=0,
                stdout="arm64\n",
                stderr="",
            ),
            ("sysctl", "-n", "machdep.cpu.brand_string"): CommandResult(
                arguments=("sysctl", "-n", "machdep.cpu.brand_string"),
                exit_code=0,
                stdout="Apple\n",
                stderr="",
            ),
            ("sysctl", "-n", "hw.logicalcpu"): CommandResult(
                arguments=("sysctl", "-n", "hw.logicalcpu"),
                exit_code=0,
                stdout="not-an-int\n",
                stderr="",
            ),
        }
    )
    with pytest.raises(PreflightCollectionError) as host_error:
        collect_host_snapshot(host_runner)
    assert preflight_failure_result(host_error.value) == PreflightResult(
        outcome=Outcome.BLOCKED_ENVIRONMENT,
        exit_code=20,
        reason_codes=("PREFLIGHT_BLOCKED",),
    )

    docker_runner = FixtureRunner(
        {
            (
                "docker",
                "--context",
                "desktop-linux",
                "context",
                "inspect",
                "desktop-linux",
                "--format",
                "{{json .}}",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--context",
                    "desktop-linux",
                    "context",
                    "inspect",
                    "desktop-linux",
                    "--format",
                    "{{json .}}",
                ),
                exit_code=0,
                stdout=json.dumps(
                    {
                        "Name": "desktop-linux",
                        "Endpoints": {
                            "docker": {
                                "Host": "unix:///var/run/docker.sock",
                            }
                        },
                    }
                ),
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "--version",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "--version",
                ),
                exit_code=0,
                stdout="Docker version 29.6.1",
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "compose",
                "version",
                "--short",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "compose",
                    "version",
                    "--short",
                ),
                exit_code=0,
                stdout="v2.29.1",
                stderr="",
            ),
            (
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "info",
                "--format",
                "{{json .}}",
            ): CommandResult(
                arguments=(
                    "docker",
                    "--host",
                    "unix:///var/run/docker.sock",
                    "info",
                    "--format",
                    "{{json .}}",
                ),
                exit_code=0,
                stdout="{malformed",
                stderr="",
            ),
        }
    )
    with pytest.raises(PreflightCollectionError) as docker_error:
        collect_docker_snapshot(docker_runner)
    assert preflight_failure_result(docker_error.value).reason_codes == (
        "PREFLIGHT_BLOCKED",
    )


def test_preflight_result_rejects_wrong_exit_or_empty_failure_reasons() -> None:
    with pytest.raises(ValidationError, match="exit"):
        PreflightResult(
            outcome=Outcome.UNSAFE,
            exit_code=0,
            reason_codes=("RESOURCE_OWNERSHIP_UNKNOWN",),
        )
    with pytest.raises(ValidationError, match="reason"):
        PreflightResult(
            outcome=Outcome.UNSAFE,
            exit_code=40,
            reason_codes=(),
        )
