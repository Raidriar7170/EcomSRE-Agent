"""Event-scoped references and structural source choices; no winning rule input."""

from copy import deepcopy
import itertools
import json
from types import SimpleNamespace

import pytest

from ecomsre.product.knowledge import drafts_v050 as d
from ecomsre.product.knowledge.compiler import (
    _CORE_SOURCE,
    _ANOMALY_SOURCE,
    _predicate_parts,
)
from test_final_closure import closure  # noqa: F401


def discovery():
    rows = []
    for n in range(2):
        obs = dict(
            evidence_ref="same-ref",
            object_sha256=str(n) * 64,
            source="RUNTIME",
            status="SUCCESS_NONEMPTY",
            truncated=False,
            covered_services=["payment"],
            records=[dict(service="payment", healthy=bool(n))],
            window=dict(started_at=str(n), ended_at=str(n + 1)),
        )
        obs["observation_identity"] = dict(
            incident_id=str(n),
            parent_diagnosis_id="diag" + str(n),
            observation_sha256=d.sha(obs),
            object_sha256=obs["object_sha256"],
        )
        rows.append(
            dict(
                incident_id=str(n),
                observations=[obs],
                status="COMPLETED",
                hypotheses=[],
                decisions=[],
                deployable_resource_dependencies=[],
            )
        )
    return dict(
        sessions=rows,
        snapshot_sha256="a" * 64,
        feature_catalog={},
        predicate_catalog=sorted(
            ["core:" + p.value for p in _CORE_SOURCE]
            + ["ga:" + p.value for p in _ANOMALY_SOURCE]
        ),
    )


def view(data=None):
    return d.scoped_view(
        data or discovery(),
        request_key="knowledge-draft-v050.final:proposal:2",
        target="payment",
        members=["0", "1"],
        event_bound=True,
    )


def test_event_binding_same_reference_different_content_and_order():
    data = discovery()
    a = view(data)
    data["sessions"].reverse()
    b = view(data)
    assert a == b
    by_event = {r["incident_id"]: r for r in a["target_evidence"].values()}
    assert by_event["0"]["object_sha256"] != by_event["1"]["object_sha256"]
    assert by_event["0"]["records"] != by_event["1"]["records"]
    assert by_event["0"]["observation_identity"]["parent_diagnosis_id"] == "diag0"


def test_same_cas_content_does_not_merge_event_or_window():
    data = discovery()
    a, b = [s["observations"][0] for s in data["sessions"]]
    b["records"] = deepcopy(a["records"])
    b["object_sha256"] = a["object_sha256"]
    b["observation_identity"]["object_sha256"] = a["object_sha256"]
    b["observation_identity"]["observation_sha256"] = d.sha(
        {k: v for k, v in b.items() if k != "observation_identity"}
    )
    result = view(data)
    rows = list(result["target_evidence"].values())
    assert len(rows) == 2 and rows[0]["object_sha256"] == rows[1]["object_sha256"]
    assert rows[0]["observation_identity"] != rows[1]["observation_identity"]
    assert rows[0]["window"] != rows[1]["window"]


def test_same_event_conflict_and_changed_object_fail_closed():
    data = discovery()
    other = deepcopy(data["sessions"][0]["observations"][0])
    other["object_sha256"] = "f" * 64
    data["sessions"][0]["observations"].append(other)
    with pytest.raises(ValueError, match="BINDING|CONFLICT"):
        view(data)
    other["observation_identity"]["observation_sha256"] = d.sha(
        {k: v for k, v in other.items() if k != "observation_identity"}
    )
    with pytest.raises(ValueError, match="CONFLICT"):
        view(data)


def source_draft(v, values):
    return dict(
        disposition="CANDIDATE",
        reason="Fixture, not model output.",
        binding_id=v["binding_id"],
        candidate=dict(
            name="fixture",
            target="payment",
            broad_domain="UNKNOWN",
            member_incidents=list(v["members"]),
            predicates=dict(
                first=values[0],
                second=values[1],
                third=values[2] if len(values) == 3 else None,
            ),
            expression=None,
            target_support=list(v["target_evidence"]),
            target_counterevidence=[],
            comparison_context=[],
            confusable_patterns=["unknown"],
            prediction="fixture only",
            inapplicable_conditions=["incomplete"],
        ),
    )


def test_schema_exhaustively_retains_all_legal_level_a_combinations():
    v = view()
    schema = d.scoped_schema(v)
    predicate_schema = schema["$defs"]["SourceCandidateDraft"]["properties"][
        "predicates"
    ]

    def accepts_value(node, value):
        if "$ref" in node:
            return accepts_value(schema["$defs"][node["$ref"].rsplit("/",1)[1]], value)
        if "anyOf" in node:
            return any(accepts_value(part,value) for part in node["anyOf"])
        if node.get("type") == "null":
            return value is None
        return value in node["enum"]

    def accepts(wire):
        return any(set(wire) == set(node["required"]) and
            all(accepts_value(node["properties"][key], value) for key,value in wire.items())
            for node in predicate_schema["anyOf"])
    legal = 0
    for size in (1, 2, 3):
        for ps in itertools.combinations(v["predicate_catalog"], size):
            allowed = len({_predicate_parts(p)[1] for p in ps}) >= 2
            representations = []
            for permutation in itertools.permutations(ps):
                if size < 2:
                    continue
                wire = dict(
                    first=permutation[0],
                    second=permutation[1],
                    third=permutation[2] if size == 3 else None,
                )
                representations.append(accepts(wire))
            assert any(representations) == allowed
            legal += allowed
    assert legal == 3509
    # No outcome, feedback, support or negative example may trim this catalog.
    changed = deepcopy(v)
    changed["feedback"] = {"all_conditions": "UNKNOWN"}
    changed["binding_id"] = d.sha(
        {k: x for k, x in changed.items() if k != "binding_id"}
    )
    assert (
        d.scoped_schema(changed)["$defs"]["SourceCandidateDraft"]["properties"][
            "predicates"
        ]
        == predicate_schema
    )
    for first in v["predicate_catalog"]:
        for second in v["predicate_catalog"]:
            wire = dict(first=first, second=second, third=None)
            assert accepts(wire) == (
                _predicate_parts(first)[1] != _predicate_parts(second)[1]
            )
    # Duplicate optional third is still rejected by local semantic validation.
    with pytest.raises(ValueError, match="DUPLICATE"):
        d.SourceConjunction(
            first="core:LOG_CONFIGURATION_ERROR",
            second="core:RUNTIME_UNHEALTHY",
            third="core:LOG_CONFIGURATION_ERROR",
        )


