from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ecomsre_rcaeval.dataset import DevCase
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.indicator_evaluation import (
    audit_metric_value_quality,
    audit_metric_schemas,
    build_runtime_metric_candidates,
    evaluate_design_formulas,
    read_metric_samples,
)
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    SplitAssignment,
    SplitName,
)


CONFIG_PATH = (
    Path(__file__).parents[3]
    / "config"
    / "rcaeval-re2-v2-dev"
    / "indicator-candidate-formulas.json"
)


def _config():
    digest = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    return load_indicator_config(CONFIG_PATH, expected_sha256=digest)


def _case(
    root: Path,
    *,
    system: str,
    service: str,
    fault: str,
    instance: str,
) -> DevCase:
    return DevCase(
        case_id=f"{system.lower()}-case-0001",
        system=system,
        root=root,
        metrics_path=root / "simple_metrics.csv",
        logs_path=root / "logs.csv",
        traces_path=None,
        inject_time=1_000,
        root_cause_service=service,
        fault=fault,  # type: ignore[arg-type]
        instance=instance,
    )


def _assignment(case: DevCase, split: SplitName) -> SplitAssignment:
    identity = CaseIdentity(
        system=case.system,  # type: ignore[arg-type]
        root_cause_service=case.root_cause_service,
        fault=case.fault,
        instance=case.instance,
    )
    return SplitAssignment(
        identity=identity,
        split=split,
        selection_digest_sha256=hashlib.sha256(
            repr(identity).encode("utf-8")
        ).hexdigest(),
    )


def test_schema_audit_reads_headers_only_and_reports_explicit_mapping(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-SS" / "orders_mem" / "1"
    root.mkdir(parents=True)
    (root / "simple_metrics.csv").write_text(
        "time,orders_mem,orders_cpu,orders_error,orders_mystery\n"
        "not-a-time,not-a-number,still-not-a-number,x,y\n",
        encoding="utf-8",
    )
    case = _case(
        root,
        system="RE2-SS",
        service="orders",
        fault="mem",
        instance="1",
    )

    audit = audit_metric_schemas((case,), _config())

    system = audit.systems["RE2-SS"]
    assert audit.case_count == 1
    assert system.unique_metric_names == 4
    assert system.disposition_unique_counts == {
        "CANONICAL": 2,
        "AUXILIARY": 1,
        "UNKNOWN": 1,
        "AMBIGUOUS": 0,
    }
    assert audit.raw_truth_indicator_coverage.model_dump() == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }


def test_metric_reader_rejects_tt_path_before_file_io() -> None:
    with pytest.raises(ValueError, match="TT"):
        read_metric_samples(
            Path("/definitely-missing/RE2-TT/simple_metrics.csv"), _config()
        )


def test_design_formula_evaluation_never_opens_validation_telemetry(
    tmp_path: Path,
) -> None:
    suffix_by_fault = {
        "cpu": "cpu",
        "mem": "mem",
        "disk": "diskio",
        "delay": "latency-50",
        "loss": "latency-50",
        "socket": "socket",
    }
    design_cases: list[DevCase] = []
    design_assignments: list[SplitAssignment] = []
    for index, (fault, suffix) in enumerate(suffix_by_fault.items(), start=1):
        design_root = (
            tmp_path / "RE2-OB" / f"checkoutservice_{fault}" / str(index)
        )
        design_root.mkdir(parents=True)
        (design_root / "simple_metrics.csv").write_text(
            f"time,checkoutservice_{suffix},frontend_cpu\n"
            "400,1,1\n"
            "999,1,1\n"
            "1000,9,1\n"
            "1600,9,1\n",
            encoding="utf-8",
        )
        design = _case(
            design_root,
            system="RE2-OB",
            service="checkoutservice",
            fault=fault,
            instance=str(index),
        )
        design_cases.append(design)
        design_assignments.append(_assignment(design, SplitName.DESIGN))
    validation_root = tmp_path / "RE2-OB" / "checkoutservice_cpu" / "2"
    validation = _case(
        validation_root,
        system="RE2-OB",
        service="checkoutservice",
        fault="cpu",
        instance="2",
    )

    outcomes, evaluations, selection = evaluate_design_formulas(
        (*design_cases, validation),
        (*design_assignments, _assignment(validation, SplitName.DEV_VALIDATION)),
        _config(),
    )

    assert len(outcomes) == 18
    assert {item.formula for item in outcomes} == set(FormulaId)
    assert all(item.truth_indicator_top6_present for item in outcomes)
    assert all(item.overall_coverage_at_6.denominator == 6 for item in evaluations)
    assert selection.gate_passed is True


def test_metric_reader_uses_preregistered_previous_finite_or_zero_policy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "simple_metrics.csv"
    path.write_text(
        "time,orders_mem\n400,\n600,nan\n800,5\n1000,inf\n",
        encoding="utf-8",
    )

    samples = read_metric_samples(path, _config())["orders_mem"]

    assert tuple(item.value for item in samples) == (0.0, 0.0, 5.0, 5.0)


def test_value_quality_audit_counts_raw_missing_and_nonfinite_without_case_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-SS" / "orders_mem" / "1"
    root.mkdir(parents=True)
    (root / "simple_metrics.csv").write_text(
        "time,orders_mem\n400,\n600,nan\n800,5\n1000,inf\n",
        encoding="utf-8",
    )
    case = _case(
        root,
        system="RE2-SS",
        service="orders",
        fault="mem",
        instance="1",
    )

    audit = audit_metric_value_quality((case,))

    assert audit.systems["RE2-SS"].model_dump() == {
        "system": "RE2-SS",
        "case_count": 1,
        "row_count": 4,
        "metric_cell_count": 4,
        "missing_timestamp_count": 0,
        "missing_value_count": 1,
        "nonfinite_value_count": 2,
    }


def test_runtime_candidate_builder_uses_opaque_identity_and_indicator_refs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "RE2-OB" / "checkoutservice_cpu" / "1"
    root.mkdir(parents=True)
    (root / "simple_metrics.csv").write_text(
        "time,checkoutservice_cpu,checkoutservice_mem\n"
        "400,1,1\n999,1,1\n1000,9,1\n1600,9,1\n",
        encoding="utf-8",
    )
    case = _case(
        root,
        system="RE2-OB",
        service="checkoutservice",
        fault="cpu",
        instance="1",
    )

    candidates = build_runtime_metric_candidates(
        dev_case_to_telemetry_case(case),
        case_identity_sha256="c" * 64,
        formula=FormulaId.F0,
        config=_config(),
    )

    assert candidates[0].service == "checkoutservice"
    assert candidates[0].canonical_indicator == "cpu"
    assert tuple(item.evidence_ref for item in candidates) == tuple(
        f"indicator:{index:04d}" for index in range(1, len(candidates) + 1)
    )
