"""Offline exact-head verifier for DTA v2.2 PR-D Provider Boundary v4."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, cast

from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderOutputModeV22,
)
from ecomsre.dta_v2.v22.provider_boundary_v4 import (
    materialize_protocol_requests_v4,
)
from ecomsre.dta_v2.v22.provider_protocol_v4 import (
    OpenAICompatibleProviderBoundaryV4,
    ProviderBoundaryProbeReportV4,
    ProviderBoundaryTurnV4,
)
from ecomsre.dta_v2.v22.protocol_suite_v4 import (
    ProviderProtocolReplicateReportV4,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.phase2.token_policy import load_offline_tokenizer


STARTING_HEAD_V4 = "23f6cd5c8a47fc1f91d3ac71829878fa1d1396bc"
STARTING_TREE_V4 = "ce4bd6e652a0c39753c08e2512cd8e26b61da2f1"
GOAL_VERSION_V4 = "dta-v22-p0-master-v1"
AMENDMENT_VERSION_V4 = "dta-v22-pr-d-provider-boundary-v4-amendment-v1"
MANIFEST_RELATIVE_V4 = Path(
    "config/dta-v22/provider-gate/pr-d-provider-boundary-v4-manifest.json"
)
PROGRESS_RELATIVE_V4 = Path("docs/analysis/dta-v22-p0-master-progress.json")
HUMAN_BRIEF_RELATIVE_V4 = Path(
    "docs/human-briefs/2026-08-20-dta-v22-pr-d-provider-boundary-v4.md"
)
PUBLIC_RESULT_RELATIVES_V4 = {
    "A": Path("docs/analysis/dta-v22-pr-d-provider-boundary-v4-replicate-a.json"),
    "B": Path("docs/analysis/dta-v22-pr-d-provider-boundary-v4-replicate-b.json"),
    "campaign": Path("docs/analysis/dta-v22-pr-d-provider-boundary-v4-campaign.json"),
}
DISPOSITION_RELATIVE_V4 = Path(
    "docs/review-evidence/dta-v22-pr-d-provider-boundary-v4/current-disposition.json"
)
ADMIN_ATTESTATION_RELATIVE_V4 = Path(
    "config/dta-v22/pr-d-provider-boundary-v4-administrative-attestation.json"
)
_DISPOSITION_FIELDS_V4 = (
    "schema_version",
    "goal_version",
    "amendment_version",
    "decision_id",
    "implementation_commit",
    "implementation_tree",
    "manifest_sha256",
    "campaign_sha256",
    "pre_execution_exact_head_ci_head",
    "pre_execution_exact_head_ci_run_id",
    "pre_execution_exact_head_ci_run_url",
    "pre_execution_exact_head_ci_status",
    "pre_execution_independent_review_head",
    "pre_execution_independent_review_must_fix_count",
    "pre_execution_claim_accuracy",
    "terminal",
    "merge_ready",
    "disposition_sha256",
)
_ADMIN_ATTESTATION_FIELDS_V4 = (
    "schema_version",
    "goal_version",
    "amendment_version",
    "decision_id",
    "repository",
    "pr",
    "starting_head",
    "starting_tree",
    "implementation_commit",
    "implementation_tree",
    "commit_b_parent",
    "changed_paths",
    "artifact_raw_sha256_by_path",
    "provider_called",
    "provider_call_count",
    "docker_called",
    "held_out_executed",
    "scenario_executed",
    "fault_injected",
    "agent_evidence_dispatched",
    "agent_write_executed",
    "runbook_executed",
    "private_evidence_changed",
    "public_result_changed",
    "third_v3_replicate_executed",
    "execution_report_rebound",
    "campaign_sha256",
    "terminal",
    "record_sha256",
)
HISTORICAL_PUBLIC_RAW_V4 = {
    "docs/analysis/dta-v22-pr-d-provider-protocol-attempt-1-invalid-location.json": "aa956933027cdd2902ebcc9a8c3b0df076df69b61ae37a1019bacbc8742a7552",
    "docs/analysis/dta-v22-pr-d-provider-protocol-attempt-2-validator-abort.json": "6510bf827dd8b2348ee0aab0560e404a41d34ce42adc3a719dfa901200c8f57e",
    "docs/analysis/dta-v22-pr-d-provider-protocol-attempt-3-rate-limited.json": "391973adb861c7ec5e93e2c59b38726680435c4f93a04fda7551014a9096d15f",
    "docs/analysis/dta-v22-pr-d-provider-protocol-attempt-4-rate-limited.json": "b2051cf6f06c2121e98f4c56defa651755dea7842a97dbefa6c056b41b23c0bf",
    "docs/analysis/dta-v22-pr-d-provider-protocol-attempt-5-gate-blocked.json": "ada22ef182f721e586a2d6e61e8f2138a9ae33d6fca6062830a63265852eee5a",
    "config/dta-v22/pr-d-provider-protocol-v3-preregistration.json": "0df94383805fe00c08c4b2f89e79d9d68ffa9c35d622203a941a6f7bb73a4175",
    "docs/analysis/dta-v22-pr-d-provider-protocol-v3-replicate-a-summary.json": "109ecbdf18651467bf6cc4f902accfc6d1fdca881692a0dc4326ab00eb4804e6",
    "docs/analysis/dta-v22-pr-d-provider-protocol-v3-replicate-b-summary.json": "7ee47877a313ac38641a5fc37f7799957301e1162f03cdd764e841dcda071363",
    "docs/analysis/dta-v22-pr-d-provider-protocol-v3-campaign-summary.json": "ddf1cc0f8eaaca2b7fefa81242f06581bdf69dddd78a0a082d6d312b028c5586",
    "docs/human-briefs/2026-08-20-dta-v22-pr-d-controller-protocol.md": "dd8c09b927add55503151d5f6f4cec7830d82c6a9141af5fe1e665fcfb2806b2",
}
HISTORICAL_PRIVATE_RAW_V4 = {
    "provider-mode-probe.json": "07bce87d85a69dda3eba78cac13f70246295e159353b698245d3d544b305a0c5",
    "replicate-a.json": "5dfe80b3d071b3caeb0a0b766ff94678131ec55f8e99e35002ec65709c2c3ece",
    "replicate-b.json": "1807a8746b2fc533cfd3dd0b613a44d97d3f0f044e2ec93e2d8cbe144bdd427e",
    "campaign.json": "4f2ccf4a8cd529be1d048a7cb74c1c0b3d1b305c28b0189b9cd713f121994bf7",
}
HISTORICAL_PRIVATE_EVIDENCE_V4 = {
    "provider-mode-probe.json": "f5c22d1d864a5a83a63979610a13237c4a5a74c1c7db0a36c77563f5e8076035",
    "replicate-a.json": "269600a9f293244927a049b2f62292298d873cdfab2d7ba17a5b38b6f7705adf",
    "replicate-b.json": "e26d33f2a69568b55f911431125e9d1687c03742be3aad5ac265523371edfb6f",
    "campaign.json": "22c3733336367c090737da6cf18b66e2fa2fcb3d9cda9a773c802766b79c5b0c",
}
HISTORICAL_PRIVATE_SEMANTIC_V4 = {
    "provider-mode-probe.json": "f3f4d8691439fd94191728fe3b5771df9929ae362c91cdaaad2ead8162fcedfc",
    "replicate-a.json": "e2cb542173217d76ed557917e3b7ca694e24becc03375a1d9d19fa6cb422f5f7",
    "replicate-b.json": "3f2b0039f508ef908967a64c2fc96fbd01ed97752289a1b4976d43fdce8cecd3",
    "campaign.json": "c534e799f23645afa316cc6451c39b2367e5b4011a522e3d78018f979883afd8",
}
COMMIT_A_PATHS_V4 = frozenset(
    {
        ".github/workflows/agent-mainline.yml",
        "config/dta-v22/provider-gate/pr-d-provider-boundary-v4-manifest.json",
        "docs/DECISIONS.md",
        "docs/analysis/dta-v22-p0-master-progress.json",
        "scripts/ci/verify_dta_v22_pr_d_v4.py",
        "scripts/dta_v22/run_pr_d_provider_boundary_v4.py",
        "src/ecomsre/dta_v2/v22/protocol_suite_v4.py",
        "src/ecomsre/dta_v2/v22/provider_boundary_v4.py",
        "src/ecomsre/dta_v2/v22/provider_protocol_v4.py",
        "tests/dta_v22/test_v22_pr_d_provider_boundary_v4_execution.py",
        "tests/dta_v22/test_v22_pr_d_verifier.py",
        "tests/dta_v22/test_v22_pr_d_v4_verifier.py",
        "tests/dta_v22/test_v22_provider_adapter_v4.py",
        "tests/dta_v22/test_v22_provider_boundary_v4.py",
        "tests/dta_v22/test_v22_provider_protocol_v4.py",
    }
)
COMMIT_B_PATHS_V4 = frozenset(
    {
        "docs/analysis/dta-v22-p0-master-progress.json",
        "docs/analysis/dta-v22-pr-d-provider-boundary-v4-replicate-a.json",
        "docs/analysis/dta-v22-pr-d-provider-boundary-v4-replicate-b.json",
        "docs/analysis/dta-v22-pr-d-provider-boundary-v4-campaign.json",
        "docs/human-briefs/2026-08-20-dta-v22-pr-d-provider-boundary-v4.md",
        "docs/review-evidence/dta-v22-pr-d-provider-boundary-v4/current-disposition.json",
        "config/dta-v22/pr-d-provider-boundary-v4-administrative-attestation.json",
    }
)


def _raw_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _load_private_object_v4(path: Path) -> dict[str, Any]:
    detail = path.lstat()
    if (
        stat.S_ISLNK(detail.st_mode)
        or not stat.S_ISREG(detail.st_mode)
        or stat.S_IMODE(detail.st_mode) != 0o600
        or detail.st_uid != os.getuid()
    ):
        raise ValueError(f"private v4 evidence authority differs: {path.name}")
    return _load_object(path)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _changed_paths(root: Path, base: str) -> set[str]:
    return _changed_paths_between(root, base, "HEAD")


def _changed_paths_between(root: Path, base: str, head: str) -> set[str]:
    output = _git(root, "diff", "--name-only", "--diff-filter=ACMRTUXB", base, head)
    return {line for line in output.splitlines() if line}


def _require_direct_child_v4(
    root: Path,
    *,
    child: str,
    parent: str,
    label: str,
) -> None:
    lineage = _git(root, "rev-list", "--parents", "-n", "1", child).split()
    if lineage != [child, parent]:
        raise ValueError(f"v4 {label} is not the exact single-parent child")


def _verify_commit_a_topology_v4(root: Path, implementation_commit: str) -> None:
    if not _is_sha(implementation_commit, 40):
        raise ValueError("v4 implementation commit identity differs")
    _require_direct_child_v4(
        root,
        child=implementation_commit,
        parent=STARTING_HEAD_V4,
        label="Commit A",
    )


def _verify_commit_b_topology_v4(root: Path, implementation_commit: str) -> None:
    _verify_commit_a_topology_v4(root, implementation_commit)
    final_head = _git(root, "rev-parse", "HEAD")
    _require_direct_child_v4(
        root,
        child=final_head,
        parent=implementation_commit,
        label="Commit B",
    )


def _verify_progress_v4(
    root: Path,
    *,
    manifest_sha256: str,
    campaign: dict[str, Any] | None,
) -> None:
    progress = _load_object(root / PROGRESS_RELATIVE_V4)
    common = {
        "schema_version": "dta-v22-p0-master-progress.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "completed_stage": "PR-C",
        "current_stage": "PR-D",
        "active_branch": "codex/dta-v22-p0-pr-d-planner-lite",
        "active_pr": 60,
        "primary_model": PRIMARY_MODEL_V22,
        "provider_boundary_version": "v4",
        "provider_boundary_v3_terminal": "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE",
        "provider_boundary_v3_campaign_sha256": "b23184d23ad5d6fc801e85efca268d5c7e7ad951ee004b8221fe2a5889211170",
        "provider_boundary_v4_manifest_sha256": manifest_sha256,
    }
    for key, expected in common.items():
        if progress.get(key) != expected:
            raise ValueError(f"v4 progress field differs: {key}")
    if campaign is None:
        expected = {
            "provider_boundary_v4_state": "PREREGISTERED_NOT_EXECUTED",
            "provider_boundary_v4_implementation_commit": None,
            "provider_boundary_v4_implementation_tree": None,
            "provider_boundary_v4_replicate_a_sha256": None,
            "provider_boundary_v4_replicate_b_sha256": None,
            "provider_boundary_v4_campaign_sha256": None,
            "final_engineering_terminal": None,
        }
    else:
        bindings = {
            item["replicate_id"]: item for item in campaign["replicate_bindings"]
        }
        expected = {
            "provider_boundary_v4_state": (
                "COMPLETE_PASS" if campaign["merge_ready"] else "COMPLETE_BLOCKED"
            ),
            "provider_boundary_v4_implementation_commit": campaign[
                "implementation_commit"
            ],
            "provider_boundary_v4_implementation_tree": campaign["implementation_tree"],
            "provider_boundary_v4_replicate_a_sha256": (
                bindings.get("A", {}).get("report_sha256")
            ),
            "provider_boundary_v4_replicate_b_sha256": (
                bindings.get("B", {}).get("report_sha256")
            ),
            "provider_boundary_v4_campaign_sha256": campaign["campaign_sha256"],
            "final_engineering_terminal": campaign["terminal"],
        }
    for key, value in expected.items():
        if progress.get(key) != value:
            raise ValueError(f"v4 progress result field differs: {key}")


def _require_raw_bindings(root: Path, bindings: dict[str, str]) -> None:
    for relative, expected in bindings.items():
        path = root / relative
        detail = path.lstat()
        if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
            raise ValueError(f"historical binding is not a regular file: {relative}")
        if _raw_sha(path) != expected:
            raise ValueError(f"historical raw hash differs: {relative}")


def _verify_private_history_v4(
    private_root: Path,
    bindings: dict[str, str],
    semantic_bindings: dict[str, str] | None = None,
    evidence_bindings: dict[str, str] | None = None,
) -> None:
    for name, expected in bindings.items():
        path = private_root / name
        detail = path.lstat()
        if (
            stat.S_ISLNK(detail.st_mode)
            or not stat.S_ISREG(detail.st_mode)
            or stat.S_IMODE(detail.st_mode) != 0o600
            or detail.st_uid != os.getuid()
            or _raw_sha(path) != expected
        ):
            raise ValueError(f"private v3 historical binding differs: {name}")
        value = _load_object(path)
        if semantic_bindings is not None and semantic_sha256_v22(
            value
        ) != semantic_bindings.get(name):
            raise ValueError(f"private v3 semantic binding differs: {name}")
        if evidence_bindings is not None and (
            value.get("evidence_sha256") != evidence_bindings.get(name)
            or semantic_sha256_v22(
                {key: item for key, item in value.items() if key != "evidence_sha256"}
            )
            != evidence_bindings.get(name)
        ):
            raise ValueError(f"private v3 evidence binding differs: {name}")


def _materialized_metrics(root: Path) -> dict[str, Any]:
    provider = OpenAICompatibleProviderBoundaryV4(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="offline-fixture",
            model=PRIMARY_MODEL_V22,
        ),
        timeout_seconds=60.0,
        max_completion_tokens=256,
        min_request_interval_seconds=12.0,
    )
    encoding = load_offline_tokenizer(root)
    matrix: dict[str, list[dict[str, Any]]] = {}
    request_commitments: dict[str, str] = {}
    projection_commitments: dict[str, str] = {}
    schema_commitments: dict[str, str] = {}
    projection_bytes_by_replicate: dict[str, list[int]] = {}
    input_tokens_by_replicate: dict[str, list[int]] = {}
    input_tokens_by_mode: dict[str, dict[str, list[int]]] = {
        mode.value: {} for mode in ProviderOutputModeV22
    }
    projection_sizes: list[int] = []
    input_tokens: list[int] = []
    for replicate_id in ("A", "B"):
        rows: list[dict[str, Any]] = []
        request_hashes: list[str] = []
        projection_hashes: list[str] = []
        schema_hashes: list[str] = []
        replicate_projection_sizes: list[int] = []
        replicate_input_tokens: list[int] = []
        replicate_input_tokens_by_mode: dict[str, list[int]] = {
            mode.value: [] for mode in ProviderOutputModeV22
        }
        for spec in materialize_protocol_requests_v4(replicate_id=replicate_id):  # type: ignore[arg-type]
            projection_text = json.dumps(
                spec.request.visible_state(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            size = len(projection_text.encode("utf-8"))
            tokens_by_mode: dict[str, int] = {}
            for mode in ProviderOutputModeV22:
                payload_text = json.dumps(
                    provider.payload(request=spec.request, mode=mode),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                mode_tokens = len(encoding.encode(payload_text))
                tokens_by_mode[mode.value] = mode_tokens
                replicate_input_tokens_by_mode[mode.value].append(mode_tokens)
            tokens = max(tokens_by_mode.values())
            projection_sizes.append(size)
            input_tokens.append(tokens)
            request_hashes.append(spec.request.request_sha256)
            projection_hashes.append(spec.request.projection_sha256)
            schema_hashes.append(spec.request.schema_sha256)
            replicate_projection_sizes.append(size)
            replicate_input_tokens.append(tokens)
            rows.append(
                {
                    "transition_id": spec.transition_id,
                    "arm": spec.arm.value,
                    "protocol_intent": spec.protocol_intent,
                    "protocol_category": spec.protocol_category,
                    "transition_kind": spec.transition_kind,
                    "correction_class": spec.correction_class,
                }
            )
        matrix[replicate_id] = rows
        request_commitments[replicate_id] = semantic_sha256_v22(request_hashes)
        projection_commitments[replicate_id] = semantic_sha256_v22(projection_hashes)
        schema_commitments[replicate_id] = semantic_sha256_v22(schema_hashes)
        projection_bytes_by_replicate[replicate_id] = replicate_projection_sizes
        input_tokens_by_replicate[replicate_id] = replicate_input_tokens
        for mode in ProviderOutputModeV22:
            input_tokens_by_mode[mode.value][replicate_id] = (
                replicate_input_tokens_by_mode[mode.value]
            )
    return {
        "replicate_transition_specs": matrix,
        "request_sha256_commitment_by_replicate": request_commitments,
        "projection_sha256_commitment_by_replicate": projection_commitments,
        "dynamic_schema_sha256_commitment_by_replicate": schema_commitments,
        "projection_bytes_by_replicate": projection_bytes_by_replicate,
        "projected_input_tokens_by_replicate": input_tokens_by_replicate,
        "projected_input_tokens_by_mode_and_replicate": input_tokens_by_mode,
        "projection_max_bytes_observed": max(projection_sizes),
        "projection_mean_bytes_observed": sum(projection_sizes) / len(projection_sizes),
        "projected_input_token_max": max(input_tokens),
        "projected_input_token_mean": sum(input_tokens) / len(input_tokens),
        "projected_input_tokens_per_minute": sum(input_tokens) / len(input_tokens) * 5,
    }


def load_and_verify_manifest_v4(root: Path) -> dict[str, Any]:
    manifest = _load_object(root / MANIFEST_RELATIVE_V4)
    claimed = manifest.get("manifest_sha256")
    expected = semantic_sha256_v22(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    if claimed != expected:
        raise ValueError("v4 manifest semantic hash differs")
    required = {
        "schema_version": "dta-v22-pr-d-provider-boundary-v4-manifest.v1",
        "goal_version": GOAL_VERSION_V4,
        "amendment_version": AMENDMENT_VERSION_V4,
        "decision_id": "DEC-058",
        "starting_head": STARTING_HEAD_V4,
        "starting_tree": STARTING_TREE_V4,
        "model": PRIMARY_MODEL_V22,
        "transition_count_per_replicate": 24,
        "first_pass_count_per_replicate": 20,
        "correction_count_per_replicate": 4,
        "minimum_request_start_interval_seconds": 12.0,
        "inter_replicate_cooldown_seconds": 120.0,
        "http_auto_retry_count": 0,
        "semantic_retry_count": 0,
        "replacement_replicate_count": 0,
        "provider_probe_count": 1,
        "provider_probe_provider_call_minimum": 1,
        "provider_probe_provider_call_maximum": 2,
        "formal_provider_call_count_range": [49, 50],
        "provider_output_mode_rule": (
            "STRICT_THEN_LOCAL_ONLY_ON_EXACT_STRICT_SCHEMA_UNSUPPORTED"
        ),
        "pre_execution_state": "V4_EXECUTION_READY",
        "merge_ready": False,
    }
    for key, value in required.items():
        if manifest.get(key) != value:
            raise ValueError(f"v4 manifest field differs: {key}")
    if manifest.get("post_execution_required_public_artifacts") != [
        PUBLIC_RESULT_RELATIVES_V4["campaign"].as_posix(),
        HUMAN_BRIEF_RELATIVE_V4.as_posix(),
        DISPOSITION_RELATIVE_V4.as_posix(),
        ADMIN_ATTESTATION_RELATIVE_V4.as_posix(),
    ]:
        raise ValueError("v4 manifest post-execution artifact contract differs")
    required_aggregates = manifest.get("required_replicate_aggregate_fields")
    if not isinstance(required_aggregates, list) or not {
        "completed_response_with_known_usage_count",
        "completed_response_with_unknown_usage_count",
        "mean_input_tokens",
        "max_input_tokens",
    }.issubset(required_aggregates):
        raise ValueError("v4 manifest usage aggregate contract differs")
    if manifest.get("historical_public_raw_sha256_by_path") != HISTORICAL_PUBLIC_RAW_V4:
        raise ValueError("v4 manifest historical public bindings differ")
    if (
        manifest.get("historical_private_raw_sha256_by_role")
        != HISTORICAL_PRIVATE_RAW_V4
    ):
        raise ValueError("v4 manifest historical private declarations differ")
    if (
        manifest.get("historical_private_semantic_sha256_by_role")
        != HISTORICAL_PRIVATE_SEMANTIC_V4
    ):
        raise ValueError("v4 manifest historical private semantic declarations differ")
    if (
        manifest.get("historical_private_evidence_sha256_by_role")
        != HISTORICAL_PRIVATE_EVIDENCE_V4
    ):
        raise ValueError("v4 manifest historical private evidence declarations differ")
    frozen = manifest.get("frozen_raw_sha256_by_path")
    if not isinstance(frozen, dict) or not frozen:
        raise ValueError("v4 manifest lacks frozen source bindings")
    _require_raw_bindings(root, frozen)
    metrics = _materialized_metrics(root)
    for key, value in metrics.items():
        observed = manifest.get(key)
        if isinstance(value, float):
            if (
                not isinstance(observed, (float, int))
                or abs(float(observed) - value) > 1e-9
            ):
                raise ValueError(f"v4 manifest metric differs: {key}")
        elif observed != value:
            raise ValueError(f"v4 manifest matrix differs: {key}")
    if (
        metrics["projection_max_bytes_observed"] > 12_000
        or metrics["projection_mean_bytes_observed"] > 8_000
        or metrics["projected_input_token_max"] > 5_500
        or metrics["projected_input_token_mean"] > 4_000
        or metrics["projected_input_tokens_per_minute"] > 30_000
    ):
        raise ValueError("v4 pre-execution size or token admission failed")
    return manifest


def verify_pre_execution_admission_v4(
    root: Path,
    *,
    require_private_history: bool = False,
    private_history_root: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if _git(root, "rev-parse", STARTING_HEAD_V4 + "^{tree}") != STARTING_TREE_V4:
        raise ValueError("v4 starting tree differs")
    if (
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                STARTING_HEAD_V4,
                "HEAD",
            )
        ).returncode
        != 0
    ):
        raise ValueError("v4 head does not descend from inspected start")
    _require_raw_bindings(root, HISTORICAL_PUBLIC_RAW_V4)
    manifest = load_and_verify_manifest_v4(root)
    if not any((root / path).exists() for path in PUBLIC_RESULT_RELATIVES_V4.values()):
        _verify_commit_a_topology_v4(root, _git(root, "rev-parse", "HEAD"))
        _verify_progress_v4(
            root,
            manifest_sha256=str(manifest["manifest_sha256"]),
            campaign=None,
        )
    if require_private_history:
        private_root = private_history_root or (
            Path.home()
            / ".ecomsre"
            / "private"
            / "dta-v22-p0-master-v1"
            / "pr-d"
            / "provider-protocol-v3"
        )
        _verify_private_history_v4(
            private_root,
            HISTORICAL_PRIVATE_RAW_V4,
            HISTORICAL_PRIVATE_SEMANTIC_V4,
            HISTORICAL_PRIVATE_EVIDENCE_V4,
        )
    return manifest


_PUBLIC_REPLICATE_FIELDS_V4 = frozenset(
    {
        "schema_version",
        "executed_at",
        "report",
        "private_raw_sha256",
        "private_semantic_sha256",
        "result_sha256",
    }
)
_PROBE_BINDING_FIELDS_V4 = frozenset(
    {
        "manifest_sha256",
        "private_raw_sha256",
        "private_semantic_sha256",
        "probe_evidence_sha256",
        "provider_calls",
        "supported",
        "selected_mode",
        "probe_report_sha256",
        "probe_report",
        "failure_class",
        "safe_failure_code",
        "attempted_modes",
        "manifest_binding_raw_sha256",
        "manifest_binding_semantic_sha256",
    }
)
_REPLICATE_BINDING_FIELDS_V4 = frozenset(
    {
        "replicate_id",
        "report_sha256",
        "terminal",
        "private_raw_sha256",
        "private_semantic_sha256",
        "public_raw_sha256",
        "public_semantic_sha256",
    }
)
_PUBLIC_CAMPAIGN_FIELDS_V4 = frozenset(
    {
        "schema_version",
        "goal_version",
        "amendment_version",
        "decision_id",
        "implementation_commit",
        "implementation_tree",
        "manifest_sha256",
        "probe_binding",
        "selected_mode",
        "replicate_bindings",
        "completed_replicate_count",
        "observed_provider_calls",
        "expected_provider_calls_for_complete_campaign",
        "provider_call_gate",
        "http_auto_retry_count",
        "semantic_retry_count",
        "replacement_replicate_count",
        "third_v3_replicate_count",
        "docker_calls",
        "scenario_executions",
        "fault_injections",
        "agent_evidence_dispatches",
        "agent_writes",
        "runbook_executions",
        "held_out_executions",
        "terminal",
        "merge_ready",
        "private_campaign_raw_sha256",
        "private_campaign_semantic_sha256",
        "campaign_sha256",
    }
)


def _is_sha(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None
    )


def _is_strict_int(value: object, *, allowed: set[int] | None = None) -> bool:
    return type(value) is int and (allowed is None or value in allowed)


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)


def _verify_public_replicate(
    path: Path,
) -> tuple[dict[str, Any], ProviderProtocolReplicateReportV4]:
    value = _load_object(path)
    if (
        frozenset(value) != _PUBLIC_REPLICATE_FIELDS_V4
        or value.get("schema_version")
        != "dta-v22-pr-d-provider-boundary-v4-replicate-result.v1"
        or not _is_utc_timestamp(value.get("executed_at"))
        or not _is_sha(value.get("private_raw_sha256"), 64)
        or not _is_sha(value.get("private_semantic_sha256"), 64)
    ):
        raise ValueError("v4 public replicate envelope differs")
    claimed = value.get("result_sha256")
    if claimed != semantic_sha256_v22(
        {key: item for key, item in value.items() if key != "result_sha256"}
    ):
        raise ValueError("v4 public replicate result digest differs")
    report_value = value.get("report")
    report = ProviderProtocolReplicateReportV4.model_validate_json(
        json.dumps(report_value, allow_nan=False)
    )
    return value, report


_PUBLIC_LEAK_PATTERNS_V4 = (
    re.compile(r"/(?:Users|home)/[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ECOMSRE_LLM_API_KEY\s*[=:]\s*[^\s\"']+"),
)
_PUBLIC_FORBIDDEN_KEYS_V4 = frozenset(
    {
        "raw_provider_text",
        "raw_provider_response",
        "base_url",
        "full_raw_evidence",
        "chain_of_thought",
        "credentials",
        "api_key",
        "authorization",
        "private_path",
    }
)


def _contains_forbidden_public_key(value: object) -> bool:
    if isinstance(value, dict):
        return bool(_PUBLIC_FORBIDDEN_KEYS_V4.intersection(value)) or any(
            _contains_forbidden_public_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_public_key(item) for item in value)
    return False


def _verify_public_leakage_v4(paths: tuple[Path, ...]) -> None:
    for path in paths:
        detail = path.lstat()
        if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
            raise ValueError("v4 public leakage scan requires regular files")
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in _PUBLIC_LEAK_PATTERNS_V4):
            raise ValueError(f"v4 public leakage scan failed: {path.name}")
        if path.suffix == ".json":
            try:
                value = json.loads(text)
            except (json.JSONDecodeError, RecursionError) as error:
                raise ValueError(
                    f"v4 public leakage scan requires valid JSON: {path.name}"
                ) from error
            if _contains_forbidden_public_key(value):
                raise ValueError(f"v4 public leakage scan failed: {path.name}")


def _verify_result_identity_v4(
    *,
    manifest: dict[str, Any],
    campaign: dict[str, Any],
    probe_report: ProviderBoundaryProbeReportV4 | None,
    reports: dict[str, ProviderProtocolReplicateReportV4],
) -> None:
    manifest_sha256 = manifest.get("manifest_sha256")
    if (
        campaign.get("schema_version")
        != "dta-v22-pr-d-provider-boundary-v4-campaign-result.v1"
        or campaign.get("manifest_sha256") != manifest_sha256
        or campaign.get("goal_version") != GOAL_VERSION_V4
        or campaign.get("amendment_version") != AMENDMENT_VERSION_V4
        or campaign.get("decision_id") != "DEC-058"
    ):
        raise ValueError("v4 result manifest binding differs")
    probe = campaign.get("probe_binding")
    selected_mode = campaign.get("selected_mode")
    if (
        not isinstance(probe, dict)
        or frozenset(probe) != _PROBE_BINDING_FIELDS_V4
        or probe.get("manifest_sha256") != manifest_sha256
    ):
        raise ValueError("v4 result probe binding differs")
    if probe_report is None:
        if (
            probe.get("probe_report_sha256") is not None
            or probe.get("probe_report") is not None
            or probe.get("supported") is not False
            or probe.get("selected_mode") is not None
            or selected_mode is not None
            or not _is_strict_int(probe.get("provider_calls"), allowed={1, 2})
            or probe.get("attempted_modes")
            != (
                ["STRICT_STRUCTURED_OUTPUT"]
                if probe.get("provider_calls") == 1
                else ["STRICT_STRUCTURED_OUTPUT", "LOCAL_FAIL_CLOSED_JSON"]
            )
            or probe.get("failure_class")
            not in {
                "PROVIDER_TRANSPORT_ABORT",
                "PROVIDER_RESPONSE_PROTOCOL_FAILURE",
            }
            or not isinstance(probe.get("safe_failure_code"), str)
            or reports
        ):
            raise ValueError("v4 result probe report binding differs")
    else:
        expected_mode = (
            None
            if probe_report.selected_mode is None
            else probe_report.selected_mode.value
        )
        expected_failure_class = (
            None if probe_report.supported else "PROVIDER_RESPONSE_PROTOCOL_FAILURE"
        )
        expected_failure_code = (
            None if probe_report.supported else "PROBE_DECISION_REJECTED"
        )
        expected_attempted_modes = [
            attempt.mode.value for attempt in probe_report.attempts
        ]
        if (
            probe.get("probe_report_sha256") != probe_report.report_sha256
            or probe.get("probe_report") != probe_report.model_dump(mode="json")
            or probe.get("supported") is not probe_report.supported
            or not _is_strict_int(probe.get("provider_calls"), allowed={1, 2})
            or probe.get("provider_calls") != probe_report.provider_calls
            or probe.get("selected_mode") != expected_mode
            or selected_mode != expected_mode
            or probe.get("attempted_modes") != expected_attempted_modes
            or probe.get("failure_class") != expected_failure_class
            or probe.get("safe_failure_code") != expected_failure_code
            or (bool(reports) and not probe_report.supported)
        ):
            raise ValueError("v4 result probe report binding differs")
    implementation_commit = campaign.get("implementation_commit")
    implementation_tree = campaign.get("implementation_tree")
    for replicate_id, report in reports.items():
        if (
            report.replicate_id != replicate_id
            or report.manifest_sha256 != manifest_sha256
            or report.implementation_commit != implementation_commit
            or report.implementation_tree != implementation_tree
            or probe_report is None
            or report.probe_report_sha256 != probe_report.report_sha256
            or report.selected_mode.value != selected_mode
        ):
            raise ValueError("v4 replicate result identity differs")


def verify_public_results_v4(
    root: Path,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    paths = {key: root / value for key, value in PUBLIC_RESULT_RELATIVES_V4.items()}
    present = {key: path.exists() for key, path in paths.items()}
    if not any(present.values()):
        return None
    if manifest is None:
        manifest = load_and_verify_manifest_v4(root)
    if not present["campaign"]:
        raise ValueError("v4 campaign result is missing after result creation")
    _verify_public_leakage_v4(
        tuple(path for key, path in paths.items() if present[key])
    )
    campaign = _load_object(paths["campaign"])
    if frozenset(campaign) != _PUBLIC_CAMPAIGN_FIELDS_V4:
        raise ValueError("v4 public campaign envelope differs")
    claimed = campaign.get("campaign_sha256")
    if claimed != semantic_sha256_v22(
        {key: value for key, value in campaign.items() if key != "campaign_sha256"}
    ):
        raise ValueError("v4 campaign result digest differs")
    reports: dict[str, ProviderProtocolReplicateReportV4] = {}
    values: dict[str, dict[str, Any]] = {}
    for replicate_id in ("A", "B"):
        if present[replicate_id]:
            values[replicate_id], reports[replicate_id] = _verify_public_replicate(
                paths[replicate_id]
            )
    probe_value = campaign.get("probe_binding")
    embedded_probe = (
        probe_value.get("probe_report") if isinstance(probe_value, dict) else None
    )
    probe_report = (
        None
        if embedded_probe is None
        else ProviderBoundaryProbeReportV4.model_validate_json(
            json.dumps(embedded_probe, allow_nan=False)
        )
    )
    _verify_result_identity_v4(
        manifest=manifest,
        campaign=campaign,
        probe_report=probe_report,
        reports=reports,
    )
    bindings = campaign.get("replicate_bindings")
    if (
        not isinstance(bindings, list)
        or len(bindings) != len(reports)
        or any(
            not isinstance(item, dict)
            or frozenset(item) != _REPLICATE_BINDING_FIELDS_V4
            for item in bindings
        )
    ):
        raise ValueError("v4 campaign replicate bindings differ")
    if [item.get("replicate_id") for item in bindings] != list(reports):
        raise ValueError("v4 campaign replicate order differs")
    for binding in bindings:
        replicate_id = binding.get("replicate_id")
        if replicate_id not in reports:
            raise ValueError("v4 campaign names an absent replicate")
        if (
            binding.get("report_sha256") != reports[replicate_id].report_sha256
            or binding.get("terminal") != reports[replicate_id].terminal.value
            or binding.get("private_raw_sha256")
            != values[replicate_id].get("private_raw_sha256")
            or binding.get("private_semantic_sha256")
            != values[replicate_id].get("private_semantic_sha256")
            or binding.get("public_raw_sha256") != _raw_sha(paths[replicate_id])
            or binding.get("public_semantic_sha256")
            != semantic_sha256_v22(values[replicate_id])
        ):
            raise ValueError("v4 campaign replicate binding differs")
    probe = campaign.get("probe_binding")
    probe_supported = isinstance(probe, dict) and probe.get("supported") is True
    probe_calls = probe.get("provider_calls") if isinstance(probe, dict) else None
    expected_calls = (
        cast(int, probe_calls)
        + sum(report.provider_calls for report in reports.values())
        if _is_strict_int(probe_calls, allowed={1, 2})
        else -1
    )
    if (
        not isinstance(probe, dict)
        or not _is_strict_int(probe_calls, allowed={1, 2})
        or (probe_supported and probe_report is None)
        or (not probe_supported and bool(reports))
        or not _is_strict_int(campaign.get("observed_provider_calls"))
        or campaign.get("observed_provider_calls") != expected_calls
        or not _is_strict_int(campaign.get("completed_replicate_count"))
        or campaign.get("completed_replicate_count") != len(reports)
        or not _is_strict_int(campaign.get("http_auto_retry_count"), allowed={0})
        or not _is_strict_int(campaign.get("semantic_retry_count"), allowed={0})
        or not _is_strict_int(campaign.get("replacement_replicate_count"), allowed={0})
        or any(
            not _is_strict_int(campaign.get(field), allowed={0})
            for field in (
                "docker_calls",
                "scenario_executions",
                "fault_injections",
                "agent_evidence_dispatches",
                "agent_writes",
                "runbook_executions",
                "held_out_executions",
            )
        )
    ):
        raise ValueError("v4 campaign call or protected-activity accounting differs")
    implementation_commit = campaign.get("implementation_commit")
    implementation_tree = campaign.get("implementation_tree")
    if (
        not isinstance(implementation_commit, str)
        or not isinstance(implementation_tree, str)
        or _git(root, "rev-parse", implementation_commit + "^{tree}")
        != implementation_tree
        or subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                implementation_commit,
                "HEAD",
            )
        ).returncode
        != 0
        or _changed_paths_between(root, STARTING_HEAD_V4, implementation_commit)
        != set(COMMIT_A_PATHS_V4)
    ):
        raise ValueError("v4 campaign implementation provenance differs")
    if not _changed_paths(root, implementation_commit).issubset(COMMIT_B_PATHS_V4):
        raise ValueError("v4 Commit A and result-only surface differs")
    both_pass = len(reports) == 2 and all(
        report.terminal.value == "PASS" for report in reports.values()
    )
    expected_complete_calls = cast(int, probe_calls) + 48 if len(reports) == 2 else None
    expected_terminal = (
        "DTA_V22_PR_D_CONTROLLER_READY"
        if both_pass
        and campaign.get("observed_provider_calls") == expected_complete_calls
        and campaign.get("expected_provider_calls_for_complete_campaign")
        == expected_complete_calls
        else "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"
    )
    if (
        campaign.get("terminal") != expected_terminal
        or campaign.get("merge_ready")
        != (expected_terminal == "DTA_V22_PR_D_CONTROLLER_READY")
        or not _is_strict_int(campaign.get("third_v3_replicate_count"), allowed={0})
    ):
        raise ValueError("v4 campaign terminal differs from frozen gates")
    return campaign


def _verify_private_manifest_binding_v4(
    *,
    private_root: Path,
    manifest_sha256: str,
    public_campaign: dict[str, Any],
) -> dict[str, Any]:
    binding_path = private_root / "manifest-binding.json"
    binding = _load_private_object_v4(binding_path)
    probe_binding = public_campaign.get("probe_binding")
    if (
        not isinstance(probe_binding, dict)
        or binding.get("binding_sha256")
        != semantic_sha256_v22(
            {key: value for key, value in binding.items() if key != "binding_sha256"}
        )
        or binding.get("manifest_sha256") != manifest_sha256
        or binding.get("implementation_commit")
        != public_campaign.get("implementation_commit")
        or binding.get("implementation_tree")
        != public_campaign.get("implementation_tree")
        or _raw_sha(binding_path) != probe_binding.get("manifest_binding_raw_sha256")
        or semantic_sha256_v22(binding)
        != probe_binding.get("manifest_binding_semantic_sha256")
    ):
        raise ValueError("private v4 manifest binding differs")
    return binding


def verify_private_execution_v4(
    *,
    root: Path,
    private_root: Path,
    manifest: dict[str, Any],
    public_campaign: dict[str, Any],
) -> None:
    """Verify the local create-once v4 evidence against its public projection."""

    detail = private_root.lstat()
    if (
        stat.S_ISLNK(detail.st_mode)
        or not stat.S_ISDIR(detail.st_mode)
        or stat.S_IMODE(detail.st_mode) != 0o700
        or detail.st_uid != os.getuid()
    ):
        raise ValueError("private v4 evidence root authority differs")
    manifest_sha256 = str(manifest["manifest_sha256"])
    _verify_private_manifest_binding_v4(
        private_root=private_root,
        manifest_sha256=manifest_sha256,
        public_campaign=public_campaign,
    )
    probe = _load_private_object_v4(private_root / "provider-mode-probe.json")
    probe_binding = public_campaign.get("probe_binding")
    exact_probe_projection_fields = (
        "manifest_sha256",
        "probe_evidence_sha256",
        "provider_calls",
        "supported",
        "selected_mode",
        "failure_class",
        "safe_failure_code",
        "attempted_modes",
        "probe_report",
    )
    private_probe_report = probe.get("probe_report")
    private_probe_report_sha = (
        private_probe_report.get("report_sha256")
        if isinstance(private_probe_report, dict)
        else None
    )
    if (
        not isinstance(probe_binding, dict)
        or frozenset(probe_binding) != _PROBE_BINDING_FIELDS_V4
        or probe.get("probe_evidence_sha256")
        != semantic_sha256_v22(
            {
                key: value
                for key, value in probe.items()
                if key != "probe_evidence_sha256"
            }
        )
        or probe.get("manifest_sha256") != manifest_sha256
        or _raw_sha(private_root / "provider-mode-probe.json")
        != probe_binding.get("private_raw_sha256")
        or semantic_sha256_v22(probe) != probe_binding.get("private_semantic_sha256")
        or any(
            json.loads(json.dumps(probe.get(key), allow_nan=False))
            != json.loads(json.dumps(probe_binding.get(key), allow_nan=False))
            for key in exact_probe_projection_fields
        )
        or probe_binding.get("probe_report_sha256") != private_probe_report_sha
    ):
        raise ValueError("private v4 probe binding differs")
    public_replicates = {
        replicate_id: _load_object(root / PUBLIC_RESULT_RELATIVES_V4[replicate_id])
        for replicate_id in ("A", "B")
        if (root / PUBLIC_RESULT_RELATIVES_V4[replicate_id]).exists()
    }
    for replicate_binding in public_campaign.get("replicate_bindings", []):
        replicate_id = replicate_binding.get("replicate_id")
        if replicate_id not in public_replicates:
            raise ValueError("private v4 replicate lacks a public result")
        private_path = private_root / f"replicate-{str(replicate_id).lower()}.json"
        private = _load_private_object_v4(private_path)
        if (
            private.get("evidence_sha256")
            != semantic_sha256_v22(
                {
                    key: value
                    for key, value in private.items()
                    if key != "evidence_sha256"
                }
            )
            or _raw_sha(private_path) != replicate_binding.get("private_raw_sha256")
            or semantic_sha256_v22(private)
            != replicate_binding.get("private_semantic_sha256")
            or private.get("report") != public_replicates[replicate_id].get("report")
        ):
            raise ValueError("private v4 replicate binding differs")
        report = ProviderProtocolReplicateReportV4.model_validate(private.get("report"))
        turns = tuple(
            ProviderBoundaryTurnV4.model_validate(value)
            for value in private.get("completed_turns", [])
        )
        expected_turn_request_sha256 = {
            item.provider_request_sha256
            for item in report.transitions
            if item.status.value == "COMPLETED_RESPONSE"
            and item.failure_class.value != "PROVIDER_RESPONSE_PROTOCOL_FAILURE"
        }
        if {
            turn.provider_request_sha256 for turn in turns
        } != expected_turn_request_sha256 or any(
            turn.mode is not report.selected_mode for turn in turns
        ):
            raise ValueError("private v4 completed turn binding differs")
    private_campaign_path = private_root / "campaign.json"
    private_campaign = _load_private_object_v4(private_campaign_path)
    if (
        _raw_sha(private_campaign_path)
        != public_campaign.get("private_campaign_raw_sha256")
        or semantic_sha256_v22(private_campaign)
        != public_campaign.get("private_campaign_semantic_sha256")
        or any(
            private_campaign.get(key) != public_campaign.get(key)
            for key in private_campaign
            if key != "campaign_sha256"
        )
    ):
        raise ValueError("private v4 campaign binding differs")


def _require_regular_public_file_v4(path: Path) -> None:
    detail = path.lstat()
    if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
        raise ValueError("v4 required public closure artifacts must be regular files")


def _verify_post_execution_artifacts_v4(
    root: Path,
    *,
    manifest: dict[str, Any],
    campaign: dict[str, Any],
) -> tuple[Path, ...]:
    required = (
        root / HUMAN_BRIEF_RELATIVE_V4,
        root / DISPOSITION_RELATIVE_V4,
        root / ADMIN_ATTESTATION_RELATIVE_V4,
    )
    if any(not path.exists() for path in required):
        raise ValueError("v4 required public closure artifacts are missing")
    for path in required:
        _require_regular_public_file_v4(path)

    implementation_commit = str(campaign["implementation_commit"])
    implementation_tree = str(campaign["implementation_tree"])
    _verify_commit_b_topology_v4(root, implementation_commit)
    changed_paths = sorted(_changed_paths(root, implementation_commit))

    disposition = _load_object(root / DISPOSITION_RELATIVE_V4)
    disposition_payload = dict(disposition)
    disposition_digest = disposition_payload.pop("disposition_sha256", None)
    ci_run_id = disposition.get("pre_execution_exact_head_ci_run_id")
    ci_run_url = disposition.get("pre_execution_exact_head_ci_run_url")
    if (
        tuple(disposition) != _DISPOSITION_FIELDS_V4
        or disposition.get("schema_version")
        != "dta-v22-pr-d-provider-boundary-v4.current-disposition.v1"
        or disposition.get("goal_version") != GOAL_VERSION_V4
        or disposition.get("amendment_version") != AMENDMENT_VERSION_V4
        or disposition.get("decision_id") != "DEC-058"
        or disposition.get("implementation_commit") != implementation_commit
        or disposition.get("implementation_tree") != implementation_tree
        or disposition.get("manifest_sha256") != manifest.get("manifest_sha256")
        or disposition.get("campaign_sha256") != campaign.get("campaign_sha256")
        or disposition.get("pre_execution_exact_head_ci_head") != implementation_commit
        or not isinstance(ci_run_id, int)
        or isinstance(ci_run_id, bool)
        or ci_run_id <= 0
        or not isinstance(ci_run_url, str)
        or re.fullmatch(
            r"https://github\.com/Raidriar7170/EcomSRE-Agent/actions/runs/[1-9][0-9]*",
            ci_run_url,
        )
        is None
        or disposition.get("pre_execution_exact_head_ci_status") != "PASS"
        or disposition.get("pre_execution_independent_review_head")
        != implementation_commit
        or disposition.get("pre_execution_independent_review_must_fix_count") != 0
        or disposition.get("pre_execution_claim_accuracy") != "PASS"
        or disposition.get("terminal") != campaign.get("terminal")
        or disposition.get("merge_ready") != campaign.get("merge_ready")
        or not _is_sha(disposition_digest, 64)
        or disposition_digest != semantic_sha256_v22(disposition_payload)
    ):
        raise ValueError("v4 review disposition binding differs")

    attestation = _load_object(root / ADMIN_ATTESTATION_RELATIVE_V4)
    attestation_payload = dict(attestation)
    record_digest = attestation_payload.pop("record_sha256", None)
    raw_hashes = attestation.get("artifact_raw_sha256_by_path")
    attestable_paths = [
        path
        for path in changed_paths
        if path != ADMIN_ATTESTATION_RELATIVE_V4.as_posix()
    ]
    expected_raw_hashes = {path: _raw_sha(root / path) for path in attestable_paths}
    false_activity_fields = (
        "docker_called",
        "held_out_executed",
        "scenario_executed",
        "fault_injected",
        "agent_evidence_dispatched",
        "agent_write_executed",
        "runbook_executed",
        "third_v3_replicate_executed",
        "execution_report_rebound",
    )
    if (
        tuple(attestation) != _ADMIN_ATTESTATION_FIELDS_V4
        or attestation.get("schema_version")
        != "dta-v22-pr-d-provider-boundary-v4-administrative-attestation.v1"
        or attestation.get("goal_version") != GOAL_VERSION_V4
        or attestation.get("amendment_version") != AMENDMENT_VERSION_V4
        or attestation.get("decision_id") != "DEC-058"
        or attestation.get("repository") != "Raidriar7170/EcomSRE-Agent"
        or attestation.get("pr") != 60
        or attestation.get("starting_head") != STARTING_HEAD_V4
        or attestation.get("starting_tree") != STARTING_TREE_V4
        or attestation.get("implementation_commit") != implementation_commit
        or attestation.get("implementation_tree") != implementation_tree
        or attestation.get("commit_b_parent") != implementation_commit
        or attestation.get("changed_paths") != changed_paths
        or raw_hashes != expected_raw_hashes
        or attestation.get("provider_called") is not True
        or attestation.get("provider_call_count")
        != campaign.get("observed_provider_calls")
        or attestation.get("private_evidence_changed") is not True
        or attestation.get("public_result_changed") is not True
        or any(attestation.get(field) is not False for field in false_activity_fields)
        or attestation.get("campaign_sha256") != campaign.get("campaign_sha256")
        or attestation.get("terminal") != campaign.get("terminal")
        or not _is_sha(record_digest, 64)
        or record_digest != semantic_sha256_v22(attestation_payload)
    ):
        raise ValueError("v4 administrative attestation binding differs")

    brief_text = (root / HUMAN_BRIEF_RELATIVE_V4).read_text(encoding="utf-8")
    for marker in (
        "DEC-058",
        AMENDMENT_VERSION_V4,
        implementation_commit,
        str(campaign["terminal"]),
    ):
        if marker not in brief_text:
            raise ValueError("v4 Human Brief claim binding differs")
    return required


def verify_repository_v4(root: Path) -> dict[str, Any]:
    manifest = verify_pre_execution_admission_v4(root)
    campaign = verify_public_results_v4(root, manifest)
    if campaign is None:
        if _changed_paths(root, STARTING_HEAD_V4) != set(COMMIT_A_PATHS_V4):
            raise ValueError("v4 Commit A changed surface differs")
        _verify_public_leakage_v4(
            (
                root / MANIFEST_RELATIVE_V4,
                root / PROGRESS_RELATIVE_V4,
                root / "docs/DECISIONS.md",
            )
        )
        return {
            "schema_version": "dta-v22-pr-d-verification.v4",
            "status": "EXECUTION_READY",
            "execution_state": "V4_EXECUTION_READY",
            "historical_v3_terminal": "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE",
            "historical_v3_bindings": "PASS",
            "private_historical_verification": "DECLARATION_BOUND_NOT_REQUIRED_IN_CI",
            "provider_protocol_gate": "NOT_EXECUTED",
            "projection_size_gate": "PASS",
            "token_rate_admission": "PASS",
            "manifest_sha256": manifest["manifest_sha256"],
            "merge_ready": False,
            "terminal": None,
        }
    implementation_commit = str(campaign["implementation_commit"])
    if not _changed_paths(root, implementation_commit).issubset(COMMIT_B_PATHS_V4):
        raise ValueError("v4 Commit B changed surface exceeds result-only authority")
    closure_paths = _verify_post_execution_artifacts_v4(
        root,
        manifest=manifest,
        campaign=campaign,
    )
    public_paths = (
        root / MANIFEST_RELATIVE_V4,
        root / PROGRESS_RELATIVE_V4,
        root / "docs/DECISIONS.md",
        *(
            root / value
            for value in PUBLIC_RESULT_RELATIVES_V4.values()
            if (root / value).exists()
        ),
        *closure_paths,
    )
    _verify_public_leakage_v4(public_paths)
    _verify_progress_v4(
        root,
        manifest_sha256=str(manifest["manifest_sha256"]),
        campaign=campaign,
    )
    return {
        "schema_version": "dta-v22-pr-d-verification.v4",
        "status": "PASS" if campaign["merge_ready"] else "BLOCKED",
        "execution_state": (
            "V4_COMPLETE_PASS" if campaign["merge_ready"] else "V4_COMPLETE_BLOCKED"
        ),
        "historical_v3_terminal": "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE",
        "historical_v3_bindings": "PASS",
        "private_historical_verification": "DECLARATION_BOUND_NOT_REQUIRED_IN_CI",
        "provider_protocol_gate": "PASS" if campaign["merge_ready"] else "BLOCKED",
        "manifest_sha256": manifest["manifest_sha256"],
        "merge_ready": campaign["merge_ready"],
        "terminal": campaign["terminal"],
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    print(json.dumps(verify_repository_v4(root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
