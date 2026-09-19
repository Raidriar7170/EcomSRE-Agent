from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.product.app import create_app
from ecomsre.product.errors import ProductError
from ecomsre.product.investigation.contracts import InvestigationDecision, PriceSchedule
from ecomsre.product.investigation.provider import StructuredProvider
from ecomsre.product.investigation.repository import InvestigationRepository
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def repository(tmp_path):
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    return InvestigationRepository(
        store, ContentAddressedObjectStoreV1(tmp_path / "objects", metadata_store=store)
    )


def decision(kind="ABSTAIN", **kwargs):
    return InvestigationDecision.model_validate(
        dict(
            kind=kind,
            action_id=None,
            hypotheses=[],
            rationale="Insufficient distinguishing evidence.",
            result="UNRESOLVED",
            unresolved_alternatives=["Alternative mechanism remains possible."],
        )
        | kwargs
    )


def test_reservation_survives_restart_and_counts_uncertain(tmp_path):
    repo = repository(tmp_path)
    repo.reserve("request-1", {"test": True}, 10_000_000)
    restarted = repository(tmp_path)
    with pytest.raises(ProductError, match="cannot be repeated"):
        restarted.reserve("request-1", {"test": True}, 10_000_000)
    restarted.reserve("request-2", {}, 10_000_000)
    with pytest.raises(ProductError) as error:
        restarted.reserve("request-3", {}, 1)
    assert error.value.code == "BUDGET_EXHAUSTED"
    assert restarted.accounting()["unknown_usage_requests"] == 2


def test_successful_response_is_reused_and_drift_rejected(tmp_path):
    repo = repository(tmp_path)
    repo.reserve("request", {"version": 1}, 100)
    repo.settle("request", {"proposal": {"a": 1}}, 20, "COMPLETED")
    assert repo.reserve("request", {"version": 1}, 100) == {"proposal": {"a": 1}}
    with pytest.raises(ProductError) as error:
        repo.reserve("request", {"version": 2}, 100)
    assert error.value.code == "PROVIDER_REQUEST_DRIFT"
    assert repo.accounting()["request_count"] == 1


@pytest.mark.parametrize(
    "response,code",
    [
        (
            {"model": "gpt-5.4", "choices": [{"finish_reason": "length"}]},
            "PROVIDER_TRUNCATED",
        ),
        (
            {"model": "gpt-5.4", "choices": [{"message": {"refusal": "no"}}]},
            "PROVIDER_REFUSED",
        ),
        ({"model": "gpt-5.4", "choices": []}, "PROVIDER_PROTOCOL_INVALID"),
        ({"model": "different", "choices": []}, "PROVIDER_MODEL_MISMATCH"),
    ],
)
def test_provider_failures_keep_reservations(tmp_path, response, code):
    repo = repository(tmp_path)
    transport = SimpleNamespace(post_json=lambda **kwargs: response)
    provider = StructuredProvider(
        OpenAICompatibleConfig("https://example.test/v1", "not-a-real-key", "gpt-5.4"),
        PriceSchedule(
            provider_profile="fixture",
            model="gpt-5.4",
            as_of="2026-09-17",
            source="fixture only",
            input_usd_per_million=3,
            output_usd_per_million=15,
        ),
        repo,
        transport,
    )
    with pytest.raises(ProductError) as error:
        provider.complete(
            key="one", task="investigate", view={}, schema=InvestigationDecision
        )
    assert error.value.code == code
    assert repo.accounting()["request_count"] == 1
    assert repo.accounting()["unknown_usage_requests"] == 1


