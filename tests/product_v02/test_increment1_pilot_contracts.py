from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from ecomsre.product.pilot.contracts_v02 import (
    LivePilotEpisodeV02,
    PilotEpisodeRoleV02,
    PilotEpisodeTerminalV02,
    QueueProfileV02,
    TrafficProfileV02,
)
from ecomsre.product.pilot.queue_profile_v02 import QueueFlagControllerV02
from ecomsre.product.pilot.episode_runner_v02 import PilotEpisodeRepositoryV02
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    load_pilot_runtime_authority_v02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.pilot.traffic_v02 import BoundedCheckoutTrafficV02


def _flag_document() -> dict[str, object]:
    return {
        "$schema": "https://flagd.dev/schema/v0/flags.json",
        "flags": {
            "adFailure": {
                "defaultVariant": "off",
                "state": "ENABLED",
                "variants": {"off": False, "on": True},
            },
            "kafkaQueueProblems": {
                "defaultVariant": "off",
                "state": "ENABLED",
                "variants": {"off": 0, "on": 100},
            },
        },
    }


def _profile() -> QueueProfileV02:
    return QueueProfileV02(
        profile_id="profile-checkout-queue-v02",
        candidate_values=(5, 10, 20),
        maximum_calibration_changes=2,
        expected_default_value=0,
    )


