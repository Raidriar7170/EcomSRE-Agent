#!/usr/bin/env python3
"""Run the fresh DTA v2.3.4.1 Provider smoke exactly once."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.provider_smoke_v2341 import (
    RegistrationSmokeModeV2341,
    load_smoke_tasks_v2341,
    load_smoke_truth_v2341,
    run_provider_smoke_v2341,
    verify_smoke_surface_v2341,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    OpenAICompatibleRegistrationAliasTransportV2341,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def _write_private_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument(
        "--execute-once",
        required=True,
        choices=("DTA_V2341_SMOKE_PREFLIGHT_PASS",),
    )
    parser.add_argument("--minimum-request-interval", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    root = args.repository_root.resolve()
    smoke_root = root / "config/dta-v2341/smoke"
    manifest_path = smoke_root / "manifest.json"
    output_path = root / "docs/analysis/dta-v2341-provider-smoke.json"
    blocker_path = root / "docs/analysis/dta-v2341-provider-smoke-blocker.json"
    local_root = root / ".local/dta-v2341"
    sentinel = local_root / "provider-smoke.started.json"
    complete = local_root / "provider-smoke.complete.json"
    if output_path.exists() or blocker_path.exists() or sentinel.exists() or complete.exists():
        raise FileExistsError("v2.3.4.1 Provider smoke was already started")

    values = load_private_provider_env(args.provider_env)
    config = OpenAICompatibleConfig(
        base_url=values["ECOMSRE_LLM_BASE_URL"],
        api_key=values["ECOMSRE_LLM_API_KEY"],
        model=values["ECOMSRE_LLM_MODEL"],
    )
    manifest = verify_smoke_surface_v2341(
        repository_root=root,
        manifest_path=manifest_path,
        expected_provider_model=config.model,
    )
    tasks = load_smoke_tasks_v2341(smoke_root / "tasks.json")
    truths = load_smoke_truth_v2341(smoke_root / "truth.json")
    preflight = run_provider_smoke_v2341(
        repository_root=root,
        task_set=tasks,
        truth_set=truths,
        mode=RegistrationSmokeModeV2341.DETERMINISTIC_FIXTURE,
    )
    if preflight.terminal != args.execute_once:
        raise ValueError("v2.3.4.1 smoke deterministic preflight differs")
    manifest_file_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    _write_private_once(
        sentinel,
        {
            "schema_version": "dta-v2341.provider-smoke-sentinel.v1",
            "status": "STARTED",
            "execution_count": 1,
            "task_count": 8,
            "provider_called_task_count": 6,
            "manifest_sha256": manifest.manifest_sha256,
            "manifest_file_sha256": manifest_file_sha256,
            "preflight_sha256": preflight.artifact_sha256,
        },
    )
    transport = OpenAICompatibleRegistrationAliasTransportV2341(
        config=config,
        minimum_request_interval_seconds=args.minimum_request_interval,
        timeout_seconds=args.timeout,
        raw_artifact_dir=local_root / "provider-raw/smoke",
    )
    try:
        artifact = run_provider_smoke_v2341(
            repository_root=root,
            task_set=tasks,
            truth_set=truths,
            mode=RegistrationSmokeModeV2341.OPENAI_COMPATIBLE,
            transport=transport,
        )
    except Exception as exc:
        blocker_payload = {
            "schema_version": "dta-v2341.provider-smoke-blocker.v1",
            "terminal": "BLOCKED_DTA_V2341_PROVIDER_SMOKE",
            "execution_count": 1,
            "manifest_sha256": manifest.manifest_sha256,
            "provider_model": config.model,
            "safe_exception_type": type(exc).__name__,
            "safe_error": str(exc),
            "fixed_evaluation_execution_count": 0,
            "action_authority_violations": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "raw_provider_artifacts_scope": ".local/dta-v2341/provider-raw/smoke",
        }
        blocker_payload["blocker_sha256"] = semantic_sha256_v22(blocker_payload)
        with blocker_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(blocker_payload, sort_keys=True, indent=2) + "\n")
        raise
    evidence_payload = {
        "schema_version": "dta-v2341.provider-smoke-evidence.v1",
        "status": artifact.terminal,
        "execution_count": artifact.execution_count,
        "task_count": artifact.task_count,
        "provider_called_task_count": artifact.provider_called_task_count,
        "manifest_sha256": manifest.manifest_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "provider_model": config.model,
        "provider_call_count": artifact.provider_call_count,
        "protocol_repair_count": artifact.protocol_repair_count,
        "transport_retry_count": artifact.transport_retry_count,
        "input_tokens": transport.input_tokens,
        "output_tokens": transport.output_tokens,
        "total_tokens": transport.total_tokens,
        "latency_ms": transport.latency_ms,
        "smoke": artifact.model_dump(mode="json"),
        "fixed_evaluation_execution_count": 0,
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "remediation_registrations": 0,
        "raw_provider_artifacts_scope": ".local/dta-v2341/provider-raw/smoke",
    }
    evidence_payload["evidence_sha256"] = semantic_sha256_v22(evidence_payload)
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(evidence_payload, sort_keys=True, indent=2) + "\n")
    _write_private_once(
        complete,
        {
            "schema_version": "dta-v2341.provider-smoke-complete.v1",
            "status": artifact.terminal,
            "execution_count": 1,
            "manifest_sha256": manifest.manifest_sha256,
            "evidence_sha256": evidence_payload["evidence_sha256"],
            "output_file_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        },
    )
    print(
        json.dumps(
            {
                "status": artifact.terminal,
                "execution_count": 1,
                "provider_calls": artifact.provider_call_count,
                "protocol_repairs": artifact.protocol_repair_count,
                "transport_retries": artifact.transport_retry_count,
                "evidence_sha256": evidence_payload["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
