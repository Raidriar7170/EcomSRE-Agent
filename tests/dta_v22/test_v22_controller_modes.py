from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    ControllerDecisionKindV22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.controller_modes import (
    PRIMARY_MODEL_V22,
    ControllerIdentityManifestV22,
    EvaluationArmV22,
    OneShotOracleContextV22,
    ProviderOutputModeV22,
    ProviderProbeStatusV22,
    build_controller_identity_manifests_v22,
    build_one_shot_oracle_context_v22,
    probe_provider_output_mode_v22,
    select_deterministic_router_decision_v22,
)
from ecomsre.dta_v2.v22.memory import FullEvidenceMemoryV22, build_memory_views_v22
from ecomsre.dta_v2.v22.protocol_suite import (
    _action_v22,
    _baseline_v22,
    _memory_v22,
    _outcome_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)


def _actions(
    *, executed_action_ids: tuple[str, ...] = (), remaining_budget: float = 3.0
):
    topology = StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=executed_action_ids,
        remaining_budget=remaining_budget,
    )


def _empty_full_memory() -> FullEvidenceMemoryV22:
    payload = {
        "schema_version": "dta-v22.full-evidence-memory.v1",
        "baseline_sha256": "1" * 64,
        "observed_at": datetime(2026, 8, 20, tzinfo=timezone.utc),
        "minimal_index": (),
        "full_observations": (),
    }
    draft = FullEvidenceMemoryV22.model_construct(
        **payload,
        memory_sha256="0" * 64,
    )
    return FullEvidenceMemoryV22.model_validate(
        {
            **payload,
            "memory_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"memory_sha256"})
            ),
        }
    )


def _complete_full_memory() -> FullEvidenceMemoryV22:
    actions = _actions()
    _, partial = _memory_v22(anomaly=False)
    outcomes = list(partial.full_observations)
    for source in (
        EvidenceSourceV22.LOGS,
        EvidenceSourceV22.TRACES,
        EvidenceSourceV22.RESOURCES,
        EvidenceSourceV22.CHANGES,
    ):
        action = _action_v22(actions, source=source, targets=("payment",))
        outcomes.append(
            _outcome_v22(
                action=action,
                status=ReadSourceStatusV22.SUCCESS_EMPTY,
                records=(),
            )
        )
    _, full = build_memory_views_v22(
        outcomes=tuple(outcomes),
        baseline=_baseline_v22(),
        observed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        top_k=64,
    )
    return full


def test_provider_probe_prefers_strict_and_uses_the_same_lightweight_schema() -> None:
    calls: list[tuple[str, ProviderOutputModeV22, str]] = []

    def probe(
        model: str,
        mode: ProviderOutputModeV22,
        schema_sha256: str,
    ) -> ProviderProbeStatusV22:
        calls.append((model, mode, schema_sha256))
        return ProviderProbeStatusV22.SUPPORTED

    report = probe_provider_output_mode_v22(probe=probe)
    assert report.model == PRIMARY_MODEL_V22
    assert report.selected_mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
    assert report.provider_calls == 1
    assert len(calls) == 1
    assert calls[0][2] == report.controller_schema_sha256


def test_provider_probe_falls_back_only_for_strict_schema_unsupported() -> None:
    calls: list[tuple[ProviderOutputModeV22, str]] = []

    def probe(
        _model: str,
        mode: ProviderOutputModeV22,
        schema_sha256: str,
    ) -> ProviderProbeStatusV22:
        calls.append((mode, schema_sha256))
        return (
            ProviderProbeStatusV22.STRICT_SCHEMA_UNSUPPORTED
            if mode is ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT
            else ProviderProbeStatusV22.SUPPORTED
        )

    report = probe_provider_output_mode_v22(probe=probe)
    assert report.selected_mode is ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON
    assert report.provider_calls == 2
    assert [item[0] for item in calls] == [
        ProviderOutputModeV22.STRICT_STRUCTURED_OUTPUT,
        ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
    ]
    assert len({item[1] for item in calls}) == 1

    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE"):
        probe_provider_output_mode_v22(
            probe=lambda _model, _mode, _schema: ProviderProbeStatusV22.FAILED
        )


