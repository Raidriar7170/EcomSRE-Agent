#!/usr/bin/env python3
"""Run one append-only Product v0.2.3.2.1 live traffic preflight."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Literal, Mapping, Sequence

from pydantic import TypeAdapter

from ecomsre.dta_v2.tool_contracts import (
    HealthState,
    RuntimeRecord,
    RuntimeState,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.baseline_readiness_v021 import verify_queue_default_v021
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    HealthyTrafficRunnerV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
)
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateSourceV0232,
    admit_product_state_source_v0232,
)
from ecomsre.product.pilot.product_state_clone_v02321 import (
    PreflightStateCloneReportV02321,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
    load_preserved_runtime_inputs_v0231,
)
from ecomsre.product.pilot.traffic_harness_closure_v02321 import (
    DemoCleanupObservationV02321,
    InfrastructureSessionCompletionV02321,
    InfrastructureSessionStartV02321,
    OwnedResourceCountsV02321,
    ProductCleanupObservationV02321,
    TrafficDispatchFailureEvidenceV02321,
    TrafficHarnessClosureContractV02321,
    TrafficHarnessClosureV02321,
    TrafficHarnessStageV02321,
    TrafficPreflightAttemptCompletionV02321,
    TrafficPreflightAttemptStartV02321,
    TrafficPreflightEventV02321,
    TrafficPreflightLedgerV02321,
    append_traffic_preflight_event_file_v02321,
    bind_changed_source_files_v02321,
    invoke_first_cart_transport_v02321,
    request_sandbox_start_v02321,
)
from ecomsre.product.pilot.traffic_preflight_live_v02321 import (
    TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321,
    LiveTrafficPreflightAttemptV02321,
    LiveTrafficPreflightPassV02321,
)
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    load_traffic_profile_v0232,
)
from ecomsre.product.pilot.typed_request_plan_v02321 import (
    TrafficHarnessTypedRequestPlanV02321,
    build_traffic_harness_typed_request_plan_v02321,
    materialize_planned_request_v02321,
)
from ecomsre_live_sandbox.contracts import (
    canonical_json_bytes,
    load_bundle,
    write_private_json,
)
from ecomsre_live_sandbox.environment import DockerSnapshot
from scripts.ci.verify_product_v02321_history import verify_product_v02321_history
from scripts.ci.verify_product_v0232_history import SOURCE_LOCATOR_V0232
from scripts.product_v0232.run_state_clone import (
    BASELINE_ID_V0232,
    BASELINE_SHA256_V0232,
    ENVIRONMENT_ID_V0232,
    PILOT_RUNTIME_AUTHORITY_SHA256_V0232,
    PROFILE_SHA256_V0232,
    RUNTIME_CONNECTOR_BINDING_SHA256_V0232,
    _require_fixed_source_root,
    _require_source_unowned,
)
from scripts.product_v02321.run_harness_contract_preflight import (
    _load_successor_campaign_sha256,
    run_harness_contract_preflight_v02321,
)


_ENDPOINT_V02321 = "http://127.0.0.1:18080/api/checkout"
_PRIVATE_LEDGER_ID = "campaign"
_ATTEMPT_LABEL_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"
_PREDECESSOR_HEAD_V02321 = "cc270e5624af573a12bc31f3df9ca8cacad8685d"
_INITIAL_CHANGED_SOURCES = (
    "scripts/product_v02321/run_traffic_preflight.py",
    "src/ecomsre/product/pilot/traffic_preflight_live_v02321.py",
)
_PROFILE_SOURCE_PATHS_V02321 = {
    "PREFLIGHT": "config/product-v0232/traffic/preflight-profile.json",
    "FORMAL": "config/product-v0232/traffic/formal-profile.json",
}
_PRE_SESSION_START_FIELDS_V02321 = frozenset(
    {
        "schema_version",
        "attempt_label",
        "reservation_ordinal",
        "prior_pre_session_sha256",
        "prior_evidence_path",
        "prior_evidence_file_sha256",
        "changed_surface",
        "changed_source_bindings",
        "changed_implementation_sha256",
        "repair_rationale",
        "infrastructure_session_count_before",
        "traffic_attempt_count_before",
        "formal_healthy_traffic_execution_count",
        "accepted_successor_incident_count",
        "successor_diagnosis_count",
        "action_authority",
        "reservation_sha256",
    }
)
_PRE_SESSION_COMPLETION_FIELDS_V02321 = frozenset(
    {
        "schema_version",
        "terminal",
        "attempt_label",
        "reservation_sha256",
        "session_id",
        "session_start_sha256",
        "infrastructure_session_count_after",
        "traffic_attempt_count_before",
        "formal_healthy_traffic_execution_count",
        "accepted_successor_incident_count",
        "successor_diagnosis_count",
        "action_authority",
        "completion_sha256",
    }
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.2.1 JSON object differs: {path.name}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_public_exact_or_create(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = canonical_json_bytes(dict(payload))
    if path.is_symlink():
        raise FileExistsError(f"Product v0.2.3.2.1 report exists: {path.name}")
    if path.exists():
        if not path.is_file() or path.read_bytes() != expected:
            raise FileExistsError(
                f"Product v0.2.3.2.1 report differs: {path.name}"
            )
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_public(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Product v0.2.3.2.1 report is a symlink: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(payload)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publication_bundle(
    *,
    attempt_label: str,
    terminal: str,
    ledger_tail: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    attempt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.preflight-publication.v02321",
        "terminal": terminal,
        "attempt_label": attempt_label,
        "ledger_tail": list(ledger_tail),
        "artifacts": list(artifacts),
        "attempt": dict(attempt) if attempt is not None else None,
    }
    return {**body, "publication_sha256": semantic_sha256_v22(body)}


def _persist_ledger_tail(
    root: Path,
    *,
    tail_payloads: object,
) -> TrafficPreflightLedgerV02321:
    if not isinstance(tail_payloads, list) or not tail_payloads:
        raise ValueError("Product v0.2.3.2.1 publication ledger tail differs")
    adapter: TypeAdapter[TrafficPreflightEventV02321] = TypeAdapter(
        TrafficPreflightEventV02321
    )
    tail = tuple(adapter.validate_python(item) for item in tail_payloads)
    if any(
        event.event_ordinal != tail[0].event_ordinal + offset
        for offset, event in enumerate(tail)
    ):
        raise ValueError("Product v0.2.3.2.1 publication ledger tail differs")

    persisted = list(_load_private_ledger(root).events)
    if len(persisted) > tail[-1].event_ordinal:
        raise ValueError("Product v0.2.3.2.1 publication was superseded")
    for event in tail:
        index = event.event_ordinal - 1
        if index < len(persisted):
            if persisted[index] != event:
                raise ValueError(
                    "Product v0.2.3.2.1 publication ledger event differs"
                )
            continue
        if index != len(persisted):
            raise ValueError("Product v0.2.3.2.1 publication ledger tail differs")
        append_traffic_preflight_event_file_v02321(
            root, _PRIVATE_LEDGER_ID, event
        )
        persisted.append(event)
    return TrafficPreflightLedgerV02321.build(events=tuple(persisted))


def _publish_publication_bundle(
    root: Path,
    bundle: Mapping[str, Any],
) -> LiveTrafficPreflightAttemptV02321 | None:
    body = dict(bundle)
    supplied = body.pop("publication_sha256", None)
    artifacts = body.get("artifacts")
    attempt_label = body.get("attempt_label")
    if (
        supplied != semantic_sha256_v22(body)
        or not isinstance(artifacts, list)
        or not isinstance(attempt_label, str)
        or re.fullmatch(_ATTEMPT_LABEL_PATTERN, attempt_label) is None
    ):
        raise ValueError("Product v0.2.3.2.1 publication bundle differs")
    ledger = _persist_ledger_tail(
        root,
        tail_payloads=body.get("ledger_tail"),
    )
    ledger_artifacts = [
        item
        for item in artifacts
        if isinstance(item, Mapping)
        and item.get("path")
        == "docs/analysis/product-v02321-traffic-preflight-ledger.json"
    ]
    if (
        len(ledger_artifacts) != 1
        or ledger_artifacts[0].get("payload")
        != ledger.model_dump(mode="json")
    ):
        raise ValueError("Product v0.2.3.2.1 publication ledger differs")
    for item in artifacts:
        if not isinstance(item, Mapping):
            raise ValueError("Product v0.2.3.2.1 publication entry differs")
        relative = item.get("path")
        mode = item.get("mode")
        payload = item.get("payload")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(payload, Mapping)
            or mode not in {"CREATE_EXACT", "REPLACE"}
        ):
            raise ValueError("Product v0.2.3.2.1 publication entry differs")
        destination = root / relative
        if mode == "CREATE_EXACT":
            _write_public_exact_or_create(destination, payload)
        else:
            _replace_public(destination, payload)
    attempt_payload = body.get("attempt")
    if attempt_payload is None:
        return None
    return LiveTrafficPreflightAttemptV02321.model_validate(attempt_payload)


def _event_sha256(event: TrafficPreflightEventV02321) -> str:
    if isinstance(event, TrafficHarnessClosureV02321):
        return event.closure_sha256
    return event.event_sha256


def _event_meta(
    events: Sequence[TrafficPreflightEventV02321],
) -> dict[str, object]:
    return {
        "event_ordinal": len(events) + 1,
        "prior_event_sha256": _event_sha256(events[-1]) if events else None,
        "observed_at_utc": datetime.now(UTC).isoformat(),
    }


def _load_private_ledger(root: Path) -> TrafficPreflightLedgerV02321:
    ledger_root = (
        root
        / ".local/product-v02321/traffic-preflight"
        / _PRIVATE_LEDGER_ID
        / "ledger"
    )
    if not ledger_root.exists():
        return TrafficPreflightLedgerV02321.build(events=())
    if ledger_root.is_symlink() or not ledger_root.is_dir():
        raise ValueError("Product v0.2.3.2.1 private ledger root differs")
    adapter: TypeAdapter[TrafficPreflightEventV02321] = TypeAdapter(
        TrafficPreflightEventV02321
    )
    events: list[TrafficPreflightEventV02321] = []
    for ordinal, path in enumerate(sorted(ledger_root.iterdir()), start=1):
        if (
            path.is_symlink()
            or not path.is_file()
            or path.name != f"event-{ordinal:06d}.json"
        ):
            raise ValueError("Product v0.2.3.2.1 private ledger sequence differs")
        events.append(adapter.validate_json(path.read_bytes()))
    return TrafficPreflightLedgerV02321.build(events=tuple(events))


def _append_event(
    root: Path,
    events: list[TrafficPreflightEventV02321],
    event: TrafficPreflightEventV02321,
) -> None:
    append_traffic_preflight_event_file_v02321(
        root, _PRIVATE_LEDGER_ID, event
    )
    events.append(event)


def _admit_state(path: Path, *, locator: str) -> ProductStateSourceV0232:
    return admit_product_state_source_v0232(
        path,
        source_locator=locator,
        expected_environment_id=ENVIRONMENT_ID_V0232,
        expected_baseline_id=BASELINE_ID_V0232,
        expected_baseline_sha256=BASELINE_SHA256_V0232,
        expected_profile_sha256=PROFILE_SHA256_V0232,
        expected_pilot_runtime_authority_sha256=(
            PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        ),
        expected_runtime_connector_binding_sha256=(
            RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        ),
    )


def _admit_source_state(source_root: Path) -> ProductStateSourceV0232:
    _require_fixed_source_root(source_root)
    _require_source_unowned(source_root / "product.sqlite3")
    return _admit_state(source_root, locator=SOURCE_LOCATOR_V0232)


def _admit_clone_state(
    root: Path, report: PreflightStateCloneReportV02321
) -> tuple[ProductStateSourceV0232, Path]:
    clone_root = root / report.destination_locator
    state = _admit_state(clone_root, locator=report.destination_locator)
    if state != report.destination_state:
        raise ValueError("Product v0.2.3.2.1 clone admission differs")
    return state, clone_root


def _database_owner_count(database: Path) -> int:
    result = subprocess.run(
        ("lsof", "-F", "p", str(database)),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Product-state owner observation failed")
    return len(
        {
            line[1:]
            for line in result.stdout.splitlines()
            if line.startswith("p") and line[1:].isdigit()
        }
    )


def _verify_profile_binding(
    root: Path, *, role: Literal["PREFLIGHT", "FORMAL"]
) -> str:
    filename = (
        "preflight-profile-binding.json"
        if role == "PREFLIGHT"
        else "formal-profile-binding.json"
    )
    payload = _load_object(root / "config/product-v02321/traffic" / filename)
    body = dict(payload)
    supplied = body.pop("binding_sha256", None)
    source_path = payload.get("source_path")
    if (
        supplied != semantic_sha256_v22(body)
        or payload.get("schema_version")
        != "ecomsre.product.traffic-profile-binding.v02321"
        or payload.get("role") != role
        or source_path != _PROFILE_SOURCE_PATHS_V02321[role]
        or payload.get("predecessor_head") != _PREDECESSOR_HEAD_V02321
        or not isinstance(source_path, str)
        or _sha256_file(root / source_path) != payload.get("source_file_sha256")
        or load_traffic_profile_v0232(root, role=role).profile_sha256
        != payload.get("profile_sha256")
    ):
        raise ValueError(f"Product v0.2.3.2.1 {role} profile binding differs")
    assert isinstance(supplied, str)
    return supplied


def _load_sealed_private_object(
    path: Path,
    *,
    digest_field: str,
    schema_version: str,
) -> dict[str, Any]:
    payload = _load_object(path)
    body = dict(payload)
    supplied = body.pop(digest_field, None)
    if (
        payload.get("schema_version") != schema_version
        or supplied != semantic_sha256_v22(body)
    ):
        raise ValueError(f"Product v0.2.3.2.1 private evidence differs: {path.name}")
    return payload


def _write_checkpoint(
    path: Path,
    *,
    schema_version: str,
    digest_field: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    body = {"schema_version": schema_version, **dict(payload)}
    sealed = {**body, digest_field: semantic_sha256_v22(body)}
    _write_public_exact_or_create(path, sealed)
    return sealed


def _write_docker_baseline_snapshot(
    path: Path, snapshot: DockerSnapshot
) -> dict[str, Any]:
    return _write_checkpoint(
        path,
        schema_version="ecomsre.product.docker-baseline-snapshot.v02321",
        digest_field="snapshot_sha256",
        payload={
            "containers": sorted(snapshot.containers),
            "networks": sorted(snapshot.networks),
            "volumes": sorted(snapshot.volumes),
        },
    )


def _load_docker_baseline_snapshot(path: Path) -> DockerSnapshot:
    payload = _load_sealed_private_object(
        path,
        digest_field="snapshot_sha256",
        schema_version="ecomsre.product.docker-baseline-snapshot.v02321",
    )
    collections = tuple(payload.get(name) for name in ("containers", "networks", "volumes"))
    if any(
        not isinstance(items, list)
        or not all(isinstance(item, str) for item in items)
        or items != sorted(set(items))
        for items in collections
    ):
        raise ValueError("Product v0.2.3.2.1 Docker snapshot differs")
    containers, networks, volumes = collections
    assert isinstance(containers, list)
    assert isinstance(networks, list)
    assert isinstance(volumes, list)
    return DockerSnapshot(
        containers=frozenset(containers),
        networks=frozenset(networks),
        volumes=frozenset(volumes),
    )


def _require_reserved_private_root(private_root: Path) -> None:
    if private_root.is_symlink() or not private_root.is_dir():
        raise ValueError("Product v0.2.3.2.1 reserved private root differs")


def _require_preserved_runtime_root_v02321(
    preserved_runtime_root: Path,
    source_product_root: Path,
) -> None:
    try:
        expected_source_root = (
            preserved_runtime_root / SOURCE_LOCATOR_V0232
        ).resolve(strict=True)
        observed_source_root = source_product_root.resolve(strict=True)
    except OSError as error:
        raise ValueError(
            "Product v0.2.3.2.1 preserved Runtime root differs"
        ) from error
    if expected_source_root != observed_source_root:
        raise ValueError("Product v0.2.3.2.1 preserved Runtime root differs")


def _pre_session_starts(root: Path) -> tuple[tuple[Path, dict[str, Any]], ...]:
    preflight_root = root / ".local/product-v02321/traffic-preflight"
    if not preflight_root.exists():
        return ()
    if preflight_root.is_symlink() or not preflight_root.is_dir():
        raise ValueError("Product v0.2.3.2.1 private preflight root differs")
    starts: list[tuple[Path, dict[str, Any]]] = []
    for child in preflight_root.iterdir():
        if child.name == _PRIVATE_LEDGER_ID:
            continue
        if child.is_symlink() or not child.is_dir():
            raise ValueError("Product v0.2.3.2.1 private attempt root differs")
        start_path = child / "pre-session-start.json"
        if not start_path.exists():
            continue
        if start_path.is_symlink() or not start_path.is_file():
            raise ValueError("Product v0.2.3.2.1 pre-session start differs")
        start = _load_sealed_private_object(
            start_path,
            digest_field="reservation_sha256",
            schema_version="ecomsre.product.pre-session-start.v02321",
        )
        bindings = start.get("changed_source_bindings")
        if (
            set(start) != _PRE_SESSION_START_FIELDS_V02321
            or start.get("attempt_label") != child.name
            or type(start.get("reservation_ordinal")) is not int
            or not isinstance(start.get("changed_surface"), str)
            or not start["changed_surface"]
            or not isinstance(bindings, list)
            or not bindings
            or not all(isinstance(item, dict) for item in bindings)
            or start.get("changed_implementation_sha256")
            != _changed_implementation_from_bindings(bindings)
            or not isinstance(start.get("repair_rationale"), str)
            or not start["repair_rationale"]
            or type(start.get("infrastructure_session_count_before")) is not int
            or start["infrastructure_session_count_before"] < 0
            or type(start.get("traffic_attempt_count_before")) is not int
            or start["traffic_attempt_count_before"] < 0
            or start.get("formal_healthy_traffic_execution_count") != 0
            or start.get("accepted_successor_incident_count") != 0
            or start.get("successor_diagnosis_count") != 0
            or start.get("action_authority") != "NONE"
        ):
            raise ValueError("Product v0.2.3.2.1 pre-session label differs")
        starts.append((child, start))
    starts.sort(key=lambda item: item[1].get("reservation_ordinal", -1))
    if [item[1].get("reservation_ordinal") for item in starts] != list(
        range(1, len(starts) + 1)
    ):
        raise ValueError("Product v0.2.3.2.1 pre-session sequence differs")
    return tuple(starts)


def _incomplete_pre_session(
    root: Path,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]] | None:
    starts = _pre_session_starts(root)
    candidates: list[tuple[Path, dict[str, Any], Path, dict[str, Any]]] = []
    for attempt_root, start in starts:
        completion_path = attempt_root / "pre-session-completion.json"
        publication_path = attempt_root / "publication-bundle.json"
        if completion_path.exists():
            completion = _load_sealed_private_object(
                completion_path,
                digest_field="completion_sha256",
                schema_version="ecomsre.product.pre-session-completion.v02321",
            )
            if completion.get("reservation_sha256") != start.get(
                "reservation_sha256"
            ):
                raise ValueError("Product v0.2.3.2.1 pre-session completion differs")
            continue
        if publication_path.exists():
            continue
        blocker_path = attempt_root / "pre-session-blocker.json"
        if blocker_path.exists():
            blocker = _load_sealed_private_object(
                blocker_path,
                digest_field="blocker_sha256",
                schema_version="ecomsre.product.pre-session-blocker.v02321",
            )
            if blocker.get("reservation_sha256") != start.get(
                "reservation_sha256"
            ):
                raise ValueError("Product v0.2.3.2.1 pre-session blocker differs")
            evidence_path = blocker_path
            evidence = blocker
        else:
            evidence_path = attempt_root / "pre-session-start.json"
            evidence = start
        candidates.append((attempt_root, start, evidence_path, evidence))

    frontier: list[tuple[Path, dict[str, Any], Path, dict[str, Any]]] = []
    matched_successor_ordinals: set[int] = set()
    for candidate in candidates:
        _, start, evidence_path, evidence = candidate
        evidence_sha256 = evidence.get("blocker_sha256") or evidence.get(
            "reservation_sha256"
        )
        successor_matches = tuple(
            later_start
            for _, later_start in starts
            if later_start["reservation_ordinal"] > start["reservation_ordinal"]
            and later_start.get("prior_pre_session_sha256") == evidence_sha256
        )
        if not successor_matches:
            frontier.append(candidate)
            continue
        if len(successor_matches) != 1:
            raise ValueError("Product v0.2.3.2.1 pre-session history differs")
        successor = successor_matches[0]
        successor_ordinal = successor["reservation_ordinal"]
        prior_evidence_path = successor.get("prior_evidence_path")
        prior_evidence_candidate = Path(str(prior_evidence_path))
        if not prior_evidence_candidate.is_absolute():
            raise ValueError("Product v0.2.3.2.1 pre-session history differs")
        try:
            resolved_prior_evidence = prior_evidence_candidate.resolve(strict=True)
            resolved_candidate_evidence = evidence_path.resolve(strict=True)
        except OSError as error:
            raise ValueError(
                "Product v0.2.3.2.1 pre-session history differs"
            ) from error
        if (
            successor_ordinal != start["reservation_ordinal"] + 1
            or successor_ordinal in matched_successor_ordinals
            or resolved_prior_evidence != resolved_candidate_evidence
            or successor.get("prior_evidence_file_sha256")
            != _sha256_file(evidence_path)
        ):
            raise ValueError("Product v0.2.3.2.1 pre-session history differs")
        matched_successor_ordinals.add(successor_ordinal)
    referenced_successor_ordinals = {
        start["reservation_ordinal"]
        for _, start in starts
        if start.get("prior_pre_session_sha256") is not None
    }
    if matched_successor_ordinals != referenced_successor_ordinals:
        raise ValueError("Product v0.2.3.2.1 pre-session history differs")
    if len(frontier) > 1:
        raise ValueError("Product v0.2.3.2.1 pre-session history differs")
    return frontier[0] if frontier else None


def _changed_implementation_from_bindings(
    bindings: Sequence[Mapping[str, Any]],
) -> str:
    return semantic_sha256_v22({"changed_source_bindings": list(bindings)})


def _prepare_pre_session_start(
    root: Path,
    *,
    attempt_label: str,
    prior_attempt: Path | None,
    changed_surface: str | None,
    changed_source_paths: tuple[str, ...],
    repair_rationale: str | None,
) -> dict[str, Any]:
    starts = _pre_session_starts(root)
    incomplete = _incomplete_pre_session(root)
    prior_ledger = _load_private_ledger(root)
    requires_repair = bool(prior_ledger.events) or incomplete is not None
    if requires_repair:
        if (
            prior_attempt is None
            or changed_surface in {None, "", "INITIAL"}
            or not changed_source_paths
            or not repair_rationale
        ):
            raise ValueError("successor preflight lacks prior/change evidence")
        sources = tuple(sorted(set(changed_source_paths)))
        effective_surface = str(changed_surface)
        effective_rationale = repair_rationale
    else:
        if prior_attempt is not None or changed_surface not in {None, "", "INITIAL"}:
            raise ValueError("initial preflight prior/change evidence differs")
        sources = _INITIAL_CHANGED_SOURCES
        effective_surface = "INITIAL"
        effective_rationale = (
            "initial successor admission after typed request and cleanup contracts "
            "passed offline"
        )

    changed_bindings = bind_changed_source_files_v02321(root, sources)
    changed_binding_payloads = [
        item.model_dump(mode="json") for item in changed_bindings
    ]
    changed_implementation_sha256 = _changed_implementation_from_bindings(
        changed_binding_payloads
    )
    if requires_repair and starts and changed_implementation_sha256 == starts[-1][
        1
    ].get("changed_implementation_sha256"):
        raise ValueError("identical pre-session replay is forbidden")
    prior_pre_session_sha256: str | None = None
    prior_evidence_path: str | None = None
    prior_evidence_file_sha256: str | None = None
    if prior_attempt is not None:
        resolved_prior = Path(prior_attempt).resolve(strict=True)
        prior_evidence_path = str(resolved_prior)
        prior_evidence_file_sha256 = _sha256_file(resolved_prior)
    if incomplete is not None:
        _, prior_start, expected_path, evidence = incomplete
        assert prior_attempt is not None
        if Path(prior_attempt).resolve(strict=True) != expected_path.resolve(strict=True):
            raise ValueError("successor preflight prior pre-session differs")
        prior_pre_session_sha256 = str(
            evidence.get("blocker_sha256")
            or evidence.get("reservation_sha256")
        )
        if prior_start != starts[-1][1]:
            raise ValueError("Product v0.2.3.2.1 pre-session history differs")

    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.pre-session-start.v02321",
        "attempt_label": attempt_label,
        "reservation_ordinal": len(starts) + 1,
        "prior_pre_session_sha256": prior_pre_session_sha256,
        "prior_evidence_path": prior_evidence_path,
        "prior_evidence_file_sha256": prior_evidence_file_sha256,
        "changed_surface": effective_surface,
        "changed_source_bindings": changed_binding_payloads,
        "changed_implementation_sha256": changed_implementation_sha256,
        "repair_rationale": effective_rationale,
        "infrastructure_session_count_before": (
            prior_ledger.infrastructure_session_count
        ),
        "traffic_attempt_count_before": prior_ledger.traffic_attempt_count,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "action_authority": "NONE",
    }
    return {**body, "reservation_sha256": semantic_sha256_v22(body)}


def _write_pre_session_blocker(
    private_root: Path,
    *,
    reservation: Mapping[str, Any],
    error: BaseException,
) -> None:
    body = {
        "schema_version": "ecomsre.product.pre-session-blocker.v02321",
        "terminal": "BLOCKED_ECOMSRE_PRODUCT_V02321_PRE_SESSION",
        "attempt_label": reservation["attempt_label"],
        "reservation_sha256": reservation["reservation_sha256"],
        "safe_error_code": _safe_error_code(error),
        "infrastructure_session_count": reservation[
            "infrastructure_session_count_before"
        ],
        "traffic_attempt_count": reservation["traffic_attempt_count_before"],
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "action_authority": "NONE",
    }
    write_private_json(
        private_root / "pre-session-blocker.json",
        {**body, "blocker_sha256": semantic_sha256_v22(body)},
        create_once=True,
    )


def _checkout_runtime(
    backend: Any,
    request: Any,
) -> tuple[str, bool, int]:
    result = backend.execute(request)
    records = tuple(item for item in result.records if type(item) is RuntimeRecord)
    if len(records) != 1 or records[0].logical_service != "checkout":
        raise RuntimeError("Product v0.2.3.2.1 checkout Runtime coverage differs")
    record = records[0]
    if (
        record.state is not RuntimeState.RUNNING
        or record.health is not HealthState.HEALTHY
        or record.restart_count != 0
    ):
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V02321_CHECKOUT_RUNTIME_NOT_HEALTHY"
        )
    return record.state.value, True, record.restart_count


def _safe_error_code(error: BaseException) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", type(error).__name__.upper()).strip(
        "_"
    )
    return f"LIVE_{normalized or 'ERROR'}"[:120]


def _product_cleanup_observation(
    payload: Mapping[str, Any] | None,
    *,
    database_owner_count_before: int | None,
    database_owner_count_after: int | None,
    safe_error_code: str | None = None,
) -> ProductCleanupObservationV02321:
    observed = payload or {}
    complete = payload is not None and safe_error_code is None
    clean = (
        complete
        and observed.get("verdict") == "CLEAN"
        and observed.get("owned_host_processes") == 0
        and observed.get("product_api_port_available") is True
        and observed.get("non_owned_resources_changed") is False
        and observed.get("launches") == ()
        and database_owner_count_before == 0
        and database_owner_count_after == 0
    )
    return ProductCleanupObservationV02321(
        observation_complete=complete,
        verdict="CLEAN" if clean else "BLOCKED",
        owned_host_processes=(
            observed.get("owned_host_processes") if payload is not None else None
        ),
        database_owner_count_before=database_owner_count_before,
        database_owner_count_after=database_owner_count_after,
        product_api_port_available=(
            observed.get("product_api_port_available")
            if payload is not None
            else None
        ),
        non_owned_resources_changed=(
            observed.get("non_owned_resources_changed")
            if payload is not None
            else None
        ),
        safe_error_code=None if clean else safe_error_code or "PRODUCT_CLEANUP_BLOCKED",
    )


def _demo_cleanup_observation(
    payload: Any | None,
    *,
    safe_error_code: str | None = None,
) -> DemoCleanupObservationV02321:
    complete = payload is not None and safe_error_code is None
    containers = getattr(payload, "owned_containers", None)
    networks = getattr(payload, "owned_networks", None)
    volumes = getattr(payload, "owned_volumes", None)
    non_owned = getattr(payload, "non_owned_resources_changed", None)
    clean = (
        complete
        and getattr(payload, "verdict", None) == "CLEAN"
        and containers == 0
        and networks == 0
        and volumes == 0
        and non_owned is False
    )
    return DemoCleanupObservationV02321(
        observation_complete=complete,
        verdict="CLEAN" if clean else "BLOCKED",
        owned_containers=containers,
        owned_networks=networks,
        owned_volumes=volumes,
        non_owned_resources_changed=non_owned,
        safe_error_code=None if clean else safe_error_code or "DEMO_CLEANUP_BLOCKED",
    )


def _first_failure_code(execution: HealthyTrafficExecutionV0232) -> str | None:
    failure = next(
        (item for item in execution.observations if not item.business_success),
        None,
    )
    if failure is None or failure.safe_error_code is None:
        return None
    return failure.safe_error_code.value


def _build_execution_completion(
    events: Sequence[TrafficPreflightEventV02321],
    *,
    attempt_start: TrafficPreflightAttemptStartV02321,
    execution: HealthyTrafficExecutionV0232,
) -> TrafficPreflightAttemptCompletionV02321:
    failure_code = _first_failure_code(execution)
    return TrafficPreflightAttemptCompletionV02321.build(
        **_event_meta(events),
        attempt_id=attempt_start.attempt_id,
        attempt_ordinal=attempt_start.attempt_ordinal,
        attempt_start_sha256=attempt_start.event_sha256,
        session_id=attempt_start.session_id,
        traffic_execution_sha256=execution.execution_sha256,
        traffic_dispatch_failure=None,
        stage=TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE,
        first_cart_transport_invoked=True,
        planned_transactions=10,
        completed_transactions=execution.run.completed_transactions,
        successful_transactions=execution.run.successful_transactions,
        failed_transactions=execution.run.failed_transactions,
        safe_error_code=failure_code,
        terminal="ATTEMPT_PASS" if execution.run.passed else "ATTEMPT_FAILED",
        monotonic_duration_ms=max(
            0,
            int(
                (execution.run.ended_at - execution.run.started_at).total_seconds()
                * 1000
            ),
        ),
    )


def _updated_progress(
    root: Path,
    *,
    terminal: str,
    ledger: TrafficPreflightLedgerV02321,
    attempt: LiveTrafficPreflightAttemptV02321 | None,
    preflight: LiveTrafficPreflightPassV02321 | None,
) -> dict[str, Any]:
    progress = _load_object(root / "docs/analysis/product-v02321-progress.json")
    supplied = progress.pop("progress_sha256", None)
    if supplied != semantic_sha256_v22(progress):
        raise ValueError("Product v0.2.3.2.1 progress digest differs")
    body: dict[str, Any] = {
        **progress,
        "terminal": terminal,
        "increment": 3,
        "infrastructure_session_count": ledger.infrastructure_session_count,
        "traffic_attempt_count": ledger.traffic_attempt_count,
        "live_traffic_preflight_attempt_count": ledger.traffic_attempt_count,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "action_authority": "NONE",
        "traffic_preflight_ledger_sha256": ledger.ledger_sha256,
    }
    if attempt is not None:
        body.update(
            {
                "live_request_plan_status": "PASS",
                "typed_request_plan_sha256": attempt.typed_request_plan_sha256,
                "product_state_clone_report_sha256": (
                    attempt.product_state_clone_report_sha256
                ),
                "product_state_clone_sha256": attempt.product_state_clone_sha256,
                "source_state_sha256": attempt.source_state_after_sha256,
                "traffic_preflight_attempt_sha256": attempt.attempt_sha256,
            }
        )
    if preflight is not None:
        body.update(
            {
                "live_traffic_preflight_status": "PASS",
                "traffic_preflight_sha256": preflight.preflight_sha256,
            }
        )
    return {**body, "progress_sha256": semantic_sha256_v22(body)}


def _recover_interrupted_traffic_preflight_v02321(
    *,
    root: Path,
    predecessor_root: Path,
    source_product_root: Path,
    attempt_label: str,
    private_root: Path,
) -> None:
    """Close an interrupted consumed Session without restarting or replaying traffic."""

    verify_product_v02321_history(root)
    _verify_profile_binding(root, role="PREFLIGHT")
    _verify_profile_binding(root, role="FORMAL")
    predecessor = Path(predecessor_root).resolve(strict=True)
    source_root = Path(source_product_root).resolve(strict=True)
    _require_preserved_runtime_root_v02321(predecessor, source_root)
    ledger_before = _load_private_ledger(root)
    session_starts = tuple(
        event
        for event in ledger_before.events
        if isinstance(event, InfrastructureSessionStartV02321)
    )
    closed_session_ids = {
        event.session_id
        for event in ledger_before.events
        if isinstance(event, InfrastructureSessionCompletionV02321)
    }
    open_sessions = tuple(
        event for event in session_starts if event.session_id not in closed_session_ids
    )
    if len(open_sessions) != 1:
        raise FileExistsError("Product v0.2.3.2.1 private attempt exists")
    session = open_sessions[0]
    attempt_starts = tuple(
        event
        for event in ledger_before.events
        if isinstance(event, TrafficPreflightAttemptStartV02321)
        and event.session_id == session.session_id
    )
    completed_attempt_ids = {
        event.attempt_id
        for event in ledger_before.events
        if isinstance(event, TrafficPreflightAttemptCompletionV02321)
    }
    open_attempt_starts = tuple(
        event
        for event in attempt_starts
        if event.attempt_id not in completed_attempt_ids
    )
    if len(attempt_starts) > 1 or len(open_attempt_starts) != len(attempt_starts):
        raise ValueError("Product v0.2.3.2.1 recovery Attempt differs")
    reservation = _load_sealed_private_object(
        private_root / "pre-session-start.json",
        digest_field="reservation_sha256",
        schema_version="ecomsre.product.pre-session-start.v02321",
    )
    try:
        reservation_history = _pre_session_starts(root)
    except ValueError as error:
        raise ValueError(
            "Product v0.2.3.2.1 recovery reservation differs"
        ) from error
    current_reservations = tuple(
        (ordinal, persisted)
        for ordinal, (attempt_root, persisted) in enumerate(
            reservation_history, start=1
        )
        if attempt_root.resolve(strict=True) == private_root.resolve(strict=True)
    )
    infrastructure_session_count_before = reservation.get(
        "infrastructure_session_count_before"
    )
    traffic_attempt_count_before = reservation.get("traffic_attempt_count_before")
    if (
        len(current_reservations) != 1
        or current_reservations[0][1] != reservation
        or reservation.get("reservation_ordinal") != current_reservations[0][0]
        or reservation.get("attempt_label") != attempt_label
        or type(infrastructure_session_count_before) is not int
        or infrastructure_session_count_before + 1
        != session.infrastructure_session_count_after
        or session.infrastructure_session_count_after
        != ledger_before.infrastructure_session_count
        or type(traffic_attempt_count_before) is not int
        or traffic_attempt_count_before + len(open_attempt_starts)
        != ledger_before.traffic_attempt_count
    ):
        raise ValueError("Product v0.2.3.2.1 recovery reservation differs")
    plan = TrafficHarnessTypedRequestPlanV02321.model_validate_json(
        (private_root / "typed-request-plan.json").read_bytes()
    )
    if (
        plan.plan_sha256 != session.request_plan_sha256
        or plan.state_clone_sha256 != session.state_clone_sha256
    ):
        raise ValueError("Product v0.2.3.2.1 recovery request plan differs")
    pre_session_completion_path = private_root / "pre-session-completion.json"
    completion_payload = {
        "terminal": "ECOMSRE_PRODUCT_V02321_PRE_SESSION_COMPLETE",
        "attempt_label": attempt_label,
        "reservation_sha256": reservation["reservation_sha256"],
        "session_id": session.session_id,
        "session_start_sha256": session.event_sha256,
        "infrastructure_session_count_after": (
            session.infrastructure_session_count_after
        ),
        "traffic_attempt_count_before": traffic_attempt_count_before,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "action_authority": "NONE",
    }
    if pre_session_completion_path.exists():
        pre_session_completion = _load_sealed_private_object(
            pre_session_completion_path,
            digest_field="completion_sha256",
            schema_version="ecomsre.product.pre-session-completion.v02321",
        )
        if (
            set(pre_session_completion) != _PRE_SESSION_COMPLETION_FIELDS_V02321
            or any(
                pre_session_completion.get(field) != value
                for field, value in completion_payload.items()
            )
        ):
            raise ValueError("Product v0.2.3.2.1 recovery Session differs")
    else:
        _write_checkpoint(
            pre_session_completion_path,
            schema_version="ecomsre.product.pre-session-completion.v02321",
            digest_field="completion_sha256",
            payload=completion_payload,
        )

    clone_report = PreflightStateCloneReportV02321.model_validate_json(
        (
            root / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_bytes()
    )
    frozen_source = ProductStateSourceV0232.model_validate(
        _load_object(
            root / "docs/analysis/product-v0232-predecessor-audit.json"
        ).get("source_state")
    )
    source_before = _admit_source_state(source_root)
    if source_before != frozen_source or clone_report.source_state != frozen_source:
        raise ValueError("Product v0.2.3.2.1 frozen source state differs")
    product_before, product_data_root = _admit_clone_state(root, clone_report)
    database_owner_count_before = _database_owner_count(
        product_data_root / "product.sqlite3"
    )
    product_processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_data_root,
        private_root=private_root / "product-processes",
    )

    profile = load_traffic_profile_v0232(root, role="PREFLIGHT")
    manifest = _load_object(root / "config/product-v0231/historical-results.v1.json")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest.get("private_state")
    )
    context = ProductBaselineContinuationContextV0231.model_validate(
        _load_object(
            root / "docs/analysis/product-v0231-baseline-continuation-context.json"
        )
    )
    tracked_runtime = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _load_object(
            root / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
        )
    )
    runtime_bundle = load_bundle(
        predecessor / "config/live-telemetry-controlled-remediation-v1"
    )
    authority, resolved_compose = load_preserved_runtime_inputs_v0231(
        predecessor_root=predecessor,
        binding=binding,
    )
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=private_root / "demo",
        binding=binding,
        context=context,
        bundle=runtime_bundle,
        preserved_authority=authority,
        preserved_resolved_compose=resolved_compose,
    )
    snapshot_path = private_root / "docker-baseline-snapshot.json"
    snapshot = (
        _load_docker_baseline_snapshot(snapshot_path)
        if snapshot_path.is_file() and not snapshot_path.is_symlink()
        else None
    )
    demo_cleanup_result: Any | None = None
    demo_cleanup_error: BaseException | None = None
    try:
        demo_cleanup_result = lifecycle.recover_cleanup_owned(
            baseline_snapshot=snapshot,
            baseline_unchanged=False,
        )
        if (
            lifecycle.runtime_descriptor is not None
            and lifecycle.runtime_descriptor != tracked_runtime
        ):
            raise ValueError(
                "Product v0.2.3.2.1 recovery Runtime descriptor differs"
            )
    except BaseException as error:
        demo_cleanup_error = error

    product_cleanup_payload: Mapping[str, Any] | None = None
    product_cleanup_error: BaseException | None = None
    database_owner_count_after: int | None = None
    try:
        product_cleanup_payload = product_processes.cleanup_observation()
        database_owner_count_after = _database_owner_count(
            product_data_root / "product.sqlite3"
        )
    except BaseException as error:
        product_cleanup_error = error

    attempt_start = attempt_starts[0] if attempt_starts else None
    events = list(ledger_before.events)
    ledger_tail: list[TrafficPreflightEventV02321] = []
    execution: HealthyTrafficExecutionV0232 | None = None
    dispatch_failure: TrafficDispatchFailureEvidenceV02321 | None = None
    attempt_completion: TrafficPreflightAttemptCompletionV02321 | None = None
    if attempt_start is not None:
        execution_path = private_root / "traffic-execution.json"
        if execution_path.is_file() and not execution_path.is_symlink():
            execution_checkpoint = _load_sealed_private_object(
                execution_path,
                digest_field="checkpoint_sha256",
                schema_version=(
                    "ecomsre.product.traffic-execution-checkpoint.v02321"
                ),
            )
            if (
                execution_checkpoint.get("attempt_id") != attempt_start.attempt_id
                or execution_checkpoint.get("attempt_start_sha256")
                != attempt_start.event_sha256
            ):
                raise ValueError("Product v0.2.3.2.1 recovery execution differs")
            execution = HealthyTrafficExecutionV0232.model_validate(
                execution_checkpoint.get("traffic_execution")
            )
        completion_path = private_root / "attempt-completion.json"
        if completion_path.is_file() and not completion_path.is_symlink():
            completion_checkpoint = _load_sealed_private_object(
                completion_path,
                digest_field="checkpoint_sha256",
                schema_version=(
                    "ecomsre.product.attempt-completion-checkpoint.v02321"
                ),
            )
            attempt_completion = TrafficPreflightAttemptCompletionV02321.model_validate(
                completion_checkpoint.get("attempt_completion")
            )
            if (
                attempt_completion.attempt_id != attempt_start.attempt_id
                or attempt_completion.attempt_start_sha256
                != attempt_start.event_sha256
                or (
                    execution is not None
                    and attempt_completion.traffic_execution_sha256
                    != execution.execution_sha256
                )
            ):
                raise ValueError("Product v0.2.3.2.1 recovery completion differs")
            dispatch_failure = attempt_completion.traffic_dispatch_failure
        elif execution is not None:
            attempt_completion = _build_execution_completion(
                events,
                attempt_start=attempt_start,
                execution=execution,
            )
        else:
            dispatch_failure = TrafficDispatchFailureEvidenceV02321.build(
                attempt_id=attempt_start.attempt_id,
                endpoint_sha256=attempt_start.endpoint_sha256,
                first_cart_payload_sha256=attempt_start.first_cart_payload_sha256,
                transport_invoked=True,
                remote_delivery="UNKNOWN",
                safe_error_code="PROCESS_INTERRUPTED_BEFORE_EXECUTION_CHECKPOINT",
            )
            attempt_completion = TrafficPreflightAttemptCompletionV02321.build(
                **_event_meta(events),
                attempt_id=attempt_start.attempt_id,
                attempt_ordinal=attempt_start.attempt_ordinal,
                attempt_start_sha256=attempt_start.event_sha256,
                session_id=attempt_start.session_id,
                traffic_execution_sha256=None,
                traffic_dispatch_failure=dispatch_failure.model_dump(mode="json"),
                stage=TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED,
                first_cart_transport_invoked=True,
                planned_transactions=10,
                completed_transactions=0,
                successful_transactions=0,
                failed_transactions=0,
                safe_error_code=dispatch_failure.safe_error_code,
                terminal="ATTEMPT_FAILED",
                monotonic_duration_ms=0,
            )
        assert attempt_completion is not None
        _write_checkpoint(
            private_root / "attempt-completion.json",
            schema_version=(
                "ecomsre.product.attempt-completion-checkpoint.v02321"
            ),
            digest_field="checkpoint_sha256",
            payload={
                "attempt_completion": attempt_completion.model_dump(mode="json")
            },
        )
        events.append(attempt_completion)
        ledger_tail.append(attempt_completion)

    queue_before_sha256: str | None = None
    baseline_before_sha256: str | None = None
    if attempt_start is not None:
        queue_before_sha256 = attempt_start.queue_before_sha256
        baseline_before_sha256 = attempt_start.outer_baseline_before_sha256
    queue_after_sha256: str | None = None
    try:
        if queue_before_sha256 is not None:
            queue_after_sha256 = verify_queue_default_v021(
                lifecycle.flag_file,
                expected_default_value=profile.queue_fault_flag,
                expected_sha256=queue_before_sha256,
            ).after_sha256
    except BaseException:
        queue_after_sha256 = None

    source_after = _admit_source_state(source_root)
    product_after, observed_data_root = _admit_clone_state(root, clone_report)
    if source_after != frozen_source or observed_data_root != product_data_root:
        raise ValueError("Product v0.2.3.2.1 recovery state drifted")
    product_cleanup = _product_cleanup_observation(
        product_cleanup_payload,
        database_owner_count_before=database_owner_count_before,
        database_owner_count_after=database_owner_count_after,
        safe_error_code=(
            _safe_error_code(product_cleanup_error)
            if product_cleanup_error is not None
            else None
        ),
    )
    demo_cleanup = _demo_cleanup_observation(
        demo_cleanup_result,
        safe_error_code=(
            _safe_error_code(demo_cleanup_error)
            if demo_cleanup_error is not None
            else None
        ),
    )
    counts = OwnedResourceCountsV02321(
        containers=demo_cleanup.owned_containers,
        networks=demo_cleanup.owned_networks,
        volumes=demo_cleanup.owned_volumes,
        host_processes=product_cleanup.owned_host_processes,
    )
    non_owned: bool | None = None
    if (
        demo_cleanup.non_owned_resources_changed is not None
        and product_cleanup.non_owned_resources_changed is not None
    ):
        non_owned = (
            demo_cleanup.non_owned_resources_changed
            or product_cleanup.non_owned_resources_changed
        )

    trace = [
        TrafficHarnessStageV02321.REQUEST_PLAN_CONSTRUCTION,
        TrafficHarnessStageV02321.REQUEST_PLAN_VALIDATED,
        TrafficHarnessStageV02321.SANDBOX_START_REQUESTED,
    ]
    if attempt_start is not None:
        trace.extend(
            (
                TrafficHarnessStageV02321.SANDBOX_READY,
                TrafficHarnessStageV02321.RUNTIME_AUTHORITY_VERIFICATION_REQUESTED,
                TrafficHarnessStageV02321.RUNTIME_AUTHORITY_VERIFIED,
                TrafficHarnessStageV02321.QUEUE_PRESTATE_CAPTURED,
                TrafficHarnessStageV02321.BASELINE_PRESTATE_CAPTURED,
                TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED,
                TrafficHarnessStageV02321.RUNTIME_INSPECTED,
                TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED,
                TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED,
            )
        )
        if execution is not None:
            trace.append(TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE)
        trace.extend(
            (
                TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED,
                TrafficHarnessStageV02321.BASELINE_POSTSTATE_CAPTURED,
            )
        )
    trace.append(TrafficHarnessStageV02321.CLEANUP_COMPLETE)
    if attempt_completion is not None and attempt_completion.terminal == "ATTEMPT_FAILED":
        failure_stage: TrafficHarnessStageV02321 = attempt_completion.stage
        safe_error_code: str = str(attempt_completion.safe_error_code)
    else:
        failure_stage = TrafficHarnessStageV02321.CLEANUP_COMPLETE
        safe_error_code = "PROCESS_INTERRUPTED_RECOVERY"
    closure = TrafficHarnessClosureV02321.build(
        **_event_meta(events),
        session_id=session.session_id,
        attempt_id=attempt_start.attempt_id if attempt_start is not None else None,
        stage_reached=failure_stage,
        observed_stage_sequence=trace,
        request_plan_sha256=session.request_plan_sha256,
        queue_before_sha256=queue_before_sha256,
        queue_after_sha256=queue_after_sha256,
        outer_baseline_before_sha256=baseline_before_sha256,
        outer_baseline_after_sha256=None,
        runtime_inspect_request_sha256=(
            attempt_start.runtime_inspect_request_sha256
            if attempt_start is not None
            else None
        ),
        traffic_execution_sha256=(
            execution.execution_sha256 if execution is not None else None
        ),
        traffic_dispatch_failure_sha256=(
            dispatch_failure.dispatch_failure_sha256
            if dispatch_failure is not None
            else None
        ),
        product_cleanup=product_cleanup.model_dump(mode="json"),
        demo_cleanup=demo_cleanup.model_dump(mode="json"),
        owned_resource_counts=counts.model_dump(mode="json"),
        non_owned_resources_changed=non_owned,
        failure_stage=failure_stage,
        safe_error_code=safe_error_code,
        closure_terminal="BLOCKED_RESOURCE_CLEANUP",
    )
    events.append(closure)
    ledger_tail.append(closure)
    session_completion = InfrastructureSessionCompletionV02321.build(
        **_event_meta(events),
        session_id=session.session_id,
        session_start_sha256=session.event_sha256,
        closure_sha256=closure.closure_sha256,
        stage=TrafficHarnessStageV02321.CLEANUP_COMPLETE,
        stage_reached=closure.stage_reached,
        monotonic_duration_ms=0,
        infrastructure_session_count_after=(
            session.infrastructure_session_count_after
        ),
        cleanup_stage="BLOCKED",
        terminal="SESSION_CLOSED_BLOCKED",
    )
    events.append(session_completion)
    ledger_tail.append(session_completion)
    ledger = TrafficPreflightLedgerV02321.build(events=tuple(events))
    blocker_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.traffic-preflight-blocker.v02321",
        "terminal": "BLOCKED_ECOMSRE_PRODUCT_V02321_INTERRUPTED_SESSION",
        "attempt_id": attempt_start.attempt_id if attempt_start else None,
        "attempt_ordinal": (
            attempt_start.attempt_ordinal
            if attempt_start is not None
            else ledger.traffic_attempt_count + 1
        ),
        "session_id": session.session_id,
        "failure_stage": failure_stage.value,
        "safe_error_code": safe_error_code,
        "traffic_execution": (
            execution.model_dump(mode="json") if execution is not None else None
        ),
        "closure_sha256": closure.closure_sha256,
        "ledger_sha256": ledger.ledger_sha256,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "action_authority": "NONE",
    }
    blocker = {
        **blocker_body,
        "blocker_sha256": semantic_sha256_v22(blocker_body),
    }
    blocker_path = (
        "docs/analysis/product-v02321-traffic-preflight-attempt-"
        f"{attempt_start.attempt_ordinal}.json"
        if attempt_start is not None
        else (
            "docs/analysis/product-v02321-traffic-preflight-session-"
            f"{ledger.infrastructure_session_count}-blocker.json"
        )
    )
    progress = _updated_progress(
        root,
        terminal="BLOCKED_ECOMSRE_PRODUCT_V02321_INTERRUPTED_SESSION",
        ledger=ledger,
        attempt=None,
        preflight=None,
    )
    artifacts = [
        {"path": blocker_path, "mode": "CREATE_EXACT", "payload": blocker},
        {
            "path": "docs/analysis/product-v02321-traffic-preflight-ledger.json",
            "mode": "REPLACE",
            "payload": ledger.model_dump(mode="json"),
        },
        {
            "path": "docs/analysis/product-v02321-progress.json",
            "mode": "REPLACE",
            "payload": progress,
        },
    ]
    publication = _publication_bundle(
        attempt_label=attempt_label,
        terminal="BLOCKED_ECOMSRE_PRODUCT_V02321_INTERRUPTED_SESSION",
        ledger_tail=[event.model_dump(mode="json") for event in ledger_tail],
        artifacts=artifacts,
        attempt=None,
    )
    _write_public_exact_or_create(
        private_root / "publication-bundle.json", publication
    )
    _publish_publication_bundle(root, publication)


def _run_reserved_traffic_preflight_v02321(
    *,
    project_root: Path,
    predecessor_root: Path,
    source_product_root: Path,
    attempt_label: str,
    pre_session_start: Mapping[str, Any],
    prior_attempt: Path | None = None,
    changed_surface: str | None = None,
    changed_source_paths: tuple[str, ...] = (),
    repair_rationale: str | None = None,
) -> LiveTrafficPreflightAttemptV02321 | None:
    """Run one session and, only after all admission gates, one traffic Attempt."""

    root = Path(project_root).resolve(strict=True)
    private_root = root / ".local/product-v02321/traffic-preflight" / attempt_label
    if private_root.is_symlink() or not private_root.is_dir():
        raise ValueError("Product v0.2.3.2.1 private attempt root differs")
    predecessor = Path(predecessor_root).resolve(strict=True)
    source_root = Path(source_product_root).resolve(strict=True)
    _require_preserved_runtime_root_v02321(predecessor, source_root)

    verify_product_v02321_history(root)
    contract_preflight = run_harness_contract_preflight_v02321(root)
    if contract_preflight["terminal"] != (
        "ECOMSRE_PRODUCT_V02321_PREFLIGHT_CLOSURE_CONTRACT_PASS"
    ):
        raise ValueError("Product v0.2.3.2.1 closure contract differs")
    _verify_profile_binding(root, role="PREFLIGHT")
    _verify_profile_binding(root, role="FORMAL")

    prior_ledger = _load_private_ledger(root)
    events = list(prior_ledger.events)
    attempt_ordinal = prior_ledger.traffic_attempt_count + 1
    session_repair_payload: dict[str, Any] | None = None
    has_pre_session_repair = (
        pre_session_start.get("prior_pre_session_sha256") is not None
    )
    if events:
        if prior_attempt is None or changed_surface in {None, "", "INITIAL"}:
            raise ValueError("successor preflight attempt lacks prior/change evidence")
        if not changed_source_paths or not repair_rationale:
            raise ValueError("successor preflight attempt lacks changed source evidence")
        prior_payload = _load_object(Path(prior_attempt).resolve(strict=True))
        latest_closure = next(
            (
                event
                for event in reversed(events)
                if isinstance(event, TrafficHarnessClosureV02321)
            ),
            None,
        )
        prior_completion = next(
            (
                event
                for event in reversed(events)
                if isinstance(event, TrafficPreflightAttemptCompletionV02321)
            ),
            None,
        )
        prior_start = next(
            (
                event
                for event in reversed(events)
                if isinstance(event, TrafficPreflightAttemptStartV02321)
            ),
            None,
        )
        if latest_closure is None:
            raise ValueError("successor preflight prior Attempt differs")
        if latest_closure.closure_terminal not in {
            "CLEAN_PRE_TRAFFIC",
            "CLEAN_POST_TRAFFIC",
        }:
            raise ValueError("successor preflight prior closure is not clean")
        if latest_closure.attempt_id is None:
            if not has_pre_session_repair and (
                prior_payload.get("session_id") != latest_closure.session_id
                or prior_payload.get("closure_sha256")
                != latest_closure.closure_sha256
            ):
                raise ValueError("successor preflight prior Session differs")
            session_repair_payload = {
                "schema_version": (
                    "ecomsre.product.preflight-session-repair.v02321"
                ),
                "prior_session_id": latest_closure.session_id,
                "prior_closure_sha256": latest_closure.closure_sha256,
                "prior_failure_stage": (
                    latest_closure.failure_stage.value
                    if latest_closure.failure_stage is not None
                    else None
                ),
                "prior_safe_error_code": latest_closure.safe_error_code,
                "changed_surface": str(changed_surface),
                "changed_source_paths": list(sorted(set(changed_source_paths))),
                "repair_rationale": repair_rationale,
                "prior_pre_session_sha256": pre_session_start.get(
                    "prior_pre_session_sha256"
                ),
            }
        elif (
            not has_pre_session_repair
            and prior_payload.get("attempt_id") != latest_closure.attempt_id
        ):
            raise ValueError("successor preflight prior Attempt differs")
        if prior_ledger.traffic_attempt_count == 0:
            prior_completion_sha256 = None
            prior_failure_stage = None
            prior_safe_error_code = None
            prior_implementation_sha256 = None
            effective_surface = "INITIAL"
            effective_sources = changed_source_paths
            effective_rationale = (
                "first traffic Attempt after a changed and closed pre-traffic "
                "infrastructure session"
            )
        else:
            if (
                prior_completion is None
                or prior_start is None
                or prior_completion.terminal != "ATTEMPT_FAILED"
            ):
                raise ValueError("successor preflight prior Attempt differs")
            prior_completion_sha256 = prior_completion.event_sha256
            prior_failure_stage = prior_completion.stage.value
            prior_safe_error_code = prior_completion.safe_error_code
            prior_implementation_sha256 = prior_start.changed_implementation_sha256
            effective_surface = str(changed_surface)
            effective_sources = changed_source_paths
            effective_rationale = repair_rationale
    else:
        if has_pre_session_repair:
            if (
                prior_attempt is None
                or changed_surface in {None, "", "INITIAL"}
                or not changed_source_paths
                or not repair_rationale
            ):
                raise ValueError(
                    "initial preflight pre-session repair evidence differs"
                )
            effective_sources = changed_source_paths
            effective_rationale = (
                "first traffic Attempt after changed pre-session admission: "
                f"{repair_rationale}"
            )
        else:
            if prior_attempt is not None or changed_surface not in {
                None,
                "",
                "INITIAL",
            }:
                raise ValueError(
                    "initial preflight Attempt prior/change evidence differs"
                )
            effective_sources = _INITIAL_CHANGED_SOURCES
            effective_rationale = (
                "initial successor admission after typed request and cleanup "
                "contracts passed offline"
            )
        prior_completion_sha256 = None
        prior_failure_stage = None
        prior_safe_error_code = None
        prior_implementation_sha256 = None
        effective_surface = "INITIAL"

    clone_report = PreflightStateCloneReportV02321.model_validate_json(
        (
            root / "docs/analysis/product-v02321-product-state-clone-preflight.json"
        ).read_bytes()
    )
    frozen_source = ProductStateSourceV0232.model_validate(
        _load_object(
            root / "docs/analysis/product-v0232-predecessor-audit.json"
        ).get("source_state")
    )
    if clone_report.source_state != frozen_source:
        raise ValueError("Product v0.2.3.2.1 frozen source state differs")
    source_before = _admit_source_state(source_root)
    if source_before != clone_report.source_state:
        raise ValueError("Product v0.2.3.2.1 source drifted after clone")
    product_before, product_data_root = _admit_clone_state(root, clone_report)
    database_owner_count_before = _database_owner_count(
        product_data_root / "product.sqlite3"
    )
    product_processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_data_root,
        private_root=private_root / "product-processes",
    )
    product_preflight = product_processes.cleanup_observation()
    if (
        database_owner_count_before != 0
        or product_preflight.get("verdict") != "CLEAN"
        or product_preflight.get("launches") != ()
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_PRODUCT_PREEXISTING")

    campaign_sha256 = _load_successor_campaign_sha256(root)
    plan = build_traffic_harness_typed_request_plan_v02321(
        campaign_sha256=campaign_sha256,
        role="PREFLIGHT",
        state_clone_sha256=clone_report.clone.clone_sha256,
        attempt_ordinal=attempt_ordinal,
    )
    runtime_request = materialize_planned_request_v02321(
        plan, tool_name="inspect_service_runtime"
    )
    _require_reserved_private_root(private_root)
    write_private_json(
        private_root / "typed-request-plan.json",
        plan.model_dump(mode="json"),
        create_once=True,
    )
    if session_repair_payload is not None:
        session_repair = {
            **session_repair_payload,
            "repair_sha256": semantic_sha256_v22(session_repair_payload),
        }
        write_private_json(
            private_root / "session-repair.json",
            session_repair,
            create_once=True,
        )

    profile = load_traffic_profile_v0232(root, role="PREFLIGHT")
    formal_profile = load_traffic_profile_v0232(root, role="FORMAL")
    contract = load_checkout_traffic_contract_v0232(root)
    manifest = _load_object(root / "config/product-v0231/historical-results.v1.json")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest.get("private_state")
    )
    context = ProductBaselineContinuationContextV0231.model_validate(
        _load_object(
            root / "docs/analysis/product-v0231-baseline-continuation-context.json"
        )
    )
    tracked_runtime = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _load_object(
            root / "docs/analysis/product-v0231-runtime-authority-descriptor.json"
        )
    )
    bundle = load_bundle(predecessor / "config/live-telemetry-controlled-remediation-v1")
    authority, resolved_compose = load_preserved_runtime_inputs_v0231(
        predecessor_root=predecessor,
        binding=binding,
    )
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=private_root / "demo",
        binding=binding,
        context=context,
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=resolved_compose,
    )
    lifecycle.admit_prestart()
    if lifecycle.runtime_descriptor != tracked_runtime:
        raise ValueError("Product v0.2.3.2.1 tracked Runtime descriptor differs")

    docker_baseline_snapshot: DockerSnapshot | None = None

    def persist_docker_baseline_snapshot() -> None:
        nonlocal docker_baseline_snapshot
        if lifecycle.environment is None:
            raise RuntimeError("Product v0.2.3.2.1 Sandbox environment unavailable")
        docker_baseline_snapshot = lifecycle.environment.snapshot_all_resources()
        _write_docker_baseline_snapshot(
            private_root / "docker-baseline-snapshot.json",
            docker_baseline_snapshot,
        )

    trace = [
        TrafficHarnessStageV02321.REQUEST_PLAN_CONSTRUCTION,
        TrafficHarnessStageV02321.REQUEST_PLAN_VALIDATED,
        TrafficHarnessStageV02321.SANDBOX_START_REQUESTED,
    ]
    session = InfrastructureSessionStartV02321.build(
        **_event_meta(events),
        request_plan_sha256=plan.plan_sha256,
        runtime_inspect_request_sha256=runtime_request.normalized_request_sha256,
        runtime_continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
        state_clone_sha256=clone_report.clone.clone_sha256,
        stage=TrafficHarnessStageV02321.SANDBOX_START_REQUESTED,
        sandbox_start_requested=True,
        infrastructure_session_count_after=(
            prior_ledger.infrastructure_session_count + 1
        ),
    )

    queue_before_sha256: str | None = None
    queue_after_sha256: str | None = None
    baseline_before_sha256: str | None = None
    baseline_after_sha256: str | None = None
    checkout_runtime: tuple[str, bool, int] | None = None
    attempt_start: TrafficPreflightAttemptStartV02321 | None = None
    attempt_completion: TrafficPreflightAttemptCompletionV02321 | None = None
    execution: HealthyTrafficExecutionV0232 | None = None
    dispatch_failure: TrafficDispatchFailureEvidenceV02321 | None = None
    live_error: BaseException | None = None
    live_failure_stage: TrafficHarnessStageV02321 | None = None
    demo_cleanup_result: Any | None = None
    demo_cleanup_error: BaseException | None = None
    product_cleanup_payload: Mapping[str, Any] | None = None
    product_cleanup_error: BaseException | None = None
    database_owner_count_after: int | None = None
    source_after: ProductStateSourceV0232 | None = None
    product_after: ProductStateSourceV0232 | None = None
    session_persisted = False
    attempt_persisted = False
    ledger_tail: list[TrafficPreflightEventV02321] = []

    def persist_session(event: InfrastructureSessionStartV02321) -> None:
        nonlocal session_persisted
        _append_event(root, events, event)
        session_persisted = True
        completion_body = {
            "schema_version": "ecomsre.product.pre-session-completion.v02321",
            "terminal": "ECOMSRE_PRODUCT_V02321_PRE_SESSION_COMPLETE",
            "attempt_label": attempt_label,
            "reservation_sha256": pre_session_start["reservation_sha256"],
            "session_id": event.session_id,
            "session_start_sha256": event.event_sha256,
            "infrastructure_session_count_after": (
                event.infrastructure_session_count_after
            ),
            "traffic_attempt_count_before": prior_ledger.traffic_attempt_count,
            "formal_healthy_traffic_execution_count": 0,
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
            "action_authority": "NONE",
        }
        write_private_json(
            private_root / "pre-session-completion.json",
            {
                **completion_body,
                "completion_sha256": semantic_sha256_v22(completion_body),
            },
            create_once=True,
        )

    def persist_attempt(event: TrafficPreflightAttemptStartV02321) -> None:
        nonlocal attempt_persisted
        _append_event(root, events, event)
        attempt_persisted = True
        trace.append(TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED)

    try:
        request_sandbox_start_v02321(
            session,
            persist_start=persist_session,
            request_start=lambda: lifecycle.start(
                on_boundary_verified=persist_docker_baseline_snapshot
            ),
        )
        if (
            docker_baseline_snapshot is None
            or getattr(lifecycle.environment, "_baseline_snapshot", None)
            != docker_baseline_snapshot
        ):
            raise ValueError("Product v0.2.3.2.1 Docker baseline snapshot differs")
        _write_checkpoint(
            private_root / "sandbox-started.json",
            schema_version="ecomsre.product.sandbox-started.v02321",
            digest_field="checkpoint_sha256",
            payload={
                "session_id": session.session_id,
                "session_start_sha256": session.event_sha256,
                "docker_baseline_snapshot_sha256": _load_object(
                    private_root / "docker-baseline-snapshot.json"
                )["snapshot_sha256"],
            },
        )
        trace.append(TrafficHarnessStageV02321.SANDBOX_READY)
        trace.append(
            TrafficHarnessStageV02321.RUNTIME_AUTHORITY_VERIFICATION_REQUESTED
        )
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if lifecycle.rebound_authority != authority:
            raise ValueError("Product v0.2.3.2.1 fresh Runtime authority differs")
        trace.append(TrafficHarnessStageV02321.RUNTIME_AUTHORITY_VERIFIED)
        queue_before = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=profile.queue_fault_flag,
        )
        queue_before_sha256 = queue_before.before_sha256
        _write_checkpoint(
            private_root / "queue-before.json",
            schema_version="ecomsre.product.queue-before.v02321",
            digest_field="checkpoint_sha256",
            payload={
                "session_id": session.session_id,
                "queue_before_sha256": queue_before_sha256,
            },
        )
        trace.append(TrafficHarnessStageV02321.QUEUE_PRESTATE_CAPTURED)
        baseline_before_sha256 = lifecycle.read_baseline_sha256()
        _write_checkpoint(
            private_root / "baseline-before.json",
            schema_version="ecomsre.product.baseline-before.v02321",
            digest_field="checkpoint_sha256",
            payload={
                "session_id": session.session_id,
                "baseline_before_sha256": baseline_before_sha256,
            },
        )
        trace.append(TrafficHarnessStageV02321.BASELINE_PRESTATE_CAPTURED)
        trace.append(TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED)
        checkout_runtime = _checkout_runtime(backend, runtime_request)
        trace.append(TrafficHarnessStageV02321.RUNTIME_INSPECTED)
        time.sleep(profile.stabilization_seconds)

        changed_bindings = bind_changed_source_files_v02321(
            root, tuple(sorted(set(effective_sources)))
        )
        changed_binding_payloads = [
            item.model_dump(mode="json") for item in changed_bindings
        ]
        if _changed_implementation_from_bindings(
            changed_binding_payloads
        ) != pre_session_start.get("changed_implementation_sha256"):
            raise ValueError(
                "Product v0.2.3.2.1 changed sources drifted after reservation"
            )
        first_cart_payload = HealthyTrafficRunnerV0232._payloads(
            profile.request_seed, 1
        )[0]
        endpoint_sha256 = semantic_sha256_v22({"endpoint": _ENDPOINT_V02321})
        first_cart_payload_sha256 = semantic_sha256_v22(first_cart_payload)
        attempt_start = TrafficPreflightAttemptStartV02321.build(
            **_event_meta(events),
            attempt_ordinal=attempt_ordinal,
            prior_attempt_completion_sha256=prior_completion_sha256,
            prior_failure_stage=prior_failure_stage,
            prior_safe_error_code=prior_safe_error_code,
            prior_implementation_sha256=prior_implementation_sha256,
            changed_surface=effective_surface,
            changed_source_bindings=changed_binding_payloads,
            repair_rationale=effective_rationale,
            session_id=session.session_id,
            session_start_sha256=session.event_sha256,
            request_plan_sha256=plan.plan_sha256,
            traffic_contract_sha256=contract.contract_sha256,
            profile_sha256=profile.profile_sha256,
            runtime_inspect_request_sha256=(
                runtime_request.normalized_request_sha256
            ),
            runtime_authority_sha256=tracked_runtime.descriptor_sha256,
            endpoint_sha256=endpoint_sha256,
            first_cart_payload_sha256=first_cart_payload_sha256,
            queue_before_sha256=queue_before_sha256,
            outer_baseline_before_sha256=baseline_before_sha256,
            sandbox_ready=True,
            runtime_authority_equal=True,
            request_plan_equal=True,
            checkout_state=checkout_runtime[0],
            checkout_healthy=checkout_runtime[1],
            checkout_restart_count=checkout_runtime[2],
            endpoint_validator_ready=True,
            payload_validator_ready=True,
            stage=TrafficHarnessStageV02321.TRAFFIC_ATTEMPT_CONSUMED,
            traffic_attempt_count_after=attempt_ordinal,
        )

        def run_traffic() -> HealthyTrafficExecutionV0232:
            trace.append(TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED)
            with HealthyTrafficRunnerV0232() as runner:
                return runner.run(
                    endpoint=_ENDPOINT_V02321,
                    profile=profile,
                    contract=contract,
                    role="PREFLIGHT",
                )

        execution = invoke_first_cart_transport_v02321(
            attempt_start,
            persist_start=persist_attempt,
            invoke_transport=run_traffic,
        )
        _write_checkpoint(
            private_root / "traffic-execution.json",
            schema_version="ecomsre.product.traffic-execution-checkpoint.v02321",
            digest_field="checkpoint_sha256",
            payload={
                "attempt_id": attempt_start.attempt_id,
                "attempt_start_sha256": attempt_start.event_sha256,
                "traffic_execution": execution.model_dump(mode="json"),
            },
        )
        trace.append(TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE)
        attempt_completion = _build_execution_completion(
            events,
            attempt_start=attempt_start,
            execution=execution,
        )
        _write_checkpoint(
            private_root / "attempt-completion.json",
            schema_version="ecomsre.product.attempt-completion-checkpoint.v02321",
            digest_field="checkpoint_sha256",
            payload={
                "attempt_completion": attempt_completion.model_dump(mode="json")
            },
        )
        events.append(attempt_completion)
        ledger_tail.append(attempt_completion)
        if not execution.run.passed:
            live_failure_stage = TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE
    except BaseException as error:
        live_error = error
        live_failure_stage = trace[-1]
    finally:
        if (
            attempt_start is not None
            and attempt_persisted
            and attempt_completion is None
            and execution is not None
        ):
            if TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE not in trace:
                trace.append(TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE)
            attempt_completion = _build_execution_completion(
                events,
                attempt_start=attempt_start,
                execution=execution,
            )
            try:
                _write_checkpoint(
                    private_root / "traffic-execution.json",
                    schema_version=(
                        "ecomsre.product.traffic-execution-checkpoint.v02321"
                    ),
                    digest_field="checkpoint_sha256",
                    payload={
                        "attempt_id": attempt_start.attempt_id,
                        "attempt_start_sha256": attempt_start.event_sha256,
                        "traffic_execution": execution.model_dump(mode="json"),
                    },
                )
                _write_checkpoint(
                    private_root / "attempt-completion.json",
                    schema_version=(
                        "ecomsre.product.attempt-completion-checkpoint.v02321"
                    ),
                    digest_field="checkpoint_sha256",
                    payload={
                        "attempt_completion": (
                            attempt_completion.model_dump(mode="json")
                        )
                    },
                )
            except BaseException as error:
                if live_error is None:
                    live_error = error
            events.append(attempt_completion)
            ledger_tail.append(attempt_completion)
            if not execution.run.passed:
                live_failure_stage = (
                    TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE
                )
        if (
            attempt_start is not None
            and attempt_persisted
            and attempt_completion is None
            and execution is None
        ):
            if TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED not in trace:
                trace.append(TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED)
            failure_code = _safe_error_code(live_error or RuntimeError("traffic"))
            dispatch_failure = TrafficDispatchFailureEvidenceV02321.build(
                attempt_id=attempt_start.attempt_id,
                endpoint_sha256=attempt_start.endpoint_sha256,
                first_cart_payload_sha256=attempt_start.first_cart_payload_sha256,
                transport_invoked=True,
                remote_delivery="UNKNOWN",
                safe_error_code=failure_code,
            )
            attempt_completion = TrafficPreflightAttemptCompletionV02321.build(
                **_event_meta(events),
                attempt_id=attempt_start.attempt_id,
                attempt_ordinal=attempt_start.attempt_ordinal,
                attempt_start_sha256=attempt_start.event_sha256,
                session_id=attempt_start.session_id,
                traffic_execution_sha256=None,
                traffic_dispatch_failure=dispatch_failure.model_dump(mode="json"),
                stage=TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED,
                first_cart_transport_invoked=True,
                planned_transactions=10,
                completed_transactions=0,
                successful_transactions=0,
                failed_transactions=0,
                safe_error_code=failure_code,
                terminal="ATTEMPT_FAILED",
                monotonic_duration_ms=0,
            )
            try:
                _write_checkpoint(
                    private_root / "attempt-completion.json",
                    schema_version=(
                        "ecomsre.product.attempt-completion-checkpoint.v02321"
                    ),
                    digest_field="checkpoint_sha256",
                    payload={
                        "attempt_completion": (
                            attempt_completion.model_dump(mode="json")
                        )
                    },
                )
            except BaseException as error:
                if live_error is None:
                    live_error = error
            events.append(attempt_completion)
            ledger_tail.append(attempt_completion)
            live_failure_stage = TrafficHarnessStageV02321.FIRST_CART_SEND_REQUESTED

        if queue_before_sha256 is not None:
            try:
                trace.append(TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED)
                queue_after = verify_queue_default_v021(
                    lifecycle.flag_file,
                    expected_default_value=profile.queue_fault_flag,
                    expected_sha256=queue_before_sha256,
                )
                queue_after_sha256 = queue_after.after_sha256
            except BaseException as error:
                if live_error is None:
                    live_error = error
                    live_failure_stage = (
                        TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED
                    )
        if baseline_before_sha256 is not None:
            try:
                trace.append(TrafficHarnessStageV02321.BASELINE_POSTSTATE_CAPTURED)
                baseline_after_sha256 = lifecycle.read_baseline_sha256()
            except BaseException as error:
                if live_error is None:
                    live_error = error
                    live_failure_stage = (
                        TrafficHarnessStageV02321.BASELINE_POSTSTATE_CAPTURED
                    )
        try:
            demo_cleanup_result = lifecycle.cleanup_owned(
                baseline_unchanged=(
                    baseline_before_sha256 is not None
                    and baseline_before_sha256 == baseline_after_sha256
                )
            )
        except BaseException as error:
            demo_cleanup_error = error
        try:
            product_cleanup_payload = product_processes.cleanup_observation()
            database_owner_count_after = _database_owner_count(
                product_data_root / "product.sqlite3"
            )
        except BaseException as error:
            product_cleanup_error = error
        trace.append(TrafficHarnessStageV02321.CLEANUP_COMPLETE)

    if not session_persisted:
        failure_body = {
            "schema_version": (
                "ecomsre.product.session-start-persistence-blocker.v02321"
            ),
            "terminal": "BLOCKED_ECOMSRE_PRODUCT_V02321_SESSION_NOT_CONSUMED",
            "attempt_label": attempt_label,
            "request_plan_sha256": plan.plan_sha256,
            "safe_error_code": _safe_error_code(
                live_error or RuntimeError("session persistence")
            ),
            "infrastructure_session_count": prior_ledger.infrastructure_session_count,
            "traffic_attempt_count": prior_ledger.traffic_attempt_count,
            "formal_healthy_traffic_execution_count": 0,
            "action_authority": "NONE",
        }
        write_private_json(
            private_root / "session-start-persistence-blocker.json",
            {
                **failure_body,
                "blocker_sha256": semantic_sha256_v22(failure_body),
            },
            create_once=True,
        )
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V02321_SESSION_NOT_CONSUMED"
        ) from live_error
    if not attempt_persisted:
        attempt_start = None

    try:
        source_after = _admit_source_state(source_root)
        if source_after != clone_report.source_state:
            raise ValueError("Product v0.2.3.2.1 source drifted after clone")
        product_after, observed_data_root = _admit_clone_state(root, clone_report)
        if observed_data_root != product_data_root:
            raise ValueError("Product v0.2.3.2.1 clone root changed")
    except BaseException as error:
        if live_error is None:
            live_error = error
        if live_failure_stage is None:
            live_failure_stage = TrafficHarnessStageV02321.CLEANUP_COMPLETE

    product_cleanup = _product_cleanup_observation(
        product_cleanup_payload,
        database_owner_count_before=database_owner_count_before,
        database_owner_count_after=database_owner_count_after,
        safe_error_code=(
            _safe_error_code(product_cleanup_error)
            if product_cleanup_error is not None
            else None
        ),
    )
    demo_cleanup = _demo_cleanup_observation(
        demo_cleanup_result,
        safe_error_code=(
            _safe_error_code(demo_cleanup_error)
            if demo_cleanup_error is not None
            else None
        ),
    )
    counts = OwnedResourceCountsV02321(
        containers=demo_cleanup.owned_containers,
        networks=demo_cleanup.owned_networks,
        volumes=demo_cleanup.owned_volumes,
        host_processes=product_cleanup.owned_host_processes,
    )
    non_owned: bool | None = None
    if (
        demo_cleanup.non_owned_resources_changed is not None
        and product_cleanup.non_owned_resources_changed is not None
    ):
        non_owned = (
            demo_cleanup.non_owned_resources_changed
            or product_cleanup.non_owned_resources_changed
        )

    prestate = (
        queue_before_sha256,
        queue_after_sha256,
        baseline_before_sha256,
        baseline_after_sha256,
    )
    prestate_complete = all(value is not None for value in prestate)
    resources_clean = product_cleanup.clean and demo_cleanup.clean and counts.all_zero
    failure_stage: TrafficHarnessStageV02321 | None
    safe_error_code: str | None
    if not resources_clean:
        closure_terminal = "BLOCKED_RESOURCE_CLEANUP"
        failure_stage = TrafficHarnessStageV02321.CLEANUP_COMPLETE
        safe_error_code = "RESOURCE_CLEANUP_BLOCKED"
    elif not prestate_complete:
        closure_terminal = "BLOCKED_PRESTATE_UNAVAILABLE"
        failure_stage = live_failure_stage or trace[-2]
        safe_error_code = _safe_error_code(live_error or RuntimeError("prestate"))
    elif queue_before_sha256 != queue_after_sha256:
        closure_terminal = "BLOCKED_QUEUE_CHANGED"
        failure_stage = TrafficHarnessStageV02321.QUEUE_POSTSTATE_CAPTURED
        safe_error_code = "QUEUE_CHANGED"
    elif baseline_before_sha256 != baseline_after_sha256:
        closure_terminal = "BLOCKED_BASELINE_CHANGED"
        failure_stage = TrafficHarnessStageV02321.BASELINE_POSTSTATE_CAPTURED
        safe_error_code = "BASELINE_CHANGED"
    elif attempt_start is None:
        closure_terminal = "CLEAN_PRE_TRAFFIC"
        failure_stage = live_failure_stage or trace[-2]
        safe_error_code = _safe_error_code(live_error or RuntimeError("pretraffic"))
    else:
        closure_terminal = "CLEAN_POST_TRAFFIC"
        if attempt_completion is not None and attempt_completion.terminal == "ATTEMPT_PASS":
            failure_stage = None
            safe_error_code = None
        else:
            assert attempt_completion is not None
            failure_stage = attempt_completion.stage
            safe_error_code = attempt_completion.safe_error_code

    closure = TrafficHarnessClosureV02321.build(
        **_event_meta(events),
        session_id=session.session_id,
        attempt_id=attempt_start.attempt_id if attempt_start is not None else None,
        stage_reached=(
            failure_stage
            if failure_stage is not None
            else TrafficHarnessStageV02321.TRAFFIC_EXECUTION_COMPLETE
        ),
        observed_stage_sequence=trace,
        request_plan_sha256=plan.plan_sha256,
        queue_before_sha256=queue_before_sha256,
        queue_after_sha256=queue_after_sha256,
        outer_baseline_before_sha256=baseline_before_sha256,
        outer_baseline_after_sha256=baseline_after_sha256,
        runtime_inspect_request_sha256=(
            runtime_request.normalized_request_sha256
            if TrafficHarnessStageV02321.RUNTIME_INSPECT_REQUESTED in trace
            else None
        ),
        traffic_execution_sha256=(
            execution.execution_sha256 if execution is not None else None
        ),
        traffic_dispatch_failure_sha256=(
            dispatch_failure.dispatch_failure_sha256
            if dispatch_failure is not None
            else None
        ),
        product_cleanup=product_cleanup.model_dump(mode="json"),
        demo_cleanup=demo_cleanup.model_dump(mode="json"),
        owned_resource_counts=counts.model_dump(mode="json"),
        non_owned_resources_changed=non_owned,
        failure_stage=failure_stage,
        safe_error_code=safe_error_code,
        closure_terminal=closure_terminal,
    )
    events.append(closure)
    ledger_tail.append(closure)
    session_completion = InfrastructureSessionCompletionV02321.build(
        **_event_meta(events),
        session_id=session.session_id,
        session_start_sha256=session.event_sha256,
        closure_sha256=closure.closure_sha256,
        stage=TrafficHarnessStageV02321.CLEANUP_COMPLETE,
        stage_reached=closure.stage_reached,
        monotonic_duration_ms=0,
        infrastructure_session_count_after=(
            session.infrastructure_session_count_after
        ),
        cleanup_stage=(
            "BLOCKED"
            if closure.closure_terminal == "BLOCKED_RESOURCE_CLEANUP"
            else "OBSERVATION_COMPLETE"
        ),
        terminal=(
            "SESSION_CLOSED_CLEAN"
            if closure.closure_terminal in {"CLEAN_PRE_TRAFFIC", "CLEAN_POST_TRAFFIC"}
            else "SESSION_CLOSED_BLOCKED"
        ),
    )
    events.append(session_completion)
    ledger_tail.append(session_completion)
    ledger = TrafficPreflightLedgerV02321.build(events=tuple(events))

    live_attempt: LiveTrafficPreflightAttemptV02321 | None = None
    preflight: LiveTrafficPreflightPassV02321 | None = None
    create_artifacts: list[dict[str, Any]] = []
    if (
        execution is not None
        and attempt_start is not None
        and source_after is not None
        and product_after is not None
    ):
        live_attempt = LiveTrafficPreflightAttemptV02321.build(
            attempt_id=attempt_start.attempt_id,
            attempt_ordinal=attempt_start.attempt_ordinal,
            typed_request_plan_sha256=plan.plan_sha256,
            product_state_clone_report_sha256=clone_report.report_sha256,
            product_state_clone_report=clone_report,
            product_state_clone_sha256=clone_report.clone.clone_sha256,
            traffic_contract_sha256=contract.contract_sha256,
            traffic_profile_sha256=profile.profile_sha256,
            formal_profile_sha256=formal_profile.profile_sha256,
            runtime_continuity_descriptor_sha256=tracked_runtime.descriptor_sha256,
            traffic_execution=execution,
            closure_sha256=closure.closure_sha256,
            ledger=ledger,
            source_state_before_sha256=source_before.source_sha256,
            source_state_after_sha256=source_after.source_sha256,
            product_state_before_sha256=product_before.source_sha256,
            product_state_after_sha256=product_after.source_sha256,
            incident_count_before=product_before.source_counts.incident_count,
            incident_count_after=product_after.source_counts.incident_count,
            diagnosis_count_before=product_before.source_counts.diagnosis_count,
            diagnosis_count_after=product_after.source_counts.diagnosis_count,
            infrastructure_session_count_after=ledger.infrastructure_session_count,
            traffic_attempt_count_after=ledger.traffic_attempt_count,
            formal_healthy_traffic_execution_count=0,
            accepted_successor_incident_count=0,
            successor_diagnosis_count=0,
            fault_attempt_count=0,
            knowledge_loop_campaign_count=0,
            agent_writes=0,
            runbook_executions=0,
            provider_calls=0,
            action_authority="NONE",
        )
        create_artifacts.append(
            {
                "path": (
                "docs/analysis/product-v02321-traffic-preflight-attempt-"
                f"{attempt_ordinal}.json"
                ),
                "mode": "CREATE_EXACT",
                "payload": live_attempt.model_dump(mode="json"),
            }
        )
        if live_attempt.terminal == TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321:
            preflight = LiveTrafficPreflightPassV02321.build(
                attempt=live_attempt,
                frozen_traffic_contract_sha256=contract.contract_sha256,
                frozen_preflight_profile_sha256=profile.profile_sha256,
                frozen_formal_profile_sha256=formal_profile.profile_sha256,
                typed_request_plan_schema_sha256=semantic_sha256_v22(
                    TrafficHarnessTypedRequestPlanV02321.model_json_schema()
                ),
                closure_contract_schema_sha256=semantic_sha256_v22(
                    TrafficHarnessClosureContractV02321.model_json_schema()
                ),
                live_traffic_preflight_attempt_count=attempt_ordinal,
                infrastructure_session_count=ledger.infrastructure_session_count,
                traffic_attempt_count=ledger.traffic_attempt_count,
                formal_healthy_traffic_execution_count=0,
                accepted_successor_incident_count=0,
                successor_diagnosis_count=0,
                action_authority="NONE",
            )
            create_artifacts.extend(
                (
                    {
                        "path": "config/product-v02321/typed-request-plan.json",
                        "mode": "CREATE_EXACT",
                        "payload": plan.model_dump(mode="json"),
                    },
                    {
                        "path": (
                            "docs/analysis/"
                            "product-v02321-traffic-preflight.json"
                        ),
                        "mode": "CREATE_EXACT",
                        "payload": preflight.model_dump(mode="json"),
                    },
                )
            )
    else:
        blocker_body: dict[str, Any] = {
            "schema_version": "ecomsre.product.traffic-preflight-blocker.v02321",
            "terminal": "BLOCKED_ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT",
            "attempt_id": attempt_start.attempt_id if attempt_start else None,
            "attempt_ordinal": attempt_ordinal,
            "session_id": session.session_id,
            "failure_stage": failure_stage.value if failure_stage else None,
            "safe_error_code": safe_error_code,
            "closure_sha256": closure.closure_sha256,
            "ledger_sha256": ledger.ledger_sha256,
            "formal_healthy_traffic_execution_count": 0,
            "accepted_successor_incident_count": 0,
            "successor_diagnosis_count": 0,
            "action_authority": "NONE",
        }
        blocker = {
            **blocker_body,
            "blocker_sha256": semantic_sha256_v22(blocker_body),
        }
        blocker_path = (
            (
                "docs/analysis/product-v02321-traffic-preflight-attempt-"
                f"{attempt_ordinal}.json"
            )
            if attempt_start is not None
            else (
                "docs/analysis/product-v02321-traffic-preflight-session-"
                f"{ledger.infrastructure_session_count}-blocker.json"
            )
        )
        create_artifacts.append(
            {
                "path": blocker_path,
                "mode": "CREATE_EXACT",
                "payload": blocker,
            }
        )

    terminal = (
        preflight.terminal
        if preflight is not None
        else "BLOCKED_ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT"
    )
    progress = _updated_progress(
        root,
        terminal=terminal,
        ledger=ledger,
        attempt=live_attempt,
        preflight=preflight,
    )
    artifacts = [
        *create_artifacts,
        {
            "path": "docs/analysis/product-v02321-traffic-preflight-ledger.json",
            "mode": "REPLACE",
            "payload": ledger.model_dump(mode="json"),
        },
        {
            "path": "docs/analysis/product-v02321-progress.json",
            "mode": "REPLACE",
            "payload": progress,
        },
    ]
    publication = _publication_bundle(
        attempt_label=attempt_label,
        terminal=terminal,
        ledger_tail=[event.model_dump(mode="json") for event in ledger_tail],
        artifacts=artifacts,
        attempt=(
            live_attempt.model_dump(mode="json")
            if live_attempt is not None
            else None
        ),
    )
    write_private_json(
        private_root / "publication-bundle.json",
        publication,
        create_once=True,
    )
    published_attempt = _publish_publication_bundle(root, publication)
    if preflight is None:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT")
    if published_attempt is None:
        raise ValueError("Product v0.2.3.2.1 PASS publication lacks Attempt")
    return published_attempt


def run_traffic_preflight_v02321(
    *,
    project_root: Path,
    predecessor_root: Path,
    source_product_root: Path,
    attempt_label: str,
    prior_attempt: Path | None = None,
    changed_surface: str | None = None,
    changed_source_paths: tuple[str, ...] = (),
    repair_rationale: str | None = None,
) -> LiveTrafficPreflightAttemptV02321 | None:
    """Reserve, execute, and recover one append-only preflight iteration."""

    root = Path(project_root).resolve(strict=True)
    if re.fullmatch(_ATTEMPT_LABEL_PATTERN, attempt_label) is None:
        raise ValueError("Product v0.2.3.2.1 private attempt label differs")
    private_root = root / ".local/product-v02321/traffic-preflight" / attempt_label
    if private_root.exists() or private_root.is_symlink():
        publication_path = private_root / "publication-bundle.json"
        if (
            not private_root.is_symlink()
            and private_root.is_dir()
            and not publication_path.exists()
            and (private_root / "pre-session-start.json").is_file()
            and (private_root / "typed-request-plan.json").is_file()
        ):
            _recover_interrupted_traffic_preflight_v02321(
                root=root,
                predecessor_root=Path(predecessor_root),
                source_product_root=Path(source_product_root),
                attempt_label=attempt_label,
                private_root=private_root,
            )
        if (
            private_root.is_symlink()
            or not private_root.is_dir()
            or publication_path.is_symlink()
            or not publication_path.is_file()
        ):
            raise FileExistsError("Product v0.2.3.2.1 private attempt exists")
        publication = _load_object(publication_path)
        recovered = _publish_publication_bundle(root, publication)
        if (
            recovered is None
            or recovered.terminal != TRAFFIC_PREFLIGHT_ATTEMPT_PASS_V02321
        ):
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V02321_TRAFFIC_PREFLIGHT"
            )
        return recovered
    if (root / "docs/analysis/product-v02321-traffic-preflight.json").exists():
        raise FileExistsError("Product v0.2.3.2.1 preflight PASS already exists")

    reservation = _prepare_pre_session_start(
        root,
        attempt_label=attempt_label,
        prior_attempt=prior_attempt,
        changed_surface=changed_surface,
        changed_source_paths=changed_source_paths,
        repair_rationale=repair_rationale,
    )
    private_root.mkdir(parents=True, mode=0o700)
    write_private_json(
        private_root / "pre-session-start.json",
        reservation,
        create_once=True,
    )
    try:
        return _run_reserved_traffic_preflight_v02321(
            project_root=root,
            predecessor_root=predecessor_root,
            source_product_root=source_product_root,
            attempt_label=attempt_label,
            pre_session_start=reservation,
            prior_attempt=prior_attempt,
            changed_surface=changed_surface,
            changed_source_paths=changed_source_paths,
            repair_rationale=repair_rationale,
        )
    except BaseException as error:
        if not (private_root / "publication-bundle.json").exists() and not (
            private_root / "pre-session-completion.json"
        ).exists():
            _write_pre_session_blocker(
                private_root,
                reservation=reservation,
                error=error,
            )
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--predecessor-root",
        type=Path,
        required=True,
        help=(
            "preserved Product v0.2.3 repository root that owns the fixed "
            "Product-state source locator"
        ),
    )
    parser.add_argument("--source-product-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--prior-attempt", type=Path)
    parser.add_argument("--changed-surface")
    parser.add_argument("--changed-source", action="append", default=[])
    parser.add_argument("--repair-rationale")
    arguments = parser.parse_args(argv)
    result = run_traffic_preflight_v02321(
        project_root=arguments.project_root,
        predecessor_root=arguments.predecessor_root,
        source_product_root=arguments.source_product_root,
        attempt_label=arguments.attempt_id,
        prior_attempt=arguments.prior_attempt,
        changed_surface=arguments.changed_surface,
        changed_source_paths=tuple(arguments.changed_source),
        repair_rationale=arguments.repair_rationale,
    )
    assert result is not None
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("run_traffic_preflight_v02321",)
