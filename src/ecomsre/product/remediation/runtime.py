"""Default-disabled executor and control-gateway process entrypoints."""

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import threading
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import ConfigDict
import uvicorn

from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1
from ecomsre.product.remediation.execution_contracts import (
    RecoveryObservationV1,
    RecoveryPolicyV1,
)
from ecomsre.product.remediation.executor import (
    ProductPaymentConfigurationRollbackExecutor,
)
from ecomsre.product.remediation.payment_control import (
    GuardedPaymentControlV1,
    LocalPaymentStateProviderV1,
    PrivatePaymentControlProfileV1,
    UnixPaymentRestoreClientV1,
    UnixPaymentStateClientV1,
    control_apps,
    required_secret,
)
from ecomsre.product.remediation.recovery import (
    RecoveryRepositoryV1,
    RecoveryWindowProviderV1,
)
from ecomsre.product.remediation.recovery_transport import (
    SignedRecoveryWindowProviderV1,
    UnixRecoveryWindowClientV1,
)
from ecomsre.product.remediation.repository import RemediationRepositoryV1
from ecomsre.product.remediation.state import TrustedStateBindingV1
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def approvals_from_environment() -> RemediationRepositoryV1:
    settings = ProductSettingsV1.from_environment()
    store = SqliteStoreV1(settings.sqlite_path)
    return RemediationRepositoryV1(
        store,
        ContentAddressedObjectStoreV1(settings.object_store_root, metadata_store=store),
    )


def configured_attempts(
    approvals: RemediationRepositoryV1,
) -> RemediationAttemptRepositoryV1:
    """API loads only a public-safe binding and read-channel credential."""
    path = os.environ.get("ECOMSRE_REMEDIATION_BINDING_PATH")
    if not path:
        return RemediationAttemptRepositoryV1(approvals)
    binding = TrustedStateBindingV1.model_validate_json(Path(path).read_bytes())
    provider = UnixPaymentStateClientV1(
        Path(os.environ["ECOMSRE_REMEDIATION_READ_SOCKET"]),
        required_secret("ECOMSRE_REMEDIATION_READ_TOKEN"),
    )
    return RemediationAttemptRepositoryV1(approvals, binding=binding, provider=provider)


def executor_main() -> None:
    repo = configured_attempts(approvals_from_environment())
    if repo.binding is None:
        raise ValueError("executor state binding unavailable")
    executor = ProductPaymentConfigurationRollbackExecutor(
        repo,
        UnixPaymentRestoreClientV1(
            Path(os.environ["ECOMSRE_REMEDIATION_WRITE_SOCKET"]),
            required_secret("ECOMSRE_REMEDIATION_WRITE_TOKEN"),
        ),
    )
    recovery = RecoveryRepositoryV1(repo)
    recovery.bind_policy(
        RecoveryPolicyV1.model_validate_json(
            Path(os.environ["ECOMSRE_REMEDIATION_POLICY_PATH"]).read_bytes()
        )
    )
    windows = UnixRecoveryWindowClientV1(
        Path(os.environ["ECOMSRE_REMEDIATION_READ_SOCKET"]),
        required_secret("ECOMSRE_REMEDIATION_WINDOW_TOKEN"),
    )
    while True:
        with repo.store.connect() as connection:
            rows = connection.execute(
                "SELECT attempt_id, state, payload_json FROM remediation_attempts WHERE terminal IS NULL ORDER BY rowid"
            ).fetchall()
        for row in rows:
            attempt = repo.get(row["attempt_id"])
            try:
                if attempt.state.value == "AUTHORIZED":
                    if (
                        attempt.lease_expires_at is not None
                        and datetime.now(UTC) < attempt.lease_expires_at
                    ):
                        continue
                    attempt = executor.run_one(attempt.attempt_id)
                elif attempt.state.value in {"WRITE_INTENT_COMMITTED", "EXECUTING"}:
                    if (
                        attempt.lease_expires_at is not None
                        and datetime.now(UTC) < attempt.lease_expires_at
                    ):
                        continue
                    # This process did not commit that intent in its current call.
                    # Never dispatch it, regardless of the readback result.
                    repo._capture()
                    executor.mark_unknown(attempt.attempt_id)
                    continue
                if (
                    attempt.state.value == "VERIFYING"
                    and attempt.lease_expires_at is not None
                    and datetime.now(UTC) < attempt.lease_expires_at
                ):
                    continue
                if attempt.state.value in {"APPLIED", "VERIFYING"}:
                    recovery.verify(attempt.attempt_id, windows)
            except Exception:
                # Fail the process closed. Supervisor restart may only reconcile.
                raise RuntimeError(
                    "REMEDIATION_EXECUTOR_REQUIRES_RECONCILIATION"
                ) from None
        time.sleep(0.25)


