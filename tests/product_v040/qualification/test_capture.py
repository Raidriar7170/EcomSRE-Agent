from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from scripts.product.qualification_v040.capture import capture
from scripts.product.qualification_v040.guard import (
    QualificationBlocked,
    validate_envelope,
)


@pytest.mark.parametrize("drift", [None, "volume", "network", "image", "enumeration"])
def test_live_adapter_retains_both_complete_inspect_passes(tmp_path, drift):
    original = {
        "container": [],
        "volume": [{"Name": "v", "CreatedAt": "time1", "Labels": {}}],
        "network": [{"Id": "n", "Name": "bridge", "Labels": {}}],
        "image": [{"Id": "i", "Config": {"Volumes": {}}}],
    }
    calls = []
    counts = {}

    def docker(kind, verb, *args):
        calls.append((kind, verb, args))
        key = (kind, verb)
        counts[key] = counts.get(key, 0) + 1
        if verb == "ls":
            if drift == "enumeration" and kind == "volume" and counts[key] == 2:
                return "v new"
            return " ".join(
                r["Name"] if kind == "volume" else r["Id"] for r in original[kind]
            )
        rows = deepcopy(original[kind])
        if drift == kind and counts[key] == 2:
            rows[0]["Labels"] = {"replaced": "yes"}
        return json.dumps(rows)

    runtime = SimpleNamespace(
        docker=docker, boundary=lambda: {"id": "daemon"}, repository=tmp_path
    )
    result = capture(runtime, "q", [], set())
    assert len([c for c in calls if c[1] == "ls"]) == 8
    assert len([c for c in calls if c[1] == "inspect"]) == 6
    assert set(result["inspect"]) == set(result["inspect_after"])
    if drift:
        with pytest.raises(QualificationBlocked, match="RACED_"):
            validate_envelope(result)
    else:
        validate_envelope(result)
