from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

UTC_START = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)
UTC_END = UTC_START + timedelta(minutes=5)
CASE_ID = "checkout-latency-001"
EXPECTED_FILES = {
    "manifest.json",
    "incident.json",
    "metrics.json",
    "logs.json",
    "traces.json",
    "changes.json",
}


def replay_api() -> SimpleNamespace:
    from ecomsre.backends.live_protocol import BackendStatus
    from ecomsre.backends.replay import (
        MAX_REPLAY_FILE_BYTES,
        MAX_REPLAY_JSON_DEPTH,
        ReplayLoadError,
        ReplayObservabilityBackend,
        load_replay_case,
    )
    from ecomsre.tools.changes import ChangesQuery
    from ecomsre.tools.logs import LogsQuery
    from ecomsre.tools.metrics import MetricsQuery
    from ecomsre.tools.traces import TracesQuery

    return SimpleNamespace(
        BackendStatus=BackendStatus,
        ChangesQuery=ChangesQuery,
        LogsQuery=LogsQuery,
        MAX_REPLAY_FILE_BYTES=MAX_REPLAY_FILE_BYTES,
        MAX_REPLAY_JSON_DEPTH=MAX_REPLAY_JSON_DEPTH,
        MetricsQuery=MetricsQuery,
        ReplayLoadError=ReplayLoadError,
        ReplayObservabilityBackend=ReplayObservabilityBackend,
        TracesQuery=TracesQuery,
        load_replay_case=load_replay_case,
    )


def incident_payload() -> dict[str, object]:
    return {
        "schema_version": "phase1.incident.v1",
        "incident_id": "inc-001",
        "summary": "Checkout latency exceeds the SLO.",
        "started_at": UTC_START.isoformat().replace("+00:00", "Z"),
        "ended_at": UTC_END.isoformat().replace("+00:00", "Z"),
        "affected_sli": "checkout p95 latency",
        "severity": "SEV2",
        "alert_source_service": "frontend",
    }


def observation(
    *,
    service: str = "checkoutservice",
    started_at: datetime = UTC_START,
    ended_at: datetime = UTC_END,
    observation_type: str = "latency_observation",
) -> dict[str, object]:
    return {
        "service": service,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "ended_at": ended_at.isoformat().replace("+00:00", "Z"),
        "observation_type": observation_type,
        "attributes": {
            "fault_mechanism": "dependency timeout",
            "sample_count": 12,
        },
        "limitations": ["fixture-backed replay only"],
    }


def source_payload(
    *,
    status: str = "AVAILABLE",
    observations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "phase1.replay-observations.v1",
        "status": status,
        "observations": (
            [observation()]
            if observations is None
            else observations
        ),
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=True),
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_case(
    root: Path,
    *,
    case_id: str = CASE_ID,
    statuses: dict[str, str] | None = None,
) -> Path:
    case_dir = root / case_id
    case_dir.mkdir(parents=True)
    statuses = statuses or {}
    write_json(case_dir / "incident.json", incident_payload())
    for source in ("metrics", "logs", "traces", "changes"):
        status = statuses.get(source, "AVAILABLE")
        observations = [] if status != "AVAILABLE" else [observation()]
        write_json(
            case_dir / f"{source}.json",
            source_payload(status=status, observations=observations),
        )
    files = {
        filename: file_sha256(case_dir / filename)
        for filename in sorted(EXPECTED_FILES - {"manifest.json"})
    }
    write_json(
        case_dir / "manifest.json",
        {
            "schema_version": "phase1.replay-manifest.v1",
            "case_id": case_id,
            "files": files,
        },
    )
    return case_dir


def rewrite_manifest(case_dir: Path) -> None:
    files = {
        filename: file_sha256(case_dir / filename)
        for filename in sorted(EXPECTED_FILES - {"manifest.json"})
    }
    write_json(
        case_dir / "manifest.json",
        {
            "schema_version": "phase1.replay-manifest.v1",
            "case_id": case_dir.name,
            "files": files,
        },
    )


