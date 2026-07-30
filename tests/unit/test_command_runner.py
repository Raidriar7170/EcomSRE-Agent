import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ecomsre.environment.command_runner import (
    AuditedSubprocessRunner,
    _classify_process,
)
from ecomsre.environment.preflight import parse_port_observation
from ecomsre.evidence.models import CommandLog
from ecomsre.phase0.models import Outcome


RUN_ID = "d" * 32
ROOT = Path(__file__).resolve().parents[2]


def test_audited_runner_uses_project_temp_and_persists_separate_streams(
    tmp_path: Path,
) -> None:
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )

    result = runner.run(
        (
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
        ),
        timeout_seconds=10,
    )

    assert result.exit_code == 7
    assert result.process_exit_code == 7
    assert result.process_timed_out is False
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.stdout_artifact is not None
    assert result.stderr_artifact is not None
    assert result.command_log_artifact is not None
    stdout_payload = json.loads(Path(result.stdout_artifact).read_text())
    stderr_payload = json.loads(Path(result.stderr_artifact).read_text())
    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact).read_text(encoding="utf-8")
    )
    assert stdout_payload["content"] == "out\n"
    assert stderr_payload["content"] == "err\n"
    assert command_log.classification is Outcome.FAILED_ACCEPTANCE
    assert command_log.terminal_exit_code == Outcome.FAILED_ACCEPTANCE.exit_code
    assert command_log.reason_code == "PROCESS_EXIT_NONZERO"
    assert command_log.network_access_scope == "NONE"
    assert command_log.observed_effect_scope == ("NOT_OBSERVED",)
    with pytest.raises(OSError):
        Path(result.stdout_artifact).write_text("replace", encoding="utf-8")
    assert str(runner.temp_directory).startswith(str(ROOT / ".ecomsre-tmp"))


def test_exact_lsof_no_listener_preserves_exit_one_but_logs_success(
    tmp_path: Path,
) -> None:
    lsof = tmp_path / "lsof"
    lsof.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(lsof, 0o700)
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )
    arguments = (
        str(lsof),
        "-nP",
        "-F",
        "pcn",
        "-iTCP:8080",
        "-sTCP:LISTEN",
    )

    result = runner.run(arguments, timeout_seconds=10)
    observation = parse_port_observation(
        result,
        port=8080,
        owned_processes={},
        manifest_sha256="a" * 64,
        active_run_id=RUN_ID,
    )
    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact or "").read_text(encoding="utf-8")
    )

    assert result.exit_code == 1
    assert result.process_exit_code == 1
    assert result.stdout == ""
    assert result.stderr == ""
    assert observation.occupied is False
    assert observation.ownership == "NONE"
    assert command_log.classification is Outcome.SUCCESS
    assert command_log.reason_code == "PROCESS_EXPECTED_NO_MATCH"
    assert command_log.terminal_exit_code == 0


@pytest.mark.parametrize(
    ("arguments", "exit_code", "stdout", "stderr"),
    [
        (
            ("lsof", "-nP", "-F", "pcn", "-iTCP:8080", "-sTCP:LISTEN"),
            1,
            "p123\n",
            "",
        ),
        (
            ("lsof", "-nP", "-F", "pcn", "-iTCP:8080", "-sTCP:LISTEN"),
            1,
            "",
            "lsof: Permission denied\n",
        ),
        (
            ("lsof", "-nP", "-F", "pcn", "-iTCP:any", "-sTCP:LISTEN"),
            1,
            "",
            "",
        ),
        (
            ("lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN"),
            1,
            "",
            "",
        ),
        (
            ("lsof", "-nP", "-F", "pcn", "-iTCP:8080", "-sTCP:LISTEN"),
            2,
            "",
            "",
        ),
    ],
)
def test_lsof_expected_no_match_classification_is_exact_and_fail_closed(
    arguments: tuple[str, ...],
    exit_code: int,
    stdout: str,
    stderr: str,
) -> None:
    outcome, reason = _classify_process(
        arguments=arguments,
        start_failed=False,
        timed_out=False,
        process_exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )

    assert outcome is Outcome.FAILED_ACCEPTANCE
    assert reason == "PROCESS_EXIT_NONZERO"


