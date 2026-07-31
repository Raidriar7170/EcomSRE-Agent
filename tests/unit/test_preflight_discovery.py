import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.environment.manifests import (
    InspectedImage,
    UPSTREAM_COMMIT,
)
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnershipManifest,
    OwnedResource,
)
from ecomsre.environment.preflight import (
    CommandResult,
    DiscoveryParseError,
    OwnershipProof,
    PathIdentity,
    PortObservation,
    ProcessIdentity,
    ResourceObservation,
    build_read_only_discovery_plan,
    parse_cached_images,
    parse_canonical_compose_contract_hash,
    parse_docker_resource_listing,
    parse_path_probe,
    parse_port_observation,
    parse_process_listing,
    parse_resolved_compose_config,
    parse_runtime_compose_instance_hash,
    parse_upstream_commit,
)
from ecomsre.evidence.hashes import canonical_json_sha256, sha256_bytes


RUN_ID = "4" * 32
MANIFEST_HASH = "5" * 64
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"


def _result(
    arguments: tuple[str, ...],
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CommandResult:
    return CommandResult(
        arguments=arguments,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def _option_values(arguments: tuple[str, ...], option: str) -> list[str]:
    return [
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == option
    ]


def test_discovery_plan_covers_all_read_only_preflight_surfaces(
    tmp_path: Path,
) -> None:
    project_file = tmp_path / "state" / "ownership.json"
    lock_file = tmp_path / "state" / "phase0.lock"

    plan = build_read_only_discovery_plan(
        project_root=tmp_path,
        ports=(8080, 4318),
        image_references=("otel/demo:3.0.0-adservice",),
        project_paths=(project_file,),
        lock_paths=(lock_file,),
        docker_endpoint=DOCKER_ENDPOINT,
    )

    purposes = {command.purpose for command in plan}
    assert {
        "port",
        "containers",
        "networks",
        "volumes",
        "processes",
        "project_file",
        "lock_file",
        "upstream_commit",
        "compose_config",
        "cached_images",
    }.issubset(purposes)
    forbidden = {"up", "down", "pull", "rm", "stop", "kill", "prune", "start"}
    assert all(forbidden.isdisjoint(command.arguments) for command in plan)
    assert all(command.read_only is True for command in plan)
    compose_command = next(
        command for command in plan if command.purpose == "compose_config"
    )
    assert "--no-interpolate" not in compose_command.arguments
    assert compose_command.arguments[-2:] == ("--format", "json")
    assert compose_command.arguments[:4] == (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "compose",
    )
    assert _option_values(compose_command.arguments, "--project-name") == [
        PROJECT_NAMESPACE
    ]
    assert _option_values(compose_command.arguments, "--file") == [
        str(tmp_path / "third_party" / "opentelemetry-demo" / "compose.yaml"),
        str(
            tmp_path
            / "third_party"
            / "opentelemetry-demo"
            / "compose.observability.yaml"
        ),
        str(tmp_path / "config" / "phase0" / "compose.phase0.yaml"),
    ]
    assert _option_values(compose_command.arguments, "--env-file") == [
        str(tmp_path / "third_party" / "opentelemetry-demo" / ".env")
    ]
    cached_command = next(
        command for command in plan if command.purpose == "cached_images"
    )
    assert cached_command.arguments == (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "image",
        "inspect",
        "--platform",
        "linux/arm64",
        "otel/demo:3.0.0-adservice",
    )


def test_discovery_plan_rejects_paths_outside_project_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="project root"):
        build_read_only_discovery_plan(
            project_root=tmp_path,
            ports=(),
            image_references=(),
            project_paths=(tmp_path.parent / "outside.json",),
            lock_paths=(),
            docker_endpoint=DOCKER_ENDPOINT,
        )


def test_unproven_owned_observation_is_invalid() -> None:
    with pytest.raises(ValidationError, match="proof"):
        PortObservation(port=8080, occupied=True, ownership="OWNED")
    with pytest.raises(ValidationError, match="proof"):
        ResourceObservation(
            kind="container",
            name="ecomsre-phase0-adservice-1",
            resource_id="container-id",
            labels={
                PROJECT_LABEL: PROJECT_NAMESPACE,
                RUN_LABEL: RUN_ID,
            },
            ownership="OWNED",
        )


def test_observation_structure_binds_none_and_owned_proof_to_target() -> None:
    with pytest.raises(ValidationError, match="unoccupied"):
        PortObservation(port=8080, occupied=False, ownership="UNKNOWN")
    with pytest.raises(ValidationError, match="present resource"):
        ResourceObservation(
            kind="container",
            name="ecomsre-phase0-adservice-1",
            resource_id="container-id",
            present=True,
            ownership="NONE",
        )

    wrong_port_proof = OwnershipProof(
        project_namespace=PROJECT_NAMESPACE,
        manifest_sha256=MANIFEST_HASH,
        run_id=RUN_ID,
        resource_kind="port",
        resource_id="tcp:9999",
        port=9999,
        identifiers=(
            "port:tcp:9999",
            "pid:123",
            "start:Thu Jul 30 10:00:00 2026",
            "executable:/usr/bin/python3",
            "socket:*:9999",
        ),
    )
    with pytest.raises(ValidationError, match="port"):
        PortObservation(
            port=8080,
            occupied=True,
            ownership="OWNED",
            ownership_proof=wrong_port_proof,
        )

    wrong_resource_proof = OwnershipProof(
        project_namespace=PROJECT_NAMESPACE,
        manifest_sha256=MANIFEST_HASH,
        run_id=RUN_ID,
        resource_kind="network",
        resource_id="network-id",
        port=None,
        identifiers=("network:network-id",),
    )
    with pytest.raises(ValidationError, match="resource"):
        ResourceObservation(
            kind="container",
            name="ecomsre-phase0-adservice-1",
            resource_id="container-id",
            labels={},
            ownership="OWNED",
            ownership_proof=wrong_resource_proof,
        )

    with pytest.raises(ValidationError, match="identifier"):
        OwnershipProof(
            project_namespace=PROJECT_NAMESPACE,
            manifest_sha256=MANIFEST_HASH,
            run_id=RUN_ID,
            resource_kind="container",
            resource_id="container-id",
            port=None,
            identifiers=("unrelated:value",),
        )


def test_port_parser_marks_only_manifest_proven_process_as_owned() -> None:
    arguments = (
        "lsof",
        "-nP",
        "-F",
        "pcn",
        "-iTCP:8080",
        "-sTCP:LISTEN",
    )
    result = _result(arguments, stdout="p123\ncpython\nn*:8080\n")
    stable = ProcessIdentity(
        pid=123,
        start_time="Thu Jul 30 10:00:00 2026",
        executable="/usr/bin/python3",
    )
    port_identity = (
        "port:tcp:8080",
        "pid:123",
        "start:Thu Jul 30 10:00:00 2026",
        "executable:/usr/bin/python3",
        "socket:*:8080",
    )
    manifest = OwnershipManifest(
        run_id=RUN_ID,
        resources=(
            OwnedResource(
                kind="port",
                name="tcp:8080",
                resource_id="tcp:8080",
                labels={
                    PROJECT_LABEL: PROJECT_NAMESPACE,
                    RUN_LABEL: RUN_ID,
                },
                identity_evidence=port_identity,
            ),
        ),
    )
    manifest_hash = canonical_json_sha256(manifest.model_dump(mode="json"))

    unknown = parse_port_observation(
        result,
        port=8080,
        owned_processes={},
        manifest_sha256=manifest_hash,
        active_run_id=RUN_ID,
    )
    owned = parse_port_observation(
        result,
        port=8080,
        owned_processes={123: stable},
        manifest_sha256=manifest_hash,
        active_run_id=RUN_ID,
        manifest=manifest,
    )

    assert unknown.ownership == "UNKNOWN"
    assert owned.ownership == "OWNED"
    assert owned.ownership_proof is not None
    assert owned.ownership_proof.identifiers == port_identity

    reused_pid = parse_port_observation(
        result,
        port=8080,
        owned_processes={
            123: stable.model_copy(update={"start_time": "Thu Jul 30 11:00:00 2026"})
        },
        manifest_sha256=manifest_hash,
        active_run_id=RUN_ID,
        manifest=manifest,
    )
    assert reused_pid.ownership == "UNKNOWN"


def test_permission_denied_is_never_parsed_as_absent() -> None:
    port_result = _result(
        (
            "lsof",
            "-nP",
            "-F",
            "pcn",
            "-iTCP:8080",
            "-sTCP:LISTEN",
        ),
        exit_code=1,
        stderr="lsof: Permission denied",
    )
    path = Path("/project/phase0.lock")
    path_result = _result(
        ("stat", "-f", "%N|%i|%p", str(path)),
        exit_code=1,
        stderr="stat: Permission denied",
    )

    assert (
        parse_port_observation(
            port_result,
            port=8080,
            owned_processes={},
            manifest_sha256=MANIFEST_HASH,
            active_run_id=RUN_ID,
        ).ownership
        == "UNKNOWN"
    )
    assert (
        parse_path_probe(
            path_result,
            path=path,
            expected_identity=None,
            manifest_sha256=MANIFEST_HASH,
            active_run_id=RUN_ID,
            kind="lock",
        ).ownership
        == "UNKNOWN"
    )


def test_only_explicit_no_match_or_not_found_is_absent() -> None:
    port_result = _result(
        ("lsof", "-nP", "-F", "pcn", "-iTCP:8080", "-sTCP:LISTEN"),
        exit_code=1,
        stdout="",
        stderr="",
    )
    path = Path("/project/missing.lock")
    path_result = _result(
        ("stat", "-f", "%N|%i|%p", str(path)),
        exit_code=1,
        stderr=f"stat: {path}: No such file or directory",
    )

    port = parse_port_observation(
        port_result,
        port=8080,
        owned_processes={},
        manifest_sha256=MANIFEST_HASH,
        active_run_id=RUN_ID,
    )
    missing_path = parse_path_probe(
        path_result,
        path=path,
        expected_identity=None,
        manifest_sha256=MANIFEST_HASH,
        active_run_id=RUN_ID,
        kind="lock",
    )
    assert port.occupied is False and port.ownership == "NONE"
    assert missing_path.ownership == "NONE"


def test_resource_parser_requires_exact_manifest_identity_and_labels() -> None:
    resource = OwnedResource(
        kind="container",
        name="ecomsre-phase0-adservice-1",
        resource_id="container-id",
        labels={
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: RUN_ID,
        },
    )
    manifest = OwnershipManifest(run_id=RUN_ID, resources=(resource,))
    arguments = (
        "docker",
        "container",
        "ls",
        "--all",
        "--format",
        "{{json .}}",
    )
    matching = _result(
        arguments,
        stdout=json.dumps(
            {
                "ID": "container-id",
                "Names": resource.name,
                "Labels": (f"{PROJECT_LABEL}={PROJECT_NAMESPACE},{RUN_LABEL}={RUN_ID}"),
            }
        )
        + "\n",
    )
    mismatched = _result(
        arguments,
        stdout=json.dumps(
            {
                "ID": "other-id",
                "Names": resource.name,
                "Labels": f"{PROJECT_LABEL}={PROJECT_NAMESPACE}",
            }
        )
        + "\n",
    )

    assert (
        parse_docker_resource_listing(
            "container",
            matching,
            manifest,
        )[0].ownership
        == "OWNED"
    )
    assert (
        parse_docker_resource_listing(
            "container",
            mismatched,
            manifest,
        )[0].ownership
        == "UNKNOWN"
    )


def test_project_file_and_process_parsers_fail_closed_without_manifest_proof(
    tmp_path: Path,
) -> None:
    path = tmp_path / "phase0.lock"
    stat_result = _result(
        ("stat", "-f", "%N|%i|%p", str(path)),
        stdout=f"{path}|12345|100600\n",
    )
    process_result = _result(
        ("ps", "-axo", "pid=,command="),
        stdout="123 /usr/bin/python phase0-probe\n",
    )

    path_unknown = parse_path_probe(
        stat_result,
        path=path,
        expected_identity=None,
        manifest_sha256=MANIFEST_HASH,
        active_run_id=RUN_ID,
        kind="lock",
    )
    process_unknown = parse_process_listing(
        process_result,
        expected_processes={},
        manifest_sha256=MANIFEST_HASH,
        active_run_id=RUN_ID,
    )

    assert path_unknown.ownership == "UNKNOWN"
    assert process_unknown[0].ownership == "UNKNOWN"


def test_path_and_process_require_full_stable_identity(tmp_path: Path) -> None:
    path = tmp_path / "phase0.lock"
    canonical = str(path.resolve(strict=False))
    path_identity = PathIdentity(
        canonical_path=canonical,
        device=7,
        inode=12345,
        file_type="Regular File",
        uid=501,
    )
    path_result = _result(
        ("stat", "-f", "%N|%d|%i|%HT|%u|%p", str(path)),
        stdout=f"{canonical}|7|12345|Regular File|501|100600\n",
    )
    path_evidence = (
        "lock:7:12345",
        f"path:{canonical}",
        "device:7",
        "inode:12345",
        "type:Regular File",
        "uid:501",
    )
    path_manifest = OwnershipManifest(
        run_id=RUN_ID,
        resources=(
            OwnedResource(
                kind="lock",
                name=canonical,
                resource_id="7:12345",
                labels={
                    PROJECT_LABEL: PROJECT_NAMESPACE,
                    RUN_LABEL: RUN_ID,
                },
                identity_evidence=path_evidence,
            ),
        ),
    )
    path_manifest_hash = canonical_json_sha256(path_manifest.model_dump(mode="json"))
    owned_path = parse_path_probe(
        path_result,
        path=path,
        expected_identity=path_identity,
        manifest_sha256=path_manifest_hash,
        active_run_id=RUN_ID,
        kind="lock",
        manifest=path_manifest,
    )
    assert owned_path.ownership == "OWNED"
    inode_collision = _result(
        ("stat", "-f", "%N|%d|%i|%HT|%u|%p", str(path)),
        stdout=f"{canonical}|8|12345|Regular File|501|100600\n",
    )
    assert (
        parse_path_probe(
            inode_collision,
            path=path,
            expected_identity=path_identity,
            manifest_sha256=path_manifest_hash,
            active_run_id=RUN_ID,
            kind="lock",
            manifest=path_manifest,
        ).ownership
        == "UNKNOWN"
    )

    expected_process = ProcessIdentity(
        pid=123,
        start_time="Thu Jul 30 10:00:00 2026",
        executable="/usr/bin/python3",
    )
    process_resource_id = "123:Thu Jul 30 10:00:00 2026"
    process_evidence = (
        f"process:{process_resource_id}",
        "pid:123",
        "start:Thu Jul 30 10:00:00 2026",
        "executable:/usr/bin/python3",
    )
    process_manifest = OwnershipManifest(
        run_id=RUN_ID,
        resources=(
            OwnedResource(
                kind="process",
                name="/usr/bin/python3",
                resource_id=process_resource_id,
                labels={
                    PROJECT_LABEL: PROJECT_NAMESPACE,
                    RUN_LABEL: RUN_ID,
                },
                identity_evidence=process_evidence,
            ),
        ),
    )
    process_manifest_hash = canonical_json_sha256(
        process_manifest.model_dump(mode="json")
    )
    exact = _result(
        ("ps", "-axo", "pid=,lstart=,comm="),
        stdout="123 Thu Jul 30 10:00:00 2026 /usr/bin/python3\n",
    )
    reused = _result(
        ("ps", "-axo", "pid=,lstart=,comm="),
        stdout="123 Thu Jul 30 11:00:00 2026 /usr/bin/python3\n",
    )
    substring_only = _result(
        ("ps", "-axo", "pid=,lstart=,comm="),
        stdout=("123 Thu Jul 30 10:00:00 2026 /usr/bin/python3-malicious\n"),
    )
    assert (
        parse_process_listing(
            exact,
            expected_processes={123: expected_process},
            manifest_sha256=process_manifest_hash,
            active_run_id=RUN_ID,
            manifest=process_manifest,
        )[0].ownership
        == "OWNED"
    )
    assert (
        parse_process_listing(
            reused,
            expected_processes={123: expected_process},
            manifest_sha256=process_manifest_hash,
            active_run_id=RUN_ID,
            manifest=process_manifest,
        )[0].ownership
        == "UNKNOWN"
    )
    assert (
        parse_process_listing(
            substring_only,
            expected_processes={123: expected_process},
            manifest_sha256=process_manifest_hash,
            active_run_id=RUN_ID,
            manifest=process_manifest,
        )[0].ownership
        == "UNKNOWN"
    )


def test_upstream_compose_and_cached_image_parsers_preserve_exact_facts() -> None:
    upstream = _result(
        ("git", "-C", "third_party/opentelemetry-demo", "rev-parse", "HEAD"),
        stdout=f"{UPSTREAM_COMMIT}\n",
    )
    compose_content = json.dumps(
        {
            "services": {
                "adservice": {
                    "image": "otel/demo:3.0.0-adservice",
                }
            }
        },
        sort_keys=True,
    )
    compose = _result(
        ("docker", "compose", "config"),
        stdout=compose_content,
    )
    image_payload = [
        {
            "Id": "sha256:" + "a" * 64,
            "RepoTags": ["otel/demo:3.0.0-adservice"],
            "RepoDigests": ["otel/demo@sha256:" + "b" * 64],
            "Descriptor": {"digest": "sha256:" + "c" * 64},
            "Architecture": "arm64",
            "Os": "linux",
        }
    ]
    images = _result(
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            "otel/demo:3.0.0-adservice",
        ),
        stdout=json.dumps(image_payload),
    )

    assert parse_upstream_commit(upstream) == UPSTREAM_COMMIT
    assert parse_runtime_compose_instance_hash(compose) == sha256_bytes(
        compose_content.encode()
    )
    resolved = parse_resolved_compose_config(compose)
    assert parse_canonical_compose_contract_hash(compose) == (
        resolved.canonical_compose_contract_sha256
    )
    assert resolved.stdout == compose_content
    assert resolved.runtime_compose_instance_sha256 == sha256_bytes(
        compose_content.encode()
    )
    assert resolved.image_references == ("otel/demo:3.0.0-adservice",)
    parsed = parse_cached_images(images)
    assert parsed == (
        InspectedImage(
            logical_name="3.0.0-adservice",
            source_reference="otel/demo:3.0.0-adservice",
            image_index_digest="sha256:" + "b" * 64,
            resolved_platform_digest="sha256:" + "c" * 64,
            architecture="arm64",
            platform="linux/arm64",
            image_id="sha256:" + "a" * 64,
        ),
    )