def test_valid_case_is_hash_verified_immutable_and_memory_backed(
    tmp_path: Path,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)

    case = api.load_replay_case(tmp_path, CASE_ID)
    expected_metrics_hash = file_sha256(case_dir / "metrics.json")
    moved_case = tmp_path / "fixture-removed-after-load"
    case_dir.rename(moved_case)
    backend = api.ReplayObservabilityBackend(case)
    result = backend.query_metrics(
        api.MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
        timeout_seconds=0.5,
    )

    assert case.case_id == CASE_ID
    assert case.incident.incident_id == "inc-001"
    assert result.status is api.BackendStatus.AVAILABLE
    assert result.raw_artifact_filename == "metrics.json"
    assert result.raw_artifact_sha256 == expected_metrics_hash
    assert len(result.observations) == 1
    assert tuple(
        attribute.name
        for attribute in result.observations[0].attributes
    ) == (
        "fault_mechanism",
        "sample_count",
    )
    with pytest.raises(ValidationError):
        case.case_id = "mutated"  # type: ignore[misc]


def test_loader_requires_exact_approved_observations_json_contract(
    tmp_path: Path,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    approved_document = {
        "schema_version": "phase1.replay-observations.v1",
        "status": "AVAILABLE",
        "observations": [
            {
                "service": "ad",
                "started_at": "2026-07-31T01:01:00Z",
                "ended_at": "2026-07-31T01:02:00Z",
                "observation_type": "request_error_rate",
                "attributes": {
                    "state": "anomalous",
                    "fault_mechanism": "request_processing_failure",
                },
                "limitations": [],
            }
        ],
    }
    write_json(case_dir / "metrics.json", approved_document)
    rewrite_manifest(case_dir)

    replay_case = api.load_replay_case(tmp_path, CASE_ID)

    serialized = replay_case.metrics.model_dump(mode="python")
    assert "observations" in serialized
    assert "rows" not in serialized
    assert len(replay_case.metrics.observations) == 1
    assert "summary" not in serialized["observations"][0]
    assert "raw_index" not in serialized["observations"][0]
    assert serialized["raw_artifact_indices"] == (0,)
    assert replay_case.metrics.raw_artifact_indices == (0,)
    assert (
        replay_case.metrics.observations[0].observation_type
        == "request_error_rate"
    )

    legacy_document = {
        **approved_document,
        "rows": approved_document["observations"],
    }
    del legacy_document["observations"]
    write_json(case_dir / "metrics.json", legacy_document)
    rewrite_manifest(case_dir)

    with pytest.raises(api.ReplayLoadError) as raised:
        api.load_replay_case(tmp_path, CASE_ID)

    assert raised.value.code.value == "INVALID_SCHEMA"

    unexpected_summary_document = json.loads(
        json.dumps(approved_document)
    )
    unexpected_summary_document["observations"][0][
        "summary"
    ] = "not part of the approved observation contract"
    write_json(case_dir / "metrics.json", unexpected_summary_document)
    rewrite_manifest(case_dir)

    with pytest.raises(api.ReplayLoadError) as raised:
        api.load_replay_case(tmp_path, CASE_ID)

    assert raised.value.code.value == "INVALID_SCHEMA"


@pytest.mark.parametrize(
    "case_id",
    (
        "",
        ".",
        "..",
        "../outside",
        "nested/case",
        "/absolute",
        "Uppercase",
        "space case",
        "a" * 65,
    ),
)
def test_loader_rejects_unbounded_or_path_like_case_ids(
    tmp_path: Path,
    case_id: str,
) -> None:
    api = replay_api()
    tmp_path.mkdir(exist_ok=True)

    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, case_id)


