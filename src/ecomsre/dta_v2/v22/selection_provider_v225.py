"""Provider adapter that lints every exact rendered payload before transport."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
import time
from typing import cast

from ecomsre.dta_v2.v22.provider_identity_lint_v225 import (
    ProviderIdentityLintReportV225,
    lint_provider_payload_v225,
)
from ecomsre.dta_v2.v22.selection_provider_v222 import SelectionTurnRequestV222
from ecomsre.dta_v2.v22.simple_provider import ProviderTransportV22
from ecomsre.dta_v2.v22.selection_provider_v223 import SelectionProviderV223
from ecomsre.model.gateway import OpenAICompatibleConfig


class SelectionProviderV225(SelectionProviderV223):
    """Fail before transport if an initial or repair payload leaks identities."""

    def __init__(
        self,
        *,
        config: OpenAICompatibleConfig,
        transport: ProviderTransportV22 | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        minimum_request_interval_seconds: float = 4.0,
        timeout_seconds: float = 120.0,
        max_completion_tokens: int = 120,
        debug_root: Path = Path(".local/dta-v22-5-debug"),
    ) -> None:
        super().__init__(
            config=config,
            transport=transport,
            sleeper=sleeper,
            clock=clock,
            minimum_request_interval_seconds=minimum_request_interval_seconds,
            timeout_seconds=timeout_seconds,
            max_completion_tokens=max_completion_tokens,
            debug_root=debug_root,
        )
        self._identity_lint_reports: list[ProviderIdentityLintReportV225] = []

    @property
    def identity_lint_reports(self) -> tuple[ProviderIdentityLintReportV225, ...]:
        return tuple(self._identity_lint_reports)

    def _payload(
        self, *, request: SelectionTurnRequestV222, repair_code: str | None
    ) -> dict[str, object]:
        payload = super()._payload(request=request, repair_code=repair_code)
        messages = cast(list[dict[str, object]], payload["messages"])
        rendered_user = json.loads(cast(str, messages[1]["content"]))
        if repair_code is not None:
            payload_class = "repair"
        elif request.visible_state.get("last_contrast") is not None:
            payload_class = "post-bundle-read"
        else:
            closure = request.visible_state.get("closure")
            read_count = (
                closure.get("read_count", 0)
                if isinstance(closure, dict)
                else 0
            )
            if isinstance(read_count, int) and read_count > 0:
                payload_class = "post-individual-read"
            elif request.visible_state.get("actions") is not None:
                payload_class = "bootstrap"
            else:
                payload_class = "terminal-only"
        report = lint_provider_payload_v225(
            rendered_user,
            payload_class=payload_class,
        )
        self._identity_lint_reports.append(report)
        return payload


__all__ = ("SelectionProviderV225",)
