from __future__ import annotations

import inspect
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.provider_compatibility_v5 import (
    ProviderDecisionAliasV5,
    STATIC_PROVIDER_ALIAS_SCHEMA_V5,
    materialize_protocol_requests_v5,
    resolve_provider_alias_decision_v5,
    static_schema_sha256_v5,
)
from ecomsre.dta_v2.v22.controller_modes import ProviderOutputModeV22
from ecomsre.dta_v2.v22.controller_provider import ProviderHttpErrorV22
from ecomsre.dta_v2.v22.provider_protocol_v5 import (
    ProviderBoundaryTurnV5,
    safe_provider_failure_v5,
)
from ecomsre.dta_v2.v22.protocol_suite_v5 import (
    ProviderProtocolFailureClassV5,
    completed_transition_v5,
)
from scripts.ci import verify_dta_v22_pr_d_v5 as verifier


def test_static_schema_is_one_request_independent_conservative_shape() -> None:
    text = json.dumps(STATIC_PROVIDER_ALIAS_SCHEMA_V5, sort_keys=True)
    for forbidden in (
        "uniqueItems",
        "minItems",
        "maxItems",
        "pattern",
        "oneOf",
        "allOf",
        "if",
        "then",
        "else",
        "H00",
        "A00",
        "E00",
    ):
        assert forbidden not in text
    assert len(static_schema_sha256_v5()) == 64


def test_materialized_metrics_freeze_48_requests_and_one_static_schema() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    metrics = verifier._materialized_metrics(repository_root)
    assert set(metrics["replicate_transition_specs"]) == {"A", "B"}
    assert all(
        len(rows) == 24
        for rows in metrics["replicate_transition_specs"].values()
    )
    assert metrics["static_schema_sha256"] == static_schema_sha256_v5()
    assert metrics["projection_max_bytes_observed"] <= 12_000
    assert metrics["projection_mean_bytes_observed"] <= 8_000
    assert metrics["projected_input_token_max"] <= 5_500
    assert metrics["projected_input_token_mean"] <= 4_000


def test_manifest_static_contract_rejects_rehashed_protocol_drift() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    manifest = json.loads(
        (repository_root / verifier.MANIFEST_RELATIVE_V5).read_text(encoding="utf-8")
    )
    manifest["temperature"] = 1
    manifest["manifest_sha256"] = verifier.semantic_sha256_v22(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )
    with pytest.raises(ValueError, match="temperature"):
        verifier._verify_manifest_static_contract_v5(manifest)


def test_current_historical_v3_v4_public_bytes_are_exact() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    verifier._require_raw_bindings(
        repository_root, verifier.HISTORICAL_PUBLIC_RAW_V5
    )


def test_public_probe_rejects_bool_call_count_and_leakage(tmp_path: Path) -> None:
    value = {
        "schema_version": "dta-v22-pr-d-provider-compatibility-v5-probe-result.v1",
        "executed_at": "2026-08-20T00:00:00+00:00",
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
        "manifest_sha256": "c" * 64,
        "supported": False,
        "provider_calls": True,
        "selected_mode": None,
        "provider_request_sha256": None,
        "static_schema_sha256": None,
        "prompt_sha256": None,
        "probe_report_sha256": None,
        "failure_class": "PROVIDER_REQUEST_REJECTED",
        "safe_failure": {"safe_code": "HTTP_REQUEST_REJECTED"},
        "private_raw_sha256": "d" * 64,
        "private_semantic_sha256": "e" * 64,
        "manifest_binding_raw_sha256": "f" * 64,
        "manifest_binding_semantic_sha256": "0" * 64,
    }
    value["result_sha256"] = verifier.semantic_sha256_v22(value)
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="probe envelope"):
        verifier._verify_public_probe_v5(path)


def test_one_entrypoint_names_all_three_exact_states() -> None:
    source = inspect.getsource(verifier.verify_repository_v5)
    for state in (
        "V5_PRE_EXECUTION_READY",
        "V5_COMPLETE_PASS",
        "V5_COMPLETE_BLOCKED",
    ):
        assert state in source


def test_progress_verifier_uses_the_exact_v5_post_execution_states() -> None:
    source = inspect.getsource(verifier._verify_progress_v5)
    assert '"V5_COMPLETE_PASS"' in source
    assert '"V5_COMPLETE_BLOCKED"' in source


