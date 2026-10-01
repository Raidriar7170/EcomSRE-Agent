import json
from pathlib import Path
import pytest
from scripts.ci.verify_product_v050_live_resume import verify_claims


def result():
    return json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/results/product-v050/live-resume/result.json"
        ).read_text()
    )


def test_actual_prestart_boundary():
    assert verify_claims(result())["global_clean"] is False


@pytest.mark.parametrize("change", ["clean", "live", "promotion", "budget", "drift"])
def test_no_success_from_safety_block(change):
    r = result()
    if change == "clean":
        r["owned_cleanup"]["clean"] = True
    if change == "live":
        r["live_episode_count"] = 1
    if change == "promotion":
        r["promotion"] = "PASS"
    if change == "budget":
        r["provider"]["provider_request_count"] = 0
    if change == "drift":
        r["network_observations"][1]["identity_sha256"] = r["network_observations"][0][
            "identity_sha256"
        ]
    with pytest.raises(ValueError):
        verify_claims(r)
