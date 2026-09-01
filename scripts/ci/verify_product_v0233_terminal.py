#!/usr/bin/env python3
"""Verify the public Product v0.2.3.3 blocked repository terminal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.formal_live_v0233 import (
    BaselineRestartProofV0233,
    FormalClosureProofV0233,
    FormalExecutionBlockerV0233,
    FormalTrafficResultV0233,
    RuntimeAuthorityProofV0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalStateCloneV0233,
)
from ecomsre.product.pilot.repository_state_v0233 import (
    ProductV0233RepositoryStateManifest,
    RepositoryPhaseV0233,
)


_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
_EXECUTION_HEAD = "466796648c2c4a3360b911a12be1ee806d39124e"
_MANIFEST_SHA256 = "08fdbd61e3fa439b55b1ef903bdea26dee6a3c839129bef53ee99c19a3c61014"
_REQUIRED_ABSENCES = (
    "docs/analysis/product-v0233-fresh-runtime-snapshot.json",
    "docs/analysis/product-v0233-incident-traffic-binding.json",
    "docs/analysis/product-v0233-evidence-assessment.json",
    "docs/analysis/product-v0233-diagnosis-stage-journal.json",
    "docs/analysis/product-v0233-diagnosis-blocker.json",
    "docs/analysis/product-v0233-diagnosis-blocker.md",
    "docs/analysis/product-v0233-knowledge-loop-handoff.json",
    "docs/analysis/product-v0233-knowledge-loop-handoff.md",
    "docs/results/product-v0233-nofault-acceptance.json",
    "docs/results/product-v0233-nofault-acceptance.md",
    "docs/results/product-v0233-limitations.md",
    "docs/results/product-v0233-interview-brief.md",
)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Product v0.2.3.3 JSON object differs: {path.name}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_public_file(
    root: Path,
    artifact: Mapping[str, Any],
    *,
    file_field: str = "file_sha256",
) -> Path:
    relative = artifact.get("public_path")
    if not isinstance(relative, str) or relative.startswith(("/", ".local/")):
        raise ValueError("Product v0.2.3.3 public evidence path differs")
    path = root / relative
    expected = artifact.get(file_field)
    if (
        path.is_symlink()
        or not path.is_file()
        or not isinstance(expected, str)
        or _sha256_file(path) != expected
    ):
        raise ValueError(f"Product v0.2.3.3 public evidence differs: {relative}")
    return path


def _verify_required_absences(root: Path, declared: list[object]) -> None:
    if tuple(declared) != _REQUIRED_ABSENCES:
        raise ValueError("Product v0.2.3.3 required absence inventory differs")
    for relative in _REQUIRED_ABSENCES:
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError(
                f"Product v0.2.3.3 forbidden terminal artifact exists: {relative}"
            )


def verify_product_v0233_terminal(root: Path) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    manifest_path = (
        project / "docs/analysis/product-v0233-formal-blocker-evidence-manifest.json"
    )
    manifest = _object(manifest_path)
    supplied_manifest_sha256 = manifest.pop("manifest_sha256", None)
    if (
        supplied_manifest_sha256 != semantic_sha256_v22(manifest)
        or supplied_manifest_sha256 != _MANIFEST_SHA256
        or manifest.get("schema_version")
        != "ecomsre.product.formal-blocker-evidence-manifest.v0233"
        or manifest.get("goal_version")
        != "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
        or manifest.get("terminal") != _TERMINAL
        or manifest.get("execution_head") != _EXECUTION_HEAD
        or manifest.get("failure_stage") != "FORMAL_TRAFFIC_PASS"
        or manifest.get("safe_error_code") != "TypeError:FORMAL_TRAFFIC_PASS"
        or manifest.get("one_shot_consumed") is not True
        or manifest.get("formal_rerun_authorized") is not False
        or manifest.get("diagnosis_retry_authorized") is not False
        or manifest.get("formal_healthy_traffic_execution_count") != 1
        or manifest.get("formal_clone_count") != 1
        or manifest.get("completed_transactions") != 30
        or manifest.get("new_incident_count") != 0
        or manifest.get("new_diagnosis_count") != 0
        or manifest.get("measured_result_count") != 0
        or manifest.get("measured_terminal") is not None
        or manifest.get("nofault_acceptance_complete") is not False
        or manifest.get("knowledge_loop_handoff_authorized") is not False
    ):
        raise ValueError("Product v0.2.3.3 blocker evidence manifest differs")

    artifacts = manifest.get("artifacts")
    closure_claim = manifest.get("authority_and_closure")
    required_absences = manifest.get("required_absences")
    if (
        not isinstance(artifacts, dict)
        or not isinstance(closure_claim, dict)
        or not isinstance(required_absences, list)
        or any(not isinstance(item, str) for item in required_absences)
    ):
        raise ValueError("Product v0.2.3.3 blocker evidence inventory differs")
    _verify_required_absences(project, required_absences)

    clone_path = _require_public_file(project, artifacts["formal_state_clone"])
    authority_path = _require_public_file(
        project, artifacts["runtime_authority"], file_field="public_file_sha256"
    )
    restart_path = _require_public_file(
        project, artifacts["baseline_restart"], file_field="public_file_sha256"
    )
    traffic_path = _require_public_file(
        project, artifacts["formal_traffic"], file_field="public_file_sha256"
    )
    closure_path = _require_public_file(project, artifacts["formal_closure"])
    blocker_path = _require_public_file(project, artifacts["formal_blocker"])
    repository_path = _require_public_file(project, artifacts["repository_state"])
    progress_path = _require_public_file(project, artifacts["progress"])

    clone = FreshFormalStateCloneV0233.model_validate_json(clone_path.read_bytes())
    authority = RuntimeAuthorityProofV0233.model_validate_json(
        authority_path.read_bytes()
    )
    restart = BaselineRestartProofV0233.model_validate_json(restart_path.read_bytes())
    traffic = FormalTrafficResultV0233.model_validate_json(traffic_path.read_bytes())
    closure = FormalClosureProofV0233.model_validate_json(closure_path.read_bytes())
    blocker = FormalExecutionBlockerV0233.model_validate_json(blocker_path.read_bytes())
    repository = ProductV0233RepositoryStateManifest.model_validate_json(
        repository_path.read_bytes()
    )
    progress = _object(progress_path)
    progress_sha256 = progress.pop("progress_sha256", None)

    public_semantic = {
        "formal_state_clone": clone.clone_sha256,
        "runtime_authority": authority.proof_sha256,
        "baseline_restart": restart.proof_sha256,
        "formal_traffic": traffic.result_sha256,
        "formal_closure": closure.closure_sha256,
        "formal_blocker": blocker.blocker_sha256,
        "repository_state": repository.manifest_sha256,
        "progress": progress_sha256,
    }
    if any(
        artifacts[name].get("semantic_sha256") != value
        for name, value in public_semantic.items()
    ):
        raise ValueError("Product v0.2.3.3 public semantic binding differs")

    zero_safety = (
        blocker.new_incident_count,
        blocker.new_diagnosis_count,
        blocker.measured_result_count,
        blocker.safety_observation.provider_calls,
        blocker.safety_observation.agent_writes,
        blocker.safety_observation.runbook_executions,
        blocker.safety_observation.fault_attempts,
        blocker.safety_observation.knowledge_loop_executions,
    )
    exact = (
        authority.admission_sha256
        == restart.admission_sha256
        == traffic.admission_sha256
        == blocker.admission_sha256
        and blocker.terminal == _TERMINAL
        and blocker.failure_stage == "FORMAL_TRAFFIC_PASS"
        and blocker.safe_error_code == "TypeError:FORMAL_TRAFFIC_PASS"
        and blocker.formal_clone_count == 1
        and blocker.formal_execution_count == 1
        and blocker.formal_clone_sha256 == clone.clone_sha256
        and traffic.execution.execution_sha256
        == artifacts["formal_traffic"].get("execution_sha256")
        and traffic.execution.run.result_sha256
        == artifacts["formal_traffic"].get("traffic_run_sha256")
        and traffic.execution.run.successful_transactions == 30
        and traffic.execution.run.failed_transactions == 0
        and traffic.execution.run.transport_retry_count == 0
        and traffic.monotonic_duration_ms >= 300_000
        and closure.verdict == "CLEAN"
        and closure.safety_observation == blocker.safety_observation
        and closure.source_database_before_sha256
        == closure.source_database_after_sha256
        and closure.frozen_semantic_surface_before_sha256
        == closure.frozen_semantic_surface_after_sha256
        and not any(zero_safety)
        and repository.phase is RepositoryPhaseV0233.FORMAL_BLOCKED
        and repository.formal_blocker_sha256 == blocker.blocker_sha256
        and repository.cleanup_proof_sha256 == closure.closure_sha256
        and repository.formal_clone_count == 1
        and repository.formal_execution_count == 1
        and repository.new_incident_count == 0
        and repository.new_diagnosis_count == 0
        and repository.measured_result_count == 0
        and repository.formal_result_sha256 is None
        and repository.knowledge_handoff_sha256 is None
        and progress_sha256 == semantic_sha256_v22(progress)
        and progress.get("current_terminal") == _TERMINAL
        and progress.get("phase") == "INCREMENT_4_FORMAL_BLOCKED"
        and progress.get("formal_transaction_count") == 30
        and progress.get("new_incident_count") == 0
        and progress.get("new_diagnosis_count") == 0
        and progress.get("measured_result_count") == 0
        and progress.get("next_gate") == "NONE"
        and closure_claim
        == {
            "action_authority": "NONE",
            "product_cleanup": "CLEAN",
            "demo_cleanup": "CLEAN",
            "source_state_unchanged": True,
            "frozen_semantic_surface_unchanged_during_execution": True,
            "provider_calls": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "fault_attempts": 0,
            "knowledge_loop_executions": 0,
            "non_owned_resources_changed": False,
        }
    )
    if not exact:
        raise ValueError("Product v0.2.3.3 blocked repository binding differs")
    return {
        "terminal": _TERMINAL,
        "failure_stage": blocker.failure_stage,
        "safe_error_code": blocker.safe_error_code,
        "one_shot_consumed": True,
        "formal_clone_count": blocker.formal_clone_count,
        "formal_execution_count": blocker.formal_execution_count,
        "formal_transaction_count": traffic.execution.run.successful_transactions,
        "new_incident_count": blocker.new_incident_count,
        "new_diagnosis_count": blocker.new_diagnosis_count,
        "measured_result_count": blocker.measured_result_count,
        "measured_terminal": blocker.measured_terminal,
        "formal_rerun_authorized": False,
        "diagnosis_retry_authorized": False,
        "action_authority": blocker.action_authority,
        "closure": closure.verdict,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    print(
        json.dumps(
            verify_product_v0233_terminal(arguments.project_root), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v0233_terminal",)
