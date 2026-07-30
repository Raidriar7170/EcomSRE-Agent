"""Production composition for one fresh, in-process Phase 0 preflight."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ecomsre.environment.lifecycle import (
    ComposeAction,
    ExpectedPortBinding,
    LifecycleRunner,
    _discover_verified_resources,
    build_compose_invocation,
    build_image_inspection_invocations,
    parse_expected_port_bindings,
)
from ecomsre.environment.manifests import (
    ImageLockStatus,
    LockMatchChecks,
    LockVerification,
    load_image_lock,
    verify_acceptance_image_lock,
)
from ecomsre.environment.ownership import verify_owned_resources
from ecomsre.environment.ownership import (
    OwnershipError,
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
)
from ecomsre.environment.ownership_authority import (
    AuthenticatedOwnershipContext,
    OwnershipAuthorityError,
    load_authenticated_ownership_context,
)
from ecomsre.environment.preflight import (
    AuthenticatedPreflightEvidence,
    CommandResult,
    OwnershipProof,
    PortObservation,
    PreflightInputs,
    PreflightCollectionError,
    ResourceObservation,
    collect_docker_snapshot,
    collect_host_snapshot,
    issue_authenticated_preflight_evidence,
    parse_cached_images,
    parse_port_observation,
    parse_resolved_compose_config,
)
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.evidence.hashes import canonical_json_sha256
from ecomsre.phase0.models import Outcome
from ecomsre.environment.preflight import docker_host_prefix


_FRESH_STOP_AUTHORITY_TOKEN = object()
_OBSERVER_RESOURCE_LABEL_ALLOWLIST = frozenset(
    {
        "com.docker.compose.project",
        "com.docker.compose.service",
        PROJECT_LABEL,
        RUN_LABEL,
    }
)


@dataclass(frozen=True, slots=True)
class FreshStopAuthority:
    """Narrow authority for one owned stop, independent of telemetry/capacity."""

    run_id: str
    docker_endpoint: str
    daemon_id: str
    manifest_sha256: str
    resources_sha256: str
    evidence_artifact: str
    evidence_sha256: str
    _token: object = field(repr=False, compare=False)

    def is_authentic(self, ownership: AuthenticatedOwnershipContext) -> bool:
        return (
            self._token is _FRESH_STOP_AUTHORITY_TOKEN
            and ownership.is_authentic()
            and self.run_id == ownership.run_id
            and self.manifest_sha256 == ownership.manifest_sha256
            and bool(self.docker_endpoint)
            and bool(self.daemon_id)
        )


def collect_fresh_stop_authority(
    *,
    project_root: Path,
    artifacts_root: Path,
    runner: LifecycleRunner,
    ownership: AuthenticatedOwnershipContext,
    expected_docker_endpoint: str,
    expected_daemon_id: str,
) -> FreshStopAuthority:
    """Revalidate only daemon identity and the exact owned resource manifest."""
    if (
        not ownership.is_authentic()
        or not expected_docker_endpoint
        or not expected_daemon_id
    ):
        raise OwnershipAuthorityError("fresh stop authority inputs are invalid")
    arguments = (
        *docker_host_prefix(expected_docker_endpoint),
        "info",
        "--format",
        "{{json .ID}}",
    )
    result = runner.run(
        arguments,
        timeout_seconds=30,
        environment={"ECOMSRE_RUN_ID": ownership.run_id},
    )
    try:
        daemon_id = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise OwnershipAuthorityError(
            "fresh stop daemon identity is malformed"
        ) from error
    if result.exit_code != 0 or daemon_id != expected_daemon_id:
        raise OwnershipAuthorityError("fresh stop daemon identity changed")
    command_results: list[CommandResult] = []
    discovered = _discover_verified_resources(
        runner,
        project_root=project_root,
        run_id=ownership.run_id,
        docker_endpoint=expected_docker_endpoint,
        command_results=command_results,
    )
    try:
        verify_owned_resources(discovered, ownership.manifest)
    except (ValueError, OwnershipError) as error:
        raise OwnershipAuthorityError(
            "fresh stop resource identity changed"
        ) from error
    resources_payload = [
        {
            "kind": resource.kind,
            "name": resource.name,
            "resource_id": resource.resource_id,
            "labels": _observer_resource_labels(resource),
            "identity_evidence": list(resource.identity_evidence),
        }
        for resource in discovered
    ]
    resources_sha256 = canonical_json_sha256(resources_payload)
    payload = {
        "schema_version": "phase0.fresh-stop-authority.v1",
        "run_id": ownership.run_id,
        "docker_endpoint": expected_docker_endpoint,
        "daemon_id": expected_daemon_id,
        "manifest_sha256": ownership.manifest_sha256,
        "resources_sha256": resources_sha256,
        "resources": resources_payload,
        "telemetry_required": False,
        "host_capacity_required": False,
        "image_lock_required": False,
    }
    with ObserverEvidenceStore(artifacts_root, ownership.run_id) as store:
        artifact = store.write_immutable(
            f"lifecycle/stop-authority/{time.monotonic_ns()}.json",
            payload,
        )
    return FreshStopAuthority(
        run_id=ownership.run_id,
        docker_endpoint=expected_docker_endpoint,
        daemon_id=expected_daemon_id,
        manifest_sha256=ownership.manifest_sha256,
        resources_sha256=resources_sha256,
        evidence_artifact=str(artifact.path),
        evidence_sha256=artifact.sha256,
        _token=_FRESH_STOP_AUTHORITY_TOKEN,
    )


def collect_fresh_preflight(
    *,
    project_root: Path,
    artifacts_root: Path,
    run_id: str,
    runner: LifecycleRunner,
) -> AuthenticatedPreflightEvidence:
    """Collect, classify, sign, and persist current host/Docker facts."""
    started_ns = time.monotonic_ns()
    host = collect_host_snapshot(runner)
    docker = collect_docker_snapshot(runner)
    lock = load_image_lock(project_root / "config" / "phase0" / "image-lock.json")
    ownership = _load_optional_ownership(artifacts_root, run_id)
    observed_upstream_commit = collect_upstream_commit(
        runner,
        project_root=project_root,
        run_id=run_id,
    )

    resolved_hash = lock.compose_config_sha256 or ("0" * 64)
    expected_hash = resolved_hash
    verification = _uninitialized_verification()
    ports: tuple[PortObservation, ...] = ()
    resources: tuple[ResourceObservation, ...] = ()

    if docker.daemon_available and docker.endpoint:
        config = build_compose_invocation(
            ComposeAction.CONFIG,
            project_root=project_root,
            run_id=run_id,
            docker_endpoint=docker.endpoint,
        )
        config_result = runner.run(
            config.arguments,
            timeout_seconds=config.timeout_seconds,
            environment=config.environment,
        )
        resolved = parse_resolved_compose_config(config_result)
        expected_bindings = parse_expected_port_bindings(resolved)
        resolved_hash = resolved.sha256
        expected_hash = lock.compose_config_sha256 or resolved.sha256

        command_results: list[CommandResult] = []
        discovered = _discover_verified_resources(
            runner,
            project_root=project_root,
            run_id=run_id,
            docker_endpoint=docker.endpoint,
            command_results=command_results,
        )
        _reject_relevant_resource_conflicts(
            runner,
            docker_endpoint=docker.endpoint,
            run_id=run_id,
            discovered=discovered,
        )
        if ownership is None:
            if discovered:
                raise OwnershipAuthorityError(
                    "project resources exist without authenticated ownership"
                )
        else:
            verify_owned_resources(discovered, ownership.manifest)
            _owned_ports, resources = _observations_from_owned(
                ownership,
                discovered,
            )
        ports = _collect_fixed_port_observations(
            runner,
            bindings=expected_bindings,
            ownership=ownership,
            discovered=discovered,
            run_id=run_id,
        )

        if lock.status is ImageLockStatus.LOCKED:
            cached = []
            for invocation in build_image_inspection_invocations(
                lock,
                run_id=run_id,
                docker_endpoint=docker.endpoint,
            ):
                result = runner.run(
                    invocation.arguments,
                    timeout_seconds=invocation.timeout_seconds,
                    environment=invocation.environment,
                )
                cached.extend(parse_cached_images(result))
            verification = verify_acceptance_image_lock(
                lock,
                cached_images=tuple(cached),
                observed_upstream_commit=observed_upstream_commit,
                observed_compose_config_sha256=resolved.sha256,
            )

    inputs = PreflightInputs(
        host=host,
        docker=docker,
        ports=ports,
        resources=resources,
        ownership_context=ownership,
        observed_upstream_commit=observed_upstream_commit,
        observed_compose_config_sha256=resolved_hash,
        expected_compose_config_sha256=expected_hash,
        image_lock_verification=verification,
        pull_policy="never",
    )
    finished_ns = time.monotonic_ns()
    evidence = issue_authenticated_preflight_evidence(
        run_id=run_id,
        inputs=inputs,
        collected_at=datetime.now(UTC),
        monotonic_started_ns=started_ns,
        monotonic_finished_ns=finished_ns,
    )
    _persist_preflight(
        artifacts_root=artifacts_root,
        evidence=evidence,
        sequence=finished_ns,
    )
    return evidence


def _load_optional_ownership(
    artifacts_root: Path,
    run_id: str,
) -> AuthenticatedOwnershipContext | None:
    authority_paths = (
        artifacts_root
        / "observer-visible"
        / run_id
        / "resource-ownership.json",
        artifacts_root
        / "evaluator-only"
        / run_id
        / "ownership-anchor.json",
        artifacts_root
        / "evaluator-only"
        / run_id
        / ".ownership-anchor.key",
    )
    if not any(path.exists() or path.is_symlink() for path in authority_paths):
        return None
    try:
        return load_authenticated_ownership_context(artifacts_root, run_id)
    except OwnershipAuthorityError:
        raise


def collect_upstream_commit(
    runner: LifecycleRunner,
    *,
    project_root: Path,
    run_id: str,
) -> str:
    upstream = Path(project_root).resolve() / "third_party" / "opentelemetry-demo"
    arguments = ("git", "-C", str(upstream), "rev-parse", "HEAD")
    result = runner.run(
        arguments,
        timeout_seconds=10,
        environment={"ECOMSRE_RUN_ID": run_id},
    )
    observed = result.stdout.strip().lower()
    if (
        result.exit_code != 0
        or re.fullmatch(r"[0-9a-f]{40}", observed) is None
    ):
        raise PreflightCollectionError(
            "PREFLIGHT_BLOCKED: upstream commit could not be observed"
        )
    return observed


def _collect_fixed_port_observations(
    runner: LifecycleRunner,
    *,
    bindings: tuple[ExpectedPortBinding, ...],
    ownership: AuthenticatedOwnershipContext | None,
    discovered: tuple[OwnedResource, ...],
    run_id: str,
) -> tuple[PortObservation, ...]:
    owned_ports: dict[int, PortObservation] = {}
    if ownership is not None:
        observed_owned_ports, _resources = _observations_from_owned(
            ownership,
            discovered,
        )
        owned_ports = {item.port: item for item in observed_owned_ports}
    observations: list[PortObservation] = []
    seen: set[int] = set()
    for binding in bindings:
        if binding.published_port is None or binding.published_port in seen:
            continue
        port = binding.published_port
        seen.add(port)
        arguments = (
            "lsof",
            "-nP",
            "-F",
            "pcn",
            f"-iTCP:{port}",
            "-sTCP:LISTEN",
        )
        result = runner.run(
            arguments,
            timeout_seconds=10,
            environment={"ECOMSRE_RUN_ID": run_id},
        )
        observed = parse_port_observation(
            result,
            port=port,
            owned_processes={},
            manifest_sha256=(
                ownership.manifest_sha256 if ownership is not None else "0" * 64
            ),
            active_run_id=run_id,
            manifest=(ownership.manifest if ownership is not None else None),
        )
        owned = owned_ports.get(port)
        if owned is not None:
            if not observed.occupied:
                observations.append(
                    PortObservation(
                        port=port,
                        occupied=True,
                        ownership="UNKNOWN",
                    )
                )
            else:
                observations.append(owned)
        else:
            observations.append(observed)
    return tuple(sorted(observations, key=lambda item: item.port))


def _reject_relevant_resource_conflicts(
    runner: LifecycleRunner,
    *,
    docker_endpoint: str,
    run_id: str,
    discovered: tuple[OwnedResource, ...],
) -> None:
    prefix = ("docker", "--host", docker_endpoint)
    arguments_by_kind = {
        "container": (
            *prefix,
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--format",
            "{{json .}}",
        ),
        "network": (
            *prefix,
            "network",
            "ls",
            "--no-trunc",
            "--format",
            "{{json .}}",
        ),
        "volume": (
            *prefix,
            "volume",
            "ls",
            "--format",
            "{{json .}}",
        ),
    }
    known = {
        (resource.kind, resource.name, resource.resource_id)
        for resource in discovered
        if resource.kind != "port"
    }
    for kind, arguments in arguments_by_kind.items():
        result = runner.run(
            arguments,
            timeout_seconds=30,
            environment={"ECOMSRE_RUN_ID": run_id},
        )
        if result.exit_code != 0:
            raise PreflightCollectionError(
                "PREFLIGHT_BLOCKED: broad Docker conflict discovery failed"
            )
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise PreflightCollectionError(
                    "PREFLIGHT_BLOCKED: Docker conflict listing is malformed"
                ) from error
            name = str(payload.get("Names") or payload.get("Name") or "")
            resource_id = str(
                payload.get("ID") or (name if kind == "volume" else "")
            )
            labels = _parse_listing_labels(str(payload.get("Labels", "")))
            relevant = (
                name == PROJECT_NAMESPACE
                or name.startswith(PROJECT_NAMESPACE + "-")
                or labels.get(PROJECT_LABEL) == PROJECT_NAMESPACE
                or labels.get("com.docker.compose.project") == PROJECT_NAMESPACE
            )
            if relevant and (kind, name, resource_id) not in known:
                raise OwnershipAuthorityError(
                    "relevant Docker namespace/name/resource conflict is unowned"
                )


def _parse_listing_labels(value: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for segment in value.split(","):
        if "=" not in segment:
            continue
        key, label_value = segment.split("=", 1)
        labels[key.strip()] = label_value.strip()
    return labels


def _uninitialized_verification() -> LockVerification:
    return LockVerification(
        passed=False,
        outcome=Outcome.BLOCKED_UPSTREAM,
        reason_codes=("INPUT_NOT_FROZEN",),
        checks=LockMatchChecks(
            source_references=False,
            digests=False,
            platforms=False,
            image_ids=False,
            upstream_binding=True,
            compose_binding=False,
            complete_inventory=False,
        ),
    )


def _observations_from_owned(
    context: AuthenticatedOwnershipContext,
    discovered: tuple,
) -> tuple[tuple[PortObservation, ...], tuple[ResourceObservation, ...]]:
    ports: list[PortObservation] = []
    resources: list[ResourceObservation] = []
    for resource in discovered:
        proof = OwnershipProof(
            project_namespace=context.project_name,
            manifest_sha256=context.manifest_sha256,
            run_id=context.run_id,
            resource_kind=resource.kind,
            resource_id=resource.resource_id,
            port=(
                _published_port(resource.identity_evidence)
                if resource.kind == "port"
                else None
            ),
            identifiers=resource.identity_evidence,
        )
        if resource.kind == "port":
            assert proof.port is not None
            ports.append(
                PortObservation(
                    port=proof.port,
                    occupied=True,
                    ownership="OWNED",
                    ownership_proof=proof,
                )
            )
        else:
            resources.append(
                ResourceObservation(
                    kind=resource.kind,
                    name=resource.name,
                    resource_id=resource.resource_id,
                    labels=_observer_resource_labels(resource),
                    ownership="OWNED",
                    ownership_proof=proof,
                )
            )
    return tuple(ports), tuple(resources)


def _observer_resource_labels(resource: OwnedResource) -> dict[str, str]:
    """Expose only stable ownership identity labels to observer evidence."""
    labels = {
        key: value
        for key, value in resource.labels.items()
        if key in _OBSERVER_RESOURCE_LABEL_ALLOWLIST
    }
    if (
        labels.get(PROJECT_LABEL) != PROJECT_NAMESPACE
        or labels.get(RUN_LABEL) is None
    ):
        raise OwnershipAuthorityError(
            "observer resource evidence lacks canonical ownership labels"
        )
    return labels


def _published_port(identifiers: tuple[str, ...]) -> int:
    values = [
        value.removeprefix("published_port:")
        for value in identifiers
        if value.startswith("published_port:")
    ]
    if len(values) != 1 or not values[0].isdigit():
        raise ValueError("owned port lacks one published port")
    return int(values[0])


def _persist_preflight(
    *,
    artifacts_root: Path,
    evidence: AuthenticatedPreflightEvidence,
    sequence: int,
) -> None:
    payload = {
        "schema_version": "phase0.preflight-snapshot.v1",
        "run_id": evidence.run_id,
        "content_sha256": evidence.content_sha256,
        "result": evidence.result.model_dump(mode="json"),
        "host": evidence.inputs.host.model_dump(mode="json"),
        "docker": evidence.inputs.docker.model_dump(mode="json"),
        "ports": [item.model_dump(mode="json") for item in evidence.inputs.ports],
        "resources": [
            item.model_dump(mode="json") for item in evidence.inputs.resources
        ],
        "observed_upstream_commit": evidence.inputs.observed_upstream_commit,
        "observed_compose_config_sha256": (
            evidence.inputs.observed_compose_config_sha256
        ),
        "expected_compose_config_sha256": (
            evidence.inputs.expected_compose_config_sha256
        ),
        "image_lock_verification": (
            evidence.inputs.image_lock_verification.model_dump(mode="json")
        ),
        "pull_policy": evidence.inputs.pull_policy,
    }
    with ObserverEvidenceStore(artifacts_root, evidence.run_id) as store:
        if not (store.root / "machine-manifest.json").exists():
            store.write_immutable(
                "machine-manifest.json",
                {
                    "schema_version": "phase0.machine-snapshot.v1",
                    "run_id": evidence.run_id,
                    "host": payload["host"],
                    "docker": payload["docker"],
                },
            )
        if not (store.root / "environment-manifest.json").exists():
            store.write_immutable(
                "environment-manifest.json",
                {
                    "schema_version": "phase0.environment-snapshot.v1",
                    "run_id": evidence.run_id,
                    "ports": payload["ports"],
                    "resources": payload["resources"],
                    "preflight_outcome": evidence.result.outcome.value,
                },
            )
        store.write_immutable(
            f"lifecycle/preflight/{sequence}.json",
            payload,
        )
