from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from ecomsre.dta_v2.v21.live_reconciliation import (
    BLOCKED_ATTEMPT_ID_V1,
    BLOCKED_CODE_HEAD_V1,
    FLAGD_BIND_SENTINEL_V1,
    IndependentRetryReviewV1,
    PostTerminalReconciliationV1,
    RetryAdmissionV1,
    RETRY_CONSUMPTION_FILENAME_V1,
    build_resolved_compose_identity_v1,
    build_retry_admission_v1,
    consume_retry_admission_v1,
    verify_historical_blocker_eligibility_v1,
    verify_retry_consumption_v1,
    verify_cross_context_compose_identity_v1,
)
from ecomsre.dta_v2.v21.live_contracts import LiveReadinessV2
from ecomsre.dta_v2.v21.contracts import semantic_sha256


REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose(flagd_directory: Path) -> dict[str, object]:
    return {
        "services": {
            "flagd": {
                "image": "flagd@sha256:" + "1" * 64,
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(flagd_directory),
                        "target": "/etc/flagd",
                        "read_only": True,
                    }
                ],
            },
            "flagd-ui": {
                "image": "flagd-ui@sha256:" + "2" * 64,
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(flagd_directory),
                        "target": "/app/data",
                    }
                ],
            },
        },
        "networks": {"default": {"name": "owned"}},
        "volumes": {},
    }


def _identity(
    raw: dict[str, object],
    *,
    flagd_directory: Path,
    private_root: Path,
    repository_root: Path,
):
    return build_resolved_compose_identity_v1(
        raw,
        expected_flagd_directory=flagd_directory,
        accepted_private_prf_root=private_root,
        repository_root=repository_root,
        raw_contract_verifier=lambda value: None,
    )


