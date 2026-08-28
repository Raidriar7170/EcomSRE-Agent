"""Product v0.2.1 baseline-readiness profiles and bounded attempt contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationInfo, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import BaselineBuildModeV1, BaselineBuildPolicyV1
from ecomsre.product.contracts import ProductModelV1


class HealthyTrafficProfileV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.healthy-traffic-profile.v021"] = (
        "ecomsre.product.healthy-traffic-profile.v021"
    )
    request_seed: int = Field(ge=0)
    maximum_request_count: int = Field(ge=1, le=180)
    requests_per_second: float = Field(gt=0, le=2, allow_inf_nan=False)
    error_budget: int = Field(ge=1, le=20)


class HealthyTrafficRunResultV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.healthy-traffic-result.v021"] = (
        "ecomsre.product.healthy-traffic-result.v021"
    )
    request_seed: int = Field(ge=0)
    attempted: int = Field(ge=0, le=180)
    succeeded: int = Field(ge=0, le=180)
    failed: int = Field(ge=0, le=180)
    stopped_on_error_budget: bool
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_traffic_result(self) -> "HealthyTrafficRunResultV021":
        if self.attempted != self.succeeded + self.failed:
            raise ValueError("healthy traffic counts differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("healthy traffic result digest differs")
        return self


class QueueDefaultObservationV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.queue-default-observation.v021"] = (
        "ecomsre.product.queue-default-observation.v021"
    )
    default_value: int
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unchanged: Literal[True] = True
    observation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_observation(self) -> "QueueDefaultObservationV021":
        if self.before_sha256 != self.after_sha256:
            raise ValueError("queue default bytes changed during observation")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"observation_sha256"})
        )
        if self.observation_sha256 != expected:
            raise ValueError("queue default observation digest differs")
        return self


_PRIVATE_QUEUE_FLAG_KEY_V021 = "kafkaQueueProblems"


def _read_regular_bytes_v021(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("queue default runtime is not a regular file")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def verify_queue_default_v021(
    runtime_path: Path,
    *,
    expected_default_value: int,
    expected_sha256: str | None = None,
) -> QueueDefaultObservationV021:
    """Evaluator-only observation; it never exposes the private flag key."""

    before = _read_regular_bytes_v021(Path(runtime_path))
    before_sha256 = hashlib.sha256(before).hexdigest()
    if expected_sha256 is not None and before_sha256 != expected_sha256:
        raise ValueError("queue default baseline digest differs")
    payload = json.loads(before)
    flags = payload.get("flags") if isinstance(payload, dict) else None
    target = flags.get(_PRIVATE_QUEUE_FLAG_KEY_V021) if isinstance(flags, dict) else None
    variants = target.get("variants") if isinstance(target, dict) else None
    default_variant = target.get("defaultVariant") if isinstance(target, dict) else None
    if (
        not isinstance(target, dict)
        or target.get("state") != "ENABLED"
        or not isinstance(variants, dict)
        or not isinstance(default_variant, str)
        or variants.get(default_variant) != expected_default_value
        or variants.get("off") != expected_default_value
    ):
        raise ValueError("queue flag active default differs")
    after = _read_regular_bytes_v021(Path(runtime_path))
    after_sha256 = hashlib.sha256(after).hexdigest()
    body = {
        "schema_version": "ecomsre.product.queue-default-observation.v021",
        "default_value": expected_default_value,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "unchanged": True,
    }
    return QueueDefaultObservationV021.model_validate(
        {**body, "observation_sha256": semantic_sha256_v22(body)}
    )


class BoundedHealthyCheckoutTrafficV021:
    def __init__(
        self,
        *,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.sleep = sleep

    @staticmethod
    def _checkout_payload(seed: int, ordinal: int) -> dict[str, object]:
        suffix = f"{seed:010d}-{ordinal:03d}"
        return {
            "userId": f"readiness-{suffix}",
            "userCurrency": "USD",
            "email": f"readiness-{suffix}@example.invalid",
            "address": {
                "streetAddress": "1 Readiness Way",
                "city": "Local",
                "state": "CA",
                "country": "United States",
                "zipCode": "94016",
            },
            "creditCard": {
                "creditCardNumber": "4111111111111111",
                "creditCardCvv": 123,
                "creditCardExpirationYear": 2030,
                "creditCardExpirationMonth": 12,
            },
        }

    @staticmethod
    def _cart_payload(seed: int, ordinal: int) -> dict[str, object]:
        suffix = f"{seed:010d}-{ordinal:03d}"
        return {
            "userId": f"readiness-{suffix}",
            "item": {"productId": "0PUK6V6EV0", "quantity": 1},
        }

    def run(
        self,
        *,
        endpoint: str,
        profile: HealthyTrafficProfileV021,
    ) -> HealthyTrafficRunResultV021:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.path != "/api/checkout"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("healthy traffic endpoint must be the local checkout API")
        started = time.monotonic()
        cart_endpoint = endpoint.removesuffix("/api/checkout") + "/api/cart"
        attempted = succeeded = failed = 0
        for ordinal in range(1, profile.maximum_request_count + 1):
            try:
                cart_response = self.client.post(
                    cart_endpoint,
                    json=self._cart_payload(profile.request_seed, ordinal),
                    timeout=10.0,
                )
                response = (
                    self.client.post(
                        endpoint,
                        json=self._checkout_payload(profile.request_seed, ordinal),
                        timeout=20.0,
                    )
                    if 200 <= cart_response.status_code < 300
                    else cart_response
                )
                request_succeeded = 200 <= response.status_code < 300
            except httpx.RequestError:
                request_succeeded = False
            attempted += 1
            if request_succeeded:
                succeeded += 1
            else:
                failed += 1
            if failed >= profile.error_budget:
                break
            if ordinal < profile.maximum_request_count:
                self.sleep(1.0 / profile.requests_per_second)
        body = {
            "schema_version": "ecomsre.product.healthy-traffic-result.v021",
            "request_seed": profile.request_seed,
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "stopped_on_error_budget": failed >= profile.error_budget,
            "duration_seconds": max(0.0, time.monotonic() - started),
        }
        return HealthyTrafficRunResultV021.model_validate(
            {**body, "result_sha256": semantic_sha256_v22(body)}
        )


class ReadinessChangeParameterV021(str, Enum):
    INITIAL = "INITIAL"
    HEALTHY_TRAFFIC_DURATION = "HEALTHY_TRAFFIC_DURATION"
    HEALTHY_TRAFFIC_RATE = "HEALTHY_TRAFFIC_RATE"
    OTEL_STABILIZATION_DURATION = "OTEL_STABILIZATION_DURATION"
    BASELINE_ACCUMULATION_DURATION = "BASELINE_ACCUMULATION_DURATION"
    BASELINE_MINIMUM_SUCCESSFUL_WINDOWS = "BASELINE_MINIMUM_SUCCESSFUL_WINDOWS"
    CONNECTOR_QUERY_TEMPLATE = "CONNECTOR_QUERY_TEMPLATE"
    SERVICE_ALIAS_MAPPING = "SERVICE_ALIAS_MAPPING"
    TARGET_COMPLETE_CAPABILITY_DECLARATION = (
        "TARGET_COMPLETE_CAPABILITY_DECLARATION"
    )


class ReadinessAttemptDispositionV021(str, Enum):
    PASS = "PASS"
    TARGETED_REPAIR_ELIGIBLE = "TARGETED_REPAIR_ELIGIBLE"
    INFRASTRUCTURE_REPLACEMENT_ELIGIBLE = (
        "INFRASTRUCTURE_REPLACEMENT_ELIGIBLE"
    )
    BLOCKED = "BLOCKED"


class ReadinessFailureDomainV021(str, Enum):
    NONE = "NONE"
    INFRASTRUCTURE_STARTUP = "INFRASTRUCTURE_STARTUP"
    CAMPAIGN = "CAMPAIGN"
    CLEANUP = "CLEANUP"
    INTERRUPTED = "INTERRUPTED"
    EVIDENCE = "EVIDENCE"


class ReadinessSemanticInputsV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.readiness-semantic-inputs.v021"] = (
        "ecomsre.product.readiness-semantic-inputs.v021"
    )
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_services: tuple[str, ...]
    build_mode: Literal["DEMO_ONLY"] = "DEMO_ONLY"
    lookback_seconds: Literal[180] = 180
    window_count: Literal[5] = 5
    warmup_seconds: Literal[180] = 180
    stabilization_seconds: int = Field(ge=0, le=600)
    baseline_accumulation_seconds: int = Field(ge=180, le=900)
    minimum_successful_windows: int = Field(ge=4, le=5)
    healthy_traffic_maximum_request_count: int = Field(ge=1, le=180)
    healthy_traffic_request_seed: Literal[501] = 501
    healthy_traffic_error_budget: Literal[12] = 12
    healthy_traffic_requests_per_second: float = Field(
        gt=0,
        le=2,
        allow_inf_nan=False,
    )
    connector_query_bindings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_query_templates_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    service_alias_mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_source_policy_id: str = Field(pattern=r"^[A-Z0-9_]{1,120}$")
    semantics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_semantics(self) -> "ReadinessSemanticInputsV021":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"semantics_sha256"})
        )
        if self.semantics_sha256 != expected:
            raise ValueError("readiness semantic input digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "ReadinessSemanticInputsV021":
        body = {
            "schema_version": "ecomsre.product.readiness-semantic-inputs.v021",
            **payload,
        }
        body["semantics_sha256"] = semantic_sha256_v22(body)
        return cls.model_validate(body)


class ReadinessAttemptSignatureV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.readiness-attempt-signature.v021"] = (
        "ecomsre.product.readiness-attempt-signature.v021"
    )
    semantic_inputs: ReadinessSemanticInputsV021
    changed_parameter: ReadinessChangeParameterV021
    prior_audit_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    prior_rejection_reason_codes: tuple[str, ...] = ()
    attempt_signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_signature(self) -> "ReadinessAttemptSignatureV021":
        if self.changed_parameter is ReadinessChangeParameterV021.INITIAL:
            if self.prior_audit_sha256 is not None or self.prior_rejection_reason_codes:
                raise ValueError("initial readiness attempt must not claim prior evidence")
        elif self.prior_audit_sha256 is None or not self.prior_rejection_reason_codes:
            raise ValueError("changed readiness attempt requires prior audit evidence")
        if self.prior_rejection_reason_codes != tuple(
            sorted(set(self.prior_rejection_reason_codes))
        ):
            raise ValueError("readiness rejection reasons are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"attempt_signature_sha256"})
        )
        if self.attempt_signature_sha256 != expected:
            raise ValueError("readiness attempt signature digest differs")
        return self


def build_readiness_attempt_signature_v021(
    *,
    semantic_inputs: ReadinessSemanticInputsV021,
    changed_parameter: ReadinessChangeParameterV021,
    prior_audit_sha256: str | None = None,
    prior_rejection_reason_codes: tuple[str, ...] = (),
) -> ReadinessAttemptSignatureV021:
    body = {
        "schema_version": "ecomsre.product.readiness-attempt-signature.v021",
        "semantic_inputs": semantic_inputs.model_dump(mode="json"),
        "changed_parameter": changed_parameter.value,
        "prior_audit_sha256": prior_audit_sha256,
        "prior_rejection_reason_codes": list(prior_rejection_reason_codes),
    }
    return ReadinessAttemptSignatureV021.model_validate(
        {**body, "attempt_signature_sha256": semantic_sha256_v22(body)}
    )


class PilotBaselineBindingV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.pilot-baseline-binding.v021"] = (
        "ecomsre.product.pilot-baseline-binding.v021"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS"] = (
        "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    product_data_root: str = Field(
        pattern=(
            r"^\.local/product-v021/baseline-readiness/"
            r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
        )
    )
    readiness_private_root: str = Field(
        pattern=(
            r"^\.local/product-v021/private-baseline-readiness/runs/"
            r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
        )
    )
    queue_flag_ref: Literal["runtime/flagd/demo.flagd.json"] = (
        "runtime/flagd/demo.flagd.json"
    )
    runtime_snapshot_ref: Literal["pilot/runtime-readiness.json"] = (
        "pilot/runtime-readiness.json"
    )
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_policy: BaselineBuildPolicyV1
    accepted_window_ordinals: tuple[int, ...] = Field(min_length=4, max_length=5)
    source_coverage_matrix: dict[str, dict[str, int]]
    service_identity_map_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    healthy_traffic_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_at: datetime
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_pilot_baseline(
        self,
        info: ValidationInfo,
    ) -> "PilotBaselineBindingV021":
        if self.accepted_window_ordinals != tuple(
            sorted(set(self.accepted_window_ordinals))
        ) or any(not 1 <= ordinal <= 5 for ordinal in self.accepted_window_ordinals):
            raise ValueError("pilot baseline accepted-window ordinals differ")
        if (
            PurePosixPath(self.product_data_root).name
            != PurePosixPath(self.readiness_private_root).name
        ):
            raise ValueError("pilot baseline lifecycle run binding differs")
        if (
            self.build_policy.mode is not BaselineBuildModeV1.DEMO_ONLY
            or self.build_policy.window_count != 5
            or self.build_policy.minimum_successful_windows < 4
        ):
            raise ValueError("pilot baseline build policy differs")
        if (
            self.frozen_at.tzinfo is None
            or self.frozen_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("pilot baseline freeze time must be UTC")
        if info.context and info.context.get("skip_binding_digest") is True:
            return self
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("pilot baseline binding digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "PilotBaselineBindingV021":
        body = {
            "schema_version": "ecomsre.product.pilot-baseline-binding.v021",
            "terminal": "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS",
            **payload,
        }
        draft = cls.model_validate(
            {**body, "binding_sha256": "0" * 64},
            context={"skip_binding_digest": True},
        )
        normalized = draft.model_dump(mode="json", exclude={"binding_sha256"})
        return cls.model_validate(
            {
                **normalized,
                "binding_sha256": semantic_sha256_v22(normalized),
            }
        )


def load_pilot_baseline_binding_v021(path: Path) -> PilotBaselineBindingV021:
    return PilotBaselineBindingV021.model_validate_json(
        _read_regular_bytes_v021(Path(path))
    )


def write_pilot_baseline_binding_v021(
    path: Path,
    binding: PilotBaselineBindingV021,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("pilot baseline binding parent is not a regular directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(destination, flags, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(binding.model_dump_json(indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


class PilotBaselineReadinessProfileV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-readiness-profile.v021"] = (
        "ecomsre.product.baseline-readiness-profile.v021"
    )
    profile_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    candidate_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    build_policy: BaselineBuildPolicyV1
    stabilization_seconds: int = Field(ge=0, le=600)
    healthy_traffic_profile: HealthyTrafficProfileV021
    baseline_accumulation_seconds: int = Field(ge=180, le=900)
    connector_query_bindings: dict[str, str] = Field(min_length=1, max_length=20)
    maximum_changed_attempts: Literal[2] = 2
    public_root: Literal[".local/product-v021/baseline-readiness"] = (
        ".local/product-v021/baseline-readiness"
    )
    private_root: Literal[".local/product-v021/private-baseline-readiness"] = (
        ".local/product-v021/private-baseline-readiness"
    )
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_readiness_profile(self) -> "PilotBaselineReadinessProfileV021":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("baseline readiness services are not canonical")
        if self.candidate_services != ("checkout",):
            raise ValueError("baseline readiness default service differs")
        policy = self.build_policy
        if (
            policy.mode is not BaselineBuildModeV1.DEMO_ONLY
            or policy.lookback_seconds != 180
            or policy.window_count != 5
            or policy.minimum_successful_windows < 4
            or policy.warmup_seconds != 180
        ):
            raise ValueError("baseline readiness build policy differs")
        if tuple(self.connector_query_bindings) != tuple(
            sorted(self.connector_query_bindings)
        ):
            raise ValueError("baseline connector query bindings are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("baseline readiness profile digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "PilotBaselineReadinessProfileV021":
        body = {
            "schema_version": "ecomsre.product.baseline-readiness-profile.v021",
            **payload,
        }
        draft = cls.model_construct(**body, profile_sha256="0" * 64)
        body["profile_sha256"] = semantic_sha256_v22(
            draft.model_dump(mode="json", exclude={"profile_sha256"})
        )
        return cls.model_validate(body)


def render_public_readiness_markdown_v021(
    payload: Mapping[str, object],
) -> str:
    """Render the deterministic public readiness summary."""

    latest_raw = payload.get("latest_attempt")
    latest = latest_raw if isinstance(latest_raw, Mapping) else {}
    return f"""# Product v0.2.1 baseline readiness

