"""One-command deterministic Phase 4 Domain replay demonstration."""

from __future__ import annotations

from pathlib import Path

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase4.contracts import DomainVariant
from ecomsre.phase4.workflows import run_domain_replay_workflow


DEFAULT_DEMO_CASE = "search-feature-freshness-lag-complete"


def build_domain_demo_report(project_root: Path) -> dict[str, object]:
    """Build a compact report without provider, evaluator, Docker, or mutation."""

    root = Path(project_root).resolve(strict=True)
    replay_case = load_replay_case(
        root / "config/phase4/replay-cases/agent-visible",
        DEFAULT_DEMO_CASE,
    )
    trace = run_domain_replay_workflow(
        project_root=root,
        replay_case=replay_case,
        variant=DomainVariant.DYNAMIC_MULTI_AGENT,
    )
    if trace.status != "COMPLETED" or trace.final_rca is None:
        raise RuntimeError("Phase 4 demo workflow did not complete")
    graph = trace.admitted_graph
    if graph is None:
        raise RuntimeError("Phase 4 demo has no admitted DAG")
    result = trace.final_rca
    return {
        "schema_version": "phase4.domain-demo-report.v1",
        "mode": "SCRIPTED_REPLAY",
        "incident": {
            "incident_id": replay_case.incident.incident_id,
            "affected_sli": replay_case.incident.affected_sli,
        },
        "admitted_dag": [
            {
                "node_id": node.node_id,
                "source": node.source.value,
                "specialist": node.specialist_role.value,
            }
            for node in graph.initial_plan.nodes
        ],
        "specialists_used": list(
            dict.fromkeys(finding.specialist_role.value for finding in trace.findings)
        ),
        "evidence_refs": list(result.supporting_evidence),
        "domain_decision": result.decision.value,
        "root_service": result.root_service,
        "domain_mechanism": (
            result.fault_mechanism.value
            if result.fault_mechanism is not None
            else None
        ),
        "usage": {
            "model_calls": trace.final_budget_snapshot.charged_model_calls,
            "tool_calls": trace.final_budget_snapshot.charged_tool_calls,
            "tokens": trace.final_budget_snapshot.cumulative_tokens,
        },
        "remediation_disposition": trace.remediation_disposition.outcome.value,
        "remediation_backend": "NONE",
        "live_mutation": False,
        "phase5_entered": False,
    }
