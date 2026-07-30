import re

from ecomsre.evidence.models import new_run_id


def test_run_ids_are_unique_opaque_and_path_safe() -> None:
    run_ids = {new_run_id() for _ in range(1_000)}

    assert len(run_ids) == 1_000
    assert all(re.fullmatch(r"[0-9a-f]{32}", run_id) for run_id in run_ids)
    assert all("/" not in run_id and "\\" not in run_id for run_id in run_ids)