def prepare(client, settings, dataset="capture-c2aa", services=("payment",)):
    env = client.post(
        "/v1/environments",
        json={
            "name": "v050-test",
            "description": "Fixture only",
            "timezone": "UTC",
            "service_identity_policy": {"services": [{"logical_service": s} for s in services]},
            "connector_configs": [
                {
                    "name": "fixture",
                    "kind": "FIXTURE",
                    "settings": {"dataset": dataset},
                    "credential_refs": {},
                }
            ],
            "explicit_service_catalog": list(services),
        },
    ).json()["environment_id"]
    client.post(f"/v1/environments/{env}/verify-jobs")
    assert run_one_job(settings, worker_id="fixture")
    client.post(f"/v1/environments/{env}/baseline-jobs", json={"activate": True})
    assert run_one_job(settings, worker_id="fixture")
    app = client.app
    service_ids = sorted(s.service_id for s in app.state.services.get_map(env).services)
    incident = client.post(
        "/v1/incidents",
        json={
            "environment_id": env,
            "external_incident_key": "v050-fixture",
            "alert_name": "alert",
            "summary": "Bounded observation",
            "started_at": datetime.now(UTC).isoformat(),
            "candidate_service_ids": service_ids,
        },
    )
    assert incident.status_code == 201, incident.text
    incident_id = incident.json()["incident_id"]
    job = client.post(f"/v1/incidents/{incident_id}/diagnosis-jobs").json()
    assert run_one_job(settings, worker_id="fixture")
    assert client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "SUCCEEDED"
    return incident_id


class InvestigatingFixture:
    calls = 0

    def complete(self, *, view, **kwargs):
        self.calls += 1
        if self.calls == 1:
            assert view["observations"], "initial telemetry must be provided"
            assert view["capability_limitations"] is not None
            return decision(
                "READ", action_id=view["legal_reads"][0]["action_id"], result=None
            )
        # An observation changes the next decision; fixture evidence is not live.
        return decision(
            "CONCLUDE",
            result="UNRESOLVED",
            rationale="Observed source narrows the question but does not establish mechanism.",
        )


def test_api_worker_roundtrip_and_reentry(tmp_path, monkeypatch):
    settings = ProductSettingsV1(data_root=tmp_path, investigation={"enabled": True})
    provider = InvestigatingFixture()
    monkeypatch.setattr(
        "ecomsre.product.investigation.runtime.configured_provider",
        lambda repository: provider,
    )
    with TestClient(create_app(settings)) as client:
        incident = prepare(client, settings)
        parent = client.get(f"/v1/incidents/{incident}/diagnosis").json()
        job = client.post(f"/v1/incidents/{incident}/investigation-jobs")
        assert job.status_code == 202, job.text
        assert run_one_job(settings, worker_id="investigator")
        state = client.get("/v1/jobs/" + job.json()["job_id"]).json()
        assert state["status"] == "SUCCEEDED", state
        result = client.get(f"/v1/incidents/{incident}/investigation").json()
        assert result["status"] == "UNRESOLVED", result
        assert result["read_count"] == 1
        assert result["provider_calls"] == 2
        assert result["observations"][0]["object_sha256"]
        assert client.get(f"/v1/incidents/{incident}/diagnosis").json() == parent
        assert (
            client.post(f"/v1/incidents/{incident}/investigation-jobs").json()["job_id"]
            == job.json()["job_id"]
        )
        assert not run_one_job(settings, worker_id="duplicate")
        assert provider.calls == 2
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/v1/incidents/{incident}/investigation").json() == result


