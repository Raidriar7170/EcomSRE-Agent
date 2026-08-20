"""Frozen input contract for the single DTA v2.2.1 final study."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, StrictInt, StringConstraints, model_validator
from typing_extensions import Annotated

from ecomsre.dta_v2.v22.evidence_acquisition_v221 import StudyCombinationV221
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, Sha256V22


CommitShaV221 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class StudyFileBindingV221(DtaModelV22):
    path: str = Field(min_length=1, max_length=256)
    sha256: Sha256V22

    @model_validator(mode="after")
    def require_safe_relative_path(self) -> "StudyFileBindingV221":
        relative = PurePosixPath(self.path)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != self.path:
            raise ValueError("study binding path is not a canonical relative path")
        return self


class EvidenceAcquisitionStudyManifestV221(DtaModelV22):
    schema_version: Literal[
        "dta-v22.1.evidence-acquisition-study-manifest.v1"
    ]
    base_commit: CommitShaV221
    implementation_commit: CommitShaV221
    model: str = Field(min_length=1, max_length=160)
    prompt: StudyFileBindingV221
    case_set: StudyFileBindingV221
    truth_set: StudyFileBindingV221
    policy_source: StudyFileBindingV221
    scorer_source: StudyFileBindingV221
    historical_results_manifest: StudyFileBindingV221
    combinations: tuple[StudyCombinationV221, ...]
    expected_cases: StrictInt
    expected_arm_policy_runs: StrictInt
    single_execution_rule: Literal["EXACTLY_ONE_FULL_STUDY_EXECUTION"]
    schedule_rule: Literal[
        "DETERMINISTIC_BALANCED_ROTATION_INTERLEAVED_BY_CASE"
    ]
    truth_isolation_rule: Literal[
        "LOAD_ONLY_AFTER_ALL_ARM_POLICY_EXECUTIONS"
    ]

    @model_validator(mode="after")
    def require_preregistered_shape(self) -> "EvidenceAcquisitionStudyManifestV221":
        if self.base_commit != "fceadc924d4909ca1457b35f268429f0272427ce":
            raise ValueError("study base commit differs")
        if self.combinations != tuple(StudyCombinationV221):
            raise ValueError("study combinations differ")
        if self.expected_cases != 12 or self.expected_arm_policy_runs != 48:
            raise ValueError("study execution shape differs")
        bindings = (
            self.prompt,
            self.case_set,
            self.truth_set,
            self.policy_source,
            self.scorer_source,
            self.historical_results_manifest,
        )
        if len({item.path for item in bindings}) != len(bindings):
            raise ValueError("study binding paths are not unique")
        return self


def sha256_file_v221(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_and_verify_study_manifest_v221(
    *,
    manifest_path: Path,
    repository_root: Path,
    configured_model: str,
) -> EvidenceAcquisitionStudyManifestV221:
    manifest = EvidenceAcquisitionStudyManifestV221.model_validate_json(
        manifest_path.read_bytes()
    )
    if manifest.model != configured_model:
        raise ValueError("configured Provider model differs from frozen study manifest")
    root = repository_root.resolve()
    for binding in (
        manifest.prompt,
        manifest.case_set,
        manifest.truth_set,
        manifest.policy_source,
        manifest.scorer_source,
        manifest.historical_results_manifest,
    ):
        target = (root / binding.path).resolve()
        if root not in target.parents:
            raise ValueError("study binding escapes repository root")
        if sha256_file_v221(target) != binding.sha256:
            raise ValueError(f"study binding drift: {binding.path}")
    return manifest


__all__ = (
    "EvidenceAcquisitionStudyManifestV221",
    "StudyFileBindingV221",
    "load_and_verify_study_manifest_v221",
    "sha256_file_v221",
)
