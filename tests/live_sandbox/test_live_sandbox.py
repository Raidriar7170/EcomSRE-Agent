from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path

import pytest

from ecomsre.live_sandbox.contracts import (
    ApprovalRequest,
    CleanupResult,
    ConfigurationState,
    DiagnosisResult,
    LiveRemediationPlan,
    PolicyDecision,
    PolicyVerdict,
    SLIWindow,
    canonical_sha256,
    load_bundle,
)
from ecomsre.live_sandbox.control import (
    ForwardMutationCounter,
    InMemoryConfigurationAdapter,
    IndependentVerifier,
    LocalSandboxRestrictedExecutor,
    approve_plan,
    build_flag_documents,
    build_plan,
    compensate_rollback,
    evaluate_diagnosis_gate,
    evaluate_policy,
)
from ecomsre.live_sandbox.environment import (
    DockerBoundaryError,
    ResourceOwnershipError,
    discover_endpoints,
    require_local_docker_endpoint,
    require_owned_labels,
)
from ecomsre.live_sandbox.telemetry import (
    build_a0_context,
    parse_jaeger_response,
    parse_opensearch_response,
    parse_prometheus_vector,
)
from ecomsre_rca100.prompt import output_schema_sha256, prompt_sha256


CONFIG = Path("config/live-telemetry-controlled-remediation-v1")


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(CONFIG)


def _diagnosis(bundle, *, fault_class: str = "APPLICATION") -> DiagnosisResult:
    return DiagnosisResult(
        terminal="COMPLETED",
        root_service="payment",
        root_entity_ref="apm|apm.service|payment",
        fault_type_raw="payment request error",
        fault_class=fault_class,
        confidence=0.91,
        evidence_refs=("metric:0001", "log:0001"),
        evidence_source_types=("METRICS", "LOGS"),
        summary="Payment request errors are the causal root.",
        semantic_model_calls=1,
        specialist_calls=0,
        fusion_calls=0,
    )


def _request(bundle, now: datetime) -> ApprovalRequest:
    template = LiveRemediationPlan.template_payload(bundle)
    return ApprovalRequest(
        approval_request_id="approval-0000000000000001",
        scenario_id=bundle.scenario.scenario_id,
        scenario_lock_sha256="a" * 64,
        plan_template_sha256=canonical_sha256(template),
        environment_id=bundle.environment.environment_id,
        sandbox_id=bundle.environment.sandbox_id,
        action=bundle.policy.action,
        target_service=bundle.scenario.target_service,
        configuration_key=bundle.scenario.target_configuration_key,
        baseline_sha256=bundle.scenario.baseline_document_sha256,
        max_forward_mutations=1,
        requested_at=now,
        expires_at=now + timedelta(hours=1),
    )


def test_remote_docker_context_is_denied() -> None:
    assert require_local_docker_endpoint("unix:///tmp/docker.sock") == "/tmp/docker.sock"
    for endpoint in ("tcp://docker.example:2376", "ssh://host", "npipe:////pipe/docker"):
        with pytest.raises(DockerBoundaryError):
            require_local_docker_endpoint(endpoint)


def test_missing_or_wrong_ownership_label_is_denied(bundle) -> None:
    labels = {
        "com.docker.compose.project": bundle.environment.compose_project,
        bundle.environment.sandbox_label_key: bundle.environment.sandbox_id,
    }
    require_owned_labels(labels, bundle.environment)
    for key in tuple(labels):
        damaged = dict(labels)
        damaged.pop(key)
        with pytest.raises(ResourceOwnershipError):
            require_owned_labels(damaged, bundle.environment)


def test_flag_documents_are_exactly_two_hash_bound_whole_documents(bundle) -> None:
    upstream = json.loads(
        Path("third_party/opentelemetry-demo/src/flagd/demo.flagd.json").read_text()
    )
    baseline, fault = build_flag_documents(upstream, bundle)
    assert canonical_sha256(baseline) == bundle.scenario.baseline_document_sha256
    assert canonical_sha256(fault) == bundle.scenario.fault_document_sha256
    assert baseline["flags"]["loadGeneratorVUs"]["defaultVariant"] == "25"
    assert baseline["flags"]["paymentFailure"]["defaultVariant"] == "off"
    assert fault["flags"]["paymentFailure"]["defaultVariant"] == "100%"
    baseline["flags"]["paymentFailure"]["defaultVariant"] = "100%"
    assert baseline == fault


def test_live_diagnosis_reuses_exact_main_a0_prompt_and_schema(bundle) -> None:
    assert prompt_sha256() == bundle.diagnosis.prompt_sha256
    assert output_schema_sha256() == bundle.diagnosis.output_schema_sha256


