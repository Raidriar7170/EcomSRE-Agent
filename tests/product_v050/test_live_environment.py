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
