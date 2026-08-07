from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval.adapter import ArchitectureContext, IncidentManifest, SourceObservation
from ecomsre_rcaeval.artifacts import (
    read_json_object,
    sha256_file,
    sha256_tree,
    write_json_create_once,
)
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.freeze import (
    verify_source_bound_snapshot,
    verify_state_artifact,
)
from ecomsre_rcaeval.lifecycle import advance_state, current_state
from ecomsre_rcaeval.sanitize import verify_sanitized_holdout
from ecomsre_rcaeval.state import HoldoutState
from ecomsre_rcaeval.tools import SourceStatus, ToolEvidence
from scripts.rcaeval.common import (
    CONFIG_ROOT,
    frozen_schedule,
    provider_from_lock,
    verify_prompt_lock,
)


def _provider_health() -> dict[str, object]:
    provider = provider_from_lock()
    incident = IncidentManifest(
        case_id="synthetic-provider-health",
        system="RE2-OB",
        anomaly_timestamp=100,
        modalities=("metrics",),
    )
    context = ArchitectureContext(
        context_id="f" * 32,
        run_id="e" * 32,
        case_id=incident.case_id,
        architecture=Architecture.SINGLE,
        evidence=(
            ToolEvidence(
                evidence_id="metric:0001",
                service="checkoutservice",
                name="checkoutservice_cpu",
                started_at=90.0,
                ended_at=110.0,
                summary="Synthetic provider health evidence only.",
            ),
        ),
        canonical_evidence=(),
        specialist_assessments=(),
        source_observations=(
            SourceObservation(source="metrics", status=SourceStatus.AVAILABLE),
        ),
        investigated_sources=("metrics",),
        commander_stages=(),
        tool_call_count=1,
        targeted_refinement_used=False,
    )
    diagnosis = provider.diagnose(incident, context, Architecture.SINGLE)
    return {
        "model_calls": provider.calls,
        "known_provider_tokens": provider.last_usage_tokens,
        "diagnosis_schema_version": diagnosis.schema_version,
        "non_holdout_synthetic": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight a sealed RCAEval holdout")
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--sanitized-root", type=Path, required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    args = parser.parse_args()
    journal = args.control_root / "state-journal"
    if current_state(journal) is not HoldoutState.HOLDOUT_SEALED:
        raise ValueError("holdout preflight requires HOLDOUT_SEALED state")
    verify_source_bound_snapshot(args.control_root)
    seal_lock = verify_state_artifact(
        args.control_root,
        HoldoutState.HOLDOUT_SEALED,
        "locks/holdout-seal.json",
    )
    verify_prompt_lock()
    verify_sanitized_holdout(args.sanitized_root, expected_cases=90)
    sanitized_resolved = args.sanitized_root.resolve()
    evaluator_resolved = args.evaluator_root.resolve()
    if (
        sanitized_resolved == evaluator_resolved
        or sanitized_resolved in evaluator_resolved.parents
        or evaluator_resolved in sanitized_resolved.parents
    ):
        raise ValueError("Agent-visible and evaluator-only roots must differ")
    truth_path = args.evaluator_root / "ground-truth.json"
    if not truth_path.is_file() or truth_path.is_symlink():
        raise ValueError("evaluator-only Ground Truth mapping is missing")
    if (
        seal_lock.get("sanitized_manifest_sha256")
        != sha256_file(args.sanitized_root / "manifest.json")
        or seal_lock.get("sanitized_tree_sha256")
        != sha256_tree(
            args.sanitized_root,
            include_suffixes=(".csv", ".json"),
        )
        or seal_lock.get("ground_truth_sha256") != sha256_file(truth_path)
    ):
        raise ValueError("sealed holdout artifacts drifted before preflight")
    schedule = frozen_schedule()
    schedule_path = args.control_root / "locks" / "holdout-schedule.json"
    expected_schedule_sha = read_json_object(
        CONFIG_ROOT / "schedule-generation.json"
    ).get("expected_schedule_sha256")
    if len(schedule) != 270 or sha256_file(schedule_path) != expected_schedule_sha:
        raise ValueError("frozen holdout schedule is invalid")
    static_evidence = {
        "sanitized_manifest_sha256": sha256_file(
            args.sanitized_root / "manifest.json"
        ),
        "ground_truth_sha256": sha256_file(truth_path),
        "schedule_sha256": sha256_file(schedule_path),
        "protocol_freeze_sha256": sha256_file(
            args.control_root / "locks" / "protocol-freeze.json"
        ),
        "holdout_seal_lock_sha256": sha256_file(
            args.control_root / "locks" / "holdout-seal.json"
        ),
    }
    preflight_path = args.control_root / "locks" / "holdout-preflight.json"
    if preflight_path.exists():
        preflight = read_json_object(preflight_path)
        if (
            preflight.get("schema_version")
            != "rcaeval-re2.holdout-preflight.v1"
            or any(preflight.get(key) != value for key, value in static_evidence.items())
            or not isinstance(preflight.get("provider_health"), dict)
            or preflight["provider_health"].get("model_calls") != 1
            or preflight["provider_health"].get("non_holdout_synthetic") is not True
        ):
            raise ValueError("existing holdout preflight evidence is invalid")
        preflight_sha = sha256_file(preflight_path)
    else:
        preflight_sha = write_json_create_once(
            preflight_path,
            {
                "schema_version": "rcaeval-re2.holdout-preflight.v1",
                **static_evidence,
                "provider_health": _provider_health(),
            },
        )
    advance_state(
        journal,
        HoldoutState.HOLDOUT_PREFLIGHT_PASSED,
        evidence_sha256=preflight_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
