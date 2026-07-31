import json
from pathlib import Path

import pytest

from ecomsre.environment.live_preflight import (
    _collect_fixed_port_observations,
    _observations_from_owned,
    _reject_relevant_resource_conflicts,
    _load_optional_ownership,
    collect_upstream_commit,
    collect_fresh_stop_authority,
)
from ecomsre.environment.lifecycle import ExpectedPortBinding
from ecomsre.environment.manifests import ResolvedComposeConfig
from ecomsre.environment.ownership import OwnedResource, OwnershipManifest
from ecomsre.environment.ownership_authority import (
    OwnershipAuthorityError,
    create_ownership_authority_artifacts,
    load_authenticated_ownership_context,
)
from ecomsre.environment.preflight import (
    CommandResult,
    DockerSnapshot,
    HostSnapshot,
)
from ecomsre.evidence.store import ObserverEvidenceStore
from datetime import UTC, datetime


RUN_ID = "8" * 32
COMMIT = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"


class Runner:
    def __init__(self, results: dict[tuple[str, ...], CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments, *, timeout_seconds, environment=None):
        self.calls.append(arguments)
        return self.results[arguments]


def _result(arguments: tuple[str, ...], *, stdout: str = "", exit_code: int = 0):
    return CommandResult(
        arguments=arguments,
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
    )


def test_upstream_commit_is_observed_by_exact_read_only_git_command(
    tmp_path: Path,
) -> None:
    upstream = tmp_path / "third_party" / "opentelemetry-demo"
    arguments = ("git", "-C", str(upstream), "rev-parse", "HEAD")
    runner = Runner({arguments: _result(arguments, stdout=COMMIT + "\n")})

    observed = collect_upstream_commit(
        runner,
        project_root=tmp_path,
        run_id=RUN_ID,
    )

    assert observed == COMMIT
    assert runner.calls == [arguments]


def test_corrupt_or_partial_ownership_authority_is_not_treated_as_missing(
    tmp_path: Path,
) -> None:
    manifest = (
        tmp_path
        / "observer-visible"
        / RUN_ID
        / "resource-ownership.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{corrupt", encoding="utf-8")

    with pytest.raises(OwnershipAuthorityError):
        _load_optional_ownership(tmp_path, RUN_ID)


def test_absent_ownership_authority_is_the_only_optional_state(
    tmp_path: Path,
) -> None:
    assert _load_optional_ownership(tmp_path, RUN_ID) is None


@pytest.mark.parametrize(
    ("scenario", "expected_reason"),
    [
        ("inspect-nonzero", "INPUT_NOT_FROZEN"),
        ("missing-descriptor", "INPUT_NOT_FROZEN"),
        ("index-as-child", "ARM64_DIGEST_MISMATCH"),
    ],
)
def test_cli_fresh_preflight_types_platform_image_failures_without_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
    scenario: str,
    expected_reason: str,
) -> None:
    from ecomsre import cli
    from ecomsre.environment import live_preflight

    source = "grafana/grafana:13.1.0"
    index_digest = "sha256:" + "a" * 64
    child_digest = "sha256:" + "b" * 64
    compose = json.dumps(
        {
            "services": {
                "grafana": {
                    "container_name": "ecomsre-phase0-grafana",
                    "image": source,
                    "platform": "linux/arm64",
                    "ports": [],
                }
            }
        },
        sort_keys=True,
    )
    resolved = ResolvedComposeConfig.from_stdout(compose)
    lock_path = tmp_path / "config" / "phase0" / "image-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.image-lock.v2",
                "status": "LOCKED",
                "upstream_tag": "3.0.0",
                "upstream_commit": COMMIT,
                "canonical_compose_contract_sha256": (
                    resolved.canonical_compose_contract_sha256
                ),
                "compose_canonicalization_schema_version": (
                    resolved.canonicalization_schema_version
                ),
                "created_at": "2026-07-30T08:00:00Z",
                "allowed_source_references": [source],
                "images": [
                    {
                        "logical_name": "13.1.0",
                        "source_reference": source,
                        "image_index_digest": index_digest,
                        "resolved_platform_digest": child_digest,
                        "architecture": "arm64",
                        "platform": "linux/arm64",
                        "image_id": child_digest,
                        "acquired_at": "2026-07-30T08:00:00Z",
                        "upstream_commit": COMMIT,
                        "canonical_compose_contract_sha256": (
                            resolved.canonical_compose_contract_sha256
                        ),
                        "compose_canonicalization_schema_version": (
                            resolved.canonicalization_schema_version
                        ),
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    original_lock = lock_path.read_bytes()

    class PreflightRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, arguments, *, timeout_seconds, environment=None):
            self.calls.append(arguments)
            if arguments[-3:] == ("config", "--format", "json"):
                return _result(arguments, stdout=compose)
            if arguments[3:5] == ("image", "inspect"):
                if scenario == "inspect-nonzero":
                    return _result(arguments, exit_code=1)
                inspected = {
                    "Id": (
                        index_digest
                        if scenario == "index-as-child"
                        else child_digest
                    ),
                    "RepoTags": [source],
                    "RepoDigests": [f"grafana/grafana@{index_digest}"],
                    "Architecture": "arm64",
                    "Os": "linux",
                }
                if scenario != "missing-descriptor":
                    inspected["Descriptor"] = {
                        "digest": (
                            index_digest
                            if scenario == "index-as-child"
                            else child_digest
                        )
                    }
                return _result(arguments, stdout=json.dumps([inspected]))
            raise AssertionError(f"unexpected command: {arguments}")

    runner = PreflightRunner()
    host = HostSnapshot(
        macos_version="26.5.2",
        macos_build="25F84",
        architecture="arm64",
        cpu_model="Apple M5 Pro",
        cpu_count=12,
        total_memory_bytes=48 * 1024**3,
        available_memory_bytes=24 * 1024**3,
        available_disk_bytes=100 * 1024**3,
    )
    docker = DockerSnapshot(
        client_available=True,
        client_version="29.6.1",
        daemon_available=True,
        server_version="29.6.1",
        desktop_version="4.50.0",
        engine="Docker Desktop",
        desktop_identity_verified=True,
        compose_available=True,
        compose_version="5.3.0",
        compose_plugin_v2=True,
        server_os_type="linux",
        server_architecture="arm64",
        native_platform="linux/arm64",
        cpu_count=12,
        memory_bytes=24 * 1024**3,
        disk_bytes=100 * 1024**3,
        resource_fields_verified=True,
        context_name="desktop-linux",
        endpoint=DOCKER_ENDPOINT,
        daemon_id="fixture-daemon",
    )
    monkeypatch.setattr(
        live_preflight,
        "collect_host_snapshot",
        lambda _runner: host,
    )
    monkeypatch.setattr(
        live_preflight,
        "collect_docker_snapshot",
        lambda _runner: docker,
    )
    monkeypatch.setattr(
        live_preflight,
        "collect_upstream_commit",
        lambda *_args, **_kwargs: COMMIT,
    )
    monkeypatch.setattr(
        live_preflight,
        "_discover_verified_resources",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        live_preflight,
        "_reject_relevant_resource_conflicts",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        live_preflight,
        "_collect_fixed_port_observations",
        lambda *_args, **_kwargs: (),
    )

    exit_code = cli.main(
        ["phase0", "preflight", "--run-id", RUN_ID],
        runner=runner,
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert exit_code == 21
    assert payload["outcome"] == "BLOCKED_UPSTREAM"
    assert payload["reason_code"] == expected_reason
    assert output.err == ""
    assert lock_path.read_bytes() == original_lock
    inspect_call = next(
        arguments
        for arguments in runner.calls
        if arguments[3:5] == ("image", "inspect")
    )
    assert inspect_call[5:7] == ("--platform", "linux/arm64")
    forbidden = {"up", "down", "pull", "rm", "stop", "kill", "prune"}
    assert all(forbidden.isdisjoint(arguments) for arguments in runner.calls)


def test_fixed_port_discovery_marks_occupied_unproven_listener_unknown() -> None:
    binding = ExpectedPortBinding(
        service="prometheus",
        container_name="ecomsre-phase0-prometheus",
        target_port=9090,
        protocol="tcp",
        published_port=9090,
        host_ip="127.0.0.1",
    )
    arguments = (
        "lsof",
        "-nP",
        "-F",
        "pcn",
        "-iTCP:9090",
        "-sTCP:LISTEN",
    )
    runner = Runner(
        {
            arguments: _result(
                arguments,
                stdout="p4242\ncpython\nn127.0.0.1:9090\n",
            )
        }
    )

    observations = _collect_fixed_port_observations(
        runner,
        bindings=(binding,),
        ownership=None,
        discovered=(),
        run_id=RUN_ID,
    )

    assert len(observations) == 1
    assert observations[0].port == 9090
    assert observations[0].occupied is True
    assert observations[0].ownership == "UNKNOWN"


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
def test_relevant_name_conflict_without_project_labels_fails_closed(
    kind: str,
) -> None:
    commands = {
        resource_kind: (
            "docker",
            "--host",
            DOCKER_ENDPOINT,
            resource_kind,
            "ls",
            *(("--all", "--no-trunc") if resource_kind == "container" else ()),
            *(("--no-trunc",) if resource_kind == "network" else ()),
            "--format",
            "{{json .}}",
        )
        for resource_kind in ("container", "network", "volume")
    }
    results = {}
    for resource_kind, arguments in commands.items():
        payload = ""
        if resource_kind == kind:
            payload = json.dumps(
                {
                    "ID": "foreign-id",
                    "Names" if kind == "container" else "Name": (
                        "ecomsre-phase0-conflict"
                    ),
                    "Labels": "",
                }
            )
        results[arguments] = _result(arguments, stdout=payload)
    runner = Runner(results)

    with pytest.raises(OwnershipAuthorityError, match="conflict"):
        _reject_relevant_resource_conflicts(
            runner,
            docker_endpoint=DOCKER_ENDPOINT,
            run_id=RUN_ID,
            discovered=(),
        )


def test_fresh_stop_authority_checks_only_daemon_identity_and_exact_resources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = OwnershipManifest(run_id=RUN_ID, resources=())
    create_ownership_authority_artifacts(
        tmp_path,
        manifest,
        created_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
    )
    ownership = load_authenticated_ownership_context(tmp_path, RUN_ID)
    info = (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "info",
        "--format",
        "{{json .ID}}",
    )
    runner = Runner(
        {info: _result(info, stdout=json.dumps("fixture-daemon") + "\n")}
    )
    monkeypatch.setattr(
        "ecomsre.environment.live_preflight._discover_verified_resources",
        lambda *_args, **_kwargs: (),
    )

    authority = collect_fresh_stop_authority(
        project_root=tmp_path,
        artifacts_root=tmp_path,
        runner=runner,
        ownership=ownership,
        expected_docker_endpoint=DOCKER_ENDPOINT,
        expected_daemon_id="fixture-daemon",
    )

    assert authority.is_authentic(ownership)
    assert authority.docker_endpoint == DOCKER_ENDPOINT
    assert runner.calls == [info]


def test_fresh_stop_authority_survives_observer_persistence_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = OwnershipManifest(run_id=RUN_ID, resources=())
    create_ownership_authority_artifacts(
        tmp_path,
        manifest,
        created_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
    )
    ownership = load_authenticated_ownership_context(tmp_path, RUN_ID)
    info = (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "info",
        "--format",
        "{{json .ID}}",
    )
    runner = Runner(
        {info: _result(info, stdout=json.dumps("fixture-daemon") + "\n")}
    )
    monkeypatch.setattr(
        "ecomsre.environment.live_preflight._discover_verified_resources",
        lambda *_args, **_kwargs: (),
    )

    def fail_write(*_args, **_kwargs):
        raise OSError("observer disk fixture")

    monkeypatch.setattr(ObserverEvidenceStore, "write_immutable", fail_write)

    authority = collect_fresh_stop_authority(
        project_root=tmp_path,
        artifacts_root=tmp_path,
        runner=runner,
        ownership=ownership,
        expected_docker_endpoint=DOCKER_ENDPOINT,
        expected_daemon_id="fixture-daemon",
    )

    assert authority.is_authentic(ownership)
    assert authority.evidence_artifact is None
    assert authority.evidence_sha256 is None
    assert authority.evidence_persistence_error == "OBSERVER_PERSISTENCE_FAILED"


def test_observer_owned_resource_evidence_omits_private_bind_source(
    tmp_path: Path,
) -> None:
    resource = OwnedResource(
        kind="container",
        name="ecomsre-phase0-flagd",
        resource_id="container-id",
        labels={
            "com.docker.compose.project": "ecomsre-phase0",
            "com.docker.compose.service": "flagd",
            "desktop.docker.io/binds/0/Source": (
                "/workspace/artifacts/phase0/evaluator-only/"
                f"{RUN_ID}/control"
            ),
            "io.ecomsre.project": "ecomsre-phase0",
            "io.ecomsre.run": RUN_ID,
        },
        identity_evidence=(
            "container:container-id",
            "container_name:ecomsre-phase0-flagd",
            "service:flagd",
        ),
    )
    manifest = OwnershipManifest(run_id=RUN_ID, resources=(resource,))
    create_ownership_authority_artifacts(
        tmp_path,
        manifest,
        created_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
    )
    ownership = load_authenticated_ownership_context(
        tmp_path,
        RUN_ID,
    )

    _ports, resources = _observations_from_owned(ownership, (resource,))

    assert len(resources) == 1
    assert resources[0].labels == {
        "com.docker.compose.project": "ecomsre-phase0",
        "com.docker.compose.service": "flagd",
        "io.ecomsre.project": "ecomsre-phase0",
        "io.ecomsre.run": RUN_ID,
    }
    serialized = json.dumps(resources[0].model_dump(mode="json")).casefold()
    assert "evaluator-only" not in serialized
    assert "/control" not in serialized


def test_fresh_stop_authority_omits_private_bind_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resource = OwnedResource(
        kind="container",
        name="ecomsre-phase0-flagd",
        resource_id="container-id",
        labels={
            "com.docker.compose.project": "ecomsre-phase0",
            "com.docker.compose.service": "flagd",
            "desktop.docker.io/binds/0/Source": (
                str(tmp_path)
                + "/evaluator-only/"
                + RUN_ID
                + "/control"
            ),
            "io.ecomsre.project": "ecomsre-phase0",
            "io.ecomsre.run": RUN_ID,
        },
        identity_evidence=(
            "container:container-id",
            "container_name:ecomsre-phase0-flagd",
            "service:flagd",
        ),
    )
    manifest = OwnershipManifest(run_id=RUN_ID, resources=(resource,))
    create_ownership_authority_artifacts(
        tmp_path,
        manifest,
        created_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
    )
    ownership = load_authenticated_ownership_context(tmp_path, RUN_ID)
    info = (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "info",
        "--format",
        "{{json .ID}}",
    )
    runner = Runner(
        {info: _result(info, stdout=json.dumps("fixture-daemon") + "\n")}
    )
    monkeypatch.setattr(
        "ecomsre.environment.live_preflight._discover_verified_resources",
        lambda *_args, **_kwargs: (resource,),
    )

    authority = collect_fresh_stop_authority(
        project_root=tmp_path,
        artifacts_root=tmp_path,
        runner=runner,
        ownership=ownership,
        expected_docker_endpoint=DOCKER_ENDPOINT,
        expected_daemon_id="fixture-daemon",
    )

    evidence = json.loads(Path(authority.evidence_artifact).read_text())
    serialized = json.dumps(evidence).casefold()
    assert "evaluator-only" not in serialized
    assert "/control" not in serialized
    assert evidence["resources"][0]["labels"] == {
        "com.docker.compose.project": "ecomsre-phase0",
        "com.docker.compose.service": "flagd",
        "io.ecomsre.project": "ecomsre-phase0",
        "io.ecomsre.run": RUN_ID,
    }


def test_fresh_stop_authority_fails_closed_on_daemon_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = OwnershipManifest(run_id=RUN_ID, resources=())
    create_ownership_authority_artifacts(
        tmp_path,
        manifest,
        created_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
    )
    ownership = load_authenticated_ownership_context(tmp_path, RUN_ID)
    info = (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "info",
        "--format",
        "{{json .ID}}",
    )
    runner = Runner({info: _result(info, stdout=json.dumps("other-daemon"))})
    monkeypatch.setattr(
        "ecomsre.environment.live_preflight._discover_verified_resources",
        lambda *_args, **_kwargs: (),
    )

    with pytest.raises(OwnershipAuthorityError, match="daemon"):
        collect_fresh_stop_authority(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            runner=runner,
            ownership=ownership,
            expected_docker_endpoint=DOCKER_ENDPOINT,
            expected_daemon_id="fixture-daemon",
        )


def test_fresh_stop_authority_resource_drift_still_fails_when_observer_is_broken(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = OwnershipManifest(run_id=RUN_ID, resources=())
    create_ownership_authority_artifacts(
        tmp_path,
        manifest,
        created_at=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
    )
    ownership = load_authenticated_ownership_context(tmp_path, RUN_ID)
    info = (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "info",
        "--format",
        "{{json .ID}}",
    )
    runner = Runner(
        {info: _result(info, stdout=json.dumps("fixture-daemon") + "\n")}
    )
    changed = OwnedResource(
        kind="network",
        name="ecomsre-phase0",
        resource_id="changed-network",
        labels={
            "com.docker.compose.project": "ecomsre-phase0",
            "io.ecomsre.project": "ecomsre-phase0",
            "io.ecomsre.run": RUN_ID,
        },
        identity_evidence=("network:changed-network",),
    )
    monkeypatch.setattr(
        "ecomsre.environment.live_preflight._discover_verified_resources",
        lambda *_args, **_kwargs: (changed,),
    )
    monkeypatch.setattr(
        ObserverEvidenceStore,
        "write_immutable",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("observer disk fixture")
        ),
    )

    with pytest.raises(OwnershipAuthorityError, match="resource identity"):
        collect_fresh_stop_authority(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            runner=runner,
            ownership=ownership,
            expected_docker_endpoint=DOCKER_ENDPOINT,
            expected_daemon_id="fixture-daemon",
        )