def test_disabled_route_and_known_zero_calls(tmp_path, monkeypatch):
    settings = ProductSettingsV1(data_root=tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/incidents/absent/investigation-jobs").status_code == 409
    settings = ProductSettingsV1(data_root=tmp_path, investigation={"enabled": True})
    monkeypatch.setattr(
        "ecomsre.product.investigation.runtime.configured_provider",
        lambda repository: pytest.fail("known path called model"),
    )
    with TestClient(create_app(settings)) as client:
        incident = prepare(client, settings, "capture-7f31")
        job = client.post(f"/v1/incidents/{incident}/investigation-jobs").json()
        assert run_one_job(settings, worker_id="known")
        assert client.get("/v1/jobs/" + job["job_id"]).json()["status"] == "SUCCEEDED"
        result = client.get(f"/v1/incidents/{incident}/investigation").json()
        assert result["provider_calls"] == 0
        assert result["status"] == "NOT_REQUIRED"


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"covered_services": []}, "EVIDENCE_INCOMPLETE"),
        ({"status": "SUCCESS_EMPTY", "records": []}, "EVIDENCE_INCOMPLETE"),
        ({"truncated": True}, "EVIDENCE_INCOMPLETE"),
        ({"targets": ["ad"]}, "EVIDENCE_TARGET_MISMATCH"),
        (
            {
                "window": {
                    "started_at": "2026-09-17T10:01:00Z",
                    "ended_at": "2026-09-17T10:01:30Z",
                }
            },
            "EVIDENCE_WINDOW_MISMATCH",
        ),
    ],
)
def test_hypothesis_requires_actual_coverage_and_exact_window(mutation, error):
    from ecomsre.product.investigation.contracts import HypothesisProposal
    from ecomsre.product.investigation.runtime import validate_hypothesis_evidence

    window = {"started_at": "2026-09-17T10:00:00Z", "ended_at": "2026-09-17T10:00:30Z"}
    h = HypothesisProposal(
        hypothesis_id=None,
        mechanism="Transient resource spike",
        target="payment",
        support=["ref"],
        against=[],
        missing_observations=[],
        falsifiable_prediction="CPU falls in next window",
        claim_window=window,
    )
    observation = {
        "targets": ["payment"],
        "covered_services": ["payment"],
        "status": "SUCCESS_NONEMPTY",
        "truncated": False,
        "records": [{"service": "payment", "value": 90.0}],
        "window": window,
    }
    validate_hypothesis_evidence(h, {"ref": observation})
    with pytest.raises(ValueError, match=error):
        validate_hypothesis_evidence(h, {"ref": observation | mutation})


def test_expired_job_cannot_reserve_provider_or_commit_session(tmp_path):
    from ecomsre.product.jobs.repository import JobRepositoryV1
    from ecomsre.product.jobs.contracts import JobLeaseFenceV1
    from ecomsre.product.jobs.contracts_v050 import InvestigationJobTypeV050

    repo = repository(tmp_path)
    jobs = JobRepositoryV1(repo.store)
    job = jobs.enqueue(InvestigationJobTypeV050.INVESTIGATION, {}, now=1)
    claimed = jobs.claim_next("worker", lease_seconds=1, now=2)
    fence = JobLeaseFenceV1(
        job_id=job.job_id,
        claimed_by="worker",
        attempt_count=claimed.attempt_count,
        checked_at=4,
    )
    with pytest.raises(ProductError) as error:
        repo.reserve("expired", {}, 100, fence=fence)
    assert error.value.code == "JOB_LEASE_LOST"
    assert repo.accounting()["request_count"] == 0


def test_unknown_cost_bound_failure_blocks_future_dispatch(tmp_path):
    repo = repository(tmp_path)
    repo.reserve("first", {}, 100)
    repo.settle(
        "first",
        {"error_code": "PROVIDER_USAGE_BOUND_EXCEEDED"},
        101,
        "UNSAFE_COST_BOUND",
    )
    with pytest.raises(ProductError) as error:
        repo.reserve("second", {}, 100)
    assert error.value.code == "PROVIDER_COST_BOUND_INVALID"


def test_invalid_request_cap_counts_every_reserved_request(tmp_path):
    repo = repository(tmp_path)
    for i in range(200):
        repo.reserve(str(i), {}, 1)
    with pytest.raises(ProductError) as error:
        repo.reserve("one-too-many", {}, 1)
    assert error.value.code == "BUDGET_EXHAUSTED"
    assert repo.accounting()["request_count"] == 200


