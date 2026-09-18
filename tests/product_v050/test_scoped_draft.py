from copy import deepcopy
import pytest
from test_draft_contract import materials
from ecomsre.product.knowledge import drafts_v050 as d


def bundle(key="request-one"):
    old, draft, _ = materials()
    return d.scoped_view(
        old, request_key=key, target="payment", members=["one", "two"]
    ), draft


def test_scoped_payload_has_short_role_specific_choices():
    view, _ = bundle()
    schema = d.scoped_schema(view)
    props = schema["$defs"]["CandidateDraft"]["properties"]
    assert props["target"]["enum"] == ["payment"]
    assert props["target_support"]["items"]["enum"] == list(view["target_evidence"])
    assert all(len(k) < 5 for k in view["target_evidence"])
    assert not set(view["target_evidence"]) & set(view["comparison_evidence"])
    assert "hypotheses" not in str(d.scoped_model_view(view))


def test_bound_context_cannot_cross_request_or_snapshot():
    view, draft = bundle()
    data = draft.model_dump(mode="json")
    data["binding_id"] = view["binding_id"]
    data["candidate"]["member_incidents"] = list(view["members"])
    data["candidate"]["target_support"] = list(view["target_evidence"])
    data["candidate"]["comparison_context"] = []
    data["candidate"]["expression"]["dependency_aliases"] = list(view["dependencies"])
    parsed = d.ScopedKnowledgeDraft.model_validate(data)
    proposal, _ = d.compile_scoped_draft(parsed, view)
    assert proposal.expression.threshold == 42
    assert proposal.member_incidents == ["one", "two"]
    with pytest.raises(ValueError, match="BINDING"):
        d.compile_scoped_draft(parsed, bundle("other-request")[0])
    bad = deepcopy(view)
    bad["snapshot_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="BINDING"):
        d.compile_scoped_draft(parsed, bad)


def test_aggregate_diagnostics_report_multiple_faults():
    view, draft = bundle()
    data = draft.model_dump(mode="json")
    data["binding_id"] = view["binding_id"]
    data["candidate"]["member_incidents"] = ["I01", "I02"]
    data["candidate"]["target_support"] = ["unknown"]
    data["candidate"]["target_counterevidence"] = list(view["comparison_evidence"])
    data["candidate"]["expression"]["dependency_aliases"] = ["unknown", "unknown"]
    data["candidate"]["expression"]["threshold_unit"] = "PERCENT"
    issues = d.diagnose_scoped_draft(data, view)
    assert {
        "UNKNOWN_TARGET_EVIDENCE",
        "DUPLICATE_DEPENDENCY",
        "UNKNOWN_DEPENDENCY",
        "UNIT_MISMATCH",
    } <= {x["code"] for x in issues}


def test_actual_transport_payload_and_truncation_accounting(tmp_path):
    import json
    from types import SimpleNamespace
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import PriceSchedule
    from ecomsre.product.investigation.provider import StructuredProvider
    from ecomsre.product.errors import ProductError

    key = "knowledge-draft-v050.2:proposal:0"
    view, _ = bundle(key)
    repo = repository(tmp_path)
    captured = []

    def post(**kw):
        payload = kw["payload"]
        captured.append(payload)
        assert "NOT online investigation" in payload["instructions"]
        assert payload["reasoning"] == {"effort": "medium"}
        assert payload["max_output_tokens"] == 8192
        assert payload["tools"][0]["parameters"] == d.scoped_schema(view)
        sent = json.loads(payload["input"][0]["content"])["view"]
        assert sent == d.scoped_model_view(view)
        assert "snapshot_sha256" not in sent
        return dict(
            model="gpt-5.4",
            id="fixture",
            status="incomplete",
            incomplete_details={"reason": "max_output_tokens"},
            output=[],
            usage={
                "input_tokens": 50,
                "output_tokens": 8192,
                "output_tokens_details": {"reasoning_tokens": 8000},
            },
        )

    provider = StructuredProvider(
        OpenAICompatibleConfig("https://example.test/v1", "fixture", "gpt-5.4"),
        PriceSchedule(
            provider_profile="fixture",
            model="gpt-5.4",
            as_of="2026-09-17",
            source="fixture",
            input_usd_per_million=0.75,
            output_usd_per_million=4.5,
        ),
        repo,
        SimpleNamespace(post_json=post),
        api_style="responses",
    )
    with pytest.raises(ProductError, match="Output token cap"):
        provider.complete(
            key=key,
            task=d.SCOPED_TASK,
            view=d.scoped_model_view(view),
            schema=d.ScopedKnowledgeDraft,
            scoped_binding=view,
            max_output_tokens=8192,
        )
    with repo.store.connect() as c:
        row = c.execute("select * from investigation_provider_calls_v050").fetchone()
    ledger = json.loads(row["payload_json"])
    assert ledger["reasoning_tokens"] == 8000
    assert ledger["incomplete_details"] == {"reason": "max_output_tokens"}
    assert row["charged_microusd"] > 36000
    assert row["reserved_microusd"] >= row["charged_microusd"]
    with pytest.raises(ProductError):
        provider.complete(
            key=key,
            task=d.SCOPED_TASK,
            view=d.scoped_model_view(view),
            schema=d.ScopedKnowledgeDraft,
            scoped_binding=view,
            max_output_tokens=8192,
        )
    assert len(captured) == 1


def test_incomplete_dependency_and_wrong_offsets_are_not_silently_fixed():
    old, _, _ = materials()
    rows = list(old["dependency_catalog"].values())
    rows[1]["availability"] = "LEGAL_NOT_COLLECTED"
    view = d.scoped_view(
        old, request_key="one", target="payment", members=["one", "two"]
    )
    assert len(view["dependencies"]) == 1
    assert d.scoped_schema(view)["$defs"]["CandidateDraft"]["properties"][
        "expression"
    ] == {"type": "null"}
    assert any(g.get("availability") == "LEGAL_NOT_COLLECTED" for g in view["gaps"])
    rows[1]["availability"] = "BOUND_OBSERVATION"
    rows[1]["dependency"]["window_offset_seconds"] = 30
    view = d.scoped_view(
        old, request_key="one", target="payment", members=["one", "two"]
    )
    assert d.scoped_schema(view)["$defs"]["CandidateDraft"]["properties"][
        "expression"
    ] == {"type": "null"}


def test_zero_request_read_path_does_not_initialize_tables_or_expose(tmp_path):
    import sqlite3
    from scripts.product_v050.knowledge_feasibility import ReadOnlyStore
    from ecomsre.product.knowledge.observations_v050 import load_observations
    from types import SimpleNamespace

    path = tmp_path / "db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE sentinel(value TEXT)")
    store = ReadOnlyStore(path)
    assert (
        load_observations(
            SimpleNamespace(incident_id="not-found"),
            SimpleNamespace(metadata_store=store),
        )
        == []
    )
    with store.connect() as c:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("INSERT INTO sentinel VALUES ('write')")
        assert [r[0] for r in c.execute("SELECT name FROM sqlite_master")] == [
            "sentinel"
        ]


