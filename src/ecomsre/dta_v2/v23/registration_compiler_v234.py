"""Deterministic compiler and local patch-bundle renderer for DTA v2.3.4."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Literal

from pydantic import Field, model_validator

from pydantic import StrictBool

from ecomsre.dta_v2.v22.predicates import RequirementServiceBindingV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    CoreOntologySchemaSnapshotV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    ExtensionPredicateRuleV234,
    FormalFaultRegistrationDraftV234,
    MechanismProposalV234,
    PredicateImplementationModeV234,
    PredicateRequirementDraftV234,
    RegistrationImplementationModeV234,
    RegistrationTestPlanV234,
    hashed_model_v234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    RegistrationDraftValidationV234,
)


class ExtensionPredicateDefinitionV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-predicate-definition.v1"]
    predicate_name: str
    predicate_slug: str
    implementation_mode: PredicateImplementationModeV234
    evidence_source: EvidenceSourceV22
    service_binding: RequirementServiceBindingV22
    require_exact_parent: StrictBool
    semantic_definition: str
    extraction_rule: ExtensionPredicateRuleV234
    threshold_rule: Literal["RULE_EMBEDS_TYPED_THRESHOLD"] | None
    predicate_sha256: str

    @model_validator(mode="after")
    def require_predicate(self) -> "ExtensionPredicateDefinitionV234":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"predicate_sha256"})
        )
        if self.predicate_sha256 != expected:
            raise ValueError("compiled extension predicate digest differs")
        return self


class ExtensionSupportClauseV234(DtaModelV22):
    schema_version: Literal["dta-v234.extension-support-clause.v1"]
    clause_id: str
    mechanism_slug: str
    requirements: tuple[PredicateRequirementDraftV234, ...]
    rationale: str
    clause_sha256: str

    @model_validator(mode="after")
    def require_clause(self) -> "ExtensionSupportClauseV234":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"clause_sha256"})
        )
        if self.clause_sha256 != expected:
            raise ValueError("compiled extension support-clause digest differs")
        return self


class CompiledFaultRegistrationV234(DtaModelV22):
    schema_version: Literal["dta-v234.compiled-fault-registration.v1"]
    registration_id: str = Field(pattern=r"^registration-v234-[0-9a-f]{16}$")
    source_draft_id: str
    source_draft_sha256: str
    source_validation_sha256: str
    core_ontology_snapshot_sha256: str
    implementation_mode: Literal["DECLARATIVE_READY"]
    mechanism: MechanismProposalV234
    predicates: tuple[ExtensionPredicateDefinitionV234, ...]
    support_clauses: tuple[ExtensionSupportClauseV234, ...]
    test_plan: RegistrationTestPlanV234
    remediation_registration: Literal["NOT_INCLUDED"]
    action_authority: Literal["NONE"]
    repository_write_authority: Literal["NONE"]
    compiled_sha256: str

    @model_validator(mode="after")
    def require_compiled(self) -> "CompiledFaultRegistrationV234":
        expected_id = (
            "registration-v234-"
            + semantic_sha256_v22(
                {
                    "source_draft_sha256": self.source_draft_sha256,
                    "source_validation_sha256": self.source_validation_sha256,
                    "mechanism_slug": self.mechanism.mechanism_slug,
                }
            )[:16]
        )
        if self.registration_id != expected_id:
            raise ValueError("compiled extension registration identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"compiled_sha256"})
        )
        if self.compiled_sha256 != expected:
            raise ValueError("compiled extension registration digest differs")
        return self


class RegistrationPatchBundleFileV234(DtaModelV22):
    relative_path: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    media_type: Literal["application/json", "text/markdown"]
    content: str
    content_sha256: str

    @model_validator(mode="after")
    def require_file(self) -> "RegistrationPatchBundleFileV234":
        if self.content_sha256 != hashlib.sha256(
            self.content.encode("utf-8")
        ).hexdigest():
            raise ValueError("registration patch-bundle file digest differs")
        return self


class RegistrationPatchBundleV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-patch-bundle.v1"]
    bundle_id: str = Field(pattern=r"^bundle-v234-[0-9a-f]{16}$")
    registration_id: str
    source_draft_id: str
    source_compiled_sha256: str
    files: tuple[RegistrationPatchBundleFileV234, ...]
    suggested_repository_targets: tuple[str, ...]
    remediation_registration: Literal["NOT_INCLUDED"]
    automatic_tracked_write: Literal[False]
    bundle_sha256: str
    bundle_directory: Path | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def require_bundle(self) -> "RegistrationPatchBundleV234":
        paths = tuple(item.relative_path for item in self.files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("registration patch-bundle files are not canonical")
        if set(paths) != set(_BUNDLE_MEDIA_TYPES_V234):
            raise ValueError("registration patch bundle does not contain exact seven files")
        if any(
            item.media_type != _BUNDLE_MEDIA_TYPES_V234[item.relative_path]
            for item in self.files
        ):
            raise ValueError("registration patch-bundle media types differ")
        targets = self.suggested_repository_targets
        if targets != tuple(sorted(set(targets))):
            raise ValueError("registration patch-bundle targets are not canonical")
        if any("/dta_v2/v22/" in path or path.startswith("src/ecomsre/dta_v2/v22") for path in targets):
            raise ValueError("registration patch bundle targets frozen v2.2 files")
        expected_id = f"bundle-v234-{self.source_compiled_sha256[:16]}"
        if self.bundle_id != expected_id:
            raise ValueError("registration patch-bundle identity differs")
        non_manifest_inventory = tuple(
            {
                "relative_path": item.relative_path,
                "media_type": item.media_type,
                "content_sha256": item.content_sha256,
            }
            for item in self.files
            if item.relative_path != "registration-manifest.json"
        )
        expected = semantic_sha256_v22(
            {
                "registration_id": self.registration_id,
                "source_draft_id": self.source_draft_id,
                "source_compiled_sha256": self.source_compiled_sha256,
                "suggested_repository_targets": self.suggested_repository_targets,
                "non_manifest_inventory": non_manifest_inventory,
            }
        )
        if self.bundle_sha256 != expected:
            raise ValueError("registration patch-bundle digest differs")
        return self


_BUNDLE_MEDIA_TYPES_V234 = {
    "dnf-support-policy.json": "application/json",
    "mechanism-definition.json": "application/json",
    "patch-plan.md": "text/markdown",
    "predicate-definitions.json": "application/json",
    "promotion-checklist.md": "text/markdown",
    "registration-manifest.json": "application/json",
    "test-specification.json": "application/json",
}


def compile_registration_v234(
    *,
    draft: FormalFaultRegistrationDraftV234,
    validation: RegistrationDraftValidationV234,
    snapshot: CoreOntologySchemaSnapshotV234,
) -> CompiledFaultRegistrationV234:
    if draft.implementation_mode is not RegistrationImplementationModeV234.DECLARATIVE_READY:
        raise ValueError("compiler accepts only DECLARATIVE_READY drafts")
    if validation.status is not DraftValidationStatusV234.VALID:
        raise ValueError("compiler requires a valid DECLARATIVE_READY validation")
    if (
        validation.classification
        is not RegistrationImplementationModeV234.DECLARATIVE_READY
    ):
        raise ValueError("compiler validation classification is not DECLARATIVE_READY")
    if (
        validation.draft_id != draft.draft_id
        or validation.draft_sha256 != draft.draft_sha256
    ):
        raise ValueError("compiler validation differs from source draft")
    if (
        validation.core_ontology_snapshot_sha256 != snapshot.snapshot_sha256
        or draft.core_ontology_snapshot_sha256 != snapshot.snapshot_sha256
    ):
        raise ValueError("compiler snapshot differs from validated source draft")
    predicates = []
    for predicate in draft.predicates:
        if predicate.extraction_rule is None:
            raise ValueError("DECLARATIVE_READY predicate lacks an extraction rule")
        payload: dict[str, Any] = {
            "schema_version": "dta-v234.extension-predicate-definition.v1",
            "predicate_name": predicate.predicate_name,
            "predicate_slug": predicate.predicate_slug,
            "implementation_mode": predicate.implementation_mode,
            "evidence_source": predicate.evidence_source,
            "service_binding": predicate.service_binding,
            "require_exact_parent": predicate.require_exact_parent,
            "semantic_definition": predicate.semantic_definition,
            "extraction_rule": predicate.extraction_rule,
            "threshold_rule": predicate.threshold_rule,
        }
        predicates.append(
            hashed_model_v234(
                ExtensionPredicateDefinitionV234,
                payload,
                "predicate_sha256",
            )
        )
    clauses = tuple(
        hashed_model_v234(
            ExtensionSupportClauseV234,
            {
                "schema_version": "dta-v234.extension-support-clause.v1",
                "clause_id": clause.clause_id,
                "mechanism_slug": clause.mechanism_slug,
                "requirements": clause.requirements,
                "rationale": clause.rationale,
            },
            "clause_sha256",
        )
        for clause in draft.support_clauses
    )
    registration_id = (
        "registration-v234-"
        + semantic_sha256_v22(
            {
                "source_draft_sha256": draft.draft_sha256,
                "source_validation_sha256": validation.validation_sha256,
                "mechanism_slug": draft.mechanism.mechanism_slug,
            }
        )[:16]
    )
    payload = {
        "schema_version": "dta-v234.compiled-fault-registration.v1",
        "registration_id": registration_id,
        "source_draft_id": draft.draft_id,
        "source_draft_sha256": draft.draft_sha256,
        "source_validation_sha256": validation.validation_sha256,
        "core_ontology_snapshot_sha256": snapshot.snapshot_sha256,
        "implementation_mode": "DECLARATIVE_READY",
        "mechanism": draft.mechanism,
        "predicates": tuple(predicates),
        "support_clauses": clauses,
        "test_plan": draft.test_plan,
        "remediation_registration": "NOT_INCLUDED",
        "action_authority": "NONE",
        "repository_write_authority": "NONE",
    }
    return hashed_model_v234(
        CompiledFaultRegistrationV234,
        payload,
        "compiled_sha256",
    )


def _json_text(value: object) -> str:
    rendered = (
        value.model_dump(mode="json")
        if isinstance(value, DtaModelV22)
        else value
    )
    return json.dumps(rendered, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _patch_plan_v234(compiled: CompiledFaultRegistrationV234) -> str:
    slug = compiled.mechanism.mechanism_slug
    return f"""# DTA v2.3.4 registration patch plan: {slug}

