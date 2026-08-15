from __future__ import annotations

import hashlib
import operator
from datetime import datetime, timedelta, timezone

import pytest

from ecomsre.dta_v2.evidence_store import (
    build_canonical_request_envelope,
    build_evidence_store_snapshot,
)
from ecomsre.dta_v2.read_tools import (
    BackendResult,
    FakeReadBackend,
    InvestigationReadTools,
)
from ecomsre.dta_v2.telemetry_adapters import (
    LocalReadBackendConfig,
    LocalSandboxReadBackend,
    UrllibLoopbackJsonTransport,
)
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    EndpointState,
    HealthState,
    LogSeverity,
    MetricKind,
    MetricRecord,
    MetricUnit,
    ObservationStatus,
    ResourceSample,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    SpanRelationship,
    SpanStatus,
    ToolErrorCode,
    TraceNeighborhoodRecord,
    TruthIsolationError,
    assert_truth_isolated,
    build_fake_read_authority,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.unicode_confusables_v15 import (
    DIRECT_ASCII_HEX_CONFUSABLE_COUNT,
    DIRECT_ASCII_HEX_CONFUSABLES,
    DIRECT_ASCII_HEX_CONFUSABLES_CANONICAL_BYTE_COUNT,
    DIRECT_ASCII_HEX_CONFUSABLES_CANONICAL_SHA256,
    UNICODE_CONFUSABLES_SOURCE_SHA256,
    UNICODE_CONFUSABLES_VERSION,
)


RUN_ID = "5" * 32
START = datetime(2026, 8, 16, 4, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=2)


def test_unicode_15_direct_ascii_hex_confusables_provenance() -> None:
    assert UNICODE_CONFUSABLES_VERSION == "15.0.0"
    assert UNICODE_CONFUSABLES_SOURCE_SHA256 == (
        "2b10130885c3370b101c52d7baedc452ab7f0e257b86c1e52ee657ecfc29ce64"
    )
    assert DIRECT_ASCII_HEX_CONFUSABLE_COUNT == 270
    assert len(DIRECT_ASCII_HEX_CONFUSABLES) == 270
    assert DIRECT_ASCII_HEX_CONFUSABLES[0x03DC] == "f"
    assert tuple(DIRECT_ASCII_HEX_CONFUSABLES[codepoint] for codepoint in (
        0x13AA,
        0x13F4,
        0x13DF,
        0x13A0,
        0x13AC,
    )) == ("a", "b", "c", "d", "e")
    assert DIRECT_ASCII_HEX_CONFUSABLES[0xA4D3] == "d"
    canonical_bytes = "".join(
        f"{codepoint:06X}:{mapped_ascii_lower}\n"
        for codepoint, mapped_ascii_lower in sorted(
            DIRECT_ASCII_HEX_CONFUSABLES.items()
        )
    ).encode("utf-8")
    assert len(canonical_bytes) == DIRECT_ASCII_HEX_CONFUSABLES_CANONICAL_BYTE_COUNT
    assert hashlib.sha256(canonical_bytes).hexdigest() == (
        DIRECT_ASCII_HEX_CONFUSABLES_CANONICAL_SHA256
    )
    with pytest.raises(TypeError):
        operator.setitem(DIRECT_ASCII_HEX_CONFUSABLES, 0x03DC, "a")


class StaticBackend:
    def __init__(self, result: BackendResult) -> None:
        self.result = result
        self.authority = FakeReadBackend.healthy().authority

    def execute(self, request: object) -> BackendResult:
        del request
        return self.result


def _metric_request():
    return build_query_metrics_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT),
        max_results=2,
    )


def test_dispatch_revalidates_forged_request_instance_from_dump() -> None:
    request = _metric_request()
    forged = request.model_copy(update={"service": "recommendation"})
    tools = InvestigationReadTools(run_id=RUN_ID, backend=FakeReadBackend.healthy())

    with pytest.raises(ValueError, match="request"):
        tools.dispatch(forged)
    assert tools.snapshot().dispatch_count == 0


def test_nested_forged_result_and_request_mismatch_fail_as_schema_not_truth() -> None:
    request = _metric_request()
    valid = MetricRecord(
        service="payment",
        metric_kind=MetricKind.ERROR_RATE,
        value=0.1,
        unit=MetricUnit.RATIO,
        sample_count=3,
    )
    forged = valid.model_copy(update={"service": "recommendation"})
    tools = InvestigationReadTools(
        run_id=RUN_ID, backend=StaticBackend(BackendResult(records=(forged,)))
    )
    observation = tools.dispatch(request)

    assert observation.status is ObservationStatus.FAILURE
    assert observation.error_code is ToolErrorCode.SOURCE_SCHEMA_INVALID


