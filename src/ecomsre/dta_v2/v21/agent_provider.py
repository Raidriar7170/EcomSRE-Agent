"""Strict OpenAI-compatible Provider boundary for DTA v2.1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import time
from typing import Any, Literal, cast

from pydantic import Field, StrictInt, ValidationError, model_validator

from ecomsre.dta_v2.agent_contracts import ProviderUsage
from ecomsre.dta_v2.agent_provider import (
    _canonical_json,
    _contains_forbidden_raw_key,
    _contains_forbidden_reasoning,
    _contains_secret_material,
    _mapping,
    _one,
    _parse_arguments,
)
from ecomsre.dta_v2.tool_contracts import (
    LogicalService,
    MetricKind,
    ReadToolRequest,
    ToolName,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.v21.agent_contracts import (
    ActionSelectionDecisionV21,
    AgentArmV21,
    AgentIdentityManifestV21,
    AlertContextV21,
    CandidateActionViewV21,
)
from ecomsre.dta_v2.v21.contracts import (
    DtaDiagnosisV21,
    DtaModelV21,
    EvidenceSourceV21,
    ResolvedDiagnosisEvidenceViewV21,
    evidence_source_from_ref,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.identity import build_three_arm_identities_v21
from ecomsre.dta_v2.v21.planner_contracts import (
    DiagnosticHypothesisV21,
    EvidencePlanDecisionV21,
    PlannerNextStepV21,
    build_evidence_plan_decision_v21,
)
from ecomsre.dta_v2.v21.prompts import (
    ACTION_SELECTION_SYSTEM_PROMPT_V21,
    FLAT_ADAPTIVE_SYSTEM_PROMPT_V21,
    ONE_SHOT_SYSTEM_PROMPT_V21,
    PLANNER_SYSTEM_PROMPT_V21,
)
from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    ProviderProtocolError,
    StdlibOpenAICompatibleTransport,
    _contains_credential,
    _parse_usage,
    _require_bounded_json,
)


PLANNER_FUNCTION_V21 = "submit_dta_v21_evidence_plan"
FLAT_FUNCTION_V21 = "submit_dta_v21_flat_turn"
DIAGNOSIS_FUNCTION_V21 = "submit_dta_v21_diagnosis"
ACTION_SELECTION_FUNCTION_V21 = "submit_dta_v21_action_selection"


class ProviderQueryMetricsV21(DtaModelV21):
    tool: Literal[ToolName.QUERY_METRICS]
    service: LogicalService
    metric_kinds: tuple[MetricKind, ...] = Field(min_length=1, max_length=6)
    max_results: StrictInt = Field(ge=1, le=12)


class ProviderSearchLogsV21(DtaModelV21):
    tool: Literal[ToolName.SEARCH_LOGS]
    service: LogicalService
    max_records: StrictInt = Field(ge=1, le=20)


class ProviderTraceNeighborhoodV21(DtaModelV21):
    tool: Literal[ToolName.QUERY_TRACE_NEIGHBORHOOD]
    service: LogicalService
    max_spans: StrictInt = Field(ge=1, le=40)


class ProviderInspectRuntimeV21(DtaModelV21):
    tool: Literal[ToolName.INSPECT_SERVICE_RUNTIME]
    services: tuple[LogicalService, ...] = Field(min_length=1, max_length=8)
    max_results: StrictInt = Field(ge=1, le=8)

    @model_validator(mode="after")
    def require_capacity(self) -> ProviderInspectRuntimeV21:
        if self.max_results < len(self.services):
            raise ValueError("runtime result capacity is insufficient")
        return self


class ProviderInspectResourcesV21(DtaModelV21):
    tool: Literal[ToolName.INSPECT_RESOURCE_USAGE]
    services: tuple[LogicalService, ...] = Field(min_length=1, max_length=8)
    sampling_window_seconds: StrictInt = Field(ge=1, le=30)
    sample_count: StrictInt = Field(ge=2, le=10)


ProviderReadRequestV21 = (
    ProviderQueryMetricsV21
    | ProviderSearchLogsV21
    | ProviderTraceNeighborhoodV21
    | ProviderInspectRuntimeV21
    | ProviderInspectResourcesV21
)


class PlannerProviderOutputV21(DtaModelV21):
    turn_ordinal: StrictInt = Field(ge=1, le=5)
    hypotheses: tuple[DiagnosticHypothesisV21, ...] = Field(max_length=3)
    next_step: PlannerNextStepV21
    evidence_gap_sources: tuple[EvidenceSourceV21, ...] = Field(max_length=6)
    read_request: ProviderReadRequestV21 | None
    diagnosis: DtaDiagnosisV21 | None
    bounded_rationale: str = Field(min_length=1, max_length=1000)


class FlatProviderOutputV21(DtaModelV21):
    read_request: ProviderReadRequestV21 | None
    diagnosis: DtaDiagnosisV21 | None

    @model_validator(mode="after")
    def require_one_output(self) -> FlatProviderOutputV21:
        if (self.read_request is None) == (self.diagnosis is None):
            raise ValueError("flat Provider output is not exactly one semantic output")
        return self


@dataclass(frozen=True, slots=True)
class ProviderTurnV21:
    """One screened response with exactly one admitted semantic output."""

    function_name: str
    tool_call_id: str
    raw_response_sha256: str
    usage: ProviderUsage
    monotonic_latency_ms: int
    plan_decision: EvidencePlanDecisionV21 | None = None
    read_request: ReadToolRequest | None = None
    diagnosis: DtaDiagnosisV21 | None = None
    action_selection: ActionSelectionDecisionV21 | None = None

    def __post_init__(self) -> None:
        if not self.function_name.strip() or not self.tool_call_id.strip():
            raise ValueError("Provider turn identity must be nonempty")
        if len(self.raw_response_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.raw_response_sha256
        ):
            raise ValueError("Provider response digest is invalid")
        if self.monotonic_latency_ms < 0:
            raise ValueError("Provider latency must be nonnegative")
        outputs = sum(
            item is not None
            for item in (
                self.plan_decision,
                self.read_request,
                self.diagnosis,
                self.action_selection,
            )
        )
        if outputs != 1:
            raise ValueError("Provider turn must carry exactly one semantic output")


_SOURCE_ORDER = {source: index for index, source in enumerate(EvidenceSourceV21)}
_PROMPT_BY_ARM = {
    AgentArmV21.ONE_SHOT_FULL_CONTEXT: ONE_SHOT_SYSTEM_PROMPT_V21,
    AgentArmV21.FLAT_ADAPTIVE: FLAT_ADAPTIVE_SYSTEM_PROMPT_V21,
    AgentArmV21.EVIDENCE_GUIDED_PLANNER: PLANNER_SYSTEM_PROMPT_V21,
}


def _definition(name: str, description: str, model: type[DtaModelV21]) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            # The configured OpenAI-compatible endpoint applies OpenAI strict-mode
            # schema rules that reject nullable Pydantic fields unless every field
            # is marked required. Runtime parsing below remains exact and fail-closed.
            "strict": False,
            "parameters": model.model_json_schema(mode="validation"),
        },
    }


def _safe_validation_codes_v21(
    error: Exception, *, model: type[DtaModelV21]
) -> tuple[str, ...]:
    """Return bounded schema-owned locations and Pydantic error types only."""

    if not isinstance(error, ValidationError):
        return (f"output:{type(error).__name__}",)
    allowed_roots = frozenset(model.model_fields)
    codes: set[str] = set()
    for item in error.errors(include_input=False, include_url=False):
        location = item.get("loc")
        root = location[0] if isinstance(location, tuple) and location else None
        safe_root = root if isinstance(root, str) and root in allowed_roots else "output"
        kind = item.get("type")
        safe_kind = kind if isinstance(kind, str) else "validation_error"
        codes.add(f"{safe_root}:{safe_kind}")
    return tuple(sorted(codes))[:16] or ("output:validation_error",)


def _sort_evidence_refs(values: object) -> object:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        return values
    try:
        return sorted(
            values,
            key=lambda ref: (_SOURCE_ORDER[evidence_source_from_ref(ref)], ref),
        )
    except (KeyError, ValueError):
        return values


def _canonicalize_diagnosis(value: object) -> object:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if result.get("terminal") != "COMPLETED":
        for field in (
            "root_service",
            "root_entity_ref",
            "fault_domain",
            "mechanism",
            "confidence",
        ):
            result[field] = None
    elif isinstance(result.get("root_service"), str):
        result["root_entity_ref"] = f"service:{result['root_service']}"
    result["supporting_evidence_refs"] = _sort_evidence_refs(
        result.get("supporting_evidence_refs")
    )
    result["contradicting_evidence_refs"] = _sort_evidence_refs(
        result.get("contradicting_evidence_refs")
    )
    sources = result.get("evidence_source_types")
    if isinstance(sources, list) and all(isinstance(item, str) for item in sources):
        try:
            result["evidence_source_types"] = sorted(
                sources, key=lambda item: _SOURCE_ORDER[EvidenceSourceV21(item)]
            )
        except (KeyError, ValueError):
            pass
    return result


def _canonicalize_planner_output(value: object) -> object:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    hypotheses = result.get("hypotheses")
    if isinstance(hypotheses, list):
        canonical: list[object] = []
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                canonical.append(hypothesis)
                continue
            item = dict(hypothesis)
            item["supporting_evidence_refs"] = _sort_evidence_refs(
                item.get("supporting_evidence_refs")
            )
            item["contradicting_evidence_refs"] = _sort_evidence_refs(
                item.get("contradicting_evidence_refs")
            )
            if item.get("status") == "REJECTED":
                item["unresolved_evidence_sources"] = []
            gaps = item.get("unresolved_evidence_sources")
            if isinstance(gaps, list):
                try:
                    item["unresolved_evidence_sources"] = sorted(
                        gaps,
                        key=lambda source: _SOURCE_ORDER[EvidenceSourceV21(source)],
                    )
                except (KeyError, ValueError):
                    pass
            canonical.append(item)
        def hypothesis_id(item: object) -> str:
            if not isinstance(item, dict):
                return ""
            value = cast(dict[str, object], item).get("hypothesis_id", "")
            return value if isinstance(value, str) else ""

        result["hypotheses"] = sorted(canonical, key=hypothesis_id)
    gaps = result.get("evidence_gap_sources")
    if isinstance(gaps, list):
        try:
            result["evidence_gap_sources"] = sorted(
                gaps, key=lambda source: _SOURCE_ORDER[EvidenceSourceV21(source)]
            )
        except (KeyError, ValueError):
            pass
    if result.get("diagnosis") is not None:
        result["diagnosis"] = _canonicalize_diagnosis(result["diagnosis"])
    return result


class OpenAICompatibleDtaAgentProviderV21:
    """Issue strict no-retry semantic turns for one frozen v2.1 arm identity."""

    def __init__(
        self,
        *,
        arm: AgentArmV21,
        config: OpenAICompatibleConfig,
        timeout_seconds: float,
        max_completion_tokens: int,
        transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        if not isinstance(arm, AgentArmV21):
            raise TypeError("arm must be AgentArmV21")
        if not isinstance(config, OpenAICompatibleConfig):
            raise TypeError("config must be OpenAICompatibleConfig")
        if type(timeout_seconds) is not float or timeout_seconds <= 0:
            raise ValueError("Provider timeout must be a positive float")
        if type(max_completion_tokens) is not int or max_completion_tokens <= 0:
            raise ValueError("Provider completion limit must be positive")
        self._arm = arm
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._max_completion_tokens = max_completion_tokens
        self._transport = transport or StdlibOpenAICompatibleTransport()
        self._attempted_calls = 0
        self._accepted_calls: list[ProviderTurnV21] = []
        self._raw_response_sha256_by_attempt: list[str | None] = []
        self._identity = next(
            item
            for item in build_three_arm_identities_v21(
                model_id=config.model,
                max_completion_tokens=max_completion_tokens,
            )
            if item.arm is arm
        )

    @property
    def identity(self) -> AgentIdentityManifestV21:
        return self._identity

    @property
    def attempted_calls(self) -> int:
        return self._attempted_calls

    @property
    def raw_response_sha256_by_attempt(self) -> tuple[str | None, ...]:
        """Return one safe digest slot per transport attempt, including rejects."""

        return tuple(self._raw_response_sha256_by_attempt)

    @property
    def accepted_calls(self) -> tuple[ProviderTurnV21, ...]:
        return tuple(self._accepted_calls)

    def investigation_turn(
        self,
        *,
        context: AlertContextV21,
        visible_state: object,
        read_tools_enabled: bool,
    ) -> ProviderTurnV21:
        context = AlertContextV21.model_validate(context.model_dump(mode="python"))
        if type(read_tools_enabled) is not bool:
            raise TypeError("read_tools_enabled must be bool")
        if not hasattr(visible_state, "model_dump"):
            raise TypeError("Provider visible state must be a typed model")
        visible = cast(Any, visible_state).model_dump(mode="json")
        if self._arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER:
            function_name = PLANNER_FUNCTION_V21
            output_model: type[DtaModelV21] = PlannerProviderOutputV21
            definition = _definition(
                function_name,
                "Submit one evidence-guided plan decision and its admitted output.",
                output_model,
            )
        elif self._arm is AgentArmV21.FLAT_ADAPTIVE:
            function_name = FLAT_FUNCTION_V21
            output_model = FlatProviderOutputV21
            definition = _definition(
                function_name,
                "Submit one flat read request or one final Diagnosis.",
                output_model,
            )
        else:
            function_name = DIAGNOSIS_FUNCTION_V21
            output_model = DtaDiagnosisV21
            definition = _definition(
                function_name,
                "Submit exactly one Diagnosis from the frozen full context.",
                output_model,
            )
        arguments, raw_sha, tool_call_id, usage, latency = self._complete(
            system_prompt=_PROMPT_BY_ARM[self._arm],
            visible_input=visible,
            definition=definition,
            expected_name=function_name,
        )
        try:
            if self._arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER:
                parsed = PlannerProviderOutputV21.model_validate_json(
                    _canonical_json(_canonicalize_planner_output(arguments))
                )
                request = (
                    None
                    if parsed.read_request is None
                    else self._build_read_request(parsed.read_request, context)
                )
                if not read_tools_enabled and request is not None:
                    raise ProviderProtocolError("Provider requested evidence after budget")
                plan = build_evidence_plan_decision_v21(
                    run_id=context.run_id,
                    turn_ordinal=parsed.turn_ordinal,
                    hypotheses=parsed.hypotheses,
                    next_step=parsed.next_step,
                    evidence_gap_sources=parsed.evidence_gap_sources,
                    read_request=request,
                    diagnosis=parsed.diagnosis,
                    bounded_rationale=parsed.bounded_rationale,
                )
                turn = ProviderTurnV21(
                    function_name=function_name,
                    tool_call_id=tool_call_id,
                    raw_response_sha256=raw_sha,
                    usage=usage,
                    monotonic_latency_ms=latency,
                    plan_decision=plan,
                )
            elif self._arm is AgentArmV21.FLAT_ADAPTIVE:
                parsed_flat = FlatProviderOutputV21.model_validate_json(
                    _canonical_json(
                        {
                            **arguments,
                            "diagnosis": _canonicalize_diagnosis(
                                arguments.get("diagnosis")
                            ),
                        }
                    )
                )
                request = (
                    None
                    if parsed_flat.read_request is None
                    else self._build_read_request(parsed_flat.read_request, context)
                )
                if not read_tools_enabled and request is not None:
                    raise ProviderProtocolError("Provider requested evidence after budget")
                turn = ProviderTurnV21(
                    function_name=function_name,
                    tool_call_id=tool_call_id,
                    raw_response_sha256=raw_sha,
                    usage=usage,
                    monotonic_latency_ms=latency,
                    read_request=request,
                    diagnosis=parsed_flat.diagnosis,
                )
            else:
                diagnosis = DtaDiagnosisV21.model_validate_json(
                    _canonical_json(_canonicalize_diagnosis(arguments))
                )
                turn = ProviderTurnV21(
                    function_name=function_name,
                    tool_call_id=tool_call_id,
                    raw_response_sha256=raw_sha,
                    usage=usage,
                    monotonic_latency_ms=latency,
                    diagnosis=diagnosis,
                )
        except ProviderProtocolError:
            raise
        except (TypeError, ValidationError, ValueError) as error:
            codes = ",".join(
                _safe_validation_codes_v21(error, model=output_model)
            )
            raise ProviderProtocolError(
                f"Provider investigation output is invalid [codes={codes}]"
            ) from error
        self._accepted_calls.append(turn)
        return turn

    def action_selection_turn(
        self,
        *,
        diagnosis: DtaDiagnosisV21,
        resolved_evidence: ResolvedDiagnosisEvidenceViewV21,
        candidate_view: CandidateActionViewV21,
    ) -> ProviderTurnV21:
        diagnosis = DtaDiagnosisV21.model_validate(diagnosis.model_dump(mode="python"))
        resolved_evidence = ResolvedDiagnosisEvidenceViewV21.model_validate(
            resolved_evidence.model_dump(mode="python")
        )
        candidate_view = CandidateActionViewV21.model_validate(
            candidate_view.model_dump(mode="python")
        )
        arguments, raw_sha, tool_call_id, usage, latency = self._complete(
            system_prompt=ACTION_SELECTION_SYSTEM_PROMPT_V21,
            visible_input={
                "diagnosis": diagnosis.model_dump(mode="json"),
                "resolved_evidence": resolved_evidence.model_dump(mode="json"),
                "candidate_view": candidate_view.model_dump(mode="json"),
            },
            definition=_definition(
                ACTION_SELECTION_FUNCTION_V21,
                "Select one exact visible action candidate or non-write disposition.",
                ActionSelectionDecisionV21,
            ),
            expected_name=ACTION_SELECTION_FUNCTION_V21,
        )
        try:
            normalized = dict(arguments)
            normalized["supporting_evidence_refs"] = _sort_evidence_refs(
                normalized.get("supporting_evidence_refs")
            )
            decision = ActionSelectionDecisionV21.model_validate_json(
                _canonical_json(normalized)
            )
        except (TypeError, ValidationError, ValueError) as error:
            codes = ",".join(
                _safe_validation_codes_v21(
                    error, model=ActionSelectionDecisionV21
                )
            )
            raise ProviderProtocolError(
                f"Provider Action Selection is invalid [codes={codes}]"
            ) from error
        turn = ProviderTurnV21(
            function_name=ACTION_SELECTION_FUNCTION_V21,
            tool_call_id=tool_call_id,
            raw_response_sha256=raw_sha,
            usage=usage,
            monotonic_latency_ms=latency,
            action_selection=decision,
        )
        self._accepted_calls.append(turn)
        return turn

    def _complete(
        self,
        *,
        system_prompt: str,
        visible_input: Mapping[str, object],
        definition: dict[str, object],
        expected_name: str,
    ) -> tuple[dict[str, Any], str, str, ProviderUsage, int]:
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _canonical_json(visible_input)},
            ],
            "temperature": 0.0,
            "n": 1,
            "parallel_tool_calls": False,
            "max_completion_tokens": self._max_completion_tokens,
            "tool_choice": {
                "type": "function",
                "function": {"name": expected_name},
            },
            "tools": [definition],
        }
        self._attempted_calls += 1
        self._raw_response_sha256_by_attempt.append(None)
        started = time.monotonic_ns()
        try:
            raw_value = self._transport.post_json(
                url=f"{self._config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except ProviderProtocolError:
            raise
        except TimeoutError:
            raise TimeoutError("DTA v2.1 Provider request timed out") from None
        except Exception:
            raise ConnectionError("DTA v2.1 Provider request failed") from None
        try:
            self._raw_response_sha256_by_attempt[-1] = semantic_sha256(raw_value)
        except (TypeError, ValueError) as error:
            raise ProviderProtocolError("Provider response is not hashable JSON") from error
        latency = max(0, (time.monotonic_ns() - started) // 1_000_000)
        response = _mapping(raw_value, "Provider response")
        _require_bounded_json(response)
        if _contains_credential(response, self._config.api_key) or _contains_secret_material(
            response, self._config.api_key
        ):
            raise ProviderProtocolError("Provider response contains credential material")
        if _contains_forbidden_reasoning(response):
            raise ProviderProtocolError("Provider response contains private reasoning")
        if _contains_forbidden_raw_key(response):
            raise ProviderProtocolError("Provider response contains private configuration")
        detached = cast(
            Mapping[str, object], json.loads(_canonical_json(response))
        )
        if detached.get("model") != self._config.model:
            raise ProviderProtocolError("Provider response model differs")
        response_id = detached.get("id")
        if not isinstance(response_id, str) or not response_id.strip():
            raise ProviderProtocolError("Provider response ID is invalid")
        choice = _mapping(_one(detached.get("choices"), "Provider choices"), "choice")
        if choice.get("index") != 0 or choice.get("finish_reason") != "tool_calls":
            raise ProviderProtocolError("Provider choice metadata is invalid")
        message = _mapping(choice.get("message"), "Provider message")
        if (
            message.get("role") != "assistant"
            or message.get("content") is not None
            or message.get("refusal") is not None
            or "function_call" in message
        ):
            raise ProviderProtocolError("Provider assistant message is invalid")
        tool_call = _mapping(
            _one(message.get("tool_calls"), "Provider tool calls"), "tool call"
        )
        if set(tool_call) != {"id", "type", "function"}:
            raise ProviderProtocolError("Provider tool-call fields are not exact")
        tool_call_id = tool_call.get("id")
        if (
            not isinstance(tool_call_id, str)
            or not tool_call_id.strip()
            or tool_call.get("type") != "function"
        ):
            raise ProviderProtocolError("Provider tool-call identity is invalid")
        function = _mapping(tool_call.get("function"), "Provider function")
        if set(function) != {"name", "arguments"} or function.get("name") != expected_name:
            raise ProviderProtocolError("Provider function is not the required function")
        arguments, _ = _parse_arguments(function.get("arguments"))
        if _contains_credential(arguments, self._config.api_key) or _contains_secret_material(
            arguments, self._config.api_key
        ):
            raise ProviderProtocolError("Provider arguments contain credential material")
        if _contains_forbidden_reasoning(arguments) or _contains_forbidden_raw_key(arguments):
            raise ProviderProtocolError("Provider arguments contain prohibited private fields")
        model_usage = _parse_usage(detached.get("usage"))
        if model_usage.output_tokens > self._max_completion_tokens:
            raise ProviderProtocolError("Provider completion exceeds limit")
        usage = ProviderUsage(
            input_tokens=model_usage.input_tokens,
            output_tokens=model_usage.output_tokens,
            total_tokens=model_usage.total_tokens,
        )
        return (
            arguments,
            semantic_sha256(detached),
            tool_call_id.strip(),
            usage,
            latency,
        )

    @staticmethod
    def _build_read_request(
        request: ProviderReadRequestV21, context: AlertContextV21
    ) -> ReadToolRequest:
        services = (
            (request.service,)
            if isinstance(
                request,
                (ProviderQueryMetricsV21, ProviderSearchLogsV21, ProviderTraceNeighborhoodV21),
            )
            else request.services
        )
        if not set(services).issubset(context.candidate_services):
            raise ProviderProtocolError("Provider requested an out-of-scope service")
        if request.tool not in context.allowed_read_tools:
            raise ProviderProtocolError("Provider requested an out-of-scope tool")
        if isinstance(request, ProviderQueryMetricsV21):
            return build_query_metrics_request(
                run_id=context.run_id,
                service=request.service,
                started_at=context.started_at,
                ended_at=context.ended_at,
                metric_kinds=request.metric_kinds,
                max_results=request.max_results,
            )
        if isinstance(request, ProviderSearchLogsV21):
            return build_search_logs_request(
                run_id=context.run_id,
                service=request.service,
                started_at=context.started_at,
                ended_at=context.ended_at,
                max_records=request.max_records,
            )
        if isinstance(request, ProviderTraceNeighborhoodV21):
            return build_trace_neighborhood_request(
                run_id=context.run_id,
                service=request.service,
                started_at=context.started_at,
                ended_at=context.ended_at,
                max_spans=request.max_spans,
            )
        if isinstance(request, ProviderInspectRuntimeV21):
            return build_inspect_service_runtime_request(
                run_id=context.run_id,
                services=request.services,
                max_results=request.max_results,
            )
        return build_inspect_resource_usage_request(
            run_id=context.run_id,
            services=request.services,
            sampling_window_seconds=request.sampling_window_seconds,
            sample_count=request.sample_count,
        )


__all__ = (
    "ACTION_SELECTION_FUNCTION_V21",
    "DIAGNOSIS_FUNCTION_V21",
    "FLAT_FUNCTION_V21",
    "OpenAICompatibleDtaAgentProviderV21",
    "PLANNER_FUNCTION_V21",
    "ProviderProtocolError",
    "ProviderTurnV21",
)
