"""Runtime-derived view of the current formal ontology for DTA v2.3.4."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.action_catalog import (
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_router_v222 import (
    SOURCE_PREDICATE_CAPABILITIES_V222,
)
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    RequirementServiceBindingV22,
    SupportClauseV22,
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.ontology_view import REGISTERED_MECHANISMS_V23


AUTHORITATIVE_DIRECT_STATE_PREDICATES_V234 = frozenset(
    {PredicateKindV22.RUNTIME_NOT_RUNNING}
)


class PredicateSourceBindingV234(DtaModelV22):
    predicate_kind: PredicateKindV22
    evidence_source: EvidenceSourceV22


class CoreMechanismExampleV234(DtaModelV22):
    mechanism: MechanismV22
    support_clauses: tuple[SupportClauseV22, ...]


class CoreOntologySourceFileBindingV234(DtaModelV22):
    binding_name: str
    repository_path: str


class CoreOntologySchemaSnapshotV234(DtaModelV22):
    schema_version: Literal["dta-v234.core-ontology-schema-snapshot.v1"]
    frozen_policy_sha256: str
    effective_policy_sha256: str
    tool_capability_registry_sha256: str
    core_mechanisms: tuple[MechanismV22, ...]
    core_predicate_kinds: tuple[PredicateKindV22, ...]
    frozen_core_support_clauses: tuple[SupportClauseV22, ...]
    core_support_clauses: tuple[SupportClauseV22, ...]
    service_binding_options: tuple[RequirementServiceBindingV22, ...]
    predicate_source_bindings: tuple[PredicateSourceBindingV234, ...]
    authoritative_single_predicate_allowlist: tuple[PredicateKindV22, ...]
    representative_examples: tuple[CoreMechanismExampleV234, ...]
    source_file_bindings: tuple[CoreOntologySourceFileBindingV234, ...]
    snapshot_sha256: str

    @model_validator(mode="after")
    def require_runtime_snapshot(self) -> "CoreOntologySchemaSnapshotV234":
        expected_payload = _snapshot_payload_v234()
        expected = CoreOntologySchemaSnapshotV234.model_construct(
            **expected_payload,
            snapshot_sha256="0" * 64,
        ).model_dump(mode="json", exclude={"snapshot_sha256"})
        actual = self.model_dump(mode="json", exclude={"snapshot_sha256"})
        if actual != expected:
            raise ValueError("core ontology snapshot differs from runtime objects")
        digest = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != digest:
            raise ValueError("core ontology snapshot digest differs")
        return self


def _predicate_source_bindings_v234() -> tuple[PredicateSourceBindingV234, ...]:
    by_kind: dict[PredicateKindV22, EvidenceSourceV22] = {}
    for source, kinds in SOURCE_PREDICATE_CAPABILITIES_V222.items():
        for kind in kinds:
            if kind in by_kind and by_kind[kind] is not source:
                raise ValueError("core predicate has multiple source bindings")
            by_kind[kind] = source
    if set(by_kind) != set(PredicateKindV22):
        raise ValueError("core predicate source bindings are incomplete")
    return tuple(
        PredicateSourceBindingV234(
            predicate_kind=kind,
            evidence_source=by_kind[kind],
        )
        for kind in sorted(by_kind, key=lambda item: item.value)
    )


def _source_file_bindings_v234() -> tuple[CoreOntologySourceFileBindingV234, ...]:
    values = (
        (
            "core-mechanisms-and-support-clauses",
            "src/ecomsre/dta_v2/v22/predicates.py",
        ),
        (
            "effective-support-policy",
            "src/ecomsre/dta_v2/v22/effective_policy_v222.py",
        ),
        (
            "predicate-extraction",
            "src/ecomsre/dta_v2/v22/predicates.py",
        ),
        (
            "salient-memory",
            "src/ecomsre/dta_v2/v22/memory.py",
        ),
        (
            "predicate-source-capabilities",
            "src/ecomsre/dta_v2/v22/gap_router_v222.py",
        ),
        (
            "read-action-capabilities",
            "src/ecomsre/dta_v2/v22/action_catalog.py",
        ),
        (
            "formal-ontology-tests",
            "tests/dta_v22/test_v22_memory_predicates_diagnosis.py",
        ),
    )
    return tuple(
        CoreOntologySourceFileBindingV234(
            binding_name=name,
            repository_path=path,
        )
        for name, path in values
    )


def _snapshot_payload_v234() -> dict[str, Any]:
    frozen = build_default_evidence_support_policy_v22()
    effective = build_effective_support_policy_v222()
    capability_registry = build_default_tool_capability_registry_v22()
    mechanisms = tuple(
        sorted(
            {clause.mechanism for clause in effective.clauses},
            key=lambda item: item.value,
        )
    )
    if mechanisms != REGISTERED_MECHANISMS_V23:
        raise ValueError("effective policy mechanisms differ from registered ontology")
    source_bindings = _predicate_source_bindings_v234()
    enabled_sources = {
        capability.source
        for capability in capability_registry.capabilities
        if capability.enabled
    }
    if {item.evidence_source for item in source_bindings} - enabled_sources:
        raise ValueError("core predicate source lacks an enabled read capability")
    source_by_kind = {
        binding.predicate_kind: binding.evidence_source
        for binding in source_bindings
    }
    runtime_singletons = {
        clause.requirements[0].predicate_kind
        for clause in effective.clauses
        if len(clause.requirements) == 1
        and source_by_kind[clause.requirements[0].predicate_kind]
        is EvidenceSourceV22.RUNTIME
    }
    if not AUTHORITATIVE_DIRECT_STATE_PREDICATES_V234.issubset(runtime_singletons):
        raise ValueError("authoritative direct-state predicate lacks a core singleton clause")
    direct_state_allowlist = tuple(
        sorted(
            AUTHORITATIVE_DIRECT_STATE_PREDICATES_V234,
            key=lambda item: item.value,
        )
    )
    examples = tuple(
        CoreMechanismExampleV234(
            mechanism=mechanism,
            support_clauses=tuple(
                clause
                for clause in effective.clauses
                if clause.mechanism is mechanism
            ),
        )
        for mechanism in mechanisms
    )
    return {
        "schema_version": "dta-v234.core-ontology-schema-snapshot.v1",
        "frozen_policy_sha256": frozen.policy_sha256,
        "effective_policy_sha256": effective.policy_sha256,
        "tool_capability_registry_sha256": capability_registry.registry_sha256,
        "core_mechanisms": mechanisms,
        "core_predicate_kinds": tuple(
            sorted(PredicateKindV22, key=lambda item: item.value)
        ),
        "frozen_core_support_clauses": frozen.clauses,
        "core_support_clauses": effective.clauses,
        "service_binding_options": tuple(
            sorted(RequirementServiceBindingV22, key=lambda item: item.value)
        ),
        "predicate_source_bindings": source_bindings,
        "authoritative_single_predicate_allowlist": direct_state_allowlist,
        "representative_examples": examples,
        "source_file_bindings": _source_file_bindings_v234(),
    }


def build_core_ontology_schema_snapshot_v234() -> CoreOntologySchemaSnapshotV234:
    payload = _snapshot_payload_v234()
    draft = CoreOntologySchemaSnapshotV234.model_construct(
        **payload,
        snapshot_sha256="0" * 64,
    )
    return CoreOntologySchemaSnapshotV234.model_validate(
        {
            **payload,
            "snapshot_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"snapshot_sha256"})
            ),
        }
    )


__all__ = (
    "CoreMechanismExampleV234",
    "CoreOntologySchemaSnapshotV234",
    "CoreOntologySourceFileBindingV234",
    "PredicateSourceBindingV234",
    "build_core_ontology_schema_snapshot_v234",
)