class WindowRequestV1(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    started_after: datetime
    policy_sha256: str


def gateway_main() -> None:
    import hmac

    profile = PrivatePaymentControlProfileV1.model_validate_json(
        Path(os.environ["ECOMSRE_REMEDIATION_PRIVATE_PROFILE"]).read_bytes()
    )
    witness_key = required_secret("ECOMSRE_REMEDIATION_OBSERVER_TOKEN")
    state = LocalPaymentStateProviderV1(profile, witness_key)
    attempts = RemediationAttemptRepositoryV1(
        approvals_from_environment(), provider=state, binding=profile.binding
    )
    control = GuardedPaymentControlV1(
        attempts, state, Path(os.environ["ECOMSRE_REMEDIATION_CONTROL_LEDGER"])
    )
    read_token = required_secret("ECOMSRE_REMEDIATION_READ_TOKEN")
    read_app, write_app = control_apps(
        control,
        read_token=read_token,
        write_token=required_secret("ECOMSRE_REMEDIATION_WRITE_TOKEN"),
    )
    policy = RecoveryPolicyV1.model_validate_json(
        Path(os.environ["ECOMSRE_REMEDIATION_POLICY_PATH"]).read_bytes()
    )
    observer: RecoveryWindowProviderV1 = SignedRecoveryWindowProviderV1(
        Path(os.environ["ECOMSRE_REMEDIATION_RECOVERY_WITNESS"]), witness_key
    )

    if request_root := os.environ.get("ECOMSRE_REMEDIATION_WINDOW_REQUESTS"):
        from ecomsre.product.remediation.window_requests import (
            RequestedRecoveryWindowProviderV1,
        )

        observer = RequestedRecoveryWindowProviderV1(
            RecoveryRepositoryV1(attempts),
            Path(request_root),
            Path(os.environ["ECOMSRE_REMEDIATION_WINDOW_RESPONSES"]),
            witness_key,
        )

    window_token = required_secret("ECOMSRE_REMEDIATION_WINDOW_TOKEN")

    @read_app.post("/recovery-window")
    def recovery_window(
        body: WindowRequestV1, authorization: str | None = Header(default=None)
    ) -> RecoveryObservationV1:
        if authorization is None or not hmac.compare_digest(
            authorization, "Bearer " + window_token.get_secret_value()
        ):
            raise HTTPException(status_code=403, detail="CONTROL_AUTHORIZATION_DENIED")
        if (
            body.policy_sha256 != policy.policy_sha256
            or body.started_after.tzinfo is None
        ):
            raise HTTPException(status_code=409, detail="RECOVERY_POLICY_MISMATCH")
        try:
            return observer.acquire(started_after=body.started_after, policy=policy)
        except Exception:
            raise HTTPException(
                status_code=409, detail="RECOVERY_OBSERVATION_UNAVAILABLE"
            ) from None

    read_socket = Path(os.environ["ECOMSRE_REMEDIATION_READ_SOCKET"])
    write_socket = Path(os.environ["ECOMSRE_REMEDIATION_WRITE_SOCKET"])
    for path in (read_socket, write_socket):
        if path.exists() or path.is_symlink():
            raise ValueError(
                "control socket already exists; ownership reconciliation required"
            )
    if port := os.environ.get("ECOMSRE_REMEDIATION_READ_PROXY_PORT"):
        from ecomsre.product.remediation.observation_proxy import (
            ObservationProxyProfileV1,
            mount_observation_proxy,
        )

        proxy_profile = ObservationProxyProfileV1.model_validate_json(
            Path("/run/remediation-private/observation-proxy.json").read_bytes()
        )
        mount_observation_proxy(read_app, proxy_profile)
        proxy_thread = threading.Thread(
            target=uvicorn.run,
            kwargs={
                "app": read_app,
                "host": "0.0.0.0",
                "port": int(port),
                "access_log": False,
                "log_level": "critical",
            },
            daemon=True,
        )
        proxy_thread.start()
    thread = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": read_app,
            "uds": str(read_socket),
            "access_log": False,
            "log_level": "critical",
        },
        daemon=True,
    )
    thread.start()
    uvicorn.run(
        write_app, uds=str(write_socket), access_log=False, log_level="critical"
    )


def observation_proxy_app() -> FastAPI:
    """Bootstrap has observation routes only and loads no write authority."""
    from ecomsre.product.remediation.observation_proxy import (
        ObservationProxyProfileV1,
        mount_observation_proxy,
    )

    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    profile = ObservationProxyProfileV1.model_validate_json(
        Path("/run/remediation-private/observation-proxy.json").read_bytes()
    )
    mount_observation_proxy(app, profile)
    return app


def observation_proxy_main() -> None:
    uvicorn.run(
        observation_proxy_app(),
        host="0.0.0.0",
        port=8081,
        access_log=False,
        log_level="critical",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "role", choices=("executor", "control-gateway", "observation-proxy")
    )
    role = parser.parse_args().role
    if os.environ.get("ECOMSRE_REMEDIATION_ENABLED") != "1":
        raise SystemExit("REMEDIATION_DISABLED")
    if (
        os.environ.get("ECOMSRE_REMEDIATION_ISOLATED_PROFILE")
        != "product-v040-network-v1"
    ):
        raise SystemExit("REMEDIATION_ISOLATED_PROFILE_REQUIRED")
    try:
        {
            "executor": executor_main,
            "control-gateway": gateway_main,
            "observation-proxy": observation_proxy_main,
        }[role]()
    except Exception:
        raise SystemExit("REMEDIATION_PROCESS_FAILED_CLOSED") from None


if __name__ == "__main__":
    main()
