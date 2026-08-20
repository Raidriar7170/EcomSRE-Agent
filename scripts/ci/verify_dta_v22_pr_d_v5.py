"""Offline exact-head verifier for DTA v2.2 PR-D Provider Compatibility v5."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    build_controller_identity_manifests_v22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import ControllerDecisionV22
from ecomsre.dta_v2.v22.provider_compatibility_v5 import (
    AliasResolutionErrorV5,
    STATIC_PROVIDER_ALIAS_SCHEMA_V5,
    _projection_v5,
    build_provider_probe_request_v5,
    materialize_protocol_requests_v5,
    resolve_provider_alias_decision_v5,
    static_schema_sha256_v5,
)
from ecomsre.dta_v2.v22.provider_protocol_v5 import (
    OpenAICompatibleProviderBoundaryV5,
    PROVIDER_BOUNDARY_SYSTEM_PROMPT_V5,
    ProviderBoundaryProbeReportV5,
    ProviderBoundaryTurnV5,
    SafeProviderFailureV5,
    provider_request_payload_v5,
)
from ecomsre.dta_v2.v22.protocol_suite_v5 import (
    ProviderProtocolReplicateReportV5,
    ProviderProtocolTransitionV5,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.phase2.token_policy import load_offline_tokenizer


STARTING_HEAD_V5 = "255d60a92baf89ef1fca2c0170d4e757fa3014f5"
STARTING_TREE_V5 = "9f66c3ca1dfe6e45b6a453b6ee530a0e0650e245"
GOAL_VERSION_V5 = "dta-v22-p0-master-v1"
AMENDMENT_VERSION_V5 = "dta-v22-pr-d-provider-compatibility-v5-amendment-v1"
PersistedModelV5 = TypeVar("PersistedModelV5", bound=BaseModel)
MANIFEST_RELATIVE_V5 = Path(
    "config/dta-v22/provider-gate/pr-d-provider-compatibility-v5-manifest.json"
)
PROGRESS_RELATIVE_V5 = Path("docs/analysis/dta-v22-p0-master-progress.json")
HUMAN_BRIEF_RELATIVE_V5 = Path(
    "docs/human-briefs/2026-08-20-dta-v22-pr-d-provider-compatibility-v5.md"
)
PUBLIC_RESULT_RELATIVES_V5 = {
    "probe": Path("docs/analysis/dta-v22-pr-d-provider-compatibility-v5-probe.json"),
    "A": Path("docs/analysis/dta-v22-pr-d-provider-compatibility-v5-replicate-a.json"),
    "B": Path("docs/analysis/dta-v22-pr-d-provider-compatibility-v5-replicate-b.json"),
    "campaign": Path("docs/analysis/dta-v22-pr-d-provider-compatibility-v5-campaign.json"),
}
DISPOSITION_RELATIVE_V5 = Path(
    "docs/review-evidence/dta-v22-pr-d-provider-compatibility-v5/current-disposition.json"
)
ADMIN_ATTESTATION_RELATIVE_V5 = Path(
    "config/dta-v22/pr-d-provider-compatibility-v5-administrative-attestation.json"
)
_DISPOSITION_FIELDS_V5 = (
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
_ADMIN_ATTESTATION_FIELDS_V5 = (
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
HISTORICAL_PUBLIC_RAW_V5 = {
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
    "config/dta-v22/provider-gate/pr-d-provider-boundary-v4-manifest.json": "58a96662e2f6d3e7bca2c83783faaa37f99932beab78ac328647485b96a34c94",
    "docs/analysis/dta-v22-pr-d-provider-boundary-v4-campaign.json": "1df781493e8d9c6fb952c6f9dc8bf4626d1eb85e0aae645c4d4951c6a30687ed",
    "docs/human-briefs/2026-08-20-dta-v22-pr-d-provider-boundary-v4.md": "4b5f9561b1f3eb1a5439c434a0511d9aeabcd2c546dec5174d6f2a470d302a45",
    "docs/review-evidence/dta-v22-pr-d-provider-boundary-v4/current-disposition.json": "e79533467f8424c79bb1351bc4a381f431222990bd4c505a0c437d2f53ae5e48",
    "config/dta-v22/pr-d-provider-boundary-v4-administrative-attestation.json": "de2f19668b2f36d8e0f5ce802d622ecf690bf4592783a34a445d9972660fdd18",
    "scripts/ci/verify_dta_v22_pr_d_v4.py": "340c031a82429f7295659b2c9529d010b470d75b60b0d8ec300b140f0d12f0e0",
    "scripts/dta_v22/run_pr_d_provider_boundary_v4.py": "a72f4a231db19c6d16eff1a1b72f5ce83ac02d2e96d3f5fd09c54409aea64aca",
    "src/ecomsre/dta_v2/v22/protocol_suite_v4.py": "788c548277a63442422ff8495330c823009e1abc3477b5a9404c016a04471db5",
    "src/ecomsre/dta_v2/v22/provider_boundary_v4.py": "af8e0fdc34602a5a8eb01b2b7598a3c3177c7634ab8069515c39010668f8482a",
    "src/ecomsre/dta_v2/v22/provider_protocol_v4.py": "24fa26282133aeab0951465622a0bc2fb884779b34e2b7c02cd1b2b382be9280",
    "tests/dta_v22/test_v22_pr_d_provider_boundary_v4_execution.py": "a2ba623c7b17b2f886333ee7d414e60605fab6874dd0ba24f6f82f8e86aa2b4f",
    "tests/dta_v22/test_v22_pr_d_v4_verifier.py": "117a11d12cf50862680202d9aca389e6f50a9b1269e74ecf095d63a924f69693",
    "tests/dta_v22/test_v22_provider_adapter_v4.py": "868c9dd44db4a0803e58b542a7aaab1a38b0c6a1ff7c41dbbdc658bd92b440e3",
    "tests/dta_v22/test_v22_provider_boundary_v4.py": "6b204f31dfaea2a55113a7b218c68cc5ea63058be3b2de99bdf26e1cfaa996ac",
    "tests/dta_v22/test_v22_provider_protocol_v4.py": "36e3505e6e2f04300f18eb0ca34fad60c3c006d514ca33dce5893842c7fe9130",
}
HISTORICAL_PRIVATE_RAW_V5 = {
    "provider-mode-probe.json": "07bce87d85a69dda3eba78cac13f70246295e159353b698245d3d544b305a0c5",
    "replicate-a.json": "5dfe80b3d071b3caeb0a0b766ff94678131ec55f8e99e35002ec65709c2c3ece",
    "replicate-b.json": "1807a8746b2fc533cfd3dd0b613a44d97d3f0f044e2ec93e2d8cbe144bdd427e",
    "campaign.json": "4f2ccf4a8cd529be1d048a7cb74c1c0b3d1b305c28b0189b9cd713f121994bf7",
}
HISTORICAL_PRIVATE_EVIDENCE_V5 = {
    "provider-mode-probe.json": "f5c22d1d864a5a83a63979610a13237c4a5a74c1c7db0a36c77563f5e8076035",
    "replicate-a.json": "269600a9f293244927a049b2f62292298d873cdfab2d7ba17a5b38b6f7705adf",
    "replicate-b.json": "e26d33f2a69568b55f911431125e9d1687c03742be3aad5ac265523371edfb6f",
    "campaign.json": "22c3733336367c090737da6cf18b66e2fa2fcb3d9cda9a773c802766b79c5b0c",
}
HISTORICAL_PRIVATE_SEMANTIC_V5 = {
    "provider-mode-probe.json": "f3f4d8691439fd94191728fe3b5771df9929ae362c91cdaaad2ead8162fcedfc",
    "replicate-a.json": "e2cb542173217d76ed557917e3b7ca694e24becc03375a1d9d19fa6cb422f5f7",
    "replicate-b.json": "3f2b0039f508ef908967a64c2fc96fbd01ed97752289a1b4976d43fdce8cecd3",
    "campaign.json": "c534e799f23645afa316cc6451c39b2367e5b4011a522e3d78018f979883afd8",
}
HISTORICAL_PRIVATE_RAW_V4_V5 = {
    "manifest-binding.json": "77f103acb392beb3f526f5b63709ce09b2c5a1bddf000562cf4c58342289d247",
    "provider-mode-probe.json": "e49b53aa4d77e5fef20c7676cf4a81e575c59e0df4df29554b6e9dd7f48e8d33",
    "campaign.json": "5641b4981f26e10aec18197afa6c36e3a3ba9541f0ce93dfd78ba2dfb7fe4a75",
}
HISTORICAL_PRIVATE_SEMANTIC_V4_V5 = {
    "manifest-binding.json": "76003e0e64cde1515813cb88d7d9bfefd8398904f62f077d42f78642accd1b18",
    "provider-mode-probe.json": "b608c3b3127b06d556938fd196dde10ed121c32651c1ce9ae762fae605b62fc9",
    "campaign.json": "06c3b1185370df2d348b0f1ed373becb730ec13286becd40a8f04efa94735e3d",
}
COMMIT_A_PATHS_V5 = frozenset(
    {
        ".github/workflows/agent-mainline.yml",
        "conftest.py",
        "config/dta-v22/provider-gate/pr-d-provider-compatibility-v5-manifest.json",
        "docs/DECISIONS.md",
        "docs/analysis/dta-v22-p0-master-progress.json",
        "scripts/ci/verify_dta_v22_pr_d_v5.py",
        "scripts/dta_v22/run_pr_d_provider_compatibility_v5.py",
        "src/ecomsre/dta_v2/v22/protocol_suite_v5.py",
        "src/ecomsre/dta_v2/v22/provider_compatibility_v5.py",
        "src/ecomsre/dta_v2/v22/provider_protocol_v5.py",
        "tests/dta_v22/test_v22_pr_d_v5_lifecycle.py",
        "tests/dta_v22/test_v22_pr_d_provider_compatibility_v5_execution.py",
        "tests/dta_v22/test_v22_pr_d_v5_verifier.py",
        "tests/dta_v22/test_v22_provider_adapter_v5.py",
        "tests/dta_v22/test_v22_provider_compatibility_v5.py",
        "tests/dta_v22/test_v22_provider_protocol_v5.py",
    }
)
COMMIT_B_PATHS_V5 = frozenset(
    {
        "docs/analysis/dta-v22-p0-master-progress.json",
        "docs/analysis/dta-v22-pr-d-provider-compatibility-v5-probe.json",
        "docs/analysis/dta-v22-pr-d-provider-compatibility-v5-replicate-a.json",
        "docs/analysis/dta-v22-pr-d-provider-compatibility-v5-replicate-b.json",
        "docs/analysis/dta-v22-pr-d-provider-compatibility-v5-campaign.json",
        "docs/human-briefs/2026-08-20-dta-v22-pr-d-provider-compatibility-v5.md",
        "docs/review-evidence/dta-v22-pr-d-provider-compatibility-v5/current-disposition.json",
        "config/dta-v22/pr-d-provider-compatibility-v5-administrative-attestation.json",
    }
)


def _raw_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _validate_persisted_json_v5(
    model_type: type[PersistedModelV5], value: object
) -> PersistedModelV5:
    return model_type.model_validate_json(
        json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _load_private_object_v5(path: Path) -> dict[str, Any]:
    detail = path.lstat()
    if (
        stat.S_ISLNK(detail.st_mode)
        or not stat.S_ISREG(detail.st_mode)
        or stat.S_IMODE(detail.st_mode) != 0o600
        or detail.st_uid != os.getuid()
    ):
        raise ValueError(f"private v5 evidence authority differs: {path.name}")
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
    output = _git(root, "diff", "--name-only", "--diff-filter=ACDMRTUXB", base, head)
    return {line for line in output.splitlines() if line}


def _require_direct_child_v5(
    root: Path,
    *,
    child: str,
    parent: str,
    label: str,
) -> None:
    lineage = _git(root, "rev-list", "--parents", "-n", "1", child).split()
    if lineage != [child, parent]:
        raise ValueError(f"v5 {label} is not the exact single-parent child")


def _verify_commit_a_topology_v5(root: Path, implementation_commit: str) -> None:
    if not _is_sha(implementation_commit, 40):
        raise ValueError("v5 implementation commit identity differs")
    _require_direct_child_v5(
        root,
        child=implementation_commit,
        parent=STARTING_HEAD_V5,
        label="Commit A",
    )


def _verify_commit_b_topology_v5(root: Path, implementation_commit: str) -> None:
    _verify_commit_a_topology_v5(root, implementation_commit)
    final_head = _git(root, "rev-parse", "HEAD")
    _require_direct_child_v5(
        root,
        child=final_head,
        parent=implementation_commit,
        label="Commit B",
    )


def _verify_progress_v5(
    root: Path,
    *,
    manifest_sha256: str,
    campaign: dict[str, Any] | None,
) -> None:
    progress = _load_object(root / PROGRESS_RELATIVE_V5)
    common = {
        "schema_version": "dta-v22-p0-master-progress.v1",
        "goal_version": "dta-v22-p0-master-v1",
        "completed_stage": "PR-C",
        "current_stage": "PR-D",
        "active_branch": "codex/dta-v22-p0-pr-d-planner-lite",
        "active_pr": 60,
        "primary_model": PRIMARY_MODEL_V22,
        "provider_boundary_version": "v5",
        "provider_boundary_v3_terminal": "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE",
        "provider_boundary_v3_campaign_sha256": "b23184d23ad5d6fc801e85efca268d5c7e7ad951ee004b8221fe2a5889211170",
        "provider_boundary_v4_state": "COMPLETE_BLOCKED",
        "provider_boundary_v4_campaign_sha256": "1837365119ac0cf1fcd2ddbd50199387c47bdf6dfd88cf3f0e4b87382453fc3a",
        "provider_compatibility_v5_manifest_sha256": manifest_sha256,
        "planner_claim": None,
    }
    for key, expected in common.items():
        if progress.get(key) != expected:
            raise ValueError(f"v5 progress field differs: {key}")
    if campaign is None:
        expected = {
            "provider_mode": None,
            "flat_identity_sha256": None,
            "planner_identity_sha256": None,
            "router_identity_sha256": None,
            "one_shot_identity_sha256": None,
            "provider_compatibility_v5_state": "V5_PRE_EXECUTION_READY",
            "provider_compatibility_v5_implementation_commit": None,
            "provider_compatibility_v5_implementation_tree": None,
            "provider_compatibility_v5_replicate_a_sha256": None,
            "provider_compatibility_v5_replicate_b_sha256": None,
            "provider_compatibility_v5_campaign_sha256": None,
            "final_engineering_terminal": None,
        }
    else:
        bindings = {
            item["replicate_id"]: item for item in campaign["replicate_bindings"]
        }
        identity_values = _materialized_metrics(root)[
            "controller_identity_sha256_by_arm"
        ]
        passed = campaign["merge_ready"] is True
        expected = {
            "provider_mode": (
                "LOCAL_FAIL_CLOSED_JSON" if passed else None
            ),
            "flat_identity_sha256": (
                identity_values["FLAT_CANONICAL_SALIENT"] if passed else None
            ),
            "planner_identity_sha256": (
                identity_values["PLANNER_LITE_SALIENT"] if passed else None
            ),
            "router_identity_sha256": (
                identity_values["DETERMINISTIC_ROUTER_SALIENT"] if passed else None
            ),
            "one_shot_identity_sha256": (
                identity_values["ONE_SHOT_ORACLE_CONTEXT"] if passed else None
            ),
            "provider_compatibility_v5_state": (
                "V5_COMPLETE_PASS"
                if campaign["merge_ready"]
                else "V5_COMPLETE_BLOCKED"
            ),
            "provider_compatibility_v5_implementation_commit": campaign[
                "implementation_commit"
            ],
            "provider_compatibility_v5_implementation_tree": campaign["implementation_tree"],
            "provider_compatibility_v5_replicate_a_sha256": (
                bindings.get("A", {}).get("report_sha256")
            ),
            "provider_compatibility_v5_replicate_b_sha256": (
                bindings.get("B", {}).get("report_sha256")
            ),
            "provider_compatibility_v5_campaign_sha256": campaign["campaign_sha256"],
            "final_engineering_terminal": (
                None if passed else campaign["terminal"]
            ),
        }
    for key, value in expected.items():
        if progress.get(key) != value:
            raise ValueError(f"v5 progress result field differs: {key}")


def _require_raw_bindings(root: Path, bindings: dict[str, str]) -> None:
    for relative, expected in bindings.items():
        path = root / relative
        detail = path.lstat()
        if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
            raise ValueError(f"historical binding is not a regular file: {relative}")
        if _raw_sha(path) != expected:
            raise ValueError(f"historical raw hash differs: {relative}")


def _verify_private_history_v5(
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
    provider = OpenAICompatibleProviderBoundaryV5(
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
    matrix: dict[str, list[str]] = {}
    request_commitments: dict[str, str] = {}
    projection_commitments: dict[str, str] = {}
    static_schema_commitments: dict[str, str] = {}
    projection_sizes: list[int] = []
    input_tokens: list[int] = []
    for replicate_id in ("A", "B"):
        rows: list[str] = []
        request_hashes: list[str] = []
        projection_hashes: list[str] = []
        schema_hashes: list[str] = []
        for spec in materialize_protocol_requests_v5(replicate_id=replicate_id):  # type: ignore[arg-type]
            projection_text = json.dumps(
                spec.request.visible_state(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            size = len(projection_text.encode("utf-8"))
            payload_text = json.dumps(
                provider.payload(request=spec.request),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            tokens = len(encoding.encode(payload_text))
            projection_sizes.append(size)
            input_tokens.append(tokens)
            request_hashes.append(spec.request.request_sha256)
            projection_hashes.append(spec.request.projection_sha256)
            schema_hashes.append(spec.request.static_schema_sha256)
            rows.append(
                "|".join(
                    (
                        spec.transition_id,
                        spec.arm.value,
                        spec.protocol_intent,
                        spec.protocol_category,
                        spec.transition_kind,
                        spec.correction_class or "NONE",
                    )
                )
            )
        matrix[replicate_id] = rows
        request_commitments[replicate_id] = semantic_sha256_v22(request_hashes)
        projection_commitments[replicate_id] = semantic_sha256_v22(projection_hashes)
        static_schema_commitments[replicate_id] = semantic_sha256_v22(schema_hashes)
    identity_probe = probe_provider_output_mode_v22(
        probe=lambda *_args: ProviderProbeStatusV22.SUPPORTED
    )
    identities = {
        identity.arm.value: identity.identity_sha256
        for identity in build_controller_identity_manifests_v22(
            provider_probe=identity_probe
        )
    }
    return {
        "replicate_transition_specs": matrix,
        "request_sha256_commitment_by_replicate": request_commitments,
        "projection_sha256_commitment_by_replicate": projection_commitments,
        "static_schema_sha256": static_schema_sha256_v5(),
        "static_schema_canonical_raw_sha256": hashlib.sha256(
            json.dumps(
                STATIC_PROVIDER_ALIAS_SCHEMA_V5,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        "static_schema_sha256_commitment_by_replicate": static_schema_commitments,
        "projection_max_bytes_observed": max(projection_sizes),
        "projection_mean_bytes_observed": sum(projection_sizes) / len(projection_sizes),
        "projected_input_token_max": max(input_tokens),
        "projected_input_token_mean": sum(input_tokens) / len(input_tokens),
        "projected_input_tokens_per_minute": sum(input_tokens) / len(input_tokens) * 5,
        "system_prompt_sha256": semantic_sha256_v22(
            {"system_prompt": PROVIDER_BOUNDARY_SYSTEM_PROMPT_V5}
        ),
        "alias_resolver_source_sha256": hashlib.sha256(
            inspect.getsource(resolve_provider_alias_decision_v5).encode("utf-8")
        ).hexdigest(),
        "minimal_projection_source_sha256": hashlib.sha256(
            inspect.getsource(_projection_v5).encode("utf-8")
        ).hexdigest(),
        "controller_runtime_source_sha256": _raw_sha(
            root / "src/ecomsre/dta_v2/v22/controller_runtime.py"
        ),
        "internal_controller_decision_schema_sha256": semantic_sha256_v22(
            ControllerDecisionV22.model_json_schema()
        ),
        "controller_identity_sha256_by_arm": identities,
    }


def _verify_manifest_static_contract_v5(manifest: dict[str, Any]) -> None:
    required = {
        "schema_version": "dta-v22-pr-d-provider-compatibility-v5-manifest.v1",
        "goal_version": GOAL_VERSION_V5,
        "parent_goal_sha256": "cc176cc3eb63e96c6af7e4fda0022a7517776e7a3bfbe7c2f684966ffdb5a23c",
        "amendment_1_sha256": "ee38fd3fdb706cd59ea80a8b2c1be268b03f8d6435469852d607b3cb332e2c81",
        "amendment_2_sha256": "83e3fa80a8c5fd327460827f66d37cc3db3d6ee42e91d9539c609b31fd263d5b",
        "amendment_3_sha256": "394977bc6ffe8f2f2f9e815342641e9398efa8602dc9f748e90a653e8926a39a",
        "amendment_version": AMENDMENT_VERSION_V5,
        "decision_id": "DEC-059",
        "stage": "PR-D",
        "pr": 60,
        "branch": "codex/dta-v22-p0-pr-d-planner-lite",
        "base_main": "145d152c2c2d1367e7dac2f0229e2b369fbe55dc",
        "starting_head": STARTING_HEAD_V5,
        "starting_tree": STARTING_TREE_V5,
        "model": PRIMARY_MODEL_V22,
        "temperature": 0,
        "max_completion_tokens": 256,
        "execution_mode": "PROTOCOL_CONFORMANCE_ONLY",
        "replicate_ids": ["A", "B"],
        "replicate_count": 2,
        "transition_count_per_replicate": 24,
        "first_pass_count_per_replicate": 20,
        "correction_count_per_replicate": 4,
        "minimum_request_start_interval_seconds": 12.0,
        "inter_replicate_cooldown_seconds": 120.0,
        "http_auto_retry_count": 0,
        "semantic_retry_count": 0,
        "replacement_replicate_count": 0,
        "provider_probe_count": 1,
        "provider_probe_provider_call_count": 1,
        "formal_complete_campaign_provider_call_count": 49,
        "maximum_provider_call_count": 49,
        "provider_output_mode": "LOCAL_FAIL_CLOSED_JSON",
        "response_format_sent": False,
        "strict_function_schema": False,
        "parallel_tool_calls": False,
        "forced_function_call": True,
        "ordinary_first_pass_gate": {
            "overall_denominator": 20,
            "overall_minimum": 19,
            "per_arm_denominator": 10,
            "per_arm_minimum": 9,
        },
        "correction_gate": {"overall_required": 4, "per_arm_required": 2},
        "final_gate": {"denominator": 24, "minimum": 23},
        "transport_completeness_gate": {
            "actual_transport_abort_events": 0,
            "bounded_responses_required": 24,
        },
        "projection_max_bytes": 12_000,
        "projection_mean_bytes": 8_000,
        "formal_input_token_mean_max": 4_000,
        "formal_input_token_request_max": 5_500,
        "projected_requests_per_minute": 5.0,
        "projected_input_tokens_per_minute_max": 30_000,
        "run_b_after_a_semantic_failure": True,
        "run_b_after_a_transport_abort": False,
        "additional_provider_campaign_allowed": False,
        "pre_execution_state": "V5_PRE_EXECUTION_READY",
        "post_execution_states": ["V5_COMPLETE_PASS", "V5_COMPLETE_BLOCKED"],
        "success_terminal": "DTA_V22_PR_D_CONTROLLER_READY",
        "blocked_terminal": "BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE",
        "merge_ready": False,
        "private_evidence_location_class": (
            "DTA_V22_PRIVATE_ROOT_PR_D_PROVIDER_COMPATIBILITY_V5"
        ),
        "private_artifact_roles": [
            "manifest-binding.json",
            "local-mode-probe.json",
            "replicate-a.json",
            "replicate-b.json",
            "campaign.json",
        ],
        "public_result_paths": {
            "probe": PUBLIC_RESULT_RELATIVES_V5["probe"].as_posix(),
            "replicate_a": PUBLIC_RESULT_RELATIVES_V5["A"].as_posix(),
            "replicate_b": PUBLIC_RESULT_RELATIVES_V5["B"].as_posix(),
            "campaign": PUBLIC_RESULT_RELATIVES_V5["campaign"].as_posix(),
        },
    }
    for key, value in required.items():
        if manifest.get(key) != value:
            raise ValueError(f"v5 manifest field differs: {key}")
    if manifest.get("post_execution_required_public_artifacts") != [
        PUBLIC_RESULT_RELATIVES_V5["probe"].as_posix(),
        PUBLIC_RESULT_RELATIVES_V5["campaign"].as_posix(),
        HUMAN_BRIEF_RELATIVE_V5.as_posix(),
        DISPOSITION_RELATIVE_V5.as_posix(),
        ADMIN_ATTESTATION_RELATIVE_V5.as_posix(),
    ]:
        raise ValueError("v5 manifest post-execution artifact contract differs")
    if manifest.get("required_replicate_aggregate_fields") != [
        "planned_transition_count",
        "attempted_transition_count",
        "completed_response_count",
        "request_rejection_event_count",
        "rate_limit_event_count",
        "server_error_event_count",
        "timeout_event_count",
        "connection_error_event_count",
        "not_attempted_after_abort_count",
        "parse_failure_count",
        "alias_resolution_failure_count",
        "runtime_admission_failure_count",
        "protocol_intent_mismatch_count",
        "accepted_transition_count",
        "completed_response_with_known_usage_count",
        "completed_response_with_unknown_usage_count",
        "mean_input_tokens",
        "max_input_tokens",
    ]:
        raise ValueError("v5 manifest usage aggregate contract differs")
    derived_fields = {
        "replicate_transition_specs",
        "request_sha256_commitment_by_replicate",
        "projection_sha256_commitment_by_replicate",
        "static_schema_sha256",
        "static_schema_canonical_raw_sha256",
        "static_schema_sha256_commitment_by_replicate",
        "projection_max_bytes_observed",
        "projection_mean_bytes_observed",
        "projected_input_token_max",
        "projected_input_token_mean",
        "projected_input_tokens_per_minute",
        "system_prompt_sha256",
        "alias_resolver_source_sha256",
        "minimal_projection_source_sha256",
        "controller_runtime_source_sha256",
        "internal_controller_decision_schema_sha256",
        "controller_identity_sha256_by_arm",
    }
    binding_fields = {
        "historical_public_raw_sha256_by_path",
        "historical_private_raw_sha256_by_role",
        "historical_private_semantic_sha256_by_role",
        "historical_private_evidence_sha256_by_role",
        "historical_v4_private_raw_sha256_by_role",
        "historical_v4_private_semantic_sha256_by_role",
        "frozen_raw_sha256_by_path",
        "post_execution_required_public_artifacts",
        "required_replicate_aggregate_fields",
        "manifest_sha256",
    }
    if frozenset(manifest) != frozenset(required).union(
        derived_fields, binding_fields
    ):
        raise ValueError("v5 manifest field set differs")


def load_and_verify_manifest_v5(root: Path) -> dict[str, Any]:
    manifest = _load_object(root / MANIFEST_RELATIVE_V5)
    claimed = manifest.get("manifest_sha256")
    expected = semantic_sha256_v22(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    if claimed != expected:
        raise ValueError("v5 manifest semantic hash differs")
    _verify_manifest_static_contract_v5(manifest)
    if manifest.get("historical_public_raw_sha256_by_path") != HISTORICAL_PUBLIC_RAW_V5:
        raise ValueError("v5 manifest historical public bindings differ")
    if (
        manifest.get("historical_private_raw_sha256_by_role")
        != HISTORICAL_PRIVATE_RAW_V5
    ):
        raise ValueError("v5 manifest historical private declarations differ")
    if (
        manifest.get("historical_private_semantic_sha256_by_role")
        != HISTORICAL_PRIVATE_SEMANTIC_V5
    ):
        raise ValueError("v5 manifest historical private semantic declarations differ")
    if (
        manifest.get("historical_private_evidence_sha256_by_role")
        != HISTORICAL_PRIVATE_EVIDENCE_V5
    ):
        raise ValueError("v5 manifest historical private evidence declarations differ")
    if (
        manifest.get("historical_v4_private_raw_sha256_by_role")
        != HISTORICAL_PRIVATE_RAW_V4_V5
        or manifest.get("historical_v4_private_semantic_sha256_by_role")
        != HISTORICAL_PRIVATE_SEMANTIC_V4_V5
    ):
        raise ValueError("v5 manifest historical v4 private declarations differ")
    frozen = manifest.get("frozen_raw_sha256_by_path")
    if not isinstance(frozen, dict) or not frozen:
        raise ValueError("v5 manifest lacks frozen source bindings")
    _require_raw_bindings(root, frozen)
    metrics = _materialized_metrics(root)
    for key, value in metrics.items():
        observed = manifest.get(key)
        if isinstance(value, float):
            if (
                not isinstance(observed, (float, int))
                or abs(float(observed) - value) > 1e-9
            ):
                raise ValueError(f"v5 manifest metric differs: {key}")
        elif observed != value:
            raise ValueError(f"v5 manifest matrix differs: {key}")
    if (
        metrics["projection_max_bytes_observed"] > 12_000
        or metrics["projection_mean_bytes_observed"] > 8_000
        or metrics["projected_input_token_max"] > 5_500
        or metrics["projected_input_token_mean"] > 4_000
        or metrics["projected_input_tokens_per_minute"] > 30_000
    ):
        raise ValueError("v5 pre-execution size or token admission failed")
    return manifest


def verify_pre_execution_admission_v5(
    root: Path,
    *,
    require_private_history: bool = False,
    private_history_root: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("v5 exact-head verification requires a clean worktree")
    if _git(root, "rev-parse", STARTING_HEAD_V5 + "^{tree}") != STARTING_TREE_V5:
        raise ValueError("v5 starting tree differs")
    if (
        subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                STARTING_HEAD_V5,
                "HEAD",
            )
        ).returncode
        != 0
    ):
        raise ValueError("v5 head does not descend from inspected start")
    _require_raw_bindings(root, HISTORICAL_PUBLIC_RAW_V5)
    manifest = load_and_verify_manifest_v5(root)
    if not any((root / path).exists() for path in PUBLIC_RESULT_RELATIVES_V5.values()):
        _verify_commit_a_topology_v5(root, _git(root, "rev-parse", "HEAD"))
        _verify_progress_v5(
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
        _verify_private_history_v5(
            private_root,
            HISTORICAL_PRIVATE_RAW_V5,
            HISTORICAL_PRIVATE_SEMANTIC_V5,
            HISTORICAL_PRIVATE_EVIDENCE_V5,
        )
        v4_private_root = private_root.parent / "provider-boundary-v4"
        _verify_private_history_v5(
            v4_private_root,
            HISTORICAL_PRIVATE_RAW_V4_V5,
            HISTORICAL_PRIVATE_SEMANTIC_V4_V5,
        )
    return manifest


_PUBLIC_REPLICATE_FIELDS_V5 = frozenset(
    {
        "schema_version",
        "executed_at",
        "report",
        "private_raw_sha256",
        "private_semantic_sha256",
        "result_sha256",
    }
)
_PROBE_BINDING_FIELDS_V5 = frozenset(
    {
        "manifest_sha256",
        "public_result_sha256",
        "public_raw_sha256",
        "public_semantic_sha256",
        "private_raw_sha256",
        "private_semantic_sha256",
        "probe_evidence_sha256",
        "provider_calls",
        "supported",
        "selected_mode",
        "probe_report_sha256",
        "failure_class",
        "safe_failure_code",
        "attempted_modes",
        "manifest_binding_raw_sha256",
        "manifest_binding_semantic_sha256",
    }
)
_PUBLIC_PROBE_FIELDS_V5 = frozenset(
    {
        "schema_version",
        "executed_at",
        "implementation_commit",
        "implementation_tree",
        "manifest_sha256",
        "supported",
        "provider_calls",
        "selected_mode",
        "provider_request_sha256",
        "static_schema_sha256",
        "prompt_sha256",
        "probe_report_sha256",
        "failure_class",
        "safe_failure",
        "private_raw_sha256",
        "private_semantic_sha256",
        "manifest_binding_raw_sha256",
        "manifest_binding_semantic_sha256",
        "result_sha256",
    }
)
_REPLICATE_BINDING_FIELDS_V5 = frozenset(
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
_PUBLIC_CAMPAIGN_FIELDS_V5 = frozenset(
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


def _verify_closure_claim_count_types_v5(
    *,
    review_must_fix_count: object,
    provider_call_count: object,
    observed_provider_calls: object,
) -> None:
    if (
        not _is_strict_int(review_must_fix_count, allowed={0})
        or not _is_strict_int(provider_call_count)
        or not _is_strict_int(observed_provider_calls)
        or provider_call_count != observed_provider_calls
    ):
        raise ValueError("v5 closure claim counts require strict integers")


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
) -> tuple[dict[str, Any], ProviderProtocolReplicateReportV5]:
    value = _load_object(path)
    if (
        frozenset(value) != _PUBLIC_REPLICATE_FIELDS_V5
        or value.get("schema_version")
        != "dta-v22-pr-d-provider-compatibility-v5-replicate-result.v1"
        or not _is_utc_timestamp(value.get("executed_at"))
        or not _is_sha(value.get("private_raw_sha256"), 64)
        or not _is_sha(value.get("private_semantic_sha256"), 64)
    ):
        raise ValueError("v5 public replicate envelope differs")
    claimed = value.get("result_sha256")
    if claimed != semantic_sha256_v22(
        {key: item for key, item in value.items() if key != "result_sha256"}
    ):
        raise ValueError("v5 public replicate result digest differs")
    report_value = value.get("report")
    report = ProviderProtocolReplicateReportV5.model_validate_json(
        json.dumps(report_value, allow_nan=False)
    )
    return value, report


_PUBLIC_LEAK_PATTERNS_V5 = (
    re.compile(r"/(?:Users|home)/[^/\s]+/"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ECOMSRE_LLM_API_KEY\s*[=:]\s*[^\s\"']+"),
)
_PUBLIC_FORBIDDEN_KEYS_V5 = frozenset(
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
        return bool(_PUBLIC_FORBIDDEN_KEYS_V5.intersection(value)) or any(
            _contains_forbidden_public_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_public_key(item) for item in value)
    return False


def _verify_public_leakage_v5(paths: tuple[Path, ...]) -> None:
    for path in paths:
        detail = path.lstat()
        if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
            raise ValueError("v5 public leakage scan requires regular files")
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in _PUBLIC_LEAK_PATTERNS_V5):
            raise ValueError(f"v5 public leakage scan failed: {path.name}")
        if path.suffix == ".json":
            try:
                value = json.loads(text)
            except (json.JSONDecodeError, RecursionError) as error:
                raise ValueError(
                    f"v5 public leakage scan requires valid JSON: {path.name}"
                ) from error
            if _contains_forbidden_public_key(value):
                raise ValueError(f"v5 public leakage scan failed: {path.name}")


def _verify_public_probe_v5(path: Path) -> dict[str, Any]:
    value = _load_object(path)
    probe_request = build_provider_probe_request_v5()
    expected_request_sha256 = probe_request.request_sha256
    expected_prompt_sha256 = semantic_sha256_v22(
        {"system_prompt": PROVIDER_BOUNDARY_SYSTEM_PROMPT_V5}
    )
    expected_payload_sha256 = semantic_sha256_v22(
        provider_request_payload_v5(request=probe_request)
    )
    if (
        frozenset(value) != _PUBLIC_PROBE_FIELDS_V5
        or value.get("schema_version")
        != "dta-v22-pr-d-provider-compatibility-v5-probe-result.v1"
        or not _is_utc_timestamp(value.get("executed_at"))
        or not _is_sha(value.get("implementation_commit"), 40)
        or not _is_sha(value.get("implementation_tree"), 40)
        or not _is_sha(value.get("manifest_sha256"), 64)
        or not _is_strict_int(value.get("provider_calls"), allowed={1})
        or value.get("selected_mode")
        not in {None, ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON.value}
        or not _is_sha(value.get("private_raw_sha256"), 64)
        or not _is_sha(value.get("private_semantic_sha256"), 64)
        or not _is_sha(value.get("manifest_binding_raw_sha256"), 64)
        or not _is_sha(value.get("manifest_binding_semantic_sha256"), 64)
    ):
        raise ValueError("v5 public probe envelope differs")
    if value.get("result_sha256") != semantic_sha256_v22(
        {key: item for key, item in value.items() if key != "result_sha256"}
    ):
        raise ValueError("v5 public probe result digest differs")
    supported = value.get("supported")
    if type(supported) is not bool:
        raise ValueError("v5 public probe support flag is not strict bool")
    if supported:
        if (
            value.get("selected_mode") != "LOCAL_FAIL_CLOSED_JSON"
            or value.get("failure_class") is not None
            or value.get("safe_failure") is not None
            or value.get("provider_request_sha256") != expected_request_sha256
            or value.get("static_schema_sha256") != static_schema_sha256_v5()
            or value.get("prompt_sha256") != expected_prompt_sha256
            or not _is_sha(value.get("probe_report_sha256"), 64)
        ):
            raise ValueError("v5 successful public probe binding differs")
    elif (
        value.get("selected_mode") is not None
        or not isinstance(value.get("failure_class"), str)
        or value.get("safe_failure") is None
    ):
        raise ValueError("v5 negative public probe binding differs")
    else:
        try:
            safe_failure = _validate_persisted_json_v5(
                SafeProviderFailureV5,
                value.get("safe_failure"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("v5 public probe safe failure differs") from error
        if (
            safe_failure.failure_stage != "PROBE"
            or safe_failure.failure_class.value != value.get("failure_class")
            or safe_failure.request_payload_sha256 != expected_payload_sha256
        ):
            raise ValueError("v5 public probe safe failure differs")
        report_identity = (
            value.get("provider_request_sha256"),
            value.get("static_schema_sha256"),
            value.get("prompt_sha256"),
            value.get("probe_report_sha256"),
        )
        if any(item is not None for item in report_identity) and (
            report_identity[0] != expected_request_sha256
            or report_identity[1] != static_schema_sha256_v5()
            or report_identity[2] != expected_prompt_sha256
            or not _is_sha(report_identity[3], 64)
        ):
            raise ValueError("v5 negative public probe report identity differs")
    return value


def _verify_public_probe_campaign_identity_v5(
    *,
    manifest: dict[str, Any],
    campaign: dict[str, Any],
    public_probe: dict[str, Any],
) -> None:
    if (
        public_probe.get("manifest_sha256") != manifest.get("manifest_sha256")
        or public_probe.get("implementation_commit")
        != campaign.get("implementation_commit")
        or public_probe.get("implementation_tree")
        != campaign.get("implementation_tree")
    ):
        raise ValueError("v5 public probe identity differs from campaign")


def _verify_campaign_replicate_state_v5(
    *,
    probe_supported: bool,
    reports: dict[str, Any],
) -> None:
    ids = tuple(reports)
    valid = (
        (not probe_supported and not ids)
        or (probe_supported and ids == ("A", "B"))
        or (
            probe_supported
            and ids == ("A",)
            and reports["A"].completed_response_count < 24
        )
    )
    if not valid:
        raise ValueError("v5 campaign replicate state differs")


def _verify_result_identity_v5(
    *,
    manifest: dict[str, Any],
    campaign: dict[str, Any],
    public_probe: dict[str, Any],
    reports: dict[str, ProviderProtocolReplicateReportV5],
) -> None:
    manifest_sha256 = manifest.get("manifest_sha256")
    _verify_public_probe_campaign_identity_v5(
        manifest=manifest,
        campaign=campaign,
        public_probe=public_probe,
    )
    if (
        campaign.get("schema_version")
        != "dta-v22-pr-d-provider-compatibility-v5-campaign-result.v1"
        or campaign.get("manifest_sha256") != manifest_sha256
        or campaign.get("goal_version") != GOAL_VERSION_V5
        or campaign.get("amendment_version") != AMENDMENT_VERSION_V5
        or campaign.get("decision_id") != "DEC-059"
    ):
        raise ValueError("v5 result manifest binding differs")
    probe = campaign.get("probe_binding")
    selected_mode = campaign.get("selected_mode")
    if (
        not isinstance(probe, dict)
        or frozenset(probe) != _PROBE_BINDING_FIELDS_V5
        or probe.get("manifest_sha256") != manifest_sha256
        or probe.get("public_result_sha256") != public_probe.get("result_sha256")
        or probe.get("private_raw_sha256")
        != public_probe.get("private_raw_sha256")
        or probe.get("private_semantic_sha256")
        != public_probe.get("private_semantic_sha256")
        or probe.get("manifest_binding_raw_sha256")
        != public_probe.get("manifest_binding_raw_sha256")
        or probe.get("manifest_binding_semantic_sha256")
        != public_probe.get("manifest_binding_semantic_sha256")
    ):
        raise ValueError("v5 result probe binding differs")
    if not public_probe["supported"]:
        safe_failure = public_probe.get("safe_failure")
        if (
            probe.get("probe_report_sha256")
            != public_probe.get("probe_report_sha256")
            or probe.get("supported") is not False
            or probe.get("selected_mode") is not None
            or selected_mode is not None
            or not _is_strict_int(probe.get("provider_calls"), allowed={1})
            or probe.get("attempted_modes") != ["LOCAL_FAIL_CLOSED_JSON"]
            or probe.get("failure_class") != public_probe.get("failure_class")
            or not isinstance(safe_failure, dict)
            or probe.get("safe_failure_code") != safe_failure.get("safe_code")
            or reports
        ):
            raise ValueError("v5 result probe report binding differs")
    else:
        if (
            probe.get("probe_report_sha256")
            != public_probe.get("probe_report_sha256")
            or probe.get("supported") is not True
            or not _is_strict_int(probe.get("provider_calls"), allowed={1})
            or probe.get("provider_calls") != public_probe.get("provider_calls")
            or probe.get("selected_mode") != "LOCAL_FAIL_CLOSED_JSON"
            or selected_mode != "LOCAL_FAIL_CLOSED_JSON"
            or probe.get("attempted_modes") != ["LOCAL_FAIL_CLOSED_JSON"]
            or probe.get("failure_class") is not None
            or probe.get("safe_failure_code") is not None
        ):
            raise ValueError("v5 result probe report binding differs")
    implementation_commit = campaign.get("implementation_commit")
    implementation_tree = campaign.get("implementation_tree")
    for replicate_id, report in reports.items():
        if (
            report.replicate_id != replicate_id
            or report.manifest_sha256 != manifest_sha256
            or report.implementation_commit != implementation_commit
            or report.implementation_tree != implementation_tree
            or report.probe_report_sha256
            != public_probe.get("probe_report_sha256")
            or report.selected_mode.value != selected_mode
        ):
            raise ValueError("v5 replicate result identity differs")


def verify_public_results_v5(
    root: Path,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    paths = {key: root / value for key, value in PUBLIC_RESULT_RELATIVES_V5.items()}
    present = {key: path.exists() for key, path in paths.items()}
    if not any(present.values()):
        return None
    if manifest is None:
        manifest = load_and_verify_manifest_v5(root)
    if not present["campaign"] or not present["probe"]:
        raise ValueError("v5 probe/campaign result is missing after result creation")
    _verify_public_leakage_v5(
        tuple(path for key, path in paths.items() if present[key])
    )
    campaign = _load_object(paths["campaign"])
    public_probe = _verify_public_probe_v5(paths["probe"])
    if frozenset(campaign) != _PUBLIC_CAMPAIGN_FIELDS_V5:
        raise ValueError("v5 public campaign envelope differs")
    if (
        type(campaign.get("merge_ready")) is not bool
        or not _is_sha(campaign.get("private_campaign_raw_sha256"), 64)
        or not _is_sha(campaign.get("private_campaign_semantic_sha256"), 64)
    ):
        raise ValueError("v5 public campaign typed envelope differs")
    claimed = campaign.get("campaign_sha256")
    if claimed != semantic_sha256_v22(
        {key: value for key, value in campaign.items() if key != "campaign_sha256"}
    ):
        raise ValueError("v5 campaign result digest differs")
    reports: dict[str, ProviderProtocolReplicateReportV5] = {}
    values: dict[str, dict[str, Any]] = {}
    for replicate_id in ("A", "B"):
        if present[replicate_id]:
            values[replicate_id], reports[replicate_id] = _verify_public_replicate(
                paths[replicate_id]
            )
    _verify_campaign_replicate_state_v5(
        probe_supported=public_probe.get("supported") is True,
        reports=reports,
    )
    _verify_result_identity_v5(
        manifest=manifest,
        campaign=campaign,
        public_probe=public_probe,
        reports=reports,
    )
    probe_value = campaign.get("probe_binding")
    if (
        not isinstance(probe_value, dict)
        or probe_value.get("public_raw_sha256") != _raw_sha(paths["probe"])
        or probe_value.get("public_semantic_sha256")
        != semantic_sha256_v22(public_probe)
    ):
        raise ValueError("v5 campaign public probe binding differs")
    bindings = campaign.get("replicate_bindings")
    if (
        not isinstance(bindings, list)
        or len(bindings) != len(reports)
        or any(
            not isinstance(item, dict)
            or frozenset(item) != _REPLICATE_BINDING_FIELDS_V5
            for item in bindings
        )
    ):
        raise ValueError("v5 campaign replicate bindings differ")
    if [item.get("replicate_id") for item in bindings] != list(reports):
        raise ValueError("v5 campaign replicate order differs")
    for binding in bindings:
        replicate_id = binding.get("replicate_id")
        if replicate_id not in reports:
            raise ValueError("v5 campaign names an absent replicate")
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
            raise ValueError("v5 campaign replicate binding differs")
    probe = campaign.get("probe_binding")
    probe_supported = isinstance(probe, dict) and probe.get("supported") is True
    probe_calls = probe.get("provider_calls") if isinstance(probe, dict) else None
    expected_calls = (
        cast(int, probe_calls)
        + sum(report.provider_calls for report in reports.values())
        if _is_strict_int(probe_calls, allowed={1})
        else -1
    )
    expected_complete_calls = 49 if len(reports) == 2 else None
    expected_call_gate = campaign.get("observed_provider_calls") == expected_calls
    if (
        not isinstance(probe, dict)
        or not _is_strict_int(probe_calls, allowed={1})
        or (probe_supported and not public_probe["supported"])
        or (not probe_supported and bool(reports))
        or not _is_strict_int(campaign.get("observed_provider_calls"))
        or campaign.get("observed_provider_calls") != expected_calls
        or not _is_strict_int(campaign.get("completed_replicate_count"))
        or campaign.get("completed_replicate_count") != len(reports)
        or campaign.get("provider_call_gate") is not expected_call_gate
        or campaign.get("expected_provider_calls_for_complete_campaign")
        != expected_complete_calls
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
        raise ValueError("v5 campaign call or protected-activity accounting differs")
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
        or _changed_paths_between(root, STARTING_HEAD_V5, implementation_commit)
        != set(COMMIT_A_PATHS_V5)
    ):
        raise ValueError("v5 campaign implementation provenance differs")
    if not _changed_paths(root, implementation_commit).issubset(COMMIT_B_PATHS_V5):
        raise ValueError("v5 Commit A and result-only surface differs")
    both_pass = len(reports) == 2 and all(
        report.terminal.value == "PASS" for report in reports.values()
    )
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
        is not (expected_terminal == "DTA_V22_PR_D_CONTROLLER_READY")
        or not _is_strict_int(campaign.get("third_v3_replicate_count"), allowed={0})
    ):
        raise ValueError("v5 campaign terminal differs from frozen gates")
    return campaign


def _verify_private_manifest_binding_v5(
    *,
    private_root: Path,
    manifest_sha256: str,
    public_campaign: dict[str, Any],
) -> dict[str, Any]:
    binding_path = private_root / "manifest-binding.json"
    binding = _load_private_object_v5(binding_path)
    probe_binding = public_campaign.get("probe_binding")
    if (
        not isinstance(probe_binding, dict)
        or frozenset(binding)
        != {
            "schema_version",
            "implementation_commit",
            "implementation_tree",
            "manifest_sha256",
            "bound_at",
            "binding_sha256",
        }
        or binding.get("schema_version")
        != "dta-v22-pr-d-provider-compatibility-v5-manifest-binding.v1"
        or not _is_utc_timestamp(binding.get("bound_at"))
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
        raise ValueError("private v5 manifest binding differs")
    return binding


def _verify_private_completed_turn_bindings_v5(
    transitions: tuple[ProviderProtocolTransitionV5, ...],
    turns: tuple[ProviderBoundaryTurnV5, ...],
) -> None:
    expected = tuple(
        transition
        for transition in transitions
        if transition.status.value == "COMPLETED_RESPONSE"
        and transition.failure_class.value
        != "PROVIDER_RESPONSE_PROTOCOL_FAILURE"
    )
    if len(turns) != len(expected):
        raise ValueError("private v5 completed turn binding differs")
    prompt_sha256 = semantic_sha256_v22(
        {"system_prompt": PROVIDER_BOUNDARY_SYSTEM_PROMPT_V5}
    )
    for transition, turn in zip(expected, turns, strict=True):
        replicate_id = "A" if "-a-" in transition.transition_id else "B"
        specs = {
            spec.transition_id: spec
            for spec in materialize_protocol_requests_v5(replicate_id=replicate_id)  # type: ignore[arg-type]
        }
        spec = specs.get(transition.transition_id)
        if spec is None:
            raise ValueError("private v5 completed turn binding differs")
        expected_payload_sha256 = semantic_sha256_v22(
            provider_request_payload_v5(request=spec.request)
        )
        expected_canonical: ControllerDecisionV22 | None = None
        expected_failure_code: str | None
        if turn.alias_decision is None:
            expected_failure_code = "INVALID_ALIAS_DECISION_SHAPE"
        else:
            try:
                expected_canonical = resolve_provider_alias_decision_v5(
                    alias_decision=turn.alias_decision,
                    binding=spec.request.alias_binding,
                )
            except AliasResolutionErrorV5 as error:
                expected_failure_code = error.code.value
            else:
                expected_failure_code = None
        if (
            turn.mode is not transition.selected_mode
            or transition.alias_binding_sha256
            != spec.request.alias_binding.binding_sha256
            or turn.provider_request_sha256 != transition.provider_request_sha256
            or turn.provider_request_sha256 != spec.request.request_sha256
            or turn.projection_sha256 != spec.request.projection_sha256
            or turn.static_schema_sha256 != spec.request.static_schema_sha256
            or turn.prompt_sha256 != prompt_sha256
            or turn.request_payload_sha256 != expected_payload_sha256
            or turn.raw_response_sha256 != transition.raw_response_sha256
            or turn.turn_sha256 != transition.provider_turn_sha256
            or turn.raw_alias_decision_sha256
            != transition.raw_alias_decision_sha256
            or turn.resolved_canonical_decision_sha256
            != transition.resolved_canonical_decision_sha256
            or turn.alias_binding_sha256 != transition.alias_binding_sha256
            or turn.alias_binding_sha256
            != spec.request.alias_binding.binding_sha256
            or turn.canonical_decision != expected_canonical
            or (None if turn.failure_code is None else turn.failure_code.value)
            != expected_failure_code
        ):
            raise ValueError("private v5 completed turn binding differs")


def verify_persisted_probe_stage_v5(
    *,
    root: Path,
    private_root: Path,
    manifest: dict[str, Any],
    expected_public_probe: dict[str, Any],
    expected_private_probe: dict[str, Any],
    manifest_binding_raw_sha256: str,
    manifest_binding_semantic_sha256: str,
) -> None:
    """Read back the create-once probe before any replicate is authorized."""

    public_path = root / PUBLIC_RESULT_RELATIVES_V5["probe"]
    public = _verify_public_probe_v5(public_path)
    private_path = private_root / "local-mode-probe.json"
    private = _load_private_object_v5(private_path)
    binding_path = private_root / "manifest-binding.json"
    binding = _load_private_object_v5(binding_path)
    if (
        public != expected_public_probe
        or private != expected_private_probe
        or frozenset(private)
        != {
            "schema_version",
            "implementation_commit",
            "implementation_tree",
            "manifest_sha256",
            "supported",
            "provider_calls",
            "selected_mode",
            "failure_class",
            "safe_failure_code",
            "attempted_modes",
            "safe_failure",
            "probe_report",
            "probe_evidence_sha256",
        }
        or private.get("schema_version")
        != "dta-v22-pr-d-private-provider-compatibility-v5-probe.v1"
        or frozenset(binding)
        != {
            "schema_version",
            "implementation_commit",
            "implementation_tree",
            "manifest_sha256",
            "bound_at",
            "binding_sha256",
        }
        or binding.get("schema_version")
        != "dta-v22-pr-d-provider-compatibility-v5-manifest-binding.v1"
        or not _is_utc_timestamp(binding.get("bound_at"))
        or binding.get("binding_sha256")
        != semantic_sha256_v22(
            {key: value for key, value in binding.items() if key != "binding_sha256"}
        )
        or private.get("probe_evidence_sha256")
        != semantic_sha256_v22(
            {key: value for key, value in private.items() if key != "probe_evidence_sha256"}
        )
        or public.get("private_raw_sha256") != _raw_sha(private_path)
        or public.get("private_semantic_sha256") != semantic_sha256_v22(private)
        or public.get("manifest_binding_raw_sha256")
        != manifest_binding_raw_sha256
        or public.get("manifest_binding_semantic_sha256")
        != manifest_binding_semantic_sha256
        or _raw_sha(binding_path) != manifest_binding_raw_sha256
        or semantic_sha256_v22(binding) != manifest_binding_semantic_sha256
        or binding.get("manifest_sha256") != manifest.get("manifest_sha256")
        or binding.get("implementation_commit")
        != public.get("implementation_commit")
        or binding.get("implementation_tree") != public.get("implementation_tree")
    ):
        raise ValueError("persisted v5 probe stage binding differs")
    report_value = private.get("probe_report")
    if report_value is not None:
        report = _validate_persisted_json_v5(
            ProviderBoundaryProbeReportV5, report_value
        )
        report_failure = report.attempts[0].failure
        expected_failure = (
            None if report_failure is None else report_failure.model_dump(mode="json")
        )
        if (
            report.report_sha256 != public.get("probe_report_sha256")
            or private.get("safe_failure") != expected_failure
            or private.get("failure_class")
            != (None if report_failure is None else report_failure.failure_class.value)
            or private.get("safe_failure_code")
            != (
                None
                if report_failure is None
                else (report_failure.safe_code or report_failure.failure_class.value)
            )
        ):
            raise ValueError("persisted v5 probe report binding differs")


def verify_persisted_replicate_stage_v5(
    *,
    root: Path,
    private_root: Path,
    replicate_binding: dict[str, Any],
) -> None:
    """Read back one create-once replicate before the campaign may continue."""

    replicate_id = replicate_binding.get("replicate_id")
    if replicate_id not in {"A", "B"}:
        raise ValueError("persisted v5 replicate identity differs")
    public_path = root / PUBLIC_RESULT_RELATIVES_V5[str(replicate_id)]
    public, report = _verify_public_replicate(public_path)
    private_path = private_root / f"replicate-{str(replicate_id).lower()}.json"
    private = _load_private_object_v5(private_path)
    if (
        frozenset(private)
        != {"schema_version", "report", "completed_turns", "evidence_sha256"}
        or private.get("schema_version")
        != "dta-v22-pr-d-private-provider-compatibility-v5-replicate.v1"
        or private.get("evidence_sha256")
        != semantic_sha256_v22(
            {key: value for key, value in private.items() if key != "evidence_sha256"}
        )
        or private.get("report") != public.get("report")
        or _raw_sha(private_path) != replicate_binding.get("private_raw_sha256")
        or semantic_sha256_v22(private)
        != replicate_binding.get("private_semantic_sha256")
        or _raw_sha(public_path) != replicate_binding.get("public_raw_sha256")
        or semantic_sha256_v22(public)
        != replicate_binding.get("public_semantic_sha256")
        or report.report_sha256 != replicate_binding.get("report_sha256")
        or report.terminal.value != replicate_binding.get("terminal")
    ):
        raise ValueError("persisted v5 replicate stage binding differs")
    turns = tuple(
        _validate_persisted_json_v5(ProviderBoundaryTurnV5, value)
        for value in private.get("completed_turns", [])
    )
    _verify_private_completed_turn_bindings_v5(report.transitions, turns)


def verify_private_execution_v5(
    *,
    root: Path,
    private_root: Path,
    manifest: dict[str, Any],
    public_campaign: dict[str, Any],
) -> None:
    """Verify the local create-once v5 evidence against its public projection."""

    for component in reversed((private_root, *private_root.parents)):
        if component.exists() or component.is_symlink():
            component_detail = component.lstat()
            if stat.S_ISLNK(component_detail.st_mode):
                raise ValueError("private v5 evidence ancestry contains a symlink")
    detail = private_root.lstat()
    if (
        stat.S_ISLNK(detail.st_mode)
        or not stat.S_ISDIR(detail.st_mode)
        or stat.S_IMODE(detail.st_mode) != 0o700
        or detail.st_uid != os.getuid()
    ):
        raise ValueError("private v5 evidence root authority differs")
    manifest_sha256 = str(manifest["manifest_sha256"])
    manifest_binding = _verify_private_manifest_binding_v5(
        private_root=private_root,
        manifest_sha256=manifest_sha256,
        public_campaign=public_campaign,
    )
    probe_path = private_root / "local-mode-probe.json"
    probe = _load_private_object_v5(probe_path)
    probe_binding = public_campaign.get("probe_binding")
    exact_probe_projection_fields = (
        "manifest_sha256",
        "implementation_commit",
        "implementation_tree",
        "probe_evidence_sha256",
        "provider_calls",
        "supported",
        "selected_mode",
        "failure_class",
        "safe_failure_code",
        "attempted_modes",
    )
    private_probe_report = probe.get("probe_report")
    private_probe_report_sha = (
        private_probe_report.get("report_sha256")
        if isinstance(private_probe_report, dict)
        else None
    )
    if (
        not isinstance(probe_binding, dict)
        or frozenset(probe)
        != {
            "schema_version",
            "implementation_commit",
            "implementation_tree",
            "manifest_sha256",
            "supported",
            "provider_calls",
            "selected_mode",
            "failure_class",
            "safe_failure_code",
            "attempted_modes",
            "safe_failure",
            "probe_report",
            "probe_evidence_sha256",
        }
        or probe.get("schema_version")
        != "dta-v22-pr-d-private-provider-compatibility-v5-probe.v1"
        or frozenset(probe_binding) != _PROBE_BINDING_FIELDS_V5
        or probe.get("probe_evidence_sha256")
        != semantic_sha256_v22(
            {
                key: value
                for key, value in probe.items()
                if key != "probe_evidence_sha256"
            }
        )
        or probe.get("manifest_sha256") != manifest_sha256
        or probe.get("implementation_commit")
        != manifest_binding.get("implementation_commit")
        or probe.get("implementation_tree")
        != manifest_binding.get("implementation_tree")
        or _raw_sha(probe_path)
        != probe_binding.get("private_raw_sha256")
        or semantic_sha256_v22(probe) != probe_binding.get("private_semantic_sha256")
        or any(
            json.loads(json.dumps(probe.get(key), allow_nan=False))
            != json.loads(json.dumps(probe_binding.get(key), allow_nan=False))
            for key in exact_probe_projection_fields
        )
        or probe_binding.get("probe_report_sha256") != private_probe_report_sha
    ):
        raise ValueError("private v5 probe binding differs")
    if private_probe_report is not None:
        typed_probe_report = _validate_persisted_json_v5(
            ProviderBoundaryProbeReportV5, private_probe_report
        )
        report_failure = typed_probe_report.attempts[0].failure
        expected_failure = (
            None if report_failure is None else report_failure.model_dump(mode="json")
        )
        if (
            probe.get("safe_failure") != expected_failure
            or probe.get("failure_class")
            != (None if report_failure is None else report_failure.failure_class.value)
            or probe.get("safe_failure_code")
            != (
                None
                if report_failure is None
                else (report_failure.safe_code or report_failure.failure_class.value)
            )
        ):
            raise ValueError("private v5 probe failure projection differs")
    public_probe_path = root / PUBLIC_RESULT_RELATIVES_V5["probe"]
    public_probe = _verify_public_probe_v5(public_probe_path)
    if (
        _raw_sha(public_probe_path) != probe_binding.get("public_raw_sha256")
        or semantic_sha256_v22(public_probe)
        != probe_binding.get("public_semantic_sha256")
        or public_probe.get("result_sha256")
        != probe_binding.get("public_result_sha256")
        or public_probe.get("private_raw_sha256")
        != probe_binding.get("private_raw_sha256")
        or public_probe.get("private_semantic_sha256")
        != probe_binding.get("private_semantic_sha256")
        or public_probe.get("probe_report_sha256") != private_probe_report_sha
        or public_probe.get("safe_failure") != probe.get("safe_failure")
    ):
        raise ValueError("private/public v5 probe projection differs")
    public_replicates = {
        replicate_id: _load_object(root / PUBLIC_RESULT_RELATIVES_V5[replicate_id])
        for replicate_id in ("A", "B")
        if (root / PUBLIC_RESULT_RELATIVES_V5[replicate_id]).exists()
    }
    for replicate_binding in public_campaign.get("replicate_bindings", []):
        replicate_id = replicate_binding.get("replicate_id")
        if replicate_id not in public_replicates:
            raise ValueError("private v5 replicate lacks a public result")
        private_path = private_root / f"replicate-{str(replicate_id).lower()}.json"
        private = _load_private_object_v5(private_path)
        if (
            frozenset(private)
            != {"schema_version", "report", "completed_turns", "evidence_sha256"}
            or private.get("schema_version")
            != "dta-v22-pr-d-private-provider-compatibility-v5-replicate.v1"
            or not isinstance(private.get("completed_turns"), list)
            or
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
            raise ValueError("private v5 replicate binding differs")
        report = _validate_persisted_json_v5(
            ProviderProtocolReplicateReportV5, private.get("report")
        )
        turns = tuple(
            _validate_persisted_json_v5(ProviderBoundaryTurnV5, value)
            for value in private.get("completed_turns", [])
        )
        _verify_private_completed_turn_bindings_v5(report.transitions, turns)
    private_campaign_path = private_root / "campaign.json"
    private_campaign = _load_private_object_v5(private_campaign_path)
    expected_private_campaign_fields = _PUBLIC_CAMPAIGN_FIELDS_V5.difference(
        {"private_campaign_raw_sha256", "private_campaign_semantic_sha256"}
    )
    if (
        frozenset(private_campaign) != expected_private_campaign_fields
        or private_campaign.get("campaign_sha256")
        != semantic_sha256_v22(
            {
                key: value
                for key, value in private_campaign.items()
                if key != "campaign_sha256"
            }
        )
        or
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
        raise ValueError("private v5 campaign binding differs")

    replicate_roles = {
        f"replicate-{str(item.get('replicate_id')).lower()}.json"
        for item in public_campaign.get("replicate_bindings", [])
    }
    expected_private_roles = {
        "manifest-binding.json",
        "local-mode-probe.json",
        "campaign.json",
        *replicate_roles,
    }
    observed_private_roles: set[str] = set()
    for path in private_root.iterdir():
        detail = path.lstat()
        if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
            raise ValueError("private v5 evidence contains a non-regular artifact")
        observed_private_roles.add(path.name)
    if observed_private_roles != expected_private_roles:
        raise ValueError("private v5 evidence role set differs")


def _require_regular_public_file_v5(path: Path) -> None:
    detail = path.lstat()
    if stat.S_ISLNK(detail.st_mode) or not stat.S_ISREG(detail.st_mode):
        raise ValueError("v5 required public closure artifacts must be regular files")


def _verify_post_execution_artifacts_v5(
    root: Path,
    *,
    manifest: dict[str, Any],
    campaign: dict[str, Any],
) -> tuple[Path, ...]:
    required = (
        root / HUMAN_BRIEF_RELATIVE_V5,
        root / DISPOSITION_RELATIVE_V5,
        root / ADMIN_ATTESTATION_RELATIVE_V5,
    )
    if any(not path.exists() for path in required):
        raise ValueError("v5 required public closure artifacts are missing")
    for path in required:
        _require_regular_public_file_v5(path)

    implementation_commit = str(campaign["implementation_commit"])
    implementation_tree = str(campaign["implementation_tree"])
    _verify_commit_b_topology_v5(root, implementation_commit)
    changed_paths = sorted(_changed_paths(root, implementation_commit))

    disposition = _load_object(root / DISPOSITION_RELATIVE_V5)
    disposition_payload = dict(disposition)
    disposition_digest = disposition_payload.pop("disposition_sha256", None)
    ci_run_id = disposition.get("pre_execution_exact_head_ci_run_id")
    ci_run_url = disposition.get("pre_execution_exact_head_ci_run_url")
    if (
        tuple(disposition) != _DISPOSITION_FIELDS_V5
        or disposition.get("schema_version")
        != "dta-v22-pr-d-provider-compatibility-v5.current-disposition.v1"
        or disposition.get("goal_version") != GOAL_VERSION_V5
        or disposition.get("amendment_version") != AMENDMENT_VERSION_V5
        or disposition.get("decision_id") != "DEC-059"
        or disposition.get("implementation_commit") != implementation_commit
        or disposition.get("implementation_tree") != implementation_tree
        or disposition.get("manifest_sha256") != manifest.get("manifest_sha256")
        or disposition.get("campaign_sha256") != campaign.get("campaign_sha256")
        or disposition.get("pre_execution_exact_head_ci_head") != implementation_commit
        or not isinstance(ci_run_id, int)
        or isinstance(ci_run_id, bool)
        or ci_run_id <= 0
        or ci_run_url
        != f"https://github.com/Raidriar7170/EcomSRE-Agent/actions/runs/{ci_run_id}"
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
        raise ValueError("v5 review disposition binding differs")

    attestation = _load_object(root / ADMIN_ATTESTATION_RELATIVE_V5)
    _verify_closure_claim_count_types_v5(
        review_must_fix_count=disposition.get(
            "pre_execution_independent_review_must_fix_count"
        ),
        provider_call_count=attestation.get("provider_call_count"),
        observed_provider_calls=campaign.get("observed_provider_calls"),
    )
    attestation_payload = dict(attestation)
    record_digest = attestation_payload.pop("record_sha256", None)
    raw_hashes = attestation.get("artifact_raw_sha256_by_path")
    attestable_paths = [
        path
        for path in changed_paths
        if path != ADMIN_ATTESTATION_RELATIVE_V5.as_posix()
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
        tuple(attestation) != _ADMIN_ATTESTATION_FIELDS_V5
        or attestation.get("schema_version")
        != "dta-v22-pr-d-provider-compatibility-v5-administrative-attestation.v1"
        or attestation.get("goal_version") != GOAL_VERSION_V5
        or attestation.get("amendment_version") != AMENDMENT_VERSION_V5
        or attestation.get("decision_id") != "DEC-059"
        or attestation.get("repository") != "Raidriar7170/EcomSRE-Agent"
        or attestation.get("pr") != 60
        or attestation.get("starting_head") != STARTING_HEAD_V5
        or attestation.get("starting_tree") != STARTING_TREE_V5
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
        raise ValueError("v5 administrative attestation binding differs")

    brief_text = (root / HUMAN_BRIEF_RELATIVE_V5).read_text(encoding="utf-8")
    for marker in (
        "DEC-059",
        AMENDMENT_VERSION_V5,
        implementation_commit,
        str(campaign["terminal"]),
    ):
        if marker not in brief_text:
            raise ValueError("v5 Human Brief claim binding differs")
    return required


def verify_repository_v5(root: Path) -> dict[str, Any]:
    manifest = verify_pre_execution_admission_v5(root)
    campaign = verify_public_results_v5(root, manifest)
    if campaign is None:
        if _changed_paths(root, STARTING_HEAD_V5) != set(COMMIT_A_PATHS_V5):
            raise ValueError("v5 Commit A changed surface differs")
        _verify_public_leakage_v5(
            (
                root / MANIFEST_RELATIVE_V5,
                root / PROGRESS_RELATIVE_V5,
                root / "docs/DECISIONS.md",
            )
        )
        return {
            "schema_version": "dta-v22-pr-d-verification.v5",
            "status": "EXECUTION_READY",
            "execution_state": "V5_PRE_EXECUTION_READY",
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
    if not _changed_paths(root, implementation_commit).issubset(COMMIT_B_PATHS_V5):
        raise ValueError("v5 Commit B changed surface exceeds result-only authority")
    closure_paths = _verify_post_execution_artifacts_v5(
        root,
        manifest=manifest,
        campaign=campaign,
    )
    public_paths = (
        root / MANIFEST_RELATIVE_V5,
        root / PROGRESS_RELATIVE_V5,
        root / "docs/DECISIONS.md",
        *(
            root / value
            for value in PUBLIC_RESULT_RELATIVES_V5.values()
            if (root / value).exists()
        ),
        *closure_paths,
    )
    _verify_public_leakage_v5(public_paths)
    _verify_progress_v5(
        root,
        manifest_sha256=str(manifest["manifest_sha256"]),
        campaign=campaign,
    )
    return {
        "schema_version": "dta-v22-pr-d-verification.v5",
        "status": "PASS" if campaign["merge_ready"] else "BLOCKED",
        "execution_state": (
            "V5_COMPLETE_PASS" if campaign["merge_ready"] else "V5_COMPLETE_BLOCKED"
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
    print(json.dumps(verify_repository_v5(root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
