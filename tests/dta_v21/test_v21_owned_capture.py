from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ecomsre.dta_v2.v21.capture_campaign import (
    CalibrationKindV21,
    CaptureConditionV21,
    OperationalFamilyV21,
    build_default_capture_plan_v21,
)
from ecomsre.dta_v2.v21.contracts import (
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
)
from ecomsre.dta_v2.v21.evaluation_contracts import EvaluationSplitV21
from ecomsre.dta_v2.v21.owned_capture import (
    ExactFlagDocumentControllerV21,
    OwnedCaptureLifecycleV21,
    _ad_cpu_fault_measurable_v21,
    _international_checkout_payload_v21,
    _shipping_fault_measurable_v21,
    build_capture_flag_document_v21,
    build_evaluator_truth_v21,
)
from ecomsre.dta_v2.tool_contracts import RuntimeState, SpanStatus


ROOT = Path(__file__).resolve().parents[2]
BASE_HEAD = "c0a541fec48f11b02dc2cd6ba41673a777e55eee"


def _upstream() -> dict[str, object]:
    return json.loads(
        (ROOT / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json").read_text(
            encoding="utf-8"
        )
    )


def test_v21_flag_projection_changes_only_five_exact_default_variants() -> None:
    upstream = _upstream()
    changed = build_capture_flag_document_v21(
        upstream,
        load_vus=10,
        payment_variant="75%",
        email_variant="100x",
        ad_cpu_variant="on",
        shipping_variant="5sec",
    )

    expected = deepcopy(upstream)
    selections = {
        "loadGeneratorVUs": "10",
        "paymentFailure": "75%",
        "emailMemoryLeak": "100x",
        "adHighCpu": "on",
        "intlShippingSlowdown": "5sec",
    }
    expected_flags = expected["flags"]
    assert isinstance(expected_flags, dict)
    for name, variant in selections.items():
        flag = expected_flags[name]
        assert isinstance(flag, dict)
        flag["defaultVariant"] = variant
    assert changed == expected

    controller = ExactFlagDocumentControllerV21.__new__(ExactFlagDocumentControllerV21)
    controller.upstream = upstream
    controller._require_allowed_document(changed)
    changed_flags = changed["flags"]
    assert isinstance(changed_flags, dict)
    cart_failure = changed_flags["cartFailure"]
    assert isinstance(cart_failure, dict)
    cart_failure["defaultVariant"] = "on"
    with pytest.raises(ValueError, match="unauthorized"):
        controller._require_allowed_document(changed)


def test_evaluator_truth_maps_every_family_without_entering_visible_case() -> None:
    plan = build_default_capture_plan_v21(base_head=BASE_HEAD)
    truth = {case.case_id: build_evaluator_truth_v21(case) for case in plan.cases}

    assert len(truth) == 20
    assert truth["dta21-case-006"].expected_mechanism is (
        FaultMechanismV21.CPU_SATURATION
    )
    assert truth["dta21-case-006"].expected_runbook is (
        RunbookIdV21.MITIGATE_CPU_SATURATION
    )
    assert truth["dta21-case-008"].expected_mechanism is (
        FaultMechanismV21.SERVICE_UNAVAILABLE
    )
    assert truth["dta21-case-010"].expected_mechanism is (
        FaultMechanismV21.DEPENDENCY_LATENCY
    )
    assert truth["dta21-case-011"].expected_terminal is TerminalV21.COMPLETED
    assert truth["dta21-case-012"].expected_terminal is (TerminalV21.NEED_MORE_EVIDENCE)
    assert truth["dta21-case-020"].expected_terminal is TerminalV21.ABSTAIN
    assert all(
        item.split
        is (
            EvaluationSplitV21.DEVELOPMENT
            if int(case_id.rsplit("-", 1)[-1]) <= 12
            else EvaluationSplitV21.HELD_OUT
        )
        for case_id, item in truth.items()
    )
    assert (
        next(case for case in plan.cases if case.case_id == "dta21-case-012").condition
        is CaptureConditionV21.RECOVERY_TRANSITION
    )
    assert (
        next(
            case for case in plan.cases if case.case_id == "dta21-case-005"
        ).operational_family
        is OperationalFamilyV21.RECOMMENDATION_UNAVAILABLE
    )


def test_ad_cpu_capacity_uses_exact_owned_container_online_cpus() -> None:
    class _DockerApi:
        def get_json(self, path: str) -> dict[str, object]:
            assert path == "/containers/owned-ad-container/stats?stream=false"
            return {"cpu_stats": {"online_cpus": 12}}

    docker = SimpleNamespace(
        docker=_DockerApi(),
        _owned_container_identity=lambda service: (
            "owned-ad-container" if service == "ad" else None
        ),
    )
    lifecycle = OwnedCaptureLifecycleV21.__new__(OwnedCaptureLifecycleV21)
    lifecycle.backend = cast(Any, SimpleNamespace(docker=docker))

    assert lifecycle._cpu_capacity_percent("ad") == 1200.0
    with pytest.raises(RuntimeError, match="lacks owned service"):
        lifecycle._cpu_capacity_percent("unknown")


