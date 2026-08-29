"""One capture-first live OpenSearch session for Product v0.2.2.2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import quote

import httpx
from pydantic import Field, model_validator

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    render_operator_brief_v0222,
)
from ecomsre.product.connectors.opensearch_capture_analysis_v0222 import (
    analyze_capture_bundle_v0222,
)
from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureStoreV0222,
)
from ecomsre.product.connectors.opensearch_http_v0221 import OpenSearchProbeClientV0221
from ecomsre.product.connectors.opensearch_probe_execution_v0221 import (
    execute_probe_protocol_v0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeEndpointKindV0221,
)
from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchProbeProtocolBlockerV0221,
    SCHEMA_AMBIGUOUS_V0221,
)
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
)
from ecomsre_live_sandbox.environment import ExactCommandRunner
from scripts.ci.verify_product_v0222_increment2 import verify_product_v0222_increment2


PINNED_UPSTREAM_V0222 = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
CAPTURE_PASS_V0222 = "ECOMSRE_PRODUCT_V0222_CAPTURE_FIRST_PASS"
CANDIDATE_READY_V0222 = "ECOMSRE_PRODUCT_V0222_CANDIDATE_SET_READY"
OPERATOR_BLOCKED_V0222 = "BLOCKED_ECOMSRE_PRODUCT_V0222_OPERATOR_SELECTION"
CAPTURE_PROTOCOL_BLOCKED_V0222 = "BLOCKED_ECOMSRE_PRODUCT_V0222_CAPTURE_PROTOCOL"
CAPTURE_INCOMPLETE_V0222 = "BLOCKED_ECOMSRE_PRODUCT_V0222_CAPTURE_INCOMPLETE"
CANDIDATE_EMPTY_V0222 = "BLOCKED_ECOMSRE_PRODUCT_V0222_CANDIDATE_SET_EMPTY"


class OpenSearchCaptureProfileV0222(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-capture-profile.v0222"] = (
        "ecomsre.product.opensearch-capture-profile.v0222"
    )
    session_id: Literal["product-v0222-capture-1"]
    index_pattern: str = Field(min_length=1, max_length=255)
    checkout_aliases: tuple[str, ...] = Field(min_length=1, max_length=10)
    maximum_changed_plan_count: Literal[3]
    maximum_request_count: Literal[20]
    maximum_transport_retries: Literal[3]
    protocol_transport_retries: Literal[2]
    maximum_sample_documents: Literal[5]
    maximum_response_bytes: Literal[2_000_000]
    recent_window_seconds: int = Field(ge=60, le=1_800)
    stabilization_seconds: int = Field(ge=0, le=120)
    healthy_traffic_profile: HealthyTrafficProfileV021
    private_root: Literal[".local/product-v0222/opensearch-capture/private"]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_profile(self) -> "OpenSearchCaptureProfileV0222":
        if self.checkout_aliases != tuple(sorted(set(self.checkout_aliases))):
            raise ValueError("Product v0.2.2.2 checkout aliases are not canonical")
        body = self.model_dump(mode="json", exclude={"profile_sha256"})
        if self.profile_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.2.2 capture profile digest differs")
        return self


def load_capture_profile_v0222(path: Path) -> OpenSearchCaptureProfileV0222:
    return OpenSearchCaptureProfileV0222.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _private_json(path: Path, payload: Mapping[str, object]) -> str:
    body = dict(payload)
    digest = semantic_sha256_v22(body)
    body["report_sha256"] = digest
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        encoded = (json.dumps(body, indent=2, sort_keys=True) + "\n").encode()
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest


def _public_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v0222.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _bound_payload(payload: dict[str, Any], digest_field: str) -> dict[str, Any]:
    payload[digest_field] = semantic_sha256_v22(payload)
    return payload


def _load_private_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.2 private report is not an object")
    report_sha256 = payload.get("report_sha256")
    body = {key: value for key, value in payload.items() if key != "report_sha256"}
    if report_sha256 != semantic_sha256_v22(body):
        raise ValueError("Product v0.2.2.2 private report digest differs")
    return payload


def _render_capture_markdown(payload: Mapping[str, object]) -> str:
    return "\n".join(
        (
            "# Product v0.2.2.2 Capture Summary",
            "",
            f"Capture terminal: `{payload['capture_terminal']}`",
            f"Candidate terminal: `{payload['candidate_set_terminal']}`",
            f"Current terminal: `{payload['terminal']}`",
            "",
            f"- session: `{payload['session_id']}`",
            f"- read-only requests: `{payload['read_only_request_count']} / 20`",
            f"- changed request plans: `{payload['changed_request_plan_count']} / 3`",
            f"- transport retries: `{payload['transport_retry_count']} / 3`",
            f"- capture bundle SHA: `{payload['capture_bundle_sha256']}`",
            f"- Candidate Set SHA: `{payload['candidate_set_sha256']}`",
            f"- candidates: `{payload['candidate_count']} / 12`",
            f"- cleanup: `{payload['cleanup']}`",
            "- fault / Baseline / Product Diagnosis / Knowledge Loop: `0 / 0 / 0 / 0`",
            "- Agent writes / Runbooks: `0 / 0`",
            "",
            "Raw response bytes remain only under the private capture root.",
            "A real operator must select one frozen candidate alias.",
            "",
        )
    )


def _render_capture_blocker_markdown(payload: Mapping[str, object]) -> str:
    return "\n".join(
        (
            "# Product v0.2.2.2 Capture Summary",
            "",
            f"Current terminal: `{payload['terminal']}`",
            "",
            f"- session: `{payload['session_id']}`",
            f"- read-only requests: `{payload['read_only_request_count']} / 20`",
            f"- changed request plans: `{payload['changed_request_plan_count']} / 3`",
            f"- transport retries: `{payload['transport_retry_count']} / 3`",
            f"- cleanup: `{payload['cleanup']}`",
            "- fault / Baseline / Product Diagnosis / Knowledge Loop: `0 / 0 / 0 / 0`",
            "- Agent writes / Runbooks: `0 / 0`",
            "",
            "Raw response bytes remain only under the private capture root.",
            "The consumed capture session is not eligible for an in-place retry.",
            "",
        )
    )


def verify_live_capture_preflight_v0222(root: Path) -> dict[str, object]:
    repository = Path(root).resolve(strict=True)
    increment2 = verify_product_v0222_increment2(repository)
    profile_path = repository / "config/product-v0222/opensearch/profile.json"
    profile = load_capture_profile_v0222(profile_path)
    upstream = (
        ExactCommandRunner()
        .run(
            ("git", "rev-parse", "HEAD"),
            cwd=repository / "third_party/opentelemetry-demo",
            timeout_seconds=30,
        )
        .stdout.strip()
    )
    private_root = repository / profile.private_root
    protected = (
        private_root / "capture-session-start.json",
        private_root / "capture-session-complete.json",
        private_root / "capture-ledger.jsonl",
        repository / "config/product-v0222/opensearch/capture-session.json",
        repository / "config/product-v0222/opensearch/candidate-set.json",
        repository / "docs/analysis/product-v0222-capture-summary.json",
        repository / "docs/analysis/product-v0222-candidate-set.json",
        repository / "docs/human-briefs/product-v0222-opensearch-profile-selection.md",
    )
    if upstream != PINNED_UPSTREAM_V0222 or any(path.exists() for path in protected):
        raise ValueError("Product v0.2.2.2 live capture preflight differs")
    return {
        "status": "ECOMSRE_PRODUCT_V0222_LIVE_CAPTURE_PREFLIGHT_READY",
        "increment2_status": increment2["status"],
        "session_id": profile.session_id,
        "profile_sha256": profile.profile_sha256,
        "upstream_commit": upstream,
        "capture_session_count": 0,
        "read_only_request_count": 0,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "action_authority": "NONE",
    }


def resume_frozen_capture_analysis_v0222(root: Path) -> dict[str, object]:
    """Replay the consumed capture bytes once after an offline parser repair."""

    repository = Path(root).resolve(strict=True)
    verify_product_v0222_increment2(repository)
    profile = load_capture_profile_v0222(
        repository / "config/product-v0222/opensearch/profile.json"
    )
    private_root = repository / profile.private_root
    start = _load_private_report(private_root / "capture-session-start.json")
    completion = _load_private_report(private_root / "capture-session-complete.json")
    if (
        start.get("session_id") != profile.session_id
        or start.get("capture_session_count") != 1
        or completion.get("session_id") != profile.session_id
        or completion.get("terminal") != CAPTURE_PROTOCOL_BLOCKED_V0222
        or completion.get("safe_message") != "OPENSEARCH_REQUIRED_FIELD_NOT_DISCOVERED"
        or completion.get("baseline_unchanged") is not True
        or completion.get("cleanup") != "CLEAN"
    ):
        raise ValueError("Product v0.2.2.2 frozen capture replay boundary differs")
    replay_path = private_root / "offline-analysis-iteration-1.json"
    if replay_path.exists():
        raise ValueError("Product v0.2.2.2 offline replay iteration is consumed")
    store = OpenSearchCaptureStoreV0222(
        private_root=private_root,
        session_id=profile.session_id,
        maximum_response_bytes=profile.maximum_response_bytes,
    )
    bundle = OpenSearchCaptureStoreV0222.load_bundle(private_root=private_root)
    if (
        not bundle.capture_completeness
        or bundle.ledger_sha256 != store.capture_ledger().ledger_sha256
        or store.verify_content_addressed_objects() != len(bundle.responses)
    ):
        raise ValueError("Product v0.2.2.2 frozen capture integrity differs")
    total_requests = int(completion["read_only_request_count"])
    changed_plans = int(completion["changed_request_plan_count"])
    transport_retries = int(completion["transport_retry_count"])
    if (
        total_requests != len(bundle.requests)
        or total_requests > profile.maximum_request_count
        or changed_plans > profile.maximum_changed_plan_count
        or transport_retries > profile.maximum_transport_retries
    ):
        raise ValueError("Product v0.2.2.2 frozen capture counters differ")
    analysis = analyze_capture_bundle_v0222(
        private_root=private_root,
        bundle=bundle,
        index_pattern=profile.index_pattern,
        checkout_aliases=profile.checkout_aliases,
    )
    candidate_set = analysis.candidate_set
    if not candidate_set.candidates:
        raise RuntimeError(CANDIDATE_EMPTY_V0222)
    replay_sha256 = _private_json(
        replay_path,
        {
            "schema_version": (
                "ecomsre.product.opensearch-offline-analysis-iteration.v0222"
            ),
            "session_id": profile.session_id,
            "iteration_ordinal": 1,
            "source_terminal": completion["terminal"],
            "source_completion_sha256": completion["report_sha256"],
            "capture_bundle_sha256": bundle.bundle_sha256,
            "candidate_set_sha256": candidate_set.candidate_set_sha256,
            "public_structural_summary_sha256": analysis.public_summary.summary_sha256,
            "terminal": CANDIDATE_READY_V0222,
            "read_only_request_count": total_requests,
            "additional_live_request_count": 0,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
        },
    )
    capture_summary = _bound_payload(
        {
            "schema_version": "ecomsre.product.opensearch-capture-summary.v0222",
            "goal_version": "ecomsre-product-v0222-capture-first-operator-profile-v1",
            "terminal": OPERATOR_BLOCKED_V0222,
            "capture_terminal": CAPTURE_PASS_V0222,
            "candidate_set_terminal": CANDIDATE_READY_V0222,
            "initial_consumed_session_terminal": completion["terminal"],
            "initial_consumed_session_completion_sha256": completion["report_sha256"],
            "offline_analysis_iteration_count": 1,
            "offline_analysis_report_sha256": replay_sha256,
            "session_id": profile.session_id,
            "capture_bundle_sha256": bundle.bundle_sha256,
            "public_structural_summary": analysis.public_summary.model_dump(
                mode="json"
            ),
            "candidate_set_sha256": candidate_set.candidate_set_sha256,
            "candidate_count": len(candidate_set.candidates),
            "recommendation_status": candidate_set.recommendation_status.value,
            "recommended_candidate_alias": candidate_set.recommended_candidate_alias,
            "read_only_request_count": total_requests,
            "changed_request_plan_count": changed_plans,
            "transport_retry_count": transport_retries,
            "baseline_unchanged": True,
            "cleanup": "CLEAN",
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "action_authority": "NONE",
        },
        "summary_sha256",
    )
    session_result = _bound_payload(
        {
            "schema_version": "ecomsre.product.opensearch-capture-session.v0222",
            **{
                key: value
                for key, value in capture_summary.items()
                if key != "schema_version"
            },
        },
        "session_sha256",
    )
    candidate_json = candidate_set.model_dump_json(indent=2) + "\n"
    _public_text(
        repository / "config/product-v0222/opensearch/capture-session.json",
        json.dumps(session_result, indent=2, sort_keys=True) + "\n",
    )
    _public_text(
        repository / "config/product-v0222/opensearch/candidate-set.json",
        candidate_json,
    )
    _public_text(
        repository / "docs/analysis/product-v0222-capture-summary.json",
        json.dumps(capture_summary, indent=2, sort_keys=True) + "\n",
    )
    _public_text(
        repository / "docs/analysis/product-v0222-capture-summary.md",
        _render_capture_markdown(capture_summary),
    )
    _public_text(
        repository / "docs/analysis/product-v0222-candidate-set.json",
        candidate_json,
    )
    _public_text(
        repository / "docs/human-briefs/product-v0222-opensearch-profile-selection.md",
        render_operator_brief_v0222(
            candidate_set=candidate_set,
            capture_session_id=profile.session_id,
        ),
    )
    progress = _bound_payload(
        {
            "schema_version": "ecomsre.product.v0222.progress.v1",
            "goal_version": "ecomsre-product-v0222-capture-first-operator-profile-v1",
            "branch": "codex/product-v0222-capture-first-operator-profile",
            "increment": 3,
            "terminal": OPERATOR_BLOCKED_V0222,
            "capture_terminal": CAPTURE_PASS_V0222,
            "candidate_set_terminal": CANDIDATE_READY_V0222,
            "initial_consumed_session_terminal": completion["terminal"],
            "initial_consumed_session_completion_sha256": completion["report_sha256"],
            "capture_bundle_sha256": bundle.bundle_sha256,
            "candidate_set_sha256": candidate_set.candidate_set_sha256,
            "candidate_count": len(candidate_set.candidates),
            "capture_session_count": 1,
            "capture_read_only_request_count": total_requests,
            "capture_changed_request_plan_count": changed_plans,
            "transport_retry_count": transport_retries,
            "operator_selection_count": 0,
            "holdout_verification_session_count": 0,
            "offline_changed_iteration_count": 1,
            "offline_analysis_report_sha256": replay_sha256,
            "cleanup": "CLEAN",
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "action_authority": "NONE",
            "next_boundary": "STOP_FOR_REAL_OPERATOR_SELECTION",
        },
        "progress_sha256",
    )
    _public_text(
        repository / "docs/analysis/product-v0222-progress.json",
        json.dumps(progress, indent=2, sort_keys=True) + "\n",
    )
    return {
        "status": OPERATOR_BLOCKED_V0222,
        "capture_terminal": CAPTURE_PASS_V0222,
        "candidate_set_terminal": CANDIDATE_READY_V0222,
        "initial_consumed_session_terminal": completion["terminal"],
        "session_id": profile.session_id,
        "capture_session_count": 1,
        "read_only_request_count": total_requests,
        "additional_live_request_count": 0,
        "changed_request_plan_count": changed_plans,
        "transport_retry_count": transport_retries,
        "offline_changed_iteration_count": 1,
        "capture_bundle_sha256": bundle.bundle_sha256,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "candidate_count": len(candidate_set.candidates),
        "recommendation_status": candidate_set.recommendation_status.value,
        "cleanup": "CLEAN",
    }


def run_live_capture_v0222(root: Path) -> dict[str, object]:
    repository = Path(root).resolve(strict=True)
    preflight = verify_live_capture_preflight_v0222(repository)
    profile = load_capture_profile_v0222(
        repository / "config/product-v0222/opensearch/profile.json"
    )
    private_root = repository / profile.private_root
    start_path = private_root / "capture-session-start.json"
    complete_path = private_root / "capture-session-complete.json"
    _private_json(
        start_path,
        {
            "schema_version": "ecomsre.product.opensearch-capture-start.v0222",
            "session_id": profile.session_id,
            "started_at": datetime.now(UTC).isoformat(),
            "profile_sha256": profile.profile_sha256,
            "capture_session_count": 1,
            "maximum_request_count": 20,
            "maximum_changed_plan_count": 3,
            "maximum_transport_retries": 3,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
        },
    )
    store = OpenSearchCaptureStoreV0222(
        private_root=private_root,
        session_id=profile.session_id,
        maximum_response_bytes=profile.maximum_response_bytes,
    )
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=repository,
        private_root=private_root,
        stabilization_seconds=profile.stabilization_seconds,
    )
    cleanup = CleanupObservation.unknown_blocked()
    baseline_before: str | None = None
    baseline_unchanged = False
    started = False
    resolution_client: OpenSearchProbeClientV0221 | None = None
    probe_client: OpenSearchProbeClientV0221 | None = None
    failure: BaseException | None = None
    bundle = None
    analysis = None
    traffic_result: dict[str, object] | None = None
    try:
        lifecycle.admit()
        lifecycle.start()
        started = True
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if not isinstance(backend, LocalSandboxReadBackend):
            raise TypeError("Product v0.2.2.2 did not receive owned read authority")
        baseline_before = lifecycle.read_baseline_sha256()
        with httpx.Client(trust_env=False) as traffic_client:
            traffic = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=profile.healthy_traffic_profile,
            )
        traffic_result = traffic.model_dump(mode="json")
        if traffic.succeeded == 0 or traffic.stopped_on_error_budget:
            raise RuntimeError("Product v0.2.2.2 healthy traffic did not complete")
        query_ended_at = datetime.now(UTC)
        query_started_at = query_ended_at - timedelta(
            seconds=profile.recent_window_seconds
        )
        resolution_client = OpenSearchProbeClientV0221(
            base_url=backend.config.opensearch_base_url,
            maximum_request_count=20,
            maximum_response_bytes=profile.maximum_response_bytes,
            capture_store=store,
            request_count_bound=20,
            transport_retry_bound=3,
        )
        encoded_index = quote(profile.index_pattern, safe="*,-_")
        resolution_client.request_json_with_transport_retries(
            maximum_transport_retries=profile.maximum_transport_retries,
            plan_id="capture-plan-a",
            request_id="resolved-index",
            method="GET",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.INDEX_RESOLUTION,
            path=f"/_resolve/index/{encoded_index}",
            path_template="/_resolve/index/{index}",
            query_parameters={},
            json_body=None,
        )
        probe_client = OpenSearchProbeClientV0221(
            base_url=backend.config.opensearch_base_url,
            maximum_request_count=19,
            maximum_response_bytes=profile.maximum_response_bytes,
            capture_store=store,
            request_count_bound=20,
            transport_retry_bound=3,
            capture_request_ordinal_offset=1,
        )
        try:
            execute_probe_protocol_v0221(
                client=probe_client,
                index_pattern=profile.index_pattern,
                checkout_aliases=profile.checkout_aliases,
                maximum_sample_documents=profile.maximum_sample_documents,
                maximum_transport_retries=profile.protocol_transport_retries,
                started_at=query_started_at,
                ended_at=query_ended_at,
            )
        except OpenSearchProbeProtocolBlockerV0221 as error:
            if error.terminal != SCHEMA_AMBIGUOUS_V0221:
                raise
        bundle = store.build_bundle()
        if not bundle.capture_completeness:
            raise RuntimeError(CAPTURE_INCOMPLETE_V0222)
        baseline_unchanged = lifecycle.read_baseline_sha256() == baseline_before
        cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_unchanged)
        if not baseline_unchanged or cleanup.verdict != "CLEAN":
            raise RuntimeError(CAPTURE_PROTOCOL_BLOCKED_V0222)
        analysis = analyze_capture_bundle_v0222(
            private_root=private_root,
            bundle=bundle,
            index_pattern=profile.index_pattern,
            checkout_aliases=profile.checkout_aliases,
        )
        if not analysis.candidate_set.candidates:
            raise RuntimeError(CANDIDATE_EMPTY_V0222)
    except BaseException as error:
        failure = error
    finally:
        if resolution_client is not None:
            resolution_client.close()
        if probe_client is not None:
            probe_client.close()
        if started and cleanup.verdict != "CLEAN":
            if baseline_before is not None and lifecycle.controller is not None:
                try:
                    baseline_unchanged = (
                        lifecycle.read_baseline_sha256() == baseline_before
                    )
                except BaseException:
                    baseline_unchanged = False
            try:
                cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_unchanged)
            except BaseException:
                cleanup = CleanupObservation.unknown_blocked()
    total_requests = (
        0 if resolution_client is None else resolution_client.request_count
    ) + (0 if probe_client is None else probe_client.request_count)
    changed_plans = 0
    if bundle is not None:
        changed_plans = len(
            {
                request.request_plan_id
                for request in bundle.requests
                if request.request_kind.value != "INDEX_RESOLUTION"
            }
        )
    transport_retries = max(
        (
            attempt.transport_retry_count
            for client in (resolution_client, probe_client)
            if client is not None
            for attempt in client.attempts
        ),
        default=0,
    )
    if failure is None and (
        total_requests > profile.maximum_request_count
        or changed_plans > profile.maximum_changed_plan_count
        or transport_retries > profile.maximum_transport_retries
    ):
        failure = RuntimeError(CAPTURE_PROTOCOL_BLOCKED_V0222)
    if failure is not None or bundle is None or analysis is None:
        message = str(failure)[:240] if failure is not None else "capture failed"
        if CAPTURE_INCOMPLETE_V0222 in message:
            terminal = CAPTURE_INCOMPLETE_V0222
        elif CANDIDATE_EMPTY_V0222 in message:
            terminal = CANDIDATE_EMPTY_V0222
        else:
            terminal = CAPTURE_PROTOCOL_BLOCKED_V0222
        blocker_summary = _bound_payload(
            {
                "schema_version": "ecomsre.product.opensearch-capture-summary.v0222",
                "goal_version": (
                    "ecomsre-product-v0222-capture-first-operator-profile-v1"
                ),
                "terminal": terminal,
                "session_id": profile.session_id,
                "capture_session_count": 1,
                "read_only_request_count": total_requests,
                "changed_request_plan_count": changed_plans,
                "transport_retry_count": transport_retries,
                "baseline_unchanged": baseline_unchanged,
                "cleanup": cleanup.verdict,
                "fault_attempt_count": 0,
                "baseline_readiness_attempt_count": 0,
                "product_diagnosis_attempt_count": 0,
                "knowledge_loop_campaign_count": 0,
                "agent_writes": 0,
                "runbook_executions": 0,
                "action_authority": "NONE",
            },
            "summary_sha256",
        )
        _public_text(
            repository / "docs/analysis/product-v0222-capture-summary.json",
            json.dumps(blocker_summary, indent=2, sort_keys=True) + "\n",
        )
        _public_text(
            repository / "docs/analysis/product-v0222-capture-summary.md",
            _render_capture_blocker_markdown(blocker_summary),
        )
        progress = _bound_payload(
            {
                "schema_version": "ecomsre.product.v0222.progress.v1",
                "goal_version": (
                    "ecomsre-product-v0222-capture-first-operator-profile-v1"
                ),
                "branch": "codex/product-v0222-capture-first-operator-profile",
                "increment": 3,
                "terminal": terminal,
                "capture_session_count": 1,
                "capture_read_only_request_count": total_requests,
                "capture_changed_request_plan_count": changed_plans,
                "transport_retry_count": transport_retries,
                "operator_selection_count": 0,
                "holdout_verification_session_count": 0,
                "offline_changed_iteration_count": 0,
                "cleanup": cleanup.verdict,
                "fault_attempt_count": 0,
                "baseline_readiness_attempt_count": 0,
                "product_diagnosis_attempt_count": 0,
                "knowledge_loop_campaign_count": 0,
                "agent_writes": 0,
                "runbook_executions": 0,
                "action_authority": "NONE",
                "next_boundary": "STOP_WITH_CAPTURE_BLOCKER",
            },
            "progress_sha256",
        )
        _public_text(
            repository / "docs/analysis/product-v0222-progress.json",
            json.dumps(progress, indent=2, sort_keys=True) + "\n",
        )
        completion_sha = _private_json(
            complete_path,
            {
                "schema_version": "ecomsre.product.opensearch-capture-complete.v0222",
                "session_id": profile.session_id,
                "terminal": terminal,
                "completed_at": datetime.now(UTC).isoformat(),
                "safe_message": message,
                "read_only_request_count": total_requests,
                "changed_request_plan_count": changed_plans,
                "transport_retry_count": transport_retries,
                "baseline_unchanged": baseline_unchanged,
                "cleanup": cleanup.verdict,
                "fault_attempt_count": 0,
                "baseline_readiness_attempt_count": 0,
                "product_diagnosis_attempt_count": 0,
                "knowledge_loop_campaign_count": 0,
                "action_authority": "NONE",
            },
        )
        return {
            "status": terminal,
            "session_id": profile.session_id,
            "capture_session_count": 1,
            "read_only_request_count": total_requests,
            "changed_request_plan_count": changed_plans,
            "transport_retry_count": transport_retries,
            "completion_sha256": completion_sha,
            "cleanup": cleanup.verdict,
        }
    candidate_set = analysis.candidate_set
    capture_summary = _bound_payload(
        {
            "schema_version": "ecomsre.product.opensearch-capture-summary.v0222",
            "goal_version": "ecomsre-product-v0222-capture-first-operator-profile-v1",
            "terminal": OPERATOR_BLOCKED_V0222,
            "capture_terminal": CAPTURE_PASS_V0222,
            "candidate_set_terminal": CANDIDATE_READY_V0222,
            "session_id": profile.session_id,
            "capture_bundle_sha256": bundle.bundle_sha256,
            "public_structural_summary": analysis.public_summary.model_dump(
                mode="json"
            ),
            "candidate_set_sha256": candidate_set.candidate_set_sha256,
            "candidate_count": len(candidate_set.candidates),
            "recommendation_status": candidate_set.recommendation_status.value,
            "recommended_candidate_alias": candidate_set.recommended_candidate_alias,
            "read_only_request_count": total_requests,
            "changed_request_plan_count": changed_plans,
            "transport_retry_count": transport_retries,
            "baseline_unchanged": baseline_unchanged,
            "cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "action_authority": "NONE",
        },
        "summary_sha256",
    )
    session_result = _bound_payload(
        {
            "schema_version": "ecomsre.product.opensearch-capture-session.v0222",
            **{
                key: value
                for key, value in capture_summary.items()
                if key != "schema_version"
            },
            "traffic_result": traffic_result,
        },
        "session_sha256",
    )
    candidate_json = candidate_set.model_dump_json(indent=2) + "\n"
    _public_text(
        repository / "config/product-v0222/opensearch/capture-session.json",
        json.dumps(session_result, indent=2, sort_keys=True) + "\n",
    )
    _public_text(
        repository / "config/product-v0222/opensearch/candidate-set.json",
        candidate_json,
    )
    _public_text(
        repository / "docs/analysis/product-v0222-capture-summary.json",
        json.dumps(capture_summary, indent=2, sort_keys=True) + "\n",
    )
    _public_text(
        repository / "docs/analysis/product-v0222-capture-summary.md",
        _render_capture_markdown(capture_summary),
    )
    _public_text(
        repository / "docs/analysis/product-v0222-candidate-set.json", candidate_json
    )
    _public_text(
        repository / "docs/human-briefs/product-v0222-opensearch-profile-selection.md",
        render_operator_brief_v0222(
            candidate_set=candidate_set,
            capture_session_id=profile.session_id,
        ),
    )
    progress = _bound_payload(
        {
            "schema_version": "ecomsre.product.v0222.progress.v1",
            "goal_version": "ecomsre-product-v0222-capture-first-operator-profile-v1",
            "branch": "codex/product-v0222-capture-first-operator-profile",
            "increment": 3,
            "terminal": OPERATOR_BLOCKED_V0222,
            "capture_terminal": CAPTURE_PASS_V0222,
            "candidate_set_terminal": CANDIDATE_READY_V0222,
            "capture_bundle_sha256": bundle.bundle_sha256,
            "candidate_set_sha256": candidate_set.candidate_set_sha256,
            "candidate_count": len(candidate_set.candidates),
            "capture_session_count": 1,
            "capture_read_only_request_count": total_requests,
            "capture_changed_request_plan_count": changed_plans,
            "transport_retry_count": transport_retries,
            "operator_selection_count": 0,
            "holdout_verification_session_count": 0,
            "offline_changed_iteration_count": 0,
            "cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
            "action_authority": "NONE",
            "next_boundary": "STOP_FOR_REAL_OPERATOR_SELECTION",
        },
        "progress_sha256",
    )
    _public_text(
        repository / "docs/analysis/product-v0222-progress.json",
        json.dumps(progress, indent=2, sort_keys=True) + "\n",
    )
    completion_sha = _private_json(
        complete_path,
        {
            "schema_version": "ecomsre.product.opensearch-capture-complete.v0222",
            "session_id": profile.session_id,
            "terminal": OPERATOR_BLOCKED_V0222,
            "capture_terminal": CAPTURE_PASS_V0222,
            "candidate_set_terminal": CANDIDATE_READY_V0222,
            "completed_at": datetime.now(UTC).isoformat(),
            "capture_bundle_sha256": bundle.bundle_sha256,
            "candidate_set_sha256": candidate_set.candidate_set_sha256,
            "read_only_request_count": total_requests,
            "changed_request_plan_count": changed_plans,
            "transport_retry_count": transport_retries,
            "baseline_unchanged": baseline_unchanged,
            "cleanup": cleanup.verdict,
            "fault_attempt_count": 0,
            "baseline_readiness_attempt_count": 0,
            "product_diagnosis_attempt_count": 0,
            "knowledge_loop_campaign_count": 0,
            "action_authority": "NONE",
        },
    )
    return {
        "status": OPERATOR_BLOCKED_V0222,
        "capture_terminal": CAPTURE_PASS_V0222,
        "candidate_set_terminal": CANDIDATE_READY_V0222,
        "session_id": profile.session_id,
        "capture_session_count": 1,
        "read_only_request_count": total_requests,
        "changed_request_plan_count": changed_plans,
        "transport_retry_count": transport_retries,
        "capture_bundle_sha256": bundle.bundle_sha256,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "candidate_count": len(candidate_set.candidates),
        "recommendation_status": candidate_set.recommendation_status.value,
        "completion_sha256": completion_sha,
        "cleanup": cleanup.verdict,
        "preflight_status": preflight["status"],
    }


__all__ = (
    "CANDIDATE_READY_V0222",
    "CAPTURE_PASS_V0222",
    "OPERATOR_BLOCKED_V0222",
    "load_capture_profile_v0222",
    "resume_frozen_capture_analysis_v0222",
    "run_live_capture_v0222",
    "verify_live_capture_preflight_v0222",
)