def test_loader_rejects_missing_or_non_directory_roots_and_cases(
    tmp_path: Path,
) -> None:
    api = replay_api()
    missing_root = tmp_path / "missing-root"
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(missing_root, CASE_ID)

    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(root_file, CASE_ID)

    valid_root = tmp_path / "valid-root"
    valid_root.mkdir()
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(valid_root, CASE_ID)

    (valid_root / CASE_ID).write_text("not a directory", encoding="utf-8")
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(valid_root, CASE_ID)


def test_loader_rejects_root_case_and_file_symlinks(tmp_path: Path) -> None:
    api = replay_api()
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    case_dir = build_case(real_root)

    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(root_link, CASE_ID)

    case_link_root = tmp_path / "case-link-root"
    case_link_root.mkdir()
    (case_link_root / CASE_ID).symlink_to(case_dir, target_is_directory=True)
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(case_link_root, CASE_ID)

    target = tmp_path / "metrics-target.json"
    target.write_bytes((case_dir / "metrics.json").read_bytes())
    (case_dir / "metrics.json").unlink()
    (case_dir / "metrics.json").symlink_to(target)
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(real_root, CASE_ID)


def test_loader_never_follows_case_directory_replacement_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = replay_api()
    allowed_root = tmp_path / "allowed-root"
    original_case = build_case(allowed_root)
    attacker_root = tmp_path / "attacker-root"
    attacker_case = build_case(attacker_root)
    attacker_incident = incident_payload()
    attacker_incident["incident_id"] = "inc-attacker"
    write_json(attacker_case / "incident.json", attacker_incident)
    rewrite_manifest(attacker_case)
    moved_original = tmp_path / "moved-original-case"
    original_iterdir = Path.iterdir
    original_listdir = os.listdir
    replaced = False

    def replace_case_path() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        original_case.rename(moved_original)
        original_case.symlink_to(attacker_case, target_is_directory=True)

    def replace_before_path_listing(path: Path) -> object:
        if path == original_case:
            replace_case_path()
        return original_iterdir(path)

    def replace_after_capability_open(path: Any) -> list[str]:
        replace_case_path()
        return original_listdir(path)

    monkeypatch.setattr(Path, "iterdir", replace_before_path_listing)
    monkeypatch.setattr(os, "listdir", replace_after_capability_open)

    try:
        replay_case = api.load_replay_case(allowed_root, CASE_ID)
    except api.ReplayLoadError:
        return
    assert replay_case.incident.incident_id == "inc-001"


def test_loader_pins_allowed_root_when_root_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = replay_api()
    allowed_root = tmp_path / "allowed-root"
    original_case = build_case(allowed_root)
    attacker_root = tmp_path / "attacker-root"
    attacker_case = build_case(attacker_root)
    attacker_incident = incident_payload()
    attacker_incident["incident_id"] = "inc-attacker"
    write_json(attacker_case / "incident.json", attacker_incident)
    rewrite_manifest(attacker_case)
    moved_root = tmp_path / "moved-original-root"
    original_iterdir = Path.iterdir
    original_listdir = os.listdir
    replaced = False

    def replace_root_path() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        allowed_root.rename(moved_root)
        allowed_root.symlink_to(attacker_root, target_is_directory=True)

    def replace_before_path_listing(path: Path) -> object:
        if path == original_case:
            replace_root_path()
        return original_iterdir(path)

    def replace_after_capability_open(path: Any) -> list[str]:
        replace_root_path()
        return original_listdir(path)

    monkeypatch.setattr(Path, "iterdir", replace_before_path_listing)
    monkeypatch.setattr(os, "listdir", replace_after_capability_open)

    try:
        replay_case = api.load_replay_case(allowed_root, CASE_ID)
    except api.ReplayLoadError:
        return
    assert replay_case.incident.incident_id == "inc-001"


