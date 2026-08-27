from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

import scripts.product.run_increment5_live_acceptance as live_runner
from ecomsre.dta_v2.read_only_smoke import CleanupObservation
from ecomsre.product.live_acceptance import LiveReadOnlyAcceptanceV1
from scripts.product.run_increment5_live_acceptance import (
    _finalize_live_acceptance,
    _write_sha_bound_json,
)


ROOT = Path(__file__).resolve().parents[2]


def _passing_payload() -> dict[str, object]:
    return {
        "observed_at": datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        "docker_context": "desktop-linux",
        "docker_daemon_id_sha256": "1" * 64,
        "sources": {
            "PROMETHEUS": "AVAILABLE",
            "OPENSEARCH": "AVAILABLE",
            "JAEGER": "AVAILABLE",
            "HTTP_HEALTH": "AVAILABLE",
        },
        "normalized_services": tuple(f"service-{index:02d}" for index in range(10)),
        "environment_id": "env-" + "1" * 24,
        "baseline_id": "base-" + "2" * 24,
        "baseline_mode": "DEMO_ONLY",
        "successful_baseline_windows": 5,
        "incident_id": "inc-" + "3" * 24,
        "diagnosis_terminal": "NO_INCIDENT",
        "evidence_object_count": 8,
        "evidence_refs_resolved": True,
        "connector_raw_failures": 0,
        "explicit_source_failures": (),
        "agent_writes": 0,
        "runbook_executions": 0,
        "fault_injections": 0,
        "forward_mutations": 0,
        "product_cleanup": "CLEAN",
        "demo_cleanup": "CLEAN",
        "owned_containers_after_cleanup": 0,
        "owned_networks_after_cleanup": 0,
        "owned_volumes_after_cleanup": 0,
        "non_owned_resources_changed": False,
    }


def _passing_product_result() -> dict[str, object]:
    payload = _passing_payload()
    for field in (
        "observed_at",
        "docker_context",
        "docker_daemon_id_sha256",
        "fault_injections",
        "forward_mutations",
        "product_cleanup",
        "demo_cleanup",
        "owned_containers_after_cleanup",
        "owned_networks_after_cleanup",
        "owned_volumes_after_cleanup",
        "non_owned_resources_changed",
    ):
        payload.pop(field)
    return payload


def test_live_acceptance_mints_terminal_only_for_complete_read_only_evidence() -> None:
    report = LiveReadOnlyAcceptanceV1.build(**_passing_payload())

    assert report.terminal == "ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS"
    assert len(report.normalized_services) == 10
    assert report.report_sha256 != "0" * 64
    assert LiveReadOnlyAcceptanceV1.model_validate_json(
        report.model_dump_json()
    ) == report


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("normalized_services", tuple(f"service-{index:02d}" for index in range(9))),
        ("sources", {"PROMETHEUS": "AVAILABLE"}),
        ("baseline_mode", "HISTORICAL"),
        ("evidence_refs_resolved", False),
        ("connector_raw_failures", 1),
        ("agent_writes", 1),
        ("runbook_executions", 1),
        ("fault_injections", 1),
        ("product_cleanup", "BLOCKED"),
        ("demo_cleanup", "BLOCKED"),
        ("non_owned_resources_changed", True),
    ],
)
def test_live_acceptance_fails_closed(field: str, value: object) -> None:
    payload = _passing_payload()
    payload[field] = value

    with pytest.raises(ValueError):
        LiveReadOnlyAcceptanceV1.build(**payload)


def test_no_incident_rejects_fully_enumerated_source_failure() -> None:
    payload = _passing_payload()
    payload["connector_raw_failures"] = 1
    payload["explicit_source_failures"] = (
        "RUNTIME:CONNECTOR_TARGET_UNAVAILABLE:a:runtime:payment",
    )

    with pytest.raises(ValueError, match="No-Incident cannot hide"):
        LiveReadOnlyAcceptanceV1.build(**payload)


