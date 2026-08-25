#!/usr/bin/env python3
"""Verify the consumed v2.3.1 study and frozen algorithm surface."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ecomsre.dta_v2.v23.discovery_provider import DISCOVERY_SYSTEM_PROMPT_V23
from ecomsre.dta_v2.v23.discovery_provider_v231 import DISCOVERY_SYSTEM_PROMPT_V231
from ecomsre.dta_v2.v23.evaluation_v231 import FixedEvaluationArtifactV231


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    freeze = json.loads(
        (root / "config/dta-v231-successor/predecessor-freeze.json").read_text(
            encoding="utf-8"
        )
    )
    expected_contract = {
        "maximum_discovery_reads": 3,
        "maximum_conflict_resolution_reads": 1,
        "maximum_protocol_repairs": 2,
        "maximum_exact_request_transport_retries": 3,
        "positive_treatment_novelty_recall": 0.70,
        "positive_absolute_recall_improvement": 0.20,
        "positive_conflict_prone_recall": 0.625,
        "positive_root_localization": 0.65,
        "positive_broad_domain_accuracy": 0.55,
        "positive_evidence_ref_validity": 0.90,
        "positive_maximum_false_novel_rate": 0.20,
        "positive_maximum_known_accuracy_drop_cases": 1,
        "positive_maximum_no_incident_accuracy_drop_cases": 1,
        "positive_maximum_true_conflict_converted_cases": 1,
        "positive_required_action_authority_violations": 0,
        "mixed_absolute_recall_improvement": 0.15,
        "mixed_conflict_prone_recall": 0.50,
        "mixed_evidence_ref_validity": 0.85,
        "mixed_maximum_false_novel_rate": 0.25,
        "mixed_required_action_authority_violations": 0,
    }
    if freeze.get("frozen_contract") != expected_contract:
        raise ValueError("frozen v2.3.1 execution or threshold contract differs")
    for binding in freeze["bindings"]:
        path = root / binding["path"]
        if not path.is_file() or _sha256(path) != binding["sha256"]:
            raise ValueError(f"frozen predecessor artifact differs: {binding['path']}")

    runtime_manifest_path = root / "config/dta-v231/evaluation/manifest.json"
    if _sha256(runtime_manifest_path) != freeze["frozen_runtime_manifest_sha256"]:
        raise ValueError("frozen v2.3.1 runtime manifest differs")
    runtime_manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
    if len(runtime_manifest["runtime_sources"]) != freeze["frozen_runtime_source_count"]:
        raise ValueError("frozen v2.3.1 runtime source count differs")
    for binding in runtime_manifest["runtime_sources"]:
        path = root / binding["path"]
        if not path.is_file() or _sha256(path) != binding["sha256"]:
            raise ValueError(f"frozen algorithm source differs: {binding['path']}")

    strict_prompt = hashlib.sha256(DISCOVERY_SYSTEM_PROMPT_V23.encode()).hexdigest()
    treatment_prompt = hashlib.sha256(
        DISCOVERY_SYSTEM_PROMPT_V231.encode()
    ).hexdigest()
    if strict_prompt != freeze["strict_system_prompt_sha256"]:
        raise ValueError("frozen strict Provider Prompt differs")
    if treatment_prompt != freeze["treatment_system_prompt_sha256"]:
        raise ValueError("frozen treatment Provider Prompt differs")

    artifact = FixedEvaluationArtifactV231.model_validate_json(
        (root / "docs/results/dta-v231-conflict-aware-evaluation.json").read_bytes()
    )
    if (
        artifact.execution_count != freeze["predecessor_execution_count"]
        or artifact.artifact_sha256 != freeze["predecessor_artifact_sha256"]
        or artifact.measured_result_terminal.value
        != freeze["predecessor_measured_terminal"]
    ):
        raise ValueError("consumed predecessor study identity differs")
    partial = root / ".local/dta-v231/fixed-evaluation.partial.jsonl"
    if len(partial.read_text(encoding="utf-8").splitlines()) != 24:
        raise ValueError("consumed predecessor partial denominator differs")

    print("DTA_V231_SUCCESSOR_PREDECESSOR_FREEZE_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
