import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.environment.bootstrap import (
    Arm64ManifestUnavailable,
    bootstrap_image_lock,
    parse_arm64_manifest,
)
from ecomsre.environment.manifests import InspectedImage
from ecomsre.environment.preflight import CommandResult
from ecomsre.evidence.hashes import (
    canonical_json_sha256,
    sha256_bytes,
    sha256_file,
)
from ecomsre.phase0.models import Outcome


ARM_DIGEST = "sha256:" + "a" * 64
AMD_DIGEST = "sha256:" + "b" * 64
SCUTIL_DIRECT = "<dictionary> {\n}\n"
SCUTIL_LOOPBACK = """<dictionary> {
  HTTPEnable : 1
  HTTPPort : 1097
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : 1097
  HTTPSProxy : 127.0.0.1
}
"""
SCUTIL_LOOPBACK_WITH_SOCKS = SCUTIL_LOOPBACK.replace(
    "}\n",
    """  SOCKSEnable : 1
  SOCKSPort : 1097
  SOCKSProxy : 127.0.0.1
}
""",
)


def _with_fake_command_log(
    result: CommandResult,
    arguments: tuple[str, ...],
    *,
    artifacts_root: Path,
    run_id: str,
    marker: str = "",
) -> CommandResult:
    artifact_id = sha256_bytes(
        "\0".join((*arguments, marker)).encode()
    )
    evaluator_commands = artifacts_root / "evaluator-only" / run_id / "commands"
    observer_commands = (
        artifacts_root / "observer-visible" / run_id / "commands"
    )
    evaluator_commands.mkdir(parents=True, exist_ok=True)
    observer_commands.mkdir(parents=True, exist_ok=True)
    stdout_hash = sha256_bytes(result.stdout.encode())
    stderr_hash = sha256_bytes(result.stderr.encode())
    stdout_relative = f"commands/{artifact_id}.stdout.json"
    stderr_relative = f"commands/{artifact_id}.stderr.json"
    stdout_path = evaluator_commands / f"{artifact_id}.stdout.json"
    stderr_path = evaluator_commands / f"{artifact_id}.stderr.json"
    command_path = observer_commands / f"{artifact_id}.command-log.json"
    stdout_path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.command-stream.v1",
                "stream": "stdout",
                "encoding": "utf-8",
                "content": result.stdout,
                "content_sha256": stdout_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    stderr_path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.command-stream.v1",
                "stream": "stderr",
                "encoding": "utf-8",
                "content": result.stderr,
                "content_sha256": stderr_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    process_exit_code = (
        None
        if result.process_timed_out
        else (
            result.process_exit_code
            if result.process_exit_code is not None
            else result.exit_code
        )
    )
    if result.process_timed_out:
        classification = "BLOCKED_ENVIRONMENT"
        terminal_exit_code = 20
        reason_code = "PROCESS_TIMEOUT"
    elif result.exit_code == 0:
        classification = "SUCCESS"
        terminal_exit_code = 0
        reason_code = "PROCESS_EXIT_ZERO"
    else:
        classification = "BLOCKED_UPSTREAM"
        terminal_exit_code = 21
        reason_code = "UPSTREAM_COMMAND_FAILED"
    command_path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.command-log.v2",
                "run_id": run_id,
                "command": Path(arguments[0]).name,
                "arguments": list(arguments),
                "working_directory": str(artifacts_root.parent),
                "started_at": "2026-07-30T08:00:00Z",
                "ended_at": "2026-07-30T08:00:01Z",
                "monotonic_started_seconds": 1.0,
                "monotonic_ended_seconds": 2.0,
                "timeout_seconds": 120.0,
                "process_exit_code": process_exit_code,
                "process_timed_out": result.process_timed_out,
                "classification": classification,
                "terminal_exit_code": terminal_exit_code,
                "reason_code": reason_code,
                "stdout_artifact": stdout_relative,
                "stdout_sha256": stdout_hash,
                "stderr_artifact": stderr_relative,
                "stderr_sha256": stderr_hash,
                "network_access_declared": True,
                "network_access_scope": "EXTERNAL_REGISTRY",
                "filesystem_write_scope": ["NOT_OBSERVED"],
                "observed_effect_scope": ["NOT_OBSERVED"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return result.model_copy(
        update={
            "exit_code": terminal_exit_code,
            "process_exit_code": process_exit_code,
            "stdout_artifact": str(stdout_path),
            "stdout_sha256": stdout_hash,
            "stderr_artifact": str(stderr_path),
            "stderr_sha256": stderr_hash,
            "command_log_artifact": str(command_path),
            "command_log_sha256": sha256_file(command_path),
        }
    )


def _evidenced_result(
    *,
    artifacts_root: Path,
    run_id: str,
    arguments: tuple[str, ...],
    stdout: str,
) -> CommandResult:
    raw = (
        artifacts_root
        / "evaluator-only"
        / run_id
        / "commands"
        / "compose.stdout.json"
    )
    command = (
        artifacts_root
        / "observer-visible"
        / run_id
        / "commands"
        / "compose.command-log.json"
    )
    raw.parent.mkdir(parents=True, exist_ok=True)
    command.parent.mkdir(parents=True, exist_ok=True)
    content_sha256 = sha256_bytes(stdout.encode())
    raw.write_text(
        json.dumps(
            {
                "schema_version": "phase0.command-stream.v1",
                "stream": "stdout",
                "encoding": "utf-8",
                "content": stdout,
                "content_sha256": content_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    command.write_text('{"classification":"SUCCESS"}', encoding="utf-8")
    return CommandResult(
        arguments=arguments,
        exit_code=0,
        stdout=stdout,
        stderr="",
        stdout_artifact=str(raw),
        stdout_sha256=content_sha256,
        command_log_artifact=str(command),
        command_log_sha256=sha256_file(command),
    )


def test_parse_registry_index_selects_one_native_arm64_manifest() -> None:
    raw = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": AMD_DIGEST,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
                {
                    "digest": ARM_DIGEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
            ],
        },
        separators=(",", ":"),
    )

    parsed = parse_arm64_manifest(raw)

    assert parsed.image_index_digest == "sha256:" + sha256_bytes(raw.encode())
    assert parsed.resolved_platform_digest == ARM_DIGEST


def test_parse_registry_index_accepts_arm64_v8_and_ignores_cli_newline() -> None:
    raw_without_cli_newline = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": ARM_DIGEST,
                    "platform": {
                        "os": "linux",
                        "architecture": "arm64",
                        "variant": "v8",
                    },
                }
            ],
        },
        separators=(",", ":"),
    )

    parsed = parse_arm64_manifest(raw_without_cli_newline + "\n")

    assert parsed.image_index_digest == "sha256:" + sha256_bytes(
        raw_without_cli_newline.encode()
    )
    assert parsed.resolved_platform_digest == ARM_DIGEST


