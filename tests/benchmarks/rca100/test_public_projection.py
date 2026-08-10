from __future__ import annotations

from pathlib import Path

from ecomsre_rca100.public_projection import scan_public_artifacts


def test_public_leakage_scan_rejects_case_identity_and_private_path(
    tmp_path: Path,
) -> None:
    safe = tmp_path / "safe.json"
    safe.write_text('{"fixed_denominator":103}', encoding="utf-8")
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(
        '{"source_task_id":"t007","path":"/Users/person/private"}',
        encoding="utf-8",
    )

    assert scan_public_artifacts((safe,)) == ()
    findings = scan_public_artifacts((unsafe,))
    assert any("private-marker" in item for item in findings)
    assert any("private-value" in item for item in findings)
