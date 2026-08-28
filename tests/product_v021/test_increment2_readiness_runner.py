from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

import ecomsre.product.pilot.live_baseline_readiness_v021 as live_readiness
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
    PilotBaselineBindingV021,
    ReadinessChangeParameterV021,
    ReadinessFailureDomainV021,
    ReadinessSemanticInputsV021,
    build_readiness_attempt_signature_v021,
    verify_queue_default_v021,
)
from ecomsre.product.pilot.live_baseline_readiness_v021 import (
    reserve_readiness_attempt_v021,
    verify_baseline_readiness_contract_v021,
)
from ecomsre.product.pilot.readiness_attempts_v021 import (
    READINESS_BLOCKED_V021,
    READINESS_REPAIR_REQUIRED_V021,
    load_public_readiness_attempt_v021,
    write_public_readiness_attempt_v021,
    write_readiness_attempt_final_v021,
)


ROOT = Path(__file__).resolve().parents[2]


def _flag_document(default_variant: str = "off") -> bytes:
    return (
        "{\n"
        '  "flags": {\n'
        '    "kafkaQueueProblems": {\n'
        f'      "defaultVariant": "{default_variant}",\n'
        '      "state": "ENABLED",\n'
        '      "variants": {"off": 0, "on": 5}\n'
        "    }\n"
        "  }\n"
        "}\n"
    ).encode("utf-8")


def test_queue_default_verifier_is_read_only_and_sha_bound(tmp_path) -> None:
    runtime = tmp_path / "demo.flagd.json"
    original = _flag_document()
    runtime.write_bytes(original)

    observation = verify_queue_default_v021(runtime, expected_default_value=0)

    assert observation.default_value == 0
    assert observation.unchanged is True
    assert runtime.read_bytes() == original
    assert observation.before_sha256 == observation.after_sha256


def test_queue_default_verifier_rejects_nondefault_and_symlink(tmp_path) -> None:
    runtime = tmp_path / "demo.flagd.json"
    runtime.write_bytes(_flag_document(default_variant="on"))
    with pytest.raises(ValueError, match="default"):
        verify_queue_default_v021(runtime, expected_default_value=0)

    target = tmp_path / "target.json"
    target.write_bytes(_flag_document())
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        verify_queue_default_v021(link, expected_default_value=0)


def test_healthy_traffic_is_local_bounded_and_hash_bound() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    profile = HealthyTrafficProfileV021(
        request_seed=501,
        maximum_request_count=3,
        requests_per_second=2.0,
        error_budget=2,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = BoundedHealthyCheckoutTrafficV021(
            client=client,
            sleep=lambda _seconds: None,
        ).run(endpoint="http://127.0.0.1:18080/api/checkout", profile=profile)

    assert result.attempted == result.succeeded == 3
    assert result.failed == 0
    assert seen == ["/api/cart", "/api/checkout"] * 3
    assert len(result.result_sha256) == 64

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="local checkout"):
            BoundedHealthyCheckoutTrafficV021(client=client).run(
                endpoint="https://example.com/api/checkout",
                profile=profile,
            )


