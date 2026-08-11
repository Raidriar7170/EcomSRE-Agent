"""Frozen flag control, diagnosis gate, policy, execution, and verification."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Literal, Protocol
from urllib.request import Request, urlopen

from ecomsre.live_sandbox.contracts import (
    ApprovalRequest,
    ConfigBundle,
    ConfigurationState,
    DiagnosisGate,
    DiagnosisResult,
    ExecutionReceipt,
    HumanApprovalRecord,
    LiveRemediationPlan,
    LocalEndpoints,
    PolicyDecision,
    PolicyVerdict,
    RollbackReceipt,
    SLIWindow,
    VerificationResult,
    canonical_json_bytes,
    canonical_sha256,
)
from ecomsre.live_sandbox.environment import (
    DockerBoundaryError,
    ResourceOwnershipError,
    require_local_docker_endpoint,
    require_owned_labels,
)


def build_flag_documents(
    upstream: Mapping[str, object], bundle: ConfigBundle
) -> tuple[dict[str, object], dict[str, object]]:
    baseline = deepcopy(dict(upstream))
    flags = baseline.get("flags")
    if not isinstance(flags, dict):
        raise ValueError("upstream flag document lacks a flags object")
    load = flags.get("loadGeneratorVUs")
    payment = flags.get(bundle.scenario.target_flag)
    if not isinstance(load, dict) or not isinstance(payment, dict):
        raise ValueError("required frozen flag definitions are unavailable")
    load["defaultVariant"] = str(bundle.scenario.load_generator_vus)
    payment["defaultVariant"] = bundle.scenario.baseline_variant
    fault = deepcopy(baseline)
    fault_flags = fault["flags"]
    assert isinstance(fault_flags, dict)
    fault_payment = fault_flags[bundle.scenario.target_flag]
    assert isinstance(fault_payment, dict)
    fault_payment["defaultVariant"] = bundle.scenario.fault_variant
    if canonical_sha256(baseline) != bundle.scenario.baseline_document_sha256:
        raise ValueError("frozen baseline flag document hash differs")
    if canonical_sha256(fault) != bundle.scenario.fault_document_sha256:
        raise ValueError("frozen fault flag document hash differs")
    probe = deepcopy(baseline)
    probe_flags = probe["flags"]
    assert isinstance(probe_flags, dict)
    probe_payment = probe_flags[bundle.scenario.target_flag]
    assert isinstance(probe_payment, dict)
    probe_payment["defaultVariant"] = bundle.scenario.fault_variant
    if probe != fault:
        raise ValueError("baseline and fault documents differ outside the target field")
    return baseline, fault


class ConfigurationAdapter(Protocol):
    def read_current(self) -> ConfigurationState: ...

    def restore_baseline(self) -> ConfigurationState: ...

    def restore_fault(self) -> ConfigurationState: ...


class InMemoryConfigurationAdapter:
    def __init__(
        self,
        *,
        baseline: ConfigurationState,
        fault: ConfigurationState,
        current: Literal["BASELINE", "FAULT"],
    ) -> None:
        self.baseline = baseline
        self.fault = fault
        self.current = current

    def read_current(self) -> ConfigurationState:
        return self.baseline if self.current == "BASELINE" else self.fault

    def restore_baseline(self) -> ConfigurationState:
        self.current = "BASELINE"
        return self.read_current()

    def restore_fault(self) -> ConfigurationState:
        self.current = "FAULT"
        return self.read_current()


def _local_json(
    url: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    timeout_seconds: float = 10,
) -> object:
    data = None if payload is None else canonical_json_bytes(payload).rstrip(b"\n")
    request = Request(
        url,
        method=method,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - exact loopback endpoints
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"flag control HTTP status is {response.status}")
        return json.loads(response.read().decode("utf-8"))


class SandboxFaultController:
    """Allow only the two frozen full flag documents and three-way readback."""

    def __init__(
        self,
        *,
        endpoints: LocalEndpoints,
        bundle: ConfigBundle,
        flag_file: Path,
        baseline_document: Mapping[str, object],
        fault_document: Mapping[str, object],
    ) -> None:
        self.endpoints = endpoints
        self.bundle = bundle
        self.flag_file = flag_file
        self.baseline_document = dict(baseline_document)
        self.fault_document = dict(fault_document)
        if canonical_sha256(self.baseline_document) != bundle.scenario.baseline_document_sha256:
            raise ValueError("controller baseline does not match the scenario")
        if canonical_sha256(self.fault_document) != bundle.scenario.fault_document_sha256:
            raise ValueError("controller fault does not match the scenario")

    def _document(self) -> Mapping[str, object]:
        if self.flag_file.is_symlink() or not self.flag_file.is_file():
            raise RuntimeError("private flag file is unavailable")
        value = json.loads(self.flag_file.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise RuntimeError("private flag file is malformed")
        return value

    def read_current(self) -> ConfigurationState:
        document = self._document()
        document_hash = canonical_sha256(document)
        expected_variant: Literal["off", "100%"]
        expected_value: Literal[0, 1]
        if document_hash == self.bundle.scenario.baseline_document_sha256:
            expected_variant = self.bundle.scenario.baseline_variant
            expected_value = self.bundle.scenario.baseline_value
        elif document_hash == self.bundle.scenario.fault_document_sha256:
            expected_variant = self.bundle.scenario.fault_variant
            expected_value = self.bundle.scenario.fault_value
        else:
            raise RuntimeError("private flag document is neither frozen state")
        readback = _local_json(f"{self.endpoints.flag_control}/read")
        if not isinstance(readback, Mapping) or readback.get("flags") != document.get("flags"):
            raise RuntimeError("flag UI readback differs from the private document")
        evaluation = _local_json(
            f"{self.endpoints.flag_evaluation}/ofrep/v1/evaluate/flags/{self.bundle.scenario.target_flag}",
            method="POST",
            payload={},
        )
        if not isinstance(evaluation, Mapping):
            raise RuntimeError("OFREP evaluation is malformed")
        observed_value = evaluation.get("value")
        observed_variant = evaluation.get("variant")
        if observed_value != expected_value or observed_variant != expected_variant:
            raise RuntimeError("OFREP state differs from frozen configuration")
        return ConfigurationState(
            variant=expected_variant,
            value=expected_value,
            document_sha256=document_hash,
        )

    def _apply(self, state: Literal["BASELINE", "FAULT"]) -> ConfigurationState:
        document = self.baseline_document if state == "BASELINE" else self.fault_document
        expected_hash = (
            self.bundle.scenario.baseline_document_sha256
            if state == "BASELINE"
            else self.bundle.scenario.fault_document_sha256
        )
        if canonical_sha256(document) != expected_hash:
            raise RuntimeError("controller document drifted before mutation")
        _local_json(
            f"{self.endpoints.flag_control}/write",
            method="POST",
            payload={"data": document},
        )
        deadline = time.monotonic() + 15
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                observed = self.read_current()
                if observed.document_sha256 == expected_hash:
                    return observed
            except Exception as error:  # polling preserves the final typed failure
                last_error = error
            time.sleep(0.25)
        raise RuntimeError("frozen flag mutation did not reach three-way agreement") from last_error

    def restore_baseline(self) -> ConfigurationState:
        return self._apply("BASELINE")

    def restore_fault(self) -> ConfigurationState:
        return self._apply("FAULT")

    def inject_fault(self) -> ConfigurationState:
        return self._apply("FAULT")


def evaluate_diagnosis_gate(
    diagnosis: DiagnosisResult, bundle: ConfigBundle
) -> DiagnosisGate:
    reasons: list[str] = []
    if diagnosis.terminal != "COMPLETED" or diagnosis.semantic_model_calls != 1:
        reasons.append("DIAGNOSIS_NOT_COMPLETED")
    if diagnosis.root_service != bundle.scenario.expected_root_service:
        reasons.append("ROOT_SERVICE_MISMATCH")
    if diagnosis.fault_class != bundle.scenario.expected_fault_class:
        reasons.append("FAULT_CLASS_MISMATCH")
    if not diagnosis.evidence_refs or len(set(diagnosis.evidence_refs)) != len(
        diagnosis.evidence_refs
    ):
        reasons.append("EVIDENCE_REFS_INVALID")
    legal_prefixes = {"metric:": "METRICS", "log:": "LOGS", "trace:": "TRACES"}
    sources: list[str] = []
    for reference in diagnosis.evidence_refs:
        matches = [source for prefix, source in legal_prefixes.items() if reference.startswith(prefix)]
        if len(matches) != 1:
            reasons.append("EVIDENCE_REFS_INVALID")
        else:
            sources.append(matches[0])
    inferred = set(sources)
    if not inferred:
        reasons.append("EVIDENCE_REFS_INVALID")
    if "METRICS" not in inferred or not inferred.intersection({"LOGS", "TRACES"}):
        reasons.append("EVIDENCE_SOURCE_COVERAGE_INSUFFICIENT")
    if set(diagnosis.evidence_source_types) != inferred:
        reasons.append("EVIDENCE_SOURCE_ACCOUNTING_MISMATCH")
    if diagnosis.specialist_calls or diagnosis.fusion_calls:
        reasons.append("UNAUTHORIZED_MODEL_CALL")
    unique = tuple(dict.fromkeys(reasons))
    return DiagnosisGate(passed=not unique, reason_codes=unique)


def build_plan(diagnosis: DiagnosisResult, bundle: ConfigBundle) -> LiveRemediationPlan:
    gate = evaluate_diagnosis_gate(diagnosis, bundle)
    if not gate.passed:
        raise ValueError("diagnosis gate did not admit a remediation plan")
    diagnosis_hash = canonical_sha256(diagnosis)
    template_hash = canonical_sha256(LiveRemediationPlan.template_payload(bundle))
    plan_id = "plan-" + hashlib.sha256(
        f"{template_hash}:{diagnosis_hash}".encode("utf-8")
    ).hexdigest()[:16]
    return LiveRemediationPlan(
        plan_id=plan_id,
        scenario_id=bundle.scenario.scenario_id,
        environment_id=bundle.environment.environment_id,
        sandbox_id=bundle.environment.sandbox_id,
        action=bundle.policy.action,
        target_service=bundle.scenario.target_service,
        configuration_key=bundle.scenario.target_configuration_key,
        baseline_ref="PRIVATE_FROZEN_BASELINE_DOCUMENT",
        baseline_sha256=bundle.scenario.baseline_document_sha256,
        desired_value_sha256=hashlib.sha256(b"0").hexdigest(),
        diagnosis_sha256=diagnosis_hash,
        atomic_actions=1,
    )


def approve_plan(
    request: ApprovalRequest, *, approver: str, now: datetime
) -> HumanApprovalRecord:
    name = approver.strip()
    if not name or now > request.expires_at:
        raise ValueError("human approval is blank or expired")
    return HumanApprovalRecord(
        mode="HUMAN",
        approver=name,
        approval_request_id=request.approval_request_id,
        request_sha256=canonical_sha256(request),
        plan_template_sha256=request.plan_template_sha256,
        scenario_id=request.scenario_id,
        environment_id=request.environment_id,
        sandbox_id=request.sandbox_id,
        action=request.action,
        target_service=request.target_service,
        configuration_key=request.configuration_key,
        baseline_sha256=request.baseline_sha256,
        approved_at=now,
        expires_at=request.expires_at,
    )


def evaluate_policy(
    *,
    plan: LiveRemediationPlan,
    diagnosis: DiagnosisResult,
    request: ApprovalRequest,
    approval: HumanApprovalRecord,
    bundle: ConfigBundle,
    docker_endpoint: str,
    owned_labels: Mapping[str, str],
    forward_mutations: int,
    now: datetime,
) -> PolicyDecision:
    reasons: list[str] = []
    try:
        require_local_docker_endpoint(docker_endpoint)
    except DockerBoundaryError:
        reasons.append("REMOTE_DOCKER_DENIED")
    try:
        require_owned_labels(owned_labels, bundle.environment)
    except ResourceOwnershipError:
        reasons.append("RESOURCE_OWNERSHIP_MISMATCH")
    if not evaluate_diagnosis_gate(diagnosis, bundle).passed:
        reasons.append("DIAGNOSIS_GATE_NOT_PASSED")
    template_hash = canonical_sha256(LiveRemediationPlan.template_payload(bundle))
    expected_plan = {
        "scenario_id": bundle.scenario.scenario_id,
        "environment_id": bundle.environment.environment_id,
        "sandbox_id": bundle.environment.sandbox_id,
        "action": bundle.policy.action,
        "target_service": bundle.scenario.target_service,
        "configuration_key": bundle.scenario.target_configuration_key,
        "baseline_sha256": bundle.scenario.baseline_document_sha256,
        "desired_value_sha256": hashlib.sha256(b"0").hexdigest(),
        "atomic_actions": 1,
    }
    for key, expected in expected_plan.items():
        if getattr(plan, key) != expected:
            reasons.append(f"PLAN_{key.upper()}_MISMATCH")
    if plan.diagnosis_sha256 != canonical_sha256(diagnosis):
        reasons.append("PLAN_DIAGNOSIS_HASH_MISMATCH")
    if forward_mutations != 0:
        reasons.append("FORWARD_MUTATION_LIMIT_REACHED")
    request_values = {
        "scenario_id": bundle.scenario.scenario_id,
        "environment_id": bundle.environment.environment_id,
        "sandbox_id": bundle.environment.sandbox_id,
        "action": bundle.policy.action,
        "target_service": bundle.scenario.target_service,
        "configuration_key": bundle.scenario.target_configuration_key,
        "baseline_sha256": bundle.scenario.baseline_document_sha256,
        "plan_template_sha256": template_hash,
        "max_forward_mutations": 1,
    }
    for key, expected in request_values.items():
        if getattr(request, key) != expected:
            reasons.append(f"REQUEST_{key.upper()}_MISMATCH")
    approval_values = {
        "approval_request_id": request.approval_request_id,
        "request_sha256": canonical_sha256(request),
        "plan_template_sha256": request.plan_template_sha256,
        "scenario_id": request.scenario_id,
        "environment_id": request.environment_id,
        "sandbox_id": request.sandbox_id,
        "action": request.action,
        "target_service": request.target_service,
        "configuration_key": request.configuration_key,
        "baseline_sha256": request.baseline_sha256,
        "expires_at": request.expires_at,
    }
    for key, expected in approval_values.items():
        if getattr(approval, key) != expected:
            reasons.append(f"APPROVAL_{key.upper()}_MISMATCH")
    if (
        approval.mode != "HUMAN"
        or not approval.approver.strip()
        or approval.approved_at < request.requested_at
        or approval.approved_at > request.expires_at
        or now > request.expires_at
    ):
        reasons.append("HUMAN_APPROVAL_INVALID_OR_EXPIRED")
    unique = tuple(dict.fromkeys(reasons))
    return PolicyDecision(
        verdict=PolicyVerdict.DENY if unique else PolicyVerdict.ALLOW,
        reason_codes=unique or ("ALLOWED",),
    )


class ForwardMutationCounter:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._count = 0
        if path is not None and path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("forward mutation journal is not a regular file")
            self._count = len([line for line in path.read_text().splitlines() if line])

    @property
    def count(self) -> int:
        return self._count

    def reserve(self, receipt_seed: str) -> Literal[1]:
        if self._count != 0:
            raise RuntimeError("second forward mutation is forbidden")
        self._count = 1
        if self.path is not None:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            with self.path.open("x", encoding="utf-8") as handle:
                handle.write(receipt_seed + "\n")
                handle.flush()
            self.path.chmod(0o600)
        return 1


class LocalSandboxRestrictedExecutor:
    def execute(
        self,
        *,
        plan: LiveRemediationPlan,
        policy: PolicyDecision,
        controller: ConfigurationAdapter,
        mutation_counter: ForwardMutationCounter,
    ) -> ExecutionReceipt:
        if policy.verdict is not PolicyVerdict.ALLOW:
            raise PermissionError("Policy Gate did not issue ALLOW")
        if mutation_counter.count != 0:
            raise RuntimeError("second forward mutation is forbidden")
        before = controller.read_current()
        if before.variant != "100%":
            raise RuntimeError("remediation pre-state is not the frozen fault")
        plan_hash = canonical_sha256(plan)
        number = mutation_counter.reserve(plan_hash)
        after = controller.restore_baseline()
        if after.variant != "off" or after.document_sha256 != plan.baseline_sha256:
            raise RuntimeError("restricted executor did not restore frozen baseline")
        receipt_id = "receipt-" + hashlib.sha256(
            f"{plan_hash}:{before.document_sha256}:{after.document_sha256}".encode()
        ).hexdigest()[:16]
        return ExecutionReceipt(
            receipt_id=receipt_id,
            plan_sha256=plan_hash,
            scenario_id=plan.scenario_id,
            environment_id=plan.environment_id,
            sandbox_id=plan.sandbox_id,
            action=plan.action,
            target_service=plan.target_service,
            configuration_key=plan.configuration_key,
            before_sha256=before.document_sha256,
            after_sha256=after.document_sha256,
            forward_mutation_number=number,
            applied_at=datetime.now(timezone.utc),
        )


def fault_impact_passed(
    baseline_windows: tuple[SLIWindow, ...],
    fault_windows: tuple[SLIWindow, ...],
    bundle: ConfigBundle,
) -> bool:
    if len(baseline_windows) != 2 or len(fault_windows) != 2:
        return False
    baseline = sum(item.error_rate for item in baseline_windows) / 2
    threshold = max(
        baseline + bundle.verification.fault_error_rate_absolute_increase,
        baseline * bundle.verification.fault_error_rate_multiplier,
    )
    return all(item.error_rate >= threshold for item in fault_windows)


class IndependentVerifier:
    def verify(
        self,
        *,
        plan: LiveRemediationPlan,
        receipt: ExecutionReceipt,
        current: ConfigurationState,
        baseline_windows: tuple[SLIWindow, ...],
        recovery_windows: tuple[SLIWindow, ...],
        services_healthy: bool,
        labels_exact: bool,
        bundle: ConfigBundle,
    ) -> VerificationResult:
        reasons: list[str] = []
        if receipt.plan_sha256 != canonical_sha256(plan):
            reasons.append("EXECUTION_RECEIPT_INVALID")
        if (
            receipt.target_service != plan.target_service
            or receipt.configuration_key != plan.configuration_key
            or receipt.forward_mutation_number != 1
        ):
            reasons.append("EXECUTION_RECEIPT_SCOPE_MISMATCH")
        if (
            current.variant != "off"
            or current.document_sha256 != bundle.scenario.baseline_document_sha256
            or receipt.after_sha256 != current.document_sha256
        ):
            reasons.append("BASELINE_CONFIGURATION_NOT_RESTORED")
        if not services_healthy:
            reasons.append("SERVICES_UNHEALTHY")
        if not labels_exact:
            reasons.append("OWNERSHIP_LABELS_MISMATCH")
        baseline_rate = (
            sum(item.error_rate for item in baseline_windows) / len(baseline_windows)
            if baseline_windows
            else 1.0
        )
        threshold = max(
            baseline_rate + bundle.verification.recovery_error_rate_absolute_increase,
            baseline_rate * bundle.verification.recovery_error_rate_multiplier,
        )
        passed_windows = sum(item.error_rate <= threshold for item in recovery_windows)
        if len(recovery_windows) != 2 or passed_windows != 2:
            reasons.append("RECOVERY_SLI_FAILED")
        unique = tuple(dict.fromkeys(reasons))
        return VerificationResult(
            passed=not unique,
            reason_codes=unique,
            receipt_sha256=canonical_sha256(receipt),
            configuration_sha256=current.document_sha256,
            services_healthy=services_healthy,
            labels_exact=labels_exact,
            recovery_windows_passed=passed_windows,
        )


def compensate_rollback(
    *,
    receipt: ExecutionReceipt,
    verification: VerificationResult,
    controller: ConfigurationAdapter,
) -> RollbackReceipt:
    if verification.passed:
        raise ValueError("rollback is forbidden after successful verification")
    current = controller.read_current()
    if current.document_sha256 != receipt.after_sha256:
        raise RuntimeError("rollback current state differs from execution receipt")
    restored = controller.restore_fault()
    return RollbackReceipt(
        executed=True,
        before_sha256=receipt.before_sha256,
        restored_sha256=restored.document_sha256,
        exact_hash_verified=restored.document_sha256 == receipt.before_sha256,
    )


__all__ = [
    "ConfigurationAdapter",
    "ForwardMutationCounter",
    "InMemoryConfigurationAdapter",
    "IndependentVerifier",
    "LocalSandboxRestrictedExecutor",
    "SandboxFaultController",
    "approve_plan",
    "build_flag_documents",
    "build_plan",
    "compensate_rollback",
    "evaluate_diagnosis_gate",
    "evaluate_policy",
    "fault_impact_passed",
]
