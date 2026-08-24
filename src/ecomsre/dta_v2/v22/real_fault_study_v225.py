"""Frozen configuration and truth-late execution for the real-fault study."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_bundle_arm_v225 import (
    run_current_runtime_bundle_v225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultAliasMapV1,
    RealFaultOpaqueCaptureV1,
)
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v225 import (
    RealFaultArmRun,
    RealFaultCaseTruthV1,
    RealFaultScheduleEntry,
    RealFaultStudyArm,
    RealFaultStudyExecutionV1,
    build_real_fault_schedule_v225,
    build_real_fault_study_execution_v225,
)
from ecomsre.dta_v2.v22.real_fault_flat_arm_v225 import (
    FlatComparisonProviderV225,
    run_v2_style_flat_adaptive_v225,
)
from ecomsre.dta_v2.v22.selection_provider_v223 import SelectionProviderProtocolV223


class RealFaultAliasMapSetV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.alias-map-set.v1"]
    maps: tuple[RealFaultAliasMapV1, RealFaultAliasMapV1]
    set_sha256: str

    @model_validator(mode="after")
    def require_set(self) -> RealFaultAliasMapSetV1:
        if tuple(item.map_name for item in self.maps) != ("MAP_A", "MAP_B"):
            raise ValueError("real-fault alias-map order differs")
        aliases = {tuple(item.alias for item in value.bindings) for value in self.maps}
        if len(aliases) != 1:
            raise ValueError("real-fault alias maps do not share identities")
        first = {item.alias: item.physical_service for item in self.maps[0].bindings}
        second = {item.alias: item.physical_service for item in self.maps[1].bindings}
        if any(first[key] == second[key] for key in first):
            raise ValueError("real-fault alias maps are not exact swaps")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"set_sha256"})
        )
        if self.set_sha256 != expected:
            raise ValueError("real-fault alias-map set digest differs")
        return self


class RealFaultPublicAliasMapSetV1(DtaModelV22):
    """Public counterfactual declaration without private physical bindings."""

    schema_version: Literal["dta-v225-real-fault.public-alias-map-set.v1"]
    aliases: tuple[str, str]
    map_names: tuple[Literal["MAP_A"], Literal["MAP_B"]]
    exact_two_way_swap: Literal[True]
    public_set_sha256: str

    @model_validator(mode="after")
    def require_public_set(self) -> RealFaultPublicAliasMapSetV1:
        if self.aliases != tuple(sorted(set(self.aliases))):
            raise ValueError("public alias set is not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"public_set_sha256"})
        )
        if self.public_set_sha256 != expected:
            raise ValueError("public alias-set digest differs")
        return self


class RealFaultCaseBindingV1(DtaModelV22):
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    capture_path: str = Field(
        pattern=r"^config/dta-v225-real-fault/captures/(?:fault|baseline)-map-[ab]\.json$"
    )
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alias_map_name: Literal["MAP_A", "MAP_B"]


class RealFaultCaseSetV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.case-set.v1"]
    cases: tuple[
        RealFaultCaseBindingV1,
        RealFaultCaseBindingV1,
        RealFaultCaseBindingV1,
        RealFaultCaseBindingV1,
    ]
    case_set_sha256: str

    @model_validator(mode="after")
    def require_cases(self) -> RealFaultCaseSetV1:
        if tuple(item.case_id for item in self.cases) != (
            "fault-map-a",
            "fault-map-b",
            "baseline-map-a",
            "baseline-map-b",
        ):
            raise ValueError("real-fault case order differs")
        if tuple(item.alias_map_name for item in self.cases) != (
            "MAP_A",
            "MAP_B",
            "MAP_A",
            "MAP_B",
        ):
            raise ValueError("real-fault case alias-map binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"case_set_sha256"})
        )
        if self.case_set_sha256 != expected:
            raise ValueError("real-fault case-set digest differs")
        return self


class RealFaultTruthSetV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.truth-set.v1"]
    truths: tuple[
        RealFaultCaseTruthV1,
        RealFaultCaseTruthV1,
        RealFaultCaseTruthV1,
        RealFaultCaseTruthV1,
    ]
    truth_set_sha256: str

    @model_validator(mode="after")
    def require_truths(self) -> RealFaultTruthSetV1:
        if tuple(item.case_id for item in self.truths) != (
            "fault-map-a",
            "fault-map-b",
            "baseline-map-a",
            "baseline-map-b",
        ):
            raise ValueError("real-fault truth order differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"truth_set_sha256"})
        )
        if self.truth_set_sha256 != expected:
            raise ValueError("real-fault truth-set digest differs")
        return self


class RealFaultPreLiveFreezeV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.pre-live-freeze.v1"]
    goal_version: Literal["dta-v225-real-fault-shadow-v1"]
    starting_main: Literal["8e4227fb8ac8880f89eadc3d13bf423244b378a7"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    temperature: Literal[0]
    comparator_service: Literal["email", "product-catalog", "recommendation"]
    alias_map_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flat_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scorer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schedule: tuple[RealFaultScheduleEntry, ...] = Field(min_length=8, max_length=8)
    maximum_adaptive_semantic_actions: Literal[4]
    maximum_target_equivalent_reads: Literal[4]
    maximum_transport_retries: Literal[3]
    maximum_final_execution_count: Literal[1]
    maximum_accepted_live_campaigns: Literal[1]
    agent_write_authority: Literal[0]
    action_proposal_authority: Literal[0]
    runbook_execution_authority: Literal[0]
    freeze_sha256: str

    @model_validator(mode="after")
    def require_freeze(self) -> RealFaultPreLiveFreezeV1:
        if self.schedule != build_real_fault_schedule_v225():
            raise ValueError("real-fault pre-live schedule differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"freeze_sha256"})
        )
        if self.freeze_sha256 != expected:
            raise ValueError("real-fault pre-live freeze digest differs")
        return self


class RealFaultManifestV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.manifest.v1"]
    pre_live_freeze: RealFaultPreLiveFreezeV1
    capture_pair_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str

    @model_validator(mode="after")
    def require_manifest(self) -> RealFaultManifestV1:
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("real-fault manifest digest differs")
        return self


def _build(model: type[DtaModelV22], digest_name: str, payload: dict[str, object]):
    draft = cast(Any, model).model_construct(**payload, **{digest_name: "0" * 64})
    return model.model_validate(
        {
            **payload,
            digest_name: semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={digest_name})
            ),
        }
    )


def build_alias_map_set_v225(
    *, map_a: RealFaultAliasMapV1, map_b: RealFaultAliasMapV1
) -> RealFaultAliasMapSetV1:
    return cast(
        RealFaultAliasMapSetV1,
        _build(
            RealFaultAliasMapSetV1,
            "set_sha256",
            {
                "schema_version": "dta-v225-real-fault.alias-map-set.v1",
                "maps": (map_a, map_b),
            },
        ),
    )


def build_public_alias_map_set_v225(
    *, private_maps: RealFaultAliasMapSetV1
) -> RealFaultPublicAliasMapSetV1:
    return cast(
        RealFaultPublicAliasMapSetV1,
        _build(
            RealFaultPublicAliasMapSetV1,
            "public_set_sha256",
            {
                "schema_version": "dta-v225-real-fault.public-alias-map-set.v1",
                "aliases": tuple(
                    item.alias for item in private_maps.maps[0].bindings
                ),
                "map_names": ("MAP_A", "MAP_B"),
                "exact_two_way_swap": True,
            },
        ),
    )


def build_case_set_v225(
    *, captures: tuple[RealFaultOpaqueCaptureV1, ...]
) -> RealFaultCaseSetV1:
    bindings = tuple(
        RealFaultCaseBindingV1(
            case_id=item.case_id,
            capture_path=f"config/dta-v225-real-fault/captures/{item.case_id}.json",
            capture_sha256=item.opaque_capture_sha256,
            alias_map_name=item.alias_map_name,
        )
        for item in captures
    )
    return cast(
        RealFaultCaseSetV1,
        _build(
            RealFaultCaseSetV1,
            "case_set_sha256",
            {
                "schema_version": "dta-v225-real-fault.case-set.v1",
                "cases": bindings,
            },
        ),
    )


def build_truth_set_v225(
    *, truths: tuple[RealFaultCaseTruthV1, ...]
) -> RealFaultTruthSetV1:
    return cast(
        RealFaultTruthSetV1,
        _build(
            RealFaultTruthSetV1,
            "truth_set_sha256",
            {
                "schema_version": "dta-v225-real-fault.truth-set.v1",
                "truths": truths,
            },
        ),
    )


def build_pre_live_freeze_v225(
    *,
    code_head: str,
    comparator_service: Literal["email", "product-catalog", "recommendation"],
    alias_map_set_sha256: str,
    flat_prompt_sha256: str,
    current_prompt_sha256: str,
    scorer_sha256: str,
) -> RealFaultPreLiveFreezeV1:
    return cast(
        RealFaultPreLiveFreezeV1,
        _build(
            RealFaultPreLiveFreezeV1,
            "freeze_sha256",
            {
                "schema_version": "dta-v225-real-fault.pre-live-freeze.v1",
                "goal_version": "dta-v225-real-fault-shadow-v1",
                "starting_main": "8e4227fb8ac8880f89eadc3d13bf423244b378a7",
                "code_head": code_head,
                "provider_model": "gpt-5.4-mini-2026-03-17",
                "temperature": 0,
                "comparator_service": comparator_service,
                "alias_map_set_sha256": alias_map_set_sha256,
                "flat_prompt_sha256": flat_prompt_sha256,
                "current_prompt_sha256": current_prompt_sha256,
                "scorer_sha256": scorer_sha256,
                "schedule": build_real_fault_schedule_v225(),
                "maximum_adaptive_semantic_actions": 4,
                "maximum_target_equivalent_reads": 4,
                "maximum_transport_retries": 3,
                "maximum_final_execution_count": 1,
                "maximum_accepted_live_campaigns": 1,
                "agent_write_authority": 0,
                "action_proposal_authority": 0,
                "runbook_execution_authority": 0,
            },
        ),
    )


def build_manifest_v225(
    *,
    pre_live_freeze: RealFaultPreLiveFreezeV1,
    capture_pair_sha256: str,
    case_set_sha256: str,
    truth_set_sha256: str,
) -> RealFaultManifestV1:
    return cast(
        RealFaultManifestV1,
        _build(
            RealFaultManifestV1,
            "manifest_sha256",
            {
                "schema_version": "dta-v225-real-fault.manifest.v1",
                "pre_live_freeze": pre_live_freeze,
                "capture_pair_sha256": capture_pair_sha256,
                "case_set_sha256": case_set_sha256,
                "truth_set_sha256": truth_set_sha256,
            },
        ),
    )


def load_opaque_case_v225(
    *, repository_root: Path, binding: RealFaultCaseBindingV1
) -> RealFaultOpaqueCaptureV1:
    path = repository_root / binding.capture_path
    capture = RealFaultOpaqueCaptureV1.model_validate_json(path.read_bytes())
    if capture.case_id != binding.case_id or capture.opaque_capture_sha256 != binding.capture_sha256:
        raise ValueError("real-fault case bytes differ from their binding")
    return capture


def execute_real_fault_study_v225(
    *,
    captures: dict[str, RealFaultOpaqueCaptureV1],
    model_id: str,
    flat_provider_factory: Callable[[], FlatComparisonProviderV225],
    current_provider_factory: Callable[[], SelectionProviderProtocolV223],
    truth_loader: Callable[[str], RealFaultCaseTruthV1],
    run_observer: Callable[[int, RealFaultArmRun], None] | None = None,
) -> tuple[RealFaultStudyExecutionV1, tuple[RealFaultCaseTruthV1, ...]]:
    schedule = build_real_fault_schedule_v225()
    runs = []
    truths = []
    for entry in schedule:
        capture = captures[entry.case_id]
        baseline_id = f"baseline-map-{entry.case_id[-1]}"
        baseline = captures[baseline_id]
        if entry.arm is RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE:
            run = run_v2_style_flat_adaptive_v225(
                capture=capture,
                baseline_capture=baseline,
                model_id=model_id,
                provider=flat_provider_factory(),
            )
        else:
            run = run_current_runtime_bundle_v225(
                capture=capture,
                baseline_capture=baseline,
                model_id=model_id,
                provider=current_provider_factory(),
            )
        runs.append(run)
        if run_observer is not None:
            run_observer(entry.ordinal, run)
        if entry.case_local_position == 2:
            truths.append(truth_loader(entry.case_id))
    return build_real_fault_study_execution_v225(runs=tuple(runs)), tuple(truths)


__all__ = (
    "RealFaultAliasMapSetV1",
    "RealFaultCaseBindingV1",
    "RealFaultCaseSetV1",
    "RealFaultManifestV1",
    "RealFaultPreLiveFreezeV1",
    "RealFaultPublicAliasMapSetV1",
    "RealFaultTruthSetV1",
    "build_alias_map_set_v225",
    "build_case_set_v225",
    "build_manifest_v225",
    "build_pre_live_freeze_v225",
    "build_public_alias_map_set_v225",
    "build_truth_set_v225",
    "execute_real_fault_study_v225",
    "load_opaque_case_v225",
)