@pytest.mark.parametrize(
    "records",
    (
        (
            MetricRecord(
                service="payment",
                metric_kind=MetricKind.ERROR_RATE,
                value=1.0,
                unit=MetricUnit.COUNT,
                sample_count=1,
            ),
            MetricRecord(
                service="payment",
                metric_kind=MetricKind.REQUEST_SUPPORT,
                value=1.0,
                unit=MetricUnit.COUNT,
                sample_count=1,
            ),
        ),
        (
            MetricRecord(
                service="payment",
                metric_kind=MetricKind.REQUEST_SUPPORT,
                value=1.0,
                unit=MetricUnit.COUNT,
                sample_count=1,
            ),
            MetricRecord(
                service="payment",
                metric_kind=MetricKind.ERROR_RATE,
                value=0.1,
                unit=MetricUnit.RATIO,
                sample_count=1,
            ),
        ),
    ),
)
def test_request_specific_metric_unit_and_order_are_enforced(records) -> None:
    tools = InvestigationReadTools(
        run_id=RUN_ID, backend=StaticBackend(BackendResult(records=records))
    )
    observation = tools.dispatch(_metric_request())
    assert observation.status is ObservationStatus.FAILURE
    assert observation.error_code is ToolErrorCode.SOURCE_SCHEMA_INVALID


@pytest.mark.parametrize(
    "leak",
    (
        "ｅｘｐｅｃｔｅｄ\u200b－ｒｏｏｔ payment",
        "paymentFailure．defaultVariant",
        "ｆｅａｔｕｒｅ＿ｆｌａｇ＿ｋｅｙ",
        "injected\u2060variant",
        "１００％",
    ),
)
def test_unicode_and_invisible_truth_bypasses_fail_closed(leak: str) -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=5,
    )
    observation = InvestigationReadTools(
        run_id=RUN_ID, backend=FakeReadBackend.with_log_message(leak)
    ).dispatch(request)
    assert observation.status is ObservationStatus.FAILURE
    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION


def test_json_escaped_control_character_fails_truth_isolation_closed() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=5,
    )
    observation = InvestigationReadTools(
        run_id=RUN_ID, backend=FakeReadBackend.with_log_message("error\u0001signal")
    ).dispatch(request)
    assert observation.status is ObservationStatus.FAILURE
    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION


def test_snapshot_resolves_canonical_request_and_duplicate_lineage() -> None:
    request = _metric_request()
    tools = InvestigationReadTools(run_id=RUN_ID, backend=FakeReadBackend.healthy())
    first = tools.dispatch(request)
    duplicate = tools.dispatch(request)
    snapshot = tools.snapshot()

    assert len(snapshot.request_envelopes) == 1
    assert snapshot.request_envelopes[0].request_sha256 == request.normalized_request_sha256
    assert duplicate.duplicate_of_request_sha256 == first.request_sha256

    forged = first.model_copy(
        update={"request_sha256": "f" * 64, "artifact_sha256": "0" * 64}
    )
    with pytest.raises(ValueError):
        build_evidence_store_snapshot(
            run_id=RUN_ID,
            authority=tools.backend.authority,
            request_envelopes=snapshot.request_envelopes,
            observations=(forged,),
        )


def test_snapshot_rejects_unobserved_resolver_envelope() -> None:
    request = _metric_request()
    tools = InvestigationReadTools(run_id=RUN_ID, backend=FakeReadBackend.healthy())
    tools.dispatch(request)
    snapshot = tools.snapshot()
    extra = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=2,
    )
    with pytest.raises(ValueError, match="envelope"):
        build_evidence_store_snapshot(
            run_id=RUN_ID,
            authority=tools.backend.authority,
            request_envelopes=(
                *snapshot.request_envelopes,
                build_canonical_request_envelope(extra),
            ),
            observations=snapshot.observations,
        )


