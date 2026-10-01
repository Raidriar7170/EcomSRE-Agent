import pytest
from ecomsre.product.connectors.opensearch import _project_observer_message_v1

MESSAGE = 'Created log for partition __consumer_offsets-2 in /tmp/kafka-logs/__consumer_offsets-2 with properties {cleanup.policy=compact, compression.type="producer", segment.bytes=104857600}'


def test_observed_kafka_initialization_keeps_fact_without_false_truth_marker():
    projected = _project_observer_message_v1(MESSAGE, policy="OBSERVER_SYMPTOM_V1")
    assert projected == MESSAGE.replace('"producer"', "producer")
    assert _project_observer_message_v1(MESSAGE, policy="AS_OBSERVED") == MESSAGE


@pytest.mark.parametrize(
    "message",
    [
        MESSAGE + " FeatureFlag 'unseenControl' is on",
        MESSAGE.replace('"producer"', '"unseenControl"'),
        "unrecognized 'unseenControl' value",
    ],
)
def test_kafka_normalization_never_admits_unknown_control_text(message):
    with pytest.raises(ValueError, match="control truth"):
        _project_observer_message_v1(message, policy="OBSERVER_SYMPTOM_V1")
