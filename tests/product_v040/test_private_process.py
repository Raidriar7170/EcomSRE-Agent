"""Fixed role bootstrap applies umask before the application creates storage."""

import os
from pathlib import Path
import sqlite3
import stat
import sys

import pytest

from ecomsre.product.remediation import private_process


@pytest.mark.parametrize('role', private_process.ROLES)
def test_role_creates_private_database_wal_shm_and_directory(monkeypatch, tmp_path, role):
    previous = os.umask(0o022)
    observed = []

    def application(module, *, run_name):
        expected = ('ecomsre.product.app' if role == 'api' else 'ecomsre.product.jobs.worker' if role == 'worker' else 'ecomsre.product.remediation.runtime')
        assert module == expected and run_name == '__main__'
        assert sys.argv == [expected] + ([] if role in {'api', 'worker'} else [role])
        root = tmp_path / 'new-private-data'
        root.mkdir()
        with sqlite3.connect(root / 'state.sqlite3') as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE private_state(value TEXT)')
            connection.execute("INSERT INTO private_state VALUES ('fixture')")
            for path in root.iterdir():
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
                observed.append(path.name)
            assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert Path(root / 'state.sqlite3').exists()

    try:
        monkeypatch.setattr(sys, 'argv', ['private-process', role])
        monkeypatch.setattr(private_process.runpy, 'run_module', application)
        private_process.main()
        assert set(observed) == {'state.sqlite3', 'state.sqlite3-wal', 'state.sqlite3-shm'}
    finally:
        os.umask(previous)


def test_arbitrary_module_role_is_rejected_before_dispatch(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['private-process', 'os'])
    with pytest.raises(SystemExit) as error:
        private_process.main()
    assert error.value.code == 2


@pytest.mark.parametrize("defect", [None, "file_mode", "directory_mode", "symlink"])
def test_live_permission_gate_reports_only_actual_observed_files(tmp_path, defect):
    from types import SimpleNamespace
    from scripts.product.v040_gates import private_storage_modes
    for name in ("product", "ledger"):
        (tmp_path / name).mkdir(mode=0o700)
    database = tmp_path / "product/product.sqlite3"
    database.touch(mode=0o600)
    if defect == "file_mode":
        database.chmod(0o644)
    elif defect == "directory_mode":
        database.parent.chmod(0o755)
    elif defect == "symlink":
        (database.parent / "alias").symlink_to(database)
    runtime = SimpleNamespace(private=tmp_path)
    if defect is not None:
        with pytest.raises(ValueError, match="private raw storage"):
            private_storage_modes(runtime)
    else:
        assert private_storage_modes(runtime) == {"status": "PASS", "regular_files": 1,
            "directories": 2, "databases": 1, "wal_files": 0, "shm_files": 0}
