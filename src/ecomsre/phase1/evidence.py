"""Run-local, in-memory Evidence allocation and resolution."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum
from typing import cast

from pydantic import Field, ValidationInfo, field_validator

from ecomsre.phase1.contracts import (
    EVIDENCE_REF_PATTERN,
    MAX_EVIDENCE_ATTRIBUTES,
    MAX_EVIDENCE_LIMITATIONS,
    MAX_EVIDENCE_OBSERVATION_TYPE_LENGTH,
    MAX_EVIDENCE_SUMMARY_LENGTH,
    MAX_RAW_ARTIFACT_REF_LENGTH,
    MAX_SERVICE_LENGTH,
    MAX_TEXT_ENTRY_LENGTH,
    Evidence,
    EvidenceAttribute,
    EvidenceScalar,
    EvidenceSource,
    Phase1Model,
)

_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_EVIDENCE_REF_RE = re.compile(EVIDENCE_REF_PATTERN)


class EvidenceStoreErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    MALFORMED_REF = "MALFORMED_REF"
    CROSS_RUN_REF = "CROSS_RUN_REF"
    UNKNOWN_REF = "UNKNOWN_REF"
    SEQUENCE_EXHAUSTED = "SEQUENCE_EXHAUSTED"


class EvidenceStoreError(ValueError):
    """Typed fail-closed EvidenceStore error."""

    def __init__(self, code: EvidenceStoreErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class EvidenceDraft(Phase1Model):
    """Immutable, validated input for one not-yet-allocated Evidence record."""

    source: EvidenceSource
    observation_type: str = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_OBSERVATION_TYPE_LENGTH,
    )
    attributes: tuple[EvidenceAttribute, ...] = Field(
        max_length=MAX_EVIDENCE_ATTRIBUTES,
    )
    raw_artifact_ref: str = Field(
        min_length=1,
        max_length=MAX_RAW_ARTIFACT_REF_LENGTH,
        pattern=r"^(?:metrics|logs|traces|changes)\.json#[0-9]+$",
    )
    raw_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limitations: tuple[str, ...] = Field(
        max_length=MAX_EVIDENCE_LIMITATIONS,
    )
    summary: str = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_SUMMARY_LENGTH,
    )
    started_at: datetime
    ended_at: datetime
    service: str = Field(min_length=1, max_length=MAX_SERVICE_LENGTH)

    @field_validator("observation_type", "summary", "service", mode="before")
    @classmethod
    def trim_required_text(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> str:
        if not isinstance(value, str):
            # Pydantic field validators must raise ValueError for bad input.
            raise ValueError(  # noqa: TRY004
                f"{info.field_name} must be a string"
            )
        trimmed = value.strip()
        if not trimmed:
            raise ValueError(f"{info.field_name} must not be empty")
        return trimmed

    @field_validator("limitations")
    @classmethod
    def require_bounded_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        bounded: list[str] = []
        for value in values:
            if not isinstance(value, str):
                # Pydantic field validators must raise ValueError for bad input.
                raise ValueError(  # noqa: TRY004
                    "limitations must contain strings"
                )
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("limitations must not contain empty text")
            if len(trimmed) > MAX_TEXT_ENTRY_LENGTH:
                raise ValueError(
                    f"limitations exceeds {MAX_TEXT_ENTRY_LENGTH} characters"
                )
            bounded.append(trimmed)
        return tuple(bounded)

    @field_validator("attributes")
    @classmethod
    def require_canonical_attributes(
        cls,
        values: tuple[EvidenceAttribute, ...],
    ) -> tuple[EvidenceAttribute, ...]:
        names = tuple(attribute.name for attribute in values)
        if len(names) != len(set(names)):
            raise ValueError("attributes contain duplicate names")
        if names != tuple(sorted(names)):
            raise ValueError(
                "attributes must use canonical order sorted by name"
            )
        return values


class EvidenceStore:
    """Allocate and resolve immutable Evidence for exactly one run."""

    def __init__(self, run_id: str) -> None:
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            raise ValueError("run_id must be exactly 32 lowercase hex characters")
        self._run_id = run_id
        self._source_counters = {source: 0 for source in EvidenceSource}
        self._items: list[Evidence] = []
        self._by_ref: dict[str, Evidence] = {}

    @property
    def run_id(self) -> str:
        return self._run_id

    def add(
        self,
        *,
        source: EvidenceSource,
        observation_type: str,
        attributes: Mapping[str, object]
        | Iterable[EvidenceAttribute],
        raw_artifact_ref: str,
        raw_artifact_sha256: str,
        limitations: tuple[str, ...],
        summary: str,
        started_at: datetime,
        ended_at: datetime,
        service: str,
    ) -> Evidence:
        """Allocate the next per-source reference and return the Evidence."""

        try:
            if not isinstance(source, EvidenceSource):
                raise TypeError("source must be an EvidenceSource")
            if isinstance(attributes, Mapping):
                if any(not isinstance(name, str) for name in attributes):
                    raise ValueError("attribute mapping keys must be strings")
                canonical_attributes = tuple(
                    EvidenceAttribute(
                        name=name,
                        value=cast(EvidenceScalar, value),
                    )
                    for name, value in sorted(attributes.items())
                )
            else:
                canonical_attributes = tuple(attributes)
                if any(
                    not isinstance(attribute, EvidenceAttribute)
                    for attribute in canonical_attributes
                ):
                    raise ValueError(
                        "iterable attributes must contain EvidenceAttribute "
                        "records"
                    )

            draft = EvidenceDraft(
                source=source,
                observation_type=observation_type,
                attributes=canonical_attributes,
                raw_artifact_ref=raw_artifact_ref,
                raw_artifact_sha256=raw_artifact_sha256,
                limitations=limitations,
                summary=summary,
                started_at=started_at,
                ended_at=ended_at,
                service=service,
            )
        except EvidenceStoreError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise EvidenceStoreError(
                EvidenceStoreErrorCode.INVALID_INPUT,
                f"{type(error).__name__}: {error}",
            ) from error

        return self.add_batch((draft,))[0]

    def add_batch(
        self,
        drafts: tuple[EvidenceDraft, ...],
    ) -> tuple[Evidence, ...]:
        """Validate and allocate one all-or-nothing Evidence batch."""

        # Local import avoids a module cycle: validator resolves store errors.
        from ecomsre.phase1.validator import revalidate_phase1_model

        if type(drafts) is not tuple:
            raise EvidenceStoreError(
                EvidenceStoreErrorCode.INVALID_INPUT,
                "drafts must be an exact tuple",
            )

        candidate_counters = dict(self._source_counters)
        candidate_items: list[Evidence] = []
        try:
            for draft in drafts:
                validated_draft = revalidate_phase1_model(
                    draft,
                    EvidenceDraft,
                )
                source = validated_draft.source
                next_sequence = candidate_counters[source] + 1
                if next_sequence > 9999:
                    raise EvidenceStoreError(
                        EvidenceStoreErrorCode.SEQUENCE_EXHAUSTED,
                        f"{source.value} reference space is exhausted",
                    )
                evidence_ref = (
                    f"evidence://{self._run_id}/{source.value.lower()}/"
                    f"{next_sequence:04d}"
                )
                candidate_items.append(
                    Evidence(
                        schema_version="phase1.evidence.v1",
                        evidence_ref=evidence_ref,
                        run_id=self._run_id,
                        source=source,
                        observation_type=validated_draft.observation_type,
                        attributes=validated_draft.attributes,
                        raw_artifact_ref=validated_draft.raw_artifact_ref,
                        raw_artifact_sha256=(
                            validated_draft.raw_artifact_sha256
                        ),
                        limitations=validated_draft.limitations,
                        summary=validated_draft.summary,
                        started_at=validated_draft.started_at,
                        ended_at=validated_draft.ended_at,
                        service=validated_draft.service,
                    )
                )
                candidate_counters[source] = next_sequence
        except EvidenceStoreError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise EvidenceStoreError(
                EvidenceStoreErrorCode.INVALID_INPUT,
                f"{type(error).__name__}: {error}",
            ) from error

        replacement_items = [*self._items, *candidate_items]
        replacement_by_ref = self._by_ref | {
            item.evidence_ref: item for item in candidate_items
        }
        self._source_counters = candidate_counters
        self._items = replacement_items
        self._by_ref = replacement_by_ref
        return tuple(candidate_items)

    def resolve(self, reference: str) -> Evidence:
        if (
            not isinstance(reference, str)
            or _EVIDENCE_REF_RE.fullmatch(reference) is None
        ):
            raise EvidenceStoreError(
                EvidenceStoreErrorCode.MALFORMED_REF,
                "evidence reference does not match the exact grammar",
            )
        reference_run_id = reference.split("/")[2]
        if reference_run_id != self._run_id:
            raise EvidenceStoreError(
                EvidenceStoreErrorCode.CROSS_RUN_REF,
                "evidence reference belongs to another run",
            )
        try:
            return self._by_ref[reference]
        except KeyError as error:
            raise EvidenceStoreError(
                EvidenceStoreErrorCode.UNKNOWN_REF,
                "evidence reference is not present in this store",
            ) from error

    def snapshot(self) -> tuple[Evidence, ...]:
        """Return Evidence in deterministic insertion order."""

        return tuple(self._items)