def test_identity_manifests_share_model_schema_and_mode_but_bind_arm_difference() -> None:
    report = probe_provider_output_mode_v22(
        probe=lambda _model, _mode, _schema: ProviderProbeStatusV22.SUPPORTED
    )
    manifests = build_controller_identity_manifests_v22(provider_probe=report)
    assert tuple(item.arm for item in manifests) == tuple(EvaluationArmV22)
    assert len({item.identity_sha256 for item in manifests}) == 4
    assert {item.model for item in manifests} == {PRIMARY_MODEL_V22}
    assert {item.controller_schema_sha256 for item in manifests} == {
        report.controller_schema_sha256
    }
    assert {item.provider_output_mode for item in manifests} == {
        report.selected_mode
    }
    planner = next(
        item for item in manifests if item.arm is EvaluationArmV22.PLANNER_LITE_SALIENT
    )
    flat = next(
        item for item in manifests if item.arm is EvaluationArmV22.FLAT_CANONICAL_SALIENT
    )
    assert planner.receives_persistent_belief_ledger is True
    assert flat.receives_persistent_belief_ledger is False

    forged_draft = planner.model_copy(update={"model": "silent-model-swap"})
    with pytest.raises(ValueError, match="model continuity"):
        ControllerIdentityManifestV22.model_validate(
            forged_draft.model_copy(
                update={
                    "identity_sha256": semantic_sha256_v22(
                        forged_draft.model_dump(
                            mode="json",
                            exclude={"identity_sha256"},
                        )
                    )
                }
            ).model_dump(mode="python")
        )


def test_deterministic_router_is_generic_stable_and_never_dispatches_masked_action() -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _actions()
    first = select_deterministic_router_decision_v22(
        action_catalog=actions,
        hypothesis_catalog=hypotheses,
    )
    second = select_deterministic_router_decision_v22(
        action_catalog=actions,
        hypothesis_catalog=hypotheses,
    )
    assert first == second
    assert first.decision is ControllerDecisionKindV22.READ
    assert first.action_id in {item.action_id for item in actions.actions}
    assert set(inspect.signature(select_deterministic_router_decision_v22).parameters) == {
        "action_catalog",
        "hypothesis_catalog",
    }
    assert not {
        "truth",
        "fixture",
        "expected_mechanism",
        "case_id",
    }.intersection(inspect.signature(select_deterministic_router_decision_v22).parameters)

    refreshed = _actions(executed_action_ids=(first.action_id,))
    next_decision = select_deterministic_router_decision_v22(
        action_catalog=refreshed,
        hypothesis_catalog=hypotheses,
    )
    assert next_decision.action_id != first.action_id

    exhausted = _actions(
        executed_action_ids=tuple(item.action_id for item in actions.registry_actions),
        remaining_budget=0.0,
    )
    with pytest.raises(
        RuntimeError,
        match="DETERMINISTIC_ROUTER_FINAL_MODEL_REQUIRED",
    ):
        select_deterministic_router_decision_v22(
            action_catalog=exhausted,
            hypothesis_catalog=hypotheses,
        )


def test_one_shot_oracle_context_counts_full_materialization_and_has_no_tool_metric() -> None:
    actions = _actions()
    memory = _complete_full_memory()
    context = build_one_shot_oracle_context_v22(
        full_memory=memory,
        action_catalog=actions,
    )
    assert context.tool_selection_applicable is False
    assert context.context_materialization_bytes > 0
    assert context.canonical_action_ids == tuple(
        item.action_id for item in actions.registry_actions
    )
    assert context.full_memory_sha256 == memory.memory_sha256

    forged_draft = context.model_copy(
        update={
            "context_materialization_bytes": context.context_materialization_bytes - 1
        }
    )
    with pytest.raises(ValueError, match="full materialization"):
        OneShotOracleContextV22.model_validate(
            forged_draft.model_copy(
                update={
                    "context_sha256": semantic_sha256_v22(
                        forged_draft.model_dump(
                            mode="json",
                            exclude={"context_sha256"},
                        )
                    )
                }
            ).model_dump(mode="python"),
            context={"full_memory": memory, "action_catalog": actions},
        )


def test_one_shot_oracle_context_rejects_partial_materialization() -> None:
    with pytest.raises(ValueError, match="lacks all canonical enabled sources"):
        build_one_shot_oracle_context_v22(
            full_memory=_empty_full_memory(),
            action_catalog=_actions(),
        )
