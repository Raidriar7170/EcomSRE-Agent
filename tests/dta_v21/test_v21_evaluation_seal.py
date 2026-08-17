from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    MetricRecord,
    MetricUnit,
    ToolName,
)
from ecomsre.dta_v2.v21.capture_campaign import build_default_capture_plan_v21
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.evaluation_campaign import (
    build_evaluation_freeze_manifest_v21,
    build_evaluation_preregistration_v21,
    build_evaluation_schedule_v21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationSplitV21,
    PublicCaseBindingV21,
    PublicEvaluationManifestV21,
    ReplayObservationFixtureV21,
)
from ecomsre.dta_v2.v21.evaluation_seal import (
    HeldOutPackSealV21,
    seal_held_out_pack_v21,
    verify_held_out_pack_seal_v21,
)
from ecomsre.dta_v2.v21.owned_capture import build_evaluator_truth_v21
from ecomsre_live_sandbox.contracts import write_private_json


ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-5.4-mini-2026-03-17"
BASE_HEAD = "c0a541fec48f11b02dc2cd6ba41673a777e55eee"
START = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)


def _hashed(model_type, payload: dict[str, object], field: str):
    draft = model_type.model_construct(**payload, **{field: "0" * 64})
    return model_type.model_validate(
        {
            **payload,
            field: semantic_sha256(draft.model_dump(mode="json", exclude={field})),
        }
    )


def _visible_case(case_id: str, scenario_id: str) -> AgentVisibleReplayCaseV21:
    fixture_payload: dict[str, object] = {
        "schema_version": "dta-v21.replay-observation-fixture.v1",
        "tool": ToolName.QUERY_METRICS,
        "service_scope": ("frontend",),
        "records": (
            MetricRecord(
                service="frontend",
                metric_kind=MetricKind.ERROR_RATE,
                value=0.0,
                unit=MetricUnit.RATIO,
                sample_count=20,
            ),
        ),
        "truncated": False,
        "error_code": None,
    }
    fixture = _hashed(ReplayObservationFixtureV21, fixture_payload, "fixture_sha256")
    case_payload: dict[str, object] = {
        "schema_version": "dta-v21.agent-visible-replay-case.v1",
        "case_id": case_id,
        "scenario_id": scenario_id,
        "captured_started_at": START,
        "captured_ended_at": START + timedelta(seconds=30),
        "observations": (fixture,),
        "full_context_tools": (ToolName.QUERY_METRICS,),
    }
    return _hashed(AgentVisibleReplayCaseV21, case_payload, "case_sha256")


def _public_manifest_and_pack(tmp_path: Path):
    plan = build_default_capture_plan_v21(base_head=BASE_HEAD)
    development = []
    held_out = []
    pack = tmp_path / "held-out-pack"
    for item in plan.cases:
        visible = _visible_case(item.case_id, item.scenario_id)
        truth = build_evaluator_truth_v21(item)
        binding = PublicCaseBindingV21(
            case_id=item.case_id,
            case_sha256=visible.case_sha256,
            truth_sha256=truth.truth_sha256,
            split_sha256=semantic_sha256(item.split.value),
        )
        if item.split is EvaluationSplitV21.DEVELOPMENT:
            development.append(binding)
        else:
            held_out.append(binding)
            case_root = pack / "cases" / item.case_id
            write_private_json(
                case_root / "agent-visible.json", visible, create_once=True
            )
            write_private_json(
                case_root / "evaluator-truth.json", truth, create_once=True
            )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.public-evaluation-manifest.v1",
        "case_schema_version": "dta-v21.agent-visible-replay-case.v1",
        "truth_schema_version": "dta-v21.evaluator-case-truth.v1",
        "development_cases": tuple(development),
        "held_out_cases": tuple(held_out),
    }
    return (
        _hashed(PublicEvaluationManifestV21, payload, "manifest_sha256"),
        pack,
    )


def test_seal_is_create_once_and_detects_post_seal_byte_drift(tmp_path: Path) -> None:
    public, pack = _public_manifest_and_pack(tmp_path)
    schedule = build_evaluation_schedule_v21(
        seed_sha256=semantic_sha256("seal test schedule")
    )
    preregistration = build_evaluation_preregistration_v21(
        model_id=MODEL,
        max_completion_tokens=1600,
        schedule_sha256=schedule.schedule_sha256,
    )
    freeze = build_evaluation_freeze_manifest_v21(
        repository_root=ROOT,
        base_code_head=BASE_HEAD,
        model_id=MODEL,
        max_completion_tokens=1600,
        public_case_manifest=public,
        schedule=schedule,
        preregistration=preregistration,
    )

    seal = seal_held_out_pack_v21(
        held_out_pack_root=pack,
        freeze_manifest=freeze,
        schedule=schedule,
        preregistration=preregistration,
        created_at=START,
    )

    assert seal.held_out_executed is False
    assert tuple(item.case_id for item in seal.cases) == tuple(
        f"dta21-case-{index:03d}" for index in range(13, 21)
    )
    persisted = HeldOutPackSealV21.model_validate_json(
        (pack / "held-out-seal.v1.json").read_text(encoding="utf-8")
    )
    verify_held_out_pack_seal_v21(held_out_pack_root=pack, seal=persisted)
    with pytest.raises(FileExistsError):
        seal_held_out_pack_v21(
            held_out_pack_root=pack,
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            created_at=START,
        )

    case_path = pack / "cases/dta21-case-013/agent-visible.json"
    case_path.write_text(case_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after seal"):
        verify_held_out_pack_seal_v21(held_out_pack_root=pack, seal=persisted)


def test_seal_rejects_private_bytes_that_differ_from_public_binding(
    tmp_path: Path,
) -> None:
    public, pack = _public_manifest_and_pack(tmp_path)
    schedule = build_evaluation_schedule_v21(
        seed_sha256=semantic_sha256("seal mismatch schedule")
    )
    preregistration = build_evaluation_preregistration_v21(
        model_id=MODEL,
        max_completion_tokens=1600,
        schedule_sha256=schedule.schedule_sha256,
    )
    freeze = build_evaluation_freeze_manifest_v21(
        repository_root=ROOT,
        base_code_head=BASE_HEAD,
        model_id=MODEL,
        max_completion_tokens=1600,
        public_case_manifest=public,
        schedule=schedule,
        preregistration=preregistration,
    )
    truth_path = pack / "cases/dta21-case-013/evaluator-truth.json"
    truth_path.write_text(
        truth_path.read_text(encoding="utf-8").replace(
            '"truth_sha256":"', '"truth_sha256":"0'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        seal_held_out_pack_v21(
            held_out_pack_root=pack,
            freeze_manifest=freeze,
            schedule=schedule,
            preregistration=preregistration,
            created_at=START,
        )
