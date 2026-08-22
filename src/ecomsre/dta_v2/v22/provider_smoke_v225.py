"""Bounded real-Provider and deterministic protocol smoke for DTA v2.2.5."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Literal

from pydantic import StrictInt, model_validator

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    load_frozen_predicate_yield_priors_v223,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    AmbiguityBundleCaseRunV225,
    StudyCombinationV225,
    execute_ambiguity_bundle_case_v225,
)
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.offline_simulation_v225 import (
    simulate_fail_closed_contracts_v225,
)
from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    ProviderIdentityLintReportV225,
)
from ecomsre.dta_v2.v22.provider_payload_lint_report_v225 import (
    build_provider_payload_lint_report_v225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    load_replay_target_coverage_set_v225,
)
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionAliasTableV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.selection_provider_v225 import SelectionProviderV225
from ecomsre.dta_v2.v22.simple_provider import ProviderTransportErrorV22
from ecomsre.model.gateway import OpenAICompatibleConfig


SMOKE_SCHEDULE_V225: tuple[tuple[str, StudyCombinationV225], ...] = (
    ("d01", StudyCombinationV225.TARGET_ONE),
    ("d02", StudyCombinationV225.TARGET_SET),
    ("d03", StudyCombinationV225.BUNDLE_ONE),
    ("d04", StudyCombinationV225.BUNDLE_SET),
    ("d05", StudyCombinationV225.TARGET_ONE),
    ("d06", StudyCombinationV225.TARGET_SET),
    ("d07", StudyCombinationV225.BUNDLE_ONE),
    ("d08", StudyCombinationV225.BUNDLE_SET),
    ("d09", StudyCombinationV225.TARGET_SET),
    ("d10", StudyCombinationV225.BUNDLE_SET),
    ("d11", StudyCombinationV225.TARGET_SET),
    ("d12", StudyCombinationV225.BUNDLE_SET),
    ("d13", StudyCombinationV225.TARGET_ONE),
    ("d14", StudyCombinationV225.TARGET_SET),
    ("d15", StudyCombinationV225.BUNDLE_ONE),
    ("d16", StudyCombinationV225.BUNDLE_SET),
)


class ProviderSmokeReportV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.provider-smoke.v1"]
    recorded_at_utc: str
    provider_model: str
    real_runs: Literal[16]
    combinations: tuple[StudyCombinationV225, ...]
    provider_turns: StrictInt
    provider_calls: StrictInt
    protocol_repairs: StrictInt
    transport_retries: StrictInt
    post_repair_protocol_success_rate: float
    valid_terminal_runs: StrictInt
    coverage: dict[str, bool]
    rendered_lint_reports: tuple[ProviderIdentityLintReportV225, ...]
    runs: tuple[AmbiguityBundleCaseRunV225, ...]
    uncaught_exceptions: StrictInt
    agent_writes: Literal[0]
    gate_passed: bool

    @model_validator(mode="after")
    def require_smoke_gate(self) -> "ProviderSmokeReportV225":
        expected_gate = (
            self.real_runs == 16
            and self.valid_terminal_runs == 16
            and self.post_repair_protocol_success_rate >= 0.90
            and self.uncaught_exceptions == 0
            and self.agent_writes == 0
            and all(self.coverage.values())
        )
        if self.gate_passed != expected_gate:
            raise ValueError("v2.2.5 Provider smoke gate differs")
        return self


class _RecordingTransportV225:
    def __init__(self, responses: list[Mapping[str, object] | Exception]) -> None:
        self.responses = list(responses)
        self.payloads: list[Mapping[str, object]] = []

    def post_json(self, **kwargs: object) -> Mapping[str, object]:
        self.payloads.append(kwargs["payload"])  # type: ignore[arg-type]
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(selection: str, focus: str) -> Mapping[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_dta_selection",
                                "arguments": json.dumps(
                                    {"selection": selection, "focus": focus}
                                ),
                            }
                        }
                    ]
                }
            }
        ]
    }


def _protocol_request() -> SelectionTurnRequestV222:
    return SelectionTurnRequestV222.build(
        system_prompt="read-only opaque v2.2.5 protocol smoke",
        aliases=SelectionAliasTableV222.build(
            hypothesis_ids=("hypothesis:opaque",),
            action_ids=("action:opaque",),
            terminal_ids=("terminal:no-incident",),
            evidence_refs=(),
        ),
        visible_state={
            "candidate_services": ["svc-071d758b9b", "svc-136fa531b7"],
            "actions": ["A00"],
            "terminals": ["T00"],
        },
    )


def run_protocol_smoke_simulations_v225() -> dict[str, bool]:
    config = OpenAICompatibleConfig(
        base_url="https://provider.invalid/v1",
        api_key="simulation-only-not-sent",
        model="gpt-5.4-mini-2026-03-17",
    )
    request = _protocol_request()

    def provider(responses: list[Mapping[str, object] | Exception]):
        transport = _RecordingTransportV225(responses)
        return (
            SelectionProviderV225(
                config=config,
                transport=transport,
                sleeper=lambda _: None,
                minimum_request_interval_seconds=0,
            ),
            transport,
        )

    invalid, _ = provider([_response("BAD", "H00"), _response("T00", "NONE")])
    invalid_outcome = invalid.complete_turn(request=request, run_id="1" * 32)
    stale, _ = provider([_response("A99", "H00"), _response("A00", "H00")])
    stale_outcome = stale.complete_turn(request=request, run_id="2" * 32)
    retry_errors: list[Mapping[str, object] | Exception] = [
        ProviderTransportErrorV22("HTTP_429", status_code=429),
        ProviderTransportErrorV22("TIMEOUTERROR"),
        ProviderTransportErrorV22("CONNECTION_ERROR"),
        _response("T00", "NONE"),
    ]
    retried, retry_transport = provider(retry_errors)
    retry_outcome = retried.complete_turn(request=request, run_id="3" * 32)
    return {
        "invalid_alias_repair": invalid_outcome.protocol_repairs == 1,
        "stale_alias_repair": stale_outcome.protocol_repairs == 1,
        "http_429_retry_simulation": retry_outcome.transport_retry_count == 3,
        "timeout_retry_simulation": retry_outcome.transport_retry_count == 3,
        "exact_request_retry_identity": len(retry_transport.payloads) == 4
        and all(item == retry_transport.payloads[0] for item in retry_transport.payloads),
    }


def run_provider_smoke_v225(
    *, repository_root: Path, provider_env_path: Path
) -> ProviderSmokeReportV225:
    values = load_private_provider_env(provider_env_path)
    provider = SelectionProviderV225(
        config=OpenAICompatibleConfig(
            base_url=values["ECOMSRE_LLM_BASE_URL"],
            api_key=values["ECOMSRE_LLM_API_KEY"],
            model=values["ECOMSRE_LLM_MODEL"],
        ),
        minimum_request_interval_seconds=4.0,
        timeout_seconds=120.0,
        debug_root=repository_root / ".local/dta-v22-5-smoke-debug",
    )
    development = repository_root / "config/dta-v22-5/development"
    cases = load_practical_case_set_v22(development / "cases.json")
    by_id = {item.case_id: item for item in cases.cases}
    coverage = load_replay_target_coverage_set_v225(development / "coverage.json")
    priors = load_frozen_predicate_yield_priors_v223(
        repository_root / "config/dta-v22-3/development-predicate-yield-prior.json"
    )
    runs = tuple(
        execute_ambiguity_bundle_case_v225(
            spec=by_id[case_id],
            coverage=coverage.require(case_id),
            repository_root=repository_root,
            combination=combination,
            provider=provider,
            predicate_yield_priors=priors,
        )
        for case_id, combination in SMOKE_SCHEDULE_V225
    )
    simulation = run_protocol_smoke_simulations_v225()
    fail_closed = simulate_fail_closed_contracts_v225()
    lint = build_provider_payload_lint_report_v225(repository_root=repository_root)
    provider_turns = sum(item.provider_calls > 0 for item in runs)
    post_successes = sum(item.post_repair_protocol_successes for item in runs)
    valid = sum(item.status.value == "VALID_TERMINAL" for item in runs)
    coverage_flags = {
        "opaque_bootstrap_payload": "bootstrap" in lint.rendered_payload_classes,
        "opaque_post_individual_read_payload": any(
            item.payload_class == "post-individual-read"
            for item in provider.identity_lint_reports
        ),
        "opaque_post_bundle_read_payload": any(
            item.payload_class == "post-bundle-read"
            for item in provider.identity_lint_reports
        ),
        "opaque_terminal_only_payload": any(
            item.payload_class == "terminal-only"
            for item in provider.identity_lint_reports
        ),
        "opaque_repair_payload": "repair" in lint.rendered_payload_classes,
        "target_one": any(item.combination is StudyCombinationV225.TARGET_ONE for item in runs),
        "target_set": any(item.combination is StudyCombinationV225.TARGET_SET for item in runs),
        "bundle_one": any(item.combination is StudyCombinationV225.BUNDLE_ONE for item in runs),
        "bundle_set": any(item.combination is StudyCombinationV225.BUNDLE_SET for item in runs),
        "budget_insufficient_abstain": fail_closed[
            "budget_insufficient_typed_abstain"
        ],
        "source_failure_abstain": fail_closed["source_failure_typed_abstain"],
        "complete_normal_no_incident": runs[8].terminal == "NO_INCIDENT"
        and runs[9].terminal == "NO_INCIDENT",
        "incident_terminal": any(item.mechanism is not None for item in runs),
        "opaque_payload_lint_pass": lint.terminal == "OPAQUE_PROVIDER_IDENTITY_LINT_PASS",
        **simulation,
    }
    success_rate = 1.0 if provider_turns == 0 else post_successes / provider_turns
    return ProviderSmokeReportV225(
        schema_version="dta-v22.5.provider-smoke.v1",
        recorded_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        provider_model=provider.config.model,
        real_runs=16,
        combinations=tuple(StudyCombinationV225),
        provider_turns=provider_turns,
        provider_calls=sum(item.provider_calls for item in runs),
        protocol_repairs=sum(item.protocol_repairs for item in runs),
        transport_retries=sum(item.transport_retry_count for item in runs),
        post_repair_protocol_success_rate=success_rate,
        valid_terminal_runs=valid,
        coverage=coverage_flags,
        rendered_lint_reports=provider.identity_lint_reports,
        runs=runs,
        uncaught_exceptions=sum(item.uncaught_exceptions for item in runs),
        agent_writes=0,
        gate_passed=(
            success_rate >= 0.90
            and valid == 16
            and not any(item.uncaught_exceptions for item in runs)
            and all(coverage_flags.values())
        ),
    )


def write_provider_smoke_v225(
    *, repository_root: Path, provider_env_path: Path, output_path: Path
) -> ProviderSmokeReportV225:
    report = run_provider_smoke_v225(
        repository_root=repository_root, provider_env_path=provider_env_path
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(report.model_dump(mode="json"), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


__all__ = (
    "ProviderSmokeReportV225",
    "SMOKE_SCHEDULE_V225",
    "run_protocol_smoke_simulations_v225",
    "run_provider_smoke_v225",
    "write_provider_smoke_v225",
)
