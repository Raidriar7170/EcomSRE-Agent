"""Additive Product job adapter; the historical v1 schema stays byte-frozen."""

from enum import Enum

from ecomsre.product.jobs.contracts import ProductJobRecordV1, ProductJobTypeV1


class InvestigationJobTypeV050(str, Enum):
    INVESTIGATION = "INVESTIGATION"
    KNOWLEDGE_PROPOSAL = "KNOWLEDGE_PROPOSAL"


class ProductJobRecordV050(ProductJobRecordV1):
    # The successor widens only this field; legacy values retain their enum identity.
    # It is deliberately used at the new repository/API boundary, not written back
    # into the frozen historical model definition.
    job_type: ProductJobTypeV1 | InvestigationJobTypeV050  # type: ignore[assignment]
