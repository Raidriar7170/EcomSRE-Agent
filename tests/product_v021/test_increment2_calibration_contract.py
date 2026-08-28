from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import ecomsre.product.pilot.live_calibration_v021 as live_calibration
import scripts.ci.verify_product_v021_increment1 as increment1_verifier
import pytest
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import (
    ProvisionalFaultDomainV23,
    ProvisionalIncidentReportV23,
)
from ecomsre.product.pilot.calibration_v021 import (
    QueueProfileV021,
    render_public_calibration_markdown_v021,
)
from ecomsre.product.pilot.live_calibration_v021 import (
    CALIBRATION_CONTRACT_READY_V021,
    READINESS_BLOCKED_V021,
    _build_public_calibration_payload_v021,
    _classify_calibration_attempt_v021,
    _exact_bound_path_v021,
    _load_exact_repository_object_v021,
    _require_absent_public_targets_v021,
    _should_continue_calibration_v021,
    _summarize_calibration_evidence_v021,
    verify_calibration_contract_v021,
    run_live_calibration_v021,
)
from ecomsre.product.pilot.contracts_v02 import PilotEpisodeTerminalV02
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    write_private_json,
)
from scripts.ci.verify_product_v021_increment1 import (
    _verify_calibration_terminal_artifacts_v021,
    _verify_queue_profile_state_v021,
)


ROOT = Path(__file__).resolve().parents[2]


def _valid_provisional_report(
    supporting_evidence_refs: tuple[str, ...] = ("o:logs", "o:metrics"),
) -> dict[str, object]:
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.provisional-incident-report.v1",
        "report_id": "report-v23-" + "1" * 16,
        "terminal": "UNREGISTERED_INCIDENT_SUSPECTED",
        "suspected_root_services": ("checkout",),
        "affected_services": ("checkout",),
        "broad_fault_domain": ProvisionalFaultDomainV23.CONCURRENCY,
        "provisional_mechanism_label": "observer-visible queue pressure",
        "mechanism_description": (
            "A bounded observer-visible queue symptom remains unregistered."
        ),
        "observed_symptoms": ("checkout queue pressure",),
        "supporting_evidence_refs": supporting_evidence_refs,
        "contradicting_evidence_refs": (),
        "unexplained_anomaly_ids": ("anomaly-1",),
        "alternative_hypotheses": ("another unregistered queue mechanism",),
        "recommended_next_observations": ("collect another bounded sample",),
        "confidence": 0.55,
        "action_authority": "NONE",
    }
    draft = ProvisionalIncidentReportV23.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return {
        **payload,
        "report_sha256": semantic_sha256_v22(
            draft.model_dump(mode="json", exclude={"report_sha256"})
        ),
    }


def test_successor_queue_profile_is_fresh_and_freezes_as_one_bound_object() -> None:
    profile = QueueProfileV021.model_validate_json(
        (ROOT / "config/product-v021/live-pilot/profile.json").read_bytes()
    )

    assert profile.candidate_values == (5, 10, 20)
    assert profile.maximum_calibration_changes == 2
    assert profile.selected_value is None
    assert profile.profile_sha256 is None
    frozen = profile.freeze(
        selected_value=5,
        selected_root_service="checkout",
        calibration_report_sha256="a" * 64,
        calibration_runtime_binding_sha256="b" * 64,
        calibrated_at="2026-08-28T00:00:00+00:00",
    )
    assert frozen.calibration_contract_sha256 == profile.contract_sha256
    assert frozen.profile_sha256 is not None
    assert QueueProfileV021.model_validate_json(frozen.model_dump_json()) == frozen


def test_calibration_contract_blocks_before_baseline_without_consuming() -> None:
    binding = ROOT / "config/product-v021/live-pilot/baseline-binding.json"
    sentinel = ROOT / ".local/product-v021/private-live-control/calibration-start.json"
    assert not binding.exists()
    before = sentinel.read_bytes() if sentinel.exists() else None

    result = verify_calibration_contract_v021(ROOT)

    assert result["terminal"] != CALIBRATION_CONTRACT_READY_V021
    assert result["terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"
    )
    assert result["baseline_binding_status"] == "ABSENT"
    assert result["calibration_execution_count"] == 0
    assert result["fault_attempt_count"] == 0
    assert result["action_authority"] == "NONE"
    after = sentinel.read_bytes() if sentinel.exists() else None
    assert after == before


