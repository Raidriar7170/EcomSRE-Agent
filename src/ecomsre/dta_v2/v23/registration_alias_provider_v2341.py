"""Six-field alias Provider protocol for DTA v2.3.4.1."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Literal, cast

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.simple_provider import (
    ProviderTransportErrorV22,
    StdlibProviderTransportV22,
)
from ecomsre.dta_v2.v23.discovery_provider import (
    DiscoveryProviderProtocolFailureV23,
    DiscoveryProviderTransportErrorV23,
    MAX_EXACT_TRANSPORT_RETRIES_V23,
    MAX_PROTOCOL_REPAIRS_V23,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    RegistrationOptionCatalogV2341,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    RegistrationImplementationModeV234,
    RegistrationProviderModeV234,
    hashed_model_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    AcceptedEvidenceSummaryV234,
    AcceptedReportProjectionSourceV234,
    AcceptedReportProjectionV234,
    ProviderCoreOntologyViewV234,
    RegistrationDraftProviderRequestV234,
    RootOwnershipV234,
    build_provider_core_ontology_view_v234,
    build_registration_draft_provider_request_v234,
    project_development_report_v234,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
)
from ecomsre.dta_v2.v23.review_registry import ReviewQueueItemV23, ShadowFaultEntryV23
from ecomsre.model.gateway import OpenAICompatibleConfig


REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341 = """You are a registration-selection assistant.
The human authorized FORMAL_DRAFT_ONLY. Choose only opaque aliases from the supplied
Runtime-owned catalog. Return exactly the six response fields. Do not emit canonical
names, fixed prose, DSL objects, service bindings, evidence refs, IDs, hashes, code,
paths, URLs, commands, Runbooks, remediation, actions, or repository writes. Alias
ordering is irrelevant. Do not invent an alias. Cardinality is mandatory: D00 requires
one or more clause aliases and zero engineering-gap aliases; D01 requires one or more
engineering-gap aliases and may include safe partial clauses; D02 and D03 require zero
clause aliases and zero engineering-gap aliases. If any engineering-gap alias is
selected, never use D00."""

_SELECTION_FIELDS_V2341 = frozenset(
    {
        "disposition_alias",
        "mechanism_concept",
        "clause_aliases",
        "confusable_aliases",
        "engineering_gap_aliases",
        "semantic_rationale",
    }
)
_SAFE_TEXT_V2341 = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.'()%-]{2,499}[.!]?$", re.ASCII)
_FORBIDDEN_TEXT_V2341 = re.compile(
    r"(?:https?://|[/\\`{}\[\];$#|&=<>]|\b(?:bash|chmod|chown|curl|docker|"
    r"eval|exec|git|import|kubectl|open|python|remediation|rm|runbook|shell|"
    r"subprocess|sudo|wget|write)\b)",
    re.IGNORECASE,
)
_DISPOSITION_BY_ALIAS_V2341 = {
    "D00": RegistrationImplementationModeV234.DECLARATIVE_READY,
    "D01": RegistrationImplementationModeV234.ENGINEERING_REQUIRED,
    "D02": RegistrationImplementationModeV234.DUPLICATE_EXISTING,
    "D03": RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE,
}


def _require_safe_text_v2341(value: str, *, label: str, sentence: bool) -> str:
    normalized = " ".join(value.strip().split())
    if (
        not _SAFE_TEXT_V2341.fullmatch(normalized)
        or _FORBIDDEN_TEXT_V2341.search(normalized)
        or (sentence and normalized[-1] not in ".!")
    ):
        raise ValueError(f"{label} contains forbidden executable content")
    return normalized


class RegistrationAliasSelectionV2341(DtaModelV22):
    disposition_alias: str = Field(pattern=r"^D0[0-3]$")
    mechanism_concept: str = Field(min_length=3, max_length=120)
    clause_aliases: tuple[str, ...]
    confusable_aliases: tuple[str, ...]
    engineering_gap_aliases: tuple[str, ...]
    semantic_rationale: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def require_selection(self) -> "RegistrationAliasSelectionV2341":
        for aliases, pattern, label in (
            (self.clause_aliases, r"^C[0-9]{2}$", "clause"),
            (self.confusable_aliases, r"^M[0-9]{2}$", "confusable"),
            (self.engineering_gap_aliases, r"^G[0-9]{2}$", "engineering-gap"),
        ):
            if aliases != tuple(sorted(set(aliases))):
                raise ValueError(f"{label} aliases are not canonical")
            if any(re.fullmatch(pattern, alias) is None for alias in aliases):
                raise ValueError(f"{label} alias format differs")
        _require_safe_text_v2341(
            self.mechanism_concept,
            label="mechanism concept",
            sentence=False,
        )
        _require_safe_text_v2341(
            self.semantic_rationale,
            label="semantic rationale",
            sentence=True,
        )
        disposition = _DISPOSITION_BY_ALIAS_V2341[self.disposition_alias]
        if disposition is RegistrationImplementationModeV234.DECLARATIVE_READY:
            if not self.clause_aliases or self.engineering_gap_aliases:
                raise ValueError("DECLARATIVE_READY alias cardinality differs")
        elif disposition is RegistrationImplementationModeV234.ENGINEERING_REQUIRED:
            if not self.engineering_gap_aliases:
                raise ValueError("ENGINEERING_REQUIRED alias cardinality differs")
        elif self.clause_aliases or self.engineering_gap_aliases:
            raise ValueError("non-registrable alias cardinality differs")
        return self


class RegistrationAliasProviderRequestV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-alias-provider-request.v1"]
    authorization_summary: str
    shadow_fault_summary: str
    accepted_evidence_summaries: tuple[dict[str, str], ...]
    human_canonical_label_seed: str
    broad_fault_domain: str
    registration_option_catalog: dict[str, Any]
    visible_format_examples: tuple[dict[str, Any], ...]
    source_registration_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_request(self) -> "RegistrationAliasProviderRequestV2341":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("registration alias Provider request digest differs")
        return self

    def provider_payload(self) -> dict[str, Any]:
        """Project only fields explicitly permitted to the Provider."""

        return self.model_dump(
            mode="json",
            exclude={
                "schema_version",
                "source_registration_request_sha256",
                "catalog_sha256",
                "request_sha256",
            },
        )


class RegistrationAliasSelectionTraceV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-alias-selection-trace.v1"]
    provider_mode: RegistrationProviderModeV234
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_calls: StrictInt = Field(ge=0, le=12)
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    transport_retries: StrictInt = Field(ge=0, le=9)
    max_exact_request_retries: Literal[3]
    semantic_retries: Literal[0]
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_trace(self) -> "RegistrationAliasSelectionTraceV2341":
        if (
            self.provider_mode is RegistrationProviderModeV234.DETERMINISTIC_DEVELOPMENT
            and self.provider_calls != 0
        ):
            raise ValueError("deterministic alias selection claims Provider calls")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"trace_sha256"})
        )
        if self.trace_sha256 != expected:
            raise ValueError("registration alias selection trace digest differs")
        return self


class RegistrationAliasProviderResultV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-alias-provider-result.v1"]
    authorization_id: str = Field(pattern=r"^authorization-v234-[0-9a-f]{16}$")
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection: RegistrationAliasSelectionV2341
    trace: RegistrationAliasSelectionTraceV2341
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_result(self) -> "RegistrationAliasProviderResultV2341":
        selection_sha256 = semantic_sha256_v22(
            self.selection.model_dump(mode="json")
        )
        if self.trace.canonical_selection_sha256 != selection_sha256:
            raise ValueError("alias result selection digest differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("registration alias Provider result digest differs")
        return self


def build_registration_alias_provider_request_v2341(
    *,
    source_request: RegistrationDraftProviderRequestV234,
    catalog: RegistrationOptionCatalogV2341,
) -> RegistrationAliasProviderRequestV2341:
    if catalog.source_request_sha256 != source_request.request_sha256:
        raise ValueError("registration alias catalog differs from its source request")
    evidence = tuple(
        {
            "source": item.source.value,
            "summary": item.summary,
        }
        for report in source_request.accepted_reports
        for item in report.evidence_summaries
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.registration-alias-provider-request.v1",
        "authorization_summary": (
            "Human authorization permits one formal registration draft only."
        ),
        "shadow_fault_summary": (
            " ".join(
                (
                    *source_request.shadow_fault.symptom_signature,
                    *source_request.shadow_fault.distinguishing_features,
                )
            )
        )[:1000],
        "accepted_evidence_summaries": evidence,
        "human_canonical_label_seed": source_request.shadow_fault.canonical_label,
        "broad_fault_domain": source_request.shadow_fault.broad_fault_domain.value,
        "registration_option_catalog": catalog.provider_projection(),
        "visible_format_examples": tuple(
            [
                {
                "disposition_alias": "D00",
                "mechanism_concept": "bounded fault mechanism",
                "clause_aliases": ["C00"],
                "confusable_aliases": ["M00"],
                "engineering_gap_aliases": [],
                "semantic_rationale": (
                    "Accepted evidence supports one bounded mechanism."
                ),
                }
            ]
            + (
                [
                    {
                        "disposition_alias": "D01",
                        "mechanism_concept": "bounded extraction gap",
                        "clause_aliases": [],
                        "confusable_aliases": [],
                        "engineering_gap_aliases": [
                            catalog.engineering_gap_options[0].engineering_gap_alias
                        ],
                        "semantic_rationale": (
                            "Accepted evidence requires one bounded extraction capability."
                        ),
                    }
                ]
                if catalog.engineering_gap_options
                else []
            )
        ),
        "source_registration_request_sha256": source_request.request_sha256,
        "catalog_sha256": catalog.catalog_sha256,
    }
    return hashed_model_v234(
        RegistrationAliasProviderRequestV2341,
        payload,
        "request_sha256",
    )


def _runtime_evidence_source_v2341(value: str) -> str:
    lowered = value.casefold()
    matches = tuple(
        source
        for token, source in (
            (":metrics:", "METRICS"),
            (":logs:", "LOGS"),
            (":traces:", "TRACES"),
            (":runtime:", "RUNTIME"),
            (":resources:", "RESOURCES"),
            (":changes:", "CHANGES"),
        )
        if token in lowered
    )
    if len(matches) != 1:
        raise ValueError("runtime report evidence ref lacks one source binding")
    return matches[0]


def project_accepted_report_v2341(
    item: ReviewQueueItemV23,
) -> AcceptedReportProjectionV234:
    """Project an accepted report without weakening the predecessor bindings."""

    if item.automated_fixture and item.source_case_id.startswith("dta-v234-"):
        return project_development_report_v234(item)
    anomalies_by_source = {
        source: tuple(
            sorted(
                (value for value in item.residual_anomalies if value.source.value == source),
                key=lambda value: value.anomaly_id,
            )
        )
        for source in (
            "METRICS",
            "LOGS",
            "TRACES",
            "RUNTIME",
            "RESOURCES",
            "CHANGES",
        )
    }
    source_ordinals = {source: 0 for source in anomalies_by_source}
    summaries = []
    for ref in item.report.supporting_evidence_refs:
        source = _runtime_evidence_source_v2341(ref)
        candidates = anomalies_by_source[source]
        ordinal = source_ordinals[source]
        if ordinal >= len(candidates):
            raise ValueError("runtime report evidence lacks an anomaly projection")
        anomaly = candidates[ordinal]
        source_ordinals[source] += 1
        summaries.append(
            AcceptedEvidenceSummaryV234(
                evidence_ref=ref,
                source=anomaly.source,
                service=anomaly.service,
                anomaly_kind=anomaly.kind,
                summary=next(
                    (
                        symptom
                        for symptom in item.report.observed_symptoms
                        if source.casefold().removesuffix("s") in symptom.casefold()
                    ),
                    item.report.mechanism_description,
                ),
            )
        )
    return hashed_model_v234(
        AcceptedReportProjectionV234,
        {
            "schema_version": "dta-v234.accepted-report-projection.v1",
            "accepted_seed_report_id": item.report.report_id,
            "source_report_id": item.report.report_id,
            "source_case_id": item.source_case_id,
            "report_sha256": item.report.report_sha256,
            "queue_item_sha256": item.queue_item_sha256,
            "projection_source": AcceptedReportProjectionSourceV234.RUNTIME_BOUND_V233,
            "selected_root_service": item.report.suspected_root_services[0],
            "root_ownership": RootOwnershipV234.RUNTIME_OWNED,
            "broad_fault_domain": item.report.broad_fault_domain.value,
            "evidence_summaries": tuple(
                sorted(summaries, key=lambda value: value.evidence_ref)
            ),
        },
        "projection_sha256",
    )


def build_registration_alias_source_request_v2341(
    *,
    authorization_context: DraftGenerationAuthorizationResultV234,
    shadow: ShadowFaultEntryV23,
    accepted_reports: tuple[ReviewQueueItemV23, ...],
    hidden_mechanism: Any | None = None,
    ontology_view: ProviderCoreOntologyViewV234 | None = None,
) -> RegistrationDraftProviderRequestV234:
    projections = tuple(
        project_accepted_report_v2341(item)
        for item in sorted(accepted_reports, key=lambda value: value.report.report_id)
    )
    if ontology_view is not None and hidden_mechanism is not None:
        raise ValueError("alias source request accepts one ontology-view source")
    view = ontology_view or build_provider_core_ontology_view_v234(
        snapshot=authorization_context.core_ontology_snapshot,
        hidden_mechanism=hidden_mechanism,
    )
    return build_registration_draft_provider_request_v234(
        authorization_context=authorization_context,
        shadow_fault=shadow,
        accepted_reports=projections,
        ontology_view=view,
    )


def _request_body_v2341(
    request: RegistrationAliasProviderRequestV2341,
    *,
    repair_ordinal: int,
    issue_codes: tuple[str, ...] = (),
) -> str:
    payload: dict[str, Any] = {
        "system": REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341,
        "request": request.provider_payload(),
        "response_contract": {
            "fields": tuple(sorted(_SELECTION_FIELDS_V2341)),
            "format": "one JSON object only",
            "alias_order": "arbitrary",
            "cardinality_rules": {
                "D00": "one or more clause aliases and zero engineering-gap aliases",
                "D01": "one or more engineering-gap aliases; safe partial clauses allowed",
                "D02": "zero clause aliases and zero engineering-gap aliases",
                "D03": "zero clause aliases and zero engineering-gap aliases",
                "gap_binding": "any selected engineering-gap alias requires D01, never D00",
            },
        },
    }
    if repair_ordinal:
        payload["protocol_repair"] = {
            "ordinal": repair_ordinal,
            "safe_issue_codes": issue_codes or ("PRIOR_RESPONSE_PROTOCOL_INVALID",),
            "instruction": (
                "Return the same six fields using only listed aliases. Resolve every "
                "safe issue code and obey cardinality exactly: D00 requires at least "
                "one clause and zero gaps; D01 requires at least one gap; any selected "
                "gap requires D01 and must never be paired with D00; D02 and D03 use "
                "zero clauses and zero gaps."
            ),
        }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normalize_aliases_v2341(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of aliases")
    return tuple(sorted(set(cast(list[str], value))))


def _parse_alias_selection_v2341(
    raw: str,
    *,
    catalog: RegistrationOptionCatalogV2341,
) -> RegistrationAliasSelectionV2341:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("registration alias response is not one JSON object")
    if set(value) != _SELECTION_FIELDS_V2341:
        raise ValueError("registration alias response fields differ")
    normalized = {
        **value,
        "mechanism_concept": _require_safe_text_v2341(
            str(value["mechanism_concept"]),
            label="mechanism concept",
            sentence=False,
        ),
        "clause_aliases": _normalize_aliases_v2341(
            value["clause_aliases"], label="clause_aliases"
        ),
        "confusable_aliases": _normalize_aliases_v2341(
            value["confusable_aliases"], label="confusable_aliases"
        ),
        "engineering_gap_aliases": _normalize_aliases_v2341(
            value["engineering_gap_aliases"], label="engineering_gap_aliases"
        ),
        "semantic_rationale": _require_safe_text_v2341(
            str(value["semantic_rationale"]),
            label="semantic rationale",
            sentence=True,
        ),
    }
    selection = RegistrationAliasSelectionV2341.model_validate(normalized)
    known_aliases = {
        "disposition_alias": {
            item.disposition_alias for item in catalog.disposition_options
        },
        "clause_aliases": {item.clause_alias for item in catalog.clause_options},
        "confusable_aliases": {
            item.confusable_alias for item in catalog.confusable_options
        },
        "engineering_gap_aliases": {
            item.engineering_gap_alias for item in catalog.engineering_gap_options
        },
    }
    if selection.disposition_alias not in known_aliases["disposition_alias"]:
        raise ValueError("DISPOSITION_ALIAS_INVALID")
    for field in ("clause_aliases", "confusable_aliases", "engineering_gap_aliases"):
        if not set(getattr(selection, field)).issubset(known_aliases[field]):
            raise ValueError(f"UNKNOWN_{field.removesuffix('es').upper()}")
    return selection


def _deterministic_selection_v2341(
    catalog: RegistrationOptionCatalogV2341,
) -> RegistrationAliasSelectionV2341:
    if catalog.engineering_gap_options:
        return RegistrationAliasSelectionV2341(
            disposition_alias="D01",
            mechanism_concept=catalog.human_canonical_label.replace("-", " "),
            clause_aliases=(),
            confusable_aliases=(),
            engineering_gap_aliases=tuple(
                item.engineering_gap_alias for item in catalog.engineering_gap_options
            ),
            semantic_rationale=(
                "Accepted evidence requires one bounded extraction capability."
            ),
        )
    if catalog.clause_options:
        return RegistrationAliasSelectionV2341(
            disposition_alias="D00",
            mechanism_concept=catalog.human_canonical_label.replace("-", " "),
            clause_aliases=(catalog.clause_options[0].clause_alias,),
            confusable_aliases=tuple(
                item.confusable_alias for item in catalog.confusable_options[:2]
            ),
            engineering_gap_aliases=(),
            semantic_rationale="Accepted evidence supports one bounded mechanism.",
        )
    return RegistrationAliasSelectionV2341(
        disposition_alias="D03",
        mechanism_concept="insufficient bounded evidence",
        clause_aliases=(),
        confusable_aliases=(),
        engineering_gap_aliases=(),
        semantic_rationale="Accepted evidence cannot support formal registration.",
    )


class OpenAICompatibleRegistrationAliasTransportV2341:
    """Dedicated forced-tool transport for the exact six-field response."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        minimum_request_interval_seconds: float = 6.0,
        timeout_seconds: float = 120.0,
        raw_artifact_dir: Path | None = None,
    ) -> None:
        if minimum_request_interval_seconds < 0:
            raise ValueError("registration alias request interval cannot be negative")
        self.config = config
        self.minimum_request_interval_seconds = minimum_request_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.raw_artifact_dir = raw_artifact_dir
        self.transport = StdlibProviderTransportV22()
        self._last_started: float | None = None
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.latency_ms = 0.0

    def _write_private_raw(self, *, payload: object, response: object) -> None:
        if self.raw_artifact_dir is None:
            return
        resolved = self.raw_artifact_dir.resolve()
        parts = resolved.parts
        if not any(
            parts[index : index + 3] == (".local", "dta-v2341", "provider-raw")
            for index in range(max(len(parts) - 2, 0))
        ):
            raise ValueError("v2.3.4.1 raw Provider scope is not private")
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved.chmod(0o700)
        ordinal = len(tuple(resolved.glob("request-*.json"))) + 1
        for prefix, value in (("request", payload), ("response", response)):
            path = resolved / f"{prefix}-{ordinal:03d}.json"
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")

    @staticmethod
    def _tool() -> dict[str, object]:
        schema = RegistrationAliasSelectionV2341.model_json_schema()
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": "submit_registration_alias_selection",
                "description": "Submit only six bounded registration-selection fields.",
                "strict": False,
                "parameters": schema,
            },
        }

    @staticmethod
    def _extract(response: Mapping[str, object]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_PROVIDER_ENVELOPE", retryable=False
            )
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, Mapping) else None
        calls = message.get("tool_calls") if isinstance(message, Mapping) else None
        if not isinstance(calls, list) or len(calls) != 1:
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_REGISTRATION_ALIAS_TOOL_CALL", retryable=False
            )
        call = calls[0]
        function = call.get("function") if isinstance(call, Mapping) else None
        if (
            not isinstance(function, Mapping)
            or function.get("name") != "submit_registration_alias_selection"
            or not isinstance(function.get("arguments"), str)
        ):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_REGISTRATION_ALIAS_TOOL_CALL", retryable=False
            )
        return cast(str, function["arguments"])

    def __call__(self, body: str) -> str:
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise DiscoveryProviderTransportErrorV23(
                "INVALID_LOCAL_REQUEST", retryable=False
            )
        now = time.monotonic()
        if self._last_started is not None:
            delay = self.minimum_request_interval_seconds - (now - self._last_started)
            if delay > 0:
                time.sleep(delay)
        self._last_started = time.monotonic()
        started = time.monotonic()
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": str(parsed["system"])},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": parsed["request"],
                            "response_contract": parsed["response_contract"],
                            "protocol_repair": parsed.get("protocol_repair"),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "tools": [self._tool()],
            "tool_choice": {
                "type": "function",
                "function": {"name": "submit_registration_alias_selection"},
            },
            "temperature": 0,
        }
        try:
            response = self.transport.post_json(
                url=f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
        except ProviderTransportErrorV22 as exc:
            raise DiscoveryProviderTransportErrorV23(
                exc.safe_code, retryable=exc.retryable
            ) from exc
        self._write_private_raw(payload=payload, response=response)
        self.latency_ms += (time.monotonic() - started) * 1000.0
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            self.input_tokens += int(usage.get("prompt_tokens", 0))
            self.output_tokens += int(usage.get("completion_tokens", 0))
            self.total_tokens += int(usage.get("total_tokens", 0))
        return self._extract(response)


class RegistrationAliasProviderV2341:
    def __init__(self, transport: Callable[[str], str] | None = None) -> None:
        self.transport = transport

    def select(
        self,
        *,
        request: RegistrationAliasProviderRequestV2341,
        catalog: RegistrationOptionCatalogV2341,
    ) -> RegistrationAliasProviderResultV2341:
        if request.catalog_sha256 != catalog.catalog_sha256:
            raise ValueError("alias Provider request differs from catalog")
        if self.transport is None:
            selection = _deterministic_selection_v2341(catalog)
            raw = selection.model_dump_json()
            provider_mode = RegistrationProviderModeV234.DETERMINISTIC_DEVELOPMENT
            provider_calls = protocol_repairs = transport_retries = 0
        else:
            (
                selection,
                raw,
                provider_calls,
                protocol_repairs,
                transport_retries,
            ) = self._call_provider(request=request, catalog=catalog)
            provider_mode = RegistrationProviderModeV234.OPENAI_COMPATIBLE
        selection_sha256 = semantic_sha256_v22(selection.model_dump(mode="json"))
        trace = hashed_model_v234(
            RegistrationAliasSelectionTraceV2341,
            {
                "schema_version": "dta-v2341.registration-alias-selection-trace.v1",
                "provider_mode": provider_mode,
                "request_sha256": request.request_sha256,
                "raw_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "canonical_selection_sha256": selection_sha256,
                "provider_calls": provider_calls,
                "protocol_repairs": protocol_repairs,
                "transport_retries": transport_retries,
                "max_exact_request_retries": 3,
                "semantic_retries": 0,
            },
            "trace_sha256",
        )
        return hashed_model_v234(
            RegistrationAliasProviderResultV2341,
            {
                "schema_version": "dta-v2341.registration-alias-provider-result.v1",
                "authorization_id": catalog.authorization_id,
                "catalog_sha256": catalog.catalog_sha256,
                "selection": selection,
                "trace": trace,
            },
            "result_sha256",
        )

    def _call_provider(
        self,
        *,
        request: RegistrationAliasProviderRequestV2341,
        catalog: RegistrationOptionCatalogV2341,
    ) -> tuple[RegistrationAliasSelectionV2341, str, int, int, int]:
        assert self.transport is not None
        provider_calls = 0
        transport_retries = 0
        issue_codes: tuple[str, ...] = ()
        for repair_ordinal in range(MAX_PROTOCOL_REPAIRS_V23 + 1):
            body = _request_body_v2341(
                request,
                repair_ordinal=repair_ordinal,
                issue_codes=issue_codes,
            )
            raw: str | None = None
            for retry in range(MAX_EXACT_TRANSPORT_RETRIES_V23 + 1):
                try:
                    provider_calls += 1
                    raw = self.transport(body)
                    break
                except DiscoveryProviderTransportErrorV23 as exc:
                    if not exc.retryable or retry == MAX_EXACT_TRANSPORT_RETRIES_V23:
                        raise
                    transport_retries += 1
            assert raw is not None
            try:
                selection = _parse_alias_selection_v2341(raw, catalog=catalog)
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                if repair_ordinal == MAX_PROTOCOL_REPAIRS_V23:
                    raise DiscoveryProviderProtocolFailureV23(
                        "registration alias Provider exhausted two protocol repairs"
                    ) from exc
                issue_codes = _safe_issue_codes_v2341(exc)
                continue
            return selection, raw, provider_calls, repair_ordinal, transport_retries
        raise AssertionError("unreachable registration alias Provider state")


def _safe_issue_codes_v2341(exc: Exception) -> tuple[str, ...]:
    rendered = str(exc).casefold()
    rules = (
        ("json", "INVALID_JSON_OBJECT"),
        ("fields differ", "RESPONSE_FIELDS_DIFFER"),
        ("unknown_clause", "UNKNOWN_CLAUSE_ALIAS"),
        ("unknown_confusable", "UNKNOWN_CONFUSABLE_ALIAS"),
        ("unknown_engineering_gap", "UNKNOWN_ENGINEERING_GAP_ALIAS"),
        ("disposition_alias_invalid", "DISPOSITION_ALIAS_INVALID"),
        ("declarative_ready", "DECLARATIVE_READY_REQUIRES_CLAUSE"),
        ("engineering_required", "ENGINEERING_REQUIRED_REQUIRES_GAP"),
        ("forbidden executable", "FORBIDDEN_EXECUTABLE_CONTENT"),
    )
    values = tuple(sorted({code for token, code in rules if token in rendered}))
    return values or ("PRIOR_RESPONSE_PROTOCOL_INVALID",)


__all__ = (
    "OpenAICompatibleRegistrationAliasTransportV2341",
    "REGISTRATION_ALIAS_SYSTEM_PROMPT_V2341",
    "RegistrationAliasProviderRequestV2341",
    "RegistrationAliasProviderResultV2341",
    "RegistrationAliasProviderV2341",
    "RegistrationAliasSelectionTraceV2341",
    "RegistrationAliasSelectionV2341",
    "build_registration_alias_provider_request_v2341",
    "build_registration_alias_source_request_v2341",
    "project_accepted_report_v2341",
)
