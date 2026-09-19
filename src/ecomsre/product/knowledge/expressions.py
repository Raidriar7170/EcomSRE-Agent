"""Level B: bounded derived numeric features over typed Resource observations.

No executable expressions, dynamic fields, interpolation, or data-dependent
threshold fitting. UNKNOWN is distinct from a negative match.
"""

import math
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.product.investigation.contracts import StrictModel


class Aggregate(StrictModel):
    field: Literal["cpu_percent", "memory_bytes"]
    operator: Literal["mean", "max", "delta", "rate"]

    @property
    def unit(self) -> str:
        unit = "PERCENT" if self.field == "cpu_percent" else "BYTES"
        return unit + ("_PER_SECOND" if self.operator == "rate" else "")


class DerivedExpression(StrictModel):
    schema_version: Literal["ecomsre.product.expression.v050"] = (
        "ecomsre.product.expression.v050"
    )
    source: Literal["RESOURCES"] = "RESOURCES"
    numerator: Aggregate
    denominator: Aggregate | None = None
    comparator: Literal["gt", "ge", "lt", "le"]
    threshold: float = Field(allow_inf_nan=False)
    threshold_unit: str = Field(min_length=1, max_length=40)
    threshold_provenance: str = Field(min_length=1, max_length=160)
    window_seconds: int = Field(ge=1, le=30)
    minimum_samples: int = Field(ge=2, le=10)
    evaluator_version: Literal["resource-aggregates-v1"] = "resource-aggregates-v1"

    @model_validator(mode="after")
    def units(self):
        unit = self.numerator.unit
        if self.denominator is not None:
            if unit != self.denominator.unit:
                raise ValueError("ratio operands have incompatible units")
            unit = "RATIO"
        if self.threshold_unit != unit:
            raise ValueError("comparison threshold unit differs")
        return self


class ExpressionOutcome(StrictModel):
    status: Literal["TRUE", "FALSE", "UNKNOWN"]
    value: float | None
    reason: str
    evidence_refs: tuple[str, ...] = ()


def evaluate_expression(
    expression: DerivedExpression, *, target: str, observations: list[dict[str, Any]]
) -> ExpressionOutcome:
    # A single exact observation owns both operands, so cross-source or cross-window
    # alignment cannot be guessed. Multiple differing measurements stay unknown.
    usable = []
    for observation in observations:
        if (
            observation.get("source") != expression.source
            or observation.get("status") != "SUCCESS_NONEMPTY"
            or observation.get("truncated")
            or target not in observation.get("covered_services", [])
        ):
            continue
        for record in observation.get("records", []):
            if record.get("service") == target and "samples" in record:
                usable.append((observation, record))
    if len(usable) != 1:
        return ExpressionOutcome(
            status="UNKNOWN", value=None, reason="MISSING_OR_AMBIGUOUS_OBSERVATION"
        )
    observation, record = usable[0]
    samples = record["samples"]
    if (
        record.get("sampling_window_seconds") != expression.window_seconds
        or not expression.minimum_samples <= len(samples) <= 10
    ):
        return ExpressionOutcome(
            status="UNKNOWN", value=None, reason="WINDOW_OR_SAMPLE_COUNT"
        )
    offsets = [s.get("offset_ms") for s in samples]
    if (
        any(type(v) is not int for v in offsets)
        or offsets[0] != 0
        or offsets[-1] != expression.window_seconds * 1000
        or any(a >= b for a, b in zip(offsets, offsets[1:]))
    ):
        return ExpressionOutcome(
            status="UNKNOWN", value=None, reason="INCOMPLETE_TIME_COVERAGE"
        )

    def aggregate(operand: Aggregate) -> float:
        values = [s.get(operand.field) for s in samples]
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values
        ):
            raise ValueError("INVALID_FIELD_VALUES")
        if operand.operator == "max":
            return float(max(values))
        if operand.operator == "mean":
            # Time-weighted trapezoidal mean, not an unweighted irregular sample mean.
            return (
                sum(
                    (a + b) / 2 * (tb - ta)
                    for a, b, ta, tb in zip(values, values[1:], offsets, offsets[1:])
                )
                / offsets[-1]
            )
        delta = values[-1] - values[0]
        return float(
            delta / expression.window_seconds if operand.operator == "rate" else delta
        )

    try:
        value = aggregate(expression.numerator)
        if expression.denominator is not None:
            divisor = aggregate(expression.denominator)
            if divisor == 0:
                raise ValueError("ZERO_DENOMINATOR")
            value /= divisor
        if not math.isfinite(value):
            raise ValueError("NONFINITE_RESULT")
    except ValueError as exc:
        return ExpressionOutcome(status="UNKNOWN", value=None, reason=str(exc))
    match = {
        "gt": value > expression.threshold,
        "ge": value >= expression.threshold,
        "lt": value < expression.threshold,
        "le": value <= expression.threshold,
    }[expression.comparator]
    return ExpressionOutcome(
        status="TRUE" if match else "FALSE",
        value=value,
        reason="EVALUATED",
        evidence_refs=(observation["evidence_ref"],),
    )
