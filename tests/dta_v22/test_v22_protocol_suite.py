from __future__ import annotations

import pytest

from ecomsre.dta_v2.v22.controller_modes import (
    ProviderProbeStatusV22,
    probe_provider_output_mode_v22,
)
from ecomsre.dta_v2.v22.protocol_suite import (
    ProtocolCapabilitySuiteReportV22,
    ProtocolSuiteTerminalV22,
    SyntheticTransitionCategoryV22,
    run_local_protocol_capability_suite_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


def _probe():
    return probe_provider_output_mode_v22(
        probe=lambda _model, _mode, _schema: ProviderProbeStatusV22.SUPPORTED
    )


def test_protocol_capability_suite_covers_fifty_transitions_and_meets_gate() -> None:
    report = run_local_protocol_capability_suite_v22(provider_probe=_probe())
    assert report.transition_count == 50
    assert report.first_pass_accepted_count == 48
    assert report.post_correction_accepted_count == 50
    assert report.first_pass_protocol_acceptance == 0.96
    assert report.post_correction_protocol_acceptance == 1.0
    assert report.correction_count == 2
    assert report.correction_rate == 0.04
    assert report.invalid_dispatches == 0
    assert {item.category for item in report.transitions} == set(
        SyntheticTransitionCategoryV22
    )
    assert report.terminal is ProtocolSuiteTerminalV22.LOCAL_HARNESS_PASS
    assert report.provider_calls == 0
    assert report.provider_gate_eligible is False


def test_protocol_report_rejects_rehashed_entry_omission_and_fake_provider_gate() -> None:
    report = run_local_protocol_capability_suite_v22(provider_probe=_probe())
    forged_draft = report.model_copy(
        update={
            "transitions": report.transitions[:-1],
            "transition_count": 49,
        }
    )
    with pytest.raises(ValueError, match="canonical transition matrix"):
        ProtocolCapabilitySuiteReportV22.model_validate(
            forged_draft.model_copy(
                update={
                    "report_sha256": semantic_sha256_v22(
                        forged_draft.model_dump(
                            mode="json",
                            exclude={"report_sha256"},
                        )
                    )
                }
            ).model_dump(mode="python")
        )

    with pytest.raises(ValueError, match="provider_gate_eligible"):
        ProtocolCapabilitySuiteReportV22.model_validate(
            report.model_copy(
                update={
                    "provider_gate_eligible": True,
                }
            ).model_dump(mode="python")
        )


def test_protocol_gate_thresholds_are_machine_enforced() -> None:
    report = run_local_protocol_capability_suite_v22(provider_probe=_probe())
    bad_transitions = []
    for transition in report.transitions[:2]:
        bad_transition = transition.model_copy(
            update={
                "first_pass_accepted": False,
                "post_correction_accepted": False,
            }
        )
        bad_transitions.append(
            type(bad_transition).model_validate(
                bad_transition.model_copy(
                    update={
                        "transition_sha256": semantic_sha256_v22(
                            bad_transition.model_dump(
                                mode="json",
                                exclude={"transition_sha256"},
                            )
                        )
                    }
                ).model_dump(mode="python")
            )
        )
    transitions = (*bad_transitions, *report.transitions[2:])
    forged_draft = report.model_copy(
        update={
            "transitions": transitions,
            "first_pass_accepted_count": 46,
            "first_pass_protocol_acceptance": 0.92,
            "post_correction_accepted_count": 48,
            "post_correction_protocol_acceptance": 0.96,
        }
    )
    with pytest.raises(ValueError, match="gate metrics"):
        ProtocolCapabilitySuiteReportV22.model_validate(
            forged_draft.model_copy(
                update={
                    "report_sha256": semantic_sha256_v22(
                        forged_draft.model_dump(
                            mode="json",
                            exclude={"report_sha256"},
                        )
                    )
                }
            ).model_dump(mode="python")
        )
