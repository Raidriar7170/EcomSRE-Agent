from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre_live_sandbox.contracts import LocalEndpoints
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticJournal,
    DiagnosticRunKind,
    DiagnosticStage,
    ExceptionArtifactStore,
    V3_DIAGNOSTIC_STAGES,
)
from ecomsre_live_sandbox.e2e_v3 import (
    _StageTracker,
    _record_legacy_source_completion_stages,
    _require_ordered_source_collector_stages,
)
from ecomsre_live_sandbox.e2e_source_batch import collect_ordered_source_batch
import ecomsre_live_sandbox.e2e_source_batch as source_batch_module
from ecomsre_live_sandbox.e2e_v3_contracts import load_e2e_v3_config
from ecomsre_live_sandbox.instrumentation_v2 import (
    SourceProbeResult,
    SourceProbeStatus,
    load_instrumentation_config,
)


COLLECTOR_OWNED_COMPLETIONS = (
    DiagnosticStage.SOURCE_BATCH_TERMINALIZATION_COMPLETED,
    DiagnosticStage.METRICS_PREFLIGHT_COMPLETED,
    DiagnosticStage.LOGS_PREFLIGHT_COMPLETED,
    DiagnosticStage.TRACES_PREFLIGHT_COMPLETED,
    DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED,
    DiagnosticStage.SOURCE_AVAILABILITY_GATE_EVALUATED,
)
NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
ENDPOINTS = LocalEndpoints(
    frontend="http://127.0.0.1:18080",
    flag_control="http://127.0.0.1:18080/feature/api",
    flag_evaluation="http://127.0.0.1:18016",
    prometheus="http://127.0.0.1:19090",
    opensearch="http://127.0.0.1:19200",
    jaeger="http://127.0.0.1:11686",
)


def _passed_stages(path: Path) -> list[DiagnosticStage]:
    return [
        DiagnosticStage(event["stage"])
        for event in map(json.loads, path.read_text(encoding="utf-8").splitlines())
        if event["status"] == "PASSED"
    ]


def _available_source(source: str) -> SourceProbeResult:
    backend = {
        "METRICS": "PROMETHEUS_HTTP_API",
        "LOGS": "OPENSEARCH_HTTP_API",
        "TRACES": "JAEGER_QUERY_API",
    }[source]
    return SourceProbeResult(
        source=source,
        backend_kind=backend,
        status=SourceProbeStatus.AVAILABLE,
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        probe_started_at=NOW,
        probe_ended_at=NOW + timedelta(seconds=1),
        attempt_count=1,
        backend_reachable=True,
        raw_response_count=1,
        parsed_record_count=1,
        target_record_count=1,
        service_catalog_count=25,
        target_service_present=True,
        evidence_refs=(f"{source.casefold()}:0001",),
    )


def test_ordered_collector_owns_source_stages_before_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = DiagnosticJournal(
        tmp_path / "journal" / "events.jsonl",
        run_kind=DiagnosticRunKind.INVOCATION_B,
        run_id="invocation-b",
    )
    tracker = _StageTracker(
        journal,
        ExceptionArtifactStore(tmp_path / "journal" / "exceptions"),
    )
    source_results = tuple(
        _available_source(source) for source in ("METRICS", "LOGS", "TRACES")
    )

    def terminalize(*_: object, **__: object) -> tuple[SourceProbeResult, ...]:
        return source_results

    def combined_resolver(*_: object, common_root: Path) -> object:
        path = common_root / "source-resolver.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"records": {}}\n', encoding="utf-8")
        return object()

    monkeypatch.setattr(source_batch_module, "terminalize_source_probes", terminalize)
    monkeypatch.setattr(source_batch_module, "_combined_resolver", combined_resolver)
    monkeypatch.setattr(
        source_batch_module,
        "_revalidate_refs",
        lambda results, **_: (results, True),
    )
    clock = iter((NOW, NOW + timedelta(seconds=30)))

    batch = collect_ordered_source_batch(
        instrumentation=load_instrumentation_config(
            Path("config/live-telemetry-instrumentation-v3")
        ),
        endpoints=ENDPOINTS,
        telemetry_root=tmp_path / "telemetry",
        run_root=tmp_path / "invocation-b",
        run_id="invocation-b",
        projection=load_e2e_v3_config(
            Path("config/live-fault-a0-controlled-remediation-e2e-v3")
        ).projection,
        tracker=tracker,
        sleep=lambda _: None,
        now=lambda: next(clock),
    )
    _require_ordered_source_collector_stages(tracker)
    tracker.pass_stage(DiagnosticStage.MULTISERVICE_PROJECTION_STARTED)
    tracker.pass_stage(DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED)

    assert batch.all_refs_resolve is True
    passed = _passed_stages(journal.path)
    for stage in (
        *COLLECTOR_OWNED_COMPLETIONS,
        DiagnosticStage.MULTISERVICE_PROJECTION_STARTED,
        DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED,
    ):
        assert passed.count(stage) == 1
    source_gate_index = passed.index(
        DiagnosticStage.SOURCE_AVAILABILITY_GATE_EVALUATED
    )
    assert passed[source_gate_index + 1] is DiagnosticStage.MULTISERVICE_PROJECTION_STARTED
    assert not {
        DiagnosticStage.METRICS_PREFLIGHT_STARTED,
        DiagnosticStage.LOGS_PREFLIGHT_STARTED,
        DiagnosticStage.TRACES_PREFLIGHT_STARTED,
    }.intersection(passed[source_gate_index + 1 :])
    assert [tuple(DiagnosticStage).index(stage) for stage in passed] == sorted(
        tuple(DiagnosticStage).index(stage) for stage in passed
    )


def test_legacy_v3_retains_explicit_source_completion_stages(tmp_path: Path) -> None:
    journal = DiagnosticJournal(
        tmp_path / "legacy" / "events.jsonl",
        run_kind=DiagnosticRunKind.INVOCATION_B,
        run_id="invocation-b",
    )
    tracker = _StageTracker(
        journal,
        ExceptionArtifactStore(tmp_path / "legacy" / "exceptions"),
    )

    _record_legacy_source_completion_stages(tracker, invalid_refs=0)
    tracker.pass_stage(DiagnosticStage.MULTISERVICE_PROJECTION_STARTED)
    tracker.pass_stage(DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED)

    passed = _passed_stages(journal.path)
    assert passed == [
        DiagnosticStage.METRICS_PREFLIGHT_STARTED,
        DiagnosticStage.METRICS_PREFLIGHT_COMPLETED,
        DiagnosticStage.LOGS_PREFLIGHT_STARTED,
        DiagnosticStage.LOGS_PREFLIGHT_COMPLETED,
        DiagnosticStage.TRACES_PREFLIGHT_STARTED,
        DiagnosticStage.TRACES_PREFLIGHT_COMPLETED,
        DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED,
        DiagnosticStage.MULTISERVICE_PROJECTION_STARTED,
        DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED,
    ]
    assert set(passed).issubset(V3_DIAGNOSTIC_STAGES)
