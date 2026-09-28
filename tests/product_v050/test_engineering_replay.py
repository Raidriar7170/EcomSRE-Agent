"""Fresh engineering captures replay without touching retained inputs."""

import hashlib
import json

import pytest
from test_ingestion_evidence import FIXTURE, SELECTOR, body
from scripts.product_v050 import (
    engineering_replay as er,
    ingestion_evidence as ie,
    sampling_support as ss,
)


def test_replay_round_is_repeatable_and_checks_raw_binding(tmp_path):
    root = tmp_path / "capture"
    round_dir = root / "round-1"
    round_dir.mkdir(parents=True)
    query = f"sum({SELECTOR})"
    params = ie.expected_reads(query, 1000, 1300, version=ss.VERSION)[0][2]
    payload = body()
    detail = ss.assess(
        payload,
        SELECTOR,
        1000,
        1300,
        ss.profile(FIXTURE["collector"], SELECTOR),
        query_start=1000,
        inner_seconds=0,
    )
    states = {SELECTOR: "FRESH_COVERED"}
    original = dict(
        start=1000,
        end=1300,
        queries=[
            dict(
                key="prometheus:queue_lag:fraud-detection",
                query=query,
                actual_query_available=True,
                states=states,
                diagnostics={SELECTOR: detail},
                correspondence=[],
                coverage="SUPPORTED",
            )
        ],
    )
    for p, value in [
        (root / "application-support.json", {}),
        (root / "collector.json", FIXTURE["collector"]),
        (round_dir / "result.json", original),
    ]:
        p.write_text(json.dumps(value))
    for name, args in [
        ("00-query", dict(query=query, start=1000, end=1300, step=10)),
        ("00-raw-0", params),
    ]:
        raw = json.dumps(payload).encode()
        (round_dir / (name + ".body")).write_bytes(raw)
        (round_dir / (name + ".json")).write_text(
            json.dumps(
                dict(
                    params=args,
                    body_sha256=hashlib.sha256(raw).hexdigest(),
                    status=200,
                    error=None,
                    truncated=False,
                )
            )
        )
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    a = er.replay(root, 1, tmp_path / "a")
    b = er.replay(root, 1, tmp_path / "b")
    assert a["original_diagnostics_reproduced"] and b["original_diagnostics_reproduced"]
    assert not a["formal_pass"] and not a["promotion_eligible"]
    assert all(p.read_bytes() == value for p, value in before.items())
    with pytest.raises(ValueError, match="fresh separate"):
        er.replay(root, 1, tmp_path / "a")
    with pytest.raises(ValueError, match="fresh separate"):
        er.replay(root, 1, root / "bad")
    (round_dir / "00-raw-0.body").write_text("{}")
    with pytest.raises(ValueError, match="binding"):
        er.replay(root, 1, tmp_path / "tampered")
