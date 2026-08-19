from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import cast

import pytest

from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_final_cli import (
    _ADMINISTRATIVE_ATTESTATION_RELATIVE,
    _DEC048_NON_PUBLIC_CHANGED_PATHS,
    _DEC048_PUBLIC_CHANGED_PATHS,
    _PR55_MERGE_HEAD,
    _build_administrative_successor_attestation,
    _read_public_report,
    _verify_frozen_report_scope_against_pr55_merge_tree,
    verify_frozen_report_and_administrative_successor,
)
from ecomsre.dta_v2.v21.live_final_reporting import (
    PublicLiveCapabilityCloseoutReportV4,
)
from scripts.ci.verify_dta_v21_pr_f_protocol import (
    _CAPABILITY_FROZEN_PATHS,
    _capability_frozen_scope_sha256,
    _decision_section_sha256,
    _verify_capability_frozen_scope,
    _verify_pr_f_targets_do_not_reach_held_out_execution,
    verify_pr_f_protocol,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMIT_A_PATHS = (
    *_DEC048_NON_PUBLIC_CHANGED_PATHS,
    *(
        path
        for path in _DEC048_PUBLIC_CHANGED_PATHS
        if path != _ADMINISTRATIVE_ATTESTATION_RELATIVE
    ),
)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(root: Path, message: str, *paths: str) -> str:
    _git(root, "add", "--", *paths)
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _build_successor_repository(
    tmp_path: Path,
) -> tuple[Path, PublicLiveCapabilityCloseoutReportV4, Path]:
    root = tmp_path / "repository"
    subprocess.run(
        ("git", "clone", "--shared", "--no-checkout", str(REPO_ROOT), str(root)),
        check=True,
        capture_output=True,
        text=True,
    )
    _git(
        root,
        "checkout",
        "-q",
        "-B",
        "codex/dta-v21-p0-pr-f-final-metadata",
        _PR55_MERGE_HEAD,
    )
    _git(root, "config", "user.name", "DTA v2.1 Test")
    _git(root, "config", "user.email", "dta-v21-test@example.invalid")
    for relative in _COMMIT_A_PATHS:
        source = REPO_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    pre_attestation_head = _commit(root, "Commit A", *_COMMIT_A_PATHS)
    attestation = _build_administrative_successor_attestation(
        repository_root=root,
        pre_attestation_candidate_head=pre_attestation_head,
    )
    attestation_path = root / _ADMINISTRATIVE_ATTESTATION_RELATIVE
    attestation_path.parent.mkdir(parents=True, exist_ok=True)
    attestation_path.write_text(
        json.dumps(attestation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    attestation_path.chmod(0o644)
    _commit(root, "Commit B", _ADMINISTRATIVE_ATTESTATION_RELATIVE)
    report = _read_public_report(
        root / "docs/results/dta-v21-live-capability-closeout.json"
    )
    return root, report, attestation_path


def _rewrite_attestation(
    root: Path,
    attestation_path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> None:
    value = json.loads(attestation_path.read_text(encoding="utf-8"))
    value.pop("record_sha256")
    mutate(value)
    value["record_sha256"] = semantic_sha256(value)
    attestation_path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    attestation_path.chmod(0o644)
    _commit(root, "tamper attestation", _ADMINISTRATIVE_ATTESTATION_RELATIVE)


def test_public_pr_f_protocol_verifier_passes_without_private_evidence() -> None:
    protocol = verify_pr_f_protocol(REPO_ROOT)

    assert protocol.fault_impact_kind == "RESOURCE_ONLY"
    assert protocol.business_sli_role == "NON_REGRESSION_GUARDRAIL"
    assert protocol.user_visible_recovery_claimed is False


def test_frozen_report_scope_validates_against_pr55_merge_tree() -> None:
    report = _read_public_report(
        REPO_ROOT / "docs/results/dta-v21-live-capability-closeout.json"
    )

    _verify_frozen_report_scope_against_pr55_merge_tree(
        repository_root=REPO_ROOT,
        report=report,
    )


def test_exact_administrative_successor_attestation_passes(tmp_path: Path) -> None:
    root, report, _attestation_path = _build_successor_repository(tmp_path)

    verify_frozen_report_and_administrative_successor(
        repository_root=root,
        report=report,
    )


def test_changed_current_scope_requires_attestation(tmp_path: Path) -> None:
    root, report, attestation_path = _build_successor_repository(tmp_path)
    attestation_path.unlink()

    with pytest.raises(ValueError, match="attestation.*missing|missing.*attestation"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("report_sha256", "report SHA-256"),
        ("candidate_scope_sha256", "frozen candidate scope"),
    ),
)
def test_changed_frozen_report_binding_fails(field: str, message: str) -> None:
    report = _read_public_report(
        REPO_ROOT / "docs/results/dta-v21-live-capability-closeout.json"
    ).model_copy(update={field: "f" * 64})

    with pytest.raises(ValueError, match=message):
        _verify_frozen_report_scope_against_pr55_merge_tree(
            repository_root=REPO_ROOT,
            report=report,
        )


def test_changed_successor_scope_fails(tmp_path: Path) -> None:
    root, report, attestation_path = _build_successor_repository(tmp_path)
    _rewrite_attestation(
        root,
        attestation_path,
        lambda value: value.__setitem__("successor_candidate_scope_sha256", "f" * 64),
    )

    with pytest.raises(ValueError, match="successor candidate scope"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


def test_extra_non_public_attested_path_fails(tmp_path: Path) -> None:
    root, report, attestation_path = _build_successor_repository(tmp_path)

    def add_path(value: dict[str, object]) -> None:
        paths = list(cast(list[str], value["non_public_changed_paths"]))
        paths.append("tests/dta_v21/test_future.py")
        value["non_public_changed_paths"] = paths
        hashes = dict(cast(dict[str, str], value["blob_sha256_by_path"]))
        hashes["tests/dta_v21/test_future.py"] = "f" * 64
        value["blob_sha256_by_path"] = hashes

    _rewrite_attestation(root, attestation_path, add_path)
    with pytest.raises(ValueError, match="non-public changed paths"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


def test_missing_authorized_attested_path_fails(tmp_path: Path) -> None:
    root, report, attestation_path = _build_successor_repository(tmp_path)

    def remove_path(value: dict[str, object]) -> None:
        missing = _DEC048_NON_PUBLIC_CHANGED_PATHS[-1]
        value["non_public_changed_paths"] = [
            path
            for path in cast(list[str], value["non_public_changed_paths"])
            if path != missing
        ]
        hashes = dict(cast(dict[str, str], value["blob_sha256_by_path"]))
        hashes.pop(missing)
        value["blob_sha256_by_path"] = hashes

    _rewrite_attestation(root, attestation_path, remove_path)
    with pytest.raises(ValueError, match="non-public changed paths"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


def test_changed_attested_blob_hash_fails(tmp_path: Path) -> None:
    root, report, attestation_path = _build_successor_repository(tmp_path)

    def replace_hash(value: dict[str, object]) -> None:
        hashes = dict(cast(dict[str, str], value["blob_sha256_by_path"]))
        hashes[_DEC048_NON_PUBLIC_CHANGED_PATHS[0]] = "f" * 64
        value["blob_sha256_by_path"] = hashes

    _rewrite_attestation(root, attestation_path, replace_hash)
    with pytest.raises(ValueError, match="blob SHA-256"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


def test_changed_public_result_file_fails(tmp_path: Path) -> None:
    root, report, _attestation_path = _build_successor_repository(tmp_path)
    relative = "docs/results/dta-v21-final-summary.md"
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    _commit(root, "change public result", relative)

    with pytest.raises(ValueError, match="public result|changed path set"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


@pytest.mark.parametrize(
    "relative",
    (
        "src/ecomsre/dta_v2/v21/agent.py",
        "src/ecomsre/dta_v2/v21/prompts.py",
        "src/ecomsre/dta_v2/v21/planner.py",
        "src/ecomsre/dta_v2/v21/live_runner.py",
    ),
)
def test_changed_protected_runtime_path_fails(tmp_path: Path, relative: str) -> None:
    root, report, _attestation_path = _build_successor_repository(tmp_path)
    path = root / relative
    path.write_text(path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    _commit(root, "change protected runtime", relative)

    with pytest.raises(ValueError, match="successor candidate scope|protected"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


def test_symlink_attestation_fails(tmp_path: Path) -> None:
    root, report, attestation_path = _build_successor_repository(tmp_path)
    attestation_path.unlink()
    os.symlink("current-disposition.json", attestation_path)
    _commit(root, "replace attestation with symlink", _ADMINISTRATIVE_ATTESTATION_RELATIVE)

    with pytest.raises(ValueError, match="attestation.*unsafe|unsafe.*attestation"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


@pytest.mark.parametrize("kind", ("noncanonical", "duplicate"))
def test_noncanonical_or_duplicate_attestation_fails(
    tmp_path: Path, kind: str
) -> None:
    root, report, attestation_path = _build_successor_repository(tmp_path)
    raw = attestation_path.read_text(encoding="utf-8")
    if kind == "noncanonical":
        raw = raw.rstrip("\n") + " \n"
    else:
        raw = raw.replace(
            "{\n",
            '{\n  "schema_version": "duplicate",\n',
            1,
        )
    attestation_path.write_text(raw, encoding="utf-8")
    _commit(root, f"{kind} attestation", _ADMINISTRATIVE_ATTESTATION_RELATIVE)

    with pytest.raises(ValueError, match="canonical|duplicate"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


def test_future_arbitrary_test_file_is_not_accepted(tmp_path: Path) -> None:
    root, report, _attestation_path = _build_successor_repository(tmp_path)
    relative = "tests/dta_v21/test_future_administrative_change.py"
    path = root / relative
    path.write_text("def test_future() -> None:\n    pass\n", encoding="utf-8")
    _commit(root, "add future test", relative)

    with pytest.raises(ValueError, match="successor candidate scope|changed path set"):
        verify_frozen_report_and_administrative_successor(
            repository_root=root,
            report=report,
        )


def test_pr_f_target_cannot_depend_on_held_out_execution_or_scoring() -> None:
    dangerous_makefiles = (
        "dta-v21-pr-f-live: dta-v21-held-out-execute\n\t@true\n",
        (
            "evil: dta-v21-held-out-execute\n"
            "\t@true\n"
            "dta-v21-pr-f-live: evil\n"
            "\t@true\n"
        ),
        "dta-v21-pr-f-live:\n\t$(DTA_V21_HELD_OUT_CLI) execute\n",
        (
            "dta-v21-pr-f-live:\n"
            "\t$(DTA_V21_HELD_OUT_CLI) \\\n"
            "\t  execute\n"
        ),
    )
    for makefile in dangerous_makefiles:
        with pytest.raises(ValueError, match="held-out execution or scoring"):
            _verify_pr_f_targets_do_not_reach_held_out_execution(makefile)


def test_capability_frozen_scope_is_content_bound_without_pr_git_object(
    tmp_path: Path,
) -> None:
    root = tmp_path / "squash-checkout-without-git"
    frozen = root / "config/dta-v21/agent-identities/planner.json"
    frozen.parent.mkdir(parents=True)
    frozen.write_text('{"identity":"frozen"}\n', encoding="utf-8")
    decisions = root / "docs/DECISIONS.md"
    decisions.parent.mkdir(parents=True)
    decisions.write_text(
        "## DEC-044 — frozen\n\nresource-only\n\n"
        "## DEC-045 — frozen\n\ncompose identity\n\n"
        "## DEC-046 — appended\n\ncapability closeout\n",
        encoding="utf-8",
    )
    paths = ("config/dta-v21/agent-identities",)
    expected_scope = _capability_frozen_scope_sha256(
        root, frozen_paths=paths
    )
    expected_decisions = {
        decision_id: _decision_section_sha256(
            decisions.read_text(encoding="utf-8"), decision_id
        )
        for decision_id in ("DEC-044", "DEC-045")
    }

    _verify_capability_frozen_scope(
        root,
        frozen_paths=paths,
        expected_scope_sha256=expected_scope,
        expected_decision_sections=expected_decisions,
    )

    frozen.write_text('{"identity":"changed"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="frozen Agent"):
        _verify_capability_frozen_scope(
            root,
            frozen_paths=paths,
            expected_scope_sha256=expected_scope,
            expected_decision_sections=expected_decisions,
        )
    frozen.write_text('{"identity":"frozen"}\n', encoding="utf-8")
    decisions.write_text(
        decisions.read_text(encoding="utf-8").replace(
            "resource-only", "resource-only changed"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DEC-044 changed"):
        _verify_capability_frozen_scope(
            root,
            frozen_paths=paths,
            expected_scope_sha256=expected_scope,
            expected_decision_sections=expected_decisions,
        )


def test_capability_frozen_scope_includes_identity_and_exact_no_fault_files() -> None:
    assert "config/dta-v21/agent-identities" in _CAPABILITY_FROZEN_PATHS
    assert (
        "config/dta-v21/scenarios/agent-visible/dta21-dev-005.json"
        in _CAPABILITY_FROZEN_PATHS
    )
    assert (
        "config/dta-v21/scenarios/evaluator-contract/dta21-dev-005.json"
        in _CAPABILITY_FROZEN_PATHS
    )