@pytest.mark.parametrize("mutation", ("missing", "unexpected", "nonregular"))
def test_loader_requires_exact_six_regular_entries(
    tmp_path: Path,
    mutation: str,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    if mutation == "missing":
        (case_dir / "logs.json").unlink()
    elif mutation == "unexpected":
        (case_dir / "notes.txt").write_text("unexpected", encoding="utf-8")
    else:
        (case_dir / "traces.json").unlink()
        (case_dir / "traces.json").mkdir()

    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)


def test_loader_rejects_hash_mismatch(tmp_path: Path) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    with (case_dir / "logs.json").open("a", encoding="utf-8") as target:
        target.write(" ")

    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)


def test_loader_cannot_parse_one_snapshot_and_hash_a_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    metrics_path = case_dir / "metrics.json"
    replacement_bytes = json.dumps(
        source_payload(
            observations=[
                observation(observation_type="replacement_snapshot")
            ]
        ),
        ensure_ascii=False,
    ).encode("utf-8")
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["metrics.json"] = hashlib.sha256(replacement_bytes).hexdigest()
    write_json(case_dir / "manifest.json", manifest)
    replacement_path = tmp_path / "replacement-metrics.json"
    replacement_path.write_bytes(replacement_bytes)
    target_inode = metrics_path.stat().st_ino
    original_read = os.read
    swapped = False

    def read_then_replace(file_descriptor: int, length: int) -> bytes:
        nonlocal swapped
        content = original_read(file_descriptor, length)
        if (
            content
            and os.fstat(file_descriptor).st_ino == target_inode
            and not swapped
        ):
            swapped = True
            replacement_path.replace(metrics_path)
        return content

    monkeypatch.setattr(os, "read", read_then_replace)

    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)
    assert swapped is True


def test_loader_rejects_file_replaced_after_initial_entry_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = replay_api()
    from ecomsre.backends import replay

    case_dir = build_case(tmp_path)
    metrics_path = case_dir / "metrics.json"
    replacement_path = tmp_path / "replacement-metrics.json"
    write_json(
        replacement_path,
        source_payload(
            observations=[
                observation(observation_type="replacement_file")
            ]
        ),
    )
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["metrics.json"] = file_sha256(replacement_path)
    write_json(case_dir / "manifest.json", manifest)
    original_require_regular_file: Any = replay._require_regular_file
    replaced = False

    def validate_then_replace(
        path: Path | str,
        **kwargs: object,
    ) -> object:
        nonlocal replaced
        result = original_require_regular_file(path, **kwargs)
        if Path(path).name == metrics_path.name and not replaced:
            replaced = True
            replacement_path.replace(metrics_path)
        return result

    monkeypatch.setattr(
        replay,
        "_require_regular_file",
        validate_then_replace,
    )

    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)


@pytest.mark.parametrize(
    "invalid_json",
    (
        '{"x": 1, "x": 2}',
        '{"x": NaN}',
        '{"x": Infinity}',
        '{"x": -Infinity}',
    ),
)
def test_loader_rejects_duplicate_keys_and_nonfinite_json(
    tmp_path: Path,
    invalid_json: str,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    (case_dir / "logs.json").write_text(invalid_json, encoding="utf-8")
    rewrite_manifest(case_dir)

    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)


