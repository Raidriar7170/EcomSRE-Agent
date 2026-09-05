"""Independent owned-local observations, never an executor or reset path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import time
import threading
from typing import Any

import httpx

from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
)
from ecomsre.product.remediation.execution_contracts import (
    RecoveryObservationV1,
    RecoveryPolicyV1,
)
from ecomsre.product.remediation.payment_control import (
    LocalPaymentStateProviderV1,
    OwnershipWitnessV1,
    digest,
)
from ecomsre.product.remediation.recovery import RecoveryRepositoryV1
from ecomsre.product.remediation.window_requests import ObserverWindowRequestV1
from scripts.product.v040_runtime import (
    ProductRuntimeV040,
    atomic_private,
    read_json,
    seal_private,
)
from scripts.live_sandbox.product_v040 import ProductV040Lifecycle


def checkout_business_passed(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("orderId"), str)
        and bool(value["orderId"])
        and isinstance(value.get("items"), list)
        and len(value["items"]) == 1
        and isinstance(value["items"][0], dict)
        and isinstance(value["items"][0].get("item"), dict)
        and value["items"][0]["item"].get("productId") == "0PUK6V6EV0"
        and value["items"][0]["item"].get("quantity") == 1
    )


class HostLoopbackTransportV040(httpx.BaseTransport):
    """Preserve the sealed gateway URLs while resolving their host locally."""

    def __init__(self) -> None:
        self.transport = httpx.HTTPTransport(retries=0, trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.url.scheme != "http"
            or request.url.host != "host.docker.internal"
            or request.url.port not in {18080, 18016}
        ):
            raise ValueError("host observer origin is outside the fixed local control")
        request.url = request.url.copy_with(host="127.0.0.1")
        return self.transport.handle_request(request)

    def close(self) -> None:
        self.transport.close()


def resource_fingerprints(
    runtime: ProductRuntimeV040, *, exclude_projects: set[str]
) -> dict[str, dict[str, str]]:
    """Static identity/config/state fingerprints; no credentials or raw logs stored."""
    result: dict[str, dict[str, str]] = {}
    for kind in ("container", "network", "volume"):
        ids = runtime.docker(
            kind, "ls", "-q", *(("--all",) if kind == "container" else ())
        ).split()
        fingerprints: dict[str, str] = {}
        if ids:
            for item in json.loads(runtime.docker(kind, "inspect", *ids)):
                labels = (
                    item["Config"].get("Labels")
                    if kind == "container"
                    else item.get("Labels")
                ) or {}
                if labels.get("com.docker.compose.project") in exclude_projects:
                    continue
                if kind == "container":
                    state = item["State"]
                    value = {
                        "image": item["Image"],
                        "labels": labels,
                        "restart_count": item["RestartCount"],
                        "state": {
                            key: state.get(key)
                            for key in (
                                "Running",
                                "Paused",
                                "Restarting",
                                "Dead",
                                "OOMKilled",
                                "StartedAt",
                                "FinishedAt",
                            )
                        },
                        "mounts": item["Mounts"],
                        "ports": item["HostConfig"]["PortBindings"],
                        "networks": item["NetworkSettings"]["Networks"],
                        "host_config_sha256": digest(item["HostConfig"]),
                        "config_sha256": digest(item["Config"]),
                    }
                else:
                    value = item
                fingerprints[item.get("Id", item.get("Name"))] = digest(value)
        result[kind] = fingerprints
    return result


class LiveObserverV040:
    def __init__(
        self,
        runtime: ProductRuntimeV040,
        lifecycle: ProductV040Lifecycle,
        recovery: RecoveryRepositoryV1,
        host_state: LocalPaymentStateProviderV1,
    ) -> None:
        self.runtime = runtime
        self.lifecycle = lifecycle
        self.recovery = recovery
        self.host_state = host_state
        self.private = runtime.private
        self.stop_event = threading.Event()
        self.profile = read_json(
            runtime.repository / "config/product-v040/live-profile.v1.json"
        )

    def unchanged(self) -> bool:
        expected = read_json(self.private / "host/non-owned-before.json")
        actual = resource_fingerprints(
            self.runtime,
            exclude_projects={"ecomsre-product-v040", "ecomsre-live-sandbox-v1"},
        )
        return actual == expected

    def check_running(self) -> None:
        if self.stop_event.is_set():
            raise InterruptedError("observer was stopped; consumed window is retained")

    def witness(self) -> OwnershipWitnessV1:
        self.check_running()
        env = self.lifecycle.environment
        actual_daemon = env.verify_local_docker()
        if actual_daemon != self.runtime.boundary():
            raise ValueError("Product and Sandbox daemon differ")
        env.verify_owned_resources(require_complete=True)
        product_owned = self.runtime.owned()
        product_frozen = self.private / "host/product-ownership.json"
        if product_frozen.exists() and read_json(product_frozen) != product_owned:
            raise ValueError("Product frozen inventory changed")
        owner = read_json(self.private / "host/ownership.json")
        if {
            kind: sorted(env._owned_ids(kind))
            for kind in ("container", "network", "volume")
        } != owner["sandbox_ids"]:
            raise ValueError("sandbox resource identities changed")
        if not self.unchanged():
            raise ValueError("non-owned resource drift")
        binding = self.host_state.profile.binding
        body = {
            "environment_id": binding.environment_id,
            "environment_ownership_digest": binding.environment_ownership_digest,
            "target_identity_digest": binding.target_identity_digest,
            "control_identity_sha256": binding.control_identity_sha256,
            "non_owned_resources_unchanged": True,
            "observed_at": datetime.now(UTC).isoformat(),
        }
        unsigned = OwnershipWitnessV1.model_validate({**body, "signature": "0" * 64})
        signed_body = unsigned.model_dump(mode="json", exclude={"signature"})
        key = self.runtime.env["ECOMSRE_REMEDIATION_OBSERVER_TOKEN"].encode()
        signature = hmac.new(
            key,
            json.dumps(signed_body, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        value = OwnershipWitnessV1.model_validate(
            {**signed_body, "signature": signature}
        )
        atomic_private(
            self.private / "observer/ownership.json", value.model_dump(mode="json")
        )
        return value

    def validate_request(
        self, request: ObserverWindowRequestV1, policy: RecoveryPolicyV1
    ) -> None:
        # Independent final gate immediately before the consumed measurement start.
        request = ObserverWindowRequestV1.model_validate_json(request.model_dump_json())
        repo = self.recovery.attempts
        attempt = repo.get(request.attempt_id)
        receipt = self.recovery.receipt(request.attempt_id)
        now = datetime.now(UTC)
        if (
            attempt.state.value != "VERIFYING"
            or attempt.terminal is not None
            or attempt.lease_expires_at is None
            or now >= attempt.lease_expires_at
            or receipt is None
            or receipt.outcome != "APPLIED"
            or receipt.receipt_sha256 != request.receipt_sha256
            or request.policy_sha256 != policy.policy_sha256
            or not receipt.ended_at
            <= request.started_after
            <= request.created_at
            <= now
            or (now - request.created_at).total_seconds() > 30
        ):
            raise ValueError("observer request authority is not current")
        with repo.store.connect() as connection:
            row = connection.execute(
                "SELECT started_at FROM remediation_window_acquisitions WHERE attempt_id = ? AND ordinal = ?",
                (request.attempt_id, request.ordinal),
            ).fetchone()
            if row is None or datetime.fromisoformat(row[0]) > request.started_after:
                raise ValueError("observer window is not reserved")
            if connection.execute(
                "SELECT 1 FROM remediation_recovery_windows WHERE attempt_id = ? AND ordinal = ?",
                (request.attempt_id, request.ordinal),
            ).fetchone():
                raise ValueError("observer window already persisted")
        prior = self.recovery.windows(request.attempt_id)
        if any(window.ended_at > request.started_after for window in prior):
            raise ValueError("observer request overlaps a previous window")

    def window(
        self, request: ObserverWindowRequestV1, policy: RecoveryPolicyV1
    ) -> RecoveryObservationV1:
        window_root = self.private / "host/windows"
        start_path = (
            window_root / f"{request.attempt_id}-{request.ordinal}-started.json"
        )
        self.witness()
        self.validate_request(request, policy)
        # The sentinel consumes this exact ordinal before any business probe.
        seal_private(
            start_path,
            {
                "request": request.model_dump(mode="json"),
                "consumed_at": datetime.now(UTC).isoformat(),
            },
        )
        started = datetime.now(UTC)
        monotonic_start = time.monotonic()
        ended = started + timedelta(seconds=policy.window_seconds)
        observations: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        requests = errors = 0
        next_ordinal = 0
        with httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=self.profile["recovery_request_timeout_seconds"],
        ) as client:
            while next_ordinal < self.profile["recovery_requests_per_window"]:
                if datetime.now(UTC) >= ended:
                    raise ValueError("business observation window expired")
                due = (
                    monotonic_start
                    + next_ordinal * self.profile["recovery_request_interval_seconds"]
                )
                if due > time.monotonic():
                    self.stop_event.wait(due - time.monotonic())
                self.check_running()
                elapsed = time.monotonic() - monotonic_start
                self.witness()
                self.check_running()
                state = self.host_state.read_current()
                self.check_running()
                health = self.lifecycle.environment.service_health()["payment"]
                checks.append(
                    {
                        "observed_at": datetime.now(UTC).isoformat(),
                        "state": state.model_dump(mode="json"),
                        "payment_healthy": health,
                    }
                )
                if (
                    next_ordinal < self.profile["recovery_requests_per_window"]
                    and elapsed
                    >= next_ordinal * self.profile["recovery_request_interval_seconds"]
                ):
                    # One cart + one checkout per frozen ordinal. Transport
                    # failures count as errors; no retry and no replacement.
                    seed = self.profile["recovery_request_seeds"][request.ordinal - 1]
                    ordinal = next_ordinal + 1
                    self.check_running()
                    request_start = datetime.now(UTC)
                    if (ended - request_start).total_seconds() < 2 * self.profile[
                        "recovery_request_timeout_seconds"
                    ]:
                        raise ValueError(
                            "insufficient time for the bounded request pair"
                        )
                    business_passed = False
                    checkout_body: object = None
                    cart_status = checkout_status = None
                    try:
                        cart = client.post(
                            "http://127.0.0.1:18080/api/cart",
                            json=BoundedHealthyCheckoutTrafficV021._cart_payload(
                                seed, ordinal
                            ),
                        )
                        cart_status = cart.status_code
                        self.check_running()
                        if 200 <= cart_status < 300:
                            checkout = client.post(
                                "http://127.0.0.1:18080/api/checkout",
                                json=BoundedHealthyCheckoutTrafficV021._checkout_payload(
                                    seed, ordinal
                                ),
                            )
                            checkout_status = checkout.status_code
                            try:
                                checkout_body = checkout.json()
                                business_passed = checkout_business_passed(
                                    checkout_body
                                )
                            except ValueError:
                                pass
                    except httpx.RequestError:
                        pass
                    request_end = datetime.now(UTC)
                    requests += 1
                    errors += int(
                        not business_passed
                        or checkout_status is None
                        or not 200 <= checkout_status < 300
                        or request_end > ended
                    )
                    observations.append(
                        {
                            "ordinal": ordinal,
                            "started_at": request_start.isoformat(),
                            "ended_at": request_end.isoformat(),
                            "cart_status": cart_status,
                            "checkout_status": checkout_status,
                            "checkout_body": checkout_body,
                            "business_passed": business_passed,
                        }
                    )
                    next_ordinal += 1
                seal_private(
                    window_root
                    / f"{request.attempt_id}-{request.ordinal}-probe-{next_ordinal}.json",
                    {"checks": checks[-1:], "business": observations[-1:]},
                )
            remaining = (ended - datetime.now(UTC)).total_seconds()
            if remaining > 0:
                self.stop_event.wait(remaining)
        self.check_running()
        elapsed_ms = (time.monotonic() - monotonic_start) * 1000
        finalized = datetime.now(UTC)
        if not checks or any(
            datetime.fromisoformat(row["observed_at"]) > ended for row in checks
        ):
            raise ValueError("infrastructure probe escaped the fixed window")
        observation = RecoveryObservationV1.build(
            environment_id=policy.environment_id,
            policy_sha256=policy.policy_sha256,
            started_at=started,
            ended_at=ended,
            created_at=finalized,
            elapsed_ms=elapsed_ms,
            infrastructure_passed=all(row["payment_healthy"] for row in checks),
            endpoint_passed=bool(observations)
            and all(
                row["checkout_status"] is not None
                and 200 <= row["checkout_status"] < 300
                for row in observations
            ),
            business_requests=requests,
            business_errors=errors,
            configuration_digest=checks[-1]["state"]["current_configuration_digest"],
            flag_evaluation_restored=all(
                not row["state"]["fault_still_present"]
                and row["state"]["current_configuration_digest"]
                == policy.baseline_configuration_digest
                for row in checks
            ),
            non_owned_resources_unchanged=self.unchanged(),
            environment_ownership_digest=policy.environment_ownership_digest,
        )
        seal_private(
            window_root / f"{request.attempt_id}-{request.ordinal}-raw.json",
            {
                "request": request.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
                "business": observations,
                "checks": checks,
            },
        )
        body = observation.model_dump(mode="json")
        signature = hmac.new(
            self.runtime.env["ECOMSRE_REMEDIATION_OBSERVER_TOKEN"].encode(),
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        seal_private(
            self.private / "observer/windows" / f"{request.request_sha256}.json",
            {"observation": body, "signature": signature},
        )
        return observation
