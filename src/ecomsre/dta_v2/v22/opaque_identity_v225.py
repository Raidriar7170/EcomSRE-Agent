"""Mechanism-independent opaque identities for the DTA v2.2.5 study."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal

from pydantic import StringConstraints, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


OPAQUE_IDENTITY_SEED_V225 = "dta-v225-opaque-evaluation-v1"

OpaqueServiceIdV225 = Annotated[
    str, StringConstraints(strict=True, pattern=r"^svc-[0-9a-f]{10}$")
]
OpaqueOperationIdV225 = Annotated[
    str, StringConstraints(strict=True, pattern=r"^op-[0-9a-f]{10}$")
]
OpaqueChangeIdV225 = Annotated[
    str, StringConstraints(strict=True, pattern=r"^chg-[0-9a-f]{10}$")
]
OpaquePairIdV225 = Annotated[
    str, StringConstraints(strict=True, pattern=r"^pair-[0-9a-f]{10}$")
]


def _identity(seed: str, namespace: str, ordinal: int, prefix: str) -> str:
    if ordinal < 0:
        raise ValueError("opaque identity ordinal must be nonnegative")
    digest = sha256(f"{seed}:{namespace}:{ordinal:04d}".encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:10]}"


class OpaqueIdentityPlanV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.opaque-identity-plan.v1"]
    seed: Literal["dta-v225-opaque-evaluation-v1"]
    services: tuple[OpaqueServiceIdV225, ...]
    operations: tuple[OpaqueOperationIdV225, ...]
    changes: tuple[OpaqueChangeIdV225, ...]
    pairs: tuple[OpaquePairIdV225, ...]
    plan_sha256: str

    @model_validator(mode="after")
    def require_mechanism_independent_plan(self) -> "OpaqueIdentityPlanV225":
        for namespace, prefix, values in (
            ("service", "svc", self.services),
            ("operation", "op", self.operations),
            ("change", "chg", self.changes),
            ("pair", "pair", self.pairs),
        ):
            expected = tuple(
                _identity(self.seed, namespace, ordinal, prefix)
                for ordinal in range(len(values))
            )
            if values != expected or len(values) != len(set(values)):
                raise ValueError(f"opaque {namespace} identities are not neutral-ordinal derived")
        expected_digest = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected_digest:
            raise ValueError("opaque identity plan digest differs")
        return self


def generate_opaque_identity_plan_v225(
    *,
    seed: str = OPAQUE_IDENTITY_SEED_V225,
    service_count: int,
    operation_count: int,
    change_count: int,
    pair_count: int,
) -> OpaqueIdentityPlanV225:
    """Generate identity pools before any mechanism or terminal assignment exists."""

    if seed != OPAQUE_IDENTITY_SEED_V225:
        raise ValueError("opaque identity seed differs from the committed neutral seed")
    counts = (service_count, operation_count, change_count, pair_count)
    if any(type(item) is not int or not 0 <= item <= 256 for item in counts):
        raise ValueError("opaque identity pool counts are invalid")
    payload = {
        "schema_version": "dta-v22.5.opaque-identity-plan.v1",
        "seed": seed,
        "services": tuple(_identity(seed, "service", index, "svc") for index in range(service_count)),
        "operations": tuple(_identity(seed, "operation", index, "op") for index in range(operation_count)),
        "changes": tuple(_identity(seed, "change", index, "chg") for index in range(change_count)),
        "pairs": tuple(_identity(seed, "pair", index, "pair") for index in range(pair_count)),
    }
    return OpaqueIdentityPlanV225.model_validate(
        {**payload, "plan_sha256": semantic_sha256_v22(payload)}
    )


__all__ = (
    "OPAQUE_IDENTITY_SEED_V225",
    "OpaqueChangeIdV225",
    "OpaqueIdentityPlanV225",
    "OpaqueOperationIdV225",
    "OpaquePairIdV225",
    "OpaqueServiceIdV225",
    "generate_opaque_identity_plan_v225",
)
