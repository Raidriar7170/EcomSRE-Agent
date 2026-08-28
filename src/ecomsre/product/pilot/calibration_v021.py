"""Product v0.2.1 successor profile-calibration contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping

from pydantic import Field, ValidationInfo, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


class QueueProfileV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.pilot.queue-profile.v021"] = (
        "ecomsre.product.pilot.queue-profile.v021"
    )
    profile_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    profile_name: Literal["CHECKOUT_KAFKA_QUEUE_OVERLOAD"] = (
        "CHECKOUT_KAFKA_QUEUE_OVERLOAD"
    )
    candidate_values: tuple[int, ...] = Field(min_length=3, max_length=3)
    maximum_calibration_changes: Literal[2] = 2
    expected_default_value: Literal[0] = 0
    baseline_binding_required: Literal[True] = True
    selected_value: int | None = Field(default=None, ge=1, le=20)
    selected_root_service: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]{0,63}$",
    )
    calibration_report_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    calibration_contract_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    calibration_runtime_binding_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    calibrated_at: datetime | None = None
    profile_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @property
    def contract_sha256(self) -> str:
        return semantic_sha256_v22(
            {
                "schema_version": self.schema_version,
                "profile_id": self.profile_id,
                "profile_name": self.profile_name,
                "candidate_values": list(self.candidate_values),
                "maximum_calibration_changes": self.maximum_calibration_changes,
                "expected_default_value": self.expected_default_value,
                "baseline_binding_required": self.baseline_binding_required,
            }
        )

    @model_validator(mode="after")
    def require_bound_profile(self, info: ValidationInfo) -> "QueueProfileV021":
        if self.candidate_values != (5, 10, 20):
            raise ValueError("successor queue candidates differ")
        selection = (
            self.selected_value,
            self.selected_root_service,
            self.calibration_report_sha256,
            self.calibration_contract_sha256,
            self.calibration_runtime_binding_sha256,
            self.calibrated_at,
        )
        if any(value is not None for value in selection) and any(
            value is None for value in selection
        ):
            raise ValueError("successor queue profile selection is incomplete")
        if self.selected_value is None:
            if self.profile_sha256 is not None:
                raise ValueError("unselected queue profile cannot be frozen")
            return self
        if (
            self.selected_value not in self.candidate_values
            or self.selected_root_service != "checkout"
            or self.calibration_contract_sha256 != self.contract_sha256
            or self.calibrated_at is None
            or self.calibrated_at.tzinfo is None
            or self.calibrated_at.utcoffset() != timedelta(0)
            or self.profile_sha256 is None
        ):
            raise ValueError("successor queue profile selection differs")
        if info.context and info.context.get("skip_profile_digest") is True:
            return self
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("successor queue profile digest differs")
        return self

    def freeze(
        self,
        *,
        selected_value: int,
        selected_root_service: str,
        calibration_report_sha256: str,
        calibration_runtime_binding_sha256: str,
        calibrated_at: str | datetime,
    ) -> "QueueProfileV021":
        if self.selected_value is not None or self.profile_sha256 is not None:
            raise ValueError("successor queue profile is already frozen")
        timestamp = (
            datetime.fromisoformat(calibrated_at.replace("Z", "+00:00"))
            if isinstance(calibrated_at, str)
            else calibrated_at
        ).astimezone(UTC)
        body: dict[str, Any] = {
            **self.model_dump(mode="json", exclude={"profile_sha256"}),
            "selected_value": selected_value,
            "selected_root_service": selected_root_service,
            "calibration_report_sha256": calibration_report_sha256,
            "calibration_contract_sha256": self.contract_sha256,
            "calibration_runtime_binding_sha256": (
                calibration_runtime_binding_sha256
            ),
            "calibrated_at": timestamp,
        }
        draft = type(self).model_validate(
            {**body, "profile_sha256": "0" * 64},
            context={"skip_profile_digest": True},
        )
        normalized = draft.model_dump(mode="json", exclude={"profile_sha256"})
        return type(self).model_validate(
            {
                **normalized,
                "profile_sha256": semantic_sha256_v22(normalized),
            }
        )


def render_public_calibration_markdown_v021(
    payload: Mapping[str, object],
) -> str:
    """Render the deterministic truth-isolated public calibration summary."""

    return """# Product v0.2.1 unknown-fault profile calibration

Terminal: `{terminal}`

- Calibration runs: `{runs}`
- Changed calibration iterations: `{changes}`
- Selected observer-visible root: `{root}`
- Frozen readiness baseline unchanged: `{baseline}`
- Post-run outer baseline restored: `{restored}`
- Owned Demo cleanup: `{cleanup}`
- Action authority: `NONE`
- Agent writes: `0`
- Runbook executions: `0`

This public report intentionally excludes evaluator-only control identifiers,
numeric control values, expected mechanism labels, truth mechanisms, and
injection commands. Private evidence is referenced only by SHA-256.
""".format(
        terminal=payload["terminal"],
        runs=payload["calibration_iteration_count"],
        changes=payload["changed_calibration_iteration_count"],
        root=payload.get("selected_root_service") or "NONE",
        baseline=str(payload["active_baseline_unchanged"]).lower(),
        restored=str(payload["outer_baseline_restored"]).lower(),
        cleanup=payload["owned_demo_cleanup"],
    )


__all__ = ("QueueProfileV021", "render_public_calibration_markdown_v021")
