"""Closed, version-specific terminal policies for the shared Invocation B runtime."""

from __future__ import annotations

from dataclasses import dataclass

from ecomsre_live_sandbox.contracts import canonical_sha256
from ecomsre_live_sandbox.e2e_diagnostics import DiagnosticFailureCode


_SUPPORTED_VERSIONS = frozenset({"v3", "v4", "v5", "v6"})
_COMMON_LATE_TERMINALS = frozenset(
    {
        "BLOCKED_PROVIDER_PREFLIGHT",
        "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
        "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
        "BLOCKED_POLICY_REJECTED",
        "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
        "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
        "BLOCKED_CLEANUP_INCOMPLETE",
    }
)


@dataclass(frozen=True, slots=True)
class InvocationBVerdictPolicy:
    version: str
    legal_terminals: frozenset[str]
    success: str
    provider_preflight_failed: str
    image_authority_mismatch: str
    compose_structure_identity_mismatch: str
    compose_up_failed: str
    service_health_timeout: str
    baseline_unavailable: str
    baseline_mismatch: str
    unclassified_runtime_failure: str
    cleanup_incomplete: str

    def terminal_for(self, failure_code: DiagnosticFailureCode | None) -> str:
        mapping = {
            DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH: (
                self.image_authority_mismatch
            ),
            DiagnosticFailureCode.COMPOSE_STRUCTURE_IDENTITY_MISMATCH: (
                self.compose_structure_identity_mismatch
            ),
            DiagnosticFailureCode.COMPOSE_UP_FAILED: self.compose_up_failed,
            DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT: (
                self.service_health_timeout
            ),
            DiagnosticFailureCode.SERVICE_EXITED_BEFORE_READY: (
                self.service_health_timeout
            ),
            DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE: (
                self.baseline_unavailable
            ),
            DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH: (
                self.baseline_mismatch
            ),
            DiagnosticFailureCode.UNCLASSIFIED_RUNTIME_FAILURE: (
                self.unclassified_runtime_failure
            ),
        }
        terminal = (
            self.unclassified_runtime_failure
            if failure_code is None
            else mapping.get(failure_code, self.unclassified_runtime_failure)
        )
        if terminal not in self.legal_terminals:
            raise ValueError("Invocation B verdict policy produced an illegal terminal")
        return terminal


def _policy(version: str) -> InvocationBVerdictPolicy:
    marker = version.upper()
    versioned = {
        "success": (
            "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_"
            f"{marker}_PASSED_READY_FOR_REVIEW"
        ),
        "image_authority_mismatch": (
            f"BLOCKED_E2E_{marker}_IMAGE_AUTHORITY_MISMATCH"
        ),
        "compose_structure_identity_mismatch": (
            f"BLOCKED_E2E_{marker}_COMPOSE_STRUCTURE_IDENTITY_MISMATCH"
        ),
        "compose_up_failed": f"BLOCKED_E2E_{marker}_COMPOSE_UP_FAILED",
        "service_health_timeout": (
            f"BLOCKED_E2E_{marker}_SERVICE_HEALTH_TIMEOUT"
        ),
        "baseline_unavailable": (
            f"BLOCKED_E2E_{marker}_BASELINE_CONFIGURATION_UNAVAILABLE"
        ),
        "baseline_mismatch": (
            f"BLOCKED_E2E_{marker}_BASELINE_CONFIGURATION_MISMATCH"
        ),
        "unclassified_runtime_failure": (
            f"BLOCKED_E2E_{marker}_UNCLASSIFIED_RUNTIME_FAILURE"
        ),
    }
    legal = frozenset({*versioned.values(), *_COMMON_LATE_TERMINALS})
    if version == "v6":
        legal = frozenset({*legal, "BLOCKED_PUBLIC_RESULT_VERIFICATION"})
    return InvocationBVerdictPolicy(
        version=version,
        legal_terminals=legal,
        success=versioned["success"],
        provider_preflight_failed="BLOCKED_PROVIDER_PREFLIGHT",
        image_authority_mismatch=versioned["image_authority_mismatch"],
        compose_structure_identity_mismatch=(
            versioned["compose_structure_identity_mismatch"]
        ),
        compose_up_failed=versioned["compose_up_failed"],
        service_health_timeout=versioned["service_health_timeout"],
        baseline_unavailable=versioned["baseline_unavailable"],
        baseline_mismatch=versioned["baseline_mismatch"],
        unclassified_runtime_failure=versioned["unclassified_runtime_failure"],
        cleanup_incomplete="BLOCKED_CLEANUP_INCOMPLETE",
    )


_POLICIES = {version: _policy(version) for version in sorted(_SUPPORTED_VERSIONS)}


def get_invocation_b_verdict_policy(version: str) -> InvocationBVerdictPolicy:
    try:
        return _POLICIES[version]
    except KeyError as error:
        raise ValueError(f"unsupported Invocation B version: {version}") from error


def invocation_b_verdict_policy_sha256(version: str) -> str:
    policy = get_invocation_b_verdict_policy(version)
    return canonical_sha256(
        {
            "schema_version": "live-e2e.invocation-b-verdict-policy.v1",
            "version": policy.version,
            "legal_terminals": sorted(policy.legal_terminals),
            "success": policy.success,
            "provider_preflight_failed": policy.provider_preflight_failed,
            "image_authority_mismatch": policy.image_authority_mismatch,
            "compose_structure_identity_mismatch": (
                policy.compose_structure_identity_mismatch
            ),
            "compose_up_failed": policy.compose_up_failed,
            "service_health_timeout": policy.service_health_timeout,
            "baseline_unavailable": policy.baseline_unavailable,
            "baseline_mismatch": policy.baseline_mismatch,
            "unclassified_runtime_failure": policy.unclassified_runtime_failure,
            "cleanup_incomplete": policy.cleanup_incomplete,
        }
    )


__all__ = [
    "InvocationBVerdictPolicy",
    "get_invocation_b_verdict_policy",
    "invocation_b_verdict_policy_sha256",
]
