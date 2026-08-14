from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ecomsre_live_sandbox.contracts import (
    DiagnosisGate,
    DiagnosisResult,
    LocalDemoStandingAuthorization,
    PolicyVerdict,
    load_bundle,
)
from ecomsre_live_sandbox.control import (
    build_plan,
    evaluate_diagnosis_gate,
    evaluate_local_demo_diagnosis_gate,
    evaluate_policy,
)
from ecomsre_live_sandbox.local_demo import simulate_local_demo


CONFIG = Path("config/live-telemetry-controlled-remediation-v1")
R3_REFS = ("metric:0011", "metric:0003", "trace:0015", "trace:0007")
CONTEXT_SHA256 = "b1949108aa80b7f2c0aad24a757d67bc5d704c7e9c6658f84508eaa1acc5fb07"


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(CONFIG)


@pytest.fixture
def r3_diagnosis() -> DiagnosisResult:
    return DiagnosisResult(
        terminal="COMPLETED",
        root_service="payment",
        root_entity_ref="apm|apm.service|payment",
        fault_type_raw="upstream payment failure",
        fault_class="PROPAGATION",
        confidence=0.93,
        evidence_refs=R3_REFS,
        evidence_source_types=("METRICS", "TRACES"),
        summary="Payment is the selected causal root.",
        semantic_model_calls=1,
        specialist_calls=0,
        fusion_calls=0,
        provider_attempts=1,
        transport_retries=0,
        usage_tokens=4372,
    )


@pytest.fixture
def standing_authorization(bundle) -> LocalDemoStandingAuthorization:
    return LocalDemoStandingAuthorization(
        approver="Minghong Sun",
        environment_id=bundle.environment.environment_id,
        sandbox_id=bundle.environment.sandbox_id,
        scenario_id=bundle.scenario.scenario_id,
        target_service="payment",
        configuration_key="paymentFailure.defaultVariant",
        action="RESTORE_FROZEN_SERVICE_CONFIGURATION",
        baseline_sha256=bundle.scenario.baseline_document_sha256,
        created_at=datetime.now(timezone.utc),
    )


def _local_admission(diagnosis: DiagnosisResult, bundle):
    return evaluate_local_demo_diagnosis_gate(
        diagnosis,
        bundle,
        resolvable_refs=frozenset(R3_REFS),
        context_sha256=CONTEXT_SHA256,
        provider_live_input_sha256=CONTEXT_SHA256,
        control_truth_findings=(),
        visible_entity_refs={"apm|apm.service|payment"},
    )


def _local_gate(diagnosis: DiagnosisResult, bundle) -> DiagnosisGate:
    admission = _local_admission(diagnosis, bundle)
    return DiagnosisGate(
        passed=admission.passed,
        reason_codes=admission.blocking_reason_codes,
    )


def _policy(
    diagnosis: DiagnosisResult,
    bundle,
    standing_authorization: LocalDemoStandingAuthorization,
    *,
    endpoint: str = "unix:///tmp/docker.sock",
    labels: dict[str, str] | None = None,
    forward_mutations: int = 0,
):
    plan = build_plan(diagnosis, bundle, gate_evaluator=_local_gate)
    return plan, evaluate_policy(
        plan=plan,
        diagnosis=diagnosis,
        bundle=bundle,
        docker_endpoint=endpoint,
        owned_labels=labels
        or {
            "com.docker.compose.project": bundle.environment.compose_project,
            bundle.environment.sandbox_label_key: bundle.environment.sandbox_id,
        },
        forward_mutations=forward_mutations,
        now=datetime.now(timezone.utc),
        standing_authorization=standing_authorization,
        gate_evaluator=_local_gate,
    )


def test_r3_strict_negative_is_preserved_but_local_demo_admits(
    bundle, r3_diagnosis
) -> None:
    strict = evaluate_diagnosis_gate(r3_diagnosis, bundle)
    local = _local_admission(r3_diagnosis, bundle)

    assert strict.passed is False
    assert strict.reason_codes == ("FAULT_CLASS_MISMATCH",)
    assert local.passed is True
    assert local.blocking_reason_codes == ()
    assert local.warning_codes == ("FAULT_CLASS_MISMATCH_WARNING",)
    assert local.root_match is True
    assert local.fault_class_match is False
    assert local.evidence_valid is True
    assert local.source_coverage_valid is True
    assert local.single_call_valid is True
    assert local.context_binding_valid is True


