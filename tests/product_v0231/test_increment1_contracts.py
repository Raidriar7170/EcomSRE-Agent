from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3

from pydantic import ValidationError
import pytest

from ecomsre.product.pilot import runtime_continuity_v0231
from ecomsre.product.pilot.runtime_continuity_v0231 import (
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    SquashMergeBoundFileV0231,
    SquashMergeHistoryBindingV0231,
    _bound_file_bytes,
    _open_bound_path_fd,
    _open_root_fd,
    _read_only_connection,
    _sqlite_bundle_snapshot,
)


SHA = "a" * 64
HEAD = "b" * 40


def _private_binding(**updates: str) -> ProductV023PrivateStateBindingV0231:
    payload = {
        "baseline_private_report_locator": ".local/product-v023/report.json",
        "baseline_private_report_sha256": SHA,
        "product_data_root_locator": ".local/product-v023/product",
        "product_database_sha256": SHA,
        "product_database_wal_sha256": SHA,
        "product_database_shm_sha256": SHA,
        "nofault_blocker_locator": ".local/product-v023/nofault.json",
        "nofault_blocker_sha256": SHA,
        "runtime_authority_locator": ".local/product-v023/runtime-authority.json",
        "runtime_authority_file_sha256": SHA,
        "resolved_compose_locator": ".local/product-v023/resolved-compose.json",
        "resolved_compose_file_sha256": SHA,
        "flagd_file_locator": ".local/product-v023/demo.flagd.json",
        "flagd_file_sha256": SHA,
    }
    return ProductV023PrivateStateBindingV0231.model_validate({**payload, **updates})


def test_squash_history_binding_is_canonical_and_self_sealed() -> None:
    binding = SquashMergeHistoryBindingV0231.build(
        source_pr=75,
        source_branch="codex/product-v02-live-knowledge-loop-pilot",
        source_head=HEAD,
        source_terminal="BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE",
        import_pr=79,
        import_squash_merge_commit="c" * 40,
        public_base="d" * 40,
        bound_files=(
            SquashMergeBoundFileV0231(
                path="docs/results/product-v02-live-knowledge-loop.json",
                sha256=SHA,
                size_bytes=10,
            ),
        ),
    )

    assert binding.source_pr == 75
    assert binding.import_pr == 79
    assert binding.binding_sha256 != SHA

    with pytest.raises(ValidationError, match="squash history binding digest differs"):
        binding.model_copy(update={"binding_sha256": SHA}).model_validate(
            {**binding.model_dump(mode="json"), "binding_sha256": SHA}
        )


def test_private_state_binding_rejects_absolute_or_parent_locators() -> None:
    payload = _private_binding().model_dump(mode="json")

    for invalid in ("/private/report.json", "../private/report.json"):
        with pytest.raises(ValidationError, match="repository-relative"):
            ProductV023PrivateStateBindingV0231.model_validate(
                {**payload, "baseline_private_report_locator": invalid}
            )


def test_fd_relative_private_read_rejects_symlink_component(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    payload = b"private"
    (target / "report.json").write_bytes(payload)
    (tmp_path / "private").symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError):
        _bound_file_bytes(
            tmp_path,
            "private/report.json",
            hashlib.sha256(payload).hexdigest(),
        )


def test_fd_relative_private_read_holds_opened_directory_during_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = b"original"
    attacker = b"attacker"
    private = tmp_path / "private"
    private.mkdir()
    (private / "report.json").write_bytes(original)
    attacker_dir = tmp_path / "attacker"
    attacker_dir.mkdir()
    (attacker_dir / "report.json").write_bytes(attacker)
    real_open = runtime_continuity_v0231.os.open
    swapped = False

    def racing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if path == "report.json" and not swapped:
            private.rename(tmp_path / "private-frozen")
            private.symlink_to(attacker_dir, target_is_directory=True)
            swapped = True
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runtime_continuity_v0231.os, "open", racing_open)

    assert _bound_file_bytes(
        tmp_path,
        "private/report.json",
        hashlib.sha256(original).hexdigest(),
    ) == original


def test_sqlite_bundle_rejects_sidecar_creation_or_deletion(tmp_path: Path) -> None:
    data_root = tmp_path / "product"
    data_root.mkdir()
    payloads = {
        "product.sqlite3": b"database",
        "product.sqlite3-shm": b"shared-memory",
        "product.sqlite3-wal": b"",
    }
    for name, payload in payloads.items():
        (data_root / name).write_bytes(payload)
    binding = _private_binding(
        product_data_root_locator="product",
        product_database_sha256=hashlib.sha256(payloads["product.sqlite3"]).hexdigest(),
        product_database_shm_sha256=hashlib.sha256(
            payloads["product.sqlite3-shm"]
        ).hexdigest(),
        product_database_wal_sha256=hashlib.sha256(
            payloads["product.sqlite3-wal"]
        ).hexdigest(),
    )
    root_fd = _open_root_fd(tmp_path)
    data_fd = _open_bound_path_fd(root_fd, "product", regular_file=False)
    try:
        snapshot, database = _sqlite_bundle_snapshot(data_fd, binding=binding)
        assert database == payloads["product.sqlite3"]
        assert set(snapshot) == set(payloads)

        (data_root / "product.sqlite3-shm").unlink()
        with pytest.raises(ValueError, match="sidecar set differs"):
            _sqlite_bundle_snapshot(data_fd, binding=binding)
        (data_root / "product.sqlite3-shm").write_bytes(
            payloads["product.sqlite3-shm"]
        )
        (data_root / "product.sqlite3-journal").write_bytes(b"unexpected")
        with pytest.raises(ValueError, match="sidecar set differs"):
            _sqlite_bundle_snapshot(data_fd, binding=binding)
    finally:
        os.close(data_fd)
        os.close(root_fd)


def test_read_only_memory_connection_accepts_bound_empty_wal_image(
    tmp_path: Path,
) -> None:
    database = tmp_path / "wal.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE preserved (value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved VALUES ('baseline')")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    image = database.read_bytes()
    assert image[18:20] == b"\x02\x02"

    with _read_only_connection(image) as connection:
        assert connection.execute("SELECT value FROM preserved").fetchone()[0] == (
            "baseline"
        )

    assert database.read_bytes() == image


def test_baseline_continuation_context_is_self_sealed() -> None:
    context = ProductBaselineContinuationContextV0231.build(
        predecessor_head=HEAD,
        source_attempt_sha256=SHA,
        source_private_report_sha256=SHA,
        product_data_root_locator=".local/product-v023/product",
        product_data_root_locator_sha256=SHA,
        environment_id="env-" + "1" * 24,
        active_baseline_id="base-" + "2" * 24,
        active_baseline_sha256=SHA,
        readiness_audit_sha256=SHA,
        parity_sha256=SHA,
        active_profile_sha256=SHA,
        service_identity_sha256=SHA,
        capability_sha256=SHA,
        runtime_authority_path=".local/product-v023/runtime-authority.json",
        runtime_authority_sha256=SHA,
    )

    assert context.context_sha256 != SHA
    with pytest.raises(ValidationError, match="continuation context digest differs"):
        ProductBaselineContinuationContextV0231.model_validate(
            {**context.model_dump(mode="json"), "context_sha256": SHA}
        )
