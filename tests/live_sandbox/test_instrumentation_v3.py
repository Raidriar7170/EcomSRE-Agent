from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ecomsre_live_sandbox import instrumentation_v2
from ecomsre_live_sandbox.instrumentation_v2 import (
    SourceProbeResult,
    SourceProbeStatus,
    V2_LIFECYCLE,
    V3_LIFECYCLE,
    build_instrumentation_report,
    load_instrumentation_config,
    public_projection,
    resolve_private_root,
    verify_public_result,
)


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
CONFIG = Path("config/live-telemetry-instrumentation-v3")


def _available(source: str, backend: str, prefix: str) -> SourceProbeResult:
    return SourceProbeResult(
        source=source,
        backend_kind=backend,
        status=SourceProbeStatus.AVAILABLE,
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        probe_started_at=NOW + timedelta(seconds=45),
        probe_ended_at=NOW + timedelta(seconds=46),
        attempt_count=1,
        backend_reachable=True,
        raw_response_count=1,
        parsed_record_count=1,
        target_record_count=1,
        service_catalog_count=1,
        target_service_present=True,
        identity_fields_present=("service.name",),
        raw_artifact_hashes={"raw.json": "a" * 64},
        evidence_refs=(f"{prefix}:0001",),
        invalid_ref_count=0,
    )


def test_v3_lifecycle_profile_binds_new_branch_config_verdict_and_outputs() -> None:
    config = load_instrumentation_config(CONFIG)
    assert V3_LIFECYCLE.version == "live-telemetry-instrumentation-v3"
    assert V3_LIFECYCLE.config_relative == CONFIG
    assert V3_LIFECYCLE.branch == "feature/live-telemetry-instrumentation-v3"
    assert V3_LIFECYCLE.private_root_name == "live-telemetry-instrumentation-v3"
    assert V3_LIFECYCLE.success_verdict == "LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E"
    assert config.environment.version == V3_LIFECYCLE.version
    assert config.reporting.public_result_json == "docs/results/live-telemetry-instrumentation-v3.json"
    assert config.reporting.public_result_markdown == "docs/results/live-telemetry-instrumentation-v3.md"
    assert config.reporting.public_human_brief == "docs/results/live-telemetry-instrumentation-v3-human-brief.md"


def test_v3_private_root_is_create_once_bound_and_rejects_the_v2_root(
    tmp_path: Path,
) -> None:
    v2_root = tmp_path / V2_LIFECYCLE.private_root_name
    v2_root.mkdir(mode=0o755)
    sentinel = v2_root / "sentinel.json"
    sentinel.write_text("v2 evidence", encoding="utf-8")
    sentinel.chmod(0o644)
    original_root_mode = v2_root.stat().st_mode & 0o777
    original_file_mode = sentinel.stat().st_mode & 0o777
    with pytest.raises(ValueError, match="different lifecycle"):
        resolve_private_root(
            v2_root,
            repository_root=Path.cwd(),
            lifecycle=V3_LIFECYCLE,
        )
    assert v2_root.stat().st_mode & 0o777 == original_root_mode
    assert sentinel.stat().st_mode & 0o777 == original_file_mode
    assert set(v2_root.iterdir()) == {sentinel}

    v3_root = tmp_path / V3_LIFECYCLE.private_root_name
    roots = resolve_private_root(
        v3_root,
        repository_root=Path.cwd(),
        lifecycle=V3_LIFECYCLE,
    )
    binding_path = roots.root / "lifecycle-binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    assert binding == {
        "branch": V3_LIFECYCLE.branch,
        "schema_version": "live-telemetry.private-root-binding.v1",
        "version": V3_LIFECYCLE.version,
    }
    assert binding_path.stat().st_mode & 0o777 == 0o600
    assert resolve_private_root(
        v3_root,
        repository_root=Path.cwd(),
        lifecycle=V3_LIFECYCLE,
    ).root == roots.root


def test_v3_report_and_public_verifier_recompute_the_versioned_verdict() -> None:
    config = load_instrumentation_config(CONFIG)
    report = build_instrumentation_report(
        version=V3_LIFECYCLE.version,
        environment_id="opentelemetry-demo-local-v1",
        sandbox_binding_sha256="b" * 64,
        resolved_compose_sha256="c" * 64,
        target_service="payment",
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        ingestion_grace_seconds=15,
        metrics=_available("METRICS", "PROMETHEUS_HTTP_API", "metric"),
        logs=_available("LOGS", "OPENSEARCH_HTTP_API", "log"),
        traces=_available("TRACES", "JAEGER_QUERY_API", "trace"),
        all_refs_resolve=True,
        canonical_preflight=True,
        cleanup={
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        },
    )
    assert report.version == V3_LIFECYCLE.version
    assert report.final_verdict == V3_LIFECYCLE.success_verdict
    public = public_projection(report, claim_boundary=config.reporting.claim_boundary)
    assert public["schema_version"] == "live-telemetry-instrumentation-v3.public.v1"
    assert public["verdict"] == V3_LIFECYCLE.success_verdict
    verify_public_result(public)


