from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess

import pytest

from ecomsre.product.pilot import live_baseline_readiness_v023
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
    build_product_v023_environment_payload,
)
from ecomsre.product.baselines import (
    BaselineBuildModeV1,
    BaselineBuildPolicyV1,
    BaselineJobCreateV1,
)
from ecomsre.product.connectors.base import ConnectorWindowV1
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.baseline_attempts_v023 import (
    BaselineAttemptStartV023,
    BaselineChangedParameterV023,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
    _RetryableTransportV023,
    _TransportRetriesExhaustedV023,
    _implementation_revision_sha256,
    _observe_baseline_submission_after_stability_v023,
    _recover_baseline_job_by_idempotency_key_v023,
    _refresh_job_after_timeout_v023,
    _request_json_with_transport_retries_v023,
    _require_clean_head,
    _wait_job,
    attempt_semantic_inputs_v023,
    planned_baseline_windows_v023,
    verify_live_baseline_readiness_contract_v023,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


ROOT = Path(__file__).resolve().parents[2]


def test_live_attempt_plans_all_five_windows_before_execution() -> None:
    started_at = datetime(2026, 8, 29, 1, 2, 3, tzinfo=UTC)
    windows = planned_baseline_windows_v023(started_at)

    assert len(windows) == 5
    assert windows[0]["started_at"] == started_at.isoformat()
    assert windows[-1]["ended_at"] == (started_at + timedelta(seconds=180)).isoformat()
    assert all(
        left["ended_at"] == right["started_at"]
        for left, right in zip(windows, windows[1:])
    )


def test_live_planned_window_dicts_are_canonicalized_before_start_digest() -> None:
    started_at = datetime(2026, 8, 29, 1, 2, 3, tzinfo=UTC)
    environment = build_product_v023_environment_payload(
        repository_root=ROOT,
        runtime_authority_sha256="a" * 64,
    )

    start = BaselineAttemptStartV023.build(
        attempt_ordinal=1,
        changed_parameter=BaselineChangedParameterV023.INITIAL,
        prior_completion_sha256=None,
        environment_id="env-" + "1" * 24,
        product_data_root="/tmp/product-v023-live-start-digest",
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        planned_windows=planned_baseline_windows_v023(started_at),
        semantic_inputs=attempt_semantic_inputs_v023(
            root=ROOT,
            environment_payload=environment,
        ),
        started_at=started_at,
    )

    assert isinstance(start.planned_windows[0], ConnectorWindowV1)
    assert len(start.start_sha256) == 64


def test_live_attempt_rejects_non_utc_schedule() -> None:
    with pytest.raises(ValueError, match="must be UTC"):
        planned_baseline_windows_v023(datetime(2026, 8, 29, 1, 2, 3))


def test_demo_builder_accepts_only_one_exact_contiguous_planned_schedule() -> None:
    started_at = datetime(2026, 8, 29, 1, 2, 3, tzinfo=UTC)
    windows = tuple(
        ConnectorWindowV1.model_validate(item)
        for item in planned_baseline_windows_v023(started_at)
    )
    request = BaselineJobCreateV1(
        build_policy=BaselineBuildPolicyV1(
            mode=BaselineBuildModeV1.DEMO_ONLY,
            lookback_seconds=180,
            window_count=5,
            minimum_successful_windows=4,
            warmup_seconds=180,
        ),
        candidate_services=("checkout",),
        planned_windows=windows,
        activate=True,
    )

    assert request.planned_windows == windows
    with pytest.raises(ValueError, match="planned-window schedule"):
        BaselineJobCreateV1(planned_windows=windows)


def test_live_environment_and_semantics_bind_active_p01() -> None:
    environment = build_product_v023_environment_payload(
        repository_root=ROOT,
        runtime_authority_sha256="a" * 64,
    )
    semantics = attempt_semantic_inputs_v023(
        root=ROOT,
        environment_payload=environment,
    )
    logs = next(
        item
        for item in environment["connector_configs"]
        if item["kind"] == "OPENSEARCH"
    )

    assert logs["settings"]["mode"] == "PROFILE_BOUND"
    assert (
        logs["settings"]["profile_binding"]["profile_sha256"]
        == ACTIVE_PROFILE_SHA256_V023
    )
    assert set(semantics) == {
        "connector_query_binding_sha256",
        "service_alias_binding_sha256",
        "implementation_revision_sha256",
    }
    assert all(len(value) == 64 for value in semantics.values())


def test_live_contract_check_is_offline_and_attempt_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        live_baseline_readiness_v023,
        "_load_public_attempts",
        lambda _root: (),
    )
    result = verify_live_baseline_readiness_contract_v023(ROOT)
    repeated = verify_live_baseline_readiness_contract_v023(ROOT)

    assert result["terminal"] == "ECOMSRE_PRODUCT_V023_LIVE_BASELINE_CONTRACT_READY"
    assert repeated["baseline_attempt_count"] == result["baseline_attempt_count"]
    assert result["fault_attempt_count"] == 0
    assert result["action_authority"] == "NONE"


