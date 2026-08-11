"""Exact B0 and compact C1 Provider request contracts."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from pydantic import ValidationError

from ecomsre.evidence.hashes import canonical_json_bytes
from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    ProviderProtocolError,
    StdlibOpenAICompatibleTransport,
)
from ecomsre_rca100.contracts import RCA100InitialDiagnosis
from ecomsre_rca100.prompt import SYSTEM_PROMPT as SOURCE_B0_SYSTEM_PROMPT
from ecomsre_rcaeval_v2.provider import (
    ProviderOutputValidationError,
    safe_validation_error_from_exception,
)
from ecomsre_rca_unified.compact_contracts import (
    CompactBaseContext,
    CompactCandidateContext,
    CompactRootSelection,
    ResolvedCompactDiagnosis,
    resolve_compact_selection,
)


B0_FUNCTION_NAME = "submit_strong_single_diagnosis"
C1_FUNCTION_NAME = "submit_compact_root_selection"
B0_SYSTEM_PROMPT = SOURCE_B0_SYSTEM_PROMPT.replace(
    "rca100.initial-diagnosis", "strong-single.diagnosis"
)
if B0_SYSTEM_PROMPT == SOURCE_B0_SYSTEM_PROMPT:
    raise ValueError("Strong Single prompt identity neutralization did not apply")
C1_SYSTEM_PROMPT = """You are selecting the causal root from a compact, pre-retrieved candidate set.