def test_queue_controller_changes_only_preregistered_flag_and_restores_exact_bytes(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "private" / "control" / "demo.flagd.json"
    runtime.parent.mkdir(parents=True, mode=0o700)
    baseline = json.dumps(_flag_document(), indent=2, sort_keys=True).encode() + b"\n"
    runtime.write_bytes(baseline)
    runtime.chmod(0o600)
    controller = QueueFlagControllerV02(
        runtime_path=runtime,
        profile=_profile(),
        expected_baseline_sha256=hashlib.sha256(baseline).hexdigest(),
    )

    with pytest.raises(RuntimeError, match="boom"):
        with controller.activated(5) as transition:
            changed = json.loads(runtime.read_text(encoding="utf-8"))
            assert transition.applied_value == 5
            assert changed["flags"]["kafkaQueueProblems"]["defaultVariant"] == "ecomsre-v02-5"
            assert changed["flags"]["kafkaQueueProblems"]["variants"]["ecomsre-v02-5"] == 5
            assert changed["flags"]["adFailure"] == _flag_document()["flags"]["adFailure"]
            raise RuntimeError("boom")

    assert runtime.read_bytes() == baseline
    assert runtime.stat().st_mode & 0o777 == 0o600


def test_queue_controller_rejects_default_variant_that_does_not_map_to_zero(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "demo.flagd.json"
    document = _flag_document()
    document["flags"]["kafkaQueueProblems"]["defaultVariant"] = "on"  # type: ignore[index]
    baseline = json.dumps(document, sort_keys=True).encode()
    runtime.write_bytes(baseline)
    runtime.chmod(0o600)

    with pytest.raises(ValueError, match="active default does not map to baseline"):
        QueueFlagControllerV02(
            runtime_path=runtime,
            profile=_profile(),
            expected_baseline_sha256=hashlib.sha256(baseline).hexdigest(),
        )


@pytest.mark.parametrize("value", (0, 4, 21, 100))
def test_queue_controller_rejects_values_outside_frozen_candidates(
    tmp_path: Path,
    value: int,
) -> None:
    runtime = tmp_path / "demo.flagd.json"
    baseline = json.dumps(_flag_document(), sort_keys=True).encode()
    runtime.write_bytes(baseline)
    runtime.chmod(0o600)
    controller = QueueFlagControllerV02(
        runtime_path=runtime,
        profile=_profile(),
        expected_baseline_sha256=hashlib.sha256(baseline).hexdigest(),
    )

    with pytest.raises(ValueError, match="frozen candidate"):
        controller.apply(value)

    assert runtime.read_bytes() == baseline


def test_checkout_traffic_is_seeded_bounded_and_stops_on_error_budget() -> None:
    seen: list[tuple[str, dict[str, object]]] = []
    checkout_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal checkout_count
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/api/checkout":
            checkout_count += 1
        status = 503 if checkout_count == 3 and request.url.path == "/api/checkout" else 200
        return httpx.Response(status, json={"ok": status == 200})

    profile = TrafficProfileV02(
        profile_id="traffic-v02-test",
        request_seed=17,
        maximum_request_count=10,
        requests_per_second=2.0,
        error_budget=1,
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = BoundedCheckoutTrafficV02(client=client, sleep=lambda _: None).run(
            endpoint="http://127.0.0.1:8080/api/checkout",
            profile=profile,
        )

    assert result.attempted == 3
    assert result.succeeded == 2
    assert result.failed == 1
    assert result.stopped_on_error_budget is True
    assert [path for path, _ in seen] == [
        "/api/cart",
        "/api/checkout",
        "/api/cart",
        "/api/checkout",
        "/api/cart",
        "/api/checkout",
    ]
    checkout_payloads = [payload for path, payload in seen if path == "/api/checkout"]
    assert all(item["userId"].startswith("pilot-") for item in checkout_payloads)
    assert checkout_payloads == sorted(checkout_payloads, key=lambda item: item["userId"])


def test_live_episode_public_projection_redacts_private_control_and_truth() -> None:
    episode = LivePilotEpisodeV02.build(
        episode_id="episode-p1",
        role=PilotEpisodeRoleV02.POSITIVE_FIT,
        environment_id="env-v02",
        incident_id="inc-v02",
        product_job_id="job-v02",
        private_control_sha256="1" * 64,
        public_evidence_bundle_sha256="2" * 64,
        flag_profile_sha256="3" * 64,
        traffic_profile_sha256="4" * 64,
        baseline_sha256="5" * 64,
        diagnosis_terminal="OPEN_WORLD",
        root_services=("svc-checkout",),
        broad_domain="DEPENDENCY",
        mechanism="PRIVATE_QUEUE_OVERLOAD_TRUTH",
        evidence_refs=("obj-1", "obj-2"),
        fingerprint_id="fingerprint-v02",
        family_id=None,
        open_world_invocations=1,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
        baseline_restored=True,
        cleanup_status="CLEAN",
        episode_terminal=PilotEpisodeTerminalV02.PASS,
        observed_at=datetime(2026, 8, 28, 0, 0, tzinfo=UTC),
    )

    public = episode.public_projection()

    serialized = json.dumps(public, sort_keys=True)
    assert "kafkaQueueProblems" not in serialized
    assert "POSITIVE_FIT" not in serialized
    assert "PRIVATE_QUEUE_OVERLOAD_TRUTH" not in serialized
    assert "private_control_sha256" not in serialized
    assert public["episode_sha256"] == episode.episode_sha256


def test_passing_episode_requires_restoration_cleanup_and_zero_authority() -> None:
    common = {
        "episode_id": "episode-p1",
        "role": PilotEpisodeRoleV02.POSITIVE_FIT,
        "environment_id": "env-v02",
        "incident_id": "inc-v02",
        "product_job_id": "job-v02",
        "private_control_sha256": "1" * 64,
        "public_evidence_bundle_sha256": "2" * 64,
        "flag_profile_sha256": "3" * 64,
        "traffic_profile_sha256": "4" * 64,
        "baseline_sha256": "5" * 64,
        "diagnosis_terminal": "OPEN_WORLD",
        "root_services": ("svc-checkout",),
        "broad_domain": "DEPENDENCY",
        "mechanism": "PRIVATE",
        "evidence_refs": ("obj-1", "obj-2"),
        "fingerprint_id": "fingerprint-v02",
        "family_id": None,
        "open_world_invocations": 1,
        "agent_writes": 0,
        "runbook_executions": 0,
        "cleanup_status": "CLEAN",
        "episode_terminal": PilotEpisodeTerminalV02.PASS,
        "observed_at": datetime(2026, 8, 28, 0, 0, tzinfo=UTC),
    }

    with pytest.raises(ValueError, match="PASS episode"):
        LivePilotEpisodeV02.build(
            **common,
            action_authority_violations=1,
            baseline_restored=True,
        )
    with pytest.raises(ValueError, match="PASS episode"):
        LivePilotEpisodeV02.build(
            **common,
            action_authority_violations=0,
            baseline_restored=False,
        )


def test_episode_repository_is_create_once_and_hash_bound(tmp_path: Path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    repository = PilotEpisodeRepositoryV02(store)
    episode = LivePilotEpisodeV02.build(
        episode_id="episode-n0",
        role=PilotEpisodeRoleV02.LIVE_NO_FAULT_NEGATIVE,
        environment_id="env-v02",
        incident_id="inc-n0",
        product_job_id="job-n0",
        private_control_sha256="1" * 64,
        public_evidence_bundle_sha256="2" * 64,
        flag_profile_sha256="3" * 64,
        traffic_profile_sha256="4" * 64,
        baseline_sha256="5" * 64,
        diagnosis_terminal="NO_INCIDENT",
        root_services=("svc-checkout",),
        broad_domain="NONE",
        mechanism="PRIVATE_NO_FAULT_CONTROL",
        evidence_refs=("obj-1",),
        fingerprint_id=None,
        family_id=None,
        open_world_invocations=0,
        action_authority_violations=0,
        agent_writes=0,
        runbook_executions=0,
        baseline_restored=True,
        cleanup_status="CLEAN",
        episode_terminal=PilotEpisodeTerminalV02.PASS,
        observed_at=datetime(2026, 8, 28, 0, 0, tzinfo=UTC),
    )

    assert repository.create(episode) == episode
    assert repository.create(episode) == episode
    assert repository.get("episode-n0") == episode
    assert repository.list_for_environment("env-v02") == (episode,)

    changed = episode.model_copy(update={"episode_sha256": "f" * 64})
    with pytest.raises(ValueError, match="episode digest differs"):
        repository.create(changed)


def test_runtime_authority_is_owned_environment_scoped_and_create_once(
    tmp_path: Path,
) -> None:
    authority = PilotRuntimeAuthorityV02.build(
        environment_id="env-v02",
        allowed_logical_services=("checkout", "fraud-detection"),
        profile_sha256="1" * 64,
        daemon_identity_sha256="2" * 64,
        docker_context_sha256="3" * 64,
        config_bundle_sha256="4" * 64,
        resolved_sandbox_sha256="5" * 64,
        resolved_endpoints_sha256="6" * 64,
        ownership_scope_sha256="7" * 64,
    )
    data_root = tmp_path / "product"
    path = data_root / "pilot-runtime-authority.json"
    write_pilot_runtime_authority_v02(path, authority)

    assert load_pilot_runtime_authority_v02(path) == authority
    assert authority.read_authority.mode.value == "OWNED_LOCAL"
    assert authority.admits(environment_id="env-v02", services=("checkout",))
    assert not authority.admits(environment_id="other", services=("checkout",))
    assert not authority.admits(environment_id="env-v02", services=("payment",))
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError, match="create-once"):
        write_pilot_runtime_authority_v02(path, authority)

    settings = ProductSettingsV1(
        data_root=data_root,
        pilot_runtime_authority_path=path,
    )
    assert settings.pilot_runtime_authority_path == path.resolve()
    with pytest.raises(ValueError, match="inside Product data root"):
        ProductSettingsV1(
            data_root=data_root,
            pilot_runtime_authority_path=tmp_path / "outside.json",
        )
