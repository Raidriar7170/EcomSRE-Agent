"""Eight-transition live Provider smoke for the practical static adapter."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, StrictBool, StrictInt

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerTurnInputV22,
)
from ecomsre.dta_v2.v22.controller_runtime import initialize_controller_session_v22
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.predicates import build_default_evidence_support_policy_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import (
    _baseline,
    _bootstrap,
    _memory_outcome,
    _turn_input,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v22.simple_provider import (
    ProviderProtocolFailureV22,
    SHARED_SYSTEM_PROMPT_V22,
    SimpleProviderV22,
)


class SmokeTransitionResultV22(DtaModelV22):
    transition_id: str
    valid_output: StrictBool
    first_pass_protocol_success: StrictBool
    post_repair_protocol_success: StrictBool
    repair_used: StrictBool
    decision: str | None
    provider_calls: StrictInt = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    safe_error_code: str | None


class PracticalSmokeResultV22(DtaModelV22):
    schema_version: str = Field(pattern=r"^dta-v22\.practical-smoke-result\.v1$")
    transitions: tuple[SmokeTransitionResultV22, ...] = Field(min_length=8, max_length=8)
    post_repair_valid_outputs: StrictInt = Field(ge=0, le=8)
    first_pass_protocol_successes: StrictInt = Field(ge=0, le=8)
    semantic_repairs: StrictInt = Field(ge=0, le=8)
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: StrictInt = Field(ge=0, le=0)
    passed: StrictBool


def _smoke_input(
    *,
    repository_root: Path,
    case_id: str,
    arm: ControllerArmV22,
    extra_source: EvidenceSourceV22 | None,
) -> ControllerTurnInputV22:
    case_set = load_practical_case_set_v22(
        repository_root / "config/dta-v22-sprint/development/cases.json"
    )
    spec = next(item for item in case_set.cases if item.case_id == case_id)
    case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    run_id = "5" * 32
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=case.candidate_services
    )
    policy = build_default_evidence_support_policy_v22()
    outcomes, bootstrap, _, _ = _bootstrap(
        case=case,
        topology=topology,
        run_id=run_id,
    )
    selected_outcomes = list(outcomes)
    if extra_source is not None:
        catalog = build_action_catalog_v22(
            candidate_services=case.candidate_services,
            topology=topology,
            capability_registry=build_default_tool_capability_registry_v22(),
            executed_action_ids=(),
            remaining_budget=3.0,
        )
        action = next(
            item
            for item in catalog.actions
            if item.source is extra_source
            and spec.case_id in {"d01", "d08"}
            and item.target_services == (case.candidate_services[-1],)
        )
        backend = QuerySpecificReplayBackendV22(case.capture)
        selected_outcomes.append(
            _memory_outcome(
                action=action,
                outcome=backend.execute(action),
                run_id=run_id,
                dispatch_ordinal=1,
                observed_at=case.capture.captured_at,
            )
        )
    salient, _ = build_memory_views_v22(
        outcomes=tuple(selected_outcomes),
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    capabilities = build_default_tool_capability_registry_v22()
    catalog = build_action_catalog_v22(
        candidate_services=case.candidate_services,
        topology=topology,
        capability_registry=capabilities,
        executed_action_ids=(),
        remaining_budget=3.0,
    )
    identity = "6" * 64
    session = initialize_controller_session_v22(
        arm=arm,
        controller_identity_sha256=identity,
        hypothesis_catalog=hypotheses,
        bootstrap=bootstrap,  # type: ignore[arg-type]
        support_policy_sha256=policy.policy_sha256,
    )
    return _turn_input(
        arm=arm,
        run_id=run_id,
        identity_sha256=identity,
        session=session,
        bootstrap=bootstrap,
        hypotheses=hypotheses,
        catalog=catalog,
        salient=salient,
    )


def run_practical_provider_smoke_v22(
    *,
    repository_root: Path,
    provider: SimpleProviderV22,
    system_prompt: str = SHARED_SYSTEM_PROMPT_V22,
) -> PracticalSmokeResultV22:
    specifications = (
        ("read", "d01", ControllerArmV22.FLAT_CANONICAL, None, None),
        (
            "commit",
            "d01",
            ControllerArmV22.FLAT_CANONICAL,
            EvidenceSourceV22.TRACES,
            None,
        ),
        ("no_incident", "d07", ControllerArmV22.FLAT_CANONICAL, None, None),
        (
            "abstain",
            "d08",
            ControllerArmV22.FLAT_CANONICAL,
            EvidenceSourceV22.TRACES,
            None,
        ),
        (
            "invalid_alias_repair",
            "d01",
            ControllerArmV22.FLAT_CANONICAL,
            None,
            "UNKNOWN_H_ALIAS",
        ),
        (
            "invalid_json_repair",
            "d01",
            ControllerArmV22.FLAT_CANONICAL,
            None,
            "INVALID_JSON",
        ),
        ("flat_input", "d01", ControllerArmV22.FLAT_CANONICAL, None, None),
        ("planner_input", "d01", ControllerArmV22.PLANNER_LITE, None, None),
    )
    targets = {
        "read": "Return a valid READ decision for this smoke transition.",
        "commit": "Return a valid COMMIT decision citing exactly the minimum support.",
        "no_incident": "Return a valid NO_INCIDENT decision.",
        "abstain": "Return a valid ABSTAIN decision.",
        "flat_input": "Return a valid READ decision for this Flat input smoke.",
        "planner_input": "Return a valid READ decision for this Planner input smoke.",
        "invalid_alias_repair": "Repair the supplied alias error with one valid decision.",
        "invalid_json_repair": "Repair the supplied JSON error with one valid decision.",
    }
    results: list[SmokeTransitionResultV22] = []
    for transition_id, case_id, arm, extra_source, repair_code in specifications:
        turn_input = _smoke_input(
            repository_root=repository_root,
            case_id=case_id,
            arm=arm,
            extra_source=extra_source,
        )
        prompt = f"{system_prompt} {targets[transition_id]}"
        try:
            if repair_code is None:
                outcome = provider.complete_turn(
                    turn_input=turn_input,
                    run_id=f"smoke-{transition_id}".replace("_", "-"),
                    system_prompt=prompt,
                )
            else:
                outcome = provider.complete_repair_turn(
                    turn_input=turn_input,
                    run_id=f"smoke-{transition_id}".replace("_", "-"),
                    safe_error_code=repair_code,
                    system_prompt=prompt,
                )
        except ProviderProtocolFailureV22 as error:
            results.append(
                SmokeTransitionResultV22(
                    transition_id=transition_id,
                    valid_output=False,
                    first_pass_protocol_success=False,
                    post_repair_protocol_success=False,
                    repair_used=repair_code is not None,
                    decision=None,
                    provider_calls=0,
                    transport_retries=0,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    safe_error_code=error.safe_code,
                )
            )
            continue
        results.append(
            SmokeTransitionResultV22(
                transition_id=transition_id,
                valid_output=True,
                first_pass_protocol_success=outcome.first_pass_protocol_success,
                post_repair_protocol_success=outcome.post_repair_protocol_success,
                repair_used=outcome.semantic_repair_used,
                decision=outcome.decision.decision.value,
                provider_calls=outcome.provider_calls,
                transport_retries=outcome.transport_retry_count,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                total_tokens=outcome.total_tokens,
                safe_error_code=None,
            )
        )
    valid = sum(item.valid_output for item in results)
    uncaught = 0
    return PracticalSmokeResultV22(
        schema_version="dta-v22.practical-smoke-result.v1",
        transitions=tuple(results),
        post_repair_valid_outputs=valid,
        first_pass_protocol_successes=sum(
            item.first_pass_protocol_success for item in results
        ),
        semantic_repairs=sum(item.repair_used for item in results),
        uncaught_exceptions=uncaught,
        agent_writes=0,
        passed=valid >= 7 and uncaught == 0,
    )


__all__ = (
    "PracticalSmokeResultV22",
    "SmokeTransitionResultV22",
    "run_practical_provider_smoke_v22",
)
