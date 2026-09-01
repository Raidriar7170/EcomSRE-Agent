#!/usr/bin/env python3
"""Project reached Product v0.2.3.3 formal evidence into public paths.

This is a post-terminal administrative operation.  It never starts Docker,
re-enters the formal runner, or creates Incident, Diagnosis, or measured output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Sequence

from ecomsre.product.pilot.formal_live_v0233 import (
    BaselineRestartProofV0233,
    FormalExecutionBlockerV0233,
    FormalTrafficResultV0233,
    RuntimeAuthorityProofV0233,
)


_PRIVATE_ROOT = Path(".local/product-v0233/formal-execution")
_PUBLIC_ROOT = Path("docs/analysis")
_EXPECTED_FILE_SHA256 = {
    "runtime-authority.json": (
        "41134e13794946f8839cbd457cac444047ce26ac3719b1d55ab92325738441c1"
    ),
    "baseline-restart.json": (
        "3b07e99c3fa7f5bd57b43dff6ffaa3a9bca00a489204fb9eb57498099da5ac0f"
    ),
    "formal-traffic.json": (
        "44ea769a91b49df6d69f0def28c43c1938cba7a499432faaa57d478c9c12d55d"
    ),
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _project_exact_file(
    *,
    source: Path,
    target: Path,
    expected_sha256: str,
    validator: Callable[[bytes], object],
) -> str:
    """Create one exact projection, accepting only identical recovery bytes."""

    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Product v0.2.3.3 private source is invalid: {source.name}")
    payload = source.read_bytes()
    observed_sha256 = _sha256_bytes(payload)
    if observed_sha256 != expected_sha256:
        raise ValueError(f"Product v0.2.3.3 source SHA-256 differs: {source.name}")
    validator(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if (
            target.is_symlink()
            or not target.is_file()
            or target.read_bytes() != payload
        ):
            raise FileExistsError(
                f"Product v0.2.3.3 public artifact differs: {target.name}"
            ) from None
        return observed_sha256
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return observed_sha256


def project_blocked_evidence_v0233(root: Path) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    blocker = FormalExecutionBlockerV0233.model_validate_json(
        (project / _PUBLIC_ROOT / "product-v0233-formal-blocker.json").read_bytes()
    )
    if (
        blocker.terminal != "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
        or blocker.failure_stage != "FORMAL_TRAFFIC_PASS"
        or blocker.safe_error_code != "TypeError:FORMAL_TRAFFIC_PASS"
        or blocker.formal_execution_count != 1
        or blocker.new_incident_count != 0
        or blocker.new_diagnosis_count != 0
        or blocker.measured_result_count != 0
    ):
        raise ValueError("Product v0.2.3.3 blocker projection gate differs")

    validators: dict[str, Callable[[bytes], object]] = {
        "runtime-authority.json": RuntimeAuthorityProofV0233.model_validate_json,
        "baseline-restart.json": BaselineRestartProofV0233.model_validate_json,
        "formal-traffic.json": FormalTrafficResultV0233.model_validate_json,
    }
    projected: dict[str, str] = {}
    for private_name, expected_sha256 in _EXPECTED_FILE_SHA256.items():
        public_name = f"product-v0233-{private_name}"
        projected[public_name] = _project_exact_file(
            source=project / _PRIVATE_ROOT / private_name,
            target=project / _PUBLIC_ROOT / public_name,
            expected_sha256=expected_sha256,
            validator=validators[private_name],
        )

    authority = RuntimeAuthorityProofV0233.model_validate_json(
        (project / _PUBLIC_ROOT / "product-v0233-runtime-authority.json").read_bytes()
    )
    restart = BaselineRestartProofV0233.model_validate_json(
        (project / _PUBLIC_ROOT / "product-v0233-baseline-restart.json").read_bytes()
    )
    traffic = FormalTrafficResultV0233.model_validate_json(
        (project / _PUBLIC_ROOT / "product-v0233-formal-traffic.json").read_bytes()
    )
    if (
        authority.admission_sha256 != blocker.admission_sha256
        or restart.admission_sha256 != blocker.admission_sha256
        or traffic.admission_sha256 != blocker.admission_sha256
        or traffic.execution.run.successful_transactions != 30
        or traffic.execution.run.failed_transactions != 0
        or traffic.execution.run.transport_retry_count != 0
        or traffic.monotonic_duration_ms < 300_000
    ):
        raise ValueError("Product v0.2.3.3 projected evidence binding differs")
    return {
        "terminal": blocker.terminal,
        "failure_stage": blocker.failure_stage,
        "projected_file_sha256": projected,
        "formal_transaction_count": traffic.execution.run.successful_transactions,
        "new_incident_count": blocker.new_incident_count,
        "new_diagnosis_count": blocker.new_diagnosis_count,
        "measured_result_count": blocker.measured_result_count,
        "action_authority": blocker.action_authority,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    print(
        json.dumps(
            project_blocked_evidence_v0233(arguments.project_root), sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("project_blocked_evidence_v0233",)