def test_endpoint_discovery_uses_only_resolved_compose_ports(bundle) -> None:
    services = {}
    for name, target, published in (
        ("frontend-proxy", 8080, 18080),
        ("flagd", 8016, 18016),
        ("prometheus", 9090, 19090),
        ("jaeger", 16686, 11686),
        ("opensearch", 9200, 19200),
    ):
        services[name] = {
            "ports": [
                {
                    "host_ip": "127.0.0.1",
                    "target": target,
                    "published": str(published),
                    "protocol": "tcp",
                }
            ]
        }
    endpoints = discover_endpoints({"services": services}, bundle)
    assert endpoints.prometheus == "http://127.0.0.1:19090"
    assert endpoints.opensearch == "http://127.0.0.1:19200"
    assert endpoints.jaeger == "http://127.0.0.1:11686"
    assert endpoints.flag_control == "http://127.0.0.1:18080/feature/api"
    assert endpoints.flag_evaluation == "http://127.0.0.1:18016"


def test_metrics_logs_and_traces_parsers_keep_real_identity_fields() -> None:
    metric = parse_prometheus_vector(
        {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {"service_name": "payment"}, "value": [1, "4.5"]}]}},
        expected_service="payment",
    )
    assert metric == 4.5
    logs = parse_opensearch_response(
        {"hits": {"hits": [{"_source": {"observedTimestamp": "2026-08-11T00:00:00Z", "resource": {"service": {"name": "payment"}, "service.instance.id": "instance-1"}, "severity": {"text": "WARN"}, "body": "request failed", "traceId": "a" * 32, "spanId": "b" * 16}}]}},
        expected_service="payment",
    )
    assert logs[0].service_name == "payment"
    assert logs[0].service_instance_id == "instance-1"
    traces = parse_jaeger_response(
        {"data": [{"traceID": "c" * 32, "processes": {"p1": {"serviceName": "payment", "tags": [{"key": "service.instance.id", "value": "instance-2"}]}}, "spans": [{"traceID": "c" * 32, "spanID": "d" * 16, "processID": "p1", "operationName": "charge", "startTime": 1_000_000, "duration": 20_000, "tags": [{"key": "otel.status_code", "value": "ERROR"}]}]}]},
        expected_service="payment",
    )
    assert traces[0].span_name == "charge"
    assert traces[0].service_instance_id == "instance-2"


def test_a0_context_contains_no_scenario_or_control_truth(bundle) -> None:
    window = SLIWindow(
        phase="FAULT",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        ended_at=datetime.now(timezone.utc),
        request_count=10,
        error_count=8,
        error_rate=0.8,
        p95_latency_ms=25.0,
        runtime_health=1.0,
        sample_count=6,
    )
    context = build_a0_context(
        alert_title="Observed payment request error rate increase",
        baseline_windows=(window.model_copy(update={"phase": "BASELINE", "error_count": 0, "error_rate": 0.0}),) * 2,
        fault_windows=(window, window),
        log_evidence=parse_opensearch_response(
            {"hits": {"hits": [{"_source": {"observedTimestamp": "2026-08-11T00:00:00Z", "resource.service.name": "payment", "severity.text": "WARN", "body": "request failed"}}]}},
            expected_service="payment",
        ),
        trace_evidence=parse_jaeger_response(
            {"data": [{"traceID": "c" * 32, "processes": {"p1": {"serviceName": "payment"}}, "spans": [{"traceID": "c" * 32, "spanID": "d" * 16, "processID": "p1", "operationName": "charge", "startTime": 1_000_000, "duration": 20_000, "tags": []}]}]},
            expected_service="payment",
        ),
    )
    payload = context.model_dump_json()
    for forbidden in (
        bundle.environment.sandbox_id,
        bundle.scenario.scenario_id,
        bundle.scenario.target_flag,
        bundle.policy.action,
        "expected_root_service",
    ):
        assert forbidden not in payload
    refs = {item.evidence_ref for item in context.metrics.evidence + context.logs.evidence + context.traces.evidence}
    assert refs == {"metric:0001", "log:0001", "trace:0001"}


def test_diagnosis_gate_requires_exact_root_class_and_cross_source_evidence(bundle) -> None:
    good = _diagnosis(bundle)
    assert evaluate_diagnosis_gate(good, bundle).passed
    for damaged in (
        good.model_copy(update={"root_service": "checkout"}),
        good.model_copy(update={"fault_class": "DEPENDENCY"}),
        good.model_copy(update={"evidence_source_types": ("METRICS",)}),
        good.model_copy(update={"evidence_refs": ("bogus:0001",)}),
        good.model_copy(update={"terminal": "INVALID_SCHEMA"}),
    ):
        assert not evaluate_diagnosis_gate(damaged, bundle).passed


