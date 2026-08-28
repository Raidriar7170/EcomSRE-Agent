"""Single bounded live OpenSearch schema session for Product v0.2.2.1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping

import httpx

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchHttpErrorV0221,
    OpenSearchProbeClientV0221,
)
from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
    normalize_opensearch_search_v022,
)
from ecomsre.product.connectors.opensearch_probe_execution_v0221 import (
    OpenSearchProbeExecutionV0221,
    execute_probe_protocol_v0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchProbeProtocolBlockerV0221,
)
from ecomsre.product.connectors.opensearch_probe_session_v0221 import (
    OFFLINE_PARSER_BLOCKED_V0221,
    OFFLINE_PARSER_PASS_V0221,
    SCHEMA_DISCOVERY_PASS_V0221,
    OpenSearchOfflineParserReportV0221,
    OpenSearchSanitizedFixtureV0221,
    build_sanitized_live_fixture_v0221,
    evaluate_offline_parser_v0221,
    load_schema_session_profile_v0221,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchBatchStatusV022,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre_live_sandbox.contracts import ensure_private_directory
from ecomsre_live_sandbox.environment import ExactCommandRunner
from scripts.ci.verify_product_v0221_increment2 import (
    verify_product_v0221_increment2,
)


PINNED_UPSTREAM_V0221 = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
REQUEST_PROTOCOL_BLOCKED_V0221 = (
    "BLOCKED_ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL"
)
MAPPING_UNAVAILABLE_V0221 = (
    "BLOCKED_ECOMSRE_PRODUCT_V0221_MAPPING_UNAVAILABLE"
)
LOG_INGESTION_BLOCKED_V0221 = (
    "BLOCKED_ECOMSRE_PRODUCT_V0221_LOG_INGESTION"
)


class LiveSchemaSessionBlockerV0221(RuntimeError):
    def __init__(self, terminal: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.terminal = terminal


def _write_private_bytes_v0221(path: Path, content: bytes) -> str:
    ensure_private_directory(path.parent)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return hashlib.sha256(content).hexdigest()


def _write_private_json_v0221(
    path: Path,
    payload: Mapping[str, object],
) -> str:
    body = dict(payload)
    digest = semantic_sha256_v22(body)
    body["report_sha256"] = digest
    encoded = (
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_private_bytes_v0221(path, encoded)
    return digest


def _write_public_text_v0221(path: Path, content: str) -> None:
    metadata = os.lstat(path.parent)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Product v0.2.2.1 public parent is not a directory")
    temporary = path.parent / f".{path.name}.product-v0221.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        path.chmod(0o644)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.lstat(temporary)
        except FileNotFoundError:
            pass
        else:
            temporary.unlink()


def _require_absent_v0221(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    raise ValueError(f"Product v0.2.2.1 output already exists: {path.name}")


def _service_alias_map_v0221(aliases: tuple[str, ...]) -> dict[str, str]:
    output = {
        alias: "checkout"
        for alias in aliases
        if "checkout" in alias.lower().replace("-", "").replace("_", "")
    }
    output["checkout"] = "checkout"
    return dict(sorted(output.items()))


def _validate_live_sample_v0221(
    *,
    execution: OpenSearchProbeExecutionV0221,
    started_at: datetime,
    ended_at: datetime,
    service_aliases: Mapping[str, str],
) -> dict[str, object]:
    context = ConnectorQueryContextV1(
        environment_id="env-" + "1" * 24,
        requested_services=("checkout",),
        service_aliases=dict(service_aliases),
        window=ConnectorWindowV1(started_at=started_at, ended_at=ended_at),
        maximum_records=5,
        requested_source=EvidenceSourceV22.LOGS,
    )
    batch = normalize_opensearch_search_v022(
        execution.sample_response,
        profile=execution.resolution.profile.as_v022(),
        context=context,
        latency_ms=0.0,
    )
    if (
        batch.status
        not in {OpenSearchBatchStatusV022.SUCCESS_NONEMPTY}
        or batch.accepted_record_count == 0
        or "checkout" not in batch.covered_services
    ):
        raise LiveSchemaSessionBlockerV0221(
            LOG_INGESTION_BLOCKED_V0221,
            "OpenSearch live sample did not validate through the typed parser",
        )
    return {
        "batch_status": batch.status.value,
        "sampled_record_count": batch.sampled_hit_count,
        "accepted_record_count": batch.accepted_record_count,
        "rejected_record_count": batch.rejected_record_count,
        "rejection_codes_by_count": batch.rejection_codes_by_count,
        "batch_sha256": batch.batch_sha256,
    }


def _session_payload_v0221(
    *,
    execution: OpenSearchProbeExecutionV0221,
    session_id: str,
    session_profile_sha256: str,
    fixture: OpenSearchSanitizedFixtureV0221,
    offline: OpenSearchOfflineParserReportV0221,
    private_capture_sha256: str,
    private_sample_response_sha256: str,
    live_sample_validation: Mapping[str, object],
    baseline_unchanged: bool,
    cleanup: str,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.opensearch-schema-session.v0221",
        "terminal": SCHEMA_DISCOVERY_PASS_V0221,
        "session_id": session_id,
        "session_profile_sha256": session_profile_sha256,
        "live_schema_discovery_session_count": 1,
        "changed_request_plan_count": execution.changed_plan_count,
        "total_read_only_opensearch_request_count": execution.request_count,
        "transport_retry_count": execution.transport_retry_count,
        "plans": [plan.model_dump(mode="json") for plan in execution.plans],
        "attempts": [attempt.model_dump(mode="json") for attempt in execution.attempts],
        "safe_http_errors": [
            {
                "http_status": envelope.http_status,
                "safe_error_code": envelope.safe_error_code.value,
                "envelope_sha256": envelope.envelope_sha256,
            }
            for envelope in execution.safe_error_envelopes
        ],
        "mapping_sha256": execution.mapping.mapping_sha256,
        "field_caps_status": execution.resolution.profile.field_caps_status.value,
        "field_caps_sha256": execution.resolution.profile.field_caps_sha256,
        "private_sample_shape_sha256": execution.sample_shapes.sample_shape_sha256,
        "private_sample_response_sha256": private_sample_response_sha256,
        "normalization_profile_id": execution.resolution.profile.profile_id,
        "normalization_profile_sha256": execution.resolution.profile.profile_sha256,
        "sanitized_fixture_sha256": fixture.fixture_sha256,
        "offline_parser_terminal": offline.terminal,
        "offline_parser_report_sha256": offline.report_sha256,
        "live_sample_validation": dict(live_sample_validation),
        "private_capture_sha256": private_capture_sha256,
        "baseline_unchanged": baseline_unchanged,
        "cleanup": cleanup,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    body["report_sha256"] = semantic_sha256_v22(body)
    return body


def _render_session_markdown_v0221(session: Mapping[str, object]) -> str:
    return "\n".join(
        (
            "# Product v0.2.2.1 OpenSearch Schema Session",
            "",
            f"Terminal: `{session['terminal']}`",
            "",
            f"- session: `{session['session_id']}`",
            f"- changed request plans: `{session['changed_request_plan_count']} / 3`",
            f"- read-only OpenSearch requests: `{session['total_read_only_opensearch_request_count']} / 16`",
            f"- transport retries: `{session['transport_retry_count']} / 2`",
            f"- Field Caps: `{session['field_caps_status']}`",
            f"- normalization profile: `{session['normalization_profile_sha256']}`",
            f"- sanitized fixture: `{session['sanitized_fixture_sha256']}`",
            f"- offline parser: `{session['offline_parser_terminal']}`",
            "- fault attempts: `0`",
            "- Baseline Readiness attempts: `0`",
            "- Agent writes / Runbooks: `0 / 0`",
            f"- baseline unchanged: `{str(session['baseline_unchanged']).lower()}`",
            f"- owned cleanup: `{session['cleanup']}`",
            "",
            "Raw Mapping, Field Caps, error bodies, and log sample bytes remain",
            "under the private `.local/product-v0221` evidence root.",
            "",
            f"Report SHA-256: `{session['report_sha256']}`",
            "",
        )
    )


def _progress_payload_v0221(
    *,
    request_protocol_matrix_sha256: str,
    session: Mapping[str, object],
    offline: OpenSearchOfflineParserReportV0221,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.v0221.progress.v1",
        "goal_version": "ecomsre-product-v0221-opensearch-probe-protocol-v1",
        "branch": "codex/product-v0221-opensearch-probe-protocol",
        "increment": 3,
        "terminal": OFFLINE_PARSER_PASS_V0221,
        "request_protocol_terminal": "ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_PASS",
        "schema_discovery_terminal": SCHEMA_DISCOVERY_PASS_V0221,
        "offline_parser_terminal": offline.terminal,
        "next_boundary": "INCREMENT_4_LIVE_CONNECTOR_SMOKE",
        "request_protocol_matrix_sha256": request_protocol_matrix_sha256,
        "schema_session_sha256": session["report_sha256"],
        "normalization_profile_sha256": session[
            "normalization_profile_sha256"
        ],
        "sanitized_fixture_sha256": session["sanitized_fixture_sha256"],
        "offline_parser_report_sha256": offline.report_sha256,
        "live_schema_discovery_session_count": 1,
        "changed_request_plan_count": session["changed_request_plan_count"],
        "total_read_only_opensearch_request_count": session[
            "total_read_only_opensearch_request_count"
        ],
        "transport_retry_count": session["transport_retry_count"],
        "offline_parser_changed_iteration_count": offline.changed_iteration_count,
        "connector_smoke_changed_attempt_count": 0,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "baseline_unchanged": True,
        "cleanup": "CLEAN",
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    body["progress_sha256"] = semantic_sha256_v22(body)
    return body


def run_live_schema_probe_v0221(
    repository_root: Path,
    config_path: Path | None = None,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    increment2 = verify_product_v0221_increment2(root)
    profile = load_schema_session_profile_v0221(
        config_path
        or root / "config/product-v0221/opensearch-probe/profile.json"
    )
    upstream = ExactCommandRunner().run(
        ("git", "rev-parse", "HEAD"),
        cwd=root / "third_party/opentelemetry-demo",
        timeout_seconds=30,
    ).stdout.strip()
    if upstream != PINNED_UPSTREAM_V0221:
        raise ValueError("pinned OTel Demo commit differs")
    private_root = root / profile.private_root
    start_path = private_root / "schema-session-start.json"
    complete_path = private_root / "schema-session-complete.json"
    for path in (
        start_path,
        complete_path,
        root / profile.schema_session_json,
        root / profile.schema_session_markdown,
        root / profile.normalization_profile_path,
        root / profile.sanitized_fixture_path,
        root / profile.offline_parser_report_path,
    ):
        _require_absent_v0221(path)
    ensure_private_directory(private_root)
    session_started_at = datetime.now(UTC)
    _write_private_json_v0221(
        start_path,
        {
            "schema_version": "ecomsre.product.opensearch-schema-session-start.v0221",
            "session_id": profile.session_id,
            "started_at": session_started_at.isoformat(),
            "profile_sha256": profile.profile_sha256,
            "execution_count": 1,
            "maximum_changed_plan_count": 3,
            "maximum_request_count": 16,
            "maximum_transport_retries": 2,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
            "agent_writes": 0,
            "runbook_executions": 0,
        },
    )
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private_root,
        stabilization_seconds=profile.stabilization_seconds,
    )
    cleanup = CleanupObservation.unknown_blocked()
    baseline_before: str | None = None
    baseline_unchanged = False
    probe_client: OpenSearchProbeClientV0221 | None = None
    execution: OpenSearchProbeExecutionV0221 | None = None
    traffic_result: dict[str, object] | None = None
    failure: BaseException | None = None
    try:
        lifecycle.admit()
        lifecycle.start()
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if not isinstance(backend, LocalSandboxReadBackend):
            raise TypeError("owned schema session did not return a local read backend")
        baseline_before = lifecycle.read_baseline_sha256()
        traffic_profile = HealthyTrafficProfileV021.model_validate(
            profile.healthy_traffic_profile.model_dump(mode="json")
        )
        with httpx.Client(trust_env=False) as traffic_client:
            traffic = BoundedHealthyCheckoutTrafficV021(
                client=traffic_client
            ).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=traffic_profile,
            )
        traffic_result = traffic.model_dump(mode="json")
        if (
            traffic.attempted != traffic_profile.maximum_request_count
            or traffic.stopped_on_error_budget
            or traffic.succeeded == 0
        ):
            raise LiveSchemaSessionBlockerV0221(
                LOG_INGESTION_BLOCKED_V0221,
                "bounded healthy checkout traffic did not complete",
            )
        query_ended_at = datetime.now(UTC)
        query_started_at = query_ended_at - timedelta(
            seconds=profile.recent_window_seconds
        )
        probe_client = OpenSearchProbeClientV0221(
            base_url=backend.config.opensearch_base_url,
            maximum_request_count=profile.maximum_request_count,
            maximum_response_bytes=profile.maximum_response_bytes,
        )
        execution = execute_probe_protocol_v0221(
            client=probe_client,
            index_pattern=profile.index_pattern,
            checkout_aliases=profile.checkout_aliases,
            maximum_sample_documents=profile.maximum_sample_documents,
            maximum_transport_retries=profile.maximum_transport_retries,
            started_at=query_started_at,
            ended_at=query_ended_at,
        )
        if (
            execution.changed_plan_count > profile.maximum_changed_plan_count
            or execution.request_count > profile.maximum_request_count
            or execution.transport_retry_count > profile.maximum_transport_retries
        ):
            raise LiveSchemaSessionBlockerV0221(
                REQUEST_PROTOCOL_BLOCKED_V0221,
                "OpenSearch live request ledger exceeded its bound",
            )
        service_aliases = _service_alias_map_v0221(profile.checkout_aliases)
        live_sample_validation = _validate_live_sample_v0221(
            execution=execution,
            started_at=query_started_at,
            ended_at=query_ended_at,
            service_aliases=service_aliases,
        )
        baseline_after = lifecycle.read_baseline_sha256()
        baseline_unchanged = baseline_before == baseline_after
        cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_unchanged)
        if cleanup.verdict != "CLEAN" or not baseline_unchanged:
            raise LiveSchemaSessionBlockerV0221(
                REQUEST_PROTOCOL_BLOCKED_V0221,
                "owned schema-session cleanup or baseline binding is not clean",
            )
        raw_hashes: dict[str, str] = {}
        for name, content in execution.raw_response_bodies:
            raw_hashes[name] = _write_private_bytes_v0221(
                private_root / name,
                content,
            )
        for ordinal, content in enumerate(execution.raw_error_bodies, start=1):
            name = f"http-error-{ordinal}.json"
            raw_hashes[name] = _write_private_bytes_v0221(
                private_root / name,
                content,
            )
        envelopes_sha256 = _write_private_json_v0221(
            private_root / "safe-http-error-envelopes.json",
            {
                "schema_version": (
                    "ecomsre.product.opensearch-safe-http-errors.v0221"
                ),
                "session_id": profile.session_id,
                "envelopes": [
                    envelope.model_dump(mode="json")
                    for envelope in execution.safe_error_envelopes
                ],
            },
        )
        private_sample_response_sha256 = raw_hashes["checkout-sample.json"]
        fixture = build_sanitized_live_fixture_v0221(
            live_response=execution.sample_response,
            profile=execution.resolution.profile,
            private_sample_shape_sha256=execution.sample_shapes.sample_shape_sha256,
            started_at=query_started_at,
            ended_at=query_ended_at,
            service_aliases=service_aliases,
        )
        offline = evaluate_offline_parser_v0221(
            fixture=fixture,
            profile=execution.resolution.profile,
            changed_iteration_count=1,
        )
        if offline.terminal != OFFLINE_PARSER_PASS_V0221:
            raise LiveSchemaSessionBlockerV0221(
                OFFLINE_PARSER_BLOCKED_V0221,
                "sanitized live-shape fixture did not pass offline parsing",
            )
        capture_sha256 = _write_private_json_v0221(
            private_root / "schema-session-capture.json",
            {
                "schema_version": "ecomsre.product.opensearch-schema-capture.v0221",
                "session_id": profile.session_id,
                "profile_sha256": profile.profile_sha256,
                "raw_artifact_sha256": dict(sorted(raw_hashes.items())),
                "safe_http_error_envelopes_sha256": envelopes_sha256,
                "sample_shape_sha256": execution.sample_shapes.sample_shape_sha256,
                "request_count": execution.request_count,
                "changed_plan_count": execution.changed_plan_count,
                "attempts": [
                    attempt.model_dump(mode="json")
                    for attempt in execution.attempts
                ],
                "traffic_result": traffic_result,
                "baseline_before_sha256": baseline_before,
                "baseline_unchanged": baseline_unchanged,
                "owned_demo_cleanup": cleanup.model_dump(mode="json"),
                "fault_attempt_count": 0,
                "baseline_readiness_attempt_count": 0,
                "knowledge_loop_campaign_count": 0,
                "action_authority": "NONE",
                "agent_writes": 0,
                "runbook_executions": 0,
            },
        )
        session = _session_payload_v0221(
            execution=execution,
            session_id=profile.session_id,
            session_profile_sha256=profile.profile_sha256,
            fixture=fixture,
            offline=offline,
            private_capture_sha256=capture_sha256,
            private_sample_response_sha256=private_sample_response_sha256,
            live_sample_validation=live_sample_validation,
            baseline_unchanged=baseline_unchanged,
            cleanup=cleanup.verdict,
        )
        matrix = json.loads(
            (
                root
                / "docs/analysis/product-v0221-request-protocol-matrix.json"
            ).read_text(encoding="utf-8")
        )
        progress = _progress_payload_v0221(
            request_protocol_matrix_sha256=str(matrix["matrix_sha256"]),
            session=session,
            offline=offline,
        )
        _write_public_text_v0221(
            root / profile.normalization_profile_path,
            execution.resolution.profile.model_dump_json(indent=2) + "\n",
        )
        _write_public_text_v0221(
            root / profile.sanitized_fixture_path,
            fixture.model_dump_json(indent=2) + "\n",
        )
        _write_public_text_v0221(
            root / profile.offline_parser_report_path,
            offline.model_dump_json(indent=2) + "\n",
        )
        _write_public_text_v0221(
            root / profile.schema_session_json,
            json.dumps(session, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        _write_public_text_v0221(
            root / profile.schema_session_markdown,
            _render_session_markdown_v0221(session),
        )
        _write_public_text_v0221(
            root / "docs/analysis/product-v0221-progress.json",
            json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        completion_sha256 = _write_private_json_v0221(
            complete_path,
            {
                "schema_version": (
                    "ecomsre.product.opensearch-schema-session-complete.v0221"
                ),
                "session_id": profile.session_id,
                "terminal": OFFLINE_PARSER_PASS_V0221,
                "schema_discovery_terminal": SCHEMA_DISCOVERY_PASS_V0221,
                "completed_at": datetime.now(UTC).isoformat(),
                "request_count": execution.request_count,
                "changed_plan_count": execution.changed_plan_count,
                "normalization_profile_sha256": (
                    execution.resolution.profile.profile_sha256
                ),
                "sanitized_fixture_sha256": fixture.fixture_sha256,
                "offline_parser_report_sha256": offline.report_sha256,
                "schema_session_sha256": session["report_sha256"],
                "private_capture_sha256": capture_sha256,
                "baseline_unchanged": baseline_unchanged,
                "owned_demo_cleanup": cleanup.verdict,
                "fault_attempt_count": 0,
                "baseline_readiness_attempt_count": 0,
                "knowledge_loop_campaign_count": 0,
                "action_authority": "NONE",
                "agent_writes": 0,
                "runbook_executions": 0,
            },
        )
        return {
            "status": OFFLINE_PARSER_PASS_V0221,
            "schema_discovery_terminal": SCHEMA_DISCOVERY_PASS_V0221,
            "request_protocol_terminal": increment2["status"],
            "session_id": profile.session_id,
            "live_schema_discovery_session_count": 1,
            "changed_request_plan_count": execution.changed_plan_count,
            "total_read_only_opensearch_request_count": execution.request_count,
            "transport_retry_count": execution.transport_retry_count,
            "offline_parser_changed_iteration_count": 1,
            "normalization_profile_sha256": (
                execution.resolution.profile.profile_sha256
            ),
            "sanitized_fixture_sha256": fixture.fixture_sha256,
            "completion_sha256": completion_sha256,
            "baseline_unchanged": baseline_unchanged,
            "owned_demo_cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
            "agent_writes": 0,
            "runbook_executions": 0,
        }
    except BaseException as error:
        failure = error
    finally:
        if probe_client is not None:
            probe_client.close()
        if cleanup.verdict != "CLEAN":
            if baseline_before is not None and lifecycle.controller is not None:
                try:
                    baseline_unchanged = (
                        lifecycle.read_baseline_sha256() == baseline_before
                    )
                except BaseException:
                    baseline_unchanged = False
            try:
                cleanup = lifecycle.cleanup_owned(
                    baseline_unchanged=baseline_unchanged
                )
            except BaseException:
                cleanup = CleanupObservation.unknown_blocked()
    if isinstance(failure, (LiveSchemaSessionBlockerV0221, OpenSearchProbeProtocolBlockerV0221)):
        terminal = failure.terminal
    elif isinstance(failure, OpenSearchHttpErrorV0221) and (
        failure.envelope.endpoint_kind.value == "MAPPING"
    ):
        terminal = MAPPING_UNAVAILABLE_V0221
    elif isinstance(failure, OpenSearchSchemaExceptionV022):
        terminal = MAPPING_UNAVAILABLE_V0221
    else:
        terminal = REQUEST_PROTOCOL_BLOCKED_V0221
    failure_type = type(failure).__name__ if failure is not None else "UNKNOWN"
    safe_message = str(failure)[:240] if failure is not None else "unknown failure"
    completion_sha256 = _write_private_json_v0221(
        complete_path,
        {
            "schema_version": (
                "ecomsre.product.opensearch-schema-session-complete.v0221"
            ),
            "session_id": profile.session_id,
            "terminal": terminal,
            "completed_at": datetime.now(UTC).isoformat(),
            "request_count": 0 if probe_client is None else probe_client.request_count,
            "changed_plan_count": (
                0 if execution is None else execution.changed_plan_count
            ),
            "failure_type": failure_type,
            "safe_message": safe_message,
            "baseline_unchanged": baseline_unchanged,
            "owned_demo_cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
            "agent_writes": 0,
            "runbook_executions": 0,
        },
    )
    return {
        "status": terminal,
        "session_id": profile.session_id,
        "live_schema_discovery_session_count": 1,
        "changed_request_plan_count": (
            0 if execution is None else execution.changed_plan_count
        ),
        "total_read_only_opensearch_request_count": (
            0 if probe_client is None else probe_client.request_count
        ),
        "failure_type": failure_type,
        "safe_message": safe_message,
        "completion_sha256": completion_sha256,
        "baseline_unchanged": baseline_unchanged,
        "owned_demo_cleanup": cleanup.verdict,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }


__all__ = (
    "LOG_INGESTION_BLOCKED_V0221",
    "MAPPING_UNAVAILABLE_V0221",
    "PINNED_UPSTREAM_V0221",
    "REQUEST_PROTOCOL_BLOCKED_V0221",
    "run_live_schema_probe_v0221",
)
