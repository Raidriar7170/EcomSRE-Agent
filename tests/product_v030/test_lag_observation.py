from datetime import UTC, datetime

import httpx
import pytest

from ecomsre_live_sandbox.knowledge_v030 import observe_queue_lag_v030


@pytest.mark.parametrize(
    "age,value,valid",
    [(5, "0", True), (60, "0", False), (-5, "0", False), (5, "NaN", False)],
)
def test_lag_observation_binds_one_instant_and_rejects_bad_samples(age, value, valid):
    instants = []

    def respond(request):
        instants.append(request.url.params["time"])
        timestamp_query = request.url.params["query"].startswith("timestamp(")
        sample = str(datetime.now(UTC).timestamp() - age) if timestamp_query else value
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {
                                "group": "fraud-detection",
                                "topic": "orders",
                                "partition": "0",
                            },
                            "value": [float(instants[-1]), sample],
                        }
                    ],
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if valid:
            assert observe_queue_lag_v030(client)["lag"] == 0
        else:
            with pytest.raises(RuntimeError, match="invalid or stale"):
                observe_queue_lag_v030(client)
    assert len(instants) == 2 and len(set(instants)) == 1
