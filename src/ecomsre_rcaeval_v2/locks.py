"""Live verification for the v2 model, prompt, and typed-schema lock."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval.adapter import INCIDENT_TEMPLATE
from ecomsre_rcaeval.artifacts import read_json_object
from ecomsre_rcaeval.contracts import CommanderDecision, SpecialistAssessment
from ecomsre_rcaeval.provider import COMMANDER_PROMPT, SPECIALIST_PROMPT
from ecomsre_rcaeval_v2.contracts import (
    CommanderDecisionV2,
    CommanderInputSnapshotV2,
    IndicatorResolutionV2,
    JudgeInputSnapshotV2,
    JudgeServiceDecisionV2,
    ResolverInputSnapshotV2,
    SpecialistAssessmentV2,
    SpecialistInputSnapshotV2,
    V2Model,
)
from ecomsre_rcaeval_v2.provider import FINAL_JUDGE_PROMPT_V2


PROJECT_ROOT = Path(__file__).resolve().parents[2]
V1_CONFIG = PROJECT_ROOT / "config" / "rcaeval-re2-v1"
V2_CONFIG = PROJECT_ROOT / "config" / "rcaeval-re2-v2-dev"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text_sha(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _file_sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _schema_sha(contract: type[V2Model] | type[SpecialistAssessment] | type[CommanderDecision]) -> str:
    payload = json.dumps(
        contract.model_json_schema(mode="validation"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha_bytes(payload)


def expected_model_prompt_lock() -> dict[str, object]:
    v1 = read_json_object(V1_CONFIG / "prompt-lock.json")
    return {
        "schema_version": "rcaeval-re2-v2-dev.model-prompt-lock.v1",
        "protocol_id": "rcaeval-re2-v2-dev-v1",
        "provider": v1["provider"],
        "model": v1["model"],
        "temperature": v1["temperature"],
        "top_p": v1["top_p"],
        "max_completion_tokens": v1["max_completion_tokens"],
        "v1_prompt_lock_sha256": _file_sha(V1_CONFIG / "prompt-lock.json"),
        "prompts": {
            "incident_template_sha256": _text_sha(INCIDENT_TEMPLATE),
            "specialist_prompt_sha256": _text_sha(SPECIALIST_PROMPT),
            "commander_prompt_sha256": _text_sha(COMMANDER_PROMPT),
            "final_judge_prompt_v2_sha256": _text_sha(FINAL_JUDGE_PROMPT_V2),
        },
        "schemas": {
            "v1_specialist_sha256": _schema_sha(SpecialistAssessment),
            "v1_commander_sha256": _schema_sha(CommanderDecision),
            "v2_specialist_input_sha256": _schema_sha(SpecialistInputSnapshotV2),
            "v2_specialist_output_sha256": _schema_sha(SpecialistAssessmentV2),
            "v2_commander_input_sha256": _schema_sha(CommanderInputSnapshotV2),
            "v2_commander_output_sha256": _schema_sha(CommanderDecisionV2),
            "v2_judge_input_sha256": _schema_sha(JudgeInputSnapshotV2),
            "v2_judge_output_sha256": _schema_sha(JudgeServiceDecisionV2),
            "v2_resolver_input_sha256": _schema_sha(ResolverInputSnapshotV2),
            "v2_resolver_output_sha256": _schema_sha(IndicatorResolutionV2),
        },
        "retry": {
            "semantic": "FORBIDDEN",
            "transport": "FORBIDDEN",
            "fallback": "NO_FALLBACK",
        },
    }


def verify_model_prompt_lock(
    path: Path | None = None,
) -> dict[str, object]:
    lock_path = path or V2_CONFIG / "model-prompt-lock.json"
    observed = read_json_object(lock_path)
    expected = expected_model_prompt_lock()
    if observed != expected:
        raise ValueError("v2 model/prompt/schema lock differs from live contracts")
    return observed


def verify_evaluation_lock(path: Path | None = None) -> dict[str, object]:
    """Verify the root lock without claiming it predates the stopped smoke."""

    lock_path = path or V2_CONFIG / "evaluation-lock.json"
    observed = read_json_object(lock_path)
    if observed.get("schema_version") != "rcaeval-re2-v2-dev.evaluation-lock.v1":
        raise ValueError("v2 evaluation root lock schema is invalid")
    if observed.get("protocol_id") != "rcaeval-re2-v2-dev-v1":
        raise ValueError("v2 evaluation root lock protocol is invalid")
    bindings = observed.get("prerequisite_lock_sha256")
    if not isinstance(bindings, dict):
        raise ValueError("v2 evaluation root lock bindings are invalid")
    expected = {
        name: _file_sha(V2_CONFIG / name)
        for name in (
            "protocol.json",
            "dataset-lock.json",
            "split-lock.json",
            "model-prompt-lock.json",
            "budget-lock.json",
            "indicator-candidate-formulas.json",
        )
    }
    if bindings != expected:
        raise ValueError("v2 evaluation root lock differs from prerequisite locks")
    timing = observed.get("freeze_timing")
    if timing != {
        "created_after_provider_smoke_termination": True,
        "negative_gate_evidence_only": True,
        "retroactive_provider_authorization": False,
    }:
        raise ValueError("v2 evaluation root lock timing is not fail closed")
    selection = observed.get("indicator_selection")
    if not isinstance(selection, dict) or selection.get("formula") != "F0":
        raise ValueError("v2 evaluation root lock formula is invalid")
    gate_path = PROJECT_ROOT / "docs" / "review-evidence" / "rcaeval-re2-v2-dev" / "indicator-tool-gate.json"
    if selection.get("tool_gate_sha256") != _file_sha(gate_path):
        raise ValueError("v2 evaluation root lock tool gate differs")
    return observed
