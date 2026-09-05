"""Private fixed Payment control adapter; authenticated Unix sockets isolate writes.

The upstream flag UI does not authenticate writes. Only this gateway receives
its private control profile; the executor uses a distinct authenticated socket.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx
from pydantic import ConfigDict, Field, SecretStr, model_validator

from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.remediation.attempt_contracts import AttemptStateV1
from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
from ecomsre.product.remediation.execution_contracts import ExecutorDispatchV1
from ecomsre.product.remediation.executor import (
    ProductPaymentConfigurationRollbackExecutor,
)
from ecomsre.product.remediation.state import (
    StateObservationV1,
    TrustedStateBindingV1,
    validate_observation,
)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


class PrivatePaymentControlProfileV1(ProductModelV1):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["ecomsre.product.private-payment-control.v1"] = (
        "ecomsre.product.private-payment-control.v1"
    )
    binding: TrustedStateBindingV1
    flag_control_url: str
    flag_evaluation_url: str
    flag_file: Path
    ownership_witness_file: Path
    baseline_document: dict[str, Any]
    fault_document: dict[str, Any]

    @model_validator(mode="after")
    def exact_profile(self) -> Self:
        for url in (self.flag_control_url, self.flag_evaluation_url):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "host.docker.internal"}
                or parsed.port is None
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("control endpoint is not a fixed local endpoint")
        baseline = json.loads(json.dumps(self.baseline_document))
        fault = json.loads(json.dumps(self.fault_document))
        try:
            if (
                baseline["flags"]["paymentFailure"]["defaultVariant"] != "off"
                or fault["flags"]["paymentFailure"]["defaultVariant"] != "100%"
            ):
                raise ValueError("Payment variants differ")
            fault["flags"]["paymentFailure"]["defaultVariant"] = "off"
        except (KeyError, TypeError) as error:
            raise ValueError("Payment flag is unavailable") from error
        if fault != baseline:
            raise ValueError("flag documents differ outside Payment")
        if (
            digest(self.baseline_document) != self.binding.baseline_configuration_digest
            or digest(self.fault_document) != self.binding.fault_configuration_digest
        ):
            raise ValueError("control document binding differs")
        if (
            self.flag_file.is_symlink()
            or not self.flag_file.is_absolute()
            or not self.ownership_witness_file.is_absolute()
        ):
            raise ValueError("private control paths differ")
        if (
            digest(
                {
                    "flag_control_url": self.flag_control_url,
                    "flag_evaluation_url": self.flag_evaluation_url,
                    "baseline_configuration_digest": self.binding.baseline_configuration_digest,
                    "fault_configuration_digest": self.binding.fault_configuration_digest,
                }
            )
            != self.binding.control_identity_sha256
        ):
            raise ValueError("control identity differs")
        return self


class OwnershipWitnessV1(ProductModelV1):
    model_config = ConfigDict(frozen=True, extra="forbid")
    environment_id: str
    environment_ownership_digest: str
    target_identity_digest: str
    control_identity_sha256: str
    non_owned_resources_unchanged: Literal[True]
    observed_at: datetime
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalPaymentStateProviderV1:
    """Read-only file/UI/OFREP agreement plus a fresh signed ownership witness."""

    def __init__(
        self,
        profile: PrivatePaymentControlProfileV1,
        witness_key: SecretStr,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        client: httpx.Client | None = None,
    ) -> None:
        self.profile = PrivatePaymentControlProfileV1.model_validate_json(
            profile.model_dump_json()
        )
        self._witness_key = witness_key
        self.clock = clock
        self.client = client or httpx.Client(
            timeout=5, trust_env=False, follow_redirects=False
        )

    def ownership(self) -> None:
        path = self.profile.ownership_witness_file
        if path.is_symlink():
            raise ValueError("ownership witness unavailable")
        witness = OwnershipWitnessV1.model_validate_json(path.read_bytes())
        body = witness.model_dump(mode="json", exclude={"signature"})
        expected = hmac.new(
            self._witness_key.get_secret_value().encode(),
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        binding = self.profile.binding
        if (
            not hmac.compare_digest(expected, witness.signature)
            or witness.observed_at.tzinfo is None
            or not timedelta(0)
            <= self.clock() - witness.observed_at
            <= timedelta(seconds=15)
        ):
            raise ValueError("ownership witness is not fresh and authenticated")
        if any(
            getattr(witness, name) != getattr(binding, name)
            for name in (
                "environment_id",
                "environment_ownership_digest",
                "target_identity_digest",
                "control_identity_sha256",
            )
        ):
            raise ValueError("ownership witness identity differs")

    def read_current(self) -> StateObservationV1:
        self.ownership()
        profile = self.profile
        if profile.flag_file.is_symlink():
            raise ValueError("private configuration unavailable")
        document = json.loads(profile.flag_file.read_bytes())
        config_digest = digest(document)
        if config_digest not in {
            profile.binding.baseline_configuration_digest,
            profile.binding.fault_configuration_digest,
        }:
            raise ValueError("configuration differs from both bound states")
        response = self.client.get(profile.flag_control_url + "/read")
        response.raise_for_status()
        if response.json() != {"flags": document.get("flags")}:
            raise ValueError("file and control readback disagree")
        response = self.client.post(
            profile.flag_evaluation_url + "/ofrep/v1/evaluate/flags/paymentFailure",
            json={},
        )
        response.raise_for_status()
        fault = config_digest == profile.binding.fault_configuration_digest
        evaluation = response.json()
        if (
            evaluation.get("variant") != ("100%" if fault else "off")
            or type(evaluation.get("value")) is not int
            or evaluation["value"] != int(fault)
        ):
            raise ValueError("flag evaluation disagrees")
        self.ownership()
        now = self.clock()
        return StateObservationV1.build(
            environment_id=profile.binding.environment_id,
            environment_owned=True,
            local_control_trusted=True,
            environment_ownership_digest=profile.binding.environment_ownership_digest,
            target_identity_digest=profile.binding.target_identity_digest,
            control_identity_sha256=profile.binding.control_identity_sha256,
            target_logical_service="payment",
            baseline_configuration_digest=profile.binding.baseline_configuration_digest,
            current_configuration_digest=config_digest,
            fault_still_present=fault,
            observed_at=now,
            created_at=now,
        )


class GuardedPaymentControlV1:
    """Gateway-only write capability, with a separate durable duplicate fence."""

    def __init__(
        self,
        attempts: RemediationAttemptRepositoryV1,
        state: LocalPaymentStateProviderV1,
        ledger: Path,
    ) -> None:
        self.attempts = attempts
        self.state = state
        self.ledger = ledger
        self.lock = threading.Lock()
        if ledger.is_symlink():
            raise ValueError("control ledger must not be a symlink")
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS consumed (intent_id TEXT PRIMARY KEY, dispatch_sha256 TEXT NOT NULL UNIQUE)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.ledger)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def restore(self, dispatch: ExecutorDispatchV1) -> StateObservationV1:
        dispatch = ExecutorDispatchV1.model_validate_json(dispatch.model_dump_json())
        with self.lock:
            repo = self.attempts
            with repo.store.connect() as connection:
                # Hold the approval/revocation writer fence through the single send.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    attempt = repo._read(connection, dispatch.attempt_id)
                    repo._require_lease(
                        attempt,
                        attempt.active_lease_owner or "",
                        attempt.lease_generation,
                    )
                    if attempt.state != AttemptStateV1.EXECUTING:
                        raise ValueError("attempt is not executing")
                    row = connection.execute(
                        "SELECT payload_json FROM remediation_executor_dispatches WHERE attempt_id = ?",
                        (attempt.attempt_id,),
                    ).fetchone()
                    if row is None or row[0] != canonical_dispatch(dispatch):
                        raise ValueError("dispatch binding differs")
                    executor = ProductPaymentConfigurationRollbackExecutor(
                        repo, self
                    )  # gateway is a trusted fixed adapter
                    if executor._dispatch(connection, attempt) != dispatch:
                        raise ValueError("dispatch authority differs")
                    authorization = repo._authorization(connection, attempt)
                    approval = repo.approvals.require_active_approval(
                        connection, attempt.approval_id, attempt.candidate_id
                    )
                    candidate = repo.approvals._candidate(
                        connection, attempt.candidate_id
                    )
                    repo._current_candidate(candidate)
                    observed = self.state.read_current()
                    assert repo.binding is not None
                    validate_observation(
                        binding=repo.binding,
                        candidate=candidate,
                        approval=approval,
                        observation=observed,
                        now=repo.clock(),
                    )
                    if repo.clock() >= min(
                        authorization.expires_at, approval.expires_at
                    ):
                        raise ValueError("authorization expired")
                    with self._connect() as ledger:
                        ledger.execute(
                            "INSERT INTO consumed VALUES (?, ?)",
                            (dispatch.write_intent_id, dispatch.dispatch_sha256),
                        )
                    self.state.ownership()
                    if repo.clock() >= min(
                        authorization.expires_at, approval.expires_at
                    ):
                        raise ValueError("authorization expired")
                    repo._require_lease(
                        attempt,
                        attempt.active_lease_owner or "",
                        attempt.lease_generation,
                    )
                    PrivatePaymentControlProfileV1.model_validate_json(
                        self.state.profile.model_dump_json()
                    )
                    # Exactly one HTTP send, redirects/proxies/retries disabled. Unknown
                    # transport outcomes remain consumed; only readback is polled.
                    response = self.state.client.post(
                        self.state.profile.flag_control_url + "/write",
                        json={"data": self.state.profile.baseline_document},
                    )
                    response.raise_for_status()
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            deadline = time.monotonic() + 15
            while True:
                try:
                    after = self.state.read_current()
                    if not after.fault_still_present:
                        return after
                except Exception:
                    pass
                if time.monotonic() >= deadline:
                    raise ValueError("restoration outcome unknown")
                time.sleep(0.1)

    def restore_baseline(
        self, dispatch: ExecutorDispatchV1, *, expires_at: datetime
    ) -> StateObservationV1:
        # This method is only for the fixed adapter protocol; expiration is also
        # independently read from durable authorization inside restore().
        if self.attempts.clock() >= expires_at:
            raise ValueError("authorization expired")
        return self.restore(dispatch)


def canonical_dispatch(dispatch: ExecutorDispatchV1) -> str:
    return json.dumps(
        dispatch.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def required_secret(name: str) -> SecretStr:
    value = os.environ.get(name, "")
    if len(value) < 32 or any(c.isspace() for c in value):
        raise ValueError("required process credential unavailable")
    return SecretStr(value)


def control_apps(
    control: GuardedPaymentControlV1, *, read_token: SecretStr, write_token: SecretStr
) -> tuple[FastAPI, FastAPI]:
    if hmac.compare_digest(
        read_token.get_secret_value(), write_token.get_secret_value()
    ):
        raise ValueError("read and write credentials must differ")
    read_app, write_app = (
        FastAPI(docs_url=None, redoc_url=None, openapi_url=None),
        FastAPI(docs_url=None, redoc_url=None, openapi_url=None),
    )

    async def invalid_request(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse({"detail": "CONTROL_REQUEST_INVALID"}, status_code=422)

    read_app.add_exception_handler(RequestValidationError, invalid_request)  # type: ignore[arg-type]
    write_app.add_exception_handler(RequestValidationError, invalid_request)  # type: ignore[arg-type]

    def authenticate(value: str | None, expected: SecretStr) -> None:
        if value is None or not hmac.compare_digest(
            value, "Bearer " + expected.get_secret_value()
        ):
            raise HTTPException(status_code=403, detail="CONTROL_AUTHORIZATION_DENIED")

    @read_app.get("/state")
    def state(authorization: str | None = Header(default=None)) -> StateObservationV1:
        authenticate(authorization, read_token)
        try:
            return control.state.read_current()
        except Exception:
            raise HTTPException(
                status_code=409, detail="CONTROL_STATE_UNAVAILABLE"
            ) from None

    @write_app.post("/restore-payment-baseline")
    def restore(
        dispatch: ExecutorDispatchV1, authorization: str | None = Header(default=None)
    ) -> StateObservationV1:
        authenticate(authorization, write_token)
        try:
            return control.restore(dispatch)
        except Exception:
            raise HTTPException(
                status_code=409, detail="CONTROL_RESTORE_UNKNOWN"
            ) from None

    return read_app, write_app


class UnixPaymentStateClientV1:
    def __init__(self, socket: Path, token: SecretStr) -> None:
        self._token = token
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket), retries=0),
            base_url="http://control",
            timeout=25,
            trust_env=False,
            follow_redirects=False,
        )

    def read_current(self) -> StateObservationV1:
        response = self.client.get(
            "/state",
            headers={"Authorization": "Bearer " + self._token.get_secret_value()},
        )
        response.raise_for_status()
        return StateObservationV1.model_validate_json(response.content)


class UnixPaymentRestoreClientV1:
    def __init__(self, socket: Path, token: SecretStr) -> None:
        self._token = token
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket), retries=0),
            base_url="http://control",
            timeout=25,
            trust_env=False,
            follow_redirects=False,
        )

    def restore_baseline(
        self, dispatch: ExecutorDispatchV1, *, expires_at: datetime
    ) -> StateObservationV1:
        if datetime.now(UTC) >= expires_at:
            raise ValueError("authorization expired")
        response = self.client.post(
            "/restore-payment-baseline",
            json=dispatch.model_dump(mode="json"),
            headers={"Authorization": "Bearer " + self._token.get_secret_value()},
        )
        response.raise_for_status()
        return StateObservationV1.model_validate_json(response.content)
