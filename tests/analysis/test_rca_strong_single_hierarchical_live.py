from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import urllib.error

import pytest

import ecomsre_rca_unified.live_runtime as live_runtime_module
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rca100.contracts import RCA100InitialDiagnosis, RCA100ReasoningStep
from ecomsre_rca100.evaluator import RCA100GroundTruth
from ecomsre_rca100.projection import RCA100AgentTask
from ecomsre_rcaeval_adaptive.v2_runner import RequestPacer
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptBudget
from ecomsre_rcaeval_v2.dev3_token_accounting import ProviderAttemptStart
from ecomsre_rcaeval_v2.dev3_evidence import verify_provider_sidecar
from ecomsre_rcaeval_v2.dev3_provider import SemanticOperationStart
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.hierarchical_context import (
    EvidenceItem,
    HierarchySource,
    LiveBaseContext,
    LiveEntity,
    RelationSource,
    build_hierarchical_context,
)
from ecomsre_rca_unified.live_comparison import (
    Arm,
    CaseRef,
    build_request_payload,
    execute_arm,
    paired_schedule,
    validate_diagnosis,
)
from ecomsre_rca_unified.live_context_adapters import (
    _bounded_obss_visibility,
    _strict_temporal_relations,
    _trace_relations,
    assert_model_context_private,
    discover_label_blind_dev_cases,
)
from ecomsre_rca_unified.live_evaluation import (
    CaseScore,
    aggregate_paired_scores,
    paired_development_inference,
    regression_gate,
    scan_public_payloads,
    tune_gate,
)
from ecomsre_rca_unified.live_runtime import (
    CrossLifecycleRequestPacer,
    LiveTerminalStatus,
    execute_live_arm,
    terminalize_not_admitted,
)
from ecomsre_rca_unified.live_rca100_scan import (
    _in_task_window,
    _trace_parent_child_edges,
    read_live_rca_topology,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import scripts.rca_live.cli as live_cli_module  # noqa: E402
from scripts.rca_live.reporting import (  # noqa: E402
    REGRESSION_JSON,
    TUNE_JSON,
    _public_context_audit,
    _regression_markdown,
    _regression_public,
    _reject_unexpected_optional_outputs,
    verify_scoring_artifact_hashes,
)
from scripts.rca_live.scan_boundaries import scan_file, scan_project  # noqa: E402
from scripts.rca_live.evaluator import _rca_score  # noqa: E402
from scripts.rca_live.cli import (  # noqa: E402
    EXPECTED_INPUT_TREES,
    PR24_FIRST_PARENT,
    PR24_HEAD_COMMIT,
    STARTING_MAIN_COMMIT,
    _count_distribution,
    _config_hashes,
    _context_audit_implementation_hashes,
    _advance_state,
    _expected_schedule_lock,
    _require_active_control,
    _require_exact_object,
    _require_state,
    _validate_public_frozen_state,
    _validate_partial_phase_records,
    _verify_context_audit_binding,
    _verify_schedule_payload,
    _verify_schedule_source_locks,
    _write_create_once,
    mark_superseded,
)


def _entity(
    index: int,
    *,
    layer: CanonicalEntityLayer = CanonicalEntityLayer.SERVICE,
    parent: str | None = None,
) -> LiveEntity:
    ref = f"apm|apm.service|service-{index:03d}"
    return LiveEntity(
        entity_ref=ref,
        entity_name=f"service-{index:03d}",
        layer=layer,
        service_ancestor_or_none=ref if layer is CanonicalEntityLayer.SERVICE else parent,
        parent_ref_or_none=parent,
    )


def test_count_distribution_includes_final_report_mean() -> None:
    assert _count_distribution([1, 2, 9]) == {
        "max": 9,
        "mean": 4.0,
        "median": 2,
        "min": 1,
        "p95": 9,
    }


def test_historical_pr24_merge_parent_binding_matches_real_git_object() -> None:
    parents = subprocess.run(
        ("git", "rev-list", "--parents", "-n", "1", STARTING_MAIN_COMMIT),
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert parents == [STARTING_MAIN_COMMIT, PR24_FIRST_PARENT, PR24_HEAD_COMMIT]


def test_request_pacing_is_conservative_across_fresh_lifecycle_commands() -> None:
    delays: list[float] = []
    CrossLifecycleRequestPacer(5.0, sleep_fn=delays.append).wait()
    CrossLifecycleRequestPacer(5.0, sleep_fn=delays.append).wait()
    assert delays == [5.0, 5.0]


def test_tune_only_publication_rejects_stale_regression_output(tmp_path) -> None:
    stale = tmp_path / REGRESSION_JSON
    stale.parent.mkdir(parents=True)
    stale.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale public result"):
        _reject_unexpected_optional_outputs(tmp_path, {TUNE_JSON: b"{}\n"})


def test_public_scanner_allows_required_heldout_no_access_disclosure() -> None:
    scan_public_payloads(
        {
            Path("result.md"): (
                b"RE2-TT and new external data were not accessed."
            )
        }
    )
    with pytest.raises(ValueError, match="case identity"):
        scan_public_payloads({Path("result.md"): b"private result t001"})


def test_regression_public_artifacts_repeat_independent_one_call_boundary(
    tmp_path,
) -> None:
    evaluation = tmp_path / "evaluation"
    locks = tmp_path / "locks"
    evaluation.mkdir()
    locks.mkdir()
    (evaluation / "regression-aggregate.json").write_text(
        (
            '{"cost":{},"execution":{},"gate":{"verdict":"SYNTHETIC"},'
            '"obss":{},"root_inference":{}}\n'
        ),
        encoding="utf-8",
    )
    (locks / "regression-scoring-lock.json").write_text(
        '{"aggregate_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n',
        encoding="utf-8",
    )
    (locks / "schedule-lock.json").write_text(
        '{"regression":{"case_pairs":120,"records":240,"seed":20260813}}\n',
        encoding="utf-8",
    )
    report = _regression_public(tmp_path)
    assert report is not None
    assert report["arm_contract"] == {
        "b0": "BASELINE_STRONG_SINGLE",
        "h1": "STRONG_SINGLE_HIERARCHICAL",
        "same_model": True,
        "same_output_schema": True,
        "same_raw_bounded_evidence": True,
        "independent_model_calls_per_arm": 1,
        "specialist_calls": 0,
        "fusion_calls": 0,
        "post_model_override": False,
    }
    markdown = _regression_markdown(report)
    assert "paired independent one-call executions" in markdown
    assert "zero post-model" in markdown
    scan_public_payloads({Path("regression.md"): markdown.encode("utf-8")})


def test_non429_http_4xx_is_a_protocol_failure() -> None:
    for status in (400, 401, 403):
        error = urllib.error.HTTPError(
            "https://provider.invalid", status, "synthetic", {}, None
        )
        terminal_status, failure_code = live_runtime_module._failure(error)
        assert terminal_status is LiveTerminalStatus.PROTOCOL_VIOLATION
        assert failure_code == "HTTP_4XX_NON_429"


def test_context_audit_public_projection_excludes_private_input_roots() -> None:
    raw = {
        "b0_valid_contexts": 163,
        "duplicate_entity_count": 0,
        "h1_entity_count": {"mean": 10.0},
        "h1_propagation_relation_count": {"mean": 2.0},
        "h1_valid_contexts": 163,
        "input_token_estimate": {"h1_to_b0_mean_ratio": 1.2},
        "invalid_ref_count": 0,
        "revision": "synthetic",
        "source_counts": {"RCA100": 103, "OBSS": 60},
        "truncation_count": 0,
        "input_trees": {
            "rca100": {"absolute_root": "/Users/private/source"}
        },
    }
    public = _public_context_audit(raw)
    encoded = json.dumps(public).encode("utf-8")
    assert b"/Users/" not in encoded
    scan_public_payloads({Path("context.json"): encoded})


def test_context_audit_lock_rechecks_raw_audit_artifact(tmp_path) -> None:
    (tmp_path / "audit").mkdir()
    (tmp_path / "locks").mkdir()
    audit_path = tmp_path / "audit" / "context-audit.json"
    schedule_lock_path = tmp_path / "locks" / "schedule-lock.json"
    context_lock_path = tmp_path / "locks" / "context-audit-lock.json"
    audit_path.write_text('{"audit":"frozen"}\n', encoding="utf-8")
    schedule_lock_path.write_text('{"schedule":"frozen"}\n', encoding="utf-8")

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    methodology_sha = json.loads(
        (
            REPO_ROOT
            / "config"
            / "rca-strong-single-hierarchical-live-v1"
            / "context-policy.json"
        ).read_text(encoding="utf-8")
    )["methodology_sha256"]
    context_lock_path.write_text(
        json.dumps(
            {
                "audited_implementation_sha256": (
                    _context_audit_implementation_hashes()
                ),
                "config_hashes": _config_hashes(),
                "context_audit_sha256": sha(audit_path),
                "created_at_utc": "2026-08-10T00:00:00Z",
                "evaluation_version": "strong-single-hierarchical-live-dev-v1",
                "input_trees": {
                    name: {
                        "absolute_root": str((tmp_path / name).resolve()),
                        "byte_count": value["bytes"],
                        "file_count": value["files"],
                        "sha256": value["sha256"],
                    }
                    for name, value in EXPECTED_INPUT_TREES.items()
                },
                "methodology_sha256": methodology_sha,
                "revision": "v3_dictionary_bitmask_final",
                "schedule_lock_sha256": sha(schedule_lock_path),
                "schema_version": (
                    "strong-single-hierarchical-live.context-audit-lock.v1"
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _verify_context_audit_binding(tmp_path)
    audit_path.write_text('{"audit":"mutated"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="context audit binding"):
        _verify_context_audit_binding(tmp_path)


def test_schedule_source_locks_hash_actual_files(tmp_path, monkeypatch) -> None:
    obss = tmp_path / "obss-audit.json"
    rca = tmp_path / "rca-lock.json"
    obss.write_text('{"audit":"synthetic"}\n', encoding="utf-8")
    rca.write_text('{"lock":"synthetic"}\n', encoding="utf-8")
    monkeypatch.setattr(
        live_cli_module,
        "EXPECTED_OBSS_DATASET_AUDIT_SHA256",
        hashlib.sha256(obss.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        live_cli_module,
        "EXPECTED_RCA100_INPUT_SOURCE_LOCK_SHA256",
        hashlib.sha256(rca.read_bytes()).hexdigest(),
    )
    _verify_schedule_source_locks(
        obss_dataset_audit_path=obss,
        rca100_input_source_lock_path=rca,
    )
    rca.write_text('{"lock":"mutated"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="source lock"):
        _verify_schedule_source_locks(
            obss_dataset_audit_path=obss,
            rca100_input_source_lock_path=rca,
        )


def test_active_control_generation_rejects_old_and_superseded_roots(
    tmp_path, monkeypatch
) -> None:
    lock_path = tmp_path / "locks" / "schedule-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="control generation"):
        _require_active_control(tmp_path)

    schedules = tmp_path / "schedules"
    schedules.mkdir()
    for split, records, seed in (
        ("tune", 326, 20260812),
        ("regression", 240, 20260813),
    ):
        cases = (
            tuple(
                CaseRef(source="RCA100", source_key=f"rca-{index:03d}")
                for index in range(103)
            )
            + tuple(
                CaseRef(source="OBSS", source_key=f"tune-{index:03d}")
                for index in range(60)
            )
            if split == "tune"
            else tuple(
                CaseRef(source="OBSS", source_key=f"regression-{index:03d}")
                for index in range(120)
            )
        )
        scheduled = paired_schedule(
            cases,
            seed=seed,
            split="TUNE" if split == "tune" else "REGRESSION",
        )
        assert len(scheduled) == records
        (schedules / f"{split}.json").write_text(
            json.dumps(live_cli_module._private_schedule_payload(scheduled, seed=seed))
            + "\n",
            encoding="utf-8",
        )
    created_at = "2026-08-10T00:00:00Z"
    obss = tmp_path / "obss-audit.json"
    rca = tmp_path / "rca-lock.json"
    obss.write_text('{"audit":"synthetic"}\n', encoding="utf-8")
    rca.write_text('{"lock":"synthetic"}\n', encoding="utf-8")
    obss_sha = hashlib.sha256(obss.read_bytes()).hexdigest()
    rca_sha = hashlib.sha256(rca.read_bytes()).hexdigest()
    monkeypatch.setattr(
        live_cli_module, "EXPECTED_OBSS_DATASET_AUDIT_SHA256", obss_sha
    )
    monkeypatch.setattr(
        live_cli_module, "EXPECTED_RCA100_INPUT_SOURCE_LOCK_SHA256", rca_sha
    )
    source_locks = {
        "obss_dataset_audit": {
            "absolute_path": str(obss.resolve()),
            "sha256": obss_sha,
        },
        "rca100_input_source_lock": {
            "absolute_path": str(rca.resolve()),
            "sha256": rca_sha,
        },
    }
    lock_path.write_text(
        json.dumps(
            _expected_schedule_lock(
                tmp_path,
                created_at_utc=created_at,
                source_locks=source_locks,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _require_active_control(tmp_path)
    tune_path = schedules / "tune.json"
    tune_payload = json.loads(tune_path.read_text(encoding="utf-8"))
    tune_payload["records"][0]["arm_position"] = 2
    tune_path.write_text(json.dumps(tune_payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pair ordering"):
        _verify_schedule_payload(
            tmp_path, split="TUNE", expected_records=326, seed=20260812
        )
    (tmp_path / "locks" / "superseded-lock.json").write_text(
        "{}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="superseded"):
        _require_active_control(tmp_path)


def test_required_state_validates_its_lock_hash_chain(tmp_path) -> None:
    lock_path = tmp_path / "locks" / "schedule-lock.json"
    state_path = tmp_path / "state" / "SCHEDULE_FROZEN.json"
    lock_path.parent.mkdir(parents=True)
    state_path.parent.mkdir(parents=True)
    lock_path.write_text('{"lock":"value"}\n', encoding="utf-8")
    state = {
        "created_at_utc": "2026-08-10T00:00:00Z",
        "evaluation_version": "strong-single-hierarchical-live-dev-v1",
        "lock_name": "schedule-lock.json",
        "lock_sha256": "0" * 64,
        "predecessor": None,
        "predecessor_sha256": None,
        "schema_version": "strong-single-hierarchical-live.state.v1",
        "state": "SCHEDULE_FROZEN",
    }
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="state binding"):
        _require_state(tmp_path, "SCHEDULE_FROZEN")

    state["lock_sha256"] = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    assert _require_state(tmp_path, "SCHEDULE_FROZEN")["state"] == "SCHEDULE_FROZEN"


def test_create_once_lock_crash_resume_preserves_timestamp_then_advances_state(
    tmp_path,
) -> None:
    lock_path = tmp_path / "locks" / "schedule-lock.json"
    first = {
        "created_at_utc": "2026-08-10T00:00:00Z",
        "value": "frozen",
    }
    lock_sha = _write_create_once(lock_path, first)
    assert _write_create_once(
        lock_path,
        {**first, "created_at_utc": "2026-08-10T00:01:00Z"},
    ) == lock_sha
    assert json.loads(lock_path.read_text(encoding="utf-8"))["created_at_utc"] == (
        "2026-08-10T00:00:00Z"
    )
    _advance_state(
        tmp_path,
        "SCHEDULE_FROZEN",
        predecessor=None,
        lock_name="schedule-lock.json",
        lock_sha256=lock_sha,
    )
    assert (tmp_path / "state" / "SCHEDULE_FROZEN.json").is_file()
    with pytest.raises(ValueError, match="create-once artifact differs"):
        _write_create_once(
            lock_path,
            {"created_at_utc": "2026-08-10T00:02:00Z", "value": "mutated"},
        )


def test_public_freeze_idempotence_validates_full_lock_and_state(tmp_path) -> None:
    predecessor_path = tmp_path / "state" / "TUNE_SCORED.json"
    lock_path = tmp_path / "locks" / "public-verification-lock.json"
    predecessor_path.parent.mkdir(parents=True)
    lock_path.parent.mkdir(parents=True)
    predecessor_path.write_text('{"state":"TUNE_SCORED"}\n', encoding="utf-8")
    expected_lock = {
        "canonical_exact_comparison": "PASS",
        "public_files": {"result.json": "a" * 64},
    }
    lock_path.write_text(json.dumps(expected_lock) + "\n", encoding="utf-8")
    _require_exact_object(lock_path, expected_lock, "public verification lock")
    with pytest.raises(ValueError, match="public verification lock"):
        _require_exact_object(
            lock_path,
            {**expected_lock, "public_leakage_scan": "PASS"},
            "public verification lock",
        )

    lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    state = {
        "created_at_utc": "2026-08-10T00:00:00Z",
        "evaluation_version": "strong-single-hierarchical-live-dev-v1",
        "lock_name": "public-verification-lock.json",
        "lock_sha256": lock_sha,
        "predecessor": "TUNE_SCORED",
        "predecessor_sha256": hashlib.sha256(
            predecessor_path.read_bytes()
        ).hexdigest(),
        "schema_version": "strong-single-hierarchical-live.state.v1",
        "state": "PUBLIC_RESULT_FROZEN",
    }
    state_path = tmp_path / "state" / "PUBLIC_RESULT_FROZEN.json"
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    _validate_public_frozen_state(
        tmp_path,
        predecessor="TUNE_SCORED",
        verification_lock_sha256=lock_sha,
    )
    state["lock_name"] = "wrong.json"
    state_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="public frozen state"):
        _validate_public_frozen_state(
            tmp_path,
            predecessor="TUNE_SCORED",
            verification_lock_sha256=lock_sha,
        )


def test_superseded_marker_is_append_only_and_idempotent(tmp_path) -> None:
    (tmp_path / "locks").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "schedules").mkdir()
    (tmp_path / "locks" / "schedule-lock.json").write_text(
        '{"schema_version":"old"}\n', encoding="utf-8"
    )
    (tmp_path / "state" / "SCHEDULE_FROZEN.json").write_text(
        '{"state":"SCHEDULE_FROZEN"}\n', encoding="utf-8"
    )
    (tmp_path / "schedules" / "tune.json").write_text(
        '{"records":[]}\n', encoding="utf-8"
    )
    args = SimpleNamespace(
        private_root=tmp_path,
        reason="PRE_IMPLEMENTATION_PROVIDER_PAYLOAD_IDENTITY_REPAIR",
    )

    mark_superseded(args)
    first = (tmp_path / "locks" / "superseded-lock.json").read_bytes()
    mark_superseded(args)

    assert (tmp_path / "state" / "SUPERSEDED.json").is_file()
    assert (tmp_path / "locks" / "superseded-lock.json").read_bytes() == first


def _base() -> LiveBaseContext:
    entities = tuple(_entity(index) for index in range(70))
    evidence = tuple(
        EvidenceItem(
            evidence_ref=f"metric:{index + 1:04d}",
            source="METRICS",
            entity_ref=entities[index].entity_ref,
            name="metric",
            started_at=1.0,
            ended_at=2.0,
            score=float(10 - index),
            summary="bounded evidence",
        )
        for index in range(6)
    )
    return LiveBaseContext(
        alert_title="Synthetic alert",
        prompt_text="Investigate the incident.",
        alert_entity_ref=entities[0].entity_ref,
        entities=entities,
        evidence=evidence,
        source_status={"METRICS": "AVAILABLE", "LOGS": "AVAILABLE", "TRACES": "AVAILABLE"},
    )


def test_obss_discovery_is_label_blind(tmp_path) -> None:
    root = tmp_path / "RE2-SS"
    case_root = root / "opaque-directory-name" / "1"
    case_root.mkdir(parents=True)
    (case_root / "simple_metrics.csv").write_text("time,value\n1,1\n", encoding="utf-8")
    (case_root / "logs.csv").write_text("time,body\n1,x\n", encoding="utf-8")
    (case_root / "inject_time.txt").write_text("1\n", encoding="utf-8")

    cases = discover_label_blind_dev_cases(root, system="RE2-SS")

    assert len(cases) == 1
    assert cases[0].case_id == "re2-ss-case-0001"
    assert not hasattr(cases[0], "root_cause_service")
    assert not hasattr(cases[0], "fault")


def test_full_h1_context_rejects_source_identity_in_hierarchy_only() -> None:
    base_entity = _entity(0)
    source_key = "synthetic-task-secret"
    hierarchy_only = LiveEntity(
        entity_ref=f"apm|apm.service|{source_key}",
        entity_name="opaque service",
        layer=CanonicalEntityLayer.SERVICE,
        service_ancestor_or_none=f"apm|apm.service|{source_key}",
        parent_ref_or_none=None,
    )
    base = LiveBaseContext(
        alert_title="Synthetic alert",
        prompt_text="Investigate the incident.",
        alert_entity_ref=base_entity.entity_ref,
        entities=(base_entity,),
        evidence=(),
        source_status={
            "METRICS": "SOURCE_UNAVAILABLE",
            "LOGS": "SOURCE_UNAVAILABLE",
            "TRACES": "SOURCE_UNAVAILABLE",
        },
    )
    hierarchy = build_hierarchical_context(
        base,
        HierarchySource(
            entities=(base_entity, hierarchy_only),
            parent_edges=(),
            topology_edges=(),
            propagation_edges=(),
            source_visibility={hierarchy_only.entity_ref: frozenset({"EVENTS"})},
            first_anomaly_source={},
        ),
    )
    assert_model_context_private(base, source_key)
    with pytest.raises(ValueError, match="private identity"):
        assert_model_context_private(base, source_key, hierarchy)


def test_event_and_alert_visibility_requires_timestamp_in_task_window() -> None:
    task = RCA100AgentTask(
        opaque_case_id="rca100-case-0001",
        alert_title="Synthetic alert",
        prompt_text="Investigate.",
        window_start_timestamp=10.0,
        anchor_timestamp=15.0,
        window_end_timestamp=20.0,
        anchor_source="TASK_ALERT_TRIGGER",
    )
    assert not _in_task_window(None, task)
    assert not _in_task_window(9.999, task)
    assert _in_task_window(10.0, task)
    assert _in_task_window(20.0, task)
    assert not _in_task_window(20.001, task)


def test_obss_visibility_includes_bounded_non_top6_source_entities(tmp_path) -> None:
    metrics = tmp_path / "metrics.csv"
    logs = tmp_path / "logs.csv"
    traces = tmp_path / "traces.csv"
    metrics.write_text(
        "time,service-a_cpu,service-b_memory\n1000,1,2\n",
        encoding="utf-8",
    )
    logs.write_text(
        "timestamp,service,message\n1001,service-c,normal\n2000,outside,late\n",
        encoding="utf-8",
    )
    traces.write_text(
        "startTime,serviceName,traceId,spanId\n1002,service-d,t,s\n",
        encoding="utf-8",
    )
    visibility = _bounded_obss_visibility(
        TelemetryCase(
            case_id="synthetic",
            system="RE2-OB",
            root=tmp_path,
            metrics_path=metrics,
            logs_path=logs,
            traces_path=traces,
            inject_time=1000,
        )
    )
    assert visibility["apm|apm.service|service-a"] == frozenset({"METRICS"})
    assert visibility["apm|apm.service|service-b"] == frozenset({"METRICS"})
    assert visibility["apm|apm.service|service-c"] == frozenset({"LOGS"})
    assert visibility["apm|apm.service|service-d"] == frozenset({"TRACES"})
    assert "apm|apm.service|outside" not in visibility


def test_first_observed_relations_never_order_timestamp_ties() -> None:
    first = {
        "apm|apm.service|a": (1.0, "METRICS"),
        "apm|apm.service|b": (1.0, "LOGS"),
        "apm|apm.service|c": (2.0, "TRACES"),
    }
    relations = _strict_temporal_relations(first)  # type: ignore[arg-type]
    assert all(
        first[item.source_entity_ref][0] < first[item.target_entity_ref][0]
        for item in relations
    )


def test_obss_trace_relations_are_bounded_and_parent_to_child(tmp_path) -> None:
    metrics = tmp_path / "metrics.csv"
    logs = tmp_path / "logs.csv"
    traces = tmp_path / "traces.csv"
    metrics.write_text("time,service-a_cpu\n1000,1\n", encoding="utf-8")
    logs.write_text("time,service,message\n1000,service-a,x\n", encoding="utf-8")
    traces.write_text(
        "startTime,serviceName,traceId,spanId,parentSpanId\n"
        "1000,service-a,t,p,\n"
        "2000,service-b,t,c,p\n",
        encoding="utf-8",
    )
    case = TelemetryCase(
        case_id="synthetic",
        system="RE2-OB",
        root=tmp_path,
        metrics_path=metrics,
        logs_path=logs,
        traces_path=traces,
        inject_time=1000,
    )
    known = {
        "apm|apm.service|service-a",
        "apm|apm.service|service-b",
    }
    assert _trace_relations(case, known) == ()
    traces.write_text(
        "startTime,serviceName,traceId,spanId,parentSpanId\n"
        "1000,service-a,t,p,\n"
        "1001,service-b,t,c,p\n",
        encoding="utf-8",
    )
    relations = _trace_relations(case, known)
    assert len(relations) == 1
    assert relations[0].source_entity_ref == "apm|apm.service|service-a"
    assert relations[0].target_entity_ref == "apm|apm.service|service-b"
    assert _trace_parent_child_edges(
        {("t", "p"): "service-a", ("t", "c"): "service-b"},
        ((('t', 'c'), ('t', 'p')),),
    ) == {("service-a", "service-b")}


def test_topology_relation_allowlist_is_explicit_and_fail_closed(tmp_path) -> None:
    path = tmp_path / "topology.json"
    payload = {
        "entities": [
            {"id": "a", "type": "apm.service", "name": "a"},
            {"id": "b", "type": "apm.service", "name": "b"},
        ],
        "edges": [
            {"src": "a", "dst": "b", "relation": "depends_on"},
            {"src": "a", "dst": "b", "relation": "unknown"},
            {"src": "a", "dst": "b", "relation": "same_as"},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    topology = read_live_rca_topology(path)
    assert topology.explicit_dependency_edges == (
        ("apm|apm.service|b", "apm|apm.service|a"),
    )
    assert topology.unknown_edges
    payload["edges"] = [{"src": "a", "dst": "b", "relation": "surprise"}]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlisted"):
        read_live_rca_topology(path)


def test_hierarchy_is_label_blind_stable_and_bounded() -> None:
    base = _base()
    source = HierarchySource(
        entities=base.entities,
        parent_edges=(),
        topology_edges=tuple(
            RelationSource(
                source_entity_ref=base.entities[index].entity_ref,
                target_entity_ref=base.entities[index + 1].entity_ref,
                relation_type="DIRECTED_TOPOLOGY",
            )
            for index in range(20)
        ),
        propagation_edges=(),
        source_visibility={
            **{
                item.entity_ref: frozenset({"METRICS"})
                for item in base.entities
            },
            base.entities[0].entity_ref: frozenset({"ALERTS", "METRICS"}),
            base.entities[1].entity_ref: frozenset({"LOGS"}),
        },
        first_anomaly_source={base.entities[0].entity_ref: "METRICS"},
    )

    first = build_hierarchical_context(base, source)
    second = build_hierarchical_context(base, source)

    assert first == second
    assert len(first.entity_cards) == 64
    assert len(first.propagation_relations) == 12
    assert len({item.entity_ref for item in first.entity_cards}) == 64
    assert tuple(item.entity_ref for item in first.entity_cards) == tuple(
        item.entity_ref for item in base.entities[:64]
    )
    assert first.included_candidate_count == 70
    assert first.dropped_included_candidate_count == 6
    assert first.entity_cards[0].entity_ref == base.entities[0].entity_ref
    assert first.entity_cards[0].visible_sources == ("METRICS", "ALERTS")
    assert "ground_truth" not in first.model_dump_json().casefold()


def test_hierarchy_cap_fails_closed_instead_of_reordering_dangling_lineage() -> None:
    services = tuple(_entity(index) for index in range(32))
    operations = tuple(
        LiveEntity(
            entity_ref=f"k8s|k8s.deployment|operation-{index:03d}",
            entity_name=f"operation-{index:03d}",
            layer=CanonicalEntityLayer.WORKLOAD,
            service_ancestor_or_none=services[index % len(services)].entity_ref,
            parent_ref_or_none=services[index % len(services)].entity_ref,
        )
        for index in range(38)
    )
    entities = (*services, *operations)
    base = LiveBaseContext(
        alert_title="Synthetic alert",
        prompt_text="Investigate.",
        alert_entity_ref=operations[0].entity_ref,
        entities=entities,
        evidence=tuple(
            EvidenceItem(
                evidence_ref=f"metric:{index + 1:04d}",
                source="METRICS",
                entity_ref=operation.entity_ref,
                name="metric",
                started_at=1.0,
                ended_at=2.0,
                score=1.0,
                summary="bounded",
            )
            for index, operation in enumerate(operations[:6])
        ),
        source_status={
            "METRICS": "AVAILABLE",
            "LOGS": "SOURCE_UNAVAILABLE",
            "TRACES": "SOURCE_UNAVAILABLE",
        },
    )
    source = HierarchySource(
        entities=entities,
        parent_edges=tuple(
            (item.entity_ref, item.parent_ref_or_none)
            for item in operations
            if item.parent_ref_or_none is not None
        ),
        topology_edges=(),
        propagation_edges=(),
        source_visibility={
            item.entity_ref: (
                frozenset({"METRICS", "LOGS"})
                if item.layer is CanonicalEntityLayer.WORKLOAD
                else frozenset({"METRICS"})
            )
            for item in entities
        },
        first_anomaly_source={},
    )
    with pytest.raises(ValueError, match="dangling lineage"):
        build_hierarchical_context(base, source)


def test_hierarchy_includes_ancestors_and_distance_two_but_not_ineligible_root() -> None:
    service = _entity(90)
    workload = LiveEntity(
        entity_ref="k8s|k8s.deployment|workload",
        entity_name="workload",
        layer=CanonicalEntityLayer.WORKLOAD,
        service_ancestor_or_none=service.entity_ref,
        parent_ref_or_none=service.entity_ref,
    )
    operation = LiveEntity(
        entity_ref="apm|apm.operation|operation",
        entity_name="operation",
        layer=CanonicalEntityLayer.OPERATION,
        service_ancestor_or_none=service.entity_ref,
        parent_ref_or_none=workload.entity_ref,
    )
    distance_two = _entity(91)
    middle = _entity(92)
    base = LiveBaseContext(
        alert_title="Alert",
        prompt_text="Investigate.",
        alert_entity_ref=operation.entity_ref,
        entities=(operation,),
        evidence=(
            EvidenceItem(
                evidence_ref="log:0001",
                source="LOGS",
                entity_ref=operation.entity_ref,
                name="pattern",
                started_at=1.0,
                ended_at=1.0,
                score=1.0,
                summary="error",
            ),
        ),
        source_status={"METRICS": "AVAILABLE", "LOGS": "AVAILABLE", "TRACES": "SOURCE_UNAVAILABLE"},
    )
    source = HierarchySource(
        entities=(operation, workload, service, middle, distance_two),
        parent_edges=((operation.entity_ref, workload.entity_ref), (workload.entity_ref, service.entity_ref)),
        topology_edges=(
            RelationSource(
                source_entity_ref=operation.entity_ref,
                target_entity_ref=middle.entity_ref,
                relation_type="UNDIRECTED",
            ),
            RelationSource(
                source_entity_ref=middle.entity_ref,
                target_entity_ref=distance_two.entity_ref,
                relation_type="UNDIRECTED",
            ),
        ),
        propagation_edges=(),
        source_visibility={operation.entity_ref: frozenset({"LOGS"})},
        first_anomaly_source={},
    )

    hierarchy = build_hierarchical_context(base, source)
    refs = {item.entity_ref for item in hierarchy.entity_cards}

    assert {service.entity_ref, workload.entity_ref, distance_two.entity_ref} <= refs
    assert operation.entity_ref not in hierarchy.root_eligible_entity_refs
    assert service.entity_ref in hierarchy.root_eligible_entity_refs


def test_hierarchy_preserves_all_dag_parents_with_one_deterministic_card_parent() -> None:
    service = _entity(93)
    workload_a = LiveEntity(
        entity_ref="k8s|k8s.deployment|workload-a",
        entity_name="workload-a",
        layer=CanonicalEntityLayer.WORKLOAD,
        service_ancestor_or_none=service.entity_ref,
        parent_ref_or_none=service.entity_ref,
    )
    workload_b = LiveEntity(
        entity_ref="k8s|k8s.deployment|workload-b",
        entity_name="workload-b",
        layer=CanonicalEntityLayer.WORKLOAD,
        service_ancestor_or_none=service.entity_ref,
        parent_ref_or_none=service.entity_ref,
    )
    operation = LiveEntity(
        entity_ref="apm|apm.operation|shared-operation",
        entity_name="shared-operation",
        layer=CanonicalEntityLayer.OPERATION,
        service_ancestor_or_none=service.entity_ref,
        parent_ref_or_none=workload_a.entity_ref,
    )
    base = LiveBaseContext(
        alert_title="Alert",
        prompt_text="Investigate.",
        alert_entity_ref=operation.entity_ref,
        entities=(operation,),
        evidence=(),
        source_status={
            "METRICS": "SOURCE_UNAVAILABLE",
            "LOGS": "SOURCE_UNAVAILABLE",
            "TRACES": "SOURCE_UNAVAILABLE",
        },
    )
    hierarchy = build_hierarchical_context(
        base,
        HierarchySource(
            entities=(operation, workload_a, workload_b, service),
            parent_edges=(
                (operation.entity_ref, workload_a.entity_ref),
                (operation.entity_ref, workload_b.entity_ref),
                (workload_a.entity_ref, service.entity_ref),
                (workload_b.entity_ref, service.entity_ref),
            ),
            topology_edges=(),
            propagation_edges=(),
            source_visibility={},
            first_anomaly_source={},
        ),
    )

    cards = {card.entity_ref: card for card in hierarchy.entity_cards}
    assert {workload_a.entity_ref, workload_b.entity_ref, service.entity_ref} <= cards.keys()
    assert cards[operation.entity_ref].parent_ref_or_none == workload_a.entity_ref


def test_hierarchy_uses_only_topology_for_distance_and_includes_service_ancestor() -> None:
    operation = LiveEntity(
        entity_ref="apm|apm.operation|checkout",
        entity_name="checkout",
        layer=CanonicalEntityLayer.OPERATION,
        service_ancestor_or_none="apm|apm.service|checkout",
        parent_ref_or_none=None,
    )
    visible_service = LiveEntity(
        entity_ref="apm|apm.service|visible",
        entity_name="visible",
        layer=CanonicalEntityLayer.SERVICE,
        service_ancestor_or_none="apm|apm.service|visible",
        parent_ref_or_none=None,
    )
    service_ancestor = LiveEntity(
        entity_ref="apm|apm.service|checkout",
        entity_name="checkout",
        layer=CanonicalEntityLayer.SERVICE,
        service_ancestor_or_none="apm|apm.service|checkout",
        parent_ref_or_none=None,
    )
    temporal_only = LiveEntity(
        entity_ref="apm|apm.service|temporal-only",
        entity_name="temporal-only",
        layer=CanonicalEntityLayer.SERVICE,
        service_ancestor_or_none="apm|apm.service|temporal-only",
        parent_ref_or_none=None,
    )
    base = LiveBaseContext(
        alert_title="Synthetic alert",
        prompt_text="Diagnose visible evidence.",
        alert_entity_ref=visible_service.entity_ref,
        entities=(operation, visible_service),
        evidence=(
            EvidenceItem(
                evidence_ref="metric:0001",
                source="METRICS",
                entity_ref=operation.entity_ref,
                name="latency",
                started_at=1.0,
                ended_at=2.0,
                score=4.0,
                summary="Latency increased.",
            ),
        ),
        source_status={
            "METRICS": "AVAILABLE",
            "LOGS": "SOURCE_UNAVAILABLE",
            "TRACES": "SOURCE_UNAVAILABLE",
        },
    )
    source = HierarchySource(
        entities=(operation, visible_service, service_ancestor, temporal_only),
        parent_edges=(),
        topology_edges=(),
        propagation_edges=(
            RelationSource(
                source_entity_ref=visible_service.entity_ref,
                target_entity_ref=temporal_only.entity_ref,
                relation_type="FIRST_OBSERVED_BEFORE",
            ),
        ),
        source_visibility={
            operation.entity_ref: frozenset({"METRICS"}),
            visible_service.entity_ref: frozenset({"ALERTS"}),
        },
        first_anomaly_source={operation.entity_ref: "METRICS"},
    )

    hierarchy = build_hierarchical_context(base, source)
    cards = {card.entity_ref: card for card in hierarchy.entity_cards}

    assert service_ancestor.entity_ref in cards
    assert temporal_only.entity_ref not in cards
    assert cards[visible_service.entity_ref].topology_distance_or_none == 0


def test_paired_schedule_randomizes_cases_and_alternates_arms() -> None:
    cases = tuple(CaseRef(source="SYNTHETIC", source_key=f"case-{index}") for index in range(8))

    schedule = paired_schedule(cases, seed=20260812, split="TUNE")

    assert len(schedule) == 16
    assert [item.arm for item in schedule[:6]] == [
        Arm.B0,
        Arm.H1,
        Arm.H1,
        Arm.B0,
        Arm.B0,
        Arm.H1,
    ]
    assert len({item.run_id for item in schedule}) == 16
    assert all(schedule[index].opaque_case_id == schedule[index + 1].opaque_case_id for index in range(0, 16, 2))
    assert [schedule[index].source_key for index in range(0, 16, 2)] != [item.source_key for item in cases]


def test_output_validation_uses_same_schema_and_visible_refs() -> None:
    base = _base()
    hierarchy = build_hierarchical_context(
        base,
        HierarchySource(
            entities=base.entities,
            parent_edges=(),
            topology_edges=(),
            propagation_edges=(),
            source_visibility={},
            first_anomaly_source={},
        ),
    )
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref=hierarchy.root_eligible_entity_refs[0],
        fault_type="cpu saturation",
        confidence=0.8,
        evidence_refs=("metric:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="Metric changed.",
                entity_ref_or_none=hierarchy.root_eligible_entity_refs[0],
                evidence_refs=("metric:0001",),
            ),
        ),
        summary="Likely root.",
    )

    validate_diagnosis(diagnosis, base=base, arm=Arm.B0, hierarchy=None)
    validate_diagnosis(diagnosis, base=base, arm=Arm.H1, hierarchy=hierarchy)
    b0_payload = build_request_payload(
        model="frozen-model",
        base=base,
        arm=Arm.B0,
        hierarchy=None,
        max_completion_tokens=128,
    )
    h1_payload = build_request_payload(
        model="frozen-model",
        base=base,
        arm=Arm.H1,
        hierarchy=hierarchy,
        max_completion_tokens=128,
    )
    assert b0_payload["tools"] == h1_payload["tools"]
    for payload in (b0_payload, h1_payload):
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
        for marker in (
            "rca100",
            "re2-ob",
            "re2-ss",
            "task_id",
            "case_id",
            "ground_truth",
            "root_cause_service",
        ):
            assert marker not in encoded
    assert "hierarchical_context" not in str(b0_payload)
    assert "hierarchical_context" in str(h1_payload)
    h1_user = str(h1_payload["messages"])
    assert "entity_card_columns" in h1_user
    assert "entity_ref_dictionary" in h1_user
    assert "visible_source_bit_order" in h1_user
    assert "source_card_index" in h1_user
    h1_system = str(h1_payload["messages"][0]["content"])
    assert "fault ontology" in h1_system.casefold()
    with pytest.raises(ValueError, match="visible evidence"):
        validate_diagnosis(
            diagnosis.model_copy(update={"evidence_refs": ("log:9999",)}),
            base=base,
            arm=Arm.B0,
            hierarchy=None,
        )
    with pytest.raises(ValueError, match="root-eligible"):
        validate_diagnosis(
            diagnosis.model_copy(update={"root_cause_entity_ref": base.entities[-1].entity_ref}),
            base=base,
            arm=Arm.H1,
            hierarchy=hierarchy,
        )


def test_rca_secondary_dimensions_accept_any_ground_truth_target(tmp_path) -> None:
    case_root = tmp_path / "synthetic-case"
    case_root.mkdir()
    (case_root / "topology.json").write_text(
        json.dumps(
            {
                "entities": [
                    {"id": "svc-a", "type": "apm.service", "name": "Service A"},
                    {"id": "svc-b", "type": "apm.service", "name": "Service B"},
                    {"id": "op-a", "type": "apm.operation", "name": "Operation A"},
                    {"id": "op-b", "type": "apm.operation", "name": "Operation B"},
                ],
                "edges": [
                    {"src": "svc-a", "dst": "op-a", "relation": "contains"},
                    {"src": "svc-b", "dst": "op-b", "relation": "contains"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref="apm|apm.service|svc-b",
        fault_type="timeout",
        confidence=0.8,
        evidence_refs=("metric:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="Service is upstream of the affected operation.",
                entity_ref_or_none="apm|apm.service|svc-b",
                evidence_refs=("metric:0001",),
            ),
        ),
        summary="Service B is the service-level root.",
    )
    terminal = live_runtime_module.LiveTerminalRecord.model_construct(
        status=LiveTerminalStatus.COMPLETED,
        diagnosis=diagnosis,
    )
    score = _rca_score(
        opaque_case_id="case-00000000000000000000",
        source_key="synthetic-case",
        b0=terminal,
        h1=terminal,
        truth=RCA100GroundTruth(
            source_task_id="t001",
            canonical_case_id="synthetic-multi-target",
            target_entity_ids=("op-a", "op-b"),
            fault_types=("timeout",),
        ),
        cases_root=tmp_path,
    )

    assert score.b0_root is False
    assert score.b0_service is True
    assert score.b0_ancestor is True
    assert score.b0_layer is False


def test_execute_arm_makes_exactly_one_model_call() -> None:
    base = _base()
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref=base.entities[0].entity_ref,
        fault_type="cpu",
        confidence=1.0,
        evidence_refs=("metric:0001",),
        reasoning_steps=(RCA100ReasoningStep(claim="Observed.", evidence_refs=("metric:0001",)),),
        summary="Observed.",
    )

    class FakeProvider:
        calls = 0

        def diagnose(self, *, base: LiveBaseContext, arm: Arm, hierarchy: object) -> RCA100InitialDiagnosis:
            del base, arm, hierarchy
            self.calls += 1
            return diagnosis

    provider = FakeProvider()
    result = execute_arm(base=base, arm=Arm.B0, hierarchy=None, provider=provider)

    assert provider.calls == 1
    assert result.diagnosis == diagnosis
    assert result.model_calls == 1
    assert result.specialist_calls == 0
    assert result.fusion_calls == 0


class _FakeTransport:
    def __init__(self, diagnosis: RCA100InitialDiagnosis) -> None:
        self.diagnosis = diagnosis
        self.calls = 0

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, payload, timeout_seconds
        self.calls += 1
        return {
            "model": "frozen-model",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "submit_strong_single_diagnosis",
                                    "arguments": json.dumps(
                                        self.diagnosis.model_dump(mode="json")
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
        }


class _Http429Transport:
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del headers, payload, timeout_seconds
        raise urllib.error.HTTPError(url, 429, "synthetic capacity", {}, None)


def test_partial_tune_abort_evidence_binds_prefix_and_provider_sidecars(
    tmp_path, monkeypatch
) -> None:
    records = paired_schedule(
        (CaseRef(source="SYNTHETIC", source_key="case"),),
        seed=20260812,
        split="TUNE",
    )
    schedule_path = tmp_path / "schedules" / "tune.json"
    schedule_path.parent.mkdir(parents=True)
    schedule_path.write_text('{"synthetic":true}\n', encoding="utf-8")
    implementation_path = tmp_path / "locks" / "implementation-lock.json"
    implementation_path.parent.mkdir(parents=True)
    implementation_path.write_text('{"synthetic":true}\n', encoding="utf-8")
    schedule_sha = hashlib.sha256(schedule_path.read_bytes()).hexdigest()
    implementation_sha = hashlib.sha256(implementation_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        live_cli_module,
        "_load_private_schedule",
        lambda _root, _split: tuple(
            {
                "arm": item.arm.value,
                "arm_position": item.arm_position,
                "opaque_case_id": item.opaque_case_id,
                "pair_position": item.pair_position,
                "run_id": item.run_id,
                "source": item.source,
                "source_key": item.source_key,
                "split": item.split,
            }
            for item in records
        ),
    )
    base = _base()
    hierarchy = build_hierarchical_context(
        base,
        HierarchySource(
            entities=base.entities,
            parent_edges=(),
            topology_edges=(),
            propagation_edges=(),
            source_visibility={
                base.entities[0].entity_ref: frozenset(("METRICS", "ALERTS"))
            },
            first_anomaly_source={},
        ),
    )
    budget = AttemptBudget(
        max_provider_attempts=4,
        max_retry_attempts=2,
        prompt_token_reservation=29_952,
        max_completion_tokens=2_048,
        max_conservative_tokens=128_000,
    )
    for record in records:
        terminal = execute_live_arm(
            record,
            base=base,
            hierarchy=None if record.arm is Arm.B0 else hierarchy,
            journal_root=tmp_path / "runtime" / "tune" / "journal",
            output_root=tmp_path / "runtime" / "tune" / "output",
            schedule_sha256=schedule_sha,
            implementation_lock_sha256=implementation_sha,
            provider_config=OpenAICompatibleConfig(
                base_url="https://provider.invalid/v1",
                api_key="synthetic",
                model="frozen-model",
            ),
            expected_model="frozen-model",
            timeout_seconds=30.0,
            max_completion_tokens=2_048,
            prompt_token_reservation=29_952,
            pacer=RequestPacer(0.0),
            budget=budget,
            retry_policy_sha256=(
                "7fd010103f83a1cb99b0c478ddafdf6e9fd0dc349a4297e7bb55c9b4157c202b"
            ),
            base_transport=_Http429Transport(),
        )
        assert terminal.failure_code == "HTTP_429"

    terminals, evidence = _validate_partial_phase_records(
        tmp_path,
        split="TUNE",
        allow_orphan_attempt=False,
        require_http_429_boundary=True,
    )
    assert len(terminals) == 2
    assert evidence["terminal_files"] == 2
    assert evidence["run_attempt_files"] == 2
    assert evidence["provider_attempt_files"] > 0

    semantic_path = next(
        (tmp_path / "runtime" / "tune" / "journal" / "runs").glob(
            "*/semantic-operations/0001.json"
        )
    )
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    semantic["request_sha256s"] = ["d" * 64]
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
    with pytest.raises(ValueError):
        _validate_partial_phase_records(
            tmp_path,
            split="TUNE",
            allow_orphan_attempt=False,
            require_http_429_boundary=True,
        )


def test_live_runtime_is_create_once_and_never_reissues_terminal(tmp_path) -> None:
    base = _base()
    record = paired_schedule(
        (CaseRef(source="SYNTHETIC", source_key="case"),),
        seed=20260812,
        split="TUNE",
    )[0]
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref=base.entities[0].entity_ref,
        fault_type="cpu",
        confidence=1.0,
        evidence_refs=("metric:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="Metric changed.",
                entity_ref_or_none=base.entities[0].entity_ref,
                evidence_refs=("metric:0001",),
            ),
        ),
        summary="Root found.",
    )
    transport = _FakeTransport(diagnosis)
    budget = AttemptBudget(
        max_provider_attempts=2,
        max_retry_attempts=1,
        prompt_token_reservation=1_000,
        max_completion_tokens=128,
        max_conservative_tokens=2_256,
    )
    keyword_args = {
        "base": base,
        "hierarchy": None,
        "journal_root": tmp_path / "journal",
        "output_root": tmp_path / "output",
        "schedule_sha256": "a" * 64,
        "implementation_lock_sha256": "b" * 64,
        "provider_config": OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="synthetic",
            model="frozen-model",
        ),
        "expected_model": "frozen-model",
        "timeout_seconds": 30.0,
        "max_completion_tokens": 128,
        "prompt_token_reservation": 1_000,
        "pacer": RequestPacer(0.0),
        "budget": budget,
        "retry_policy_sha256": "c" * 64,
        "base_transport": transport,
    }

    first = execute_live_arm(record, **keyword_args)
    second = execute_live_arm(record, **keyword_args)

    assert first == second
    assert first.status is LiveTerminalStatus.COMPLETED
    assert first.input_tokens_if_known == 100
    assert first.output_tokens_if_known == 20
    assert first.semantic_model_operations == 1
    assert transport.calls == 1
    assert first.diagnosis_metadata is not None
    assert first.diagnosis_metadata.entity_layer is CanonicalEntityLayer.SERVICE
    assert first.diagnosis_metadata.service_ancestor_or_none == diagnosis.root_cause_entity_ref
    assert first.diagnosis_metadata.root_provenance == "MODEL_STRONG_SINGLE_B0"
    assert first.diagnosis_metadata.fault_ontology_class.value == "LOCAL_RESOURCE"
    assert first.diagnosis_metadata.visibility_summary.visible_sources == (
        "METRICS",
        "ALERTS",
    )
    invalid_failure = first.model_dump()
    invalid_failure.update(
        {
            "status": LiveTerminalStatus.INVALID_SCHEMA,
            "failure_code": "SYNTHETIC_FAILURE",
            "diagnosis_metadata": None,
        }
    )
    with pytest.raises(ValueError, match="failed live terminal contains diagnosis"):
        live_runtime_module.LiveTerminalRecord.model_validate(invalid_failure)

    run_root = tmp_path / "journal" / "runs" / record.run_id
    semantics, _attempts = verify_provider_sidecar(
        run_root,
        expected_semantic_operations=1,
        expected_policy_lock_sha256="c" * 64,
        expected_timeout_seconds=30.0,
        prompt_token_reservation=1_000,
        max_completion_tokens=128,
    )
    assert set(semantics[0].request_sha256s) == {first.request_sha256}
    semantic_path = run_root / "semantic-operations" / "0001.json"
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    semantic["request_sha256s"] = ["d" * 64]
    semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
    with pytest.raises(ValueError, match="request binding"):
        verify_provider_sidecar(
            run_root,
            expected_semantic_operations=1,
            expected_policy_lock_sha256="c" * 64,
            expected_timeout_seconds=30.0,
            prompt_token_reservation=1_000,
            max_completion_tokens=128,
        )

    terminal_path = tmp_path / "output" / "terminals" / f"{record.run_id}.json"
    terminal_value = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal_value["arm"] = "H1"
    terminal_path.write_text(json.dumps(terminal_value), encoding="utf-8")
    with pytest.raises(ValueError, match="binding"):
        execute_live_arm(record, **keyword_args)


def test_live_runtime_clears_diagnosis_if_metadata_construction_fails(
    tmp_path, monkeypatch
) -> None:
    base = _base()
    record = paired_schedule(
        (CaseRef(source="SYNTHETIC", source_key="case"),),
        seed=20260812,
        split="TUNE",
    )[0]
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref=base.entities[0].entity_ref,
        fault_type="cpu",
        confidence=1.0,
        evidence_refs=("metric:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="Metric changed.",
                entity_ref_or_none=base.entities[0].entity_ref,
                evidence_refs=("metric:0001",),
            ),
        ),
        summary="Root found.",
    )

    def fail_metadata(*args, **kwargs):
        del args, kwargs
        raise ValueError("synthetic metadata failure")

    monkeypatch.setattr(live_runtime_module, "_diagnosis_metadata", fail_metadata)
    terminal = execute_live_arm(
        record,
        base=base,
        hierarchy=None,
        journal_root=tmp_path / "journal",
        output_root=tmp_path / "output",
        schedule_sha256="a" * 64,
        implementation_lock_sha256="b" * 64,
        provider_config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="synthetic",
            model="frozen-model",
        ),
        expected_model="frozen-model",
        timeout_seconds=30.0,
        max_completion_tokens=128,
        prompt_token_reservation=1_000,
        pacer=RequestPacer(0.0),
        budget=AttemptBudget(
            max_provider_attempts=2,
            max_retry_attempts=1,
            prompt_token_reservation=1_000,
            max_completion_tokens=128,
            max_conservative_tokens=2_256,
        ),
        retry_policy_sha256="c" * 64,
        base_transport=_FakeTransport(diagnosis),
    )

    assert terminal.status is LiveTerminalStatus.INVALID_SCHEMA
    assert terminal.diagnosis is None
    assert terminal.diagnosis_metadata is None


@pytest.mark.parametrize(
    ("crash_stage", "semantic_operations", "provider_attempts"),
    (("before_semantic", 0, 0), ("after_semantic", 1, 0), ("after_attempt", 1, 1)),
)
def test_interrupted_live_arm_recovery_never_reissues_and_remains_verifiable(
    tmp_path,
    crash_stage,
    semantic_operations,
    provider_attempts,
) -> None:
    base = _base()
    record = paired_schedule(
        (CaseRef(source="SYNTHETIC", source_key="case"),),
        seed=20260812,
        split="TUNE",
    )[0]
    journal_root = tmp_path / "runtime" / "tune" / "journal"
    output_root = tmp_path / "runtime" / "tune" / "output"
    started_at = datetime.now(timezone.utc)
    attempt = live_runtime_module.LiveRunAttempt(
        run_id=record.run_id,
        opaque_case_id=record.opaque_case_id,
        split=record.split,
        pair_position=record.pair_position,
        arm_position=record.arm_position,
        arm=record.arm,
        schedule_sha256="a" * 64,
        implementation_lock_sha256="b" * 64,
        started_at_utc=started_at,
    )
    attempt_path = journal_root / "run-attempts" / f"{record.run_id}.json"
    attempt_path.parent.mkdir(parents=True)
    attempt_path.write_text(
        json.dumps(attempt.model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    run_root = journal_root / "runs" / record.run_id
    if crash_stage != "before_semantic":
        semantic_start = SemanticOperationStart(
            schema_version="rcaeval-re2-v2-dev3.semantic-operation-start.v1",
            semantic_operation_index=1,
            operation_type="FINAL_JUDGE",
            started_at_utc=started_at,
            policy_lock_sha256=(
                "7fd010103f83a1cb99b0c478ddafdf6e9fd0dc349a4297e7bb55c9b4157c202b"
            ),
        )
        semantic_path = run_root / "semantic-operation-starts" / "0001.json"
        semantic_path.parent.mkdir(parents=True)
        semantic_path.write_text(
            json.dumps(semantic_start.model_dump(mode="json")) + "\n",
            encoding="utf-8",
        )
    if crash_stage == "after_attempt":
        provider_start = ProviderAttemptStart(
            schema_version="rcaeval-re2-v2-dev3.provider-attempt-start.v1",
            semantic_operation_index=1,
            provider_attempt_index=1,
            retry_number=0,
            request_sha256="d" * 64,
            started_at_utc=started_at,
            retry_wait_ms=0,
            timeout_seconds=30.0,
            prompt_token_reservation=29_952,
            max_completion_tokens=2_048,
            attempt_token_reservation=32_000,
            policy_lock_sha256=(
                "7fd010103f83a1cb99b0c478ddafdf6e9fd0dc349a4297e7bb55c9b4157c202b"
            ),
        )
        provider_path = (
            run_root / "provider-attempt-starts" / "0001-0001-0.json"
        )
        provider_path.parent.mkdir(parents=True)
        provider_path.write_text(
            json.dumps(provider_start.model_dump(mode="json")) + "\n",
            encoding="utf-8",
        )
    transport = _FakeTransport(
        RCA100InitialDiagnosis(
            root_cause_entity_ref=base.entities[0].entity_ref,
            fault_type="cpu",
            confidence=1.0,
            evidence_refs=("metric:0001",),
            reasoning_steps=(
                RCA100ReasoningStep(
                    claim="Metric changed.",
                    entity_ref_or_none=base.entities[0].entity_ref,
                    evidence_refs=("metric:0001",),
                ),
            ),
            summary="Root found.",
        )
    )
    terminal = execute_live_arm(
        record,
        base=base,
        hierarchy=None,
        journal_root=journal_root,
        output_root=output_root,
        schedule_sha256="a" * 64,
        implementation_lock_sha256="b" * 64,
        provider_config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="synthetic",
            model="frozen-model",
        ),
        expected_model="frozen-model",
        timeout_seconds=30.0,
        max_completion_tokens=2_048,
        prompt_token_reservation=29_952,
        pacer=RequestPacer(0.0),
        budget=AttemptBudget(
            max_provider_attempts=2,
            max_retry_attempts=1,
            prompt_token_reservation=29_952,
            max_completion_tokens=2_048,
            max_conservative_tokens=64_000,
        ),
        retry_policy_sha256=(
            "7fd010103f83a1cb99b0c478ddafdf6e9fd0dc349a4297e7bb55c9b4157c202b"
        ),
        base_transport=transport,
    )
    assert transport.calls == 0
    assert terminal.status is LiveTerminalStatus.INTERRUPTED
    assert terminal.semantic_model_operations == semantic_operations
    assert terminal.provider_attempts == provider_attempts
    live_cli_module._verify_provider_run_sidecars(
        tmp_path, phase="tune", terminals=(terminal,)
    )


def test_h1_runtime_metadata_accepts_a_hierarchy_only_root(tmp_path) -> None:
    service = _entity(94)
    operation = LiveEntity(
        entity_ref="apm|apm.operation|hierarchy-only-root-case",
        entity_name="operation",
        layer=CanonicalEntityLayer.OPERATION,
        service_ancestor_or_none=service.entity_ref,
        parent_ref_or_none=service.entity_ref,
    )
    base = LiveBaseContext(
        alert_title="Alert",
        prompt_text="Investigate.",
        alert_entity_ref=operation.entity_ref,
        entities=(operation,),
        evidence=(
            EvidenceItem(
                evidence_ref="metric:0001",
                source="METRICS",
                entity_ref=operation.entity_ref,
                name="latency",
                started_at=1.0,
                ended_at=2.0,
                score=1.0,
                summary="Latency increased.",
            ),
        ),
        source_status={
            "METRICS": "AVAILABLE",
            "LOGS": "SOURCE_UNAVAILABLE",
            "TRACES": "SOURCE_UNAVAILABLE",
        },
    )
    hierarchy = build_hierarchical_context(
        base,
        HierarchySource(
            entities=(operation, service),
            parent_edges=((operation.entity_ref, service.entity_ref),),
            topology_edges=(),
            propagation_edges=(),
            source_visibility={operation.entity_ref: frozenset({"METRICS"})},
            first_anomaly_source={},
        ),
    )
    record = paired_schedule(
        (CaseRef(source="SYNTHETIC", source_key="case"),),
        seed=20260812,
        split="TUNE",
    )[1]
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref=service.entity_ref,
        fault_type="dependency timeout",
        confidence=0.8,
        evidence_refs=("metric:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="Operation is a downstream symptom.",
                entity_ref_or_none=operation.entity_ref,
                evidence_refs=("metric:0001",),
            ),
        ),
        summary="Service is the causal root.",
    )
    terminal = execute_live_arm(
        record,
        base=base,
        hierarchy=hierarchy,
        journal_root=tmp_path / "journal",
        output_root=tmp_path / "output",
        schedule_sha256="a" * 64,
        implementation_lock_sha256="b" * 64,
        provider_config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="synthetic",
            model="frozen-model",
        ),
        expected_model="frozen-model",
        timeout_seconds=30.0,
        max_completion_tokens=128,
        prompt_token_reservation=1_000,
        pacer=RequestPacer(0.0),
        budget=AttemptBudget(
            max_provider_attempts=2,
            max_retry_attempts=1,
            prompt_token_reservation=1_000,
            max_completion_tokens=128,
            max_conservative_tokens=2_256,
        ),
        retry_policy_sha256="c" * 64,
        base_transport=_FakeTransport(diagnosis),
    )

    assert terminal.status is LiveTerminalStatus.COMPLETED
    assert terminal.diagnosis_metadata is not None
    assert terminal.diagnosis_metadata.entity_layer is CanonicalEntityLayer.SERVICE
    assert terminal.diagnosis_metadata.service_ancestor_or_none == service.entity_ref
    assert terminal.diagnosis_metadata.root_provenance == "MODEL_STRONG_SINGLE_H1"


def test_live_runtime_rejects_existing_attempt_binding_drift(tmp_path) -> None:
    base = _base()
    record = paired_schedule(
        (CaseRef(source="SYNTHETIC", source_key="case"),),
        seed=20260812,
        split="TUNE",
    )[0]
    diagnosis = RCA100InitialDiagnosis(
        root_cause_entity_ref=base.entities[0].entity_ref,
        fault_type="cpu",
        confidence=1.0,
        evidence_refs=("metric:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="Metric changed.",
                entity_ref_or_none=base.entities[0].entity_ref,
                evidence_refs=("metric:0001",),
            ),
        ),
        summary="Root found.",
    )
    keyword_args = {
        "base": base,
        "hierarchy": None,
        "journal_root": tmp_path / "journal",
        "output_root": tmp_path / "output",
        "schedule_sha256": "a" * 64,
        "implementation_lock_sha256": "b" * 64,
        "provider_config": OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="synthetic",
            model="frozen-model",
        ),
        "expected_model": "frozen-model",
        "timeout_seconds": 30.0,
        "max_completion_tokens": 128,
        "prompt_token_reservation": 1_000,
        "pacer": RequestPacer(0.0),
        "budget": AttemptBudget(
            max_provider_attempts=2,
            max_retry_attempts=1,
            prompt_token_reservation=1_000,
            max_completion_tokens=128,
            max_conservative_tokens=2_256,
        ),
        "retry_policy_sha256": "c" * 64,
        "base_transport": _FakeTransport(diagnosis),
    }
    execute_live_arm(record, **keyword_args)
    attempt_path = tmp_path / "journal" / "run-attempts" / f"{record.run_id}.json"
    attempt_value = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt_value["schedule_sha256"] = "d" * 64
    attempt_path.write_text(json.dumps(attempt_value), encoding="utf-8")

    with pytest.raises(ValueError, match="binding"):
        execute_live_arm(record, **keyword_args)


def test_paired_metrics_count_rescue_damage_and_error_modes() -> None:
    rows = (
        CaseScore("a", "RCA100", b0_root=False, h1_root=True, b0_service=False, h1_service=True, b0_fault=True, h1_fault=True, b0_pair=False, h1_pair=True, b0_layer=False, h1_layer=True, b0_ancestor=False, h1_ancestor=False, b0_descendant=True, h1_descendant=False, b0_downstream=True, h1_downstream=False),
        CaseScore("b", "RCA100", b0_root=True, h1_root=False, b0_service=True, h1_service=False, b0_fault=True, h1_fault=True, b0_pair=True, h1_pair=False, b0_layer=True, h1_layer=False, b0_ancestor=False, h1_ancestor=True, b0_descendant=False, h1_descendant=True, b0_downstream=False, h1_downstream=True),
        CaseScore("c", "OBSS", b0_root=False, h1_root=True, b0_service=False, h1_service=True, b0_fault=False, h1_fault=False, b0_pair=False, h1_pair=False, b0_layer=True, h1_layer=True, b0_ancestor=False, h1_ancestor=False, b0_descendant=False, h1_descendant=False, b0_downstream=False, h1_downstream=False),
    )

    aggregate = aggregate_paired_scores(rows)

    assert aggregate["root_rescue"] == 2
    assert aggregate["root_damage"] == 1
    assert aggregate["root_net_rescue"] == 1
    assert aggregate["pair_rescue"] == 1
    assert aggregate["pair_damage"] == 1
    assert aggregate["entity_layer_mismatch_delta"] == 0
    assert aggregate["downstream_symptom_selection_delta"] == 0
    assert aggregate["ancestor_error_delta"] == 1
    assert aggregate["descendant_error_delta"] == 0
    inference = paired_development_inference(rows, seed=20260812)
    assert inference["bootstrap_replicates"] == 10_000
    assert inference["point_difference"] == pytest.approx(1 / 3)


def test_tune_gate_is_exact_and_cost_fail_closed() -> None:
    rca100 = {
        "root_rescue": 4,
        "root_damage": 1,
        "root_net_rescue": 3,
        "h1_root_correct": 60,
        "b0_root_correct": 57,
        "service_root_net_rescue": 0,
        "downstream_symptom_selection_delta": 0,
        "entity_layer_mismatch_delta": 0,
    }
    obss = {
        "root_net_rescue": 0,
        "root_damage": 1,
        "pair_net_rescue": 0,
    }
    combined = {"root_net_rescue": 3}
    execution = {
        "rca100_completed_b0": 103,
        "rca100_completed_h1": 103,
        "obss_completed_b0": 60,
        "obss_completed_h1": 60,
        "http_429": 0,
        "schema_privacy_schedule_failure": 0,
        "semantic_model_operations": 326,
        "terminal_count": 326,
        "specialist_calls": 0,
        "fusion_calls": 0,
    }

    passed = tune_gate(
        rca100=rca100,
        obss=obss,
        combined=combined,
        execution=execution,
        h1_input_token_ratio=1.35,
        h1_latency_ratio=1.40,
    )
    failed = tune_gate(
        rca100=rca100,
        obss=obss,
        combined=combined,
        execution=execution,
        h1_input_token_ratio=1.350001,
        h1_latency_ratio=1.40,
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert failed["verdict"] == "HIERARCHICAL_STRONG_SINGLE_LIVE_TUNE_NOT_PASSED"


def test_regression_gate_allows_bounded_429_with_fixed_denominator() -> None:
    aggregate = {
        "h1_root_correct": 100,
        "b0_root_correct": 100,
        "root_rescue": 1,
        "root_damage": 1,
        "root_net_rescue": 0,
        "h1_pair_correct": 70,
        "b0_pair_correct": 70,
        "pair_rescue": 1,
        "pair_damage": 1,
        "pair_net_rescue": 0,
    }
    execution = {
        "completed_b0": 119,
        "completed_h1": 118,
        "http_429": 1,
        "schema_privacy_schedule_failure": 0,
        "terminal_count": 240,
        "admitted_arms": 238,
        "semantic_model_operations": 238,
        "specialist_calls": 0,
        "fusion_calls": 0,
    }

    gate = regression_gate(
        aggregate=aggregate,
        execution=execution,
        h1_input_token_ratio=1.1,
        h1_latency_ratio=1.1,
    )

    assert gate["passed"]
    assert gate["checks"]["http_429_at_most_two"]
    assert gate["checks"]["mean_model_calls_exactly_one"]


def test_regression_not_admitted_disposition_is_create_once(tmp_path) -> None:
    record = paired_schedule(
        (CaseRef(source="SYNTHETIC", source_key="case"),),
        seed=20260813,
        split="REGRESSION",
    )[0]
    keyword_args = {
        "output_root": tmp_path / "output",
        "schedule_sha256": "a" * 64,
        "implementation_lock_sha256": "b" * 64,
    }

    first = terminalize_not_admitted(record, **keyword_args)
    second = terminalize_not_admitted(record, **keyword_args)

    assert first == second
    assert first.status is LiveTerminalStatus.NOT_ADMITTED
    assert first.failure_code == "NOT_ADMITTED_AFTER_HTTP429"
    assert first.semantic_model_operations == 0


def test_public_projection_scanner_rejects_case_identity_and_private_path() -> None:
    scan_public_payloads({Path("safe.json"): b'{"denominator":163}\n'})

    with pytest.raises(ValueError, match="leakage"):
        scan_public_payloads({Path("bad.json"): b'{"private_root":"/Users/name"}\n'})
    with pytest.raises(ValueError, match="case identity"):
        scan_public_payloads({Path("bad.json"): b'{"value":"case-abcdef123456"}\n'})


def test_scoring_artifact_hash_verification_rejects_post_lock_drift(tmp_path) -> None:
    evaluation = tmp_path / "evaluation"
    locks = tmp_path / "locks"
    evaluation.mkdir()
    locks.mkdir()
    aggregate = b'{"gate":{"verdict":"SYNTHETIC"}}\n'
    scores = b'{"records":[],"schema_version":"synthetic"}\n'
    (evaluation / "tune-aggregate.json").write_bytes(aggregate)
    (evaluation / "tune-case-scores.json").write_bytes(scores)
    (locks / "tune-scoring-lock.json").write_text(
        json.dumps(
            {
                "aggregate_sha256": hashlib.sha256(aggregate).hexdigest(),
                "case_scores_sha256": hashlib.sha256(scores).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    verify_scoring_artifact_hashes(tmp_path, "tune")
    (evaluation / "tune-aggregate.json").write_bytes(b'{"mutated":true}\n')

    with pytest.raises(ValueError, match="aggregate hash"):
        verify_scoring_artifact_hashes(tmp_path, "tune")


def test_live_boundary_scanner_covers_runtime_and_cli(tmp_path) -> None:
    scan_project(REPO_ROOT)
    runtime = tmp_path / "runtime.py"
    runtime.write_text(
        'value = identity.get("root_cause_service")\n', encoding="utf-8"
    )
    cli = tmp_path / "cli.py"
    cli.write_text(
        "from scripts.rca_live.evaluator import evaluate_tune\n",
        encoding="utf-8",
    )

    assert any("label key" in item for item in scan_file(runtime, role="RUNTIME"))
    assert any("post-lock" in item for item in scan_file(cli, role="CLI"))
