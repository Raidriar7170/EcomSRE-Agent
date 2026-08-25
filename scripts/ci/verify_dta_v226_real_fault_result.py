"""Verify the frozen public DTA v2.2.6 real-fault result and bindings."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultOpaqueCaptureV1,
    require_public_capture_opaque_v225,
)
from ecomsre.dta_v2.v22.real_fault_cli_v226 import RealFaultStudyArtifactV226
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultStudyArmV226,
)
from ecomsre.dta_v2.v22.real_fault_manifest_v226 import (
    PREDECESSOR_ALIASES_V225,
    RealFaultCaseSetV226,
    RealFaultManifestV226,
    RealFaultPublicAliasMapSetV226,
    RealFaultTruthSetV226,
)
from ecomsre.dta_v2.v22.real_fault_preflight_v226 import sha256_file_v226
from ecomsre.dta_v2.v22.real_fault_scorer_v226 import (
    RealFaultComparisonDispositionV226,
    RealFaultTransferTerminalV226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226,
)


EXPECTED_RESULT_SHA256_V226 = (
    "f219d21a981789a0d22093273f2220bd94177b6e02249796e098d0f56573b814"
)
_V226_SQUASH_INTEGRATION_HEAD = "f17688f4c313b1483bfb7c56675c429605faf489"


def _require_frozen_code_reachable(root: Path, *, code_head: str) -> None:
    """Accept the original admission history or its exact squash integration."""

    for trusted_head in (code_head, _V226_SQUASH_INTEGRATION_HEAD):
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", trusted_head, "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            return
    raise ValueError("v2.2.6 frozen code is not reachable from this checkout")


def verify_dta_v226_real_fault_result(repository_root: Path) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    config_root = root / "config/dta-v226-real-fault"
    result_path = root / "docs/results/dta-v226-real-fault-comparison.json"
    if sha256_file_v226(result_path) != EXPECTED_RESULT_SHA256_V226:
        raise ValueError("v2.2.6 frozen result bytes differ")
    artifact = RealFaultStudyArtifactV226.model_validate_json(result_path.read_bytes())
    manifest = RealFaultManifestV226.model_validate_json(
        (config_root / "manifest.json").read_bytes()
    )
    aliases = RealFaultPublicAliasMapSetV226.model_validate_json(
        (config_root / "alias-maps.json").read_bytes()
    )
    cases = RealFaultCaseSetV226.model_validate_json(
        (config_root / "cases.json").read_bytes()
    )
    truths = RealFaultTruthSetV226.model_validate_json(
        (config_root / "truth.json").read_bytes()
    )
    if artifact.manifest != manifest:
        raise ValueError("v2.2.6 result manifest bytes differ")
    if aliases.aliases == PREDECESSOR_ALIASES_V225:
        raise ValueError("v2.2.6 aliases repeat the predecessor")
    if (
        manifest.case_set_sha256 != cases.case_set_sha256
        or manifest.truth_set_sha256 != truths.truth_set_sha256
        or manifest.capture_pair_sha256 != artifact.capture_pair_sha256
    ):
        raise ValueError("v2.2.6 public set bindings differ")
    captures: list[RealFaultOpaqueCaptureV1] = []
    predecessor_hashes = {
        sha256_file_v226(path)
        for path in (root / "config/dta-v225-real-fault/captures").glob("*.json")
    }
    for binding in cases.cases:
        path = root / binding.capture_path
        if not path.is_file() or sha256_file_v226(path) in predecessor_hashes:
            raise ValueError("v2.2.6 capture is absent or repeats predecessor bytes")
        capture = RealFaultOpaqueCaptureV1.model_validate_json(path.read_bytes())
        require_public_capture_opaque_v225(capture)
        if (
            capture.case_id != binding.case_id
            or capture.alias_map_name != binding.alias_map_name
            or capture.opaque_capture_sha256 != binding.capture_sha256
        ):
            raise ValueError("v2.2.6 capture binding differs")
        captures.append(capture)
    physical_states = {item.physical_capture_sha256 for item in captures}
    if len(physical_states) != 2:
        raise ValueError("v2.2.6 public cases do not bind exactly two physical states")
    freeze = manifest.pre_live_freeze
    if (
        hashlib.sha256(
            REAL_FAULT_SELECTION_SYSTEM_PROMPT_V226.encode("utf-8")
        ).hexdigest()
        != freeze.selection_prompt_sha256
    ):
        raise ValueError("v2.2.6 frozen selection Prompt differs")
    for relative, expected in (
        (
            "src/ecomsre/dta_v2/v22/real_fault_terminalizer_v226.py",
            freeze.terminalizer_sha256,
        ),
        (
            "src/ecomsre/dta_v2/v22/real_fault_scorer_v226.py",
            freeze.scorer_sha256,
        ),
        (
            "docs/analysis/dta-v226-provider-development.json",
            freeze.provider_development_summary_sha256,
        ),
        (
            "docs/external-reviews/dta-v226-real-fault-pre-live-review.md",
            freeze.pre_live_review_sha256,
        ),
    ):
        if sha256_file_v226(root / relative) != expected:
            raise ValueError(f"v2.2.6 frozen binding drifted: {relative}")
    _require_frozen_code_reachable(root, code_head=freeze.code_head)
    score = artifact.score
    model_directed_score = next(
        (
            item
            for item in score.arm_scores
            if item.arm is RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL
        ),
        None,
    )
    model_directed_runs = tuple(
        run
        for run in artifact.execution.runs
        if run.arm is RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL
    )
    if (
        artifact.accepted_live_campaigns != 1
        or artifact.shared_physical_captures != 2
        or artifact.execution_count != 1
        or artifact.final_snapshot_arm_runs != 8
        or not artifact.baseline_restored
        or artifact.cleanup != "CLEAN"
        or artifact.non_owned_changes != 0
        or not score.all_snapshot_runs_valid
        or score.transfer_terminal is not RealFaultTransferTerminalV226.SUPPORTED
        or score.comparison_disposition
        is not RealFaultComparisonDispositionV226.CURRENT_ADVANTAGE
        or score.current_snapshot_exact_count != 4
        or model_directed_score is None
        or model_directed_score.exact_count != 0
        or len(model_directed_runs) != 4
        or any(run.prediction.terminal != "ABSTAIN" for run in model_directed_runs)
        or not score.current_live_fault_exact
        or not score.current_live_baseline_exact
        or artifact.agent_writes
        or artifact.action_proposals
        or artifact.runbook_executions
    ):
        raise ValueError("v2.2.6 frozen result terminal differs")
    markdown_paths = (
        root / "docs/results/dta-v226-real-fault-comparison.md",
        root / "docs/results/dta-v226-real-fault-error-analysis.md",
        root / "docs/results/dta-v226-real-fault-interview-brief.md",
    )
    for path in markdown_paths:
        if not path.is_file():
            raise ValueError(f"v2.2.6 result report is absent: {path.name}")
    result_markdown = markdown_paths[0].read_text(encoding="utf-8")
    if "Post-terminal Docker inspection found zero" in result_markdown:
        raise ValueError("v2.2.6 result report exceeds frozen cleanup evidence")
    for marker in (
        "DTA_V226_CURRENT_REAL_FAULT_TRANSFER_SUPPORTED",
        "CURRENT_RUNTIME_ACQUISITION_ADVANTAGE",
        "not the exact frozen original v2 Agent",
    ):
        if marker not in result_markdown:
            raise ValueError(f"v2.2.6 result report lacks marker: {marker}")
    return {
        "status": "DTA_V226_REAL_FAULT_RESULT_VERIFIED",
        "execution_count": artifact.execution_count,
        "arm_run_count": artifact.execution.arm_run_count,
        "valid_terminal_count": sum(
            run.status.value == "VALID_TERMINAL" for run in artifact.execution.runs
        ),
        "transfer_terminal": score.transfer_terminal.value,
        "comparison_disposition": score.comparison_disposition.value,
        "baseline_restored": artifact.baseline_restored,
        "cleanup": artifact.cleanup,
        "non_owned_changes": artifact.non_owned_changes,
        "artifact_sha256": EXPECTED_RESULT_SHA256_V226,
    }


def main() -> int:
    import json

    result = verify_dta_v226_real_fault_result(Path(__file__).resolve().parents[2])
    print(json.dumps(result, sort_keys=True))
    print("DTA_V226_REAL_FAULT_RESULT_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_dta_v226_real_fault_result",)