def test_live_calibration_cannot_reach_lifecycle_before_readiness(monkeypatch) -> None:
    lifecycle_called = False

    def unexpected_lifecycle(**_kwargs: object) -> object:
        nonlocal lifecycle_called
        lifecycle_called = True
        raise AssertionError("lifecycle must not be constructed")

    monkeypatch.setattr(
        live_calibration,
        "_SandboxOwnedSmokeLifecycle",
        unexpected_lifecycle,
    )

    result = run_live_calibration_v021(repository_root=ROOT)

    assert result["terminal"] == READINESS_BLOCKED_V021
    assert result["calibration_execution_count"] == 0
    assert lifecycle_called is False


def test_calibration_profile_config_has_no_private_control_identifier() -> None:
    payload = json.loads(
        (ROOT / "config/product-v021/live-pilot/profile.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(payload, sort_keys=True).casefold()

    assert "kafkaqueueproblems" not in serialized
    assert "flag_key" not in serialized


def test_calibration_admits_only_sanitized_multi_source_open_world() -> None:
    diagnosis = {
        "terminal": "OPEN_WORLD",
        "provisional_report": _valid_provisional_report(),
        "supporting_evidence_refs": ["o:logs", "o:metrics"],
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    evidence = {
        "supporting_evidence_refs": ["o:logs", "o:metrics"],
        "contradicting_evidence_refs": [],
        "objects": [
            {
                "evidence_ref": "o:logs",
                "source": "LOGS",
                "payload": {
                    "connector_result": {
                        "covered_services": ["checkout"],
                        "records": [
                            {"message": "Warning: overloading queue now."}
                        ],
                    }
                },
            },
            {
                "evidence_ref": "o:metrics",
                "source": "METRICS",
                "payload": {
                    "connector_result": {"covered_services": ["checkout"]}
                },
            },
            {
                "evidence_ref": "o:runtime",
                "source": "RUNTIME",
                "payload": {
                    "connector_result": {"covered_services": ["checkout"]}
                },
            },
        ],
    }

    summary = _summarize_calibration_evidence_v021(
        diagnosis,
        evidence,
        injected_value=5,
    )

    assert summary["truth_isolation_pass"] is True
    assert summary["provisional_report_valid"] is True
    assert summary["queue_log_observed"] is True
    assert summary["runtime_root_coverage"] is True
    assert _classify_calibration_attempt_v021(
        diagnosis,
        summary,
        logical_root_services=("checkout",),
    ) is PilotEpisodeTerminalV02.PASS

    leaked = json.loads(json.dumps(evidence))
    leaked["objects"][0]["payload"]["connector_result"]["records"][0][
        "message"
    ] = "FeatureFlag 'kafkaQueueProblems' is activated"
    leaked_summary = _summarize_calibration_evidence_v021(
        diagnosis,
        leaked,
        injected_value=5,
    )
    assert leaked_summary["truth_isolation_pass"] is False
    assert _classify_calibration_attempt_v021(
        diagnosis,
        leaked_summary,
        logical_root_services=("checkout",),
    ) is PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE

    malformed_competing = dict(diagnosis)
    malformed_competing["provisional_report"] = {
        "terminal": "UNREGISTERED_INCIDENT_WITH_COMPETING_HYPOTHESES"
    }
    malformed_summary = _summarize_calibration_evidence_v021(
        malformed_competing,
        evidence,
        injected_value=5,
    )
    assert malformed_summary["provisional_report_valid"] is False
    assert _classify_calibration_attempt_v021(
        malformed_competing,
        malformed_summary,
        logical_root_services=("checkout",),
    ) is PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE


def test_calibration_rejects_unlinked_queue_log_as_support() -> None:
    diagnosis = {
        "terminal": "OPEN_WORLD",
        "provisional_report": _valid_provisional_report(
            ("o:metrics", "o:runtime")
        ),
        "supporting_evidence_refs": ["o:metrics", "o:runtime"],
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    evidence = {
        "supporting_evidence_refs": ["o:metrics", "o:runtime"],
        "contradicting_evidence_refs": [],
        "objects": [
            {
                "evidence_ref": "o:logs",
                "source": "LOGS",
                "payload": {
                    "connector_result": {
                        "covered_services": ["checkout"],
                        "records": [{"message": "Warning: overloading queue now."}],
                    }
                },
            },
            {
                "evidence_ref": "o:metrics",
                "source": "METRICS",
                "payload": {
                    "connector_result": {"covered_services": ["checkout"]}
                },
            },
            {
                "evidence_ref": "o:runtime",
                "source": "RUNTIME",
                "payload": {
                    "connector_result": {"covered_services": ["checkout"]}
                },
            },
        ],
    }

    summary = _summarize_calibration_evidence_v021(
        diagnosis,
        evidence,
        injected_value=5,
    )

    assert summary["queue_log_observed"] is False
    assert summary["support_sources"] == ("METRICS", "RUNTIME")
    assert _classify_calibration_attempt_v021(
        diagnosis,
        summary,
        logical_root_services=("checkout",),
    ) is PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE


def test_calibration_rejects_unlinked_queue_anomaly_with_linked_normal_log() -> None:
    support_refs = ("o:logs-normal", "o:metrics")
    diagnosis = {
        "terminal": "OPEN_WORLD",
        "provisional_report": _valid_provisional_report(support_refs),
        "supporting_evidence_refs": list(support_refs),
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    evidence = {
        "supporting_evidence_refs": list(support_refs),
        "contradicting_evidence_refs": [],
        "objects": [
            {
                "evidence_ref": "o:logs-normal",
                "source": "LOGS",
                "payload": {
                    "connector_result": {
                        "covered_services": ["checkout"],
                        "records": [{"message": "checkout completed"}],
                    }
                },
            },
            {
                "evidence_ref": "o:logs-unlinked-anomaly",
                "source": "LOGS",
                "payload": {
                    "connector_result": {
                        "covered_services": ["checkout"],
                        "records": [{"message": "Warning: overloading queue now."}],
                    }
                },
            },
            {
                "evidence_ref": "o:metrics",
                "source": "METRICS",
                "payload": {
                    "connector_result": {"covered_services": ["checkout"]}
                },
            },
        ],
    }

    summary = _summarize_calibration_evidence_v021(
        diagnosis,
        evidence,
        injected_value=5,
    )

    assert summary["support_sources"] == ("LOGS", "METRICS")
    assert summary["queue_log_observed"] is False
    assert _classify_calibration_attempt_v021(
        diagnosis,
        summary,
        logical_root_services=("checkout",),
    ) is PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE


def test_public_calibration_projection_hides_control_truth_and_counts_changes() -> None:
    attempts = [
        {
            "injected_value": value,
            "episode_terminal": terminal,
            "diagnosis": {
                "terminal": "OPEN_WORLD",
                "private_control": "kafkaQueueProblems",
            },
            "evidence_summary": {
                "support_sources": ("LOGS", "METRICS"),
                "queue_log_observed": True,
                "truth_isolation_pass": True,
            },
            "baseline_recovery": {"status": "PASS"},
        }
        for value, terminal in (
            (5, "PROFILE_NOT_OBSERVABLE"),
            (10, "PROFILE_NOT_OBSERVABLE"),
            (20, "PASS"),
        )
    ]

    payload = _build_public_calibration_payload_v021(
        terminal="ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS",
        observed_at="2026-08-28T00:00:00+00:00",
        attempt_results=attempts,
        selected_root_service="checkout",
        selected_profile_sha256="a" * 64,
        private_report_sha256="b" * 64,
        baseline_binding_sha256="c" * 64,
        owned_demo_cleanup="CLEAN",
        outer_baseline_restored=True,
        active_baseline_unchanged=True,
    )

    serialized = json.dumps(payload, sort_keys=True).casefold()
    assert payload["calibration_iteration_count"] == 3
    assert payload["changed_calibration_iteration_count"] == 2
    assert "injected_value" not in serialized
    assert "kafkaqueueproblems" not in serialized
    assert '"selected_value"' not in serialized


def test_increment2_verifier_accepts_only_pass_bound_frozen_profile() -> None:
    fresh = json.loads(
        (ROOT / "config/product-v021/live-pilot/profile.json").read_text(
            encoding="utf-8"
        )
    )
    frozen = QueueProfileV021.model_validate(fresh).freeze(
        selected_value=20,
        selected_root_service="checkout",
        calibration_report_sha256="a" * 64,
        calibration_runtime_binding_sha256="b" * 64,
        calibrated_at="2026-08-28T00:00:00+00:00",
    )

    _verify_queue_profile_state_v021(
        frozen.model_dump(mode="json"),
        increment=2,
        terminal="ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS",
    )

    try:
        _verify_queue_profile_state_v021(
            frozen.model_dump(mode="json"),
            increment=2,
            terminal="BLOCKED_ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE",
        )
    except ValueError as error:
        assert "queue profile" in str(error)
    else:
        raise AssertionError("a blocked calibration may not freeze the profile")


def test_calibration_resumes_exact_readiness_lifecycle_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    readiness_root = (
        tmp_path
        / ".local/product-v021/private-baseline-readiness/runs/"
        / "20260828T000000Z-deadbeef"
    )
    flag_path = readiness_root / "runtime/flagd/demo.flagd.json"
    ensure_private_directory(flag_path.parent)
    ensure_private_directory(readiness_root / "control")
    baseline = {
        "flags": {
            "kafkaQueueProblems": {
                "state": "ENABLED",
                "defaultVariant": "off",
                "variants": {"off": 0},
            }
        }
    }
    write_private_json(flag_path, baseline, create_once=True)
    upstream = tmp_path / "third_party/opentelemetry-demo/src/flagd"
    upstream.mkdir(parents=True)
    (upstream / "demo.flagd.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "config/live-telemetry-controlled-remediation-v1").mkdir(
        parents=True
    )
    observed_flag_directories: list[Path] = []

    class FakeEnvironment:
        def __init__(self, *, flagd_directory: Path, **_kwargs: object) -> None:
            observed_flag_directories.append(flagd_directory)

        def verify_local_docker(self) -> None:
            return None

        def verify_upstream(self) -> None:
            return None

        def resolve(self):
            resolved = SimpleNamespace(
                model_dump=lambda **_kwargs: {"resolved": "readiness"}
            )
            return resolved, {"resolved": "readiness"}

        def verify_cached_images(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(live_calibration, "load_bundle", lambda _path: object())
    monkeypatch.setattr(
        live_calibration,
        "build_flag_documents",
        lambda _upstream, _bundle: (baseline, {"fault": True}),
    )
    monkeypatch.setattr(live_calibration, "SandboxEnvironment", FakeEnvironment)
    expected_resolved = live_calibration.authority_semantic_sha256(
        {"resolved": "readiness"}
    )
    binding = SimpleNamespace(
        readiness_private_root=(
            ".local/product-v021/private-baseline-readiness/runs/"
            "20260828T000000Z-deadbeef"
        ),
        queue_flag_ref="runtime/flagd/demo.flagd.json",
    )

    lifecycle = live_calibration._resume_readiness_lifecycle_v021(
        repository_root=tmp_path,
        binding=cast(Any, binding),
        stabilization_seconds=0,
        expected_resolved_sandbox_sha256=expected_resolved,
    )

    assert lifecycle.private_root == readiness_root
    assert lifecycle.flag_file == flag_path
    assert observed_flag_directories == [flag_path.parent]


def test_core_or_extension_absorption_stops_calibration_immediately() -> None:
    assert (
        _should_continue_calibration_v021(
            PilotEpisodeTerminalV02.CORE_ABSORBED.value
        )
        is False
    )
    assert (
        _should_continue_calibration_v021(
            PilotEpisodeTerminalV02.EXTENSION_ABSORBED.value
        )
        is False
    )
    assert (
        _should_continue_calibration_v021(
            PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE.value
        )
        is True
    )


def test_calibration_preflight_rejects_dangling_public_target(
    tmp_path: Path,
) -> None:
    analysis = tmp_path / "docs/analysis"
    analysis.mkdir(parents=True)
    target = analysis / "product-v021-profile-calibration.json"
    target.symlink_to(analysis / "missing.json")

    try:
        _require_absent_public_targets_v021(tmp_path, (target,))
    except ValueError as error:
        assert "public" in str(error)
    else:
        raise AssertionError("dangling public target must fail before consumption")


def test_exact_bound_path_rejects_symlinked_readiness_components(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "queue.json").write_text("{}\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    try:
        _exact_bound_path_v021(
            tmp_path,
            "linked/queue.json",
            expected="file",
        )
    except ValueError as error:
        assert "symlink" in str(error)
    else:
        raise AssertionError("exact bound path must reject symlink components")


def _write_pass_calibration_artifacts(
    root: Path,
    *,
    profile_report_sha256: str = "b" * 64,
    public_binding_sha256: str = "c" * 64,
    selected_value: int = 5,
) -> tuple[dict[str, object], QueueProfileV021, object]:
    profile = QueueProfileV021.model_validate_json(
        (ROOT / "config/product-v021/live-pilot/profile.json").read_bytes()
    ).freeze(
        selected_value=selected_value,
        selected_root_service="checkout",
        calibration_report_sha256=profile_report_sha256,
        calibration_runtime_binding_sha256="d" * 64,
        calibrated_at="2026-08-28T00:00:00+00:00",
    )
    public_payload = _build_public_calibration_payload_v021(
        terminal="ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS",
        observed_at="2026-08-28T00:00:00+00:00",
        attempt_results=(
            {
                "episode_terminal": "PASS",
                "diagnosis": {"terminal": "OPEN_WORLD"},
                "evidence_summary": {
                    "support_sources": ("LOGS", "METRICS"),
                    "queue_log_observed": True,
                    "corroborating_source_available": True,
                    "runtime_root_coverage": True,
                    "evidence_refs_resolve": True,
                    "provisional_report_valid": True,
                    "truth_isolation_pass": True,
                },
                "baseline_recovery": {"status": "PASS"},
            },
        ),
        selected_root_service="checkout",
        selected_profile_sha256=profile.profile_sha256,
        private_report_sha256="b" * 64,
        baseline_binding_sha256=public_binding_sha256,
        owned_demo_cleanup="CLEAN",
        outer_baseline_restored=True,
        active_baseline_unchanged=True,
    )
    analysis = root / "docs/analysis"
    analysis.mkdir(parents=True)
    (analysis / "product-v021-profile-calibration.json").write_text(
        json.dumps(public_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (analysis / "product-v021-profile-calibration.md").write_text(
        render_public_calibration_markdown_v021(public_payload),
        encoding="utf-8",
    )
    progress: dict[str, object] = {
        "terminal": "ECOMSRE_PRODUCT_V021_UNKNOWN_FAULT_PROFILE_PASS",
        "profile_calibration_iteration_count": 1,
        "profile_calibration_changed_iteration_count": 0,
        "calibration_execution_count": 1,
    }
    binding = SimpleNamespace(
        binding_sha256="c" * 64,
        runtime_authority_sha256="d" * 64,
    )
    return progress, profile, binding


def _rewrite_public_calibration_payload(
    root: Path,
    payload: dict[str, object],
) -> None:
    payload.pop("report_sha256", None)
    payload["report_sha256"] = semantic_sha256_v22(payload)
    analysis = root / "docs/analysis"
    (analysis / "product-v021-profile-calibration.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (analysis / "product-v021-profile-calibration.md").write_text(
        render_public_calibration_markdown_v021(payload),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "artifact_name",
    (
        "product-v021-profile-calibration.json",
        "product-v021-profile-calibration.md",
    ),
)
def test_calibration_terminal_verifier_rejects_missing_public_artifact(
    tmp_path: Path,
    monkeypatch,
    artifact_name: str,
) -> None:
    progress, profile, binding = _write_pass_calibration_artifacts(tmp_path)
    (tmp_path / "docs/analysis" / artifact_name).unlink()
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public calibration"):
        _verify_calibration_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )


@pytest.mark.parametrize("tamper", ("plain tamper", "kafkaQueueProblems"))
def test_calibration_terminal_verifier_rejects_markdown_tamper_or_leak(
    tmp_path: Path,
    monkeypatch,
    tamper: str,
) -> None:
    progress, profile, binding = _write_pass_calibration_artifacts(tmp_path)
    path = tmp_path / "docs/analysis/product-v021-profile-calibration.md"
    path.write_text(path.read_text(encoding="utf-8") + tamper, encoding="utf-8")
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public calibration"):
        _verify_calibration_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )


@pytest.mark.parametrize(
    ("profile_report_sha256", "public_binding_sha256"),
    (("e" * 64, "c" * 64), ("b" * 64, "f" * 64)),
)
def test_calibration_terminal_verifier_cross_binds_profile_report_and_baseline(
    tmp_path: Path,
    monkeypatch,
    profile_report_sha256: str,
    public_binding_sha256: str,
) -> None:
    progress, profile, binding = _write_pass_calibration_artifacts(
        tmp_path,
        profile_report_sha256=profile_report_sha256,
        public_binding_sha256=public_binding_sha256,
    )
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public calibration"):
        _verify_calibration_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )


def test_calibration_terminal_verifier_accepts_bound_pass_semantics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    progress, profile, binding = _write_pass_calibration_artifacts(tmp_path)
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    _verify_calibration_terminal_artifacts_v021(
        tmp_path,
        progress=progress,
        profile=profile,
    )


@pytest.mark.parametrize(
    ("field", "drifted"),
    (
        ("diagnosis_terminal", "NO_INCIDENT"),
        ("queue_log_observed", False),
        ("support_sources", []),
        ("corroborating_source_available", False),
        ("runtime_root_coverage", False),
        ("evidence_refs_resolve", False),
        ("provisional_report_valid", False),
    ),
)
def test_calibration_terminal_verifier_rejects_semantic_pass_forgery(
    tmp_path: Path,
    monkeypatch,
    field: str,
    drifted: object,
) -> None:
    progress, profile, binding = _write_pass_calibration_artifacts(tmp_path)
    path = tmp_path / "docs/analysis/product-v021-profile-calibration.json"
    payload = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    attempts = cast(list[dict[str, object]], payload["attempts"])
    attempts[-1][field] = drifted
    _rewrite_public_calibration_payload(tmp_path, payload)
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public calibration PASS"):
        _verify_calibration_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )


def test_calibration_terminal_verifier_binds_selected_value_to_candidate_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    progress, profile, binding = _write_pass_calibration_artifacts(
        tmp_path,
        selected_value=20,
    )
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public calibration PASS"):
        _verify_calibration_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )


def test_calibration_preflight_rejects_symlinked_progress_snapshot(
    tmp_path: Path,
) -> None:
    analysis = tmp_path / "docs/analysis"
    analysis.mkdir(parents=True)
    actual = tmp_path / "actual-progress.json"
    actual.write_text("{}\n", encoding="utf-8")
    (analysis / "product-v021-progress.json").symlink_to(actual)

    with pytest.raises(ValueError, match="exact bound"):
        _load_exact_repository_object_v021(
            tmp_path,
            "docs/analysis/product-v021-progress.json",
        )


@pytest.mark.parametrize(
    ("field", "drifted"),
    (
        ("schema_version", "garbage"),
        ("observed_at", "not-a-timestamp"),
        ("observed_at", "2026-08-28T00:00:01+00:00"),
    ),
)
def test_calibration_terminal_verifier_binds_schema_and_calibration_timestamp(
    tmp_path: Path,
    monkeypatch,
    field: str,
    drifted: str,
) -> None:
    progress, profile, binding = _write_pass_calibration_artifacts(tmp_path)
    path = tmp_path / "docs/analysis/product-v021-profile-calibration.json"
    payload = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    payload[field] = drifted
    _rewrite_public_calibration_payload(tmp_path, payload)
    monkeypatch.setattr(
        increment1_verifier,
        "load_pilot_baseline_binding_v021",
        lambda _path: binding,
    )

    with pytest.raises(ValueError, match="public calibration"):
        _verify_calibration_terminal_artifacts_v021(
            tmp_path,
            progress=progress,
            profile=profile,
        )