def test_resolved_compose_config_fails_closed_on_missing_variable() -> None:
    result = _result(
        ("docker", "compose", "config"),
        exit_code=1,
        stderr="required variable IMAGE_TAG is missing a value",
    )

    with pytest.raises(DiscoveryParseError, match="Compose"):
        parse_resolved_compose_config(result)


def test_resolved_compose_config_rejects_incomplete_image_inventory() -> None:
    result = _result(
        ("docker", "compose", "config", "--format", "json"),
        stdout=json.dumps({"services": {"adservice": {}}}),
    )

    with pytest.raises(DiscoveryParseError, match="Compose"):
        parse_resolved_compose_config(result)


def test_cached_image_parser_binds_requested_reference_not_repo_tag_order() -> None:
    requested = "otel/demo:3.0.0-adservice"
    result = _result(
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            requested,
        ),
        stdout=json.dumps(
            [
                {
                    "Id": "sha256:" + "a" * 64,
                    "RepoTags": ["alias/demo:9.9.9", requested],
                    "RepoDigests": ["otel/demo@sha256:" + "b" * 64],
                    "Descriptor": {"digest": "sha256:" + "c" * 64},
                    "Architecture": "arm64",
                    "Os": "linux",
                }
            ]
        ),
    )

    parsed = parse_cached_images(result)

    assert parsed[0].source_reference == requested


