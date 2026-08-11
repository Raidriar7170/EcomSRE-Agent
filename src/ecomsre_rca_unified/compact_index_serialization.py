"""Compact shared-evidence and pipe-row serialization with exact token counts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Protocol, cast

from ecomsre.evidence.hashes import canonical_json_bytes
from ecomsre.phase2.token_policy import load_offline_tokenizer
from ecomsre_rca100.contracts import RCA100InitialDiagnosis
from ecomsre_rca100.prompt import SYSTEM_PROMPT as SOURCE_B0_SYSTEM_PROMPT
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.root_candidate_index import CandidateIndex
from ecomsre_rca_unified.root_evidence_projection import SOURCE_ORDER


B0_FUNCTION_NAME = "submit_strong_single_diagnosis"
C1_FUNCTION_NAME = "submit_compact_root_selection"
B0_SYSTEM_PROMPT = SOURCE_B0_SYSTEM_PROMPT.replace(
    "rca100.initial-diagnosis", "strong-single.diagnosis"
)
C1_SYSTEM_PROMPT = """You are selecting the causal root from a compact, pre-retrieved candidate set.

1. Choose exactly one candidate_id from the provided cards.
2. Distinguish causal root from the strongest downstream symptom.
3. Prefer candidates with direct or upstream causal evidence over candidates that are merely highly anomalous.
4. Use only the supplied evidence refs.
5. Do not invent an entity or candidate ID."""

LAYER_CODES: Mapping[CanonicalEntityLayer, str] = {
    CanonicalEntityLayer.SERVICE: "S",
    CanonicalEntityLayer.WORKLOAD: "W",
    CanonicalEntityLayer.NODE: "N",
    CanonicalEntityLayer.DATABASE: "D",
    CanonicalEntityLayer.CACHE: "C",
    CanonicalEntityLayer.MESSAGE_QUEUE: "Q",
    CanonicalEntityLayer.NETWORK_COMPONENT: "X",
    CanonicalEntityLayer.CLUSTER: "I",
    CanonicalEntityLayer.INFRASTRUCTURE: "I",
}
SOURCE_CODES = {
    "METRICS": "M",
    "LOGS": "L",
    "TRACES": "T",
    "EVENTS": "E",
    "ALERTS": "A",
}


def compact_rows(index: CandidateIndex) -> tuple[str, ...]:
    rows: list[str] = []
    anomaly_times = [
        item.universe.first_anomaly_time
        for item in index.candidates
        if item.universe.first_anomaly_time is not None
    ]
    first_case_anomaly = min(anomaly_times) if anomaly_times else None
    for item in index.candidates:
        value = item.universe
        sources = (
            "".join(
                SOURCE_CODES[source]
                for source in sorted(value.all_sources, key=SOURCE_ORDER.index)
            )
            or "-"
        )
        reasons = "".join(value.reasons[:2]) or "-"
        metric = "-" if value.metrics_rank is None else str(value.metrics_rank)
        anomaly = (
            "-"
            if value.first_anomaly_time is None
            else f"{int(round((value.first_anomaly_time - cast(float, first_case_anomaly)) * 1000)):+d}"
        )
        refs = ",".join(
            ref
            for ref in value.evidence_refs
            if ref.partition(":")[0] in {"metric", "log", "trace"}
        )
        refs = ",".join(refs.split(",")[:2]) if refs else "-"
        name = value.display_name.replace("|", "/").replace("\n", " ").strip()
        rows.append(
            f"{item.candidate_id}|{LAYER_CODES[value.layer]}|{name}|src={sources}|why={reasons}|m={metric}|t={anomaly}|rel={value.relation_to_alert}|ref={refs}"
        )
    return tuple(rows)


def model_index_payload(index: CandidateIndex) -> dict[str, object]:
    return {
        "schema_version": "compact-root-candidate-index.model.v1",
        "format": "candidate_id|layer|name|src=...|why=...|m=...|t=...|rel=...|ref=...",
        "rows": list(compact_rows(index)),
    }


def _b0_output_schema() -> dict[str, object]:
    schema = RCA100InitialDiagnosis.model_json_schema(mode="validation")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise ValueError("B0 schema definitions are missing")
    reasoning = definitions.pop("RCA100ReasoningStep", None)
    if not isinstance(reasoning, dict):
        raise ValueError("B0 reasoning schema is missing")
    reasoning["title"] = "StrongSingleReasoningStep"
    definitions["StrongSingleReasoningStep"] = reasoning
    schema["title"] = "StrongSingleDiagnosis"

    def replace(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref" and item == "#/$defs/RCA100ReasoningStep":
                    value[key] = "#/$defs/StrongSingleReasoningStep"
                else:
                    replace(item)
        elif isinstance(value, list):
            for item in value:
                replace(item)

    replace(schema)
    return schema


def _c1_output_schema() -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": {
            "root_candidate_id": {"pattern": "^C(?:0[1-9]|1[0-2])$", "type": "string"},
            "fault_type": {"maxLength": 128, "minLength": 1, "type": "string"},
            "confidence": {"maximum": 1.0, "minimum": 0.0, "type": "number"},
            "evidence_refs": {
                "items": {
                    "pattern": "^(?:metric|log|trace):[0-9]{4}$",
                    "type": "string",
                },
                "maxItems": 4,
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
            },
            "summary": {"maxLength": 400, "minLength": 1, "type": "string"},
        },
        "required": [
            "root_candidate_id",
            "fault_type",
            "confidence",
            "evidence_refs",
            "summary",
        ],
        "title": "CompactRootSelection",
        "type": "object",
    }


def build_full_request(
    *,
    base_context: Mapping[str, object],
    index: CandidateIndex | None,
    model: str = "gpt-5.4-mini-2026-03-17",
) -> dict[str, object]:
    arm = "B0" if index is None else "C1"
    envelope: dict[str, object] = {
        "schema_version": "compact-evidence-retrieval.model-envelope.v1",
        "context": dict(base_context),
    }
    if index is not None:
        envelope["compact_candidate_index"] = model_index_payload(index)
    function_name = B0_FUNCTION_NAME if arm == "B0" else C1_FUNCTION_NAME
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": B0_SYSTEM_PROMPT if arm == "B0" else C1_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    envelope,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ],
        "temperature": 0.0,
        "top_p": 1.0,
        "n": 1,
        "parallel_tool_calls": False,
        "max_completion_tokens": 2048,
        "tool_choice": {"type": "function", "function": {"name": function_name}},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": function_name,
                    "description": "Return the one typed Strong Single diagnosis."
                    if arm == "B0"
                    else "Select exactly one supplied compact root candidate.",
                    "strict": arm == "C1",
                    "parameters": _b0_output_schema()
                    if arm == "B0"
                    else _c1_output_schema(),
                },
            }
        ],
    }


class _Encoding(Protocol):
    def encode(
        self,
        text: str,
        *,
        allowed_special: set[str],
        disallowed_special: str,
    ) -> list[int]: ...


def load_frozen_encoding(project_root: Path) -> _Encoding:
    return load_offline_tokenizer(project_root)


def offline_full_request_tokens(
    encoding: _Encoding, request: Mapping[str, object]
) -> int:
    text = canonical_json_bytes(dict(request)).decode("utf-8")
    return len(encoding.encode(text, allowed_special=set(), disallowed_special="all"))


def contract_hashes() -> dict[str, str]:
    return {
        "b0_system_prompt_sha256": hashlib.sha256(
            B0_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "c1_system_prompt_sha256": hashlib.sha256(
            C1_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "b0_output_schema_sha256": hashlib.sha256(
            canonical_json_bytes(_b0_output_schema())
        ).hexdigest(),
        "c1_output_schema_sha256": hashlib.sha256(
            canonical_json_bytes(_c1_output_schema())
        ).hexdigest(),
    }


__all__ = [
    "B0_SYSTEM_PROMPT",
    "C1_SYSTEM_PROMPT",
    "build_full_request",
    "compact_rows",
    "contract_hashes",
    "load_frozen_encoding",
    "model_index_payload",
    "offline_full_request_tokens",
]