def test_partial_multiservice_coverage_retains_gap():
    old, _, _ = materials()
    row = next(iter(old["evidence_catalog"].values()))
    row.update(
        services=["payment", "checkout"],
        allowed_target_services=["checkout"],
        covered_services=["checkout"],
        records=[{"service": "payment"}, {"service": "checkout"}],
    )
    v = d.scoped_view(
        old, request_key="partial", target="payment", members=["one", "two"]
    )
    assert any("payment" in g.get("services", []) for g in v["gaps"])
    assert all(
        r["evidence_ref"] != row["evidence_ref"] for r in v["target_evidence"].values()
    )


def test_invalid_schema_still_aggregates_safe_reference_and_unit_errors():
    v, draft = bundle()
    raw = draft.model_dump(mode="json")
    raw["binding_id"] = v["binding_id"]
    raw["candidate"].update(
        member_incidents=["I01", "I02"],
        target_support=["secret prose"],
        target_counterevidence=[],
        comparison_context=[],
    )
    raw["candidate"]["expression"].update(
        dependency_aliases=["unknown"], threshold_unit="KIB"
    )
    issues = d.diagnose_scoped_draft(raw, v)
    assert {
        "UNKNOWN_TARGET_EVIDENCE",
        "UNKNOWN_DEPENDENCY",
        "DEPENDENCY_MEMBER_MISMATCH",
        "UNIT_MISMATCH",
    } <= {i["code"] for i in issues}
    assert "secret prose" not in str(issues)
    raw["candidate"]["member_incidents"] = [{}]
    raw["candidate"]["expression"]["dependency_aliases"] = {}
    assert "STRING_ARRAY_REQUIRED" in {
        i["code"] for i in d.diagnose_scoped_draft(raw, v)
    }