1. Choose exactly one candidate_id from the provided cards.
2. Distinguish causal root from the strongest downstream symptom.
3. Prefer candidates with direct or upstream causal evidence over candidates that are merely highly anomalous.
4. Use only the supplied evidence refs.
5. Do not invent an entity or candidate ID."""


def _private_payload_markers() -> tuple[str, ...]:
    return (
        "rca100",
        "re2-ob",
        "re2-ss",
        "task_id",
        "case_id",
        "ground_truth",
        "root_cause_service",
    )


def _base_model_dump(base: CompactBaseContext) -> dict[str, object]:
    return {
        "schema_version": "strong-single-live.base-context.v1",
        "alert_title": base.alert_title,
        "prompt_text": base.prompt_text,
        "alert_entity_ref": base.alert_entity_ref,
        "entities": [
            {
                "entity_ref": item.entity_ref,
                "entity_name": item.display_name,
                "layer": item.layer.value,
                "service_ancestor_or_none": item.service_ancestor_or_none,
                "parent_ref_or_none": item.parent_ref_or_none,
            }
            for item in base.entities
        ],
        "evidence": [item.model_dump(mode="json") for item in base.evidence],
        "source_status": dict(base.source_status),
    }


def _b0_output_schema() -> dict[str, object]:
    schema = RCA100InitialDiagnosis.model_json_schema(mode="validation")
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise ValueError("Strong Single output schema definitions are missing")
    reasoning = definitions.pop("RCA100ReasoningStep", None)
    if not isinstance(reasoning, dict):
        raise ValueError("Strong Single reasoning schema is missing")
    reasoning["title"] = "StrongSingleReasoningStep"
    definitions["StrongSingleReasoningStep"] = reasoning
    schema["title"] = "StrongSingleDiagnosis"

    def replace_refs(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref" and item == "#/$defs/RCA100ReasoningStep":
                    value[key] = "#/$defs/StrongSingleReasoningStep"
                else:
                    replace_refs(item)
        elif isinstance(value, list):
            for item in value:
                replace_refs(item)

    replace_refs(schema)
    return schema


def _c1_output_schema() -> dict[str, object]:
    return CompactRootSelection.model_json_schema(mode="validation")


def build_request_payload(
    *,
    model: str,
    base: CompactBaseContext,
    arm: str,
    candidates: CompactCandidateContext | None,
    max_completion_tokens: int,
) -> dict[str, object]:
    if max_completion_tokens <= 0:
        raise ValueError("compact Provider completion budget must be positive")
    if arm not in {"B0", "C1"} or (arm == "C1") != (candidates is not None):
        raise ValueError("compact Provider arm and candidate context differ")
    envelope: dict[str, object] = {
        "schema_version": "compact-evidence-retrieval.model-envelope.v1",
        "context": _base_model_dump(base),
    }
    if candidates is not None:
        envelope["compact_candidate_context"] = candidates.model_visible_dump()
    function_name = B0_FUNCTION_NAME if arm == "B0" else C1_FUNCTION_NAME
    payload: dict[str, object] = {
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
        "max_completion_tokens": max_completion_tokens,
        "tool_choice": {"type": "function", "function": {"name": function_name}},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": function_name,
                    "description": (
                        "Return the one typed Strong Single diagnosis."
                        if arm == "B0"
                        else "Select exactly one supplied compact root candidate."
                    ),
                    "strict": arm == "C1",
                    "parameters": (
                        _b0_output_schema() if arm == "B0" else _c1_output_schema()
                    ),
                },
            }
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    if any(marker in encoded for marker in _private_payload_markers()):
        raise ValueError("compact Provider request contains private identity metadata")
    return payload


def prompt_hashes() -> dict[str, str]:
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


def estimate_input_tokens(payload: Mapping[str, object]) -> int:
    return int(math.ceil(len(canonical_json_bytes(payload)) / 3.0))


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProviderProtocolError(f"{label} must be an object")
    return value


def _one(value: object, label: str) -> object:
    if not isinstance(value, list) or len(value) != 1:
        raise ProviderProtocolError(f"{label} must contain exactly one item")
    return value[0]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate Provider JSON key")
        output[key] = value
    return output


class OpenAICompatibleCompactProvider:
    """Exactly one typed B0 or C1 semantic model operation per invocation."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        expected_model: str,
        timeout_seconds: float,
        max_completion_tokens: int,
        transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        if config.model != expected_model:
            raise ValueError("compact Provider model differs from the lock")
        if timeout_seconds <= 0 or max_completion_tokens <= 0:
            raise ValueError("compact Provider budget is invalid")
        self._config = config
        self._timeout = float(timeout_seconds)
        self._max_completion_tokens = max_completion_tokens
        self._transport = transport or StdlibOpenAICompatibleTransport()
        self._calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._usage_known = True
        self._last_request_sha256: str | None = None

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def input_tokens_if_known(self) -> int | None:
        return self._input_tokens if self._usage_known else None

    @property
    def output_tokens_if_known(self) -> int | None:
        return self._output_tokens if self._usage_known else None

    @property
    def last_usage_tokens(self) -> int | None:
        if not self._usage_known:
            return None
        return self._input_tokens + self._output_tokens

    @property
    def last_request_sha256(self) -> str | None:
        return self._last_request_sha256

    def diagnose(
        self,
        *,
        base: CompactBaseContext,
        arm: str,
        candidates: CompactCandidateContext | None,
    ) -> RCA100InitialDiagnosis | ResolvedCompactDiagnosis:
        payload = build_request_payload(
            model=self._config.model,
            base=base,
            arm=arm,
            candidates=candidates,
            max_completion_tokens=self._max_completion_tokens,
        )
        self._last_request_sha256 = hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest()
        self._calls += 1
        raw = self._transport.post_json(
            url=f"{self._config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=self._timeout,
        )
        response = _mapping(raw, "Provider response")
        usage = response.get("usage")
        if usage is None:
            self._usage_known = False
        else:
            usage_object = _mapping(usage, "Provider usage")
            tokens = (
                usage_object.get("prompt_tokens"),
                usage_object.get("completion_tokens"),
                usage_object.get("total_tokens"),
            )
            if not all(type(item) is int and item >= 0 for item in tokens):
                raise ProviderProtocolError("Provider usage token counts are invalid")
            input_tokens, output_tokens, total_tokens = tokens
            assert isinstance(input_tokens, int)
            assert isinstance(output_tokens, int)
            assert isinstance(total_tokens, int)
            if input_tokens + output_tokens != total_tokens:
                raise ProviderProtocolError("Provider usage total is inconsistent")
            if self._usage_known:
                self._input_tokens += input_tokens
                self._output_tokens += output_tokens
        if response.get("model") != self._config.model:
            raise ProviderProtocolError("Provider response model differs from lock")
        choice = _mapping(_one(response.get("choices"), "Provider choices"), "choice")
        if choice.get("index") != 0 or choice.get("finish_reason") != "tool_calls":
            raise ProviderProtocolError("Provider choice metadata is invalid")
        message = _mapping(choice.get("message"), "Provider message")
        if message.get("role") != "assistant":
            raise ProviderProtocolError("Provider message role is invalid")
        tool_call = _mapping(
            _one(message.get("tool_calls"), "Provider tool calls"), "tool call"
        )
        if tool_call.get("type") != "function":
            raise ProviderProtocolError("Provider tool-call type is invalid")
        function = _mapping(tool_call.get("function"), "Provider function")
        expected_function = B0_FUNCTION_NAME if arm == "B0" else C1_FUNCTION_NAME
        if function.get("name") != expected_function:
            raise ProviderProtocolError("Provider function name is invalid")
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise ProviderProtocolError("Provider function arguments must be JSON text")
        try:
            parsed = json.loads(
                arguments,
                object_pairs_hook=_strict_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid constant: {value}")
                ),
            )
            validated_json = json.dumps(
                parsed,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if arm == "B0":
                diagnosis = RCA100InitialDiagnosis.model_validate_json(validated_json)
                self._validate_b0(diagnosis, base=base)
                return diagnosis
            if candidates is None:
                raise ValueError("C1 Provider output lacks a candidate context")
            selection = CompactRootSelection.model_validate_json(validated_json)
            return resolve_compact_selection(
                selection,
                context=candidates,
                visible_evidence_refs=frozenset(
                    item.evidence_ref for item in base.evidence
                ),
            )
        except (
            json.JSONDecodeError,
            RecursionError,
            ValidationError,
            ValueError,
        ) as error:
            raise ProviderOutputValidationError(
                safe_validation_error_from_exception(error)
            ) from error

    @staticmethod
    def _validate_b0(
        diagnosis: RCA100InitialDiagnosis, *, base: CompactBaseContext
    ) -> None:
        visible_evidence = {item.evidence_ref for item in base.evidence}
        cited = set(diagnosis.evidence_refs) | {
            ref for step in diagnosis.reasoning_steps for ref in step.evidence_refs
        }
        if not cited.issubset(visible_evidence):
            raise ValueError("B0 diagnosis cited non-visible evidence")
        visible_entities = {item.entity_ref for item in base.entities}
        if diagnosis.root_cause_entity_ref not in visible_entities:
            raise ValueError("B0 diagnosis root is not visible")
        if any(
            step.entity_ref_or_none is not None
            and step.entity_ref_or_none not in visible_entities
            for step in diagnosis.reasoning_steps
        ):
            raise ValueError("B0 reasoning cited a non-visible entity")


__all__ = [
    "B0_SYSTEM_PROMPT",
    "C1_SYSTEM_PROMPT",
    "OpenAICompatibleCompactProvider",
    "build_request_payload",
    "estimate_input_tokens",
    "prompt_hashes",
]