Status: declarative bundle preview only. No tracked repository write is authorized.

1. Review the compiled mechanism and bounded predicate definitions.
2. Run positive, known-control, no-incident, counterfactual, source-failure, and clause-binding tests.
3. Obtain a separate APPROVE_SHADOW_EVALUATION human record.
4. Evaluate the immutable draft and compiler hashes in the isolated Shadow lane.
5. Only a later explicit PROMOTE decision may copy the versioned registration to:
   - config/dta-v234/extension-ontology/registrations/{slug}.json
   - tests/dta_v23/test_extension_registration_{slug.replace('-', '_')}.py
   - docs/registrations/{slug}.md

Remediation registration: NOT_INCLUDED.
Action authority: NONE.
"""


def _promotion_checklist_v234(compiled: CompiledFaultRegistrationV234) -> str:
    return f"""# Promotion checklist

- [ ] Draft `{compiled.source_draft_id}` remains byte-bound.
- [ ] Deterministic validation remains VALID.
- [ ] Shadow evaluation is separately approved by a human record.
- [ ] Positive and negative controls pass in the isolated extension lane.
- [ ] Core Known Diagnosis retains priority.
- [ ] Action authority remains NONE.
- [ ] Remediation registration remains NOT_INCLUDED.
- [ ] Promotion writes only versioned extension artifacts.
"""


def _bundle_file(
    path: str,
    media_type: Literal["application/json", "text/markdown"],
    content: str,
) -> RegistrationPatchBundleFileV234:
    return RegistrationPatchBundleFileV234(
        relative_path=path,
        media_type=media_type,
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def _write_bound_v234(path: Path, content: str) -> None:
    if path.exists():
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise ValueError(f"registration patch-bundle file already differs: {path.name}")
        return
    path.write_text(content, encoding="utf-8")


def _verify_rendered_bundle_v234(
    directory: Path,
    files: tuple[RegistrationPatchBundleFileV234, ...],
) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("registration patch-bundle directory is not a real directory")
    actual_names = {path.name for path in directory.iterdir()}
    expected_names = {item.relative_path for item in files}
    if actual_names != expected_names:
        raise ValueError("registration patch-bundle directory contents differ")
    for item in files:
        path = directory / item.relative_path
        if path.is_symlink() or not path.is_file():
            raise ValueError("registration patch-bundle file is not a real file")
        raw = path.read_bytes()
        if raw != item.content.encode("utf-8") or (
            hashlib.sha256(raw).hexdigest() != item.content_sha256
        ):
            raise ValueError(f"registration patch-bundle bytes differ: {path.name}")


def render_registration_patch_bundle_v234(
    *,
    compiled: CompiledFaultRegistrationV234,
    output_root: Path,
) -> RegistrationPatchBundleV234:
    slug = compiled.mechanism.mechanism_slug
    targets = tuple(
        sorted(
            (
                f"config/dta-v234/extension-ontology/registrations/{slug}.json",
                f"docs/registrations/{slug}.md",
                f"tests/dta_v23/test_extension_registration_{slug.replace('-', '_')}.py",
            )
        )
    )
    non_manifest_files = tuple(
        sorted(
            (
                _bundle_file(
                    "mechanism-definition.json",
                    "application/json",
                    _json_text(compiled.mechanism),
                ),
                _bundle_file(
                    "predicate-definitions.json",
                    "application/json",
                    _json_text(
                        {
                            "schema_version": "dta-v234.extension-predicate-set.v1",
                            "predicates": [
                                item.model_dump(mode="json") for item in compiled.predicates
                            ],
                        }
                    ),
                ),
                _bundle_file(
                    "dnf-support-policy.json",
                    "application/json",
                    _json_text(
                        {
                            "schema_version": "dta-v234.extension-support-policy.v1",
                            "dnf_semantics": "one clause is AND; multiple clauses are OR",
                            "clauses": [
                                item.model_dump(mode="json")
                                for item in compiled.support_clauses
                            ],
                        }
                    ),
                ),
                _bundle_file(
                    "test-specification.json",
                    "application/json",
                    _json_text(compiled.test_plan),
                ),
                _bundle_file(
                    "patch-plan.md",
                    "text/markdown",
                    _patch_plan_v234(compiled),
                ),
                _bundle_file(
                    "promotion-checklist.md",
                    "text/markdown",
                    _promotion_checklist_v234(compiled),
                ),
            ),
            key=lambda item: item.relative_path,
        )
    )
    non_manifest_inventory = tuple(
        {
            "relative_path": item.relative_path,
            "media_type": item.media_type,
            "content_sha256": item.content_sha256,
        }
        for item in non_manifest_files
    )
    bundle_sha256 = semantic_sha256_v22(
        {
            "registration_id": compiled.registration_id,
            "source_draft_id": compiled.source_draft_id,
            "source_compiled_sha256": compiled.compiled_sha256,
            "suggested_repository_targets": targets,
            "non_manifest_inventory": non_manifest_inventory,
        }
    )
    manifest = {
        "schema_version": "dta-v234.registration-manifest.v1",
        "registration_id": compiled.registration_id,
        "source_draft_id": compiled.source_draft_id,
        "source_compiled_sha256": compiled.compiled_sha256,
        "bundle_payload_sha256": bundle_sha256,
        "artifact_inventory": (
            {
                "relative_path": "registration-manifest.json",
                "content_sha256": "SELF_EXCLUDED_TO_AVOID_CIRCULAR_DIGEST",
            },
            *non_manifest_inventory,
        ),
        "suggested_repository_targets": targets,
        "automatic_tracked_write": False,
        "remediation_registration": "NOT_INCLUDED",
        "action_authority": "NONE",
    }
    files = tuple(
        sorted(
            (
                *non_manifest_files,
                _bundle_file(
                    "registration-manifest.json",
                    "application/json",
                    _json_text(manifest),
                ),
            ),
            key=lambda item: item.relative_path,
        )
    )
    bundle_id = f"bundle-v234-{compiled.compiled_sha256[:16]}"
    requested_root = output_root.absolute()
    if requested_root.parts[-3:] != (
        ".local",
        "dta-v234",
        "registration-bundles",
    ):
        raise ValueError(
            "registration patch bundles must stay under a .local/dta-v234 local root"
        )
    if requested_root.resolve(strict=False) != requested_root:
        raise ValueError("registration patch-bundle root or ancestor may not be a symlink")
    requested_root.mkdir(parents=True, exist_ok=True)
    if any(
        path.is_symlink()
        for path in (
            requested_root.parent.parent,
            requested_root.parent,
            requested_root,
        )
    ):
        raise ValueError("registration patch-bundle root or ancestor may not be a symlink")
    output_root = requested_root.resolve()
    bundle_directory = output_root / compiled.source_draft_id
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-patch-bundle.v1",
        "bundle_id": bundle_id,
        "registration_id": compiled.registration_id,
        "source_draft_id": compiled.source_draft_id,
        "source_compiled_sha256": compiled.compiled_sha256,
        "files": files,
        "suggested_repository_targets": targets,
        "remediation_registration": "NOT_INCLUDED",
        "automatic_tracked_write": False,
        "bundle_sha256": bundle_sha256,
        "bundle_directory": bundle_directory,
    }
    bundle = RegistrationPatchBundleV234.model_validate(payload)
    if bundle_directory.exists():
        _verify_rendered_bundle_v234(bundle_directory, bundle.files)
        return bundle
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{compiled.source_draft_id}.",
            dir=output_root,
        )
    )
    try:
        for file in bundle.files:
            _write_bound_v234(temporary / file.relative_path, file.content)
        _verify_rendered_bundle_v234(temporary, bundle.files)
        try:
            os.rename(temporary, bundle_directory)
        except FileExistsError:
            _verify_rendered_bundle_v234(bundle_directory, bundle.files)
        _verify_rendered_bundle_v234(bundle_directory, bundle.files)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return bundle


__all__ = (
    "CompiledFaultRegistrationV234",
    "ExtensionPredicateDefinitionV234",
    "ExtensionSupportClauseV234",
    "RegistrationPatchBundleFileV234",
    "RegistrationPatchBundleV234",
    "compile_registration_v234",
    "render_registration_patch_bundle_v234",
)
