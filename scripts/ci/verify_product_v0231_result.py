#!/usr/bin/env python3
"""Verify the frozen public Product v0.2.3.1 No-Fault result."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.nofault_acceptance_v0231 import (
    NOFAULT_ACCEPTANCE_COMPLETE_V0231,
    NOFAULT_NOT_SUPPORTED_V0231,
    NoFaultAcceptanceResultV0231,
    NoFaultCampaignV0231,
    NoFaultProfileBindingV0231,
)
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NoFaultExecutionProfileV023,
)
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    FlagdBindDescriptorV0231,
    RuntimeAuthorityContinuityDescriptorV0231,
)
from ecomsre.product.pilot.runtime_session_v0231 import (
    BaselineRestartProofV0231,
    RuntimeAuthorityContinuityProofV0231,
    RuntimeContinuationSessionLedgerV0231,
)


EXECUTION_HEAD_V0231 = "e2c2f640d34a9bd928e32d8394894fd54d93722a"
FROZEN_EVIDENCE_COMMIT_V0231 = "505f16eb344e8dd6253c16437ff7e0ba8e5debab"
RUNTIME_PASS_V0231 = "ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS"
RESTART_PASS_V0231 = "ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS"
HANDOFF_NOT_AUTHORIZED_V0231 = (
    "ECOMSRE_PRODUCT_V0231_KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED"
)
_REASONS_V0231 = (
    "CAPABILITY_LIMITATION_NOT_EVIDENCE_BACKED",
    "FRESH_HEALTHY_RUNTIME_MISSING",
    "HEALTHY_TRAFFIC_FAILED_OR_UNBOUND",
    "LOGS_PROFILE_BINDING_MISSING",
)
_PUBLIC_OUTPUTS_V0231 = (
    "docs/analysis/product-v0231-baseline-restart.json",
    "docs/analysis/product-v0231-continuation-session-1.json",
    "docs/analysis/product-v0231-knowledge-loop-handoff.json",
    "docs/analysis/product-v0231-knowledge-loop-handoff.md",
    "docs/analysis/product-v0231-progress.json",
    "docs/results/product-v0231-interview-brief.md",
    "docs/results/product-v0231-limitations.md",
    "docs/results/product-v0231-nofault-acceptance.json",
    "docs/results/product-v0231-nofault-acceptance.md",
)
_FROZEN_PUBLIC_OUTPUTS_V0231 = {
    "docs/analysis/product-v0231-baseline-restart.json": (
        "c8e674bf7b4351fc28e82b4d8e3b79845685e0d21e26d8a9703c38c64a90d280",
        3688,
    ),
    "docs/analysis/product-v0231-continuation-session-1.json": (
        "bd9b99377ff14865deb7dabebc491524d534b1e1bcde71c06f1fce23688d29a1",
        9213,
    ),
    "docs/analysis/product-v0231-knowledge-loop-handoff.json": (
        "17d5ac2fe233ae3610068a3d58951b23bd9190f82e8a616f87f14bab28e2c147",
        1967,
    ),
    "docs/analysis/product-v0231-knowledge-loop-handoff.md": (
        "982ed8c2f9631fa1fca03aa6f088d4a2d6fc804b89878b5c1328e69c39bf41fc",
        286,
    ),
    "docs/analysis/product-v0231-progress.json": (
        "16461de44b1c3c239cd66e76aba718f330e716407c1047b5534d0c6e879e5932",
        631,
    ),
    "docs/results/product-v0231-interview-brief.md": (
        "47ed60d3ec6f226a4eedeba5737bb1ce24cc698fe96a18b60cf392250408ade8",
        398,
    ),
    "docs/results/product-v0231-limitations.md": (
        "352874cd4785dae5a80f63bd2c39dab169f631bdd8cc5f4cd80b4d0b676bde9f",
        340,
    ),
    "docs/results/product-v0231-nofault-acceptance.json": (
        "872e98c5f3214394cd8d4f8ac4362813369296578c57b32d3ff929a9f3266d9e",
        7765,
    ),
    "docs/results/product-v0231-nofault-acceptance.md": (
        "ff0e3c70b7ffc6e0bac85b02c1388913f4d3d02cabe7874033459126813fe991",
        305,
    ),
}
_ABSOLUTE_LOCAL_LOCATOR = re.compile(r"(?:/Users/|/home/|[A-Za-z]:[/\\])")


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Product v0.2.3.1 public object differs: {path.name}")
    return value


def _require_seal(payload: dict[str, Any], field: str) -> None:
    body = dict(payload)
    digest = body.pop(field, None)
    if digest != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.1 {field} differs")


def _require_markdown(path: Path, lines: Sequence[str]) -> None:
    expected = ("\n".join(lines) + "\n").encode("utf-8")
    if path.read_bytes() != expected:
        raise ValueError(f"Product v0.2.3.1 Markdown differs: {path.name}")


def _verify_frozen_public_outputs(root: Path) -> None:
    if tuple(sorted(_FROZEN_PUBLIC_OUTPUTS_V0231)) != tuple(
        sorted(_PUBLIC_OUTPUTS_V0231)
    ):
        raise ValueError("Product v0.2.3.1 frozen output set differs")
    for relative in _PUBLIC_OUTPUTS_V0231:
        raw = (root / relative).read_bytes()
        expected_sha256, expected_size = _FROZEN_PUBLIC_OUTPUTS_V0231[relative]
        if (
            len(raw) != expected_size
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise ValueError(f"Product v0.2.3.1 frozen output bytes differ: {relative}")
        if _ABSOLUTE_LOCAL_LOCATOR.search(raw.decode("utf-8")):
            raise ValueError(
                f"Product v0.2.3.1 public output leaks a local locator: {relative}"
            )


def _verify_product_v0231_result_payloads(root: Path) -> dict[str, object]:
    session = _object(root / "docs/analysis/product-v0231-continuation-session-1.json")
    restart = _object(root / "docs/analysis/product-v0231-baseline-restart.json")
    public = _object(root / "docs/results/product-v0231-nofault-acceptance.json")
    handoff = _object(root / "docs/analysis/product-v0231-knowledge-loop-handoff.json")
    progress = _object(root / "docs/analysis/product-v0231-progress.json")
    descriptor = RuntimeAuthorityContinuityDescriptorV0231.model_validate(
        _object(root / "docs/analysis/product-v0231-runtime-authority-descriptor.json")
    )
    flagd = FlagdBindDescriptorV0231.model_validate(
        _object(root / "docs/analysis/product-v0231-flagd-bind-descriptor.json")
    )
    profile = NoFaultProfileBindingV0231.model_validate(
        _object(root / "config/product-v0231/continuity/nofault-profile-binding.json")
    )
    campaign = NoFaultCampaignV0231.model_validate(
        _object(root / "config/product-v0231/continuity/campaign.json")
    )
    source_profile_path = root / profile.source_profile_locator
    if (
        source_profile_path.is_symlink()
        or not source_profile_path.is_file()
        or not source_profile_path.resolve(strict=True).is_relative_to(root)
    ):
        raise ValueError("Product v0.2.3.1 source profile locator differs")
    source_profile_bytes = source_profile_path.read_bytes()
    source_profile = NoFaultExecutionProfileV023.load(source_profile_path)
    if (
        hashlib.sha256(source_profile_bytes).hexdigest()
        != profile.source_profile_file_sha256
        or profile.source_profile_file_sha256
        != "0b4b38a4fc4fe030f51d003f4c97ceca8f9cfb9c0766a869c55a367c76a79e48"
        or source_profile.profile_sha256 != profile.source_profile_sha256
        or profile.source_profile_sha256
        != "7b580805f8dc86e1239811903044035f46e5d7eb10a239431bebbe18476c7e10"
    ):
        raise ValueError("Product v0.2.3.1 source profile bytes differ")
    result = NoFaultAcceptanceResultV0231.model_validate(public["result"])
    authority = RuntimeAuthorityContinuityProofV0231.model_validate(
        session["runtime_authority_proof"]
    )
    restart_proof = BaselineRestartProofV0231.model_validate(restart["proof"])
    ledger = RuntimeContinuationSessionLedgerV0231.model_validate(session["ledger"])

    for payload, field in (
        (session, "report_sha256"),
        (restart, "report_sha256"),
        (handoff, "handoff_sha256"),
        (progress, "progress_sha256"),
    ):
        _require_seal(payload, field)

    wrapped = result.wrapped_v023_result
    resolution = wrapped.evidence_resolution
    start = ledger.starts[0]
    completion = ledger.completions[0]
    rotations = session.get("runtime_snapshot_rotations")
    if not isinstance(rotations, list) or len(rotations) != 2:
        raise ValueError("Product v0.2.3.1 Runtime snapshot rotations differ")
    if (
        public.get("execution_head") != EXECUTION_HEAD_V0231
        or result.terminal != NOFAULT_NOT_SUPPORTED_V0231
        or result.acceptance_terminal != NOFAULT_ACCEPTANCE_COMPLETE_V0231
        or result.runtime_terminal != RUNTIME_PASS_V0231
        or result.restart_terminal != RESTART_PASS_V0231
        or tuple(wrapped.reasons) != _REASONS_V0231
        or wrapped.terminal.value != "ECOMSRE_PRODUCT_V023_NOFAULT_NOT_SUPPORTED"
        or wrapped.diagnosis_terminal.value != "INSUFFICIENT_EVIDENCE"
        or wrapped.incident_count != 1
        or wrapped.diagnosis_count != 1
        or wrapped.fault_family_count != 0
        or wrapped.action_authority != "NONE"
        or wrapped.action_authority_violations != 0
        or wrapped.agent_writes != 0
        or wrapped.runbook_executions != 0
        or not resolution.all_references_resolved
        or not resolution.all_object_sha256_resolved
        or not resolution.source_failures_explicit
        or not resolution.agent_visible_control_truth_absent
        or resolution.logs_profile_binding_visible
    ):
        raise ValueError("Product v0.2.3.1 measured result differs")
    if (
        descriptor.descriptor_sha256 != result.runtime_continuity_descriptor_sha256
        or profile.binding_sha256 != result.profile_binding_sha256
        or campaign.profile_binding_sha256 != profile.binding_sha256
        or campaign.runtime_continuity_descriptor_sha256 != descriptor.descriptor_sha256
        or campaign.predecessor_pr != 80
        or campaign.predecessor_head != "b15072c48acf8b143d0a950e7248a1684d3eedf0"
        or campaign.maximum_live_sessions != 2
        or campaign.active_baseline_id != profile.active_baseline_id
        or campaign.active_baseline_sha256 != profile.active_baseline_sha256
        or campaign.accepted_incident_limit != 1
        or campaign.diagnosis_limit != 1
        or campaign.fault_attempt_limit != 0
        or campaign.knowledge_loop_campaign_limit != 0
        or campaign.action_authority != "NONE"
        or profile.active_baseline_id != wrapped.baseline_id
        or profile.active_baseline_sha256 != wrapped.baseline_sha256
        or authority.proof_sha256 != result.runtime_authority_proof_sha256
        or restart_proof.proof_sha256 != result.baseline_restart_proof_sha256
        or ledger.live_session_count != 1
        or ledger.accepted_incident_count != 1
        or ledger.diagnosis_count != 1
        or completion.cleanup != "CLEAN"
        or completion.runtime_terminal != RUNTIME_PASS_V0231
        or completion.restart_terminal != RESTART_PASS_V0231
        or completion.nofault_terminal != NOFAULT_NOT_SUPPORTED_V0231
        or session.get("start") != start.model_dump(mode="json")
        or session.get("completion") != completion.model_dump(mode="json")
        or session.get("baseline_restart_proof_sha256") != restart_proof.proof_sha256
        or session.get("runtime_authority_proof") != authority.model_dump(mode="json")
        or result.session_start_sha256 != start.start_sha256
        or rotations[0].get("authority_sha256") != descriptor.connector_binding_sha256
        or rotations[1].get("authority_sha256") != descriptor.connector_binding_sha256
        or rotations[0].get("after_snapshot_sha256")
        != rotations[1].get("before_snapshot_sha256")
        or rotations[0].get("before_snapshot_sha256")
        == rotations[0].get("after_snapshot_sha256")
        or rotations[1].get("before_snapshot_sha256")
        == rotations[1].get("after_snapshot_sha256")
    ):
        raise ValueError("Product v0.2.3.1 session binding differs")
    closure = {
        "queue_default_before_sha256": public.get("queue_default_before_sha256"),
        "queue_default_unchanged": public.get("queue_default_unchanged"),
        "outer_baseline_before_sha256": public.get("outer_baseline_before_sha256"),
        "outer_baseline_unchanged": public.get("outer_baseline_unchanged"),
        "product_cleanup": public.get("product_cleanup"),
        "product_cleanup_observation": public.get("product_cleanup_observation"),
        "demo_cleanup": public.get("demo_cleanup"),
        "demo_cleanup_observation": public.get("demo_cleanup_observation"),
        "non_owned_resources_changed": public.get("non_owned_resources_changed"),
    }
    if (
        public.get("terminal") != NOFAULT_NOT_SUPPORTED_V0231
        or public.get("acceptance_terminal") != NOFAULT_ACCEPTANCE_COMPLETE_V0231
        or public.get("incident_count") != 1
        or public.get("diagnosis_count") != 1
        or public.get("fault_family_count") != 0
        or public.get("knowledge_artifact_count") != 0
        or public.get("provider_calls") != 0
        or public.get("fault_attempt_count") != 0
        or public.get("knowledge_loop_campaign_count") != 0
        or public.get("action_authority") != "NONE"
        or public.get("agent_writes") != 0
        or public.get("runbook_executions") != 0
        or public.get("product_cleanup") != "CLEAN"
        or public.get("demo_cleanup") != "CLEAN"
        or public.get("queue_default_unchanged") is not True
        or public.get("outer_baseline_unchanged") is not True
        or public.get("non_owned_resources_changed") is not False
        or public.get("runtime_continuity_descriptor_sha256")
        != descriptor.descriptor_sha256
        or public.get("flagd_bind_descriptor_sha256") != flagd.descriptor_sha256
        or public.get("runtime_authority_proof_sha256") != authority.proof_sha256
        or public.get("restart_proof_sha256") != restart_proof.proof_sha256
        or public.get("profile_binding_sha256") != profile.binding_sha256
        or public.get("execution_profile_sha256") != profile.source_profile_sha256
        or public.get("execution_profile_sha256") != wrapped.execution_profile_sha256
        or public.get("active_baseline_id") != wrapped.baseline_id
        or public.get("active_baseline_sha256") != wrapped.baseline_sha256
        or public.get("active_profile_sha256") != wrapped.profile_sha256
        or public.get("active_profile_sha256") != restart_proof.active_profile_sha256
        or public.get("readiness_audit_sha256") != restart_proof.readiness_audit_sha256
        or public.get("environment_configuration_sha256")
        != hashlib.sha256(
            (root / "config/product-v023/environment.otel-demo.json").read_bytes()
        ).hexdigest()
        or public.get("private_report_sha256")
        != "2f3001aa40e57844d166c7507f5df4d481635ce12fafa2e846221e6b9d72100f"
        or public.get("cleanup_proof_sha256") != semantic_sha256_v22(closure)
        or public.get("product_cleanup_observation") != session.get("product_cleanup")
        or public.get("demo_cleanup_observation") != session.get("demo_cleanup")
    ):
        raise ValueError("Product v0.2.3.1 public counters differ")
    if (
        restart.get("terminal") != RESTART_PASS_V0231
        or restart.get("runtime_terminal") != RUNTIME_PASS_V0231
        or restart.get("proof") != restart_proof.model_dump(mode="json")
        or restart.get("runtime_authority_proof_sha256") != authority.proof_sha256
        or restart.get("session_completion_sha256") != completion.completion_sha256
        or restart.get("incident_count") != 1
        or restart.get("diagnosis_count") != 1
        or restart.get("fault_attempt_count") != 0
        or restart.get("action_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.1 restart report differs")
    if (
        handoff.get("terminal") != HANDOFF_NOT_AUTHORIZED_V0231
        or handoff.get("authorized") is not False
        or handoff.get("fault_calibration_authorized") is not False
        or tuple(handoff.get("required_repair_reasons", ())) != _REASONS_V0231
        or handoff.get("execution_head") != EXECUTION_HEAD_V0231
        or handoff.get("runtime_continuity_descriptor_sha256")
        != descriptor.descriptor_sha256
        or handoff.get("flagd_bind_descriptor_sha256") != flagd.descriptor_sha256
        or handoff.get("post_start_authority_proof_sha256") != authority.proof_sha256
        or handoff.get("environment_configuration_sha256")
        != public.get("environment_configuration_sha256")
        or handoff.get("active_profile_sha256") != wrapped.profile_sha256
        or handoff.get("service_identity_sha256") != wrapped.service_identity_sha256
        or handoff.get("capability_sha256") != wrapped.capability_sha256
        or handoff.get("active_baseline_id") != wrapped.baseline_id
        or handoff.get("active_baseline_sha256") != wrapped.baseline_sha256
        or handoff.get("readiness_audit_sha256") != restart_proof.readiness_audit_sha256
        or handoff.get("restart_proof_sha256") != restart_proof.proof_sha256
        or handoff.get("incident_sha256") != wrapped.incident_sha256
        or handoff.get("diagnosis_result_sha256") != wrapped.diagnosis_result_sha256
        or handoff.get("evidence_bundle_sha256") != wrapped.evidence_bundle_sha256
        or handoff.get("queue_default_sha256")
        != public.get("queue_default_before_sha256")
        or handoff.get("cleanup_proof_sha256") != public.get("cleanup_proof_sha256")
    ):
        raise ValueError("Product v0.2.3.1 handoff differs")
    if (
        progress.get("terminal") != NOFAULT_ACCEPTANCE_COMPLETE_V0231
        or progress.get("runtime_terminal") != RUNTIME_PASS_V0231
        or progress.get("restart_terminal") != RESTART_PASS_V0231
        or progress.get("measured_nofault_terminal") != NOFAULT_NOT_SUPPORTED_V0231
        or progress.get("live_session_count") != 1
        or progress.get("baseline_attempt_count") != 1
        or progress.get("incident_count") != 1
        or progress.get("diagnosis_count") != 1
        or progress.get("fault_attempt_count") != 0
        or progress.get("knowledge_loop_campaign_count") != 0
        or progress.get("action_authority") != "NONE"
        or progress.get("repository_acceptance") != "REVIEW_REQUIRED"
    ):
        raise ValueError("Product v0.2.3.1 progress differs")

    _require_markdown(
        root / "docs/results/product-v0231-nofault-acceptance.md",
        (
            "# Product v0.2.3.1 No-Fault Acceptance",
            "",
            f"Measured terminal: `{NOFAULT_NOT_SUPPORTED_V0231}`",
            f"Acceptance terminal: `{NOFAULT_ACCEPTANCE_COMPLETE_V0231}`",
            "Incident / Diagnosis: `1 / 1`",
            "Action authority / Agent writes / Runbooks: `NONE / 0 / 0`",
            "Cleanup: `Product CLEAN / Demo CLEAN`",
        ),
    )
    _require_markdown(
        root / "docs/results/product-v0231-limitations.md",
        (
            "# Product v0.2.3.1 Limitations",
            "",
            f"Measured terminal: `{NOFAULT_NOT_SUPPORTED_V0231}`",
            *(f"- `{reason}`" for reason in _REASONS_V0231),
            "",
            "This is one owned local No-Fault episode. It does not authorize deployment or remediation.",
        ),
    )
    _require_markdown(
        root / "docs/results/product-v0231-interview-brief.md",
        (
            "# Product v0.2.3.1 Interview Brief",
            "",
            "One exact-path Runtime authority session preserved the active P01 Baseline across an ordinary Product restart.",
            "The same session then measured one frozen healthy checkout episode with no action authority.",
            f"Measured terminal: `{NOFAULT_NOT_SUPPORTED_V0231}`",
            "Claim boundary: local owned environment, no fault injection, no Agent write, and no Runbook.",
        ),
    )
    _require_markdown(
        root / "docs/analysis/product-v0231-knowledge-loop-handoff.md",
        (
            "# Product v0.2.3.1 Knowledge-Loop Handoff",
            "",
            f"Terminal: `{HANDOFF_NOT_AUTHORIZED_V0231}`",
            "Authorized: `false`",
            *(f"- `{reason}`" for reason in _REASONS_V0231),
        ),
    )
    return {
        "terminal": NOFAULT_ACCEPTANCE_COMPLETE_V0231,
        "measured_terminal": NOFAULT_NOT_SUPPORTED_V0231,
        "execution_head": EXECUTION_HEAD_V0231,
        "frozen_evidence_commit": FROZEN_EVIDENCE_COMMIT_V0231,
        "public_output_count": len(_PUBLIC_OUTPUTS_V0231),
        "live_session_count": ledger.live_session_count,
        "incident_count": wrapped.incident_count,
        "diagnosis_count": wrapped.diagnosis_count,
        "fault_attempt_count": public["fault_attempt_count"],
        "knowledge_loop_campaign_count": public["knowledge_loop_campaign_count"],
        "action_authority": public["action_authority"],
    }


def verify_product_v0231_result(project_root: Path) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    _verify_frozen_public_outputs(root)
    return _verify_product_v0231_result_payloads(root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args(argv)
    result = verify_product_v0231_result(args.project_root)
    print(result["terminal"])
    print(result["measured_terminal"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