Terminal: `{payload['terminal']}`

- changed readiness attempts: `{payload['readiness_attempt_count']}` / `2`
- total readiness runs: `{payload['readiness_run_count']}` / `3`
- accepted windows: `{latest.get('accepted_window_count', 0)}` / `5`
- baseline active: `{str(latest.get('baseline_active', False)).lower()}`
- queue default unchanged: `{str(latest.get('queue_default_unchanged', False)).lower()}`
- outer baseline restored: `{str(latest.get('outer_baseline_restored', False)).lower()}`
- owned cleanup: `{latest.get('owned_demo_cleanup', 'BLOCKED')}`
- Product action authority: `NONE`
- Agent writes: `0`
- Runbooks: `0`

The report contains normalized source/window audit data only. Evaluator-private
control identifiers, private paths, and mutation commands are excluded.
"""


__all__ = (
    "BoundedHealthyCheckoutTrafficV021",
    "HealthyTrafficRunResultV021",
    "HealthyTrafficProfileV021",
    "PilotBaselineBindingV021",
    "PilotBaselineReadinessProfileV021",
    "QueueDefaultObservationV021",
    "ReadinessAttemptDispositionV021",
    "ReadinessAttemptSignatureV021",
    "ReadinessChangeParameterV021",
    "ReadinessFailureDomainV021",
    "ReadinessSemanticInputsV021",
    "build_readiness_attempt_signature_v021",
    "load_pilot_baseline_binding_v021",
    "render_public_readiness_markdown_v021",
    "verify_queue_default_v021",
    "write_pilot_baseline_binding_v021",
)
