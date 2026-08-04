"""One-command deterministic Phase 5A missing-source demonstration."""

from __future__ import annotations

from pathlib import Path

from ecomsre.backends.live_protocol import BackendStatus
from ecomsre.backends.replay import ReplayCase, load_replay_case
from ecomsre.phase1.contracts import EvidenceSource
from ecomsre.phase5a.workflows import (
    DiagnosisVariantV2,
    run_diagnosis_v2,
)


def _select_missing_logs_case(project_root: Path) -> ReplayCase:
    visible_root = project_root / "config/phase1/replay-cases/agent-visible"
    candidates: list[ReplayCase] = []
    for case_root in sorted(visible_root.iterdir()):
        if not case_root.is_dir() or case_root.is_symlink():
            raise ValueError("Phase 5A demo visible root contains an unsafe entry")
        replay_case = load_replay_case(visible_root, case_root.name)
        if (
            replay_case.logs.status is BackendStatus.UNAVAILABLE
            and not replay_case.logs.observations
            and replay_case.metrics.status is BackendStatus.AVAILABLE
            and bool(replay_case.metrics.observations)
            and replay_case.traces.status is BackendStatus.AVAILABLE
            and bool(replay_case.traces.observations)
        ):
            candidates.append(replay_case)
    if len(candidates) != 1:
        raise RuntimeError("Phase 5A demo source-shape selector is not unique")
    return candidates[0]


def build_phase5a_demo_report(project_root: Path) -> dict[str, object]:
    """Show missing telemetry as a typed finding, never a workflow failure."""

    root = Path(project_root).resolve(strict=True)
    replay_case = _select_missing_logs_case(root)
    trace = run_diagnosis_v2(
        project_root=root,
        replay_case=replay_case,
        variant=DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2,
    )
    if trace.status != "COMPLETED" or trace.final_diagnosis is None:
        raise RuntimeError("Phase 5A demo workflow did not complete")
    findings_by_source = {finding.source: finding for finding in trace.findings}

    def finding_projection(source: EvidenceSource) -> dict[str, object]:
        finding = findings_by_source[source]
        return {
            "source": source.value,
            "observations_status": finding.observations_status.value,
            "candidate_mechanisms": [
                candidate.fault_mechanism.value
                for candidate in finding.candidates
            ],
            "supporting_evidence": list(finding.supporting_evidence),
            "missing_evidence": list(finding.missing_evidence),
        }

    final = trace.final_diagnosis
    return {
        "schema_version": "phase5a.diagnosis-quality-demo.v2",
        "mode": "SCRIPTED_REPLAY",
        "case_id": replay_case.case_id,
        "variant": trace.variant.value,
        "incident_id": replay_case.incident.incident_id,
        "metrics_finding": finding_projection(EvidenceSource.METRICS),
        "logs_missing_finding": finding_projection(EvidenceSource.LOGS),
        "trace_mechanism_finding": finding_projection(EvidenceSource.TRACES),
        "judge_decision": {
            "decision": final.decision.value,
            "root_service": final.root_service,
            "fault_mechanism": (
                final.fault_mechanism.value
                if final.fault_mechanism is not None
                else None
            ),
            "supporting_evidence": list(final.supporting_evidence),
            "missing_evidence": list(final.missing_evidence),
        },
        "workflow_failure": False,
        "usage": {
            "model_calls": trace.final_budget_snapshot.charged_model_calls,
            "tool_calls": trace.final_budget_snapshot.charged_tool_calls,
            "tokens": trace.final_budget_snapshot.cumulative_tokens,
        },
        "live_mutation": False,
        "new_remediation_action": False,
        "phase5b_entered": False,
    }
