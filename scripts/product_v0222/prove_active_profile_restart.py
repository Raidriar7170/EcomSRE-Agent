"""Prove that a fresh consumer process reloads the active v0.2.2.2 profile.

This proof is deliberately offline.  The child constructs the configured
connector and reads its static capabilities, but it does not issue a network
request or repeat the consumed live smoke.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OpenSearchNormalizationProfileV0222,
    OpenSearchProfileStatusV0222,
)
from ecomsre.product.connectors.opensearch_smoke_v0222 import (
    CONNECTOR_SMOKE_PASS_V0222,
    OpenSearchConnectorSmokeProfileV0222,
    OpenSearchConnectorSmokeReportV0222,
)


RESTART_PROOF_PASS_V0222 = (
    "ECOMSRE_PRODUCT_V0222_ACTIVE_PROFILE_RESTART_PROOF_PASS"
)
_CHILD_RELOADED_V0222 = (
    "ECOMSRE_PRODUCT_V0222_ACTIVE_PROFILE_CONSUMER_RELOADED"
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _load_child_payload(root: Path) -> dict[str, object]:
    repository = root.resolve(strict=True)
    active_path = (
        repository / "config/product-v0222/opensearch/normalization-profile.json"
    )
    smoke_profile_path = (
        repository / "config/product-v0222/opensearch/smoke-profile.json"
    )
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        active_path.read_text(encoding="utf-8")
    )
    smoke_profile = OpenSearchConnectorSmokeProfileV0222.model_validate_json(
        smoke_profile_path.read_text(encoding="utf-8")
    )
    if (
        active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
        or smoke_profile.active_profile_sha256 != active.profile_sha256
    ):
        raise ValueError("Product v0.2.2.2 child profile binding differs")

    connector = OpenSearchConnectorV1(
        smoke_profile.connector_config,
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=5,
    )
    try:
        capabilities = tuple(
            item.model_dump(mode="json") for item in connector.capabilities()
        )
    finally:
        connector.close()
    connector_config = smoke_profile.connector_config.model_dump(mode="json")
    capability_body = {
        "schema_version": (
            "ecomsre.product.opensearch-reloaded-capabilities.v0222"
        ),
        "connector_name": smoke_profile.connector_config.name,
        "capabilities": capabilities,
    }
    return {
        "terminal": _CHILD_RELOADED_V0222,
        "child_pid": os.getpid(),
        "active_profile_sha256": active.profile_sha256,
        "active_profile_file_sha256": _file_sha256(active_path),
        "smoke_profile_sha256": smoke_profile.smoke_profile_sha256,
        "smoke_profile_file_sha256": _file_sha256(smoke_profile_path),
        "connector_config_sha256": semantic_sha256_v22(connector_config),
        "reloaded_capabilities_sha256": semantic_sha256_v22(capability_body),
        "network_request_count": 0,
    }


def run_active_profile_restart_proof_v0222(
    root: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, object]:
    repository = root.resolve(strict=True)
    smoke = OpenSearchConnectorSmokeReportV0222.model_validate_json(
        (
            repository / "docs/analysis/product-v0222-connector-smoke.json"
        ).read_text(encoding="utf-8")
    )
    if smoke.terminal != CONNECTOR_SMOKE_PASS_V0222:
        raise ValueError("Product v0.2.2.2 restart proof requires smoke PASS")

    child_environment = os.environ.copy()
    inherited_pythonpath = child_environment.get("PYTHONPATH")
    child_pythonpath: tuple[str, ...] = (
        str(repository),
        str(repository / "src"),
    )
    if inherited_pythonpath:
        child_pythonpath = (*child_pythonpath, inherited_pythonpath)
    child_environment["PYTHONPATH"] = os.pathsep.join(child_pythonpath)
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.product_v0222.prove_active_profile_restart",
            "--project-root",
            str(repository),
            "--child",
        ),
        cwd=repository,
        env=child_environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip().splitlines()
        suffix = "" if not diagnostic else f": {diagnostic[-1][:240]}"
        raise RuntimeError(f"Product v0.2.2.2 restart child failed{suffix}")
    try:
        child = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Product v0.2.2.2 restart child output differs") from error
    if not isinstance(child, dict):
        raise RuntimeError("Product v0.2.2.2 restart child payload differs")
    parent_pid = os.getpid()
    if (
        child.get("terminal") != _CHILD_RELOADED_V0222
        or not isinstance(child.get("child_pid"), int)
        or child["child_pid"] == parent_pid
        or child.get("active_profile_sha256") != smoke.active_profile_sha256
        or child.get("active_profile_file_sha256")
        != smoke.active_profile_file_sha256_after
        or child.get("smoke_profile_sha256") != smoke.smoke_profile_sha256
        or child.get("network_request_count") != 0
    ):
        raise RuntimeError("Product v0.2.2.2 restart child binding differs")

    proof: dict[str, object] = {
        "schema_version": "ecomsre.product.active-profile-restart-proof.v0222",
        "terminal": RESTART_PROOF_PASS_V0222,
        "process_relation": "DISTINCT_CONSUMER_PROCESS",
        "parent_pid": parent_pid,
        "child_pid": child["child_pid"],
        "child_payload_sha256": semantic_sha256_v22(child),
        "active_profile_sha256": child["active_profile_sha256"],
        "active_profile_file_sha256": child["active_profile_file_sha256"],
        "smoke_profile_sha256": child["smoke_profile_sha256"],
        "smoke_profile_file_sha256": child["smoke_profile_file_sha256"],
        "connector_config_sha256": child["connector_config_sha256"],
        "reloaded_capabilities_sha256": child[
            "reloaded_capabilities_sha256"
        ],
        "connector_smoke_sha256": smoke.smoke_sha256,
        "live_opensearch_capability_sha256": (
            smoke.opensearch_capability_sha256
        ),
        "network_request_count": 0,
        "live_smoke_rerun_count": 0,
        "action_authority": "NONE",
    }
    proof["proof_sha256"] = semantic_sha256_v22(proof)
    destination = output_path or (
        repository
        / "docs/analysis/product-v0222-active-profile-restart-proof.json"
    )
    _write_once(destination, proof)
    return proof


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args(argv)
    payload: dict[str, Any]
    if args.child:
        payload = _load_child_payload(args.project_root)
    else:
        payload = run_active_profile_restart_proof_v0222(args.project_root)
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "RESTART_PROOF_PASS_V0222",
    "run_active_profile_restart_proof_v0222",
)
