"""Immutable local stores for DTA v2.3.4 draft and validation artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
    OntologyExpansionStateV234,
)
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
    RegistrationPatchBundleV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    FormalFaultRegistrationDraftV234,
    hashed_model_v234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    RegistrationDraftValidationV234,
)


class RegistrationLifecycleTransitionV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-lifecycle-transition.v1"]
    transition_id: str
    authorization_id: str
    shadow_fault_id: str
    from_state: OntologyExpansionStateV234
    to_state: OntologyExpansionStateV234
    authorization_sha256: str
    registration_seed_sha256: str
    core_ontology_snapshot_sha256: str
    draft_sha256: str
    validation_sha256: str | None
    compiled_sha256: str | None
    bundle_sha256: str | None
    transitioned_at: datetime
    simulation: StrictBool
    transition_sha256: str

    @model_validator(mode="after")
    def require_transition(self) -> "RegistrationLifecycleTransitionV234":
        if self.transitioned_at.tzinfo is None or self.transitioned_at.utcoffset() != timedelta(0):
            raise ValueError("registration lifecycle timestamp must be UTC")
        allowed = {
            (
                OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED,
                OntologyExpansionStateV234.DRAFT_GENERATED,
            ),
            (
                OntologyExpansionStateV234.DRAFT_GENERATED,
                OntologyExpansionStateV234.DRAFT_INVALID,
            ),
            (
                OntologyExpansionStateV234.DRAFT_GENERATED,
                OntologyExpansionStateV234.DRAFT_VALIDATED,
            ),
            (
                OntologyExpansionStateV234.DRAFT_VALIDATED,
                OntologyExpansionStateV234.PATCH_RENDERED,
            ),
        }
        if (self.from_state, self.to_state) not in allowed:
            raise ValueError("registration lifecycle transition is not allowed")
        if self.to_state is OntologyExpansionStateV234.DRAFT_GENERATED:
            if any(
                value is not None
                for value in (
                    self.validation_sha256,
                    self.compiled_sha256,
                    self.bundle_sha256,
                )
            ):
                raise ValueError("draft-generated transition carries later artifacts")
        elif self.to_state in {
            OntologyExpansionStateV234.DRAFT_INVALID,
            OntologyExpansionStateV234.DRAFT_VALIDATED,
        }:
            if self.validation_sha256 is None or any(
                value is not None for value in (self.compiled_sha256, self.bundle_sha256)
            ):
                raise ValueError("draft validation transition artifacts differ")
        elif any(
            value is None
            for value in (
                self.validation_sha256,
                self.compiled_sha256,
                self.bundle_sha256,
            )
        ):
            raise ValueError("patch-rendered transition lacks compiled artifacts")
        expected_id = (
            "registration-transition-v234-"
            + semantic_sha256_v22(
                {
                    "authorization_id": self.authorization_id,
                    "draft_sha256": self.draft_sha256,
                    "from_state": self.from_state.value,
                    "to_state": self.to_state.value,
                    "validation_sha256": self.validation_sha256,
                    "compiled_sha256": self.compiled_sha256,
                    "bundle_sha256": self.bundle_sha256,
                }
            )[:16]
        )
        if self.transition_id != expected_id:
            raise ValueError("registration lifecycle transition identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"transition_sha256"})
        )
        if self.transition_sha256 != expected:
            raise ValueError("registration lifecycle transition digest differs")
        return self


class LocalRegistrationDraftStoreV234:
    """Project-local create-once artifacts; no tracked or external write path."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.drafts_dir = self.root / "formal-registration-drafts"
        self.validations_dir = self.root / "registration-validations"
        self.bundles_dir = self.root / "registration-bundles"
        self.transitions_dir = self.root / "registration-lifecycle-transitions"

    @staticmethod
    def _write_bound(path: Path, value: DtaModelV22) -> None:
        rendered = value.model_dump_json(indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
                raise ValueError(f"local registration artifact already differs: {path.name}")
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)

    def save_draft(self, draft: FormalFaultRegistrationDraftV234) -> Path:
        path = self.drafts_dir / f"{draft.draft_id}.json"
        self._write_bound(path, draft)
        return path

    def load_draft(self, draft_id: str) -> FormalFaultRegistrationDraftV234:
        if not re.fullmatch(r"draft-v234-[0-9a-f]{16}", draft_id):
            raise ValueError("formal registration draft ID is invalid")
        path = self.drafts_dir / f"{draft_id}.json"
        if not path.is_file():
            raise ValueError("formal registration draft is absent")
        return FormalFaultRegistrationDraftV234.model_validate_json(path.read_bytes())

    def list_draft_ids(self) -> tuple[str, ...]:
        if not self.drafts_dir.is_dir():
            return ()
        return tuple(
            sorted(path.stem for path in self.drafts_dir.glob("draft-v234-*.json"))
        )

    def save_validation(self, validation: RegistrationDraftValidationV234) -> Path:
        path = self.validations_dir / f"{validation.draft_id}.json"
        self._write_bound(path, validation)
        return path

    def load_validation(self, draft_id: str) -> RegistrationDraftValidationV234:
        if not re.fullmatch(r"draft-v234-[0-9a-f]{16}", draft_id):
            raise ValueError("registration validation draft ID is invalid")
        path = self.validations_dir / f"{draft_id}.json"
        if not path.is_file():
            raise ValueError("registration draft validation is absent")
        return RegistrationDraftValidationV234.model_validate_json(path.read_bytes())

    def list_transitions(self) -> tuple[RegistrationLifecycleTransitionV234, ...]:
        if not self.transitions_dir.is_dir():
            return ()
        return tuple(
            RegistrationLifecycleTransitionV234.model_validate_json(path.read_bytes())
            for path in sorted(
                self.transitions_dir.glob("registration-transition-v234-*.json")
            )
        )

    def _record_transition(
        self,
        *,
        context: DraftGenerationAuthorizationResultV234,
        draft: FormalFaultRegistrationDraftV234,
        from_state: OntologyExpansionStateV234,
        to_state: OntologyExpansionStateV234,
        transitioned_at: datetime,
        validation: RegistrationDraftValidationV234 | None = None,
        compiled: CompiledFaultRegistrationV234 | None = None,
        bundle: RegistrationPatchBundleV234 | None = None,
    ) -> RegistrationLifecycleTransitionV234:
        if (
            draft.authorization_id != context.authorization.authorization_id
            or draft.registration_seed_sha256
            != context.registration_seed.seed_sha256
            or draft.core_ontology_snapshot_sha256
            != context.core_ontology_snapshot.snapshot_sha256
        ):
            raise ValueError("registration lifecycle draft differs from authorization")
        if any(
            item.to_state is to_state and item.draft_sha256 == draft.draft_sha256
            for item in self.list_transitions()
        ):
            raise ValueError("registration lifecycle stage is already recorded")
        if to_state is not OntologyExpansionStateV234.DRAFT_GENERATED and not any(
            item.to_state is from_state and item.draft_sha256 == draft.draft_sha256
            for item in self.list_transitions()
        ):
            raise ValueError("registration lifecycle prior stage is absent")
        if validation is not None and (
            validation.draft_sha256 != draft.draft_sha256
            or validation.core_ontology_snapshot_sha256
            != context.core_ontology_snapshot.snapshot_sha256
        ):
            raise ValueError("registration lifecycle validation differs from draft")
        if compiled is not None and (
            compiled.source_draft_sha256 != draft.draft_sha256
            or validation is None
            or compiled.source_validation_sha256 != validation.validation_sha256
        ):
            raise ValueError("registration lifecycle compiled artifact differs")
        if bundle is not None and (
            compiled is None
            or bundle.source_compiled_sha256 != compiled.compiled_sha256
        ):
            raise ValueError("registration lifecycle bundle differs")
        identity = {
            "authorization_id": draft.authorization_id,
            "draft_sha256": draft.draft_sha256,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "validation_sha256": (
                None if validation is None else validation.validation_sha256
            ),
            "compiled_sha256": None if compiled is None else compiled.compiled_sha256,
            "bundle_sha256": None if bundle is None else bundle.bundle_sha256,
        }
        payload: dict[str, Any] = {
            "schema_version": "dta-v234.registration-lifecycle-transition.v1",
            "transition_id": (
                "registration-transition-v234-"
                + semantic_sha256_v22(identity)[:16]
            ),
            "authorization_id": draft.authorization_id,
            "shadow_fault_id": draft.shadow_fault_id,
            "from_state": from_state,
            "to_state": to_state,
            "authorization_sha256": context.authorization.authorization_sha256,
            "registration_seed_sha256": context.registration_seed.seed_sha256,
            "core_ontology_snapshot_sha256": (
                context.core_ontology_snapshot.snapshot_sha256
            ),
            "draft_sha256": draft.draft_sha256,
            "validation_sha256": (
                None if validation is None else validation.validation_sha256
            ),
            "compiled_sha256": None if compiled is None else compiled.compiled_sha256,
            "bundle_sha256": None if bundle is None else bundle.bundle_sha256,
            "transitioned_at": transitioned_at,
            "simulation": context.authorization.simulation,
        }
        transition = hashed_model_v234(
            RegistrationLifecycleTransitionV234,
            payload,
            "transition_sha256",
        )
        self._write_bound(
            self.transitions_dir / f"{transition.transition_id}.json",
            transition,
        )
        return transition

    def record_draft_generated(
        self,
        *,
        context: DraftGenerationAuthorizationResultV234,
        draft: FormalFaultRegistrationDraftV234,
        transitioned_at: datetime,
    ) -> RegistrationLifecycleTransitionV234:
        return self._record_transition(
            context=context,
            draft=draft,
            from_state=OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED,
            to_state=OntologyExpansionStateV234.DRAFT_GENERATED,
            transitioned_at=transitioned_at,
        )

    def record_validation(
        self,
        *,
        context: DraftGenerationAuthorizationResultV234,
        draft: FormalFaultRegistrationDraftV234,
        validation: RegistrationDraftValidationV234,
        transitioned_at: datetime,
    ) -> RegistrationLifecycleTransitionV234:
        to_state = (
            OntologyExpansionStateV234.DRAFT_VALIDATED
            if validation.status is DraftValidationStatusV234.VALID
            else OntologyExpansionStateV234.DRAFT_INVALID
        )
        return self._record_transition(
            context=context,
            draft=draft,
            from_state=OntologyExpansionStateV234.DRAFT_GENERATED,
            to_state=to_state,
            transitioned_at=transitioned_at,
            validation=validation,
        )

    def record_patch_rendered(
        self,
        *,
        context: DraftGenerationAuthorizationResultV234,
        draft: FormalFaultRegistrationDraftV234,
        validation: RegistrationDraftValidationV234,
        compiled: CompiledFaultRegistrationV234,
        bundle: RegistrationPatchBundleV234,
        transitioned_at: datetime,
    ) -> RegistrationLifecycleTransitionV234:
        return self._record_transition(
            context=context,
            draft=draft,
            from_state=OntologyExpansionStateV234.DRAFT_VALIDATED,
            to_state=OntologyExpansionStateV234.PATCH_RENDERED,
            transitioned_at=transitioned_at,
            validation=validation,
            compiled=compiled,
            bundle=bundle,
        )


__all__ = (
    "LocalRegistrationDraftStoreV234",
    "RegistrationLifecycleTransitionV234",
)
