from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import ecomsre.cli as cli_module
from ecomsre.environment import lifecycle
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipManifest,
)


RUN_ID = "f" * 32
OTHER_RUN_ID = "e" * 32
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"


def _observation(
    *,
    present: bool,
    labels: dict[str, str] | None = None,
    resource_id: str = "anonymous-volume-id",
):
    return lifecycle.ResourceDriftObservation(
        kind="volume",
        name=resource_id,
        resource_id=resource_id,
        labels=labels or {},
        present=present,
    )


def _manifest_resource(
    *,
    run_id: str = OTHER_RUN_ID,
    resource_id: str = "manifest-volume",
) -> OwnedResource:
    return OwnedResource(
        kind="volume",
        name=resource_id,
        resource_id=resource_id,
        labels={
            "com.docker.compose.project": PROJECT_NAMESPACE,
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: run_id,
        },
        identity_evidence=(f"volume:{resource_id}",),
    )


def test_unowned_anonymous_volume_disappearance_is_non_blocking_warning() -> None:
    assessment = lifecycle.classify_resource_drift(
        before=_observation(present=True),
        after=_observation(present=False),
        authenticated_manifests=(),
        audited_arguments=(),
        current_run_id=RUN_ID,
    )

    assert assessment is not None
    assert assessment.reason_code == "EXTERNAL_UNOWNED_RESOURCE_DRIFT"
    assert assessment.severity == "WARNING"
    assert assessment.current_run_causality == "UNPROVEN"
    assert not assessment.blocking
    assert not assessment.delete_command_attributed


def test_manifest_owned_volume_disappearance_is_unsafe() -> None:
    resource = _manifest_resource()
    manifest = OwnershipManifest(
        run_id=OTHER_RUN_ID,
        resources=(resource,),
    )
    assessment = lifecycle.classify_resource_drift(
        before=_observation(
            present=True,
            labels=resource.labels,
            resource_id=resource.resource_id,
        ),
        after=_observation(
            present=False,
            labels={},
            resource_id=resource.resource_id,
        ),
        authenticated_manifests=(manifest,),
        audited_arguments=(),
        current_run_id=RUN_ID,
    )

    assert assessment is not None
    assert assessment.severity == "UNSAFE"
    assert assessment.blocking
    assert assessment.manifest_owned


def test_project_labeled_volume_disappearance_is_unsafe() -> None:
    assessment = lifecycle.classify_resource_drift(
        before=_observation(
            present=True,
            labels={
                PROJECT_LABEL: PROJECT_NAMESPACE,
                RUN_LABEL: OTHER_RUN_ID,
            },
        ),
        after=_observation(present=False),
        authenticated_manifests=(),
        audited_arguments=(),
        current_run_id=RUN_ID,
    )

    assert assessment is not None
    assert assessment.project_labeled
    assert assessment.severity == "UNSAFE"
    assert assessment.blocking


def test_exact_volume_rm_in_command_audit_attributes_disappearance() -> None:
    resource_id = "anonymous-volume-id"
    assessment = lifecycle.classify_resource_drift(
        before=_observation(present=True, resource_id=resource_id),
        after=_observation(present=False, resource_id=resource_id),
        authenticated_manifests=(),
        audited_arguments=(
            (
                "docker",
                "--host",
                DOCKER_ENDPOINT,
                "volume",
                "rm",
                resource_id,
            ),
        ),
        current_run_id=RUN_ID,
    )

    assert assessment is not None
    assert assessment.delete_command_attributed
    assert assessment.current_run_causality == "PROVEN"
    assert assessment.severity == "UNSAFE"
    assert assessment.blocking


def test_unowned_volume_that_still_exists_has_no_drift_warning() -> None:
    observation = _observation(present=True)

    assessment = lifecycle.classify_resource_drift(
        before=observation,
        after=observation,
        authenticated_manifests=(),
        audited_arguments=(),
        current_run_id=RUN_ID,
    )

    assert assessment is None


def test_unowned_drift_warning_is_explicitly_non_blocking() -> None:
    assessment = lifecycle.classify_resource_drift(
        before=_observation(present=True),
        after=_observation(present=False),
        authenticated_manifests=(),
        audited_arguments=(),
        current_run_id=RUN_ID,
    )

    assert assessment is not None
    assert assessment.warning_reason_codes == (
        "EXTERNAL_UNOWNED_RESOURCE_DRIFT",
    )
    assert assessment.blocking_reason_codes == ()