def test_public_negative_probe_requires_the_exact_safe_failure_schema(
    tmp_path: Path,
) -> None:
    failure = safe_provider_failure_v5(
        error=ProviderHttpErrorV22(
            status=400,
            code="invalid_request",
            error_type="invalid_request_error",
            param="tools",
        ),
        failure_stage="PROBE",
        request_payload_sha256=verifier.semantic_sha256_v22(
            verifier.provider_request_payload_v5(
                request=verifier.build_provider_probe_request_v5()
            )
        ),
    ).model_dump(mode="json")
    failure["message"] = "provider controlled detail"
    value = {
        "schema_version": "dta-v22-pr-d-provider-compatibility-v5-probe-result.v1",
        "executed_at": "2026-08-20T00:00:00+00:00",
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
        "manifest_sha256": "c" * 64,
        "supported": False,
        "provider_calls": 1,
        "selected_mode": None,
        "provider_request_sha256": None,
        "static_schema_sha256": None,
        "prompt_sha256": None,
        "probe_report_sha256": None,
        "failure_class": "PROVIDER_REQUEST_REJECTED",
        "safe_failure": failure,
        "private_raw_sha256": "d" * 64,
        "private_semantic_sha256": "e" * 64,
        "manifest_binding_raw_sha256": "f" * 64,
        "manifest_binding_semantic_sha256": "0" * 64,
    }
    value["result_sha256"] = verifier.semantic_sha256_v22(value)
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="safe failure"):
        verifier._verify_public_probe_v5(path)


def test_valid_typed_negative_probe_json_is_accepted(tmp_path: Path) -> None:
    failure_model = safe_provider_failure_v5(
        error=ProviderHttpErrorV22(
            status=400,
            code="invalid_request",
            error_type="invalid_request_error",
            param="tools",
        ),
        failure_stage="PROBE",
        request_payload_sha256=verifier.semantic_sha256_v22(
            verifier.provider_request_payload_v5(
                request=verifier.build_provider_probe_request_v5()
            )
        ),
    )
    failure = json.loads(failure_model.model_dump_json())
    assert (
        verifier._validate_persisted_json_v5(type(failure_model), failure)
        == failure_model
    )
    value = {
        "schema_version": "dta-v22-pr-d-provider-compatibility-v5-probe-result.v1",
        "executed_at": "2026-08-20T00:00:00+00:00",
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
        "manifest_sha256": "c" * 64,
        "supported": False,
        "provider_calls": 1,
        "selected_mode": None,
        "provider_request_sha256": None,
        "static_schema_sha256": None,
        "prompt_sha256": None,
        "probe_report_sha256": None,
        "failure_class": "PROVIDER_REQUEST_REJECTED",
        "safe_failure": failure,
        "private_raw_sha256": "d" * 64,
        "private_semantic_sha256": "e" * 64,
        "manifest_binding_raw_sha256": "f" * 64,
        "manifest_binding_semantic_sha256": "0" * 64,
    }
    value["result_sha256"] = verifier.semantic_sha256_v22(value)
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert verifier._verify_public_probe_v5(path) == value

    value["provider_request_sha256"] = "1" * 64
    value["result_sha256"] = verifier.semantic_sha256_v22(
        {key: item for key, item in value.items() if key != "result_sha256"}
    )
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="negative public probe"):
        verifier._verify_public_probe_v5(path)


def test_post_claim_counts_reject_boolean_integer_aliases() -> None:
    with pytest.raises(ValueError, match="strict integer"):
        verifier._verify_closure_claim_count_types_v5(
            review_must_fix_count=False,
            provider_call_count=True,
            observed_provider_calls=1,
        )


