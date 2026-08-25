"""Deterministic 48-arm runtime-totality preflight for DTA v2.3.2."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.anomaly_interpretation_v232 import (
    DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232,
)
from ecomsre.dta_v2.v23.evaluation_data_v232 import (
    EvaluationCaseSetV232,
    EvaluationOntologyViewSetV232,
)
from ecomsre.dta_v2.v23.evaluation_v232 import (
    ArmRuntimeTraceV232,
    EvaluationPolicyV232,
    run_evaluation_policy_with_trace_v232,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23


class RuntimeTotalityPreflightV232(DtaModelV22):
    schema_version: Literal["dta-v232.runtime-totality-preflight.v1"]
    case_count: Literal[24]
    arm_run_count: Literal[48]
    valid_terminal_or_boundary_count: Literal[48]
    registry_sha256: str
    registered_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    encountered_anomaly_kinds: tuple[GenericAnomalyKindV23, ...]
    conflict_types: tuple[str, ...]
    final_pre_provider_states: tuple[str, ...]
    traces: tuple[ArmRuntimeTraceV232, ...] = Field(min_length=48, max_length=48)
    runtime_exceptions: Literal[0]
    keyerrors: Literal[0]
    unmapped_anomaly_kinds: Literal[0]
    schema_failures: Literal[0]
    provider_calls: Literal[0]
    truth_access_before_both_arms: Literal[0]
    action_authority_violations: Literal[0]
    status: Literal["DTA_V232_RUNTIME_TOTALITY_PREFLIGHT_PASS"]
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_preflight(self) -> "RuntimeTotalityPreflightV232":
        expected_kinds = tuple(
            sorted(GenericAnomalyKindV23, key=lambda item: item.value)
        )
        if self.registered_anomaly_kinds != expected_kinds:
            raise ValueError("v2.3.2 preflight registry is not enum-total")
        trace_keys = tuple((item.case_id, item.policy.value) for item in self.traces)
        expected_trace_keys = tuple(
            (f"vx-{ordinal:03d}", policy.value)
            for ordinal in range(201, 225)
            for policy in EvaluationPolicyV232
        )
        if trace_keys != expected_trace_keys:
            raise ValueError("v2.3.2 preflight schedule differs")
        if any(
            item.registry_sha256 != self.registry_sha256
            or item.runtime_exception_count
            or item.keyerror_count
            or item.unmapped_anomaly_count
            or item.schema_failure_count
            or item.provider_calls
            for item in self.traces
        ):
            raise ValueError("v2.3.2 preflight contains a failed arm")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("v2.3.2 preflight digest differs")
        return self


def build_runtime_totality_preflight_v232(
    *,
    repository_root: Path,
    cases: EvaluationCaseSetV232,
    views: EvaluationOntologyViewSetV232,
) -> RuntimeTotalityPreflightV232:
    traces: list[ArmRuntimeTraceV232] = []
    for spec in cases.cases:
        view = views.require(spec.case_id)
        for policy in EvaluationPolicyV232:
            run, trace = run_evaluation_policy_with_trace_v232(
                repository_root=repository_root,
                spec=spec,
                view_spec=view,
                policy=policy,
            )
            if (
                not run.final_disposition
                and not trace.provider_selection_boundary
            ):
                raise ValueError(
                    f"v2.3.2 arm lacks terminal or Provider boundary: "
                    f"{spec.case_id}/{policy.value}"
                )
            if run.action_authority_violations != 0:
                raise ValueError("v2.3.2 preflight observed action authority")
            traces.append(trace)
    encountered = tuple(
        sorted(
            {
                kind
                for trace in traces
                for kind in trace.encountered_anomaly_kinds
            },
            key=lambda item: item.value,
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v232.runtime-totality-preflight.v1",
        "case_count": 24,
        "arm_run_count": 48,
        "valid_terminal_or_boundary_count": 48,
        "registry_sha256": (
            DEFAULT_ANOMALY_INTERPRETATION_REGISTRY_V232.registry_sha256
        ),
        "registered_anomaly_kinds": tuple(
            sorted(GenericAnomalyKindV23, key=lambda item: item.value)
        ),
        "encountered_anomaly_kinds": encountered,
        "conflict_types": tuple(
            sorted({value for trace in traces for value in trace.conflict_types})
        ),
        "final_pre_provider_states": tuple(
            sorted({trace.final_pre_provider_state for trace in traces})
        ),
        "traces": tuple(traces),
        "runtime_exceptions": 0,
        "keyerrors": 0,
        "unmapped_anomaly_kinds": 0,
        "schema_failures": 0,
        "provider_calls": 0,
        "truth_access_before_both_arms": 0,
        "action_authority_violations": 0,
        "status": "DTA_V232_RUNTIME_TOTALITY_PREFLIGHT_PASS",
    }
    draft = RuntimeTotalityPreflightV232.model_construct(
        **payload,
        preflight_sha256="0" * 64,
    )
    return RuntimeTotalityPreflightV232.model_validate(
        {
            **payload,
            "preflight_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"preflight_sha256"})
            ),
        }
    )


__all__ = (
    "RuntimeTotalityPreflightV232",
    "build_runtime_totality_preflight_v232",
)