@pytest.mark.parametrize(
    ("tool_request", "record"),
    (
        (
            build_search_logs_request(
                run_id=RUN_ID,
                service="payment",
                started_at=START,
                ended_at=END,
                max_records=1,
            ),
            DiagnosticLogRecord(
                observed_at=END,
                service="recommendation",
                severity=LogSeverity.ERROR,
                message="bounded error",
            ),
        ),
        (
            build_trace_neighborhood_request(
                run_id=RUN_ID,
                service="payment",
                started_at=START,
                ended_at=END,
                max_spans=1,
            ),
            TraceNeighborhoodRecord(
                anchor_service="recommendation",
                service_path=("recommendation",),
                relationship=SpanRelationship.ROOT,
                service="recommendation",
                parent_service=None,
                operation="request",
                status=SpanStatus.ERROR,
                duration_ms=1.0,
                first_error_location=True,
            ),
        ),
        (
            build_inspect_service_runtime_request(
                run_id=RUN_ID, services=("payment",), max_results=1
            ),
            RuntimeRecord(
                logical_service="recommendation",
                owned_container_present=True,
                state=RuntimeState.RUNNING,
                health=HealthState.HEALTHY,
                restart_count=0,
                exit_code=None,
                endpoint_probe_performed=False,
                endpoint_state=EndpointState.UNKNOWN,
            ),
        ),
        (
            build_inspect_resource_usage_request(
                run_id=RUN_ID,
                services=("payment",),
                sampling_window_seconds=2,
                sample_count=3,
            ),
            ResourceUsageRecord(
                logical_service="payment",
                sampling_window_seconds=2,
                samples=(
                    ResourceSample(offset_ms=0, cpu_percent=1.0, memory_bytes=1),
                    ResourceSample(offset_ms=500, cpu_percent=1.0, memory_bytes=2),
                    ResourceSample(offset_ms=2000, cpu_percent=1.0, memory_bytes=3),
                ),
                memory_slope_bytes_per_second=1.0,
            ),
        ),
    ),
)
def test_all_nonmetric_tools_reject_request_result_mismatch(
    tool_request, record
) -> None:
    observation = InvestigationReadTools(
        run_id=RUN_ID, backend=StaticBackend(BackendResult(records=(record,)))
    ).dispatch(tool_request)
    assert observation.status is ObservationStatus.FAILURE
    assert observation.error_code is ToolErrorCode.SOURCE_SCHEMA_INVALID


def test_resource_long_finite_float_tokens_are_not_text_identities() -> None:
    request = build_inspect_resource_usage_request(
        run_id=RUN_ID,
        services=("payment",),
        sampling_window_seconds=2,
        sample_count=3,
    )
    record = ResourceUsageRecord(
        logical_service="payment",
        sampling_window_seconds=2,
        samples=(
            ResourceSample(
                offset_ms=0,
                cpu_percent=0.3333333333333333,
                memory_bytes=1,
            ),
            ResourceSample(
                offset_ms=1000,
                cpu_percent=1.6666666666666667,
                memory_bytes=2,
            ),
            ResourceSample(
                offset_ms=2000,
                cpu_percent=2.3333333333333335,
                memory_bytes=3,
            ),
        ),
        memory_slope_bytes_per_second=0.3333333333333333,
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)
    snapshot = tools.snapshot()

    assert observation.status is ObservationStatus.SUCCESS
    assert observation.error_code is None
    assert snapshot.observations == (observation,)


def test_identity_fragments_in_string_sequence_fail_closed() -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(["01234567", "89abcdef"])


def test_identity_fragments_at_matching_nested_record_path_fail_closed() -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(
            [{"message": "01234567"}, {"message": "89abcdef"}]
        )


def test_identity_fragments_across_different_record_fields_fail_closed() -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(
            [{"message": "01234567"}, {"detail": "89abcdef"}]
        )


def test_uuid_fragments_across_left_and_right_fields_fail_closed() -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(
            {
                "left": "550e8400-e29b",
                "right": "41d4-a716-446655440000",
            }
        )


@pytest.mark.parametrize(
    "leak",
    (
        "span=0123-4567-89ab-cdef",
        "550e8400.e29b.41d4.a716.446655440000",
    ),
)
def test_punctuation_separated_identities_fail_closed(leak: str) -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(leak)


@pytest.mark.parametrize(
    "leak",
    (
        "01|23|45|67|89|ab|cd|ef",
        "01 23 45 67 89 ab cd ef",
        "012,345,678,9ab,cde,f",
    ),
)
def test_arbitrary_separator_identity_tokens_fail_closed(leak: str) -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(leak)


def test_combining_marks_cannot_split_an_identity_token() -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(
            "01\u030723\u030745\u030767\u030789\u0307ab\u0307cd\u0307ef"
        )


