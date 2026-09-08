"""Exercise the coordinator across daemon-side success and client-side interruption."""

from copy import deepcopy
import pytest
from scripts.product.preflight_v040_v4.common import Failure, LABEL, load
from scripts.product.preflight_v040_v4.resources import Resources
from scripts.product.preflight_v040_v4.cleanup import nonowned


class FakeDocker:
    def __init__(self):
        self.bound = None
        self.resources = {"container": [], "network": [], "volume": []}
        self.commands = []

    def binding(self):
        return {"daemon": {"ID": "d"}, "context": {"Name": "local"}}

    def capture(self):
        return {
            "binding": self.binding(),
            "resources": deepcopy(self.resources),
            "ids": {
                k: [r["Name" if k == "volume" else "Id"] for r in rows]
                for k, rows in self.resources.items()
            },
            "images": [],
        }


class FakeResources(Resources):
    def command(self, args):
        self.docker.commands.append(args)
        kind = args[0]
        if args[1] == "create":
            name = args[-1]
            plan = next(v for v in self.plan[kind + "s"].values() if v["name"] == name)
            self.docker.resources[kind].append(
                {
                    "Name": name,
                    "CreatedAt": "now",
                    "Driver": "local",
                    "Scope": "local",
                    "Options": None,
                    "Labels": plan["labels"],
                    "Mountpoint": "/vol/" + name,
                }
            )
        elif args[1] == "rm":
            self.docker.resources[kind] = [
                r for r in self.docker.resources[kind] if r["Name"] != args[-1]
            ]
        else:
            raise AssertionError(args)
        return {"client_exit_status": 0}


def plan():
    return {
        "attempt_id": "0" * 32,
        "containers": {},
        "networks": {},
        "builtin_none_id": "n" * 64,
        "sandbox_start_order": [],
        "volumes": {
            key: {"name": key, "driver": "local", "labels": {LABEL: "0" * 32}}
            for key in ("v1", "v2")
        },
    }


def test_partial_create_seals_valid_birth_and_can_cleanup(tmp_path):
    docker = FakeDocker()
    resources = FakeResources(tmp_path, plan(), docker, "head")
    with pytest.raises(Failure, match="POSTCONDITION"):
        resources.create_group(
            "partial",
            ["volume", "create", "v1"],
            [("volume", "v1", "v1"), ("volume", "v2", "v2")],
        )
    assert len(resources.births) == 1
    assert resources.births[0]["create_receipt_digest"]
    resources.remove(resources.births[0])
    assert docker.resources["volume"] == []
    assert docker.commands == [["volume", "create", "v1"], ["volume", "rm", "v1"]]


def test_create_success_without_birth_then_resume(tmp_path):
    docker = FakeDocker()
    resources = FakeResources(tmp_path, plan(), docker, "head")
    real_birth = resources.birth

    def interrupted(*args):
        raise KeyboardInterrupt()

    resources.birth = interrupted
    with pytest.raises(KeyboardInterrupt):
        resources.storage("volume", "v1")
    assert len(docker.commands) == 1
    resources.birth = real_birth
    resumed = FakeResources(tmp_path, plan(), docker, "head")
    resumed.storage("volume", "v1")
    assert len(resumed.births) == 1 and len(docker.commands) == 1
    resumed.remove(resumed.births[0])


def test_remove_success_without_receipt_then_resume(tmp_path):
    docker = FakeDocker()
    resources = FakeResources(tmp_path, plan(), docker, "head")
    resources.storage("volume", "v1")
    original_capture = resources.capture

    def interrupted(label):
        if label == "after-remove-v1":
            raise KeyboardInterrupt()
        return original_capture(label)

    resources.capture = interrupted
    with pytest.raises(KeyboardInterrupt):
        resources.remove(resources.births[0])
    assert docker.resources["volume"] == []
    resumed = FakeResources(tmp_path, plan(), docker, "head")
    resumed.remove(resumed.births[0])
    assert load(tmp_path / "journal/receipts/remove-v1.json")["postcondition"]
    assert len(docker.commands) == 2


def test_nonowned_keeps_oom_and_network_ids():
    view = {
        "binding": {},
        "resources": {
            "container": [
                {
                    "Id": "x",
                    "Config": {"Labels": {}},
                    "HostConfig": {"OomKillDisable": False},
                    "NetworkSettings": {"Networks": {"same-name": {"NetworkID": "a"}}},
                }
            ],
            "network": [],
            "volume": [],
        },
    }
    a = nonowned(view, [], "attempt")
    changed = deepcopy(view)
    changed["resources"]["container"][0]["HostConfig"]["OomKillDisable"] = True
    assert nonowned(changed, [], "attempt") != a
    changed = deepcopy(view)
    changed["resources"]["container"][0]["NetworkSettings"]["Networks"]["same-name"][
        "NetworkID"
    ] = "b"
    assert nonowned(changed, [], "attempt") != a


def test_invalid_created_resource_still_consumes_budget(tmp_path):
    from scripts.product.preflight_v040_v4.budget import reserve

    root = tmp_path / ("0" * 32)
    reserve(tmp_path, "0" * 32, "source")
    docker = FakeDocker()
    resources = FakeResources(root, plan(), docker, "head")

    def reject(*args):
        raise Failure("INVALID_BIRTH")

    resources.validate_birth = reject
    with pytest.raises(Failure, match="UNPROVABLE"):
        resources.storage("volume", "v1")
    assert not resources.births
    assert load(tmp_path / "budget/attempt-01.json")["attempt_id"] == "0" * 32
    assert load(root / "first-runtime-creation.json")["observed_new_resources"]


def test_restart_recovers_budget_before_birth_sealing(tmp_path):
    from scripts.product.preflight_v040_v4.budget import reserve

    root = tmp_path / ("0" * 32)
    reserve(tmp_path, "0" * 32, "source")
    docker = FakeDocker()
    resources = FakeResources(root, plan(), docker, "head")
    resources.consume_observation = lambda _: None
    resources.birth = lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        resources.storage("volume", "v1")
    assert not (tmp_path / "budget/attempt-01.json").exists()
    FakeResources(root, plan(), docker, "head")
    assert load(tmp_path / "budget/attempt-01.json")["attempt_id"] == "0" * 32


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
@pytest.mark.parametrize(
    "labels,name",
    [
        ({"io.ecomsre.preflight.v4.attempt": "old"}, "opaque"),
        ({"io.ecomsre.qualification": "old"}, "opaque"),
        ({}, "ecomsre-old"),
    ],
)
def test_old_owned_resources_cannot_become_nonowned_baseline(kind, labels, name):
    from scripts.product.preflight_v040_v4.host import require_no_preexisting_owned

    row = {"Name": name, "Labels": labels, "Config": {"Labels": labels}}
    with pytest.raises(Failure, match="PREEXISTING_PROJECT_RESOURCE"):
        require_no_preexisting_owned({"resources": {kind: [row]}})
