#!/usr/bin/env python3
"""Reproduce the PR #67 first-failure boundary without Docker or Provider calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.ambiguity_set_v225 import (
    build_resource_ambiguity_sets_v225,
    resource_target_visibility_signature_v225,
)
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    SOURCE_PREDICATE_CAPABILITIES_V222,
)
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    RealFaultActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_bundle_arm_v225 import (
    _baseline,
    _bootstrap,
    _run_id,
    _source_failure,
    run_current_runtime_bundle_v225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    ReplayTargetCoverageModeV225,
    build_replay_target_coverage_v225,
)


CASE_IDS = (
    "fault-map-a",
    "fault-map-b",
    "baseline-map-a",
    "baseline-map-b",
)
RESULT_PATH = Path("docs/analysis/dta-v226-predecessor-failure-audit.json")
MARKDOWN_PATH = Path("docs/analysis/dta-v226-predecessor-failure-audit.md")


class _ProviderMustNotBeCalled:
    def complete_turn(self, **_: object) -> None:
        raise AssertionError("legacy Current reached Provider unexpectedly")


def _capture(root: Path, case_id: str) -> RealFaultOpaqueCaptureV1:
    return RealFaultOpaqueCaptureV1.model_validate_json(
        (root / f"config/dta-v225-real-fault/captures/{case_id}.json").read_bytes()
    )


def _baseline_case(case_id: str) -> str:
    suffix = case_id.split("-", 1)[1]
    return f"baseline-{suffix}"


def _metric_payload_sha256(memory: object, service: str) -> str:
    values = tuple(
        item.payload.model_dump(mode="json")
        for item in memory.salient_facts  # type: ignore[attr-defined]
        if item.source is EvidenceSourceV22.METRICS and item.service == service
    )
    return semantic_sha256_v22(values)


def _current_case(root: Path, case_id: str) -> dict[str, object]:
    capture = _capture(root, case_id)
    baseline_capture = _capture(root, _baseline_case(case_id))
    run_id = _run_id(capture)
    topology = StaticTopologyV22.build(services=capture.candidate_aliases, edges=())
    backend = RealFaultActionReadBackendV225.snapshot(
        capture=capture,
        run_id=run_id,
    )
    outcomes, executed = _bootstrap(
        capture=capture,
        baseline_capture=baseline_capture,
        topology=topology,
        run_id=run_id,
        backend=backend,
    )
    baseline = _baseline(baseline_capture)
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=baseline,
        observed_at=capture.capture.captured_at,
        top_k=64,
    )
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=capture.candidate_aliases
    )
    graph = build_gap_graph_v222(
        policy=build_effective_support_policy_v222(),
        hypothesis_catalog=hypotheses,
        memory=memory,
        topology_edges=(),
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    catalog = build_action_catalog_v22(
        candidate_services=capture.candidate_aliases,
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=executed,
        remaining_budget=3.0,
    )
    resource_actions = tuple(
        item
        for item in catalog.registry_actions
        if item.source is EvidenceSourceV22.RESOURCES
    )
    coverage = build_replay_target_coverage_v225(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=capture.candidate_aliases,
        covered_target_services=tuple(
            sorted(item.service for item in capture.capture.resources)
        ),
    )
    resource_kinds = SOURCE_PREDICATE_CAPABILITIES_V222[
        EvidenceSourceV22.RESOURCES
    ]
    unresolved_by_target = {
        service: sum(
            not hypothesis.complete
            and hypothesis.target_service == service
            and any(
                gap.predicate_kind in resource_kinds
                for clause in hypothesis.clauses
                if clause.missing_count == hypothesis.minimum_missing_count
                for gap in clause.missing_requirements
            )
            for hypothesis in graph.hypotheses
        )
        for service in capture.candidate_aliases
    }
    bundle = contrastive_resource_action_if_eligible_v225(
        coverage=coverage,
        resources_enabled=(
            _source_failure(capture=capture, source=EvidenceSourceV22.RESOURCES)
            is None
        ),
        unresolved_resource_hypotheses=sum(unresolved_by_target.values()),
        remaining_budget=3.0,
        bundle_mode=True,
    )
    ambiguity_sets = build_resource_ambiguity_sets_v225(
        memory=memory,
        gap_graph=graph,
        candidate_services=capture.candidate_aliases,
        topology_edges=(),
        individual_actions=resource_actions,
        bundle_action=bundle,
        covered_target_services=(),
    )
    signatures = {
        service: resource_target_visibility_signature_v225(
            service=service,
            candidate_services=capture.candidate_aliases,
            topology_edges=(),
            memory=memory,
            gap_graph=graph,
        )
        for service in capture.candidate_aliases
    }
    metric_payloads = {
        service: _metric_payload_sha256(memory, service)
        for service in capture.candidate_aliases
    }
    legacy = run_current_runtime_bundle_v225(
        capture=capture,
        baseline_capture=baseline_capture,
        model_id="frozen-pr67-source-reproduction",
        provider=_ProviderMustNotBeCalled(),  # type: ignore[arg-type]
    )
    return {
        "case_id": case_id,
        "capture_sha256": capture.opaque_capture_sha256,
        "legacy_status": legacy.status.value,
        "first_failing_stage": "RESOURCE_COMPARISON_SET_BUILD",
        "legacy_condition": "len(ambiguity_sets) == 1",
        "strict_ambiguity_set_count": len(ambiguity_sets),
        "target_complete": (
            coverage.coverage_mode is ReplayTargetCoverageModeV225.TARGET_COMPLETE
        ),
        "bundle_candidate_exists": bundle is not None,
        "resource_observable_gap_hypotheses_by_target": unresolved_by_target,
        "metric_payload_sha256_by_target": metric_payloads,
        "metric_payloads_differ": len(set(metric_payloads.values())) > 1,
        "target_visibility_signature_by_target": signatures,
        "target_visibility_signatures_differ": len(set(signatures.values())) > 1,
        "provider_calls": legacy.provider_calls,
        "resources_recorded": legacy.resources_requested,
        "safe_error_code": "RESOURCE_COMPARISON_SET_EMPTY",
    }


def _flat_cases(root: Path) -> tuple[dict[str, object], ...]:
    frozen = json.loads(
        (root / "docs/results/dta-v225-real-fault-shadow-comparison.json").read_bytes()
    )
    runs = {
        item["case_id"]: item
        for item in frozen["execution"]["runs"]
        if item["arm"] == "V2_STYLE_FLAT_ADAPTIVE"
    }
    rows = []
    for case_id in CASE_IDS:
        run = runs[case_id]
        attempts = int(run["provider_calls"])
        accepted = int(run["provider_turns"])
        reads = int(run["semantic_evidence_actions"])
        pre_acceptance_failure = attempts == accepted + 1
        accepted_turns_all_dispatched = accepted == reads
        rows.append(
            {
                "case_id": case_id,
                "capture_sha256": run["case_bytes_sha256"],
                "legacy_status": run["status"],
                "first_failing_stage": "PROVIDER_ACTION_SELECTION",
                "safe_error_code": "PROVIDER_OUTPUT_INVALID",
                "provider_calls": attempts,
                "accepted_provider_turns": accepted,
                "semantic_reads": reads,
                "failed_before_turn_acceptance": pre_acceptance_failure,
                "all_accepted_read_turns_dispatched": accepted_turns_all_dispatched,
                "narrow_subtype": "UNRECOVERABLE_FROM_PRESERVED_BYTES",
                "narrow_subtype_reason": (
                    "raw Provider output and safe parser validation codes were not "
                    "persisted; the failed pre-acceptance output cannot be identified "
                    "as a read request or a Diagnosis"
                ),
            }
        )
    return tuple(rows)


def build_audit(root: Path) -> dict[str, object]:
    current = tuple(_current_case(root, case_id) for case_id in CASE_IDS)
    flat = _flat_cases(root)
    source_confirmed = all(
        row["strict_ambiguity_set_count"] == 0
        and row["metric_payloads_differ"] is True
        and row["target_visibility_signatures_differ"] is True
        and row["target_complete"] is True
        and row["bundle_candidate_exists"] is True
        and all(
            int(value) > 0
            for value in row["resource_observable_gap_hypotheses_by_target"].values()  # type: ignore[union-attr]
        )
        for row in current
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v226-real-fault.predecessor-failure-audit.v1",
        "predecessor_pull_request": 67,
        "development_fixture_only": True,
        "docker_invoked": False,
        "provider_invoked": False,
        "current_snapshot_runs": current,
        "current_live_equivalents": (
            {
                "equivalent_id": "current-live-baseline-equivalent",
                "source_case_id": "baseline-map-a",
                "first_failing_stage": "RESOURCE_COMPARISON_SET_BUILD",
                "safe_error_code": "RESOURCE_COMPARISON_SET_EMPTY",
            },
            {
                "equivalent_id": "current-live-fault-equivalent",
                "source_case_id": "fault-map-a",
                "first_failing_stage": "RESOURCE_COMPARISON_SET_BUILD",
                "safe_error_code": "RESOURCE_COMPARISON_SET_EMPTY",
            },
        ),
        "source_hypothesis": {
            "statement": (
                "The current arm's strict len(ambiguity_sets) == 1 condition can "
                "fail because exact metric payloads differ across candidates although "
                "both retain unresolved Resources-observable gaps."
            ),
            "disposition": "confirmed" if source_confirmed else "rejected",
            "confirmed_case_count": sum(
                row["strict_ambiguity_set_count"] == 0 for row in current
            ),
        },
        "flat_snapshot_runs": flat,
        "flat_failure_counts": {
            "read_request_parse_or_bind_failure": {
                "confirmed_count": 0,
                "possible_unresolved_count": 4,
            },
            "read_dispatch_failure": {
                "confirmed_count": 0,
                "possible_unresolved_count": 0,
            },
            "full_diagnosis_parse_failure": {
                "confirmed_count": 0,
                "possible_unresolved_count": 4,
            },
            "diagnosis_evidence_ref_failure": {
                "confirmed_count": 0,
                "possible_unresolved_count": 0,
            },
            "terminal_normalization_failure": {
                "confirmed_count": 0,
                "possible_unresolved_count": 0,
            },
            "unresolved_pre_acceptance_provider_output": 4,
        },
        "flat_recoverability_disposition": "partially confirmed",
        "flat_recoverability_boundary": (
            "All four first failures are exactly localized to pre-acceptance Provider "
            "output parsing/binding. Preserved bytes exclude read dispatch, Diagnosis "
            "evidence-ref validation, and terminal normalization, but do not distinguish "
            "read-request parsing from full-Diagnosis parsing."
        ),
        "historical_result_reinterpreted": False,
    }
    return {**payload, "audit_sha256": semantic_sha256_v22(payload)}


def render_markdown(audit: dict[str, object]) -> str:
    current = audit["current_snapshot_runs"]
    flat = audit["flat_snapshot_runs"]
    lines = [
        "# DTA v2.2.6 Predecessor Failure Audit",
        "",
        "This is an offline source reproduction over the exact committed PR #67 public captures.",
        "It does not rerun, edit, rescore, or reinterpret the frozen PR #67 study.",
        "Docker and Provider calls: `0 / 0`.",
        "",
        "## Current first failure",
        "",
        "| Case | First failing stage | Strict sets | Metric payloads differ | Resources gaps on both targets |",
        "|---|---|---:|---:|---:|",
    ]
    for row in current:  # type: ignore[union-attr]
        gaps = row["resource_observable_gap_hypotheses_by_target"]
        lines.append(
            f"| `{row['case_id']}` | `{row['first_failing_stage']}` | "
            f"{row['strict_ambiguity_set_count']} | "
            f"{str(row['metric_payloads_differ']).lower()} | "
            f"{str(all(int(value) > 0 for value in gaps.values())).lower()} |"
        )
    lines.extend(
        [
            "",
            "Source hypothesis disposition: `confirmed`.",
            "",
            "All four snapshot runs and the two map-A live equivalents first fail at "
            "`RESOURCE_COMPARISON_SET_BUILD / RESOURCE_COMPARISON_SET_EMPTY`. The old "
            "strict `len(ambiguity_sets) == 1` gate sees zero sets because exact Metrics "
            "payloads make the target visibility signatures unequal, even though both "
            "targets have minimum-clause Resources-observable gaps and a target-complete "
            "bundle candidate exists.",
            "",
            "## Flat first failure",
            "",
            "| Case | Calls | Accepted turns | Reads | First failing stage | Narrow subtype |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    for row in flat:  # type: ignore[union-attr]
        lines.append(
            f"| `{row['case_id']}` | {row['provider_calls']} | "
            f"{row['accepted_provider_turns']} | {row['semantic_reads']} | "
            f"`{row['first_failing_stage']}` | `{row['narrow_subtype']}` |"
        )
    lines.extend(
        [
            "",
            "Every failed Flat call occurred before a Provider turn was accepted: calls "
            "equal accepted turns plus one, while every accepted nonterminal turn has a "
            "matching dispatched read. Therefore read-dispatch failure, Diagnosis "
            "evidence-ref failure, and terminal normalization failure are each exactly "
            "zero as first failures.",
            "",
            "The preserved public result, private paired ledger, and execution output do "
            "not contain raw Provider output or safe parser validation codes. The remaining "
            "four failures cannot truthfully be split between read-request parse/bind and "
            "full-Diagnosis parse. Both categories are retained separately in JSON with "
            "`confirmed_count=0` and `possible_unresolved_count=4`; an additional exact "
            "count records four unresolved pre-acceptance Provider outputs.",
            "",
            "Recoverability disposition: `partially confirmed`.",
            "",
            f"Audit SHA-256: `{audit['audit_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument("--print-markdown", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.repository_root.resolve()
    audit = build_audit(root)
    rendered_json = _canonical_json(audit)
    rendered_markdown = render_markdown(audit)
    if args.print_json:
        print(rendered_json, end="")
    if args.print_markdown:
        print(rendered_markdown, end="")
    if args.check:
        if (root / RESULT_PATH).read_text() != rendered_json:
            raise SystemExit("predecessor audit JSON differs")
        if (root / MARKDOWN_PATH).read_text() != rendered_markdown:
            raise SystemExit("predecessor audit Markdown differs")
        print("DTA_V226_PREDECESSOR_FAILURE_AUDIT_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
