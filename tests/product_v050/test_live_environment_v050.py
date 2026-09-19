from types import SimpleNamespace
import pytest
from scripts.product_v050 import live_environment as live


def test_capture_all_births_before_failed_validation(monkeypatch):
    owner = object.__new__(live.Owned)
    owner.nonce = "test"
    owner.births = {"container": {}}
    owner.plan = {"services": {"a": {}, "b": {}}}
    owner.containers = {}
    monkeypatch.setattr(live, "command", lambda *args: "id-a id-b")

    def capture(kind, cid):
        owner.births[kind][cid] = {
            "Config": {"Labels": {"com.docker.compose.service": cid[-1]}}
        }

    owner.capture_birth = capture
    owner.require_birth = lambda kind, cid: owner.births[kind][cid]

    def fail(*args):
        raise ValueError("bad spec")

    owner.validate = fail
    with pytest.raises(ValueError, match="bad spec"):
        owner.capture_created()
    assert set(owner.births["container"]) == {"id-a", "id-b"}


def test_aux_timeout_recovers_exact_created_identity(monkeypatch):
    owner = object.__new__(live.Owned)
    owner.births = {"network": {}}

    def timeout(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(live.BaseOwned, "create_aux", timeout)
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout='[{"Id":"new"}]'),
    )
    owner.capture_birth = lambda kind, cid: owner.births[kind].update({cid: {}})
    with pytest.raises(TimeoutError):
        owner.create_aux("network", "declared-name")
    assert "new" in owner.births["network"]


def test_compose_null_inherits_image_command():
    image = {"Config": {"Entrypoint": ["/fixed"], "Cmd": ["serve"]}}
    assert live.effective_command({"entrypoint": None}, image) == ["/fixed", "serve"]
    assert live.effective_command({"entrypoint": [], "command": ["other"]}, image) == [
        "other"
    ]


def test_created_network_still_requires_exact_name_and_running_requires_id():
    row = {
        "State": {"Status": "created"},
        "NetworkSettings": {"Networks": {"owned": {"NetworkID": ""}}},
    }
    live.validate_networks(row, {"real-id": {"Name": "owned"}})
    with pytest.raises(ValueError, match="NAME"):
        live.validate_networks(row, {"real-id": {"Name": "other"}})
    row["State"]["Status"] = "running"
    with pytest.raises(ValueError, match="NETWORK_DRIFT"):
        live.validate_networks(row, {"real-id": {"Name": "owned"}})


def test_desktop_bind_alias_cannot_escape_exact_repository_source():
    path = str(live.REPO / "config/test.json")
    assert live.source_matches("/host_mnt" + path, path, "desktop-linux", "bind")
    assert not live.source_matches(
        "/host_mnt" + path + "-other", path, "desktop-linux", "bind"
    )
    assert not live.source_matches("/host_mnt" + path, path, "other-context", "bind")
    assert not live.source_matches("/host_mnt/etc", "/etc", "desktop-linux", "bind")


def test_campaign_path_rejects_old_other_and_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "REPO", tmp_path)
    allowed = tmp_path / ".local/product-v050/live-02"
    allowed.parent.mkdir(parents=True)
    assert live.campaign_root(allowed) == allowed
    for name in ("live-01", "live-03", "../escape"):
        with pytest.raises(ValueError, match="CAMPAIGN_PATH"):
            live.campaign_root(allowed.parent / name)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    allowed.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="CAMPAIGN_PATH"):
        live.campaign_root(allowed)


def test_prepare_refuses_prepared_campaign_before_external_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "REPO", tmp_path)
    root = tmp_path / ".local/product-v050/live-02"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text("{}")
    monkeypatch.setattr(
        live, "command", lambda *a: pytest.fail("must not run Docker or git")
    )
    with pytest.raises(ValueError, match="ALREADY_PREPARED"):
        live.prepare(root)


def test_product_reads_owner_directory_without_legacy_global(tmp_path):
    from scripts.product_v050.live_product import Campaign

    campaign = object.__new__(Campaign)
    campaign.root = tmp_path
    (tmp_path / "documents.json").write_text('{"bound": true}')
    assert campaign.load("documents.json") == {"bound": True}


def test_start_checks_drift_before_any_creation():
    owner = object.__new__(live.Owned)

    def fail():
        raise ValueError("NON_OWNED_DRIFT")

    owner.verify = fail
    owner.create_aux = lambda *a: pytest.fail("must not create after drift")
    with pytest.raises(ValueError, match="NON_OWNED_DRIFT"):
        owner.start()


def test_new_baseline_network_scope_cannot_drift(monkeypatch):
    import json

    owner = object.__new__(live.Owned)
    owner.network_extra = {
        "known": {
            "Scope": "local",
            "Attachable": False,
            "Ingress": False,
            "EnableIPv6": False,
        }
    }
    monkeypatch.setattr(live.BaseOwned, "unchanged", lambda self: True)
    row = {"Id": "known", **owner.network_extra["known"]}
    monkeypatch.setattr(live, "command", lambda *a: json.dumps([row]))
    assert owner.unchanged()
    row["Scope"] = "swarm"
    assert not owner.unchanged()


