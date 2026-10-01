"""Offline entrypoint repeats into independent outputs without mutating sources."""

import hashlib
import json

import pytest
from test_ingestion_evidence import FIXTURE, QUERIES, setup, body
from scripts.product_v050 import ingestion_evidence as ie
from scripts.product_v050.calibration_replay import replay


@pytest.mark.parametrize("modern", [False, True])
def test_replay_retains_failure_and_repeats_without_original_store_writes(
    tmp_path, modern
):
    capture = tmp_path / "retained"
    objects = tmp_path / "objects"
    capture.mkdir()

    def put(value):
        raw = json.dumps(value).encode()
        h = hashlib.sha256(raw).hexdigest()
        path = objects / "sha256" / h[:2] / (h + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return h

    def read(h):
        return (objects / "sha256" / h[:2] / (h + ".json")).read_bytes()

    binding, req = setup()
    if modern:
        binding = ie.topology_v3(
            FIXTURE["collector"], FIXTURE["prometheus_command"], QUERIES
        )
    _, _, params = ie.expected_reads(
        req["query"], req["start"], req["end"], version=binding["version"]
    )[0]
    entries = [
        dict(
            params=params,
            occurrence="retained",
            truncated=False,
            action_context=dict(
                ingestion_version=binding["version"],
                incident_id=None,
                binding_sha256=binding["sha256"],
            ),
            method="GET",
            url="http://localhost/api/v1/query",
            status_code=200,
            requested_at="2026-09-27T00:00:00+00:00",
            received_at="2026-09-27T00:00:00+00:00",
            response_object_sha256=put(body(range(1000, 1101, 10))),
        )
    ]
    actual = ie.verify(
        binding,
        entries=entries,
        occurrence="retained",
        incident_id=None,
        queries=QUERIES,
        read_bytes=read,
        collector=FIXTURE["collector"],
        command=FIXTURE["prometheus_command"],
        requirements=[req],
    )
    assert not actual["passed"]
    documents = {
        "ingestion-readiness-result.json": dict(assessment=actual, requirements=[req]),
        "validation-authorization.json": {
            "plan": {"collection": {"ingestion": binding, "actual_queries": QUERIES}}
        },
        "collector.json": FIXTURE["collector"],
        "compose.json": {
            "services": {"prometheus": {"command": FIXTURE["prometheus_command"]}}
        },
    }
    for name, value in documents.items():
        (capture / name).write_text(json.dumps(value))
    (capture / "ingestion-readiness.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries)
    )
    before = {
        str(p): p.read_bytes()
        for root in [capture, objects]
        for p in root.rglob("*")
        if p.is_file()
    }
    a = replay(capture, objects, tmp_path / "a")
    b = replay(capture, objects, tmp_path / "b")
    assert a["query_results"] == b["query_results"]
    assert a["query_results"][0]["sample_diagnostics"]
    assert "MISSING_RAW_SAMPLE_QUERY" not in a["query_results"][0]["selectors"].values()
    assert not a["formal_pass"] and not a["promotion_eligible"]
    assert a["query_results"][0]["coverage"] == "INVALID_SAMPLE_EVIDENCE"
    assert all(
        __import__("pathlib").Path(p).read_bytes() == value
        for p, value in before.items()
    )
    with pytest.raises(ValueError, match="already exists"):
        replay(capture, objects, tmp_path / "a")
    with pytest.raises(ValueError, match="separate"):
        replay(capture, objects, capture / "output")