def test_cached_image_parser_selects_digest_for_requested_repository() -> None:
    requested = "otel/demo:3.0.0-adservice"
    requested_digest = "sha256:" + "b" * 64
    result = _result(
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            requested,
        ),
        stdout=json.dumps(
            [
                {
                    "Id": "sha256:" + "a" * 64,
                    "RepoTags": [requested, "alias/demo:9.9.9"],
                    "RepoDigests": [
                        "alias/demo@sha256:" + "9" * 64,
                        f"otel/demo@{requested_digest}",
                    ],
                    "Descriptor": {"digest": "sha256:" + "c" * 64},
                    "Architecture": "arm64",
                    "Os": "linux",
                }
            ]
        ),
    )

    parsed = parse_cached_images(result)

    assert parsed[0].image_index_digest == requested_digest


def test_cached_image_parser_rejects_missing_requested_repository_digest() -> None:
    requested = "otel/demo:3.0.0-adservice"
    result = _result(
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            requested,
        ),
        stdout=json.dumps(
            [
                {
                    "Id": "sha256:" + "a" * 64,
                    "RepoTags": [requested],
                    "RepoDigests": ["alias/demo@sha256:" + "9" * 64],
                    "Descriptor": {"digest": "sha256:" + "c" * 64},
                    "Architecture": "arm64",
                    "Os": "linux",
                }
            ]
        ),
    )

    with pytest.raises(DiscoveryParseError, match="repository digest"):
        parse_cached_images(result)


