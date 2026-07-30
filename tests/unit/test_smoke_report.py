from pathlib import Path
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.evidence.hashes import canonical_json_sha256
from ecomsre.evidence.models import IntegrityManifest
from ecomsre.evidence.store import ReportEvidenceStore
from ecomsre.phase0.smoke import (
    SmokeSupervisorState,
    _build_smoke_attempt_evidence,
    _run_artifact_hashes,
)
from ecomsre.phase0.models import (
    DiagnosticStatus,
    SmokeAttemptEvidence,
    SmokeControlAcknowledgement,
    SmokePhaseEvidence,
    SmokeReport,
)


RUN_ID = "d" * 32
NOW = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)


def _attempt() -> SmokeAttemptEvidence:
    phases = tuple(
        SmokePhaseEvidence(
            phase=phase,
            attempts=100,
            errors=(10 if phase == "fault" else 0),
            error_rate=(0.1 if phase == "fault" else 0),
            wilson_lower=0,
            wilson_upper=(0.2 if phase == "fault" else 0.04),
            window_started_at=NOW + timedelta(minutes=index),
            window_ended_at=NOW + timedelta(minutes=index, seconds=30),
            monotonic_duration_seconds=30,
            fixture_sha256="f" * 64,
            raw_artifact_refs=(f"raw-{phase}.json",),
            passed=True,
            reason_code="THRESHOLD_PASSED",
        )
        for index, phase in enumerate(("baseline", "fault", "recovery"))
    )
    control_identities = (
        ("promotion", "baseline"),
        ("promotion", "fault"),
        ("promotion", "recovery"),
        ("diagnostic", "baseline"),
        ("diagnostic", "fault"),
        ("diagnostic", "recovery"),
        ("finalization", None),
    )
    controls = tuple(
        SmokeControlAcknowledgement(
            stage=stage,
            phase=phase,
            transition_succeeded=True,
            acknowledgement_duration_seconds=0.1,
            reason_code="CONTROL_STATE_CONFIRMED",
            artifact_ref=f"control-{index}.json",
        )
        for index, (stage, phase) in enumerate(control_identities)
    )
    return SmokeAttemptEvidence(
        phase_evidence=phases,
        control_acknowledgements=controls,
        initial_readiness_artifacts=("initial.json",),
        post_promotion_readiness_artifacts=("post.json",),
        final_readiness_artifacts=("final.json",),
        probe_attribution_artifacts=("probe.json",),
        safe_reset_attempted=True,
        safe_reset_succeeded=True,
        fresh_stop_authority=True,
        safe_stop_attempted=True,
        safe_stop_succeeded=True,
        failure_reason_codes=(),
    )


def _report(*, status: DiagnosticStatus = DiagnosticStatus.PASSED) -> SmokeReport:
    return SmokeReport(
        schema_version="phase0.smoke-report.v1",
        run_id=RUN_ID,
        canonical=False,
        diagnostic_status=status,
        phase0_complete=False,
        formal_three_cycle_acceptance_executed=False,
        policy={
            "cycles": 1,
            "stabilization_seconds": 30,
            "minimum_getads_attempts_per_window": 100,
            "window_deadline_seconds": 120,
            "baseline_max_error_rate": 0.01,
            "fault_min_error_rate": 0.05,
            "fault_max_error_rate": 0.20,
            "recovery_max_error_rate": 0.01,
        },
        phase_decisions={
            "baseline": True,
            "fault": True,
            "recovery": True,
        },
        telemetry_gate_decisions={
            "prometheus": True,
            "jaeger": True,
            "opensearch": True,
            "probe": True,
        },
        task7_registry_frozen=True,
        origin_promotion_run_id="a" * 32,
        attempts=(_attempt(),),
        safe_stop_completed=True,
        failure_reason_codes=(),
    )