def test_source_compilation_preserves_exact_model_selected_conditions():
    v = view()
    values = [
        "core:LOG_CONFIGURATION_ERROR",
        "core:METRIC_LATENCY_STRONG",
        "ga:RESOURCE_CPU_OUTLIER",
    ]
    parsed = d.SourceScopedKnowledgeDraft.model_validate(source_draft(v, values))
    proposal, _ = d.compile_scoped_draft(parsed, v)
    assert proposal.predicates == values and proposal.expression is None
    abstain = d.SourceScopedKnowledgeDraft(
        disposition="NEEDS_OBSERVATION",
        candidate=None,
        reason="Insufficient evidence",
        binding_id=v["binding_id"],
    )
    with pytest.raises(ValueError, match="NEEDS_OBSERVATION"):
        d.compile_scoped_draft(abstain, v)


def test_actual_source_wire_payload(tmp_path):
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import PriceSchedule
    from ecomsre.product.investigation.provider import StructuredProvider
    from ecomsre.product.investigation import closure_budget

    repo = repository(tmp_path)
    with repo.store.connect() as c:
        initial = closure_budget.ledger(c)
    closure_budget.start(repo.store, expected_ledger_sha256=d.sha(initial))
    v = view()
    v["request_key"] = closure_budget.PROPOSAL_PREFIX + "0"
    v["binding_id"] = d.sha({k: x for k, x in v.items() if k != "binding_id"})
    raw = source_draft(
        v, ["core:LOG_CONFIGURATION_ERROR", "core:METRIC_LATENCY_STRONG"]
    )
    captured = []

    def post(**kw):
        payload = kw["payload"]
        captured.append(payload)
        assert payload["tools"][0]["parameters"] == d.scoped_schema(v)
        assert d.SourceScopedKnowledgeDraft.model_validate(raw).candidate is not None
        assert payload["tools"][0]["parameters"]["$defs"]["SourceCandidateDraft"][
            "properties"
        ]["predicates"]["anyOf"]
        assert json.loads(payload["input"][0]["content"])[
            "view"
        ] == d.scoped_model_view(v)
        return dict(
            model="gpt-5.4-mini-2026-03-17",
            status="completed",
            id="fixture",
            output=[
                dict(
                    type="function_call",
                    status="completed",
                    name="submit_proposal",
                    arguments=json.dumps(raw),
                )
            ],
            usage=dict(input_tokens=100, output_tokens=100),
        )

    provider = StructuredProvider(
        OpenAICompatibleConfig(
            "https://example.test/v1", "fixture", "gpt-5.4-mini-2026-03-17"
        ),
        PriceSchedule(
            provider_profile="fixture",
            model="gpt-5.4-mini-2026-03-17",
            as_of="2026-09-17",
            source="fixture",
            input_usd_per_million=0.75,
            output_usd_per_million=4.5,
        ),
        repo,
        SimpleNamespace(post_json=post),
        api_style="responses",
    )
    got = provider.complete(
        key=v["request_key"],
        task=d.SOURCE_TASK,
        view=d.scoped_model_view(v),
        schema=d.SourceScopedKnowledgeDraft,
        scoped_binding=v,
        max_output_tokens=8192,
    )
    assert len(captured) == 1 and got.model_dump(mode="json") == raw


def test_verified_discovery_rejects_valid_but_wrong_event_object(closure, monkeypatch):  # noqa: F811
    runner = closure.runner
    evo = runner.evo
    ids = list(runner.plan["original_incidents"].values())
    good = evo.verified_discovery_view(
        runner.environment_id, ids, record_exposure=False
    )
    assert all(
        o["observation_identity"]["incident_id"] == s["incident_id"]
        for s in good["sessions"]
        for o in s["observations"]
    )
    old = evo.discovery_view(runner.environment_id, ids, record_exposure=False)
    a, b = old["sessions"][:2]
    first = next(o for o in a["observations"] if o["source"] == "RUNTIME")
    other = next(o for o in b["observations"] if o["source"] == "RUNTIME")
    first["object_sha256"] = other["object_sha256"]
    monkeypatch.setattr(evo, "discovery_view", lambda *a, **kw: old)
    with pytest.raises(ValueError, match="EVENT_OBSERVATION_OBJECT_MISMATCH"):
        evo.verified_discovery_view(runner.environment_id, ids, record_exposure=False)
