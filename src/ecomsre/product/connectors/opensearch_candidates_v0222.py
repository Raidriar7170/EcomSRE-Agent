"""Evidence-bound OpenSearch profile candidates for Product v0.2.2.2."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from itertools import count
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CAPTURE_REF_PATTERN = r"^objects/sha256/[0-9a-f]{2}/[0-9a-f]{64}$"


class OpenSearchProfileComponentKindV0222(str, Enum):
    TIMESTAMP = "TIMESTAMP"
    SERVICE_SOURCE = "SERVICE_SOURCE"
    SERVICE_QUERY = "SERVICE_QUERY"
    MESSAGE = "MESSAGE"
    SEVERITY = "SEVERITY"
    TRACE_ID = "TRACE_ID"


class OpenSearchProfileRecommendationStatusV0222(str, Enum):
    UNIQUE_RECOMMENDATION = "UNIQUE_RECOMMENDATION"
    OPERATOR_SELECTION_REQUIRED = "OPERATOR_SELECTION_REQUIRED"
    NO_VALID_CANDIDATE = "NO_VALID_CANDIDATE"


class OpenSearchOperatorDecisionV0222(str, Enum):
    SELECT_PROFILE = "SELECT_PROFILE"
    REJECT_ALL_CANDIDATES = "REJECT_ALL_CANDIDATES"


class OpenSearchProfileComponentCandidateV0222(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-profile-component.v0222"
    ] = "ecomsre.product.opensearch-profile-component.v0222"
    component_alias: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    kind: OpenSearchProfileComponentKindV0222
    accessor: str = Field(min_length=1, max_length=255)
    encoding_or_mode: str = Field(pattern=r"^[A-Z0-9_]{2,40}$")
    mapping_types: tuple[str, ...] = Field(max_length=20)
    field_caps_types: tuple[str, ...] = Field(max_length=20)
    sample_presence_count: int = Field(ge=0, le=1_000)
    sample_parse_success_count: int = Field(ge=0, le=1_000)
    checkout_match_count: int = Field(ge=0, le=1_000)
    query_verification_status: str = Field(pattern=r"^[A-Z0-9_]{2,80}$")
    supporting_capture_refs: tuple[str, ...] = Field(min_length=1, max_length=20)
    contradicting_capture_refs: tuple[str, ...] = Field(max_length=20)
    hard_rejection_codes: tuple[str, ...] = Field(max_length=20)
    component_score: int = Field(ge=-100, le=100)
    component_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_component(self) -> "OpenSearchProfileComponentCandidateV0222":
        tuple_fields = (
            self.mapping_types,
            self.field_caps_types,
            self.supporting_capture_refs,
            self.contradicting_capture_refs,
            self.hard_rejection_codes,
        )
        if any(items != tuple(sorted(set(items))) for items in tuple_fields):
            raise ValueError("OpenSearch component tuple is not canonical")
        if any(
            not re.fullmatch(_CAPTURE_REF_PATTERN, reference)
            for reference in (
                *self.supporting_capture_refs,
                *self.contradicting_capture_refs,
            )
        ):
            raise ValueError("OpenSearch component capture reference differs")
        if self.sample_parse_success_count > self.sample_presence_count:
            raise ValueError("OpenSearch component parse count differs")
        body = self.model_dump(mode="json", exclude={"component_sha256"})
        if self.component_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch component digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchProfileComponentCandidateV0222":
        draft = cls.model_construct(**values, component_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"component_sha256"})
        return cls.model_validate(
            {**body, "component_sha256": semantic_sha256_v22(body)}
        )


def build_component_candidate_v0222(
    *,
    component_alias: str,
    kind: OpenSearchProfileComponentKindV0222,
    accessor: str,
    encoding_or_mode: str,
    mapping_types: tuple[str, ...],
    field_caps_types: tuple[str, ...],
    sample_presence_count: int,
    sample_parse_success_count: int,
    checkout_match_count: int,
    query_verification_status: str,
    supporting_capture_refs: tuple[str, ...],
    contradicting_capture_refs: tuple[str, ...],
) -> OpenSearchProfileComponentCandidateV0222:
    if not supporting_capture_refs:
        raise ValueError("OpenSearch component lacks capture evidence")
    optional = accessor == "__OPTIONAL__" and encoding_or_mode == "OPTIONAL"
    rejection_codes: set[str] = set()
    score = 0
    if kind is OpenSearchProfileComponentKindV0222.TIMESTAMP:
        if sample_presence_count == 0 or sample_parse_success_count != sample_presence_count:
            rejection_codes.add("TIMESTAMP_PARSE_INCOMPLETE")
        else:
            score = 4
    elif kind is OpenSearchProfileComponentKindV0222.SERVICE_SOURCE:
        if checkout_match_count == 0:
            rejection_codes.add("CHECKOUT_ALIAS_UNOBSERVED")
        elif sample_presence_count == 0 or sample_parse_success_count == 0:
            rejection_codes.add("SERVICE_SOURCE_UNPARSEABLE")
        else:
            score = 4
    elif kind is OpenSearchProfileComponentKindV0222.SERVICE_QUERY:
        if query_verification_status != "PASS":
            rejection_codes.add("CHECKOUT_QUERY_UNVERIFIED")
        else:
            score = 4
    elif kind is OpenSearchProfileComponentKindV0222.MESSAGE:
        if sample_presence_count == 0 or sample_parse_success_count != sample_presence_count:
            rejection_codes.add("MESSAGE_PARSE_INCOMPLETE")
        else:
            score = 3
    elif kind is OpenSearchProfileComponentKindV0222.SEVERITY:
        if optional:
            score = 2
        elif sample_presence_count == 0 or sample_parse_success_count == 0:
            rejection_codes.add("SEVERITY_UNPARSEABLE")
        else:
            score = 2
    elif kind is OpenSearchProfileComponentKindV0222.TRACE_ID:
        if optional:
            score = 1
        elif sample_presence_count == 0 or sample_parse_success_count == 0:
            rejection_codes.add("TRACE_ID_UNPARSEABLE")
        else:
            score = 1
    return OpenSearchProfileComponentCandidateV0222.build(
        component_alias=component_alias,
        kind=kind,
        accessor=accessor,
        encoding_or_mode=encoding_or_mode,
        mapping_types=tuple(sorted(set(mapping_types))),
        field_caps_types=tuple(sorted(set(field_caps_types))),
        sample_presence_count=sample_presence_count,
        sample_parse_success_count=sample_parse_success_count,
        checkout_match_count=checkout_match_count,
        query_verification_status=query_verification_status,
        supporting_capture_refs=tuple(sorted(set(supporting_capture_refs))),
        contradicting_capture_refs=tuple(sorted(set(contradicting_capture_refs))),
        hard_rejection_codes=tuple(sorted(rejection_codes)),
        component_score=score,
    )


class OpenSearchCandidateSampleParseReportV0222(ProductModelV1):
    total_records: int = Field(ge=1, le=1_000)
    accepted_records: int = Field(ge=0, le=1_000)
    window_match_count: int = Field(ge=0, le=1_000)
    observer_projection_success_count: int = Field(ge=0, le=1_000)

    @model_validator(mode="after")
    def require_bounded_counts(self) -> "OpenSearchCandidateSampleParseReportV0222":
        if any(
            value > self.total_records
            for value in (
                self.accepted_records,
                self.window_match_count,
                self.observer_projection_success_count,
            )
        ):
            raise ValueError("OpenSearch candidate sample counts differ")
        return self


class OpenSearchCandidateEmpiricalQueryReportV0222(ProductModelV1):
    checkout_query_verification: Literal["PASS", "FAIL"]
    service_query_field: str = Field(min_length=1, max_length=255)


class OpenSearchProfileCandidateV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-profile-candidate.v0222"] = (
        "ecomsre.product.opensearch-profile-candidate.v0222"
    )
    candidate_alias: str = Field(pattern=r"^P[0-9]{2}$")
    timestamp_component_alias: str
    service_source_component_alias: str
    service_query_component_alias: str
    message_component_alias: str
    severity_component_alias: str
    trace_id_component_alias: str
    profile_fields: dict[str, str]
    static_compatibility: bool
    sample_parse_report: OpenSearchCandidateSampleParseReportV0222
    empirical_query_report: OpenSearchCandidateEmpiricalQueryReportV0222
    support_score: int = Field(ge=0, le=100)
    contradiction_score: int = Field(ge=0, le=100)
    net_score: int = Field(ge=-100, le=100)
    supporting_capture_refs: tuple[str, ...] = Field(min_length=1, max_length=100)
    contradicting_capture_refs: tuple[str, ...] = Field(max_length=100)
    rejection_codes: tuple[str, ...] = Field(max_length=100)
    candidate_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_candidate(self) -> "OpenSearchProfileCandidateV0222":
        required_fields = {
            "timestamp",
            "service_source",
            "service_query",
            "message",
            "severity",
            "trace_id",
        }
        if set(self.profile_fields) != required_fields:
            raise ValueError("OpenSearch candidate profile fields differ")
        if self.net_score != self.support_score - self.contradiction_score:
            raise ValueError("OpenSearch candidate score arithmetic differs")
        for items in (
            self.supporting_capture_refs,
            self.contradicting_capture_refs,
            self.rejection_codes,
        ):
            if items != tuple(sorted(set(items))):
                raise ValueError("OpenSearch candidate tuple is not canonical")
        body = self.model_dump(mode="json", exclude={"candidate_sha256"})
        if self.candidate_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch candidate digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchProfileCandidateV0222":
        draft = cls.model_construct(**values, candidate_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"candidate_sha256"})
        return cls.model_validate(
            {**body, "candidate_sha256": semantic_sha256_v22(body)}
        )


class OpenSearchProfileCandidateSetV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-candidate-set.v0222"] = (
        "ecomsre.product.opensearch-candidate-set.v0222"
    )
    capture_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidates: tuple[OpenSearchProfileCandidateV0222, ...] = Field(max_length=12)
    recommended_candidate_alias: str | None = Field(
        default=None,
        pattern=r"^P[0-9]{2}$",
    )
    recommendation_status: OpenSearchProfileRecommendationStatusV0222
    score_margin: int = Field(ge=0, le=200)
    eliminated_candidates: tuple[str, ...] = Field(max_length=500)
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_candidate_set(self) -> "OpenSearchProfileCandidateSetV0222":
        aliases = tuple(candidate.candidate_alias for candidate in self.candidates)
        if aliases != tuple(f"P{index:02d}" for index in range(len(aliases))):
            raise ValueError("OpenSearch candidate aliases differ")
        if self.eliminated_candidates != tuple(sorted(set(self.eliminated_candidates))):
            raise ValueError("OpenSearch eliminated candidates are not canonical")
        if self.recommendation_status is (
            OpenSearchProfileRecommendationStatusV0222.NO_VALID_CANDIDATE
        ):
            if self.candidates or self.recommended_candidate_alias is not None:
                raise ValueError("OpenSearch no-valid-candidate state differs")
        elif not self.candidates:
            raise ValueError("OpenSearch candidate set is empty")
        if (
            self.recommended_candidate_alias is not None
            and self.recommended_candidate_alias not in aliases
        ):
            raise ValueError("OpenSearch recommended candidate is unknown")
        body = self.model_dump(mode="json", exclude={"candidate_set_sha256"})
        if self.candidate_set_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch candidate-set digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchProfileCandidateSetV0222":
        draft = cls.model_construct(**values, candidate_set_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"candidate_set_sha256"})
        return cls.model_validate(
            {**body, "candidate_set_sha256": semantic_sha256_v22(body)}
        )


_COMPONENT_ORDER = (
    OpenSearchProfileComponentKindV0222.TIMESTAMP,
    OpenSearchProfileComponentKindV0222.SERVICE_SOURCE,
    OpenSearchProfileComponentKindV0222.SERVICE_QUERY,
    OpenSearchProfileComponentKindV0222.MESSAGE,
    OpenSearchProfileComponentKindV0222.SEVERITY,
    OpenSearchProfileComponentKindV0222.TRACE_ID,
)


def _service_fields_compatible(
    partial: Mapping[
        OpenSearchProfileComponentKindV0222,
        OpenSearchProfileComponentCandidateV0222,
    ],
) -> bool:
    source = partial.get(OpenSearchProfileComponentKindV0222.SERVICE_SOURCE)
    query = partial.get(OpenSearchProfileComponentKindV0222.SERVICE_QUERY)
    if source is None or query is None:
        return True
    return query.accessor == source.accessor or query.accessor.startswith(
        f"{source.accessor}."
    )


def _profile_field_sort_key(
    partial: Mapping[
        OpenSearchProfileComponentKindV0222,
        OpenSearchProfileComponentCandidateV0222,
    ],
) -> tuple[str, ...]:
    return tuple(partial[kind].accessor for kind in _COMPONENT_ORDER if kind in partial)


def build_profile_candidate_set_v0222(
    *,
    capture_bundle_sha256: str,
    components: Sequence[OpenSearchProfileComponentCandidateV0222],
) -> OpenSearchProfileCandidateSetV0222:
    grouped: dict[
        OpenSearchProfileComponentKindV0222,
        list[OpenSearchProfileComponentCandidateV0222],
    ] = {kind: [] for kind in _COMPONENT_ORDER}
    eliminated: set[str] = set()
    for component in components:
        if component.hard_rejection_codes:
            eliminated.add(
                f"{component.component_alias}:{','.join(component.hard_rejection_codes)}"
            )
            continue
        grouped[component.kind].append(component)
    for kind in _COMPONENT_ORDER:
        grouped[kind] = sorted(
            grouped[kind],
            key=lambda item: (-item.component_score, item.component_alias),
        )[:8]
        if not grouped[kind]:
            return OpenSearchProfileCandidateSetV0222.build(
                capture_bundle_sha256=capture_bundle_sha256,
                candidates=(),
                recommended_candidate_alias=None,
                recommendation_status=(
                    OpenSearchProfileRecommendationStatusV0222.NO_VALID_CANDIDATE
                ),
                score_margin=0,
                eliminated_candidates=tuple(sorted(eliminated | {f"MISSING_{kind.value}"})),
            )

    beam: list[
        dict[
            OpenSearchProfileComponentKindV0222,
            OpenSearchProfileComponentCandidateV0222,
        ]
    ] = [{}]
    combination_counter = count(1)
    for kind in _COMPONENT_ORDER:
        expanded: list[
            dict[
                OpenSearchProfileComponentKindV0222,
                OpenSearchProfileComponentCandidateV0222,
            ]
        ] = []
        for partial in beam:
            for component in grouped[kind]:
                candidate = {**partial, kind: component}
                combination_ordinal = next(combination_counter)
                if not _service_fields_compatible(candidate):
                    eliminated.add(
                        f"COMBINATION_{combination_ordinal}:SERVICE_FIELDS_INCOMPATIBLE"
                    )
                    continue
                expanded.append(candidate)
        beam = sorted(
            expanded,
            key=lambda item: (
                -sum(component.component_score for component in item.values()),
                _profile_field_sort_key(item),
            ),
        )[:24]

    drafts: list[dict[str, Any]] = []
    for partial in beam:
        timestamp = partial[OpenSearchProfileComponentKindV0222.TIMESTAMP]
        service_source = partial[OpenSearchProfileComponentKindV0222.SERVICE_SOURCE]
        service_query = partial[OpenSearchProfileComponentKindV0222.SERVICE_QUERY]
        message = partial[OpenSearchProfileComponentKindV0222.MESSAGE]
        severity = partial[OpenSearchProfileComponentKindV0222.SEVERITY]
        trace_id = partial[OpenSearchProfileComponentKindV0222.TRACE_ID]
        selected = tuple(partial[kind] for kind in _COMPONENT_ORDER)
        total_records = max(
            component.sample_presence_count
            for component in (timestamp, service_source, message)
        )
        accepted_records = min(
            component.sample_parse_success_count
            for component in (timestamp, service_source, message)
        )
        consistent = accepted_records == total_records
        measured = tuple(
            component
            for component in selected
            if component.accessor != "__OPTIONAL__"
        )
        mapping_agreement = all(
            not component.mapping_types
            or not component.field_caps_types
            or bool(set(component.mapping_types) & set(component.field_caps_types))
            for component in measured
        )
        support_score = sum(component.component_score for component in selected)
        support_score += 2 if consistent else 0
        support_score += 2 if mapping_agreement else 0
        optional_fallback_count = sum(
            component.accessor == "__OPTIONAL__" for component in selected
        )
        contradiction_score = optional_fallback_count
        supporting_refs = tuple(
            sorted(
                {
                    reference
                    for component in selected
                    for reference in component.supporting_capture_refs
                }
            )
        )
        contradicting_refs = tuple(
            sorted(
                {
                    reference
                    for component in selected
                    for reference in component.contradicting_capture_refs
                }
            )
        )
        drafts.append(
            {
                "components": selected,
                "profile_fields": {
                    "timestamp": timestamp.accessor,
                    "service_source": service_source.accessor,
                    "service_query": service_query.accessor,
                    "message": message.accessor,
                    "severity": severity.accessor,
                    "trace_id": trace_id.accessor,
                },
                "sample_parse_report": OpenSearchCandidateSampleParseReportV0222(
                    total_records=total_records,
                    accepted_records=accepted_records,
                    window_match_count=timestamp.sample_parse_success_count,
                    observer_projection_success_count=(
                        message.sample_parse_success_count
                    ),
                ),
                "empirical_query_report": (
                    OpenSearchCandidateEmpiricalQueryReportV0222(
                        checkout_query_verification="PASS",
                        service_query_field=service_query.accessor,
                    )
                ),
                "support_score": support_score,
                "contradiction_score": contradiction_score,
                "net_score": support_score - contradiction_score,
                "supporting_capture_refs": supporting_refs,
                "contradicting_capture_refs": contradicting_refs,
            }
        )
    drafts = sorted(
        drafts,
        key=lambda item: (
            -int(item["net_score"]),
            tuple(sorted(item["profile_fields"].items())),
        ),
    )[:12]
    candidates: list[OpenSearchProfileCandidateV0222] = []
    for index, draft in enumerate(drafts):
        selected = draft.pop("components")
        by_kind = {component.kind: component for component in selected}
        candidates.append(
            OpenSearchProfileCandidateV0222.build(
                candidate_alias=f"P{index:02d}",
                timestamp_component_alias=by_kind[
                    OpenSearchProfileComponentKindV0222.TIMESTAMP
                ].component_alias,
                service_source_component_alias=by_kind[
                    OpenSearchProfileComponentKindV0222.SERVICE_SOURCE
                ].component_alias,
                service_query_component_alias=by_kind[
                    OpenSearchProfileComponentKindV0222.SERVICE_QUERY
                ].component_alias,
                message_component_alias=by_kind[
                    OpenSearchProfileComponentKindV0222.MESSAGE
                ].component_alias,
                severity_component_alias=by_kind[
                    OpenSearchProfileComponentKindV0222.SEVERITY
                ].component_alias,
                trace_id_component_alias=by_kind[
                    OpenSearchProfileComponentKindV0222.TRACE_ID
                ].component_alias,
                static_compatibility=True,
                rejection_codes=(),
                **draft,
            )
        )
    margin = (
        candidates[0].net_score - candidates[1].net_score
        if len(candidates) > 1
        else candidates[0].net_score
    )
    top = candidates[0]
    unique = (
        not top.rejection_codes
        and top.net_score >= 12
        and top.sample_parse_report.accepted_records
        == top.sample_parse_report.total_records
        and top.empirical_query_report.checkout_query_verification == "PASS"
        and margin >= 3
    )
    return OpenSearchProfileCandidateSetV0222.build(
        capture_bundle_sha256=capture_bundle_sha256,
        candidates=tuple(candidates),
        recommended_candidate_alias=(top.candidate_alias if unique else None),
        recommendation_status=(
            OpenSearchProfileRecommendationStatusV0222.UNIQUE_RECOMMENDATION
            if unique
            else OpenSearchProfileRecommendationStatusV0222.OPERATOR_SELECTION_REQUIRED
        ),
        score_margin=margin,
        eliminated_candidates=tuple(sorted(eliminated)),
    )


def render_operator_brief_v0222(
    *,
    candidate_set: OpenSearchProfileCandidateSetV0222,
    capture_session_id: str,
) -> str:
    lines = [
        "# Product v0.2.2.2 OpenSearch Profile Selection",
        "",
        f"- Capture session: `{capture_session_id}`",
        f"- Capture bundle SHA: `{candidate_set.capture_bundle_sha256}`",
        f"- Candidate Set SHA: `{candidate_set.candidate_set_sha256}`",
        f"- Machine status: `{candidate_set.recommendation_status.value}`",
        f"- Machine recommendation: `{candidate_set.recommended_candidate_alias or 'NONE'}`",
        "",
        "| Candidate | Timestamp | Service source | Service query | Message | Severity | Trace ID | Sample parse | Checkout query | Support | Contradiction | Net | Warnings |",
        "|---|---|---|---|---|---|---|---:|---|---:|---:|---:|---|",
    ]
    for candidate in candidate_set.candidates:
        report = candidate.sample_parse_report
        warnings = ", ".join(candidate.rejection_codes) or (
            "optional fallback"
            if candidate.contradiction_score
            else "none"
        )
        fields = candidate.profile_fields
        lines.append(
            "| "
            + " | ".join(
                (
                    candidate.candidate_alias,
                    fields["timestamp"],
                    fields["service_source"],
                    fields["service_query"],
                    fields["message"],
                    fields["severity"],
                    fields["trace_id"],
                    f"{report.accepted_records}/{report.total_records}",
                    candidate.empirical_query_report.checkout_query_verification,
                    str(candidate.support_score),
                    str(candidate.contradiction_score),
                    str(candidate.net_score),
                    warnings,
                )
            )
            + " |"
        )
    lines.extend(
        (
            "",
            "Select exactly one frozen candidate alias. Do not enter or alter field paths.",
            "Activation still requires offline acceptance and fresh holdout verification.",
            "",
        )
    )
    return "\n".join(lines)


class OpenSearchOperatorProfileDecisionV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-operator-decision.v0222"] = (
        "ecomsre.product.opensearch-operator-decision.v0222"
    )
    decision_id: str = Field(pattern=r"^product-v0222-profile-decision-[12]$")
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    selected_candidate_alias: str | None = Field(default=None, pattern=r"^P[0-9]{2}$")
    decision: OpenSearchOperatorDecisionV0222
    reviewer: str = Field(min_length=2, max_length=120)
    review_note: str = Field(min_length=1, max_length=500)
    decided_at: datetime
    selection_ordinal: int = Field(ge=1, le=2)
    decision_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("decided_at")
    @classmethod
    def decided_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("OpenSearch operator decision time is not aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_bound_decision(self) -> "OpenSearchOperatorProfileDecisionV0222":
        if self.reviewer.strip().upper() == "TEST_REVIEWER":
            raise ValueError("TEST_REVIEWER is forbidden at the live checkpoint")
        if self.decision is OpenSearchOperatorDecisionV0222.SELECT_PROFILE:
            if self.selected_candidate_alias is None:
                raise ValueError("OpenSearch selected profile alias is absent")
        elif self.selected_candidate_alias is not None:
            raise ValueError("OpenSearch reject-all decision selected an alias")
        body = self.model_dump(mode="json", exclude={"decision_sha256"})
        if self.decision_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch operator decision digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchOperatorProfileDecisionV0222":
        draft = cls.model_construct(**values, decision_sha256="0" * 64)
        body = draft.model_dump(mode="json", exclude={"decision_sha256"})
        return cls.model_validate(
            {**body, "decision_sha256": semantic_sha256_v22(body)}
        )


class OpenSearchOperatorDecisionLedgerV0222(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-operator-decision-ledger.v0222"
    ] = "ecomsre.product.opensearch-operator-decision-ledger.v0222"
    candidate_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    decisions: tuple[OpenSearchOperatorProfileDecisionV0222, ...] = Field(
        min_length=1,
        max_length=2,
    )
    ledger_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bounded_decision_history(
        self,
    ) -> "OpenSearchOperatorDecisionLedgerV0222":
        if tuple(decision.selection_ordinal for decision in self.decisions) != tuple(
            range(1, len(self.decisions) + 1)
        ):
            raise ValueError("OpenSearch operator decision ordinals differ")
        if any(
            decision.candidate_set_sha256 != self.candidate_set_sha256
            for decision in self.decisions
        ):
            raise ValueError("OpenSearch operator decision set binding differs")
        aliases = tuple(
            decision.selected_candidate_alias
            for decision in self.decisions
            if decision.selected_candidate_alias is not None
        )
        if len(aliases) != len(set(aliases)):
            raise ValueError("OpenSearch operator decision aliases are duplicated")
        body = self.model_dump(mode="json", exclude={"ledger_sha256"})
        if self.ledger_sha256 != semantic_sha256_v22(body):
            raise ValueError("OpenSearch operator decision ledger digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        candidate_set_sha256: str,
        decisions: tuple[OpenSearchOperatorProfileDecisionV0222, ...],
    ) -> "OpenSearchOperatorDecisionLedgerV0222":
        draft = cls.model_construct(
            candidate_set_sha256=candidate_set_sha256,
            decisions=decisions,
            ledger_sha256="0" * 64,
        )
        body = draft.model_dump(mode="json", exclude={"ledger_sha256"})
        return cls.model_validate({**body, "ledger_sha256": semantic_sha256_v22(body)})


def build_operator_profile_decision_v0222(
    *,
    candidate_set: OpenSearchProfileCandidateSetV0222,
    selected_candidate_alias: str | None,
    reviewer: str,
    review_note: str,
    previous_decisions: Sequence[OpenSearchOperatorProfileDecisionV0222],
    decided_at: datetime | None = None,
) -> OpenSearchOperatorProfileDecisionV0222:
    if len(previous_decisions) >= 2:
        raise ValueError("OpenSearch operator selection budget exhausted")
    if any(
        decision.candidate_set_sha256 != candidate_set.candidate_set_sha256
        for decision in previous_decisions
    ):
        raise ValueError("OpenSearch prior decision candidate-set SHA differs")
    aliases = {candidate.candidate_alias for candidate in candidate_set.candidates}
    if selected_candidate_alias is not None and selected_candidate_alias not in aliases:
        raise ValueError("OpenSearch operator selected an unknown candidate")
    if selected_candidate_alias is not None and any(
        decision.selected_candidate_alias == selected_candidate_alias
        for decision in previous_decisions
    ):
        raise ValueError("OpenSearch candidate cannot be selected twice")
    ordinal = len(previous_decisions) + 1
    return OpenSearchOperatorProfileDecisionV0222.build(
        decision_id=f"product-v0222-profile-decision-{ordinal}",
        candidate_set_sha256=candidate_set.candidate_set_sha256,
        selected_candidate_alias=selected_candidate_alias,
        decision=(
            OpenSearchOperatorDecisionV0222.SELECT_PROFILE
            if selected_candidate_alias is not None
            else OpenSearchOperatorDecisionV0222.REJECT_ALL_CANDIDATES
        ),
        reviewer=reviewer.strip(),
        review_note=review_note.strip(),
        decided_at=decided_at or datetime.now(UTC),
        selection_ordinal=ordinal,
    )


__all__ = (
    "OpenSearchOperatorDecisionV0222",
    "OpenSearchOperatorDecisionLedgerV0222",
    "OpenSearchOperatorProfileDecisionV0222",
    "OpenSearchProfileCandidateSetV0222",
    "OpenSearchProfileCandidateV0222",
    "OpenSearchProfileComponentCandidateV0222",
    "OpenSearchProfileComponentKindV0222",
    "OpenSearchProfileRecommendationStatusV0222",
    "build_component_candidate_v0222",
    "build_operator_profile_decision_v0222",
    "build_profile_candidate_set_v0222",
    "render_operator_brief_v0222",
)
