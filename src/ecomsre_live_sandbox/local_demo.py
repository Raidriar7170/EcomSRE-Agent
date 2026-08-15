"""Pure LOCAL_DEMO admission and simulated remediation helpers."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta, timezone

from ecomsre_live_sandbox.contracts import (
    CleanupResult,
    ConfigBundle,
    ConfigurationState,
    DiagnosisGate,
    DiagnosisResult,
    LocalDemoStandingAuthorization,
    PolicyVerdict,
    SLIWindow,
)
from ecomsre_live_sandbox.control import (
    ForwardMutationCounter,
    InMemoryConfigurationAdapter,
    IndependentVerifier,
    LocalSandboxRestrictedExecutor,
    build_plan,
    evaluate_diagnosis_gate,
    evaluate_local_demo_diagnosis_gate,
    evaluate_policy,
)


def _window(phase: str, index: int) -> SLIWindow:
    ended_at = datetime.now(timezone.utc) + timedelta(seconds=index)
    return SLIWindow(
        phase=phase,  # type: ignore[arg-type]
        started_at=ended_at - timedelta(seconds=30),
        ended_at=ended_at,
        request_count=100.0,
        error_count=0.0,
        error_rate=0.0,
        p95_latency_ms=10.0,
        runtime_health=1.0,
        sample_count=100,
    )


def simulate_local_demo(
    *,
    diagnosis: DiagnosisResult,
    bundle: ConfigBundle,
    standing_authorization: LocalDemoStandingAuthorization,
    resolvable_refs: Collection[str],
    context_sha256: str,
    provider_live_input_sha256: str | None,
) -> dict[str, object]:
    """Execute the safety-critical path against an in-memory frozen adapter."""

    strict = evaluate_diagnosis_gate(diagnosis, bundle)
    admission = evaluate_local_demo_diagnosis_gate(
        diagnosis,
        bundle,
        resolvable_refs=resolvable_refs,
        context_sha256=context_sha256,
        provider_live_input_sha256=provider_live_input_sha256,
        control_truth_findings=(),
        visible_entity_refs={"apm|apm.service|payment"},
    )

    def local_gate(current: DiagnosisResult, current_bundle: ConfigBundle) -> DiagnosisGate:
        current_admission = evaluate_local_demo_diagnosis_gate(
            current,
            current_bundle,
            resolvable_refs=resolvable_refs,
            context_sha256=context_sha256,
            provider_live_input_sha256=provider_live_input_sha256,
            control_truth_findings=(),
            visible_entity_refs={"apm|apm.service|payment"},
        )
        return DiagnosisGate(
            passed=current_admission.passed,
            reason_codes=current_admission.blocking_reason_codes,
        )

    plan = build_plan(diagnosis, bundle, gate_evaluator=local_gate)
    policy = evaluate_policy(
        plan=plan,
        diagnosis=diagnosis,
        bundle=bundle,
        docker_endpoint="unix:///tmp/docker.sock",
        owned_labels={
            "com.docker.compose.project": bundle.environment.compose_project,
            bundle.environment.sandbox_label_key: bundle.environment.sandbox_id,
        },
        forward_mutations=0,
        now=datetime.now(timezone.utc),
        standing_authorization=standing_authorization,
        gate_evaluator=local_gate,
    )
    if policy.verdict is not PolicyVerdict.ALLOW:
        raise RuntimeError("simulated LOCAL_DEMO Policy Gate denied")
    controller = InMemoryConfigurationAdapter(
        baseline=ConfigurationState(
            variant="off",
            value=0,
            document_sha256=bundle.scenario.baseline_document_sha256,
        ),
        fault=ConfigurationState(
            variant="100%",
            value=1,
            document_sha256=bundle.scenario.fault_document_sha256,
        ),
        current="FAULT",
    )
    counter = ForwardMutationCounter()
    receipt = LocalSandboxRestrictedExecutor().execute(
        plan=plan,
        policy=policy,
        controller=controller,
        mutation_counter=counter,
    )
    baseline = (_window("BASELINE", 1), _window("BASELINE", 2))
    recovery = (_window("RECOVERY", 3), _window("RECOVERY", 4))
    verification = IndependentVerifier().verify(
        plan=plan,
        receipt=receipt,
        current=controller.read_current(),
        baseline_windows=baseline,
        recovery_windows=recovery,
        services_healthy=True,
        labels_exact=True,
        bundle=bundle,
    )
    cleanup = CleanupResult(
        baseline_restored=(
            controller.read_current().document_sha256
            == bundle.scenario.baseline_document_sha256
        ),
        owned_containers=0,
        owned_networks=0,
        owned_volumes=0,
        non_owned_resources_changed=False,
        verdict="CLEAN",
    )
    return {
        "strict_audit_pass": strict.passed,
        "strict_reason_codes": strict.reason_codes,
        "local_demo_pass": admission.passed,
        "local_demo_warning_codes": admission.warning_codes,
        "policy": policy.verdict.value,
        "forward_mutations": counter.count,
        "recovery_windows": len(recovery),
        "verification": "PASS" if verification.passed else "FAIL",
        "baseline_restored": cleanup.baseline_restored,
        "cleanup": cleanup.verdict,
    }


__all__ = ["simulate_local_demo"]
