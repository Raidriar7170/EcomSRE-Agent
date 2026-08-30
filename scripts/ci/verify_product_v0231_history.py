#!/usr/bin/env python3
"""Verify Product v0.2.3 history, squash lineage, and private Baseline state."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    SquashMergeHistoryBindingV0231,
    admit_product_baseline_continuation_context_v0231,
)
from scripts.ci.product_squash_history_v0231 import (
    PRODUCT_IMPORT_PR_V0231,
    PRODUCT_IMPORT_SQUASH_V0231,
)
from scripts.ci.verify_product_v021_history import verify_product_v021_history
from scripts.ci.verify_product_v022_history import verify_product_v022_history
from scripts.ci.verify_product_v0221_history import verify_product_v0221_history
from scripts.ci.verify_product_v0222_history import verify_product_v0222_history
from scripts.ci.verify_product_v023_history import verify_product_v023_history


GOAL_VERSION_V0231 = "ecomsre-product-v0231-runtime-authority-continuity-nofault-v1"
PUBLIC_MAIN_BASE_V0231 = "613f6203e4a174b4549b912cb16ca7998cf6238c"
PREDECESSOR_HEAD_V0231 = "b15072c48acf8b143d0a950e7248a1684d3eedf0"
PREDECESSOR_TERMINAL_V0231 = "BLOCKED_ECOMSRE_PRODUCT_V023_NOFAULT_INFRASTRUCTURE"
SQUASH_HISTORY_PASS_V0231 = "ECOMSRE_PRODUCT_V0231_SQUASH_HISTORY_PASS"
HISTORY_AND_BASELINE_PASS_V0231 = "ECOMSRE_PRODUCT_V0231_HISTORY_AND_BASELINE_PASS"

_EXPECTED_PREDECESSOR_V0231 = {
    "pr": 80,
    "branch": "codex/product-v023-fresh-baseline-nofault",
    "head": PREDECESSOR_HEAD_V0231,
    "terminal": PREDECESSOR_TERMINAL_V0231,
    "baseline_readiness_terminal": "ECOMSRE_PRODUCT_V023_BASELINE_READINESS_PASS",
    "baseline_public_terminal": "BLOCKED_ECOMSRE_PRODUCT_V023_BASELINE_RESTART",
    "environment_id": "env-2b5c86f47f449acfc54cfcec",
    "active_baseline_id": "base-b25440a36089a8f0e6b9f1dc",
    "active_baseline_sha256": (
        "6d3d2d7a4854d1cfc2477746e7d0c940ed8a08644ebc69b7b91066eabe45ae64"
    ),
    "readiness_audit_sha256": (
        "dbe43c6f8a11d75aeb7b15f06f128c13f62cc9f396d3a115a87e3da46b872a87"
    ),
    "window_evaluation_parity_sha256": (
        "235c78749678c9d1dd53e919f0c366996afa4148d9462c5a3a2149b7653a7e54"
    ),
    "active_profile_sha256": (
        "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
    ),
    "nofault_execution_profile_sha256": (
        "7b580805f8dc86e1239811903044035f46e5d7eb10a239431bebbe18476c7e10"
    ),
    "preserved_runtime_read_authority_sha256": (
        "ea132c5d6e4498012404865b5210a8359262047f117f3e11a7c792b91f5e8a1c"
    ),
    "preserved_pilot_runtime_authority_sha256": (
        "bd1546ecdf961206d3c7a4c9c065bdb2882357da56dfd775ff5d6aed9edad57c"
    ),
    "preserved_connector_binding_sha256": (
        "ee49aaa2835b97645c639a3a9cae01471e51e6aa427e92f353d7b4fdf3840915"
    ),
    "preserved_resolved_compose_sha256": (
        "ae69ca454a77f189fe6c403f0473e3faa28a7352a4898dd881faf18fb70daa24"
    ),
    "frozen_blocker_report_sha256": (
        "2940fab104e7d76a464640c25048ebd0c88edcb56b18b4fe25ac72fecfae98d2"
    ),
    "frozen_progress_sha256": (
        "be58137b5cd30e761528b475a2211169f251d99edf3ee51938a7614b2076fa6a"
    ),
    "baseline_readiness_attempt_count": 1,
    "infrastructure_replacement_count": 1,
    "accepted_incident_count": 0,
    "diagnosis_count": 0,
    "fault_attempt_count": 0,
    "fault_family_count": 0,
    "knowledge_loop_campaign_count": 0,
    "knowledge_artifact_count": 0,
    "agent_writes": 0,
    "runbook_executions": 0,
    "action_authority": "NONE",
    "product_cleanup": "CLEAN",
    "demo_cleanup": "CLEAN",
}

_EXPECTED_TRACKED_ROLE_PATHS_V0231 = {
    "V023_BASELINE_PROFILE": "config/product-v023/baseline-readiness/profile.json",
    "V023_ENVIRONMENT": "config/product-v023/environment.otel-demo.json",
    "V023_PREDECESSOR_HISTORY": "config/product-v023/historical-results.v1.json",
    "V023_NOFAULT_PROFILE": "config/product-v023/nofault/profile.json",
    "V023_BASELINE_ATTEMPT": "docs/analysis/product-v023-baseline-attempt-1.json",
    "V023_BASELINE_PREFLIGHT": "docs/analysis/product-v023-baseline-preflight.json",
    "V023_BASELINE_READINESS": "docs/analysis/product-v023-baseline-readiness.json",
    "V023_BASELINE_REPORT": "docs/analysis/product-v023-baseline-readiness.md",
    "V023_PROFILE_BINDING": "docs/analysis/product-v023-profile-binding.json",
    "V023_PROGRESS": "docs/analysis/product-v023-progress.json",
}

_EXPECTED_PRIVATE_BINDING_V0231 = ProductV023PrivateStateBindingV0231(
    baseline_private_report_locator=(
        ".local/product-v023/baseline-readiness/runs/20260829T150806-1eaee825/"
        "private/attempt-completion.json"
    ),
    baseline_private_report_sha256=(
        "8b8cf766ca486503a4b1abd98d3f0934faa983e9bb5b445edabe691fbec0beae"
    ),
    product_data_root_locator=(
        ".local/product-v023/baseline-readiness/runs/20260829T150806-1eaee825/product"
    ),
    product_database_sha256=(
        "adf35ac7a3a6504baab7c8b8777030104dbdfb463b4853e50820845378453b04"
    ),
    product_database_wal_sha256=(
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    ),
    product_database_shm_sha256=(
        "fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb"
    ),
    nofault_blocker_locator=(
        ".local/product-v023/nofault/runs/20260829T153651-afbddc43/acceptance.json"
    ),
    nofault_blocker_sha256=(
        "2940fab104e7d76a464640c25048ebd0c88edcb56b18b4fe25ac72fecfae98d2"
    ),
    runtime_authority_locator=(
        ".local/product-v023/baseline-readiness/runs/20260829T150806-1eaee825/"
        "product/pilot/runtime-authority.json"
    ),
    runtime_authority_file_sha256=(
        "b51603e94550b1b2511313970f527ac87fad950c008d46fbd66239b085ccc066"
    ),
    resolved_compose_locator=(
        ".local/product-v023/baseline-readiness/runs/20260829T150806-1eaee825/"
        "private/demo/control/resolved-compose.json"
    ),
    resolved_compose_file_sha256=(
        "37c6d9e0e948fcbb702073f2ab273a5a6914219bf65868fbdb6b118836cb318f"
    ),
    flagd_file_locator=(
        ".local/product-v023/baseline-readiness/runs/20260829T150806-1eaee825/"
        "private/demo/runtime/flagd/demo.flagd.json"
    ),
    flagd_file_sha256=(
        "14bd13734d46566828779fd61b16e654cc260274a0e30ae9948371a9dbba5beb"
    ),
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.1 object is invalid: {path}")
    return payload


def _regular_bytes(root: Path, relative: str) -> bytes:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Product v0.2.3.1 historical path is not repository-relative")
    resolved = root / candidate
    metadata = resolved.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Product v0.2.3.1 historical path is not regular: {relative}")
    return resolved.read_bytes()


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    return subprocess.run(
        ("git", "show", f"{revision}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _require_commit(root: Path, revision: str) -> None:
    subprocess.run(
        ("git", "cat-file", "-e", f"{revision}^{{commit}}"),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_ancestry(root: Path, ancestor: str, descendant: str) -> None:
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=root,
        check=True,
        capture_output=True,
    )


def verify_product_v0231_squash_history(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    manifest = _load_object(
        manifest_path or root / "config/product-v0231/squash-history-bindings.v1.json"
    )
    if (
        manifest.get("schema_version")
        != "ecomsre.product.squash-history-bindings.v0231"
        or manifest.get("goal_version") != GOAL_VERSION_V0231
    ):
        raise ValueError("Product v0.2.3.1 squash manifest identity differs")
    raw_bindings = manifest.get("bindings")
    if not isinstance(raw_bindings, list) or len(raw_bindings) != 1:
        raise ValueError("Product v0.2.3.1 squash binding set differs")
    binding = SquashMergeHistoryBindingV0231.model_validate(raw_bindings[0])
    if (
        binding.source_pr != 75
        or binding.source_branch != "codex/product-v02-live-knowledge-loop-pilot"
        or binding.source_head != "a439f8882cd2fcdd3767f6bcfd5d955219fa1e15"
        or binding.source_terminal
        != "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
        or binding.import_pr != PRODUCT_IMPORT_PR_V0231
        or binding.import_squash_merge_commit != PRODUCT_IMPORT_SQUASH_V0231
        or binding.public_base != "8398a063de048064f160a7ffed236fbb3327b701"
        or len(binding.bound_files) != 8
    ):
        raise ValueError("Product v0.2.3.1 squash binding identity differs")
    _require_commit(root, binding.source_head)
    _require_commit(root, binding.import_squash_merge_commit)
    _require_ancestry(root, binding.public_base, binding.source_head)
    _require_ancestry(root, binding.import_squash_merge_commit, "HEAD")
    for item in binding.bound_files:
        current = _regular_bytes(root, item.path)
        if (
            len(current) != item.size_bytes
            or hashlib.sha256(current).hexdigest() != item.sha256
            or _git_bytes(root, binding.source_head, item.path) != current
            or _git_bytes(root, binding.import_squash_merge_commit, item.path)
            != current
        ):
            raise ValueError(
                f"Product v0.2.3.1 squash file binding differs: {item.path}"
            )

    legacy = {
        "v021": verify_product_v021_history(root)["status"],
        "v022": verify_product_v022_history(root)["status"],
        "v0221": verify_product_v0221_history(root)["status"],
        "v0222": verify_product_v0222_history(root)["status"],
        "v023": verify_product_v023_history(root)["status"],
    }
    expected_legacy = {
        "v021": "ECOMSRE_PRODUCT_V021_HISTORY_VERIFIED",
        "v022": "ECOMSRE_PRODUCT_V022_HISTORY_VERIFIED",
        "v0221": "ECOMSRE_PRODUCT_V0221_HISTORY_VERIFIED",
        "v0222": "ECOMSRE_PRODUCT_V0222_HISTORY_VERIFIED",
        "v023": "ECOMSRE_PRODUCT_V023_HISTORY_VERIFIED",
    }
    if legacy != expected_legacy:
        raise ValueError("Product v0.2.x historical verifier chain differs")
    return {
        "terminal": SQUASH_HISTORY_PASS_V0231,
        "binding_count": 1,
        "direct_bound_file_count": len(binding.bound_files),
        "import_pr": binding.import_pr,
        "import_squash_merge_commit": binding.import_squash_merge_commit,
        "legacy_verifiers": legacy,
    }


def _verify_tracked_predecessor_files(
    root: Path,
    manifest: dict[str, Any],
) -> int:
    files = manifest.get("tracked_files")
    if not isinstance(files, list) or len(files) != len(
        _EXPECTED_TRACKED_ROLE_PATHS_V0231
    ):
        raise ValueError("Product v0.2.3.1 tracked predecessor set differs")
    roles: set[str] = set()
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Product v0.2.3.1 tracked binding is malformed")
        relative = item.get("path")
        revision = item.get("revision")
        role = item.get("role")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or revision != PREDECESSOR_HEAD_V0231
            or not isinstance(role, str)
            or _EXPECTED_TRACKED_ROLE_PATHS_V0231.get(role) != relative
            or not isinstance(digest, str)
            or not isinstance(size, int)
            or role in roles
            or relative in paths
        ):
            raise ValueError("Product v0.2.3.1 tracked binding is malformed")
        current = _regular_bytes(root, relative)
        if (
            len(current) != size
            or hashlib.sha256(current).hexdigest() != digest
            or _git_bytes(root, PREDECESSOR_HEAD_V0231, relative) != current
        ):
            raise ValueError(f"Product v0.2.3 predecessor byte drift: {relative}")
        roles.add(role)
        paths.add(relative)
    if roles != set(_EXPECTED_TRACKED_ROLE_PATHS_V0231):
        raise ValueError("Product v0.2.3.1 tracked roles differ")
    return len(files)


def _verify_progress_semantics(root: Path, predecessor: dict[str, Any]) -> None:
    progress = _load_object(root / "docs/analysis/product-v023-progress.json")
    semantic = semantic_sha256_v22(
        {key: value for key, value in progress.items() if key != "progress_sha256"}
    )
    expected = {
        "terminal": predecessor["terminal"],
        "baseline_readiness_terminal": predecessor["baseline_readiness_terminal"],
        "baseline_public_terminal": predecessor["baseline_public_terminal"],
        "nofault_stage": "PREFLIGHT",
        "root_cause_code": (
            "CONTINUATION_FLAGD_BIND_PATH_CHANGED_RESOLVED_COMPOSE_AUTHORITY"
        ),
        "active_profile_sha256": predecessor["active_profile_sha256"],
        "environment_id": predecessor["environment_id"],
        "active_baseline_id": predecessor["active_baseline_id"],
        "active_baseline_sha256": predecessor["active_baseline_sha256"],
        "readiness_audit_sha256": predecessor["readiness_audit_sha256"],
        "preserved_runtime_read_authority_sha256": predecessor[
            "preserved_runtime_read_authority_sha256"
        ],
        "preserved_resolved_compose_sha256": predecessor[
            "preserved_resolved_compose_sha256"
        ],
        "private_nofault_report_sha256": predecessor["frozen_blocker_report_sha256"],
        "baseline_readiness_attempt_count": 1,
        "infrastructure_replacement_count": 1,
        "accepted_nofault_incident_count": 0,
        "product_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "fault_family_count": 0,
        "knowledge_loop_campaign_count": 0,
        "knowledge_artifact_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
        "product_cleanup": "CLEAN",
        "demo_cleanup": "CLEAN",
        "outer_baseline_unchanged": True,
        "queue_default_unchanged": True,
        "non_owned_resources_changed": False,
    }
    if (
        semantic != predecessor["frozen_progress_sha256"]
        or progress.get("progress_sha256") != semantic
        or any(progress.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("Product v0.2.3 frozen progress semantics differ")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    _write_json(path, payload)
    path.chmod(0o600)


def _contains_absolute_locator(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("/")
    if isinstance(value, dict):
        return any(_contains_absolute_locator(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_locator(item) for item in value)
    return False


def _expected_context_v0231() -> ProductBaselineContinuationContextV0231:
    return ProductBaselineContinuationContextV0231.build(
        predecessor_head=PREDECESSOR_HEAD_V0231,
        source_attempt_sha256=(
            "30e358e650866dfdf699ef6d1df8b858745ea6dd408b7c01e8c4c4ba959567be"
        ),
        source_private_report_sha256=(
            "8b8cf766ca486503a4b1abd98d3f0934faa983e9bb5b445edabe691fbec0beae"
        ),
        product_data_root_locator=_EXPECTED_PRIVATE_BINDING_V0231.product_data_root_locator,
        product_data_root_locator_sha256=(
            "70378caf2eb782dd820dd5b6386aa064ea9461014e7331e8d8e60e3a852b6064"
        ),
        environment_id=str(_EXPECTED_PREDECESSOR_V0231["environment_id"]),
        active_baseline_id=str(_EXPECTED_PREDECESSOR_V0231["active_baseline_id"]),
        active_baseline_sha256=str(
            _EXPECTED_PREDECESSOR_V0231["active_baseline_sha256"]
        ),
        readiness_audit_sha256=str(
            _EXPECTED_PREDECESSOR_V0231["readiness_audit_sha256"]
        ),
        parity_sha256=str(
            _EXPECTED_PREDECESSOR_V0231["window_evaluation_parity_sha256"]
        ),
        active_profile_sha256=str(
            _EXPECTED_PREDECESSOR_V0231["active_profile_sha256"]
        ),
        service_identity_sha256=(
            "1e420ccef98b5a2b4a881a7fe5ca94f2b3ce31b2947f30a4750071bb6043b487"
        ),
        capability_sha256=(
            "b278a6694b1c9596e291ee7cb514298319c4d3bb0989b0addb041c25690d511e"
        ),
        runtime_authority_path=_EXPECTED_PRIVATE_BINDING_V0231.runtime_authority_locator,
        runtime_authority_sha256=str(
            _EXPECTED_PREDECESSOR_V0231["preserved_pilot_runtime_authority_sha256"]
        ),
    )


def _history_result_v0231(
    *,
    context: ProductBaselineContinuationContextV0231,
    squash: dict[str, object],
    tracked_count: int,
    binding: ProductV023PrivateStateBindingV0231,
) -> dict[str, object]:
    return {
        "terminal": HISTORY_AND_BASELINE_PASS_V0231,
        "squash_terminal": squash["terminal"],
        "predecessor_terminal": PREDECESSOR_TERMINAL_V0231,
        "predecessor_head": PREDECESSOR_HEAD_V0231,
        "tracked_file_count": tracked_count,
        "private_binding_count": len(type(binding).model_fields),
        "context_sha256": context.context_sha256,
        "source_attempt_sha256": context.source_attempt_sha256,
        "active_baseline_id": context.active_baseline_id,
        "active_baseline_sha256": context.active_baseline_sha256,
        "runtime_authority_sha256": context.runtime_authority_sha256,
        "fault_attempt_count": 0,
        "accepted_incident_count": 0,
        "diagnosis_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }


def _squash_report_v0231(squash: dict[str, object]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "ecomsre.product.squash-history-verification.v0231",
        **squash,
    }
    report["verification_sha256"] = semantic_sha256_v22(report)
    return report


def _predecessor_audit_v0231(result: dict[str, object]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "ecomsre.product.predecessor-audit.v0231",
        **result,
        "product_cleanup": "CLEAN",
        "demo_cleanup": "CLEAN",
        "private_baseline_admitted": True,
    }
    report["audit_sha256"] = semantic_sha256_v22(report)
    return report


def verify_product_v0231_written_reports(
    project_root: Path,
    *,
    predecessor_audit_path: Path | None = None,
    squash_report_path: Path | None = None,
    context_path: Path | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    predecessor_audit = _load_object(
        predecessor_audit_path
        or root / "docs/analysis/product-v0231-predecessor-audit.json"
    )
    squash_report = _load_object(
        squash_report_path
        or root / "docs/analysis/product-v0231-squash-history-verification.json"
    )
    context_payload = _load_object(
        context_path
        or root / "docs/analysis/product-v0231-baseline-continuation-context.json"
    )
    context = ProductBaselineContinuationContextV0231.model_validate(context_payload)
    expected_context = _expected_context_v0231()
    manifest = _load_object(root / "config/product-v0231/historical-results.v1.json")
    if (
        manifest.get("schema_version") != "ecomsre.product-v0231.historical-results.v1"
        or manifest.get("goal_version") != GOAL_VERSION_V0231
        or manifest.get("public_main_base") != PUBLIC_MAIN_BASE_V0231
        or manifest.get("predecessor") != _EXPECTED_PREDECESSOR_V0231
    ):
        raise ValueError("Product v0.2.3.1 written report source differs")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest.get("private_state")
    )
    if binding != _EXPECTED_PRIVATE_BINDING_V0231:
        raise ValueError("Product v0.2.3.1 written report private binding differs")
    _require_commit(root, PREDECESSOR_HEAD_V0231)
    tracked_count = _verify_tracked_predecessor_files(root, manifest)
    _verify_progress_semantics(root, _EXPECTED_PREDECESSOR_V0231)
    squash = verify_product_v0231_squash_history(root)
    expected_result = _history_result_v0231(
        context=expected_context,
        squash=squash,
        tracked_count=tracked_count,
        binding=binding,
    )
    expected_predecessor_audit = _predecessor_audit_v0231(expected_result)
    expected_squash_report = _squash_report_v0231(squash)
    if (
        _contains_absolute_locator(predecessor_audit)
        or _contains_absolute_locator(squash_report)
        or _contains_absolute_locator(context_payload)
        or context != expected_context
        or context_payload != expected_context.model_dump(mode="json")
        or predecessor_audit != expected_predecessor_audit
        or squash_report != expected_squash_report
    ):
        raise ValueError("Product v0.2.3.1 written report binding differs")
    return {
        "terminal": HISTORY_AND_BASELINE_PASS_V0231,
        "squash_terminal": SQUASH_HISTORY_PASS_V0231,
        "context_sha256": context.context_sha256,
        "predecessor_audit_sha256": predecessor_audit["audit_sha256"],
        "squash_verification_sha256": squash_report["verification_sha256"],
    }


def verify_product_v0231_history(
    project_root: Path,
    *,
    predecessor_root: Path,
    manifest_path: Path | None = None,
    squash_manifest_path: Path | None = None,
    write_reports: bool = False,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    manifest = _load_object(
        manifest_path or root / "config/product-v0231/historical-results.v1.json"
    )
    if (
        manifest.get("schema_version") != "ecomsre.product-v0231.historical-results.v1"
        or manifest.get("goal_version") != GOAL_VERSION_V0231
        or manifest.get("public_main_base") != PUBLIC_MAIN_BASE_V0231
        or manifest.get("predecessor") != _EXPECTED_PREDECESSOR_V0231
    ):
        raise ValueError("Product v0.2.3.1 predecessor identity differs")
    predecessor = _EXPECTED_PREDECESSOR_V0231
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest.get("private_state")
    )
    if binding != _EXPECTED_PRIVATE_BINDING_V0231:
        raise ValueError("Product v0.2.3.1 private binding differs")

    _require_commit(root, PREDECESSOR_HEAD_V0231)
    _require_ancestry(root, PUBLIC_MAIN_BASE_V0231, PREDECESSOR_HEAD_V0231)
    _require_ancestry(root, PREDECESSOR_HEAD_V0231, "HEAD")
    _require_ancestry(root, PUBLIC_MAIN_BASE_V0231, "HEAD")
    tracked_count = _verify_tracked_predecessor_files(root, manifest)
    _verify_progress_semantics(root, predecessor)
    squash = verify_product_v0231_squash_history(root, squash_manifest_path)

    predecessor_checkout = Path(predecessor_root).expanduser().resolve(strict=True)
    actual_predecessor_head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=predecessor_checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual_predecessor_head != PREDECESSOR_HEAD_V0231:
        raise ValueError("Product v0.2.3 predecessor checkout HEAD differs")

    context = admit_product_baseline_continuation_context_v0231(
        predecessor_root=predecessor_checkout,
        binding=binding,
        predecessor=predecessor,
    )
    if context != _expected_context_v0231():
        raise ValueError("Product v0.2.3 private Baseline context differs")
    result = _history_result_v0231(
        context=context,
        squash=squash,
        tracked_count=tracked_count,
        binding=binding,
    )
    if write_reports:
        squash_report = _squash_report_v0231(squash)
        predecessor_audit = _predecessor_audit_v0231(result)
        _write_json(
            root / "docs/analysis/product-v0231-squash-history-verification.json",
            squash_report,
        )
        _write_json(
            root / "docs/analysis/product-v0231-predecessor-audit.json",
            predecessor_audit,
        )
        _write_json(
            root / "docs/analysis/product-v0231-baseline-continuation-context.json",
            context.model_dump(mode="json"),
        )
        private_locator = {
            "schema_version": "ecomsre.product.private-locator.v0231",
            "predecessor_checkout": str(predecessor_checkout),
            "predecessor_head": PREDECESSOR_HEAD_V0231,
            "product_data_root": str(
                predecessor_checkout / binding.product_data_root_locator
            ),
            "product_data_root_locator_sha256": (
                context.product_data_root_locator_sha256
            ),
            "flagd_directory": str(
                (predecessor_checkout / binding.flagd_file_locator).parent
            ),
        }
        private_locator["locator_sha256"] = semantic_sha256_v22(private_locator)
        _write_private_json(
            root / ".local/product-v0231/predecessor-locator.json",
            private_locator,
        )
    reports = verify_product_v0231_written_reports(root)
    if reports["context_sha256"] != context.context_sha256:
        raise ValueError("Product v0.2.3.1 written context differs from admission")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--predecessor-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--squash-manifest", type=Path)
    parser.add_argument("--write-reports", action="store_true")
    arguments = parser.parse_args(argv)
    result = verify_product_v0231_history(
        arguments.project_root,
        predecessor_root=arguments.predecessor_root,
        manifest_path=arguments.manifest,
        squash_manifest_path=arguments.squash_manifest,
        write_reports=arguments.write_reports,
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "HISTORY_AND_BASELINE_PASS_V0231",
    "PREDECESSOR_HEAD_V0231",
    "PREDECESSOR_TERMINAL_V0231",
    "SQUASH_HISTORY_PASS_V0231",
    "verify_product_v0231_history",
    "verify_product_v0231_squash_history",
    "verify_product_v0231_written_reports",
)
