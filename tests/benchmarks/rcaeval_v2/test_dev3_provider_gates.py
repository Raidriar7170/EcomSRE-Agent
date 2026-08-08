from __future__ import annotations

import json
import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ecomsre_rcaeval_v2.dev3_evidence as dev3_evidence
from ecomsre_rcaeval_v2.dev3_audit import Dev2FailureAuditLock
from ecomsre_rcaeval_v2.dev3_provider import (
    Dev2FailureEvidence,
    audit_dev2_failures,
)
from ecomsre_rcaeval_v2.public_projection import assert_public_payload
from ecomsre_rcaeval_v2.dev3_paths import (
    journal_root_for,
    reject_dev3_forbidden_paths,
    terminal_path_for,
)
from ecomsre_rcaeval_v2.dev3_schedule import SCHEDULE_SEED, build_schedule
from ecomsre_rcaeval_v2.schedule import (
    SPLIT_SEED,
    CaseIdentity,
    SplitName,
    build_split_assignments,
)


def _identities() -> tuple[CaseIdentity, ...]:
    services = {
        "RE2-OB": ("checkoutservice", "currencyservice", "emailservice", "productcatalogservice", "recommendationservice"),
        "RE2-SS": ("carts", "catalogue", "orders", "payment", "user"),
    }
    return tuple(
        CaseIdentity(system=system, root_cause_service=service, fault=fault, instance=str(instance))  # type: ignore[arg-type]
        for system, system_services in services.items()
        for service in system_services
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket")
        for instance in (1, 2, 3)
    )