def test_frozen_live_profiles_are_valid_json() -> None:
    for path in (
        ROOT / "config/product-v023/baseline-readiness/profile.json",
        ROOT / "config/product-v023/nofault/profile.json",
    ):
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


def test_live_execution_requires_a_clean_committed_head(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".ecomsre-tmp/\n.local/\n", encoding="utf-8")
    subprocess.run(
        ("git", "-C", str(tmp_path), "add", "tracked.txt", ".gitignore"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Product v0.2.3 Test",
            "-c",
            "user.email=product-v023@example.invalid",
            "commit",
            "-qm",
            "clean head",
        ),
        check=True,
    )

    assert len(_require_clean_head(tmp_path)) == 40
    tracked.write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean HEAD"):
        _require_clean_head(tmp_path)


def test_product_cleanup_retains_full_zero_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._require_port_available",
        lambda _port: None,
    )
    processes = _ProductHostProcessesV023(
        root=tmp_path,
        data_root=tmp_path / "data",
        private_root=tmp_path / "private",
    )

    observation = processes.cleanup_observation()

    assert observation == {
        "schema_version": "ecomsre.product.host-process-cleanup.v023",
        "verdict": "CLEAN",
        "owned_host_processes": 0,
        "product_api_port": 18081,
        "product_api_port_available": True,
        "launches": (),
        "non_owned_resources_changed": False,
        "safe_error": None,
    }


def test_binding_repairs_do_not_also_change_implementation_revision(
    tmp_path: Path,
) -> None:
    product_root = tmp_path / "src/ecomsre/product"
    binding = product_root / "connectors/opensearch_profile_binding_v023.py"
    implementation = product_root / "baselines.py"
    binding.parent.mkdir(parents=True)
    binding.write_text(
        "def build_product_v023_environment_payload():\n"
        "    return {'alias': 'checkout'}\n"
        "\n"
        "def other_behavior():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    implementation.write_text("VALUE = 1\n", encoding="utf-8")
    before = _implementation_revision_sha256(tmp_path)

    binding.write_text(
        "def build_product_v023_environment_payload():\n"
        "    return {'alias': 'checkoutservice'}\n"
        "\n"
        "def other_behavior():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    after_binding_repair = _implementation_revision_sha256(tmp_path)
    implementation.write_text("VALUE = 2\n", encoding="utf-8")
    after_implementation_repair = _implementation_revision_sha256(tmp_path)

    assert after_binding_repair == before
    assert after_implementation_repair != before


def test_timeout_boundary_continues_when_refresh_is_already_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = ProductJobRecordV1(
        job_id="job-" + "1" * 24,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=ProductJobStatusV1.SUCCEEDED,
        payload={},
        result={},
        safe_error_code=None,
        idempotency_key="product-v023-timeout-boundary",
        claimed_by="worker-v023",
        lease_expires_at=None,
        attempt_count=1,
        created_at=1.0,
        updated_at=2.0,
    )
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._request_json",
        lambda *_args, **_kwargs: job.model_dump(mode="json"),
    )

    observed, still_incomplete = _refresh_job_after_timeout_v023(
        object(),  # type: ignore[arg-type]
        job.job_id,
    )

    assert observed == job
    assert still_incomplete is False


def test_acknowledged_job_polling_failure_falls_back_to_the_bound_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = ProductJobRecordV1(
        job_id="job-" + "3" * 24,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=ProductJobStatusV1.FAILED,
        payload={},
        result=None,
        safe_error_code="BUILDER_FAILED",
        idempotency_key="product-v023-poll-fallback",
        claimed_by="worker-v023",
        lease_expires_at=None,
        attempt_count=1,
        created_at=1.0,
        updated_at=2.0,
    )
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._request_json_with_transport_retries_v023",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("response schema differs")
        ),
    )
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._read_job_from_store_v023",
        lambda *_args, **_kwargs: job,
    )

    observed = _wait_job(
        object(),  # type: ignore[arg-type]
        job.job_id,
        data_root=Path("/tmp/unused-product-v023"),
    )

    assert observed == job


