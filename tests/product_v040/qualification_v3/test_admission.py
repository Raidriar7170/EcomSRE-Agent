import sys
import os
from pathlib import Path

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040_v3 import __main__ as entry
from scripts.product.qualification_v040_v3.freeze import GOAL_PATH


def test_missing_review_or_exact_head_ci_cannot_consume_fuse_or_create_runtime(
    monkeypatch,
):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(
        sys, "argv", ["v3", "--run-no-fault", "--goal", str(root / GOAL_PATH)]
    )

    def blocked(_):
        raise QualificationBlocked("EXACT_HEAD_CI_REQUIRED")

    monkeypatch.setattr(entry, "verify_freeze", blocked)

    def forbidden(*args, **kwargs):
        pytest.fail("Protected action reached before admission")

    monkeypatch.setattr(entry, "consume_once", forbidden)
    monkeypatch.setattr(entry, "QualificationRuntime", forbidden)
    prior_umask = os.umask(0o077)
    try:
        with pytest.raises(QualificationBlocked, match="EXACT_HEAD_CI_REQUIRED"):
            entry.main()
    finally:
        os.umask(prior_umask)
