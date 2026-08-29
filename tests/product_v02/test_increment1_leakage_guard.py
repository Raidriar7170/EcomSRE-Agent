from __future__ import annotations

from ecomsre.product.pilot.leakage_guard_v02 import normalized_observed_log_tokens_v02


def test_fingerprint_log_tokens_remove_private_truth_and_episode_identifiers() -> None:
    tokens = normalized_observed_log_tokens_v02(
        "kafkaQueueProblems ecomsre-v02-10 POSITIVE_FIT episode-p1 "
        "order-48291 trace-ae019c2421c3499fb90289b192668155 "
        "queue overload waiting checkout"
    )

    assert tokens == ("checkout", "overload", "queue", "waiting")
    serialized = " ".join(tokens)
    assert "kafkaqueueproblems" not in serialized
    assert "positive" not in serialized
    assert "48291" not in serialized
    assert "ae019c" not in serialized


def test_guard_keeps_generic_observed_symptoms_only_once() -> None:
    assert normalized_observed_log_tokens_v02(
        "Queue waiting; queue overload observed from checkout"
    ) == ("checkout", "observed", "overload", "queue", "waiting")
