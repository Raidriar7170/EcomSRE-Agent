from copy import deepcopy
import json
from pathlib import Path

import pytest

from ecomsre_live_sandbox.contracts import canonical_sha256
from ecomsre_live_sandbox.knowledge_v030 import (
    build_goal_flag_documents_v030,
    initialize_goal_flag_file_v030,
)
from ecomsre_live_sandbox.product_v030 import build_product_v030_runtime_bundle


ROOT = Path(__file__).resolve().parents[2]


def test_existing_ui_serialization_of_baseline_is_preserved(tmp_path):
    path = tmp_path / "flags.json"
    raw = '{"flags": {"example": {"defaultVariant": "off"}}}'
    path.write_text(raw)
    initialize_goal_flag_file_v030(path, json.loads(raw))
    assert path.read_text() == raw
    assert path.stat().st_mode & 0o777 == 0o600


def test_existing_nonbaseline_flag_file_is_not_rewritten(tmp_path):
    path = tmp_path / "flags.json"
    path.write_text('{"flags": {"example": "on"}}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="baseline"):
        initialize_goal_flag_file_v030(path, {"flags": {"example": "off"}})
    assert path.read_bytes() == before


def test_goal_flag_documents_disable_background_traffic_and_isolate_faults():
    upstream = json.loads(
        (ROOT / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json").read_text()
    )
    original = deepcopy(upstream)
    bundle, documents = build_goal_flag_documents_v030(
        upstream, build_product_v030_runtime_bundle(ROOT)
    )
    assert upstream == original
    assert set(documents) == {"BASELINE", "QUEUE", "PAYMENT"}
    for name, document in documents.items():
        flags = document["flags"]
        assert flags["loadGeneratorTraffic"]["defaultVariant"] == "off"
        assert flags["kafkaQueueProblems"]["defaultVariant"] == (
            "on" if name == "QUEUE" else "off"
        )
        assert flags["paymentFailure"]["defaultVariant"] == (
            "100%" if name == "PAYMENT" else "off"
        )
    assert (
        canonical_sha256(documents["BASELINE"])
        == bundle.scenario.baseline_document_sha256
    )
    assert (
        canonical_sha256(documents["PAYMENT"]) == bundle.scenario.fault_document_sha256
    )
    normalized = deepcopy(documents["QUEUE"])
    normalized["flags"]["kafkaQueueProblems"]["defaultVariant"] = "off"
    assert normalized == documents["BASELINE"]


def test_goal_flag_documents_reject_upstream_drift():
    upstream = json.loads(
        (ROOT / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json").read_text()
    )
    upstream["flags"]["kafkaQueueProblems"]["variants"]["on"] = 50
    with pytest.raises(ValueError):
        build_goal_flag_documents_v030(
            upstream, build_product_v030_runtime_bundle(ROOT)
        )