def test_job_observation_never_swallows_process_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._request_json_with_transport_retries_v023",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        _wait_job(
            object(),  # type: ignore[arg-type]
            "job-" + "4" * 24,
            data_root=Path("/tmp/unused-product-v023"),
        )
    with pytest.raises(KeyboardInterrupt):
        _refresh_job_after_timeout_v023(
            object(),  # type: ignore[arg-type]
            "job-" + "4" * 24,
            data_root=Path("/tmp/unused-product-v023"),
        )


def test_ambiguous_builder_acknowledgement_recovers_by_idempotency_key(
    tmp_path: Path,
) -> None:
    environment_id = "env-" + "1" * 24
    caller_key = "product-v023-attempt-1-abcdef"
    full_key = f"baseline-build:{environment_id}:{caller_key}"
    repository = JobRepositoryV1(SqliteStoreV1(tmp_path / "product.sqlite3"))
    queued = repository.enqueue(
        ProductJobTypeV1.BASELINE_BUILD,
        {"environment_id": environment_id, "request": {}},
        idempotency_key=full_key,
        now=1.0,
    )

    recovered = _recover_baseline_job_by_idempotency_key_v023(
        tmp_path,
        environment_id=environment_id,
        idempotency_key=caller_key,
    )

    assert recovered == queued


def test_same_request_transport_retry_is_bounded_and_transport_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def retryable_then_success(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls < 4:
            raise _RetryableTransportV023("HTTP_5XX", "temporary")
        return {"job_id": "job-" + "1" * 24}

    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._request_json",
        retryable_then_success,
    )
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023.time.sleep",
        lambda _seconds: None,
    )

    result = _request_json_with_transport_retries_v023(
        object(),  # type: ignore[arg-type]
        "POST",
        "/baseline-jobs",
        extra_headers={"Idempotency-Key": "same-key"},
    )

    assert result["job_id"] == "job-" + "1" * 24
    assert calls == 4

    def schema_failure(*_args, **_kwargs):
        raise ValueError("schema differs")

    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._request_json",
        schema_failure,
    )
    with pytest.raises(ValueError, match="schema differs"):
        _request_json_with_transport_retries_v023(
            object(),  # type: ignore[arg-type]
            "POST",
            "/baseline-jobs",
        )


def test_transport_retry_exhaustion_preserves_all_four_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _RetryableTransportV023("TIMEOUT", "temporary")
        ),
    )
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(_TransportRetriesExhaustedV023) as caught:
        _request_json_with_transport_retries_v023(
            object(),  # type: ignore[arg-type]
            "POST",
            "/baseline-jobs",
        )

    assert caught.value.retry_count == 3
    assert caught.value.failure_codes == ("TIMEOUT",) * 4


def test_stability_observation_recovers_a_delayed_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = ProductJobRecordV1(
        job_id="job-" + "2" * 24,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=ProductJobStatusV1.PENDING,
        payload={},
        result=None,
        safe_error_code=None,
        idempotency_key="delayed",
        claimed_by=None,
        lease_expires_at=None,
        attempt_count=0,
        created_at=1.0,
        updated_at=1.0,
    )
    observations = iter((None, None, job))
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023._recover_baseline_job_by_idempotency_key_v023",
        lambda *_args, **_kwargs: next(observations),
    )
    monkeypatch.setattr(
        "ecomsre.product.pilot.live_baseline_readiness_v023.time.sleep",
        lambda _seconds: None,
    )

    recovered = _observe_baseline_submission_after_stability_v023(
        Path("/tmp/unused-product-v023"),
        environment_id="env-" + "1" * 24,
        idempotency_key="same-key",
    )

    assert recovered == job
