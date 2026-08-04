from __future__ import annotations

from ecomsre.phase1.contracts import FaultMechanism
from ecomsre.phase3.contracts import ActionType
from ecomsre.phase4.contracts import (
    DomainFaultMechanism,
    DomainRemediationOutcome,
)
from ecomsre.phase4.handoff import domain_remediation_disposition


def test_phase3_action_allowlist_remains_exactly_frozen() -> None:
    assert tuple(ActionType) == (ActionType.RESTORE_FROZEN_SERVICE_CONFIGURATION,)
    assert tuple(FaultMechanism) == (
        FaultMechanism.RUNTIME_CONFIGURATION_FAILURE,
        FaultMechanism.REQUEST_PROCESSING_FAILURE,
        FaultMechanism.CACHE_BACKEND_TIMEOUT,
    )


def test_all_domain_mechanisms_have_no_supported_phase3_action() -> None:
    for mechanism in DomainFaultMechanism:
        disposition = domain_remediation_disposition(confirmed_mechanism=mechanism)
        assert disposition.outcome is DomainRemediationOutcome.NO_SUPPORTED_REMEDIATION
        assert disposition.remediation_action is None
        assert disposition.live_mutation is False
        assert disposition.remediation_backend == "NONE"


def test_unconfirmed_domain_result_produces_no_action() -> None:
    disposition = domain_remediation_disposition(confirmed_mechanism=None)
    assert disposition.outcome is DomainRemediationOutcome.NO_ACTION
    assert disposition.remediation_action is None
