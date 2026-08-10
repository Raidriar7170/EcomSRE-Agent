from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from ecomsre_rca100.contracts import (
    RCA100DiagnosisProvenance,
    RCA100MetricsArbitrationAction,
)
from ecomsre_rca100.entity import load_entity_catalog
from ecomsre_rca100.entity import normalize_entity_name
from ecomsre_rca100.evaluator import (
    RCA100GroundTruth,
    evaluate_terminals,
    fault_correct,
    parse_ground_truth,
)
from ecomsre_rca100.lifecycle import build_schedule
from ecomsre_rca100.runner import RCA100TerminalRecord, RCA100TerminalStatus
from ecomsre_rca100.statistics import exact_mcnemar_p_value, paired_inference


def test_ground_truth_parser_prefers_structured_outcome_ids() -> None:
    truth = parse_ground_truth(
        "t001",
        "F1-latency.synthetic",
        {
            "root_cause_entities": ["fallback-name"],
            "root_cause_types": ["Network Latency"],
            "raw_ground_truth": {
                "outcome": {
                    "target_entity_ids": ["svc-a"],
                    "target_entities": [
                        {"entity_id": "svc-b", "entity_name": "Service B"}
                    ],
                }
            },
        },
    )

    assert truth.target_entity_ids == ("svc-a", "svc-b")
    assert truth.target_entity_names == ("Service B",)
    assert fault_correct(" network latency ", truth)
    assert normalize_entity_name("  SÉRVICE   A ") == "sérvice a"


def test_paired_inference_is_deterministic_and_uses_exact_mcnemar() -> None:
    initial = (True, False, False, True)
    final = (True, True, False, False)

    first = paired_inference(initial, final)
    second = paired_inference(initial, final)

    assert first == second
    assert first.damage == 1
    assert first.damage_rate_denominator == 2
    assert first.damage_rate == 0.5
    assert first.rescue == 1
    assert first.net_rescue == 0
    assert first.point_difference == 0.0
    assert first.mcnemar_exact_p_value == 1.0
    assert exact_mcnemar_p_value(0, 3) == 0.25
    assert first.classification == "RCA100_EXTERNAL_M3_NOT_SUPPORTED"


def test_canonical_evaluation_includes_all_frozen_descriptive_subgroups(
    tmp_path: Path,
) -> None:
    topology = tmp_path / "topology.json"
    topology.write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "root",
                        "type": "apm.service",
                        "name": "root",
                        "props": {},
                    },
                    {
                        "id": "wrong",
                        "type": "apm.service",
                        "name": "wrong",
                        "props": {},
                    },
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    catalog = load_entity_catalog(topology)
    schedule = build_schedule(tuple(f"t{index:03d}" for index in range(1, 104)))
    now = datetime.now(timezone.utc)
    terminals: dict[str, RCA100TerminalRecord] = {}
    truths: dict[str, RCA100GroundTruth] = {}
    catalogs = {}
    alerts = {}
    for record in schedule.records:
        initial = "apm|apm.service|root" if record.position == 1 else "apm|apm.service|wrong"
        final = "apm|apm.service|wrong" if record.position == 1 else "apm|apm.service|root"
        terminals[record.opaque_case_id] = RCA100TerminalRecord(
            run_id=record.run_id,
            opaque_case_id=record.opaque_case_id,
            schedule_position=record.position,
            status=RCA100TerminalStatus.COMPLETED,
            initial_root_entity_ref=initial,
            final_root_entity_ref=final,
            initial_fault_type="latency",
            final_fault_type="latency",
            m3_action=RCA100MetricsArbitrationAction.OVERRIDE_METRICS_TOP1,
            m3_reason_codes=("M3_OVERRIDE_RANK_AND_MARGIN",),
            initial_metrics_rank_or_none=None,
            metrics_top1_entity_ref=final,
            metrics_top2_entity_ref_or_none=initial,
            normalized_margin=0.5,
            root_provenance=(
                RCA100DiagnosisProvenance.DETERMINISTIC_METRICS_M3
            ),
            fault_type_provenance="MODEL_INITIAL",
            initial_evidence_refs=("metric:0002",),
            final_evidence_refs=("metric:0001",),
            metrics_projection_status="AVAILABLE",
            semantic_model_operations=1,
            provider_attempts=1,
            transport_retries=0,
            known_token_lower_bound=100,
            conservative_token_upper_bound=32_000,
            request_sha256="d" * 64,
            latency_seconds=1.0,
            started_at_utc=now,
            ended_at_utc=now,
        )
        truths[record.source_task_id] = RCA100GroundTruth(
            source_task_id=record.source_task_id,
            canonical_case_id=f"F1-latency.synthetic-{record.position}",
            target_entity_ids=("root",),
            fault_types=("latency",),
        )
        catalogs[record.source_task_id] = catalog
        alerts[record.source_task_id] = "k8s.pod"

    aggregate, _ = evaluate_terminals(
        schedule=schedule,
        terminals=terminals,
        truths=truths,
        catalogs=catalogs,
        alert_entity_types=alerts,
    )

    root_result = aggregate["root"]
    assert isinstance(root_result, dict)
    assert aggregate["primary_inference_eligible"] == 103
    assert root_result["damage"] == 1
    assert root_result["damage_rate_denominator"] == 1
    assert root_result["damage_rate"] == 1.0
    subgroups = aggregate["descriptive_subgroups"]
    assert isinstance(subgroups, dict)
    assert set(subgroups) == {
        "fault_category",
        "fault_type",
        "root_entity_domain_type",
        "alert_entity_type",
        "m3_action",
        "m3_applicability",
        "initial_rank",
        "margin_bin",
        "metrics_projection_status",
    }
    for records in subgroups.values():
        assert isinstance(records, list)
        assert sum(item["denominator"] for item in records) == 103