def test_v3_canonical_admission_binds_the_exact_v3_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = resolve_private_root(
        tmp_path / V3_LIFECYCLE.private_root_name,
        repository_root=Path.cwd(),
        lifecycle=V3_LIFECYCLE,
    )
    probe = roots.development_probes / "development-probe-01"
    probe.mkdir(mode=0o700)
    terminal_path = probe / "development-probe-01.json"
    terminal_path.write_text(
        json.dumps(
            {
                "schema_version": "live-telemetry-instrumentation-v3.terminal.v1",
                "mode": "DEVELOPMENT_PROBE",
                "development_probe_number": 1,
                "verdict": "DEVELOPMENT_PROBE_AVAILABLE",
                "sandbox_startup_attempted": True,
                "all_refs_resolve": True,
                "sources": {
                    name: {
                        "status": "AVAILABLE",
                        "target_record_count": 1,
                        "invalid_ref_count": 0,
                    }
                    for name in ("METRICS", "LOGS", "TRACES")
                },
                "cleanup": {
                    "baseline_restored": True,
                    "owned_containers": 0,
                    "owned_networks": 0,
                    "owned_volumes": 0,
                    "non_owned_resources_changed": False,
                    "verdict": "CLEAN",
                },
            }
        ),
        encoding="utf-8",
    )
    terminal_path.chmod(0o600)
    calls: list[tuple[str, ...]] = []

    def fake_git(_: Path, *arguments: str) -> str:
        calls.append(arguments)
        if arguments == ("status", "--porcelain=v1"):
            return ""
        if arguments == ("branch", "--show-current"):
            return V3_LIFECYCLE.branch
        return "a" * 40

    monkeypatch.setattr(instrumentation_v2, "_git", fake_git)
    head = instrumentation_v2._verify_canonical_admission(
        tmp_path,
        roots,
        lifecycle=V3_LIFECYCLE,
        implementation_ci_passed=True,
    )
    assert head == "a" * 40
    assert ("rev-parse", f"origin/{V3_LIFECYCLE.branch}") in calls
    admission = json.loads(
        roots.canonical_preflight.joinpath("admission.json").read_text(encoding="utf-8")
    )
    assert admission["schema_version"] == (
        "live-telemetry-instrumentation-v3.canonical-admission.v1"
    )


def test_v3_canonical_admission_rejects_a_v2_development_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = resolve_private_root(
        tmp_path / V3_LIFECYCLE.private_root_name,
        repository_root=Path.cwd(),
        lifecycle=V3_LIFECYCLE,
    )
    probe = roots.development_probes / "development-probe-01"
    probe.mkdir(mode=0o700)
    terminal_path = probe / "development-probe-01.json"
    terminal_path.write_text(
        json.dumps(
            {
                "schema_version": "live-telemetry-instrumentation-v2.terminal.v1",
                "mode": "DEVELOPMENT_PROBE",
                "development_probe_number": 1,
                "verdict": "DEVELOPMENT_PROBE_AVAILABLE",
                "sandbox_startup_attempted": True,
                "all_refs_resolve": True,
                "sources": {
                    name: {
                        "status": "AVAILABLE",
                        "target_record_count": 1,
                        "invalid_ref_count": 0,
                    }
                    for name in ("METRICS", "LOGS", "TRACES")
                },
                "cleanup": {
                    "baseline_restored": True,
                    "owned_containers": 0,
                    "owned_networks": 0,
                    "owned_volumes": 0,
                    "non_owned_resources_changed": False,
                    "verdict": "CLEAN",
                },
            }
        ),
        encoding="utf-8",
    )
    terminal_path.chmod(0o600)
    monkeypatch.setattr(instrumentation_v2, "_git", lambda *_: "a" * 40)
    with pytest.raises(RuntimeError, match="terminal lifecycle"):
        instrumentation_v2._verify_canonical_admission(
            tmp_path,
            roots,
            lifecycle=V3_LIFECYCLE,
            implementation_ci_passed=True,
        )
