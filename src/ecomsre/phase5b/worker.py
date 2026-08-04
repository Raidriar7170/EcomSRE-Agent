"""Ground-truth-free mock worker boundary used only by the protocol dry run."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import Field, StrictBool, StrictInt

from ecomsre.phase5b.contracts import Phase5BModel, VariantName


class MockWorkerRequest(Phase5BModel):
    instance_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    variant: VariantName
    agent_visible: dict[str, str | StrictBool]


class MockWorkerResult(Phase5BModel):
    instance_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    variant: VariantName
    terminal_status: Literal["COMPLETED", "WORKFLOW_FAILURE"]
    decision: Literal["RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"] | None
    tool_calls: StrictInt = Field(ge=0)
    failure_code: Literal["MOCK_WORKFLOW_FAILURE"] | None


def run_mock_worker(request_payload: dict[str, object]) -> MockWorkerResult:
    request = MockWorkerRequest.model_validate(request_payload)
    if request.agent_visible.get("inject_terminal_failure") is True:
        return MockWorkerResult(
            instance_id=request.instance_id,
            variant=request.variant,
            terminal_status="WORKFLOW_FAILURE",
            decision=None,
            tool_calls=1,
            failure_code="MOCK_WORKFLOW_FAILURE",
        )
    raw_decision = request.agent_visible.get("synthetic_decision_signal")
    if raw_decision not in {"RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"}:
        raise ValueError("synthetic worker input has no admitted decision signal")
    decision = cast(
        Literal["RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"], raw_decision
    )
    tool_calls = {
        "SINGLE_AGENT_V2": 4,
        "FIXED_SPECIALIST_V2": 3,
        "DYNAMIC_MULTI_AGENT_V2": 2,
    }[request.variant]
    return MockWorkerResult(
        instance_id=request.instance_id,
        variant=request.variant,
        terminal_status="COMPLETED",
        decision=decision,
        tool_calls=tool_calls,
        failure_code=None,
    )
