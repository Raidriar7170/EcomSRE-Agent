from pathlib import Path

from ecomsre.environment.preflight import CommandResult
from ecomsre.telemetry import probe


RUN_ID = "9" * 32
ENDPOINT = "unix:///var/run/docker.sock"


class Delegate:
    def __init__(self) -> None:
        self.timeout_seconds = None

    def run(self, arguments, *, timeout_seconds, environment=None):
        self.timeout_seconds = timeout_seconds
        return CommandResult(
            arguments=arguments,
            exit_code=0,
            stdout="ok",
            stderr="",
        )


def test_production_docker_runner_delegates_exact_timeout_seconds(
    tmp_path: Path,
) -> None:
    runner = probe.ProductionDockerRunner(
        _token=probe._PRODUCTION_DOCKER_RUNNER_TOKEN,
        run_id=RUN_ID,
        docker_endpoint=ENDPOINT,
        daemon_id="daemon",
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )
    delegate = Delegate()
    object.__setattr__(runner, "_runner", delegate)
    arguments = ("docker", "--host", ENDPOINT, "info")

    result = runner.run(
        arguments,
        timeout_seconds=30,
        environment={"ECOMSRE_RUN_ID": RUN_ID},
    )

    assert result.stdout == "ok"
    assert delegate.timeout_seconds == 30
