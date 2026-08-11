from __future__ import annotations

from collections import Counter
import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rcaeval_adaptive.v2_runner import RequestPacer
from ecomsre_rcaeval_v2.dev3_token_accounting import AttemptBudget
from ecomsre_rca_unified.compact_contracts import (
    CompactBaseContext,
    CompactEdge,
    CompactEntity,
    CompactEvidence,
    CompactRetrievalSource,
    CompactRootSelection,
    resolve_compact_selection,
)
from ecomsre_rca_unified.compact_prompt import (
    OpenAICompatibleCompactProvider,
    build_request_payload,
    prompt_hashes,
)
from ecomsre_rca_unified.compact_retrieval import build_compact_candidate_context
from ecomsre_rca_unified.compact_runtime import (
    Arm,
    CaseRef,
    CompactTerminalStatus,
    ScheduledArm,
    execute_scheduled_arm,
    paired_schedule,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer


def _entity(
    name: str,
    layer: CanonicalEntityLayer,
    *,
    service: str | None = None,
    parent: str | None = None,
) -> CompactEntity:
    return CompactEntity(
        entity_ref=f"apm|apm.{layer.value.casefold()}|{name}",
        display_name=name,
        layer=layer,
        service_ancestor_or_none=service,
        parent_ref_or_none=parent,
    )


def _fixture() -> tuple[CompactBaseContext, CompactRetrievalSource]:
    alert = "apm|apm.service|alert"
    upstream = "apm|apm.service|upstream"
    workload = "apm|apm.workload|work"
    pod = "apm|apm.pod|pod"
    node = "apm|apm.node|node"
    database = "apm|apm.database|db"
    cache = "apm|apm.cache|cache"
    entities = (
        _entity("alert", CanonicalEntityLayer.SERVICE, service=alert),
        _entity("upstream", CanonicalEntityLayer.SERVICE, service=upstream),
        _entity(
            "work",
            CanonicalEntityLayer.WORKLOAD,
            service=upstream,
            parent=upstream,
        ),
        _entity(
            "pod",
            CanonicalEntityLayer.POD,
            service=upstream,
            parent=workload,
        ),
        _entity("node", CanonicalEntityLayer.NODE, parent=None),
        _entity("db", CanonicalEntityLayer.DATABASE, service=upstream),
        _entity("cache", CanonicalEntityLayer.CACHE, service=upstream),
    )
    base = CompactBaseContext(
        alert_title="Synthetic visible alert",
        prompt_text="Choose the causal root from visible bounded evidence.",
        alert_entity_ref=alert,
        entities=entities,
        evidence=(
            CompactEvidence(
                evidence_ref="metric:0001",
                source="METRICS",
                entity_ref=pod,
                name="cpu",
                started_at=101.0,
                ended_at=102.0,
                score=9.0,
                summary="metric anomaly",
            ),
            CompactEvidence(
                evidence_ref="log:0001",
                source="LOGS",
                entity_ref=database,
                name="error",
                started_at=100.0,
                ended_at=100.0,
                score=8.0,
                summary="error log",
            ),
            CompactEvidence(
                evidence_ref="trace:0001",
                source="TRACES",
                entity_ref=alert,
                name="slow span",
                started_at=103.0,
                ended_at=104.0,
                score=7.0,
                summary="slow span",
            ),
        ),
        source_status={
            "METRICS": "AVAILABLE",
            "LOGS": "AVAILABLE",
            "TRACES": "AVAILABLE",
        },
    )
    source = CompactRetrievalSource(
        entities=entities,
        edges=(
            CompactEdge(
                source_entity_ref=pod,
                target_entity_ref=workload,
                edge_type="PARENT",
            ),
            CompactEdge(
                source_entity_ref=workload,
                target_entity_ref=upstream,
                edge_type="PARENT",
            ),
            CompactEdge(
                source_entity_ref=upstream,
                target_entity_ref=alert,
                edge_type="DIRECTED_TOPOLOGY",
            ),
            CompactEdge(
                source_entity_ref=database,
                target_entity_ref=alert,
                edge_type="EXPLICIT_DEPENDENCY",
            ),
            CompactEdge(
                source_entity_ref=node,
                target_entity_ref=pod,
                edge_type="UNDIRECTED",
            ),
        ),
        source_visibility={
            pod: frozenset({"METRICS"}),
            database: frozenset({"LOGS", "EVENTS"}),
            alert: frozenset({"TRACES", "ALERTS"}),
            cache: frozenset({"METRICS"}),
        },
        source_occurrences={
            pod: {"METRICS": 2},
            database: {"LOGS": 2, "EVENTS": 1},
            alert: {"TRACES": 1, "ALERTS": 1},
            cache: {"METRICS": 1},
        },
        first_anomaly_time={database: 100.0, pod: 101.0, alert: 103.0},
        metrics_ranking=(pod, cache, database),
        metrics_scores={pod: 9.0, cache: 6.0, database: 4.0},
        alert_entities=(alert,),
    )
    return base, source


def test_fixed_r1_r6_policy_slots_order_and_stable_candidate_ids() -> None:
    base, source = _fixture()
    context = build_compact_candidate_context(base, source)

    assert 4 <= len(context.candidates) <= 12
    assert tuple(card.candidate_id for card in context.candidates) == tuple(
        f"C{index:02d}" for index in range(1, len(context.candidates) + 1)
    )
    reasons = {
        reason for card in context.candidates for reason in card.retrieval_reasons
    }
    assert reasons == {
        "DIRECT_EVIDENCE",
        "EVIDENCE_ANCESTOR",
        "UPSTREAM_DEPENDENCY",
        "EARLIEST_ANOMALY",
        "METRICS_TOPK",
        "ALERT_RELATED",
    }
    assert [card.entity_ref for card in context.candidates] == [
        card.entity_ref
        for card in build_compact_candidate_context(
            base,
            source.model_copy(update={"entities": tuple(reversed(source.entities))}),
        ).candidates
    ]
    assert "apm|apm.node|node" not in {item.entity_ref for item in context.candidates}


def test_service_and_layer_diversity_caps_apply_before_id_assignment() -> None:
    common_service = "apm|apm.service|shared"
    entities = (
        _entity("shared", CanonicalEntityLayer.SERVICE, service=common_service),
        *tuple(
            _entity(
                f"db{index}",
                CanonicalEntityLayer.DATABASE,
                service=common_service,
            )
            for index in range(8)
        ),
        *tuple(
            _entity(f"node{index}", CanonicalEntityLayer.NODE) for index in range(8)
        ),
    )
    visibility = {item.entity_ref: frozenset({"METRICS"}) for item in entities}
    source = CompactRetrievalSource(
        entities=entities,
        edges=(),
        source_visibility=visibility,
        source_occurrences={ref: {"METRICS": 1} for ref in visibility},
        first_anomaly_time={ref: float(index) for index, ref in enumerate(visibility)},
        metrics_ranking=tuple(visibility)[:6],
        metrics_scores={
            ref: float(10 - index) for index, ref in enumerate(tuple(visibility)[:6])
        },
        alert_entities=(),
    )
    base = CompactBaseContext(
        alert_title="Synthetic diversity alert",
        prompt_text="Use bounded evidence.",
        entities=entities,
        evidence=(),
        source_status={
            "METRICS": "AVAILABLE",
            "LOGS": "SOURCE_UNAVAILABLE",
            "TRACES": "SOURCE_UNAVAILABLE",
        },
    )
    context = build_compact_candidate_context(base, source)
    service_counts = Counter(
        card.service_ancestor_or_none for card in context.candidates
    )
    layer_counts = Counter(card.entity_layer for card in context.candidates)

    assert service_counts[common_service] <= 3
    assert max(layer_counts.values()) <= 6
    assert len(context.candidates) <= 12


def test_strict_candidate_id_mapping_and_visible_ref_validation() -> None:
    base, source = _fixture()
    context = build_compact_candidate_context(base, source)
    selection = CompactRootSelection(
        root_candidate_id="C01",
        fault_type="dependency timeout",
        confidence=0.8,
        evidence_refs=("log:0001",),
        summary="The upstream evidence precedes the alert symptom.",
    )
    resolved = resolve_compact_selection(
        selection,
        context=context,
        visible_evidence_refs=frozenset(item.evidence_ref for item in base.evidence),
    )

    assert resolved.root_cause_entity_ref == context.candidates[0].entity_ref
    with pytest.raises(ValueError, match="absent"):
        resolve_compact_selection(
            selection.model_copy(update={"root_candidate_id": "C12"}),
            context=context,
            visible_evidence_refs=frozenset(
                item.evidence_ref for item in base.evidence
            ),
        )
    with pytest.raises(ValueError, match="non-visible"):
        resolve_compact_selection(
            selection.model_copy(update={"evidence_refs": ("metric:9999",)}),
            context=context,
            visible_evidence_refs=frozenset(
                item.evidence_ref for item in base.evidence
            ),
        )
    with pytest.raises(ValidationError):
        CompactRootSelection(
            root_candidate_id="C01",
            fault_type="dependency timeout",
            confidence=0.8,
            evidence_refs=("log:0001", "log:0001"),
            summary="duplicate refs are invalid",
        )


def test_compact_schema_has_no_free_entity_or_reasoning_array() -> None:
    base, source = _fixture()
    context = build_compact_candidate_context(base, source)
    payload = build_request_payload(
        model="synthetic-model",
        base=base,
        arm="C1",
        candidates=context,
        max_completion_tokens=512,
    )
    parameters = payload["tools"][0]["function"]["parameters"]  # type: ignore[index]
    encoded_schema = json.dumps(parameters, sort_keys=True)

    assert "root_candidate_id" in encoded_schema
    assert "root_cause_entity_ref" not in encoded_schema
    assert "reasoning_steps" not in encoded_schema
    assert payload["tools"][0]["function"]["strict"] is True  # type: ignore[index]
    assert prompt_hashes()["b0_system_prompt_sha256"] == (
        "6b64c9e43f25029ca2f76f491faf98906c70fe888270284bf4bd3ff47e564049"
    )


def test_retrieval_has_no_ground_truth_or_benchmark_routing_dependency() -> None:
    source = inspect.getsource(build_compact_candidate_context).casefold()
    signature = str(inspect.signature(build_compact_candidate_context)).casefold()

    assert "ground_truth" not in source + signature
    assert "benchmark" not in source + signature
    assert set(CompactRetrievalSource.model_fields).isdisjoint(
        {"ground_truth", "benchmark", "case_id"}
    )


def test_paired_schedule_is_seeded_alternating_and_one_candidate_only() -> None:
    cases = tuple(
        CaseRef(source="RCA100", source_key=f"source-{index}") for index in range(1, 7)
    )
    first = paired_schedule(cases)
    second = paired_schedule(tuple(reversed(cases)))

    assert len(first) == 12
    assert [item.arm for item in first[:4]] == [Arm.B0, Arm.C1, Arm.C1, Arm.B0]
    assert {item.arm for item in first} == {Arm.B0, Arm.C1}
    assert first != second
    assert all(item.source == "RCA100" for item in first)


class _SyntheticTransport:
    def __init__(self, *, model: str, arguments: dict[str, object]) -> None:
        self.model = model
        self.arguments = arguments
        self.calls = 0

    def post_json(self, **_kwargs: object) -> dict[str, object]:
        self.calls += 1
        return {
            "model": self.model,
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "submit_compact_root_selection",
                                    "arguments": json.dumps(self.arguments),
                                },
                            }
                        ],
                    },
                }
            ],
        }