@pytest.mark.parametrize("extra", [False, "READ_FAILED"])
def test_cleanup_extra_drift_or_failure_retains_receipts_and_never_clean(
    tmp_path, monkeypatch, extra
):
    import json

    owner = object.__new__(live.Owned)
    owner.root = tmp_path
    owner.births = {"container": {}, "network": {}, "volume": {"owned": {}}}
    owner.before = {"container": {}, "network": {}, "volume": {}}
    owner.require_birth = lambda *a: {}
    monkeypatch.setattr(live, "command", lambda *a: "")
    monkeypatch.setattr(live, "inventory", lambda: owner.before)

    def check():
        if extra == "READ_FAILED":
            raise TimeoutError()
        return extra

    owner.network_extra_unchanged = check
    with pytest.raises(ValueError, match="CLEANUP_NOT_CLEAN"):
        owner.cleanup()
    result = json.loads((tmp_path / "cleanup.json").read_text())["result"]
    assert len(result["receipts"]) == 1
    assert not result["clean"] and not result["non_owned_unchanged"]
    assert result["remaining"] == {"container": 0, "network": 0, "volume": 0}


def test_admitted_baseline_tamper_rejected_before_docker(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(live, "REPO", tmp_path)
    root = tmp_path / ".local/product-v050/live-02"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"admitted_baseline_sha256": live.digest({"original": True})})
    )
    (root / "admitted-baseline.json").write_text('{"changed": true}')
    monkeypatch.setattr(
        live.BaseOwned, "__init__", lambda *a: pytest.fail("must reject before Docker")
    )
    with pytest.raises(ValueError, match="ADMITTED_BASELINE_BINDING"):
        live.Owned(root)


def test_start_failure_keeps_stderr_when_state_unavailable(tmp_path):
    import json
    import subprocess

    owner = object.__new__(live.Owned)
    owner.root = tmp_path

    def unavailable():
        raise ValueError("DAEMON_DRIFT")

    owner.fresh = unavailable
    error = subprocess.CalledProcessError(
        1,
        ["docker", "compose", "start"],
        output="partial",
        stderr="service failed readiness",
    )
    owner.record_start_failure(error)
    report = json.loads((tmp_path / "start-command-failure.json").read_text())
    assert report["stderr"] == "service failed readiness"
    assert report["returncode"] == 1 and report["state_read_error"] == "ValueError"
    assert report["states"] == {}


def test_only_one_named_diagnostic_directory_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "REPO", tmp_path)
    root = tmp_path / ".local/product-v050/live-02"
    assert live.campaign_root(root / "diagnostic-01") == root / "diagnostic-01"
    with pytest.raises(ValueError, match="CAMPAIGN_PATH"):
        live.campaign_root(root / "diagnostic-02")


def test_explicit_runtime_user_is_verified():
    owner = object.__new__(live.Owned)
    owner.plan = {"services": {"astronomy-db": {"user": "999:999"}}}
    owner.images = {"astronomy-db": {}}
    with pytest.raises(ValueError, match="RUNTIME_USER_DRIFT"):
        owner.validate("astronomy-db", {"Config": {"User": "0:0"}, "HostConfig": {}})


def test_episode_cap_counts_original_and_nested_attempts(tmp_path, monkeypatch):
    from scripts.product_v050 import live_product

    monkeypatch.setattr(live_product, "DATA", tmp_path)
    for index in range(12):
        parent = tmp_path / ("live-01" if index < 6 else "live-02/postgres-user-01")
        p = parent / "episodes" / f"e{index:02}" / "started.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    campaign = object.__new__(live_product.Campaign)
    campaign.root = tmp_path / "live-02/postgres-user-01"
    with pytest.raises(ValueError, match="LIVE_EPISODE_BUDGET"):
        campaign.episode(1)


def test_baseline_excludes_settlement_without_lowering_window_policy(
    tmp_path, monkeypatch
):
    from scripts.product_v050 import live_product as product

    elapsed = [0.0]
    events = []
    monkeypatch.setattr(product.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        product.time, "sleep", lambda n: elapsed.__setitem__(0, elapsed[0] + n)
    )
    campaign = object.__new__(product.Campaign)
    campaign.root = tmp_path
    campaign.env = "test-env"
    campaign.owner = SimpleNamespace(
        verify=lambda: events.append(("verify", elapsed[0]))
    )
    traffic_calls = []
    campaign.traffic = lambda *a, **kw: traffic_calls.append((a, kw))
    campaign.runtime = lambda label: events.append(("runtime", elapsed[0]))
    campaign.lag = lambda: {"lag": 0}
    calls = []
    campaign.work = lambda path, payload: calls.append((elapsed[0], payload))
    campaign.app = SimpleNamespace(
        state=SimpleNamespace(
            services=SimpleNamespace(get_map=lambda env: SimpleNamespace(services=[]))
        )
    )
    campaign.repo = SimpleNamespace(accounting=lambda: {})
    campaign.build_baseline("bounded", 123)
    assert traffic_calls == [(("bounded", 30, 123), {"rate": 1 / 6})]
    assert calls[0][0] == 180
    assert calls[0][1]["build_policy"] == {
        "mode": "DEMO_ONLY",
        "lookback_seconds": 180,
        "window_count": 5,
        "minimum_successful_windows": 5,
        "warmup_seconds": 180,
    }
    assert len([e for e in events if e[0] == "verify"]) == 7
    assert (tmp_path / "bounded-settlement.json").is_file()
