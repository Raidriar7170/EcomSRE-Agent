from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.product_v0233.project_blocked_evidence import _project_exact_file
from scripts.ci.verify_product_v0233_terminal import (
    _REQUIRED_ABSENCES,
    _verify_attempt3_diagnosis_failure_supplement_v0233,
    _verify_required_absences,
    verify_product_v0233_terminal,
)


def _validate_object(payload: bytes) -> object:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("fixture is not a JSON object")
    return value


def test_exact_projection_creates_once_and_accepts_identical_bytes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private.json"
    target = tmp_path / "public.json"
    payload = b'{"status":"BLOCKED"}'
    source.write_bytes(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    first = _project_exact_file(
        source=source,
        target=target,
        expected_sha256=expected_sha256,
        validator=_validate_object,
    )
    second = _project_exact_file(
        source=source,
        target=target,
        expected_sha256=expected_sha256,
        validator=_validate_object,
    )

    assert first == second == expected_sha256
    assert target.read_bytes() == payload


def test_exact_projection_rejects_source_hash_drift(tmp_path: Path) -> None:
    source = tmp_path / "private.json"
    source.write_text('{"status":"CHANGED"}', encoding="utf-8")

    with pytest.raises(ValueError, match="source SHA-256 differs"):
        _project_exact_file(
            source=source,
            target=tmp_path / "public.json",
            expected_sha256="0" * 64,
            validator=_validate_object,
        )


def test_exact_projection_rejects_different_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "private.json"
    target = tmp_path / "public.json"
    payload = b'{"status":"BLOCKED"}'
    source.write_bytes(payload)
    target.write_text('{"status":"DIFFERENT"}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="public artifact differs"):
        _project_exact_file(
            source=source,
            target=target,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            validator=_validate_object,
        )


def test_exact_projection_rejects_existing_symlink(tmp_path: Path) -> None:
    source = tmp_path / "private.json"
    target = tmp_path / "public.json"
    elsewhere = tmp_path / "elsewhere.json"
    payload = b'{"status":"BLOCKED"}'
    source.write_bytes(payload)
    elsewhere.write_bytes(payload)
    target.symlink_to(elsewhere)

    with pytest.raises(FileExistsError, match="public artifact differs"):
        _project_exact_file(
            source=source,
            target=target,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            validator=_validate_object,
        )


def test_repository_terminal_is_publicly_verifiable() -> None:
    root = Path(__file__).resolve().parents[2]
    result = verify_product_v0233_terminal(root)
    ledger = json.loads(
        (root / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    latest = ledger["attempts"][-1]

    assert result["attempt_id"] == latest["attempt_id"]
    assert result["measured_result_count"] == ledger["measured_result_count"]
    assert result["action_authority"] == "NONE"
    assert result["closure"] == "CLEAN"
    if ledger["measured_result_count"] == 0:
        blocker = json.loads(
            (
                root
                / "docs/analysis/product-v0233-attempts"
                / latest["attempt_id"]
                / "formal-blocker.json"
            ).read_bytes()
        )
        assert result["terminal"] == latest["blocker_terminal"]
        assert result["new_incident_count"] == blocker["new_incident_count"]
        assert result["new_diagnosis_count"] == blocker["new_diagnosis_count"]
    else:
        assert result["terminal"] == (
            "ECOMSRE_PRODUCT_V0233_NOFAULT_ACCEPTANCE_COMPLETE"
        )
        assert result["measured_terminal"] == latest["measured_terminal"]


def test_attempt3_diagnosis_failure_supplement_is_publicly_verifiable() -> None:
    root = Path(__file__).resolve().parents[2]
    ledger = json.loads(
        (root / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )

    result = _verify_attempt3_diagnosis_failure_supplement_v0233(root, ledger)

    assert result["attempt_id"] == "attempt-3"
    assert result["failure_stage"] == "READ_ACQUISITION_STARTED"
    assert result["repair_classification"] == "SEMANTIC_GENERATION_CHANGE_REQUIRED"
    assert result["successor_semantic_generation"] == 3


def _mutable_attempt3_supplement(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    root = Path(__file__).resolve().parents[2]
    relative_root = Path("docs/analysis/product-v0233-attempts/attempt-3")
    target_root = tmp_path / relative_root
    target_root.mkdir(parents=True)
    (target_root / "formal-blocker.json").write_bytes(
        (root / relative_root / "formal-blocker.json").read_bytes()
    )
    supplement = json.loads(
        (root / relative_root / "diagnosis-failure-supplement.json").read_bytes()
    )
    ledger = json.loads(
        (root / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    return tmp_path, supplement, ledger


def _reseal(payload: dict[str, object], field: str) -> None:
    payload[field] = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != field}
    )


def _write_mutated_supplement(root: Path, payload: dict[str, object]) -> None:
    _reseal(payload, "supplement_sha256")
    path = (
        root
        / "docs/analysis/product-v0233-attempts/attempt-3/"
        "diagnosis-failure-supplement.json"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_attempt3_supplement_rejects_resealed_cross_job_journal(
    tmp_path: Path,
) -> None:
    root, supplement, ledger = _mutable_attempt3_supplement(tmp_path)
    events = supplement["journal_tail_events"]
    envelope = supplement["failure_envelope"]
    job = supplement["diagnosis_job"]
    assert isinstance(events, list) and len(events) == 2
    assert isinstance(events[0], dict) and isinstance(events[1], dict)
    assert isinstance(envelope, dict) and isinstance(job, dict)
    events[0]["job_id"] = "job-aaaaaaaaaaaaaaaaaaaaaaaa"
    _reseal(events[0], "event_sha256")
    envelope["journal_tail_sha256"] = events[0]["event_sha256"]
    _reseal(envelope, "failure_envelope_sha256")
    events[1].update(
        {
            "job_id": "job-aaaaaaaaaaaaaaaaaaaaaaaa",
            "previous_event_sha256": events[0]["event_sha256"],
            "input_binding_sha256": events[0]["event_sha256"],
            "output_artifact_sha256": envelope["failure_envelope_sha256"],
        }
    )
    _reseal(events[1], "event_sha256")
    job["journal_tail_sha256"] = events[1]["event_sha256"]
    _write_mutated_supplement(root, supplement)

    with pytest.raises(ValueError, match="Attempt 3 supplement differs"):
        _verify_attempt3_diagnosis_failure_supplement_v0233(root, ledger)


def test_attempt3_supplement_rejects_resealed_non_diagnosis_job(
    tmp_path: Path,
) -> None:
    root, supplement, ledger = _mutable_attempt3_supplement(tmp_path)
    job = supplement["diagnosis_job"]
    assert isinstance(job, dict)
    job["job_type"] = "ENVIRONMENT_VERIFY"
    _write_mutated_supplement(root, supplement)

    with pytest.raises(ValueError, match="Attempt 3 supplement differs"):
        _verify_attempt3_diagnosis_failure_supplement_v0233(root, ledger)


def test_attempt3_supplement_rejects_resealed_incomplete_changes_scope(
    tmp_path: Path,
) -> None:
    root, supplement, ledger = _mutable_attempt3_supplement(tmp_path)
    capability = supplement["capability_matrix"]
    incident = supplement["incident"]
    envelope = supplement["failure_envelope"]
    events = supplement["journal_tail_events"]
    job = supplement["diagnosis_job"]
    assert isinstance(capability, dict) and isinstance(incident, dict)
    assert isinstance(envelope, dict) and isinstance(events, list)
    assert isinstance(events[1], dict) and isinstance(job, dict)
    sources = capability["sources"]
    assert isinstance(sources, list) and isinstance(sources[0], dict)
    sources[0]["status"] = "PARTIAL"
    sources[0]["covered_services"] = [
        service
        for service in sources[0]["covered_services"]
        if service != "checkout"
    ]
    _reseal(capability, "capability_sha256")
    incident["source_capability_sha256"] = capability["capability_sha256"]
    _reseal(incident, "incident_sha256")
    envelope["capability_sha256"] = capability["capability_sha256"]
    envelope["incident_sha256"] = incident["incident_sha256"]
    _reseal(envelope, "failure_envelope_sha256")
    events[1]["output_artifact_sha256"] = envelope["failure_envelope_sha256"]
    _reseal(events[1], "event_sha256")
    job["journal_tail_sha256"] = events[1]["event_sha256"]
    _write_mutated_supplement(root, supplement)

    with pytest.raises(ValueError, match="Attempt 3 supplement differs"):
        _verify_attempt3_diagnosis_failure_supplement_v0233(root, ledger)


@pytest.mark.parametrize("relative_path", _REQUIRED_ABSENCES)
def test_blocked_repository_rejects_every_forbidden_success_artifact(
    tmp_path: Path,
    relative_path: str,
) -> None:
    forbidden = tmp_path / relative_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("fabricated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden terminal artifact exists"):
        _verify_required_absences(tmp_path, list(_REQUIRED_ABSENCES))