def test_parse_single_manifest_requires_independent_local_arm64_proof() -> None:
    raw = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": "sha256:" + "c" * 64},
            "layers": [],
        },
        separators=(",", ":"),
    )
    expected = "sha256:" + sha256_bytes(raw.encode())
    local = InspectedImage(
        logical_name="ad",
        source_reference="otel/demo:3.0.0-ad",
        image_index_digest=expected,
        resolved_platform_digest=expected,
        architecture="arm64",
        platform="linux/arm64",
        image_id="sha256:" + "d" * 64,
    )

    with pytest.raises(Arm64ManifestUnavailable, match="local"):
        parse_arm64_manifest(raw)
    parsed = parse_arm64_manifest(raw, local_image=local)
    assert parsed.image_index_digest == expected
    assert parsed.resolved_platform_digest == expected


@pytest.mark.parametrize(
    "manifests",
    [
        [{"digest": AMD_DIGEST, "platform": {"os": "linux", "architecture": "amd64"}}],
        [
            {"digest": ARM_DIGEST, "platform": {"os": "linux", "architecture": "arm64"}},
            {"digest": ARM_DIGEST, "platform": {"os": "linux", "architecture": "arm64"}},
        ],
    ],
)
def test_parse_registry_index_fails_closed_without_one_arm64_manifest(
    manifests,
) -> None:
    raw = json.dumps({"schemaVersion": 2, "manifests": manifests})

    with pytest.raises(Arm64ManifestUnavailable):
        parse_arm64_manifest(raw)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            'ERROR: failed to do request: Head "https://registry/v2/": EOF',
            "EOF",
        ),
        ("ERROR: failed to copy: unexpected EOF", "UNEXPECTED_EOF"),
        ("dial tcp: i/o timeout", "IO_TIMEOUT"),
        ("dial tcp: connect timeout", None),
        ("dial tcp: connection refused", None),
        ("read tcp: connection reset by peer", "CONNECTION_RESET"),
        ("net/http: TLS handshake timeout", "TLS_HANDSHAKE_TIMEOUT"),
        ("temporary failure in name resolution", "TEMPORARY_DNS_FAILURE"),
        ("unexpected status code 429", "HTTP_429"),
        ("503 Service Unavailable", "HTTP_503"),
        ("denied: requested access", None),
        ("unauthorized: authentication required", None),
        ("manifest unknown", None),
        ("not found", None),
        ("x509: certificate signed by unknown authority: i/o timeout", None),
        ("denied: requested access: i/o timeout", None),
        ("unexpected EOF while parsing local metadata", None),
        ("EOF", None),
    ],
)
def test_registry_retry_classifier_accepts_only_strict_transient_terminal_causes(
    message: str,
    expected: str | None,
) -> None:
    from ecomsre.environment import bootstrap

    assert bootstrap._transient_registry_failure_category(message) == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            CommandResult(
                arguments=("docker", "pull", "image"),
                exit_code=124,
                stdout="",
                stderr="ERROR: TLS handshake timeout",
                process_timed_out=True,
            ),
            ("PROCESS_TIMEOUT", False),
        ),
        (
            CommandResult(
                arguments=("docker", "pull", "image"),
                exit_code=70,
                stdout="",
                stderr="OSError: executable unavailable",
            ),
            ("PROCESS_START_FAILED", False),
        ),
        (
            CommandResult(
                arguments=("docker", "pull", "image"),
                exit_code=1,
                stdout="",
                stderr="",
                process_exit_code=1,
            ),
            ("EMPTY_FAILURE", False),
        ),
        (
            CommandResult(
                arguments=("docker", "pull", "image"),
                exit_code=1,
                stdout="",
                stderr="dial tcp: connection refused",
                process_exit_code=1,
            ),
            ("UNKNOWN_FAILURE", False),
        ),
        (
            CommandResult(
                arguments=("docker", "pull", "image"),
                exit_code=1,
                stdout=(
                    "ERROR: failed to do request: "
                    'Head "https://registry/v2/": EOF'
                ),
                stderr="",
                process_exit_code=1,
            ),
            ("UNKNOWN_FAILURE", False),
        ),
        (
            CommandResult(
                arguments=("docker", "pull", "image"),
                exit_code=1,
                stdout="ERROR: failed to copy: unexpected EOF",
                stderr="denied: requested access",
                process_exit_code=1,
            ),
            ("AUTHORIZATION_OR_AUTHENTICATION_FAILURE", False),
        ),
        (
            CommandResult(
                arguments=("docker", "pull", "image"),
                exit_code=1,
                stdout="denied: requested access",
                stderr=(
                    "ERROR: failed to do request: "
                    'Head "https://registry/v2/": EOF'
                ),
                process_exit_code=1,
            ),
            ("AUTHORIZATION_OR_AUTHENTICATION_FAILURE", False),
        ),
    ],
)
def test_registry_retry_result_disposition_fails_closed(
    result: CommandResult,
    expected: tuple[str, bool],
) -> None:
    from ecomsre.environment import bootstrap

    assert bootstrap._registry_result_disposition(result) == expected


def test_registry_retry_rejects_tampered_stream_evidence(tmp_path: Path) -> None:
    from ecomsre.environment import bootstrap

    run_id = "c" * 32
    arguments = ("docker", "pull", "otel/demo:3.0.0-ad")
    result = _with_fake_command_log(
        CommandResult(
            arguments=arguments,
            exit_code=1,
            stdout="",
            stderr="ERROR: failed to copy: unexpected EOF",
        ),
        arguments,
        artifacts_root=tmp_path / "artifacts",
        run_id=run_id,
    )
    assert result.stderr_artifact is not None
    Path(result.stderr_artifact).write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="stream evidence differs"):
        bootstrap._validate_registry_command_evidence(
            result,
            artifacts_root=tmp_path / "artifacts",
            run_id=run_id,
        )


