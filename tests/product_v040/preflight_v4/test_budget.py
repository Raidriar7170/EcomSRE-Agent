import pytest
from scripts.product.preflight_v040_v4.budget import consume, pass_streak
from scripts.product.preflight_v040_v4.common import Failure, seal


def test_five_attempts_and_same_failed_surface(tmp_path):
    for i in range(5):
        attempt = str(i) * 32
        consume(tmp_path, attempt, str(i))
        seal(tmp_path / attempt, "cleanup-result.json", {"status": "CLEAN"})
        seal(tmp_path / attempt, "attempt-result.json", {"status": "FAILED"})
        with pytest.raises(Failure, match="IDENTICAL_FAILED|EXHAUSTED"):
            consume(tmp_path, "f" * 32, str(i))
    with pytest.raises(Failure, match="EXHAUSTED"):
        consume(tmp_path, "a" * 32, "new")


def test_unclean_attempt_blocks_next(tmp_path):
    consume(tmp_path, "0" * 32, "source")
    with pytest.raises(Failure, match="CLEANUP_PENDING"):
        consume(tmp_path, "1" * 32, "source2")


def test_two_consecutive_same_surface_passes():
    def result(status="PASS", surface="a", cleanup="CLEAN"):
        return {"status": status, "runtime_surface": surface, "cleanup_status": cleanup}

    assert pass_streak([result(), result()]) == 2
    assert pass_streak([result(), result(surface="b")]) == 1
    assert pass_streak([result(), result(status="FAILED"), result()]) == 1
    assert pass_streak([result(), result(cleanup="BLOCKED")]) == 0


def test_reservation_rejects_identical_failure_before_any_creation(tmp_path):
    from scripts.product.preflight_v040_v4.budget import reserve

    consume(tmp_path, "0" * 32, "source")
    seal(tmp_path / ("0" * 32), "cleanup-result.json", {"status": "CLEAN"})
    seal(tmp_path / ("0" * 32), "attempt-result.json", {"status": "FAILED"})
    with pytest.raises(Failure, match="IDENTICAL_FAILED"):
        reserve(tmp_path, "1" * 32, "source")
    assert not (tmp_path / "budget/reservations" / ("1" * 32 + ".json")).exists()
    reserve(tmp_path, "1" * 32, "repaired")
