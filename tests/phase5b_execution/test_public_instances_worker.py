from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase5b.contracts import ExecutionSchedule
from ecomsre.phase5b.protocol import load_strict_json

from scripts.phase5b_execution.contracts import ScoredRunRequest
from scripts.phase5b_execution.public_instances import (
    PUBLIC_ANCHOR_ROOTS,
    materialize_public_instance,
    semantic_projection,
)
from scripts.phase5b_execution.worker import (
    load_worker_instance,
    parse_actual_worker_record,
    sanitized_worker_environment,
)
from scripts.phase5b_execution.contracts import (
    ObservedDiagnosisRecord,
    ProviderUsageRecord,
    TerminalStatus,
    seal_raw_record,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _public_request(template_id: str, seed_id: str) -> ScoredRunRequest:
    schedule = load_strict_json(
        PROJECT_ROOT / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )
    scheduled = next(
        item
        for item in schedule.runs
        if item.template_id == template_id and item.seed_id == seed_id
    )
    return ScoredRunRequest.from_scheduled_run(scheduled)


def test_public_seed_materialization_is_deterministic_and_semantic_invariant(
    tmp_path: Path,
) -> None:
    template_id = "ad-partial-failure-complete"
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    other_root = tmp_path / "other"

    first = materialize_public_instance(
        PROJECT_ROOT, first_root, template_id, "seed-02"
    )
    second = materialize_public_instance(
        PROJECT_ROOT, second_root, template_id, "seed-02"
    )
    other = materialize_public_instance(
        PROJECT_ROOT, other_root, template_id, "seed-03"
    )

    assert _tree_sha256(first) == _tree_sha256(second)
    assert _tree_sha256(first) != _tree_sha256(other)
    base = load_replay_case(
        PROJECT_ROOT / PUBLIC_ANCHOR_ROOTS[template_id], template_id
    )
    transformed = load_replay_case(first.parent, first.name)
    assert semantic_projection(transformed) == semantic_projection(base)


def test_all_six_public_anchors_materialize_without_tracked_fixture_copies(
    tmp_path: Path,
) -> None:
    for template_id in PUBLIC_ANCHOR_ROOTS:
        base = load_replay_case(
            PROJECT_ROOT / PUBLIC_ANCHOR_ROOTS[template_id], template_id
        )
        seed_hashes: set[str] = set()
        for seed_index in range(5):
            seed_id = f"seed-{seed_index:02d}"
            instance = materialize_public_instance(
                PROJECT_ROOT, tmp_path, template_id, seed_id
            )
            assert instance == tmp_path / template_id / seed_id
            assert json.loads((instance / "manifest.json").read_text())[
                "case_id"
            ] == seed_id
            transformed = load_replay_case(instance.parent, instance.name)
            assert semantic_projection(transformed) == semantic_projection(base)
            seed_hashes.add(_tree_sha256(instance))
        assert len(seed_hashes) == 5

    assert not (PROJECT_ROOT / "config/phase5b/public-seed-instances").exists()


def test_worker_environment_and_request_exclude_truth_capabilities(
    tmp_path: Path,
) -> None:
    request = _public_request("ad-partial-failure-complete", "seed-01")
    environment = sanitized_worker_environment(
        {
            "PATH": "/usr/bin",
            "PHASE5B_AGENT_VISIBLE_ROOT": str(tmp_path / "agent-visible"),
            "PHASE5B_GROUND_TRUTH_ROOT": "/forbidden/truth",
            "PHASE5B_HIDDEN_PACK_ROOT": "/forbidden/pack",
            "PHASE5B_EVALUATOR_TRUTH_ROOT": "/forbidden/evaluator",
            "PHASE5B_BUILDER_ROOT": "/forbidden/builder",
        }
    )
    serialized_request = json.dumps(request.model_dump(mode="json"))

    assert environment["PHASE5B_AGENT_VISIBLE_ROOT"].endswith("agent-visible")
    assert all(
        marker not in key
        for key in environment
        for marker in ("GROUND_TRUTH", "HIDDEN_PACK_ROOT", "EVALUATOR_TRUTH", "BUILDER")
    )
    for forbidden in (
        "ground_truth",
        "hidden_pack",
        "coverage",
        "expected_decision",
        "root_service",
        "fault_mechanism",
    ):
        assert forbidden not in serialized_request

    replay_case = load_worker_instance(
        project_root=PROJECT_ROOT,
        request=request,
        environment=environment,
        materialized_root=tmp_path / "materialized",
    )
    assert replay_case.case_id == "seed-01"


def test_actual_worker_json_boundary_preserves_strict_enum_and_datetime() -> None:
    request = _public_request("ad-partial-failure-complete", "seed-01")
    diagnosis = ObservedDiagnosisRecord(
        run_id=request.run_id,
        decision="NEED_MORE_EVIDENCE",
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="synthetic",
        supporting_evidence=(),
        contradicting_evidence=(),
        missing_evidence=("synthetic gap",),
        confidence=0.2,
        decision_rationale="Synthetic subprocess boundary record.",
        recommended_next_action="No external action.",
    )
    record = seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=TerminalStatus.COMPLETED,
        observed_diagnosis=diagnosis,
        investigated_sources=("METRICS",),
        targeted_refinement_used=False,
        usage=ProviderUsageRecord(
            model_calls=1,
            tool_calls=1,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            workflow_tokens=0,
            combined_tokens=15,
            provider_network_calls=1,
            provider_usage_known=True,
        ),
        evidence_class="ACTUAL_SCORED",
        provider_attempted=True,
        latency_ms=1,
        failure_code=None,
        failure_stage=None,
    )

    parsed = parse_actual_worker_record(record.model_dump(mode="json"))

    assert parsed == record
    assert parsed.terminal_status is TerminalStatus.COMPLETED
