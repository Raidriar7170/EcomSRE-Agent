"""Read-only acquisition from signed independent observer windows."""

from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import time

import httpx
from pydantic import SecretStr

from ecomsre.product.remediation.execution_contracts import (
    RecoveryObservationV1,
    RecoveryPolicyV1,
)


class SignedRecoveryWindowProviderV1:
    """The observer owns business probing; the gateway verifies its signed capsule."""

    def __init__(self, path: Path, observer_key: SecretStr) -> None:
        self.path = path
        self._key = observer_key

    def acquire(
        self, *, started_after: datetime, policy: RecoveryPolicyV1
    ) -> RecoveryObservationV1:
        deadline = time.monotonic() + policy.window_seconds * 3 + 30
        while time.monotonic() < deadline:
            try:
                if self.path.is_symlink():
                    raise ValueError("observer evidence unavailable")
                envelope = json.loads(self.path.read_bytes())
                if set(envelope) != {"observation", "signature"}:
                    raise ValueError("observer envelope differs")
                raw = json.dumps(
                    envelope["observation"], sort_keys=True, separators=(",", ":")
                )
                signature = hmac.new(
                    self._key.get_secret_value().encode(), raw.encode(), hashlib.sha256
                ).hexdigest()
                if not hmac.compare_digest(signature, envelope["signature"]):
                    raise ValueError("observer signature differs")
                observation = RecoveryObservationV1.model_validate(
                    envelope["observation"]
                )
                if (
                    observation.policy_sha256 != policy.policy_sha256
                    or observation.environment_id != policy.environment_id
                ):
                    raise ValueError("observer policy differs")
                if (
                    started_after
                    < observation.started_at
                    < observation.ended_at
                    <= datetime.now(UTC)
                    and (observation.ended_at - observation.started_at).total_seconds()
                    == policy.window_seconds
                ):
                    return observation
            except (OSError, ValueError, TypeError, KeyError):
                pass
            time.sleep(0.1)
        raise ValueError("fresh independent recovery observation unavailable")


class UnixRecoveryWindowClientV1:
    def __init__(self, socket: Path, token: SecretStr) -> None:
        self._token = token
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket), retries=0),
            base_url="http://control",
            timeout=950,
            trust_env=False,
            follow_redirects=False,
        )

    def acquire(
        self, *, started_after: datetime, policy: RecoveryPolicyV1
    ) -> RecoveryObservationV1:
        response = self.client.post(
            "/recovery-window",
            json={
                "started_after": started_after.isoformat(),
                "policy_sha256": policy.policy_sha256,
            },
            headers={"Authorization": "Bearer " + self._token.get_secret_value()},
        )
        response.raise_for_status()
        return RecoveryObservationV1.model_validate_json(response.content)
