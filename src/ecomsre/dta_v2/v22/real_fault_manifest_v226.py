"""Frozen aliases, cases, truth, and execution bindings for DTA v2.2.6."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultAliasMapV1,
    RealFaultOpaqueCaptureV1,
    build_alias_maps_v225,
)
from ecomsre.dta_v2.v22.real_fault_study_v226 import (
    RealFaultCaseTruthV226,
    RealFaultScheduleEntryV226,
    build_real_fault_schedule_v226,
)


OPAQUE_IDENTITY_SEED_V226 = "dta-v226-real-fault-transfer-repair-v1"
PREDECESSOR_ALIASES_V225 = ("svc-6db724c330", "svc-f1a57dd3c4")


def _service_alias_v226(ordinal: int) -> str:
    digest = sha256(
        f"{OPAQUE_IDENTITY_SEED_V226}:service:{ordinal:04d}".encode("utf-8")
    ).hexdigest()
    return f"svc-{digest[:10]}"


def generate_opaque_service_aliases_v226() -> tuple[str, str]:
    aliases = tuple(sorted(_service_alias_v226(index) for index in range(2)))
    if aliases == PREDECESSOR_ALIASES_V225:
        raise ValueError("v2.2.6 opaque aliases repeat the predecessor identities")
    return cast(tuple[str, str], aliases)


class RealFaultAliasMapSetV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.alias-map-set.v1"]
    identity_seed: Literal["dta-v226-real-fault-transfer-repair-v1"]
    aliases: tuple[str, str]
    maps: tuple[RealFaultAliasMapV1, RealFaultAliasMapV1]
    predecessor_aliases_different: Literal[True]
    set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_set(self) -> RealFaultAliasMapSetV226:
        if self.aliases != generate_opaque_service_aliases_v226():
            raise ValueError("v2.2.6 alias identities differ from the neutral seed")
        if tuple(item.map_name for item in self.maps) != ("MAP_A", "MAP_B"):
            raise ValueError("v2.2.6 alias-map order differs")
        visible = {
            tuple(item.alias for item in alias_map.bindings)
            for alias_map in self.maps
        }
        if visible != {self.aliases}:
            raise ValueError("v2.2.6 alias maps expose different identity pools")
        first = {
            item.alias: item.physical_service for item in self.maps[0].bindings
        }
        second = {
            item.alias: item.physical_service for item in self.maps[1].bindings
        }
        if any(first[alias] == second[alias] for alias in self.aliases):
            raise ValueError("v2.2.6 alias maps are not exact swaps")
        if self.set_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 alias-map set digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"set_sha256"})
        )


class RealFaultPublicAliasMapSetV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.public-alias-map-set.v1"]
    aliases: tuple[str, str]
    map_names: tuple[Literal["MAP_A"], Literal["MAP_B"]]
    exact_two_way_swap: Literal[True]
    predecessor_aliases_different: Literal[True]
    public_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_public_set(self) -> RealFaultPublicAliasMapSetV226:
        if self.aliases != generate_opaque_service_aliases_v226():
            raise ValueError("v2.2.6 public aliases differ from the frozen pool")
        if self.public_set_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 public alias-set digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"public_set_sha256"})
        )


class RealFaultCaseBindingV226(DtaModelV22):
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    capture_path: str = Field(
        pattern=r"^config/dta-v226-real-fault/captures/(?:fault|baseline)-map-[ab]\.json$"
    )
    capture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alias_map_name: Literal["MAP_A", "MAP_B"]


class RealFaultCaseSetV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.case-set.v1"]
    cases: tuple[
        RealFaultCaseBindingV226,
        RealFaultCaseBindingV226,
        RealFaultCaseBindingV226,
        RealFaultCaseBindingV226,
    ]
    case_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_cases(self) -> RealFaultCaseSetV226:
        if tuple(item.case_id for item in self.cases) != (
            "fault-map-a",
            "fault-map-b",
            "baseline-map-a",
            "baseline-map-b",
        ):
            raise ValueError("v2.2.6 case order differs")
        if tuple(item.alias_map_name for item in self.cases) != (
            "MAP_A",
            "MAP_B",
            "MAP_A",
            "MAP_B",
        ):
            raise ValueError("v2.2.6 case-to-map binding differs")
        if self.case_set_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 case-set digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"case_set_sha256"})
        )


class RealFaultTruthSetV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.truth-set.v1"]
    truths: tuple[
        RealFaultCaseTruthV226,
        RealFaultCaseTruthV226,
        RealFaultCaseTruthV226,
        RealFaultCaseTruthV226,
    ]
    truth_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_truths(self) -> RealFaultTruthSetV226:
        if tuple(item.case_id for item in self.truths) != (
            "fault-map-a",
            "fault-map-b",
            "baseline-map-a",
            "baseline-map-b",
        ):
            raise ValueError("v2.2.6 truth order differs")
        if self.truth_set_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 truth-set digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"truth_set_sha256"})
        )


class RealFaultPreLiveFreezeV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.pre-live-freeze.v1"]
    goal_version: Literal["dta-v226-real-fault-transfer-repair-v1"]
    starting_main: Literal["1c6520d706481f37b63a5b14c1fe8554b52d530b"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    provider_model: Literal["gpt-5.4-mini-2026-03-17"]
    temperature: Literal[0]
    comparator_service: Literal["email", "product-catalog", "recommendation"]
    alias_map_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminalizer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scorer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_development_summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_gate_iteration_sha256: Literal[
        "d0ca56b5b6d03faf8135fc7d5dca1568ef911935c6d2655902b1716749e9dbec"
    ]
    pre_live_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schedule: tuple[RealFaultScheduleEntryV226, ...] = Field(
        min_length=8, max_length=8
    )
    maximum_adaptive_semantic_actions: Literal[4]
    maximum_target_equivalent_reads: Literal[4]
    maximum_protocol_repairs: Literal[2]
    maximum_transport_retries: Literal[3]
    maximum_final_execution_count: Literal[1]
    maximum_accepted_live_campaigns: Literal[1]
    agent_write_authority: Literal[0]
    action_proposal_authority: Literal[0]
    runbook_execution_authority: Literal[0]
    freeze_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_freeze(self) -> RealFaultPreLiveFreezeV226:
        if self.schedule != build_real_fault_schedule_v226():
            raise ValueError("v2.2.6 frozen schedule differs")
        if self.freeze_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 pre-live freeze digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"freeze_sha256"})
        )


class RealFaultManifestV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.manifest.v1"]
    pre_live_freeze: RealFaultPreLiveFreezeV226
    capture_pair_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    truth_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_id: str = Field(pattern=r"^exec-v226-[0-9a-f]{16}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_manifest(self) -> RealFaultManifestV226:
        if self.manifest_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 manifest digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )


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


def build_alias_map_set_v226(
    *, comparator_service: Literal["email", "product-catalog", "recommendation"]
) -> RealFaultAliasMapSetV226:
    aliases = generate_opaque_service_aliases_v226()
    maps = build_alias_maps_v225(
        fault_service="ad", comparator_service=comparator_service, aliases=aliases
    )
    return cast(
        RealFaultAliasMapSetV226,
        _build(
            RealFaultAliasMapSetV226,
            "set_sha256",
            {
                "schema_version": "dta-v226-real-fault.alias-map-set.v1",
                "identity_seed": OPAQUE_IDENTITY_SEED_V226,
                "aliases": aliases,
                "maps": maps,
                "predecessor_aliases_different": True,
            },
        ),
    )


def build_public_alias_map_set_v226(
    *, private_maps: RealFaultAliasMapSetV226
) -> RealFaultPublicAliasMapSetV226:
    return cast(
        RealFaultPublicAliasMapSetV226,
        _build(
            RealFaultPublicAliasMapSetV226,
            "public_set_sha256",
            {
                "schema_version": "dta-v226-real-fault.public-alias-map-set.v1",
                "aliases": private_maps.aliases,
                "map_names": ("MAP_A", "MAP_B"),
                "exact_two_way_swap": True,
                "predecessor_aliases_different": True,
            },
        ),
    )


def build_case_set_v226(
    *, captures: tuple[RealFaultOpaqueCaptureV1, ...]
) -> RealFaultCaseSetV226:
    bindings = tuple(
        RealFaultCaseBindingV226(
            case_id=capture.case_id,
            capture_path=(
                f"config/dta-v226-real-fault/captures/{capture.case_id}.json"
            ),
            capture_sha256=capture.opaque_capture_sha256,
            alias_map_name=capture.alias_map_name,
        )
        for capture in captures
    )
    return cast(
        RealFaultCaseSetV226,
        _build(
            RealFaultCaseSetV226,
            "case_set_sha256",
            {
                "schema_version": "dta-v226-real-fault.case-set.v1",
                "cases": bindings,
            },
        ),
    )


def build_truth_set_v226(
    *, truths: tuple[RealFaultCaseTruthV226, ...]
) -> RealFaultTruthSetV226:
    return cast(
        RealFaultTruthSetV226,
        _build(
            RealFaultTruthSetV226,
            "truth_set_sha256",
            {
                "schema_version": "dta-v226-real-fault.truth-set.v1",
                "truths": truths,
            },
        ),
    )


def build_pre_live_freeze_v226(**values: object) -> RealFaultPreLiveFreezeV226:
    return cast(
        RealFaultPreLiveFreezeV226,
        _build(
            RealFaultPreLiveFreezeV226,
            "freeze_sha256",
            {
                "schema_version": "dta-v226-real-fault.pre-live-freeze.v1",
                "goal_version": "dta-v226-real-fault-transfer-repair-v1",
                "starting_main": "1c6520d706481f37b63a5b14c1fe8554b52d530b",
                "temperature": 0,
                "schedule": build_real_fault_schedule_v226(),
                "maximum_adaptive_semantic_actions": 4,
                "maximum_target_equivalent_reads": 4,
                "maximum_protocol_repairs": 2,
                "maximum_transport_retries": 3,
                "maximum_final_execution_count": 1,
                "maximum_accepted_live_campaigns": 1,
                "agent_write_authority": 0,
                "action_proposal_authority": 0,
                "runbook_execution_authority": 0,
                **values,
            },
        ),
    )


def build_manifest_v226(**values: object) -> RealFaultManifestV226:
    return cast(
        RealFaultManifestV226,
        _build(
            RealFaultManifestV226,
            "manifest_sha256",
            {
                "schema_version": "dta-v226-real-fault.manifest.v1",
                **values,
            },
        ),
    )


__all__ = (
    "OPAQUE_IDENTITY_SEED_V226",
    "PREDECESSOR_ALIASES_V225",
    "RealFaultAliasMapSetV226",
    "RealFaultCaseSetV226",
    "RealFaultManifestV226",
    "RealFaultPreLiveFreezeV226",
    "RealFaultPublicAliasMapSetV226",
    "RealFaultTruthSetV226",
    "build_alias_map_set_v226",
    "build_case_set_v226",
    "build_manifest_v226",
    "build_pre_live_freeze_v226",
    "build_public_alias_map_set_v226",
    "build_truth_set_v226",
    "generate_opaque_service_aliases_v226",
)