def test_local_demo_rejects_wrong_root(bundle, r3_diagnosis) -> None:
    diagnosis = r3_diagnosis.model_copy(
        update={
            "root_service": "checkout",
            "root_entity_ref": "apm|apm.service|checkout",
            "fault_class": "APPLICATION",
        }
    )

    admission = _local_admission(diagnosis, bundle)

    assert admission.passed is False
    assert "ROOT_SERVICE_MISMATCH" in admission.blocking_reason_codes
    assert "ROOT_ENTITY_MISMATCH" in admission.blocking_reason_codes


@pytest.mark.parametrize(
    ("updates", "resolvable_refs", "reason"),
    [
        ({"evidence_refs": (), "evidence_source_types": ()}, frozenset(), "EVIDENCE_REFS_EMPTY"),
        (
            {
                "evidence_refs": ("metric:0011", "metric:0011", "trace:0015"),
                "evidence_source_types": ("METRICS", "TRACES"),
            },
            frozenset({"metric:0011", "trace:0015"}),
            "EVIDENCE_REFS_DUPLICATE",
        ),
        (
            {
                "evidence_refs": ("metric:0011", "event:0001"),
                "evidence_source_types": ("METRICS",),
            },
            frozenset({"metric:0011", "event:0001"}),
            "EVIDENCE_REF_PREFIX_INVALID",
        ),
        (
            {
                "evidence_refs": ("metric:0011", "trace:9999"),
                "evidence_source_types": ("METRICS", "TRACES"),
            },
            frozenset({"metric:0011"}),
            "EVIDENCE_REF_UNRESOLVED",
        ),
        (
            {
                "evidence_refs": ("trace:0015",),
                "evidence_source_types": ("TRACES",),
            },
            frozenset({"trace:0015"}),
            "EVIDENCE_SOURCE_COVERAGE_INSUFFICIENT",
        ),
        (
            {
                "evidence_refs": ("metric:0011",),
                "evidence_source_types": ("METRICS",),
            },
            frozenset({"metric:0011"}),
            "EVIDENCE_SOURCE_COVERAGE_INSUFFICIENT",
        ),
        (
            {"evidence_source_types": ("METRICS", "LOGS")},
            frozenset(R3_REFS),
            "EVIDENCE_SOURCE_ACCOUNTING_MISMATCH",
        ),
    ],
)
def test_local_demo_rejects_invalid_evidence(
    bundle,
    r3_diagnosis,
    updates: dict[str, object],
    resolvable_refs: frozenset[str],
    reason: str,
) -> None:
    admission = evaluate_local_demo_diagnosis_gate(
        r3_diagnosis.model_copy(update=updates),
        bundle,
        resolvable_refs=resolvable_refs,
        context_sha256=CONTEXT_SHA256,
        provider_live_input_sha256=CONTEXT_SHA256,
        control_truth_findings=(),
        visible_entity_refs={"apm|apm.service|payment"},
    )

    assert admission.passed is False
    assert reason in admission.blocking_reason_codes


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"semantic_model_calls": 0}, "SEMANTIC_MODEL_CALL_COUNT_INVALID"),
        ({"semantic_model_calls": 2}, "SEMANTIC_MODEL_CALL_COUNT_INVALID"),
        ({"specialist_calls": 1}, "UNAUTHORIZED_MODEL_CALL"),
        ({"fusion_calls": 1}, "UNAUTHORIZED_MODEL_CALL"),
    ],
)
def test_local_demo_rejects_invalid_model_call_shape(
    bundle, r3_diagnosis, updates: dict[str, object], reason: str
) -> None:
    # model_copy deliberately bypasses the strict historical schema so the
    # admission boundary itself is exercised against hostile runtime objects.
    admission = _local_admission(r3_diagnosis.model_copy(update=updates), bundle)

    assert admission.passed is False
    assert reason in admission.blocking_reason_codes


