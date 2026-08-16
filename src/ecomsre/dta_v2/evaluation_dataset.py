"""Truth-separated promotion and loading for the public PR-E replay dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.capture_campaign import CaptureCampaignClosure, CaptureTerminal
from ecomsre.dta_v2.contracts import DtaModel, Sha256, semantic_sha256
from ecomsre.dta_v2.evaluation_contracts import (
    AgentVisibleReplayCase,
    EvaluationSplit,
    EvaluatorCaseTruth,
    GitCommit,
)


class PublicEvaluationCaseBinding(DtaModel):
    schema_version: Literal["dta-v2.public-evaluation-case-binding.v1"]
    case_id: str = Field(pattern=r"^dta-case-[0-9]{3}$")
    split: Literal[EvaluationSplit.DEVELOPMENT, EvaluationSplit.NO_ACTION]
    case_sha256: Sha256
    truth_sha256: Sha256
    agent_visible_path: str = Field(
        pattern=r"^(development|no-action)/agent-visible/dta-case-[0-9]{3}\.json$"
    )
    evaluator_truth_path: str = Field(
        pattern=r"^(development|no-action)/evaluator-truth/dta-case-[0-9]{3}\.json$"
    )

    @model_validator(mode="after")
    def require_binding(self) -> PublicEvaluationCaseBinding:
        prefix = (
            "development"
            if self.split is EvaluationSplit.DEVELOPMENT
            else "no-action"
        )
        if (
            self.agent_visible_path
            != f"{prefix}/agent-visible/{self.case_id}.json"
            or self.evaluator_truth_path
            != f"{prefix}/evaluator-truth/{self.case_id}.json"
        ):
            raise ValueError("public evaluation binding paths differ from split")
        return self


class PublicEvaluationDatasetManifest(DtaModel):
    schema_version: Literal["dta-v2.public-evaluation-dataset.v1"]
    capture_head: GitCommit
    capture_closure_sha256: Sha256
    selected_email_variant: Literal["10x", "100x", "1000x"]
    public_cases: tuple[PublicEvaluationCaseBinding, ...] = Field(
        min_length=9, max_length=9
    )
    held_out_case_sha256s: tuple[Sha256, Sha256, Sha256]
    held_out_truth_sha256s: tuple[Sha256, Sha256, Sha256]
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def require_manifest(self) -> PublicEvaluationDatasetManifest:
        if (
            tuple(item.case_id for item in self.public_cases)
            != tuple(sorted(item.case_id for item in self.public_cases))
            or len({item.case_id for item in self.public_cases}) != 9
            or sum(
                item.split is EvaluationSplit.DEVELOPMENT
                for item in self.public_cases
            )
            != 6
            or sum(
                item.split is EvaluationSplit.NO_ACTION
                for item in self.public_cases
            )
            != 3
        ):
            raise ValueError("public evaluation case matrix differs")
        for values in (
            self.held_out_case_sha256s,
            self.held_out_truth_sha256s,
        ):
            if len(set(values)) != 3:
                raise ValueError("held-out digest projection contains duplicates")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("public evaluation dataset digest differs")
        return self


class LoadedPublicEvaluationCase(DtaModel):
    case: AgentVisibleReplayCase
    truth: EvaluatorCaseTruth

    @model_validator(mode="after")
    def require_pair(self) -> LoadedPublicEvaluationCase:
        if self.case.case_id != self.truth.case_id:
            raise ValueError("public evaluation case and truth differ")
        return self


def _encoded(value: object) -> bytes:
    if isinstance(value, DtaModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_once(path: Path, value: object) -> None:
    encoded = _encoded(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("public evaluation target is a symbolic link")
    if path.exists():
        if not path.is_file() or path.read_bytes() != encoded:
            raise FileExistsError("public evaluation target already differs")
        return
    path.write_bytes(encoded)


def promote_public_evaluation_dataset(
    *,
    capture_root: Path,
    output_root: Path,
    capture_head: str,
) -> PublicEvaluationDatasetManifest:
    """Promote dev/no-action only; held-out projects to hashes and counts."""

    source = Path(capture_root).resolve()
    target = Path(output_root).resolve()
    closure = CaptureCampaignClosure.model_validate_json(
        (source / "capture-campaign-closure.json").read_text(encoding="utf-8")
    )
    if closure.terminal is not CaptureTerminal.PASS:
        raise ValueError("capture campaign is not PASS")
    public: list[PublicEvaluationCaseBinding] = []
    held_cases: list[str] = []
    held_truths: list[str] = []
    seen_capture_hashes: set[str] = set()
    for directory in sorted((source / "cases").iterdir()):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("capture case directory is invalid")
        case = AgentVisibleReplayCase.model_validate_json(
            (directory / "agent-visible.json").read_text(encoding="utf-8")
        )
        truth = EvaluatorCaseTruth.model_validate_json(
            (directory / "evaluator-truth.json").read_text(encoding="utf-8")
        )
        if case.case_id != truth.case_id or directory.name != case.case_id:
            raise ValueError("capture case and evaluator truth differ")
        if case.case_sha256 not in closure.captured_case_sha256s:
            raise ValueError("capture closure does not bind replay case")
        seen_capture_hashes.add(case.case_sha256)
        if truth.split is EvaluationSplit.HELD_OUT:
            held_cases.append(case.case_sha256)
            held_truths.append(truth.truth_sha256)
            continue
        prefix = (
            "development"
            if truth.split is EvaluationSplit.DEVELOPMENT
            else "no-action"
        )
        case_relative = f"{prefix}/agent-visible/{case.case_id}.json"
        truth_relative = f"{prefix}/evaluator-truth/{case.case_id}.json"
        binding = PublicEvaluationCaseBinding(
            schema_version="dta-v2.public-evaluation-case-binding.v1",
            case_id=case.case_id,
            split=truth.split,
            case_sha256=case.case_sha256,
            truth_sha256=truth.truth_sha256,
            agent_visible_path=case_relative,
            evaluator_truth_path=truth_relative,
        )
        _write_once(target / case_relative, case)
        _write_once(target / truth_relative, truth)
        public.append(binding)
    if (
        len(seen_capture_hashes) != 12
        or seen_capture_hashes != set(closure.captured_case_sha256s)
    ):
        raise ValueError("capture tree and closure case set differ")
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.public-evaluation-dataset.v1",
        "capture_head": capture_head,
        "capture_closure_sha256": closure.closure_sha256,
        "selected_email_variant": closure.selected_email_variant,
        "public_cases": tuple(
            item.model_dump(mode="json")
            for item in sorted(public, key=lambda item: item.case_id)
        ),
        "held_out_case_sha256s": tuple(sorted(held_cases)),
        "held_out_truth_sha256s": tuple(sorted(held_truths)),
    }
    manifest = PublicEvaluationDatasetManifest.model_validate(
        {**payload, "manifest_sha256": semantic_sha256(payload)}
    )
    _write_once(target / "manifest.json", manifest)
    return manifest


def load_public_evaluation_dataset(
    root: Path,
) -> tuple[
    PublicEvaluationDatasetManifest,
    tuple[LoadedPublicEvaluationCase, ...],
]:
    source = Path(root).resolve()
    manifest = PublicEvaluationDatasetManifest.model_validate_json(
        (source / "manifest.json").read_text(encoding="utf-8")
    )
    loaded: list[LoadedPublicEvaluationCase] = []
    for binding in manifest.public_cases:
        case_path = (source / binding.agent_visible_path).resolve()
        truth_path = (source / binding.evaluator_truth_path).resolve()
        if (
            not case_path.is_relative_to(source)
            or not truth_path.is_relative_to(source)
            or case_path.is_symlink()
            or truth_path.is_symlink()
        ):
            raise ValueError("public evaluation binding escapes dataset root")
        case = AgentVisibleReplayCase.model_validate_json(
            case_path.read_text(encoding="utf-8")
        )
        truth = EvaluatorCaseTruth.model_validate_json(
            truth_path.read_text(encoding="utf-8")
        )
        if (
            case.case_id != binding.case_id
            or truth.case_id != binding.case_id
            or truth.split is not binding.split
            or case.case_sha256 != binding.case_sha256
            or truth.truth_sha256 != binding.truth_sha256
        ):
            raise ValueError("public evaluation binding content differs")
        loaded.append(LoadedPublicEvaluationCase(case=case, truth=truth))
    return manifest, tuple(loaded)


__all__ = [
    "LoadedPublicEvaluationCase",
    "PublicEvaluationCaseBinding",
    "PublicEvaluationDatasetManifest",
    "load_public_evaluation_dataset",
    "promote_public_evaluation_dataset",
]