@pytest.mark.parametrize(
    "payload",
    [
        [{"Id": "sha256:" + "a" * 64, "RepoTags": [], "RepoDigests": []}],
        [
            {
                "Id": "sha256:" + "a" * 64,
                "RepoTags": ["otel/demo:3.0.0-adservice"],
                "RepoDigests": ["otel/demo@sha256:" + "b" * 64],
                "Architecture": "arm64",
                "Os": "linux",
            }
        ],
    ],
)
def test_cached_image_parser_fails_closed_on_incomplete_metadata(
    payload: list[dict],
) -> None:
    result = _result(
        ("docker", "image", "inspect", "otel/demo:3.0.0-adservice"),
        stdout=json.dumps(payload),
    )

    with pytest.raises(DiscoveryParseError):
        parse_cached_images(result)


def test_cached_image_parser_rejects_unscoped_inspection() -> None:
    source = "otel/demo:3.0.0-adservice"
    result = _result(
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            source,
        ),
        stdout=json.dumps(
            [
                {
                    "Id": "sha256:" + "c" * 64,
                    "RepoTags": [source],
                    "RepoDigests": ["otel/demo@sha256:" + "a" * 64],
                    "Descriptor": {"digest": "sha256:" + "b" * 64},
                    "Architecture": "arm64",
                    "Os": "linux",
                }
            ]
        ),
    )

    with pytest.raises(DiscoveryParseError, match="arguments"):
        parse_cached_images(result)


