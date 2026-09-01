from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.formal_recovery_v0233 import (
    FormalAttemptLedgerV0233,
    FormalAttemptRecordV0233,
    FormalCheckpointRepositoryV0233,
    FormalExecutionCheckpointV0233,
    FormalExecutionStateV0233,
    FormalOperationalSurfaceV0233,
    FormalSemanticSurfaceV0233,
    build_legacy_attempt1_record_v0233,
    determine_earliest_safe_resume_state_v0233,
    verify_checkpoint_artifacts_v0233,
)
from scripts.product_v0233 import resume_formal_nofault as resume_command
from scripts.product_v0233.run_formal_nofault import _formal_surfaces_v0233


ROOT = Path(__file__).resolve().parents[2]


def _sha(character: str) -> str:
    return character * 64


def _semantic_surface(*, generation: int = 1) -> FormalSemanticSurfaceV0233:
    return FormalSemanticSurfaceV0233.build(
        semantic_generation=generation,
        checkout_traffic_contract_sha256=_sha("1"),
        checkout_traffic_source_sha256=_sha("0"),
        preflight_profile_sha256=_sha("2"),
        formal_profile_sha256=_sha("3"),
        active_profile_sha256=_sha("4"),
        active_baseline_id="base-" + "5" * 24,
        active_baseline_sha256=_sha("5"),
        source_selection_sha256=_sha("6"),
        formal_clone_contract_sha256=_sha("f"),
        runtime_authority_contract_sha256=_sha("7"),
        service_identity_contract_sha256=_sha("8"),
        capability_contract_sha256=_sha("9"),
        diagnosis_source_sha256_by_path={
            "src/ecomsre/product/jobs/handlers.py": _sha("a"),
            "src/ecomsre/product/incidents/diagnosis_bridge.py": _sha("b"),
        },
        nofault_scorer_source_sha256=_sha("c"),
        stage_journal_contract_sha256=_sha("d"),
    )


def _operational_surface(character: str = "e") -> FormalOperationalSurfaceV0233:
    return FormalOperationalSurfaceV0233.build(
        operational_file_sha256_by_path={
            "scripts/product_v0233/run_formal_nofault.py": _sha(character),
            "src/ecomsre/product/pilot/formal_recovery_v0233.py": _sha("f"),
        }
    )