def test_registry_retry_rejects_incomplete_command_log(tmp_path: Path) -> None:
    from ecomsre.environment import bootstrap

    run_id = "b" * 32
    arguments = ("docker", "pull", "otel/demo:3.0.0-ad")
    result = _with_fake_command_log(
        CommandResult(
            arguments=arguments,
            exit_code=1,
            stdout="",
            stderr="ERROR: failed to copy: unexpected EOF",
        ),
        arguments,
        artifacts_root=tmp_path / "artifacts",
        run_id=run_id,
    )
    assert result.command_log_artifact is not None
    command_path = Path(result.command_log_artifact)
    command_payload = json.loads(command_path.read_text(encoding="utf-8"))
    command_payload.pop("command")
    command_path.write_text(
        json.dumps(command_payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    result = result.model_copy(
        update={"command_log_sha256": sha256_file(command_path)}
    )

    with pytest.raises(ValueError, match="command log evidence"):
        bootstrap._validate_registry_command_evidence(
            result,
            artifacts_root=tmp_path / "artifacts",
            run_id=run_id,
        )


def test_registry_retry_does_not_cross_frozen_operation_deadline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre.environment import bootstrap
    from ecomsre.evidence.store import ObserverEvidenceStore

    run_id = "d" * 32
    arguments = ("docker", "pull", "otel/demo:3.0.0-ad")
    calls: list[float] = []
    sleeps: list[float] = []
    clock = iter((0.0, 0.0, 129.5))

    def execute(timeout_seconds: float) -> CommandResult:
        calls.append(timeout_seconds)
        return _with_fake_command_log(
            CommandResult(
                arguments=arguments,
                exit_code=1,
                stdout="",
                stderr="ERROR: failed to copy: unexpected EOF",
            ),
            arguments,
            artifacts_root=tmp_path / "artifacts",
            run_id=run_id,
            marker="deadline-attempt-1",
        )

    monkeypatch.setattr(bootstrap, "_retry_monotonic", lambda: next(clock))
    monkeypatch.setattr(bootstrap, "_retry_sleep", sleeps.append)
    with ObserverEvidenceStore(tmp_path / "artifacts", run_id) as store:
        with pytest.raises(bootstrap.UpstreamCommandFailed):
            bootstrap._run_registry_command_with_retry(
                execute=execute,
                store=store,
                artifacts_root=tmp_path / "artifacts",
                run_id=run_id,
                source_index=0,
                source_reference="otel/demo:3.0.0-ad",
                operation="pull",
                deadline_seconds=130.0,
                per_attempt_timeout_seconds=120.0,
            )

    assert calls == [120.0]
    assert sleeps == []
    summary_path = (
        tmp_path
        / "artifacts"
        / "observer-visible"
        / run_id
        / "inputs"
        / "bootstrap"
        / "000-pull-retry-summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["final_status"] == "DEADLINE_EXHAUSTED"
    assert summary["attempt_count"] == 1
    assert summary["attempts_started"] == 1
    assert summary["complete_attempt_count"] == 1
    assert summary["failed_ordinal"] is None
    assert summary["evidence_complete"] is True
    assert summary["exhausted"] is False


def test_bootstrap_resolves_pulls_inspects_and_publishes_candidate_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre.environment import bootstrap as bootstrap_module

    retry_sleeps: list[float] = []
    monkeypatch.setattr(
        bootstrap_module,
        "_retry_sleep",
        retry_sleeps.append,
    )
    project = tmp_path / "project"
    lock_path = project / "config" / "phase0" / "image-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.image-lock.v1",
                "status": "UNINITIALIZED",
                "upstream_tag": "3.0.0",
                "upstream_commit": "1755859a9de82c2e5e225be68abc401a5ebf2b4f",
                "compose_config_sha256": None,
                "created_at": None,
                "allowed_source_references": [],
                "images": [],
            }
        ),
        encoding="utf-8",
    )
    source = "otel/demo:3.0.0-ad"
    second_source = "grafana/grafana:13.1.0"
    compose = json.dumps(
        {
            "services": {
                "ad": {
                    "container_name": "ecomsre-phase0-ad",
                    "image": source,
                    "platform": "linux/arm64",
                    "ports": [],
                },
                "grafana": {
                    "container_name": "ecomsre-phase0-grafana",
                    "image": second_source,
                    "platform": "linux/arm64",
                    "ports": [],
                },
            }
        },
        sort_keys=True,
    )
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": ARM_DIGEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                }
            ],
        },
        separators=(",", ":"),
    )
    registry_index_digest = "sha256:" + sha256_bytes(manifest.encode())

    class Runner:
        def __init__(self):
            self.calls = []
            self.environments = []
            self.registry_routes = []
            self.manifest_attempts = {}
            self.pull_attempts = {}

        def run(self, arguments, *, timeout_seconds, environment=None):
            self.calls.append(arguments)
            self.environments.append((arguments, environment))
            if arguments[-3:] == ("config", "--format", "json"):
                stdout = compose
            elif arguments == ("/usr/sbin/scutil", "--proxy"):
                stdout = SCUTIL_LOOPBACK_WITH_SOCKS
            elif (
                arguments[3:6] == ("buildx", "imagetools", "inspect")
                and arguments[6] in {source, second_source}
            ):
                inspected_source = arguments[6]
                attempt = self.manifest_attempts.get(inspected_source, 0) + 1
                self.manifest_attempts[inspected_source] = attempt
                if inspected_source == source and attempt == 1:
                    return CommandResult(
                        arguments=arguments,
                        exit_code=1,
                        stdout="",
                        stderr=(
                            "ERROR: failed to do request: unexpected EOF"
                        ),
                    )
                stdout = manifest
            elif arguments[3:5] == ("pull", "--platform"):
                pulled_source = arguments[-1]
                attempt = self.pull_attempts.get(pulled_source, 0) + 1
                self.pull_attempts[pulled_source] = attempt
                if pulled_source == source and attempt == 1:
                    result = CommandResult(
                        arguments=arguments,
                        exit_code=1,
                        stdout="",
                        stderr="ERROR: connection reset by peer",
                    )
                else:
                    result = CommandResult(
                        arguments=arguments,
                        exit_code=0,
                        stdout="",
                        stderr="",
                    )
                return _with_fake_command_log(
                    result,
                    arguments,
                    artifacts_root=tmp_path / "artifacts",
                    run_id="e" * 32,
                    marker=f"pull-{pulled_source}-{attempt}",
                )
            elif arguments[3:5] == ("image", "inspect"):
                inspected_source = arguments[-1]
                repository = inspected_source.rsplit(":", 1)[0]
                stdout = json.dumps(
                    [
                        {
                            "RepoTags": [inspected_source],
                            "RepoDigests": [
                                f"{repository}@{registry_index_digest}"
                            ],
                            "Descriptor": {"digest": ARM_DIGEST},
                            "Architecture": "arm64",
                            "Os": "linux",
                            "Id": (
                                "sha256:"
                                + (
                                    "d"
                                    if inspected_source == source
                                    else "e"
                                )
                                * 64
                            ),
                        }
                    ]
                )
            else:
                stdout = ""
            if arguments[-3:] == ("config", "--format", "json"):
                return _evidenced_result(
                    artifacts_root=tmp_path / "artifacts",
                    run_id="e" * 32,
                    arguments=arguments,
                    stdout=stdout,
                )
            return CommandResult(
                arguments=arguments,
                exit_code=0,
                stdout=stdout,
                stderr="",
            )

        def run_registry_inspect(
            self,
            arguments,
            *,
            timeout_seconds,
            route,
        ):
            self.registry_routes.append(route)
            self.environments.append(
                (
                    arguments,
                    {
                        "ECOMSRE_RUN_ID": route.run_id,
                        **dict(route.proxy_environment),
                    },
                )
            )
            return _with_fake_command_log(
                self.run(
                    arguments,
                    timeout_seconds=timeout_seconds,
                    environment=None,
                ),
                arguments,
                artifacts_root=tmp_path / "artifacts",
                run_id="e" * 32,
                marker=f"manifest-{len(self.registry_routes)}",
            )

    runner = Runner()
    lock = bootstrap_image_lock(
        project_root=project,
        artifacts_root=tmp_path / "artifacts",
        run_id="e" * 32,
        runner=runner,
        docker_endpoint="unix:///var/run/docker.sock",
    )

    assert lock.status.value == "LOCKED"
    assert lock.images[0].image_index_digest == "sha256:" + sha256_bytes(
        manifest.encode()
    )
    assert lock.images[0].resolved_platform_digest == ARM_DIGEST
    assert len(lock.images) == 2
    assert any(call[3:5] == ("pull", "--platform") for call in runner.calls)
    inspect_calls = [
        call for call in runner.calls if call[3:5] == ("image", "inspect")
    ]
    assert set(inspect_calls) == {
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
        (
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "image",
            "inspect",
            "--platform",
            "linux/arm64",
            second_source,
        ),
    }
    selections = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / "inputs"
                / "bootstrap"
            ).glob("*-manifest-selection.json")
        )
    ]
    assert len(selections) == 2
    assert all(
        selection["local_resolved_platform_digest"] == ARM_DIGEST
        and selection["local_resolved_platform_digest_source"]
        == "docker_image_inspect_platform_descriptor"
        and selection["registry_local_cross_binding_verified"] is True
        for selection in selections
    )
    assert all(
        len(selection["successful_manifest_attempt_sha256"]) == 64
        and len(selection["successful_pull_attempt_sha256"]) == 64
        and selection["successful_manifest_attempt_ordinal"] in {1, 2}
        and selection["successful_pull_attempt_ordinal"] in {1, 2}
        for selection in selections
    )
    manifest_environment = next(
        environment
        for arguments, environment in runner.environments
        if "imagetools" in arguments
    )
    assert manifest_environment == {
        "ECOMSRE_RUN_ID": "e" * 32,
        "HTTP_PROXY": "http://127.0.0.1:1097",
        "HTTPS_PROXY": "http://127.0.0.1:1097",
    }
    pull_environments = [
        environment
        for arguments, environment in runner.environments
        if "pull" in arguments
    ]
    assert pull_environments == [
        {"ECOMSRE_RUN_ID": "e" * 32},
        {"ECOMSRE_RUN_ID": "e" * 32},
        {"ECOMSRE_RUN_ID": "e" * 32},
    ]
    assert retry_sleeps == [1.0, 1.0]
    resolved_evidence = json.loads(
        (
            tmp_path
            / "artifacts"
            / "observer-visible"
            / ("e" * 32)
            / "inputs"
            / "bootstrap"
            / "resolved-compose.json"
        ).read_text(encoding="utf-8")
    )
    assert resolved_evidence["service_image_mapping"] == [
        ["ad", source],
        ["grafana", second_source],
    ]
    assert resolved_evidence["service_platforms"] == [
        ["ad", "linux/arm64"],
        ["grafana", "linux/arm64"],
    ]
    assert resolved_evidence["pull_policy"] == "bootstrap-explicit-pull"
    assert resolved_evidence["required_platform"] == "linux/arm64"
    assert resolved_evidence["acceptance_pull_policy"] == "never"
    assert resolved_evidence["registry_proxy_mode"] == (
        "LOOPBACK_HTTP_HTTPS"
    )
    assert resolved_evidence["registry_proxy_source"] == "MACOS_SCUTIL"
    assert resolved_evidence["registry_proxy_socks_present"] is True
    assert resolved_evidence["registry_proxy_parser_schema"] == (
        "phase0.macos-registry-proxy.v1"
    )
    assert len(resolved_evidence["registry_proxy_raw_sha256"]) == 64
    assert len(resolved_evidence["registry_proxy_configuration_sha256"]) == 64
    assert len(resolved_evidence["registry_proxy_environment_sha256"]) == 64
    assert len(
        resolved_evidence["registry_proxy_environment_artifact_sha256"]
    ) == 64
    assert "127.0.0.1" not in json.dumps(resolved_evidence)
    assert "http://" not in json.dumps(resolved_evidence)
    assert "HTTP_PROXY" not in json.dumps(resolved_evidence)
    assert "HTTPS_PROXY" not in json.dumps(resolved_evidence)
    assert "SOCKS_PROXY" not in json.dumps(resolved_evidence)
    assert sum(
        arguments == ("/usr/sbin/scutil", "--proxy")
        for arguments in runner.calls
    ) == 1
    assert len(runner.registry_routes) == 3
    assert len({id(route) for route in runner.registry_routes}) == 1
    environment_artifact = (
        tmp_path
        / "artifacts"
        / "evaluator-only"
        / ("e" * 32)
        / "lifecycle"
        / "bootstrap"
        / "registry-route-environment.json"
    )
    environment_payload = json.loads(environment_artifact.read_text())
    assert environment_payload["environment"] == {
        "ECOMSRE_RUN_ID": "e" * 32,
        "HTTP_PROXY": "http://127.0.0.1:1097",
        "HTTPS_PROXY": "http://127.0.0.1:1097",
    }
    assert "ALL_PROXY" not in environment_payload["environment"]
    assert "SOCKS_PROXY" not in environment_payload["environment"]
    assert environment_payload["environment_sha256"] == canonical_json_sha256(
        environment_payload["environment"]
    )
    assert sha256_file(environment_artifact) == resolved_evidence[
        "registry_proxy_environment_artifact_sha256"
    ]
    route_bindings = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / "inputs"
                / "bootstrap"
            ).glob("*-registry-route-binding-attempt-*.json")
        )
    ]
    assert len(route_bindings) == 3
    assert all(
        "-attempt-" in path.name
        for path in sorted(
            (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / "inputs"
                / "bootstrap"
            ).glob("*-registry-route-binding-attempt-*.json")
        )
    )
    assert sorted(binding["attempt"] for binding in route_bindings) == [1, 1, 2]
    assert {
        binding["route_configuration_sha256"]
        for binding in route_bindings
    } == {resolved_evidence["registry_proxy_configuration_sha256"]}
    assert {
        binding["route_environment_sha256"]
        for binding in route_bindings
    } == {resolved_evidence["registry_proxy_environment_sha256"]}
    assert len(
        {
            binding["manifest_command_log_sha256"]
            for binding in route_bindings
        }
    ) == 3
    for binding in route_bindings:
        bound_attempt = (
            tmp_path
            / "artifacts"
            / "observer-visible"
            / ("e" * 32)
            / binding["attempt_artifact_ref"]
        )
        assert sha256_file(bound_attempt) == binding["attempt_artifact_sha256"]
    assert "http://" not in json.dumps(route_bindings)
    assert "127.0.0.1" not in json.dumps(route_bindings)
    manifest_summaries = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / "inputs"
                / "bootstrap"
            ).glob("*-manifest-retry-summary.json")
        )
    ]
    pull_summaries = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / "inputs"
                / "bootstrap"
            ).glob("*-pull-retry-summary.json")
        )
    ]
    assert sorted(summary["attempt_count"] for summary in manifest_summaries) == [
        1,
        2,
    ]
    assert sorted(summary["attempt_count"] for summary in pull_summaries) == [
        1,
        2,
    ]
    assert all(
        "stderr" not in json.dumps(summary)
        for summary in [*manifest_summaries, *pull_summaries]
    )
    attempt_paths = sorted(
        (
            tmp_path
            / "artifacts"
            / "observer-visible"
            / ("e" * 32)
            / "inputs"
            / "bootstrap"
        ).glob("[0-9][0-9][0-9]-*-attempt-[0-9][0-9].json")
    )
    attempt_payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in attempt_paths
        if "registry-route-binding" not in path.name
    ]
    assert len(attempt_payloads) == 6
    required_attempt_fields = {
        "schema_version",
        "policy_schema_version",
        "policy_sha256",
        "run_id",
        "operation",
        "source_reference",
        "ordinal",
        "max_attempts",
        "process_exit_code",
        "process_timed_out",
        "terminal_exit_code",
        "reason_category",
        "decision",
        "command_log_sha256",
        "stdout_content_sha256",
        "stdout_artifact_sha256",
        "stderr_content_sha256",
        "stderr_artifact_sha256",
        "backoff_seconds",
        "monotonic_observed_seconds",
    }
    assert all(
        required_attempt_fields <= payload.keys()
        for payload in attempt_payloads
    )
    assert all(
        {
            "route_raw_sha256",
            "route_configuration_sha256",
            "route_environment_sha256",
        }
        <= payload.keys()
        for payload in attempt_payloads
        if payload["operation"] == "manifest"
    )
    assert all(
        "route_raw_sha256" not in payload
        and "route_configuration_sha256" not in payload
        and "route_environment_sha256" not in payload
        for payload in attempt_payloads
        if payload["operation"] == "pull"
    )
    retry_evidence_payload = json.dumps(
        [
            *attempt_payloads,
            *route_bindings,
            *manifest_summaries,
            *pull_summaries,
        ]
    )
    assert "127.0.0.1" not in retry_evidence_payload
    assert "http://" not in retry_evidence_payload
    assert "https://" not in retry_evidence_payload
    assert "HTTP_PROXY" not in retry_evidence_payload
    assert "HTTPS_PROXY" not in retry_evidence_payload
    for summary in [*manifest_summaries, *pull_summaries]:
        assert summary["run_id"] == "e" * 32
        assert summary["policy_schema_version"] == (
            "phase0.registry-retry-policy.v1"
        )
        assert len(summary["policy_sha256"]) == 64
        assert summary["successful_ordinal"] in {1, 2}
        assert summary["exhausted"] is False
        assert summary["attempts_started"] == summary["attempt_count"]
        assert summary["complete_attempt_count"] == summary["attempt_count"]
        assert summary["failed_ordinal"] is None
        assert summary["evidence_complete"] is True
        for attempt in summary["attempt_artifacts"]:
            attempt_path = (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / attempt["artifact_ref"]
            )
            assert sha256_file(attempt_path) == attempt["artifact_sha256"]
    lock_binding_path = (
        tmp_path
        / "artifacts"
        / "observer-visible"
        / ("e" * 32)
        / "inputs"
        / "bootstrap"
        / "image-lock-attempt-binding.json"
    )
    lock_binding = json.loads(lock_binding_path.read_text(encoding="utf-8"))
    assert lock_binding["image_lock_sha256"] == sha256_file(lock_path)
    assert len(lock_binding["sources"]) == 2
    assert all(
        len(binding["successful_manifest_attempt_sha256"]) == 64
        and len(binding["successful_pull_attempt_sha256"]) == 64
        for binding in lock_binding["sources"]
    )
    for binding in lock_binding["sources"]:
        for operation in ("manifest", "pull"):
            attempt_path = (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / binding[f"successful_{operation}_attempt_ref"]
            )
            assert sha256_file(attempt_path) == binding[
                f"successful_{operation}_attempt_sha256"
            ]
    assert len(
        list(
            (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / ("e" * 32)
                / "inputs"
                / "bootstrap"
            ).glob("*-manifest-raw.json")
        )
    ) == 2
    assert resolved_evidence["port_plan"] == []
    assert resolved_evidence["compose_raw_stdout_ref"] == (
        "commands/compose.stdout.json"
    )
    assert resolved_evidence["compose_raw_stdout_content_sha256"] == (
        sha256_bytes(compose.encode())
    )
    assert len(resolved_evidence["compose_raw_stdout_artifact_sha256"]) == 64
    assert resolved_evidence["compose_command_log_ref"] == (
        "commands/compose.command-log.json"
    )
    assert len(resolved_evidence["compose_command_log_sha256"]) == 64
    with pytest.raises(FileExistsError):
        bootstrap_image_lock(
            project_root=project,
            artifacts_root=tmp_path / "artifacts-2",
            run_id="f" * 32,
            runner=runner,
            docker_endpoint="unix:///var/run/docker.sock",
            replace_locked=True,
        )


