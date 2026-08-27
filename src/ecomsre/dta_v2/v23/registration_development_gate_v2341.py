"""Development-only gate over the five blocked predecessor Provider roles."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.evaluation_v234 import (
    RegistrationTaskClassV234,
    _prepare_authorized_task_v234,
    load_core_schema_views_v234,
    load_registration_tasks_v234,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    RegistrationAliasProviderV2341,
    build_registration_alias_provider_request_v2341,
    build_registration_alias_source_request_v2341,
)
from ecomsre.dta_v2.v23.registration_assembler_v2341 import (
    RegistrationValidationContextV2341,
    assemble_formal_registration_draft_v2341,
    validate_registration_draft_in_context_v2341,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    CatalogFeasibilityStatusV2341,
    build_registration_option_catalog_v2341,
    evaluate_catalog_feasibility_v2341,
)


_FAILED_PROVIDER_TASK_IDS_V2341 = (
    "rt-001",
    "rt-003",
    "rt-011",
    "rt-012",
    "rt-014",
)


class PredecessorDevelopmentTaskResultV2341(DtaModelV22):
    task_id: str
    role: str
    catalog_feasibility_pass: Literal[True]
    provider_response_field_count: Literal[6]
    provider_calls: Literal[1]
    protocol_repairs: Literal[0]
    selection_protocol_valid: Literal[True]
    aliases_resolved: Literal[True]
    draft_assembled: Literal[True]
    contextual_validation_pass: Literal[True]
    production_collision_safe: StrictBool
    engineering_gap_selected: StrictBool
    canonical_order_failures: Literal[0]
    action_authority: Literal["NONE"]


class PredecessorDevelopmentGateV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.predecessor-development-gate.v1"]
    changed_iteration_count: int = Field(ge=1, le=3)
    failed_provider_role_count: Literal[5]
    protocol_valid_role_count: Literal[5]
    tasks: tuple[PredecessorDevelopmentTaskResultV2341, ...]
    rt_011_canonical_order_failure_eliminated: Literal[True]
    hidden_known_reconstruction_pass_count: Literal[2]
    declarative_ready_valid_count: Literal[2]
    engineering_required_gap_count: Literal[1]
    duplicate_control_provider_calls: Literal[0]
    insufficient_control_provider_calls: Literal[0]
    action_authority_violations: Literal[0]
    terminal: Literal["DTA_V2341_PREDECESSOR_DEVELOPMENT_GATE_PASS"]
    gate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_gate(self) -> "PredecessorDevelopmentGateV2341":
        if tuple(item.task_id for item in self.tasks) != _FAILED_PROVIDER_TASK_IDS_V2341:
            raise ValueError("predecessor development task IDs differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"gate_sha256"})
        )
        if self.gate_sha256 != expected:
            raise ValueError("predecessor development gate digest differs")
        return self


def _fixture_alias_response_v2341(
    *,
    task_id: str,
    role: RegistrationTaskClassV234,
    mechanism_concept: str,
    clause_aliases: tuple[str, ...],
    confusable_aliases: tuple[str, ...],
    gap_aliases: tuple[str, ...],
) -> str:
    engineering = bool(gap_aliases)
    selected_clauses = list(clause_aliases[:1])
    selected_confusables = list(confusable_aliases[:2])
    if task_id == "rt-011":
        selected_clauses = [*reversed(selected_clauses), *selected_clauses]
        selected_confusables = [
            *reversed(selected_confusables),
            *selected_confusables,
        ]
    payload: dict[str, Any] = {
        "disposition_alias": "D01" if engineering else "D00",
        "mechanism_concept": mechanism_concept,
        "clause_aliases": [] if engineering else selected_clauses,
        "confusable_aliases": selected_confusables,
        "engineering_gap_aliases": list(gap_aliases),
        "semantic_rationale": (
            "Accepted evidence requires one bounded extraction capability."
            if engineering
            else "Accepted evidence supports one bounded mechanism."
        ),
    }
    if role not in {
        RegistrationTaskClassV234.HIDDEN_KNOWN,
        RegistrationTaskClassV234.UNREGISTERED,
    }:
        raise ValueError("development fixture role is not Provider-called")
    return json.dumps(payload)


def run_predecessor_development_gate_v2341(
    *, repository_root: Path
) -> PredecessorDevelopmentGateV2341:
    tasks = load_registration_tasks_v234(
        repository_root / "config/dta-v234/evaluation/tasks.json"
    )
    views = load_core_schema_views_v234(
        repository_root / "config/dta-v234/evaluation/core-schema-snapshot.json"
    )
    results: list[PredecessorDevelopmentTaskResultV2341] = []
    with tempfile.TemporaryDirectory(prefix="dta-v2341-development-gate-") as raw:
        local_root = Path(raw)
        for task_id in _FAILED_PROVIDER_TASK_IDS_V2341:
            task = tasks.require(task_id)
            item, shadow, authorization = _prepare_authorized_task_v234(
                repository_root=repository_root,
                task=task,
                local_root=local_root / task_id,
            )
            source_request = build_registration_alias_source_request_v2341(
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                ontology_view=views.require(task_id),
            )
            catalog = build_registration_option_catalog_v2341(request=source_request)
            feasibility = evaluate_catalog_feasibility_v2341(catalog=catalog)
            if feasibility.status is not CatalogFeasibilityStatusV2341.PASS:
                raise ValueError(f"{task_id} lacks Runtime catalog coverage")
            provider_request = build_registration_alias_provider_request_v2341(
                source_request=source_request,
                catalog=catalog,
            )
            raw_response = _fixture_alias_response_v2341(
                task_id=task_id,
                role=task.task_class,
                mechanism_concept=task.provisional_mechanism_label,
                clause_aliases=tuple(
                    item.clause_alias for item in catalog.clause_options
                ),
                confusable_aliases=tuple(
                    item.confusable_alias for item in catalog.confusable_options
                ),
                gap_aliases=tuple(
                    item.engineering_gap_alias
                    for item in catalog.engineering_gap_options
                ),
            )

            def fixture_transport(_body: str) -> str:
                return raw_response

            provider_result = RegistrationAliasProviderV2341(
                transport=fixture_transport
            ).select(request=provider_request, catalog=catalog)
            hidden_known = task.task_class is RegistrationTaskClassV234.HIDDEN_KNOWN
            context = (
                RegistrationValidationContextV2341.HIDDEN_KNOWN_RECONSTRUCTION
                if hidden_known
                else RegistrationValidationContextV2341.PRODUCTION_REGISTRATION
            )
            assembly = assemble_formal_registration_draft_v2341(
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                catalog=catalog,
                provider_result=provider_result,
                validation_context=context,
            )
            validation = validate_registration_draft_in_context_v2341(
                draft=assembly.formal_draft,
                authorization_context=authorization,
                shadow=shadow,
                accepted_reports=(item,),
                context=context,
                promoted_mechanism_slugs=(),
                shadow_mechanism_slugs=(),
            )
            if len(type(provider_result.selection).model_fields) != 6:
                raise ValueError("development selection field count differs")
            if (
                provider_result.trace.provider_calls != 1
                or provider_result.trace.protocol_repairs != 0
            ):
                raise ValueError("development Provider trace differs")
            if not validation.context_pass:
                raise ValueError("development contextual validation failed")
            results.append(
                PredecessorDevelopmentTaskResultV2341(
                    task_id=task_id,
                    role=(
                        "ENGINEERING_REQUIRED"
                        if task_id == "rt-014"
                        else task.task_class.value
                    ),
                    catalog_feasibility_pass=True,
                    provider_response_field_count=6,
                    provider_calls=1,
                    protocol_repairs=0,
                    selection_protocol_valid=True,
                    aliases_resolved=True,
                    draft_assembled=True,
                    contextual_validation_pass=True,
                    production_collision_safe=(
                        hidden_known and bool(validation.collision_evidence_codes)
                    ),
                    engineering_gap_selected=bool(
                        provider_result.selection.engineering_gap_aliases
                    ),
                    canonical_order_failures=assembly.canonical_order_failures,
                    action_authority=assembly.action_authority,
                )
            )
    hidden_results = tuple(
        item for item in results if item.role == RegistrationTaskClassV234.HIDDEN_KNOWN.value
    )
    declarative_results = tuple(
        item for item in results if item.task_id in {"rt-011", "rt-012"}
    )
    engineering_results = tuple(
        item for item in results if item.role == "ENGINEERING_REQUIRED"
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.predecessor-development-gate.v1",
        "changed_iteration_count": 1,
        "failed_provider_role_count": 5,
        "protocol_valid_role_count": sum(
            item.selection_protocol_valid for item in results
        ),
        "tasks": tuple(results),
        "rt_011_canonical_order_failure_eliminated": next(
            item for item in results if item.task_id == "rt-011"
        ).canonical_order_failures
        == 0,
        "hidden_known_reconstruction_pass_count": sum(
            item.contextual_validation_pass and item.production_collision_safe
            for item in hidden_results
        ),
        "declarative_ready_valid_count": sum(
            item.contextual_validation_pass for item in declarative_results
        ),
        "engineering_required_gap_count": sum(
            item.engineering_gap_selected for item in engineering_results
        ),
        "duplicate_control_provider_calls": int(
            tasks.require("rt-015").provider_call_expected
        ),
        "insufficient_control_provider_calls": int(
            tasks.require("rt-016").provider_call_expected
        ),
        "action_authority_violations": sum(
            item.action_authority != "NONE" for item in results
        ),
        "terminal": "DTA_V2341_PREDECESSOR_DEVELOPMENT_GATE_PASS",
    }
    draft = PredecessorDevelopmentGateV2341.model_construct(
        **payload, gate_sha256="0" * 64
    )
    return PredecessorDevelopmentGateV2341.model_validate(
        {
            **payload,
            "gate_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"gate_sha256"})
            ),
        }
    )


__all__ = (
    "PredecessorDevelopmentGateV2341",
    "PredecessorDevelopmentTaskResultV2341",
    "run_predecessor_development_gate_v2341",
)
