"""Deterministic static and rendered-payload opaque-identity lint report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

from pydantic import model_validator

from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v225 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V225,
)
from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    ProviderIdentityLintReportV225,
    lint_static_identity_surface_v225,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionAliasTableV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.selection_provider_v225 import SelectionProviderV225
from ecomsre.model.gateway import OpenAICompatibleConfig


class OpaqueProviderPayloadLintAggregateV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.opaque-provider-payload-lint.v1"]
    terminal: Literal["OPAQUE_PROVIDER_IDENTITY_LINT_PASS"]
    evaluation_files_scanned: Literal[16]
    rendered_payload_classes: tuple[str, ...]
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
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.2.5 opaque lint aggregate digest differs")
        return self


def _rendered_reports(
    *, source: dict[str, object]
) -> tuple[ProviderIdentityLintReportV225, ...]:
    normalized = cast(dict[str, object], source["normalized_case"])
    candidates = cast(list[str], normalized["candidate_services"])
    topology = cast(list[list[str]], normalized["topology_edges"])
    aliases = SelectionAliasTableV222.build(
        hypothesis_ids=("hypothesis:opaque-a", "hypothesis:opaque-b"),
        action_ids=("action:resources:opaque-a",),
        terminal_ids=("terminal:no-incident",),
        evidence_refs=("evidence:opaque-a",),
    )
    renderer = SelectionProviderV225(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="lint-only-not-sent",
            model="gpt-5.4-mini-2026-03-17",
        ),
        minimum_request_interval_seconds=0,
    )

    states: tuple[dict[str, object], ...] = (
        {
            "candidate_services": candidates,
            "topology_edges": topology,
            "actions": [
                {
                    "alias": "A00",
                    "source": "RESOURCES",
                    "target_services": candidates,
                }
            ],
        },
        {
            "candidate_services": candidates,
            "closure": {"attempted_action_ids": ["A00"]},
            "provider_projected_services": candidates,
        },
        {
            "candidate_services": candidates,
            "closure": {"attempted_action_ids": ["A00"]},
            "last_contrast": {
                "target_services": candidates,
                "outcome": "TARGET_COMPLETE",
            },
        },
        {
            "candidate_services": candidates,
            "terminals": [
                {
                    "alias": "T00",
                    "kind": "NO_INCIDENT",
                    "root_service": None,
                }
            ],
            "closure": {"attempted_action_ids": []},
        },
    )
    for state in states:
        request = SelectionTurnRequestV222.build(
            system_prompt=SHARED_SELECTION_SYSTEM_PROMPT_V225,
            aliases=aliases,
            visible_state=state,
        )
        renderer._payload(request=request, repair_code=None)
    repair_request = SelectionTurnRequestV222.build(
        system_prompt=SHARED_SELECTION_SYSTEM_PROMPT_V225,
        aliases=aliases,
        visible_state=states[0],
    )
    renderer._payload(request=repair_request, repair_code="UNKNOWN_ACTION_ALIAS")
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
    rendered_reports = _rendered_reports(source=json.loads(files[0].read_bytes()))
    payload = {
        "schema_version": "dta-v22.5.opaque-provider-payload-lint.v1",
        "terminal": "OPAQUE_PROVIDER_IDENTITY_LINT_PASS",
        "evaluation_files_scanned": 16,
        "rendered_payload_classes": tuple(
            sorted(item.payload_class for item in rendered_reports)
        ),
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
