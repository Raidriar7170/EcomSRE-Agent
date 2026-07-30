import base64
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    canonical_json_sha256,
    sha256_bytes,
    sha256_file,
)
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.telemetry.prometheus import (
    _load_test_query_registry,
    FixtureState,
    load_query_registry,
    validate_frozen_query_registry,
)
from telemetry_promotion_support import issue_strict_frozen_test_capability


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "config" / "phase0" / "telemetry-queries-v3.0.0.json"
FROZEN_FIXTURE = (
    ROOT / "tests" / "fixtures" / "telemetry" / "frozen-query-registry.json"
)


def test_repository_registry_is_explicitly_unresolved_and_cannot_gate_readiness() -> (
    None
):
    loaded = load_query_registry(REGISTRY)

    assert loaded.registry.state is FixtureState.UNRESOLVED
    assert loaded.registry.prometheus.state in {
        FixtureState.UNRESOLVED,
        FixtureState.CANDIDATE,
    }
    assert loaded.registry.jaeger.state in {
        FixtureState.UNRESOLVED,
        FixtureState.CANDIDATE,
    }
    assert loaded.registry.opensearch.state in {
        FixtureState.UNRESOLVED,
        FixtureState.CANDIDATE,
    }
    assert loaded.registry.probe.state is FixtureState.UNRESOLVED
    assert loaded.content_sha256 == sha256_file(REGISTRY)
    with pytest.raises(ValueError, match="QUERY_FIXTURE_NOT_FROZEN"):
        loaded.require_frozen()


def test_every_backend_declares_the_complete_discovery_contract() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))

    for name in ("prometheus", "jaeger", "opensearch"):
        backend = payload[name]
        assert {
            "state",
            "target",
            "request_template",
            "expected_response_schema",
            "upstream_tag",
            "upstream_commit",
            "applicable_service",
            "failure_semantics",
            "freshness_semantics",
            "source_facts",
        } <= set(backend)
    assert {
        "counter_identity_labels",
        "error_classification",
        "target_incarnation_query",
        "expected_target_incarnation_series",
        "scrape_interval_seconds",
        "scrape_interval_tolerance_seconds",
        "maximum_scrape_lag_seconds",
        "boundary_rule",
        "cardinality_rule",
        "reset_policy",
        "staleness_policy",
        "zero_series_rule",
    } <= set(payload["prometheus"])
    assert {
        "method",
        "path",
        "input",
        "response_contract",
        "exit_semantics",
        "attribution_mechanism",
        "getads_proof_artifact",
        "hidden_input_denial_required",
        "required_phases",
    } <= set(payload["probe"])


def test_repository_candidate_source_facts_are_hash_bound_to_pinned_files() -> None:
    loaded = load_query_registry(REGISTRY)
    facts = [
        *loaded.registry.source_facts,
        *loaded.registry.prometheus.source_facts,
        *loaded.registry.jaeger.source_facts,
        *loaded.registry.opensearch.source_facts,
        *loaded.registry.probe.source_facts,
    ]

    for fact in facts:
        source = ROOT / fact.path
        assert source.is_file()
        assert sha256_file(source) == fact.sha256


def test_frozen_fixture_requires_complete_promotion_evidence() -> None:
    payload = json.loads(FROZEN_FIXTURE.read_text(encoding="utf-8"))
    payload["promotion_proof"] = None

    with pytest.raises(ValidationError, match="promotion"):
        load_query_registry(payload)


def test_frozen_fixture_binds_probe_attribution_to_promotion_proof() -> None:
    payload = json.loads(FROZEN_FIXTURE.read_text(encoding="utf-8"))
    payload["probe"]["getads_proof_artifact"] = (
        "observer-visible/99999999999999999999999999999999/"
        "telemetry/promotion/different-attribution.json"
    )

    with pytest.raises(ValidationError, match="attribution"):
        load_query_registry(payload)


