"""Paired one-call Strong Single versus hierarchical Strong Single runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import random
from typing import Any, Literal, Mapping, Protocol

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
from ecomsre_rca_unified.hierarchical_context import (
    HierarchicalContext,
    LiveBaseContext,
)


EVALUATION_VERSION = "strong-single-hierarchical-live-dev-v1"
FUNCTION_NAME = "submit_strong_single_diagnosis"
B0_SYSTEM_PROMPT = SOURCE_B0_SYSTEM_PROMPT.replace(
    "rca100.initial-diagnosis", "strong-single.diagnosis"
)
if B0_SYSTEM_PROMPT == SOURCE_B0_SYSTEM_PROMPT:
    raise ValueError("Strong Single prompt identity neutralization did not apply")
H1_GUIDANCE = (
    " Distinguish the most anomalous symptom from the causal root. Respect "
    "entity hierarchy and service ancestry. Use propagation relations when "
    "available. Do not select an operation or pod merely because it has the "
    "strongest metric. Return one visible root-eligible entity. Use the generic "
    "fault ontology: local resource, network, dependency, propagation, "
    "application, or unknown; never infer a benchmark label."
)
H1_SYSTEM_PROMPT = B0_SYSTEM_PROMPT + H1_GUIDANCE


def _private_payload_markers() -> tuple[str, ...]:
    """Return deny-only markers; no value is read from an incident source."""

    return (
        "rca100",
        "re2-ob",
        "re2-ss",
        "task_id",
        "case_id",
        "ground_truth",
        "root_cause_service",
    )


class Arm(str, Enum):
    B0 = "B0"
    H1 = "H1"


@dataclass(frozen=True, slots=True)
class CaseRef:
    source: str
    source_key: str

    def __post_init__(self) -> None:
        if not self.source or not self.source_key:
            raise ValueError("case reference contains an empty field")


@dataclass(frozen=True, slots=True)
class ScheduledArm:
    split: Literal["TUNE", "REGRESSION", "PREFLIGHT"]
    pair_position: int
    arm_position: int
    opaque_case_id: str
    source: str
    source_key: str
    arm: Arm
    run_id: str


def paired_schedule(
    cases: tuple[CaseRef, ...], *, seed: int, split: Literal["TUNE", "REGRESSION"]
) -> tuple[ScheduledArm, ...]:
    if not cases or len(set(cases)) != len(cases):
        raise ValueError("paired schedule cases must be unique and nonempty")
    shuffled = list(cases)
    random.Random(seed).shuffle(shuffled)
    output: list[ScheduledArm] = []
    for pair_position, case in enumerate(shuffled, 1):
        opaque_case_id = "case-" + hashlib.sha256(
            b"\0".join(
                (
                    EVALUATION_VERSION.encode(),
                    split.encode(),
                    str(seed).encode(),
                    case.source.encode(),
                    case.source_key.encode(),
                )
            )
        ).hexdigest()[:20]
        arms = (Arm.B0, Arm.H1) if pair_position % 2 else (Arm.H1, Arm.B0)
        for arm_position, arm in enumerate(arms, 1):
            run_id = hashlib.sha256(
                b"\0".join(
                    (
                        EVALUATION_VERSION.encode(),
                        split.encode(),
                        opaque_case_id.encode(),
                        arm.value.encode(),
                    )
                )
            ).hexdigest()[:32]
            output.append(
                ScheduledArm(
                    split=split,
                    pair_position=pair_position,
                    arm_position=arm_position,
                    opaque_case_id=opaque_case_id,
                    source=case.source,
                    source_key=case.source_key,
                    arm=arm,
                    run_id=run_id,
                )
            )
    if len(cases) > 1 and all(item.arm is Arm.B0 for item in output[: len(cases)]):
        raise ValueError("paired schedule accidentally bulk-ordered an arm")
    return tuple(output)


class DiagnosisProvider(Protocol):
    @property
    def calls(self) -> int: ...

    def diagnose(
        self,
        *,
        base: LiveBaseContext,
        arm: Arm,
        hierarchy: HierarchicalContext | None,
    ) -> RCA100InitialDiagnosis: ...


@dataclass(frozen=True, slots=True)
class ArmResult:
    diagnosis: RCA100InitialDiagnosis
    model_calls: Literal[1] = 1
    specialist_calls: Literal[0] = 0
    fusion_calls: Literal[0] = 0


def _output_schema() -> dict[str, object]:
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
    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True).casefold()
    if any(marker in encoded for marker in _private_payload_markers()):
        raise ValueError("Strong Single output schema contains private identity")
    return schema


def build_request_payload(
    *,
    model: str,
    base: LiveBaseContext,
    arm: Arm,
    hierarchy: HierarchicalContext | None,
    max_completion_tokens: int,
) -> dict[str, object]:
    if max_completion_tokens <= 0:
        raise ValueError("live comparison completion budget must be positive")
    if (arm is Arm.H1) != (hierarchy is not None):
        raise ValueError("arm and hierarchical request context differ")
    envelope: dict[str, object] = {
        "schema_version": "strong-single-live.model-envelope.v1",
        "context": base.model_dump(mode="json"),
    }
    if hierarchy is not None:
        cards = tuple(hierarchy.entity_cards)
        card_indexes = {item.entity_ref: index for index, item in enumerate(cards)}
        entity_refs = tuple(
            dict.fromkeys(
                ref
                for item in cards
                for ref in (
                    item.entity_ref,
                    item.service_ancestor_or_none,
                    item.parent_ref_or_none,
                )
                if ref is not None
            )
        )
        entity_ref_indexes = {value: index for index, value in enumerate(entity_refs)}
        layers = tuple(dict.fromkeys(item.layer.value for item in cards))
        layer_indexes = {value: index for index, value in enumerate(layers)}
        alert_relations = tuple(
            dict.fromkeys(item.relation_to_alert for item in cards)
        )
        alert_relation_indexes = {
            value: index for index, value in enumerate(alert_relations)
        }
        source_bit_order = ("METRICS", "LOGS", "TRACES", "EVENTS", "ALERTS")
        source_indexes = {value: index for index, value in enumerate(source_bit_order)}
        propagation_relation_types = tuple(
            dict.fromkeys(item.relation_type for item in hierarchy.propagation_relations)
        )
        propagation_relation_type_indexes = {
            value: index for index, value in enumerate(propagation_relation_types)
        }
        envelope["hierarchical_context"] = {
            "schema_version": "strong-single-live.hierarchical-columnar.v2",
            "entity_card_columns": [
                "entity_ref",
                "layer",
                "service_ancestor_or_none",
                "parent_ref_or_none",
                "relation_to_alert",
                "topology_distance_or_none",
                "visible_sources",
                "first_anomaly_source_or_none",
            ],
            "entity_ref_dictionary": list(entity_refs),
            "layer_dictionary": list(layers),
            "relation_to_alert_dictionary": list(alert_relations),
            "visible_source_bit_order": list(source_bit_order),
            "entity_cards": [
                [
                    entity_ref_indexes[item.entity_ref],
                    layer_indexes[item.layer.value],
                    (
                        None
                        if item.service_ancestor_or_none is None
                        else entity_ref_indexes[item.service_ancestor_or_none]
                    ),
                    (
                        None
                        if item.parent_ref_or_none is None
                        else entity_ref_indexes[item.parent_ref_or_none]
                    ),
                    alert_relation_indexes[item.relation_to_alert],
                    item.topology_distance_or_none,
                    sum(1 << source_indexes[value] for value in item.visible_sources),
                    (
                        None
                        if item.first_anomaly_source_or_none is None
                        else source_indexes[item.first_anomaly_source_or_none]
                    ),
                ]
                for item in cards
            ],
            "root_eligible_card_indexes": [
                card_indexes[entity_ref]
                for entity_ref in hierarchy.root_eligible_entity_refs
            ],
            "propagation_relation_columns": [
                "source_card_index",
                "target_card_index",
                "relation_type",
            ],
            "propagation_relation_type_dictionary": list(
                propagation_relation_types
            ),
            "propagation_relations": [
                [
                    card_indexes[item.source_entity_ref],
                    card_indexes[item.target_entity_ref],
                    propagation_relation_type_indexes[item.relation_type],
                ]
                for item in hierarchy.propagation_relations
            ],
        }
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": B0_SYSTEM_PROMPT if arm is Arm.B0 else H1_SYSTEM_PROMPT,
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
        "tool_choice": {"type": "function", "function": {"name": FUNCTION_NAME}},
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": FUNCTION_NAME,
                    "description": "Return the one typed Strong Single diagnosis.",
                    "strict": False,
                    "parameters": _output_schema(),
                },
            }
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    if any(marker in encoded for marker in _private_payload_markers()):
        raise ValueError("model-facing request contains private identity metadata")
    return payload


def prompt_hashes() -> dict[str, str]:
    return {
        "b0_system_prompt_sha256": hashlib.sha256(
            B0_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "h1_system_prompt_sha256": hashlib.sha256(
            H1_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "output_schema_sha256": hashlib.sha256(
            canonical_json_bytes(_output_schema())
        ).hexdigest(),
    }


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


class OpenAICompatibleLiveComparisonProvider:
    """Exactly one schema-checked Strong Single operation per invocation."""

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
            raise ValueError("live comparison Provider model differs from lock")
        if timeout_seconds <= 0 or max_completion_tokens <= 0:
            raise ValueError("live comparison Provider budget is invalid")
        self._config = config
        self._timeout = float(timeout_seconds)
        self._max_completion_tokens = max_completion_tokens
        self._transport = transport or StdlibOpenAICompatibleTransport()
        self._calls = 0
        self._usage_total = 0
        self._usage_known = True
        self._last_request_sha256: str | None = None

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def last_usage_tokens(self) -> int | None:
        return self._usage_total if self._usage_known else None

    @property
    def usage_known(self) -> bool:
        return self._usage_known

    @property
    def last_request_sha256(self) -> str | None:
        return self._last_request_sha256

    def diagnose(
        self,
        *,
        base: LiveBaseContext,
        arm: Arm,
        hierarchy: HierarchicalContext | None,
    ) -> RCA100InitialDiagnosis:
        payload = build_request_payload(
            model=self._config.model,
            base=base,
            arm=arm,
            hierarchy=hierarchy,
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
            prompt_tokens, completion_tokens, total_tokens = tokens
            assert isinstance(prompt_tokens, int)
            assert isinstance(completion_tokens, int)
            assert isinstance(total_tokens, int)
            if prompt_tokens + completion_tokens != total_tokens:
                raise ProviderProtocolError("Provider usage total is inconsistent")
            if self._usage_known:
                self._usage_total += total_tokens
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
        if function.get("name") != FUNCTION_NAME:
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
            diagnosis = RCA100InitialDiagnosis.model_validate_json(
                json.dumps(
                    parsed,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        except (json.JSONDecodeError, RecursionError, ValidationError, ValueError) as error:
            raise ValueError("Provider diagnosis is invalid") from error
        validate_diagnosis(
            diagnosis,
            base=base,
            arm=arm,
            hierarchy=hierarchy,
        )
        return diagnosis


def validate_diagnosis(
    diagnosis: RCA100InitialDiagnosis,
    *,
    base: LiveBaseContext,
    arm: Arm,
    hierarchy: HierarchicalContext | None,
) -> None:
    if (arm is Arm.H1) != (hierarchy is not None):
        raise ValueError("arm and hierarchical context differ")
    visible_evidence = {item.evidence_ref for item in base.evidence}
    cited = set(diagnosis.evidence_refs) | {
        ref for step in diagnosis.reasoning_steps for ref in step.evidence_refs
    }
    if not cited.issubset(visible_evidence):
        raise ValueError("diagnosis cited non-visible evidence")
    visible_entities = {item.entity_ref for item in base.entities}
    if hierarchy is not None:
        allowed_roots = set(hierarchy.root_eligible_entity_refs)
        visible_entities |= {item.entity_ref for item in hierarchy.entity_cards}
        if diagnosis.root_cause_entity_ref not in allowed_roots:
            raise ValueError("H1 diagnosis root is not root-eligible")
    elif diagnosis.root_cause_entity_ref not in visible_entities:
        raise ValueError("B0 diagnosis root is not visible")
    if any(
        step.entity_ref_or_none is not None
        and step.entity_ref_or_none not in visible_entities
        for step in diagnosis.reasoning_steps
    ):
        raise ValueError("diagnosis reasoning cited a non-visible entity")


def execute_arm(
    *,
    base: LiveBaseContext,
    arm: Arm,
    hierarchy: HierarchicalContext | None,
    provider: DiagnosisProvider,
) -> ArmResult:
    before = provider.calls
    diagnosis = provider.diagnose(base=base, arm=arm, hierarchy=hierarchy)
    after = provider.calls
    if after - before != 1:
        raise ValueError("Strong Single arm did not make exactly one model call")
    validate_diagnosis(diagnosis, base=base, arm=arm, hierarchy=hierarchy)
    return ArmResult(diagnosis=diagnosis)


__all__ = [
    "Arm",
    "ArmResult",
    "B0_SYSTEM_PROMPT",
    "CaseRef",
    "EVALUATION_VERSION",
    "H1_SYSTEM_PROMPT",
    "OpenAICompatibleLiveComparisonProvider",
    "ScheduledArm",
    "build_request_payload",
    "execute_arm",
    "paired_schedule",
    "prompt_hashes",
    "validate_diagnosis",
]