def test_compose_like_raw_stream_stays_evaluator_only_and_observer_is_safe(
    tmp_path: Path,
) -> None:
    secret = '"adServiceFailure":{"defaultVariant":"on"}'
    compose_like = (
        '{"services":{"flagd":{"environment":{"FEATURE_FLAGS":{'
        + secret
        + "}}}}}"
    )
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )
    emitter = tmp_path / "compose-config"
    emitter.write_text(
        "#!/bin/sh\nprintf '%s\\n' " + repr(compose_like) + "\n",
        encoding="utf-8",
    )
    os.chmod(emitter, 0o700)

    result = runner.run(
        (str(emitter),),
        timeout_seconds=10,
    )

    raw_path = Path(result.stdout_artifact or "")
    assert raw_path.is_relative_to(
        tmp_path / "artifacts" / "evaluator-only" / RUN_ID
    )
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert secret in raw_payload["content"]
    assert raw_payload["content_sha256"] == result.stdout_sha256
    observer_root = tmp_path / "artifacts" / "observer-visible" / RUN_ID
    observer_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in observer_root.rglob("*")
        if path.is_file()
    )
    assert "adServiceFailure" not in observer_text
    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact or "").read_text(encoding="utf-8")
    )
    assert command_log.stdout_artifact.startswith("commands/")
    assert command_log.stdout_sha256 == result.stdout_sha256


def test_audited_runner_records_timeout_without_inventing_process_exit(
    tmp_path: Path,
) -> None:
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )

    result = runner.run(
        (sys.executable, "-c", "import time; time.sleep(2)"),
        timeout_seconds=0.01,
    )

    assert result.exit_code == 124
    assert result.process_exit_code is None
    assert result.process_timed_out is True
    assert result.command_log_artifact is not None
    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact).read_text(encoding="utf-8")
    )
    assert command_log.classification is Outcome.BLOCKED_ENVIRONMENT
    assert command_log.reason_code == "PROCESS_TIMEOUT"


def test_audited_runner_records_process_start_failure_as_valid_v2_evidence(
    tmp_path: Path,
) -> None:
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )

    result = runner.run(
        ("/definitely/not/an/executable",),
        timeout_seconds=10,
    )

    assert result.process_exit_code is None
    assert result.process_timed_out is False
    assert result.exit_code == Outcome.BLOCKED_ENVIRONMENT.exit_code
    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact or "").read_text(encoding="utf-8")
    )
    assert command_log.reason_code == "PROCESS_START_FAILED"
    assert command_log.observed_effect_scope == ("process-start-failed",)


def test_audited_runner_allows_literal_loopback_proxy_for_registry_inspect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre.environment import bootstrap

    docker = tmp_path / "docker"
    docker.write_text(
        (
            "#!/bin/sh\n"
            "printf '%s\\n%s\\n%s\\n' "
            "\"$HTTP_PROXY\" \"$HTTPS_PROXY\" \"${ALL_PROXY-unset}\"\n"
        ),
        encoding="utf-8",
    )
    os.chmod(docker, 0o700)
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )
    route = getattr(bootstrap, "_parse_scutil_proxy")(
        """<dictionary> {
  HTTPEnable : 1
  HTTPPort : 1097
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : 1097
  HTTPSProxy : ::1
}
""",
        run_id=RUN_ID,
        docker_endpoint="unix:///var/run/docker.sock",
    )
    monkeypatch.setenv("ALL_PROXY", "http://ambient-secret.example:8080")

    result = getattr(runner, "run_registry_inspect")(
        (
            str(docker),
            "--host",
            "unix:///var/run/docker.sock",
            "buildx",
            "imagetools",
            "inspect",
            "grafana/grafana:13.1.0",
            "--raw",
        ),
        timeout_seconds=10,
        route=route,
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "http://127.0.0.1:1097\n"
        "http://[::1]:1097\n"
        "unset\n"
    )
    observer_root = tmp_path / "artifacts" / "observer-visible" / RUN_ID
    observer_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in observer_root.rglob("*")
        if path.is_file()
    )
    assert "ambient-secret" not in observer_text
    assert "127.0.0.1:1097" not in observer_text