def test_healthy_traffic_counts_request_errors_against_the_budget() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("owned checkout unavailable", request=request)
        return httpx.Response(200, json={"ok": True})

    profile = HealthyTrafficProfileV021(
        request_seed=501,
        maximum_request_count=3,
        requests_per_second=2.0,
        error_budget=2,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = BoundedHealthyCheckoutTrafficV021(
            client=client,
            sleep=lambda _seconds: None,
        ).run(endpoint="http://127.0.0.1:18080/api/checkout", profile=profile)

    assert result.attempted == 3
    assert result.failed == 1
    assert result.succeeded == 2
    assert result.stopped_on_error_budget is False


def _semantics(
    *,
    required_source_policy_id: str = "GLOBAL_AVAILABLE_TARGET_COMPLETE_V1",
) -> ReadinessSemanticInputsV021:
    return ReadinessSemanticInputsV021.build(
        profile_sha256="a" * 64,
        candidate_services=("checkout",),
        build_mode="DEMO_ONLY",
        lookback_seconds=180,
        window_count=5,
        warmup_seconds=180,
        stabilization_seconds=60,
        baseline_accumulation_seconds=360,
        minimum_successful_windows=4,
        healthy_traffic_maximum_request_count=180,
        healthy_traffic_request_seed=501,
        healthy_traffic_error_budget=12,
        healthy_traffic_requests_per_second=0.5,
        connector_query_bindings_sha256="b" * 64,
        connector_query_templates_sha256="c" * 64,
        service_alias_mapping_sha256="d" * 64,
        required_source_policy_id=required_source_policy_id,
    )


def _initial_signature():
    return build_readiness_attempt_signature_v021(
        semantic_inputs=_semantics(),
        changed_parameter=ReadinessChangeParameterV021.INITIAL,
    )


def _write_final(
    private_root: Path,
    *,
    run_number: int,
    changed_attempt_number: int,
    run_id: str,
    signature_sha256: str,
    changed_parameter: ReadinessChangeParameterV021,
    disposition: str,
    failure_domain: str,
    audit_sha256: str | None,
    rejection_reason_codes: tuple[str, ...],
    usable_audit: bool,
    replacement_for: str | None = None,
    terminal: str = READINESS_REPAIR_REQUIRED_V021,
) -> None:
    write_readiness_attempt_final_v021(
        private_root=private_root,
        payload={
            "schema_version": "ecomsre.product.readiness-attempt-final.v021",
            "run_number": run_number,
            "changed_attempt_number": changed_attempt_number,
            "run_id": run_id,
            "attempt_signature_sha256": signature_sha256,
            "changed_parameter": changed_parameter.value,
            "infrastructure_replacement_for_run_id": replacement_for,
            "terminal": terminal,
            "disposition": disposition,
            "failure_domain": failure_domain,
            "audit_sha256": audit_sha256,
            "rejection_reason_codes": list(rejection_reason_codes),
            "scheduled_window_count": 5 if usable_audit else 0,
            "usable_audit": usable_audit,
            "queue_default_unchanged": True,
            "outer_baseline_restored": True,
            "owned_demo_cleanup": "CLEAN",
            "failure_before_cleanup_sha256": "e" * 64,
            "private_attempt_report_sha256": "f" * 64,
            "public_attempt_report_sha256": "1" * 64,
            "interrupted": False,
            "action_authority": "NONE",
            "action_authority_violations": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
        },
    )


def test_readiness_signature_is_semantic_not_implementation_byte_based() -> None:
    first = _initial_signature()
    second = _initial_signature()
    changed = build_readiness_attempt_signature_v021(
        semantic_inputs=_semantics(
            required_source_policy_id="BASELINE_QUERY_ELIGIBLE_TARGET_COMPLETE_V1"
        ),
        changed_parameter=(
            ReadinessChangeParameterV021.TARGET_COMPLETE_CAPABILITY_DECLARATION
        ),
        prior_audit_sha256="2" * 64,
        prior_rejection_reason_codes=("REQUIRED_SOURCE_MISSING",),
    )

    assert first == second
    assert changed.attempt_signature_sha256 != first.attempt_signature_sha256


def test_live_entry_verifies_contract_before_reserving_an_attempt(monkeypatch) -> None:
    reservation_called = False

    def reject_contract(_root: Path) -> dict[str, object]:
        raise ValueError("frozen contract differs")

    def unexpected_reservation(**_kwargs: object) -> int:
        nonlocal reservation_called
        reservation_called = True
        return 1

    monkeypatch.setattr(
        live_readiness,
        "verify_baseline_readiness_contract_v021",
        reject_contract,
    )
    monkeypatch.setattr(
        live_readiness,
        "reserve_readiness_attempt_v021",
        unexpected_reservation,
    )

    with pytest.raises(ValueError, match="frozen contract differs"):
        live_readiness.run_live_baseline_readiness_v021(repository_root=ROOT)

    assert reservation_called is False


@pytest.mark.parametrize(
    "linked_relative_path",
    (
        "docs/analysis",
        ".local/product-v021/private-baseline-readiness",
        ".local/product-v021/baseline-readiness",
    ),
)
def test_live_entry_rejects_symlinked_roots_before_reserving_an_attempt(
    tmp_path: Path,
    monkeypatch,
    linked_relative_path: str,
) -> None:
    reservation_called = False
    profile_payload = json.loads(
        (ROOT / "config/product-v021/baseline-readiness/profile.json").read_text(
            encoding="utf-8"
        )
    )
    (tmp_path / "config/product-v021/live-pilot").mkdir(parents=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    if linked_relative_path != "docs/analysis":
        (tmp_path / "docs/analysis").mkdir()
    (tmp_path / ".local/product-v021").mkdir(parents=True)
    alternate = tmp_path / (
        "alternate-" + linked_relative_path.replace("/", "-").lstrip(".")
    )
    alternate.mkdir()
    linked = tmp_path / linked_relative_path
    linked.parent.mkdir(parents=True, exist_ok=True)
    linked.symlink_to(alternate, target_is_directory=True)

    monkeypatch.setattr(
        live_readiness,
        "verify_baseline_readiness_contract_v021",
        lambda _root: {},
    )
    monkeypatch.setattr(
        live_readiness,
        "_load_object_v021",
        lambda _path: profile_payload,
    )

    def unexpected_reservation(**_kwargs: object) -> int:
        nonlocal reservation_called
        reservation_called = True
        return 1

    monkeypatch.setattr(
        live_readiness,
        "reserve_readiness_attempt_v021",
        unexpected_reservation,
    )

    with pytest.raises(ValueError, match="symlink"):
        live_readiness.run_live_baseline_readiness_v021(
            repository_root=tmp_path
        )

    assert reservation_called is False


def test_live_entry_rejects_dangling_binding_before_reserving_an_attempt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    reservation_called = False
    profile_payload = json.loads(
        (ROOT / "config/product-v021/baseline-readiness/profile.json").read_text(
            encoding="utf-8"
        )
    )
    binding_parent = tmp_path / "config/product-v021/live-pilot"
    binding_parent.mkdir(parents=True)
    (binding_parent / "baseline-binding.json").symlink_to("missing.json")
    (tmp_path / "docs/analysis").mkdir(parents=True)
    (tmp_path / ".local/product-v021").mkdir(parents=True)

    monkeypatch.setattr(
        live_readiness,
        "verify_baseline_readiness_contract_v021",
        lambda _root: {},
    )
    monkeypatch.setattr(
        live_readiness,
        "_load_object_v021",
        lambda _path: profile_payload,
    )

    def unexpected_reservation(**_kwargs: object) -> int:
        nonlocal reservation_called
        reservation_called = True
        return 1

    monkeypatch.setattr(
        live_readiness,
        "reserve_readiness_attempt_v021",
        unexpected_reservation,
    )

    with pytest.raises(ValueError, match="already exists or is a symlink"):
        live_readiness.run_live_baseline_readiness_v021(
            repository_root=tmp_path
        )

    assert reservation_called is False


def test_check_only_contract_does_not_consume_a_live_attempt() -> None:
    result = verify_baseline_readiness_contract_v021(ROOT)

    assert result["terminal"] == "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_CONTRACT_PASS"
    assert result["pinned_upstream"] == "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    assert result["maximum_changed_attempts"] == 2
    assert result["action_authority"] == "NONE"


def test_repository_cli_defaults_to_check_only_without_pythonpath() -> None:
    evidence_root = ROOT / ".local/product-v021/private-baseline-readiness"
    before = tuple(sorted(evidence_root.glob("attempts/run-*-start.json")))
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.product_v021.run_baseline_readiness",
        ),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    result = json.loads(completed.stdout)

    assert result["terminal"] == "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_CONTRACT_PASS"
    assert tuple(sorted(evidence_root.glob("attempts/run-*-start.json"))) == before


def test_attempt_ledger_allows_only_audit_bound_semantic_r2(tmp_path) -> None:
    initial = _initial_signature()
    first = reserve_readiness_attempt_v021(
        private_root=tmp_path,
        signature=initial,
        run_id="run-first",
        started_at="2026-08-28T00:00:00+00:00",
    )
    assert first.run_number == first.changed_attempt_number == 1
    _write_final(
        tmp_path,
        run_number=1,
        changed_attempt_number=1,
        run_id="run-first",
        signature_sha256=initial.attempt_signature_sha256,
        changed_parameter=ReadinessChangeParameterV021.INITIAL,
        disposition="TARGETED_REPAIR_ELIGIBLE",
        failure_domain="CAMPAIGN",
        audit_sha256="2" * 64,
        rejection_reason_codes=("REQUIRED_SOURCE_MISSING",),
        usable_audit=True,
    )
    comment_only = build_readiness_attempt_signature_v021(
        semantic_inputs=_semantics(),
        changed_parameter=(
            ReadinessChangeParameterV021.TARGET_COMPLETE_CAPABILITY_DECLARATION
        ),
        prior_audit_sha256="2" * 64,
        prior_rejection_reason_codes=("REQUIRED_SOURCE_MISSING",),
    )
    with pytest.raises(ValueError, match="declared parameter"):
        reserve_readiness_attempt_v021(
            private_root=tmp_path,
            signature=comment_only,
            run_id="run-comment-only",
            started_at="2026-08-28T00:01:00+00:00",
        )
    changed = build_readiness_attempt_signature_v021(
        semantic_inputs=_semantics(
            required_source_policy_id="BASELINE_QUERY_ELIGIBLE_TARGET_COMPLETE_V1"
        ),
        changed_parameter=(
            ReadinessChangeParameterV021.TARGET_COMPLETE_CAPABILITY_DECLARATION
        ),
        prior_audit_sha256="2" * 64,
        prior_rejection_reason_codes=("REQUIRED_SOURCE_MISSING",),
    )
    second = reserve_readiness_attempt_v021(
        private_root=tmp_path,
        signature=changed,
        run_id="run-second",
        started_at="2026-08-28T00:02:00+00:00",
    )
    assert second.run_number == second.changed_attempt_number == 2


def test_attempt_ledger_allows_one_identical_infrastructure_replacement(
    tmp_path,
) -> None:
    initial = _initial_signature()
    first = reserve_readiness_attempt_v021(
        private_root=tmp_path,
        signature=initial,
        run_id="run-first",
        started_at="2026-08-28T00:00:00+00:00",
    )
    _write_final(
        tmp_path,
        run_number=1,
        changed_attempt_number=1,
        run_id="run-first",
        signature_sha256=initial.attempt_signature_sha256,
        changed_parameter=ReadinessChangeParameterV021.INITIAL,
        disposition="INFRASTRUCTURE_REPLACEMENT_ELIGIBLE",
        failure_domain="INFRASTRUCTURE_STARTUP",
        audit_sha256=None,
        rejection_reason_codes=(),
        usable_audit=False,
    )
    replacement = reserve_readiness_attempt_v021(
        private_root=tmp_path,
        signature=initial,
        run_id="run-replacement",
        started_at="2026-08-28T00:01:00+00:00",
        infrastructure_replacement_for_run_id=first.run_id,
    )
    assert replacement.run_number == 2
    assert replacement.changed_attempt_number == 1
    assert replacement.infrastructure_replacement_for_run_id == "run-first"
    _write_final(
        tmp_path,
        run_number=2,
        changed_attempt_number=1,
        run_id="run-replacement",
        signature_sha256=initial.attempt_signature_sha256,
        changed_parameter=ReadinessChangeParameterV021.INITIAL,
        disposition="BLOCKED",
        failure_domain="INFRASTRUCTURE_STARTUP",
        audit_sha256=None,
        rejection_reason_codes=(),
        usable_audit=False,
        replacement_for="run-first",
        terminal=READINESS_BLOCKED_V021,
    )
    with pytest.raises(ValueError, match="not eligible"):
        reserve_readiness_attempt_v021(
            private_root=tmp_path,
            signature=initial,
            run_id="run-replacement-two",
            started_at="2026-08-28T00:02:00+00:00",
            infrastructure_replacement_for_run_id="run-replacement",
        )


def test_attempt_reservation_rejects_nested_symlink_escape(tmp_path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (private / "attempts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="regular directory"):
        reserve_readiness_attempt_v021(
            private_root=private,
            signature=_initial_signature(),
            run_id="run-first",
            started_at="2026-08-28T00:00:00+00:00",
        )
    assert tuple(outside.iterdir()) == ()


def _public_attempt_payload() -> dict[str, object]:
    return {
        "schema_version": "ecomsre.product.public-baseline-readiness-attempt.v021",
        "run_number": 1,
        "changed_attempt_number": 1,
        "attempt_signature_sha256": "a" * 64,
        "changed_parameter": "INITIAL",
        "infrastructure_replacement": False,
        "terminal": READINESS_BLOCKED_V021,
        "disposition": "BLOCKED",
        "failure_domain": "INTERRUPTED",
        "observed_at": "2026-08-28T00:00:00+00:00",
        "environment_id": None,
        "baseline_id": None,
        "baseline_sha256": None,
        "baseline_active": False,
        "audit": None,
        "audit_sha256": None,
        "parity_sha256": None,
        "scheduled_window_count": 0,
        "accepted_window_count": 0,
        "traffic_result": None,
        "queue_default_unchanged": False,
        "healthy_traffic_stopped": False,
        "api_restart_verified": False,
        "worker_restart_verified": False,
        "outer_baseline_restored": False,
        "owned_demo_cleanup": "CLEAN",
        "baseline_job_safe_error_code": None,
        "safe_error_type": "KeyboardInterrupt",
        "private_report_sha256": "b" * 64,
        "failure_before_cleanup_sha256": "c" * 64,
        "fault_attempt_count": 0,
        "action_authority": "NONE",
        "action_authority_violations": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
    }


def test_public_attempt_loader_rejects_tamper_and_symlink(tmp_path) -> None:
    path = tmp_path / "attempt.json"
    write_public_readiness_attempt_v021(path, _public_attempt_payload())
    assert load_public_readiness_attempt_v021(path).run_number == 1

    tampered = json.loads(path.read_text())
    tampered["private_flag_key"] = "must-not-leak"
    body = dict(tampered)
    body.pop("report_sha256")
    tampered["report_sha256"] = semantic_sha256_v22(body)
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError):
        load_public_readiness_attempt_v021(path)

    target = tmp_path / "private.json"
    target.write_text(json.dumps(tampered), encoding="utf-8")
    link = tmp_path / "linked-attempt.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        load_public_readiness_attempt_v021(link)


def test_live_preflight_rejects_preplanted_next_public_attempt(tmp_path) -> None:
    analysis_root = tmp_path / "docs/analysis"
    analysis_root.mkdir(parents=True)
    (analysis_root / "product-v021-baseline-readiness-attempt-1.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="file set differs"):
        live_readiness._verify_public_attempt_history_v021(tmp_path, (), ())


def test_public_readiness_progress_binds_zero_authority_violations(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "docs/analysis"
    analysis_root.mkdir(parents=True)
    normalized = write_public_readiness_attempt_v021(
        analysis_root / "product-v021-baseline-readiness-attempt-1.json",
        _public_attempt_payload(),
    )

    live_readiness._write_public_readiness_v021(
        repository_root=tmp_path,
        terminal=READINESS_BLOCKED_V021,
        observed_at=datetime(2026, 8, 28, tzinfo=UTC),
        normalized_attempt=normalized,
    )

    progress = json.loads(
        (analysis_root / "product-v021-progress.json").read_text(
            encoding="utf-8"
        )
    )
    supplied = progress.pop("progress_sha256")
    assert progress["action_authority_violations"] == 0
    assert supplied == semantic_sha256_v22(progress)


def test_readiness_pass_rejects_any_truncated_baseline_query() -> None:
    audit: dict[str, object] = {
        "scheduled_window_count": 5,
        "accepted_window_count": 4,
        "final_builder_would_pass": True,
        "windows": [
            {
                "source_results": [
                    {
                        "truncated": False,
                    }
                ]
            }
            for _ordinal in range(5)
        ],
    }
    pass_inputs = {
        "safe_error": None,
        "baseline": {"active": True},
        "audit": audit,
        "verification": {"status": "SUCCEEDED"},
        "identity_sha256": "a" * 64,
        "connector_configuration_sha256": "b" * 64,
        "capability_matrix_sha256": "d" * 64,
        "runtime_binding_sha256": "c" * 64,
        "api_restart_verified": True,
        "worker_restart_verified": True,
        "queue_default_unchanged": True,
        "healthy_traffic_stopped": True,
        "outer_baseline_restored": True,
        "cleanup_status": "CLEAN",
    }

    assert live_readiness._readiness_pass_preconditions_v021(**pass_inputs) is True
    windows = audit["windows"]
    assert isinstance(windows, list)
    first_window = windows[0]
    assert isinstance(first_window, dict)
    source_results = first_window["source_results"]
    assert isinstance(source_results, list)
    first_result = source_results[0]
    assert isinstance(first_result, dict)
    first_result["truncated"] = True

    assert live_readiness._readiness_pass_preconditions_v021(**pass_inputs) is False


def test_successor_environment_enables_observer_only_log_projection() -> None:
    payload = live_readiness._build_environment_payload_v021(
        ROOT,
        candidate_services=("checkout",),
        runtime_authority_sha256="a" * 64,
    )
    connectors = payload["connector_configs"]
    assert isinstance(connectors, list)
    logs = next(
        item
        for item in connectors
        if isinstance(item, dict) and item.get("kind") == "OPENSEARCH"
    )
    settings = logs["settings"]
    assert isinstance(settings, dict)
    assert settings["message_projection_policy"] == "OBSERVER_SYMPTOM_V1"


def test_frozen_baseline_binding_includes_reusable_product_state() -> None:
    payload: dict[str, object] = {
        "schema_version": "ecomsre.product.pilot-baseline-binding.v021",
        "terminal": "ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS",
        "environment_id": "env-" + "1" * 24,
        "product_data_root": (
            ".local/product-v021/baseline-readiness/20260828T000000Z-deadbeef"
        ),
        "readiness_private_root": (
            ".local/product-v021/private-baseline-readiness/runs/"
            "20260828T000000Z-deadbeef"
        ),
        "queue_flag_ref": "runtime/flagd/demo.flagd.json",
        "runtime_snapshot_ref": "pilot/runtime-readiness.json",
        "baseline_id": "base-" + "2" * 24,
        "baseline_sha256": "3" * 64,
        "build_policy": {
            "schema_version": "ecomsre.product.baseline-build-policy.v1",
            "mode": "DEMO_ONLY",
            "lookback_seconds": 180,
            "window_count": 5,
            "minimum_successful_windows": 4,
            "warmup_seconds": 180,
        },
        "accepted_window_ordinals": [1, 2, 3, 4],
        "source_coverage_matrix": {"METRICS": {"checkout": 4}},
        "service_identity_map_sha256": "4" * 64,
        "connector_configuration_sha256": "5" * 64,
        "capability_matrix_sha256": "6" * 64,
        "runtime_authority_sha256": "7" * 64,
        "healthy_traffic_profile_sha256": "8" * 64,
        "audit_sha256": "9" * 64,
        "parity_sha256": "a" * 64,
        "frozen_at": "2026-08-28T00:00:00+00:00",
    }
    binding = PilotBaselineBindingV021.build(
        **{
            key: value
            for key, value in payload.items()
            if key not in {"schema_version", "terminal"}
        }
    )

    assert binding.environment_id == "env-" + "1" * 24
    assert binding.product_data_root.startswith(
        ".local/product-v021/baseline-readiness/"
    )
    assert binding.readiness_private_root.endswith(
        "20260828T000000Z-deadbeef"
    )
    drifted = binding.model_dump(mode="json")
    drifted["product_data_root"] = (
        ".local/product-v021/baseline-readiness/20260828T000001Z-deadbeef"
    )
    with pytest.raises(ValueError, match="binding|digest"):
        PilotBaselineBindingV021.model_validate(drifted)


def test_keyboard_interrupt_is_sealed_before_cleanup_and_blocks(tmp_path, monkeypatch) -> None:
    (tmp_path / "config/product-v021/baseline-readiness").mkdir(parents=True)
    (tmp_path / "config/product-v021/live-pilot").mkdir(parents=True)
    (tmp_path / "docs/analysis").mkdir(parents=True)
    shutil.copy(
        ROOT / "config/product-v021/baseline-readiness/profile.json",
        tmp_path / "config/product-v021/baseline-readiness/profile.json",
    )
    cleanup_observations: list[bool] = []

    class InterruptingLifecycle:
        flag_file = None

        def __init__(self, *, private_root: Path, **_kwargs: object) -> None:
            self.private_root = private_root

        def admit(self) -> None:
            raise KeyboardInterrupt()

        def cleanup_owned(self, *, baseline_unchanged: bool) -> SimpleNamespace:
            del baseline_unchanged
            cleanup_observations.append(
                (self.private_root / "report/failure-before-cleanup.json").is_file()
            )
            return SimpleNamespace(verdict="CLEAN")

    monkeypatch.setattr(
        live_readiness,
        "verify_baseline_readiness_contract_v021",
        lambda _root: {"terminal": "PASS"},
    )
    monkeypatch.setattr(
        live_readiness,
        "_build_readiness_semantic_inputs_v021",
        lambda _root, _profile: _semantics(),
    )
    monkeypatch.setattr(
        live_readiness,
        "_SandboxOwnedSmokeLifecycle",
        InterruptingLifecycle,
    )

    result = live_readiness.run_live_baseline_readiness_v021(
        repository_root=tmp_path,
    )

    assert result["terminal"] == READINESS_BLOCKED_V021
    assert cleanup_observations == [True]
    latest = result["latest_attempt"]
    assert isinstance(latest, dict)
    assert latest["failure_domain"] == (
        ReadinessFailureDomainV021.INTERRUPTED.value
    )