def test_cached_image_parser_wraps_primitive_digest_as_typed_failure() -> None:
    source = "otel/demo:3.0.0-adservice"
    result = _result(
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            source,
        ),
        stdout=json.dumps(
            [
                {
                    "Id": "sha256:" + "c" * 64,
                    "RepoTags": [source],
                    "RepoDigests": [1],
                    "Descriptor": {"digest": "sha256:" + "b" * 64},
                    "Architecture": "arm64",
                    "Os": "linux",
                }
            ]
        ),
    )

    with pytest.raises(DiscoveryParseError, match="incomplete"):
        parse_cached_images(result)


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        ("missing-descriptor", "incomplete"),
        ("wrong-architecture", "native linux/arm64"),
        ("wrong-os", "native linux/arm64"),
        ("nonzero", "inspection failed"),
    ],
)
def test_cached_image_parser_fails_closed_without_complete_native_platform_facts(
    mutation: str,
    expected_message: str,
) -> None:
    source = "otel/demo:3.0.0-adservice"
    payload = {
        "Id": "sha256:" + "c" * 64,
        "RepoTags": [source],
        "RepoDigests": ["otel/demo@sha256:" + "a" * 64],
        "Descriptor": {"digest": "sha256:" + "b" * 64},
        "Architecture": "arm64",
        "Os": "linux",
    }
    exit_code = 0
    if mutation == "missing-descriptor":
        del payload["Descriptor"]
    elif mutation == "wrong-architecture":
        payload["Architecture"] = "amd64"
    elif mutation == "wrong-os":
        payload["Os"] = "windows"
    else:
        exit_code = 1
    result = _result(
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            source,
        ),
        exit_code=exit_code,
        stdout=json.dumps([payload]) if exit_code == 0 else "",
    )

    with pytest.raises(DiscoveryParseError, match=expected_message):
        parse_cached_images(result)