@pytest.mark.parametrize(
    "mismatch_kind",
    ["registry-index", "docker29-unscoped-index-descriptor"],
)
def test_bootstrap_rejects_registry_to_local_digest_mismatch_without_freezing_lock(
    tmp_path: Path,
    mismatch_kind: str,
) -> None:
    project = tmp_path / "project"
    lock_path = project / "config" / "phase0" / "image-lock.json"
    lock_path.parent.mkdir(parents=True)
    original = {
        "schema_version": "phase0.image-lock.v1",
        "status": "UNINITIALIZED",
        "upstream_tag": "3.0.0",
        "upstream_commit": "1755859a9de82c2e5e225be68abc401a5ebf2b4f",
        "compose_config_sha256": None,
        "created_at": None,
        "allowed_source_references": [],
        "images": [],
    }
    lock_path.write_text(json.dumps(original), encoding="utf-8")
    source = "otel/demo:3.0.0-ad"
    compose = json.dumps(
        {
            "services": {
                "ad": {
                    "container_name": "ecomsre-phase0-ad",
                    "image": source,
                    "platform": "linux/arm64",
                    "ports": [],
                }
            }
        },
        sort_keys=True,
    )
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": ARM_DIGEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                }
            ],
        },
        separators=(",", ":"),
    )
    registry_index_digest = "sha256:" + sha256_bytes(manifest.encode())
    local_index_digest = (
        "sha256:" + "f" * 64
        if mismatch_kind == "registry-index"
        else registry_index_digest
    )
    local_platform_digest = (
        ARM_DIGEST
        if mismatch_kind == "registry-index"
        else registry_index_digest
    )
    inspect = json.dumps(
        [
            {
                "RepoTags": [source],
                "RepoDigests": [f"otel/demo@{local_index_digest}"],
                "Descriptor": {"digest": local_platform_digest},
                "Architecture": "arm64",
                "Os": "linux",
                "Id": (
                    registry_index_digest
                    if mismatch_kind == "docker29-unscoped-index-descriptor"
                    else "sha256:" + "d" * 64
                ),
            }
        ]
    )

    class Runner:
        def run(self, arguments, *, timeout_seconds, environment=None):
            if arguments[-3:] == ("config", "--format", "json"):
                return _evidenced_result(
                    artifacts_root=tmp_path / "artifacts",
                    run_id="e" * 32,
                    arguments=arguments,
                    stdout=compose,
                )
            elif arguments == ("/usr/sbin/scutil", "--proxy"):
                stdout = SCUTIL_DIRECT
            elif arguments[3:5] == ("image", "inspect"):
                stdout = inspect
            elif "imagetools" in arguments:
                stdout = manifest
            elif "pull" in arguments:
                return _with_fake_command_log(
                    CommandResult(
                        arguments=arguments,
                        exit_code=0,
                        stdout="",
                        stderr="",
                    ),
                    arguments,
                    artifacts_root=tmp_path / "artifacts",
                    run_id="e" * 32,
                    marker="pull",
                )
            else:
                stdout = ""
            return CommandResult(
                arguments=arguments,
                exit_code=0,
                stdout=stdout,
                stderr="",
            )

        def run_registry_inspect(
            self,
            arguments,
            *,
            timeout_seconds,
            route,
        ):
            return _with_fake_command_log(
                self.run(
                    arguments,
                    timeout_seconds=timeout_seconds,
                    environment=None,
                ),
                arguments,
                artifacts_root=tmp_path / "artifacts",
                run_id="e" * 32,
            )

    with pytest.raises(ValueError, match="digest"):
        bootstrap_image_lock(
            project_root=project,
            artifacts_root=tmp_path / "artifacts",
            run_id="e" * 32,
            runner=Runner(),
            docker_endpoint="unix:///var/run/docker.sock",
        )

    assert json.loads(lock_path.read_text(encoding="utf-8")) == original


