"""Read-only field-level plan previews. Deliberately no executor dependency."""

from typing import Literal

from pydantic import Field

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.investigation.contracts import StrictModel


class PreviewField(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    observed_value: float
    baseline_value: float
    minimum: float
    maximum: float
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class PreviewContext(StrictModel):
    target: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    baseline_version: str = Field(min_length=1, max_length=120)
    fields: list[PreviewField] = Field(min_length=1, max_length=16)
    unrelated_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_evidence_refs: list[str] = Field(min_length=1, max_length=40)

    @property
    def state_sha256(self) -> str:
        return semantic_sha256_v22(self.model_dump(mode="json"))


class FieldChange(StrictModel):
    field: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    expected_current_value: float
    proposed_value: float
    evidence_refs: list[str] = Field(min_length=1, max_length=8)


class RemediationPlanProposal(StrictModel):
    target: str = Field(min_length=1, max_length=80)
    operation: Literal["RESTORE_FIELDS_TO_VERSIONED_BASELINE", "UNSUPPORTED"]
    changes: list[FieldChange] = Field(min_length=1, max_length=4)
    precondition_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    applicability: str = Field(min_length=1, max_length=500)
    impact_scope: Literal["NAMED_FIELDS_ONLY", "BROADER"]
    success_metrics: list[str] = Field(min_length=1, max_length=4)
    stop_conditions: list[str] = Field(min_length=1, max_length=4)
    compensation: str = Field(min_length=1, max_length=500)


class PlanPreview(StrictModel):
    status: Literal[
        "PREVIEW_ACCEPTABLE",
        "REQUIRES_APPROVAL",
        "UNSUPPORTED_OPERATION",
        "INSUFFICIENT_EVIDENCE",
    ]
    reasons: tuple[str, ...]
    plan: RemediationPlanProposal
    state_sha256: str
    unchanged_fields: tuple[str, ...]
    execution_authority: Literal["NONE"] = "NONE"
    external_writes: Literal[0] = 0


def validate_preview(
    plan: RemediationPlanProposal, context: PreviewContext
) -> PlanPreview:
    reasons = []
    status = "PREVIEW_ACCEPTABLE"
    fields = {f.name: f for f in context.fields}
    if len(fields) != len(context.fields):
        raise ValueError("ambiguous field catalog")
    if plan.precondition_state_sha256 != context.state_sha256:
        status = "REQUIRES_APPROVAL"
        reasons.append("STATE_DRIFT_REGENERATE_PLAN")
    if (
        plan.target != context.target
        or plan.operation != "RESTORE_FIELDS_TO_VERSIONED_BASELINE"
        or plan.impact_scope != "NAMED_FIELDS_ONLY"
    ):
        status = "UNSUPPORTED_OPERATION"
        reasons.append("TARGET_OPERATION_OR_SCOPE")
    names = [c.field for c in plan.changes]
    if len(set(names)) != len(names):
        status = "UNSUPPORTED_OPERATION"
        reasons.append("DUPLICATE_FIELD")
    for change in plan.changes:
        field = fields.get(change.field)
        if field is None or change.field in {
            "monitoring_enabled",
            "validation_enabled",
            "delete_messages",
        }:
            status = "UNSUPPORTED_OPERATION"
            reasons.append("FIELD_NOT_ALLOWED")
            continue
        if (
            change.proposed_value != field.baseline_value
            or not field.minimum <= change.proposed_value <= field.maximum
            or field.observed_value == field.baseline_value
        ):
            status = "UNSUPPORTED_OPERATION"
            reasons.append("PARAMETER_NOT_JUSTIFIED")
        if change.expected_current_value != field.observed_value:
            if status == "PREVIEW_ACCEPTABLE":
                status = "REQUIRES_APPROVAL"
            reasons.append("FIELD_STATE_DRIFT")
        if not set(change.evidence_refs).issubset(field.evidence_refs) or not set(
            change.evidence_refs
        ).issubset(context.observed_evidence_refs):
            if status == "PREVIEW_ACCEPTABLE":
                status = "INSUFFICIENT_EVIDENCE"
            reasons.append("EVIDENCE_NOT_BOUND_TO_FIELD")
    return PlanPreview.model_validate(
        {
            "status": status,
            "reasons": tuple(sorted(set(reasons))),
            "plan": plan,
            "state_sha256": context.state_sha256,
            "unchanged_fields": tuple(sorted(set(fields) - set(names))),
        }
    )


def propose_preview(*, provider, context: PreviewContext, key: str) -> PlanPreview:
    plan = provider.complete(
        key=key,
        task="preview_local_field_repair_no_execution",
        view={**context.model_dump(mode="json"), "state_sha256": context.state_sha256},
        schema=RemediationPlanProposal,
        reasoning="high",
    )
    return validate_preview(plan, context)