def test_cross_context_identity_preserves_raw_and_normalizes_only_two_sources(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    private = tmp_path / "private" / "pr-f"
    first = private / "readiness" / "runtime" / "flagd"
    second = private / "attempts" / "runtime" / "flagd"
    repository.mkdir()
    first.mkdir(parents=True, mode=0o700)
    second.mkdir(parents=True, mode=0o700)
    first.chmod(0o700)
    second.chmod(0o700)

    first_raw = _compose(first)
    second_raw = _compose(second)
    first_identity = _identity(
        first_raw,
        flagd_directory=first,
        private_root=private,
        repository_root=repository,
    )
    second_identity = _identity(
        second_raw,
        flagd_directory=second,
        private_root=private,
        repository_root=repository,
    )

    assert first_identity.raw_compose_sha256 != second_identity.raw_compose_sha256
    assert (
        first_identity.execution_compose_sha256
        == second_identity.execution_compose_sha256
    )
    assert first_identity.normalized_bind_count == 2
    assert {item.normalized_source for item in first_identity.normalized_bindings} == {
        FLAGD_BIND_SENTINEL_V1
    }
    verify_cross_context_compose_identity_v1(
        first_raw=first_raw,
        first_identity=first_identity,
        first_expected_flagd_directory=first,
        second_raw=second_raw,
        second_identity=second_identity,
        second_expected_flagd_directory=second,
    )


@pytest.mark.parametrize(
    ("service", "field", "value"),
    [
        ("flagd", "target", "/wrong"),
        ("flagd", "type", "volume"),
        ("flagd", "read_only", False),
        ("flagd-ui", "read_only", True),
    ],
)
def test_identity_rejects_non_exact_mount_shape(
    tmp_path: Path, service: str, field: str, value: object
) -> None:
    repository = tmp_path / "repo"
    private = tmp_path / "private" / "pr-f"
    flagd = private / "attempt" / "flagd"
    repository.mkdir()
    flagd.mkdir(parents=True, mode=0o700)
    flagd.chmod(0o700)
    raw = _compose(flagd)
    services = raw["services"]
    assert isinstance(services, dict)
    service_value = services[service]
    assert isinstance(service_value, dict)
    mounts = service_value["volumes"]
    assert isinstance(mounts, list)
    assert isinstance(mounts[0], dict)
    mounts[0][field] = value

    with pytest.raises(ValueError, match="closed-world flag bind"):
        _identity(
            raw,
            flagd_directory=flagd,
            private_root=private,
            repository_root=repository,
        )


def test_identity_rejects_unsafe_source_and_cross_context_third_difference(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    private = tmp_path / "private" / "pr-f"
    first = private / "one" / "flagd"
    second = private / "two" / "flagd"
    repository.mkdir()
    first.mkdir(parents=True, mode=0o700)
    second.mkdir(parents=True, mode=0o700)
    first.chmod(0o700)
    second.chmod(0o700)

    with pytest.raises(ValueError, match="accepted private PR-F root"):
        _identity(
            _compose(repository),
            flagd_directory=repository,
            private_root=private,
            repository_root=repository,
        )

    first_raw = _compose(first)
    second_raw = _compose(second)
    second_services = second_raw["services"]
    assert isinstance(second_services, dict)
    second_flagd = second_services["flagd"]
    assert isinstance(second_flagd, dict)
    second_flagd["image"] = "changed@sha256:" + "3" * 64
    first_identity = _identity(
        first_raw,
        flagd_directory=first,
        private_root=private,
        repository_root=repository,
    )
    second_identity = _identity(
        second_raw,
        flagd_directory=second,
        private_root=private,
        repository_root=repository,
    )
    with pytest.raises(ValueError, match="execution Compose identity"):
        verify_cross_context_compose_identity_v1(
            first_raw=first_raw,
            first_identity=first_identity,
            first_expected_flagd_directory=first,
            second_raw=second_raw,
            second_identity=second_identity,
            second_expected_flagd_directory=second,
        )


def test_identity_rejects_symlink_flag_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    private = tmp_path / "private" / "pr-f"
    target = private / "real"
    link = private / "link"
    repository.mkdir()
    target.mkdir(parents=True, mode=0o700)
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="non-symlink directory"):
        _identity(
            _compose(link),
            flagd_directory=link,
            private_root=private,
            repository_root=repository,
        )


def test_same_context_fresh_identity_must_match_raw_and_execution(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    private = tmp_path / "private" / "pr-f"
    flagd = private / "attempt" / "flagd"
    repository.mkdir()
    flagd.mkdir(parents=True, mode=0o700)
    flagd.chmod(0o700)
    admitted_raw = _compose(flagd)
    admitted = _identity(
        admitted_raw,
        flagd_directory=flagd,
        private_root=private,
        repository_root=repository,
    )
    fresh_raw = copy.deepcopy(admitted_raw)
    fresh = _identity(
        fresh_raw,
        flagd_directory=flagd,
        private_root=private,
        repository_root=repository,
    )
    assert fresh == admitted

    fresh_services = fresh_raw["services"]
    assert isinstance(fresh_services, dict)
    fresh_flagd = fresh_services["flagd"]
    assert isinstance(fresh_flagd, dict)
    fresh_flagd["environment"] = {"DRIFT": "1"}
    drifted = _identity(
        fresh_raw,
        flagd_directory=flagd,
        private_root=private,
        repository_root=repository,
    )
    assert drifted.raw_compose_sha256 != admitted.raw_compose_sha256
    assert drifted.execution_compose_sha256 != admitted.execution_compose_sha256


def _reconciliation() -> PostTerminalReconciliationV1:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.pr-f-post-terminal-reconciliation.v1",
        "amendment_raw_sha256": "ea6740bce0ba63e093cda2807aea886d4ca48907702a2bf41ad1eedd0e2ab164",
        "decision_id": "DEC-045",
        "blocked_code_head": BLOCKED_CODE_HEAD_V1,
        "blocked_attempt_id": BLOCKED_ATTEMPT_ID_V1,
        "blocked_attempt_claim_raw_sha256": "1" * 64,
        "blocked_attempt_claim_semantic_sha256": "2" * 64,
        "blocked_attempt_terminal_raw_sha256": "3" * 64,
        "blocked_attempt_terminal_semantic_sha256": "4" * 64,
        "blocked_readiness_raw_sha256": "5" * 64,
        "blocked_readiness_semantic_sha256": "6" * 64,
        "preflight_resolved_compose_file_raw_sha256": "7" * 64,
        "blocked_attempt_resolved_compose_file_raw_sha256": "8" * 64,
        "preflight_raw_resolved_compose_sha256": "7" * 64,
        "blocked_attempt_raw_resolved_compose_sha256": "8" * 64,
        "preflight_execution_compose_sha256": "9" * 64,
        "blocked_attempt_execution_compose_sha256": "9" * 64,
        "preflight_compose_identity_sha256": "a" * 64,
        "blocked_attempt_compose_identity_sha256": "b" * 64,
        "master_authorization_raw_sha256": "c" * 64,
        "master_authorization_semantic_sha256": "d" * 64,
        "master_authorization_sha256": "e" * 64,
        "protocol_freeze_raw_sha256": "f" * 64,
        "protocol_freeze_semantic_sha256": "0" * 64,
        "ad_protocol_sha256": "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517",
        "current_quiescence_observation_sha256": "1" * 64,
        "compose_diff_json_pointers": (
            "/services/flagd/volumes/0/source",
            "/services/flagd-ui/volumes/0/source",
        ),
        "classification": "PRE_BASELINE_HARNESS_IDENTITY_MISMATCH",
        "historical_attempt_status": "BLOCKED",
        "historical_cleanup_status": "BLOCKED",
        "historical_baseline_restoration_proven": False,
        "historical_fault_observed": False,
        "historical_provider_called": False,
        "historical_forward_action_observed": False,
        "historical_remaining_owned_resources": 0,
        "historical_non_owned_change_observed": False,
        "historical_artifact_absence_proven": True,
        "closed_world_compose_difference_proven": True,
        "current_resource_quiescence_proven": True,
        "retry_eligible": True,
    }
    return PostTerminalReconciliationV1.model_validate(
        {**payload, "reconciliation_sha256": semantic_sha256(payload)}
    )


def _readiness_v2(*, head: str) -> LiveReadinessV2:
    return LiveReadinessV2.build(
        terminal="DTA_V21_PR_F_PRELIVE_READY",
        readiness_attempt_id="readiness-0001",
        code_head=head,
        exact_head_ci_success=True,
        exact_head_ci_run_id=123,
        exact_head_ci_run_url="https://github.com/example/repo/actions/runs/123",
        branch="codex/dta-v21-p0-pr-f-live-closeout",
        origin_main_is_ancestor=True,
        protocol_sha256="c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517",
        live_config_sha256="b" * 64,
        planner_identity_sha256="8" * 64,
        provider_model="gpt-5.4-mini-2026-03-17",
        pr_e_claim="DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED",
        docker_boundary="LOCAL_UNIX_DOCKER",
        raw_compose_sha256="1" * 64,
        execution_compose_sha256="2" * 64,
        compose_identity_sha256="3" * 64,
        normalization_policy_id="DTA_V21_PRF_ATTEMPT_LOCAL_FLAGD_BIND_SOURCE_V1",
        baseline_flag_document_sha256="4" * 64,
        owned_resource_collisions=0,
        required_ports_available=True,
        cleanup_readiness="OWNED_SCOPE_ADMITTED",
        private_permissions="0700_DIRECTORIES_0600_FILES",
        master_authorization_sha256="5" * 64,
    )


def test_retry_admission_is_exact_head_review_and_slot_one_bound() -> None:
    head = "a" * 40
    review = IndependentRetryReviewV1.build(
        code_head=head,
        reviewer="independent-reviewer",
        reviewed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        must_fix_count=0,
        should_fix_count=0,
        claim_accuracy="PASS",
    )
    admission = build_retry_admission_v1(
        new_code_head=head,
        reconciliation=_reconciliation(),
        readiness=_readiness_v2(head=head),
        review=review,
    )
    assert admission.verdict == "ALLOW_ONE_NEW_CAMPAIGN"
    assert admission.admitted_first_scenario == "NO_FAULT"
    assert admission.maximum_new_campaigns == 1
    assert admission.maximum_retry_campaigns_after_consumption == 0

    with pytest.raises(ValueError, match="exact-head"):
        RetryAdmissionV1.model_validate(
            {
                **admission.model_dump(mode="json"),
                "new_code_head": BLOCKED_CODE_HEAD_V1,
            }
        )


def test_retry_consumption_is_atomic_and_second_campaign_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = "a" * 40
    review = IndependentRetryReviewV1.build(
        code_head=head,
        reviewer="independent-reviewer",
        reviewed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        must_fix_count=0,
        should_fix_count=0,
        claim_accuracy="PASS",
    )
    admission = build_retry_admission_v1(
        new_code_head=head,
        reconciliation=_reconciliation(),
        readiness=_readiness_v2(head=head),
        review=review,
    )
    attempts = tmp_path / "pr-f/attempts"
    (attempts / BLOCKED_ATTEMPT_ID_V1).mkdir(parents=True)
    current_admission = {"value": admission}
    monkeypatch.setattr(
        "ecomsre.dta_v2.v21.live_reconciliation.verify_retry_admission_v1",
        lambda **_values: current_admission["value"],
    )

    first = consume_retry_admission_v1(
        repository_root=tmp_path,
        private_root=tmp_path,
        new_code_head=head,
        consumed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert first.maximum_additional_campaigns == 0

    alternate_payload = _reconciliation().model_dump(
        mode="python", exclude={"reconciliation_sha256"}
    )
    alternate_payload["current_quiescence_observation_sha256"] = "2" * 64
    alternate_reconciliation = PostTerminalReconciliationV1.model_validate(
        {
            **alternate_payload,
            "reconciliation_sha256": semantic_sha256(alternate_payload),
        }
    )
    current_admission["value"] = build_retry_admission_v1(
        new_code_head=head,
        reconciliation=alternate_reconciliation,
        readiness=_readiness_v2(head=head),
        review=review,
    )
    with pytest.raises(RuntimeError, match="BLOCKED_DTA_V21_PRF_RETRY_EXHAUSTED"):
        consume_retry_admission_v1(
            repository_root=tmp_path,
            private_root=tmp_path,
            new_code_head=head,
            consumed_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        )

    current_admission["value"] = admission
    forged_payload = first.model_dump(mode="json", exclude={"consumption_sha256"})
    forged_payload["reconciliation_sha256"] = "f" * 64
    forged = type(first).model_validate_json(
        json.dumps(
            {
            **forged_payload,
            "consumption_sha256": semantic_sha256(forged_payload),
            }
        )
    )
    consumption_path = (
        tmp_path / "pr-f/retry-consumptions" / RETRY_CONSUMPTION_FILENAME_V1
    )
    consumption_path.write_text(forged.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="retry consumption binding differs"):
        verify_retry_consumption_v1(
            repository_root=tmp_path,
            private_root=tmp_path,
            new_code_head=head,
        )


def test_exact_private_historical_blocker_is_eligible_without_relabeling() -> None:
    configured = os.environ.get("DTA_V21_ACCEPTED_PRIVATE_ROOT")
    if configured is None:
        pytest.skip("DTA_V21_ACCEPTED_PRIVATE_ROOT is not configured")
    eligibility = verify_historical_blocker_eligibility_v1(
        repository_root=REPO_ROOT,
        private_root=Path(configured),
    )
    assert (
        eligibility.preflight_identity.execution_compose_sha256
        == eligibility.attempt_identity.execution_compose_sha256
    )
    assert (
        eligibility.preflight_identity.raw_compose_sha256
        != eligibility.attempt_identity.raw_compose_sha256
    )
