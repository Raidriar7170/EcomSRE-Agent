"""Create-once cryptographic seal for the private eight-case held-out pack."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from ecomsre.dta_v2.v21.contracts import DtaModelV21, Sha256V21, semantic_sha256
from ecomsre.dta_v2.v21.evaluation_campaign import (
    EvaluationFreezeManifestV21,
    EvaluationPreregistrationV21,
    EvaluationScheduleV21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationSplitV21,
    EvaluatorCaseTruthV21,
)
from ecomsre_live_sandbox.contracts import write_private_json


class HeldOutCaseSealBindingV21(DtaModelV21):
    case_id: str = Field(pattern=r"^dta21-case-0(?:1[3-9]|20)$")
    case_sha256: Sha256V21
    truth_sha256: Sha256V21
    case_file_sha256: Sha256V21
    truth_file_sha256: Sha256V21
    binding_sha256: Sha256V21

    @model_validator(mode="after")
    def require_binding(self) -> HeldOutCaseSealBindingV21:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("held-out case seal binding digest differs")
        return self


class HeldOutPackSealV21(DtaModelV21):
    schema_version: Literal["dta-v21.held-out-pack-seal.v1"]
    created_at: datetime
    base_code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    freeze_manifest_sha256: Sha256V21
    public_case_manifest_sha256: Sha256V21
    preregistration_sha256: Sha256V21
    schedule_sha256: Sha256V21
    cases: tuple[
        HeldOutCaseSealBindingV21,
        HeldOutCaseSealBindingV21,
        HeldOutCaseSealBindingV21,
        HeldOutCaseSealBindingV21,
        HeldOutCaseSealBindingV21,
        HeldOutCaseSealBindingV21,
        HeldOutCaseSealBindingV21,
        HeldOutCaseSealBindingV21,
    ]
    pack_sha256: Sha256V21
    held_out_executed: Literal[False]
    seal_sha256: Sha256V21

    @model_validator(mode="after")
    def require_seal(self) -> HeldOutPackSealV21:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != timedelta(
            0
        ):
            raise ValueError("held-out seal timestamp must use UTC")
        ids = tuple(item.case_id for item in self.cases)
        if ids != tuple(f"dta21-case-{index:03d}" for index in range(13, 21)):
            raise ValueError("held-out seal case order differs")
        expected_pack = semantic_sha256(
            [item.model_dump(mode="json") for item in self.cases]
        )
        if self.pack_sha256 != expected_pack:
            raise ValueError("held-out pack digest differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"seal_sha256"})
        )
        if self.seal_sha256 != expected:
            raise ValueError("held-out seal digest differs")
        return self


def seal_held_out_pack_v21(
    *,
    held_out_pack_root: Path,
    freeze_manifest: EvaluationFreezeManifestV21,
    schedule: EvaluationScheduleV21,
    preregistration: EvaluationPreregistrationV21,
    created_at: datetime | None = None,
) -> HeldOutPackSealV21:
    """Validate and seal without exposing or executing any held-out entry."""

    seal_path = held_out_pack_root / "held-out-seal.v1.json"
    if seal_path.exists() or seal_path.is_symlink():
        raise FileExistsError("held-out pack has already been sealed")
    if schedule.schedule_sha256 != freeze_manifest.schedule_sha256:
        raise ValueError("held-out seal schedule differs from freeze")
    if preregistration.preregistration_sha256 != freeze_manifest.preregistration_sha256:
        raise ValueError("held-out seal preregistration differs from freeze")
    public = {
        item.case_id: item
        for item in freeze_manifest.public_case_manifest.held_out_cases
    }
    bindings = []
    for index in range(13, 21):
        case_id = f"dta21-case-{index:03d}"
        root = held_out_pack_root / "cases" / case_id
        case_path = root / "agent-visible.json"
        truth_path = root / "evaluator-truth.json"
        case = AgentVisibleReplayCaseV21.model_validate_json(_read_regular(case_path))
        truth = EvaluatorCaseTruthV21.model_validate_json(_read_regular(truth_path))
        expected = public.get(case_id)
        if (
            expected is None
            or case.case_id != case_id
            or truth.case_id != case_id
            or truth.split is not EvaluationSplitV21.HELD_OUT
            or case.case_sha256 != expected.case_sha256
            or truth.truth_sha256 != expected.truth_sha256
        ):
            raise ValueError("private held-out bytes differ from public binding")
        payload: dict[str, object] = {
            "case_id": case_id,
            "case_sha256": case.case_sha256,
            "truth_sha256": truth.truth_sha256,
            "case_file_sha256": _file_sha256(case_path),
            "truth_file_sha256": _file_sha256(truth_path),
        }
        bindings.append(
            HeldOutCaseSealBindingV21.model_validate(
                {**payload, "binding_sha256": semantic_sha256(payload)}
            )
        )
    seal_payload: dict[str, object] = {
        "schema_version": "dta-v21.held-out-pack-seal.v1",
        "created_at": (created_at or datetime.now(timezone.utc)).replace(microsecond=0),
        "base_code_head": freeze_manifest.base_code_head,
        "freeze_manifest_sha256": freeze_manifest.manifest_sha256,
        "public_case_manifest_sha256": (
            freeze_manifest.public_case_manifest.manifest_sha256
        ),
        "preregistration_sha256": preregistration.preregistration_sha256,
        "schedule_sha256": schedule.schedule_sha256,
        "cases": tuple(bindings),
        "pack_sha256": semantic_sha256(
            [item.model_dump(mode="json") for item in bindings]
        ),
        "held_out_executed": False,
    }
    draft = cast(Any, HeldOutPackSealV21).model_construct(
        **seal_payload, seal_sha256="0" * 64
    )
    seal = HeldOutPackSealV21.model_validate(
        {
            **seal_payload,
            "seal_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"seal_sha256"})
            ),
        }
    )
    write_private_json(seal_path, seal, create_once=True)
    return seal


def verify_held_out_pack_seal_v21(
    *, held_out_pack_root: Path, seal: HeldOutPackSealV21
) -> None:
    seal = HeldOutPackSealV21.model_validate(seal.model_dump(mode="python"))
    for binding in seal.cases:
        root = held_out_pack_root / "cases" / binding.case_id
        if (
            _file_sha256(root / "agent-visible.json") != binding.case_file_sha256
            or _file_sha256(root / "evaluator-truth.json") != binding.truth_file_sha256
        ):
            raise ValueError("held-out pack bytes changed after seal")


def _read_regular(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("held-out pack file is missing or unsafe")
    return path.read_text(encoding="utf-8")


def _file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("held-out pack file is missing or unsafe")
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = (
    "HeldOutCaseSealBindingV21",
    "HeldOutPackSealV21",
    "seal_held_out_pack_v21",
    "verify_held_out_pack_seal_v21",
)
