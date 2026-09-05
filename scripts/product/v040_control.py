"""Private setup, observation scheduling and one evaluator-side Payment fault."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import threading
import time

import httpx
from pydantic import SecretStr

from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
from ecomsre.product.remediation.execution_contracts import RecoveryPolicyV1
from ecomsre.product.remediation.payment_control import (
    LocalPaymentStateProviderV1,
    PrivatePaymentControlProfileV1,
    digest,
)
from ecomsre.product.remediation.recovery import RecoveryRepositoryV1
from ecomsre.product.remediation.repository import (
    REGISTRY_SHA256,
    RemediationRepositoryV1,
)
from ecomsre.product.remediation.state import TrustedStateBindingV1
from ecomsre.product.remediation.window_requests import ObserverWindowRequestV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.live_sandbox.product_v040 import ProductV040Lifecycle
from scripts.product.v040_observer import HostLoopbackTransportV040, LiveObserverV040
from scripts.product.v040_runtime import ProductRuntimeV040, read_json, seal_private


def write_bindings(
    runtime: ProductRuntimeV040, lifecycle: ProductV040Lifecycle
) -> None:
    baseline = read_json(runtime.private / "host/healthy-baseline.json")
    env = lifecycle.environment
    env.verify_owned_resources(require_complete=True)
    sandbox_ids = {
        kind: sorted(env._owned_ids(kind))
        for kind in ("container", "network", "volume")
    }
    containers = json.loads(
        runtime.docker("container", "inspect", *sandbox_ids["container"])
    )
    payment = [
        item
        for item in containers
        if item["Config"]["Labels"]["com.docker.compose.service"] == "payment"
    ]
    if len(payment) != 1:
        raise ValueError("Payment target inventory differs")
    target = {
        "id": payment[0]["Id"],
        "image": payment[0]["Image"],
        "labels": payment[0]["Config"]["Labels"],
    }
    ownership = {
        "sandbox_ids": sandbox_ids,
        "daemon": runtime.boundary(),
        "target": target,
    }
    seal_private(runtime.private / "host/ownership.json", ownership)
    control_url = "http://host.docker.internal:18080/feature/api"
    evaluation_url = "http://host.docker.internal:18016"
    baseline_digest = digest(lifecycle.baseline_document)
    fault_digest = digest(lifecycle.fault_document)
    control_digest = digest(
        {
            "flag_control_url": control_url,
            "flag_evaluation_url": evaluation_url,
            "baseline_configuration_digest": baseline_digest,
            "fault_configuration_digest": fault_digest,
        }
    )
    binding = TrustedStateBindingV1.build(
        environment_id=baseline["environment"]["environment_id"],
        environment_ownership_digest=digest(ownership),
        target_identity_digest=digest(target),
        identity_map_sha256=baseline["verification"]["service_identity_map"][
            "identity_sha256"
        ],
        control_identity_sha256=control_digest,
        baseline_id=baseline["baseline"]["baseline_id"],
        baseline_sha256=baseline["baseline"]["baseline_sha256"],
        baseline_configuration_digest=baseline_digest,
        fault_configuration_digest=fault_digest,
        registry_sha256=REGISTRY_SHA256,
        created_at=datetime.now(UTC),
    )
    profile = PrivatePaymentControlProfileV1.model_validate(
        {
            "binding": binding,
            "flag_control_url": control_url,
            "flag_evaluation_url": evaluation_url,
            "flag_file": "/runtime/payment-flags/demo.flagd.json",
            "ownership_witness_file": "/run/remediation-observer/ownership.json",
            "baseline_document": lifecycle.baseline_document,
            "fault_document": lifecycle.fault_document,
        }
    )
    settings = read_json(
        runtime.repository / "config/product-v040/live-profile.v1.json"
    )
    policy = RecoveryPolicyV1.build(
        **{
            key: getattr(binding, key)
            for key in (
                "environment_id",
                "baseline_sha256",
                "baseline_configuration_digest",
                "fault_configuration_digest",
                "target_identity_digest",
                "control_identity_sha256",
                "environment_ownership_digest",
            )
        },
        business_error_ratio_max=settings["recovery_business_error_ratio_max"],
        minimum_business_requests=settings["recovery_minimum_requests"],
        window_seconds=settings["recovery_window_seconds"],
        created_at=datetime.now(UTC),
    )
    for name, value in (
        ("config/binding.json", binding),
        ("config/recovery-policy.json", policy),
        ("control/profile.json", profile),
    ):
        seal_private(runtime.private / name, value.model_dump(mode="json"))


def observer_for(
    runtime: ProductRuntimeV040, lifecycle: ProductV040Lifecycle
) -> LiveObserverV040:
    profile = read_json(runtime.private / "control/profile.json")
    profile.update(
        flag_file=str(runtime.private / "sandbox/runtime/flagd/demo.flagd.json"),
        ownership_witness_file=str(runtime.private / "observer/ownership.json"),
    )
    host_profile = PrivatePaymentControlProfileV1.model_validate(profile)
    host_state = LocalPaymentStateProviderV1(
        host_profile,
        SecretStr(runtime.env["ECOMSRE_REMEDIATION_OBSERVER_TOKEN"]),
        client=httpx.Client(
            transport=HostLoopbackTransportV040(),
            trust_env=False,
            follow_redirects=False,
            timeout=5,
        ),
    )
    store = SqliteStoreV1(runtime.private / "product/product.sqlite3")
    objects = ContentAddressedObjectStoreV1(
        runtime.private / "product/objects", metadata_store=store
    )
    attempts = RemediationAttemptRepositoryV1(
        RemediationRepositoryV1(store, objects),
        binding=host_profile.binding,
        provider=host_state,
    )
    recovery = RecoveryRepositoryV1(attempts)
    recovery.bind_policy(
        RecoveryPolicyV1.model_validate_json(
            (runtime.private / "config/recovery-policy.json").read_bytes()
        )
    )
    return LiveObserverV040(runtime, lifecycle, recovery, host_state)


class ObserverLoopV040:
    def __init__(self, observer: LiveObserverV040) -> None:
        self.observer = observer
        self.stop = observer.stop_event
        self.failed: str | None = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        observer = self.observer
        policy = RecoveryPolicyV1.model_validate_json(
            (observer.private / "config/recovery-policy.json").read_bytes()
        )
        while not self.stop.is_set():
            try:
                observer.witness()
                for path in sorted((observer.private / "requests").glob("*.json")):
                    try:
                        request = ObserverWindowRequestV1.model_validate_json(
                            path.read_bytes()
                        )
                    except ValueError:
                        continue  # Publication may still be flushing; no measurement starts.
                    start = (
                        observer.private
                        / "host/windows"
                        / f"{request.attempt_id}-{request.ordinal}-started.json"
                    )
                    if start.exists():
                        continue
                    try:
                        observer.window(request, policy)
                    except Exception as error:
                        failure = (
                            observer.private
                            / "host/windows"
                            / f"{request.attempt_id}-{request.ordinal}-failure.json"
                        )
                        if not failure.exists():
                            seal_private(
                                failure,
                                {
                                    "request_sha256": request.request_sha256,
                                    "error_type": type(error).__name__,
                                    "created_at": datetime.now(UTC).isoformat(),
                                },
                            )
                        # A rejected request must never be attempted again, even
                        # if rejection happened before the measurement sentinel.
                        if not start.exists():
                            seal_private(
                                start,
                                {
                                    "request_sha256": request.request_sha256,
                                    "terminal": "REJECTED_WITHOUT_SAMPLING",
                                },
                            )
            except Exception as error:
                if self.stop.is_set():
                    return
                self.failed = type(error).__name__
                path = observer.private / "host/observer-loop-failure.json"
                if not path.exists():
                    seal_private(
                        path,
                        {
                            "error_type": self.failed,
                            "created_at": datetime.now(UTC).isoformat(),
                        },
                    )
                return
            self.stop.wait(2)

    def __enter__(self) -> ObserverLoopV040:
        self.observer.witness()
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=60)
        if self.thread.is_alive():
            raise RuntimeError(
                "observer still active; retain runtime for bounded reconciliation"
            )


def inject_once(observer: LiveObserverV040, manifest_sha256: str) -> None:
    observer.witness()
    before = observer.host_state.read_current()
    if before.fault_still_present:
        raise ValueError("fault already present before the one campaign")
    private = observer.private
    seal_private(
        private / "host/fault-intent.json",
        {
            "manifest_sha256": manifest_sha256,
            "created_at": datetime.now(UTC).isoformat(),
            "maximum_mutations": 1,
        },
    )
    state = observer.host_state
    # Exactly one send, after durable intent. Any unknown response consumes the
    # campaign; no restoration helper, redirects, proxy, retries or compensation.
    response = state.client.post(
        state.profile.flag_control_url + "/write",
        json={"data": state.profile.fault_document},
    )
    response.raise_for_status()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            current = state.read_current()
            if current.fault_still_present:
                os.chmod(state.profile.flag_file, 0o600)
                seal_private(
                    private / "host/fault-confirmed.json",
                    {
                        "observation": current.model_dump(mode="json"),
                        "manifest_sha256": manifest_sha256,
                    },
                )
                return
        except (ValueError, httpx.HTTPError):
            pass
        time.sleep(0.25)
    raise RuntimeError("fault send outcome could not be confirmed; allowance consumed")
