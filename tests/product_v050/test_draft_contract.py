import pytest
from ecomsre.product.knowledge.drafts_v050 import (
    KnowledgeDraft,
    draft_view,
    compile_draft,
    strict_schema,
)


def materials():
    sessions = []
    for event in ("one", "two"):
        dep = dict(
            template="RESOURCE_USAGE_SAMPLES",
            window_offset_seconds=0,
            query_window_seconds=30,
            sampling_window_seconds=10,
            sample_count=3,
        )
        observations = [
            dict(
                evidence_ref=f"{event}-{service}",
                source="RESOURCES",
                status="SUCCESS_NONEMPTY",
                truncated=False,
                covered_services=[service],
                window={"end": event},
                records=[{"service": service}],
            )
            for service in ("payment", "checkout")
        ]
        sessions.append(
            dict(
                incident_id=event,
                observations=observations,
                status="UNRESOLVED",
                hypotheses=[],
                decisions=[],
                deployable_resource_dependencies=[
                    dict(
                        target="payment",
                        dependency=dep,
                        availability="BOUND_OBSERVATION",
                        supporting_refs=[event + "-payment"],
                    )
                ],
            )
        )
    view = draft_view(
        dict(
            snapshot_sha256="a" * 64,
            sessions=sessions,
            predicate_catalog=["core:RUNTIME_HEALTHY"],
            feature_catalog={},
        )
    )
    support = [
        a for a, r in view["evidence_catalog"].items() if r["services"] == ["payment"]
    ]
    comparison = next(
        a for a, r in view["evidence_catalog"].items() if r["services"] == ["checkout"]
    )
    draft = KnowledgeDraft(
        disposition="CANDIDATE",
        reason="fixture only",
        candidate=dict(
            name="fixture-pattern",
            target="payment",
            broad_domain="RESOURCE",
            member_incidents=["one", "two"],
            predicates=["core:RUNTIME_HEALTHY"],
            expression=dict(
                numerator=dict(field="memory_bytes", operator="delta"),
                denominator=None,
                comparator="gt",
                threshold=42,
                threshold_unit="BYTES",
                dependency_aliases=list(view["dependency_catalog"]),
            ),
            target_support=support,
            target_counterevidence=[],
            comparison_context=[
                dict(
                    evidence_alias=comparison,
                    service="checkout",
                    inference="comparison only",
                )
            ],
            confusable_patterns=["other load"],
            prediction="bounded pattern",
            inapplicable_conditions=["missing samples"],
        ),
    )
    return view, draft, comparison


def test_context_is_audited_but_cannot_change_canonical_matcher():
    view, draft, _ = materials()
    proposal, context = compile_draft(draft, view)
    without = draft.model_copy(deep=True)
    without.candidate.comparison_context.clear()
    assert compile_draft(without, view)[0] == proposal
    assert context[0]["authority"] == "AUDIT_ONLY_MODEL_INFERENCE"
    assert proposal.expression.threshold == 42
    assert proposal.expression.window_seconds == 10
    assert proposal.required_sources == ("RESOURCES", "RUNTIME")


def test_wrong_target_cannot_be_counterevidence_or_support():
    for field in ("target_support", "target_counterevidence"):
        view, draft, other = materials()
        getattr(draft.candidate, field)[:] = [other]
        with pytest.raises(ValueError, match="TARGET_EVIDENCE_ROLE_MISMATCH"):
            compile_draft(draft, view)


def test_context_does_not_replace_missing_support():
    view, draft, _ = materials()
    draft.candidate.target_support.clear()
    with pytest.raises(ValueError, match="DEPENDENCY_NOT_SUPPORTED"):
        compile_draft(draft, view)