def test_loader_maps_deep_json_recursion_to_stable_invalid_json(
    tmp_path: Path,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    (case_dir / "metrics.json").write_text(
        ("[" * 1_100) + "0" + ("]" * 1_100),
        encoding="utf-8",
    )
    rewrite_manifest(case_dir)

    with pytest.raises(api.ReplayLoadError) as raised:
        api.load_replay_case(tmp_path, CASE_ID)

    assert raised.value.code.value == "INVALID_JSON"


def test_loader_rejects_excessive_json_depth_below_interpreter_limit(
    tmp_path: Path,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    depth = api.MAX_REPLAY_JSON_DEPTH + 1
    (case_dir / "metrics.json").write_text(
        ("[" * depth) + "0" + ("]" * depth),
        encoding="utf-8",
    )
    rewrite_manifest(case_dir)

    with pytest.raises(api.ReplayLoadError) as raised:
        api.load_replay_case(tmp_path, CASE_ID)

    assert raised.value.code.value == "INVALID_JSON"


def test_loader_maps_unsupported_directory_listing_to_stable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = replay_api()
    build_case(tmp_path)
    attempted = False

    def unsupported_listdir(_file_descriptor: int) -> list[str]:
        nonlocal attempted
        attempted = True
        raise NotImplementedError("directory-fd listing is unsupported")

    monkeypatch.setattr(os, "listdir", unsupported_listdir)

    with pytest.raises(api.ReplayLoadError) as raised:
        api.load_replay_case(tmp_path, CASE_ID)

    assert raised.value.code.value == "INVALID_CASE_DIRECTORY"
    assert attempted is True


def test_loader_rejects_non_utf8_and_oversize_files(tmp_path: Path) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    (case_dir / "logs.json").write_bytes(b"\xff")
    rewrite_manifest(case_dir)
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)

    case_dir = tmp_path / CASE_ID
    (case_dir / "logs.json").write_bytes(b" " * (api.MAX_REPLAY_FILE_BYTES + 1))
    rewrite_manifest(case_dir)
    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)


@pytest.mark.parametrize(
    "mutation",
    (
        "manifest_schema",
        "manifest_case",
        "manifest_files",
        "observation_schema",
        "status_observations",
        "evaluator_key",
        "raw_index",
    ),
)
def test_loader_rejects_wrong_closed_world_replay_contracts(
    tmp_path: Path,
    mutation: str,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    if mutation.startswith("manifest_"):
        manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
        if mutation == "manifest_schema":
            manifest["schema_version"] = "phase1.replay-manifest.v2"
        elif mutation == "manifest_case":
            manifest["case_id"] = "another-case"
        else:
            manifest["files"].pop("changes.json")
        write_json(case_dir / "manifest.json", manifest)
    else:
        metrics = json.loads((case_dir / "metrics.json").read_text(encoding="utf-8"))
        if mutation == "observation_schema":
            metrics["schema_version"] = "phase1.replay-observations.v2"
        elif mutation == "status_observations":
            metrics["status"] = "UNAVAILABLE"
        elif mutation == "raw_index":
            metrics["observations"][0]["raw_index"] = 0
        else:
            metrics["observations"][0][
                "evaluator_root_service"
            ] = "paymentservice"
        write_json(case_dir / "metrics.json", metrics)
        rewrite_manifest(case_dir)

    with pytest.raises(api.ReplayLoadError):
        api.load_replay_case(tmp_path, CASE_ID)


def test_backend_filters_closed_window_and_optional_service(
    tmp_path: Path,
) -> None:
    api = replay_api()
    case_dir = build_case(tmp_path)
    observations = [
        observation(
            service="checkoutservice",
            started_at=UTC_START,
            ended_at=UTC_START + timedelta(minutes=1),
            observation_type="left_boundary",
        ),
        observation(
            service="paymentservice",
            started_at=UTC_START + timedelta(minutes=2),
            ended_at=UTC_START + timedelta(minutes=3),
            observation_type="different_service",
        ),
        observation(
            service="checkoutservice",
            started_at=UTC_END,
            ended_at=UTC_END,
            observation_type="right_boundary",
        ),
    ]
    write_json(
        case_dir / "metrics.json",
        source_payload(observations=observations),
    )
    rewrite_manifest(case_dir)
    backend = api.ReplayObservabilityBackend(api.load_replay_case(tmp_path, CASE_ID))

    result = backend.query_metrics(
        api.MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        ),
        timeout_seconds=1.0,
    )

    assert tuple(
        observation.observation_type
        for observation in result.observations
    ) == (
        "left_boundary",
        "right_boundary",
    )
    assert result.raw_artifact_indices == (0, 2)


