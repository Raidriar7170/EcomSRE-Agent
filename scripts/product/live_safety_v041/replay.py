"""Fixed duplicate wake-up in the existing isolated executor container."""

from ecomsre.product.errors import ProductError
import json
import os
from pathlib import Path
from ecomsre.product.remediation.runtime import (
    configured_attempts,
    approvals_from_environment,
)
from ecomsre.product.remediation.executor import (
    ProductPaymentConfigurationRollbackExecutor,
)
from ecomsre.product.remediation.payment_control import (
    UnixPaymentRestoreClientV1,
    required_secret,
)

repo = configured_attempts(approvals_from_environment())
with repo.store.connect() as db:
    rows = db.execute("SELECT attempt_id FROM remediation_attempts").fetchall()
if len(rows) != 1:
    raise ValueError("REPLAY_REQUIRES_ONE_ATTEMPT")
executor = ProductPaymentConfigurationRollbackExecutor(
    repo,
    UnixPaymentRestoreClientV1(
        Path(os.environ["ECOMSRE_REMEDIATION_WRITE_SOCKET"]),
        required_secret("ECOMSRE_REMEDIATION_WRITE_TOKEN"),
    ),
)

try:
    outcome = executor.run_one(rows[0][0]).state.value
except ProductError as error:
    outcome = error.code
print(
    json.dumps(
        {
            "duplicate_wakeup_result": outcome,
            "actual_run_one_calls": 1,
            "terminal_after": repo.get(rows[0][0]).state.value,
        }
    )
)