def test_state_only_smoke_gate_cannot_unlock_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = tmp_path / "control/evidence/provider-smoke-gate.json"
    gate.parent.mkdir(parents=True)
    gate.write_text(
        json.dumps(
            {
                "schema_version": "rcaeval-re2-v2-dev3.provider-smoke-gate.v1",
                "protocol_id": "rcaeval-re2-v2-dev.3",
                "state": "V2_DEV3_PROVIDER_SMOKE_GATE_PASSED",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dev3_evidence, "evidence_source_bindings", lambda **kwargs: {})
    monkeypatch.setattr(
        dev3_evidence,
        "assess_smoke_gate",
        lambda *args, **kwargs: (
            {
                "schema_version": "rcaeval-re2-v2-dev3.provider-smoke-gate.v1",
                "protocol_id": "rcaeval-re2-v2-dev.3",
                "state": "V2_DEV3_PROVIDER_SMOKE_GATE_PASSED",
                "gate_checks": {"terminal_accounting": {"passed": True}},
            },
            True,
        ),
    )
    with pytest.raises(ValueError, match="verification failed"):
        dev3_evidence.verify_passing_smoke_gate(
            gate,
            control_root=tmp_path / "control",
            private_schedule_root=tmp_path / "private-schedules",
            output_root=tmp_path / "output",
            smoke_journal_root=tmp_path / "smoke-journal",
            design_journal_root=tmp_path / "design-journal",
            project_root=tmp_path / "repo",
            smoke_schedule=(),
        )


def test_smoke_verifier_rejects_forbidden_private_schedule_path_before_io(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        dev3_evidence.verify_passing_smoke_gate(
            tmp_path / "control/evidence/provider-smoke-gate.json",
            control_root=tmp_path / "control",
            private_schedule_root=tmp_path / "RE2-TT/private-schedules",
            output_root=tmp_path / "output",
            smoke_journal_root=tmp_path / "smoke-journal",
            design_journal_root=tmp_path / "design-journal",
            project_root=tmp_path / "repo",
            smoke_schedule=(),
        )


def test_smoke_rows_reuse_smoke_journal_and_new_design_rows_use_design_root(
    tmp_path: Path,
) -> None:
    assignments = build_split_assignments(_identities(), seed=SPLIT_SEED)
    design = build_schedule(assignments, SplitName.DESIGN, seed=SCHEDULE_SEED)
    smoke_ids = {record.run_id for record in design[:6]}
    smoke_record = design[0]
    design_record = next(record for record in design if record.run_id not in smoke_ids)
    smoke_root = tmp_path / "smoke-journal"
    design_root = tmp_path / "design-journal"
    assert journal_root_for(
        smoke_record,
        phase="design",
        smoke_run_ids=smoke_ids,
        smoke_journal_root=smoke_root,
        design_journal_root=design_root,
    ) == smoke_root
    selected = journal_root_for(
        design_record,
        phase="design",
        smoke_run_ids=smoke_ids,
        smoke_journal_root=smoke_root,
        design_journal_root=design_root,
    )
    assert selected == design_root
    assert terminal_path_for(design_record, selected).is_relative_to(design_root)


def test_every_dev3_path_boundary_rejects_tt_markers_before_io(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        reject_dev3_forbidden_paths(tmp_path / "RE2-TT/raw")
    with pytest.raises(ValueError, match="forbidden"):
        reject_dev3_forbidden_paths(tmp_path / "evaluator-only/output")


def test_path_boundary_rejects_safe_alias_resolving_into_tt(tmp_path: Path) -> None:
    target = tmp_path / "RE2-TT"
    target.mkdir()
    alias = tmp_path / "safe-alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="forbidden"):
        reject_dev3_forbidden_paths(alias / "opaque.json")


def test_publisher_requires_fresh_canonical_f0_reverification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    publisher = importlib.import_module(
        "scripts.rcaeval_v2.publish_dev3_results"
    )
    monkeypatch.setattr(
        publisher, "verify_provider_ready", lambda *args, **kwargs: (object(), object())
    )
    monkeypatch.setattr(publisher, "run_reverification", lambda **kwargs: False)
    with pytest.raises(ValueError, match="F0 reverification drift"):
        publisher.publish(
            ob_root=tmp_path / "ob",
            ss_root=tmp_path / "ss",
            control_root=tmp_path / "control",
            private_schedule_root=tmp_path / "private-schedules",
            output_root=tmp_path / "output",
            smoke_journal_root=tmp_path / "smoke",
            design_journal_root=tmp_path / "design",
            preserved_roots={},
        )


def test_publisher_projects_identity_free_failure_audit_and_final_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    publisher = importlib.import_module("scripts.rcaeval_v2.publish_dev3_results")
    evidence = Dev2FailureEvidence(
        architecture_family="V2",
        variant="dynamic_v2_dev2",
        operation_type="FINAL_JUDGE",
        operation_stage="PROVIDER_CALL",
        failure_code="PROVIDER_TRANSPORT_FAILURE",
        safe_http_status_class=None,
        provider_attempt_index=1,
        provider_call_index=4,
        latency_bucket="<10s",
        valid_response_received=False,
        usage_object_received=False,
        token_usage_known=False,
        timestamp_bucket="2026-08-08T10:00Z",
        canonical_request_sha256=None,
    )
    lock = Dev2FailureAuditLock(
        schema_version="rcaeval-re2-v2-dev3.failure-audit-lock.v1",
        protocol_id="rcaeval-re2-v2-dev.3",
        audited_at_utc=datetime.now(timezone.utc),
        evaluation_root_lock_sha256="e" * 64,
        dev2_smoke_gate_sha256="a" * 64,
        dev2_smoke_schedule_sha256="b" * 64,
        dev2_smoke_journal_tree_sha256="c" * 64,
        audit=audit_dev2_failures((evidence,)),
    )
    projected = publisher._public_failure_audit(lock, lock_sha256="d" * 64)
    assert projected["failure_classes"] == {"UNKNOWN_INSUFFICIENT_EVIDENCE": 1}
    assert projected["retry_eligible"] == 0
    assert "groups" not in projected
    assert_public_payload(projected)

    handoff = publisher._agent_redesign_handoff(publisher.FINAL_PROVIDER_LIMIT)
    assert "zero escalation for easy cases" in handoff
    assert "contradiction-aware fusion" in handoff
    assert "Damage Rate" in handoff
    assert "Implement Single-first Adaptive RCA Agent" in handoff
    assert "do not create another Harness-only" in handoff
    assert_public_payload(handoff)