def test_backend_results_are_deep_snapshots_isolated_from_internal_state(
    tmp_path: Path,
) -> None:
    api = replay_api()
    from ecomsre.phase1.budgets import RunBudget
    from ecomsre.phase1.contracts import BudgetLimits
    from ecomsre.phase1.evidence import EvidenceStore
    from ecomsre.tools.base import ToolContext
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    build_case(tmp_path)
    backend = api.ReplayObservabilityBackend(
        api.load_replay_case(tmp_path, CASE_ID)
    )
    internal_batch = backend._case.metrics
    query = MetricsQuery(
        schema_version="phase1.metrics-query.v1",
        started_at=UTC_START,
        ended_at=UTC_END,
    )

    first = backend.query_metrics(query, timeout_seconds=1.0)

    assert first is not internal_batch
    assert first.observations[0] is not internal_batch.observations[0]
    assert first.raw_artifact_indices is not internal_batch.raw_artifact_indices
    first.observations[0].__dict__["service"] = "mutated-result"
    first.observations[0].__dict__[
        "observation_type"
    ] = "mutated_observation"

    second = backend.query_metrics(query, timeout_seconds=1.0)

    assert second is not first
    assert second.observations[0] is not first.observations[0]
    assert second.observations[0] is not internal_batch.observations[0]
    assert second.observations[0].service == "checkoutservice"
    assert second.observations[0].observation_type == "latency_observation"
    assert second.raw_artifact_indices == (0,)

    store = EvidenceStore("d" * 32)
    tool_context = ToolContext(
        incident=backend._case.incident,
        evidence_store=store,
        budget=RunBudget(
            BudgetLimits(
                max_model_calls=0,
                max_tool_calls=1,
                max_total_tokens=0,
            )
        ),
        backend=backend,
        timeout_seconds=1.0,
    )
    tool_result = query_metrics(tool_context, query)

    assert tool_result.status.value == "OK"
    assert store.snapshot()[0].service == "checkoutservice"
    assert store.snapshot()[0].summary == (
        "metrics observation for checkoutservice: latency_observation."
    )
    assert store.snapshot()[0].raw_artifact_ref == "metrics.json#0"


def test_unavailable_backend_results_never_alias_internal_batch(
    tmp_path: Path,
) -> None:
    api = replay_api()
    build_case(tmp_path, statuses={"metrics": "UNAVAILABLE"})
    backend = api.ReplayObservabilityBackend(
        api.load_replay_case(tmp_path, CASE_ID)
    )
    internal_batch = backend._case.metrics
    query = api.MetricsQuery(
        schema_version="phase1.metrics-query.v1",
        started_at=UTC_START,
        ended_at=UTC_END,
    )

    first = backend.query_metrics(query, timeout_seconds=1.0)
    second = backend.query_metrics(query, timeout_seconds=1.0)

    assert first is not internal_batch
    assert second is not internal_batch
    assert second is not first
    assert first.model_dump(mode="python") == internal_batch.model_dump(
        mode="python"
    )


def test_backend_constructor_revalidates_hidden_replay_case_storage(
    tmp_path: Path,
) -> None:
    api = replay_api()
    from ecomsre.phase1.validator import (
        EvidenceValidationError,
        EvidenceValidationReason,
    )

    build_case(tmp_path)
    replay_case = api.load_replay_case(tmp_path, CASE_ID)
    replay_case.__dict__["evaluator_path"] = "/hidden/evaluator.json"

    with pytest.raises(EvidenceValidationError) as raised:
        api.ReplayObservabilityBackend(replay_case)

    assert (
        raised.value.code
        is EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED
    )


