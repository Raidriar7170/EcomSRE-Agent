"""Single bounded live OpenSearch schema-discovery campaign for Product v0.2.2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping
from urllib.parse import quote, urlsplit

import httpx

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_probe_v022 import (
    build_public_schema_fingerprint_v022,
    load_schema_probe_profile_v022,
    parse_field_caps_v022,
    parse_mapping_v022,
    resolve_normalization_profile_v022,
    summarize_sample_shapes_v022,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre_live_sandbox.contracts import ensure_private_directory
from ecomsre_live_sandbox.environment import ExactCommandRunner
from scripts.ci.verify_product_v022_increment1 import verify_product_v022_increment1


PINNED_UPSTREAM_V022 = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
SCHEMA_DISCOVERY_PASS_V022 = "ECOMSRE_PRODUCT_V022_SCHEMA_DISCOVERY_PASS"
SCHEMA_PROBE_BLOCKED_V022 = "BLOCKED_ECOMSRE_PRODUCT_V022_SCHEMA_PROBE"
LOG_INGESTION_BLOCKED_V022 = "BLOCKED_ECOMSRE_PRODUCT_V022_LOG_INGESTION"


class SchemaProbeBlockerV022(RuntimeError):
    def __init__(self, terminal: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.terminal = terminal


class _BoundedOpenSearchProbeClientV022:
    def __init__(
        self,
        *,
        base_url: str,
        maximum_request_count: int,
        maximum_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port is None
            or parsed.path not in {"", "/"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenSearch schema probe endpoint must be loopback")
        if not 1 <= maximum_request_count <= 12:
            raise ValueError("OpenSearch schema probe request bound differs")
        self.maximum_request_count = maximum_request_count
        self.maximum_response_bytes = maximum_response_bytes
        self.request_count = 0
        self.request_metadata: list[dict[str, object]] = []
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=15.0,
            follow_redirects=False,
            transport=transport,
        )

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
    ) -> tuple[object, bytes]:
        if method not in {"GET", "POST"} or not path.startswith("/"):
            raise ValueError("OpenSearch schema probe request is not read-only")
        if self.request_count >= self.maximum_request_count:
            raise RuntimeError("OpenSearch schema probe request budget exhausted")
        self.request_count += 1
        response = self._client.request(method, path, json=json_body)
        content = response.content
        if len(content) > self.maximum_response_bytes:
            raise RuntimeError("OpenSearch schema probe response exceeds byte bound")
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"OpenSearch schema probe HTTP status {response.status_code}"
            )
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("OpenSearch schema probe response is not JSON") from error
        self.request_metadata.append(
            {
                "ordinal": self.request_count,
                "method": method,
                "path": path.split("?", 1)[0],
                "request_body_sha256": semantic_sha256_v22(json_body),
                "response_sha256": hashlib.sha256(content).hexdigest(),
                "response_bytes": len(content),
            }
        )
        return payload, content

    def close(self) -> None:
        self._client.close()


def _write_private_bytes_v022(path: Path, content: bytes) -> str:
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


def _write_private_json_v022(path: Path, payload: Mapping[str, object]) -> str:
    body = dict(payload)
    digest = semantic_sha256_v22(body)
    body["report_sha256"] = digest
    encoded = (
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_private_bytes_v022(path, encoded)
    return digest


def _write_public_text_v022(path: Path, content: str) -> None:
    metadata = os.lstat(path.parent)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("Product v0.2.2 public parent is not a regular directory")
    temporary = path.parent / f".{path.name}.product-v022.tmp"
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


def _require_absent_v022(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    raise ValueError(f"Product v0.2.2 output already exists: {path.name}")


def _field_caps_allowlist_v022(mapping_fields: tuple[str, ...]) -> tuple[str, ...]:
    tokens = ("timestamp", "timeunixnano", "service", "severity", "body", "message", "trace")
    selected = tuple(
        path
        for path in sorted(set(mapping_fields))
        if any(token in path.lower().replace("_", "") for token in tokens)
    )
    if not selected or len(selected) > 64:
        raise SchemaProbeBlockerV022(
            SCHEMA_PROBE_BLOCKED_V022,
            "OpenSearch field-caps candidate allowlist is invalid",
        )
    return selected


def _select_probe_fields_v022(
    mapping: object,
    field_caps: object,
) -> tuple[str, str, str]:
    mapping_snapshot = parse_mapping_v022(mapping)
    caps_snapshot = parse_field_caps_v022(field_caps)
    timestamp_candidates = tuple(
        path
        for path, item in mapping_snapshot.fields.items()
        if item.mapping_type in {"date", "date_nanos"}
        and ("timestamp" in path.lower() or "time" in path.lower())
    )
    service_source_candidates = tuple(
        path
        for path in mapping_snapshot.fields
        if "service" in path.lower()
        and "name" in path.lower()
        and not path.endswith(".keyword")
    )
    service_query_candidates = tuple(
        path
        for path, item in caps_snapshot.fields.items()
        if "service" in path.lower()
        and "name" in path.lower()
        and item.searchable
        and item.aggregatable
    )
    if not timestamp_candidates or not service_source_candidates:
        raise SchemaProbeBlockerV022(
            SCHEMA_PROBE_BLOCKED_V022,
            "OpenSearch required probe fields were not discovered",
        )
    if not service_query_candidates:
        raise SchemaProbeBlockerV022(
            SCHEMA_PROBE_BLOCKED_V022,
            "OpenSearch service query field is not aggregatable",
        )
    timestamp = sorted(
        timestamp_candidates,
        key=lambda path: (path not in {"observedTimestamp", "@timestamp"}, path),
    )[0]
    service_source = sorted(
        service_source_candidates,
        key=lambda path: (path != "resource.service.name", path),
    )[0]
    service_query = sorted(
        service_query_candidates,
        key=lambda path: (
            path != f"{service_source}.keyword",
            not path.endswith(".keyword"),
            path,
        ),
    )[0]
    return timestamp, service_source, service_query


def _mapping_object_v022(value: object, safe_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SchemaProbeBlockerV022(
            SCHEMA_PROBE_BLOCKED_V022,
            f"OpenSearch {safe_name} response shape is invalid",
        )
    return value


def _aggregation_aliases_v022(
    payload: object,
    configured: tuple[str, ...],
) -> tuple[str, ...]:
    body = _mapping_object_v022(payload, "aggregation")
    aggregations = _mapping_object_v022(body.get("aggregations"), "aggregation")
    services = _mapping_object_v022(aggregations.get("services"), "aggregation")
    buckets = services.get("buckets")
    if not isinstance(buckets, list):
        raise SchemaProbeBlockerV022(
            SCHEMA_PROBE_BLOCKED_V022,
            "OpenSearch service aggregation buckets are invalid",
        )
    observed = tuple(
        str(bucket["key"])
        for bucket in buckets
        if isinstance(bucket, Mapping)
        and isinstance(bucket.get("key"), str)
        and "checkout" in str(bucket["key"]).lower().replace("-", "").replace("_", "")
    )
    return tuple(sorted(set(configured) | set(observed)))


def _sample_sources_v022(payload: object) -> tuple[Mapping[str, object], ...]:
    body = _mapping_object_v022(payload, "sample")
    hits = _mapping_object_v022(body.get("hits"), "sample hits")
    rows = hits.get("hits")
    if not isinstance(rows, list) or len(rows) > 5:
        raise SchemaProbeBlockerV022(
            SCHEMA_PROBE_BLOCKED_V022,
            "OpenSearch bounded sample hit list is invalid",
        )
    sources: list[Mapping[str, object]] = []
    for row in rows:
        hit = _mapping_object_v022(row, "sample hit")
        source = _mapping_object_v022(hit.get("_source"), "sample source")
        sources.append(source)
    return tuple(sources)


def _render_fingerprint_markdown_v022(fingerprint: Mapping[str, object]) -> str:
    profile = fingerprint.get("normalization_profile")
    index_names = fingerprint.get("index_names")
    field_paths = fingerprint.get("field_paths")
    if (
        not isinstance(profile, Mapping)
        or not isinstance(index_names, (list, tuple))
        or not isinstance(field_paths, (list, tuple))
    ):
        raise ValueError("OpenSearch public fingerprint profile is absent")
    return "\n".join(
        (
            "# Product v0.2.2 OpenSearch schema fingerprint",
            "",
            f"Terminal: `{fingerprint['terminal']}`",
            "",
            f"- indexes: `{len(index_names)}`",
            f"- mapped fields: `{len(field_paths)}`",
            f"- bounded samples: `{fingerprint['sample_count']}` / `5`",
            f"- OpenSearch requests: `{fingerprint['request_count']}` / `12`",
            f"- timestamp extraction: `{profile['timestamp_extraction']}`",
            f"- service source field: `{profile['service_source_field']}`",
            f"- service query field: `{profile['service_query_field']}`",
            f"- severity extraction: `{profile['severity_extraction']}`",
            f"- message extraction: `{profile['message_extraction']}`",
            f"- profile SHA-256: `{profile['profile_sha256']}`",
            "",
            "Raw mappings, field capabilities, source documents, and message bodies ",
            "remain under the private `.local/product-v022` evidence root.",
            "",
        )
    )


def _progress_payload_v022(*, profile_sha256: str) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.v022.progress.v1",
        "goal_version": (
            "ecomsre-product-v022-opensearch-baseline-compatibility-v1"
        ),
        "branch": "codex/product-v022-opensearch-baseline-compatibility",
        "increment": 2,
        "terminal": SCHEMA_DISCOVERY_PASS_V022,
        "v02_terminal": "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE",
        "v021_terminal": "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS",
        "next_boundary": "OFFLINE_COMPATIBILITY_REPLAY",
        "schema_probe_execution_count": 1,
        "offline_changed_iteration_count": 0,
        "connector_smoke_changed_attempt_count": 0,
        "baseline_readiness_campaign_count": 0,
        "infrastructure_replacement_count": 0,
        "nofault_acceptance_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "profile_status": "DISCOVERED",
        "normalization_profile_sha256": profile_sha256,
    }
    body["progress_sha256"] = semantic_sha256_v22(body)
    return body


def run_live_schema_probe_v022(
    repository_root: Path,
    config_path: Path | None = None,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    increment1 = verify_product_v022_increment1(root)
    profile = load_schema_probe_profile_v022(
        config_path or root / "config/product-v022/opensearch-probe/profile.json"
    )
    upstream = ExactCommandRunner().run(
        ("git", "rev-parse", "HEAD"),
        cwd=root / "third_party/opentelemetry-demo",
        timeout_seconds=30,
    ).stdout.strip()
    if upstream != PINNED_UPSTREAM_V022:
        raise ValueError("pinned OTel Demo commit differs")
    private_root = root / profile.private_root
    start_path = private_root / "schema-probe-start.json"
    complete_path = private_root / "schema-probe-complete.json"
    for path in (
        start_path,
        complete_path,
        root / profile.public_fingerprint_json,
        root / profile.public_fingerprint_markdown,
        root / profile.normalization_profile_path,
    ):
        _require_absent_v022(path)
    ensure_private_directory(private_root)
    started_at = datetime.now(UTC)
    _write_private_json_v022(
        start_path,
        {
            "schema_version": "ecomsre.product.opensearch-schema-probe-start.v022",
            "campaign_id": profile.campaign_id,
            "started_at": started_at.isoformat(),
            "profile_sha256": profile.profile_sha256,
            "execution_count": 1,
            "maximum_request_count": profile.maximum_request_count,
            "fault_attempt_count": 0,
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
    traffic_result: dict[str, object] | None = None
    probe_client: _BoundedOpenSearchProbeClientV022 | None = None
    mapping_payload: object | None = None
    field_caps_payload: object | None = None
    sample_payload: object | None = None
    mapping_bytes = field_caps_bytes = aggregation_bytes = sample_bytes = b""
    failure: BaseException | None = None
    try:
        lifecycle.admit()
        lifecycle.start()
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if not isinstance(backend, LocalSandboxReadBackend):
            raise TypeError("owned schema probe did not return a local read backend")
        baseline_before = lifecycle.read_baseline_sha256()
        traffic_profile = HealthyTrafficProfileV021.model_validate(
            profile.healthy_traffic_profile.model_dump(mode="json")
        )
        with httpx.Client() as traffic_client:
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
            raise SchemaProbeBlockerV022(
                LOG_INGESTION_BLOCKED_V022,
                "bounded healthy checkout traffic did not complete",
            )
        probe_client = _BoundedOpenSearchProbeClientV022(
            base_url=backend.config.opensearch_base_url,
            maximum_request_count=profile.maximum_request_count,
            maximum_response_bytes=profile.maximum_response_bytes,
        )
        index = quote(profile.index_pattern, safe="*,-_")
        mapping_payload, mapping_bytes = probe_client.request_json(
            "GET", f"/{index}/_mapping"
        )
        mapping_snapshot = parse_mapping_v022(mapping_payload)
        field_allowlist = _field_caps_allowlist_v022(tuple(mapping_snapshot.fields))
        field_caps_payload, field_caps_bytes = probe_client.request_json(
            "POST",
            f"/{index}/_field_caps",
            json_body={"fields": list(field_allowlist)},
        )
        timestamp_field, service_source_field, service_query_field = (
            _select_probe_fields_v022(mapping_payload, field_caps_payload)
        )
        aggregation_payload, aggregation_bytes = probe_client.request_json(
            "POST",
            f"/{index}/_search",
            json_body={
                "size": 0,
                "aggs": {
                    "services": {
                        "terms": {"field": service_query_field, "size": 100}
                    }
                },
            },
        )
        aliases = _aggregation_aliases_v022(
            aggregation_payload,
            profile.checkout_aliases,
        )
        ended_at = datetime.now(UTC)
        started_window = ended_at - timedelta(seconds=profile.recent_window_seconds)
        query_filter: list[object] = [
            {
                "range": {
                    timestamp_field: {
                        "gte": started_window.isoformat(),
                        "lte": ended_at.isoformat(),
                    }
                }
            },
            {"terms": {service_query_field: list(aliases)}},
        ]
        sample_payload, sample_bytes = probe_client.request_json(
            "POST",
            f"/{index}/_search",
            json_body={
                "size": profile.maximum_sample_documents,
                "sort": [{timestamp_field: {"order": "desc"}}],
                "query": {"bool": {"filter": query_filter}},
                "_source": True,
            },
        )
        samples = _sample_sources_v022(sample_payload)
        if not samples:
            sample_payload, followup_bytes = probe_client.request_json(
                "POST",
                f"/{index}/_search",
                json_body={
                    "size": profile.maximum_sample_documents,
                    "sort": [{timestamp_field: {"order": "desc"}}],
                    "query": {
                        "bool": {
                            "filter": [query_filter[0]],
                            "should": [
                                {"match": {service_source_field: alias}}
                                for alias in aliases
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                    "_source": True,
                },
            )
            sample_bytes += followup_bytes
            samples = _sample_sources_v022(sample_payload)
        if len(sample_bytes) > profile.maximum_response_bytes:
            raise SchemaProbeBlockerV022(
                SCHEMA_PROBE_BLOCKED_V022,
                "OpenSearch combined sample responses exceed byte bound",
            )
        if not samples:
            raise SchemaProbeBlockerV022(
                LOG_INGESTION_BLOCKED_V022,
                "bounded checkout traffic produced zero checkout log documents",
            )
        sample_shapes = summarize_sample_shapes_v022(samples)
        field_caps_snapshot = parse_field_caps_v022(field_caps_payload)
        resolution = resolve_normalization_profile_v022(
            index_pattern=profile.index_pattern,
            mapping=mapping_snapshot,
            field_caps=field_caps_snapshot,
            samples=samples,
            sample_shapes=sample_shapes,
            checkout_aliases=aliases,
        )
        raw_hashes = {
            "mapping.json": _write_private_bytes_v022(
                private_root / "mapping.json", mapping_bytes
            ),
            "field-caps.json": _write_private_bytes_v022(
                private_root / "field-caps.json", field_caps_bytes
            ),
            "service-aggregation.json": _write_private_bytes_v022(
                private_root / "service-aggregation.json", aggregation_bytes
            ),
            "sample-search.json": _write_private_bytes_v022(
                private_root / "sample-search.json", sample_bytes
            ),
        }
        baseline_after = lifecycle.read_baseline_sha256()
        baseline_unchanged = baseline_before == baseline_after
        cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_unchanged)
        if cleanup.verdict != "CLEAN":
            raise SchemaProbeBlockerV022(
                SCHEMA_PROBE_BLOCKED_V022,
                "owned schema-probe cleanup is not clean",
            )
        capture_sha256 = _write_private_json_v022(
            private_root / "schema-probe-capture.json",
            {
                "schema_version": "ecomsre.product.opensearch-schema-capture.v022",
                "campaign_id": profile.campaign_id,
                "profile_sha256": profile.profile_sha256,
                "raw_artifact_sha256": raw_hashes,
                "sample_count": sample_shapes.sample_count,
                "sample_response_bytes": len(sample_bytes),
                "request_count": probe_client.request_count,
                "request_metadata": probe_client.request_metadata,
                "traffic_result": traffic_result,
                "baseline_before_sha256": baseline_before,
                "baseline_unchanged": baseline_unchanged,
                "owned_demo_cleanup": cleanup.model_dump(mode="json"),
                "fault_attempt_count": 0,
                "action_authority": "NONE",
                "agent_writes": 0,
                "runbook_executions": 0,
            },
        )
        fingerprint = build_public_schema_fingerprint_v022(
            mapping=mapping_snapshot,
            field_caps=field_caps_snapshot,
            sample_shapes=sample_shapes,
            resolution=resolution,
            private_capture_sha256=capture_sha256,
            request_count=probe_client.request_count,
        )
        fingerprint_payload = fingerprint.model_dump(mode="json")
        _write_public_text_v022(
            root / profile.normalization_profile_path,
            resolution.profile.model_dump_json(indent=2) + "\n",
        )
        _write_public_text_v022(
            root / profile.public_fingerprint_json,
            fingerprint.model_dump_json(indent=2) + "\n",
        )
        _write_public_text_v022(
            root / profile.public_fingerprint_markdown,
            _render_fingerprint_markdown_v022(fingerprint_payload),
        )
        progress = _progress_payload_v022(
            profile_sha256=resolution.profile.profile_sha256
        )
        _write_public_text_v022(
            root / "docs/analysis/product-v022-progress.json",
            json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        completion_sha256 = _write_private_json_v022(
            complete_path,
            {
                "schema_version": (
                    "ecomsre.product.opensearch-schema-probe-complete.v022"
                ),
                "campaign_id": profile.campaign_id,
                "terminal": SCHEMA_DISCOVERY_PASS_V022,
                "completed_at": datetime.now(UTC).isoformat(),
                "request_count": probe_client.request_count,
                "sample_count": sample_shapes.sample_count,
                "normalization_profile_sha256": (
                    resolution.profile.profile_sha256
                ),
                "fingerprint_sha256": fingerprint.fingerprint_sha256,
                "private_capture_sha256": capture_sha256,
                "baseline_unchanged": baseline_unchanged,
                "owned_demo_cleanup": cleanup.verdict,
                "fault_attempt_count": 0,
                "action_authority": "NONE",
                "agent_writes": 0,
                "runbook_executions": 0,
            },
        )
        return {
            "status": SCHEMA_DISCOVERY_PASS_V022,
            "campaign_id": profile.campaign_id,
            "increment1_status": increment1["status"],
            "execution_count": 1,
            "request_count": probe_client.request_count,
            "sample_count": sample_shapes.sample_count,
            "normalization_profile_sha256": resolution.profile.profile_sha256,
            "fingerprint_sha256": fingerprint.fingerprint_sha256,
            "completion_sha256": completion_sha256,
            "baseline_unchanged": baseline_unchanged,
            "owned_demo_cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
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
    terminal = (
        failure.terminal
        if isinstance(failure, SchemaProbeBlockerV022)
        else SCHEMA_PROBE_BLOCKED_V022
    )
    failure_type = type(failure).__name__ if failure is not None else "UNKNOWN"
    safe_message = str(failure)[:240] if failure is not None else "unknown failure"
    _write_private_json_v022(
        complete_path,
        {
            "schema_version": "ecomsre.product.opensearch-schema-probe-complete.v022",
            "campaign_id": profile.campaign_id,
            "terminal": terminal,
            "completed_at": datetime.now(UTC).isoformat(),
            "request_count": 0 if probe_client is None else probe_client.request_count,
            "sample_count": 0,
            "failure_type": failure_type,
            "safe_message": safe_message,
            "baseline_unchanged": baseline_unchanged,
            "owned_demo_cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "action_authority": "NONE",
            "agent_writes": 0,
            "runbook_executions": 0,
        },
    )
    return {
        "status": terminal,
        "campaign_id": profile.campaign_id,
        "execution_count": 1,
        "request_count": 0 if probe_client is None else probe_client.request_count,
        "failure_type": failure_type,
        "safe_message": safe_message,
        "baseline_unchanged": baseline_unchanged,
        "owned_demo_cleanup": cleanup.verdict,
        "fault_attempt_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }


__all__ = (
    "LOG_INGESTION_BLOCKED_V022",
    "PINNED_UPSTREAM_V022",
    "SCHEMA_DISCOVERY_PASS_V022",
    "SCHEMA_PROBE_BLOCKED_V022",
    "run_live_schema_probe_v022",
)
