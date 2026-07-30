from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ecomsre.phase0.models import MeasurementPhase
from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    PhaseWindow,
)
from ecomsre.telemetry.probe import (
    ProbeAdapter,
    ProbeReason,
)
from ecomsre.telemetry.prometheus import (
    _load_test_query_registry,
    load_query_registry,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "telemetry"
FROZEN = _load_test_query_registry(FIXTURES / "frozen-query-registry.json")
UNRESOLVED = load_query_registry(
    ROOT / "config" / "phase0" / "telemetry-queries-v3.0.0.json"
)
RUN_ID = "4" * 32
START = datetime(2026, 7, 30, 1, 2, 0, tzinfo=UTC)


class RecordingStore:
    _synthetic_telemetry_test_double = True

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.fail_at = fail_at

    def write_immutable(
        self,
        relative_path: str,
        value: dict[str, Any],
    ) -> SimpleNamespace:
        self.records.append((relative_path, value))
        if self.fail_at == len(self.records):
            raise ValueError("fixture evidence failure")
        return SimpleNamespace(
            path=Path("observer-visible") / RUN_ID / relative_path,
            sha256="a" * 64,
        )


class OneResponseClient:
    _synthetic_telemetry_test_double = True

    def __init__(
        self,
        body: bytes,
        *,
        observed_at: datetime | None = None,
        monotonic_ended: float = 20.0,
    ) -> None:
        self.run_id = RUN_ID
        self.body = body
        self.observed_at = observed_at or START + timedelta(seconds=20)
        self.monotonic_ended = monotonic_ended
        self.requests: list[HttpRequest] = []

    def request(self, request: HttpRequest) -> HttpExchange:
        self.requests.append(request)
        return HttpExchange(
            reason=HttpReason.OK,
            request=request,
            started_at=self.observed_at - timedelta(seconds=1),
            ended_at=self.observed_at,
            monotonic_started_at=self.monotonic_ended - 1,
            monotonic_ended_at=self.monotonic_ended,
            status_code=200,
            response_headers=(("Content-Type", "application/json"),),
            raw_body=self.body,
            raw_sha256=sha256_bytes(self.body),
            raw_body_partial=False,
        )


def _window(phase: MeasurementPhase, offset: int) -> PhaseWindow:
    start = START + timedelta(seconds=offset)
    return PhaseWindow(
        run_id=RUN_ID,
        cycle_number=1,
        scenario_phase=phase,
        utc_started_at=start,
        utc_ended_at=start + timedelta(seconds=30),
        monotonic_started_at=float(offset),
        monotonic_ended_at=float(offset + 30),
    )


def test_probe_refuses_repository_unresolved_candidate_without_http() -> None:
    client = OneResponseClient(b"")
    store = RecordingStore()

    result = ProbeAdapter(
        client=client,
        evidence_store=store,
        fixture=UNRESOLVED,
    ).observe(
        window=_window(MeasurementPhase.BASELINE, 0),
        base_url="http://127.0.0.1:32774",
        artifact_prefix="cycles/001/baseline",
    )

    assert not result.observed
    assert result.reason is ProbeReason.QUERY_FIXTURE_NOT_FROZEN
    assert client.requests == []
    assert store.records == []


def test_frozen_probe_records_command_raw_exit_attribution_and_hidden_denial() -> None:
    body = (FIXTURES / "probe-current.json").read_bytes()
    client = OneResponseClient(body)
    store = RecordingStore()

    result = ProbeAdapter(
        client=client,
        evidence_store=store,
        fixture=FROZEN,
    ).observe(
        window=_window(MeasurementPhase.BASELINE, 0),
        base_url="http://127.0.0.1:32774",
        artifact_prefix="cycles/001/baseline",
    )

    assert result.observed
    assert result.reason is ProbeReason.OBSERVED
    assert result.exit_code == 0
    assert result.trace_id is None
    assert result.request_id is None
    assert len(store.records) == 2
    raw = store.records[0][1]
    assert raw["sanitized_command"] == [
        "HTTP",
        "GET",
        "/api/data?contextKeys=telescopes",
    ]
    assert raw["input_capability_schema"] == "phase0.probe-observer-input.v1"
    assert raw["unexpected_input_count"] == 1
    assert not raw["observer_input_boundary_passed"]
    assert base64.b64decode(raw["raw_response_base64"]) == body
    assert "getads_attempts" not in raw
    assert "getads_errors" not in raw
    assert store.records[1][1]["getads_attribution_proof_artifact"].startswith(
        "observer-visible/"
    )


def test_probe_rejects_synthetic_ads_envelope_not_emitted_by_pinned_upstream() -> None:
    body = b'{"ads":[{"id":"invented-envelope"}]}'
    result = ProbeAdapter(
        client=OneResponseClient(body),
        evidence_store=RecordingStore(),
        fixture=FROZEN,
    ).observe(
        window=_window(MeasurementPhase.BASELINE, 0),
        base_url="http://127.0.0.1:32774",
        artifact_prefix="cycles/001/baseline",
    )

    assert not result.observed
    assert result.reason is ProbeReason.PROBE_SCHEMA_INVALID


def test_probe_rejects_non_upstream_ad_shape_and_unknown_fields() -> None:
    for body in (
        b'[{"id":"not-an-upstream-ad"}]',
        b'[{"redirectUrl":"/product/1"}]',
        b'[{"redirectUrl":"/product/1","text":"ad","extra":true}]',
        b'[{"redirectUrl":1,"text":"ad"}]',
    ):
        result = ProbeAdapter(
            client=OneResponseClient(body),
            evidence_store=RecordingStore(),
            fixture=FROZEN,
        ).observe(
            window=_window(MeasurementPhase.BASELINE, 0),
            base_url="http://127.0.0.1:32774",
            artifact_prefix="cycles/001/baseline",
        )
        assert result.reason is ProbeReason.PROBE_SCHEMA_INVALID


def test_probe_module_has_no_controller_or_hidden_truth_import_path() -> None:
    source = ROOT / "src" / "ecomsre" / "telemetry" / "probe.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert all("ecomsre.scenarios" not in module for module in imports)
    assert all("ground_truth" not in module for module in imports)
    assert all("flagd" not in module for module in imports)


def test_probe_phase_coverage_requires_same_frozen_contract_in_all_three_phases() -> (
    None
):
    body = (FIXTURES / "probe-current.json").read_bytes()
    store = RecordingStore()
    observations = []
    for phase, offset in (
        (MeasurementPhase.BASELINE, 0),
        (MeasurementPhase.FAULT, 40),
        (MeasurementPhase.RECOVERY, 80),
    ):
        observations.append(
            ProbeAdapter(
                client=OneResponseClient(
                    body,
                    observed_at=START + timedelta(seconds=offset + 20),
                    monotonic_ended=float(offset + 20),
                ),
                evidence_store=store,
                fixture=FROZEN,
            ).observe(
                window=_window(phase, offset),
                base_url="http://127.0.0.1:32774",
                artifact_prefix=f"cycles/001/{phase.value}",
            )
        )
    adapter = ProbeAdapter(
        client=OneResponseClient(body),
        evidence_store=store,
        fixture=FROZEN,
    )

    coverage = adapter.validate_phase_coverage(
        observations=tuple(observations),
        artifact_prefix="cycles/001",
    )

    assert not coverage.complete
    assert coverage.reason is ProbeReason.THREE_PHASE_COVERAGE_INCOMPLETE
    assert set(coverage.phases) == {"baseline", "fault", "recovery"}
    assert not store.records[-1][1]["decision"]


def test_probe_phase_coverage_rejects_missing_phase_and_evidence_failure() -> None:
    body = (FIXTURES / "probe-current.json").read_bytes()
    store = RecordingStore()
    adapter = ProbeAdapter(
        client=OneResponseClient(body),
        evidence_store=store,
        fixture=FROZEN,
    )
    baseline = adapter.observe(
        window=_window(MeasurementPhase.BASELINE, 0),
        base_url="http://127.0.0.1:32774",
        artifact_prefix="cycles/001/baseline",
    )

    incomplete = adapter.validate_phase_coverage(
        observations=(baseline,),
        artifact_prefix="cycles/001",
    )
    assert not incomplete.complete
    assert incomplete.reason is ProbeReason.THREE_PHASE_COVERAGE_INCOMPLETE

    failing_store = RecordingStore(fail_at=1)
    failed = ProbeAdapter(
        client=OneResponseClient(body),
        evidence_store=failing_store,
        fixture=FROZEN,
    ).observe(
        window=_window(MeasurementPhase.BASELINE, 0),
        base_url="http://127.0.0.1:32774",
        artifact_prefix="cycles/001/baseline",
    )
    assert failed.reason is ProbeReason.EVIDENCE_PERSISTENCE_FAILED
    assert not failed.observed


def test_probe_phase_coverage_requires_client_run_one_cycle_and_bound_paths() -> None:
    body = (FIXTURES / "probe-current.json").read_bytes()
    store = RecordingStore()
    adapter = ProbeAdapter(
        client=OneResponseClient(body),
        evidence_store=store,
        fixture=FROZEN,
    )
    observations = tuple(
        ProbeAdapter(
            client=OneResponseClient(
                body,
                observed_at=START + timedelta(seconds=offset + 20),
                monotonic_ended=float(offset + 20),
            ),
            evidence_store=store,
            fixture=FROZEN,
        ).observe(
            window=_window(phase, offset),
            base_url="http://127.0.0.1:32774",
            artifact_prefix=f"cycles/001/{phase.value}",
        )
        for phase, offset in (
            (MeasurementPhase.BASELINE, 0),
            (MeasurementPhase.FAULT, 40),
            (MeasurementPhase.RECOVERY, 80),
        )
    )

    wrong_cycle = (
        observations[0],
        replace(observations[1], cycle_number=2),
        observations[2],
    )
    wrong_run = (
        observations[0],
        replace(observations[1], run_id="5" * 32),
        observations[2],
    )
    wrong_path = (
        observations[0],
        replace(observations[1], artifact_paths=("cycles/99/fault/raw.json",)),
        observations[2],
    )

    for candidate in (wrong_cycle, wrong_run, wrong_path):
        coverage = adapter.validate_phase_coverage(
            observations=candidate,
            artifact_prefix="cycles/001",
        )
        assert not coverage.complete
        assert coverage.reason is ProbeReason.THREE_PHASE_COVERAGE_INCOMPLETE