def _first_checkpoint(
    *,
    attempt_id: str = "attempt-2",
    generation: int = 1,
    operational: FormalOperationalSurfaceV0233 | None = None,
) -> FormalExecutionCheckpointV0233:
    semantic = _semantic_surface(generation=generation)
    operational = operational or _operational_surface()
    return FormalExecutionCheckpointV0233.build(
        previous=None,
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=generation,
        attempt_id=attempt_id,
        state=FormalExecutionStateV0233.PREPARED,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=operational.operational_surface_sha256,
        source_selection_sha256=_sha("6"),
        formal_clone_sha256=None,
        input_artifact_sha256s={
            "config/product-v0233/campaign.json": _sha("1")
        },
        output_artifact_sha256s={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_semantic_and_operational_surfaces_are_independent_and_self_sealed() -> None:
    semantic = _semantic_surface()
    operational = _operational_surface("e")
    repaired_operational = _operational_surface("0")

    assert semantic.semantic_surface_sha256 == semantic_sha256_v22(
        semantic.model_dump(mode="json", exclude={"semantic_surface_sha256"})
    )
    assert operational.operational_surface_sha256 == semantic_sha256_v22(
        operational.model_dump(mode="json", exclude={"operational_surface_sha256"})
    )
    assert repaired_operational.operational_surface_sha256 != (
        operational.operational_surface_sha256
    )
    assert semantic.semantic_generation == 1


def test_live_repository_surface_builder_excludes_operational_code_from_semantics() -> (
    None
):
    semantic, operational = _formal_surfaces_v0233(ROOT, semantic_generation=1)

    assert "scripts/product_v0233/run_formal_nofault.py" not in (
        semantic.diagnosis_source_sha256_by_path
    )
    assert "scripts/product_v0233/run_formal_nofault.py" in (
        operational.operational_file_sha256_by_path
    )
    assert "src/ecomsre/product/pilot/formal_recovery_v0233.py" in (
        operational.operational_file_sha256_by_path
    )


def test_checkpoint_chain_accepts_valid_transitions_and_operational_repair() -> None:
    prepared = _first_checkpoint()
    environment_ready = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
        operational_surface_sha256=_operational_surface("0").operational_surface_sha256,
        formal_clone_sha256=_sha("1"),
        output_artifact_sha256s={
            "docs/analysis/product-v0233-attempts/attempt-2/formal-clone.json": (
                _sha("1")
            )
        },
        created_at=prepared.created_at + timedelta(seconds=1),
    )

    assert environment_ready.sequence == 2
    assert environment_ready.previous_checkpoint_sha256 == prepared.checkpoint_sha256
    assert environment_ready.semantic_surface_sha256 == prepared.semantic_surface_sha256
    assert environment_ready.operational_surface_sha256 != (
        prepared.operational_surface_sha256
    )
    assert determine_earliest_safe_resume_state_v0233(environment_ready) == (
        FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY
    )


def test_checkpoint_rejects_invalid_transition_or_semantic_change_in_same_attempt() -> (
    None
):
    prepared = _first_checkpoint()
    with pytest.raises(ValueError, match="transition"):
        FormalExecutionCheckpointV0233.build(
            previous=prepared,
            state=FormalExecutionStateV0233.SCORED,
            created_at=prepared.created_at + timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="semantic surface"):
        FormalExecutionCheckpointV0233.build(
            previous=prepared,
            state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
            semantic_surface_sha256=_sha("0"),
            created_at=prepared.created_at + timedelta(seconds=1),
        )

    generation_two = _first_checkpoint(attempt_id="attempt-3", generation=2)
    assert generation_two.semantic_generation == 2
    assert generation_two.attempt_id == "attempt-3"


def test_checkpoint_repository_is_append_only_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    repository = FormalCheckpointRepositoryV0233(
        tmp_path / ".local/product-v0233/attempts/attempt-2"
    )
    prepared = _first_checkpoint()
    repository.append(prepared)
    traffic_running = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
        formal_clone_sha256=_sha("1"),
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    repository.append(traffic_running)

    assert repository.load_chain() == (prepared, traffic_running)
    with pytest.raises(FileExistsError):
        repository.append(traffic_running)

    second_path = repository.checkpoint_path(traffic_running)
    payload = json.loads(second_path.read_text(encoding="utf-8"))
    payload["state"] = FormalExecutionStateV0233.SCORED.value
    second_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, ValidationError), match="checkpoint"):
        repository.load_chain()


def test_attempt_ledger_preserves_legacy_attempt_and_one_measured_result() -> None:
    legacy = FormalAttemptRecordV0233.build(
        attempt_id="attempt-1",
        ordinal=1,
        semantic_generation=1,
        disposition="LEGACY_BLOCKED",
        latest_state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        latest_checkpoint_sha256=None,
        blocker_terminal="BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        measured_terminal=None,
        evidence_sha256_by_path={
            "docs/analysis/product-v0233-formal-blocker.json": _sha("1")
        },
    )
    active = FormalAttemptRecordV0233.build(
        attempt_id="attempt-2",
        ordinal=2,
        semantic_generation=1,
        disposition="ACTIVE",
        latest_state=FormalExecutionStateV0233.PREPARED,
        latest_checkpoint_sha256=_sha("2"),
        blocker_terminal=None,
        measured_terminal=None,
        evidence_sha256_by_path={},
    )
    ledger = FormalAttemptLedgerV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        attempts=(legacy, active),
    )

    assert ledger.latest_attempt_id == "attempt-2"
    assert ledger.measured_result_count == 0
    assert ledger.ledger_sha256 == semantic_sha256_v22(
        ledger.model_dump(mode="json", exclude={"ledger_sha256"})
    )