def test_typed_dense_confusable_log_fails_closed() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=1,
    )
    record = DiagnosticLogRecord(
        observed_at=START,
        service="payment",
        severity=LogSeverity.ERROR,
        message="trace=0123456789aɑԁαаβ",
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


def test_typed_log_pipe_separated_identity_fails_closed() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=1,
    )
    record = DiagnosticLogRecord(
        observed_at=START,
        service="payment",
        severity=LogSeverity.ERROR,
        message="01|23|45|67|89|ab|cd|ef",
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


@pytest.mark.parametrize(
    "message",
    (
        "retry at 2026-08-16 12:34:56.789",
        "retry at 2026/08/16 12:34:56.789",
        "retry at 2026-08-16 12:34:56,789",
    ),
)
def test_typed_retry_timestamp_is_not_an_identity(message: str) -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=1,
    )
    record = DiagnosticLogRecord(
        observed_at=START,
        service="payment",
        severity=LogSeverity.ERROR,
        message=message,
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.status is ObservationStatus.SUCCESS
    assert observation.error_code is None
    assert tools.snapshot().observations == (observation,)


def test_build_version_date_is_not_an_identity() -> None:
    assert_truth_isolated("build 2026.08.16.12345678 failed")


@pytest.mark.parametrize(
    "diagnostic",
    (
        "retry at 2026/08/16 12:34:56.789",
        "retry at 2026-08-16 12:34:56,789",
    ),
)
def test_extended_decimal_dates_are_not_identities(diagnostic: str) -> None:
    assert_truth_isolated(diagnostic)


@pytest.mark.parametrize(
    "label",
    (
        "trɑceId",
        "trаceId",
        "spɑnId",
        "contɑinerId",
    ),
)
def test_confusable_inline_identity_labels_disable_date_exemption(
    label: str,
) -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(f"{label}=2026/08/16 12:34:56,789")


def test_typed_confusable_trace_label_disables_date_exemption() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=1,
    )
    record = DiagnosticLogRecord(
        observed_at=START,
        service="payment",
        severity=LogSeverity.ERROR,
        message="trɑceId=2026/08/16 12:34:56,789",
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


@pytest.mark.parametrize(
    "key",
    (
        "trɑceId",
        "trаceId",
        "traсeId",
        "tracеId",
        "spɑnId",
        "contɑinerId",
    ),
)
def test_confusable_structural_identity_keys_disable_date_exemption(
    key: str,
) -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated({key: "2026/08/16 12:34:56,789"})


def test_repeated_date_run_under_later_trace_label_fails_closed() -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(
            "retry at 2026-08-16 12:34:56.789; "
            "trace=2026-08-16 12:34:56.789"
        )


def test_typed_repeated_date_run_under_later_trace_label_fails_closed() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=1,
    )
    record = DiagnosticLogRecord(
        observed_at=START,
        service="payment",
        severity=LogSeverity.ERROR,
        message=(
            "retry at 2026-08-16 12:34:56.789; "
            "trace=2026-08-16 12:34:56.789"
        ),
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


@pytest.mark.parametrize(
    "leak",
    (
        "id=2026-08-16 12:34:56.789",
        "trace=2026.08.16.12345678",
        "span: 2026-08-16 12:34:56.789",
        "span[id]=2026-08-16 12:34:56.789",
        "container_id=2026.08.16.12345678",
        "id=2026/08/16 12:34:56.789",
        "trace=2026-08-16 12:34:56,789",
    ),
)
def test_identity_labels_disable_decimal_date_exemption(leak: str) -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated(leak)


def test_structural_trace_id_key_disables_decimal_date_exemption() -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated({"trace_id": "2026-08-16 12:34:56.789"})


@pytest.mark.parametrize(
    "key",
    ("traceId", "spanId", "containerId", "trace-id", "trace.id"),
)
def test_canonical_structural_identity_keys_disable_date_exemption(key: str) -> None:
    with pytest.raises(TruthIsolationError, match="opaque identity"):
        assert_truth_isolated({key: "2026-08-16 12:34:56.789"})


@pytest.mark.parametrize("key", ("invalid", "traceable"))
def test_structural_identity_key_near_misses_remain_diagnostics(key: str) -> None:
    assert_truth_isolated({key: "2026-08-16 12:34:56.789"})


def test_typed_json_trace_id_date_fails_closed() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=1,
    )
    record = DiagnosticLogRecord(
        observed_at=START,
        service="payment",
        severity=LogSeverity.ERROR,
        message='{"traceId":"2026-08-16 12:34:56.789"}',
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


@pytest.mark.parametrize(
    "diagnostic",
    (
        "valid=2026-08-16 12:34:56.789",
        "invalid=2026-08-16 12:34:56.789",
        "solid=2026.08.16.12345678",
    ),
)
def test_id_suffix_in_ordinary_word_does_not_disable_date_exemption(
    diagnostic: str,
) -> None:
    assert_truth_isolated(diagnostic)


def test_mixed_prose_numeric_log_leaves_do_not_form_an_identity() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=4,
    )
    records = tuple(
        DiagnosticLogRecord(
            observed_at=START + timedelta(seconds=index),
            service="payment",
            severity=LogSeverity.ERROR,
            message=message,
        )
        for index, message in enumerate(
            ("error code 1234", "5678", "9012", "3456")
        )
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=records)),
    )

    observation = tools.dispatch(request)

    assert observation.status is ObservationStatus.SUCCESS
    assert observation.error_code is None
    assert tools.snapshot().observations == (observation,)