@pytest.mark.parametrize(
    ("query_name", "method_name"),
    (
        ("MetricsQuery", "query_metrics"),
        ("LogsQuery", "search_logs"),
        ("TracesQuery", "search_traces"),
        ("ChangesQuery", "list_changes"),
    ),
)
def test_each_direct_backend_method_revalidates_hidden_query_storage(
    tmp_path: Path,
    query_name: str,
    method_name: str,
) -> None:
    api = replay_api()
    from ecomsre.phase1.validator import (
        EvidenceValidationError,
        EvidenceValidationReason,
    )

    build_case(tmp_path)
    backend = api.ReplayObservabilityBackend(
        api.load_replay_case(tmp_path, CASE_ID)
    )
    source = method_name.removeprefix("query_").removeprefix("search_")
    source = source.removeprefix("list_")
    query = getattr(api, query_name)(
        schema_version=f"phase1.{source}-query.v1",
        started_at=UTC_START,
        ended_at=UTC_END,
    )
    query.__dict__["backend_name"] = "hidden-live-backend"

    with pytest.raises(EvidenceValidationError) as raised:
        getattr(backend, method_name)(query, timeout_seconds=1.0)

    assert (
        raised.value.code
        is EvidenceValidationReason.SCHEMA_REVALIDATION_FAILED
    )


def test_tool_preserves_original_raw_index_after_backend_filtering(
    tmp_path: Path,
) -> None:
    api = replay_api()
    from ecomsre.phase1.budgets import RunBudget
    from ecomsre.phase1.contracts import BudgetLimits
    from ecomsre.phase1.evidence import EvidenceStore
    from ecomsre.tools.base import ToolContext
    from ecomsre.tools.metrics import MetricsQuery, query_metrics

    case_dir = build_case(tmp_path)
    write_json(
        case_dir / "metrics.json",
        source_payload(
            observations=[
                observation(
                    service="paymentservice",
                    observation_type="row_zero",
                ),
                observation(
                    service="checkoutservice",
                    observation_type="row_one",
                ),
            ]
        ),
    )
    rewrite_manifest(case_dir)
    replay_case = api.load_replay_case(tmp_path, CASE_ID)
    backend = api.ReplayObservabilityBackend(replay_case)
    store = EvidenceStore("c" * 32)
    tool_context = ToolContext(
        incident=replay_case.incident,
        evidence_store=store,
        budget=RunBudget(
            BudgetLimits(
                max_model_calls=0,
                max_tool_calls=1,
                max_total_tokens=0,
            )
        ),
        backend=backend,
        timeout_seconds=1.0,
    )

    result = query_metrics(
        tool_context,
        MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
            service="checkoutservice",
        ),
    )

    assert len(result.evidence_refs) == 1
    assert store.snapshot()[0].raw_artifact_ref == "metrics.json#1"


@pytest.mark.parametrize(
    ("status", "expected"),
    (("UNAVAILABLE", "UNAVAILABLE"), ("TIMEOUT", "TIMEOUT")),
)
def test_backend_preserves_unavailable_and_timeout_status(
    tmp_path: Path,
    status: str,
    expected: str,
) -> None:
    api = replay_api()
    build_case(tmp_path, statuses={"metrics": status})
    backend = api.ReplayObservabilityBackend(api.load_replay_case(tmp_path, CASE_ID))

    result = backend.query_metrics(
        api.MetricsQuery(
            schema_version="phase1.metrics-query.v1",
            started_at=UTC_START,
            ended_at=UTC_END,
        ),
        timeout_seconds=1.0,
    )

    assert result.status.value == expected
    assert result.observations == ()


def test_backend_rejects_nonpositive_timeout(tmp_path: Path) -> None:
    api = replay_api()
    build_case(tmp_path)
    backend = api.ReplayObservabilityBackend(api.load_replay_case(tmp_path, CASE_ID))
    query = api.MetricsQuery(
        schema_version="phase1.metrics-query.v1",
        started_at=UTC_START,
        ended_at=UTC_END,
    )

    with pytest.raises(ValueError):
        backend.query_metrics(query, timeout_seconds=0)