def test_smoke_report_can_pass_without_phase0_success_semantics(tmp_path: Path) -> None:
    report = _report()

    assert report.diagnostic_status is DiagnosticStatus.PASSED
    assert report.canonical is False
    assert report.phase0_complete is False
    assert not hasattr(report, "outcome")

    with ReportEvidenceStore(tmp_path, RUN_ID) as store:
        stored = store.write_smoke_report(report)

    assert stored.path.name == "smoke-report.json"


def test_passing_smoke_requires_safe_stop_and_no_failure_reasons() -> None:
    payload = _report().model_dump(mode="python")
    payload["safe_stop_completed"] = False
    with pytest.raises(ValidationError):
        SmokeReport.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transition_succeeded", False),
        ("acknowledgement_duration_seconds", 30.001),
        ("reason_code", "UNCONFIRMED"),
    ],
)
def test_passing_smoke_requires_exact_successful_control_acknowledgements(
    field: str,
    value: object,
) -> None:
    payload = _report().model_dump(mode="python")
    payload["attempts"][0]["control_acknowledgements"][0][field] = value

    with pytest.raises(ValidationError, match="diagnostic proof"):
        SmokeReport.model_validate(payload)


@pytest.mark.parametrize("fixture_sha256", [None, "0" * 64, "e" * 64])
def test_passing_smoke_requires_one_nonzero_fixture_hash(
    fixture_sha256: str | None,
) -> None:
    payload = _report().model_dump(mode="python")
    payload["attempts"][0]["phase_evidence"][1]["fixture_sha256"] = fixture_sha256

    with pytest.raises(ValidationError, match="diagnostic proof"):
        SmokeReport.model_validate(payload)


def test_complete_smoke_report_summary_and_checksum_bundle(
    tmp_path: Path,
) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / RUN_ID
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")
    report = _report()
    with ReportEvidenceStore(tmp_path, RUN_ID) as store:
        store.write_smoke_report(report)
        store.write_human_summary(report)
        hashes = _run_artifact_hashes(tmp_path, run_id=RUN_ID)
        store.write_checksums(
            IntegrityManifest(
                schema_version="phase0.integrity.v1",
                run_id=RUN_ID,
                content_hashes=hashes,
                manifest_sha256=canonical_json_sha256(hashes),
            )
        )

    summary = (
        tmp_path / "reports" / RUN_ID / "human-summary.md"
    ).read_text(encoding="utf-8")
    checksums = (
        tmp_path / "reports" / RUN_ID / "checksums.sha256"
    ).read_text(encoding="utf-8")
    assert "Canonical acceptance: `false`" in summary
    assert "Phase 0 complete: `false`" in summary
    assert "## Phase measurements" in summary
    assert "`fault`: attempts=100, errors=10, error_rate=0.100000" in summary
    assert "## Control acknowledgements" in summary
    assert "`finalization/none`: succeeded=true" in summary
    assert "## Backend freshness decisions" in summary
    assert "`jaeger` freshness gate: `true`" in summary
    assert f"reports/{RUN_ID}/smoke-report.json" in checksums
    assert f"reports/{RUN_ID}/human-summary.md" in checksums


def test_attempt_builder_includes_promotion_transition_acknowledgements(
    tmp_path: Path,
) -> None:
    transition = (
        tmp_path
        / "observer-visible"
        / RUN_ID
        / "telemetry"
        / "promotion"
        / "transitions"
        / "01-baseline.json"
    )
    transition.parent.mkdir(parents=True)
    transition.write_text(
        (
            '{"stage":"promotion","phase":"baseline",'
            '"transition_succeeded":true,'
            '"acknowledgement_duration_seconds":0.1,'
            '"reason_code":"CONTROL_STATE_CONFIRMED"}'
        ),
        encoding="utf-8",
    )

    attempt = _build_smoke_attempt_evidence(
        tmp_path,
        state=SmokeSupervisorState(run_id=RUN_ID),
    )

    assert len(attempt.control_acknowledgements) == 1
    assert attempt.control_acknowledgements[0].stage == "promotion"
