import pytest
from scripts.product.preflight_v040_v4.cleanup_oom import parse_cleanup_oom
from scripts.product.preflight_v040_v4.oom import parse_oom
from scripts.product.preflight_v040_v4.common import Failure


def capture(observer_uid="1000", observer_cap="0", target_uid="999", target_nnp="0"):
    fields = [
        "OOM",
        "0::/",
        "cgroup2",
        "536870912",
        "0",
        "0",
        "ABSENT",
        "IDENTITY",
        "1",
        "234",
        " ".join([target_uid] * 4),
        "999 " * 4,
        "999",
        "0",
        "0",
        target_nnp,
        "IDENTITY",
        "42",
        "567",
        " ".join([observer_uid] * 4),
        "1000 " * 4,
        "1000",
        observer_cap,
        "0",
        "0",
        "COMPLETE",
        "",
    ]
    part = "\0".join(fields)
    return part + part


def test_cleanup_observes_actual_target_credentials_without_weakening_probe():
    raw = capture()
    parsed = parse_cleanup_oom(raw)
    assert parsed["first"]["uids"] == [999] * 4
    assert parsed["first"]["no_new_privs"] == 0
    with pytest.raises(Failure):
        parse_oom(raw)


@pytest.mark.parametrize(
    "raw",
    [
        capture(observer_uid="0"),
        capture(observer_cap="1"),
        capture(target_uid="-1"),
        capture(target_nnp="2"),
        capture()[:-1],
        capture().replace("567", "568", 1),
    ],
)
def test_cleanup_rejects_privileged_observer_partial_and_raced_evidence(raw):
    with pytest.raises(Failure):
        parse_cleanup_oom(raw)


@pytest.fixture
def proof_chain(tmp_path):
    from copy import deepcopy
    from scripts.product.preflight_v040_v4 import cleanup_oom as c
    from scripts.product.preflight_v040_v4.common import digest, seal
    from scripts.product.preflight_v040_v4.identity import immutable
    from scripts.product.preflight_v040_v4.oom import OOM_PROTOCOL

    row = {
        "Id": c.CONTAINER,
        "Created": "t0",
        "Image": "sha256:pinned",
        "Name": c.ROLE,
        "Config": {},
        "HostConfig": {"OomKillDisable": None, "Memory": 536870912},
        "Mounts": [],
        "RestartCount": 0,
        "State": {"Running": True, "Pid": 1458, "StartedAt": "t1"},
        "NetworkSettings": {"Networks": {"net": {"NetworkID": "n", "EndpointID": "e"}}},
    }
    born = deepcopy(row)
    born["HostConfig"]["OomKillDisable"] = False
    binding = {"daemon": "d"}
    view = {"binding": binding, "resources": {"container": [row]}}
    create_intent = {"command": ["create"]}
    create = {"intent_digest": digest(create_intent)}
    birth = {
        "record": born,
        "binding": binding,
        "role": c.ROLE,
        "create_key": "create",
        "create_receipt_digest": digest(create),
        "create_intent_digest": digest(create_intent),
    }
    admission = {
        "cleanup_head": "h",
        "running_cleanup": {
            "policy": c.POLICY,
            "role": c.ROLE,
            "container_id": c.CONTAINER,
        },
    }
    intent = {"command": ["container", "start", c.CONTAINER]}
    receipt = {
        "intent_digest": digest(intent),
        "postcondition": True,
        "outcome": {"client_exit_status": 0},
        "observation": view,
    }
    census = {
        "policy": c.POLICY,
        "command": [
            "exec",
            "--user",
            "1000:1000",
            c.CONTAINER,
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            OOM_PROTOCOL,
        ],
        "admission_digest": digest(admission),
        "birth_digest": digest(birth),
        "start_receipt_digest": digest(receipt),
        "before": view,
    }
    proof = {
        "policy": c.POLICY,
        "attempt_id": c.ATTEMPT,
        "role": c.ROLE,
        "container_id": c.CONTAINER,
        "cleanup_head": "h",
        "admission_digest": digest(admission),
        "birth_record_digest": digest(born),
        "create_receipt_digest": digest(create),
        "binding_digest": digest(binding),
        "immutable_digest": digest(immutable(row)),
        "started_at": "t1",
        "pid": 1458,
        "before_digest": digest(view),
        "after_digest": digest(view),
        "evidence": c.parse_cleanup_oom(capture()),
        "networks": row["NetworkSettings"]["Networks"],
    }
    for name, value in {
        "cleanup-only-oom-proof.json": proof,
        "cleanup-oom-census-intent.json": census,
        "cleanup-oom-census-raw.json": {"returncode": 0, "stdout": capture()},
        "cleanup-oom-census-after.json": view,
        "journal/receipts/start-astronomy-db.json": receipt,
        "journal/intents/start-astronomy-db.json": intent,
        "journal/receipts/create.json": create,
        "journal/intents/create.json": create_intent,
    }.items():
        seal(tmp_path, name, value)
    attached = {
        **birth,
        "cleanup_only_oom_proof": proof,
        "cleanup_admission": admission,
        "cleanup_proof_root": str(tmp_path),
    }
    return attached, row, binding, tmp_path


def test_proof_stop_then_remove_and_process_rejections(proof_chain):
    from copy import deepcopy
    from scripts.product.preflight_v040_v4.cleanup_oom import permits_cleanup, ATTEMPT

    birth, row, binding, _ = proof_chain
    assert permits_cleanup(birth, row, binding, ATTEMPT)
    stopped = deepcopy(row)
    stopped["State"]["Running"] = False
    stopped["State"]["Pid"] = 0
    assert permits_cleanup(birth, stopped, binding, ATTEMPT)
    for key, value in [("Pid", 1459), ("StartedAt", "replacement")]:
        bad = deepcopy(row)
        bad["State"][key] = value
        assert not permits_cleanup(birth, bad, binding, ATTEMPT)
    bad = deepcopy(row)
    bad["RestartCount"] = 1
    assert not permits_cleanup(birth, bad, binding, ATTEMPT)
    bad = deepcopy(row)
    bad["NetworkSettings"]["Networks"]["net"]["EndpointID"] = "other"
    assert not permits_cleanup(birth, bad, binding, ATTEMPT)
    stopped["State"]["Pid"] = 1
    assert not permits_cleanup(birth, stopped, binding, ATTEMPT)


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("cleanup-only-oom-proof.json", lambda x: x.update(evidence={})),
        (
            "cleanup-oom-census-raw.json",
            lambda x: x.update(stdout=capture(observer_uid="0")),
        ),
        (
            "cleanup-oom-census-after.json",
            lambda x: x["resources"]["container"][0]["NetworkSettings"]["Networks"][
                "net"
            ].update(EndpointID="other"),
        ),
        (
            "journal/receipts/start-astronomy-db.json",
            lambda x: x.update(postcondition=False),
        ),
        ("journal/intents/create.json", lambda x: x.update(command=["other"])),
    ],
)
def test_persisted_proof_chain_tamper_rejected(proof_chain, name, mutate):
    import json
    from scripts.product.preflight_v040_v4.cleanup_oom import permits_cleanup, ATTEMPT

    birth, row, binding, root = proof_chain
    path = root / name
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))
    with pytest.raises(Failure):
        permits_cleanup(birth, row, binding, ATTEMPT)