def test_app_compatibility_fallback_is_rejected_at_any_fixture_state() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload["prometheus"]["candidate_metric"] = "app.ads_total"

    with pytest.raises(ValidationError, match=r"app\.\*"):
        load_query_registry(payload)


def test_production_loader_cannot_authorize_synthetic_frozen_registry() -> None:
    loaded = load_query_registry(FROZEN_FIXTURE)

    assert loaded.registry.state is FixtureState.FROZEN
    with pytest.raises(ValueError, match="QUERY_FIXTURE_NOT_FROZEN"):
        loaded.require_frozen()

    synthetic = _load_test_query_registry(FROZEN_FIXTURE)
    assert synthetic.require_frozen() is synthetic.registry
    assert synthetic.synthetic_test_only
    assert loaded.registry.promotion_proof is not None
    assert loaded.registry.promotion_proof.review_decision == "APPROVED"
    assert len(loaded.registry.promotion_proof.fixture_content_sha256) == 64


def test_runtime_frozen_capability_requires_hashed_same_run_observer_evidence(
    tmp_path: Path,
) -> None:
    run_id = "8" * 32
    with ObserverEvidenceStore(tmp_path, run_id) as store:
        payload, test_capability = issue_strict_frozen_test_capability(
            store,
            run_id=run_id,
            fixture_path=FROZEN_FIXTURE,
        )

        audit = validate_frozen_query_registry(payload, store)
        assert audit.valid
        assert audit.run_id == run_id
        assert test_capability.synthetic_test_only
        assert not hasattr(test_capability, "is_authentic")

        missing_payload = json.loads(json.dumps(payload))
        prefix = f"observer-visible/{run_id}/"
        missing_path = missing_payload["promotion_proof"]["review_artifact"]
        missing_payload["promotion_proof"]["review_artifact"] = (
            prefix + "telemetry/promotion/missing-review.json"
        )
        missing_payload["promotion_proof"]["artifact_sha256"][
            prefix + "telemetry/promotion/missing-review.json"
        ] = missing_payload["promotion_proof"]["artifact_sha256"].pop(missing_path)
        assert not validate_frozen_query_registry(missing_payload, store).valid

        weak_payload = json.loads(json.dumps(payload))
        weak_payload["promotion_proof"]["upstream_sha256"] = "0" * 64
        weak_audit = validate_frozen_query_registry(weak_payload, store)
        assert not weak_audit.valid
        assert weak_audit.reason is not None
        assert "upstream" in weak_audit.reason


@pytest.mark.parametrize(
    "mutator",
    [
        lambda artifacts: _raw_promotion(artifacts)["backend_observations"][0].update(
            {"request": "up"}
        ),
        lambda artifacts: _mutate_embedded_identity(
            _raw_promotion(artifacts)["backend_observations"][0],
            backend="prometheus",
        ),
        lambda artifacts: _mutate_embedded_identity(
            _raw_promotion(artifacts)["backend_observations"][3],
            backend="jaeger",
        ),
        lambda artifacts: _identity_promotion(artifacts).update(
            {"unexpected": "self-attested"}
        ),
        lambda artifacts: _raw_promotion(artifacts)["probe_phase_observations"].pop(),
        lambda artifacts: _replay_old_prometheus_samples(artifacts),
        lambda artifacts: _overlap_all_promotion_phases(artifacts),
        lambda artifacts: _break_baseline_trace_correlation(artifacts),
    ],
    ids=(
        "wrong-query",
        "wrong-prometheus-identity",
        "wrong-jaeger-identity",
        "unknown-schema-field",
        "missing-recovery-proof",
        "old-prometheus-replay",
        "overlapping-phase-windows",
        "unrelated-jaeger-trace",
    ),
)
def test_runtime_frozen_capability_rejects_semantically_forged_hashed_artifacts(
    tmp_path: Path,
    mutator,
) -> None:
    run_id = "7" * 32
    with ObserverEvidenceStore(tmp_path, run_id) as store:
        with pytest.raises(ValueError):
            issue_strict_frozen_test_capability(
                store,
                run_id=run_id,
                fixture_path=FROZEN_FIXTURE,
                artifact_mutator=mutator,
            )


