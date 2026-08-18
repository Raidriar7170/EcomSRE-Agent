from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.verify_dta_v21_pr_f_protocol import (
    _CAPABILITY_FROZEN_PATHS,
    _capability_frozen_scope_sha256,
    _decision_section_sha256,
    _verify_capability_frozen_scope,
    _verify_pr_f_targets_do_not_reach_held_out_execution,
    verify_pr_f_protocol,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_public_pr_f_protocol_verifier_passes_without_private_evidence() -> None:
    protocol = verify_pr_f_protocol(REPO_ROOT)

    assert protocol.fault_impact_kind == "RESOURCE_ONLY"
    assert protocol.business_sli_role == "NON_REGRESSION_GUARDRAIL"
    assert protocol.user_visible_recovery_claimed is False


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
