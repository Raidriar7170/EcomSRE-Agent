"""Opaque one-shot capability joining an owned campaign claim to one runner."""

from __future__ import annotations

from dataclasses import dataclass, field

from ecomsre.dta_v2.live_contracts import LiveCampaignAttemptClaim


_OWNED_CAMPAIGN_TOKEN = object()


@dataclass(frozen=True, init=False)
class OwnedLiveExecutionGrant:
    claim: LiveCampaignAttemptClaim
    lifecycle_identity: int
    _token: object = field(repr=False, compare=False)
    _consumed: list[bool] = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        claim: LiveCampaignAttemptClaim,
        lifecycle: object,
        _token: object | None = None,
    ) -> None:
        if _token is not _OWNED_CAMPAIGN_TOKEN:
            raise TypeError("owned live execution grants are campaign-issued only")
        typed = LiveCampaignAttemptClaim.model_validate(
            claim.model_dump(mode="python")
        )
        object.__setattr__(self, "claim", typed)
        object.__setattr__(self, "lifecycle_identity", id(lifecycle))
        object.__setattr__(self, "_token", _OWNED_CAMPAIGN_TOKEN)
        object.__setattr__(self, "_consumed", [False])

    def consume(self, *, lifecycle: object, attempt_id: str) -> LiveCampaignAttemptClaim:
        if (
            self._token is not _OWNED_CAMPAIGN_TOKEN
            or self.lifecycle_identity != id(lifecycle)
            or self.claim.attempt_id != attempt_id
            or self._consumed[0]
        ):
            raise TypeError("owned live execution grant is invalid or already consumed")
        self._consumed[0] = True
        return LiveCampaignAttemptClaim.model_validate(
            self.claim.model_dump(mode="python")
        )


def issue_owned_live_execution_grant(
    *,
    claim: LiveCampaignAttemptClaim,
    lifecycle: object,
    _token: object | None = None,
) -> OwnedLiveExecutionGrant:
    if _token is not _OWNED_CAMPAIGN_TOKEN:
        raise TypeError("only the owned campaign issuer can grant live execution")
    return OwnedLiveExecutionGrant(
        claim=claim,
        lifecycle=lifecycle,
        _token=_OWNED_CAMPAIGN_TOKEN,
    )


__all__ = ["OwnedLiveExecutionGrant"]
