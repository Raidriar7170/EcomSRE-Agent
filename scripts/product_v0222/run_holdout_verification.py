"""Run one fresh bounded OpenSearch profile holdout for Product v0.2.2.2."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

import httpx

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureStoreV0222,
)
from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchProbeClientV0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeEndpointKindV0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    build_profile_verification_body_v0221,
    build_service_aggregation_body_v0221,
    build_timestamp_range_body_v0221,
)
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    HOLDOUT_VERIFICATION_PASS_V0222,
    OFFLINE_PROFILE_PASS_V0222,
    OpenSearchNormalizationProfileV0222,
    OpenSearchOfflineProfileReportV0222,
    OpenSearchProfileStatusV0222,
    evaluate_holdout_verification_v0222,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
)
from ecomsre.product.pilot.live_capture_v0222 import load_capture_profile_v0222


def _write_public(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v0222.tmp"
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_private_once(path: Path, payload: Mapping[str, object]) -> str:
    body = dict(payload)
    body["report_sha256"] = semantic_sha256_v22(body)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        encoded = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode()
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return str(body["report_sha256"])


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preflight(root: Path) -> tuple[
    OpenSearchNormalizationProfileV0222,
    OpenSearchOfflineProfileReportV0222,
    int,
    Path,
]:
    profile_path = root / "config/product-v0222/opensearch/normalization-profile.json"
    selected = OpenSearchNormalizationProfileV0222.model_validate_json(
        profile_path.read_text(encoding="utf-8")
    )
    offline = OpenSearchOfflineProfileReportV0222.model_validate_json(
        (
            root / "docs/analysis/product-v0222-offline-profile.json"
        ).read_text(encoding="utf-8")
    )
    progress = json.loads(
        (root / "docs/analysis/product-v0222-progress.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        selected.profile_status is not OpenSearchProfileStatusV0222.OPERATOR_SELECTED
        or offline.terminal != OFFLINE_PROFILE_PASS_V0222
        or offline.normalization_profile_sha256 != selected.profile_sha256
        or progress.get("operator_selection_count") not in {1, 2}
        or progress.get("holdout_verification_session_count") != 0
        or progress.get("fault_attempt_count") != 0
        or progress.get("baseline_readiness_attempt_count") != 0
        or progress.get("product_diagnosis_attempt_count") != 0
        or progress.get("knowledge_loop_campaign_count") != 0
    ):
        raise ValueError("Product v0.2.2.2 holdout preflight differs")
    ordinal = int(progress["operator_selection_count"])
    private_root = (
        root
        / f".local/product-v0222/opensearch-holdout/session-{ordinal}/private"
    )
    protected = (
        private_root / "holdout-session-start.json",
        private_root / "holdout-session-complete.json",
        root / "docs/analysis/product-v0222-holdout-verification.json",
    )
    if any(path.exists() for path in protected):
        raise ValueError("Product v0.2.2.2 holdout session is already consumed")
    return selected, offline, ordinal, private_root


def run_holdout_verification_v0222(root: Path) -> dict[str, Any]:
    repository = root.resolve(strict=True)
    selected, offline, ordinal, private_root = _preflight(repository)
    capture_profile = load_capture_profile_v0222(
        repository / "config/product-v0222/opensearch/profile.json"
    )
    profile_path = (
        repository / "config/product-v0222/opensearch/normalization-profile.json"
    )
    profile_file_sha_before = _file_sha256(profile_path)
    start_path = private_root / "holdout-session-start.json"
    completion_path = private_root / "holdout-session-complete.json"
    _write_private_once(
        start_path,
        {
            "schema_version": "ecomsre.product.opensearch-holdout-start.v0222",
            "session_id": f"product-v0222-holdout-{ordinal}",
            "started_at": datetime.now(UTC).isoformat(),
            "holdout_verification_session_count": ordinal,
            "selected_profile_sha256": selected.profile_sha256,
            "selected_profile_file_sha256": profile_file_sha_before,
            "capture_bundle_sha256": selected.capture_bundle_sha256,
            "candidate_set_sha256": selected.candidate_set_sha256,
            "operator_decision_sha256": selected.operator_decision_sha256,
            "maximum_request_count": 12,
            "maximum_same_request_transport_retries": 3,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
        },
    )
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=repository,
        private_root=private_root,
        stabilization_seconds=capture_profile.stabilization_seconds,
    )
    store = OpenSearchCaptureStoreV0222(
        private_root=private_root,
        session_id=f"product-v0222-holdout-{ordinal}",
        maximum_response_bytes=capture_profile.maximum_response_bytes,
    )
    client: OpenSearchProbeClientV0221 | None = None
    cleanup = CleanupObservation.unknown_blocked()
    baseline_before: str | None = None
    baseline_unchanged = False
    started = False
    failure: BaseException | None = None
    now = datetime.now(UTC)
    query_started_at = now - timedelta(seconds=1)
    query_ended_at = now
    empty_search: dict[str, object] = {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {"total": {"value": 0}, "hits": []},
    }
    aggregation_response: object = {
        **empty_search,
        "aggregations": {"services": {"buckets": []}},
    }
    timestamp_response: object = empty_search
    targeted_response: object = empty_search
    traffic_result: dict[str, object] | None = None
    try:
        lifecycle.admit()
        lifecycle.start()
        started = True
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if not isinstance(backend, LocalSandboxReadBackend):
            raise TypeError("Product v0.2.2.2 holdout lacks owned read authority")
        baseline_before = lifecycle.read_baseline_sha256()
        with httpx.Client(trust_env=False) as traffic_client:
            traffic = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=capture_profile.healthy_traffic_profile,
            )
        traffic_result = traffic.model_dump(mode="json")
        if traffic.succeeded == 0 or traffic.stopped_on_error_budget:
            raise RuntimeError("Product v0.2.2.2 holdout traffic failed")
        query_ended_at = datetime.now(UTC)
        query_started_at = query_ended_at - timedelta(
            seconds=capture_profile.recent_window_seconds
        )
        started_iso = query_started_at.isoformat()
        ended_iso = query_ended_at.isoformat()
        client = OpenSearchProbeClientV0221(
            base_url=backend.config.opensearch_base_url,
            maximum_request_count=12,
            maximum_response_bytes=capture_profile.maximum_response_bytes,
            capture_store=store,
            request_count_bound=16,
            transport_retry_bound=3,
        )
        encoded_index = quote(selected.index_pattern, safe="*,-_")
        path = f"/{encoded_index}/_search"
        aggregation_body = build_service_aggregation_body_v0221(
            selected.service_query_field
        )
        aggregation_body["query"] = {
            "range": {
                selected.timestamp_extraction.extraction.paths[0]: {
                    "gte": started_iso,
                    "lte": ended_iso,
                }
            }
        }
        aggregation_response, _, _ = client.request_json_with_transport_retries(
            maximum_transport_retries=3,
            plan_id="holdout-selected-profile",
            request_id="service-aggregation",
            method="POST",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.SERVICE_AGGREGATION,
            path=path,
            path_template="/{index}/_search",
            query_parameters={},
            json_body=aggregation_body,
        )
        timestamp_response, _, _ = client.request_json_with_transport_retries(
            maximum_transport_retries=3,
            plan_id="holdout-selected-profile",
            request_id="timestamp-range",
            method="POST",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.TIMESTAMP_RANGE,
            path=path,
            path_template="/{index}/_search",
            query_parameters={},
            json_body=build_timestamp_range_body_v0221(
                selected.timestamp_extraction.extraction.paths[0],
                started_at=started_iso,
                ended_at=ended_iso,
            ),
        )
        targeted_response, _, _ = client.request_json_with_transport_retries(
            maximum_transport_retries=3,
            plan_id="holdout-selected-profile",
            request_id="targeted-checkout",
            method="POST",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.PROFILE_VERIFICATION,
            path=path,
            path_template="/{index}/_search",
            query_parameters={},
            json_body=build_profile_verification_body_v0221(
                service_query_field=selected.service_query_field,
                timestamp_query_field=(
                    selected.timestamp_extraction.extraction.paths[0]
                ),
                checkout_aliases=capture_profile.checkout_aliases,
                started_at=started_iso,
                ended_at=ended_iso,
            ),
        )
        baseline_unchanged = lifecycle.read_baseline_sha256() == baseline_before
        cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_unchanged)
    except BaseException as error:
        failure = error
    finally:
        if client is not None:
            client.close()
        if started and cleanup.verdict != "CLEAN":
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
    profile_file_sha_after = _file_sha256(profile_path)
    request_count = 0 if client is None else client.request_count
    retry_count = max(
        (
            attempt.transport_retry_count
            for attempt in (() if client is None else client.attempts)
        ),
        default=0,
    )
    report = evaluate_holdout_verification_v0222(
        profile=selected,
        service_aggregation_response=aggregation_response,
        timestamp_range_response=timestamp_response,
        targeted_response=targeted_response,
        started_at=query_started_at,
        ended_at=query_ended_at,
        service_aliases={
            alias: "checkout" for alias in capture_profile.checkout_aliases
        },
        read_only_request_count=request_count,
        transport_retry_count=retry_count,
        selected_profile_file_sha256_before=profile_file_sha_before,
        selected_profile_file_sha256_after=profile_file_sha_after,
        cleanup=cleanup.verdict,
        baseline_unchanged=baseline_unchanged,
        session_ordinal=ordinal,
    )
    if failure is not None and report.terminal == HOLDOUT_VERIFICATION_PASS_V0222:
        raise RuntimeError("Product v0.2.2.2 holdout failure was not fail closed")
    completion_sha256 = _write_private_once(
        completion_path,
        {
            "schema_version": "ecomsre.product.opensearch-holdout-complete.v0222",
            "session_id": report.session_id,
            "completed_at": datetime.now(UTC).isoformat(),
            "terminal": report.terminal,
            "safe_message": None if failure is None else str(failure)[:240],
            "verification_sha256": report.verification_sha256,
            "read_only_request_count": request_count,
            "transport_retry_count": retry_count,
            "private_ledger_sha256": store.capture_ledger().ledger_sha256,
            "private_response_object_count": store.verify_content_addressed_objects(),
            "baseline_unchanged": baseline_unchanged,
            "cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
        },
    )
    _write_public(
        repository / "docs/analysis/product-v0222-holdout-verification.json",
        report.model_dump(mode="json"),
    )
    active_profile = None
    if report.terminal == HOLDOUT_VERIFICATION_PASS_V0222:
        active_profile = selected.activate()
        _write_public(profile_path, active_profile.model_dump(mode="json"))
    progress_path = repository / "docs/analysis/product-v0222-progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    progress.pop("progress_sha256", None)
    progress.update(
        {
            "holdout_verification_session_count": ordinal,
            "holdout_read_only_request_count": request_count,
            "holdout_transport_retry_count": retry_count,
            "holdout_verification_sha256": report.verification_sha256,
            "holdout_verification_terminal": report.terminal,
            "normalization_profile_status": (
                "ACTIVE" if active_profile is not None else "OPERATOR_SELECTED"
            ),
            "normalization_profile_sha256": (
                active_profile.profile_sha256
                if active_profile is not None
                else selected.profile_sha256
            ),
            "selected_profile_sha256": selected.profile_sha256,
            "cleanup": cleanup.verdict,
            "terminal": report.terminal,
            "next_boundary": (
                "RUN_FRESH_CONNECTOR_SMOKE"
                if active_profile is not None
                else "STOP_FOR_OPERATOR_RESELECTION"
            ),
        }
    )
    progress["progress_sha256"] = semantic_sha256_v22(progress)
    _write_public(progress_path, progress)
    return {
        "status": report.terminal,
        "session_id": report.session_id,
        "holdout_verification_session_count": ordinal,
        "read_only_request_count": request_count,
        "transport_retry_count": retry_count,
        "selected_profile_sha256": selected.profile_sha256,
        "active_profile_sha256": (
            None if active_profile is None else active_profile.profile_sha256
        ),
        "verification_sha256": report.verification_sha256,
        "completion_sha256": completion_sha256,
        "traffic_result": traffic_result,
        "cleanup": cleanup.verdict,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--execute-live", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_live:
        raise ValueError("Product v0.2.2.2 holdout requires --execute-live")
    print(
        json.dumps(
            run_holdout_verification_v0222(args.project_root),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("run_holdout_verification_v0222", "main")
