"""Verify the DTA v2.2 PR-D controller and Provider protocol boundary."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22 import PR_D_TERMINAL
from ecomsre.dta_v2.v22.controller_contracts import (
    ControllerDecisionV22,
    HypothesisCatalogV22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerTurnInputV22,
    build_common_triage_snapshot_v22,
    build_controller_turn_input_v22,
)
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    EvaluationArmV22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    build_controller_identity_manifests_v22,
    build_one_shot_oracle_context_v22,
    probe_provider_output_mode_v22,
    select_deterministic_router_decision_v22,
)
from ecomsre.dta_v2.v22.controller_provider import (
    ProviderControllerTurnV22,
    _controller_schema_v22,
)
from ecomsre.dta_v2.v22.controller_runtime import (
    PlanCorrectionV22,
    process_controller_decision_v22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    ProviderProtocolCapabilityReportV3,
    ProviderProtocolPartialFailureReceiptV3,
    run_local_protocol_capability_suite_v22,
    run_provider_protocol_capability_suite_v22,
    run_provider_protocol_capability_suite_v3,
    run_provider_protocol_replicate_v3,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_dta_v22_pr_b import verify_pr_b_protocol
from scripts.ci.verify_dta_v22_pr_c import (
    _verify_runtime_contracts as _verify_pr_c_runtime_contracts,
    verify_pr_c_bindings,
    verify_pr_c_protocol,
)
from scripts.dta_v22.run_pr_d_provider_protocol import (
    _FORMAL_HTTP_AUTO_RETRY_COUNT,
    _FORMAL_INTER_REPLICATE_COOLDOWN_SECONDS,
    _FORMAL_MIN_REQUEST_INTERVAL_SECONDS,
    _FORMAL_REPLICATE_IDS,
)


PR_D_BASE = "145d152c2c2d1367e7dac2f0229e2b369fbe55dc"
PR_D_PR = 60
PR_D_BRANCH = "codex/dta-v22-p0-pr-d-planner-lite"
BLOCKED_PR_D_TERMINAL = "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
EXECUTION_READY_PR_D_TERMINAL = (
    "DTA_V22_PR_D_PROVIDER_PROTOCOL_V3_EXECUTION_READY"
)
PR_D_MANIFEST = Path("config/dta-v22/pr-d-controller-bindings.v1.json")
PR_D_SUCCESSOR_ATTESTATION = Path(
    "config/dta-v22/pr-d-successor-attestation.v1.json"
)
PR_C_SUCCESSOR_ATTESTATION = Path(
    "config/dta-v22/pr-c-successor-attestation.v2.json"
)
PROVIDER_SUMMARY = Path(
    "docs/analysis/dta-v22-pr-d-provider-protocol-summary.json"
)
PROVIDER_V3_PREREGISTRATION = Path(
    "config/dta-v22/pr-d-provider-protocol-v3-preregistration.json"
)
PROVIDER_V3_REPLICATE_SUMMARIES = (
    Path(
        "docs/analysis/dta-v22-pr-d-provider-protocol-v3-replicate-a-summary.json"
    ),
    Path(
        "docs/analysis/dta-v22-pr-d-provider-protocol-v3-replicate-b-summary.json"
    ),
)
PROVIDER_V3_CAMPAIGN_SUMMARY = Path(
    "docs/analysis/dta-v22-pr-d-provider-protocol-v3-campaign-summary.json"
)
PROVIDER_ATTEMPT_PATHS = (
    Path(
        "docs/analysis/"
        "dta-v22-pr-d-provider-protocol-attempt-1-invalid-location.json"
    ),
    Path(
        "docs/analysis/"
        "dta-v22-pr-d-provider-protocol-attempt-2-validator-abort.json"
    ),
    Path(
        "docs/analysis/"
        "dta-v22-pr-d-provider-protocol-attempt-3-rate-limited.json"
    ),
    Path(
        "docs/analysis/"
        "dta-v22-pr-d-provider-protocol-attempt-4-rate-limited.json"
    ),
    Path(
        "docs/analysis/"
        "dta-v22-pr-d-provider-protocol-attempt-5-gate-blocked.json"
    ),
)
EXPECTED_PROVIDER_ATTEMPT_RAW_SHA256S = (
    "aa956933027cdd2902ebcc9a8c3b0df076df69b61ae37a1019bacbc8742a7552",
    "6510bf827dd8b2348ee0aab0560e404a41d34ce42adc3a719dfa901200c8f57e",
    "391973adb861c7ec5e93e2c59b38726680435c4f93a04fda7551014a9096d15f",
    "b2051cf6f06c2121e98f4c56defa651755dea7842a97dbefa6c056b41b23c0bf",
    "ada22ef182f721e586a2d6e61e8f2138a9ae33d6fca6062830a63265852eee5a",
)
EXPECTED_V3_FROZEN_PATHS = (
    Path("scripts/dta_v22/run_pr_d_provider_protocol.py"),
    Path("src/ecomsre/dta_v2/v22/protocol_suite.py"),
    Path("src/ecomsre/dta_v2/v22/controller_contracts.py"),
    Path("src/ecomsre/dta_v2/v22/controller_inputs.py"),
    Path("src/ecomsre/dta_v2/v22/controller_modes.py"),
    Path("src/ecomsre/dta_v2/v22/controller_provider.py"),
    Path("src/ecomsre/dta_v2/v22/controller_runtime.py"),
    Path("src/ecomsre/dta_v2/v22/action_catalog.py"),
    Path("src/ecomsre/dta_v2/v22/memory.py"),
    Path("src/ecomsre/dta_v2/v22/predicates.py"),
    Path("src/ecomsre/dta_v2/v22/diagnosis.py"),
)
EXPECTED_V3_IMPLEMENTATION_COMMIT = (
    "f9625cd45a1ed5a8ae38b56aac9e08dc99972902"
)
EXPECTED_V3_IMPLEMENTATION_TREE = "fac6a7f2db96e3705a5fbd5e7973fe20d25161c2"
EXPECTED_V3_REPLICATE_RAW_SHA256S = (
    "109ecbdf18651467bf6cc4f902accfc6d1fdca881692a0dc4326ab00eb4804e6",
    "7ee47877a313ac38641a5fc37f7799957301e1162f03cdd764e841dcda071363",
)
EXPECTED_V3_REPLICATE_SEMANTIC_SHA256S = (
    "17d714fb238f0968bbaf1cea49b39ed0e7db0c3261554a75d39eb0c79e2e6788",
    "c04f6ecd1fc305fcb21ec8bfc9dfcbbfa049adf27499d91b14b63c4b5815ae40",
)
EXPECTED_V3_CAMPAIGN_RAW_SHA256 = (
    "ddf1cc0f8eaaca2b7fefa81242f06581bdf69dddd78a0a082d6d312b028c5586"
)
EXPECTED_V3_CAMPAIGN_SEMANTIC_SHA256 = (
    "b23184d23ad5d6fc801e85efca268d5c7e7ad951ee004b8221fe2a5889211170"
)
EXPECTED_V3_PREREGISTRATION_SHA256 = (
    "3ef35bc80a151e90c4bc21f27f061e496819a916e12020ff20dcd65719d03a8f"
)
EXPECTED_V3_PROBE_REPORT_SHA256 = (
    "e43cb0dc1bb070898cad08ad0bf78aa59297fb3035225da25951013b8425e775"
)
EXPECTED_V3_PROBE_EVIDENCE_SHA256 = (
    "f5c22d1d864a5a83a63979610a13237c4a5a74c1c7db0a36c77563f5e8076035"
)
EXPECTED_V3_CONTROLLER_SCHEMA_SHA256 = (
    "46ec7840b93789e1cae477d7f9569c8f30df072de247d564586c402e650ee6f0"
)
EXPECTED_V3_CONTROLLER_IDENTITY_SHA256S = (
    "9b0ee4e2ad384d51e6cc6ab6426f5d2dca0f351618ab1518e2e124246fd85836",
    "e188d6e73e18ca8d552f436d42431fdfd4bee626bad16abd0f0b7edd73ba2920",
    "8d2906f62dd6680ea942f8d7e27ce6f5fb413eb8b67143aeac47289878eaaf30",
    "35b63334e485ceef7a1d4c55165851fc95bf4097594e73ae73da82693d8666e8",
)
EXPECTED_V3_CONTROLLER_PROMPT_SHA256S = (
    "68fc684f217ba7200b82654a46a2f96a18b12fe0d9feab4291f9f9bf6299c6c4",
    "fbec445184f2f4732db2f6267b951c831730ff7090c81aa3097d9c0bc9953fe0",
    "7e76d33c6569e075568878a95a673d1d671e5a936493f153892ac673bccdf211",
    "577150ee5e76a8715d509cce7e8b94ba77b42391dbd63f6c307ed70417936517",
)
EXPECTED_V3_OUTCOME_SHA256S = (
    "2906c1436f0c981694bceaf19b973c5efbf98e08d930d76649a2cd40f2123761",
    "57caf68b427e5095dbabcbfa5b066e795e592fdfbc2edeab397b9a5c67d6a5d7",
)
EXPECTED_MANIFEST_SHA256 = (
    "4a8ad04967009af871d6f8ed51d68464218f36e3a64895a47387fdc0193cf7bb"
)
EXPECTED_SUMMARY_SHA256 = (
    "8911b46de9c0cbd2c063e5af94ca42c153c92d9de4888bf5da0220b573416bf1"
)
EXPECTED_REPORT_SHA256 = (
    "088b9febb807af46fd0708cd1a0a6cdb2ed7b943a1f44ec615173a53302bbdb8"
)
EXPECTED_IMPLEMENTATION_COMMIT = "b60f164df1409422110e7a72cff682ac59cf66f0"
EXPECTED_IMPLEMENTATION_TREE = "5715e24e52c27dc25cae0100164b407213ac2994"
EXPECTED_IDENTITY_SHA256S = (
    "f1c69ba0cd2d2d107748ab2bd66833a7ac0293ddaa1857b37227a1681e9b1ce8",
    "f9236c3939866d0c41c93b67fa0811b4f1c3cb6364a68e7b7e17c4ff88eb5176",
    "754e649a792919baf6cb2827f7787411c47e9b9af08324210db20b5e335e37cc",
    "bafbbbed13fa98e1bfaef754ecd1fcbff3ffb485d3840f6bbdfca72c906b4b37",
)
EXPECTED_PR_D_CHANGED_PATHS = (
    Path(".github/workflows/agent-mainline.yml"),
    Path("docs/DECISIONS.md"),
    Path("docs/analysis/dta-v22-p0-master-progress.json"),
    *PROVIDER_ATTEMPT_PATHS,
    Path("docs/human-briefs/2026-08-20-dta-v22-pr-d-controller-protocol.md"),
    Path("scripts/ci/verify_dta_v22_pr_c.py"),
    Path("scripts/ci/verify_dta_v22_pr_d.py"),
    Path("scripts/dta_v22/run_pr_d_provider_protocol.py"),
    Path("src/ecomsre/dta_v2/v22/__init__.py"),
    Path("src/ecomsre/dta_v2/v22/controller_contracts.py"),
    Path("src/ecomsre/dta_v2/v22/controller_inputs.py"),
    Path("src/ecomsre/dta_v2/v22/controller_modes.py"),
    Path("src/ecomsre/dta_v2/v22/controller_provider.py"),
    Path("src/ecomsre/dta_v2/v22/controller_runtime.py"),
    Path("src/ecomsre/dta_v2/v22/protocol_suite.py"),
    Path("tests/dta_v22/test_v22_controller_contracts.py"),
    Path("tests/dta_v22/test_v22_controller_modes.py"),
    Path("tests/dta_v22/test_v22_controller_provider.py"),
    Path("tests/dta_v22/test_v22_controller_runtime.py"),
    Path("tests/dta_v22/test_v22_pr_c_verifier.py"),
    Path("tests/dta_v22/test_v22_pr_d_provider_execution.py"),
    Path("tests/dta_v22/test_v22_pr_d_verifier.py"),
    Path("tests/dta_v22/test_v22_protocol_suite.py"),
    Path("tests/dta_v22/test_v22_provider_protocol_suite.py"),
    PROVIDER_V3_PREREGISTRATION,
    *PROVIDER_V3_REPLICATE_SUMMARIES,
    PROVIDER_V3_CAMPAIGN_SUMMARY,
)
EXPECTED_PR_D_PRE_EXECUTION_CHANGED_PATHS = tuple(
    relative
    for relative in EXPECTED_PR_D_CHANGED_PATHS
    if relative
    not in (*PROVIDER_V3_REPLICATE_SUMMARIES, PROVIDER_V3_CAMPAIGN_SUMMARY)
)
PERSISTENT_PR_D_ARTIFACTS = (
    PR_D_MANIFEST,
    PR_D_SUCCESSOR_ATTESTATION,
    PROVIDER_SUMMARY,
    Path("docs/human-briefs/2026-08-20-dta-v22-pr-d-controller-protocol.md"),
    Path("scripts/ci/verify_dta_v22_pr_d.py"),
    Path("scripts/dta_v22/run_pr_d_provider_protocol.py"),
    Path("src/ecomsre/dta_v2/v22/controller_contracts.py"),
    Path("src/ecomsre/dta_v2/v22/controller_inputs.py"),
    Path("src/ecomsre/dta_v2/v22/controller_modes.py"),
    Path("src/ecomsre/dta_v2/v22/controller_provider.py"),
    Path("src/ecomsre/dta_v2/v22/controller_runtime.py"),
    Path("src/ecomsre/dta_v2/v22/protocol_suite.py"),
    PROVIDER_V3_PREREGISTRATION,
    *PROVIDER_V3_REPLICATE_SUMMARIES,
    PROVIDER_V3_CAMPAIGN_SUMMARY,
)
EXPECTED_ARTIFACT_PATHS = (
    "docs/analysis/dta-v22-pr-d-provider-protocol-summary.json",
    "scripts/dta_v22/run_pr_d_provider_protocol.py",
    "src/ecomsre/dta_v2/v22/controller_contracts.py",
    "src/ecomsre/dta_v2/v22/controller_inputs.py",
    "src/ecomsre/dta_v2/v22/controller_modes.py",
    "src/ecomsre/dta_v2/v22/controller_provider.py",
    "src/ecomsre/dta_v2/v22/controller_runtime.py",
    "src/ecomsre/dta_v2/v22/protocol_suite.py",
    "tests/dta_v22/test_v22_controller_contracts.py",
    "tests/dta_v22/test_v22_controller_modes.py",
    "tests/dta_v22/test_v22_controller_provider.py",
    "tests/dta_v22/test_v22_controller_runtime.py",
    "tests/dta_v22/test_v22_pr_d_provider_execution.py",
    "tests/dta_v22/test_v22_protocol_suite.py",
    "tests/dta_v22/test_v22_provider_protocol_suite.py",
)
EXPECTED_ACTIVITY_FIELDS = (
    "provider_called",
    "docker_called",
    "held_out_executed",
    "scenario_executed",
    "fault_injected",
    "runbook_executed",
    "private_evidence_changed",
    "public_result_changed",
    "execution_report_rebound",
)
EXPECTED_PR_E_ACTIVITY = {
    "provider_called": False,
    "docker_called": True,
    "held_out_executed": False,
    "scenario_executed": True,
    "fault_injected": True,
    "runbook_executed": False,
    "private_evidence_changed": True,
    "public_result_changed": True,
    "execution_report_rebound": False,
}
EXPECTED_SUCCESSOR_ATTESTATION_FIELDS = (
    "schema_version",
    "goal_version",
    "decision_id",
    "repository",
    "source_stage",
    "source_pr",
    "source_candidate_head",
    "source_candidate_tree",
    "source_merge_commit",
    "source_merge_tree",
    "successor_stage",
    "successor_pr",
    "successor_branch",
    "base_main_head",
    "successor_head",
    "successor_tree",
    "changed_paths",
    "raw_sha256_by_path",
    *EXPECTED_ACTIVITY_FIELDS,
    "record_sha256",
)
_FORBIDDEN_PUBLIC_PATTERNS = (
    re.compile(r"/(?:Users|home)/[^\s\"']+"),
    re.compile(r"[A-Za-z]:\\[^\s\"']+"),
    re.compile(r"bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}", re.IGNORECASE),
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return value


def _regular_file(root: Path, relative: Path) -> Path:
    path = root / relative
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(f"PR-D artifact must be a regular file: {relative}")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError(f"PR-D artifact escapes repository: {relative}")
    return path


def _git_text(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_single_parent_commit(
    root: Path,
    commit: str,
    *,
    label: str,
) -> None:
    parents = _git_text(
        root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        commit,
    ).split()
    if len(parents) != 2:
        raise ValueError(f"{label} is not single-parent")


def _git_paths(root: Path, *args: str) -> set[Path]:
    return {Path(item) for item in _git_text(root, *args).splitlines() if item}


def _is_sha(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _assert_no_pr_d_public_leak(text: str) -> None:
    if any(pattern.search(text) is not None for pattern in _FORBIDDEN_PUBLIC_PATTERNS):
        raise ValueError("PR-D public leakage detected")


def _changed_text(root: Path, relative: Path) -> str:
    path = _regular_file(root, relative)
    if subprocess.run(
        ("git", "-C", str(root), "cat-file", "-e", f"{PR_D_BASE}:{relative}"),
        check=False,
        capture_output=True,
    ).returncode != 0:
        return path.read_text(encoding="utf-8")
    diff = _git_text(root, "diff", "--unified=0", PR_D_BASE, "--", relative.as_posix())
    return "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def _verify_closed_changed_surface(
    root: Path,
    *,
    expected_paths: tuple[Path, ...],
) -> None:
    observed = _git_paths(root, "diff", "--name-only", PR_D_BASE, "--")
    observed.update(_git_paths(root, "ls-files", "--others", "--exclude-standard"))
    expected = set(expected_paths)
    if observed != expected:
        raise ValueError(
            "PR-D changed surface differs: "
            f"undeclared={sorted(str(item) for item in observed - expected)}, "
            f"missing={sorted(str(item) for item in expected - observed)}"
        )


def _validate_stage_record(value: object, *, stage: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"stage", "pr", "head_sha", "merge_commit"}
        or value.get("stage") != stage
        or not isinstance(value.get("pr"), int)
        or not _is_sha(value.get("head_sha"), 40)
        or not _is_sha(value.get("merge_commit"), 40)
    ):
        raise ValueError(f"progress lacks exact {stage} merge provenance")
    return value


def _require_pr_d_progress(
    progress: dict[str, Any],
    *,
    expected_terminal: str,
) -> None:
    if (
        set(progress)
        != {
            "schema_version",
            "goal_version",
            "inspected_starting_main",
            "actual_starting_main",
            "completed_stage",
            "current_stage",
            "active_branch",
            "active_pr",
            "merged_prs",
            "primary_model",
            "provider_mode",
            "flat_identity_sha256",
            "planner_identity_sha256",
            "router_identity_sha256",
            "one_shot_identity_sha256",
            "development_report_sha256",
            "held_out_seal_sha256",
            "held_out_execution_id",
            "planner_claim",
            "memory_claim",
            "final_engineering_terminal",
        }
        or progress.get("schema_version") != "dta-v22-p0-master-progress.v1"
        or progress.get("goal_version") != "dta-v22-p0-master-v1"
        or progress.get("inspected_starting_main")
        != "9da92d54a4fb470c5452cee36a731e81529d05a5"
        or progress.get("actual_starting_main")
        != "9da92d54a4fb470c5452cee36a731e81529d05a5"
        or progress.get("current_stage") != "PR-D"
        or progress.get("completed_stage") != "PR-C"
        or progress.get("active_branch") != PR_D_BRANCH
        or progress.get("active_pr") != PR_D_PR
        or progress.get("primary_model") != PRIMARY_MODEL_V22
        or tuple(
            progress.get(field)
            for field in (
                "provider_mode",
                "flat_identity_sha256",
                "planner_identity_sha256",
                "router_identity_sha256",
                "one_shot_identity_sha256",
                "development_report_sha256",
                "held_out_seal_sha256",
                "held_out_execution_id",
                "planner_claim",
                "memory_claim",
            )
        )
        != (None,) * 10
        or progress.get("final_engineering_terminal") != expected_terminal
    ):
        raise ValueError("PR-D progress identity or terminal differs")
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or len(merged) != 3:
        raise ValueError("PR-D merged sequence differs")
    pr_c = _validate_stage_record(merged[2], stage="PR-C")
    if (
        pr_c["pr"] != 59
        or pr_c["head_sha"] != "de0f0b39bdd51e75925d75580401bab15a04ec66"
        or pr_c["merge_commit"] != PR_D_BASE
    ):
        raise ValueError("PR-D base provenance differs")


def _require_pr_d_successor_progress(root: Path, progress: dict[str, Any]) -> None:
    stages = ("PR-A", "PR-B", "PR-C", "PR-D", "PR-E", "PR-F")
    current = progress.get("current_stage")
    if current == "COMPLETE":
        current_index = len(stages)
        if progress.get("completed_stage") != "PR-F":
            raise ValueError("terminal successor progress is not complete")
    else:
        if current not in stages or stages.index(current) < 4:
            raise ValueError("successor current stage is not after PR-D")
        current_index = stages.index(current)
        if progress.get("completed_stage") != stages[current_index - 1]:
            raise ValueError("successor progress is not monotonic")
    merged = progress.get("merged_prs")
    if not isinstance(merged, list) or len(merged) != current_index:
        raise ValueError("successor merged PR sequence is not monotonic")
    pr_d = _validate_stage_record(merged[3], stage="PR-D")
    path = _regular_file(root, PR_D_SUCCESSOR_ATTESTATION)
    raw = path.read_text(encoding="utf-8")
    attestation = _load_json(path)
    if raw != json.dumps(attestation, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-D successor attestation is not canonical JSON")
    if tuple(attestation) != EXPECTED_SUCCESSOR_ATTESTATION_FIELDS:
        raise ValueError("PR-D successor attestation fields differ")
    payload = dict(attestation)
    record_sha = payload.pop("record_sha256")
    if (
        attestation.get("schema_version")
        != "dta-v22-pr-d-successor-attestation.v1"
        or attestation.get("goal_version") != "dta-v22-p0-master-v1"
        or attestation.get("decision_id") != "DEC-055"
        or attestation.get("repository") != "Raidriar7170/EcomSRE-Agent"
        or attestation.get("source_stage") != "PR-D"
        or attestation.get("source_pr") != pr_d["pr"]
        or attestation.get("source_candidate_head") != pr_d["head_sha"]
        or attestation.get("source_merge_commit") != pr_d["merge_commit"]
        or attestation.get("successor_stage") != "PR-E"
        or attestation.get("base_main_head") != pr_d["merge_commit"]
        or not isinstance(attestation.get("successor_pr"), int)
        or attestation["successor_pr"] <= pr_d["pr"]
        or attestation.get("successor_branch") != "codex/dta-v22-p0-pr-e-capture-freeze"
        or not _is_sha(attestation.get("source_candidate_tree"), 40)
        or not _is_sha(attestation.get("source_merge_tree"), 40)
        or not _is_sha(attestation.get("successor_head"), 40)
        or not _is_sha(attestation.get("successor_tree"), 40)
        or record_sha != semantic_sha256_v22(payload)
        or any(
            attestation.get(field) is not expected
            for field, expected in EXPECTED_PR_E_ACTIVITY.items()
        )
    ):
        raise ValueError("PR-D successor attestation differs")
    changed_paths = attestation.get("changed_paths")
    raw_hashes = attestation.get("raw_sha256_by_path")
    if (
        not isinstance(changed_paths, list)
        or not changed_paths
        or changed_paths != sorted(set(changed_paths))
        or any(not isinstance(item, str) for item in changed_paths)
        or any(
            Path(item).is_absolute()
            or ".." in Path(item).parts
            or Path(item).as_posix() != item
            or item == PR_D_SUCCESSOR_ATTESTATION.as_posix()
            for item in changed_paths
        )
        or not isinstance(raw_hashes, dict)
        or list(raw_hashes) != changed_paths
        or any(not _is_sha(value, 64) for value in raw_hashes.values())
    ):
        raise ValueError("PR-D successor exact changed path or raw hash set differs")
    _verify_successor_git_provenance(
        root=root,
        progress=progress,
        current=current,
        merged=merged,
        source=pr_d,
        attestation=attestation,
        attestation_raw=raw,
        changed_paths=changed_paths,
        raw_hashes=raw_hashes,
    )


def _verify_successor_git_provenance(
    *,
    root: Path,
    progress: dict[str, Any],
    current: object,
    merged: list[object],
    source: dict[str, Any],
    attestation: dict[str, Any],
    attestation_raw: str,
    changed_paths: list[str],
    raw_hashes: dict[str, str],
) -> None:
    candidate = source["head_sha"]
    merge = source["merge_commit"]
    source_ref = f"refs/remotes/dta-pr/{source['pr']}"
    successor_pr = attestation["successor_pr"]
    successor_ref = f"refs/remotes/dta-pr/{successor_pr}"
    try:
        if _git_text(root, "rev-parse", "--verify", source_ref) != candidate:
            raise ValueError("PR-D candidate head does not match pull ref")
        candidate_tree = _git_text(root, "rev-parse", f"{candidate}^{{tree}}")
        merge_tree = _git_text(root, "rev-parse", f"{merge}^{{tree}}")
        if (
            candidate_tree != attestation["source_candidate_tree"]
            or merge_tree != attestation["source_merge_tree"]
            or candidate_tree != merge_tree
            or candidate == merge
        ):
            raise ValueError("PR-D squash tree identity differs")
        _require_single_parent_commit(
            root,
            merge,
            label="PR-D squash merge",
        )
        ancestor = subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", candidate, merge),
            check=False,
            capture_output=True,
        )
        if ancestor.returncode != 1:
            raise ValueError("PR-D squash candidate ancestry differs")
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", merge, "HEAD"),
            check=True,
            capture_output=True,
        )
        subject = _git_text(root, "show", "-s", "--format=%s", merge)
        if not (
            subject.startswith("DTA v2.2 P0 PR-D:")
            and subject.endswith(f"(#{source['pr']})")
        ):
            raise ValueError("PR-D merge subject does not bind PR number")
        for commit in (candidate, merge):
            manifest = subprocess.run(
                ("git", "-C", str(root), "show", f"{commit}:{PR_D_MANIFEST}"),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(manifest).hexdigest() != EXPECTED_MANIFEST_SHA256:
                raise ValueError("PR-D Git tree does not bind frozen manifest")
        if current == "PR-E":
            if (
                progress.get("active_pr") != successor_pr
                or progress.get("active_branch") != attestation["successor_branch"]
            ):
                raise ValueError("active PR-E identity differs from attestation")
            successor_final_head = _git_text(root, "rev-parse", "HEAD")
        else:
            pr_e = _validate_stage_record(merged[4], stage="PR-E")
            if pr_e["pr"] != successor_pr:
                raise ValueError("successor progress lacks exact PR-E provenance")
            successor_final_head = pr_e["head_sha"]
        if _git_text(root, "rev-parse", "--verify", successor_ref) != successor_final_head:
            raise ValueError("PR-E final head does not match pull ref")
        _require_single_parent_commit(
            root,
            successor_final_head,
            label="PR-E final attestation commit",
        )
        successor_head = attestation["successor_head"]
        if _git_text(root, "rev-parse", f"{successor_final_head}^") != successor_head:
            raise ValueError("PR-E attestation commit is not final single-file child")
        if _git_text(root, "rev-parse", f"{successor_head}^{{tree}}") != attestation[
            "successor_tree"
        ]:
            raise ValueError("PR-E successor tree differs")
        subprocess.run(
            ("git", "-C", str(root), "merge-base", "--is-ancestor", merge, successor_head),
            check=True,
            capture_output=True,
        )
        changed = _git_text(
            root, "diff", "--name-status", "--no-renames", merge, successor_head, "--"
        )
        observed: list[str] = []
        for line in changed.splitlines():
            status, relative = line.split("\t", 1)
            if status not in {"A", "M"}:
                raise ValueError("PR-E successor changed path kind differs")
            observed.append(relative)
        if sorted(observed) != changed_paths:
            raise ValueError("PR-E successor exact changed path set differs")
        for relative in changed_paths:
            blob = subprocess.run(
                ("git", "-C", str(root), "show", f"{successor_head}:{relative}"),
                check=True,
                capture_output=True,
            ).stdout
            if hashlib.sha256(blob).hexdigest() != raw_hashes[relative]:
                raise ValueError(f"PR-E successor raw SHA-256 differs: {relative}")
        delta = _git_text(
            root,
            "diff",
            "--name-status",
            "--no-renames",
            successor_head,
            successor_final_head,
            "--",
        )
        if delta != f"A\t{PR_D_SUCCESSOR_ATTESTATION}":
            raise ValueError("PR-E attestation commit changed more than its record")
        committed = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "show",
                f"{successor_final_head}:{PR_D_SUCCESSOR_ATTESTATION}",
            ),
            check=True,
            capture_output=True,
        ).stdout
        if committed != attestation_raw.encode("utf-8"):
            raise ValueError("PR-E attestation record differs from committed bytes")
    except subprocess.CalledProcessError as error:
        raise ValueError("PR-D Git provenance is unavailable") from error


def _public_scan_plan(
    root: Path,
    progress: dict[str, Any],
) -> tuple[str, tuple[Path, ...]]:
    if progress.get("current_stage") == "PR-D":
        terminal = progress.get("final_engineering_terminal")
        if terminal == EXECUTION_READY_PR_D_TERMINAL:
            _require_pr_d_progress(
                progress,
                expected_terminal=EXECUTION_READY_PR_D_TERMINAL,
            )
            return (
                "PR_D_V3_EXECUTION_READY_SURFACE",
                EXPECTED_PR_D_PRE_EXECUTION_CHANGED_PATHS,
            )
        if terminal == BLOCKED_PR_D_TERMINAL:
            _require_pr_d_progress(
                progress,
                expected_terminal=BLOCKED_PR_D_TERMINAL,
            )
            return "PR_D_V3_POST_EXECUTION_SURFACE", EXPECTED_PR_D_CHANGED_PATHS
        raise ValueError("PR-D pre/post-execution terminal differs")
    _require_pr_d_successor_progress(root, progress)
    return "SUCCESSOR_PERSISTENT_ARTIFACTS", PERSISTENT_PR_D_ARTIFACTS


def _expected_blocked_attempts() -> tuple[dict[str, Any], ...]:
    common = {
        "schema_version": "dta-v22-pr-d-provider-protocol-invalid-attempt.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "model": PRIMARY_MODEL_V22,
        "eligible_as_pr_d_gate_evidence": False,
        "preservation_rule": "PRESERVE_ATTEMPT_DO_NOT_RELABEL_OR_DELETE",
    }
    return (
        {
            **common,
            "attempt_ordinal": 1,
            "disposition": "INVALID_NON_AUTHORITATIVE_PRIVATE_LOCATION",
            "invalid_reason": "PRIVATE_EVIDENCE_ROOT_MISMATCH",
            "implementation_commit": "b60f164df1409422110e7a72cff682ac59cf66f0",
            "implementation_tree": "5715e24e52c27dc25cae0100164b407213ac2994",
            "executed_at": "2026-08-19T19:57:02.898435+00:00",
            "provider_protocol_report_sha256": (
                "088b9febb807af46fd0708cd1a0a6cdb2ed7b943a1f44ec615173a53302bbdb8"
            ),
            "original_public_summary_sha256": (
                "8911b46de9c0cbd2c063e5af94ca42c153c92d9de4888bf5da0220b573416bf1"
            ),
            "transition_count": 50,
            "first_pass_accepted_count": 48,
            "post_correction_accepted_count": 50,
            "invalid_dispatches": 0,
            "provider_protocol_calls": 52,
            "raw_provider_content_published": False,
        },
        {
            **common,
            "attempt_ordinal": 2,
            "disposition": "INVALID_LOCAL_VALIDATOR_ABORT",
            "invalid_reason": (
                "NON_CORRECTION_PROTOCOL_FAILURE_REJECTED_AS_REPORT_SHAPE_ERROR"
            ),
            "implementation_commit": "60c096d178b2e01ade880f694237a3154177afef",
            "implementation_tree": "6a87f4013de935d3898246e0d8c69545914238ad",
            "failure_recorded_at": "2026-08-19T21:19:16Z",
            "provider_call_count_exact": None,
            "provider_call_count_lower_bound": 2,
            "private_report_created": False,
            "public_summary_created": False,
            "agent_read_dispatches_executed": 0,
            "agent_write_calls": 0,
            "runbook_executions": 0,
            "docker_calls": 0,
        },
        {
            **common,
            "attempt_ordinal": 3,
            "disposition": "INVALID_PROVIDER_RATE_LIMIT_ABORT",
            "invalid_reason": "PROVIDER_HTTP_429",
            "implementation_commit": "085433a19026eca89cfb801fc77d650f7a3ff046",
            "implementation_tree": "de545d8d766ee4974ea6adb6d89c178f16fa4913",
            "failure_recorded_at": "2026-08-19T21:22:34Z",
            "provider_call_count_exact": None,
            "provider_call_count_lower_bound": 2,
            "http_retry_count": 0,
            "private_report_created": False,
            "public_summary_created": False,
            "agent_read_dispatches_executed": 0,
            "agent_write_calls": 0,
            "runbook_executions": 0,
            "docker_calls": 0,
        },
        {
            **common,
            "attempt_ordinal": 4,
            "disposition": "INVALID_PROVIDER_RATE_LIMIT_ABORT",
            "invalid_reason": "PROVIDER_HTTP_429",
            "implementation_commit": "a5a9a12755679130e6bb0e2c3c26b1f9c59f7a59",
            "implementation_tree": "d632be8cf82450b95ec13f37dc7e40641a63fdef",
            "failure_recorded_at": "2026-08-19T21:24:56Z",
            "provider_call_count_exact": None,
            "provider_call_count_lower_bound": 2,
            "minimum_request_interval_seconds": 1.5,
            "http_retry_count": 0,
            "private_report_created": False,
            "public_summary_created": False,
            "agent_read_dispatches_executed": 0,
            "agent_write_calls": 0,
            "runbook_executions": 0,
            "docker_calls": 0,
        },
        {
            **common,
            "attempt_ordinal": 5,
            "disposition": "BLOCKED_PROVIDER_PROTOCOL_GATE",
            "blocker": BLOCKED_PR_D_TERMINAL,
            "implementation_commit": "984c7f1296259c27dbe1d1b7f8b74ba37b01c17a",
            "implementation_tree": "ba3f99d6205ab58693cba45bae5070e74c3e8679",
            "failure_recorded_at": "2026-08-19T21:29:07Z",
            "transition_count": 50,
            "provider_protocol_calls": 50,
            "provider_probe_calls": None,
            "first_pass_accepted_count": None,
            "post_correction_accepted_count": None,
            "invalid_dispatches": 0,
            "provider_gate_eligible": False,
            "exact_aggregate_counts_persisted": False,
            "aggregate_loss_reason": "RUNNER_ABORTED_BEFORE_NEGATIVE_REPORT_WRITE",
            "private_report_created": False,
            "public_summary_created": False,
            "agent_read_dispatches_executed": 0,
            "agent_write_calls": 0,
            "runbook_executions": 0,
            "docker_calls": 0,
        },
    )


def _require_absent(root: Path, relative: Path) -> None:
    path = root / relative
    if path.exists() or path.is_symlink():
        raise ValueError(f"blocked PR-D must not publish pass artifact: {relative}")


def verify_blocked_provider_attempts(
    root: Path,
    *,
    attempt_paths: Sequence[Path] | None = None,
) -> tuple[dict[str, Any], ...]:
    _require_absent(root, PROVIDER_SUMMARY)
    _require_absent(root, PR_D_MANIFEST)
    expected = _expected_blocked_attempts()
    if attempt_paths is None:
        paths = tuple(_regular_file(root, relative) for relative in PROVIDER_ATTEMPT_PATHS)
    else:
        paths = tuple(attempt_paths)
    if len(paths) != len(expected):
        raise ValueError("PR-D blocked attempt path count differs")
    observed: list[dict[str, Any]] = []
    for ordinal, (path, exact) in enumerate(zip(paths, expected, strict=True), start=1):
        raw = path.read_text(encoding="utf-8")
        attempt = _load_json(path)
        if raw != json.dumps(attempt, indent=2, ensure_ascii=False) + "\n":
            raise ValueError(f"PR-D attempt {ordinal} is not canonical JSON")
        if attempt != exact:
            raise ValueError(f"PR-D attempt {ordinal} contract differs")
        _assert_no_pr_d_public_leak(raw)
        commit = attempt["implementation_commit"]
        tree = attempt["implementation_tree"]
        try:
            if _git_text(root, "rev-parse", f"{commit}^{{tree}}") != tree:
                raise ValueError(f"PR-D attempt {ordinal} implementation tree differs")
            subprocess.run(
                ("git", "-C", str(root), "merge-base", "--is-ancestor", commit, "HEAD"),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as error:
            raise ValueError(
                f"PR-D attempt {ordinal} implementation provenance is unavailable"
            ) from error
        observed.append(attempt)
    return tuple(observed)


def verify_provider_v3_preregistration(root: Path) -> dict[str, Any]:
    path = _regular_file(root, PROVIDER_V3_PREREGISTRATION)
    raw = path.read_text(encoding="utf-8")
    preregistration = _load_json(path)
    if raw != json.dumps(preregistration, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-D Provider v3 preregistration is not canonical JSON")
    payload = dict(preregistration)
    preregistration_sha256 = payload.pop("preregistration_sha256", None)
    expected_attempts = {
        relative.as_posix(): raw_sha
        for relative, raw_sha in zip(
            PROVIDER_ATTEMPT_PATHS,
            EXPECTED_PROVIDER_ATTEMPT_RAW_SHA256S,
            strict=True,
        )
    }
    expected_public = {
        "replicate_a": PROVIDER_V3_REPLICATE_SUMMARIES[0].as_posix(),
        "replicate_b": PROVIDER_V3_REPLICATE_SUMMARIES[1].as_posix(),
        "campaign": PROVIDER_V3_CAMPAIGN_SUMMARY.as_posix(),
    }
    if (
        preregistration_sha256 != semantic_sha256_v22(payload)
        or preregistration.get("schema_version")
        != "dta-v22-pr-d-provider-protocol-v3-preregistration.v1"
        or preregistration.get("goal_version") != "dta-v22-p0-master-v1"
        or preregistration.get("amendment_version")
        != "dta-v22-pr-d-provider-protocol-replicated-gate-v1"
        or preregistration.get("decision_id") != "DEC-057"
        or preregistration.get("stage") != "PR-D"
        or preregistration.get("pr") != PR_D_PR
        or preregistration.get("branch") != PR_D_BRANCH
        or preregistration.get("base_main") != PR_D_BASE
        or preregistration.get("model") != PRIMARY_MODEL_V22
        or preregistration.get("temperature") != 0
        or preregistration.get("protocol_report_schema")
        != "dta-v22.provider-protocol-capability-report.v3"
        or preregistration.get("replicate_ids") != ["A", "B"]
        or preregistration.get("replicate_count") != 2
        or preregistration.get("transition_count_per_replicate") != 52
        or preregistration.get("ordinary_transition_count_per_replicate") != 48
        or preregistration.get("ordinary_transition_count_by_arm")
        != {"FLAT_CANONICAL": 24, "PLANNER_LITE": 24}
        or preregistration.get("correction_transition_count_per_replicate") != 4
        or preregistration.get("correction_transition_count_by_arm")
        != {"FLAT_CANONICAL": 2, "PLANNER_LITE": 2}
        or preregistration.get("correction_error_classes")
        != {
            "INVALID_REF_CORRECTION": ["FLAT_CANONICAL", "PLANNER_LITE"],
            "STALE_ACTION_CORRECTION": ["FLAT_CANONICAL", "PLANNER_LITE"],
        }
        or preregistration.get("ordinary_first_pass_gate")
        != {
            "overall_minimum": 46,
            "overall_denominator": 48,
            "per_arm_minimum": 23,
            "per_arm_denominator": 24,
        }
        or preregistration.get("correction_gate")
        != {"overall_required": 4, "per_arm_required": 2}
        or preregistration.get("final_gate")
        != {"minimum": 51, "denominator": 52, "invalid_dispatches": 0}
        or preregistration.get("minimum_request_start_interval_seconds") != 4.0
        or preregistration.get("inter_replicate_cooldown_seconds") != 60.0
        or preregistration.get("http_auto_retry_count") != 0
        or preregistration.get("third_replicate_allowed") is not False
        or preregistration.get("run_b_after_a_semantic_failure") is not True
        or preregistration.get("private_evidence_location_class")
        != "DTA_V22_PRIVATE_ROOT_PR_D_PROVIDER_PROTOCOL_V3"
        or preregistration.get("public_summary_paths") != expected_public
        or preregistration.get("historical_attempt_raw_sha256_by_path")
        != expected_attempts
        or preregistration.get("protected_activity")
        != {
            "agent_evidence_dispatches": 0,
            "agent_writes": 0,
            "docker_calls": 0,
            "fault_injections": 0,
            "held_out_executions": 0,
            "runbook_executions": 0,
            "scenario_executions": 0,
        }
    ):
        raise ValueError("PR-D Provider v3 preregistration contract differs")
    frozen = preregistration.get("frozen_raw_sha256_by_path")
    if not isinstance(frozen, dict) or tuple(frozen) != tuple(
        item.as_posix() for item in EXPECTED_V3_FROZEN_PATHS
    ):
        raise ValueError("PR-D Provider v3 frozen path surface differs")
    for relative in EXPECTED_V3_FROZEN_PATHS:
        observed = hashlib.sha256(_regular_file(root, relative).read_bytes()).hexdigest()
        if frozen.get(relative.as_posix()) != observed:
            raise ValueError(f"PR-D Provider v3 frozen raw SHA-256 differs: {relative}")
    for relative, expected_sha in zip(
        PROVIDER_ATTEMPT_PATHS,
        EXPECTED_PROVIDER_ATTEMPT_RAW_SHA256S,
        strict=True,
    ):
        if hashlib.sha256(_regular_file(root, relative).read_bytes()).hexdigest() != expected_sha:
            raise ValueError(f"PR-D historical attempt bytes changed: {relative}")
    _assert_no_pr_d_public_leak(raw)
    return preregistration


def _verify_provider_v3_public_json(
    root: Path,
    *,
    relative: Path,
    raw_sha256: str,
    semantic_field: str,
    semantic_sha256: str,
) -> dict[str, Any]:
    path = _regular_file(root, relative)
    raw_bytes = path.read_bytes()
    raw = raw_bytes.decode("utf-8")
    value = _load_json(path)
    if (
        hashlib.sha256(raw_bytes).hexdigest() != raw_sha256
        or raw != json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        or value.get(semantic_field) != semantic_sha256
        or semantic_sha256_v22(
            {key: item for key, item in value.items() if key != semantic_field}
        )
        != semantic_sha256
    ):
        raise ValueError(f"PR-D Provider v3 public result differs: {relative}")
    _assert_no_pr_d_public_leak(raw)
    return value


def verify_provider_v3_campaign_results(root: Path) -> dict[str, Any]:
    preregistration = verify_provider_v3_preregistration(root)
    if (
        preregistration.get("preregistration_sha256")
        != EXPECTED_V3_PREREGISTRATION_SHA256
    ):
        raise ValueError("PR-D Provider v3 result preregistration binding differs")
    summaries = tuple(
        _verify_provider_v3_public_json(
            root,
            relative=relative,
            raw_sha256=raw_sha256,
            semantic_field="summary_sha256",
            semantic_sha256=semantic_sha256,
        )
        for relative, raw_sha256, semantic_sha256 in zip(
            PROVIDER_V3_REPLICATE_SUMMARIES,
            EXPECTED_V3_REPLICATE_RAW_SHA256S,
            EXPECTED_V3_REPLICATE_SEMANTIC_SHA256S,
            strict=True,
        )
    )
    expected_taxonomies = (
        {
            "PARSE_SHAPE_REJECTED": 6,
            "RUNTIME_PROTOCOL_REJECTED": 0,
            "SEMANTIC_CATEGORY_MISMATCH": 5,
            "CORRECTION_NOT_RECOVERED": 0,
            "PROVIDER_TRANSPORT_ABORT": 35,
            "PROVIDER_PROBE_FAILED": 0,
        },
        {
            "PARSE_SHAPE_REJECTED": 1,
            "RUNTIME_PROTOCOL_REJECTED": 0,
            "SEMANTIC_CATEGORY_MISMATCH": 0,
            "CORRECTION_NOT_RECOVERED": 0,
            "PROVIDER_TRANSPORT_ABORT": 50,
            "PROVIDER_PROBE_FAILED": 0,
        },
    )
    expected_ordinary = (6, 1)
    expected_final = (6, 1)
    expected_calls = (18, 3)
    for index, summary in enumerate(summaries):
        replicate_id = ("A", "B")[index]
        ordinary_by_arm = summary.get("ordinary_first_pass_by_arm")
        correction_by_arm = summary.get("correction_acceptance_by_arm")
        correction_by_error = summary.get("correction_acceptance_by_error_class")
        if (
            summary.get("schema_version")
            != "dta-v22-pr-d-provider-protocol-v3-replicate-summary.v1"
            or summary.get("goal_version") != "dta-v22-p0-master-v1"
            or summary.get("amendment_version")
            != "dta-v22-pr-d-provider-protocol-replicated-gate-v1"
            or summary.get("replicate_id") != replicate_id
            or summary.get("implementation_commit")
            != EXPECTED_V3_IMPLEMENTATION_COMMIT
            or summary.get("implementation_tree") != EXPECTED_V3_IMPLEMENTATION_TREE
            or summary.get("preregistration_sha256")
            != EXPECTED_V3_PREREGISTRATION_SHA256
            or summary.get("model") != PRIMARY_MODEL_V22
            or summary.get("temperature") != 0
            or summary.get("selected_mode") != "STRICT_STRUCTURED_OUTPUT"
            or summary.get("provider_probe_report_sha256")
            != EXPECTED_V3_PROBE_REPORT_SHA256
            or summary.get("provider_probe_evidence_sha256")
            != EXPECTED_V3_PROBE_EVIDENCE_SHA256
            or summary.get("controller_schema_sha256")
            != EXPECTED_V3_CONTROLLER_SCHEMA_SHA256
            or tuple(summary.get("controller_identity_sha256s", ()))
            != EXPECTED_V3_CONTROLLER_IDENTITY_SHA256S
            or tuple(summary.get("controller_prompt_sha256s", ()))
            != EXPECTED_V3_CONTROLLER_PROMPT_SHA256S
            or summary.get("outcome_sha256") != EXPECTED_V3_OUTCOME_SHA256S[index]
            or summary.get("transition_count") != 52
            or summary.get("ordinary_transition_count") != 48
            or summary.get("ordinary_first_pass_accepted_count")
            != expected_ordinary[index]
            or summary.get("ordinary_first_pass_protocol_acceptance")
            != expected_ordinary[index] / 48
            or not isinstance(ordinary_by_arm, dict)
            or {
                arm: cell.get("transition_count")
                for arm, cell in ordinary_by_arm.items()
                if isinstance(cell, dict)
            }
            != {"FLAT_CANONICAL": 24, "PLANNER_LITE": 24}
            or sum(
                cell.get("accepted_count", -1)
                for cell in ordinary_by_arm.values()
                if isinstance(cell, dict)
            )
            != expected_ordinary[index]
            or summary.get("correction_transition_count") != 4
            or summary.get("correction_envelope_accepted_count") != 0
            or summary.get("correction_envelope_acceptance") != 0.0
            or not isinstance(correction_by_arm, dict)
            or {
                arm: cell.get("transition_count")
                for arm, cell in correction_by_arm.items()
                if isinstance(cell, dict)
            }
            != {"FLAT_CANONICAL": 2, "PLANNER_LITE": 2}
            or not isinstance(correction_by_error, dict)
            or {
                error: cell.get("transition_count")
                for error, cell in correction_by_error.items()
                if isinstance(cell, dict)
            }
            != {"STALE_ACTION_CORRECTION": 2, "INVALID_REF_CORRECTION": 2}
            or summary.get("final_accepted_count") != expected_final[index]
            or summary.get("final_protocol_acceptance") != expected_final[index] / 52
            or summary.get("provider_calls") != expected_calls[index]
            or summary.get("failure_taxonomy") != expected_taxonomies[index]
            or sum(expected_taxonomies[index].values()) + expected_final[index] != 52
            or summary.get("invalid_dispatches") != 0
            or summary.get("http_auto_retry_count") != 0
            or summary.get("provider_gate_eligible") is not False
            or summary.get("terminal") != BLOCKED_PR_D_TERMINAL
            or summary.get("private_evidence_location_class")
            != "DTA_V22_PRIVATE_ROOT"
            or summary.get("raw_provider_content_published") is not False
            or summary.get("private_paths_published") is not False
            or summary.get("total_tokens")
            != summary.get("input_tokens", -1) + summary.get("output_tokens", -2)
            or any(
                summary.get(field) != 0
                for field in (
                    "agent_read_dispatches_executed",
                    "agent_write_calls",
                    "runbook_executions",
                    "docker_calls",
                    "held_out_executions",
                    "scenario_executions",
                    "fault_injections",
                )
            )
            or not _is_sha(summary.get("private_evidence_raw_sha256"), 64)
            or not _is_sha(summary.get("private_evidence_semantic_sha256"), 64)
        ):
            raise ValueError(f"PR-D Provider v3 replicate {replicate_id} differs")

    campaign = _verify_provider_v3_public_json(
        root,
        relative=PROVIDER_V3_CAMPAIGN_SUMMARY,
        raw_sha256=EXPECTED_V3_CAMPAIGN_RAW_SHA256,
        semantic_field="campaign_sha256",
        semantic_sha256=EXPECTED_V3_CAMPAIGN_SEMANTIC_SHA256,
    )
    bindings = campaign.get("replicate_bindings")
    expected_aggregate = {
        failure: sum(taxonomy[failure] for taxonomy in expected_taxonomies)
        for failure in expected_taxonomies[0]
    }
    if (
        campaign.get("schema_version")
        != "dta-v22-pr-d-provider-protocol-v3-campaign-summary.v1"
        or campaign.get("goal_version") != "dta-v22-p0-master-v1"
        or campaign.get("amendment_version")
        != "dta-v22-pr-d-provider-protocol-replicated-gate-v1"
        or campaign.get("implementation_commit") != EXPECTED_V3_IMPLEMENTATION_COMMIT
        or campaign.get("implementation_tree") != EXPECTED_V3_IMPLEMENTATION_TREE
        or campaign.get("preregistration_sha256")
        != EXPECTED_V3_PREREGISTRATION_SHA256
        or campaign.get("probe_evidence_sha256")
        != EXPECTED_V3_PROBE_EVIDENCE_SHA256
        or campaign.get("replicate_ids") != ["A", "B"]
        or campaign.get("replicate_outcome_sha256s")
        != list(EXPECTED_V3_OUTCOME_SHA256S)
        or campaign.get("replicate_terminals")
        != [BLOCKED_PR_D_TERMINAL, BLOCKED_PR_D_TERMINAL]
        or campaign.get("failure_taxonomy_by_replicate")
        != {"A": expected_taxonomies[0], "B": expected_taxonomies[1]}
        or campaign.get("aggregate_failure_taxonomy") != expected_aggregate
        or not isinstance(bindings, list)
        or len(bindings) != 2
        or any(not isinstance(binding, dict) for binding in bindings)
        or any(
            binding.get("replicate_id") != ("A", "B")[index]
            or binding.get("private_raw_sha256")
            != summaries[index].get("private_evidence_raw_sha256")
            or binding.get("private_semantic_sha256")
            != summaries[index].get("private_evidence_semantic_sha256")
            or binding.get("public_raw_sha256")
            != EXPECTED_V3_REPLICATE_RAW_SHA256S[index]
            or binding.get("public_semantic_sha256")
            != EXPECTED_V3_REPLICATE_SEMANTIC_SHA256S[index]
            or binding.get("verified") is not True
            for index, binding in enumerate(bindings)
            if isinstance(binding, dict)
        )
        or campaign.get("both_replicates_independently_passed") is not False
        or campaign.get("implementation_and_controller_bindings_equal") is not False
        or campaign.get("provider_probe_calls") != 1
        or campaign.get("replicate_provider_calls") != [18, 3]
        or campaign.get("expected_provider_calls") != 22
        or campaign.get("observed_provider_calls") != 22
        or campaign.get("undeclared_provider_calls") != 0
        or campaign.get("provider_call_accounting_exact") is not True
        or campaign.get("invalid_dispatches") != 0
        or campaign.get("http_auto_retry_count") != 0
        or campaign.get("campaign_gate_eligible") is not False
        or campaign.get("terminal") != BLOCKED_PR_D_TERMINAL
        or campaign.get("private_evidence_location_class")
        != "DTA_V22_PRIVATE_ROOT"
        or not _is_sha(campaign.get("private_evidence_raw_sha256"), 64)
        or not _is_sha(campaign.get("private_evidence_semantic_sha256"), 64)
        or any(
            campaign.get(field) != 0
            for field in (
                "agent_read_dispatches_executed",
                "agent_write_calls",
                "runbook_executions",
                "docker_calls",
                "held_out_executions",
                "scenario_executions",
                "fault_injections",
            )
        )
    ):
        raise ValueError("PR-D Provider v3 campaign result differs")
    try:
        if (
            _git_text(
                root,
                "rev-parse",
                f"{EXPECTED_V3_IMPLEMENTATION_COMMIT}^{{tree}}",
            )
            != EXPECTED_V3_IMPLEMENTATION_TREE
        ):
            raise ValueError("PR-D Provider v3 implementation tree differs")
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                EXPECTED_V3_IMPLEMENTATION_COMMIT,
                "HEAD",
            ),
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("PR-D Provider v3 implementation provenance differs") from error
    return {
        **campaign,
        "replicate_summary_sha256s": list(
            EXPECTED_V3_REPLICATE_SEMANTIC_SHA256S
        ),
    }


def verify_provider_summary(
    root: Path,
    *,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    path = summary_path or _regular_file(root, PROVIDER_SUMMARY)
    raw = path.read_text(encoding="utf-8")
    summary = _load_json(path)
    if raw != json.dumps(summary, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-D Provider summary is not canonical JSON")
    payload = dict(summary)
    summary_sha = payload.pop("summary_sha256", None)
    if summary_sha != EXPECTED_SUMMARY_SHA256 or summary_sha != semantic_sha256_v22(
        payload
    ):
        raise ValueError("PR-D Provider summary digest differs")
    expected_categories = {
        "BUDGET_EXHAUSTION": 6,
        "EMPTY_SOURCE": 5,
        "INVALID_REF_CORRECTION": 1,
        "STALE_ACTION_CORRECTION": 1,
        "UNAVAILABLE_SOURCE": 5,
        "VALID_ABSTAIN": 8,
        "VALID_COMMIT": 8,
        "VALID_NO_INCIDENT": 8,
        "VALID_READ": 8,
    }
    if (
        summary.get("schema_version")
        != "dta-v22-pr-d-provider-protocol-summary.v1"
        or summary.get("goal_version") != "dta-v22-p0-master-v1"
        or summary.get("implementation_commit") != EXPECTED_IMPLEMENTATION_COMMIT
        or summary.get("implementation_tree") != EXPECTED_IMPLEMENTATION_TREE
        or summary.get("model") != PRIMARY_MODEL_V22
        or summary.get("selected_mode") != "STRICT_STRUCTURED_OUTPUT"
        or summary.get("controller_schema_sha256")
        != semantic_sha256_v22(_controller_schema_v22())
        or summary.get("provider_protocol_report_sha256") != EXPECTED_REPORT_SHA256
        or tuple(summary.get("controller_identity_sha256s", ()))
        != EXPECTED_IDENTITY_SHA256S
        or summary.get("transition_count") != 50
        or summary.get("transition_category_counts") != expected_categories
        or summary.get("controller_arm_counts")
        != {"FLAT_CANONICAL": 25, "PLANNER_LITE": 25}
        or summary.get("first_pass_accepted_count") != 48
        or summary.get("first_pass_protocol_acceptance") != 0.96
        or summary.get("post_correction_accepted_count") != 50
        or summary.get("post_correction_protocol_acceptance") != 1.0
        or summary.get("correction_count") != 2
        or summary.get("correction_rate") != 0.04
        or summary.get("invalid_dispatches") != 0
        or summary.get("provider_probe_calls") != 1
        or summary.get("provider_protocol_calls") != 52
        or summary.get("total_tokens")
        != summary.get("input_tokens", -1) + summary.get("output_tokens", -2)
        or summary.get("provider_gate_eligible") is not True
        or summary.get("terminal") != "PROVIDER_PROTOCOL_GATE_PASS"
        or summary.get("raw_provider_content_published") is not False
        or any(
            summary.get(field) != 0
            for field in (
                "agent_read_dispatches_executed",
                "agent_write_calls",
                "runbook_executions",
                "docker_calls",
            )
        )
        or not _is_sha(summary.get("response_digest_set_sha256"), 64)
    ):
        raise ValueError("PR-D Provider summary contract differs")
    if (
        _git_text(root, "rev-parse", f"{EXPECTED_IMPLEMENTATION_COMMIT}^{{tree}}")
        != EXPECTED_IMPLEMENTATION_TREE
    ):
        raise ValueError("PR-D Provider implementation tree differs")
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            EXPECTED_IMPLEMENTATION_COMMIT,
            "HEAD",
        ),
        check=True,
        capture_output=True,
    )
    _assert_no_pr_d_public_leak(raw)
    return summary


def verify_pr_d_bindings(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    path = manifest_path or (root / PR_D_MANIFEST)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_MANIFEST_SHA256:
        raise ValueError("PR-D manifest raw SHA-256 differs")
    manifest = _load_json(path)
    if raw.decode("utf-8") != json.dumps(manifest, indent=2, ensure_ascii=False) + "\n":
        raise ValueError("PR-D manifest is not canonical JSON")
    if (
        manifest.get("schema_version") != "dta-v22-pr-d-controller-bindings.v1"
        or manifest.get("goal_version") != "dta-v22-p0-master-v1"
        or manifest.get("stage") != "PR-D"
        or manifest.get("base_main") != PR_D_BASE
        or manifest.get("terminal") != PR_D_TERMINAL
    ):
        raise ValueError("PR-D manifest identity differs")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or tuple(
        item.get("path") if isinstance(item, dict) else None for item in artifacts
    ) != EXPECTED_ARTIFACT_PATHS:
        raise ValueError("PR-D artifact surface differs")
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256"}
            or not _is_sha(artifact.get("sha256"), 64)
        ):
            raise ValueError("PR-D artifact binding differs")
        source = _regular_file(root, Path(artifact["path"]))
        if hashlib.sha256(source.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError(f"PR-D artifact raw SHA-256 differs: {artifact['path']}")
    provider_gate = manifest.get("provider_protocol_gate")
    if not isinstance(provider_gate, dict) or (
        provider_gate.get("public_summary_sha256") != EXPECTED_SUMMARY_SHA256
        or provider_gate.get("private_report_sha256") != EXPECTED_REPORT_SHA256
        or provider_gate.get("model") != PRIMARY_MODEL_V22
        or provider_gate.get("selected_mode") != "STRICT_STRUCTURED_OUTPUT"
        or provider_gate.get("transition_count") != 50
        or provider_gate.get("first_pass_protocol_acceptance") != 0.96
        or provider_gate.get("post_correction_protocol_acceptance") != 1.0
        or provider_gate.get("invalid_dispatches") != 0
        or provider_gate.get("provider_protocol_calls") != 52
        or provider_gate.get("terminal") != "PROVIDER_PROTOCOL_GATE_PASS"
    ):
        raise ValueError("PR-D Provider gate binding differs")
    identities = manifest.get("controller_identities")
    if not isinstance(identities, dict) or tuple(identities.values()) != (
        "STRICT_STRUCTURED_OUTPUT",
        *EXPECTED_IDENTITY_SHA256S,
    ):
        raise ValueError("PR-D controller identity binding differs")
    correction = manifest.get("bounded_correction")
    if correction != {
        "maximum_per_run": 1,
        "consumes_provider_turn_and_tokens": True,
        "read_dispatches": 0,
        "write_authority": 0,
        "second_invalid_terminal": "FAILED",
    }:
        raise ValueError("PR-D correction contract differs")
    safety = manifest.get("safety_activity")
    if safety != {
        "provider_called": True,
        "private_evidence_changed": True,
        "public_result_changed": True,
        "agent_read_dispatches_executed": 0,
        "agent_write_calls": 0,
        "docker_called": False,
        "held_out_executed": False,
        "scenario_executed": False,
        "fault_injected": False,
        "runbook_executed": False,
    }:
        raise ValueError("PR-D safety activity differs")
    successor = manifest.get("successor_attestation_contract")
    if not isinstance(successor, dict) or (
        successor.get("path") != PR_D_SUCCESSOR_ATTESTATION.as_posix()
        or successor.get("schema_version")
        != "dta-v22-pr-d-successor-attestation.v1"
        or successor.get("decision_id") != "DEC-055"
        or successor.get("source_stage") != "PR-D"
        or successor.get("successor_stage") != "PR-E"
        or successor.get("successor_branch") != "codex/dta-v22-p0-pr-e-capture-freeze"
        or successor.get("required_fields")
        != list(EXPECTED_SUCCESSOR_ATTESTATION_FIELDS)
        or successor.get("activity_expectations") != EXPECTED_PR_E_ACTIVITY
    ):
        raise ValueError("PR-D successor attestation contract differs")
    return manifest


def _verify_runtime_contracts() -> None:
    if tuple(ControllerDecisionV22.model_fields) != (
        "decision",
        "working_hypothesis_id",
        "action_id",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
    ):
        raise ValueError("shared ControllerDecision schema differs")
    if tuple(PlanCorrectionV22.model_fields) != (
        "schema_version",
        "safe_error_code",
        "current_valid_action_ids",
        "remaining_evidence_budget",
        "read_dispatches",
        "write_authority",
        "correction_sha256",
    ):
        raise ValueError("bounded correction schema differs")
    if PRIMARY_MODEL_V22 != "gpt-5.4-mini-2026-03-17":
        raise ValueError("PR-D model continuity differs")
    probe = probe_provider_output_mode_v22(
        probe=lambda _model, _mode, _schema: ProviderProbeStatusV22.SUPPORTED
    )
    identities = build_controller_identity_manifests_v22(provider_probe=probe)
    if (
        probe.selected_mode is not ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
        or probe.provider_calls != 1
        or probe.model != PRIMARY_MODEL_V22
        or probe.controller_schema_sha256
        != semantic_sha256_v22(_controller_schema_v22())
        or tuple(item.arm for item in identities) != tuple(EvaluationArmV22)
        or len({item.identity_sha256 for item in identities}) != len(identities)
        or any(item.model != PRIMARY_MODEL_V22 for item in identities)
        or any(
            item.provider_probe.report_sha256 != probe.report_sha256
            for item in identities
        )
        or sum(item.receives_persistent_belief_ledger for item in identities) != 1
        or next(
            item
            for item in identities
            if item.receives_persistent_belief_ledger
        ).arm
        is not EvaluationArmV22.PLANNER_LITE_SALIENT
    ):
        raise ValueError("PR-D identity reconstruction differs")
    local = run_local_protocol_capability_suite_v22(provider_probe=probe)
    if (
        local.transition_count != 50
        or local.first_pass_protocol_acceptance != 0.96
        or local.post_correction_protocol_acceptance != 1.0
        or local.invalid_dispatches != 0
        or local.provider_calls != 0
        or local.provider_gate_eligible is not False
    ):
        raise ValueError("PR-D deterministic protocol harness differs")
    forbidden = {"truth", "fixture", "expected_mechanism", "case_id"}
    for function in (
        build_common_triage_snapshot_v22,
        build_controller_turn_input_v22,
        select_deterministic_router_decision_v22,
        build_one_shot_oracle_context_v22,
        run_provider_protocol_capability_suite_v22,
        process_controller_decision_v22,
    ):
        if not forbidden.isdisjoint(inspect.signature(function).parameters):
            raise ValueError("PR-D controller exposes evaluator truth input")
    if (
        "belief_ledger_view" not in ControllerTurnInputV22.model_fields
        or "truth" in HypothesisCatalogV22.model_fields
        or "raw_response" in ProviderControllerTurnV22.model_fields
    ):
        raise ValueError("PR-D typed privacy or truth boundary differs")
    required_v3_report_fields = {
        "transition_count",
        "parsed_decision_count",
        "runtime_protocol_admitted_count",
        "semantic_category_accepted_count",
        "ordinary_transition_count",
        "ordinary_first_pass_accepted_count",
        "ordinary_first_pass_protocol_acceptance",
        "ordinary_first_pass_by_arm",
        "ordinary_first_pass_by_category",
        "correction_transition_count",
        "correction_envelope_accepted_count",
        "correction_envelope_acceptance",
        "correction_acceptance_by_arm",
        "correction_acceptance_by_error_class",
        "final_accepted_count",
        "final_protocol_acceptance",
        "failure_taxonomy",
        "invalid_dispatches",
        "provider_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "latency",
        "http_auto_retry_count",
    }
    if (
        not required_v3_report_fields.issubset(
            ProviderProtocolCapabilityReportV3.model_fields
        )
        or "completed_transitions"
        not in ProviderProtocolPartialFailureReceiptV3.model_fields
        or "failure_taxonomy"
        not in ProviderProtocolPartialFailureReceiptV3.model_fields
        or "on_transition"
        not in inspect.signature(
            run_provider_protocol_capability_suite_v3
        ).parameters
        or "attempted_calls"
        not in inspect.signature(run_provider_protocol_replicate_v3).parameters
        or _FORMAL_REPLICATE_IDS != ("A", "B")
        or _FORMAL_MIN_REQUEST_INTERVAL_SECONDS != 4.0
        or _FORMAL_INTER_REPLICATE_COOLDOWN_SECONDS != 60.0
        or _FORMAL_HTTP_AUTO_RETRY_COUNT != 0
    ):
        raise ValueError("PR-D Provider protocol v3 runtime contract differs")


def verify_pr_d_protocol(root: Path) -> dict[str, object]:
    root = root.resolve(strict=True)
    progress = _load_json(root / "docs/analysis/dta-v22-p0-master-progress.json")
    mode, paths = _public_scan_plan(root, progress)
    if mode in {
        "PR_D_V3_EXECUTION_READY_SURFACE",
        "PR_D_V3_POST_EXECUTION_SURFACE",
    }:
        _verify_closed_changed_surface(root, expected_paths=paths)
    for relative in paths:
        text = (
            _changed_text(root, relative)
            if mode == "PR_D_V3_EXECUTION_READY_SURFACE"
            else _regular_file(root, relative).read_text(encoding="utf-8")
        )
        _assert_no_pr_d_public_leak(text)
    prior = verify_pr_b_protocol(root)
    verify_pr_c_bindings(root)
    _verify_pr_c_runtime_contracts()
    verify_blocked_provider_attempts(root)
    preregistration = verify_provider_v3_preregistration(root)
    _verify_runtime_contracts()
    if mode == "PR_D_V3_EXECUTION_READY_SURFACE":
        for relative in (
            *PROVIDER_V3_REPLICATE_SUMMARIES,
            PROVIDER_V3_CAMPAIGN_SUMMARY,
        ):
            _require_absent(root, relative)
        return {
            "schema_version": "dta-v22-pr-d-verification.v2",
            "status": "EXECUTION_READY",
            "historical_bindings": prior["historical_bindings"],
            "pr_c_successor_gate": "NOT_APPLICABLE_PRE_EXECUTION",
            "public_scan_mode": mode,
            "secret_private_path_scan": "PASS",
            "truth_isolation": "PASS",
            "shared_controller_schema": "PASS",
            "bounded_correction": "PASS_V3_CROSS_ARM",
            "identity_manifests": "CONSTRUCTION_FROZEN_MODE_PENDING",
            "provider_protocol_gate": "NOT_EXECUTED",
            "preregistration_sha256": preregistration["preregistration_sha256"],
            "merge_ready": False,
            "terminal": EXECUTION_READY_PR_D_TERMINAL,
        }
    results = verify_provider_v3_campaign_results(root)
    return {
        "schema_version": "dta-v22-pr-d-verification.v2",
        "status": "BLOCKED",
        "historical_bindings": prior["historical_bindings"],
        "pr_c_successor_gate": "NOT_APPLICABLE_UNMERGED_PR_D",
        "public_scan_mode": mode,
        "secret_private_path_scan": "PASS",
        "truth_isolation": "PASS",
        "shared_controller_schema": "PASS",
        "bounded_correction": "PASS_V3_CROSS_ARM",
        "identity_manifests": "FROZEN_PARTIAL_RECEIPTS_BOUND",
        "provider_protocol_gate": "BLOCKED",
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "replicate_summary_sha256s": list(
            EXPECTED_V3_REPLICATE_SEMANTIC_SHA256S
        ),
        "campaign_sha256": results["campaign_sha256"],
        "merge_ready": False,
        "terminal": BLOCKED_PR_D_TERMINAL,
    }


def verify_pr_c_stage_aware_gate(root: Path) -> dict[str, object]:
    """Route PR-C provenance according to whether PR-D has merged yet."""

    root = root.resolve(strict=True)
    progress = _load_json(root / "docs/analysis/dta-v22-p0-master-progress.json")
    if progress.get("current_stage") == "PR-D":
        if (
            progress.get("completed_stage") != "PR-C"
            or progress.get("active_pr") != PR_D_PR
            or progress.get("active_branch") != PR_D_BRANCH
        ):
            raise ValueError("PR-D stage identity differs for PR-C predecessor gate")
        verify_pr_c_bindings(root)
        _verify_pr_c_runtime_contracts()
        return {
            "schema_version": "dta-v22-pr-c-stage-aware-verification.v1",
            "status": "PASS",
            "mode": "PR_D_STAGE_FROZEN_BINDINGS",
            "successor_attestation": "NOT_APPLICABLE_UNMERGED_PR_D",
        }
    return verify_pr_c_protocol(root)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--stage-aware-pr-c", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = (
        verify_pr_c_stage_aware_gate(args.root)
        if args.stage_aware_pr_c
        else verify_pr_d_protocol(args.root)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