def test_private_turn_binding_rejects_rehashed_contradictory_turn() -> None:
    spec = next(
        item
        for item in materialize_protocol_requests_v5(replicate_id="A")
        if item.transition_kind == "ORDINARY" and item.protocol_intent == "READ"
    )
    alias_decision = ProviderDecisionAliasV5(
        decision="READ",
        hypothesis_alias=spec.request.alias_binding.hypotheses[0].alias,
        action_alias=next(
            item.alias for item in spec.request.alias_binding.actions if item.available
        ),
        support_aliases=(),
        contradict_aliases=(),
    )
    canonical_decision = resolve_provider_alias_decision_v5(
        alias_decision=alias_decision,
        binding=spec.request.alias_binding,
    )
    transition = completed_transition_v5(
        spec=spec,
        accepted=True,
        parsed_alias=True,
        alias_resolved=True,
        runtime_admitted=True,
        intent_conformant=True,
        input_tokens=100,
        output_tokens=10,
        latency_ms=1,
        provider_request_sha256=spec.request.request_sha256,
        raw_response_sha256="1" * 64,
        failure_class=ProviderProtocolFailureClassV5.ACCEPTED,
        provider_turn_sha256="2" * 64,
        raw_alias_decision_sha256="3" * 64,
        resolved_canonical_decision_sha256="4" * 64,
        alias_binding_sha256=spec.request.alias_binding.binding_sha256,
    )
    turn = ProviderBoundaryTurnV5.model_construct(
        mode=ProviderOutputModeV22.LOCAL_FAIL_CLOSED_JSON,
        provider_request_sha256=spec.request.request_sha256,
        projection_sha256=spec.request.projection_sha256,
        static_schema_sha256=spec.request.static_schema_sha256,
        prompt_sha256=verifier.semantic_sha256_v22(
            {"system_prompt": verifier.PROVIDER_BOUNDARY_SYSTEM_PROMPT_V5}
        ),
        request_payload_sha256=verifier.semantic_sha256_v22(
            verifier.provider_request_payload_v5(request=spec.request)
        ),
        alias_decision=alias_decision,
        canonical_decision=canonical_decision,
        failure_code=None,
        raw_response_sha256="1" * 64,
        turn_sha256="2" * 64,
        raw_alias_decision_sha256="3" * 64,
        resolved_canonical_decision_sha256=verifier.semantic_sha256_v22(
            canonical_decision.model_dump(mode="json")
        ),
        alias_binding_sha256=spec.request.alias_binding.binding_sha256,
    )
    transition = transition.model_copy(
        update={
            "resolved_canonical_decision_sha256": (
                turn.resolved_canonical_decision_sha256
            )
        }
    )
    verifier._verify_private_completed_turn_bindings_v5((transition,), (turn,))
    forged = turn.model_copy(update={"raw_response_sha256": "5" * 64})
    with pytest.raises(ValueError, match="completed turn binding"):
        verifier._verify_private_completed_turn_bindings_v5(
            (transition,), (forged,)
        )


def test_changed_path_detection_includes_deleted_files(tmp_path: Path) -> None:
    subprocess.run(("git", "init", str(tmp_path)), check=True, capture_output=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.name", "Test"), check=True
    )
    target = tmp_path / "deleted.txt"
    target.write_text("frozen\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "add", "deleted.txt"), check=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "commit", "-m", "base"),
        check=True,
        capture_output=True,
    )
    base = subprocess.run(
        ("git", "-C", str(tmp_path), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    target.unlink()
    subprocess.run(("git", "-C", str(tmp_path), "add", "-u"), check=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "commit", "-m", "delete"),
        check=True,
        capture_output=True,
    )
    assert verifier._changed_paths_between(tmp_path, base, "HEAD") == {"deleted.txt"}


@pytest.mark.parametrize(
    ("probe_supported", "reports"),
    (
        (True, {"B": SimpleNamespace(completed_response_count=0)}),
        (True, {"A": SimpleNamespace(completed_response_count=24)}),
        (False, {"A": SimpleNamespace(completed_response_count=0)}),
    ),
)
def test_campaign_replicate_state_rejects_forbidden_presence_sets(
    probe_supported: bool,
    reports: dict[str, SimpleNamespace],
) -> None:
    with pytest.raises(ValueError, match="replicate state"):
        verifier._verify_campaign_replicate_state_v5(
            probe_supported=probe_supported,
            reports=reports,
        )


def test_public_probe_identity_is_exactly_bound_to_campaign() -> None:
    manifest = {"manifest_sha256": "c" * 64}
    campaign = {
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
    }
    probe = {
        "manifest_sha256": "c" * 64,
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
    }
    verifier._verify_public_probe_campaign_identity_v5(
        manifest=manifest,
        campaign=campaign,
        public_probe=probe,
    )
    probe["implementation_commit"] = "d" * 40
    with pytest.raises(ValueError, match="probe identity"):
        verifier._verify_public_probe_campaign_identity_v5(
            manifest=manifest,
            campaign=campaign,
            public_probe=probe,
        )