def test_resume_artifact_verification_fails_closed_on_missing_or_changed_bytes(
    tmp_path: Path,
) -> None:
    campaign = tmp_path / "config/product-v0233/campaign.json"
    campaign.parent.mkdir(parents=True)
    campaign.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(campaign.read_bytes()).hexdigest()
    checkpoint = FormalExecutionCheckpointV0233.build(
        previous=None,
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=1,
        attempt_id="attempt-2",
        state=FormalExecutionStateV0233.PREPARED,
        semantic_surface_sha256=_sha("2"),
        operational_surface_sha256=_sha("3"),
        source_selection_sha256=_sha("4"),
        formal_clone_sha256=None,
        input_artifact_sha256s={"config/product-v0233/campaign.json": digest},
        output_artifact_sha256s={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    verify_checkpoint_artifacts_v0233(tmp_path, checkpoint)
    campaign.write_text('{"drift":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="artifact"):
        verify_checkpoint_artifacts_v0233(tmp_path, checkpoint)


def test_resume_command_verifies_chain_surfaces_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semantic = _semantic_surface()
    checkpoint_operational = _operational_surface("e")
    current_operational = _operational_surface("0")
    campaign = tmp_path / "config/product-v0233/campaign.json"
    campaign.parent.mkdir(parents=True)
    campaign.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(campaign.read_bytes()).hexdigest()
    checkpoint = FormalExecutionCheckpointV0233.build(
        previous=None,
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=1,
        attempt_id="attempt-2",
        state=FormalExecutionStateV0233.PREPARED,
        semantic_surface_sha256=semantic.semantic_surface_sha256,
        operational_surface_sha256=(
            checkpoint_operational.operational_surface_sha256
        ),
        source_selection_sha256=_sha("6"),
        formal_clone_sha256=None,
        input_artifact_sha256s={"config/product-v0233/campaign.json": digest},
        output_artifact_sha256s={},
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    repository = FormalCheckpointRepositoryV0233(
        tmp_path / ".local/product-v0233/attempts/attempt-2"
    )
    repository.append(checkpoint)
    monkeypatch.setattr(
        resume_command,
        "_formal_surfaces_v0233",
        lambda _root, *, semantic_generation: (semantic, current_operational),
    )

    decision = resume_command.inspect_formal_resume_v0233(
        project_root=tmp_path,
        attempt_id="attempt-2",
    )

    assert decision["resume_state"] == "PREPARED"
    assert decision["operational_surface_changed"] is True
    assert decision["referenced_artifacts_verified"] is True
    assert decision["decision_sha256"] == semantic_sha256_v22(
        {key: value for key, value in decision.items() if key != "decision_sha256"}
    )


def test_live_legacy_attempt1_record_binds_existing_bytes_without_checkpoint() -> None:
    record = build_legacy_attempt1_record_v0233(ROOT)
    tracked_record = FormalAttemptRecordV0233.model_validate_json(
        (
            ROOT
            / "docs/analysis/product-v0233-attempts/attempt-1/legacy-reference.json"
        ).read_bytes()
    )
    tracked_ledger = FormalAttemptLedgerV0233.model_validate_json(
        (ROOT / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )

    assert record.attempt_id == "attempt-1"
    assert tracked_record == record
    assert tracked_ledger.attempts == (record,)
    assert record.disposition == "LEGACY_BLOCKED"
    assert record.latest_checkpoint_sha256 is None
    assert record.blocker_terminal == (
        "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
    )
    assert record.measured_terminal is None
    assert record.evidence_sha256_by_path[
        "docs/analysis/product-v0233-formal-blocker.json"
    ] == "a02cce3787c1a443f365c83c4207b6256de431d792ebd6fded628ca36bc32ed1"
