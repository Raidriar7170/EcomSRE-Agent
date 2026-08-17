from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ecomsre.dta_v2.v21.capture_campaign import (
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
    build_capture_flag_document_v21,
    build_evaluator_truth_v21,
)


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