def test_c1_executes_one_model_call_and_zero_specialist_or_fusion_calls() -> None:
    base, source = _fixture()
    context = build_compact_candidate_context(base, source)
    transport = _SyntheticTransport(
        model="synthetic-model",
        arguments={
            "root_candidate_id": "C01",
            "fault_type": "dependency timeout",
            "confidence": 0.8,
            "evidence_refs": ["log:0001"],
            "summary": "Upstream evidence precedes the visible alert.",
        },
    )
    provider = OpenAICompatibleCompactProvider(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="synthetic-not-a-secret",
            model="synthetic-model",
        ),
        expected_model="synthetic-model",
        timeout_seconds=30.0,
        max_completion_tokens=512,
        transport=transport,
    )
    resolved = provider.diagnose(base=base, arm="C1", candidates=context)

    assert provider.calls == 1
    assert transport.calls == 1
    assert resolved.root_cause_entity_ref == context.candidates[0].entity_ref  # type: ignore[union-attr]
    assert not hasattr(provider, "specialize")
    assert not hasattr(provider, "fuse")


def test_runtime_terminal_records_one_call_and_no_agent_fanout(tmp_path: Path) -> None:
    base, source = _fixture()
    context = build_compact_candidate_context(base, source)
    transport = _SyntheticTransport(
        model="synthetic-model",
        arguments={
            "root_candidate_id": "C01",
            "fault_type": "dependency timeout",
            "confidence": 0.8,
            "evidence_refs": ["log:0001"],
            "summary": "Upstream evidence precedes the visible alert.",
        },
    )
    record = ScheduledArm(
        split="PREFLIGHT",
        pair_position=1,
        arm_position=1,
        opaque_case_id="case-0123456789abcdefabcd",
        source="RCA100",
        source_key="synthetic",
        arm=Arm.C1,
        run_id="0123456789abcdef0123456789abcdef",
    )
    terminal = execute_scheduled_arm(
        record,
        base=base,
        candidates=context,
        journal_root=tmp_path / "journal",
        output_root=tmp_path / "output",
        schedule_sha256="a" * 64,
        implementation_lock_sha256="b" * 64,
        provider_config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="synthetic-not-a-secret",
            model="synthetic-model",
        ),
        expected_model="synthetic-model",
        timeout_seconds=30.0,
        max_completion_tokens=512,
        prompt_token_reservation=1_024,
        pacer=RequestPacer(0.0),
        budget=AttemptBudget(
            max_provider_attempts=1,
            max_retry_attempts=0,
            prompt_token_reservation=1_024,
            max_completion_tokens=512,
            max_conservative_tokens=1_536,
        ),
        retry_policy_sha256="c" * 64,
        base_transport=transport,
    )

    assert terminal.status is CompactTerminalStatus.COMPLETED
    assert terminal.semantic_model_operations == 1
    assert terminal.provider_attempts == 1
    assert terminal.transport_retries == 0
    assert terminal.specialist_calls == 0
    assert terminal.fusion_model_calls == 0
