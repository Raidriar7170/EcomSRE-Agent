from __future__ import annotations

from ecomsre_rcaeval.contracts import (
    Architecture,
    Diagnosis,
    GroundTruth,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.reporting import build_final_report
from ecomsre_rcaeval.scoring import normalize_indicator


def test_final_report_uses_complete_preregistered_comparisons() -> None:
    services = tuple(f"service-{index}" for index in range(1, 6))
    faults = ("cpu", "mem", "disk", "delay", "loss", "socket")
    truth: dict[str, GroundTruth] = {}
    records: list[TerminalRecord] = []
    case_index = 0
    for service in services:
        for fault in faults:
            for instance in ("1", "2", "3"):
                case_index += 1
                case_id = f"tt-case-{case_index:04d}"
                item = GroundTruth(
                    case_id=case_id,
                    root_cause_service=service,
                    fault=fault,
                    instance=instance,
                )
                truth[case_id] = item
                for architecture in Architecture:
                    correct = architecture is not Architecture.SINGLE or instance == "1"
                    diagnosis = Diagnosis(
                        root_cause_service=service if correct else "wrong-service",
                        root_cause_indicator=normalize_indicator(fault),
                        evidence_refs=("metric:0001",),
                        explanation="Synthetic report fixture.",
                    )
                    records.append(
                        TerminalRecord(
                            run_id=f"{case_index:030x}{tuple(Architecture).index(architecture):02x}",
                            case_id=case_id,
                            architecture=architecture,
                            terminal_status=TerminalStatus.COMPLETED,
                            diagnosis=diagnosis,
                            tool_calls=(6 if architecture is Architecture.DYNAMIC else 8),
                            model_calls=1,
                            known_provider_tokens=100,
                            latency_seconds=1.0,
                        )
                    )

    report = build_final_report(tuple(records), truth, bootstrap_replicates=100)

    assert report["primary_superiority_supported"] is True
    assert report["cost_quality_supported"] is True
    assert report["terminal_taxonomy"] == {"COMPLETED": 270}
    assert len(report["scored_cases"]) == 270