@pytest.mark.parametrize(
    "environment",
    [
        {"HTTPS_PROXY": "http://127.0.0.1:1097"},
        {"ALL_PROXY": "socks5://127.0.0.1:1097"},
        {"HTTPS_PROXY": "http://user:secret@127.0.0.1:1097"},
        {"HTTPS_PROXY": "http://192.0.2.1:1097"},
        {"HTTPS_PROXY": "http://localhost:1097"},
    ],
)
def test_audited_runner_rejects_unapproved_proxy_environment_without_logging_it(
    tmp_path: Path,
    environment: dict[str, str],
) -> None:
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(docker, 0o700)
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )
    secret = next(iter(environment.values()))

    with pytest.raises(ValueError) as raised:
        runner.run(
            (
                str(docker),
                "buildx",
                "imagetools",
                "inspect",
                "grafana/grafana:13.1.0",
                "--raw",
            ),
            timeout_seconds=1,
            environment=environment,
        )

    assert secret not in str(raised.value)
    observer_root = tmp_path / "artifacts" / "observer-visible" / RUN_ID
    assert not observer_root.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        (
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "grafana/grafana:13.1.0",
            "--raw",
        ),
        ("docker", "pull", "grafana/grafana:13.1.0"),
        (
            "docker",
            "--host",
            "tcp://192.0.2.1:2375",
            "buildx",
            "imagetools",
            "inspect",
            "grafana/grafana:13.1.0",
            "--raw",
        ),
        (
            "docker",
            "--host",
            "unix:///tmp/other.sock",
            "buildx",
            "imagetools",
            "inspect",
            "grafana/grafana:13.1.0",
            "--raw",
        ),
        ("python", "-c", "pass"),
    ],
)
def test_audited_runner_rejects_proxy_environment_for_other_commands(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    from ecomsre.environment import bootstrap

    executable = tmp_path / arguments[0]
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(executable, 0o700)
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )
    route = getattr(bootstrap, "_parse_scutil_proxy")(
        """<dictionary> {
  HTTPSEnable : 1
  HTTPSPort : 1097
  HTTPSProxy : 127.0.0.1
}
""",
        run_id=RUN_ID,
        docker_endpoint="unix:///var/run/docker.sock",
    )

    with pytest.raises(ValueError):
        getattr(runner, "run_registry_inspect")(
            (str(executable), *arguments[1:]),
            timeout_seconds=1,
            route=route,
        )


def test_registry_route_hash_binding_is_fail_closed(tmp_path: Path) -> None:
    from ecomsre.environment import bootstrap

    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(docker, 0o700)
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )
    route = getattr(bootstrap, "_parse_scutil_proxy")(
        """<dictionary> {
  HTTPSEnable : 1
  HTTPSPort : 1097
  HTTPSProxy : 127.0.0.1
}
""",
        run_id=RUN_ID,
        docker_endpoint="unix:///var/run/docker.sock",
    )
    tampered_routes = (
        replace(route, environment_sha256="0" * 64),
        replace(route, socks_present=not route.socks_present),
        replace(route, docker_endpoint="unix:///tmp/other.sock"),
    )

    for tampered in tampered_routes:
        with pytest.raises(ValueError):
            getattr(runner, "run_registry_inspect")(
                (
                    str(docker),
                    "--host",
                    "unix:///var/run/docker.sock",
                    "buildx",
                    "imagetools",
                    "inspect",
                    "grafana/grafana:13.1.0",
                    "--raw",
                ),
                timeout_seconds=10,
                route=tampered,
            )


@pytest.mark.parametrize(
    ("start_failed", "timed_out", "process_exit_code"),
    [
        (False, False, 1),
        (True, False, None),
        (False, True, None),
    ],
)
def test_scutil_proxy_process_failures_have_typed_environment_classification(
    start_failed: bool,
    timed_out: bool,
    process_exit_code: int | None,
) -> None:
    outcome, reason = _classify_process(
        arguments=("/usr/sbin/scutil", "--proxy"),
        start_failed=start_failed,
        timed_out=timed_out,
        process_exit_code=process_exit_code,
        stdout="",
        stderr="unavailable",
    )

    assert outcome is Outcome.BLOCKED_ENVIRONMENT
    assert reason == "PROXY_DISCOVERY_UNAVAILABLE"