def test_calibration_impact_thresholds_bind_distribution_separation() -> None:
    assert _ad_cpu_fault_measurable_v21(baseline_p95=1.8, fault_p95=412.0)
    assert not _ad_cpu_fault_measurable_v21(baseline_p95=20.0, fault_p95=60.0)
    assert _shipping_fault_measurable_v21(
        baseline_business_latency_ms=2.0,
        fault_business_latency_ms=5_750.0,
        baseline_trace_latency_ms=50.0,
        fault_trace_latency_ms=5_100.0,
    )
    assert not _shipping_fault_measurable_v21(
        baseline_business_latency_ms=2.0,
        fault_business_latency_ms=5_750.0,
        baseline_trace_latency_ms=50.0,
        fault_trace_latency_ms=130.0,
    )


def test_shipping_probe_uses_exact_upstream_canada_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _international_checkout_payload_v21(
        repository_root=ROOT, user_id="dta21-test-user"
    )
    assert payload["userId"] == "dta21-test-user"
    address = payload["address"]
    assert isinstance(address, dict)
    assert address["country"] == "Canada"

    lifecycle = OwnedCaptureLifecycleV21.__new__(OwnedCaptureLifecycleV21)
    lifecycle.repository_root = ROOT
    requests: list[tuple[str, dict[str, object], float]] = []
    monkeypatch.setattr(
        lifecycle,
        "_post_frontend_json",
        lambda *, path, payload, timeout_seconds: requests.append(
            (path, dict(payload), timeout_seconds)
        ),
    )
    monotonic = iter((100.0, 105.25))
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.owned_capture.time.monotonic",
        lambda: next(monotonic),
    )

    assert lifecycle._international_checkout_probe() == 5_250.0
    assert [item[0] for item in requests] == ["/api/cart", "/api/checkout"]
    checkout_address = requests[1][1]["address"]
    assert isinstance(checkout_address, dict)
    assert checkout_address["country"] == "Canada"

    unpatched = OwnedCaptureLifecycleV21.__new__(OwnedCaptureLifecycleV21)
    with pytest.raises(ValueError, match="outside the allowlist"):
        unpatched._post_frontend_json(
            path="/api/products", payload={}, timeout_seconds=1.0
        )


def test_shipping_probe_http_is_loopback_path_and_size_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, bytes, dict[str, str]]] = []

    class _Response:
        status = 200

        def read(self, maximum: int) -> bytes:
            assert maximum == 1_000_001
            return b"{}"

    class _Connection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert (host, port, timeout) == ("127.0.0.1", 18080, 20.0)

        def request(
            self, method: str, path: str, body: bytes, headers: dict[str, str]
        ) -> None:
            requests.append((method, path, body, headers))

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            pass

    lifecycle = OwnedCaptureLifecycleV21.__new__(OwnedCaptureLifecycleV21)
    lifecycle.flag_controller = cast(
        Any,
        SimpleNamespace(
            endpoints=SimpleNamespace(frontend="http://127.0.0.1:18080")
        ),
    )
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.owned_capture.http.client.HTTPConnection", _Connection
    )

    lifecycle._post_frontend_json(
        path="/api/checkout", payload={"userId": "bounded"}, timeout_seconds=20.0
    )

    assert requests == [
        (
            "POST",
            "/api/checkout",
            b'{"userId":"bounded"}',
            {"Accept": "application/json", "Content-Type": "application/json"},
        )
    ]


def test_service_unavailable_calibration_binds_caller_impact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = OwnedCaptureLifecycleV21.__new__(OwnedCaptureLifecycleV21)
    lifecycle.active_condition = None
    lifecycle.calibration_step_v21 = "BEGIN"
    lifecycle.fault_operation_count = 0
    lifecycle.unavailable_business_anchor_v21 = {}
    controller = SimpleNamespace(stop=lambda: None)
    monkeypatch.setattr(lifecycle, "_service", lambda service: controller)
    monkeypatch.setattr(
        lifecycle,
        "_runtime_record",
        lambda service: SimpleNamespace(state=RuntimeState.EXITED),
    )
    monkeypatch.setattr(
        lifecycle,
        "_trace_records",
        lambda *, service, started_at, ended_at: (
            (
                SimpleNamespace(
                    status=SpanStatus.ERROR,
                    first_error_location=True,
                ),
            )
            if service == "checkout"
            else ()
        ),
    )
    monkeypatch.setattr(lifecycle, "_metric_value", lambda **kwargs: 0.0)
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.owned_capture.time.sleep", lambda seconds: None
    )

    observation = lifecycle.calibrate(
        kind=CalibrationKindV21.SERVICE_UNAVAILABLE,
        target_service="email",
        variant="STOPPED",
    )

    assert observation.target_runtime_stopped is True
    assert observation.business_impact_observed is True
    assert observation.business_impact_service == "checkout"
    assert observation.measurable is True
    assert lifecycle.unavailable_business_anchor_v21 == {"email": "checkout"}