def test_typed_log_service_and_message_identity_fragments_fail_closed() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="deadbeef",
        started_at=START,
        ended_at=END,
        max_records=1,
    )
    record = DiagnosticLogRecord(
        observed_at=START,
        service="deadbeef",
        severity=LogSeverity.ERROR,
        message="cafebabe",
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


def test_typed_log_message_identity_fragments_fail_closed() -> None:
    request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=2,
    )
    records = (
        DiagnosticLogRecord(
            observed_at=START,
            service="payment",
            severity=LogSeverity.ERROR,
            message="01234567",
        ),
        DiagnosticLogRecord(
            observed_at=START + timedelta(seconds=1),
            service="payment",
            severity=LogSeverity.ERROR,
            message="89abcdef",
        ),
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=records)),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


def test_typed_trace_service_path_identity_fragments_fail_closed() -> None:
    request = build_trace_neighborhood_request(
        run_id=RUN_ID,
        service="cafebabe",
        started_at=START,
        ended_at=END,
        max_spans=1,
    )
    record = TraceNeighborhoodRecord(
        anchor_service="cafebabe",
        service_path=("deadbeef", "cafebabe"),
        relationship=SpanRelationship.CHILD,
        service="cafebabe",
        parent_service="deadbeef",
        operation="request",
        status=SpanStatus.ERROR,
        duration_ms=1.0,
        first_error_location=True,
    )
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,))),
    )

    observation = tools.dispatch(request)

    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert tools.snapshot().observations == (observation,)


def test_runtime_request_limit_must_cover_requested_service_set() -> None:
    with pytest.raises(ValueError, match="cover"):
        build_inspect_service_runtime_request(
            run_id=RUN_ID,
            services=("payment", "recommendation"),
            max_results=1,
        )


class _OriginDriftResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        del args

    def geturl(self) -> str:
        return "http://127.0.0.1:19999/drift"

    def read(self, maximum: int) -> bytes:
        del maximum
        return b"{}"


class _OriginDriftOpener:
    def open(self, request, timeout):
        del request, timeout
        return _OriginDriftResponse()


def test_http_transport_rejects_final_origin_drift() -> None:
    transport = UrllibLoopbackJsonTransport(
        timeout_seconds=1.0, opener=_OriginDriftOpener()
    )
    with pytest.raises(RuntimeError, match="origin"):
        transport.request_json(
            base_url="http://127.0.0.1:19090",
            path="/api/v1/query",
            method="GET",
            payload=None,
        )


def test_backend_cannot_claim_truncation_below_request_limit() -> None:
    tool_request = build_search_logs_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_records=5,
    )
    record = DiagnosticLogRecord(
        observed_at=END,
        service="payment",
        severity=LogSeverity.ERROR,
        message="bounded error",
    )
    observation = InvestigationReadTools(
        run_id=RUN_ID,
        backend=StaticBackend(BackendResult(records=(record,), truncated=True)),
    ).dispatch(tool_request)
    assert observation.status is ObservationStatus.FAILURE
    assert observation.error_code is ToolErrorCode.SOURCE_SCHEMA_INVALID


def test_local_backend_revalidates_forged_config_instance() -> None:
    config = LocalReadBackendConfig(
        prometheus_base_url="http://127.0.0.1:19090",
        opensearch_base_url="http://127.0.0.1:19200",
        jaeger_base_url="http://127.0.0.1:11686",
        opensearch_index="otel-logs-*",
        docker_endpoint="unix:///var/run/docker.sock",
        compose_project="ecomsre-live-sandbox-v1",
        sandbox_label_key="io.ecomsre.sandbox.id",
        sandbox_label_value="sandbox-opaque",
        timeout_seconds=3.0,
        authority=build_fake_read_authority(),
    )
    forged = config.model_copy(
        update={"prometheus_base_url": "https://example.com"}
    )
    with pytest.raises(ValueError, match="loopback"):
        LocalSandboxReadBackend(
            config=forged, http=StaticBackend(BackendResult(records=())), docker=object()
        )

    forged_socket = config.model_copy(
        update={"docker_endpoint": "tcp://127.0.0.1:2375"}
    )
    with pytest.raises(ValueError, match="Unix"):
        LocalSandboxReadBackend(
            config=forged_socket,
            http=StaticBackend(BackendResult(records=())),
            docker=object(),
        )