@pytest.mark.parametrize(
    "arguments",
    [
        (
            "docker",
            "--host",
            DOCKER_ENDPOINT,
            "volume",
            "prune",
        ),
        (
            "docker",
            "--host",
            DOCKER_ENDPOINT,
            "volume",
            "rm",
            "ecomsre-phase0-*",
        ),
        (
            "docker",
            "--host",
            DOCKER_ENDPOINT,
            "compose",
            "down",
            "-v",
        ),
    ],
)
def test_owned_volume_cleanup_allowlist_rejects_broad_or_wildcard_argv(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        lifecycle.ComposeInvocation(
            purpose="cleanup_owned_volume",
            arguments=arguments,
            environment={"ECOMSRE_RUN_ID": RUN_ID},
            timeout_seconds=30,
            read_only=False,
        )


def test_owned_volume_cleanup_builder_requires_exact_manifest_identity() -> None:
    resource = _manifest_resource(
        run_id=RUN_ID,
        resource_id=(
            f"{PROJECT_NAMESPACE}-{RUN_ID}-prometheus-data"
        ),
    )

    invocation = lifecycle.build_owned_volume_cleanup_invocation(
        resource,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    assert invocation.arguments == (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "volume",
        "rm",
        resource.name,
    )
    assert not invocation.read_only

    with pytest.raises(ValueError, match="ownership"):
        lifecycle.build_owned_volume_cleanup_invocation(
            resource.model_copy(
                update={
                    "labels": {
                        **resource.labels,
                        RUN_LABEL: OTHER_RUN_ID,
                    }
                }
            ),
            run_id=RUN_ID,
            docker_endpoint=DOCKER_ENDPOINT,
        )


def test_drift_observation_rejects_identity_change_between_snapshots() -> None:
    with pytest.raises(ValueError, match="identity"):
        lifecycle.classify_resource_drift(
            before=_observation(present=True, resource_id="before"),
            after=_observation(present=False, resource_id="after"),
            authenticated_manifests=(),
            audited_arguments=(),
            current_run_id=RUN_ID,
        )


def test_cleanup_handler_uses_authenticated_manifest_and_minimal_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ownership = SimpleNamespace(run_id=RUN_ID, is_authentic=lambda: True)
    cleanup_calls: list[dict[str, object]] = []
    reseals: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli_module,
        "load_authenticated_ownership_context",
        lambda *_args, **_kwargs: ownership,
    )
    monkeypatch.setattr(
        cli_module,
        "collect_direct_stop_docker_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            daemon_available=True,
            context_name="desktop-linux",
            docker_endpoint=DOCKER_ENDPOINT,
            daemon_id="daemon-id",
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "cleanup_owned_named_volumes",
        lambda *_args, **kwargs: (
            cleanup_calls.append(kwargs)
            or SimpleNamespace(
                result=cli_module.TerminalResult(
                    outcome=cli_module.Outcome.SUCCESS,
                    reason_code="OWNED_NAMED_VOLUMES_CLEANED",
                ),
                removed_volume_names=("owned-volume",),
            )
        ),
    )
    report_root = tmp_path / "artifacts" / "reports" / RUN_ID
    report_root.mkdir(parents=True)
    (report_root / "smoke-report.json").write_text("{}", encoding="utf-8")
    (report_root / "checksums.sha256").write_text(
        "a" * 64 + "  observer-visible/file.json\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli_module,
        "reseal_recovery_evidence",
        lambda **kwargs: (
            reseals.append(kwargs) or SimpleNamespace(current=True)
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "validate_current_recovery_seal",
        lambda *_args, **_kwargs: True,
    )
    context = cli_module.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    result = cli_module._handle_cleanup_owned_volumes(
        SimpleNamespace(run_id=RUN_ID),
        context,
    )

    assert result.outcome is cli_module.Outcome.SUCCESS
    assert result.owned_volume_cleanup_completed
    assert result.removed_volume_names == ("owned-volume",)
    assert result.recovery_seal_current
    assert cleanup_calls[0]["context"] is ownership
    assert cleanup_calls[0]["docker_endpoint"] == DOCKER_ENDPOINT
    assert cleanup_calls[0]["expected_daemon_id"] == "daemon-id"
    assert reseals[0]["disposition"] == "OWNED_VOLUME_CLEANUP_COMPLETED"


def test_cleanup_handler_preserves_completed_cleanup_if_reseal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ownership = SimpleNamespace(run_id=RUN_ID, is_authentic=lambda: True)
    monkeypatch.setattr(
        cli_module,
        "load_authenticated_ownership_context",
        lambda *_args, **_kwargs: ownership,
    )
    monkeypatch.setattr(
        cli_module,
        "collect_direct_stop_docker_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            daemon_available=True,
            context_name="desktop-linux",
            docker_endpoint=DOCKER_ENDPOINT,
            daemon_id="daemon-id",
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "cleanup_owned_named_volumes",
        lambda *_args, **_kwargs: SimpleNamespace(
            result=cli_module.TerminalResult(
                outcome=cli_module.Outcome.SUCCESS,
                reason_code="OWNED_NAMED_VOLUMES_CLEANED",
            ),
            removed_volume_names=("owned-volume",),
        ),
    )
    report_root = tmp_path / "artifacts" / "reports" / RUN_ID
    report_root.mkdir(parents=True)
    (report_root / "smoke-report.json").write_text("{}", encoding="utf-8")
    (report_root / "checksums.sha256").write_text(
        "a" * 64 + "  observer-visible/file.json\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli_module,
        "reseal_recovery_evidence",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("disk")),
    )
    context = cli_module.HandlerContext(
        runner=SimpleNamespace(),
        project_root=tmp_path,
        artifacts_root=tmp_path / "artifacts",
    )

    result = cli_module._handle_cleanup_owned_volumes(
        SimpleNamespace(run_id=RUN_ID),
        context,
    )

    assert result.outcome is cli_module.Outcome.MANUAL_INTERVENTION_REQUIRED
    assert result.reason_code == "RECOVERY_EVIDENCE_PERSISTENCE_FAILED"
    assert result.owned_volume_cleanup_completed