@pytest.mark.parametrize(
    "damage",
    (
        "request_hash",
        "expired",
        "scenario",
        "environment",
        "target",
        "key",
        "action",
        "sandbox",
        "baseline",
    ),
)
def test_policy_denies_forged_expired_or_mismatched_approval(bundle, damage: str) -> None:
    now = datetime.now(timezone.utc)
    request = _request(bundle, now)
    diagnosis = _diagnosis(bundle)
    plan = build_plan(diagnosis, bundle)
    approval = approve_plan(request, approver="Human Reviewer", now=now)
    if damage == "request_hash":
        approval = approval.model_copy(update={"request_sha256": "f" * 64})
    elif damage == "expired":
        approval = approval.model_copy(update={"approved_at": request.expires_at + timedelta(seconds=1)})
    elif damage == "scenario":
        request = request.model_copy(update={"scenario_id": "00000000-0000-4000-8000-000000000000"})
    elif damage == "environment":
        request = request.model_copy(update={"environment_id": "another-environment"})
    elif damage == "target":
        plan = plan.model_copy(update={"target_service": "checkout"})
    elif damage == "key":
        plan = plan.model_copy(update={"configuration_key": "other.defaultVariant"})
    elif damage == "action":
        raw = plan.model_dump(mode="json")
        raw["action"] = "RESTART"
        with pytest.raises(Exception):
            LiveRemediationPlan.model_validate(raw)
        return
    elif damage == "sandbox":
        approval = approval.model_copy(update={"sandbox_id": "00000000-0000-4000-8000-000000000000"})
    elif damage == "baseline":
        plan = plan.model_copy(update={"baseline_sha256": "b" * 64})
    decision = evaluate_policy(
        plan=plan,
        diagnosis=diagnosis,
        request=request,
        approval=approval,
        bundle=bundle,
        docker_endpoint="unix:///tmp/docker.sock",
        owned_labels={
            "com.docker.compose.project": bundle.environment.compose_project,
            bundle.environment.sandbox_label_key: bundle.environment.sandbox_id,
        },
        forward_mutations=0,
        now=now,
    )
    assert decision.verdict is PolicyVerdict.DENY


def test_restricted_executor_has_no_argv_surface_and_rejects_second_mutation(bundle) -> None:
    signature = inspect.signature(LocalSandboxRestrictedExecutor.execute)
    assert "argv" not in signature.parameters and "command" not in signature.parameters
    diagnosis = _diagnosis(bundle)
    plan = build_plan(diagnosis, bundle)
    counter = ForwardMutationCounter()
    adapter = InMemoryConfigurationAdapter(
        baseline=ConfigurationState(variant="off", value=0, document_sha256=bundle.scenario.baseline_document_sha256),
        fault=ConfigurationState(variant="100%", value=1, document_sha256=bundle.scenario.fault_document_sha256),
        current="FAULT",
    )
    allow = PolicyDecision(verdict="ALLOW", reason_codes=("ALLOWED",))
    receipt = LocalSandboxRestrictedExecutor().execute(
        plan=plan,
        policy=allow,
        controller=adapter,
        mutation_counter=counter,
    )
    assert receipt.forward_mutation_number == 1 and receipt.after_sha256 == bundle.scenario.baseline_document_sha256
    with pytest.raises(RuntimeError, match="second forward mutation"):
        LocalSandboxRestrictedExecutor().execute(
            plan=plan,
            policy=allow,
            controller=adapter,
            mutation_counter=counter,
        )


def test_independent_verifier_pass_and_failure_compensates_exact_rollback(bundle) -> None:
    diagnosis = _diagnosis(bundle)
    plan = build_plan(diagnosis, bundle)
    counter = ForwardMutationCounter()
    adapter = InMemoryConfigurationAdapter(
        baseline=ConfigurationState(variant="off", value=0, document_sha256=bundle.scenario.baseline_document_sha256),
        fault=ConfigurationState(variant="100%", value=1, document_sha256=bundle.scenario.fault_document_sha256),
        current="FAULT",
    )
    receipt = LocalSandboxRestrictedExecutor().execute(
        plan=plan,
        policy=PolicyDecision(verdict="ALLOW", reason_codes=("ALLOWED",)),
        controller=adapter,
        mutation_counter=counter,
    )
    baseline = SLIWindow(
        phase="BASELINE",
        started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        ended_at=datetime.now(timezone.utc),
        request_count=10,
        error_count=0,
        error_rate=0.0,
        p95_latency_ms=20.0,
        runtime_health=1.0,
        sample_count=6,
    )
    recovered = baseline.model_copy(update={"phase": "RECOVERY", "error_rate": 0.01})
    verifier = IndependentVerifier()
    result = verifier.verify(
        plan=plan,
        receipt=receipt,
        current=adapter.read_current(),
        baseline_windows=(baseline, baseline),
        recovery_windows=(recovered, recovered),
        services_healthy=True,
        labels_exact=True,
        bundle=bundle,
    )
    assert result.passed
    failed = result.model_copy(update={"passed": False, "reason_codes": ("RECOVERY_SLI_FAILED",)})
    rollback = compensate_rollback(
        receipt=receipt,
        verification=failed,
        controller=adapter,
    )
    assert rollback.executed and rollback.exact_hash_verified
    assert adapter.read_current().document_sha256 == bundle.scenario.fault_document_sha256


def test_cleanup_result_rejects_non_owned_change() -> None:
    assert CleanupResult(
        baseline_restored=True,
        owned_containers=0,
        owned_networks=0,
        owned_volumes=0,
        non_owned_resources_changed=False,
        verdict="CLEAN",
    ).verdict == "CLEAN"
    with pytest.raises(ValueError):
        CleanupResult(
            baseline_restored=True,
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            non_owned_resources_changed=True,
            verdict="CLEAN",
        )