def test_local_demo_rejects_context_drift_or_control_truth(
    bundle, r3_diagnosis
) -> None:
    drift = evaluate_local_demo_diagnosis_gate(
        r3_diagnosis,
        bundle,
        resolvable_refs=frozenset(R3_REFS),
        context_sha256=CONTEXT_SHA256,
        provider_live_input_sha256="a" * 64,
        control_truth_findings=(),
        visible_entity_refs={"apm|apm.service|payment"},
    )
    leaked = evaluate_local_demo_diagnosis_gate(
        r3_diagnosis,
        bundle,
        resolvable_refs=frozenset(R3_REFS),
        context_sha256=CONTEXT_SHA256,
        provider_live_input_sha256=CONTEXT_SHA256,
        control_truth_findings=("scenario_id",),
        visible_entity_refs={"apm|apm.service|payment"},
    )

    assert "CONTEXT_BINDING_MISMATCH" in drift.blocking_reason_codes
    assert "CONTROL_TRUTH_VISIBLE" in leaked.blocking_reason_codes


@pytest.mark.parametrize(
    "root_entity_ref",
    ("attacker|fake|payment", "k8s|pod|payment"),
)
def test_local_demo_rejects_unresolved_or_wrong_layer_payment_suffix(
    bundle, r3_diagnosis, root_entity_ref: str
) -> None:
    diagnosis = r3_diagnosis.model_copy(
        update={"root_entity_ref": root_entity_ref}
    )

    admission = evaluate_local_demo_diagnosis_gate(
        diagnosis,
        bundle,
        resolvable_refs=frozenset(R3_REFS),
        context_sha256=CONTEXT_SHA256,
        provider_live_input_sha256=CONTEXT_SHA256,
        control_truth_findings=(),
        visible_entity_refs={
            "apm|apm.service|payment",
            "k8s|pod|payment",
        },
    )

    assert admission.passed is False
    assert "ROOT_ENTITY_MISMATCH" in admission.blocking_reason_codes


def test_plan_and_policy_keep_strict_default_and_allow_only_local_scope(
    bundle, r3_diagnosis, standing_authorization
) -> None:
    with pytest.raises(ValueError, match="diagnosis gate"):
        build_plan(r3_diagnosis, bundle)

    plan, policy = _policy(r3_diagnosis, bundle, standing_authorization)
    assert plan.action == "RESTORE_FROZEN_SERVICE_CONFIGURATION"
    assert policy.verdict is PolicyVerdict.ALLOW

    wrong_action = plan.model_copy(update={"action": "ARBITRARY_MODEL_ACTION"})
    wrong_action_policy = evaluate_policy(
        plan=wrong_action,
        diagnosis=r3_diagnosis,
        bundle=bundle,
        docker_endpoint="unix:///tmp/docker.sock",
        owned_labels={
            "com.docker.compose.project": bundle.environment.compose_project,
            bundle.environment.sandbox_label_key: bundle.environment.sandbox_id,
        },
        forward_mutations=0,
        now=datetime.now(timezone.utc),
        standing_authorization=standing_authorization,
        gate_evaluator=_local_gate,
    )
    assert wrong_action_policy.verdict is PolicyVerdict.DENY
    assert "PLAN_ACTION_MISMATCH" in wrong_action_policy.reason_codes

    for policy in (
        _policy(
            r3_diagnosis,
            bundle,
            standing_authorization,
            endpoint="tcp://remote.example:2376",
        )[1],
        _policy(
            r3_diagnosis,
            bundle,
            standing_authorization,
            labels={"com.docker.compose.project": "foreign"},
        )[1],
        _policy(
            r3_diagnosis,
            bundle,
            standing_authorization,
            forward_mutations=1,
        )[1],
    ):
        assert policy.verdict is PolicyVerdict.DENY


def test_complete_simulated_local_demo_executes_one_mutation_and_two_windows(
    bundle, r3_diagnosis, standing_authorization
) -> None:
    result = simulate_local_demo(
        diagnosis=r3_diagnosis,
        bundle=bundle,
        standing_authorization=standing_authorization,
        resolvable_refs=frozenset(R3_REFS),
        context_sha256=CONTEXT_SHA256,
        provider_live_input_sha256=CONTEXT_SHA256,
    )

    assert result["strict_audit_pass"] is False
    assert result["local_demo_pass"] is True
    assert result["policy"] == "ALLOW"
    assert result["forward_mutations"] == 1
    assert result["recovery_windows"] == 2
    assert result["verification"] == "PASS"
    assert result["baseline_restored"] is True
    assert result["cleanup"] == "CLEAN"