def test_scoped_budget_includes_failures_and_semantic_attempts(tmp_path):
    from test_investigation import repository
    from ecomsre.product.errors import ProductError

    repo = repository(tmp_path)
    for i in range(3):
        key = f"knowledge-draft-v050.2:proposal:{i}"
        repo.reserve(key, {"fixture": i}, 500_000)
        repo.settle(key, {}, None, "FAILED")
    with pytest.raises(ProductError, match="sublimit"):
        repo.reserve("knowledge-draft-v050.2:proposal:3", {}, 1)
    with pytest.raises(ProductError, match="sublimit"):
        repo.reserve("knowledge-draft-v050.2:selection:0", {}, 500_001)
    assert repo.accounting()["committed_upper_microusd"] == 1_500_000


def test_mechanical_scoped_compiler_uses_existing_evaluator_false_unknown():
    # DIAGNOSTIC_ONLY / NOT_MODEL_OUTPUT: no registration, no real-world claims.
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from test_expressions import observation
    from ecomsre.product.knowledge.candidates_v050 import (
        evaluate_candidate,
        candidate_components,
    )

    v, draft = bundle()
    raw = draft.model_dump(mode="json")
    raw["binding_id"] = v["binding_id"]
    raw["candidate"].update(
        member_incidents=list(v["members"]),
        target_support=list(v["target_evidence"]),
        comparison_context=[],
    )
    raw["candidate"]["expression"]["dependency_aliases"] = list(v["dependencies"])
    proposal, _ = d.compile_scoped_draft(d.ScopedKnowledgeDraft.model_validate(raw), v)
    candidate = SimpleNamespace(proposal=proposal)
    end = datetime(2026, 9, 18, tzinfo=UTC)
    obs = observation()
    obs.update(
        targets=["payment"],
        window={
            "started_at": (end - timedelta(seconds=30))
            .isoformat()
            .replace("+00:00", "Z"),
            "ended_at": end.isoformat().replace("+00:00", "Z"),
        },
        resource_dependency=proposal.resource_dependency.model_dump(mode="json"),
    )
    runtime = dict(
        source="RUNTIME",
        status="SUCCESS_NONEMPTY",
        truncated=False,
        covered_services=["payment"],
        evidence_ref="runtime",
    )
    memory = SimpleNamespace(
        predicates=[
            SimpleNamespace(
                service="payment",
                predicate_kind=SimpleNamespace(value="RUNTIME_HEALTHY"),
                evidence_refs=["runtime"],
            )
        ]
    )
    result = evaluate_candidate(
        candidate,
        target="payment",
        memory=memory,
        anomalies=[],
        observations=[obs, runtime],
        incident_end=end,
    )
    assert (
        result.status == "FALSE"
    )  # memory delta zero, model-like fixture threshold42 preserved
    components = candidate_components(
        candidate,
        memory=memory,
        anomalies=[],
        observations=[obs, runtime],
        incident_end=end,
    )
    assert components["expression"]["status"] == "FALSE"
    assert (
        evaluate_candidate(
            candidate,
            target="payment",
            memory=memory,
            anomalies=[],
            observations=[runtime],
            incident_end=end,
        ).status
        == "UNKNOWN"
    )
    assert proposal.expression.threshold == 42


