#!/usr/bin/env python3
"""Verify the frozen Product v0.2.3.1 predecessor for v0.2.3.2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.product_state_clone_v0232 import (
    HISTORY_AND_STATE_PASS_V0232,
    ProductStateCloneV0232,
    ProductStateSourceV0232,
)
from scripts.ci.verify_product_v0231_result import verify_product_v0231_result


STARTING_MAIN_V0232 = "73fe478886a4f0875b4d60b07b3600e8aae02132"
PREDECESSOR_HEAD_V0232 = "7ee7eca638edd388c8cba46e4092228fdbcc1008"
PREDECESSOR_TERMINAL_V0232 = "ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED"
HISTORY_VERIFIED_V0232 = "ECOMSRE_PRODUCT_V0232_HISTORY_VERIFIED"
SOURCE_REPOSITORY_HEAD_V0232 = "b15072c48acf8b143d0a950e7248a1684d3eedf0"
SOURCE_REPOSITORY_BRANCH_V0232 = "codex/product-v023-fresh-baseline-nofault"
SOURCE_LOCATOR_V0232 = (
    ".local/product-v023/baseline-readiness/runs/"
    "20260829T150806-1eaee825/product"
)
_REASONS_V0232 = (
    "CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED",
    "FRESH_HEALTHY_RUNTIME_MISSING",
    "HEALTHY_TRAFFIC_FAILED_OR_UNBOUND",
    "LOGS_PROFILE_BINDING_MISSING",
)
_SHA256 = "7860a121492fbd37bd8a94995bbc80fc124aed6d9be37b4cb915ed0eed3d2f73"
_TRACKED_ROLES_V0232 = {
    "V0231_NOFAULT_ACCEPTANCE_JSON": (
        "docs/results/product-v0231-nofault-acceptance.json"
    ),
    "V0231_NOFAULT_ACCEPTANCE_MD": (
        "docs/results/product-v0231-nofault-acceptance.md"
    ),
    "V0231_LIMITATIONS": "docs/results/product-v0231-limitations.md",
    "V0231_CONTINUATION_SESSION": (
        "docs/analysis/product-v0231-continuation-session-1.json"
    ),
    "V0231_RUNTIME_AUTHORITY_DESCRIPTOR": (
        "docs/analysis/product-v0231-runtime-authority-descriptor.json"
    ),
    "V0231_FLAGD_BIND_DESCRIPTOR": (
        "docs/analysis/product-v0231-flagd-bind-descriptor.json"
    ),
    "V0231_BASELINE_CONTINUATION_CONTEXT": (
        "docs/analysis/product-v0231-baseline-continuation-context.json"
    ),
    "V0231_BASELINE_RESTART": (
        "docs/analysis/product-v0231-baseline-restart.json"
    ),
    "V0231_KNOWLEDGE_LOOP_HANDOFF": (
        "docs/analysis/product-v0231-knowledge-loop-handoff.json"
    ),
    "V0231_FINAL_REVIEW": "docs/external-reviews/product-v0231-final-review.md",
}
_PRIVATE_STATE_V0232 = {
    "acceptance_locator": (
        ".local/product-v0231/continuation-sessions/session-1/acceptance.json"
    ),
    "acceptance_sha256": (
        "2f3001aa40e57844d166c7507f5df4d481635ce12fafa2e846221e6b9d72100f"
    ),
    "acceptance_size_bytes": 175020,
    "traffic_result_sha256": (
        "4c8d838c8c2e4a773e4c45353a84620367ef6647140b88ccdbed8f802b2796ab"
    ),
}
_EVIDENCE_PREFLIGHT_CASES_V0232 = (
    (
        "01_FRESH_RUNTIME_EXPLICIT",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_FULLY_SUPPORTED",
        (),
        (),
    ),
    (
        "02_STALE_RUNTIME",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED",
        (
            "CONNECTOR_PROVENANCE_INVALID",
            "FRESH_HEALTHY_RUNTIME_MISSING",
        ),
        (),
    ),
    (
        "03_ACTIVE_P01_EXPLICIT",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_FULLY_SUPPORTED",
        (),
        (),
    ),
    (
        "04_LOGS_WITHOUT_PROFILE",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED",
        ("LOGS_PROFILE_BINDING_MISSING",),
        (),
    ),
    (
        "05_SOURCE_FAILURE_BOUND",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_CAPABILITY_LIMITED",
        (),
        (),
    ),
    (
        "06_SOURCE_LIMITATION_UNBOUND",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED",
        ("CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED",),
        (),
    ),
    (
        "07_ALGORITHMIC_REASON_SEPARATED",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED",
        ("CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED",),
        ("ALGORITHMIC_REASON_MASQUERADES_AS_CAPABILITY",),
    ),
    (
        "08_NO_INCIDENT_COMPLETE",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_FULLY_SUPPORTED",
        (),
        (),
    ),
    (
        "09_INSUFFICIENT_EVIDENCE_BOUND",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_CAPABILITY_LIMITED",
        (),
        (),
    ),
    (
        "10_FALSE_OPEN_WORLD_HEALTHY",
        "ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED",
        ("FALSE_INCIDENT_TERMINAL",),
        (),
    ),
)


def _evidence_preflight_cases_match(payload: Mapping[str, Any]) -> bool:
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != len(
        _EVIDENCE_PREFLIGHT_CASES_V0232
    ):
        return False
    expected_keys = {
        "case_id",
        "expected_terminal",
        "observed_terminal",
        "reasons",
        "assessment_sha256",
        "passed",
    }
    for case, (case_id, terminal, required, forbidden) in zip(
        cases,
        _EVIDENCE_PREFLIGHT_CASES_V0232,
        strict=True,
    ):
        if not isinstance(case, dict) or set(case) != expected_keys:
            return False
        reasons = case.get("reasons")
        digest = case.get("assessment_sha256")
        if (
            case.get("case_id") != case_id
            or case.get("expected_terminal") != terminal
            or case.get("observed_terminal") != terminal
            or case.get("passed") is not True
            or not isinstance(reasons, list)
            or reasons != sorted(set(reasons))
            or reasons != list(required)
            or set(forbidden).intersection(reasons)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return False
    return True


def expected_source_repository_binding_v0232() -> dict[str, object]:
    body: dict[str, object] = {
        "source_repository_head": SOURCE_REPOSITORY_HEAD_V0232,
        "source_repository_branch": SOURCE_REPOSITORY_BRANCH_V0232,
        "source_locator": SOURCE_LOCATOR_V0232,
        "source_locator_resolved": True,
    }
    return {
        **body,
        "binding_sha256": semantic_sha256_v22(body),
    }


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    result = subprocess.run(
        ("git", "show", f"{revision}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _require_ancestry(root: Path, ancestor: str, descendant: str) -> None:
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_commit(root: Path, revision: str) -> None:
    subprocess.run(
        ("git", "cat-file", "-e", f"{revision}^{{commit}}"),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _expected_predecessor() -> dict[str, object]:
    return {
        "pr": 81,
        "branch": "codex/product-v0231-runtime-authority-nofault-successor",
        "head": PREDECESSOR_HEAD_V0232,
        "merge_commit": STARTING_MAIN_V0232,
        "terminal": PREDECESSOR_TERMINAL_V0232,
        "acceptance_reasons": list(_REASONS_V0232),
        "environment_id": "env-2b5c86f47f449acfc54cfcec",
        "active_baseline_id": "base-b25440a36089a8f0e6b9f1dc",
        "active_baseline_sha256": (
            "6d3d2d7a4854d1cfc2477746e7d0c940ed8a08644ebc69b7b91066eabe45ae64"
        ),
        "active_profile_sha256": (
            "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
        ),
        "runtime_continuity_descriptor_sha256": (
            "b103990c21d1804177a5d15900252259481e520dc2c5380547db0754c76c2e65"
        ),
        "runtime_authority_proof_sha256": (
            "30ba40431f7581460b693c431cc85bed4924e73049d3c4cb6a1ce55300b41e1d"
        ),
        "flagd_bind_descriptor_sha256": (
            "ecd2bffefe79fb7cb356e356a10278bd276d849ad5dd1e220cd0d3e77c0729e9"
        ),
        "baseline_restart_proof_sha256": (
            "afb5f57d688741d547b1ab1bc97d46e32a86431ea5e22e990fb3387dbac77333"
        ),
        "nofault_result_sha256": _SHA256,
        "planned_transactions": 30,
        "completed_transactions": 1,
        "failed_transactions": 1,
        "runtime_continuation_session_count": 1,
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_attempt_count": 0,
        "fault_family_count": 0,
        "knowledge_loop_campaign_count": 0,
        "knowledge_artifact_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "action_authority": "NONE",
        "product_cleanup": "CLEAN",
        "demo_cleanup": "CLEAN",
    }


def _verify_acceptance_semantics(root: Path) -> None:
    result = verify_product_v0231_result(root)
    expected = {
        "terminal": "ECOMSRE_PRODUCT_V0231_NOFAULT_ACCEPTANCE_COMPLETE",
        "measured_terminal": PREDECESSOR_TERMINAL_V0232,
        "live_session_count": 1,
        "incident_count": 1,
        "diagnosis_count": 1,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
    }
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError("Product v0.2.3.1 strict result verification differs")


def verify_product_v0232_private_result(path: Path) -> dict[str, object]:
    private_path = Path(path)
    if private_path.is_symlink() or not private_path.is_file():
        raise ValueError("Product v0.2.3.1 private acceptance path differs")
    raw = private_path.read_bytes()
    if (
        len(raw) != _PRIVATE_STATE_V0232["acceptance_size_bytes"]
        or hashlib.sha256(raw).hexdigest()
        != _PRIVATE_STATE_V0232["acceptance_sha256"]
    ):
        raise ValueError("Product v0.2.3.1 private acceptance bytes differ")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3.1 private acceptance differs")
    result = payload.get("result")
    traffic = payload.get("traffic_result")
    if (
        payload.get("schema_version")
        != "ecomsre.product.private-nofault-acceptance.v0231"
        or not isinstance(result, dict)
        or result.get("terminal") != PREDECESSOR_TERMINAL_V0232
        or result.get("result_sha256") != _SHA256
        or not isinstance(traffic, dict)
        or traffic.get("planned_request_count") != 30
        or traffic.get("completed_request_count") != 1
        or traffic.get("error_count") != 1
        or traffic.get("passed") is not False
        or traffic.get("result_sha256")
        != _PRIVATE_STATE_V0232["traffic_result_sha256"]
    ):
        raise ValueError("Product v0.2.3.1 private traffic result differs")
    return {
        "terminal": "ECOMSRE_PRODUCT_V0232_PRIVATE_HISTORY_VERIFIED",
        **_PRIVATE_STATE_V0232,
        "planned_transactions": 30,
        "completed_transactions": 1,
        "failed_transactions": 1,
    }


def verify_product_v0232_history(
    root: Path,
    *,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = root.resolve()
    manifest = _load_object(
        manifest_path or root / "config/product-v0232/historical-results.v1.json"
    )
    if (
        manifest.get("schema_version")
        != "ecomsre.product-v0232.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v0232-healthy-traffic-evidence-nofault-v1"
        or manifest.get("starting_main") != STARTING_MAIN_V0232
        or manifest.get("predecessor") != _expected_predecessor()
        or manifest.get("private_state") != _PRIVATE_STATE_V0232
    ):
        raise ValueError("Product v0.2.3.2 predecessor identity differs")
    _require_commit(root, PREDECESSOR_HEAD_V0232)
    _require_commit(root, STARTING_MAIN_V0232)
    _require_ancestry(root, STARTING_MAIN_V0232, "HEAD")
    tracked = manifest.get("tracked_files")
    if not isinstance(tracked, list) or len(tracked) != 10:
        raise ValueError("Product v0.2.3.2 historical tracked files differ")
    roles: set[str] = set()
    for item in tracked:
        if not isinstance(item, dict):
            raise ValueError("Product v0.2.3.2 historical binding is invalid")
        relative = item.get("path")
        role = item.get("role")
        revision = item.get("revision")
        if (
            not isinstance(relative, str)
            or not isinstance(role, str)
            or role in roles
            or _TRACKED_ROLES_V0232.get(role) != relative
            or revision != STARTING_MAIN_V0232
            or not isinstance(item.get("sha256"), str)
            or not isinstance(item.get("size_bytes"), int)
        ):
            raise ValueError("Product v0.2.3.2 historical binding differs")
        roles.add(role)
        local_path = root / relative
        if (
            local_path.is_symlink()
            or not local_path.is_file()
            or not local_path.resolve(strict=True).is_relative_to(root)
        ):
            raise ValueError(f"Product v0.2.3.1 frozen path differs: {relative}")
        local_bytes = local_path.read_bytes()
        main_bytes = _git_bytes(root, revision, relative)
        source_head_bytes = _git_bytes(root, PREDECESSOR_HEAD_V0232, relative)
        if (
            local_bytes != main_bytes
            or local_bytes != source_head_bytes
            or len(local_bytes) != item["size_bytes"]
            or hashlib.sha256(local_bytes).hexdigest() != item["sha256"]
        ):
            raise ValueError(f"Product v0.2.3.1 frozen bytes differ: {relative}")
    if roles != set(_TRACKED_ROLES_V0232):
        raise ValueError("Product v0.2.3.2 historical role set differs")
    _verify_acceptance_semantics(root)
    return {
        "terminal": HISTORY_VERIFIED_V0232,
        "starting_main": STARTING_MAIN_V0232,
        "predecessor_head": PREDECESSOR_HEAD_V0232,
        "predecessor_terminal": PREDECESSOR_TERMINAL_V0232,
        "tracked_file_count": len(tracked),
    }


def _require_seal(payload: dict[str, Any], field: str) -> None:
    body = dict(payload)
    digest = body.pop(field, None)
    if digest != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.2 {field} differs")


def verify_product_v0232_written_reports(
    root: Path,
    *,
    audit_path: Path | None = None,
    clone_path: Path | None = None,
    progress_path: Path | None = None,
    evidence_preflight_path: Path | None = None,
    expected_progress_terminal: str | None = None,
    expected_progress_increment: int | None = None,
    expected_offline_changed_iteration_count: int | None = None,
    expected_progress_bindings: Mapping[str, object] | None = None,
) -> dict[str, object]:
    root = root.resolve()
    audit = _load_object(
        audit_path or root / "docs/analysis/product-v0232-predecessor-audit.json"
    )
    clone_payload = _load_object(
        clone_path or root / "docs/analysis/product-v0232-product-state-clone.json"
    )
    progress = _load_object(
        progress_path or root / "docs/analysis/product-v0232-progress.json"
    )
    _require_seal(audit, "audit_sha256")
    _require_seal(progress, "progress_sha256")
    source = ProductStateSourceV0232.model_validate(audit.get("source_state"))
    clone = ProductStateCloneV0232.model_validate(clone_payload)
    history = audit.get("history")
    private_history = audit.get("private_history")
    source_repository_binding = audit.get("source_repository_binding")
    expected_history = verify_product_v0232_history(root)
    expected_private_history = {
        "terminal": "ECOMSRE_PRODUCT_V0232_PRIVATE_HISTORY_VERIFIED",
        **_PRIVATE_STATE_V0232,
        "planned_transactions": 30,
        "completed_transactions": 1,
        "failed_transactions": 1,
    }
    expected_zero_counters = {
        "live_traffic_preflight_attempt_count": 0,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
    }
    evidence_preflight: dict[str, Any] | None = None
    if expected_progress_increment is None:
        observed_increment = progress.get("increment")
        if observed_increment == 1:
            expected_progress_terminal = HISTORY_AND_STATE_PASS_V0232
            expected_progress_increment = 1
            expected_offline_changed_iteration_count = 1
        elif observed_increment == 2:
            expected_progress_terminal = "ECOMSRE_PRODUCT_V0232_TRAFFIC_CONTRACT_PASS"
            expected_progress_increment = 2
            expected_offline_changed_iteration_count = 2
        elif observed_increment == 3:
            expected_progress_terminal = (
                "ECOMSRE_PRODUCT_V0232_EVIDENCE_BINDING_CONTRACT_PASS"
            )
            expected_progress_increment = 3
            expected_offline_changed_iteration_count = 3
        else:
            raise ValueError("Product v0.2.3.2 progress lifecycle differs")
    if expected_progress_increment == 3:
        evidence_preflight = _load_object(
            evidence_preflight_path
            or root / "docs/analysis/product-v0232-evidence-binding-preflight.json"
        )
        _require_seal(evidence_preflight, "preflight_sha256")
        if (
            evidence_preflight.get("schema_version")
            != "ecomsre.product.evidence-binding-preflight.v0232"
            or evidence_preflight.get("terminal")
            != "ECOMSRE_PRODUCT_V0232_EVIDENCE_BINDING_CONTRACT_PASS"
            or evidence_preflight.get("case_count") != 10
            or evidence_preflight.get("passed_case_count") != 10
            or not _evidence_preflight_cases_match(evidence_preflight)
            or evidence_preflight.get("predecessor_result_verified") is not True
            or evidence_preflight.get("evidence_bundle_v1_compatible") is not True
            or evidence_preflight.get("index_deterministic") is not True
            or evidence_preflight.get("index_seal_rejects_mutation") is not True
            or evidence_preflight.get("index_immutable_persistence") is not True
            or evidence_preflight.get("index_deterministic_and_immutable") is not True
            or any(
                evidence_preflight.get(key) != 0
                for key in (
                    "agent_writes",
                    "runbook_executions",
                    "provider_calls",
                )
            )
        ):
            raise ValueError("Product v0.2.3.2 Evidence preflight binding differs")
        derived_progress_bindings = {
            "evidence_binding_preflight_sha256": evidence_preflight[
                "preflight_sha256"
            ],
            "reference_evidence_index_sha256": evidence_preflight[
                "reference_evidence_index_sha256"
            ],
        }
        if expected_progress_bindings is None:
            expected_progress_bindings = derived_progress_bindings
        else:
            if any(
                key in expected_progress_bindings
                and expected_progress_bindings[key] != value
                for key, value in derived_progress_bindings.items()
            ):
                raise ValueError(
                    "Product v0.2.3.2 expected Evidence progress binding differs"
                )
            expected_progress_bindings = {
                **expected_progress_bindings,
                **derived_progress_bindings,
            }
    if (
        expected_progress_terminal is None
        or expected_offline_changed_iteration_count is None
    ):
        raise ValueError("Product v0.2.3.2 expected progress binding is incomplete")
    if (
        audit.get("terminal") != HISTORY_AND_STATE_PASS_V0232
        or audit.get("source_product_process_owner_count") != 0
        or audit.get("source_clone_count") != 1
        or audit.get("clone_sha256") != clone.clone_sha256
        or history != expected_history
        or private_history != expected_private_history
        or source_repository_binding != expected_source_repository_binding_v0232()
        or clone.source_locator != source.source_locator
        or clone.source_database_file_sha256_before
        != source.source_database_file_sha256
        or clone.source_database_file_sha256_after
        != source.source_database_file_sha256
        or clone.source_database_logical_sha256
        != source.source_database_logical_sha256
        or clone.source_object_inventory_sha256
        != source.source_object_inventory_sha256
        or clone.source_runtime_file_inventory_sha256
        != source.source_runtime_file_inventory_sha256
        or clone.source_counts != source.source_counts
        or clone.source_environment_id != source.source_environment_id
        or clone.source_active_baseline_id != source.source_active_baseline_id
        or clone.source_active_baseline_sha256
        != source.source_active_baseline_sha256
        or clone.source_profile_sha256 != source.source_profile_sha256
        or progress.get("terminal") != expected_progress_terminal
        or progress.get("increment") != expected_progress_increment
        or progress.get("history_terminal") != HISTORY_VERIFIED_V0232
        or progress.get("source_clone_count") != 1
        or progress.get("offline_changed_iteration_count")
        != expected_offline_changed_iteration_count
        or progress.get("action_authority") != "NONE"
        or progress.get("clone_sha256") != clone.clone_sha256
        or any(progress.get(key) != value for key, value in expected_zero_counters.items())
        or any(
            progress.get(key) != value
            for key, value in (expected_progress_bindings or {}).items()
        )
    ):
        raise ValueError("Product v0.2.3.2 written report binding differs")
    public_payloads = [audit, clone_payload, progress]
    if evidence_preflight is not None:
        public_payloads.append(evidence_preflight)
    public_bytes = b"\n".join(
        json.dumps(payload, sort_keys=True).encode("utf-8")
        for payload in public_payloads
    )
    if b"/Users/" in public_bytes or b"/home/" in public_bytes:
        raise ValueError("Product v0.2.3.2 public report leaks a local locator")
    return {
        "terminal": HISTORY_AND_STATE_PASS_V0232,
        "source_clone_count": 1,
        "clone_sha256": clone.clone_sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    arguments = parser.parse_args(argv)
    print(json.dumps(verify_product_v0232_history(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SOURCE_LOCATOR_V0232",
    "SOURCE_REPOSITORY_BRANCH_V0232",
    "SOURCE_REPOSITORY_HEAD_V0232",
    "expected_source_repository_binding_v0232",
    "verify_product_v0232_history",
    "verify_product_v0232_private_result",
    "verify_product_v0232_written_reports",
)
