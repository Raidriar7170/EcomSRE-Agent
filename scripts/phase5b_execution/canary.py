"""Create-once public Provider health canary for Phase 5B execution."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Mapping

from scripts.phase5b_execution.admission import (
    require_merged_execution_source,
    require_provider_configuration,
    require_scored_execution_authorization,
    provider_configuration_fingerprint,
    safe_execution_environment,
)
from scripts.phase5b_execution.checkpoint import (
    _atomic_create,
    _ensure_private_directory,
    _entry_exists,
    _load_canonical,
)
from scripts.phase5b_execution.contracts import (
    ExecutionAttemptMarker,
    PROVIDER_CANARY_RUN_ID,
    ProviderCanaryRecord,
    RawScoredRunRecord,
    ScoredRunRequest,
    canonical_json_bytes,
)
from scripts.phase5b_execution.worker import IsolatedCanaryExecutor


CANARY_RECORD = Path("state/provider-canary-record.json")
CANARY_ATTEMPT = Path("state/provider-canary-attempt.json")
CANARY_RAW_RECORD = Path("state/provider-canary-raw.json")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record_from_raw(
    raw: RawScoredRunRecord,
    provider_configuration_sha256: str,
) -> ProviderCanaryRecord:
    return ProviderCanaryRecord(
        schema_version="phase5b.provider-canary-record.v1",
        evaluation_version="phase5b.v1",
        public_template_id="ad-partial-failure-complete",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
        terminal_status=raw.terminal_status,
        raw_record_sha256=raw.record_sha256,
        provider_configuration_sha256=provider_configuration_sha256,
        provider_network_calls=raw.usage.provider_network_calls,
        model_calls=raw.usage.model_calls,
        input_tokens=raw.usage.input_tokens,
        output_tokens=raw.usage.output_tokens,
        total_tokens=raw.usage.total_tokens,
        provider_usage_known=raw.usage.provider_usage_known,
        typed_protocol_pass=(
            raw.terminal_status.value == "COMPLETED"
            and raw.usage.provider_network_calls == 1
            and raw.usage.model_calls >= 1
            and raw.usage.provider_usage_known
        ),
        no_retry=True,
        scripted_fallback=False,
    )


def _require_canary_raw_identity(raw: RawScoredRunRecord) -> None:
    raw.verify_record_sha256()
    if (
        raw.run_id != PROVIDER_CANARY_RUN_ID
        or raw.template_id != "ad-partial-failure-complete"
        or raw.seed_id != "seed-00"
        or raw.variant != "SINGLE_AGENT_V2"
        or raw.evidence_class != "UNSCORED_PROVIDER_CANARY"
        or not raw.provider_attempted
        or raw.usage.provider_network_calls != 1
    ):
        raise ValueError("Provider canary raw record differs from the frozen request")


def verify_canary_chain(
    execution_root: Path,
    *,
    expected_provider_configuration_sha256: str | None = None,
) -> ProviderCanaryRecord:
    """Verify the create-once canary summary against its raw evidence."""

    summary = _load_canonical(
        execution_root / CANARY_RECORD,
        ProviderCanaryRecord,
    )
    raw = _load_canonical(
        execution_root / CANARY_RAW_RECORD,
        RawScoredRunRecord,
    )
    _require_canary_raw_identity(raw)
    expected = _record_from_raw(raw, summary.provider_configuration_sha256)
    if summary != expected or summary.raw_record_sha256 != raw.record_sha256:
        raise ValueError("Provider canary summary does not bind its raw record")
    if (
        expected_provider_configuration_sha256 is not None
        and summary.provider_configuration_sha256
        != expected_provider_configuration_sha256
    ):
        raise ValueError("Provider configuration differs from the successful canary")
    if _entry_exists(execution_root / CANARY_ATTEMPT):
        raise ValueError("Provider canary still has an open attempt marker")
    return summary


def run_provider_canary(
    *,
    project_root: Path,
    execution_root: Path,
    environment: Mapping[str, str],
) -> ProviderCanaryRecord:
    """Run at most one public, unscored Provider call and persist its disposition."""

    existing_path = execution_root / CANARY_RECORD
    require_scored_execution_authorization(environment)
    require_merged_execution_source(project_root)
    config = require_provider_configuration(environment)
    config_sha256 = provider_configuration_fingerprint(config)
    _ensure_private_directory(execution_root)
    _ensure_private_directory((execution_root / CANARY_RECORD).parent)
    if _entry_exists(existing_path):
        return verify_canary_chain(
            execution_root,
            expected_provider_configuration_sha256=config_sha256,
        )
    raw_path = execution_root / CANARY_RAW_RECORD
    marker_path = execution_root / CANARY_ATTEMPT
    if _entry_exists(raw_path):
        raw = _load_canonical(raw_path, RawScoredRunRecord)
        _require_canary_raw_identity(raw)
        marker = _load_canonical(marker_path, ExecutionAttemptMarker)
        request = ScoredRunRequest(
            run_id=PROVIDER_CANARY_RUN_ID,
            template_id="ad-partial-failure-complete",
            seed_id="seed-00",
            variant="SINGLE_AGENT_V2",
        )
        marker_config_sha256 = marker.provider_configuration_sha256
        if (
            marker.run_id != request.run_id
            or marker.request_sha256 != request.request_sha256()
            or marker.evidence_class != "UNSCORED_PROVIDER_CANARY"
            or marker_config_sha256 is None
            or marker_config_sha256 != config_sha256
        ):
            raise ValueError("Provider canary recovery marker is invalid")
        canary = _record_from_raw(
            raw,
            marker_config_sha256,
        )
        _atomic_create(
            existing_path,
            canonical_json_bytes(canary.model_dump(mode="json")),
        )
        marker_path.unlink(missing_ok=True)
        _fsync_directory(marker_path.parent)
        return verify_canary_chain(
            execution_root,
            expected_provider_configuration_sha256=config_sha256,
        )
    if _entry_exists(marker_path):
        raise RuntimeError("Provider canary was interrupted and cannot be retried")
    request = ScoredRunRequest(
        run_id=PROVIDER_CANARY_RUN_ID,
        template_id="ad-partial-failure-complete",
        seed_id="seed-00",
        variant="SINGLE_AGENT_V2",
    )
    marker = ExecutionAttemptMarker(
        run_id=request.run_id,
        request_sha256=request.request_sha256(),
        evidence_class="UNSCORED_PROVIDER_CANARY",
        provider_configuration_sha256=config_sha256,
        attempt_number=1,
        state="EXECUTION_ATTEMPT_STARTED",
        started_at_utc=datetime.now(timezone.utc),
    )
    _atomic_create(marker_path, marker.canonical_bytes())
    sanitized = safe_execution_environment(environment)
    raw = IsolatedCanaryExecutor(
        project_root=project_root,
        environment=sanitized,
    )(request)
    _require_canary_raw_identity(raw)
    _atomic_create(raw_path, raw.canonical_bytes())
    require_merged_execution_source(project_root)
    canary = _record_from_raw(raw, config_sha256)
    _atomic_create(
        existing_path,
        canonical_json_bytes(canary.model_dump(mode="json")),
    )
    marker_path.unlink()
    _fsync_directory(marker_path.parent)
    return verify_canary_chain(
        execution_root,
        expected_provider_configuration_sha256=config_sha256,
    )