def test_lossless_projection_keeps_complete_resource_samples_and_conflicts():
    import json
    from collections import Counter

    v, _ = bundle()
    model = d.scoped_model_view(v)
    for section in ("target_evidence", "comparison_evidence"):
        for key, row in model[section].items():
            factored = row["records"]
            reconstructed = [
                factored["shared"] | x["value"]
                for x in factored["rows"]
                for _ in range(x["count"])
            ]
            assert Counter(
                map(lambda x: json.dumps(x, sort_keys=True), reconstructed)
            ) == Counter(
                map(lambda x: json.dumps(x, sort_keys=True), v[section][key]["records"])
            )
            assert model["windows"][row["window"]] == v[section][key]["window"]
    assert model["comparison_evidence"]


def test_provider_rejects_namespace_before_charge_and_retains_all_invalid_diagnostics(
    tmp_path,
):
    import json
    from types import SimpleNamespace
    from test_investigation import repository
    from ecomsre.model.gateway import OpenAICompatibleConfig
    from ecomsre.product.investigation.contracts import PriceSchedule
    from ecomsre.product.investigation.provider import StructuredProvider
    from ecomsre.product.errors import ProductError

    repo = repository(tmp_path)
    key = "knowledge-draft-v050.2:proposal:0"
    v, draft = bundle(key)
    raw = draft.model_dump(mode="json")
    raw["binding_id"] = v["binding_id"]
    raw["candidate"].update(
        member_incidents=["I01", "I02"],
        target_support=["bad"],
        target_counterevidence=[],
        comparison_context=[],
    )
    raw["candidate"]["expression"].update(
        dependency_aliases=["bad"], threshold_unit="KIB"
    )
    sends = []

    def post(**kw):
        sends.append(kw["payload"])
        return dict(
            model="gpt-5.4",
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
            usage={"input_tokens": 100, "output_tokens": 100},
        )

    provider = StructuredProvider(
        OpenAICompatibleConfig("https://example.test/v1", "fixture", "gpt-5.4"),
        PriceSchedule(
            provider_profile="fixture",
            model="gpt-5.4",
            as_of="2026-09-17",
            source="fixture",
            input_usd_per_million=0.75,
            output_usd_per_million=4.5,
        ),
        repo,
        SimpleNamespace(post_json=post),
        api_style="responses",
    )
    for bad in ("wrong", "knowledge-draft-v050.2:selection:0"):
        view, _ = bundle(bad)
        with pytest.raises(ValueError, match="NAMESPACE"):
            provider.complete(
                key=bad,
                task=d.SCOPED_TASK,
                view=d.scoped_model_view(view),
                schema=d.ScopedKnowledgeDraft,
                scoped_binding=view,
                max_output_tokens=8192,
            )
    assert repo.accounting()["request_count"] == 0 and not sends
    with pytest.raises(ProductError):
        provider.complete(
            key=key,
            task=d.SCOPED_TASK,
            view=d.scoped_model_view(v),
            schema=d.ScopedKnowledgeDraft,
            scoped_binding=v,
            max_output_tokens=8192,
        )
    with repo.store.connect() as c:
        ledger = json.loads(
            c.execute(
                "SELECT payload_json FROM investigation_provider_calls_v050"
            ).fetchone()[0]
        )
    assert {"UNKNOWN_TARGET_EVIDENCE", "UNKNOWN_DEPENDENCY", "UNIT_MISMATCH"} <= {
        i["code"] for i in ledger["draft_diagnostics"]
    }
    assert ledger["schema_validation_errors"]
    assert repo.accounting()["request_count"] == 1