def test_audited_scutil_failure_preserves_exit_and_writes_typed_command_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ecomsre.environment import command_runner

    class FailedScutil:
        returncode = 1

        @staticmethod
        def communicate(*, timeout):
            assert timeout == 5
            return "", "proxy lookup failed"

    def fake_popen(arguments, **_kwargs):
        assert arguments == ("/usr/sbin/scutil", "--proxy")
        return FailedScutil()

    monkeypatch.setattr(command_runner, "_Popen", fake_popen)
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )

    result = runner.run(
        ("/usr/sbin/scutil", "--proxy"),
        timeout_seconds=5,
        environment={"ECOMSRE_RUN_ID": RUN_ID},
    )
    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact or "").read_text(encoding="utf-8")
    )

    assert result.exit_code == 1
    assert result.process_exit_code == 1
    assert command_log.classification is Outcome.BLOCKED_ENVIRONMENT
    assert command_log.reason_code == "PROXY_DISCOVERY_UNAVAILABLE"
    assert command_log.terminal_exit_code == Outcome.BLOCKED_ENVIRONMENT.exit_code


def test_audited_runner_terminates_the_entire_process_group_on_timeout(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "child-received-signal"
    child_code = (
        "import pathlib,signal,time;"
        f"p=pathlib.Path({str(marker)!r});"
        "signal.signal(signal.SIGTERM,lambda *_:(p.write_text('term'),exit(0)));"
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "print('spawned',flush=True);time.sleep(60)"
    )
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )

    result = runner.run(
        (sys.executable, "-c", parent_code),
        timeout_seconds=0.2,
    )

    assert result.process_timed_out is True
    assert marker.read_text(encoding="utf-8") == "term"


@pytest.mark.parametrize(
    ("arguments", "expected_scope", "declared"),
    [
        (("git", "rev-parse", "HEAD"), "NONE", False),
        (("git", "fetch", "origin"), "EXTERNAL_GIT", True),
        (("docker", "inspect", "image"), "LOCAL_DOCKER_DAEMON", False),
        (
            ("docker", "pull", "registry.example/image@sha256:" + "a" * 64),
            "EXTERNAL_REGISTRY",
            True,
        ),
    ],
)
def test_command_network_declaration_distinguishes_local_and_external_operations(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected_scope: str,
    declared: bool,
) -> None:
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )
    executable = tmp_path / arguments[0]
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(executable, 0o700)
    fake_arguments = (str(executable), *arguments[1:])

    result = runner.run(fake_arguments, timeout_seconds=1)

    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact or "").read_text(encoding="utf-8")
    )
    assert command_log.network_access_scope == expected_scope
    assert command_log.network_access_declared is declared


def test_production_subprocess_access_is_centralized() -> None:
    offenders = []
    for path in (ROOT / "src" / "ecomsre").rglob("*.py"):
        if path.name == "command_runner.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "subprocess.run(" in text or "_SUBPROCESS_RUN(" in text:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


@pytest.mark.parametrize(
    ("arguments", "expected_outcome", "expected_reason"),
    [
        (
            ("git", "rev-parse", "HEAD"),
            Outcome.BLOCKED_UPSTREAM,
            "UPSTREAM_COMMAND_FAILED",
        ),
        (
            ("docker", "compose", "down"),
            Outcome.MANUAL_INTERVENTION_REQUIRED,
            "OWNED_STOP_COMMAND_FAILED",
        ),
        (
            ("docker", "compose", "up"),
            Outcome.BLOCKED_ENVIRONMENT,
            "ENVIRONMENT_COMMAND_FAILED",
        ),
        (
            ("docker", "inspect", "container"),
            Outcome.BLOCKED_ENVIRONMENT,
            "DOCKER_READ_COMMAND_FAILED",
        ),
    ],
)
def test_nonzero_command_classification_is_purpose_aware_and_effect_unobserved(
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected_outcome: Outcome,
    expected_reason: str,
) -> None:
    executable = tmp_path / arguments[0]
    executable.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    os.chmod(executable, 0o700)
    runner = AuditedSubprocessRunner(
        project_root=ROOT,
        artifacts_root=tmp_path / "artifacts",
        run_id=RUN_ID,
    )

    result = runner.run(
        (str(executable), *arguments[1:]),
        timeout_seconds=1,
    )

    command_log = CommandLog.model_validate_json(
        Path(result.command_log_artifact or "").read_text(encoding="utf-8")
    )
    assert command_log.classification is expected_outcome
    assert command_log.reason_code == expected_reason
    assert command_log.observed_effect_scope == ("NOT_OBSERVED",)
