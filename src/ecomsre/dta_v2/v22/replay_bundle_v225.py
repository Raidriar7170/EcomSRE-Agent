"""v2.2.5 replay adapter for versioned multi-target Resources bundles."""

from __future__ import annotations

from typing import Any

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import (
    QuerySpecificReplayBackendV22,
    ReadOutcomeV22,
    ReplayCaptureV22,
)


class QuerySpecificReplayBackendV225:
    """Keep the frozen v2.2 action contract intact and add one bundle path."""

    def __init__(self, capture: ReplayCaptureV22) -> None:
        self.capture = ReplayCaptureV22.model_validate(capture.model_dump(mode="python"))
        self.legacy = QuerySpecificReplayBackendV22(self.capture)
        self.call_count = 0

    def execute(
        self, action: EvidenceActionV22 | ContrastiveResourceActionV225
    ) -> ReadOutcomeV22:
        self.call_count += 1
        if isinstance(action, EvidenceActionV22):
            return self.legacy.execute(action)
        action = ContrastiveResourceActionV225.model_validate(
            action.model_dump(mode="python")
        )
        failure = next(
            (
                item.status
                for item in self.capture.source_failures
                if item.source is EvidenceSourceV22.RESOURCES
            ),
            None,
        )
        if failure is not None:
            return _outcome(action=action, status=failure, records=())
        by_service = {item.service: item for item in self.capture.resources}
        records = tuple(
            by_service[target]
            for target in action.target_services
            if target in by_service
        )
        if not records:
            return _outcome(
                action=action,
                status=ReadSourceStatusV22.SUCCESS_EMPTY,
                records=(),
            )
        if len(records) != len(action.target_services) or any(
            item.sampling_window_seconds != action.request.sampling_window_seconds
            or len(item.samples) != action.request.sample_count
            for item in records
        ):
            return _outcome(
                action=action,
                status=ReadSourceStatusV22.FAILURE_SCHEMA,
                records=(),
            )
        return _outcome(
            action=action,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            records=records,
        )


def _outcome(
    *,
    action: ContrastiveResourceActionV225,
    status: ReadSourceStatusV22,
    records: tuple[Any, ...],
) -> ReadOutcomeV22:
    payload: dict[str, object] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": status,
        "records": records,
        "truncated": False,
    }
    draft = ReadOutcomeV22.model_construct(
        schema_version="dta-v22.read-outcome.v1",
        action_id=action.action_id,
        source=action.source,
        request_sha256=action.request_sha256,
        status=status,
        records=records,
        truncated=False,
        outcome_sha256="0" * 64,
    )
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


__all__ = ("QuerySpecificReplayBackendV225",)
