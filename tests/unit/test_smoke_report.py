import json
from pathlib import Path
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.evidence.hashes import canonical_json_sha256
from ecomsre.evidence.models import IntegrityManifest
from ecomsre.evidence.store import ReportEvidenceStore
from ecomsre.phase0.smoke import (
    RecoverySealIndexEntry,
    SmokeSupervisorState,
    _build_smoke_attempt_evidence,
    _run_artifact_hashes,
    reseal_recovery_evidence,
    validate_current_recovery_seal,
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
        owned_volume_cleanup_attempted=True,
        owned_volume_cleanup_succeeded=True,
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
        owned_volume_cleanup_completed=True,
        failure_reason_codes=(
            ()
            if status is DiagnosticStatus.PASSED
            else ("POST_UP_EVIDENCE_PERSISTENCE_FAILED",)
        ),
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


def test_recovery_reseal_is_append_only_and_covers_final_artifacts(
    tmp_path: Path,
) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        run_root = tmp_path / zone / RUN_ID
        run_root.mkdir(parents=True)
        (run_root / "evidence.json").write_text("{}", encoding="utf-8")
    report = _report(status=DiagnosticStatus.UNSAFE)
    assert report.diagnostic_status is DiagnosticStatus.UNSAFE
    with ReportEvidenceStore(tmp_path, RUN_ID) as store:
        store.write_smoke_report(report)
        store.write_human_summary(report)
        initial_hashes = _run_artifact_hashes(tmp_path, run_id=RUN_ID)
        store.write_checksums(
            IntegrityManifest(
                schema_version="phase0.integrity.v1",
                run_id=RUN_ID,
                content_hashes=initial_hashes,
                manifest_sha256=canonical_json_sha256(initial_hashes),
            )
        )
    old_checksum = (
        tmp_path / "reports" / RUN_ID / "checksums.sha256"
    ).read_bytes()
    audit = (
        tmp_path
        / "observer-visible"
        / RUN_ID
        / "commands"
        / "process-audit.jsonl"
    )
    audit.parent.mkdir(parents=True)
    audit.write_text('{"event":"stop-complete"}\n', encoding="utf-8")

    first = reseal_recovery_evidence(
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        sequence=1,
        disposition="SAFE_STOP_COMPLETED",
        reason_code="BOUNDED_RECOVERY",
    )

    assert first.current
    assert (tmp_path / "reports" / RUN_ID / "checksums.sha256").read_bytes() == (
        old_checksum
    )
    assert (
        tmp_path / "reports" / RUN_ID / "recovery" / "001.json"
    ).is_file()
    first_seal = tmp_path / "reports" / RUN_ID / "seals" / "001.sha256"
    assert first_seal.is_file()
    assert f"reports/{RUN_ID}/checksums.sha256" in first_seal.read_text(
        encoding="utf-8"
    )
    assert validate_current_recovery_seal(tmp_path, run_id=RUN_ID)

    initial_checksum = tmp_path / "reports" / RUN_ID / "checksums.sha256"
    initial_bytes = initial_checksum.read_bytes()
    initial_checksum.write_bytes(initial_bytes + b"# tampered\n")
    assert not validate_current_recovery_seal(tmp_path, run_id=RUN_ID)
    initial_checksum.write_bytes(initial_bytes)
    assert validate_current_recovery_seal(tmp_path, run_id=RUN_ID)

    with audit.open("a", encoding="utf-8") as stream:
        stream.write('{"event":"late-audit"}\n')
    assert not validate_current_recovery_seal(tmp_path, run_id=RUN_ID)

    second = reseal_recovery_evidence(
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        sequence=2,
        disposition="AUDIT_RESEALED",
        reason_code="LATE_AUDIT_CAPTURED",
    )

    assert second.sequence == 2
    second_seal = tmp_path / "reports" / RUN_ID / "seals" / "002.sha256"
    assert first_seal.is_file()
    assert second_seal.is_file()
    assert f"reports/{RUN_ID}/seals/001.sha256" in second_seal.read_text(
        encoding="utf-8"
    )
    index_path = tmp_path / "reports" / RUN_ID / "seal-index.jsonl"
    index_lines = index_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["sequence"] for line in index_lines] == [1, 2]
    assert validate_current_recovery_seal(tmp_path, run_id=RUN_ID)

    first_seal_bytes = first_seal.read_bytes()
    first_seal.write_bytes(first_seal_bytes + b"# tampered\n")
    assert not validate_current_recovery_seal(tmp_path, run_id=RUN_ID)
    first_seal.write_bytes(first_seal_bytes)
    assert validate_current_recovery_seal(tmp_path, run_id=RUN_ID)

    original_index = index_path.read_bytes()
    tampered_lines = original_index.decode("utf-8").splitlines()
    first_entry = json.loads(tampered_lines[0])
    first_entry["checksum_sha256"] = "0" * 64
    tampered_lines[0] = json.dumps(first_entry, separators=(",", ":"))
    index_path.write_text("\n".join(tampered_lines) + "\n", encoding="utf-8")
    assert not validate_current_recovery_seal(tmp_path, run_id=RUN_ID)
    index_path.write_bytes(original_index)
    assert validate_current_recovery_seal(tmp_path, run_id=RUN_ID)


def test_recovery_seal_rejects_non_allowlisted_checksum_path() -> None:
    with pytest.raises(ValidationError, match="outside the allowlist"):
        RecoverySealIndexEntry(
            schema_version="phase0.recovery-seal-index.v1",
            run_id=RUN_ID,
            sequence=1,
            checksum_path="../../outside.sha256",
            checksum_sha256="a" * 64,
            content_manifest_sha256="b" * 64,
            prior_index_sha256="c" * 64,
        )


def test_recovery_reseal_rejects_out_of_order_sequence(tmp_path: Path) -> None:
    for zone in ("observer-visible", "evaluator-only"):
        (tmp_path / zone / RUN_ID).mkdir(parents=True)

    with pytest.raises(ValueError, match="next append-only sequence"):
        reseal_recovery_evidence(
            artifacts_root=tmp_path,
            run_id=RUN_ID,
            sequence=2,
            disposition="OUT_OF_ORDER",
            reason_code="OUT_OF_ORDER",
        )


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
