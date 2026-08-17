"""Provisional three-arm identity construction for DTA v2.1."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ecomsre.dta_v2.tool_contracts import (
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    QueryMetricsRequest,
    SearchLogsRequest,
    TraceNeighborhoodRequest,
)
from ecomsre.dta_v2.v21.agent_contracts import (
    ActionSelectionDecisionV21,
    AgentArmV21,
    AgentIdentityManifestV21,
    PROVIDER_ADAPTER_VERSION_V21,
)
from ecomsre.dta_v2.v21.contracts import (
    ActionProposalV21,
    DtaDiagnosisV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.planner_contracts import EvidencePlanDecisionV21
from ecomsre.dta_v2.v21.prompts import (
    ACTION_SELECTION_SYSTEM_PROMPT_V21,
    FLAT_ADAPTIVE_SYSTEM_PROMPT_V21,
    ONE_SHOT_SYSTEM_PROMPT_V21,
    PLANNER_SYSTEM_PROMPT_V21,
)
from ecomsre.dta_v2.v21.registry import load_default_runbook_registry


_PROMPT_BY_ARM = {
    AgentArmV21.ONE_SHOT_FULL_CONTEXT: ONE_SHOT_SYSTEM_PROMPT_V21,
    AgentArmV21.FLAT_ADAPTIVE: FLAT_ADAPTIVE_SYSTEM_PROMPT_V21,
    AgentArmV21.EVIDENCE_GUIDED_PLANNER: PLANNER_SYSTEM_PROMPT_V21,
}


def _source_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("identity source path is missing or unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_three_arm_identities_v21(
    *, model_id: str, max_completion_tokens: int
) -> tuple[AgentIdentityManifestV21, ...]:
    repository_root = Path(__file__).resolve().parents[4]
    registry = load_default_runbook_registry(repository_root)
    common_tool_schema = semantic_sha256(
        {
            "query_metrics": QueryMetricsRequest.model_json_schema(mode="validation"),
            "search_logs": SearchLogsRequest.model_json_schema(mode="validation"),
            "query_trace_neighborhood": TraceNeighborhoodRequest.model_json_schema(
                mode="validation"
            ),
            "inspect_service_runtime": InspectServiceRuntimeRequest.model_json_schema(
                mode="validation"
            ),
            "inspect_resource_usage": InspectResourceUsageRequest.model_json_schema(
                mode="validation"
            ),
        }
    )
    diagnosis_schema = semantic_sha256(
        DtaDiagnosisV21.model_json_schema(mode="validation")
    )
    action_selection_schema = semantic_sha256(
        ActionSelectionDecisionV21.model_json_schema(mode="validation")
    )
    action_proposal_schema = semantic_sha256(
        ActionProposalV21.model_json_schema(mode="validation")
    )
    planner_schema = semantic_sha256(
        EvidencePlanDecisionV21.model_json_schema(mode="validation")
    )
    planner_contracts_source = _source_sha256(
        Path(__file__).with_name("planner_contracts.py")
    )
    planner_runtime_source = _source_sha256(Path(__file__).with_name("planner.py"))
    context_source = _source_sha256(Path(__file__).with_name("context_projection.py"))
    agent_contracts_source = _source_sha256(
        Path(__file__).with_name("agent_contracts.py")
    )
    agent_runtime_source = _source_sha256(Path(__file__).with_name("agent.py"))
    provider_adapter_source = _source_sha256(
        Path(__file__).with_name("agent_provider.py")
    )
    candidate_source = _source_sha256(Path(__file__).with_name("candidate_filter.py"))

    identities: list[AgentIdentityManifestV21] = []
    for arm in AgentArmV21:
        payload: dict[str, object] = {
            "schema_version": "dta-v21.agent-identity.v1",
            "arm": arm,
            "model_id": model_id,
            "temperature": 0.0,
            "provider_adapter_version": PROVIDER_ADAPTER_VERSION_V21,
            "system_prompt_sha256": semantic_sha256(
                {
                    "investigation": _PROMPT_BY_ARM[arm],
                    "action_selection": ACTION_SELECTION_SYSTEM_PROMPT_V21,
                }
            ),
            "tool_schema_sha256": common_tool_schema,
            "planner_schema_sha256": (
                planner_schema if arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER else None
            ),
            "planner_contracts_source_sha256": (
                planner_contracts_source
                if arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER
                else None
            ),
            "planner_runtime_source_sha256": (
                planner_runtime_source
                if arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER
                else None
            ),
            "diagnosis_schema_sha256": diagnosis_schema,
            "action_selection_schema_sha256": action_selection_schema,
            "action_proposal_schema_sha256": action_proposal_schema,
            "context_projection_source_sha256": context_source,
            "agent_contracts_source_sha256": agent_contracts_source,
            "agent_runtime_source_sha256": agent_runtime_source,
            "provider_adapter_source_sha256": provider_adapter_source,
            "registry_sha256": registry.registry_sha256,
            "candidate_filter_source_sha256": candidate_source,
            "max_completion_tokens": max_completion_tokens,
        }
        identities.append(
            AgentIdentityManifestV21.model_validate(
                {**payload, "identity_sha256": semantic_sha256(payload)}
            )
        )
    return tuple(identities)


__all__ = ("build_three_arm_identities_v21",)