def test_increment5_compose_examples_docs_and_acceptance_surfaces_exist() -> None:
    compose = (ROOT / "docker-compose.product.yml").read_text(encoding="utf-8")
    environment = json.loads(
        (ROOT / "examples/product/environment.otel-demo.json").read_text(
            encoding="utf-8"
        )
    )

    assert "name: ecomsre-product-mvp-v01" in compose
    assert "io.ecomsre.product: ecomsre-product-mvp-v01" in compose
    assert "pull_policy: never" in compose
    assert "read_only: true" in compose
    assert "security_opt:" in compose and "no-new-privileges:true" in compose
    assert "docker.sock" not in compose
    dockerfile = (ROOT / "Dockerfile.product").read_text(encoding="utf-8")
    assert 'LABEL io.ecomsre.product="ecomsre-product-mvp-v01"' in dockerfile
    endpoints = {
        item["kind"]: item.get("endpoint")
        for item in environment["connector_configs"]
    }
    assert endpoints == {
        "PROMETHEUS": "http://host.docker.internal:19090",
        "OPENSEARCH": "http://host.docker.internal:19200",
        "JAEGER": "http://host.docker.internal:11686/jaeger/ui",
        "HTTP_HEALTH": None,
    }

    required_docs = {
        "ARCHITECTURE.md",
        "QUICKSTART.md",
        "API.md",
        "CONNECTORS.md",
        "BASELINES.md",
        "KNOWLEDGE_EVOLUTION.md",
        "OPERATIONS.md",
        "LIMITATIONS.md",
    }
    assert required_docs == {
        path.name for path in (ROOT / "docs/product").glob("*.md")
    }
    quickstart = (ROOT / "docs/product/QUICKSTART.md").read_text(encoding="utf-8")
    knowledge = (ROOT / "docs/product/KNOWLEDGE_EVOLUTION.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "docker compose",
        "/v1/environments",
        "verify-jobs",
        "baseline-jobs",
        "/v1/incidents",
        "diagnosis-jobs",
        "fault-families",
        "registration-drafts",
        "shadow-evaluation-jobs",
        "promotions",
    ):
        assert required in quickstart
    for required in (
        "PRESENT",
        "ABSENT_WITH_COMPLETE_COVERAGE",
        "UNKNOWN",
        "SOURCE_FAILED",
        "conjunction sizes 1, 2, and 3",
        "beam-width cap of 20",
    ):
        assert required in knowledge

    for relative in (
        "docs/results/ecomsre-product-mvp-v01-acceptance.json",
        "docs/results/ecomsre-product-mvp-v01-acceptance.md",
        "docs/results/ecomsre-product-mvp-v01-limitations.md",
        "docs/results/ecomsre-product-mvp-v01-live-attempt-ledger.json",
        "docs/external-reviews/ecomsre-product-mvp-v01-final-review.md",
        "scripts/product/run_increment5_live_acceptance.py",
    ):
        assert (ROOT / relative).is_file(), relative
    ledger = json.loads(
        (
            ROOT
            / "docs/results/ecomsre-product-mvp-v01-live-attempt-ledger.json"
        ).read_text(encoding="utf-8")
    )
    assert ledger["evidence_kind"] == "RETROSPECTIVE_SESSION_LEDGER"
    assert ledger["authoritative_runtime_terminal_artifact"] is False
    assert len(ledger["attempts"]) == 3
    assert all(
        item["runtime_terminal_artifact_available"] is False
        and item["cleanup_artifact_available"] is False
        for item in ledger["attempts"]
    )


def test_live_failure_writer_binds_private_machine_readable_evidence(tmp_path: Path) -> None:
    output = _write_sha_bound_json(
        tmp_path,
        "failure.json",
        {
            "schema_version": "ecomsre.product.live-read-only-failure.v1",
            "terminal": "BLOCKED_ECOMSRE_PRODUCT_LIVE_ACCEPTANCE",
            "safe_error": "BASELINE_INSUFFICIENT_WINDOWS",
        },
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["report_sha256"]) == 64
    assert output.stat().st_mode & 0o777 == 0o600