@pytest.mark.parametrize(
    ("scenario", "expected_outcome", "expected_reason"),
    [
        (
            "manifest-command-failure",
            Outcome.BLOCKED_UPSTREAM,
            "UPSTREAM_COMMAND_FAILED",
        ),
        (
            "manifest-transient-exhausted",
            Outcome.BLOCKED_UPSTREAM,
            "UPSTREAM_COMMAND_FAILED",
        ),
        (
            "manifest-evidence-missing",
            Outcome.BLOCKED_UPSTREAM,
            "UPSTREAM_COMMAND_FAILED",
        ),
        (
            "pull-command-failure",
            Outcome.BLOCKED_UPSTREAM,
            "UPSTREAM_COMMAND_FAILED",
        ),
        (
            "pull-transient-exhausted",
            Outcome.BLOCKED_UPSTREAM,
            "UPSTREAM_COMMAND_FAILED",
        ),
        (
            "arm64-manifest-unavailable",
            Outcome.BLOCKED_UPSTREAM,
            "BLOCKED_UPSTREAM_ARM64_UNAVAILABLE",
        ),
        (
            "proxy-discovery-failure",
            Outcome.BLOCKED_ENVIRONMENT,
            "PROXY_DISCOVERY_UNAVAILABLE",
        ),
        (
            "proxy-configuration-unsafe",
            Outcome.UNSAFE,
            "PROXY_CONFIGURATION_UNSAFE",
        ),
        (
            "platform-inspect-nonzero",
            Outcome.BLOCKED_UPSTREAM,
            "INPUT_NOT_FROZEN",
        ),
        (
            "platform-inspect-missing-descriptor",
            Outcome.BLOCKED_UPSTREAM,
            "INPUT_NOT_FROZEN",
        ),
        (
            "platform-inspect-index-as-child",
            Outcome.BLOCKED_UPSTREAM,
            "IMAGE_LOCK_LIVE_VERIFICATION_REQUIRED",
        ),
    ],
)
def test_bootstrap_handler_distinguishes_command_failure_from_arm64_unavailable(
    tmp_path: Path,
    monkeypatch,
    capsys,
    scenario: str,
    expected_outcome: Outcome,
    expected_reason: str,
) -> None:
    from ecomsre import cli
    from ecomsre.environment import bootstrap as bootstrap_module

    retry_sleeps: list[float] = []
    monkeypatch.setattr(
        bootstrap_module,
        "_retry_sleep",
        retry_sleeps.append,
    )

    project = tmp_path / "project"
    lock_path = project / "config" / "phase0" / "image-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": "phase0.image-lock.v1",
                "status": "UNINITIALIZED",
                "upstream_tag": "3.0.0",
                "upstream_commit": "1755859a9de82c2e5e225be68abc401a5ebf2b4f",
                "compose_config_sha256": None,
                "created_at": None,
                "allowed_source_references": [],
                "images": [],
            }
        ),
        encoding="utf-8",
    )
    original_lock = lock_path.read_bytes()
    source = "grafana/grafana:13.1.0"
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
    manifest = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": (
                        AMD_DIGEST
                        if scenario == "arm64-manifest-unavailable"
                        else ARM_DIGEST
                    ),
                    "platform": {
                        "os": "linux",
                        "architecture": (
                            "amd64"
                            if scenario == "arm64-manifest-unavailable"
                            else "arm64"
                        ),
                    },
                }
            ],
        },
        separators=(",", ":"),
    )
    registry_index_digest = "sha256:" + sha256_bytes(manifest.encode())
    inspect = json.dumps(
        [
            {
                "RepoTags": [source],
                "RepoDigests": [f"grafana/grafana@{registry_index_digest}"],
                **(
                    {}
                    if scenario == "platform-inspect-missing-descriptor"
                    else {
                        "Descriptor": {
                            "digest": (
                                registry_index_digest
                                if scenario
                                == "platform-inspect-index-as-child"
                                else ARM_DIGEST
                            )
                        }
                    }
                ),
                "Architecture": "arm64",
                "Os": "linux",
                "Id": (
                    registry_index_digest
                    if scenario == "platform-inspect-index-as-child"
                    else "sha256:" + "d" * 64
                ),
            }
        ]
    )
    run_id = "9" * 32

    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, arguments, *, timeout_seconds, environment=None):
            self.calls.append((arguments, environment))
            if arguments[-3:] == ("config", "--format", "json"):
                return _evidenced_result(
                    artifacts_root=tmp_path / "artifacts",
                    run_id=run_id,
                    arguments=arguments,
                    stdout=compose,
                )
            if arguments == ("/usr/sbin/scutil", "--proxy"):
                return CommandResult(
                    arguments=arguments,
                    exit_code=(
                        1 if scenario == "proxy-discovery-failure" else 0
                    ),
                    stdout=(
                        SCUTIL_LOOPBACK.replace(
                            "127.0.0.1",
                            "192.0.2.1",
                        )
                        if scenario == "proxy-configuration-unsafe"
                        else SCUTIL_DIRECT
                    ),
                    stderr="",
                )
            if "imagetools" in arguments:
                return CommandResult(
                    arguments=arguments,
                    exit_code=(
                        1
                        if scenario
                        in {
                            "manifest-command-failure",
                            "manifest-transient-exhausted",
                            "manifest-evidence-missing",
                        }
                        else 0
                    ),
                    stdout=(
                        ""
                        if scenario
                        in {
                            "manifest-command-failure",
                            "manifest-transient-exhausted",
                            "manifest-evidence-missing",
                        }
                        else manifest
                    ),
                    stderr=(
                        "denied: requested access"
                        if scenario == "manifest-command-failure"
                        else (
                            (
                                "ERROR: failed to do request: "
                                'Head "https://registry/v2/": EOF'
                            )
                            if scenario == "manifest-transient-exhausted"
                            else (
                                "ERROR: failed to copy: unexpected EOF"
                                if scenario == "manifest-evidence-missing"
                                else ""
                            )
                        )
                    ),
                )
            if "pull" in arguments:
                result = CommandResult(
                    arguments=arguments,
                    exit_code=(
                        1
                        if scenario
                        in {
                            "pull-command-failure",
                            "pull-transient-exhausted",
                        }
                        else 0
                    ),
                    stdout="",
                    stderr=(
                        "pull access denied"
                        if scenario == "pull-command-failure"
                        else (
                            "ERROR: TLS handshake timeout"
                            if scenario == "pull-transient-exhausted"
                            else ""
                        )
                    ),
                )
                return _with_fake_command_log(
                    result,
                    arguments,
                    artifacts_root=tmp_path / "artifacts",
                    run_id=run_id,
                    marker=f"pull-{len(self.calls)}",
                )
            if arguments[3:5] == ("image", "inspect"):
                return CommandResult(
                    arguments=arguments,
                    exit_code=(
                        1 if scenario == "platform-inspect-nonzero" else 0
                    ),
                    stdout=(
                        ""
                        if scenario == "platform-inspect-nonzero"
                        else inspect
                    ),
                    stderr=(
                        "inspection failed"
                        if scenario == "platform-inspect-nonzero"
                        else ""
                    ),
                )
            raise AssertionError(f"unexpected command: {arguments}")

        def run_registry_inspect(
            self,
            arguments,
            *,
            timeout_seconds,
            route,
        ):
            result = self.run(
                arguments,
                timeout_seconds=timeout_seconds,
                environment=None,
            )
            if scenario == "manifest-evidence-missing":
                return result
            return _with_fake_command_log(
                result,
                arguments,
                artifacts_root=tmp_path / "artifacts",
                run_id=run_id,
                marker=f"manifest-{len(self.calls)}",
            )

    monkeypatch.setattr(
        cli,
        "bootstrap_frozen_upstream",
        lambda *_args: SimpleNamespace(
            outcome=Outcome.SUCCESS,
            reason_codes=(),
        ),
    )
    monkeypatch.setattr(
        cli,
        "collect_docker_snapshot",
        lambda *_args: SimpleNamespace(
            daemon_available=True,
            endpoint="unix:///var/run/docker.sock",
        ),
    )
    runner = Runner()
    exit_code = cli.main(
        ["phase0", "bootstrap", "--run-id", run_id],
        runner=runner,
        project_root=project,
        artifacts_root=tmp_path / "artifacts",
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)

    assert exit_code == expected_outcome.exit_code
    assert payload["outcome"] == expected_outcome.value
    assert payload["reason_code"] == expected_reason
    assert output.err == ""
    assert lock_path.read_bytes() == original_lock
    manifest_attempts = sum(
        arguments[3:6] == ("buildx", "imagetools", "inspect")
        for arguments, _environment in runner.calls
    )
    pull_attempts = sum(
        arguments[3:5] == ("pull", "--platform")
        for arguments, _environment in runner.calls
    )
    if scenario == "manifest-transient-exhausted":
        assert manifest_attempts == 3
        assert pull_attempts == 0
        assert retry_sleeps == [1.0, 2.0]
    elif scenario == "manifest-command-failure":
        assert manifest_attempts == 1
        assert pull_attempts == 0
        assert retry_sleeps == []
    elif scenario == "manifest-evidence-missing":
        assert manifest_attempts == 1
        assert pull_attempts == 0
        assert retry_sleeps == []
    elif scenario == "pull-transient-exhausted":
        assert manifest_attempts == 1
        assert pull_attempts == 3
        assert retry_sleeps == [1.0, 2.0]
    elif scenario == "pull-command-failure":
        assert manifest_attempts == 1
        assert pull_attempts == 1
        assert retry_sleeps == []
    if scenario in {
        "manifest-command-failure",
        "manifest-transient-exhausted",
        "manifest-evidence-missing",
        "pull-command-failure",
        "pull-transient-exhausted",
    }:
        operation = (
            "manifest" if scenario.startswith("manifest-") else "pull"
        )
        summary_path = next(
            (
                tmp_path
                / "artifacts"
                / "observer-visible"
                / run_id
                / "inputs"
                / "bootstrap"
            ).glob(f"*-{operation}-retry-summary.json")
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["attempt_count"] == (
            3 if scenario.endswith("transient-exhausted") else 1
        )
        assert summary["final_status"] in {
            "EXHAUSTED",
            "NON_RETRYABLE_FAILURE",
            "EVIDENCE_INCOMPLETE",
        }
        if scenario == "manifest-evidence-missing":
            assert summary["attempt_count"] == 1
            assert summary["attempts_started"] == 1
            assert summary["complete_attempt_count"] == 0
            assert summary["failed_ordinal"] == 1
            assert summary["evidence_complete"] is False
            assert summary["attempt_artifacts"] == []
        else:
            assert summary["attempts_started"] == summary["attempt_count"]
            assert summary["complete_attempt_count"] == summary["attempt_count"]
            assert summary["failed_ordinal"] is None
            assert summary["evidence_complete"] is True
            assert all(
                len(attempt["artifact_sha256"]) == 64
                for attempt in summary["attempt_artifacts"]
            )
        assert "stderr" not in json.dumps(summary)
    if scenario == "arm64-manifest-unavailable":
        assert payload["reason_code"] != "UPSTREAM_COMMAND_FAILED"


def test_scutil_proxy_parser_accepts_only_enabled_literal_loopback() -> None:
    from ecomsre.environment import bootstrap

    configuration = getattr(bootstrap, "_parse_scutil_proxy")(
        SCUTIL_LOOPBACK,
        run_id="e" * 32,
        docker_endpoint="unix:///var/run/docker.sock",
    )

    assert configuration.mode == "LOOPBACK_HTTP_HTTPS"
    assert configuration.source == "MACOS_SCUTIL"
    assert dict(configuration.proxy_environment) == {
        "HTTP_PROXY": "http://127.0.0.1:1097",
        "HTTPS_PROXY": "http://127.0.0.1:1097",
    }

    ipv6 = getattr(bootstrap, "_parse_scutil_proxy")(
        SCUTIL_LOOPBACK.replace("127.0.0.1", "::1"),
        run_id="e" * 32,
        docker_endpoint="unix:///var/run/docker.sock",
    )
    assert dict(ipv6.proxy_environment) == {
        "HTTP_PROXY": "http://[::1]:1097",
        "HTTPS_PROXY": "http://[::1]:1097",
    }


def test_scutil_proxy_parser_records_but_does_not_inject_socks() -> None:
    from ecomsre.environment import bootstrap

    configuration = getattr(bootstrap, "_parse_scutil_proxy")(
        SCUTIL_LOOPBACK_WITH_SOCKS,
        run_id="e" * 32,
        docker_endpoint="unix:///var/run/docker.sock",
    )

    assert configuration.mode == "LOOPBACK_HTTP_HTTPS"
    assert configuration.socks_present is True
    assert dict(configuration.proxy_environment) == {
        "HTTP_PROXY": "http://127.0.0.1:1097",
        "HTTPS_PROXY": "http://127.0.0.1:1097",
    }

    socks_only = getattr(bootstrap, "_parse_scutil_proxy")(
        """<dictionary> {
  SOCKSEnable : 1
  SOCKSPort : 1097
  SOCKSProxy : 127.0.0.1
}
""",
        run_id="e" * 32,
        docker_endpoint="unix:///var/run/docker.sock",
    )
    assert socks_only.mode == "DIRECT"
    assert socks_only.socks_present is True
    assert socks_only.proxy_environment == ()


def test_scutil_proxy_parser_treats_absent_proxy_as_explicit_direct() -> None:
    from ecomsre.environment import bootstrap

    configuration = getattr(bootstrap, "_parse_scutil_proxy")(
        SCUTIL_DIRECT,
        run_id="e" * 32,
        docker_endpoint="unix:///var/run/docker.sock",
    )

    assert configuration.mode == "DIRECT"
    assert configuration.source == "MACOS_SCUTIL"
    assert configuration.proxy_environment == ()


@pytest.mark.parametrize(
    ("disabled_prefix", "expected_mode", "expected_environment"),
    [
        (
            "HTTPS",
            "LOOPBACK_HTTP",
            {"HTTP_PROXY": "http://127.0.0.1:1097"},
        ),
        (
            "HTTP",
            "LOOPBACK_HTTPS",
            {"HTTPS_PROXY": "http://127.0.0.1:1097"},
        ),
    ],
)
def test_scutil_proxy_parser_keeps_http_and_https_routes_separate(
    disabled_prefix: str,
    expected_mode: str,
    expected_environment: dict[str, str],
) -> None:
    from ecomsre.environment import bootstrap

    raw = SCUTIL_LOOPBACK.replace(f"{disabled_prefix}Enable : 1", "")
    configuration = getattr(bootstrap, "_parse_scutil_proxy")(
        raw,
        run_id="e" * 32,
        docker_endpoint="unix:///var/run/docker.sock",
    )

    assert configuration.mode == expected_mode
    assert dict(configuration.proxy_environment) == expected_environment


@pytest.mark.parametrize(
    "raw",
    [
        SCUTIL_LOOPBACK.replace("127.0.0.1", "192.0.2.1"),
        SCUTIL_LOOPBACK.replace("127.0.0.1", "localhost"),
        SCUTIL_LOOPBACK.replace(
            "127.0.0.1",
            "user:secret@127.0.0.1",
        ),
        SCUTIL_LOOPBACK.replace(
            "127.0.0.1",
            "http://127.0.0.1",
        ),
        SCUTIL_LOOPBACK.replace("127.0.0.1", "127.0.0.1/path"),
        SCUTIL_LOOPBACK.replace("127.0.0.1", "127.0.0.1?query"),
        SCUTIL_LOOPBACK.replace("127.0.0.1", "127.0.0.1#fragment"),
        SCUTIL_LOOPBACK.replace("127.0.0.1", "127.0.0.1 invalid"),
        SCUTIL_LOOPBACK.replace("HTTPPort : 1097\n", ""),
        SCUTIL_LOOPBACK.replace("HTTPPort : 1097", "HTTPPort : 70000"),
        SCUTIL_LOOPBACK.replace("HTTPEnable : 1", "HTTPEnable : maybe"),
        SCUTIL_LOOPBACK.replace(
            "HTTPEnable : 1",
            "HTTPEnable : 1\n  HTTPEnable : 1",
        ),
        SCUTIL_LOOPBACK.replace(
            "HTTPEnable : 1",
            "ExceptionsList : <array> {\n    HTTPEnable : 1\n  }",
        ),
        SCUTIL_LOOPBACK.replace(
            "HTTPEnable : 1",
            "ProxyAutoConfigEnable : 1",
        ),
        SCUTIL_LOOPBACK.replace(
            "HTTPEnable : 1",
            "ProxyAutoDiscoveryEnable : 1",
        ),
        SCUTIL_LOOPBACK.replace("\n", "\r\n"),
    ],
)
def test_scutil_proxy_parser_rejects_enabled_malformed_configuration(
    raw: str,
) -> None:
    from ecomsre.environment import bootstrap

    with pytest.raises(ValueError):
        getattr(bootstrap, "_parse_scutil_proxy")(
            raw,
            run_id="e" * 32,
            docker_endpoint="unix:///var/run/docker.sock",
        )


def test_scutil_proxy_parser_rejects_remote_docker_endpoint() -> None:
    from ecomsre.environment import bootstrap

    with pytest.raises(ValueError):
        getattr(bootstrap, "_parse_scutil_proxy")(
            SCUTIL_LOOPBACK,
            run_id="e" * 32,
            docker_endpoint="tcp://192.0.2.1:2375",
        )


@pytest.mark.parametrize("exit_code", [1, 20, 124])
def test_scutil_proxy_discovery_failure_is_fail_closed(exit_code: int) -> None:
    from ecomsre.environment import bootstrap

    class Runner:
        def run(self, arguments, *, timeout_seconds, environment=None):
            assert arguments == ("/usr/sbin/scutil", "--proxy")
            return CommandResult(
                arguments=arguments,
                exit_code=exit_code,
                stdout="",
                stderr="scutil unavailable",
            )

    with pytest.raises(
        getattr(bootstrap, "ProxyDiscoveryUnavailable"),
        match="proxy discovery failed",
    ):
        getattr(bootstrap, "_discover_registry_proxy")(
            Runner(),
            run_id="e" * 32,
            docker_endpoint="unix:///var/run/docker.sock",
        )
