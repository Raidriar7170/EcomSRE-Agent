"""No-retry OpenAI-compatible Phase 4 Domain Judge and four-run smoke."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path

from ecomsre.backends.replay import load_replay_case
from ecomsre.model.gateway import (
    OpenAICompatibleConfig,
    OpenAICompatibleTransport,
    ProviderProtocolError,
    StdlibOpenAICompatibleTransport,
    _contains_credential,
    _parse_usage,
    _require_bounded_json,
)
from ecomsre.phase1.runtime_config import load_agent_settings
from ecomsre.phase2.provider import (
    PHASE2_PROVIDER_IDENTITY,
    OpenAICompatiblePhase2Backend,
    _parse_content,
    _require_mapping,
    _require_one,
)
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
from ecomsre.phase4.contracts import (
    DomainRCAResult,
    DomainVariant,
)


PHASE4_PROVIDER_IDENTITY = "openai-compatible"
_PROVIDER_ENVIRONMENT_NAMES = (
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_MODEL",
)
_SMOKE_CASES = (
    (
        "fixed_positive",
        DomainVariant.FIXED_SPECIALIST_WORKFLOW,
        "search-feature-freshness-lag-complete",
        "RCA_CONFIRMED",
    ),
    (
        "dynamic_positive",
        DomainVariant.DYNAMIC_MULTI_AGENT,
        "search-feature-freshness-lag-complete",
        "RCA_CONFIRMED",
    ),
    (
        "fixed_negative",
        DomainVariant.FIXED_SPECIALIST_WORKFLOW,
        "ranking-change-with-normal-search-sli",
        "ABSTAIN",
    ),
    (
        "dynamic_negative",
        DomainVariant.DYNAMIC_MULTI_AGENT,
        "ranking-change-with-normal-search-sli",
        "ABSTAIN",
    ),
)


@dataclass(frozen=True, slots=True)
class DomainProviderCompletion:
    result: DomainRCAResult
    provider_prompt_tokens: int
    output_tokens: int


class OpenAICompatibleDomainBackend:
    """Issue one strict DomainRCAResult tool call without retries."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        timeout_seconds: float,
        transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        if not isinstance(config, OpenAICompatibleConfig):
            raise TypeError("config must be OpenAICompatibleConfig")
        if config.model != MODEL_SNAPSHOT:
            raise ValueError("Phase 4 provider model must match Agent mainline")
        if type(timeout_seconds) is not float or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive float")
        self._config = config
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._calls = 0

    @property
    def provider_identity(self) -> str:
        return PHASE4_PROVIDER_IDENTITY

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def calls(self) -> int:
        return self._calls

    def complete(
        self,
        *,
        envelope: Mapping[str, object],
        max_completion_tokens: int,
    ) -> DomainProviderCompletion:
        if type(max_completion_tokens) is not int or max_completion_tokens <= 0:
            raise ProviderProtocolError("Domain completion limit is invalid")
        schema = DomainRCAResult.model_json_schema(mode="validation")
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return one exact phase4.domain-rca-result.v1 object. "
                        "Use only the supplied current-run evidence. Confirm only "
                        "one typed Feature or Ranking mechanism supported by an "
                        "anomalous metric and a non-metric source. A business "
                        "anomaly with insufficient mechanism support needs more "
                        "evidence; a normal business SLI requires abstention."
                    ),
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
            "n": 1,
            "parallel_tool_calls": False,
            "max_completion_tokens": max_completion_tokens,
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_phase4_domain_rca"},
            },
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_phase4_domain_rca",
                        "description": "Return the exact typed Phase 4 Domain RCA.",
                        "strict": False,
                        "parameters": schema,
                    },
                }
            ],
        }
        effective_transport = self._transport or StdlibOpenAICompatibleTransport()
        try:
            raw = effective_transport.post_json(
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
            raise TimeoutError("Phase 4 provider request timed out") from None
        except Exception:
            raise ConnectionError("Phase 4 provider request failed") from None
        self._calls += 1
        response = _require_mapping(raw, "provider response")
        _require_bounded_json(response)
        if _contains_credential(response, self._config.api_key):
            raise ProviderProtocolError("provider response contains credential material")
        if response.get("model") != self._config.model:
            raise ProviderProtocolError("provider response model is not frozen")
        response_id = response.get("id")
        if not isinstance(response_id, str) or not response_id.strip():
            raise ProviderProtocolError("provider response id is invalid")
        choice = _require_mapping(
            _require_one(response.get("choices"), "choices"),
            "choice",
        )
        if (
            type(choice.get("index")) is not int
            or choice.get("index") != 0
            or choice.get("finish_reason") != "tool_calls"
        ):
            raise ProviderProtocolError("provider choice metadata is invalid")
        message = _require_mapping(choice.get("message"), "message")
        if (
            message.get("role") != "assistant"
            or message.get("content") is not None
            or message.get("refusal") is not None
            or "tool_calls" not in message
            or "function_call" in message
        ):
            raise ProviderProtocolError("provider assistant message is invalid")
        tool_call = _require_mapping(
            _require_one(message.get("tool_calls"), "tool_calls"),
            "tool call",
        )
        if set(tool_call) != {"id", "type", "function"}:
            raise ProviderProtocolError("provider tool call fields are not exact")
        tool_call_id = tool_call.get("id")
        if (
            not isinstance(tool_call_id, str)
            or not tool_call_id.strip()
            or tool_call.get("type") != "function"
        ):
            raise ProviderProtocolError("provider tool call identity is invalid")
        function = _require_mapping(tool_call.get("function"), "function")
        if (
            set(function) != {"name", "arguments"}
            or function.get("name") != "submit_phase4_domain_rca"
        ):
            raise ProviderProtocolError("provider Domain tool call is invalid")
        result = DomainRCAResult.model_validate(_parse_content(function.get("arguments")))
        usage = _parse_usage(response.get("usage"))
        if usage.output_tokens > max_completion_tokens:
            raise ProviderProtocolError("provider completion exceeds the admitted limit")
        return DomainProviderCompletion(
            result=result,
            provider_prompt_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )


def run_provider_smoke(
    project_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    transport: OpenAICompatibleTransport | None = None,
) -> dict[str, object]:
    """Run the exact four provider gates or return a typed offline skip."""

    source = os.environ if environment is None else environment
    if all(name not in source for name in _PROVIDER_ENVIRONMENT_NAMES):
        return {
            "schema_version": "phase4.provider-smoke-report.v1",
            "status": "SKIPPED_NOT_CONFIGURED",
            "configured": False,
            "provider": PHASE4_PROVIDER_IDENTITY,
            "model": None,
            "run_count": 0,
            "scripted_fallback": False,
            "case_results": [],
        }
    config = OpenAICompatibleConfig.from_environment(source)
    if config is None:
        raise RuntimeError("complete provider configuration was not loaded")
    if config.model != MODEL_SNAPSHOT:
        raise ValueError("provider model must match the Agent mainline snapshot")
    root = Path(project_root).resolve(strict=True)
    settings = load_agent_settings(root)
    case_results: list[dict[str, object]] = []
    for requirement, variant, case_id, expected_decision in _SMOKE_CASES:
        phase2_backend = OpenAICompatiblePhase2Backend(
            config=config,
            timeout_seconds=float(settings.model_timeout_seconds),
            transport=transport,
        )
        domain_backend = OpenAICompatibleDomainBackend(
            config=config,
            timeout_seconds=float(settings.model_timeout_seconds),
            transport=transport,
        )
        from ecomsre.phase4.workflows import run_domain_replay_workflow

        trace = run_domain_replay_workflow(
            project_root=root,
            replay_case=load_replay_case(
                root / "config/phase4/replay-cases/agent-visible",
                case_id,
            ),
            variant=variant,
            phase2_model_backend=phase2_backend,
            expected_provider_identity=PHASE2_PROVIDER_IDENTITY,
            domain_backend=domain_backend,
        )
        final = trace.final_rca
        passed = (
            trace.status == "COMPLETED"
            and final is not None
            and final.decision.value == expected_decision
            and len(trace.domain_model_call_audits) == 1
            and domain_backend.calls == 1
        )
        if expected_decision == "RCA_CONFIRMED":
            passed = passed and (
                final is not None
                and final.root_service == "feature"
                and final.fault_mechanism is not None
                and final.fault_mechanism.value == "feature_freshness_lag"
                and bool(final.supporting_evidence)
            )
        else:
            passed = passed and (
                final is not None
                and final.root_service is None
                and final.fault_mechanism is None
            )
        case_results.append(
            {
                "requirement": requirement,
                "variant": variant.value,
                "case_id": case_id,
                "status": "PASSED" if passed else "FAILED",
                "decision": final.decision.value if final is not None else None,
                "root_service": final.root_service if final is not None else None,
                "fault_mechanism": (
                    final.fault_mechanism.value
                    if final is not None and final.fault_mechanism is not None
                    else None
                ),
                "phase2_provider_calls": phase2_backend.calls,
                "domain_provider_calls": domain_backend.calls,
                "usage": [
                    audit.model_dump(mode="json")
                    for audit in trace.domain_model_call_audits
                ],
            }
        )
    passed = all(item["status"] == "PASSED" for item in case_results)
    return {
        "schema_version": "phase4.provider-smoke-report.v1",
        "status": "PASSED" if passed else "FAILED",
        "configured": True,
        "provider": PHASE4_PROVIDER_IDENTITY,
        "model": config.model,
        "temperature": 0,
        "run_count": len(case_results),
        "scripted_fallback": False,
        "case_results": case_results,
    }
