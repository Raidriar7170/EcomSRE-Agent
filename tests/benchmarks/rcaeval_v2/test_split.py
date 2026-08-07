from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    SplitName,
    build_split_assignments,
    case_identity_bytes,
    write_split_artifacts,
)


ROOT_SERVICES = {
    "RE2-OB": (
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "productcatalogservice",
        "recommendationservice",
    ),
    "RE2-SS": ("carts", "catalogue", "orders", "payment", "user"),
}
FAULTS = ("cpu", "mem", "disk", "delay", "loss", "socket")


def synthetic_identities() -> tuple[CaseIdentity, ...]:
    return tuple(
        CaseIdentity(
            system=system,
            root_cause_service=service,
            fault=fault,
            instance=str(instance),
        )
        for system, services in ROOT_SERVICES.items()
        for service in services
        for fault in FAULTS
        for instance in (1, 2, 3)
    )


def test_identity_is_exact_utf8_nul_joined_without_labels_or_values() -> None:
    identity = CaseIdentity(
        system="RE2-OB",
        root_cause_service="checkoutservice",
        fault="cpu",
        instance="1",
    )
    assert case_identity_bytes(identity) == (
        b"RE2-OB\x00checkoutservice\x00cpu\x00" + b"1"
    )


def test_split_is_deterministic_stratified_and_uses_one_plus_two_instances() -> None:
    identities = synthetic_identities()
    forward = build_split_assignments(identities, seed=20260807)
    reverse = build_split_assignments(tuple(reversed(identities)), seed=20260807)

    assert forward == reverse
    assert len(forward) == 180
    assert sum(item.split is SplitName.DESIGN for item in forward) == 60
    assert sum(item.split is SplitName.DEV_VALIDATION for item in forward) == 120
    strata: dict[tuple[str, str, str], list[SplitName]] = {}
    for item in forward:
        key = (
            item.identity.system,
            item.identity.root_cause_service,
            item.identity.fault,
        )
        strata.setdefault(key, []).append(item.split)
    assert len(strata) == 60
    assert all(
        values.count(SplitName.DESIGN) == 1
        and values.count(SplitName.DEV_VALIDATION) == 2
        for values in strata.values()
    )


def test_private_manifest_and_public_lock_are_create_once_and_projected(
    tmp_path: Path,
) -> None:
    assignments = build_split_assignments(synthetic_identities(), seed=20260807)
    private_root = tmp_path / ".ecomsre-private" / "rcaeval-re2-v2-dev" / "split"
    split_lock = tmp_path / "public" / "split-lock.json"
    protocol_sha = "a" * 64
    dataset_sha = "b" * 64

    first = write_split_artifacts(
        assignments,
        private_root=private_root,
        split_lock_output=split_lock,
        protocol_sha256=protocol_sha,
        dataset_lock_sha256=dataset_sha,
        seed=20260807,
    )
    first_mtime = split_lock.stat().st_mtime_ns
    second = write_split_artifacts(
        tuple(reversed(assignments)),
        private_root=private_root,
        split_lock_output=split_lock,
        protocol_sha256=protocol_sha,
        dataset_lock_sha256=dataset_sha,
        seed=20260807,
    )

    manifest_path = private_root / "split-assignment-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    public = json.loads(split_lock.read_text(encoding="utf-8"))
    assert first == second
    assert split_lock.stat().st_mtime_ns == first_mtime
    assert private_root.stat().st_mode & 0o077 == 0
    assert manifest_path.stat().st_mode & 0o777 == 0o600
    assert split_lock.stat().st_mode & 0o777 == 0o600
    assert len(manifest["assignments"]) == 180
    assert set(public) == {
        "algorithm",
        "assignment_manifest_sha256",
        "classification",
        "counts",
        "dataset_lock_sha256",
        "identity_fields",
        "protocol_id",
        "protocol_sha256",
        "schema_version",
        "seed",
    }
    assert public["identity_fields"] == [
        "system",
        "root_cause_service",
        "fault",
        "instance",
    ]
    assert public["counts"] == {
        "total": 180,
        "strata": 60,
        "design": 60,
        "design_re2_ob": 30,
        "design_re2_ss": 30,
        "dev_validation": 120,
        "dev_validation_re2_ob": 60,
        "dev_validation_re2_ss": 60,
    }
    public_text = split_lock.read_text(encoding="utf-8")
    assert not any(
        marker in public_text
        for marker in (
            '"assignments"',
            '"case_id"',
            '"run_id"',
            "/Users/",
            "/home/",
            "/private/",
            "checkoutservice",
            "catalogue",
        )
    )
    assert public["assignment_manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()


def test_split_output_rejects_tt_marker_before_file_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("forbidden split path was opened")

    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(ValueError, match="forbidden"):
        write_split_artifacts(
            build_split_assignments(synthetic_identities(), seed=20260807),
            private_root=tmp_path / "RE2-TT-private",
            split_lock_output=tmp_path / "split-lock.json",
            protocol_sha256="a" * 64,
            dataset_lock_sha256="b" * 64,
            seed=20260807,
        )
