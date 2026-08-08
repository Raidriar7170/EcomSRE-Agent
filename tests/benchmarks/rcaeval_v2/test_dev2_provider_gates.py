from __future__ import annotations

import json
import importlib
from pathlib import Path

import pytest

import ecomsre_rcaeval_v2.dev2_evidence as dev2_evidence
from ecomsre_rcaeval_v2.dev2_paths import (
    journal_root_for,
    reject_dev2_forbidden_paths,
    terminal_path_for,
)
from ecomsre_rcaeval_v2.dev2_schedule import SCHEDULE_SEED, build_schedule
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
                "schema_version": "rcaeval-re2-v2-dev2.provider-smoke-gate.v1",
                "protocol_id": "rcaeval-re2-v2-dev.2",
                "state": "V2_DEV2_PROVIDER_SMOKE_GATE_PASSED",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dev2_evidence, "evidence_source_bindings", lambda **kwargs: {})
    monkeypatch.setattr(
        dev2_evidence,
        "assess_smoke_gate",
        lambda *args, **kwargs: (
            {
                "schema_version": "rcaeval-re2-v2-dev2.provider-smoke-gate.v1",
                "protocol_id": "rcaeval-re2-v2-dev.2",
                "state": "V2_DEV2_PROVIDER_SMOKE_GATE_PASSED",
                "gate_checks": {"terminal_accounting": {"passed": True}},
            },
            True,
        ),
    )
    with pytest.raises(ValueError, match="verification failed"):
        dev2_evidence.verify_passing_smoke_gate(
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
        dev2_evidence.verify_passing_smoke_gate(
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


def test_every_dev2_path_boundary_rejects_tt_markers_before_io(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        reject_dev2_forbidden_paths(tmp_path / "RE2-TT/raw")
    with pytest.raises(ValueError, match="forbidden"):
        reject_dev2_forbidden_paths(tmp_path / "evaluator-only/output")


def test_publisher_requires_fresh_canonical_f0_reverification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    publisher = importlib.import_module(
        "scripts.rcaeval_v2.publish_dev2_results"
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
