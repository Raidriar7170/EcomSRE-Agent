"""Diagnosis-to-Action v2.1 successor namespace."""

from ecomsre.dta_v2.v21.contracts import (
    ActionProposalV21,
    CandidateSetV21,
    DtaDiagnosisV21,
    FaultDomainV21,
    FaultMechanismV21,
    ResolvedDiagnosisEvidenceViewV21,
    RunbookIdV21,
    ScenarioSpecV21,
)

SCHEMA_PREFIX = "dta-v21."
PUBLIC_RESULT_PREFIX = "dta-v21-"

__all__ = (
    "ActionProposalV21",
    "CandidateSetV21",
    "DtaDiagnosisV21",
    "FaultDomainV21",
    "FaultMechanismV21",
    "PUBLIC_RESULT_PREFIX",
    "ResolvedDiagnosisEvidenceViewV21",
    "RunbookIdV21",
    "SCHEMA_PREFIX",
    "ScenarioSpecV21",
)