def _raw_promotion(
    artifacts: dict[str, dict[str, object]],
) -> dict[str, object]:
    return next(
        payload
        for payload in artifacts.values()
        if payload["schema_version"] == "phase0.telemetry-promotion-raw.v1"
    )


def _identity_promotion(
    artifacts: dict[str, dict[str, object]],
) -> dict[str, object]:
    return next(
        payload
        for payload in artifacts.values()
        if payload["schema_version"] == "phase0.telemetry-emitted-identities.v1"
    )


def _mutate_embedded_identity(
    observation: dict[str, object],
    *,
    backend: str,
) -> None:
    body = json.loads(base64.b64decode(observation["raw_response_base64"]))
    if backend == "prometheus":
        body["data"]["result"][0]["metric"]["service_name"] = "frontend"
    else:
        body["data"][0]["processes"]["p1"]["serviceName"] = "frontend"
    encoded = canonical_json_bytes(body)
    observation["raw_response_base64"] = base64.b64encode(encoded).decode("ascii")
    observation["raw_response_sha256"] = sha256_bytes(encoded)


def _replay_old_prometheus_samples(
    artifacts: dict[str, dict[str, object]],
) -> None:
    observations = _raw_promotion(artifacts)["backend_observations"]
    for observation in observations[:3]:
        body = json.loads(base64.b64decode(observation["raw_response_base64"]))
        for result in body["data"]["result"]:
            result["value"][0] = 1.0
        encoded = canonical_json_bytes(body)
        observation["raw_response_base64"] = base64.b64encode(encoded).decode("ascii")
        observation["raw_response_sha256"] = sha256_bytes(encoded)


def _overlap_all_promotion_phases(
    artifacts: dict[str, dict[str, object]],
) -> None:
    phases = _raw_promotion(artifacts)["probe_phase_observations"]
    baseline = phases[0]
    for phase in phases[1:]:
        phase.update(
            {
                "phase_started_at": baseline["phase_started_at"],
                "phase_ended_at": baseline["phase_ended_at"],
                "phase_monotonic_started_at": baseline["phase_monotonic_started_at"],
                "phase_monotonic_ended_at": baseline["phase_monotonic_ended_at"],
                "request_started_at": baseline["request_started_at"],
                "response_ended_at": baseline["response_ended_at"],
                "monotonic_started_at": baseline["monotonic_started_at"],
                "monotonic_ended_at": baseline["monotonic_ended_at"],
            }
        )


def _break_baseline_trace_correlation(
    artifacts: dict[str, dict[str, object]],
) -> None:
    raw = _raw_promotion(artifacts)
    phase = raw["probe_phase_observations"][0]
    exchange = next(
        payload
        for payload in artifacts.values()
        if payload.get("purpose") == "jaeger-correlation-baseline"
    )
    body = json.loads(base64.b64decode(exchange["raw_response_base64"]))
    body["data"][0]["traceID"] = "f" * 32
    body["data"][0]["spans"][0]["traceID"] = "f" * 32
    encoded = canonical_json_bytes(body)
    encoded_base64 = base64.b64encode(encoded).decode("ascii")
    exchange["raw_response_base64"] = encoded_base64
    exchange["raw_response_sha256"] = sha256_bytes(encoded)
    artifact_sha256 = canonical_json_sha256(exchange)
    phase["jaeger_raw_response_base64"] = encoded_base64
    phase["jaeger_raw_response_sha256"] = sha256_bytes(encoded)
    phase["jaeger_raw_sha256"] = artifact_sha256
    attribution = next(
        payload
        for payload in artifacts.values()
        if payload["schema_version"] == "phase0.probe-getads-attribution.v1"
    )
    attribution["phase_correlations"]["baseline"]["jaeger_raw_sha256"] = artifact_sha256
