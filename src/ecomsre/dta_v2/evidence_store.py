"""Run-scoped immutable DTA v2 Evidence Store snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.contracts import (
    DtaModel,
    ResolvedDiagnosisEvidenceView,
    ResolvedEvidence,
    RunId,
    Sha256,
    build_resolved_diagnosis_evidence_view,
    semantic_sha256,
)
from ecomsre.dta_v2.tool_contracts import (
    ObservationStatus,
    ReadAuthorityContext,
    ReadToolObservation,
    ReadToolRequest,
    ToolErrorCode,
    ToolName,
    parse_read_tool_request_json,
    revalidate_observation,
    revalidate_read_tool_request,
    validate_results_for_request,
    validate_truncation_for_request,
)


class CanonicalRequestEnvelope(DtaModel):
    schema_version: Literal["dta-v2.canonical-request-envelope.v1"]
    tool: ToolName
    run_id: RunId
    request_sha256: Sha256
    canonical_request_json: str = Field(min_length=2, max_length=4096)
    envelope_sha256: Sha256

    @model_validator(mode="after")
    def require_envelope(self) -> CanonicalRequestEnvelope:
        request = parse_read_tool_request_json(self.canonical_request_json)
        if (
            request.tool is not self.tool
            or request.run_id != self.run_id
            or request.normalized_request_sha256 != self.request_sha256
        ):
            raise ValueError("request envelope binding differs")
        canonical = json.dumps(
            request.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if self.canonical_request_json != canonical:
            raise ValueError("request envelope JSON is not canonical")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"envelope_sha256"})
        )
        if self.envelope_sha256 != expected:
            raise ValueError("request envelope digest does not bind envelope")
        return self

    def resolve(self) -> ReadToolRequest:
        revalidated = CanonicalRequestEnvelope.model_validate(self.model_dump())
        return parse_read_tool_request_json(revalidated.canonical_request_json)


def build_canonical_request_envelope(
    request: ReadToolRequest,
) -> CanonicalRequestEnvelope:
    validated = revalidate_read_tool_request(request)
    canonical = json.dumps(
        validated.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.canonical-request-envelope.v1",
        "tool": validated.tool,
        "run_id": validated.run_id,
        "request_sha256": validated.normalized_request_sha256,
        "canonical_request_json": canonical,
    }
    return CanonicalRequestEnvelope.model_validate(
        {**payload, "envelope_sha256": semantic_sha256(payload)}
    )


class EvidenceStoreSnapshot(DtaModel):
    schema_version: Literal["dta-v2.evidence-store-snapshot.v1"]
    run_id: RunId
    authority: ReadAuthorityContext
    authority_sha256: Sha256
    maximum_dispatches: Literal[4]
    dispatch_count: StrictInt = Field(ge=0, le=4)
    request_envelopes: tuple[CanonicalRequestEnvelope, ...] = Field(max_length=4)
    observations: tuple[ReadToolObservation, ...] = Field(max_length=4)
    evidence_store_sha256: Sha256

    @model_validator(mode="after")
    def require_store_semantics(self) -> EvidenceStoreSnapshot:
        envelopes = tuple(
            CanonicalRequestEnvelope.model_validate(item.model_dump())
            for item in self.request_envelopes
        )
        observations = tuple(
            revalidate_observation(item) for item in self.observations
        )
        authority = ReadAuthorityContext.model_validate(self.authority.model_dump())
        if authority.authority_sha256 != self.authority_sha256:
            raise ValueError("store authority context differs from digest")
        if len(observations) != self.dispatch_count:
            raise ValueError("store observation count differs from dispatch count")
        if any(item.run_id != self.run_id for item in envelopes) or any(
            item.run_id != self.run_id for item in observations
        ):
            raise ValueError("store contains an observation from another run")
        if any(
            item.authority != authority
            or item.authority_sha256 != self.authority_sha256
            for item in observations
        ):
            raise ValueError("store observation authority differs")
        if envelopes != tuple(sorted(envelopes, key=lambda item: item.request_sha256)):
            raise ValueError("request envelopes are not canonically keyed by digest")
        envelope_shas = tuple(item.request_sha256 for item in envelopes)
        if len(envelope_shas) != len(set(envelope_shas)):
            raise ValueError("store request envelopes contain duplicate digests")
        by_request = {item.request_sha256: item.resolve() for item in envelopes}
        observed_request_shas = {
            item.request_sha256 for item in observations
        }
        if set(by_request) != observed_request_shas:
            raise ValueError(
                "request envelope keys differ from observed request digests"
            )
        ordinals = tuple(item.counters.dispatch_ordinal for item in observations)
        if ordinals != tuple(range(1, self.dispatch_count + 1)):
            raise ValueError("store observations are not in dispatch order")
        refs = tuple(item.evidence_ref for item in observations)
        if len(refs) != len(set(refs)):
            raise ValueError("store contains duplicate evidence references")
        seen_requests: set[str] = set()
        backend_calls = successes = failures = 0
        for observation in observations:
            request = by_request.get(observation.request_sha256)
            if request is None:
                raise ValueError("observation has no resolver-backed request envelope")
            if observation.error_code is ToolErrorCode.DUPLICATE_REQUEST:
                if observation.request_sha256 not in seen_requests:
                    raise ValueError("duplicate reason lacks a prior identical digest")
            else:
                if observation.request_sha256 in seen_requests:
                    raise ValueError("repeated request digest is not typed duplicate")
                seen_requests.add(observation.request_sha256)
                backend_calls += 1
            if observation.status is ObservationStatus.SUCCESS:
                successes += 1
                validate_results_for_request(request, observation.results)
                validate_truncation_for_request(
                    request, observation.results, observation.truncated
                )
            else:
                failures += 1
            if (
                observation.tool is not request.tool
                or observation.run_id != request.run_id
                or observation.counters.backend_call_count != backend_calls
                or observation.counters.success_count != successes
                or observation.counters.failure_count != failures
            ):
                raise ValueError("observation request or counter state machine differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"evidence_store_sha256"})
        )
        if self.evidence_store_sha256 != expected:
            raise ValueError("Evidence Store digest does not bind snapshot")
        return self

    def persist_create_once(self, path: Path) -> str:
        """Persist one immutable snapshot to a private create-once file."""

        validated = EvidenceStoreSnapshot.model_validate_json(self.model_dump_json())
        target = Path(path)
        _ensure_private_directory(target.parent)
        payload = validated.model_dump_json().encode("utf-8")
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file():
                raise ValueError("Evidence Store target is not a regular file")
            if target.read_bytes() != payload:
                raise FileExistsError("create-once Evidence Store snapshot differs")
            if target.stat().st_mode & 0o777 != 0o600:
                raise PermissionError("Evidence Store file permissions differ")
            return self.evidence_store_sha256
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            raise
        target.chmod(0o600)
        if target.stat().st_mode & 0o777 != 0o600:
            raise PermissionError("Evidence Store file permissions differ")
        return self.evidence_store_sha256


def build_evidence_store_snapshot(
    *,
    run_id: str,
    authority: ReadAuthorityContext,
    request_envelopes: tuple[CanonicalRequestEnvelope, ...],
    observations: tuple[ReadToolObservation, ...],
) -> EvidenceStoreSnapshot:
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.evidence-store-snapshot.v1",
        "run_id": run_id,
        "authority": authority,
        "authority_sha256": authority.authority_sha256,
        "maximum_dispatches": 4,
        "dispatch_count": len(observations),
        "request_envelopes": tuple(
            sorted(request_envelopes, key=lambda item: item.request_sha256)
        ),
        "observations": observations,
    }
    draft = EvidenceStoreSnapshot.model_construct(
        **payload, evidence_store_sha256="0" * 64
    )
    return EvidenceStoreSnapshot.model_validate(
        {
            **payload,
            "evidence_store_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"evidence_store_sha256"})
            ),
        }
    )


def resolve_diagnosis_view(
    snapshot: EvidenceStoreSnapshot, *, evidence_refs: tuple[str, ...]
) -> ResolvedDiagnosisEvidenceView:
    """Resolve only diagnosis-cited successful references from the full store."""

    snapshot = EvidenceStoreSnapshot.model_validate_json(snapshot.model_dump_json())
    if len(evidence_refs) != len(set(evidence_refs)):
        raise ValueError("diagnosis evidence references contain duplicates")
    by_ref = {item.evidence_ref: item for item in snapshot.observations}
    resolved: list[ResolvedEvidence] = []
    for reference in evidence_refs:
        observation = by_ref.get(reference)
        if observation is None:
            raise ValueError("diagnosis cites evidence outside the full store")
        if observation.status is not ObservationStatus.SUCCESS:
            raise ValueError("diagnosis cannot cite a failed tool observation")
        resolved.append(
            ResolvedEvidence(
                evidence_ref=observation.evidence_ref,
                source=observation.source,
                artifact_sha256=observation.artifact_sha256,
            )
        )
    return build_resolved_diagnosis_evidence_view(
        run_id=snapshot.run_id, evidence=tuple(resolved)
    )


def _ensure_private_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        if current.is_symlink():
            raise ValueError("Evidence Store parent is a symbolic link")
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError("Evidence Store ancestor is not a directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        directory.chmod(0o700)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Evidence Store parent is not a directory")
    path.chmod(0o700)
    if path.stat().st_mode & 0o777 != 0o700:
        raise PermissionError("Evidence Store directory permissions differ")
