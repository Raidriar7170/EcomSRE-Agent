"""Source-bound checkout traffic and transaction-stage evidence for Product v0.2.3.2."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator
from pydantic_core import to_jsonable_python

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


TRAFFIC_CONTRACT_PASS_V0232 = "ECOMSRE_PRODUCT_V0232_TRAFFIC_CONTRACT_PASS"
UPSTREAM_COMMIT_V0232 = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PRODUCT_ID_V0232 = "0PUK6V6EV0"
_MAXIMUM_RESPONSE_BYTES_V0232 = 1_000_000
_MAXIMUM_JSON_DEPTH_V0232 = 64

_SOURCE_FILE_SHA256S_V0232 = {
    "src/frontend/gateways/Api.gateway.ts": (
        "70871abdd9f2aac61f6e0fd2cb4e20794d05899874c1e076c56da0b9898f37ec"
    ),
    "src/frontend/pages/api/cart.ts": (
        "baddb1546b9132f54928a87b789bdaf66170380492a0a69d8c479b35b9a273b5"
    ),
    "src/frontend/pages/api/checkout.ts": (
        "48d272678bdee3a1a0e707453598d1fd8f8edc1896a8f2872166241ea3143983"
    ),
    "src/frontend/protos/demo.ts": (
        "6d525d23e48a502b7f13079d7261beb29f0a30622b3f0b9ef916246a59da9212"
    ),
    "src/frontend/types/Cart.ts": (
        "e57bbcca175d932b08bb8aa978babbdfc493c6b231c2ec713c93f7ac2cf1130a"
    ),
    "src/load-generator/people.json": (
        "2e780c99cd1950213c798ba086c184b6ae89a98af8a6df4bb190b2e19bcf77ab"
    ),
    "src/load-generator/script.js": (
        "cacfe65d69e2c20f2e22ebef618369a4ddf731ce750e275444f9c86cf50ac755"
    ),
}

_CART_REQUEST_SCHEMA_V0232: dict[str, Any] = {
    "properties": {
        "item": {
            "properties": {
                "productId": {"type": "string"},
                "quantity": {"type": "integer"},
            },
            "required": ["productId", "quantity"],
            "type": "object",
        },
        "userId": {"type": "string"},
    },
    "required": ["userId", "item"],
    "type": "object",
}

_CHECKOUT_REQUEST_SCHEMA_V0232: dict[str, Any] = {
    "properties": {
        "address": {
            "properties": {
                field: {"type": "string"}
                for field in (
                    "streetAddress",
                    "city",
                    "state",
                    "country",
                    "zipCode",
                )
            },
            "required": [
                "streetAddress",
                "city",
                "state",
                "country",
                "zipCode",
            ],
            "type": "object",
        },
        "creditCard": {
            "properties": {
                "creditCardCvv": {"type": "integer"},
                "creditCardExpirationMonth": {"type": "integer"},
                "creditCardExpirationYear": {"type": "integer"},
                "creditCardNumber": {"type": "string"},
            },
            "required": [
                "creditCardNumber",
                "creditCardCvv",
                "creditCardExpirationYear",
                "creditCardExpirationMonth",
            ],
            "type": "object",
        },
        "email": {"type": "string"},
        "userCurrency": {"type": "string"},
        "userId": {"type": "string"},
    },
    "required": ["userId", "userCurrency", "address", "email", "creditCard"],
    "type": "object",
}


class TrafficContractErrorV0232(RuntimeError):
    """The pinned route source or transaction contract cannot be admitted."""


class SourceFileBindingV0232(ProductModelV1):
    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_safe_path(self) -> "SourceFileBindingV0232":
        candidate = PurePosixPath(self.path)
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in self.path:
            raise ValueError("traffic source binding path is unsafe")
        return self


class CheckoutTrafficContractV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.checkout-traffic-contract.v0232"] = (
        "ecomsre.product.checkout-traffic-contract.v0232"
    )
    contract_id: Literal["CHECKOUT_CART_THEN_ORDER_V0232"] = (
        "CHECKOUT_CART_THEN_ORDER_V0232"
    )
    upstream_commit: str
    source_file_bindings: tuple[SourceFileBindingV0232, ...]
    cart_method: Literal["POST"] = "POST"
    cart_path: Literal["/api/cart"] = "/api/cart"
    cart_request_schema: dict[str, Any]
    cart_success_statuses: tuple[int, ...]
    cart_success_validator: Literal["MATCHED_USER_AND_REQUESTED_ITEM_V0232"] = (
        "MATCHED_USER_AND_REQUESTED_ITEM_V0232"
    )
    checkout_method: Literal["POST"] = "POST"
    checkout_path: Literal["/api/checkout"] = "/api/checkout"
    checkout_request_schema: dict[str, Any]
    checkout_success_statuses: tuple[int, ...]
    checkout_success_validator: Literal["ORDER_ID_AND_REQUESTED_ITEM_V0232"] = (
        "ORDER_ID_AND_REQUESTED_ITEM_V0232"
    )
    cart_before_checkout: Literal[True] = True
    synthetic_identity_policy: Literal["SHA256_DERIVED_PUBLIC_IDENTITY_V0232"] = (
        "SHA256_DERIVED_PUBLIC_IDENTITY_V0232"
    )
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_source_bound_contract(self) -> "CheckoutTrafficContractV0232":
        expected_bindings = tuple(
            SourceFileBindingV0232(path=path, sha256=sha256)
            for path, sha256 in sorted(_SOURCE_FILE_SHA256S_V0232.items())
        )
        if (
            self.upstream_commit != UPSTREAM_COMMIT_V0232
            or self.source_file_bindings != expected_bindings
            or self.cart_request_schema != _CART_REQUEST_SCHEMA_V0232
            or self.checkout_request_schema != _CHECKOUT_REQUEST_SCHEMA_V0232
            or self.cart_success_statuses != (200,)
            or self.checkout_success_statuses != (200,)
        ):
            raise ValueError("Product v0.2.3.2 checkout traffic contract differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"contract_sha256"})
        )
        if self.contract_sha256 != expected:
            raise ValueError("Product v0.2.3.2 traffic contract digest differs")
        return self

    @classmethod
    def build(cls) -> "CheckoutTrafficContractV0232":
        body: dict[str, Any] = {
            "schema_version": "ecomsre.product.checkout-traffic-contract.v0232",
            "contract_id": "CHECKOUT_CART_THEN_ORDER_V0232",
            "upstream_commit": UPSTREAM_COMMIT_V0232,
            "source_file_bindings": [
                {"path": path, "sha256": sha256}
                for path, sha256 in sorted(_SOURCE_FILE_SHA256S_V0232.items())
            ],
            "cart_method": "POST",
            "cart_path": "/api/cart",
            "cart_request_schema": _CART_REQUEST_SCHEMA_V0232,
            "cart_success_statuses": [200],
            "cart_success_validator": "MATCHED_USER_AND_REQUESTED_ITEM_V0232",
            "checkout_method": "POST",
            "checkout_path": "/api/checkout",
            "checkout_request_schema": _CHECKOUT_REQUEST_SCHEMA_V0232,
            "checkout_success_statuses": [200],
            "checkout_success_validator": "ORDER_ID_AND_REQUESTED_ITEM_V0232",
            "cart_before_checkout": True,
            "synthetic_identity_policy": "SHA256_DERIVED_PUBLIC_IDENTITY_V0232",
        }
        return cls.model_validate(
            {**body, "contract_sha256": semantic_sha256_v22(body)}
        )


class CheckoutTrafficStageV0232(str, Enum):
    CART_REQUEST_BUILD = "CART_REQUEST_BUILD"
    CART_TRANSPORT = "CART_TRANSPORT"
    CART_HTTP = "CART_HTTP"
    CART_RESPONSE = "CART_RESPONSE"
    CHECKOUT_REQUEST_BUILD = "CHECKOUT_REQUEST_BUILD"
    CHECKOUT_TRANSPORT = "CHECKOUT_TRANSPORT"
    CHECKOUT_HTTP = "CHECKOUT_HTTP"
    CHECKOUT_RESPONSE = "CHECKOUT_RESPONSE"
    BUSINESS_SUCCESS = "BUSINESS_SUCCESS"
    COMPLETE = "COMPLETE"


class TrafficSafeErrorCodeV0232(str, Enum):
    CART_REQUEST_SCHEMA_INVALID = "CART_REQUEST_SCHEMA_INVALID"
    CART_TRANSPORT_ERROR = "CART_TRANSPORT_ERROR"
    CART_HTTP_NON_SUCCESS = "CART_HTTP_NON_SUCCESS"
    CART_RESPONSE_SCHEMA_INVALID = "CART_RESPONSE_SCHEMA_INVALID"
    CART_BUSINESS_SUCCESS_MISSING = "CART_BUSINESS_SUCCESS_MISSING"
    CHECKOUT_REQUEST_SCHEMA_INVALID = "CHECKOUT_REQUEST_SCHEMA_INVALID"
    CHECKOUT_TRANSPORT_ERROR = "CHECKOUT_TRANSPORT_ERROR"
    CHECKOUT_HTTP_NON_SUCCESS = "CHECKOUT_HTTP_NON_SUCCESS"
    CHECKOUT_RESPONSE_SCHEMA_INVALID = "CHECKOUT_RESPONSE_SCHEMA_INVALID"
    CHECKOUT_BUSINESS_SUCCESS_MISSING = "CHECKOUT_BUSINESS_SUCCESS_MISSING"
    TRAFFIC_CONTRACT_SOURCE_DRIFT = "TRAFFIC_CONTRACT_SOURCE_DRIFT"
    TRAFFIC_TRANSACTION_TIMEOUT = "TRAFFIC_TRANSACTION_TIMEOUT"


class CheckoutTransactionObservationV0232(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.checkout-transaction-observation.v0232"
    ] = "ecomsre.product.checkout-transaction-observation.v0232"
    ordinal: int = Field(ge=1, le=30)
    synthetic_user_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    cart_request_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    cart_status: int | None = Field(default=None, ge=100, le=599)
    cart_response_content_type: str | None = Field(default=None, max_length=200)
    cart_response_shape_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    cart_response_shape_summary: str | None = Field(default=None, max_length=200)
    checkout_request_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    checkout_status: int | None = Field(default=None, ge=100, le=599)
    checkout_response_content_type: str | None = Field(default=None, max_length=200)
    checkout_response_shape_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    checkout_response_shape_summary: str | None = Field(default=None, max_length=200)
    business_success: bool
    failure_stage: CheckoutTrafficStageV0232 | None = None
    safe_error_code: TrafficSafeErrorCodeV0232 | None = None
    cart_latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    checkout_latency_ms: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    transaction_started_at: datetime
    transaction_ended_at: datetime
    observation_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_one_exact_disposition(self) -> "CheckoutTransactionObservationV0232":
        if (
            self.transaction_started_at.tzinfo is None
            or self.transaction_ended_at.tzinfo is None
            or self.transaction_ended_at < self.transaction_started_at
            or (self.cart_status is None)
            != (self.cart_response_content_type is None)
            or (self.checkout_status is None)
            != (self.checkout_response_content_type is None)
            or (self.cart_response_shape_sha256 is None)
            != (self.cart_response_shape_summary is None)
            or (self.checkout_response_shape_sha256 is None)
            != (self.checkout_response_shape_summary is None)
        ):
            raise ValueError("traffic transaction timestamps differ")
        success = (
            self.business_success
            and self.failure_stage is None
            and self.safe_error_code is None
            and self.cart_status == 200
            and self.checkout_status == 200
            and self.cart_response_content_type == "application/json"
            and self.checkout_response_content_type == "application/json"
            and self.cart_response_shape_sha256 is not None
            and self.checkout_response_shape_sha256 is not None
        )
        failure = (
            not self.business_success
            and self.failure_stage is not None
            and self.failure_stage != CheckoutTrafficStageV0232.COMPLETE
            and self.safe_error_code is not None
        )
        if success == failure:
            raise ValueError("traffic transaction disposition is not exact")
        if failure:
            assert self.failure_stage is not None
            assert self.safe_error_code is not None
            allowed_stages = {
                TrafficSafeErrorCodeV0232.CART_REQUEST_SCHEMA_INVALID: {
                    CheckoutTrafficStageV0232.CART_REQUEST_BUILD
                },
                TrafficSafeErrorCodeV0232.CART_TRANSPORT_ERROR: {
                    CheckoutTrafficStageV0232.CART_TRANSPORT
                },
                TrafficSafeErrorCodeV0232.CART_HTTP_NON_SUCCESS: {
                    CheckoutTrafficStageV0232.CART_HTTP
                },
                TrafficSafeErrorCodeV0232.CART_RESPONSE_SCHEMA_INVALID: {
                    CheckoutTrafficStageV0232.CART_RESPONSE
                },
                TrafficSafeErrorCodeV0232.CART_BUSINESS_SUCCESS_MISSING: {
                    CheckoutTrafficStageV0232.BUSINESS_SUCCESS
                },
                TrafficSafeErrorCodeV0232.CHECKOUT_REQUEST_SCHEMA_INVALID: {
                    CheckoutTrafficStageV0232.CHECKOUT_REQUEST_BUILD
                },
                TrafficSafeErrorCodeV0232.CHECKOUT_TRANSPORT_ERROR: {
                    CheckoutTrafficStageV0232.CHECKOUT_TRANSPORT
                },
                TrafficSafeErrorCodeV0232.CHECKOUT_HTTP_NON_SUCCESS: {
                    CheckoutTrafficStageV0232.CHECKOUT_HTTP
                },
                TrafficSafeErrorCodeV0232.CHECKOUT_RESPONSE_SCHEMA_INVALID: {
                    CheckoutTrafficStageV0232.CHECKOUT_RESPONSE
                },
                TrafficSafeErrorCodeV0232.CHECKOUT_BUSINESS_SUCCESS_MISSING: {
                    CheckoutTrafficStageV0232.BUSINESS_SUCCESS
                },
                TrafficSafeErrorCodeV0232.TRAFFIC_CONTRACT_SOURCE_DRIFT: {
                    CheckoutTrafficStageV0232.CART_REQUEST_BUILD
                },
                TrafficSafeErrorCodeV0232.TRAFFIC_TRANSACTION_TIMEOUT: {
                    CheckoutTrafficStageV0232.CART_TRANSPORT,
                    CheckoutTrafficStageV0232.CHECKOUT_TRANSPORT,
                },
            }
            if self.failure_stage not in allowed_stages[self.safe_error_code]:
                raise ValueError("traffic transaction stage/error disposition differs")
            cart_unattempted = self.failure_stage in {
                CheckoutTrafficStageV0232.CART_REQUEST_BUILD,
                CheckoutTrafficStageV0232.CART_TRANSPORT,
            }
            checkout_unattempted = self.failure_stage in {
                CheckoutTrafficStageV0232.CART_REQUEST_BUILD,
                CheckoutTrafficStageV0232.CART_TRANSPORT,
                CheckoutTrafficStageV0232.CART_HTTP,
                CheckoutTrafficStageV0232.CART_RESPONSE,
                CheckoutTrafficStageV0232.CHECKOUT_REQUEST_BUILD,
                CheckoutTrafficStageV0232.CHECKOUT_TRANSPORT,
            } or self.safe_error_code == (
                TrafficSafeErrorCodeV0232.CART_BUSINESS_SUCCESS_MISSING
            )
            if (
                (cart_unattempted and self.cart_status is not None)
                or (
                    not cart_unattempted
                    and self.failure_stage != CheckoutTrafficStageV0232.CART_HTTP
                    and self.cart_status != 200
                )
                or (
                    self.failure_stage == CheckoutTrafficStageV0232.CART_HTTP
                    and (self.cart_status is None or self.cart_status == 200)
                )
                or (checkout_unattempted and self.checkout_status is not None)
                or (
                    not checkout_unattempted
                    and self.failure_stage != CheckoutTrafficStageV0232.CHECKOUT_HTTP
                    and self.checkout_status != 200
                )
                or (
                    self.failure_stage == CheckoutTrafficStageV0232.CHECKOUT_HTTP
                    and (self.checkout_status is None or self.checkout_status == 200)
                )
            ):
                raise ValueError("traffic transaction stage/status disposition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"observation_sha256"})
        )
        if self.observation_sha256 != expected:
            raise ValueError("traffic transaction observation digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "CheckoutTransactionObservationV0232":
        body = {
            "schema_version": (
                "ecomsre.product.checkout-transaction-observation.v0232"
            ),
            **payload,
        }
        return cls.model_validate(
            {
                **body,
                "observation_sha256": semantic_sha256_v22(
                    to_jsonable_python(body)
                ),
            }
        )


class HealthyTrafficProfileV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.healthy-traffic-profile.v0232"] = (
        "ecomsre.product.healthy-traffic-profile.v0232"
    )
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    transactions: int = Field(ge=1, le=30)
    requests_per_second: float = Field(gt=0, le=1, allow_inf_nan=False)
    request_seed: int = Field(ge=0)
    maximum_failures: int = Field(ge=0, le=30)
    stabilization_seconds: int = Field(ge=0, le=60)
    minimum_full_episode_duration_seconds: int = Field(ge=0, le=900)
    queue_fault_flag: Literal[0] = 0
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_profile_seal(self) -> "HealthyTrafficProfileV0232":
        if self.maximum_failures > self.transactions:
            raise ValueError("healthy traffic maximum failures differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("healthy traffic profile digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "HealthyTrafficProfileV0232":
        body = {
            "schema_version": "ecomsre.product.healthy-traffic-profile.v0232",
            **payload,
        }
        return cls.model_validate(
            {**body, "profile_sha256": semantic_sha256_v22(body)}
        )


class HealthyTrafficRunV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.healthy-traffic-run.v0232"] = (
        "ecomsre.product.healthy-traffic-run.v0232"
    )
    role: Literal["PREFLIGHT", "FORMAL"]
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    planned_transactions: int = Field(ge=1, le=30)
    completed_transactions: int = Field(ge=0, le=30)
    successful_transactions: int = Field(ge=0, le=30)
    failed_transactions: int = Field(ge=0, le=30)
    stage_failure_counts: dict[str, int]
    transport_retry_count: Literal[0] = 0
    started_at: datetime
    ended_at: datetime
    passed: bool
    transaction_observation_sha256s: tuple[str, ...]
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_run_result(self) -> "HealthyTrafficRunV0232":
        if (
            self.started_at.tzinfo is None
            or self.ended_at.tzinfo is None
            or self.ended_at < self.started_at
            or self.completed_transactions
            != self.successful_transactions + self.failed_transactions
            or self.completed_transactions
            != len(self.transaction_observation_sha256s)
            or sum(self.stage_failure_counts.values()) != self.failed_transactions
            or any(count < 1 for count in self.stage_failure_counts.values())
        ):
            raise ValueError("healthy traffic run counts differ")
        expected_pass = (
            self.completed_transactions == self.planned_transactions
            and self.successful_transactions == self.planned_transactions
            and self.failed_transactions == 0
            and not self.stage_failure_counts
            and self.transport_retry_count == 0
        )
        if self.passed != expected_pass:
            raise ValueError("healthy traffic pass disposition differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("healthy traffic run digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "HealthyTrafficRunV0232":
        body = {
            "schema_version": "ecomsre.product.healthy-traffic-run.v0232",
            **payload,
        }
        return cls.model_validate(
            {
                **body,
                "result_sha256": semantic_sha256_v22(to_jsonable_python(body)),
            }
        )


class HealthyTrafficExecutionV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.healthy-traffic-execution.v0232"] = (
        "ecomsre.product.healthy-traffic-execution.v0232"
    )
    run: HealthyTrafficRunV0232
    observations: tuple[CheckoutTransactionObservationV0232, ...]
    execution_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_bound_observations(self) -> "HealthyTrafficExecutionV0232":
        observed = tuple(item.observation_sha256 for item in self.observations)
        if observed != self.run.transaction_observation_sha256s:
            raise ValueError("healthy traffic observations differ")
        if any(
            item.contract_sha256 != self.run.contract_sha256
            for item in self.observations
        ):
            raise ValueError("healthy traffic observation contract differs")
        ordinals = tuple(item.ordinal for item in self.observations)
        identities = tuple(item.synthetic_user_sha256 for item in self.observations)
        if (
            ordinals != tuple(range(1, len(self.observations) + 1))
            or len(set(observed)) != len(observed)
            or len(set(identities)) != len(identities)
        ):
            raise ValueError("healthy traffic transaction identity differs")
        if any(
            item.transaction_started_at < self.run.started_at
            or item.transaction_ended_at > self.run.ended_at
            for item in self.observations
        ):
            raise ValueError("healthy traffic observation time window differs")
        successful = sum(item.business_success for item in self.observations)
        failed = len(self.observations) - successful
        stage_failures = dict(
            sorted(
                Counter(
                    item.failure_stage.value
                    for item in self.observations
                    if item.failure_stage is not None
                ).items()
            )
        )
        passed = (
            len(self.observations) == self.run.planned_transactions
            and successful == self.run.planned_transactions
            and failed == 0
        )
        if (
            self.run.completed_transactions != len(self.observations)
            or self.run.successful_transactions != successful
            or self.run.failed_transactions != failed
            or self.run.stage_failure_counts != stage_failures
            or self.run.passed != passed
        ):
            raise ValueError("healthy traffic observation summary differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"execution_sha256"})
        )
        if self.execution_sha256 != expected:
            raise ValueError("healthy traffic execution digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        run: HealthyTrafficRunV0232,
        observations: tuple[CheckoutTransactionObservationV0232, ...],
    ) -> "HealthyTrafficExecutionV0232":
        body = {
            "schema_version": "ecomsre.product.healthy-traffic-execution.v0232",
            "run": run.model_dump(mode="json"),
            "observations": [item.model_dump(mode="json") for item in observations],
        }
        return cls.model_validate(
            {**body, "execution_sha256": semantic_sha256_v22(body)}
        )


class HealthyTrafficPreflightV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.healthy-traffic-preflight.v0232"] = (
        "ecomsre.product.healthy-traffic-preflight.v0232"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS"] = (
        "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS"
    )
    execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    transaction_count: Literal[10] = 10
    preflight_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_preflight_seal(self) -> "HealthyTrafficPreflightV0232":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"preflight_sha256"})
        )
        if self.preflight_sha256 != expected:
            raise ValueError("healthy traffic preflight digest differs")
        return self

    @classmethod
    def build(
        cls, execution: HealthyTrafficExecutionV0232
    ) -> "HealthyTrafficPreflightV0232":
        run = execution.run
        if run.role != "PREFLIGHT" or not run.passed or run.planned_transactions != 10:
            raise ValueError("healthy traffic preflight does not pass 10 / 10")
        body = {
            "schema_version": "ecomsre.product.healthy-traffic-preflight.v0232",
            "terminal": "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS",
            "execution_sha256": execution.execution_sha256,
            "profile_sha256": run.profile_sha256,
            "contract_sha256": run.contract_sha256,
            "transaction_count": 10,
        }
        return cls.model_validate(
            {**body, "preflight_sha256": semantic_sha256_v22(body)}
        )


class IncidentTrafficBindingV0232(ProductModelV1):
    schema_version: Literal["ecomsre.product.incident-traffic-binding.v0232"] = (
        "ecomsre.product.incident-traffic-binding.v0232"
    )
    incident_id: str = Field(pattern=r"^inc-[a-zA-Z0-9-]{1,120}$")
    traffic_execution_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    episode_started_at: datetime
    episode_ended_at: datetime
    traffic_started_at: datetime
    traffic_ended_at: datetime
    planned_transactions: Literal[30] = 30
    successful_transactions: Literal[30] = 30
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_binding_seal(self) -> "IncidentTrafficBindingV0232":
        timestamps = (
            self.episode_started_at,
            self.episode_ended_at,
            self.traffic_started_at,
            self.traffic_ended_at,
        )
        if any(value.tzinfo is None for value in timestamps):
            raise ValueError("Incident traffic binding timestamps differ")
        if not (
            self.episode_started_at
            <= self.traffic_started_at
            <= self.traffic_ended_at
            <= self.episode_ended_at
        ):
            raise ValueError("Incident episode window does not contain traffic")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("Incident traffic binding digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        incident_id: str,
        execution: HealthyTrafficExecutionV0232,
        episode_started_at: datetime,
        episode_ended_at: datetime,
    ) -> "IncidentTrafficBindingV0232":
        run = execution.run
        if (
            run.role != "FORMAL"
            or not run.passed
            or run.planned_transactions != 30
            or run.successful_transactions != 30
        ):
            raise ValueError("Incident traffic binding requires formal 30 / 30")
        body = {
            "schema_version": "ecomsre.product.incident-traffic-binding.v0232",
            "incident_id": incident_id,
            "traffic_execution_sha256": execution.execution_sha256,
            "contract_sha256": run.contract_sha256,
            "formal_profile_sha256": run.profile_sha256,
            "episode_started_at": episode_started_at,
            "episode_ended_at": episode_ended_at,
            "traffic_started_at": run.started_at,
            "traffic_ended_at": run.ended_at,
            "planned_transactions": 30,
            "successful_transactions": 30,
        }
        return cls.model_validate(
            {
                **body,
                "binding_sha256": semantic_sha256_v22(to_jsonable_python(body)),
            }
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_bound_file(root: Path, relative: str) -> Path:
    path = root / relative
    current = root
    if root.is_symlink() or not root.is_dir():
        raise TrafficContractErrorV0232("traffic contract source drift")
    for component in PurePosixPath(relative).parts:
        current /= component
        if current.is_symlink():
            raise TrafficContractErrorV0232("traffic contract source drift")
    if not path.is_file():
        raise TrafficContractErrorV0232("traffic contract source drift")
    return path


def _read_git_head_commit(root: Path) -> str:
    dot_git = root / ".git"
    if dot_git.is_file():
        pointer = dot_git.read_text(encoding="utf-8").strip()
        if not pointer.startswith("gitdir: "):
            raise ValueError("traffic contract Git metadata differs")
        git_dir = (root / pointer.removeprefix("gitdir: ")).resolve(strict=True)
    elif dot_git.is_dir():
        git_dir = dot_git.resolve(strict=True)
    else:
        raise ValueError("traffic contract Git metadata differs")
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        reference = PurePosixPath(head.removeprefix("ref: "))
        if reference.is_absolute() or ".." in reference.parts:
            raise ValueError("traffic contract Git metadata differs")
        loose_reference = git_dir.joinpath(*reference.parts)
        if loose_reference.is_file():
            head = loose_reference.read_text(encoding="utf-8").strip()
        else:
            packed = git_dir / "packed-refs"
            matches = (
                line.split(" ", 1)[0]
                for line in packed.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith(("#", "^"))
                and line.endswith(f" {reference.as_posix()}")
            )
            head = next(matches, "")
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise ValueError("traffic contract Git metadata differs")
    return head


def load_checkout_traffic_contract_v0232(
    project_root: Path,
    *,
    upstream_root: Path | None = None,
    observed_upstream_commit: str | None = None,
) -> CheckoutTrafficContractV0232:
    root = Path(project_root).resolve(strict=True)
    source = (
        (root / "third_party/opentelemetry-demo")
        if upstream_root is None
        else Path(upstream_root)
    )
    try:
        source = source.absolute()
        commit = observed_upstream_commit
        if commit is None:
            commit = _read_git_head_commit(source)
        if commit != UPSTREAM_COMMIT_V0232:
            raise TrafficContractErrorV0232("traffic contract source drift")
        for relative, expected_sha256 in _SOURCE_FILE_SHA256S_V0232.items():
            path = _require_regular_bound_file(source, relative)
            if _sha256_file(path) != expected_sha256:
                raise TrafficContractErrorV0232("traffic contract source drift")
    except (OSError, ValueError) as error:
        raise TrafficContractErrorV0232("traffic contract source drift") from error
    return CheckoutTrafficContractV0232.build()


def _is_string(value: object) -> bool:
    return isinstance(value, str)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_cart_request(payload: object) -> bool:
    if not isinstance(payload, dict) or not {"userId", "item"}.issubset(payload):
        return False
    item = payload.get("item")
    return (
        _is_string(payload.get("userId"))
        and isinstance(item, dict)
        and {"productId", "quantity"}.issubset(item)
        and _is_string(item.get("productId"))
        and _is_integer(item.get("quantity"))
    )


def _valid_checkout_request(payload: object) -> bool:
    if not isinstance(payload, dict) or not {
        "userId",
        "userCurrency",
        "address",
        "email",
        "creditCard",
    }.issubset(payload):
        return False
    address = payload.get("address")
    card = payload.get("creditCard")
    address_fields = {"streetAddress", "city", "state", "country", "zipCode"}
    card_fields = {
        "creditCardNumber",
        "creditCardCvv",
        "creditCardExpirationYear",
        "creditCardExpirationMonth",
    }
    return (
        _is_string(payload.get("userId"))
        and _is_string(payload.get("userCurrency"))
        and _is_string(payload.get("email"))
        and isinstance(address, dict)
        and address_fields.issubset(address)
        and all(_is_string(address.get(field)) for field in address_fields)
        and isinstance(card, dict)
        and card_fields.issubset(card)
        and _is_string(card.get("creditCardNumber"))
        and _is_integer(card.get("creditCardCvv"))
        and _is_integer(card.get("creditCardExpirationYear"))
        and _is_integer(card.get("creditCardExpirationMonth"))
    )


def _valid_money(value: object) -> bool:
    return (
        isinstance(value, dict)
        and _is_string(value.get("currencyCode"))
        and _is_integer(value.get("units"))
        and _is_integer(value.get("nanos"))
    )


def _valid_address(value: object) -> bool:
    fields = {"streetAddress", "city", "state", "country", "zipCode"}
    return (
        isinstance(value, dict)
        and fields.issubset(value)
        and all(_is_string(value.get(field)) for field in fields)
    )


def _valid_cart_response(value: object) -> bool:
    if (
        not isinstance(value, dict)
        or not _is_string(value.get("userId"))
        or not isinstance(value.get("items"), list)
    ):
        return False
    return all(
        isinstance(item, dict)
        and _is_string(item.get("productId"))
        and _is_integer(item.get("quantity"))
        for item in value["items"]
    )


def _valid_checkout_response(value: object) -> bool:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("orderId"), str)
        or not isinstance(value.get("shippingTrackingId"), str)
        or not _valid_money(value.get("shippingCost"))
        or not _valid_address(value.get("shippingAddress"))
        or not isinstance(value.get("items"), list)
    ):
        return False
    for entry in value["items"]:
        if not isinstance(entry, dict) or not _valid_money(entry.get("cost")):
            return False
        item = entry.get("item")
        if (
            not isinstance(item, dict)
            or not _is_string(item.get("productId"))
            or not _is_integer(item.get("quantity"))
            or not isinstance(item.get("product"), dict)
        ):
            return False
    return True


def _response_shape(value: object) -> object:
    if isinstance(value, dict):
        return {
            "type": "object",
            "fields": {
                str(key): _response_shape(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            },
        }
    if isinstance(value, list):
        shapes = {
            json.dumps(_response_shape(item), sort_keys=True, separators=(",", ":"))
            for item in value
        }
        return {"type": "array", "item_shapes": sorted(shapes)}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    return {"type": "unsupported"}


def _response_shape_sha256(value: object) -> str:
    return semantic_sha256_v22(_response_shape(value))


def _response_shape_summary(value: object) -> str:
    """Return a bounded, value-free top-level response-shape summary."""

    def kind(item: object) -> str:
        if isinstance(item, dict):
            return "object"
        if isinstance(item, list):
            return "array"
        if item is None:
            return "null"
        if isinstance(item, bool):
            return "boolean"
        if isinstance(item, int):
            return "integer"
        if isinstance(item, float):
            return "number"
        if isinstance(item, str):
            return "string"
        return "unsupported"

    if isinstance(value, dict):
        counts = Counter(kind(item) for item in value.values())
        detail = ",".join(f"{name}={counts[name]}" for name in sorted(counts))
        return f"object:fields={len(value)}:types={detail}"[:200]
    if isinstance(value, list):
        counts = Counter(kind(item) for item in value)
        detail = ",".join(f"{name}={counts[name]}" for name in sorted(counts))
        return f"array:items={len(value)}:types={detail}"[:200]
    return kind(value)


def _response_content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _json_within_depth(value: object) -> bool:
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAXIMUM_JSON_DEPTH_V0232:
            return False
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return True


def _json_response(response: httpx.Response) -> Mapping[str, object] | None:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if (
        content_type != "application/json"
        or len(response.content) > _MAXIMUM_RESPONSE_BYTES_V0232
        or response.extensions.get("ecomsre_body_truncated") is True
    ):
        return None

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def reject_nonfinite(_value: str) -> object:
        raise ValueError("non-finite JSON value")

    try:
        value = json.loads(
            response.content.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and _json_within_depth(value) else None


class HealthyTrafficRunnerV0232:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("custom healthy traffic transport is not admitted")
        bound_transport = (
            httpx.HTTPTransport(retries=0) if transport is None else transport
        )
        self.client = httpx.Client(
            transport=bound_transport,
            trust_env=False,
            follow_redirects=False,
        )
        self.sleep = sleep
        self.clock = clock
        self.monotonic = monotonic

    def __enter__(self) -> "HealthyTrafficRunnerV0232":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.client.close()

    def _bounded_post(
        self,
        url: str,
        *,
        payload: Mapping[str, object],
        timeout: float,
    ) -> httpx.Response:
        with self.client.stream(
            "POST",
            url,
            json=dict(payload),
            timeout=timeout,
        ) as response:
            content = bytearray()
            truncated = False
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > _MAXIMUM_RESPONSE_BYTES_V0232:
                    truncated = True
                    break
                content.extend(chunk)
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=bytes(content),
                request=response.request,
                extensions={"ecomsre_body_truncated": truncated},
            )

    @staticmethod
    def _payloads(
        seed: int, ordinal: int
    ) -> tuple[dict[str, object], dict[str, object]]:
        identity = hashlib.sha256(
            f"product-v0232:{seed}:{ordinal}".encode()
        ).hexdigest()
        user_id = f"v0232-{identity[:24]}"
        cart: dict[str, object] = {
            "userId": user_id,
            "item": {"productId": _PRODUCT_ID_V0232, "quantity": 1},
        }
        checkout: dict[str, object] = {
            "userId": user_id,
            "userCurrency": "USD",
            "email": f"{user_id}@example.invalid",
            "address": {
                "streetAddress": " ".join(("1", "Contract", "Way")),
                "city": "Local",
                "state": "CA",
                "country": "United States",
                "zipCode": "94016",
            },
            "creditCard": {
                "creditCardNumber": "".join(("4111", "1111", "1111", "1111")),
                "creditCardCvv": 123,
                "creditCardExpirationYear": 2030,
                "creditCardExpirationMonth": 12,
            },
        }
        return cart, checkout

    def _observation(
        self,
        *,
        ordinal: int,
        user_id: str,
        contract: CheckoutTrafficContractV0232,
        started_at: datetime,
        cart_status: int | None = None,
        cart_content_type: str | None = None,
        cart_shape: str | None = None,
        cart_shape_summary: str | None = None,
        checkout_status: int | None = None,
        checkout_content_type: str | None = None,
        checkout_shape: str | None = None,
        checkout_shape_summary: str | None = None,
        business_success: bool,
        failure_stage: CheckoutTrafficStageV0232 | None,
        safe_error_code: TrafficSafeErrorCodeV0232 | None,
        cart_latency_ms: float | None = None,
        checkout_latency_ms: float | None = None,
    ) -> CheckoutTransactionObservationV0232:
        return CheckoutTransactionObservationV0232.build(
            ordinal=ordinal,
            synthetic_user_sha256=hashlib.sha256(user_id.encode()).hexdigest(),
            contract_sha256=contract.contract_sha256,
            cart_request_schema_sha256=semantic_sha256_v22(
                contract.cart_request_schema
            ),
            cart_status=cart_status,
            cart_response_content_type=cart_content_type,
            cart_response_shape_sha256=cart_shape,
            cart_response_shape_summary=cart_shape_summary,
            checkout_request_schema_sha256=semantic_sha256_v22(
                contract.checkout_request_schema
            ),
            checkout_status=checkout_status,
            checkout_response_content_type=checkout_content_type,
            checkout_response_shape_sha256=checkout_shape,
            checkout_response_shape_summary=checkout_shape_summary,
            business_success=business_success,
            failure_stage=failure_stage,
            safe_error_code=safe_error_code,
            cart_latency_ms=cart_latency_ms,
            checkout_latency_ms=checkout_latency_ms,
            transaction_started_at=started_at,
            transaction_ended_at=self.clock(),
        )

    def observe_transaction(
        self,
        *,
        endpoint: str,
        ordinal: int,
        contract: CheckoutTrafficContractV0232,
        cart_payload: Mapping[str, object],
        checkout_payload: Mapping[str, object],
    ) -> CheckoutTransactionObservationV0232:
        started_at = self.clock()
        user_id = cart_payload.get("userId")
        safe_user = str(user_id) if isinstance(user_id, str) else "invalid"
        if not _valid_cart_request(cart_payload):
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CART_REQUEST_BUILD,
                safe_error_code=TrafficSafeErrorCodeV0232.CART_REQUEST_SCHEMA_INVALID,
            )
        cart_started = self.monotonic()
        try:
            cart_response = self._bounded_post(
                endpoint.removesuffix(contract.checkout_path) + contract.cart_path,
                payload=cart_payload,
                timeout=10.0,
            )
        except httpx.TimeoutException:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CART_TRANSPORT,
                safe_error_code=TrafficSafeErrorCodeV0232.TRAFFIC_TRANSACTION_TIMEOUT,
                cart_latency_ms=max(0.0, (self.monotonic() - cart_started) * 1000),
            )
        except httpx.RequestError:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CART_TRANSPORT,
                safe_error_code=TrafficSafeErrorCodeV0232.CART_TRANSPORT_ERROR,
                cart_latency_ms=max(0.0, (self.monotonic() - cart_started) * 1000),
            )
        cart_latency = max(0.0, (self.monotonic() - cart_started) * 1000)
        cart_json = _json_response(cart_response)
        cart_content_type = _response_content_type(cart_response)
        cart_shape = (
            None if cart_json is None else _response_shape_sha256(cart_json)
        )
        cart_shape_summary = (
            None if cart_json is None else _response_shape_summary(cart_json)
        )
        if cart_response.status_code not in contract.cart_success_statuses:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CART_HTTP,
                safe_error_code=TrafficSafeErrorCodeV0232.CART_HTTP_NON_SUCCESS,
                cart_latency_ms=cart_latency,
            )
        if not _valid_cart_response(cart_json):
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CART_RESPONSE,
                safe_error_code=(
                    TrafficSafeErrorCodeV0232.CART_RESPONSE_SCHEMA_INVALID
                ),
                cart_latency_ms=cart_latency,
            )
        assert cart_json is not None
        item = cart_payload["item"]
        assert isinstance(item, Mapping)
        cart_items = cart_json["items"]
        assert isinstance(cart_items, list)
        cart_business_success = cart_json.get("userId") == safe_user and any(
            isinstance(observed, dict)
            and observed.get("productId") == item.get("productId")
            and observed.get("quantity") == item.get("quantity")
            for observed in cart_items
        )
        if not cart_business_success:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.BUSINESS_SUCCESS,
                safe_error_code=(
                    TrafficSafeErrorCodeV0232.CART_BUSINESS_SUCCESS_MISSING
                ),
                cart_latency_ms=cart_latency,
            )
        if (
            not _valid_checkout_request(checkout_payload)
            or checkout_payload.get("userId") != safe_user
        ):
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CHECKOUT_REQUEST_BUILD,
                safe_error_code=(
                    TrafficSafeErrorCodeV0232.CHECKOUT_REQUEST_SCHEMA_INVALID
                ),
                cart_latency_ms=cart_latency,
            )
        checkout_started = self.monotonic()
        try:
            checkout_response = self._bounded_post(
                endpoint,
                payload=checkout_payload,
                timeout=20.0,
            )
        except httpx.TimeoutException:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CHECKOUT_TRANSPORT,
                safe_error_code=TrafficSafeErrorCodeV0232.TRAFFIC_TRANSACTION_TIMEOUT,
                cart_latency_ms=cart_latency,
                checkout_latency_ms=max(
                    0.0, (self.monotonic() - checkout_started) * 1000
                ),
            )
        except httpx.RequestError:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CHECKOUT_TRANSPORT,
                safe_error_code=(
                    TrafficSafeErrorCodeV0232.CHECKOUT_TRANSPORT_ERROR
                ),
                cart_latency_ms=cart_latency,
                checkout_latency_ms=max(
                    0.0, (self.monotonic() - checkout_started) * 1000
                ),
            )
        checkout_latency = max(
            0.0, (self.monotonic() - checkout_started) * 1000
        )
        checkout_json = _json_response(checkout_response)
        checkout_content_type = _response_content_type(checkout_response)
        checkout_shape = (
            None if checkout_json is None else _response_shape_sha256(checkout_json)
        )
        checkout_shape_summary = (
            None if checkout_json is None else _response_shape_summary(checkout_json)
        )
        if checkout_response.status_code not in contract.checkout_success_statuses:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                checkout_status=checkout_response.status_code,
                checkout_content_type=checkout_content_type,
                checkout_shape=checkout_shape,
                checkout_shape_summary=checkout_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CHECKOUT_HTTP,
                safe_error_code=(
                    TrafficSafeErrorCodeV0232.CHECKOUT_HTTP_NON_SUCCESS
                ),
                cart_latency_ms=cart_latency,
                checkout_latency_ms=checkout_latency,
            )
        if not _valid_checkout_response(checkout_json):
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                checkout_status=checkout_response.status_code,
                checkout_content_type=checkout_content_type,
                checkout_shape=checkout_shape,
                checkout_shape_summary=checkout_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.CHECKOUT_RESPONSE,
                safe_error_code=(
                    TrafficSafeErrorCodeV0232.CHECKOUT_RESPONSE_SCHEMA_INVALID
                ),
                cart_latency_ms=cart_latency,
                checkout_latency_ms=checkout_latency,
            )
        assert checkout_json is not None
        checkout_items = checkout_json["items"]
        assert isinstance(checkout_items, list)
        checkout_business_success = (
            bool(checkout_json.get("orderId"))
            and any(
                isinstance(entry, dict)
                and isinstance(entry.get("item"), dict)
                and entry["item"].get("productId") == item.get("productId")
                and entry["item"].get("quantity") == item.get("quantity")
                for entry in checkout_items
            )
        )
        if not checkout_business_success:
            return self._observation(
                ordinal=ordinal,
                user_id=safe_user,
                contract=contract,
                started_at=started_at,
                cart_status=cart_response.status_code,
                cart_content_type=cart_content_type,
                cart_shape=cart_shape,
                cart_shape_summary=cart_shape_summary,
                checkout_status=checkout_response.status_code,
                checkout_content_type=checkout_content_type,
                checkout_shape=checkout_shape,
                checkout_shape_summary=checkout_shape_summary,
                business_success=False,
                failure_stage=CheckoutTrafficStageV0232.BUSINESS_SUCCESS,
                safe_error_code=(
                    TrafficSafeErrorCodeV0232.CHECKOUT_BUSINESS_SUCCESS_MISSING
                ),
                cart_latency_ms=cart_latency,
                checkout_latency_ms=checkout_latency,
            )
        return self._observation(
            ordinal=ordinal,
            user_id=safe_user,
            contract=contract,
            started_at=started_at,
            cart_status=cart_response.status_code,
            cart_content_type=cart_content_type,
            cart_shape=cart_shape,
            cart_shape_summary=cart_shape_summary,
            checkout_status=checkout_response.status_code,
            checkout_content_type=checkout_content_type,
            checkout_shape=checkout_shape,
            checkout_shape_summary=checkout_shape_summary,
            business_success=True,
            failure_stage=None,
            safe_error_code=None,
            cart_latency_ms=cart_latency,
            checkout_latency_ms=checkout_latency,
        )

    def run(
        self,
        *,
        endpoint: str,
        profile: HealthyTrafficProfileV0232,
        contract: CheckoutTrafficContractV0232,
        role: Literal["PREFLIGHT", "FORMAL"],
    ) -> HealthyTrafficExecutionV0232:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.path != contract.checkout_path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("healthy traffic endpoint must be the local checkout API")
        started_at = self.clock()
        observations: list[CheckoutTransactionObservationV0232] = []
        for ordinal in range(1, profile.transactions + 1):
            cart_payload, checkout_payload = self._payloads(
                profile.request_seed, ordinal
            )
            observation = self.observe_transaction(
                endpoint=endpoint,
                ordinal=ordinal,
                contract=contract,
                cart_payload=cart_payload,
                checkout_payload=checkout_payload,
            )
            observations.append(observation)
            if ordinal < profile.transactions:
                self.sleep(1.0 / profile.requests_per_second)
        failures = Counter(
            item.failure_stage.value
            for item in observations
            if item.failure_stage is not None
        )
        successful = sum(item.business_success for item in observations)
        failed = len(observations) - successful
        run = HealthyTrafficRunV0232.build(
            role=role,
            profile_sha256=profile.profile_sha256,
            contract_sha256=contract.contract_sha256,
            planned_transactions=profile.transactions,
            completed_transactions=len(observations),
            successful_transactions=successful,
            failed_transactions=failed,
            stage_failure_counts=dict(sorted(failures.items())),
            transport_retry_count=0,
            started_at=started_at,
            ended_at=self.clock(),
            passed=(
                len(observations) == profile.transactions
                and successful == profile.transactions
                and failed == 0
            ),
            transaction_observation_sha256s=[
                item.observation_sha256 for item in observations
            ],
        )
        return HealthyTrafficExecutionV0232.build(
            run=run,
            observations=tuple(observations),
        )


__all__ = [
    "CheckoutTrafficContractV0232",
    "CheckoutTrafficStageV0232",
    "CheckoutTransactionObservationV0232",
    "HealthyTrafficExecutionV0232",
    "HealthyTrafficPreflightV0232",
    "HealthyTrafficProfileV0232",
    "HealthyTrafficRunV0232",
    "HealthyTrafficRunnerV0232",
    "IncidentTrafficBindingV0232",
    "SourceFileBindingV0232",
    "TRAFFIC_CONTRACT_PASS_V0232",
    "TrafficContractErrorV0232",
    "TrafficSafeErrorCodeV0232",
    "load_checkout_traffic_contract_v0232",
]
