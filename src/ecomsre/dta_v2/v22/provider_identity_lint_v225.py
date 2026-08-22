"""Identity-aware lint for static sources and rendered Provider payloads."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


_SERVICE = re.compile(r"^svc-[0-9a-f]{10}$")
_OPERATION = re.compile(r"^op-[0-9a-f]{10}$")
_CHANGE = re.compile(r"^chg-[0-9a-f]{10}$")
_CASE_ID = re.compile(r"^(?:d|e)[0-9]{2}$")
_FORBIDDEN = re.compile(
    r"(?:cpu|memory|mem|resource|normal|healthy|fault|incident|config|"
    r"configuration|dependency|latency|unavailable|service-unavailable|"
    r"saturation|leak|control|abstain|no-incident)",
    re.IGNORECASE,
)
_EVALUATOR_FIELDS = {
    "case_id",
    "case_ids",
    "truth",
    "truth_set",
    "truth_target",
    "expected_terminal",
    "expected_mechanism",
    "expected_root_service",
    "pair_id",
    "pair_metadata",
    "derivation_note",
    "derivation_notes",
    "source_path",
    "source_paths",
    "evaluator_strata",
    "strata",
}
_SERVICE_KEYS = {
    "candidate_services",
    "service",
    "services",
    "target_service",
    "target_services",
    "root_service",
    "parent_service",
    "service_path",
    "source_service",
    "destination_service",
    "runtime_target",
    "runtime_targets",
    "resource_target",
    "resource_targets",
    "provider_projected_services",
}
_OPERATION_KEYS = {"operation", "operation_name", "operations"}
_CHANGE_KEYS = {"change_id", "opaque_change_id", "change_ids"}
_EDGE_KEYS = {"edges", "topology_edges"}


class ProviderIdentityLintErrorV225(ValueError):
    pass


class ProviderIdentityLintReportV225(DtaModelV22):
    schema_version: Literal["dta-v22.5.provider-identity-lint-report.v1"]
    payload_class: str
    identity_values_scanned: tuple[str, ...]
    forbidden_identity_values: tuple[str, ...]
    case_ids: tuple[str, ...]
    evaluator_metadata_fields: tuple[str, ...]
    report_sha256: str

    @model_validator(mode="after")
    def require_report(self) -> "ProviderIdentityLintReportV225":
        for values in (
            self.identity_values_scanned,
            self.forbidden_identity_values,
            self.case_ids,
            self.evaluator_metadata_fields,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("provider identity lint values are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("provider identity lint report digest differs")
        return self


def _expected_identity_kind(key: str) -> str | None:
    normalized = key.casefold()
    if normalized in _SERVICE_KEYS or normalized.endswith("_service") or normalized.endswith("_services"):
        return "service"
    if normalized in _OPERATION_KEYS or normalized.endswith("_operation"):
        return "operation"
    if normalized in _CHANGE_KEYS or normalized.endswith("_change_id"):
        return "change"
    if normalized in _EDGE_KEYS:
        return "service"
    return None


def _lint_payload_v225(
    payload: object,
    *,
    payload_class: str,
    reject_evaluator_metadata: bool,
    reject_case_ids: bool,
) -> ProviderIdentityLintReportV225:
    identities: list[str] = []
    forbidden: list[str] = []
    case_ids: list[str] = []
    metadata: list[str] = []
    problems: list[str] = []

    def visit(value: object, *, path: str, identity_kind: str | None = None) -> None:
        if isinstance(value, dict):
            for raw_key, item in value.items():
                key = str(raw_key)
                child_path = f"{path}.{key}" if path else key
                if key.casefold() in _EVALUATOR_FIELDS:
                    metadata.append(child_path)
                    if reject_evaluator_metadata:
                        problems.append(child_path)
                visit(
                    item,
                    path=child_path,
                    identity_kind=_expected_identity_kind(key) or identity_kind,
                )
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, path=f"{path}[{index}]", identity_kind=identity_kind)
            return
        if not isinstance(value, str):
            return
        if _CASE_ID.fullmatch(value):
            case_ids.append(value)
            if reject_case_ids:
                problems.append(path)
        if identity_kind is None:
            return
        identities.append(value)
        expected = {
            "service": _SERVICE,
            "operation": _OPERATION,
            "change": _CHANGE,
        }[identity_kind]
        if not expected.fullmatch(value) or _FORBIDDEN.search(value):
            forbidden.append(value)
            problems.append(path)

    visit(payload, path="payload")
    report_payload = {
        "schema_version": "dta-v22.5.provider-identity-lint-report.v1",
        "payload_class": payload_class,
        "identity_values_scanned": tuple(sorted(set(identities))),
        "forbidden_identity_values": tuple(sorted(set(forbidden))),
        "case_ids": tuple(sorted(set(case_ids))),
        "evaluator_metadata_fields": tuple(sorted(set(metadata))),
    }
    report = ProviderIdentityLintReportV225.model_validate(
        {**report_payload, "report_sha256": semantic_sha256_v22(report_payload)}
    )
    if problems:
        raise ProviderIdentityLintErrorV225(
            "Provider identity lint failed at " + ", ".join(sorted(set(problems)))
        )
    return report


def lint_provider_payload_v225(
    payload: object, *, payload_class: str
) -> ProviderIdentityLintReportV225:
    return _lint_payload_v225(
        payload,
        payload_class=payload_class,
        reject_evaluator_metadata=True,
        reject_case_ids=True,
    )


def lint_static_identity_surface_v225(
    payload: object, *, surface_class: str
) -> ProviderIdentityLintReportV225:
    """Lint identity fields in source bytes without treating evaluator fields as payload."""

    return _lint_payload_v225(
        payload,
        payload_class=f"static:{surface_class}",
        reject_evaluator_metadata=False,
        reject_case_ids=False,
    )


__all__ = (
    "ProviderIdentityLintErrorV225",
    "ProviderIdentityLintReportV225",
    "lint_provider_payload_v225",
    "lint_static_identity_surface_v225",
)