def test_aliases_cannot_cross_snapshots():
    view, draft, _ = materials()
    view["snapshot_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="SNAPSHOT_MISMATCH"):
        compile_draft(draft, view)


def test_unit_and_dependency_window_mismatch_reject():
    view, draft, _ = materials()
    draft = draft.model_copy(
        update={
            "candidate": draft.candidate.model_copy(
                update={
                    "expression": draft.candidate.expression.model_copy(
                        update={"threshold_unit": "PERCENT"}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError, match="unit differs"):
        compile_draft(draft, view)
    view, draft, _ = materials()
    list(view["dependency_catalog"].values())[1]["dependency"][
        "sampling_window_seconds"
    ] = 5
    with pytest.raises(ValueError, match="DEPENDENCY_SEMANTICS_MISMATCH"):
        compile_draft(draft, view)


def test_multiservice_ref_requires_actual_record_for_service():
    view, draft, other = materials()
    view["evidence_catalog"][other]["covered_services"].append("payment")
    draft.candidate.target_counterevidence[:] = [other]
    with pytest.raises(ValueError, match="TARGET_EVIDENCE_ROLE_MISMATCH"):
        compile_draft(draft, view)


def test_strict_schema_objects_all_required_and_no_extras():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(strict_schema(KnowledgeDraft))


def test_draft_provenance_reconstructs_live_bound_response(tmp_path, monkeypatch):
    # Fixture ledger only; no actual model or learning claim.
    import json
    from datetime import UTC, datetime
    from fastapi.testclient import TestClient
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.jobs.worker import run_one_job
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.knowledge.evolution_v050 import KnowledgeEvolutionV050
    from ecomsre.product.knowledge.drafts_v050 import TASK, PROTOCOL
    from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
    from test_investigation import prepare, InvestigatingFixture

    settings = ProductSettingsV1(data_root=tmp_path, investigation={"enabled": True})
    monkeypatch.setattr(
        "ecomsre.product.investigation.runtime.configured_provider",
        lambda repository: InvestigatingFixture(),
    )
    with TestClient(create_app(settings)) as client:
        first = prepare(client, settings)
        app = client.app
        incident = app.state.incidents.get(first)

        def new_event(key):
            raw = incident.model_dump(
                mode="json",
                include={
                    "environment_id",
                    "alert_name",
                    "summary",
                    "candidate_service_ids",
                },
            )
            raw.update(
                external_incident_key=key, started_at=datetime.now(UTC).isoformat()
            )
            created = client.post("/v1/incidents", json=raw).json()["incident_id"]
            job = client.post(f"/v1/incidents/{created}/diagnosis-jobs").json()
            assert run_one_job(settings, worker_id=key)
            assert (
                client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "SUCCEEDED"
            )
            return created

        second = new_event("second-discovery")
        for item in [first, second]:
            job = client.post(f"/v1/incidents/{item}/investigation-jobs").json()
            assert run_one_job(settings, worker_id="fixture-investigate")
            assert (
                client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "SUCCEEDED"
            )
        evolution = KnowledgeEvolutionV050(
            app.state.knowledge,
            InvestigationRepository(app.state.store, app.state.object_store),
        )

        evolution.freeze_split(
            incident.environment_id, {"a": "DISCOVERY", "b": "DISCOVERY"}
        )
        evolution.bind_episode(first, "a")
        evolution.bind_episode(second, "b")
        discovery = evolution.discovery_view(incident.environment_id, [first, second])
        view = draft_view(discovery)
        support = [
            a
            for a, r in view["evidence_catalog"].items()
            if "payment" in r["allowed_target_services"]
        ]
        draft = KnowledgeDraft(
            disposition="CANDIDATE",
            reason="fixture only",
            candidate=dict(
                name="fixture-draft",
                target="payment",
                broad_domain="RESOURCE",
                member_incidents=[first, second],
                predicates=["core:RUNTIME_HEALTHY", "ga:RESOURCE_CPU_OUTLIER"],
                expression=None,
                target_support=support[:24],
                target_counterevidence=[],
                comparison_context=[],
                confusable_patterns=["other"],
                prediction="bounded",
                inapplicable_conditions=["missing"],
            ),
        )
        proposal, _ = compile_draft(draft, view)
        valid = dict(
            proposal=draft.model_dump(mode="json"),
            evidence_mode="LIVE_PROVIDER",
            prompt_version=PROTOCOL,
            task_view_sha256=sha({"task": TASK, "view": view}),
        )
        for i, mutation in enumerate(
            ("raw", "canonical", "mapping", "protocol", "mode", "view")
        ):
            payload = json.loads(json.dumps(valid))
            candidate = proposal
            binding = json.loads(json.dumps(view))
            if mutation == "raw":
                payload["proposal"]["candidate"]["predicates"] = [
                    "core:RUNTIME_NOT_RUNNING"
                ]
            if mutation == "canonical":
                candidate = proposal.model_copy(update={"name": "changed"})
            if mutation == "mapping":
                next(iter(binding["evidence_catalog"].values()))["source"] = "CHANGES"
            if mutation == "protocol":
                payload["prompt_version"] = "other"
            if mutation == "mode":
                payload["evidence_mode"] = "FIXTURE_ONLY"
            if mutation == "view":
                payload["task_view_sha256"] = "0" * 64
            key = "fixture-draft-" + str(i)
            evolution.investigations.reserve(key, {"fixture": i}, 1)
            evolution.investigations.settle(key, payload, 1, "COMPLETED")
            with pytest.raises(ValueError):
                evolution.add_candidate(
                    environment_id=incident.environment_id,
                    proposal=candidate,
                    origin="LLM",
                    source_request_key=key,
                    discovery=discovery,
                    draft_view_binding=binding,
                )
        key = "fixture-draft-good"
        evolution.investigations.reserve(key, {"fixture": "valid"}, 1)
        evolution.investigations.settle(key, valid, 1, "COMPLETED")
        accepted = evolution.add_candidate(
            environment_id=incident.environment_id,
            proposal=proposal,
            origin="LLM",
            source_request_key=key,
            discovery=discovery,
            draft_view_binding=view,
        )
        assert accepted.proposal == proposal
        with evolution.store.connect() as c:
            assert (
                c.execute(
                    "select count(*) from knowledge_draft_provenance_v050"
                ).fetchone()[0]
                == 1
            )


def test_shared_continuation_budget_counts_failures_and_reservations(tmp_path):
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.investigation.repository import InvestigationRepository
    from ecomsre.product.errors import ProductError

    app = create_app(ProductSettingsV1(data_root=tmp_path))
    repo = InvestigationRepository(app.state.store, app.state.object_store)
    for i in range(6):
        repo.reserve(f"knowledge-draft-v050.1:{i}", {"fixture": i}, 100)
        repo.settle(
            f"knowledge-draft-v050.1:{i}",
            {"error_code": "FIXTURE_FAILURE"},
            None,
            "FAILED",
        )
    with pytest.raises(ProductError, match="sublimit"):
        repo.reserve("knowledge-draft-v050.1:seventh", {}, 100)


def test_safe_diagnostics_never_retain_unknown_prose():
    import json
    from ecomsre.product.knowledge.drafts_v050 import safe_parameters

    view, _, _ = materials()
    diagnostic = safe_parameters(
        json.dumps(
            {
                "candidate": {
                    "expression": {"threshold": 12, "threshold_unit": "SECRET"},
                    "name": "PRIVATE",
                }
            }
        ),
        view,
    )
    assert "SECRET" not in json.dumps(diagnostic)
    assert "PRIVATE" not in json.dumps(diagnostic)
    assert any(r.get("value") == 12 for r in diagnostic["fields"])


def test_replay_attempt_guard_global_success_and_semantic_cap(tmp_path):
    import json
    from scripts.product_v050.knowledge_contract_repair import require_next_attempt
    from ecomsre.product.app import create_app
    from ecomsre.product.settings import ProductSettingsV1
    from ecomsre.product.investigation.repository import InvestigationRepository

    app = create_app(ProductSettingsV1(data_root=tmp_path / "data"))
    repo = InvestigationRepository(app.state.store, app.state.object_store)
    require_next_attempt(repo, 0, 0, tmp_path)
    with pytest.raises(ValueError, match="SEMANTIC_ANCHOR"):
        require_next_attempt(repo, 0, 1, tmp_path)
    with pytest.raises(ValueError, match="MONOTONIC"):
        require_next_attempt(repo, 1, 0, tmp_path)
    result = tmp_path / "revision-1-repair-0.json"
    result.write_text(json.dumps({"independent_validation_eligible": True}))
    with pytest.raises(ValueError, match="NO_FURTHER_SAMPLING"):
        require_next_attempt(repo, 0, 0, tmp_path)
    result.unlink()
    for i in range(3):
        repo.reserve(f"knowledge-draft-v050.1:{i}", {"fixture": i}, 1)
        repo.settle(f"knowledge-draft-v050.1:{i}", {}, None, "FAILED")
    with pytest.raises(ValueError, match="EXHAUSTED"):
        require_next_attempt(repo, 2, 0, tmp_path)


def test_published_replay_failures_cannot_be_upgraded():
    import json
    from copy import deepcopy
    from pathlib import Path
    from scripts.ci.verify_product_v050_docker_stability import verify_repair_claims

    root = (
        Path(__file__).resolve().parents[2]
        / "docs/results/product-v050/knowledge-contract-repair"
    )
    report = json.loads((root / "result.json").read_text())
    calls = json.loads((root / "calls.json").read_text())
    assert verify_repair_claims(report, calls)["development_evaluations"] == 0
    for field in (
        "accepted_candidates",
        "development_evaluations",
        "new_product_recovery_writes",
        "independent_new_episode_count",
    ):
        bad = deepcopy(report)
        bad[field] = 1
        with pytest.raises(ValueError):
            verify_repair_claims(bad, calls)
    bad = deepcopy(calls)
    bad[-1]["format_only_repair_claimed"] = True
    with pytest.raises(ValueError):
        verify_repair_claims(report, bad)


def test_repair_verifier_rejects_false_levels_or_model():
    import json
    from copy import deepcopy
    from pathlib import Path
    from scripts.ci.verify_product_v050_docker_stability import verify_repair_claims

    root = (
        Path(__file__).resolve().parents[2]
        / "docs/results/product-v050/knowledge-contract-repair"
    )
    report = json.loads((root / "result.json").read_text())
    calls = json.loads((root / "calls.json").read_text())
    for key in ("level_a_validated", "level_b_validated"):
        bad = deepcopy(report)
        bad[key] = True
        with pytest.raises(ValueError, match="FALSE_LEVEL"):
            verify_repair_claims(bad, calls)
    bad = deepcopy(calls)
    bad[0]["actual_model"] = "different-model"
    with pytest.raises(ValueError, match="PROVIDER"):
        verify_repair_claims(report, bad)
    for call in calls:
        draft = call["draft_projection"]
        if draft:
            assert draft["reason"] == "PRIVATE_MODEL_TEXT_WITHHELD"
            for ref in (
                draft["candidate"]["target_support"]
                + draft["candidate"]["target_counterevidence"]
            ):
                assert (
                    ref in call["admission_replay_view"]["evidence_catalog"]
                    or ref == "UNKNOWN_ALIAS_PRIVATE_TEXT_WITHHELD"
                )
