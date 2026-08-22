"""Deterministic static and rendered-payload opaque-identity lint report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    load_frozen_predicate_yield_priors_v223,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V225,
    balanced_combination_order_v225,
    execute_ambiguity_bundle_case_v225,
)
from ecomsre.dta_v2.v22.offline_simulation_v223 import (
    _EvaluatorSelectionProviderV223,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    ProviderIdentityLintReportV225,
    lint_static_identity_surface_v225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    load_replay_target_coverage_set_v225,
)
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionAliasTableV222,
    SelectionProviderOutcomeV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.selection_provider_v225 import SelectionProviderV225
from ecomsre.model.gateway import OpenAICompatibleConfig


class OpaqueProviderPayloadLintAggregateV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.opaque-provider-payload-lint.v1"]
    terminal: Literal["OPAQUE_PROVIDER_IDENTITY_LINT_PASS"]
    evaluation_files_scanned: Literal[16]
    rendered_payload_classes: tuple[str, ...]
    evaluation_runs_rendered: Literal[64]
    runtime_payloads_rendered: Literal[64]
    synthetic_protocol_payloads_rendered: Literal[2]
    static_identity_values_scanned: int
    rendered_identity_values_scanned: int
    forbidden_identity_value_count: Literal[0]
    provider_case_id_count: Literal[0]
    provider_evaluator_metadata_field_count: Literal[0]
    static_reports: tuple[ProviderIdentityLintReportV225, ...]
    rendered_reports: tuple[ProviderIdentityLintReportV225, ...]
    report_sha256: str

    @model_validator(mode="after")
    def require_complete_lint(self) -> "OpaqueProviderPayloadLintAggregateV225":
        if self.rendered_payload_classes != (
            "bootstrap",
            "post-bundle-read",
            "post-individual-read",
            "repair",
            "terminal-only",
        ):
            raise ValueError("v2.2.5 rendered payload classes differ")
        if len(self.static_reports) != 16:
            raise ValueError("v2.2.5 static identity lint file count differs")
        if len(self.rendered_reports) != 66:
            raise ValueError("v2.2.5 rendered payload count differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.2.5 opaque lint aggregate digest differs")
        return self


class _RenderingOracleV225:
    def __init__(
        self,
        *,
        renderer: SelectionProviderV225,
        oracle: _EvaluatorSelectionProviderV223,
    ) -> None:
        self._renderer = renderer
        self._oracle = oracle

    def complete_turn(
        self,
        *,
        request: SelectionTurnRequestV222,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> SelectionProviderOutcomeV222:
        self._renderer._payload(request=request, repair_code=None)
        return self._oracle.complete_turn(
            request=request,
            run_id=run_id,
            max_protocol_repairs=max_protocol_repairs,
        )


def _rendered_reports(
    *, repository_root: Path
) -> tuple[ProviderIdentityLintReportV225, ...]:
    renderer = SelectionProviderV225(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="lint-only-not-sent",
            model="gpt-5.4-mini-2026-03-17",
        ),
        minimum_request_interval_seconds=0,
    )
    case_set = load_practical_case_set_v22(
        repository_root / "config/dta-v22-5/evaluation/cases.json"
    )
    truths = {
        item.case_id: item
        for item in load_practical_truth_set_v22(
            repository_root / "config/dta-v22-5/evaluation/truth.json"
        ).truths
    }
    coverages = load_replay_target_coverage_set_v225(
        repository_root / "config/dta-v22-5/evaluation/coverage.json"
    )
    priors = load_frozen_predicate_yield_priors_v223(
        repository_root
        / "config/dta-v22-3/development-predicate-yield-prior.json"
    )
    rendered_runs = 0
    for case_index, spec in enumerate(case_set.cases):
        for combination in balanced_combination_order_v225(case_index):
            before = len(renderer.identity_lint_reports)
            run = execute_ambiguity_bundle_case_v225(
                spec=spec,
                coverage=coverages.require(spec.case_id),
                repository_root=repository_root,
                combination=combination,
                provider=_RenderingOracleV225(
                    renderer=renderer,
                    oracle=_EvaluatorSelectionProviderV223(
                        truth=truths[spec.case_id],
                        oracle_action_ids=(),
                    ),
                ),
                predicate_yield_priors=priors,
            )
            if run.uncaught_exceptions or len(renderer.identity_lint_reports) != before + 1:
                raise ValueError("v2.2.5 runtime lint render did not produce one payload")
            rendered_runs += 1
    if rendered_runs != 64:
        raise ValueError("v2.2.5 runtime lint render count differs")

    first_case = json.loads(
        (
            repository_root
            / "config/dta-v22-5/evaluation/agent-visible/e01.json"
        ).read_bytes()
    )["normalized_case"]
    candidates = tuple(first_case["candidate_services"])
    aliases = SelectionAliasTableV222.build(
        hypothesis_ids=("hypothesis:internal-a",),
        action_ids=("action:internal-a",),
        terminal_ids=("terminal:internal-a",),
        evidence_refs=(),
    )
    bootstrap_request = SelectionTurnRequestV222.build(
        system_prompt=SHARED_SELECTION_SYSTEM_PROMPT_V225,
        aliases=aliases,
        visible_state={
            "candidate_services": candidates,
            "actions": (
                {
                    "alias": "A00",
                    "source": "RESOURCES",
                    "target_services": candidates,
                },
            ),
            "closure": {"read_count": 0},
        },
    )
    renderer._payload(request=bootstrap_request, repair_code=None)
    renderer._payload(request=bootstrap_request, repair_code="UNKNOWN_ACTION_ALIAS")
    return renderer.identity_lint_reports


def build_provider_payload_lint_report_v225(
    *, repository_root: Path
) -> OpaqueProviderPayloadLintAggregateV225:
    files = tuple(
        repository_root
        / f"config/dta-v22-5/evaluation/agent-visible/e{index:02d}.json"
        for index in range(1, 17)
    )
    static_reports = tuple(
        lint_static_identity_surface_v225(
            json.loads(path.read_bytes()), surface_class=path.name
        )
        for path in files
    )
    rendered_reports = _rendered_reports(repository_root=repository_root)
    payload = {
        "schema_version": "dta-v22.5.opaque-provider-payload-lint.v1",
        "terminal": "OPAQUE_PROVIDER_IDENTITY_LINT_PASS",
        "evaluation_files_scanned": 16,
        "rendered_payload_classes": tuple(
            sorted({item.payload_class for item in rendered_reports})
        ),
        "evaluation_runs_rendered": 64,
        "runtime_payloads_rendered": 64,
        "synthetic_protocol_payloads_rendered": 2,
        "static_identity_values_scanned": sum(
            len(item.identity_values_scanned) for item in static_reports
        ),
        "rendered_identity_values_scanned": sum(
            len(item.identity_values_scanned) for item in rendered_reports
        ),
        "forbidden_identity_value_count": sum(
            len(item.forbidden_identity_values)
            for item in (*static_reports, *rendered_reports)
        ),
        "provider_case_id_count": sum(len(item.case_ids) for item in rendered_reports),
        "provider_evaluator_metadata_field_count": sum(
            len(item.evaluator_metadata_fields) for item in rendered_reports
        ),
        "static_reports": static_reports,
        "rendered_reports": rendered_reports,
    }
    digest_payload = {
        **payload,
        "static_reports": tuple(item.model_dump(mode="json") for item in static_reports),
        "rendered_reports": tuple(
            item.model_dump(mode="json") for item in rendered_reports
        ),
    }
    return OpaqueProviderPayloadLintAggregateV225.model_validate(
        {**payload, "report_sha256": semantic_sha256_v22(digest_payload)}
    )


def write_provider_payload_lint_report_v225(
    *, repository_root: Path, output_path: Path
) -> OpaqueProviderPayloadLintAggregateV225:
    report = build_provider_payload_lint_report_v225(repository_root=repository_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(report.model_dump(mode="json"), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


__all__ = (
    "OpaqueProviderPayloadLintAggregateV225",
    "build_provider_payload_lint_report_v225",
    "write_provider_payload_lint_report_v225",
)
