"""Frozen evaluator-only case strata and treatment-independent denominators."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class EvaluatorStrataV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.evaluator-strata.v1"]
    resource_ambiguity_incidents: tuple[str, ...]
    resource_normal_controls: tuple[str, ...]
    abstention_controls: tuple[str, ...]
    configuration_incidents: tuple[str, ...]
    service_unavailable_incidents: tuple[str, ...]
    dependency_incidents: tuple[str, ...]
    cpu_incidents: tuple[str, ...]
    memory_incidents: tuple[str, ...]
    strata_sha256: str

    @classmethod
    def build(cls, **values: tuple[str, ...]) -> "EvaluatorStrataV225":
        payload = {
            "schema_version": "dta-v22.5.evaluator-strata.v1",
            **{name: tuple(items) for name, items in values.items()},
        }
        return cls.model_validate(
            {**payload, "strata_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_fixed_strata(self) -> "EvaluatorStrataV225":
        fields = (
            self.resource_ambiguity_incidents,
            self.resource_normal_controls,
            self.abstention_controls,
            self.configuration_incidents,
            self.service_unavailable_incidents,
            self.dependency_incidents,
            self.cpu_incidents,
            self.memory_incidents,
        )
        if any(items != tuple(sorted(set(items))) for items in fields):
            raise ValueError("evaluator strata case IDs are not canonical")
        terminal_strata = fields[:6]
        flattened = tuple(item for items in terminal_strata for item in items)
        if len(flattened) != len(set(flattened)):
            raise ValueError("a case belongs to multiple terminal strata")
        if len(flattened) != 16:
            raise ValueError("evaluator strata must bind exactly 16 cases")
        if set((*self.cpu_incidents, *self.memory_incidents)) != set(
            self.resource_ambiguity_incidents
        ) or set(self.cpu_incidents).intersection(self.memory_incidents):
            raise ValueError("CPU and memory strata do not partition resource incidents")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"strata_sha256"})
        )
        if self.strata_sha256 != expected:
            raise ValueError("evaluator strata digest differs")
        return self

    @property
    def all_case_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *self.resource_ambiguity_incidents,
                    *self.resource_normal_controls,
                    *self.abstention_controls,
                    *self.configuration_incidents,
                    *self.service_unavailable_incidents,
                    *self.dependency_incidents,
                }
            )
        )

    @property
    def resource_ambiguity_denominator(self) -> int:
        return len(self.resource_ambiguity_incidents)

    @property
    def resource_case_ids(self) -> tuple[str, ...]:
        return tuple(sorted((*self.resource_ambiguity_incidents, *self.resource_normal_controls)))

    @property
    def resource_case_denominator(self) -> int:
        return len(self.resource_case_ids)


__all__ = ("EvaluatorStrataV225",)