def test_prediction_check_is_runtime_computed_and_window_bound():
    from ecomsre.product.investigation.runtime import check_predictions
    from test_expressions import observation

    window = {"started_at": "2026-09-17T10:00:00Z", "ended_at": "2026-09-17T10:00:10Z"}
    hypothesis = {
        "hypothesis_id": "runtime-id",
        "target": "payment",
        "claim_window": window,
        "prediction_test": {
            "field": "cpu_percent",
            "operator": "max",
            "comparator": "gt",
            "threshold": 80.0,
            "window_seconds": 10,
            "minimum_samples": 3,
        },
    }
    obs = observation() | {"window": window}
    assert check_predictions([hypothesis], [obs])[0]["status"] == "TRUE"
    assert (
        check_predictions(
            [hypothesis], [observation((5.0, 5.0, 5.0)) | {"window": window}]
        )[0]["status"]
        == "FALSE"
    )
    assert (
        check_predictions([hypothesis], [obs | {"window": {}}])[0]["status"]
        == "UNKNOWN"
    )
    assert (
        check_predictions([hypothesis | {"prediction_test": None}], [obs])[0]["status"]
        == "NOT_CHECKED"
    )


@pytest.mark.parametrize(
    "response",
    [
        {"model": "gpt-5.4", "choices": [None]},
        {"model": "gpt-5.4", "choices": [{"message": None}]},
        {"model": "gpt-5.4", "choices": [{"message": {"tool_calls": [None]}}]},
    ],
)
def test_malformed_nested_provider_response_is_typed_failure(tmp_path, response):
    repo = repository(tmp_path)
    provider = StructuredProvider(
        OpenAICompatibleConfig("https://example.test/v1", "fixture", "gpt-5.4"),
        PriceSchedule(
            provider_profile="fixture",
            model="gpt-5.4",
            as_of="2026-09-17",
            source="fixture",
            input_usd_per_million=3,
            output_usd_per_million=15,
        ),
        repo,
        SimpleNamespace(post_json=lambda **kw: response),
    )
    with pytest.raises(ProductError) as error:
        provider.complete(
            key="bad-envelope",
            task="investigate",
            view={},
            schema=InvestigationDecision,
        )
    assert error.value.code == "PROVIDER_PROTOCOL_INVALID"
    assert repo.accounting()["unknown_usage_requests"] == 1


def test_provider_does_not_persist_gateway_hidden_reasoning(tmp_path):
    import json

    repo = repository(tmp_path)
    response = {
        "model": "gpt-5.4",
        "id": "fixture-id",
        "reasoning": "PRIVATE_REASONING_SENTINEL",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "reasoning_content": "PRIVATE_REASONING_SENTINEL",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_proposal",
                                "arguments": decision().model_dump_json(),
                            }
                        }
                    ],
                },
            }
        ],
    }
    provider = StructuredProvider(
        OpenAICompatibleConfig("https://example.test/v1", "fixture", "gpt-5.4"),
        PriceSchedule(
            provider_profile="fixture",
            model="gpt-5.4",
            as_of="2026-09-17",
            source="fixture",
            input_usd_per_million=3,
            output_usd_per_million=15,
        ),
        repo,
        SimpleNamespace(post_json=lambda **kw: response),
    )
    provider.complete(
        key="privacy", task="investigate", view={}, schema=InvestigationDecision
    )
    with repo.store.connect() as connection:
        payload = connection.execute(
            "SELECT payload_json FROM investigation_provider_calls_v050"
        ).fetchone()[0]
    assert "PRIVATE_REASONING_SENTINEL" not in payload
    ledger = json.loads(payload)
    audit = repo.objects.read_bytes(ledger["response_audit_object_sha256"])
    assert b"PRIVATE_REASONING_SENTINEL" not in audit
    assert b"reasoning_content" not in audit
    assert ledger["proposal"] == decision().model_dump(mode="json")


