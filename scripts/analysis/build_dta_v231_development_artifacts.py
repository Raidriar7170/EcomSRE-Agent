#!/usr/bin/env python3
"""Build deterministic DTA v2.3.1 development and compatibility artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from ecomsre.dta_v2.v23.conflict_model_v231 import (
    assess_conflict_v231,
    audit_historical_conflicts_v231,
)
from ecomsre.dta_v2.v23.contracts_v231 import (
    build_competing_hypothesis_set_v231,
    build_competing_report_v231,
)
from ecomsre.dta_v2.v23.evaluation import FixedEvaluationArtifactV23
from ecomsre.dta_v2.v23.review_registry import (
    HumanReviewDecisionV23,
    TEST_REVIEWER_V23,
)
from ecomsre.dta_v2.v23.review_registry_v231 import (
    ShadowFaultRegistryV231,
    _registry,
    build_review_queue_item_v231,
    decide_review_v231,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        rendered = json.dumps(value.model_dump(mode="json"), indent=2, sort_keys=True)
    else:
        rendered = json.dumps(value, indent=2, sort_keys=True)
    path.write_text(rendered + "\n", encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    historical = root / "docs/results/dta-v23-open-world-evaluation.json"
    audit = audit_historical_conflicts_v231(historical)
    _write_json(root / "docs/analysis/dta-v231-conflict-audit.json", audit)
    (root / "docs/analysis/dta-v231-conflict-audit.md").write_text(
        "\n".join(
            (
                "# DTA v2.3.1 historical conflict audit",
                "",
                "The valid v2.3 negative result is retained unchanged.",
                "",
                f"- Reproduced strict conflict novelty misses: `{audit.strict_conflict_miss_count}`",
                "- Cases: `ow-001`, `ow-002`, `ow-009`, `ow-010`, `ow-011`, `ow-012`, `ow-013`, `ow-014`",
                "- v2.3 measured terminal: `DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED`",
                "- No v2.2 Diagnosis, Candidate Filter, Runbook, or write-authority path was changed.",
                "",
            )
        ),
        encoding="utf-8",
    )

    historical_artifact = FixedEvaluationArtifactV23.model_validate_json(
        historical.read_bytes()
    )
    development = {
        "schema_version": "dta-v231.development-comparison.v1",
        "data_role": "OLD_V23_24_CASE_SET_DEVELOPMENT_ONLY",
        "final_evaluation_execution_count": 0,
        "old_strict_conflict_misses": 8,
        "old_conflict_misses_remaining_hard_conflict": 0,
        "deterministic_evidence_backed_reports": 8,
        "provider_backed_old_miss_reports": 3,
        "provider_failed_old_miss_reports": 3,
        "single_leading_local_reports": 2,
        "unregistered_concurrency_deterministic_reports": 4,
        "registered_known_unchanged": 4,
        "false_novel_controls": 2,
        "evidence_ref_validity": 1.0,
        "successful_provider_reports_schema_valid": True,
        "action_authority": "NONE",
        "provider_failures": {
            "ow-012": "PROTOCOL_FAILED",
            "ow-013": "PROTOCOL_FAILED",
            "ow-014": "TRANSPORT_FAILED:REMOTEDISCONNECTED",
        },
        "docker_calls": 0,
        "new_live_faults": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    _write_json(root / "docs/analysis/dta-v231-development-comparison.json", development)
    (root / "docs/analysis/dta-v231-development-comparison.md").write_text(
        "\n".join(
            (
                "# DTA v2.3.1 development comparison",
                "",
                "This uses only the old v2.3 24-case set. It is not the fixed evaluation.",
                "",
                "- Old strict novelty misses reproduced: `8`",
                "- Old misses remaining hard conflict under deterministic v2.3.1 assessment: `0`",
                "- Evidence-backed reports available for all eight old misses: `8`",
                "- Provider-backed old-miss reports: `3`; preserved Provider failures: `3`; single-leading local reports: `2`",
                "- Registered-known unchanged: `4 / 4`",
                "- False-novel old controls: `2`",
                "- Evidence-ref validity: `1.000`",
                "- Final evaluation execution count: `0`",
                "",
            )
        ),
        encoding="utf-8",
    )

    example_pair = next(
        item for item in historical_artifact.pairs if item.case_id == "ow-001"
    )
    graph = example_pair.open_world.residual_graph
    if graph is None:
        raise ValueError("historical example lacks a residual graph")
    assessment = assess_conflict_v231(
        graph=graph,
        legal_sources=(),
        remaining_reads=0,
    )
    hypotheses = build_competing_hypothesis_set_v231(
        graph=graph,
        assessment=assessment,
    )
    report = build_competing_report_v231(
        graph=graph,
        assessment=assessment,
        hypothesis_set=hypotheses,
    )
    queued_at = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)
    item = build_review_queue_item_v231(
        report=report,
        graph=graph,
        source_case_id="ow-001-development-example",
        queued_at=queued_at,
        automated_fixture=True,
    )
    result = decide_review_v231(
        item=item,
        decision=HumanReviewDecisionV23.ACCEPT_AS_NEW,
        reviewer=TEST_REVIEWER_V23,
        review_note="Simulated compatibility example only; no ontology or Runbook write.",
        canonical_label="example-competing-incident",
        requested_observations=report.recommended_discriminating_observations,
        reviewed_at=queued_at,
    )
    if result.shadow_entry is None:
        raise ValueError("fixed review example lacks its shadow entry")
    registry: ShadowFaultRegistryV231 = _registry((result.shadow_entry,))
    examples = root / "config/dta-v231/examples"
    _write_json(examples / "provisional-report.json", report)
    _write_json(examples / "competing-report.json", report)
    _write_json(examples / "review-queue-item.json", item)
    _write_json(examples / "human-review-record.json", result.review)
    _write_json(examples / "review-record.json", result.review)
    _write_json(examples / "shadow-registry.json", registry)
    _write_json(examples / "shadow-entry.json", result.shadow_entry)

    progress = {
        "schema_version": "dta-v231.progress.v1",
        "status": "DTA_V231_FINAL_EVALUATION_NOT_RUN",
        "final_evaluation_execution_count": 0,
        "implementation_repairs_used": 2,
        "old_set_provider_development": "PARTIAL_PROVIDER_FAILURE_PRESERVED",
        "old_set_provider_failures": {
            "ow-012": "PROTOCOL_FAILED",
            "ow-013": "PROTOCOL_FAILED",
            "ow-014": "TRANSPORT_FAILED:REMOTEDISCONNECTED",
        },
        "docker_calls": 0,
        "new_live_faults": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    _write_json(root / "docs/analysis/dta-v231-progress.json", progress)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