def test_cleanup_blocked_is_persisted_as_failure_terminal(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        _finalize_live_acceptance(
            private_root=tmp_path,
            failure=None,
            docker_context="desktop-linux",
            daemon_id="daemon",
            product_result=_passing_product_result(),
            baseline_unchanged=True,
            product_cleanup="BLOCKED",
            demo_cleanup=CleanupObservation.unknown_blocked(),
            demo_cleanup_error=None,
        )

    payload = json.loads(
        (tmp_path / "report/product-live-read-only-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["terminal"] == "BLOCKED_ECOMSRE_PRODUCT_LIVE_ACCEPTANCE"
    assert payload["product_cleanup"] == "BLOCKED"
    assert payload["demo_cleanup"] == "BLOCKED"


def test_terminal_validation_failure_is_persisted(tmp_path: Path) -> None:
    product_result = _passing_product_result()
    product_result["diagnosis_terminal"] = "OPEN_WORLD"

    with pytest.raises(ValueError):
        _finalize_live_acceptance(
            private_root=tmp_path,
            failure=None,
            docker_context="desktop-linux",
            daemon_id="daemon",
            product_result=product_result,
            baseline_unchanged=True,
            product_cleanup="CLEAN",
            demo_cleanup=CleanupObservation.clean(),
            demo_cleanup_error=None,
        )

    payload = json.loads(
        (tmp_path / "report/product-live-read-only-failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["terminal"] == "BLOCKED_ECOMSRE_PRODUCT_LIVE_ACCEPTANCE"
    assert payload["error_type"] == "ValidationError"


def test_cleanup_refuses_unknown_same_project_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = (
        frozenset({"owned-container"}),
        frozenset({"owned-network"}),
        frozenset({"owned-volume"}),
    )
    baseline = (frozenset(), frozenset(), frozenset())
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        live_runner,
        "_project_ids",
        lambda _root, kind: (
            expected[live_runner._PRODUCT_RESOURCE_KINDS.index(kind)]
            | ({"unknown-orphan"} if kind == "container" else set())
        ),
    )
    monkeypatch.setattr(
        live_runner,
        "_custom_label_ids",
        lambda _root, kind: expected[
            live_runner._PRODUCT_RESOURCE_KINDS.index(kind)
        ],
    )
    monkeypatch.setattr(
        live_runner,
        "_product_ids",
        lambda _root, kind: expected[
            live_runner._PRODUCT_RESOURCE_KINDS.index(kind)
        ],
    )
    monkeypatch.setattr(
        live_runner,
        "_run",
        lambda arguments, **_kwargs: commands.append(tuple(arguments)) or "",
    )

    result = live_runner._cleanup_product(
        tmp_path,
        "token",
        baseline,
        expected,
    )

    assert result == "BLOCKED"
    assert commands == []


def test_start_refuses_exact_name_collision_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(live_runner, "_any_product_namespace_resource", lambda _root: False)
    monkeypatch.setattr(live_runner, "_has_product_name_collision", lambda _root: True)
    monkeypatch.setattr(
        live_runner,
        "_run",
        lambda arguments, **_kwargs: commands.append(tuple(arguments)) or "",
    )

    with pytest.raises(RuntimeError, match="exact resource name"):
        live_runner._start_product(tmp_path, "token")

    assert commands == []


def test_start_refuses_unowned_fixed_image_tag_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(live_runner, "_any_product_namespace_resource", lambda _root: False)
    monkeypatch.setattr(live_runner, "_has_product_name_collision", lambda _root: False)
    monkeypatch.setattr(live_runner, "_product_image_owner", lambda _root: "unknown")
    monkeypatch.setattr(
        live_runner,
        "_run",
        lambda arguments, **_kwargs: commands.append(tuple(arguments)) or "",
    )

    with pytest.raises(RuntimeError, match="image tag is not owned"):
        live_runner._start_product(tmp_path, "token")

    assert commands == []


def test_partial_start_recovers_exact_owned_inventory_for_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = (
        frozenset({"owned-container-1", "owned-container-2"}),
        frozenset({"owned-network"}),
        frozenset({"owned-volume"}),
    )
    baseline = (frozenset(), frozenset(), frozenset())
    cleaned: list[object] = []
    monkeypatch.setattr(
        live_runner,
        "_discover_exact_product_inventory",
        lambda _root: expected,
    )
    monkeypatch.setattr(
        live_runner,
        "_cleanup_product",
        lambda root, token, original, inventory: (
            cleaned.append((root, token, original, inventory)) or "CLEAN"
        ),
    )

    result = live_runner._cleanup_product_after_attempt(
        tmp_path,
        "token",
        baseline,
        None,
    )

    assert result == "CLEAN"
    assert cleaned == [(tmp_path, "token", baseline, expected)]


def test_partial_unknown_inventory_is_not_mutated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = (frozenset(), frozenset(), frozenset())
    cleaned: list[object] = []
    monkeypatch.setattr(
        live_runner,
        "_discover_exact_product_inventory",
        lambda _root: None,
    )
    monkeypatch.setattr(
        live_runner,
        "_product_namespace_is_empty",
        lambda _root: False,
    )
    monkeypatch.setattr(
        live_runner,
        "_cleanup_product",
        lambda *_args, **_kwargs: cleaned.append(True) or "CLEAN",
    )

    result = live_runner._cleanup_product_after_attempt(
        tmp_path,
        "token",
        baseline,
        None,
    )

    assert result == "BLOCKED"
    assert cleaned == []