def test_unknown_model_suffix_invalidates_campaign_cost_bound(tmp_path):
    repo = repository(tmp_path)
    transport = SimpleNamespace(
        post_json=lambda **kw: {"model": "gpt-5.4-other-family", "choices": []}
    )
    provider = StructuredProvider(
        OpenAICompatibleConfig("https://example.test/v1", "fixture", "gpt-5.4"),
        PriceSchedule(
            provider_profile="fixture",
            model="gpt-5.4",
            as_of="2026-09-17",
            source="fixture",
            input_usd_per_million=3,
            output_usd_per_million=15,
        ),
        repo,
        transport,
    )
    with pytest.raises(ProductError) as first:
        provider.complete(
            key="wrong-model", task="investigate", view={}, schema=InvestigationDecision
        )
    assert first.value.code == "PROVIDER_MODEL_MISMATCH"
    with pytest.raises(ProductError) as second:
        provider.complete(
            key="later", task="investigate", view={}, schema=InvestigationDecision
        )
    assert second.value.code == "PROVIDER_COST_BOUND_INVALID"
    assert repo.accounting()["request_count"] == 1
    assert repo.accounting()["unknown_usage_requests"] == 1


def test_responses_adapter_preserves_model_usage_and_discards_reasoning(tmp_path):
    repo=repository(tmp_path)
    def post_json(**kwargs):
        assert kwargs['url']=='https://example.test/v1/responses'
        assert kwargs['payload']['store'] is False
        assert kwargs['payload']['model']=='gpt-5.4'
        assert kwargs['payload']['service_tier']=='default'
        return {'id':'fixture-response','model':'gpt-5.4','status':'completed',
                'usage':{'input_tokens':100,'output_tokens':20},
                'output':[{'type':'reasoning','text':'HIDDEN_SENTINEL'},
                          {'type':'function_call','status':'completed','name':'submit_proposal','arguments':decision().model_dump_json()}]}
    provider=StructuredProvider(OpenAICompatibleConfig('https://example.test/v1','fixture','gpt-5.4'),
        PriceSchedule(provider_profile='fixture',model='gpt-5.4',as_of='2026-09-17',source='fixture',
                      input_usd_per_million=3,output_usd_per_million=15),repo,
        SimpleNamespace(post_json=post_json),api_style='responses')
    assert provider.complete(key='responses-fixture',task='investigate',view={},schema=InvestigationDecision)==decision()
    with repo.store.connect() as c:
        raw=c.execute('SELECT payload_json FROM investigation_provider_calls_v050').fetchone()[0]
    assert 'HIDDEN_SENTINEL' not in raw
    assert repo.accounting()['known_cost_microusd']==600


@pytest.mark.parametrize('status,error,call_status',[('failed',{'code':'server_error'},'in_progress'),
    ('in_progress',None,'in_progress'),('cancelled',None,'completed'),('completed',None,'in_progress')])
def test_responses_incomplete_function_is_not_committed(tmp_path,status,error,call_status):
    repo=repository(tmp_path)
    response={'id':'incomplete','model':'gpt-5.4','status':status,'error':error,
              'usage':{'input_tokens':100,'output_tokens':20},
              'output':[{'type':'function_call','status':call_status,'name':'submit_proposal',
                         'arguments':decision().model_dump_json()}]}
    provider=StructuredProvider(OpenAICompatibleConfig('https://example.test/v1','fixture','gpt-5.4'),
        PriceSchedule(provider_profile='fixture',model='gpt-5.4',as_of='2026-09-17',source='fixture',
                      input_usd_per_million=3,output_usd_per_million=15),repo,
        SimpleNamespace(post_json=lambda **kwargs:response),api_style='responses')
    with pytest.raises(ProductError) as caught:
        provider.complete(key='partial',task='investigate',view={},schema=InvestigationDecision)
    assert caught.value.code=='PROVIDER_RESPONSE_NOT_COMPLETED'
    assert repo.accounting()['known_cost_microusd']==600
