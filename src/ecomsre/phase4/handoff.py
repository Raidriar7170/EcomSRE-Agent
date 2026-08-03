"""Typed Phase 4 boundary before the frozen Phase 3 remediation planner."""

from __future__ import annotations

from ecomsre.phase4.contracts import (
    DomainFaultMechanism,
    DomainRemediationDisposition,
    DomainRemediationOutcome,
)


def domain_remediation_disposition(
    *,
    confirmed_mechanism: DomainFaultMechanism | None,
) -> DomainRemediationDisposition:
    """Refuse to coerce a Domain mechanism into the Phase 3 action contract."""

    return DomainRemediationDisposition(
        schema_version="phase4.remediation-disposition.v1",
        outcome=(
            DomainRemediationOutcome.NO_SUPPORTED_REMEDIATION
            if confirmed_mechanism is not None
            else DomainRemediationOutcome.NO_ACTION
        ),
        remediation_action=None,
        live_mutation=False,
        remediation_backend="NONE",
    )
