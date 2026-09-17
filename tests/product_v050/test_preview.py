from ecomsre.product.remediation.planner import (
    PreviewContext,
    RemediationPlanProposal,
    validate_preview,
)


def context():
    return PreviewContext(
        target="payment",
        baseline_version="baseline-1",
        fields=[
            {
                "name": "timeout_ms",
                "observed_value": 1.0,
                "baseline_value": 100.0,
                "minimum": 10.0,
                "maximum": 1000.0,
                "evidence_refs": ["timeout-ref"],
            },
            {
                "name": "batch_size",
                "observed_value": 20.0,
                "baseline_value": 20.0,
                "minimum": 1.0,
                "maximum": 100.0,
                "evidence_refs": ["batch-ref"],
            },
        ],
        unrelated_configuration_sha256="1" * 64,
        observed_evidence_refs=["timeout-ref", "batch-ref"],
    )


def plan(**changes):
    return RemediationPlanProposal.model_validate(
        dict(
            target="payment",
            operation="RESTORE_FIELDS_TO_VERSIONED_BASELINE",
            changes=[
                {
                    "field": "timeout_ms",
                    "expected_current_value": 1.0,
                    "proposed_value": 100.0,
                    "evidence_refs": ["timeout-ref"],
                }
            ],
            precondition_state_sha256=context().state_sha256,
            applicability="Only with matching observed state",
            impact_scope="NAMED_FIELDS_ONLY",
            success_metrics=["request success"],
            stop_conditions=["state drift"],
            compensation="Reassess; reversal is not guaranteed.",
        )
        | changes
    )


def test_local_preview_preserves_unrelated_configuration():
    result = validate_preview(plan(), context())
    assert result.status == "PREVIEW_ACCEPTABLE"
    assert result.unchanged_fields == ("batch_size",)
    assert result.execution_authority == "NONE" and result.external_writes == 0


def test_state_drift_requires_regeneration():
    changed = context().model_copy(update={"unrelated_configuration_sha256": "2" * 64})
    assert validate_preview(plan(), changed).status == "REQUIRES_APPROVAL"


def test_extra_fields_fabricated_evidence_and_unsafe_scope():
    assert (
        validate_preview(plan(target="invented"), context()).status
        == "UNSUPPORTED_OPERATION"
    )
    assert (
        validate_preview(plan(impact_scope="BROADER"), context()).status
        == "UNSUPPORTED_OPERATION"
    )
    assert (
        validate_preview(
            plan(
                changes=[
                    {
                        "field": "monitoring_enabled",
                        "expected_current_value": 1.0,
                        "proposed_value": 0.0,
                        "evidence_refs": ["timeout-ref"],
                    }
                ]
            ),
            context(),
        ).status
        == "UNSUPPORTED_OPERATION"
    )
    assert (
        validate_preview(
            plan(
                changes=[
                    {
                        "field": "timeout_ms",
                        "expected_current_value": 1.0,
                        "proposed_value": 100.0,
                        "evidence_refs": ["invented"],
                    }
                ]
            ),
            context(),
        ).status
        == "INSUFFICIENT_EVIDENCE"
    )
    assert (
        validate_preview(
            plan(
                changes=[
                    {
                        "field": "batch_size",
                        "expected_current_value": 20.0,
                        "proposed_value": 20.0,
                        "evidence_refs": ["batch-ref"],
                    }
                ]
            ),
            context(),
        ).status
        == "UNSUPPORTED_OPERATION"
    )
